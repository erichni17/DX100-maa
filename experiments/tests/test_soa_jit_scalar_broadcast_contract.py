from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def block(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    finish = text.index(end, begin)
    return text[begin:finish]


def test_guarded_scalar_abi_and_legacy_wire_image_are_disjoint():
    api = (ROOT / "benchmarks/API/MAA_gem5.hpp").read_text()
    guarded = block(
        api,
        "inline void maa_indirect_rmw_scalar_soa_jit(",
        "inline void maa_indirect_rmw_scalar(",
    )
    legacy = block(
        api,
        "inline void maa_indirect_rmw_scalar(",
        "// for each tile of i",
    )
    vector = block(
        api,
        "inline void maa_indirect_rmw_vector_soa_jit(",
        "inline void maa_indirect_rmw_vector_soa_jit_masked_indices(",
    )
    assert "OpcodeType::INDIR_RMW_SCALAR" in guarded
    assert "*INSTR_backingaddr = (uint64_t)scalar_reg" in guarded
    assert "old_value_tile == -1" in guarded
    assert "OpcodeType::INDIR_RMW_SCALAR" in legacy
    assert "*INSTR_backingaddr" not in legacy
    assert "OpcodeType::INDIR_RMW_VECTOR" in vector
    assert "*INSTR_backingaddr = (uint64_t)values" in vector


def test_live_engine_reuses_bounded_contexts_and_omits_value_reads():
    header = (ROOT / "src/mem/MAA/IndirectAccess.hh").read_text()
    source = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
    helper = (ROOT / "src/mem/MAA/SoaJitScalarBroadcast.hh").read_text()
    assert "SoaJitScalarBroadcast soa_jit_scalar_broadcast" in header
    assert "std::array<SoaJitContext, SoaJitContexts>" in header
    assert "issueSoaJitScalar" in source
    assert "isSoaJitScalarRmw()\n            ? issueSoaJitScalar" in source
    assert "soa_jit_scalar_broadcast.apply(destination)" in source
    assert "SoaJitWriteSenderState" in header
    assert "rejected stale/unmatched WriteResp" in source
    assert "FixedPayloadBytes = MaxValueBytes" in helper
    assert "std::vector" not in helper
    assert "std::map" not in helper


def test_spans_aliases_predicates_and_completion_are_fail_closed():
    cpu = (ROOT / "src/mem/MAA/CpuSidePort.cc").read_text()
    indirect = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
    interface = (ROOT / "src/mem/MAA/IF.cc").read_text()
    assert "validateRegisters(" in cpu
    assert "scalarOperandsAligned(" in cpu
    assert "validateSoaJitAddressSpans();" in indirect
    assert "if (!isSoaJitScalarRmw())" in indirect
    assert "if (isSoaJitVectorRmw())" in interface
    assert "completion_only_tiles" in interface


def test_exact_dual_generation_geometry_and_counters_are_exercised():
    test = (ROOT / "tests/maa/soa_jit_scalar_broadcast_test.cc").read_text()
    runner = (
        ROOT / "experiments/scripts/run_soa_jit_scalar_broadcast_unit.sh"
    ).read_text()
    for evidence in (
        "LogicalElements = 16 * 1024",
        "runGeneration<float>(LogicalElements, 101",
        "runGeneration<float>(4096, 101",
        "runGeneration<int32_t>(LogicalElements, 102",
        "runGeneration<int32_t>(4096, 102",
        "aliasesApplied == 12655",
        "valueReadIssues == 0",
        "Status::StaleCompletion",
    ):
        assert evidence in test
    assert "optimized sanitize" in runner
    assert "-fsanitize=address,undefined" in runner


def test_focused_live_guest_uses_two_scalar_generations_only():
    guest = (
        ROOT / "benchmarks/API/test_hybrid_rmw_scalar_soa.cpp"
    ).read_text()
    assert guest.count("maa_indirect_rmw_scalar_soa_jit<") == 2
    assert "maa_indirect_rmw_scalar_soa_jit<float>" in guest
    assert "maa_indirect_rmw_scalar_soa_jit<int32_t>" in guest
    assert guest.count("wait_ready(completion)") == 2
    assert "generations=2" in guest
    assert "maa_indirect_rmw_vector_soa_jit" not in guest
