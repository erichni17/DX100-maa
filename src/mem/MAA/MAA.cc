#include "mem/MAA/MAA.hh"

#include <algorithm>
#include <cassert>
#include <cstdint>
#include <limits>
#include <string>
#include <utility>

#include "base/addr_range.hh"
#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/MAA.hh"
#include "debug/MAACachePort.hh"
#include "debug/MAAController.hh"
#include "debug/MAACpuPort.hh"
#include "debug/MAAMacroEvent.hh"
#include "debug/MAAMemPort.hh"
#include "debug/MAAVirtualTrace.hh"
#include "mem/MAA/ALU.hh"
#include "mem/MAA/IF.hh"
#include "mem/MAA/IndirectAccess.hh"
#include "mem/MAA/Invalidator.hh"
#include "mem/MAA/LogicalSPDCacheGem5Bridge.hh"
#include "mem/MAA/LogicalSPDCachePortProvenance.hh"
#include "mem/MAA/RangeFuser.hh"
#include "mem/MAA/SPD.hh"
#include "mem/MAA/StreamAccess.hh"
#include "mem/packet.hh"
#include "params/MAA.hh"
#include "sim/cur_tick.hh"

#ifndef TRACING_ON
#define TRACING_ON 1
#endif

template <typename Integral_t>
Integral_t calc_log2(Integral_t val) {
    static_assert(std::is_integral_v<Integral_t>, "Only integral types are allowed for bitwise operations!");

    Integral_t n = 0;
    while ((val >>= 1)) {
        n++;
    }
    return n;
};

namespace gem5 {

namespace {

const char *
transparentActionName(TransparentSPDController::Action action)
{
    switch (action) {
      case TransparentSPDController::Action::None:
        return "none";
      case TransparentSPDController::Action::Fill:
        return "stream_fill";
      case TransparentSPDController::Action::Compute:
        return "compute";
      case TransparentSPDController::Action::Store:
        return "stream_store";
    }
    panic("invalid transparent-controller action\n");
}

HybridMacroEventTracker::Stage
transparentMacroStage(TransparentSPDController::Action action)
{
    switch (action) {
      case TransparentSPDController::Action::Fill:
        return HybridMacroEventTracker::Stage::PageFill;
      case TransparentSPDController::Action::Compute:
        return HybridMacroEventTracker::Stage::ALU;
      case TransparentSPDController::Action::Store:
        return HybridMacroEventTracker::Stage::StreamStore;
      case TransparentSPDController::Action::None:
        panic("empty transparent action has no macro stage\n");
    }
    panic("invalid transparent action has no macro stage\n");
}

class ImmediateLogicalSPDTranslation final : public BaseMMU::Translation
{
  public:
    void markDelayed() override { delayed = true; }

    void finish(const Fault &translationFault, const RequestPtr &req,
                ThreadContext *, BaseMMU::Mode) override
    {
        fault = translationFault;
        address = req->getPaddr();
        finished = true;
    }

