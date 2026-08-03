#!/usr/bin/env python3
"""Fail-closed parser for the matched hybrid-overhead attribution pair."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import (
    Counter,
    defaultdict,
)
from pathlib import Path
from typing import Iterable

PHYSICAL_SCHEMA = "dx100.physical_admission.v1"
ATTRIBUTION_SCHEMA = "2"
UINT64_MAX = (1 << 64) - 1
BASELINE_COMMIT = "d7875f99e6caf1d47bd6010b89112458384aec6c"
PHYSICAL_FIELDS = {
    "schema",
    "event",
    "itr",
    "b_paddr",
    "b_value",
    "a_paddr",
    "a_line_paddr",
    "channel",
    "rank",
    "bank_group",
    "bank",
    "row",
    "column",
    "native_slice",
    "grow_addr",
    "wid",
    "generation_available",
    "generation",
    "opcode",
    "optype",
    "if_id",
    "cid",
    "pc",
    "operation_tick",
    "controller_managed",
    "controller_action",
    "controller_transaction",
    "controller_page",
    "rt_config",
    "aperture_slice_begin",
    "aperture_slice_end",
    "aperture_slices",
    "provenance",
}

EVENT_FIELDS = {
    "indirect_execute": {
        "schema",
        "event",
        "unit",
        "operation_tick",
        "sequence",
        "state",
        "itr",
    },
    "indirect_stage_begin": {
        "schema",
        "event",
        "unit",
        "operation_tick",
        "stage",
        "reason",
    },
    "indirect_stage_interval": {
        "schema",
        "event",
        "unit",
        "operation_tick",
        "stage",
        "start",
        "end",
        "sim_ticks",
        "cycles",
        "reason",
    },
    "indirect_stage_summary": {
        "schema",
        "event",
        "unit",
        "operation_tick",
        "decode_sim_ticks",
        "fill_sim_ticks",
        "build_sim_ticks",
        "request_sim_ticks",
        "response_sim_ticks",
        "total_sim_ticks",
    },
    "indirect_stall": None,
    "index_line_issue": {
        "schema",
        "event",
        "unit",
        "operation_tick",
        "line",
        "first_itr",
        "words",
        "merged",
    },
    "index_line_response": {
        "schema",
        "event",
        "unit",
        "operation_tick",
        "line",
        "words",
        "cached",
    },
    "source_issue": {
        "schema",
        "event",
        "unit",
        "operation_tick",
        "sequence",
        "addr",
        "bounded",
        "virtual",
    },
    "source_response": {
        "schema",
        "event",
        "unit",
        "operation_tick",
        "addr",
        "head",
        "words",
        "cached",
    },
    "backing_write_issue": {
        "schema",
        "event",
        "unit",
        "operation_tick",
        "key",
        "vaddr",
        "paddr",
        "bytes",
        "valid_words",
        "outstanding",
    },
    "backing_write_complete": {
        "schema",
        "event",
        "unit",
        "operation_tick",
        "key",
        "outstanding",
    },
    "indirect_counter_summary": {
        "schema",
        "event",
        "unit",
        "operation_tick",
        "row_attempts",
        "row_successes",
        "offset_pressure",
        "row_pressure",
        "source_issues",
        "source_responses",
        "combiner_words",
        "write_issues",
        "write_completions",
    },
    "page_ready": {
        "schema",
        "event",
        "unit",
        "operation_tick",
        "page",
        "pages",
        "scanned",
        "expected",
        "issued",
        "completed",
        "sources_drained",
    },
    "transparent_submit": {
        "schema",
        "event",
        "token",
        "physical",
        "output",
        "generation",
        "logical",
        "page",
        "pages",
    },
    "transparent_backpressure": {
        "schema",
        "event",
        "page",
        "action",
        "action_name",
        "reason",
    },
    "transparent_issue": {
        "schema",
        "event",
        "generation",
        "page",
        "action",
        "action_name",
        "offset",
        "elements",
        "dependency",
    },
    "transparent_complete": {
        "schema",
        "event",
        "generation",
        "page",
        "action",
        "action_name",
    },
    "transparent_retire": {"schema", "event", "generation", "pages"},
}

# Every repeatable attribution event carries a source-generated occurrence
# number. Indirect-unit and transparent-controller counters have independent
# namespaces; neither trace line order nor neighboring records supplies
# identity.
for _event_fields in EVENT_FIELDS.values():
    if _event_fields is not None:
        _event_fields.add("occurrence")

ARTIFACT_LABELS = (
    "gem5_binary",
    "workload_binary",
    "se_config",
    "ramulator_config",
    "ramulator_library",
    "ramulator_provenance",
    "dynamic_link_audit",
    "runner",
    "workload_source",
    "maa_api",
    "indirect_cc",
    "indirect_hh",
    "transparent_controller_hh",
    "maa_cc",
    "maa_hh",
    "parser",
    "if_cc",
    "if_hh",
    "cpu_side_port_cc",
    "source_diff",
    "source_status",
    "invocation",
)


class AuditError(ValueError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_int(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as exc:
        raise AuditError(f"invalid integer {value!r}") from exc


def parse_canonical_uint64(value: str, field: str) -> int:
    if re.fullmatch(r"0|[1-9][0-9]*", value) is None:
        raise AuditError(f"{field} is not canonical unsigned decimal")
    number = int(value, 10)
    if number > UINT64_MAX:
        raise AuditError(f"{field} exceeds uint64")
    return number


def parse_payload(payload: str, line_no: int) -> dict[str, str]:
    fields: dict[str, str] = {}
    for token in payload.split():
        if "=" not in token:
            raise AuditError(f"line {line_no}: malformed token {token!r}")
        key, value = token.split("=", 1)
        if not key or not value or key in fields:
            raise AuditError(
                f"line {line_no}: invalid/duplicate field {key!r}"
            )
        fields[key] = value
    return fields


def iter_trace(path: Path) -> Iterable[tuple[int, int, str, dict[str, str]]]:
    prefix = re.compile(r"^(\d+): ([^:]+): (.*)$")
    event_token = re.compile(r"(?:^| )event=([^ ]+)(?: |$)")
    with path.open(encoding="utf-8") as stream:
        for line_no, raw in enumerate(stream, 1):
            raw = raw.rstrip("\n")
            event_match = event_token.search(raw)
            recognized_target = event_match is not None and (
                event_match.group(1) in EVENT_FIELDS
                or event_match.group(1) == "physical_admission"
            )
            if "schema=" not in raw and not recognized_target:
                continue
            match = prefix.fullmatch(raw)
            if match is None:
                raise AuditError(f"line {line_no}: malformed trace prefix")
            tick = int(match.group(1))
            fields = parse_payload(match.group(3), line_no)
            yield line_no, tick, match.group(3), fields


def validate_physical(
    path: Path,
    expected: int,
    aperture: int,
    records_output: Path | None = None,
) -> dict:
    records = []
    canonical = hashlib.sha256()
    itrs: set[int] = set()
    operation_ticks: set[int] = set()
    generation_available: Counter[int] = Counter()
    for line_no, tick, payload, fields in iter_trace(path):
        is_physical = (
            fields.get("schema") == PHYSICAL_SCHEMA
            or fields.get("event") == "physical_admission"
        )
        if not is_physical:
            continue
        if set(fields) != PHYSICAL_FIELDS:
            missing = sorted(PHYSICAL_FIELDS - set(fields))
            extra = sorted(set(fields) - PHYSICAL_FIELDS)
            message = f"physical fields missing={missing} extra={extra}"
            raise AuditError(f"line {line_no}: {message}")
        if (
            fields["schema"] != PHYSICAL_SCHEMA
            or fields["event"] != "physical_admission"
        ):
            raise AuditError(f"line {line_no}: wrong physical schema/event")
        itr = parse_int(fields["itr"])
        if not 0 <= itr < expected or itr in itrs:
            raise AuditError(
                f"line {line_no}: duplicate/out-of-range itr {itr}"
            )
        itrs.add(itr)
        native_slice = parse_int(fields["native_slice"])
        begin = parse_int(fields["aperture_slice_begin"])
        end = parse_int(fields["aperture_slice_end"])
        slices = parse_int(fields["aperture_slices"])
        if (begin, end, slices) != (0, aperture, aperture):
            raise AuditError(f"line {line_no}: aperture is not [0,{aperture})")
        if not begin <= native_slice < end:
            raise AuditError(f"line {line_no}: native slice out of aperture")
        wid = parse_int(fields["wid"])
        a_paddr = parse_int(fields["a_paddr"])
        a_line = parse_int(fields["a_line_paddr"])
        b_paddr = parse_int(fields["b_paddr"])
        if not 0 <= wid < 8 or a_paddr != a_line + wid * 8:
            raise AuditError(f"line {line_no}: A line/wid relation is invalid")
        if a_line % 64 or b_paddr % 4:
            raise AuditError(
                f"line {line_no}: physical address alignment is invalid"
            )
        for key in (
            "channel",
            "rank",
            "bank_group",
            "bank",
            "row",
            "column",
            "grow_addr",
            "b_value",
            "if_id",
            "cid",
            "operation_tick",
        ):
            if parse_int(fields[key]) < 0:
                raise AuditError(f"line {line_no}: negative {key}")
        available = parse_int(fields["generation_available"])
        generation = parse_int(fields["generation"])
        if (
            available not in (0, 1)
            or (available == 0 and generation != 0)
            or (available == 1 and generation == 0)
        ):
            raise AuditError(
                f"line {line_no}: invalid generation availability"
            )
        if fields["provenance"] != "direct_index_descriptor_admission":
            raise AuditError(f"line {line_no}: invalid provenance")
        operation_ticks.add(parse_int(fields["operation_tick"]))
        generation_available[available] += 1
        canonical.update((payload + "\n").encode())
        records.append(
            {
                "trace_line": line_no,
                "sim_tick": tick,
                **{key: fields[key] for key in sorted(PHYSICAL_FIELDS)},
            }
        )
    if len(records) != expected or itrs != set(range(expected)):
        raise AuditError(
            f"physical record count/domain is {len(records)}/{expected}"
        )
    result = {
        "schema": PHYSICAL_SCHEMA,
        "record_count": len(records),
        "field_count": len(PHYSICAL_FIELDS),
        "record_sha256": canonical.hexdigest(),
        "operation_ticks": sorted(operation_ticks),
        "generation": {
            "available_records": generation_available[1],
            "unavailable_records": generation_available[0],
            "unavailable_is_explicit": True,
        },
        "aperture": {
            "slice_begin": 0,
            "slice_end": aperture,
            "slices": aperture,
        },
        "trace_path": str(path.resolve()),
        "trace_sha256": sha256(path),
    }
    if records_output is not None:
        ordered = sorted(records, key=lambda record: parse_int(record["itr"]))
        encoded = "".join(
            json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            for record in ordered
        )
        records_output.write_text(encoded)
        result["records"] = {
            "format": "jsonl",
            "order": "logical_itr_ascending",
            "path": str(records_output.resolve()),
            "sha256": sha256(records_output),
            "record_count": len(ordered),
        }
    return result


def strict_events(path: Path) -> list[dict]:
    events = []
    seen = set()
    next_occurrence = defaultdict(int)
    last_scope_tick = {}
    next_execute_sequence = defaultdict(int)
    execute_ticks = {}
    next_source_issue_sequence = defaultdict(int)
    for line_no, tick, payload, fields in iter_trace(path):
        if fields.get("schema") == PHYSICAL_SCHEMA:
            continue
        if fields.get("schema") != ATTRIBUTION_SCHEMA:
            raise AuditError(f"line {line_no}: unknown versioned schema")
        name = fields.get("event")
        if name not in EVENT_FIELDS:
            raise AuditError(
                f"line {line_no}: unknown versioned event {name!r}"
            )
        expected = EVENT_FIELDS[name]
        if expected is None:
            required = {
                "schema",
                "event",
                "unit",
                "occurrence",
                "operation_tick",
                "sequence",
                "reason",
                "itr",
            }
            allowed = required | {"occupancy", "limit", "slice", "grow"}
            if not required <= set(fields) or not set(fields) <= allowed:
                raise AuditError(f"line {line_no}: malformed stall fields")
        elif set(fields) != expected:
            missing = sorted(expected - set(fields))
            extra = sorted(set(fields) - expected)
            message = f"{name} fields missing={missing} extra={extra}"
            raise AuditError(f"line {line_no}: {message}")
        identity = (tick, payload)
        if identity in seen:
            raise AuditError(f"line {line_no}: duplicate versioned event")
        seen.add(identity)
        if "unit" in fields:
            unit = parse_canonical_uint64(fields["unit"], "unit")
            source_scope = ("indirect", unit)
            operation_tick = parse_canonical_uint64(
                fields["operation_tick"], "operation_tick"
            )
            if operation_tick > tick:
                raise AuditError(
                    f"line {line_no}: operation_tick follows event"
                )
            operation_scope = (unit, operation_tick)
        else:
            source_scope = ("transparent-controller",)
            operation_scope = None
        occurrence = parse_canonical_uint64(fields["occurrence"], "occurrence")
        expected_occurrence = next_occurrence[source_scope]
        if occurrence != expected_occurrence:
            raise AuditError(
                f"line {line_no}: occurrence discontinuity in "
                f"{source_scope}: expected {expected_occurrence}, "
                f"got {occurrence}"
            )
        previous_tick = last_scope_tick.get(source_scope)
        if previous_tick is not None and tick < previous_tick:
            raise AuditError(
                f"line {line_no}: source events out of tick order"
            )
        next_occurrence[source_scope] += 1
        last_scope_tick[source_scope] = tick
        if name == "indirect_execute":
            sequence = parse_canonical_uint64(fields["sequence"], "sequence")
            expected_sequence = next_execute_sequence[operation_scope]
            if sequence != expected_sequence:
                raise AuditError(
                    f"line {line_no}: execute sequence discontinuity for "
                    f"{operation_scope}: expected {expected_sequence}, "
                    f"got {sequence}"
                )
            if sequence == 0 and fields["state"] != "Idle":
                raise AuditError(
                    f"line {line_no}: operation does not start in Idle"
                )
            next_execute_sequence[operation_scope] += 1
            execute_ticks[(operation_scope, sequence)] = tick
        elif name == "indirect_stall":
            sequence = parse_canonical_uint64(fields["sequence"], "sequence")
            parent_tick = execute_ticks.get((operation_scope, sequence))
            if parent_tick is None or parent_tick != tick:
                raise AuditError(
                    f"line {line_no}: stall has no same-tick owning execute"
                )
        elif name == "source_issue":
            sequence = parse_canonical_uint64(fields["sequence"], "sequence")
            expected_sequence = next_source_issue_sequence[operation_scope]
            if sequence != expected_sequence:
                raise AuditError(
                    f"line {line_no}: source issue sequence discontinuity"
                )
            next_source_issue_sequence[operation_scope] += 1
        events.append({"line": line_no, "sim_tick": tick, **fields})
    if not events:
        raise AuditError("no versioned attribution events")
    return events


def indirect_scope(event: dict) -> tuple[int, int]:
    try:
        unit = parse_canonical_uint64(event["unit"], "unit")
        operation_tick = parse_canonical_uint64(
            event["operation_tick"], "operation_tick"
        )
    except KeyError as exc:
        raise AuditError("indirect event lacks unit/operation scope") from exc
    return unit, operation_tick


def reconcile_counter_events(events: list[dict]) -> list[dict]:
    summaries = {}
    counts = defaultdict(Counter)
    counted_events = {
        "source_issue": "source_issues",
        "source_response": "source_responses",
        "backing_write_issue": "write_issues",
        "backing_write_complete": "write_completions",
    }
    for event in events:
        name = event["event"]
        if name == "indirect_counter_summary":
            scope = indirect_scope(event)
            if scope in summaries:
                raise AuditError(f"duplicate counter summary for {scope}")
            summaries[scope] = event
        elif name in counted_events:
            counts[indirect_scope(event)][counted_events[name]] += 1
        elif name == "indirect_stall":
            reason = event["reason"]
            if reason == "offset_epoch_full":
                counts[indirect_scope(event)]["offset_pressure"] += 1
            elif reason == "row_table_full":
                counts[indirect_scope(event)]["row_pressure"] += 1
    if not summaries:
        raise AuditError("missing indirect counter summaries")
    unexpected_scopes = set(counts) - set(summaries)
    if unexpected_scopes:
        raise AuditError(
            f"counter events lack same-scope summary: {sorted(unexpected_scopes)}"
        )
    counter_fields = (
        "source_issues",
        "source_responses",
        "write_issues",
        "write_completions",
        "offset_pressure",
        "row_pressure",
    )
    for scope, summary in summaries.items():
        for field in counter_fields:
            recorded = parse_canonical_uint64(summary[field], field)
            if recorded != counts[scope][field]:
                raise AuditError(f"{field} counter/event mismatch for {scope}")
        attempts = parse_canonical_uint64(
            summary["row_attempts"], "row_attempts"
        )
        successes = parse_canonical_uint64(
            summary["row_successes"], "row_successes"
        )
        if attempts != successes + counts[scope]["row_pressure"]:
            raise AuditError(f"row attempts do not reconcile for {scope}")
        parse_canonical_uint64(summary["combiner_words"], "combiner_words")
    return [summaries[scope] for scope in sorted(summaries)]


def first_stats(path: Path) -> dict[str, int | float]:
    values: dict[str, int | float] = {}
    active = False
    with path.open(encoding="utf-8") as stream:
        for raw in stream:
            if raw.startswith("---------- Begin Simulation Statistics"):
                if active:
                    raise AuditError("nested statistics section")
                active = True
                continue
            if (
                raw.startswith("---------- End Simulation Statistics")
                and active
            ):
                return values
            if not active or not raw.strip() or raw.startswith("#"):
                continue
            parts = raw.split()
            if len(parts) >= 2 and parts[1] not in ("nan", "-nan"):
                try:
                    number = float(parts[1])
                except ValueError:
                    continue
                values[parts[0]] = (
                    int(number) if number.is_integer() else number
                )
    raise AuditError("missing complete first statistics section")


def sum_suffix(stats: dict, suffix: str) -> int:
    return sum(
        int(value) for key, value in stats.items() if key.endswith(suffix)
    )


def read_result(path: Path) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(rows) != 1 or None in rows[0]:
        raise AuditError(
            f"{path}: expected exactly one rectangular result row"
        )
    return rows[0]


def read_key_values(path: Path) -> dict[str, str]:
    values = {}
    for line_no, raw in enumerate(path.read_text().splitlines(), 1):
        if raw.count("=") != 1:
            raise AuditError(f"{path}: malformed line {line_no}")
        key, value = raw.split("=", 1)
        if not key or not value or key in values:
            raise AuditError(f"{path}: invalid/duplicate key {key!r}")
        values[key] = value
    return values


def checkpoint_identity(run: Path) -> str:
    identity = run / "shared_checkpoint_identity.sha256"
    lines = identity.read_text().split()
    if (
        len(lines) != 2
        or not re.fullmatch(r"[0-9a-f]{64}", lines[0])
        or Path(lines[1]).name != "shared_checkpoint_files.sha256"
    ):
        raise AuditError(f"{identity}: malformed checkpoint identity")
    manifest = run / "shared_checkpoint_files.sha256"
    if not manifest.is_file() or sha256(manifest) != lines[0]:
        raise AuditError(f"{identity}: checkpoint manifest hash mismatch")
    return lines[0]


def audited_artifacts(path: Path) -> dict[str, dict[str, str]]:
    records = []
    for line_no, raw in enumerate(path.read_text().splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", raw)
        if match is None:
            raise AuditError(f"{path}: malformed artifact line {line_no}")
        artifact = Path(match.group(2))
        if not artifact.is_file() or sha256(artifact) != match.group(1):
            raise AuditError(
                f"{path}: unavailable/changed artifact {artifact}"
            )
        records.append((match.group(1), str(artifact.resolve())))
    if len(records) != len(ARTIFACT_LABELS):
        raise AuditError(
            f"{path}: artifact count {len(records)}/{len(ARTIFACT_LABELS)}"
        )
    return {
        label: {"sha256": digest, "path": artifact}
        for label, (digest, artifact) in zip(ARTIFACT_LABELS, records)
    }


def audit_ramulator_provenance(path: Path, library: dict[str, str]) -> dict:
    data = json.loads(path.read_text())
    required = {
        "schema",
        "outer_tree",
        "source_tree",
        "nested_gitlinks",
        "normalized_dependency_sha256",
        "elf_build_id",
        "frozen_library",
        "reference_worktree",
    }
    if (
        set(data) != required
        or data["schema"] != "dx100.ramulator_provenance.v1"
    ):
        raise AuditError(f"{path}: wrong Ramulator provenance schema/fields")
    for key in ("outer_tree", "source_tree"):
        if not re.fullmatch(r"[0-9a-f]{40}", data[key]):
            raise AuditError(f"{path}: malformed {key}")
    links = data["nested_gitlinks"]
    if set(links) != {"argparse", "spdlog", "yaml-cpp"} or any(
        not re.fullmatch(r"[0-9a-f]{40}", value) for value in links.values()
    ):
        raise AuditError(f"{path}: malformed nested gitlinks")
    if not re.fullmatch(r"[0-9a-f]{64}", data["normalized_dependency_sha256"]):
        raise AuditError(f"{path}: malformed dependency digest")
    if not re.fullmatch(r"[0-9a-f]+", data["elf_build_id"]):
        raise AuditError(f"{path}: malformed ELF BuildID")
    frozen = data["frozen_library"]
    if (
        set(frozen) != {"path", "sha256"}
        or str(Path(frozen["path"]).resolve()) != library["path"]
        or frozen["sha256"] != library["sha256"]
    ):
        raise AuditError(f"{path}: frozen library mismatch")
    return data


def stage_audit(grouped: dict[str, list[dict]], stages: list[dict]) -> dict:
    names = ("decode", "fill", "build", "request", "response")
    summaries = {}
    for stage in stages:
        scope = indirect_scope(stage)
        if scope in summaries:
            raise AuditError(f"duplicate stage summary for {scope}")
        summaries[scope] = stage
    if not summaries:
        raise AuditError("missing stage summaries")
    intervals_by_scope = defaultdict(list)
    for event in grouped["indirect_stage_interval"]:
        intervals_by_scope[indirect_scope(event)].append(event)
    if set(intervals_by_scope) != set(summaries):
        raise AuditError("stage interval/summary scopes differ")
    aggregate_ticks = Counter()
    aggregate_cycles = Counter()
    operations = {}
    total_intervals = 0
    for scope, stage in sorted(summaries.items()):
        intervals = intervals_by_scope[scope]
        totals = Counter()
        cycles_by_stage = Counter()
        ordered = sorted(
            intervals,
            key=lambda event: (
                parse_canonical_uint64(event["start"], "stage start"),
                parse_canonical_uint64(event["end"], "stage end"),
            ),
        )
        previous_end = None
        for event in ordered:
            if event["stage"] not in names:
                raise AuditError("unknown stage interval name")
            start = parse_canonical_uint64(event["start"], "stage start")
            end = parse_canonical_uint64(event["end"], "stage end")
            sim_ticks = parse_canonical_uint64(
                event["sim_ticks"], "stage sim_ticks"
            )
            cycles = parse_canonical_uint64(event["cycles"], "stage cycles")
            if end < start or end - start != sim_ticks:
                raise AuditError("invalid stage interval")
            if previous_end is not None and start < previous_end:
                raise AuditError("overlapping stage intervals")
            previous_end = end
            totals[event["stage"]] += sim_ticks
            cycles_by_stage[event["stage"]] += cycles
        summary = {
            name: parse_canonical_uint64(
                stage[f"{name}_sim_ticks"], f"{name}_sim_ticks"
            )
            for name in names
        }
        if any(totals[name] != summary[name] for name in names):
            raise AuditError(
                f"stage intervals do not reconcile to summary for {scope}"
            )
        total = parse_canonical_uint64(
            stage["total_sim_ticks"], "total_sim_ticks"
        )
        if sum(summary.values()) != total:
            raise AuditError(f"stage summary does not reconcile for {scope}")
        aggregate_ticks.update(summary)
        aggregate_cycles.update(cycles_by_stage)
        total_intervals += len(intervals)
        operations[f"unit={scope[0]},operation_tick={scope[1]}"] = {
            "sim_ticks": summary,
            "cycles": {name: cycles_by_stage[name] for name in names},
            "interval_count": len(intervals),
        }
    return {
        "sim_ticks": {name: aggregate_ticks[name] for name in names},
        "cycles": {name: aggregate_cycles[name] for name in names},
        "interval_count": total_intervals,
        "operation_count": len(operations),
        "operations": operations,
        "intervals_do_not_overlap_within_operation": True,
    }


def fifo_latencies(
    issues: list[dict], responses: list[dict], key: str
) -> dict:
    pending = defaultdict(list)
    issue_counts = Counter()
    response_counts = Counter()
    for event in sorted(
        issues, key=lambda item: (item["sim_tick"], item.get("line", 0))
    ):
        scope = indirect_scope(event)
        pending[(scope, event[key])].append(event["sim_tick"])
        issue_counts[scope] += 1
    latencies = []
    scoped_latencies = defaultdict(list)
    for event in sorted(
        responses, key=lambda item: (item["sim_tick"], item.get("line", 0))
    ):
        scope = indirect_scope(event)
        queue = pending[(scope, event[key])]
        if not queue:
            raise AuditError(
                f"response without same-scope issue for {scope} "
                f"{key}={event[key]}"
            )
        issue_tick = queue.pop(0)
        if event["sim_tick"] < issue_tick:
            raise AuditError(f"response precedes issue for {key}={event[key]}")
        latency = event["sim_tick"] - issue_tick
        latencies.append(latency)
        scoped_latencies[scope].append(latency)
        response_counts[scope] += 1
    if any(queue for queue in pending.values()):
        raise AuditError(f"unmatched same-scope issue for {key}")
    if issue_counts != response_counts:
        raise AuditError(f"per-scope issue/response count mismatch for {key}")
    return {
        "count": len(latencies),
        "total_sim_ticks": sum(latencies),
        "max_sim_ticks": max(latencies, default=0),
        "per_unit_operation": {
            f"unit={scope[0]},operation_tick={scope[1]}": {
                "count": len(values),
                "total_sim_ticks": sum(values),
                "max_sim_ticks": max(values, default=0),
            }
            for scope, values in sorted(scoped_latencies.items())
        },
    }


def controller_audit(grouped: dict[str, list[dict]], case_name: str) -> dict:
    controller_names = (
        "transparent_submit",
        "transparent_backpressure",
        "transparent_issue",
        "transparent_complete",
        "transparent_retire",
    )
    if case_name == "native_direct_16k":
        if any(grouped[name] for name in controller_names):
            raise AuditError(
                "native arm emitted transparent-controller events"
            )
        return {"active": False}
    submits = grouped["transparent_submit"]
    retires = grouped["transparent_retire"]
    issues = grouped["transparent_issue"]
    completions = grouped["transparent_complete"]
    if len(submits) != 1 or len(retires) != 1:
        raise AuditError("transparent submit/retire count mismatch")
    expected_order = [
        (page, action) for page in range(4) for action in range(1, 4)
    ]
    actual_order = [
        (parse_int(event["page"]), parse_int(event["action"]))
        for event in issues
    ]
    if actual_order != expected_order or len(completions) != len(issues):
        raise AuditError("transparent action issue order/count mismatch")
    completion_by_key = {}
    for event in completions:
        key = (parse_int(event["page"]), parse_int(event["action"]))
        if key in completion_by_key:
            raise AuditError("duplicate transparent completion")
        completion_by_key[key] = event
    durations = Counter()
    dependency_gaps = Counter()
    previous_complete = submits[0]["sim_tick"]
    action_names = {1: "fill", 2: "compute", 3: "store"}
    for event, key in zip(issues, expected_order):
        complete = completion_by_key.get(key)
        action = key[1]
        if (
            complete is None
            or event["action_name"] != action_names[action]
            or complete["action_name"] != action_names[action]
        ):
            raise AuditError("transparent action identity mismatch")
        issue_tick = event["sim_tick"]
        complete_tick = complete["sim_tick"]
        if issue_tick < previous_complete or complete_tick < issue_tick:
            raise AuditError("transparent dependency order violation")
        dependency_gaps[action_names[action]] += issue_tick - previous_complete
        durations[action_names[action]] += complete_tick - issue_tick
        previous_complete = complete_tick
    if retires[0]["sim_tick"] < previous_complete:
        raise AuditError("transparent retire precedes final completion")
    return {
        "active": True,
        "action_count": len(issues),
        "action_duration_sim_ticks": dict(durations),
        "dependency_gap_sim_ticks": dict(dependency_gaps),
        "backpressure_events": len(grouped["transparent_backpressure"]),
        "strict_page_action_order": True,
    }


def audit_run(path: Path) -> dict:
    for name in (
        "result.tsv",
        "restore.log",
        "restore.exit",
        "checkpoint.exit",
        "run/stats.txt",
        "run/virtual_trace.log",
        "manifest.txt",
        "artifact_sha256.txt",
        "shared_checkpoint_identity.sha256",
    ):
        if not (path / name).is_file():
            raise AuditError(f"{path}: missing {name}")
    if (path / "restore.exit").read_text().strip() != "0" or (
        path / "checkpoint.exit"
    ).read_text().strip() != "0":
        raise AuditError(f"{path}: nonzero checkpoint/restore exit")
    log = (path / "restore.log").read_text(errors="replace")
    if (
        len(
            re.findall(
                r"^VIRTUAL_TILE_CONSUMER_RESULT .* errors=0$", log, re.M
            )
        )
        != 1
    ):
        raise AuditError(f"{path}: wrong exact-output marker count")
    if len(re.findall(r"^ROI Ended$", log, re.M)) != 1:
        raise AuditError(f"{path}: wrong ROI marker count")
    if re.search(
        r"panic|fatal|assert|abort|segmentation fault|error:", log, re.I
    ):
        raise AuditError(f"{path}: fatal marker")
    result = read_result(path / "result.tsv")
    manifest = read_key_values(path / "manifest.txt")
    if manifest.get("baseline_commit") != BASELINE_COMMIT:
        raise AuditError(f"{path}: wrong baseline commit")
    if not re.fullmatch(r"[0-9a-f]{40}", manifest.get("source_commit", "")):
        raise AuditError(f"{path}: malformed implementation commit")
    stats = first_stats(path / "run/stats.txt")
    trace_path = path / "run/virtual_trace.log"
    events = strict_events(trace_path)
    grouped = defaultdict(list)
    for event in events:
        grouped[event["event"]].append(event)
    try:
        summaries = reconcile_counter_events(events)
    except AuditError as exc:
        raise AuditError(f"{path}: {exc}") from exc
    try:
        stages = stage_audit(grouped, grouped["indirect_stage_summary"])
        controller = controller_audit(grouped, result["case"])
        index_lines = fifo_latencies(
            grouped["index_line_issue"], grouped["index_line_response"], "line"
        )
        sources = fifo_latencies(
            grouped["source_issue"], grouped["source_response"], "addr"
        )
        writes = fifo_latencies(
            grouped["backing_write_issue"],
            grouped["backing_write_complete"],
            "key",
        )
    except AuditError as exc:
        raise AuditError(f"{path}: {exc}") from exc
    physical_path = path / "physical_validation.json"
    if not physical_path.is_file():
        raise AuditError(f"{path}: missing physical_validation.json")
    recorded_physical = json.loads(physical_path.read_text())
    records_metadata = recorded_physical.get("records")
    if (
        not isinstance(records_metadata, dict)
        or "path" not in records_metadata
    ):
        raise AuditError(f"{path}: missing deterministic physical records")
    records_path = Path(records_metadata["path"])
    physical = validate_physical(trace_path, 16384, 16, records_path)
    for key in (
        "schema",
        "record_count",
        "field_count",
        "record_sha256",
        "trace_sha256",
        "aperture",
        "generation",
        "records",
    ):
        if recorded_physical.get(key) != physical[key]:
            raise AuditError(
                f"{path}: stale/invalid physical validation {key}"
            )
    if (
        result["physical_records"] != "16384"
        or result["physical_record_sha256"] != physical["record_sha256"]
    ):
        raise AuditError(f"{path}: result/physical validation mismatch")
    request_cycles = sum_suffix(stats, "IND_CyclesRequest")
    request_reasons = {
        name: sum_suffix(stats, suffix)
        for name, suffix in {
            "build": "IND_VirtRequestCyclesBuild",
            "source_flight": "IND_VirtRequestCyclesSourceFlight",
            "retained": "IND_VirtRequestCyclesRetained",
            "writes": "IND_VirtRequestCyclesWrites",
            "final_drain": "IND_VirtRequestCyclesFinalDrain",
            "runnable": "IND_VirtRequestCyclesRunnable",
        }.items()
    }
    if (
        result["case"] == "transparent_4k"
        and sum(request_reasons.values()) != request_cycles
    ):
        raise AuditError(
            f"{path}: mutually exclusive request cycles do not reconcile"
        )
    artifacts = audited_artifacts(path / "artifact_sha256.txt")
    ramulator_provenance = audit_ramulator_provenance(
        Path(artifacts["ramulator_provenance"]["path"]),
        artifacts["ramulator_library"],
    )
    stall_counts = Counter(
        event["reason"] for event in grouped["indirect_stall"]
    )
    return {
        "path": str(path.resolve()),
        "result": result,
        "manifest": manifest,
        "simTicks": int(stats["simTicks"]),
        "stats": {
            "request_cycles": request_cycles,
            "request_reason_cycles": request_reasons,
        },
        "trace": {
            "event_counts": dict(
                sorted(Counter(e["event"] for e in events).items())
            ),
            "stages": stages,
            "stall_reason_events": dict(sorted(stall_counts.items())),
            "index_line_issue_response": index_lines,
            "source_issue_response": sources,
            "backing_write_issue_completion": writes,
            "controller": controller,
            "counter_summaries": [
                {
                    k: (
                        parse_canonical_uint64(v, k)
                        if k not in {"schema", "event"}
                        else v
                    )
                    for k, v in summary.items()
                }
                for summary in summaries
            ],
            "physical_admission": physical,
        },
        "frozen_artifacts": artifacts,
        "ramulator_provenance": ramulator_provenance,
        "checkpoint_identity_sha256": checkpoint_identity(path),
        "hashes": {
            name: sha256(path / name)
            for name in (
                "result.tsv",
                "restore.log",
                "run/stats.txt",
                "run/virtual_trace.log",
                "manifest.txt",
                "artifact_sha256.txt",
                "treatment.txt",
                "physical_validation.json",
                "physical_admission_records.jsonl",
            )
        },
    }


def analyze_pair(native_path: Path, hybrid_path: Path) -> dict:
    native = audit_run(native_path)
    hybrid = audit_run(hybrid_path)
    if native["result"]["case"] != "native_direct_16k" or (
        hybrid["result"]["case"] != "transparent_4k"
    ):
        raise AuditError("pair cases are not native_direct_16k/transparent_4k")
    for field in ("output_hash",):
        if native["result"][field] != hybrid["result"][field]:
            raise AuditError(f"pair mismatch: {field}")
    if (
        native["checkpoint_identity_sha256"]
        != hybrid["checkpoint_identity_sha256"]
    ):
        raise AuditError("pair did not reuse one identical checkpoint")
    if (native_path / "shared_checkpoint_files.sha256").read_bytes() != (
        hybrid_path / "shared_checkpoint_files.sha256"
    ).read_bytes():
        raise AuditError("pair checkpoint manifests differ")
    if (
        native["manifest"]["source_commit"]
        != hybrid["manifest"]["source_commit"]
    ):
        raise AuditError("pair implementation commits differ")
    comparable_artifacts = set(ARTIFACT_LABELS) - {"invocation"}
    for label in comparable_artifacts:
        if (
            native["frozen_artifacts"][label]["sha256"]
            != hybrid["frozen_artifacts"][label]["sha256"]
        ):
            raise AuditError(f"pair frozen artifact mismatch: {label}")
    native_ticks = native["simTicks"]
    hybrid_ticks = hybrid["simTicks"]
    delta = hybrid_ticks - native_ticks
    reason_delta = {
        key: hybrid["stats"]["request_reason_cycles"][key]
        - native["stats"]["request_reason_cycles"][key]
        for key in hybrid["stats"]["request_reason_cycles"]
    }
    largest = max(reason_delta, key=reason_delta.get)
    common_artifacts = {
        label: native["frozen_artifacts"][label]
        for label in ARTIFACT_LABELS
        if label != "invocation"
    }
    return {
        "schema": "dx100.hybrid_overhead_attribution.v1",
        "units": {
            "end_to_end": "simTicks",
            "maa_stage_intervals": "simTicks",
            "maa_request_categories": "cycles",
        },
        "pair": {"native": native, "hybrid": hybrid},
        "comparison": {
            "simTicks_delta": delta,
            "overhead_percent": 100.0 * delta / native_ticks,
            "request_reason_cycle_delta": reason_delta,
            "largest_actionable_request_category": largest,
            "largest_actionable_request_category_delta_cycles": reason_delta[
                largest
            ],
            "request_categories_are_mutually_exclusive": True,
            "stage_and_pipeline_views_must_not_be_added": True,
        },
        "provenance": {
            "baseline_commit": BASELINE_COMMIT,
            "implementation_commit": native["manifest"]["source_commit"],
            "checkpoint_identity_sha256": native["checkpoint_identity_sha256"],
            "exact_output_oracle_hash": native["result"]["output_hash"],
            "common_frozen_artifacts": common_artifacts,
            "invocations": {
                "native": native["frozen_artifacts"]["invocation"],
                "hybrid": hybrid["frozen_artifacts"]["invocation"],
            },
            "physical_admission_records": {
                "native": native["trace"]["physical_admission"]["records"],
                "hybrid": hybrid["trace"]["physical_admission"]["records"],
            },
            "raw_hashes": {
                "native": native["hashes"],
                "hybrid": hybrid["hashes"],
            },
        },
        "gates": {
            "exact_output": True,
            "one_identical_checkpoint": True,
            "checkpoint_manifest_byte_identical": True,
            "one_frozen_binary_config_input_and_sources": True,
            "strict_versioned_trace": True,
            "strict_physical_record_domain": True,
            "first_roi_stats": True,
        },
    }


def render_markdown(result: dict) -> str:
    native = result["pair"]["native"]
    hybrid = result["pair"]["hybrid"]
    comp = result["comparison"]
    actionable_delta = comp["largest_actionable_request_category_delta_cycles"]
    lines = [
        "# Hybrid 16K-Reorder / 4K-Payload Overhead Attribution",
        "",
        "## Result",
        "",
        f"Native direct16: **{native['simTicks']:,} simTicks**.  Transparent "
        f"4K payload: **{hybrid['simTicks']:,} simTicks**.  Delta: "
        f"**{comp['simTicks_delta']:,} simTicks "
        f"({comp['overhead_percent']:.6f}%)**.",
        "",
        "The largest mutually exclusive MAA request category increase is "
        f"`{comp['largest_actionable_request_category']}` at "
        f"{actionable_delta:,} cycles.",
        "",
        "## Request-cycle reconciliation",
        "",
        "| Category | Native cycles | Hybrid cycles | Delta cycles |",
        "|---|---:|---:|---:|",
    ]
    for key, delta in comp["request_reason_cycle_delta"].items():
        lines.append(
            f"| {key} | {native['stats']['request_reason_cycles'][key]:,} | "
            f"{hybrid['stats']['request_reason_cycles'][key]:,} | {delta:+,} |"
        )
    lines += [
        "",
        (
            "These request categories are mutually exclusive and sum to the "
            "hybrid request-cycle total. Stage intervals and pipeline-overlap "
            "views are alternate views and are not added to them."
        ),
        "",
        "## Provenance and gates",
        "",
        f"Both restores use checkpoint identity "
        f"`{native['checkpoint_identity_sha256']}` and exact output hash "
        f"`{native['result']['output_hash']}`. Completion, first-ROI stats, "
        "versioned trace schemas, physical-record domains, event/counter "
        "reconciliation, and raw hashes were checked fail closed.",
        "",
        f"Frozen gem5 SHA-256: "
        f"`{native['frozen_artifacts']['gem5_binary']['sha256']}`  ",
        f"Frozen workload SHA-256: "
        f"`{native['frozen_artifacts']['workload_binary']['sha256']}`  ",
        f"Frozen se.py SHA-256: "
        f"`{native['frozen_artifacts']['se_config']['sha256']}`  ",
        f"Frozen Ramulator config SHA-256: "
        f"`{native['frozen_artifacts']['ramulator_config']['sha256']}`",
        "",
        f"Native raw path: `{native['path']}`  ",
        f"Hybrid raw path: `{hybrid['path']}`",
        "",
    ]
    return "\n".join(lines)


def write_json(data: dict, path: Path | None) -> None:
    encoded = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if path is None:
        print(encoded, end="")
    else:
        path.write_text(encoded)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    physical = subs.add_parser("validate-physical")
    physical.add_argument("trace", type=Path)
    physical.add_argument("--expected-records", type=int, required=True)
    physical.add_argument("--aperture-slices", type=int, required=True)
    physical.add_argument("--output", type=Path)
    physical.add_argument("--records-output", type=Path)
    pair = subs.add_parser("analyze-pair")
    pair.add_argument("native", type=Path)
    pair.add_argument("hybrid", type=Path)
    pair.add_argument("--json-output", type=Path)
    pair.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "validate-physical":
            result = validate_physical(
                args.trace,
                args.expected_records,
                args.aperture_slices,
                args.records_output,
            )
            write_json(result, args.output)
        else:
            result = analyze_pair(args.native, args.hybrid)
            write_json(result, args.json_output)
            markdown = render_markdown(result)
            if args.markdown_output is None:
                print(markdown)
            else:
                args.markdown_output.write_text(markdown)
    except (AuditError, KeyError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
