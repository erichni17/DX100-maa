#!/usr/bin/env python3
"""Fail-closed audit for bounded descriptor-spool replay read-ahead."""

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
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Iterable,
    Sequence,
)

EXPECTED_PASSES = (1, 2, 3)
EXPECTED_LINES_PER_PASS = 384
EXPECTED_DESCRIPTOR_LINES = 1152
MAX_READ_CREDITS = 32
HEX64 = re.compile(r"[0-9a-f]{64}")


class AuditError(RuntimeError):
    """Raised when evidence does not close the overlap contract."""


@dataclass(frozen=True)
class Event:
    line_number: int
    name: str
    fields: dict[str, str]


def fail(message: str) -> None:
    raise AuditError(message)


def parse_int(value: str, context: str) -> int:
    try:
        return int(value, 0)
    except (TypeError, ValueError) as error:
        raise AuditError(f"{context} is not an integer: {value!r}") from error


def require_int(mapping: dict[str, str], name: str, context: str) -> int:
    if name not in mapping:
        fail(f"{context} is missing {name}")
    return parse_int(mapping[name], f"{context}.{name}")


def read_manifest(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw or raw.startswith("#"):
            continue
        if "=" not in raw:
            fail(f"manifest line {line_number} is not key=value")
        key, value = raw.split("=", 1)
        if not key or key in values:
            fail(f"manifest key is empty or duplicated: {key!r}")
        values[key] = value
    return values


def read_result(path: Path) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.reader(stream, delimiter="\t"))
    if len(rows) != 2 or not rows[0] or len(rows[0]) != len(rows[1]):
        fail("result TSV must contain one header and one equally sized row")
    if len(set(rows[0])) != len(rows[0]):
        fail("result TSV contains duplicate columns")
    return dict(zip(rows[0], rows[1], strict=True))


def parse_events(path: Path) -> list[Event]:
    events: list[Event] = []
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(),
        start=1,
    ):
        tokens = raw.split()
        fields: dict[str, str] = {}
        for token in tokens:
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            if key in fields:
                fail(f"trace line {line_number} duplicates field {key}: {raw}")
            fields[key] = value.rstrip(",")
        name = fields.get("event")
        if name:
            events.append(Event(line_number, name, fields))
    return events


def select(events: Iterable[Event], name: str) -> list[Event]:
    return [event for event in events if event.name == name]


def only(events: Iterable[Event], name: str) -> Event:
    selected = select(events, name)
    if len(selected) != 1:
        fail(f"expected exactly one {name} event, found {len(selected)}")
    return selected[0]


def event_int(event: Event, name: str) -> int:
    return require_int(
        event.fields, name, f"trace line {event.line_number} {event.name}"
    )


def event_key(event: Event) -> tuple[int, int, int, int]:
    return (
        event_int(event, "unit"),
        event_int(event, "operation_tick"),
        event_int(event, "pass"),
        event_int(event, "line"),
    )


def require_exact_ints(
    mapping: dict[str, str], expected: dict[str, int], context: str
) -> None:
    for name, expected_value in expected.items():
        actual = require_int(mapping, name, context)
        if actual != expected_value:
            fail(f"{context}.{name} is {actual}, expected {expected_value}")


