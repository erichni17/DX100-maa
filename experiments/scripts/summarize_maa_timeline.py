#!/usr/bin/env python3
"""Extract MAA instruction intervals and overlap from gem5 debug logs."""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path

EVENT_RE = re.compile(
    r"^(?P<tick>\d+): global: (?P<kind>[IS])\[(?P<unit>\d+)\] "
    r"(?P<action>Start|End) \[INSTR\[(?P<body>.*)\]\]$"
)
FIELD_RE = re.compile(r"(?P<name>[A-Za-z0-9_]+)\((?P<value>[^)]*)\)")


@dataclass(frozen=True)
class Event:
    label: str
    index: int
    kind: str
    unit: int
    core_id: int
    opcode: str
    start_tick: int
    end_tick: int

    @property
    def duration_ticks(self) -> int:
        return self.end_tick - self.start_tick


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def parse_run(value: str) -> tuple[str, Path]:
    label, separator, path = value.partition("=")
    if (
        not separator
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", label)
        or not path
    ):
        raise argparse.ArgumentTypeError("run must have the form LABEL=PATH")
    return label, Path(path).resolve()


def resolve_debug_log(path: Path) -> Path:
    if path.is_file():
        return path
    candidate = path / "run" / "xrage-debug.log"
    if candidate.is_file():
        return candidate
    fail(f"debug log not found under {path}")
    raise AssertionError


def parse_events(label: str, path: Path) -> list[Event]:
    active: dict[tuple[str, int], tuple[int, dict[str, str]]] = {}
    events: list[Event] = []
    for line_number, raw_line in enumerate(
        resolve_debug_log(path).read_text(encoding="utf-8").splitlines(), 1
    ):
        match = EVENT_RE.match(raw_line)
        if not match:
            continue
        tick = int(match.group("tick"))
        key = (match.group("kind"), int(match.group("unit")))
        fields = {
            field.group("name"): field.group("value")
            for field in FIELD_RE.finditer(match.group("body"))
        }
        for required in ("core_id", "opcode"):
            if required not in fields:
                fail(f"{label}:{line_number} missing {required}")
        if match.group("action") == "Start":
            if key in active:
                fail(f"{label}:{line_number} starts already-active {key}")
            active[key] = (tick, fields)
            continue

        if key not in active:
            fail(f"{label}:{line_number} ends inactive {key}")
        start_tick, start_fields = active.pop(key)
        if fields["core_id"] != start_fields["core_id"]:
            fail(f"{label}:{line_number} changes core_id for {key}")
        if fields["opcode"] != start_fields["opcode"]:
            fail(f"{label}:{line_number} changes opcode for {key}")
        if tick < start_tick:
            fail(f"{label}:{line_number} ends before it starts")
        events.append(
            Event(
                label=label,
                index=len(events),
                kind=key[0],
                unit=key[1],
                core_id=int(fields["core_id"]),
                opcode=fields["opcode"],
                start_tick=start_tick,
                end_tick=tick,
            )
        )
    if active:
        fail(f"{label} has unmatched starts: {sorted(active)}")
    if not events:
        fail(f"{label} has no complete MAA events")
    return sorted(events, key=lambda event: (event.start_tick, event.end_tick))


def overlap_ticks(left: Event, right: Event) -> int:
    return max(0, min(left.end_tick, right.end_tick) - max(left.start_tick, right.start_tick))


def union_ticks(events: list[Event]) -> int:
    intervals = sorted((event.start_tick, event.end_tick) for event in events)
    total = 0
    start, end = intervals[0]
    for next_start, next_end in intervals[1:]:
        if next_start > end:
            total += end - start
            start, end = next_start, next_end
        else:
            end = max(end, next_end)
    return total + end - start


def write_tsv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("runs", nargs="+", type=parse_run)
    args = parser.parse_args()
    labels = [label for label, _ in args.runs]
    if len(labels) != len(set(labels)):
        parser.error("run labels must be unique")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    event_rows: list[dict[str, object]] = []
    overlap_rows: list[dict[str, object]] = []
    summary_rows: list[dict[str, object]] = []
    for label, path in args.runs:
        events = parse_events(label, path)
        for index, event in enumerate(events):
            event_rows.append(
                {
                    "run": label,
                    "event": index,
                    "unit": f"{event.kind}{event.unit}",
                    "core_id": event.core_id,
                    "opcode": event.opcode,
                    "start_tick": event.start_tick,
                    "end_tick": event.end_tick,
                    "duration_ticks": event.duration_ticks,
                }
            )

        stream_indirect_overlap = 0
        cross_core_stream_indirect_overlap = 0
        all_overlap = 0
        for left_index, left in enumerate(events):
            for right_index in range(left_index + 1, len(events)):
                right = events[right_index]
                overlap = overlap_ticks(left, right)
                if overlap == 0:
                    continue
                all_overlap += overlap
                is_stream_indirect = {left.kind, right.kind} == {"S", "I"}
                if is_stream_indirect:
                    stream_indirect_overlap += overlap
                    if left.core_id != right.core_id:
                        cross_core_stream_indirect_overlap += overlap
                overlap_rows.append(
                    {
                        "run": label,
                        "left_event": left_index,
                        "right_event": right_index,
                        "left_unit": f"{left.kind}{left.unit}",
                        "right_unit": f"{right.kind}{right.unit}",
                        "left_core": left.core_id,
                        "right_core": right.core_id,
                        "overlap_ticks": overlap,
                        "stream_indirect": int(is_stream_indirect),
                        "cross_core": int(left.core_id != right.core_id),
                    }
                )

        first_tick = min(event.start_tick for event in events)
        last_tick = max(event.end_tick for event in events)
        summary_rows.append(
            {
                "run": label,
                "events": len(events),
                "stream_events": sum(event.kind == "S" for event in events),
                "indirect_events": sum(event.kind == "I" for event in events),
                "first_tick": first_tick,
                "last_tick": last_tick,
                "span_ticks": last_tick - first_tick,
                "busy_union_ticks": union_ticks(events),
                "all_pair_overlap_ticks": all_overlap,
                "stream_indirect_overlap_ticks": stream_indirect_overlap,
                "cross_core_stream_indirect_overlap_ticks": (
                    cross_core_stream_indirect_overlap
                ),
            }
        )

    write_tsv(
        args.output_dir / "maa_timeline_events.tsv",
        [
            "run",
            "event",
            "unit",
            "core_id",
            "opcode",
            "start_tick",
            "end_tick",
            "duration_ticks",
        ],
        event_rows,
    )
    write_tsv(
        args.output_dir / "maa_timeline_overlap.tsv",
        [
            "run",
            "left_event",
            "right_event",
            "left_unit",
            "right_unit",
            "left_core",
            "right_core",
            "overlap_ticks",
            "stream_indirect",
            "cross_core",
        ],
        overlap_rows,
    )
    write_tsv(
        args.output_dir / "maa_timeline_summary.tsv",
        [
            "run",
            "events",
            "stream_events",
            "indirect_events",
            "first_tick",
            "last_tick",
            "span_ticks",
            "busy_union_ticks",
            "all_pair_overlap_ticks",
            "stream_indirect_overlap_ticks",
            "cross_core_stream_indirect_overlap_ticks",
        ],
        summary_rows,
    )
    (args.output_dir / "maa_timeline.pass").touch()
    print(f"PASS MAA timeline: {args.output_dir / 'maa_timeline_summary.tsv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
