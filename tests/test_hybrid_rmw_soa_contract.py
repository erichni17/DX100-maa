import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def test_guarded_word_five_abi_preserves_instruction_file_size():
    api = read("benchmarks/API/MAA_gem5.hpp")
    port = read("src/mem/MAA/CpuSidePort.cc")
    assert "#define INSTRUCTION_FILE_SIZE 64" in api
    assert "INSTRUCTION_FILE_SIZE - 6 * sizeof(uint64_t)" in api
    assert "*INSTR_backingaddr = (uint64_t)values" in api
    assert "*INSTR_indexaddr = (uint64_t)indices" in api
    assert "*INSTR_predicateaddr = (uint64_t)predicates" in api
    word_five = port[
        port.index("case 5:") : port.index("default:", port.index("case 5:"))
    ]
    assert "isSoaJitRmw" in word_five
    assert "hasValidSoaJitRmwOperands" in word_five
    assert "scheduleDispatchInstructionEvent" in word_five


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
    assert "MaxContexts = 32" in read("src/mem/MAA/SoaJitOverlapState.hh")
    assert "std::vector" not in soa_state
    assert "4096" not in soa_state


def test_full_window_and_timed_jit_protocol_have_exact_drain():
    source = read("src/mem/MAA/IndirectAccess.cc")
    assert "my_max > offset_table->capacity()" in source
    epoch_bound = (
        "my_max > static_cast<int>(\n"
        "                                          "
        "maa->num_offset_table_epoch_entries)"
    )
    assert epoch_bound in source
    assert "SoA/JIT RMW requires one full logical" in source
    assert "SoA/JIT RMW does not admit range passes" in source
    assert "panic_if(isSoaJitRmw() && needDrain" in source
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
        "offset_table->occupancy() != 0",
        "soa_jit_selected + soa_jit_predicate_rejected",
        "soa_jit_value_read_issues !=\n"
        "                     soa_jit_value_read_responses",
        "soa_jit_lookahead_issues != soa_jit_selected",
        "soa_jit_value_deliveries != soa_jit_selected",
        "soa_jit_a_write_issues != soa_jit_a_write_responses",
        "soa_jit_predicate_line_issues !=",
        "soa_jit_predicate_line_hits != expected_predicate_uses",
        "soa_jit_predicate_uses != expected_predicate_uses",
        "soa_jit_predicate_feeder_high_water >",
    ):
        assert invariant in terminal


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
    assert "count == 64 || count == 128" in state
    assert "isValidActiveOwnerCount(size_t count)" in state
    assert "size_t active_owners = 4" in state
    assert "activeOwnerLines" in state
    assert "32, 64, or 128\\n" in source
    assert '"--maa_soa_jit_active_value_owners"' in options
    assert "choices=(4, 8, 16, 32, 64, 128)" in options
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


def test_context_pool_is_fixed_32_with_exact_runtime_choices_and_storage():
    state = read("src/mem/MAA/SoaJitOverlapState.hh")
    source = read("src/mem/MAA/IndirectAccess.cc")
    options = read("configs/common/Options.py")
    simobject = read("src/mem/MAA/MAA.py")

    assert "MaxContexts = 32" in state
    assert "MaxWaiters = MaxContexts * MaxLookahead" in state
    assert "std::bitset<MaxWaiters> waiterMask" in state
    assert "uint16_t waiter" in state
    assert "_soa_jit_active_contexts != 8" in source
    assert "_soa_jit_active_contexts != 16" in source
    assert "_soa_jit_active_contexts != 32" in source
    assert "fixed_contexts=%lu" in source
    assert "active_contexts_bytes=%lu" in source
    assert "choices=(8, 16, 32)" in options
    assert (
        "default=8"
        in options[options.index('"--maa_soa_jit_active_contexts"') :]
    )
    assert "hardware is 32" in simobject


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
