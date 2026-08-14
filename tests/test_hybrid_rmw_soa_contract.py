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


def test_fixed_bounded_overlap_storage_and_no_operation_sized_payload():
    header = read("src/mem/MAA/IndirectAccess.hh")
    begin = header.index("struct SoaPredicateLine")
    end = header.index("int my_dst_tile", begin)
    soa_state = header[begin:end]
    assert "std::array<uint8_t, 64> data" in soa_state
    assert "std::array<uint8_t, 64> aLine" in soa_state
    assert "sizeof(SoaJitContext) <= 512" in soa_state
    assert "SoaJitValueCoalescer::MaxLookahead> lookahead" in soa_state
    assert "SoaJitValueCoalescer::MaxContexts" in soa_state
    assert "std::array<SoaJitContext, SoaJitContexts>" in soa_state
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
    ):
        assert invariant in terminal


def test_all_soa_buffers_have_disjoint_physical_routing_spans():
    source = read("src/mem/MAA/IndirectAccess.cc")
    begin = source.index("validateSoaJitAddressSpans")
    validator = source[begin : source.index("ensureSoaPredicate", begin)]
    assert "SoA/JIT byte spans overlap" in validator
    assert "non-contiguous physical" in validator
    assert "SoA/JIT physical routing spans overlap" in validator
    assert "simulator legality check, not" in validator
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
    assert "run_soa soa_serial_physical16 16384 1 1 1 0" in runner
    assert "run_soa baseline_c1_i1_l1_v0 4096 1 1 1 0" in runner
    assert "run_soa lookahead4_c1_i8_l4_v4 4096 1 8 4 1" in runner
    assert "run_soa lookahead8_c1_i8_l8_v4 4096 1 8 8 1" in runner
    assert "run_soa combined_c8_i8_l8_v4 4096 8 8 8 1" in runner
    assert "fixed_context_slots=8" in runner
    assert "fixed_lookahead_slots_per_context=8" in runner
    assert "fixed_value_cache_lines=4" in runner
    assert "fixed_apply_lanes=1" in runner
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
        "fixed_value_owner_bytes",
        "fixed_apply_arbiter_bytes",
        "existing_predicate_feeder_bytes",
        "index_active_data_tag_bytes",
        "incremental_overlap_bytes",
        "active_contexts",
        "active_lookahead",
    ):
        assert field in storage
