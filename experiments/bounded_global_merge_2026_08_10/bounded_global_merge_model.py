#!/usr/bin/env python3
"""Executable bounded four-run global-merge model.

The candidate reuses the current counted-grow four-population plan.  During
the final B scan, one population occupies a 4K descriptor workspace and the
other three are appended to a timing-visible logical LLC store.  The resident
population is sorted and written as a run.  Each external population is then
read into the same workspace, sorted, and written back in place.  A four-head
merge reads the immutable runs and issues one A request for each equal-line
cluster while restoring every result by its explicit logical iteration.

This is an architectural trace model, not a gem5 timing model.  It deliberately
executes descriptor packing, line-buffered backing accesses, in-place heap
sort, bounded four-head merge, A-line coalescing, DRAM-row accounting, and
exact output reconstruction instead of treating them as prose assumptions.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Callable,
    Iterator,
    Sequence,
)

SOURCE_COMMIT = "ee08be4bb902ac72ced1f34ed02771cbe9588114"
TRACE_SCHEMA = "dx100.physical_admission.v1"
RESULT_SCHEMA = "dx100.bounded_global_merge.v1"

LOGICAL_LIMIT = 16_384
ACTIVE_DESCRIPTOR_LIMIT = 4_096
POPULATIONS = 4
MAX_GROW_RECORDS = 64
DESCRIPTOR_BYTES = 6
LINE_BYTES = 64
RUN_STRIDE_BYTES = ACTIVE_DESCRIPTOR_LIMIT * DESCRIPTOR_BYTES
BACKING_BASE = 0x8000_0000
B_WORD_BYTES = 4
A_WORD_BYTES = 8

# DDR4_8Gb_x8, one channel/rank, RoBaRaCoCh, after the 64-byte transaction
# offset used by the frozen DX100 trace.  MAA::map_addr consumes column, rank,
# bank-group, bank, and row in that order after the channel field.
COLUMN_LINE_BITS = 7
BANK_GROUP_BITS = 2
BANK_BITS = 2
ROW_BITS = 17
SLICES = 16
ROW_TABLE_SLICE_ORDER = tuple(
    bank_group * 4 + bank for bank in range(4) for bank_group in range(4)
)
SLICE_RANK = {
    native_slice: rank
    for rank, native_slice in enumerate(ROW_TABLE_SLICE_ORDER)
}


class ModelError(RuntimeError):
    """Fail-closed model contract violation."""


def ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


def sha256_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class Descriptor:
    logical_i: int
    b_value: int

    def pack(self) -> bytes:
        if not 0 <= self.logical_i < LOGICAL_LIMIT:
            raise ModelError(f"logical i {self.logical_i} is out of range")
        if not 0 <= self.b_value <= 0xFFFF_FFFF:
            raise ModelError(f"B value {self.b_value} is not uint32")
        payload = (self.b_value << 14) | self.logical_i
        if payload >= (1 << 46):
            raise ModelError("descriptor exceeds its 46-bit payload")
        return payload.to_bytes(DESCRIPTOR_BYTES, "little")

    @classmethod
    def unpack(cls, payload: bytes) -> Descriptor:
        if len(payload) != DESCRIPTOR_BYTES:
            raise ModelError("descriptor payload is not six bytes")
        packed = int.from_bytes(payload, "little")
        if packed >> 46:
            raise ModelError("descriptor reserved bits are nonzero")
        return cls(
            logical_i=packed & ((1 << 14) - 1),
            b_value=(packed >> 14) & 0xFFFF_FFFF,
        )


@dataclass(frozen=True)
class PhysicalLocation:
    word_paddr: int
    line_paddr: int
    wid: int
    column: int
    bank_group: int
    bank: int
    row: int
    native_slice: int
    slice_rank: int
    grow: int


@dataclass(frozen=True)
class AddressTranslation:
    """Existing page-table translation used to reconstruct A[B[i]]."""

    base_page_offset: int
    physical_pages: tuple[tuple[int, int], ...]

    @functools.lru_cache(maxsize=None)
    def translate(self, b_value: int) -> int:
        linear_offset = self.base_page_offset + b_value * A_WORD_BYTES
        virtual_page = linear_offset // 4096
        in_page = linear_offset % 4096
        page_map = dict(self.physical_pages)
        if virtual_page not in page_map:
            raise ModelError(
                f"A translation has no physical page for virtual page {virtual_page}"
            )
        return page_map[virtual_page] + in_page

    def summary(self) -> dict[str, object]:
        return {
            "base_page_offset": self.base_page_offset,
            "observed_pages": len(self.physical_pages),
            "page_table_is_existing_address_translation_not_reorder_state": True,
            "physical_page_map_sha256": sha256_json(
                [[page, physical] for page, physical in self.physical_pages]
            ),
        }


@functools.lru_cache(maxsize=None)
def decode_descriptor(
    descriptor: Descriptor, translation: AddressTranslation
) -> PhysicalLocation:
    word_paddr = translation.translate(descriptor.b_value)
    if word_paddr % A_WORD_BYTES:
        raise ModelError("reconstructed A word is not naturally aligned")
    line_paddr = word_paddr & ~(LINE_BYTES - 1)
    wid = (word_paddr - line_paddr) // A_WORD_BYTES
    line_index = line_paddr // LINE_BYTES
    column_mask = (1 << COLUMN_LINE_BITS) - 1
    bank_group_mask = (1 << BANK_GROUP_BITS) - 1
    bank_mask = (1 << BANK_BITS) - 1
    row_mask = (1 << ROW_BITS) - 1
    column = line_index & column_mask
    line_index >>= COLUMN_LINE_BITS
    bank_group = line_index & bank_group_mask
    line_index >>= BANK_GROUP_BITS
    bank = line_index & bank_mask
    line_index >>= BANK_BITS
    row = line_index & row_mask
    if line_index >> ROW_BITS:
        raise ModelError("A address exceeds the modeled DDR4 geometry")
    native_slice = bank_group * 4 + bank
    return PhysicalLocation(
        word_paddr=word_paddr,
        line_paddr=line_paddr,
        wid=wid,
        column=column,
        bank_group=bank_group,
        bank=bank,
        row=row,
        native_slice=native_slice,
        slice_rank=SLICE_RANK[native_slice],
        grow=row,
    )


@functools.lru_cache(maxsize=None)
def descriptor_key(
    descriptor: Descriptor, translation: AddressTranslation
) -> tuple[int, int, int, int]:
    location = decode_descriptor(descriptor, translation)
    return (
        location.slice_rank,
        location.row,
        location.line_paddr,
        descriptor.logical_i,
    )


@dataclass(frozen=True)
class InputTrace:
    name: str
    descriptors: tuple[Descriptor, ...]
    a_translation: AddressTranslation
    b_base: int | None
    provenance: dict[str, object]

    @property
    def count(self) -> int:
        return len(self.descriptors)

    @property
    def population_capacity(self) -> int:
        return ceil_div(self.count, POPULATIONS)


def validate_trace(trace: InputTrace) -> None:
    if not 4 <= trace.count <= LOGICAL_LIMIT:
        raise ModelError("trace must contain 4..16384 descriptors")
    if trace.population_capacity > ACTIVE_DESCRIPTOR_LIMIT:
        raise ModelError("four populations cannot satisfy the 4096 bound")
    seen: set[int] = set()
    for expected, descriptor in enumerate(trace.descriptors):
        if descriptor.logical_i in seen:
            raise ModelError(f"duplicate logical i {descriptor.logical_i}")
        seen.add(descriptor.logical_i)
        if descriptor.logical_i != expected:
            raise ModelError(
                "logical iterations must be the exact dense range 0..N-1"
            )
        descriptor.pack()


def _strict_int(value: object, field: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError as error:
            raise ModelError(
                f"invalid integer for {field}: {value}"
            ) from error
    raise ModelError(f"missing or invalid integer field {field}")


def load_physical_trace(
    path: Path, expected_sha256: str, expected_records: int = LOGICAL_LIMIT
) -> InputTrace:
    raw_bytes = path.read_bytes()
    actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ModelError(
            f"trace SHA-256 mismatch: {actual_sha256} != {expected_sha256}"
        )
    raw_records: list[tuple[Descriptor, int, dict[str, object]]] = []
    b_bases: set[int] = set()
    for line_number, line in enumerate(raw_bytes.splitlines(), 1):
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as error:
            raise ModelError(
                f"invalid JSON on trace line {line_number}"
            ) from error
        if (
            raw.get("schema") != TRACE_SCHEMA
            or raw.get("event") != "physical_admission"
        ):
            raise ModelError(
                f"unexpected trace schema/event on line {line_number}"
            )
        descriptor = Descriptor(
            logical_i=_strict_int(raw.get("itr"), "itr"),
            b_value=_strict_int(raw.get("b_value"), "b_value"),
        )
        a_paddr = _strict_int(raw.get("a_paddr"), "a_paddr")
        b_paddr = _strict_int(raw.get("b_paddr"), "b_paddr")
        b_bases.add(b_paddr - descriptor.logical_i * B_WORD_BYTES)
        raw_records.append((descriptor, line_number, raw))
    if len(raw_records) != expected_records:
        raise ModelError(
            f"expected {expected_records} records, found {len(raw_records)}"
        )
    page_offsets = {
        (
            _strict_int(raw.get("a_paddr"), "a_paddr")
            - descriptor.b_value * A_WORD_BYTES
        )
        % 4096
        for descriptor, _, raw in raw_records
    }
    if len(page_offsets) != 1:
        raise ModelError("trace A base has no stable page offset")
    base_page_offset = next(iter(page_offsets))
    page_map: dict[int, int] = {}
    for descriptor, line_number, raw in raw_records:
        a_paddr = _strict_int(raw.get("a_paddr"), "a_paddr")
        linear_offset = base_page_offset + descriptor.b_value * A_WORD_BYTES
        virtual_page = linear_offset // 4096
        in_page = linear_offset % 4096
        physical_page = a_paddr - in_page
        if physical_page % 4096:
            raise ModelError(
                f"unaligned A physical page on trace line {line_number}"
            )
        if (
            virtual_page in page_map
            and page_map[virtual_page] != physical_page
        ):
            raise ModelError(
                f"inconsistent A page translation on trace line {line_number}"
            )
        page_map[virtual_page] = physical_page
    translation = AddressTranslation(
        base_page_offset=base_page_offset,
        physical_pages=tuple(sorted(page_map.items())),
    )
    # The minimum is the original sequential B array in the frozen spool
    # trace; higher bases are descriptor replay sources.  Traffic accounting
    # uses semantic B bytes and this base only for line-boundary rounding.
    original_b_base = min(b_bases)
    raw_records.sort(key=lambda item: item[0].logical_i)
    descriptors = tuple(item[0] for item in raw_records)
    trace = InputTrace(
        name="current_resident_first_physical_trace",
        descriptors=descriptors,
        a_translation=translation,
        b_base=original_b_base,
        provenance={
            "kind": "authenticated_physical_admission_trace",
            "path": str(path),
            "sha256": actual_sha256,
            "records": len(descriptors),
            "observed_b_source_bases": [hex(base) for base in sorted(b_bases)],
        },
    )
    validate_trace(trace)
    for descriptor, line_number, raw in raw_records:
        location = decode_descriptor(descriptor, translation)
        expected = {
            "a_paddr": location.word_paddr,
            "a_line_paddr": location.line_paddr,
            "wid": location.wid,
            "row": location.row,
            "grow_addr": location.grow,
            "native_slice": location.native_slice,
            "bank_group": location.bank_group,
            "bank": location.bank,
            "column": location.column,
        }
        for field, expected_value in expected.items():
            actual_value = _strict_int(raw.get(field), field)
            if actual_value != expected_value:
                raise ModelError(
                    f"decoded {field} mismatch on trace line {line_number}: "
                    f"{actual_value} != {expected_value}"
                )
    return trace


@dataclass(frozen=True)
class GrowPassPlan:
    populations: tuple[int, ...]
    counts: tuple[tuple[int, int], ...]
    quotas: tuple[tuple[int, tuple[int, ...]], ...]
    resident_pass: int
    planning_operations: int

    def quota_map(self) -> dict[int, tuple[int, ...]]:
        return dict(self.quotas)

    def assignments(
        self,
        descriptors: Sequence[Descriptor],
        translation: AddressTranslation,
    ) -> Iterator[tuple[Descriptor, int]]:
        quota_map = self.quota_map()
        ordinals = {grow: 0 for grow, _ in self.counts}
        for descriptor in descriptors:
            grow = decode_descriptor(descriptor, translation).grow
            if grow not in quota_map:
                raise ModelError(f"unplanned grow {grow}")
            ordinal = ordinals[grow]
            ordinals[grow] += 1
            cursor = 0
            assigned = -1
            for population, quota in enumerate(quota_map[grow]):
                if cursor <= ordinal < cursor + quota:
                    assigned = population
                    break
                cursor += quota
            if assigned == -1:
                raise ModelError(
                    f"grow {grow} ordinal {ordinal} is unassigned"
                )
            yield descriptor, assigned
        for grow, expected in self.counts:
            if ordinals[grow] != expected:
                raise ModelError(f"grow {grow} replay is incomplete")


def build_grow_pass_plan(trace: InputTrace) -> GrowPassPlan:
    counts_by_grow: dict[int, int] = {}
    for descriptor in trace.descriptors:
        grow = decode_descriptor(descriptor, trace.a_translation).grow
        counts_by_grow[grow] = counts_by_grow.get(grow, 0) + 1
    if not 1 <= len(counts_by_grow) <= MAX_GROW_RECORDS:
        raise ModelError(
            f"grow plan needs {len(counts_by_grow)} records (max 64)"
        )
    capacity = trace.population_capacity
    expected_populations = tuple(
        min(capacity, trace.count - population * capacity)
        for population in range(POPULATIONS)
    )
    if any(population <= 0 for population in expected_populations):
        raise ModelError(
            "the trace does not produce four nonempty populations"
        )
    ranked = sorted(
        counts_by_grow, key=lambda grow: (-counts_by_grow[grow], grow)
    )
    populations = [0] * POPULATIONS
    quota_map = {grow: [0] * POPULATIONS for grow in counts_by_grow}
    placed: set[int] = set()
    planning_operations = len(counts_by_grow)

    # Exact first-fit whole-grow phase from BoundedGrowPassPlan.
    for grow in ranked:
        count = counts_by_grow[grow]
        if count > capacity:
            continue
        for population in range(POPULATIONS):
            planning_operations += 1
            if capacity - populations[population] < count:
                continue
            quota_map[grow][population] = count
            populations[population] += count
            placed.add(grow)
            break

    # Exact deterministic ordinal quotas for every grow that did not fit whole.
    for grow in ranked:
        if grow in placed:
            continue
        remaining = counts_by_grow[grow]
        population = 0
        while remaining:
            planning_operations += 1
            while (
                population < POPULATIONS
                and populations[population] == capacity
            ):
                population += 1
            if population == POPULATIONS:
                raise ModelError(
                    "grow plan cannot fit four bounded populations"
                )
            gap = capacity - populations[population]
            quota = min(gap, remaining)
            quota_map[grow][population] = quota
            populations[population] += quota
            remaining -= quota
        placed.add(grow)
    if tuple(populations) != expected_populations:
        raise ModelError(
            f"population mismatch: {populations} != {expected_populations}"
        )
    resident_pass = max(
        range(POPULATIONS),
        key=lambda population: (populations[population], -population),
    )
    return GrowPassPlan(
        populations=tuple(populations),
        counts=tuple(sorted(counts_by_grow.items())),
        quotas=tuple(
            (grow, tuple(quota_map[grow])) for grow in sorted(quota_map)
        ),
        resident_pass=resident_pass,
        planning_operations=planning_operations,
    )


def heap_sort_in_place(
    records: list[Descriptor], key: Callable[[Descriptor], tuple[int, ...]]
) -> dict[str, int]:
    comparisons = 0
    swaps = 0

    def greater(left: int, right: int) -> bool:
        nonlocal comparisons
        comparisons += 1
        return key(records[left]) > key(records[right])

    def sift_down(root: int, end: int) -> None:
        nonlocal swaps
        while True:
            child = root * 2 + 1
            if child >= end:
                return
            if child + 1 < end and greater(child + 1, child):
                child += 1
            if not greater(child, root):
                return
            records[root], records[child] = records[child], records[root]
            swaps += 1
            root = child

    for root in range(len(records) // 2 - 1, -1, -1):
        sift_down(root, len(records))
    for end in range(len(records) - 1, 0, -1):
        records[0], records[end] = records[end], records[0]
        swaps += 1
        sift_down(0, end)
    return {"comparisons": comparisons, "swaps": swaps}


class LogicalLLCStore:
    """Fixed four-segment backing with explicit line-access accounting."""

    def __init__(self, run_counts: Sequence[int]):
        if len(run_counts) != POPULATIONS:
            raise ModelError("logical LLC store requires four runs")
        if any(
            count <= 0 or count > ACTIVE_DESCRIPTOR_LIMIT
            for count in run_counts
        ):
            raise ModelError("run count violates the descriptor bound")
        self.run_counts = tuple(run_counts)
        self.data = bytearray(POPULATIONS * RUN_STRIDE_BYTES)
        self.appended = [0] * POPULATIONS
        self.events: dict[str, int] = {}
        self.event_digest = hashlib.sha256()

    @staticmethod
    def run_base(run: int) -> int:
        return run * RUN_STRIDE_BYTES

    def _record_event(
        self, action: str, phase: str, run: int, line: int
    ) -> None:
        key = f"{phase}_{action}_lines"
        self.events[key] = self.events.get(key, 0) + 1
        offset = self.run_base(run) + line * LINE_BYTES
        content = bytes(self.data[offset : offset + LINE_BYTES])
        self.event_digest.update(
            f"{action}:{phase}:{run}:{line}:{BACKING_BASE + offset:x}:".encode(
                "ascii"
            )
        )
        self.event_digest.update(hashlib.sha256(content).digest())

    def append_unsorted(self, run: int, descriptor: Descriptor) -> None:
        index = self.appended[run]
        if index >= self.run_counts[run]:
            raise ModelError(f"run {run} classification append overflow")
        offset = self.run_base(run) + index * DESCRIPTOR_BYTES
        self.data[offset : offset + DESCRIPTOR_BYTES] = descriptor.pack()
        self.appended[run] += 1

    def finalize_unsorted(self, run: int) -> None:
        if self.appended[run] != self.run_counts[run]:
            raise ModelError(f"run {run} classification append is incomplete")
        for line in range(
            ceil_div(self.run_counts[run] * DESCRIPTOR_BYTES, LINE_BYTES)
        ):
            self._record_event("write", "classification", run, line)

    def write_sorted_run(
        self, run: int, records: Sequence[Descriptor]
    ) -> None:
        if len(records) != self.run_counts[run]:
            raise ModelError(f"run {run} sorted population mismatch")
        begin = self.run_base(run)
        end = begin + RUN_STRIDE_BYTES
        self.data[begin:end] = bytes(RUN_STRIDE_BYTES)
        for index, descriptor in enumerate(records):
            offset = begin + index * DESCRIPTOR_BYTES
            self.data[offset : offset + DESCRIPTOR_BYTES] = descriptor.pack()
        self.appended[run] = len(records)
        for line in range(
            ceil_div(len(records) * DESCRIPTOR_BYTES, LINE_BYTES)
        ):
            self._record_event("write", "sorted_run", run, line)

    def reader(self, run: int, phase: str) -> SequentialRunReader:
        return SequentialRunReader(self, run, phase)

    def event_summary(self) -> dict[str, object]:
        return {
            **dict(sorted(self.events.items())),
            "event_count": sum(self.events.values()),
            "event_sha256": self.event_digest.hexdigest(),
        }


class SequentialRunReader:
    """One 64-byte buffer plus at most five carry bytes for a six-byte run."""

    def __init__(self, store: LogicalLLCStore, run: int, phase: str):
        self.store = store
        self.run = run
        self.phase = phase
        self.count = store.run_counts[run]
        self.index = 0
        self.buffer_line = -1
        self.buffer = b""
        self.lines_read = 0
        self.max_carry_bytes = 0

    def _load_line(self, line: int) -> None:
        begin = self.store.run_base(self.run) + line * LINE_BYTES
        self.buffer = bytes(self.store.data[begin : begin + LINE_BYTES])
        self.buffer_line = line
        self.lines_read += 1
        self.store._record_event("read", self.phase, self.run, line)

    def next(self) -> Descriptor | None:
        if self.index == self.count:
            return None
        byte_offset = self.index * DESCRIPTOR_BYTES
        line = byte_offset // LINE_BYTES
        in_line = byte_offset % LINE_BYTES
        if self.buffer_line != line:
            self._load_line(line)
        available = LINE_BYTES - in_line
        if available >= DESCRIPTOR_BYTES:
            payload = self.buffer[in_line : in_line + DESCRIPTOR_BYTES]
        else:
            carry = self.buffer[in_line:]
            self.max_carry_bytes = max(self.max_carry_bytes, len(carry))
            self._load_line(line + 1)
            payload = carry + self.buffer[: DESCRIPTOR_BYTES - len(carry)]
        self.index += 1
        return Descriptor.unpack(payload)


def value_oracle(b_value: int) -> int:
    # A stable 64-bit payload with no dependence on issue order.
    return (
        (b_value * 0x9E3779B185EBCA87) ^ 0xD1B54A32D192ED03
    ) & 0xFFFF_FFFF_FFFF_FFFF


class IssueAccumulator:
    def __init__(self, trace: InputTrace):
        self.trace = trace
        self.output: list[int | None] = [None] * trace.count
        self.issue_digest = hashlib.sha256()
        self.placement_digest = hashlib.sha256()
        self.row_digest = hashlib.sha256()
        self.open_rows: dict[int, int] = {}
        self.unique_lines: set[int] = set()
        self.unique_bank_rows: set[tuple[int, int]] = set()
        self.a_line_requests = 0
        self.dram_row_activations = 0
        self.dram_row_hits = 0
        self.coalesced_descriptors = 0
        self.max_line_fanout = 0
        self._cluster_line: int | None = None
        self._cluster: list[Descriptor] = []

    def feed(self, descriptor: Descriptor) -> None:
        location = decode_descriptor(descriptor, self.trace.a_translation)
        if self._cluster_line is None:
            self._cluster_line = location.line_paddr
        if location.line_paddr != self._cluster_line:
            self._flush_cluster()
            self._cluster_line = location.line_paddr
        self._cluster.append(descriptor)

    def population_barrier(self) -> None:
        # The current/native4 comparators cannot keep an A response live across
        # independently retired 4K populations.
        self._flush_cluster()

    def _flush_cluster(self) -> None:
        if not self._cluster:
            self._cluster_line = None
            return
        first = decode_descriptor(self._cluster[0], self.trace.a_translation)
        if any(
            decode_descriptor(descriptor, self.trace.a_translation).line_paddr
            != first.line_paddr
            for descriptor in self._cluster
        ):
            raise ModelError("equal-line cluster contains multiple A lines")
        self.a_line_requests += 1
        self.unique_lines.add(first.line_paddr)
        bank_row = (first.native_slice, first.row)
        self.unique_bank_rows.add(bank_row)
        if self.open_rows.get(first.native_slice) != first.row:
            self.dram_row_activations += 1
            self.open_rows[first.native_slice] = first.row
        else:
            self.dram_row_hits += 1
        self.coalesced_descriptors += len(self._cluster) - 1
        self.max_line_fanout = max(self.max_line_fanout, len(self._cluster))
        self.issue_digest.update(first.line_paddr.to_bytes(8, "little"))
        self.row_digest.update(first.native_slice.to_bytes(1, "little"))
        self.row_digest.update(first.row.to_bytes(4, "little"))
        for descriptor in self._cluster:
            logical_i = descriptor.logical_i
            if logical_i >= self.trace.count:
                raise ModelError(f"logical i {logical_i} exceeds trace output")
            if self.output[logical_i] is not None:
                raise ModelError(
                    f"duplicate output reconstruction for i={logical_i}"
                )
            value = value_oracle(descriptor.b_value)
            self.output[logical_i] = value
            descriptor_location = decode_descriptor(
                descriptor, self.trace.a_translation
            )
            self.placement_digest.update(logical_i.to_bytes(2, "little"))
            self.placement_digest.update(
                first.line_paddr.to_bytes(8, "little")
            )
            self.placement_digest.update(
                descriptor_location.wid.to_bytes(1, "little")
            )
        self._cluster = []
        self._cluster_line = None

    def finish(self) -> dict[str, object]:
        self._flush_cluster()
        expected = [
            value_oracle(descriptor.b_value)
            for descriptor in self.trace.descriptors
        ]
        if self.output != expected:
            raise ModelError("exact logical-i output reconstruction failed")
        output_bytes = b"".join(
            int(value).to_bytes(8, "little") for value in expected
        )
        return {
            "descriptors": self.trace.count,
            "a_line_requests": self.a_line_requests,
            "unique_a_lines": len(self.unique_lines),
            "coalesced_descriptors": self.coalesced_descriptors,
            "max_equal_line_fanout": self.max_line_fanout,
            "dram_row_activations": self.dram_row_activations,
            "dram_row_hits": self.dram_row_hits,
            "unique_dram_bank_rows": len(self.unique_bank_rows),
            "dram_row_reactivations": (
                self.dram_row_activations - len(self.unique_bank_rows)
            ),
            "issue_sha256": self.issue_digest.hexdigest(),
            "placement_sha256": self.placement_digest.hexdigest(),
            "dram_row_sequence_sha256": self.row_digest.hexdigest(),
            "output_sha256": hashlib.sha256(output_bytes).hexdigest(),
            "exact_output_reconstruction": True,
        }


def _local_arm(
    trace: InputTrace,
    assignment: Callable[[Descriptor], int],
    name: str,
) -> dict[str, object]:
    accumulator = IssueAccumulator(trace)
    sort_totals = {"comparisons": 0, "swaps": 0}
    populations: list[int] = []
    active_hwm = 0
    for population in range(POPULATIONS):
        workspace = [
            descriptor
            for descriptor in trace.descriptors
            if assignment(descriptor) == population
        ]
        if not workspace or len(workspace) > ACTIVE_DESCRIPTOR_LIMIT:
            raise ModelError(
                f"{name} population {population} violates its bound"
            )
        populations.append(len(workspace))
        active_hwm = max(active_hwm, len(workspace))
        counts = heap_sort_in_place(
            workspace,
            lambda descriptor: descriptor_key(descriptor, trace.a_translation),
        )
        for counter in sort_totals:
            sort_totals[counter] += counts[counter]
        for descriptor in workspace:
            accumulator.feed(descriptor)
        accumulator.population_barrier()
    result = accumulator.finish()
    return {
        "policy": name,
        "populations": populations,
        "active_descriptor_high_water": active_hwm,
        "active_descriptor_limit": ACTIVE_DESCRIPTOR_LIMIT,
        "sort": sort_totals,
        "memory_behavior": result,
    }


def model_native4(trace: InputTrace) -> dict[str, object]:
    capacity = trace.population_capacity
    return _local_arm(
        trace,
        lambda descriptor: min(
            descriptor.logical_i // capacity, POPULATIONS - 1
        ),
        "four_contiguous_i_populations_row_local",
    )


def model_current_four_pass(
    trace: InputTrace, plan: GrowPassPlan
) -> dict[str, object]:
    assignments = {
        descriptor.logical_i: population
        for descriptor, population in plan.assignments(
            trace.descriptors, trace.a_translation
        )
    }
    result = _local_arm(
        trace,
        lambda descriptor: assignments[descriptor.logical_i],
        "current_16k_informed_four_pass_row_local",
    )
    external_records = trace.count - plan.populations[plan.resident_pass]
    external_valid_bytes = external_records * DESCRIPTOR_BYTES
    external_lines = sum(
        ceil_div(plan.populations[population] * DESCRIPTOR_BYTES, LINE_BYTES)
        for population in range(POPULATIONS)
        if population != plan.resident_pass
    )
    result["backing"] = {
        "resident_pass": plan.resident_pass,
        "external_records": external_records,
        "record_bytes": DESCRIPTOR_BYTES,
        "reserved_bytes": external_valid_bytes,
        "valid_bytes": external_valid_bytes,
        "line_writes": external_lines,
        "line_reads": external_lines,
        "line_bytes_each_direction": external_lines * LINE_BYTES,
    }
    result["passes"] = {
        "b_summary_scans": 1,
        "b_classification_scans": 1,
        "b_total_scans": 2,
        "sequential_row_local_populations": POPULATIONS,
    }
    return result


def model_native_global16(trace: InputTrace) -> dict[str, object]:
    ordered = sorted(
        trace.descriptors,
        key=lambda descriptor: descriptor_key(descriptor, trace.a_translation),
    )
    accumulator = IssueAccumulator(trace)
    for descriptor in ordered:
        accumulator.feed(descriptor)
    return {
        "policy": "native_global16_unbounded_ordering_oracle",
        "active_descriptor_high_water": trace.count,
        "candidate_bound_applicable": False,
        "memory_behavior": accumulator.finish(),
    }


def model_bounded_global_merge(
    trace: InputTrace, plan: GrowPassPlan
) -> dict[str, object]:
    store = LogicalLLCStore(plan.populations)
    resident = plan.resident_pass
    workspace: list[Descriptor] = []
    active_hwm = 0

    # One classification scan: only the selected resident population stays in
    # the 4K workspace; all other descriptors immediately enter LLC backing.
    for descriptor, population in plan.assignments(
        trace.descriptors, trace.a_translation
    ):
        if population == resident:
            workspace.append(descriptor)
            active_hwm = max(active_hwm, len(workspace))
            if len(workspace) > ACTIVE_DESCRIPTOR_LIMIT:
                raise ModelError("resident sort workspace exceeded 4096")
        else:
            store.append_unsorted(population, descriptor)
    for population in range(POPULATIONS):
        if population != resident:
            store.finalize_unsorted(population)
    if len(workspace) != plan.populations[resident]:
        raise ModelError("resident population is incomplete")

    sort_totals = {"comparisons": 0, "swaps": 0}

    def sort_and_accumulate(records: list[Descriptor]) -> None:
        counts = heap_sort_in_place(
            records,
            lambda descriptor: descriptor_key(descriptor, trace.a_translation),
        )
        for counter in sort_totals:
            sort_totals[counter] += counts[counter]

    sort_and_accumulate(workspace)
    store.write_sorted_run(resident, workspace)
    workspace = []

    sort_read_lines = 0
    max_sort_carry = 0
    for population in range(POPULATIONS):
        if population == resident:
            continue
        reader = store.reader(population, "sort_input")
        while True:
            descriptor = reader.next()
            if descriptor is None:
                break
            workspace.append(descriptor)
            active_hwm = max(active_hwm, len(workspace))
            if len(workspace) > ACTIVE_DESCRIPTOR_LIMIT:
                raise ModelError("external sort workspace exceeded 4096")
        sort_read_lines += reader.lines_read
        max_sort_carry = max(max_sort_carry, reader.max_carry_bytes)
        sort_and_accumulate(workspace)
        store.write_sorted_run(population, workspace)
        workspace = []

    # Four finite readers and four six-byte heads are the only descriptor-run
    # state live during the global merge.
    readers = [
        store.reader(population, "merge") for population in range(POPULATIONS)
    ]
    heads = [reader.next() for reader in readers]
    accumulator = IssueAccumulator(trace)
    merge_comparisons = 0
    merged_records = 0
    previous_key: tuple[int, int, int, int] | None = None
    while any(head is not None for head in heads):
        selected = -1
        selected_key: tuple[int, int, int, int] | None = None
        for population, head in enumerate(heads):
            if head is None:
                continue
            key = descriptor_key(head, trace.a_translation)
            if selected == -1:
                selected = population
                selected_key = key
                continue
            merge_comparisons += 1
            if key < selected_key:  # type: ignore[operator]
                selected = population
                selected_key = key
        if selected == -1 or selected_key is None:
            raise ModelError("four-head merge lost its selected run")
        if previous_key is not None and selected_key < previous_key:
            raise ModelError("merged run is not globally nondecreasing")
        descriptor = heads[selected]
        if descriptor is None:
            raise ModelError("selected merge head is empty")
        accumulator.feed(descriptor)
        merged_records += 1
        previous_key = selected_key
        heads[selected] = readers[selected].next()
    if merged_records != trace.count:
        raise ModelError("merge lost or duplicated descriptors")
    memory_behavior = accumulator.finish()
    merge_read_lines = sum(reader.lines_read for reader in readers)
    max_merge_carry = max(reader.max_carry_bytes for reader in readers)

    valid_bytes = trace.count * DESCRIPTOR_BYTES
    final_run_lines = sum(
        ceil_div(population * DESCRIPTOR_BYTES, LINE_BYTES)
        for population in plan.populations
    )
    classification_lines = sum(
        ceil_div(plan.populations[population] * DESCRIPTOR_BYTES, LINE_BYTES)
        for population in range(POPULATIONS)
        if population != resident
    )
    sorted_write_lines = final_run_lines
    logical_write_lines = classification_lines + sorted_write_lines
    logical_read_lines = sort_read_lines + merge_read_lines
    eventual_dirty_writeback_lines = final_run_lines
    coherent_line_transfers = (
        logical_write_lines
        + logical_read_lines
        + eventual_dirty_writeback_lines
    )
    if active_hwm > ACTIVE_DESCRIPTOR_LIMIT:
        raise ModelError("candidate active descriptor high-water is illegal")
    if merge_read_lines != final_run_lines:
        raise ModelError(
            "merge did not read every final run line exactly once"
        )
    if classification_lines != 1_152 and trace.count == LOGICAL_LIMIT:
        raise ModelError(
            "full trace does not preserve the current 1152-line spool"
        )
    b_scan_bytes = trace.count * B_WORD_BYTES
    b_lines_per_scan = ceil_div(
        (0 if trace.b_base is None else trace.b_base % LINE_BYTES)
        + b_scan_bytes,
        LINE_BYTES,
    )
    return {
        "policy": "four_llc_backed_row_local_runs_global_four_head_merge",
        "plan": {
            "populations": list(plan.populations),
            "resident_pass": resident,
            "grow_records": len(plan.counts),
            "grow_counts": {str(grow): count for grow, count in plan.counts},
            "grow_quotas": {
                str(grow): list(quotas) for grow, quotas in plan.quotas
            },
            "planning_operations": plan.planning_operations,
            "assignment_state_is_bounded_quota_and_ordinal_only": True,
        },
        "bounds": {
            "active_descriptor_limit": ACTIVE_DESCRIPTOR_LIMIT,
            "active_descriptor_high_water": active_hwm,
            "run_count": POPULATIONS,
            "run_population_limit": ACTIVE_DESCRIPTOR_LIMIT,
            "run_populations": list(plan.populations),
            "merge_head_descriptors": POPULATIONS,
            "merge_line_buffers": POPULATIONS,
            "merge_line_buffer_bytes": POPULATIONS * LINE_BYTES,
            "merge_carry_bytes": POPULATIONS * (DESCRIPTOR_BYTES - 1),
            "merge_head_record_bytes": POPULATIONS * DESCRIPTOR_BYTES,
            "merge_cursor_count_bytes": 13,
            "merge_valid_bytes": 1,
            "merge_control_total_bytes": (
                POPULATIONS * LINE_BYTES
                + POPULATIONS * (DESCRIPTOR_BYTES - 1)
                + POPULATIONS * DESCRIPTOR_BYTES
                + 13
                + 1
            ),
            "classification_staging_bytes": (
                (POPULATIONS - 1) * (LINE_BYTES + DESCRIPTOR_BYTES - 1)
            ),
            "sort_workspace_payload_bytes": (
                max(plan.populations) * DESCRIPTOR_BYTES
            ),
            "unbounded_descriptor_mapping_state": False,
        },
        "record_format": {
            "bytes": DESCRIPTOR_BYTES,
            "used_bits": 46,
            "logical_i_bits": 14,
            "b_value_bits": 32,
            "reserved_bits": 2,
            "a_line_slice_row_wid_redecoded_by_existing_address_translation": True,
        },
        "sort": {
            **sort_totals,
            "algorithm": "in_place_binary_heap_sort",
            "max_sort_carry_bytes": max_sort_carry,
        },
        "merge": {
            "heads": POPULATIONS,
            "records": merged_records,
            "comparisons": merge_comparisons,
            "passes": 1,
            "read_lines": merge_read_lines,
            "read_line_bytes": merge_read_lines * LINE_BYTES,
            "max_reader_carry_bytes": max_merge_carry,
            "key": [
                "row_table_slice_order",
                "dram_row",
                "physical_a_line",
                "logical_i",
            ],
        },
        "passes": {
            "b_summary_scans": 1,
            "b_classification_scans": 1,
            "b_total_scans": 2,
            "external_sort_read_passes": POPULATIONS - 1,
            "sorted_run_write_passes": POPULATIONS,
            "global_merge_passes": 1,
        },
        "traffic": {
            "b_summary_scan_bytes": b_scan_bytes,
            "b_classification_scan_bytes": b_scan_bytes,
            "b_total_scan_bytes": 2 * b_scan_bytes,
            "b_lines_per_scan": b_lines_per_scan,
            "b_total_line_reads": 2 * b_lines_per_scan,
            "backing_reserved_bytes": POPULATIONS * RUN_STRIDE_BYTES,
            "backing_reserved_lines": POPULATIONS
            * RUN_STRIDE_BYTES
            // LINE_BYTES,
            "backing_valid_bytes": valid_bytes,
            "final_run_valid_lines": final_run_lines,
            "classification_append_records": trace.count
            - plan.populations[resident],
            "classification_append_valid_bytes": (
                trace.count - plan.populations[resident]
            )
            * DESCRIPTOR_BYTES,
            "classification_append_line_writes": classification_lines,
            "sort_input_read_records": trace.count
            - plan.populations[resident],
            "sort_input_read_lines": sort_read_lines,
            "sorted_run_write_records": trace.count,
            "sorted_run_write_lines": sorted_write_lines,
            "merge_read_records": trace.count,
            "merge_read_lines": merge_read_lines,
            "logical_llc_write_lines": logical_write_lines,
            "logical_llc_read_lines": logical_read_lines,
            "eventual_dirty_writeback_lines": eventual_dirty_writeback_lines,
            "coherent_line_transfers": coherent_line_transfers,
            "coherent_line_bytes": coherent_line_transfers * LINE_BYTES,
        },
        "logical_llc_store_events": store.event_summary(),
        "memory_behavior": memory_behavior,
    }


def line_for(row: int, native_slice: int, column: int) -> int:
    if not 0 <= native_slice < SLICES or not 0 <= column < (
        1 << COLUMN_LINE_BITS
    ):
        raise ModelError("synthetic physical coordinate is out of range")
    bank_group = native_slice // 4
    bank = native_slice % 4
    line_index = (
        column
        | (bank_group << COLUMN_LINE_BITS)
        | (bank << (COLUMN_LINE_BITS + BANK_GROUP_BITS))
        | (row << (COLUMN_LINE_BITS + BANK_GROUP_BITS + BANK_BITS))
    )
    return line_index * LINE_BYTES


def make_synthetic_trace(
    name: str,
    repeated_lines: bool,
    adversarial_order: bool,
) -> InputTrace:
    # The skew forces the current first-fit plan to place/split rows across
    # multiple populations: 60/50/40/30/20/20/20/16 = 256, capacity 64.
    row_counts = [60, 50, 40, 30, 20, 20, 20, 16]
    tokens: list[tuple[int, int, int]] = []
    for row, count in enumerate(row_counts):
        for ordinal in range(count):
            if repeated_lines and row >= 6:
                column = ordinal % 4
            elif repeated_lines and adversarial_order:
                column = ordinal % 8
            else:
                column = ordinal
            tokens.append((row, column, ordinal % 8))
    if adversarial_order:
        # 73 is coprime to 256, so this is a deterministic full permutation.
        tokens = [
            tokens[(index * 73) % len(tokens)] for index in range(len(tokens))
        ]
    descriptors = []
    physical_pages: dict[int, int] = {}
    for logical_i, (row, column, wid) in enumerate(tokens):
        virtual_page = row
        physical_pages[virtual_page] = line_for(row, 0, 0)
        b_value = virtual_page * (4096 // A_WORD_BYTES) + column * 8 + wid
        descriptors.append(Descriptor(logical_i=logical_i, b_value=b_value))
    trace = InputTrace(
        name=name,
        descriptors=tuple(descriptors),
        a_translation=AddressTranslation(
            base_page_offset=0,
            physical_pages=tuple(sorted(physical_pages.items())),
        ),
        b_base=0x1000_0020,
        provenance={
            "kind": "deterministic_synthetic",
            "row_counts": row_counts,
            "repeated_lines": repeated_lines,
            "adversarial_order": adversarial_order,
        },
    )
    validate_trace(trace)
    return trace


def _cross_population_counts(
    trace: InputTrace, plan: GrowPassPlan
) -> dict[str, int]:
    line_populations: dict[int, set[int]] = {}
    row_populations: dict[tuple[int, int], set[int]] = {}
    for descriptor, population in plan.assignments(
        trace.descriptors, trace.a_translation
    ):
        location = decode_descriptor(descriptor, trace.a_translation)
        line_populations.setdefault(location.line_paddr, set()).add(population)
        row_populations.setdefault(
            (location.native_slice, location.row), set()
        ).add(population)
    return {
        "a_lines_in_multiple_populations": sum(
            len(populations) > 1 for populations in line_populations.values()
        ),
        "dram_bank_rows_in_multiple_populations": sum(
            len(populations) > 1 for populations in row_populations.values()
        ),
    }


def compare_trace(trace: InputTrace) -> dict[str, object]:
    validate_trace(trace)
    plan = build_grow_pass_plan(trace)
    native4 = model_native4(trace)
    current = model_current_four_pass(trace, plan)
    candidate = model_bounded_global_merge(trace, plan)
    global16 = model_native_global16(trace)
    current_memory = current["memory_behavior"]
    candidate_memory = candidate["memory_behavior"]
    global_memory = global16["memory_behavior"]
    assert isinstance(current_memory, dict)
    assert isinstance(candidate_memory, dict)
    assert isinstance(global_memory, dict)
    if candidate_memory["issue_sha256"] != global_memory["issue_sha256"]:
        raise ModelError(
            "candidate does not reproduce the global16 issue order"
        )
    if candidate_memory["output_sha256"] != global_memory["output_sha256"]:
        raise ModelError("candidate does not reproduce the global16 output")
    if candidate_memory["a_line_requests"] > current_memory["a_line_requests"]:
        raise ModelError(
            "candidate regresses current-four-pass A-line requests"
        )
    if (
        candidate_memory["dram_row_activations"]
        > current_memory["dram_row_activations"]
    ):
        raise ModelError("candidate regresses current-four-pass DRAM rows")
    return {
        "trace": {
            "name": trace.name,
            "records": trace.count,
            "a_translation": trace.a_translation.summary(),
            "b_base": None if trace.b_base is None else hex(trace.b_base),
            "descriptor_sha256": sha256_json(
                [
                    [descriptor.logical_i, descriptor.b_value]
                    for descriptor in trace.descriptors
                ]
            ),
            "provenance": trace.provenance,
        },
        "cross_population_opportunities": _cross_population_counts(
            trace, plan
        ),
        "arms": {
            "native4": native4,
            "current_four_pass": current,
            "bounded_global_merge": candidate,
            "native_global16_oracle": global16,
        },
        "comparison": {
            "candidate_minus_current_a_line_requests": (
                candidate_memory["a_line_requests"]
                - current_memory["a_line_requests"]
            ),
            "candidate_minus_current_dram_row_activations": (
                candidate_memory["dram_row_activations"]
                - current_memory["dram_row_activations"]
            ),
            "candidate_matches_global16_issue_order": True,
            "candidate_matches_global16_a_line_requests": (
                candidate_memory["a_line_requests"]
                == global_memory["a_line_requests"]
            ),
            "candidate_matches_global16_dram_row_activations": (
                candidate_memory["dram_row_activations"]
                == global_memory["dram_row_activations"]
            ),
            "all_outputs_exact": True,
            "candidate_within_4096_active_descriptors": (
                candidate["bounds"]["active_descriptor_high_water"]
                <= ACTIVE_DESCRIPTOR_LIMIT
            ),
        },
    }


MEASURED_CURRENT_CONTEXT = {
    "provenance": (
        "exact comparator values supplied by the coordination objective on "
        "2026-08-10; no candidate gem5 timing is modeled or inferred"
    ),
    "native4": {
        "simTicks": 59_267_176,
        "descriptor_fill_cycles": 5_169_508,
        "a_request_cycles": 52_038_128,
    },
    "bounded_paged4": {
        "simTicks": 60_913_869,
        "descriptor_fill_cycles": 22_029_879,
        "a_request_cycles": 30_625_172,
        "external_descriptor_records": 12_288,
        "external_descriptor_bytes": 73_728,
        "descriptor_line_writes": 1_152,
        "descriptor_line_reads": 1_152,
    },
}


def build_results(traces: Sequence[InputTrace]) -> dict[str, object]:
    comparisons = [compare_trace(trace) for trace in traces]
    aggregate_current_a = 0
    aggregate_candidate_a = 0
    aggregate_current_rows = 0
    aggregate_candidate_rows = 0
    no_regressions = True
    exact_and_bounded = True
    for comparison in comparisons:
        arms = comparison["arms"]
        current = arms["current_four_pass"]["memory_behavior"]
        candidate = arms["bounded_global_merge"]["memory_behavior"]
        aggregate_current_a += current["a_line_requests"]
        aggregate_candidate_a += candidate["a_line_requests"]
        aggregate_current_rows += current["dram_row_activations"]
        aggregate_candidate_rows += candidate["dram_row_activations"]
        no_regressions &= (
            candidate["a_line_requests"] <= current["a_line_requests"]
            and candidate["dram_row_activations"]
            <= current["dram_row_activations"]
        )
        exact_and_bounded &= (
            comparison["comparison"]["all_outputs_exact"]
            and comparison["comparison"][
                "candidate_within_4096_active_descriptors"
            ]
        )
    strict_a = aggregate_candidate_a < aggregate_current_a
    strict_rows = aggregate_candidate_rows < aggregate_current_rows
    propose_vertical_slice = (
        no_regressions and exact_and_bounded and strict_a and strict_rows
    )
    return {
        "schema": RESULT_SCHEMA,
        "source_commit": SOURCE_COMMIT,
        "model_boundary": {
            "candidate_gem5_timing_measured": False,
            "simTicks_claim": False,
            "model_executes_functional_and_traffic_mechanisms": True,
        },
        "source_semantics": {
            "offset_entry": ["logical_i", "word_id", "next_link"],
            "row_entry": [
                "physical_a_line",
                "offset_chain_head",
                "offset_chain_tail",
            ],
            "row_slice_group": ["dram_grow", "physical_a_lines"],
            "issue_order": [
                "row_table_slice_order",
                "dram_row",
                "physical_a_line",
                "logical_i",
            ],
            "result_restoration": "logical_i plus word_id decoded from A base and B value",
        },
        "configuration": {
            "logical_descriptor_limit": LOGICAL_LIMIT,
            "active_descriptor_limit": ACTIVE_DESCRIPTOR_LIMIT,
            "populations": POPULATIONS,
            "descriptor_bytes": DESCRIPTOR_BYTES,
            "line_bytes": LINE_BYTES,
            "run_stride_bytes": RUN_STRIDE_BYTES,
            "row_table_slice_order": list(ROW_TABLE_SLICE_ORDER),
            "address_mapping": "DDR4_8Gb_x8_1ch_1rank_RoBaRaCoCh",
        },
        "measured_current_context": MEASURED_CURRENT_CONTEXT,
        "traces": comparisons,
        "aggregate_gate": {
            "traces": len(comparisons),
            "current_four_pass_a_line_requests": aggregate_current_a,
            "bounded_global_merge_a_line_requests": aggregate_candidate_a,
            "current_four_pass_dram_row_activations": aggregate_current_rows,
            "bounded_global_merge_dram_row_activations": aggregate_candidate_rows,
            "no_per_trace_a_line_or_dram_row_regression": no_regressions,
            "strict_aggregate_a_line_improvement": strict_a,
            "strict_aggregate_dram_row_improvement": strict_rows,
            "exact_output_and_bounds": exact_and_bounded,
            "propose_live_gem5_vertical_slice": propose_vertical_slice,
            "promotion_claim": False,
            "reason": (
                "structural model gate only; a matched, correctness-gated gem5 "
                "slice must measure candidate timing and mechanism counters"
            ),
        },
    }


def default_synthetic_traces() -> list[InputTrace]:
    return [
        make_synthetic_trace(
            "skewed_rows_with_cross_run_repeated_lines",
            repeated_lines=True,
            adversarial_order=False,
        ),
        make_synthetic_trace(
            "skewed_rows_unique_lines",
            repeated_lines=False,
            adversarial_order=False,
        ),
        make_synthetic_trace(
            "adversarial_ingress_with_repeated_lines",
            repeated_lines=True,
            adversarial_order=True,
        ),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--trace-sha256")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if bool(args.trace) != bool(args.trace_sha256):
        raise SystemExit(
            "--trace and --trace-sha256 must be provided together"
        )
    traces = default_synthetic_traces()
    if args.trace:
        traces.append(load_physical_trace(args.trace, args.trace_sha256))
    result = build_results(traces)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
