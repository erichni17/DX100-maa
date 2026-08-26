#!/usr/bin/env python3
"""Adversarial contract tests for the candidate-only full direct4/q16 run."""

from __future__ import annotations

import copy
import importlib.util
import inspect
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = (
    ROOT / "experiments/scripts/run_cg_direct4_product_page_fed_q16_full.py"
)
RUNNER_TEXT = RUNNER_PATH.read_text(encoding="utf-8")
SPEC = importlib.util.spec_from_file_location(
    "cg_direct4_q16_full", RUNNER_PATH
)
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
        "page_fed_product_windows": 0,
        "direct4_product_page_fed_q16_windows": runner.EXPECTED_WINDOWS,
        "virtual_p_gather_windows": 0,
        "physical_p_gather_pages": runner.EXPECTED_PAGES,
        "page_fed_admit_pages": runner.EXPECTED_PAGES,
        "page_fed_closes": runner.EXPECTED_WINDOWS,
        "q_spmv_eligible_windows": runner.EXPECTED_Q_WINDOWS,
        "q_spmv_routed_windows": runner.EXPECTED_Q_WINDOWS,
        "residual_spmv_eligible_windows": runner.EXPECTED_RESIDUAL_WINDOWS,
        "residual_spmv_routed_windows": runner.EXPECTED_RESIDUAL_WINDOWS,
        "external_coherent_backing_bytes": 262144,
        "physical_spd_payload_bytes": 524288,
        "logical_scheduler_reserved_lanes": 0,
        "logical_scheduler_reserved_lane_payload_bytes": 0,
        "host_payload_access": 0,
        "coherent_index_backing_bytes": 0,
        "virtual_p_backing_bytes": 0,
        "virtual_backing_traffic_eliminated": 1,
        "p16_reorder_preserved": 0,
        "q16_reorder_preserved": 1,
    }
    fields = {key: str(value) for key, value in values.items()}
    fields.update(
        treatment=runner.TREATMENT,
        slice="all_spmv_full_windows",
        producer="direct4_physical_p_gather_product_publish_then_q16",
        p_gather_mode="physical_4k_direct",
        performance_promotable="0",
        result="PASS",
    )
    return fields


