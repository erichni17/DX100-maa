import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def between(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end, text.index(start))]


def test_guest_encodes_only_inactive_indices_after_rng_initialization():
    source = read("benchmarks/UME/gradzatp.cpp")
    initialization = between(
        source,
        "// Initialize c_to_p_map and c_to_z_map",
        "#if defined(UME_GATHER_VERIFY)",
    )
    assert initialization.index(
        "rand() % (2 * DISTANCE_OTEHRS + 1)"
    ) < initialization.index("encode_and_audit_gzp_masked_indices")
    audit = between(
        source,
        "static void encode_and_audit_gzp_masked_indices",
        "static const char *gzp_rmw_treatment_name",
    )
    assert "if (corner_type[c] < 1)" in audit
    assert "c_to_p_map[c] = -1" in audit
    assert "static_cast<uint32_t>(-1) == UINT32_MAX" in audit
    for field in (
        "active_uint32_max",
        "active_illegal_index",
        "inactive_legal_index",
        "inactive_non_sentinel",
    ):
        assert field in audit
        assert f"ledger.{field} == 0" in audit
    assert '"UME_GZP_MASKED_INDEX_LEDGER result=FAIL"' in audit
    assert '"UME_GZP_MASKED_INDEX_LEDGER result=PASS"' in source


def test_opt_in_arm_uses_masked_api_without_changing_existing_helpers():
    source = read("benchmarks/UME/gradzatp.cpp")
    api = read("benchmarks/API/MAA_gem5.hpp")
    assert 'treatment == "volume_soa_jit"' in source
    assert 'treatment == "volume_masked_index"' in source
    assert 'treatment == "soa_jit"' in source
    volume = between(
        source,
        "if (soa_volume_only_full_window ||",
        "for (int page_offset",
    )
    assert "maa_indirect_rmw_vector_soa_jit_masked_indices" in volume
    assert "maa_indirect_rmw_vector_soa_jit<DATATYPE>" in volume
    assert "corner_volume.data() + c" in volume
    assert "c_to_p_map.data() + c" in volume
    assert "wait_ready(soa_volume_completion_tiles" in volume
    assert "*INSTR_predicateaddr = (uint64_t)predicates" in api
    assert "*INSTR_predicateaddr = MAA_SOA_JIT_MASKED_INDEX_MODE_TAG" in api


def test_masked_treatment_omits_predicate_region_and_reports_cost():
    source = read("benchmarks/UME/gradzatp.cpp")
    registration = between(
        source,
        "// Existing arms retain their original publication registrations",
        'std::cout << "ROI Begin"',
    )
    assert (
        "gzp_rmw_treatment != GzpRmwTreatment::VolumeMaskedIndexSoaJit"
        in registration
    )
    assert "add_mem_region(corner_predicate_soa.data()" in registration
    assert 'return "masked_index_no_predicate_publication"' in source
    assert '<< " predicate_publications="' in source
    masked_call = between(
        source,
        "maa_indirect_rmw_vector_soa_jit_masked_indices",
        "} else {",
    )
    assert "corner_predicate_soa" not in masked_call


def test_runner_requires_same_checkpoint_exact_runtime_ledger(tmp_path: Path):
    runner = read("experiments/scripts/run_gzp_masked_index_pair.py")
    for contract in (
        '"shared_checkpoint": True',
        '"IND_SoaJitSelected"',
        '"IND_SoaJitPredicateRejected"',
        '"IND_SoaJitPredicateLineReads"',
        '"IND_CyclesFill"',
        '"IND_CyclesRequest"',
        '"masked_index_compare_bits"',
        '"predicate_publications_avoided"',
        'predicate_lines_avoided != int(baseline["predicate_lines"])',
        '"incremental_buffer_bytes": 0',
        'EXPECTED_FULL_HASH = "11225737641199706160"',
    ):
        assert contract in runner

    fake = tmp_path / "gem5.opt"
    library = tmp_path / "libramulator.so"
    fake.write_bytes(b"gem5")
    library.write_bytes(b"ramulator")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "experiments/scripts/run_gzp_masked_index_pair.py"),
            "--out",
            str(tmp_path / "out"),
            "--gem5",
            str(fake),
            "--ramulator-library",
            str(library),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    plan = json.loads(result.stdout)
    assert plan["n"] == 16384
    assert plan["arms"] == ["separate_predicate", "masked_index"]
    assert plan["geometry"] == {"logical": 16384, "physical_spd": 4096}


def test_stats_parser_ignores_nonfinite_derived_formulas(tmp_path: Path):
    path = tmp_path / "stats.txt"
    path.write_text(
        "---------- Begin Simulation Statistics ----------\n"
        "simTicks 123\n"
        "system.maa.unrelatedFormula inf\n"
        "system.maa.I0_IND_CyclesFill 17\n"
        "---------- End Simulation Statistics   ----------\n"
    )
    spec = __import__("importlib.util").util.spec_from_file_location(
        "gzp_masked_runner",
        ROOT / "experiments/scripts/run_gzp_masked_index_pair.py",
    )
    module = __import__("importlib.util").util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stats = module.first_stats(path)
    assert stats == {"simTicks": 123, "system.maa.I0_IND_CyclesFill": 17}
