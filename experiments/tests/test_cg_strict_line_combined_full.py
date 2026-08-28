#!/usr/bin/env python3
"""Adversarial gates for the strict line-combined full-CG runner."""

from __future__ import annotations

import copy
import importlib.util
import inspect
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "experiments/scripts/run_cg_strict_line_combined_full.py"
SPEC = importlib.util.spec_from_file_location(
    "cg_strict_line_combined_full", RUNNER_PATH
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

CLASSIFIER_PATH = (
    ROOT / "experiments/scripts/classify_cg_strict_line_combined_full.py"
)
CLASSIFIER_SPEC = importlib.util.spec_from_file_location(
    "cg_strict_line_combined_full_classifier", CLASSIFIER_PATH
)
assert CLASSIFIER_SPEC is not None and CLASSIFIER_SPEC.loader is not None
classifier = importlib.util.module_from_spec(CLASSIFIER_SPEC)
CLASSIFIER_SPEC.loader.exec_module(classifier)


def timing_line(
    event: str,
    generation: int,
    backing_issues: int,
    *,
    unit: int = 0,
    token: int = 6,
) -> str:
    capacity = (
        "feeder_words=4096 result_context_words=4096"
        if event == "strict_page_fed_two_phase_timing"
        else "feeder_words=16 result_words=384"
    )
    return (
        f"1: global: event={event} unit={unit} token={token} "
        f"generation={generation} {capacity} "
        "terminal=1 order_ok=1 exact_b_once=1 raw_b_retained_bytes=0 "
        "descriptor_backing_bytes=0 replay_passes=0 coherent_ack=1 "
        "b_words=16384 b_lines=1024 descriptors=16384 pages_ready=4 "
        "A_FIRST_ISSUE=20 ROW_OFFSET_LAST_INSERT=19 a_issues=2 "
        f"a_responses=2 backing_issues={backing_issues} "
        f"backing_acks={backing_issues}\n"
    )


def whole_line(
    *,
    p_token: int = 6,
    p_generation: int = 1,
    q_unit: int = 0,
    q_generation: int = 2,
    p_core: int = 0,
    product_backing: str = "0x260000",
) -> str:
    return (
        "9: system.maa: event=strict_cg_p16_q16_window "
        f"p_token={p_token} p_generation={p_generation} "
        f"q_unit={q_unit} q_generation={q_generation} "
        "p_terminal=1 q_terminal=1 "
        "p16_reorder=1 q16_reorder=1 direct4=0 p_mode=nonfused "
        "drains=0 fallbacks=0 order_ok=1 terminal=1 "
        "cg_numerical_terminal=runner_join_required "
        "p_product_page_responses=4 q_product_deliveries=16384 "
        "q_value_read_issues=3 q_value_read_responses=3 q_value_fills=3 "
        "p_A_FIRST_ISSUE=20 p_ROW_OFFSET_LAST_INSERT=19 "
        "q_A_FIRST_ISSUE=30 q_ROW_OFFSET_LAST_INSERT=29 "
        f"p_core={p_core} product_backing={product_backing}\n"
    )


def good_trace(write_bytes: int = 64) -> str:
    lines = [timing_line("strict_two_phase_timing", 1, 1)]
    for page in range(4):
        lines.append(
            "2: system.maa: event=strict_product_page_response "
            f"core=0 backing=0x260000 page={page} generation={page + 1} "
            f"pages={page + 1}/4\n"
        )
    lines.append(timing_line("strict_page_fed_two_phase_timing", 2, 2))
    lines.append(whole_line())
    lines.append(
        "10: global: event=backing_write_issue "
        f"bytes={write_bytes} valid_words=0xffff\n"
    )
    return "".join(lines)


def test_restore_is_exactly_one_nonfused_candidate_treatment() -> None:
    command = runner.restore_command(
        Path("guest"), Path("selector"), Path("checkpoint"), Path("run")
    )
    for flag in (
        "--maa_virtual_strict_two_phase",
        "--maa_virtual_masked_writes",
        "--maa_soa_jit_value_cache_enable",
        "--maa_soa_jit_active_value_owners=32",
        "--maa_soa_jit_apply_lanes=4",
        "--maa_num_tiles_per_core=8",
    ):
        assert command.count(flag) == 1
    assert command[0] == str(runner.STRICT_GEM5)
    assert "direct4_product_page_fed_q16" not in " ".join(command)
    assert "--maa_fused_p16_product" not in command


def test_checkpoint_reuse_fails_closed_on_binary_and_abi_hashes() -> None:
    assert runner.FROZEN_GEM5_SHA256 != runner.STRICT_GEM5_SHA256
    assert runner.FROZEN_ABI_SHA256 != runner.CURRENT_ABI_SHA256
    source = inspect.getsource(runner.checkpoint_reuse_decision)
    assert 'reasons.append("gem5_sha256_mismatch")' in source
    assert 'reasons.append("guest_abi_sha256_mismatch")' in source
    assert '"reuse_accepted": False' in source
    assert '"new_treatment_neutral_checkpoint_required": True' in source


def test_config_requires_strict_masked_cache_and_four_lanes() -> None:
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
        "soa_jit_apply_lanes=4",
        "soa_jit_value_cache_enable=true",
        "virtual_strict_two_phase=true",
        "virtual_masked_writes=true",
        "[system.mem_ctrls0]",
        "[system.mem_ctrls1]",
    ]
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / "config.ini"
        config.write_text("\n".join(lines) + "\n", encoding="utf-8")
        runner.validate_config(config)
        for required in (
            "soa_jit_apply_lanes=4",
            "soa_jit_value_cache_enable=true",
            "virtual_strict_two_phase=true",
            "virtual_masked_writes=true",
        ):
            broken = [line for line in lines if line != required]
            config.write_text("\n".join(broken) + "\n", encoding="utf-8")
            with unittest.TestCase().assertRaises(RuntimeError):
                runner.validate_config(config)


