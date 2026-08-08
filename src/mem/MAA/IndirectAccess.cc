#include "mem/MAA/IndirectAccess.hh"
#include "mem/MAA/Tables.hh"
#include "base/logging.hh"
#include "mem/MAA/MAA.hh"
#include "mem/MAA/SPD.hh"
#include "mem/MAA/IF.hh"
#include "base/trace.hh"
#include "base/types.hh"
#include "debug/MAAIndirect.hh"
#include "debug/MAAIssueDigest.hh"
#include "debug/MAAIssueTrace.hh"
#include "debug/MAAPhysicalRecordTrace.hh"
#include "debug/MAATrace.hh"
#include "debug/MAAVirtualTrace.hh"
#include "mem/packet.hh"
#include "sim/cur_tick.hh"
#include <cassert>
#include <cstdint>
#include <string>

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
        assert(RT[i] != nullptr);
        delete[] RT[i];
    }
    delete[] RT;
    assert(offset_table != nullptr);
    delete offset_table;
    assert(my_RT_req_sent != nullptr);
    for (int i = 0; i < num_RT_configs; i++) {
        assert(my_RT_req_sent[i] != nullptr);
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
                                  int _virtual_index_buffer_lines,
                                  bool _virtual_index_force_cache,
                                  int _virtual_index_partitions,
                                  int _virtual_index_filter_words_per_cycle,
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
    virtual_response_slots.resize(_virtual_response_slots);
    virtual_response_words = _virtual_response_words;
    virtual_response_word_pool_limit = _virtual_response_word_pool;
    virtual_words_per_cycle_limit = _virtual_words_per_cycle;
    panic_if(_virtual_max_outstanding_writes <= 0,
             "I[%d] virtual retirement must allow at least one write\n",
             my_indirect_id);
    virtual_max_outstanding_writes_limit = _virtual_max_outstanding_writes;
    virtual_masked_writes = _virtual_masked_writes;
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
    direct_index_filter_words_per_cycle =
        _virtual_index_filter_words_per_cycle;
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

    RT = new RowTableSlice *[num_RT_configs];
    my_RT_req_sent = new bool *[num_RT_configs];
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
        RT[i] = new RowTableSlice[current_num_RT_slices];
        my_RT_req_sent[i] = new bool[current_num_RT_slices];
        num_RT_slices[i] = current_num_RT_slices;
        num_RT_rows_total[i] = current_num_RT_rows_total;
        num_RT_subslices[i] = current_num_RT_subslices;
        num_RT_slice_columns[i] = current_num_RT_entries_per_row;
        num_RT_possible_grows[i] = current_num_RT_possible_grows;
        if (reconfigure_RT == false && current_num_RT_slices == num_initial_RT_slices) {
            initial_RT_config = i;
        }
        panic_if(current_num_RT_entries_per_row <= 0, "I[%d] TC[%d] %s: current_num_RT_entries_per_row is %d!\n",
                 my_indirect_id, i, __func__, current_num_RT_entries_per_row);
        for (int j = 0; j < current_num_RT_slices; j++) {
            RT[i][j].allocate(my_indirect_id, j, num_RT_rows_per_slice, current_num_RT_entries_per_row, offset_table, maa, false);
            my_RT_req_sent[i][j] = false;
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
    if (reconfigure_RT)
        initial_RT_config = num_RT_configs - 1;
    else
        panic_if(initial_RT_config == -1,
                 "I[%d] unsupported initial Row-Table slice count %d\n",
                 my_indirect_id, num_initial_RT_slices);
    DPRINTF(MAAIndirect, "I[%d] %s: initial_RT_config(%d)!\n",
            my_indirect_id, __func__, initial_RT_config);
    if (maa->virtual_index_range_passes) {
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
void IndirectAccessUnit::check_reset() {
    for (int i = 0; i < num_RT_configs; i++) {
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
    panic_if(maa->allIndirectPacketsSent(my_indirect_id) == false, "All indirect packets are not sent!\n");
    panic_if(my_decode_start_tick != 0, "Decode start tick is not 0: %lu!\n", my_decode_start_tick);
    panic_if(my_fill_start_tick != 0, "Fill start tick is not 0: %lu!\n", my_fill_start_tick);
    panic_if(my_build_start_tick != 0, "Build start tick is not 0: %lu!\n", my_build_start_tick);
    panic_if(my_request_start_tick != 0, "Request start tick is not 0: %lu!\n", my_request_start_tick);
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
                 !direct_index_ready_lines.empty() ||
                 !direct_index_words.empty(),
             "I[%d] direct-index buffer is not empty at reset\n",
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
                Instruction::OpcodeType::INDIR_LD_INDEX);
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
    while (direct_index_pending_lines.size() +
               direct_index_ready_lines.size() <
           static_cast<size_t>(direct_index_buffer_lines)) {
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
bool IndirectAccessUnit::ensureDirectIndex(int itr) {
    if (!isDirectIndexLoad())
        return true;
    fillDirectIndexWindow();
    return direct_index_words.find(itr) != direct_index_words.end();
}
uint32_t IndirectAccessUnit::peekDirectIndex(int itr) const {
    auto entry = direct_index_words.find(itr);
    panic_if(entry == direct_index_words.end(),
             "I[%d] streamed index %d is not buffered\n",
             my_indirect_id, itr);
    return entry->second.value;
}
uint32_t IndirectAccessUnit::directIndexPassForGrow(Addr grow_addr) const {
    if (!maa->virtual_index_range_passes)
        return grow_addr % direct_index_partitions;
    const uint32_t pass = bounded_range_pass.passForGrow(grow_addr);
    panic_if(pass >= static_cast<uint32_t>(direct_index_partitions),
             "I[%d] grow 0x%lx has no bounded range pass\n",
             my_indirect_id, grow_addr);
    return pass;
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
    if (!maa->virtual_index_range_passes)
        return;
    const auto result = bounded_range_pass.finishPass(pass);
    panic_if(result != BoundedRangePassTracker::Result::Accepted,
             "I[%d] bounded range pass %d failed closure: %s\n",
             my_indirect_id, pass,
             BoundedRangePassTracker::resultName(result));
    const auto range = bounded_range_pass.range(pass);
    DPRINTF(MAAVirtualTrace,
            "event=bounded_range_pass_complete schema=1 unit=%d "
            "operation_tick=%lu pass=%d passes=%d lower=0x%lx upper=0x%lx "
            "admitted=%u retired=%u admitted_total=%u retired_total=%u "
            "reason=%s\n",
            my_indirect_id, my_decode_start_tick, pass,
            direct_index_partitions, range.lower, range.upper,
            bounded_range_pass.admissionsForPass(pass),
            bounded_range_pass.retirementsForPass(pass),
            bounded_range_pass.admissions(), bounded_range_pass.retirements(),
            reason);
}
void IndirectAccessUnit::discardDirectIndex(
    int itr, uint32_t expected_value, DirectIndexDiscardReason reason) {
    auto word = direct_index_words.find(itr);
    panic_if(word == direct_index_words.end(),
             "I[%d] streamed index %d cannot be consumed\n",
             my_indirect_id, itr);
    panic_if(word->second.value != expected_value,
             "I[%d] streamed index %d changed from %u to %u before discard\n",
             my_indirect_id, itr, expected_value, word->second.value);
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
                                        addr + wid * sizeof(uint32_t)})
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
    bool cond_ready = my_cond_tile == -1 || maa->spd->getElementFinished(my_cond_tile, my_i, 4, (uint8_t)FuncUnitType::INDIRECT, my_indirect_id);
    bool idx_ready = cond_ready &&
        (isDirectIndexLoad()
             ? ensureDirectIndex(my_i)
             : maa->spd->getElementFinished(
                   my_idx_tile, my_i, 4,
                   (uint8_t)FuncUnitType::INDIRECT, my_indirect_id));
    bool src_ready = idx_ready &&
        (my_instruction->opcode == Instruction::OpcodeType::INDIR_LD ||
         my_instruction->opcode ==
             Instruction::OpcodeType::INDIR_LD_INDEX ||
         isVirtualLoad() ||
         my_instruction->opcode ==
             Instruction::OpcodeType::INDIR_RMW_SCALAR ||
         my_instruction->opcode ==
             Instruction::OpcodeType::INDIR_ST_SCALAR ||
         maa->spd->getElementFinished(
             my_src_tile, my_i, my_word_size,
             (uint8_t)FuncUnitType::INDIRECT, my_indirect_id));
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
        if (my_max != -1 && my_i >= my_max) {
            if (isVirtualLoad() && isDirectIndexLoad() &&
                direct_index_partition + 1 < direct_index_partitions) {
                panic_if(!direct_index_pending_lines.empty() ||
                             !direct_index_ready_lines.empty() ||
                             !direct_index_words.empty(),
                         "I[%d] direct-index partition %d ended with buffered "
                         "index data\n",
                         my_indirect_id, direct_index_partition);
                const int completed_partition = direct_index_partition++;
                my_i = 0;
                direct_index_next_prefetch_itr = 0;
                direct_index_partition_barrier = true;
                needDrain = true;
                DPRINTF(MAAVirtualTrace,
                        "event=index_partition unit=%d completed=%d next=%d "
                        "total=%d policy=%s\n", my_indirect_id,
                        completed_partition, direct_index_partition,
                        direct_index_partitions,
                        maa->virtual_index_range_passes ? "grow_range" :
                                                         "grow_modulo");
                break;
            }
            if (my_dst_tile != -1) {
                panic_if(my_max != -1 && my_i != my_max, "I[%d] %s: my_i(%d) != my_max(%d)!\n", my_indirect_id, __func__, my_i, my_max);
                if (isVirtualLoad())
                    maa->spd->setVirtualSize(my_dst_tile, my_i);
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
        const bool condition_taken =
            my_cond_tile == -1 ||
            maa->spd->getData<uint32_t>(my_cond_tile, my_i) != 0;
        bool direct_index_descriptor_inserted = false;
        bool direct_index_predicate_rejected = false;
        bool direct_index_partition_rejected = false;
        const uint32_t direct_index_value = isDirectIndexLoad()
            ? peekDirectIndex(my_i)
            : 0;
        if (isDirectIndexLoad() && !condition_taken)
            direct_index_predicate_rejected = true;
        if (isVirtualLoad() && isDirectIndexLoad() &&
            direct_index_partitions > 1)
            num_direct_index_filter_words++;
        bool virtual_iteration_selected = condition_taken;
        if (condition_taken) {
            uint32_t idx = isDirectIndexLoad()
                ? direct_index_value
                : maa->spd->getData<uint32_t>(my_idx_tile, my_i);
            if (!isDirectIndexLoad())
                num_spd_read_condidx_accesses++;
            Addr vaddr = my_base_addr + my_word_size * idx;
            panic_if(vaddr < my_min_addr || vaddr >= my_max_addr, "I[%d] %s: vaddr 0x%lx out of range [0x%lx, 0x%lx)!\n", my_indirect_id, __func__, vaddr, my_min_addr, my_max_addr);
            Addr block_vaddr = addrBlockAligner(vaddr, block_size);
            DPRINTF(MAAIndirect, "I[%d] %s: baseaddr = 0x%lx idx = %u wordsize = %d vaddr = 0x%lx!\n", my_indirect_id, __func__, my_base_addr, idx, my_word_size, vaddr);
            Addr paddr = translatePacket(block_vaddr);
            Addr block_paddr = addrBlockAligner(paddr, block_size);
            DPRINTF(MAAIndirect, "I[%d] %s: idx = %u, addr = 0x%lx!\n", my_indirect_id, __func__, idx, block_paddr);
            uint16_t wid = (vaddr - block_vaddr) / my_word_size;
            std::vector<int> addr_vec = maa->map_addr(block_paddr);
            my_RT_idx = getRowTableIdx(my_RT_config, addr_vec[ADDR_CHANNEL_LEVEL], addr_vec[ADDR_RANK_LEVEL], addr_vec[ADDR_BANKGROUP_LEVEL], addr_vec[ADDR_BANK_LEVEL]);
            Addr grow_addr = getGrowAddr(my_RT_config, addr_vec[ADDR_BANKGROUP_LEVEL], addr_vec[ADDR_BANK_LEVEL], addr_vec[ADDR_ROW_LEVEL]);
            virtual_iteration_selected =
                !isVirtualLoad() || !isDirectIndexLoad() ||
                direct_index_partitions == 1 ||
                static_cast<int>(directIndexPassForGrow(grow_addr)) ==
                    direct_index_partition;
            if (isDirectIndexLoad() && !virtual_iteration_selected)
                direct_index_partition_rejected = true;
            if (virtual_iteration_selected) {
                if (offset_table->occupancy() >=
                    maa->num_offset_table_epoch_entries) {
                    attribution_offset_pressure_events++;
                    DPRINTF(MAAVirtualTrace,
                            "event=indirect_stall schema=2 unit=%d "
                            "occurrence=%lu operation_tick=%lu sequence=%lu "
                            "reason=offset_epoch_full itr=%d "
                            "occupancy=%d limit=%d\n",
                            my_indirect_id, attribution_event_occurrence++,
                            my_decode_start_tick,
                            attribution_execute_sequence - 1, my_i,
                            offset_table->occupancy(),
                            maa->num_offset_table_epoch_entries);
                    offset_table_drain = true;
                    needDrain = true;
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
                        grow_addr, my_i, idx, wid, my_RT_idx);
                bool first_CL_access;
                attribution_row_insert_attempts++;
                bool inserted = RT[my_RT_config][my_RT_idx].insert(
                    grow_addr, block_paddr, my_i, wid, first_CL_access);
                num_rowtable_accesses++;
                if (!inserted) {
                    attribution_row_pressure_events++;
                    DPRINTF(MAAVirtualTrace,
                            "event=indirect_stall schema=2 unit=%d "
                            "occurrence=%lu operation_tick=%lu sequence=%lu "
                            "reason=row_table_full itr=%d "
                            "slice=%d grow=0x%lx\n",
                            my_indirect_id, attribution_event_occurrence++,
                            my_decode_start_tick,
                            attribution_execute_sequence - 1, my_i,
                            my_RT_idx, grow_addr);
                    needDrain = true;
                    (*maa->stats.IND_NumRTFull[my_indirect_id])++;
                    break;
                } else {
                    attribution_row_insert_successes++;
                    if (maa->virtual_index_range_passes) {
                        const auto result = bounded_range_pass.recordAdmission(
                            my_i, grow_addr, direct_index_partition);
                        panic_if(
                            result != BoundedRangePassTracker::Result::Accepted,
                            "I[%d] bounded range admission itr=%d grow=0x%lx "
                            "pass=%d failed: %s\n", my_indirect_id, my_i,
                            grow_addr, direct_index_partition,
                            BoundedRangePassTracker::resultName(result));
                    }
                    if (isDirectIndexLoad())
                        direct_index_descriptor_inserted = true;
                    if (isDirectIndexLoad()) {
                        const auto direct_word =
                            direct_index_words.find(my_i);
                        panic_if(direct_word == direct_index_words.end(),
                                 "I[%d] admitted index %d has no physical "
                                 "record\n", my_indirect_id, my_i);
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
                                my_i, direct_word->second.word_paddr, idx,
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
                    my_unique_WORD_addrs.insert(vaddr);
                    my_unique_CL_addrs.insert(block_paddr);
                    my_unique_ROW_addrs.insert(
                        grow_addr +
                        my_RT_idx * num_RT_possible_grows[my_RT_config]);
                    if (!reorder_RT && first_CL_access) {
                        DPRINTF(MAAIndirect,
                                "I[%d] %s: Creating packet for bank[%d], "
                                "addr[0x%lx]!\n",
                                my_indirect_id, __func__, my_RT_idx,
                                block_paddr);
                        my_expected_responses++;
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
                    my_indirect_id, __func__, my_dst_tile, my_i, 0);
            maa->spd->setFakeData(my_dst_tile, my_i, my_word_size);
        }
        // False predicates have no source address to partition.
        // Count them once in partition zero; selected true iterations
        // remain exact-once.
        const bool track_virtual_iteration =
            virtual_iteration_selected ||
            (!condition_taken &&
             (!isDirectIndexLoad() || direct_index_partition == 0));
        if (isVirtualLoad() && track_virtual_iteration)
            trackVirtualIteration(my_i, condition_taken);
        if (isDirectIndexLoad()) {
            const int terminal_decisions =
                static_cast<int>(direct_index_descriptor_inserted) +
                static_cast<int>(direct_index_predicate_rejected) +
                static_cast<int>(direct_index_partition_rejected);
            panic_if(terminal_decisions != 1,
                     "I[%d] direct index %d must be discarded after exactly "
                     "one terminal fill decision (inserted=%d predicate=%d "
                     "partition=%d)\n",
                     my_indirect_id, my_i,
                     direct_index_descriptor_inserted,
                     direct_index_predicate_rejected,
                     direct_index_partition_rejected);
            discardDirectIndex(
                my_i, direct_index_value,
                direct_index_descriptor_inserted
                    ? DirectIndexDiscardReason::DescriptorInserted
                    : direct_index_predicate_rejected
                        ? DirectIndexDiscardReason::PredicateRejected
                        : DirectIndexDiscardReason::PartitionRejected);
        }
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
        attribution_execute_sequence = 1;
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
        } else if (my_instruction->opcode == Instruction::OpcodeType::INDIR_ST_VECTOR ||
                   my_instruction->opcode == Instruction::OpcodeType::INDIR_RMW_VECTOR) {
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
            (my_instruction->opcode == Instruction::OpcodeType::INDIR_LD ||
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
        virtual_retirement_write_forwardable.clear();
        virtual_page_logical_words.clear();
        virtual_page_scanned_words.clear();
        virtual_page_expected_words.clear();
        virtual_page_issued_words.clear();
        virtual_page_completed_words.clear();
        virtual_page_unforwardable_writes.clear();
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
        for (auto &slot : virtual_combine_slots)
            slot = VirtualCombineSlot();
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
        direct_index_partition_barrier = false;
        bounded_range_pass.reset();
        offset_table_drain = false;
        direct_index_pending_lines.clear();
        direct_index_ready_lines.clear();
        direct_index_words.clear();
        direct_index_max_lines = 0;
        direct_index_max_words = 0;
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
            my_idx_tile_ready = true;
            if (maa->virtual_index_range_passes) {
                panic_if(!isVirtualLoad(),
                         "I[%d] bounded range passes require a virtual load\n",
                         my_indirect_id);
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
        const bool native_order_claim =
            usesBoundedSourceResponses() && maa->virtual_native_issue_order;
        int last_RT_sent = native_order_claim
            ? virtual_native_slice_cursor
            : 0;
        int num_rowtable_accesses = 0;
        Addr addr;
        if (my_force_cache_determined == false) {
            my_force_cache_determined = true;
            if (my_unique_WORD_addrs.size() > my_words_per_cl * my_unique_CL_addrs.size()) {
                DPRINTF(MAAIndirect, "I[%d] %s: Direct cache access is needed!\n", my_indirect_id, __func__);
                my_force_cache = true;
            } else {
                DPRINTF(MAAIndirect, "I[%d] %s: Direct cache access is not needed!\n", my_indirect_id, __func__);
                my_force_cache = false;
            }
        }
        auto issueVirtualSource = [&](Addr source_addr, int source_head,
                                      int source_words, int source_rt_idx,
                                      int source_row_id, int source_entry_id,
                                      Addr source_grow_addr, int latency) {
            panic_if(source_head < 0 || source_words <= 0,
                     "I[%d] virtual source claim is empty\n",
                     my_indirect_id);
            if (virtual_response_words != 0 &&
                virtual_response_word_pool_limit == 0)
                panic_if(source_words > virtual_response_words,
                         "I[%d] source response needs %d/%d packed words\n",
                         my_indirect_id, source_words,
                         virtual_response_words);
            if (virtual_response_word_pool_limit != 0)
                panic_if(source_words > virtual_response_word_pool_limit,
                         "I[%d] source response needs %d/%d pooled words\n",
                         my_indirect_id, source_words,
                         virtual_response_word_pool_limit);
            panic_if(virtual_reserved_responses ==
                         virtual_response_slots.size(),
                     "I[%d] cannot issue virtual source without a slot\n",
                     my_indirect_id);
            panic_if(virtual_response_word_pool_limit != 0 &&
                         virtual_reserved_response_words + source_words >
                             virtual_response_word_pool_limit,
                     "I[%d] cannot issue virtual source without pooled "
                     "words\n",
                     my_indirect_id);

            if (virtual_response_word_pool_limit != 0)
                virtual_reserved_response_words += source_words;
            panic_if(!virtual_source_reservations
                          .emplace(source_addr,
                                   VirtualSourceReservation{source_head,
                                                            source_words,
                                                            source_rt_idx,
                                                            source_row_id,
                                                            source_entry_id,
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
            panic_if(virtual_reserved_responses >
                         virtual_response_slots.size(),
                     "I[%d] virtual response slots exceeded capacity\n",
                     my_indirect_id);
            createReadPacket(source_addr, latency);
        };

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
        const bool retain_partition_combiner =
            isVirtualLoad() && direct_index_partition_barrier &&
            maa->virtual_partition_keep_combiner;
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
            } else if (direct_index_partition_barrier) {
                finishBoundedRangePass(direct_index_partition - 1,
                                       "barrier_drained");
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
            refill_allowed) {
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
            panic_if(!virtual_retirement_write_forwardable.empty(),
                     "I[%d] virtual forwarding metadata remains at response\n",
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
            if (maa->virtual_index_range_passes) {
                finishBoundedRangePass(direct_index_partition,
                                       "final_drained");
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
            }
        }
        if (isDirectIndexLoad()) {
            (*maa->stats.IND_VirtIndexLineHighWater[my_indirect_id]) +=
                direct_index_max_lines;
            (*maa->stats.IND_VirtIndexWordHighWater[my_indirect_id]) +=
                direct_index_max_words;
        }
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
        setRowTableConfig(my_base_addr, my_unique_CL_addrs.size(), my_unique_ROW_addrs.size());
        (*maa->stats.IND_NumUniqueWordsInserted[my_indirect_id]) += my_unique_WORD_addrs.size();
        (*maa->stats.IND_NumUniqueCacheLineInserted[my_indirect_id]) += my_unique_CL_addrs.size();
        (*maa->stats.IND_NumUniqueRowsInserted[my_indirect_id]) += my_unique_ROW_addrs.size();
        my_unique_WORD_addrs.clear();
        my_unique_CL_addrs.clear();
        my_unique_ROW_addrs.clear();
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
    maa->sendPacket(FuncUnitType::INDIRECT, my_indirect_id, read_pkt, maa->getClockEdge(Cycles(latency)), my_force_cache);
    DPRINTF(MAAIndirect, "I[%d] %s: created %s for mem\n", my_indirect_id, __func__, read_pkt->print());
}
void IndirectAccessUnit::createDirectIndexReadPacket(Addr addr, int latency) {
    RequestPtr real_req = std::make_shared<Request>(
        addr, block_size, flags, maa->requestorId);
    real_req->setRegion(my_index_addr_range_id);
    PacketPtr read_pkt = new Packet(real_req, MemCmd::ReadReq);
    read_pkt->headerDelay = read_pkt->payloadDelay = 0;
    read_pkt->allocate();
    maa->sendPacket(FuncUnitType::INDIRECT, my_indirect_id, read_pkt,
                    maa->getClockEdge(Cycles(latency)),
                    direct_index_force_cache);
    (*maa->stats.IND_VirtIndexLineReads[my_indirect_id])++;
    DPRINTF(MAAIndirect,
            "I[%d] %s: created direct-index read %s\n",
            my_indirect_id, __func__, read_pkt->print());
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
bool IndirectAccessUnit::recvData(const Addr addr, uint8_t *dataptr, bool is_block_cached) {
    if (receiveDirectIndex(addr, dataptr, is_block_cached))
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
        slot->valid = true;
        slot->next_itr = virtual_head;
        slot->claim_rt_idx = virtual_claim_rt_idx;
        slot->claim_row_id = virtual_claim_row_id;
        slot->claim_entry_id = virtual_claim_entry_id;
        slot->claim_grow_addr = virtual_claim_grow_addr;
        slot->claim_addr = addr;
        slot->claim_head = virtual_head;
        if (virtual_response_word_pool_limit != 0) {
            panic_if(virtual_reserved_words <= 0,
                     "I[%d] response 0x%lx has no packed-word reservation\n",
                     my_indirect_id, addr);
            slot->reserved_words = virtual_reserved_words;
        }
        const bool packed_response = virtual_response_words != 0 ||
                                     virtual_response_word_pool_limit != 0;
        if (!packed_response) {
            std::memcpy(slot->data.data(), dataptr, block_size);
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
    virtual_page_unforwardable_writes.assign(pages, 0);
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
    markVirtualPageReadyIfEligible(page);
}

void IndirectAccessUnit::markVirtualPageReadyIfEligible(int page) {
    panic_if(page < 0 ||
                 page >= static_cast<int>(virtual_page_logical_words.size()),
             "I[%d] invalid virtual page %d\n", my_indirect_id, page);
    if (virtual_page_ready[page] ||
        virtual_page_scanned_words[page] != virtual_page_logical_words[page] ||
        virtual_page_issued_words[page] != virtual_page_expected_words[page] ||
        (!maa->virtual_page_ready_on_issue &&
         virtual_page_completed_words[page] !=
             virtual_page_expected_words[page]) ||
        (maa->virtual_page_ready_on_issue &&
         virtual_page_unforwardable_writes[page] != 0))
        return;

    const int pending_words = virtual_page_issued_words[page] -
        virtual_page_completed_words[page];
    panic_if(pending_words < 0,
             "I[%d] virtual page %d completed more words than issued\n",
             my_indirect_id, page);
    virtual_page_ready[page] = true;
    virtual_pages_ready++;
    if (pending_words != 0) {
        (*maa->stats
               .IND_VirtPagesReadyWithPendingWrites[my_indirect_id])++;
        (*maa->stats.IND_VirtPageReadyPendingWords[my_indirect_id]) +=
            pending_words;
    }
    maa->setVirtualPageReady(my_dst_tile, page);
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
    metadata.assign(page_words.begin(), page_words.end());
    const bool forwardable = size == block_size && valid_words == 0;
    const auto inserted =
        virtual_retirement_write_forwardable.emplace(write_key, forwardable);
    panic_if(!inserted.second,
             "I[%d] duplicate forwarding metadata for 0x%lx\n",
             my_indirect_id, write_key);
    if (!forwardable) {
        for (const auto &entry : metadata)
            ++virtual_page_unforwardable_writes[entry.first];
    }
}

void IndirectAccessUnit::completeVirtualRetirementWrite(Addr write_key) {
    auto metadata = virtual_retirement_write_pages.find(write_key);
    panic_if(metadata == virtual_retirement_write_pages.end(),
             "I[%d] completed virtual write 0x%lx has no page metadata\n",
             my_indirect_id, write_key);
    const auto forwarding =
        virtual_retirement_write_forwardable.find(write_key);
    panic_if(forwarding == virtual_retirement_write_forwardable.end(),
             "I[%d] completed virtual write 0x%lx has no forwarding "
             "metadata\n",
             my_indirect_id, write_key);
    for (const auto &[page, words] : metadata->second) {
        virtual_page_completed_words[page] += words;
        panic_if(virtual_page_completed_words[page] >
                     virtual_page_expected_words[page],
                 "I[%d] virtual page %d completed too many words: %d/%d\n",
                 my_indirect_id, page, virtual_page_completed_words[page],
                 virtual_page_expected_words[page]);
        if (!forwarding->second) {
            panic_if(virtual_page_unforwardable_writes[page] == 0,
                     "I[%d] virtual page %d lost an unforwardable write\n",
                     my_indirect_id, page);
            --virtual_page_unforwardable_writes[page];
        }
        markVirtualPageReadyIfEligible(page);
    }
    virtual_retirement_write_forwardable.erase(forwarding);
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
    if (virtual_outstanding_write_lines.count(write_key) != 0)
        return false;
    if (maa->hasOutstandingPacket(paddr)) {
        (*maa->stats.IND_VirtWriteAddressConflicts[my_indirect_id])++;
        virtual_write_address_blocked = true;
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
    (*maa->stats.IND_VirtWriteIssues[my_indirect_id])++;
    attribution_write_issues++;
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
    const auto tracked = virtual_retirement_write_pages.find(write_key);
    panic_if(tracked == virtual_retirement_write_pages.end(),
             "I[%d] issued virtual write 0x%lx lost page metadata\n",
             my_indirect_id, write_key);
    // The packet is now owned by MAA's exact-address retirement map.  A
    // consumer fill released here is either deferred behind that write or
    // observes its completed cache value; it can never bypass unregistered
    // producer data.
    for (const auto &entry : tracked->second)
        markVirtualPageReadyIfEligible(entry.first);
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
    for (auto &slot : virtual_response_slots) {
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
                slot.data.data() + entry.wid * my_word_size;
            if (virtual_load) {
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
    const Addr vaddr = backingWordAddr(itr);
    const Addr line_vaddr = vaddr & ~(block_size - 1);
    const unsigned word = (vaddr - line_vaddr) / my_word_size;
    const uint16_t word_bit = 1U << word;
    const uint16_t full_mask = (1U << my_words_per_cl) - 1;
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
    if (virtual_combine_words == virtual_combine_words_limit)
        drainVirtualCombiner(false);
    const bool word_capacity_full =
        virtual_combine_words == virtual_combine_words_limit;
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
            const bool candidate_fragment =
                candidate.valid_words != full_mask;
            const bool victim_fragment =
                victim_idx != -1 &&
                virtual_combine_slots[victim_idx].valid_words != full_mask;
            if (victim_idx == -1 ||
                (maa->virtual_page_ready_on_issue && candidate_fragment &&
                 !victim_fragment) ||
                ((!maa->virtual_page_ready_on_issue ||
                  candidate_fragment == victim_fragment) &&
                 ((virtual_combine_victim_policy == 1 &&
                   candidate_words < victim_words) ||
                  (virtual_combine_victim_policy == 2 &&
                   candidate_words > victim_words)))) {
                victim_idx = idx;
                victim_words = candidate_words;
                if (virtual_combine_victim_policy == 0 &&
                    (!maa->virtual_page_ready_on_issue ||
                     candidate_fragment)) {
                    break;
                }
            }
        }
        if (victim_idx == -1 && target != nullptr)
            victim_idx = target - virtual_combine_slots.data();
        panic_if(victim_idx == -1,
                 "I[%d] virtual combiner has no valid victim\n", my_indirect_id);
        auto &victim = virtual_combine_slots[victim_idx];
        if (maa->virtual_page_ready_on_issue &&
            victim.valid_words == full_mask &&
            virtual_outstanding_writes <
                virtual_max_outstanding_writes_limit) {
            if (createRetirementWrite(victim.line_vaddr, block_size,
                                      victim.data.data())) {
                virtual_combine_words -= my_words_per_cl;
                virtual_full_line_writes++;
                victim.valid_words = 0;
            }
        } else if (
            virtual_masked_writes && victim.valid_words != 0 &&
            virtual_outstanding_writes <
                virtual_max_outstanding_writes_limit) {
            const int words = __builtin_popcount(victim.valid_words);
            if (createRetirementWrite(victim.line_vaddr, block_size,
                                      victim.data.data(), victim.valid_words)) {
                virtual_combine_words -= words;
                virtual_partial_word_writes++;
                victim.valid_words = 0;
            }
        } else {
            while (victim.valid_words != 0 &&
                   virtual_outstanding_writes < virtual_max_outstanding_writes_limit) {
                unsigned victim_word = __builtin_ctz(victim.valid_words);
                if (!createRetirementWrite(
                        victim.line_vaddr + victim_word * my_word_size,
                        my_word_size,
                        victim.data.data() + victim_word * my_word_size))
                    break;
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
    if (!target->valid) {
        target->valid = true;
        target->line_vaddr = line_vaddr;
        const int occupancy = std::count_if(
            virtual_combine_slots.begin(), virtual_combine_slots.end(),
            [](const VirtualCombineSlot &slot) { return slot.valid; });
        virtual_max_combine_occupancy =
            std::max(virtual_max_combine_occupancy, occupancy);
    }
    panic_if(target->valid_words & word_bit,
             "I[%d] duplicate virtual output word %d at 0x%lx\n",
             my_indirect_id, word, line_vaddr);
    std::memcpy(target->data.data() + word * my_word_size, data, my_word_size);
    target->valid_words |= word_bit;
    virtual_combine_words++;
    attribution_combiner_words++;
    if (maa->virtual_index_range_passes) {
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
    virtual_max_combine_words =
        std::max(virtual_max_combine_words, virtual_combine_words);
    return true;
}

void IndirectAccessUnit::drainVirtualCombiner(bool flush_partial) {
    const uint16_t full_mask = (1U << my_words_per_cl) - 1;
    constexpr int issue_ready_reserve_lines = 4;
    if (flush_partial && maa->virtual_page_ready_on_issue) {
        // Retire non-forwardable fragments first. Once acknowledged, later
        // full-line writes can remain in the bounded retirement map and feed
        // an issue-ready STREAM consumer. This changes only cross-address
        // scheduling; the exact-address map still preserves ordering.
        for (auto &slot : virtual_combine_slots) {
            if (!slot.valid || slot.valid_words == full_mask)
                continue;
            if (virtual_outstanding_writes ==
                virtual_max_outstanding_writes_limit) {
                break;
            }
            if (virtual_masked_writes) {
                const int words = __builtin_popcount(slot.valid_words);
                if (createRetirementWrite(slot.line_vaddr, block_size,
                                          slot.data.data(),
                                          slot.valid_words)) {
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
                if (!createRetirementWrite(
                        slot.line_vaddr + word * my_word_size, my_word_size,
                        slot.data.data() + word * my_word_size)) {
                    break;
                }
                slot.valid_words &= ~(1U << word);
                panic_if(virtual_combine_words == 0,
                         "I[%d] virtual word accounting underflow\n",
                         my_indirect_id);
                virtual_combine_words--;
                virtual_partial_word_writes++;
            }
            if (slot.valid_words == 0)
                slot = VirtualCombineSlot();
        }
    }
    int full_lines_to_drain = std::count_if(
        virtual_combine_slots.begin(), virtual_combine_slots.end(),
        [full_mask](const VirtualCombineSlot &slot) {
            return slot.valid && slot.valid_words == full_mask;
        });
    if (!flush_partial && maa->virtual_page_ready_on_issue) {
        // Keep a finite tail of complete lines in the already-accounted
        // combiner. The four-line reserve is below its configured 384-line and
        // 4096-word limits, so admission can continue while older lines drain.
        full_lines_to_drain =
            std::max(0, full_lines_to_drain - issue_ready_reserve_lines);
    }
    for (auto &slot : virtual_combine_slots) {
        if (!slot.valid)
            continue;
        if (slot.valid_words == full_mask &&
            virtual_outstanding_writes < virtual_max_outstanding_writes_limit) {
            if (full_lines_to_drain == 0)
                continue;
            if (!createRetirementWrite(slot.line_vaddr, block_size,
                                       slot.data.data()))
                continue;
            --full_lines_to_drain;
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
            virtual_outstanding_writes < virtual_max_outstanding_writes_limit) {
            const int words = __builtin_popcount(slot.valid_words);
            if (createRetirementWrite(slot.line_vaddr, block_size,
                                      slot.data.data(), slot.valid_words)) {
                virtual_combine_words -= words;
                virtual_partial_word_writes++;
                slot = VirtualCombineSlot();
            }
            continue;
        }
        while (slot.valid_words != 0 &&
               virtual_outstanding_writes < virtual_max_outstanding_writes_limit) {
            unsigned word = __builtin_ctz(slot.valid_words);
            if (!createRetirementWrite(
                    slot.line_vaddr + word * my_word_size, my_word_size,
                    slot.data.data() + word * my_word_size))
                break;
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
}

bool IndirectAccessUnit::virtualCombinerEmpty() const {
    return std::all_of(virtual_combine_slots.begin(), virtual_combine_slots.end(),
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
    Cycles non_source_cycles(0);
    for (size_t i = 0; i < buckets.size(); ++i) {
        if (i == 1)
            continue;
        Cycles cycles = maa->getTicksToCycles(virtual_request_reason_ticks[i]);
        (*buckets[i]) += cycles;
        non_source_cycles += cycles;
    }
    panic_if(non_source_cycles > request_cycles,
             "I[%d] non-source virtual request buckets exceed total cycles\n",
             my_indirect_id);
    // Assign the residual to the dominant source-flight bucket so integer cycle
    // rounding cannot make the mutually exclusive buckets exceed the total.
    (*buckets[1]) += request_cycles - non_source_cycles;
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
    for (size_t i = 0; i < pipeline_buckets.size(); ++i)
        (*pipeline_buckets[i]) += Cycles(pipeline_cycles[i]);
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

void IndirectAccessUnit::retirementWriteComplete(Addr addr) {
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
    completeVirtualRetirementWrite(addr);
    (*maa->stats.IND_VirtWriteCompletions[my_indirect_id])++;
    attribution_write_completions++;
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
