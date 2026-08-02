#!/usr/bin/env python3
"""Deterministic replay of a bounded, single-owner hybrid gather scheduler.

The replay consumes static gather index streams.  It executes the corrected
policy's ownership, request, response, write-acceptance, and write-completion
transitions.  Transition counts are deterministic work counts, not cycles.
The archive has no response timing or backpressure trace, so this module is
deliberately not a timing model.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import heapq
import json
import math
from collections import (
    OrderedDict,
    deque,
)
from dataclasses import (
    asdict,
    dataclass,
    field,
)
from pathlib import Path
from typing import Sequence

SCHEMA = "dx100-corrected-hybrid-single-owner-replay-v3"
ARCHIVED_SOURCE_LINE_BITS = 18
GENERATION_BITS = 64
REQUEST_ID_BITS = 64
VALUE_BITS = 64
SOURCE_RESPONSE_PAYLOAD_WORDS = 8
MAX_SOURCE_LINE = (1 << ARCHIVED_SOURCE_LINE_BITS) - 1
MAX_GENERATION = (1 << GENERATION_BITS) - 1
MAX_REQUEST_ID = (1 << REQUEST_ID_BITS) - 1
VALUE_MASK = (1 << VALUE_BITS) - 1
MAX_SOURCE_RESPONSE_DIAGNOSTIC = MAX_REQUEST_ID
WORK_COUNTER_NAMES = (
    "atomic_transition_invocations",
    "descriptor_word_scans",
    "focus_page_counter_scans",
    "focus_rebuild_source_scans",
    "focus_membership_line_scans",
    "focus_heap_pushes",
    "focus_heap_pops",
    "focus_heap_reinserts",
    "row_directory_scans",
    "planning_line_scans",
    "sort_input_items",
    "sort_comparison_bound",
    "reservation_token_walks",
    "promotion_owner_scans",
    "promotion_token_walks",
    "response_admission_field_checks",
    "response_admission_payload_word_checks",
    "response_admission_diagnostic_updates",
    "response_match_probes",
    "response_payload_word_builds",
    "response_payload_word_checks",
    "response_token_prechecks",
    "ready_owner_scans",
    "write_token_walks",
    "write_ack_match_entry_scans",
)


def deterministic_source_value(source_word: int) -> int:
    """Return a deterministic, bijective 64-bit value for one source word."""
    if source_word < 0:
        raise ValueError("source word must be non-negative")
    return (source_word * 0x9E3779B185EBCA87 + 0xD1B54A32D192ED03) & VALUE_MASK


def bounded_transition(method):
    """Charge and bound one externally visible logical state transition."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        outermost = self._atomic_depth == 0
        if outermost:
            self._atomic_start_work = self.functional_work_total
            self._atomic_name = method.__name__
            self._charge("atomic_transition_invocations", 1)
        self._atomic_depth += 1
        try:
            return method(self, *args, **kwargs)
        finally:
            self._atomic_depth -= 1
            if outermost:
                work = self.functional_work_total - self._atomic_start_work
                if work > self.atomic_transition_work_limit:
                    raise AssertionError(
                        f"{self._atomic_name} exceeded atomic work bound: "
                        f"{work} > {self.atomic_transition_work_limit}"
                    )
                self.atomic_transition_work_high_water = max(
                    self.atomic_transition_work_high_water, work
                )
                self._atomic_name = None

    return wrapper


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
    owner_lines: int = 384
    owner_ways: int = 4
    source_request_slots: int = 4
    source_response_slots: int = 4
    write_request_slots: int = 8
    write_ack_slots: int = 8
    new_focus_owner_lines_per_request: int = 384
    new_future_owner_lines_per_request: int = 1
    row_burst: int = 128

    def validate(self) -> None:
        positive = (
            self.logical_elements,
            self.page_elements,
            self.word_bytes,
            self.cache_line_bytes,
            self.combine_slots,
            self.combine_ways,
            self.owner_lines,
            self.owner_ways,
            self.source_request_slots,
            self.source_response_slots,
            self.write_request_slots,
            self.write_ack_slots,
            self.new_focus_owner_lines_per_request,
            self.new_future_owner_lines_per_request,
            self.row_burst,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("sizes and finite capacities must be positive")
        if self.logical_elements % self.page_elements:
            raise ValueError("logical elements must be page aligned")
        if self.cache_line_bytes % self.word_bytes:
            raise ValueError("cache line must hold an integer number of words")
        if (
            self.word_bytes != VALUE_BITS // 8
            or self.cache_line_bytes
            != SOURCE_RESPONSE_PAYLOAD_WORDS * (VALUE_BITS // 8)
        ):
            raise ValueError(
                "replay requires one 64-byte cache line with exactly eight "
                "64-bit source-response words"
            )
        if self.combine_slots % self.combine_ways:
            raise ValueError("combiner slots must be divisible by ways")
        if self.owner_lines % self.owner_ways:
            raise ValueError("owner lines must be divisible by ways")
        if (
            self.new_focus_owner_lines_per_request > self.owner_lines
            or self.new_future_owner_lines_per_request > self.owner_lines
        ):
            raise ValueError("per-request owner allocation exceeds capacity")

    @property
    def words_per_line(self) -> int:
        return self.cache_line_bytes // self.word_bytes

    @property
    def pages_per_tile(self) -> int:
        return self.logical_elements // self.page_elements

    @property
    def owner_sets(self) -> int:
        return self.owner_lines // self.owner_ways

    @property
    def destination_lines(self) -> int:
        return ceil_div(self.logical_elements, self.words_per_line)

    @property
    def max_response_tokens(self) -> int:
        return self.owner_lines * self.words_per_line


@dataclass(frozen=True)
class SourceDescriptor:
    source_line: int
    destinations: tuple[int, ...]


@dataclass
class LineOwner:
    line: int
    generation: int
    expected_mask: int
    allocation_sequence: int
    received_mask: int = 0
    reserved_mask: int = 0
    tokens: dict[int, int] = field(default_factory=dict)
    reservation_request_ids: dict[int, int] = field(default_factory=dict)
    reservation_source_lines: dict[int, int] = field(default_factory=dict)
    payload: dict[int, int] = field(default_factory=dict)
    state: str = "collecting"
    write_request_id: int | None = None


@dataclass(frozen=True)
class SourceRequest:
    request_id: int
    generation: int
    source_line: int
    destinations: tuple[int, ...]


@dataclass(frozen=True)
class SourceResponse:
    request_id: int
    generation: int
    source_line: int
    payload: tuple[int, ...]


@dataclass(frozen=True)
class WriteRequest:
    request_id: int
    generation: int
    line: int
    mask: int
    destinations: tuple[int, ...]


HARDWARE_POLICY_STATE = "hardware_policy_state"
REPLAY_EVIDENCE_OBSERVER_STATE = "replay_evidence_observer_state"

# This inventory names only state that persists in the finite replay model.
# Function locals, temporary Python containers, object headers, hash-table
# slack, and other interpreter implementation overhead are deliberately out of
# scope.  A field appears once, at the component that stores its bit-packed
# logical representation.  The unit tests compare this inventory with the
# scheduler's actual assignments and embedded record schemas.
_CONFIG_FIELDS = (
    "logical_elements",
    "page_elements",
    "word_bytes",
    "cache_line_bytes",
    "combine_slots",
    "combine_ways",
    "owner_lines",
    "owner_ways",
    "source_request_slots",
    "source_response_slots",
    "write_request_slots",
    "write_ack_slots",
    "new_focus_owner_lines_per_request",
    "new_future_owner_lines_per_request",
    "row_burst",
)
_OWNER_METADATA_FIELDS = (
    "line",
    "generation",
    "expected_mask",
    "allocation_sequence",
    "received_mask",
    "reserved_mask",
    "tokens",
    "reservation_request_ids",
    "reservation_source_lines",
    "state",
    "write_request_id",
)
_SOURCE_REQUEST_FIELDS = (
    "request_id",
    "generation",
    "source_line",
    "destinations",
)
_SOURCE_RESPONSE_FIELDS = (
    "request_id",
    "generation",
    "source_line",
    "payload",
)
_WRITE_REQUEST_FIELDS = (
    "request_id",
    "generation",
    "line",
    "mask",
    "destinations",
)
_EVENT_COUNTER_FIELDS = (
    "transition_steps",
    "source_request_issues",
    "source_request_acceptances",
    "source_response_completions",
    "write_request_issues",
    "write_request_acceptances",
    "write_completions",
    "owner_promotions",
    "owner_allocations",
    "owner_allocation_refusals",
    "focus_switches",
    "stale_source_responses",
    "forged_source_responses",
    "malformed_source_responses",
    "stale_write_responses",
    "same_bank_row_successors",
    "source_successor_pairs",
    "row_rotations",
    "row_same_reselections",
)
_HIGH_WATER_FIELDS = (
    "owner",
    "source_request",
    "accepted_source",
    "source_response",
    "write_request",
    "write_ack",
)


def _inventory_entries(
    classification: str, component: str, fields: Sequence[str]
) -> tuple[tuple[str, str, str], ...]:
    return tuple((field, classification, component) for field in fields)


PERSISTENT_FIELD_INVENTORY = (
    *_inventory_entries(
        HARDWARE_POLICY_STATE,
        "configuration_image_bits",
        tuple(
            f"CorrectedHybridScheduler.config.{name}"
            for name in _CONFIG_FIELDS
        )
        + ("CorrectedHybridScheduler.source_line_phase",),
    ),
    *_inventory_entries(
        HARDWARE_POLICY_STATE,
        "source_mapping_table_bits",
        ("CorrectedHybridScheduler.pattern",),
    ),
    *_inventory_entries(
        HARDWARE_POLICY_STATE,
        "live_predicate_bits",
        ("CorrectedHybridScheduler.live",),
    ),
    *_inventory_entries(
        HARDWARE_POLICY_STATE,
        "exact_live_mask_table_bits",
        ("CorrectedHybridScheduler.expected_masks",),
    ),
    *_inventory_entries(
        HARDWARE_POLICY_STATE,
        "destination_line_directory_bits",
        ("CorrectedHybridScheduler.line_destinations",),
    ),
    *_inventory_entries(
        HARDWARE_POLICY_STATE,
        "source_target_directory_bits",
        ("CorrectedHybridScheduler.source_targets",),
    ),
    *_inventory_entries(
        HARDWARE_POLICY_STATE,
        "source_pending_directory_bits",
        ("CorrectedHybridScheduler.source_pending_lines",),
    ),
    *_inventory_entries(
        HARDWARE_POLICY_STATE,
        "token_state_bits",
        ("CorrectedHybridScheduler.token_state",),
    ),
    *_inventory_entries(
        HARDWARE_POLICY_STATE,
        "progress_state_bits",
        (
            "CorrectedHybridScheduler.remaining_live_words",
            "CorrectedHybridScheduler.page_remaining",
        ),
    ),
    *_inventory_entries(
        HARDWARE_POLICY_STATE,
        "focus_row_structure_bits",
        ("CorrectedHybridScheduler.focus_rows",),
    ),
    *_inventory_entries(
        HARDWARE_POLICY_STATE,
        "focus_membership_bits",
        ("CorrectedHybridScheduler.focus_heap_members",),
    ),
    *_inventory_entries(
        HARDWARE_POLICY_STATE,
        "selector_state_bits",
        (
            "CorrectedHybridScheduler.focus_page",
            "CorrectedHybridScheduler.active_row",
            "CorrectedHybridScheduler.active_row_remaining",
            "CorrectedHybridScheduler.owner_set_counts",
        ),
    ),
    *_inventory_entries(
        HARDWARE_POLICY_STATE,
        "owner_payload_bits",
        ("CorrectedHybridScheduler.owners[].LineOwner.payload",),
    ),
    *_inventory_entries(
        HARDWARE_POLICY_STATE,
        "owner_metadata_bits",
        tuple(
            f"CorrectedHybridScheduler.owners[].LineOwner.{name}"
            for name in _OWNER_METADATA_FIELDS
        ),
    ),
    *_inventory_entries(
        HARDWARE_POLICY_STATE,
        "source_request_queue_bits",
        tuple(
            "CorrectedHybridScheduler.source_requests[]"
            f".SourceRequest.{name}"
            for name in _SOURCE_REQUEST_FIELDS
        ),
    ),
    *_inventory_entries(
        HARDWARE_POLICY_STATE,
        "accepted_source_ledger_bits",
        tuple(
            "CorrectedHybridScheduler.accepted_source_requests[]"
            f".SourceRequest.{name}"
            for name in _SOURCE_REQUEST_FIELDS
        ),
    ),
    *_inventory_entries(
        HARDWARE_POLICY_STATE,
        "source_response_event_queue_bits",
        tuple(
            "CorrectedHybridScheduler.source_responses[]"
            f".SourceResponse.{name}"
            for name in _SOURCE_RESPONSE_FIELDS
        ),
    ),
    *_inventory_entries(
        HARDWARE_POLICY_STATE,
        "write_request_queue_bits",
        tuple(
            "CorrectedHybridScheduler.write_requests[]" f".WriteRequest.{name}"
            for name in _WRITE_REQUEST_FIELDS
        ),
    ),
    *_inventory_entries(
        HARDWARE_POLICY_STATE,
        "write_ack_queue_bits",
        tuple(
            f"CorrectedHybridScheduler.write_acks[].WriteRequest.{name}"
            for name in _WRITE_REQUEST_FIELDS
        ),
    ),
    *_inventory_entries(
        HARDWARE_POLICY_STATE,
        "identity_state_bits",
        (
            "CorrectedHybridScheduler.generation",
            "CorrectedHybridScheduler.next_source_request_id",
            "CorrectedHybridScheduler.next_write_request_id",
            "CorrectedHybridScheduler.next_allocation_sequence",
        ),
    ),
    *_inventory_entries(
        REPLAY_EVIDENCE_OBSERVER_STATE,
        "payload_oracle_observer_bits",
        (
            "CorrectedHybridScheduler.expected_destination_values",
            "CorrectedHybridScheduler.destination_values",
            "CorrectedHybridScheduler.destination_receive_counts",
        ),
    ),
    *_inventory_entries(
        REPLAY_EVIDENCE_OBSERVER_STATE,
        "functional_work_accounting_bits",
        tuple(
            f"CorrectedHybridScheduler.work_counts.{name}"
            for name in WORK_COUNTER_NAMES
        )
        + (
            "CorrectedHybridScheduler.functional_work_total",
            "CorrectedHybridScheduler._atomic_depth",
            "CorrectedHybridScheduler._atomic_start_work",
            "CorrectedHybridScheduler._atomic_name",
            "CorrectedHybridScheduler.atomic_transition_work_high_water",
            "CorrectedHybridScheduler.atomic_transition_work_limit",
        ),
    ),
    *_inventory_entries(
        REPLAY_EVIDENCE_OBSERVER_STATE,
        "execution_event_counter_bits",
        tuple(
            f"CorrectedHybridScheduler.{name}"
            for name in _EVENT_COUNTER_FIELDS
        ),
    ),
    *_inventory_entries(
        REPLAY_EVIDENCE_OBSERVER_STATE,
        "ordering_observer_state_bits",
        ("CorrectedHybridScheduler.previous_source_row",),
    ),
    *_inventory_entries(
        REPLAY_EVIDENCE_OBSERVER_STATE,
        "high_water_observer_bits",
        tuple(
            f"CorrectedHybridScheduler.high_water.{name}"
            for name in _HIGH_WATER_FIELDS
        ),
    ),
)


def persistent_field_inventory() -> list[dict[str, str]]:
    """Return the complete field-level finite-state classification."""
    return [
        {
            "field": field,
            "classification": classification,
            "component": component,
        }
        for field, classification, component in PERSISTENT_FIELD_INVENTORY
    ]


def validate_pattern(pattern: Sequence[int]) -> None:
    if not pattern:
        raise ValueError("pattern must not be empty")
    if any(
        not isinstance(index, int) or isinstance(index, bool) or index < 0
        for index in pattern
    ):
        raise ValueError("pattern indices must be non-negative integers")


def validate_live_mask(pattern: Sequence[int], live: Sequence[bool]) -> None:
    if len(pattern) != len(live):
        raise ValueError("predicate/live mask length must match the pattern")
    if any(not isinstance(value, bool) for value in live):
        raise ValueError("predicate/live mask entries must be booleans")


def bank_row_key(
    source_line: int, source_line_phase: int = 0
) -> tuple[int, int, int, int]:
    """Archived DDR4 RoBaRaCoCh bank-row key, with rank fixed at zero."""
    if source_line < 0 or source_line_phase < 0:
        raise ValueError("source line and phase must be non-negative")
    line = source_line + source_line_phase
    if line > MAX_SOURCE_LINE:
        raise ValueError(
            f"source line plus phase exceeds {ARCHIVED_SOURCE_LINE_BITS}-bit "
            "archived field"
        )
    return line & 1, (line >> 8) & 3, (line >> 10) & 3, line >> 12


def exact_live_masks(
    live: Sequence[bool], words_per_line: int
) -> dict[int, int]:
    masks: dict[int, int] = {}
    for destination, is_live in enumerate(live):
        if not is_live:
            continue
        line, word = divmod(destination, words_per_line)
        masks[line] = masks.get(line, 0) | (1 << word)
    return masks


def descriptorize(
    pattern: Sequence[int], live: Sequence[bool], config: ReplayConfig
) -> list[SourceDescriptor]:
    destinations: OrderedDict[int, list[int]] = OrderedDict()
    for destination, source_index in enumerate(pattern):
        if live[destination]:
            source_line = source_index // config.words_per_line
            destinations.setdefault(source_line, []).append(destination)
    return [
        SourceDescriptor(line, tuple(offsets))
        for line, offsets in destinations.items()
    ]


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
    pattern: Sequence[int],
    live: Sequence[bool],
    config: ReplayConfig,
    source_line_phase: int,
) -> list[SourceDescriptor]:
    return group_by_row(
        descriptorize(pattern, live, config), source_line_phase
    )


def schedule_direct4(
    pattern: Sequence[int],
    live: Sequence[bool],
    config: ReplayConfig,
    source_line_phase: int,
) -> list[SourceDescriptor]:
    schedule: list[SourceDescriptor] = []
    for begin in range(0, len(pattern), config.page_elements):
        end = min(begin + config.page_elements, len(pattern))
        page_pattern = pattern[begin:end]
        page_live = live[begin:end]
        page_descriptors = descriptorize(page_pattern, page_live, config)
        page_descriptors = [
            SourceDescriptor(
                descriptor.source_line,
                tuple(
                    begin + destination
                    for destination in descriptor.destinations
                ),
            )
            for descriptor in page_descriptors
        ]
        schedule.extend(group_by_row(page_descriptors, source_line_phase))
    return schedule


def validate_schedule(
    schedule: Sequence[SourceDescriptor], live: Sequence[bool]
) -> None:
    observed = sorted(
        destination
        for descriptor in schedule
        for destination in descriptor.destinations
    )
    expected = [index for index, is_live in enumerate(live) if is_live]
    if observed != expected:
        raise AssertionError("schedule does not cover every live word once")


def _source_order_metrics(
    source_lines: Sequence[int], source_line_phase: int
) -> dict[str, int | float]:
    rows = [bank_row_key(line, source_line_phase) for line in source_lines]
    same_row = sum(left == right for left, right in zip(rows, rows[1:]))
    pairs = max(0, len(rows) - 1)
    unique = len(set(source_lines))
    return {
        "source_request_issues": len(source_lines),
        "source_request_acceptances": len(source_lines),
        "source_response_completions": len(source_lines),
        "unique_source_lines": unique,
        "duplicate_source_reads": len(source_lines) - unique,
        "same_bank_row_successors": same_row,
        "source_successor_pairs": pairs,
        "same_bank_row_successor_rate": round(
            same_row / pairs if pairs else 0.0, 9
        ),
    }


def simulate_reference_combiner(
    schedule: Sequence[SourceDescriptor],
    live: Sequence[bool],
    config: ReplayConfig,
) -> dict[str, int]:
    """Finite reference combiner with immediate write acceptance/completion."""
    sets = config.combine_slots // config.combine_ways
    slots: list[list[tuple[int, int] | None]] = [
        [None] * config.combine_ways for _ in range(sets)
    ]
    victims = [0] * sets
    expected = exact_live_masks(live, config.words_per_line)
    physical_full_mask = (1 << config.words_per_line) - 1
    writes = exact_writes = partial_writes = physical_full_writes = 0

    def account(line: int, mask: int) -> None:
        nonlocal writes, exact_writes, partial_writes, physical_full_writes
        writes += 1
        if mask == expected[line]:
            exact_writes += 1
        else:
            partial_writes += 1
        if mask == physical_full_mask:
            physical_full_writes += 1

    for descriptor in schedule:
        for destination in descriptor.destinations:
            line, word = divmod(destination, config.words_per_line)
            set_id = line % sets
            target = next(
                (
                    way
                    for way, entry in enumerate(slots[set_id])
                    if entry is not None and entry[0] == line
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
                    raise AssertionError("combiner selected an empty victim")
                account(*victim)
                victims[set_id] = (target + 1) % config.combine_ways
                slots[set_id][target] = None
            entry = slots[set_id][target]
            mask = 0 if entry is None else entry[1]
            bit = 1 << word
            if mask & bit:
                raise AssertionError("reference combiner saw a duplicate word")
            mask |= bit
            if mask == expected[line]:
                account(line, mask)
                slots[set_id][target] = None
            else:
                slots[set_id][target] = (line, mask)

    for set_slots in slots:
        for entry in set_slots:
            if entry is not None:
                account(*entry)
    return {
        "c_write_requests": writes,
        "c_write_acceptances": writes,
        "c_write_completions": writes,
        "exact_live_mask_writes": exact_writes,
        "partial_live_mask_writes": partial_writes,
        "full_physical_mask_writes": physical_full_writes,
    }


def reference_metrics(
    name: str,
    schedule: Sequence[SourceDescriptor],
    live: Sequence[bool],
    config: ReplayConfig,
    source_line_phase: int,
) -> dict[str, object]:
    validate_schedule(schedule, live)
    source_lines = [descriptor.source_line for descriptor in schedule]
    preissue = (
        min(len(live), config.page_elements)
        if name == "direct4"
        else len(live)
    )
    return {
        "policy_kind": "immediate-response ordering reference",
        "logical_words": len(live),
        "live_words": sum(live),
        "predicated_false_words": len(live) - sum(live),
        "index_scan_words": len(live),
        "preissue_barrier_words": preissue,
        "transition_steps": 0,
        "owner_promotions": 0,
        "owner_allocations": 0,
        "owner_allocation_refusals": 0,
        "focus_switches": 0,
        "stale_source_responses": 0,
        "stale_write_responses": 0,
        "owner_high_water": 0,
        "source_request_queue_high_water": 0,
        "source_response_queue_high_water": 0,
        "write_request_queue_high_water": 0,
        "write_ack_queue_high_water": 0,
        **_source_order_metrics(source_lines, source_line_phase),
        **simulate_reference_combiner(schedule, live, config),
    }


class CorrectedHybridScheduler:
    """Executable bounded transition model for CHSO-384.

    A destination line has exactly one owner from allocation through matching
    write response.  Source issue reserves tokens and downstream response
    credit.  Write acceptance moves a request into the bounded ACK queue but
    does not commit tokens or free the owner.
    """

    def __init__(
        self,
        pattern: Sequence[int],
        live: Sequence[bool] | None = None,
        config: ReplayConfig | None = None,
        source_line_phase: int = 0,
        generation: int = 1,
    ) -> None:
        validate_pattern(pattern)
        self.config = config or ReplayConfig()
        self.config.validate()
        if len(pattern) > self.config.logical_elements:
            raise ValueError("pattern exceeds one configured logical tile")
        self.pattern = list(pattern)
        self.live = list(live) if live is not None else [True] * len(pattern)
        validate_live_mask(self.pattern, self.live)
        if not 0 < generation <= MAX_GENERATION:
            raise ValueError(
                f"generation must fit the nonzero {GENERATION_BITS}-bit field"
            )
        if not 0 <= source_line_phase <= MAX_SOURCE_LINE:
            raise ValueError(
                f"source-line phase must fit {ARCHIVED_SOURCE_LINE_BITS} bits"
            )
        self.generation = generation
        self.source_line_phase = source_line_phase
        self.work_counts = {name: 0 for name in WORK_COUNTER_NAMES}
        self.functional_work_total = 0
        self._atomic_depth = 0
        self._atomic_start_work = 0
        self._atomic_name: str | None = None
        self.atomic_transition_work_high_water = 0
        self.atomic_transition_work_limit = (
            1
            + self.config.logical_elements
            * (self.config.destination_lines + 3)
            + 4 * self.config.max_response_tokens
            + self.config.owner_lines * (self.config.words_per_line + 3)
            + 64
        )
        self.expected_masks = exact_live_masks(
            self.live, self.config.words_per_line
        )
        self.expected_destination_values = [
            (
                deterministic_source_value(source_word)
                if self.live[destination]
                else None
            )
            for destination, source_word in enumerate(self.pattern)
        ]
        self.destination_values: list[int | None] = [None] * len(self.pattern)
        self.destination_receive_counts = [0] * len(self.pattern)
        self.line_destinations: dict[int, list[int]] = {}
        self.source_targets: dict[int, dict[int, list[int]]] = {}
        for destination, source_index in enumerate(self.pattern):
            if not self.live[destination]:
                continue
            destination_line = destination // self.config.words_per_line
            source_line = source_index // self.config.words_per_line
            if source_line + self.source_line_phase > MAX_SOURCE_LINE:
                raise ValueError(
                    "source line plus phase does not fit the archived "
                    f"{ARCHIVED_SOURCE_LINE_BITS}-bit field"
                )
            self.line_destinations.setdefault(destination_line, []).append(
                destination
            )
            self.source_targets.setdefault(source_line, {}).setdefault(
                destination_line, []
            ).append(destination)
        self.source_pending_lines = {
            source: set(targets)
            for source, targets in self.source_targets.items()
        }
        self.token_state = [
            "unconsumed" if is_live else "predicated_false"
            for is_live in self.live
        ]
        self.remaining_live_words = sum(self.live)
        self.page_remaining = [0] * ceil_div(
            len(pattern), self.config.page_elements
        )
        for destination, is_live in enumerate(self.live):
            if is_live:
                self.page_remaining[
                    destination // self.config.page_elements
                ] += 1
        self.focus_page = 0
        self.focus_rows: dict[tuple[int, ...], list[int]] = {}
        self.focus_heap_members: set[int] = set()
        self.active_row: tuple[int, ...] | None = None
        self.active_row_remaining = 0
        self.owners: dict[int, LineOwner] = {}
        self.owner_set_counts = [0] * self.config.owner_sets
        self.source_requests: deque[SourceRequest] = deque()
        self.accepted_source_requests: dict[int, SourceRequest] = {}
        self.source_responses: deque[SourceResponse] = deque()
        self.write_requests: deque[WriteRequest] = deque()
        self.write_acks: deque[WriteRequest] = deque()
        self.next_source_request_id = 1
        self.next_write_request_id = 1
        self.next_allocation_sequence = 1
        self.transition_steps = 0
        self.source_request_issues = 0
        self.source_request_acceptances = 0
        self.source_response_completions = 0
        self.write_request_issues = 0
        self.write_request_acceptances = 0
        self.write_completions = 0
        self.owner_promotions = 0
        self.owner_allocations = 0
        self.owner_allocation_refusals = 0
        self.focus_switches = 0
        self.stale_source_responses = 0
        self.forged_source_responses = 0
        self.malformed_source_responses = 0
        self.stale_write_responses = 0
        self.previous_source_row: tuple[int, ...] | None = None
        self.same_bank_row_successors = 0
        self.source_successor_pairs = 0
        self.row_rotations = 0
        self.row_same_reselections = 0
        self.high_water = {
            "owner": 0,
            "source_request": 0,
            "accepted_source": 0,
            "source_response": 0,
            "write_request": 0,
            "write_ack": 0,
        }
        self._charge("descriptor_word_scans", len(self.pattern))
        self._advance_focus()
        if not self.focus_rows and self.focus_page < len(self.page_remaining):
            self._rebuild_focus_heap()

    def _charge(self, name: str, amount: int = 1) -> None:
        if name not in self.work_counts:
            raise AssertionError(f"unknown functional-work category {name}")
        if amount < 0:
            raise AssertionError("functional work cannot be negative")
        self.work_counts[name] += amount
        self.functional_work_total += amount

    def _charged_sorted(self, values, *, key=None):
        materialized = list(values)
        count = len(materialized)
        self._charge("sort_input_items", count)
        if count > 1:
            self._charge(
                "sort_comparison_bound", count * math.ceil(math.log2(count))
            )
        return sorted(materialized, key=key)

    def _update_high_water(self) -> None:
        current = {
            "owner": len(self.owners),
            "source_request": len(self.source_requests),
            "accepted_source": len(self.accepted_source_requests),
            "source_response": len(self.source_responses),
            "write_request": len(self.write_requests),
            "write_ack": len(self.write_acks),
        }
        for name, value in current.items():
            self.high_water[name] = max(self.high_water[name], value)

    def _line_page(self, line: int) -> int:
        return (line * self.config.words_per_line) // self.config.page_elements

    def _source_has_focus_work(self, source_line: int) -> bool:
        for line in self.source_pending_lines.get(source_line, set()):
            self._charge("focus_membership_line_scans", 1)
            if self._line_page(line) == self.focus_page:
                return True
        return False

    def _push_focus_source(self, source_line: int) -> None:
        if (
            source_line in self.focus_heap_members
            or not self._source_has_focus_work(source_line)
        ):
            return
        key = bank_row_key(source_line, self.source_line_phase)
        heapq.heappush(self.focus_rows.setdefault(key, []), source_line)
        self._charge("focus_heap_pushes", 1)
        self.focus_heap_members.add(source_line)

    @bounded_transition
    def _rebuild_focus_heap(self) -> None:
        self.focus_rows.clear()
        self.focus_heap_members.clear()
        self.active_row = None
        self.active_row_remaining = 0
        if self.focus_page >= len(self.page_remaining):
            return
        for source_line in self.source_pending_lines:
            self._charge("focus_rebuild_source_scans", 1)
            self._push_focus_source(source_line)

    @bounded_transition
    def _advance_focus(self) -> bool:
        old = self.focus_page
        while self.focus_page < len(self.page_remaining):
            self._charge("focus_page_counter_scans", 1)
            if self.page_remaining[self.focus_page] != 0:
                break
            self.focus_page += 1
        if self.focus_page != old:
            self.focus_switches += self.focus_page - old
            self._rebuild_focus_heap()
            return True
        return False

    def _owner_set_occupancy(self, line: int) -> int:
        return self.owner_set_counts[line % self.config.owner_sets]

    def _can_allocate_owner(self, line: int) -> bool:
        return (
            line not in self.owners
            and len(self.owners) < self.config.owner_lines
            and self._owner_set_occupancy(line) < self.config.owner_ways
        )

    def _allocate_owner(self, line: int) -> LineOwner:
        if not self._can_allocate_owner(line):
            raise AssertionError("owner allocation was not credit checked")
        if self.next_allocation_sequence > self.config.destination_lines:
            raise AssertionError("finite owner-allocation sequence exhausted")
        owner = LineOwner(
            line=line,
            generation=self.generation,
            expected_mask=self.expected_masks[line],
            allocation_sequence=self.next_allocation_sequence,
        )
        self.next_allocation_sequence += 1
        self.owners[line] = owner
        self.owner_set_counts[line % self.config.owner_sets] += 1
        self.owner_allocations += 1
        self._update_high_water()
        return owner

    def _reserve_line_tokens(
        self, source_line: int, line: int, request_id: int
    ) -> list[int]:
        owner = self.owners[line]
        selected = []
        for destination in self.source_targets[source_line][line]:
            self._charge("reservation_token_walks", 1)
            if self.token_state[destination] == "unconsumed":
                selected.append(destination)
        if not selected:
            self.source_pending_lines[source_line].discard(line)
            return []
        for destination in selected:
            _, word = divmod(destination, self.config.words_per_line)
            bit = 1 << word
            if owner.expected_mask & bit == 0:
                raise AssertionError("owner reserved a predicated-false word")
            if (owner.received_mask | owner.reserved_mask) & bit:
                raise AssertionError("destination word has two owners")
            self.token_state[destination] = "reserved"
            owner.reserved_mask |= bit
            owner.tokens[word] = destination
            owner.reservation_request_ids[word] = request_id
            owner.reservation_source_lines[word] = source_line
        self.source_pending_lines[source_line].discard(line)
        return selected

    def _plan_and_reserve(
        self, source_line: int, request_id: int
    ) -> tuple[int, ...]:
        pending = self.source_pending_lines.get(source_line)
        if not pending:
            return ()
        selected: list[int] = []
        owned_candidates = []
        new_candidates = []
        for line in pending:
            self._charge("planning_line_scans", 1)
            if line in self.owners:
                owned_candidates.append(line)
            else:
                new_candidates.append(line)
        owned = self._charged_sorted(
            owned_candidates,
            key=lambda line: self.owners[line].allocation_sequence,
        )
        for line in owned:
            selected.extend(
                self._reserve_line_tokens(source_line, line, request_id)
            )

        new_lines = self._charged_sorted(
            new_candidates,
            key=lambda line: (
                self._line_page(line) != self.focus_page,
                self._line_page(line),
                line,
            ),
        )
        focus_allocations = 0
        future_allocations = 0
        for line in new_lines:
            is_focus = self._line_page(line) == self.focus_page
            if is_focus:
                if (
                    focus_allocations
                    == self.config.new_focus_owner_lines_per_request
                ):
                    continue
            elif (
                future_allocations
                == self.config.new_future_owner_lines_per_request
            ):
                continue
            if not self._can_allocate_owner(line):
                self.owner_allocation_refusals += 1
                continue
            self._allocate_owner(line)
            if is_focus:
                focus_allocations += 1
            else:
                future_allocations += 1
            selected.extend(
                self._reserve_line_tokens(source_line, line, request_id)
            )
        return tuple(self._charged_sorted(selected))

    def _promotion_source(self) -> int | None:
        for owner in self.owners.values():
            self._charge("promotion_owner_scans", 1)
            if owner.state != "collecting":
                continue
            for destination in self.line_destinations[owner.line]:
                self._charge("promotion_token_walks", 1)
                if self.token_state[destination] == "unconsumed":
                    return (
                        self.pattern[destination] // self.config.words_per_line
                    )
        return None

    def _pop_focus_candidate(self) -> int | None:
        """Select one eligible source, rotating rows at quantum expiry."""

        def pop_row(row: tuple[int, ...]) -> int | None:
            sources = self.focus_rows.get(row)
            while sources:
                source_line = heapq.heappop(sources)
                self._charge("focus_heap_pops", 1)
                self.focus_heap_members.discard(source_line)
                if self._source_has_focus_work(source_line):
                    if not sources:
                        del self.focus_rows[row]
                    return source_line
            self.focus_rows.pop(row, None)
            return None

        if self.active_row is not None and self.active_row_remaining > 0:
            selected_source = pop_row(self.active_row)
            if selected_source is not None:
                return selected_source

        previous_row = self.active_row
        rows = self._charged_sorted(self.focus_rows)
        if previous_row is None:
            ordered_rows = rows
        else:
            greater_rows = []
            lower_rows = []
            same_rows = []
            for row in rows:
                self._charge("row_directory_scans", 1)
                if row > previous_row:
                    greater_rows.append(row)
                elif row < previous_row:
                    lower_rows.append(row)
                else:
                    same_rows.append(row)
            ordered_rows = greater_rows + lower_rows + same_rows
        for selected_row in ordered_rows:
            selected_source = pop_row(selected_row)
            if selected_source is None:
                continue
            if previous_row is not None:
                if selected_row != previous_row:
                    self.row_rotations += 1
                else:
                    self.row_same_reselections += 1
            self.active_row = selected_row
            self.active_row_remaining = self.config.row_burst
            return selected_source

        self.active_row = None
        self.active_row_remaining = 0
        return None

    @bounded_transition
    def issue_source_request(self) -> bool:
        if len(self.source_requests) >= self.config.source_request_slots:
            return False
        reserved_response_credits = len(self.source_requests) + len(
            self.accepted_source_requests
        )
        if reserved_response_credits >= self.config.source_response_slots:
            return False
        if self.next_source_request_id > MAX_REQUEST_ID:
            raise RuntimeError("finite source request-ID space exhausted")
        request_id = self.next_source_request_id

        promotion: int | None = None
        candidate = self._pop_focus_candidate()
        selected_source: int | None = None
        destinations: tuple[int, ...] = ()
        if candidate is not None:
            destinations = self._plan_and_reserve(candidate, request_id)
            if destinations:
                selected_source = candidate
            else:
                self._push_focus_source(candidate)
        if selected_source is None:
            promotion = self._promotion_source()
            if promotion is not None:
                destinations = self._plan_and_reserve(promotion, request_id)
                if destinations:
                    selected_source = promotion
        if selected_source is None:
            return False
        if promotion is not None and selected_source == promotion:
            self.owner_promotions += 1
        request = SourceRequest(
            request_id,
            self.generation,
            selected_source,
            destinations,
        )
        self.next_source_request_id += 1
        self.source_requests.append(request)
        self.source_request_issues += 1
        selected_row = bank_row_key(selected_source, self.source_line_phase)
        if self.previous_source_row is not None:
            self.source_successor_pairs += 1
            if selected_row == self.previous_source_row:
                self.same_bank_row_successors += 1
        self.previous_source_row = selected_row
        if (
            self.active_row
            == bank_row_key(selected_source, self.source_line_phase)
            and self.active_row_remaining
        ):
            self.active_row_remaining -= 1
        self._push_focus_source(selected_source)
        self._update_high_water()
        return True

    def _source_payload(self, source_line: int) -> tuple[int, ...]:
        begin = source_line * self.config.words_per_line
        self._charge(
            "response_payload_word_builds", self.config.words_per_line
        )
        return tuple(
            deterministic_source_value(begin + word)
            for word in range(self.config.words_per_line)
        )

    @bounded_transition
    def accept_source_request(self, auto_respond: bool = True) -> bool:
        if not self.source_requests:
            return False
        if auto_respond and len(self.source_responses) >= (
            self.config.source_response_slots
        ):
            return False
        request = self.source_requests.popleft()
        if request.request_id in self.accepted_source_requests:
            raise AssertionError("source request ID was not unique")
        self.accepted_source_requests[request.request_id] = request
        if auto_respond:
            self.source_responses.append(
                SourceResponse(
                    request.request_id,
                    request.generation,
                    request.source_line,
                    self._source_payload(request.source_line),
                )
            )
        self.source_request_acceptances += 1
        self._update_high_water()
        return True

    @bounded_transition
    def inject_source_response(self, response: SourceResponse) -> bool:
        # This is the only external SourceResponse admission boundary.  Check
        # the exact record type before touching attributes so subclasses and
        # arbitrary objects cannot run user-defined accessors.  Payload length
        # is checked in O(1), and payload contents are walked only after the
        # exact eight-word cache-line shape is established.
        self._charge("response_admission_field_checks", 1)
        well_formed = type(response) is SourceResponse
        if well_formed:
            scalar_fields = (
                (response.request_id, MAX_REQUEST_ID, False),
                (response.generation, MAX_GENERATION, False),
                (response.source_line, MAX_SOURCE_LINE, True),
            )
            for value, maximum, allow_zero in scalar_fields:
                self._charge("response_admission_field_checks", 1)
                if (
                    type(value) is not int
                    or value < 0
                    or (not allow_zero and value == 0)
                    or value > maximum
                ):
                    well_formed = False
                    break
        if well_formed:
            self._charge("response_admission_field_checks", 1)
            well_formed = type(response.payload) is tuple
        if well_formed:
            self._charge("response_admission_field_checks", 1)
            well_formed = (
                len(response.payload) == SOURCE_RESPONSE_PAYLOAD_WORDS
            )
        if well_formed:
            for word in response.payload:
                self._charge("response_admission_payload_word_checks", 1)
                if type(word) is not int or word < 0 or word > VALUE_MASK:
                    well_formed = False
                    break
        if not well_formed:
            self._charge("response_admission_diagnostic_updates", 1)
            if (
                self.malformed_source_responses
                < MAX_SOURCE_RESPONSE_DIAGNOSTIC
            ):
                self.malformed_source_responses += 1
            return False
        if len(self.source_responses) >= self.config.source_response_slots:
            return False
        self.source_responses.append(response)
        self._update_high_water()
        return True

    @bounded_transition
    def deliver_source_response(self) -> bool:
        if not self.source_responses:
            return False
        response = self.source_responses.popleft()
        self._charge("response_match_probes", 1)
        expected = self.accepted_source_requests.get(response.request_id)
        if response.generation != self.generation or expected is None:
            self.stale_source_responses += 1
            return True
        if (
            response.generation != expected.generation
            or response.source_line != expected.source_line
            or not 0 < response.request_id <= MAX_REQUEST_ID
            or not 0 < response.generation <= MAX_GENERATION
            or not 0 <= response.source_line <= MAX_SOURCE_LINE
        ):
            self.forged_source_responses += 1
            return True
        expected_payload = self._source_payload(expected.source_line)
        self._charge("response_payload_word_checks", len(expected_payload))
        if response.payload != expected_payload:
            self.forged_source_responses += 1
            return True

        # Preflight the complete accepted record before mutating any owner.
        for destination in expected.destinations:
            self._charge("response_token_prechecks", 1)
            if self.token_state[destination] != "reserved":
                raise AssertionError("response does not own a reserved token")
            line, word = divmod(destination, self.config.words_per_line)
            owner = self.owners.get(line)
            if owner is None or owner.generation != response.generation:
                raise AssertionError(
                    "response lost its destination-line owner"
                )
            bit = 1 << word
            if owner.reserved_mask & bit == 0:
                raise AssertionError("response token was not reserved")
            if (
                owner.reservation_request_ids.get(word) != response.request_id
                or owner.reservation_source_lines.get(word)
                != response.source_line
            ):
                raise AssertionError(
                    "response does not match the word reservation identity"
                )

        for destination in expected.destinations:
            line, word = divmod(destination, self.config.words_per_line)
            owner = self.owners[line]
            bit = 1 << word
            owner.reserved_mask &= ~bit
            owner.received_mask |= bit
            del owner.reservation_request_ids[word]
            del owner.reservation_source_lines[word]
            self.token_state[destination] = "tentative"
            value = response.payload[
                self.pattern[destination] % self.config.words_per_line
            ]
            if value != self.expected_destination_values[destination]:
                raise AssertionError(
                    "source payload failed deterministic oracle"
                )
            if self.destination_receive_counts[destination] != 0:
                raise AssertionError(
                    "destination received a source value twice"
                )
            owner.payload[word] = value
            self.destination_values[destination] = value
            self.destination_receive_counts[destination] += 1
        del self.accepted_source_requests[response.request_id]
        self.source_response_completions += 1
        return True

    @bounded_transition
    def enqueue_ready_write(self) -> bool:
        if len(self.write_requests) >= self.config.write_request_slots:
            return False
        ready = None
        for owner in self.owners.values():
            self._charge("ready_owner_scans", 1)
            if (
                owner.state == "collecting"
                and owner.reserved_mask == 0
                and owner.received_mask == owner.expected_mask
            ):
                ready = owner
                break
        if ready is None:
            return False
        if self.next_write_request_id > MAX_REQUEST_ID:
            raise RuntimeError("finite write request-ID space exhausted")
        token_words = self._charged_sorted(ready.tokens)
        self._charge("write_token_walks", len(token_words))
        destinations = tuple(ready.tokens[word] for word in token_words)
        request = WriteRequest(
            self.next_write_request_id,
            self.generation,
            ready.line,
            ready.received_mask,
            destinations,
        )
        self.next_write_request_id += 1
        ready.state = "write_queued"
        ready.write_request_id = request.request_id
        self.write_requests.append(request)
        self.write_request_issues += 1
        self._update_high_water()
        return True

    @bounded_transition
    def accept_write_request(self) -> bool:
        if not self.write_requests:
            return False
        if len(self.write_acks) >= self.config.write_ack_slots:
            return False
        request = self.write_requests.popleft()
        owner = self.owners.get(request.line)
        if (
            owner is None
            or owner.generation != request.generation
            or owner.write_request_id != request.request_id
            or owner.state != "write_queued"
        ):
            raise AssertionError("write acceptance lost its owner")
        owner.state = "waiting_for_true_completion"
        self.write_acks.append(request)
        self.write_request_acceptances += 1
        self._update_high_water()
        return True

    @bounded_transition
    def complete_external_write(
        self, generation: int, line: int, request_id: int
    ) -> bool:
        matching_index = None
        for index, request in enumerate(self.write_acks):
            self._charge("write_ack_match_entry_scans", 1)
            if (
                request.generation == generation
                and request.line == line
                and request.request_id == request_id
            ):
                matching_index = index
                break
        if generation != self.generation or matching_index is None:
            self.stale_write_responses += 1
            return False
        request = self.write_acks[matching_index]
        owner = self.owners.get(line)
        if (
            owner is None
            or owner.state != "waiting_for_true_completion"
            or owner.generation != generation
            or owner.write_request_id != request_id
        ):
            self.stale_write_responses += 1
            return False
        if request.mask != owner.expected_mask:
            raise AssertionError("corrected policy attempted a partial write")
        del self.write_acks[matching_index]
        for destination in request.destinations:
            self._charge("write_token_walks", 1)
            _, word = divmod(destination, self.config.words_per_line)
            if self.token_state[destination] != "tentative":
                raise AssertionError("write completion token is not tentative")
            if (
                self.destination_receive_counts[destination] != 1
                or self.destination_values[destination]
                != self.expected_destination_values[destination]
                or owner.payload.get(word)
                != self.expected_destination_values[destination]
            ):
                raise AssertionError("write completion failed payload oracle")
            self.token_state[destination] = "committed"
            self.remaining_live_words -= 1
            if self.remaining_live_words < 0:
                raise AssertionError("descriptor completed a live word twice")
            page = destination // self.config.page_elements
            self.page_remaining[page] -= 1
            if self.page_remaining[page] < 0:
                raise AssertionError("page completed a word twice")
        del self.owners[line]
        self.owner_set_counts[line % self.config.owner_sets] -= 1
        self.write_completions += 1
        self._advance_focus()
        return True

    @bounded_transition
    def complete_oldest_write(self) -> bool:
        if not self.write_acks:
            return False
        request = self.write_acks[0]
        return self.complete_external_write(
            request.generation, request.line, request.request_id
        )

    def step(
        self, auto_complete_writes: bool = True, validate: bool = True
    ) -> bool:
        """Perform at most one transition in each bounded pipeline stage."""
        self.transition_steps += 1
        progress = False
        if auto_complete_writes:
            progress = self.complete_oldest_write() or progress
        progress = self.accept_write_request() or progress
        progress = self.enqueue_ready_write() or progress
        progress = self.deliver_source_response() or progress
        progress = self.accept_source_request() or progress
        progress = self.issue_source_request() or progress
        progress = self._advance_focus() or progress
        self._update_high_water()
        if validate:
            self.assert_invariants()
        return progress

    def done(self) -> bool:
        return (
            self.remaining_live_words == 0
            and not self.owners
            and not self.source_requests
            and not self.accepted_source_requests
            and not self.source_responses
            and not self.write_requests
            and not self.write_acks
        )

    def assert_invariants(self, check_all_tokens: bool = True) -> None:
        if len(self.owners) > self.config.owner_lines:
            raise AssertionError("owner table exceeded finite capacity")
        for set_id in range(self.config.owner_sets):
            occupancy = sum(
                line % self.config.owner_sets == set_id for line in self.owners
            )
            if occupancy != self.owner_set_counts[set_id]:
                raise AssertionError("owner set credit counter diverged")
            if occupancy > self.config.owner_ways:
                raise AssertionError("owner set exceeded finite associativity")
        bounded = (
            (len(self.source_requests), self.config.source_request_slots),
            (
                len(self.accepted_source_requests),
                self.config.source_response_slots,
            ),
            (len(self.source_responses), self.config.source_response_slots),
            (len(self.write_requests), self.config.write_request_slots),
            (len(self.write_acks), self.config.write_ack_slots),
        )
        if any(used > capacity for used, capacity in bounded):
            raise AssertionError("a finite queue exceeded capacity")
        if len(self.source_requests) + len(self.accepted_source_requests) > (
            self.config.source_response_slots
        ):
            raise AssertionError(
                "source requests and accepted responses exceeded shared credits"
            )
        focus_entry_count = sum(
            len(sources) for sources in self.focus_rows.values()
        )
        if focus_entry_count > len(self.source_targets):
            raise AssertionError("focus heap exceeded the finite source bound")
        if len(self.focus_heap_members) != focus_entry_count:
            raise AssertionError("focus heap membership state diverged")
        if self.atomic_transition_work_high_water > (
            self.atomic_transition_work_limit
        ):
            raise AssertionError(
                "atomic transition exceeded finite work bound"
            )
        if not 1 <= self.next_source_request_id <= MAX_REQUEST_ID + 1:
            raise AssertionError("source request-ID state exceeded its width")
        if not 1 <= self.next_write_request_id <= MAX_REQUEST_ID + 1:
            raise AssertionError("write request-ID state exceeded its width")
        for line, owner in self.owners.items():
            if owner.line != line or owner.generation != self.generation:
                raise AssertionError("owner tag mismatch")
            if owner.received_mask & owner.reserved_mask:
                raise AssertionError("owner received/reserved masks overlap")
            if (
                owner.received_mask | owner.reserved_mask
            ) & ~owner.expected_mask:
                raise AssertionError("owner contains a predicated-false word")
            reservation_words = {
                word
                for word in range(self.config.words_per_line)
                if owner.reserved_mask & (1 << word)
            }
            if reservation_words != set(owner.reservation_request_ids):
                raise AssertionError("reservation request-ID ledger diverged")
            if reservation_words != set(owner.reservation_source_lines):
                raise AssertionError("reservation source-line ledger diverged")
            received_words = {
                word
                for word in range(self.config.words_per_line)
                if owner.received_mask & (1 << word)
            }
            if received_words != set(owner.payload):
                raise AssertionError("owner payload ledger diverged")
        for request_id, request in self.accepted_source_requests.items():
            if request_id != request.request_id:
                raise AssertionError("accepted source ledger key diverged")
            if request.generation != self.generation:
                raise AssertionError("accepted source generation diverged")
            for destination in request.destinations:
                line, word = divmod(destination, self.config.words_per_line)
                owner = self.owners.get(line)
                if (
                    owner is None
                    or owner.reservation_request_ids.get(word) != request_id
                    or owner.reservation_source_lines.get(word)
                    != request.source_line
                ):
                    raise AssertionError(
                        "accepted source lost its exact reservation"
                    )
        if check_all_tokens:
            observed_remaining = 0
            for destination, state in enumerate(self.token_state):
                if not self.live[destination] and state != "predicated_false":
                    raise AssertionError("false predicate acquired an owner")
                if self.live[destination] and state != "committed":
                    observed_remaining += 1
                if self.live[destination] and state in (
                    "tentative",
                    "committed",
                ):
                    if (
                        self.destination_receive_counts[destination] != 1
                        or self.destination_values[destination]
                        != self.expected_destination_values[destination]
                    ):
                        raise AssertionError(
                            "live destination failed value oracle"
                        )
                elif self.destination_receive_counts[destination] != 0:
                    raise AssertionError(
                        "non-received destination has a value"
                    )
            if observed_remaining != self.remaining_live_words:
                raise AssertionError("remaining-live-word counter diverged")

    def run(self, max_steps: int | None = None) -> dict[str, object]:
        live_words = sum(self.live)
        limit = max_steps or max(
            64, 12 * live_words + 16 * len(self.expected_masks)
        )
        stalled = 0
        while not self.done() and self.transition_steps < limit:
            if self.step(auto_complete_writes=True, validate=False):
                stalled = 0
            else:
                stalled += 1
                if stalled > 2:
                    raise RuntimeError("corrected scheduler made no progress")
            if self.transition_steps % 1024 == 0:
                self.assert_invariants(check_all_tokens=False)
        if not self.done():
            raise RuntimeError(
                f"corrected scheduler exceeded liveness bound {limit}"
            )
        self.assert_invariants(check_all_tokens=True)
        return self.metrics()

    def metrics(self) -> dict[str, object]:
        unique_source_lines = len(self.source_targets)
        if self.done() and self.source_request_issues < unique_source_lines:
            raise AssertionError(
                "completed replay did not issue every source line"
            )
        same_row_rate = round(
            (
                self.same_bank_row_successors / self.source_successor_pairs
                if self.source_successor_pairs
                else 0.0
            ),
            9,
        )
        physical_full_mask = (1 << self.config.words_per_line) - 1
        physical_full = sum(
            mask == physical_full_mask for mask in self.expected_masks.values()
        )
        return {
            "policy_kind": "executed bounded single-owner transition model",
            "logical_words": len(self.pattern),
            "live_words": sum(self.live),
            "predicated_false_words": len(self.live) - sum(self.live),
            "index_scan_words": len(self.pattern),
            "preissue_barrier_words": len(self.pattern),
            "transition_steps": self.transition_steps,
            "functional_work_total": self.functional_work_total,
            **{
                f"work_{name}": value
                for name, value in self.work_counts.items()
            },
            "atomic_transition_work_high_water": (
                self.atomic_transition_work_high_water
            ),
            "atomic_transition_work_limit": self.atomic_transition_work_limit,
            "unique_source_lines": unique_source_lines,
            "duplicate_source_reads": (
                self.source_request_issues - unique_source_lines
            ),
            "same_bank_row_successors": self.same_bank_row_successors,
            "source_successor_pairs": self.source_successor_pairs,
            "same_bank_row_successor_rate": same_row_rate,
            "source_request_issues": self.source_request_issues,
            "source_request_acceptances": self.source_request_acceptances,
            "source_response_completions": self.source_response_completions,
            "c_write_requests": self.write_request_issues,
            "c_write_acceptances": self.write_request_acceptances,
            "c_write_completions": self.write_completions,
            "exact_live_mask_writes": self.write_completions,
            "partial_live_mask_writes": 0,
            "full_physical_mask_writes": physical_full,
            "owner_promotions": self.owner_promotions,
            "owner_allocations": self.owner_allocations,
            "owner_allocation_refusals": self.owner_allocation_refusals,
            "focus_switches": self.focus_switches,
            "stale_source_responses": self.stale_source_responses,
            "forged_source_responses": self.forged_source_responses,
            "malformed_source_responses": self.malformed_source_responses,
            "rejected_source_responses": (
                self.stale_source_responses
                + self.forged_source_responses
                + self.malformed_source_responses
            ),
            "stale_write_responses": self.stale_write_responses,
            "row_rotations": self.row_rotations,
            "row_same_reselections": self.row_same_reselections,
            "payload_oracle_live_words_verified": sum(
                count == 1 and value == expected
                for count, value, expected, is_live in zip(
                    self.destination_receive_counts,
                    self.destination_values,
                    self.expected_destination_values,
                    self.live,
                )
                if is_live
            ),
            "payload_oracle_exact_once_failures": sum(
                count != 1 or value != expected
                for count, value, expected, is_live in zip(
                    self.destination_receive_counts,
                    self.destination_values,
                    self.expected_destination_values,
                    self.live,
                )
                if is_live
            ),
            "owner_high_water": self.high_water["owner"],
            "source_request_queue_high_water": self.high_water[
                "source_request"
            ],
            "accepted_source_response_high_water": self.high_water[
                "accepted_source"
            ],
            "source_response_queue_high_water": self.high_water[
                "source_response"
            ],
            "write_request_queue_high_water": self.high_water["write_request"],
            "write_ack_queue_high_water": self.high_water["write_ack"],
        }


def corrected_state_lower_bound(
    config: ReplayConfig | None = None,
) -> dict[str, object]:
    """Return a complete finite replay-state ledger, not synthesis evidence."""
    config = config or ReplayConfig()
    config.validate()
    line_count = config.destination_lines
    line_bits = max(1, math.ceil(math.log2(line_count)))
    token_bits = max(1, math.ceil(math.log2(config.logical_elements)))
    source_line_bits = ARCHIVED_SOURCE_LINE_BITS
    source_word_bits = max(1, math.ceil(math.log2(config.words_per_line)))
    mask_bits = config.words_per_line
    allocation_sequence_bits = max(1, math.ceil(math.log2(line_count + 1)))
    owner_state_bits = 2
    response_count_bits = max(
        1, math.ceil(math.log2(config.max_response_tokens + 1))
    )
    write_count_bits = max(1, math.ceil(math.log2(config.words_per_line + 1)))
    row_key_bits = 1 + 2 + 2 + max(1, source_line_bits - 12)
    page_count = config.pages_per_tile
    page_bits = max(1, math.ceil(math.log2(page_count + 1)))
    page_remaining_bits = max(
        1, math.ceil(math.log2(config.page_elements + 1))
    )
    owner_set_count_bits = max(1, math.ceil(math.log2(config.owner_ways + 1)))
    row_burst_bits = max(1, math.ceil(math.log2(config.row_burst + 1)))
    observer_counter_bits = REQUEST_ID_BITS

    def fifo_protocol_bits(slots: int) -> int:
        pointer_bits = max(1, math.ceil(math.log2(slots)))
        count_bits = max(1, math.ceil(math.log2(slots + 1)))
        return 2 * pointer_bits + count_bits

    configuration_image_bits = (
        sum(getattr(config, name).bit_length() for name in _CONFIG_FIELDS)
        + source_line_bits
    )
    source_mapping_bits = config.logical_elements * (
        source_line_bits + source_word_bits
    )
    live_predicate_bits = config.logical_elements
    exact_live_mask_table_bits = line_count * (1 + mask_bits)
    destination_line_directory_bits = line_count * (1 + mask_bits)
    source_target_directory_bits = config.logical_elements * source_line_bits
    source_pending_directory_bits = config.logical_elements * (
        1 + source_line_bits + line_bits
    )
    token_state_bits = config.logical_elements * 2
    progress_state_bits = (
        max(1, math.ceil(math.log2(config.logical_elements + 1)))
        + page_count * page_remaining_bits
    )
    focus_pointer_bits = max(
        1, math.ceil(math.log2(config.logical_elements + 1))
    )
    focus_row_structure_bits = config.logical_elements * (
        1 + source_line_bits + row_key_bits + 2 * focus_pointer_bits
    )
    focus_membership_bits = config.logical_elements * (1 + source_line_bits)
    selector_state_bits = (
        page_bits
        + config.owner_sets * owner_set_count_bits
        + 1
        + row_key_bits
        + row_burst_bits
    )
    owner_payload_bits = (
        config.owner_lines * config.words_per_line * VALUE_BITS
    )
    owner_metadata_bits = config.owner_lines * (
        line_bits
        + GENERATION_BITS
        + 3 * mask_bits
        + allocation_sequence_bits
        + owner_state_bits
        + 1
        + REQUEST_ID_BITS
        + config.words_per_line
        * (token_bits + REQUEST_ID_BITS + source_line_bits)
    )
    source_queue_descriptor_bits = (
        1
        + REQUEST_ID_BITS
        + GENERATION_BITS
        + source_line_bits
        + response_count_bits
        + token_bits * config.max_response_tokens
    )
    source_request_queue_bits = (
        config.source_request_slots * source_queue_descriptor_bits
        + fifo_protocol_bits(config.source_request_slots)
    )
    accepted_source_ledger_bits = (
        config.source_response_slots * source_queue_descriptor_bits
        + max(1, math.ceil(math.log2(config.source_response_slots + 1)))
    )
    source_response_event_queue_bits = config.source_response_slots * (
        1
        + REQUEST_ID_BITS
        + GENERATION_BITS
        + source_line_bits
        + SOURCE_RESPONSE_PAYLOAD_WORDS * VALUE_BITS
    ) + fifo_protocol_bits(config.source_response_slots)
    write_descriptor_bits = (
        1
        + REQUEST_ID_BITS
        + GENERATION_BITS
        + line_bits
        + mask_bits
        + write_count_bits
        + token_bits * config.words_per_line
    )
    write_request_queue_bits = (
        config.write_request_slots * write_descriptor_bits
        + fifo_protocol_bits(config.write_request_slots)
    )
    write_ack_queue_bits = (
        config.write_ack_slots * write_descriptor_bits
        + fifo_protocol_bits(config.write_ack_slots)
    )
    identity_state_bits = (
        GENERATION_BITS + 2 * REQUEST_ID_BITS + allocation_sequence_bits
    )

    # Replay/evidence fields never steer issue, ownership, or completion.
    # They are retained so the frozen replay can prove bounded work, ordering,
    # queue occupancy, and exact-once values without smuggling them into the
    # hardware-policy subtotal.
    payload_oracle_observer_bits = config.logical_elements * (
        VALUE_BITS + 1 + VALUE_BITS + 1
    )
    functional_work_accounting_bits = (
        len(WORK_COUNTER_NAMES) + 4
    ) * observer_counter_bits + 6
    execution_event_counter_bits = (
        len(_EVENT_COUNTER_FIELDS) * observer_counter_bits
    )
    ordering_observer_state_bits = 1 + row_key_bits
    high_water_observer_bits = (
        max(1, math.ceil(math.log2(config.owner_lines + 1)))
        + max(1, math.ceil(math.log2(config.source_request_slots + 1)))
        + 2 * max(1, math.ceil(math.log2(config.source_response_slots + 1)))
        + max(1, math.ceil(math.log2(config.write_request_slots + 1)))
        + max(1, math.ceil(math.log2(config.write_ack_slots + 1)))
    )

    component_bits = {
        "configuration_image_bits": configuration_image_bits,
        "source_mapping_table_bits": source_mapping_bits,
        "live_predicate_bits": live_predicate_bits,
        "exact_live_mask_table_bits": exact_live_mask_table_bits,
        "destination_line_directory_bits": destination_line_directory_bits,
        "source_target_directory_bits": source_target_directory_bits,
        "source_pending_directory_bits": source_pending_directory_bits,
        "token_state_bits": token_state_bits,
        "progress_state_bits": progress_state_bits,
        "focus_row_structure_bits": focus_row_structure_bits,
        "focus_membership_bits": focus_membership_bits,
        "selector_state_bits": selector_state_bits,
        "owner_payload_bits": owner_payload_bits,
        "owner_metadata_bits": owner_metadata_bits,
        "source_request_queue_bits": source_request_queue_bits,
        "accepted_source_ledger_bits": accepted_source_ledger_bits,
        "source_response_event_queue_bits": source_response_event_queue_bits,
        "write_request_queue_bits": write_request_queue_bits,
        "write_ack_queue_bits": write_ack_queue_bits,
        "identity_state_bits": identity_state_bits,
        "payload_oracle_observer_bits": payload_oracle_observer_bits,
        "functional_work_accounting_bits": functional_work_accounting_bits,
        "execution_event_counter_bits": execution_event_counter_bits,
        "ordering_observer_state_bits": ordering_observer_state_bits,
        "high_water_observer_bits": high_water_observer_bits,
    }
    inventory = persistent_field_inventory()
    fields = [entry["field"] for entry in inventory]
    if len(fields) != len(set(fields)):
        raise AssertionError("persistent-field inventory has a duplicate")
    component_classifications: dict[str, str] = {}
    for entry in inventory:
        classification = entry["classification"]
        component = entry["component"]
        if classification not in {
            HARDWARE_POLICY_STATE,
            REPLAY_EVIDENCE_OBSERVER_STATE,
        }:
            raise AssertionError(
                "persistent field has an unknown classification"
            )
        previous = component_classifications.setdefault(
            component, classification
        )
        if previous != classification:
            raise AssertionError(
                "persistent component has mixed classifications"
            )
    if set(component_bits) != set(component_classifications):
        raise AssertionError("ledger components and field inventory diverged")

    policy_components = sorted(
        component
        for component, classification in component_classifications.items()
        if classification == HARDWARE_POLICY_STATE
    )
    observer_components = sorted(
        component
        for component, classification in component_classifications.items()
        if classification == REPLAY_EVIDENCE_OBSERVER_STATE
    )
    policy_bits = sum(
        component_bits[component] for component in policy_components
    )
    observer_bits = sum(
        component_bits[component] for component in observer_components
    )
    total_bits = policy_bits + observer_bits
    return {
        "widths": {
            "destination_line_bits": line_bits,
            "destination_token_bits": token_bits,
            "source_line_bits": source_line_bits,
            "source_word_offset_bits": source_word_bits,
            "generation_bits": GENERATION_BITS,
            "request_id_bits": REQUEST_ID_BITS,
            "allocation_sequence_bits": allocation_sequence_bits,
            "owner_state_bits": owner_state_bits,
            "row_key_bits": row_key_bits,
            "value_bits": VALUE_BITS,
            "source_response_payload_words": SOURCE_RESPONSE_PAYLOAD_WORDS,
            "observer_counter_bits": observer_counter_bits,
        },
        "capacities": {
            "destination_lines": line_count,
            "maximum_source_line": MAX_SOURCE_LINE,
            "maximum_response_tokens": config.max_response_tokens,
            "focus_heap_entries": config.logical_elements,
        },
        "components_bits": component_bits,
        "component_classifications": component_classifications,
        "persistent_field_inventory": inventory,
        "persistent_field_inventory_count": len(inventory),
        "hardware_policy_state_field_count": sum(
            entry["classification"] == HARDWARE_POLICY_STATE
            for entry in inventory
        ),
        "replay_evidence_observer_state_field_count": sum(
            entry["classification"] == REPLAY_EVIDENCE_OBSERVER_STATE
            for entry in inventory
        ),
        "hardware_policy_components": policy_components,
        "replay_evidence_observer_components": observer_components,
        "hardware_policy_state_bits": policy_bits,
        "hardware_policy_state_bytes": ceil_div(policy_bits, 8),
        "replay_evidence_observer_state_bits": observer_bits,
        "replay_evidence_observer_state_bytes": ceil_div(observer_bits, 8),
        "finite_replay_model_bits": total_bits,
        "finite_replay_model_bytes": ceil_div(total_bits, 8),
        # Backward-compatible aliases retained for consumers of the frozen
        # artifact.  They have the exact same values as the explicit names.
        "bit_packed_policy_state_bits": policy_bits,
        "bit_packed_policy_state_bytes": ceil_div(policy_bits, 8),
        "bit_packed_replay_observer_state_bits": observer_bits,
        "bit_packed_replay_observer_state_bytes": ceil_div(observer_bits, 8),
        "bit_packed_finite_ledger_bits": total_bits,
        "bit_packed_finite_ledger_bytes": ceil_div(total_bits, 8),
    }


def analyze_tile(
    pattern: Sequence[int],
    live: Sequence[bool],
    config: ReplayConfig,
    source_line_phase: int,
    generation: int,
) -> dict[str, dict[str, object]]:
    full = schedule_full_row(pattern, live, config, source_line_phase)
    direct = schedule_direct4(pattern, live, config, source_line_phase)
    corrected = CorrectedHybridScheduler(
        pattern, live, config, source_line_phase, generation
    ).run()
    return {
        "full_row": reference_metrics(
            "full_row", full, live, config, source_line_phase
        ),
        "direct4": reference_metrics(
            "direct4", direct, live, config, source_line_phase
        ),
        "corrected": corrected,
    }


def _aggregate_policy(
    records: Sequence[dict[str, object]],
) -> dict[str, object]:
    if not records:
        raise ValueError("cannot aggregate no policy records")
    result: dict[str, object] = {"policy_kind": records[0]["policy_kind"]}
    high_water_keys = {
        "owner_high_water",
        "source_request_queue_high_water",
        "accepted_source_response_high_water",
        "source_response_queue_high_water",
        "write_request_queue_high_water",
        "write_ack_queue_high_water",
        "atomic_transition_work_high_water",
        "atomic_transition_work_limit",
    }
    ignored = {"policy_kind", "same_bank_row_successor_rate"}
    for key in records[0]:
        if key in ignored:
            continue
        values = [record[key] for record in records]
        if not all(isinstance(value, int) for value in values):
            raise AssertionError(f"unexpected aggregate field {key}")
        result[key] = max(values) if key in high_water_keys else sum(values)
    pairs = int(result["source_successor_pairs"])
    result["same_bank_row_successor_rate"] = round(
        int(result["same_bank_row_successors"]) / pairs if pairs else 0.0, 9
    )
    result["logical_tiles"] = len(records)
    return result


def _aggregate_policy_summaries(
    records: Sequence[dict[str, object]],
) -> dict[str, object]:
    """Merge already-aggregated case summaries without relabeling tiles."""
    if not records:
        raise ValueError("cannot aggregate no policy summaries")
    result: dict[str, object] = {"policy_kind": records[0]["policy_kind"]}
    high_water_keys = {
        "owner_high_water",
        "source_request_queue_high_water",
        "accepted_source_response_high_water",
        "source_response_queue_high_water",
        "write_request_queue_high_water",
        "write_ack_queue_high_water",
        "atomic_transition_work_high_water",
        "atomic_transition_work_limit",
    }
    ignored = {"policy_kind", "same_bank_row_successor_rate"}
    for key in records[0]:
        if key in ignored:
            continue
        values = [record[key] for record in records]
        if not all(isinstance(value, int) for value in values):
            raise AssertionError(f"unexpected summary field {key}")
        result[key] = max(values) if key in high_water_keys else sum(values)
    pairs = int(result["source_successor_pairs"])
    result["same_bank_row_successor_rate"] = round(
        int(result["same_bank_row_successors"]) / pairs if pairs else 0.0, 9
    )
    return result


def analyze_pattern(
    pattern: Sequence[int],
    config: ReplayConfig | None = None,
    source_line_phase: int = 0,
    live: Sequence[bool] | None = None,
) -> dict[str, object]:
    validate_pattern(pattern)
    config = config or ReplayConfig()
    config.validate()
    max_source_line = max(pattern) // config.words_per_line
    if max_source_line + source_line_phase > MAX_SOURCE_LINE:
        raise ValueError(
            "input source line plus phase exceeds the archived 18-bit field"
        )
    live_values = list(live) if live is not None else [True] * len(pattern)
    validate_live_mask(pattern, live_values)
    per_policy: dict[str, list[dict[str, object]]] = {
        "full_row": [],
        "direct4": [],
        "corrected": [],
    }
    for tile_index, begin in enumerate(
        range(0, len(pattern), config.logical_elements), start=1
    ):
        end = min(begin + config.logical_elements, len(pattern))
        tile = analyze_tile(
            pattern[begin:end],
            live_values[begin:end],
            config,
            source_line_phase,
            tile_index,
        )
        for policy, metrics in tile.items():
            per_policy[policy].append(metrics)
    return {
        "pattern_words": len(pattern),
        "max_source_line": max_source_line,
        "all_words_live": all(live_values),
        "policies": {
            policy: _aggregate_policy(records)
            for policy, records in per_policy.items()
        },
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
    args = parser.parse_args()
    if args.xrage is None and args.flag_root is None:
        parser.error("at least one of --xrage or --flag-root is required")

    config = ReplayConfig()
    output: dict[str, object] = {
        "schema": SCHEMA,
        "model_scope": {
            "claim": "deterministic_policy_transition_and_work_replay_only",
            "source_response_order": "model-selected issue order",
            "write_acceptance": "explicit bounded transition",
            "write_completion": "distinct explicit bounded ACK transition",
            "archived_response_ticks_available": False,
            "timing_prediction": False,
            "synthesis_or_area_claim": False,
            "measurement_domains": {
                "functional_work": (
                    "exact integer counts prefixed work_ plus requests, "
                    "writes, scans, transitions, and ordering successors"
                ),
                "timing": (
                    "unavailable: no cycles, ticks, latency, throughput, "
                    "or speedup is derived"
                ),
            },
            "payload_oracle": (
                "64-bit bijective deterministic source-word values; every "
                "live destination must receive its mapped value exactly once"
            ),
        },
        "config": asdict(config),
        "state_contract": corrected_state_lower_bound(config),
    }
    if args.xrage is not None:
        output["xrage"] = _input_record(
            args.xrage,
            args.xrage_source_line_phase,
            analyze_pattern(
                load_gather_pattern(args.xrage),
                config,
                args.xrage_source_line_phase,
            ),
        )
    if args.flag_root is not None:
        paths = sorted(args.flag_root.glob("**/config_*_gather.json"))
        if not paths:
            raise SystemExit(f"no FLAG gather inputs below {args.flag_root}")
        cases = [
            _input_record(
                path,
                args.flag_source_line_phase,
                analyze_pattern(
                    load_gather_pattern(path),
                    config,
                    args.flag_source_line_phase,
                ),
            )
            for path in paths
        ]
        output["flag"] = {
            "case_count": len(cases),
            "pattern_words": sum(int(case["pattern_words"]) for case in cases),
            "max_source_line": max(
                int(case["max_source_line"]) for case in cases
            ),
            "policies": {
                policy: _aggregate_policy_summaries(
                    [case["policies"][policy] for case in cases]
                )
                for policy in ("full_row", "direct4", "corrected")
            },
            "source_line_phase_limitation": (
                "FLAG archive omits the A base; phase zero is a declared "
                "bank-row ordering proxy"
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
