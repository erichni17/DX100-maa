#!/usr/bin/env python3
"""Adversarial tests for the read-only p16/q16 lane-4 classifier."""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT / "experiments/scripts/"
    "classify_cg_page_fed_p16_q16_value_cache_lane4_full.py"
)
SPEC = importlib.util.spec_from_file_location(
    "cg_page_fed_p16q16_lane4_successor", SCRIPT
)
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


def journal_entries() -> list[dict[str, str]]:
    start = {
        "MESSAGE": (
            "Started DX100 full CG page-fed p16q16 value-cache lane4 "
            "candidate r1."
        ),
        "USER_UNIT": classifier.SERVICE_UNIT,
        "USER_INVOCATION_ID": classifier.SERVICE_INVOCATION_ID,
        "_BOOT_ID": classifier.SERVICE_BOOT_ID,
        "__MONOTONIC_TIMESTAMP": str(classifier.SERVICE_START_MONOTONIC_US),
        "_PID": "4784",
    }
    messages = [
        "Traceback (most recent call last):",
        "frame 1",
        "frame 2",
        "frame 3",
        "frame 4",
        "frame 5",
        "frame 6",
        "frame 7",
        "frame 8",
        "frame 9",
        "frame 10",
        "frame 11",
        "frame 12",
        "__main__.GateError: four-lane active/high-water closure failed",
    ]
    process = [
        {
            "MESSAGE": message,
            "_SYSTEMD_USER_UNIT": classifier.SERVICE_UNIT,
            "_SYSTEMD_INVOCATION_ID": classifier.SERVICE_INVOCATION_ID,
            "_BOOT_ID": classifier.SERVICE_BOOT_ID,
            "_PID": str(classifier.REGISTERED_MAIN_PID),
            "_CMDLINE": classifier.REGISTERED_COMMAND,
            "__MONOTONIC_TIMESTAMP": str(
                classifier.SERVICE_EXIT_MONOTONIC_US - 100
            ),
        }
        for message in messages
    ]
    exit_entry = {
        "MESSAGE_ID": "98e322203f7a4ed290d09fe03c09fe15",
        "MESSAGE": "main process exited",
        "USER_UNIT": classifier.SERVICE_UNIT,
        "USER_INVOCATION_ID": classifier.SERVICE_INVOCATION_ID,
        "EXIT_CODE": "exited",
        "EXIT_STATUS": "1",
        "COMMAND": "ExecStart",
        "__MONOTONIC_TIMESTAMP": str(classifier.SERVICE_EXIT_MONOTONIC_US - 1),
    }
    result_entry = {
        "MESSAGE_ID": "d9b373ed55a64feb8242e02dbe79a49c",
        "MESSAGE": "failed with result exit-code",
        "USER_UNIT": classifier.SERVICE_UNIT,
        "USER_INVOCATION_ID": classifier.SERVICE_INVOCATION_ID,
        "UNIT_RESULT": "exit-code",
        "__MONOTONIC_TIMESTAMP": str(classifier.SERVICE_EXIT_MONOTONIC_US),
    }
    cpu_entry = {
        "MESSAGE_ID": "ae8f7b866b0347b9af31fe1c80b127c0",
        "MESSAGE": "consumed CPU time",
        "USER_UNIT": classifier.SERVICE_UNIT,
    }
    return [start, *process, exit_entry, result_entry, cpu_entry]


