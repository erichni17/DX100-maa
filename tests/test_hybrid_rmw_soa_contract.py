import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def test_guarded_seven_word_abi_preserves_instruction_file_size():
    api = read("benchmarks/API/MAA_gem5.hpp")
    port = read("src/mem/MAA/CpuSidePort.cc")
    assert "#define INSTRUCTION_FILE_SIZE 64" in api
    assert "INSTRUCTION_FILE_SIZE - 7 * sizeof(uint64_t)" in api
    assert "*INSTR_backingaddr = (uint64_t)values" in api
    assert "*INSTR_indexaddr = (uint64_t)indices" in api
    assert "*INSTR_predicateaddr = (uint64_t)predicates" in api
    assert "*INSTR_resultaddr = (uint64_t)old_values" in api
    word_five = port[
        port.index("case 5:") : port.index("default:", port.index("case 5:"))
    ]
    assert "isSoaJitRmw" in word_five
    assert "hasValidSoaJitRmwOperands" in word_five
    assert "scheduleDispatchInstructionEvent" in word_five
    word_six = port[
        port.index("case 6:") : port.index("default:", port.index("case 6:"))
    ]
    assert "hasSoaJitOldResult" in word_six
    assert "soaJitPredicateWordReceived" in word_six
    assert "soaJitResultWordReceived" in word_six


def test_shape_rejects_old_value_and_uses_completion_only_dst2():
    interface = read("src/mem/MAA/IF.hh")
    api = read("benchmarks/API/MAA_gem5.hpp")
    port = read("src/mem/MAA/CpuSidePort.cc")
    assert "dst1SpdID == -1 && dst2SpdID != -1" in interface
    assert "src1SpdID == -1 && src2SpdID == -1" in interface
    assert "old_value_tile == -1" in api
    assert "((uint64_t)NA_UINT8 << 8) | (uint64_t)completion_tile" in api
    token_bound = (
        "current_instruction->dst2SpdID >=\n"
        "                                static_cast<int>(num_tiles)"
    )
    assert token_bound in port
    assert (
        "current_instruction->dst2SpdID +"
        not in port[
            port.index("if (current_instruction->isSoaJitRmw())") : port.index(
                "SoA/JIT RMW supports only ADD/MIN/MAX"
            )
        ]
    )


def test_fixed_bounded_overlap_and_predicate_storage():
    header = read("src/mem/MAA/IndirectAccess.hh")
    begin = header.index("SoaPredicateMaxLines")
    end = header.index("int my_dst_tile", begin)
    soa_state = header[begin:end]
    assert "SoaPredicateMaxLines = 16" in soa_state
    assert "std::array<uint8_t, SoaPredicateLineDataBytes> data" in soa_state
    assert "std::array<SoaPredicateLine, SoaPredicateMaxLines>" in soa_state
    assert "SoaPredicateFeederStateBytes" in soa_state
    assert "std::array<uint8_t, 64> aLine" in soa_state
    assert "sizeof(SoaJitContext) <= 512" in soa_state
    assert "SoaJitValueCoalescer::MaxLookahead> lookahead" in soa_state
    assert "SoaJitValueCoalescer::MaxContexts" in soa_state
    assert "std::array<SoaJitContext, SoaJitContexts>" in soa_state
    assert (
        read("src/mem/MAA/SoaJitOverlapState.hh").count("MaxContexts = 64")
        == 2
    )
    assert "std::vector" not in soa_state
    assert "4096" not in soa_state


