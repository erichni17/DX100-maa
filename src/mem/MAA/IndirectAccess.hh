#ifndef __MEM_MAA_INDIRECT_ACCESS_HH__
#define __MEM_MAA_INDIRECT_ACCESS_HH__

#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include "arch/generic/mmu.hh"
#include "base/statistics.hh"
#include "base/types.hh"
#include "mem/MAA/BoundedRangePass.hh"
#include "mem/MAA/ReorderSurvivalTracker.hh"
#include "mem/MAA/Tables.hh"
#include "mem/packet.hh"
#include "mem/request.hh"
#include "sim/system.hh"

namespace gem5 {

class MAA;
class IndirectAccessUnit;
class Instruction;

class IndirectAccessUnit : public BaseMMU::Translation {
public:
    enum class Status : uint8_t {
        Idle = 0,
        Decode = 1,
        Fill = 2,
        Build = 3,
        Request = 4,
        Response = 5,
        max
    };

protected:
    std::string status_names[7] = {
        "Idle",
        "Decode",
        "Fill",
        "Build",
        "Request",
        "Response",
        "max"};
    int total_num_RT_subslices;
    int num_RT_configs;
    int my_RT_config;
    int initial_RT_config;
    int **RT_slice_org;
    int *num_RT_slices;
    int *num_RT_rows_total;
    Addr *num_RT_possible_grows;
    int *num_RT_subslices;
    int *num_RT_slice_columns;
    Addr *RT_config_addr;
    int *RT_config_cache;
    Tick *RT_config_cache_tick;
    int num_tile_elements;
    int num_RT_rows_per_slice;
    int num_RT_entries_per_subslice_row;
    int num_RT_config_cache_entries;
    int num_channels;
    int num_cores;
    bool reconfigure_RT;
    bool reorder_RT;
    int num_initial_RT_slices;
    Status state;
    RowTableSlice **RT;
    OffsetTable *offset_table;
    int dst_tile_id;
    Cycles rowtable_latency;
    std::map<Addr, Tick> LoadsCacheHitRespondingTimeHistory;
    std::map<Addr, Tick> LoadsCacheHitAccessingTimeHistory;
    std::map<Addr, Tick> LoadsMemAccessingTimeHistory;

