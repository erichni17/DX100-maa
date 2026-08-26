import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def test_optimized_and_sanitized_full_16k_state_model():
    subprocess.run(
        [str(ROOT / "tests/maa/run_fused_p16_product_state_unit.sh")],
        cwd=ROOT,
        check=True,
    )


def test_guarded_six_word_abi_and_registered_p_region_hazards():
    api = read("benchmarks/API/MAA_gem5.hpp")
    decode = read("src/mem/MAA/CpuSidePort.cc")
    interface = read("src/mem/MAA/IF.cc")
    header = read("src/mem/MAA/IF.hh")
    assert "maa_indirect_load_virtual_index_product_fp32" in api
    assert "Operation_t::MUL_OP << 16" in api
    assert "*INSTR_predicateaddr = (uint64_t)coefficients" in api
    assert "isFusedP16ProductCandidate" in header
    assert "datatype == DataType::FLOAT32_TYPE" in header
    assert "optype == OPType::MUL_OP" in header
    assert "fusedP16CoefficientWordReceived" in header
    assert "Malformed fused-p16 coefficient closure" in decode
    assert "registered p region and exact 16K" in decode
    assert "registeredWordFits" in decode
    assert "registeredRegionsDisjoint" in decode
    fused_accesses = interface.split("if (isFusedP16Product())", 1)[1]
    for access in (
        "append(addrRangeID, AccessType::READ)",
        "append(indexAddrRangeID, AccessType::READ)",
        "append(predicateAddrRangeID, AccessType::READ)",
        "append(backingAddrRangeID, AccessType::WRITE)",
    ):
        assert access in fused_accesses


def test_exact_finite_geometry_and_no_drain_or_fallback():
    contract = read("src/mem/MAA/FusedP16ProductState.hh")
    indirect = read("src/mem/MAA/IndirectAccess.cc")
    for token in (
        "LogicalElements = 16 * 1024",
        "ResponseSlots = 8",
        "CombinerSlots = 16",
        "CombinerWays = 4",
        "CombinerBanks = 4",
        "WordsPerCycle = 1",
        "OutstandingWrites = 32",
        "CoefficientOwnerLines = 32",
        "CoefficientPrefetchCredits = 0",
        "GuestBackingBytesRemoved =",
        "LifecycleSemanticBytesPerUnit",
        "LifecycleCppBoundBytesPerUnit",
        "DescriptorClosureSemanticBytesPerIf",
        "DescriptorClosureCppBoundBytesPerIf",
        "registeredWordFits",
        "registeredRegionsDisjoint",
    ):
        assert token in contract
    assert "num_RT_slices[my_RT_config] != 32" in indirect
    assert "virtual_response_words != 0" in indirect
    assert "virtual_index_range_passes" in indirect
    assert "epoch drain/fallback is forbidden" in indirect
    assert (
        "global_fallbacks=0 hidden_spill_bytes=0 publisher_lines=0" in indirect
    )


def test_tagged_one_cycle_ordinary_alu_and_in_place_product():
    state = read("src/mem/MAA/FusedP16ProductState.hh")
    indirect = read("src/mem/MAA/IndirectAccess.cc")
    alu = read("src/mem/MAA/ALU.cc")
    maa = read("src/mem/MAA/MAA.cc")
    for token in (
        "NeedCoefficient",
        "AwaitCoefficient",
        "AwaitMultiply",
        "ProductReady",
        "FusedP16AluToken",
    ):
        assert token in state
    assert "virtual_response_line_payloads.lineData(slot_idx)" in indirect
    assert "startFusedP16ProductALU" in indirect
    assert "insertVirtualCombineWord(entry.itr, product)" in indirect
    assert "offset_table->consume_entry(slot.next_itr)" in indirect
    assert "startDirectPair" in alu
    assert "source *= coefficient" in alu
    assert "scheduleExecuteInstructionEvent(ALU_lane_latency)" in alu
    assert "completeFusedP16ProductALU" in maa
    # The retained SoA apply lanes remain ADD/MIN/MAX only.
    apply = indirect.split("#define APPLY_SOA_JIT(TYPE)", 1)[1].split(
        "#undef APPLY_SOA_JIT", 1
    )[0]
    assert "MUL_OP" not in apply


def test_candidate_removes_virtual_p_and_product_publisher():
    cg = read("benchmarks/NAS/cg/cg.cpp")
    helper = cg.split("cg_fused_p16_product(", 1)[1].split(
        "static void\ncg_page_fed_q16_open", 1
    )[0]
    assert 'return "fused_p16_product_q16"' in cg
    assert "maa_indirect_load_virtual_index_product_fp32" in helper
    assert "maa_publish_spd_page_logical16_response_bearing" not in helper
    assert "cg_logical_product_words[tid] += TILE_SIZE" in helper
    assert "!cg_uses_fused_p16_product_q16()" in cg
    assert "virtual_gather_storage = nullptr" in cg
    assert '" virtual_p_allocation_bytes="' in cg
    assert '" product_publisher_lines="' in cg
    assert '" hidden_spill_bytes=0 global_fallbacks=0"' in cg
