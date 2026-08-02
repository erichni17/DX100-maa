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

SCHEMA = "dx100-corrected-hybrid-single-owner-replay-v1"


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
    destinations: tuple[int, ...]


@dataclass(frozen=True)
class WriteRequest:
    request_id: int
    generation: int
    line: int
    mask: int
    destinations: tuple[int, ...]


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
    """Executable bounded transition model for CHSO-64.

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
        if generation <= 0:
            raise ValueError("generation must be positive")
        self.generation = generation
        self.source_line_phase = source_line_phase
        self.expected_masks = exact_live_masks(
            self.live, self.config.words_per_line
        )
        self.line_destinations: dict[int, list[int]] = {}
        self.source_targets: dict[int, dict[int, list[int]]] = {}
        for destination, source_index in enumerate(self.pattern):
            if not self.live[destination]:
                continue
            destination_line = destination // self.config.words_per_line
            source_line = source_index // self.config.words_per_line
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
        self.focus_heap: list[tuple[tuple[int, ...], int]] = []
        self.focus_heap_members: set[int] = set()
        self.active_row: tuple[int, ...] | None = None
        self.active_row_remaining = 0
        self.owners: dict[int, LineOwner] = {}
        self.owner_set_counts = [0] * self.config.owner_sets
        self.source_requests: deque[SourceRequest] = deque()
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
        self.stale_write_responses = 0
        self.source_order: list[int] = []
        self.high_water = {
            "owner": 0,
            "source_request": 0,
            "source_response": 0,
            "write_request": 0,
            "write_ack": 0,
        }
        self._advance_focus()
        self._rebuild_focus_heap()

    def _update_high_water(self) -> None:
        current = {
            "owner": len(self.owners),
            "source_request": len(self.source_requests),
            "source_response": len(self.source_responses),
            "write_request": len(self.write_requests),
            "write_ack": len(self.write_acks),
        }
        for name, value in current.items():
            self.high_water[name] = max(self.high_water[name], value)

    def _line_page(self, line: int) -> int:
        return (line * self.config.words_per_line) // self.config.page_elements

    def _source_has_focus_work(self, source_line: int) -> bool:
        return any(
            self._line_page(line) == self.focus_page
            for line in self.source_pending_lines.get(source_line, set())
        )

    def _push_focus_source(self, source_line: int) -> None:
        if (
            source_line in self.focus_heap_members
            or not self._source_has_focus_work(source_line)
        ):
            return
        key = bank_row_key(source_line, self.source_line_phase)
        heapq.heappush(self.focus_heap, (key, source_line))
        self.focus_heap_members.add(source_line)

    def _rebuild_focus_heap(self) -> None:
        self.focus_heap.clear()
        self.focus_heap_members.clear()
        self.active_row = None
        self.active_row_remaining = 0
        if self.focus_page >= len(self.page_remaining):
            return
        for source_line in self.source_pending_lines:
            self._push_focus_source(source_line)

    def _advance_focus(self) -> bool:
        old = self.focus_page
        while (
            self.focus_page < len(self.page_remaining)
            and self.page_remaining[self.focus_page] == 0
        ):
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

    def _reserve_line_tokens(self, source_line: int, line: int) -> list[int]:
        owner = self.owners[line]
        selected = [
            destination
            for destination in self.source_targets[source_line][line]
            if self.token_state[destination] == "unconsumed"
        ]
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
        self.source_pending_lines[source_line].discard(line)
        return selected

    def _plan_and_reserve(self, source_line: int) -> tuple[int, ...]:
        pending = self.source_pending_lines.get(source_line)
        if not pending:
            return ()
        selected: list[int] = []
        owned = sorted(
            (line for line in pending if line in self.owners),
            key=lambda line: self.owners[line].allocation_sequence,
        )
        for line in owned:
            selected.extend(self._reserve_line_tokens(source_line, line))

        new_lines = sorted(
            (line for line in tuple(pending) if line not in self.owners),
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
            selected.extend(self._reserve_line_tokens(source_line, line))
        return tuple(sorted(selected))

    def _promotion_source(self) -> int | None:
        for owner in self.owners.values():
            if owner.state != "collecting":
                continue
            for destination in self.line_destinations[owner.line]:
                if self.token_state[destination] == "unconsumed":
                    return (
                        self.pattern[destination] // self.config.words_per_line
                    )
        return None

    def _pop_focus_candidates(self) -> list[int]:
        if self.active_row is not None and self.active_row_remaining > 0:
            deferred: list[tuple[tuple[int, ...], int]] = []
            while self.focus_heap:
                row, source_line = heapq.heappop(self.focus_heap)
                if not self._source_has_focus_work(source_line):
                    self.focus_heap_members.discard(source_line)
                    continue
                if row == self.active_row:
                    self.focus_heap_members.discard(source_line)
                    for item in deferred:
                        heapq.heappush(self.focus_heap, item)
                    return [source_line]
                deferred.append((row, source_line))
            for item in deferred:
                heapq.heappush(self.focus_heap, item)
            self.active_row = None
            self.active_row_remaining = 0

        while self.focus_heap:
            row, source_line = heapq.heappop(self.focus_heap)
            self.focus_heap_members.discard(source_line)
            if not self._source_has_focus_work(source_line):
                continue
            self.active_row = row
            self.active_row_remaining = self.config.row_burst
            return [source_line]
        return []

    def issue_source_request(self) -> bool:
        if len(self.source_requests) >= self.config.source_request_slots:
            return False
        reserved_response_credits = len(self.source_requests) + len(
            self.source_responses
        )
        if reserved_response_credits >= self.config.source_response_slots:
            return False

        promotion: int | None = None
        candidates = self._pop_focus_candidates()
        blocked: list[int] = []
        selected_source: int | None = None
        destinations: tuple[int, ...] = ()
        for source_line in candidates:
            if source_line is None:
                continue
            destinations = self._plan_and_reserve(source_line)
            if destinations:
                selected_source = source_line
                break
            blocked.append(source_line)
        for source_line in blocked:
            self._push_focus_source(source_line)
        if selected_source is None:
            promotion = self._promotion_source()
            if promotion is not None:
                destinations = self._plan_and_reserve(promotion)
                if destinations:
                    selected_source = promotion
        if selected_source is None:
            return False
        if promotion is not None and selected_source == promotion:
            self.owner_promotions += 1
        request = SourceRequest(
            self.next_source_request_id,
            self.generation,
            selected_source,
            destinations,
        )
        self.next_source_request_id += 1
        self.source_requests.append(request)
        self.source_request_issues += 1
        self.source_order.append(selected_source)
        if (
            self.active_row
            == bank_row_key(selected_source, self.source_line_phase)
            and self.active_row_remaining
        ):
            self.active_row_remaining -= 1
        self._push_focus_source(selected_source)
        self._update_high_water()
        return True

    def accept_source_request(self) -> bool:
        if not self.source_requests:
            return False
        if len(self.source_responses) >= self.config.source_response_slots:
            return False
        request = self.source_requests.popleft()
        self.source_responses.append(
            SourceResponse(
                request.request_id,
                request.generation,
                request.source_line,
                request.destinations,
            )
        )
        self.source_request_acceptances += 1
        self._update_high_water()
        return True

    def inject_source_response(self, response: SourceResponse) -> bool:
        if len(self.source_responses) >= self.config.source_response_slots:
            return False
        self.source_responses.append(response)
        self._update_high_water()
        return True

    def deliver_source_response(self) -> bool:
        if not self.source_responses:
            return False
        response = self.source_responses.popleft()
        if response.generation != self.generation:
            self.stale_source_responses += 1
            return True
        for destination in response.destinations:
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
            owner.reserved_mask &= ~bit
            owner.received_mask |= bit
            self.token_state[destination] = "tentative"
        self.source_response_completions += 1
        return True

    def enqueue_ready_write(self) -> bool:
        if len(self.write_requests) >= self.config.write_request_slots:
            return False
        ready = next(
            (
                owner
                for owner in self.owners.values()
                if owner.state == "collecting"
                and owner.reserved_mask == 0
                and owner.received_mask == owner.expected_mask
            ),
            None,
        )
        if ready is None:
            return False
        destinations = tuple(
            ready.tokens[word] for word in sorted(ready.tokens)
        )
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

    def complete_external_write(
        self, generation: int, line: int, request_id: int
    ) -> bool:
        matching_index = next(
            (
                index
                for index, request in enumerate(self.write_acks)
                if request.generation == generation
                and request.line == line
                and request.request_id == request_id
            ),
            None,
        )
        if generation != self.generation or matching_index is None:
            self.stale_write_responses += 1
            return False
        request = self.write_acks[matching_index]
        del self.write_acks[matching_index]
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
        for destination in request.destinations:
            if self.token_state[destination] != "tentative":
                raise AssertionError("write completion token is not tentative")
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
            (len(self.source_responses), self.config.source_response_slots),
            (len(self.write_requests), self.config.write_request_slots),
            (len(self.write_acks), self.config.write_ack_slots),
        )
        if any(used > capacity for used, capacity in bounded):
            raise AssertionError("a finite queue exceeded capacity")
        if len(self.source_requests) + len(self.source_responses) > (
            self.config.source_response_slots
        ):
            raise AssertionError("source responses were not credit reserved")
        for line, owner in self.owners.items():
            if owner.line != line or owner.generation != self.generation:
                raise AssertionError("owner tag mismatch")
            if owner.received_mask & owner.reserved_mask:
                raise AssertionError("owner received/reserved masks overlap")
            if (
                owner.received_mask | owner.reserved_mask
            ) & ~owner.expected_mask:
                raise AssertionError("owner contains a predicated-false word")
        if check_all_tokens:
            observed_remaining = 0
            for destination, state in enumerate(self.token_state):
                if not self.live[destination] and state != "predicated_false":
                    raise AssertionError("false predicate acquired an owner")
                if self.live[destination] and state != "committed":
                    observed_remaining += 1
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
        order = _source_order_metrics(
            self.source_order, self.source_line_phase
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
            **order,
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
            "stale_write_responses": self.stale_write_responses,
            "owner_high_water": self.high_water["owner"],
            "source_request_queue_high_water": self.high_water[
                "source_request"
            ],
            "source_response_queue_high_water": self.high_water[
                "source_response"
            ],
            "write_request_queue_high_water": self.high_water["write_request"],
            "write_ack_queue_high_water": self.high_water["write_ack"],
        }


def corrected_state_lower_bound(
    config: ReplayConfig | None = None,
    generation_bits: int = 64,
) -> dict[str, int]:
    """Bit-packed policy-state contract; not area or synthesis evidence."""
    config = config or ReplayConfig()
    config.validate()
    if generation_bits <= 0:
        raise ValueError("generation width must be positive")
    line_count = ceil_div(config.logical_elements, config.words_per_line)
    line_bits = max(1, math.ceil(math.log2(line_count)))
    token_bits = max(1, math.ceil(math.log2(config.logical_elements)))
    source_line_bits = token_bits
    mask_bits = config.words_per_line
    owner_metadata_bits = config.owner_lines * (
        line_bits
        + generation_bits
        + 3 * mask_bits
        + 3
        + token_bits * config.words_per_line
    )
    live_mask_table_bits = line_count * (mask_bits + generation_bits + 1)
    reverse_source_token_bits = config.logical_elements * token_bits
    token_state_bits = config.logical_elements * 2
    max_response_tokens = config.owner_lines * config.words_per_line
    source_queue_descriptor_bits = (
        source_line_bits + generation_bits + token_bits * max_response_tokens
    )
    source_request_queue_bits = (
        config.source_request_slots * source_queue_descriptor_bits
    )
    source_response_queue_bits = config.source_response_slots * (
        source_queue_descriptor_bits + config.cache_line_bytes * 8
    )
    write_descriptor_bits = (
        line_bits + generation_bits + mask_bits + token_bits
    )
    write_request_queue_bits = (
        config.write_request_slots * write_descriptor_bits
    )
    write_ack_queue_bits = config.write_ack_slots * write_descriptor_bits
    parts_bits = {
        "owner_payload_bits": config.owner_lines * config.cache_line_bytes * 8,
        "owner_metadata_bits": owner_metadata_bits,
        "exact_live_mask_table_bits": live_mask_table_bits,
        "reverse_source_token_bits": reverse_source_token_bits,
        "token_state_bits": token_state_bits,
        "source_request_queue_bits": source_request_queue_bits,
        "source_response_queue_bits": source_response_queue_bits,
        "write_request_queue_bits": write_request_queue_bits,
        "write_ack_queue_bits": write_ack_queue_bits,
    }
    result = {
        name.replace("_bits", "_bytes"): ceil_div(bits, 8)
        for name, bits in parts_bits.items()
    }
    result["bit_packed_policy_state_bytes"] = sum(result.values())
    return result


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
    records: Sequence[dict[str, object]]
) -> dict[str, object]:
    if not records:
        raise ValueError("cannot aggregate no policy records")
    result: dict[str, object] = {"policy_kind": records[0]["policy_kind"]}
    high_water_keys = {
        "owner_high_water",
        "source_request_queue_high_water",
        "source_response_queue_high_water",
        "write_request_queue_high_water",
        "write_ack_queue_high_water",
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
        "source_response_queue_high_water",
        "write_request_queue_high_water",
        "write_ack_queue_high_water",
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