def test_bounded_epoch_and_timed_jit_protocol_have_exact_drain():
    source = read("src/mem/MAA/IndirectAccess.cc")
    assert "my_max > offset_table->capacity()" not in source
    assert "SoA/JIT RMW requires one full logical" not in source
    assert "SoA/JIT RMW does not admit range passes" in source
    assert "rememberSoaJitPressureRetry(logical_itr" in source
    assert "commitSoaJitSourceOrdinal(logical_itr, condition_taken)" in source
    assert "soa_jit_epoch_drained = true" in source
    assert "soa_jit_all_rows_claimed = true" in source
    assert "IND_SoaJitEpochDrains" in source
    assert "!descriptor_spool_operation && !isSoaJitRmw()" in source
    assert "descriptor_spool_operation || isSoaJitRmw()" in source
    assert "createDirectIndexReadPacket" in source
    assert "createSoaPredicateReadPacket" in source
    assert "createSoaJitReadPacket" in source
    assert "MemCmd::ReadExReq" in source
    assert "MemCmd::WriteReq" in source
    assert "AwaitAWriteResp" in source
    assert "completeSoaJitWrite" in source
    terminal = source[
        source.index("checkSoaJitTerminal") : source.index(
            "executeInstruction", source.index("checkSoaJitTerminal")
        )
    ]
    for invariant in (
        "soaJitContextsEmpty",
        "soa_jit_epoch_drained",
        "soa_jit_retry_valid",
        "soa_jit_next_source_ordinal",
        "offset_table->occupancy() != 0",
        "soa_jit_selected + soa_jit_predicate_rejected",
        "soa_jit_value_read_issues !=\n"
        "                     soa_jit_value_read_responses",
        "soa_jit_lookahead_issues != soa_jit_selected",
        "soa_jit_value_deliveries != soa_jit_selected",
        "soa_jit_a_write_issues != soa_jit_a_write_responses",
        "soa_jit_write_retirement.empty()",
        "soa_jit_write_retirement.issueCount() !=",
        "soa_jit_write_retirement.responseCount() !=",
        "soa_jit_predicate_line_issues !=",
        "soa_jit_predicate_line_hits != expected_predicate_uses",
        "soa_jit_predicate_uses != expected_predicate_uses",
        "soa_jit_predicate_feeder_high_water >",
    ):
        assert invariant in terminal


def test_default_off_compact_write_retirement_transfers_exact_ownership():
    header = read("src/mem/MAA/SoaJitWriteRetirement.hh")
    indirect = read("src/mem/MAA/IndirectAccess.cc")
    simobject = read("src/mem/MAA/MAA.py")
    options = read("configs/common/Options.py")
    config = read("configs/common/MAAConfig.py")

    assert (
        "soa_jit_compact_write_retirement = Param.Bool(\n        False,"
        in simobject
    )
    assert '"--maa_soa_jit_compact_write_retirement"' in options
    assert 'opts["soa_jit_compact_write_retirement"]' in config
    assert "Credits = 8" in header
    assert "PersistentStateBits" in header
    assert "MaxTransientPacketPayloadBytes" in header

    issue = indirect[
        indirect.index(
            "bool IndirectAccessUnit::issueSoaJitWrite"
        ) : indirect.index("bool IndirectAccessUnit::receiveSoaJitData")
    ]
    reserve = issue.index("soa_jit_write_retirement.reserve")
    send = issue.index("maa->sendPacket")
    commit = issue.index("soa_jit_write_retirement.commit")
    clear = issue.index("context = SoaJitContext()", commit)
    assert reserve < send < commit < clear
    assert "soa_jit_write_retirement_stalls++" in issue
    assert "scheduleExecuteInstructionEvent(1)" in issue
    assert "sender_state_mapping=credit_tag_indexes_tracker_" in indirect
    assert "duplicated_identity_is_validation_metadata" in indirect
    assert "installed_persistent_tracker_bits=" in indirect
    assert "validation_counter_bits=0" in indirect
    assert "response_delivery=reliable_exactly_once_" in indirect
    assert "transient_packet_payload_hwm_bytes=" in indirect


def test_two_observational_terminal_checks_precede_one_ownership_finish():
    source = read("src/mem/MAA/IndirectAccess.cc")
    terminal = source[
        source.index(
            "void IndirectAccessUnit::checkSoaJitTerminal()"
        ) : source.index("void IndirectAccessUnit::executeInstruction()")
    ]
    assert "soa_jit_write_retirement.finish()" not in terminal
    assert "soa_jit_value_coalescer.clearGeneration" not in terminal
    assert "soa_jit_value_coalescer.canClearGeneration" in terminal
    assert source.count("checkSoaJitTerminal();") == 2

    response = source[source.index("case Status::Response:") :]
    second_check = response.index("checkSoaJitTerminal();")
    publish = response.index("IND_SoaJitTerminalCompletions", second_check)
    clear = response.index("soa_jit_value_coalescer.clearGeneration", publish)
    finish = response.index("soa_jit_write_retirement.finish()", clear)
    idle = response.index("state = Status::Idle;", finish)
    assert second_check < publish < clear < finish < idle
    assert response.count("soa_jit_write_retirement.finish()") == 1


