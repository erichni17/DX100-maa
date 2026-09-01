#ifndef __MEM_MAA_INDIRECT_ACCESS_HH__
#define __MEM_MAA_INDIRECT_ACCESS_HH__

#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <iterator>
#include <map>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include "arch/generic/mmu.hh"
#include "base/statistics.hh"
#include "base/types.hh"
#include "gem5/maa_page_fed_soa_abi.hh"
#include "mem/MAA/BoundedDescriptorSpool.hh"
#include "mem/MAA/BoundedFourRunMerge.hh"
#include "mem/MAA/BoundedMetadataLedger.hh"
#include "mem/MAA/BoundedQuantileRanges.hh"
#include "mem/MAA/BoundedRangePass.hh"
#include "mem/MAA/CompleteLineDrainBudget.hh"
#include "mem/MAA/CompleteLinePayloadStaging.hh"
#include "mem/MAA/DenseBackingLineTracker.hh"
#include "mem/MAA/DirectIndexFeeder.hh"
#include "mem/MAA/FusedP16ProductState.hh"
#include "mem/MAA/InlineOperandRetirement.hh"
#include "mem/MAA/ReorderSurvivalTracker.hh"
#include "mem/MAA/SharedSourceOverlapScheduler.hh"
#include "mem/MAA/SoaJitOldResultBuffer.hh"
#include "mem/MAA/SoaJitOverlapState.hh"
#include "mem/MAA/SoaJitResultPipeline.hh"
#include "mem/MAA/SoaJitScalarBroadcast.hh"
#include "mem/MAA/Tables.hh"
#include "mem/MAA/VirtualCombineLookupPipeline.hh"
#include "mem/MAA/VirtualCombinePayloadStore.hh"
#include "mem/MAA/VirtualCombineVictimSelector.hh"
#include "mem/MAA/VirtualCombinerPageOrder.hh"
#include "mem/MAA/VirtualResponsePayloadStore.hh"
#include "mem/MAA/VirtualRetirementScoreboard.hh"
#include "mem/MAA/VirtualSourceFanout.hh"
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
        std::vector<std::array<uint8_t, 8>> packed_words;
        size_t next_packed_word = 0;
        int reserved_words = 0;
        maa::VirtualSourceFanout fanout{};
        VirtualCombinePayloadStore::LineRefs shared_word_refs =
            VirtualCombinePayloadStore::emptyLineRefs();
        int claim_rt_idx = -1;
        int claim_row_id = -1;
        int claim_entry_id = -1;
        Addr claim_grow_addr = 0;
        Addr claim_addr = 0;
        int claim_head = -1;
        uint32_t lookup_next_issue_sequence = 0;
        uint32_t lookup_next_completion_sequence = 0;
        uint32_t lookup_pending = 0;
        bool lookup_issue_closed = false;
        maa::FusedP16ResponseOwner fusedProduct{};
    };
    std::vector<VirtualResponseSlot> virtual_response_slots;
    VirtualResponsePayloadStore virtual_response_line_payloads;
    int virtual_response_words = 0;
    int virtual_response_word_pool_limit = 0;
    int virtual_combine_lookup_latency_cycles = 0;
    int virtual_words_per_cycle_limit = 0;
    uint64_t virtual_word_budget_cycle = 0;
    int virtual_word_attempts_this_cycle = 0;
    int virtual_reserved_response_words = 0;
    int virtual_response_payload_words = 0;
    maa::VirtualCombineLookupPipeline virtual_combine_lookup_pipeline;
    uint64_t virtual_combine_lookup_generation = 0;
    uint64_t virtual_combine_lookup_next_generation = 0;
    uint64_t virtual_combine_lookup_next_issue_sequence = 0;
    uint64_t virtual_combine_lookup_completion_budget_cycle = 0;
    int virtual_combine_lookup_completions_this_cycle = 0;
    bool virtual_pending_source = false;
    Addr virtual_pending_source_addr = 0;
    int virtual_pending_source_head = -1;
    int virtual_pending_source_words = 0;
    int virtual_pending_source_rt_idx = -1;
    int virtual_pending_source_row_id = -1;
    int virtual_pending_source_entry_id = -1;
    Addr virtual_pending_source_grow_addr = 0;
    maa::VirtualSourceFanout virtual_pending_source_fanout{};
    Tick virtual_pending_source_fanout_ready_tick = 0;
    Tick virtual_fanout_scan_finish_tick = 0;
    int virtual_pending_source_high_water = 0;
    uint64_t virtual_fanout_overlap_resumes = 0;
    uint64_t virtual_fanout_overlap_slot_stalls = 0;
    uint64_t virtual_fanout_overlap_credit_stalls = 0;
    uint64_t virtual_fanout_overlap_credit_stall_cycles = 0;
    Tick virtual_fanout_overlap_credit_stall_start_tick = MaxTick;
    maa::SharedSourceOverlapScheduler::Decision
        virtual_fanout_overlap_last_block =
            maa::SharedSourceOverlapScheduler::Decision::NoPending;
    struct VirtualSourceReservation
    {
        int head = -1;
        int words = 0;
        maa::VirtualSourceFanout fanout{};
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
        VirtualCombinePayloadStore::LineRefs word_refs =
            VirtualCombinePayloadStore::emptyLineRefs();
    };
    std::vector<VirtualCombineSlot> virtual_combine_slots;
    VirtualCombinePayloadStore virtual_combine_payload;
    bool virtual_shared_result_payload = false;
    int virtual_shared_result_payload_limit = 0;
    uint64_t virtual_shared_payload_transfers = 0;
    uint64_t virtual_shared_payload_rollbacks = 0;
    int virtual_shared_payload_high_water = 0;
    VirtualCombinerPageOrder virtual_combine_page_ready;
    int virtual_combine_words_configured = 0;
    int virtual_combine_ways = 0;
    int virtual_combine_set_xor_shift = 0;
    int virtual_combine_victim_policy = 0;
    int virtual_combine_banks = 0;
    std::vector<int> virtual_combine_set_victims;
    std::vector<bool> virtual_combine_bank_used;
    uint64_t virtual_combine_bank_cycle = 0;
    uint64_t virtual_combine_bank_conflict_cycle = 0;
    int virtual_combine_words_limit = 0;
    int virtual_combine_words = 0;
    int virtual_max_combine_words = 0;
    int virtual_max_outstanding_writes_limit = 0;
    bool virtual_masked_writes = false;
    bool virtual_dense_write_allocate = false;
    bool virtual_complete_line_only = false;
    maa::CompleteLineDrainBudget virtual_complete_line_drain_budget;
    maa::CompleteLinePayloadStaging virtual_complete_line_payload_staging;
    bool virtual_complete_line_payload_stage_partial = false;
    Tick virtual_complete_line_drain_retry_tick = 0;
    Tick virtual_complete_line_payload_backpressure_tick = 0;
    maa::DenseBackingLineTracker dense_backing_lines;
    uint64_t virtual_dense_initialization_writes = 0;
    struct VirtualRetirementSenderState : public Packet::SenderState
    {
        maa::VirtualRetirementScoreboard::Identity identity{};
    };
    maa::VirtualRetirementScoreboard virtual_retirement_scoreboard;
    std::vector<int> virtual_page_logical_words;
    std::vector<int> virtual_page_scanned_words;
    std::vector<int> virtual_page_expected_words;
    std::vector<int> virtual_page_issued_words;
    std::vector<int> virtual_page_completed_words;
    std::vector<Addr> virtual_page_last_write_key;
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
    std::vector<bool> virtual_shared_partial_spill_lines;
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
    Tick macro_b_first_issue_tick = 0;
    Tick macro_b_last_issue_tick = 0;
    Tick macro_b_last_response_tick = 0;
    Tick macro_row_first_insert_tick = 0;
    Tick macro_row_last_insert_tick = 0;
    Tick macro_a_first_issue_tick = 0;
    Tick macro_a_last_issue_tick = 0;
    Tick macro_a_last_response_tick = 0;
    Tick macro_backing_first_issue_tick = 0;
    Tick macro_backing_last_issue_tick = 0;
    Tick macro_backing_last_ack_tick = 0;
    Tick macro_backing_credit_stall_tick = 0;
    uint64_t macro_b_lines = 0;
    uint64_t macro_b_bytes = 0;
    uint64_t macro_b_retries = 0;
    uint64_t macro_b_queue_high_water = 0;
    uint64_t macro_a_lines = 0;
    uint64_t macro_a_bytes = 0;
    uint64_t macro_a_retries = 0;
    uint64_t macro_backing_transport_bytes = 0;
    uint64_t macro_backing_semantic_bytes = 0;
    uint64_t macro_backing_line_issues = 0;
    uint64_t macro_backing_word_issues = 0;
    uint64_t macro_backing_credit_stalls = 0;
    uint64_t macro_backing_address_retries = 0;
    std::array<uint64_t, 6> macro_request_reason_cycles{};
    std::array<uint64_t, 4> macro_pipeline_cycles{};
    ReorderSurvivalTracker reorder_survival;
    uint64_t reorder_instruction_sequence = 0;

