#!/usr/bin/env python3
"""Repository-local form of the final-review 49-probe adversarial audit."""

import importlib.util
import json
import sys
import unittest
from dataclasses import fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = ROOT / "experiments/analysis/professor_bounded_reorder_policy_model.py"
SPEC = importlib.util.spec_from_file_location(
    "bounded_reorder_adversarial_model", MODEL_PATH
)
MODEL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODEL
SPEC.loader.exec_module(MODEL)


def metric_state(metrics):
    return tuple(getattr(metrics, field.name) for field in fields(MODEL.Metrics))


def observer_state(observer):
    return (
        metric_state(observer.metrics),
        observer.previous_row,
        observer.proof.seen_mask,
        observer.proof.seen_count,
        observer.proof.pattern,
    )


def run_probes():
    results = []

    def record(name, passed, detail):
        results.append({"name": name, "passed": passed, "detail": detail})

    def require_replay_error(name, action, state=None):
        before = state() if state else None
        try:
            action()
        except MODEL.ReplayError as error:
            after = state() if state else None
            record(name, state is None or after == before, str(error))
        except Exception as error:  # pragma: no cover - diagnostic fail path
            record(name, False, f"unexpected {type(error).__name__}: {error}")
        else:
            record(name, False, "accepted invalid value")

    # ACK identity admission, equality forgery, aliasing, and exhaustion.
    invalid_ack_fields = {
        "generation": (True, 1.0, -1, 0, MODEL.UINT64_MAX + 1),
        "serial": (True, 1.0, -1, 0, MODEL.UINT64_MAX + 1),
        "direction": (True, 1.0, -1, 2),
        "line_index": (True, 1.0, -1, MODEL.MAX_TRANSFER_LINE + 1),
    }
    for field_name, bad_values in invalid_ack_fields.items():
        for bad in bad_values:
            ledger = MODEL.AckLedger(1)
            good = ledger.issue(0, 0)
            forged = MODEL.TransferTag(
                good.generation,
                good.serial,
                good.direction,
                good.line_index,
            )
            object.__setattr__(forged, field_name, bad)
            require_replay_error(
                f"ack_{field_name}_{bad!r}",
                lambda forged=forged, ledger=ledger: ledger.complete(forged),
                lambda ledger=ledger: (
                    ledger.generation,
                    ledger.next_serial,
                    ledger.active,
                ),
            )

    ledger = MODEL.AckLedger(MODEL.UINT64_MAX)
    ledger.next_serial = MODEL.UINT64_MAX
    caller_tag = ledger.issue(1, MODEL.MAX_TRANSFER_LINE)
    live_before = ledger.active
    object.__setattr__(caller_tag, "serial", 1)
    record(
        "returned_tag_alias",
        ledger.active == live_before and ledger.active is not caller_tag,
        repr(ledger.active),
    )
    require_replay_error(
        "mutated_returned_tag_rejected_atomically",
        lambda: ledger.complete(caller_tag),
        lambda: (ledger.generation, ledger.next_serial, ledger.active),
    )
    ledger.complete(live_before)
    require_replay_error(
        "zero_exhausted_sentinel_atomic",
        lambda: ledger.issue(0, 0),
        lambda: (ledger.generation, ledger.next_serial, ledger.active),
    )

    # Pattern snapshot and CoverageProof record admission are atomic.
    caller_pattern = [8]
    proof = MODEL.CoverageProof(caller_pattern)
    caller_pattern[0] = 0
    record("coverage_pattern_snapshot", proof.pattern == (8,), repr(proof.pattern))
    invalid_records = (
        [1, 0, 0],
        (True, 0, 0),
        (1.0, 0, 0),
        (-1, 0, 0),
        (MODEL.MAX_SOURCE_LINE + 1, 0, 0),
        (1, True, 0),
        (1, 1.0, 0),
        (1, -1, 0),
        (1, MODEL.WORDS_PER_LINE, 0),
        (1, 0, True),
        (1, 0, 1.0),
        (1, 0, -1),
        (1, 0, MODEL.LOGICAL_ELEMENTS),
    )
    for bad_record in invalid_records:
        require_replay_error(
            f"coverage_record_{bad_record!r}",
            lambda bad_record=bad_record: proof.observe(bad_record),
            lambda: (proof.seen_mask, proof.seen_count, proof.pattern),
        )

    # Queue boundary: reject malformed records before any observer mutation.
    issue_records = (
        ("non_tuple", [0, 0, 0]),
        ("bool_word", (0, True, 0)),
        ("float_word", (0, 1.0, 0)),
        ("negative_word", (0, -1, 0)),
        ("wide_word", (0, MODEL.WORDS_PER_LINE, 0)),
        ("bool_destination", (0, 0, True)),
        ("float_destination", (0, 0, 1.0)),
        ("negative_destination", (0, 0, -1)),
        ("wide_destination", (0, 0, MODEL.LOGICAL_ELEMENTS)),
    )
    for label, bad_record in issue_records:
        metrics = MODEL.Metrics()
        observer = MODEL.IssueObserver([0], 0, metrics)
        require_replay_error(
            f"issue_atomic_{label}",
            lambda bad_record=bad_record, observer=observer: observer.issue(
                0, [bad_record]
            ),
            lambda observer=observer: observer_state(observer),
        )

    metrics = MODEL.Metrics()
    observer = MODEL.IssueObserver([8], 0, metrics)
    require_replay_error(
        "observe_record_bool_source_line_equality_forgery",
        lambda: observer.observe_record(True, (1, 0, 0)),
        lambda: observer_state(observer),
    )

    metrics = MODEL.Metrics()
    observer = MODEL.IssueObserver([0], 0, metrics)
    require_replay_error(
        "empty_issue_atomic",
        lambda: observer.issue(0, []),
        lambda: observer_state(observer),
    )

    metrics = MODEL.Metrics()
    observer = MODEL.IssueObserver([0], 0, metrics)
    observer.issue(0, [(0, 0, 0)])
    require_replay_error(
        "duplicate_issue_atomic",
        lambda: observer.issue(0, [(0, 0, 0)]),
        lambda: observer_state(observer),
    )

    # Maximum legal source, word, destination, phase, and transfer identities.
    max_pattern = [0] * MODEL.LOGICAL_ELEMENTS
    max_pattern[-1] = MODEL.MAX_SOURCE_INDEX
    max_proof = MODEL.CoverageProof(max_pattern)
    max_record = MODEL.make_record(MODEL.LOGICAL_ELEMENTS - 1, MODEL.MAX_SOURCE_INDEX)
    max_proof.observe(max_record)
    record(
        "max_valid_record",
        max_record
        == (
            MODEL.MAX_SOURCE_LINE,
            MODEL.WORDS_PER_LINE - 1,
            MODEL.LOGICAL_ELEMENTS - 1,
        ),
        repr(max_record),
    )
    phase_key = MODEL.bank_row_key(0, MODEL.MAX_SOURCE_LINE)
    record("max_valid_phase", phase_key is not None, repr(phase_key))
    return results


class ProfessorBoundedReorderAdversarialTest(unittest.TestCase):
    def test_all_49_reviewer_probes(self):
        results = run_probes()
        self.assertEqual(len(results), 49)
        for result in results:
            with self.subTest(probe=result["name"]):
                self.assertTrue(result["passed"], result["detail"])


if __name__ == "__main__":
    probe_results = run_probes()
    payload = {
        "passed": sum(result["passed"] for result in probe_results),
        "total": len(probe_results),
        "results": probe_results,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    raise SystemExit(0 if payload["passed"] == payload["total"] else 1)