def validate_common_evidence(
    manifest: dict[str, str],
    result: dict[str, str],
    events: list[Event],
    enabled: bool,
    expected_case: str,
) -> tuple[Event, int]:
    expected_knob = 1 if enabled else 0
    read_credits = require_int(
        manifest, "virtual_descriptor_spool_read_credits", "manifest"
    )
    if not 1 <= read_credits <= MAX_READ_CREDITS:
        fail(
            "manifest.virtual_descriptor_spool_read_credits is outside "
            f"[1,{MAX_READ_CREDITS}]"
        )
    require_exact_ints(
        manifest,
        {
            "logical_tile_elements": 16384,
            "physical_tile_elements": 4096,
            "virtual_index_descriptor_spool": 1,
            "virtual_descriptor_spool_read_ahead": expected_knob,
            "virtual_descriptor_spool_read_credits": read_credits,
        },
        "manifest",
    )
    if result.get("case") != expected_case:
        fail(f"result.case is not {expected_case}: {result.get('case')!r}")
    require_exact_ints(
        result,
        {
            "virtual_index_descriptor_spool": 1,
            "virtual_descriptor_spool_read_ahead": expected_knob,
            "physical_records": 16384,
            "bounded_replay_passes": 4,
            "bounded_replay_words": 0,
            "bounded_bucket_words": 16384,
            "descriptor_spool_b_scans": 2,
            "descriptor_spool_resident_populations": 1,
            "descriptor_spool_resident_descriptors": 4096,
            "descriptor_spool_external_descriptors": 12288,
            "descriptor_spool_external_segments": 3,
            "descriptor_spool_line_writes": EXPECTED_DESCRIPTOR_LINES,
            "descriptor_spool_write_bytes": 73728,
            "descriptor_spool_write_acks": EXPECTED_DESCRIPTOR_LINES,
            "descriptor_spool_line_reads": EXPECTED_DESCRIPTOR_LINES,
            "descriptor_spool_read_bytes": 73728,
            "descriptor_spool_backing_bytes": 73728,
        },
        "result",
    )
    for name in (
        "output_hash",
        "physical_record_sha256",
        "source_issue_sha256",
    ):
        value = result.get(name, "")
        if name == "output_hash":
            if not value.isdigit():
                fail(f"result.{name} is not an unsigned decimal hash")
        elif HEX64.fullmatch(value) is None:
            fail(f"result.{name} is not a lowercase SHA-256")
    for name in (
        "simTicks",
        "source_issue_records",
        "source_issue_requests",
        "descriptor_spool_control_bytes",
    ):
        if require_int(result, name, "result") <= 0:
            fail(f"result.{name} must be positive")
    if require_int(result, "descriptor_spool_control_bytes", "result") > 4096:
        fail("descriptor spool control state exceeds 4096 bytes")
    if require_int(result, "descriptor_spool_write_high_water", "result") > 16:
        fail("descriptor spool write high-water exceeds 16")
    for name in (
        "bounded_word_entries",
        "bounded_offset_entries",
        "bounded_row_directory_entries",
        "bounded_row_line_entries",
        "bounded_replay_max_epoch_admissions",
    ):
        value = require_int(result, name, "result")
        if value <= 0 or value > 4096:
            fail(f"result.{name} is outside the bounded range [1,4096]")
    if require_int(result, "descriptor_spool_write_bytes", "result") != (
        require_int(result, "descriptor_spool_line_writes", "result") * 64
    ):
        fail("descriptor write-line/byte traffic does not close")
    if require_int(result, "descriptor_spool_read_bytes", "result") != (
        require_int(result, "descriptor_spool_line_reads", "result") * 64
    ):
        fail("descriptor read-line/byte traffic does not close")

    complete = only(events, "descriptor_spool_complete")
    if event_int(complete, "schema") != 2:
        fail("descriptor terminal event must use schema 2")
    require_exact_ints(
        complete.fields,
        {
            "b_scans": 2,
            "descriptors": 16384,
            "resident_pass": 0,
            "resident_descriptors": 4096,
            "external_descriptors": 12288,
            "external_segments": 3,
            "descriptor_bytes": 6,
            "payload_bytes": 73728,
            "write_lines": EXPECTED_DESCRIPTOR_LINES,
            "write_acks": EXPECTED_DESCRIPTOR_LINES,
            "read_lines": EXPECTED_DESCRIPTOR_LINES,
            "read_responses": EXPECTED_DESCRIPTOR_LINES,
            "backing_bytes": 73728,
            "prefetch_occupancy": 0,
            "active_limit": 4096,
            "read_ahead": expected_knob,
        },
        "descriptor_spool_complete",
    )
    if complete.fields.get("identity_check") != "trace_side":
        fail("descriptor terminal identity check is not trace_side")
    if complete.fields.get("fallback") != "none":
        fail("descriptor terminal record used a fallback")
    if event_int(complete, "control_bytes") > 4096:
        fail("terminal descriptor control state exceeds 4096 bytes")
    if event_int(complete, "read_hwm") > read_credits:
        fail("terminal descriptor read high-water exceeds configured credits")

    issues = select(events, "descriptor_spool_read_issue")
    responses = select(events, "descriptor_spool_read_response")
    if len(issues) != EXPECTED_DESCRIPTOR_LINES:
        fail(
            f"expected {EXPECTED_DESCRIPTOR_LINES} descriptor issues, "
            f"found {len(issues)}"
        )
    if len(responses) != EXPECTED_DESCRIPTOR_LINES:
        fail(
            f"expected {EXPECTED_DESCRIPTOR_LINES} descriptor responses, "
            f"found {len(responses)}"
        )
    issue_by_key: dict[tuple[int, int, int, int], Event] = {}
    response_by_key: dict[tuple[int, int, int, int], Event] = {}
    for collection, destination, label in (
        (issues, issue_by_key, "issue"),
        (responses, response_by_key, "response"),
    ):
        for event in collection:
            if event_int(event, "schema") != 2:
                fail(
                    f"descriptor {label} at line {event.line_number} is not schema 2"
                )
            key = event_key(event)
            if key in destination:
                fail(f"duplicate descriptor {label} key: {key}")
            destination[key] = event
    if issue_by_key.keys() != response_by_key.keys():
        missing = sorted(issue_by_key.keys() - response_by_key.keys())[:3]
        extra = sorted(response_by_key.keys() - issue_by_key.keys())[:3]
        fail(
            f"descriptor issue/response keys differ: missing={missing} extra={extra}"
        )
    pass_lines: Counter[int] = Counter()
    for key, issue in issue_by_key.items():
        _, _, pass_number, line = key
        if (
            pass_number not in EXPECTED_PASSES
            or not 0 <= line < EXPECTED_LINES_PER_PASS
        ):
            fail(f"descriptor issue has invalid pass/line tag: {key}")
        pass_lines[pass_number] += 1
        if event_int(issue, "limit") != read_credits:
            fail(
                f"descriptor issue {key} does not expose the configured "
                f"{read_credits}-credit limit"
            )
        pending = event_int(issue, "pending")
        if pending <= 0 or pending > read_credits:
            fail(
                f"descriptor issue {key} has invalid pending occupancy {pending}"
            )
        response = response_by_key[key]
        if issue.fields.get("mode") != response.fields.get("mode"):
            fail(f"descriptor issue/response mode mismatch for {key}")
        if event_int(response, "cached") != 1:
            fail(
                f"descriptor response {key} did not use the required cache path"
            )
        if event_int(issue, "payload_bytes") != event_int(
            response, "payload_bytes"
        ):
            fail(f"descriptor issue/response payload mismatch for {key}")
        if response.line_number <= issue.line_number:
            fail(f"descriptor response precedes its issue for {key}")
    if pass_lines != Counter({1: 384, 2: 384, 3: 384}):
        fail(
            f"descriptor lines are not exactly partitioned by pass: {pass_lines}"
        )
    return complete, read_credits


