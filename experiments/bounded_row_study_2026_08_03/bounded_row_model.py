#!/usr/bin/env python3
"""Finite model for a prospective 16K-logical/4K-active row mechanism.

This file is model evidence only.  It accepts physical records exported by a
gem5 trace; it never invents virtual-to-physical placement.  The policy state
is represented by fixed-size arrays: 4,096 Offset slots, 16 slices containing
32 row slots each (512 total), and eight line slots per row (4,096 total).

Validation lists and issue-event lists are external oracles.  They are not
read by admission or issue selection and are not proposed hardware state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import (
    asdict,
    dataclass,
)
from typing import Sequence

LOGICAL_ELEMENTS = 16_384
ACTIVE_ELEMENTS = 4_096
PARTITIONS = 4
INDEX_BYTES = 4
CACHE_LINE_BYTES = 64
WORD_BYTES = 8
FILTER_WORDS_PER_CYCLE = 16

NUM_CHANNELS = 1
NUM_RANKS = 1
NUM_BANK_GROUPS = 4
NUM_BANKS = 4
NUM_SLICES = 16
ROWS_PER_SLICE = 32
ROW_SLOTS = NUM_SLICES * ROWS_PER_SLICE
LINES_PER_ROW = 8
LINE_SLOTS = ROW_SLOTS * LINES_PER_ROW

# Frozen matched controls used 96 finite response descriptors and a 480-word
# response pool.  A future line chain is drained before it exceeds the latter.
RESPONSE_SLOTS = 96
RESPONSE_WORD_POOL = 480

DRAIN_OFFSET = 0
DRAIN_ROW = 1
DRAIN_LINE_WORDS = 2
DRAIN_NAMES = ("offset_limit", "row_slot_limit", "line_word_limit")


def ceil_div(value: int, divisor: int) -> int:
    if value < 0 or divisor <= 0:
        raise ValueError("value must be nonnegative and divisor positive")
    return (value + divisor - 1) // divisor


def bits_for_values(values: int) -> int:
    if values <= 1:
        return 1
    return math.ceil(math.log2(values))


def byte_array_bytes(count: int, field_bits: int) -> int:
    """Charge each field element at an independently addressable byte width."""
    if count < 0 or field_bits <= 0:
        raise ValueError("invalid array geometry")
    return count * ceil_div(field_bits, 8)


@dataclass(frozen=True)
class PhysicalRecord:
    """One B word and its gem5-translated/decoded A target."""

    itr: int
    index: int
    b_paddr: int
    a_line_paddr: int
    channel: int
    rank: int
    bankgroup: int
    bank: int
    row: int
    column: int
    wid: int

    @property
    def slice_id(self) -> int:
        # Exact getRowTableIdx() result for the frozen 1ch/1rank/4BG/4bank,
        # 16-slice organization: (((ch * 1 + rank) * 4 + BG) * 4 + bank).
        return (
            (self.channel * NUM_RANKS + self.rank) * NUM_BANK_GROUPS
            + self.bankgroup
        ) * NUM_BANKS + self.bank

    @property
    def grow(self) -> int:
        # Exact getGrowAddr() result at 16 slices: BG and bank are wholly in
        # the slice, leaving the DDR row as grow_addr.
        return self.row


@dataclass(frozen=True)
class ApertureGeometry:
    """Registered physical A grow bounds, independently frozen per slice."""

    grow_lower: tuple[int, ...]
    grow_upper_exclusive: tuple[int, ...]

    def validate(self) -> None:
        if (
            len(self.grow_lower) != NUM_SLICES
            or len(self.grow_upper_exclusive) != NUM_SLICES
        ):
            raise ValueError("aperture must provide exactly 16 slice bounds")
        for lower, upper in zip(self.grow_lower, self.grow_upper_exclusive):
            if not (0 <= lower < upper <= 65_536):
                raise ValueError("invalid DDR4 grow aperture bound")

    def partition(self, record: PhysicalRecord, partitions: int) -> int:
        lower = self.grow_lower[record.slice_id]
        upper = self.grow_upper_exclusive[record.slice_id]
        if not lower <= record.grow < upper:
            raise ValueError("A record escaped its registered slice aperture")
        return min(
            partitions - 1,
            (record.grow - lower) * partitions // (upper - lower),
        )

    @staticmethod
    def synthetic_full_ddr4() -> ApertureGeometry:
        """Explicit synthetic geometry for unit/adversarial checks only."""
        return ApertureGeometry((0,) * NUM_SLICES, (65_536,) * NUM_SLICES)


@dataclass(frozen=True)
class IssueEvent:
    epoch: int
    build_round: int
    slice_id: int
    grow: int
    a_line_paddr: int
    words: int


@dataclass
class RunResult:
    evidence_class: str
    logical_elements: int
    active_elements: int
    partitions: int
    epochs: int
    capacity_drains: int
    drain_reasons: dict[str, int]
    peak_offsets: int
    peak_row_slots: int
    peak_line_slots: int
    geometry_bound_respected: bool
    line_slot_rollovers: int
    build_rounds: int
    peak_reserved_responses: int
    peak_reserved_words: int
    a_line_requests: int
    materialized_issue_order_entries: int
    per_slice_row_transitions: int
    issue_order_sha256: str
    b_unique_lines_per_pass: int
    b_line_reads: int
    b_reread_lines: int
    b_semantic_bytes: int
    selector_words: int
    selector_cycles_lower_bound: int
    placements: int
    missing_placements: int
    duplicate_placements: int
    synthetic_output_hash: int

    def summary(self) -> dict[str, object]:
        return asdict(self)


class IssueValidationOracle:
    """Streaming issue-order oracle that policy emits to but never reads."""

    def __init__(self) -> None:
        self._digest = hashlib.sha256()
        self._last_grow = [-1] * NUM_SLICES
        self.requests = 0
        self.per_slice_row_transitions = 0

    def observe(self, event: IssueEvent) -> None:
        self._digest.update(event.slice_id.to_bytes(1, "little"))
        self._digest.update(event.grow.to_bytes(2, "little"))
        self._digest.update(event.a_line_paddr.to_bytes(8, "little"))
        if self._last_grow[event.slice_id] != -1 and (
            self._last_grow[event.slice_id] != event.grow
        ):
            self.per_slice_row_transitions += 1
        self._last_grow[event.slice_id] = event.grow
        self.requests += 1

    def hexdigest(self) -> str:
        return self._digest.hexdigest()


class FiniteTables:
    """Fixed prospective hardware arrays; none grows after construction."""

    def __init__(self, active_entries: int) -> None:
        if active_entries <= 0 or active_entries > ACTIVE_ELEMENTS:
            raise ValueError("active entries must be in [1,4096]")
        self.active_entries = active_entries

        self.offset_valid = [False] * active_entries
        self.offset_itr = [0] * active_entries
        self.offset_wid = [0] * active_entries
        self.offset_next = [-1] * active_entries

        self.row_valid = [False] * ROW_SLOTS
        self.row_grow = [0] * ROW_SLOTS
        self.row_sent = [False] * ROW_SLOTS

        self.line_valid = [False] * LINE_SLOTS
        self.line_addr = [0] * LINE_SLOTS
        self.line_head = [-1] * LINE_SLOTS
        self.line_tail = [-1] * LINE_SLOTS
        self.line_words = [0] * LINE_SLOTS

        self.offset_occupancy = 0
        self.row_occupancy = 0
        self.line_occupancy = 0
        self.peak_offsets = 0
        self.peak_rows = 0
        self.peak_lines = 0

        # Fixed issue-selection state.  These arrays and scalars correspond
        # exactly to the charged row.sent, cursor.*, and control native-slice
        # fields in storage_ledger(); no line-order vector is constructed.
        self.issue_row_cursor = [0] * NUM_SLICES
        self.issue_grow_row_cursor = [0] * NUM_SLICES
        self.issue_line_cursor = [0] * NUM_SLICES
        self.issue_active_grow = [0] * NUM_SLICES
        self.issue_active_grow_valid = [False] * NUM_SLICES
        self.issue_native_slice_cursor = 0
        self.issue_remaining = 0
        self.issue_build_round = 0
        self.issue_reserved_slots = 0
        self.issue_reserved_words = 0
        self.issue_peak_slots = 0
        self.issue_peak_words = 0
        self.issue_started = False

    def is_empty(self) -> bool:
        return self.offset_occupancy == 0

    @staticmethod
    def _row_base(slice_id: int) -> int:
        return slice_id * ROWS_PER_SLICE

    @staticmethod
    def _line_base(row_slot: int) -> int:
        return row_slot * LINES_PER_ROW

    def _allocate_offset(self, record: PhysicalRecord) -> int:
        slot = self.offset_occupancy
        self.offset_valid[slot] = True
        self.offset_itr[slot] = record.itr
        self.offset_wid[slot] = record.wid
        self.offset_next[slot] = -1
        self.offset_occupancy += 1
        self.peak_offsets = max(self.peak_offsets, self.offset_occupancy)
        return slot

    def insert(self, record: PhysicalRecord) -> tuple[bool, int | None, bool]:
        """Insert atomically or return a drain reason without mutation."""
        base = self._row_base(record.slice_id)
        free_row = -1
        grow_row_with_free_line = -1
        exact_line = -1

        for row_slot in range(base, base + ROWS_PER_SLICE):
            if not self.row_valid[row_slot]:
                if free_row == -1:
                    free_row = row_slot
                continue
            if self.row_grow[row_slot] != record.grow:
                continue
            line_base = self._line_base(row_slot)
            row_has_free_line = False
            for line_slot in range(line_base, line_base + LINES_PER_ROW):
                if self.line_valid[line_slot]:
                    if self.line_addr[line_slot] == record.a_line_paddr:
                        exact_line = line_slot
                        break
                else:
                    row_has_free_line = True
            if exact_line != -1:
                break
            if row_has_free_line and grow_row_with_free_line == -1:
                grow_row_with_free_line = row_slot

        if exact_line != -1:
            if self.line_words[exact_line] >= RESPONSE_WORD_POOL:
                return False, DRAIN_LINE_WORDS, False
            if self.offset_occupancy >= self.active_entries:
                return False, DRAIN_OFFSET, False
            offset = self._allocate_offset(record)
            tail = self.line_tail[exact_line]
            if tail != -1:
                self.offset_next[tail] = offset
            self.line_tail[exact_line] = offset
            self.line_words[exact_line] += 1
            return True, None, False

        row_slot = grow_row_with_free_line
        rolled_over = False
        if row_slot == -1:
            if free_row == -1:
                return False, DRAIN_ROW, False
            row_slot = free_row
            # A new row with an already-present grow means the previous row's
            # eight finite line slots were exhausted.
            rolled_over = any(
                self.row_valid[slot] and self.row_grow[slot] == record.grow
                for slot in range(base, base + ROWS_PER_SLICE)
            )

        if self.offset_occupancy >= self.active_entries:
            return False, DRAIN_OFFSET, False

        line_slot = -1
        line_base = self._line_base(row_slot)
        for candidate in range(line_base, line_base + LINES_PER_ROW):
            if not self.line_valid[candidate]:
                line_slot = candidate
                break
        if line_slot == -1:
            raise AssertionError("selected row has no finite line slot")

        # All checks above precede the first mutation.
        if not self.row_valid[row_slot]:
            self.row_valid[row_slot] = True
            self.row_grow[row_slot] = record.grow
            self.row_occupancy += 1
            self.peak_rows = max(self.peak_rows, self.row_occupancy)
        self.line_valid[line_slot] = True
        self.line_addr[line_slot] = record.a_line_paddr
        self.line_occupancy += 1
        self.peak_lines = max(self.peak_lines, self.line_occupancy)
        offset = self._allocate_offset(record)
        self.line_head[line_slot] = offset
        self.line_tail[line_slot] = offset
        self.line_words[line_slot] = 1
        return True, None, rolled_over

    def _next_native_line(self, slice_id: int) -> int | None:
        """Select one line directly from fixed row/line arrays and cursors."""
        base = self._row_base(slice_id)
        while True:
            if not self.issue_active_grow_valid[slice_id]:
                first = self.issue_row_cursor[slice_id]
                while first < ROWS_PER_SLICE:
                    row_slot = base + first
                    if (
                        self.row_valid[row_slot]
                        and not self.row_sent[row_slot]
                    ):
                        break
                    first += 1
                self.issue_row_cursor[slice_id] = first
                if first == ROWS_PER_SLICE:
                    return None
                self.issue_active_grow[slice_id] = self.row_grow[base + first]
                self.issue_active_grow_valid[slice_id] = True
                self.issue_grow_row_cursor[slice_id] = first
                self.issue_line_cursor[slice_id] = 0
                self.issue_row_cursor[slice_id] = first + 1

            local_row = self.issue_grow_row_cursor[slice_id]
            while local_row < ROWS_PER_SLICE:
                row_slot = base + local_row
                if (
                    self.row_valid[row_slot]
                    and not self.row_sent[row_slot]
                    and self.row_grow[row_slot]
                    == self.issue_active_grow[slice_id]
                ):
                    line_cursor = self.issue_line_cursor[slice_id]
                    line_base = self._line_base(row_slot)
                    while line_cursor < LINES_PER_ROW:
                        line_slot = line_base + line_cursor
                        line_cursor += 1
                        self.issue_line_cursor[slice_id] = line_cursor
                        if self.line_valid[line_slot]:
                            return line_slot
                    self.row_sent[row_slot] = True
                    self.issue_line_cursor[slice_id] = 0
                local_row += 1
                self.issue_grow_row_cursor[slice_id] = local_row

            self.issue_active_grow_valid[slice_id] = False
            self.issue_grow_row_cursor[slice_id] = 0
            self.issue_line_cursor[slice_id] = 0

    def begin_issue(self, first_build_round: int) -> None:
        """Initialize finite issue state for the current nonempty epoch."""
        if self.issue_started:
            raise AssertionError("issue traversal is already active")
        if self.is_empty() or self.line_occupancy <= 0:
            raise AssertionError("cannot issue empty tables")
        for row_slot in range(ROW_SLOTS):
            self.row_sent[row_slot] = False
        for slice_id in range(NUM_SLICES):
            self.issue_row_cursor[slice_id] = 0
            self.issue_grow_row_cursor[slice_id] = 0
            self.issue_line_cursor[slice_id] = 0
            self.issue_active_grow[slice_id] = 0
            self.issue_active_grow_valid[slice_id] = False
        self.issue_native_slice_cursor = 0
        self.issue_remaining = self.line_occupancy
        self.issue_build_round = first_build_round
        self.issue_reserved_slots = 0
        self.issue_reserved_words = 0
        self.issue_peak_slots = 0
        self.issue_peak_words = 0
        self.issue_started = True

    def issue_next(self, epoch: int) -> IssueEvent | None:
        """Issue one request using native bank-outer/BG-inner traversal."""
        if not self.issue_started:
            raise AssertionError("issue traversal was not initialized")
        if self.issue_remaining == 0:
            return None

        for _ in range(NUM_SLICES):
            traversal_slot = self.issue_native_slice_cursor
            self.issue_native_slice_cursor = (
                self.issue_native_slice_cursor + 1
            ) % NUM_SLICES
            bank = traversal_slot // NUM_BANK_GROUPS
            bankgroup = traversal_slot % NUM_BANK_GROUPS
            slice_id = bankgroup * NUM_BANKS + bank
            line_slot = self._next_native_line(slice_id)
            if line_slot is None:
                continue

            words = self.line_words[line_slot]
            if words <= 0 or words > RESPONSE_WORD_POOL:
                raise AssertionError(
                    "line violates finite response descriptor"
                )
            if (
                self.issue_reserved_slots == RESPONSE_SLOTS
                or self.issue_reserved_words + words > RESPONSE_WORD_POOL
            ):
                self.issue_build_round += 1
                self.issue_reserved_slots = 0
                self.issue_reserved_words = 0
            row_slot = line_slot // LINES_PER_ROW
            event = IssueEvent(
                epoch=epoch,
                build_round=self.issue_build_round,
                slice_id=slice_id,
                grow=self.row_grow[row_slot],
                a_line_paddr=self.line_addr[line_slot],
                words=words,
            )
            self.issue_remaining -= 1
            self.issue_reserved_slots += 1
            self.issue_reserved_words += words
            self.issue_peak_slots = max(
                self.issue_peak_slots, self.issue_reserved_slots
            )
            self.issue_peak_words = max(
                self.issue_peak_words, self.issue_reserved_words
            )
            return event

        raise AssertionError("native slice traversal made no progress")

    def finish_issue(self) -> tuple[int, int, int]:
        """Finish an exhausted traversal and return next-round/peak counters."""
        if not self.issue_started or self.issue_remaining != 0:
            raise AssertionError("issue traversal is not exhausted")
        self.issue_started = False
        return (
            self.issue_build_round + 1,
            self.issue_peak_slots,
            self.issue_peak_words,
        )

    def placements(self, counts: list[int]) -> None:
        for slot in range(self.offset_occupancy):
            if not self.offset_valid[slot]:
                raise AssertionError("invalid live Offset slot")
            counts[self.offset_itr[slot]] += 1

    def reset(self) -> None:
        # Array capacities remain fixed; only live prefixes/valid bits reset.
        for slot in range(self.offset_occupancy):
            self.offset_valid[slot] = False
            self.offset_next[slot] = -1
        for slot in range(LINE_SLOTS):
            if self.line_valid[slot]:
                self.line_valid[slot] = False
                self.line_head[slot] = -1
                self.line_tail[slot] = -1
                self.line_words[slot] = 0
        for slot in range(ROW_SLOTS):
            self.row_valid[slot] = False
            self.row_sent[slot] = False
        self.offset_occupancy = 0
        self.row_occupancy = 0
        self.line_occupancy = 0


class Model:
    def __init__(
        self,
        logical_elements: int = LOGICAL_ELEMENTS,
        active_elements: int = ACTIVE_ELEMENTS,
        source_elements: int = LOGICAL_ELEMENTS * 8,
        partitions: int = PARTITIONS,
        filter_words_per_cycle: int = FILTER_WORDS_PER_CYCLE,
    ) -> None:
        if logical_elements <= 0:
            raise ValueError("logical elements must be positive")
        if active_elements <= 0 or active_elements > ACTIVE_ELEMENTS:
            raise ValueError("active elements must be in [1,4096]")
        if source_elements <= 0:
            raise ValueError("source elements must be positive")
        if partitions <= 0 or partitions > 64:
            raise ValueError("partitions must be in [1,64]")
        if filter_words_per_cycle <= 0:
            raise ValueError("filter throughput must be positive")
        self.n = logical_elements
        self.k = active_elements
        self.source_elements = source_elements
        self.partitions = partitions
        self.filter_words_per_cycle = filter_words_per_cycle
        self.tables: FiniteTables | None = None

    @staticmethod
    def _strict_int(value: object, name: str) -> int:
        if type(value) is not int:
            raise ValueError(f"{name} must be an integer")
        return value

    def _validate_record(self, record: PhysicalRecord) -> None:
        fields = asdict(record)
        for name, value in fields.items():
            self._strict_int(value, name)
        if not 0 <= record.itr < self.n:
            raise ValueError("iteration is out of range")
        if not 0 <= record.index < self.source_elements:
            raise ValueError("B index is out of source range")
        if record.b_paddr < 0 or record.b_paddr % INDEX_BYTES:
            raise ValueError("B physical address is malformed")
        if record.a_line_paddr < 0 or record.a_line_paddr % CACHE_LINE_BYTES:
            raise ValueError("A physical line is malformed")
        if record.channel != 0 or record.rank != 0:
            raise ValueError("record is outside frozen channel/rank geometry")
        if not 0 <= record.bankgroup < NUM_BANK_GROUPS:
            raise ValueError("bank group is out of range")
        if not 0 <= record.bank < NUM_BANKS:
            raise ValueError("bank is out of range")
        if not 0 <= record.row < 65_536:
            raise ValueError("DDR row is out of range")
        if not 0 <= record.column < 1_024:
            raise ValueError("DDR column is out of range")
        if not 0 <= record.wid < CACHE_LINE_BYTES // WORD_BYTES:
            raise ValueError("word id is out of range")
        if not 0 <= record.slice_id < NUM_SLICES:
            raise ValueError("derived slice is out of range")

    def _validate_trace(
        self, records: Sequence[PhysicalRecord], geometry: ApertureGeometry
    ) -> None:
        # Full preflight is an evidence/oracle operation.  Policy tables do not
        # exist until every B index and physical field has been rejected or
        # accepted, so malformed input cannot mutate policy state.
        if len(records) != self.n:
            raise ValueError("trace record count does not match logical count")
        geometry.validate()
        seen = [False] * self.n
        for record in records:
            self._validate_record(record)
            geometry.partition(record, self.partitions)
            if seen[record.itr]:
                raise ValueError("duplicate logical iteration")
            seen[record.itr] = True
        if not all(seen):
            raise ValueError("trace omits a logical iteration")

    def run(
        self, records: Sequence[PhysicalRecord], geometry: ApertureGeometry
    ) -> RunResult:
        self._validate_trace(records, geometry)
        self.tables = FiniteTables(self.k)

        placements = [0] * self.n  # validation oracle only
        issue_oracle = IssueValidationOracle()
        drain_counts = [0, 0, 0]
        epochs = 0
        capacity_drains = 0
        rollovers = 0
        build_rounds = 0
        peak_reserved_slots = 0
        peak_reserved_words = 0

        def drain() -> None:
            nonlocal epochs, build_rounds
            nonlocal peak_reserved_slots, peak_reserved_words
            if self.tables is None or self.tables.is_empty():
                return
            self.tables.begin_issue(build_rounds)
            while True:
                event = self.tables.issue_next(epochs)
                if event is None:
                    break
                issue_oracle.observe(event)
            next_round, peak_slots, peak_words = self.tables.finish_issue()
            build_rounds = next_round
            peak_reserved_slots = max(peak_reserved_slots, peak_slots)
            peak_reserved_words = max(peak_reserved_words, peak_words)
            self.tables.placements(placements)
            self.tables.reset()
            epochs += 1

        for partition in range(self.partitions):
            for record in records:
                if geometry.partition(record, self.partitions) != partition:
                    continue
                inserted, reason, rolled_over = self.tables.insert(record)
                if not inserted:
                    if reason is None or self.tables.is_empty():
                        raise AssertionError(
                            "finite record cannot fit empty tables"
                        )
                    drain_counts[reason] += 1
                    capacity_drains += 1
                    drain()
                    inserted, retry_reason, rolled_over = self.tables.insert(
                        record
                    )
                    if not inserted:
                        raise AssertionError(
                            f"record still failed after {DRAIN_NAMES[retry_reason]} drain"
                        )
                if rolled_over:
                    rollovers += 1
            drain()

        # This hash deliberately checks only the synthetic semantic formula;
        # it is never represented as the frozen workload oracle.
        output_hash = 1469598103934665603
        by_itr = sorted(records, key=lambda record: record.itr)
        for record in by_itr:
            value = record.index * 17 + 3
            for byte in value.to_bytes(8, "little"):
                output_hash ^= byte
                output_hash = (output_hash * 1099511628211) & ((1 << 64) - 1)

        b_lines = len(
            {record.b_paddr // CACHE_LINE_BYTES for record in records}
        )
        peaks = self.tables
        return RunResult(
            evidence_class="synthetic_semantic_check_only",
            logical_elements=self.n,
            active_elements=self.k,
            partitions=self.partitions,
            epochs=epochs,
            capacity_drains=capacity_drains,
            drain_reasons={
                name: drain_counts[index]
                for index, name in enumerate(DRAIN_NAMES)
            },
            peak_offsets=peaks.peak_offsets,
            peak_row_slots=peaks.peak_rows,
            peak_line_slots=peaks.peak_lines,
            geometry_bound_respected=(
                peaks.peak_offsets <= self.k
                and peaks.peak_rows <= ROW_SLOTS
                and peaks.peak_lines <= LINE_SLOTS
            ),
            line_slot_rollovers=rollovers,
            build_rounds=build_rounds,
            peak_reserved_responses=peak_reserved_slots,
            peak_reserved_words=peak_reserved_words,
            a_line_requests=issue_oracle.requests,
            materialized_issue_order_entries=0,
            per_slice_row_transitions=(issue_oracle.per_slice_row_transitions),
            issue_order_sha256=issue_oracle.hexdigest(),
            b_unique_lines_per_pass=b_lines,
            b_line_reads=b_lines * self.partitions,
            b_reread_lines=b_lines * (self.partitions - 1),
            b_semantic_bytes=self.n * INDEX_BYTES * self.partitions,
            selector_words=self.n * self.partitions,
            selector_cycles_lower_bound=ceil_div(
                self.n * self.partitions, self.filter_words_per_cycle
            ),
            placements=sum(count > 0 for count in placements),
            missing_placements=sum(count == 0 for count in placements),
            duplicate_placements=sum(
                max(0, count - 1) for count in placements
            ),
            synthetic_output_hash=output_hash,
        )


def storage_ledger(
    logical_elements: int, active_elements: int
) -> dict[str, object]:
    """Source-realizable, byte-addressable ledger with no hardcoded subtotal."""
    if logical_elements <= 0 or active_elements <= 0:
        raise ValueError("ledger sizes must be positive")
    if active_elements % (NUM_SLICES * LINES_PER_ROW):
        raise ValueError("active geometry must divide across slices and rows")
    row_slots = active_elements // LINES_PER_ROW
    rows_per_slice = row_slots // NUM_SLICES
    itr_bits = bits_for_values(logical_elements)
    offset_id_bits = bits_for_values(active_elements + 1)
    row_cursor_bits = bits_for_values(rows_per_slice + 1)
    scan_bits = bits_for_values(logical_elements + 1)
    occupancy_bits = bits_for_values(active_elements + 1)
    row_slot_bits = bits_for_values(row_slots)
    line_slot_bits = bits_for_values(active_elements)

    fields = [
        ("offset.iteration", active_elements, itr_bits),
        ("offset.word_id", active_elements, 3),
        ("offset.next_or_sentinel", active_elements, offset_id_bits),
        ("offset.valid", active_elements, 1),
        ("row.grow", row_slots, 16),
        ("row.valid", row_slots, 1),
        ("row.sent", row_slots, 1),
        ("line.paddr_line", active_elements, 58),
        ("line.head", active_elements, offset_id_bits),
        ("line.tail", active_elements, offset_id_bits),
        (
            "line.word_count",
            active_elements,
            bits_for_values(RESPONSE_WORD_POOL + 1),
        ),
        ("line.valid", active_elements, 1),
        ("line.claimed", active_elements, 1),
        ("response.generation", RESPONSE_SLOTS, 16),
        ("response.slice", RESPONSE_SLOTS, bits_for_values(NUM_SLICES)),
        ("response.row_slot", RESPONSE_SLOTS, row_slot_bits),
        ("response.line_slot", RESPONSE_SLOTS, line_slot_bits),
        ("response.paddr_line", RESPONSE_SLOTS, 58),
        ("response.offset_head", RESPONSE_SLOTS, offset_id_bits),
        (
            "response.offset_count",
            RESPONSE_SLOTS,
            bits_for_values(RESPONSE_WORD_POOL + 1),
        ),
        ("response.valid", RESPONSE_SLOTS, 1),
        ("response_word.payload", RESPONSE_WORD_POOL, 64),
        (
            "response_word.owner_slot",
            RESPONSE_WORD_POOL,
            bits_for_values(RESPONSE_SLOTS),
        ),
        ("response_word.valid", RESPONSE_WORD_POOL, 1),
        (
            "invalidator.logical_line_bits",
            32 * logical_elements * INDEX_BYTES // CACHE_LINE_BYTES,
            1,
        ),
        ("partition.lower_grow", NUM_SLICES * PARTITIONS, 17),
        ("partition.upper_exclusive_grow", NUM_SLICES * PARTITIONS, 17),
        ("partition.bound_valid", NUM_SLICES * PARTITIONS, 1),
        ("cursor.row", NUM_SLICES, row_cursor_bits),
        ("cursor.grow_row", NUM_SLICES, row_cursor_bits),
        ("cursor.line", NUM_SLICES, 4),
        ("cursor.active_grow", NUM_SLICES, 16),
        ("cursor.active_grow_valid", NUM_SLICES, 1),
        ("control.partition_id", 1, bits_for_values(PARTITIONS)),
        ("control.scan_iteration", 1, scan_bits),
        ("control.epoch_occupancy", 1, occupancy_bits),
        (
            "control.selector_credit",
            1,
            bits_for_values(FILTER_WORDS_PER_CYCLE + 1),
        ),
        ("control.drain_reason", 1, bits_for_values(len(DRAIN_NAMES) + 1)),
        ("control.state", 1, 3),
        ("control.native_slice_cursor", 1, bits_for_values(NUM_SLICES + 1)),
        (
            "control.response_slots_used",
            1,
            bits_for_values(RESPONSE_SLOTS + 1),
        ),
        (
            "control.response_words_used",
            1,
            bits_for_values(RESPONSE_WORD_POOL + 1),
        ),
        ("control.owner_indirect_unit_id", 1, 2),
        ("control.owner_instruction_id", 1, 16),
        ("control.owner_generation", 1, 16),
        ("control.b_base_paddr", 1, 64),
        ("control.a_base_vaddr", 1, 64),
        ("control.logical_count", 1, scan_bits),
        ("control.source_elements", 1, 32),
        ("control.word_size_code", 1, 2),
        ("control.destination_tile_id", 1, 5),
        ("control.placements_completed", 1, scan_bits),
        ("control.pending_writes", 1, bits_for_values(65)),
    ]
    charged = [
        {
            "name": name,
            "count": count,
            "field_bits": bits,
            "bytes_per_element": ceil_div(bits, 8),
            "charged_bytes": byte_array_bytes(count, bits),
        }
        for name, count, bits in fields
    ]
    return {
        "logical_elements": logical_elements,
        "active_elements": active_elements,
        "slices": NUM_SLICES,
        "row_slots": row_slots,
        "rows_per_slice": rows_per_slice,
        "line_slots": active_elements,
        "lines_per_row": LINES_PER_ROW,
        "response_slots": RESPONSE_SLOTS,
        "response_word_pool": RESPONSE_WORD_POOL,
        "packing_rule": (
            "each field array element rounds independently to bytes; "
            "no cross-entry or cross-field optimistic packing"
        ),
        "fields": charged,
        "charged_total_bytes": sum(
            field["charged_bytes"] for field in charged
        ),
    }


def _synthetic_record(
    itr: int,
    *,
    index: int | None = None,
    slice_id: int = 0,
    grow: int = 0,
    line: int | None = None,
    wid: int = 0,
) -> PhysicalRecord:
    """Explicit synthetic record used only by committed adversarial checks."""
    bankgroup, bank = divmod(slice_id, NUM_BANKS)
    if line is None:
        line = itr
    return PhysicalRecord(
        itr=itr,
        index=itr if index is None else index,
        b_paddr=0x100004 + itr * INDEX_BYTES,
        a_line_paddr=0x400000 + line * CACHE_LINE_BYTES,
        channel=0,
        rank=0,
        bankgroup=bankgroup,
        bank=bank,
        row=grow,
        column=line % 1024,
        wid=wid,
    )


def _synthetic_exact_capacity(
    count: int, grow_base: int = 0
) -> list[PhysicalRecord]:
    records: list[PhysicalRecord] = []  # test stimulus, never policy state
    for itr in range(count):
        line = itr // 16
        records.append(
            _synthetic_record(
                itr,
                slice_id=line % NUM_SLICES,
                grow=grow_base + line // NUM_SLICES,
                line=line,
                wid=itr % 8,
            )
        )
    return records


def _synthetic_full_line_capacity() -> list[PhysicalRecord]:
    """Fill every fixed Offset, row, and line slot exactly once."""
    records: list[PhysicalRecord] = []
    for itr in range(ACTIVE_ELEMENTS):
        slice_id = itr % NUM_SLICES
        local_line = itr // NUM_SLICES
        records.append(
            _synthetic_record(
                itr,
                slice_id=slice_id,
                grow=local_line // LINES_PER_ROW,
                line=itr,
                wid=itr % (CACHE_LINE_BYTES // WORD_BYTES),
            )
        )
    return records


def synthetic_adversarial_results() -> dict[str, object]:
    geometry = ApertureGeometry.synthetic_full_ddr4()
    cases: dict[str, RunResult] = {}
    cases["exact_4096_offsets"] = Model(
        logical_elements=4096, source_elements=8192
    ).run(_synthetic_exact_capacity(4096), geometry)
    cases["one_past_4097_offsets"] = Model(
        logical_elements=4097, source_elements=8192
    ).run(_synthetic_exact_capacity(4097), geometry)
    distinct_rows = [
        _synthetic_record(
            itr,
            slice_id=itr % NUM_SLICES,
            grow=itr // NUM_SLICES,
            line=itr,
        )
        for itr in range(4096)
    ]
    cases["distinct_4096_rows"] = Model(
        logical_elements=4096, source_elements=8192
    ).run(distinct_rows, geometry)
    cases["nine_lines_one_grow"] = Model(
        logical_elements=9, source_elements=32
    ).run(
        [_synthetic_record(itr, grow=11, line=itr) for itr in range(9)],
        geometry,
    )
    cases["row_slot_exhaustion"] = Model(
        logical_elements=257, source_elements=512
    ).run(
        [_synthetic_record(itr, grow=19, line=itr) for itr in range(257)],
        geometry,
    )
    cases["one_line_fanout"] = Model(
        logical_elements=4096, source_elements=8192
    ).run(
        [
            _synthetic_record(itr, index=13, grow=23, line=7)
            for itr in range(4096)
        ],
        geometry,
    )
    cases["partition_skew"] = Model(
        logical_elements=4096, source_elements=8192
    ).run(_synthetic_exact_capacity(4096, grow_base=50_000), geometry)
    cases["full_4096_line_occupancy"] = Model(
        logical_elements=4096, source_elements=8192
    ).run(_synthetic_full_line_capacity(), geometry)

    fields = (
        "epochs",
        "capacity_drains",
        "drain_reasons",
        "peak_offsets",
        "peak_row_slots",
        "peak_line_slots",
        "line_slot_rollovers",
        "peak_reserved_responses",
        "peak_reserved_words",
        "a_line_requests",
        "materialized_issue_order_entries",
        "issue_order_sha256",
        "selector_words",
        "placements",
        "missing_placements",
        "duplicate_placements",
        "geometry_bound_respected",
    )
    return {
        name: {field: getattr(result, field) for field in fields}
        for name, result in cases.items()
    }


def model_report() -> dict[str, object]:
    return {
        "schema": 3,
        "evidence_class": "model_only",
        "workload_trace_status": "blocked_new_gem5_physical_trace_required",
        "workload_a_line_comparisons": None,
        "authorization": {
            "production": False,
            "performance": False,
            "requires_new_physical_trace": True,
        },
        "reason": (
            "frozen MAAVirtualTrace logs contain lifecycle counters but no "
            "per-iteration B paddr and translated A paddr/slice/grow records"
        ),
        "finite_geometry": {
            "offset_entries": ACTIVE_ELEMENTS,
            "slices": NUM_SLICES,
            "rows_per_slice": ROWS_PER_SLICE,
            "row_slots": ROW_SLOTS,
            "lines_per_row": LINES_PER_ROW,
            "line_slots": LINE_SLOTS,
            "response_slots": RESPONSE_SLOTS,
            "response_word_pool": RESPONSE_WORD_POOL,
            "issue_selection": {
                "mechanism": "direct_fixed_array_cursor_walk",
                "materialized_order_entries": 0,
                "row_sent_entries": ROW_SLOTS,
                "per_slice_cursor_sets": NUM_SLICES,
                "native_slice_cursor_entries": 1,
                "validation_order_storage": "streaming_digest_only",
            },
            "native_slice_traversal": [
                bg * NUM_BANKS + bank
                for bank in range(NUM_BANKS)
                for bg in range(NUM_BANK_GROUPS)
            ],
        },
        "synthetic_adversarial_results": synthetic_adversarial_results(),
        "ledgers": {
            "logical16k_active4k": storage_ledger(16_384, 4_096),
            "logical64k_active16k_arithmetic_only": storage_ledger(
                65_536, 16_384
            ),
        },
        "output_evidence": {
            "model_hash": "synthetic semantic check only",
            "frozen_workload_oracle": 7_228_541_527_853_630_339,
            "frozen_oracle_verified_by": "audit_gem5_controls.py",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            model_report(),
            indent=None if args.compact else 2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
