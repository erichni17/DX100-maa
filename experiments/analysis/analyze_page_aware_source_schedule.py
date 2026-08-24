#!/usr/bin/env python3
"""Offline gate for bounded page-aware hybrid source ordering.

The model joins authenticated ``dx100.physical_admission.v1`` descriptor
records to an observed ``event=source_issue`` order.  It never runs gem5.  A
line is issued exactly once even when descriptors on several logical output
pages reference it; a finite page mask on the existing line entry carries that
relationship.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Iterable,
    Sequence,
)

PHYSICAL_SCHEMA = "dx100.physical_admission.v1"
SOURCE_ISSUE_RE = re.compile(
    r"event=source_issue\b.*?\bsequence=(\d+)\b.*?\baddr=(0x[0-9a-fA-F]+)\b"
)
BUILD_BEGIN_RE = re.compile(r"\bevent=build_begin\b")


class GateError(ValueError):
    """Raised when an input cannot support an exact ordering comparison."""


@dataclass(frozen=True)
class Descriptor:
    iteration: int
    line: int
    row: tuple[int, int, int, int, int]
    word: int
    trace_line: int = 0


@dataclass(frozen=True)
class IssueEvent:
    sequence: int
    line: int
    trace_line: int
    epoch: int = 0


@dataclass(frozen=True)
class LineRecord:
    line: int
    row: tuple[int, int, int, int, int]
    page_mask: int
    descriptors: int
    first_iteration: int
    epoch: int = 0
    request_id: int = 0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integer(value: object, field: str) -> int:
    try:
        return int(str(value), 0)
    except (TypeError, ValueError) as exc:
        raise GateError(f"invalid integer field {field}={value!r}") from exc


def load_descriptors(path: Path) -> list[Descriptor]:
    records: list[Descriptor] = []
    with path.open() as stream:
        for line_number, text in enumerate(stream, 1):
            try:
                raw = json.loads(text)
            except json.JSONDecodeError as exc:
                raise GateError(
                    f"invalid JSON at {path}:{line_number}"
                ) from exc
            if (
                raw.get("schema") != PHYSICAL_SCHEMA
                or raw.get("event") != "physical_admission"
                or raw.get("provenance") != "direct_index_descriptor_admission"
            ):
                raise GateError(
                    f"wrong physical record schema at line {line_number}"
                )
            line_address = _integer(raw.get("a_line_paddr"), "a_line_paddr")
            address = _integer(raw.get("a_paddr"), "a_paddr")
            word = _integer(raw.get("wid"), "wid")
            if (
                line_address & 63
                or not line_address <= address < line_address + 64
            ):
                raise GateError(
                    f"invalid A-line identity at line {line_number}"
                )
            if word < 0 or word > 7:
                raise GateError(f"invalid FP64 word at line {line_number}")
            records.append(
                Descriptor(
                    iteration=_integer(raw.get("itr"), "itr"),
                    line=line_address,
                    row=tuple(
                        _integer(raw.get(field), field)
                        for field in (
                            "channel",
                            "rank",
                            "bank_group",
                            "bank",
                            "row",
                        )
                    ),
                    word=word,
                    trace_line=_integer(raw.get("trace_line"), "trace_line"),
                )
            )
    return records


def load_issue_events(path: Path) -> list[IssueEvent]:
    by_sequence: dict[int, IssueEvent] = {}
    epoch = -1
    with path.open() as stream:
        for trace_line, text in enumerate(stream, 1):
            if BUILD_BEGIN_RE.search(text):
                epoch += 1
            match = SOURCE_ISSUE_RE.search(text)
            if not match:
                continue
            if epoch < 0:
                raise GateError(
                    "source issue appears before a finite build wave"
                )
            sequence, line = int(match.group(1)), int(match.group(2), 16)
            if sequence in by_sequence:
                raise GateError(f"duplicate source issue sequence {sequence}")
            by_sequence[sequence] = IssueEvent(
                sequence, line, trace_line, epoch
            )
    if not by_sequence:
        raise GateError(f"no source_issue events in {path}")
    expected = list(range(len(by_sequence)))
    if sorted(by_sequence) != expected:
        raise GateError("source issue sequences are not contiguous from zero")
    return [by_sequence[index] for index in expected]


def load_issue_order(path: Path) -> list[int]:
    return [event.line for event in load_issue_events(path)]


def build_lines(
    descriptors: Iterable[Descriptor],
    logical_elements: int,
    page_elements: int,
) -> tuple[list[LineRecord], int]:
    if logical_elements <= 0 or page_elements <= 0:
        raise GateError("logical and page element counts must be positive")
    pages = math.ceil(logical_elements / page_elements)
    if pages <= 1 or pages > 64:
        raise GateError("the finite page mask supports between 2 and 64 pages")
    records = list(descriptors)
    iterations = [record.iteration for record in records]
    if len(records) != logical_elements or set(iterations) != set(
        range(logical_elements)
    ):
        raise GateError(
            "descriptor iterations must cover the logical set exactly once"
        )

    grouped: dict[int, list[Descriptor]] = {}
    for record in records:
        grouped.setdefault(record.line, []).append(record)
    lines: list[LineRecord] = []
    for line, members in grouped.items():
        rows = {member.row for member in members}
        if len(rows) != 1:
            raise GateError(
                f"A line 0x{line:x} has inconsistent DRAM-row identity"
            )
        page_mask = 0
        for member in members:
            page_mask |= 1 << (member.iteration // page_elements)
        lines.append(
            LineRecord(
                line=line,
                row=next(iter(rows)),
                page_mask=page_mask,
                descriptors=len(members),
                first_iteration=min(member.iteration for member in members),
                request_id=line,
            )
        )
    lines.sort(key=lambda record: record.line)
    return lines, pages


def reconstruct_request_instances(
    descriptors: Sequence[Descriptor],
    issues: Sequence[IssueEvent],
    logical_elements: int,
    page_elements: int,
) -> tuple[list[LineRecord], int]:
    """Join same-file admissions to finite source-request epochs."""
    _, pages = build_lines(descriptors, logical_elements, page_elements)
    if any(record.trace_line <= 0 for record in descriptors):
        raise GateError("physical records need positive raw trace_line fields")
    if any(event.trace_line <= 0 for event in issues):
        raise GateError("source issues need positive trace line identities")
    ordered_descriptors = sorted(
        descriptors, key=lambda record: record.trace_line
    )
    if len({record.trace_line for record in ordered_descriptors}) != len(
        ordered_descriptors
    ):
        raise GateError("physical admission trace lines are not unique")

    cursor = 0
    pending: dict[int, list[Descriptor]] = {}
    result: list[LineRecord] = []
    for event in sorted(issues, key=lambda item: item.trace_line):
        while (
            cursor < len(ordered_descriptors)
            and ordered_descriptors[cursor].trace_line < event.trace_line
        ):
            descriptor = ordered_descriptors[cursor]
            pending.setdefault(descriptor.line, []).append(descriptor)
            cursor += 1
        members = pending.pop(event.line, None)
        if not members:
            raise GateError(
                f"source issue {event.sequence} has no pending descriptors"
            )
        rows = {member.row for member in members}
        if len(rows) != 1:
            raise GateError("request instance crosses DRAM rows")
        page_mask = 0
        for member in members:
            page_mask |= 1 << (member.iteration // page_elements)
        result.append(
            LineRecord(
                line=event.line,
                row=next(iter(rows)),
                page_mask=page_mask,
                descriptors=len(members),
                first_iteration=min(member.iteration for member in members),
                epoch=event.epoch,
                request_id=event.sequence,
            )
        )
    if cursor != len(ordered_descriptors) or pending:
        raise GateError("physical admissions remain after final source issue")
    if sum(record.descriptors for record in result) != logical_elements:
        raise GateError("request instances do not close semantic descriptors")
    return result, pages


def _first_page(mask: int) -> int:
    if mask <= 0:
        raise GateError("empty page mask")
    return (mask & -mask).bit_length() - 1


def page_major_row_order(lines: Sequence[LineRecord]) -> list[LineRecord]:
    return sorted(
        lines,
        key=lambda record: (
            _first_page(record.page_mask),
            record.row,
            record.line,
            record.first_iteration,
        ),
    )


def row_first_page_order(lines: Sequence[LineRecord]) -> list[LineRecord]:
    return sorted(
        lines,
        key=lambda record: (
            record.row,
            _first_page(record.page_mask),
            record.line,
            record.first_iteration,
        ),
    )


def least_complete_score_order(
    lines: Sequence[LineRecord],
    pages: int,
    page_weight: int = 8,
    row_bonus: int = 3,
    global_totals: Sequence[int] | None = None,
    completed_counts: list[int] | None = None,
) -> list[LineRecord]:
    """Select finite row heads with page pressure and a row-hit bonus.

    The implementation model keeps one sorted queue per already-existing DRAM
    row.  At every issue it targets the page with the smallest completed-line
    fraction (page number breaks exact ties).  A target-page hit is weighted
    above the maximum row/share bonus, so page pressure remains authoritative;
    the row bonus only chooses among target-page candidates.
    """
    if page_weight <= row_bonus + pages - 1:
        raise GateError("page weight must dominate all locality/share bonuses")
    rows: dict[tuple[int, int, int, int, int], list[LineRecord]] = {}
    for record in lines:
        rows.setdefault(record.row, []).append(record)
    for queue in rows.values():
        queue.sort(key=lambda record: (record.line, record.first_iteration))

    local_totals = [
        sum(bool(record.page_mask & (1 << page)) for record in lines)
        for page in range(pages)
    ]
    totals = list(global_totals) if global_totals is not None else local_totals
    completed = (
        completed_counts if completed_counts is not None else [0] * pages
    )
    if (
        len(totals) != pages
        or len(completed) != pages
        or any(total <= 0 for total in totals)
    ):
        raise GateError("score counters do not cover every logical page")
    active_pages = [page for page, total in enumerate(local_totals) if total]
    if not active_pages:
        raise GateError("score epoch has no page contributors")
    positions = {row: 0 for row in rows}
    current_row: tuple[int, int, int, int, int] | None = None
    result: list[LineRecord] = []
    while len(result) < len(lines):
        target = min(
            active_pages,
            key=lambda page: (completed[page] / totals[page], page),
        )
        candidates = [
            queue[positions[row]]
            for row, queue in rows.items()
            if positions[row] < len(queue)
        ]

        def candidate_key(record: LineRecord) -> tuple:
            score = (
                page_weight * bool(record.page_mask & (1 << target))
                + row_bonus * (record.row == current_row)
                + record.page_mask.bit_count()
                - 1
            )
            # max() plus negated deterministic identities: score first, then
            # the lexicographically smallest row/line/iteration.
            return (
                score,
                tuple(-value for value in record.row),
                -record.line,
                -record.first_iteration,
            )

        selected = max(candidates, key=candidate_key)
        result.append(selected)
        positions[selected.row] += 1
        current_row = selected.row
        for page in range(pages):
            completed[page] += bool(selected.page_mask & (1 << page))
    return result


def order_metrics(
    name: str, order: Sequence[LineRecord], pages: int, semantic_work: int
) -> dict[str, object]:
    if not order or len({record.request_id for record in order}) != len(order):
        raise GateError(f"{name} is not an exact request-instance permutation")
    row_transitions = sum(
        left.row != right.row for left, right in zip(order, order[1:])
    )
    last_row_by_bank: dict[tuple[int, int, int, int], int] = {}
    bank_local_activations = 0
    for record in order:
        bank, row = record.row[:4], record.row[4]
        if last_row_by_bank.get(bank) != row:
            bank_local_activations += 1
            last_row_by_bank[bank] = row
    last = []
    contributors = []
    unique_page_lines = []
    for page in range(pages):
        positions = [
            position
            for position, record in enumerate(order)
            if record.page_mask & (1 << page)
        ]
        if not positions:
            raise GateError(f"page {page} has no contributors")
        last.append(max(positions))
        contributors.append(len(positions))
        unique_page_lines.append(
            len(
                {
                    record.line
                    for record in order
                    if record.page_mask & (1 << page)
                }
            )
        )
    first_page = min(range(pages), key=lambda page: (last[page], page))
    first_position = last[first_page]
    remaining = len(order) - first_position - 1
    mean_tail = sum(len(order) - position - 1 for position in last) / pages
    order_digest = hashlib.sha256(
        b"".join(
            record.line.to_bytes(8, "little")
            + record.request_id.to_bytes(8, "little")
            for record in order
        )
    ).hexdigest()
    unique_lines = len({record.line for record in order})
    return {
        "policy": name,
        "line_order_sha256": order_digest,
        "semantic_descriptors": semantic_work,
        "unique_a_lines": unique_lines,
        "requests": len(order),
        "coalesced_descriptor_requests_avoided": semantic_work - len(order),
        "finite_epoch_a_line_reissues": len(order) - unique_lines,
        "descriptors_per_request": semantic_work / len(order),
        "unique_dram_rows": len({record.row for record in order}),
        "row_transitions": row_transitions,
        "activation_run_proxy": row_transitions + 1,
        "bank_local_activation_proxy": bank_local_activations,
        "page_contributor_requests": contributors,
        "page_unique_a_lines": unique_page_lines,
        "page_last_contributor_zero_based": last,
        "page_last_contributor_one_based": [position + 1 for position in last],
        "first_ready_page": first_page,
        "first_page_last_contributor_zero_based": first_position,
        "all_pages_last_contributor_zero_based": max(last),
        "optimistic_source_requests_after_first_page_ready": remaining,
        "optimistic_source_tail_overlap_ceiling_fraction": remaining
        / len(order),
        "mean_page_ready_tail_fraction": mean_tail / len(order),
    }


def metadata_cost(
    observed_lines: int,
    capacity_lines: int,
    pages: int,
) -> dict[str, dict[str, int]]:
    if capacity_lines < observed_lines or observed_lines <= 0:
        raise GateError("line capacity must cover the observed unique lines")
    counter_bits = max(1, math.ceil(math.log2(capacity_lines + 1)))
    target_bits = max(1, math.ceil(math.log2(pages)))

    def cost(line_slots: int, policy: str) -> dict[str, int]:
        mask_bits = 0 if policy == "current" else line_slots * pages
        if policy == "page_major_then_row":
            control_bits = pages * counter_bits
        elif policy == "row_first_target_page_tie":
            control_bits = pages * counter_bits + target_bits
        elif policy == "least_complete_score":
            control_bits = 2 * pages * counter_bits + target_bits
        else:
            control_bits = 0
        total = mask_bits + control_bits
        return {
            "line_page_mask_bits": mask_bits,
            "page_counter_bits": control_bits,
            "total_bits": total,
            "total_bytes_ceil": (total + 7) // 8,
        }

    return {
        policy: {
            "observed": cost(observed_lines, policy),
            "provisioned": cost(capacity_lines, policy),
        }
        for policy in (
            "current",
            "page_major_then_row",
            "row_first_target_page_tie",
            "least_complete_score",
        )
    }


def analyze(
    descriptors: Sequence[Descriptor],
    current_lines: Sequence[int] | Sequence[IssueEvent],
    logical_elements: int = 16_384,
    page_elements: int = 4_096,
    capacity_lines: int = 8_192,
    page_weight: int = 8,
    row_bonus: int = 3,
) -> dict[str, object]:
    if current_lines and isinstance(current_lines[0], IssueEvent):
        current, pages = reconstruct_request_instances(
            descriptors,
            current_lines,  # type: ignore[arg-type]
            logical_elements,
            page_elements,
        )
    else:
        lines, pages = build_lines(
            descriptors, logical_elements, page_elements
        )
        by_address = {record.line: record for record in lines}
        if len(current_lines) != len(lines) or set(current_lines) != set(
            by_address
        ):
            raise GateError(
                "current issue order does not exactly cover A lines"
            )
        current = [by_address[line] for line in current_lines]  # type: ignore[index]

    by_epoch: dict[int, list[LineRecord]] = {}
    for record in current:
        by_epoch.setdefault(record.epoch, []).append(record)

    def per_epoch(ordering) -> list[LineRecord]:
        ordered: list[LineRecord] = []
        for epoch in sorted(by_epoch):
            ordered.extend(ordering(by_epoch[epoch]))
        return ordered

    policies = {
        "current": current,
        "page_major_then_row": per_epoch(page_major_row_order),
        "row_first_target_page_tie": per_epoch(row_first_page_order),
    }
    score_totals = [
        sum(bool(record.page_mask & (1 << page)) for record in current)
        for page in range(pages)
    ]
    score_completed = [0] * pages
    policies["least_complete_score"] = []
    for epoch in sorted(by_epoch):
        policies["least_complete_score"].extend(
            least_complete_score_order(
                by_epoch[epoch],
                pages,
                page_weight,
                row_bonus,
                score_totals,
                score_completed,
            )
        )
    metrics = {
        name: order_metrics(name, order, pages, len(descriptors))
        for name, order in policies.items()
    }
    reference_requests = {record.request_id for record in current}
    for name, order in policies.items():
        if {
            record.request_id for record in order
        } != reference_requests or sum(
            record.descriptors for record in order
        ) != logical_elements:
            raise GateError(f"{name} changed semantic/request work")
    max_epoch_lines = max(len(records) for records in by_epoch.values())
    return {
        "schema": "dx100.hybrid_page_aware_source_schedule.v1",
        "logical_elements": logical_elements,
        "page_elements": page_elements,
        "pages": pages,
        "finite_epochs": len(by_epoch),
        "max_epoch_requests": max_epoch_lines,
        "score": {"page_weight": page_weight, "row_bonus": row_bonus},
        "policies": metrics,
        "ordering_metadata": metadata_cost(
            max_epoch_lines, capacity_lines, pages
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("descriptors", type=Path)
    parser.add_argument("issue_trace", type=Path)
    parser.add_argument("--logical-elements", type=int, default=16_384)
    parser.add_argument("--page-elements", type=int, default=4_096)
    parser.add_argument("--capacity-lines", type=int, default=8_192)
    parser.add_argument("--page-weight", type=int, default=8)
    parser.add_argument("--row-bonus", type=int, default=3)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    result = analyze(
        load_descriptors(args.descriptors),
        load_issue_events(args.issue_trace),
        args.logical_elements,
        args.page_elements,
        args.capacity_lines,
        args.page_weight,
        args.row_bonus,
    )
    result["inputs"] = {
        "descriptors": str(args.descriptors),
        "descriptors_sha256": sha256(args.descriptors),
        "issue_trace": str(args.issue_trace),
        "issue_trace_sha256": sha256(args.issue_trace),
    }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        args.json_out.write_text(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    main()
