#include "mem/MAA/IndirectAccess.hh"

#include <algorithm>
#include <cassert>
#include <cstdint>
#include <limits>
#include <string>

#include "base/logging.hh"
#include "base/trace.hh"
#include "base/types.hh"
#include "debug/MAAIndirect.hh"
#include "debug/MAAIssueDigest.hh"
#include "debug/MAAIssueTrace.hh"
#include "debug/MAAMacroEvent.hh"
#include "debug/MAAPhysicalRecordTrace.hh"
#include "debug/MAAReorderTrace.hh"
#include "debug/MAATrace.hh"
#include "debug/MAAVirtualTrace.hh"
#include "mem/MAA/IF.hh"
#include "mem/MAA/MAA.hh"
#include "mem/MAA/SPD.hh"
#include "mem/MAA/SoaJitSafety.hh"
#include "mem/MAA/Tables.hh"
#include "mem/packet.hh"
#include "sim/cur_tick.hh"

#ifndef TRACING_ON
#define TRACING_ON 1
#endif

namespace gem5 {

///////////////
//
// INDIRECT ACCESS UNIT
//
///////////////
IndirectAccessUnit::IndirectAccessUnit()
    : executeInstructionEvent([this] { executeInstruction(); }, name()) {
    RT_slice_org = nullptr;
    num_RT_slices = nullptr;
    num_RT_rows_total = nullptr;
    num_RT_possible_grows = nullptr;
    num_RT_subslices = nullptr;
    num_RT_slice_columns = nullptr;
    RT_config_addr = nullptr;
    RT_config_cache = nullptr;
    RT_config_cache_tick = nullptr;
    RT = nullptr;
    offset_table = nullptr;
    my_RT_req_sent = nullptr;
    my_RT_slice_order = nullptr;
    my_instruction = nullptr;
}
IndirectAccessUnit::~IndirectAccessUnit() {
    assert(RT_slice_org != nullptr);
    for (int i = 0; i < num_RT_configs; i++) {
        assert(RT_slice_org[i] != nullptr);
        delete[] RT_slice_org[i];
    }
    delete[] RT_slice_org;
    assert(num_RT_slices != nullptr);
    delete[] num_RT_slices;
    assert(num_RT_rows_total != nullptr);
    delete[] num_RT_rows_total;
    assert(num_RT_possible_grows != nullptr);
    delete[] num_RT_possible_grows;
    assert(num_RT_subslices != nullptr);
    delete[] num_RT_subslices;
    assert(num_RT_slice_columns != nullptr);
    delete[] num_RT_slice_columns;
    assert(RT_config_addr != nullptr);
    delete[] RT_config_addr;
    assert(RT_config_cache != nullptr);
    delete[] RT_config_cache;
    assert(RT_config_cache_tick != nullptr);
    delete[] RT_config_cache_tick;
    assert(RT != nullptr);
    for (int i = 0; i < num_RT_configs; i++) {
        if (RT[i] != nullptr)
            delete[] RT[i];
    }
    delete[] RT;
    assert(offset_table != nullptr);
    delete offset_table;
    assert(my_RT_req_sent != nullptr);
    for (int i = 0; i < num_RT_configs; i++) {
        if (my_RT_req_sent[i] != nullptr)
            delete[] my_RT_req_sent[i];
    }
    delete[] my_RT_req_sent;
    assert(my_RT_slice_order != nullptr);
    delete[] my_RT_slice_order;
}
void IndirectAccessUnit::allocate(int _my_indirect_id,
                                  int _num_tile_elements,
                                  int _num_offset_table_entries,
                                  int _num_row_table_rows_per_slice,
                                  int _num_row_table_entries_per_subslice_row,
                                  int _num_row_table_config_cache_entries,
                                  bool _reconfigure_row_table,
                                  bool _reorder_row_table,
                                  int _num_initial_row_table_slice,
                                  int _virtual_combine_slots,
                                  int _virtual_combine_words,
                                  int _virtual_combine_ways,
                                  int _virtual_combine_victim_policy,
                                  int _virtual_combine_banks,
                                  int _virtual_response_slots,
                                  int _virtual_response_words,
                                  int _virtual_response_word_pool,
                                  int _virtual_words_per_cycle,
                                  int _virtual_max_outstanding_writes,
                                  bool _virtual_masked_writes,
                                  int _soa_jit_predicate_active_credits,
                                  int _virtual_index_buffer_lines,
                                  bool _virtual_index_force_cache,
                                  int _virtual_index_partitions,
                                  int _virtual_index_filter_words_per_cycle,
                                  int _soa_jit_active_contexts,
                                  int _soa_jit_value_lookahead,
                                  bool _soa_jit_value_cache_enable,
                                  bool _soa_jit_pre_a_value_lookahead,
                                  int _soa_jit_value_prefetch_credits,
                                  int _soa_jit_active_value_owners,
                                  int _soa_jit_apply_lanes,
                                  Cycles _rowtable_latency,
                                  int _num_channels,
                                  int _num_cores,
                                  MAA *_maa) {
    my_indirect_id = _my_indirect_id;
    maa = _maa;
    num_tile_elements = _num_tile_elements;
    num_RT_rows_per_slice = _num_row_table_rows_per_slice;
    num_RT_entries_per_subslice_row = _num_row_table_entries_per_subslice_row;
    num_RT_config_cache_entries = _num_row_table_config_cache_entries;
    reconfigure_RT = _reconfigure_row_table;
    reorder_RT = _reorder_row_table;
    num_initial_RT_slices = _num_initial_row_table_slice;
    panic_if(_virtual_combine_slots <= 0,
             "I[%d] virtual combiner must have at least one slot\n",
             my_indirect_id);
    virtual_combine_slots.resize(_virtual_combine_slots);
    virtual_combine_page_ready.reset(_virtual_combine_slots);
    virtual_combine_words_configured = _virtual_combine_words;
    virtual_combine_ways = _virtual_combine_ways;
    panic_if(virtual_combine_ways != 0 &&
                 (_virtual_combine_slots % virtual_combine_ways) != 0,
             "I[%d] virtual combiner slots (%d) must divide into %d ways\n",
             my_indirect_id, _virtual_combine_slots, virtual_combine_ways);
    if (virtual_combine_ways != 0)
        virtual_combine_set_victims.resize(
            _virtual_combine_slots / virtual_combine_ways, 0);
    panic_if(_virtual_combine_victim_policy < 0 ||
                 _virtual_combine_victim_policy > 2,
             "I[%d] invalid virtual combiner victim policy %d\n",
             my_indirect_id, _virtual_combine_victim_policy);
    virtual_combine_victim_policy = _virtual_combine_victim_policy;
    virtual_combine_banks = _virtual_combine_banks;
    panic_if(virtual_combine_banks != 0 && virtual_combine_ways == 0,
             "I[%d] banked virtual combiner requires finite associativity\n",
             my_indirect_id);
    const int virtual_combine_sets = virtual_combine_ways == 0
        ? 1 : _virtual_combine_slots / virtual_combine_ways;
    panic_if(virtual_combine_banks > virtual_combine_sets,
             "I[%d] virtual combiner banks (%d) exceed sets (%d)\n",
             my_indirect_id, virtual_combine_banks, virtual_combine_sets);
    virtual_combine_bank_used.resize(virtual_combine_banks, false);
    panic_if(_virtual_response_slots <= 0,
             "I[%d] virtual response buffer must have at least one slot\n",
             my_indirect_id);
    virtual_response_words = _virtual_response_words;
    virtual_response_word_pool_limit = _virtual_response_word_pool;
    virtual_response_slots.resize(_virtual_response_slots);
    virtual_response_line_payloads.configure(
        _virtual_response_slots,
        virtual_response_words != 0 ||
            virtual_response_word_pool_limit != 0);
    virtual_words_per_cycle_limit = _virtual_words_per_cycle;
    panic_if(_virtual_max_outstanding_writes <= 0,
             "I[%d] virtual retirement must allow at least one write\n",
             my_indirect_id);
    virtual_max_outstanding_writes_limit = _virtual_max_outstanding_writes;
    virtual_masked_writes = _virtual_masked_writes;
    panic_if(_soa_jit_predicate_active_credits != 1 &&
                 _soa_jit_predicate_active_credits != 4 &&
                 _soa_jit_predicate_active_credits != 8 &&
                 _soa_jit_predicate_active_credits != 16,
             "I[%d] SoA/JIT predicate credits must be 1/4/8/16, got %d\n",
             my_indirect_id, _soa_jit_predicate_active_credits);
    soa_jit_predicate_active_credits =
        _soa_jit_predicate_active_credits;
    panic_if(_virtual_index_buffer_lines <= 0 ||
                 _virtual_index_buffer_lines > 1024,
             "I[%d] direct-index buffer lines (%d) must be in [1,1024]\n",
             my_indirect_id, _virtual_index_buffer_lines);
    direct_index_buffer_lines = _virtual_index_buffer_lines;
    direct_index_force_cache = _virtual_index_force_cache;
    panic_if(_virtual_index_partitions <= 0 ||
                 _virtual_index_partitions > 64,
             "I[%d] direct-index partitions (%d) must be in [1,64]\n",
             my_indirect_id, _virtual_index_partitions);
    direct_index_partitions = _virtual_index_partitions;
    direct_index_max_partitions = _virtual_index_partitions;
    direct_index_filter_words_per_cycle =
        _virtual_index_filter_words_per_cycle;
    panic_if(_soa_jit_active_contexts != 8 &&
                 _soa_jit_active_contexts != 16 &&
                 _soa_jit_active_contexts != 32 &&
                 _soa_jit_active_contexts != 64,
             "I[%d] SoA/JIT active contexts (%d) must be 8, 16, 32, or 64\n",
             my_indirect_id, _soa_jit_active_contexts);
    panic_if(_soa_jit_value_lookahead != 1 &&
                 _soa_jit_value_lookahead != 2 &&
                 _soa_jit_value_lookahead != 4 &&
                 _soa_jit_value_lookahead != 8,
             "I[%d] SoA/JIT value lookahead (%d) must be 1, 2, 4, or 8\n",
             my_indirect_id, _soa_jit_value_lookahead);
    soa_jit_active_contexts = _soa_jit_active_contexts;
    soa_jit_value_lookahead = _soa_jit_value_lookahead;
    soa_jit_value_cache_enable = _soa_jit_value_cache_enable;
    soa_jit_pre_a_value_lookahead =
        _soa_jit_pre_a_value_lookahead;
    panic_if(_soa_jit_value_prefetch_credits < 0 ||
                 _soa_jit_value_prefetch_credits >
                     static_cast<int>(
                         SoaJitValueCoalescer::MaxPrefetchCredits) ||
                 !SoaJitValueCoalescer::isValidActivePrefetchCreditCount(
                     static_cast<uint8_t>(
                         _soa_jit_value_prefetch_credits)),
             "I[%d] SoA/JIT value prefetch credits (%d) must be "
             "0, 1, 2, 4, or 8\n",
             my_indirect_id, _soa_jit_value_prefetch_credits);
    soa_jit_value_prefetch_credits = _soa_jit_value_prefetch_credits;
    panic_if(!SoaJitValueCoalescer::isValidActiveOwnerCount(
                 _soa_jit_active_value_owners),
             "I[%d] SoA/JIT active value owners (%d) must be 4, 8, 16, "
             "32, 64, 96, or 128\n",
             my_indirect_id, _soa_jit_active_value_owners);
    soa_jit_active_value_owners = _soa_jit_active_value_owners;
    soa_jit_value_coalescer.configure(
        soa_jit_value_cache_enable, soa_jit_value_prefetch_credits,
        soa_jit_active_value_owners);
    panic_if(!SoaJitApplyLanePool::isValidActiveLaneCount(
                 _soa_jit_apply_lanes),
             "I[%d] SoA/JIT apply lanes (%d) must be 1, 2, or 4\n",
             my_indirect_id, _soa_jit_apply_lanes);
    soa_jit_apply_lanes = _soa_jit_apply_lanes;
    soa_jit_apply_lane_pool.configure(soa_jit_apply_lanes);
    soa_jit_apply_lane_pool.reset();
    rowtable_latency = _rowtable_latency;
    num_channels = _num_channels;
    num_cores = _num_cores;
    my_translation_done = false;
    state = Status::Idle;
    my_instruction = nullptr;
    dst_tile_id = -1;
    offset_table = new OffsetTable();
    offset_table->allocate(my_indirect_id, _num_offset_table_entries, maa,
                           false);

    // Row Table initialization
    int min_num_RT_slices = maa->m_org[ADDR_CHANNEL_LEVEL] * maa->m_org[ADDR_RANK_LEVEL] * 2;
    Addr max_num_RT_possible_grows = 2 * maa->m_org[ADDR_BANK_LEVEL] * maa->m_org[ADDR_ROW_LEVEL];
    total_num_RT_subslices = maa->m_org[ADDR_CHANNEL_LEVEL] * maa->m_org[ADDR_RANK_LEVEL] *
                             maa->m_org[ADDR_BANKGROUP_LEVEL] * maa->m_org[ADDR_BANK_LEVEL];
    num_RT_configs = log2((double)total_num_RT_subslices / (double)min_num_RT_slices) + 1;

    RT_config_addr = new Addr[num_RT_config_cache_entries];
    RT_config_cache = new int[num_RT_config_cache_entries];
    RT_config_cache_tick = new Tick[num_RT_config_cache_entries];
    for (int i = 0; i < num_RT_config_cache_entries; i++) {
        RT_config_addr[i] = 0;
        RT_config_cache[i] = -1;
        RT_config_cache_tick[i] = 0;
    }

    RT = new RowTableSlice *[num_RT_configs]();
    my_RT_req_sent = new bool *[num_RT_configs]();
    my_RT_slice_order = new std::vector<int>[num_RT_configs];
    RT_slice_org = new int *[num_RT_configs];
    num_RT_slices = new int[num_RT_configs];
    num_RT_rows_total = new int[num_RT_configs];
    num_RT_subslices = new int[num_RT_configs];
    num_RT_slice_columns = new int[num_RT_configs];
    num_RT_possible_grows = new Addr[num_RT_configs];

    int current_num_RT_slices = min_num_RT_slices;
    int current_num_RT_rows_total = current_num_RT_slices * num_RT_rows_per_slice;
    Addr current_num_RT_possible_grows = max_num_RT_possible_grows;
    int current_num_RT_subslices = total_num_RT_subslices / min_num_RT_slices;
    int current_num_RT_entries_per_row = num_RT_entries_per_subslice_row * current_num_RT_subslices;
    initial_RT_config = -1;
    for (int i = 0; i < num_RT_configs; i++) {
        num_RT_slices[i] = current_num_RT_slices;
        num_RT_rows_total[i] = current_num_RT_rows_total;
        num_RT_subslices[i] = current_num_RT_subslices;
        num_RT_slice_columns[i] = current_num_RT_entries_per_row;
        num_RT_possible_grows[i] = current_num_RT_possible_grows;
        const bool configured_row_table = reconfigure_RT
            ? i == num_RT_configs - 1
            : current_num_RT_slices == num_initial_RT_slices;
        if (configured_row_table) {
            initial_RT_config = i;
        }
        panic_if(current_num_RT_entries_per_row <= 0, "I[%d] TC[%d] %s: current_num_RT_entries_per_row is %d!\n",
                 my_indirect_id, i, __func__, current_num_RT_entries_per_row);
        const bool allocate_row_table =
            !maa->virtual_index_range_passes || configured_row_table;
        if (allocate_row_table) {
            RT[i] = new RowTableSlice[current_num_RT_slices];
            my_RT_req_sent[i] = new bool[current_num_RT_slices];
            for (int j = 0; j < current_num_RT_slices; j++) {
                RT[i][j].allocate(my_indirect_id, j,
                                  num_RT_rows_per_slice,
                                  current_num_RT_entries_per_row,
                                  offset_table, maa, false);
                my_RT_req_sent[i][j] = false;
            }
        }

        // How many banks corresponding to which level exist in
        // this configuration (RowTableSlice Bank Organization)
        RT_slice_org[i] = new int[ADDR_MAX_LEVEL];
        int remaining_banks = current_num_RT_slices;
        for (int k = 0; k < ADDR_MAX_LEVEL; k++) {
            if (remaining_banks > maa->m_org[k]) {
                RT_slice_org[i][k] = maa->m_org[k];
                assert(remaining_banks % maa->m_org[k] == 0);
                remaining_banks /= maa->m_org[k];
            } else if (remaining_banks > 0) {
                RT_slice_org[i][k] = remaining_banks;
                remaining_banks = 0;
            } else {
                RT_slice_org[i][k] = 1;
            }
        }
        DPRINTF(MAAIndirect, "I[%d] TC[%d]: %d banks x %d subslices x %d rows x %d columns -- CH: %d, RA: %d, BG: %d, BA: %d, RO: %d, CO: %d\n",
                my_indirect_id, i, num_RT_slices[i], num_RT_subslices[i], num_RT_rows_per_slice, num_RT_slice_columns[i],
                RT_slice_org[i][ADDR_CHANNEL_LEVEL], RT_slice_org[i][ADDR_RANK_LEVEL],
                RT_slice_org[i][ADDR_BANKGROUP_LEVEL], RT_slice_org[i][ADDR_BANK_LEVEL],
                RT_slice_org[i][ADDR_ROW_LEVEL], RT_slice_org[i][ADDR_COLUMN_LEVEL]);

        my_RT_slice_order[i].clear();
        for (int bank = 0; bank < maa->m_org[ADDR_BANK_LEVEL]; bank++) {
            for (int bankgroup = 0; bankgroup < maa->m_org[ADDR_BANKGROUP_LEVEL]; bankgroup++) {
                for (int rank = 0; rank < maa->m_org[ADDR_RANK_LEVEL]; rank++) {
                    for (int channel = 0; channel < maa->m_org[ADDR_CHANNEL_LEVEL]; channel++) {
                        int RT_index = getRowTableIdx(i, channel, rank, bankgroup, bank);
                        if (std::find(my_RT_slice_order[i].begin(),
                                      my_RT_slice_order[i].end(),
                                      RT_index) == my_RT_slice_order[i].end()) {
                            my_RT_slice_order[i].push_back(RT_index);
                        }
                    }
                }
            }
        }
        panic_if(my_RT_slice_order[i].size() != num_RT_slices[i],
                 "I[%d] TC[%d] %s: my_RT_slice_order(%d) != num_RT_slices(%d)!\n",
                 my_indirect_id, i, __func__, my_RT_slice_order[i].size(), num_RT_slices[i]);
        current_num_RT_slices *= 2;
        current_num_RT_rows_total *= 2;
        current_num_RT_subslices /= 2;
        current_num_RT_entries_per_row /= 2;
        current_num_RT_possible_grows /= 2;
    }
    panic_if(initial_RT_config == -1,
             "I[%d] unsupported initial Row-Table slice count %d\n",
             my_indirect_id, num_initial_RT_slices);
    DPRINTF(MAAIndirect, "I[%d] %s: initial_RT_config(%d)!\n",
            my_indirect_id, __func__, initial_RT_config);
    if (maa->virtual_index_range_passes) {
        uint32_t allocated_row_configs = 0;
        for (int config = 0; config < num_RT_configs; ++config)
            allocated_row_configs += RT[config] != nullptr;
        const uint64_t active_row_line_slots =
            static_cast<uint64_t>(num_RT_slices[initial_RT_config]) *
            num_RT_rows_per_slice *
            num_RT_slice_columns[initial_RT_config];
        panic_if(offset_table->capacity() >
                     BoundedRangePassTracker::MaxActiveEntries,
                 "I[%d] bounded range OffsetTable has %d entries (max %u)\n",
                 my_indirect_id, offset_table->capacity(),
                 BoundedRangePassTracker::MaxActiveEntries);
        panic_if(active_row_line_slots >
                     BoundedRangePassTracker::MaxActiveEntries,
                 "I[%d] bounded range RowTable has %lu active line slots "
                 "(max %u)\n", my_indirect_id, active_row_line_slots,
                 BoundedRangePassTracker::MaxActiveEntries);
        panic_if(allocated_row_configs != 1,
                 "I[%d] bounded range allocated %u Row Table configs "
                 "(expected exactly one)\n",
                 my_indirect_id, allocated_row_configs);
    }
}
int IndirectAccessUnit::getRowTableIdx(int RT_config, int channel, int rank, int bankgroup, int bank) {
    int RT_index = 0;
    RT_index += (channel % RT_slice_org[RT_config][ADDR_CHANNEL_LEVEL]);
    RT_index *= (RT_slice_org[RT_config][ADDR_RANK_LEVEL]);
    RT_index += (rank % RT_slice_org[RT_config][ADDR_RANK_LEVEL]);
    RT_index *= (RT_slice_org[RT_config][ADDR_BANKGROUP_LEVEL]);
    RT_index += (bankgroup % RT_slice_org[RT_config][ADDR_BANKGROUP_LEVEL]);
    RT_index *= (RT_slice_org[RT_config][ADDR_BANK_LEVEL]);
    RT_index += (bank % RT_slice_org[RT_config][ADDR_BANK_LEVEL]);
    panic_if(RT_index >= num_RT_slices[RT_config],
             "I[%d] TC[%d] %s: RT_index(%d) >= num_RT_slices(%d)!\n",
             my_indirect_id, RT_config, __func__, RT_index, num_RT_slices[RT_config]);
    return RT_index;
}
Addr IndirectAccessUnit::getGrowAddr(int RT_config, int bankgroup, int bank, int row) {
    Addr grow_addr = 0;
    grow_addr = (bankgroup / RT_slice_org[RT_config][ADDR_BANKGROUP_LEVEL]);
    grow_addr *= maa->m_org[ADDR_BANK_LEVEL];
    grow_addr += (bank / RT_slice_org[RT_config][ADDR_BANK_LEVEL]);
    grow_addr *= maa->m_org[ADDR_ROW_LEVEL];
    grow_addr += (row / RT_slice_org[RT_config][ADDR_ROW_LEVEL]);
    assert(RT_slice_org[RT_config][ADDR_ROW_LEVEL] == 1);
    panic_if(grow_addr >= num_RT_possible_grows[RT_config],
             "I[%d] TC[%d] %s: grow_addr(%lu) >= num_RT_possible_grows(%lu)!\n",
             my_indirect_id, RT_config, __func__, grow_addr, num_RT_possible_grows[RT_config]);
    return grow_addr;
}
int IndirectAccessUnit::getRowTableConfig(Addr addr) {
    if (reconfigure_RT == false)
        return initial_RT_config;

    int oldest_entry = -1;
    Tick oldest_tick = 0;
    Tick current_tick = curTick();
    for (int i = 0; i < num_RT_config_cache_entries; i++) {
        if (RT_config_addr[i] == addr) {
            RT_config_cache_tick[i] = current_tick;
            return RT_config_cache[i];
        } else if (RT_config_cache_tick[i] <= oldest_tick) {
            oldest_tick = RT_config_cache_tick[i];
            oldest_entry = i;
        }
    }
    assert(oldest_entry != -1);
    RT_config_addr[oldest_entry] = addr;
    RT_config_cache[oldest_entry] = initial_RT_config;
    RT_config_cache_tick[oldest_entry] = current_tick;
    return initial_RT_config;
}
void IndirectAccessUnit::setRowTableConfig(Addr addr, int num_CLs, int num_ROWs) {
    if (reconfigure_RT == false)
        return;

    // This approach selects the configuration with as many ROWs as needed
    int new_config = -1;
    if (num_ROWs >= num_RT_rows_total[num_RT_configs - 1]) {
        new_config = num_RT_configs - 1;
    } else {
        for (int i = 0; i < num_RT_configs; i++) {
            if (num_ROWs < num_RT_rows_total[i]) {
                new_config = i;
                break;
            }
        }
    }

#if 0
    // This approach selects the configuration with as many ROWs as needed
    int new_config = -1;
    if (num_ROWs >= num_RT_rows_total[num_RT_configs - 1]) {
        new_config = num_RT_configs - 1;
    } else {
        for (int i = 0; i < num_RT_configs - 1; i++) {
            if (num_ROWs < ((num_RT_rows_total[i] + num_RT_rows_total[i + 1]) / 2)) {
                new_config = i;
                break;
            }
        }
    }
#endif

#if 0
    // This approach does not work. If D is too large, there will be many unique CLs
    // and many unique ROWs. The CL/ROW is still large, but since num_ROWs >> number
    // of RT banks x RT subslices x rows/subslice, there will be a lot of drains.
    int num_CLs_per_ROW = num_CLs / num_ROWs;
    if (num_CLs_per_ROW >= num_RT_slice_columns[0]) {
        new_config = 0;
    } else if (num_CLs_per_ROW < num_RT_slice_columns[num_RT_configs - 1]) {
        new_config = num_RT_configs - 1;
    } else {
        for (int i = 1; i < num_RT_configs; i++) {
            if (num_CLs_per_ROW < num_RT_slice_columns[i - 1] &&
                num_CLs_per_ROW >= num_RT_slice_columns[i]) {
                new_config = i;
            }
        }
    }
#endif

    assert(new_config != -1);
    for (int i = 0; i < num_RT_config_cache_entries; i++) {
        if (RT_config_addr[i] == addr) {
            RT_config_cache[i] = new_config;
            DPRINTF(MAATrace, "I[%d] %s: addr(0x%lx) set to config(%d) with (%d/%d) CLs, (%d/%d) ROWs, (%d/%d) CLs/ROW!\n",
                    my_indirect_id, __func__, addr, new_config,
                    num_CLs, num_RT_slice_columns[new_config] * num_RT_slices[new_config] * num_RT_rows_per_slice,
                    num_ROWs, num_RT_rows_total[new_config],
                    num_CLs / num_ROWs, num_RT_slice_columns[new_config]);
            return;
        }
    }
    panic_if(true, "I[%d] %s: addr(0x%lx) not found in the cache!\n", my_indirect_id, __func__, addr);
}
void
IndirectAccessUnit::recordReorderSurvivalIssue(Addr addr)
{
    if (!debug::MAAReorderTrace)
        return;
    const std::vector<int> addr_vec = maa->map_addr(addr);
    const int rt_idx = getRowTableIdx(
        my_RT_config, addr_vec[ADDR_CHANNEL_LEVEL],
        addr_vec[ADDR_RANK_LEVEL], addr_vec[ADDR_BANKGROUP_LEVEL],
        addr_vec[ADDR_BANK_LEVEL]);
    const Addr grow = getGrowAddr(
        my_RT_config, addr_vec[ADDR_BANKGROUP_LEVEL],
        addr_vec[ADDR_BANK_LEVEL], addr_vec[ADDR_ROW_LEVEL]);
    const uint64_t row_key = grow +
        static_cast<uint64_t>(rt_idx) *
            static_cast<uint64_t>(num_RT_possible_grows[my_RT_config]);
    panic_if(!reorder_survival.issueLine(row_key),
             "I[%d] could not record reorder-survival source issue\n",
             my_indirect_id);
}

void
IndirectAccessUnit::recordReorderSurvivalIssuedEntries(uint64_t entries)
{
    if (!debug::MAAReorderTrace)
        return;
    panic_if(!reorder_survival.issueEntries(entries),
             "I[%d] could not record %lu reorder-survival issued entries\n",
             my_indirect_id, entries);
}

void
IndirectAccessUnit::recordReorderSurvivalDrain(
    ReorderSurvivalTracker::DrainReason reason)
{
    if (!debug::MAAReorderTrace)
        return;
    panic_if(!reorder_survival.markDrain(reason),
             "I[%d] could not record reorder-survival drain\n",
             my_indirect_id);
}

void
IndirectAccessUnit::closeReorderSurvivalEpoch(bool final)
{
    if (!debug::MAAReorderTrace)
        return;
    ReorderSurvivalTracker::Epoch epoch;
    panic_if(!reorder_survival.closeEpoch(final, epoch),
             "I[%d] could not close reorder-survival epoch (final=%d)\n",
             my_indirect_id, final);
    DPRINTF(MAAReorderTrace,
            "schema=dx100.reorder_epoch.v1 event=reorder_epoch unit=%d "
            "instruction_id=%lu operation_tick=%lu pc=0x%lx cid=%d "
            "if_id=%d opcode=%d epoch_id=%lu admissions=%lu "
            "issued_lines=%lu issued_entries=%lu "
            "max_joint_admissions=%lu row_transitions=%lu "
            "rt_full_drains=%lu offset_drains=%lu "
            "partition_drains=%lu final=%d\n",
            my_indirect_id, reorder_survival.instructionId,
            my_decode_start_tick, my_instruction->PC, my_instruction->CID,
            my_instruction->if_id,
            static_cast<int>(my_instruction->opcode), epoch.id,
            epoch.admissions, epoch.issuedLines, epoch.issuedEntries,
            epoch.maxJointAdmissions, epoch.rowTransitions,
            epoch.rtFullDrains, epoch.offsetDrains,
            epoch.partitionDrains, epoch.final);
}

void
IndirectAccessUnit::finishReorderSurvival()
{
    if (!debug::MAAReorderTrace)
        return;
    closeReorderSurvivalEpoch(true);
    panic_if(reorder_survival.totalAdmissions !=
                 attribution_row_insert_successes,
             "I[%d] reorder-survival admissions %lu != row successes %lu\n",
             my_indirect_id, reorder_survival.totalAdmissions,
             attribution_row_insert_successes);
    panic_if(reorder_survival.totalSelectedDescriptors !=
                 reorder_survival.totalAdmissions,
             "I[%d] reorder-survival selected/admitted descriptors do not "
             "reconcile: %lu/%lu\n",
             my_indirect_id, reorder_survival.totalSelectedDescriptors,
             reorder_survival.totalAdmissions);
    panic_if(reorder_survival.totalRTFullDrains !=
                 attribution_row_pressure_events,
             "I[%d] reorder-survival RT-full drains %lu != pressure events "
             "%lu\n",
             my_indirect_id, reorder_survival.totalRTFullDrains,
             attribution_row_pressure_events);
    panic_if(reorder_survival.totalIssuedLines != source_issue_sequence,
             "I[%d] reorder-survival lines %lu != source issues %lu\n",
             my_indirect_id, reorder_survival.totalIssuedLines,
             source_issue_sequence);
    panic_if(!reorder_survival.reconciled(),
             "I[%d] reorder-survival admitted/issued entries do not "
             "reconcile: %lu/%lu\n",
             my_indirect_id, reorder_survival.totalAdmissions,
             reorder_survival.totalIssuedEntries);
    const bool predicate_present = my_cond_tile != -1 ||
        (isSoaJitRmw() && my_predicate_addr != 0);
    const char *classification =
        reorder_survival.preserves16K(predicate_present)
        ? "preserved"
        : "inherited/partitioned";
    DPRINTF(MAAReorderTrace,
            "schema=dx100.reorder_summary.v1 event=reorder_summary unit=%d "
            "instruction_id=%lu operation_tick=%lu pc=0x%lx cid=%d "
            "if_id=%d opcode=%d predicate_present=%d "
            "selected_descriptors=%lu epochs=%lu "
            "total_admitted=%lu max_joint_admissions=%lu "
            "rt_full_drains=%lu offset_drains=%lu "
            "partition_drains=%lu mid_instruction_drains=%lu "
            "total_issued_lines=%lu total_issued_entries=%lu "
            "row_transitions=%lu reconciled=1 classification=%s\n",
            my_indirect_id, reorder_survival.instructionId,
            my_decode_start_tick, my_instruction->PC, my_instruction->CID,
            my_instruction->if_id,
            static_cast<int>(my_instruction->opcode),
            predicate_present, reorder_survival.totalSelectedDescriptors,
            reorder_survival.epochs,
            reorder_survival.totalAdmissions,
            reorder_survival.maxJointAdmissions,
            reorder_survival.totalRTFullDrains,
            reorder_survival.totalOffsetDrains,
            reorder_survival.totalPartitionDrains,
            reorder_survival.midInstructionDrains(),
            reorder_survival.totalIssuedLines,
            reorder_survival.totalIssuedEntries,
            reorder_survival.totalRowTransitions, classification);
}

void IndirectAccessUnit::check_reset() {
    for (int i = 0; i < num_RT_configs; i++) {
        if (RT[i] == nullptr)
            continue;
        for (int j = 0; j < num_RT_slices[i]; j++) {
            RT[i][j].check_reset();
        }
    }
    offset_table->check_reset();
    panic_if(virtual_reserved_responses != 0 || virtual_outstanding_writes != 0,
             "I[%d] virtual retirement state is not empty: slots=%d writes=%d\n",
             my_indirect_id, virtual_reserved_responses,
             virtual_outstanding_writes);
    panic_if(virtual_reserved_response_words != 0 || virtual_pending_source ||
                 !virtual_source_reservations.empty(),
             "I[%d] packed source reservation state is not empty\n",
             my_indirect_id);
    panic_if(virtual_native_slice_cursor != 0,
             "I[%d] native slice cursor is not reset: %d\n",
             my_indirect_id, virtual_native_slice_cursor);
    panic_if(!virtual_outstanding_write_lines.empty(),
             "I[%d] virtual write-line scoreboard is not empty\n",
             my_indirect_id);
    panic_if(!virtualCombinerEmpty(),
             "I[%d] virtual combiner is not empty at reset\n", my_indirect_id);
    panic_if(virtual_combine_words != 0,
             "I[%d] virtual combiner still accounts for %d words\n",
             my_indirect_id, virtual_combine_words);
    panic_if(!virtual_combine_payload.empty(),
             "I[%d] virtual combiner payload pool still owns %zu words\n",
             my_indirect_id, virtual_combine_payload.used());
    panic_if(!maa->allIndirectPacketsSent(my_indirect_id),
             "All indirect packets are not sent!\n");
    panic_if(my_decode_start_tick != 0,
             "Decode start tick is not 0: %lu!\n", my_decode_start_tick);
    panic_if(my_fill_start_tick != 0,
             "Fill start tick is not 0: %lu!\n", my_fill_start_tick);
    panic_if(my_build_start_tick != 0,
             "Build start tick is not 0: %lu!\n", my_build_start_tick);
    panic_if(my_request_start_tick != 0,
             "Request start tick is not 0: %lu!\n", my_request_start_tick);
    panic_if(virtual_request_reason != VirtualRequestReason::None ||
                 virtual_request_reason_tick != 0 ||
                 virtual_request_attributed_ticks != 0 ||
                 std::any_of(virtual_request_reason_ticks.begin(),
                             virtual_request_reason_ticks.end(),
                             [](Tick ticks) { return ticks != 0; }),
             "I[%d] virtual request attribution is still active\n",
             my_indirect_id);
    panic_if(virtual_pipeline_state != 0 || virtual_pipeline_tick != 0 ||
                 virtual_pipeline_attributed_ticks != 0 ||
                 std::any_of(virtual_pipeline_ticks.begin(),
                             virtual_pipeline_ticks.end(),
                             [](Tick ticks) { return ticks != 0; }),
             "I[%d] virtual pipeline attribution is still active\n",
             my_indirect_id);
    panic_if(attribution_stage != AttributionStage::None ||
                 attribution_stage_tick != 0 ||
                 std::any_of(attribution_stage_ticks.begin(),
                             attribution_stage_ticks.end(),
                             [](Tick ticks) { return ticks != 0; }),
             "I[%d] stage attribution is still active\n", my_indirect_id);
    panic_if(!direct_index_pending_lines.empty() ||
                 std::any_of(descriptor_spool_read_slots.begin(),
                             descriptor_spool_read_slots.end(),
                             [](const auto &slot) {
                                 return slot.valid || slot.demand_observed;
                             }) ||
                 std::any_of(descriptor_spool_write_slots.begin(),
                             descriptor_spool_write_slots.end(),
                             [](const auto &slot) { return slot.valid; }) ||
                 descriptor_spool_current_valid ||
                 !direct_index_ready_lines.empty() ||
                 !direct_index_words.empty(),
             "I[%d] direct-index buffer is not empty at reset\n",
             my_indirect_id);
    panic_if(!soaPredicateLinesEmpty(),
             "I[%d] SoA/JIT predicate feeder is not empty at reset\n",
             my_indirect_id);
    panic_if(!soaJitContextsEmpty(),
             "I[%d] SoA/JIT A-line scoreboard is not empty at reset\n",
             my_indirect_id);
    panic_if(soa_jit_old_result_buffer.activeRun() ||
                 !soa_jit_old_result_buffer.empty(),
             "I[%d] SoA/JIT old-result publisher is not empty at reset\n",
             my_indirect_id);
    panic_if(descriptor_spool_bucket_active ||
                 descriptor_spool_bucket_scan_complete ||
                 descriptor_spool_replay_active ||
                 descriptor_spool_read_ahead_active ||
                 descriptor_spool_prefetch_occupancy != 0 ||
                 descriptor_spool_prefetch_occupancy_tick != 0 ||
                 descriptor_spool_demand_wait_active ||
                 descriptor_spool.configured(),
             "I[%d] descriptor-spool lifecycle is not reset\n",
             my_indirect_id);
    panic_if(
        bounded_global_merge.configured() ||
            bounded_global_merge_phase != BoundedGlobalMergePhase::None ||
            bounded_global_merge_chain_head != -1 ||
            bounded_global_merge_batch_inflight ||
            bounded_global_merge_source_pending ||
            bounded_global_merge_source_ready ||
            bounded_global_merge_source_head != -1 ||
            bounded_global_merge_source_tail != -1 ||
            bounded_global_merge_source_words != 0 ||
            std::any_of(bounded_global_merge_read_slots.begin(),
                        bounded_global_merge_read_slots.end(),
                        [](const auto &slot) { return slot.valid; }) ||
            std::any_of(bounded_global_merge_write_slots.begin(),
                        bounded_global_merge_write_slots.end(),
                        [](const auto &slot) { return slot.valid; }),
        "I[%d] bounded-global-merge lifecycle is not reset\n",
        my_indirect_id);
}
Cycles IndirectAccessUnit::updateLatency(int num_spd_read_data_accesses, int num_spd_read_condidx_accesses, int num_spd_write_accesses, int num_rowtable_read_accesses, int num_rowtable_write_accesses, int RT_access_parallelism) {
    if (num_spd_read_data_accesses != 0) {
        // XByte -- 64/X bytes per SPD access
        Cycles get_data_latency = maa->spd->getDataLatency(getCeiling(num_spd_read_data_accesses, my_words_per_cl));
        my_SPD_read_finish_tick = maa->getClockEdge(get_data_latency);
        if (num_spd_read_condidx_accesses == 0) {
            (*maa->stats.IND_CyclesSPDReadAccess[my_indirect_id]) += get_data_latency;
        }
    }
    if (num_spd_read_condidx_accesses != 0) {
        // 4Byte conditions and indices -- 16 bytes per SPD access
        Cycles get_data_latency = maa->spd->getDataLatency(getCeiling(num_spd_read_condidx_accesses, 16));
        my_SPD_read_finish_tick = maa->getClockEdge(get_data_latency);
        (*maa->stats.IND_CyclesSPDReadAccess[my_indirect_id]) += get_data_latency;
    }
    if (num_spd_write_accesses != 0) {
        // XByte -- 64/X bytes per SPD access
        Cycles set_data_latency = maa->spd->setDataLatency(my_dst_tile, getCeiling(num_spd_write_accesses, my_words_per_cl));
        my_SPD_write_finish_tick = maa->getClockEdge(set_data_latency);
        (*maa->stats.IND_CyclesSPDWriteAccess[my_indirect_id]) += set_data_latency;
    }
    if (num_rowtable_read_accesses != 0) {
        num_rowtable_read_accesses = getCeiling(num_rowtable_read_accesses, RT_access_parallelism);
        Cycles read_access_rowtable_latency = Cycles(num_rowtable_read_accesses * rowtable_latency);
        if (my_RT_read_access_finish_tick < curTick())
            my_RT_read_access_finish_tick = maa->getClockEdge(read_access_rowtable_latency);
        else
            my_RT_read_access_finish_tick += maa->getCyclesToTicks(read_access_rowtable_latency);
        (*maa->stats.IND_CyclesRTAccess[my_indirect_id]) += read_access_rowtable_latency;
    }
    if (num_rowtable_write_accesses != 0) {
        num_rowtable_write_accesses = getCeiling(num_rowtable_write_accesses, RT_access_parallelism);
        Cycles write_access_rowtable_latency = Cycles(num_rowtable_write_accesses * rowtable_latency);
        if (my_RT_write_access_finish_tick < curTick())
            my_RT_write_access_finish_tick = maa->getClockEdge(write_access_rowtable_latency);
        else
            my_RT_write_access_finish_tick += maa->getCyclesToTicks(write_access_rowtable_latency);
        (*maa->stats.IND_CyclesRTAccess[my_indirect_id]) += write_access_rowtable_latency;
    }
    Tick finish_tick = std::max(std::max(std::max(my_SPD_read_finish_tick, my_SPD_write_finish_tick), my_RT_read_access_finish_tick), my_RT_write_access_finish_tick);
    return maa->getTicksToCycles(finish_tick - curTick());
}
bool IndirectAccessUnit::scheduleNextExecution(bool force) {
    Tick other_finish_tick = my_RT_write_access_finish_tick;
    if (state == Status::Response ||
        (state == Status::Request && usesBoundedSourceResponses() &&
         !isVirtualLoad())) {
        other_finish_tick =
            std::max(std::max(my_SPD_read_finish_tick,
                              my_SPD_write_finish_tick),
                     std::max(my_RT_read_access_finish_tick,
                              my_RT_write_access_finish_tick));
    }
    const Tick finish_tick =
        std::max(other_finish_tick, my_direct_index_filter_finish_tick);
    if (curTick() < finish_tick) {
        const Tick exposed_start =
            std::max(std::max(curTick(), other_finish_tick),
                     my_direct_index_filter_accounted_tick);
        if (my_direct_index_filter_finish_tick > exposed_start) {
            const Cycles exposed = maa->getTicksToCycles(
                my_direct_index_filter_finish_tick - exposed_start);
            (*maa->stats
                  .IND_VirtIndexFilterWaitEvents[my_indirect_id])++;
            (*maa->stats
                  .IND_VirtIndexFilterWaitCycles[my_indirect_id]) += exposed;
            my_direct_index_filter_accounted_tick =
                my_direct_index_filter_finish_tick;
            DPRINTF(MAAVirtualTrace,
                    "event=index_filter_wait unit=%d cycles=%lu until=%lu\n",
                    my_indirect_id, static_cast<uint64_t>(exposed),
                    my_direct_index_filter_finish_tick);
        }
        scheduleExecuteInstructionEvent(
            maa->getTicksToCycles(finish_tick - curTick()));
        return true;
    } else if (force) {
        scheduleExecuteInstructionEvent(Cycles(0));
        return true;
    }
    return false;
}
bool IndirectAccessUnit::isVirtualLoad() const {
    return my_instruction != nullptr &&
           (my_instruction->opcode ==
                Instruction::OpcodeType::INDIR_LD_VIRTUAL ||
            my_instruction->opcode ==
                Instruction::OpcodeType::INDIR_LD_VIRTUAL_INDEX);
}
bool IndirectAccessUnit::isDirectIndexLoad() const {
    return my_instruction != nullptr &&
           (my_instruction->opcode ==
                Instruction::OpcodeType::INDIR_LD_VIRTUAL_INDEX ||
            my_instruction->opcode ==
                Instruction::OpcodeType::INDIR_LD_INDEX ||
            isSoaJitRmw());
}
bool IndirectAccessUnit::isSoaJitRmw() const {
    return my_instruction != nullptr && my_instruction->isSoaJitRmw();
}
bool IndirectAccessUnit::isSoaJitScalarRmw() const {
    return my_instruction != nullptr &&
           my_instruction->isSoaJitScalarRmw();
}
bool IndirectAccessUnit::isSoaJitMaskedIndexRmw() const {
    return my_instruction != nullptr &&
           my_instruction->isSoaJitMaskedIndexRmw();
}
bool IndirectAccessUnit::isSoaJitOldResultRmw() const {
    return my_instruction != nullptr &&
           my_instruction->hasSoaJitOldResult();
}
bool IndirectAccessUnit::usesBoundedDirectIndexPasses() const {
    return isDirectIndexLoad() && !isSoaJitRmw() &&
           maa->virtual_index_range_passes;
}
bool IndirectAccessUnit::usesBoundedSourceResponses() const {
    return isVirtualLoad() ||
           (my_instruction != nullptr &&
            my_instruction->opcode ==
                Instruction::OpcodeType::INDIR_LD_INDEX);
}
void IndirectAccessUnit::accountReadResponse(Addr addr,
                                             bool is_block_cached) {
    if (is_block_cached) {
        auto responding = LoadsCacheHitRespondingTimeHistory.find(addr);
        auto accessing = LoadsCacheHitAccessingTimeHistory.find(addr);
        if (responding != LoadsCacheHitRespondingTimeHistory.end()) {
            (*maa->stats.IND_LoadsCacheHitRespondingLatency[my_indirect_id]) +=
                maa->getTicksToCycles(curTick() - responding->second);
            LoadsCacheHitRespondingTimeHistory.erase(responding);
        } else if (accessing != LoadsCacheHitAccessingTimeHistory.end()) {
            (*maa->stats.IND_LoadsCacheHitAccessingLatency[my_indirect_id]) +=
                maa->getTicksToCycles(curTick() - accessing->second);
            LoadsCacheHitAccessingTimeHistory.erase(accessing);
        } else {
            panic("I[%d] %s: addr(0x%lx) is not in the cache hit history!\n",
                  my_indirect_id, __func__, addr);
        }
    } else {
        auto accessing = LoadsMemAccessingTimeHistory.find(addr);
        panic_if(accessing == LoadsMemAccessingTimeHistory.end(),
                 "I[%d] %s: addr(0x%lx) is not in the memory history!\n",
                 my_indirect_id, __func__, addr);
        (*maa->stats.IND_LoadsMemAccessingLatency[my_indirect_id]) +=
            maa->getTicksToCycles(curTick() - accessing->second);
        LoadsMemAccessingTimeHistory.erase(accessing);
    }
}
void IndirectAccessUnit::fillDirectIndexWindow() {
    if (!isDirectIndexLoad())
        return;
    if (descriptor_spool_replay_active) {
        fillDescriptorSpoolWindow();
        return;
    }
    // The B-stream feeder and descriptor replay use separate state.  Keep
    // the configured B feeder depth even when descriptor spooling is active;
    // descriptor replay remains independently bounded by its read credits.
    const size_t line_capacity =
        static_cast<size_t>(direct_index_buffer_lines);
    panic_if(line_capacity == 0,
             "I[%d] direct-index feeder has zero line capacity\n",
             my_indirect_id);
    while (direct_index_pending_lines.size() +
               direct_index_ready_lines.size() < line_capacity) {
        const int itr = direct_index_next_prefetch_itr;
        if (itr >= my_max)
            return;
        const int64_t source_index =
            static_cast<int64_t>(my_index_min) +
            static_cast<int64_t>(itr) * my_index_stride;
        panic_if(source_index < 0,
                 "I[%d] negative streamed-index position %ld for itr %d\n",
                 my_indirect_id, source_index, itr);
        const uint64_t byte_offset =
            static_cast<uint64_t>(source_index) * sizeof(uint32_t);
        const Addr index_bytes = my_index_max_addr - my_index_addr;
        panic_if(my_index_addr < my_index_min_addr ||
                     my_index_addr >= my_index_max_addr ||
                     index_bytes < sizeof(uint32_t) ||
                     byte_offset > index_bytes - sizeof(uint32_t),
                 "I[%d] streamed-index position %ld exceeds "
                 "[0x%lx, 0x%lx)\n",
                 my_indirect_id, source_index, my_index_min_addr,
                 my_index_max_addr);
        const Addr first_vaddr = my_index_addr + byte_offset;
        const Addr block_vaddr = addrBlockAligner(first_vaddr, block_size);
        const Addr block_paddr =
            addrBlockAligner(translatePacket(block_vaddr), block_size);
        const bool has_outstanding = maa->hasOutstandingPacket(block_paddr);
        const bool merge_outstanding =
            has_outstanding && maa->canCoalesceOutstandingRead(
                                   block_paddr, FuncUnitType::INDIRECT,
                                   my_indirect_id);
        if (has_outstanding && !merge_outstanding) {
            if (isVirtualLoad())
                macro_b_retries++;
            (*maa->stats.IND_VirtIndexOutstandingWaitCycles
                  [my_indirect_id])++;
            scheduleExecuteInstructionEvent(1);
            return;
        }

        std::vector<std::pair<int, uint16_t>> pending_words;
        int candidate = itr;
        for (; candidate < my_max; ++candidate) {
            const int64_t candidate_source =
                static_cast<int64_t>(my_index_min) +
                static_cast<int64_t>(candidate) * my_index_stride;
            if (candidate_source < 0)
                break;
            const uint64_t candidate_offset =
                static_cast<uint64_t>(candidate_source) * sizeof(uint32_t);
            if (index_bytes < sizeof(uint32_t) ||
                candidate_offset > index_bytes - sizeof(uint32_t))
                break;
            const Addr candidate_vaddr = my_index_addr + candidate_offset;
            if (addrBlockAligner(candidate_vaddr, block_size) != block_vaddr)
                break;
            pending_words.emplace_back(
                candidate,
                static_cast<uint16_t>((candidate_vaddr - block_vaddr) /
                                      sizeof(uint32_t)));
        }
        panic_if(pending_words.empty(),
                 "I[%d] direct-index request at itr %d captured no words\n",
                 my_indirect_id, itr);
        panic_if(direct_index_pending_lines.find(block_paddr) !=
                     direct_index_pending_lines.end() ||
                     direct_index_ready_lines.find(block_paddr) !=
                         direct_index_ready_lines.end(),
                 "I[%d] direct-index line 0x%lx is already buffered\n",
                 my_indirect_id, block_paddr);
        const int pending_word_count = pending_words.size();
        direct_index_pending_lines.emplace(
            block_paddr, std::move(pending_words));
        direct_index_next_prefetch_itr = candidate;
        direct_index_max_lines = std::max(
            direct_index_max_lines,
            static_cast<int>(direct_index_pending_lines.size() +
                             direct_index_ready_lines.size()));
        if (isVirtualLoad()) {
            macro_b_queue_high_water = std::max<uint64_t>(
                macro_b_queue_high_water,
                direct_index_pending_lines.size() +
                    direct_index_ready_lines.size());
        }
        DPRINTF(MAAVirtualTrace,
                "event=index_line_issue schema=2 unit=%d occurrence=%lu "
                "operation_tick=%lu line=0x%lx "
                "first_itr=%d words=%d merged=%d\n",
                my_indirect_id, attribution_event_occurrence++,
                my_decode_start_tick, block_paddr, itr,
                pending_word_count, merge_outstanding);
        if (merge_outstanding)
            (*maa->stats.IND_VirtIndexOutstandingMerges[my_indirect_id])++;
        createDirectIndexReadPacket(block_paddr, rowtable_latency);
    }
}
void IndirectAccessUnit::fillDescriptorSpoolWindow(bool read_ahead)
{
    panic_if(!descriptor_spool_replay_active,
             "I[%d] descriptor-spool replay is not active\n",
             my_indirect_id);
    panic_if(read_ahead != descriptor_spool_read_ahead_active,
             "I[%d] descriptor-spool window mode mismatch: request=%d "
             "active=%d\n", my_indirect_id, read_ahead,
             descriptor_spool_read_ahead_active);
    const uint32_t pass = direct_index_partition;
    while (descriptorSpoolReadSlotsUsed() <
           descriptor_spool.readCredits()) {
        const uint32_t line = direct_index_next_prefetch_itr;
        if (line >= descriptor_spool.passLines(pass))
            return;
        const Addr vaddr = descriptor_spool.lineAddress(pass, line);
        createDescriptorSpoolReadPacket(vaddr, pass, line, read_ahead);
        direct_index_next_prefetch_itr++;
    }
    if (!read_ahead && direct_index_next_prefetch_itr <
        static_cast<int>(descriptor_spool.passLines(pass)))
        (*maa->stats.IND_DescriptorSpoolReadCreditStalls[my_indirect_id])++;
}
void
IndirectAccessUnit::serviceDescriptorSpoolReadAhead()
{
    if (!descriptor_spool_read_ahead_active)
        return;
    panic_if(!maa->virtual_descriptor_spool_read_ahead ||
                 !direct_index_partition_barrier ||
                 !descriptor_spool_replay_active ||
                 descriptor_spool.activeReplayPass() !=
                     static_cast<uint32_t>(direct_index_partition),
             "I[%d] invalid descriptor read-ahead lifecycle\n",
             my_indirect_id);
    if (virtual_source_received >= virtual_source_expected)
        return;
    if (!descriptor_spool_overlap_opportunity_recorded) {
        descriptor_spool_overlap_opportunity_recorded = true;
        descriptor_spool_overlap_opportunities++;
        DPRINTF(MAAVirtualTrace,
                "event=descriptor_spool_overlap_opportunity schema=1 "
                "unit=%d operation_tick=%lu current_pass=%d next_pass=%d "
                "source_expected=%d source_received=%d slots=%u\n",
                my_indirect_id, my_decode_start_tick,
                direct_index_partition - 1, direct_index_partition,
                virtual_source_expected, virtual_source_received,
                descriptor_spool.readCredits());
    }
    fillDescriptorSpoolWindow(true);
}
void
IndirectAccessUnit::accountDescriptorSpoolPrefetchOccupancy()
{
    if (descriptor_spool_prefetch_occupancy_tick != 0) {
        panic_if(curTick() < descriptor_spool_prefetch_occupancy_tick,
                 "I[%d] descriptor read-ahead occupancy tick regressed\n",
                 my_indirect_id);
        descriptor_spool_prefetch_occupancy_line_ticks +=
            static_cast<uint64_t>(descriptor_spool_prefetch_occupancy) *
            (curTick() - descriptor_spool_prefetch_occupancy_tick);
    }
    descriptor_spool_prefetch_occupancy_tick = curTick();
}
void
IndirectAccessUnit::promoteDescriptorSpoolReadAhead(uint32_t pass)
{
    panic_if(!descriptor_spool_read_ahead_active ||
                 descriptor_spool.activeReplayPass() != pass ||
                 pass != static_cast<uint32_t>(direct_index_partition),
             "I[%d] cannot promote descriptor read-ahead pass %u\n",
             my_indirect_id, pass);
    accountDescriptorSpoolPrefetchOccupancy();
    uint32_t issued = 0;
    uint32_t ready = 0;
    for (auto &slot : descriptor_spool_read_slots) {
        if (!slot.valid || !slot.read_ahead)
            continue;
        panic_if(slot.pass != pass,
                 "I[%d] read-ahead slot pass %u survived promotion of %u\n",
                 my_indirect_id, slot.pass, pass);
        issued++;
        if (slot.responded)
            ready++;
    }
    panic_if(issued != descriptor_spool_prefetch_occupancy,
             "I[%d] descriptor read-ahead occupancy mismatch %u/%u\n",
             my_indirect_id, issued,
             descriptor_spool_prefetch_occupancy);
    descriptor_spool_prefetch_occupancy = 0;
    descriptor_spool_prefetch_occupancy_tick = 0;
    descriptor_spool_read_ahead_active = false;
    descriptor_spool_overlap_opportunity_recorded = false;
    DPRINTF(MAAVirtualTrace,
            "event=descriptor_spool_read_ahead_promote schema=1 unit=%d "
            "operation_tick=%lu pass=%u issued=%u ready=%u "
            "pending=%u\n",
            my_indirect_id, my_decode_start_tick, pass, issued, ready,
            issued - ready);
}
void
IndirectAccessUnit::markDescriptorSpoolLineUseful(
    DescriptorSpoolPendingLine &slot)
{
    if (!slot.read_ahead || slot.useful)
        return;
    slot.useful = true;
    descriptor_spool_useful_prefetched_lines++;
    if (slot.ready_before_demand)
        descriptor_spool_demand_waits_avoided++;
}
void
IndirectAccessUnit::startDescriptorSpoolDemandWait(uint32_t cursor)
{
    if (descriptor_spool_demand_wait_active) {
        panic_if(descriptor_spool_demand_wait_cursor != cursor,
                 "I[%d] descriptor demand wait cursor changed %u/%u\n",
                 my_indirect_id, descriptor_spool_demand_wait_cursor,
                 cursor);
        return;
    }
    descriptor_spool_demand_wait_active = true;
    descriptor_spool_demand_wait_boundary = cursor == 0;
    descriptor_spool_demand_wait_tick = curTick();
    descriptor_spool_demand_wait_cursor = cursor;
    if (descriptor_spool_demand_wait_boundary)
        descriptor_spool_boundary_demand_wait_events++;
    else
        descriptor_spool_within_pass_demand_wait_events++;
}
void
IndirectAccessUnit::finishDescriptorSpoolDemandWait(uint32_t cursor)
{
    if (!descriptor_spool_demand_wait_active)
        return;
    panic_if(descriptor_spool_demand_wait_cursor != cursor ||
                 curTick() < descriptor_spool_demand_wait_tick,
             "I[%d] invalid descriptor demand wait closure %u/%u\n",
             my_indirect_id, descriptor_spool_demand_wait_cursor, cursor);
    const Tick waited = curTick() - descriptor_spool_demand_wait_tick;
    if (descriptor_spool_demand_wait_boundary)
        descriptor_spool_boundary_demand_wait_ticks += waited;
    else
        descriptor_spool_within_pass_demand_wait_ticks += waited;
    DPRINTF(MAAVirtualTrace,
            "event=descriptor_spool_demand_wait schema=1 unit=%d "
            "operation_tick=%lu pass=%d cursor=%u boundary=%d "
            "sim_ticks=%lu cycles=%lu\n",
            my_indirect_id, my_decode_start_tick, direct_index_partition,
            cursor, descriptor_spool_demand_wait_boundary, waited,
            static_cast<uint64_t>(maa->getTicksToCycles(waited)));
    descriptor_spool_demand_wait_active = false;
    descriptor_spool_demand_wait_boundary = false;
    descriptor_spool_demand_wait_tick = 0;
    descriptor_spool_demand_wait_cursor = 0;
}
size_t
IndirectAccessUnit::descriptorSpoolReadSlotsUsed() const
{
    return std::count_if(
        descriptor_spool_read_slots.begin(),
        descriptor_spool_read_slots.end(),
        [](const auto &slot) { return slot.valid; });
}
size_t
IndirectAccessUnit::descriptorSpoolWriteSlotsUsed() const
{
    return std::count_if(
        descriptor_spool_write_slots.begin(),
        descriptor_spool_write_slots.end(),
        [](const auto &slot) { return slot.valid; });
}
bool
IndirectAccessUnit::loadDescriptorSpoolCurrent(uint32_t cursor)
{
    panic_if(!descriptor_spool_replay_active,
             "I[%d] cannot decode a descriptor outside replay\n",
             my_indirect_id);
    if (descriptor_spool_current_valid) {
        panic_if(descriptor_spool_current_cursor != cursor,
                 "I[%d] decoded descriptor cursor changed from %u to %u\n",
                 my_indirect_id, descriptor_spool_current_cursor, cursor);
        return true;
    }
    const uint32_t pass = direct_index_partition;
    if (cursor >= descriptor_spool.population(pass))
        return false;
    const uint64_t byte_offset =
        static_cast<uint64_t>(cursor) *
        BoundedDescriptorSpool::DescriptorBytes;
    const uint32_t first_line = byte_offset /
        BoundedDescriptorSpool::LineBytes;
    const uint32_t first_byte = byte_offset %
        BoundedDescriptorSpool::LineBytes;
    auto findLine = [this, pass](uint32_t line)
        -> DescriptorSpoolPendingLine * {
        for (auto &slot : descriptor_spool_read_slots) {
            if (slot.valid && slot.pass == pass && slot.line == line)
                return &slot;
        }
        return nullptr;
    };
    auto *first = findLine(first_line);
    std::array<DescriptorSpoolPendingLine *,
               BoundedDescriptorSpool::DescriptorBytes> sources{};
    std::array<uint8_t, BoundedDescriptorSpool::DescriptorBytes> packed{};
    bool all_ready = true;
    for (uint32_t byte = 0;
         byte < BoundedDescriptorSpool::DescriptorBytes; ++byte) {
        const uint32_t stream_byte = first_byte + byte;
        const uint32_t line = first_line +
            stream_byte / BoundedDescriptorSpool::LineBytes;
        auto *source = line == first_line ? first : findLine(line);
        panic_if(source == nullptr,
                 "I[%d] descriptor demand has no issued line: pass=%u "
                 "cursor=%u line=%u\n",
                 my_indirect_id, pass, cursor, line);
        source->demand_observed = true;
        if (!source->responded)
            all_ready = false;
        sources[byte] = source;
    }
    if (!all_ready) {
        startDescriptorSpoolDemandWait(cursor);
        return false;
    }
    finishDescriptorSpoolDemandWait(cursor);
    for (uint32_t byte = 0;
         byte < BoundedDescriptorSpool::DescriptorBytes; ++byte) {
        markDescriptorSpoolLineUseful(*sources[byte]);
        const uint64_t stream_byte = byte_offset + byte;
        packed[byte] = sources[byte]->data[
            stream_byte % BoundedDescriptorSpool::LineBytes];
    }
    descriptor_spool_current_descriptor =
        BoundedDescriptorSpool::unpack(packed.data());
    panic_if(descriptor_spool_current_descriptor.iteration >=
                 static_cast<uint32_t>(my_max),
             "I[%d] decoded descriptor cursor %u has iteration %u/%d\n",
             my_indirect_id, cursor,
             descriptor_spool_current_descriptor.iteration, my_max);
    descriptor_spool_current_cursor = cursor;
    descriptor_spool_current_word = DirectIndexWord{
        descriptor_spool_current_descriptor.value,
        first->paddr,
        descriptorIndexWordPaddr(
            descriptor_spool_current_descriptor.iteration),
        direct_index_phase,
        descriptor_spool_current_descriptor.iteration};
    descriptor_spool_current_valid = true;
    direct_index_max_words = std::max(direct_index_max_words, 1);
    return true;
}
void
IndirectAccessUnit::releaseDescriptorSpoolReadLines(uint32_t next_cursor)
{
    const uint32_t pass = direct_index_partition;
    const uint32_t first_needed =
        next_cursor >= descriptor_spool.population(pass)
        ? descriptor_spool.passLines(pass)
        : static_cast<uint32_t>(
              static_cast<uint64_t>(next_cursor) *
              BoundedDescriptorSpool::DescriptorBytes /
              BoundedDescriptorSpool::LineBytes);
    for (auto &slot : descriptor_spool_read_slots) {
        if (!slot.valid || slot.pass != pass || slot.line >= first_needed)
            continue;
        panic_if(!slot.responded,
                 "I[%d] releasing pending descriptor line %u\n",
                 my_indirect_id, slot.line);
        if (slot.read_ahead && !slot.useful)
            descriptor_spool_wasted_prefetched_lines++;
        slot = DescriptorSpoolPendingLine();
    }
}
bool IndirectAccessUnit::ensureDirectIndex(int itr) {
    if (!isDirectIndexLoad())
        return true;
    fillDirectIndexWindow();
    if (descriptor_spool_replay_active)
        return loadDescriptorSpoolCurrent(itr);
    return direct_index_words.find(itr) != direct_index_words.end();
}
int64_t IndirectAccessUnit::soaSourcePosition(int logical_itr) const
{
    panic_if(!isSoaJitRmw() || logical_itr < 0 || logical_itr >= my_max,
             "I[%d] invalid SoA/JIT logical iteration %d/%d\n",
             my_indirect_id, logical_itr, my_max);
    return static_cast<int64_t>(my_index_min) +
           static_cast<int64_t>(logical_itr) * my_index_stride;
}

void
IndirectAccessUnit::validateSoaJitAddressSpans()
{
    panic_if(!isSoaJitRmw(),
             "I[%d] SoA/JIT span validation used by another shape\n",
             my_indirect_id);
    const bool operands_aligned = isSoaJitScalarRmw()
        ? SoaJitSafety::scalarOperandsAligned(
              my_base_addr, my_index_addr, my_predicate_addr,
              my_word_size)
        : SoaJitSafety::typedOperandsAligned(
              my_base_addr, my_backing_addr, my_index_addr,
              my_predicate_addr, my_word_size);
    panic_if(!operands_aligned,
             "I[%d] misaligned typed SoA/JIT operand reached decode\n",
             my_indirect_id);
    if (my_max == 0)
        return;

    struct Span
    {
        const char *name;
        Addr begin;
        Addr end;
    };

    const int64_t last_source = soaSourcePosition(my_max - 1);
    panic_if(last_source < 0 ||
                 static_cast<uint64_t>(last_source) ==
                     std::numeric_limits<Addr>::max(),
             "I[%d] invalid final SoA/JIT source position %ld\n",
             my_indirect_id, last_source);
    const Addr source_elements = static_cast<Addr>(last_source) + 1;
    const auto checkedEnd = [this](Addr begin, Addr elements,
                                   Addr element_bytes, const char *name) {
        panic_if(elements >
                     (std::numeric_limits<Addr>::max() - begin) /
                         element_bytes,
                 "I[%d] SoA/JIT %s byte span overflows\n",
                 my_indirect_id, name);
        return begin + elements * element_bytes;
    };
    const auto inside = [this](const Span &span, Addr minimum,
                               Addr maximum) {
        panic_if(span.begin < minimum || span.begin >= maximum ||
                     span.end <= span.begin || span.end > maximum,
                 "I[%d] SoA/JIT %s span [0x%lx,0x%lx) exceeds "
                 "registered range [0x%lx,0x%lx)\n",
                 my_indirect_id, span.name, span.begin, span.end,
                 minimum, maximum);
    };

    std::array<Span, 4> spans{};
    size_t span_count = 0;
    spans[span_count++] = {"mutable-A", my_base_addr, my_max_addr};
    if (!isSoaJitScalarRmw()) {
        spans[span_count++] = {
            "values", my_backing_addr,
            checkedEnd(my_backing_addr, source_elements, my_word_size,
                       "values")};
    }
    const size_t index_span = span_count;
    spans[span_count++] = {
        "indices", my_index_addr,
        checkedEnd(my_index_addr, source_elements, sizeof(uint32_t),
                   "indices")};
    size_t predicate_span = spans.size();
    if (my_predicate_addr != 0) {
        predicate_span = span_count;
        spans[span_count++] = {
            "predicate", my_predicate_addr,
            checkedEnd(my_predicate_addr, source_elements,
                       sizeof(uint32_t), "predicate")};
    }
    inside(spans[0], my_min_addr, my_max_addr);
    if (!isSoaJitScalarRmw())
        inside(spans[1], my_backing_min_addr, my_backing_max_addr);
    inside(spans[index_span], my_index_min_addr, my_index_max_addr);
    if (predicate_span != spans.size())
        inside(spans[predicate_span], my_predicate_min_addr,
               my_predicate_max_addr);

    const auto overlaps = [](Addr first_begin, Addr first_end,
                             Addr second_begin, Addr second_end) {
        return first_begin < second_end && second_begin < first_end;
    };
    for (size_t first = 0; first < span_count; ++first) {
        for (size_t second = first + 1; second < span_count; ++second) {
            panic_if(overlaps(spans[first].begin, spans[first].end,
                              spans[second].begin, spans[second].end),
                     "I[%d] SoA/JIT byte spans overlap: %s=[0x%lx,0x%lx) "
                     "%s=[0x%lx,0x%lx)\n",
                     my_indirect_id, spans[first].name, spans[first].begin,
                     spans[first].end, spans[second].name,
                     spans[second].begin, spans[second].end);
        }
    }
    struct PhysicalLineOwner
    {
        size_t span;
        Addr vaddr;
    };
    // This synchronous full-span prewalk is a simulator legality check, not
    // modeled hardware latency or state; its temporary host-side ledger and
    // translation/checking cost are deliberately absent from simulated time.
    // Runtime requests translate each virtual block independently, so physical
    // adjacency is irrelevant. Response routing is address-only, however, and
    // therefore requires every routed virtual cache line to have a unique
    // physical cache line across and within all spans.
    std::map<Addr, PhysicalLineOwner> physical_lines;
    for (size_t index = 0; index < span_count; ++index) {
        const Span &span = spans[index];
        const Addr first_block = addrBlockAligner(span.begin, block_size);
        const Addr last_block = addrBlockAligner(span.end - 1, block_size);
        for (Addr block = first_block;; block += block_size) {
            const Addr paddr = addrBlockAligner(
                translatePacket(block), block_size);
            const auto inserted = physical_lines.emplace(
                paddr, PhysicalLineOwner{index, block});
            if (!inserted.second) {
                const PhysicalLineOwner &owner = inserted.first->second;
                panic_if(owner.span == index,
                         "I[%d] SoA/JIT physical cache-line alias within "
                         "%s: vaddr=0x%lx and vaddr=0x%lx map to "
                         "paddr=0x%lx\n",
                         my_indirect_id, span.name, owner.vaddr, block,
                         paddr);
                panic("I[%d] SoA/JIT physical cache-line alias across "
                      "%s and %s: vaddr=0x%lx and vaddr=0x%lx map to "
                      "paddr=0x%lx\n",
                      my_indirect_id, spans[owner.span].name, span.name,
                      owner.vaddr, block, paddr);
            }
            if (block == last_block)
                break;
            panic_if(block >
                         std::numeric_limits<Addr>::max() - block_size,
                     "I[%d] SoA/JIT %s virtual routing span overflows\n",
                     my_indirect_id, span.name);
        }
    }
}
size_t
IndirectAccessUnit::soaPredicateSlotsUsed() const
{
    return std::count_if(
        soa_predicate_lines.begin(), soa_predicate_lines.end(),
        [](const SoaPredicateLine &line) {
            return line.pending || line.valid;
        });
}

bool
IndirectAccessUnit::soaPredicateLinesEmpty() const
{
    return soaPredicateSlotsUsed() == 0;
}

IndirectAccessUnit::SoaPredicateLine *
IndirectAccessUnit::findSoaPredicateLine(Addr block_vaddr)
{
    auto line = std::find_if(
        soa_predicate_lines.begin(), soa_predicate_lines.end(),
        [block_vaddr](const SoaPredicateLine &candidate) {
            return (candidate.pending || candidate.valid) &&
                   candidate.blockVaddr == block_vaddr;
        });
    return line == soa_predicate_lines.end() ? nullptr : &*line;
}

const IndirectAccessUnit::SoaPredicateLine *
IndirectAccessUnit::findSoaPredicateLine(Addr block_vaddr) const
{
    auto line = std::find_if(
        soa_predicate_lines.begin(), soa_predicate_lines.end(),
        [block_vaddr](const SoaPredicateLine &candidate) {
            return (candidate.pending || candidate.valid) &&
                   candidate.blockVaddr == block_vaddr;
        });
    return line == soa_predicate_lines.end() ? nullptr : &*line;
}

void
IndirectAccessUnit::serviceSoaPredicateFeeder(int itr)
{
    if (my_predicate_addr == 0)
        return;
    panic_if(!isSoaJitRmw() || itr < 0 || itr >= my_max ||
                 soa_jit_generation == 0,
             "I[%d] invalid SoA/JIT predicate feeder service\n",
             my_indirect_id);
    panic_if(soa_jit_predicate_active_credits <= 0 ||
                 soa_jit_predicate_active_credits >
                     static_cast<int>(SoaPredicateMaxLines),
             "I[%d] invalid active predicate credits %d\n",
             my_indirect_id, soa_jit_predicate_active_credits);

    size_t used = soaPredicateSlotsUsed();
    panic_if(used > static_cast<size_t>(soa_jit_predicate_active_credits),
             "I[%d] predicate feeder occupancy %lu exceeds credits %d\n",
             my_indirect_id, static_cast<unsigned long>(used),
             soa_jit_predicate_active_credits);
    for (int candidate = itr;
         candidate < my_max &&
             used < static_cast<size_t>(soa_jit_predicate_active_credits);
         ++candidate) {
        const int64_t source = soaSourcePosition(candidate);
        panic_if(source < 0,
                 "I[%d] negative SoA/JIT predicate position %ld\n",
                 my_indirect_id, source);
        const Addr vaddr = my_predicate_addr +
            static_cast<Addr>(source) * sizeof(uint32_t);
        const Addr block_vaddr = addrBlockAligner(vaddr, block_size);
        if (findSoaPredicateLine(block_vaddr) != nullptr)
            continue;

        auto free_line = std::find_if(
            soa_predicate_lines.begin(), soa_predicate_lines.end(),
            [](const SoaPredicateLine &line) {
                return !line.pending && !line.valid;
            });
        panic_if(free_line == soa_predicate_lines.end(),
                 "I[%d] predicate feeder has no free fixed slot\n",
                 my_indirect_id);
        const Addr block_paddr = addrBlockAligner(
            translatePacket(block_vaddr), block_size);
        panic_if(std::any_of(
                     soa_predicate_lines.begin(),
                     soa_predicate_lines.end(),
                     [block_vaddr, block_paddr](const SoaPredicateLine &line) {
                         return (line.pending || line.valid) &&
                                line.blockVaddr != block_vaddr &&
                                line.blockPaddr == block_paddr;
                     }),
                 "I[%d] predicate lines with distinct vaddrs alias paddr "
                 "0x%lx\n",
                 my_indirect_id, block_paddr);

        *free_line = SoaPredicateLine();
        free_line->blockVaddr = block_vaddr;
        free_line->blockPaddr = block_paddr;
        free_line->generation = soa_jit_generation;
        free_line->pending = true;
        used++;
        soa_jit_predicate_line_issues++;
        soa_jit_predicate_feeder_high_water = std::max<uint64_t>(
            soa_jit_predicate_feeder_high_water, used);
        DPRINTF(MAAVirtualTrace,
                "event=soa_jit_predicate_issue schema=1 unit=%d "
                "operation_tick=%lu generation=%lu slot=%lu "
                "vaddr=0x%lx paddr=0x%lx candidate=%d occupancy=%lu "
                "active_credits=%d\n",
                my_indirect_id, my_decode_start_tick, soa_jit_generation,
                static_cast<unsigned long>(std::distance(
                    soa_predicate_lines.begin(), free_line)),
                block_vaddr, block_paddr, candidate,
                static_cast<unsigned long>(used),
                soa_jit_predicate_active_credits);
        createSoaPredicateReadPacket(block_paddr, rowtable_latency);
    }
}

bool IndirectAccessUnit::ensureSoaPredicate(int itr)
{
    if (!isSoaJitRmw() || my_predicate_addr == 0)
        return true;
    const int64_t source = soaSourcePosition(itr);
    panic_if(source < 0,
             "I[%d] negative SoA/JIT predicate position %ld\n",
             my_indirect_id, source);
    const uint64_t byte_offset =
        static_cast<uint64_t>(source) * sizeof(uint32_t);
    const Addr bytes = my_predicate_max_addr - my_predicate_addr;
    panic_if(my_predicate_addr < my_predicate_min_addr ||
                 my_predicate_addr >= my_predicate_max_addr ||
                 bytes < sizeof(uint32_t) ||
                 byte_offset > bytes - sizeof(uint32_t),
             "I[%d] SoA/JIT predicate position %ld exceeds "
             "[0x%lx, 0x%lx)\n",
             my_indirect_id, source, my_predicate_min_addr,
             my_predicate_max_addr);
    const Addr vaddr = my_predicate_addr + byte_offset;
    const Addr block_vaddr = addrBlockAligner(vaddr, block_size);
    serviceSoaPredicateFeeder(itr);
    SoaPredicateLine *line = findSoaPredicateLine(block_vaddr);
    panic_if(line == nullptr || line->generation != soa_jit_generation,
             "I[%d] predicate feeder lost current itr %d generation %lu\n",
             my_indirect_id, itr, soa_jit_generation);
    if (line->valid) {
        soa_jit_predicate_line_hits++;
        DPRINTF(MAAVirtualTrace,
                "event=soa_jit_predicate_hit schema=1 unit=%d "
                "operation_tick=%lu generation=%lu itr=%d vaddr=0x%lx "
                "paddr=0x%lx occupancy=%lu active_credits=%d\n",
                my_indirect_id, my_decode_start_tick, soa_jit_generation,
                itr, block_vaddr, line->blockPaddr,
                static_cast<unsigned long>(soaPredicateSlotsUsed()),
                soa_jit_predicate_active_credits);
        return true;
    }
    panic_if(!line->pending,
             "I[%d] predicate feeder current line is neither ready nor "
             "pending\n",
             my_indirect_id);
    soa_jit_predicate_feeder_stalls++;
    DPRINTF(MAAVirtualTrace,
            "event=soa_jit_predicate_stall schema=1 unit=%d "
            "operation_tick=%lu generation=%lu itr=%d paddr=0x%lx "
            "occupancy=%lu active_credits=%d\n",
            my_indirect_id, my_decode_start_tick, soa_jit_generation, itr,
            line->blockPaddr,
            static_cast<unsigned long>(soaPredicateSlotsUsed()),
            soa_jit_predicate_active_credits);
    return false;
}
bool IndirectAccessUnit::soaPredicateValue(int itr)
{
    if (isSoaJitMaskedIndexRmw()) {
        panic_if(itr != my_i,
                 "I[%d] masked-index classification lost sequential order "
                 "%d/%d\n",
                 my_indirect_id, itr, my_i);
        return peekDirectIndex(itr) != SoaJitSafety::MaskedIndexInactive;
    }
    if (my_predicate_addr == 0)
        return true;
    const int64_t source = soaSourcePosition(itr);
    const Addr vaddr = my_predicate_addr +
        static_cast<Addr>(source) * sizeof(uint32_t);
    const Addr block_vaddr = addrBlockAligner(vaddr, block_size);
    SoaPredicateLine *line = findSoaPredicateLine(block_vaddr);
    panic_if(line == nullptr || !line->valid || line->pending ||
                 line->generation != soa_jit_generation,
             "I[%d] SoA/JIT predicate line for itr %d is not resident\n",
             my_indirect_id, itr);
    uint32_t predicate = 0;
    std::memcpy(&predicate,
                line->data.data() + (vaddr - block_vaddr),
                sizeof(predicate));
    soa_jit_predicate_uses++;
    DPRINTF(MAAVirtualTrace,
            "event=soa_jit_predicate_use schema=1 unit=%d "
            "operation_tick=%lu generation=%lu itr=%d source=%ld "
            "paddr=0x%lx value=%u uses=%lu\n",
            my_indirect_id, my_decode_start_tick, soa_jit_generation, itr,
            source, line->blockPaddr, predicate,
            soa_jit_predicate_uses);
    return predicate != 0;
}
void IndirectAccessUnit::discardSoaPredicateIfDone(int itr)
{
    if (my_predicate_addr == 0)
        return;
    const int64_t current_source = soaSourcePosition(itr);
    const Addr current_vaddr = my_predicate_addr +
        static_cast<Addr>(current_source) * sizeof(uint32_t);
    const Addr current_block_vaddr =
        addrBlockAligner(current_vaddr, block_size);
    SoaPredicateLine *line = findSoaPredicateLine(current_block_vaddr);
    panic_if(line == nullptr || !line->valid || line->pending ||
                 line->generation != soa_jit_generation,
             "I[%d] cannot discard unowned predicate line for itr %d\n",
             my_indirect_id, itr);
    const int next = itr + 1;
    if (next >= my_max) {
        *line = SoaPredicateLine();
        return;
    }
    const int64_t source = soaSourcePosition(next);
    const Addr next_vaddr = my_predicate_addr +
        static_cast<Addr>(source) * sizeof(uint32_t);
    if (addrBlockAligner(next_vaddr, block_size) !=
        current_block_vaddr)
        *line = SoaPredicateLine();
}
bool IndirectAccessUnit::receiveSoaPredicate(
    Addr addr, uint8_t *dataptr, bool is_block_cached)
{
    if (!isSoaJitRmw())
        return false;
    auto line = std::find_if(
        soa_predicate_lines.begin(), soa_predicate_lines.end(),
        [addr](const SoaPredicateLine &candidate) {
            return (candidate.pending || candidate.valid) &&
                   candidate.blockPaddr == addr;
        });
    // Predicate ownership is exact: an unmatched response may belong to an
    // active A/value request and must continue to those exact scoreboards.
    // receiveSoaJitData() panics if none of them owns it, so unknown responses
    // still fail closed without guessing from a physical address interval.
    if (line == soa_predicate_lines.end())
        return false;
    panic_if(line->generation != soa_jit_generation ||
                 soa_jit_generation == 0,
             "I[%d] stale predicate response at paddr 0x%lx: slot=%lu "
             "active=%lu\n",
             my_indirect_id, addr, line->generation, soa_jit_generation);
    panic_if(!line->pending || line->valid,
             "I[%d] duplicate predicate response at paddr 0x%lx\n",
             my_indirect_id, addr);
    accountReadResponse(addr, is_block_cached);
    std::memcpy(line->data.data(), dataptr, block_size);
    line->pending = false;
    line->valid = true;
    soa_jit_predicate_line_responses++;
    DPRINTF(MAAVirtualTrace,
            "event=soa_jit_predicate_response schema=1 unit=%d "
            "operation_tick=%lu generation=%lu slot=%lu vaddr=0x%lx "
            "paddr=0x%lx responses=%lu occupancy=%lu active_credits=%d\n",
            my_indirect_id, my_decode_start_tick, soa_jit_generation,
            static_cast<unsigned long>(std::distance(
                soa_predicate_lines.begin(), line)),
            line->blockVaddr, addr, soa_jit_predicate_line_responses,
            static_cast<unsigned long>(soaPredicateSlotsUsed()),
            soa_jit_predicate_active_credits);
    scheduleNextExecution(true);
    return true;
}
uint32_t IndirectAccessUnit::peekDirectIndex(int itr) const {
    if (descriptor_spool_replay_active) {
        panic_if(!descriptor_spool_current_valid ||
                     descriptor_spool_current_cursor !=
                         static_cast<uint32_t>(itr),
                 "I[%d] descriptor cursor %d is not decoded\n",
                 my_indirect_id, itr);
        return descriptor_spool_current_descriptor.value;
    }
    auto entry = direct_index_words.find(itr);
    panic_if(entry == direct_index_words.end(),
             "I[%d] streamed index %d is not buffered\n",
             my_indirect_id, itr);
    panic_if(entry->second.phase != direct_index_phase,
             "I[%d] streamed index %d has stale phase %u (expected %u)\n",
             my_indirect_id, itr, entry->second.phase,
             direct_index_phase);
    return entry->second.value;
}
const IndirectAccessUnit::DirectIndexWord &
IndirectAccessUnit::currentDirectIndexWord(int itr) const
{
    if (descriptor_spool_replay_active) {
        panic_if(!descriptor_spool_current_valid ||
                     descriptor_spool_current_cursor !=
                         static_cast<uint32_t>(itr),
                 "I[%d] descriptor cursor %d has no current word\n",
                 my_indirect_id, itr);
        return descriptor_spool_current_word;
    }
    const auto word = direct_index_words.find(itr);
    panic_if(word == direct_index_words.end(),
             "I[%d] streamed index %d is not buffered\n",
             my_indirect_id, itr);
    return word->second;
}
uint32_t IndirectAccessUnit::directIndexPassForGrow(Addr grow_addr) const {
    if (!usesBoundedDirectIndexPasses())
        return grow_addr % direct_index_partitions;
    const uint32_t pass = bounded_range_pass.passForGrow(grow_addr);
    panic_if(pass >= static_cast<uint32_t>(direct_index_partitions),
             "I[%d] grow 0x%lx has no bounded range pass\n",
             my_indirect_id, grow_addr);
    return pass;
}
uint64_t IndirectAccessUnit::directIndexRangeKey(
    uint32_t, Addr grow_addr, int iteration) const
{
    if (!usesBoundedDirectIndexPasses() ||
        maa->virtual_index_range_policy != 3)
        return grow_addr;
    if (direct_index_iteration_fallback)
        return static_cast<uint64_t>(iteration);
    return grow_addr;
}
void IndirectAccessUnit::finishAdaptiveSummary()
{
    panic_if(!direct_index_summary_active || !offset_table->summaryActive(),
             "I[%d] adaptive summary is not active\n", my_indirect_id);
    panic_if(!direct_index_pending_lines.empty() ||
                 !direct_index_ready_lines.empty() ||
                 !direct_index_words.empty(),
             "I[%d] adaptive summary ended with buffered B data\n",
             my_indirect_id);
    panic_if(direct_index_summary_next_iteration !=
                 static_cast<uint32_t>(my_max),
             "I[%d] adaptive summary inspected %u/%d iterations\n",
             my_indirect_id, direct_index_summary_next_iteration, my_max);

    direct_index_summary_records = offset_table->summaryRecords();
    direct_index_summary_probes = offset_table->summaryProbes();
    (*maa->stats.IND_BoundedSummaryRecords[my_indirect_id]) +=
        direct_index_summary_records;
    (*maa->stats.IND_BoundedSummaryHashProbes[my_indirect_id]) +=
        direct_index_summary_probes;
    auto visit = [this](auto consumer) {
        offset_table->forEachSummaryRecord(
            [this, &consumer](uint32_t key, uint32_t count) {
                DPRINTF(MAAVirtualTrace,
                        "event=bounded_grow_histogram_record schema=1 "
                        "unit=%d operation_tick=%lu grow=%u count=%u\n",
                        my_indirect_id, my_decode_start_tick, key, count);
                consumer(key, count);
            });
    };
    const auto plan_result = direct_index_summary_overflow
        ? BoundedGrowPassPlan::Result::TooManyRecords
        : bounded_grow_plan.configure(
              my_max, offset_table->capacity(),
              direct_index_max_partitions, visit);
    direct_index_summary_reduction_visits =
        BoundedGrowPassPlan::modeledReductionVisits(
            offset_table->capacity(), bounded_grow_plan.operations());
    (*maa->stats.IND_BoundedSummaryReductionVisits[my_indirect_id]) +=
        direct_index_summary_reduction_visits;
    (*maa->stats.IND_BoundedSummaryPlanBytes[my_indirect_id]) +=
        bounded_grow_plan.chargedBytes();

    BoundedRangePassTracker::Result tracker_result;
    if (plan_result == BoundedGrowPassPlan::Result::Accepted) {
        direct_index_partitions = bounded_grow_plan.passes();
        if (maa->virtual_index_descriptor_spool) {
            tracker_result = bounded_range_pass.configureSelectedPopulations(
                my_max, offset_table->capacity(), direct_index_partitions,
                [this](uint32_t pass) {
                    return bounded_grow_plan.population(pass);
                });
        } else {
            tracker_result = bounded_range_pass.configureSelected(
                my_max, offset_table->capacity(), direct_index_partitions);
        }
        direct_index_iteration_fallback = false;
        const auto replay_result = bounded_grow_plan.beginReplay();
        panic_if(replay_result != BoundedGrowPassPlan::Result::Accepted,
                 "I[%d] cannot begin grow replay: %s\n", my_indirect_id,
                 BoundedGrowPassPlan::resultName(replay_result));
    } else {
        panic_if(maa->virtual_index_descriptor_spool,
                 "I[%d] descriptor spool has no fallback: grow plan failed "
                 "closed with %s\n", my_indirect_id,
                 BoundedGrowPassPlan::resultName(plan_result));
        const bool has_iteration_fallback =
            plan_result == BoundedGrowPassPlan::Result::TooManyRecords ||
            plan_result ==
                BoundedGrowPassPlan::Result::RequiresIterationFallback;
        panic_if(!has_iteration_fallback,
                 "I[%d] adaptive summary failed closed: %s\n",
                 my_indirect_id,
                 BoundedGrowPassPlan::resultName(plan_result));
        direct_index_iteration_fallback = true;
        direct_index_partitions = getCeiling(
            my_max, offset_table->capacity());
        tracker_result = bounded_range_pass.configureRange(
            my_max, offset_table->capacity(), direct_index_partitions,
            0, my_max);
    }
    panic_if(tracker_result != BoundedRangePassTracker::Result::Accepted,
             "I[%d] adaptive replay configuration failed: %s\n",
             my_indirect_id,
             BoundedRangePassTracker::resultName(tracker_result));

    if (maa->virtual_index_descriptor_spool) {
        panic_if(my_max != static_cast<int>(
                              BoundedDescriptorSpool::MaxLogicalDescriptors) ||
                     offset_table->capacity() != static_cast<int>(
                         BoundedDescriptorSpool::MaxActiveDescriptors) ||
                     direct_index_partitions !=
                         static_cast<int>(BoundedDescriptorSpool::MaxPasses) ||
                     bounded_grow_plan.residentPass() != 0,
                 "I[%d] resident-first spool requires logical16K, active4K, "
                 "four counted passes, and deterministic resident pass 0\n",
                 my_indirect_id);
        const uint64_t payload_end = my_backing_addr +
            static_cast<uint64_t>(my_max) * my_word_size;
        constexpr uint64_t paged_slot_bytes =
            static_cast<uint64_t>(
                BoundedDescriptorSpool::MaxExternalPasses) *
            BoundedDescriptorSpool::MaxActiveDescriptors *
            BoundedDescriptorSpool::DescriptorBytes;
        static_assert(paged_slot_bytes %
                          BoundedDescriptorSpool::LineBytes == 0);
        const uint64_t slot_bytes = maa->virtual_bounded_global_merge
            ? BoundedFourRunMerge::RequiredBackingBytes
            : paged_slot_bytes;
        const uint64_t unit_tail =
            static_cast<uint64_t>(my_indirect_id + 1) * slot_bytes;
        panic_if(my_backing_max_addr < unit_tail,
                 "I[%d] descriptor spool range underflows backing\n",
                 my_indirect_id);
        descriptor_spool_base_vaddr =
            (my_backing_max_addr - unit_tail) &
            ~(static_cast<Addr>(BoundedDescriptorSpool::LineBytes) - 1);
        panic_if(descriptor_spool_base_vaddr < payload_end ||
                     descriptor_spool_base_vaddr < my_backing_min_addr,
                 "I[%d] registered backing [0x%lx,0x%lx) lacks an isolated "
                 "%lu-byte descriptor slot after payload end 0x%lx\n",
                 my_indirect_id, my_backing_min_addr, my_backing_max_addr,
                 slot_bytes, payload_end);
        const auto spool_result = descriptor_spool.configure(
            my_max, direct_index_partitions,
            bounded_grow_plan.residentPass(),
            [this](uint32_t pass) {
                return bounded_grow_plan.population(pass);
            },
            descriptor_spool_base_vaddr +
                (maa->virtual_bounded_global_merge
                     ? BoundedFourRunMerge::RunStrideBytes : 0),
            paged_slot_bytes,
            maa->virtual_descriptor_spool_read_credits,
            maa->virtual_descriptor_spool_write_credits);
        panic_if(spool_result != BoundedDescriptorSpool::Result::Accepted,
                 "I[%d] descriptor spool configuration failed: %s\n",
                 my_indirect_id,
                 BoundedDescriptorSpool::resultName(spool_result));
        if (maa->virtual_bounded_global_merge) {
            const std::array<uint32_t, BoundedFourRunMerge::Runs>
                populations{
                    bounded_grow_plan.population(0),
                    bounded_grow_plan.population(1),
                    bounded_grow_plan.population(2),
                    bounded_grow_plan.population(3)};
            const auto merge_result = bounded_global_merge.configure(
                my_max, populations, descriptor_spool_base_vaddr,
                slot_bytes);
            panic_if(merge_result !=
                         BoundedFourRunMerge::Result::Accepted,
                     "I[%d] bounded global merge configuration failed: %s\n",
                     my_indirect_id,
                     BoundedFourRunMerge::resultName(merge_result));
        }
        descriptor_spool_bucket_active = true;
        descriptor_spool_bucket_scan_complete = false;
        descriptor_spool_replay_active = false;
        descriptor_spool_operation = true;
        descriptor_spool_index_page_paddrs.fill(0);
        descriptor_spool_index_page_valid.fill(false);
    }

    const uint32_t row_directories =
        num_RT_slices[my_RT_config] * num_RT_rows_per_slice;
    const uint32_t row_lines =
        row_directories * num_RT_slice_columns[my_RT_config];
    const BoundedMetadataLedger ledger{
        static_cast<uint32_t>(offset_table->capacity()),
        static_cast<uint32_t>(offset_table->capacity()),
        row_directories, row_lines,
        maa->physical_tile_elements, maa->num_tiles};
    (*maa->stats.IND_BoundedWordEntries[my_indirect_id]) +=
        ledger.wordEntries;
    (*maa->stats.IND_BoundedOffsetLinkEntries[my_indirect_id]) +=
        ledger.offsetLinkEntries;
    (*maa->stats.IND_BoundedRowDirectoryEntries[my_indirect_id]) +=
        ledger.rowDirectoryEntries;
    (*maa->stats.IND_BoundedRowLineEntries[my_indirect_id]) +=
        ledger.rowLineEntries;
    (*maa->stats.IND_BoundedReorderMetadataBytes[my_indirect_id]) +=
        ledger.reorderMetadataBytes();
    DPRINTF(MAAVirtualTrace,
            "event=bounded_range_begin schema=2 unit=%d "
            "operation_tick=%lu logical=%d word_entries=%u "
            "offset_link_entries=%u row_directory_entries=%u "
            "row_line_entries=%u allocated_row_configs=1 passes=%d "
            "max_passes=%d "
            "range_policy=3 key=%s checker_bytes=%lu "
            "reorder_metadata_bytes=%lu scratchpad_elements_per_tile=%u "
            "scratchpad_payload_bytes=%lu backing=%s "
            "combiner=retained\n",
            my_indirect_id, my_decode_start_tick, my_max,
            ledger.wordEntries, ledger.offsetLinkEntries,
            ledger.rowDirectoryEntries, ledger.rowLineEntries,
            direct_index_partitions, direct_index_max_partitions,
            direct_index_iteration_fallback
                ? "logical_iteration_fallback"
                : "translated_dram_grow",
            static_cast<unsigned long>(bounded_range_pass.chargedBytes()),
            static_cast<unsigned long>(ledger.reorderMetadataBytes()),
            ledger.scratchpadElementsPerTile,
            static_cast<unsigned long>(ledger.scratchpadPayloadBytes()),
            maa->virtual_index_descriptor_spool
                ? "llc_descriptor_spool" : "llc_index_rescan");

    const uint64_t modeled_visits = direct_index_summary_probes +
        direct_index_summary_reduction_visits;
    panic_if(modeled_visits > static_cast<uint64_t>(
                                  std::numeric_limits<int>::max()),
             "I[%d] adaptive summary modeled work overflow\n",
             my_indirect_id);
    const Cycles summary_latency(getCeiling(
        static_cast<int>(modeled_visits),
        direct_index_filter_words_per_cycle));
    if (my_direct_index_filter_finish_tick < curTick())
        my_direct_index_filter_finish_tick =
            maa->getClockEdge(summary_latency);
    else
        my_direct_index_filter_finish_tick +=
            maa->getCyclesToTicks(summary_latency);
    (*maa->stats.IND_VirtIndexFilterCycles[my_indirect_id]) +=
        summary_latency;

    DPRINTF(MAAVirtualTrace,
            "event=bounded_grow_summary_complete schema=1 unit=%d "
            "operation_tick=%lu grow_records=%u observations=%u "
            "hash_probes=%lu reduction_visits=%lu modeled_cycles=%lu "
            "fallback=%s plan_result=%s split_records=%u "
            "plan_bytes=%lu backing=llc_index_scan "
            "histogram_storage=phase_shared_word_offset\n",
            my_indirect_id, my_decode_start_tick,
            direct_index_summary_records,
            offset_table->summaryObservations(),
            direct_index_summary_probes,
            direct_index_summary_reduction_visits,
            static_cast<uint64_t>(summary_latency),
            direct_index_iteration_fallback ? "iteration_ranges" : "none",
            BoundedGrowPassPlan::resultName(plan_result),
            direct_index_iteration_fallback
                ? 0 : bounded_grow_plan.splitRecords(),
            static_cast<unsigned long>(bounded_grow_plan.chargedBytes()));
    for (int pass = 0; pass < direct_index_partitions; ++pass) {
        const auto range = bounded_range_pass.range(pass);
        DPRINTF(MAAVirtualTrace,
                "event=bounded_grow_pass_plan schema=1 unit=%d "
                "operation_tick=%lu pass=%d lower=%lu upper=%lu "
                "planned_population=%u quota_mode=%s key=%s\n",
                my_indirect_id, my_decode_start_tick, pass,
                range.lower, range.upper,
                direct_index_iteration_fallback
                    ? static_cast<uint32_t>(range.upper - range.lower)
                    : bounded_grow_plan.population(pass),
                direct_index_iteration_fallback
                    ? "none" : "record_pass",
                direct_index_iteration_fallback
                    ? "logical_iteration_fallback"
                    : "translated_dram_grow");
    }

    offset_table->endSummary();
    direct_index_summary_active = false;
    direct_index_next_prefetch_itr = 0;
    direct_index_partition = 0;
    panic_if(direct_index_phase == std::numeric_limits<uint32_t>::max(),
             "I[%d] direct-index phase token overflow\n", my_indirect_id);
    direct_index_phase++;
    my_i = 0;
    scheduleNextExecution(true);
}
uint16_t
IndirectAccessUnit::captureDescriptorIndexPage(uint32_t iteration,
                                                Addr word_paddr)
{
    panic_if(iteration >= static_cast<uint32_t>(my_max),
             "I[%d] descriptor iteration %u exceeds logical size %d\n",
             my_indirect_id, iteration, my_max);
    const int64_t source_index = static_cast<int64_t>(my_index_min) +
        static_cast<int64_t>(iteration) * my_index_stride;
    panic_if(source_index < 0,
             "I[%d] descriptor source index is negative: itr=%u index=%ld\n",
             my_indirect_id, iteration, source_index);
    panic_if(static_cast<uint64_t>(source_index) >
                 std::numeric_limits<Addr>::max() / sizeof(uint32_t),
             "I[%d] descriptor source byte offset overflows\n",
             my_indirect_id);
    const Addr byte_offset = static_cast<Addr>(source_index) *
        sizeof(uint32_t);
    panic_if(my_index_addr > std::numeric_limits<Addr>::max() - byte_offset,
             "I[%d] descriptor index virtual address overflows\n",
             my_indirect_id);
    const Addr word_vaddr = my_index_addr + byte_offset;
    panic_if(my_index_min < 0 ||
                 static_cast<uint64_t>(my_index_min) >
                     std::numeric_limits<Addr>::max() / sizeof(uint32_t),
             "I[%d] descriptor first source index is not representable: %d\n",
             my_indirect_id, my_index_min);
    const Addr first_source_byte_offset =
        static_cast<Addr>(my_index_min) * sizeof(uint32_t);
    panic_if(my_index_addr >
                 std::numeric_limits<Addr>::max() -
                     first_source_byte_offset,
             "I[%d] descriptor first source address overflows\n",
             my_indirect_id);
    // The finite page map describes this instruction's logical window, not
    // every earlier element in the application's index array.
    const Addr first_word_vaddr = my_index_addr + first_source_byte_offset;
    const Addr first_page = first_word_vaddr &
        ~(static_cast<Addr>(DescriptorIndexPageBytes) - 1);
    const uint64_t page = (word_vaddr - first_page) /
        DescriptorIndexPageBytes;
    const Addr page_offset = word_vaddr &
        (static_cast<Addr>(DescriptorIndexPageBytes) - 1);
    panic_if(page >= MaxDescriptorIndexPages ||
                 page_offset + sizeof(uint32_t) > DescriptorIndexPageBytes ||
                 word_paddr < page_offset,
             "I[%d] descriptor source mapping is not representable: "
             "itr=%u page=%lu offset=%lu paddr=0x%lx\n",
             my_indirect_id, iteration, page, page_offset, word_paddr);
    const Addr page_paddr = word_paddr - page_offset;
    if (!descriptor_spool_index_page_valid[page]) {
        descriptor_spool_index_page_valid[page] = true;
        descriptor_spool_index_page_paddrs[page] = page_paddr;
    } else {
        panic_if(descriptor_spool_index_page_paddrs[page] != page_paddr,
                 "I[%d] descriptor index page %lu changed physical base "
                 "from 0x%lx to 0x%lx\n", my_indirect_id, page,
                 descriptor_spool_index_page_paddrs[page], page_paddr);
    }
    return static_cast<uint16_t>(page);
}

Addr
IndirectAccessUnit::descriptorIndexWordPaddr(uint32_t iteration) const
{
    panic_if(iteration >= static_cast<uint32_t>(my_max),
             "I[%d] replay descriptor iteration %u exceeds logical size %d\n",
             my_indirect_id, iteration, my_max);
    const int64_t source_index = static_cast<int64_t>(my_index_min) +
        static_cast<int64_t>(iteration) * my_index_stride;
    panic_if(source_index < 0,
             "I[%d] replay descriptor source index is negative: "
             "itr=%u index=%ld\n", my_indirect_id, iteration, source_index);
    panic_if(static_cast<uint64_t>(source_index) >
                 std::numeric_limits<Addr>::max() / sizeof(uint32_t),
             "I[%d] replay descriptor source byte offset overflows\n",
             my_indirect_id);
    const Addr byte_offset = static_cast<Addr>(source_index) *
        sizeof(uint32_t);
    panic_if(my_index_addr > std::numeric_limits<Addr>::max() - byte_offset,
             "I[%d] replay descriptor index virtual address overflows\n",
             my_indirect_id);
    const Addr word_vaddr = my_index_addr + byte_offset;
    panic_if(my_index_min < 0 ||
                 static_cast<uint64_t>(my_index_min) >
                     std::numeric_limits<Addr>::max() / sizeof(uint32_t),
             "I[%d] replay first source index is not representable: %d\n",
             my_indirect_id, my_index_min);
    const Addr first_source_byte_offset =
        static_cast<Addr>(my_index_min) * sizeof(uint32_t);
    panic_if(my_index_addr >
                 std::numeric_limits<Addr>::max() -
                     first_source_byte_offset,
             "I[%d] replay first source address overflows\n",
             my_indirect_id);
    const Addr first_word_vaddr = my_index_addr + first_source_byte_offset;
    const Addr first_page = first_word_vaddr &
        ~(static_cast<Addr>(DescriptorIndexPageBytes) - 1);
    const uint64_t source_page = (word_vaddr - first_page) /
        DescriptorIndexPageBytes;
    const Addr page_offset = word_vaddr &
        (static_cast<Addr>(DescriptorIndexPageBytes) - 1);
    panic_if(source_page >= MaxDescriptorIndexPages ||
                 !descriptor_spool_index_page_valid[source_page],
             "I[%d] replay descriptor has invalid derived page: "
             "itr=%u page=%lu\n", my_indirect_id, iteration,
             source_page);
    return descriptor_spool_index_page_paddrs[source_page] + page_offset;
}

size_t
IndirectAccessUnit::descriptorSpoolControlBytes() const
{
    // Charge every candidate-only fixed structure at semantic capacity.
    const size_t read_scoreboard_bytes =
        maa->virtual_descriptor_spool_read_credits *
        sizeof(DescriptorSpoolPendingLine);
    constexpr size_t current_descriptor_bytes =
        sizeof(bool) + sizeof(uint32_t) +
        sizeof(BoundedDescriptorSpool::Descriptor) +
        sizeof(DirectIndexWord);
    const size_t write_scoreboard_bytes =
        maa->virtual_descriptor_spool_write_credits *
        sizeof(DescriptorSpoolWriteSlot);
    // Four existing read slots gain fixed read-ahead/demand/use tags through
    // their charged sizeof above. Charge the finite overlap sequencer and its
    // requested observability counters separately; no line capacity is added.
    constexpr size_t overlap_control_bytes =
        4 * sizeof(bool) + 11 * sizeof(uint32_t) + 5 * sizeof(uint64_t);
    return descriptor_spool.chargedControlBytes() +
        descriptor_spool_index_page_paddrs.size() * sizeof(Addr) +
        descriptor_spool_index_page_valid.size() * sizeof(bool) +
        read_scoreboard_bytes + current_descriptor_bytes +
        write_scoreboard_bytes + overlap_control_bytes;
}

bool IndirectAccessUnit::flushDescriptorSpoolLine(uint32_t pass,
                                                  bool allow_partial)
{
    if (!descriptor_spool.lineReady(pass, allow_partial))
        return true;
    Addr vaddr = 0;
    uint32_t payload_bytes = 0;
    std::array<uint8_t, BoundedDescriptorSpool::LineBytes> data{};
    const auto result = descriptor_spool.issueStagedLine(
        pass, allow_partial, vaddr, data, payload_bytes);
    if (result == BoundedDescriptorSpool::Result::NoWriteCredit) {
        (*maa->stats
              .IND_DescriptorSpoolWriteCreditStalls[my_indirect_id])++;
        return false;
    }
    panic_if(result != BoundedDescriptorSpool::Result::Accepted,
             "I[%d] descriptor line flush for pass %u failed: %s\n",
             my_indirect_id, pass,
             BoundedDescriptorSpool::resultName(result));
    createDescriptorSpoolWritePacket(vaddr, data);
    DPRINTF(MAAVirtualTrace,
            "event=descriptor_spool_line_materialized schema=1 unit=%d "
            "operation_tick=%lu pass=%u vaddr=0x%lx payload_bytes=%u "
            "external_bytes=%lu\n",
            my_indirect_id, my_decode_start_tick, pass, vaddr, payload_bytes,
            descriptor_spool.requiredBackingBytes());
    return true;
}
bool IndirectAccessUnit::finishDescriptorSpoolBucketing()
{
    panic_if(!descriptor_spool_bucket_active,
             "I[%d] descriptor bucketing is not active\n", my_indirect_id);
    if (!descriptor_spool_bucket_scan_complete) {
        const auto replay_result = bounded_grow_plan.finishReplay();
        panic_if(replay_result != BoundedGrowPassPlan::Result::Accepted,
                 "I[%d] one-scan grow assignment failed closure: %s\n",
                 my_indirect_id,
                 BoundedGrowPassPlan::resultName(replay_result));
        descriptor_spool_bucket_scan_complete = true;
    }
    for (uint32_t pass = 0; pass < descriptor_spool.passes(); ++pass) {
        if (!flushDescriptorSpoolLine(pass, true)) {
            descriptor_spool_final_flush_stalls++;
            (*maa->stats
                  .IND_DescriptorSpoolFinalFlushStalls[my_indirect_id])++;
            DPRINTF(MAAVirtualTrace,
                    "event=descriptor_spool_final_flush_stall schema=1 "
                    "unit=%d operation_tick=%lu pass=%u "
                    "reason=write_credit b_reinspection=0\n",
                    my_indirect_id, my_decode_start_tick, pass);
            return false;
        }
    }
    if (descriptor_spool.outstandingWriteCount() != 0)
        return false;
    const auto finish = descriptor_spool.finishBucketing();
    panic_if(finish != BoundedDescriptorSpool::Result::Accepted,
             "I[%d] descriptor bucketing failed closure: %s\n",
             my_indirect_id, BoundedDescriptorSpool::resultName(finish));
    DPRINTF(MAAVirtualTrace,
            "event=descriptor_spool_bucket_complete schema=1 unit=%d "
            "operation_tick=%lu logical=%u resident_pass=%u resident=%u "
            "external=%u segments=%u payload_bytes=%lu lines=%u "
            "write_acks=%u backing_bytes=%lu control_bytes=%lu "
            "staging_bytes=%u write_hwm=%u unique_inspections=%lu "
            "retry_inspections=%lu identity_check=trace_side\n",
            my_indirect_id, my_decode_start_tick, descriptor_spool.logical(),
            descriptor_spool.residentPass(),
            descriptor_spool.residentDescriptors(),
            descriptor_spool.externalDescriptors(),
            descriptor_spool.externalSegments(),
            descriptor_spool.externalPayloadBytes(),
            descriptor_spool.writeLinesIssued(),
            descriptor_spool.writeAcks(),
            descriptor_spool.reservedBackingBytes(),
            descriptorSpoolControlBytes(),
            descriptor_spool.activeStagingBytes(),
            descriptor_spool.outstandingWriteHighWater(),
            descriptor_spool_bucket_commits,
            descriptor_spool_bucket_attempts -
                descriptor_spool_bucket_commits);
    if (maa->virtual_bounded_global_merge)
        startBoundedGlobalRunMaterialization();
    else
        startDescriptorSpoolReplay();
    return true;
}
void IndirectAccessUnit::startBoundedGlobalRunMaterialization()
{
    panic_if(!maa->virtual_bounded_global_merge ||
                 !bounded_global_merge.configured() ||
                 descriptor_spool.residentPass() != 0 ||
                 descriptor_spool.residentDescriptors() !=
                     descriptor_spool.population(0),
             "I[%d] bounded global materialization cannot start\n",
             my_indirect_id);
    const auto begin = bounded_global_merge.beginMaterialization(0);
    panic_if(begin != BoundedFourRunMerge::Result::Accepted,
             "I[%d] resident run materialization failed to start: %s\n",
             my_indirect_id, BoundedFourRunMerge::resultName(begin));
    descriptor_spool_bucket_active = false;
    descriptor_spool_replay_active = false;
    bounded_global_merge_phase = BoundedGlobalMergePhase::Materialize;
    bounded_global_merge_run = 0;
    bounded_global_merge_slice_cursor = 0;
    bounded_global_merge_chain_head = -1;
    direct_index_partition = 0;
    direct_index_next_prefetch_itr = 0;
    my_i = my_max;
    DPRINTF(MAAVirtualTrace,
            "event=bounded_global_run_begin schema=1 unit=%d "
            "operation_tick=%lu run=0 population=%u source=resident "
            "sorter=row_offset_table active_limit=%u\n",
            my_indirect_id, my_decode_start_tick,
            bounded_global_merge.population(0),
            BoundedFourRunMerge::MaxActiveDescriptors);
    scheduleNextExecution(true);
}
void IndirectAccessUnit::startDescriptorSpoolReplay()
{
    panic_if(descriptor_spool.residentPass() != 0 ||
                 descriptor_spool.residentDescriptors() !=
                     descriptor_spool.population(0),
             "I[%d] deterministic resident pass did not close\n",
             my_indirect_id);
    descriptor_spool_bucket_active = false;
    descriptor_spool_replay_active = false;
    direct_index_partition = descriptor_spool.residentPass();
    direct_index_next_prefetch_itr = 0;
    // The complete resident population is already in active Row/Offset state.
    // Re-enter Fill at the logical end so the ordinary partition barrier
    // drains resident A/C work before the first external replay.
    my_i = my_max;
    DPRINTF(MAAVirtualTrace,
            "event=descriptor_spool_resident_begin schema=1 unit=%d "
            "operation_tick=%lu pass=0 population=%u active_limit=%u "
            "selection=largest_planned_population_low_pass_tie\n",
            my_indirect_id, my_decode_start_tick,
            descriptor_spool.population(0),
            BoundedDescriptorSpool::MaxActiveDescriptors);
    scheduleNextExecution(true);
}

void IndirectAccessUnit::resetBoundedGlobalSorterTables()
{
    panic_if(offset_table->occupancy() != 0,
             "I[%d] bounded global sorter retained %d Offset entries\n",
             my_indirect_id, offset_table->occupancy());
    offset_table->check_reset();
    for (int slice = 0; slice < num_RT_slices[my_RT_config]; ++slice) {
        RT[my_RT_config][slice].check_reset();
        RT[my_RT_config][slice].reset();
        my_RT_req_sent[my_RT_config][slice] = false;
    }
    bounded_global_merge_slice_cursor = 0;
    bounded_global_merge_chain_head = -1;
}

void IndirectAccessUnit::serviceBoundedGlobalRunMaterialization()
{
    panic_if(bounded_global_merge_phase !=
                     BoundedGlobalMergePhase::Materialize ||
                 bounded_global_merge_run >= BoundedFourRunMerge::Runs ||
                 !bounded_global_merge.materializing(),
             "I[%d] bounded global run materializer is not active\n",
             my_indirect_id);

    if (bounded_global_merge.writeLineReady(false)) {
        Addr vaddr = 0;
        uint32_t payload_bytes = 0;
        std::array<uint8_t, BoundedFourRunMerge::LineBytes> data{};
        const auto issue = bounded_global_merge.issueWriteLine(
            false, vaddr, data, payload_bytes);
        if (issue == BoundedFourRunMerge::Result::NoWriteCredit)
            return;
        panic_if(issue != BoundedFourRunMerge::Result::Accepted,
                 "I[%d] sorted run %u full-line issue failed: %s\n",
                 my_indirect_id, bounded_global_merge_run,
                 BoundedFourRunMerge::resultName(issue));
        createBoundedGlobalMergeWritePacket(vaddr, data);
        DPRINTF(MAAVirtualTrace,
                "event=bounded_global_sort_write_issue schema=1 unit=%d "
                "operation_tick=%lu run=%u vaddr=0x%lx "
                "payload_bytes=%u mode=timing\n",
                my_indirect_id, my_decode_start_tick,
                bounded_global_merge_run, vaddr, payload_bytes);
        scheduleNextExecution(true);
        return;
    }

    if (bounded_global_merge_chain_head != -1) {
        const OffsetTableEntry entry =
            offset_table->consume_entry(bounded_global_merge_chain_head);
        static_assert(sizeof(entry.wid) == sizeof(uint32_t));
        uint32_t descriptor_value = 0;
        std::memcpy(&descriptor_value, &entry.wid,
                    sizeof(descriptor_value));
        const auto staged = bounded_global_merge.stageMaterialized(
            BoundedFourRunMerge::Descriptor{
                static_cast<uint16_t>(entry.itr), descriptor_value});
        panic_if(staged != BoundedFourRunMerge::Result::Accepted,
                 "I[%d] sorted run %u descriptor %d stage failed: %s\n",
                 my_indirect_id, bounded_global_merge_run, entry.itr,
                 BoundedFourRunMerge::resultName(staged));
        updateLatency(0, 0, 0, 1, 0, total_num_RT_subslices);
        scheduleNextExecution(true);
        return;
    }

    while (bounded_global_merge_slice_cursor <
           static_cast<uint32_t>(num_RT_slices[my_RT_config])) {
        const int slice = my_RT_slice_order[my_RT_config]
            [bounded_global_merge_slice_cursor];
        Addr grow_addr = 0;
        Addr line_addr = 0;
        int head = -1;
        int words = 0;
        uint64_t comparisons = 0;
        if (RT[my_RT_config][slice].claim_entry_send_sorted(
                grow_addr, line_addr, head, words, comparisons)) {
            panic_if(head < 0 || words <= 0,
                     "I[%d] sorted RowTable claim is empty\n",
                     my_indirect_id);
            bounded_global_merge_chain_head = head;
            bounded_global_merge_sort_comparisons += comparisons;
            panic_if(comparisons >=
                         static_cast<uint64_t>(
                             std::numeric_limits<int>::max()),
                     "I[%d] sorted RowTable comparison charge overflow\n",
                     my_indirect_id);
            updateLatency(0, 0, 0,
                          static_cast<int>(comparisons) + 1, 0,
                          total_num_RT_subslices);
            DPRINTF(MAAVirtualTrace,
                    "event=bounded_global_sort_claim schema=1 unit=%d "
                    "operation_tick=%lu run=%u slice_rank=%u slice=%d "
                    "grow=%lu line=0x%lx words=%d comparisons=%lu\n",
                    my_indirect_id, my_decode_start_tick,
                    bounded_global_merge_run,
                    bounded_global_merge_slice_cursor, slice, grow_addr,
                    line_addr, words, comparisons);
            scheduleNextExecution(true);
            return;
        }
        bounded_global_merge_slice_cursor++;
    }

    if (bounded_global_merge.writeLineReady(true)) {
        Addr vaddr = 0;
        uint32_t payload_bytes = 0;
        std::array<uint8_t, BoundedFourRunMerge::LineBytes> data{};
        const auto issue = bounded_global_merge.issueWriteLine(
            true, vaddr, data, payload_bytes);
        if (issue == BoundedFourRunMerge::Result::NoWriteCredit)
            return;
        panic_if(issue != BoundedFourRunMerge::Result::Accepted,
                 "I[%d] sorted run %u final-line issue failed: %s\n",
                 my_indirect_id, bounded_global_merge_run,
                 BoundedFourRunMerge::resultName(issue));
        createBoundedGlobalMergeWritePacket(vaddr, data);
        DPRINTF(MAAVirtualTrace,
                "event=bounded_global_sort_write_issue schema=1 unit=%d "
                "operation_tick=%lu run=%u vaddr=0x%lx "
                "payload_bytes=%u mode=timing final=1\n",
                my_indirect_id, my_decode_start_tick,
                bounded_global_merge_run, vaddr, payload_bytes);
        scheduleNextExecution(true);
        return;
    }
    if (bounded_global_merge.outstandingWriteCount() != 0)
        return;

    const uint32_t completed_run = bounded_global_merge_run;
    const auto finish =
        bounded_global_merge.finishMaterialization(completed_run);
    panic_if(finish != BoundedFourRunMerge::Result::Accepted,
             "I[%d] sorted run %u failed closure: %s\n",
             my_indirect_id, completed_run,
             BoundedFourRunMerge::resultName(finish));
    if (completed_run != descriptor_spool.residentPass()) {
        const auto replay = descriptor_spool.finishReplay(completed_run);
        panic_if(replay != BoundedDescriptorSpool::Result::Accepted,
                 "I[%d] sorter input run %u failed replay closure: %s\n",
                 my_indirect_id, completed_run,
                 BoundedDescriptorSpool::resultName(replay));
        descriptor_spool_replay_active = false;
    }
    DPRINTF(MAAVirtualTrace,
            "event=bounded_global_run_complete schema=1 unit=%d "
            "operation_tick=%lu run=%u population=%u write_lines=%u "
            "write_acks=%u active_hwm=%u sort_comparisons=%lu\n",
            my_indirect_id, my_decode_start_tick, completed_run,
            bounded_global_merge.population(completed_run),
            bounded_global_merge.runLines(completed_run),
            bounded_global_merge.runLines(completed_run),
            bounded_global_merge.activeHighWater(),
            bounded_global_merge_sort_comparisons);
    resetBoundedGlobalSorterTables();

    if (completed_run + 1 < BoundedFourRunMerge::Runs) {
        bounded_global_merge_run = completed_run + 1;
        const auto materialize = bounded_global_merge.beginMaterialization(
            bounded_global_merge_run);
        panic_if(materialize != BoundedFourRunMerge::Result::Accepted,
                 "I[%d] sorted run %u cannot begin: %s\n",
                 my_indirect_id, bounded_global_merge_run,
                 BoundedFourRunMerge::resultName(materialize));
        const auto replay =
            descriptor_spool.beginReplay(bounded_global_merge_run);
        panic_if(replay != BoundedDescriptorSpool::Result::Accepted,
                 "I[%d] sorter input run %u cannot begin: %s\n",
                 my_indirect_id, bounded_global_merge_run,
                 BoundedDescriptorSpool::resultName(replay));
        descriptor_spool_replay_active = true;
        direct_index_partition = bounded_global_merge_run;
        panic_if(direct_index_phase ==
                     std::numeric_limits<uint32_t>::max(),
                 "I[%d] direct-index phase token overflow\n",
                 my_indirect_id);
        direct_index_phase++;
        direct_index_next_prefetch_itr = 0;
        my_i = 0;
        my_fill_finished = false;
        state = Status::Fill;
        transitionAttributionStage(AttributionStage::Fill,
                                   "bounded_global_next_sort_run");
        DPRINTF(MAAVirtualTrace,
                "event=bounded_global_run_begin schema=1 unit=%d "
                "operation_tick=%lu run=%u population=%u "
                "source=timed_unsorted_run sorter=row_offset_table "
                "active_limit=%u\n",
                my_indirect_id, my_decode_start_tick,
                bounded_global_merge_run,
                bounded_global_merge.population(bounded_global_merge_run),
                BoundedFourRunMerge::MaxActiveDescriptors);
        scheduleNextExecution(true);
        return;
    }

    const auto merge = bounded_global_merge.beginMerge();
    panic_if(merge != BoundedFourRunMerge::Result::Accepted,
             "I[%d] four-head merge cannot begin: %s\n",
             my_indirect_id, BoundedFourRunMerge::resultName(merge));
    bounded_global_merge_phase = BoundedGlobalMergePhase::Merge;
    bounded_global_merge_run = BoundedFourRunMerge::Runs;
    bounded_global_merge_batch_inflight = false;
    bounded_global_merge_source_pending = false;
    bounded_global_merge_source_ready = false;
    bounded_global_merge_source_paddr = 0;
    bounded_global_merge_source_vaddr = 0;
    bounded_global_merge_source_head = -1;
    bounded_global_merge_source_tail = -1;
    bounded_global_merge_source_words = 0;
    direct_index_partition = 0;
    my_i = my_max;
    my_fill_finished = false;
    virtual_build_incomplete = false;
    my_force_cache_determined = true;
    my_force_cache = maa->virtual_descriptor_spool_source_bypass_cache
        ? false : direct_index_force_cache;
    state = Status::Build;
    transitionAttributionStage(AttributionStage::Build,
                               "bounded_global_merge_begin");
    DPRINTF(MAAVirtualTrace,
            "event=bounded_global_merge_begin schema=1 unit=%d "
            "operation_tick=%lu runs=4 populations=%u,%u,%u,%u "
            "heads=4 line_buffers=4 descriptor_bytes=%u\n",
            my_indirect_id, my_decode_start_tick,
            bounded_global_merge.population(0),
            bounded_global_merge.population(1),
            bounded_global_merge.population(2),
            bounded_global_merge.population(3),
            BoundedFourRunMerge::DescriptorBytes);
    DPRINTF(MAAVirtualTrace,
            "event=bounded_global_source_route schema=1 unit=%d "
            "operation_tick=%lu source=A force_cache=%d bypass_cache=%d\n",
            my_indirect_id, my_decode_start_tick, my_force_cache,
            maa->virtual_descriptor_spool_source_bypass_cache);
    scheduleNextExecution(true);
}

std::array<uint64_t, 4>
IndirectAccessUnit::boundedGlobalMergeKey(
    const BoundedFourRunMerge::Descriptor &descriptor)
{
    panic_if(descriptor.iteration >= static_cast<uint32_t>(my_max),
             "I[%d] merged descriptor iteration %u exceeds %d\n",
             my_indirect_id, descriptor.iteration, my_max);
    panic_if(descriptor.value >
                 (std::numeric_limits<Addr>::max() - my_base_addr) /
                     static_cast<Addr>(my_word_size),
             "I[%d] merged descriptor address overflows\n",
             my_indirect_id);
    const Addr word_vaddr = my_base_addr +
        static_cast<Addr>(descriptor.value) * my_word_size;
    panic_if(word_vaddr < my_min_addr || word_vaddr >= my_max_addr,
             "I[%d] merged source word 0x%lx is outside A range\n",
             my_indirect_id, word_vaddr);
    const Addr line_vaddr = addrBlockAligner(word_vaddr, block_size);
    const Addr line_paddr = addrBlockAligner(
        translatePacket(line_vaddr), block_size);
    const std::vector<int> address = maa->map_addr(line_paddr);
    const int native_slice = getRowTableIdx(
        my_RT_config, address[ADDR_CHANNEL_LEVEL], address[ADDR_RANK_LEVEL],
        address[ADDR_BANKGROUP_LEVEL], address[ADDR_BANK_LEVEL]);
    uint32_t slice_rank = num_RT_slices[my_RT_config];
    for (uint32_t rank = 0;
         rank < static_cast<uint32_t>(num_RT_slices[my_RT_config]); ++rank) {
        if (my_RT_slice_order[my_RT_config][rank] == native_slice) {
            slice_rank = rank;
            break;
        }
    }
    panic_if(slice_rank >=
                 static_cast<uint32_t>(num_RT_slices[my_RT_config]),
             "I[%d] merged source slice %d has no RowTable rank\n",
             my_indirect_id, native_slice);
    const Addr grow_addr = getGrowAddr(
        my_RT_config, address[ADDR_BANKGROUP_LEVEL],
        address[ADDR_BANK_LEVEL], address[ADDR_ROW_LEVEL]);
    return {slice_rank, grow_addr, line_paddr, descriptor.iteration};
}

bool
IndirectAccessUnit::virtualSourceCreditAvailable(int source_words) const
{
    panic_if(source_words <= 0,
             "I[%d] virtual source credit requested for %d words\n",
             my_indirect_id, source_words);
    if (virtual_reserved_responses >=
        static_cast<int>(virtual_response_slots.size()))
        return false;
    return virtual_response_word_pool_limit == 0 ||
        virtual_reserved_response_words + source_words <=
            virtual_response_word_pool_limit;
}

void
IndirectAccessUnit::issueVirtualSource(
    Addr source_addr, int source_head, int source_words, int source_rt_idx,
    int source_row_id, int source_entry_id, Addr source_grow_addr, int latency)
{
    panic_if(source_head < 0 || source_words <= 0,
             "I[%d] virtual source claim is empty\n", my_indirect_id);
    if (virtual_response_words != 0 &&
        virtual_response_word_pool_limit == 0)
        panic_if(source_words > virtual_response_words,
                 "I[%d] source response needs %d/%d packed words\n",
                 my_indirect_id, source_words, virtual_response_words);
    if (virtual_response_word_pool_limit != 0)
        panic_if(source_words > virtual_response_word_pool_limit,
                 "I[%d] source response needs %d/%d pooled words\n",
                 my_indirect_id, source_words,
                 virtual_response_word_pool_limit);
    panic_if(!virtualSourceCreditAvailable(source_words),
             "I[%d] cannot issue virtual source without bounded credit\n",
             my_indirect_id);

    if (virtual_response_word_pool_limit != 0)
        virtual_reserved_response_words += source_words;
    panic_if(!virtual_source_reservations
                  .emplace(source_addr,
                           VirtualSourceReservation{
                               source_head, source_words, source_rt_idx,
                               source_row_id, source_entry_id,
                               source_grow_addr})
                  .second,
             "I[%d] duplicate source reservation for 0x%lx\n",
             my_indirect_id, source_addr);
    my_expected_responses++;
    virtual_reserved_responses++;
    virtual_source_expected++;
    virtual_max_reserved_responses = std::max(
        virtual_max_reserved_responses, virtual_reserved_responses);
    virtual_max_reserved_response_words = std::max(
        virtual_max_reserved_response_words,
        virtual_reserved_response_words);
    recordReorderSurvivalIssue(source_addr);
    createReadPacket(source_addr, latency);
}

bool
IndirectAccessUnit::issueBoundedGlobalSourceLine()
{
    panic_if(!bounded_global_merge_source_ready ||
                 bounded_global_merge_source_paddr == 0 ||
                 bounded_global_merge_source_head < 0 ||
                 bounded_global_merge_source_tail < 0 ||
                 bounded_global_merge_source_words <= 0,
             "I[%d] bounded global source line is incomplete\n",
             my_indirect_id);
    if (!virtualSourceCreditAvailable(bounded_global_merge_source_words)) {
        if (virtual_reserved_responses >=
            static_cast<int>(virtual_response_slots.size()))
            macro_a_retries++;
        else
            virtual_response_word_pool_stalls++;
        DPRINTF(MAAVirtualTrace,
                "event=bounded_global_stream_stall schema=1 unit=%d "
                "operation_tick=%lu reason=source_credit paddr=0x%lx "
                "words=%d reserved=%d reserved_words=%d\n",
                my_indirect_id, my_decode_start_tick,
                bounded_global_merge_source_paddr,
                bounded_global_merge_source_words,
                virtual_reserved_responses,
                virtual_reserved_response_words);
        return false;
    }
    const auto end = bounded_global_merge.endSourceLine();
    panic_if(end != BoundedFourRunMerge::Result::Accepted,
             "I[%d] merged A-line cluster failed closure: %s\n",
             my_indirect_id, BoundedFourRunMerge::resultName(end));
    issueVirtualSource(
        bounded_global_merge_source_paddr,
        bounded_global_merge_source_head,
        bounded_global_merge_source_words, -1, -1, -1, 0, 0);
    DPRINTF(MAAVirtualTrace,
            "event=bounded_global_stream_issue schema=1 unit=%d "
            "operation_tick=%lu paddr=0x%lx vaddr=0x%lx head=%d "
            "words=%d reserved=%d reserved_words=%d\n",
            my_indirect_id, my_decode_start_tick,
            bounded_global_merge_source_paddr,
            bounded_global_merge_source_vaddr,
            bounded_global_merge_source_head,
            bounded_global_merge_source_words,
            virtual_reserved_responses,
            virtual_reserved_response_words);
    bounded_global_merge_source_ready = false;
    bounded_global_merge_source_paddr = 0;
    bounded_global_merge_source_vaddr = 0;
    bounded_global_merge_source_head = -1;
    bounded_global_merge_source_tail = -1;
    bounded_global_merge_source_words = 0;
    return true;
}

void IndirectAccessUnit::serviceBoundedGlobalMerge()
{
    panic_if(bounded_global_merge_phase !=
                     BoundedGlobalMergePhase::Merge ||
                 !bounded_global_merge.merging(),
             "I[%d] bounded global merge service is not active\n",
             my_indirect_id);
    if (drainVirtualResponses()) {
        scheduleExecuteInstructionEvent(1);
        return;
    }
    drainVirtualCombiner(false);

    for (uint32_t run = 0; run < BoundedFourRunMerge::Runs; ++run) {
        if (!bounded_global_merge.needsRead(run))
            continue;
        Addr vaddr = 0;
        uint32_t line = 0;
        const auto next = bounded_global_merge.nextRead(
            run, vaddr, line);
        panic_if(next != BoundedFourRunMerge::Result::Accepted,
                 "I[%d] merge run %u read issue failed: %s\n",
                 my_indirect_id, run,
                 BoundedFourRunMerge::resultName(next));
        createBoundedGlobalMergeReadPacket(vaddr, run, line);
    }
    if (!bounded_global_merge.readyToSelect())
        return;

    uint32_t selected = BoundedFourRunMerge::Runs;
    const auto selected_result = bounded_global_merge.selectHead(
        [this](const auto &descriptor) {
            return boundedGlobalMergeKey(descriptor);
        }, selected);
    if (selected_result == BoundedFourRunMerge::Result::NoWork) {
        panic_if(!bounded_global_merge.mergeDone(),
                 "I[%d] merge lost all heads before closure\n",
                 my_indirect_id);
        if (bounded_global_merge_source_ready &&
            !issueBoundedGlobalSourceLine())
            return;
        if (state != Status::Request) {
            state = Status::Request;
            transitionAttributionStage(
                AttributionStage::Request,
                "bounded_global_stream_merge_complete");
            scheduleNextExecution(true);
            return;
        }
        virtual_final_flush = true;
        if (drainVirtualResponses()) {
            scheduleExecuteInstructionEvent(1);
            return;
        }
        drainVirtualCombiner(true);
        if (!boundedRetirementComplete())
            return;
        const auto finish = bounded_global_merge.finishMerge();
        panic_if(finish != BoundedFourRunMerge::Result::Accepted,
                 "I[%d] bounded global merge failed closure: %s\n",
                 my_indirect_id,
                 BoundedFourRunMerge::resultName(finish));
        bounded_global_merge_terminal_acks =
            descriptor_spool.writeAcks() +
            bounded_global_merge.sortedWriteAcks() +
            descriptor_spool.readLineResponses() +
            bounded_global_merge.readLines() +
            bounded_global_merge_source_responses +
            attribution_write_completions;
        bounded_global_merge_phase = BoundedGlobalMergePhase::Complete;
        my_fill_finished = false;
        state = Status::Response;
        transitionAttributionStage(AttributionStage::Response,
                                   "bounded_global_merge_complete");
        scheduleNextExecution(true);
        return;
    }
    panic_if(selected_result != BoundedFourRunMerge::Result::Accepted ||
                 selected >= BoundedFourRunMerge::Runs,
             "I[%d] four-head selection failed: %s\n", my_indirect_id,
             BoundedFourRunMerge::resultName(selected_result));
    const auto descriptor = bounded_global_merge.head(selected);
    const auto key = boundedGlobalMergeKey(descriptor);
    panic_if(bounded_global_merge_last_key_valid &&
                 key < bounded_global_merge_last_key,
             "I[%d] four-run merge order regressed at itr=%u\n",
             my_indirect_id, descriptor.iteration);
    const Addr line_paddr = key[2];
    if (bounded_global_merge_source_ready &&
        bounded_global_merge_source_paddr != line_paddr &&
        !issueBoundedGlobalSourceLine())
        return;
    if (offset_table->is_full()) {
        DPRINTF(MAAVirtualTrace,
                "event=bounded_global_stream_stall schema=1 unit=%d "
                "operation_tick=%lu reason=offset_credit occupancy=%d\n",
                my_indirect_id, my_decode_start_tick,
                offset_table->occupancy());
        return;
    }
    const Addr word_vaddr = my_base_addr +
        static_cast<Addr>(descriptor.value) * my_word_size;
    const Addr line_vaddr = addrBlockAligner(word_vaddr, block_size);
    const uint32_t wid = (word_vaddr - line_vaddr) / my_word_size;
    panic_if(wid >= static_cast<uint32_t>(my_words_per_cl),
             "I[%d] merged descriptor word id %u is invalid\n",
             my_indirect_id, wid);
    if (!bounded_global_merge_source_ready) {
        const auto begin = bounded_global_merge.beginSourceLine(line_paddr);
        panic_if(begin != BoundedFourRunMerge::Result::Accepted,
                 "I[%d] staged A-line cluster 0x%lx cannot begin: %s\n",
                 my_indirect_id, line_paddr,
                 BoundedFourRunMerge::resultName(begin));
        bounded_global_merge_source_ready = true;
        bounded_global_merge_source_paddr = line_paddr;
        bounded_global_merge_source_vaddr = line_vaddr;
        bounded_global_merge_source_head = -1;
        bounded_global_merge_source_tail = -1;
        bounded_global_merge_source_words = 0;
        if (!bounded_global_merge_last_row_valid ||
            bounded_global_merge_last_slice != key[0] ||
            bounded_global_merge_last_row != key[1]) {
            bounded_global_merge_row_groups++;
            bounded_global_merge_last_row_valid = true;
            bounded_global_merge_last_slice = key[0];
            bounded_global_merge_last_row = key[1];
        }
    }
    const int offset_entry = offset_table->insert(
        descriptor.iteration, static_cast<int>(wid),
        bounded_global_merge_source_tail, static_cast<int>(selected));
    if (bounded_global_merge_source_head == -1)
        bounded_global_merge_source_head = offset_entry;
    bounded_global_merge_source_tail = offset_entry;
    bounded_global_merge_source_words++;
    const auto retired = bounded_global_merge.recordRetirement(line_paddr);
    panic_if(retired != BoundedFourRunMerge::Result::Accepted,
             "I[%d] merged descriptor %u retirement failed: %s\n",
             my_indirect_id, descriptor.iteration,
             BoundedFourRunMerge::resultName(retired));
    const auto consumed = bounded_global_merge.consumeHead(selected);
    panic_if(consumed != BoundedFourRunMerge::Result::Accepted,
             "I[%d] merge run %u head consumption failed: %s\n",
             my_indirect_id, selected,
             BoundedFourRunMerge::resultName(consumed));
    bounded_global_merge_last_key = key;
    bounded_global_merge_last_key_valid = true;
    updateLatency(0, 0, 0, 1, 0, total_num_RT_subslices);
    DPRINTF(MAAVirtualTrace,
            "event=bounded_global_stream_stage schema=1 unit=%d "
            "operation_tick=%lu run=%u itr=%u line=0x%lx wid=%u "
            "line_words=%d occupancy=%d\n",
            my_indirect_id, my_decode_start_tick, selected,
            descriptor.iteration, line_paddr, wid,
            bounded_global_merge_source_words,
            offset_table->occupancy());
    scheduleExecuteInstructionEvent(1);
}
BoundedRangePassTracker::Range
IndirectAccessUnit::directIndexSourceGrowRange()
{
    panic_if(my_instruction->maxAddr <= my_instruction->minAddr,
             "I[%d] source-relative range has an empty A interval\n",
             my_indirect_id);
    const auto growForAddress = [this](Addr vaddr) {
        const Addr block_vaddr = addrBlockAligner(vaddr, block_size);
        const Addr block_paddr = addrBlockAligner(
            translatePacket(block_vaddr), block_size);
        const std::vector<int> address = maa->map_addr(block_paddr);
        return getGrowAddr(my_RT_config,
                           address[ADDR_BANKGROUP_LEVEL],
                           address[ADDR_BANK_LEVEL],
                           address[ADDR_ROW_LEVEL]);
    };
    const Addr first = growForAddress(my_instruction->minAddr);
    const Addr last = growForAddress(my_instruction->maxAddr - 1);
    const Addr lower = std::min(first, last);
    const Addr upper = std::max(first, last) + 1;
    panic_if(upper > num_RT_possible_grows[my_RT_config],
             "I[%d] source-relative grow range [0x%lx,0x%lx) exceeds "
             "hardware grow space 0x%lx\n",
             my_indirect_id, lower, upper,
             num_RT_possible_grows[my_RT_config]);
    return {lower, upper};
}
int IndirectAccessUnit::directIndexRetirementPass() const {
    const int pass = direct_index_partition_barrier
        ? direct_index_partition - 1 : direct_index_partition;
    panic_if(pass < 0 || pass >= direct_index_partitions,
             "I[%d] invalid retirement pass %d (current=%d barrier=%d)\n",
             my_indirect_id, pass, direct_index_partition,
             direct_index_partition_barrier);
    return pass;
}
void IndirectAccessUnit::finishBoundedRangePass(int pass, const char *reason) {
    if (!usesBoundedDirectIndexPasses())
        return;
    if (descriptor_spool.configured()) {
        if (maa->virtual_bounded_global_merge) {
            panic_if(descriptor_spool_replay_active,
                     "I[%d] global merge pass %d retained an active replay\n",
                     my_indirect_id, pass);
            const bool resident = pass == static_cast<int>(
                descriptor_spool.residentPass());
            panic_if(!resident && !descriptor_spool.replayFinished(pass),
                     "I[%d] global merge pass %d did not finish its sorter "
                     "replay\n", my_indirect_id, pass);
        } else if (descriptor_spool_replay_active &&
            descriptor_spool.activeReplayPass() ==
                static_cast<uint32_t>(pass)) {
            const auto replay_result = descriptor_spool.finishReplay(pass);
            panic_if(replay_result != BoundedDescriptorSpool::Result::Accepted,
                     "I[%d] descriptor pass %d replay failed closure: %s\n",
                     my_indirect_id, pass,
                     BoundedDescriptorSpool::resultName(replay_result));
            descriptor_spool_replay_active = false;
        } else {
            const bool resident = pass == static_cast<int>(
                descriptor_spool.residentPass());
            panic_if(!resident && !descriptor_spool.replayFinished(pass),
                     "I[%d] descriptor pass %d neither closed replay nor "
                     "resident\n", my_indirect_id, pass);
        }
    } else if (!descriptor_spool.configured() &&
        maa->virtual_index_range_policy == 3 &&
        !direct_index_iteration_fallback) {
        const auto replay_result = bounded_grow_plan.finishReplay();
        panic_if(replay_result != BoundedGrowPassPlan::Result::Accepted,
                 "I[%d] bounded grow pass %d replay failed closure: %s\n",
                 my_indirect_id, pass,
                 BoundedGrowPassPlan::resultName(replay_result));
    }
    const auto result = bounded_range_pass.finishPass(pass);
    panic_if(result != BoundedRangePassTracker::Result::Accepted,
             "I[%d] bounded range pass %d failed closure: %s\n",
             my_indirect_id, pass,
             BoundedRangePassTracker::resultName(result));
    const auto range = bounded_range_pass.range(pass);
    (*maa->stats.IND_BoundedReplayPasses[my_indirect_id])++;
    DPRINTF(MAAVirtualTrace,
            "event=bounded_range_pass_complete schema=1 unit=%d "
            "operation_tick=%lu pass=%d passes=%d lower=0x%lx upper=0x%lx "
            "inspected=%u admitted=%u retired=%u drains=%u "
            "max_epoch_admissions=%u admitted_total=%u retired_total=%u "
            "reason=%s\n",
            my_indirect_id, my_decode_start_tick, pass,
            direct_index_partitions, range.lower, range.upper,
            bounded_range_pass.inspectionsForPass(pass),
            bounded_range_pass.admissionsForPass(pass),
            bounded_range_pass.retirementsForPass(pass),
            bounded_range_pass.drainsForPass(pass),
            bounded_range_pass.maxEpochAdmissionsForPass(pass),
            bounded_range_pass.admissions(), bounded_range_pass.retirements(),
            reason);
    if (!maa->virtual_bounded_global_merge &&
        descriptor_spool.configured() &&
        pass + 1 < direct_index_partitions) {
        panic_if(descriptor_spool_current_valid,
                 "I[%d] descriptor pass %d retained a decoded word\n",
                 my_indirect_id, pass);
        if (descriptor_spool_replay_active) {
            panic_if(!maa->virtual_descriptor_spool_read_ahead ||
                         !descriptor_spool_read_ahead_active ||
                         descriptor_spool.activeReplayPass() !=
                             static_cast<uint32_t>(pass + 1),
                     "I[%d] descriptor pass %d has an invalid early next "
                     "replay\n", my_indirect_id, pass);
            promoteDescriptorSpoolReadAhead(pass + 1);
        } else {
            panic_if(descriptorSpoolReadSlotsUsed() != 0,
                     "I[%d] descriptor pass %d retained read slots\n",
                     my_indirect_id, pass);
            const auto replay_result = descriptor_spool.beginReplay(pass + 1);
            panic_if(
                replay_result != BoundedDescriptorSpool::Result::Accepted,
                "I[%d] descriptor pass %d cannot start next replay: %s\n",
                my_indirect_id, pass,
                BoundedDescriptorSpool::resultName(replay_result));
            descriptor_spool_replay_active = true;
            DPRINTF(MAAVirtualTrace,
                    "event=descriptor_spool_replay_begin schema=2 unit=%d "
                    "operation_tick=%lu pass=%d population=%u lines=%u "
                    "mode=demand previous_pass=%d\n",
                    my_indirect_id, my_decode_start_tick, pass + 1,
                    descriptor_spool.population(pass + 1),
                    descriptor_spool.passLines(pass + 1), pass);
        }
    } else if (!descriptor_spool.configured() &&
        maa->virtual_index_range_policy == 3 &&
        !direct_index_iteration_fallback &&
        pass + 1 < direct_index_partitions) {
        const auto replay_result = bounded_grow_plan.beginReplay();
        panic_if(replay_result != BoundedGrowPassPlan::Result::Accepted,
                 "I[%d] bounded grow pass %d cannot start next replay: %s\n",
                 my_indirect_id, pass,
                 BoundedGrowPassPlan::resultName(replay_result));
    }
}
void IndirectAccessUnit::discardDirectIndex(
    int itr, uint32_t expected_value, DirectIndexDiscardReason reason) {
    if (descriptor_spool_replay_active) {
        panic_if(!descriptor_spool_current_valid ||
                     descriptor_spool_current_cursor !=
                         static_cast<uint32_t>(itr) ||
                     descriptor_spool_current_descriptor.value !=
                         expected_value ||
                     descriptor_spool_current_word.phase !=
                         direct_index_phase,
                 "I[%d] descriptor cursor %d changed before discard\n",
                 my_indirect_id, itr);
        panic_if(reason == DirectIndexDiscardReason::SummaryObserved,
                 "I[%d] replay descriptor cannot be a summary word\n",
                 my_indirect_id);
        DPRINTF(MAAVirtualTrace,
                "event=index_feeder_discard unit=%d itr=%d logical_itr=%u "
                "value=%u reason=descriptor_replay private=scalar_decode\n",
                my_indirect_id, itr,
                descriptor_spool_current_descriptor.iteration,
                expected_value);
        descriptor_spool_current_valid = false;
        descriptor_spool_current_descriptor = {};
        descriptor_spool_current_word = {};
        releaseDescriptorSpoolReadLines(itr + 1);
        return;
    }
    auto word = direct_index_words.find(itr);
    panic_if(word == direct_index_words.end(),
             "I[%d] streamed index %d cannot be consumed\n",
             my_indirect_id, itr);
    panic_if(word->second.value != expected_value,
             "I[%d] streamed index %d changed from %u to %u before discard\n",
             my_indirect_id, itr, expected_value, word->second.value);
    panic_if(word->second.phase != direct_index_phase,
             "I[%d] stale streamed index %d phase %u (expected %u)\n",
             my_indirect_id, itr, word->second.phase, direct_index_phase);
    const Addr line_addr = word->second.line_addr;

    // direct_index_words is a private feeder copy populated from the B memory
    // stream.  For a selected iteration, the Row/Offset insertion has already
    // retained the A cache-line address, logical destination iteration, and
    // response word ID.  Poison only this private copy before erasing it.  The
    // architectural index memory and the ordinary SPD index-tile path are
    // deliberately outside this operation.
    constexpr uint32_t feeder_poison = 0xd15ca4dU;
    const char *reason_name = nullptr;
    uint32_t observed_poison = 0;
    switch (reason) {
      case DirectIndexDiscardReason::DescriptorInserted:
        word->second.value = feeder_poison;
        observed_poison = word->second.value;
        panic_if(observed_poison != feeder_poison,
                 "I[%d] streamed index %d private poison did not stick\n",
                 my_indirect_id, itr);
        reason_name = "descriptor_inserted";
        break;
      case DirectIndexDiscardReason::PredicateRejected:
        reason_name = "predicate_rejected";
        break;
      case DirectIndexDiscardReason::PartitionRejected:
        reason_name = "partition_rejected";
        break;
      case DirectIndexDiscardReason::SummaryObserved:
        reason_name = "summary_observed";
        break;
    }
    panic_if(reason_name == nullptr,
             "I[%d] streamed index %d has an invalid discard reason\n",
             my_indirect_id, itr);
    DPRINTF(MAAVirtualTrace,
            "event=index_feeder_discard unit=%d itr=%d value=%u "
            "poisoned=%d poison=0x%x reason=%s "
            "private=direct_index_words\n",
            my_indirect_id, itr, expected_value,
            reason == DirectIndexDiscardReason::DescriptorInserted
                ? 1
                : 0,
            observed_poison,
            reason_name);
    direct_index_words.erase(word);
    auto line = direct_index_ready_lines.find(line_addr);
    panic_if(line == direct_index_ready_lines.end() || line->second <= 0,
             "I[%d] streamed index %d has no ready line 0x%lx\n",
             my_indirect_id, itr, line_addr);
    if (--line->second == 0)
        direct_index_ready_lines.erase(line);
}
bool IndirectAccessUnit::receiveDirectIndex(Addr addr, uint8_t *dataptr,
                                            bool is_block_cached) {
    if (!isDirectIndexLoad())
        return false;
    auto pending = direct_index_pending_lines.find(addr);
    if (pending == direct_index_pending_lines.end())
        return false;
    accountReadResponse(addr, is_block_cached);
    if (isVirtualLoad())
        macro_b_last_response_tick = curTick();
    const auto *words = reinterpret_cast<const uint32_t *>(dataptr);
    const auto pending_words = std::move(pending->second);
    direct_index_pending_lines.erase(pending);
    panic_if(!direct_index_ready_lines.emplace(
                  addr, static_cast<int>(pending_words.size())).second,
             "I[%d] duplicate ready direct-index line 0x%lx\n",
             my_indirect_id, addr);
    for (const auto &[itr, wid] : pending_words) {
        panic_if(wid >= block_size / sizeof(uint32_t),
                 "I[%d] invalid streamed-index word %u\n",
                 my_indirect_id, wid);
        panic_if(!direct_index_words
                      .emplace(itr, DirectIndexWord{
                                        words[wid], addr,
                                        addr + wid * sizeof(uint32_t),
                                        direct_index_phase,
                                        static_cast<uint32_t>(itr)})
                      .second,
                 "I[%d] duplicate streamed index %d\n",
                 my_indirect_id, itr);
    }
    (*maa->stats.IND_VirtIndexWords[my_indirect_id]) +=
        pending_words.size();
    DPRINTF(MAAVirtualTrace,
            "event=index_line_response schema=2 unit=%d occurrence=%lu "
            "operation_tick=%lu line=0x%lx "
            "words=%d cached=%d\n",
            my_indirect_id, attribution_event_occurrence++,
            my_decode_start_tick, addr,
            static_cast<int>(pending_words.size()),
            is_block_cached);
    direct_index_max_lines = std::max(
        direct_index_max_lines,
        static_cast<int>(direct_index_pending_lines.size() +
                         direct_index_ready_lines.size()));
    direct_index_max_words = std::max(
        direct_index_max_words, static_cast<int>(direct_index_words.size()));
    scheduleNextExecution(true);
    return true;
}
bool IndirectAccessUnit::receiveDescriptorSpool(
    Addr addr, uint8_t *dataptr, bool is_block_cached)
{
    auto pending = std::find_if(
        descriptor_spool_read_slots.begin(),
        descriptor_spool_read_slots.end(),
        [addr](const auto &slot) {
            return slot.valid && slot.paddr == addr;
        });
    if (pending == descriptor_spool_read_slots.end())
        return false;
    panic_if(!descriptor_spool_replay_active,
             "I[%d] received descriptor line outside replay\n",
             my_indirect_id);
    accountReadResponse(addr, is_block_cached);
    panic_if(pending->responded,
             "I[%d] duplicate descriptor response line %u\n",
             my_indirect_id, pending->line);
    panic_if(pending->pass != static_cast<uint32_t>(direct_index_partition),
             "I[%d] descriptor response pass %u is stale (current=%d)\n",
             my_indirect_id, pending->pass, direct_index_partition);
    const auto response = descriptor_spool.recordReadResponse(
        pending->pass, pending->line);
    panic_if(response != BoundedDescriptorSpool::Result::Accepted,
             "I[%d] descriptor response failed: %s\n", my_indirect_id,
             BoundedDescriptorSpool::resultName(response));
    std::memcpy(pending->data.data(), dataptr,
                BoundedDescriptorSpool::LineBytes);
    pending->responded = true;
    if (pending->read_ahead) {
        // Promotion is not demand.  A response can therefore arrive after
        // promotion and still avoid the first descriptor demand for its line.
        pending->ready_before_demand = !pending->demand_observed;
        descriptor_spool_next_pass_read_responses++;
    }
    DPRINTF(MAAVirtualTrace,
            "event=descriptor_spool_read_response schema=2 unit=%d "
            "operation_tick=%lu pass=%u line=%u paddr=0x%lx "
            "payload_bytes=%u cached=%d mode=%s before_demand=%d\n",
            my_indirect_id, my_decode_start_tick, pending->pass,
            pending->line, addr,
            descriptor_spool.passPayloadLineBytes(
                pending->pass, pending->line),
            is_block_cached,
            pending->read_ahead ? "next_pass_read_ahead" : "demand",
            pending->read_ahead && pending->ready_before_demand);
    direct_index_max_lines = std::max(
        direct_index_max_lines,
        static_cast<int>(descriptorSpoolReadSlotsUsed()));
    scheduleNextExecution(true);
    return true;
}
void IndirectAccessUnit::checkTileReady() {
    // Check if any of the source tiles are ready
    // Set my_max to the size of the ready tile
    if (my_cond_tile != -1) {
        if (maa->spd->getTileStatus(my_cond_tile) == SPD::TileStatus::Finished) {
            my_cond_tile_ready = true;
            if (my_max == -1) {
                my_max = maa->spd->getSize(my_cond_tile);
                DPRINTF(MAAIndirect, "I[%d] %s: my_max = cond size (%d)!\n", my_indirect_id, __func__, my_max);
            }
            panic_if(maa->spd->getSize(my_cond_tile) != my_max, "I[%d] %s: cond size (%d) != max (%d)!\n", my_indirect_id, __func__, maa->spd->getSize(my_cond_tile), my_max);
        }
    }
    if (isDirectIndexLoad()) {
        my_idx_tile_ready = true;
    } else if (maa->spd->getTileStatus(my_idx_tile) ==
               SPD::TileStatus::Finished) {
        my_idx_tile_ready = true;
        if (my_max == -1) {
            my_max = maa->spd->getSize(my_idx_tile);
            DPRINTF(MAAIndirect, "I[%d] %s: my_max = idx size (%d)!\n", my_indirect_id, __func__, my_max);
        }
        panic_if(maa->spd->getSize(my_idx_tile) != my_max, "I[%d] %s: idx size (%d) != max (%d)!\n", my_indirect_id, __func__, maa->spd->getSize(my_idx_tile), my_max);
    }
    if (my_instruction->opcode != Instruction::OpcodeType::INDIR_LD &&
        my_instruction->opcode !=
            Instruction::OpcodeType::INDIR_LD_INDEX &&
        !isSoaJitRmw() &&
        !isVirtualLoad() &&
        my_instruction->opcode !=
            Instruction::OpcodeType::INDIR_ST_SCALAR &&
        my_instruction->opcode !=
            Instruction::OpcodeType::INDIR_RMW_SCALAR &&
        maa->spd->getTileStatus(my_src_tile) == SPD::TileStatus::Finished) {
        my_src_tile_ready = true;
    }
}
bool IndirectAccessUnit::checkElementReady() {
    bool idx_ready = isDirectIndexLoad()
        ? ensureDirectIndex(my_i)
        : maa->spd->getElementFinished(
              my_idx_tile, my_i, 4,
              (uint8_t)FuncUnitType::INDIRECT, my_indirect_id);
    int operand_itr = my_i;
    if (idx_ready && descriptor_spool_replay_active) {
        operand_itr = currentDirectIndexWord(my_i).logical_itr;
    }
    // A row/offset pressure retry has already consumed this predicate.  Its
    // exact source identity must still be at the cursor before it can bypass
    // the feeder: a different direct-index replay word must never inherit the
    // latched condition or evade a normal predicate lookup/accounting.
    bool soa_jit_latched_retry = false;
    if (isSoaJitRmw() && soa_jit_retry_valid) {
        panic_if(!soa_jit_retry_condition ||
                     soa_jit_retry_ordinal < 0 ||
                     soa_jit_retry_ordinal >= my_max ||
                     my_i != soa_jit_retry_ordinal ||
                     operand_itr != soa_jit_retry_ordinal ||
                     operand_itr != my_i,
                 "I[%d] SoA/JIT latched pressure retry identity changed "
                 "cursor=%d operand=%d retry=%d\\n",
                 my_indirect_id, my_i, operand_itr,
                 soa_jit_retry_ordinal);
        soa_jit_latched_retry = true;
    }
    bool cond_ready = isSoaJitRmw()
        ? (!idx_ready || soa_jit_latched_retry ||
           ensureSoaPredicate(operand_itr))
        : (my_cond_tile == -1 || !idx_ready ||
           maa->spd->getElementFinished(
               my_cond_tile, operand_itr, 4,
               (uint8_t)FuncUnitType::INDIRECT, my_indirect_id));
    idx_ready = idx_ready && cond_ready;
    bool src_ready = idx_ready &&
        (isSoaJitRmw() ||
        (my_instruction->opcode == Instruction::OpcodeType::INDIR_LD ||
         my_instruction->opcode ==
             Instruction::OpcodeType::INDIR_LD_INDEX ||
         isVirtualLoad() ||
         my_instruction->opcode ==
             Instruction::OpcodeType::INDIR_RMW_SCALAR ||
         my_instruction->opcode ==
             Instruction::OpcodeType::INDIR_ST_SCALAR ||
             maa->spd->getElementFinished(
             my_src_tile, operand_itr, my_word_size,
             (uint8_t)FuncUnitType::INDIRECT, my_indirect_id)));
    if (!cond_ready) {
        DPRINTF(MAAIndirect,
                "I[%d] %s: cond tile[%d] element[%d] not ready, "
                "returning!\n",
                my_indirect_id, __func__, my_cond_tile, my_i);
    } else if (!idx_ready) {
        DPRINTF(MAAIndirect,
                "I[%d] %s: idx tile[%d] element[%d] not ready, "
                "returning!\n",
                my_indirect_id, __func__, my_idx_tile, my_i);
    } else if (!src_ready) {
        // TODO: this is too early to check src_ready, check it in other stages
        DPRINTF(MAAIndirect,
                "I[%d] %s: src tile[%d] element[%d] not ready, "
                "returning!\n",
                my_indirect_id, __func__, my_src_tile, my_i);
    }
    if (!cond_ready || !idx_ready || !src_ready) {
        return false;
    }
    return true;
}
bool IndirectAccessUnit::checkReadyForFinish() {
    if (my_cond_tile_ready == false) {
        DPRINTF(MAAIndirect, "I[%d] %s: cond tile[%d] not ready, returning!\n", my_indirect_id, __func__, my_cond_tile);
        // Just a fake access to callback INDIRECT when the condition is ready
        maa->spd->getElementFinished(my_cond_tile, my_i, 4, (uint8_t)FuncUnitType::INDIRECT, my_indirect_id);
        return false;
    } else if (my_idx_tile_ready == false) {
        DPRINTF(MAAIndirect, "I[%d] %s: idx tile[%d] not ready, returning!\n", my_indirect_id, __func__, my_idx_tile);
        // Just a fake access to callback INDIRECT when the idx is ready
        maa->spd->getElementFinished(my_idx_tile, my_i, 4, (uint8_t)FuncUnitType::INDIRECT, my_indirect_id);
        return false;
    } else if (my_src_tile_ready == false) {
        DPRINTF(MAAIndirect, "I[%d] %s: src tile[%d] not ready, returning!\n", my_indirect_id, __func__, my_src_tile);
        // Just a fake access to callback INDIRECT when the src is ready
        maa->spd->getElementFinished(my_src_tile, my_i, my_word_size, (uint8_t)FuncUnitType::INDIRECT, my_indirect_id);
        return false;
    }
    return true;
}
void IndirectAccessUnit::fillRowTable(
    bool &finished, bool &waitForFinish, bool &waitForElement,
    bool &needDrain, int &num_spd_read_condidx_accesses,
    int &num_rowtable_accesses, int &num_direct_index_filter_words) {
    finished = false;
    waitForFinish = false;
    waitForElement = false;
    needDrain = false;
    num_spd_read_condidx_accesses = 0;
    num_rowtable_accesses = 0;
    num_direct_index_filter_words = 0;
    if (offset_table_drain) {
        if (offset_table->occupancy() != 0) {
            needDrain = true;
            return;
        }
        offset_table_drain = false;
    }
    checkTileReady();
    while (true) {
        const int feeder_limit = descriptor_spool_replay_active
            ? static_cast<int>(descriptor_spool.population(
                  direct_index_partition))
            : my_max;
        if (feeder_limit != -1 && my_i >= feeder_limit) {
            if (direct_index_summary_active) {
                finishAdaptiveSummary();
                waitForElement = true;
                break;
            }
            if (descriptor_spool_bucket_active) {
                finishDescriptorSpoolBucketing();
                waitForElement = true;
                break;
            }
            if (maa->virtual_bounded_global_merge &&
                bounded_global_merge_phase ==
                    BoundedGlobalMergePhase::Materialize) {
                panic_if(bounded_global_merge_run !=
                             static_cast<uint32_t>(direct_index_partition),
                         "I[%d] sorter run %u does not match pass %d\n",
                         my_indirect_id, bounded_global_merge_run,
                         direct_index_partition);
                panic_if(!direct_index_pending_lines.empty() ||
                             descriptorSpoolReadSlotsUsed() != 0 ||
                             descriptor_spool_current_valid ||
                             !direct_index_ready_lines.empty() ||
                             !direct_index_words.empty(),
                         "I[%d] sorter run %u reached its drain with "
                         "buffered descriptor input\n",
                         my_indirect_id, bounded_global_merge_run);
                needDrain = true;
                break;
            }
            if (isVirtualLoad() && isDirectIndexLoad() &&
                direct_index_partition + 1 < direct_index_partitions) {
                panic_if(!direct_index_pending_lines.empty() ||
                             descriptorSpoolReadSlotsUsed() != 0 ||
                             descriptor_spool_current_valid ||
                             !direct_index_ready_lines.empty() ||
                             !direct_index_words.empty(),
                         "I[%d] direct-index partition %d ended with buffered "
                         "index data\n",
                         my_indirect_id, direct_index_partition);
                const int completed_partition = direct_index_partition++;
                panic_if(
                    direct_index_phase ==
                        std::numeric_limits<uint32_t>::max(),
                    "I[%d] direct-index phase token overflow\n",
                    my_indirect_id);
                direct_index_phase++;
                my_i = 0;
                direct_index_next_prefetch_itr = 0;
                direct_index_partition_barrier = true;
                if (descriptor_spool.configured() &&
                    maa->virtual_descriptor_spool_read_ahead) {
                    panic_if(descriptor_spool_demand_wait_active ||
                                 descriptor_spool_read_ahead_active ||
                                 descriptor_spool_prefetch_occupancy != 0 ||
                                 descriptor_spool_prefetch_occupancy_tick != 0,
                             "I[%d] descriptor overlap state survived pass "
                             "%d fill\n", my_indirect_id,
                             completed_partition);
                    if (descriptor_spool_replay_active) {
                        panic_if(descriptor_spool.activeReplayPass() !=
                                     static_cast<uint32_t>(
                                         completed_partition),
                                 "I[%d] descriptor replay pass %u does not "
                                 "match completed fill pass %d\n",
                                 my_indirect_id,
                                 descriptor_spool.activeReplayPass(),
                                 completed_partition);
                        const auto finish = descriptor_spool.finishReplay(
                            completed_partition);
                        panic_if(
                            finish !=
                                BoundedDescriptorSpool::Result::Accepted,
                            "I[%d] descriptor pass %d cannot close before "
                            "read-ahead: %s\n", my_indirect_id,
                            completed_partition,
                            BoundedDescriptorSpool::resultName(finish));
                        descriptor_spool_replay_active = false;
                    } else {
                        panic_if(completed_partition != static_cast<int>(
                                     descriptor_spool.residentPass()),
                                 "I[%d] non-replay completed pass %d is not "
                                 "resident\n", my_indirect_id,
                                 completed_partition);
                    }
                    const auto begin = descriptor_spool.beginReplay(
                        direct_index_partition);
                    panic_if(begin !=
                                 BoundedDescriptorSpool::Result::Accepted,
                             "I[%d] descriptor pass %d cannot open early: "
                             "%s\n", my_indirect_id,
                             direct_index_partition,
                             BoundedDescriptorSpool::resultName(begin));
                    descriptor_spool_replay_active = true;
                    descriptor_spool_read_ahead_active = true;
                    descriptor_spool_overlap_opportunity_recorded = false;
                    descriptor_spool_prefetch_occupancy_tick = curTick();
                    DPRINTF(MAAVirtualTrace,
                            "event=descriptor_spool_replay_begin schema=2 "
                            "unit=%d operation_tick=%lu pass=%d "
                            "population=%u lines=%u mode=next_pass_read_ahead "
                            "previous_pass=%d\n",
                            my_indirect_id, my_decode_start_tick,
                            direct_index_partition,
                            descriptor_spool.population(
                                direct_index_partition),
                            descriptor_spool.passLines(
                                direct_index_partition),
                            completed_partition);
                }
                needDrain = true;
                recordReorderSurvivalDrain(
                    ReorderSurvivalTracker::DrainReason::PartitionBoundary);
                DPRINTF(MAAVirtualTrace,
                        "event=index_partition unit=%d completed=%d next=%d "
                        "total=%d policy=%s\n", my_indirect_id,
                        completed_partition, direct_index_partition,
                        direct_index_partitions,
                        usesBoundedDirectIndexPasses() ? "grow_range" :
                                                        "grow_modulo");
                break;
            }
            if (my_dst_tile != -1) {
                panic_if(feeder_limit != -1 && my_i != feeder_limit,
                         "I[%d] %s: feeder cursor(%d) != limit(%d)!\n",
                         my_indirect_id, __func__, my_i, feeder_limit);
                if (isVirtualLoad())
                    maa->spd->setVirtualSize(my_dst_tile, my_max);
                else
                    maa->spd->setSize(my_dst_tile, my_i);
            }
            if (checkReadyForFinish()) {
                finished = true;
                break;
            } else {
                waitForFinish = true;
                break;
            }
        }
        if (checkElementReady() == false) {
            // Row table parallelism = total #sub-banks. Each bank can be inserted once at a cycle
            // updateLatency(0, num_spd_read_condidx_accesses, 0, num_rowtable_accesses, total_num_RT_subslices);
            waitForElement = true;
            break;
        }
        const DirectIndexWord *direct_word = isDirectIndexLoad()
            ? &currentDirectIndexWord(my_i) : nullptr;
        const int logical_itr = descriptor_spool_replay_active
            ? static_cast<int>(direct_word->logical_itr) : my_i;
        if (isVirtualLoad() && !isDirectIndexLoad() && my_max == -1) {
            my_max = maa->spd->getSizeForReadyElement(
                my_idx_tile, my_i, sizeof(uint32_t));
            DPRINTF(MAAIndirect,
                    "I[%d] %s: my_max = pipelined idx size (%d)!\n",
                    my_indirect_id, __func__, my_max);
        }
        if (my_cond_tile != -1) {
            num_spd_read_condidx_accesses++;
        }
        bool condition_taken;
        if (isSoaJitRmw() && soa_jit_retry_valid) {
            panic_if(logical_itr != soa_jit_retry_ordinal ||
                         my_i != soa_jit_retry_ordinal,
                     "I[%d] SoA/JIT pressure retry changed ordinal "
                     "%d/%d (cursor=%d)\n",
                     my_indirect_id, logical_itr, soa_jit_retry_ordinal,
                     my_i);
            condition_taken = soa_jit_retry_condition;
        } else {
            condition_taken = isSoaJitRmw()
                ? soaPredicateValue(logical_itr)
                : (my_cond_tile == -1 ||
                   maa->spd->getData<uint32_t>(my_cond_tile,
                                               logical_itr) != 0);
        }
        bool direct_index_descriptor_inserted = false;
        bool direct_index_predicate_rejected = false;
        bool direct_index_partition_rejected = false;
        bool commit_grow_ordinal = false;
        bool resident_bucket = false;
        uint32_t grow_ordinal = 0;
        uint32_t grow_ordinal_key = 0;
        const uint32_t direct_index_value = isDirectIndexLoad()
            ? peekDirectIndex(my_i)
            : 0;
        if (isDirectIndexLoad() && !condition_taken)
            direct_index_predicate_rejected = true;
        const bool direct_index_filtering =
            isVirtualLoad() && isDirectIndexLoad() &&
            direct_index_partitions > 1 && !descriptor_spool_replay_active;
        if (direct_index_filtering)
            num_direct_index_filter_words++;
        if (descriptor_spool_bucket_active)
            descriptor_spool_bucket_attempts++;
        bool virtual_iteration_selected = condition_taken;
        if (!condition_taken && maa->virtual_index_descriptor_spool &&
            direct_index_summary_active) {
            constexpr uint32_t predicate_key =
                std::numeric_limits<uint32_t>::max();
            if (!offset_table->observeSummaryKey(predicate_key))
                direct_index_summary_overflow = true;
            (*maa->stats.IND_BoundedSummaryWords[my_indirect_id])++;
            direct_index_summary_next_iteration++;
            discardDirectIndex(
                my_i, direct_index_value,
                DirectIndexDiscardReason::SummaryObserved);
            my_i++;
            continue;
        }
        if (!condition_taken && descriptor_spool_bucket_active) {
            constexpr uint32_t predicate_key =
                std::numeric_limits<uint32_t>::max();
            uint32_t predicate_ordinal = 0;
            const auto ordinal_result =
                bounded_grow_plan.peekReplayOrdinal(
                    predicate_key, predicate_ordinal);
            panic_if(ordinal_result !=
                         BoundedGrowPassPlan::Result::Accepted,
                     "I[%d] predicate bucket has no ordinal: %s\n",
                     my_indirect_id,
                     BoundedGrowPassPlan::resultName(ordinal_result));
            const uint32_t bucket_pass = bounded_grow_plan.passFor(
                predicate_key, predicate_ordinal);
            panic_if(bucket_pass >= descriptor_spool.passes(),
                     "I[%d] predicate ordinal %u has no pass\n",
                     my_indirect_id, predicate_ordinal);
            captureDescriptorIndexPage(
                logical_itr, direct_word->word_paddr);
            if (descriptor_spool.isResidentPass(bucket_pass)) {
                resident_bucket = true;
                virtual_iteration_selected = true;
                grow_ordinal_key = predicate_key;
                grow_ordinal = predicate_ordinal;
                commit_grow_ordinal = true;
            } else {
                if (descriptor_spool.lineReady(bucket_pass, false) &&
                    !flushDescriptorSpoolLine(bucket_pass, false)) {
                    if (direct_index_filtering) {
                        descriptor_spool_filter_retry_inspections++;
                        (*maa->stats
                              .IND_DescriptorSpoolFilterRetryInspections[
                                  my_indirect_id])++;
                        DPRINTF(MAAVirtualTrace,
                                "event=descriptor_spool_filter_retry "
                                "schema=1 unit=%d operation_tick=%lu "
                                "source=predicate_bucket pass=%u itr=%d "
                                "reason=write_credit\n",
                                my_indirect_id, my_decode_start_tick,
                                bucket_pass, logical_itr);
                    }
                    waitForElement = true;
                    break;
                }
                const auto stage_result = descriptor_spool.stage(
                    bucket_pass,
                    BoundedDescriptorSpool::Descriptor{
                        static_cast<uint16_t>(logical_itr),
                        direct_index_value});
                panic_if(stage_result !=
                             BoundedDescriptorSpool::Result::Accepted,
                         "I[%d] predicate descriptor stage itr=%d pass=%u "
                         "failed: %s\n",
                         my_indirect_id, logical_itr, bucket_pass,
                         BoundedDescriptorSpool::resultName(stage_result));
                const auto commit_result =
                    bounded_grow_plan.commitReplayOrdinal(
                        predicate_key, predicate_ordinal);
                panic_if(commit_result !=
                             BoundedGrowPassPlan::Result::Accepted,
                         "I[%d] predicate ordinal %u commit failed: %s\n",
                         my_indirect_id, predicate_ordinal,
                         BoundedGrowPassPlan::resultName(commit_result));
                (*maa->stats.IND_BoundedBucketWords[my_indirect_id])++;
                descriptor_spool_bucket_commits++;
                discardDirectIndex(
                    my_i, direct_index_value,
                    DirectIndexDiscardReason::DescriptorInserted);
                my_i++;
                continue;
            }
        }
        if (!condition_taken && descriptor_spool_replay_active)
            virtual_iteration_selected = true;
        if (condition_taken) {
            uint32_t idx = isDirectIndexLoad()
                ? direct_index_value
                : maa->spd->getData<uint32_t>(my_idx_tile, logical_itr);
            if (!isDirectIndexLoad())
                num_spd_read_condidx_accesses++;
            if (isSoaJitRmw()) {
                const Addr available = my_max_addr - my_min_addr;
                panic_if(my_base_addr < my_min_addr ||
                             my_base_addr >= my_max_addr ||
                             available < static_cast<Addr>(my_word_size) ||
                             my_max_addr - my_base_addr <
                                 static_cast<Addr>(my_word_size) ||
                             static_cast<Addr>(idx) >
                                 (my_max_addr - my_base_addr - my_word_size) /
                                     my_word_size,
                         "I[%d] SoA/JIT A word %u exceeds registered "
                         "range [0x%lx, 0x%lx)\n",
                         my_indirect_id, idx, my_min_addr, my_max_addr);
            }
            Addr vaddr = my_base_addr + my_word_size * idx;
            panic_if(vaddr < my_min_addr || vaddr >= my_max_addr ||
                         static_cast<Addr>(my_word_size) >
                             my_max_addr - vaddr,
                     "I[%d] %s: word [0x%lx, 0x%lx) out of range "
                     "[0x%lx, 0x%lx)!\n",
                     my_indirect_id, __func__, vaddr,
                     vaddr + my_word_size, my_min_addr, my_max_addr);
            Addr block_vaddr = addrBlockAligner(vaddr, block_size);
            panic_if(isSoaJitRmw() &&
                         ((vaddr % my_word_size) != 0 ||
                          vaddr - block_vaddr + my_word_size > block_size),
                     "I[%d] unsafe SoA/JIT A word at line end 0x%lx\n",
                     my_indirect_id, vaddr);
            DPRINTF(MAAIndirect,
                    "I[%d] %s: baseaddr = 0x%lx idx = %u wordsize = %d "
                    "vaddr = 0x%lx!\n",
                    my_indirect_id, __func__, my_base_addr, idx,
                    my_word_size, vaddr);
            Addr paddr = translatePacket(block_vaddr);
            Addr block_paddr = addrBlockAligner(paddr, block_size);
            DPRINTF(MAAIndirect,
                    "I[%d] %s: idx = %u, addr = 0x%lx!\n",
                    my_indirect_id, __func__, idx, block_paddr);
            uint16_t wid = (vaddr - block_vaddr) / my_word_size;
            std::vector<int> addr_vec = maa->map_addr(block_paddr);
            my_RT_idx = getRowTableIdx(
                my_RT_config, addr_vec[ADDR_CHANNEL_LEVEL],
                addr_vec[ADDR_RANK_LEVEL], addr_vec[ADDR_BANKGROUP_LEVEL],
                addr_vec[ADDR_BANK_LEVEL]);
            Addr grow_addr = getGrowAddr(
                my_RT_config, addr_vec[ADDR_BANKGROUP_LEVEL],
                addr_vec[ADDR_BANK_LEVEL], addr_vec[ADDR_ROW_LEVEL]);
            if (direct_index_summary_active) {
                panic_if(grow_addr > std::numeric_limits<uint32_t>::max(),
                         "I[%d] translated grow 0x%lx exceeds bounded "
                         "summary key width\n", my_indirect_id, grow_addr);
                panic_if(my_i != static_cast<int>(
                                     direct_index_summary_next_iteration),
                         "I[%d] adaptive summary replay order changed at "
                         "%d/%u\n", my_indirect_id, my_i,
                         direct_index_summary_next_iteration);
                if (!offset_table->observeSummaryKey(
                        static_cast<uint32_t>(grow_addr)))
                    direct_index_summary_overflow = true;
                (*maa->stats.IND_BoundedSummaryWords[my_indirect_id])++;
                direct_index_summary_next_iteration++;
                discardDirectIndex(
                    my_i, direct_index_value,
                    DirectIndexDiscardReason::SummaryObserved);
                my_i++;
                continue;
            }
            if (descriptor_spool_bucket_active) {
                panic_if(grow_addr > std::numeric_limits<uint32_t>::max(),
                         "I[%d] bucket grow 0x%lx exceeds key width\n",
                         my_indirect_id, grow_addr);
                grow_ordinal_key = static_cast<uint32_t>(grow_addr);
                const auto ordinal_result =
                    bounded_grow_plan.peekReplayOrdinal(
                        grow_ordinal_key, grow_ordinal);
                panic_if(ordinal_result !=
                             BoundedGrowPassPlan::Result::Accepted,
                         "I[%d] bucket grow 0x%lx has no ordinal: %s\n",
                         my_indirect_id, grow_addr,
                         BoundedGrowPassPlan::resultName(ordinal_result));
                const uint32_t bucket_pass = bounded_grow_plan.passFor(
                    grow_ordinal_key, grow_ordinal);
                panic_if(bucket_pass >= descriptor_spool.passes(),
                         "I[%d] bucket grow 0x%lx ordinal %u has no pass\n",
                         my_indirect_id, grow_addr, grow_ordinal);
                captureDescriptorIndexPage(
                    logical_itr, direct_word->word_paddr);
                if (descriptor_spool.isResidentPass(bucket_pass)) {
                    resident_bucket = true;
                    commit_grow_ordinal = true;
                } else {
                    if (descriptor_spool.lineReady(bucket_pass, false) &&
                        !flushDescriptorSpoolLine(bucket_pass, false)) {
                        if (direct_index_filtering) {
                            descriptor_spool_filter_retry_inspections++;
                            (*maa->stats
                                  .IND_DescriptorSpoolFilterRetryInspections[
                                      my_indirect_id])++;
                            DPRINTF(MAAVirtualTrace,
                                    "event=descriptor_spool_filter_retry "
                                    "schema=1 unit=%d operation_tick=%lu "
                                    "source=grow_bucket pass=%u itr=%d "
                                    "reason=write_credit\n",
                                    my_indirect_id, my_decode_start_tick,
                                    bucket_pass, logical_itr);
                        }
                        waitForElement = true;
                        break;
                    }
                    const auto stage_result = descriptor_spool.stage(
                        bucket_pass,
                        BoundedDescriptorSpool::Descriptor{
                            static_cast<uint16_t>(logical_itr),
                            idx});
                    panic_if(stage_result !=
                                 BoundedDescriptorSpool::Result::Accepted,
                             "I[%d] descriptor stage itr=%d pass=%u failed: "
                             "%s\n", my_indirect_id, logical_itr, bucket_pass,
                             BoundedDescriptorSpool::resultName(stage_result));
                    const auto commit_result =
                        bounded_grow_plan.commitReplayOrdinal(
                            grow_ordinal_key, grow_ordinal);
                    panic_if(commit_result !=
                                 BoundedGrowPassPlan::Result::Accepted,
                             "I[%d] bucket ordinal %u commit failed: %s\n",
                             my_indirect_id, grow_ordinal,
                             BoundedGrowPassPlan::resultName(commit_result));
                    (*maa->stats.IND_BoundedBucketWords[my_indirect_id])++;
                    descriptor_spool_bucket_commits++;
                    if (maa->virtual_bounded_global_merge)
                        trackVirtualIteration(logical_itr, true);
                    discardDirectIndex(
                        my_i, direct_index_value,
                        DirectIndexDiscardReason::DescriptorInserted);
                    my_i++;
                    continue;
                }
            }
            const uint64_t bounded_range_key =
                directIndexRangeKey(idx, grow_addr, logical_itr);
            uint32_t selected_pass;
            if (resident_bucket) {
                selected_pass = descriptor_spool.residentPass();
            } else if (descriptor_spool_replay_active) {
                selected_pass = direct_index_partition;
            } else if (usesBoundedDirectIndexPasses() &&
                maa->virtual_index_range_policy == 3) {
                if (direct_index_iteration_fallback) {
                    selected_pass = bounded_range_pass.passForGrow(
                        bounded_range_key);
                } else {
                    grow_ordinal_key = static_cast<uint32_t>(grow_addr);
                    const auto ordinal_result =
                        bounded_grow_plan.peekReplayOrdinal(
                            grow_ordinal_key, grow_ordinal);
                    panic_if(
                        ordinal_result !=
                            BoundedGrowPassPlan::Result::Accepted,
                        "I[%d] grow 0x%lx has no replay ordinal: %s\n",
                        my_indirect_id, grow_addr,
                        BoundedGrowPassPlan::resultName(ordinal_result));
                    commit_grow_ordinal = true;
                    selected_pass = bounded_grow_plan.passFor(
                        grow_ordinal_key, grow_ordinal);
                    panic_if(selected_pass >=
                                 static_cast<uint32_t>(
                                     direct_index_partitions),
                             "I[%d] grow 0x%lx ordinal %u has no bounded "
                             "grow-plan pass\n", my_indirect_id, grow_addr,
                             grow_ordinal);
                }
            } else {
                selected_pass = directIndexPassForGrow(grow_addr);
            }
            virtual_iteration_selected =
                !isVirtualLoad() || !isDirectIndexLoad() ||
                direct_index_partitions == 1 ||
                static_cast<int>(selected_pass) == direct_index_partition;
            if (isDirectIndexLoad() && !virtual_iteration_selected)
                direct_index_partition_rejected = true;
            if (virtual_iteration_selected) {
                if (debug::MAAReorderTrace) {
                    const uint64_t selection_id =
                        (static_cast<uint64_t>(
                             static_cast<uint32_t>(direct_index_partition))
                         << 32) |
                        static_cast<uint32_t>(logical_itr);
                    panic_if(!reorder_survival.select(selection_id),
                             "I[%d] could not record selected descriptor\n",
                             my_indirect_id);
                }
                if (offset_table->occupancy() >=
                    maa->num_offset_table_epoch_entries) {
                    panic_if(resident_bucket,
                             "I[%d] resident population exceeded bounded "
                             "Offset state before its planned 4K closure\n",
                             my_indirect_id);
                    attribution_offset_pressure_events++;
                    DPRINTF(MAAVirtualTrace,
                            "event=indirect_stall schema=2 unit=%d "
                            "occurrence=%lu operation_tick=%lu sequence=%lu "
                            "reason=offset_epoch_full itr=%d "
                            "occupancy=%d limit=%d\n",
                            my_indirect_id, attribution_event_occurrence++,
                            my_decode_start_tick,
                            attribution_execute_sequence - 1, logical_itr,
                            offset_table->occupancy(),
                            maa->num_offset_table_epoch_entries);
                    offset_table_drain = true;
                    needDrain = true;
                    if (isSoaJitRmw())
                        rememberSoaJitPressureRetry(logical_itr,
                                                    condition_taken);
                    recordReorderSurvivalDrain(
                        ReorderSurvivalTracker::DrainReason::OffsetEpochFull);
                    (*maa->stats.IND_NumOTEpochDrain[my_indirect_id])++;
                    if (offset_table->is_full())
                        (*maa->stats.IND_NumOTFull[my_indirect_id])++;
                    break;
                }
                DPRINTF(MAAIndirect,
                        "I[%d] %s: inserting vaddr(0x%lx), paddr(0x%lx), "
                        "MAP(RO: %d, BA: %d, BG: %d, RA: %d, CO: %d, "
                        "CH: %d), grow(0x%lx), itr(%d), idx(%d), wid(%d) "
                        "to T[%d]\n",
                        my_indirect_id, __func__, block_vaddr, block_paddr,
                        addr_vec[ADDR_ROW_LEVEL], addr_vec[ADDR_BANK_LEVEL],
                        addr_vec[ADDR_BANKGROUP_LEVEL],
                        addr_vec[ADDR_RANK_LEVEL], addr_vec[ADDR_COLUMN_LEVEL],
                        addr_vec[ADDR_CHANNEL_LEVEL],
                        grow_addr, logical_itr, idx, wid, my_RT_idx);
                bool first_CL_access;
                attribution_row_insert_attempts++;
                int row_payload = wid;
                if (maa->virtual_bounded_global_merge &&
                    bounded_global_merge.configured()) {
                    static_assert(sizeof(row_payload) == sizeof(idx));
                    std::memcpy(&row_payload, &idx, sizeof(idx));
                }
                bool inserted = RT[my_RT_config][my_RT_idx].insert(
                    grow_addr, block_paddr, logical_itr, row_payload,
                    first_CL_access);
                num_rowtable_accesses++;
                if (!inserted) {
                    panic_if(resident_bucket,
                             "I[%d] resident population exceeded bounded "
                             "RowTable state before its planned 4K closure\n",
                             my_indirect_id);
                    attribution_row_pressure_events++;
                    DPRINTF(MAAVirtualTrace,
                            "event=indirect_stall schema=2 unit=%d "
                            "occurrence=%lu operation_tick=%lu sequence=%lu "
                            "reason=row_table_full itr=%d "
                            "slice=%d grow=0x%lx\n",
                            my_indirect_id, attribution_event_occurrence++,
                            my_decode_start_tick,
                            attribution_execute_sequence - 1, logical_itr,
                            my_RT_idx, grow_addr);
                    needDrain = true;
                    if (isSoaJitRmw())
                        rememberSoaJitPressureRetry(logical_itr,
                                                    condition_taken);
                    recordReorderSurvivalDrain(
                        ReorderSurvivalTracker::DrainReason::RowTableFull);
                    (*maa->stats.IND_NumRTFull[my_indirect_id])++;
                    break;
                } else {
                    attribution_row_insert_successes++;
                    if (isVirtualLoad()) {
                        if (macro_row_first_insert_tick == 0)
                            macro_row_first_insert_tick = curTick();
                        macro_row_last_insert_tick = curTick();
                    }
                    if (debug::MAAReorderTrace)
                        panic_if(!reorder_survival.admit(),
                                 "I[%d] could not record reorder admission\n",
                                 my_indirect_id);
                    if (usesBoundedDirectIndexPasses()) {
                        const auto result =
                            maa->virtual_index_range_policy == 3 &&
                                    !direct_index_iteration_fallback
                            ? bounded_range_pass.recordSelectedAdmission(
                                  logical_itr, direct_index_partition)
                            : bounded_range_pass.recordAdmission(
                                  logical_itr, bounded_range_key,
                                  direct_index_partition);
                        panic_if(
                            result != BoundedRangePassTracker::Result::Accepted,
                            "I[%d] bounded range admission itr=%d grow=0x%lx "
                            "pass=%d failed: %s\n", my_indirect_id,
                            logical_itr,
                            grow_addr, direct_index_partition,
                            BoundedRangePassTracker::resultName(result));
                    }
                    if (isDirectIndexLoad())
                        direct_index_descriptor_inserted = true;
                    if (isDirectIndexLoad()) {
                        const Addr a_paddr =
                            block_paddr + (vaddr - block_vaddr);
                        const bool generation_available =
                            my_instruction->src1LogicalGeneration != 0 ||
                            my_instruction->dst1LogicalGeneration != 0;
                        const uint64_t generation =
                            my_instruction->src1LogicalGeneration != 0
                                ? my_instruction->src1LogicalGeneration
                                : my_instruction->dst1LogicalGeneration;
                        DPRINTF(MAAPhysicalRecordTrace,
                                "schema=dx100.physical_admission.v1 "
                                "event=physical_admission itr=%d "
                                "b_paddr=0x%lx b_value=%u "
                                "a_paddr=0x%lx a_line_paddr=0x%lx "
                                "channel=%d rank=%d bank_group=%d bank=%d "
                                "row=%d column=%d native_slice=%d "
                                "grow_addr=0x%lx wid=%u "
                                "generation_available=%d generation=%lu "
                                "opcode=%d optype=%d if_id=%d cid=%d "
                                "pc=0x%lx operation_tick=%lu "
                                "controller_managed=%d controller_action=%d "
                                "controller_transaction=%lu "
                                "controller_page=%d rt_config=%d "
                                "aperture_slice_begin=0 "
                                "aperture_slice_end=%d aperture_slices=%d "
                                "provenance=direct_index_"
                                "descriptor_admission\n",
                                logical_itr, direct_word->word_paddr,
                                idx,
                                a_paddr, block_paddr,
                                addr_vec[ADDR_CHANNEL_LEVEL],
                                addr_vec[ADDR_RANK_LEVEL],
                                addr_vec[ADDR_BANKGROUP_LEVEL],
                                addr_vec[ADDR_BANK_LEVEL],
                                addr_vec[ADDR_ROW_LEVEL],
                                addr_vec[ADDR_COLUMN_LEVEL], my_RT_idx,
                                grow_addr, wid, generation_available,
                                generation,
                                static_cast<int>(my_instruction->opcode),
                                static_cast<int>(my_instruction->optype),
                                my_instruction->if_id, my_instruction->CID,
                                my_instruction->PC, my_decode_start_tick,
                                my_instruction->controllerManaged,
                                static_cast<int>(
                                    my_instruction->controllerAction),
                                my_instruction->controllerTransactionID,
                                my_instruction->controllerPage, my_RT_config,
                                num_RT_slices[my_RT_config],
                                num_RT_slices[my_RT_config]);
                    }
                    if (usesBoundedSourceResponses())
                        my_RT_req_sent[my_RT_config][my_RT_idx] = false;
                    if (!descriptor_spool_operation && !isSoaJitRmw()) {
                        my_unique_WORD_addrs.insert(vaddr);
                        my_unique_CL_addrs.insert(block_paddr);
                        my_unique_ROW_addrs.insert(
                            grow_addr + my_RT_idx *
                                num_RT_possible_grows[my_RT_config]);
                    }
                    if (!reorder_RT && first_CL_access) {
                        DPRINTF(MAAIndirect,
                                "I[%d] %s: Creating packet for bank[%d], "
                                "addr[0x%lx]!\n",
                                my_indirect_id, __func__, my_RT_idx,
                                block_paddr);
                        my_expected_responses++;
                        recordReorderSurvivalIssue(block_paddr);
                        createReadPacket(
                            block_paddr,
                            getCeiling(num_rowtable_accesses,
                                       total_num_RT_subslices) *
                                rowtable_latency);
                    }
                }
            }
        } else if (my_dst_tile != -1 && !isVirtualLoad()) {
            DPRINTF(MAAIndirect,
                    "I[%d] %s: SPD[%d][%d] = %u (cond not taken)\n",
                    my_indirect_id, __func__, my_dst_tile, logical_itr, 0);
            maa->spd->setFakeData(my_dst_tile, logical_itr, my_word_size);
        }
        // False predicates have no source address to partition.
        // Count them once in partition zero; selected true iterations
        // remain exact-once.
        const bool track_virtual_iteration =
            virtual_iteration_selected ||
            (!condition_taken &&
             (!isDirectIndexLoad() || direct_index_partition == 0));
        if (isVirtualLoad() && track_virtual_iteration &&
            !(maa->virtual_bounded_global_merge &&
              descriptor_spool_replay_active))
            trackVirtualIteration(logical_itr, condition_taken);
        if (isDirectIndexLoad()) {
            if ((descriptor_spool_replay_active || resident_bucket) &&
                !condition_taken) {
                const auto admitted =
                    bounded_range_pass.recordSelectedAdmission(
                        logical_itr, direct_index_partition);
                panic_if(admitted !=
                             BoundedRangePassTracker::Result::Accepted,
                         "I[%d] predicate admission itr=%d pass=%d "
                         "failed: %s\n",
                         my_indirect_id, logical_itr,
                         direct_index_partition,
                         BoundedRangePassTracker::resultName(admitted));
                const auto retired = bounded_range_pass.recordRetirement(
                    logical_itr, direct_index_partition);
                panic_if(retired !=
                             BoundedRangePassTracker::Result::Accepted,
                         "I[%d] predicate retirement itr=%d pass=%d "
                         "failed: %s\n",
                         my_indirect_id, logical_itr,
                         direct_index_partition,
                         BoundedRangePassTracker::resultName(retired));
            }
            if (usesBoundedDirectIndexPasses()) {
                const bool grouped_descriptor =
                    descriptor_spool_replay_active || resident_bucket;
                const auto inspection_result = grouped_descriptor
                    ? bounded_range_pass.recordSelectedInspection(
                          logical_itr, direct_index_partition)
                    : bounded_range_pass.recordInspection(
                          logical_itr, direct_index_partition);
                panic_if(
                    inspection_result !=
                        BoundedRangePassTracker::Result::Accepted,
                    "I[%d] bounded range inspection itr=%d pass=%d "
                    "failed: %s\n", my_indirect_id, logical_itr,
                    direct_index_partition,
                    BoundedRangePassTracker::resultName(
                        inspection_result));
                if (!descriptor_spool_replay_active && !resident_bucket)
                    (*maa->stats.IND_BoundedReplayWords[my_indirect_id])++;
            }
            const int terminal_decisions =
                static_cast<int>(direct_index_descriptor_inserted) +
                static_cast<int>(direct_index_predicate_rejected) +
                static_cast<int>(direct_index_partition_rejected);
            panic_if(terminal_decisions != 1,
                     "I[%d] direct index %d must be discarded after exactly "
                     "one terminal fill decision (inserted=%d predicate=%d "
                     "partition=%d)\n",
                     my_indirect_id, logical_itr,
                     direct_index_descriptor_inserted,
                     direct_index_predicate_rejected,
                     direct_index_partition_rejected);
            if (descriptor_spool_replay_active) {
                const auto consumed = descriptor_spool.recordConsumption(
                    direct_index_partition,
                    BoundedDescriptorSpool::Descriptor{
                        static_cast<uint16_t>(logical_itr),
                        direct_index_value});
                panic_if(consumed !=
                             BoundedDescriptorSpool::Result::Accepted,
                         "I[%d] descriptor consumption failed: %s\n",
                         my_indirect_id,
                         BoundedDescriptorSpool::resultName(consumed));
            }
            if (resident_bucket) {
                const auto resident =
                    descriptor_spool.recordResidentClassification(
                        direct_index_partition,
                        BoundedDescriptorSpool::Descriptor{
                            static_cast<uint16_t>(logical_itr),
                            direct_index_value});
                panic_if(resident !=
                             BoundedDescriptorSpool::Result::Accepted,
                         "I[%d] resident descriptor classification failed: "
                         "%s\n", my_indirect_id,
                         BoundedDescriptorSpool::resultName(resident));
                (*maa->stats.IND_BoundedBucketWords[my_indirect_id])++;
                descriptor_spool_bucket_commits++;
                DPRINTF(MAAVirtualTrace,
                        "event=descriptor_spool_resident_admit schema=1 "
                        "unit=%d operation_tick=%lu pass=%d itr=%d "
                        "condition=%d active=%u limit=%u\n",
                        my_indirect_id, my_decode_start_tick,
                        direct_index_partition, logical_itr,
                        condition_taken,
                        bounded_range_pass.admissionsForPass(
                            direct_index_partition),
                        BoundedDescriptorSpool::MaxActiveDescriptors);
            }
            discardDirectIndex(
                my_i, direct_index_value,
                direct_index_descriptor_inserted
                    ? DirectIndexDiscardReason::DescriptorInserted
                    : direct_index_predicate_rejected
                        ? DirectIndexDiscardReason::PredicateRejected
                        : DirectIndexDiscardReason::PartitionRejected);
            if (isSoaJitRmw())
                discardSoaPredicateIfDone(logical_itr);
            if (commit_grow_ordinal) {
                const auto commit_result =
                    bounded_grow_plan.commitReplayOrdinal(
                        grow_ordinal_key, grow_ordinal);
                panic_if(
                    commit_result != BoundedGrowPassPlan::Result::Accepted,
                    "I[%d] grow ordinal %u commit failed: %s\n",
                    my_indirect_id, grow_ordinal,
                    BoundedGrowPassPlan::resultName(commit_result));
            }
        }
        if (isSoaJitRmw())
            commitSoaJitSourceOrdinal(logical_itr, condition_taken);
        my_i++;
    }
}
void IndirectAccessUnit::chargeDirectIndexFilterLatency(int words) {
    if (words == 0)
        return;
    panic_if(words < 0 || direct_index_partitions <= 1,
             "I[%d] invalid direct-index filter charge: words=%d "
             "partitions=%d\n",
             my_indirect_id, words, direct_index_partitions);
    (*maa->stats.IND_VirtIndexFilterWords[my_indirect_id]) += words;
    if (direct_index_filter_words_per_cycle == 0)
        return;
    const Cycles latency(
        getCeiling(words, direct_index_filter_words_per_cycle));
    if (my_direct_index_filter_finish_tick < curTick())
        my_direct_index_filter_finish_tick = maa->getClockEdge(latency);
    else
        my_direct_index_filter_finish_tick += maa->getCyclesToTicks(latency);
    (*maa->stats.IND_VirtIndexFilterCycles[my_indirect_id]) += latency;
}
bool IndirectAccessUnit::soaJitContextsEmpty() const
{
    return std::all_of(
        soa_jit_contexts.begin(), soa_jit_contexts.end(),
        [](const SoaJitContext &context) {
            return context.state == SoaJitContextState::Free;
        });
}

void
IndirectAccessUnit::observeSoaJitResultPipeline()
{
    std::array<uint8_t, SoaJitResultPipeline::Regions> reads{};
    std::array<uint8_t, SoaJitResultPipeline::Regions> writes{};
    for (size_t index = 0;
         index < static_cast<size_t>(soa_jit_active_contexts); ++index) {
        const size_t region = SoaJitResultPipeline::regionForLine(index);
        panic_if(region >= SoaJitResultPipeline::Regions,
                 "I[%d] SoA/JIT result context %lu exceeds fixed regions\n",
                 my_indirect_id, index);
        if (soa_jit_contexts[index].state ==
            SoaJitContextState::AwaitARead)
            reads[region]++;
        else if (soa_jit_contexts[index].state ==
                 SoaJitContextState::AwaitAWriteResp)
            writes[region]++;
    }
    panic_if(!soa_jit_result_pipeline.observe(curTick(), reads, writes),
             "I[%d] invalid SoA/JIT result-pipeline observation\n",
             my_indirect_id);
}
bool
IndirectAccessUnit::hasLiveSoaJitState() const
{
    return soa_jit_operation_active ||
           (my_instruction != nullptr && my_instruction->isSoaJitRmw()) ||
           !soaPredicateLinesEmpty() ||
           !soaJitContextsEmpty() ||
           soa_jit_old_result_buffer.activeRun() ||
           !soa_jit_old_result_buffer.empty() ||
           !soa_jit_value_coalescer.prefetchComplete();
}

size_t
IndirectAccessUnit::soaJitActiveContextCount() const
{
    return std::count_if(
        soa_jit_contexts.begin(),
        soa_jit_contexts.begin() + soa_jit_active_contexts,
        [](const SoaJitContext &context) {
            return context.state != SoaJitContextState::Free;
        });
}
size_t
IndirectAccessUnit::soaJitLookaheadOccupancy() const
{
    size_t occupancy = 0;
    for (auto context = soa_jit_contexts.begin();
         context != soa_jit_contexts.begin() + soa_jit_active_contexts;
         ++context)
        occupancy += context->lookaheadOccupancy;
    return occupancy;
}

bool
IndirectAccessUnit::soaJitValuePrefetchComplete() const
{
    return isSoaJitScalarRmw() || soa_jit_value_prefetch_credits == 0 ||
           (soa_jit_value_prefetch_cursor.nextLogical ==
                static_cast<uint32_t>(my_max) &&
            soa_jit_value_coalescer.prefetchComplete());
}

bool
IndirectAccessUnit::serviceSoaJitValuePrefetch()
{
    if (isSoaJitScalarRmw() || soa_jit_value_prefetch_credits == 0)
        return false;
    panic_if(!isSoaJitRmw() || soa_jit_generation == 0 || my_max < 0 ||
                 (my_word_size != 4 && my_word_size != 8) ||
                 soa_jit_value_prefetch_cursor.nextLogical >
                     static_cast<uint32_t>(my_max) ||
                 (soa_jit_value_prefetch_cursor.nextLogical == 0 &&
                  (soa_jit_value_prefetch_cursor.lastBlockValid ||
                   soa_jit_value_prefetch_cursor.lastBlockVaddr != 0)) ||
                 (soa_jit_value_prefetch_cursor.nextLogical != 0 &&
                  !soa_jit_value_prefetch_cursor.lastBlockValid) ||
                 soa_jit_value_coalescer.activePrefetchCreditCount() !=
                     static_cast<size_t>(soa_jit_value_prefetch_credits),
             "I[%d] invalid SoA/JIT value-prefetch feeder state\n",
             my_indirect_id);

    bool progressed = false;
    size_t scans = 0;
    // A legal stream has positive element stride and 32- or 64-bit values,
    // so at most sixteen consecutive logical elements share one 64-B line.
    // This static scan cap can discover every active credit without an
    // unbounded same-cycle walk.  A new line is committed only after the
    // exact (generation, VA, PA) reservation succeeds.
    while (soa_jit_value_prefetch_cursor.nextLogical <
               static_cast<uint32_t>(my_max) &&
           scans++ < SoaJitValuePrefetchMaxScans) {
        const uint32_t logical =
            soa_jit_value_prefetch_cursor.nextLogical;
        const int64_t source = soaSourcePosition(logical);
        panic_if(source < 0,
                 "I[%d] negative SoA/JIT prefetch source position %ld\n",
                 my_indirect_id, source);
        const uint64_t byte_offset =
            static_cast<uint64_t>(source) * my_word_size;
        const Addr span = my_backing_max_addr - my_backing_addr;
        panic_if(span < static_cast<Addr>(my_word_size) ||
                     byte_offset > span - my_word_size,
                 "I[%d] SoA/JIT prefetch source %ld exceeds "
                 "[0x%lx, 0x%lx)\n",
                 my_indirect_id, source, my_backing_min_addr,
                 my_backing_max_addr);
        const Addr block_vaddr = addrBlockAligner(
            my_backing_addr + byte_offset, block_size);
        if (soa_jit_value_prefetch_cursor.lastBlockValid &&
            soa_jit_value_prefetch_cursor.lastBlockVaddr == block_vaddr) {
            soa_jit_value_prefetch_cursor.nextLogical++;
            progressed = true;
            continue;
        }

        const Addr block_paddr = addrBlockAligner(
            translatePacket(block_vaddr), block_size);
        const auto result = soa_jit_value_coalescer.reservePrefetch(
            soa_jit_generation, block_vaddr, block_paddr);
        if (result == SoaJitValueCoalescer::PrefetchResult::Full) {
            soa_jit_value_prefetch_credit_stalls++;
            break;
        }
        panic_if(result == SoaJitValueCoalescer::PrefetchResult::Stale ||
                     result ==
                         SoaJitValueCoalescer::PrefetchResult::Invalid,
                 "I[%d] invalid SoA/JIT prefetch owner logical=%u "
                 "vaddr=0x%lx paddr=0x%lx result=%d\n",
                 my_indirect_id, logical, block_vaddr, block_paddr,
                 static_cast<int>(result));

        soa_jit_value_prefetch_cursor.lastBlockVaddr = block_vaddr;
        soa_jit_value_prefetch_cursor.lastBlockValid = true;
        soa_jit_value_prefetch_cursor.nextLogical++;
        progressed = true;
        if (result ==
            SoaJitValueCoalescer::PrefetchResult::AlreadyOwned) {
            soa_jit_value_prefetch_owned++;
            continue;
        }
        panic_if(result != SoaJitValueCoalescer::PrefetchResult::Issue,
                 "I[%d] unknown SoA/JIT prefetch reservation result\n",
                 my_indirect_id);
        soa_jit_value_prefetch_issues++;
        soa_jit_value_prefetch_high_water = std::max<uint64_t>(
            soa_jit_value_prefetch_high_water,
            soa_jit_value_coalescer.prefetchCount());
        createSoaJitReadPacket(block_paddr, rowtable_latency);
        DPRINTF(MAAVirtualTrace,
                "event=soa_jit_value_prefetch_issue schema=1 unit=%d "
                "operation_tick=%lu generation=%lu logical=%u "
                "vaddr=0x%lx paddr=0x%lx occupancy=%lu credits=%d\n",
                my_indirect_id, my_decode_start_tick, soa_jit_generation,
                logical, block_vaddr, block_paddr,
                soa_jit_value_coalescer.prefetchCount(),
                soa_jit_value_prefetch_credits);
    }
    return progressed;
}

void
IndirectAccessUnit::rememberSoaJitPressureRetry(
    int logical_itr, bool condition_taken)
{
    panic_if(!isSoaJitRmw() || !condition_taken || logical_itr != my_i ||
                 logical_itr < 0 || logical_itr >= my_max ||
                 static_cast<uint64_t>(logical_itr) !=
                     soa_jit_next_source_ordinal ||
                 soa_jit_epoch_drained || soa_jit_all_rows_claimed,
             "I[%d] invalid SoA/JIT pressure retry at logical=%d "
             "cursor=%d next=%lu\n",
             my_indirect_id, logical_itr, my_i,
             soa_jit_next_source_ordinal);
    if (soa_jit_retry_valid) {
        panic_if(soa_jit_retry_ordinal != logical_itr ||
                     soa_jit_retry_condition != condition_taken,
                 "I[%d] SoA/JIT pressure retry identity changed\n",
                 my_indirect_id);
        return;
    }
    soa_jit_retry_valid = true;
    soa_jit_retry_condition = condition_taken;
    soa_jit_retry_ordinal = logical_itr;
}

void
IndirectAccessUnit::commitSoaJitSourceOrdinal(
    int logical_itr, bool condition_taken)
{
    panic_if(!isSoaJitRmw() || logical_itr < 0 || logical_itr >= my_max ||
                 logical_itr != my_i ||
                 static_cast<uint64_t>(logical_itr) !=
                     soa_jit_next_source_ordinal,
             "I[%d] SoA/JIT source ordinal duplicate/skip at logical=%d "
             "cursor=%d next=%lu\n",
             my_indirect_id, logical_itr, my_i,
             soa_jit_next_source_ordinal);
    if (soa_jit_retry_valid) {
        panic_if(soa_jit_retry_ordinal != logical_itr ||
                     soa_jit_retry_condition != condition_taken,
                 "I[%d] SoA/JIT committed a different pressure retry\n",
                 my_indirect_id);
        soa_jit_retry_valid = false;
        soa_jit_retry_condition = false;
        soa_jit_retry_ordinal = -1;
    }
    if (condition_taken)
        soa_jit_selected++;
    else
        soa_jit_predicate_rejected++;
    soa_jit_next_source_ordinal++;
}

void
IndirectAccessUnit::resetSoaJitEpochTables()
{
    panic_if(!isSoaJitRmw() || !soa_jit_epoch_drained ||
                 !soaJitContextsEmpty() ||
                 offset_table->occupancy() != 0 ||
                 soa_jit_epoch_resume_i != my_i ||
                 static_cast<uint64_t>(my_i) !=
                     soa_jit_next_source_ordinal ||
                 !soa_jit_retry_valid ||
                 soa_jit_retry_ordinal != my_i,
             "I[%d] SoA/JIT epoch cannot reset at cursor=%d next=%lu "
             "resume=%d occupancy=%d retry=%d/%d\n",
             my_indirect_id, my_i, soa_jit_next_source_ordinal,
             soa_jit_epoch_resume_i, offset_table->occupancy(),
             soa_jit_retry_valid, soa_jit_retry_ordinal);
    offset_table->check_reset();
    for (int slice = 0; slice < num_RT_slices[my_RT_config]; ++slice) {
        RT[my_RT_config][slice].check_reset();
        RT[my_RT_config][slice].reset();
        my_RT_req_sent[my_RT_config][slice] = false;
    }
}

bool IndirectAccessUnit::serviceSoaJitBuild()
{
    panic_if(!isSoaJitRmw() || soa_jit_epoch_drained ||
                 soa_jit_all_rows_claimed,
             "I[%d] invalid SoA/JIT build state\n", my_indirect_id);
    auto context = std::find_if(
        soa_jit_contexts.begin(),
        soa_jit_contexts.begin() + soa_jit_active_contexts,
        [](const SoaJitContext &candidate) {
            return candidate.state == SoaJitContextState::Free;
        });
    if (context ==
        soa_jit_contexts.begin() + soa_jit_active_contexts) {
        soa_jit_context_stalls++;
        return false;
    }
    for (int rt_idx = 0; rt_idx < num_RT_slices[my_RT_config]; ++rt_idx) {
        Addr addr = 0;
        int head = -1;
        int words = 0;
        if (!RT[my_RT_config][rt_idx].claim_entry_send(
                addr, head, words, true, false, true))
            continue;
        panic_if(head < 0 || words <= 0,
                 "I[%d] SoA/JIT claimed an empty A line\n",
                 my_indirect_id);
        panic_if(std::any_of(
                     soa_jit_contexts.begin(),
                     soa_jit_contexts.begin() + soa_jit_active_contexts,
                     [addr](const SoaJitContext &active) {
                         return active.state != SoaJitContextState::Free &&
                                active.aPaddr == addr;
                     }),
                 "I[%d] SoA/JIT claimed duplicate active A line 0x%lx\n",
                 my_indirect_id, addr);
        *context = SoaJitContext();
        context->aPaddr = addr;
        context->generation = soa_jit_generation;
        context->nextOffset = head;
        context->issueOffset = head;
        context->remaining = words;
        context->state = SoaJitContextState::AwaitARead;
        observeSoaJitResultPipeline();
        const size_t context_index = std::distance(
            soa_jit_contexts.begin(), context);
        soa_jit_context_high_water = std::max<uint64_t>(
            soa_jit_context_high_water,
            soaJitActiveContextCount());
        soa_jit_a_read_issues++;
        recordReorderSurvivalIssue(addr);
        createReadPacket(addr, rowtable_latency);
        DPRINTF(MAAVirtualTrace,
                "event=soa_jit_a_read_issue schema=1 unit=%d "
                "operation_tick=%lu generation=%lu addr=0x%lx "
                "head=%d aliases=%d contexts=%lu active_limit=%d\n",
                my_indirect_id, my_decode_start_tick, soa_jit_generation,
                addr, head, words, soaJitActiveContextCount(),
                soa_jit_active_contexts);
        if (soa_jit_pre_a_value_lookahead)
            fillSoaJitLookahead(context_index);
        return true;
    }
    if (my_fill_finished) {
        panic_if(soa_jit_retry_valid ||
                     soa_jit_next_source_ordinal !=
                         static_cast<uint64_t>(my_max),
                 "I[%d] SoA/JIT final rows claimed before exact source "
                 "closure (%lu/%d retry=%d)\n",
                 my_indirect_id, soa_jit_next_source_ordinal, my_max,
                 soa_jit_retry_valid);
        soa_jit_all_rows_claimed = true;
        DPRINTF(MAAVirtualTrace,
                "event=soa_jit_all_rows_claimed schema=1 unit=%d "
                "operation_tick=%lu generation=%lu cursor=%d "
                "selected=%lu rejected=%lu epoch_drains=%lu\n",
                my_indirect_id, my_decode_start_tick, soa_jit_generation,
                my_i, soa_jit_selected, soa_jit_predicate_rejected,
                soa_jit_epoch_drains);
    } else {
        panic_if(!soa_jit_retry_valid ||
                     soa_jit_retry_ordinal != my_i ||
                     soa_jit_next_source_ordinal ==
                         soa_jit_epoch_start_ordinal ||
                     soa_jit_epoch_drains ==
                         std::numeric_limits<uint64_t>::max(),
                 "I[%d] SoA/JIT pressure epoch made no bounded progress "
                 "at cursor=%d start=%lu retry=%d/%d\n",
                 my_indirect_id, my_i, soa_jit_epoch_start_ordinal,
                 soa_jit_retry_valid, soa_jit_retry_ordinal);
        soa_jit_epoch_drained = true;
        soa_jit_epoch_resume_i = my_i;
        soa_jit_epoch_drains++;
        DPRINTF(MAAVirtualTrace,
                "event=soa_jit_epoch_rows_claimed schema=1 unit=%d "
                "operation_tick=%lu generation=%lu epoch=%lu cursor=%d "
                "epoch_begin=%lu selected=%lu rejected=%lu "
                "offset_occupancy=%d contexts=%lu\n",
                my_indirect_id, my_decode_start_tick, soa_jit_generation,
                soa_jit_epoch_drains, my_i, soa_jit_epoch_start_ordinal,
                soa_jit_selected, soa_jit_predicate_rejected,
                offset_table->occupancy(), soaJitActiveContextCount());
    }
    return false;
}
bool
IndirectAccessUnit::issueSoaJitScalar(
    size_t context_index, size_t slot_index, int offset)
{
    panic_if(!isSoaJitScalarRmw() ||
                 context_index >=
                     static_cast<size_t>(soa_jit_active_contexts) ||
                 slot_index >= SoaJitValueCoalescer::MaxLookahead,
             "I[%d] invalid scalar-broadcast lookahead owner %lu/%lu\n",
             my_indirect_id, context_index, slot_index);
    SoaJitContext &context = soa_jit_contexts[context_index];
    SoaJitLookaheadSlot &slot = context.lookahead[slot_index];
    const bool pre_a = context.state == SoaJitContextState::AwaitARead;
    panic_if(context.generation != soa_jit_generation ||
                 (context.state != SoaJitContextState::Active &&
                  !(soa_jit_pre_a_value_lookahead && pre_a)) ||
                 slot.state != SoaJitLookaheadState::Free || offset < 0 ||
                 context.issueOffset != offset || context.remaining <= 0 ||
                 !soa_jit_scalar_broadcast.valid() ||
                 soa_jit_scalar_broadcast.valueBytes() !=
                     static_cast<size_t>(my_word_size),
             "I[%d] invalid captured scalar lookahead state\n",
             my_indirect_id);
    const OffsetTableEntry entry = offset_table->peek_entry(offset);
    slot = SoaJitLookaheadSlot();
    slot.generation = soa_jit_generation;
    slot.offset = offset;
    slot.logicalItr = entry.itr;
    slot.aWord = static_cast<uint16_t>(entry.wid);
    std::memcpy(slot.value.data(), soa_jit_scalar_broadcast.data(),
                my_word_size);
    slot.state = SoaJitLookaheadState::Ready;
    context.issueOffset = entry.next_itr;
    context.lookaheadOccupancy++;
    soa_jit_lookahead_issues++;
    soa_jit_lookahead_responses++;
    soa_jit_value_deliveries++;
    if (pre_a)
        soa_jit_pre_a_value_issues++;
    soa_jit_lookahead_high_water = std::max<uint64_t>(
        soa_jit_lookahead_high_water, soaJitLookaheadOccupancy());
    DPRINTF(MAAVirtualTrace,
            "event=soa_jit_scalar_capture schema=1 unit=%d "
            "operation_tick=%lu generation=%lu context=%lu slot=%lu "
            "offset=%d logical_itr=%d bytes=%d pre_a=%d\n",
            my_indirect_id, my_decode_start_tick, soa_jit_generation,
            context_index, slot_index, offset, entry.itr, my_word_size,
            pre_a);
    return true;
}

bool
IndirectAccessUnit::issueSoaJitValueRead(
    size_t context_index, size_t slot_index, int offset)
{
    panic_if(context_index >=
                 static_cast<size_t>(soa_jit_active_contexts) ||
                 slot_index >= SoaJitValueCoalescer::MaxLookahead,
             "I[%d] invalid SoA/JIT lookahead owner %lu/%lu\n",
             my_indirect_id, context_index, slot_index);
    SoaJitContext &context = soa_jit_contexts[context_index];
    SoaJitLookaheadSlot &slot = context.lookahead[slot_index];
    const bool pre_a =
        context.state == SoaJitContextState::AwaitARead;
    panic_if(context.generation != soa_jit_generation ||
                 (context.state != SoaJitContextState::Active &&
                  !(soa_jit_pre_a_value_lookahead && pre_a)) ||
                 slot.state != SoaJitLookaheadState::Free ||
                 offset < 0 || context.issueOffset != offset ||
                 context.remaining <= 0,
             "I[%d] invalid SoA/JIT value-read context\n",
             my_indirect_id);
    const OffsetTableEntry entry =
        offset_table->peek_entry(offset);
    const int64_t source = soaSourcePosition(entry.itr);
    panic_if(source < 0,
             "I[%d] negative SoA/JIT value position %ld\n",
             my_indirect_id, source);
    const uint64_t byte_offset =
        static_cast<uint64_t>(source) * my_word_size;
    const Addr span = my_backing_max_addr - my_backing_addr;
    panic_if(span < static_cast<Addr>(my_word_size) ||
                 byte_offset > span - my_word_size,
             "I[%d] SoA/JIT value position %ld exceeds "
             "[0x%lx, 0x%lx)\n",
             my_indirect_id, source, my_backing_min_addr,
             my_backing_max_addr);
    const Addr vaddr = my_backing_addr + byte_offset;
    const Addr block_vaddr = addrBlockAligner(vaddr, block_size);
    panic_if((vaddr % my_word_size) != 0 ||
                 vaddr - block_vaddr + my_word_size > block_size,
             "I[%d] unsafe SoA/JIT value word at line end 0x%lx\n",
             my_indirect_id, vaddr);
    const Addr value_paddr = addrBlockAligner(
        translatePacket(block_vaddr), block_size);
    const uint16_t waiter = static_cast<uint16_t>(
        context_index * SoaJitValueCoalescer::MaxLookahead + slot_index);
    const auto request = soa_jit_value_coalescer.requestAlias(
        soa_jit_generation, value_paddr, waiter);
    if (request.result == SoaJitValueCoalescer::AliasResult::Stall) {
        soa_jit_value_stalls++;
        soa_jit_lookahead_stalls++;
        return false;
    }
    panic_if(request.result == SoaJitValueCoalescer::AliasResult::Duplicate ||
                 request.result == SoaJitValueCoalescer::AliasResult::Stale ||
                 request.result == SoaJitValueCoalescer::AliasResult::Invalid,
             "I[%d] invalid SoA/JIT value owner for context=%lu slot=%lu "
             "offset=%d paddr=0x%lx result=%d\n",
             my_indirect_id, context_index, slot_index, offset, value_paddr,
             static_cast<int>(request.result));
    slot = SoaJitLookaheadSlot();
    slot.valuePaddr = value_paddr;
    slot.generation = soa_jit_generation;
    slot.offset = offset;
    slot.logicalItr = entry.itr;
    slot.aWord = static_cast<uint16_t>(entry.wid);
    slot.valueWord = static_cast<uint16_t>(
        (vaddr - block_vaddr) / my_word_size);
    slot.state = SoaJitLookaheadState::Waiting;
    context.issueOffset = entry.next_itr;
    context.lookaheadOccupancy++;
    soa_jit_lookahead_issues++;
    if (pre_a)
        soa_jit_pre_a_value_issues++;
    soa_jit_lookahead_high_water = std::max<uint64_t>(
        soa_jit_lookahead_high_water, soaJitLookaheadOccupancy());
    soa_jit_value_cache_high_water = std::max<uint64_t>(
        soa_jit_value_cache_high_water,
        soa_jit_value_coalescer.cacheOccupancy());
    if (request.evicted)
        soa_jit_value_evictions++;
    const char *action = nullptr;
    if (request.result == SoaJitValueCoalescer::AliasResult::Fill) {
        action = "fill";
        soa_jit_value_read_issues++;
        createSoaJitReadPacket(value_paddr, rowtable_latency);
    } else if (request.result ==
               SoaJitValueCoalescer::AliasResult::Merge) {
        action = "merge";
        soa_jit_value_merged_waiters++;
    } else {
        panic_if(request.result !=
                     SoaJitValueCoalescer::AliasResult::Hit,
                 "I[%d] unknown SoA/JIT value request result\n",
                 my_indirect_id);
        action = "hit";
        soa_jit_value_hits++;
    }
    DPRINTF(MAAVirtualTrace,
            "event=soa_jit_value_request schema=1 unit=%d "
            "operation_tick=%lu generation=%lu context=%lu slot=%lu "
            "offset=%d logical_itr=%d paddr=0x%lx action=%s "
            "cache_occupancy=%lu lookahead=%u/%d pre_a=%d\n",
            my_indirect_id, my_decode_start_tick, soa_jit_generation,
            context_index, slot_index, offset, entry.itr, value_paddr,
            action, soa_jit_value_coalescer.cacheOccupancy(),
            context.lookaheadOccupancy, soa_jit_value_lookahead, pre_a);
    return true;
}
bool
IndirectAccessUnit::fillSoaJitLookahead(size_t context_index)
{
    SoaJitContext &context = soa_jit_contexts[context_index];
    if (context.state != SoaJitContextState::Active &&
        !(soa_jit_pre_a_value_lookahead &&
          context.state == SoaJitContextState::AwaitARead))
        return false;
    bool issued = false;
    while (context.issueOffset != -1 &&
           context.lookaheadOccupancy < soa_jit_value_lookahead) {
        auto slot = std::find_if(
            context.lookahead.begin(), context.lookahead.end(),
            [](const SoaJitLookaheadSlot &candidate) {
                return candidate.state == SoaJitLookaheadState::Free;
            });
        panic_if(slot == context.lookahead.end(),
                 "I[%d] SoA/JIT lookahead occupancy lost a free slot\n",
                 my_indirect_id);
        const size_t slot_index =
            std::distance(context.lookahead.begin(), slot);
        const bool success = isSoaJitScalarRmw()
            ? issueSoaJitScalar(
                  context_index, slot_index, context.issueOffset)
            : issueSoaJitValueRead(
                  context_index, slot_index, context.issueOffset);
        if (!success)
            break;
        issued = true;
    }
    return issued;
}
bool
IndirectAccessUnit::serviceSoaJitLookahead()
{
    bool progressed = false;
    const size_t context_count = soa_jit_active_contexts;
    soa_jit_apply_lane_pool.beginCycle(curTick());
    for (size_t context_index = 0; context_index < context_count;
         ++context_index) {
        progressed = fillSoaJitLookahead(context_index) || progressed;
    }

    size_t deliveries_done = 0;
    const size_t delivery_start = soa_jit_apply_lane_pool.cursor();
    for (size_t turn = 0;
         turn < context_count &&
             deliveries_done < static_cast<size_t>(soa_jit_apply_lanes);
         ++turn) {
        const size_t context_index =
            (delivery_start + turn) % context_count;
        SoaJitContext &context = soa_jit_contexts[context_index];
        if (context.state != SoaJitContextState::Active &&
            !(soa_jit_pre_a_value_lookahead &&
              context.state == SoaJitContextState::AwaitARead))
            continue;
        for (size_t slot_index = 0;
             slot_index < context.lookahead.size(); ++slot_index) {
            SoaJitLookaheadSlot &slot = context.lookahead[slot_index];
            if (slot.state != SoaJitLookaheadState::Waiting)
                continue;
            const uint16_t waiter = static_cast<uint16_t>(
                context_index * SoaJitValueCoalescer::MaxLookahead +
                slot_index);
            SoaJitValueCoalescer::Delivery delivery;
            const auto result = soa_jit_value_coalescer.deliver(
                soa_jit_generation, waiter, curTick(), delivery,
                soa_jit_apply_lanes);
            panic_if(result ==
                         SoaJitValueCoalescer::DeliveryResult::Stale ||
                         result ==
                         SoaJitValueCoalescer::DeliveryResult::Invalid,
                     "I[%d] stale or invalid SoA/JIT value delivery "
                     "context=%lu slot=%lu\n",
                     my_indirect_id, context_index, slot_index);
            if (result ==
                SoaJitValueCoalescer::DeliveryResult::CycleLimited) {
                deliveries_done = soa_jit_apply_lanes;
                break;
            }
            if (result !=
                SoaJitValueCoalescer::DeliveryResult::Delivered)
                continue;
            const size_t byte = slot.valueWord * my_word_size;
            panic_if(byte + my_word_size > delivery.data.size() ||
                         my_word_size > static_cast<int>(slot.value.size()),
                     "I[%d] invalid SoA/JIT delivered word %u/%d\n",
                     my_indirect_id, slot.valueWord, my_word_size);
            std::memcpy(slot.value.data(), delivery.data.data() + byte,
                        my_word_size);
            slot.state = SoaJitLookaheadState::Ready;
            soa_jit_value_deliveries++;
            soa_jit_lookahead_responses++;
            progressed = true;
            deliveries_done++;
            break;
        }
    }

    const size_t apply_start = soa_jit_apply_lane_pool.cursor();
    for (size_t turn = 0; turn < context_count; ++turn) {
        if (soa_jit_apply_lane_pool.currentCycleOccupancy() >=
            soa_jit_apply_lanes)
            break;
        const size_t context_index =
            (apply_start + turn) % context_count;
        SoaJitContext &context = soa_jit_contexts[context_index];
        if (context.state != SoaJitContextState::Active)
            continue;
        auto slot = std::find_if(
            context.lookahead.begin(), context.lookahead.end(),
            [&context](const SoaJitLookaheadSlot &candidate) {
                return candidate.state == SoaJitLookaheadState::Ready &&
                       candidate.offset == context.nextOffset;
            });
        if (slot != context.lookahead.end()) {
            panic_if(slot->generation != soa_jit_generation,
                     "I[%d] stale SoA/JIT ordered alias owner\n",
                     my_indirect_id);
            if (!soa_jit_apply_lane_pool.grant(
                    curTick(), context.generation, context.aPaddr,
                    context_index, context_count))
                continue;
            if (!applySoaJitValue(
                    context, static_cast<uint16_t>(context_index),
                    slot->aWord, static_cast<uint32_t>(slot->logicalItr),
                    slot->value.data())) {
                progressed = true;
                continue;
            }
            const int expected_offset = context.nextOffset;
            const OffsetTableEntry consumed =
                offset_table->consume_entry(context.nextOffset);
            panic_if(consumed.itr != slot->logicalItr ||
                         consumed.wid != slot->aWord ||
                         expected_offset != slot->offset,
                     "I[%d] SoA/JIT alias identity changed at offset %d\n",
                     my_indirect_id, expected_offset);
            context.remaining--;
            context.lookaheadOccupancy--;
            if (context.preAUsesPending != 0) {
                context.preAUsesPending--;
                soa_jit_pre_a_value_uses++;
            }
            *slot = SoaJitLookaheadSlot();
            soa_jit_aliases_applied++;
            soa_jit_apply_lane_high_water = std::max<uint64_t>(
                soa_jit_apply_lane_high_water,
                soa_jit_apply_lane_pool.currentCycleOccupancy());
            progressed = true;
            panic_if(context.remaining < 0,
                     "I[%d] SoA/JIT alias chain exceeded its count\n",
                     my_indirect_id);
            fillSoaJitLookahead(context_index);
        }
    }

    for (size_t context_index = 0; context_index < context_count;
         ++context_index) {
        SoaJitContext &context = soa_jit_contexts[context_index];
        if (context.state != SoaJitContextState::Active)
            continue;
        if (context.remaining == 0) {
            panic_if(context.nextOffset != -1 ||
                         context.issueOffset != -1 ||
                         context.lookaheadOccupancy != 0,
                     "I[%d] SoA/JIT alias chain has incomplete lookahead "
                     "closure\n",
                     my_indirect_id);
            issueSoaJitWrite(context);
            progressed = true;
        }
    }
    return progressed;
}
bool IndirectAccessUnit::applySoaJitValue(
    SoaJitContext &context, uint16_t context_index, uint16_t a_word,
    uint32_t logical_itr, const uint8_t *value)
{
    uint8_t *destination =
        context.aLine.data() + a_word * my_word_size;
    if (isSoaJitOldResultRmw()) {
        const auto capture = soa_jit_old_result_buffer.capture(
            soa_jit_generation, context_index, logical_itr, destination,
            my_word_size);
        if (capture == SoaJitOldResultBuffer::Result::Full ||
            capture ==
                SoaJitOldResultBuffer::Result::LineAwaitingResponse) {
            soa_jit_old_result_stalls++;
            serviceSoaJitOldResultWrites(true);
            return false;
        }
        panic_if(capture != SoaJitOldResultBuffer::Result::Accepted,
                 "I[%d] rejected old-result capture generation=%lu "
                 "context=%u logical=%u result=%u\n",
                 my_indirect_id, soa_jit_generation, context_index,
                 logical_itr, static_cast<unsigned>(capture));
        soa_jit_old_result_captures++;
        serviceSoaJitOldResultWrites(false);
    }
    if (isSoaJitScalarRmw()) {
        panic_if(value == nullptr ||
                     std::memcmp(value, soa_jit_scalar_broadcast.data(),
                                 my_word_size) != 0 ||
                     soa_jit_scalar_broadcast.apply(destination) !=
                         SoaJitScalarBroadcast::Status::Accepted,
                 "I[%d] captured scalar apply rejected\n",
                 my_indirect_id);
        return true;
    }
#define APPLY_SOA_JIT(TYPE) \
    do { \
        TYPE lhs{}; \
        TYPE rhs{}; \
        std::memcpy(&lhs, destination, sizeof(TYPE)); \
        std::memcpy(&rhs, value, sizeof(TYPE)); \
        if (my_instruction->optype == Instruction::OPType::ADD_OP) \
            lhs += rhs; \
        else if (my_instruction->optype == Instruction::OPType::MIN_OP) \
            lhs = lhs < rhs ? lhs : rhs; \
        else if (my_instruction->optype == Instruction::OPType::MAX_OP) \
            lhs = lhs > rhs ? lhs : rhs; \
        else \
            panic("I[%d] invalid SoA/JIT RMW operation\n", \
                  my_indirect_id); \
        std::memcpy(destination, &lhs, sizeof(TYPE)); \
    } while (false)
    switch (my_instruction->datatype) {
      case Instruction::DataType::UINT32_TYPE: APPLY_SOA_JIT(uint32_t); break;
      case Instruction::DataType::INT32_TYPE: APPLY_SOA_JIT(int32_t); break;
      case Instruction::DataType::FLOAT32_TYPE: APPLY_SOA_JIT(float); break;
      case Instruction::DataType::UINT64_TYPE: APPLY_SOA_JIT(uint64_t); break;
      case Instruction::DataType::INT64_TYPE: APPLY_SOA_JIT(int64_t); break;
      case Instruction::DataType::FLOAT64_TYPE: APPLY_SOA_JIT(double); break;
      default: panic("I[%d] invalid SoA/JIT datatype\n", my_indirect_id);
    }
#undef APPLY_SOA_JIT
    return true;
}

bool
IndirectAccessUnit::serviceSoaJitOldResultWrites(bool force_partial)
{
    if (!isSoaJitOldResultRmw())
        return false;
    bool progressed = false;
    SoaJitOldResultBuffer::Request write;
    while (soa_jit_old_result_buffer.issue(&write, force_partial) ==
           SoaJitOldResultBuffer::Result::Accepted) {
        const Addr vaddr = write.identity.lineAddress;
        panic_if(write.payload == nullptr || write.identity.validWords == 0 ||
                     vaddr < my_result_addr || vaddr >= my_result_max_addr ||
                     my_result_max_addr - vaddr < block_size,
                 "I[%d] invalid retained old-result line 0x%lx mask=0x%x\n",
                 my_indirect_id, vaddr, write.identity.validWords);
        const Addr paddr = translatePacket(
            vaddr, BaseMMU::Write, block_size);
        RequestPtr req = std::make_shared<Request>(
            paddr, block_size, flags, maa->requestorId);
        req->setRegion(my_result_addr_range_id);
        std::vector<bool> byte_enable(block_size, false);
        for (size_t word = 0;
             word < SoaJitOldResultBuffer::WordsPerLine; ++word) {
            if ((write.identity.validWords & (1U << word)) == 0)
                continue;
            std::fill(byte_enable.begin() + word * sizeof(float),
                      byte_enable.begin() + (word + 1) * sizeof(float),
                      true);
        }
        req->setByteEnable(byte_enable);
        PacketPtr pkt = new Packet(req, MemCmd::WriteReq);
        pkt->headerDelay = pkt->payloadDelay = 0;
        pkt->dataStatic(const_cast<uint8_t *>(write.payload));
        auto *sender = new SoaJitOldResultSenderState;
        sender->identity = write.identity;
        sender->physicalAddress = paddr;
        pkt->pushSenderState(sender);
        soa_jit_old_result_write_issues++;
        // Keep bypass_deferred_queue at its default false: an exact-address
        // conflict must retain MAA's existing FIFO before this credit retires.
        maa->sendPacket(FuncUnitType::INDIRECT, my_indirect_id, pkt,
                        maa->getClockEdge(Cycles(0)), true, true);
        DPRINTF(MAAVirtualTrace,
                "event=soa_jit_old_result_issue schema=1 unit=%d "
                "operation_tick=%lu generation=%lu sequence=%lu "
                "credit=%u vaddr=0x%lx paddr=0x%lx mask=0x%x\n",
                my_indirect_id, my_decode_start_tick,
                write.identity.generation, write.identity.issueSequence,
                write.identity.credit, vaddr, paddr,
                write.identity.validWords);
        progressed = true;
    }
    return progressed;
}

bool
IndirectAccessUnit::completeSoaJitOldResultWrite(
    const SoaJitOldResultBuffer::Identity &identity)
{
    if (!isSoaJitOldResultRmw())
        return false;
    const auto result = soa_jit_old_result_buffer.acknowledge(identity);
    if (result != SoaJitOldResultBuffer::Result::Accepted)
        return false;
    soa_jit_old_result_write_responses++;
    DPRINTF(MAAVirtualTrace,
            "event=soa_jit_old_result_response schema=1 unit=%d "
            "operation_tick=%lu generation=%lu sequence=%lu credit=%u "
            "vaddr=0x%lx mask=0x%x\n",
            my_indirect_id, my_decode_start_tick, identity.generation,
            identity.issueSequence, identity.credit, identity.lineAddress,
            identity.validWords);
    return true;
}
void IndirectAccessUnit::issueSoaJitWrite(SoaJitContext &context)
{
    panic_if(context.generation != soa_jit_generation ||
                 context.nextOffset != -1 || context.remaining != 0 ||
                 context.preAUsesPending != 0,
             "I[%d] SoA/JIT A write issued before alias drain\n",
             my_indirect_id);
    RequestPtr req = std::make_shared<Request>(
        context.aPaddr, block_size, flags, maa->requestorId);
    req->setRegion(my_addr_range_id);
    PacketPtr pkt = new Packet(req, MemCmd::WriteReq);
    pkt->headerDelay = pkt->payloadDelay = 0;
    pkt->allocate();
    pkt->setData(context.aLine.data());
    const size_t context_index = static_cast<size_t>(
        &context - soa_jit_contexts.data());
    panic_if(context_index >=
                 static_cast<size_t>(soa_jit_active_contexts),
             "I[%d] scalar/JIT write context is outside active geometry\n",
             my_indirect_id);
    auto *sender = new SoaJitWriteSenderState;
    sender->identity = {
        soa_jit_generation, static_cast<uint16_t>(context_index),
        context.aPaddr};
    pkt->pushSenderState(sender);
    context.state = SoaJitContextState::AwaitAWriteResp;
    observeSoaJitResultPipeline();
    soa_jit_a_write_issues++;
    maa->sendPacket(FuncUnitType::INDIRECT, my_indirect_id, pkt,
                    maa->getClockEdge(Cycles(0)), true);
    DPRINTF(MAAVirtualTrace,
            "event=soa_jit_a_write_issue schema=1 unit=%d "
            "operation_tick=%lu generation=%lu addr=0x%lx aliases=%lu\n",
            my_indirect_id, my_decode_start_tick, soa_jit_generation,
            context.aPaddr, soa_jit_aliases_applied);
}
bool IndirectAccessUnit::receiveSoaJitData(
    Addr addr, uint8_t *dataptr, bool is_block_cached)
{
    if (!isSoaJitRmw())
        return false;
    for (size_t context_index = 0;
         context_index < static_cast<size_t>(soa_jit_active_contexts);
         ++context_index) {
        auto &context = soa_jit_contexts[context_index];
        if (context.state == SoaJitContextState::AwaitARead &&
            context.aPaddr == addr) {
            panic_if(context.generation != soa_jit_generation,
                     "I[%d] stale SoA/JIT A response generation\n",
                     my_indirect_id);
            accountReadResponse(addr, is_block_cached);
            std::memcpy(context.aLine.data(), dataptr, block_size);
            soa_jit_a_read_responses++;
            recordReorderSurvivalIssuedEntries(context.remaining);
            panic_if(context.preAUsesPending != 0,
                     "I[%d] retained pre-A use state before A response\n",
                     my_indirect_id);
            if (soa_jit_pre_a_value_lookahead) {
                context.preAUsesPending =
                    context.lookaheadOccupancy;
                soa_jit_pre_a_value_ready_at_a_response +=
                    std::count_if(
                        context.lookahead.begin(),
                        context.lookahead.end(),
                        [](const SoaJitLookaheadSlot &slot) {
                            return slot.state ==
                                SoaJitLookaheadState::Ready;
                        });
            } else {
                panic_if(context.lookaheadOccupancy != 0,
                         "I[%d] disabled pre-A lookahead retained slots\n",
                         my_indirect_id);
            }
            context.state = SoaJitContextState::Active;
            observeSoaJitResultPipeline();
            fillSoaJitLookahead(context_index);
            scheduleNextExecution(true);
            return true;
        }
    }
    Addr prefetch_vaddr = 0;
    const auto response = soa_jit_value_coalescer.acceptResponse(
        soa_jit_generation, addr, dataptr, block_size, &prefetch_vaddr);
    panic_if(response == SoaJitValueCoalescer::ResponseResult::Duplicate ||
                 response ==
                     SoaJitValueCoalescer::ResponseResult::Stale ||
                 response ==
                     SoaJitValueCoalescer::ResponseResult::Unknown ||
                 response ==
                     SoaJitValueCoalescer::ResponseResult::Invalid,
             "I[%d] SoA/JIT received stale/duplicate/unknown value "
             "response 0x%lx result=%d\n",
             my_indirect_id, addr, static_cast<int>(response));
    accountReadResponse(addr, is_block_cached);
    if (response == SoaJitValueCoalescer::ResponseResult::CacheFill) {
        panic_if(prefetch_vaddr != 0,
                 "I[%d] demand value response retained prefetch VA "
                 "0x%lx\n",
                 my_indirect_id, prefetch_vaddr);
        soa_jit_value_read_responses++;
        soa_jit_value_fills++;
        if (is_block_cached)
            soa_jit_value_cached_responses++;
    } else {
        panic_if(
            response !=
                    SoaJitValueCoalescer::ResponseResult::PrefetchPromote &&
                response !=
                    SoaJitValueCoalescer::ResponseResult::PrefetchDiscard,
                 "I[%d] unknown SoA/JIT value response result=%d\n",
                 my_indirect_id, static_cast<int>(response));
        soa_jit_value_prefetch_responses++;
        if (response ==
            SoaJitValueCoalescer::ResponseResult::PrefetchPromote)
            soa_jit_value_prefetch_promotions++;
        else
            soa_jit_value_prefetch_discards++;
        DPRINTF(MAAVirtualTrace,
                "event=soa_jit_value_prefetch_response schema=1 unit=%d "
                "operation_tick=%lu generation=%lu vaddr=0x%lx "
                "paddr=0x%lx action=%s cached=%d responses=%lu\n",
                my_indirect_id, my_decode_start_tick, soa_jit_generation,
                prefetch_vaddr, addr,
                response ==
                        SoaJitValueCoalescer::ResponseResult::PrefetchPromote
                    ? "promote"
                    : "discard",
                is_block_cached, soa_jit_value_prefetch_responses);
    }
    scheduleNextExecution(true);
    return true;
}
bool IndirectAccessUnit::completeSoaJitWrite(
    const SoaJitScalarBroadcast::WriteIdentity &identity)
{
    if (identity.context >=
        static_cast<uint16_t>(soa_jit_active_contexts))
        return false;
    auto &context = soa_jit_contexts[identity.context];
    const SoaJitScalarBroadcast::WriteIdentity expected{
        context.generation, identity.context, context.aPaddr};
    if (context.state != SoaJitContextState::AwaitAWriteResp ||
        SoaJitScalarBroadcast::validateCompletion(expected, identity) !=
            SoaJitScalarBroadcast::Status::Accepted)
        return false;
    panic_if(context.generation != soa_jit_generation ||
                 context.nextOffset != -1 || context.remaining != 0 ||
                 context.preAUsesPending != 0,
             "I[%d] invalid exact SoA/JIT WriteResp owner\n",
             my_indirect_id);
    soa_jit_a_write_responses++;
    DPRINTF(MAAVirtualTrace,
            "event=soa_jit_a_write_response schema=1 unit=%d "
            "operation_tick=%lu generation=%lu addr=0x%lx\n",
            my_indirect_id, my_decode_start_tick, soa_jit_generation,
            identity.address);
    context = SoaJitContext();
    observeSoaJitResultPipeline();
    return true;
}
void IndirectAccessUnit::checkSoaJitTerminal()
{
    const uint64_t expected_predicate_uses =
        my_predicate_addr == 0 ? 0 : static_cast<uint64_t>(my_max);
    panic_if(!isSoaJitRmw() || soa_jit_generation == 0 ||
                 !soa_jit_all_rows_claimed || !soaJitContextsEmpty() ||
                 soa_jit_epoch_drained || soa_jit_retry_valid ||
                 soa_jit_retry_condition || soa_jit_retry_ordinal != -1 ||
                 soa_jit_epoch_resume_i != -1 ||
                 soa_jit_next_source_ordinal !=
                     static_cast<uint64_t>(my_max) ||
                 soa_jit_epoch_start_ordinal >
                     soa_jit_next_source_ordinal ||
                 offset_table->occupancy() != 0 ||
                 !soaPredicateLinesEmpty() ||
                 soa_jit_selected + soa_jit_predicate_rejected !=
                     static_cast<uint64_t>(my_max) ||
                 soa_jit_predicate_line_hits != expected_predicate_uses ||
                 soa_jit_predicate_uses != expected_predicate_uses ||
                 soa_jit_value_read_issues !=
                     soa_jit_value_read_responses ||
                 soa_jit_value_fills !=
                     soa_jit_value_read_responses ||
                 soa_jit_value_prefetch_issues !=
                     soa_jit_value_prefetch_responses ||
                 soa_jit_value_prefetch_responses !=
                     soa_jit_value_prefetch_promotions +
                         soa_jit_value_prefetch_discards ||
                 soa_jit_value_prefetch_high_water >
                     static_cast<uint64_t>(
                         soa_jit_value_prefetch_credits) ||
                 soa_jit_lookahead_issues != soa_jit_selected ||
                 soa_jit_lookahead_responses != soa_jit_selected ||
                 soa_jit_aliases_applied != soa_jit_selected ||
                 soa_jit_value_deliveries != soa_jit_selected ||
                 soa_jit_pre_a_value_issues >
                     soa_jit_lookahead_issues ||
                 (soa_jit_pre_a_value_lookahead
                      ? (soa_jit_pre_a_value_issues !=
                             soa_jit_pre_a_value_uses ||
                         soa_jit_pre_a_value_ready_at_a_response >
                             soa_jit_pre_a_value_issues)
                      : (soa_jit_pre_a_value_issues != 0 ||
                         soa_jit_pre_a_value_ready_at_a_response != 0 ||
                         soa_jit_pre_a_value_uses != 0)) ||
                 soa_jit_a_read_issues != soa_jit_a_read_responses ||
                 soa_jit_a_read_issues != soa_jit_a_write_issues ||
                 soa_jit_a_write_issues != soa_jit_a_write_responses ||
                 (isSoaJitOldResultRmw()
                      ? (!soa_jit_old_result_selection_closed ||
                         !soa_jit_old_result_finished ||
                         soa_jit_old_result_buffer.activeRun() ||
                         !soa_jit_old_result_buffer.empty() ||
                         soa_jit_old_result_captures != soa_jit_selected ||
                         soa_jit_old_result_write_issues !=
                             soa_jit_old_result_write_responses ||
                         soa_jit_old_result_buffer.captured() !=
                             soa_jit_selected ||
                         soa_jit_old_result_buffer.rejected() !=
                             soa_jit_predicate_rejected ||
                         soa_jit_old_result_buffer.issues() !=
                             soa_jit_old_result_write_issues ||
                         soa_jit_old_result_buffer.responses() !=
                             soa_jit_old_result_write_responses)
                      : (soa_jit_old_result_selection_closed ||
                         soa_jit_old_result_finished ||
                         soa_jit_old_result_captures != 0 ||
                         soa_jit_old_result_write_issues != 0 ||
                         soa_jit_old_result_write_responses != 0 ||
                         soa_jit_old_result_stalls != 0)) ||
                 soa_jit_predicate_line_issues !=
                     soa_jit_predicate_line_responses ||
                 soa_jit_predicate_feeder_high_water >
                     static_cast<uint64_t>(
                         soa_jit_predicate_active_credits) ||
                 soa_jit_context_high_water >
                     static_cast<uint64_t>(soa_jit_active_contexts) ||
                 soa_jit_value_cache_high_water >
                     static_cast<uint64_t>(soa_jit_active_value_owners) ||
                 soa_jit_lookahead_high_water >
                     static_cast<uint64_t>(soa_jit_active_contexts *
                                           soa_jit_value_lookahead) ||
                 soa_jit_apply_lane_high_water >
                     static_cast<uint64_t>(soa_jit_apply_lanes) ||
                 !soa_jit_result_pipeline.assertInvariants(
                     soa_jit_active_contexts) ||
                 !soa_jit_apply_lane_pool.assertInvariants() ||
                 !soa_jit_value_coalescer.assertInvariants() ||
                 soa_jit_value_coalescer.fillingCount() != 0 ||
                 !soaJitValuePrefetchComplete() ||
                 !soa_jit_value_coalescer.clearGeneration(
                     soa_jit_generation),
             "I[%d] SoA/JIT terminal accounting failed\n",
             my_indirect_id);
    offset_table->check_reset();
    for (int slice = 0; slice < num_RT_slices[my_RT_config]; ++slice)
        RT[my_RT_config][slice].check_reset();
}
void IndirectAccessUnit::executeInstruction() {
    if (state == Status::Idle) {
        attribution_execute_sequence = 0;
        my_decode_start_tick = curTick();
    }
    DPRINTF(MAAVirtualTrace,
            "event=indirect_execute schema=2 unit=%d occurrence=%lu "
            "operation_tick=%lu sequence=%lu "
            "state=%s itr=%d\n",
            my_indirect_id, attribution_event_occurrence++,
            my_decode_start_tick, attribution_execute_sequence++,
            status_names[static_cast<int>(state)], my_i);
    if (state != Status::Idle && isSoaJitRmw() &&
        soa_jit_generation != 0)
        serviceSoaJitValuePrefetch();
    switch (state) {
    case Status::Idle: {
        assert(my_instruction != nullptr);
        DPRINTF(MAAIndirect, "I[%d] %s: idling %s!\n", my_indirect_id, __func__, my_instruction->print());
        DPRINTF(MAATrace, "I[%d] Start [%s]\n", my_indirect_id, my_instruction->print());
        attribution_stage_ticks.fill(0);
        attribution_row_insert_attempts = 0;
        attribution_row_insert_successes = 0;
        attribution_offset_pressure_events = 0;
        attribution_row_pressure_events = 0;
        attribution_combiner_words = 0;
        attribution_write_issues = 0;
        attribution_write_completions = 0;
        macro_b_first_issue_tick = 0;
        macro_b_last_issue_tick = 0;
        macro_b_last_response_tick = 0;
        macro_row_first_insert_tick = 0;
        macro_row_last_insert_tick = 0;
        macro_a_first_issue_tick = 0;
        macro_a_last_issue_tick = 0;
        macro_a_last_response_tick = 0;
        macro_backing_first_issue_tick = 0;
        macro_backing_last_issue_tick = 0;
        macro_backing_last_ack_tick = 0;
        macro_backing_credit_stall_tick = 0;
        macro_b_lines = 0;
        macro_b_bytes = 0;
        macro_b_retries = 0;
        macro_b_queue_high_water = 0;
        macro_a_lines = 0;
        macro_a_bytes = 0;
        macro_a_retries = 0;
        macro_backing_transport_bytes = 0;
        macro_backing_semantic_bytes = 0;
        macro_backing_line_issues = 0;
        macro_backing_word_issues = 0;
        macro_backing_credit_stalls = 0;
        macro_backing_address_retries = 0;
        macro_request_reason_cycles.fill(0);
        macro_pipeline_cycles.fill(0);
        attribution_execute_sequence = 1;
        if (debug::MAAReorderTrace)
            reorder_survival.begin(reorder_instruction_sequence++);
        state = Status::Decode;
        transitionAttributionStage(AttributionStage::Decode,
                                   "instruction_start");
        [[fallthrough]];
    }
    case Status::Decode: {
        assert(my_instruction != nullptr);
        DPRINTF(MAAIndirect, "I[%d] %s: decoding %s!\n", my_indirect_id, __func__, my_instruction->print());

        // Decoding the instruction
        my_base_addr = my_instruction->baseAddr;
        my_backing_addr = my_instruction->backingAddr;
        my_index_addr = my_instruction->indexAddr;
        my_predicate_addr = my_instruction->predicateAddr;
        my_result_addr = my_instruction->resultAddr;
        my_idx_tile = my_instruction->src1SpdID;
        my_src_tile = my_instruction->src2SpdID;
        my_src_reg = my_instruction->src1RegID;
        my_dst_tile = my_instruction->dst1SpdID;
        my_cond_tile = my_instruction->condSpdID;
        panic_if(usesBoundedSourceResponses() && !reorder_RT,
                 "I[%d] bounded-response indirect load requires row-table "
                 "reordering\n",
                 my_indirect_id);
        if (my_instruction->opcode == Instruction::OpcodeType::INDIR_LD ||
            my_instruction->opcode ==
                Instruction::OpcodeType::INDIR_LD_INDEX ||
            isVirtualLoad() ||
            my_instruction->opcode ==
                Instruction::OpcodeType::INDIR_RMW_VECTOR ||
            my_instruction->opcode ==
                Instruction::OpcodeType::INDIR_RMW_SCALAR) {
            my_is_load = true;
        } else if (my_instruction->opcode == Instruction::OpcodeType::INDIR_ST_VECTOR ||
                   my_instruction->opcode == Instruction::OpcodeType::INDIR_ST_SCALAR) {
            my_is_load = false;
        } else {
            assert(false);
        }
        if (my_instruction->opcode == Instruction::OpcodeType::INDIR_LD ||
            my_instruction->opcode ==
                Instruction::OpcodeType::INDIR_LD_INDEX ||
            isVirtualLoad()) {
            my_word_size = my_instruction->getWordSize(my_dst_tile);
        } else if (isSoaJitRmw()) {
            my_word_size = my_instruction->WordSize();
        } else if (my_instruction->opcode ==
                       Instruction::OpcodeType::INDIR_ST_VECTOR ||
                   my_instruction->opcode ==
                       Instruction::OpcodeType::INDIR_RMW_VECTOR) {
            my_word_size = my_instruction->getWordSize(my_src_tile);
        } else if (my_instruction->opcode == Instruction::OpcodeType::INDIR_ST_SCALAR ||
                   my_instruction->opcode == Instruction::OpcodeType::INDIR_RMW_SCALAR) {
            my_word_size = my_instruction->WordSize();
        } else {
            assert(false);
        }
        my_words_per_cl = 64 / my_word_size;
        virtual_combine_words_limit = virtual_combine_words_configured == 0
            ? virtual_combine_slots.size() * my_words_per_cl
            : virtual_combine_words_configured;
        panic_if(virtual_combine_words_limit <= 0,
                 "I[%d] virtual combiner must hold at least one word\n",
                 my_indirect_id);
        if (isVirtualLoad()) {
            const auto reset_result = virtual_combine_payload.reset(
                static_cast<size_t>(virtual_combine_words_limit));
            panic_if(reset_result != VirtualCombinePayloadStore::Result::Ok,
                     "I[%d] could not reset %d-word virtual payload "
                     "pool: %s\n",
                     my_indirect_id, virtual_combine_words_limit,
                     VirtualCombinePayloadStore::resultName(reset_result));
        }
        maa->stats.numInst++;
        (*maa->stats.IND_NumInsts[my_indirect_id])++;
        if (my_instruction->opcode == Instruction::OpcodeType::INDIR_LD ||
            my_instruction->opcode ==
                Instruction::OpcodeType::INDIR_LD_INDEX ||
            isVirtualLoad()) {
            maa->stats.numInst_INDRD++;
        } else if (my_instruction->opcode == Instruction::OpcodeType::INDIR_ST_SCALAR ||
                   my_instruction->opcode == Instruction::OpcodeType::INDIR_ST_VECTOR) {
            maa->stats.numInst_INDWR++;
        } else if (my_instruction->opcode == Instruction::OpcodeType::INDIR_RMW_SCALAR ||
                   my_instruction->opcode == Instruction::OpcodeType::INDIR_RMW_VECTOR) {
            maa->stats.numInst_INDRMW++;
        } else {
            assert(false);
        }
        my_cond_tile_ready = (my_cond_tile == -1) ? true : false;
        my_idx_tile_ready = false;
        my_src_tile_ready =
            (isSoaJitRmw() ||
             my_instruction->opcode == Instruction::OpcodeType::INDIR_LD ||
             my_instruction->opcode ==
                 Instruction::OpcodeType::INDIR_LD_INDEX ||
             isVirtualLoad() ||
             my_instruction->opcode ==
                 Instruction::OpcodeType::INDIR_ST_SCALAR ||
             my_instruction->opcode ==
                 Instruction::OpcodeType::INDIR_RMW_SCALAR);
        my_RT_config = getRowTableConfig(my_base_addr);

        // Initialization
        my_virtual_addr = 0;
        my_received_responses = my_expected_responses = 0;
        source_issue_sequence = 0;
        source_issue_digest = 1469598103934665603ULL;
        source_issue_digest_secondary = 0x9e3779b97f4a7c15ULL;
        virtual_reserved_responses = 0;
        virtual_reserved_response_words = 0;
        virtual_word_budget_tick = curTick();
        virtual_word_attempts_this_cycle = 0;
        virtual_combine_bank_tick = curTick();
        virtual_combine_bank_conflict_tick = 0;
        std::fill(virtual_combine_bank_used.begin(),
                  virtual_combine_bank_used.end(), false);
        virtual_pending_source = false;
        virtual_pending_source_addr = 0;
        virtual_pending_source_head = -1;
        virtual_pending_source_words = 0;
        virtual_pending_source_rt_idx = -1;
        virtual_pending_source_row_id = -1;
        virtual_pending_source_entry_id = -1;
        virtual_pending_source_grow_addr = 0;
        virtual_source_reservations.clear();
        virtual_outstanding_writes = 0;
        virtual_retirement_write_pages.clear();
        virtual_page_logical_words.clear();
        virtual_page_scanned_words.clear();
        virtual_page_expected_words.clear();
        virtual_page_issued_words.clear();
        virtual_page_completed_words.clear();
        virtual_page_last_write_key.clear();
        virtual_page_ready.clear();
        virtual_pages_ready = 0;
        virtual_pages_ready_before_source_drain = 0;
        virtual_first_page_ready_tick = 0;
        virtual_all_pages_ready_tick = 0;
        virtual_source_expected = 0;
        virtual_source_received = 0;
        virtual_combine_victim = 0;
        std::fill(virtual_combine_set_victims.begin(),
                  virtual_combine_set_victims.end(), 0);
        virtual_full_line_writes = 0;
        virtual_partial_word_writes = 0;
        virtual_max_combine_occupancy = 0;
        virtual_combine_words = 0;
        virtual_max_combine_words = 0;
        virtual_final_flush = false;
        virtual_max_reserved_responses = 0;
        virtual_max_reserved_response_words = 0;
        virtual_response_word_pool_stalls = 0;
        virtual_max_outstanding_writes = 0;
        virtual_build_incomplete = false;
        virtual_native_slice_cursor = 0;
        virtual_write_address_blocked = false;
        virtual_request_reason = VirtualRequestReason::None;
        virtual_request_reason_tick = 0;
        virtual_request_attributed_ticks = 0;
        virtual_request_reason_ticks.fill(0);
        virtual_pipeline_state = 0;
        virtual_pipeline_tick = 0;
        virtual_pipeline_attributed_ticks = 0;
        virtual_pipeline_ticks.fill(0);
        virtual_trace_request_calls = 0;
        for (auto &slot : virtual_response_slots)
            slot = VirtualResponseSlot();
        virtual_response_line_payloads.reset();
        for (auto &slot : virtual_combine_slots)
            slot = VirtualCombineSlot();
        virtual_combine_page_ready.reset(virtual_combine_slots.size());
        offset_table->reset();
        for (int i = 0; i < num_RT_slices[my_RT_config]; i++) {
            RT[my_RT_config][i].reset();
            my_RT_req_sent[my_RT_config][i] = false;
        }
        my_i = 0;
        my_max = -1;
        my_index_min = 0;
        my_index_stride = 1;
        direct_index_next_prefetch_itr = 0;
        direct_index_partition = 0;
        direct_index_partitions = isDirectIndexLoad() && !isSoaJitRmw()
            ? direct_index_max_partitions : 1;
        direct_index_phase = 1;
        direct_index_partition_barrier = false;
        bounded_range_pass.reset();
        bounded_grow_plan.reset();
        descriptor_spool.reset();
        bounded_global_merge.reset();
        bounded_global_merge_phase = BoundedGlobalMergePhase::None;
        bounded_global_merge_run = 0;
        bounded_global_merge_slice_cursor = 0;
        bounded_global_merge_chain_head = -1;
        bounded_global_merge_sort_comparisons = 0;
        bounded_global_merge_row_groups = 0;
        bounded_global_merge_source_responses = 0;
        bounded_global_merge_terminal_acks = 0;
        bounded_global_merge_batch_inflight = false;
        bounded_global_merge_last_key_valid = false;
        bounded_global_merge_last_key.fill(0);
        bounded_global_merge_last_row_valid = false;
        bounded_global_merge_last_slice = 0;
        bounded_global_merge_last_row = 0;
        for (auto &slot : bounded_global_merge_read_slots)
            slot = BoundedGlobalMergeReadSlot();
        for (auto &slot : bounded_global_merge_write_slots)
            slot = BoundedGlobalMergeWriteSlot();
        bounded_global_merge_source_pending = false;
        bounded_global_merge_source_ready = false;
        bounded_global_merge_source_paddr = 0;
        bounded_global_merge_source_vaddr = 0;
        bounded_global_merge_source_head = -1;
        bounded_global_merge_source_tail = -1;
        bounded_global_merge_source_words = 0;
        bounded_global_merge_source_data.fill(0);
        descriptor_spool_bucket_active = false;
        descriptor_spool_bucket_scan_complete = false;
        descriptor_spool_replay_active = false;
        descriptor_spool_read_ahead_active = false;
        descriptor_spool_overlap_opportunity_recorded = false;
        descriptor_spool_operation = false;
        descriptor_spool_base_vaddr = 0;
        descriptor_spool_index_page_paddrs.fill(0);
        descriptor_spool_index_page_valid.fill(false);
        direct_index_summary_active = false;
        direct_index_summary_overflow = false;
        direct_index_iteration_fallback = false;
        direct_index_summary_next_iteration = 0;
        direct_index_summary_records = 0;
        direct_index_summary_probes = 0;
        direct_index_summary_reduction_visits = 0;
        offset_table_drain = false;
        direct_index_pending_lines.clear();
        for (auto &slot : descriptor_spool_read_slots)
            slot = DescriptorSpoolPendingLine();
        for (auto &slot : descriptor_spool_write_slots)
            slot = DescriptorSpoolWriteSlot();
        descriptor_spool_current_valid = false;
        descriptor_spool_current_cursor = 0;
        descriptor_spool_current_descriptor = {};
        descriptor_spool_current_word = {};
        descriptor_spool_bucket_attempts = 0;
        descriptor_spool_bucket_commits = 0;
        descriptor_spool_filter_retry_inspections = 0;
        descriptor_spool_final_flush_stalls = 0;
        descriptor_spool_overlap_opportunities = 0;
        descriptor_spool_next_pass_read_issues = 0;
        descriptor_spool_next_pass_read_responses = 0;
        descriptor_spool_useful_prefetched_lines = 0;
        descriptor_spool_demand_waits_avoided = 0;
        descriptor_spool_prefetch_occupancy = 0;
        descriptor_spool_prefetch_occupancy_hwm = 0;
        descriptor_spool_prefetch_occupancy_tick = 0;
        descriptor_spool_prefetch_occupancy_line_ticks = 0;
        descriptor_spool_wasted_prefetched_lines = 0;
        descriptor_spool_demand_wait_active = false;
        descriptor_spool_demand_wait_boundary = false;
        descriptor_spool_demand_wait_tick = 0;
        descriptor_spool_demand_wait_cursor = 0;
        descriptor_spool_boundary_demand_wait_events = 0;
        descriptor_spool_boundary_demand_wait_ticks = 0;
        descriptor_spool_within_pass_demand_wait_events = 0;
        descriptor_spool_within_pass_demand_wait_ticks = 0;
        direct_index_ready_lines.clear();
        direct_index_words.clear();
        direct_index_max_lines = 0;
        direct_index_max_words = 0;
        for (auto &line : soa_predicate_lines)
            line = SoaPredicateLine();
        for (auto &context : soa_jit_contexts)
            context = SoaJitContext();
        soa_jit_result_pipeline.reset(curTick());
        soa_jit_scalar_broadcast.reset();
        soa_jit_value_coalescer.configure(
            soa_jit_value_cache_enable, soa_jit_value_prefetch_credits,
            soa_jit_active_value_owners);
        soa_jit_value_coalescer.reset();
        soa_jit_value_prefetch_cursor = SoaJitValuePrefetchCursor();
        soa_jit_apply_lane_pool.configure(soa_jit_apply_lanes);
        soa_jit_apply_lane_pool.reset();
        soa_jit_all_rows_claimed = false;
        soa_jit_epoch_drained = false;
        soa_jit_retry_valid = false;
        soa_jit_retry_condition = false;
        soa_jit_retry_ordinal = -1;
        soa_jit_epoch_resume_i = -1;
        soa_jit_epoch_drains = 0;
        soa_jit_epoch_start_ordinal = 0;
        soa_jit_next_source_ordinal = 0;
        soa_jit_generation = 0;
        soa_jit_selected = 0;
        soa_jit_predicate_rejected = 0;
        soa_jit_predicate_line_issues = 0;
        soa_jit_predicate_line_responses = 0;
        soa_jit_predicate_line_hits = 0;
        soa_jit_predicate_uses = 0;
        soa_jit_predicate_feeder_stalls = 0;
        soa_jit_predicate_feeder_high_water = 0;
        soa_jit_a_read_issues = 0;
        soa_jit_a_read_responses = 0;
        soa_jit_value_read_issues = 0;
        soa_jit_value_read_responses = 0;
        soa_jit_value_fills = 0;
        soa_jit_value_cached_responses = 0;
        soa_jit_value_hits = 0;
        soa_jit_value_merged_waiters = 0;
        soa_jit_value_evictions = 0;
        soa_jit_value_deliveries = 0;
        soa_jit_value_stalls = 0;
        soa_jit_value_cache_high_water = 0;
        soa_jit_value_prefetch_issues = 0;
        soa_jit_value_prefetch_responses = 0;
        soa_jit_value_prefetch_promotions = 0;
        soa_jit_value_prefetch_discards = 0;
        soa_jit_value_prefetch_owned = 0;
        soa_jit_value_prefetch_credit_stalls = 0;
        soa_jit_value_prefetch_high_water = 0;
        soa_jit_lookahead_issues = 0;
        soa_jit_lookahead_responses = 0;
        soa_jit_lookahead_stalls = 0;
        soa_jit_lookahead_high_water = 0;
        soa_jit_pre_a_value_issues = 0;
        soa_jit_pre_a_value_ready_at_a_response = 0;
        soa_jit_pre_a_value_uses = 0;
        soa_jit_aliases_applied = 0;
        soa_jit_apply_lane_high_water = 0;
        soa_jit_a_write_issues = 0;
        soa_jit_a_write_responses = 0;
        soa_jit_old_result_captures = 0;
        soa_jit_old_result_write_issues = 0;
        soa_jit_old_result_write_responses = 0;
        soa_jit_old_result_stalls = 0;
        soa_jit_old_result_selection_closed = false;
        soa_jit_old_result_finished = false;
        panic_if(soa_jit_old_result_buffer.activeRun() ||
                     !soa_jit_old_result_buffer.empty(),
                 "I[%d] retained old-result state at decode\n",
                 my_indirect_id);
        soa_jit_context_stalls = 0;
        soa_jit_context_high_water = 0;
        panic_if(soa_jit_operation_active,
                 "I[%d] retained a live SoA/JIT operation at decode\n",
                 my_indirect_id);
        soa_jit_operation_active = isSoaJitRmw();
        if (isDirectIndexLoad()) {
            panic_if(direct_index_partitions != 1 && !isVirtualLoad(),
                     "I[%d] direct-index partitioning is only supported by "
                     "virtual loads\n",
                     my_indirect_id);
            panic_if(my_cond_tile != -1,
                     "I[%d] direct-index load does not yet support "
                     "condition tiles\n",
                     my_indirect_id);
            my_index_min =
                maa->rf->getData<int>(my_instruction->src1RegID);
            const int index_max =
                maa->rf->getData<int>(my_instruction->src2RegID);
            my_index_stride =
                maa->rf->getData<int>(my_instruction->src3RegID);
            panic_if(my_index_stride <= 0 || index_max < my_index_min,
                     "I[%d] invalid streamed-index range %d:%d:%d\n",
                     my_indirect_id, my_index_min, index_max,
                     my_index_stride);
            my_max = index_max == my_index_min
                ? 0
                : (index_max - my_index_min - 1) / my_index_stride + 1;
            panic_if(my_max > num_tile_elements,
                     "I[%d] streamed-index length %d exceeds logical tile "
                     "capacity %d\n",
                     my_indirect_id, my_max, num_tile_elements);
            if (isSoaJitRmw()) {
                panic_if(!my_instruction->hasValidSoaJitRmwOperands(),
                         "I[%d] malformed SoA/JIT RMW reached decode\n",
                         my_indirect_id);
                if (isSoaJitScalarRmw()) {
                    const auto register_validation =
                        SoaJitScalarBroadcast::validateRegisters(
                            my_instruction->soaJitScalarRegID,
                            my_word_size / sizeof(uint32_t),
                            my_instruction->src1RegID,
                            my_instruction->src2RegID,
                            my_instruction->src3RegID,
                            maa->num_regs);
                    panic_if(
                        register_validation !=
                                SoaJitScalarBroadcast::Status::Accepted ||
                            soa_jit_scalar_broadcast.capture(
                                maa->rf->getDataPtr(
                                    my_instruction->soaJitScalarRegID),
                                my_word_size,
                                static_cast<uint8_t>(
                                    my_instruction->datatype),
                                static_cast<uint8_t>(
                                    my_instruction->optype)) !=
                                SoaJitScalarBroadcast::Status::Accepted,
                        "I[%d] rejected scalar capture before Row/Offset "
                        "mutation\n",
                        my_indirect_id);
                }
                panic_if(maa->virtual_index_range_passes ||
                             maa->virtual_index_descriptor_spool ||
                             maa->virtual_bounded_global_merge,
                         "I[%d] SoA/JIT RMW does not admit range passes, "
                         "descriptor spooling, or GZP/global merge\n",
                         my_indirect_id);
                panic_if(offset_table->capacity() <= 0 ||
                             maa->num_offset_table_epoch_entries == 0 ||
                             maa->num_offset_table_epoch_entries >
                                 static_cast<uint32_t>(
                                     offset_table->capacity()),
                         "I[%d] invalid bounded SoA/JIT Offset geometry: "
                         "capacity=%d epoch=%u\n",
                         my_indirect_id, offset_table->capacity(),
                         maa->num_offset_table_epoch_entries);
            }
            my_idx_tile_ready = true;
            if (maa->virtual_index_range_passes && !isSoaJitRmw()) {
                panic_if(!isVirtualLoad(),
                         "I[%d] bounded range passes require a virtual load\n",
                         my_indirect_id);
                if (maa->virtual_index_range_policy == 3) {
                    offset_table->beginSummary();
                    direct_index_summary_active = true;
                    DPRINTF(MAAVirtualTrace,
                            "event=bounded_quantile_summary_begin schema=1 "
                            "unit=%d operation_tick=%lu logical=%d "
                            "histogram_capacity=%d key=translated_dram_grow "
                            "backing=llc_index_scan "
                            "storage=phase_shared_word_offset\n",
                            my_indirect_id, my_decode_start_tick, my_max,
                            offset_table->capacity());
                } else {
                BoundedRangePassTracker::Range grow_range{
                    0, num_RT_possible_grows[my_RT_config]};
                if (maa->virtual_index_range_policy == 1)
                    grow_range = directIndexSourceGrowRange();
                BoundedRangePassTracker::Result result;
                if (maa->virtual_index_range_policy == 2) {
                    std::vector<BoundedRangePassTracker::Range> ranges;
                    ranges.reserve(direct_index_partitions);
                    for (int pass = 0; pass < direct_index_partitions;
                         ++pass) {
                        ranges.push_back({
                            maa->virtual_index_range_boundaries[pass],
                            maa->virtual_index_range_boundaries[pass + 1]});
                    }
                    grow_range = {ranges.front().lower,
                                  ranges.back().upper};
                    result = bounded_range_pass.configureRanges(
                        my_max, offset_table->capacity(), ranges);
                } else {
                    result = bounded_range_pass.configureRange(
                        my_max, offset_table->capacity(),
                        direct_index_partitions, grow_range.lower,
                        grow_range.upper);
                }
                panic_if(
                    result != BoundedRangePassTracker::Result::Accepted,
                    "I[%d] cannot configure bounded range passes: %s\n",
                    my_indirect_id,
                    BoundedRangePassTracker::resultName(result));
                const uint64_t active_row_line_slots =
                    static_cast<uint64_t>(num_RT_slices[my_RT_config]) *
                    num_RT_rows_per_slice *
                    num_RT_slice_columns[my_RT_config];
                DPRINTF(MAAVirtualTrace,
                        "event=bounded_range_begin schema=1 unit=%d "
                        "operation_tick=%lu logical=%d active_offsets=%d "
                        "active_row_lines=%lu passes=%d possible_grows=%lu "
                        "range_policy=%u lower=0x%lx upper=0x%lx "
                        "checker_bytes=%lu backing=llc_index_rescan "
                        "combiner=retained\n",
                        my_indirect_id, my_decode_start_tick, my_max,
                        offset_table->capacity(), active_row_line_slots,
                        direct_index_partitions,
                        num_RT_possible_grows[my_RT_config],
                        maa->virtual_index_range_policy, grow_range.lower,
                        grow_range.upper,
                        static_cast<unsigned long>(
                            bounded_range_pass.chargedBytes()));
                if (maa->virtual_index_range_policy == 2) {
                    for (int pass = 0; pass < direct_index_partitions;
                         ++pass) {
                        const auto range = bounded_range_pass.range(pass);
                        DPRINTF(MAAVirtualTrace,
                                "event=bounded_range_oracle schema=1 "
                                "unit=%d operation_tick=%lu pass=%d "
                                "lower=0x%lx upper=0x%lx "
                                "provenance=offline_profile\n",
                                my_indirect_id, my_decode_start_tick, pass,
                                range.lower, range.upper);
                    }
                }
                }
            }
        }
        my_SPD_read_finish_tick = curTick();
        my_SPD_write_finish_tick = curTick();
        my_RT_read_access_finish_tick = curTick();
        my_RT_write_access_finish_tick = curTick();
        my_direct_index_filter_finish_tick = curTick();
        my_direct_index_filter_accounted_tick = curTick();
        my_fill_start_tick = 0;
        my_build_start_tick = 0;
        my_request_start_tick = 0;
        my_fill_finished = false;
        my_force_cache_determined = false;
        my_force_cache = false;
        my_min_addr = my_instruction->minAddr;
        my_max_addr = my_instruction->maxAddr;
        my_addr_range_id = my_instruction->addrRangeID;
        my_backing_min_addr = my_instruction->backingMinAddr;
        my_backing_max_addr = my_instruction->backingMaxAddr;
        my_backing_addr_range_id = my_instruction->backingAddrRangeID;
        my_index_min_addr = my_instruction->indexMinAddr;
        my_index_max_addr = my_instruction->indexMaxAddr;
        my_index_addr_range_id = my_instruction->indexAddrRangeID;
        my_predicate_min_addr = my_instruction->predicateMinAddr;
        my_predicate_max_addr = my_instruction->predicateMaxAddr;
        my_predicate_addr_range_id =
            my_instruction->predicateAddrRangeID;
        my_result_min_addr = my_instruction->resultMinAddr;
        my_result_max_addr = my_instruction->resultMaxAddr;
        my_result_addr_range_id = my_instruction->resultAddrRangeID;
        if (isVirtualLoad()) {
            panic_if(my_backing_addr_range_id < 0,
                     "I[%d] virtual backing has no registered region\n",
                     my_indirect_id);
            panic_if(my_backing_addr < my_backing_min_addr ||
                         my_backing_addr >= my_backing_max_addr,
                     "I[%d] virtual backing 0x%lx out of range "
                     "[0x%lx, 0x%lx)\n",
                     my_indirect_id, my_backing_addr, my_backing_min_addr,
                     my_backing_max_addr);
        }
        if (isDirectIndexLoad()) {
            panic_if(my_index_addr_range_id < 0,
                     "I[%d] direct index has no registered region\n",
                     my_indirect_id);
            panic_if(my_index_addr < my_index_min_addr ||
                         my_index_addr >= my_index_max_addr,
                     "I[%d] direct index 0x%lx out of range "
                     "[0x%lx, 0x%lx)\n",
                     my_indirect_id, my_index_addr, my_index_min_addr,
                     my_index_max_addr);
        }
        if (isSoaJitRmw()) {
            panic_if(!isSoaJitScalarRmw() &&
                         (my_backing_addr_range_id < 0 ||
                          my_backing_addr < my_backing_min_addr ||
                          my_backing_addr >= my_backing_max_addr),
                     "I[%d] SoA/JIT values base 0x%lx has no valid "
                     "registered range\n",
                     my_indirect_id, my_backing_addr);
            if (my_max != 0) {
                const int64_t last_source = soaSourcePosition(my_max - 1);
                panic_if(last_source < 0,
                         "I[%d] SoA/JIT range begins below zero\n",
                         my_indirect_id);
                const Addr index_span =
                    my_index_max_addr - my_index_addr;
                const uint64_t last_index_offset =
                    static_cast<uint64_t>(last_source) * sizeof(uint32_t);
                panic_if((!isSoaJitScalarRmw() &&
                          (my_backing_max_addr - my_backing_addr <
                               static_cast<Addr>(my_word_size) ||
                           static_cast<uint64_t>(last_source) *
                                   my_word_size >
                               my_backing_max_addr - my_backing_addr -
                                   my_word_size)) ||
                             index_span < sizeof(uint32_t) ||
                             last_index_offset >
                                 index_span - sizeof(uint32_t),
                         "I[%d] SoA/JIT values or indices span exceeds its "
                         "registered range\n",
                         my_indirect_id);
                if (my_predicate_addr != 0) {
                    const Addr predicate_span =
                        my_predicate_max_addr - my_predicate_addr;
                    panic_if(my_predicate_addr_range_id < 0 ||
                                 my_predicate_addr <
                                     my_predicate_min_addr ||
                                 my_predicate_addr >=
                                     my_predicate_max_addr ||
                                 predicate_span < sizeof(uint32_t) ||
                                 last_index_offset >
                                     predicate_span - sizeof(uint32_t),
                             "I[%d] SoA/JIT predicate span exceeds its "
                             "registered range\n",
                             my_indirect_id);
                }
            }
            validateSoaJitAddressSpans();
            panic_if(isSoaJitMaskedIndexRmw() &&
                         !SoaJitSafety::maskedIndexMarkerOutsideLegalRange(
                             my_base_addr, my_min_addr, my_max_addr,
                             my_word_size),
                     "I[%d] masked-index sentinel can name a legal A word "
                     "in [0x%lx,0x%lx) from base 0x%lx\n",
                     my_indirect_id, my_min_addr, my_max_addr,
                     my_base_addr);
            panic_if(soa_jit_next_generation == 0 ||
                         soa_jit_next_generation ==
                             std::numeric_limits<uint64_t>::max(),
                     "I[%d] SoA/JIT generation exhausted\n",
                     my_indirect_id);
            soa_jit_generation = soa_jit_next_generation++;
            if (isSoaJitOldResultRmw()) {
                panic_if(my_word_size != sizeof(float) ||
                             my_instruction->datatype !=
                                 Instruction::DataType::FLOAT32_TYPE ||
                             my_result_addr_range_id < 0 ||
                             my_result_addr < my_result_min_addr ||
                             my_result_addr >= my_result_max_addr,
                         "I[%d] invalid FP32 old-result backing geometry\n",
                         my_indirect_id);
                const auto begin = soa_jit_old_result_buffer.begin(
                    soa_jit_generation, my_result_addr, my_max);
                panic_if(begin != SoaJitOldResultBuffer::Result::Accepted,
                         "I[%d] old-result generation begin failed: %u\n",
                         my_indirect_id, static_cast<unsigned>(begin));
            }
        }

        // Setting the state of the instruction and stream unit
        my_instruction->state = Instruction::Status::Service;
        DPRINTF(MAAIndirect, "I[%d] %s: state set to Fill for request %s!\n", my_indirect_id, __func__, my_instruction->print());
        state = Status::Fill;
        transitionAttributionStage(AttributionStage::Fill,
                                   "decode_complete");
        [[fallthrough]];
    }
    case Status::Fill: {
        // Reordering the indices
        DPRINTF(MAAIndirect, "I[%d] %s: filling %s!\n", my_indirect_id, __func__, my_instruction->print());
        if (scheduleNextExecution()) {
            break;
        }
        if (my_fill_start_tick == 0) {
            my_fill_start_tick = curTick();
        }
        if (my_request_start_tick != 0) {
            finishVirtualRequestInterval();
            (*maa->stats.IND_CyclesRequest[my_indirect_id]) += maa->getTicksToCycles(curTick() - my_request_start_tick);
            my_request_start_tick = 0;
        }
        bool finished, waitForFinish, waitForElement, needDrain;
        int num_spd_read_condidx_accesses, num_rowtable_accesses;
        int num_direct_index_filter_words;
        fillRowTable(finished, waitForFinish, waitForElement, needDrain,
                     num_spd_read_condidx_accesses, num_rowtable_accesses,
                     num_direct_index_filter_words);
        panic_if(isSoaJitRmw() && needDrain &&
                     (!soa_jit_retry_valid ||
                      soa_jit_retry_ordinal != my_i ||
                      soa_jit_epoch_drained || my_fill_finished),
                 "I[%d] invalid SoA/JIT Row/Offset pressure boundary at "
                 "cursor=%d retry=%d/%d epoch_drained=%d\n",
                 my_indirect_id, my_i, soa_jit_retry_valid,
                 soa_jit_retry_ordinal, soa_jit_epoch_drained);
        bool buildReady = false;
        if (waitForFinish) {
            DPRINTF(MAAVirtualTrace,
                    "event=indirect_stall schema=2 unit=%d occurrence=%lu "
                    "operation_tick=%lu sequence=%lu "
                    "reason=fill_wait_finish itr=%d\n",
                    my_indirect_id, attribution_event_occurrence++,
                    my_decode_start_tick,
                    attribution_execute_sequence - 1, my_i);
            DPRINTF(MAAIndirect,
                    "I[%d] %s: waiting for fill finish %s!\n",
                    my_indirect_id, __func__, my_instruction->print());
        } else if (finished) {
            DPRINTF(MAAIndirect, "I[%d] %s: fill finished %s!\n",
                    my_indirect_id, __func__, my_instruction->print());
            my_fill_finished = true;
            buildReady = true;
        } else if (waitForElement) {
            DPRINTF(MAAVirtualTrace,
                    "event=indirect_stall schema=2 unit=%d occurrence=%lu "
                    "operation_tick=%lu sequence=%lu "
                    "reason=source_index_or_tile_wait itr=%d\n",
                    my_indirect_id, attribution_event_occurrence++,
                    my_decode_start_tick,
                    attribution_execute_sequence - 1, my_i);
            DPRINTF(MAAIndirect,
                    "I[%d] %s: waiting for fill element %s!\n",
                    my_indirect_id, __func__, my_instruction->print());
        } else if (needDrain) {
            DPRINTF(MAAIndirect, "I[%d] %s: fill needs to drain %s!\n",
                    my_indirect_id, __func__, my_instruction->print());
            DPRINTF(MAAVirtualTrace,
                    "event=fill_drain unit=%d itr=%d expected=%d "
                    "received=%d reserved=%d writes=%d\n",
                    my_indirect_id, my_i, virtual_source_expected,
                    virtual_source_received, virtual_reserved_responses,
                    virtual_outstanding_writes);
            if (isSoaJitRmw()) {
                DPRINTF(MAAVirtualTrace,
                        "event=soa_jit_epoch_pressure schema=1 unit=%d "
                        "operation_tick=%lu generation=%lu next_epoch=%lu "
                        "cursor=%d epoch_begin=%lu offset_occupancy=%d "
                        "selected=%lu rejected=%lu retry_ordinal=%d\n",
                        my_indirect_id, my_decode_start_tick,
                        soa_jit_generation, soa_jit_epoch_drains + 1,
                        my_i, soa_jit_epoch_start_ordinal,
                        offset_table->occupancy(), soa_jit_selected,
                        soa_jit_predicate_rejected,
                        soa_jit_retry_ordinal);
            }
            my_fill_finished = false;
            buildReady = true;
        } else {
            panic_if(false, "I[%d] %s: unknown state!\n", my_indirect_id, __func__);
        }
        // Row table parallelism = total #sub-banks. Each bank can be inserted once at a cycle
        updateLatency(0, num_spd_read_condidx_accesses, 0, 0, num_rowtable_accesses, total_num_RT_subslices);
        chargeDirectIndexFilterLatency(num_direct_index_filter_words);
        if (buildReady) {
            if (reorder_RT) {
                DPRINTF(MAAIndirect, "I[%d] %s: state set to Build for %s!\n", my_indirect_id, __func__, my_instruction->print());
                state = Status::Build;
                transitionAttributionStage(AttributionStage::Build,
                                           needDrain ? "fill_pressure" :
                                                       "fill_complete");
                scheduleNextExecution(true);
            } else {
                DPRINTF(MAAIndirect, "I[%d] %s: state set to Request for %s!\n", my_indirect_id, __func__, my_instruction->print());
                state = Status::Request;
                transitionAttributionStage(AttributionStage::Request,
                                           "fill_complete");
                scheduleNextExecution(true);
            }
        }
        return;
    }
    case Status::Build: {
        assert(my_instruction != nullptr);
        accountVirtualRequestInterval();
        DPRINTF(MAAIndirect, "I[%d] %s: Building %s requests, fill finished: %s!\n",
                my_indirect_id, __func__, my_instruction->print(), my_fill_finished ? "true" : "false");
        if (scheduleNextExecution()) {
            break;
        }
        if (maa->virtual_bounded_global_merge &&
            bounded_global_merge_phase ==
                BoundedGlobalMergePhase::Materialize) {
            if (my_fill_start_tick != 0) {
                (*maa->stats.IND_CyclesFill[my_indirect_id]) +=
                    maa->getTicksToCycles(curTick() - my_fill_start_tick);
                my_fill_start_tick = 0;
            }
            serviceBoundedGlobalRunMaterialization();
            return;
        }
        if (maa->virtual_bounded_global_merge &&
            bounded_global_merge_phase == BoundedGlobalMergePhase::Merge &&
            !bounded_global_merge_batch_inflight) {
            serviceBoundedGlobalMerge();
            return;
        }
        if (usesBoundedSourceResponses())
            (*maa->stats.IND_VirtBuildRounds[my_indirect_id])++;
        DPRINTF(MAAVirtualTrace,
                "event=build_begin unit=%d itr=%d fill_finished=%d "
                "pending=%d expected=%d received=%d reserved=%d writes=%d\n",
                my_indirect_id, my_i, my_fill_finished,
                virtual_pending_source, virtual_source_expected,
                virtual_source_received,
                virtual_reserved_responses,
                virtual_outstanding_writes);
        if (my_build_start_tick == 0) {
            my_build_start_tick = curTick();
        }
        if (my_fill_start_tick != 0) {
            (*maa->stats.IND_CyclesFill[my_indirect_id]) += maa->getTicksToCycles(curTick() - my_fill_start_tick);
            my_fill_start_tick = 0;
        }
        if (isSoaJitRmw()) {
            const bool issued = serviceSoaJitBuild();
            updateLatency(0, 0, 0, issued ? 1 : 0, 0,
                          total_num_RT_subslices);
            state = Status::Request;
            transitionAttributionStage(AttributionStage::Request,
                                       issued ? "soa_a_read" :
                                                "soa_rows_drained");
            scheduleNextExecution(true);
            return;
        }
        const bool native_order_claim =
            usesBoundedSourceResponses() && maa->virtual_native_issue_order;
        int last_RT_sent = native_order_claim
            ? virtual_native_slice_cursor
            : 0;
        int num_rowtable_accesses = 0;
        Addr addr;
        if (my_force_cache_determined == false) {
            my_force_cache_determined = true;
            if (descriptor_spool_operation) {
                const bool source_bypass_cache =
                    maa->virtual_descriptor_spool_source_bypass_cache;
                my_force_cache = source_bypass_cache
                    ? false
                    : direct_index_force_cache;
                DPRINTF(MAAIndirect,
                        "I[%d] bounded operation declared cache route %d\n",
                        my_indirect_id, my_force_cache);
                DPRINTF(MAAVirtualTrace,
                        "event=descriptor_spool_source_route schema=1 "
                        "unit=%d operation_tick=%lu source=A "
                        "force_cache=%d bypass_cache=%d "
                        "direct_index_force_cache=%d\n",
                        my_indirect_id, my_decode_start_tick,
                        my_force_cache, source_bypass_cache,
                        direct_index_force_cache);
            } else if (my_unique_WORD_addrs.size() >
                       my_words_per_cl * my_unique_CL_addrs.size()) {
                DPRINTF(MAAIndirect,
                        "I[%d] %s: Direct cache access is needed!\n",
                        my_indirect_id, __func__);
                my_force_cache = true;
            } else {
                DPRINTF(MAAIndirect,
                        "I[%d] %s: Direct cache access is not needed!\n",
                        my_indirect_id, __func__);
                my_force_cache = false;
            }
        }
        bool virtual_capacity_full = false;
        if (usesBoundedSourceResponses() && virtual_pending_source) {
            issueVirtualSource(virtual_pending_source_addr,
                               virtual_pending_source_head,
                               virtual_pending_source_words,
                               virtual_pending_source_rt_idx,
                               virtual_pending_source_row_id,
                               virtual_pending_source_entry_id,
                               virtual_pending_source_grow_addr, 0);
            virtual_pending_source = false;
            virtual_pending_source_addr = 0;
            virtual_pending_source_head = -1;
            virtual_pending_source_words = 0;
            virtual_pending_source_rt_idx = -1;
            virtual_pending_source_row_id = -1;
            virtual_pending_source_entry_id = -1;
            virtual_pending_source_grow_addr = 0;
        }
        while (!virtual_capacity_full) {
            if (checkAndResetAllRowTablesSent())
                break;
            for (; last_RT_sent < num_RT_slices[my_RT_config]; last_RT_sent++) {
                if (usesBoundedSourceResponses() &&
                    virtual_reserved_responses ==
                        virtual_response_slots.size()) {
                    macro_a_retries++;
                    virtual_capacity_full = true;
                    break;
                }
                int RT_idx = my_RT_slice_order[my_RT_config][last_RT_sent];
                assert(RT_idx < num_RT_slices[my_RT_config]);
                DPRINTF(MAAIndirect, "I[%d] %s: Checking row table bank[%d]!\n", my_indirect_id, __func__, RT_idx);
                if (my_RT_req_sent[my_RT_config][RT_idx] == false) {
                    int virtual_head = -1;
                    int virtual_words = 0;
                    int virtual_row_id = -1;
                    int virtual_entry_id = -1;
                    Addr virtual_grow_addr = 0;
                    bool entry_ready;
                    if (native_order_claim) {
                        entry_ready = RT[my_RT_config][RT_idx]
                                          .claim_entry_send_native_order(
                                              addr, virtual_head,
                                              virtual_words,
                                              my_fill_finished,
                                              virtual_row_id,
                                              virtual_entry_id);
                    } else if (usesBoundedSourceResponses()) {
                        entry_ready =
                            RT[my_RT_config][RT_idx].claim_entry_send(
                                addr, virtual_head, virtual_words,
                                my_fill_finished, maa->virtual_grow_order,
                                false);
                    } else {
                        entry_ready = RT[my_RT_config][RT_idx].get_entry_send(
                            addr, my_fill_finished);
                    }
                    if (entry_ready) {
                        if (native_order_claim) {
                            const std::vector<int> addr_vec =
                                maa->map_addr(addr);
                            const int claim_rt_idx = getRowTableIdx(
                                my_RT_config,
                                addr_vec[ADDR_CHANNEL_LEVEL],
                                addr_vec[ADDR_RANK_LEVEL],
                                addr_vec[ADDR_BANKGROUP_LEVEL],
                                addr_vec[ADDR_BANK_LEVEL]);
                            panic_if(claim_rt_idx != RT_idx,
                                     "I[%d] native claim moved from RT %d "
                                     "to %d\n",
                                     my_indirect_id, RT_idx, claim_rt_idx);
                            virtual_grow_addr = getGrowAddr(
                                my_RT_config,
                                addr_vec[ADDR_BANKGROUP_LEVEL],
                                addr_vec[ADDR_BANK_LEVEL],
                                addr_vec[ADDR_ROW_LEVEL]);
                        }
                        DPRINTF(MAAIndirect,
                                "I[%d] %s: Creating packet for bank[%d], "
                                "addr[0x%lx]!\n",
                                my_indirect_id, __func__, RT_idx, addr);
                        if (usesBoundedSourceResponses()) {
                            panic_if(virtual_head < 0 || virtual_words <= 0,
                                     "I[%d] virtual source claim is empty\n",
                                     my_indirect_id);
                            if (virtual_response_word_pool_limit != 0)
                                panic_if(virtual_words >
                                             virtual_response_word_pool_limit,
                                         "I[%d] source response needs %d/%d pooled words\n",
                                         my_indirect_id, virtual_words,
                                         virtual_response_word_pool_limit);
                            if (virtual_response_word_pool_limit != 0 &&
                                virtual_reserved_response_words +
                                        virtual_words >
                                    virtual_response_word_pool_limit) {
                                if (!native_order_claim) {
                                    Addr committed_addr = 0;
                                    int committed_head = -1;
                                    int committed_words = 0;
                                    const bool committed =
                                        RT[my_RT_config][RT_idx]
                                            .claim_entry_send(
                                                committed_addr,
                                                committed_head,
                                                committed_words,
                                                my_fill_finished,
                                                maa->virtual_grow_order,
                                                true);
                                    panic_if(
                                        !committed ||
                                            committed_addr != addr ||
                                            committed_head != virtual_head ||
                                            committed_words != virtual_words,
                                        "I[%d] virtual deferred claim "
                                        "changed between peek and commit\n",
                                        my_indirect_id);
                                }
                                virtual_pending_source = true;
                                virtual_pending_source_addr = addr;
                                virtual_pending_source_head = virtual_head;
                                virtual_pending_source_words = virtual_words;
                                virtual_pending_source_rt_idx = RT_idx;
                                virtual_pending_source_row_id = virtual_row_id;
                                virtual_pending_source_entry_id =
                                    virtual_entry_id;
                                virtual_pending_source_grow_addr =
                                    virtual_grow_addr;
                                if (native_order_claim) {
                                    last_RT_sent++;
                                    if (last_RT_sent ==
                                        num_RT_slices[my_RT_config])
                                        last_RT_sent = 0;
                                }
                                virtual_response_word_pool_stalls++;
                                macro_a_retries++;
                                num_rowtable_accesses++;
                                virtual_capacity_full = true;
                                break;
                            }
                            if (!native_order_claim) {
                                Addr committed_addr = 0;
                                int committed_head = -1;
                                int committed_words = 0;
                                const bool committed =
                                    RT[my_RT_config][RT_idx].claim_entry_send(
                                        committed_addr, committed_head,
                                        committed_words, my_fill_finished,
                                        maa->virtual_grow_order, true);
                                panic_if(!committed ||
                                             committed_addr != addr ||
                                             committed_head != virtual_head ||
                                             committed_words != virtual_words,
                                         "I[%d] virtual source claim changed "
                                         "between peek and commit\n",
                                         my_indirect_id);
                            }
                        }
                        if (usesBoundedSourceResponses()) {
                            issueVirtualSource(
                                addr, virtual_head, virtual_words,
                                RT_idx, virtual_row_id, virtual_entry_id,
                                virtual_grow_addr,
                                getCeiling(num_rowtable_accesses + 1,
                                           total_num_RT_subslices) *
                                    rowtable_latency);
                        } else {
                            my_expected_responses++;
                            recordReorderSurvivalIssue(addr);
                            createReadPacket(
                                addr,
                                getCeiling(num_rowtable_accesses + 1,
                                           total_num_RT_subslices) *
                                    rowtable_latency);
                        }
                        num_rowtable_accesses++;
                    } else {
                        DPRINTF(MAAIndirect, "I[%d] %s: T[%d] has nothing, setting sent to true!\n", my_indirect_id, __func__, RT_idx);
                        my_RT_req_sent[my_RT_config][RT_idx] = true;
                    }
                } else {
                    DPRINTF(MAAIndirect, "I[%d] %s: T[%d] has already sent the requests!\n", my_indirect_id, __func__, RT_idx);
                }
            }
            if (virtual_capacity_full)
                break;
            last_RT_sent = (last_RT_sent >= num_RT_slices[my_RT_config]) ? 0 : last_RT_sent;
        }
        if (usesBoundedSourceResponses() && maa->virtual_grow_order) {
            for (int RT_idx = 0; RT_idx < num_RT_slices[my_RT_config];
                 ++RT_idx)
                RT[my_RT_config][RT_idx].reset_virtual_claim_group();
        }
        if (native_order_claim)
            virtual_native_slice_cursor = virtual_capacity_full
                ? last_RT_sent
                : 0;
        virtual_build_incomplete = virtual_capacity_full;
        DPRINTF(MAAVirtualTrace,
                "event=build_end unit=%d itr=%d incomplete=%d pending=%d "
                "expected=%d received=%d reserved=%d words=%d writes=%d\n",
                my_indirect_id, my_i, virtual_build_incomplete,
                virtual_pending_source, virtual_source_expected,
                virtual_source_received,
                virtual_reserved_responses,
                virtual_reserved_response_words, virtual_outstanding_writes);
        DPRINTF(MAAIndirect, "I[%d] %s: state set to Request for %s!\n", my_indirect_id, __func__, my_instruction->print());
        // Row table parallelism = total #banks. Each bank can give us a address in a cycle.
        updateLatency(0, 0, 0, num_rowtable_accesses, 0, total_num_RT_subslices);
        state = Status::Request;
        transitionAttributionStage(AttributionStage::Request,
                                   virtual_build_incomplete
                                       ? "build_capacity"
                                       : "build_complete");
        accountVirtualRequestInterval();
        scheduleNextExecution(true);
        break;
    }
    case Status::Request: {
        assert(my_instruction != nullptr);
        virtual_write_address_blocked = false;
        DPRINTF(MAAIndirect, "I[%d] %s: requesting %s!\n", my_indirect_id, __func__, my_instruction->print());
        if (my_request_start_tick == 0) {
            my_request_start_tick = curTick();
            startVirtualRequestInterval();
        }
        if (maa->virtual_bounded_global_merge &&
            bounded_global_merge_phase == BoundedGlobalMergePhase::Merge &&
            !bounded_global_merge_batch_inflight) {
            if (scheduleNextExecution())
                break;
            accountVirtualRequestInterval();
            serviceBoundedGlobalMerge();
            return;
        }
        if (isSoaJitRmw()) {
            if (my_build_start_tick != 0) {
                (*maa->stats.IND_CyclesBuild[my_indirect_id]) +=
                    maa->getTicksToCycles(curTick() - my_build_start_tick);
                my_build_start_tick = 0;
            }
            bool progressed = serviceSoaJitOldResultWrites(false);
            progressed = serviceSoaJitLookahead() || progressed;
            const size_t active_contexts = soaJitActiveContextCount();
            if (!soa_jit_all_rows_claimed &&
                !soa_jit_epoch_drained &&
                active_contexts <
                    static_cast<size_t>(soa_jit_active_contexts)) {
                if (my_request_start_tick != 0) {
                    finishVirtualRequestInterval();
                    (*maa->stats.IND_CyclesRequest[my_indirect_id]) +=
                        maa->getTicksToCycles(
                            curTick() - my_request_start_tick);
                    my_request_start_tick = 0;
                }
                state = Status::Build;
                transitionAttributionStage(
                    AttributionStage::Build, "soa_context_available");
                scheduleNextExecution(true);
                break;
            }
            if (!soa_jit_all_rows_claimed &&
                !soa_jit_epoch_drained &&
                active_contexts ==
                    static_cast<size_t>(soa_jit_active_contexts))
                soa_jit_context_stalls++;
            if (!soaJitContextsEmpty()) {
                // A ready value may have been produced after this unit's
                // active delivery/apply lanes were used for curTick(). Keep
                // a bounded one-cycle wakeup while slots are occupied so an
                // ordered head cannot wait forever for another response.
                if (progressed || soaJitLookaheadOccupancy() != 0)
                    scheduleExecuteInstructionEvent(1);
                break;
            }
            if (soa_jit_epoch_drained) {
                resetSoaJitEpochTables();
                if (debug::MAAReorderTrace &&
                    reorder_survival.drainPending())
                    closeReorderSurvivalEpoch(false);
                DPRINTF(MAAVirtualTrace,
                        "event=soa_jit_epoch_complete schema=1 unit=%d "
                        "operation_tick=%lu generation=%lu epoch=%lu "
                        "cursor=%d next_source=%lu selected=%lu "
                        "rejected=%lu offset_occupancy=%d contexts=0 "
                        "old_result_selection_closed=%d\n",
                        my_indirect_id, my_decode_start_tick,
                        soa_jit_generation, soa_jit_epoch_drains, my_i,
                        soa_jit_next_source_ordinal, soa_jit_selected,
                        soa_jit_predicate_rejected,
                        offset_table->occupancy(),
                        soa_jit_old_result_selection_closed);
                panic_if(soa_jit_old_result_selection_closed ||
                             soa_jit_all_rows_claimed ||
                             my_fill_finished,
                         "I[%d] SoA/JIT epoch closed terminal selection\n",
                         my_indirect_id);
                soa_jit_epoch_drained = false;
                soa_jit_epoch_resume_i = -1;
                soa_jit_epoch_start_ordinal =
                    soa_jit_next_source_ordinal;
                if (my_request_start_tick != 0) {
                    finishVirtualRequestInterval();
                    (*maa->stats.IND_CyclesRequest[my_indirect_id]) +=
                        maa->getTicksToCycles(
                            curTick() - my_request_start_tick);
                    my_request_start_tick = 0;
                }
                state = Status::Fill;
                transitionAttributionStage(AttributionStage::Fill,
                                           "soa_epoch_refill");
            } else if (!soa_jit_all_rows_claimed) {
                if (my_request_start_tick != 0) {
                    finishVirtualRequestInterval();
                    (*maa->stats.IND_CyclesRequest[my_indirect_id]) +=
                        maa->getTicksToCycles(
                            curTick() - my_request_start_tick);
                    my_request_start_tick = 0;
                }
                state = Status::Build;
                transitionAttributionStage(AttributionStage::Build,
                                           "soa_context_released");
            } else {
                if (!soaJitValuePrefetchComplete()) {
                    if (soa_jit_value_coalescer.prefetchComplete())
                        scheduleExecuteInstructionEvent(1);
                    break;
                }
                if (isSoaJitOldResultRmw()) {
                    if (!soa_jit_old_result_selection_closed) {
                        const auto result =
                            soa_jit_old_result_buffer.closeSelection(
                                soa_jit_selected,
                                soa_jit_predicate_rejected);
                        panic_if(
                            result !=
                                SoaJitOldResultBuffer::Result::Accepted,
                            "I[%d] old-result selection closure failed: %u\n",
                            my_indirect_id,
                            static_cast<unsigned>(result));
                        soa_jit_old_result_selection_closed = true;
                    }
                    progressed = serviceSoaJitOldResultWrites(true) ||
                        progressed;
                    if (!soa_jit_old_result_buffer.complete()) {
                        if (progressed)
                            scheduleExecuteInstructionEvent(1);
                        break;
                    }
                    if (!soa_jit_old_result_finished) {
                        const auto finish =
                            soa_jit_old_result_buffer.finish();
                        panic_if(
                            finish !=
                                SoaJitOldResultBuffer::Result::Accepted,
                            "I[%d] old-result terminal finish failed: %u\n",
                            my_indirect_id,
                            static_cast<unsigned>(finish));
                        soa_jit_old_result_finished = true;
                    }
                }
                checkSoaJitTerminal();
                state = Status::Response;
                transitionAttributionStage(AttributionStage::Response,
                                           "soa_exact_drain");
                my_fill_finished = false;
            }
            scheduleNextExecution(true);
            break;
        }
        if (usesBoundedSourceResponses()) {
            virtual_trace_request_calls++;
            if ((virtual_trace_request_calls &
                 (virtual_trace_request_calls - 1)) == 0) {
                DPRINTF(MAAVirtualTrace,
                        "event=request_heartbeat unit=%d calls=%lu itr=%d "
                        "fill_finished=%d incomplete=%d expected=%d "
                        "received=%d reserved=%d words=%d writes=%d "
                        "combine_words=%d\n",
                        my_indirect_id, virtual_trace_request_calls, my_i,
                        my_fill_finished, virtual_build_incomplete,
                        virtual_source_expected, virtual_source_received,
                        virtual_reserved_responses,
                        virtual_reserved_response_words,
                        virtual_outstanding_writes, virtual_combine_words);
            }
        }
        accountVirtualRequestInterval();
        serviceDescriptorSpoolReadAhead();
        if (usesBoundedSourceResponses() && drainVirtualResponses()) {
            scheduleExecuteInstructionEvent(1);
            break;
        }
        if (reorder_RT) {
            if (my_build_start_tick != 0) {
                (*maa->stats.IND_CyclesBuild[my_indirect_id]) += maa->getTicksToCycles(curTick() - my_build_start_tick);
                my_build_start_tick = 0;
            }
        } else {
            if (my_fill_start_tick != 0) {
                (*maa->stats.IND_CyclesFill[my_indirect_id]) += maa->getTicksToCycles(curTick() - my_fill_start_tick);
                my_fill_start_tick = 0;
            }
        }
        const bool virtual_sources_drained =
            virtual_source_received == virtual_source_expected &&
            virtual_reserved_responses == 0;
        if (isVirtualLoad() && my_fill_finished &&
            !virtual_build_incomplete &&
            virtual_sources_drained) {
            virtual_final_flush = true;
            drainVirtualCombiner(true);
        }
        if (isVirtualLoad() && direct_index_partition_barrier &&
            !virtual_build_incomplete && virtual_sources_drained &&
            !maa->virtual_partition_keep_combiner)
            drainVirtualCombiner(true);
        const bool retain_global_merge_combiner =
            maa->virtual_bounded_global_merge &&
            bounded_global_merge_phase == BoundedGlobalMergePhase::Merge &&
            bounded_global_merge_batch_inflight;
        const bool retain_partition_combiner =
            (isVirtualLoad() && direct_index_partition_barrier &&
             maa->virtual_partition_keep_combiner) ||
            retain_global_merge_combiner;
        bool responses_complete;
        if (!usesBoundedSourceResponses()) {
            responses_complete =
                maa->allIndirectPacketsSent(my_indirect_id) &&
                my_received_responses == my_expected_responses;
        } else if (virtual_build_incomplete) {
            responses_complete = virtual_sources_drained;
        } else if (retain_partition_combiner) {
            responses_complete = boundedSourceResponsesComplete();
        } else {
            responses_complete = boundedRetirementComplete();
        }
        if (responses_complete) {
            if (scheduleNextExecution()) {
                DPRINTF(MAAIndirect, "I[%d] %s: requesting is still not ready, returning!\n", my_indirect_id, __func__);
                break;
            }
            if (virtual_build_incomplete) {
                state = Status::Build;
                transitionAttributionStage(AttributionStage::Build,
                                           "request_rebuild");
                virtual_build_incomplete = false;
            } else if (retain_global_merge_combiner) {
                bounded_global_merge_batch_inflight = false;
                state = Status::Build;
                transitionAttributionStage(
                    AttributionStage::Build,
                    "bounded_global_batch_complete");
            } else if (direct_index_partition_barrier) {
                finishBoundedRangePass(direct_index_partition - 1,
                                       "barrier_drained");
                closeReorderSurvivalEpoch(false);
                state = Status::Fill;
                transitionAttributionStage(AttributionStage::Fill,
                                           "partition_advance");
                direct_index_partition_barrier = false;
            } else if (my_fill_finished) {
                state = Status::Response;
                transitionAttributionStage(AttributionStage::Response,
                                           "request_complete");
                my_fill_finished = false;
            } else {
                if (debug::MAAReorderTrace &&
                    reorder_survival.drainPending())
                    closeReorderSurvivalEpoch(false);
                if (usesBoundedDirectIndexPasses()) {
                    const auto drain_result =
                        bounded_range_pass.recordDrain(
                            direct_index_partition);
                    panic_if(
                        drain_result !=
                            BoundedRangePassTracker::Result::Accepted,
                        "I[%d] bounded range pass %d drain failed: %s\n",
                        my_indirect_id, direct_index_partition,
                        BoundedRangePassTracker::resultName(drain_result));
                    DPRINTF(MAAVirtualTrace,
                            "event=bounded_range_drain schema=1 unit=%d "
                            "operation_tick=%lu pass=%d drains=%u "
                            "max_epoch_admissions=%u\n",
                            my_indirect_id, my_decode_start_tick,
                            direct_index_partition,
                            bounded_range_pass.drainsForPass(
                                direct_index_partition),
                            bounded_range_pass.maxEpochAdmissionsForPass(
                                direct_index_partition));
                }
                state = Status::Fill;
                transitionAttributionStage(AttributionStage::Fill,
                                           "request_refill");
            }
            DPRINTF(MAAVirtualTrace,
                    "event=request_complete unit=%d calls=%lu itr=%d "
                    "next=%s expected=%d received=%d writes=%d\n",
                    my_indirect_id, virtual_trace_request_calls, my_i,
                    status_names[(int)state], virtual_source_expected,
                    virtual_source_received, virtual_outstanding_writes);
            accountVirtualRequestInterval();
            DPRINTF(MAAIndirect, "I[%d] %s: all responses received, calling execution again in state %s!\n", my_indirect_id, __func__, status_names[(int)state]);
            scheduleNextExecution(true);
            break;
        }
        const bool legacy_refill_allowed =
            !maa->virtual_native_issue_order ||
            (!virtual_build_incomplete &&
             boundedSourceResponsesComplete());
        // A finite direct-index pass owns the current Row/Offset contents
        // while a capacity drain is incomplete.  Refilling sooner can
        // rediscover the same full table forever at the same tick.  Keep the
        // established overlap policy for ordinary single-pass operations.
        const bool finite_direct_index_pass =
            isDirectIndexLoad() && direct_index_partitions > 1;
        const bool refill_allowed = finite_direct_index_pass
            ? !virtual_build_incomplete
            : legacy_refill_allowed;
        if (!my_fill_finished && !direct_index_partition_barrier &&
            !retain_global_merge_combiner && refill_allowed) {
            bool finished, waitForFinish, waitForElement, needDrain;
            int num_spd_read_condidx_accesses, num_rowtable_accesses;
            int num_direct_index_filter_words;
            const int fill_start_itr = my_i;
            fillRowTable(finished, waitForFinish, waitForElement, needDrain,
                         num_spd_read_condidx_accesses,
                         num_rowtable_accesses,
                         num_direct_index_filter_words);
            if (usesBoundedSourceResponses()) {
                if (finished)
                    my_fill_finished = true;
                if (needDrain || finished ||
                    (!maa->virtual_native_issue_order &&
                     my_i != fill_start_itr))
                    virtual_build_incomplete = true;
                if (finished || needDrain) {
                    DPRINTF(MAAVirtualTrace,
                            "event=request_refill unit=%d from=%d to=%d "
                            "finished=%d need_drain=%d incomplete=%d\n",
                            my_indirect_id, fill_start_itr, my_i, finished,
                            needDrain, virtual_build_incomplete);
                    scheduleNextExecution(true);
                }
            }
            // Row table parallelism = total #sub-banks. Each bank can be inserted once at a cycle
            updateLatency(0, num_spd_read_condidx_accesses, 0, 0, num_rowtable_accesses, total_num_RT_subslices);
            chargeDirectIndexFilterLatency(num_direct_index_filter_words);
        }
        if (virtual_write_address_blocked)
            scheduleExecuteInstructionEvent(1);
        break;
    }
    case Status::Response: {
        assert(my_instruction != nullptr);
        DPRINTF(MAAIndirect, "I[%d] %s: responding %s!\n", my_indirect_id, __func__, my_instruction->print());
        DPRINTF(MAATrace, "I[%d] End [%s]\n", my_indirect_id, my_instruction->print());
        if (usesBoundedSourceResponses()) {
            DPRINTF(MAAIndirect,
                    "I[%d] bounded-response high water: slots=%d/%zu "
                    "words=%d/%d\n",
                    my_indirect_id, virtual_max_reserved_responses,
                    virtual_response_slots.size(),
                    virtual_max_reserved_response_words,
                    virtual_response_word_pool_limit);
            (*maa->stats.IND_VirtResponseSlotHighWater[my_indirect_id]) +=
                virtual_max_reserved_responses;
            (*maa->stats.IND_VirtResponseWordHighWater[my_indirect_id]) +=
                virtual_max_reserved_response_words;
            (*maa->stats.IND_VirtResponseWordPoolStalls[my_indirect_id]) +=
                virtual_response_word_pool_stalls;
        }
        if (isVirtualLoad()) {
            DPRINTF(MAAIndirect,
                    "I[%d] virtual combining: slots=%zu max_occupancy=%d "
                    "max_words=%d/%d full_lines=%d partial_words=%d\n",
                    my_indirect_id, virtual_combine_slots.size(),
                    virtual_max_combine_occupancy, virtual_max_combine_words,
                    virtual_combine_words_limit, virtual_full_line_writes,
                    virtual_partial_word_writes);
            (*maa->stats.IND_VirtOutstandingWriteHighWater[my_indirect_id]) +=
                virtual_max_outstanding_writes;
            (*maa->stats.IND_VirtCombineLineHighWater[my_indirect_id]) +=
                virtual_max_combine_occupancy;
            (*maa->stats.IND_VirtCombineWordHighWater[my_indirect_id]) +=
                virtual_max_combine_words;
            (*maa->stats.IND_VirtFullLineWrites[my_indirect_id]) +=
                virtual_full_line_writes;
            (*maa->stats.IND_VirtPartialWrites[my_indirect_id]) +=
                virtual_partial_word_writes;
            initializeVirtualPageTracking();
            panic_if(!virtual_retirement_write_pages.empty(),
                     "I[%d] virtual retirement metadata remains at response\n",
                     my_indirect_id);
            panic_if(virtual_pages_ready !=
                         static_cast<int>(virtual_page_expected_words.size()),
                     "I[%d] only %d/%zu virtual pages became ready\n",
                     my_indirect_id, virtual_pages_ready,
                     virtual_page_expected_words.size());
            for (size_t page = 0; page < virtual_page_expected_words.size();
                 ++page) {
                panic_if(virtual_page_scanned_words[page] !=
                             virtual_page_logical_words[page] ||
                             virtual_page_issued_words[page] !=
                             virtual_page_expected_words[page] ||
                             virtual_page_completed_words[page] !=
                             virtual_page_expected_words[page],
                         "I[%d] virtual page %zu word accounting logical=%d "
                         "scanned=%d expected=%d issued=%d completed=%d\n",
                         my_indirect_id, page,
                         virtual_page_logical_words[page],
                         virtual_page_scanned_words[page],
                         virtual_page_expected_words[page],
                         virtual_page_issued_words[page],
                         virtual_page_completed_words[page]);
            }
            panic_if(!virtual_page_expected_words.empty() &&
                         (virtual_first_page_ready_tick == 0 ||
                          virtual_all_pages_ready_tick == 0),
                     "I[%d] virtual page-ready timestamps are incomplete\n",
                     my_indirect_id);
            (*maa->stats.IND_VirtPagesReady[my_indirect_id]) +=
                virtual_pages_ready;
            (*maa->stats
                   .IND_VirtPagesReadyBeforeSourceDrain[my_indirect_id]) +=
                virtual_pages_ready_before_source_drain;
            if (!virtual_page_expected_words.empty()) {
                (*maa->stats.IND_VirtFirstPageReadyCycles[my_indirect_id]) +=
                    maa->getTicksToCycles(virtual_first_page_ready_tick -
                                          my_decode_start_tick);
                (*maa->stats.IND_VirtAllPagesReadyCycles[my_indirect_id]) +=
                    maa->getTicksToCycles(virtual_all_pages_ready_tick -
                                          my_decode_start_tick);
                (*maa->stats.IND_VirtPageReadySpanCycles[my_indirect_id]) +=
                    maa->getTicksToCycles(virtual_all_pages_ready_tick -
                                          virtual_first_page_ready_tick);
            }
            if (usesBoundedDirectIndexPasses()) {
                if (maa->virtual_bounded_global_merge) {
                    panic_if(bounded_global_merge_phase !=
                                 BoundedGlobalMergePhase::Complete,
                             "I[%d] bounded global merge reached Response "
                             "before terminal closure\n",
                             my_indirect_id);
                    for (int pass = 0;
                         pass < direct_index_partitions; ++pass)
                        finishBoundedRangePass(
                            pass, "global_merge_drained");
                } else {
                    finishBoundedRangePass(direct_index_partition,
                                           "final_drained");
                }
                const auto result = bounded_range_pass.finish();
                panic_if(
                    result != BoundedRangePassTracker::Result::Accepted,
                    "I[%d] bounded range exact-once closure failed: %s "
                    "admitted=%u/%u retired=%u/%u\n", my_indirect_id,
                    BoundedRangePassTracker::resultName(result),
                    bounded_range_pass.admissions(),
                    bounded_range_pass.logical(),
                    bounded_range_pass.retirements(),
                    bounded_range_pass.logical());
                DPRINTF(MAAVirtualTrace,
                        "event=bounded_range_complete schema=1 unit=%d "
                        "operation_tick=%lu logical=%u admitted=%u "
                        "retired=%u duplicate_admissions=0 "
                        "duplicate_retirements=0 missing=0 "
                        "checker_bytes=%lu\n",
                        my_indirect_id, my_decode_start_tick,
                        bounded_range_pass.logical(),
                        bounded_range_pass.admissions(),
                        bounded_range_pass.retirements(),
                        static_cast<unsigned long>(
                            bounded_range_pass.chargedBytes()));
                uint32_t replay_drains = 0;
                uint32_t max_epoch_admissions = 0;
                for (uint32_t pass = 0;
                     pass < bounded_range_pass.passes(); ++pass) {
                    replay_drains +=
                        bounded_range_pass.drainsForPass(pass);
                    max_epoch_admissions = std::max(
                        max_epoch_admissions,
                        bounded_range_pass.maxEpochAdmissionsForPass(pass));
                }
                (*maa->stats.IND_BoundedReplayDrains[my_indirect_id]) +=
                    replay_drains;
                (*maa->stats
                      .IND_BoundedReplayMaxEpochAdmissions[my_indirect_id]) +=
                    max_epoch_admissions;
            }
        }
        if (isDirectIndexLoad()) {
            (*maa->stats.IND_VirtIndexLineHighWater[my_indirect_id]) +=
                direct_index_max_lines;
            (*maa->stats.IND_VirtIndexWordHighWater[my_indirect_id]) +=
                direct_index_max_words;
        }
        if (isSoaJitRmw()) {
            checkSoaJitTerminal();
            (*maa->stats.IND_SoaJitInstructions[my_indirect_id])++;
            (*maa->stats.IND_SoaJitSelected[my_indirect_id]) +=
                soa_jit_selected;
            (*maa->stats.IND_SoaJitPredicateRejected[my_indirect_id]) +=
                soa_jit_predicate_rejected;
            (*maa->stats.IND_SoaJitPredicateLineReads[my_indirect_id]) +=
                soa_jit_predicate_line_issues;
            (*maa->stats
                  .IND_SoaJitPredicateLineResponses[my_indirect_id]) +=
                soa_jit_predicate_line_responses;
            (*maa->stats.IND_SoaJitPredicateLineHits[my_indirect_id]) +=
                soa_jit_predicate_line_hits;
            (*maa->stats.IND_SoaJitPredicateUses[my_indirect_id]) +=
                soa_jit_predicate_uses;
            (*maa->stats.IND_SoaJitPredicateFeederStalls[my_indirect_id]) +=
                soa_jit_predicate_feeder_stalls;
            (*maa->stats.IND_SoaJitPredicateActiveCredits[my_indirect_id]) +=
                soa_jit_predicate_active_credits;
            (*maa->stats
                  .IND_SoaJitPredicateFeederHighWater[my_indirect_id]) +=
                soa_jit_predicate_feeder_high_water;
            (*maa->stats
                  .IND_SoaJitPredicateFeederStateBytes[my_indirect_id]) +=
                SoaPredicateFeederStateBytes;
            (*maa->stats.IND_SoaJitAReadIssues[my_indirect_id]) +=
                soa_jit_a_read_issues;
            (*maa->stats.IND_SoaJitAReadResponses[my_indirect_id]) +=
                soa_jit_a_read_responses;
            (*maa->stats.IND_SoaJitValueReadIssues[my_indirect_id]) +=
                soa_jit_value_read_issues;
            (*maa->stats.IND_SoaJitValueReadResponses[my_indirect_id]) +=
                soa_jit_value_read_responses;
            (*maa->stats.IND_SoaJitValueFills[my_indirect_id]) +=
                soa_jit_value_fills;
            (*maa->stats
                  .IND_SoaJitValueCachedResponses[my_indirect_id]) +=
                soa_jit_value_cached_responses;
            (*maa->stats.IND_SoaJitValueHits[my_indirect_id]) +=
                soa_jit_value_hits;
            (*maa->stats.IND_SoaJitValueMergedWaiters[my_indirect_id]) +=
                soa_jit_value_merged_waiters;
            (*maa->stats.IND_SoaJitValueEvictions[my_indirect_id]) +=
                soa_jit_value_evictions;
            (*maa->stats.IND_SoaJitValueDeliveries[my_indirect_id]) +=
                soa_jit_value_deliveries;
            (*maa->stats.IND_SoaJitValueStalls[my_indirect_id]) +=
                soa_jit_value_stalls;
            (*maa->stats.IND_SoaJitValueCacheHighWater[my_indirect_id]) +=
                soa_jit_value_cache_high_water;
            (*maa->stats.IND_SoaJitValuePrefetchIssues[my_indirect_id]) +=
                soa_jit_value_prefetch_issues;
            (*maa->stats.IND_SoaJitValuePrefetchResponses[my_indirect_id]) +=
                soa_jit_value_prefetch_responses;
            (*maa->stats
                  .IND_SoaJitValuePrefetchPromotions[my_indirect_id]) +=
                soa_jit_value_prefetch_promotions;
            (*maa->stats.IND_SoaJitValuePrefetchDiscards[my_indirect_id]) +=
                soa_jit_value_prefetch_discards;
            (*maa->stats.IND_SoaJitValuePrefetchOwned[my_indirect_id]) +=
                soa_jit_value_prefetch_owned;
            (*maa->stats
                  .IND_SoaJitValuePrefetchCreditStalls[my_indirect_id]) +=
                soa_jit_value_prefetch_credit_stalls;
            (*maa->stats
                  .IND_SoaJitValuePrefetchActiveCredits[my_indirect_id]) +=
                soa_jit_value_prefetch_credits;
            (*maa->stats.IND_SoaJitValuePrefetchHighWater[my_indirect_id]) +=
                soa_jit_value_prefetch_high_water;
            (*maa->stats.IND_SoaJitLookaheadIssues[my_indirect_id]) +=
                soa_jit_lookahead_issues;
            (*maa->stats.IND_SoaJitLookaheadResponses[my_indirect_id]) +=
                soa_jit_lookahead_responses;
            (*maa->stats.IND_SoaJitLookaheadStalls[my_indirect_id]) +=
                soa_jit_lookahead_stalls;
            (*maa->stats.IND_SoaJitLookaheadHighWater[my_indirect_id]) +=
                soa_jit_lookahead_high_water;
            (*maa->stats.IND_SoaJitPreAValueIssues[my_indirect_id]) +=
                soa_jit_pre_a_value_issues;
            (*maa->stats
                  .IND_SoaJitPreAValueReadyAtAResponse[my_indirect_id]) +=
                soa_jit_pre_a_value_ready_at_a_response;
            (*maa->stats.IND_SoaJitPreAValueUses[my_indirect_id]) +=
                soa_jit_pre_a_value_uses;
            (*maa->stats.IND_SoaJitActiveContexts[my_indirect_id]) +=
                soa_jit_active_contexts;
            (*maa->stats.IND_SoaJitActiveValueOwners[my_indirect_id]) +=
                soa_jit_active_value_owners;
            (*maa->stats.IND_SoaJitActiveApplyLanes[my_indirect_id]) +=
                soa_jit_apply_lanes;
            (*maa->stats.IND_SoaJitApplyLaneHighWater[my_indirect_id]) +=
                soa_jit_apply_lane_high_water;
            (*maa->stats.IND_SoaJitAliasesApplied[my_indirect_id]) +=
                soa_jit_aliases_applied;
            (*maa->stats.IND_SoaJitAWriteIssues[my_indirect_id]) +=
                soa_jit_a_write_issues;
            (*maa->stats.IND_SoaJitAWriteResponses[my_indirect_id]) +=
                soa_jit_a_write_responses;
            (*maa->stats.IND_SoaJitOldResultCaptures[my_indirect_id]) +=
                soa_jit_old_result_captures;
            (*maa->stats.IND_SoaJitOldResultWriteIssues[my_indirect_id]) +=
                soa_jit_old_result_write_issues;
            (*maa->stats
                  .IND_SoaJitOldResultWriteResponses[my_indirect_id]) +=
                soa_jit_old_result_write_responses;
            (*maa->stats
                  .IND_SoaJitOldResultCreditHighWater[my_indirect_id]) +=
                soa_jit_old_result_buffer.creditHighWater();
            (*maa->stats.IND_SoaJitOldResultStalls[my_indirect_id]) +=
                soa_jit_old_result_stalls;
            (*maa->stats.IND_SoaJitContextHighWater[my_indirect_id]) +=
                soa_jit_context_high_water;
            (*maa->stats.IND_SoaJitContextStalls[my_indirect_id]) +=
                soa_jit_context_stalls;
            (*maa->stats.IND_SoaJitEpochDrains[my_indirect_id]) +=
                soa_jit_epoch_drains;
            (*maa->stats.IND_SoaJitTerminalCompletions[my_indirect_id])++;
            DPRINTF(MAAVirtualTrace,
                    "event=soa_jit_epoch_summary schema=1 unit=%d "
                    "operation_tick=%lu generation=%lu logical=%d "
                    "next_source=%lu epoch_drains=%lu selected=%lu "
                    "rejected=%lu duplicate_ordinals=0 skipped_ordinals=0 "
                    "old_result_ordered=%d scalar=%d terminal=1\n",
                    my_indirect_id, my_decode_start_tick,
                    soa_jit_generation, my_max,
                    soa_jit_next_source_ordinal, soa_jit_epoch_drains,
                    soa_jit_selected, soa_jit_predicate_rejected,
                    isSoaJitOldResultRmw(), isSoaJitScalarRmw());
            DPRINTF(MAAVirtualTrace,
                    "event=soa_jit_old_result_complete schema=1 unit=%d "
                    "operation_tick=%lu enabled=%d generation=%lu "
                    "captures=%lu rejected=%lu write_issues=%lu "
                    "write_responses=%lu credit_high_water=%lu stalls=%lu "
                    "state_bytes=%lu terminal=1\n",
                    my_indirect_id, my_decode_start_tick,
                    isSoaJitOldResultRmw(), soa_jit_generation,
                    soa_jit_old_result_captures,
                    soa_jit_predicate_rejected,
                    soa_jit_old_result_write_issues,
                    soa_jit_old_result_write_responses,
                    soa_jit_old_result_buffer.creditHighWater(),
                    soa_jit_old_result_stalls,
                    static_cast<unsigned long>(
                        sizeof(soa_jit_old_result_buffer)));
            const auto &result_read_hwm =
                soa_jit_result_pipeline.aReadHighWater();
            const auto &result_write_hwm =
                soa_jit_result_pipeline.aWriteHighWater();
            const auto &result_traffic_hwm =
                soa_jit_result_pipeline.activeLineHighWater();
            constexpr size_t fixed_result_context_bytes =
                sizeof(SoaJitContext);
            constexpr size_t fixed_result_contexts_bytes =
                sizeof(soa_jit_contexts);
            constexpr size_t fixed_result_payload_bytes =
                SoaJitResultPipeline::FixedPayloadBytes;
            constexpr size_t lookahead_value_bytes_per_context =
                SoaJitValueCoalescer::MaxLookahead *
                sizeof(std::array<uint8_t, 8>);
            constexpr size_t fixed_lookahead_value_payload_bytes =
                SoaJitContexts * lookahead_value_bytes_per_context;
            constexpr size_t baseline_32_lookahead_value_payload_bytes =
                SoaJitResultPipeline::BaselineLines *
                lookahead_value_bytes_per_context;
            constexpr size_t incremental_lookahead_value_payload_bytes =
                fixed_lookahead_value_payload_bytes -
                baseline_32_lookahead_value_payload_bytes;
            constexpr size_t fixed_result_nonpayload_bytes =
                fixed_result_contexts_bytes - fixed_result_payload_bytes -
                fixed_lookahead_value_payload_bytes;
            constexpr size_t baseline_result_contexts_bytes =
                SoaJitResultPipeline::BaselineLines *
                fixed_result_context_bytes;
            constexpr size_t incremental_result_contexts_bytes =
                fixed_result_contexts_bytes -
                baseline_result_contexts_bytes;
            constexpr size_t incremental_result_nonpayload_bytes =
                (fixed_result_context_bytes -
                 SoaJitResultPipeline::LineBytes -
                 lookahead_value_bytes_per_context) *
                (SoaJitResultPipeline::MaxLines -
                 SoaJitResultPipeline::BaselineLines);
            constexpr size_t fixed_result_waiter_mask_bytes =
                SoaJitValueCoalescer::MaxWaiters / 8 *
                SoaJitValueCoalescer::CacheLines;
            constexpr size_t baseline_result_waiter_mask_bytes =
                SoaJitResultPipeline::BaselineLines *
                SoaJitValueCoalescer::MaxLookahead / 8 *
                SoaJitValueCoalescer::CacheLines;
            constexpr size_t incremental_result_waiter_mask_bytes =
                fixed_result_waiter_mask_bytes -
                baseline_result_waiter_mask_bytes;
            constexpr size_t incremental_result_total_nonpayload_bytes =
                incremental_result_nonpayload_bytes +
                incremental_result_waiter_mask_bytes;
            constexpr size_t incremental_result_total_state_bytes =
                incremental_result_contexts_bytes +
                incremental_result_waiter_mask_bytes;
            constexpr size_t fixed_max_transient_write_payload_bytes =
                SoaJitContexts * SoaJitResultPipeline::LineBytes;
            constexpr size_t baseline_32_max_transient_write_payload_bytes =
                SoaJitResultPipeline::BaselineLines *
                SoaJitResultPipeline::LineBytes;
            constexpr size_t incremental_max_transient_write_payload_bytes =
                fixed_max_transient_write_payload_bytes -
                baseline_32_max_transient_write_payload_bytes;
            static_assert(SoaJitValueCoalescer::MaxWaiters % 8 == 0);
            static_assert(lookahead_value_bytes_per_context == 64);
            DPRINTF(MAAVirtualTrace,
                    "event=soa_jit_result_pipeline schema=2 unit=%d "
                    "operation_tick=%lu generation=%lu active_contexts=%d "
                    "regions=%lu lines_per_region=%lu "
                    "region_payload_bytes=%lu fixed_result_payload_bytes=%lu "
                    "active_result_payload_bytes=%lu "
                    "incremental_result_payload_bytes_vs_32=%lu "
                    "fixed_lookahead_value_payload_bytes=%lu "
                    "active_lookahead_value_payload_bytes=%lu "
                    "incremental_lookahead_value_payload_bytes_vs_32=%lu "
                    "fixed_max_transient_write_payload_bytes=%lu "
                    "active_max_transient_write_payload_bytes=%lu "
                    "incremental_max_transient_write_payload_bytes_vs_32=%lu "
                    "fixed_result_context_bytes=%lu "
                    "fixed_result_contexts_bytes=%lu "
                    "fixed_result_nonpayload_bytes=%lu "
                    "baseline_32_result_contexts_bytes=%lu "
                    "incremental_result_contexts_bytes_vs_32=%lu "
                    "incremental_result_nonpayload_bytes_vs_32=%lu "
                    "fixed_result_waiter_mask_bytes=%lu "
                    "baseline_32_result_waiter_mask_bytes=%lu "
                    "incremental_result_waiter_mask_bytes_vs_32=%lu "
                    "incremental_result_total_nonpayload_bytes_vs_32=%lu "
                    "incremental_result_total_state_bytes_vs_32=%lu "
                    "a_read_hwm_r0=%u a_read_hwm_r1=%u "
                    "a_write_hwm_r0=%u a_write_hwm_r1=%u "
                    "traffic_hwm_r0=%u traffic_hwm_r1=%u "
                    "read_write_overlap_ticks=%lu "
                    "dual_region_overlap_ticks=%lu "
                    "serialized_write_only_ticks=%lu terminal=1\n",
                    my_indirect_id, my_decode_start_tick,
                    soa_jit_generation, soa_jit_active_contexts,
                    SoaJitResultPipeline::Regions,
                    SoaJitResultPipeline::LinesPerRegion,
                    SoaJitResultPipeline::RegionPayloadBytes,
                    fixed_result_payload_bytes,
                    SoaJitResultPipeline::activePayloadBytes(
                        soa_jit_active_contexts),
                    SoaJitResultPipeline::incrementalPayloadBytesVsBaseline(),
                    fixed_lookahead_value_payload_bytes,
                    static_cast<size_t>(soa_jit_active_contexts) *
                        lookahead_value_bytes_per_context,
                    incremental_lookahead_value_payload_bytes,
                    fixed_max_transient_write_payload_bytes,
                    static_cast<size_t>(soa_jit_active_contexts) *
                        SoaJitResultPipeline::LineBytes,
                    incremental_max_transient_write_payload_bytes,
                    fixed_result_context_bytes,
                    fixed_result_contexts_bytes,
                    fixed_result_nonpayload_bytes,
                    baseline_result_contexts_bytes,
                    incremental_result_contexts_bytes,
                    incremental_result_nonpayload_bytes,
                    fixed_result_waiter_mask_bytes,
                    baseline_result_waiter_mask_bytes,
                    incremental_result_waiter_mask_bytes,
                    incremental_result_total_nonpayload_bytes,
                    incremental_result_total_state_bytes,
                    result_read_hwm[0], result_read_hwm[1],
                    result_write_hwm[0], result_write_hwm[1],
                    result_traffic_hwm[0], result_traffic_hwm[1],
                    soa_jit_result_pipeline.resultReadWriteOverlapTicks(),
                    soa_jit_result_pipeline.dualRegionResultOverlapTicks(),
                    soa_jit_result_pipeline.serializedWriteOnlyTicks());
            DPRINTF(MAAVirtualTrace,
                    "event=soa_jit_complete schema=2 unit=%d "
                    "operation_tick=%lu generation=%lu logical=%d "
                    "selected=%lu predicate_rejected=%lu "
                    "predicate_mode=%s masked_index_compare_bits=%lu "
                    "masked_index_mode_state_bits=%lu "
                    "masked_index_additional_buffer_bytes=%lu "
                    "predicate_lines=%lu/%lu predicate_hits=%lu "
                    "predicate_uses=%lu predicate_stalls=%lu "
                    "predicate_credits=%d predicate_hwm=%lu "
                    "predicate_state_bytes=%lu predicate_host_bytes=%lu "
                    "a_reads=%lu/%lu "
                    "value_reads=%lu/%lu fills=%lu cached=%lu "
                    "hits=%lu merged=%lu evictions=%lu "
                    "deliveries=%lu value_stalls=%lu aliases=%lu "
                    "lookahead=%lu/%lu lookahead_hwm=%lu "
                    "lookahead_stalls=%lu "
                    "pre_a_enable=%d pre_a=%lu/%lu/%lu "
                    "a_writes=%lu/%lu context_hwm=%lu stalls=%lu "
                    "active_contexts=%d active_lookahead=%d "
                    "cache_enable=%d apply_lanes=%d apply_hwm=%lu "
                    "active_value_owners=%d "
                    "max_value_owners=%lu "
                    "context_slots=%lu "
                    "lookahead_slots_per_context=%lu "
                    "value_prefetch=%lu/%lu prefetch_promotions=%lu "
                    "prefetch_discards=%lu prefetch_owned=%lu "
                    "prefetch_credit_stalls=%lu prefetch_credits=%d "
                    "prefetch_hwm=%lu "
                    "terminal=1\n",
                    my_indirect_id, my_decode_start_tick,
                    soa_jit_generation, my_max, soa_jit_selected,
                    soa_jit_predicate_rejected,
                    isSoaJitMaskedIndexRmw() ? "masked_index" :
                        my_predicate_addr == 0 ? "unpredicated" :
                                                 "separate_array",
                    static_cast<unsigned long>(
                        isSoaJitMaskedIndexRmw()
                            ? SoaJitSafety::MaskedIndexCompareBits : 0),
                    static_cast<unsigned long>(
                        isSoaJitMaskedIndexRmw()
                            ? SoaJitSafety::MaskedIndexModeStateBits : 0),
                    static_cast<unsigned long>(
                        SoaJitSafety::MaskedIndexAdditionalBufferBytes),
                    soa_jit_predicate_line_issues,
                    soa_jit_predicate_line_responses,
                    soa_jit_predicate_line_hits,
                    soa_jit_predicate_uses,
                    soa_jit_predicate_feeder_stalls,
                    soa_jit_predicate_active_credits,
                    soa_jit_predicate_feeder_high_water,
                    static_cast<unsigned long>(
                        SoaPredicateFeederStateBytes),
                    static_cast<unsigned long>(
                        sizeof(soa_predicate_lines)),
                    soa_jit_a_read_issues, soa_jit_a_read_responses,
                    soa_jit_value_read_issues,
                    soa_jit_value_read_responses,
                    soa_jit_value_fills,
                    soa_jit_value_cached_responses,
                    soa_jit_value_hits,
                    soa_jit_value_merged_waiters,
                    soa_jit_value_evictions,
                    soa_jit_value_deliveries,
                    soa_jit_value_stalls,
                    soa_jit_aliases_applied,
                    soa_jit_lookahead_issues,
                    soa_jit_lookahead_responses,
                    soa_jit_lookahead_high_water,
                    soa_jit_lookahead_stalls,
                    soa_jit_pre_a_value_lookahead,
                    soa_jit_pre_a_value_issues,
                    soa_jit_pre_a_value_ready_at_a_response,
                    soa_jit_pre_a_value_uses,
                    soa_jit_a_write_issues,
                    soa_jit_a_write_responses,
                    soa_jit_context_high_water,
                    soa_jit_context_stalls,
                    soa_jit_active_contexts,
                    soa_jit_value_lookahead,
                    soa_jit_value_cache_enable,
                    soa_jit_apply_lanes,
                    soa_jit_apply_lane_high_water,
                    soa_jit_active_value_owners,
                    SoaJitValueCoalescer::MaxOwners,
                    SoaJitContexts,
                    SoaJitValueCoalescer::MaxLookahead,
                    soa_jit_value_prefetch_issues,
                    soa_jit_value_prefetch_responses,
                    soa_jit_value_prefetch_promotions,
                    soa_jit_value_prefetch_discards,
                    soa_jit_value_prefetch_owned,
                    soa_jit_value_prefetch_credit_stalls,
                    soa_jit_value_prefetch_credits,
                    soa_jit_value_prefetch_high_water);
            constexpr size_t fixed_context_bytes = sizeof(SoaJitContext);
            constexpr size_t fixed_contexts_bytes =
                sizeof(soa_jit_contexts);
            static_assert(
                SoaJitContexts == SoaJitResultPipeline::MaxLines,
                "SoA/JIT contexts must equal the fixed result-line budget");
            static_assert(
                SoaJitResultPipeline::FixedPayloadBytes ==
                    SoaJitContexts * SoaJitResultPipeline::LineBytes,
                "SoA/JIT result payload must remain exactly 4 KiB");
            const size_t active_contexts_bytes =
                static_cast<size_t>(soa_jit_active_contexts) *
                fixed_context_bytes;
            constexpr size_t fixed_value_owner_bytes =
                sizeof(SoaJitValueCoalescer);
            constexpr size_t fixed_value_owner_entry_bytes =
                sizeof(SoaJitValueCoalescer::CacheLine);
            constexpr size_t fixed_value_owner_payload_bytes =
                SoaJitValueCoalescer::MaxOwners *
                SoaJitValueCoalescer::LineBytes;
            constexpr size_t fixed_value_owner_nonpayload_bytes =
                fixed_value_owner_bytes - fixed_value_owner_payload_bytes;
            constexpr size_t baseline_32_value_owner_bytes =
                fixed_value_owner_bytes -
                (SoaJitValueCoalescer::MaxOwners -
                 SoaJitValueCoalescer::BaselineOwners) *
                    fixed_value_owner_entry_bytes;
            constexpr size_t incremental_value_owner_bytes_vs_32 =
                fixed_value_owner_bytes - baseline_32_value_owner_bytes;
            const size_t selected_value_owner_entry_bytes =
                static_cast<size_t>(soa_jit_active_value_owners) *
                fixed_value_owner_entry_bytes;
            const size_t fixed_value_owner_bytes_per_maa =
                fixed_value_owner_bytes * maa->num_indirect_units_per_maa;
            const size_t incremental_value_owner_bytes_vs_32_per_maa =
                incremental_value_owner_bytes_vs_32 *
                maa->num_indirect_units_per_maa;
            const size_t selected_value_owner_entry_bytes_per_maa =
                selected_value_owner_entry_bytes *
                maa->num_indirect_units_per_maa;
            constexpr size_t fixed_apply_lane_owner_bytes =
                sizeof(SoaJitApplyLanePool::Owner);
            constexpr size_t fixed_apply_lane_pool_bytes =
                sizeof(SoaJitApplyLanePool);
            constexpr size_t fixed_prefetch_credit_bytes =
                sizeof(SoaJitValueCoalescer::PrefetchCredit) *
                SoaJitValueCoalescer::MaxPrefetchCredits;
            constexpr size_t fixed_prefetch_cursor_bytes =
                sizeof(SoaJitValuePrefetchCursor);
            constexpr size_t baseline_predicate_lines = 1;
            constexpr size_t baseline_predicate_modeled_bytes =
                baseline_predicate_lines * SoaPredicateLineStateBytes;
            constexpr size_t incremental_predicate_modeled_bytes =
                SoaPredicateFeederStateBytes -
                baseline_predicate_modeled_bytes;
            constexpr size_t incremental_overlap_bytes =
                fixed_contexts_bytes + fixed_value_owner_bytes +
                fixed_apply_lane_pool_bytes +
                fixed_prefetch_cursor_bytes;
            DPRINTF(MAAVirtualTrace,
                    "event=soa_jit_storage schema=2 unit=%d "
                    "operation_tick=%lu generation=%lu "
                    "fixed_context_bytes=%lu fixed_contexts=%lu "
                    "fixed_contexts_bytes=%lu "
                    "active_contexts_bytes=%lu "
                    "max_physical_value_owner_lines=%lu "
                    "fixed_value_owner_bytes=%lu "
                    "fixed_value_owner_entry_bytes=%lu "
                    "fixed_value_owner_payload_bytes=%lu "
                    "fixed_value_owner_nonpayload_bytes=%lu "
                    "baseline_32_value_owner_bytes=%lu "
                    "incremental_value_owner_bytes_vs_32_per_unit=%lu "
                    "fixed_value_owner_bytes_per_maa=%lu "
                    "incremental_value_owner_bytes_vs_32_per_maa=%lu "
                    "fixed_apply_lanes=%lu active_apply_lanes=%d "
                    "active_apply_lane_hwm=%lu "
                    "fixed_apply_lane_owner_bytes=%lu "
                    "fixed_apply_lane_pool_bytes=%lu "
                    "fixed_predicate_lines=%lu "
                    "fixed_predicate_modeled_bytes=%lu "
                    "fixed_predicate_host_bytes=%lu "
                    "baseline_predicate_lines=%lu "
                    "baseline_predicate_modeled_bytes=%lu "
                    "incremental_predicate_modeled_bytes=%lu "
                    "predicate_active_credits=%d "
                    "index_active_lines=%d index_words_per_line=%d "
                    "index_word_bytes=%lu index_active_data_tag_bytes=%lu "
                    "incremental_overlap_bytes=%lu "
                    "active_contexts=%d active_lookahead=%d "
                    "active_value_owners=%d "
                    "active_value_owner_payload_bytes=%lu "
                    "selected_value_owner_entry_bytes_per_unit=%lu "
                    "selected_value_owner_entry_bytes_per_maa=%lu "
                    "cache_enable=%d fixed_prefetch_credits=%lu "
                    "fixed_prefetch_credit_bytes=%lu "
                    "active_prefetch_credits=%d "
                    "fixed_prefetch_cursor_bytes=%lu\n",
                    my_indirect_id, my_decode_start_tick,
                    soa_jit_generation, fixed_context_bytes,
                    SoaJitContexts, fixed_contexts_bytes,
                    active_contexts_bytes,
                    SoaJitValueCoalescer::MaxOwners,
                    fixed_value_owner_bytes,
                    fixed_value_owner_entry_bytes,
                    fixed_value_owner_payload_bytes,
                    fixed_value_owner_nonpayload_bytes,
                    baseline_32_value_owner_bytes,
                    incremental_value_owner_bytes_vs_32,
                    fixed_value_owner_bytes_per_maa,
                    incremental_value_owner_bytes_vs_32_per_maa,
                    SoaJitApplyLanePool::MaxLanes,
                    soa_jit_apply_lanes,
                    soa_jit_apply_lane_high_water,
                    fixed_apply_lane_owner_bytes,
                    fixed_apply_lane_pool_bytes,
                    SoaPredicateMaxLines,
                    SoaPredicateFeederStateBytes,
                    sizeof(soa_predicate_lines),
                    baseline_predicate_lines,
                    baseline_predicate_modeled_bytes,
                    incremental_predicate_modeled_bytes,
                    soa_jit_predicate_active_credits,
                    direct_index_buffer_lines, my_words_per_cl,
                    sizeof(DirectIndexWord),
                    sizeof(DirectIndexWord) * my_words_per_cl *
                        direct_index_buffer_lines,
                    incremental_overlap_bytes,
                    soa_jit_active_contexts, soa_jit_value_lookahead,
                    soa_jit_active_value_owners,
                    static_cast<size_t>(soa_jit_active_value_owners) *
                        SoaJitValueCoalescer::LineBytes,
                    selected_value_owner_entry_bytes,
                    selected_value_owner_entry_bytes_per_maa,
                    soa_jit_value_cache_enable,
                    SoaJitValueCoalescer::MaxPrefetchCredits,
                    fixed_prefetch_credit_bytes,
                    soa_jit_value_prefetch_credits,
                    fixed_prefetch_cursor_bytes);
        }
        if (maa->virtual_bounded_global_merge) {
            const bool read_slots_empty = std::none_of(
                bounded_global_merge_read_slots.begin(),
                bounded_global_merge_read_slots.end(),
                [](const auto &slot) { return slot.valid; });
            const bool write_slots_empty = std::none_of(
                bounded_global_merge_write_slots.begin(),
                bounded_global_merge_write_slots.end(),
                [](const auto &slot) { return slot.valid; });
            panic_if(
                bounded_global_merge_phase !=
                        BoundedGlobalMergePhase::Complete ||
                    !bounded_global_merge.configured() ||
                    bounded_global_merge.materializing() ||
                    bounded_global_merge.merging() ||
                    bounded_global_merge.population(0) != 4096 ||
                    bounded_global_merge.population(1) != 4096 ||
                    bounded_global_merge.population(2) != 4096 ||
                    bounded_global_merge.population(3) != 4096 ||
                    bounded_global_merge.activeHighWater() >
                        BoundedFourRunMerge::MaxActiveDescriptors ||
                    bounded_global_merge.materializedRecords() !=
                        static_cast<uint32_t>(my_max) ||
                    bounded_global_merge.sortedWriteLines() != 1536 ||
                    bounded_global_merge.sortedWriteAcks() != 1536 ||
                    bounded_global_merge.readLines() != 1536 ||
                    bounded_global_merge.readRecords() !=
                        static_cast<uint32_t>(my_max) ||
                    bounded_global_merge.headHighWater() >
                        BoundedFourRunMerge::Runs ||
                    bounded_global_merge.maxMaterializationCarryBytes() >
                        BoundedFourRunMerge::MaxCarryBytes ||
                    bounded_global_merge.maxReaderCarryBytes() >
                        BoundedFourRunMerge::MaxCarryBytes ||
                    bounded_global_merge.sourceLineIssues() !=
                        bounded_global_merge_source_responses ||
                    bounded_global_merge.sourceLineIssues() +
                            bounded_global_merge.coalescedDescriptors() !=
                        static_cast<uint32_t>(my_max) ||
                    bounded_global_merge.retiredDescriptors() !=
                        static_cast<uint32_t>(my_max) ||
                    bounded_range_pass.admissions() !=
                        static_cast<uint32_t>(my_max) ||
                    bounded_range_pass.retirements() !=
                        static_cast<uint32_t>(my_max) ||
                    bounded_global_merge.outstandingWriteCount() != 0 ||
                    bounded_global_merge_batch_inflight ||
                    bounded_global_merge_source_pending ||
                    bounded_global_merge_source_ready ||
                    bounded_global_merge_source_head != -1 ||
                    bounded_global_merge_source_tail != -1 ||
                    bounded_global_merge_source_words != 0 ||
                    !read_slots_empty || !write_slots_empty,
                "I[%d] bounded global merge failed terminal accounting\n",
                my_indirect_id);
            (*maa->stats
                  .IND_BoundedGlobalMergePopulations[my_indirect_id]) +=
                BoundedFourRunMerge::Runs;
            (*maa->stats
                  .IND_BoundedGlobalMergeActiveHWM[my_indirect_id]) +=
                bounded_global_merge.activeHighWater();
            (*maa->stats
                  .IND_BoundedGlobalMergeDescriptorRecords[my_indirect_id]) +=
                bounded_global_merge.materializedRecords();
            (*maa->stats
                  .IND_BoundedGlobalMergeDescriptorBytes[my_indirect_id]) +=
                static_cast<uint64_t>(
                    bounded_global_merge.materializedRecords()) *
                BoundedFourRunMerge::DescriptorBytes;
            (*maa->stats
                  .IND_BoundedGlobalMergeSortReadLines[my_indirect_id]) +=
                descriptor_spool.readLinesIssued();
            (*maa->stats
                  .IND_BoundedGlobalMergeSortedWriteLines[my_indirect_id]) +=
                bounded_global_merge.sortedWriteLines();
            (*maa->stats
                  .IND_BoundedGlobalMergeSortComparisons[my_indirect_id]) +=
                bounded_global_merge_sort_comparisons;
            (*maa->stats
                  .IND_BoundedGlobalMergeMergeReadLines[my_indirect_id]) +=
                bounded_global_merge.readLines();
            (*maa->stats
                  .IND_BoundedGlobalMergeMergeComparisons[my_indirect_id]) +=
                bounded_global_merge.comparisons();
            (*maa->stats
                  .IND_BoundedGlobalMergeMergeHeadHWM[my_indirect_id]) +=
                bounded_global_merge.headHighWater();
            (*maa->stats
                  .IND_BoundedGlobalMergeALineIssues[my_indirect_id]) +=
                bounded_global_merge.sourceLineIssues();
            (*maa->stats
                  .IND_BoundedGlobalMergeCoalesced[my_indirect_id]) +=
                bounded_global_merge.coalescedDescriptors();
            (*maa->stats
                  .IND_BoundedGlobalMergeRowGroups[my_indirect_id]) +=
                bounded_global_merge_row_groups;
            (*maa->stats
                  .IND_BoundedGlobalMergeAdmissions[my_indirect_id]) +=
                bounded_range_pass.admissions();
            (*maa->stats
                  .IND_BoundedGlobalMergeRetirements[my_indirect_id]) +=
                bounded_global_merge.retiredDescriptors();
            (*maa->stats
                  .IND_BoundedGlobalMergeRunWriteAcks[my_indirect_id]) +=
                bounded_global_merge.sortedWriteAcks();
            (*maa->stats
                  .IND_BoundedGlobalMergeTerminalAcks[my_indirect_id]) +=
                bounded_global_merge_terminal_acks;
            (*maa->stats
                  .IND_BoundedGlobalMergeFallbacks[my_indirect_id]) += 0;
            (*maa->stats
                  .IND_BoundedGlobalMergeControlBytes[my_indirect_id]) +=
                bounded_global_merge.chargedControlBytes();
            (*maa->stats
                  .IND_BoundedGlobalMergeBackingBytes[my_indirect_id]) +=
                BoundedFourRunMerge::RequiredBackingBytes;
            DPRINTF(MAAVirtualTrace,
                    "event=bounded_global_merge_complete schema=1 unit=%d "
                    "operation_tick=%lu populations=4 "
                    "population_0=%u population_1=%u population_2=%u "
                    "population_3=%u active_hwm=%u records=%u "
                    "record_bytes=%lu sort_read_lines=%u "
                    "sorted_write_lines=%u sort_comparisons=%lu "
                    "merge_read_lines=%u merge_comparisons=%lu "
                    "head_hwm=%u a_line_issues=%u coalesced=%u "
                    "row_groups=%u admissions=%u retirements=%u "
                    "run_write_acks=%u terminal_acks=%u "
                    "sort_carry_hwm=%u merge_carry_hwm=%u fallback=0 "
                    "control_bytes=%lu backing_bytes=%lu mode=timing\n",
                    my_indirect_id, my_decode_start_tick,
                    bounded_global_merge.population(0),
                    bounded_global_merge.population(1),
                    bounded_global_merge.population(2),
                    bounded_global_merge.population(3),
                    bounded_global_merge.activeHighWater(),
                    bounded_global_merge.materializedRecords(),
                    static_cast<uint64_t>(
                        bounded_global_merge.materializedRecords()) *
                        BoundedFourRunMerge::DescriptorBytes,
                    descriptor_spool.readLinesIssued(),
                    bounded_global_merge.sortedWriteLines(),
                    bounded_global_merge_sort_comparisons,
                    bounded_global_merge.readLines(),
                    bounded_global_merge.comparisons(),
                    bounded_global_merge.headHighWater(),
                    bounded_global_merge.sourceLineIssues(),
                    bounded_global_merge.coalescedDescriptors(),
                    bounded_global_merge_row_groups,
                    bounded_range_pass.admissions(),
                    bounded_global_merge.retiredDescriptors(),
                    bounded_global_merge.sortedWriteAcks(),
                    bounded_global_merge_terminal_acks,
                    bounded_global_merge.maxMaterializationCarryBytes(),
                    bounded_global_merge.maxReaderCarryBytes(),
                    static_cast<unsigned long>(
                        bounded_global_merge.chargedControlBytes()),
                    BoundedFourRunMerge::RequiredBackingBytes);
        }
        if (descriptor_spool.configured()) {
            panic_if(descriptor_spool.classifiedDescriptors() !=
                         static_cast<uint32_t>(my_max) ||
                         descriptor_spool.residentDescriptors() != 4096 ||
                         descriptor_spool.externalDescriptors() != 12288 ||
                         descriptor_spool.externalSegments() != 3 ||
                         descriptor_spool.descriptorsConsumed() !=
                             descriptor_spool.externalDescriptors() ||
                         descriptor_spool.readLinesIssued() !=
                             descriptor_spool.readLineResponses() ||
                         descriptor_spool.writeLinesIssued() !=
                             descriptor_spool.writeAcks() ||
                         descriptor_spool.writeLinesIssued() != 1152 ||
                         descriptor_spool.readLinesIssued() != 1152 ||
                         descriptor_spool.externalPayloadBytes() != 73728 ||
                         direct_index_summary_next_iteration !=
                             static_cast<uint32_t>(my_max) ||
                         descriptor_spool_bucket_commits !=
                             static_cast<uint32_t>(my_max) ||
                         descriptor_spool_bucket_attempts <
                             descriptor_spool_bucket_commits ||
                         descriptor_spool_bucket_attempts !=
                             descriptor_spool_bucket_commits +
                                 descriptor_spool_filter_retry_inspections ||
                         direct_index_max_lines >
                             std::max(
                                 direct_index_buffer_lines,
                                 static_cast<int>(
                                     descriptor_spool.readCredits())) ||
                         descriptorSpoolReadSlotsUsed() != 0 ||
                         std::any_of(
                             descriptor_spool_read_slots.begin(),
                             descriptor_spool_read_slots.end(),
                             [](const auto &slot) {
                                 return slot.demand_observed;
                             }) ||
                         descriptorSpoolWriteSlotsUsed() != 0 ||
                         descriptor_spool_current_valid ||
                         descriptor_spool_replay_active ||
                         descriptor_spool_read_ahead_active ||
                         descriptor_spool_prefetch_occupancy != 0 ||
                         descriptor_spool_prefetch_occupancy_tick != 0 ||
                         descriptor_spool_demand_wait_active ||
                         descriptor_spool_next_pass_read_issues !=
                             descriptor_spool_next_pass_read_responses ||
                         descriptor_spool_next_pass_read_issues !=
                             descriptor_spool_useful_prefetched_lines +
                                 descriptor_spool_wasted_prefetched_lines ||
                         descriptor_spool_demand_waits_avoided >
                             descriptor_spool_useful_prefetched_lines ||
                         descriptor_spool_prefetch_occupancy_hwm >
                             descriptor_spool.readCredits() ||
                         (!maa->virtual_descriptor_spool_read_ahead &&
                          (descriptor_spool_overlap_opportunities != 0 ||
                           descriptor_spool_next_pass_read_issues != 0 ||
                           descriptor_spool_next_pass_read_responses != 0 ||
                           descriptor_spool_useful_prefetched_lines != 0 ||
                           descriptor_spool_demand_waits_avoided != 0 ||
                           descriptor_spool_prefetch_occupancy_hwm != 0 ||
                           descriptor_spool_wasted_prefetched_lines != 0)),
                     "I[%d] descriptor spool failed terminal accounting\n",
                     my_indirect_id);
            (*maa->stats
                  .IND_DescriptorSpoolWriteHighWater[my_indirect_id]) +=
                descriptor_spool.outstandingWriteHighWater();
            (*maa->stats
                  .IND_DescriptorSpoolOverlapOpportunities[my_indirect_id]) +=
                descriptor_spool_overlap_opportunities;
            (*maa->stats
                  .IND_DescriptorSpoolNextPassReadIssues[my_indirect_id]) +=
                descriptor_spool_next_pass_read_issues;
            (*maa->stats
                  .IND_DescriptorSpoolNextPassReadResponses[my_indirect_id]) +=
                descriptor_spool_next_pass_read_responses;
            (*maa->stats
                  .IND_DescriptorSpoolUsefulPrefetchedLines[my_indirect_id]) +=
                descriptor_spool_useful_prefetched_lines;
            (*maa->stats
                  .IND_DescriptorSpoolDemandWaitsAvoided[my_indirect_id]) +=
                descriptor_spool_demand_waits_avoided;
            (*maa->stats.IND_DescriptorSpoolPrefetchOccupancyLineCycles
                  [my_indirect_id]) += maa->getTicksToCycles(
                descriptor_spool_prefetch_occupancy_line_ticks);
            (*maa->stats.IND_DescriptorSpoolPrefetchOccupancyHighWater
                  [my_indirect_id]) +=
                descriptor_spool_prefetch_occupancy_hwm;
            (*maa->stats
                  .IND_DescriptorSpoolWastedPrefetchedLines[my_indirect_id]) +=
                descriptor_spool_wasted_prefetched_lines;
            (*maa->stats.IND_DescriptorSpoolBoundaryDemandWaitEvents
                  [my_indirect_id]) +=
                descriptor_spool_boundary_demand_wait_events;
            (*maa->stats.IND_DescriptorSpoolBoundaryDemandWaitCycles
                  [my_indirect_id]) += maa->getTicksToCycles(
                descriptor_spool_boundary_demand_wait_ticks);
            (*maa->stats.IND_DescriptorSpoolWithinPassDemandWaitEvents
                  [my_indirect_id]) +=
                descriptor_spool_within_pass_demand_wait_events;
            (*maa->stats.IND_DescriptorSpoolWithinPassDemandWaitCycles
                  [my_indirect_id]) += maa->getTicksToCycles(
                descriptor_spool_within_pass_demand_wait_ticks);
            (*maa->stats
                  .IND_DescriptorSpoolStagingEntries[my_indirect_id]) +=
                descriptor_spool.activeStagingDescriptorCapacity();
            (*maa->stats
                  .IND_DescriptorSpoolControlBytes[my_indirect_id]) +=
                descriptorSpoolControlBytes();
            (*maa->stats
                  .IND_DescriptorSpoolBackingBytes[my_indirect_id]) +=
                descriptor_spool.reservedBackingBytes();
            (*maa->stats.IND_DescriptorSpoolBScans[my_indirect_id]) += 2;
            (*maa->stats
                  .IND_DescriptorSpoolResidentPopulations[my_indirect_id])++;
            (*maa->stats
                  .IND_DescriptorSpoolResidentDescriptors[my_indirect_id]) +=
                descriptor_spool.residentDescriptors();
            (*maa->stats
                  .IND_DescriptorSpoolExternalDescriptors[my_indirect_id]) +=
                descriptor_spool.externalDescriptors();
            (*maa->stats
                  .IND_DescriptorSpoolExternalSegments[my_indirect_id]) +=
                descriptor_spool.externalSegments();
            DPRINTF(MAAVirtualTrace,
                    "event=descriptor_spool_complete schema=2 unit=%d "
                    "operation_tick=%lu b_scans=2 descriptors=%u "
                    "resident_pass=%u resident_descriptors=%u "
                    "external_descriptors=%u external_segments=%u "
                    "descriptor_bytes=%u payload_bytes=%lu "
                    "write_lines=%u write_acks=%u read_lines=%u "
                    "read_responses=%u control_bytes=%lu "
                    "backing_bytes=%lu staging_bytes=%u write_hwm=%u "
                    "read_hwm=%u unique_inspections=%lu "
                    "retry_inspections=%lu final_flush_stalls=%lu "
                    "read_ahead=%d overlap_opportunities=%u "
                    "next_pass_read_issues=%u next_pass_read_responses=%u "
                    "useful_prefetched_lines=%u demand_waits_avoided=%u "
                    "prefetch_occupancy=0 prefetch_occupancy_hwm=%u "
                    "prefetch_occupancy_line_cycles=%lu wasted_lines=%u "
                    "boundary_wait_events=%u boundary_wait_cycles=%lu "
                    "within_pass_wait_events=%u "
                    "within_pass_wait_cycles=%lu "
                    "active_limit=%u "
                    "identity_check=trace_side fallback=none\n",
                    my_indirect_id, my_decode_start_tick,
                    descriptor_spool.classifiedDescriptors(),
                    descriptor_spool.residentPass(),
                    descriptor_spool.residentDescriptors(),
                    descriptor_spool.externalDescriptors(),
                    descriptor_spool.externalSegments(),
                    BoundedDescriptorSpool::DescriptorBytes,
                    descriptor_spool.externalPayloadBytes(),
                    descriptor_spool.writeLinesIssued(),
                    descriptor_spool.writeAcks(),
                    descriptor_spool.readLinesIssued(),
                    descriptor_spool.readLineResponses(),
                    descriptorSpoolControlBytes(),
                    descriptor_spool.reservedBackingBytes(),
                    descriptor_spool.activeStagingBytes(),
                    descriptor_spool.outstandingWriteHighWater(),
                    descriptor_spool.outstandingReadHighWater(),
                    descriptor_spool_bucket_commits,
                    descriptor_spool_filter_retry_inspections,
                    descriptor_spool_final_flush_stalls,
                    maa->virtual_descriptor_spool_read_ahead,
                    descriptor_spool_overlap_opportunities,
                    descriptor_spool_next_pass_read_issues,
                    descriptor_spool_next_pass_read_responses,
                    descriptor_spool_useful_prefetched_lines,
                    descriptor_spool_demand_waits_avoided,
                    descriptor_spool_prefetch_occupancy_hwm,
                    static_cast<uint64_t>(maa->getTicksToCycles(
                        descriptor_spool_prefetch_occupancy_line_ticks)),
                    descriptor_spool_wasted_prefetched_lines,
                    descriptor_spool_boundary_demand_wait_events,
                    static_cast<uint64_t>(maa->getTicksToCycles(
                        descriptor_spool_boundary_demand_wait_ticks)),
                    descriptor_spool_within_pass_demand_wait_events,
                    static_cast<uint64_t>(maa->getTicksToCycles(
                        descriptor_spool_within_pass_demand_wait_ticks)),
                    BoundedDescriptorSpool::MaxActiveDescriptors);
            descriptor_spool.reset();
            descriptor_spool_bucket_active = false;
            descriptor_spool_bucket_scan_complete = false;
            descriptor_spool_replay_active = false;
            descriptor_spool_read_ahead_active = false;
            descriptor_spool_overlap_opportunity_recorded = false;
            descriptor_spool_prefetch_occupancy = 0;
            descriptor_spool_prefetch_occupancy_tick = 0;
            descriptor_spool_demand_wait_active = false;
            descriptor_spool_demand_wait_boundary = false;
            descriptor_spool_demand_wait_tick = 0;
            descriptor_spool_demand_wait_cursor = 0;
            descriptor_spool_base_vaddr = 0;
            descriptor_spool_index_page_paddrs.fill(0);
            descriptor_spool_index_page_valid.fill(false);
        }
        if (maa->virtual_bounded_global_merge) {
            bounded_global_merge.reset();
            bounded_global_merge_phase = BoundedGlobalMergePhase::None;
            bounded_global_merge_run = 0;
            bounded_global_merge_slice_cursor = 0;
            bounded_global_merge_chain_head = -1;
            bounded_global_merge_sort_comparisons = 0;
            bounded_global_merge_row_groups = 0;
            bounded_global_merge_source_responses = 0;
            bounded_global_merge_terminal_acks = 0;
            bounded_global_merge_batch_inflight = false;
            bounded_global_merge_last_key_valid = false;
            bounded_global_merge_last_key.fill(0);
            bounded_global_merge_last_row_valid = false;
            bounded_global_merge_last_slice = 0;
            bounded_global_merge_last_row = 0;
            for (auto &slot : bounded_global_merge_read_slots)
                slot = BoundedGlobalMergeReadSlot();
            for (auto &slot : bounded_global_merge_write_slots)
                slot = BoundedGlobalMergeWriteSlot();
            bounded_global_merge_source_pending = false;
            bounded_global_merge_source_ready = false;
            bounded_global_merge_source_paddr = 0;
            bounded_global_merge_source_vaddr = 0;
            bounded_global_merge_source_head = -1;
            bounded_global_merge_source_tail = -1;
            bounded_global_merge_source_words = 0;
            bounded_global_merge_source_data.fill(0);
        }
        finishReorderSurvival();
        DPRINTF(MAAIssueDigest,
                "unit=%d instruction_tick=%lu count=%lu "
                "fnv=0x%016lx mix=0x%016lx\n",
                my_indirect_id, my_decode_start_tick,
                source_issue_sequence, source_issue_digest,
                source_issue_digest_secondary);
        DPRINTF(MAAVirtualTrace,
                "event=indirect_counter_summary schema=2 unit=%d "
                "occurrence=%lu "
                "operation_tick=%lu row_attempts=%lu row_successes=%lu "
                "offset_pressure=%lu row_pressure=%lu "
                "source_issues=%lu source_responses=%d "
                "combiner_words=%lu write_issues=%lu "
                "write_completions=%lu\n",
                my_indirect_id, attribution_event_occurrence++,
                my_decode_start_tick,
                attribution_row_insert_attempts,
                attribution_row_insert_successes,
                attribution_offset_pressure_events,
                attribution_row_pressure_events, source_issue_sequence,
                virtual_source_received, attribution_combiner_words,
                attribution_write_issues, attribution_write_completions);
        panic_if(scheduleNextExecution(),
                 "I[%d] %s: Execution is not completed!\n",
                 my_indirect_id, __func__);
        panic_if(!maa->allIndirectPacketsSent(my_indirect_id),
                 "All indirect packets are not sent!\n");
        panic_if(!my_cond_tile_ready,
                 "I[%d] %s: cond tile[%d] is not ready!\n",
                 my_indirect_id, __func__, my_cond_tile);
        panic_if(!my_idx_tile_ready,
                 "I[%d] %s: idx tile[%d] is not ready!\n",
                 my_indirect_id, __func__, my_idx_tile);
        panic_if(!my_src_tile_ready,
                 "I[%d] %s: src tile[%d] is not ready!\n",
                 my_indirect_id, __func__, my_src_tile);
        panic_if(!LoadsCacheHitRespondingTimeHistory.empty(),
                 "I[%d] %s: cache-responding history is not empty!\n",
                 my_indirect_id, __func__);
        panic_if(!LoadsCacheHitAccessingTimeHistory.empty(),
                 "I[%d] %s: cache-accessing history is not empty!\n",
                 my_indirect_id, __func__);
        panic_if(!LoadsMemAccessingTimeHistory.empty(),
                 "I[%d] %s: memory-accessing history is not empty!\n",
                 my_indirect_id, __func__);
        DPRINTF(MAAIndirect,
                "I[%d] %s: state set to finish for request %s!\n",
                my_indirect_id, __func__, my_instruction->print());
        my_instruction->state = Instruction::Status::Finish;
        if (my_request_start_tick != 0) {
            finishVirtualRequestInterval();
            (*maa->stats.IND_CyclesRequest[my_indirect_id]) +=
                maa->getTicksToCycles(curTick() - my_request_start_tick);
            my_request_start_tick = 0;
        }
        Cycles total_cycles =
            maa->getTicksToCycles(curTick() - my_decode_start_tick);
        transitionAttributionStage(AttributionStage::None,
                                   "instruction_complete");
        maa->stats.cycles += total_cycles;
        my_decode_start_tick = 0;
        state = Status::Idle;
        check_reset();
        maa->finishInstructionCompute(my_instruction);
        if (my_instruction->opcode == Instruction::OpcodeType::INDIR_LD ||
            my_instruction->opcode ==
                Instruction::OpcodeType::INDIR_LD_INDEX ||
            isVirtualLoad()) {
            maa->stats.cycles_INDRD += total_cycles;
        } else if (my_instruction->opcode == Instruction::OpcodeType::INDIR_ST_SCALAR ||
                   my_instruction->opcode == Instruction::OpcodeType::INDIR_ST_VECTOR) {
            maa->stats.cycles_INDWR += total_cycles;
        } else {
            maa->stats.cycles_INDRMW += total_cycles;
        }
        if (descriptor_spool_operation || isSoaJitRmw()) {
            panic_if(!my_unique_WORD_addrs.empty() ||
                         !my_unique_CL_addrs.empty() ||
                         !my_unique_ROW_addrs.empty(),
                     "I[%d] bounded indirect path populated legacy "
                     "uniqueness sets\n",
                     my_indirect_id);
            DPRINTF(MAAVirtualTrace,
                    "event=bounded_identity_accounting schema=1 unit=%d "
                    "operation_tick=%lu identity_check=trace_side "
                    "legacy_unique_sets=suppressed path=%s\n",
                    my_indirect_id, my_decode_start_tick,
                    isSoaJitRmw() ? "soa_jit" : "descriptor_spool");
        } else {
            setRowTableConfig(my_base_addr, my_unique_CL_addrs.size(),
                              my_unique_ROW_addrs.size());
            (*maa->stats.IND_NumUniqueWordsInserted[my_indirect_id]) +=
                my_unique_WORD_addrs.size();
            (*maa->stats.IND_NumUniqueCacheLineInserted[my_indirect_id]) +=
                my_unique_CL_addrs.size();
            (*maa->stats.IND_NumUniqueRowsInserted[my_indirect_id]) +=
                my_unique_ROW_addrs.size();
        }
        my_unique_WORD_addrs.clear();
        my_unique_CL_addrs.clear();
        my_unique_ROW_addrs.clear();
        descriptor_spool_operation = false;
        soa_jit_operation_active = false;
        my_instruction = nullptr;
        break;
    }
    default:
        assert(false);
    }
}
bool IndirectAccessUnit::checkAndResetAllRowTablesSent() {
    for (int i = 0; i < num_RT_slices[my_RT_config]; i++) {
        if (my_RT_req_sent[my_RT_config][i] == false) {
            return false;
        }
    }
    for (int i = 0; i < num_RT_slices[my_RT_config]; i++) {
        if (!maa->virtual_native_issue_order)
            my_RT_req_sent[my_RT_config][i] = false;
    }
    return true;
}
void IndirectAccessUnit::createReadPacket(Addr addr, int latency) {
    if (isVirtualLoad()) {
        if (macro_a_first_issue_tick == 0)
            macro_a_first_issue_tick = curTick();
        macro_a_last_issue_tick = curTick();
        macro_a_lines++;
        macro_a_bytes += block_size;
    }
    const uint64_t sequence = source_issue_sequence++;
    for (int byte = 0; byte < 8; ++byte) {
        source_issue_digest ^=
            (static_cast<uint64_t>(addr) >> (byte * 8)) & 0xff;
        source_issue_digest *= 1099511628211ULL;
    }
    uint64_t mixed = static_cast<uint64_t>(addr) ^
        (sequence * 0x9e3779b97f4a7c15ULL);
    mixed ^= mixed >> 30;
    mixed *= 0xbf58476d1ce4e5b9ULL;
    mixed ^= mixed >> 27;
    mixed *= 0x94d049bb133111ebULL;
    mixed ^= mixed >> 31;
    source_issue_digest_secondary ^=
        mixed + 0x9e3779b97f4a7c15ULL +
        (source_issue_digest_secondary << 6) +
        (source_issue_digest_secondary >> 2);
    DPRINTF(MAAIssueTrace,
            "unit=%d instruction_tick=%lu sequence=%d addr=0x%lx "
            "bounded=%d virtual=%d direct_index=%d\n",
            my_indirect_id, my_decode_start_tick,
            sequence, addr, usesBoundedSourceResponses(),
            isVirtualLoad(), isDirectIndexLoad());
    DPRINTF(MAAVirtualTrace,
            "event=source_issue schema=2 unit=%d occurrence=%lu "
            "operation_tick=%lu "
            "sequence=%lu addr=0x%lx bounded=%d virtual=%d\n",
            my_indirect_id, attribution_event_occurrence++,
            my_decode_start_tick, sequence, addr,
            usesBoundedSourceResponses(), isVirtualLoad());
    /**** Packet generation ****/
    RequestPtr real_req = std::make_shared<Request>(addr, block_size, flags, maa->requestorId);
    real_req->setRegion(my_addr_range_id);
    PacketPtr read_pkt;
    if (my_instruction->opcode == Instruction::OpcodeType::INDIR_LD ||
        my_instruction->opcode ==
            Instruction::OpcodeType::INDIR_LD_INDEX ||
        isVirtualLoad()) {
        read_pkt = new Packet(real_req, MemCmd::ReadReq);
    } else {
        read_pkt = new Packet(real_req, MemCmd::ReadExReq);
    }
    read_pkt->headerDelay = read_pkt->payloadDelay = 0;
    read_pkt->allocate();
    maa->sendPacket(FuncUnitType::INDIRECT, my_indirect_id, read_pkt,
                    maa->getClockEdge(Cycles(latency)),
                    isSoaJitRmw() ? true : my_force_cache);
    DPRINTF(MAAIndirect, "I[%d] %s: created %s for mem\n",
            my_indirect_id, __func__, read_pkt->print());
}
void IndirectAccessUnit::createDirectIndexReadPacket(Addr addr, int latency) {
    if (isVirtualLoad()) {
        if (macro_b_first_issue_tick == 0)
            macro_b_first_issue_tick = curTick();
        macro_b_last_issue_tick = curTick();
        macro_b_lines++;
        macro_b_bytes += block_size;
    }
    RequestPtr real_req = std::make_shared<Request>(
        addr, block_size, flags, maa->requestorId);
    real_req->setRegion(my_index_addr_range_id);
    PacketPtr read_pkt = new Packet(real_req, MemCmd::ReadReq);
    read_pkt->headerDelay = read_pkt->payloadDelay = 0;
    read_pkt->allocate();
    maa->sendPacket(FuncUnitType::INDIRECT, my_indirect_id, read_pkt,
                    maa->getClockEdge(Cycles(latency)),
                    isSoaJitRmw() ? true : direct_index_force_cache);
    (*maa->stats.IND_VirtIndexLineReads[my_indirect_id])++;
    if (usesBoundedDirectIndexPasses()) {
        if (direct_index_summary_active)
            (*maa->stats
                  .IND_BoundedSummaryLineReads[my_indirect_id])++;
        else if (descriptor_spool_bucket_active)
            (*maa->stats.IND_BoundedBucketLineReads[my_indirect_id])++;
        else
            (*maa->stats.IND_BoundedReplayLineReads[my_indirect_id])++;
    }
    DPRINTF(MAAIndirect,
            "I[%d] %s: created direct-index read %s\n",
            my_indirect_id, __func__, read_pkt->print());
}
void IndirectAccessUnit::createSoaPredicateReadPacket(Addr addr, int latency)
{
    RequestPtr req = std::make_shared<Request>(
        addr, block_size, flags, maa->requestorId);
    req->setRegion(my_predicate_addr_range_id);
    PacketPtr pkt = new Packet(req, MemCmd::ReadReq);
    pkt->headerDelay = pkt->payloadDelay = 0;
    pkt->allocate();
    maa->sendPacket(FuncUnitType::INDIRECT, my_indirect_id, pkt,
                    maa->getClockEdge(Cycles(latency)), true);
}
void IndirectAccessUnit::createSoaJitReadPacket(Addr addr, int latency)
{
    RequestPtr req = std::make_shared<Request>(
        addr, block_size, flags, maa->requestorId);
    req->setRegion(my_backing_addr_range_id);
    PacketPtr pkt = new Packet(req, MemCmd::ReadReq);
    pkt->headerDelay = pkt->payloadDelay = 0;
    pkt->allocate();
    maa->sendPacket(FuncUnitType::INDIRECT, my_indirect_id, pkt,
                    maa->getClockEdge(Cycles(latency)), true);
}
void IndirectAccessUnit::createDescriptorSpoolReadPacket(
    Addr vaddr, uint32_t pass, uint32_t line, bool read_ahead)
{
    const Addr paddr = translatePacket(
        vaddr, BaseMMU::Read, BoundedDescriptorSpool::LineBytes);
    panic_if(std::any_of(
                 descriptor_spool_read_slots.begin(),
                 descriptor_spool_read_slots.end(),
                 [paddr](const auto &slot) {
                     return slot.valid && slot.paddr == paddr;
                 }),
             "I[%d] descriptor line 0x%lx is already pending\n",
             my_indirect_id, paddr);
    auto active_end = descriptor_spool_read_slots.begin() +
        descriptor_spool.readCredits();
    auto slot = std::find_if(
        descriptor_spool_read_slots.begin(), active_end,
        [](const auto &candidate) { return !candidate.valid; });
    panic_if(slot == active_end,
             "I[%d] descriptor read scoreboard is full\n",
             my_indirect_id);
    const auto issue = descriptor_spool.recordReadIssue(pass, line);
    panic_if(issue != BoundedDescriptorSpool::Result::Accepted,
             "I[%d] descriptor read issue failed: %s\n", my_indirect_id,
             BoundedDescriptorSpool::resultName(issue));
    *slot = DescriptorSpoolPendingLine();
    slot->valid = true;
    slot->read_ahead = read_ahead;
    slot->paddr = paddr;
    slot->vaddr = vaddr;
    slot->pass = pass;
    slot->line = line;
    RequestPtr req = std::make_shared<Request>(
        paddr, BoundedDescriptorSpool::LineBytes, flags, maa->requestorId);
    req->setRegion(my_backing_addr_range_id);
    PacketPtr pkt = new Packet(req, MemCmd::ReadReq);
    pkt->headerDelay = pkt->payloadDelay = 0;
    pkt->allocate();
    maa->sendPacket(FuncUnitType::INDIRECT, my_indirect_id, pkt,
                    maa->getClockEdge(Cycles(0)), true);
    (*maa->stats.IND_DescriptorSpoolLineReads[my_indirect_id])++;
    (*maa->stats.IND_DescriptorSpoolReadBytes[my_indirect_id]) +=
        BoundedDescriptorSpool::LineBytes;
    if (read_ahead) {
        accountDescriptorSpoolPrefetchOccupancy();
        descriptor_spool_prefetch_occupancy++;
        descriptor_spool_prefetch_occupancy_hwm = std::max(
            descriptor_spool_prefetch_occupancy_hwm,
            descriptor_spool_prefetch_occupancy);
        descriptor_spool_next_pass_read_issues++;
    }
    DPRINTF(MAAVirtualTrace,
            "event=descriptor_spool_read_issue schema=2 unit=%d "
            "operation_tick=%lu pass=%u line=%u vaddr=0x%lx paddr=0x%lx "
            "payload_bytes=%u pending=%lu limit=%u mode=%s\n",
            my_indirect_id, my_decode_start_tick, pass, line, vaddr, paddr,
            descriptor_spool.passPayloadLineBytes(pass, line),
            static_cast<unsigned long>(descriptorSpoolReadSlotsUsed()),
            descriptor_spool.readCredits(),
            read_ahead ? "next_pass_read_ahead" : "demand");
}
void IndirectAccessUnit::createBoundedGlobalMergeReadPacket(
    Addr vaddr, uint32_t run, uint32_t line)
{
    panic_if(run >= BoundedFourRunMerge::Runs,
             "I[%d] global-merge read run %u is invalid\n",
             my_indirect_id, run);
    const Addr paddr = translatePacket(
        vaddr, BaseMMU::Read, BoundedFourRunMerge::LineBytes);
    panic_if(bounded_global_merge_read_slots[run].valid,
             "I[%d] global-merge run %u already owns a read\n",
             my_indirect_id, run);
    panic_if(std::any_of(
                 bounded_global_merge_read_slots.begin(),
                 bounded_global_merge_read_slots.end(),
                 [paddr](const auto &slot) {
                     return slot.valid && slot.paddr == paddr;
                 }),
             "I[%d] duplicate global-merge read 0x%lx\n",
             my_indirect_id, paddr);
    bounded_global_merge_read_slots[run] =
        BoundedGlobalMergeReadSlot{true, paddr, vaddr, run, line};
    RequestPtr req = std::make_shared<Request>(
        paddr, BoundedFourRunMerge::LineBytes, flags, maa->requestorId);
    req->setRegion(my_backing_addr_range_id);
    PacketPtr pkt = new Packet(req, MemCmd::ReadReq);
    pkt->headerDelay = pkt->payloadDelay = 0;
    pkt->allocate();
    maa->sendPacket(FuncUnitType::INDIRECT, my_indirect_id, pkt,
                    maa->getClockEdge(Cycles(0)), true);
    DPRINTF(MAAVirtualTrace,
            "event=bounded_global_merge_read_issue schema=1 unit=%d "
            "operation_tick=%lu run=%u line=%u vaddr=0x%lx "
            "paddr=0x%lx payload_bytes=%u mode=timing\n",
            my_indirect_id, my_decode_start_tick, run, line, vaddr, paddr,
            bounded_global_merge.runLinePayloadBytes(run, line));
}
void IndirectAccessUnit::createBoundedGlobalMergeWritePacket(
    Addr vaddr,
    const std::array<uint8_t, BoundedFourRunMerge::LineBytes> &data)
{
    const Addr paddr = translatePacket(
        vaddr, BaseMMU::Write, BoundedFourRunMerge::LineBytes);
    panic_if(maa->hasOutstandingPacket(paddr),
             "I[%d] sorted-run address 0x%lx is already owned\n",
             my_indirect_id, paddr);
    panic_if(std::any_of(
                 bounded_global_merge_write_slots.begin(),
                 bounded_global_merge_write_slots.end(),
                 [paddr](const auto &slot) {
                     return slot.valid && slot.paddr == paddr;
                 }),
             "I[%d] duplicate sorted-run write 0x%lx\n",
             my_indirect_id, paddr);
    auto slot = std::find_if(
        bounded_global_merge_write_slots.begin(),
        bounded_global_merge_write_slots.end(),
        [](const auto &candidate) { return !candidate.valid; });
    panic_if(slot == bounded_global_merge_write_slots.end(),
             "I[%d] sorted-run write scoreboard exceeded finite capacity\n",
             my_indirect_id);
    *slot = BoundedGlobalMergeWriteSlot{true, paddr, vaddr};
    RequestPtr req = std::make_shared<Request>(
        paddr, BoundedFourRunMerge::LineBytes, flags, maa->requestorId);
    req->setRegion(my_backing_addr_range_id);
    PacketPtr pkt = new Packet(req, MemCmd::WriteReq);
    pkt->headerDelay = pkt->payloadDelay = 0;
    pkt->allocate();
    pkt->setData(data.data());
    maa->sendPacket(FuncUnitType::INDIRECT, my_indirect_id, pkt,
                    maa->getClockEdge(Cycles(0)), true);
}
void IndirectAccessUnit::createDescriptorSpoolWritePacket(
    Addr vaddr,
    const std::array<uint8_t, BoundedDescriptorSpool::LineBytes> &data)
{
    const Addr paddr = translatePacket(
        vaddr, BaseMMU::Write, BoundedDescriptorSpool::LineBytes);
    panic_if(maa->hasOutstandingPacket(paddr),
             "I[%d] dedicated descriptor address 0x%lx is already owned\n",
             my_indirect_id, paddr);
    panic_if(std::any_of(
                 descriptor_spool_write_slots.begin(),
                 descriptor_spool_write_slots.end(),
                 [paddr](const auto &slot) {
                     return slot.valid && slot.paddr == paddr;
                 }),
             "I[%d] duplicate descriptor write 0x%lx\n",
             my_indirect_id, paddr);
    auto slot = std::find_if(
        descriptor_spool_write_slots.begin(),
        descriptor_spool_write_slots.end(),
        [](const auto &candidate) { return !candidate.valid; });
    panic_if(slot == descriptor_spool_write_slots.end(),
             "I[%d] descriptor write scoreboard exceeded finite capacity\n",
             my_indirect_id);
    *slot = DescriptorSpoolWriteSlot{true, paddr, vaddr};
    RequestPtr req = std::make_shared<Request>(
        paddr, BoundedDescriptorSpool::LineBytes, flags, maa->requestorId);
    req->setRegion(my_backing_addr_range_id);
    PacketPtr pkt = new Packet(req, MemCmd::WriteReq);
    pkt->headerDelay = pkt->payloadDelay = 0;
    pkt->allocate();
    pkt->setData(data.data());
    maa->sendPacket(FuncUnitType::INDIRECT, my_indirect_id, pkt,
                    maa->getClockEdge(Cycles(0)), true);
    (*maa->stats.IND_DescriptorSpoolLineWrites[my_indirect_id])++;
    (*maa->stats.IND_DescriptorSpoolWriteBytes[my_indirect_id]) +=
        BoundedDescriptorSpool::LineBytes;
    DPRINTF(MAAVirtualTrace,
            "event=descriptor_spool_write_issue schema=1 unit=%d "
            "operation_tick=%lu vaddr=0x%lx paddr=0x%lx bytes=%u "
            "outstanding=%u limit=%u\n",
            my_indirect_id, my_decode_start_tick, vaddr, paddr,
            BoundedDescriptorSpool::LineBytes,
            descriptor_spool.outstandingWriteCount(),
            descriptor_spool.writeCredits());
}
void IndirectAccessUnit::memReadPacketSent(Addr addr) {
    DPRINTF(MAAIndirect, "I[%d] %s: mem read packet 0x%lx sent\n", my_indirect_id, __func__, addr);
    (*maa->stats.IND_LoadsMemAccessing[my_indirect_id])++;
    LoadsMemAccessingTimeHistory[addr] = curTick();
}
void IndirectAccessUnit::memWritePacketSent(Addr addr) {
    DPRINTF(MAAIndirect, "I[%d] %s: mem write packet 0x%lx sent\n", my_indirect_id, __func__, addr);
    my_received_responses++;
    if (maa->allIndirectPacketsSent(my_indirect_id) && (my_received_responses == my_expected_responses)) {
        DPRINTF(MAAIndirect, "I[%d] %s: all responses received, calling execution again in state %s!\n", my_indirect_id, __func__, status_names[(int)state]);
        scheduleNextExecution(true);
    } else {
        DPRINTF(MAAIndirect, "I[%d] %s: expected: %d, received: %d!\n", my_indirect_id, __func__, my_expected_responses, my_received_responses);
    }
}
void IndirectAccessUnit::cacheReadPacketSent(Addr addr) {
    DPRINTF(MAAIndirect, "I[%d] %s: cache read packet 0x%lx sent\n", my_indirect_id, __func__, addr);
    LoadsCacheHitAccessingTimeHistory[addr] = curTick();
    (*maa->stats.IND_LoadsCacheHitAccessing[my_indirect_id])++;
}
void IndirectAccessUnit::cacheWritePacketSent(Addr addr) {
    DPRINTF(MAAIndirect, "I[%d] %s: cache write packet 0x%lx sent\n", my_indirect_id, __func__, addr);
    my_received_responses++;
    if (maa->allIndirectPacketsSent(my_indirect_id) && (my_received_responses == my_expected_responses)) {
        DPRINTF(MAAIndirect, "I[%d] %s: all responses received, calling execution again in state %s!\n", my_indirect_id, __func__, status_names[(int)state]);
        scheduleNextExecution(true);
    } else {
        DPRINTF(MAAIndirect, "I[%d] %s: expected: %d, received: %d!\n", my_indirect_id, __func__, my_expected_responses, my_received_responses);
    }
}
bool IndirectAccessUnit::receiveBoundedGlobalMerge(
    Addr addr, uint8_t *dataptr, bool is_block_cached)
{
    if (!maa->virtual_bounded_global_merge)
        return false;
    if (bounded_global_merge_source_pending &&
        addr == bounded_global_merge_source_paddr) {
        accountReadResponse(addr, is_block_cached);
        std::memcpy(bounded_global_merge_source_data.data(), dataptr,
                    BoundedFourRunMerge::LineBytes);
        bounded_global_merge_source_pending = false;
        bounded_global_merge_source_ready = true;
        bounded_global_merge_source_responses++;
        virtual_source_received++;
        DPRINTF(MAAVirtualTrace,
                "event=bounded_global_a_line_response schema=1 unit=%d "
                "operation_tick=%lu paddr=0x%lx cached=%d\n",
                my_indirect_id, my_decode_start_tick, addr,
                is_block_cached);
        scheduleNextExecution(true);
        return true;
    }
    auto slot = std::find_if(
        bounded_global_merge_read_slots.begin(),
        bounded_global_merge_read_slots.end(),
        [addr](const auto &candidate) {
            return candidate.valid && candidate.paddr == addr;
        });
    if (slot == bounded_global_merge_read_slots.end())
        return false;
    accountReadResponse(addr, is_block_cached);
    std::array<uint8_t, BoundedFourRunMerge::LineBytes> data{};
    std::memcpy(data.data(), dataptr, data.size());
    const uint32_t run = slot->run;
    const uint32_t line = slot->line;
    const Addr vaddr = slot->vaddr;
    *slot = BoundedGlobalMergeReadSlot();
    const auto accepted = bounded_global_merge.acceptRead(run, line, data);
    panic_if(accepted != BoundedFourRunMerge::Result::Accepted,
             "I[%d] global-merge read run %u line %u failed: %s\n",
             my_indirect_id, run, line,
             BoundedFourRunMerge::resultName(accepted));
    DPRINTF(MAAVirtualTrace,
            "event=bounded_global_merge_read_response schema=1 unit=%d "
            "operation_tick=%lu run=%u line=%u vaddr=0x%lx "
            "paddr=0x%lx cached=%d\n",
            my_indirect_id, my_decode_start_tick, run, line, vaddr, addr,
            is_block_cached);
    scheduleNextExecution(true);
    return true;
}
bool
IndirectAccessUnit::recvData(const Addr addr, uint8_t *dataptr,
                             bool is_block_cached)
{
    if (receiveBoundedGlobalMerge(addr, dataptr, is_block_cached))
        return true;
    if (receiveDescriptorSpool(addr, dataptr, is_block_cached))
        return true;
    if (receiveDirectIndex(addr, dataptr, is_block_cached))
        return true;
    if (receiveSoaPredicate(addr, dataptr, is_block_cached))
        return true;
    if (receiveSoaJitData(addr, dataptr, is_block_cached))
        return true;
    std::vector addr_vec = maa->map_addr(addr);
    int RT_idx = getRowTableIdx(my_RT_config, addr_vec[ADDR_CHANNEL_LEVEL], addr_vec[ADDR_RANK_LEVEL], addr_vec[ADDR_BANKGROUP_LEVEL], addr_vec[ADDR_BANK_LEVEL]);
    Addr grow_addr = getGrowAddr(my_RT_config, addr_vec[ADDR_BANKGROUP_LEVEL], addr_vec[ADDR_BANK_LEVEL], addr_vec[ADDR_ROW_LEVEL]);
    bool was_full = false;
    if (RT_idx == my_RT_idx)
        was_full = RT[my_RT_config][RT_idx].is_full();
    const bool bounded_response_load = usesBoundedSourceResponses();
    int virtual_head = -1;
    int virtual_reserved_words = 0;
    int virtual_claim_rt_idx = -1;
    int virtual_claim_row_id = -1;
    int virtual_claim_entry_id = -1;
    Addr virtual_claim_grow_addr = 0;
    std::vector<OffsetTableEntry> entries;
    if (bounded_response_load) {
        auto reservation = virtual_source_reservations.find(addr);
        if (reservation == virtual_source_reservations.end())
            return false;
        virtual_head = reservation->second.head;
        virtual_reserved_words = reservation->second.words;
        virtual_claim_rt_idx = reservation->second.rt_idx;
        virtual_claim_row_id = reservation->second.row_id;
        virtual_claim_entry_id = reservation->second.entry_id;
        virtual_claim_grow_addr = reservation->second.grow_addr;
        if (maa->virtual_native_issue_order) {
            panic_if(virtual_claim_rt_idx != RT_idx ||
                         virtual_claim_grow_addr != grow_addr,
                     "I[%d] native response 0x%lx moved from RT/grow "
                     "%d/0x%lx to %d/0x%lx\n",
                     my_indirect_id, addr, virtual_claim_rt_idx,
                     virtual_claim_grow_addr, RT_idx, grow_addr);
        }
        virtual_source_reservations.erase(reservation);
    } else {
        entries = RT[my_RT_config][RT_idx].get_entry_recv(
            grow_addr, addr, reorder_RT);
        if (!entries.empty())
            recordReorderSurvivalIssuedEntries(entries.size());
    }
    bool is_full = false;
    if (RT_idx == my_RT_idx)
        is_full = RT[my_RT_config][RT_idx].is_full();
    DPRINTF(MAAIndirect, "I[%d] %s: %d entries received for addr(0x%lx), grow(x%lx) from T[%d]!\n", my_indirect_id, __func__, entries.size(), addr, grow_addr, RT_idx);
    if ((!bounded_response_load && entries.empty()) ||
        (bounded_response_load && virtual_head == -1)) {
        return false;
    }
    accountReadResponse(addr, is_block_cached);
    if (bounded_response_load) {
        accountVirtualRequestInterval();
        DPRINTF(MAAVirtualTrace,
                "event=source_response schema=2 unit=%d occurrence=%lu "
                "operation_tick=%lu addr=0x%lx head=%d words=%d "
                "cached=%d\n",
                my_indirect_id, attribution_event_occurrence++,
                my_decode_start_tick, addr, virtual_head,
                virtual_reserved_words, is_block_cached);
        auto slot = std::find_if(virtual_response_slots.begin(),
                                 virtual_response_slots.end(),
                                 [](const VirtualResponseSlot &candidate) {
                                     return !candidate.valid;
                                 });
        panic_if(slot == virtual_response_slots.end(),
                 "I[%d] %s: no reserved virtual response slot!\n",
                 my_indirect_id, __func__);
        const size_t slot_idx = std::distance(
            virtual_response_slots.begin(), slot);
        slot->valid = true;
        slot->next_itr = virtual_head;
        slot->claim_rt_idx = virtual_claim_rt_idx;
        slot->claim_row_id = virtual_claim_row_id;
        slot->claim_entry_id = virtual_claim_entry_id;
        slot->claim_grow_addr = virtual_claim_grow_addr;
        slot->claim_addr = addr;
        slot->claim_head = virtual_head;
        if (maa->virtual_bounded_global_merge &&
            bounded_global_merge_phase == BoundedGlobalMergePhase::Merge) {
            bounded_global_merge_source_responses++;
        }
        if (virtual_response_word_pool_limit != 0) {
            panic_if(virtual_reserved_words <= 0,
                     "I[%d] response 0x%lx has no packed-word reservation\n",
                     my_indirect_id, addr);
            slot->reserved_words = virtual_reserved_words;
        }
        const bool packed_response = virtual_response_words != 0 ||
                                     virtual_response_word_pool_limit != 0;
        if (!packed_response) {
            std::memcpy(virtual_response_line_payloads.lineData(slot_idx),
                        dataptr, block_size);
        } else {
            int itr = virtual_head;
            while (itr != -1) {
                OffsetTableEntry entry = offset_table->peek_entry(itr);
                panic_if(virtual_response_word_pool_limit == 0 &&
                             slot->packed_words.size() == virtual_response_words,
                         "I[%d] source response needs more than %d packed words\n",
                         my_indirect_id, virtual_response_words);
                std::array<uint8_t, 8> word{};
                std::memcpy(word.data(),
                            dataptr + entry.wid * my_word_size, my_word_size);
                slot->packed_words.push_back(word);
                itr = entry.next_itr;
            }
        }
        my_received_responses++;
        virtual_source_received++;
        macro_a_last_response_tick = curTick();
        const bool response_throttled = drainVirtualResponses();
        accountVirtualRequestInterval();
        if (response_throttled)
            scheduleExecuteInstructionEvent(1);
        else
            scheduleNextExecution(true);
        if (!response_throttled && was_full && !is_full)
            scheduleNextExecution(true);
        return true;
    }
    uint8_t new_data[block_size];
    uint32_t *dataptr_u32_typed = (uint32_t *)new_data;
    uint64_t *dataptr_u64_typed = (uint64_t *)new_data;
    std::memcpy(new_data, dataptr, block_size);
    int num_recv_spd_read_accesses = 0;
    int num_recv_spd_write_accesses = 0;
    int num_recv_rt_accesses = entries.size();
    for (auto entry : entries) {
        int itr = entry.itr;
        int wid = entry.wid;
        DPRINTF(MAAIndirect, "I[%d] %s: itr (%d) wid (%d) matched!\n", my_indirect_id, __func__, itr, wid);
        if (my_dst_tile != -1 && !isVirtualLoad()) {
            if (my_word_size == 4) {
                maa->spd->setData<uint32_t>(my_dst_tile, itr, dataptr_u32_typed[wid]);
                DPRINTF(MAAIndirect, "I[%d] %s: SPD[%d][%d] = %u/%d/%f!\n", my_indirect_id, __func__, my_dst_tile, itr, ((uint32_t *)new_data)[wid], ((int32_t *)new_data)[wid], ((float *)new_data)[wid]);
            } else {
                maa->spd->setData<uint64_t>(my_dst_tile, itr, dataptr_u64_typed[wid]);
                DPRINTF(MAAIndirect, "I[%d] %s: SPD[%d][%d] = %lu/%ld/%lf!\n", my_indirect_id, __func__, my_dst_tile, itr, ((uint64_t *)new_data)[wid], ((int64_t *)new_data)[wid], ((double *)new_data)[wid]);
            }
            num_recv_spd_write_accesses++;
        }
        switch (my_instruction->opcode) {
        case Instruction::OpcodeType::INDIR_LD_VIRTUAL:
        case Instruction::OpcodeType::INDIR_LD_VIRTUAL_INDEX:
            panic("Virtual load must use bounded response retirement\n");
        case Instruction::OpcodeType::INDIR_LD:
        case Instruction::OpcodeType::INDIR_LD_INDEX: {
            assert(my_dst_tile != -1);
            break;
        }
        case Instruction::OpcodeType::INDIR_ST_VECTOR: {
            if (my_word_size == 4) {
                ((uint32_t *)new_data)[wid] = maa->spd->getData<uint32_t>(my_src_tile, itr);
                DPRINTF(MAAIndirect, "I[%d] %s: new_data[%d] = SPD[%d][%d] = %u/%d/%f!\n", my_indirect_id, __func__, wid, my_src_tile, itr, ((uint32_t *)new_data)[wid], ((int32_t *)new_data)[wid], ((float *)new_data)[wid]);
            } else {
                ((uint64_t *)new_data)[wid] = maa->spd->getData<uint64_t>(my_src_tile, itr);
                DPRINTF(MAAIndirect, "I[%d] %s: new_data[%d] = SPD[%d][%d] = %lu/%ld/%lf!\n", my_indirect_id, __func__, wid, my_src_tile, itr, ((uint64_t *)new_data)[wid], ((int64_t *)new_data)[wid], ((double *)new_data)[wid]);
            }
            num_recv_spd_read_accesses++;
            break;
        }
        case Instruction::OpcodeType::INDIR_ST_SCALAR: {
            if (my_word_size == 4) {
                ((uint32_t *)new_data)[wid] = maa->rf->getData<uint32_t>(my_src_reg);
            } else {
                ((uint64_t *)new_data)[wid] = maa->rf->getData<uint64_t>(my_src_reg);
            }
            break;
        }
        case Instruction::OpcodeType::INDIR_RMW_VECTOR: {
            switch (my_instruction->datatype) {
            case Instruction::DataType::UINT32_TYPE: {
                uint32_t word_data = maa->spd->getData<uint32_t>(my_src_tile, itr);
                if (my_instruction->optype == Instruction::OPType::ADD_OP) {
                    DPRINTF(MAAIndirect, "I[%d] %s: new_data[%d] (%u) += SPD[%d][%d] (%u) = %u!\n",
                            my_indirect_id, __func__, wid, ((uint32_t *)new_data)[wid], my_src_tile, itr, word_data, ((uint32_t *)new_data)[wid] + word_data);
                    ((uint32_t *)new_data)[wid] += word_data;
                } else if (my_instruction->optype == Instruction::OPType::MIN_OP) {
                    ((uint32_t *)new_data)[wid] = ((uint32_t *)new_data)[wid] < word_data ? ((uint32_t *)new_data)[wid] : word_data;
                } else if (my_instruction->optype == Instruction::OPType::MAX_OP) {
                    ((uint32_t *)new_data)[wid] = ((uint32_t *)new_data)[wid] > word_data ? ((uint32_t *)new_data)[wid] : word_data;
                } else {
                    panic_if(true, "I[%d] %s: unknown optype %s!\n", my_indirect_id, __func__, my_instruction->print());
                }
                break;
            }
            case Instruction::DataType::INT32_TYPE: {
                int32_t word_data = maa->spd->getData<int32_t>(my_src_tile, itr);
                if (my_instruction->optype == Instruction::OPType::ADD_OP) {
                    DPRINTF(MAAIndirect, "I[%d] %s: new_data[%d] (%d) += SPD[%d][%d] (%d) = %d!\n",
                            my_indirect_id, __func__, wid, ((int32_t *)new_data)[wid], my_src_tile, itr, word_data, ((int32_t *)new_data)[wid] + word_data);
                    ((int32_t *)new_data)[wid] += word_data;
                } else if (my_instruction->optype == Instruction::OPType::MIN_OP) {
                    ((int32_t *)new_data)[wid] = ((int32_t *)new_data)[wid] < word_data ? ((int32_t *)new_data)[wid] : word_data;
                } else if (my_instruction->optype == Instruction::OPType::MAX_OP) {
                    ((int32_t *)new_data)[wid] = ((int32_t *)new_data)[wid] > word_data ? ((int32_t *)new_data)[wid] : word_data;
                } else {
                    panic_if(true, "I[%d] %s: unknown optype %s!\n", my_indirect_id, __func__, my_instruction->print());
                }
                break;
            }
            case Instruction::DataType::FLOAT32_TYPE: {
                float word_data = maa->spd->getData<float>(my_src_tile, itr);
                if (my_instruction->optype == Instruction::OPType::ADD_OP) {
                    DPRINTF(MAAIndirect, "I[%d] %s: new_data[%d] (%f) += SPD[%d][%d] (%f) = %f!\n",
                            my_indirect_id, __func__, wid, ((float *)new_data)[wid], my_src_tile, itr, word_data, ((float *)new_data)[wid] + word_data);
                    ((float *)new_data)[wid] += word_data;
                } else if (my_instruction->optype == Instruction::OPType::MIN_OP) {
                    ((float *)new_data)[wid] = ((float *)new_data)[wid] < word_data ? ((float *)new_data)[wid] : word_data;
                } else if (my_instruction->optype == Instruction::OPType::MAX_OP) {
                    ((float *)new_data)[wid] = ((float *)new_data)[wid] > word_data ? ((float *)new_data)[wid] : word_data;
                } else {
                    panic_if(true, "I[%d] %s: unknown optype %s!\n", my_indirect_id, __func__, my_instruction->print());
                }
                break;
            }
            case Instruction::DataType::UINT64_TYPE: {
                uint64_t word_data = maa->spd->getData<uint64_t>(my_src_tile, itr);
                if (my_instruction->optype == Instruction::OPType::ADD_OP) {
                    DPRINTF(MAAIndirect, "I[%d] %s: new_data[%d] (%lu) += SPD[%d][%d] (%lu) = %lu!\n",
                            my_indirect_id, __func__, wid, ((uint64_t *)new_data)[wid], my_src_tile, itr, word_data, ((uint64_t *)new_data)[wid] + word_data);
                    ((uint64_t *)new_data)[wid] += word_data;
                } else if (my_instruction->optype == Instruction::OPType::MIN_OP) {
                    ((uint64_t *)new_data)[wid] = ((uint64_t *)new_data)[wid] < word_data ? ((uint64_t *)new_data)[wid] : word_data;
                } else if (my_instruction->optype == Instruction::OPType::MAX_OP) {
                    ((uint64_t *)new_data)[wid] = ((uint64_t *)new_data)[wid] > word_data ? ((uint64_t *)new_data)[wid] : word_data;
                } else {
                    panic_if(true, "I[%d] %s: unknown optype %s!\n", my_indirect_id, __func__, my_instruction->print());
                }
                break;
            }
            case Instruction::DataType::INT64_TYPE: {
                int64_t word_data = maa->spd->getData<int64_t>(my_src_tile, itr);
                if (my_instruction->optype == Instruction::OPType::ADD_OP) {
                    DPRINTF(MAAIndirect, "I[%d] %s: new_data[%d] (%ld) += SPD[%d][%d] (%ld) = %ld!\n",
                            my_indirect_id, __func__, wid, ((int64_t *)new_data)[wid], my_src_tile, itr, word_data, ((int64_t *)new_data)[wid] + word_data);
                    ((int64_t *)new_data)[wid] += word_data;
                } else if (my_instruction->optype == Instruction::OPType::MIN_OP) {
                    ((int64_t *)new_data)[wid] = ((int64_t *)new_data)[wid] < word_data ? ((int64_t *)new_data)[wid] : word_data;
                } else if (my_instruction->optype == Instruction::OPType::MAX_OP) {
                    ((int64_t *)new_data)[wid] = ((int64_t *)new_data)[wid] > word_data ? ((int64_t *)new_data)[wid] : word_data;
                } else {
                    panic_if(true, "I[%d] %s: unknown optype %s!\n", my_indirect_id, __func__, my_instruction->print());
                }
                break;
            }
            case Instruction::DataType::FLOAT64_TYPE: {
                double word_data = maa->spd->getData<double>(my_src_tile, itr);
                if (my_instruction->optype == Instruction::OPType::ADD_OP) {
                    DPRINTF(MAAIndirect, "I[%d] %s: new_data[%d] (%lf) += SPD[%d][%d] (%lf) = %lf!\n",
                            my_indirect_id, __func__, wid, ((double *)new_data)[wid], my_src_tile, itr, word_data, ((double *)new_data)[wid] + word_data);
                    ((double *)new_data)[wid] += word_data;
                } else if (my_instruction->optype == Instruction::OPType::MIN_OP) {
                    ((double *)new_data)[wid] = ((double *)new_data)[wid] < word_data ? ((double *)new_data)[wid] : word_data;
                } else if (my_instruction->optype == Instruction::OPType::MAX_OP) {
                    ((double *)new_data)[wid] = ((double *)new_data)[wid] > word_data ? ((double *)new_data)[wid] : word_data;
                } else {
                    panic_if(true, "I[%d] %s: unknown optype %s!\n", my_indirect_id, __func__, my_instruction->print());
                }
                break;
            }
            default:
                assert(false);
            }
            break;
        }
        case Instruction::OpcodeType::INDIR_RMW_SCALAR: {
            switch (my_instruction->datatype) {
            case Instruction::DataType::UINT32_TYPE: {
                uint32_t word_data = maa->rf->getData<uint32_t>(my_src_reg);
                if (my_instruction->optype == Instruction::OPType::ADD_OP) {
                    DPRINTF(MAAIndirect, "I[%d] %s: new_data[%d] (%u) += RF[%d] (%u) = %u!\n",
                            my_indirect_id, __func__, wid, ((uint32_t *)new_data)[wid], my_src_reg, word_data, ((uint32_t *)new_data)[wid] + word_data);
                    ((uint32_t *)new_data)[wid] += word_data;
                } else if (my_instruction->optype == Instruction::OPType::MIN_OP) {
                    ((uint32_t *)new_data)[wid] = ((uint32_t *)new_data)[wid] < word_data ? ((uint32_t *)new_data)[wid] : word_data;
                } else if (my_instruction->optype == Instruction::OPType::MAX_OP) {
                    ((uint32_t *)new_data)[wid] = ((uint32_t *)new_data)[wid] > word_data ? ((uint32_t *)new_data)[wid] : word_data;
                } else {
                    panic_if(true, "I[%d] %s: unknown optype %s!\n", my_indirect_id, __func__, my_instruction->print());
                }
                break;
            }
            case Instruction::DataType::INT32_TYPE: {
                int32_t word_data = maa->rf->getData<int32_t>(my_src_reg);
                if (my_instruction->optype == Instruction::OPType::ADD_OP) {
                    DPRINTF(MAAIndirect, "I[%d] %s: new_data[%d] (%d) += RF[%d] (%d) = %d!\n",
                            my_indirect_id, __func__, wid, ((int32_t *)new_data)[wid], my_src_reg, word_data, ((int32_t *)new_data)[wid] + word_data);
                    ((int32_t *)new_data)[wid] += word_data;
                } else if (my_instruction->optype == Instruction::OPType::MIN_OP) {
                    ((int32_t *)new_data)[wid] = ((int32_t *)new_data)[wid] < word_data ? ((int32_t *)new_data)[wid] : word_data;
                } else if (my_instruction->optype == Instruction::OPType::MAX_OP) {
                    ((int32_t *)new_data)[wid] = ((int32_t *)new_data)[wid] > word_data ? ((int32_t *)new_data)[wid] : word_data;
                } else {
                    panic_if(true, "I[%d] %s: unknown optype %s!\n", my_indirect_id, __func__, my_instruction->print());
                }
                break;
            }
            case Instruction::DataType::FLOAT32_TYPE: {
                float word_data = maa->rf->getData<float>(my_src_reg);
                if (my_instruction->optype == Instruction::OPType::ADD_OP) {
                    DPRINTF(MAAIndirect, "I[%d] %s: new_data[%d] (%f) += RF[%d] (%f) = %f!\n",
                            my_indirect_id, __func__, wid, ((float *)new_data)[wid], my_src_reg, word_data, ((float *)new_data)[wid] + word_data);
                    ((float *)new_data)[wid] += word_data;
                } else if (my_instruction->optype == Instruction::OPType::MIN_OP) {
                    ((float *)new_data)[wid] = ((float *)new_data)[wid] < word_data ? ((float *)new_data)[wid] : word_data;
                } else if (my_instruction->optype == Instruction::OPType::MAX_OP) {
                    ((float *)new_data)[wid] = ((float *)new_data)[wid] > word_data ? ((float *)new_data)[wid] : word_data;
                } else {
                    panic_if(true, "I[%d] %s: unknown optype %s!\n", my_indirect_id, __func__, my_instruction->print());
                }
                break;
            }
            case Instruction::DataType::UINT64_TYPE: {
                uint64_t word_data = maa->rf->getData<uint64_t>(my_src_reg);
                if (my_instruction->optype == Instruction::OPType::ADD_OP) {
                    DPRINTF(MAAIndirect, "I[%d] %s: new_data[%d] (%lu) += RF[%d] (%lu) = %lu!\n",
                            my_indirect_id, __func__, wid, ((uint64_t *)new_data)[wid], my_src_reg, word_data, ((uint64_t *)new_data)[wid] + word_data);
                    ((uint64_t *)new_data)[wid] += word_data;
                } else if (my_instruction->optype == Instruction::OPType::MIN_OP) {
                    ((uint64_t *)new_data)[wid] = ((uint64_t *)new_data)[wid] < word_data ? ((uint64_t *)new_data)[wid] : word_data;
                } else if (my_instruction->optype == Instruction::OPType::MAX_OP) {
                    ((uint64_t *)new_data)[wid] = ((uint64_t *)new_data)[wid] > word_data ? ((uint64_t *)new_data)[wid] : word_data;
                } else {
                    panic_if(true, "I[%d] %s: unknown optype %s!\n", my_indirect_id, __func__, my_instruction->print());
                }
                break;
            }
            case Instruction::DataType::INT64_TYPE: {
                int64_t word_data = maa->rf->getData<int64_t>(my_src_reg);
                if (my_instruction->optype == Instruction::OPType::ADD_OP) {
                    DPRINTF(MAAIndirect, "I[%d] %s: new_data[%d] (%ld) += RF[%d] (%ld) = %ld!\n",
                            my_indirect_id, __func__, wid, ((int64_t *)new_data)[wid], my_src_reg, word_data, ((int64_t *)new_data)[wid] + word_data);
                    ((int64_t *)new_data)[wid] += word_data;
                } else if (my_instruction->optype == Instruction::OPType::MIN_OP) {
                    ((int64_t *)new_data)[wid] = ((int64_t *)new_data)[wid] < word_data ? ((int64_t *)new_data)[wid] : word_data;
                } else if (my_instruction->optype == Instruction::OPType::MAX_OP) {
                    ((int64_t *)new_data)[wid] = ((int64_t *)new_data)[wid] > word_data ? ((int64_t *)new_data)[wid] : word_data;
                } else {
                    panic_if(true, "I[%d] %s: unknown optype %s!\n", my_indirect_id, __func__, my_instruction->print());
                }
                break;
            }
            case Instruction::DataType::FLOAT64_TYPE: {
                double word_data = maa->rf->getData<double>(my_src_reg);
                if (my_instruction->optype == Instruction::OPType::ADD_OP) {
                    DPRINTF(MAAIndirect, "I[%d] %s: new_data[%d] (%lf) += RF[%d] (%lf) = %lf!\n",
                            my_indirect_id, __func__, wid, ((double *)new_data)[wid], my_src_reg, word_data, ((double *)new_data)[wid] + word_data);
                    ((double *)new_data)[wid] += word_data;
                } else if (my_instruction->optype == Instruction::OPType::MIN_OP) {
                    ((double *)new_data)[wid] = ((double *)new_data)[wid] < word_data ? ((double *)new_data)[wid] : word_data;
                } else if (my_instruction->optype == Instruction::OPType::MAX_OP) {
                    ((double *)new_data)[wid] = ((double *)new_data)[wid] > word_data ? ((double *)new_data)[wid] : word_data;
                } else {
                    panic_if(true, "I[%d] %s: unknown optype %s!\n", my_indirect_id, __func__, my_instruction->print());
                }
                break;
            }
            default:
                assert(false);
            }
            break;
        }
        default:
            assert(false);
        }
    }

    // Row table parallelism = total #banks.
    // We will have total #banks offset table walkers.
    Cycles total_latency = updateLatency(num_recv_spd_read_accesses, 0, num_recv_spd_write_accesses, num_recv_rt_accesses, 0, total_num_RT_subslices);
    if (my_instruction->opcode == Instruction::OpcodeType::INDIR_ST_VECTOR || my_instruction->opcode == Instruction::OpcodeType::INDIR_ST_SCALAR || my_instruction->opcode == Instruction::OpcodeType::INDIR_RMW_VECTOR || my_instruction->opcode == Instruction::OpcodeType::INDIR_RMW_SCALAR) {
        RequestPtr real_req = std::make_shared<Request>(addr, block_size, flags, maa->requestorId);
        real_req->setRegion(my_addr_range_id);
        PacketPtr write_pkt = new Packet(real_req, MemCmd::WritebackDirty);
        write_pkt->allocate();
        write_pkt->setData(new_data);
        for (int i = 0; i < block_size / my_word_size; i++) {
            if (my_word_size == 4)
                DPRINTF(MAAIndirect, "I[%d] %s: new_data[%d] = %f!\n", my_indirect_id, __func__, i, write_pkt->getPtr<float>()[i]);
            else
                DPRINTF(MAAIndirect, "I[%d] %s: new_data[%d] = %f!\n", my_indirect_id, __func__, i, write_pkt->getPtr<double>()[i]);
        }
        DPRINTF(MAAIndirect, "I[%d] %s: created %s to send in %d cycles\n", my_indirect_id, __func__, write_pkt->print(), total_latency);
        maa->sendPacket(FuncUnitType::INDIRECT, my_indirect_id, write_pkt,
                        maa->getClockEdge(total_latency), my_force_cache,
                        false, true);
        (*maa->stats.IND_StoresMemAccessing[my_indirect_id])++;
    } else {
        my_received_responses++;
        if (maa->allIndirectPacketsSent(my_indirect_id) && my_received_responses == my_expected_responses) {
            DPRINTF(MAAIndirect, "I[%d] %s: all responses received, calling execution again!\n", my_indirect_id, __func__);
            scheduleNextExecution(true);
        } else {
            DPRINTF(MAAIndirect, "I[%d] %s: expected: %d, received: %d responses!\n", my_indirect_id, __func__, my_expected_responses, my_received_responses);
        }
    }
    if (was_full && !is_full) {
        DPRINTF(MAAIndirect, "I[%d] %s: RT[%d] was full, now not full, calling execution again!\n", my_indirect_id, __func__, RT_idx);
        panic_if(state != Status::Request && state != Status::Fill, "I[%d] %s: state is %s!\n", my_indirect_id, __func__, status_names[(int)state]);
        scheduleNextExecution(true);
    }
    return true;
}
Addr IndirectAccessUnit::backingWordAddr(int itr) const {
    panic_if(itr < 0, "I[%d] negative virtual backing index %d\n",
             my_indirect_id, itr);
    panic_if(my_word_size <= 0,
             "I[%d] invalid virtual backing word size %d\n",
             my_indirect_id, my_word_size);
    panic_if(my_backing_addr < my_backing_min_addr ||
                 my_backing_addr >= my_backing_max_addr,
             "I[%d] virtual backing base 0x%lx out of range "
             "[0x%lx, 0x%lx)\n",
             my_indirect_id, my_backing_addr, my_backing_min_addr,
             my_backing_max_addr);
    const Addr remaining = my_backing_max_addr - my_backing_addr;
    const Addr index = static_cast<Addr>(itr);
    panic_if(index >= remaining / static_cast<Addr>(my_word_size),
             "I[%d] virtual backing index %d with word size %d exceeds "
             "[0x%lx, 0x%lx)\n",
             my_indirect_id, itr, my_word_size, my_backing_min_addr,
             my_backing_max_addr);
    return my_backing_addr + index * my_word_size;
}

void IndirectAccessUnit::validateRetirementWriteRange(
    Addr vaddr, unsigned size, uint16_t valid_words) const {
    if (valid_words == 0) {
        panic_if(size == 0 || vaddr < my_backing_min_addr ||
                     vaddr >= my_backing_max_addr ||
                     static_cast<Addr>(size) > my_backing_max_addr - vaddr,
                 "I[%d] virtual retirement write [0x%lx, 0x%lx) exceeds "
                 "backing range [0x%lx, 0x%lx)\n",
                 my_indirect_id, vaddr, vaddr + size, my_backing_min_addr,
                 my_backing_max_addr);
        return;
    }

    panic_if(size != block_size || my_word_size <= 0 ||
                 size % my_word_size != 0,
             "I[%d] invalid masked retirement write size=%u word_size=%d\n",
             my_indirect_id, size, my_word_size);
    const unsigned words = size / my_word_size;
    panic_if(words > 16 || valid_words >> words,
             "I[%d] masked retirement write has invalid word mask 0x%x "
             "for %u words\n",
             my_indirect_id, valid_words, words);
    for (unsigned word = 0; word < words; ++word) {
        if ((valid_words & (1U << word)) == 0)
            continue;
        const Addr word_vaddr = vaddr + word * my_word_size;
        panic_if(word_vaddr < my_backing_min_addr ||
                     word_vaddr >= my_backing_max_addr ||
                     static_cast<Addr>(my_word_size) >
                         my_backing_max_addr - word_vaddr,
                 "I[%d] enabled virtual retirement word "
                 "[0x%lx, 0x%lx) exceeds backing range [0x%lx, 0x%lx)\n",
                 my_indirect_id, word_vaddr, word_vaddr + my_word_size,
                 my_backing_min_addr, my_backing_max_addr);
    }
}

void IndirectAccessUnit::initializeVirtualPageTracking() {
    if (!virtual_page_logical_words.empty() || my_max == 0)
        return;
    panic_if(!isVirtualLoad() || my_max < 0,
             "I[%d] cannot initialize virtual page tracking with max=%d\n",
             my_indirect_id, my_max);
    const int page_elements = maa->physical_tile_elements;
    panic_if(page_elements <= 0,
             "I[%d] invalid physical page size %d\n", my_indirect_id,
             page_elements);
    const int pages = (my_max + page_elements - 1) / page_elements;
    panic_if(pages > MAA::MaxVirtualPages,
             "I[%d] virtual gather needs %d pages, exceeding token limit %d\n",
             my_indirect_id, pages, MAA::MaxVirtualPages);
    virtual_page_logical_words.resize(pages);
    virtual_page_scanned_words.assign(pages, 0);
    virtual_page_expected_words.assign(pages, 0);
    virtual_page_issued_words.assign(pages, 0);
    virtual_page_completed_words.assign(pages, 0);
    virtual_page_last_write_key.assign(pages, 0);
    virtual_page_ready.assign(pages, false);
    for (int page = 0; page < pages; ++page) {
        virtual_page_logical_words[page] =
            std::min(page_elements, my_max - page * page_elements);
    }
}

void IndirectAccessUnit::trackVirtualIteration(int itr,
                                               bool write_expected) {
    initializeVirtualPageTracking();
    panic_if(itr < 0 || itr >= my_max,
             "I[%d] virtual iteration %d exceeds [0, %d)\n",
             my_indirect_id, itr, my_max);
    const int page = itr / maa->physical_tile_elements;
    virtual_page_scanned_words[page]++;
    panic_if(virtual_page_scanned_words[page] >
                 virtual_page_logical_words[page],
             "I[%d] virtual page %d scanned too many words: %d/%d\n",
             my_indirect_id, page, virtual_page_scanned_words[page],
             virtual_page_logical_words[page]);
    if (write_expected)
        virtual_page_expected_words[page]++;
    markVirtualPageReadyIfComplete(page);
}

void IndirectAccessUnit::markVirtualPageReadyIfComplete(
    int page, Addr final_write_key)
{
    panic_if(page < 0 ||
                 page >= static_cast<int>(virtual_page_logical_words.size()),
             "I[%d] invalid virtual page %d\n", my_indirect_id, page);
    if (final_write_key == 0 && maa->virtual_idealized_write_ack &&
        virtual_page_expected_words[page] != 0)
        final_write_key = virtual_page_last_write_key[page];
    const bool writes_visible =
        virtual_page_completed_words[page] ==
            virtual_page_expected_words[page] ||
        (maa->virtual_idealized_write_ack && final_write_key != 0 &&
         virtual_page_issued_words[page] ==
             virtual_page_expected_words[page]);
    if (virtual_page_ready[page] ||
        virtual_page_scanned_words[page] != virtual_page_logical_words[page] ||
        virtual_page_issued_words[page] != virtual_page_expected_words[page] ||
        !writes_visible)
        return;

    const bool idealized_visibility =
        virtual_page_completed_words[page] !=
        virtual_page_expected_words[page];
    virtual_page_ready[page] = true;
    virtual_pages_ready++;
    // Normally a page with writes becomes visible only at its exact final
    // WriteResp. The diagnostic upper bound instead uses the final issued
    // transaction. A zero-write page is coherent after its complete scan.
    if (final_write_key == 0) {
        panic_if(virtual_page_expected_words[page] != 0,
                 "I[%d] page %d closed without its final WriteResp key\n",
                 my_indirect_id, page);
        final_write_key = std::numeric_limits<Addr>::max() -
            (static_cast<Addr>(my_indirect_id) << 8) - page;
    }
    maa->setVirtualPageReady(my_dst_tile, page, final_write_key);
    if (idealized_visibility) {
        (*maa->stats.IND_VirtIdealizedAckPages[my_indirect_id])++;
        DPRINTF(MAAVirtualTrace,
                "event=idealized_ack_page_ready schema=1 unit=%d "
                "operation_tick=%lu page=%d transaction=0x%lx issued=%d "
                "completed=%d\n",
                my_indirect_id, my_decode_start_tick, page, final_write_key,
                virtual_page_issued_words[page],
                virtual_page_completed_words[page]);
    }
    if (virtual_first_page_ready_tick == 0)
        virtual_first_page_ready_tick = curTick();
    if (virtual_pages_ready == static_cast<int>(virtual_page_ready.size()))
        virtual_all_pages_ready_tick = curTick();
    const bool sources_drained =
        my_fill_finished && !virtual_build_incomplete && my_i >= my_max &&
        virtual_source_received == virtual_source_expected &&
        virtual_reserved_responses == 0;
    if (!sources_drained)
        virtual_pages_ready_before_source_drain++;
    DPRINTF(MAAVirtualTrace,
            "event=page_ready schema=2 unit=%d occurrence=%lu page=%d "
            "operation_tick=%lu pages=%d/%d scanned=%d "
            "expected=%d issued=%d completed=%d sources_drained=%d\n",
            my_indirect_id, attribution_event_occurrence++, page,
            my_decode_start_tick, virtual_pages_ready,
            static_cast<int>(virtual_page_ready.size()),
            virtual_page_scanned_words[page],
            virtual_page_expected_words[page],
            virtual_page_issued_words[page],
            virtual_page_completed_words[page], sources_drained);
}

void IndirectAccessUnit::trackVirtualRetirementWrite(Addr write_key,
                                                      Addr vaddr,
                                                      unsigned size,
                                                      uint16_t valid_words) {
    initializeVirtualPageTracking();
    panic_if(size % my_word_size != 0,
             "I[%d] virtual write size %u is not word aligned\n",
             my_indirect_id, size);
    panic_if(virtual_retirement_write_pages.count(write_key) != 0,
             "I[%d] duplicate virtual write metadata for 0x%lx\n",
             my_indirect_id, write_key);

    std::map<int, int> page_words;
    const unsigned words = size / my_word_size;
    for (unsigned word = 0; word < words; ++word) {
        if (valid_words != 0 && (valid_words & (1U << word)) == 0)
            continue;
        const Addr word_vaddr = vaddr + word * my_word_size;
        panic_if(word_vaddr < my_backing_addr ||
                     (word_vaddr - my_backing_addr) % my_word_size != 0,
                 "I[%d] virtual write word 0x%lx does not map to backing "
                 "base 0x%lx\n",
                 my_indirect_id, word_vaddr, my_backing_addr);
        const int itr = (word_vaddr - my_backing_addr) / my_word_size;
        panic_if(itr < 0 || itr >= my_max,
                 "I[%d] virtual write iteration %d exceeds [0, %d)\n",
                 my_indirect_id, itr, my_max);
        const int page = itr / maa->physical_tile_elements;
        page_words[page]++;
        virtual_page_last_write_key[page] = write_key;
        virtual_page_issued_words[page]++;
        panic_if(virtual_page_issued_words[page] >
                     virtual_page_expected_words[page],
                 "I[%d] virtual page %d issued too many words: %d/%d\n",
                 my_indirect_id, page, virtual_page_issued_words[page],
                 virtual_page_expected_words[page]);
    }
    panic_if(page_words.empty(),
             "I[%d] virtual retirement write 0x%lx has no valid words\n",
             my_indirect_id, write_key);
    auto &metadata = virtual_retirement_write_pages[write_key];
    metadata.pageWords.assign(page_words.begin(), page_words.end());
    metadata.generation = maa->getVirtualPageGeneration(my_dst_tile);
    const Addr backing_offset = vaddr - my_backing_addr;
    panic_if(backing_offset % block_size + size > block_size,
             "I[%d] virtual retirement write crosses a backing line\n",
             my_indirect_id);
    metadata.backingLine = backing_offset / block_size;
    if (size == block_size) {
        metadata.backingWordMask = valid_words == 0
            ? static_cast<uint16_t>((1U << my_words_per_cl) - 1)
            : valid_words;
    } else {
        const unsigned first_word =
            (backing_offset % block_size) / my_word_size;
        const unsigned write_words = size / my_word_size;
        metadata.backingWordMask = static_cast<uint16_t>(
            ((1U << write_words) - 1) << first_word);
    }
}

void IndirectAccessUnit::completeVirtualRetirementWrite(
    Addr write_key, const uint8_t *writeRespPayload,
    unsigned payloadBytes) {
    auto metadata = virtual_retirement_write_pages.find(write_key);
    panic_if(metadata == virtual_retirement_write_pages.end(),
             "I[%d] completed virtual write 0x%lx has no page metadata\n",
             my_indirect_id, write_key);
    for (const auto &[page, words] : metadata->second.pageWords) {
        virtual_page_completed_words[page] += words;
        panic_if(virtual_page_completed_words[page] >
                     virtual_page_expected_words[page],
                 "I[%d] virtual page %d completed too many words: %d/%d\n",
                 my_indirect_id, page, virtual_page_completed_words[page],
                 virtual_page_expected_words[page]);
    }
    maa->setVirtualLineWordsReady(my_dst_tile, my_backing_addr,
                                  metadata->second.generation,
                                  metadata->second.backingLine,
                                  metadata->second.backingWordMask,
                                  write_key, writeRespPayload,
                                  payloadBytes);
    for (const auto &[page, words] : metadata->second.pageWords) {
        (void)words;
        markVirtualPageReadyIfComplete(page, write_key);
    }
    virtual_retirement_write_pages.erase(metadata);
}

bool IndirectAccessUnit::createRetirementWrite(int itr, const uint8_t *data) {
    const Addr vaddr = backingWordAddr(itr);
    return createRetirementWrite(vaddr, my_word_size, data);
}

bool IndirectAccessUnit::createRetirementWrite(Addr vaddr, unsigned size,
                                                const uint8_t *data,
                                                uint16_t valid_words) {
    accountVirtualRequestInterval();
    validateRetirementWriteRange(vaddr, size, valid_words);
    Addr paddr = translatePacket(vaddr, BaseMMU::Write, size);
    const Addr write_key = size == block_size
        ? paddr & ~(block_size - 1) : paddr;
    if (virtual_outstanding_write_lines.count(write_key) != 0) {
        macro_backing_address_retries++;
        return false;
    }
    if (maa->hasOutstandingPacket(paddr)) {
        (*maa->stats.IND_VirtWriteAddressConflicts[my_indirect_id])++;
        virtual_write_address_blocked = true;
        macro_backing_address_retries++;
        return false;
    }
    RequestPtr req = std::make_shared<Request>(paddr, size, flags,
                                               maa->requestorId);
    req->setRegion(my_backing_addr_range_id);
    std::vector<bool> byte_enable;
    if (valid_words != 0) {
        panic_if(size != block_size,
                 "I[%d] masked retirement write is not a cache line\n",
                 my_indirect_id);
        byte_enable.resize(block_size, false);
        for (unsigned word = 0; word < my_words_per_cl; ++word) {
            if ((valid_words & (1U << word)) == 0)
                continue;
            std::fill(byte_enable.begin() + word * my_word_size,
                      byte_enable.begin() + (word + 1) * my_word_size, true);
        }
    }
    PacketPtr pkt = new Packet(req, MemCmd::WriteReq);
    pkt->allocate();
    pkt->setData(data);
    if (!byte_enable.empty())
        req->setByteEnable(byte_enable);
    my_expected_responses++;
    virtual_outstanding_writes++;
    virtual_outstanding_write_lines.insert(write_key);
    trackVirtualRetirementWrite(write_key, vaddr, size, valid_words);
    if (maa->virtual_idealized_write_ack) {
        const auto metadata = virtual_retirement_write_pages.find(write_key);
        panic_if(metadata == virtual_retirement_write_pages.end(),
                 "I[%d] idealized write 0x%lx lacks page metadata\n",
                 my_indirect_id, write_key);
        for (const auto &[page, words] : metadata->second.pageWords) {
            (void)words;
            markVirtualPageReadyIfComplete(page, write_key);
        }
    }
    (*maa->stats.IND_VirtWriteIssues[my_indirect_id])++;
    attribution_write_issues++;
    if (macro_backing_first_issue_tick == 0)
        macro_backing_first_issue_tick = curTick();
    macro_backing_last_issue_tick = curTick();
    macro_backing_transport_bytes += size;
    macro_backing_semantic_bytes += valid_words == 0
        ? size : __builtin_popcount(valid_words) * my_word_size;
    if (size == block_size)
        macro_backing_line_issues++;
    else
        macro_backing_word_issues++;
    DPRINTF(MAAVirtualTrace,
            "event=backing_write_issue schema=2 unit=%d occurrence=%lu "
            "operation_tick=%lu key=0x%lx vaddr=0x%lx paddr=0x%lx "
            "bytes=%u valid_words=0x%x outstanding=%d\n",
            my_indirect_id, attribution_event_occurrence++,
            my_decode_start_tick, write_key, vaddr, paddr,
            size, valid_words, virtual_outstanding_writes);
    virtual_max_outstanding_writes = std::max(
        virtual_max_outstanding_writes, virtual_outstanding_writes);
    panic_if(virtual_outstanding_writes > virtual_max_outstanding_writes_limit,
             "I[%d] virtual retirement writes exceeded capacity\n",
             my_indirect_id);
    maa->sendPacket(FuncUnitType::INDIRECT, my_indirect_id, pkt,
                    maa->getClockEdge(Cycles(0)), true, true);
    accountVirtualRequestInterval();
    return true;
}


bool IndirectAccessUnit::drainVirtualResponses() {
    const bool virtual_load = isVirtualLoad();
    int native_spd_writes = 0;
    auto finish_drain = [this, &native_spd_writes](bool throttled) {
        if (native_spd_writes != 0)
            updateLatency(0, 0, native_spd_writes, 0, 0,
                          total_num_RT_subslices);
        return throttled;
    };
    auto release_native_claim = [this](VirtualResponseSlot &slot) {
        if (!maa->virtual_native_issue_order) {
            panic_if(slot.claim_row_id != -1 || slot.claim_entry_id != -1,
                     "I[%d] non-native response retained a claim token\n",
                     my_indirect_id);
            return;
        }
        panic_if(slot.claim_rt_idx < 0 || slot.claim_row_id < 0 ||
                     slot.claim_entry_id < 0 || slot.claim_head < 0,
                 "I[%d] native response has an incomplete claim token\n",
                 my_indirect_id);
        const bool released =
            RT[my_RT_config][slot.claim_rt_idx].release_native_claim(
                slot.claim_row_id, slot.claim_entry_id,
                slot.claim_grow_addr, slot.claim_addr, slot.claim_head);
        panic_if(!released,
                 "I[%d] native response 0x%lx head %d has no claim\n",
                 my_indirect_id, slot.claim_addr, slot.claim_head);
    };
    if (virtual_word_budget_tick != curTick()) {
        virtual_word_budget_tick = curTick();
        virtual_word_attempts_this_cycle = 0;
    }
    if (virtual_combine_bank_tick != curTick()) {
        virtual_combine_bank_tick = curTick();
        std::fill(virtual_combine_bank_used.begin(),
                  virtual_combine_bank_used.end(), false);
    }
    auto budget_exhausted = [this]() {
        return virtual_words_per_cycle_limit != 0 &&
               virtual_word_attempts_this_cycle >=
                   virtual_words_per_cycle_limit;
    };
    bool bank_stalled = false;
    for (size_t slot_idx = 0;
         slot_idx < virtual_response_slots.size(); ++slot_idx) {
        auto &slot = virtual_response_slots[slot_idx];
        if (virtual_response_words != 0 ||
            virtual_response_word_pool_limit != 0) {
            bool capacity_stalled = false;
            while (slot.valid &&
                   slot.next_packed_word < slot.packed_words.size()) {
                if (budget_exhausted())
                    return finish_drain(true);
                const OffsetTableEntry entry =
                    offset_table->peek_entry(slot.next_itr);
                const auto &word = slot.packed_words[slot.next_packed_word];
                virtual_word_attempts_this_cycle++;
                if (virtual_load) {
                    if (entry.pass >= 0)
                        direct_index_partition = entry.pass;
                    if (!reserveVirtualCombineBank(entry.itr)) {
                        virtual_word_attempts_this_cycle--;
                        bank_stalled = true;
                        break;
                    }
                    if (!insertVirtualCombineWord(entry.itr, word.data())) {
                        capacity_stalled = true;
                        break;
                    }
                } else {
                    panic_if(my_dst_tile == -1,
                             "I[%d] bounded native load has no destination "
                             "tile\n",
                             my_indirect_id);
                    if (my_word_size == 4) {
                        uint32_t value;
                        std::memcpy(&value, word.data(), sizeof(value));
                        maa->spd->setData<uint32_t>(my_dst_tile, entry.itr,
                                                    value);
                    } else {
                        uint64_t value;
                        std::memcpy(&value, word.data(), sizeof(value));
                        maa->spd->setData<uint64_t>(my_dst_tile, entry.itr,
                                                    value);
                    }
                    native_spd_writes++;
                }
                OffsetTableEntry consumed =
                    offset_table->consume_entry(slot.next_itr);
                panic_if(consumed.itr != entry.itr || consumed.wid != entry.wid,
                         "I[%d] packed response cursor changed while stalled\n",
                         my_indirect_id);
                recordReorderSurvivalIssuedEntries(1);
                slot.next_packed_word++;
            }
            if (slot.valid &&
                slot.next_packed_word == slot.packed_words.size()) {
                panic_if(slot.next_itr != -1,
                         "I[%d] packed response ended before offset chain\n",
                         my_indirect_id);
                panic_if(virtual_reserved_response_words < slot.reserved_words,
                         "I[%d] packed response word accounting underflow\n",
                         my_indirect_id);
                virtual_reserved_response_words -= slot.reserved_words;
                release_native_claim(slot);
                slot = VirtualResponseSlot();
                virtual_reserved_responses--;
            }
            if (capacity_stalled)
                break;
            continue;
        }
        bool capacity_stalled = false;
        while (slot.valid) {
            if (budget_exhausted())
                return finish_drain(true);
            OffsetTableEntry entry = offset_table->peek_entry(slot.next_itr);
            virtual_word_attempts_this_cycle++;
            const uint8_t *word =
                virtual_response_line_payloads.lineData(slot_idx) +
                entry.wid * my_word_size;
            if (virtual_load) {
                if (entry.pass >= 0)
                    direct_index_partition = entry.pass;
                if (!reserveVirtualCombineBank(entry.itr)) {
                    virtual_word_attempts_this_cycle--;
                    bank_stalled = true;
                    break;
                }
                if (!insertVirtualCombineWord(entry.itr, word)) {
                    capacity_stalled = true;
                    break;
                }
            } else {
                panic_if(my_dst_tile == -1,
                         "I[%d] bounded native load has no destination tile\n",
                         my_indirect_id);
                if (my_word_size == 4) {
                    uint32_t value;
                    std::memcpy(&value, word, sizeof(value));
                    maa->spd->setData<uint32_t>(my_dst_tile, entry.itr,
                                                value);
                } else {
                    uint64_t value;
                    std::memcpy(&value, word, sizeof(value));
                    maa->spd->setData<uint64_t>(my_dst_tile, entry.itr,
                                                value);
                }
                native_spd_writes++;
            }
            OffsetTableEntry consumed =
                offset_table->consume_entry(slot.next_itr);
            panic_if(consumed.itr != entry.itr || consumed.wid != entry.wid,
                     "I[%d] virtual offset cursor changed while stalled\n",
                     my_indirect_id);
            recordReorderSurvivalIssuedEntries(1);
            if (slot.next_itr == -1) {
                release_native_claim(slot);
                slot = VirtualResponseSlot();
                virtual_reserved_responses--;
            }
        }
        if (capacity_stalled)
            break;
    }
    if (virtual_load)
        drainVirtualCombiner(false);
    return finish_drain(bank_stalled);
}

bool IndirectAccessUnit::reserveVirtualCombineBank(int itr) {
    if (virtual_combine_banks == 0)
        return true;

    const Addr vaddr = backingWordAddr(itr);
    const Addr line_vaddr = vaddr & ~(block_size - 1);
    const int ways = virtual_combine_ways;
    const int num_sets = virtual_combine_slots.size() / ways;
    const int set = (line_vaddr / block_size) % num_sets;
    const int bank = set % virtual_combine_banks;
    if (virtual_combine_bank_used[bank]) {
        if (virtual_combine_bank_conflict_tick != curTick()) {
            virtual_combine_bank_conflict_tick = curTick();
            (*maa->stats.IND_VirtCombineBankConflictCycles[my_indirect_id])++;
        }
        return false;
    }
    virtual_combine_bank_used[bank] = true;
    (*maa->stats.IND_VirtCombineBankAccesses[my_indirect_id])++;
    return true;
}

bool IndirectAccessUnit::insertVirtualCombineWord(int itr,
                                                   const uint8_t *data) {
    // Each logical gather iteration owns one non-aliasing backing-array word.
    panic_if(virtual_combine_payload.used() !=
                 static_cast<size_t>(virtual_combine_words),
             "I[%d] virtual payload occupancy mismatch: %zu != %d\n",
             my_indirect_id, virtual_combine_payload.used(),
             virtual_combine_words);
    const Addr vaddr = backingWordAddr(itr);
    const Addr line_vaddr = vaddr & ~(block_size - 1);
    const unsigned word = (vaddr - line_vaddr) / my_word_size;
    const uint16_t word_bit = 1U << word;
    VirtualCombineSlot *target = nullptr;
    VirtualCombineSlot *free_slot = nullptr;
    const int ways = virtual_combine_ways == 0
        ? virtual_combine_slots.size() : virtual_combine_ways;
    const int num_sets = virtual_combine_slots.size() / ways;
    const int set = virtual_combine_ways == 0
        ? 0 : (line_vaddr / block_size) % num_sets;
    const int set_begin = set * ways;
    const int set_end = set_begin + ways;
    for (int idx = set_begin; idx < set_end; ++idx) {
        auto &slot = virtual_combine_slots[idx];
        if (slot.valid && slot.line_vaddr == line_vaddr) {
            target = &slot;
            break;
        }
        if (!slot.valid && free_slot == nullptr)
            free_slot = &slot;
    }
    if (virtual_combine_payload.full())
        drainVirtualCombiner(false);
    panic_if(virtual_combine_payload.used() !=
                 static_cast<size_t>(virtual_combine_words),
             "I[%d] virtual payload occupancy diverged after drain\n",
             my_indirect_id);
    const bool word_capacity_full = virtual_combine_payload.full();
    const bool line_capacity_full = target == nullptr && free_slot == nullptr;
    if (word_capacity_full || line_capacity_full) {
        int victim_idx = -1;
        const int victim_start = virtual_combine_ways == 0
            ? virtual_combine_victim
            : virtual_combine_set_victims[set];
        int victim_words = 0;
        for (int offset = 0; offset < ways; ++offset) {
            const int idx = set_begin + (victim_start + offset) % ways;
            const auto &candidate = virtual_combine_slots[idx];
            if (!candidate.valid || &candidate == target)
                continue;
            const int candidate_words =
                __builtin_popcount(candidate.valid_words);
            if (victim_idx == -1 ||
                (virtual_combine_victim_policy == 1 &&
                 candidate_words < victim_words) ||
                (virtual_combine_victim_policy == 2 &&
                 candidate_words > victim_words)) {
                victim_idx = idx;
                victim_words = candidate_words;
                if (virtual_combine_victim_policy == 0)
                    break;
            }
        }
        if (victim_idx == -1 && target != nullptr)
            victim_idx = target - virtual_combine_slots.data();
        panic_if(victim_idx == -1,
                 "I[%d] virtual combiner has no valid victim\n", my_indirect_id);
        auto &victim = virtual_combine_slots[victim_idx];
        const bool victim_was_full =
            victim.valid_words == ((1U << my_words_per_cl) - 1);
        bool victim_page_ready = maa->virtual_page_ordered_combiner_drain &&
            victim_was_full;
        auto retire_full_victim = [&]() {
            if (!victim_page_ready)
                return;
            panic_if(!virtual_combine_page_ready.retireFullLine(victim_idx),
                     "I[%d] full combiner victim %d is absent from "
                     "page-ready metadata\n", my_indirect_id, victim_idx);
            victim_page_ready = false;
        };
        if (virtual_masked_writes && victim.valid_words != 0 &&
            virtual_outstanding_writes <
                virtual_max_outstanding_writes_limit) {
            const int words = __builtin_popcount(victim.valid_words);
            VirtualCombinePayloadStore::LineData line_data{};
            const auto copy_result = virtual_combine_payload.copyLine(
                victim.word_refs, victim.valid_words, my_word_size, line_data);
            panic_if(copy_result != VirtualCombinePayloadStore::Result::Ok,
                     "I[%d] could not stage masked victim 0x%lx: %s\n",
                     my_indirect_id, victim.line_vaddr,
                     VirtualCombinePayloadStore::resultName(copy_result));
            if (createRetirementWrite(victim.line_vaddr, block_size,
                                      line_data.data(), victim.valid_words)) {
                retire_full_victim();
                const auto release_result =
                    virtual_combine_payload.releaseMasked(
                        victim.word_refs, victim.valid_words);
                panic_if(
                    release_result != VirtualCombinePayloadStore::Result::Ok,
                    "I[%d] could not release masked victim 0x%lx: %s\n",
                    my_indirect_id, victim.line_vaddr,
                    VirtualCombinePayloadStore::resultName(release_result));
                virtual_combine_words -= words;
                virtual_partial_word_writes++;
                victim.valid_words = 0;
            }
        } else {
            while (victim.valid_words != 0 &&
                   virtual_outstanding_writes <
                       virtual_max_outstanding_writes_limit) {
                unsigned victim_word = __builtin_ctz(victim.valid_words);
                const uint8_t *word_data = virtual_combine_payload.data(
                    victim.word_refs[victim_word]);
                panic_if(word_data == nullptr,
                         "I[%d] victim 0x%lx word %u has no payload\n",
                         my_indirect_id, victim.line_vaddr, victim_word);
                if (!createRetirementWrite(
                        victim.line_vaddr + victim_word * my_word_size,
                        my_word_size, word_data))
                    break;
                retire_full_victim();
                const auto release_result = virtual_combine_payload.release(
                    victim.word_refs[victim_word]);
                panic_if(
                    release_result != VirtualCombinePayloadStore::Result::Ok,
                    "I[%d] could not release victim 0x%lx word %u: %s\n",
                    my_indirect_id, victim.line_vaddr, victim_word,
                    VirtualCombinePayloadStore::resultName(release_result));
                victim.valid_words &= ~(1U << victim_word);
                panic_if(virtual_combine_words == 0,
                         "I[%d] virtual word accounting underflow\n",
                         my_indirect_id);
                virtual_combine_words--;
                virtual_partial_word_writes++;
            }
        }
        if (victim.valid_words != 0)
            return false;
        if (target == &victim)
            target = nullptr;
        victim = VirtualCombineSlot();
        free_slot = &victim;
        if (virtual_combine_ways == 0)
            virtual_combine_victim = (victim_idx + 1) % ways;
        else
            virtual_combine_set_victims[set] =
                (victim_idx - set_begin + 1) % ways;
    }
    if (target == nullptr)
        target = free_slot;
    panic_if(target == nullptr,
             "I[%d] virtual combiner has no insertion slot\n", my_indirect_id);
    panic_if(target->valid_words & word_bit,
             "I[%d] duplicate virtual output word %d at 0x%lx\n",
             my_indirect_id, word, line_vaddr);
    const auto allocate_result = virtual_combine_payload.allocate(
        data, my_word_size, target->word_refs[word]);
    if (allocate_result == VirtualCombinePayloadStore::Result::Exhausted)
        return false;
    panic_if(allocate_result != VirtualCombinePayloadStore::Result::Ok,
             "I[%d] could not allocate virtual output word %d at 0x%lx: %s\n",
             my_indirect_id, word, line_vaddr,
             VirtualCombinePayloadStore::resultName(allocate_result));
    if (!target->valid) {
        target->valid = true;
        target->line_vaddr = line_vaddr;
        const int occupancy = std::count_if(
            virtual_combine_slots.begin(), virtual_combine_slots.end(),
            [](const VirtualCombineSlot &slot) { return slot.valid; });
        virtual_max_combine_occupancy =
            std::max(virtual_max_combine_occupancy, occupancy);
    }
    target->valid_words |= word_bit;
    if (maa->virtual_page_ordered_combiner_drain &&
        target->valid_words == ((1U << my_words_per_cl) - 1)) {
        const Addr output_begin = my_backing_addr;
        const Addr output_end = backingWordAddr(my_max - 1) + my_word_size;
        uint32_t page = 0;
        panic_if(output_end <= output_begin ||
                     !VirtualCombinerPageOrder::linePage(
                         target->line_vaddr, output_begin, output_end,
                         my_word_size, page) ||
                     page >= VirtualCombinerPageOrder::MaxPages ||
                     !virtual_combine_page_ready.enqueue(
                         target - virtual_combine_slots.data(), page),
                 "I[%d] could not enqueue full combiner line 0x%lx in "
                 "page-ready metadata\n", my_indirect_id, target->line_vaddr);
    }
    virtual_combine_words++;
    attribution_combiner_words++;
    if (usesBoundedDirectIndexPasses()) {
        const int pass = directIndexRetirementPass();
        const auto result = bounded_range_pass.recordRetirement(itr, pass);
        panic_if(result != BoundedRangePassTracker::Result::Accepted,
                 "I[%d] bounded range retirement itr=%d pass=%d failed: %s\n",
                 my_indirect_id, itr, pass,
                 BoundedRangePassTracker::resultName(result));
    }
    panic_if(virtual_combine_words > virtual_combine_words_limit,
             "I[%d] virtual combiner exceeded word capacity: %d/%d\n",
             my_indirect_id, virtual_combine_words,
             virtual_combine_words_limit);
    panic_if(virtual_combine_payload.used() !=
                 static_cast<size_t>(virtual_combine_words),
             "I[%d] virtual payload occupancy mismatch after insert\n",
             my_indirect_id);
    virtual_max_combine_words =
        std::max(virtual_max_combine_words, virtual_combine_words);
    return true;
}

void IndirectAccessUnit::drainVirtualCombiner(bool flush_partial) {
    const uint16_t full_mask = (1U << my_words_per_cl) - 1;
    panic_if(virtual_combine_payload.used() !=
                 static_cast<size_t>(virtual_combine_words),
             "I[%d] virtual payload occupancy mismatch before drain\n",
             my_indirect_id);
    if (maa->virtual_page_ordered_combiner_drain && my_max > 0) {
        const Addr output_begin = my_backing_addr;
        const Addr output_end = backingWordAddr(my_max - 1) + my_word_size;
        panic_if(output_end <= output_begin,
                 "I[%d] page-ordered combiner output range wrapped\n",
                 my_indirect_id);

        // The selector is a fixed MaxPages (16) ready-head encoder.  Full
        // lines entered their intrusive page queue on the partial-to-full
        // transition, so no combiner slot is compared here.
        while (virtual_outstanding_writes <
               virtual_max_outstanding_writes_limit) {
            uint32_t selected_page = 0;
            const int selected =
                virtual_combine_page_ready.firstReady(selected_page);
            if (selected == -1)
                break;
            auto &slot = virtual_combine_slots[selected];
            panic_if(!slot.valid || slot.valid_words != full_mask,
                     "I[%d] page-ready slot %d is not a full combiner line\n",
                     my_indirect_id, selected);
            VirtualCombinePayloadStore::LineData line_data{};
            const auto copy_result = virtual_combine_payload.copyLine(
                slot.word_refs, slot.valid_words, my_word_size, line_data);
            panic_if(copy_result != VirtualCombinePayloadStore::Result::Ok,
                     "I[%d] could not stage page-ready slot %d: %s\n",
                     my_indirect_id, selected,
                     VirtualCombinePayloadStore::resultName(copy_result));
            if (!createRetirementWrite(slot.line_vaddr, block_size,
                                       line_data.data()))
                break;
            (*maa->stats.IND_VirtPageOrderedDrainSelections[my_indirect_id])++;
            if (virtual_combine_page_ready.hasReadyLater(selected_page)) {
                (*maa->stats.IND_VirtPageOrderedDrainDeferrals[
                    my_indirect_id])++;
            }
            panic_if(!virtual_combine_page_ready.retireFullLine(selected),
                     "I[%d] page-ready slot %d could not retire\n",
                     my_indirect_id, selected);
            const auto release_result = virtual_combine_payload.releaseMasked(
                slot.word_refs, slot.valid_words);
            panic_if(release_result != VirtualCombinePayloadStore::Result::Ok,
                     "I[%d] could not release page-ready slot %d: %s\n",
                     my_indirect_id, selected,
                     VirtualCombinePayloadStore::resultName(release_result));
            virtual_full_line_writes++;
            panic_if(virtual_combine_words < my_words_per_cl,
                     "I[%d] virtual full-line accounting underflow\n",
                     my_indirect_id);
            virtual_combine_words -= my_words_per_cl;
            slot = VirtualCombineSlot();
        }
    }
    for (auto &slot : virtual_combine_slots) {
        if (!slot.valid)
            continue;
        if (maa->virtual_page_ordered_combiner_drain &&
            slot.valid_words == full_mask)
            continue;
        if (slot.valid_words == full_mask &&
            virtual_outstanding_writes <
                virtual_max_outstanding_writes_limit) {
            VirtualCombinePayloadStore::LineData line_data{};
            const auto copy_result = virtual_combine_payload.copyLine(
                slot.word_refs, slot.valid_words, my_word_size, line_data);
            panic_if(copy_result != VirtualCombinePayloadStore::Result::Ok,
                     "I[%d] could not stage full combiner line 0x%lx: %s\n",
                     my_indirect_id, slot.line_vaddr,
                     VirtualCombinePayloadStore::resultName(copy_result));
            if (!createRetirementWrite(slot.line_vaddr, block_size,
                                       line_data.data()))
                continue;
            const auto release_result = virtual_combine_payload.releaseMasked(
                slot.word_refs, slot.valid_words);
            panic_if(release_result != VirtualCombinePayloadStore::Result::Ok,
                     "I[%d] could not release full combiner line 0x%lx: %s\n",
                     my_indirect_id, slot.line_vaddr,
                     VirtualCombinePayloadStore::resultName(release_result));
            virtual_full_line_writes++;
            panic_if(virtual_combine_words < my_words_per_cl,
                     "I[%d] virtual full-line accounting underflow\n",
                     my_indirect_id);
            virtual_combine_words -= my_words_per_cl;
            slot = VirtualCombineSlot();
            continue;
        }
        if (!flush_partial)
            continue;
        if (virtual_masked_writes && slot.valid_words != 0 &&
            virtual_outstanding_writes <
                virtual_max_outstanding_writes_limit) {
            const int words = __builtin_popcount(slot.valid_words);
            VirtualCombinePayloadStore::LineData line_data{};
            const auto copy_result = virtual_combine_payload.copyLine(
                slot.word_refs, slot.valid_words, my_word_size, line_data);
            panic_if(copy_result != VirtualCombinePayloadStore::Result::Ok,
                     "I[%d] could not stage masked combiner line 0x%lx: %s\n",
                     my_indirect_id, slot.line_vaddr,
                     VirtualCombinePayloadStore::resultName(copy_result));
            if (createRetirementWrite(slot.line_vaddr, block_size,
                                      line_data.data(), slot.valid_words)) {
                const auto release_result =
                    virtual_combine_payload.releaseMasked(
                        slot.word_refs, slot.valid_words);
                panic_if(
                    release_result != VirtualCombinePayloadStore::Result::Ok,
                    "I[%d] could not release masked combiner line 0x%lx: %s\n",
                    my_indirect_id, slot.line_vaddr,
                    VirtualCombinePayloadStore::resultName(release_result));
                virtual_combine_words -= words;
                virtual_partial_word_writes++;
                slot = VirtualCombineSlot();
            }
            continue;
        }
        while (slot.valid_words != 0 &&
               virtual_outstanding_writes <
                   virtual_max_outstanding_writes_limit) {
            unsigned word = __builtin_ctz(slot.valid_words);
            const uint8_t *word_data = virtual_combine_payload.data(
                slot.word_refs[word]);
            panic_if(word_data == nullptr,
                     "I[%d] combiner line 0x%lx word %u has no payload\n",
                     my_indirect_id, slot.line_vaddr, word);
            if (!createRetirementWrite(
                    slot.line_vaddr + word * my_word_size, my_word_size,
                    word_data))
                break;
            const auto release_result = virtual_combine_payload.release(
                slot.word_refs[word]);
            panic_if(release_result != VirtualCombinePayloadStore::Result::Ok,
                     "I[%d] could not release line 0x%lx word %u: %s\n",
                     my_indirect_id, slot.line_vaddr, word,
                     VirtualCombinePayloadStore::resultName(release_result));
            slot.valid_words &= ~(1U << word);
            panic_if(virtual_combine_words == 0,
                     "I[%d] virtual word accounting underflow\n", my_indirect_id);
            virtual_combine_words--;
            virtual_partial_word_writes++;
        }
        if (slot.valid_words == 0)
            slot = VirtualCombineSlot();
        if (virtual_outstanding_writes == virtual_max_outstanding_writes_limit)
            break;
    }
    if (!virtualCombinerEmpty() &&
        virtual_outstanding_writes >= virtual_max_outstanding_writes_limit &&
        macro_backing_credit_stall_tick != curTick()) {
        macro_backing_credit_stall_tick = curTick();
        macro_backing_credit_stalls++;
    }
    panic_if(virtual_combine_payload.used() !=
                 static_cast<size_t>(virtual_combine_words),
             "I[%d] virtual payload occupancy mismatch after drain\n",
             my_indirect_id);
}

bool IndirectAccessUnit::virtualCombinerEmpty() const {
    return virtual_combine_payload.empty() &&
        std::all_of(virtual_combine_slots.begin(),
                    virtual_combine_slots.end(),
                    [](const VirtualCombineSlot &slot) {
                        return !slot.valid;
                    });
}

bool IndirectAccessUnit::boundedSourceResponsesComplete() const {
    const bool responses_empty = std::all_of(
        virtual_response_slots.begin(), virtual_response_slots.end(),
        [](const VirtualResponseSlot &slot) { return !slot.valid; });
    return virtual_source_received == virtual_source_expected &&
           virtual_reserved_responses == 0 &&
           virtual_reserved_response_words == 0 &&
           virtual_source_reservations.empty() && !virtual_pending_source &&
           responses_empty && maa->allIndirectPacketsSent(my_indirect_id) &&
           my_received_responses == my_expected_responses;
}

bool IndirectAccessUnit::boundedRetirementComplete() const {
    const bool sources_complete = boundedSourceResponsesComplete();
    if (!sources_complete || !isVirtualLoad())
        return sources_complete;
    return virtualCombinerEmpty() && virtual_outstanding_writes == 0;
}

IndirectAccessUnit::VirtualRequestReason
IndirectAccessUnit::classifyVirtualRequestReason() const {
    if (state == Status::Build)
        return VirtualRequestReason::Build;

    const bool sources_arrived =
        virtual_source_received == virtual_source_expected;
    const bool sources_drained = sources_arrived && virtual_reserved_responses == 0;
    if (virtual_final_flush && sources_drained &&
        (!virtualCombinerEmpty() || virtual_outstanding_writes != 0))
        return VirtualRequestReason::FinalDrain;
    if (!sources_arrived)
        return VirtualRequestReason::SourceFlight;
    if (virtual_reserved_responses != 0)
        return VirtualRequestReason::Retained;
    if (virtual_outstanding_writes != 0)
        return VirtualRequestReason::Writes;
    return VirtualRequestReason::Runnable;
}

void IndirectAccessUnit::accountVirtualRequestInterval() {
    if (my_request_start_tick == 0 || my_instruction == nullptr ||
        !isVirtualLoad())
        return;

    const Tick now = curTick();
    if (virtual_pipeline_tick != 0 && now != virtual_pipeline_tick) {
        const Tick elapsed = now - virtual_pipeline_tick;
        virtual_pipeline_attributed_ticks += elapsed;
        virtual_pipeline_ticks[virtual_pipeline_state] += elapsed;
    }
    const bool source_active =
        virtual_source_received != virtual_source_expected;
    const bool write_active = virtual_outstanding_writes != 0;
    virtual_pipeline_state = (source_active ? 1 : 0) |
                             (write_active ? 2 : 0);
    virtual_pipeline_tick = now;

    if (virtual_request_reason_tick != 0 &&
        now != virtual_request_reason_tick) {
        const Tick elapsed = now - virtual_request_reason_tick;
        virtual_request_attributed_ticks += elapsed;
        size_t bucket = 0;
        switch (virtual_request_reason) {
        case VirtualRequestReason::Build:
            bucket = 0;
            break;
        case VirtualRequestReason::SourceFlight:
            bucket = 1;
            break;
        case VirtualRequestReason::Retained:
            bucket = 2;
            break;
        case VirtualRequestReason::Writes:
            bucket = 3;
            break;
        case VirtualRequestReason::FinalDrain:
            bucket = 4;
            break;
        case VirtualRequestReason::Runnable:
            bucket = 5;
            break;
        case VirtualRequestReason::None:
            panic("I[%d] virtual request interval has no reason\n",
                  my_indirect_id);
        }
        virtual_request_reason_ticks[bucket] += elapsed;
    }
    virtual_request_reason = classifyVirtualRequestReason();
    virtual_request_reason_tick = now;
}

void IndirectAccessUnit::startVirtualRequestInterval() {
    if (!isVirtualLoad())
        return;
    panic_if(virtual_request_reason_tick != 0,
             "I[%d] virtual request attribution already active\n",
             my_indirect_id);
    virtual_request_attributed_ticks = 0;
    virtual_request_reason_ticks.fill(0);
    virtual_request_reason = classifyVirtualRequestReason();
    virtual_request_reason_tick = curTick();
    virtual_pipeline_attributed_ticks = 0;
    virtual_pipeline_ticks.fill(0);
    virtual_pipeline_state =
        (virtual_source_received != virtual_source_expected ? 1 : 0) |
        (virtual_outstanding_writes != 0 ? 2 : 0);
    virtual_pipeline_tick = curTick();
}

void IndirectAccessUnit::finishVirtualRequestInterval() {
    if (!isVirtualLoad())
        return;
    accountVirtualRequestInterval();
    panic_if(virtual_request_attributed_ticks !=
                 curTick() - my_request_start_tick,
             "I[%d] virtual request attribution mismatch: %lu != %lu ticks\n",
             my_indirect_id, virtual_request_attributed_ticks,
             curTick() - my_request_start_tick);
    panic_if(virtual_pipeline_attributed_ticks !=
                 curTick() - my_request_start_tick,
             "I[%d] virtual pipeline attribution mismatch: %lu != %lu ticks\n",
             my_indirect_id, virtual_pipeline_attributed_ticks,
             curTick() - my_request_start_tick);
    std::array<statistics::Scalar *, 6> buckets = {
        maa->stats.IND_VirtRequestCyclesBuild[my_indirect_id],
        maa->stats.IND_VirtRequestCyclesSourceFlight[my_indirect_id],
        maa->stats.IND_VirtRequestCyclesRetained[my_indirect_id],
        maa->stats.IND_VirtRequestCyclesWrites[my_indirect_id],
        maa->stats.IND_VirtRequestCyclesFinalDrain[my_indirect_id],
        maa->stats.IND_VirtRequestCyclesRunnable[my_indirect_id],
    };
    const Cycles request_cycles =
        maa->getTicksToCycles(curTick() - my_request_start_tick);
    std::array<uint64_t, 6> request_reason_cycles{};
    Cycles non_source_cycles(0);
    for (size_t i = 0; i < buckets.size(); ++i) {
        if (i == 1)
            continue;
        Cycles cycles = maa->getTicksToCycles(virtual_request_reason_ticks[i]);
        (*buckets[i]) += cycles;
        request_reason_cycles[i] = cycles;
        non_source_cycles += cycles;
    }
    panic_if(non_source_cycles > request_cycles,
             "I[%d] non-source virtual request buckets exceed total cycles\n",
             my_indirect_id);
    // Assign the residual to the dominant source-flight bucket so integer cycle
    // rounding cannot make the mutually exclusive buckets exceed the total.
    const Cycles source_cycles = request_cycles - non_source_cycles;
    (*buckets[1]) += source_cycles;
    request_reason_cycles[1] = source_cycles;
    for (size_t i = 0; i < request_reason_cycles.size(); ++i)
        macro_request_reason_cycles[i] += request_reason_cycles[i];
    std::array<statistics::Scalar *, 4> pipeline_buckets = {
        maa->stats.IND_VirtPipelineCyclesIdle[my_indirect_id],
        maa->stats.IND_VirtPipelineCyclesSourceOnly[my_indirect_id],
        maa->stats.IND_VirtPipelineCyclesWriteOnly[my_indirect_id],
        maa->stats.IND_VirtPipelineCyclesOverlap[my_indirect_id],
    };
    std::array<uint64_t, 4> pipeline_cycles{};
    uint64_t pipeline_cycle_sum = 0;
    size_t largest_pipeline_bucket = 0;
    for (size_t i = 0; i < pipeline_buckets.size(); ++i) {
        pipeline_cycles[i] =
            maa->getTicksToCycles(virtual_pipeline_ticks[i]);
        pipeline_cycle_sum += pipeline_cycles[i];
        if (virtual_pipeline_ticks[i] >
            virtual_pipeline_ticks[largest_pipeline_bucket])
            largest_pipeline_bucket = i;
    }
    const uint64_t total_request_cycles = request_cycles;
    if (pipeline_cycle_sum < total_request_cycles) {
        pipeline_cycles[largest_pipeline_bucket] +=
            total_request_cycles - pipeline_cycle_sum;
    } else if (pipeline_cycle_sum > total_request_cycles) {
        const uint64_t rounding_excess =
            pipeline_cycle_sum - total_request_cycles;
        panic_if(pipeline_cycles[largest_pipeline_bucket] < rounding_excess,
                 "I[%d] virtual pipeline rounding exceeds largest bucket\n",
                 my_indirect_id);
        pipeline_cycles[largest_pipeline_bucket] -= rounding_excess;
    }
    for (size_t i = 0; i < pipeline_buckets.size(); ++i) {
        (*pipeline_buckets[i]) += Cycles(pipeline_cycles[i]);
        macro_pipeline_cycles[i] += pipeline_cycles[i];
    }
    virtual_request_reason = VirtualRequestReason::None;
    virtual_request_reason_tick = 0;
    virtual_request_attributed_ticks = 0;
    virtual_request_reason_ticks.fill(0);
    virtual_pipeline_state = 0;
    virtual_pipeline_tick = 0;
    virtual_pipeline_attributed_ticks = 0;
    virtual_pipeline_ticks.fill(0);
}

void IndirectAccessUnit::transitionAttributionStage(
    AttributionStage next, const char *reason) {
    static constexpr const char *stage_names[] = {
        "none", "decode", "fill", "build", "request", "response"};
    const Tick now = curTick();
    if (attribution_stage != AttributionStage::None) {
        panic_if(attribution_stage_tick == 0 || now < attribution_stage_tick,
                 "I[%d] invalid attribution stage interval\n",
                 my_indirect_id);
        const size_t index = static_cast<size_t>(attribution_stage) - 1;
        const Tick elapsed = now - attribution_stage_tick;
        attribution_stage_ticks[index] += elapsed;
        DPRINTF(MAAVirtualTrace,
                "event=indirect_stage_interval schema=2 unit=%d "
                "occurrence=%lu "
                "operation_tick=%lu stage=%s start=%lu end=%lu "
                "sim_ticks=%lu cycles=%lu reason=%s\n",
                my_indirect_id, attribution_event_occurrence++,
                my_decode_start_tick,
                stage_names[static_cast<size_t>(attribution_stage)],
                attribution_stage_tick, now, elapsed,
                static_cast<uint64_t>(maa->getTicksToCycles(elapsed)),
                reason);
    }
    attribution_stage = next;
    if (next == AttributionStage::None) {
        Tick total = 0;
        for (const Tick ticks : attribution_stage_ticks)
            total += ticks;
        DPRINTF(MAAVirtualTrace,
                "event=indirect_stage_summary schema=2 unit=%d "
                "occurrence=%lu "
                "operation_tick=%lu decode_sim_ticks=%lu "
                "fill_sim_ticks=%lu build_sim_ticks=%lu "
                "request_sim_ticks=%lu response_sim_ticks=%lu "
                "total_sim_ticks=%lu\n",
                my_indirect_id, attribution_event_occurrence++,
                my_decode_start_tick,
                attribution_stage_ticks[0], attribution_stage_ticks[1],
                attribution_stage_ticks[2], attribution_stage_ticks[3],
                attribution_stage_ticks[4], total);
        if (isVirtualLoad()) {
            DPRINTF(MAAMacroEvent,
                    "event=hybrid_producer_macro schema=1 unit=%d "
                    "generation=%lu registration_tick=%lu "
                    "operation_tick=%lu complete_tick=%lu "
                    "b_first_issue_tick=%lu b_last_issue_tick=%lu "
                    "b_last_response_tick=%lu b_lines=%lu b_bytes=%lu "
                    "b_retries=%lu b_queue_high_water=%lu "
                    "row_offset_first_insert_tick=%lu "
                    "row_offset_last_insert_tick=%lu "
                    "fill_sim_ticks=%lu build_sim_ticks=%lu "
                    "row_insert_attempts=%lu row_offset_insertions=%lu "
                    "offset_pressure_events=%lu row_pressure_events=%lu "
                    "a_first_issue_tick=%lu a_last_issue_tick=%lu "
                    "a_last_response_tick=%lu a_lines=%lu a_bytes=%lu "
                    "a_retries=%lu a_slot_queue_high_water=%d "
                    "a_word_queue_high_water=%d "
                    "backing_first_issue_tick=%lu "
                    "backing_last_issue_tick=%lu "
                    "backing_last_ack_tick=%lu "
                    "page_first_ready_tick=%lu page_last_ready_tick=%lu "
                    "pages_ready=%d backing_transport_bytes=%lu "
                    "backing_semantic_bytes=%lu backing_line_issues=%lu "
                    "backing_word_issues=%lu backing_credit_stalls=%lu "
                    "backing_address_retries=%lu "
                    "backing_queue_high_water=%d "
                    "pipeline_no_source_or_write_cycles=%lu "
                    "pipeline_source_only_cycles=%lu "
                    "pipeline_write_only_cycles=%lu "
                    "pipeline_overlap_cycles=%lu "
                    "request_build_cycles=%lu "
                    "request_source_flight_cycles=%lu "
                    "request_retained_cycles=%lu request_writes_cycles=%lu "
                    "request_final_drain_cycles=%lu "
                    "request_runnable_cycles=%lu\n",
                    my_indirect_id,
                    maa->getVirtualPageGeneration(my_dst_tile),
                    maa->getVirtualProducerRegistrationTick(my_dst_tile),
                    my_decode_start_tick, now,
                    macro_b_first_issue_tick, macro_b_last_issue_tick,
                    macro_b_last_response_tick, macro_b_lines, macro_b_bytes,
                    macro_b_retries, macro_b_queue_high_water,
                    macro_row_first_insert_tick, macro_row_last_insert_tick,
                    attribution_stage_ticks[1], attribution_stage_ticks[2],
                    attribution_row_insert_attempts,
                    attribution_row_insert_successes,
                    attribution_offset_pressure_events,
                    attribution_row_pressure_events,
                    macro_a_first_issue_tick, macro_a_last_issue_tick,
                    macro_a_last_response_tick, macro_a_lines, macro_a_bytes,
                    macro_a_retries, virtual_max_reserved_responses,
                    virtual_max_reserved_response_words,
                    macro_backing_first_issue_tick,
                    macro_backing_last_issue_tick,
                    macro_backing_last_ack_tick,
                    virtual_first_page_ready_tick,
                    virtual_all_pages_ready_tick, virtual_pages_ready,
                    macro_backing_transport_bytes,
                    macro_backing_semantic_bytes,
                    macro_backing_line_issues, macro_backing_word_issues,
                    macro_backing_credit_stalls,
                    macro_backing_address_retries,
                    virtual_max_outstanding_writes,
                    macro_pipeline_cycles[0], macro_pipeline_cycles[1],
                    macro_pipeline_cycles[2], macro_pipeline_cycles[3],
                    macro_request_reason_cycles[0],
                    macro_request_reason_cycles[1],
                    macro_request_reason_cycles[2],
                    macro_request_reason_cycles[3],
                    macro_request_reason_cycles[4],
                    macro_request_reason_cycles[5]);
        }
        attribution_stage_tick = 0;
        attribution_stage_ticks.fill(0);
        return;
    }
    attribution_stage_tick = now;
    DPRINTF(MAAVirtualTrace,
            "event=indirect_stage_begin schema=2 unit=%d occurrence=%lu "
            "operation_tick=%lu stage=%s reason=%s\n",
            my_indirect_id, attribution_event_occurrence++,
            my_decode_start_tick,
            stage_names[static_cast<size_t>(next)], reason);
}

void IndirectAccessUnit::retirementWriteComplete(
    Addr addr, const uint8_t *writeRespPayload, unsigned payloadBytes,
    PacketPtr responsePacket) {
    if (isSoaJitRmw()) {
        auto *old_peek = dynamic_cast<SoaJitOldResultSenderState *>(
            responsePacket == nullptr ? nullptr :
                                        responsePacket->senderState);
        if (old_peek != nullptr) {
            panic_if(!isSoaJitOldResultRmw() ||
                         old_peek->physicalAddress != addr,
                     "I[%d] old-result WriteResp lost exact ownership at "
                     "0x%lx\n",
                     my_indirect_id, addr);
            auto *sender = dynamic_cast<SoaJitOldResultSenderState *>(
                responsePacket->popSenderState());
            panic_if(sender != old_peek,
                     "I[%d] old-result sender-state stack diverged\n",
                     my_indirect_id);
            const auto identity = sender->identity;
            delete sender;
            panic_if(!completeSoaJitOldResultWrite(identity),
                     "I[%d] rejected old-result WriteResp generation=%lu "
                     "sequence=%lu credit=%u vaddr=0x%lx\n",
                     my_indirect_id, identity.generation,
                     identity.issueSequence, identity.credit,
                     identity.lineAddress);
            scheduleNextExecution(true);
            return;
        }
        auto *peek = dynamic_cast<SoaJitWriteSenderState *>(
            responsePacket == nullptr ? nullptr :
                                        responsePacket->senderState);
        panic_if(peek == nullptr || peek->identity.address != addr,
                 "I[%d] SoA/JIT WriteResp lacks exact generation/context "
                 "ownership at 0x%lx\n",
                 my_indirect_id, addr);
        auto *sender = dynamic_cast<SoaJitWriteSenderState *>(
            responsePacket->popSenderState());
        panic_if(sender != peek,
                 "I[%d] SoA/JIT WriteResp sender-state stack diverged\n",
                 my_indirect_id);
        const auto identity = sender->identity;
        delete sender;
        panic_if(!completeSoaJitWrite(identity),
                 "I[%d] SoA/JIT rejected stale/unmatched WriteResp "
                 "generation=%lu context=%u addr=0x%lx active=%lu\n",
                 my_indirect_id, identity.generation, identity.context,
                 identity.address, soa_jit_generation);
        scheduleNextExecution(true);
        return;
    }
    auto global_write = std::find_if(
        bounded_global_merge_write_slots.begin(),
        bounded_global_merge_write_slots.end(),
        [addr](const auto &slot) {
            return slot.valid && slot.paddr == addr;
        });
    if (global_write != bounded_global_merge_write_slots.end()) {
        const Addr vaddr = global_write->vaddr;
        *global_write = BoundedGlobalMergeWriteSlot();
        const auto ack = bounded_global_merge.acknowledgeWrite(vaddr);
        panic_if(ack != BoundedFourRunMerge::Result::Accepted,
                 "I[%d] sorted-run write ack 0x%lx failed: %s\n",
                 my_indirect_id, addr,
                 BoundedFourRunMerge::resultName(ack));
        DPRINTF(MAAVirtualTrace,
                "event=bounded_global_sort_write_ack schema=1 unit=%d "
                "operation_tick=%lu vaddr=0x%lx paddr=0x%lx "
                "outstanding=%u\n",
                my_indirect_id, my_decode_start_tick, vaddr, addr,
                bounded_global_merge.outstandingWriteCount());
        scheduleNextExecution(true);
        return;
    }
    auto spool_write = std::find_if(
        descriptor_spool_write_slots.begin(),
        descriptor_spool_write_slots.end(),
        [addr](const auto &slot) {
            return slot.valid && slot.paddr == addr;
        });
    if (spool_write != descriptor_spool_write_slots.end()) {
        const Addr vaddr = spool_write->vaddr;
        *spool_write = DescriptorSpoolWriteSlot();
        const auto ack = descriptor_spool.acknowledgeWrite(vaddr);
        panic_if(ack != BoundedDescriptorSpool::Result::Accepted,
                 "I[%d] descriptor write ack 0x%lx failed: %s\n",
                 my_indirect_id, addr,
                 BoundedDescriptorSpool::resultName(ack));
        (*maa->stats.IND_DescriptorSpoolWriteAcks[my_indirect_id])++;
        DPRINTF(MAAVirtualTrace,
                "event=descriptor_spool_write_ack schema=1 unit=%d "
                "operation_tick=%lu vaddr=0x%lx paddr=0x%lx "
                "outstanding=%u\n",
                my_indirect_id, my_decode_start_tick, vaddr, addr,
                descriptor_spool.outstandingWriteCount());
        scheduleNextExecution(true);
        return;
    }
    accountVirtualRequestInterval();
    DPRINTF(MAAIndirect, "I[%d] %s: backing write 0x%lx completed\n",
            my_indirect_id, __func__, addr);
    my_received_responses++;
    panic_if(virtual_outstanding_writes == 0,
             "I[%d] %s: no outstanding retirement write!\n",
             my_indirect_id, __func__);
    virtual_outstanding_writes--;
    panic_if(virtual_outstanding_write_lines.erase(addr) != 1,
             "I[%d] %s: completed address 0x%lx was not outstanding\n",
             my_indirect_id, __func__, addr);
    completeVirtualRetirementWrite(addr, writeRespPayload, payloadBytes);
    (*maa->stats.IND_VirtWriteCompletions[my_indirect_id])++;
    attribution_write_completions++;
    macro_backing_last_ack_tick = curTick();
    DPRINTF(MAAVirtualTrace,
            "event=backing_write_complete schema=2 unit=%d occurrence=%lu "
            "operation_tick=%lu key=0x%lx outstanding=%d\n",
            my_indirect_id, attribution_event_occurrence++,
            my_decode_start_tick, addr,
            virtual_outstanding_writes);
    const bool response_throttled = drainVirtualResponses();
    if (virtual_final_flush ||
        (direct_index_partition_barrier &&
         !maa->virtual_partition_keep_combiner))
        drainVirtualCombiner(true);
    accountVirtualRequestInterval();
    if (response_throttled)
        scheduleExecuteInstructionEvent(1);
    else
        scheduleNextExecution(true);
}

Addr IndirectAccessUnit::translatePacket(Addr vaddr, BaseMMU::Mode mode,
                                         unsigned size) {
    /**** Address translation ****/
    RequestPtr translation_req = std::make_shared<Request>(
        vaddr, size, flags, maa->requestorId, my_instruction->PC,
        my_instruction->CID);
    ThreadContext *tc = maa->system->threads[my_instruction->CID];
    maa->mmu->translateTiming(translation_req, tc, this, mode);
    // The above function immediately does the translation and calls the finish function
    assert(my_translation_done);
    my_translation_done = false;
    return my_translated_addr;
}
void IndirectAccessUnit::finish(const Fault &fault, const RequestPtr &req, ThreadContext *tc, BaseMMU::Mode mode) {
    panic_if(fault != NoFault, "I[%d] %s: fault for request 0x%lx!\n", my_indirect_id, __func__, req->getVaddr());
    assert(my_translation_done == false);
    my_translation_done = true;
    my_translated_addr = req->getPaddr();
}
void IndirectAccessUnit::setInstruction(Instruction *_instruction) {
    assert(my_instruction == nullptr);
    my_instruction = _instruction;
}
void IndirectAccessUnit::scheduleExecuteInstructionEvent(int latency) {
    DPRINTF(MAAIndirect, "I[%d] %s: scheduling execute for the IndirectAccess Unit in the next %d cycles!\n", my_indirect_id, __func__, latency);
    panic_if(latency < 0, "Negative latency of %d!\n", latency);
    Tick new_when = maa->getClockEdge(Cycles(latency));
    if (!executeInstructionEvent.scheduled()) {
        maa->schedule(executeInstructionEvent, new_when);
    } else {
        Tick old_when = executeInstructionEvent.when();
        DPRINTF(MAAIndirect, "I[%d] %s: execution already scheduled for tick %d\n", my_indirect_id, __func__, old_when);
        if (new_when < old_when) {
            DPRINTF(MAAIndirect, "I[%d] %s: rescheduling for tick %d!\n", my_indirect_id, __func__, new_when);
            maa->reschedule(executeInstructionEvent, new_when);
        }
    }
}
} // namespace gem5