    struct VirtualResponseSlot {
        bool valid = false;
        int next_itr = -1;
        std::array<uint8_t, 64> data{};
        std::vector<std::array<uint8_t, 8>> packed_words;
        size_t next_packed_word = 0;
        int reserved_words = 0;
        int claim_rt_idx = -1;
        int claim_row_id = -1;
        int claim_entry_id = -1;
        Addr claim_grow_addr = 0;
        Addr claim_addr = 0;
        int claim_head = -1;
    };
    std::vector<VirtualResponseSlot> virtual_response_slots;
    int virtual_response_words = 0;
    int virtual_response_word_pool_limit = 0;
    int virtual_words_per_cycle_limit = 0;
    Tick virtual_word_budget_tick = 0;
    int virtual_word_attempts_this_cycle = 0;
    int virtual_reserved_response_words = 0;
    bool virtual_pending_source = false;
    Addr virtual_pending_source_addr = 0;
    int virtual_pending_source_head = -1;
    int virtual_pending_source_words = 0;
    int virtual_pending_source_rt_idx = -1;
    int virtual_pending_source_row_id = -1;
    int virtual_pending_source_entry_id = -1;
    Addr virtual_pending_source_grow_addr = 0;
    struct VirtualSourceReservation
    {
        int head = -1;
        int words = 0;
        int rt_idx = -1;
        int row_id = -1;
        int entry_id = -1;
        Addr grow_addr = 0;
    };
    std::map<Addr, VirtualSourceReservation> virtual_source_reservations;
    struct VirtualCombineSlot {
        bool valid = false;
        Addr line_vaddr = 0;
        uint16_t valid_words = 0;
        std::array<uint8_t, 64> data{};
    };
    std::vector<VirtualCombineSlot> virtual_combine_slots;
    int virtual_combine_words_configured = 0;
    int virtual_combine_ways = 0;
    int virtual_combine_victim_policy = 0;
    int virtual_combine_banks = 0;
    std::vector<int> virtual_combine_set_victims;
    std::vector<bool> virtual_combine_bank_used;
    Tick virtual_combine_bank_tick = 0;
    Tick virtual_combine_bank_conflict_tick = 0;
    int virtual_combine_words_limit = 0;
    int virtual_combine_words = 0;
    int virtual_max_combine_words = 0;
    int virtual_max_outstanding_writes_limit = 0;
    bool virtual_masked_writes = false;
    std::set<Addr> virtual_outstanding_write_lines;
    std::map<Addr, std::vector<std::pair<int, int>>>
        virtual_retirement_write_pages;
    std::vector<int> virtual_page_logical_words;
    std::vector<int> virtual_page_scanned_words;
    std::vector<int> virtual_page_expected_words;
    std::vector<int> virtual_page_issued_words;
    std::vector<int> virtual_page_completed_words;
    std::vector<bool> virtual_page_ready;
    int virtual_pages_ready = 0;
    int virtual_pages_ready_before_source_drain = 0;
    Tick virtual_first_page_ready_tick = 0;
    Tick virtual_all_pages_ready_tick = 0;
    int virtual_reserved_responses = 0;
    int virtual_outstanding_writes = 0;
    int virtual_source_expected = 0;
    int virtual_source_received = 0;
    uint64_t virtual_trace_request_calls = 0;
    int virtual_combine_victim = 0;
    int virtual_full_line_writes = 0;
    int virtual_partial_word_writes = 0;
    int virtual_max_combine_occupancy = 0;
    bool virtual_final_flush = false;
    int virtual_max_reserved_responses = 0;
    int virtual_max_reserved_response_words = 0;
    int virtual_response_word_pool_stalls = 0;
    int virtual_max_outstanding_writes = 0;
    bool virtual_build_incomplete = false;
    int virtual_native_slice_cursor = 0;
    bool virtual_write_address_blocked = false;
    enum class VirtualRequestReason : uint8_t {
        None,
        Build,
        SourceFlight,
        Retained,
        Writes,
        FinalDrain,
        Runnable,
    };
    VirtualRequestReason virtual_request_reason = VirtualRequestReason::None;
    Tick virtual_request_reason_tick = 0;
    Tick virtual_request_attributed_ticks = 0;
    std::array<Tick, 6> virtual_request_reason_ticks{};
    uint8_t virtual_pipeline_state = 0;
    Tick virtual_pipeline_tick = 0;
    Tick virtual_pipeline_attributed_ticks = 0;
    std::array<Tick, 4> virtual_pipeline_ticks{};
    enum class AttributionStage : uint8_t
    {
        None,
        Decode,
        Fill,
        Build,
        Request,
        Response,
    };
    AttributionStage attribution_stage = AttributionStage::None;
    Tick attribution_stage_tick = 0;
    std::array<Tick, 5> attribution_stage_ticks{};
    uint64_t attribution_row_insert_attempts = 0;
    uint64_t attribution_row_insert_successes = 0;
    uint64_t attribution_offset_pressure_events = 0;
    uint64_t attribution_row_pressure_events = 0;
    uint64_t attribution_combiner_words = 0;
    uint64_t attribution_write_issues = 0;
    uint64_t attribution_write_completions = 0;
    uint64_t attribution_execute_sequence = 0;
    uint64_t attribution_event_occurrence = 0;
    ReorderSurvivalTracker reorder_survival;
    uint64_t reorder_instruction_sequence = 0;

public:
    MAA *maa;
    IndirectAccessUnit();
    ~IndirectAccessUnit();
    void allocate(int _my_indirect_id,
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
                  MAA *_maa);
    Status getState() const { return state; }
    bool scheduleNextExecution(bool force = false);
    void scheduleExecuteInstructionEvent(int latency = 0);
    void setInstruction(Instruction *_instruction);
    void memWritePacketSent(Addr addr);
    void memReadPacketSent(Addr addr);
    void cacheWritePacketSent(Addr addr);
    void cacheReadPacketSent(Addr addr);
    void retirementWriteComplete(Addr addr);
    bool hasPendingDirectIndexLine(Addr addr) const {
        return direct_index_pending_lines.find(addr) !=
               direct_index_pending_lines.end();
    }