    Fault fault = NoFault;
    Addr address = 0;
    bool delayed = false;
    bool finished = false;
};

} // anonymous namespace

MAA::MAAResponsePort::MAAResponsePort(const std::string &_name, MAA &_maa, const std::string &_label)
    : QueuedResponsePort(_name, queue),
      maa{_maa},
      queue(_maa, *this, true, _label) {
}

MAA::MAA(const MAAParams &p)
    : ClockedObject(p),
      addrRanges(p.addr_ranges.begin(), p.addr_ranges.end()),
      num_tiles(p.num_tiles_per_core * p.num_cores),
      num_tile_elements(p.num_tile_elements),
      physical_tile_elements(p.physical_tile_elements == 0
                                 ? p.num_tile_elements
                                 : p.physical_tile_elements),
      transparent_spd_mode(p.transparent_spd_mode),
      logical_spd_cache_mode(p.logical_spd_cache_mode),
      num_regs(p.num_regs_per_core * p.num_cores),
      num_instructions_per_core(p.num_instructions_per_core),
      num_row_table_rows_per_slice(p.num_row_table_rows_per_slice),
      num_offset_table_entries(p.num_offset_table_entries == 0
                                   ? p.num_tile_elements
                                   : p.num_offset_table_entries),
      num_offset_table_epoch_entries(
          p.num_offset_table_epoch_entries == 0
              ? (p.num_offset_table_entries == 0
                     ? p.num_tile_elements
                     : p.num_offset_table_entries)
              : p.num_offset_table_epoch_entries),
      num_row_table_entries_per_subslice_row(p.num_row_table_entries_per_subslice_row),
      num_row_table_config_cache_entries(p.num_row_table_config_cache_entries),
      reconfigure_row_table(p.reconfigure_row_table),
      reorder_row_table(p.no_reorder == false ? true : false),
      force_cache_access(p.force_cache_access),
      num_initial_row_table_slices(p.num_initial_row_table_slices),
      virtual_combine_slots(p.virtual_combine_slots),
      virtual_combine_words(p.virtual_combine_words),
      virtual_combine_ways(p.virtual_combine_ways),
      virtual_combine_victim_policy(p.virtual_combine_victim_policy),
      virtual_combine_banks(p.virtual_combine_banks),
      virtual_response_slots(p.virtual_response_slots),
      virtual_response_words(p.virtual_response_words),
      virtual_response_word_pool(p.virtual_response_word_pool),
      virtual_words_per_cycle(p.virtual_words_per_cycle),
      virtual_max_outstanding_writes(p.virtual_max_outstanding_writes),
      virtual_masked_writes(p.virtual_masked_writes),
      virtual_idealized_write_ack(p.virtual_idealized_write_ack),
      direct_retirement_line_handoff(p.direct_retirement_line_handoff),
      virtual_index_buffer_lines(p.virtual_index_buffer_lines),
      virtual_index_force_cache(p.virtual_index_force_cache),
      virtual_index_partitions(p.virtual_index_partitions),
      virtual_index_range_passes(p.virtual_index_range_passes),
      virtual_index_descriptor_spool(p.virtual_index_descriptor_spool),
      virtual_descriptor_spool_read_ahead(
          p.virtual_descriptor_spool_read_ahead),
      virtual_descriptor_spool_read_credits(
          p.virtual_descriptor_spool_read_credits),
      virtual_descriptor_spool_write_credits(
          p.virtual_descriptor_spool_write_credits),
      virtual_descriptor_spool_source_bypass_cache(
          p.virtual_descriptor_spool_source_bypass_cache),
      virtual_bounded_global_merge(p.virtual_bounded_global_merge),
      virtual_index_range_policy(p.virtual_index_range_policy),
      virtual_index_range_boundaries(p.virtual_index_range_boundaries),
      virtual_index_filter_words_per_cycle(
          p.virtual_index_filter_words_per_cycle),
      virtual_partition_keep_combiner(p.virtual_partition_keep_combiner),
      virtual_grow_order(p.virtual_grow_order),
      virtual_native_issue_order(p.virtual_native_issue_order),
      num_request_table_addresses(p.num_request_table_addresses),
      num_request_table_entries_per_address(p.num_request_table_entries_per_address),
      num_memory_channels(p.num_memory_channels),
      num_cores(p.num_cores),
      num_maas(p.num_maas),
      num_indirect_units_per_maa(p.num_indirect_units_per_maa),
      num_indirect_units_total(p.num_maas * p.num_indirect_units_per_maa),
      rowtable_latency(p.rowtable_latency),
      addrRegions(MAX_CMD_REGIONS, {0, 0}),
      maxRegionID(-1),
      system(p.system),
      mmu(p.mmu),
      logicalSpdEvent([this] { serviceLogicalSPD(); }, name()),
      directRetirementEvent([this] { serviceDirectRetirement(); }, name()),
      issueInstructionEvent([this] { issueInstruction(); }, name()),
      dispatchInstructionEvent([this] { dispatchInstruction(); }, name()),
      dispatchRegisterEvent([this] { dispatchRegister(); }, name()),
      stats(this, p.num_maas * p.num_indirect_units_per_maa, this),
      sendCacheEvent([this] { sendOutstandingCachePacket(); }, name()),
      sendMemEvent([this] { sendOutstandingMemPacket(); }, name()) {

    m_core_addr_bits = calc_log2(num_cores);
    panic_if(num_cores % num_maas != 0, "Number of cores %d must be a multiple of the number of MAAs %s\n", num_cores, num_maas);
    panic_if(num_indirect_units_per_maa == 0, "Number of indirect units per MAA must be positive\n");
    panic_if(physical_tile_elements > num_tile_elements,
             "Physical tile capacity %u exceeds logical capacity %u\n",
             physical_tile_elements, num_tile_elements);
    panic_if(transparent_spd_mode > 3,
             "Invalid transparent SPD mode %u (expected 0..3)\n",
             transparent_spd_mode);
    panic_if(logical_spd_cache_mode > 1,
             "Invalid logical SPD cache mode %u (expected 0 or 1)\n",
             logical_spd_cache_mode);
    panic_if(num_offset_table_entries == 0 ||
                 num_offset_table_entries > num_tile_elements,
             "Offset Table capacity %u must be in [1,%u]\n",
             num_offset_table_entries, num_tile_elements);
    panic_if(num_offset_table_epoch_entries == 0 ||
                 num_offset_table_epoch_entries > num_offset_table_entries,
             "Offset Table epoch capacity %u must be in [1,%u]\n",
             num_offset_table_epoch_entries, num_offset_table_entries);
    if (virtual_index_range_passes) {
        const unsigned int minimum_passes =
            (num_tile_elements + num_offset_table_entries - 1) /
            num_offset_table_entries;
        panic_if(num_offset_table_entries > 4096,
                 "Bounded range passes allow at most 4096 Offset entries, "
                 "got %u\n", num_offset_table_entries);
        panic_if(physical_tile_elements > 4096,
                 "Bounded range passes allow at most 4096 physical SPD "
                 "elements per tile, got %u\n", physical_tile_elements);
        panic_if(reconfigure_row_table,
                 "Bounded range passes require one explicitly allocated "
                 "Row Table configuration\n");
        panic_if(virtual_index_range_policy == 3 &&
                     num_offset_table_entries != 4096,
                 "Adaptive translated-grow quantiles phase-share exactly "
                 "4096 "
                 "Word/Offset entries, got %u\n",
                 num_offset_table_entries);
        panic_if(virtual_index_partitions < minimum_passes,
                 "Bounded range passes need at least %u passes for %u/%u "
                 "logical/active entries, got %u\n", minimum_passes,
                 num_tile_elements, num_offset_table_entries,
                 virtual_index_partitions);
        panic_if(!virtual_index_force_cache,
                 "Bounded range passes require LLC-visible index rescans\n");
        panic_if(virtual_index_filter_words_per_cycle == 0,
                 "Bounded range passes require a finite index-filter rate\n");
        panic_if(!virtual_partition_keep_combiner,
                 "Bounded range passes require a retained destination "
                 "combiner across passes\n");
        panic_if(!virtual_grow_order,
                 "Bounded range passes require grow-grouped source issue\n");
        panic_if(virtual_native_issue_order,
                 "Bounded range passes cannot use attribution-only native "
                 "issue order\n");
    }
    panic_if(virtual_index_range_policy > 3,
             "Invalid virtual index range policy %u (expected 0..3)\n",
             virtual_index_range_policy);
    panic_if(!virtual_index_range_passes && virtual_index_range_policy != 0,
             "Virtual index range policy %u requires range passes\n",
             virtual_index_range_policy);
    panic_if(virtual_index_descriptor_spool &&
                 (!virtual_index_range_passes ||
                  virtual_index_range_policy != 3),
             "Descriptor spooling requires bounded translated-grow policy "
             "3\n");
    panic_if(virtual_descriptor_spool_read_ahead &&
                 !virtual_index_descriptor_spool,
             "Descriptor-spool read-ahead requires descriptor spooling\n");
    panic_if(virtual_descriptor_spool_read_credits == 0 ||
                 virtual_descriptor_spool_read_credits > 32,
             "Descriptor-spool read credits must be in [1, 32], got %u\n",
             virtual_descriptor_spool_read_credits);
    panic_if(virtual_descriptor_spool_write_credits == 0 ||
                 virtual_descriptor_spool_write_credits > 32,
             "Descriptor-spool write credits must be in [1, 32], got %u\n",
             virtual_descriptor_spool_write_credits);
    panic_if(virtual_descriptor_spool_source_bypass_cache &&
                 !virtual_index_descriptor_spool,
             "Descriptor-spool A-source cache bypass requires descriptor "
             "spooling\n");
    panic_if(virtual_bounded_global_merge &&
                 (!virtual_index_descriptor_spool ||
                  virtual_descriptor_spool_read_ahead),
             "Bounded global merge requires descriptor spooling and "
             "disallows paged replay read-ahead\n");
    panic_if(virtual_index_range_policy != 2 &&
                 !virtual_index_range_boundaries.empty(),
             "Explicit range boundaries require range policy 2\n");
    panic_if(virtual_index_range_policy == 2 &&
                 virtual_index_range_boundaries.size() !=
                     virtual_index_partitions + 1,
             "Explicit range policy requires %u boundaries, received %lu\n",
             virtual_index_partitions + 1,
             virtual_index_range_boundaries.size());
    panic_if(virtual_grow_order && virtual_native_issue_order,
             "Virtual grow grouping and native issue-order attribution "
             "cannot both be enabled\n");
    const unsigned int max_virtual_pages =
        (num_tile_elements + physical_tile_elements - 1) /
        physical_tile_elements;
    panic_if(max_virtual_pages > MaxVirtualPages,
             "Logical/physical tile ratio needs %u virtual pages, "
             "exceeding token limit %d\n",
             max_virtual_pages, MaxVirtualPages);
    virtualPageReady.resize(num_tiles);
    for (auto &pages : virtualPageReady)
        pages.fill(false);
    virtualPageReadyTransaction.resize(num_tiles);
    for (auto &transactions : virtualPageReadyTransaction)
        transactions.fill(0);
    virtualPageGeneration.assign(num_tiles, 0);
    virtualPageConsumedGeneration.assign(num_tiles, 0);
    virtualPageBackingAddr.assign(num_tiles, 0);
    virtualPageWordSize.assign(num_tiles, 0);
    virtualProducerRegistrationTick.assign(num_tiles, 0);
    virtualPageLastReadyTick.assign(num_tiles, 0);
    num_cores_per_maas = num_cores / num_maas;
    requestorId = p.system->getRequestorId(this);
    spd = new SPD(this, num_tiles, num_tile_elements,
                  physical_tile_elements, p.spd_read_latency,
                  p.spd_write_latency,
                  p.num_spd_read_ports_per_maa * num_maas,
                  p.num_spd_write_ports_per_maa * num_maas);
    rf = new RF(num_regs);
    // The logical cache deliberately has a two-value contract independent of
    // the three-value transparent-controller experiment knob.
    const LogicalSPDCacheRuntime::Mode logicalSpdMode =
        logical_spd_cache_mode == 0
            ? LogicalSPDCacheRuntime::Mode::Serial4K
            : LogicalSPDCacheRuntime::Mode::PingPong2K;
    logicalSpdBridge = std::make_unique<LogicalSPDCacheGem5Bridge>(
        num_maas, logicalSpdMode);
    logicalSpdExecutions.resize(num_maas);
    num_instructions_per_maa = num_instructions_per_core * num_cores_per_maas;
    num_instructions_total = num_instructions_per_maa * num_maas;
    ifile = new IF(num_instructions_per_maa, num_maas, num_tiles, this);
    streamAccessUnits = new StreamAccessUnit[num_maas];
    streamAccessIdle = new bool[num_maas];
    for (int i = 0; i < num_maas; i++) {
        streamAccessUnits[i].allocate(i, num_request_table_addresses,
                                      num_request_table_entries_per_address,
                                      physical_tile_elements, this);
        streamAccessIdle[i] = true;
    }
    indirectAccessUnits = new IndirectAccessUnit[num_indirect_units_total];
    indirectAccessIdle = new bool[num_indirect_units_total];
    for (int i = 0; i < num_indirect_units_total; i++) {
        indirectAccessIdle[i] = true;
    }
    invalidator = new Invalidator();
    invalidator->allocate(num_maas, num_tiles, num_tile_elements, addrRanges.front().start(), this);
    aluUnits = new ALUUnit[num_maas];
    aluUnitsIdle = new bool[num_maas];
    for (int i = 0; i < num_maas; i++) {
        aluUnits[i].allocate(this, i, p.ALU_lane_latency,
                             p.num_ALU_lanes, physical_tile_elements);
        aluUnitsIdle[i] = true;
    }
    rangeUnits = new RangeFuserUnit[num_maas];
    rangeUnitsIdle = new bool[num_maas];
    for (int i = 0; i < num_maas; i++) {
        rangeUnits[i].allocate(physical_tile_elements, this, i);
        rangeUnitsIdle[i] = true;
    }
    invalidatorIdle = true;
    for (int i = 0; i < p.port_mem_sides_connection_count; ++i) {
        std::string portName = csprintf("%s.mem_side_port[%d]", p.name, i);
        memSidePorts.push_back(new MemSidePort(portName, this, "MemSidePort"));
    }
    for (int i = 0; i < p.port_cache_sides_connection_count; ++i) {
        std::string portName = csprintf("%s.cache_side_port[%d]", p.name, i);
        cacheSidePorts.push_back(new CacheSidePort(portName, this, "CacheSidePort"));
        cacheSidePorts[i]->allocate(i, p.max_outstanding_cache_side_packets);
    }
    for (int i = 0; i < p.port_retirement_sides_connection_count; ++i) {
        std::string portName =
            csprintf("%s.retirement_side_port[%d]", p.name, i);
        retirementSidePorts.push_back(
            new CacheSidePort(portName, this, "RetirementSidePort"));
        retirementSidePorts[i]->allocate(
            i, p.max_outstanding_cache_side_packets);
    }
    panic_if(p.port_cpu_sides_connection_count != num_cores, "Number of CPU ports must be equal to the number of cores");
    for (int i = 0; i < num_cores; ++i) {
        cpuPortAddrRanges.push_back(AddrRangeList());
    }
    int range_id = 0;
    for (AddrRange range : addrRanges) {
        switch (range_id) {
        case (uint8_t)AddressRangeType::Type::SPD_DATA_NONCACHEABLE_RANGE:
        case (uint8_t)AddressRangeType::Type::SPD_DATA_CACHEABLE_RANGE: {
            // Addr start = range.start();
            // Addr size_per_port = range.size() / num_cores;
            // for (int i = 0; i < num_cores; ++i) {
            //     DPRINTF(MAA, "Range[%s] for CPU port[%d]: %lx-%lx\n", AddressRangeType::address_range_names[range_id], i, start, start + size_per_port);
            //     cpuPortAddrRanges[i].push_back(AddrRange(start, start + size_per_port));
            //     start += size_per_port;
            // }
            Addr start = range.start();
            Addr end = range.end();
            std::vector<Addr> mask;
            Addr curr_mask = 1 << 6;
            printf("MAA: cpu masks: ");
            for (int i = 0; i < log2(num_cores); i++) {
                mask.push_back(curr_mask);
                printf("%lx ", curr_mask);
                curr_mask = curr_mask << 1;
            }
            printf("\n");
            for (int i = 0; i < num_cores; ++i) {
                AddrRange curr_range = AddrRange(start, end, mask, i);
                printf("MAA: Range[%s] for CPU port[%d]: %s\n", AddressRangeType::address_range_names[range_id], i, curr_range.to_string().c_str());
                cpuPortAddrRanges[i].push_back(curr_range);
            }
            break;
        }
        default: {
            DPRINTF(MAA, "Range[%s] for CPU port[0]: %lx-%lx\n", AddressRangeType::address_range_names[range_id], range.start(), range.end());
            cpuPortAddrRanges[0].push_back(AddrRange(range.start(), range.end()));
            break;
        }
        }
        range_id++;
    }
    for (int i = 0; i < p.port_cpu_sides_connection_count; ++i) {
        std::string portName = csprintf("%s.cpu_side_port[%d]", p.name, i);
        cpuSidePorts.push_back(new CpuSidePort(portName, *this, "CpuSidePort"));
        cpuSidePorts[i]->allocate(i, p.max_outstanding_cpu_side_packets);
    }

    my_last_idle_tick = curTick();
    my_last_reset_tick = curTick();
    my_num_outstanding_indirect_pkts = new uint32_t[num_indirect_units_total];
    my_num_outstanding_stream_pkts = new uint32_t[num_maas];
    for (int i = 0; i < num_indirect_units_total; i++) {
        my_num_outstanding_indirect_pkts[i] = 0;
    }
    for (int i = 0; i < num_maas; i++) {
        my_num_outstanding_stream_pkts[i] = 0;
    }
}

void MAA::init() {
    for (auto port : cpuSidePorts) {
        if (!port->isConnected())
            fatal("Cache ports on %s are not connected\n", name());
        port->sendRangeChange();
    }
}

MAA::~MAA() {
    for (auto &[paddr, packets] : my_deferred_pkt_map) {
        for (auto &deferred : packets)
            delete deferred.packet;
    }
    for (auto port : memSidePorts)
        delete port;
    for (auto port : cacheSidePorts)
        delete port;
    for (auto port : retirementSidePorts)
        delete port;
    for (auto port : cpuSidePorts)
        delete port;
    delete[] my_num_outstanding_indirect_pkts;
    delete[] my_num_outstanding_stream_pkts;
    delete spd;
}

void MAA::addAddrRegion(Addr start, Addr end, int8_t id) {
    panic_if(id >= MAX_CMD_REGIONS, "Region ID %d exceeds the maximum number of regions %d\n", id, MAX_CMD_REGIONS);
    panic_if(start >= end, "Region ID %d start address 0x%x >= end address 0x%x\n", id, start, end);
    panic_if(end == 0, "Region ID %d end address 0x%x is invalid\n", id, end);
    maxRegionID = -1;
    for (int i = 0; i < MAX_CMD_REGIONS; i++) {
        if (start <= addrRegions[i].first && addrRegions[i].first < end) {
            panic("Region[%d]:[0x%x-0x%x] overlaps with new Region[%d]:[0x%x-0x%x]\n", i, addrRegions[i].first, addrRegions[i].second, id, start, end);
        } else {
            maxRegionID = i;
        }
    }
    DPRINTF(MAA, "Region[%d]:[0x%x-0x%x] added\n", id, start, end);
    addrRegions[id] = {start, end};
    if (id > maxRegionID) {
        maxRegionID = id;
    }
}

void MAA::clearAddrRegion() {
    DPRINTF(MAA, "all addr regions cleared\n");
    maxRegionID = -1;
    for (int i = 0; i < MAX_CMD_REGIONS; i++) {
        addrRegions[i] = {0, 0};
    }
}

int MAA::getAddrRegion(Addr addr) {
    int reg_id = -1;
    for (int reg_idx = 0; reg_idx <= maxRegionID; reg_idx++) {
        if (addrRegions[reg_idx].first == 0 && addrRegions[reg_idx].second == 0) {
            continue;
        } else if (addrRegions[reg_idx].first <= addr && addr < addrRegions[reg_idx].second) {
            reg_id = reg_idx;
            break;
        }
    }
    panic_if(reg_id == -1, "Address 0x%x does not belong to any region\n", addr);
    return reg_id;
}

Port &MAA::getPort(const std::string &if_name, PortID idx) {
    if (if_name == "mem_sides" && idx < memSidePorts.size()) {
        return *memSidePorts[idx];
    } else if (if_name == "cpu_sides" && idx < cpuSidePorts.size()) {
        return *cpuSidePorts[idx];
    } else if (if_name == "cache_sides" && idx < cacheSidePorts.size()) {
        return *cacheSidePorts[idx];
    } else if (if_name == "retirement_sides" &&
               idx < retirementSidePorts.size()) {
        return *retirementSidePorts[idx];
    } else {
        return ClockedObject::getPort(if_name, idx);
    }
}
int MAA::inRange(Addr addr) const {
    int r_id = -1;
    for (const auto &r : addrRanges) {
        if (r.contains(addr)) {
            break;
        }
    }
    return r_id;
}
void MAA::addRamulator(memory::Ramulator2 *_ramulator2) {
    _ramulator2->getAddrMapData(m_org,
                                m_addr_bits,
                                m_num_levels,
                                m_tx_offset,
                                m_col_bits_idx,
                                m_row_bits_idx);
    DPRINTF(MAA, "DRAM organization [n_levels: %d] -- CH: %d, RA: %d, BG: %d, BA: %d, RO: %d, CO: %d\n",
            m_num_levels,
            m_org[ADDR_CHANNEL_LEVEL],
            m_org[ADDR_RANK_LEVEL],
            m_org[ADDR_BANKGROUP_LEVEL],
            m_org[ADDR_BANK_LEVEL],
            m_org[ADDR_ROW_LEVEL],
            m_org[ADDR_COLUMN_LEVEL]);
    DPRINTF(MAA, "DRAM addr_bit -- RO: %d, BA: %d, BG: %d, RA: %d, CO: %d, CH: %d, TX: %d\n",
            m_addr_bits[ADDR_ROW_LEVEL],
            m_addr_bits[ADDR_BANK_LEVEL],
            m_addr_bits[ADDR_BANKGROUP_LEVEL],
            m_addr_bits[ADDR_RANK_LEVEL],
            m_addr_bits[ADDR_COLUMN_LEVEL],
            m_addr_bits[ADDR_CHANNEL_LEVEL],
            m_tx_offset);
    assert(m_num_levels == 6);
    num_channels = m_org[ADDR_CHANNEL_LEVEL];
    panic_if(memSidePorts.size() != num_channels, "Number of memory channels %d != number of memside ports %d\n", num_channels, memSidePorts.size());
    mem_channels_blocked = new bool[num_channels];
    for (int i = 0; i < num_channels; i++) {
        mem_channels_blocked[i] = false;
    }
    panic_if(cacheSidePorts.size() != num_cores, "Number of cores %d != number of cacheside ports %d\n", num_cores, cacheSidePorts.size());
    panic_if(retirementSidePorts.size() != num_cores,
             "Number of cores %d != number of retirement ports %d\n",
             num_cores, retirementSidePorts.size());
    cache_bus_blocked = new bool[num_cores];
    for (int i = 0; i < num_cores; i++) {
        cache_bus_blocked[i] = false;
    }
    for (int i = 0; i < memSidePorts.size(); i++) {
        memSidePorts[i]->allocate(i);
    }
    for (int i = 0; i < num_indirect_units_total; i++) {
        indirectAccessUnits[i].allocate(i, num_tile_elements,
                                        num_offset_table_entries,
                                        num_row_table_rows_per_slice,
                                        num_row_table_entries_per_subslice_row,
                                        num_row_table_config_cache_entries,
                                        reconfigure_row_table,
                                        reorder_row_table,
                                        num_initial_row_table_slices,
                                        virtual_combine_slots,
                                        virtual_combine_words,
                                        virtual_combine_ways,
                                        virtual_combine_victim_policy,
                                        virtual_combine_banks,
                                        virtual_response_slots,
                                        virtual_response_words,
                                        virtual_response_word_pool,
                                        virtual_words_per_cycle,
                                        virtual_max_outstanding_writes,
                                        virtual_masked_writes,
                                        virtual_index_buffer_lines,
                                        virtual_index_force_cache,
                                        virtual_index_partitions,
                                        virtual_index_filter_words_per_cycle,
                                        rowtable_latency,
                                        num_channels,
                                        num_cores,
                                        this);
    }
    my_outstanding_indirect_cache_read_pkts = new std::multiset<OutstandingPacket, CompareByTick>[num_cores];
    my_outstanding_indirect_cache_write_pkts = new std::multiset<OutstandingPacket, CompareByTick>[num_cores];
    my_outstanding_indirect_mem_write_pkts = new std::multiset<OutstandingPacket, CompareByTick>[num_channels];
    my_outstanding_indirect_mem_read_pkts = new std::multiset<OutstandingPacket, CompareByTick>[num_channels];
    my_writeback_last_row = new std::unordered_map<uint64_t, Addr>[num_channels];
    my_outstanding_stream_cache_read_pkts = new std::multiset<OutstandingPacket, CompareByTick>[num_cores];
    my_outstanding_stream_cache_write_pkts = new std::multiset<OutstandingPacket, CompareByTick>[num_cores];
    my_outstanding_stream_mem_write_pkts = new std::multiset<OutstandingPacket, CompareByTick>[num_cores];
    my_outstanding_stream_mem_read_pkts = new std::multiset<OutstandingPacket, CompareByTick>[num_cores];
}
// RoBaRaCoCh address mapping taking from the Ramulator2
int slice_lower_bits(uint64_t &addr, int bits) {
    int lbits = addr & ((1 << bits) - 1);
    addr >>= bits;
    return lbits;
}
std::vector<int> MAA::map_addr(Addr addr) {
    std::vector<int> addr_vec(m_num_levels, -1);
    addr = addr >> m_tx_offset;
    addr_vec[0] = slice_lower_bits(addr, m_addr_bits[0]);
    addr_vec[m_addr_bits.size() - 1] = slice_lower_bits(addr, m_addr_bits[m_addr_bits.size() - 1]);
    for (int i = 1; i <= m_row_bits_idx; i++) {
        addr_vec[i] = slice_lower_bits(addr, m_addr_bits[i]);
    }
    return addr_vec;
}
int MAA::channel_addr(Addr addr) {
    addr = addr >> m_tx_offset;
    return slice_lower_bits(addr, m_addr_bits[0]);
}
void MAA::writeRowKey(Addr paddr, uint64_t &bank_key, Addr &row) {
    // RoBaRaCoCh decompose. Channel is already the per-channel queue index, so
    // the bank is uniquely identified within a channel by (rank, bankgroup, bank).
    // Row buffers are per-bank, so the open-row tracker must key on the bank.
    std::vector<int> v = map_addr(paddr);
    bank_key = (((uint64_t)v[ADDR_RANK_LEVEL] * 1024ULL + (uint64_t)v[ADDR_BANKGROUP_LEVEL]) * 1024ULL) + (uint64_t)v[ADDR_BANK_LEVEL];
    row = (Addr)v[ADDR_ROW_LEVEL];
}
int MAA::core_addr(Addr addr) {
    addr = addr >> m_tx_offset;
    return slice_lower_bits(addr, m_core_addr_bits);
}
bool MAA::allFuncUnitsIdle() {
    if (directRetirementExecution.active)
        return false;
    if (invalidator->getState() != Invalidator::Status::Idle) {
        return false;
    }
    for (int i = 0; i < num_maas; i++) {
        if (streamAccessUnits[i].getState() != StreamAccessUnit::Status::Idle) {
            return false;
        }
        if (aluUnits[i].getState() != ALUUnit::Status::Idle) {
            return false;
        }
        if (rangeUnits[i].getState() != RangeFuserUnit::Status::Idle) {
            return false;
        }
    }
    for (int i = 0; i < num_indirect_units_total; i++) {
        if (indirectAccessUnits[i].getState() != IndirectAccessUnit::Status::Idle) {
            return false;
        }
    }
    return true;
}
bool MAA::getAddrRegionPermit(Instruction *instruction) {
    return invalidator->getAddrRegionPermit(instruction);
}
void MAA::issueInstruction() {
    // This event is also the controller's finite lookup/backpressure retry.
    // Dispatch the generated micro-op before selecting ready functional units.
    tryIssueTransparentMicroOp();
    bool were_all_units_idle = allFuncUnitsIdle();
    bool are_all_units_idle = were_all_units_idle;
    bool issued = true;
    int num_issued = 0;
    while (issued) {
        issued = false;
        if (invalidatorIdle) {
            panic_if(invalidator->getState() != Invalidator::Status::Idle, "Invalidator is not idle!\n");
            Instruction *inst = ifile->getReady(FuncUnitType::INVALIDATOR);
            if (inst != nullptr) {
                invalidator->setInstruction(inst);
                invalidator->scheduleExecuteInstructionEvent(num_issued++);
                are_all_units_idle = false;
                issued = true;
                invalidatorIdle = false;
            }
        }
        int func_unit_type_base = rand() % 4;
        for (int func_unit_type_offset = 0; func_unit_type_offset < 4; func_unit_type_offset++) {
            int func_unit_type = (func_unit_type_base + func_unit_type_offset) % 4;
            int maa_id_base = rand() % num_maas;
            for (int maa_id_offset = 0; maa_id_offset < num_maas; maa_id_offset++) {
                int maa_id = (maa_id_base + maa_id_offset) % num_maas;
                switch (func_unit_type) {
                case 0: {
                    if (streamAccessIdle[maa_id]) {
                        panic_if(streamAccessUnits[maa_id].getState() != StreamAccessUnit::Status::Idle, "StreamAccessUnit[%d] is not idle!\n", maa_id);
                        Instruction *inst = ifile->getReady(FuncUnitType::STREAM, maa_id);
                        if (inst != nullptr) {
                            if (inst->dst1SpdID != -1) {
                                spd->setTileService(inst->dst1SpdID, inst->getWordSize(inst->dst1SpdID));
                            }
                            inst->func_unit_id = maa_id;
                            streamAccessUnits[maa_id].setInstruction(inst);
                            streamAccessUnits[maa_id].scheduleExecuteInstructionEvent(num_issued++);
                            streamAccessIdle[maa_id] = false;
                            are_all_units_idle = false;
                            issued = true;
                        }
                    }
                    break;
                }
                case 1: {
                    for (int lane = 0; lane < num_indirect_units_per_maa; lane++) {
                        int indirect_id = maa_id * num_indirect_units_per_maa + lane;
                        if (indirectAccessIdle[indirect_id]) {
                            panic_if(indirectAccessUnits[indirect_id].getState() != IndirectAccessUnit::Status::Idle, "IndirectAccessUnit[%d] is not idle!\n", indirect_id);
                            Instruction *inst = ifile->getReady(FuncUnitType::INDIRECT, maa_id);
                            if (inst != nullptr) {
                                if (inst->dst1SpdID != -1) {
                                    spd->setTileService(inst->dst1SpdID, inst->getWordSize(inst->dst1SpdID));
                                }
                                inst->func_unit_id = indirect_id;
                                indirectAccessUnits[indirect_id].setInstruction(inst);
                                indirectAccessUnits[indirect_id].scheduleExecuteInstructionEvent(num_issued++);
                                indirectAccessIdle[indirect_id] = false;
                                are_all_units_idle = false;
                                issued = true;
                            }
                        }
                    }
                    break;
                }
                case 2: {
                    if (aluUnitsIdle[maa_id]) {
                        panic_if(aluUnits[maa_id].getState() != ALUUnit::Status::Idle, "ALUUnit[%d] is not idle!\n", maa_id);
                        Instruction *inst = ifile->getReady(FuncUnitType::ALU, maa_id);
                        if (inst != nullptr) {
                            if (inst->dst1SpdID != -1) {
                                spd->setTileService(inst->dst1SpdID, inst->getWordSize(inst->dst1SpdID));
                            }
                            inst->func_unit_id = maa_id;
                            aluUnits[maa_id].setInstruction(inst);
                            aluUnits[maa_id].scheduleExecuteInstructionEvent(num_issued++);
                            aluUnitsIdle[maa_id] = false;
                            are_all_units_idle = false;
                            issued = true;
                        } else {
                            break;
                        }
                    }
                    break;
                }
                case 3: {
                    if (rangeUnitsIdle[maa_id]) {
                        panic_if(rangeUnits[maa_id].getState() != RangeFuserUnit::Status::Idle, "RangeFuserUnit[%d] is not idle!\n", maa_id);
                        Instruction *inst = ifile->getReady(FuncUnitType::RANGE, maa_id);
                        if (inst != nullptr) {
                            if (inst->dst1SpdID != -1) {
                                spd->setTileService(inst->dst1SpdID, inst->getWordSize(inst->dst1SpdID));
                            }
                            if (inst->dst2SpdID != -1) {
                                spd->setTileService(inst->dst2SpdID, inst->getWordSize(inst->dst1SpdID));
                            }
                            inst->func_unit_id = maa_id;
                            rangeUnits[maa_id].setInstruction(inst);
                            rangeUnits[maa_id].scheduleExecuteInstructionEvent(num_issued++);
                            rangeUnitsIdle[maa_id] = false;
                            are_all_units_idle = false;
                            issued = true;
                        }
                    }
                    break;
                }
                default:
                    panic("Invalid func_unit_type %d\n", func_unit_type);
                }
            }
        }
    }
    if (were_all_units_idle && !are_all_units_idle) {
        stats.cycles_IDLE += getTicksToCycles(curTick() - my_last_idle_tick);
    }
}
uint8_t MAA::getTileStatus(InstructionPtr instruction, int tile_id, bool is_dst) {
    if (tile_id == -1)
        return (uint8_t)(Instruction::TileStatus::Finished);

    bool is_dirty = spd->getTileDirty(tile_id);
    SPD::TileStatus status = spd->getTileStatus(tile_id);
    if (instruction->getWordSize(tile_id) == 8) {
        if (spd->getTileDirty(tile_id + 1) == true) {
            is_dirty = true;
        }
        panic_if(spd->getTileStatus(tile_id + 1) != status, "Tile[%d] and Tile[%d] have different statuses %s != %s\n",
                 tile_id, tile_id + 1,
                 spd->tile_status_names[(uint8_t)(spd->getTileStatus(tile_id))],
                 spd->tile_status_names[(uint8_t)(spd->getTileStatus(tile_id + 1))]);
    }
    if (is_dirty) {
        return (uint8_t)(Instruction::TileStatus::WaitForInvalidation);
    }

    if (is_dst) {
        return (uint8_t)(Instruction::TileStatus::WaitForService);
    } else {
        if (status == SPD::TileStatus::Idle) {
            return (uint8_t)(Instruction::TileStatus::WaitForService);
        } else if (status == SPD::TileStatus::Service) {
            return (uint8_t)(Instruction::TileStatus::Service);
        } else if (status == SPD::TileStatus::Finished) {
            return (uint8_t)(Instruction::TileStatus::Finished);
        } else {
            assert(false);
        }
    }
    assert(false);
    return (uint8_t)(Instruction::TileStatus::WaitForService);
}
bool MAA::transparentControllerOwnsTile(int maaID, int tileID) const {
    return transparentController.ownsTile(maaID, tileID);
}
bool MAA::transparentControllerUsesRegister(int maaID, int firstRegister,
                                            int registerWords) const {
    return transparentController.usesRegister(maaID, firstRegister,
                                              registerWords);
}
bool MAA::submitTransparentDescriptor(InstructionPtr instruction,
                                      bool directFallback) {
    panic_if(instruction->opcode !=
                 Instruction::OpcodeType::VIRTUAL_TILE_ALU_SCALAR,
             "Cannot submit non-transparent descriptor %s\n",
             instruction->print());
    if (transparentController.active())
        return false;

    const int register_ids[] = {
        instruction->dst1RegID, instruction->src1RegID,
        instruction->src2RegID, instruction->src3RegID,
    };
    const int register_words[] = {
        instruction->WordSize() / static_cast<int>(sizeof(uint32_t)), 1, 1, 1,
    };
    const auto spans_overlap = [](int lhs, int lhs_words, int rhs,
                                  int rhs_words) {
        return lhs < rhs + rhs_words && rhs < lhs + lhs_words;
    };
    for (const RegisterPtr pending : my_registers) {
        const int pending_words = pending->size / sizeof(uint32_t);
        for (int i = 0; i < 4; ++i) {
            if (spans_overlap(pending->register_id, pending_words,
                              register_ids[i], register_words[i]))
                return false;
        }
    }

    panic_if(num_tile_elements != TransparentSPDController::LogicalElements ||
                 physical_tile_elements !=
                     TransparentSPDController::PageElements,
             "Transparent controller requires logical/physical elements "
             "%d/%d, got %u/%u\n",
             TransparentSPDController::LogicalElements,
             TransparentSPDController::PageElements, num_tile_elements,
             physical_tile_elements);
    panic_if(instruction->datatype != Instruction::DataType::FLOAT64_TYPE ||
                 instruction->optype != Instruction::OPType::MUL_OP,
             "Transparent controller currently supports only FLOAT64 "
             "scalar multiply, got %s\n",
             instruction->print());

    const int word_size = instruction->WordSize();
    const int tile_words = word_size / sizeof(uint32_t);
    const auto valid_tile_span = [&](int first) {
        return first >= 0 && first + tile_words <= static_cast<int>(num_tiles);
    };
    panic_if(!valid_tile_span(instruction->src1SpdID) ||
                 !valid_tile_span(instruction->dst1SpdID) ||
                 !valid_tile_span(instruction->dst2SpdID),
             "Transparent descriptor has an invalid double-width tile span: "
             "token=%d physical=%d output=%d tiles=%u\n",
             instruction->src1SpdID, instruction->dst1SpdID,
             instruction->dst2SpdID, num_tiles);
    const auto tile_spans_overlap = [tile_words](int lhs, int rhs) {
        return lhs < rhs + tile_words && rhs < lhs + tile_words;
    };
    panic_if(tile_spans_overlap(instruction->src1SpdID,
                                instruction->dst1SpdID) ||
                 tile_spans_overlap(instruction->src1SpdID,
                                    instruction->dst2SpdID) ||
                 tile_spans_overlap(instruction->dst1SpdID,
                                    instruction->dst2SpdID),
             "Transparent descriptor tile spans overlap: token=%d "
             "physical=%d output=%d\n",
             instruction->src1SpdID, instruction->dst1SpdID,
             instruction->dst2SpdID);
    panic_if(!ifile->isCompletionOnlyTile(instruction->maa_id,
                                          instruction->src1SpdID),
             "Transparent descriptor token tile %d is not a logical virtual "
             "completion token\n",
             instruction->src1SpdID);
    for (int offset = 0; offset < tile_words; ++offset) {
        if (ifile->hasTileReference(instruction->maa_id,
                                    instruction->dst1SpdID + offset) ||
            ifile->hasTileReference(instruction->maa_id,
                                    instruction->dst2SpdID + offset))
            return false;
    }

    const int reg_words = word_size / sizeof(uint32_t);
    panic_if(instruction->dst1RegID < 0 ||
                 instruction->dst1RegID + reg_words >
                     static_cast<int>(num_regs) ||
                 instruction->src1RegID < 0 ||
                 instruction->src2RegID < 0 ||
                 instruction->src3RegID < 0 ||
                 instruction->src1RegID >= static_cast<int>(num_regs) ||
                 instruction->src2RegID >= static_cast<int>(num_regs) ||
                 instruction->src3RegID >= static_cast<int>(num_regs),
             "Transparent descriptor register span is invalid\n");
    panic_if(rf->getData<int32_t>(instruction->src1RegID) != 0 ||
                 rf->getData<int32_t>(instruction->src2RegID) !=
                     TransparentSPDController::PageElements ||
                 rf->getData<int32_t>(instruction->src3RegID) != 1,
             "Transparent page range registers must be min=0 max=%d "
             "stride=1\n",
             TransparentSPDController::PageElements);

    TransparentSPDController::Descriptor descriptor;
    descriptor.tokenTile = instruction->src1SpdID;
    descriptor.physicalTile = instruction->dst1SpdID;
    descriptor.outputTile = instruction->dst2SpdID;
    descriptor.scaleReg = instruction->dst1RegID;
    descriptor.minReg = instruction->src1RegID;
    descriptor.maxReg = instruction->src2RegID;
    descriptor.strideReg = instruction->src3RegID;
    descriptor.wordSize = word_size;
    descriptor.logicalElements = num_tile_elements;
    descriptor.pageElements = physical_tile_elements;
    descriptor.coreID = instruction->core_id;
    descriptor.maaID = instruction->maa_id;
    descriptor.contextID = instruction->CID;
    const int token_tile = instruction->src1SpdID;
    panic_if(virtualPageGeneration[token_tile] == 0 ||
                 virtualPageGeneration[token_tile] ==
                     virtualPageConsumedGeneration[token_tile],
             "Transparent token %d has no unconsumed producer generation\n",
             token_tile);
    panic_if(virtualPageBackingAddr[token_tile] != instruction->baseAddr ||
                 virtualPageWordSize[token_tile] != word_size,
             "Transparent token %d generation %lu does not name backing "
             "0x%lx/%d\n",
             token_tile, virtualPageGeneration[token_tile],
             instruction->baseAddr, word_size);
    descriptor.generation = virtualPageGeneration[token_tile];
    descriptor.dataType = static_cast<uint8_t>(instruction->datatype);
    descriptor.operation = static_cast<uint8_t>(instruction->optype);
    descriptor.pc = instruction->PC;
    descriptor.backingAddr = instruction->baseAddr;
    descriptor.backingMinAddr = instruction->minAddr;
    descriptor.backingMaxAddr = instruction->maxAddr;
    descriptor.backingRangeID = instruction->addrRangeID;
    descriptor.destinationAddr = instruction->backingAddr;
    descriptor.destinationMinAddr = instruction->backingMinAddr;
    descriptor.destinationMaxAddr = instruction->backingMaxAddr;
    descriptor.destinationRangeID = instruction->backingAddrRangeID;
    // Selector 3 reserves the direct path for the exact full-line contract.
    // An ineligible instruction must retain the existing serial-4K
    // StreamAccess/RMW behavior rather than becoming a new unsupported ABI.
    descriptor.mode = directFallback
        ? TransparentSPDController::Mode::Serial4K
        : static_cast<TransparentSPDController::Mode>(transparent_spd_mode);

    const char *validation =
        TransparentSPDController::validate(descriptor);
    panic_if(validation != nullptr, "Invalid transparent descriptor: %s\n",
             validation == nullptr ? "unknown" : validation);
    const auto result = transparentController.submit(descriptor);
    panic_if(result != TransparentSPDController::SubmitResult::Accepted,
             "Validated transparent descriptor was not accepted\n");
    transparentControllerLookupReadyTick =
        getClockEdge(Cycles(TransparentSPDController::ControllerLookupCycles));
    panic_if(!transparentMacroTracker.begin(curTick()),
             "Transparent macro tracker was active at descriptor submit\n");
    transparentMacroAllReadyRecord = {};
    transparentMacroAllReadyTick = 0;
    transparentMacroAllReadySampled = false;
    transparentMacroAllReadyBeforeSubmit = false;

    // These two credits cover the complete descriptor lifetime.  Native
    // micro-ops take and return their own credits independently.
    spd->setTileNotReady(descriptor.physicalTile, descriptor.wordSize);
    spd->setTileNotReady(descriptor.outputTile, descriptor.wordSize);
    for (int page = 0; page < TransparentSPDController::NumPages; ++page) {
        if (!getVirtualPageReady(descriptor.tokenTile, page))
            continue;
        panic_if(!transparentController.notifyPageReady(
                     descriptor.tokenTile, page),
                 "Failed to import ready page %d for token %d\n", page,
                 descriptor.tokenTile);
    }
    bool all_pages_ready = true;
    for (int page = 0; page < TransparentSPDController::NumPages; ++page) {
        all_pages_ready = all_pages_ready &&
            getVirtualPageReady(descriptor.tokenTile, page);
    }
    if (all_pages_ready) {
        panic_if(!transparentMacroTracker.sample(curTick()),
                 "Failed to sample macro tracker at submit\n");
        transparentMacroAllReadyRecord = transparentMacroTracker.result();
        transparentMacroAllReadyTick =
            virtualPageLastReadyTick[descriptor.tokenTile];
        transparentMacroAllReadySampled = true;
        transparentMacroAllReadyBeforeSubmit = true;
    }
    startTransparentBlockerTracking();
    DPRINTF(MAAVirtualTrace,
            "event=transparent_submit schema=2 occurrence=%lu token=%d "
            "physical=%d output=%d "
            "generation=%lu logical=%d page=%d pages=%d\n",
            transparentTraceOccurrence++, descriptor.tokenTile,
            descriptor.physicalTile,
            descriptor.outputTile, descriptor.generation,
            descriptor.logicalElements, descriptor.pageElements,
            TransparentSPDController::NumPages);
    DPRINTF(MAAVirtualTrace,
            "event=transparent_ping_submit mode=%u chunks=%d "
            "chunk_elements=%d\n",
            static_cast<unsigned>(descriptor.mode),
            transparentController.chunks(),
            transparentController.elementsPerChunk());
    tryIssueTransparentMicroOp();
    return true;
}

bool
MAA::submitDirectRetirementDescriptor(InstructionPtr instruction)
{
    panic_if(instruction->opcode !=
                 Instruction::OpcodeType::VIRTUAL_TILE_ALU_SCALAR,
             "Cannot submit invalid direct-retirement descriptor\n");
    if (directRetirementExecution.active || transparentController.active())
        return false;
    const int word_size = instruction->WordSize();
    const int tile_words = word_size / sizeof(uint32_t);
    const auto valid_tile_span = [&](int first) {
        return first >= 0 &&
            first + tile_words <= static_cast<int>(num_tiles);
    };
    const bool direct_eligible =
        num_tile_elements == HybridConsumerPipeline::LogicalElements &&
        physical_tile_elements ==
            HybridConsumerPipeline::ProducerPageElements &&
        instruction->datatype == Instruction::DataType::FLOAT64_TYPE &&
        instruction->optype == Instruction::OPType::MUL_OP &&
        word_size == static_cast<int>(sizeof(double)) &&
        valid_tile_span(instruction->src1SpdID) &&
        valid_tile_span(instruction->dst2SpdID) &&
        (instruction->src1SpdID + tile_words <= instruction->dst2SpdID ||
         instruction->dst2SpdID + tile_words <= instruction->src1SpdID) &&
        instruction->baseAddr % HybridConsumerPipeline::LineBytes == 0 &&
        instruction->backingAddr % HybridConsumerPipeline::LineBytes == 0;
    if (!direct_eligible) {
        stats.direct_retirement_fallbacks++;
        DPRINTF(MAAVirtualTrace,
                "event=direct_retirement_fallback schema=1 occurrence=%lu "
                "logical=%u physical=%u word_bytes=%d source=0x%lx "
                "destination=0x%lx\n",
                directRetirementTraceOccurrence++, num_tile_elements,
                physical_tile_elements, word_size, instruction->baseAddr,
                instruction->backingAddr);
        return submitTransparentDescriptor(instruction, true);
    }
    const int token_tile = instruction->src1SpdID;
    panic_if(token_tile < 0 || token_tile >= num_tiles ||
                 !ifile->isCompletionOnlyTile(instruction->maa_id,
                                              token_tile),
             "Direct retirement token is not a live completion-only tile\n");
    for (int offset = 0; offset < tile_words; ++offset) {
        if (ifile->hasTileReference(instruction->maa_id,
                                    instruction->dst2SpdID + offset))
            return false;
    }
    panic_if(virtualPageGeneration[token_tile] == 0 ||
                 virtualPageGeneration[token_tile] ==
                     virtualPageConsumedGeneration[token_tile] ||
                 virtualPageBackingAddr[token_tile] != instruction->baseAddr ||
                 virtualPageWordSize[token_tile] != word_size,
             "Direct retirement does not name an unconsumed matching "
             "producer generation\n");
    panic_if(instruction->dst1RegID < 0 ||
                 instruction->dst1RegID + word_size / sizeof(uint32_t) >
                     static_cast<int>(num_regs),
             "Direct retirement scalar register is invalid\n");

    HybridConsumerPipeline::Descriptor descriptor;
    descriptor.generation = virtualPageGeneration[token_tile];
    descriptor.logicalElements = HybridConsumerPipeline::LogicalElements;
    descriptor.wordBytes = word_size;
    descriptor.backingAddress = instruction->baseAddr;
    descriptor.backingRangeMin = instruction->minAddr;
    descriptor.backingRangeMax = instruction->maxAddr;
    descriptor.backingRangeID = instruction->addrRangeID;
    descriptor.destinationAddress = instruction->backingAddr;
    descriptor.destinationRangeMin = instruction->backingMinAddr;
    descriptor.destinationRangeMax = instruction->backingMaxAddr;
    descriptor.destinationRangeID = instruction->backingAddrRangeID;
    for (uint8_t page = 0; page < HybridConsumerPipeline::ProducerPages;
         ++page) {
        if (getVirtualPageReady(token_tile, page)) {
            descriptor.producerTransactions[page] =
                getVirtualPageReadyTransaction(token_tile, page);
        }
    }
    const char *validation = HybridConsumerPipeline::validate(descriptor);
    if (validation != nullptr) {
        stats.direct_retirement_fallbacks++;
        DPRINTF(MAAVirtualTrace,
                "event=direct_retirement_fallback schema=1 occurrence=%lu "
                "reason=%s source=0x%lx destination=0x%lx\n",
                directRetirementTraceOccurrence++, validation,
                descriptor.backingAddress, descriptor.destinationAddress);
        return submitTransparentDescriptor(instruction, true);
    }
    panic_if(directRetirement.submit(descriptor) !=
                 HybridConsumerPipeline::SubmitResult::Accepted,
             "Validated direct-retirement descriptor was not accepted\n");

    DirectRetirementExecution execution;
    execution.active = true;
    execution.coreID = instruction->core_id;
    execution.maaID = instruction->maa_id;
    execution.tokenTile = token_tile;
    execution.completionTile = instruction->dst2SpdID;
    execution.generation = descriptor.generation;
    execution.datatype = static_cast<uint8_t>(instruction->datatype);
    execution.operation = static_cast<uint8_t>(instruction->optype);
    execution.wordBytes = word_size;
    const double scalar = rf->getData<double>(instruction->dst1RegID);
    std::memcpy(&execution.scalarBits, &scalar, sizeof(scalar));
    execution.backingAddress = descriptor.backingAddress;
    execution.backingRangeID = instruction->addrRangeID;
    execution.destinationRangeID = instruction->backingAddrRangeID;
    execution.contextID = instruction->CID;
    execution.pc = instruction->PC;
    panic_if(!execution.macro.begin(curTick()),
             "Direct-retirement macro tracker was already active\n");
    directRetirementExecution = std::move(execution);
    // There is no result SPD payload. Keep the existing destination tile ID
    // only as an asynchronous completion token for later dependent work.
    spd->setTileNotReady(directRetirementExecution.completionTile,
                         word_size);
    const uint64_t charged_payload_bytes =
        HybridConsumerPipeline::chargedPayloadBytes();
    const uint64_t charged_control_bytes =
        HybridConsumerPipeline::chargedControlBytes() +
        sizeof(DirectRetirementExecution) +
        HybridConsumerPipeline::LineBufferCount * sizeof(Addr);
    stats.direct_retirement_descriptors++;
    stats.direct_retirement_payload_bytes = std::max(
        stats.direct_retirement_payload_bytes.value(),
        static_cast<double>(charged_payload_bytes));
    stats.direct_retirement_control_bytes = std::max(
        stats.direct_retirement_control_bytes.value(),
        static_cast<double>(charged_control_bytes));

    for (uint8_t page = 0; page < HybridConsumerPipeline::ProducerPages;
         ++page) {
        if (!getVirtualPageReady(token_tile, page))
            continue;
        const uint64_t transaction =
            getVirtualPageReadyTransaction(token_tile, page);
        const uint16_t fallback_before =
            directRetirement.producerPageFallbackLineCount();
        panic_if(!directRetirement.notifyProducerWriteAck(
                     {descriptor.generation, page, transaction}),
                 "Direct retirement rejected already-acknowledged producer "
                 "page %u\n", page);
        stats.direct_retirement_producer_acks++;
        stats.direct_retirement_page_fallback_lines +=
            directRetirement.producerPageFallbackLineCount() -
            fallback_before;
        DPRINTF(MAAVirtualTrace,
                "event=direct_retirement_producer_ack schema=1 "
                "occurrence=%lu generation=%lu page=%u transaction=%lu\n",
                directRetirementTraceOccurrence++, descriptor.generation,
                page, transaction);
    }
    DPRINTF(MAAVirtualTrace,
            "event=direct_retirement_submit schema=1 occurrence=%lu "
            "generation=%lu token=%d source=0x%lx destination=0x%lx "
            "scope=terminal_fp64_mul_dense_store credits=%u "
            "payload_bytes=%lu control_bytes=%lu total_bytes=%lu "
            "backing_span_bytes=%lu private_page_payload_bytes=0\n",
            directRetirementTraceOccurrence++, descriptor.generation,
            token_tile, descriptor.backingAddress,
            descriptor.destinationAddress,
            HybridConsumerPipeline::LineBufferCount,
            charged_payload_bytes, charged_control_bytes,
            charged_payload_bytes + charged_control_bytes,
            static_cast<uint64_t>(descriptor.logicalElements) *
                descriptor.wordBytes);
    scheduleDirectRetirementEvent();
    return true;
}

PacketPtr
MAA::makeDirectRetirementPacket(
    const HybridConsumerPipeline::Request &request)
{
    panic_if(!directRetirementExecution.active ||
                 (request.kind != HybridConsumerPipeline::Kind::ReadBacking &&
                  request.kind !=
                      HybridConsumerPipeline::Kind::WriteDestination) ||
                 request.size != HybridConsumerPipeline::LineBytes ||
                 request.buffer >= HybridConsumerPipeline::LineBufferCount,
             "Direct retirement produced an invalid cache-line request\n");
    const int region =
        request.kind == HybridConsumerPipeline::Kind::ReadBacking
        ? directRetirementExecution.backingRangeID
        : directRetirementExecution.destinationRangeID;
    panic_if(region < 0 || getAddrRegion(request.address) != region,
             "Direct-retirement address 0x%lx escaped its registered range\n",
             request.address);
    RequestPtr translationRequest = std::make_shared<Request>(
        request.address, request.size, Request::Flags(0), requestorId,
        directRetirementExecution.pc, directRetirementExecution.contextID);
    ImmediateLogicalSPDTranslation translation;
    ThreadContext *tc = system->threads[directRetirementExecution.contextID];
    const BaseMMU::Mode mode =
        request.kind == HybridConsumerPipeline::Kind::ReadBacking
            ? BaseMMU::Read : BaseMMU::Write;
    mmu->translateTiming(translationRequest, tc, &translation, mode);
    panic_if(translation.delayed || !translation.finished ||
                 translation.fault != NoFault,
             "Direct retirement requires immediate valid translation for "
             "0x%lx\n", request.address);
    RequestPtr realRequest = std::make_shared<Request>(
        translation.address, request.size, Request::Flags(0), requestorId);
    realRequest->setRegion(region);
    PacketPtr packet = new Packet(
        realRequest, request.kind == HybridConsumerPipeline::Kind::ReadBacking
                         ? MemCmd::ReadReq : MemCmd::WriteReq);
    // The bounded credit-owned buffers are the packet storage: a read fills
    // the credit directly, ALU updates it in place, and WriteReq retains it
    // until its exact WriteResp. No page payload or shadow write queue exists.
    packet->dataStatic(reinterpret_cast<uint8_t *>(
        directRetirement.bufferData(request.buffer)));
    auto *state = new DirectRetirementSenderState;
    state->request = request;
    // Cache-bank routing follows the translated physical address. The pure
    // scheduler's virtual-address port remains part of its unit-test identity.
    state->callbackPort = core_addr(translation.address);
    packet->pushSenderState(state);
    return packet;
}

bool
MAA::recvDirectRetirementTimingResp(PacketPtr pkt, uint8_t respondingPort)
{
    auto *peek = dynamic_cast<DirectRetirementSenderState *>(pkt->senderState);
    if (peek == nullptr)
        return false;
    auto *state = dynamic_cast<DirectRetirementSenderState *>(
        pkt->popSenderState());
    panic_if(state == nullptr || !directRetirementExecution.active ||
                 state->callbackPort != respondingPort,
             "Direct-retirement response lost exact port provenance\n");
    const Addr paddr = pkt->getAddr();
    panic_if(directRetirementOutstandingAddresses.erase(paddr) != 1,
             "Direct-retirement response at 0x%lx did not own an exact "
             "address reservation\n", paddr);
    const auto &request = state->request;
    bool accepted = false;
    if (request.kind == HybridConsumerPipeline::Kind::ReadBacking) {
        panic_if(pkt->cmd != MemCmd::ReadResp ||
                     pkt->getSize() != HybridConsumerPipeline::LineBytes,
                 "Direct retirement read did not receive an exact ReadResp\n");
        accepted = directRetirement.completeRead(
            request, reinterpret_cast<const std::byte *>(
                         pkt->getConstPtr<uint8_t>()), pkt->getSize());
        panic_if(!accepted || !directRetirementExecution.macro.complete(
                                 HybridMacroEventTracker::Stage::PageFill,
                                 curTick()),
                 "Direct retirement rejected a read completion\n");
        stats.direct_retirement_read_responses++;
    } else {
        panic_if(pkt->cmd != MemCmd::WriteResp ||
                     pkt->getSize() != HybridConsumerPipeline::LineBytes,
                 "Direct retirement write did not receive an exact "
                 "WriteResp\n");
        accepted = directRetirement.completeWriteAck(request);
        panic_if(!accepted || !directRetirementExecution.macro.complete(
                                 HybridMacroEventTracker::Stage::StreamStore,
                                 curTick()),
                 "Direct retirement rejected a write completion\n");
        stats.direct_retirement_write_responses++;
    }
    delete state;
    DPRINTF(MAAVirtualTrace,
            "event=direct_retirement_response schema=1 occurrence=%lu "
            "generation=%lu line=%u buffer=%u action=%u credits_in_use=%u\n",
            directRetirementTraceOccurrence++,
            directRetirementExecution.generation, request.line,
            request.buffer, static_cast<unsigned>(request.kind),
            directRetirement.creditsInUse());
    sendNextDeferredPacket(paddr);
    scheduleDirectRetirementEvent();
    return true;
}

void
MAA::completeDirectRetirementALU(int maaID, uint64_t transactionID)
{
    panic_if(!directRetirementExecution.active ||
                 directRetirementExecution.maaID != maaID ||
                 directRetirementExecution.aluRequest.transactionID !=
                     transactionID ||
                 !directRetirement.completeCompute(
                     directRetirementExecution.aluRequest) ||
                 !directRetirementExecution.macro.complete(
                     HybridMacroEventTracker::Stage::ALU, curTick()),
             "Direct-retirement ALU completion lost its line ownership\n");
    directRetirementExecution.aluRequest = {};
    aluUnitsIdle[maaID] = true;
    stats.direct_retirement_alu_completions++;
    DPRINTF(MAAVirtualTrace,
            "event=direct_retirement_alu_complete schema=1 occurrence=%lu "
            "generation=%lu transaction=%lu\n",
            directRetirementTraceOccurrence++,
            directRetirementExecution.generation, transactionID);
    scheduleDirectRetirementEvent();
    scheduleIssueInstructionEvent(1);
}

void
MAA::scheduleDirectRetirementEvent(int latency)
{
    const Tick when = getClockEdge(Cycles(latency));
    if (!directRetirementEvent.scheduled()) {
        schedule(directRetirementEvent, when);
    } else if (when < directRetirementEvent.when()) {
        reschedule(directRetirementEvent, when);
    }
}

void
MAA::notifyDirectRetirementPortEvent(uint8_t port)
{
    if (directRetirementExecution.active)
        scheduleDirectRetirementEvent();
    DPRINTF(MAAVirtualTrace,
            "event=direct_retirement_port_wake schema=1 occurrence=%lu "
            "port=%u active=%d\n", directRetirementTraceOccurrence++,
            port, directRetirementExecution.active);
}

void
MAA::finishDirectRetirement()
{
    panic_if(!directRetirementExecution.active ||
                 !directRetirement.complete() ||
                 directRetirement.creditsInUse() != 0 ||
                 !directRetirementOutstandingAddresses.empty() ||
                 !directRetirementExecution.macro.finish(curTick()),
             "Direct-retirement completion was attempted before all credits "
             "closed\n");
    const auto record = directRetirementExecution.macro.result();
    const uint64_t generation = directRetirementExecution.generation;
    const int token_tile = directRetirementExecution.tokenTile;
    const int completion_tile = directRetirementExecution.completionTile;
    const int word_bytes = directRetirementExecution.wordBytes;
    panic_if(virtualPageGeneration[token_tile] != generation,
             "Direct-retirement token generation changed before final ACK\n");
    panic_if(directRetirement.producerLineAckCount() +
                 directRetirement.producerPageFallbackLineCount() !=
                 directRetirement.lines(),
             "Direct-retirement producer visibility did not close exactly\n");
    virtualPageConsumedGeneration[token_tile] = generation;
    stats.direct_retirement_overlap_ticks += record.overlapTicks;
    stats.direct_retirement_active_stage_high_water = std::max(
        stats.direct_retirement_active_stage_high_water.value(),
        static_cast<double>(record.activeStageHighWater));
    stats.direct_retirement_credit_high_water = std::max(
        stats.direct_retirement_credit_high_water.value(),
        static_cast<double>(directRetirement.creditHighWater()));
    DPRINTF(MAAVirtualTrace,
            "event=direct_retirement_summary schema=1 occurrence=%lu "
            "generation=%lu reads=%u computes=%u writes=%u "
            "credit_high_water=%u overlap_ticks=%lu "
            "active_stage_high_water=%lu line_acks=%u "
            "page_fallback_lines=%u fallback_count=%lu\n",
            directRetirementTraceOccurrence++, generation,
            directRetirement.readsAccepted(),
            directRetirement.computesAccepted(),
            directRetirement.writesAccepted(),
            directRetirement.creditHighWater(),
            record.overlapTicks, record.activeStageHighWater,
            directRetirement.producerLineAckCount(),
            directRetirement.producerPageFallbackLineCount(),
            static_cast<uint64_t>(
                stats.direct_retirement_fallbacks.value()));
    panic_if(!directRetirement.retire(),
             "Direct-retirement scheduler did not retire after final ACK\n");
    directRetirementExecution = DirectRetirementExecution{};
    setTileReady(completion_tile, word_bytes);
    DPRINTF(MAAVirtualTrace,
            "event=direct_retirement_retire schema=1 occurrence=%lu "
            "generation=%lu final_write_responses=%u\n",
            directRetirementTraceOccurrence++, generation,
            HybridConsumerPipeline::MaxLines);
}

void
MAA::serviceDirectRetirement()
{
    if (!directRetirementExecution.active)
        return;
    if (directRetirement.complete()) {
        finishDirectRetirement();
        return;
    }

    auto accepted = [this](const HybridConsumerPipeline::Request &request) {
        panic_if(!directRetirement.accept(request),
                 "Direct-retirement accepted packet had stale ownership\n");
        const auto stage =
            request.kind == HybridConsumerPipeline::Kind::ReadBacking
            ? HybridMacroEventTracker::Stage::PageFill
            : HybridMacroEventTracker::Stage::StreamStore;
        panic_if(!directRetirementExecution.macro.issue(
                     stage, curTick(), directRetirement.creditsInUse()) ||
                     !directRetirementExecution.macro.traffic(
                         stage, 1, HybridConsumerPipeline::LineBytes),
                 "Direct-retirement could not record a live cache request\n");
        if (request.kind == HybridConsumerPipeline::Kind::ReadBacking)
            stats.direct_retirement_read_issues++;
        else
            stats.direct_retirement_write_issues++;
        stats.direct_retirement_credit_high_water = std::max(
            stats.direct_retirement_credit_high_water.value(),
            static_cast<double>(directRetirement.creditHighWater()));
        DPRINTF(MAAVirtualTrace,
                "event=direct_retirement_issue schema=1 occurrence=%lu "
                "generation=%lu line=%u buffer=%u action=%u address=0x%lx "
                "credits_in_use=%u\n",
                directRetirementTraceOccurrence++,
                directRetirementExecution.generation, request.line,
                request.buffer, static_cast<unsigned>(request.kind),
                request.address, directRetirement.creditsInUse());
    };

    if (directRetirementExecution.retryPacket != nullptr) {
        auto *state = dynamic_cast<DirectRetirementSenderState *>(
            directRetirementExecution.retryPacket->senderState);
        panic_if(state == nullptr,
                 "Direct-retirement retry lost sender identity\n");
        const Addr paddr = directRetirementExecution.retryPacket->getAddr();
        const auto deferred = my_deferred_pkt_map.find(paddr);
        if (hasOutstandingPacket(paddr) ||
            directRetirementOutstandingAddresses.find(paddr) !=
                directRetirementOutstandingAddresses.end() ||
            (deferred != my_deferred_pkt_map.end() &&
             !deferred->second.empty())) {
            stats.direct_retirement_address_stalls++;
            return;
        }
        if (!sendPacketCache(directRetirementExecution.retryPacket)) {
            stats.direct_retirement_retries++;
            return;
        }
        const HybridConsumerPipeline::Request request = state->request;
        directRetirementExecution.retryPacket = nullptr;
        panic_if(!directRetirementOutstandingAddresses.insert(paddr).second,
                 "Direct-retirement retry duplicated address 0x%lx\n", paddr);
        accepted(request);
        return;
    }

    auto discardUnsentPacket = [](PacketPtr packet) {
        auto *state = dynamic_cast<DirectRetirementSenderState *>(
            packet->popSenderState());
        panic_if(state == nullptr,
                 "Unsent direct-retirement packet lost sender state\n");
        delete state;
        delete packet;
    };

    for (unsigned attempt = 0;
         attempt < HybridConsumerPipeline::LineBufferCount + 2; ++attempt) {
        const auto write = directRetirement.pendingWrite();
        const auto compute = directRetirement.pendingCompute();
        const auto read = directRetirement.pendingRead();
        if (write.kind == HybridConsumerPipeline::Kind::None &&
            compute.kind == HybridConsumerPipeline::Kind::None &&
            read.kind == HybridConsumerPipeline::Kind::None) {
            if (directRetirement.creditsInUse() ==
                    HybridConsumerPipeline::LineBufferCount &&
                directRetirement.completed() != directRetirement.lines())
                stats.direct_retirement_credit_stalls++;
            return;
        }
        if (write.kind != HybridConsumerPipeline::Kind::None ||
            read.kind != HybridConsumerPipeline::Kind::None) {
            const auto request =
                write.kind != HybridConsumerPipeline::Kind::None
                ? write : read;
            PacketPtr packet = makeDirectRetirementPacket(request);
            const Addr paddr = packet->getAddr();
            const auto deferred = my_deferred_pkt_map.find(paddr);
            if (hasOutstandingPacket(paddr) ||
                directRetirementOutstandingAddresses.find(paddr) !=
                    directRetirementOutstandingAddresses.end() ||
                (deferred != my_deferred_pkt_map.end() &&
                 !deferred->second.empty())) {
                discardUnsentPacket(packet);
                stats.direct_retirement_address_stalls++;
                return;
            }
            if (!sendPacketCache(packet)) {
                directRetirementExecution.retryPacket = packet;
                stats.direct_retirement_retries++;
                return;
            }
            panic_if(!directRetirementOutstandingAddresses.insert(paddr)
                         .second,
                     "Direct-retirement duplicated address 0x%lx\n", paddr);
            accepted(request);
            continue;
        }
        panic_if(compute.kind == HybridConsumerPipeline::Kind::None,
                 "Direct-retirement scheduler lost a runnable line\n");
        if (!aluUnitsIdle[directRetirementExecution.maaID])
            return;
        panic_if(!directRetirement.accept(compute) ||
                     !directRetirementExecution.macro.issue(
                         HybridMacroEventTracker::Stage::ALU, curTick(), 1) ||
                     !aluUnits[directRetirementExecution.maaID]
                          .startDirectLine(
                         directRetirement.bufferData(compute.buffer),
                         directRetirementExecution.wordBytes,
                         directRetirementExecution.datatype,
                         directRetirementExecution.operation,
                         directRetirementExecution.scalarBits,
                         compute.transactionID),
                 "Direct-retirement could not claim the existing ALU lane\n");
        directRetirementExecution.aluRequest = compute;
        aluUnitsIdle[directRetirementExecution.maaID] = false;
        stats.direct_retirement_alu_issues++;
        DPRINTF(MAAVirtualTrace,
                "event=direct_retirement_alu_issue schema=1 occurrence=%lu "
                "generation=%lu line=%u buffer=%u transaction=%lu\n",
                directRetirementTraceOccurrence++,
                directRetirementExecution.generation, compute.line,
                compute.buffer, compute.transactionID);
        return;
    }
}

void MAA::startTransparentBlockerTracking() {
    transparentBlockerTicks.fill(0);
    transparentInstructionFileBlocked = false;
    transparentBlockerTracking = true;
    transparentBlockerLastTick = curTick();
    transparentLastBlocker = transparentController.blocker();
}
void MAA::updateTransparentBlockerTracking() {
    if (!transparentBlockerTracking)
        return;
    panic_if(curTick() < transparentBlockerLastTick,
             "Transparent blocker clock moved backwards\n");
    transparentBlockerTicks[static_cast<size_t>(transparentLastBlocker)] +=
        curTick() - transparentBlockerLastTick;
    transparentBlockerLastTick = curTick();
    transparentLastBlocker = transparentInstructionFileBlocked
        ? TransparentSPDController::Blocker::InstructionFileFull
        : transparentController.blocker();
}
void MAA::snapshotTransparentBlockerTracking(uint64_t generation) {
    updateTransparentBlockerTracking();
    Tick total = 0;
    for (Tick ticks : transparentBlockerTicks)
        total += ticks;
    using Blocker = TransparentSPDController::Blocker;
    DPRINTF(MAAVirtualTrace,
            "event=transparent_blocker_snapshot schema=2 occurrence=%lu "
            "generation=%lu point=all_pages_ready total_ticks=%lu "
            "runnable_ticks=%lu producer_not_ready_ticks=%lu "
            "stream_busy_ticks=%lu alu_busy_ticks=%lu "
            "slot_owned_ticks=%lu serialization_ticks=%lu "
            "transition_ticks=%lu if_full_ticks=%lu other_ticks=%lu "
            "inactive_ticks=%lu\n",
            transparentTraceOccurrence++, generation, total,
            transparentBlockerTicks[static_cast<size_t>(Blocker::Runnable)],
            transparentBlockerTicks[
                static_cast<size_t>(Blocker::ProducerNotReady)],
            transparentBlockerTicks[static_cast<size_t>(Blocker::StreamBusy)],
            transparentBlockerTicks[static_cast<size_t>(Blocker::ALUBusy)],
            transparentBlockerTicks[static_cast<size_t>(Blocker::SlotOwned)],
            transparentBlockerTicks[
                static_cast<size_t>(Blocker::Serialization)],
            transparentBlockerTicks[static_cast<size_t>(Blocker::Transition)],
            transparentBlockerTicks[
                static_cast<size_t>(Blocker::InstructionFileFull)],
            transparentBlockerTicks[static_cast<size_t>(Blocker::Other)],
            transparentBlockerTicks[static_cast<size_t>(Blocker::Inactive)]);
}
void MAA::finishTransparentBlockerTracking(uint64_t generation) {
    updateTransparentBlockerTracking();
    Tick total = 0;
    for (Tick ticks : transparentBlockerTicks)
        total += ticks;
    using Blocker = TransparentSPDController::Blocker;
    DPRINTF(MAAVirtualTrace,
            "event=transparent_blocker_summary schema=2 occurrence=%lu "
            "generation=%lu total_ticks=%lu runnable_ticks=%lu "
            "producer_not_ready_ticks=%lu stream_busy_ticks=%lu "
            "alu_busy_ticks=%lu slot_owned_ticks=%lu "
            "serialization_ticks=%lu transition_ticks=%lu "
            "if_full_ticks=%lu other_ticks=%lu inactive_ticks=%lu\n",
            transparentTraceOccurrence++, generation, total,
            transparentBlockerTicks[static_cast<size_t>(Blocker::Runnable)],
            transparentBlockerTicks[
                static_cast<size_t>(Blocker::ProducerNotReady)],
            transparentBlockerTicks[static_cast<size_t>(Blocker::StreamBusy)],
            transparentBlockerTicks[static_cast<size_t>(Blocker::ALUBusy)],
            transparentBlockerTicks[static_cast<size_t>(Blocker::SlotOwned)],
            transparentBlockerTicks[
                static_cast<size_t>(Blocker::Serialization)],
            transparentBlockerTicks[static_cast<size_t>(Blocker::Transition)],
            transparentBlockerTicks[
                static_cast<size_t>(Blocker::InstructionFileFull)],
            transparentBlockerTicks[static_cast<size_t>(Blocker::Other)],
            transparentBlockerTicks[static_cast<size_t>(Blocker::Inactive)]);
    transparentBlockerTracking = false;
    transparentInstructionFileBlocked = false;
}
void MAA::emitTransparentMacroSummary(uint64_t generation,
                                      Tick producerRegistrationTick) {
    using Stage = HybridMacroEventTracker::Stage;
    using Blocker = TransparentSPDController::Blocker;
    const auto &record = transparentMacroTracker.result();
    const auto &fill = record.stages[static_cast<size_t>(Stage::PageFill)];
    const auto &alu = record.stages[static_cast<size_t>(Stage::ALU)];
    const auto &store =
        record.stages[static_cast<size_t>(Stage::StreamStore)];
    const auto &ready = transparentMacroAllReadyRecord;
    const auto &ready_fill =
        ready.stages[static_cast<size_t>(Stage::PageFill)];
    const auto &ready_alu = ready.stages[static_cast<size_t>(Stage::ALU)];
    const auto &ready_store =
        ready.stages[static_cast<size_t>(Stage::StreamStore)];
    const Tick producer_consumer_overlap = transparentMacroAllReadySampled
        ? (ready.endTick - ready.startTick) - ready.exposedIdleTicks
        : 0;
    const Tick post_ready_idle = transparentMacroAllReadySampled
        ? record.exposedIdleTicks - ready.exposedIdleTicks
        : 0;
    const Tick post_ready_fill = transparentMacroAllReadySampled
        ? fill.activeTicks - ready_fill.activeTicks
        : 0;
    const Tick post_ready_alu = transparentMacroAllReadySampled
        ? alu.activeTicks - ready_alu.activeTicks
        : 0;
    const Tick post_ready_store = transparentMacroAllReadySampled
        ? store.activeTicks - ready_store.activeTicks
        : 0;
    DPRINTF(MAAMacroEvent,
            "event=hybrid_consumer_macro schema=1 generation=%lu "
            "producer_registration_tick=%lu submit_tick=%lu "
            "all_pages_ready_tick=%lu all_ready_before_submit=%d "
            "retire_tick=%lu fill_first_issue_tick=%lu "
            "fill_last_issue_tick=%lu fill_last_complete_tick=%lu "
            "fill_active_ticks=%lu fill_issues=%lu fill_completions=%lu "
            "fill_lines=%lu fill_bytes=%lu fill_retries=%lu "
            "fill_queue_high_water=%lu alu_first_issue_tick=%lu "
            "alu_last_issue_tick=%lu alu_last_complete_tick=%lu "
            "alu_active_ticks=%lu alu_issues=%lu alu_completions=%lu "
            "alu_retries=%lu alu_queue_high_water=%lu "
            "store_first_issue_tick=%lu store_last_issue_tick=%lu "
            "store_last_complete_tick=%lu store_active_ticks=%lu "
            "store_issues=%lu store_completions=%lu store_lines=%lu "
            "store_bytes=%lu store_retries=%lu "
            "store_queue_high_water=%lu action_overlap_ticks=%lu "
            "consumer_exposed_idle_ticks=%lu "
            "active_stage_high_water=%lu "
            "producer_consumer_overlap_ticks=%lu "
            "post_ready_fill_ticks=%lu post_ready_alu_ticks=%lu "
            "post_ready_store_ticks=%lu post_ready_exposed_idle_ticks=%lu "
            "blocker_producer_not_ready_ticks=%lu "
            "blocker_stream_busy_ticks=%lu blocker_alu_busy_ticks=%lu "
            "blocker_if_full_ticks=%lu\n",
            generation, producerRegistrationTick, record.startTick,
            transparentMacroAllReadyTick,
            transparentMacroAllReadyBeforeSubmit, record.endTick,
            fill.firstIssueTick, fill.lastIssueTick, fill.lastCompleteTick,
            fill.activeTicks, fill.issues, fill.completions, fill.lines,
            fill.bytes, fill.retries, fill.queueHighWater,
            alu.firstIssueTick, alu.lastIssueTick, alu.lastCompleteTick,
            alu.activeTicks, alu.issues, alu.completions, alu.retries,
            alu.queueHighWater, store.firstIssueTick, store.lastIssueTick,
            store.lastCompleteTick, store.activeTicks, store.issues,
            store.completions, store.lines, store.bytes, store.retries,
            store.queueHighWater, record.overlapTicks,
            record.exposedIdleTicks, record.activeStageHighWater,
            producer_consumer_overlap, post_ready_fill, post_ready_alu,
            post_ready_store, post_ready_idle,
            transparentBlockerTicks[
                static_cast<size_t>(Blocker::ProducerNotReady)],
            transparentBlockerTicks[static_cast<size_t>(Blocker::StreamBusy)],
            transparentBlockerTicks[static_cast<size_t>(Blocker::ALUBusy)],
            transparentBlockerTicks[
                static_cast<size_t>(Blocker::InstructionFileFull)]);
}
void MAA::recordTransparentConsumerAcceptance(
    int page, uint64_t transaction, Addr address, int ordinal, int expected) {
    panic_if(!transparentController.active() || ordinal <= 0 ||
                 expected <= 0 || ordinal > expected,
             "Invalid transparent consumer acceptance page=%d "
             "ordinal=%d/%d\n",
             page, ordinal, expected);
    DPRINTF(MAAVirtualTrace,
            "event=transparent_consumer_accept schema=2 occurrence=%lu "
            "generation=%lu page=%d transaction=%lu address=0x%lx "
            "ordinal=%d expected=%d first=%d last=%d\n",
            transparentTraceOccurrence++,
            transparentController.descriptor().generation, page,
            transaction, address, ordinal, expected, ordinal == 1,
            ordinal == expected);
}
void MAA::recordTransparentStreamTraffic(
    TransparentSPDController::Action action, uint64_t lines,
    uint64_t bytes) {
    panic_if(action != TransparentSPDController::Action::Fill &&
                 action != TransparentSPDController::Action::Store,
             "Cannot record stream traffic for transparent action %d\n",
             static_cast<int>(action));
    panic_if(!transparentMacroTracker.traffic(
                 transparentMacroStage(action), lines, bytes),
             "Failed to record transparent %s traffic\n",
             transparentActionName(action));
}
bool MAA::dispatchTransparentMicroOp(
    const TransparentSPDController::Request &request) {
    const auto &descriptor = transparentController.descriptor();
    Instruction instruction;
    instruction.core_id = descriptor.coreID;
    instruction.maa_id = descriptor.maaID;
    instruction.CID = descriptor.contextID;
    instruction.PC = descriptor.pc;
    instruction.datatype =
        static_cast<Instruction::DataType>(descriptor.dataType);
    instruction.controllerManaged = true;
    instruction.controllerAction = request.action;
    instruction.controllerPage = request.page;
    instruction.controllerTransactionID = request.transactionID;
    instruction.controllerSrcSlot = request.srcSlot;
    instruction.controllerDstSlot = request.dstSlot;
    instruction.controllerElementOffset = request.elementOffset;
    instruction.controllerElements = request.elements;

    const Addr byte_offset =
        static_cast<Addr>(request.logicalOffset) * descriptor.wordSize;
    switch (request.action) {
      case TransparentSPDController::Action::Fill:
        instruction.opcode = Instruction::OpcodeType::STREAM_LD;
        instruction.accessType = Instruction::AccessType::READ;
        instruction.dst1SpdID = descriptor.physicalTile;
        instruction.src1RegID = descriptor.minReg;
        instruction.src2RegID = descriptor.maxReg;
        instruction.src3RegID = descriptor.strideReg;
        instruction.baseAddr = descriptor.backingAddr + byte_offset;
        instruction.minAddr = descriptor.backingMinAddr;
        instruction.maxAddr = descriptor.backingMaxAddr;
        instruction.addrRangeID = descriptor.backingRangeID;
        break;
      case TransparentSPDController::Action::Compute:
        instruction.opcode = Instruction::OpcodeType::ALU_SCALAR;
        instruction.optype =
            static_cast<Instruction::OPType>(descriptor.operation);
        instruction.accessType = Instruction::AccessType::COMPUTE;
        instruction.src1SpdID = descriptor.physicalTile;
        instruction.src1RegID = descriptor.scaleReg;
        instruction.dst1SpdID = descriptor.outputTile;
        break;
      case TransparentSPDController::Action::Store:
        instruction.opcode = Instruction::OpcodeType::STREAM_ST;
        instruction.accessType = Instruction::AccessType::WRITE;
        instruction.src1SpdID = descriptor.outputTile;
        instruction.src1RegID = descriptor.minReg;
        instruction.src2RegID = descriptor.maxReg;
        instruction.src3RegID = descriptor.strideReg;
        instruction.baseAddr = descriptor.destinationAddr + byte_offset;
        instruction.minAddr = descriptor.destinationMinAddr;
        instruction.maxAddr = descriptor.destinationMaxAddr;
        instruction.addrRangeID = descriptor.destinationRangeID;
        break;
      default:
        panic("Cannot dispatch empty transparent-controller action\n");
    }

    // The finite controller is the readiness authority for its subspans.
    // Whole-tile SPD status cannot distinguish the two 2K owners.
    instruction.src1Status = Instruction::TileStatus::Finished;
    instruction.src2Status = Instruction::TileStatus::Finished;
    instruction.condStatus = Instruction::TileStatus::Finished;
    instruction.dst1Status = Instruction::TileStatus::WaitForService;
    instruction.dst2Status = Instruction::TileStatus::WaitForService;
    if (!ifile->pushInstruction(instruction))
        return false;

    if (instruction.dst1SpdID != -1) {
        spd->setTileIdle(instruction.dst1SpdID,
                         instruction.getWordSize(instruction.dst1SpdID));
        spd->setTileNotReady(
            instruction.dst1SpdID,
            instruction.getWordSize(instruction.dst1SpdID));
    }
    if (instruction.dst2SpdID != -1) {
        spd->setTileIdle(instruction.dst2SpdID,
                         instruction.getWordSize(instruction.dst2SpdID));
        spd->setTileNotReady(
            instruction.dst2SpdID,
            instruction.getWordSize(instruction.dst2SpdID));
    }
    if (instruction.src1SpdID != -1) {
        spd->setTileNotReady(
            instruction.src1SpdID,
            instruction.getWordSize(instruction.src1SpdID));
    }
    if (instruction.src2SpdID != -1) {
        spd->setTileNotReady(
            instruction.src2SpdID,
            instruction.getWordSize(instruction.src2SpdID));
    }
    scheduleIssueInstructionEvent(1);
    return true;
}
void MAA::tryIssueTransparentMicroOp() {
    updateTransparentBlockerTracking();
    if (transparentController.controllerCyclesRemaining() != 0) {
        if (curTick() < transparentControllerLookupReadyTick) {
            if (!issueInstructionEvent.scheduled()) {
                schedule(issueInstructionEvent,
                         transparentControllerLookupReadyTick);
            } else if (transparentControllerLookupReadyTick <
                       issueInstructionEvent.when()) {
                reschedule(issueInstructionEvent,
                           transparentControllerLookupReadyTick);
            }
            return;
        }
        transparentController.advanceControllerCycle();
        updateTransparentBlockerTracking();
    }
    auto try_request = [this](
                           const TransparentSPDController::Request &request) {
        if (request.action == TransparentSPDController::Action::None)
            return;
        if (!dispatchTransparentMicroOp(request)) {
            panic_if(!transparentMacroTracker.retry(
                         transparentMacroStage(request.action), curTick()),
                     "Failed to record transparent action retry\n");
            updateTransparentBlockerTracking();
            transparentInstructionFileBlocked = true;
            updateTransparentBlockerTracking();
            DPRINTF(MAAVirtualTrace,
                    "event=transparent_backpressure schema=2 occurrence=%lu "
                    "page=%d action=%d "
                    "action_name=%s reason=instruction_file_full\n",
                    transparentTraceOccurrence++, request.page,
                    static_cast<int>(request.action),
                    transparentActionName(request.action));
            return;
        }
        updateTransparentBlockerTracking();
        transparentInstructionFileBlocked = false;
        panic_if(!transparentController.accept(request),
                 "Transparent controller rejected dispatched page %d "
                 "action %d\n",
                 request.page, static_cast<int>(request.action));
        panic_if(!transparentMacroTracker.issue(
                     transparentMacroStage(request.action), curTick()),
                 "Failed to record transparent action issue\n");
        updateTransparentBlockerTracking();
        DPRINTF(MAAVirtualTrace,
                "event=transparent_issue schema=2 occurrence=%lu "
                "generation=%lu page=%d "
                "action=%d action_name=%s offset=%d elements=%d "
                "dependency=controller_order_and_tile_ready\n",
                transparentTraceOccurrence++,
                transparentController.descriptor().generation,
                request.page, static_cast<int>(request.action),
                transparentActionName(request.action), request.logicalOffset,
                request.elements);
        DPRINTF(MAAVirtualTrace,
                "event=transparent_ping_issue page=%d action=%d offset=%d "
                "elements=%d element_offset=%d src_slot=%d dst_slot=%d "
                "transaction=%lu\n",
                request.page, static_cast<int>(request.action),
                request.logicalOffset, request.elements,
                request.elementOffset, request.srcSlot, request.dstSlot,
                request.transactionID);
    };
    // ALU and STREAM are distinct real units.  The controller admits at most
    // one request to each; STREAM still serializes fills and stores.
    try_request(transparentController.pendingALU());
    try_request(transparentController.pendingStream());
}

bool
MAA::submitLogicalSPDDescriptor(
    InstructionPtr instruction, PacketPtr completionPacket)
{
    using Bridge = LogicalSPDCacheGem5Bridge;
    using Slice = Bridge::Runtime::Slice;
    using Transport = Bridge::Runtime::Transport;
    constexpr std::size_t LogicalElements =
        Slice::Pages * Slice::PageElements;

    panic_if(!instruction->isLogicalALUScalar(),
             "Cannot submit a non-logical instruction to logical SPD\n");
    panic_if(instruction->maa_id < 0 ||
                 instruction->maa_id >= static_cast<int>(num_maas),
             "Logical SPD instruction has invalid MAA %d\n",
             instruction->maa_id);
    panic_if(num_maas != 1,
             "Logical SPD live adapter is validated only for one MAA\n");
    panic_if(num_cores != LogicalSPDCacheLiveAdapterState::PortCount ||
                 cacheSidePorts.size() !=
                     LogicalSPDCacheLiveAdapterState::PortCount ||
                 system->cacheLineSize() != Transport::LineBytes,
             "Logical SPD live adapter requires exactly four cores/cache "
             "ports and 64-byte lines, got %u/%zu/%lu\n",
             num_cores, cacheSidePorts.size(),
             static_cast<unsigned long>(system->cacheLineSize()));
    LogicalSPDExecution &execution =
        logicalSpdExecutions[instruction->maa_id];
    if (execution.active)
        return false;
    const Bridge::Runtime &authority =
        logicalSpdBridge->runtime(instruction->maa_id);
    panic_if(num_tile_elements != LogicalElements ||
                 physical_tile_elements != Slice::SerialPageElements,
             "Logical SPD live slice requires 16K logical and 4K visible "
             "FP64 elements, got %u/%u\n", num_tile_elements,
             physical_tile_elements);
    panic_if(instruction->datatype != Instruction::DataType::FLOAT64_TYPE,
             "Logical SPD live slice supports FP64 only: %s\n",
             instruction->print());
    const Addr sourceBase = instruction->logicalSourceBackingAddr;
    const Addr destinationBase = instruction->backingAddr;
    panic_if(sourceBase % Slice::BackingBytes != 0 ||
                 destinationBase % Slice::BackingBytes != 0,
             "Logical SPD backing must be aligned to the 16K FP64 span "
             "(%zu bytes): source=0x%lx destination=0x%lx\n",
             Slice::BackingBytes, sourceBase, destinationBase);
    const bool backingOverlap =
        sourceBase <= destinationBase
            ? destinationBase - sourceBase < Slice::BackingBytes
            : sourceBase - destinationBase < Slice::BackingBytes;
    panic_if(backingOverlap && sourceBase != destinationBase,
             "Logical SPD source and destination backing spans partially "
             "overlap: "
             "source=0x%lx destination=0x%lx\n",
             sourceBase, destinationBase);

    Slice::Operation operation;
    switch (instruction->optype) {
      case Instruction::OPType::ADD_OP:
        operation = Slice::Operation::Add;
        break;
      case Instruction::OPType::SUB_OP:
        operation = Slice::Operation::Sub;
        break;
      case Instruction::OPType::MUL_OP:
        operation = Slice::Operation::Mul;
        break;
      case Instruction::OPType::DIV_OP:
        operation = Slice::Operation::Div;
        break;
      case Instruction::OPType::MIN_OP:
        operation = Slice::Operation::Min;
        break;
      case Instruction::OPType::MAX_OP:
        operation = Slice::Operation::Max;
        break;
      default:
        panic("Logical SPD live slice does not implement operation %d\n",
              static_cast<int>(instruction->optype));
    }

    const Bridge::CallbackClaim claim =
        logicalSpdBridge->claimCallback(instruction->maa_id);
    panic_if(claim.status != Bridge::LifecycleStatus::Accepted,
             "Logical SPD callback admission failed with status %d\n",
             static_cast<int>(claim.status));
    const Slice::BackingSpan source = {
        instruction->logicalSourceBackingAddr, Slice::BackingBytes};
    panic_if(logicalSpdBridge->registerSource(
                 claim.token, static_cast<uint8_t>(
                                  instruction->src1LogicalID), source) !=
                 Slice::Status::Accepted,
             "Logical SPD source registration failed after ABI validation\n");
    Slice::Admission admission;
    admission.sourceLogical =
        static_cast<uint8_t>(instruction->src1LogicalID);
    admission.destinationLogical =
        static_cast<uint8_t>(instruction->dst1LogicalID);
    admission.destination = {
        instruction->backingAddr, Slice::BackingBytes};
    admission.operation = operation;
    const double scalar = rf->getData<double>(instruction->src1RegID);
    std::memcpy(&admission.scalarBits, &scalar, sizeof(scalar));
    panic_if(logicalSpdBridge->admit(claim.token, admission) !=
                 Slice::Status::Accepted,
             "Logical SPD operation admission failed after source "
             "registration\n");

    execution.active = true;
    execution.token = claim.token;
    execution.completionPacket = completionPacket;
    execution.retryPacket = nullptr;
    execution.retryPort = 0;
    execution.liveOwner = claim.token.identity;
    execution.retryAuthority =
        LogicalSPDCacheLiveAdapterState::WaitAuthority::None;
    execution.coreID = instruction->core_id;
    execution.contextID = instruction->CID;
    execution.pc = instruction->PC;
    DPRINTF(MAAVirtualTrace,
            "event=logical_spd_admit maa=%d generation=%lu incarnation=%lu "
            "operation=%lu source=0x%lx destination=0x%lx elements=%lu "
            "mode=%u page_elements=%lu pages=%lu slots=%lu "
            "payload_bytes=%lu packed_metadata_bytes=%lu "
            "source_contract=pre_materialized_backing\n",
            instruction->maa_id, claim.token.generation,
            claim.token.runtimeIdentity, claim.token.identity,
            instruction->logicalSourceBackingAddr,
            instruction->backingAddr,
            static_cast<unsigned long>(LogicalElements),
            static_cast<unsigned>(authority.cacheMode()),
            static_cast<unsigned long>(authority.pageElements()),
            static_cast<unsigned long>(authority.pageCount()),
            static_cast<unsigned long>(authority.slotCount()),
            static_cast<unsigned long>(authority.payloadBytes()),
            static_cast<unsigned long>(
                Bridge::Runtime::PackedSemanticLedger::PackedBytes -
                Slice::PayloadBytes));
    scheduleLogicalSPDEvent();
    return true;
}

PacketPtr
MAA::makeLogicalSPDPacket(
    LogicalSPDExecution &execution,
    const LogicalSPDCacheGem5Bridge::Runtime::Transport::RequestPacket
        &logicalRequest)
{
    using Transport = LogicalSPDCacheGem5Bridge::Runtime::Transport;
    panic_if(logicalRequest.request == nullptr ||
                 logicalRequest.token == nullptr ||
                 logicalRequest.size != Transport::LineBytes,
             "Logical SPD transport produced an invalid request handle\n");
    const int region = getAddrRegion(logicalRequest.address);
    panic_if(region < 0,
             "Logical SPD request address 0x%lx left registered regions\n",
             logicalRequest.address);
    RequestPtr translationRequest = std::make_shared<Request>(
        logicalRequest.address, logicalRequest.size, Request::Flags(0),
        requestorId, execution.pc, execution.contextID);
    ImmediateLogicalSPDTranslation translation;
    ThreadContext *tc = system->threads[execution.contextID];
    const BaseMMU::Mode mode =
        logicalRequest.command == Transport::Command::ReadReq
            ? BaseMMU::Read
            : BaseMMU::Write;
    mmu->translateTiming(translationRequest, tc, &translation, mode);
    panic_if(translation.delayed || !translation.finished ||
                 translation.fault != NoFault,
             "Logical SPD live slice requires immediate valid address "
             "translation for 0x%lx\n", logicalRequest.address);

    RequestPtr realRequest = std::make_shared<Request>(
        translation.address, logicalRequest.size, Request::Flags(0),
        requestorId);
    realRequest->setRegion(region);
    const MemCmd command =
        logicalRequest.command == Transport::Command::ReadReq
            ? MemCmd::ReadReq
            : MemCmd::WriteReq;
    PacketPtr packet = new Packet(realRequest, command);
    packet->allocate();
    if (logicalRequest.command == Transport::Command::WriteReq) {
        panic_if(logicalRequest.data == nullptr ||
                     logicalRequest.dataSize != Transport::LineBytes,
                 "Logical SPD writeback lacks an exact 64-byte snapshot\n");
        packet->setData(
            reinterpret_cast<const uint8_t *>(logicalRequest.data));
    }
    auto *state = new LogicalSPDSenderState;
    state->token = execution.token;
    state->request = logicalRequest.request;
    state->route = logicalRequest.token;
    state->packetIncarnation = logicalRequest.incarnation;
    state->requestIncarnation = logicalRequest.request->incarnation;
    state->tokenDepth = logicalRequest.tokenDepth;
    state->tokenRecord = logicalRequest.token->record;
    state->tokenEpoch = logicalRequest.token->epoch;
    state->tokenActionID = logicalRequest.token->actionID;
    state->callbackPort = logicalRequest.callbackPort;
    state->logicalAddress = logicalRequest.address;
    state->size = logicalRequest.size;
    state->command = logicalRequest.command;
    packet->pushSenderState(state);
    return packet;
}

bool
MAA::recvLogicalSPDTimingResp(PacketPtr pkt, uint8_t respondingPort)
{
    using Transport = LogicalSPDCacheGem5Bridge::Runtime::Transport;
    auto *peek = dynamic_cast<LogicalSPDSenderState *>(pkt->senderState);
    if (peek == nullptr)
        return false;
    auto *state = dynamic_cast<LogicalSPDSenderState *>(
        pkt->popSenderState());
    panic_if(state == nullptr, "Logical SPD response lost sender state\n");
    panic_if(!LogicalSPDCachePortProvenance::responseMatches(
                 state->callbackPort, respondingPort),
             "Logical SPD response arrived on port %u, expected port %u\n",
             respondingPort, state->callbackPort);
    Transport::ReturnedHandle returned;
    returned.incarnation = state->packetIncarnation;
    returned.request = state->request;
    returned.requestIncarnation = state->requestIncarnation;
    returned.token = state->route;
    returned.tokenDepth = state->tokenDepth;
    returned.tokenRecord = state->tokenRecord;
    returned.tokenEpoch = state->tokenEpoch;
    returned.tokenActionID = state->tokenActionID;
    returned.address = state->logicalAddress;
    returned.size = state->size;
    if (state->command == Transport::Command::ReadReq) {
        panic_if(pkt->cmd != MemCmd::ReadResp &&
                     pkt->cmd != MemCmd::ReadExResp,
                 "Logical SPD read received %s\n", pkt->cmdString());
        returned.command = pkt->cmd == MemCmd::ReadExResp
                               ? Transport::Command::ReadRespWithInvalidate
                               : Transport::Command::ReadResp;
        returned.data = reinterpret_cast<const std::byte *>(
            pkt->getConstPtr<uint8_t>());
        returned.dataSize = pkt->getSize();
    } else {
        panic_if(pkt->cmd != MemCmd::WriteResp,
                 "Logical SPD writeback received %s\n", pkt->cmdString());
        returned.command = Transport::Command::WriteResp;
    }
    const Transport::Result result = logicalSpdBridge->receive(
        state->token, returned, respondingPort);
    panic_if(!returned.disposed ||
                 (result.status != Transport::Status::Accepted &&
                  result.status != Transport::Status::Completed),
             "Logical SPD rejected timed response with status %d\n",
             static_cast<int>(result.status));
    delete state;
    return true;
}

void
MAA::serviceLogicalSPD()
{
    using Bridge = LogicalSPDCacheGem5Bridge;
    using Slice = Bridge::Runtime::Slice;
    using Transport = Bridge::Runtime::Transport;
    for (LogicalSPDExecution &execution : logicalSpdExecutions) {
        if (!execution.active)
            continue;
        for (unsigned attempt = 0; attempt <
             Transport::ResponseCredits + 2; ++attempt) {
            if (logicalSpdBridge->operationComplete(execution.token)) {
                const Bridge::CallbackToken completed = execution.token;
                panic_if(logicalSpdBridge->completeOperation(completed) !=
                             Bridge::LifecycleStatus::Accepted,
                         "Logical SPD failed final retire/reset\n");
                PacketPtr completion = execution.completionPacket;
                const int core = execution.coreID;
                logicalSpdLiveBoundary.release(execution.liveOwner);
                execution = LogicalSPDExecution{};
                completion->makeTimingResponse();
                completion->headerDelay = completion->payloadDelay = 0;
                cpuSidePorts[core]->schedTimingResp(
                    completion, getClockEdge(Cycles(1)));
                DPRINTF(MAAVirtualTrace,
                        "event=logical_spd_complete maa=%lu generation=%lu "
                        "incarnation=%lu operation=%lu\n",
                        completed.maaId, completed.generation,
                        completed.runtimeIdentity, completed.identity);
                break;
            }
            if (execution.retryPacket != nullptr) {
                if (!logicalSpdLiveBoundary.consume(
                        execution.liveOwner, execution.retryPort,
                        execution.retryAuthority))
                    break;
                const Transport::Status resumed =
                    execution.retryAuthority ==
                            LogicalSPDCacheLiveAdapterState::WaitAuthority::
                                DownstreamRequestRetry
                        ? logicalSpdBridge->recvReqRetry(
                              execution.token, execution.retryPort)
                        : logicalSpdBridge->resumeLocalCapacity(
                              execution.token, execution.retryPort);
                panic_if(resumed !=
                             Transport::Status::Accepted,
                         "Logical SPD refused-send resume was rejected\n");
                const int routedPort =
                    core_addr(execution.retryPacket->getAddr());
                panic_if(routedPort != execution.retryPort,
                         "Logical SPD retry rerouted from port %u to %d\n",
                         execution.retryPort, routedPort);
                uint8_t actualPort = 0;
                LogicalSPDCacheLiveAdapterState::WaitAuthority refusal =
                    LogicalSPDCacheLiveAdapterState::WaitAuthority::None;
                const bool accepted = sendPacketCache(
                    execution.retryPacket, &actualPort, &refusal);
                panic_if(actualPort != execution.retryPort,
                         "Logical SPD retry used port %u, expected %u\n",
                         actualPort, execution.retryPort);
                const Transport::Result sent =
                    logicalSpdBridge->sendPrepared(
                        execution.token, accepted);
                panic_if(sent.status !=
                             (accepted ? Transport::Status::SendAccepted
                                       : Transport::Status::SendRefused),
                         "Logical SPD retry transition failed with %d\n",
                         static_cast<int>(sent.status));
                if (accepted) {
                    execution.retryPacket = nullptr;
                    logicalSpdLiveBoundary.release(execution.liveOwner);
                    execution.retryAuthority =
                        LogicalSPDCacheLiveAdapterState::WaitAuthority::None;
                } else {
                    panic_if(
                        refusal == LogicalSPDCacheLiveAdapterState::
                                       WaitAuthority::None ||
                            !logicalSpdLiveBoundary.arm(
                                execution.liveOwner, actualPort, refusal),
                        "Logical SPD could not re-arm exact port owner\n");
                    execution.retryAuthority = refusal;
                }
                break;
            }
            const Transport::Result prepared =
                logicalSpdBridge->prepare(execution.token);
            if (prepared.status == Transport::Status::Accepted) {
                panic_if(prepared.handle == nullptr,
                         "Logical SPD accepted a null request\n");
                PacketPtr packet =
                    makeLogicalSPDPacket(execution, *prepared.handle);
                const int routedPort = core_addr(packet->getAddr());
                panic_if(routedPort < 0 ||
                             routedPort >= static_cast<int>(
                                 LogicalSPDCacheLiveAdapterState::PortCount) ||
                             routedPort != prepared.handle->callbackPort,
                         "Logical SPD physical port %d does not match "
                         "transport port %u\n",
                         routedPort, prepared.handle->callbackPort);
                uint8_t actualPort = 0;
                LogicalSPDCacheLiveAdapterState::WaitAuthority refusal =
                    LogicalSPDCacheLiveAdapterState::WaitAuthority::None;
                const bool accepted =
                    sendPacketCache(packet, &actualPort, &refusal);
                const Transport::Result sent =
                    logicalSpdBridge->sendPrepared(
                        execution.token, accepted);
                panic_if(sent.status !=
                             (accepted ? Transport::Status::SendAccepted
                                       : Transport::Status::SendRefused),
                         "Logical SPD send transition failed with %d\n",
                         static_cast<int>(sent.status));
                if (!accepted) {
                    execution.retryPacket = packet;
                    execution.retryPort = actualPort;
                    execution.retryAuthority = refusal;
                    panic_if(
                        refusal == LogicalSPDCacheLiveAdapterState::
                                       WaitAuthority::None ||
                            !logicalSpdLiveBoundary.arm(
                                execution.liveOwner, actualPort, refusal),
                        "Logical SPD could not claim exact refused port\n");
                    break;
                }
                continue;
            }
            if (prepared.status == Transport::Status::NoCreditAvailable)
                break;
            if (prepared.status == Transport::Status::NoWork) {
                const Slice::Status compute =
                    logicalSpdBridge->driveCompute(execution.token);
                if (compute == Slice::Status::Accepted)
                    continue;
                // NoWork also covers a page whose finite records have all
                // issued while responses remain in flight.  Its live page
                // correlation makes compute Busy until the final response.
                panic_if(compute != Slice::Status::NotReady &&
                             compute != Slice::Status::Busy,
                         "Logical SPD compute transition failed with %d\n",
                         static_cast<int>(compute));
                break;
            }
            panic("Logical SPD prepare failed with status %d\n",
                  static_cast<int>(prepared.status));
        }
    }
}

void
MAA::scheduleLogicalSPDEvent(int latency)
{
    const Tick when = getClockEdge(Cycles(latency));
    if (!logicalSpdEvent.scheduled())
        schedule(logicalSpdEvent, when);
    else if (when < logicalSpdEvent.when())
        reschedule(logicalSpdEvent, when);
}

void
MAA::notifyLogicalSPDPortEvent(
    uint8_t actualPort, LogicalSPDCacheLiveAdapterState::PortEvent event)
{
    const LogicalSPDCacheLiveAdapterState::Notification notification =
        logicalSpdLiveBoundary.notify(actualPort, event);
    if (!notification.granted)
        return;
    for (LogicalSPDExecution &execution : logicalSpdExecutions) {
        if (execution.active && execution.retryPacket != nullptr &&
            execution.liveOwner == notification.owner &&
            execution.retryPort == actualPort) {
            panic_if(logicalSpdLiveBoundary.pendingAuthority(actualPort) !=
                         execution.retryAuthority,
                     "Logical SPD port %u owner authority diverged\n",
                     actualPort);
            scheduleLogicalSPDEvent();
            return;
        }
    }
    panic("Logical SPD port %u granted missing owner %lu\n", actualPort,
          notification.owner);
}

void
MAA::notifyLogicalSPDResponse()
{
    scheduleLogicalSPDEvent();
}

DrainState
MAA::drain()
{
    logicalSpdBridge->closeAdmission();
    panic_if(directRetirementExecution.active,
             "Direct-retirement checkpoint/drain requested with live line "
             "credits; serialization is unsupported\n");
    panic_if(!logicalSpdBridge->allQuiescent(),
             "Logical SPD checkpoint/drain requested with live state; "
             "serialization is unsupported\n");
    return DrainState::Drained;
}

void
MAA::drainResume()
{
    panic_if(directRetirementExecution.active,
             "Direct-retirement drain resumed with live line credits\n");
    panic_if(!logicalSpdBridge->allQuiescent(),
             "Logical SPD drain resumed with non-quiescent live state\n");
    logicalSpdBridge->reopenAdmission();
}

void MAA::dispatchRegister() {
    DPRINTF(MAAController, "%s: dispatching register...!\n", __func__);
    assert(my_register_pkts.size() == my_registers.size());
    auto pkt_it = my_register_pkts.begin();
    auto register_it = my_registers.begin();
    while (pkt_it != my_register_pkts.end() && register_it != my_registers.end()) {
        RegisterPtr reg = *register_it;
        PacketPtr pkt = *pkt_it;
        const int register_words = reg->size / sizeof(uint32_t);
        if (ifile->canPushRegister(*reg) &&
            !transparentController.usesRegister(
                reg->maa_id, reg->register_id, register_words)) {
            DPRINTF(MAAController,
                    "%s: register %d write dispatched!\n", __func__,
                    reg->register_id);
            if (reg->size == 4) {
                rf->setData<uint32_t>(reg->register_id, reg->data_UINT32);
            } else {
                panic_if(reg->size != 8, "Invalid size for RF data: %d\n", reg->size);
                rf->setData<uint64_t>(reg->register_id, reg->data_UINT64);
            }
            pkt->makeTimingResponse();
            pkt->headerDelay = pkt->payloadDelay = 0;
            cpuSidePorts[0]->schedTimingResp(pkt, getClockEdge(Cycles(1)));
            pkt_it = my_register_pkts.erase(pkt_it);
            register_it = my_registers.erase(register_it);
            delete reg;
        } else {
            DPRINTF(MAAController, "%s: Reg write %d failed to dipatch!\n", __func__, reg->register_id);
            pkt_it++;
            register_it++;
        }
    }
}
void MAA::dispatchInstruction() {
    DPRINTF(MAAController, "%s: dispatching instruction...!\n", __func__);
    assert(my_instruction_pkts.size() == my_instructions.size());
    assert(my_instruction_recvs.size() == my_instructions.size());
    assert(my_instruction_RIDs.size() == my_instructions.size());
    auto pkt_it = my_instruction_pkts.begin();
    auto recv_it = my_instruction_recvs.begin();
    auto rid_it = my_instruction_RIDs.begin();
    auto instruction_it = my_instructions.begin();
    while (pkt_it != my_instruction_pkts.end() && instruction_it != my_instructions.end() &&
           recv_it != my_instruction_recvs.end() && rid_it != my_instruction_RIDs.end()) {
        if (*recv_it == true) {
            InstructionPtr instruction = *instruction_it;
            PacketPtr pkt = *pkt_it;
            if (instruction->isLogicalALUScalar()) {
                if (!submitLogicalSPDDescriptor(instruction, pkt)) {
                    ++pkt_it;
                    ++recv_it;
                    ++rid_it;
                    ++instruction_it;
                    continue;
                }
                DPRINTF(MAAController,
                        "%s: logical SPD descriptor %s dispatched\n",
                        __func__, instruction->print());
                pkt_it = my_instruction_pkts.erase(pkt_it);
                recv_it = my_instruction_recvs.erase(recv_it);
                rid_it = my_instruction_RIDs.erase(rid_it);
                instruction_it = my_instructions.erase(instruction_it);
                delete instruction;
                continue;
            }
            if (instruction->opcode ==
                Instruction::OpcodeType::VIRTUAL_TILE_ALU_SCALAR) {
                const bool direct = transparent_spd_mode == 3;
                const bool submitted = direct
                    ? submitDirectRetirementDescriptor(instruction)
                    : submitTransparentDescriptor(instruction);
                if (!submitted) {
                    ++pkt_it;
                    ++recv_it;
                    ++rid_it;
                    ++instruction_it;
                    continue;
                }
                const bool direct_active =
                    direct && directRetirementExecution.active;
                DPRINTF(MAAController,
                        "%s: %s descriptor %s dispatched\n", __func__,
                        direct_active ? "direct-retirement" : "transparent",
                        instruction->print());
                // Completion is carried by an SPD readiness token for both
                // paths; descriptor submission itself remains asynchronous.
                pkt->makeTimingResponse();
                pkt->headerDelay = pkt->payloadDelay = 0;
                cpuSidePorts[0]->schedTimingResp(
                    pkt, getClockEdge(Cycles(1)));
                pkt_it = my_instruction_pkts.erase(pkt_it);
                recv_it = my_instruction_recvs.erase(recv_it);
                rid_it = my_instruction_RIDs.erase(rid_it);
                instruction_it = my_instructions.erase(instruction_it);
                delete instruction;
                continue;
            }
            instruction->src1Status =
                (Instruction::TileStatus)getTileStatus(
                    instruction, instruction->src1SpdID, false);
            instruction->src2Status =
                (Instruction::TileStatus)getTileStatus(
                    instruction, instruction->src2SpdID, false);
            instruction->condStatus =
                (Instruction::TileStatus)getTileStatus(
                    instruction, instruction->condSpdID, false);
            // Assume every tile is readable, so invalidate all destinations.
            // DST1 users include stream/indirect loads, range loops, and ALUs.
            instruction->dst1Status =
                (Instruction::TileStatus)getTileStatus(
                    instruction, instruction->dst1SpdID, true);
            // Instructions with DST2: range loop
            instruction->dst2Status =
                (Instruction::TileStatus)getTileStatus(
                    instruction, instruction->dst2SpdID, true);
            if (ifile->pushInstruction(*instruction)) {
                DPRINTF(MAAController, "%s: %s dispatched!\n", __func__, instruction->print());
                if (instruction->dst1SpdID != -1) {
                    assert(instruction->dst1SpdID != instruction->src1SpdID);
                    assert(instruction->dst1SpdID != instruction->src2SpdID);
                    spd->setTileIdle(instruction->dst1SpdID, instruction->getWordSize(instruction->dst1SpdID));
                    spd->setTileNotReady(instruction->dst1SpdID, instruction->getWordSize(instruction->dst1SpdID));
                }
                if (instruction->opcode ==
                        Instruction::OpcodeType::INDIR_LD_VIRTUAL ||
                    instruction->opcode ==
                        Instruction::OpcodeType::INDIR_LD_VIRTUAL_INDEX) {
                    resetVirtualPageReady(
                        instruction->dst1SpdID, instruction->backingAddr,
                        instruction->WordSize());
                }
                if (instruction->dst2SpdID != -1) {
                    assert(instruction->dst2SpdID != instruction->src1SpdID);
                    assert(instruction->dst2SpdID != instruction->src2SpdID);
                    spd->setTileIdle(instruction->dst2SpdID, instruction->getWordSize(instruction->dst2SpdID));
                    spd->setTileNotReady(instruction->dst2SpdID, instruction->getWordSize(instruction->dst2SpdID));
                }
                if (instruction->src1SpdID != -1) {
                    spd->setTileNotReady(instruction->src1SpdID, instruction->getWordSize(instruction->src1SpdID));
                }
                if (instruction->src2SpdID != -1) {
                    spd->setTileNotReady(instruction->src2SpdID, instruction->getWordSize(instruction->src2SpdID));
                }
                if (instruction->opcode ==
                    Instruction::OpcodeType::INDIR_LD_SPD_STREAM) {
                    panic_if(instruction->dst1SpdID == -1,
                             "Fused load-stream requires a destination "
                             "tile\n");
                    spd->setTileNotReady(
                        instruction->dst1SpdID,
                        instruction->getWordSize(instruction->dst1SpdID));
                }
                if (instruction->opcode ==
                        Instruction::OpcodeType::INDIR_LD_VIRTUAL_INDEX &&
                    instruction->dst2SpdID != -1) {
                    // The fused prefetch token is produced by the stream
                    // micro-op and consumed by the indirect micro-op.
                    spd->setTileNotReady(instruction->dst2SpdID, 4);
                }
                pkt->makeTimingResponse();
                pkt->headerDelay = pkt->payloadDelay = 0;
                cpuSidePorts[0]->schedTimingResp(pkt, getClockEdge(Cycles(1)));
                scheduleIssueInstructionEvent(1);
                pkt_it = my_instruction_pkts.erase(pkt_it);
                recv_it = my_instruction_recvs.erase(recv_it);
                rid_it = my_instruction_RIDs.erase(rid_it);
                instruction_it = my_instructions.erase(instruction_it);
                delete instruction;
            } else {
                DPRINTF(MAAController, "%s: %s failed to dipatch!\n", __func__, instruction->print());
                pkt_it++;
                recv_it++;
                rid_it++;
                instruction_it++;
            }
        } else {
            pkt_it++;
            recv_it++;
            rid_it++;
            instruction_it++;
        }
    }
}
void MAA::finishInstructionCompute(Instruction *instruction) {
    DPRINTF(MAAController, "%s: %s finishing!\n", __func__, instruction->print());
    const bool controller_managed = instruction->controllerManaged;
    const auto controller_action = instruction->controllerAction;
    const int controller_page = instruction->controllerPage;
    TransparentSPDController::Request controller_request;
    controller_request.action = controller_action;
    controller_request.page = controller_page;
    controller_request.elements = instruction->controllerElements;
    controller_request.logicalOffset =
        controller_page * instruction->controllerElements;
    controller_request.srcSlot = instruction->controllerSrcSlot;
    controller_request.dstSlot = instruction->controllerDstSlot;
    controller_request.elementOffset = instruction->controllerElementOffset;
    controller_request.transactionID = instruction->controllerTransactionID;
    if (instruction->dst1SpdID != -1) {
        spd->setTileFinished(instruction->dst1SpdID, instruction->getWordSize(instruction->dst1SpdID));
        setTileReady(instruction->dst1SpdID, instruction->getWordSize(instruction->dst1SpdID));
    }
    if (instruction->dst2SpdID != -1) {
        spd->setTileFinished(instruction->dst2SpdID, instruction->getWordSize(instruction->dst2SpdID));
        setTileReady(instruction->dst2SpdID, instruction->getWordSize(instruction->dst2SpdID));
    }
    if (instruction->src1SpdID != -1) {
        setTileReady(instruction->src1SpdID, instruction->getWordSize(instruction->src1SpdID));
    }
    if (instruction->src2SpdID != -1) {
        setTileReady(instruction->src2SpdID, instruction->getWordSize(instruction->src2SpdID));
    }
    ifile->finishInstructionCompute(instruction);
    if (num_maas > 1)
        invalidator->finishInstruction(instruction);
    switch (instruction->funcUniType) {
    case FuncUnitType::STREAM: {
        streamAccessIdle[instruction->func_unit_id] = true;
        break;
    }
    case FuncUnitType::INDIRECT: {
        indirectAccessIdle[instruction->func_unit_id] = true;
        break;
    }
    case FuncUnitType::ALU: {
        aluUnitsIdle[instruction->func_unit_id] = true;
        break;
    }
    case FuncUnitType::RANGE: {
        rangeUnitsIdle[instruction->func_unit_id] = true;
        break;
    }
    default: {
        assert(false);
    }
    }
    if (controller_managed) {
        updateTransparentBlockerTracking();
        panic_if(!transparentController.complete(controller_request),
                 "Transparent controller rejected completion of page %d "
                 "action %d\n",
                 controller_page, static_cast<int>(controller_action));
        panic_if(!transparentMacroTracker.complete(
                     transparentMacroStage(controller_action), curTick()),
                 "Failed to record transparent action completion\n");
        updateTransparentBlockerTracking();
        DPRINTF(MAAVirtualTrace,
                "event=transparent_complete schema=2 occurrence=%lu "
                "generation=%lu page=%d "
                "action=%d action_name=%s\n",
                transparentTraceOccurrence++,
                transparentController.descriptor().generation,
                controller_page, static_cast<int>(controller_action),
                transparentActionName(controller_action));
        DPRINTF(MAAVirtualTrace,
                "event=transparent_ping_complete page=%d action=%d "
                "element_offset=%d transaction=%lu\n",
                controller_page, static_cast<int>(controller_action),
                controller_request.elementOffset,
                controller_request.transactionID);
        if (transparentController.complete()) {
            const auto descriptor = transparentController.descriptor();
            // Return the descriptor-lifetime credits only after the final
            // native stream store has been accepted by the memory hierarchy.
            setTileReady(descriptor.physicalTile, descriptor.wordSize);
            setTileReady(descriptor.outputTile, descriptor.wordSize);
            panic_if(virtualPageGeneration[descriptor.tokenTile] !=
                         descriptor.generation,
                     "Transparent token %d generation changed while active\n",
                     descriptor.tokenTile);
            virtualPageConsumedGeneration[descriptor.tokenTile] =
                descriptor.generation;
            finishTransparentBlockerTracking(descriptor.generation);
            panic_if(!transparentMacroTracker.finish(curTick()),
                     "Transparent macro tracker did not finish cleanly\n");
            emitTransparentMacroSummary(
                descriptor.generation,
                virtualProducerRegistrationTick[descriptor.tokenTile]);
            panic_if(!transparentController.retire(),
                     "Completed transparent descriptor did not retire\n");
            transparentControllerLookupReadyTick = 0;
            DPRINTF(MAAVirtualTrace,
                    "event=transparent_retire schema=2 occurrence=%lu "
                    "generation=%lu "
                    "pages=%d\n",
                    transparentTraceOccurrence++, descriptor.generation,
                    TransparentSPDController::NumPages);
            DPRINTF(MAAVirtualTrace,
                    "event=transparent_ping_retire pages=%d chunks=%d "
                    "mode=%u\n",
                    TransparentSPDController::NumPages,
                    TransparentSPDController::LogicalElements /
                        controller_request.elements,
                    transparent_spd_mode);
        }
    } else if (transparentController.active()) {
        // The scheduled issue event below is the finite retry opportunity for
        // controller work that previously observed a full instruction file.
    }
    // Do not dispatch a successor controller micro-op synchronously here.  A
    // functional unit still owns a pointer to this IF slot until its finish
    // handler returns, so immediate reuse would mutate the retiring opcode.
    scheduleIssueInstructionEvent();
    scheduleDispatchInstructionEvent();
    scheduleDispatchRegisterEvent();
    if (allFuncUnitsIdle()) {
        my_last_idle_tick = curTick();
    }
}
void MAA::setTileReady(int tileID, int wordSize) {
    DPRINTF(MAAController, "%s: tile[%d] is ready!\n", __func__, tileID);
    spd->setTileReady(tileID, wordSize);
    assert(my_ready_pkts.size() == my_ready_tile_ids.size());
    auto pkt_it = my_ready_pkts.begin();
    auto tile_id_it = my_ready_tile_ids.begin();
    while (pkt_it != my_ready_pkts.end() &&
           tile_id_it != my_ready_tile_ids.end()) {
        const bool affected = *tile_id_it < num_tiles &&
            (*tile_id_it == tileID ||
             (wordSize == 8 && *tile_id_it == tileID + 1));
        if (affected && spd->getTileReady(*tile_id_it)) {
            PacketPtr pkt = *pkt_it;
            DPRINTF(MAAController,
                    "%s: responding to outstanding ready packet %s!\n",
                    __func__, pkt->print());
            pkt->makeTimingResponse();
            pkt->headerDelay = pkt->payloadDelay = 0;
            cpuSidePorts[0]->schedTimingResp(pkt, getClockEdge(Cycles(1)));
            pkt_it = my_ready_pkts.erase(pkt_it);
            tile_id_it = my_ready_tile_ids.erase(tile_id_it);
        } else {
            pkt_it++;
            tile_id_it++;
        }
    }
    for (auto *port : cpuSidePorts) {
        port->retryTileRequest();
    }
}
void MAA::resetVirtualPageReady(int tokenTileID, Addr backingAddr,
                                int wordSize) {
    panic_if(tokenTileID < 0 || tokenTileID >= num_tiles,
             "invalid virtual completion token tile %d\n", tokenTileID);
    const int firstReadyID = num_tiles + tokenTileID * MaxVirtualPages;
    const int lastReadyID = firstReadyID + MaxVirtualPages;
    panic_if(std::any_of(
                 my_ready_tile_ids.begin(), my_ready_tile_ids.end(),
                 [firstReadyID, lastReadyID](int readyID) {
                     return readyID >= firstReadyID && readyID < lastReadyID;
                 }),
             "token tile %d reused with an outstanding virtual-page wait\n",
             tokenTileID);
    virtualPageReady[tokenTileID].fill(false);
    virtualPageReadyTransaction[tokenTileID].fill(0);
    panic_if(virtualPageGeneration[tokenTileID] ==
                 std::numeric_limits<uint64_t>::max(),
             "virtual completion token %d generation overflow\n",
             tokenTileID);
    ++virtualPageGeneration[tokenTileID];
    virtualPageBackingAddr[tokenTileID] = backingAddr;
    virtualPageWordSize[tokenTileID] = wordSize;
    virtualProducerRegistrationTick[tokenTileID] = curTick();
    virtualPageLastReadyTick[tokenTileID] = 0;
}
bool MAA::getVirtualPageReady(int tokenTileID, int pageID) const {
    panic_if(tokenTileID < 0 || tokenTileID >= num_tiles || pageID < 0 ||
                 pageID >= MaxVirtualPages,
             "invalid virtual page token=%d page=%d\n", tokenTileID,
             pageID);
    return virtualPageReady[tokenTileID][pageID];
}
uint64_t
MAA::getVirtualPageReadyTransaction(int tokenTileID, int pageID) const
{
    panic_if(tokenTileID < 0 || tokenTileID >= num_tiles || pageID < 0 ||
                 pageID >= MaxVirtualPages,
             "invalid virtual page transaction token=%d page=%d\n",
             tokenTileID, pageID);
    return virtualPageReadyTransaction[tokenTileID][pageID];
}
uint64_t MAA::getVirtualPageGeneration(int tokenTileID) const {
    panic_if(tokenTileID < 0 || tokenTileID >= num_tiles,
             "invalid virtual page token=%d\n", tokenTileID);
    return virtualPageGeneration[tokenTileID];
}
Tick MAA::getVirtualProducerRegistrationTick(int tokenTileID) const {
    panic_if(tokenTileID < 0 || tokenTileID >= num_tiles,
             "invalid virtual page token=%d\n", tokenTileID);
    return virtualProducerRegistrationTick[tokenTileID];
}
void
MAA::setVirtualLineWordsReady(int tokenTileID, Addr backingAddr, int lineID,
                              uint16_t wordMask, uint64_t transactionID)
{
    if (!direct_retirement_line_handoff ||
        !directRetirementExecution.active ||
        directRetirementExecution.tokenTile != tokenTileID)
        return;
    panic_if(directRetirementExecution.generation !=
                 virtualPageGeneration[tokenTileID] ||
                 directRetirementExecution.backingAddress != backingAddr,
             "Direct-retirement token %d received a stale line %d\n",
             tokenTileID, lineID);
    const uint16_t ready_before = directRetirement.producerLineAckCount();
    const HybridConsumerPipeline::ProducerLineAck ack{
        directRetirementExecution.generation,
        static_cast<uint16_t>(lineID), wordMask, transactionID};
    panic_if(!directRetirement.notifyProducerLineWriteAck(ack),
             "Direct-retirement rejected producer line WriteResp "
             "token=%d line=%d transaction=%lu\n",
             tokenTileID, lineID, transactionID);
    if (directRetirement.producerLineAckCount() != ready_before) {
        stats.direct_retirement_producer_line_acks++;
        DPRINTF(MAAVirtualTrace,
                "event=direct_retirement_producer_line_ready schema=1 "
                "occurrence=%lu generation=%lu line=%d transaction=%lu\n",
                directRetirementTraceOccurrence++,
                directRetirementExecution.generation, lineID, transactionID);
        scheduleDirectRetirementEvent();
    }
}

void
MAA::setVirtualPageReady(int tokenTileID, int pageID,
                         uint64_t transactionID)
{
    panic_if(getVirtualPageReady(tokenTileID, pageID),
             "virtual page token=%d page=%d became ready twice\n",
             tokenTileID, pageID);
    panic_if(transactionID == 0,
             "virtual page token=%d page=%d lacks final WriteResp identity\n",
             tokenTileID, pageID);
    virtualPageReady[tokenTileID][pageID] = true;
    virtualPageReadyTransaction[tokenTileID][pageID] = transactionID;
    virtualPageLastReadyTick[tokenTileID] = curTick();
    stats.virtual_page_ready_signals++;

    if (transparentController.active() &&
        transparentController.descriptor().tokenTile == tokenTileID &&
        pageID < TransparentSPDController::NumPages) {
        updateTransparentBlockerTracking();
        panic_if(transparentController.descriptor().generation !=
                     virtualPageGeneration[tokenTileID],
                 "Transparent token %d received stale page %d generation\n",
                 tokenTileID, pageID);
        panic_if(!transparentController.notifyPageReady(tokenTileID, pageID),
                 "Transparent controller rejected ready token=%d page=%d\n",
                 tokenTileID, pageID);
        updateTransparentBlockerTracking();
        bool allPagesReady = true;
        for (int page = 0; page < TransparentSPDController::NumPages;
             ++page) {
            allPagesReady = allPagesReady &&
                virtualPageReady[tokenTileID][page];
        }
        if (allPagesReady) {
            panic_if(transparentMacroAllReadySampled,
                     "Transparent all-ready point sampled twice\n");
            panic_if(!transparentMacroTracker.sample(curTick()),
                     "Failed to sample transparent all-ready point\n");
            transparentMacroAllReadyRecord = transparentMacroTracker.result();
            transparentMacroAllReadyTick = curTick();
            transparentMacroAllReadySampled = true;
            snapshotTransparentBlockerTracking(
                transparentController.descriptor().generation);
        }
        tryIssueTransparentMicroOp();
    }

    if (directRetirementExecution.active &&
        directRetirementExecution.tokenTile == tokenTileID) {
        panic_if(directRetirementExecution.generation !=
                     virtualPageGeneration[tokenTileID],
                 "Direct-retirement token %d received stale page %d\n",
                 tokenTileID, pageID);
        const HybridConsumerPipeline::ProducerAck ack{
            directRetirementExecution.generation,
            static_cast<uint8_t>(pageID), transactionID};
        const uint16_t fallback_before =
            directRetirement.producerPageFallbackLineCount();
        panic_if(!directRetirement.notifyProducerWriteAck(ack),
                 "Direct-retirement rejected final producer WriteResp "
                 "token=%d page=%d transaction=%lu\n",
                 tokenTileID, pageID, transactionID);
        stats.direct_retirement_producer_acks++;
        stats.direct_retirement_page_fallback_lines +=
            directRetirement.producerPageFallbackLineCount() -
            fallback_before;
        DPRINTF(MAAVirtualTrace,
                "event=direct_retirement_producer_ack schema=1 "
                "occurrence=%lu generation=%lu page=%d transaction=%lu\n",
                directRetirementTraceOccurrence++,
                directRetirementExecution.generation, pageID, transactionID);
        scheduleDirectRetirementEvent();
    }

    const int readyID =
        num_tiles + tokenTileID * MaxVirtualPages + pageID;
    assert(my_ready_pkts.size() == my_ready_tile_ids.size());
    auto pktIt = my_ready_pkts.begin();
    auto readyIt = my_ready_tile_ids.begin();
    while (pktIt != my_ready_pkts.end()) {
        if (*readyIt != readyID) {
            ++pktIt;
            ++readyIt;
            continue;
        }
        PacketPtr pkt = *pktIt;
        DPRINTF(MAAVirtualTrace,
                "event=page_wait_wakeup token=%d page=%d ready_id=%d\n",
                tokenTileID, pageID, readyID);
        pkt->makeTimingResponse();
        pkt->headerDelay = pkt->payloadDelay = 0;
        cpuSidePorts[0]->schedTimingResp(pkt, getClockEdge(Cycles(1)));
        pktIt = my_ready_pkts.erase(pktIt);
        readyIt = my_ready_tile_ids.erase(readyIt);
        stats.virtual_page_wait_responses++;
    }
}
void MAA::finishInstructionInvalidate(Instruction *instruction, int tileID) {
    invalidatorIdle = true;
    spd->setTileClean(tileID, instruction->getWordSize(tileID));
    ifile->finishInstructionInvalidate(instruction, tileID, (uint8_t)(spd->getTileStatus(tileID)));
    scheduleIssueInstructionEvent();
    if (allFuncUnitsIdle()) {
        my_last_idle_tick = curTick();
    }
}
void MAA::scheduleIssueInstructionEvent(int latency) {
    DPRINTF(MAAController, "%s: scheduling issue for the next %d cycles!\n", __func__, latency);
    Tick new_when = curTick() + latency;
    if (!issueInstructionEvent.scheduled()) {
        schedule(issueInstructionEvent, new_when);
    } else {
        Tick old_when = issueInstructionEvent.when();
        if (new_when < old_when)
            reschedule(issueInstructionEvent, new_when);
    }
}
void MAA::scheduleDispatchInstructionEvent(int latency) {
    DPRINTF(MAAController, "%s: scheduling instruction dispatch for the next %d cycles!\n", __func__, latency);
    Tick new_when = curTick() + latency;
    if (!dispatchInstructionEvent.scheduled()) {
        schedule(dispatchInstructionEvent, new_when);
    } else {
        Tick old_when = dispatchInstructionEvent.when();
        if (new_when < old_when)
            reschedule(dispatchInstructionEvent, new_when);
    }
}
void MAA::scheduleDispatchRegisterEvent(int latency) {
    DPRINTF(MAAController, "%s: scheduling register dispatch for the next %d cycles!\n", __func__, latency);
    Tick new_when = curTick() + latency;
    if (!dispatchRegisterEvent.scheduled()) {
        schedule(dispatchRegisterEvent, new_when);
    } else {
        Tick old_when = dispatchRegisterEvent.when();
        if (new_when < old_when)
            reschedule(dispatchRegisterEvent, new_when);
    }
}
Tick MAA::getClockEdge(Cycles cycles) const {
    return clockEdge(cycles);
}
Cycles MAA::getTicksToCycles(Tick t) const {
    return ticksToCycles(t);
}
Tick MAA::getCyclesToTicks(Cycles c) const {
    return cyclesToTicks(c);
}
void MAA::resetStats() {
    my_last_idle_tick = curTick();
    my_last_reset_tick = curTick();
    printf("Resetting MAA stats\n");
    ClockedObject::resetStats();
    printf("NumInst after reset: %lf\n", stats.numInst.value());
}

#define MAKE_INDIRECT_STAT_NAME(name) \
    (std::string("I") + std::to_string(indirect_id) + "_" + std::string(name)).c_str()

#define MAKE_STREAM_STAT_NAME(name) \
    (std::string("S") + std::to_string(stream_id) + "_" + std::string(name)).c_str()

#define MAKE_RANGE_STAT_NAME(name) \
    (std::string("A") + std::to_string(range_id) + "_" + std::string(name)).c_str()

#define MAKE_ALU_STAT_NAME(name) \
    (std::string("A") + std::to_string(alu_id) + "_" + std::string(name)).c_str()

#define MAKE_INVALIDATOR_STAT_NAME(name) \
    (std::string("INV_") + std::string(name)).c_str()

Tick MAA::getCurTick() {
    return curTick();
}

void MAA::MAAStats::preDumpStats() {
    statistics::Group::preDumpStats();

    cycles_TOTAL = maa->getTicksToCycles(maa->getCurTick() - maa->my_last_reset_tick);
    if (maa->allFuncUnitsIdle())
        cycles_IDLE += maa->getTicksToCycles(maa->getCurTick() - maa->my_last_idle_tick);
}
MAA::MAAStats::MAAStats(statistics::Group *parent, int num_indirect_units, MAA *_maa)
    : statistics::Group(parent),
      maa(_maa),
      ADD_STAT(numInst_INDRD, statistics::units::Count::get(), "number of indirect read instructions"),
      ADD_STAT(numInst_INDWR, statistics::units::Count::get(), "number of indirect write instructions"),
      ADD_STAT(numInst_INDRMW, statistics::units::Count::get(), "number of indirect read-modify-write instructions"),
      ADD_STAT(numInst_STRRD, statistics::units::Count::get(), "number of stream read instructions"),
      ADD_STAT(numInst_STRWR, statistics::units::Count::get(), "number of stream write instructions"),
      ADD_STAT(numInst_RANGE, statistics::units::Count::get(), "number of range loop instructions"),
      ADD_STAT(numInst_ALUS, statistics::units::Count::get(), "number of ALU Scalar instructions"),
      ADD_STAT(numInst_ALUV, statistics::units::Count::get(), "number of ALU Vector instructions"),
      ADD_STAT(numInst_ALUR, statistics::units::Count::get(), "number of ALU Reduction instructions"),
      ADD_STAT(numInst_INV, statistics::units::Count::get(), "number of Invalidation for instructions"),
      ADD_STAT(numInst, statistics::units::Count::get(), "total number of instructions"),
      ADD_STAT(cycles_INDRD, statistics::units::Count::get(), "number of indirect read instruction cycles"),
      ADD_STAT(cycles_INDWR, statistics::units::Count::get(), "number of indirect write instruction cycles"),
      ADD_STAT(cycles_INDRMW, statistics::units::Count::get(), "number of indirect read-modify-write instruction cycles"),
      ADD_STAT(cycles_STRRD, statistics::units::Count::get(), "number of stream read instruction cycles"),
      ADD_STAT(cycles_STRWR, statistics::units::Count::get(), "number of stream write instruction cycles"),
      ADD_STAT(cycles_RANGE, statistics::units::Count::get(), "number of range loop instruction cycles"),
      ADD_STAT(cycles_ALUS, statistics::units::Count::get(), "number of ALU Scalar instruction cycles"),
      ADD_STAT(cycles_ALUV, statistics::units::Count::get(), "number of ALU Vector instruction cycles"),
      ADD_STAT(cycles_ALUR, statistics::units::Count::get(), "number of ALU Reduction instruction cycles"),
      ADD_STAT(cycles_INV, statistics::units::Count::get(), "number of Invalidation for instruction cycles"),
      ADD_STAT(cycles_IDLE, statistics::units::Count::get(), "number of idle cycles"),
      ADD_STAT(cycles_BUSY, statistics::units::Count::get(), "number of busy cycles"),
      ADD_STAT(cycles_TOTAL, statistics::units::Count::get(), "number of total cycles"),
      ADD_STAT(cycles, statistics::units::Count::get(), "total number of instruction cycles"),
      ADD_STAT(avgCPI_INDRD, statistics::units::Count::get(), "average CPI for indirect read instructions"),
      ADD_STAT(avgCPI_INDWR, statistics::units::Count::get(), "average CPI for indirect write instructions"),
      ADD_STAT(avgCPI_INDRMW, statistics::units::Count::get(), "average CPI for indirect read-modify-write instructions"),
      ADD_STAT(avgCPI_STRRD, statistics::units::Count::get(), "average CPI for stream read instructions"),
      ADD_STAT(avgCPI_STRWR, statistics::units::Count::get(), "average CPI for stream write instructions"),
      ADD_STAT(avgCPI_RANGE, statistics::units::Count::get(), "average CPI for range loop instructions"),
      ADD_STAT(avgCPI_ALUS, statistics::units::Count::get(), "average CPI for ALU Scalar instructions"),
      ADD_STAT(avgCPI_ALUV, statistics::units::Count::get(), "average CPI for ALU Vector instructions"),
      ADD_STAT(avgCPI_ALUR, statistics::units::Count::get(), "average CPI for ALU Reduction instructions"),
      ADD_STAT(avgCPI_INV, statistics::units::Count::get(), "average CPI for Invalidation for instructions"),
      ADD_STAT(avgCPI, statistics::units::Count::get(), "average CPI for all instructions"),
      ADD_STAT(port_cache_WR_packets, statistics::units::Count::get(), "number of cache write packets"),
      ADD_STAT(port_cache_RD_packets, statistics::units::Count::get(), "number of cache read packets"),
      ADD_STAT(port_mem_WR_packets, statistics::units::Count::get(), "number of memory write packets"),
      ADD_STAT(port_mem_RD_packets, statistics::units::Count::get(), "number of memory read packets"),
      ADD_STAT(cpu_spd_data_read_deferrals,
               statistics::units::Count::get(),
               "cacheable SPD data reads deferred for tile readiness"),
      ADD_STAT(cpu_spd_data_read_retry_signals,
               statistics::units::Count::get(),
               "retry signals issued for deferred cacheable SPD data reads"),
      ADD_STAT(cpu_spd_data_read_retry_attempts,
               statistics::units::Count::get(),
               "requests presented after cacheable SPD read retry signals"),
      ADD_STAT(cpu_spd_data_read_retry_acceptances,
               statistics::units::Count::get(),
               "requests accepted after cacheable SPD read retry signals"),
      ADD_STAT(virtual_page_ready_signals,
               statistics::units::Count::get(),
               "virtual output pages marked ready"),
      ADD_STAT(virtual_page_wait_reads,
               statistics::units::Count::get(),
               "CPU virtual-page readiness reads"),
      ADD_STAT(virtual_page_wait_deferrals,
               statistics::units::Count::get(),
               "CPU virtual-page readiness reads deferred until page ready"),
      ADD_STAT(virtual_page_wait_responses,
               statistics::units::Count::get(),
               "CPU virtual-page readiness responses returned"),
      ADD_STAT(virtual_retirement_native_deferrals,
               statistics::units::Count::get(),
               "MAA packets deferred by virtual-retirement exact-address "
               "serialization"),
      ADD_STAT(virtual_retirement_queue_deferrals,
               statistics::units::Count::get(),
               "MAA packets deferred by a nonempty exact-address FIFO after "
               "the virtual-retirement owner completed"),
      ADD_STAT(direct_retirement_descriptors, statistics::units::Count::get(),
               "direct-retirement descriptors admitted"),
      ADD_STAT(direct_retirement_producer_acks,
               statistics::units::Count::get(),
               "producer pages exposed by exact final WriteResp"),
      ADD_STAT(direct_retirement_producer_line_acks,
               statistics::units::Count::get(),
               "producer backing lines completed by exact WriteResp set"),
      ADD_STAT(direct_retirement_page_fallback_lines,
               statistics::units::Count::get(),
               "producer lines released by conservative page closure"),
      ADD_STAT(direct_retirement_read_issues, statistics::units::Count::get(),
               "direct-retirement backing line reads issued"),
      ADD_STAT(direct_retirement_read_responses,
               statistics::units::Count::get(),
               "direct-retirement backing line read responses"),
      ADD_STAT(direct_retirement_alu_issues, statistics::units::Count::get(),
               "direct-retirement charged ALU-line issues"),
      ADD_STAT(direct_retirement_alu_completions,
               statistics::units::Count::get(),
               "direct-retirement charged ALU-line completions"),
      ADD_STAT(direct_retirement_write_issues,
               statistics::units::Count::get(),
               "direct-retirement full-line WriteReq issues"),
      ADD_STAT(direct_retirement_write_responses,
               statistics::units::Count::get(),
               "direct-retirement exact WriteResp completions"),
      ADD_STAT(direct_retirement_credit_high_water,
               statistics::units::Count::get(),
               "maximum direct-retirement 64-byte credits in use"),
      ADD_STAT(direct_retirement_credit_stalls,
               statistics::units::Count::get(),
               "direct-retirement scheduler attempts stalled by all credits"),
      ADD_STAT(direct_retirement_address_stalls,
               statistics::units::Count::get(),
               "direct-retirement sends deferred behind an MAA address owner"),
      ADD_STAT(direct_retirement_retries, statistics::units::Count::get(),
               "direct-retirement cache-port refusals"),
      ADD_STAT(direct_retirement_overlap_ticks,
               statistics::units::Tick::get(),
               "direct-retirement measured read/ALU/write overlap ticks"),
      ADD_STAT(direct_retirement_active_stage_high_water,
               statistics::units::Count::get(),
               "maximum simultaneously active direct-retirement stages"),
      ADD_STAT(direct_retirement_fallbacks, statistics::units::Count::get(),
               "direct-retirement descriptors retained on the existing "
               "partial or unaligned fallback"),
      ADD_STAT(direct_retirement_payload_bytes,
               statistics::units::Byte::get(),
               "maximum direct-retirement cache-line payload provisioned"),
      ADD_STAT(direct_retirement_control_bytes,
               statistics::units::Byte::get(),
               "conservative persistent direct-retirement scheduler state"),
      ADD_STAT(port_mem_WR_rowhit,
               statistics::units::Count::get(),
               "indirect writebacks issued to an already-open DRAM row "
               "(per-bank)"),
      ADD_STAT(port_cache_packets,
               statistics::units::Count::get(),
               "number of cache packets"),
      ADD_STAT(port_mem_packets,
               statistics::units::Count::get(),
               "number of memory packets"),
      ADD_STAT(port_cache_WR_BW,
               statistics::units::Count::get(),
               "cache write bandwidth (GB/s)"),
      ADD_STAT(port_cache_RD_BW,
               statistics::units::Count::get(),
               "cache read bandwidth (GB/s)"),
      ADD_STAT(port_cache_BW,
               statistics::units::Count::get(),
               "cache total bandwidth (GB/s)"),
      ADD_STAT(port_mem_WR_BW,
               statistics::units::Count::get(),
               "memory write bandwidth (GB/s)"),
      ADD_STAT(port_mem_RD_BW,
               statistics::units::Count::get(),
               "memory read bandwidth (GB/s)"),
      ADD_STAT(port_mem_BW,
               statistics::units::Count::get(),
               "memory total bandwidth (GB/s)") {

    numInst_INDRD.flags(statistics::nozero);
    numInst_INDWR.flags(statistics::nozero);
    numInst_INDRMW.flags(statistics::nozero);
    numInst_STRRD.flags(statistics::nozero);
    numInst_STRWR.flags(statistics::nozero);
    numInst_RANGE.flags(statistics::nozero);
    numInst_ALUS.flags(statistics::nozero);
    numInst_ALUV.flags(statistics::nozero);
    numInst_ALUR.flags(statistics::nozero);
    numInst_INV.flags(statistics::nozero);
    numInst.flags(statistics::nozero);
    cycles_INDRD.flags(statistics::nozero);
    cycles_INDWR.flags(statistics::nozero);
    cycles_INDRMW.flags(statistics::nozero);
    cycles_STRRD.flags(statistics::nozero);
    cycles_STRWR.flags(statistics::nozero);
    cycles_RANGE.flags(statistics::nozero);
    cycles_ALUS.flags(statistics::nozero);
    cycles_ALUV.flags(statistics::nozero);
    cycles_ALUR.flags(statistics::nozero);
    cycles_INV.flags(statistics::nozero);
    cycles_IDLE.flags(statistics::nozero);
    cycles_TOTAL.flags(statistics::nozero);
    cycles.flags(statistics::nozero);
    port_cache_WR_packets.flags(statistics::nozero);
    port_cache_RD_packets.flags(statistics::nozero);
    port_mem_WR_packets.flags(statistics::nozero);
    port_mem_RD_packets.flags(statistics::nozero);
    port_mem_WR_rowhit.flags(statistics::nozero);

    cycles_BUSY = cycles_TOTAL - cycles_IDLE;
    avgCPI_INDRD = cycles_INDRD / numInst_INDRD;
    avgCPI_INDWR = cycles_INDWR / numInst_INDWR;
    avgCPI_INDRMW = cycles_INDRMW / numInst_INDRMW;
    avgCPI_STRRD = cycles_STRRD / numInst_STRRD;
    avgCPI_STRWR = cycles_STRWR / numInst_STRWR;
    avgCPI_RANGE = cycles_RANGE / numInst_RANGE;
    avgCPI_ALUS = cycles_ALUS / numInst_ALUS;
    avgCPI_ALUV = cycles_ALUV / numInst_ALUV;
    avgCPI_ALUR = cycles_ALUR / numInst_ALUR;
    avgCPI_INV = cycles_INV / numInst_INV;
    avgCPI = cycles_TOTAL / numInst;
    port_cache_packets = port_cache_WR_packets + port_cache_RD_packets;
    port_mem_packets = port_mem_WR_packets + port_mem_RD_packets;
    port_cache_WR_BW = port_cache_WR_packets * 64 / (cycles_TOTAL / 3.2);
    port_cache_RD_BW = port_cache_RD_packets * 64 / (cycles_TOTAL / 3.2);
    port_cache_BW = port_cache_packets * 64 / (cycles_TOTAL / 3.2);
    port_mem_WR_BW = port_mem_WR_packets * 64 / (cycles_TOTAL / 3.2);
    port_mem_RD_BW = port_mem_RD_packets * 64 / (cycles_TOTAL / 3.2);
    port_mem_BW = port_mem_packets * 64 / (cycles_TOTAL / 3.2);

    cycles_BUSY.flags(statistics::nonan | statistics::nozero);
    avgCPI_INDRD.flags(statistics::nonan | statistics::nozero);
    avgCPI_INDWR.flags(statistics::nonan | statistics::nozero);
    avgCPI_INDRMW.flags(statistics::nonan | statistics::nozero);
    avgCPI_STRRD.flags(statistics::nonan | statistics::nozero);
    avgCPI_STRWR.flags(statistics::nonan | statistics::nozero);
    avgCPI_RANGE.flags(statistics::nonan | statistics::nozero);
    avgCPI_ALUS.flags(statistics::nonan | statistics::nozero);
    avgCPI_ALUV.flags(statistics::nonan | statistics::nozero);
    avgCPI_ALUR.flags(statistics::nonan | statistics::nozero);
    avgCPI_INV.flags(statistics::nonan | statistics::nozero);
    avgCPI.flags(statistics::nonan | statistics::nozero);
    port_cache_WR_BW.flags(statistics::nonan | statistics::nozero);
    port_cache_RD_BW.flags(statistics::nonan | statistics::nozero);
    port_cache_BW.flags(statistics::nonan | statistics::nozero);
    port_mem_WR_BW.flags(statistics::nonan | statistics::nozero);
    port_mem_RD_BW.flags(statistics::nonan | statistics::nozero);
    port_mem_BW.flags(statistics::nonan | statistics::nozero);

    for (int indirect_id = 0; indirect_id < num_indirect_units; indirect_id++) {
        IND_NumInsts.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_NumInsts"), statistics::units::Count::get(), "number of instructions"));
        IND_NumWordsInserted.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_NumWordsInserted"), statistics::units::Count::get(), "number of words inserted to the row table"));
        IND_NumCacheLineInserted.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_NumCacheLineInserted"), statistics::units::Count::get(), "number of cachelines inserted to the row table"));
        IND_NumRowsInserted.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_NumRowsInserted"), statistics::units::Count::get(), "number of rows inserted to the row table"));
        IND_NumUniqueWordsInserted.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_NumUniqueWordsInserted"), statistics::units::Count::get(), "number of unique words inserted to the row table"));
        IND_NumUniqueCacheLineInserted.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_NumUniqueCacheLineInserted"), statistics::units::Count::get(), "number of unique cachelines inserted to the row table"));
        IND_NumUniqueRowsInserted.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_NumUniqueRowsInserted"), statistics::units::Count::get(), "number of unique rows inserted to the row table"));
        IND_NumRTFull.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_NumRTFull"), statistics::units::Count::get(), "number of row table full events"));
        IND_NumOTFull.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_NumOTFull"), statistics::units::Count::get(), "number of offset table full events"));
        IND_NumOTEpochDrain.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_NumOTEpochDrain"), statistics::units::Count::get(), "number of offset table epoch drains"));
        IND_AvgWordsPerCacheLine.push_back(new statistics::Formula(this, MAKE_INDIRECT_STAT_NAME("IND_AvgWordsPerCacheLine"), statistics::units::Count::get(), "average number of words per cacheline"));
        IND_AvgCacheLinesPerRow.push_back(new statistics::Formula(this, MAKE_INDIRECT_STAT_NAME("IND_AvgCacheLinesPerRow"), statistics::units::Count::get(), "average number of cachelines per row"));
        IND_AvgRowsPerInst.push_back(new statistics::Formula(this, MAKE_INDIRECT_STAT_NAME("IND_AvgRowsPerInst"), statistics::units::Count::get(), "average number of rows per indirect instruction"));
        IND_AvgUniqueWordsPerCacheLine.push_back(new statistics::Formula(this, MAKE_INDIRECT_STAT_NAME("IND_AvgUniqueWordsPerCacheLine"), statistics::units::Count::get(), "average number of unique words per cacheline"));
        IND_AvgUniqueCacheLinesPerRow.push_back(new statistics::Formula(this, MAKE_INDIRECT_STAT_NAME("IND_AvgUniqueCacheLinesPerRow"), statistics::units::Count::get(), "average number of unique cachelines per row"));
        IND_AvgUniqueRowsPerInst.push_back(new statistics::Formula(this, MAKE_INDIRECT_STAT_NAME("IND_AvgUniqueRowsPerInst"), statistics::units::Count::get(), "average number of unique rows per indirect instruction"));
        IND_AvgRTFullsPerInst.push_back(new statistics::Formula(this, MAKE_INDIRECT_STAT_NAME("IND_AvgRTFullsPerInst"), statistics::units::Count::get(), "average number of row table full events per indirect instruction"));
        IND_CyclesFill.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_CyclesFill"), statistics::units::Count::get(), "number of cycles in the FILL stage"));
        IND_CyclesBuild.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_CyclesBuild"), statistics::units::Count::get(), "number of cycles in the BUILD stage"));
        IND_CyclesRequest.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_CyclesRequest"), statistics::units::Count::get(), "number of cycles in the REQUEST stage"));
        IND_VirtRequestCyclesBuild.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_VirtRequestCyclesBuild"), statistics::units::Count::get(), "virtual request-interval cycles spent rebuilding row-table work"));
        IND_VirtRequestCyclesSourceFlight.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_VirtRequestCyclesSourceFlight"), statistics::units::Count::get(), "virtual request-interval cycles with source responses in flight"));
        IND_VirtRequestCyclesRetained.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_VirtRequestCyclesRetained"), statistics::units::Count::get(), "virtual request-interval cycles with returned responses blocked in retained slots"));
        IND_VirtRequestCyclesWrites.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_VirtRequestCyclesWrites"), statistics::units::Count::get(), "virtual request-interval cycles with retirement writes outstanding"));
        IND_VirtRequestCyclesFinalDrain.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_VirtRequestCyclesFinalDrain"), statistics::units::Count::get(), "virtual request-interval cycles draining output after all sources"));
        IND_VirtRequestCyclesRunnable.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_VirtRequestCyclesRunnable"), statistics::units::Count::get(), "other virtual request-interval cycles"));
        IND_VirtBuildRounds.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_VirtBuildRounds"), statistics::units::Count::get(), "number of virtual row-table build rounds"));
        IND_VirtResponseWordHighWater.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_VirtResponseWordHighWater"), statistics::units::Count::get(), "sum of per-instruction peak reserved virtual response words"));
        IND_VirtResponseWordPoolStalls.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_VirtResponseWordPoolStalls"), statistics::units::Count::get(), "virtual source requests deferred by the shared response-word pool"));
        IND_VirtWriteIssues.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_VirtWriteIssues"), statistics::units::Count::get(), "number of virtual retirement writes issued"));
        IND_VirtWriteCompletions.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_VirtWriteCompletions"), statistics::units::Count::get(), "number of virtual retirement writes completed"));
        IND_VirtWriteAddressConflicts.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_VirtWriteAddressConflicts"), statistics::units::Count::get(), "virtual retirement write attempts deferred by an exact-address MAA transaction conflict"));
        IND_VirtIdealizedAckPages.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtIdealizedAckPages"),
            statistics::units::Count::get(),
            "virtual pages exposed at final write issue by the diagnostic "
            "idealized-ack upper bound"));
        IND_VirtPagesReady.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtPagesReady"),
            statistics::units::Count::get(),
            "virtual output pages exposed to their consumer"));
        IND_VirtPagesReadyBeforeSourceDrain.push_back(new statistics::Scalar(
            this,
            MAKE_INDIRECT_STAT_NAME("IND_VirtPagesReadyBeforeSourceDrain"),
            statistics::units::Count::get(),
            "virtual output pages ready before all source responses drained"));
        IND_VirtFirstPageReadyCycles.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtFirstPageReadyCycles"),
            statistics::units::Count::get(),
            "cycles from virtual instruction decode to first output page "
            "ready"));
        IND_VirtAllPagesReadyCycles.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtAllPagesReadyCycles"),
            statistics::units::Count::get(),
            "cycles from virtual instruction decode to all output pages "
            "ready"));
        IND_VirtPageReadySpanCycles.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtPageReadySpanCycles"),
            statistics::units::Count::get(),
            "cycles between first and last virtual output page becoming "
            "ready"));
        IND_CyclesRTAccess.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_CyclesRTAccess"),
            statistics::units::Count::get(),
            "number of cycles spent on row table access"));
        IND_CyclesSPDReadAccess.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_CyclesSPDReadAccess"),
            statistics::units::Count::get(),
            "number of cycles spent on SPD read access"));
        IND_CyclesSPDWriteAccess.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_CyclesSPDWriteAccess"),
            statistics::units::Count::get(),
            "number of cycles spent on SPD write access"));
        IND_AvgCyclesFillPerInst.push_back(new statistics::Formula(
            this, MAKE_INDIRECT_STAT_NAME("IND_AvgCyclesFillPerInst"),
            statistics::units::Count::get(),
            "average FILL-stage cycles per indirect instruction"));
        IND_AvgCyclesBuildPerInst.push_back(new statistics::Formula(
            this, MAKE_INDIRECT_STAT_NAME("IND_AvgCyclesBuildPerInst"),
            statistics::units::Count::get(),
            "average BUILD-stage cycles per indirect instruction"));
        IND_AvgCyclesRequestPerInst.push_back(new statistics::Formula(
            this, MAKE_INDIRECT_STAT_NAME("IND_AvgCyclesRequestPerInst"),
            statistics::units::Count::get(),
            "average REQUEST-stage cycles per indirect instruction"));
        IND_AvgCyclesRTAccessPerInst.push_back(new statistics::Formula(
            this, MAKE_INDIRECT_STAT_NAME("IND_AvgCyclesRTAccessPerInst"),
            statistics::units::Count::get(),
            "average row-table access cycles per indirect instruction"));
        IND_AvgCyclesSPDReadAccessPerInst.push_back(new statistics::Formula(
            this, MAKE_INDIRECT_STAT_NAME("IND_AvgCyclesSPDReadAccessPerInst"),
            statistics::units::Count::get(),
            "average SPD read-access cycles per indirect instruction"));
        IND_AvgCyclesSPDWriteAccessPerInst.push_back(new statistics::Formula(
            this,
            MAKE_INDIRECT_STAT_NAME("IND_AvgCyclesSPDWriteAccessPerInst"),
            statistics::units::Count::get(),
            "average SPD write-access cycles per indirect instruction"));
        IND_LoadsCacheHitResponding.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_LoadsCacheHitResponding"), statistics::units::Count::get(), "number of loads hit in cache in the M/O state, responding back"));
        IND_LoadsCacheHitAccessing.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_LoadsCacheHitAccessing"), statistics::units::Count::get(), "number of loads hit in cache in the E/S state, reaccessed cache"));
        IND_LoadsMemAccessing.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_LoadsMemAccessing"), statistics::units::Count::get(), "number of loads miss in cache, accessed from memory"));
        IND_LoadsCacheHitRespondingLatency.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_LoadsCacheHitRespondingLatency"), statistics::units::Count::get(), "latency of loads hit in cache in the M/O state, responding back"));
        IND_LoadsCacheHitAccessingLatency.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_LoadsCacheHitAccessingLatency"), statistics::units::Count::get(), "latency of loads hit in cache in the E/S state, reaccessed cache"));
        IND_LoadsMemAccessingLatency.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_LoadsMemAccessingLatency"), statistics::units::Count::get(), "latency of loads miss in cache, accessed from memory"));
        IND_AvgLoadsCacheHitRespondingLatency.push_back(new statistics::Formula(this, MAKE_INDIRECT_STAT_NAME("IND_AvgLoadsCacheHitRespondingLatency"), statistics::units::Count::get(), "average latency of loads hit in cache in the M/O state"));
        IND_AvgLoadsCacheHitAccessingLatency.push_back(new statistics::Formula(this, MAKE_INDIRECT_STAT_NAME("IND_AvgLoadsCacheHitAccessingLatency"), statistics::units::Count::get(), "average latency of loads hit in cache in the E/S state"));
        IND_AvgLoadsMemAccessingLatency.push_back(new statistics::Formula(this, MAKE_INDIRECT_STAT_NAME("IND_AvgLoadsMemAccessingLatency"), statistics::units::Count::get(), "average latency of loads miss in cache"));
        IND_AvgLoadsCacheHitRespondingPerInst.push_back(new statistics::Formula(this, MAKE_INDIRECT_STAT_NAME("IND_AvgLoadsCacheHitRespondingPerInst"), statistics::units::Count::get(), "average number of loads hit in cache in the M/O state per indirect instruction"));
        IND_AvgLoadsCacheHitAccessingPerInst.push_back(new statistics::Formula(this, MAKE_INDIRECT_STAT_NAME("IND_AvgLoadsCacheHitAccessingPerInst"), statistics::units::Count::get(), "average number of loads hit in cache in the E/S state per indirect instruction"));
        IND_AvgLoadsMemAccessingPerInst.push_back(new statistics::Formula(this, MAKE_INDIRECT_STAT_NAME("IND_AvgLoadsMemAccessingPerInst"), statistics::units::Count::get(), "average number of loads miss in cache per indirect instruction"));
        IND_StoresMemAccessing.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_StoresMemAccessing"), statistics::units::Count::get(), "number of writes accessed from memory"));
        IND_AvgStoresMemAccessingPerInst.push_back(new statistics::Formula(this, MAKE_INDIRECT_STAT_NAME("IND_AvgStoresMemAccessingPerInst"), statistics::units::Count::get(), "average number of writes accessed from memory per indirect instruction"));
        IND_Evicts.push_back(new statistics::Scalar(this, MAKE_INDIRECT_STAT_NAME("IND_Evicts"), statistics::units::Count::get(), "number of evict accesses to the cache side port"));
        IND_AvgEvictssPerInst.push_back(new statistics::Formula(this, MAKE_INDIRECT_STAT_NAME("IND_AvgEvictssPerInst"), statistics::units::Count::get(), "average number of evict accesses to the cache side port per indirect instruction"));

        (*IND_NumInsts[indirect_id]).flags(statistics::nozero);
        (*IND_NumWordsInserted[indirect_id]).flags(statistics::nozero);
        (*IND_NumCacheLineInserted[indirect_id]).flags(statistics::nozero);
        (*IND_NumRowsInserted[indirect_id]).flags(statistics::nozero);
        (*IND_NumUniqueWordsInserted[indirect_id]).flags(statistics::nozero);
        (*IND_NumUniqueCacheLineInserted[indirect_id]).flags(statistics::nozero);
        (*IND_NumUniqueRowsInserted[indirect_id]).flags(statistics::nozero);
        (*IND_NumRTFull[indirect_id]).flags(statistics::nozero);
        (*IND_NumOTFull[indirect_id]).flags(statistics::nozero);
        (*IND_NumOTEpochDrain[indirect_id]).flags(statistics::nozero);
        (*IND_CyclesFill[indirect_id]).flags(statistics::nozero);
        (*IND_CyclesBuild[indirect_id]).flags(statistics::nozero);
        (*IND_CyclesRequest[indirect_id]).flags(statistics::nozero);
        (*IND_CyclesRTAccess[indirect_id]).flags(statistics::nozero);
        (*IND_CyclesSPDReadAccess[indirect_id]).flags(statistics::nozero);
        (*IND_CyclesSPDWriteAccess[indirect_id]).flags(statistics::nozero);
        (*IND_LoadsCacheHitResponding[indirect_id]).flags(statistics::nozero);
        (*IND_LoadsCacheHitAccessing[indirect_id]).flags(statistics::nozero);
        (*IND_LoadsMemAccessing[indirect_id]).flags(statistics::nozero);
        (*IND_LoadsCacheHitRespondingLatency[indirect_id]).flags(statistics::nozero);
        (*IND_LoadsCacheHitAccessingLatency[indirect_id]).flags(statistics::nozero);
        (*IND_LoadsMemAccessingLatency[indirect_id]).flags(statistics::nozero);
        (*IND_StoresMemAccessing[indirect_id]).flags(statistics::nozero);
        (*IND_Evicts[indirect_id]).flags(statistics::nozero);

        (*IND_AvgWordsPerCacheLine[indirect_id]) = (*IND_NumWordsInserted[indirect_id]) / (*IND_NumCacheLineInserted[indirect_id]);
        (*IND_AvgCacheLinesPerRow[indirect_id]) = (*IND_NumCacheLineInserted[indirect_id]) / (*IND_NumRowsInserted[indirect_id]);
        (*IND_AvgRowsPerInst[indirect_id]) = (*IND_NumRowsInserted[indirect_id]) / (*IND_NumInsts[indirect_id]);
        (*IND_AvgUniqueWordsPerCacheLine[indirect_id]) = (*IND_NumUniqueWordsInserted[indirect_id]) / (*IND_NumUniqueCacheLineInserted[indirect_id]);
        (*IND_AvgUniqueCacheLinesPerRow[indirect_id]) = (*IND_NumUniqueCacheLineInserted[indirect_id]) / (*IND_NumUniqueRowsInserted[indirect_id]);
        (*IND_AvgUniqueRowsPerInst[indirect_id]) = (*IND_NumUniqueRowsInserted[indirect_id]) / (*IND_NumInsts[indirect_id]);
        (*IND_AvgRTFullsPerInst[indirect_id]) = (*IND_NumRTFull[indirect_id]) / (*IND_NumInsts[indirect_id]);

        (*IND_AvgCyclesFillPerInst[indirect_id]) = (*IND_CyclesFill[indirect_id]) / (*IND_NumInsts[indirect_id]);
        (*IND_AvgCyclesBuildPerInst[indirect_id]) = (*IND_CyclesBuild[indirect_id]) / (*IND_NumInsts[indirect_id]);
        (*IND_AvgCyclesRequestPerInst[indirect_id]) = (*IND_CyclesRequest[indirect_id]) / (*IND_NumInsts[indirect_id]);

        (*IND_AvgCyclesRTAccessPerInst[indirect_id]) = (*IND_CyclesRTAccess[indirect_id]) / (*IND_NumInsts[indirect_id]);
        (*IND_AvgCyclesSPDReadAccessPerInst[indirect_id]) = (*IND_CyclesSPDReadAccess[indirect_id]) / (*IND_NumInsts[indirect_id]);
        (*IND_AvgCyclesSPDWriteAccessPerInst[indirect_id]) = (*IND_CyclesSPDWriteAccess[indirect_id]) / (*IND_NumInsts[indirect_id]);

        (*IND_AvgLoadsCacheHitRespondingPerInst[indirect_id]) = (*IND_LoadsCacheHitResponding[indirect_id]) / (*IND_NumInsts[indirect_id]);
        (*IND_AvgLoadsCacheHitAccessingPerInst[indirect_id]) = (*IND_LoadsCacheHitAccessing[indirect_id]) / (*IND_NumInsts[indirect_id]);
        (*IND_AvgLoadsMemAccessingPerInst[indirect_id]) = (*IND_LoadsMemAccessing[indirect_id]) / (*IND_NumInsts[indirect_id]);
        (*IND_AvgLoadsCacheHitRespondingLatency[indirect_id]) = (*IND_LoadsCacheHitRespondingLatency[indirect_id]) / (*IND_LoadsCacheHitResponding[indirect_id]);
        (*IND_AvgLoadsCacheHitAccessingLatency[indirect_id]) = (*IND_LoadsCacheHitAccessingLatency[indirect_id]) / (*IND_LoadsCacheHitAccessing[indirect_id]);
        (*IND_AvgLoadsMemAccessingLatency[indirect_id]) = (*IND_LoadsMemAccessingLatency[indirect_id]) / (*IND_LoadsMemAccessing[indirect_id]);
        (*IND_AvgStoresMemAccessingPerInst[indirect_id]) = (*IND_StoresMemAccessing[indirect_id]) / (*IND_NumInsts[indirect_id]);
        (*IND_AvgEvictssPerInst[indirect_id]) = (*IND_Evicts[indirect_id]) / (*IND_NumInsts[indirect_id]);

        (*IND_AvgWordsPerCacheLine[indirect_id]).flags(statistics::nozero | statistics::nonan);
        (*IND_AvgCacheLinesPerRow[indirect_id]).flags(statistics::nozero | statistics::nonan);
        (*IND_AvgRowsPerInst[indirect_id]).flags(statistics::nozero | statistics::nonan);
        (*IND_AvgUniqueWordsPerCacheLine[indirect_id]).flags(statistics::nozero | statistics::nonan);
        (*IND_AvgUniqueCacheLinesPerRow[indirect_id]).flags(statistics::nozero | statistics::nonan);
        (*IND_AvgUniqueRowsPerInst[indirect_id]).flags(statistics::nozero | statistics::nonan);
        (*IND_AvgRTFullsPerInst[indirect_id]).flags(statistics::nozero | statistics::nonan);
        (*IND_AvgCyclesFillPerInst[indirect_id]).flags(statistics::nozero | statistics::nonan);
        (*IND_AvgCyclesBuildPerInst[indirect_id]).flags(statistics::nozero | statistics::nonan);
        (*IND_AvgCyclesRequestPerInst[indirect_id]).flags(statistics::nozero | statistics::nonan);
        (*IND_AvgCyclesRTAccessPerInst[indirect_id]).flags(statistics::nozero | statistics::nonan);
        (*IND_AvgCyclesSPDReadAccessPerInst[indirect_id]).flags(statistics::nozero | statistics::nonan);
        (*IND_AvgCyclesSPDWriteAccessPerInst[indirect_id]).flags(statistics::nozero | statistics::nonan);
        (*IND_AvgLoadsCacheHitRespondingPerInst[indirect_id]).flags(statistics::nozero | statistics::nonan);
        (*IND_AvgLoadsCacheHitAccessingPerInst[indirect_id]).flags(statistics::nozero | statistics::nonan);
        (*IND_AvgLoadsMemAccessingPerInst[indirect_id]).flags(statistics::nozero | statistics::nonan);
        (*IND_AvgLoadsCacheHitRespondingLatency[indirect_id]).flags(statistics::nozero | statistics::nonan);
        (*IND_AvgLoadsCacheHitAccessingLatency[indirect_id]).flags(statistics::nozero | statistics::nonan);
        (*IND_AvgLoadsMemAccessingLatency[indirect_id]).flags(statistics::nozero | statistics::nonan);
        (*IND_AvgStoresMemAccessingPerInst[indirect_id]).flags(statistics::nozero | statistics::nonan);
        (*IND_AvgEvictssPerInst[indirect_id]).flags(statistics::nozero | statistics::nonan);
        IND_VirtIndexLineReads.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtIndexLineReads"),
            statistics::units::Count::get(),
            "cache-line reads issued by direct virtual-index ingestion"));
        IND_VirtIndexOutstandingMerges.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtIndexOutstandingMerges"),
            statistics::units::Count::get(),
            "direct-index reads attached to an outstanding read"));
        IND_VirtIndexOutstandingWaitCycles.push_back(new statistics::Scalar(
            this,
            MAKE_INDIRECT_STAT_NAME("IND_VirtIndexOutstandingWaitCycles"),
            statistics::units::Cycle::get(),
            "cycles direct-index ingestion waited on a non-mergeable packet"));
        IND_VirtIndexLineHighWater.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtIndexLineHighWater"),
            statistics::units::Count::get(),
            "sum of per-instruction peak direct-index lines in flight"));
        IND_VirtIndexWords.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtIndexWords"),
            statistics::units::Count::get(),
            "index words delivered to direct virtual-index ingestion"));
        IND_VirtIndexWordHighWater.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtIndexWordHighWater"),
            statistics::units::Count::get(),
            "sum of per-instruction peak buffered direct-index words"));
        IND_VirtIndexFilterWords.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtIndexFilterWords"),
            statistics::units::Count::get(),
            "index words examined by multi-pass DRAM-grow filtering"));
        IND_VirtIndexFilterCycles.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtIndexFilterCycles"),
            statistics::units::Cycle::get(),
            "service cycles charged to multi-pass DRAM-grow filtering"));
        IND_VirtIndexFilterWaitEvents.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtIndexFilterWaitEvents"),
            statistics::units::Count::get(),
            "scheduler waits whose critical path includes index filtering"));
        IND_VirtIndexFilterWaitCycles.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtIndexFilterWaitCycles"),
            statistics::units::Cycle::get(),
            "non-overlapped scheduler cycles caused by index filtering"));
        IND_BoundedSummaryLineReads.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedSummaryLineReads"),
            statistics::units::Count::get(),
            "LLC-visible B line reads used to build a bounded grow plan"));
        IND_BoundedSummaryWords.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedSummaryWords"),
            statistics::units::Count::get(),
            "B words inspected by bounded translated-grow discovery"));
        IND_BoundedSummaryRecords.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedSummaryRecords"),
            statistics::units::Count::get(),
            "distinct translated grows in the phase-shared histogram"));
        IND_BoundedSummaryHashProbes.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedSummaryHashProbes"),
            statistics::units::Count::get(),
            "finite phase-shared histogram hash probes"));
        IND_BoundedSummaryReductionVisits.push_back(new statistics::Scalar(
            this,
            MAKE_INDIRECT_STAT_NAME("IND_BoundedSummaryReductionVisits"),
            statistics::units::Count::get(),
            "finite histogram slots and records visited by grow planning"));
        IND_BoundedSummaryPlanBytes.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedSummaryPlanBytes"),
            statistics::units::Byte::get(),
            "fixed-width retained translated-grow replay plan bytes"));
        IND_BoundedBucketLineReads.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedBucketLineReads"),
            statistics::units::Count::get(),
            "LLC-visible B line reads used to bucket descriptor records"));
        IND_BoundedBucketWords.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedBucketWords"),
            statistics::units::Count::get(),
            "B words inspected once while bucketing descriptor records"));
        IND_DescriptorSpoolFilterRetryInspections.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_DescriptorSpoolFilterRetryInspections"),
                statistics::units::Count::get(),
                "partition-filter inspections retried after a descriptor "
                "spool write-credit denial"));
        IND_DescriptorSpoolFinalFlushStalls.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_DescriptorSpoolFinalFlushStalls"),
                statistics::units::Count::get(),
                "write-credit stalls while flushing final staged lines "
                "without re-inspecting a B word"));
        IND_DescriptorSpoolBScans.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_DescriptorSpoolBScans"),
            statistics::units::Count::get(),
            "complete B scans performed by resident-first spooling"));
        IND_DescriptorSpoolResidentPopulations.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_DescriptorSpoolResidentPopulations"),
                statistics::units::Count::get(),
                "populations admitted directly without backing traffic"));
        IND_DescriptorSpoolResidentDescriptors.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_DescriptorSpoolResidentDescriptors"),
                statistics::units::Count::get(),
                "descriptors classified into the direct resident pass"));
        IND_DescriptorSpoolExternalDescriptors.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_DescriptorSpoolExternalDescriptors"),
                statistics::units::Count::get(),
                "descriptors serialized to timing-visible backing"));
        IND_DescriptorSpoolExternalSegments.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_DescriptorSpoolExternalSegments"),
                statistics::units::Count::get(),
                "finite nonresident descriptor-spool segments"));
        IND_BoundedReplayLineReads.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedReplayLineReads"),
            statistics::units::Count::get(),
            "LLC-visible B line reads made by bounded replay passes"));
        IND_BoundedReplayWords.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedReplayWords"),
            statistics::units::Count::get(),
            "B words inspected by bounded replay passes"));
        IND_BoundedReplayPasses.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedReplayPasses"),
            statistics::units::Count::get(),
            "fully closed bounded replay passes"));
        IND_BoundedReplayDrains.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedReplayDrains"),
            statistics::units::Count::get(),
            "explicit Row/Word/Offset capacity drains within replay passes"));
        IND_BoundedReplayMaxEpochAdmissions.push_back(new statistics::Scalar(
            this,
            MAKE_INDIRECT_STAT_NAME("IND_BoundedReplayMaxEpochAdmissions"),
            statistics::units::Count::get(),
            "maximum admissions between explicit bounded drains"));
        IND_BoundedWordEntries.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedWordEntries"),
            statistics::units::Count::get(),
            "charged tile-proportional Word Table entries"));
        IND_BoundedOffsetLinkEntries.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedOffsetLinkEntries"),
            statistics::units::Count::get(),
            "charged Offset linked-placement fields co-resident with words"));
        IND_BoundedRowDirectoryEntries.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedRowDirectoryEntries"),
            statistics::units::Count::get(),
            "charged Row Table row-directory entries"));
        IND_BoundedRowLineEntries.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedRowLineEntries"),
            statistics::units::Count::get(),
            "charged Row Table line-directory entries"));
        IND_BoundedReorderMetadataBytes.push_back(new statistics::Scalar(
            this,
            MAKE_INDIRECT_STAT_NAME("IND_BoundedReorderMetadataBytes"),
            statistics::units::Byte::get(),
            "source-semantic bounded Word/Offset/Row metadata bytes"));
        IND_DescriptorSpoolLineWrites.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_DescriptorSpoolLineWrites"),
            statistics::units::Count::get(),
            "acknowledged descriptor-spool cache-line writes issued"));
        IND_DescriptorSpoolWriteBytes.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_DescriptorSpoolWriteBytes"),
            statistics::units::Byte::get(),
            "descriptor-spool bytes written through the cache hierarchy"));
        IND_DescriptorSpoolWriteAcks.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_DescriptorSpoolWriteAcks"),
            statistics::units::Count::get(),
            "authenticated descriptor-spool write acknowledgements"));
        IND_DescriptorSpoolLineReads.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_DescriptorSpoolLineReads"),
            statistics::units::Count::get(),
            "descriptor-spool cache-line reads issued"));
        IND_DescriptorSpoolReadBytes.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_DescriptorSpoolReadBytes"),
            statistics::units::Byte::get(),
            "descriptor-spool bytes read through the cache hierarchy"));
        IND_DescriptorSpoolWriteCreditStalls.push_back(new statistics::Scalar(
            this,
            MAKE_INDIRECT_STAT_NAME("IND_DescriptorSpoolWriteCreditStalls"),
            statistics::units::Count::get(),
            "bucket stalls at the finite acknowledged-write limit"));
        IND_DescriptorSpoolReadCreditStalls.push_back(new statistics::Scalar(
            this,
            MAKE_INDIRECT_STAT_NAME("IND_DescriptorSpoolReadCreditStalls"),
            statistics::units::Count::get(),
            "replay stalls at the finite descriptor-line read limit"));
        IND_DescriptorSpoolWriteHighWater.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_DescriptorSpoolWriteHighWater"),
            statistics::units::Count::get(),
            "maximum acknowledged descriptor writes in flight"));
        IND_DescriptorSpoolOverlapOpportunities.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_DescriptorSpoolOverlapOpportunities"),
                statistics::units::Count::get(),
                "pass boundaries with current-pass source reads in flight"));
        IND_DescriptorSpoolNextPassReadIssues.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_DescriptorSpoolNextPassReadIssues"),
                statistics::units::Count::get(),
                "descriptor lines issued before the next pass became demand"));
        IND_DescriptorSpoolNextPassReadResponses.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_DescriptorSpoolNextPassReadResponses"),
                statistics::units::Count::get(),
                "responses to descriptor reads issued before next-pass "
                "demand"));
        IND_DescriptorSpoolUsefulPrefetchedLines.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_DescriptorSpoolUsefulPrefetchedLines"),
                statistics::units::Count::get(),
                "read-ahead lines that supplied at least one descriptor "
                "byte"));
        IND_DescriptorSpoolDemandWaitsAvoided.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_DescriptorSpoolDemandWaitsAvoided"),
                statistics::units::Count::get(),
                "useful read-ahead lines ready before next-pass "
                "demand"));
        IND_DescriptorSpoolPrefetchOccupancyLineCycles.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_DescriptorSpoolPrefetchOccupancyLineCycles"),
                statistics::units::Count::get(),
                "integral of occupied read-ahead lines over MAA cycles"));
        IND_DescriptorSpoolPrefetchOccupancyHighWater.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_DescriptorSpoolPrefetchOccupancyHighWater"),
                statistics::units::Count::get(),
                "maximum existing read slots occupied by next-pass "
                "lines"));
        IND_DescriptorSpoolWastedPrefetchedLines.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_DescriptorSpoolWastedPrefetchedLines"),
                statistics::units::Count::get(),
                "read-ahead lines released without supplying descriptor "
                "data"));
        IND_DescriptorSpoolBoundaryDemandWaitEvents.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_DescriptorSpoolBoundaryDemandWaitEvents"),
                statistics::units::Count::get(),
                "next-pass cursor-zero descriptor wait intervals"));
        IND_DescriptorSpoolBoundaryDemandWaitCycles.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_DescriptorSpoolBoundaryDemandWaitCycles"),
                statistics::units::Count::get(),
                "MAA cycles waiting for cursor-zero descriptor data"));
        IND_DescriptorSpoolWithinPassDemandWaitEvents.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_DescriptorSpoolWithinPassDemandWaitEvents"),
                statistics::units::Count::get(),
                "non-boundary descriptor wait intervals"));
        IND_DescriptorSpoolWithinPassDemandWaitCycles.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_DescriptorSpoolWithinPassDemandWaitCycles"),
                statistics::units::Count::get(),
                "MAA cycles waiting for descriptor data within a pass"));
        IND_DescriptorSpoolStagingEntries.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_DescriptorSpoolStagingEntries"),
            statistics::units::Count::get(),
            "charged pass-line descriptor staging capacity"));
        IND_DescriptorSpoolControlBytes.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_DescriptorSpoolControlBytes"),
            statistics::units::Byte::get(),
            "charged finite on-chip spool control and staging bytes"));
        IND_DescriptorSpoolBackingBytes.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_DescriptorSpoolBackingBytes"),
            statistics::units::Byte::get(),
            "charged timing-visible descriptor-spool backing capacity"));
        IND_BoundedGlobalMergePopulations.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedGlobalMergePopulations"),
            statistics::units::Count::get(),
            "finite sorted descriptor runs materialized"));
        IND_BoundedGlobalMergeActiveHWM.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedGlobalMergeActiveHWM"),
            statistics::units::Count::get(),
            "maximum descriptors resident in the Row/Offset sorter"));
        IND_BoundedGlobalMergeDescriptorRecords.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_BoundedGlobalMergeDescriptorRecords"),
                statistics::units::Count::get(),
                "six-byte records materialized into sorted runs"));
        IND_BoundedGlobalMergeDescriptorBytes.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_BoundedGlobalMergeDescriptorBytes"),
                statistics::units::Byte::get(),
                "valid six-byte sorted-run payload traffic"));
        IND_BoundedGlobalMergeSortReadLines.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_BoundedGlobalMergeSortReadLines"),
                statistics::units::Count::get(),
                "timing-visible unsorted external-run lines read by the "
                "sorter"));
        IND_BoundedGlobalMergeSortedWriteLines.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_BoundedGlobalMergeSortedWriteLines"),
                statistics::units::Count::get(),
                "timing-visible sorted-run cache lines written"));
        IND_BoundedGlobalMergeSortComparisons.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_BoundedGlobalMergeSortComparisons"),
                statistics::units::Count::get(),
                "bounded RowTable grow/line selection comparisons"));
        IND_BoundedGlobalMergeMergeReadLines.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_BoundedGlobalMergeMergeReadLines"),
                statistics::units::Count::get(),
                "timing-visible sorted-run cache lines read by four heads"));
        IND_BoundedGlobalMergeMergeComparisons.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_BoundedGlobalMergeMergeComparisons"),
                statistics::units::Count::get(),
                "finite four-head key comparisons"));
        IND_BoundedGlobalMergeMergeHeadHWM.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_BoundedGlobalMergeMergeHeadHWM"),
                statistics::units::Count::get(),
                "maximum simultaneously valid descriptor heads"));
        IND_BoundedGlobalMergeALineIssues.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedGlobalMergeALineIssues"),
            statistics::units::Count::get(),
            "A cache-line requests issued by the global merge"));
        IND_BoundedGlobalMergeCoalesced.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedGlobalMergeCoalesced"),
            statistics::units::Count::get(),
            "descriptors coalesced behind an already-issued equal A line"));
        IND_BoundedGlobalMergeRowGroups.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedGlobalMergeRowGroups"),
            statistics::units::Count::get(),
            "observed consecutive RowTable slice and DRAM-row groups"));
        IND_BoundedGlobalMergeAdmissions.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedGlobalMergeAdmissions"),
            statistics::units::Count::get(),
            "descriptors admitted to the four bounded sorter populations"));
        IND_BoundedGlobalMergeRetirements.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedGlobalMergeRetirements"),
            statistics::units::Count::get(),
            "merged descriptors restored to exact logical destinations"));
        IND_BoundedGlobalMergeRunWriteAcks.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_BoundedGlobalMergeRunWriteAcks"),
                statistics::units::Count::get(),
                "authenticated sorted-run write acknowledgements"));
        IND_BoundedGlobalMergeTerminalAcks.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_BoundedGlobalMergeTerminalAcks"),
                statistics::units::Count::get(),
                "all timing responses and writes acknowledged at terminal "
                "closure"));
        IND_BoundedGlobalMergeFallbacks.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_BoundedGlobalMergeFallbacks"),
            statistics::units::Count::get(),
            "fail-closed bounded-global-merge fallback attempts"));
        IND_BoundedGlobalMergeControlBytes.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_BoundedGlobalMergeControlBytes"),
                statistics::units::Byte::get(),
                "charged finite run-writer, four-reader, head and cursor "
                "state"));
        IND_BoundedGlobalMergeBackingBytes.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_BoundedGlobalMergeBackingBytes"),
                statistics::units::Byte::get(),
                "charged four-run timing-visible backing capacity"));
        IND_VirtCombineBankAccesses.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtCombineBankAccesses"),
            statistics::units::Count::get(),
            "virtual destination-combiner word lookup/update accesses"));
        IND_VirtCombineBankConflictCycles.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtCombineBankConflictCycles"),
            statistics::units::Count::get(),
            "cycles with a virtual destination-combiner same-bank conflict"));
        IND_VirtResponseSlotHighWater.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtResponseSlotHighWater"),
            statistics::units::Count::get(),
            "sum of per-instruction peak occupied virtual response slots"));
        IND_VirtOutstandingWriteHighWater.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtOutstandingWriteHighWater"),
            statistics::units::Count::get(),
            "sum of per-instruction peak outstanding virtual retirement "
            "writes"));
        IND_VirtCombineLineHighWater.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtCombineLineHighWater"),
            statistics::units::Count::get(),
            "sum of per-instruction peak occupied virtual combiner lines"));
        IND_VirtCombineWordHighWater.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtCombineWordHighWater"),
            statistics::units::Count::get(),
            "sum of per-instruction peak buffered virtual combiner words"));
        IND_VirtFullLineWrites.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtFullLineWrites"),
            statistics::units::Count::get(),
            "number of full-cache-line virtual retirement writes"));
        IND_VirtPartialWrites.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtPartialWrites"),
            statistics::units::Count::get(),
            "number of partial-word virtual retirement writes"));
        IND_VirtPipelineCyclesIdle.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtPipelineCyclesIdle"),
            statistics::units::Count::get(),
            "virtual request cycles with no source request or retirement "
            "write in flight"));
        IND_VirtPipelineCyclesSourceOnly.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtPipelineCyclesSourceOnly"),
            statistics::units::Count::get(),
            "virtual request cycles with source requests but no retirement "
            "writes in flight"));
        IND_VirtPipelineCyclesWriteOnly.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtPipelineCyclesWriteOnly"),
            statistics::units::Count::get(),
            "virtual request cycles with retirement writes but no source "
            "requests in flight"));
        IND_VirtPipelineCyclesOverlap.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtPipelineCyclesOverlap"),
            statistics::units::Count::get(),
            "virtual request cycles with source requests and retirement "
            "writes in flight"));
    }
    for (int stream_id = 0; stream_id < maa->num_maas; stream_id++) {
        STR_NumInsts.push_back(new statistics::Scalar(this, MAKE_STREAM_STAT_NAME("STR_NumInsts"), statistics::units::Count::get(), "number of instructions"));
        STR_NumWordsInserted.push_back(new statistics::Scalar(this, MAKE_STREAM_STAT_NAME("STR_NumWordsInserted"), statistics::units::Count::get(), "number of words inserted to the request table"));
        STR_NumCacheLineInserted.push_back(new statistics::Scalar(this, MAKE_STREAM_STAT_NAME("STR_NumCacheLineInserted"), statistics::units::Count::get(), "number of cachelines inserted to the request table"));
        STR_NumRTFull.push_back(new statistics::Scalar(this, MAKE_STREAM_STAT_NAME("STR_NumRTFull"), statistics::units::Count::get(), "number of request table full events"));
        STR_AvgWordsPerCacheLine.push_back(new statistics::Formula(this, MAKE_STREAM_STAT_NAME("STR_AvgWordsPerCacheLine"), statistics::units::Count::get(), "average number of words per cacheline"));
        STR_AvgCacheLinesPerInst.push_back(new statistics::Formula(this, MAKE_STREAM_STAT_NAME("STR_AvgCacheLinesPerInst"), statistics::units::Count::get(), "average number of cachelines per stream instruction"));
        STR_AvgRTFullsPerInst.push_back(new statistics::Formula(this, MAKE_STREAM_STAT_NAME("STR_AvgRTFullsPerInst"), statistics::units::Count::get(), "average number of request table full events per stream instruction"));
        STR_CyclesRequest.push_back(new statistics::Scalar(this, MAKE_STREAM_STAT_NAME("STR_CyclesRequest"), statistics::units::Count::get(), "number of cycles in the REQUEST stage"));
        STR_CyclesRTAccess.push_back(new statistics::Scalar(this, MAKE_STREAM_STAT_NAME("STR_CyclesRTAccess"), statistics::units::Count::get(), "number of cycles for request table access"));
        STR_CyclesSPDReadAccess.push_back(new statistics::Scalar(this, MAKE_STREAM_STAT_NAME("STR_CyclesSPDReadAccess"), statistics::units::Count::get(), "number of cycles for SPD read access"));
        STR_CyclesSPDWriteAccess.push_back(new statistics::Scalar(this, MAKE_STREAM_STAT_NAME("STR_CyclesSPDWriteAccess"), statistics::units::Count::get(), "number of cycles for SPD write access"));
        STR_AvgCyclesRequestPerInst.push_back(new statistics::Formula(this, MAKE_STREAM_STAT_NAME("STR_AvgCyclesRequestPerInst"), statistics::units::Count::get(), "average number of cycles in the REQUEST stage per stream instruction"));
        STR_AvgCyclesRTAccessPerInst.push_back(new statistics::Formula(this, MAKE_STREAM_STAT_NAME("STR_AvgCyclesRTAccessPerInst"), statistics::units::Count::get(), "average number of cycles for request table access per stream instruction"));
        STR_AvgCyclesSPDReadAccessPerInst.push_back(new statistics::Formula(this, MAKE_STREAM_STAT_NAME("STR_AvgCyclesSPDReadAccessPerInst"), statistics::units::Count::get(), "average number of cycles for SPD read access per stream instruction"));
        STR_AvgCyclesSPDWriteAccessPerInst.push_back(new statistics::Formula(this, MAKE_STREAM_STAT_NAME("STR_AvgCyclesSPDWriteAccessPerInst"), statistics::units::Count::get(), "average number of cycles for SPD write access per stream instruction"));
        STR_LoadsCacheAccessing.push_back(new statistics::Scalar(this, MAKE_STREAM_STAT_NAME("STR_LoadsCacheAccessing"), statistics::units::Count::get(), "number of loads accessed from cache"));
        STR_AvgLoadsCacheAccessingPerInst.push_back(new statistics::Formula(this, MAKE_STREAM_STAT_NAME("STR_AvgLoadsCacheAccessingPerInst"), statistics::units::Count::get(), "average number of loads accessed from cache per stream instruction"));
        STR_Evicts.push_back(new statistics::Scalar(this, MAKE_STREAM_STAT_NAME("STR_Evicts"), statistics::units::Count::get(), "number of evict accesses to the cache side port"));
        STR_AvgEvictssPerInst.push_back(new statistics::Formula(this, MAKE_STREAM_STAT_NAME("STR_AvgEvictssPerInst"), statistics::units::Count::get(), "average number of evict accesses to the cache side port per stream instruction"));

        (*STR_NumInsts[stream_id]).flags(statistics::nozero);
        (*STR_NumWordsInserted[stream_id]).flags(statistics::nozero);
        (*STR_NumCacheLineInserted[stream_id]).flags(statistics::nozero);
        (*STR_NumRTFull[stream_id]).flags(statistics::nozero);
        (*STR_CyclesRequest[stream_id]).flags(statistics::nozero);
        (*STR_CyclesRTAccess[stream_id]).flags(statistics::nozero);
        (*STR_CyclesSPDReadAccess[stream_id]).flags(statistics::nozero);
        (*STR_CyclesSPDWriteAccess[stream_id]).flags(statistics::nozero);
        (*STR_LoadsCacheAccessing[stream_id]).flags(statistics::nozero);
        (*STR_Evicts[stream_id]).flags(statistics::nozero);

        (*STR_AvgWordsPerCacheLine[stream_id]) = (*STR_NumWordsInserted[stream_id]) / (*STR_NumCacheLineInserted[stream_id]);
        (*STR_AvgCacheLinesPerInst[stream_id]) = (*STR_NumCacheLineInserted[stream_id]) / (*STR_NumInsts[stream_id]);
        (*STR_AvgRTFullsPerInst[stream_id]) = (*STR_NumRTFull[stream_id]) / (*STR_NumInsts[stream_id]);

        (*STR_AvgCyclesRequestPerInst[stream_id]) = (*STR_CyclesRequest[stream_id]) / (*STR_NumInsts[stream_id]);
        (*STR_AvgCyclesRTAccessPerInst[stream_id]) = (*STR_CyclesRTAccess[stream_id]) / (*STR_NumInsts[stream_id]);
        (*STR_AvgCyclesSPDReadAccessPerInst[stream_id]) = (*STR_CyclesSPDReadAccess[stream_id]) / (*STR_NumInsts[stream_id]);
        (*STR_AvgCyclesSPDWriteAccessPerInst[stream_id]) = (*STR_CyclesSPDWriteAccess[stream_id]) / (*STR_NumInsts[stream_id]);

        (*STR_AvgLoadsCacheAccessingPerInst[stream_id]) = (*STR_LoadsCacheAccessing[stream_id]) / (*STR_NumInsts[stream_id]);
        (*STR_AvgEvictssPerInst[stream_id]) = (*STR_Evicts[stream_id]) / (*STR_NumInsts[stream_id]);

        (*STR_AvgWordsPerCacheLine[stream_id]).flags(statistics::nozero | statistics::nonan);
        (*STR_AvgCacheLinesPerInst[stream_id]).flags(statistics::nozero | statistics::nonan);
        (*STR_AvgRTFullsPerInst[stream_id]).flags(statistics::nozero | statistics::nonan);
        (*STR_AvgCyclesRequestPerInst[stream_id]).flags(statistics::nozero | statistics::nonan);
        (*STR_AvgCyclesRTAccessPerInst[stream_id]).flags(statistics::nozero | statistics::nonan);
        (*STR_AvgCyclesSPDReadAccessPerInst[stream_id]).flags(statistics::nozero | statistics::nonan);
        (*STR_AvgCyclesSPDWriteAccessPerInst[stream_id]).flags(statistics::nozero | statistics::nonan);
        (*STR_AvgLoadsCacheAccessingPerInst[stream_id]).flags(statistics::nozero | statistics::nonan);
        (*STR_AvgEvictssPerInst[stream_id]).flags(statistics::nozero | statistics::nonan);
    }
    for (int range_id = 0; range_id < maa->num_maas; range_id++) {
        RNG_NumInsts.push_back(new statistics::Scalar(this, MAKE_RANGE_STAT_NAME("RNG_NumInsts"), statistics::units::Count::get(), "number of instructions"));
        RNG_CyclesCompute.push_back(new statistics::Scalar(this, MAKE_RANGE_STAT_NAME("RNG_CyclesCompute"), statistics::units::Count::get(), "number of compute cycles in range loop"));
        RNG_CyclesSPDReadAccess.push_back(new statistics::Scalar(this, MAKE_RANGE_STAT_NAME("RNG_CyclesSPDReadAccess"), statistics::units::Count::get(), "number of cycles spent on SPD read access in range loop"));
        RNG_CyclesSPDWriteAccess.push_back(new statistics::Scalar(this, MAKE_RANGE_STAT_NAME("RNG_CyclesSPDWriteAccess"), statistics::units::Count::get(), "number of cycles spent on SPD write access in range loop"));
        RNG_AvgCyclesComputePerInst.push_back(new statistics::Formula(this, MAKE_RANGE_STAT_NAME("RNG_AvgCyclesComputePerInst"), statistics::units::Count::get(), "average number of compute cycles per range loop instruction"));
        RNG_AvgCyclesSPDReadAccessPerInst.push_back(new statistics::Formula(this, MAKE_RANGE_STAT_NAME("RNG_AvgCyclesSPDReadAccessPerInst"), statistics::units::Count::get(), "average number of cycles spent on SPD read access per range loop instruction"));
        RNG_AvgCyclesSPDWriteAccessPerInst.push_back(new statistics::Formula(this, MAKE_RANGE_STAT_NAME("RNG_AvgCyclesSPDWriteAccessPerInst"), statistics::units::Count::get(), "average number of cycles spent on SPD write access per range loop instruction"));

        (*RNG_NumInsts[range_id]).flags(statistics::nozero);
        (*RNG_CyclesCompute[range_id]).flags(statistics::nozero);
        (*RNG_CyclesSPDReadAccess[range_id]).flags(statistics::nozero);
        (*RNG_CyclesSPDWriteAccess[range_id]).flags(statistics::nozero);

        (*RNG_AvgCyclesComputePerInst[range_id]) = (*RNG_CyclesCompute[range_id]) / (*RNG_NumInsts[range_id]);
        (*RNG_AvgCyclesSPDReadAccessPerInst[range_id]) = (*RNG_CyclesSPDReadAccess[range_id]) / (*RNG_NumInsts[range_id]);
        (*RNG_AvgCyclesSPDWriteAccessPerInst[range_id]) = (*RNG_CyclesSPDWriteAccess[range_id]) / (*RNG_NumInsts[range_id]);

        (*RNG_AvgCyclesComputePerInst[range_id]).flags(statistics::nozero | statistics::nonan);
        (*RNG_AvgCyclesSPDReadAccessPerInst[range_id]).flags(statistics::nozero | statistics::nonan);
        (*RNG_AvgCyclesSPDWriteAccessPerInst[range_id]).flags(statistics::nozero | statistics::nonan);
    }
    for (int alu_id = 0; alu_id < maa->num_maas; alu_id++) {
        ALU_NumInsts.push_back(new statistics::Scalar(this, MAKE_ALU_STAT_NAME("ALU_NumInsts"), statistics::units::Count::get(), "number of instructions"));
        ALU_NumInstsCompare.push_back(new statistics::Scalar(this, MAKE_ALU_STAT_NAME("ALU_NumInstsCompare"), statistics::units::Count::get(), "number of compare instructions"));
        ALU_NumInstsCompute.push_back(new statistics::Scalar(this, MAKE_ALU_STAT_NAME("ALU_NumInstsCompute"), statistics::units::Count::get(), "number of compute instructions"));
        ALU_CyclesCompute.push_back(new statistics::Scalar(this, MAKE_ALU_STAT_NAME("ALU_CyclesCompute"), statistics::units::Count::get(), "number of cycles spent on compute"));
        ALU_CyclesSPDReadAccess.push_back(new statistics::Scalar(this, MAKE_ALU_STAT_NAME("ALU_CyclesSPDReadAccess"), statistics::units::Count::get(), "number of cycles for SPD read access"));
        ALU_CyclesSPDWriteAccess.push_back(new statistics::Scalar(this, MAKE_ALU_STAT_NAME("ALU_CyclesSPDWriteAccess"), statistics::units::Count::get(), "number of cycles for SPD write access"));
        ALU_AvgCyclesComputePerInst.push_back(new statistics::Formula(this, MAKE_ALU_STAT_NAME("ALU_AvgCyclesComputePerInst"), statistics::units::Count::get(), "average number of cycles spent on compute per ALU instruction"));
        ALU_AvgCyclesSPDReadAccessPerInst.push_back(new statistics::Formula(this, MAKE_ALU_STAT_NAME("ALU_AvgCyclesSPDReadAccessPerInst"), statistics::units::Count::get(), "average number of cycles for SPD read access per ALU instruction"));
        ALU_AvgCyclesSPDWriteAccessPerInst.push_back(new statistics::Formula(this, MAKE_ALU_STAT_NAME("ALU_AvgCyclesSPDWriteAccessPerInst"), statistics::units::Count::get(), "average number of cycles for SPD write access per ALU instruction"));
        ALU_NumComparedWords.push_back(new statistics::Scalar(this, MAKE_ALU_STAT_NAME("ALU_NumComparedWords"), statistics::units::Count::get(), "number of words compared"));
        ALU_NumTakenWords.push_back(new statistics::Scalar(this, MAKE_ALU_STAT_NAME("ALU_NumTakenWords"), statistics::units::Count::get(), "number of words which comparison was taken"));
        ALU_AvgNumTakenWordsPerComparedWords.push_back(new statistics::Formula(this, MAKE_ALU_STAT_NAME("ALU_AvgNumTakenWordsPerComparedWords"), statistics::units::Count::get(), "portion of words which comparison was taken"));

        (*ALU_NumInsts[alu_id]).flags(statistics::nozero);
        (*ALU_NumInstsCompare[alu_id]).flags(statistics::nozero);
        (*ALU_NumInstsCompute[alu_id]).flags(statistics::nozero);
        (*ALU_CyclesCompute[alu_id]).flags(statistics::nozero);
        (*ALU_CyclesSPDReadAccess[alu_id]).flags(statistics::nozero);
        (*ALU_CyclesSPDWriteAccess[alu_id]).flags(statistics::nozero);
        (*ALU_NumComparedWords[alu_id]).flags(statistics::nozero);
        (*ALU_NumTakenWords[alu_id]).flags(statistics::nozero);

        (*ALU_AvgCyclesComputePerInst[alu_id]) = (*ALU_CyclesCompute[alu_id]) / (*ALU_NumInsts[alu_id]);
        (*ALU_AvgCyclesSPDReadAccessPerInst[alu_id]) = (*ALU_CyclesSPDReadAccess[alu_id]) / (*ALU_NumInsts[alu_id]);
        (*ALU_AvgCyclesSPDWriteAccessPerInst[alu_id]) = (*ALU_CyclesSPDWriteAccess[alu_id]) / (*ALU_NumInsts[alu_id]);
        (*ALU_AvgNumTakenWordsPerComparedWords[alu_id]) = (*ALU_NumTakenWords[alu_id]) / (*ALU_NumComparedWords[alu_id]);

        (*ALU_AvgCyclesComputePerInst[alu_id]).flags(statistics::nozero | statistics::nonan);
        (*ALU_AvgCyclesSPDReadAccessPerInst[alu_id]).flags(statistics::nozero | statistics::nonan);
        (*ALU_AvgCyclesSPDWriteAccessPerInst[alu_id]).flags(statistics::nozero | statistics::nonan);
        (*ALU_AvgNumTakenWordsPerComparedWords[alu_id]).flags(statistics::nozero | statistics::nonan);
    }
    INV_NumInvalidatedCachelines = new statistics::Scalar(this, MAKE_INVALIDATOR_STAT_NAME("INV_NumInvalidatedCachelines"), statistics::units::Count::get(), "number of invalidated cachelines");
    INV_AvgInvalidatedCachelinesPerInst = new statistics::Formula(this, MAKE_INVALIDATOR_STAT_NAME("INV_AvgInvalidatedCachelinesPerInst"), statistics::units::Count::get(), "average number of invalidated cachelines per instruction");

    (*INV_NumInvalidatedCachelines).flags(statistics::nozero);
    (*INV_AvgInvalidatedCachelinesPerInst) = (*INV_NumInvalidatedCachelines) / numInst_INV;
    (*INV_AvgInvalidatedCachelinesPerInst).flags(statistics::nozero | statistics::nonan);
}
} // namespace gem5
