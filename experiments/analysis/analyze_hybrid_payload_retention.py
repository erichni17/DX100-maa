#!/usr/bin/env python3
"""Fail-closed replay of bounded hybrid payload-retention opportunities.

This is a trace analyzer, not a timing model.  It consumes only the recorded
MAA-cycle events needed to reconstruct one materializer lifetime and reports
which *observed* cache fallback reads a direct-indexed payload array could
avoid.  It never adds requests, delays requests, or assigns an order to events
which share an MAA cycle.

The conservative access rule is one array write and one array read per MAA
cycle.  A cycle with multiple producer arrivals or multiple fallback reads is
an unresolved port conflict: no capture or fallback avoidance is credited for
that access.  Reads observe the state before writes in their cycle, so a
same-cycle arrival is never treated as a hit.
"""

from __future__ import annotations

import argparse
import configparser
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Iterable,
    Mapping,
    Sequence,
)

CAPACITIES = (64, 128, 256, 512)
POLICIES = ("first_owner_wins", "latest_owner_wins")
EVENT_RE = re.compile(
    r"^(?P<tick>[0-9]+): (?P<component>[A-Za-z0-9_.-]+): "
    r"event=(?P<event>[A-Za-z0-9_]+)"
    r"(?P<fields>(?: [A-Za-z][A-Za-z0-9_]*=[^ ]+)*)$"
)
FIELD_RE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9_]*)=(?P<value>[^ ]+)$")
TARGET_EVENTS = {
    "direct_retirement_early_ledger_begin",
    "direct_retirement_producer_line_early",
    "page_materialization_submit",
    "page_materialization_read_issue",
    "page_materialization_read_response",
    "page_materialization_line_commit",
    "page_materialization_producer_line_ready",
    "page_materialization_producer_ack",
    "page_materialization_page_ready",
    "page_materialization_summary",
    "page_materialization_retire",
    "page_ready",
}
TARGET_PREFIXES = (
    "event=page_materialization_",
    "event=direct_retirement_early_ledger_begin",
    "event=direct_retirement_producer_line_early",
    "event=page_ready",
)
SCHEMAS: Mapping[str, set[str]] = {
    "direct_retirement_early_ledger_begin": {
        "schema",
        "occurrence",
        "token",
        "generation",
        "lines",
        "result",
        "active_slots",
        "storage_bytes",
    },
    "direct_retirement_producer_line_early": {
        "schema",
        "occurrence",
        "token",
        "generation",
        "line",
        "transaction",
        "result",
        "ready_lines",
    },
    "page_materialization_submit": {
        "schema",
        "occurrence",
        "token",
        "generation",
        "incarnation",
        "page",
        "destination",
        "new_context",
        "early_lines",
        "line_buffer_bytes",
        "control_bytes",
        "direct_stage_control_bytes",
        "page_spd_bytes",
        "charged_two_page_spd_bytes",
        "activation_count",
    },
    "page_materialization_read_issue": {
        "schema",
        "occurrence",
        "token",
        "generation",
        "incarnation",
        "line",
        "port",
        "retry",
    },
    "page_materialization_read_response": {
        "schema",
        "occurrence",
        "token",
        "generation",
        "incarnation",
        "page",
        "line",
        "buffer",
        "spd_ready_tick",
    },
    "page_materialization_line_commit": {
        "schema",
        "occurrence",
        "token",
        "generation",
        "incarnation",
        "page",
        "line",
        "destination",
    },
    "page_materialization_producer_line_ready": {
        "schema",
        "occurrence",
        "token",
        "generation",
        "incarnation",
        "page",
        "line",
        "word_mask",
        "transaction",
        "forwarded",
        "fragment_capture",
        "fragment_accumulated",
        "fragment_buffer_stall",
        "commit_tick",
    },
    "page_materialization_producer_ack": {
        "schema",
        "occurrence",
        "token",
        "generation",
        "incarnation",
        "page",
        "transaction",
        "fallback_lines",
    },
    "page_materialization_page_ready": {
        "schema",
        "occurrence",
        "token",
        "generation",
        "incarnation",
        "page",
        "destination",
        "lines",
        "reads",
        "forwarded_lines",
        "staged_direct_lines",
        "cache_read_fallback_lines",
        "pages_materialized",
    },
    "page_materialization_summary": {
        "schema",
        "occurrence",
        "token",
        "generation",
        "incarnation",
        "pages",
        "lines",
        "forwarded_lines",
        "staged_direct_lines",
        "cache_read_fallback_lines",
        "producer_line_acks",
        "page_fallback_lines",
        "exact_closure",
        "dispatch_fallbacks",
    },
    "page_materialization_retire": {
        "schema",
        "occurrence",
        "token",
        "generation",
        "incarnation",
        "pages",
    },
    "page_ready": {
        "schema",
        "occurrence",
        "unit",
        "page",
        "operation_tick",
        "pages",
        "scanned",
        "expected",
        "issued",
        "completed",
        "sources_drained",
    },
}


