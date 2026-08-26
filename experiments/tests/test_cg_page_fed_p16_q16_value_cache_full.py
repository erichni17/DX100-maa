#!/usr/bin/env python3
"""Adversarial contract tests for the full page-fed p16/q16 cache candidate."""

from __future__ import annotations

import copy
import importlib.util
import inspect
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "experiments/scripts/run_cg_page_fed_p16_q16_value_cache_full.py"
RUNNER_TEXT = RUNNER_PATH.read_text(encoding="utf-8")
SPEC = importlib.util.spec_from_file_location("cg_page_fed_p16q16_full", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def good_terminal() -> dict[str, str]:
    values = {
        "full_windows": runner.EXPECTED_WINDOWS,
        "staged_index_words": runner.EXPECTED_WORDS,
        "staged_value_words": 0,
        "product_words": runner.EXPECTED_WORDS,
        "index_publish_pages": 0,
        "value_publish_pages": 0,
        "product_publish_pages": runner.EXPECTED_PAGES,
        "logical_alu_vectors": 0,
        "physical_alu_vectors": runner.EXPECTED_PAGES,
        "logical_page_windows": 0,
        "physical_page_product_windows": 0,
        "page_fed_product_windows": runner.EXPECTED_WINDOWS,
        "direct4_product_page_fed_q16_windows": 0,
        "virtual_p_gather_windows": runner.EXPECTED_WINDOWS,
        "physical_p_gather_pages": 0,
        "page_fed_admit_pages": runner.EXPECTED_PAGES,
        "page_fed_closes": runner.EXPECTED_WINDOWS,
        "q_spmv_eligible_windows": runner.EXPECTED_Q_WINDOWS,
        "q_spmv_routed_windows": runner.EXPECTED_Q_WINDOWS,
        "residual_spmv_eligible_windows": runner.EXPECTED_RESIDUAL_WINDOWS,
        "residual_spmv_routed_windows": runner.EXPECTED_RESIDUAL_WINDOWS,
        "external_coherent_backing_bytes": 524288,
        "physical_spd_payload_bytes": 524288,
        "logical_scheduler_reserved_lanes": 0,
        "logical_scheduler_reserved_lane_payload_bytes": 0,
        "host_payload_access": 0,
        "coherent_index_backing_bytes": 0,
        "virtual_p_backing_bytes": 262144,
        "virtual_backing_traffic_eliminated": 0,
        "p16_reorder_preserved": 1,
        "q16_reorder_preserved": 1,
    }
    fields = {key: str(value) for key, value in values.items()}
    fields.update(
        treatment=runner.TREATMENT,
        slice="all_spmv_full_windows",
        producer="physical_page_mul_direct_index_admit",
        p_gather_mode="virtual_16k",
        performance_promotable="0",
        result="PASS",
    )
    return fields


def good_stats() -> dict[str, int]:
    issues = runner.EXPECTED_WORDS - 256
    return {
        "simTicks": 1,
        "IND_SoaJitInstructions": runner.EXPECTED_WINDOWS,
        "IND_SoaJitTerminalCompletions": runner.EXPECTED_WINDOWS,
        "IND_SoaJitSelected": runner.EXPECTED_WORDS,
        "IND_SoaJitAliasesApplied": runner.EXPECTED_WORDS,
        "IND_SoaJitPredicateRejected": 0,
        "IND_SoaJitValueReadIssues": issues,
        "IND_SoaJitValueReadResponses": issues,
        "IND_SoaJitValueFills": issues,
        "IND_SoaJitValueCachedResponses": issues,
        "IND_SoaJitValueHits": 215,
        "IND_SoaJitValueMergedWaiters": 41,
        "IND_SoaJitValueDeliveries": runner.EXPECTED_WORDS,
        "IND_SoaJitAReadIssues": runner.EXPECTED_A_LINES,
        "IND_SoaJitAReadResponses": runner.EXPECTED_A_LINES,
        "IND_SoaJitAWriteIssues": runner.EXPECTED_A_LINES,
        "IND_SoaJitAWriteResponses": runner.EXPECTED_A_LINES,
        "IND_SoaJitPageFedOperations": runner.EXPECTED_WINDOWS,
        "IND_SoaJitPageFedAdmitCommands": runner.EXPECTED_PAGES,
        "IND_SoaJitPageFedCloseCommands": runner.EXPECTED_WINDOWS,
        "IND_SoaJitPageFedCommandResponses": (
            runner.EXPECTED_PAGES + runner.EXPECTED_WINDOWS
        ),
        "IND_SoaJitPageFedAdmittedWords": runner.EXPECTED_WORDS,
        "IND_SoaJitPageFedSpdIndexReads": runner.EXPECTED_WORDS,
        "IND_SoaJitPageFedRowWrites": runner.EXPECTED_WORDS,
        "IND_SoaJitPageFedCoherentIndexReadLines": 0,
        "IND_SoaJitPageFedCoherentIndexWriteLines": 0,
        "IND_SoaJitPageFedStateByteOperations": runner.EXPECTED_WINDOWS * 16,
        "IND_SoaJitEpochDrains": 0,
        "IND_BoundedGlobalMergeFallbacks": 0,
        "STR_PublishIssues": runner.EXPECTED_PUBLISH_LINES,
        "STR_PublishAccepts": runner.EXPECTED_PUBLISH_LINES,
        "STR_PublishWriteResponses": runner.EXPECTED_PUBLISH_LINES,
        "STR_PublishTerminals": runner.EXPECTED_PAGES,
    }


def test_compile_is_one_ordinary_full_guest() -> None:
    command = runner.base.compile_command(Path("guest"), Path("input"))
    assert "-DUSE_DATA_FROM_FILE" in command
    assert "-DCG_NA=150000" in command
    assert "-DNUM_CORES=4" in command
    assert "-DNUM_TILES_PER_CORE=8" in command
    assert "-DTILE_SIZE=16384" in command
    assert "-DCG_DETERMINISTIC_REDUCTIONS" not in command
    assert "-DCG_REDUCTION_EVIDENCE" not in command


def test_one_cache_on_candidate_and_no_other_run() -> None:
    assert runner.TREATMENT == "page_fed_product_soa_jit"
    assert '"native_runs": 0' in RUNNER_TEXT
    assert '"cache_off_runs": 0' in RUNNER_TEXT
    assert '"direct4_runs": 0' in RUNNER_TEXT
    assert RUNNER_TEXT.count("base.run_logged(checkpoint_args") == 1
    assert RUNNER_TEXT.count("base.run_logged(restore_args") == 1
    assert "timeout=" not in RUNNER_TEXT
    restore = runner.base.restore_command(
        Path("guest"), Path("selector"), Path("checkpoint"), Path("run")
    )
    assert restore.count("--maa_soa_jit_value_cache_enable") == 1
    assert restore.count("--maa_num_tiles_per_core=8") == 1
    assert restore.count("--maa_page_fed_soa_jit") == 1


def test_terminal_requires_p16_q16_and_exact_backing() -> None:
    runner.validate_terminal(good_terminal())
    for key, bad in (
        ("p16_reorder_preserved", "0"),
        ("q16_reorder_preserved", "0"),
        ("external_coherent_backing_bytes", "262144"),
        ("virtual_p_backing_bytes", "0"),
        ("coherent_index_backing_bytes", "1"),
        ("host_payload_access", "1"),
        ("physical_spd_payload_bytes", "262144"),
    ):
        fields = good_terminal()
        fields[key] = bad
        with unittest.TestCase().assertRaisesRegex(
            runner.GateError, "terminal mismatch"
        ):
            runner.validate_terminal(fields)


def test_terminal_rejects_direct4_or_incomplete_window_routes() -> None:
    for key, bad in (
        ("page_fed_product_windows", "0"),
        ("direct4_product_page_fed_q16_windows", "1"),
        ("virtual_p_gather_windows", "0"),
        ("physical_p_gather_pages", "1"),
        ("q_spmv_routed_windows", "8767"),
        ("residual_spmv_routed_windows", "2191"),
    ):
        fields = good_terminal()
        fields[key] = bad
        with unittest.TestCase().assertRaises(runner.GateError):
            runner.validate_terminal(fields)


def test_exact_window_publisher_page_fed_and_a_closure() -> None:
    runner.validate_stats_values(good_stats())
    for key in (
        "IND_SoaJitInstructions",
        "IND_SoaJitTerminalCompletions",
        "IND_SoaJitAliasesApplied",
        "IND_SoaJitAReadIssues",
        "IND_SoaJitAReadResponses",
        "IND_SoaJitAWriteIssues",
        "IND_SoaJitAWriteResponses",
        "IND_SoaJitPageFedAdmitCommands",
        "IND_SoaJitPageFedCommandResponses",
        "IND_SoaJitPageFedRowWrites",
        "STR_PublishIssues",
        "STR_PublishAccepts",
        "STR_PublishWriteResponses",
        "STR_PublishTerminals",
    ):
        values = good_stats()
        values[key] += 1
        with unittest.TestCase().assertRaisesRegex(
            runner.GateError, "exact window/SoA/A/page-fed/publisher"
        ):
            runner.validate_stats_values(values)


def test_value_issues_are_lower_with_positive_hits_and_exact_delivery() -> None:
    values = good_stats()
    runner.validate_stats_values(values)
    assert values["IND_SoaJitValueReadIssues"] < values["IND_SoaJitValueDeliveries"]
    assert values["IND_SoaJitValueHits"] > 0
    broken = copy.deepcopy(values)
    broken["IND_SoaJitValueMergedWaiters"] -= 1
    with unittest.TestCase().assertRaisesRegex(runner.GateError, "delivery"):
        runner.validate_stats_values(broken)
    for key, bad in (
        ("IND_SoaJitValueHits", 0),
        ("IND_SoaJitValueReadIssues", runner.EXPECTED_WORDS),
    ):
        broken = copy.deepcopy(values)
        broken[key] = bad
        if key == "IND_SoaJitValueHits":
            broken["IND_SoaJitValueReadIssues"] += values[key]
            broken["IND_SoaJitValueReadResponses"] += values[key]
            broken["IND_SoaJitValueFills"] += values[key]
            broken["IND_SoaJitValueCachedResponses"] += values[key]
        else:
            broken["IND_SoaJitValueReadResponses"] = bad
            broken["IND_SoaJitValueFills"] = bad
            broken["IND_SoaJitValueCachedResponses"] = bad
            broken["IND_SoaJitValueHits"] = 0
            broken["IND_SoaJitValueMergedWaiters"] = 0
        with unittest.TestCase().assertRaisesRegex(runner.GateError, "delivery"):
            runner.validate_stats_values(broken)


def test_comparison_is_explicitly_after_pass_and_pinned() -> None:
    with unittest.TestCase().assertRaisesRegex(runner.GateError, "before PASS"):
        runner.compare_after_pass("FAIL", 1)
    source = inspect.getsource(runner.main)
    validate = source.index("validate_restore(run, authority_fields)")
    pass_gate = source.index('gate = "PASS_NUMERICAL_MECHANISM_CORRECT"')
    compare = source.index("compare_after_pass(gate, sim_ticks)")
    seal = source.index("base.write_result_and_gate(out, result, certified_ledger)")
    assert validate < pass_gate < compare < seal
    assert runner.base.CONTROL_SIMTICKS == 715387684015
    assert str(runner.base.CONTROL_ROOT).endswith(
        "2026-08-25-cg-page-fed-application-full-31c00be8-r2"
    )


def test_tolerant_full_numerical_authority_is_unchanged() -> None:
    assert runner.base.RELATIVE_BOUNDS == {
        "x_sum": 1e-8,
        "x_norm_sq": 1e-8,
        "z_sum": 1e-8,
        "z_norm_sq": 1e-8,
        "rnorm": 1e-3,
        "zeta": 1e-10,
    }
    candidate = {
        "result": "PASS",
        "nonfinite_x": "0",
        "nonfinite_z": "0",
        "x_sum": "1.000000009",
        "x_norm_sq": "1.000000009",
        "z_sum": "1.000000009",
        "z_norm_sq": "1.000000009",
        "rnorm": "1.0009",
        "zeta": "1.00000000009",
    }
    reference = dict(candidate)
    for key in runner.base.RELATIVE_BOUNDS:
        reference[key] = "1"
    runner.base.validate_numerical(candidate, reference)
    assert "x_q5" not in inspect.getsource(runner.base.validate_numerical)
    assert "x_raw" not in inspect.getsource(runner.base.validate_numerical)


def test_config_requires_eight_tiles_spd_and_cache_on() -> None:
    lines = [
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
        "soa_jit_value_cache_enable=true",
        "[system.mem_ctrls0]",
        "[system.mem_ctrls1]",
    ]
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / "config.ini"
        config.write_text("\n".join(lines) + "\n", encoding="utf-8")
        runner.base.validate_config(config)
        config.write_text(
            "\n".join(line.replace("enable=true", "enable=false") for line in lines)
            + "\n",
            encoding="utf-8",
        )
        with unittest.TestCase().assertRaises(runner.base.GateError):
            runner.base.validate_config(config)


def test_frozen_artifacts_geometry_and_counts_are_exact() -> None:
    assert runner.base.GEM5_SHA256 == (
        "606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427"
    )
    assert runner.base.RAMULATOR_SHA256 == (
        "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753"
    )
    assert runner.base.FROZEN_HEADER_SHA256 == (
        "f2b18716e4a2356c597c95ee3583549def72700f2cb3294b0fcaacca46dbe131"
    )
    assert runner.base.FROZEN_HEADER_BYTES == 992830458
    assert runner.EXPECTED_WINDOWS == 10960
    assert runner.EXPECTED_Q_WINDOWS == 8768
    assert runner.EXPECTED_RESIDUAL_WINDOWS == 2192
    assert runner.EXPECTED_PAGES == 43840
    assert runner.EXPECTED_WORDS == 179568640
    assert runner.EXPECTED_A_LINES == 57491
    assert runner.EXPECTED_PUBLISH_LINES == 11223040
    assert runner.PHYSICAL_SPD_PAYLOAD_BYTES == 524288
    assert runner.EXTERNAL_COHERENT_BACKING_BYTES == 524288
    assert runner.VIRTUAL_P_BACKING_BYTES + runner.PRODUCT_BACKING_BYTES == 524288


def test_manifest_precedes_execution_and_ledgers_close_before_seal() -> None:
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
    seal = source.index("base.write_result_and_gate(out, result, certified_ledger)")
    for token in (
        "checkpoint_after == checkpoint_before",
        "artifacts_after == artifacts_before",
        "after_status == before_status",
        "after_commit == before_commit",
        "base.validate_certificate()",
        "validate_restore(run, authority_fields)",
    ):
        assert source.index(token) < seal


def test_zero_incremental_value_retention_and_no_claims() -> None:
    assert runner.FIXED_VALUE_OWNER_LINES == 128
    assert runner.ACTIVE_VALUE_OWNER_LINES == 32
    assert runner.VALUE_OWNER_LINE_BYTES == 64
    assert runner.INDIRECT_UNITS_PER_MAA == 4
    assert '"new_payload_bytes": 0' in RUNNER_TEXT
    assert '"new_control_bytes": 0' in RUNNER_TEXT
    assert '"new_ports": 0' in RUNNER_TEXT
    assert '"native_speedup_claim": False' in RUNNER_TEXT
    assert '"direct4_claim": False' in RUNNER_TEXT
    assert '"p16_reorder_preserved": True' in RUNNER_TEXT
    assert '"q16_reorder_preserved": True' in RUNNER_TEXT


def load_tests(loader, tests, pattern):  # type: ignore[no-untyped-def]
    del loader, tests, pattern
    suite = unittest.TestSuite()
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            suite.addTest(unittest.FunctionTestCase(value))
    return suite


if __name__ == "__main__":
    unittest.main()