def test_pressure_epoch_refills_same_cursor_without_closing_old_result():
    source = read("src/mem/MAA/IndirectAccess.cc")
    build = source[
        source.index(
            "bool IndirectAccessUnit::serviceSoaJitBuild()"
        ) : source.index("IndirectAccessUnit::issueSoaJitScalar")
    ]
    assert "!my_fill_finished" not in build[: build.index("auto context")]
    final = build.index("if (my_fill_finished)")
    all_rows = build.index("soa_jit_all_rows_claimed = true", final)
    epoch = build.index("soa_jit_epoch_drained = true", all_rows)
    assert final < all_rows < epoch

    request = source[
        source.index("case Status::Request:") : source.index(
            "if (usesBoundedSourceResponses())",
            source.index("case Status::Request:"),
        )
    ]
    contexts = request.index("if (!soaJitContextsEmpty())")
    boundary = request.index("if (soa_jit_epoch_drained)", contexts)
    reset = request.index("resetSoaJitEpochTables()", boundary)
    close_reorder = request.index("closeReorderSurvivalEpoch(false)", reset)
    refill = request.index('"soa_epoch_refill"', close_reorder)
    old_result = request.index("closeSelection", refill)
    assert contexts < boundary < reset < close_reorder < refill < old_result
    epoch_reset = source[
        source.index(
            "void\nIndirectAccessUnit::resetSoaJitEpochTables()"
        ) : source.index("bool IndirectAccessUnit::serviceSoaJitBuild()")
    ]
    assert "soa_jit_epoch_resume_i != my_i" in epoch_reset
    assert "offset_table->occupancy() != 0" in epoch_reset
    assert "offset_table->check_reset()" in epoch_reset
    assert "RT[my_RT_config][slice].check_reset()" in epoch_reset
    assert "soa_jit_old_result_selection_closed" in request[boundary:refill]


def test_multiple_pressure_epochs_preserve_exact_source_ordinals():
    logical = 37
    epoch_capacity = 5
    cursor = 0
    next_source = 0
    drains = 0
    committed = []
    old_results = []
    values = list(range(logical))
    while cursor < logical:
        epoch_end = min(cursor + epoch_capacity, logical)
        while cursor < epoch_end:
            assert cursor == next_source
            committed.append(cursor)
            old_results.append(values[cursor])
            values[cursor] += 1
            cursor += 1
            next_source += 1
        if cursor != logical:
            resume = cursor
            drains += 1
            assert cursor == resume == next_source
    assert drains > 1
    assert committed == list(range(logical))
    assert old_results == list(range(logical))


def test_scalar_no_result_keeps_epoch_path_and_no_old_result_closure():
    source = read("src/mem/MAA/IndirectAccess.cc")
    assert "isSoaJitScalarRmw()" in source
    assert "if (!isSoaJitOldResultRmw())\n        return false;" in source
    summary = source[source.index("event=soa_jit_epoch_summary") :]
    assert "scalar=%d" in summary
    assert "isSoaJitScalarRmw()" in summary


def test_old_result_selection_closes_after_context_drain_before_partial_publish():
    source = read("src/mem/MAA/IndirectAccess.cc")
    build = source[
        source.index(
            "bool IndirectAccessUnit::serviceSoaJitBuild()"
        ) : source.index("IndirectAccessUnit::issueSoaJitScalar")
    ]
    assert "closeSelection" not in build

    request = source[
        source.index("case Status::Request:") : source.index(
            "if (usesBoundedSourceResponses())",
            source.index("case Status::Request:"),
        )
    ]
    context_drain = request.index("if (!soaJitContextsEmpty())")
    rows_claimed = request.index(
        "if (!soa_jit_all_rows_claimed)", context_drain
    )
    old_result = request.index("if (isSoaJitOldResultRmw())", rows_claimed)
    close = request.index("closeSelection", old_result)
    partial_publish = request.index("SoaJitOldResultWriteMode::Drain", close)
    assert context_drain < rows_claimed < old_result < close < partial_publish
    assert request.count("closeSelection") == 1
    assert "!soa_jit_old_result_selection_closed" in request[old_result:close]
    assert (
        "soa_jit_old_result_selection_closed = true"
        in request[close:partial_publish]
    )


