#!/usr/bin/env python3
"""Replay finite A-row/C-page gather schedules from index streams.

This is an ordering and cache-line-combiner model, not a timing simulator.  It
compares three schedules for each logical gather tile:

* ``full_row`` sees all descriptors and drains complete A bank-row groups;
* ``bounded4`` independently drains each 4K destination page;
* ``page_focus_r1`` is a deliberately naive diagnostic: it sees all
  descriptors, focuses the oldest destination page, drains at most four
  descriptors from one A row, and permits at most one non-focus claim.  The
  archived replay is expected to reject this policy if future-page outputs
  pollute the C combiner.

The model consumes the benchmark's archived index JSON directly.  It assumes
in-order source responses and immediately accepted retirement writes, so its C
write counts and page issue positions are mechanism proxies rather than gem5
latency predictions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import (
    OrderedDict,
    deque,
)
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Iterable,
    Sequence,
)

SCHEMA = "dx100-dual-locality-replay-v1"


def ceil_div(dividend: int, divisor: int) -> int:
    if dividend < 0 or divisor <= 0:
        raise ValueError("dividend must be non-negative and divisor positive")
    return (dividend + divisor - 1) // divisor


@dataclass(frozen=True)
class ReplayConfig:
    logical_elements: int = 16_384
    page_elements: int = 4_096
    word_bytes: int = 8
    cache_line_bytes: int = 64
    combine_slots: int = 384
    combine_ways: int = 4
    row_burst: int = 4
    nonfocus_row_bonus: int = 1

    def validate(self) -> None:
        values = (
            self.logical_elements,
            self.page_elements,
            self.word_bytes,
            self.cache_line_bytes,
            self.combine_slots,
            self.combine_ways,
            self.row_burst,
        )
        if any(value <= 0 for value in values):
            raise ValueError("capacities and sizes must be positive")
        if self.logical_elements % self.page_elements != 0:
            raise ValueError(
                "logical elements must be a multiple of page elements"
            )
        if self.cache_line_bytes % self.word_bytes != 0:
            raise ValueError(
                "cache line must contain an integer number of words"
            )
        if self.combine_slots % self.combine_ways != 0:
            raise ValueError("combiner slots must be divisible by ways")
        if not 0 <= self.nonfocus_row_bonus < self.row_burst:
            raise ValueError("non-focus bonus must be in [0, row burst)")

    @property
    def words_per_line(self) -> int:
        return self.cache_line_bytes // self.word_bytes

    @property
    def pages_per_tile(self) -> int:
        return self.logical_elements // self.page_elements


@dataclass(frozen=True)
class SourceDescriptor:
    source_line: int
    destinations: tuple[int, ...]

    def page_mask(self, page_elements: int) -> int:
        mask = 0
        for destination in self.destinations:
            mask |= 1 << (destination // page_elements)
        return mask


def validate_pattern(pattern: Sequence[int]) -> None:
    if not pattern:
        raise ValueError("pattern must not be empty")
    if any(
        not isinstance(index, int) or isinstance(index, bool) or index < 0
        for index in pattern
    ):
        raise ValueError("pattern indices must be non-negative integers")


def descriptorize(
    pattern: Sequence[int], config: ReplayConfig
) -> list[SourceDescriptor]:
    """Aggregate logical destinations by A source cache line."""
    config.validate()
    validate_pattern(pattern)
    destinations: OrderedDict[int, list[int]] = OrderedDict()
    for destination, source_index in enumerate(pattern):
        source_line = source_index // config.words_per_line
        destinations.setdefault(source_line, []).append(destination)
    return [
        SourceDescriptor(line, tuple(offsets))
        for line, offsets in destinations.items()
    ]


def bank_row_key(
    source_line: int, source_line_phase: int = 0
) -> tuple[int, ...]:
    """Return the DDR4 RoBaRaCoCh bank-row key used by the archived runs.

    The archived Ramulator2 setup has two interleaved channels, 128 column
    cache lines, four bank groups, and four banks.  ``source_line_phase`` is
    the A base address in cache-line units modulo the 4,096-line row period.
    Rank is fixed at zero and is therefore omitted.
    """
    if source_line < 0 or source_line_phase < 0:
        raise ValueError("source line and phase must be non-negative")
    line = source_line + source_line_phase
    channel = line & 0x1
    bank_group = (line >> 8) & 0x3
    bank = (line >> 10) & 0x3
    row = line >> 12
    return channel, bank_group, bank, row


def group_by_row(
    descriptors: Sequence[SourceDescriptor], source_line_phase: int
) -> list[SourceDescriptor]:
    rows: OrderedDict[tuple[int, ...], list[SourceDescriptor]] = OrderedDict()
    for descriptor in descriptors:
        rows.setdefault(
            bank_row_key(descriptor.source_line, source_line_phase), []
        ).append(descriptor)
    return [descriptor for row in rows.values() for descriptor in row]


def schedule_full_row(
    pattern: Sequence[int], config: ReplayConfig, source_line_phase: int
) -> tuple[list[SourceDescriptor], dict[str, int]]:
    schedule = group_by_row(descriptorize(pattern, config), source_line_phase)
    return schedule, {
        "focus_claims": 0,
        "nonfocus_row_bonus_claims": 0,
        "row_bursts": 0,
    }


def schedule_bounded_pages(
    pattern: Sequence[int], config: ReplayConfig, source_line_phase: int
) -> tuple[list[SourceDescriptor], dict[str, int]]:
    """Model independent Row/Offset drain epochs for each 4K page."""
    schedule: list[SourceDescriptor] = []
    for page_begin in range(0, len(pattern), config.page_elements):
        page_end = min(page_begin + config.page_elements, len(pattern))
        page_destinations: OrderedDict[int, list[int]] = OrderedDict()
        for destination in range(page_begin, page_end):
            source_line = pattern[destination] // config.words_per_line
            page_destinations.setdefault(source_line, []).append(destination)
        descriptors = [
            SourceDescriptor(line, tuple(offsets))
            for line, offsets in page_destinations.items()
        ]
        schedule.extend(group_by_row(descriptors, source_line_phase))
    return schedule, {
        "focus_claims": len(schedule),
        "nonfocus_row_bonus_claims": 0,
        "row_bursts": 0,
    }


def schedule_page_focus_r1(
    pattern: Sequence[int], config: ReplayConfig, source_line_phase: int
) -> tuple[list[SourceDescriptor], dict[str, int]]:
    """Schedule full descriptors with strict page focus and bounded row retain.

    Rows are round-robin within each page.  A selected row emits at most
    ``row_burst`` descriptors.  Focus-page descriptors are chosen first; at
    most ``nonfocus_row_bonus`` remaining slots may claim descriptors from the
    same row which do not touch the focus page.  A descriptor spanning several
    pages is fetched exactly once and satisfies every page in its mask.
    """
    descriptors = descriptorize(pattern, config)
    page_count = ceil_div(len(pattern), config.page_elements)
    if page_count > config.pages_per_tile:
        raise ValueError("pattern tile exceeds configured logical page count")

    rows: OrderedDict[tuple[int, ...], list[SourceDescriptor]] = OrderedDict()
    page_masks: dict[int, int] = {}
    for descriptor in descriptors:
        rows.setdefault(
            bank_row_key(descriptor.source_line, source_line_phase), []
        ).append(descriptor)
        page_masks[descriptor.source_line] = descriptor.page_mask(
            config.page_elements
        )

    row_list = list(rows.values())
    row_page_remaining = [
        [
            sum(
                bool(page_masks[item.source_line] & (1 << page))
                for item in row
            )
            for page in range(page_count)
        ]
        for row in row_list
    ]
    page_remaining = [
        sum(bool(mask & (1 << page)) for mask in page_masks.values())
        for page in range(page_count)
    ]
    page_rows = [
        deque(
            row_id
            for row_id, counts in enumerate(row_page_remaining)
            if counts[page] != 0
        )
        for page in range(page_count)
    ]
    remaining = {descriptor.source_line for descriptor in descriptors}
    schedule: list[SourceDescriptor] = []
    focus_claims = 0
    bonus_claims = 0
    row_bursts = 0

    def claim(descriptor: SourceDescriptor, focus_page: int) -> None:
        nonlocal focus_claims, bonus_claims
        line = descriptor.source_line
        if line not in remaining:
            raise AssertionError("source descriptor claimed twice")
        remaining.remove(line)
        schedule.append(descriptor)
        mask = page_masks[line]
        if mask & (1 << focus_page):
            focus_claims += 1
        else:
            bonus_claims += 1
        for page in range(page_count):
            if mask & (1 << page):
                page_remaining[page] -= 1

    for focus_page in range(page_count):
        queue = page_rows[focus_page]
        while page_remaining[focus_page] != 0:
            while queue and row_page_remaining[queue[0]][focus_page] == 0:
                queue.popleft()
            if not queue:
                raise AssertionError(
                    "focus page has descriptors but no eligible row"
                )
            row_id = queue.popleft()
            row = row_list[row_id]
            emitted = 0
            nonfocus_emitted = 0

            for descriptor in row:
                if emitted == config.row_burst:
                    break
                line = descriptor.source_line
                if line not in remaining:
                    continue
                if not page_masks[line] & (1 << focus_page):
                    continue
                claim(descriptor, focus_page)
                emitted += 1
                for page in range(page_count):
                    if page_masks[line] & (1 << page):
                        row_page_remaining[row_id][page] -= 1

            if emitted != config.row_burst:
                for descriptor in row:
                    if emitted == config.row_burst:
                        break
                    line = descriptor.source_line
                    if line not in remaining:
                        continue
                    if page_masks[line] & (1 << focus_page):
                        continue
                    if nonfocus_emitted == config.nonfocus_row_bonus:
                        break
                    claim(descriptor, focus_page)
                    emitted += 1
                    nonfocus_emitted += 1
                    for page in range(page_count):
                        if page_masks[line] & (1 << page):
                            row_page_remaining[row_id][page] -= 1

            if emitted == 0:
                raise AssertionError("eligible row emitted no descriptors")
            row_bursts += 1
            if row_page_remaining[row_id][focus_page] != 0:
                queue.append(row_id)

    if remaining:
        raise AssertionError("hybrid schedule left descriptors unclaimed")
    return schedule, {
        "focus_claims": focus_claims,
        "nonfocus_row_bonus_claims": bonus_claims,
        "row_bursts": row_bursts,
    }


def validate_schedule(
    schedule: Sequence[SourceDescriptor], words: int
) -> None:
    destinations = [
        destination
        for descriptor in schedule
        for destination in descriptor.destinations
    ]
    if len(destinations) != words or sorted(destinations) != list(
        range(words)
    ):
        raise AssertionError(
            "schedule does not retire every destination exactly once"
        )


def simulate_combiner(
    schedule: Sequence[SourceDescriptor], words: int, config: ReplayConfig
) -> dict[str, object]:
    """Replay the configured set-associative masked-write combiner."""
    sets = config.combine_slots // config.combine_ways
    slots: list[list[tuple[int, int] | None]] = [
        [None] * config.combine_ways for _ in range(sets)
    ]
    victims = [0] * sets
    full_mask = (1 << config.words_per_line) - 1
    pages = ceil_div(words, config.page_elements)
    page_writes = [0] * pages
    full_writes = 0
    partial_writes = 0

    def account_write(destination_line: int, full: bool) -> None:
        nonlocal full_writes, partial_writes
        if full:
            full_writes += 1
        else:
            partial_writes += 1
        first_word = destination_line * config.words_per_line
        page_writes[first_word // config.page_elements] += 1

    for descriptor in schedule:
        for destination in descriptor.destinations:
            destination_line = destination // config.words_per_line
            set_id = destination_line % sets
            target = next(
                (
                    way
                    for way, entry in enumerate(slots[set_id])
                    if entry is not None and entry[0] == destination_line
                ),
                None,
            )
            if target is None:
                target = next(
                    (
                        way
                        for way, entry in enumerate(slots[set_id])
                        if entry is None
                    ),
                    None,
                )
            if target is None:
                target = victims[set_id]
                victim = slots[set_id][target]
                if victim is None:
                    raise AssertionError("combiner chose an empty victim")
                account_write(victim[0], False)
                victims[set_id] = (target + 1) % config.combine_ways
                slots[set_id][target] = None

            entry = slots[set_id][target]
            mask = 0 if entry is None else entry[1]
            bit = 1 << (destination % config.words_per_line)
            if mask & bit:
                raise AssertionError(
                    "combiner received a duplicate destination word"
                )
            mask |= bit
            if mask == full_mask:
                account_write(destination_line, True)
                slots[set_id][target] = None
            else:
                slots[set_id][target] = (destination_line, mask)

    for set_slots in slots:
        for entry in set_slots:
            if entry is not None:
                account_write(entry[0], False)

    return {
        "c_writes": full_writes + partial_writes,
        "full_c_writes": full_writes,
        "partial_c_writes": partial_writes,
        "page_c_writes": page_writes,
    }


def schedule_metrics(
    schedule: Sequence[SourceDescriptor],
    words: int,
    config: ReplayConfig,
    source_line_phase: int,
    mechanism: dict[str, int],
) -> dict[str, object]:
    validate_schedule(schedule, words)
    row_keys = [
        bank_row_key(descriptor.source_line, source_line_phase)
        for descriptor in schedule
    ]
    page_count = ceil_div(words, config.page_elements)
    page_first = [0] * page_count
    page_last = [0] * page_count
    for ordinal, descriptor in enumerate(schedule, start=1):
        pages = {
            destination // config.page_elements
            for destination in descriptor.destinations
        }
        for page in pages:
            if page_first[page] == 0:
                page_first[page] = ordinal
            page_last[page] = ordinal

    combiner = simulate_combiner(schedule, words, config)
    minimum_writes = ceil_div(words, config.words_per_line)
    source_requests = len(schedule)
    unique_source_lines = len({item.source_line for item in schedule})
    same_row_successors = sum(
        left == right for left, right in zip(row_keys, row_keys[1:])
    )
    return {
        "logical_words": words,
        "source_read_requests": source_requests,
        "unique_source_lines": unique_source_lines,
        "duplicate_source_reads": source_requests - unique_source_lines,
        "unique_bank_rows": len(set(row_keys)),
        "same_bank_row_successors": same_row_successors,
        "source_successor_pairs": max(0, source_requests - 1),
        "minimum_c_writes": minimum_writes,
        "excess_c_writes": int(combiner["c_writes"]) - minimum_writes,
        "page_first_source_claim_ordinal": page_first,
        "page_issue_complete_ordinal": page_last,
        **combiner,
        **mechanism,
    }


POLICIES = {
    "full_row": schedule_full_row,
    "bounded4": schedule_bounded_pages,
    "page_focus_r1": schedule_page_focus_r1,
}


def analyze_pattern(
    pattern: Sequence[int], config: ReplayConfig, source_line_phase: int
) -> dict[str, object]:
    config.validate()
    validate_pattern(pattern)
    totals: dict[str, dict[str, object]] = {}
    page_last_sums: dict[str, list[int]] = {}
    page_first_sums: dict[str, list[int]] = {}
    page_observations: dict[str, list[int]] = {}
    tile_counts: dict[str, int] = {}
    gate_selected_tiles = 0
    gate_refetch_pressure = 0
    gate_unique_descriptors = 0

    integer_keys = (
        "logical_words",
        "source_read_requests",
        "unique_source_lines",
        "duplicate_source_reads",
        "unique_bank_rows",
        "same_bank_row_successors",
        "source_successor_pairs",
        "minimum_c_writes",
        "c_writes",
        "full_c_writes",
        "partial_c_writes",
        "excess_c_writes",
        "focus_claims",
        "nonfocus_row_bonus_claims",
        "row_bursts",
    )
    for policy in POLICIES:
        totals[policy] = {key: 0 for key in integer_keys}
        totals[policy]["page_c_writes"] = [0] * config.pages_per_tile
        page_last_sums[policy] = [0] * config.pages_per_tile
        page_first_sums[policy] = [0] * config.pages_per_tile
        page_observations[policy] = [0] * config.pages_per_tile
        tile_counts[policy] = 0

    for tile_begin in range(0, len(pattern), config.logical_elements):
        tile = pattern[tile_begin : tile_begin + config.logical_elements]
        tile_metrics: dict[str, dict[str, object]] = {}
        for policy, scheduler in POLICIES.items():
            schedule, mechanism = scheduler(tile, config, source_line_phase)
            metrics = schedule_metrics(
                schedule, len(tile), config, source_line_phase, mechanism
            )
            tile_metrics[policy] = metrics
            tile_counts[policy] += 1
            for key in integer_keys:
                totals[policy][key] = int(totals[policy][key]) + int(
                    metrics[key]
                )
            for page, writes in enumerate(metrics["page_c_writes"]):
                totals[policy]["page_c_writes"][page] += writes
            for page, ordinal in enumerate(
                metrics["page_issue_complete_ordinal"]
            ):
                page_last_sums[policy][page] += ordinal
                page_observations[policy][page] += 1
            for page, ordinal in enumerate(
                metrics["page_first_source_claim_ordinal"]
            ):
                page_first_sums[policy][page] += ordinal
        unique = int(tile_metrics["full_row"]["unique_source_lines"])
        refetch = int(tile_metrics["bounded4"]["duplicate_source_reads"])
        gate_unique_descriptors += unique
        gate_refetch_pressure += refetch
        if refetch * 16 >= unique:
            gate_selected_tiles += 1

    for policy, values in totals.items():
        pairs = int(values["source_successor_pairs"])
        values["logical_tiles"] = tile_counts[policy]
        values["same_bank_row_successor_rate"] = round(
            int(values["same_bank_row_successors"]) / pairs if pairs else 0.0,
            9,
        )
        minimum = int(values["minimum_c_writes"])
        values["c_write_amplification"] = round(
            int(values["c_writes"]) / minimum, 9
        )
        values["mean_page_first_source_claim_ordinal"] = [
            round(total / observations, 3) if observations else 0.0
            for total, observations in zip(
                page_first_sums[policy], page_observations[policy]
            )
        ]
        values["mean_page_issue_complete_ordinal"] = [
            round(total / observations, 3) if observations else 0.0
            for total, observations in zip(
                page_last_sums[policy], page_observations[policy]
            )
        ]
    bounded = totals["bounded4"]
    full = totals["full_row"]
    return {
        "pattern_words": len(pattern),
        "mode_gate": {
            "rule": "select_pfcc64_when_16x_cross_page_refetch_ge_unique",
            "threshold_fraction": "1/16",
            "cross_page_refetch_requests": gate_refetch_pressure,
            "unique_source_descriptors": gate_unique_descriptors,
            "selected_tiles": gate_selected_tiles,
            "total_tiles": tile_counts["full_row"],
        },
        "pfcc64_conservative_proxy_bounds": {
            "source_read_requests_lower": int(full["source_read_requests"]),
            "source_read_requests_upper": int(bounded["source_read_requests"]),
            "c_writes_target_upper": int(bounded["c_writes"]),
            "first_page_issue_complete_ordinal_target_upper": (
                bounded["mean_page_issue_complete_ordinal"][0]
            ),
            "qualification": (
                "mechanism bounds, not a response-order or latency replay"
            ),
        },
        "policies": totals,
    }


def load_gather_pattern(path: Path) -> list[int]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, list) or len(payload) != 1:
        raise ValueError(f"{path}: expected one-element JSON list")
    record = payload[0]
    if not isinstance(record, dict) or record.get("kernel") != "Gather":
        raise ValueError(f"{path}: expected one Gather record")
    pattern = record.get("pattern")
    if not isinstance(pattern, list):
        raise ValueError(f"{path}: missing pattern list")
    validate_pattern(pattern)
    return pattern


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def policy_state_lower_bound(
    source_descriptor_capacity: int = 16_384,
    row_slots: int = 2_048,
    row_entries: int = 8,
    slices: int = 32,
    pages: int = 4,
) -> dict[str, int]:
    """Return the incremental policy-state lower bound in bytes.

    Per-descriptor page masks and per-row/page descriptor counters make row
    eligibility a bounded 16-row x 8-entry operation.  Existing descriptors,
    Offset links, response buffers, and the C combiner are deliberately not
    counted again.
    """
    if (
        min(source_descriptor_capacity, row_slots, row_entries, slices, pages)
        <= 0
    ):
        raise ValueError("state dimensions must be positive")
    page_bits = max(1, math.ceil(math.log2(pages)))
    descriptor_page_mask_bytes = ceil_div(
        source_descriptor_capacity * pages, 8
    )
    row_page_counter_bits = max(1, math.ceil(math.log2(row_entries + 1)))
    row_page_counter_bytes = ceil_div(
        row_slots * pages * row_page_counter_bits, 8
    )
    rows_per_slice = row_slots // slices
    row_id_bits = max(1, math.ceil(math.log2(rows_per_slice)))
    active_row_bits = max(1, math.ceil(math.log2(rows_per_slice + 1)))
    burst_bits = max(1, math.ceil(math.log2(4 + 1)))
    per_slice_bits = row_id_bits + active_row_bits + burst_bits + 1
    slice_control_bytes = ceil_div(slices * per_slice_bits, 8)
    focus_control_bytes = ceil_div(page_bits + 1, 8)
    page_counter_bits = max(
        1, math.ceil(math.log2(source_descriptor_capacity + 1))
    )
    page_remaining_counter_bytes = ceil_div(pages * page_counter_bits, 8)
    total = (
        descriptor_page_mask_bytes
        + row_page_counter_bytes
        + slice_control_bytes
        + focus_control_bytes
        + page_remaining_counter_bytes
    )
    return {
        "descriptor_page_mask_bytes": descriptor_page_mask_bytes,
        "row_page_descriptor_counter_bytes": row_page_counter_bytes,
        "slice_control_bytes": slice_control_bytes,
        "focus_control_bytes": focus_control_bytes,
        "page_remaining_counter_bytes": page_remaining_counter_bytes,
        "incremental_policy_bytes": total,
    }


def pfcc64_state_lower_bound(
    source_descriptor_capacity: int = 16_384,
    offset_entries: int = 16_384,
    row_slots: int = 2_048,
    row_entries: int = 8,
    slices: int = 32,
    pages: int = 4,
    carry_lines: int = 64,
    words_per_line: int = 8,
    line_bytes: int = 64,
) -> dict[str, int]:
    """Incremental bit-packed state for the proposed PFCC-64 mechanism."""
    dimensions = (
        source_descriptor_capacity,
        offset_entries,
        row_slots,
        row_entries,
        slices,
        pages,
        carry_lines,
        words_per_line,
        line_bytes,
    )
    if min(dimensions) <= 0:
        raise ValueError("PFCC-64 state dimensions must be positive")
    pointer_bits = math.ceil(math.log2(offset_entries + 1))
    # Existing descriptors already have one head and one tail.  PFCC adds the
    # other three page heads and tails.
    page_subchain_pointer_delta_bytes = ceil_div(
        source_descriptor_capacity * (pages - 1) * 2 * pointer_bits, 8
    )
    descriptor_page_mask_bytes = ceil_div(
        source_descriptor_capacity * pages, 8
    )
    row_page_counter_bits = math.ceil(math.log2(row_entries + 1))
    row_page_counter_bytes = ceil_div(
        row_slots * pages * row_page_counter_bits, 8
    )
    rows_per_slice = row_slots // slices
    row_cursor_bits = math.ceil(math.log2(rows_per_slice))
    active_row_bits = math.ceil(math.log2(rows_per_slice + 1))
    grow_quantum_bits = math.ceil(math.log2(128 + 1))
    slice_control_bytes = ceil_div(
        slices * (row_cursor_bits + active_row_bits + grow_quantum_bits), 8
    )
    focus_control_bytes = ceil_div(math.ceil(math.log2(pages)) + 1, 8)
    page_counter_bits = math.ceil(math.log2(source_descriptor_capacity + 1))
    page_remaining_counter_bytes = ceil_div(pages * page_counter_bits, 8)
    carry_payload_bytes = carry_lines * line_bytes
    destination_lines = ceil_div(offset_entries, words_per_line)
    line_tag_bits = math.ceil(math.log2(destination_lines))
    carry_line_metadata_bits = (
        line_tag_bits + words_per_line * 2 + math.ceil(math.log2(pages)) + 1
    )
    carry_line_metadata_bytes = ceil_div(
        carry_lines * carry_line_metadata_bits, 8
    )
    carry_offset_token_bytes = ceil_div(
        carry_lines * words_per_line * pointer_bits, 8
    )
    tentative_offset_bitmap_bytes = ceil_div(offset_entries, 8)
    mode_gate_counter_bytes = ceil_div(page_counter_bits * 2 + 1, 8)
    parts = {
        "page_subchain_pointer_delta_bytes": page_subchain_pointer_delta_bytes,
        "descriptor_page_mask_bytes": descriptor_page_mask_bytes,
        "row_page_descriptor_counter_bytes": row_page_counter_bytes,
        "slice_control_bytes": slice_control_bytes,
        "focus_control_bytes": focus_control_bytes,
        "page_remaining_counter_bytes": page_remaining_counter_bytes,
        "carry_payload_bytes": carry_payload_bytes,
        "carry_line_metadata_bytes": carry_line_metadata_bytes,
        "carry_offset_token_bytes": carry_offset_token_bytes,
        "tentative_offset_bitmap_bytes": tentative_offset_bitmap_bytes,
        "mode_gate_counter_bytes": mode_gate_counter_bytes,
    }
    parts["incremental_pfcc64_bytes"] = sum(parts.values())
    return parts


def _input_record(
    path: Path, phase: int, analysis: dict[str, object]
) -> dict[str, object]:
    return {
        "path": str(path),
        "sha256": sha256(path),
        "source_line_phase": phase,
        **analysis,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xrage", type=Path)
    parser.add_argument("--flag-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--xrage-source-line-phase", type=int, default=3_585)
    parser.add_argument("--flag-source-line-phase", type=int, default=0)
    parser.add_argument("--row-burst", type=int, default=4)
    parser.add_argument("--nonfocus-row-bonus", type=int, default=1)
    args = parser.parse_args()
    if args.xrage is None and args.flag_root is None:
        parser.error("at least one of --xrage or --flag-root is required")

    config = ReplayConfig(
        row_burst=args.row_burst,
        nonfocus_row_bonus=args.nonfocus_row_bonus,
    )
    config.validate()
    output: dict[str, object] = {
        "schema": SCHEMA,
        "model_scope": {
            "claim": "ordering_and_combiner_proxy_only",
            "source_responses": "assumed_in_issue_order",
            "retirement_writes": "assumed_immediately_accepted",
            "timing_prediction": False,
        },
        "config": {key: value for key, value in vars(config).items()},
        "policy_state_lower_bound": policy_state_lower_bound(),
        "pfcc64_state_lower_bound": pfcc64_state_lower_bound(),
    }
    if args.xrage is not None:
        pattern = load_gather_pattern(args.xrage)
        output["xrage"] = _input_record(
            args.xrage,
            args.xrage_source_line_phase,
            analyze_pattern(pattern, config, args.xrage_source_line_phase),
        )
    if args.flag_root is not None:
        paths = sorted(args.flag_root.glob("**/config_*_gather.json"))
        if not paths:
            raise SystemExit(f"no FLAG gather inputs below {args.flag_root}")
        cases = []
        for path in paths:
            pattern = load_gather_pattern(path)
            cases.append(
                _input_record(
                    path,
                    args.flag_source_line_phase,
                    analyze_pattern(
                        pattern, config, args.flag_source_line_phase
                    ),
                )
            )
        output["flag"] = {
            "case_count": len(cases),
            "source_line_phase_limitation": (
                "A base address was not archived in the FLAG digest; phase zero "
                "is a declared row-boundary proxy"
            ),
            "cases": cases,
        }

    rendered = json.dumps(output, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.write_text(rendered)


if __name__ == "__main__":
    main()