def valid_sealed_documents() -> tuple[dict[str, object], dict[str, object]]:
    manifest: dict[str, object] = {"schema": "fixture"}
    certificate: dict[str, object] = {
        "verdict": classifier.VERDICT,
        "p16_reorder_preserved": True,
        "q16_reorder_preserved": True,
        "native_speedup_claim": False,
        "iso_area_claim": False,
        "official_nas_verification": False,
        "full_promotion_claim": False,
        "performance": {
            "lane_1_over_lane_4": classifier.ratio_record(
                classifier.LANE1_SIMTICKS,
                classifier.FIRST_ROI_SIMTICKS,
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


def test_journal_must_be_exact_failed_invocation() -> None:
    terminal = classifier.validate_journal_entries(journal_entries())
    assert terminal["terminal"] is True
    assert terminal["result"] == "exit-code"
    assert terminal["exec_main_status"] == 1
    assert terminal["obsolete_gate_failure_only"] is True

    for index, field, value in (
        (0, "USER_INVOCATION_ID", "0" * 32),
        (1, "_PID", "1"),
        (-2, "UNIT_RESULT", "success"),
    ):
        changed = [dict(item) for item in journal_entries()]
        changed[index][field] = value
        with unittest.TestCase().assertRaisesRegex(
            classifier.CertificateError, "service"
        ):
            classifier.validate_journal_entries(changed)


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


def test_tampered_artifact_and_ledger_are_rejected() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        artifact = root / "artifact"
        artifact.write_text("original", encoding="utf-8")
        expected = hashlib.sha256(artifact.read_bytes()).hexdigest()
        ledger = root / "ledger.sha256"
        ledger.write_text(f"{expected}  artifact\n", encoding="utf-8")
        classifier.verify_ledger(ledger, root, {}, expected_entries=1)
        artifact.write_text("tampered", encoding="utf-8")
        classifier._DIGEST_CACHE.clear()
        with unittest.TestCase().assertRaisesRegex(
            classifier.CertificateError, "ledger mismatch"
        ):
            classifier.verify_ledger(ledger, root, {}, expected_entries=1)
        with unittest.TestCase().assertRaisesRegex(
            classifier.CertificateError, "pinned hash mismatch"
        ):
            classifier.record_expected({}, artifact, expected, "fixture")


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
    assert ratio["numerator"] == 162_849_334_269
    assert ratio["denominator"] == 158_381_418_273
    assert ratio["exact_fraction"] == "162849334269/158381418273"


def test_p16_q16_are_true_and_native_iso_area_claims_are_false() -> None:
    _, certificate = valid_sealed_documents()
    classifier.validate_claims(certificate)
    changed = dict(certificate)
    changed["p16_reorder_preserved"] = False
    with unittest.TestCase().assertRaisesRegex(
        classifier.CertificateError, "p16/q16"
    ):
        classifier.validate_claims(changed)
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


def test_validate_existing_rejects_tamper_and_is_read_only() -> None:
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

        (output / "certificate.json").write_text(
            certificate_text.replace(
                '"iso_area_claim": false', '"iso_area_claim": true'
            )
        )
        classifier._DIGEST_CACHE.clear()
        with mock.patch.object(
            classifier, "RUNS_ROOT", runs
        ), mock.patch.object(
            classifier,
            "audit_inputs",
            return_value=(manifest, certificate, snapshot),
        ):
            with unittest.TestCase().assertRaisesRegex(
                classifier.CertificateError, "sealed certificate"
            ):
                classifier.validate_existing(output)


def test_candidate_and_lane1_certificate_precede_arithmetic() -> None:
    source = inspect.getsource(classifier.audit_inputs)
    candidate = source.index("candidate, deltas = validate_raw_candidate")
    lane1 = source.index("lane1_control = validate_lane1_control")
    documents = source.index("manifest, certificate = build_documents")
    assert candidate < lane1 < documents
    build_source = inspect.getsource(classifier.build_documents)
    assert build_source.index("build_performance") < build_source.index(
        '"schema": MANIFEST_SCHEMA'
    )


def test_classifier_contains_no_gem5_launch_path_and_gates_last() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "run_logged(" not in source
    assert "runner.main(" not in source
    assert '"gem5_runs_launched": 0' in source
    assert '"raw_root_modified": False' in source
    create_source = inspect.getsource(classifier.create_certificate)
    assert create_source.rindex('"gate.complete"') > create_source.index(
        '"input_sha256.txt"'
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
