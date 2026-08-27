#!/usr/bin/env python3
"""Adversarial contract tests for the full page-fed p16/q16 lane-4 run."""

from __future__ import annotations

import copy
import importlib.util
import inspect
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = (
    ROOT / "experiments/scripts/"
    "run_cg_page_fed_p16_q16_value_cache_lane4_full.py"
)
RUNNER_TEXT = RUNNER_PATH.read_text(encoding="utf-8")
SPEC = importlib.util.spec_from_file_location(
    "cg_page_fed_p16_q16_lane4_full", RUNNER_PATH
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def good_stats() -> dict[str, int]:
    return {
        "simTicks": 100,
        "IND_SoaJitInstructions": runner.EXPECTED_WINDOWS,
        "IND_SoaJitTerminalCompletions": runner.EXPECTED_WINDOWS,
        "IND_SoaJitSelected": runner.EXPECTED_WORDS,
        "IND_SoaJitAliasesApplied": runner.EXPECTED_WORDS,
        "IND_SoaJitPredicateRejected": 0,
        "IND_SoaJitValueReadIssues": 11_266_329,
        "IND_SoaJitValueReadResponses": 11_266_329,
        "IND_SoaJitValueFills": 11_266_329,
        "IND_SoaJitValueCachedResponses": 11_266_329,
        "IND_SoaJitValueHits": 168_302_256,
        "IND_SoaJitValueMergedWaiters": 55,
        "IND_SoaJitValueDeliveries": runner.EXPECTED_WORDS,
        "IND_SoaJitAReadIssues": runner.EXPECTED_A_LINES,
        "IND_SoaJitAReadResponses": runner.EXPECTED_A_LINES,
        "IND_SoaJitAWriteIssues": runner.EXPECTED_A_LINES,
        "IND_SoaJitAWriteResponses": runner.EXPECTED_A_LINES,
        "IND_SoaJitPageFedOperations": runner.EXPECTED_WINDOWS,
        "IND_SoaJitPageFedAdmitCommands": runner.full.EXPECTED_PAGES,
        "IND_SoaJitPageFedCloseCommands": runner.EXPECTED_WINDOWS,
        "IND_SoaJitPageFedCommandResponses": (
            runner.full.EXPECTED_PAGES + runner.EXPECTED_WINDOWS
        ),
        "IND_SoaJitPageFedAdmittedWords": runner.EXPECTED_WORDS,
        "IND_SoaJitPageFedSpdIndexReads": runner.EXPECTED_WORDS,
        "IND_SoaJitPageFedRowWrites": runner.EXPECTED_WORDS,
        "IND_SoaJitPageFedCoherentIndexReadLines": 0,
        "IND_SoaJitPageFedCoherentIndexWriteLines": 0,
        "IND_SoaJitPageFedStateByteOperations": runner.EXPECTED_WINDOWS * 16,
        "IND_SoaJitEpochDrains": 0,
        "IND_BoundedGlobalMergeFallbacks": 0,
        "STR_PublishIssues": runner.full.EXPECTED_PUBLISH_LINES,
        "STR_PublishAccepts": runner.full.EXPECTED_PUBLISH_LINES,
        "STR_PublishWriteResponses": runner.full.EXPECTED_PUBLISH_LINES,
        "STR_PublishTerminals": runner.full.EXPECTED_PAGES,
        "IND_SoaJitValueEvictions": 10_915_609,
        "IND_SoaJitValueStalls": 0,
        "IND_SoaJitValueCacheHighWater": (
            runner.EXPECTED_WINDOWS * runner.ACTIVE_VALUE_OWNER_LINES
        ),
        "IND_SoaJitLookaheadStalls": 0,
        "IND_SoaJitContextStalls": 0,
        "IND_SoaJitActiveApplyLanes": (
            runner.EXPECTED_WINDOWS * runner.APPLY_LANES
        ),
        "IND_SoaJitApplyLaneHighWater": (
            runner.EXPECTED_WINDOWS * runner.APPLY_LANES
        ),
        "system.maa.port_cache_RD_packets": 1,
        "system.maa.port_cache_WR_packets": 1,
        "system.maa.cycles_TOTAL": 1,
        "system.maa.cycles_INDRMW": 1,
        "system.maa.I0_IND_CyclesRequest": 1,
    }


def good_config(lanes: int = 4) -> str:
    return (
        "\n".join(
            (
                "page_fed_soa_jit=true",
                "num_maas=1",
                "num_indirect_units_per_maa=4",
                "num_tiles_per_core=8",
                "num_tile_elements=16384",
                "physical_tile_elements=4096",
                "num_offset_table_entries=16384",
                "num_offset_table_epoch_entries=16384",
                "num_initial_row_table_slices=32",
                "soa_jit_predicate_active_credits=16",
                "soa_jit_active_value_owners=32",
                f"soa_jit_apply_lanes={lanes}",
                "soa_jit_value_cache_enable=true",
                "[system.mem_ctrls0]",
                "[system.mem_ctrls1]",
            )
        )
        + "\n"
    )


def good_terminal() -> dict[str, str]:
    return {
        "full_windows": str(runner.full.EXPECTED_WINDOWS),
        "staged_index_words": str(runner.full.EXPECTED_WORDS),
        "staged_value_words": "0",
        "product_words": str(runner.full.EXPECTED_WORDS),
        "index_publish_pages": "0",
        "value_publish_pages": "0",
        "product_publish_pages": str(runner.full.EXPECTED_PAGES),
        "logical_alu_vectors": "0",
        "physical_alu_vectors": str(runner.full.EXPECTED_PAGES),
        "logical_page_windows": "0",
        "physical_page_product_windows": "0",
        "page_fed_product_windows": str(runner.full.EXPECTED_WINDOWS),
        "direct4_product_page_fed_q16_windows": "0",
        "virtual_p_gather_windows": str(runner.full.EXPECTED_WINDOWS),
        "physical_p_gather_pages": "0",
        "page_fed_admit_pages": str(runner.full.EXPECTED_PAGES),
        "page_fed_closes": str(runner.full.EXPECTED_WINDOWS),
        "q_spmv_eligible_windows": str(runner.full.EXPECTED_Q_WINDOWS),
        "q_spmv_routed_windows": str(runner.full.EXPECTED_Q_WINDOWS),
        "residual_spmv_eligible_windows": str(
            runner.full.EXPECTED_RESIDUAL_WINDOWS
        ),
        "residual_spmv_routed_windows": str(
            runner.full.EXPECTED_RESIDUAL_WINDOWS
        ),
        "external_coherent_backing_bytes": "524288",
        "physical_spd_payload_bytes": "524288",
        "logical_scheduler_reserved_lanes": "0",
        "logical_scheduler_reserved_lane_payload_bytes": "0",
        "host_payload_access": "0",
        "coherent_index_backing_bytes": "0",
        "virtual_p_backing_bytes": "262144",
        "virtual_backing_traffic_eliminated": "0",
        "p16_reorder_preserved": "1",
        "q16_reorder_preserved": "1",
        "treatment": runner.TREATMENT,
        "slice": "all_spmv_full_windows",
        "producer": "physical_page_mul_direct_index_admit",
        "p_gather_mode": "virtual_16k",
        "performance_promotable": "0",
        "result": "PASS",
    }


def test_one_full_candidate_and_exact_treatment_command() -> None:
    command = runner.restore_command(
        Path("guest"), Path("selector"), Path("checkpoint"), Path("run")
    )
    assert command.count("--maa_soa_jit_value_cache_enable") == 1
    assert command.count("--maa_soa_jit_active_value_owners=32") == 1
    assert command.count("--maa_soa_jit_apply_lanes=4") == 1
    assert command.count("--maa_num_tiles_per_core=8") == 1
    assert RUNNER_TEXT.count("base.run_logged(checkpoint_args") == 1
    assert RUNNER_TEXT.count("base.run_logged(restore_args") == 1
    assert '"native_runs": 0' in RUNNER_TEXT
    assert '"lane_1_runs": 0' in RUNNER_TEXT
    assert '"cache_off_runs": 0' in RUNNER_TEXT
    assert '"direct4_runs": 0' in RUNNER_TEXT
    assert '"timeout": "none"' in RUNNER_TEXT
    assert "timeout=" not in RUNNER_TEXT


def test_compile_reuses_frozen_full_input_geometry() -> None:
    command = runner.base.compile_command(Path("guest"), Path("input"))
    for value in (
        "-DUSE_DATA_FROM_FILE",
        "-DCG_NA=150000",
        "-DNUM_CORES=4",
        "-DNUM_TILES_PER_CORE=8",
        "-DTILE_SIZE=16384",
    ):
        assert command.count(value) == 1
    assert runner.base.FROZEN_HEADER_BYTES == 992_830_458
    assert runner.base.FROZEN_HEADER_SHA256 == (
        "f2b18716e4a2356c597c95ee3583549def72700f2cb3294b0fcaacca46dbe131"
    )
    assert runner.base.GEM5_SHA256 == (
        "606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427"
    )
    assert runner.base.RAMULATOR_SHA256 == (
        "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753"
    )


def test_resolved_config_requires_cache_owners_tiles_and_lane4() -> None:
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / "config.ini"
        config.write_text(good_config(), encoding="utf-8")
        runner.validate_config(config)
        for old, new in (
            ("soa_jit_apply_lanes=4", "soa_jit_apply_lanes=1"),
            (
                "soa_jit_active_value_owners=32",
                "soa_jit_active_value_owners=31",
            ),
            (
                "soa_jit_value_cache_enable=true",
                "soa_jit_value_cache_enable=false",
            ),
            ("num_tiles_per_core=8", "num_tiles_per_core=10"),
        ):
            config.write_text(
                good_config().replace(old, new), encoding="utf-8"
            )
            with unittest.TestCase().assertRaises(RuntimeError):
                runner.validate_config(config)


def test_exact_full_mechanism_and_value_retention_closure() -> None:
    values = good_stats()
    runner.validate_stats_values(values)
    for key in (
        "IND_SoaJitInstructions",
        "IND_SoaJitAReadIssues",
        "IND_SoaJitAReadResponses",
        "IND_SoaJitAWriteIssues",
        "IND_SoaJitAWriteResponses",
        "IND_SoaJitPageFedCommandResponses",
        "IND_SoaJitPageFedRowWrites",
        "STR_PublishIssues",
        "STR_PublishAccepts",
        "STR_PublishWriteResponses",
        "STR_PublishTerminals",
    ):
        broken = copy.deepcopy(values)
        broken[key] += 1
        with unittest.TestCase().assertRaises(RuntimeError):
            runner.validate_stats_values(broken)
    assert values["IND_BoundedGlobalMergeFallbacks"] == 0
    assert values["IND_SoaJitEpochDrains"] == 0


def test_four_lane_active_sum_is_exact_and_high_water_is_bounded() -> None:
    values = good_stats()
    assert values["IND_SoaJitActiveApplyLanes"] == runner.EXPECTED_WINDOWS * 4
    assert (
        values["IND_SoaJitApplyLaneHighWater"] == runner.EXPECTED_WINDOWS * 4
    )
    sparse = copy.deepcopy(values)
    sparse["IND_SoaJitApplyLaneHighWater"] = runner.EXPECTED_WINDOWS * 3 + 1
    runner.validate_stats_values(sparse)

    broken = copy.deepcopy(values)
    broken["IND_SoaJitActiveApplyLanes"] -= 1
    with unittest.TestCase().assertRaisesRegex(
        RuntimeError, "active apply-lane"
    ):
        runner.validate_stats_values(broken)


def test_four_lane_high_water_rejects_both_illegal_boundaries() -> None:
    for bad in (
        runner.EXPECTED_WINDOWS * 3,
        runner.EXPECTED_WINDOWS * 3 - 1,
        runner.EXPECTED_WINDOWS * 4 + 1,
    ):
        broken = good_stats()
        broken["IND_SoaJitApplyLaneHighWater"] = bad
        with unittest.TestCase().assertRaisesRegex(RuntimeError, "high-water"):
            runner.validate_stats_values(broken)


def test_value_retention_stalls_and_delivery_identity_fail_closed() -> None:
    for key in (
        "IND_SoaJitValueStalls",
        "IND_SoaJitLookaheadStalls",
        "IND_SoaJitContextStalls",
    ):
        broken = good_stats()
        broken[key] = 1
        with unittest.TestCase().assertRaisesRegex(RuntimeError, "stall"):
            runner.validate_stats_values(broken)
    broken = good_stats()
    broken["IND_SoaJitValueMergedWaiters"] -= 1
    with unittest.TestCase().assertRaisesRegex(RuntimeError, "delivery"):
        runner.validate_stats_values(broken)


def test_terminal_requires_p16_q16_and_exact_backing_closure() -> None:
    fields = good_terminal()
    runner.full.validate_terminal(fields)
    for key, bad in (
        ("p16_reorder_preserved", "0"),
        ("q16_reorder_preserved", "0"),
        ("external_coherent_backing_bytes", "262144"),
        ("physical_spd_payload_bytes", "262144"),
        ("virtual_p_backing_bytes", "0"),
        ("coherent_index_backing_bytes", "1"),
        ("host_payload_access", "1"),
        ("page_fed_product_windows", "0"),
        ("product_words", "0"),
    ):
        changed = dict(fields)
        changed[key] = bad
        with unittest.TestCase().assertRaises(RuntimeError):
            runner.full.validate_terminal(changed)


def test_selection_authority_is_frozen_p16_q16_exact_faster_evidence() -> None:
    identity = runner.validate_lane_selection_authority()
    assert identity["selected_lane"] == 4
    assert identity["decision"] == "ACCEPT_EXACT_FASTER_ARM"
    assert identity["raw_root_sha256"] == (
        "78c38caf27664795e1684d64ef1595140a955253361beb7a199fd25b752734dc"
    )


def test_accepted_lane1_is_unreadable_before_pass_and_exact_after_pass() -> (
    None
):
    with unittest.TestCase().assertRaisesRegex(RuntimeError, "before PASS"):
        runner.read_accepted_lane1_after_pass("FAIL")
    result, stats = runner.read_accepted_lane1_after_pass(
        "PASS_NUMERICAL_MECHANISM_CORRECT"
    )
    assert result["performance"]["candidate"] == 162_849_334_269
    assert stats["simTicks"] == 162_849_334_269
    assert stats["IND_SoaJitActiveApplyLanes"] == runner.EXPECTED_WINDOWS
    assert stats["IND_SoaJitApplyLaneHighWater"] == runner.EXPECTED_WINDOWS


def test_comparison_requires_terminal_and_retained_line_closure() -> None:
    baseline_result = {"candidate": {"terminal": {"closed": 1}}}
    baseline_stats = good_stats()
    baseline_stats["simTicks"] = runner.ACCEPTED_LANE1_SIMTICKS
    baseline_stats["IND_SoaJitActiveApplyLanes"] = runner.EXPECTED_WINDOWS
    baseline_stats["IND_SoaJitApplyLaneHighWater"] = runner.EXPECTED_WINDOWS
    original = runner.read_accepted_lane1_after_pass
    try:
        runner.read_accepted_lane1_after_pass = lambda _gate: (
            baseline_result,
            baseline_stats,
        )
        candidate = {"terminal": {"closed": 1}, "stats": good_stats()}
        comparison = runner.compare_after_pass(
            "PASS_NUMERICAL_MECHANISM_CORRECT", candidate
        )
        assert comparison["value_retention_identity_exact"] is True
        assert comparison["retained_line_closure_exact_per_arm"] is True
        assert comparison["accepted_lane_1"] == 162_849_334_269
        broken = copy.deepcopy(candidate)
        broken["stats"]["IND_SoaJitValueHits"] -= 1
        with unittest.TestCase().assertRaisesRegex(RuntimeError, "delivery"):
            runner.compare_after_pass(
                "PASS_NUMERICAL_MECHANISM_CORRECT", broken
            )
        rescheduled = copy.deepcopy(candidate)
        rescheduled["stats"]["IND_SoaJitValueHits"] -= 1
        rescheduled["stats"]["IND_SoaJitValueMergedWaiters"] += 1
        comparison = runner.compare_after_pass(
            "PASS_NUMERICAL_MECHANISM_CORRECT", rescheduled
        )
        assert comparison["value_retention_identity_exact"] is False
        assert comparison["retained_line_closure_exact_per_arm"] is True
        broken = copy.deepcopy(candidate)
        broken["terminal"]["closed"] = 0
        with unittest.TestCase().assertRaisesRegex(RuntimeError, "terminal"):
            runner.compare_after_pass(
                "PASS_NUMERICAL_MECHANISM_CORRECT", broken
            )
    finally:
        runner.read_accepted_lane1_after_pass = original


def test_comparison_occurs_only_after_independent_pass() -> None:
    source = inspect.getsource(runner.main)
    validate = source.index("candidate, numerical_deltas = validate_restore")
    pass_gate = source.index('gate = "PASS_NUMERICAL_MECHANISM_CORRECT"')
    compare = source.index("performance = compare_after_pass(gate, candidate)")
    seal = source.index(
        "base.write_result_and_gate(out, result, certified_ledger)"
    )
    assert validate < pass_gate < compare < seal
    assert source.index("checkpoint_after == checkpoint_before") < validate
    assert source.index("artifacts_after == artifacts_before") < validate
    assert source.index("after_status == before_status") < validate
    assert source.index("after_commit == before_commit") < validate


def test_zero_incremental_pool_bytes_and_ports() -> None:
    assert runner.FIXED_APPLY_LANES_PER_UNIT == 4
    assert runner.APPLY_LANES == 4
    assert runner.FIXED_APPLY_LANE_OWNER_BYTES == 32
    assert runner.FIXED_APPLY_LANE_POOL_BYTES_PER_UNIT == 144
    for token in (
        '"new_payload_bytes": 0',
        '"new_control_bytes": 0',
        '"new_ports": 0',
        '"incremental_apply_lane_pool_bytes_vs_lane_1": 0',
        '"incremental_apply_lane_ports_vs_lane_1": 0',
        '"fixed_apply_lane_owners_per_maa"',
        '"active_apply_lanes_per_indirect_unit": APPLY_LANES',
    ):
        assert token in RUNNER_TEXT


def test_manifest_is_sealed_before_execution_and_source_stability_closes() -> (
    None
):
    source = inspect.getsource(runner.main)
    execute = source.index("base.run_logged(checkpoint_args")
    for token in (
        '"artifact_sha256.before"',
        '("compile", compile_args)',
        '("checkpoint", checkpoint_args)',
        '("restore", restore_args)',
        'out / "manifest.json"',
    ):
        assert source.index(token) < execute
    assert (
        "read_accepted_lane1_after_pass"
        not in source[: source.index("gate =")]
    )


def load_tests(loader, tests, pattern):  # type: ignore[no-untyped-def]
    del loader, tests, pattern
    suite = unittest.TestSuite()
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            suite.addTest(unittest.FunctionTestCase(value))
    return suite


if __name__ == "__main__":
    unittest.main()