def test_all_soa_buffers_have_exact_disjoint_physical_cache_lines():
    source = read("src/mem/MAA/IndirectAccess.cc")
    begin = source.index("validateSoaJitAddressSpans")
    validator = source[begin : source.index("ensureSoaPredicate", begin)]
    assert "SoA/JIT byte spans overlap" in validator
    assert "std::map<Addr, PhysicalLineOwner> physical_lines" in validator
    assert "physical_lines.emplace" in validator
    assert "SoA/JIT physical cache-line alias within" in validator
    assert "SoA/JIT physical cache-line alias across" in validator
    assert "paddr != expected_paddr" not in validator
    assert "non-contiguous physical" not in validator
    assert "simulator legality check, not" in validator
    assert "modeled hardware latency or state" in validator
    assert "absent from simulated time" in validator
    decode = source[source.index("case Status::Decode") :]
    assert decode.index("validateSoaJitAddressSpans();") < decode.index(
        "soa_jit_generation = soa_jit_next_generation++"
    )


def test_api_duplicate_predicate_order_and_generation():
    test = read("benchmarks/API/test_hybrid_rmw_soa.cpp")
    assert "kLogical = 16384" in test
    assert "kOperations = 2" in test
    assert "16777216.0F, 1.0F, -16777216.0F, 1.0F" in test
    assert "kFalsePredicateIndex" in test
    assert "predicates[operation][i] = 0" in test
    assert "bits(actual[word]) == bits(expected[word])" in test
    assert "maa_indirect_rmw_vector_soa_jit<float>" in test
    assert "wait_ready(completion_tile)" in test


def test_four_arm_runner_is_matched_for_soa_physical_pair():
    runner = read("experiments/scripts/run_hybrid_rmw_soa_matrix.sh")
    arms = re.findall(r"run_arm ([a-z0-9_]+) ", runner)
    assert arms == [
        "ordinary_native16",
        "ordinary_native4",
        "soa_metadata16_physical16",
        "soa_metadata16_physical4",
    ]
    assert '--maa_num_offset_table_entries="$logical"' in runner
    assert '--maa_num_offset_table_epoch_entries="$logical"' in runner
    assert '--maa_physical_tile_elements="$physical"' in runner
    assert "soa_metadata16_physical16" in runner
    assert "soa_metadata16_physical4" in runner
    assert "IND_SoaJitTerminalCompletions" in runner
    assert "IND_SoaJitPredicateLineResponses" in runner
    assert "IND_SoaJitContextHighWater" in runner
    assert "IND_SoaJitContextStalls" in runner
    assert "$context_high_water -eq 2 && $context_stalls -eq 0" in runner
    assert "soa_physical_spd_geometry_ratio" in runner
    assert "soa_metadata_virtualization_ratio" not in runner


def test_overlap_runner_has_explicit_serial_and_optimized_treatments():
    runner = read("experiments/scripts/run_hybrid_rmw_soa_overlap_matrix.sh")
    assert '--maa_soa_jit_active_contexts="$contexts"' in runner
    assert '--maa_soa_jit_value_lookahead="$lookahead"' in runner
    assert "run_native ordinary_native16" in runner
    assert "run_native ordinary_native4" in runner
    assert "run_soa soa_serial_physical16 16384 1 1 1 0 4 1" in runner
    assert '--maa_soa_jit_active_value_owners="$owners"' in runner
    assert "run_soa baseline_c1_i1_l1_v4 4096 1 1 1 0 4 1" in runner
    assert "run_soa lookahead4_c1_i8_l4_v4 4096 1 8 4 1 4 1" in runner
    assert "run_soa lookahead8_c1_i8_l8_v4 4096 1 8 8 1 4 1" in runner
    assert "run_soa combined_c8_i8_l8_v4 4096 8 8 8 1 4 1" in runner
    for owners in (8, 16, 32):
        assert (
            f"run_soa combined_c8_i8_l8_v{owners} 4096 8 8 8 1 {owners} 1"
            in runner
        )
    assert "run_soa apply2_c8_i8_l8_v32 4096 8 8 8 1 32 2" in runner
    assert "run_soa apply4_c8_i8_l8_v32 4096 8 8 8 1 32 4" in runner
    assert "fixed_context_slots=32" in runner
    assert "fixed_lookahead_slots_per_context=8" in runner
    assert "fixed_value_owner_pool_lines=128" in runner
    assert "fixed_apply_lanes=4" in runner
    assert "default_active_apply_lanes=1" in runner
    assert '--maa_soa_jit_apply_lanes="$lanes"' in runner
    assert "IND_SoaJitActiveApplyLanes" in runner
    assert "IND_SoaJitApplyLaneHighWater" in runner
    assert "did not exercise independent same-cycle apply lanes" in runner
    assert "IND_SoaJitValueFills" in runner
    assert "IND_SoaJitValueMergedWaiters" in runner
    assert "IND_SoaJitLookaheadResponses" in runner
    assert "IND_SoaJitTerminalCompletions" in runner
    assert "event=soa_jit_storage" in runner
    assert "storage_ledger.txt" in runner


