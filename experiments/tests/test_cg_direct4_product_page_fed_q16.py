"""Focused contract tests for the narrow CG direct4-product/q16 pair."""

from __future__ import annotations

import importlib.util
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "benchmarks/NAS/cg/cg.cpp").read_text()
RUNNER_PATH = (
    ROOT / "experiments/scripts/run_cg_direct4_product_page_fed_q16.py"
)
RUNNER_TEXT = RUNNER_PATH.read_text()
SPEC = importlib.util.spec_from_file_location("cg_direct4_runner", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def terminal_fields(treatment: str, windows: int = 3) -> dict[str, str]:
    pages = windows * 4
    words = windows * 16384
    direct = treatment == "direct4_product_page_fed_q16"
    values = {
        "full_windows": windows,
        "staged_index_words": words,
        "staged_value_words": 0,
        "product_words": words,
        "index_publish_pages": 0,
        "value_publish_pages": 0,
        "product_publish_pages": pages,
        "logical_alu_vectors": 0,
        "physical_alu_vectors": pages,
        "logical_page_windows": 0,
        "physical_page_product_windows": 0,
        "page_fed_product_windows": 0 if direct else windows,
        "direct4_product_page_fed_q16_windows": windows if direct else 0,
        "virtual_p_gather_windows": 0 if direct else windows,
        "physical_p_gather_pages": pages if direct else 0,
        "page_fed_admit_pages": pages,
        "page_fed_closes": windows,
        "q_spmv_eligible_windows": 2,
        "q_spmv_routed_windows": 2,
        "residual_spmv_eligible_windows": 1,
        "residual_spmv_routed_windows": 1,
        "external_coherent_backing_bytes": 262144 if direct else 524288,
        "physical_spd_payload_bytes": 524288,
        "logical_scheduler_reserved_lanes": 0,
        "logical_scheduler_reserved_lane_payload_bytes": 0,
        "host_payload_access": 0,
        "coherent_index_backing_bytes": 0,
        "virtual_p_backing_bytes": 0 if direct else 262144,
        "virtual_backing_traffic_eliminated": 1 if direct else 0,
        "p16_reorder_preserved": 0 if direct else 1,
        "q16_reorder_preserved": 1,
    }
    fields = {key: str(value) for key, value in values.items()}
    fields["p_gather_mode"] = "physical_4k_direct" if direct else "virtual_16k"
    return fields


def test_selector_and_eight_tile_build_are_narrow() -> None:
    assert 'return "direct4_product_page_fed_q16";' in SOURCE
    assert runner.CG_NA == 1024
    assert runner.TREATMENTS == (
        ("control", "page_fed_product_soa_jit"),
        ("direct4_q16", "direct4_product_page_fed_q16"),
    )
    assert '"-DNUM_TILES_PER_CORE=8"' in RUNNER_TEXT
    assert '"-DCG_PHYSICAL_PAGE_PRODUCT_ONLY"' in RUNNER_TEXT
    assert '"-DCG_PAGE_FED_SOA_ONLY"' in RUNNER_TEXT
    assert '"native_runs": 0' in RUNNER_TEXT
    assert '"full_or_medium_runs": 0' in RUNNER_TEXT
    assert '"timeout": "none"' in RUNNER_TEXT


def test_direct4_pages_are_physical_and_publish_only_final_products() -> None:
    starts = [
        match.start()
        for match in re.finditer(
            r"if \(logical_page_full_window &&\s+"
            r"cg_uses_direct4_product_page_fed_q16\(\)\)",
            SOURCE,
        )
    ]
    assert len(starts) == 2
    for start in starts:
        end = SOURCE.index("maa_range_loop<int>", start)
        direct_page = SOURCE[start:end]
        assert "maa_stream_load<int>(&colidx[page_base]" in direct_page
        assert "maa_indirect_load<float>" in direct_page
        assert "maa_stream_load<float>(&a[page_base]" in direct_page
        assert "maa_alu_vector<float>" in direct_page
        assert "wait_ready(t7);" in direct_page
        assert "cg_direct4_publish_product_page" in direct_page
        assert "maa_indirect_load_virtual_index" not in direct_page
        assert "virtual_gather_backing_for_thread" not in direct_page


def test_product_responses_close_before_q16_open_and_ordered_admits() -> None:
    q_start = SOURCE.index(
        "// This arm intentionally gives up p-side 16K reorder:"
    )
    q_end = SOURCE.index(
        "#else\n                maa_const(k_base, r2);", q_start
    )
    q_path = SOURCE[q_start:q_end]
    publish = q_path.index("cg_direct4_publish_product_page")
    open_q16 = q_path.index("cg_page_fed_q16_open", publish)
    admit_loop = q_path.index(
        "for (int page_offset = 0; page_offset < TILE_SIZE", open_q16
    )
    admit = q_path.index("cg_page_fed_admit_q_index_page", admit_loop)
    close = q_path.index("cg_page_fed_q16_close", admit)
    assert publish < open_q16 < admit_loop < admit < close
    helper = SOURCE[
        SOURCE.index("cg_direct4_publish_product_page(") : SOURCE.index(
            "cg_page_fed_admit_q_index_page("
        )
    ]
    assert helper.index(
        "maa_publish_spd_page_logical16_response_bearing<float>"
    ) < helper.index("wait_ready(completion_tile);")


def test_terminal_gate_records_the_tradeoff_and_exact_payload() -> None:
    for treatment in (
        "page_fed_product_soa_jit",
        "direct4_product_page_fed_q16",
    ):
        assert (
            runner.require_terminal_8(terminal_fields(treatment), treatment)
            == 3
        )
    bad = terminal_fields("direct4_product_page_fed_q16")
    bad["physical_spd_payload_bytes"] = "655360"
    with unittest.TestCase().assertRaisesRegex(
        RuntimeError, "terminal closure failed"
    ):
        runner.require_terminal_8(bad, "direct4_product_page_fed_q16")


def test_config_gate_rejects_inherited_ten_tiles() -> None:
    common = [
        "page_fed_soa_jit=true",
        "num_maas=1",
        "num_indirect_units_per_maa=4",
        "num_tile_elements=16384",
        "physical_tile_elements=4096",
        "num_offset_table_entries=16384",
        "num_offset_table_epoch_entries=16384",
        "num_initial_row_table_slices=32",
        "soa_jit_predicate_active_credits=16",
        "soa_jit_active_value_owners=32",
        "[system.mem_ctrls0]",
        "[system.mem_ctrls1]",
    ]
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / "config.ini"
        config.write_text("\n".join(common + ["num_tiles_per_core=8"]) + "\n")
        runner.require_config_8(config, True)
        config.write_text("\n".join(common + ["num_tiles_per_core=10"]) + "\n")
        with unittest.TestCase().assertRaisesRegex(
            RuntimeError, "exactly one num_tiles_per_core=8"
        ):
            runner.require_config_8(config, True)


def test_hardened_delivery_gate_uses_eight_tiles() -> None:
    args = runner.restore_args(
        Path("guest"), Path("selector"), Path("cpt"), Path("arm")
    )
    assert args.count("--maa_num_tiles_per_core=8") == 1
    assert "--maa_num_tiles_per_core=10" not in args
    assert runner.HARDENED_REQUIRE_STATS is not runner.require_stats_8
    base_text = runner.BASE_PATH.read_text()
    assert 'values["IND_SoaJitValueDeliveries"] == words' in base_text
    assert 'values["IND_SoaJitValueReadIssues"]' in base_text
    assert '+ values["IND_SoaJitValueHits"]' in base_text
    assert '+ values["IND_SoaJitValueMergedWaiters"]' in base_text


def test_frozen_artifacts_and_correctness_before_performance() -> None:
    assert runner.base.GEM5_SHA256 == (
        "606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427"
    )
    assert runner.base.RAMULATOR_SHA256 == (
        "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753"
    )
    mismatch_gate = RUNNER_TEXT.index(
        "correctness mismatch; simTicks comparison forbidden"
    )
    ticks = RUNNER_TEXT.index('control_ticks = control["stats"]["simTicks"]')
    assert mismatch_gate < ticks
    assert '"deterministic_reduction_records": 11' in RUNNER_TEXT


def load_tests(loader, tests, pattern):  # type: ignore[no-untyped-def]
    del loader, tests, pattern
    suite = unittest.TestSuite()
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            suite.addTest(unittest.FunctionTestCase(value))
    return suite


if __name__ == "__main__":
    unittest.main()