public:
    MAA *maa;
    IndirectAccessUnit();
    ~IndirectAccessUnit();
    static constexpr size_t lineHandoffMetadataBytesPerWrite()
    {
        return maa::VirtualRetirementScoreboard::ConservativeBytesPerEntry;
    }
    static constexpr size_t lineHandoffMetadataFixedBytes()
    {
        return maa::VirtualRetirementScoreboard::ConservativeFixedBytes;
    }
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
                  bool _virtual_shared_result_payload,
                  int _virtual_combine_ways,
                  int _virtual_combine_set_xor_shift,
                  int _virtual_combine_victim_policy,
                  int _virtual_combine_banks,
                  int _virtual_response_slots,
                  int _virtual_response_words,
                  int _virtual_response_word_pool,
                  int _virtual_combine_lookup_latency_cycles,
                  int _virtual_words_per_cycle,
                  int _virtual_max_outstanding_writes,
                  bool _virtual_masked_writes,
                  bool _virtual_dense_write_allocate,
                  bool _virtual_complete_line_only,
                  int _virtual_complete_line_drain_width,
                  int _complete_line_payload_width,
                  int _complete_line_payload_active_lines,
                  int _complete_line_payload_banks,
                  bool _complete_line_payload_stage_partial,
                  int _soa_jit_predicate_active_credits,
                  int _virtual_index_buffer_lines,
                  int _virtual_index_issue_lines_per_cycle,
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
                  MAA *_maa);
    Status getState() const { return state; }
    bool hasLiveSoaJitState() const;
    bool scheduleNextExecution(bool force = false);
    void scheduleExecuteInstructionEvent(int latency = 0);
    void setInstruction(Instruction *_instruction);
    void memWritePacketSent(Addr addr);
    void memReadPacketSent(Addr addr);
    void cacheWritePacketSent(Addr addr);
    void cacheReadPacketSent(Addr addr);
    void retirementWriteComplete(Addr addr,
                                 const uint8_t *writeRespPayload = nullptr,
                                 unsigned payloadBytes = 0,
                                 PacketPtr responsePacket = nullptr);
    bool hasPendingDirectIndexLine(Addr addr) const {
        return direct_index_feeder.hasPending(addr);
    }

    bool recvData(const Addr addr, uint8_t *dataptr, bool is_block_cached);

    /* Related to BaseMMU::Translation Inheretance */
    void markDelayed() override {}
    void finish(const Fault &fault, const RequestPtr &req,
                ThreadContext *tc, BaseMMU::Mode mode) override;

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
    Addr my_predicate_addr, my_predicate_min_addr, my_predicate_max_addr;
    Addr my_result_addr, my_result_min_addr, my_result_max_addr;
    int8_t my_addr_range_id, my_backing_addr_range_id, my_index_addr_range_id;
    int8_t my_predicate_addr_range_id, my_result_addr_range_id;
    int my_index_min, my_index_stride;
    struct DirectIndexWord
    {
        uint32_t value = 0;
        Addr line_addr = 0;
        Addr word_paddr = 0;
        uint32_t phase = 0;
        uint32_t logical_itr = 0;
    };
    enum class DirectIndexDiscardReason : uint8_t
    {
        DescriptorInserted,
        PredicateRejected,
        PartitionRejected,
        SummaryObserved,
    };
    int direct_index_buffer_lines = 1;
    int direct_index_issue_lines_per_cycle = 1;
    bool direct_index_force_cache = false;
    int direct_index_partitions = 1;
    int direct_index_max_partitions = 1;
    int direct_index_filter_words_per_cycle = 0;
    int direct_index_partition = 0;
    uint32_t direct_index_phase = 1;
    bool direct_index_partition_barrier = false;
    BoundedRangePassTracker bounded_range_pass;
    BoundedGrowPassPlan bounded_grow_plan;
    BoundedDescriptorSpool descriptor_spool;
    BoundedFourRunMerge bounded_global_merge;
    enum class BoundedGlobalMergePhase : uint8_t
    {
        None,
        Materialize,
        Merge,
        Complete,
    };
    BoundedGlobalMergePhase bounded_global_merge_phase =
        BoundedGlobalMergePhase::None;
    uint32_t bounded_global_merge_run = 0;
    uint32_t bounded_global_merge_slice_cursor = 0;
    int bounded_global_merge_chain_head = -1;
    uint64_t bounded_global_merge_sort_comparisons = 0;
    uint32_t bounded_global_merge_row_groups = 0;
    uint32_t bounded_global_merge_source_responses = 0;
    uint32_t bounded_global_merge_terminal_acks = 0;
    bool bounded_global_merge_batch_inflight = false;
    bool bounded_global_merge_last_key_valid = false;
    std::array<uint64_t, 4> bounded_global_merge_last_key{};
    bool bounded_global_merge_last_row_valid = false;
    uint32_t bounded_global_merge_last_slice = 0;
    uint64_t bounded_global_merge_last_row = 0;
    struct BoundedGlobalMergeReadSlot
    {
        bool valid = false;
        Addr paddr = 0;
        Addr vaddr = 0;
        uint32_t run = 0;
        uint32_t line = 0;
    };
    std::array<BoundedGlobalMergeReadSlot, BoundedFourRunMerge::Runs>
        bounded_global_merge_read_slots{};
    struct BoundedGlobalMergeWriteSlot
    {
        bool valid = false;
        Addr paddr = 0;
        Addr vaddr = 0;
    };
    std::array<BoundedGlobalMergeWriteSlot,
               BoundedFourRunMerge::MaxOutstandingWrites>
        bounded_global_merge_write_slots{};
    bool bounded_global_merge_source_pending = false;
    bool bounded_global_merge_source_ready = false;
    Addr bounded_global_merge_source_paddr = 0;
    Addr bounded_global_merge_source_vaddr = 0;
    int bounded_global_merge_source_head = -1;
    int bounded_global_merge_source_tail = -1;
    int bounded_global_merge_source_words = 0;
    bool bounded_global_merge_source_fanout_valid = false;
    maa::VirtualSourceFanout bounded_global_merge_source_fanout{};
    Tick bounded_global_merge_source_fanout_ready_tick = 0;
    std::array<uint8_t, BoundedFourRunMerge::LineBytes>
        bounded_global_merge_source_data{};
    bool descriptor_spool_bucket_active = false;
    bool descriptor_spool_bucket_scan_complete = false;
    bool descriptor_spool_replay_active = false;
    bool descriptor_spool_read_ahead_active = false;
    bool descriptor_spool_overlap_opportunity_recorded = false;
    bool descriptor_spool_operation = false;
    Addr descriptor_spool_base_vaddr = 0;
    static constexpr uint32_t DescriptorIndexPageBytes = 4096;
    static constexpr uint32_t MaxDescriptorIndexPages = 17;
    std::array<Addr, MaxDescriptorIndexPages>
        descriptor_spool_index_page_paddrs{};
    std::array<bool, MaxDescriptorIndexPages>
        descriptor_spool_index_page_valid{};
    struct DescriptorSpoolPendingLine
    {
        bool valid = false;
        bool responded = false;
        bool read_ahead = false;
        bool demand_observed = false;
        bool ready_before_demand = false;
        bool useful = false;
        Addr paddr = 0;
        Addr vaddr = 0;
        uint32_t pass = 0;
        uint32_t line = 0;
        std::array<uint8_t, BoundedDescriptorSpool::LineBytes> data{};
    };
    std::array<DescriptorSpoolPendingLine,
               BoundedDescriptorSpool::MaxOutstandingReadLines>
        descriptor_spool_read_slots{};
    struct DescriptorSpoolWriteSlot
    {
        bool valid = false;
        Addr paddr = 0;
        Addr vaddr = 0;
    };
    std::array<DescriptorSpoolWriteSlot,
               BoundedDescriptorSpool::MaxOutstandingWrites>
        descriptor_spool_write_slots{};
    bool descriptor_spool_current_valid = false;
    uint32_t descriptor_spool_current_cursor = 0;
    BoundedDescriptorSpool::Descriptor descriptor_spool_current_descriptor{};
    DirectIndexWord descriptor_spool_current_word{};
    uint64_t descriptor_spool_bucket_attempts = 0;
    uint64_t descriptor_spool_bucket_commits = 0;
    uint64_t descriptor_spool_filter_retry_inspections = 0;
    uint64_t descriptor_spool_final_flush_stalls = 0;
    uint32_t descriptor_spool_overlap_opportunities = 0;
    uint32_t descriptor_spool_next_pass_read_issues = 0;
    uint32_t descriptor_spool_next_pass_read_responses = 0;
    uint32_t descriptor_spool_useful_prefetched_lines = 0;
    uint32_t descriptor_spool_demand_waits_avoided = 0;
    uint32_t descriptor_spool_prefetch_occupancy = 0;
    uint32_t descriptor_spool_prefetch_occupancy_hwm = 0;
    Tick descriptor_spool_prefetch_occupancy_tick = 0;
    uint64_t descriptor_spool_prefetch_occupancy_line_ticks = 0;
    uint32_t descriptor_spool_wasted_prefetched_lines = 0;
    bool descriptor_spool_demand_wait_active = false;
    bool descriptor_spool_demand_wait_boundary = false;
    Tick descriptor_spool_demand_wait_tick = 0;
    uint32_t descriptor_spool_demand_wait_cursor = 0;
    uint32_t descriptor_spool_boundary_demand_wait_events = 0;
    uint64_t descriptor_spool_boundary_demand_wait_ticks = 0;
    uint32_t descriptor_spool_within_pass_demand_wait_events = 0;
    uint64_t descriptor_spool_within_pass_demand_wait_ticks = 0;
    bool direct_index_summary_active = false;
    bool direct_index_summary_overflow = false;
    bool direct_index_iteration_fallback = false;
    uint32_t direct_index_summary_next_iteration = 0;
    uint32_t direct_index_summary_records = 0;
    uint64_t direct_index_summary_probes = 0;
    uint64_t direct_index_summary_reduction_visits = 0;
    int direct_index_next_prefetch_itr = 0;
    maa::DirectIndexFeeder direct_index_feeder;
    int direct_index_max_lines = 0;
    int direct_index_max_words = 0;
    static constexpr size_t SoaPredicateMaxLines = 16;
    static constexpr size_t SoaPredicateLineDataBytes = 64;
    struct SoaPredicateLine
    {
        Addr blockVaddr = 0;
        Addr blockPaddr = 0;
        uint64_t generation = 0;
        bool pending = false;
        bool valid = false;
        std::array<uint8_t, SoaPredicateLineDataBytes> data{};
    };
    static_assert(sizeof(bool) == 1,
                  "predicate feeder byte accounting requires byte bools");
    static constexpr size_t SoaPredicateLineStateBytes =
        2 * sizeof(Addr) + sizeof(uint64_t) + 2 * sizeof(bool) +
        SoaPredicateLineDataBytes;
    static constexpr size_t SoaPredicateFeederStateBytes =
        SoaPredicateMaxLines * SoaPredicateLineStateBytes;
    std::array<SoaPredicateLine, SoaPredicateMaxLines> soa_predicate_lines{};
    int soa_jit_predicate_active_credits = 1;
    enum class SoaJitContextState : uint8_t
    {
        Free,
        AwaitARead,
        Active,
        AwaitAWriteResp,
    };
    enum class SoaJitLookaheadState : uint8_t
    {
        Free,
        Waiting,
        Ready,
    };
    struct SoaJitLookaheadSlot
    {
        std::array<uint8_t, 8> value{};
        Addr valuePaddr = 0;
        uint64_t generation = 0;
        int offset = -1;
        int logicalItr = -1;
        uint16_t aWord = 0;
        uint16_t valueWord = 0;
        SoaJitLookaheadState state = SoaJitLookaheadState::Free;
    };
    struct SoaJitContext
    {
        std::array<uint8_t, 64> aLine{};
        std::array<SoaJitLookaheadSlot,
                   SoaJitValueCoalescer::MaxLookahead> lookahead{};
        Addr aPaddr = 0;
        uint64_t generation = 0;
        int nextOffset = -1;
        int issueOffset = -1;
        int remaining = 0;
        uint8_t lookaheadOccupancy = 0;
        uint8_t preAUsesPending = 0;
        uint16_t inlineSuccessMask = 0;
        std::array<uint32_t, 16> inlineDestinations{};
        std::array<uint8_t, 2> inlineRetirementCredits{};
        uint8_t inlineRetirementCreditCount = 0;
        SoaJitContextState state = SoaJitContextState::Free;
    };
    static_assert(sizeof(SoaJitContext) <= 512,
                  "SoA/JIT RMW context exceeds the fixed 512-byte budget");
    static constexpr size_t SoaJitContexts =
        SoaJitValueCoalescer::MaxContexts;
    std::array<SoaJitContext, SoaJitContexts> soa_jit_contexts{};
    SoaJitResultPipeline soa_jit_result_pipeline;
    SoaJitScalarBroadcast soa_jit_scalar_broadcast;
    SoaJitOldResultBuffer soa_jit_old_result_buffer;
    struct SoaJitWriteSenderState : public Packet::SenderState
    {
        SoaJitScalarBroadcast::WriteIdentity identity{};
    };
    struct SoaJitOldResultSenderState : public Packet::SenderState
    {
        SoaJitOldResultBuffer::Identity identity{};
        Addr physicalAddress = 0;
    };
    struct InlineRetirementSenderState : public Packet::SenderState
    {
        uint64_t generation = 0;
        uint32_t sequence = 0;
        uint8_t credit = 0;
        Addr physicalAddress = 0;
    };
    struct SoaJitValuePrefetchCursor
    {
        Addr lastBlockVaddr = 0;
        uint32_t nextLogical = 0;
        bool lastBlockValid = false;
    };
    static_assert(sizeof(SoaJitValuePrefetchCursor) <= 16,
                  "SoA/JIT value-prefetch cursor exceeds 16 bytes");
    static constexpr size_t SoaJitValuePrefetchMaxScans =
        SoaJitValueCoalescer::MaxPrefetchCredits *
        SoaJitValueCoalescer::LineBytes / sizeof(uint32_t);
    bool soa_jit_operation_active = false;
    SoaJitValueCoalescer soa_jit_value_coalescer;
    int soa_jit_active_contexts = 1;
    int soa_jit_value_lookahead = 1;
    bool soa_jit_value_cache_enable = false;
    bool soa_jit_pre_a_value_lookahead = false;
    int soa_jit_value_prefetch_credits = 0;
    int soa_jit_active_value_owners = 4;
    SoaJitValuePrefetchCursor soa_jit_value_prefetch_cursor;
    int soa_jit_apply_lanes = 1;
    SoaJitApplyLanePool soa_jit_apply_lane_pool;
    uint64_t fused_p16_generation_counter = 0;
    uint64_t fused_p16_generation = 0;
    bool fused_p16_operation_active = false;
    static_assert(
        sizeof(maa::FusedP16LifecycleStorageBound) <=
            maa::FusedP16ProductContract::LifecycleCppBoundBytesPerUnit,
        "fused-p16 lifecycle storage exceeds the accounted C++ bound");
    uint64_t fused_p16_epochs = 0;
    uint64_t fused_p16_source_ordinals = 0;
    uint64_t fused_p16_coefficient_read_issues = 0;
    uint64_t fused_p16_coefficient_read_responses = 0;
    uint64_t fused_p16_coefficient_fills = 0;
    uint64_t fused_p16_coefficient_hits = 0;
    uint64_t fused_p16_coefficient_merged_waiters = 0;
    uint64_t fused_p16_coefficient_evictions = 0;
    uint64_t fused_p16_coefficient_deliveries = 0;
    uint64_t fused_p16_coefficient_stalls = 0;
    uint64_t fused_p16_alu_accepts = 0;
    uint64_t fused_p16_alu_completions = 0;
    uint64_t fused_p16_alu_backpressure = 0;
    uint64_t fused_p16_product_insertions = 0;
    uint64_t fused_p16_product_write_completions = 0;
    bool soa_jit_all_rows_claimed = false;
    // Fixed operation state only: pressure never allocates an operation-sized
    // ordinal bitmap or spills Row/Offset metadata.
    bool soa_jit_epoch_drained = false;
    bool soa_jit_retry_valid = false;
    bool soa_jit_retry_condition = false;
    int soa_jit_retry_ordinal = -1;
    int soa_jit_epoch_resume_i = -1;
    uint64_t soa_jit_epoch_drains = 0;
    uint64_t soa_jit_epoch_start_ordinal = 0;
    uint64_t soa_jit_next_source_ordinal = 0;
    uint64_t soa_jit_next_generation = 1;
    uint64_t soa_jit_generation = 0;
    uint64_t soa_jit_selected = 0;
    uint64_t soa_jit_predicate_rejected = 0;
    uint64_t soa_jit_predicate_line_issues = 0;
    uint64_t soa_jit_predicate_line_responses = 0;
    uint64_t soa_jit_predicate_line_hits = 0;
    uint64_t soa_jit_predicate_uses = 0;
    uint64_t soa_jit_predicate_feeder_stalls = 0;
    uint64_t soa_jit_predicate_feeder_high_water = 0;
    uint64_t soa_jit_a_read_issues = 0;
    uint64_t soa_jit_a_read_responses = 0;
    uint64_t soa_jit_value_read_issues = 0;
    uint64_t soa_jit_value_read_responses = 0;
    uint64_t soa_jit_value_fills = 0;
    uint64_t soa_jit_value_cached_responses = 0;
    uint64_t soa_jit_value_hits = 0;
    uint64_t soa_jit_value_merged_waiters = 0;
    uint64_t soa_jit_value_evictions = 0;
    uint64_t soa_jit_value_deliveries = 0;
    uint64_t soa_jit_value_stalls = 0;
    uint64_t soa_jit_value_cache_high_water = 0;
    uint64_t soa_jit_value_prefetch_issues = 0;
    uint64_t soa_jit_value_prefetch_responses = 0;
    uint64_t soa_jit_value_prefetch_promotions = 0;
    uint64_t soa_jit_value_prefetch_discards = 0;
    uint64_t soa_jit_value_prefetch_owned = 0;
    uint64_t soa_jit_value_prefetch_credit_stalls = 0;
    uint64_t soa_jit_value_prefetch_high_water = 0;
    uint64_t soa_jit_lookahead_issues = 0;
    uint64_t soa_jit_lookahead_responses = 0;
    uint64_t soa_jit_lookahead_stalls = 0;
    uint64_t soa_jit_lookahead_high_water = 0;
    uint64_t soa_jit_pre_a_value_issues = 0;
    uint64_t soa_jit_pre_a_value_ready_at_a_response = 0;
    uint64_t soa_jit_pre_a_value_uses = 0;
    uint64_t soa_jit_aliases_applied = 0;
    uint64_t soa_jit_apply_lane_high_water = 0;
    uint64_t soa_jit_a_write_issues = 0;
    uint64_t soa_jit_a_write_responses = 0;
    uint64_t soa_jit_old_result_captures = 0;
    uint64_t soa_jit_old_result_write_issues = 0;
    uint64_t soa_jit_old_result_write_responses = 0;
    uint64_t soa_jit_old_result_pressure_issues = 0;
    uint64_t soa_jit_old_result_partial_high_water = 0;
    uint64_t soa_jit_old_result_stalls = 0;
    bool soa_jit_old_result_selection_closed = false;
    bool soa_jit_old_result_finished = false;
    uint64_t soa_jit_context_stalls = 0;
    uint64_t soa_jit_context_high_water = 0;
    gem5::maa::PageFedSoaJitState soa_jit_page_fed_state;
    gem5::maa::InlineOperandRetirementState inline_retirement_state;
    std::array<gem5::maa::InlineRetirementRecord, 8>
        inline_retirement_packer{};
    uint8_t inline_retirement_packer_records = 0;
    uint8_t inline_retirement_packer_credit = UINT8_MAX;
    uint64_t soa_jit_page_fed_open_commands = 0;
    uint64_t soa_jit_page_fed_admit_commands = 0;
    uint64_t soa_jit_page_fed_close_commands = 0;
    uint64_t soa_jit_page_fed_command_responses = 0;
    uint64_t soa_jit_page_fed_admitted_words = 0;
    uint64_t soa_jit_page_fed_spd_index_reads = 0;
    uint64_t inline_operand_spd_value_reads = 0;
    uint64_t inline_operand_insertions = 0;
    uint64_t inline_operand_consumptions = 0;
    uint64_t inline_retirement_successes = 0;
    uint64_t inline_retirement_write_issues = 0;
    uint64_t inline_retirement_write_responses = 0;
    uint64_t inline_retirement_acks = 0;
    uint64_t inline_retirement_credit_stalls = 0;
    uint64_t soa_jit_page_fed_row_writes = 0;
    uint64_t soa_jit_page_fed_admission_cycles = 0;
    uint64_t soa_jit_page_fed_coherent_index_read_lines = 0;
    uint64_t soa_jit_page_fed_coherent_index_write_lines = 0;
    Tick strict_page_fed_b_first_tick = 0;
    Tick strict_page_fed_b_last_tick = 0;
    Tick strict_page_fed_row_first_tick = 0;
    Tick strict_page_fed_row_last_tick = 0;
    Tick strict_page_fed_close_tick = 0;
    Tick strict_page_fed_a_first_issue_tick = 0;
    Tick strict_page_fed_a_last_issue_tick = 0;
    Tick strict_page_fed_a_last_response_tick = 0;
    Tick strict_page_fed_backing_first_issue_tick = 0;
    Tick strict_page_fed_backing_last_issue_tick = 0;
    Tick strict_page_fed_backing_last_ack_tick = 0;
    Tick strict_page_fed_consumer_begin_tick = 0;
    Tick strict_page_fed_consumer_end_tick = 0;
    bool strict_page_fed_terminal_recorded = false;
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
    // These sets are functional for every legacy/non-spool operation: cache
    // routing and row-table configuration consume their exact cardinalities.
    // The resident-first spool path explicitly suppresses updates so its
    // operation-sized state remains bounded.
    std::set<Addr> my_unique_WORD_addrs;
    std::set<Addr> my_unique_CL_addrs;
    std::set<Addr> my_unique_ROW_addrs;

    Addr translatePacket(Addr vaddr, BaseMMU::Mode mode = BaseMMU::Read,
                         unsigned size = 64);
    bool isVirtualLoad() const;
    bool isDirectIndexLoad() const;
    bool isFusedP16Product() const;
    bool isSoaJitRmw() const;
    bool isSoaJitScalarRmw() const;
    bool isSoaJitMaskedIndexRmw() const;
    bool isSoaJitOldResultRmw() const;
    bool isSoaJitPageFedRmw() const;
    bool isSoaJitInlineOperandRmw() const;
    bool strictTwoPhaseOperation() const;
    bool denseWriteAllocateOperation() const;
    bool completeLineOnlyOperation() const;
    bool legalCompleteLineTail(Addr line_vaddr, uint16_t valid_words) const;
    bool strictPageFedTwoPhaseOperation() const;
    bool usesBoundedDirectIndexPasses() const;
    bool usesBoundedSourceResponses() const;
    void fillDirectIndexWindow();
    void fillDescriptorSpoolWindow(bool read_ahead = false);
    void serviceDescriptorSpoolReadAhead();
    void promoteDescriptorSpoolReadAhead(uint32_t pass);
    void accountDescriptorSpoolPrefetchOccupancy();
    void markDescriptorSpoolLineUseful(DescriptorSpoolPendingLine &slot);
    void startDescriptorSpoolDemandWait(uint32_t cursor);
    void finishDescriptorSpoolDemandWait(uint32_t cursor);
    bool ensureDirectIndex(int itr);
    uint32_t peekDirectIndex(int itr) const;
    DirectIndexWord currentDirectIndexWord(int itr) const;
    uint32_t directIndexPassForGrow(Addr grow_addr) const;
    uint64_t directIndexRangeKey(uint32_t index, Addr grow_addr,
                                 int iteration) const;
    void finishAdaptiveSummary();
    BoundedRangePassTracker::Range directIndexSourceGrowRange();
    int directIndexRetirementPass() const;
    void finishBoundedRangePass(int pass, const char *reason);
    void discardDirectIndex(int itr, uint32_t expected_value,
                            DirectIndexDiscardReason reason);
    bool receiveDirectIndex(Addr addr, uint8_t *dataptr,
                            bool is_block_cached);
    size_t soaPredicateSlotsUsed() const;
    bool soaPredicateLinesEmpty() const;
    SoaPredicateLine *findSoaPredicateLine(Addr block_vaddr);
    const SoaPredicateLine *findSoaPredicateLine(Addr block_vaddr) const;
    void serviceSoaPredicateFeeder(int itr);
    bool ensureSoaPredicate(int itr);
    bool soaPredicateValue(int itr);
    void discardSoaPredicateIfDone(int itr);
    bool receiveSoaPredicate(Addr addr, uint8_t *dataptr,
                             bool is_block_cached);
    int64_t soaSourcePosition(int logical_itr) const;
    bool serviceSoaJitValuePrefetch();
    bool soaJitValuePrefetchComplete() const;
    void rememberSoaJitPressureRetry(int logical_itr,
                                     bool condition_taken);
    void commitSoaJitSourceOrdinal(int logical_itr,
                                   bool condition_taken);
    bool insertPageFedSoaJitIndex(uint32_t index, uint32_t ordinal,
                                  uint32_t operand_bits = 0,
                                  bool inline_operand = false);
    void resetSoaJitEpochTables();
    bool serviceSoaJitBuild();
    bool receiveSoaJitData(Addr addr, uint8_t *dataptr,
                           bool is_block_cached);
    bool receiveFusedP16Coefficient(Addr addr, uint8_t *dataptr,
                                    bool is_block_cached);
    bool beginFusedP16ResponseHead(size_t response_slot,
                                  VirtualResponseSlot &slot);
    void checkFusedP16Terminal();
    bool fillSoaJitLookahead(size_t context_index);
    bool serviceSoaJitLookahead();
    bool issueSoaJitValueRead(size_t context_index, size_t slot_index,
                              int offset);
    bool issueSoaJitScalar(size_t context_index, size_t slot_index,
                              int offset);
    bool issueSoaJitInlineOperand(size_t context_index, size_t slot_index,
                                  int offset);
    bool applySoaJitValue(SoaJitContext &context, uint16_t context_index,
                          uint16_t a_word, uint32_t logical_itr,
                          const uint8_t *value);
    enum class SoaJitOldResultWriteMode : uint8_t
    {
        FullOnly,
        Pressure,
        Drain,
    };
    bool serviceSoaJitOldResultWrites(SoaJitOldResultWriteMode mode);
    bool completeSoaJitOldResultWrite(
        const SoaJitOldResultBuffer::Identity &identity);
    bool issueSoaJitWrite(SoaJitContext &context);
    void issueInlineRetirementWrites(SoaJitContext &context);
    void issueInlineRetirementCredit(
        uint8_t credit, const maa::InlineRetirementRecord *records,
        uint8_t record_count);
    bool completeInlineRetirementWrite(uint8_t credit, uint64_t generation,
                                       uint32_t sequence);
    bool completeSoaJitWrite(
        const SoaJitScalarBroadcast::WriteIdentity &identity);
    void validateSoaJitAddressSpans();
    bool soaJitContextsEmpty() const;
    void observeSoaJitResultPipeline();
    size_t soaJitActiveContextCount() const;
    size_t soaJitLookaheadOccupancy() const;
    void checkSoaJitTerminal();
    bool receiveDescriptorSpool(Addr addr, uint8_t *dataptr,
                                bool is_block_cached);
    bool loadDescriptorSpoolCurrent(uint32_t cursor);
    void releaseDescriptorSpoolReadLines(uint32_t next_cursor);
    size_t descriptorSpoolReadSlotsUsed() const;
    size_t descriptorSpoolWriteSlotsUsed() const;
    bool flushDescriptorSpoolLine(uint32_t pass, bool allow_partial);
    bool finishDescriptorSpoolBucketing();
    void startDescriptorSpoolReplay();
    void startBoundedGlobalRunMaterialization();
    void serviceBoundedGlobalRunMaterialization();
    void serviceBoundedGlobalMerge();
    maa::VirtualSourceFanout buildVirtualSourceFanout(
        int source_head, int source_words, Tick &ready_tick);
    bool deferVirtualSourceFanout(Tick ready_tick, const char *path,
                                  bool account);
    int virtualSourcePayloadWords(
        const maa::VirtualSourceFanout &fanout) const;
    bool virtualSourceCreditAvailable(
        const maa::VirtualSourceFanout &fanout) const;
    void issuePendingVirtualSource();
    void clearPendingVirtualSource();
    bool resumePendingVirtualSourceFromRequest(bool response_throttled);
    bool virtualOverlapProgressPossible(bool response_throttled,
                                        bool spill_succeeded) const;
    void issueVirtualSource(Addr source_addr, int source_head,
                            int source_words,
                            const maa::VirtualSourceFanout &fanout,
                            Tick fanout_ready_tick,
                            int source_rt_idx,
                            int source_row_id, int source_entry_id,
                            Addr source_grow_addr, int latency);
    bool issueBoundedGlobalSourceLine();
    bool receiveBoundedGlobalMerge(Addr addr, uint8_t *dataptr,
                                   bool is_block_cached);
    std::array<uint64_t, 4> boundedGlobalMergeKey(
        const BoundedFourRunMerge::Descriptor &descriptor);
    void createBoundedGlobalMergeReadPacket(Addr vaddr, uint32_t run,
                                            uint32_t line);
    void createBoundedGlobalMergeWritePacket(
        Addr vaddr,
        const std::array<uint8_t, BoundedFourRunMerge::LineBytes> &data);
    void resetBoundedGlobalSorterTables();
    uint16_t captureDescriptorIndexPage(uint32_t iteration,
                                        Addr word_paddr);
    Addr descriptorIndexWordPaddr(uint32_t iteration) const;
    size_t descriptorSpoolControlBytes() const;
    void createDescriptorSpoolReadPacket(Addr vaddr, uint32_t pass,
                                         uint32_t line, bool read_ahead);
    void createDescriptorSpoolWritePacket(
        Addr vaddr,
        const std::array<uint8_t, BoundedDescriptorSpool::LineBytes> &data);
    void createDirectIndexReadPacket(Addr addr, int latency);
    void createSoaPredicateReadPacket(Addr addr, int latency);
    void createSoaJitReadPacket(Addr addr, int latency);
    void createFusedP16CoefficientReadPacket(Addr addr, int latency);
    void accountReadResponse(Addr addr, bool is_block_cached);
    Addr backingWordAddr(int itr) const;
    void validateRetirementWriteRange(Addr vaddr, unsigned size,
                                      uint16_t valid_words) const;
    void initializeVirtualPageTracking();
    void trackVirtualIteration(int itr, bool write_expected);
    void markVirtualPageReadyIfComplete(int page,
                                        Addr final_write_key = 0);
    maa::VirtualRetirementScoreboard::Identity trackVirtualRetirementWrite(
        Addr write_key, Addr vaddr, unsigned size, uint16_t valid_words);
    void completeVirtualRetirementWrite(
        const maa::VirtualRetirementScoreboard::Identity &identity,
        const uint8_t *writeRespPayload, unsigned payloadBytes);
    bool createRetirementWrite(int itr, const uint8_t *data);
    bool createRetirementWrite(Addr vaddr, unsigned size, const uint8_t *data,
                               uint16_t valid_words = 0);
    bool drainVirtualResponses();
    bool reserveVirtualCombineBank(int itr);
    int virtualCombineSet(Addr line_vaddr) const;
    bool insertVirtualCombineWord(
        int itr, const uint8_t *data,
        VirtualCombinePayloadStore::WordRef transferred_ref =
            VirtualCombinePayloadStore::InvalidWord);
    bool completeLineDrainAvailable();
    void recordCompleteLineDrainIssue();
    maa::CompleteLinePayloadStaging::Identity completeLinePayloadIdentity(
        int slot, const VirtualCombineSlot &line) const;
    bool completeLinePayloadReady(int slot, const VirtualCombineSlot &line);
    void completeLinePayloadIssued(int slot, const VirtualCombineSlot &line);
    void recordCompleteLinePayloadBackpressure();
    size_t virtualBackingLineIndex(Addr line_vaddr) const;
    bool virtualSharedPartialSpilled(Addr line_vaddr) const;
    void setVirtualSharedPartialSpilled(Addr line_vaddr, bool spilled);
    bool spillVirtualCombinePartialForSourceCredit();
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
    int getRowTableIdx(int RT_config, int channel, int rank,
                       int bankgroup, int bank);
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
    bool pageFedActiveForCore(int core_id) const;
    bool inlineOperandActiveForCore(int core_id) const;
    bool inlineOperandActiveGeneration(uint64_t generation) const;
    bool inlineOperandAdmissionAllowsRead(int core_id, uint64_t generation,
                                          int8_t region_id) const;
    bool inlineOperandAdmissionAllowsRead(int core_id,
                                          int8_t region_id) const;
    bool inlineOperandAdmissionAllowsReadForMaa(int maa_id,
                                                int8_t region_id) const;
    Cycles admitPageFedSoaJitIndexPage(uint64_t generation, uint8_t page,
                                      uint8_t index_tile);
    Cycles admitPageFedSoaJitIndexValuePage(
        uint64_t generation, uint8_t page, uint8_t index_tile,
        uint8_t value_tile);
    Cycles ackInlineRetirementLine(uint64_t generation,
                                   uint16_t sequence);
    bool inlineRetirementLineVisible(uint64_t generation,
                                     uint16_t sequence) const;
    Cycles closePageFedSoaJit(uint64_t generation);
    void completeFusedP16Multiply(uint64_t generation,
                                  uint8_t response_slot,
                                  uint16_t offset_slot);
};

static_assert(IndirectAccessUnit::lineHandoffMetadataBytesPerWrite() == 44);
static_assert(IndirectAccessUnit::lineHandoffMetadataFixedBytes() == 8);

} // namespace gem5

#endif //__MEM_MAA_INDIRECT_ACCESS_HH__