def test_storage_ledger_separates_fixed_provision_from_active_knobs():
    source = read("src/mem/MAA/IndirectAccess.cc")
    storage = source[source.index('"event=soa_jit_storage') :]
    for field in (
        "fixed_context_bytes",
        "fixed_contexts_bytes",
        "max_physical_value_owner_lines",
        "fixed_value_owner_bytes",
        "fixed_value_owner_entry_bytes",
        "fixed_value_owner_payload_bytes",
        "fixed_value_owner_nonpayload_bytes",
        "baseline_32_value_owner_bytes",
        "incremental_value_owner_bytes_vs_32_per_unit",
        "fixed_value_owner_bytes_per_maa",
        "incremental_value_owner_bytes_vs_32_per_maa",
        "fixed_apply_lane_owner_bytes",
        "fixed_apply_lane_pool_bytes",
        "fixed_predicate_lines",
        "fixed_predicate_modeled_bytes",
        "fixed_predicate_host_bytes",
        "baseline_predicate_lines",
        "incremental_predicate_modeled_bytes",
        "predicate_active_credits",
        "index_active_data_tag_bytes",
        "incremental_overlap_bytes",
        "active_contexts",
        "active_lookahead",
        "active_value_owners",
        "active_value_owner_payload_bytes",
        "selected_value_owner_entry_bytes_per_unit",
        "selected_value_owner_entry_bytes_per_maa",
        "active_apply_lanes",
        "active_apply_lane_hwm",
        "fixed_prefetch_credits",
        "fixed_prefetch_credit_bytes",
        "active_prefetch_credits",
        "fixed_prefetch_cursor_bytes",
    ):
        assert field in storage


def test_value_owner_pool_plumbing_restricts_runtime_selection():
    state = read("src/mem/MAA/SoaJitOverlapState.hh")
    source = read("src/mem/MAA/IndirectAccess.cc")
    options = read("configs/common/Options.py")
    simobject = read("src/mem/MAA/MAA.py")
    config = read("configs/common/MAAConfig.py")
    assert "BaselineOwners = 32" in state
    assert "MaxOwners = 128" in state
    assert "count == 4 || count == 8 || count == 16 || count == 32 ||" in state
    assert "count == 64 || count == 96 || count == 128" in state
    assert "isValidActiveOwnerCount(size_t count)" in state
    assert "size_t active_owners = 4" in state
    assert "activeOwnerLines" in state
    assert "32, 64, 96, or 128\\n" in source
    assert '"--maa_soa_jit_active_value_owners"' in options
    assert "choices=(4, 8, 16, 32, 64, 96, 128)" in options
    assert "soa_jit_active_value_owners = Param.Unsigned" in simobject
    assert 'opts["soa_jit_active_value_owners"]' in config