    bool recvData(const Addr addr, uint8_t *dataptr, bool is_block_cached);

    /* Related to BaseMMU::Translation Inheretance */
    void markDelayed() override {}
    void finish(const Fault &fault, const RequestPtr &req, ThreadContext *tc, BaseMMU::Mode mode) override;

protected:
    Instruction *my_instruction;
    bool my_is_load;
    Request::Flags flags = 0;
    const Addr block_size = 64;
    int my_word_size = -1;
    int my_words_per_cl = -1;
    Addr my_virtual_addr = 0;
    Addr my_base_addr, my_backing_addr, my_min_addr, my_max_addr;
    Addr my_backing_min_addr, my_backing_max_addr;
    Addr my_index_addr, my_index_min_addr, my_index_max_addr;
    int8_t my_addr_range_id, my_backing_addr_range_id, my_index_addr_range_id;
    int my_index_min, my_index_stride;
    struct DirectIndexWord
    {
        uint32_t value = 0;
        Addr line_addr = 0;
        Addr word_paddr = 0;
    };
    enum class DirectIndexDiscardReason : uint8_t
    {
        DescriptorInserted,
        PredicateRejected,
        PartitionRejected,
    };
    int direct_index_buffer_lines = 1;
    bool direct_index_force_cache = false;
    int direct_index_partitions = 1;
    int direct_index_filter_words_per_cycle = 0;
    int direct_index_partition = 0;
    bool direct_index_partition_barrier = false;
    BoundedRangePassTracker bounded_range_pass;
    int direct_index_next_prefetch_itr = 0;
    std::map<Addr, std::vector<std::pair<int, uint16_t>>>
        direct_index_pending_lines;
    std::map<Addr, int> direct_index_ready_lines;
    std::map<int, DirectIndexWord> direct_index_words;
    int direct_index_max_lines = 0;
    int direct_index_max_words = 0;
    int my_dst_tile, my_src_tile, my_src_reg, my_cond_tile, my_max;
    int my_idx_tile;
    bool my_cond_tile_ready, my_idx_tile_ready, my_src_tile_ready;
    int my_expected_responses;
    int my_received_responses;
    uint64_t source_issue_sequence = 0;
    uint64_t source_issue_digest = 1469598103934665603ULL;
    uint64_t source_issue_digest_secondary = 0x9e3779b97f4a7c15ULL;
    std::vector<int> my_sorted_indices;
    bool **my_RT_req_sent;
    std::vector<int> *my_RT_slice_order;
    int my_i, my_RT_idx;
    bool my_fill_finished;
    bool offset_table_drain = false;
    bool my_force_cache_determined;
    bool my_force_cache;

    bool my_translation_done;
    Addr my_translated_addr;
    int my_indirect_id;
    Tick my_SPD_read_finish_tick;
    Tick my_SPD_write_finish_tick;
    Tick my_RT_read_access_finish_tick;
    Tick my_RT_write_access_finish_tick;
    Tick my_direct_index_filter_finish_tick;
    Tick my_direct_index_filter_accounted_tick;
    Tick my_decode_start_tick;
    Tick my_fill_start_tick;
    Tick my_build_start_tick;
    Tick my_request_start_tick;
    std::set<Addr> my_unique_WORD_addrs;
    std::set<Addr> my_unique_CL_addrs;
    std::set<Addr> my_unique_ROW_addrs;

