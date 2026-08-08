#!/usr/bin/env python3
"""Fail-closed audit of bounded DX100 reorder-survival epoch records."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable


class AuditError(ValueError):
    pass


EPOCH_SCHEMA = "dx100.reorder_epoch.v1"
SUMMARY_SCHEMA = "dx100.reorder_summary.v1"
IDENTITY_FIELDS = {
    "unit",
    "instruction_id",
    "operation_tick",
    "pc",
    "cid",
    "if_id",
    "opcode",
}
EPOCH_FIELDS = IDENTITY_FIELDS | {
    "schema",
    "event",
    "epoch_id",
    "admissions",
    "issued_lines",
    "issued_entries",
    "max_joint_admissions",
    "row_transitions",
    "rt_full_drains",
    "offset_drains",
    "partition_drains",
    "final",
}
SUMMARY_FIELDS = IDENTITY_FIELDS | {
    "schema",
    "event",
    "predicate_present",
    "selected_descriptors",
    "epochs",
    "total_admitted",
    "max_joint_admissions",
    "rt_full_drains",
    "offset_drains",
    "partition_drains",
    "mid_instruction_drains",
    "total_issued_lines",
    "total_issued_entries",
    "row_transitions",
    "reconciled",
    "classification",
}
NUMERIC_FIELDS = (EPOCH_FIELDS | SUMMARY_FIELDS) - {
    "schema",
    "event",
    "classification",
}


def _tokens(raw: str, line_no: int) -> dict[str, str] | None:
    marker = raw.find("schema=dx100.reorder_")
    if marker < 0:
        return None
    fields: dict[str, str] = {}
    for token in raw[marker:].split():
        if "=" not in token:
            raise AuditError(f"line {line_no}: malformed token {token!r}")
        key, value = token.split("=", 1)
        if not key or not value or key in fields:
            raise AuditError(
                f"line {line_no}: duplicate/empty field {token!r}"
            )
        fields[key] = value
    return fields


def _number(value: str, field: str, line_no: int) -> int:
    try:
        result = int(value, 0)
    except ValueError as exc:
        raise AuditError(
            f"line {line_no}: {field} is not an integer: {value!r}"
        ) from exc
    if result < 0:
        raise AuditError(f"line {line_no}: {field} is negative")
    return result


def _records(lines: Iterable[str]) -> list[tuple[int, dict[str, object]]]:
    records: list[tuple[int, dict[str, object]]] = []
    for line_no, raw in enumerate(lines, 1):
        fields = _tokens(raw, line_no)
        if fields is None:
            continue
        schema = fields.get("schema")
        event = fields.get("event")
        if schema == EPOCH_SCHEMA and event == "reorder_epoch":
            required = EPOCH_FIELDS
        elif schema == SUMMARY_SCHEMA and event == "reorder_summary":
            required = SUMMARY_FIELDS
        else:
            raise AuditError(
                f"line {line_no}: unsupported schema/event {schema}/{event}"
            )
        if set(fields) != required:
            missing = sorted(required - set(fields))
            extra = sorted(set(fields) - required)
            raise AuditError(
                f"line {line_no}: field mismatch missing={missing} extra={extra}"
            )
        parsed: dict[str, object] = dict(fields)
        for field in NUMERIC_FIELDS & required:
            parsed[field] = _number(fields[field], field, line_no)
        records.append((line_no, parsed))
    if not records:
        raise AuditError("no reorder-survival records")
    return records


def analyze(trace: Path) -> dict[str, object]:
    if not trace.is_file():
        raise AuditError(f"missing trace: {trace}")
    records = _records(trace.read_text(errors="strict").splitlines())
    epochs: dict[tuple[int, int], list[tuple[int, dict[str, object]]]] = {}
    summaries: dict[tuple[int, int], tuple[int, dict[str, object]]] = {}
    for line_no, record in records:
        key = (int(record["unit"]), int(record["instruction_id"]))
        if record["event"] == "reorder_epoch":
            epochs.setdefault(key, []).append((line_no, record))
        else:
            if key in summaries:
                raise AuditError(f"duplicate summary for instruction {key}")
            summaries[key] = (line_no, record)
    if set(epochs) != set(summaries):
        raise AuditError(
            "epoch/summary instruction sets differ: "
            f"epochs={sorted(epochs)} summaries={sorted(summaries)}"
        )

    instructions: list[dict[str, object]] = []
    for key in sorted(summaries):
        summary_line, summary = summaries[key]
        epoch_rows = epochs[key]
        epoch_rows.sort(key=lambda item: int(item[1]["epoch_id"]))
        expected_ids = list(range(len(epoch_rows)))
        observed_ids = [int(item[1]["epoch_id"]) for item in epoch_rows]
        if observed_ids != expected_ids:
            raise AuditError(
                f"instruction {key}: non-contiguous epoch IDs {observed_ids}"
            )
        for index, (line_no, epoch) in enumerate(epoch_rows):
            for field in IDENTITY_FIELDS:
                if epoch[field] != summary[field]:
                    raise AuditError(
                        f"line {line_no}: identity field {field} differs "
                        f"from summary line {summary_line}"
                    )
            final = int(epoch["final"])
            if final not in (0, 1) or final != (index == len(epoch_rows) - 1):
                raise AuditError(f"line {line_no}: invalid final epoch marker")
            boundary_drains = sum(
                int(epoch[field])
                for field in (
                    "offset_drains",
                    "partition_drains",
                )
            )
            if (index != len(epoch_rows) - 1 and boundary_drains != 1) or (
                index == len(epoch_rows) - 1 and boundary_drains != 0
            ):
                raise AuditError(
                    f"line {line_no}: epoch boundary has "
                    f"{boundary_drains} Offset/partition drain reasons"
                )
            if int(epoch["admissions"]) != int(epoch["issued_entries"]):
                raise AuditError(
                    f"line {line_no}: epoch admitted/issued mismatch"
                )
            if int(epoch["max_joint_admissions"]) > int(epoch["admissions"]):
                raise AuditError(
                    f"line {line_no}: epoch joint visibility exceeds admissions"
                )

        def epoch_sum(field: str) -> int:
            return sum(int(item[1][field]) for item in epoch_rows)

        reconciliations = {
            "epochs": len(epoch_rows),
            "total_admitted": epoch_sum("admissions"),
            "total_issued_lines": epoch_sum("issued_lines"),
            "total_issued_entries": epoch_sum("issued_entries"),
            "row_transitions": epoch_sum("row_transitions"),
            "rt_full_drains": epoch_sum("rt_full_drains"),
            "offset_drains": epoch_sum("offset_drains"),
            "partition_drains": epoch_sum("partition_drains"),
            "max_joint_admissions": max(
                int(item[1]["max_joint_admissions"]) for item in epoch_rows
            ),
        }
        for field, value in reconciliations.items():
            if int(summary[field]) != value:
                raise AuditError(
                    f"instruction {key}: summary {field}={summary[field]} "
                    f"does not reconcile to epochs={value}"
                )
        mid_drains = (
            reconciliations["rt_full_drains"]
            + reconciliations["offset_drains"]
            + reconciliations["partition_drains"]
        )
        if int(summary["mid_instruction_drains"]) != mid_drains:
            raise AuditError(f"instruction {key}: mid-drain total mismatch")
        if int(summary["reconciled"]) != 1:
            raise AuditError(
                f"instruction {key}: simulator marked unreconciled"
            )
        if (
            reconciliations["total_admitted"]
            != reconciliations["total_issued_entries"]
        ):
            raise AuditError(f"instruction {key}: admitted/issued mismatch")
        if (
            int(summary["selected_descriptors"])
            != reconciliations["total_admitted"]
        ):
            raise AuditError(f"instruction {key}: selected/admitted mismatch")

        preserved = (
            int(summary["predicate_present"]) == 0
            and int(summary["selected_descriptors"]) == 16384
            and reconciliations["total_admitted"] == 16384
            and reconciliations["max_joint_admissions"] == 16384
            and len(epoch_rows) == 1
            and mid_drains == 0
        )
        expected_classification = (
            "preserved" if preserved else "inherited/partitioned"
        )
        if summary["classification"] != expected_classification:
            raise AuditError(
                f"instruction {key}: classification {summary['classification']!r} "
                f"must be {expected_classification!r}"
            )
        instructions.append(
            {
                **{field: summary[field] for field in sorted(IDENTITY_FIELDS)},
                **reconciliations,
                "predicate_present": summary["predicate_present"],
                "selected_descriptors": summary["selected_descriptors"],
                "mid_instruction_drains": mid_drains,
                "classification": expected_classification,
                "epochs_detail": [item[1] for item in epoch_rows],
            }
        )

    return {
        "schema": "dx100.reorder_survival_audit.v1",
        "status": "PASS",
        "trace": str(trace.resolve()),
        "trace_sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
        "instruction_count": len(instructions),
        "claim_rule": (
            "A 16K reorder-preservation claim requires exactly 16384 admitted "
            "selected descriptors in one epoch, no predicate, and zero "
            "mid-instruction drains; "
            "otherwise the result is inherited/partitioned."
        ),
        "instructions": instructions,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = analyze(args.trace)
    except (AuditError, OSError, UnicodeError) as exc:
        parser.error(str(exc))
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