def test_streaming_trace_closes_p_q_whole_pages_order_and_writes() -> None:
    with tempfile.TemporaryDirectory() as directory:
        trace = Path(directory) / "strict_trace.log"
        trace.write_text(good_trace(), encoding="utf-8")
        summary = runner.scan_strict_trace(trace, expected_windows=1)
    assert summary["p_timing"] == 1
    assert summary["q_timing"] == 1
    assert summary["whole_windows"] == 1
    assert summary["product_pages"] == 4
    assert summary["p_backing_writes"] == 1
    assert len(summary["trace_sha256"]) == 64


def test_generation_reuse_is_keyed_by_token_and_unit_lifetime() -> None:
    second = [timing_line("strict_two_phase_timing", 1, 1, unit=2, token=22)]
    for page in range(4):
        second.append(
            "12: system.maa: event=strict_product_page_response "
            f"core=2 backing=0x270000 page={page} generation={page + 1} "
            f"pages={page + 1}/4\n"
        )
    second.append(
        timing_line(
            "strict_page_fed_two_phase_timing",
            2,
            2,
            unit=1,
        )
    )
    second.append(
        whole_line(
            p_token=22,
            q_unit=1,
            p_core=2,
            product_backing="0x270000",
        )
    )
    second.append("13: global: event=backing_write_issue bytes=64\n")
    with tempfile.TemporaryDirectory() as directory:
        trace = Path(directory) / "strict_trace.log"
        trace.write_text(good_trace() + "".join(second), encoding="utf-8")
        summary = runner.scan_strict_trace(trace, expected_windows=2)
    assert summary["p_timing"] == 2
    assert summary["q_timing"] == 2
    assert summary["whole_windows"] == 2


def test_streaming_trace_rejects_non_64_byte_p_write() -> None:
    with tempfile.TemporaryDirectory() as directory:
        trace = Path(directory) / "strict_trace.log"
        trace.write_text(good_trace(write_bytes=4), encoding="utf-8")
        with unittest.TestCase().assertRaisesRegex(
            RuntimeError, "non-64-byte"
        ):
            runner.scan_strict_trace(trace, expected_windows=1)


def test_streaming_trace_rejects_order_and_response_page_failures() -> None:
    for old, new in (
        ("p_A_FIRST_ISSUE=20", "p_A_FIRST_ISSUE=18"),
        ("page=3 generation=4 pages=4/4", "page=2 generation=4 pages=4/4"),
        ("direct4=0", "direct4=1"),
        ("fallbacks=0", "fallbacks=1"),
    ):
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "strict_trace.log"
            trace.write_text(good_trace().replace(old, new), encoding="utf-8")
            with unittest.TestCase().assertRaises(RuntimeError):
                runner.scan_strict_trace(trace, expected_windows=1)