class TraceFormatError(ValueError):
    """The trace cannot establish one complete, unambiguous lifecycle."""


@dataclass(frozen=True)
class Event:
    tick: int
    line_number: int
    kind: str
    fields: Mapping[str, int | str]


@dataclass(frozen=True)
class Identity:
    token: int
    generation: int
    line: int


@dataclass(frozen=True)
class Arrival:
    cycle: int
    identity: Identity
    page: int


@dataclass(frozen=True)
class FallbackRead:
    cycle: int
    identity: Identity
    page: int


def _fail(line: int, message: str) -> TraceFormatError:
    return TraceFormatError(f"line {line}: {message}")


def _target_candidate(text: str) -> bool:
    return any(prefix in text for prefix in TARGET_PREFIXES)


def _parse_fields(line_number: int, raw: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for token in raw.split():
        match = FIELD_RE.fullmatch(token)
        if match is None:
            raise _fail(line_number, f"malformed field {token!r}")
        key = match.group("key")
        if key in fields:
            raise _fail(line_number, f"duplicate field {key!r}")
        fields[key] = match.group("value")
    return fields


def _decode(line_number: int, tick: int, kind: str, raw: str) -> Event:
    fields = _parse_fields(line_number, raw)
    expected = SCHEMAS[kind]
    if set(fields) != expected:
        raise _fail(
            line_number,
            f"{kind} schema mismatch; missing={sorted(expected - set(fields))}; "
            f"unexpected={sorted(set(fields) - expected)}",
        )
    decoded: dict[str, int | str] = {}
    for key, value in fields.items():
        if kind == "page_ready" and key == "pages":
            if re.fullmatch(r"[0-9]+/[0-9]+", value) is None:
                raise _fail(
                    line_number, "page_ready pages is not ordinal/total"
                )
            decoded[key] = value
        elif key == "word_mask":
            if re.fullmatch(r"0x[0-9a-f]+", value) is None:
                raise _fail(
                    line_number, "word_mask is not canonical lowercase hex"
                )
            decoded[key] = int(value, 16)
        else:
            if re.fullmatch(r"[0-9]+", value) is None:
                raise _fail(line_number, f"{key} is not a nonnegative integer")
            decoded[key] = int(value)
    return Event(tick, line_number, kind, decoded)


def parse_events(lines: Iterable[str]) -> tuple[Event, ...]:
    """Parse only the target lifecycle, rejecting malformed target fragments."""
    events: list[Event] = []
    previous_tick = -1
    for line_number, raw_line in enumerate(lines, 1):
        for fragment in raw_line.split("\r"):
            text = fragment.rstrip("\n")
            if not _target_candidate(text):
                continue
            match = EVENT_RE.fullmatch(text)
            if match is None:
                raise _fail(line_number, "malformed target event line")
            kind = match.group("event")
            if kind not in TARGET_EVENTS:
                raise _fail(line_number, f"unsupported target event {kind!r}")
            tick = int(match.group("tick"))
            if tick < previous_tick:
                raise _fail(line_number, "target-event ticks moved backwards")
            previous_tick = tick
            events.append(
                _decode(line_number, tick, kind, match.group("fields"))
            )
    if not events:
        raise TraceFormatError("trace contains no payload-retention lifecycle")
    return tuple(events)


def _int(event: Event, key: str) -> int:
    value = event.fields[key]
    if not isinstance(value, int):
        raise _fail(event.line_number, f"{event.kind}.{key} is not an integer")
    return value


def _only(events: Sequence[Event], kind: str) -> list[Event]:
    return [event for event in events if event.kind == kind]


def _require_one(events: Sequence[Event], kind: str) -> Event:
    selected = _only(events, kind)
    if len(selected) != 1:
        raise TraceFormatError(
            f"expected exactly one {kind}, got {len(selected)}"
        )
    return selected[0]


def _cycle(event: Event, clock_ticks: int) -> int:
    if event.tick % clock_ticks:
        raise _fail(
            event.line_number,
            f"tick {event.tick} is not an MAA-cycle boundary",
        )
    return event.tick // clock_ticks


def _identity(event: Event) -> Identity:
    return Identity(
        _int(event, "token"), _int(event, "generation"), _int(event, "line")
    )


def reconstruct(events: Sequence[Event], clock_ticks: int) -> dict:
    """Reconstruct the exact completed producer/materializer lifecycle."""
    if clock_ticks <= 0:
        raise TraceFormatError("MAA clock must be positive")
    ledger = _require_one(events, "direct_retirement_early_ledger_begin")
    if (_int(ledger, "schema"), _int(ledger, "result")) != (1, 0):
        raise _fail(ledger.line_number, "early ledger did not open cleanly")
    token, generation, total_lines = (
        _int(ledger, "token"),
        _int(ledger, "generation"),
        _int(ledger, "lines"),
    )
    if token == 0 or generation == 0 or total_lines == 0 or total_lines % 4:
        raise _fail(
            ledger.line_number, "invalid token/generation/four-page geometry"
        )
    lines_per_page = total_lines // 4
    expected_lines = set(range(total_lines))

    submits = _only(events, "page_materialization_submit")
    if len(submits) != 4:
        raise TraceFormatError(
            f"expected four page activations, got {len(submits)}"
        )
    activation_ticks: dict[int, int] = {}
    incarnation: int | None = None
    for ordinal, event in enumerate(submits, 1):
        values = (_int(event, "token"), _int(event, "generation"))
        if values != (token, generation) or _int(event, "schema") != 1:
            raise _fail(
                event.line_number, "activation lineage/schema mismatch"
            )
        page = _int(event, "page")
        if page != ordinal - 1 or page in activation_ticks:
            raise _fail(
                event.line_number, "activation page order is not canonical"
            )
        if _int(event, "activation_count") != ordinal:
            raise _fail(event.line_number, "activation count is discontinuous")
        this_incarnation = _int(event, "incarnation")
        if incarnation is None:
            incarnation = this_incarnation
        if this_incarnation != incarnation or incarnation == 0:
            raise _fail(event.line_number, "activation incarnation mismatch")
        activation_ticks[page] = event.tick

    arrivals: list[Arrival] = []
    seen_arrivals: set[Identity] = set()
    forwarded: set[Identity] = set()
    for event in _only(events, "direct_retirement_producer_line_early"):
        if (_int(event, "schema"), _int(event, "result")) != (1, 1):
            raise _fail(
                event.line_number, "early producer arrival is not successful"
            )
        identity = _identity(event)
        if identity.token != token or identity.generation != generation:
            raise _fail(event.line_number, "early producer lineage mismatch")
        if identity.line not in expected_lines or identity in seen_arrivals:
            raise _fail(
                event.line_number, "duplicate/out-of-range producer arrival"
            )
        seen_arrivals.add(identity)
        arrivals.append(
            Arrival(
                _cycle(event, clock_ticks),
                identity,
                identity.line // lines_per_page,
            )
        )
    for event in _only(events, "page_materialization_producer_line_ready"):
        if (_int(event, "schema"), _int(event, "word_mask")) != (1, 0xFF):
            raise _fail(event.line_number, "producer event is not a full line")
        identity = _identity(event)
        # This trace field is the currently materializing consumer page, not
        # the producer line's logical owner.  Derive ownership only from the
        # exact global line identity and the ledger geometry.
        _int(event, "page")
        if (
            identity.token != token
            or identity.generation != generation
            or identity.line not in expected_lines
            or identity in seen_arrivals
        ):
            raise _fail(
                event.line_number, "producer line identity/page mismatch"
            )
        seen_arrivals.add(identity)
        if _int(event, "forwarded") == 1:
            forwarded.add(identity)
        elif _int(event, "forwarded") != 0:
            raise _fail(
                event.line_number, "producer forwarded flag is not boolean"
            )
        arrivals.append(
            Arrival(
                _cycle(event, clock_ticks),
                identity,
                identity.line // lines_per_page,
            )
        )
    if {identity.line for identity in seen_arrivals} != expected_lines:
        raise TraceFormatError(
            "producer arrivals do not cover the full logical tile"
        )

    reads: list[FallbackRead] = []
    seen_reads: set[Identity] = set()
    for event in _only(events, "page_materialization_read_issue"):
        identity = _identity(event)
        if (
            _int(event, "schema") != 1
            or _int(event, "incarnation") != incarnation
            or identity.token != token
            or identity.generation != generation
            or identity.line not in expected_lines
            or identity in seen_reads
        ):
            raise _fail(event.line_number, "fallback-read identity mismatch")
        seen_reads.add(identity)
        reads.append(
            FallbackRead(
                _cycle(event, clock_ticks),
                identity,
                identity.line // lines_per_page,
            )
        )
    responses = _only(events, "page_materialization_read_response")
    response_ids = {_identity(event) for event in responses}
    if response_ids != seen_reads or len(responses) != len(seen_reads):
        raise TraceFormatError(
            "fallback read issue/response identity does not close"
        )
    if seen_reads & forwarded or seen_reads | forwarded != seen_arrivals:
        raise TraceFormatError(
            "fallback reads and observed forwards do not partition producer lines"
        )

    commits = _only(events, "page_materialization_line_commit")
    commit_ids = {_identity(event) for event in commits}
    if len(commits) != total_lines or commit_ids != seen_arrivals:
        raise TraceFormatError(
            "line commits do not close the full producer set"
        )
    for event in commits:
        identity = _identity(event)
        if (
            _int(event, "schema") != 1
            or _int(event, "incarnation") != incarnation
            or _int(event, "page") != identity.line // lines_per_page
        ):
            raise _fail(
                event.line_number, "line commit identity/page mismatch"
            )

    logical_ready = _only(events, "page_ready")
    if len(logical_ready) != 4:
        raise TraceFormatError("expected four logical page-ready events")
    ready_ticks: dict[int, int] = {}
    for ordinal, event in enumerate(logical_ready, 1):
        if (_int(event, "schema"), _int(event, "page")) != (2, ordinal - 1):
            raise _fail(
                event.line_number, "logical page-ready order/schema mismatch"
            )
        if event.fields["pages"] != f"{ordinal}/4":
            raise _fail(event.line_number, "logical page-ready count mismatch")
        ready_ticks[ordinal - 1] = event.tick

    page_ready = _only(events, "page_materialization_page_ready")
    if len(page_ready) != 4:
        raise TraceFormatError("expected four materializer page-ready events")
    for ordinal, event in enumerate(page_ready, 1):
        if (
            _int(event, "schema") != 1
            or _int(event, "page") != ordinal - 1
            or _int(event, "lines") != lines_per_page
            or _int(event, "pages_materialized") != ordinal
        ):
            raise _fail(event.line_number, "materializer page-ready mismatch")
    summary = _require_one(events, "page_materialization_summary")
    retire = _require_one(events, "page_materialization_retire")
    if (
        _int(summary, "schema") != 1
        or _int(summary, "token") != token
        or _int(summary, "generation") != generation
        or _int(summary, "incarnation") != incarnation
        or _int(summary, "pages") != 4
        or _int(summary, "lines") != total_lines
        or _int(summary, "forwarded_lines") != len(forwarded)
        or _int(summary, "cache_read_fallback_lines") != len(reads)
        or _int(summary, "exact_closure") != 1
        or _int(retire, "pages") != 4
    ):
        raise TraceFormatError(
            "materializer summary/retirement does not reconcile"
        )
    return {
        "token": token,
        "generation": generation,
        "incarnation": incarnation,
        "clock_ticks": clock_ticks,
        "total_lines": total_lines,
        "lines_per_page": lines_per_page,
        "activation_ticks": activation_ticks,
        "logical_ready_ticks": ready_ticks,
        "arrivals": tuple(
            sorted(arrivals, key=lambda item: (item.cycle, item.identity.line))
        ),
        "reads": tuple(
            sorted(reads, key=lambda item: (item.cycle, item.identity.line))
        ),
        "observed_forwarded_lines": len(forwarded),
        "observed_fallback_lines": len(reads),
    }


def simulate(
    lifecycle: Mapping[str, object], capacity: int, policy: str
) -> dict:
    """Replay exactly recorded access opportunities under one read/write port."""
    if capacity not in CAPACITIES:
        raise ValueError(f"unsupported capacity {capacity}")
    if policy not in POLICIES:
        raise ValueError(f"unsupported policy {policy}")
    arrivals = lifecycle["arrivals"]
    reads = lifecycle["reads"]
    activation = lifecycle["activation_ticks"]
    if (
        not isinstance(arrivals, tuple)
        or not isinstance(reads, tuple)
        or not isinstance(activation, dict)
    ):
        raise ValueError("malformed reconstructed lifecycle")
    by_cycle_arrivals: dict[int, list[Arrival]] = defaultdict(list)
    by_cycle_reads: dict[int, list[FallbackRead]] = defaultdict(list)
    for arrival in arrivals:
        by_cycle_arrivals[arrival.cycle].append(arrival)
    for read in reads:
        by_cycle_reads[read.cycle].append(read)
    clock_ticks = lifecycle["clock_ticks"]
    if not isinstance(clock_ticks, int):
        raise ValueError("malformed MAA clock")
    activations_by_tick: dict[int, list[int]] = defaultdict(list)
    for page, tick in activation.items():
        activations_by_tick[tick].append(page)

    slots: list[Identity | None] = [None] * capacity
    captures = [0, 0, 0, 0]
    retained_at_activation = [0, 0, 0, 0]
    avoided = [0, 0, 0, 0]
    read_port_conflicts = 0
    write_port_conflicts = 0
    matching_conflicted_reads = 0
    same_cycle_arrival_read = 0
    access_ticks = {
        cycle * clock_ticks
        for cycle in set(by_cycle_arrivals) | set(by_cycle_reads)
    }
    for tick in sorted(access_ticks | set(activations_by_tick)):
        cycle = tick // clock_ticks
        writes = by_cycle_arrivals[cycle] if tick in access_ticks else []
        cycle_reads = by_cycle_reads[cycle] if tick in access_ticks else []
        # Activation is sampled from state before this cycle's accesses.  This
        # avoids inventing an order between a submit and a producer response.
        for page in activations_by_tick[tick]:
            retained_at_activation[page] = sum(
                entry is not None
                and entry.line // int(lifecycle["lines_per_page"]) == page
                for entry in slots
            )
        if len(cycle_reads) > 1:
            read_port_conflicts += len(cycle_reads) - 1
        if len(writes) > 1:
            write_port_conflicts += len(writes) - 1
        written_ids = {arrival.identity for arrival in writes}
        for read in cycle_reads:
            index = read.identity.line % capacity
            match = slots[index] == read.identity
            if read.identity in written_ids:
                same_cycle_arrival_read += 1
            if len(cycle_reads) == 1 and match:
                avoided[read.page] += 1
                slots[index] = None  # a recorded consumer access releases it
            elif len(cycle_reads) > 1 and match:
                matching_conflicted_reads += 1
        # Multiple writes have no trace-proven winner.  Do not choose one.
        if len(writes) == 1:
            arrival = writes[0]
            index = arrival.identity.line % capacity
            incumbent = slots[index]
            if (
                incumbent is None
                or incumbent == arrival.identity
                or policy == "latest_owner_wins"
            ):
                slots[index] = arrival.identity
                captures[arrival.page] += 1
    return {
        "capacity_lines": capacity,
        "policy": policy,
        "per_page": [
            {
                "page": page,
                "captures": captures[page],
                "retained_at_activation": retained_at_activation[page],
                "predicted_fallback_lines_avoided": avoided[page],
            }
            for page in range(4)
        ],
        "totals": {
            "captures": sum(captures),
            "retained_at_activation": sum(retained_at_activation),
            "predicted_fallback_lines_avoided": sum(avoided),
            "read_port_conflicts": read_port_conflicts,
            "write_port_conflicts": write_port_conflicts,
            "matching_reads_blocked_by_read_port_conflict": matching_conflicted_reads,
            "same_cycle_arrival_read_uncredited": same_cycle_arrival_read,
        },
        "removable_tail_bounds_ticks": {
            "lower": 0,
            "upper": 0,
            "reason": "Trace records no alternate completion latency or critical-path dependency; no tail ticks are defensibly removable.",
        },
    }


def build_report(events: Sequence[Event], clock_ticks: int) -> dict:
    lifecycle = reconstruct(events, clock_ticks)
    results = [
        simulate(lifecycle, capacity, policy)
        for policy in POLICIES
        for capacity in CAPACITIES
    ]
    return {
        "kind": "hybrid_payload_retention_trace_audit",
        "method": {
            "read_only": True,
            "cycle_rule": "one write and one read access per recorded MAA cycle; no same-cycle ordering is assumed",
            "identity": "token,generation,line",
            "index": "line % capacity_lines",
            "policies": list(POLICIES),
            "capacities_lines": list(CAPACITIES),
        },
        "observed": {
            key: lifecycle[key]
            for key in (
                "token",
                "generation",
                "incarnation",
                "clock_ticks",
                "total_lines",
                "lines_per_page",
                "activation_ticks",
                "logical_ready_ticks",
                "observed_forwarded_lines",
                "observed_fallback_lines",
            )
        },
        "simulations": results,
        "conclusion": "Fallback-line counts are trace-derived opportunities only; this audit predicts no speedup or removable tail cycles.",
    }


def load_maa_clock(config_path: Path) -> int:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    if not parser.read(config_path, encoding="utf-8"):
        raise TraceFormatError(f"cannot read config {config_path}")
    if not parser.has_option("system.maa", "clk_domain"):
        raise TraceFormatError("config lacks [system.maa] clk_domain")
    domain = parser.get("system.maa", "clk_domain")
    if not parser.has_option(domain, "clock"):
        raise TraceFormatError(f"config lacks [{domain}] clock")
    value = parser.get(domain, "clock")
    if re.fullmatch(r"[1-9][0-9]*", value) is None:
        raise TraceFormatError("system.maa clock is not a positive integer")
    return int(value)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, help="write JSON report instead of stdout"
    )
    args = parser.parse_args(argv)
    try:
        clock_ticks = load_maa_clock(args.config)
        with args.trace.open(encoding="utf-8") as trace:
            report = build_report(parse_events(trace), clock_ticks)
    except (OSError, TraceFormatError, ValueError) as error:
        print(
            f"payload-retention audit failed closed: {error}", file=sys.stderr
        )
        return 2
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