def validate_control(events: list[Event], complete: Event) -> dict[str, int]:
    forbidden = (
        "descriptor_spool_overlap_opportunity",
        "descriptor_spool_read_ahead_promote",
    )
    for name in forbidden:
        if select(events, name):
            fail(f"disabled control leaked {name} events")
    replay_begins = select(events, "descriptor_spool_replay_begin")
    demand_passes = [
        event_int(event, "pass")
        for event in replay_begins
        if event.fields.get("mode") == "demand"
    ]
    if demand_passes != list(EXPECTED_PASSES):
        fail(f"control demand replay sequence is not [1,2,3]: {demand_passes}")
    read_ahead_issues = [
        event
        for event in select(events, "descriptor_spool_read_issue")
        if event.fields.get("mode") == "next_pass_read_ahead"
    ]
    read_ahead_responses = [
        event
        for event in select(events, "descriptor_spool_read_response")
        if event.fields.get("mode") == "next_pass_read_ahead"
    ]
    if read_ahead_issues or read_ahead_responses:
        fail("disabled control issued or received read-ahead traffic")
    zero_fields = (
        "overlap_opportunities",
        "next_pass_read_issues",
        "next_pass_read_responses",
        "useful_prefetched_lines",
        "demand_waits_avoided",
        "prefetch_occupancy_hwm",
        "prefetch_occupancy_line_cycles",
        "wasted_lines",
    )
    for name in zero_fields:
        if event_int(complete, name) != 0:
            fail(f"disabled control terminal counter {name} is nonzero")
    return {
        "overlap_opportunities": 0,
        "read_ahead_issues": 0,
        "ready_before_demand": 0,
    }