def test_sequential_value_prefetch_is_disabled_bounded_and_exactly_owned():
    state = read("src/mem/MAA/SoaJitOverlapState.hh")
    header = read("src/mem/MAA/IndirectAccess.hh")
    source = read("src/mem/MAA/IndirectAccess.cc")
    options = read("configs/common/Options.py")
    simobject = read("src/mem/MAA/MAA.py")
    config = read("configs/common/MAAConfig.py")

    assert "MaxPrefetchCredits = 8" in state
    assert "count == 0 || count == 1 || count == 2 || count == 4" in state
    assert "uint64_t vaddr = 0" in state
    assert "uint64_t paddr = 0" in state
    assert "credit.vaddr == vaddr" in state
    assert "first >= activePrefetchCredits && credit.valid" in state
    assert "SoaJitValuePrefetchCursor" in header
    assert "SoaJitValuePrefetchMaxScans" in header
    assert "sizeof(SoaJitValuePrefetchCursor) <= 16" in header

    assert '"--maa_soa_jit_value_prefetch_credits"' in options
    knob = options[options.index('"--maa_soa_jit_value_prefetch_credits"') :]
    assert "default=0" in knob
    assert "choices=(0, 1, 2, 4, 8)" in knob
    assert "soa_jit_value_prefetch_credits = Param.Unsigned" in simobject
    param = simobject[
        simobject.index("soa_jit_value_prefetch_credits = Param.Unsigned") :
    ]
    assert "0," in param
    assert 'opts["soa_jit_value_prefetch_credits"]' in config

    assert "serviceSoaJitValuePrefetch" in source
    assert source.count("soa_jit_value_coalescer.configure(") == 2
    assert (
        source.count(
            "soa_jit_value_cache_enable, soa_jit_value_prefetch_credits"
        )
        == 2
    )
    assert "translatePacket(block_vaddr)" in source
    assert "reservePrefetch" in source
    assert "createSoaJitReadPacket(block_paddr" in source
    assert "PrefetchPromote" in source
    assert "PrefetchDiscard" in source
    assert "soaJitValuePrefetchComplete()" in source
    assert "soa_jit_value_prefetch_issues !=" in source
    assert "soa_jit_value_prefetch_promotions +" in source
    for counter in (
        "IND_SoaJitValuePrefetchIssues",
        "IND_SoaJitValuePrefetchResponses",
        "IND_SoaJitValuePrefetchPromotions",
        "IND_SoaJitValuePrefetchDiscards",
        "IND_SoaJitValuePrefetchOwned",
        "IND_SoaJitValuePrefetchCreditStalls",
        "IND_SoaJitValuePrefetchActiveCredits",
        "IND_SoaJitValuePrefetchHighWater",
    ):
        assert counter in read("src/mem/MAA/MAA.hh")
        assert counter in read("src/mem/MAA/MAA.cc")


def test_context_pool_is_fixed_64_with_default_off_pipeline_choice_and_storage():
    state = read("src/mem/MAA/SoaJitOverlapState.hh")
    source = read("src/mem/MAA/IndirectAccess.cc")
    options = read("configs/common/Options.py")
    simobject = read("src/mem/MAA/MAA.py")

    assert state.count("MaxContexts = 64") == 2
    assert "MaxWaiters = MaxContexts * MaxLookahead" in state
    assert "std::bitset<MaxWaiters> waiterMask" in state
    assert "uint16_t waiter" in state
    assert "_soa_jit_active_contexts != 8" in source
    assert "_soa_jit_active_contexts != 16" in source
    assert "_soa_jit_active_contexts != 32" in source
    assert "_soa_jit_active_contexts != 64" in source
    assert "fixed_contexts=%lu" in source
    assert "active_contexts_bytes=%lu" in source
    assert "choices=(8, 16, 32, 64)" in options
    assert (
        "default=8"
        in options[options.index('"--maa_soa_jit_active_contexts"') :]
    )
    assert "fixed maximum hardware is 64" in simobject


def test_apply_lane_pool_is_fixed_owned_ordered_and_fail_closed():
    state = read("src/mem/MAA/SoaJitOverlapState.hh")
    source = read("src/mem/MAA/IndirectAccess.cc")
    options = read("configs/common/Options.py")
    simobject = read("src/mem/MAA/MAA.py")
    config = read("configs/common/MAAConfig.py")

    assert "class SoaJitApplyLanePool" in state
    assert "MaxLanes = 4" in state
    assert "count == 1 || count == 2 || count == 4" in state
    assert "std::array<Owner, MaxLanes> owners" in state
    assert "owners[lane].context == context" in state
    assert "owners[lane].aPaddr == a_paddr" in state
    assert "deliveriesThisCycle >= max_deliveries" in state

    ordered = source[source.index("const size_t apply_start") :]
    assert ordered.index(
        "candidate.offset == context.nextOffset"
    ) < ordered.index("soa_jit_apply_lane_pool.grant")
    assert ordered.index("soa_jit_apply_lane_pool.grant") < ordered.index(
        "offset_table->consume_entry(context.nextOffset)"
    )
    assert "!soa_jit_apply_lane_pool.assertInvariants()" in source
    assert "AwaitAWriteResp" in source

    assert '"--maa_soa_jit_apply_lanes"' in options
    assert (
        "default=1" in options[options.index('"--maa_soa_jit_apply_lanes"') :]
    )
    assert "choices=(1, 2, 4)" in options
    assert "soa_jit_apply_lanes = Param.Unsigned" in simobject
    assert 'opts["soa_jit_apply_lanes"]' in config
