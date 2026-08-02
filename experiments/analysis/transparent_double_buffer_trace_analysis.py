#!/usr/bin/env python3
"""Fail-closed analysis of transparent-controller lifecycle traces.

Only the lifecycle events named in ``SUPPORTED_EVENTS`` are accepted.  Other
trace traffic is ignored, but an unknown ``transparent_*`` event or a malformed
target event rejects the complete input rather than producing partial timing.

The ideal schedule is deliberately narrow: two input page slots, one fill
lane, and one shared compute/output/store lane.  It keeps every observed stage
duration fixed while removing all dispatch gaps and cross-lane interference.
Its result is a conditional critical-path lower bound, not a gem5 prediction
or speedup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import (
    asdict,
    dataclass,
)
from pathlib import Path
from typing import (
    Iterable,
    Mapping,
    Sequence,
)

SUPPORTED_EVENTS = {
    "transparent_submit",
    "page_ready",
    "transparent_issue",
    "transparent_complete",
    "transparent_retire",
}
ACTION_NAMES = {1: "fill", 2: "compute", 3: "store"}
LOGICAL_ELEMENTS = 16384
PAGE_ELEMENTS = 4096
PAGE_COUNT = 4
FP64_TILE_WORDS = 2
EVENT_RE = re.compile(
    r"^(?P<tick>[0-9]+): (?P<component>[A-Za-z0-9_.-]+): "
    r"event=(?P<event>[A-Za-z0-9_]+)"
    r"(?P<fields>(?: [A-Za-z][A-Za-z0-9_]*=[^ ]+)*)$"
)
FIELD_RE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9_]*)=(?P<value>[^ ]+)$")
READY_COUNT_RE = re.compile(r"^(?P<ordinal>[0-9]+)/(?P<total>[0-9]+)$")


class TraceFormatError(ValueError):
    """The target trace cannot prove one complete valid lifecycle."""


@dataclass(frozen=True)
class Event:
    tick: int
    line: int
    component: str
    kind: str
    fields: Mapping[str, int | str]


@dataclass(frozen=True)
class Interval:
    issue: int
    complete: int

    @property
    def duration(self) -> int:
        return self.complete - self.issue


@dataclass(frozen=True)
class PageTimeline:
    page: int
    ready: int
    fill: Interval
    compute: Interval
    store: Interval
    submit_to_ready: int
    ready_to_fill_issue: int
    fill_complete_to_compute_issue: int
    compute_complete_to_store_issue: int
    prior_store_to_ready: int | None
    readiness_wait_after_prior_store: int
    slot_wait_after_ready: int
    one_slot_dispatch_gap: int


@dataclass(frozen=True)
class IdealPageSchedule:
    page: int
    input_slot: int
    ready: int
    fill: Interval
    compute: Interval
    store: Interval


@dataclass(frozen=True)
class TraceAnalysis:
    submit_tick: int
    retire_tick: int
    logical_elements: int
    page_elements: int
    pages: tuple[PageTimeline, ...]
    ideal_two_slot: tuple[IdealPageSchedule, ...]

    @property
    def observed_submit_to_retire(self) -> int:
        return self.retire_tick - self.submit_tick

    @property
    def observed_first_ready_to_retire(self) -> int:
        return self.retire_tick - min(page.ready for page in self.pages)

    @property
    def final_store_to_retire(self) -> int:
        return self.retire_tick - max(
            page.store.complete for page in self.pages
        )

    @property
    def ideal_retire_tick(self) -> int:
        return max(page.store.complete for page in self.ideal_two_slot)

    @property
    def ideal_submit_to_retire(self) -> int:
        return self.ideal_retire_tick - self.submit_tick

    @property
    def ideal_first_ready_to_retire(self) -> int:
        first_ready = min(page.ready for page in self.pages)
        return self.ideal_retire_tick - first_ready

    @property
    def conditional_overlap_delta(self) -> int:
        return self.retire_tick - self.ideal_retire_tick


@dataclass
class _MutablePage:
    ready: int | None = None
    fill: Interval | None = None
    compute: Interval | None = None
    store: Interval | None = None


def _fail(line: int, message: str) -> TraceFormatError:
    return TraceFormatError(f"line {line}: {message}")


def _target_marker(line: str) -> bool:
    return (
        "event=transparent_" in line
        or re.search(r"event=page_ready(?: |$)", line) is not None
    )


def _parse_fields(line_number: int, text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for token in text.split():
        match = FIELD_RE.fullmatch(token)
        if match is None:
            raise _fail(line_number, f"malformed field {token!r}")
        key = match.group("key")
        if key in fields:
            raise _fail(line_number, f"duplicate field {key!r}")
        fields[key] = match.group("value")
    return fields


def _require_schema(
    line: int, fields: Mapping[str, str], expected: set[str]
) -> None:
    actual = set(fields)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise _fail(
            line,
            f"field schema mismatch; missing={missing}, "
            f"unexpected={unexpected}",
        )


def _integer(line: int, key: str, value: str) -> int:
    if re.fullmatch(r"[0-9]+", value) is None:
        raise _fail(line, f"field {key!r} is not a nonnegative integer")
    return int(value)


def _decode_event(
    line_number: int, tick: int, component: str, kind: str, raw: str
) -> Event:
    fields = _parse_fields(line_number, raw)
    if kind == "transparent_submit":
        expected = {
            "token",
            "physical",
            "output",
            "generation",
            "logical",
            "page",
            "pages",
        }
    elif kind == "page_ready":
        expected = {
            "unit",
            "page",
            "pages",
            "scanned",
            "expected",
            "issued",
            "completed",
            "sources_drained",
        }
    elif kind == "transparent_issue":
        expected = {"page", "action", "offset", "elements"}
    elif kind == "transparent_complete":
        expected = {"page", "action"}
    elif kind == "transparent_retire":
        expected = {"pages"}
    else:
        raise _fail(line_number, f"unsupported target event {kind!r}")
    _require_schema(line_number, fields, expected)

    decoded: dict[str, int | str] = {}
    for key, value in fields.items():
        if kind == "page_ready" and key == "pages":
            decoded[key] = value
        else:
            decoded[key] = _integer(line_number, key, value)
    return Event(tick, line_number, component, kind, decoded)


def parse_events(lines: Iterable[str]) -> tuple[Event, ...]:
    events: list[Event] = []
    previous_tick = -1
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.rstrip("\n")
        if not _target_marker(line):
            continue
        match = EVENT_RE.fullmatch(line)
        if match is None:
            raise _fail(line_number, "malformed target event line")
        kind = match.group("event")
        if kind not in SUPPORTED_EVENTS:
            raise _fail(line_number, f"unsupported target event {kind!r}")
        tick = int(match.group("tick"))
        if tick < previous_tick:
            raise _fail(line_number, "target-event ticks moved backwards")
        previous_tick = tick
        event = _decode_event(
            line_number,
            tick,
            match.group("component"),
            kind,
            match.group("fields"),
        )
        expected_component = "global" if kind == "page_ready" else "system.maa"
        if event.component != expected_component:
            raise _fail(
                line_number,
                f"{kind} came from {event.component!r}, expected "
                f"{expected_component!r}",
            )
        events.append(event)
    if not events:
        raise TraceFormatError("trace contains no supported target events")
    return tuple(events)


def _field(event: Event, key: str) -> int:
    value = event.fields[key]
    if not isinstance(value, int):
        raise _fail(event.line, f"field {key!r} is not decoded as an integer")
    return value


def _validate_submit(event: Event) -> tuple[int, int, int]:
    pages = _field(event, "pages")
    logical = _field(event, "logical")
    page_elements = _field(event, "page")
    if (pages, logical, page_elements) != (
        PAGE_COUNT,
        LOGICAL_ELEMENTS,
        PAGE_ELEMENTS,
    ):
        raise _fail(
            event.line, "submit geometry does not match the controller"
        )
    if _field(event, "generation") == 0:
        raise _fail(event.line, "generation must be nonzero")
    tile_spans = [
        range(_field(event, key), _field(event, key) + FP64_TILE_WORDS)
        for key in ("token", "physical", "output")
    ]
    if any(span.start < 0 for span in tile_spans):
        raise _fail(event.line, "submit tile span is negative")
    if any(
        set(tile_spans[left]) & set(tile_spans[right])
        for left in range(len(tile_spans))
        for right in range(left + 1, len(tile_spans))
    ):
        raise _fail(event.line, "submit FP64 tile spans overlap")
    return pages, logical, page_elements


def analyze_events(events: Sequence[Event]) -> TraceAnalysis:
    if events[0].kind != "transparent_submit":
        raise _fail(events[0].line, "lifecycle does not start with submit")
    submit = events[0]
    pages_count, logical, page_elements = _validate_submit(submit)
    pages = [_MutablePage() for _ in range(pages_count)]
    inflight: dict[tuple[int, int], Event] = {}
    ready_ordinal = 0
    ready_unit: int | None = None
    fill_issue_order: list[int] = []
    retire: Event | None = None

    for event in events[1:]:
        if retire is not None:
            raise _fail(event.line, "target event follows retirement")
        if event.kind == "transparent_submit":
            raise _fail(event.line, "second submit appears before retirement")
        if event.kind == "page_ready":
            page = _field(event, "page")
            if not 0 <= page < pages_count:
                raise _fail(
                    event.line, "ready page is outside submit geometry"
                )
            if pages[page].ready is not None:
                raise _fail(event.line, f"page {page} became ready twice")
            unit = _field(event, "unit")
            if ready_unit is None:
                ready_unit = unit
            elif unit != ready_unit:
                raise _fail(
                    event.line, "page-ready unit changed mid-lifecycle"
                )
            count = event.fields["pages"]
            if not isinstance(count, str):
                raise _fail(event.line, "page-ready count is not text")
            match = READY_COUNT_RE.fullmatch(count)
            if match is None:
                raise _fail(
                    event.line, "page-ready count is not ordinal/total"
                )
            ready_ordinal += 1
            if (
                int(match.group("ordinal")) != ready_ordinal
                or int(match.group("total")) != pages_count
            ):
                raise _fail(event.line, "page-ready count is not cumulative")
            for key in ("scanned", "expected", "issued", "completed"):
                if _field(event, key) != page_elements:
                    raise _fail(event.line, f"page-ready {key} is incomplete")
            if _field(event, "sources_drained") not in (0, 1):
                raise _fail(event.line, "sources_drained is not Boolean")
            pages[page].ready = event.tick
            continue
        if event.kind == "transparent_issue":
            page = _field(event, "page")
            action = _field(event, "action")
            if not 0 <= page < pages_count or action not in ACTION_NAMES:
                raise _fail(event.line, "issue page or action is invalid")
            if _field(event, "offset") != page * page_elements:
                raise _fail(event.line, "issue offset does not name its page")
            if _field(event, "elements") != page_elements:
                raise _fail(event.line, "issue element count is not one page")
            page_state = pages[page]
            if action == 1:
                if page_state.fill is not None or any(
                    key[0] == page and key[1] == 1 for key in inflight
                ):
                    raise _fail(event.line, f"page {page} fill issued twice")
                fill_issue_order.append(page)
            elif action == 2:
                if page_state.fill is None or page_state.compute is not None:
                    raise _fail(event.line, "compute issued before one fill")
            else:
                if page_state.compute is None or page_state.store is not None:
                    raise _fail(event.line, "store issued before one compute")
            key = (page, action)
            if key in inflight:
                raise _fail(event.line, "duplicate in-flight action")
            inflight[key] = event
            continue
        if event.kind == "transparent_complete":
            page = _field(event, "page")
            action = _field(event, "action")
            key = (page, action)
            if key not in inflight:
                raise _fail(event.line, "completion has no matching issue")
            issue = inflight.pop(key)
            interval = Interval(issue.tick, event.tick)
            if interval.duration <= 0:
                raise _fail(event.line, "action duration is not positive")
            page_state = pages[page]
            if action == 1:
                page_state.fill = interval
            elif action == 2:
                page_state.compute = interval
            elif action == 3:
                page_state.store = interval
            else:
                raise _fail(event.line, "completion action is invalid")
            continue
        if event.kind == "transparent_retire":
            if _field(event, "pages") != pages_count:
                raise _fail(
                    event.line, "retire page count does not match submit"
                )
            retire = event
            continue
        raise _fail(event.line, f"unhandled target event {event.kind!r}")

    if retire is None:
        raise TraceFormatError("target lifecycle ended without retirement")
    if inflight:
        raise TraceFormatError(
            f"retirement left actions in flight: {sorted(inflight)}"
        )
    if ready_ordinal != pages_count:
        raise TraceFormatError("not every submitted page became ready")
    if fill_issue_order != list(range(pages_count)):
        raise TraceFormatError(
            f"fills were not admitted in logical order: {fill_issue_order}"
        )

    timelines: list[PageTimeline] = []
    for page, state in enumerate(pages):
        if (
            state.ready is None
            or state.fill is None
            or state.compute is None
            or state.store is None
        ):
            raise TraceFormatError(f"page {page} lifecycle is incomplete")
        if state.ready > state.fill.issue:
            raise TraceFormatError(f"page {page} fill preceded readiness")
        if state.fill.complete > state.compute.issue:
            raise TraceFormatError(f"page {page} compute overlapped its fill")
        if state.compute.complete > state.store.issue:
            raise TraceFormatError(f"page {page} store overlapped its compute")
        previous_store = timelines[-1].store.complete if timelines else None
        dependency_tick = state.ready
        prior_store_to_ready: int | None = None
        readiness_wait = 0
        slot_wait = 0
        if previous_store is not None:
            dependency_tick = max(dependency_tick, previous_store)
            prior_store_to_ready = state.ready - previous_store
            readiness_wait = max(0, prior_store_to_ready)
            slot_wait = max(0, -prior_store_to_ready)
        dispatch_gap = state.fill.issue - dependency_tick
        if dispatch_gap < 0:
            raise TraceFormatError(
                f"page {page} fill violated current one-slot ownership"
            )
        timelines.append(
            PageTimeline(
                page=page,
                ready=state.ready,
                fill=state.fill,
                compute=state.compute,
                store=state.store,
                submit_to_ready=state.ready - submit.tick,
                ready_to_fill_issue=state.fill.issue - state.ready,
                fill_complete_to_compute_issue=(
                    state.compute.issue - state.fill.complete
                ),
                compute_complete_to_store_issue=(
                    state.store.issue - state.compute.complete
                ),
                prior_store_to_ready=prior_store_to_ready,
                readiness_wait_after_prior_store=readiness_wait,
                slot_wait_after_ready=slot_wait,
                one_slot_dispatch_gap=dispatch_gap,
            )
        )
    if retire.tick < max(page.store.complete for page in timelines):
        raise _fail(retire.line, "retirement preceded final store completion")

    ideal = ideal_two_slot_schedule(tuple(timelines))
    return TraceAnalysis(
        submit_tick=submit.tick,
        retire_tick=retire.tick,
        logical_elements=logical,
        page_elements=page_elements,
        pages=tuple(timelines),
        ideal_two_slot=ideal,
    )


def ideal_two_slot_schedule(
    pages: Sequence[PageTimeline],
) -> tuple[IdealPageSchedule, ...]:
    """Schedule fixed trace durations on two inputs and one shared output.

    A single fill lane serves pages in logical order.  Input slot ``page % 2``
    is reusable after that page's compute completes.  A single shared output
    buffer serializes compute+store chains.  The fill lane is otherwise ideal
    and independent of the output lane.
    """

    if not pages:
        raise ValueError("ideal schedule requires at least one page")
    input_free = [0, 0]
    fill_lane_free = 0
    output_free = 0
    scheduled: list[IdealPageSchedule] = []
    for expected_page, page in enumerate(pages):
        if page.page != expected_page:
            raise ValueError("ideal schedule requires logical page order")
        slot = page.page % 2
        fill_issue = max(page.ready, input_free[slot], fill_lane_free)
        fill = Interval(fill_issue, fill_issue + page.fill.duration)
        fill_lane_free = fill.complete
        compute_issue = max(fill.complete, output_free)
        compute = Interval(
            compute_issue, compute_issue + page.compute.duration
        )
        input_free[slot] = compute.complete
        store = Interval(
            compute.complete, compute.complete + page.store.duration
        )
        output_free = store.complete
        scheduled.append(
            IdealPageSchedule(
                page=page.page,
                input_slot=slot,
                ready=page.ready,
                fill=fill,
                compute=compute,
                store=store,
            )
        )
    return tuple(scheduled)


def analyze_text(text: str) -> TraceAnalysis:
    return analyze_events(parse_events(text.splitlines()))


def analysis_dict(analysis: TraceAnalysis) -> dict[str, object]:
    return {
        "geometry": {
            "logical_elements": analysis.logical_elements,
            "page_elements": analysis.page_elements,
            "pages": len(analysis.pages),
        },
        "observed": {
            "submit_tick": analysis.submit_tick,
            "retire_tick": analysis.retire_tick,
            "submit_to_retire_ticks": analysis.observed_submit_to_retire,
            "first_ready_to_retire_ticks": (
                analysis.observed_first_ready_to_retire
            ),
            "final_store_to_retire_ticks": analysis.final_store_to_retire,
            "pages": [asdict(page) for page in analysis.pages],
        },
        "conditional_ideal_two_input_slots_shared_output": {
            "retire_tick": analysis.ideal_retire_tick,
            "submit_to_retire_ticks": analysis.ideal_submit_to_retire,
            "first_ready_to_retire_ticks": (
                analysis.ideal_first_ready_to_retire
            ),
            "observed_minus_bound_ticks": analysis.conditional_overlap_delta,
            "pages": [asdict(page) for page in analysis.ideal_two_slot],
            "assumptions": [
                "page-ready ticks are fixed to the trace",
                "each page keeps its observed fill/compute/store duration",
                "one in-order fill lane is independent of the shared output lane",
                "two input slots release only after matching compute completion",
                "one output slot serializes each compute-plus-store chain",
                "fixed durations do not inflate when the two lanes overlap",
                "dispatch gaps and cross-lane resource contention are absent",
            ],
            "claim_scope": "conditional critical-path lower bound, not speedup",
        },
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    args = parser.parse_args()
    try:
        raw = args.trace.read_bytes()
        text = raw.decode("utf-8", errors="strict")
        analysis = analyze_text(text)
    except (OSError, UnicodeDecodeError, TraceFormatError) as error:
        parser.error(str(error))
    output = analysis_dict(analysis)
    output["trace"] = {
        "path": str(args.trace.resolve()),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
