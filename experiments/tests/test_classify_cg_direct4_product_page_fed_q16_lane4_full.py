#!/usr/bin/env python3
"""Adversarial tests for the read-only lane-4 full successor classifier."""

from __future__ import annotations

import importlib.util
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT / "experiments/scripts/"
    "classify_cg_direct4_product_page_fed_q16_lane4_full.py"
)
SPEC = importlib.util.spec_from_file_location("cg_lane4_successor", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
classifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(classifier)


def lane_values(high_water: int | None = None) -> dict[str, int]:
    instructions = classifier.runner.EXPECTED_WINDOWS
    return {
        "IND_SoaJitInstructions": instructions,
        "IND_SoaJitActiveApplyLanes": 4 * instructions,
        "IND_SoaJitApplyLaneHighWater": (
            4 * instructions if high_water is None else high_water
        ),
    }


def service_fields() -> dict[str, str]:
    return {
        "Id": classifier.SERVICE_UNIT,
        "LoadState": "loaded",
        "ActiveState": "failed",
        "SubState": "failed",
        "Result": "exit-code",
        "MainPID": "0",
        "ExecMainCode": "1",
        "ExecMainStatus": "1",
        "InvocationID": classifier.SERVICE_INVOCATION_ID,
        "ExecMainStartTimestampMonotonic": str(
            classifier.SERVICE_START_MONOTONIC_US
        ),
        "ExecMainExitTimestampMonotonic": str(
            classifier.SERVICE_START_MONOTONIC_US + 1
        ),
    }


def valid_sealed_documents() -> tuple[dict[str, object], dict[str, object]]:
    manifest: dict[str, object] = {"schema": "fixture"}
    certificate: dict[str, object] = {
        "verdict": classifier.VERDICT,
        "native_speedup_claim": False,
        "iso_area_claim": False,
        "official_nas_verification": False,
        "full_promotion_claim": False,
        "performance": {
            "lane_1_over_lane_4": classifier.ratio_record(
                classifier.LANE1_SIMTICKS, classifier.FIRST_ROI_SIMTICKS
            )
        },
    }
    return manifest, certificate


def test_corrected_high_water_accepts_sparse_and_full_four_lane_use() -> None:
    instructions = classifier.runner.EXPECTED_WINDOWS
    classifier.validate_corrected_lane_accounting(lane_values())
    classifier.validate_corrected_lane_accounting(
        lane_values(3 * instructions + 1)
    )
    classifier.validate_corrected_lane_accounting(
        lane_values(classifier.OBSERVED_APPLY_HIGH_WATER)
    )


def test_three_lane_or_lower_high_water_is_rejected() -> None:
    instructions = classifier.runner.EXPECTED_WINDOWS
    for offset in (0, -1, -100):
        with unittest.TestCase().assertRaisesRegex(
            classifier.CertificateError, "high-water"
        ):
            classifier.validate_corrected_lane_accounting(
                lane_values(3 * instructions + offset)
            )


def test_above_four_lane_high_water_is_rejected() -> None:
    instructions = classifier.runner.EXPECTED_WINDOWS
    for offset in (1, 100):
        with unittest.TestCase().assertRaisesRegex(
            classifier.CertificateError, "high-water"
        ):
            classifier.validate_corrected_lane_accounting(
                lane_values(4 * instructions + offset)
            )


def test_active_lane_sum_must_remain_exactly_four_per_instruction() -> None:
    values = lane_values()
    values["IND_SoaJitActiveApplyLanes"] -= 1
    with unittest.TestCase().assertRaisesRegex(
        classifier.CertificateError, "active apply-lane"
    ):
        classifier.validate_corrected_lane_accounting(values)


def test_service_must_be_the_exact_terminal_failed_invocation() -> None:
    terminal = classifier.validate_service_properties(service_fields())
    assert terminal["terminal"] is True
    assert terminal["result"] == "exit-code"
    assert terminal["exec_main_status"] == 1
    for field, value in (
        ("ActiveState", "active"),
        ("MainPID", "3632390"),
        ("InvocationID", "0" * 32),
        ("ExecMainStatus", "0"),
    ):
        changed = service_fields()
        changed[field] = value
        with unittest.TestCase().assertRaisesRegex(
            classifier.CertificateError, "service"
        ):
            classifier.validate_service_properties(changed)


def test_registered_process_reuse_or_survival_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        proc = Path(temporary)
        codex = proc / "999999"
        codex.mkdir()
        (codex / "cmdline").write_bytes(
            b"codex\0objective mentions "
            + str(classifier.RAW_ROOT).encode()
            + b"\0"
        )
        classifier.validate_registered_process_absence(proc)
        (proc / str(classifier.REGISTERED_MAIN_PID)).mkdir()
        with unittest.TestCase().assertRaisesRegex(
            classifier.CertificateError, "registered PID"
        ):
            classifier.validate_registered_process_absence(proc)


def test_exact_raw_root_process_argument_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        proc = Path(temporary)
        process = proc / "999998"
        process.mkdir()
        (process / "cmdline").write_bytes(
            b"python3\0runner.py\0" + str(classifier.RAW_ROOT).encode() + b"\0"
        )
        with unittest.TestCase().assertRaisesRegex(
            classifier.CertificateError, "conflicting raw-run process"
        ):
            classifier.validate_registered_process_absence(proc)


def test_lane1_certificate_is_required_before_arithmetic() -> None:
    candidate = {"first_roi_simTicks": classifier.FIRST_ROI_SIMTICKS}
    with unittest.TestCase().assertRaisesRegex(
        classifier.CertificateError, "lane-1 certificate"
    ):
        classifier.build_performance(candidate, {})
    performance = classifier.build_performance(
        candidate,
        {
            "certificate_verified": True,
            "first_roi_simTicks": classifier.LANE1_SIMTICKS,
        },
    )
    ratio = performance["lane_1_over_lane_4"]
    assert ratio["numerator"] == 123_968_991_971
    assert ratio["denominator"] == 111_116_739_967
    assert ratio["exact_fraction"] == "123968991971/111116739967"


def test_native_iso_area_and_promotion_overclaims_are_rejected() -> None:
    _, certificate = valid_sealed_documents()
    classifier.validate_claims(certificate)
    for field in (
        "native_speedup_claim",
        "iso_area_claim",
        "official_nas_verification",
        "full_promotion_claim",
    ):
        changed = dict(certificate)
        changed[field] = True
        with unittest.TestCase().assertRaisesRegex(
            classifier.CertificateError, "forbidden claim"
        ):
            classifier.validate_claims(changed)


def test_raw_source_and_authority_roots_are_forbidden_outputs() -> None:
    for output in (
        classifier.ROOT / "certificate",
        classifier.RAW_ROOT / "certificate",
        classifier.LANE1_ROOT / "certificate",
        classifier.NUMERICAL_AUTHORITY_ROOT / "certificate",
        classifier.LANE_SELECTION_ROOT / "certificate",
    ):
        with unittest.TestCase().assertRaisesRegex(
            classifier.CertificateError, "external"
        ):
            classifier.validate_output_root(output)


def test_validate_existing_is_read_only() -> None:
    manifest, certificate = valid_sealed_documents()
    snapshot = {"fixture:input": "a" * 64}
    manifest_text = classifier.json_text(manifest)
    certificate_text = classifier.json_text(certificate)
    inputs_text = classifier.input_ledger_text(snapshot)
    gate_text = classifier.expected_gate(
        manifest_text, certificate_text, inputs_text
    )
    with tempfile.TemporaryDirectory() as temporary:
        runs = Path(temporary)
        output = runs / "certificate"
        output.mkdir()
        (output / "manifest.json").write_text(manifest_text)
        (output / "certificate.json").write_text(certificate_text)
        (output / "input_sha256.txt").write_text(inputs_text)
        (output / "gate.complete").write_text(gate_text)
        before = {
            path.name: (path.stat().st_mtime_ns, path.read_bytes())
            for path in output.iterdir()
        }
        with mock.patch.object(
            classifier, "RUNS_ROOT", runs
        ), mock.patch.object(
            classifier,
            "audit_inputs",
            return_value=(manifest, certificate, snapshot),
        ):
            validated = classifier.validate_existing(output)
        after = {
            path.name: (path.stat().st_mtime_ns, path.read_bytes())
            for path in output.iterdir()
        }
        assert validated == certificate
        assert before == after


def test_candidate_pass_and_lane1_certificate_precede_arithmetic() -> None:
    source = inspect.getsource(classifier.audit_inputs)
    candidate = source.index("candidate, deltas = validate_raw_candidate")
    lane1 = source.index("lane1_control = validate_lane1_control")
    documents = source.index("manifest, certificate = build_documents")
    assert candidate < lane1 < documents
    build_source = inspect.getsource(classifier.build_documents)
    assert build_source.index("build_performance") < build_source.index(
        '"schema": MANIFEST_SCHEMA'
    )


def test_classifier_contains_no_gem5_launch_path() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "run_logged(" not in source
    assert "runner.main(" not in source
    assert '"gem5_runs_launched": 0' in source
    assert '"raw_root_modified": False' in source