def validate_treatment(
    events: list[Event], complete: Event, read_credits: int
) -> dict[str, int]:
    begins = [
        event
        for event in select(events, "descriptor_spool_replay_begin")
        if event.fields.get("mode") == "next_pass_read_ahead"
    ]
    opportunities = select(events, "descriptor_spool_overlap_opportunity")
    promotions = select(events, "descriptor_spool_read_ahead_promote")
    pass_completions = {
        event_int(event, "pass"): event
        for event in select(events, "bounded_range_pass_complete")
    }
    if len(pass_completions) != 4 or set(pass_completions) != {0, 1, 2, 3}:
        fail("treatment lacks one bounded pass completion for each pass")
    if len(begins) != 3 or len(opportunities) != 3 or len(promotions) != 3:
        fail(
            "treatment must expose exactly three begin/opportunity/promotion "
            f"events, found {len(begins)}/{len(opportunities)}/{len(promotions)}"
        )

    begin_by_pass = {event_int(event, "pass"): event for event in begins}
    opportunity_by_pass = {
        event_int(event, "next_pass"): event for event in opportunities
    }
    promotion_by_pass = {
        event_int(event, "pass"): event for event in promotions
    }
    if (
        set(begin_by_pass) != set(EXPECTED_PASSES)
        or set(opportunity_by_pass) != set(EXPECTED_PASSES)
        or set(promotion_by_pass) != set(EXPECTED_PASSES)
    ):
        fail("read-ahead transition pass tags are not exactly [1,2,3]")

    read_ahead_issues = [
        event
        for event in select(events, "descriptor_spool_read_issue")
        if event.fields.get("mode") == "next_pass_read_ahead"
    ]
    read_ahead_responses = [
        event
        for event in select(events, "descriptor_spool_read_response")
        if event.fields.get("mode") == "next_pass_read_ahead"
    ]
    issues_by_pass: dict[int, list[Event]] = defaultdict(list)
    responses_by_pass: dict[int, list[Event]] = defaultdict(list)
    for event in read_ahead_issues:
        issues_by_pass[event_int(event, "pass")].append(event)
    for event in read_ahead_responses:
        responses_by_pass[event_int(event, "pass")].append(event)

    total_ready = 0
    for pass_number in EXPECTED_PASSES:
        previous_pass = pass_number - 1
        begin = begin_by_pass[pass_number]
        opportunity = opportunity_by_pass[pass_number]
        promotion = promotion_by_pass[pass_number]
        pass_issues = issues_by_pass[pass_number]
        pass_responses = responses_by_pass[pass_number]
        if not 1 <= len(pass_issues) <= read_credits:
            fail(
                f"pass {pass_number} issued {len(pass_issues)} read-ahead "
                f"lines, expected [1,{read_credits}]"
            )
        if len(pass_responses) != len(pass_issues):
            fail(f"pass {pass_number} read-ahead issue/response count differs")
        if event_int(begin, "previous_pass") != previous_pass:
            fail(
                f"early replay begin for pass {pass_number} has wrong predecessor"
            )
        if event_int(opportunity, "current_pass") != previous_pass:
            fail(
                f"overlap opportunity for pass {pass_number} has wrong predecessor"
            )
        if event_int(opportunity, "slots") != read_credits:
            fail(
                f"overlap opportunity for pass {pass_number} did not expose "
                f"{read_credits} slots"
            )
        if event_int(opportunity, "source_received") >= event_int(
            opportunity, "source_expected"
        ):
            fail(
                f"pass {pass_number} opportunity was recorded after source drain"
            )
        first_issue_line = min(event.line_number for event in pass_issues)
        if not begin.line_number < opportunity.line_number < first_issue_line:
            fail(
                f"pass {pass_number} early begin/opportunity/issue order is invalid"
            )
        previous_complete = pass_completions[previous_pass]
        if promotion.line_number <= previous_complete.line_number:
            fail(
                f"pass {pass_number} was promoted before pass {previous_pass} closed"
            )
        demand_issues_before_promotion = [
            event
            for event in select(events, "descriptor_spool_read_issue")
            if event_int(event, "pass") == pass_number
            and event.fields.get("mode") == "demand"
            and event.line_number < promotion.line_number
        ]
        if demand_issues_before_promotion:
            fail(f"pass {pass_number} issued demand reads before promotion")
        ready = sum(
            event.line_number < promotion.line_number
            for event in pass_responses
        )
        if event_int(promotion, "issued") != len(pass_issues):
            fail(f"pass {pass_number} promotion issue count is inconsistent")
        if event_int(promotion, "ready") != ready:
            fail(f"pass {pass_number} promotion ready count is inconsistent")
        if event_int(promotion, "pending") != len(pass_issues) - ready:
            fail(f"pass {pass_number} promotion pending count is inconsistent")
        for response in pass_responses:
            reported = event_int(response, "before_demand")
            expected = int(response.line_number < promotion.line_number)
            if reported != expected:
                fail(
                    f"pass {pass_number} response at line {response.line_number} "
                    "misreports ready-before-demand"
                )
        total_ready += ready

    read_ahead_count = len(read_ahead_issues)
    max_read_ahead_lines = len(EXPECTED_PASSES) * read_credits
    if read_ahead_count > max_read_ahead_lines:
        fail(
            f"read-ahead issued {read_ahead_count} lines, limit is "
            f"{max_read_ahead_lines}"
        )
    require_exact_ints(
        complete.fields,
        {
            "overlap_opportunities": 3,
            "next_pass_read_issues": read_ahead_count,
            "next_pass_read_responses": read_ahead_count,
            "useful_prefetched_lines": read_ahead_count,
            "demand_waits_avoided": total_ready,
            "wasted_lines": 0,
        },
        "descriptor_spool_complete",
    )
    occupancy_hwm = event_int(complete, "prefetch_occupancy_hwm")
    if occupancy_hwm <= 0 or occupancy_hwm > read_credits:
        fail(
            "treatment prefetch occupancy high-water is outside "
            f"[1,{read_credits}]"
        )
    if event_int(complete, "prefetch_occupancy_line_cycles") <= 0:
        fail("treatment did not charge read-ahead occupancy")
    return {
        "overlap_opportunities": 3,
        "read_ahead_issues": read_ahead_count,
        "ready_before_demand": total_ready,
    }