def good_stats() -> dict[str, int]:
    issues = runner.EXPECTED_WORDS - 256
    values = {
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
        "IND_SoaJitAReadIssues": 57491,
        "IND_SoaJitAReadResponses": 57491,
        "IND_SoaJitAWriteIssues": 57491,
        "IND_SoaJitAWriteResponses": 57491,
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
    return values


def test_compile_is_one_ordinary_full_guest() -> None:
    command = runner.compile_command(Path("guest"), Path("input"))
    assert "-DUSE_DATA_FROM_FILE" in command
    assert "-DCG_NA=150000" in command
    assert "-DNUM_CORES=4" in command
    assert "-DNUM_TILES_PER_CORE=8" in command
    assert "-DTILE_SIZE=16384" in command
    assert "-DCG_DETERMINISTIC_REDUCTIONS" not in command
    assert "-DCG_REDUCTION_EVIDENCE" not in command


def test_no_baseline_or_native_invocation() -> None:
    assert runner.TREATMENT == "direct4_product_page_fed_q16"
    assert '"native_runs": 0' in RUNNER_TEXT
    assert '"physical_predecessor_runs": 0' in RUNNER_TEXT
    assert '"page_fed_control_runs": 0' in RUNNER_TEXT
    assert "page_fed_product_soa_jit.selector" not in RUNNER_TEXT
    assert "physical_page_product_soa_jit.selector" not in RUNNER_TEXT
    assert RUNNER_TEXT.count("run_logged(restore_args") == 1
    assert "--debug-flags" not in RUNNER_TEXT
    assert "--debug-file" not in RUNNER_TEXT
    assert "timeout=" not in RUNNER_TEXT
    restore = runner.restore_command(
        Path("guest"), Path("selector"), Path("checkpoint"), Path("run")
    )
    assert restore.count("--maa_soa_jit_value_cache_enable") == 1
    assert restore.count("--maa_soa_jit_active_value_owners=32") == 1


def test_exact_certificate_bounds_and_tolerant_not_exact_policy() -> None:
    assert runner.RELATIVE_BOUNDS == {
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
    for key in runner.RELATIVE_BOUNDS:
        reference[key] = "1"
    runner.validate_numerical(candidate, reference)
    weakened = dict(candidate)
    weakened["x_sum"] = "1.000000011"
    with unittest.TestCase().assertRaisesRegex(runner.GateError, "x_sum"):
        runner.validate_numerical(weakened, reference)
    assert "x_q5" not in inspect.getsource(runner.validate_numerical)
    assert "x_raw" not in inspect.getsource(runner.validate_numerical)


def test_terminal_requires_physical_p_counts_and_reorder_truth() -> None:
    runner.validate_terminal(good_terminal())
    for key, bad_value in (
        ("physical_p_gather_pages", "0"),
        ("virtual_p_gather_windows", "1"),
        ("p16_reorder_preserved", "1"),
        ("q16_reorder_preserved", "0"),
    ):
        fields = good_terminal()
        fields[key] = bad_value
        with unittest.TestCase().assertRaisesRegex(
            runner.GateError, "terminal mismatch"
        ):
            runner.validate_terminal(fields)


def test_terminal_rejects_altered_backing_and_spd_accounting() -> None:
    for key, bad_value in (
        ("external_coherent_backing_bytes", "524288"),
        ("physical_spd_payload_bytes", "262144"),
        ("coherent_index_backing_bytes", "1"),
        ("virtual_p_backing_bytes", "262144"),
        ("host_payload_access", "1"),
    ):
        fields = good_terminal()
        fields[key] = bad_value
        with unittest.TestCase().assertRaises(runner.GateError):
            runner.validate_terminal(fields)


def test_value_issues_are_not_equated_to_selected_or_delivery() -> None:
    values = good_stats()
    assert values["IND_SoaJitValueReadIssues"] != values["IND_SoaJitSelected"]
    runner.validate_stats_values(values)
    broken = copy.deepcopy(values)
    broken["IND_SoaJitValueMergedWaiters"] -= 1
    with unittest.TestCase().assertRaisesRegex(runner.GateError, "hit/merge"):
        runner.validate_stats_values(broken)
    source = inspect.getsource(runner.validate_stats_values)
    assert 'issues == values["IND_SoaJitSelected"]' not in source
    assert "issues == deliveries" not in source


def test_selected_value_retention_requires_hits_and_reduced_reads() -> None:
    values = good_stats()
    runner.validate_stats_values(values)
    for key, bad_value in (
        ("IND_SoaJitValueHits", 0),
        ("IND_SoaJitValueReadIssues", runner.EXPECTED_WORDS),
    ):
        broken = copy.deepcopy(values)
        broken[key] = bad_value
        if key == "IND_SoaJitValueHits":
            broken["IND_SoaJitValueReadIssues"] += values[key]
            broken["IND_SoaJitValueReadResponses"] += values[key]
            broken["IND_SoaJitValueFills"] += values[key]
            broken["IND_SoaJitValueCachedResponses"] += values[key]
        else:
            broken["IND_SoaJitValueHits"] = 0
            broken["IND_SoaJitValueMergedWaiters"] = 0
            broken["IND_SoaJitValueReadResponses"] = bad_value
            broken["IND_SoaJitValueFills"] = bad_value
            broken["IND_SoaJitValueCachedResponses"] = bad_value
        with unittest.TestCase().assertRaisesRegex(
            runner.GateError, "value hit/merge"
        ):
            runner.validate_stats_values(broken)


def test_exact_publisher_soa_a_and_zero_drain_closure() -> None:
    for key in (
        "STR_PublishTerminals",
        "STR_PublishIssues",
        "STR_PublishAccepts",
        "STR_PublishWriteResponses",
        "IND_SoaJitAliasesApplied",
        "IND_SoaJitAReadIssues",
        "IND_SoaJitAWriteResponses",
        "IND_SoaJitEpochDrains",
        "IND_BoundedGlobalMergeFallbacks",
    ):
        values = good_stats()
        values[key] += 1
        with unittest.TestCase().assertRaises(runner.GateError):
            runner.validate_stats_values(values)


def test_result_and_gate_are_after_every_validation_stage() -> None:
    main = inspect.getsource(runner.main)
    seal = main.index("write_result_and_gate(out, result, certified_ledger)")
    for token in (
        "run_logged(restore_args",
        "checkpoint_after == checkpoint_before",
        "artifacts_after == artifacts_before",
        "after_status == before_status",
        "after_commit == before_commit",
        "validate_certificate()",
        "validate_restore(run, native_fields)",
    ):
        assert main.index(token) < seal
    assert 'out / "result.json"' not in main[:seal]
    assert 'out / "gate.complete"' not in main[:seal]


def test_manifest_is_recorded_before_simulator_execution() -> None:
    main = inspect.getsource(runner.main)
    execute = main.index("run_logged(checkpoint_args")
    for token in (
        '"artifact_sha256.before"',
        '"compile_command.json"',
        '"checkpoint_command.json"',
        '"restore_command.json"',
        'out / "manifest.json"',
    ):
        assert main.index(token) < execute


def test_frozen_inputs_and_control_are_exactly_pinned() -> None:
    assert runner.GEM5_SHA256 == (
        "606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427"
    )
    assert runner.RAMULATOR_SHA256 == (
        "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753"
    )
    assert runner.FROZEN_HEADER_SHA256 == (
        "f2b18716e4a2356c597c95ee3583549def72700f2cb3294b0fcaacca46dbe131"
    )
    assert runner.FROZEN_HEADER_BYTES == 992830458
    assert runner.CONTROL_SIMTICKS == 715387684015
    assert runner.EXPECTED_WINDOWS == 10960
    assert runner.EXPECTED_Q_WINDOWS == 8768
    assert runner.EXPECTED_RESIDUAL_WINDOWS == 2192
    assert runner.EXPECTED_PAGES == 43840
    assert runner.EXPECTED_PUBLISH_LINES == 11223040


def test_config_rejects_non_eight_tile_or_wrong_spd_geometry() -> None:
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
        runner.validate_config(config)
        config.write_text(
            "\n".join(line.replace("4096", "8192") for line in lines) + "\n",
            encoding="utf-8",
        )
        with unittest.TestCase().assertRaises(runner.GateError):
            runner.validate_config(config)


def test_value_retention_has_explicit_zero_incremental_hardware() -> None:
    assert runner.FIXED_VALUE_OWNER_LINES == 128
    assert runner.ACTIVE_VALUE_OWNER_LINES == 32
    assert runner.VALUE_OWNER_LINE_BYTES == 64
    assert runner.INDIRECT_UNITS_PER_MAA == 4
    assert '"new_payload_bytes": 0' in RUNNER_TEXT
    assert '"new_control_bytes": 0' in RUNNER_TEXT
    assert '"new_ports": 0' in RUNNER_TEXT
    assert '"fixed_value_owner_payload_bytes_per_maa"' in RUNNER_TEXT
    assert '"active_value_owner_payload_bytes_per_maa"' in RUNNER_TEXT
    assert '"selected_value_cache_enable": True' in RUNNER_TEXT


def load_tests(loader, tests, pattern):  # type: ignore[no-untyped-def]
    del loader, tests, pattern
    suite = unittest.TestSuite()
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            suite.addTest(unittest.FunctionTestCase(value))
    return suite


if __name__ == "__main__":
    unittest.main()