def test_strict_stats_join_exact_trace_work_and_forbid_fusion() -> None:
    trace = {
        "p_b_lines": 11_223_040,
        "q_b_lines": 11_223_040,
        "p_descriptors": runner.EXPECTED_WORDS,
        "q_descriptors": runner.EXPECTED_WORDS,
        "p_a_issues": 10,
        "q_a_issues": runner.EXPECTED_A_LINES,
        "p_backing_issues": 60_000_000,
        "q_backing_issues": runner.EXPECTED_A_LINES,
        "p_pages_ready": runner.EXPECTED_PRODUCT_PAGES,
        "q_pages_ready": runner.EXPECTED_PRODUCT_PAGES,
    }
    values = {
        "IND_StrictTwoPhaseOperations": 2 * runner.EXPECTED_WINDOWS,
        "IND_StrictTwoPhaseBFetchLines": (
            trace["p_b_lines"] + trace["q_b_lines"]
        ),
        "IND_StrictTwoPhaseDescriptors": 2 * runner.EXPECTED_WORDS,
        "IND_StrictTwoPhaseAIssues": 10 + runner.EXPECTED_A_LINES,
        "IND_StrictTwoPhaseBackingIssues": (
            60_000_000 + runner.EXPECTED_A_LINES
        ),
        "IND_StrictTwoPhasePagesReady": 2 * runner.EXPECTED_PRODUCT_PAGES,
        "IND_NumOTEpochDrain": 0,
        **{
            name: 1
            for name in (
                "IND_StrictTwoPhaseBFetchCycles",
                "IND_StrictTwoPhaseRowOffsetCycles",
                "IND_StrictTwoPhaseAIssueCycles",
                "IND_StrictTwoPhaseBackingCycles",
                "IND_StrictTwoPhasePageCycles",
                "IND_StrictTwoPhaseConsumerCycles",
            )
        },
        **{name: 0 for name in runner.FUSED_ZERO_STATS},
    }
    runner.validate_strict_stats_values(values, trace)
    broken = copy.deepcopy(values)
    broken["IND_FusedP16Operations"] = 1
    with unittest.TestCase().assertRaisesRegex(RuntimeError, "fused"):
        runner.validate_strict_stats_values(broken, trace)
    broken = copy.deepcopy(values)
    broken["IND_StrictTwoPhaseBackingIssues"] -= 1
    with unittest.TestCase().assertRaisesRegex(RuntimeError, "identity"):
        runner.validate_strict_stats_values(broken, trace)


def test_manifest_forbids_native_direct4_and_incomplete_arithmetic() -> None:
    source = RUNNER_PATH.read_text(encoding="utf-8")
    for token in (
        '"candidate_restores": 1',
        '"checkpoint_creations": 1',
        '"native_runs": 0',
        '"direct4_runs": 0',
        '"control_runs": 0',
        '"other_candidate_runs": 0',
        '"timeout": "none"',
        '"all_p_writes_64_bytes": True',
    ):
        assert token in source
    execute = inspect.getsource(runner.execute)
    validate = execute.index("candidate, deltas = validate_restore")
    result = execute.index('"first_roi_simTicks": sim_ticks')
    terminal = execute.index('"accepted": True')
    assert validate < result < terminal


def test_failure_writes_terminal_rejection_without_performance() -> None:
    source = inspect.getsource(runner.main)
    rejection = source.index('"accepted": False')
    assert source.index("except Exception as error") < rejection
    assert "simTicks" not in source[rejection:]


def test_successor_pins_exact_obsolete_gate_and_launches_no_gem5() -> None:
    raw_terminal = classifier.load_json(
        classifier.RAW_ROOT / "runtime_terminal.json"
    )
    assert raw_terminal["accepted"] is False
    assert raw_terminal["error"] == "duplicate p generation: 1"
    source = CLASSIFIER_PATH.read_text(encoding="utf-8")
    assert '"gem5_runs_launched": 0' in source
    assert '"raw_root_modified": False' in source
    assert "subprocess.run(" not in source


def test_successor_creates_output_only_after_read_only_audit() -> None:
    source = inspect.getsource(classifier.create_certificate)
    assert source.index("build_documents(progress)") < source.index(
        "output.mkdir"
    )
    assert source.index("output.mkdir") < source.index("write_exclusive")


def test_successor_seal_validation_is_read_only() -> None:
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "certificate"
        output.mkdir()
        manifest = "{}\n"
        certificate = (
            '{"gem5_runs_launched": 0, "read_only_successor": true, '
            '"verdict": "PASS_NUMERICAL_MECHANISM_CORRECT"}\n'
        )
        inputs = "input\n"
        (output / "manifest.json").write_text(manifest)
        (output / "certificate.json").write_text(certificate)
        (output / "input_sha256.txt").write_text(inputs)
        gate = (
            classifier.VERDICT
            + "\nread_only_successor=true\nraw_root_modified=false\n"
            + "manifest_sha256="
            + classifier.hashlib.sha256(manifest.encode()).hexdigest()
            + "\ncertificate_sha256="
            + classifier.hashlib.sha256(certificate.encode()).hexdigest()
            + "\ninput_sha256="
            + classifier.hashlib.sha256(inputs.encode()).hexdigest()
            + "\n"
        )
        (output / "gate.complete").write_text(gate)
        before = classifier.raw_stat_state()
        result = classifier.validate_seal(output)
        after = classifier.raw_stat_state()
    assert result["verdict"] == classifier.VERDICT
    assert before == after