def validate(
    mode: str,
    manifest_path: Path,
    result_path: Path,
    trace_path: Path,
    expected_case: str = "paged_4k",
) -> dict[str, object]:
    manifest = read_manifest(manifest_path)
    result = read_result(result_path)
    events = parse_events(trace_path)
    enabled = mode == "treatment"
    complete, read_credits = validate_common_evidence(
        manifest, result, events, enabled, expected_case
    )
    metrics = (
        validate_treatment(events, complete, read_credits)
        if enabled
        else validate_control(events, complete)
    )
    for result_name, terminal_name in (
        ("descriptor_spool_overlap_opportunities", "overlap_opportunities"),
        ("descriptor_spool_next_pass_read_issues", "next_pass_read_issues"),
        (
            "descriptor_spool_next_pass_read_responses",
            "next_pass_read_responses",
        ),
        (
            "descriptor_spool_useful_prefetched_lines",
            "useful_prefetched_lines",
        ),
        ("descriptor_spool_demand_waits_avoided", "demand_waits_avoided"),
        (
            "descriptor_spool_prefetch_occupancy_line_cycles",
            "prefetch_occupancy_line_cycles",
        ),
        (
            "descriptor_spool_prefetch_occupancy_high_water",
            "prefetch_occupancy_hwm",
        ),
        ("descriptor_spool_wasted_prefetched_lines", "wasted_lines"),
        (
            "descriptor_spool_boundary_demand_wait_events",
            "boundary_wait_events",
        ),
        (
            "descriptor_spool_boundary_demand_wait_cycles",
            "boundary_wait_cycles",
        ),
        (
            "descriptor_spool_within_pass_demand_wait_events",
            "within_pass_wait_events",
        ),
        (
            "descriptor_spool_within_pass_demand_wait_cycles",
            "within_pass_wait_cycles",
        ),
    ):
        result_value = require_int(result, result_name, "result")
        terminal_value = event_int(complete, terminal_name)
        if result_value != terminal_value:
            fail(
                f"result.{result_name}={result_value} does not match "
                f"terminal {terminal_name}={terminal_value}"
            )
    return {
        "inputs": {
            "manifest_sha256": hashlib.sha256(
                manifest_path.read_bytes()
            ).hexdigest(),
            "result_sha256": hashlib.sha256(
                result_path.read_bytes()
            ).hexdigest(),
            "trace_sha256": hashlib.sha256(
                trace_path.read_bytes()
            ).hexdigest(),
        },
        "metrics": metrics,
        "mode": mode,
        "schema_version": 1,
        "status": "passed",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("control", "treatment"), required=True
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-case", default="paged_4k")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for path in (args.manifest, args.result, args.trace):
        if not path.is_file():
            raise SystemExit(
                f"descriptor overlap audit failed: missing {path}"
            )
    output = args.output_dir.resolve()
    if output.exists():
        raise SystemExit(
            f"descriptor overlap audit failed: refusing to overwrite {output}"
        )
    try:
        report = validate(
            args.mode,
            args.manifest,
            args.result,
            args.trace,
            args.expected_case,
        )
    except AuditError as error:
        raise SystemExit(
            f"descriptor overlap audit failed: {error}"
        ) from error
    output.mkdir(parents=True)
    (output / "validation.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "descriptor_spool_read_ahead_validation.pass").touch()
    print(f"PASS descriptor spool read-ahead {args.mode}: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