    Addr translatePacket(Addr vaddr, BaseMMU::Mode mode = BaseMMU::Read,
                         unsigned size = 64);
    bool isVirtualLoad() const;
    bool isDirectIndexLoad() const;
    bool usesBoundedSourceResponses() const;
    void fillDirectIndexWindow();
    bool ensureDirectIndex(int itr);
    uint32_t peekDirectIndex(int itr) const;
    uint32_t directIndexPassForGrow(Addr grow_addr) const;
    BoundedRangePassTracker::Range directIndexSourceGrowRange();
    int directIndexRetirementPass() const;
    void finishBoundedRangePass(int pass, const char *reason);
    void discardDirectIndex(int itr, uint32_t expected_value,
                            DirectIndexDiscardReason reason);
    bool receiveDirectIndex(Addr addr, uint8_t *dataptr,
                            bool is_block_cached);
    void createDirectIndexReadPacket(Addr addr, int latency);
    void accountReadResponse(Addr addr, bool is_block_cached);
    Addr backingWordAddr(int itr) const;
    void validateRetirementWriteRange(Addr vaddr, unsigned size,
                                      uint16_t valid_words) const;
    void initializeVirtualPageTracking();
    void trackVirtualIteration(int itr, bool write_expected);
    void markVirtualPageReadyIfComplete(int page);
    void trackVirtualRetirementWrite(Addr write_key, Addr vaddr,
                                     unsigned size, uint16_t valid_words);
    void completeVirtualRetirementWrite(Addr write_key);
    bool createRetirementWrite(int itr, const uint8_t *data);
    bool createRetirementWrite(Addr vaddr, unsigned size, const uint8_t *data,
                               uint16_t valid_words = 0);
    bool drainVirtualResponses();
    bool reserveVirtualCombineBank(int itr);
    bool insertVirtualCombineWord(int itr, const uint8_t *data);
    void drainVirtualCombiner(bool flush_partial);
    bool virtualCombinerEmpty() const;
    bool boundedSourceResponsesComplete() const;
    bool boundedRetirementComplete() const;
    VirtualRequestReason classifyVirtualRequestReason() const;
    void accountVirtualRequestInterval();
    void startVirtualRequestInterval();
    void finishVirtualRequestInterval();
    void transitionAttributionStage(AttributionStage next,
                                    const char *reason);
    void recordReorderSurvivalIssue(Addr addr);
    void recordReorderSurvivalIssuedEntries(uint64_t entries);
    void recordReorderSurvivalDrain(
        ReorderSurvivalTracker::DrainReason reason);
    void closeReorderSurvivalEpoch(bool final);
    void finishReorderSurvival();
    bool checkAndResetAllRowTablesSent();
    int getRowTableIdx(int RT_config, int channel, int rank, int bankgroup, int bank);
    Addr getGrowAddr(int RT_config, int bankgroup, int bank, int row);
    int getRowTableConfig(Addr addr);
    void setRowTableConfig(Addr addr, int num_CLs, int num_ROWs);
    void checkTileReady();
    bool checkElementReady();
    bool checkReadyForFinish();
    void fillRowTable(bool &finished, bool &waitForFinish,
                      bool &waitForElement, bool &needDrain,
                      int &num_spd_read_condidx_accesses,
                      int &num_rowtable_accesses,
                      int &num_direct_index_filter_words);
    void chargeDirectIndexFilterLatency(int words);
    void executeInstruction();
    EventFunctionWrapper executeInstructionEvent;
    void check_reset();
    Cycles updateLatency(int num_spd_read_data_accesses,
                         int num_spd_read_condidx_accesses,
                         int num_spd_write_accesses,
                         int num_rowtable_read_accesses,
                         int num_rowtable_write_accesses,
                         int RT_access_parallelism);

public:
    void createReadPacket(Addr addr, int latency);
};
} // namespace gem5

#endif //__MEM_MAA_INDIRECT_ACCESS_HH__
