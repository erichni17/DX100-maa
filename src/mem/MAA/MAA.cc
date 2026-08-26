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
#include "mem/MAA/DirectRetirementPortDomain.hh"
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
      logical_tile_page_scheduler(p.logical_tile_page_scheduler),
      page_fed_soa_jit(p.page_fed_soa_jit),
      page_materialization_wakeup_batches(
          p.page_materialization_wakeup_batches),
      page_materialization_fragment_buffers(
          p.page_materialization_fragment_buffers),
      page_materialization_direct_spd_fragments(
          p.page_materialization_direct_spd_fragments),
      inactive_page_payload_capture_lines(
          p.inactive_page_payload_capture_lines),
      inactive_page_masked_fragment_retention_lines(
          p.inactive_page_masked_fragment_retention_lines),
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
      virtual_page_ordered_combiner_drain(
          p.virtual_page_ordered_combiner_drain),
      virtual_combine_banks(p.virtual_combine_banks),
      virtual_response_slots(p.virtual_response_slots),
      virtual_response_words(p.virtual_response_words),
      virtual_response_word_pool(p.virtual_response_word_pool),
      virtual_words_per_cycle(p.virtual_words_per_cycle),
      virtual_max_outstanding_writes(p.virtual_max_outstanding_writes),
      virtual_masked_writes(p.virtual_masked_writes),
      virtual_idealized_write_ack(p.virtual_idealized_write_ack),
      direct_retirement_line_handoff(p.direct_retirement_line_handoff),
      soa_jit_predicate_active_credits(
          p.soa_jit_predicate_active_credits),
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
      soa_jit_active_contexts(p.soa_jit_active_contexts),
      soa_jit_old_result_partial_credits(
          p.soa_jit_old_result_partial_credits),
      soa_jit_old_result_dense_pressure(
          p.soa_jit_old_result_pressure_policy == "densest"),
      soa_jit_value_lookahead(p.soa_jit_value_lookahead),
      soa_jit_value_cache_enable(p.soa_jit_value_cache_enable),
      soa_jit_pre_a_value_lookahead(p.soa_jit_pre_a_value_lookahead),
      soa_jit_value_prefetch_credits(p.soa_jit_value_prefetch_credits),
      soa_jit_active_value_owners(p.soa_jit_active_value_owners),
      soa_jit_apply_lanes(p.soa_jit_apply_lanes),
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
      pageMaterializationEvent(
          [this] { servicePageMaterialization(); }, name()),
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
    if (logical_tile_page_scheduler) {
        panic_if(num_tile_elements != LogicalPageScheduler::LogicalElements ||
                     physical_tile_elements !=
                         LogicalPageScheduler::ElementsPerPage,
                 "Logical tile page scheduler requires 16384 logical and "
                 "4096 physical elements, got %u/%u\n",
                 num_tile_elements, physical_tile_elements);
        panic_if(num_tiles % num_maas != 0 ||
                     num_tiles / num_maas <
                         LogicalPageScheduler::PhysicalFrames *
                             LogicalPageScheduler::MaxFrameLaneSpan,
                 "Logical tile page scheduler needs at least eight existing "
                 "SPD lane IDs per MAA, got %u tiles across %u MAAs\n",
                 num_tiles, num_maas);
    }
    panic_if(soa_jit_predicate_active_credits != 1 &&
                 soa_jit_predicate_active_credits != 4 &&
                 soa_jit_predicate_active_credits != 8 &&
                 soa_jit_predicate_active_credits != 16,
             "SoA/JIT predicate credits must be one of 1/4/8/16, got %u\n",
             soa_jit_predicate_active_credits);
    panic_if(soa_jit_old_result_partial_credits != 1 &&
                 soa_jit_old_result_partial_credits != 2 &&
                 soa_jit_old_result_partial_credits != 4 &&
                 soa_jit_old_result_partial_credits != 8,
             "SoA/JIT old-result partial credits must be one of 1/2/4/8, "
             "got %u\n",
             soa_jit_old_result_partial_credits);
    panic_if(p.soa_jit_old_result_pressure_policy != "original_oldest" &&
                 p.soa_jit_old_result_pressure_policy != "densest",
             "SoA/JIT old-result pressure policy must be original_oldest or "
             "densest, got '%s'\n",
             p.soa_jit_old_result_pressure_policy.c_str());
    panic_if(page_materialization_wakeup_batches >
                 HybridConsumerPipeline::MaxEarlyWakeupBatches,
             "Page materialization wakeup batches %u exceed maximum %u\n",
             page_materialization_wakeup_batches,
             HybridConsumerPipeline::MaxEarlyWakeupBatches);
    panic_if(page_materialization_fragment_buffers >
                 HybridConsumerPipeline::
                     MaxMaterializationFragmentBuffers,
             "Page materialization fragment buffers %u exceed maximum %u\n",
             page_materialization_fragment_buffers,
             HybridConsumerPipeline::MaxMaterializationFragmentBuffers);
    panic_if(!InactiveProducerLinePayloadCapture::validCapacity(
                 inactive_page_payload_capture_lines),
             "Inactive producer payload capture capacity %u must be zero or "
             "one of 64/128/256/%u\n",
             inactive_page_payload_capture_lines,
             InactiveProducerLinePayloadCapture::MaxEntries);
    panic_if(p.inactive_page_payload_capture_conflict_policy != "first-owner",
             "Inactive producer payload capture conflict policy '%s' must be "
             "first-owner; latest-owner is not supported\n",
             p.inactive_page_payload_capture_conflict_policy.c_str());
    panic_if(inactive_page_payload_capture_lines != 0 &&
                 !direct_retirement_line_handoff,
             "Inactive producer payload capture requires exact producer "
             "WriteResp line handoff\n");
    panic_if(!InactiveProducerMaskedFragmentRetention::validCapacity(
                 inactive_page_masked_fragment_retention_lines),
             "Inactive masked-fragment retention capacity %u must be zero "
             "or one of 512/1024/2048/4096\n",
             inactive_page_masked_fragment_retention_lines);
    panic_if(inactive_page_masked_fragment_retention_lines != 0 &&
                 !direct_retirement_line_handoff,
             "Inactive masked-fragment retention requires exact producer "
             "WriteResp line handoff\n");
    panic_if(inactive_page_masked_fragment_retention_lines != 0 &&
                 inactive_page_payload_capture_lines != 0,
             "Inactive full-line payload capture and masked-fragment "
             "retention are mutually exclusive\n");
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
    virtualPagePayloadIncarnation.assign(num_tiles, 0);
    virtualPageConsumedGeneration.assign(num_tiles, 0);
    virtualPageBackingAddr.assign(num_tiles, 0);
    virtualPageBackingRangeID.assign(num_tiles, -1);
    virtualPageWordSize.assign(num_tiles, 0);
    virtualProducerRegistrationTick.assign(num_tiles, 0);
    virtualPageLastReadyTick.assign(num_tiles, 0);
    responseBearingPublishCompletionOwner.assign(num_tiles, -1);
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
    logicalPageDescriptors.resize(num_maas);
    logicalPageExecutions.resize(num_maas);
    if (logical_tile_page_scheduler) {
        logicalPageSchedulers.reserve(num_maas);
        for (unsigned maaID = 0; maaID < num_maas; ++maaID) {
            logicalPageSchedulers.push_back(
                std::make_unique<LogicalPageScheduler>(
                    logicalPageFrameIDs(maaID)));
        }
    }
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
    for (PendingPageZeroPrearm &pending : pendingPageZeroPrearms)
        delete pending.instruction;
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
                                        soa_jit_predicate_active_credits,
                                        virtual_index_buffer_lines,
                                        virtual_index_force_cache,
                                        virtual_index_partitions,
                                        virtual_index_filter_words_per_cycle,
                                        soa_jit_active_contexts,
                                        soa_jit_value_lookahead,
                                        soa_jit_value_cache_enable,
                                        soa_jit_pre_a_value_lookahead,
                                        soa_jit_value_prefetch_credits,
                                        soa_jit_active_value_owners,
                                        soa_jit_apply_lanes,
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
    if (std::any_of(
            pendingPageZeroPrearms.begin(), pendingPageZeroPrearms.end(),
            [](const PendingPageZeroPrearm &pending) {
                return pending.instruction != nullptr;
            }))
        return false;
    if (directRetirementContexts.activeContexts() != 0)
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
bool MAA::hasNonStreamActivity(int streamID) const {
    panic_if(streamID < 0 || streamID >= num_maas,
             "invalid stream unit %d for overlap accounting\n", streamID);
    if (!aluUnitsIdle[streamID] || !rangeUnitsIdle[streamID])
        return true;
    const int first_indirect = streamID * num_indirect_units_per_maa;
    for (unsigned int lane = 0; lane < num_indirect_units_per_maa; ++lane) {
        if (!indirectAccessIdle[first_indirect + lane])
            return true;
    }
    return false;
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
                            const int publisher_completion =
                                StreamAccessUnit::
                                    responseBearingPublishCompletionTile(
                                        inst);
                            if (publisher_completion != -1 &&
                                !inst->logicalPageManaged) {
                                spd->setTileService(
                                    publisher_completion,
                                    getInstructionTileWordSize(
                                        inst, publisher_completion));
                            } else if (inst->dst1SpdID != -1) {
                                spd->setTileService(
                                    inst->dst1SpdID,
                                    inst->getWordSize(inst->dst1SpdID));
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
int
MAA::getInstructionTileWordSize(InstructionPtr instruction, int tile_id)
{
    panic_if(instruction == nullptr || tile_id == -1,
             "Cannot classify an absent instruction tile\n");

    // IF deliberately retains the legacy STREAM_ST ABI, in which dst1 is
    // absent.  The guarded response-bearing form uses dst1 only as its
    // completion token, with the same width as the published source.
    if (StreamAccessUnit::isResponseBearingPublishInstruction(instruction) &&
        tile_id == StreamAccessUnit::responseBearingPublishCompletionTile(
                       instruction)) {
        return instruction->WordSize();
    }
    return instruction->getWordSize(tile_id);
}

bool
MAA::responseBearingPublishDestinationBusy(int maa_id, int first_tile,
                                           int word_size) const
{
    const int tile_words = word_size / sizeof(uint32_t);
    panic_if(maa_id < 0 || maa_id >= static_cast<int>(num_maas) ||
                 first_tile < 0 ||
                 first_tile + tile_words > static_cast<int>(num_tiles),
             "Invalid publisher completion span maa=%d tile=%d words=%d\n",
             maa_id, first_tile, tile_words);
    for (int offset = 0; offset < tile_words; ++offset) {
        if (logicalCompletionLaneOwned(first_tile + offset))
            return true;
    }
    return false;
}

void
MAA::reserveResponseBearingPublishCompletion(int maa_id, int first_tile,
                                             int word_size)
{
    panic_if(responseBearingPublishDestinationBusy(
                 maa_id, first_tile, word_size),
             "Publisher completion span is already reserved\n");
    const int tile_words = word_size / sizeof(uint32_t);
    for (int offset = 0; offset < tile_words; ++offset)
        responseBearingPublishCompletionOwner[first_tile + offset] = maa_id;
}

void
MAA::releaseResponseBearingPublishCompletion(int maa_id, int first_tile,
                                             int word_size)
{
    const int tile_words = word_size / sizeof(uint32_t);
    panic_if(first_tile < 0 ||
                 first_tile + tile_words > static_cast<int>(num_tiles),
             "Invalid publisher completion release tile=%d words=%d\n",
             first_tile, tile_words);
    for (int offset = 0; offset < tile_words; ++offset) {
        panic_if(responseBearingPublishCompletionOwner[
                     first_tile + offset] != maa_id,
                 "Publisher completion release lost owner maa=%d tile=%d\n",
                 maa_id, first_tile + offset);
        responseBearingPublishCompletionOwner[first_tile + offset] = -1;
    }
}
uint8_t
MAA::getTileStatus(InstructionPtr instruction, int tile_id, bool is_dst)
{
    if (tile_id == -1)
        return (uint8_t)(Instruction::TileStatus::Finished);

    bool is_dirty = spd->getTileDirty(tile_id);
    SPD::TileStatus status = spd->getTileStatus(tile_id);
    if (getInstructionTileWordSize(instruction, tile_id) == 8) {
        if (spd->getTileDirty(tile_id + 1)) {
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
MAA::sameDirectRetirementKey(
    const HybridConsumerContextQueue::ContextKey &lhs,
    const HybridConsumerContextQueue::ContextKey &rhs)
{
    return lhs.tokenTile == rhs.tokenTile &&
        lhs.generation == rhs.generation &&
        lhs.incarnation == rhs.incarnation;
}

bool
MAA::sameDirectRetirementRequest(
    const HybridConsumerContextQueue::Request &lhs,
    const HybridConsumerContextQueue::Request &rhs)
{
    const auto &left = lhs.request;
    const auto &right = rhs.request;
    return sameDirectRetirementKey(lhs.owner, rhs.owner) &&
        left.kind != HybridConsumerPipeline::Kind::None &&
        left.kind == right.kind && left.line == right.line &&
        left.buffer == right.buffer && left.port == right.port &&
        left.address == right.address && left.size == right.size &&
        left.transactionID == right.transactionID;
}

MAA::DirectRetirementExecution *
MAA::findDirectRetirementExecution(
    const HybridConsumerContextQueue::ContextKey &key)
{
    for (DirectRetirementExecution &execution : directRetirementExecutions) {
        if (execution.active && sameDirectRetirementKey(execution.key, key))
            return &execution;
    }
    return nullptr;
}

const MAA::DirectRetirementExecution *
MAA::findDirectRetirementExecution(
    const HybridConsumerContextQueue::ContextKey &key) const
{
    for (const DirectRetirementExecution &execution :
         directRetirementExecutions) {
        if (execution.active && sameDirectRetirementKey(execution.key, key))
            return &execution;
    }
    return nullptr;
}

MAA::DirectRetirementExecution *
MAA::findDirectRetirementExecution(uint16_t tokenTile, uint64_t generation)
{
    for (DirectRetirementExecution &execution : directRetirementExecutions) {
        if (execution.active && execution.key.tokenTile == tokenTile &&
            execution.key.generation == generation)
            return &execution;
    }
    return nullptr;
}

MAA::DirectRetirementExecution *
MAA::firstInactiveDirectRetirementExecution()
{
    for (DirectRetirementExecution &execution : directRetirementExecutions) {
        if (!execution.active)
            return &execution;
    }
    return nullptr;
}

MAA::PageMaterializationExecution *
MAA::findPageMaterializationExecution(
    const HybridConsumerContextQueue::ContextKey &key)
{
    for (PageMaterializationExecution &execution :
         pageMaterializationExecutions) {
        if (execution.active && sameDirectRetirementKey(execution.key, key))
            return &execution;
    }
    return nullptr;
}

MAA::PageMaterializationExecution *
MAA::findPageMaterializationExecution(uint16_t tokenTile,
                                      uint64_t generation)
{
    for (PageMaterializationExecution &execution :
         pageMaterializationExecutions) {
        if (execution.active && execution.key.tokenTile == tokenTile &&
            execution.key.generation == generation)
            return &execution;
    }
    return nullptr;
}

MAA::PageMaterializationExecution *
MAA::firstInactivePageMaterializationExecution()
{
    for (PageMaterializationExecution &execution :
         pageMaterializationExecutions) {
        if (!execution.active)
            return &execution;
    }
    return nullptr;
}

bool
MAA::hasDirectRetirementOutstandingAddress(Addr address) const
{
    for (const DirectRetirementRequestRecord &record :
         directRetirementRequestRecords) {
        if (record.active && record.address == address)
            return true;
    }
    return false;
}

bool
MAA::hasDirectRetirementOutstandingOwner(
    const HybridConsumerContextQueue::ContextKey &key) const
{
    for (const DirectRetirementRequestRecord &record :
         directRetirementRequestRecords) {
        if (record.active && sameDirectRetirementKey(record.request.owner,
                                                      key))
            return true;
    }
    return false;
}

uint16_t
MAA::directRetirementOutstandingRequestCount() const
{
    uint16_t count = 0;
    for (const DirectRetirementRequestRecord &record :
         directRetirementRequestRecords)
        count += record.active;
    return count;
}

bool
MAA::reserveDirectRetirementRequest(
    Addr address, const HybridConsumerContextQueue::Request &request)
{
    if (request.request.kind == HybridConsumerPipeline::Kind::None ||
        hasDirectRetirementOutstandingAddress(address))
        return false;
    for (DirectRetirementRequestRecord &record :
         directRetirementRequestRecords) {
        if (record.active)
            continue;
        record.active = true;
        record.address = address;
        record.request = request;
        return true;
    }
    return false;
}

bool
MAA::releaseDirectRetirementRequest(
    Addr address, const HybridConsumerContextQueue::Request &request)
{
    for (DirectRetirementRequestRecord &record :
         directRetirementRequestRecords) {
        if (!record.active || record.address != address ||
            !sameDirectRetirementRequest(record.request, request))
            continue;
        record = {};
        return true;
    }
    return false;
}

bool
MAA::isTokenBoundPageMaterialization(InstructionPtr instruction) const
{
    return instruction != nullptr &&
        instruction->opcode == Instruction::OpcodeType::STREAM_LD &&
        instruction->src1SpdID >= 0 &&
        instruction->src2SpdID == -1 &&
        instruction->src1SpdID < static_cast<int>(num_tiles) &&
        ifile->isCompletionOnlyTile(instruction->maa_id,
                                    instruction->src1SpdID);
}

bool
MAA::isPageZeroPrearmMaterialization(InstructionPtr instruction) const
{
    if (instruction == nullptr ||
        instruction->opcode != Instruction::OpcodeType::STREAM_LD ||
        instruction->src1SpdID < 0 || instruction->dst1SpdID < 0 ||
        instruction->src2SpdID != instruction->src1SpdID ||
        instruction->condSpdID != -1 ||
        instruction->datatype != Instruction::DataType::FLOAT64_TYPE ||
        instruction->src1RegID < 0 || instruction->src2RegID < 0 ||
        instruction->src3RegID < 0 ||
        rf->getData<int>(instruction->src1RegID) != 0 ||
        rf->getData<int>(instruction->src2RegID) !=
            static_cast<int>(HybridConsumerPipeline::ProducerPageElements) ||
        rf->getData<int>(instruction->src3RegID) != 1)
        return false;

    // Before registration, the duplicated token is an ABI-level dormant
    // marker, not a usable stream dependency.  It cannot enter the IF or
    // issue traffic.  Registration itself is performed only by the virtual
    // gather opcodes and subsequently binds this marker to their token,
    // backing address, datatype, and the local page-zero range above.
    if (virtualPageGeneration[instruction->src1SpdID] == 0)
        return true;
    return virtualPageBackingAddr[instruction->src1SpdID] ==
               instruction->baseAddr &&
           virtualPageBackingRangeID[instruction->src1SpdID] ==
               instruction->addrRangeID &&
           virtualPageWordSize[instruction->src1SpdID] ==
               instruction->WordSize();
}

bool
MAA::pageMaterializerOwnsDestination(int maaID, int firstTile,
                                     int wordBytes) const
{
    if (firstTile < 0)
        return false;
    const int words = wordBytes / sizeof(uint32_t);
    for (const PageMaterializationExecution &execution :
         pageMaterializationExecutions) {
        if (!execution.active || !execution.pageActive ||
            execution.maaID != maaID)
            continue;
        const int ownedWords = execution.wordBytes / sizeof(uint32_t);
        if (firstTile < execution.destinationTile + ownedWords &&
            execution.destinationTile < firstTile + words)
            return true;
    }
    return false;
}

bool
MAA::queuePageZeroPrearm(InstructionPtr instruction)
{
    panic_if(!isPageZeroPrearmMaterialization(instruction) ||
                 virtualPageGeneration[instruction->src1SpdID] != 0,
             "Cannot queue an invalid or already-bound page-zero prearm\n");
    for (const PendingPageZeroPrearm &pending : pendingPageZeroPrearms) {
        panic_if(pending.instruction != nullptr &&
                     pending.instruction->src1SpdID ==
                         instruction->src1SpdID,
                 "Duplicate page-zero prearm for token %d\n",
                 instruction->src1SpdID);
    }
    for (PendingPageZeroPrearm &pending : pendingPageZeroPrearms) {
        if (pending.instruction != nullptr)
            continue;
        pending.instruction = instruction;
        return true;
    }
    return false;
}

void
MAA::activatePendingPageZeroPrearms()
{
    for (PendingPageZeroPrearm &pending : pendingPageZeroPrearms) {
        InstructionPtr instruction = pending.instruction;
        if (instruction == nullptr ||
            virtualPageGeneration[instruction->src1SpdID] == 0)
            continue;
        const PageMaterializationSubmit submitted =
            submitPageMaterialization(instruction);
        if (submitted == PageMaterializationSubmit::Retry)
            continue;
        panic_if(submitted != PageMaterializationSubmit::Accepted,
                 "Bound page-zero prearm failed exact admission for token "
                 "%d\n",
                 instruction->src1SpdID);
        DPRINTF(MAAVirtualTrace,
                "event=page_materialization_prearm_activate schema=1 "
                "occurrence=%lu token=%d generation=%lu destination=%d\n",
                pageMaterializationTraceOccurrence++,
                instruction->src1SpdID,
                virtualPageGeneration[instruction->src1SpdID],
                instruction->dst1SpdID);
        delete instruction;
        pending.instruction = nullptr;
    }
}

MAA::PageMaterializationSubmit
MAA::submitPageMaterialization(InstructionPtr instruction)
{
    panic_if(!isTokenBoundPageMaterialization(instruction) &&
                 !isPageZeroPrearmMaterialization(instruction),
             "Page materializer requires an exact token-bound STREAM_LD\n");
    const int token = instruction->src1SpdID;
    const int wordBytes = instruction->WordSize();
    const int tileWords = wordBytes / sizeof(uint32_t);
    const int minimum = rf->getData<int>(instruction->src1RegID);
    const int maximum = rf->getData<int>(instruction->src2RegID);
    const int stride = rf->getData<int>(instruction->src3RegID);
    const Addr rootBackingAddress = virtualPageBackingAddr[token];
    const auto fallback = [this, instruction, token, wordBytes, minimum,
                           maximum, stride, rootBackingAddress](
                              const char *reason) {
        stats.page_materialization_admission_fallbacks++;
        DPRINTF(MAAVirtualTrace,
                "event=page_materialization_fallback schema=1 "
                "occurrence=%lu token=%d generation=%lu reason=%s "
                "base=0x%lx root=0x%lx minimum=%d maximum=%d stride=%d "
                "word_bytes=%d destination=%d active_contexts=%u\n",
                pageMaterializationTraceOccurrence++, token,
                virtualPageGeneration[token], reason,
                instruction->baseAddr, rootBackingAddress, minimum, maximum,
                stride, wordBytes, instruction->dst1SpdID,
                directRetirementContexts.activeContexts(
                    HybridConsumerPipeline::Mode::MaterializePages));
        return PageMaterializationSubmit::Fallback;
    };
    const bool staticGeometry =
        direct_retirement_line_handoff &&
        DirectRetirementPortDomain::eligible(num_cores,
                                              cacheSidePorts.size()) &&
        HybridConsumerPipeline::materializationPayloadCapacityEligible(
            num_tile_elements, physical_tile_elements) &&
        (wordBytes == 4 || wordBytes == 8) && minimum == 0 &&
        maximum ==
            static_cast<int>(HybridConsumerPipeline::ProducerPageElements) &&
        stride == 1 && instruction->condSpdID == -1 &&
        instruction->dst1SpdID >= 0 &&
        instruction->dst1SpdID + tileWords <=
            static_cast<int>(num_tiles) &&
        (instruction->dst1SpdID + tileWords <= token ||
         token + tileWords <= instruction->dst1SpdID);
    uint8_t page = HybridConsumerPipeline::NoProducerPage;
    const auto admission =
        HybridConsumerPipeline::classifyMaterializationAdmission(
            staticGeometry, virtualPageGeneration[token],
            rootBackingAddress, instruction->baseAddr, minimum, maximum,
            stride, wordBytes, &page);
    if (admission ==
        HybridConsumerPipeline::MaterializationAdmission::Fallback)
        return fallback(staticGeometry ? "abi" : "static_geometry");
    if (admission ==
        HybridConsumerPipeline::MaterializationAdmission::Retry) {
        DPRINTF(MAAVirtualTrace,
                "event=page_materialization_activation_retry schema=1 "
                "occurrence=%lu token=%d reason=producer_unregistered "
                "activation_count=%lu\n",
                pageMaterializationTraceOccurrence++, token,
                pageMaterializationActivationCount);
        return PageMaterializationSubmit::Retry;
    }
    const bool exactGeometry =
        rootBackingAddress % HybridConsumerPipeline::LineBytes == 0 &&
        virtualPageWordSize[token] == wordBytes &&
        virtualPageGeneration[token] != 0;
    if (!exactGeometry)
        return fallback("registered_producer_abi");

    const uint64_t generation = virtualPageGeneration[token];
    PageMaterializationExecution *execution =
        findPageMaterializationExecution(token, generation);
    bool newContext = false;
    EarlyProducerLineReadinessLedger::ReplaySummary replay;
    if (execution == nullptr) {
        HybridConsumerContextQueue::ContextKey existing;
        if (directRetirementContexts.findGeneration(token, generation,
                                                    &existing))
            return PageMaterializationSubmit::Retry;
        execution = firstInactivePageMaterializationExecution();
        if (execution == nullptr)
            return fallback("resource_execution_context");

        HybridConsumerContextQueue::Descriptor queueDescriptor;
        queueDescriptor.tokenTile = token;
        auto &descriptor = queueDescriptor.consumer;
        descriptor.mode = HybridConsumerPipeline::Mode::MaterializePages;
        descriptor.generation = generation;
        descriptor.logicalElements = HybridConsumerPipeline::LogicalElements;
        descriptor.wordBytes = wordBytes;
        descriptor.backingAddress = rootBackingAddress;
        descriptor.backingRangeMin = instruction->minAddr;
        descriptor.backingRangeMax = instruction->maxAddr;
        descriptor.backingRangeID = instruction->addrRangeID;
        for (uint8_t readyPage = 0;
             readyPage < HybridConsumerPipeline::ProducerPages;
             ++readyPage) {
            if (getVirtualPageReady(token, readyPage))
                descriptor.producerTransactions[readyPage] =
                    getVirtualPageReadyTransaction(token, readyPage);
        }
        HybridConsumerContextQueue::ContextKey owner;
        const auto submit = directRetirementContexts.submit(queueDescriptor,
                                                             &owner);
        if (submit == HybridConsumerContextQueue::SubmitResult::Full)
            return fallback("resource_context_queue");
        if (submit != HybridConsumerContextQueue::SubmitResult::Accepted)
            return PageMaterializationSubmit::Retry;

        const EarlyProducerLineReadinessLedger::Key earlyKey{
            static_cast<uint16_t>(token), generation,
            descriptor.backingAddress};
        if (directRetirementEarlyLineLedger.active(earlyKey)) {
            panic_if(!directRetirementEarlyLineLedger.replay(
                         earlyKey,
                         [this, &owner, generation](const auto &ack) {
                             return directRetirementContexts.
                                 notifyProducerLineWriteAck(
                                     owner,
                                     {generation, ack.line, ack.wordMask,
                                      ack.transactionID});
                         },
                         &replay) ||
                         !directRetirementEarlyLineLedger.clear(earlyKey),
                     "Page materializer rejected pre-admission readiness\n");
        }
        for (uint8_t readyPage = 0;
             readyPage < HybridConsumerPipeline::ProducerPages;
             ++readyPage) {
            if (!getVirtualPageReady(token, readyPage))
                continue;
            panic_if(!directRetirementContexts.notifyProducerWriteAck(
                         owner,
                         {generation, readyPage,
                          getVirtualPageReadyTransaction(token, readyPage)}),
                     "Page materializer rejected acknowledged page %u\n",
                     readyPage);
        }
        *execution = {};
        execution->active = true;
        execution->key = owner;
        execution->coreID = instruction->core_id;
        execution->maaID = instruction->maa_id;
        execution->wordBytes = wordBytes;
        execution->backingAddress = rootBackingAddress;
        execution->backingRangeID = instruction->addrRangeID;
        execution->contextID = instruction->CID;
        execution->pc = instruction->PC;
        newContext = true;
    }

    if (execution->pageActive)
        return PageMaterializationSubmit::Retry;
    if (execution->coreID != instruction->core_id ||
        execution->maaID != instruction->maa_id ||
        execution->wordBytes != wordBytes ||
        execution->backingAddress != rootBackingAddress ||
        execution->backingRangeID != instruction->addrRangeID ||
        execution->contextID != instruction->CID)
        return fallback("abi_context_identity");
    if (directRetirementContexts.materializationPageComplete(execution->key,
                                                              page))
        return fallback("abi_page_already_materialized");
    if (pageMaterializerOwnsDestination(instruction->maa_id,
                                        instruction->dst1SpdID,
                                        wordBytes))
        return PageMaterializationSubmit::Retry;
    for (int offset = 0; offset < tileWords; ++offset) {
        if (ifile->hasTileReference(instruction->maa_id,
                                    instruction->dst1SpdID + offset))
            return PageMaterializationSubmit::Retry;
    }
    panic_if(!directRetirementContexts.beginMaterializationPage(
                 execution->key, page),
             "Page materializer could not start page %u\n", page);
    execution->pageActive = true;
    execution->page = page;
    execution->destinationTile = instruction->dst1SpdID;
    execution->stagedWords.reset();
    execution->stagedDisallowed.reset();
    execution->stagedFallbackCounted.reset();
    // An ACK predating physical-page activation has no retained exact
    // payload.  It is irrevocably coherent-read fallback, even if a later
    // fragment happens to complete the line.
    const uint16_t pageLines = directRetirementContexts.producerPageLines(
        execution->key);
    for (uint16_t pageLine = 0; pageLine < pageLines; ++pageLine) {
        const uint16_t line = page * pageLines + pageLine;
        if (directRetirementContexts.producerLineWordMask(execution->key,
                                                           line) != 0)
            execution->stagedDisallowed.set(pageLine);
    }
    spd->setTileIdle(execution->destinationTile, wordBytes);
    spd->setTileNotReady(execution->destinationTile, wordBytes);
    spd->setTileService(execution->destinationTile, wordBytes);
    spd->setSize(execution->destinationTile,
                 HybridConsumerPipeline::ProducerPageElements);
    const uint64_t materializerControlBytes =
        HybridConsumerContextQueue::chargedControlBytes() +
        sizeof(pageMaterializationExecutions) +
        sizeof(pendingPageZeroPrearms) +
        sizeof(pageMaterializationCommits) +
        sizeof(directRetirementRequestRecords) +
        DirectRetirementPortRetry<Packet>::chargedControlBytes() +
        EarlyProducerLineReadinessLedger::chargedTotalBytes() +
        InactiveProducerLinePayloadCapture::provisionedCombinedTotalBytes(
            inactive_page_payload_capture_lines, num_tiles) +
        InactiveProducerMaskedFragmentRetention::
            provisionedCombinedTotalBytes(
                inactive_page_masked_fragment_retention_lines, num_tiles);
    DPRINTF(MAAVirtualTrace,
            "event=page_materialization_submit schema=1 occurrence=%lu "
            "token=%d generation=%lu incarnation=%lu page=%u "
            "payload_incarnation=%lu "
            "destination=%d new_context=%d early_lines=%u "
            "line_buffer_bytes=%lu control_bytes=%lu "
            "inactive_payload_capacity=%u inactive_payload_bytes=%lu "
            "inactive_payload_tag_control_bytes=%lu "
            "inactive_payload_read_pipeline_payload_bytes=%lu "
            "inactive_payload_lookup_latch_control_bytes=%lu "
            "inactive_payload_persistent_incarnation_bits=%lu "
            "inactive_payload_hardware_total_bits=%lu "
            "inactive_payload_write_ports=%u inactive_payload_read_ports=%u "
            "inactive_payload_conflict_policy=%s "
            "inactive_payload_port_access_cycles=%u "
            "inactive_payload_port_time_unit=maa_cycles "
            "direct_stage_control_bytes=%lu page_spd_bytes=%lu "
            "charged_two_page_spd_bytes=%lu activation_count=%lu\n",
            pageMaterializationTraceOccurrence++, token, generation,
            execution->key.incarnation, page,
            virtualPagePayloadIncarnation[token],
            execution->destinationTile,
            newContext, replay.readyLines,
            HybridConsumerContextQueue::chargedPayloadBytes(),
            materializerControlBytes,
            inactive_page_payload_capture_lines,
            InactiveProducerLinePayloadCapture::provisionedPayloadBytes(
                inactive_page_payload_capture_lines),
            InactiveProducerLinePayloadCapture::provisionedControlBytes(
                inactive_page_payload_capture_lines),
            InactiveProducerLinePayloadCapture::
                provisionedReadPipelinePayloadBytes(
                    inactive_page_payload_capture_lines),
            inactive_page_payload_capture_lines == 0
                ? 0 : InactiveProducerLinePayloadCapture::bitsToBytes(
                    InactiveProducerLinePayloadCapture::
                        MAALookupControlBits),
            InactiveProducerLinePayloadCapture::
                provisionedMAAPersistentStateBits(
                    inactive_page_payload_capture_lines, num_tiles),
            InactiveProducerLinePayloadCapture::provisionedCombinedTotalBits(
                inactive_page_payload_capture_lines, num_tiles),
            InactiveProducerLinePayloadCapture::WritePortCount,
            InactiveProducerLinePayloadCapture::ReadPortCount,
            InactiveProducerLinePayloadCapture::conflictPolicyName(),
            InactiveProducerLinePayloadCapture::PortAccessCycles,
            sizeof(execution->stagedWords) +
                sizeof(execution->stagedDisallowed) +
                sizeof(execution->stagedFallbackCounted),
            static_cast<uint64_t>(physical_tile_elements) * wordBytes,
            static_cast<uint64_t>(2) * physical_tile_elements * wordBytes,
            ++pageMaterializationActivationCount);
    stats.page_materialization_submissions++;
    schedulePageMaterializationEvent();
    return PageMaterializationSubmit::Accepted;
}

bool
MAA::submitDirectRetirementDescriptor(InstructionPtr instruction)
{
    panic_if(instruction->opcode !=
                 Instruction::OpcodeType::VIRTUAL_TILE_ALU_SCALAR,
             "Cannot submit invalid direct-retirement descriptor\n");
    if (transparentController.active())
        return false;
    const int word_size = instruction->WordSize();
    const int tile_words = word_size / sizeof(uint32_t);
    const auto valid_tile_span = [&](int first) {
        return first >= 0 &&
            first + tile_words <= static_cast<int>(num_tiles);
    };
    const bool direct_eligible =
        DirectRetirementPortDomain::eligible(num_cores,
                                              cacheSidePorts.size()) &&
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
        // The serial fallback shares the same existing ALU/cache machinery.
        // Do not overlap it with live direct contexts or manufacture a fifth
        // context; leave the instruction queued until the finite set drains.
        if (directRetirementContexts.activeContexts() != 0)
            return false;
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

    HybridConsumerContextQueue::Descriptor queue_descriptor;
    queue_descriptor.tokenTile = token_tile;
    auto &descriptor = queue_descriptor.consumer;
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
        if (directRetirementContexts.activeContexts() != 0)
            return false;
        stats.direct_retirement_fallbacks++;
        DPRINTF(MAAVirtualTrace,
                "event=direct_retirement_fallback schema=1 occurrence=%lu "
                "reason=%s source=0x%lx destination=0x%lx\n",
                directRetirementTraceOccurrence++, validation,
                descriptor.backingAddress, descriptor.destinationAddress);
        return submitTransparentDescriptor(instruction, true);
    }

    const auto spans_overlap = [tile_words](int lhs, int rhs) {
        return lhs < rhs + tile_words && rhs < lhs + tile_words;
    };
    for (const DirectRetirementExecution &live :
         directRetirementExecutions) {
        if (live.active && spans_overlap(live.completionTile,
                                         instruction->dst2SpdID))
            return false;
    }
    DirectRetirementExecution *execution_slot =
        firstInactiveDirectRetirementExecution();
    if (execution_slot == nullptr) {
        stats.direct_retirement_context_full_stalls++;
        return false;
    }
    HybridConsumerContextQueue::ContextKey owner;
    const auto submit = directRetirementContexts.submit(
        queue_descriptor, &owner);
    if (submit == HybridConsumerContextQueue::SubmitResult::Full) {
        stats.direct_retirement_context_full_stalls++;
        return false;
    }
    if (submit == HybridConsumerContextQueue::SubmitResult::Duplicate ||
        submit == HybridConsumerContextQueue::SubmitResult::Exhausted)
        return false;
    panic_if(submit != HybridConsumerContextQueue::SubmitResult::Accepted,
             "Validated direct-retirement descriptor was not accepted\n");

    const EarlyProducerLineReadinessLedger::Key early_key{
        static_cast<uint16_t>(token_tile), descriptor.generation,
        descriptor.backingAddress};
    EarlyProducerLineReadinessLedger::ReplaySummary early_replay;
    if (directRetirementEarlyLineLedger.active(early_key)) {
        panic_if(!directRetirementEarlyLineLedger.replay(
                     early_key,
                     [this, &owner, &descriptor](
                         const EarlyProducerLineReadinessLedger::LineAck
                             &early_ack) {
                         return directRetirementContexts.
                             notifyProducerLineWriteAck(
                                 owner,
                                 {descriptor.generation, early_ack.line,
                                  early_ack.wordMask,
                                  early_ack.transactionID});
                     },
                     &early_replay),
                 "Direct retirement rejected exact pre-admission line "
                 "readiness\n");
        panic_if(!directRetirementEarlyLineLedger.clear(early_key),
                 "Direct retirement did not consume its early-line ledger "
                 "slot\n");
        const auto payloadClear = inactiveProducerLinePayloadCapture.clear(
            {early_key.tokenTile, early_key.generation,
             virtualPagePayloadIncarnation[early_key.tokenTile],
             early_key.backingAddress});
        stats.page_materialization_inactive_payload_drops +=
            payloadClear.discardedLines;
        const auto maskedClear = inactiveMaskedFragmentRetention.clear(
            {early_key.tokenTile, early_key.generation,
             virtualPagePayloadIncarnation[early_key.tokenTile],
             early_key.backingAddress});
        if (maskedClear)
            stats.page_materialization_inactive_masked_clears++;
        stats.direct_retirement_producer_line_acks +=
            early_replay.readyLines;
    }

    DirectRetirementExecution execution;
    execution.active = true;
    execution.key = owner;
    execution.coreID = instruction->core_id;
    execution.maaID = instruction->maa_id;
    execution.completionTile = instruction->dst2SpdID;
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
    *execution_slot = std::move(execution);
    // There is no result SPD payload. Keep the existing destination tile ID
    // only as an asynchronous completion token for later dependent work.
    spd->setTileNotReady(execution_slot->completionTile, word_size);
    const uint64_t charged_payload_bytes =
        HybridConsumerContextQueue::chargedPayloadBytes();
    const uint64_t native_spd_payload_bytes =
        static_cast<uint64_t>(num_tiles) * physical_tile_elements *
        sizeof(uint32_t);
    const uint64_t producer_line_metadata_bytes =
        direct_retirement_line_handoff
        ? num_indirect_units_total * virtual_max_outstanding_writes *
              IndirectAccessUnit::lineHandoffMetadataBytesPerWrite()
        : 0;
    const uint64_t charged_control_bytes =
        HybridConsumerContextQueue::chargedControlBytes() +
        sizeof(directRetirementExecutions) +
        sizeof(directRetirementRequestRecords) +
        DirectRetirementPortRetry<Packet>::chargedControlBytes() +
        EarlyProducerLineReadinessLedger::chargedTotalBytes() +
        producer_line_metadata_bytes;
    stats.direct_retirement_descriptors++;
    stats.direct_retirement_context_high_water = std::max(
        stats.direct_retirement_context_high_water.value(),
        static_cast<double>(directRetirementContexts.activeContexts()));
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
        HybridConsumerContextQueue::Snapshot before;
        panic_if(!directRetirementContexts.snapshot(owner, &before) ||
                     !directRetirementContexts.notifyProducerWriteAck(
                         owner, {descriptor.generation, page, transaction}),
                 "Direct retirement rejected already-acknowledged producer "
                 "page %u\n", page);
        HybridConsumerContextQueue::Snapshot after;
        panic_if(!directRetirementContexts.snapshot(owner, &after),
                 "Direct retirement lost admitted context\n");
        stats.direct_retirement_producer_acks++;
        stats.direct_retirement_page_fallback_lines +=
            after.producerPageFallbackLines -
            before.producerPageFallbackLines;
        DPRINTF(MAAVirtualTrace,
                "event=direct_retirement_producer_ack schema=1 "
                "occurrence=%lu token=%u generation=%lu incarnation=%lu "
                "page=%u transaction=%lu\n",
                directRetirementTraceOccurrence++, owner.tokenTile,
                owner.generation, owner.incarnation, page, transaction);
    }
    DPRINTF(MAAVirtualTrace,
            "event=direct_retirement_submit schema=1 occurrence=%lu "
            "generation=%lu incarnation=%lu token=%d source=0x%lx "
            "destination=0x%lx scope=terminal_fp64_mul_dense_store "
            "context_credits=%u fixed_contexts=%u active_contexts=%u "
            "request_records=%lu retry_packet_slots=%u "
            "retry_packet_slot_bytes=%lu "
            "payload_bytes=%lu control_bytes=%lu total_bytes=%lu "
            "early_line_ledger_bytes=%lu early_entries_replayed=%u "
            "early_lines_replayed=%u early_ledger_overflowed=%d "
            "native_spd_payload_bytes=%lu "
            "producer_line_metadata_bytes=%lu backing_span_bytes=%lu "
            "private_page_payload_bytes=0\n",
            directRetirementTraceOccurrence++, owner.generation,
            owner.incarnation, token_tile, descriptor.backingAddress,
            descriptor.destinationAddress,
            HybridConsumerPipeline::LineBufferCount,
            HybridConsumerContextQueue::ContextCount,
            directRetirementContexts.activeContexts(),
            DirectRetirementRequestRecordCount,
            DirectRetirementPortRetry<Packet>::PortCount,
            DirectRetirementPortRetry<Packet>::chargedControlBytes(),
            charged_payload_bytes, charged_control_bytes,
            charged_payload_bytes + charged_control_bytes,
            EarlyProducerLineReadinessLedger::chargedTotalBytes(),
            early_replay.entries, early_replay.readyLines,
            early_replay.overflowed, native_spd_payload_bytes,
            producer_line_metadata_bytes,
            static_cast<uint64_t>(descriptor.logicalElements) *
                descriptor.wordBytes);
    scheduleDirectRetirementEvent();
    return true;
}

PacketPtr
MAA::makeDirectRetirementPacket(
    const HybridConsumerContextQueue::Request &request)
{
    const DirectRetirementExecution *execution =
        findDirectRetirementExecution(request.owner);
    const auto &pipeline_request = request.request;
    panic_if(execution == nullptr ||
                 (pipeline_request.kind !=
                      HybridConsumerPipeline::Kind::ReadBacking &&
                  pipeline_request.kind !=
                      HybridConsumerPipeline::Kind::WriteDestination) ||
                 pipeline_request.size != HybridConsumerPipeline::LineBytes ||
                 pipeline_request.buffer >=
                     HybridConsumerPipeline::LineBufferCount,
             "Direct retirement produced an invalid cache-line request\n");
    const int region =
        pipeline_request.kind == HybridConsumerPipeline::Kind::ReadBacking
        ? execution->backingRangeID
        : execution->destinationRangeID;
    panic_if(region < 0 || getAddrRegion(pipeline_request.address) != region,
             "Direct-retirement address 0x%lx escaped its registered range\n",
             pipeline_request.address);
    RequestPtr translationRequest = std::make_shared<Request>(
        pipeline_request.address, pipeline_request.size, Request::Flags(0),
        requestorId, execution->pc, execution->contextID);
    ImmediateLogicalSPDTranslation translation;
    ThreadContext *tc = system->threads[execution->contextID];
    const BaseMMU::Mode mode =
        pipeline_request.kind == HybridConsumerPipeline::Kind::ReadBacking
            ? BaseMMU::Read : BaseMMU::Write;
    mmu->translateTiming(translationRequest, tc, &translation, mode);
    panic_if(translation.delayed || !translation.finished ||
                 translation.fault != NoFault,
             "Direct retirement requires immediate valid translation for "
             "0x%lx\n", pipeline_request.address);
    RequestPtr realRequest = std::make_shared<Request>(
        translation.address, pipeline_request.size, Request::Flags(0),
        requestorId);
    realRequest->setRegion(region);
    PacketPtr packet = new Packet(
        realRequest,
        pipeline_request.kind == HybridConsumerPipeline::Kind::ReadBacking
                         ? MemCmd::ReadReq : MemCmd::WriteReq);
    // The bounded credit-owned buffers are the packet storage: a read fills
    // the credit directly, ALU updates it in place, and WriteReq retains it
    // until its exact WriteResp. No page payload or shadow write queue exists.
    packet->dataStatic(reinterpret_cast<uint8_t *>(
        directRetirementContexts.bufferData(request)));
    auto *state = new DirectRetirementSenderState;
    state->request = request;
    // Cache-bank routing follows the translated physical address. The pure
    // scheduler's virtual-address port remains part of its unit-test identity.
    state->callbackPort = core_addr(translation.address);
    packet->pushSenderState(state);
    return packet;
}

PacketPtr
MAA::makePageMaterializationPacket(
    const HybridConsumerContextQueue::Request &request)
{
    const PageMaterializationExecution *execution =
        findPageMaterializationExecution(request.owner);
    const auto &pipelineRequest = request.request;
    panic_if(execution == nullptr || !execution->pageActive ||
                 pipelineRequest.kind !=
                     HybridConsumerPipeline::Kind::ReadBacking ||
                 pipelineRequest.size != HybridConsumerPipeline::LineBytes ||
                 pipelineRequest.buffer >=
                     HybridConsumerPipeline::LineBufferCount ||
                 pipelineRequest.line /
                         directRetirementContexts.producerPageLines(
                             request.owner) != execution->page,
             "Page materializer produced an invalid cache-line request\n");
    panic_if(execution->backingRangeID < 0 ||
                 getAddrRegion(pipelineRequest.address) !=
                     execution->backingRangeID,
             "Page-materializer address 0x%lx escaped its registered range\n",
             pipelineRequest.address);
    RequestPtr translationRequest = std::make_shared<Request>(
        pipelineRequest.address, pipelineRequest.size, Request::Flags(0),
        requestorId, execution->pc, execution->contextID);
    ImmediateLogicalSPDTranslation translation;
    ThreadContext *tc = system->threads[execution->contextID];
    mmu->translateTiming(translationRequest, tc, &translation,
                         BaseMMU::Read);
    panic_if(translation.delayed || !translation.finished ||
                 translation.fault != NoFault,
             "Page materializer requires immediate valid translation for "
             "0x%lx\n", pipelineRequest.address);
    RequestPtr realRequest = std::make_shared<Request>(
        translation.address, pipelineRequest.size, Request::Flags(0),
        requestorId);
    realRequest->setRegion(execution->backingRangeID);
    PacketPtr packet = new Packet(realRequest, MemCmd::ReadReq);
    packet->dataStatic(reinterpret_cast<uint8_t *>(
        directRetirementContexts.bufferData(request)));
    auto *state = new DirectRetirementSenderState;
    state->request = request;
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
    DirectRetirementExecution *execution = state == nullptr
        ? nullptr : findDirectRetirementExecution(state->request.owner);
    PageMaterializationExecution *materialization = state == nullptr
        ? nullptr
        : findPageMaterializationExecution(state->request.owner);
    panic_if(state == nullptr || (execution == nullptr) ==
                                  (materialization == nullptr) ||
                 state->callbackPort != respondingPort,
             "Direct-retirement response lost exact port provenance\n");
    const Addr paddr = pkt->getAddr();
    panic_if(!releaseDirectRetirementRequest(paddr, state->request),
             "Direct-retirement response at 0x%lx did not own an exact "
             "address reservation\n", paddr);
    const auto &request = state->request.request;
    if (materialization != nullptr) {
        panic_if(!materialization->pageActive ||
                     request.kind !=
                         HybridConsumerPipeline::Kind::ReadBacking ||
                     pkt->cmd != MemCmd::ReadResp ||
                     pkt->getSize() !=
                         HybridConsumerPipeline::LineBytes ||
                     !directRetirementContexts.completeRead(
                         state->request,
                         reinterpret_cast<const std::byte *>(
                             pkt->getConstPtr<uint8_t>()),
                         pkt->getSize()),
                 "Page materializer rejected an exact cache ReadResp\n");
        const Cycles spdLatency = spd->setDataLatency(
            materialization->destinationTile,
            HybridConsumerPipeline::LineBytes /
                materialization->wordBytes);
        panic_if(!reservePageMaterializationCommit(
                     state->request, getClockEdge(spdLatency)),
                 "Page materializer exhausted its charged line commits\n");
        const uint16_t pageLines =
            directRetirementContexts.producerPageLines(materialization->key);
        const uint8_t logicalPage = request.line / pageLines;
        panic_if(logicalPage >= HybridConsumerPipeline::ProducerPages ||
                     logicalPage != materialization->page,
                 "Page materializer fallback lost logical page identity\n");
        ++materialization->cacheReadFallbackLines;
        ++materialization->cacheReadFallbackLinesPerPage[logicalPage];
        stats.page_materialization_cache_read_fallback_lines++;
        DPRINTF(MAAVirtualTrace,
                "event=page_materialization_read_response schema=1 "
                "occurrence=%lu token=%u generation=%lu incarnation=%lu "
                "page=%u line=%u buffer=%u spd_ready_tick=%lu\n",
                pageMaterializationTraceOccurrence++,
                materialization->key.tokenTile,
                materialization->key.generation,
                materialization->key.incarnation, materialization->page,
                request.line, request.buffer, getClockEdge(spdLatency));
        delete state;
        sendNextDeferredPacket(paddr);
        schedulePageMaterializationEvent();
        return true;
    }
    bool accepted = false;
    if (request.kind == HybridConsumerPipeline::Kind::ReadBacking) {
        panic_if(pkt->cmd != MemCmd::ReadResp ||
                     pkt->getSize() != HybridConsumerPipeline::LineBytes,
                 "Direct retirement read did not receive an exact ReadResp\n");
        accepted = directRetirementContexts.completeRead(
            state->request, reinterpret_cast<const std::byte *>(
                                pkt->getConstPtr<uint8_t>()), pkt->getSize());
        panic_if(!accepted || !execution->macro.complete(
                                 HybridMacroEventTracker::Stage::PageFill,
                                 curTick()),
                 "Direct retirement rejected a read completion\n");
        stats.direct_retirement_read_responses++;
    } else {
        panic_if(pkt->cmd != MemCmd::WriteResp ||
                     pkt->getSize() != HybridConsumerPipeline::LineBytes,
                 "Direct retirement write did not receive an exact "
                 "WriteResp\n");
        accepted = directRetirementContexts.completeWriteAck(state->request);
        panic_if(!accepted || !execution->macro.complete(
                                 HybridMacroEventTracker::Stage::StreamStore,
                                 curTick()),
                 "Direct retirement rejected a write completion\n");
        stats.direct_retirement_write_responses++;
    }
    HybridConsumerContextQueue::Snapshot snapshot;
    panic_if(!directRetirementContexts.snapshot(execution->key, &snapshot),
             "Direct-retirement response lost its context snapshot\n");
    DPRINTF(MAAVirtualTrace,
            "event=direct_retirement_response schema=1 occurrence=%lu "
            "token=%u generation=%lu incarnation=%lu line=%u buffer=%u "
            "action=%u context_credits_in_use=%u total_credits_in_use=%u\n",
            directRetirementTraceOccurrence++, execution->key.tokenTile,
            execution->key.generation, execution->key.incarnation, request.line,
            request.buffer, static_cast<unsigned>(request.kind),
            snapshot.creditsInUse,
            directRetirementContexts.totalCreditsInUse());
    delete state;
    sendNextDeferredPacket(paddr);
    scheduleDirectRetirementEvent();
    return true;
}

void
MAA::completeDirectRetirementALU(int maaID, uint16_t tokenTile,
                                 uint64_t generation,
                                 uint64_t incarnation,
                                 uint64_t transactionID)
{
    const HybridConsumerContextQueue::ContextKey owner{
        tokenTile, generation, incarnation};
    DirectRetirementExecution *execution =
        findDirectRetirementExecution(owner);
    panic_if(execution == nullptr || execution->maaID != maaID ||
                 execution->aluRequest.request.transactionID !=
                     transactionID ||
                 !sameDirectRetirementKey(execution->aluRequest.owner,
                                          owner) ||
                 !directRetirementContexts.completeCompute(
                     execution->aluRequest) ||
                 !execution->macro.complete(
                     HybridMacroEventTracker::Stage::ALU, curTick()),
             "Direct-retirement ALU completion lost its line ownership\n");
    execution->aluRequest = {};
    aluUnitsIdle[maaID] = true;
    stats.direct_retirement_alu_completions++;
    DPRINTF(MAAVirtualTrace,
            "event=direct_retirement_alu_complete schema=1 occurrence=%lu "
            "token=%u generation=%lu incarnation=%lu transaction=%lu\n",
            directRetirementTraceOccurrence++, tokenTile, generation,
            incarnation, transactionID);
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
    const uint8_t active_contexts = directRetirementContexts.activeContexts();
    if (!DirectRetirementPortDomain::contains(port)) {
        // Cache-side callbacks are sized by runtime num_cores, while direct
        // retirement deliberately has exactly four retry slots.  An
        // out-of-domain callback when inactive is unrelated wake traffic; a
        // live context would violate admission's fixed-domain contract.
        panic_if(!DirectRetirementPortDomain::harmlessInactiveWake(
                     port, active_contexts),
                 "Direct-retirement live context received out-of-domain "
                 "cache-port wake %u (active=%u)\n", port, active_contexts);
        return;
    }
    if (active_contexts != 0)
        scheduleDirectRetirementEvent();
    if (directRetirementContexts.activeContexts(
            HybridConsumerPipeline::Mode::MaterializePages) != 0)
        schedulePageMaterializationEvent();
    DPRINTF(MAAVirtualTrace,
            "event=direct_retirement_port_wake schema=1 occurrence=%lu "
            "port=%u active_contexts=%u\n", directRetirementTraceOccurrence++,
            port, active_contexts);
}

void
MAA::finishDirectRetirement(
    const HybridConsumerContextQueue::ContextKey &key)
{
    DirectRetirementExecution *execution =
        findDirectRetirementExecution(key);
    HybridConsumerContextQueue::Snapshot snapshot;
    bool retry_owned = false;
    for (uint8_t port = 0;
         port < DirectRetirementPortRetry<Packet>::PortCount; ++port) {
        PacketPtr packet = directRetirementRetryPackets.packet(port);
        if (packet == nullptr)
            continue;
        auto *state = dynamic_cast<DirectRetirementSenderState *>(
            packet->senderState);
        panic_if(state == nullptr || state->callbackPort != port,
                 "Direct-retirement retry lost exact port provenance\n");
        retry_owned = retry_owned ||
            sameDirectRetirementKey(state->request.owner, key);
    }
    panic_if(execution == nullptr ||
                 !directRetirementContexts.snapshot(key, &snapshot) ||
                 !snapshot.complete || snapshot.creditsInUse != 0 ||
                 retry_owned || hasDirectRetirementOutstandingOwner(key) ||
                 !execution->macro.finish(curTick()),
             "Direct-retirement completion was attempted before all credits "
             "closed\n");
    const auto record = execution->macro.result();
    const uint64_t generation = key.generation;
    const int token_tile = key.tokenTile;
    const int completion_tile = execution->completionTile;
    const int word_bytes = execution->wordBytes;
    panic_if(virtualPageGeneration[token_tile] != generation,
             "Direct-retirement token generation changed before final ACK\n");
    panic_if(snapshot.producerLineAcks +
                 snapshot.producerPageFallbackLines != snapshot.lines,
             "Direct-retirement producer visibility did not close exactly\n");
    virtualPageConsumedGeneration[token_tile] = generation;
    stats.direct_retirement_overlap_ticks += record.overlapTicks;
    stats.direct_retirement_active_stage_high_water = std::max(
        stats.direct_retirement_active_stage_high_water.value(),
        static_cast<double>(record.activeStageHighWater));
    stats.direct_retirement_credit_high_water = std::max(
        stats.direct_retirement_credit_high_water.value(),
        static_cast<double>(snapshot.creditHighWater));
    DPRINTF(MAAVirtualTrace,
            "event=direct_retirement_summary schema=1 occurrence=%lu "
            "token=%u generation=%lu incarnation=%lu reads=%u computes=%u "
            "writes=%u "
            "credit_high_water=%u overlap_ticks=%lu "
            "active_stage_high_water=%lu line_acks=%u "
            "page_fallback_lines=%u fallback_count=%lu\n",
            directRetirementTraceOccurrence++, key.tokenTile, generation,
            key.incarnation, snapshot.readsAccepted,
            snapshot.computesAccepted, snapshot.writesAccepted,
            snapshot.creditHighWater,
            record.overlapTicks, record.activeStageHighWater,
            snapshot.producerLineAcks,
            snapshot.producerPageFallbackLines,
            static_cast<uint64_t>(
                stats.direct_retirement_fallbacks.value()));
    panic_if(!directRetirementContexts.retire(key),
             "Direct-retirement scheduler did not retire after final ACK\n");
    (void)directRetirementEarlyLineLedger.clear(
        {key.tokenTile, key.generation, execution->backingAddress});
    const auto payloadClear = inactiveProducerLinePayloadCapture.clear(
        {key.tokenTile, key.generation,
         virtualPagePayloadIncarnation[key.tokenTile],
         execution->backingAddress});
    stats.page_materialization_inactive_payload_drops +=
        payloadClear.discardedLines;
    const auto maskedClear = inactiveMaskedFragmentRetention.clear(
        {key.tokenTile, key.generation,
         virtualPagePayloadIncarnation[key.tokenTile],
         execution->backingAddress});
    if (maskedClear)
        stats.page_materialization_inactive_masked_clears++;
    *execution = DirectRetirementExecution{};
    setTileReady(completion_tile, word_bytes);
    DPRINTF(MAAVirtualTrace,
            "event=direct_retirement_retire schema=1 occurrence=%lu "
            "token=%u generation=%lu incarnation=%lu "
            "final_write_responses=%u remaining_contexts=%u\n",
            directRetirementTraceOccurrence++, key.tokenTile, generation,
            key.incarnation, snapshot.writesAccepted,
            directRetirementContexts.activeContexts());
    schedulePageMaterializationEvent();
    scheduleDispatchInstructionEvent();
}

void
MAA::serviceDirectRetirement()
{
    if (directRetirementContexts.activeContexts(
            HybridConsumerPipeline::Mode::TransformAndStore) == 0)
        return;
    for (const DirectRetirementExecution &execution :
         directRetirementExecutions) {
        if (!execution.active)
            continue;
        HybridConsumerContextQueue::Snapshot snapshot;
        panic_if(!directRetirementContexts.snapshot(execution.key, &snapshot),
                 "Direct-retirement execution lost its queue context\n");
        if (snapshot.complete) {
            const auto key = execution.key;
            finishDirectRetirement(key);
            if (directRetirementContexts.activeContexts(
                    HybridConsumerPipeline::Mode::TransformAndStore) != 0)
                scheduleDirectRetirementEvent();
            return;
        }
    }

    auto claim = [this](
        const HybridConsumerContextQueue::Request &request) {
        DirectRetirementExecution *execution =
            findDirectRetirementExecution(request.owner);
        panic_if(execution == nullptr ||
                     !directRetirementContexts.accept(request),
                 "Direct-retirement accepted packet had stale ownership\n");
    };

    auto issued = [this](
        const HybridConsumerContextQueue::Request &request) {
        DirectRetirementExecution *execution =
            findDirectRetirementExecution(request.owner);
        panic_if(execution == nullptr,
                 "Direct-retirement issued packet had stale ownership\n");
        const auto stage =
            request.request.kind == HybridConsumerPipeline::Kind::ReadBacking
            ? HybridMacroEventTracker::Stage::PageFill
            : HybridMacroEventTracker::Stage::StreamStore;
        HybridConsumerContextQueue::Snapshot snapshot;
        panic_if(!directRetirementContexts.snapshot(
                     request.owner, &snapshot) ||
                     !execution->macro.issue(
                         stage, curTick(), snapshot.creditsInUse) ||
                     !execution->macro.traffic(
                         stage, 1, HybridConsumerPipeline::LineBytes),
                 "Direct-retirement could not record a live cache request\n");
        if (request.request.kind == HybridConsumerPipeline::Kind::ReadBacking)
            stats.direct_retirement_read_issues++;
        else
            stats.direct_retirement_write_issues++;
        stats.direct_retirement_credit_high_water = std::max(
            stats.direct_retirement_credit_high_water.value(),
            static_cast<double>(
                directRetirementContexts.totalCreditsInUse()));
        DPRINTF(MAAVirtualTrace,
                "event=direct_retirement_issue schema=1 occurrence=%lu "
                "token=%u generation=%lu incarnation=%lu line=%u buffer=%u "
                "action=%u address=0x%lx context_credits_in_use=%u "
                "total_credits_in_use=%u request_records_in_use=%u\n",
                directRetirementTraceOccurrence++, request.owner.tokenTile,
                request.owner.generation, request.owner.incarnation,
                request.request.line, request.request.buffer,
                static_cast<unsigned>(request.request.kind),
                request.request.address, snapshot.creditsInUse,
                directRetirementContexts.totalCreditsInUse(),
                directRetirementOutstandingRequestCount());
    };

    auto discardUnsentPacket = [](PacketPtr packet) {
        auto *state = dynamic_cast<DirectRetirementSenderState *>(
            packet->popSenderState());
        panic_if(state == nullptr,
                 "Unsent direct-retirement packet lost sender state\n");
        delete state;
        delete packet;
    };

    // Each translated physical port owns at most one refused packet. Retry
    // every independently unblocked port before considering fresh work; a
    // still-blocked bank cannot stop another bank's retained request.
    for (uint8_t port = 0;
         port < DirectRetirementPortRetry<Packet>::PortCount; ++port) {
        PacketPtr packet = directRetirementRetryPackets.packet(port);
        if (packet == nullptr)
            continue;
        auto *state = dynamic_cast<DirectRetirementSenderState *>(
            packet->senderState);
        if (state != nullptr &&
            findPageMaterializationExecution(state->request.owner) != nullptr)
            continue;
        panic_if(state == nullptr ||
                     state->callbackPort != port ||
                     findDirectRetirementExecution(state->request.owner) ==
                         nullptr,
                 "Direct-retirement retry lost sender/port identity\n");
        const Addr paddr = packet->getAddr();
        const auto deferred = my_deferred_pkt_map.find(paddr);
        if (hasOutstandingPacket(paddr) ||
            hasDirectRetirementOutstandingAddress(paddr) ||
            (deferred != my_deferred_pkt_map.end() &&
             !deferred->second.empty())) {
            stats.direct_retirement_address_stalls++;
            continue;
        }
        uint8_t actual_port = DirectRetirementPortRetry<Packet>::PortCount;
        if (!sendPacketCache(packet, &actual_port)) {
            panic_if(actual_port != port,
                     "Direct-retirement retry changed physical port\n");
            stats.direct_retirement_retries++;
            continue;
        }
        const HybridConsumerContextQueue::Request request = state->request;
        panic_if(actual_port != port ||
                     !directRetirementRetryPackets.release(port, packet),
                 "Direct-retirement retry released the wrong port packet\n");
        panic_if(!reserveDirectRetirementRequest(paddr, request),
                 "Direct-retirement retry duplicated address 0x%lx\n", paddr);
        stats.direct_retirement_request_record_high_water = std::max(
            stats.direct_retirement_request_record_high_water.value(),
            static_cast<double>(
                directRetirementOutstandingRequestCount()));
        issued(request);
    }

    struct PendingPacket
    {
        HybridConsumerContextQueue::Request request{};
        PacketPtr packet = nullptr;
        uint8_t port = DirectRetirementPortRetry<Packet>::PortCount;
    };

    // A blocked-port contender remains pending in its finite context. Rotate
    // past at most the four exact context requests to find another physical
    // bank; defer() authenticates the full owner/incarnation/request before
    // moving the corresponding read or write cursor.
    auto selectPacket = [this, &discardUnsentPacket](bool write) {
        PendingPacket selected;
        for (uint8_t scan = 0;
             scan < HybridConsumerContextQueue::ContextCount; ++scan) {
            const HybridConsumerContextQueue::Request request = write
                ? directRetirementContexts.pendingWrite()
                : directRetirementContexts.pendingRead(
                      HybridConsumerPipeline::Mode::TransformAndStore);
            if (request.request.kind == HybridConsumerPipeline::Kind::None)
                return selected;
            PacketPtr packet = makeDirectRetirementPacket(request);
            auto *state = dynamic_cast<DirectRetirementSenderState *>(
                packet->senderState);
            panic_if(state == nullptr ||
                         !sameDirectRetirementRequest(state->request,
                                                     request) ||
                         state->callbackPort >=
                             DirectRetirementPortRetry<Packet>::PortCount,
                     "Direct-retirement candidate lost sender identity\n");
            const uint8_t port = state->callbackPort;
            if (!directRetirementRetryPackets.occupied(port))
                return PendingPacket{request, packet, port};
            discardUnsentPacket(packet);
            panic_if(!directRetirementContexts.defer(request),
                     "Direct-retirement could not defer an exact blocked-port "
                     "request\n");
        }
        return selected;
    };

    for (unsigned attempt = 0;
         attempt < DirectRetirementRequestRecordCount +
                       HybridConsumerContextQueue::ContextCount + 2;
         ++attempt) {
        PendingPacket pending = selectPacket(true);
        if (pending.packet == nullptr)
            pending = selectPacket(false);
        const auto compute = directRetirementContexts.pendingCompute();
        if (pending.packet == nullptr &&
            compute.request.kind == HybridConsumerPipeline::Kind::None) {
            const bool no_cache_request =
                directRetirementContexts.pendingRead(
                    HybridConsumerPipeline::Mode::TransformAndStore)
                        .request.kind ==
                    HybridConsumerPipeline::Kind::None &&
                directRetirementContexts.pendingWrite().request.kind ==
                    HybridConsumerPipeline::Kind::None;
            if (no_cache_request &&
                directRetirementContexts.totalCreditsInUse() ==
                    DirectRetirementRequestRecordCount)
                stats.direct_retirement_credit_stalls++;
            return;
        }
        if (pending.packet != nullptr) {
            const auto request = pending.request;
            PacketPtr packet = pending.packet;
            const Addr paddr = packet->getAddr();
            const auto deferred = my_deferred_pkt_map.find(paddr);
            if (hasOutstandingPacket(paddr) ||
                hasDirectRetirementOutstandingAddress(paddr) ||
                (deferred != my_deferred_pkt_map.end() &&
                 !deferred->second.empty())) {
                discardUnsentPacket(packet);
                stats.direct_retirement_address_stalls++;
                return;
            }
            uint8_t actual_port =
                DirectRetirementPortRetry<Packet>::PortCount;
            if (!sendPacketCache(packet, &actual_port)) {
                panic_if(actual_port != pending.port ||
                             !directRetirementRetryPackets.arm(
                                 pending.port, packet),
                         "Direct-retirement could not retain exactly one "
                         "packet for physical port %u\n", pending.port);
                claim(request);
                stats.direct_retirement_retries++;
                continue;
            }
            panic_if(actual_port != pending.port ||
                         !reserveDirectRetirementRequest(paddr, request),
                     "Direct-retirement duplicated address 0x%lx\n", paddr);
            stats.direct_retirement_request_record_high_water = std::max(
                stats.direct_retirement_request_record_high_water.value(),
                static_cast<double>(
                    directRetirementOutstandingRequestCount()));
            claim(request);
            issued(request);
            continue;
        }
        panic_if(compute.request.kind == HybridConsumerPipeline::Kind::None,
                 "Direct-retirement scheduler lost a runnable line\n");
        DirectRetirementExecution *execution =
            findDirectRetirementExecution(compute.owner);
        panic_if(execution == nullptr,
                 "Direct-retirement compute lost its execution owner\n");
        if (!aluUnitsIdle[execution->maaID])
            return;
        panic_if(!directRetirementContexts.accept(compute) ||
                     !execution->macro.issue(
                         HybridMacroEventTracker::Stage::ALU, curTick(), 1) ||
                     !aluUnits[execution->maaID]
                          .startDirectLine(
                         directRetirementContexts.bufferData(compute),
                         execution->wordBytes, execution->datatype,
                         execution->operation, execution->scalarBits,
                         compute.owner.tokenTile, compute.owner.generation,
                         compute.owner.incarnation,
                         compute.request.transactionID),
                 "Direct-retirement could not claim the existing ALU lane\n");
        execution->aluRequest = compute;
        aluUnitsIdle[execution->maaID] = false;
        stats.direct_retirement_alu_issues++;
        DPRINTF(MAAVirtualTrace,
                "event=direct_retirement_alu_issue schema=1 occurrence=%lu "
                "token=%u generation=%lu incarnation=%lu line=%u buffer=%u "
                "transaction=%lu\n",
                directRetirementTraceOccurrence++, compute.owner.tokenTile,
                compute.owner.generation, compute.owner.incarnation,
                compute.request.line, compute.request.buffer,
                compute.request.transactionID);
        return;
    }
}

bool
MAA::reservePageMaterializationCommit(
    const HybridConsumerContextQueue::Request &request, Tick readyTick)
{
    if (request.request.kind !=
            HybridConsumerPipeline::Kind::ReadBacking ||
        findPageMaterializationExecution(request.owner) == nullptr)
        return false;
    for (const PageMaterializationCommit &commit :
         pageMaterializationCommits) {
        if (commit.active &&
            sameDirectRetirementRequest(commit.request, request))
            return false;
    }
    for (PageMaterializationCommit &commit : pageMaterializationCommits) {
        if (commit.active)
            continue;
        commit.active = true;
        commit.readyTick = readyTick;
        commit.request = request;
        return true;
    }
    return false;
}

bool
MAA::reservePageMaterializationDirectCommit(
    const HybridConsumerContextQueue::ContextKey &key, uint16_t line,
    Tick readyTick)
{
    PageMaterializationExecution *execution =
        findPageMaterializationExecution(key);
    if (execution == nullptr || !execution->pageActive ||
        line / directRetirementContexts.producerPageLines(key) !=
            execution->page)
        return false;
    for (const PageMaterializationCommit &commit :
         pageMaterializationCommits) {
        if (commit.active && commit.directStaged &&
            sameDirectRetirementKey(commit.owner, key) &&
            commit.line == line)
            return false;
    }
    for (PageMaterializationCommit &commit : pageMaterializationCommits) {
        if (commit.active)
            continue;
        commit.active = true;
        commit.directStaged = true;
        commit.readyTick = readyTick;
        commit.owner = key;
        commit.line = line;
        return true;
    }
    return false;
}

MAA::InactivePayloadLookupStart
MAA::startInactiveProducerPayloadLookup(
    const HybridConsumerContextQueue::Request &request)
{
    if ((inactive_page_payload_capture_lines == 0 &&
         inactive_page_masked_fragment_retention_lines == 0) ||
        request.request.kind != HybridConsumerPipeline::Kind::ReadBacking)
        return InactivePayloadLookupStart::NotApplicable;
    if (inactive_page_masked_fragment_retention_lines != 0) {
        panic_if(inactiveMaskedFragmentLookup.timing.pending(),
                 "Inactive masked-fragment lookup accepted two requests\n");
        PageMaterializationExecution *execution =
            findPageMaterializationExecution(request.owner);
        if (execution == nullptr || !execution->pageActive)
            return InactivePayloadLookupStart::NotApplicable;
        const InactiveProducerMaskedFragmentRetention::Key key{
            execution->key.tokenTile, execution->key.generation,
            virtualPagePayloadIncarnation[execution->key.tokenTile],
            execution->backingAddress};
        InactiveProducerMaskedFragmentRetention::Line retained;
        const auto probe = inactiveMaskedFragmentRetention.probe(
            key, request.request.line,
            static_cast<uint8_t>(execution->wordBytes),
            static_cast<uint64_t>(curCycle()), &retained);
        if (probe ==
            InactiveProducerMaskedFragmentRetention::ProbeResult::PortBusy) {
            stats
                .page_materialization_inactive_masked_read_port_stalls++;
            return InactivePayloadLookupStart::ReadPortBusy;
        }
        panic_if(
            (probe != InactiveProducerMaskedFragmentRetention::
                          ProbeResult::Hit &&
             probe != InactiveProducerMaskedFragmentRetention::
                          ProbeResult::Miss) ||
                !inactiveMaskedFragmentLookup.timing.arm(
                    static_cast<uint64_t>(curCycle()), probe),
            "Inactive masked-fragment lookup did not produce a timed "
            "result\n");
        inactiveMaskedFragmentLookup.request = request;
        inactiveMaskedFragmentLookup.key = key;
        inactiveMaskedFragmentLookup.line = request.request.line;
        DPRINTF(MAAVirtualTrace,
                "event=page_materialization_inactive_masked_lookup schema=1 "
                "occurrence=%lu token=%u generation=%lu incarnation=%lu "
                "payload_incarnation=%lu line=%u result=%u "
                "issue_cycle=%lu completion_cycle=%lu access_cycles=%u\n",
                pageMaterializationTraceOccurrence++, request.owner.tokenTile,
                request.owner.generation, request.owner.incarnation,
                key.incarnation, request.request.line,
                static_cast<unsigned>(probe),
                static_cast<uint64_t>(curCycle()),
                inactiveMaskedFragmentLookup.timing.completionCycle(),
                InactiveProducerMaskedFragmentRetention::PortAccessCycles);
        return InactivePayloadLookupStart::Started;
    }
    panic_if(inactivePayloadLookup.timing.pending(),
             "Inactive payload lookup pipeline accepted two requests\n");
    PageMaterializationExecution *execution =
        findPageMaterializationExecution(request.owner);
    if (execution == nullptr || !execution->pageActive)
        return InactivePayloadLookupStart::NotApplicable;
    const InactiveProducerLinePayloadCapture::Key key{
        execution->key.tokenTile, execution->key.generation,
        virtualPagePayloadIncarnation[execution->key.tokenTile],
        execution->backingAddress};
    InactiveProducerLinePayloadCapture::Line retained;
    const auto probe = inactiveProducerLinePayloadCapture.probe(
        key, request.request.line, static_cast<uint64_t>(curCycle()),
        &retained);
    if (probe == InactiveProducerLinePayloadCapture::ProbeResult::PortBusy) {
        stats.page_materialization_inactive_payload_read_port_stalls++;
        return InactivePayloadLookupStart::ReadPortBusy;
    }

    // A configured capture RAM always spends its one MAA-cycle read access
    // before either result becomes visible. `probe()` has selected exactly one
    // direct-indexed entry; even a Miss is held in this fixed one-entry latch
    // until the next MAA cycle, so same-event ReadBacking bypass is
    // impossible.
    panic_if(
        (probe != InactiveProducerLinePayloadCapture::ProbeResult::Hit &&
         probe != InactiveProducerLinePayloadCapture::ProbeResult::Miss) ||
                 !inactivePayloadLookup.timing.arm(
                     static_cast<uint64_t>(curCycle()), probe),
             "Inactive payload lookup did not produce one timed result\n");
    inactivePayloadLookup.request = request;
    inactivePayloadLookup.key = key;
    inactivePayloadLookup.line = request.request.line;
    DPRINTF(MAAVirtualTrace,
            "event=page_materialization_inactive_payload_lookup schema=1 "
            "occurrence=%lu token=%u generation=%lu incarnation=%lu "
            "payload_incarnation=%lu "
            "line=%u result=%u issue_cycle=%lu completion_cycle=%lu "
            "access_cycles=%u port_time_unit=maa_cycles\n",
            pageMaterializationTraceOccurrence++, request.owner.tokenTile,
            request.owner.generation, request.owner.incarnation,
            key.incarnation,
            request.request.line, static_cast<unsigned>(probe),
            static_cast<uint64_t>(curCycle()),
            inactivePayloadLookup.timing.completionCycle(),
            InactiveProducerLinePayloadCapture::PortAccessCycles);
    return InactivePayloadLookupStart::Started;
}

bool
MAA::consumeInactiveProducerPayload()
{
    if (inactiveMaskedFragmentLookup.timing.pending()) {
        panic_if(!inactiveMaskedFragmentLookup.timing.ready(
                     static_cast<uint64_t>(curCycle())),
                 "Inactive masked-fragment lookup completed early\n");
        const auto request = inactiveMaskedFragmentLookup.request;
        const auto key = inactiveMaskedFragmentLookup.key;
        const uint16_t line = inactiveMaskedFragmentLookup.line;
        const auto result = inactiveMaskedFragmentLookup.timing.result();
        if (result ==
            InactiveProducerMaskedFragmentRetention::ProbeResult::Miss) {
            stats.page_materialization_inactive_masked_replay_misses++;
            // The request's snapshotted buffer is not authoritative after
            // the one-cycle read. Retain its exact request identity, but
            // never treat the snapshotted buffer field as authority. The
            // shared table must rebind a current free buffer immediately
            // before coherent cache issue.
            panic_if(!inactivePayloadFallbacks.retain(request),
                     "Inactive masked-fragment fallback table exhausted\n");
            inactiveMaskedFragmentLookup = InactiveMaskedFragmentLookup{};
            return false;
        }
        panic_if(result !=
                     InactiveProducerMaskedFragmentRetention::ProbeResult::Hit,
                 "Inactive masked-fragment lookup completed invalidly\n");
        const uint64_t transactionID =
            inactiveMaskedFragmentRetention.pipelinedTransactionID();
        panic_if(transactionID == 0,
                 "Inactive masked-fragment hit lost output identity\n");
        PageMaterializationExecution *execution =
            findPageMaterializationExecution(request.owner);
        panic_if(execution == nullptr || !execution->pageActive ||
                     execution->backingAddress != key.backingAddress,
                 "Inactive masked-fragment lookup lost materializer owner\n");
        HybridConsumerContextQueue::Request captured;
        panic_if(!directRetirementContexts.captureMaterializationLine(
                     execution->key, line,
                     inactiveMaskedFragmentRetention.pipelinedPayload(),
                     HybridConsumerPipeline::LineBytes, &captured),
                 "Materializer rejected reconstructed inactive line\n");
        const uint16_t wordsPerLine =
            HybridConsumerPipeline::LineBytes / execution->wordBytes;
        const Cycles spdLatency = spd->setDataLatency(
            execution->destinationTile, wordsPerLine);
        const Tick commitTick = getClockEdge(spdLatency);
        panic_if(!reservePageMaterializationCommit(captured, commitTick) ||
                     !inactiveMaskedFragmentRetention.take(
                         key, line, transactionID,
                         static_cast<uint64_t>(curCycle())),
                 "Materializer could not consume reconstructed inactive "
                 "line\n");
        inactiveMaskedFragmentLookup = InactiveMaskedFragmentLookup{};
        ++execution->forwardedLines;
        stats.page_materialization_forwarded_lines++;
        stats.page_materialization_inactive_masked_replay_hits++;
        DPRINTF(MAAVirtualTrace,
                "event=page_materialization_inactive_masked_replay schema=1 "
                "occurrence=%lu token=%u generation=%lu incarnation=%lu "
                "payload_incarnation=%lu page=%u line=%u transaction=%lu "
                "lookup_cycles=%u commit_tick=%lu\n",
                pageMaterializationTraceOccurrence++, execution->key.tokenTile,
                execution->key.generation, execution->key.incarnation,
                key.incarnation, execution->page, line, transactionID,
                InactiveProducerMaskedFragmentRetention::PortAccessCycles,
                commitTick);
        return true;
    }
    panic_if(!inactivePayloadLookup.timing.pending() ||
                 !inactivePayloadLookup.timing.ready(
                     static_cast<uint64_t>(curCycle())),
             "Inactive payload lookup completed before its MAA-cycle delay\n");
    const auto request = inactivePayloadLookup.request;
    const auto key = inactivePayloadLookup.key;
    const uint16_t line = inactivePayloadLookup.line;
    const auto result = inactivePayloadLookup.timing.result();
    if (result == InactiveProducerLinePayloadCapture::ProbeResult::Miss) {
        stats.page_materialization_inactive_payload_lookup_misses++;
        // Keep only a fixed exact fallback identity.  Its snapshotted buffer
        // is deliberately not trusted after the one-cycle lookup.
        panic_if(!inactivePayloadFallbacks.retain(request),
                 "Inactive payload fallback table exhausted\n");
        inactivePayloadLookup = InactivePayloadLookup{};
        return false;
    }
    panic_if(result != InactiveProducerLinePayloadCapture::ProbeResult::Hit,
             "Inactive payload lookup completed with invalid result\n");
    const uint64_t transactionID =
        inactiveProducerLinePayloadCapture.pipelinedTransactionID();
    panic_if(transactionID == 0,
             "Inactive payload hit lost its authoritative output tag\n");
    stats.page_materialization_inactive_payload_lookup_hits++;
    PageMaterializationExecution *execution =
        findPageMaterializationExecution(request.owner);
    panic_if(execution == nullptr || !execution->pageActive ||
                 execution->backingAddress != key.backingAddress,
             "Inactive payload lookup lost exact materializer ownership\n");
    HybridConsumerContextQueue::Request captured;
    panic_if(!directRetirementContexts.captureMaterializationLine(
                 execution->key, line,
                 inactiveProducerLinePayloadCapture.pipelinedPayload(),
                 HybridConsumerPipeline::LineBytes, &captured),
             "Page materializer rejected a probed inactive payload\n");
    const uint16_t wordsPerLine =
        HybridConsumerPipeline::LineBytes / execution->wordBytes;
    const Cycles spdLatency = spd->setDataLatency(execution->destinationTile,
                                                   wordsPerLine);
    // The fixed lookup pipeline has already charged its one MAA cycle before
    // entering this completion path. Its output register feeds the existing
    // charged materializer buffer at this edge, then uses the unchanged SPD
    // data latency. Do not charge the RAM access a second time.
    const Tick commitTick = getClockEdge(spdLatency);
    panic_if(!reservePageMaterializationCommit(captured, commitTick) ||
                 !inactiveProducerLinePayloadCapture.take(
                     key, line, transactionID,
                     static_cast<uint64_t>(curCycle())),
             "Page materializer could not consume exact inactive payload\n");
    inactivePayloadLookup = InactivePayloadLookup{};
    ++execution->forwardedLines;
    stats.page_materialization_forwarded_lines++;
    stats.page_materialization_inactive_payload_replays++;
    DPRINTF(MAAVirtualTrace,
            "event=page_materialization_inactive_payload_replay schema=1 "
            "occurrence=%lu token=%u generation=%lu incarnation=%lu "
            "payload_incarnation=%lu "
            "page=%u line=%u transaction=%lu lookup_cycles=%u "
            "port_time_unit=maa_cycles commit_tick=%lu\n",
            pageMaterializationTraceOccurrence++, execution->key.tokenTile,
            execution->key.generation, execution->key.incarnation,
            key.incarnation,
            execution->page, line, transactionID,
            InactiveProducerLinePayloadCapture::PortAccessCycles,
            commitTick);
    return true;
}

void
MAA::finishPageMaterialization(
    const HybridConsumerContextQueue::ContextKey &key)
{
    PageMaterializationExecution *execution =
        findPageMaterializationExecution(key);
    HybridConsumerContextQueue::Snapshot snapshot;
    panic_if(execution == nullptr || !execution->pageActive ||
                 !directRetirementContexts.materializationPageComplete(
                     key, execution->page) ||
                 !directRetirementContexts.snapshot(key, &snapshot),
             "Page materializer completed without exact page ownership\n");
    for (const PageMaterializationCommit &commit :
         pageMaterializationCommits) {
        panic_if(commit.active && sameDirectRetirementKey(
                     commit.directStaged ? commit.owner : commit.request.owner,
                     key),
                 "Page materializer released SPD before a line commit\n");
    }
    panic_if(hasDirectRetirementOutstandingOwner(key),
             "Page materializer released SPD before a cache response\n");
    for (uint8_t port = 0;
         port < DirectRetirementPortRetry<Packet>::PortCount; ++port) {
        PacketPtr packet = directRetirementRetryPackets.packet(port);
        if (packet == nullptr)
            continue;
        auto *state = dynamic_cast<DirectRetirementSenderState *>(
            packet->senderState);
        panic_if(state == nullptr || state->callbackPort != port,
                 "Page materializer observed malformed shared retry state\n");
        panic_if(sameDirectRetirementKey(state->request.owner, key),
                 "Page materializer released SPD with an owned retry\n");
    }
    // A final same-line direct capture can commit and finish the page before
    // servicePageMaterialization reaches the table.  Exact-owner teardown
    // prevents that already-closed fallback from reaching a later lifetime.
    (void)inactivePayloadFallbacks.clearOwner(key);
    if (sameDirectRetirementKey(inactivePayloadLookup.request.owner, key))
        inactivePayloadLookup = InactivePayloadLookup{};
    if (sameDirectRetirementKey(inactiveMaskedFragmentLookup.request.owner,
                                key))
        inactiveMaskedFragmentLookup = InactiveMaskedFragmentLookup{};
    const uint8_t page = execution->page;
    const int destination = execution->destinationTile;
    const int wordBytes = execution->wordBytes;
    spd->setTileFinished(destination, wordBytes);
    setTileReady(destination, wordBytes);
    ++execution->pagesMaterialized;
    stats.page_materialization_pages++;
    execution->pageActive = false;
    execution->page = HybridConsumerPipeline::NoProducerPage;
    execution->destinationTile = -1;
    DPRINTF(MAAVirtualTrace,
            "event=page_materialization_page_ready schema=1 occurrence=%lu "
            "token=%u generation=%lu incarnation=%lu page=%u "
            "destination=%d lines=%u reads=%u forwarded_lines=%u "
            "staged_direct_lines=%u "
            "cache_read_fallback_lines=%u pages_materialized=%u\n",
            pageMaterializationTraceOccurrence++, key.tokenTile,
            key.generation, key.incarnation, page, destination,
            directRetirementContexts.producerPageLines(key),
            snapshot.readsAccepted,
            execution->forwardedLines,
            execution->stagedDirectLines,
            execution->cacheReadFallbackLines,
            execution->pagesMaterialized);
    if (snapshot.complete) {
        panic_if(virtualPageGeneration[key.tokenTile] != key.generation,
                 "Page materializer token generation changed before retire\n");
        virtualPageConsumedGeneration[key.tokenTile] = key.generation;
        panic_if(execution->pagesMaterialized !=
                     HybridConsumerPipeline::ProducerPages ||
            execution->forwardedLines + execution->stagedDirectLines +
                             execution->cacheReadFallbackLines !=
                         snapshot.lines ||
                     snapshot.producerLineAcks +
                             snapshot.producerPageFallbackLines !=
                         snapshot.lines,
                 "Page materializer did not close exact payload/ACK "
                 "authority\n");
        const InactiveProducerLinePayloadCapture::Key payloadKey{
            key.tokenTile, key.generation,
            virtualPagePayloadIncarnation[key.tokenTile],
            execution->backingAddress};
        if (inactiveProducerLinePayloadCapture.active(payloadKey)) {
            panic_if(inactiveProducerLinePayloadCapture.summary(payloadKey)
                         .storedLines != 0,
                     "Page materializer retired with live exact payloads\n");
        }
        uint16_t fallbackReadsPerPage = 0;
        for (uint16_t fallback : execution->cacheReadFallbackLinesPerPage)
            fallbackReadsPerPage += fallback;
        panic_if(fallbackReadsPerPage != execution->cacheReadFallbackLines,
                 "Page materializer fallback page "
                 "attribution did not close\n");
        const auto globalFirstOwnerConflicts = static_cast<uint64_t>(
            stats.page_materialization_inactive_payload_first_owner_conflicts
                .value());
        const auto globalLatestOwnerOverwrites = static_cast<uint64_t>(
            stats.page_materialization_inactive_payload_latest_owner_overwrites
                .value());
        const auto globalLatestOwnerEvictions = static_cast<uint64_t>(
            stats.page_materialization_inactive_payload_latest_owner_evictions
                .value());
        DPRINTF(MAAVirtualTrace,
                "event=page_materialization_summary schema=1 "
                "occurrence=%lu token=%u generation=%lu incarnation=%lu "
                "payload_incarnation=%lu "
                "pages=%u lines=%u forwarded_lines=%u staged_direct_lines=%u "
                "cache_read_fallback_lines=%u producer_line_acks=%u "
                "page_fallback_lines=%u exact_closure=1 "
                "dispatch_fallbacks=%lu "
                "inactive_payload_capacity=%u "
                "inactive_payload_conflict_policy=%s "
                "global_inactive_payload_captures=%lu "
                "global_inactive_payload_replays=%lu "
                "global_inactive_payload_conflicts=%lu "
                "global_inactive_payload_drops=%lu "
                "global_inactive_payload_first_owner_conflicts=%lu "
                "global_inactive_payload_latest_owner_overwrites=%lu "
                "global_inactive_payload_latest_owner_evictions=%lu "
                "global_inactive_payload_write_port_stalls=%lu "
                "global_inactive_payload_read_port_stalls=%lu "
                "inactive_payload_page0_fallback_reads=%u "
                "inactive_payload_page1_fallback_reads=%u "
                "inactive_payload_page2_fallback_reads=%u "
                "inactive_payload_page3_fallback_reads=%u "
                "global_inactive_payload_lookup_hits=%lu "
                "global_inactive_payload_lookup_misses=%lu "
                "global_inactive_payload_high_water=%lu "
                "inactive_payload_ram_payload_bits=%lu "
                "inactive_payload_ram_tag_bits=%lu "
                "inactive_payload_descriptor_bits=%lu "
                "inactive_payload_read_port_state_bits=%lu "
                "inactive_payload_write_port_state_bits=%lu "
                "inactive_payload_output_payload_bits=%lu "
                "inactive_payload_output_tag_bits=%lu "
                "inactive_payload_maa_lookup_control_bits=%lu "
                "inactive_payload_persistent_incarnation_bits=%lu "
                "inactive_payload_hardware_total_bits=%lu "
                "inactive_payload_bytes=%lu "
                "inactive_payload_tag_control_bytes=%lu "
                "inactive_payload_read_pipeline_payload_bytes=%lu "
                "inactive_payload_lookup_latch_control_bytes=%lu "
                "inactive_payload_host_capture_object_bytes=%lu "
                "inactive_payload_host_lookup_object_bytes=%lu\n",
                pageMaterializationTraceOccurrence++, key.tokenTile,
                key.generation, key.incarnation, payloadKey.incarnation,
                execution->pagesMaterialized, snapshot.lines,
                execution->forwardedLines,
                execution->stagedDirectLines,
                execution->cacheReadFallbackLines,
                snapshot.producerLineAcks,
                snapshot.producerPageFallbackLines,
                static_cast<uint64_t>(
                    stats.page_materialization_dispatch_fallbacks.value()),
                inactive_page_payload_capture_lines,
                InactiveProducerLinePayloadCapture::conflictPolicyName(),
                static_cast<uint64_t>(
                    stats.page_materialization_inactive_payload_captures
                        .value()),
                static_cast<uint64_t>(
                    stats.page_materialization_inactive_payload_replays
                        .value()),
                static_cast<uint64_t>(
                    stats.page_materialization_inactive_payload_conflicts
                        .value()),
                static_cast<uint64_t>(
                    stats.page_materialization_inactive_payload_drops
                        .value()),
                globalFirstOwnerConflicts,
                globalLatestOwnerOverwrites,
                globalLatestOwnerEvictions,
                static_cast<uint64_t>(stats
                    .page_materialization_inactive_payload_write_port_stalls
                    .value()),
                static_cast<uint64_t>(stats
                    .page_materialization_inactive_payload_read_port_stalls
                    .value()),
                execution->cacheReadFallbackLinesPerPage[0],
                execution->cacheReadFallbackLinesPerPage[1],
                execution->cacheReadFallbackLinesPerPage[2],
                execution->cacheReadFallbackLinesPerPage[3],
                static_cast<uint64_t>(
                    stats.page_materialization_inactive_payload_lookup_hits
                        .value()),
                static_cast<uint64_t>(
                    stats.page_materialization_inactive_payload_lookup_misses
                        .value()),
                static_cast<uint64_t>(
                    stats.page_materialization_inactive_payload_high_water
                        .value()),
                InactiveProducerLinePayloadCapture::provisionedPayloadBits(
                    inactive_page_payload_capture_lines),
                InactiveProducerLinePayloadCapture::provisionedTagBits(
                    inactive_page_payload_capture_lines),
                InactiveProducerLinePayloadCapture::provisionedDescriptorBits(
                    inactive_page_payload_capture_lines),
                InactiveProducerLinePayloadCapture::
                    provisionedReadPortStateBits(
                        inactive_page_payload_capture_lines),
                InactiveProducerLinePayloadCapture::
                    provisionedWritePortStateBits(
                        inactive_page_payload_capture_lines),
                inactive_page_payload_capture_lines == 0 ? 0 :
                    InactiveProducerLinePayloadCapture::OutputPayloadBits,
                InactiveProducerLinePayloadCapture::
                    provisionedOutputTagBits(
                        inactive_page_payload_capture_lines),
                inactive_page_payload_capture_lines == 0 ? 0 :
                    InactiveProducerLinePayloadCapture::MAALookupControlBits,
                InactiveProducerLinePayloadCapture::
                    provisionedMAAPersistentStateBits(
                        inactive_page_payload_capture_lines, num_tiles),
                InactiveProducerLinePayloadCapture::
                    provisionedCombinedTotalBits(
                        inactive_page_payload_capture_lines, num_tiles),
                InactiveProducerLinePayloadCapture::provisionedPayloadBytes(
                    inactive_page_payload_capture_lines),
                InactiveProducerLinePayloadCapture::provisionedControlBytes(
                    inactive_page_payload_capture_lines),
                InactiveProducerLinePayloadCapture::
                    provisionedReadPipelinePayloadBytes(
                        inactive_page_payload_capture_lines),
                inactive_page_payload_capture_lines == 0 ? 0 :
                    InactiveProducerLinePayloadCapture::bitsToBytes(
                        InactiveProducerLinePayloadCapture::
                            MAALookupControlBits),
                sizeof(inactiveProducerLinePayloadCapture),
                sizeof(inactivePayloadLookup) +
                    sizeof(inactivePayloadFallbacks));
        panic_if(!directRetirementContexts.retire(key),
             "Page materializer could not retire its 16K lifetime\n");
        (void)directRetirementEarlyLineLedger.clear(
            {key.tokenTile, key.generation, execution->backingAddress});
        const auto payloadClear =
            inactiveProducerLinePayloadCapture.clear(payloadKey);
        stats.page_materialization_inactive_payload_drops +=
            payloadClear.discardedLines;
        const auto maskedClear = inactiveMaskedFragmentRetention.clear(
            {key.tokenTile, key.generation,
             virtualPagePayloadIncarnation[key.tokenTile],
             execution->backingAddress});
        if (maskedClear)
            stats.page_materialization_inactive_masked_clears++;
        *execution = PageMaterializationExecution{};
        stats.page_materialization_retirements++;
        DPRINTF(MAAVirtualTrace,
                "event=page_materialization_retire schema=1 occurrence=%lu "
                "token=%u generation=%lu incarnation=%lu pages=%u\n",
                pageMaterializationTraceOccurrence++, key.tokenTile,
                key.generation, key.incarnation,
                HybridConsumerPipeline::ProducerPages);
    }
    scheduleDispatchInstructionEvent();
    scheduleIssueInstructionEvent();
}

void
MAA::schedulePageMaterializationEvent(int latency)
{
    const Tick when = getClockEdge(Cycles(latency));
    if (!pageMaterializationEvent.scheduled())
        schedule(pageMaterializationEvent, when);
    else if (when < pageMaterializationEvent.when())
        reschedule(pageMaterializationEvent, when);
}

void
MAA::servicePageMaterialization()
{
    if (directRetirementContexts.activeContexts(
            HybridConsumerPipeline::Mode::MaterializePages) == 0)
        return;

    Tick nextCommit = MaxTick;
    for (PageMaterializationCommit &commit :
         pageMaterializationCommits) {
        if (!commit.active)
            continue;
        if (commit.readyTick > curTick()) {
            nextCommit = std::min(nextCommit, commit.readyTick);
            continue;
        }
        const bool directStaged = commit.directStaged;
        const auto owner = directStaged ? commit.owner : commit.request.owner;
        const uint16_t line = directStaged ? commit.line :
            commit.request.request.line;
        PageMaterializationExecution *execution =
            findPageMaterializationExecution(owner);
        const auto request = commit.request;
        panic_if(execution == nullptr || !execution->pageActive ||
                     line /
                             directRetirementContexts.producerPageLines(
                                 owner) != execution->page,
                 "Page materializer SPD commit lost page ownership\n");
        const std::byte *payload = directStaged ? nullptr :
            directRetirementContexts.bufferData(request);
        panic_if(!directStaged && payload == nullptr,
                 "Page materializer SPD commit lost its line buffer\n");
        const uint16_t pageLines =
            directRetirementContexts.producerPageLines(owner);
        const uint16_t pageLine = line % pageLines;
        panic_if(!sameDirectRetirementKey(execution->key, owner) ||
                     (!directStaged &&
                      virtualPageGeneration[request.owner.tokenTile] !=
                          request.owner.generation) ||
                     (directStaged &&
                      virtualPageGeneration[owner.tokenTile] !=
                          owner.generation),
                 "Page materializer batched wakeup lost exact generation "
                 "ownership\n");
        const uint16_t wordsPerLine =
            HybridConsumerPipeline::LineBytes / execution->wordBytes;
        const uint32_t firstElement = pageLine * wordsPerLine;
        for (uint16_t word = 0; word < wordsPerLine; ++word) {
            if (directStaged) {
                spd->setFakeData(execution->destinationTile,
                                 firstElement + word,
                                 execution->wordBytes);
            } else if (execution->wordBytes == sizeof(uint32_t)) {
                uint32_t value = 0;
                std::memcpy(&value,
                            payload + word * sizeof(value), sizeof(value));
                spd->setData<uint32_t>(execution->destinationTile,
                                       firstElement + word, value);
            } else {
                uint64_t value = 0;
                std::memcpy(&value,
                            payload + word * sizeof(value), sizeof(value));
                spd->setData<uint64_t>(execution->destinationTile,
                                       firstElement + word, value);
            }
        }
        if (HybridConsumerPipeline::isEarlyWakeupLine(
                pageLine, pageLines,
                static_cast<uint8_t>(page_materialization_wakeup_batches))) {
            // This does not grant a tile-ready credit. It only rechecks
            // blocked dependents after a selected fully committed line; the
            // page-level setTileReady() control path remains authoritative.
            spd->wakeup_waiting_units(execution->destinationTile);
            DPRINTF(MAAVirtualTrace,
                    "event=page_materialization_batched_wakeup schema=1 "
                    "occurrence=%lu token=%u generation=%lu incarnation=%lu "
                    "page=%u line=%u page_lines=%u batches=%u "
                    "destination=%d\n",
                    pageMaterializationTraceOccurrence++,
                    owner.tokenTile, owner.generation, owner.incarnation,
                    execution->page, pageLine,
                    pageLines, page_materialization_wakeup_batches,
                    execution->destinationTile);
        }
        commit = {};
        panic_if(!(directStaged
                       ? directRetirementContexts.completeMaterializeDirect(
                             owner, line)
                       : directRetirementContexts.completeMaterialize(
                             request)),
                 "Page materializer rejected an exact SPD line commit\n");
        DPRINTF(MAAVirtualTrace,
                "event=page_materialization_line_commit schema=1 "
                "occurrence=%lu token=%u generation=%lu incarnation=%lu "
                "page=%u line=%u destination=%d\n",
                pageMaterializationTraceOccurrence++,
                owner.tokenTile, owner.generation, owner.incarnation,
                execution->page, line, execution->destinationTile);
        if (directRetirementContexts.materializationPageComplete(
                owner, execution->page)) {
            finishPageMaterialization(owner);
        }
    }

    auto discardUnsentPacket = [](PacketPtr packet) {
        auto *state = dynamic_cast<DirectRetirementSenderState *>(
            packet->popSenderState());
        panic_if(state == nullptr,
                 "Unsent page-materializer packet lost sender state\n");
        delete state;
        delete packet;
    };

    for (uint8_t port = 0;
         port < DirectRetirementPortRetry<Packet>::PortCount; ++port) {
        PacketPtr packet = directRetirementRetryPackets.packet(port);
        if (packet == nullptr)
            continue;
        auto *state = dynamic_cast<DirectRetirementSenderState *>(
            packet->senderState);
        if (state == nullptr ||
            findPageMaterializationExecution(state->request.owner) == nullptr)
            continue;
        panic_if(state->callbackPort != port,
                 "Page-materializer retry lost exact port identity\n");
        const Addr paddr = packet->getAddr();
        const auto deferred = my_deferred_pkt_map.find(paddr);
        if (hasOutstandingPacket(paddr) ||
            hasDirectRetirementOutstandingAddress(paddr) ||
            (deferred != my_deferred_pkt_map.end() &&
             !deferred->second.empty())) {
            schedulePageMaterializationEvent(1);
            continue;
        }
        uint8_t actualPort = DirectRetirementPortRetry<Packet>::PortCount;
        if (!sendPacketCache(packet, &actualPort)) {
            panic_if(actualPort != port,
                     "Page-materializer retry changed physical port\n");
            continue;
        }
        const auto request = state->request;
        panic_if(actualPort != port ||
                     !directRetirementRetryPackets.release(port, packet) ||
                     !reserveDirectRetirementRequest(paddr, request),
                 "Page-materializer retry lost exact request ownership\n");
        DPRINTF(MAAVirtualTrace,
                "event=page_materialization_read_issue schema=1 "
                "occurrence=%lu token=%u generation=%lu incarnation=%lu "
                "line=%u port=%u retry=1\n",
                pageMaterializationTraceOccurrence++,
                request.owner.tokenTile, request.owner.generation,
                request.owner.incarnation, request.request.line, port);
    }

    bool resolvedInactivePayloadMiss = false;
    HybridConsumerContextQueue::Request resolvedInactivePayloadRequest;
    if (inactiveMaskedFragmentLookup.timing.pending() ||
        inactivePayloadLookup.timing.pending()) {
        const bool masked = inactiveMaskedFragmentLookup.timing.pending();
        const bool ready = masked
            ? inactiveMaskedFragmentLookup.timing.ready(
                  static_cast<uint64_t>(curCycle()))
            : inactivePayloadLookup.timing.ready(
                  static_cast<uint64_t>(curCycle()));
        if (!ready) {
            schedulePageMaterializationEvent(1);
            return;
        }
        if (consumeInactiveProducerPayload()) {
            schedulePageMaterializationEvent(1);
            return;
        }
    }

    uint8_t resolvedInactivePayloadFallback =
        InactivePayloadFallbackTable::NoSlot;
    resolvedInactivePayloadMiss = inactivePayloadFallbacks.resolve(
        directRetirementContexts, &resolvedInactivePayloadRequest,
        &resolvedInactivePayloadFallback) ==
        InactivePayloadFallbackTable::ResolveResult::Rebound;

    for (unsigned attempt = 0;
         attempt < DirectRetirementRequestRecordCount; ++attempt) {
        const bool completingInactivePayloadMiss =
            resolvedInactivePayloadMiss;
        const auto request = completingInactivePayloadMiss
            ? resolvedInactivePayloadRequest
            : directRetirementContexts.pendingRead(
                HybridConsumerPipeline::Mode::MaterializePages);
        resolvedInactivePayloadMiss = false;
        if (request.request.kind == HybridConsumerPipeline::Kind::None)
            break;
        if (!completingInactivePayloadMiss) {
            // Probe exactly the selected materializer line. Both a hit and a
            // miss enter the one-entry, one-MAA-cycle lookup pipeline; only a
            // completed miss reaches the unchanged coherent fallback below.
            const auto lookup = startInactiveProducerPayloadLookup(request);
            if (lookup == InactivePayloadLookupStart::Started ||
                lookup == InactivePayloadLookupStart::ReadPortBusy) {
                schedulePageMaterializationEvent(1);
                break;
            }
        }
        PacketPtr packet = makePageMaterializationPacket(request);
        auto *state = dynamic_cast<DirectRetirementSenderState *>(
            packet->senderState);
        panic_if(state == nullptr || state->callbackPort >=
                     DirectRetirementPortRetry<Packet>::PortCount,
                 "Page-materializer candidate lost sender identity\n");
        const uint8_t port = state->callbackPort;
        if (directRetirementRetryPackets.occupied(port)) {
            discardUnsentPacket(packet);
            if (completingInactivePayloadMiss) {
                // Rebinding authenticates a free buffer for this exact line,
                // but it does not make it the context's current scheduler
                // candidate. Keep its fixed entry and retry without defer.
                schedulePageMaterializationEvent(1);
                break;
            }
            panic_if(!directRetirementContexts.defer(request),
                     "Page materializer could not rotate a blocked port\n");
            continue;
        }
        const Addr paddr = packet->getAddr();
        const auto deferred = my_deferred_pkt_map.find(paddr);
        if (hasOutstandingPacket(paddr) ||
            hasDirectRetirementOutstandingAddress(paddr) ||
            (deferred != my_deferred_pkt_map.end() &&
             !deferred->second.empty())) {
            discardUnsentPacket(packet);
            schedulePageMaterializationEvent(1);
            break;
        }
        uint8_t actualPort = DirectRetirementPortRetry<Packet>::PortCount;
        if (!sendPacketCache(packet, &actualPort)) {
            panic_if(actualPort != port ||
                         !directRetirementRetryPackets.arm(port, packet) ||
                         !directRetirementContexts.accept(request),
                     "Page materializer could not retain a refused request\n");
            if (completingInactivePayloadMiss)
                panic_if(!inactivePayloadFallbacks.clear(
                             resolvedInactivePayloadFallback),
                         "Page materializer lost its rebound fallback\n");
            continue;
        }
        panic_if(actualPort != port ||
                     !reserveDirectRetirementRequest(paddr, request) ||
                     !directRetirementContexts.accept(request),
                 "Page materializer could not claim an issued request\n");
        if (completingInactivePayloadMiss)
            panic_if(!inactivePayloadFallbacks.clear(
                         resolvedInactivePayloadFallback),
                     "Page materializer lost its rebound fallback\n");
        DPRINTF(MAAVirtualTrace,
                "event=page_materialization_read_issue schema=1 "
                "occurrence=%lu token=%u generation=%lu incarnation=%lu "
                "line=%u port=%u retry=0\n",
                pageMaterializationTraceOccurrence++,
                request.owner.tokenTile, request.owner.generation,
                request.owner.incarnation, request.request.line, port);
    }

    if (nextCommit != MaxTick) {
        if (!pageMaterializationEvent.scheduled())
            schedule(pageMaterializationEvent, nextCommit);
        else if (nextCommit < pageMaterializationEvent.when())
            reschedule(pageMaterializationEvent, nextCommit);
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

std::array<uint16_t, MAA::LogicalPageScheduler::PhysicalFrames>
MAA::logicalPageFrameIDs(unsigned maaID) const
{
    panic_if(maaID >= num_maas || num_tiles % num_maas != 0,
             "Cannot derive logical frame IDs for MAA %u\n", maaID);
    const unsigned lanesPerMAA = num_tiles / num_maas;
    const unsigned reservedLanes = LogicalPageScheduler::PhysicalFrames *
        LogicalPageScheduler::MaxFrameLaneSpan;
    panic_if(lanesPerMAA < reservedLanes,
             "MAA %u has only %u SPD lanes for %u reserved lanes\n",
             maaID, lanesPerMAA, reservedLanes);
    const unsigned first = maaID * lanesPerMAA + lanesPerMAA - reservedLanes;
    std::array<uint16_t, LogicalPageScheduler::PhysicalFrames> frames{};
    for (unsigned index = 0; index < frames.size(); ++index)
        frames[index] = first +
            index * LogicalPageScheduler::MaxFrameLaneSpan;
    return frames;
}

bool
MAA::logicalTileReservedLane(int tileID) const
{
    if (!logical_tile_page_scheduler || tileID < 0 ||
        tileID >= static_cast<int>(num_tiles)) {
        return false;
    }
    for (unsigned maaID = 0; maaID < num_maas; ++maaID) {
        const auto frames = logicalPageFrameIDs(maaID);
        for (const uint16_t frame : frames) {
            if (tileID >= frame && tileID <
                frame + LogicalPageScheduler::MaxFrameLaneSpan) {
                return true;
            }
        }
    }
    return false;
}

bool
MAA::logicalCompletionLaneOwned(int tileID) const
{
    return tileID >= 0 && tileID < static_cast<int>(
        responseBearingPublishCompletionOwner.size()) &&
        responseBearingPublishCompletionOwner[tileID] != -1;
}

bool
MAA::instructionTouchesLogicalReservedFrame(
    const Instruction &instruction) const
{
    if (!logical_tile_page_scheduler || instruction.logicalPageManaged)
        return false;
    const int tiles[] = {
        instruction.src1SpdID, instruction.src2SpdID,
        instruction.dst1SpdID, instruction.dst2SpdID,
        instruction.condSpdID,
    };
    for (const int tile : tiles) {
        if (tile < 0)
            continue;
        int lanes = 1;
        switch (instruction.datatype) {
          case Instruction::DataType::UINT64_TYPE:
          case Instruction::DataType::INT64_TYPE:
          case Instruction::DataType::FLOAT64_TYPE:
            lanes = 2;
            break;
          default:
            break;
        }
        for (int lane = 0; lane < lanes; ++lane) {
            if (logicalTileReservedLane(tile + lane))
                return true;
        }
    }
    return false;
}

bool
MAA::logicalPageUsesRegister(
    int maaID, int firstRegister, int registerWords) const
{
    if (!logical_tile_page_scheduler || maaID < 0 ||
        maaID >= static_cast<int>(num_maas) || firstRegister < 0 ||
        registerWords <= 0) {
        return false;
    }
    const int candidateEnd = firstRegister + registerWords;
    const auto &execution = logicalPageExecutions[maaID];
    if (!execution.active ||
        !execution.architectural.isLogicalALUScalar()) {
        return false;
    }
    const int leasedFirst = execution.architectural.src1RegID;
    const auto datatype = execution.architectural.datatype;
    const int leasedWords =
        datatype == Instruction::DataType::UINT64_TYPE ||
                datatype == Instruction::DataType::INT64_TYPE ||
                datatype == Instruction::DataType::FLOAT64_TYPE
            ? 2
            : 1;
    const int leasedEnd = leasedFirst + leasedWords;
    return firstRegister < leasedEnd && leasedFirst < candidateEnd;
}

bool
MAA::configureLogicalPageSource(
    unsigned maaID, uint16_t descriptor, Addr backing, uint8_t datatype,
    uint64_t *generation)
{
    panic_if(maaID >= num_maas || descriptor >=
                 LogicalPageScheduler::LogicalDescriptors ||
                 generation == nullptr,
             "Invalid logical page source descriptor %u/%u\n",
             maaID, descriptor);
    const bool fp32 = datatype == static_cast<uint8_t>(
        Instruction::DataType::FLOAT32_TYPE);
    const bool fp64 = datatype == static_cast<uint8_t>(
        Instruction::DataType::FLOAT64_TYPE);
    panic_if(!fp32 && !fp64,
             "Logical page scheduler supports only FP32/FP64, got %u\n",
             datatype);
    auto &state = logicalPageDescriptors[maaID][descriptor];
    const uint8_t wordBytes = fp32 ? sizeof(float) : sizeof(double);
    const auto type = fp32 ? LogicalPageScheduler::DataType::Float32 :
                             LogicalPageScheduler::DataType::Float64;
    if (state.configured && state.config.backingAddress == backing &&
        state.config.dataType == type &&
        state.config.wordBytes == wordBytes &&
        state.config.readyPageMask == LogicalPageScheduler::AllPagesReady) {
        *generation = state.config.generation;
        return true;
    }
    LogicalPageScheduler::DescriptorConfig config;
    config.generation = state.configured ? state.config.generation + 1 : 1;
    panic_if(config.generation == 0,
             "Logical source descriptor generation wrapped\n");
    config.backingAddress = backing;
    config.backingBytes = uint64_t{LogicalPageScheduler::LogicalElements} *
        wordBytes;
    config.dataType = type;
    config.wordBytes = wordBytes;
    config.readyPageMask = LogicalPageScheduler::AllPagesReady;
    const auto status = logicalPageSchedulers[maaID]->configure(
        descriptor, config);
    panic_if(status != LogicalPageScheduler::Status::Accepted,
             "Logical source descriptor %u configure failed with %u\n",
             descriptor, static_cast<unsigned>(status));
    state.configured = true;
    state.config = config;
    *generation = config.generation;
    return true;
}

bool
MAA::configureLogicalPageDestination(
    unsigned maaID, uint16_t descriptor, Addr backing, uint8_t datatype,
    uint64_t *generation)
{
    panic_if(maaID >= num_maas || descriptor >=
                 LogicalPageScheduler::LogicalDescriptors ||
                 generation == nullptr,
             "Invalid logical page destination descriptor %u/%u\n",
             maaID, descriptor);
    const bool fp32 = datatype == static_cast<uint8_t>(
        Instruction::DataType::FLOAT32_TYPE);
    const bool fp64 = datatype == static_cast<uint8_t>(
        Instruction::DataType::FLOAT64_TYPE);
    panic_if(!fp32 && !fp64,
             "Logical page scheduler supports only FP32/FP64, got %u\n",
             datatype);
    auto &state = logicalPageDescriptors[maaID][descriptor];
    LogicalPageScheduler::DescriptorConfig config;
    config.generation = state.configured ? state.config.generation + 1 : 1;
    panic_if(config.generation == 0,
             "Logical destination descriptor generation wrapped\n");
    config.backingAddress = backing;
    config.wordBytes = fp32 ? sizeof(float) : sizeof(double);
    config.backingBytes = uint64_t{LogicalPageScheduler::LogicalElements} *
        config.wordBytes;
    config.dataType = fp32 ? LogicalPageScheduler::DataType::Float32 :
                             LogicalPageScheduler::DataType::Float64;
    config.readyPageMask = 0;
    const auto status = logicalPageSchedulers[maaID]->configure(
        descriptor, config);
    panic_if(status != LogicalPageScheduler::Status::Accepted,
             "Logical destination descriptor %u configure failed with %u\n",
             descriptor, static_cast<unsigned>(status));
    state.configured = true;
    state.config = config;
    *generation = config.generation;
    return true;
}

bool
MAA::submitLogicalPageInstruction(
    InstructionPtr instruction, PacketPtr completionPacket)
{
    panic_if(!logical_tile_page_scheduler || instruction == nullptr ||
                 completionPacket == nullptr ||
                 instruction->maa_id < 0 ||
                 instruction->maa_id >= static_cast<int>(num_maas),
             "Invalid logical page scheduler submission\n");
    const unsigned maaID = instruction->maa_id;
    auto &execution = logicalPageExecutions[maaID];
    if (execution.active)
        return false;
    panic_if(!instruction->hasLogicalOperands(),
             "Logical page submission lacks logical operands\n");
    const uint8_t datatype = static_cast<uint8_t>(instruction->datatype);
    const int wordBytes = instruction->WordSize();
    panic_if(wordBytes != sizeof(float) && wordBytes != sizeof(double),
             "Logical page scheduler received unsupported word size %d\n",
             wordBytes);
    panic_if((instruction->isLogicalALUScalar() ||
              instruction->isLogicalALUVector()) &&
                 (instruction->datatype !=
                      Instruction::DataType::FLOAT32_TYPE &&
                  instruction->datatype !=
                      Instruction::DataType::FLOAT64_TYPE),
             "Logical ALU scheduler supports only FP32/FP64 arithmetic\n");
    panic_if((instruction->isLogicalALUScalar() ||
              instruction->isLogicalALUVector()) &&
                 (instruction->optype < Instruction::OPType::ADD_OP ||
                  instruction->optype > Instruction::OPType::MAX_OP),
             "Logical ALU scheduler supports only ADD/SUB/MUL/DIV/MIN/MAX, "
             "got operation %u\n",
             static_cast<unsigned>(instruction->optype));
    if (instruction->isLogicalStream()) {
        const int completion = instruction->logicalCompletionSpdID;
        panic_if(completion < 0 ||
                     completion + wordBytes / sizeof(uint32_t) >
                         static_cast<int>(num_tiles),
                 "Logical stream lacks a valid completion span\n");
        for (int lane = 0; lane < wordBytes / sizeof(uint32_t); ++lane) {
            panic_if(logicalTileReservedLane(completion + lane) ||
                         logicalCompletionLaneOwned(completion + lane) ||
                         ifile->hasTileReference(maaID, completion + lane),
                     "Logical stream completion span aliases reserved/live "
                     "tile %d\n", completion + lane);
        }
    }

    LogicalPageScheduler::Operation operation;
    uint64_t src1Generation = 0;
    uint64_t src2Generation = 0;
    uint64_t dstGeneration = 0;
    if (instruction->isLogicalStreamLoad()) {
        operation.shape = LogicalPageScheduler::Shape::Materialize;
        operation.destination = instruction->dst1LogicalID;
        configureLogicalPageDestination(
            maaID, operation.destination,
            instruction->logicalSourceBackingAddr, datatype,
            &dstGeneration);
    } else if (instruction->isLogicalStreamStore()) {
        operation.shape = LogicalPageScheduler::Shape::DenseStreamStore;
        operation.source1 = instruction->src1LogicalID;
        operation.destination = LogicalDenseStoreDescriptor;
        const auto &source = logicalPageDescriptors[maaID][operation.source1];
        const auto expectedType = instruction->datatype ==
                Instruction::DataType::FLOAT32_TYPE
            ? LogicalPageScheduler::DataType::Float32
            : LogicalPageScheduler::DataType::Float64;
        panic_if(!source.configured ||
                     source.config.dataType != expectedType ||
                     source.config.readyPageMask !=
                         LogicalPageScheduler::AllPagesReady,
                 "Logical STREAM_ST source descriptor %u is not a complete "
                 "typed generation\n", operation.source1);
        src1Generation = source.config.generation;
        configureLogicalPageDestination(
            maaID, operation.destination,
            instruction->logicalDestinationBackingAddr, datatype,
            &dstGeneration);
    } else if (instruction->isLogicalALUScalar()) {
        operation.shape = LogicalPageScheduler::Shape::UnaryScalarAlu;
        operation.source1 = instruction->src1LogicalID;
        operation.destination = instruction->dst1LogicalID;
        configureLogicalPageSource(
            maaID, operation.source1,
            instruction->logicalSourceBackingAddr, datatype,
            &src1Generation);
        configureLogicalPageDestination(
            maaID, operation.destination, instruction->backingAddr,
            datatype, &dstGeneration);
    } else if (instruction->isLogicalALUVector()) {
        operation.shape = LogicalPageScheduler::Shape::BinaryVectorAlu;
        operation.source1 = instruction->src1LogicalID;
        operation.source2 = instruction->src2LogicalID;
        operation.destination = instruction->dst1LogicalID;
        configureLogicalPageSource(
            maaID, operation.source1,
            instruction->logicalSourceBackingAddr, datatype,
            &src1Generation);
        if (operation.source2 == operation.source1) {
            src2Generation = src1Generation;
        } else {
            configureLogicalPageSource(
                maaID, operation.source2,
                instruction->logicalSource2BackingAddr, datatype,
                &src2Generation);
        }
        configureLogicalPageDestination(
            maaID, operation.destination,
            instruction->logicalDestinationBackingAddr, datatype,
            &dstGeneration);
    } else {
        panic("Unsupported logical scheduler opcode %u\n",
              static_cast<unsigned>(instruction->opcode));
    }

    ++logicalPageArchitecturalSequence;
    panic_if(logicalPageArchitecturalSequence == 0,
             "Logical architectural sequence wrapped\n");
    instruction->src1LogicalGeneration = src1Generation;
    instruction->src2LogicalGeneration = src2Generation;
    instruction->dst1LogicalGeneration = dstGeneration;
    execution.active = true;
    execution.actionInFlight = false;
    execution.actionDispatched = false;
    execution.architectural = *instruction;
    execution.completionPacket = completionPacket;
    execution.operation = operation;
    execution.nextPage = 0;
    execution.architecturalSequence = logicalPageArchitecturalSequence;
    if (instruction->isLogicalStream()) {
        reserveResponseBearingPublishCompletion(
            maaID, instruction->logicalCompletionSpdID, wordBytes);
        spd->setTileIdle(instruction->logicalCompletionSpdID, wordBytes);
        spd->setTileNotReady(instruction->logicalCompletionSpdID, wordBytes);
    }
    DPRINTF(MAAVirtualTrace,
            "event=logical_page_admit schema=1 sequence=%lu maa=%u "
            "opcode=%u operation=%u datatype=%u scalar_reg=%d src1=%d "
            "src1_generation=%lu src2=%d src2_generation=%lu dst=%d "
            "dst_generation=%lu completion=%d pages=4 page_elements=4096\n",
            execution.architecturalSequence, maaID,
            static_cast<unsigned>(instruction->opcode),
            static_cast<unsigned>(instruction->optype), datatype,
            instruction->src1RegID, instruction->src1LogicalID,
            src1Generation, instruction->src2LogicalID, src2Generation,
            instruction->dst1LogicalID, dstGeneration,
            instruction->logicalCompletionSpdID);
    scheduleLogicalSPDEvent();
    return true;
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
             "elements, got %u/%u\n", num_tile_elements,
             physical_tile_elements);
    const uint8_t dataType = static_cast<uint8_t>(instruction->datatype);
    const std::size_t wordBytes = Slice::wordBytes(dataType);
    const std::size_t backingBytes = Slice::backingBytes(dataType);
    panic_if(wordBytes == 0 || backingBytes == 0,
             "Logical SPD live slice does not support datatype %u: %s\n",
             dataType, instruction->print());
    const Addr sourceBase = instruction->logicalSourceBackingAddr;
    const Addr destinationBase = instruction->backingAddr;
    panic_if(sourceBase % backingBytes != 0 ||
                 destinationBase % backingBytes != 0,
             "Logical SPD backing must be aligned to the 16K-element span "
             "(%zu bytes): source=0x%lx destination=0x%lx\n",
             backingBytes, sourceBase, destinationBase);
    const bool backingOverlap =
        sourceBase <= destinationBase
            ? destinationBase - sourceBase < backingBytes
            : sourceBase - destinationBase < backingBytes;
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
        instruction->logicalSourceBackingAddr,
        static_cast<uint32_t>(backingBytes)};
    panic_if(logicalSpdBridge->registerSource(
                 claim.token, static_cast<uint8_t>(
                                  instruction->src1LogicalID), source,
                 dataType) !=
                 Slice::Status::Accepted,
             "Logical SPD source registration failed after ABI validation\n");
    Slice::Admission admission;
    admission.sourceLogical =
        static_cast<uint8_t>(instruction->src1LogicalID);
    admission.destinationLogical =
        static_cast<uint8_t>(instruction->dst1LogicalID);
    admission.destination = {
        instruction->backingAddr, static_cast<uint32_t>(backingBytes)};
    admission.dataType = dataType;
    admission.operation = operation;
    if (wordBytes == sizeof(uint32_t)) {
        const uint32_t scalar =
            rf->getData<uint32_t>(instruction->src1RegID);
        std::memcpy(&admission.scalarBits, &scalar, sizeof(scalar));
    } else {
        const uint64_t scalar =
            rf->getData<uint64_t>(instruction->src1RegID);
        std::memcpy(&admission.scalarBits, &scalar, sizeof(scalar));
    }
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
            "callback=%lu operation=%u datatype=%u word_bytes=%lu "
            "source=0x%lx "
            "destination=0x%lx elements=%lu "
            "mode=%u page_elements=%lu pages=%lu slots=%lu "
            "payload_bytes=%lu packed_metadata_bytes=%lu "
            "source_contract=pre_materialized_backing\n",
            instruction->maa_id, claim.token.generation,
            claim.token.runtimeIdentity, claim.token.identity,
            static_cast<unsigned>(instruction->optype), dataType,
            static_cast<unsigned long>(wordBytes),
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

bool
MAA::dispatchLogicalPageAction(
    unsigned maaID, LogicalPageExecution &execution)
{
    const auto &action = execution.action;
    Instruction instruction;
    instruction.core_id = execution.architectural.core_id;
    instruction.maa_id = maaID;
    instruction.CID = execution.architectural.CID;
    instruction.PC = execution.architectural.PC;
    instruction.datatype = execution.architectural.datatype;
    instruction.optype = execution.architectural.optype;
    instruction.controllerManaged = true;
    instruction.logicalPageManaged = true;
    instruction.controllerTransactionID = action.transaction;
    instruction.controllerPage = action.page;
    instruction.controllerElementOffset = 0;
    instruction.controllerElements = LogicalPageScheduler::ElementsPerPage;
    instruction.src1LogicalGeneration = action.source1Generation;
    instruction.src2LogicalGeneration = action.source2Generation;
    instruction.dst1LogicalGeneration = action.destinationGeneration;
    instruction.src1Status = Instruction::TileStatus::Finished;
    instruction.src2Status = Instruction::TileStatus::Finished;
    instruction.condStatus = Instruction::TileStatus::Finished;
    instruction.dst1Status = Instruction::TileStatus::WaitForService;
    instruction.dst2Status = Instruction::TileStatus::WaitForService;

    const Addr address = action.backingAddress + action.byteOffset;
    switch (action.kind) {
      case LogicalPageScheduler::ActionKind::MaterializeFill:
      case LogicalPageScheduler::ActionKind::Source1Fill:
      case LogicalPageScheduler::ActionKind::Source2Fill:
        instruction.opcode = Instruction::OpcodeType::STREAM_LD;
        instruction.accessType = Instruction::AccessType::READ;
        instruction.dst1SpdID = action.kind ==
                LogicalPageScheduler::ActionKind::MaterializeFill
            ? action.destinationFrame
            : (action.kind == LogicalPageScheduler::ActionKind::Source1Fill
                   ? action.source1Frame
                   : action.source2Frame);
        instruction.baseAddr = address;
        instruction.addrRangeID = getAddrRegion(address);
        panic_if(instruction.addrRangeID < 0,
                 "Logical fill address 0x%lx is unregistered\n", address);
        instruction.minAddr = addrRegions[instruction.addrRangeID].first;
        instruction.maxAddr = addrRegions[instruction.addrRangeID].second;
        break;
      case LogicalPageScheduler::ActionKind::UnaryScalarCompute:
        instruction.opcode = Instruction::OpcodeType::ALU_SCALAR;
        instruction.accessType = Instruction::AccessType::COMPUTE;
        instruction.src1SpdID = action.source1Frame;
        instruction.dst1SpdID = action.destinationFrame;
        instruction.src1RegID = execution.architectural.src1RegID;
        break;
      case LogicalPageScheduler::ActionKind::BinaryVectorCompute:
        instruction.opcode = Instruction::OpcodeType::ALU_VECTOR;
        instruction.accessType = Instruction::AccessType::COMPUTE;
        instruction.src1SpdID = action.source1Frame;
        instruction.src2SpdID = action.source2Frame ==
                LogicalPageScheduler::NoFrame
            ? action.source1Frame
            : action.source2Frame;
        instruction.dst1SpdID = action.destinationFrame;
        break;
      case LogicalPageScheduler::ActionKind::DenseStreamStore:
      case LogicalPageScheduler::ActionKind::DestinationWrite:
        instruction.opcode = Instruction::OpcodeType::STREAM_ST;
        instruction.accessType = Instruction::AccessType::WRITE;
        instruction.src1SpdID = action.kind ==
                LogicalPageScheduler::ActionKind::DenseStreamStore
            ? action.source1Frame
            : action.destinationFrame;
        instruction.controllerDstSlot = instruction.src1SpdID;
        instruction.baseAddr = address;
        instruction.addrRangeID = getAddrRegion(address);
        panic_if(instruction.addrRangeID < 0,
                 "Logical write address 0x%lx is unregistered\n", address);
        instruction.minAddr = addrRegions[instruction.addrRangeID].first;
        instruction.maxAddr = addrRegions[instruction.addrRangeID].second;
        break;
    }

    if (!ifile->pushInstruction(instruction))
        return false;
    if (instruction.dst1SpdID != -1) {
        spd->setTileIdle(instruction.dst1SpdID, instruction.WordSize());
        spd->setTileNotReady(instruction.dst1SpdID, instruction.WordSize());
    }
    if (instruction.src1SpdID != -1) {
        spd->setTileNotReady(instruction.src1SpdID,
                             instruction.getWordSize(
                                 instruction.src1SpdID));
    }
    if (instruction.src2SpdID != -1) {
        // A self-vector has two architectural source references and the
        // native completion returns two credits, so debit both references.
        spd->setTileNotReady(instruction.src2SpdID,
                             instruction.getWordSize(
                                 instruction.src2SpdID));
    }
    DPRINTF(MAAVirtualTrace,
            "event=logical_page_native_dispatch schema=1 sequence=%lu "
            "maa=%u page=%u action=%u transaction=%lu opcode=%u src1=%d "
            "src2=%d dst=%d address=0x%lx bytes=%lu\n",
            execution.architecturalSequence, maaID, action.page,
            static_cast<unsigned>(action.kind), action.transaction,
            static_cast<unsigned>(instruction.opcode), instruction.src1SpdID,
            instruction.src2SpdID, instruction.dst1SpdID, address,
            action.byteLength);
    execution.actionDispatched = true;
    scheduleIssueInstructionEvent(1);
    return true;
}

void
MAA::serviceLogicalPageScheduler()
{
    if (!logical_tile_page_scheduler)
        return;
    for (unsigned maaID = 0; maaID < num_maas; ++maaID) {
        auto &execution = logicalPageExecutions[maaID];
        if (!execution.active)
            continue;
        auto &scheduler = *logicalPageSchedulers[maaID];
        if (execution.actionInFlight) {
            if (!execution.actionDispatched &&
                !dispatchLogicalPageAction(maaID, execution)) {
                scheduleLogicalSPDEvent(1);
            }
            continue;
        }
        if (!scheduler.active()) {
            if (execution.nextPage ==
                LogicalPageScheduler::PagesPerTile) {
                retireLogicalPageInstruction(maaID, execution);
                continue;
            }
            execution.operation.page = execution.nextPage;
            const auto admitted = scheduler.admit(execution.operation);
            panic_if(admitted != LogicalPageScheduler::Status::Accepted,
                     "Logical page %u admission failed with %u\n",
                     execution.nextPage,
                     static_cast<unsigned>(admitted));
            DPRINTF(MAAVirtualTrace,
                    "event=logical_page_begin schema=1 sequence=%lu maa=%u "
                    "page=%u shape=%u\n",
                    execution.architecturalSequence, maaID,
                    execution.nextPage,
                    static_cast<unsigned>(execution.operation.shape));
            ++execution.nextPage;
        }
        LogicalPageScheduler::NativeAction action;
        const auto status = scheduler.nextAction(&action);
        if (status == LogicalPageScheduler::Status::FrameUnavailable) {
            scheduleLogicalSPDEvent(1);
            continue;
        }
        panic_if(status != LogicalPageScheduler::Status::Accepted,
                 "Logical scheduler next action failed with %u\n",
                 static_cast<unsigned>(status));
        execution.action = action;
        execution.actionInFlight = true;
        execution.actionDispatched = false;
        if (!dispatchLogicalPageAction(maaID, execution))
            scheduleLogicalSPDEvent(1);
    }
}

void
MAA::finishLogicalPageAction(InstructionPtr instruction)
{
    panic_if(!logical_tile_page_scheduler || instruction == nullptr ||
                 !instruction->logicalPageManaged ||
                 instruction->maa_id < 0 ||
                 instruction->maa_id >= static_cast<int>(num_maas),
             "Invalid logical page native completion\n");
    const unsigned maaID = instruction->maa_id;
    auto &execution = logicalPageExecutions[maaID];
    const auto &action = execution.action;
    panic_if(!execution.active || !execution.actionInFlight ||
                 !execution.actionDispatched ||
                 instruction->controllerTransactionID != action.transaction ||
                 instruction->controllerPage != action.page ||
                 instruction->datatype != execution.architectural.datatype ||
                 instruction->src1LogicalGeneration !=
                     action.source1Generation ||
                 instruction->src2LogicalGeneration !=
                     action.source2Generation ||
                 instruction->dst1LogicalGeneration !=
                     action.destinationGeneration,
             "Logical native completion lost exact action identity\n");
    const Addr address = action.backingAddress + action.byteOffset;
    switch (action.kind) {
      case LogicalPageScheduler::ActionKind::MaterializeFill:
        panic_if(instruction->opcode != Instruction::OpcodeType::STREAM_LD ||
                     instruction->dst1SpdID != action.destinationFrame ||
                     instruction->baseAddr != address,
                 "Logical materialize completion identity mismatch\n");
        break;
      case LogicalPageScheduler::ActionKind::Source1Fill:
        panic_if(instruction->opcode != Instruction::OpcodeType::STREAM_LD ||
                     instruction->dst1SpdID != action.source1Frame ||
                     instruction->baseAddr != address,
                 "Logical source1 completion identity mismatch\n");
        break;
      case LogicalPageScheduler::ActionKind::Source2Fill:
        panic_if(instruction->opcode != Instruction::OpcodeType::STREAM_LD ||
                     instruction->dst1SpdID != action.source2Frame ||
                     instruction->baseAddr != address,
                 "Logical source2 completion identity mismatch\n");
        break;
      case LogicalPageScheduler::ActionKind::UnaryScalarCompute:
        panic_if(instruction->opcode != Instruction::OpcodeType::ALU_SCALAR ||
                     instruction->src1SpdID != action.source1Frame ||
                     instruction->dst1SpdID != action.destinationFrame ||
                     instruction->optype != execution.architectural.optype ||
                     instruction->src1RegID !=
                         execution.architectural.src1RegID,
                 "Logical unary completion identity mismatch\n");
        break;
      case LogicalPageScheduler::ActionKind::BinaryVectorCompute:
        panic_if(instruction->opcode != Instruction::OpcodeType::ALU_VECTOR ||
                     instruction->src1SpdID != action.source1Frame ||
                     instruction->src2SpdID !=
                         (action.source2Frame ==
                                  LogicalPageScheduler::NoFrame
                              ? action.source1Frame
                              : action.source2Frame) ||
                     instruction->dst1SpdID != action.destinationFrame ||
                     instruction->optype != execution.architectural.optype,
                 "Logical vector completion identity mismatch\n");
        break;
      case LogicalPageScheduler::ActionKind::DenseStreamStore:
        panic_if(instruction->opcode != Instruction::OpcodeType::STREAM_ST ||
                     instruction->src1SpdID != action.source1Frame ||
                     instruction->baseAddr != address,
                 "Logical dense-store completion identity mismatch\n");
        break;
      case LogicalPageScheduler::ActionKind::DestinationWrite:
        panic_if(instruction->opcode != Instruction::OpcodeType::STREAM_ST ||
                     instruction->src1SpdID != action.destinationFrame ||
                     instruction->baseAddr != address,
                 "Logical destination-write completion identity mismatch\n");
        break;
    }
    const auto completed = logicalPageSchedulers[maaID]->complete(action);
    panic_if(completed != LogicalPageScheduler::Status::Accepted,
             "Logical scheduler rejected exact native completion with %u\n",
             static_cast<unsigned>(completed));
    if (execution.operation.destination !=
            LogicalPageScheduler::NoDescriptor &&
        logicalPageSchedulers[maaID]->pageReady(
            execution.operation.destination,
            action.destinationGeneration, action.page)) {
        logicalPageDescriptors[maaID][execution.operation.destination]
            .config.readyPageMask |= uint8_t{1} << action.page;
    }
    DPRINTF(MAAVirtualTrace,
            "event=logical_page_native_complete schema=1 sequence=%lu "
            "maa=%u page=%u action=%u transaction=%lu opcode=%u\n",
            execution.architecturalSequence, maaID, action.page,
            static_cast<unsigned>(action.kind), action.transaction,
            static_cast<unsigned>(instruction->opcode));
    execution.actionInFlight = false;
    execution.actionDispatched = false;
    scheduleLogicalSPDEvent();
}

void
MAA::retireLogicalPageInstruction(
    unsigned maaID, LogicalPageExecution &execution)
{
    panic_if(!execution.active || execution.actionInFlight ||
                 logicalPageSchedulers[maaID]->active() ||
                 execution.nextPage != LogicalPageScheduler::PagesPerTile ||
                 execution.completionPacket == nullptr,
             "Logical architectural retirement attempted before four pages "
             "closed\n");
    if (execution.architectural.isLogicalStream()) {
        const int completion =
            execution.architectural.logicalCompletionSpdID;
        const int wordBytes = execution.architectural.WordSize();
        spd->setTileFinished(completion, wordBytes);
        setTileReady(completion, wordBytes);
        releaseResponseBearingPublishCompletion(
            maaID, completion, wordBytes);
    }
    PacketPtr packet = execution.completionPacket;
    const int core = execution.architectural.core_id;
    const uint64_t sequence = execution.architecturalSequence;
    const auto opcode = execution.architectural.opcode;
    execution = LogicalPageExecution{};
    packet->makeTimingResponse();
    packet->headerDelay = packet->payloadDelay = 0;
    cpuSidePorts[core]->schedTimingResp(packet, getClockEdge(Cycles(1)));
    DPRINTF(MAAVirtualTrace,
            "event=logical_page_retire schema=1 sequence=%lu maa=%u "
            "opcode=%u pages=4 exact_write_boundary=1\n",
            sequence, maaID, static_cast<unsigned>(opcode));
}

void
MAA::serviceLogicalSPD()
{
    serviceLogicalPageScheduler();
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

bool
MAA::hasLiveSoaJitState() const
{
    if (ifile->hasLiveSoaJitRmw())
        return true;
    if (std::any_of(my_instructions.begin(), my_instructions.end(),
                    [](const InstructionPtr instruction) {
                        return instruction != nullptr &&
                               instruction->isSoaJitRmw();
                    }))
        return true;
    for (unsigned int unit = 0; unit < num_indirect_units_total; ++unit) {
        if (indirectAccessUnits[unit].hasLiveSoaJitState())
            return true;
    }
    return false;
}

DrainState
MAA::drain()
{
    logicalSpdBridge->closeAdmission();
    for (int stream = 0; stream < num_maas; ++stream) {
        panic_if(!streamAccessUnits[stream]
                      .responseBearingPublisherQuiescent(),
                 "Response-bearing SPD publisher %d cannot be checkpointed "
                 "with live credits/retries/responses\n", stream);
    }
    panic_if(std::any_of(
                 responseBearingPublishCompletionOwner.begin(),
                 responseBearingPublishCompletionOwner.end(),
                 [](int owner) { return owner != -1; }),
             "Response-bearing SPD publisher cannot be checkpointed with a "
             "queued completion reservation\n");
    panic_if(std::any_of(
                 pendingPageZeroPrearms.begin(),
                 pendingPageZeroPrearms.end(),
                 [](const PendingPageZeroPrearm &pending) {
                     return pending.instruction != nullptr;
                 }),
             "MAA checkpoint/drain requested with a pending page-zero "
             "prearm\n");
    panic_if(directRetirementContexts.activeContexts() != 0 ||
                 directRetirementRetryPackets.count() != 0 ||
                 directRetirementOutstandingRequestCount() != 0,
             "Direct-retirement checkpoint/drain requested with live line "
             "credits; serialization is unsupported\n");
    panic_if(!logicalSpdBridge->allQuiescent(),
             "Logical SPD checkpoint/drain requested with live state; "
             "serialization is unsupported\n");
    panic_if(std::any_of(
                 logicalPageExecutions.begin(), logicalPageExecutions.end(),
                 [](const LogicalPageExecution &execution) {
                     return execution.active;
                 }),
             "Logical page scheduler checkpoint/drain requested with a live "
             "architectural record\n");
    panic_if(std::any_of(
                 logicalPageDescriptors.begin(),
                 logicalPageDescriptors.end(),
                 [](const std::array<
                        LogicalPageDescriptorState,
                        LogicalPageScheduler::LogicalDescriptors> &states) {
                     return std::any_of(
                         states.begin(), states.end(),
                         [](const LogicalPageDescriptorState &state) {
                             return state.configured;
                         });
                 }),
             "Logical page scheduler checkpoint/drain requested with "
             "configured descriptor generations; serialization is "
             "unsupported\n");
    panic_if(hasLiveSoaJitState(),
             "SoA/JIT checkpoint/drain requested with a live instruction, "
             "packet, context, read, or WriteResp; serialization is "
             "unsupported\n");
    return DrainState::Drained;
}

void
MAA::drainResume()
{
    for (int stream = 0; stream < num_maas; ++stream) {
        panic_if(!streamAccessUnits[stream]
                      .responseBearingPublisherQuiescent(),
                 "Response-bearing SPD publisher %d resumed with live "
                 "credits/retries/responses\n", stream);
    }
    panic_if(std::any_of(
                 responseBearingPublishCompletionOwner.begin(),
                 responseBearingPublishCompletionOwner.end(),
                 [](int owner) { return owner != -1; }),
             "Response-bearing SPD publisher resumed with a queued "
             "completion reservation\n");
    panic_if(std::any_of(
                 pendingPageZeroPrearms.begin(),
                 pendingPageZeroPrearms.end(),
                 [](const PendingPageZeroPrearm &pending) {
                     return pending.instruction != nullptr;
                 }),
             "MAA drain resumed with a pending page-zero prearm\n");
    panic_if(directRetirementContexts.activeContexts() != 0 ||
                 directRetirementRetryPackets.count() != 0 ||
                 directRetirementOutstandingRequestCount() != 0,
             "Direct-retirement drain resumed with live line credits\n");
    panic_if(!logicalSpdBridge->allQuiescent(),
             "Logical SPD drain resumed with non-quiescent live state\n");
    panic_if(std::any_of(
                 logicalPageExecutions.begin(), logicalPageExecutions.end(),
                 [](const LogicalPageExecution &execution) {
                     return execution.active;
                 }),
             "Logical page scheduler resumed with a live architectural "
             "record\n");
    panic_if(std::any_of(
                 logicalPageDescriptors.begin(),
                 logicalPageDescriptors.end(),
                 [](const std::array<
                        LogicalPageDescriptorState,
                        LogicalPageScheduler::LogicalDescriptors> &states) {
                     return std::any_of(
                         states.begin(), states.end(),
                         [](const LogicalPageDescriptorState &state) {
                             return state.configured;
                         });
                 }),
             "Logical page scheduler resumed with configured descriptor "
             "generations\n");
    panic_if(hasLiveSoaJitState(),
             "SoA/JIT drain resumed with live protocol state\n");
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
            !logicalPageUsesRegister(
                reg->maa_id, reg->register_id, register_words) &&
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
    activatePendingPageZeroPrearms();
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
            panic_if(logical_tile_page_scheduler &&
                         !instruction->hasLogicalOperands() &&
                         instructionTouchesLogicalReservedFrame(*instruction),
                     "Legacy physical instruction aliases a reserved logical "
                     "SPD frame span: %s\n", instruction->print().c_str());
            if (logical_tile_page_scheduler &&
                instruction->hasLogicalOperands()) {
                if (!submitLogicalPageInstruction(instruction, pkt)) {
                    ++pkt_it;
                    ++recv_it;
                    ++rid_it;
                    ++instruction_it;
                    continue;
                }
                DPRINTF(MAAController,
                        "%s: logical page instruction %s dispatched\n",
                        __func__, instruction->print());
                pkt_it = my_instruction_pkts.erase(pkt_it);
                recv_it = my_instruction_recvs.erase(recv_it);
                rid_it = my_instruction_RIDs.erase(rid_it);
                instruction_it = my_instructions.erase(instruction_it);
                delete instruction;
                continue;
            }
            const bool prearm =
                !isTokenBoundPageMaterialization(instruction) &&
                isPageZeroPrearmMaterialization(instruction);
            if (isTokenBoundPageMaterialization(instruction) || prearm) {
                if (prearm &&
                    virtualPageGeneration[instruction->src1SpdID] == 0) {
                    if (!queuePageZeroPrearm(instruction)) {
                        ++pkt_it;
                        ++recv_it;
                        ++rid_it;
                        ++instruction_it;
                        continue;
                    }
                    DPRINTF(MAAVirtualTrace,
                            "event=page_materialization_prearm schema=1 "
                            "occurrence=%lu token=%d base=0x%lx range=%d "
                            "minimum=0 maximum=%u stride=1 "
                            "producer_opcode=virtual_gather "
                            "marker=dual_token\n",
                            pageMaterializationTraceOccurrence++,
                            instruction->src1SpdID, instruction->baseAddr,
                            instruction->addrRangeID,
                            HybridConsumerPipeline::ProducerPageElements);
                    pkt->makeTimingResponse();
                    pkt->headerDelay = pkt->payloadDelay = 0;
                    cpuSidePorts[0]->schedTimingResp(
                        pkt, getClockEdge(Cycles(1)));
                    pkt_it = my_instruction_pkts.erase(pkt_it);
                    recv_it = my_instruction_recvs.erase(recv_it);
                    rid_it = my_instruction_RIDs.erase(rid_it);
                    instruction_it = my_instructions.erase(instruction_it);
                    // Ownership moved to pendingPageZeroPrearms. The exact
                    // producer registration below activates and deletes it.
                    continue;
                }
                const PageMaterializationSubmit submitted =
                    submitPageMaterialization(instruction);
                if (submitted == PageMaterializationSubmit::Retry) {
                    ++pkt_it;
                    ++recv_it;
                    ++rid_it;
                    ++instruction_it;
                    continue;
                }
                if (submitted == PageMaterializationSubmit::Accepted) {
                    DPRINTF(MAAController,
                            "%s: bounded page materializer %s dispatched\n",
                            __func__, instruction->print());
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
                // The ordinary stream unit is the correctness fallback. Its
                // completion-only source serializes it behind the complete
                // virtual producer but contributes no SPD payload reads.
                instruction->src1MustBeFinished = true;
                stats.page_materialization_dispatch_fallbacks++;
                DPRINTF(MAAVirtualTrace,
                        "event=page_materialization_dispatch_fallback "
                        "schema=1 occurrence=%lu token=%d base=0x%lx "
                        "destination=%d reason=admission_fallback\n",
                        pageMaterializationTraceOccurrence++,
                        instruction->src1SpdID, instruction->baseAddr,
                        instruction->dst1SpdID);
            }
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
                const bool direct_active = direct &&
                    findDirectRetirementExecution(
                        instruction->src1SpdID,
                        virtualPageGeneration[instruction->src1SpdID]) !=
                        nullptr;
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
            bool materializerDestinationBusy = false;
            const bool responseBearingPublish =
                StreamAccessUnit::isResponseBearingPublishInstruction(
                    instruction);
            const int destinations[] = {
                instruction->dst1SpdID, instruction->dst2SpdID};
            for (const int destination : destinations) {
                if (destination == -1)
                    continue;
                const int destinationWordSize =
                    getInstructionTileWordSize(instruction, destination);
                materializerDestinationBusy =
                    materializerDestinationBusy ||
                    pageMaterializerOwnsDestination(
                        instruction->maa_id, destination,
                        destinationWordSize) ||
                    responseBearingPublishDestinationBusy(
                        instruction->maa_id, destination,
                        destinationWordSize);
            }
            if (responseBearingPublish && !materializerDestinationBusy) {
                const int completion =
                    StreamAccessUnit::responseBearingPublishCompletionTile(
                        instruction);
                const int tileWords = instruction->WordSize() /
                    sizeof(uint32_t);
                for (int offset = 0; offset < tileWords; ++offset) {
                    if (ifile->hasTileReference(
                            instruction->maa_id, completion + offset)) {
                        materializerDestinationBusy = true;
                        break;
                    }
                }
            }
            if (materializerDestinationBusy) {
                ++pkt_it;
                ++recv_it;
                ++rid_it;
                ++instruction_it;
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
            Instruction queuedInstruction = *instruction;
            if (responseBearingPublish) {
                queuedInstruction.controllerTransactionID =
                    StreamAccessUnit::
                        ResponseBearingPublishInstructionTag;
                queuedInstruction.controllerDstSlot =
                    instruction->dst1SpdID;
                queuedInstruction.dst1SpdID = -1;
                queuedInstruction.dst1Status =
                    Instruction::TileStatus::Finished;
            }
            if (ifile->pushInstruction(queuedInstruction)) {
                DPRINTF(MAAController, "%s: %s dispatched!\n", __func__,
                        instruction->print());
                if (instruction->dst1SpdID != -1) {
                    assert(instruction->dst1SpdID != instruction->src1SpdID);
                    assert(instruction->dst1SpdID != instruction->src2SpdID);
                    const int dst1_word_size =
                        getInstructionTileWordSize(
                            instruction, instruction->dst1SpdID);
                    spd->setTileIdle(
                        instruction->dst1SpdID, dst1_word_size);
                    spd->setTileNotReady(
                        instruction->dst1SpdID, dst1_word_size);
                    if (responseBearingPublish) {
                        reserveResponseBearingPublishCompletion(
                            instruction->maa_id,
                            instruction->dst1SpdID, dst1_word_size);
                    }
                }
                if (instruction->opcode ==
                        Instruction::OpcodeType::INDIR_LD_VIRTUAL ||
                    instruction->opcode ==
                        Instruction::OpcodeType::INDIR_LD_VIRTUAL_INDEX) {
                    resetVirtualPageReady(
                        instruction->dst1SpdID, instruction->backingAddr,
                        instruction->backingAddrRangeID,
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
                if (instruction->isSoaJitPageFedRmw()) {
                    const int ready_id = num_tiles +
                        num_tiles * MaxVirtualPages + instruction->core_id;
                    my_ready_pkts.push_back(pkt);
                    my_ready_tile_ids.push_back(ready_id);
                    DPRINTF(MAAVirtualTrace,
                            "event=soa_jit_page_fed_open_wait schema=1 "
                            "core=%d generation=%lu ready_id=%d\n",
                            instruction->core_id,
                            instruction->soaJitPageFedGeneration,
                            ready_id);
                } else {
                    pkt->makeTimingResponse();
                    pkt->headerDelay = pkt->payloadDelay = 0;
                    cpuSidePorts[0]->schedTimingResp(
                        pkt, getClockEdge(Cycles(1)));
                }
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
    const int publisher_completion =
        StreamAccessUnit::responseBearingPublishCompletionTile(instruction);
    const bool logical_page_managed = instruction->logicalPageManaged;
    if (publisher_completion != -1 && !logical_page_managed) {
        const int word_size = instruction->WordSize();
        panic_if(instruction->dst1SpdID != -1,
                 "Publisher retained a guest completion tile inside IF\n");
        spd->setTileFinished(publisher_completion, word_size);
        setTileReady(publisher_completion, word_size);
        // IF sees the completion only at the terminal transition, allowing it
        // to wake queued consumers without asking legacy getWordSize() to
        // classify the guarded STREAM_ST destination.
        instruction->dst1SpdID = publisher_completion;
    } else if (instruction->dst1SpdID != -1) {
        const int dst1_word_size = getInstructionTileWordSize(
            instruction, instruction->dst1SpdID);
        spd->setTileFinished(
            instruction->dst1SpdID, dst1_word_size);
        setTileReady(instruction->dst1SpdID, dst1_word_size);
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
    if (publisher_completion != -1 && !logical_page_managed) {
        instruction->dst1SpdID = -1;
        releaseResponseBearingPublishCompletion(
            instruction->maa_id, publisher_completion,
            instruction->WordSize());
    }
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
    if (logical_page_managed) {
        finishLogicalPageAction(instruction);
    } else if (controller_managed) {
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

void
MAA::signalPageFedSoaJitOpen(int coreID, uint64_t generation)
{
    panic_if(coreID < 0 || coreID >= static_cast<int>(num_cores) ||
                 generation == 0,
             "Invalid page-fed open response identity core=%d "
             "generation=%lu\n",
             coreID, generation);
    const int ready_id = num_tiles + num_tiles * MaxVirtualPages + coreID;
    auto packet = my_ready_pkts.begin();
    auto ready = my_ready_tile_ids.begin();
    int matches = 0;
    while (packet != my_ready_pkts.end() &&
           ready != my_ready_tile_ids.end()) {
        if (*ready != ready_id) {
            ++packet;
            ++ready;
            continue;
        }
        PacketPtr pkt = *packet;
        pkt->makeTimingResponse();
        pkt->headerDelay = pkt->payloadDelay = 0;
        cpuSidePorts[0]->schedTimingResp(pkt, getClockEdge(Cycles(1)));
        packet = my_ready_pkts.erase(packet);
        ready = my_ready_tile_ids.erase(ready);
        ++matches;
    }
    panic_if(matches != 1,
             "Page-fed open core=%d generation=%lu released %d responses\n",
             coreID, generation, matches);
    DPRINTF(MAAVirtualTrace,
            "event=soa_jit_page_fed_open_response schema=1 core=%d "
            "generation=%lu ready_id=%d responses=1\n",
            coreID, generation, ready_id);
}
void
MAA::signalPageFedSoaJitProductReady(
    int coreID, uint64_t generation, uint8_t page, Addr pageBacking,
    int backingRangeID, uint8_t wordBytes)
{
    panic_if(coreID < 0 || coreID >= static_cast<int>(num_cores) ||
                 generation == 0,
             "Invalid page-fed product-ready identity core=%d "
             "generation=%lu\n",
             coreID, generation);
    IndirectAccessUnit *owner = nullptr;
    for (unsigned int unit = 0; unit < num_indirect_units_total; ++unit) {
        if (!indirectAccessUnits[unit].pageFedActiveForCore(coreID))
            continue;
        panic_if(owner != nullptr,
                 "Core %d owns duplicate page-fed product contexts\n",
                 coreID);
        owner = &indirectAccessUnits[unit];
    }
    // Ordinary response-bearing publishers are unchanged.  A terminal is a
    // page-fed product notification only while that core owns an active
    // page-fed context; once classified, every identity mismatch fails closed.
    if (owner == nullptr)
        return;
    owner->signalPageFedSoaJitProductReady(
        coreID, generation, page, pageBacking, backingRangeID, wordBytes);
}
void MAA::resetVirtualPageReady(int tokenTileID, Addr backingAddr,
                                int backingRangeID, int wordSize) {
    panic_if(tokenTileID < 0 || tokenTileID >= num_tiles,
             "invalid virtual completion token tile %d\n", tokenTileID);
    panic_if(findDirectRetirementExecution(
                 tokenTileID, virtualPageGeneration[tokenTileID]) != nullptr,
             "virtual completion token %d reused by a live direct consumer\n",
             tokenTileID);
    PageMaterializationExecution *oldMaterialization =
        findPageMaterializationExecution(
            tokenTileID, virtualPageGeneration[tokenTileID]);
    if (oldMaterialization != nullptr) {
        panic_if(oldMaterialization->pageActive ||
                     hasDirectRetirementOutstandingOwner(
                         oldMaterialization->key),
                 "virtual completion token %d reused by a live page "
                 "materialization\n", tokenTileID);
        for (const PageMaterializationCommit &commit :
             pageMaterializationCommits) {
            panic_if(commit.active && sameDirectRetirementKey(
                                          commit.request.owner,
                                          oldMaterialization->key),
                     "virtual completion token %d reused before its SPD "
                     "commit\n", tokenTileID);
        }
        panic_if(!directRetirementContexts.cancelMaterialization(
                     oldMaterialization->key),
                 "virtual completion token %d could not close its idle "
                 "materializer lifetime\n", tokenTileID);
        (void)inactivePayloadFallbacks.clearOwner(oldMaterialization->key);
        if (sameDirectRetirementKey(inactivePayloadLookup.request.owner,
                                    oldMaterialization->key))
            inactivePayloadLookup = InactivePayloadLookup{};
        if (sameDirectRetirementKey(
                inactiveMaskedFragmentLookup.request.owner,
                oldMaterialization->key))
            inactiveMaskedFragmentLookup = InactiveMaskedFragmentLookup{};
        (void)directRetirementEarlyLineLedger.clear(
            {oldMaterialization->key.tokenTile,
             oldMaterialization->key.generation,
             oldMaterialization->backingAddress});
        const auto payloadClear = inactiveProducerLinePayloadCapture.clear(
            {oldMaterialization->key.tokenTile,
             oldMaterialization->key.generation,
             virtualPagePayloadIncarnation[tokenTileID],
             oldMaterialization->backingAddress});
        stats.page_materialization_inactive_payload_drops +=
            payloadClear.discardedLines;
        const auto maskedClear = inactiveMaskedFragmentRetention.clear(
            {oldMaterialization->key.tokenTile,
             oldMaterialization->key.generation,
             virtualPagePayloadIncarnation[tokenTileID],
             oldMaterialization->backingAddress});
        if (maskedClear)
            stats.page_materialization_inactive_masked_clears++;
        *oldMaterialization = PageMaterializationExecution{};
    }
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
                     std::numeric_limits<uint64_t>::max() ||
                 virtualPagePayloadIncarnation[tokenTileID] ==
                     std::numeric_limits<uint64_t>::max(),
             "virtual completion token %d generation/incarnation overflow\n",
             tokenTileID);
    ++virtualPageGeneration[tokenTileID];
    ++virtualPagePayloadIncarnation[tokenTileID];
    virtualPageBackingAddr[tokenTileID] = backingAddr;
    virtualPageBackingRangeID[tokenTileID] = backingRangeID;
    virtualPageWordSize[tokenTileID] = wordSize;
    virtualProducerRegistrationTick[tokenTileID] = curTick();
    virtualPageLastReadyTick[tokenTileID] = 0;
    if (direct_retirement_line_handoff) {
        const uint64_t bytes =
            static_cast<uint64_t>(num_tile_elements) * wordSize;
        panic_if(wordSize <= 0 ||
                     bytes % HybridConsumerPipeline::LineBytes != 0 ||
                     HybridConsumerPipeline::LineBytes % wordSize != 0,
                 "virtual producer token %d has unsupported line geometry\n",
                 tokenTileID);
        const uint64_t lines = bytes / HybridConsumerPipeline::LineBytes;
        const unsigned words = HybridConsumerPipeline::LineBytes / wordSize;
        panic_if(lines > EarlyProducerLineReadinessLedger::MaxLines ||
                     words > 16,
                 "virtual producer token %d exceeds early-line ledger "
                 "geometry\n",
                 tokenTileID);
        const auto begin = directRetirementEarlyLineLedger.begin(
            {static_cast<uint16_t>(tokenTileID),
             virtualPageGeneration[tokenTileID], backingAddr},
            static_cast<uint16_t>(lines),
            static_cast<uint16_t>((1U << words) - 1));
        panic_if(
            begin == EarlyProducerLineReadinessLedger::BeginResult::Invalid ||
                begin ==
                    EarlyProducerLineReadinessLedger::BeginResult::Stale ||
                begin ==
                    EarlyProducerLineReadinessLedger::BeginResult::Existing,
                 "virtual producer token %d could not start a fresh "
                 "early-line generation\n",
                 tokenTileID);
        if (begin == EarlyProducerLineReadinessLedger::BeginResult::Full)
            stats.direct_retirement_early_line_overflows++;
        DPRINTF(MAAVirtualTrace,
                "event=direct_retirement_early_ledger_begin schema=1 "
                "occurrence=%lu token=%d generation=%lu lines=%lu "
                "result=%u active_slots=%u storage_bytes=%lu\n",
                directRetirementTraceOccurrence++, tokenTileID,
                virtualPageGeneration[tokenTileID], lines,
                static_cast<unsigned>(begin),
                directRetirementEarlyLineLedger.activeSlots(),
                EarlyProducerLineReadinessLedger::chargedTotalBytes());
        if (inactive_page_payload_capture_lines != 0) {
            uint16_t descriptorDisplacedLines = 0;
            const auto captureBegin = inactiveProducerLinePayloadCapture.begin(
                {static_cast<uint16_t>(tokenTileID),
                 virtualPageGeneration[tokenTileID],
                 virtualPagePayloadIncarnation[tokenTileID], backingAddr},
                static_cast<uint16_t>(lines),
                static_cast<uint16_t>(inactive_page_payload_capture_lines),
                &descriptorDisplacedLines);
            panic_if(captureBegin ==
                         InactiveProducerLinePayloadCapture::
                             BeginResult::Invalid ||
                         captureBegin ==
                             InactiveProducerLinePayloadCapture::
                                 BeginResult::Stale ||
                         captureBegin ==
                             InactiveProducerLinePayloadCapture::
                                 BeginResult::Existing,
                     "virtual producer token %d could not start inactive "
                     "payload capture\n", tokenTileID);
            if (captureBegin ==
                InactiveProducerLinePayloadCapture::BeginResult::Full)
                stats.page_materialization_inactive_payload_drops++;
            stats.page_materialization_inactive_payload_drops +=
                descriptorDisplacedLines;
            stats.page_materialization_inactive_payload_bytes =
                InactiveProducerLinePayloadCapture::provisionedPayloadBytes(
                    inactive_page_payload_capture_lines) +
                InactiveProducerLinePayloadCapture::
                    provisionedReadPipelinePayloadBytes(
                        inactive_page_payload_capture_lines);
            stats.page_materialization_inactive_payload_control_bytes =
                InactiveProducerLinePayloadCapture::bitsToBytes(
                    InactiveProducerLinePayloadCapture::
                        provisionedMAAControlBits(
                            inactive_page_payload_capture_lines, num_tiles));
            DPRINTF(MAAVirtualTrace,
                    "event=page_materialization_inactive_payload_begin "
                    "schema=1 occurrence=%lu token=%d generation=%lu "
                    "payload_incarnation=%lu descriptor_index=%u "
                    "descriptor_displaced_lines=%u "
                    "capacity_lines=%u payload_bytes=%lu "
                    "tag_control_bytes=%lu write_ports=%u read_ports=%u "
                    "conflict_policy=%s "
                    "read_pipeline_payload_bytes=%lu "
                    "lookup_latch_control_bytes=%lu "
                    "persistent_incarnation_bits=%lu "
                    "hardware_total_bits=%lu "
                    "host_capture_object_bytes=%lu "
                    "host_lookup_object_bytes=%lu "
                    "port_access_cycles=%u port_time_unit=maa_cycles "
                    "descriptor_collision=replaces_to_coherent_fallback "
                    "clear_policy=lazy_descriptor_only result=%u\n",
                    pageMaterializationTraceOccurrence++, tokenTileID,
                    virtualPageGeneration[tokenTileID],
                    virtualPagePayloadIncarnation[tokenTileID],
                    InactiveProducerLinePayloadCapture::
                        descriptorIndexForToken(tokenTileID),
                    descriptorDisplacedLines,
                    inactive_page_payload_capture_lines,
                    InactiveProducerLinePayloadCapture::
                        provisionedPayloadBytes(
                            inactive_page_payload_capture_lines),
                    InactiveProducerLinePayloadCapture::
                        provisionedControlBytes(
                            inactive_page_payload_capture_lines),
                    InactiveProducerLinePayloadCapture::WritePortCount,
                    InactiveProducerLinePayloadCapture::ReadPortCount,
                    InactiveProducerLinePayloadCapture::conflictPolicyName(),
                    InactiveProducerLinePayloadCapture::
                        provisionedReadPipelinePayloadBytes(
                            inactive_page_payload_capture_lines),
                    InactiveProducerLinePayloadCapture::bitsToBytes(
                        InactiveProducerLinePayloadCapture::
                            MAALookupControlBits),
                    InactiveProducerLinePayloadCapture::
                        provisionedMAAPersistentStateBits(
                            inactive_page_payload_capture_lines, num_tiles),
                    InactiveProducerLinePayloadCapture::
                        provisionedCombinedTotalBits(
                            inactive_page_payload_capture_lines, num_tiles),
                    sizeof(inactiveProducerLinePayloadCapture),
                    sizeof(inactivePayloadLookup) +
                        sizeof(inactivePayloadFallbacks),
                    InactiveProducerLinePayloadCapture::PortAccessCycles,
                    static_cast<unsigned>(captureBegin));
        }
        if (inactive_page_masked_fragment_retention_lines != 0) {
            uint16_t discardedEntries = 0;
            const InactiveProducerMaskedFragmentRetention::Key key{
                static_cast<uint16_t>(tokenTileID),
                virtualPageGeneration[tokenTileID],
                virtualPagePayloadIncarnation[tokenTileID], backingAddr};
            const auto fragmentBegin = inactiveMaskedFragmentRetention.begin(
                key, static_cast<uint16_t>(lines),
                static_cast<uint16_t>(
                    inactive_page_masked_fragment_retention_lines),
                &discardedEntries);
            panic_if(fragmentBegin ==
                         InactiveProducerMaskedFragmentRetention::
                             BeginResult::Invalid ||
                         fragmentBegin ==
                         InactiveProducerMaskedFragmentRetention::
                             BeginResult::Stale ||
                         fragmentBegin ==
                         InactiveProducerMaskedFragmentRetention::
                             BeginResult::Existing,
                     "virtual producer token %d could not start inactive "
                     "masked-fragment retention\n", tokenTileID);
            stats.page_materialization_inactive_masked_bytes =
                InactiveProducerMaskedFragmentRetention::bitsToBytes(
                    InactiveProducerMaskedFragmentRetention::
                        provisionedPayloadBits(
                            inactive_page_masked_fragment_retention_lines) +
                    InactiveProducerMaskedFragmentRetention::LineBytes * 8);
            stats.page_materialization_inactive_masked_control_bytes =
                InactiveProducerMaskedFragmentRetention::bitsToBytes(
                    InactiveProducerMaskedFragmentRetention::
                        provisionedControlBits(
                            inactive_page_masked_fragment_retention_lines) +
                    InactiveProducerMaskedFragmentRetention::
                        MAALookupControlBits +
                    InactiveProducerMaskedFragmentRetention::
                        provisionedMAAPersistentStateBits(
                            inactive_page_masked_fragment_retention_lines,
                            num_tiles));
            DPRINTF(MAAVirtualTrace,
                    "event=page_materialization_inactive_masked_begin "
                    "schema=1 occurrence=%lu token=%d generation=%lu "
                    "payload_incarnation=%lu descriptor_partition=%u "
                    "discarded_entries=%u capacity_entries=%u "
                    "partition_entries=%u bank_partition_entries=%u "
                    "write_banks=%u read_ports=%u poison_bits=%lu "
                    "hardware_total_bits=%lu result=%u\n",
                    pageMaterializationTraceOccurrence++, tokenTileID,
                    virtualPageGeneration[tokenTileID],
                    virtualPagePayloadIncarnation[tokenTileID],
                    InactiveProducerMaskedFragmentRetention::
                        descriptorIndexForToken(tokenTileID),
                    discardedEntries,
                    inactive_page_masked_fragment_retention_lines,
                    InactiveProducerMaskedFragmentRetention::
                        entriesPerPartition(
                            inactive_page_masked_fragment_retention_lines),
                    InactiveProducerMaskedFragmentRetention::
                        entriesPerBankPerPartition(
                            inactive_page_masked_fragment_retention_lines),
                    InactiveProducerMaskedFragmentRetention::BankCount,
                    InactiveProducerMaskedFragmentRetention::ReadPortCount,
                    InactiveProducerMaskedFragmentRetention::
                        provisionedPoisonBits(
                            inactive_page_masked_fragment_retention_lines),
                    InactiveProducerMaskedFragmentRetention::
                        provisionedCombinedTotalBits(
                            inactive_page_masked_fragment_retention_lines,
                            num_tiles),
                    static_cast<unsigned>(fragmentBegin));
        }
    }
    // This runs before the newly admitted producer can execute. It binds the
    // already acknowledged prearm to the exact generation/backing metadata
    // and starts page-zero forwarding without a CPU-side dependency cycle.
    activatePendingPageZeroPrearms();
    // A token-bound page load may already be waiting in the CPU admission
    // queue. Registration changes its result from Retry to Accepted.
    scheduleDispatchInstructionEvent(1);
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
MAA::setVirtualLineWordsReady(int tokenTileID, Addr backingAddr,
                              uint64_t generation, int lineID,
                              uint16_t wordMask, uint64_t transactionID,
                              const uint8_t *writeRespPayload,
                              unsigned payloadBytes)
{
    if (!direct_retirement_line_handoff)
        return;
    panic_if(tokenTileID < 0 || tokenTileID >= num_tiles || lineID < 0,
             "invalid virtual line token=%d line=%d\n", tokenTileID,
             lineID);
    const auto accountInactiveMaskedOutcome =
        [this, wordMask](
            InactiveProducerMaskedFragmentRetention::CaptureResult result) {
        using Result =
            InactiveProducerMaskedFragmentRetention::CaptureResult;
        uint8_t mergedWords = 0;
        for (uint16_t mask = wordMask; mask != 0; mask >>= 1)
            mergedWords += mask & 1U;
        switch (result) {
          case Result::Accepted:
            stats.page_materialization_inactive_masked_fragments_accepted++;
            stats.page_materialization_inactive_masked_words_merged +=
                mergedWords;
            break;
          case Result::Reconstructed:
            stats.page_materialization_inactive_masked_fragments_accepted++;
            stats.page_materialization_inactive_masked_words_merged +=
                mergedWords;
            stats
                .page_materialization_inactive_masked_lines_reconstructed++;
            break;
          case Result::ConflictPoison:
            stats.page_materialization_inactive_masked_tag_conflicts++;
            break;
          case Result::OverlapPoison:
            stats.page_materialization_inactive_masked_overlap_poison++;
            break;
          case Result::WritePortPoison:
            stats.page_materialization_inactive_masked_write_port_poison++;
            break;
          case Result::StalePoison:
          case Result::InvalidPoison:
          case Result::Untracked:
          case Result::Invalid:
            stats
                .page_materialization_inactive_masked_stale_untracked_drops++;
            break;
          case Result::Disabled:
          case Result::AlreadyPoisoned:
            break;
        }
        if (result == Result::Accepted || result == Result::Reconstructed) {
            stats.page_materialization_inactive_masked_high_water = std::max(
                stats.page_materialization_inactive_masked_high_water.value(),
                static_cast<double>(inactiveMaskedFragmentRetention
                                        .counters().occupancyHighWater));
        }
    };
    if (generation != virtualPageGeneration[tokenTileID] ||
        backingAddr != virtualPageBackingAddr[tokenTileID]) {
        if (inactive_page_masked_fragment_retention_lines != 0) {
            const auto staleCapture = inactiveMaskedFragmentRetention.capture(
                {static_cast<uint16_t>(tokenTileID), generation,
                 virtualPagePayloadIncarnation[tokenTileID], backingAddr},
                static_cast<uint16_t>(lineID), transactionID, wordMask,
                static_cast<uint8_t>(virtualPageWordSize[tokenTileID]),
                reinterpret_cast<const std::byte *>(writeRespPayload),
                payloadBytes, static_cast<uint64_t>(curCycle()));
            accountInactiveMaskedOutcome(staleCapture);
        }
        DPRINTF(MAAVirtualTrace,
                "event=direct_retirement_producer_line_reject schema=1 "
                "occurrence=%lu token=%d generation=%lu line=%d "
                "reason=stale_generation_or_backing\n",
                directRetirementTraceOccurrence++, tokenTileID, generation,
                lineID);
        return;
    }
    const unsigned producerWordsPerLine =
        HybridConsumerPipeline::LineBytes /
        static_cast<unsigned>(virtualPageWordSize[tokenTileID]);
    const uint16_t producerFullMask = producerWordsPerLine == 16
        ? std::numeric_limits<uint16_t>::max()
        : static_cast<uint16_t>((1U << producerWordsPerLine) - 1);
    const bool fullAuthoritativePayload =
        wordMask == producerFullMask && writeRespPayload != nullptr &&
        payloadBytes == HybridConsumerPipeline::LineBytes;
    auto &firstOwnerConflicts =
        stats
            .page_materialization_inactive_payload_first_owner_conflicts;
    auto &writePortStalls =
        stats
            .page_materialization_inactive_payload_write_port_stalls;
    const auto accountInactivePayloadOutcome =
        [this, &firstOwnerConflicts, &writePortStalls](
            InactiveProducerLinePayloadCapture::CaptureResult result) {
        using Result = InactiveProducerLinePayloadCapture::CaptureResult;
        switch (result) {
          case Result::Captured:
            stats.page_materialization_inactive_payload_captures++;
            break;
          case Result::Conflict:
            stats.page_materialization_inactive_payload_conflicts++;
            stats.page_materialization_inactive_payload_drops++;
            firstOwnerConflicts++;
            break;
          case Result::PortBusy:
            writePortStalls++;
            stats.page_materialization_inactive_payload_drops++;
            break;
          case Result::Untracked:
          case Result::Stale:
          case Result::Invalid:
            // These outcomes have no live lifetime descriptor to carry a
            // per-owner tally. They close only in the explicitly global stat.
            stats.page_materialization_inactive_payload_drops++;
            break;
          case Result::Disabled:
          case Result::Duplicate:
            break;
        }
        if (result == Result::Captured) {
            stats.page_materialization_inactive_payload_high_water =
                std::max(
                    stats.page_materialization_inactive_payload_high_water
                        .value(),
                    static_cast<double>(inactiveProducerLinePayloadCapture
                                            .occupancyHighWater()));
        }
    };
    DirectRetirementExecution *directExecution =
        findDirectRetirementExecution(tokenTileID, generation);
    PageMaterializationExecution *materialization =
        findPageMaterializationExecution(tokenTileID, generation);
    panic_if(directExecution != nullptr && materialization != nullptr,
             "Token %d generation %lu has two live ACK authorities\n",
             tokenTileID, generation);
    if (directExecution == nullptr && materialization == nullptr) {
        const auto result = directRetirementEarlyLineLedger.acknowledge(
            {static_cast<uint16_t>(tokenTileID), generation, backingAddr},
            {static_cast<uint16_t>(lineID), wordMask, transactionID});
        panic_if(result ==
                     EarlyProducerLineReadinessLedger::AckResult::Invalid,
                 "Direct-retirement early line acknowledgement is invalid "
                 "token=%d line=%d transaction=%lu\n",
                 tokenTileID, lineID, transactionID);
        if (result ==
            EarlyProducerLineReadinessLedger::AckResult::Overflow)
            stats.direct_retirement_early_line_overflows++;
        InactiveProducerLinePayloadCapture::CaptureResult captureResult =
            InactiveProducerLinePayloadCapture::CaptureResult::Disabled;
        InactiveProducerMaskedFragmentRetention::CaptureResult
            maskedCapture = InactiveProducerMaskedFragmentRetention::
                CaptureResult::Disabled;
        if (fullAuthoritativePayload &&
            result != EarlyProducerLineReadinessLedger::AckResult::Duplicate) {
            captureResult = inactiveProducerLinePayloadCapture.capture(
                {static_cast<uint16_t>(tokenTileID), generation,
                 virtualPagePayloadIncarnation[tokenTileID], backingAddr},
                static_cast<uint16_t>(lineID), transactionID,
                reinterpret_cast<const std::byte *>(writeRespPayload),
                payloadBytes, static_cast<uint64_t>(curCycle()));
            accountInactivePayloadOutcome(captureResult);
        }
        if (inactive_page_masked_fragment_retention_lines != 0) {
            maskedCapture = inactiveMaskedFragmentRetention.capture(
                {static_cast<uint16_t>(tokenTileID), generation,
                 virtualPagePayloadIncarnation[tokenTileID], backingAddr},
                static_cast<uint16_t>(lineID), transactionID, wordMask,
                static_cast<uint8_t>(virtualPageWordSize[tokenTileID]),
                reinterpret_cast<const std::byte *>(writeRespPayload),
                payloadBytes, static_cast<uint64_t>(curCycle()));
            accountInactiveMaskedOutcome(maskedCapture);
        }
        DPRINTF(MAAVirtualTrace,
                "event=direct_retirement_producer_line_early schema=1 "
                "occurrence=%lu token=%d generation=%lu "
                "payload_incarnation=%lu line=%d "
                "transaction=%lu result=%u ready_lines=%u "
                "inactive_payload_capture=%u inactive_masked_capture=%u\n",
                directRetirementTraceOccurrence++, tokenTileID, generation,
                virtualPagePayloadIncarnation[tokenTileID], lineID,
                transactionID, static_cast<unsigned>(result),
                directRetirementEarlyLineLedger.readyLineCount(
                    {static_cast<uint16_t>(tokenTileID), generation,
                     backingAddr}), static_cast<unsigned>(captureResult),
                static_cast<unsigned>(maskedCapture));
        return;
    }
    const auto owner = directExecution != nullptr
        ? directExecution->key : materialization->key;
    const Addr ownerBacking = directExecution != nullptr
        ? directExecution->backingAddress : materialization->backingAddress;
    panic_if(ownerBacking != backingAddr,
             "Consumer token %d lost exact line provenance\n",
             tokenTileID);
    const bool activePageLine = materialization != nullptr &&
        materialization->pageActive && lineID >= 0 &&
        lineID / directRetirementContexts.producerPageLines(owner) ==
            materialization->page;
    InactiveProducerMaskedFragmentRetention::CaptureResult maskedCapture =
        InactiveProducerMaskedFragmentRetention::CaptureResult::Disabled;
    if (materialization != nullptr && !activePageLine &&
        inactive_page_masked_fragment_retention_lines != 0) {
        maskedCapture = inactiveMaskedFragmentRetention.capture(
            {owner.tokenTile, owner.generation,
             virtualPagePayloadIncarnation[owner.tokenTile], ownerBacking},
            static_cast<uint16_t>(lineID), transactionID, wordMask,
            static_cast<uint8_t>(materialization->wordBytes),
            reinterpret_cast<const std::byte *>(writeRespPayload),
            payloadBytes, static_cast<uint64_t>(curCycle()));
        accountInactiveMaskedOutcome(maskedCapture);
    }
    HybridConsumerContextQueue::Snapshot before;
    panic_if(!directRetirementContexts.snapshot(owner, &before),
             "Consumer producer line lost its owner\n");
    const HybridConsumerPipeline::ProducerLineAck ack{
        owner.generation,
        static_cast<uint16_t>(lineID), wordMask, transactionID};
    if (!directRetirementContexts.notifyProducerLineWriteAck(
            owner, ack)) {
        DPRINTF(MAAVirtualTrace,
                "event=direct_retirement_producer_line_reject schema=1 "
                "occurrence=%lu token=%d generation=%lu line=%d "
                "transaction=%lu reason=duplicate_or_closed\n",
                directRetirementTraceOccurrence++, tokenTileID, generation,
                lineID, transactionID);
        return;
    }
    HybridConsumerContextQueue::Snapshot after;
    panic_if(!directRetirementContexts.snapshot(owner, &after),
             "Consumer producer line lost its snapshot\n");
    if (after.producerLineAcks != before.producerLineAcks) {
        if (directExecution != nullptr)
            stats.direct_retirement_producer_line_acks++;
        else
            stats.page_materialization_producer_line_acks++;
    }
    if (directExecution != nullptr) {
        DPRINTF(MAAVirtualTrace,
                "event=direct_retirement_producer_line_ready schema=1 "
                "occurrence=%lu token=%u generation=%lu incarnation=%lu "
                "line=%d transaction=%lu\n",
                directRetirementTraceOccurrence++, owner.tokenTile,
                owner.generation, owner.incarnation, lineID, transactionID);
        scheduleDirectRetirementEvent();
        return;
    }

    const unsigned wordsPerLine =
        HybridConsumerPipeline::LineBytes / materialization->wordBytes;
    const uint16_t fullMask = wordsPerLine == 16
        ? std::numeric_limits<uint16_t>::max()
        : static_cast<uint16_t>((1U << wordsPerLine) - 1);
    InactiveProducerLinePayloadCapture::CaptureResult inactiveCapture =
        InactiveProducerLinePayloadCapture::CaptureResult::Disabled;
    if (!activePageLine && fullAuthoritativePayload) {
        inactiveCapture = inactiveProducerLinePayloadCapture.capture(
            {owner.tokenTile, owner.generation,
             virtualPagePayloadIncarnation[owner.tokenTile], ownerBacking},
            static_cast<uint16_t>(lineID), transactionID,
            reinterpret_cast<const std::byte *>(writeRespPayload),
            payloadBytes, static_cast<uint64_t>(curCycle()));
        accountInactivePayloadOutcome(inactiveCapture);
        DPRINTF(MAAVirtualTrace,
                "event=page_materialization_inactive_payload_capture "
                "schema=1 occurrence=%lu token=%u generation=%lu "
                "incarnation=%lu payload_incarnation=%lu line=%d "
                "transaction=%lu result=%u\n",
                pageMaterializationTraceOccurrence++, owner.tokenTile,
                owner.generation, owner.incarnation,
                virtualPagePayloadIncarnation[owner.tokenTile], lineID,
                transactionID,
                static_cast<unsigned>(inactiveCapture));
    }
    if (!activePageLine &&
        inactive_page_masked_fragment_retention_lines != 0) {
        DPRINTF(MAAVirtualTrace,
                "event=page_materialization_inactive_masked_capture "
                "schema=1 occurrence=%lu token=%u generation=%lu "
                "incarnation=%lu payload_incarnation=%lu line=%d "
                "word_mask=0x%x transaction=%lu result=%u bank=%u\n",
                pageMaterializationTraceOccurrence++, owner.tokenTile,
                owner.generation, owner.incarnation,
                virtualPagePayloadIncarnation[owner.tokenTile], lineID,
                wordMask, transactionID,
                static_cast<unsigned>(maskedCapture),
                InactiveProducerMaskedFragmentRetention::bankIndexForLine(
                    static_cast<uint16_t>(lineID)));
    }
    bool directStaged = false;
    bool directCompleted = false;
    if (page_materialization_direct_spd_fragments && activePageLine &&
        wordMask != fullMask) {
        const uint16_t pageLines =
            directRetirementContexts.producerPageLines(owner);
        const uint16_t pageLine = static_cast<uint16_t>(lineID) % pageLines;
        const bool exactPayload = writeRespPayload != nullptr &&
            payloadBytes == HybridConsumerPipeline::LineBytes &&
            wordMask != 0 && (wordMask & ~fullMask) == 0;
        const auto countDirectFallback = [&]() {
            if (materialization->stagedFallbackCounted.test(pageLine))
                return;
            materialization->stagedFallbackCounted.set(pageLine);
            ++materialization->stagedDirectFallbackLines;
            stats.page_materialization_staged_direct_fallback_lines++;
        };
        if (!exactPayload) {
            materialization->stagedDisallowed.set(pageLine);
            countDirectFallback();
        }
        if (exactPayload && materialization->stagedDisallowed.test(pageLine)) {
            countDirectFallback();
        } else if (exactPayload) {
            const uint16_t firstWord = pageLine * wordsPerLine;
            bool overlap = false;
            for (uint16_t word = 0; word < wordsPerLine; ++word)
                overlap = overlap ||
                    ((wordMask & (1U << word)) != 0 &&
                     materialization->stagedWords.test(firstWord + word));
            if (overlap) {
                materialization->stagedDisallowed.set(pageLine);
                countDirectFallback();
            } else {
                const uint32_t firstElement = pageLine * wordsPerLine;
                for (uint16_t word = 0; word < wordsPerLine; ++word) {
                    if ((wordMask & (1U << word)) == 0)
                        continue;
                    if (materialization->wordBytes == sizeof(uint32_t)) {
                        uint32_t value = 0;
                        std::memcpy(&value,
                                    writeRespPayload + word * sizeof(value),
                                    sizeof(value));
                        spd->stageData<uint32_t>(
                            materialization->destinationTile,
                            firstElement + word, value);
                    } else {
                        uint64_t value = 0;
                        std::memcpy(&value,
                                    writeRespPayload + word * sizeof(value),
                                    sizeof(value));
                        spd->stageData<uint64_t>(
                            materialization->destinationTile,
                            firstElement + word, value);
                    }
                    materialization->stagedWords.set(firstWord + word);
                }
                directStaged = true;
                ++materialization->stagedDirectFragments;
                stats.page_materialization_staged_direct_fragments++;
                uint16_t stagedMask = 0;
                for (uint16_t word = 0; word < wordsPerLine; ++word) {
                    if (materialization->stagedWords.test(firstWord + word))
                        stagedMask |= static_cast<uint16_t>(1U << word);
                }
                if (stagedMask == fullMask) {
                    const Cycles spdLatency = spd->setDataLatency(
                        materialization->destinationTile, wordsPerLine);
                    if (reservePageMaterializationDirectCommit(
                            owner, static_cast<uint16_t>(lineID),
                            getClockEdge(spdLatency))) {
                        panic_if(!directRetirementContexts.
                                     beginMaterializeDirect(
                                         owner,
                                         static_cast<uint16_t>(lineID)),
                                 "Page materializer could not seal direct "
                                 "staged line\n");
                        directCompleted = true;
                        ++materialization->stagedDirectLines;
                        stats.page_materialization_staged_direct_lines++;
                    } else {
                        // The payload is still private and the line remains
                        // ReadyForRead, so the normal coherent cache path
                        // can replace it without an early visibility leak.
                        materialization->stagedDisallowed.set(pageLine);
                        countDirectFallback();
                    }
                }
            }
        }
    }
    HybridConsumerContextQueue::Request captured;
    bool forwarded = !directCompleted && wordMask == fullMask &&
        writeRespPayload != nullptr &&
        payloadBytes == HybridConsumerPipeline::LineBytes &&
        materialization->pageActive &&
        lineID / directRetirementContexts.producerPageLines(owner) ==
            materialization->page &&
        directRetirementContexts.captureMaterializationLine(
            owner, static_cast<uint16_t>(lineID),
            reinterpret_cast<const std::byte *>(writeRespPayload),
            payloadBytes, &captured);
    auto fragmentCapture = HybridConsumerPipeline::FragmentCapture::Disabled;
    if (!directStaged && !forwarded && wordMask != fullMask &&
        materialization->pageActive &&
        lineID / directRetirementContexts.producerPageLines(owner) ==
            materialization->page) {
        fragmentCapture =
            directRetirementContexts.captureMaterializationFragment(
                owner, ack,
                reinterpret_cast<const std::byte *>(writeRespPayload),
                payloadBytes,
                static_cast<uint8_t>(
                    page_materialization_fragment_buffers),
                &captured);
        forwarded = fragmentCapture ==
            HybridConsumerPipeline::FragmentCapture::Captured;
        if (fragmentCapture ==
                HybridConsumerPipeline::FragmentCapture::NoBuffer)
            stats.page_materialization_fragment_buffer_stalls++;
    }
    Tick commitTick = 0;
    if (forwarded) {
        const Cycles spdLatency = spd->setDataLatency(
            materialization->destinationTile, wordsPerLine);
        commitTick = getClockEdge(spdLatency);
        panic_if(!reservePageMaterializationCommit(captured, commitTick),
                 "Page materializer exhausted forwarded line commits\n");
        ++materialization->forwardedLines;
        stats.page_materialization_forwarded_lines++;
        if (fragmentCapture ==
            HybridConsumerPipeline::FragmentCapture::Captured)
            stats.page_materialization_fragment_accumulated_lines++;
    }
    DPRINTF(MAAVirtualTrace,
            "event=page_materialization_producer_line_ready schema=1 "
            "occurrence=%lu token=%u generation=%lu incarnation=%lu "
            "page=%u line=%d word_mask=0x%x transaction=%lu forwarded=%d "
            "fragment_capture=%u fragment_accumulated=%d "
            "fragment_buffer_stall=%d commit_tick=%lu\n",
            pageMaterializationTraceOccurrence++, owner.tokenTile,
            owner.generation, owner.incarnation, materialization->page,
            lineID, wordMask, transactionID, forwarded,
            static_cast<unsigned>(fragmentCapture),
            fragmentCapture ==
                HybridConsumerPipeline::FragmentCapture::Captured,
            fragmentCapture ==
                HybridConsumerPipeline::FragmentCapture::NoBuffer,
            commitTick);
    schedulePageMaterializationEvent();
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
    if (inactive_page_masked_fragment_retention_lines != 0) {
        const InactiveProducerMaskedFragmentRetention::Key key{
            static_cast<uint16_t>(tokenTileID),
            virtualPageGeneration[tokenTileID],
            virtualPagePayloadIncarnation[tokenTileID],
            virtualPageBackingAddr[tokenTileID]};
        if (inactiveMaskedFragmentRetention.active(key)) {
            const auto sealed = inactiveMaskedFragmentRetention.sealPage(
                key, static_cast<uint8_t>(pageID));
            panic_if(sealed !=
                         InactiveProducerMaskedFragmentRetention::
                             SealResult::Sealed,
                     "virtual page token=%d page=%d could not seal exact "
                     "inactive masked-fragment partition\n",
                     tokenTileID, pageID);
        }
    }
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

    DirectRetirementExecution *direct_execution =
        findDirectRetirementExecution(
            tokenTileID, virtualPageGeneration[tokenTileID]);
    PageMaterializationExecution *materialization =
        findPageMaterializationExecution(
            tokenTileID, virtualPageGeneration[tokenTileID]);
    panic_if(direct_execution != nullptr && materialization != nullptr,
             "Token %d generation %lu has two live page ACK authorities\n",
             tokenTileID, virtualPageGeneration[tokenTileID]);
    if (direct_execution != nullptr) {
        const HybridConsumerPipeline::ProducerAck ack{
            direct_execution->key.generation,
            static_cast<uint8_t>(pageID), transactionID};
        HybridConsumerContextQueue::Snapshot before;
        panic_if(!directRetirementContexts.snapshot(
                     direct_execution->key, &before) ||
                     !directRetirementContexts.notifyProducerWriteAck(
                         direct_execution->key, ack),
                 "Direct-retirement rejected final producer WriteResp "
                 "token=%d page=%d transaction=%lu\n",
                 tokenTileID, pageID, transactionID);
        HybridConsumerContextQueue::Snapshot after;
        panic_if(!directRetirementContexts.snapshot(
                     direct_execution->key, &after),
                 "Direct-retirement producer page lost its snapshot\n");
        stats.direct_retirement_producer_acks++;
        stats.direct_retirement_page_fallback_lines +=
            after.producerPageFallbackLines -
            before.producerPageFallbackLines;
        DPRINTF(MAAVirtualTrace,
                "event=direct_retirement_producer_ack schema=1 "
                "occurrence=%lu token=%u generation=%lu incarnation=%lu "
                "page=%d transaction=%lu\n",
                directRetirementTraceOccurrence++,
                direct_execution->key.tokenTile,
                direct_execution->key.generation,
                direct_execution->key.incarnation, pageID, transactionID);
        scheduleDirectRetirementEvent();
    } else if (materialization != nullptr) {
        const HybridConsumerPipeline::ProducerAck ack{
            materialization->key.generation,
            static_cast<uint8_t>(pageID), transactionID};
        HybridConsumerContextQueue::Snapshot before;
        panic_if(!directRetirementContexts.snapshot(
                     materialization->key, &before) ||
                     !directRetirementContexts.notifyProducerWriteAck(
                         materialization->key, ack),
                 "Page materializer rejected final producer WriteResp "
                 "token=%d page=%d transaction=%lu\n",
                 tokenTileID, pageID, transactionID);
        HybridConsumerContextQueue::Snapshot after;
        panic_if(!directRetirementContexts.snapshot(
                     materialization->key, &after),
                 "Page materializer producer page lost its snapshot\n");
        stats.page_materialization_page_fallback_lines +=
            after.producerPageFallbackLines -
            before.producerPageFallbackLines;
        DPRINTF(MAAVirtualTrace,
                "event=page_materialization_producer_ack schema=1 "
                "occurrence=%lu token=%u generation=%lu incarnation=%lu "
                "page=%d transaction=%lu fallback_lines=%u\n",
                pageMaterializationTraceOccurrence++,
                materialization->key.tokenTile,
                materialization->key.generation,
                materialization->key.incarnation, pageID, transactionID,
                after.producerPageFallbackLines -
                    before.producerPageFallbackLines);
        schedulePageMaterializationEvent();
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
      ADD_STAT(cpu_spd_boundary_prefetch_drops,
               statistics::units::Count::get(),
               "speculative cache-line reads dropped beyond physical SPD"),
      ADD_STAT(cpu_spd_out_of_range_rejections,
               statistics::units::Count::get(),
               "non-droppable CPU SPD accesses rejected by the aperture"),
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
      ADD_STAT(direct_retirement_early_line_overflows,
               statistics::units::Count::get(),
               "pre-admission line events conservatively left to exact "
               "page fallback because the fixed ledger was full"),
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
               "maximum direct-retirement 64-byte credits in use across "
               "the fixed four contexts"),
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
      ADD_STAT(direct_retirement_context_high_water,
               statistics::units::Count::get(),
               "maximum live direct-retirement contexts (bounded at four)"),
      ADD_STAT(direct_retirement_context_full_stalls,
               statistics::units::Count::get(),
               "descriptor admissions retried because all four direct "
               "contexts were occupied"),
      ADD_STAT(direct_retirement_request_record_high_water,
               statistics::units::Count::get(),
               "maximum fixed direct-retirement cache request records in "
               "use (bounded at 64)"),
      ADD_STAT(direct_retirement_fallbacks, statistics::units::Count::get(),
               "direct-retirement descriptors retained on the existing "
               "partial or unaligned fallback"),
      ADD_STAT(direct_retirement_payload_bytes,
               statistics::units::Byte::get(),
               "maximum direct-retirement cache-line payload provisioned"),
      ADD_STAT(direct_retirement_control_bytes,
               statistics::units::Byte::get(),
               "conservative persistent direct-retirement scheduler state"),
      ADD_STAT(page_materialization_submissions,
               statistics::units::Count::get(),
               "token-bound ordinary STREAM_LD pages admitted by the "
               "bounded concurrent materializer"),
      ADD_STAT(page_materialization_pages,
               statistics::units::Count::get(),
               "ordinary physical SPD pages completed by the materializer"),
      ADD_STAT(page_materialization_retirements,
               statistics::units::Count::get(),
               "16K token/generation materializer lifetimes retired after "
               "four exact page closures"),
      ADD_STAT(page_materialization_forwarded_lines,
               statistics::units::Count::get(),
               "full producer WriteResp lines forwarded through charged "
               "materializer buffers"),
      ADD_STAT(page_materialization_fragment_accumulated_lines,
               statistics::units::Count::get(),
               "masked producer lines assembled from authenticated fragments "
               "in charged active-page materializer buffers"),
      ADD_STAT(page_materialization_fragment_buffer_stalls,
               statistics::units::Count::get(),
               "authenticated active-page masked fragments not retained "
               "because the configured charged buffer bound was occupied"),
      ADD_STAT(page_materialization_inactive_payload_captures,
               statistics::units::Count::get(),
               "full producer WriteResp payloads retained while their "
               "logical 4K materializer page was inactive"),
      ADD_STAT(page_materialization_inactive_payload_replays,
               statistics::units::Count::get(),
               "inactive producer payloads copied into existing charged "
               "materializer buffers and delayed commits"),
      ADD_STAT(page_materialization_inactive_payload_conflicts,
               statistics::units::Count::get(),
               "direct-indexed inactive payload captures meeting a live "
               "different exact tag under either conflict policy"),
      ADD_STAT(page_materialization_inactive_payload_drops,
               statistics::units::Count::get(),
               "full inactive producer payloads deterministically left to "
               "coherent fallback after conflict, eviction, port, untracked, "
               "stale, invalid, or descriptor-full outcomes"),
      ADD_STAT(page_materialization_inactive_payload_first_owner_conflicts,
               statistics::units::Count::get(),
               "direct-index collisions retained by the first-owner policy"),
      ADD_STAT(page_materialization_inactive_payload_latest_owner_overwrites,
               statistics::units::Count::get(),
               "reserved ABI field; always zero because latest-owner is "
               "unsupported"),
      ADD_STAT(page_materialization_inactive_payload_latest_owner_evictions,
               statistics::units::Count::get(),
               "reserved ABI field; always zero because latest-owner is "
               "unsupported"),
      ADD_STAT(page_materialization_inactive_payload_write_port_stalls,
               statistics::units::Count::get(),
               "full inactive producer WriteResp payloads dropped because "
               "the fixed capture RAM write port was busy"),
      ADD_STAT(page_materialization_inactive_payload_read_port_stalls,
               statistics::units::Count::get(),
               "selected inactive payload replays delayed because the fixed "
               "capture RAM read port was busy"),
      ADD_STAT(page_materialization_inactive_payload_lookup_hits,
               statistics::units::Count::get(),
               "selected materializer lines found by one direct payload "
               "capture probe"),
      ADD_STAT(page_materialization_inactive_payload_lookup_misses,
               statistics::units::Count::get(),
               "selected materializer lines not found by one direct payload "
               "capture probe"),
      ADD_STAT(page_materialization_inactive_payload_high_water,
               statistics::units::Count::get(),
               "maximum full-line inactive producer payloads retained in "
               "the fixed capture"),
      ADD_STAT(page_materialization_inactive_payload_bytes,
               statistics::units::Byte::get(),
               "configured packed payload RAM plus one 64-byte output latch"),
      ADD_STAT(page_materialization_inactive_payload_control_bytes,
               statistics::units::Byte::get(),
               "configured packed RAM tags, direct descriptors, port state, "
               "output tag, MAA lookup control, and persistent per-token "
               "payload incarnation state"),
      ADD_STAT(page_materialization_inactive_masked_fragments_accepted,
               statistics::units::Count::get(),
               "authoritative inactive producer fragments accepted"),
      ADD_STAT(page_materialization_inactive_masked_words_merged,
               statistics::units::Count::get(),
               "non-overlapping authoritative words merged while inactive"),
      ADD_STAT(page_materialization_inactive_masked_lines_reconstructed,
               statistics::units::Count::get(),
               "inactive lines reaching an exact complete word mask"),
      ADD_STAT(page_materialization_inactive_masked_replay_hits,
               statistics::units::Count::get(),
               "sealed exact reconstructed lines replayed"),
      ADD_STAT(page_materialization_inactive_masked_replay_misses,
               statistics::units::Count::get(),
               "sealed materializer probes using coherent fallback"),
      ADD_STAT(page_materialization_inactive_masked_tag_conflicts,
               statistics::units::Count::get(),
               "first-owner direct-index conflicts poisoning incoming lines"),
      ADD_STAT(page_materialization_inactive_masked_overlap_poison,
               statistics::units::Count::get(),
               "overlapping authoritative words poisoning logical lines"),
      ADD_STAT(page_materialization_inactive_masked_write_port_poison,
               statistics::units::Count::get(),
               "fragments lost at occupied one-write-port banks"),
      ADD_STAT(page_materialization_inactive_masked_stale_untracked_drops,
               statistics::units::Count::get(),
               "stale, invalid, or untracked fragments rejected fail-closed"),
      ADD_STAT(page_materialization_inactive_masked_read_port_stalls,
               statistics::units::Count::get(),
               "replays delayed at the shared one-read-port path"),
      ADD_STAT(page_materialization_inactive_masked_clears,
               statistics::units::Count::get(),
               "exact masked-fragment lifetime partitions cleared"),
      ADD_STAT(page_materialization_inactive_masked_high_water,
               statistics::units::Count::get(),
               "maximum active masked-fragment entries"),
      ADD_STAT(page_materialization_inactive_masked_bytes,
               statistics::units::Byte::get(),
               "configured masked-fragment payload and output latch bytes"),
      ADD_STAT(page_materialization_inactive_masked_control_bytes,
               statistics::units::Byte::get(),
               "configured tags, poison, descriptors, ports, counters, and "
               "MAA exact-identity control bytes"),
      ADD_STAT(page_materialization_cache_read_fallback_lines,
               statistics::units::Count::get(),
               "ACK-gated coherent backing lines read when producer payload "
               "forwarding was unavailable"),
      ADD_STAT(page_materialization_dispatch_fallbacks,
               statistics::units::Count::get(),
               "token-bound page loads dispatched on the sole ordinary "
               "STREAM unit after materializer admission fallback"),
      ADD_STAT(page_materialization_admission_fallbacks,
               statistics::units::Count::get(),
               "materializer admissions rejected for static, ABI, or "
               "bounded-resource reasons"),
      ADD_STAT(page_materialization_producer_line_acks,
               statistics::units::Count::get(),
               "materializer backing lines exposed by exact producer "
               "WriteResp authority"),
      ADD_STAT(page_materialization_page_fallback_lines,
               statistics::units::Count::get(),
               "materializer backing lines conservatively exposed by final "
               "producer page WriteResp authority"),
      ADD_STAT(page_materialization_staged_direct_lines,
               statistics::units::Count::get(),
               "masked producer lines directly staged in active SPD pages"),
      ADD_STAT(page_materialization_staged_direct_fragments,
               statistics::units::Count::get(),
               "authenticated masked fragments directly staged in SPD"),
      ADD_STAT(page_materialization_staged_direct_fallback_lines,
               statistics::units::Count::get(),
               "direct-stage candidates conservatively returned to coherent "
               "reads"),
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
        IND_SoaJitInstructions.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitInstructions"),
            statistics::units::Count::get(),
            "guarded SoA/JIT indirect RMW instructions completed"));
        IND_SoaJitSelected.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitSelected"),
            statistics::units::Count::get(),
            "predicate-selected SoA/JIT logical iterations"));
        IND_SoaJitPredicateRejected.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitPredicateRejected"),
            statistics::units::Count::get(),
            "predicate-rejected SoA/JIT logical iterations"));
        IND_SoaJitPredicateLineReads.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitPredicateLineReads"),
            statistics::units::Count::get(),
            "timed cache-line reads of optional SoA predicates"));
        IND_SoaJitPredicateLineResponses.push_back(new statistics::Scalar(
            this,
            MAKE_INDIRECT_STAT_NAME("IND_SoaJitPredicateLineResponses"),
            statistics::units::Count::get(),
            "exact timed cache-line responses for optional SoA predicates"));
        IND_SoaJitPredicateLineHits.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitPredicateLineHits"),
            statistics::units::Count::get(),
            "ordered predicate lookups that hit a valid feeder line"));
        IND_SoaJitPredicateUses.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitPredicateUses"),
            statistics::units::Count::get(),
            "predicate words consumed in logical iteration order"));
        IND_SoaJitPredicateFeederStalls.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitPredicateFeederStalls"),
            statistics::units::Count::get(),
            "ordered predicate lookups stalled on a pending feeder line"));
        IND_SoaJitPredicateActiveCredits.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitPredicateActiveCredits"),
            statistics::units::Count::get(),
            "sum of active predicate credits across completed operations"));
        IND_SoaJitPredicateFeederHighWater.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_SoaJitPredicateFeederHighWater"),
                statistics::units::Count::get(),
                "sum of per-operation predicate slot high-water marks"));
        IND_SoaJitPredicateFeederStateBytes.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_SoaJitPredicateFeederStateBytes"),
                statistics::units::Byte::get(),
                "sum of fixed predicate feeder state bytes per operation"));
        IND_SoaJitAReadIssues.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitAReadIssues"),
            statistics::units::Count::get(),
            "selected A cache-line read issues"));
        IND_SoaJitAReadResponses.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitAReadResponses"),
            statistics::units::Count::get(),
            "exact selected A cache-line read responses"));
        IND_SoaJitValueReadIssues.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitValueReadIssues"),
            statistics::units::Count::get(),
            "just-in-time timed value read issues"));
        IND_SoaJitValueReadResponses.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitValueReadResponses"),
            statistics::units::Count::get(),
            "just-in-time timed value read responses"));
        IND_SoaJitValueFills.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitValueFills"),
            statistics::units::Count::get(),
            "completed fills into the fixed SoA/JIT value cache"));
        IND_SoaJitValueCachedResponses.push_back(new statistics::Scalar(
            this,
            MAKE_INDIRECT_STAT_NAME("IND_SoaJitValueCachedResponses"),
            statistics::units::Count::get(),
            "SoA/JIT value fills whose timed response was cache-resident"));
        IND_SoaJitValueHits.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitValueHits"),
            statistics::units::Count::get(),
            "alias values served by ready fixed-cache lines"));
        IND_SoaJitValueMergedWaiters.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitValueMergedWaiters"),
            statistics::units::Count::get(),
            "alias values merged behind one exact filling line"));
        IND_SoaJitValueEvictions.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitValueEvictions"),
            statistics::units::Count::get(),
            "bounded LRU evictions from the fixed value cache"));
        IND_SoaJitValueDeliveries.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitValueDeliveries"),
            statistics::units::Count::get(),
            "value-cache deliveries into ordered lookahead slots"));
        IND_SoaJitValueStalls.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitValueStalls"),
            statistics::units::Count::get(),
            "alias requests stalled by four non-evictable value lines"));
        IND_SoaJitValueCacheHighWater.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitValueCacheHighWater"),
            statistics::units::Count::get(),
            "sum of per-instruction fixed value-cache high water"));
        IND_SoaJitValuePrefetchIssues.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitValuePrefetchIssues"),
            statistics::units::Count::get(),
            "sequential value-line prefetch read issues"));
        IND_SoaJitValuePrefetchResponses.push_back(new statistics::Scalar(
            this,
            MAKE_INDIRECT_STAT_NAME("IND_SoaJitValuePrefetchResponses"),
            statistics::units::Count::get(),
            "exact sequential value-line prefetch responses"));
        IND_SoaJitValuePrefetchPromotions.push_back(new statistics::Scalar(
            this,
            MAKE_INDIRECT_STAT_NAME("IND_SoaJitValuePrefetchPromotions"),
            statistics::units::Count::get(),
            "prefetch responses promoted to waiting demand aliases"));
        IND_SoaJitValuePrefetchDiscards.push_back(new statistics::Scalar(
            this,
            MAKE_INDIRECT_STAT_NAME("IND_SoaJitValuePrefetchDiscards"),
            statistics::units::Count::get(),
            "prefetch responses without coalescer demand waiters"));
        IND_SoaJitValuePrefetchOwned.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitValuePrefetchOwned"),
            statistics::units::Count::get(),
            "sequential candidates already owned by demand state"));
        IND_SoaJitValuePrefetchCreditStalls.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_SoaJitValuePrefetchCreditStalls"),
                statistics::units::Count::get(),
                "sequential candidates blocked by active prefetch credits"));
        IND_SoaJitValuePrefetchActiveCredits.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_SoaJitValuePrefetchActiveCredits"),
                statistics::units::Count::get(),
                "sum of active prefetch credits across completed operations"));
        IND_SoaJitValuePrefetchHighWater.push_back(new statistics::Scalar(
            this,
            MAKE_INDIRECT_STAT_NAME("IND_SoaJitValuePrefetchHighWater"),
            statistics::units::Count::get(),
            "sum of per-operation prefetch-credit high-water marks"));
        IND_SoaJitLookaheadIssues.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitLookaheadIssues"),
            statistics::units::Count::get(),
            "exact ordered alias lookahead slots allocated"));
        IND_SoaJitLookaheadResponses.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitLookaheadResponses"),
            statistics::units::Count::get(),
            "exact alias scalar deliveries retained by lookahead slots"));
        IND_SoaJitLookaheadStalls.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitLookaheadStalls"),
            statistics::units::Count::get(),
            "lookahead allocation retries after value-cache backpressure"));
        IND_SoaJitLookaheadHighWater.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitLookaheadHighWater"),
            statistics::units::Count::get(),
            "sum of per-instruction active ordered alias slots"));
        IND_SoaJitPreAValueIssues.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitPreAValueIssues"),
            statistics::units::Count::get(),
            "exact lookahead slots assigned while A read is outstanding"));
        IND_SoaJitPreAValueReadyAtAResponse.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_SoaJitPreAValueReadyAtAResponse"),
                statistics::units::Count::get(),
                "pre-A lookahead slots ready when exact A response arrives"));
        IND_SoaJitPreAValueUses.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitPreAValueUses"),
            statistics::units::Count::get(),
            "pre-A lookahead slots applied after exact A activation"));
        IND_SoaJitActiveContexts.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitActiveContexts"),
            statistics::units::Count::get(),
            "sum of configured active contexts for completed instructions"));
        IND_SoaJitActiveValueOwners.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitActiveValueOwners"),
            statistics::units::Count::get(),
            "sum of selected active value owners for completed instructions"));
        IND_SoaJitActiveApplyLanes.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitActiveApplyLanes"),
            statistics::units::Count::get(),
            "sum of selected active apply lanes for completed instructions"));
        IND_SoaJitApplyLaneHighWater.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitApplyLaneHighWater"),
            statistics::units::Count::get(),
            "sum of per-instruction concurrent apply-lane high water"));
        IND_SoaJitAliasesApplied.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitAliasesApplied"),
            statistics::units::Count::get(),
            "SoA/JIT duplicate aliases applied in Offset-chain order"));
        IND_SoaJitAWriteIssues.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitAWriteIssues"),
            statistics::units::Count::get(),
            "response-bearing A WriteReq issues"));
        IND_SoaJitAWriteResponses.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitAWriteResponses"),
            statistics::units::Count::get(),
            "exact A WriteResp completions"));
        IND_SoaJitOldResultCaptures.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitOldResultCaptures"),
            statistics::units::Count::get(),
            "selected aliases with an exact pre-update value captured"));
        IND_SoaJitOldResultWriteIssues.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitOldResultWriteIssues"),
            statistics::units::Count::get(),
            "response-bearing old-result cache-line WriteReq issues"));
        IND_SoaJitOldResultWriteResponses.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitOldResultWriteResponses"),
            statistics::units::Count::get(),
            "exact old-result WriteResp completions"));
        IND_SoaJitOldResultPressureIssues.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitOldResultPressureIssues"),
            statistics::units::Count::get(),
            "partial old-result WriteReqs issued by capacity pressure"));
        IND_SoaJitOldResultPartialCreditLimit.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_SoaJitOldResultPartialCreditLimit"),
                statistics::units::Count::get(),
                "sum of selected partial-write credits for completed "
                "old-result instructions"));
        IND_SoaJitOldResultPartialCreditHighWater.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_SoaJitOldResultPartialCreditHighWater"),
                statistics::units::Count::get(),
                "sum of per-instruction partial writes concurrently "
                "awaiting response"));
        IND_SoaJitOldResultDensePolicy.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitOldResultDensePolicy"),
            statistics::units::Count::get(),
            "completed old-result instructions using dense pressure scan"));
        IND_SoaJitOldResultCreditHighWater.push_back(new statistics::Scalar(
            this,
            MAKE_INDIRECT_STAT_NAME("IND_SoaJitOldResultCreditHighWater"),
            statistics::units::Count::get(),
            "sum of per-instruction occupied old-result credit high water"));
        IND_SoaJitOldResultStalls.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitOldResultStalls"),
            statistics::units::Count::get(),
            "ordered aliases stalled by bounded old-result credits"));
        IND_SoaJitContextHighWater.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitContextHighWater"),
            statistics::units::Count::get(),
            "sum of per-instruction bounded A-line context high water"));
        IND_SoaJitContextStalls.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitContextStalls"),
            statistics::units::Count::get(),
            "A-line claims blocked by the bounded context scoreboard"));
        IND_SoaJitEpochDrains.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitEpochDrains"),
            statistics::units::Count::get(),
            "ordered SoA/JIT Row/Offset pressure epochs drained"));
        IND_SoaJitTerminalCompletions.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitTerminalCompletions"),
            statistics::units::Count::get(),
            "SoA/JIT completions after exact scoreboard and WriteResp drain"));
        IND_SoaJitPageFedOperations.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitPageFedOperations"),
            statistics::units::Count::get(),
            "bounded page-fed SoA/JIT operations completed"));
        IND_SoaJitPageFedAdmitCommands.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitPageFedAdmitCommands"),
            statistics::units::Count::get(),
            "completed physical index-page admission commands"));
        IND_SoaJitPageFedCloseCommands.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitPageFedCloseCommands"),
            statistics::units::Count::get(),
            "exact page-fed closure commands"));
        IND_SoaJitPageFedCommandResponses.push_back(new statistics::Scalar(
            this,
            MAKE_INDIRECT_STAT_NAME("IND_SoaJitPageFedCommandResponses"),
            statistics::units::Count::get(),
            "timed page-admission and close command responses"));
        IND_SoaJitPageFedAdmittedWords.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitPageFedAdmittedWords"),
            statistics::units::Count::get(),
            "physical index words admitted with logical ordinals"));
        IND_SoaJitPageFedSpdIndexReads.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitPageFedSpdIndexReads"),
            statistics::units::Count::get(),
            "timed physical SPD index-word reads"));
        IND_SoaJitPageFedRowWrites.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_SoaJitPageFedRowWrites"),
            statistics::units::Count::get(),
            "timed Row/Offset admission writes"));
        IND_SoaJitPageFedAdmissionCycles.push_back(new statistics::Scalar(
            this,
            MAKE_INDIRECT_STAT_NAME("IND_SoaJitPageFedAdmissionCycles"),
            statistics::units::Cycle::get(),
            "port-charged page-fed admission cycles"));
        IND_SoaJitPageFedCoherentIndexReadLines.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_SoaJitPageFedCoherentIndexReadLines"),
                statistics::units::Count::get(),
                "candidate coherent index cache-line read issues"));
        IND_SoaJitPageFedCoherentIndexWriteLines.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_SoaJitPageFedCoherentIndexWriteLines"),
                statistics::units::Count::get(),
                "candidate coherent index publication line issues"));
        IND_SoaJitPageFedStateByteOperations.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_SoaJitPageFedStateByteOperations"),
                statistics::units::Byte::get(),
                "sum of 16-byte page-fed state capacity observations "
                "across completed operations"));
        IND_SoaJitPageFedProductReadySignals.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_SoaJitPageFedProductReadySignals"),
                statistics::units::Count::get(),
                "publisher-terminal product-ready notifications"));
        IND_SoaJitPageFedValueReadinessStalls.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_SoaJitPageFedValueReadinessStalls"),
                statistics::units::Count::get(),
                "ordered value reads or prefetches blocked by page "
                "publication readiness"));
        IND_SoaJitPageFedFirstReadyTicks.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_SoaJitPageFedFirstReadyTicks"),
                statistics::units::Tick::get(),
                "sum of first product-ready ticks across operations"));
        IND_SoaJitPageFedLastReadyTicks.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_SoaJitPageFedLastReadyTicks"),
                statistics::units::Tick::get(),
                "sum of last product-ready ticks across operations"));
        IND_SoaJitPageFedExecutionBeforeAllReady.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_SoaJitPageFedExecutionBeforeAllReady"),
                statistics::units::Count::get(),
                "closed Row/Offset executions begun before all product "
                "pages were response-published"));
        IND_SoaJitPageFedTerminalClosures.push_back(
            new statistics::Scalar(
                this,
                MAKE_INDIRECT_STAT_NAME(
                    "IND_SoaJitPageFedTerminalClosures"),
                statistics::units::Count::get(),
                "page-fed terminals with all products and exact traffic "
                "closed"));
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
        IND_VirtPageOrderedDrainSelections.push_back(new statistics::Scalar(
            this,
            MAKE_INDIRECT_STAT_NAME("IND_VirtPageOrderedDrainSelections"),
            statistics::units::Count::get(),
            "full virtual-combiner lines selected by logical output page"));
        IND_VirtPageOrderedDrainDeferrals.push_back(new statistics::Scalar(
            this, MAKE_INDIRECT_STAT_NAME("IND_VirtPageOrderedDrainDeferrals"),
            statistics::units::Count::get(),
            "page-ordered selections that deferred a later full line"));
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
        STR_PublishIssues.push_back(new statistics::Scalar(
            this, MAKE_STREAM_STAT_NAME("STR_PublishIssues"),
            statistics::units::Count::get(),
            "exact response-bearing SPD publisher WriteReq issues"));
        STR_PublishAccepts.push_back(new statistics::Scalar(
            this, MAKE_STREAM_STAT_NAME("STR_PublishAccepts"),
            statistics::units::Count::get(),
            "publisher WriteReqs accepted by the cache path"));
        STR_PublishRetries.push_back(new statistics::Scalar(
            this, MAKE_STREAM_STAT_NAME("STR_PublishRetries"),
            statistics::units::Count::get(),
            "publisher cache-path request refusals retaining retry "
            "ownership"));
        STR_PublishWriteResponses.push_back(new statistics::Scalar(
            this, MAKE_STREAM_STAT_NAME("STR_PublishWriteResponses"),
            statistics::units::Count::get(),
            "unique exact publisher WriteResp completions"));
        STR_PublishCreditHWM.push_back(new statistics::Scalar(
            this, MAKE_STREAM_STAT_NAME("STR_PublishCreditHWM"),
            statistics::units::Count::get(),
            "maximum live publisher payload credits"));
        STR_PublishCreditStalls.push_back(new statistics::Scalar(
            this, MAKE_STREAM_STAT_NAME("STR_PublishCreditStalls"),
            statistics::units::Count::get(),
            "publisher service observations blocked by all eight credits"));
        STR_PublishOverlapIssues.push_back(new statistics::Scalar(
            this, MAKE_STREAM_STAT_NAME("STR_PublishOverlapIssues"),
            statistics::units::Count::get(),
            "publisher WriteReq issues observed while a non-stream unit "
            "for the same MAA was active"));
        STR_PublishTerminals.push_back(new statistics::Scalar(
            this, MAKE_STREAM_STAT_NAME("STR_PublishTerminals"),
            statistics::units::Count::get(),
            "publisher operations completed after the final unique ACK"));
        STR_AvgWordsPerCacheLine.push_back(new statistics::Formula(
            this, MAKE_STREAM_STAT_NAME("STR_AvgWordsPerCacheLine"),
            statistics::units::Count::get(),
            "average number of words per cacheline"));
        STR_AvgCacheLinesPerInst.push_back(new statistics::Formula(
            this, MAKE_STREAM_STAT_NAME("STR_AvgCacheLinesPerInst"),
            statistics::units::Count::get(),
            "average number of cachelines per stream instruction"));
        STR_AvgRTFullsPerInst.push_back(new statistics::Formula(
            this, MAKE_STREAM_STAT_NAME("STR_AvgRTFullsPerInst"),
            statistics::units::Count::get(),
            "average number of request table full events per stream "
            "instruction"));
        STR_CyclesRequest.push_back(new statistics::Scalar(
            this, MAKE_STREAM_STAT_NAME("STR_CyclesRequest"),
            statistics::units::Count::get(),
            "number of cycles in the REQUEST stage"));
        STR_CyclesRTAccess.push_back(new statistics::Scalar(
            this, MAKE_STREAM_STAT_NAME("STR_CyclesRTAccess"),
            statistics::units::Count::get(),
            "number of cycles for request table access"));
        STR_CyclesSPDReadAccess.push_back(new statistics::Scalar(
            this, MAKE_STREAM_STAT_NAME("STR_CyclesSPDReadAccess"),
            statistics::units::Count::get(),
            "number of cycles for SPD read access"));
        STR_CyclesSPDWriteAccess.push_back(new statistics::Scalar(
            this, MAKE_STREAM_STAT_NAME("STR_CyclesSPDWriteAccess"),
            statistics::units::Count::get(),
            "number of cycles for SPD write access"));
        STR_AvgCyclesRequestPerInst.push_back(new statistics::Formula(
            this, MAKE_STREAM_STAT_NAME("STR_AvgCyclesRequestPerInst"),
            statistics::units::Count::get(),
            "average number of cycles in the REQUEST stage per stream "
            "instruction"));
        STR_AvgCyclesRTAccessPerInst.push_back(new statistics::Formula(
            this, MAKE_STREAM_STAT_NAME("STR_AvgCyclesRTAccessPerInst"),
            statistics::units::Count::get(),
            "average number of cycles for request table access per stream "
            "instruction"));
        STR_AvgCyclesSPDReadAccessPerInst.push_back(
            new statistics::Formula(
                this,
                MAKE_STREAM_STAT_NAME("STR_AvgCyclesSPDReadAccessPerInst"),
                statistics::units::Count::get(),
                "average number of cycles for SPD read access per stream "
                "instruction"));
        STR_AvgCyclesSPDWriteAccessPerInst.push_back(
            new statistics::Formula(
                this,
                MAKE_STREAM_STAT_NAME("STR_AvgCyclesSPDWriteAccessPerInst"),
                statistics::units::Count::get(),
                "average number of cycles for SPD write access per stream "
                "instruction"));
        STR_LoadsCacheAccessing.push_back(new statistics::Scalar(
            this, MAKE_STREAM_STAT_NAME("STR_LoadsCacheAccessing"),
            statistics::units::Count::get(),
            "number of loads accessed from cache"));
        STR_AvgLoadsCacheAccessingPerInst.push_back(
            new statistics::Formula(
                this,
                MAKE_STREAM_STAT_NAME("STR_AvgLoadsCacheAccessingPerInst"),
                statistics::units::Count::get(),
                "average number of loads accessed from cache per stream "
                "instruction"));
        STR_Evicts.push_back(new statistics::Scalar(
            this, MAKE_STREAM_STAT_NAME("STR_Evicts"),
            statistics::units::Count::get(),
            "number of evict accesses to the cache side port"));
        STR_AvgEvictssPerInst.push_back(new statistics::Formula(
            this, MAKE_STREAM_STAT_NAME("STR_AvgEvictssPerInst"),
            statistics::units::Count::get(),
            "average number of evict accesses to the cache side port per "
            "stream instruction"));

        (*STR_NumInsts[stream_id]).flags(statistics::nozero);
        (*STR_NumWordsInserted[stream_id]).flags(statistics::nozero);
        (*STR_NumCacheLineInserted[stream_id]).flags(statistics::nozero);
        (*STR_NumRTFull[stream_id]).flags(statistics::nozero);
        (*STR_PublishIssues[stream_id]).flags(statistics::nozero);
        (*STR_PublishAccepts[stream_id]).flags(statistics::nozero);
        (*STR_PublishRetries[stream_id]).flags(statistics::nozero);
        (*STR_PublishWriteResponses[stream_id]).flags(statistics::nozero);
        (*STR_PublishCreditHWM[stream_id]).flags(statistics::nozero);
        (*STR_PublishCreditStalls[stream_id]).flags(statistics::nozero);
        (*STR_PublishOverlapIssues[stream_id]).flags(statistics::nozero);
        (*STR_PublishTerminals[stream_id]).flags(statistics::nozero);
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
