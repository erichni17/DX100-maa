#!/usr/bin/env python3
"""Trace-level model of one honest 4K-bounded four-pass gather policy.

The candidate scans the externally resident B vector exactly four times.  Pass
``p`` admits indices whose A address has low decoded-row bits equal to ``p``.
XRAGE's A line base is grounded by a frozen address trace; FLAG is evaluated as
explicit alignment scenarios because its frozen digest logs omit the A base.
The policy retains at most 4096 destination descriptors and at most 64 row
identities in each of 32 Row-Table slices.  Capacity or per-slice row skew
causes a bounded subepoch drain; it never causes an item to be dropped.

This is a structural model.  It does not model cycles, caches, energy, area, or
gem5.  Full-trace lists and exact-once sets exist only in the source-memory and
observer objects.  CandidatePolicy retains only the bounded active subepoch and
finite response/ACK ownership tables.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from dataclasses import (
    asdict,
    dataclass,
    field,
)
from pathlib import Path
from typing import (
    Any,
    Iterable,
    Iterator,
    Sequence,
)

MAX_LOGICAL_ELEMENTS = 16_384
ACTIVE_DESCRIPTOR_CAPACITY = 4_096
PARTITIONS = 4
B_WORD_BYTES = 4
A_ELEMENT_BYTES = 8
CACHE_LINE_BYTES = 64

# Frozen DDR4_8Gb_x8/RoBaRaCoCh structural scope used by the source campaigns:
# 2 system channels, 128 transaction-sized columns, 4 bank groups, 4 banks.
CHANNELS = 2
COLUMN_LINE_BITS = 7
BANK_GROUPS = 4
BANKS_PER_GROUP = 4
ROW_TABLE_SLICES = CHANNELS * BANK_GROUPS * BANKS_PER_GROUP
ROWS_PER_SLICE = 64

RESPONSE_OWNER_SLOTS = 128
ACK_OWNER_SLOTS = 64

XRAGE_PATH = Path(
    "/data1/nier/DX100/experiments/inputs/xrage_gather0_20k.json"
)
XRAGE_SHA256 = (
    "7cb86c456e11f32ea4664510c43b519af6fac3e3bfa1bc86f95f330ca230c136"
)
XRAGE_SOURCE_COUNT = 20_000
XRAGE_GROUND_TRACE_PATH = Path(
    "/data1/nier/dx100-runs/2026-07-29-xrage-issue-trace-20k-0bab8d9/"
    "fused16/run/xrage-debug.log"
)
XRAGE_GROUND_TRACE_SHA256 = (
    "608aa7608a2641abaf4d9a068fe7f47fcf2ce58eebce0d58ee216a322dfe78cd"
)
XRAGE_BASE_LINE = 65_025

# FLAG's frozen digest logs do not expose the runtime A base.  These are
# explicit 64-byte-aligned sensitivity probes, not claims about its physical
# placement and not an exhaustive alignment proof.
FLAG_BASE_LINE_SCENARIOS = (
    0,
    64,
    4_096,
)

FLAG_MANIFEST_PATH = Path(
    "/data1/nier/worktrees/DX100-transparent-virtual-tile-20260725/"
    "benchmarks/spatter/tests/test-data/lanl/manifest.json"
)
FLAG_MANIFEST_SHA256 = (
    "9e1e8e2d7ce445194d1eea24bffa8a1b67b2843829ff8af283a0960e460263e9"
)
FLAG_EXPECTED_IDS = (
    "flag_static_2d_001.fp_00_gather",
    "flag_static_2d_001.fp_01_gather",
    "flag_static_2d_001.fp_02_gather",
    "flag_static_2d_001.fp_03_gather",
    "flag_static_2d_001_00_gather",
    "flag_static_2d_001_01_gather",
    "flag_static_2d_001_02_gather",
    "flag_static_2d_001_03_gather",
    "flag_static_2d_001_04_gather",
    "flag_static_2d_001.nonfp_00_gather",
    "flag_static_2d_001.nonfp_01_gather",
    "flag_static_2d_001.nonfp_02_gather",
    "flag_static_2d_001.nonfp_03_gather",
    "flag_static_2d_001.nonfp_04_gather",
)


class AdmissionError(ValueError):
    """The frozen source corpus is incomplete, duplicated, or changed."""


class ProtocolError(RuntimeError):
    """A response/ACK token violated finite ownership."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class TraceSource:
    source_id: str
    path: Path
    sha256: str
    source_count: int
    pattern: tuple[int, ...] = field(repr=False, compare=False)

    @property
    def tile_count(self) -> int:
        return math.ceil(self.source_count / MAX_LOGICAL_ELEMENTS)


def _read_single_gather(path: Path) -> tuple[int, ...]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AdmissionError(
            f"cannot read gather source {path}: {error}"
        ) from error
    if not isinstance(document, list) or len(document) != 1:
        raise AdmissionError(
            f"{path}: expected exactly one JSON configuration"
        )
    row = document[0]
    if str(row.get("kernel", "")).lower() != "gather":
        raise AdmissionError(f"{path}: expected a Gather kernel")
    pattern = row.get("pattern")
    if not isinstance(pattern, list) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in pattern
    ):
        raise AdmissionError(
            f"{path}: pattern must contain nonnegative integers"
        )
    if row.get("count") != 1:
        raise AdmissionError(f"{path}: only count=1 is in structural scope")
    return tuple(pattern)


def validate_source_records(
    records: Sequence[tuple[str, Path, str, int]], expected_count: int
) -> None:
    """Reject missing or duplicated source identities before reading traces."""

    if len(records) != expected_count:
        raise AdmissionError(
            f"expected {expected_count} sources, found {len(records)}"
        )
    for field_name, values in (
        ("source ID", [record[0] for record in records]),
        ("path", [str(record[1].resolve()) for record in records]),
        ("digest", [record[2] for record in records]),
    ):
        if len(values) != len(set(values)):
            raise AdmissionError(f"duplicate {field_name} in source set")
    if any(count <= 0 for _, _, _, count in records):
        raise AdmissionError("source counts must be positive")


def load_frozen_corpus(
    xrage_path: Path = XRAGE_PATH,
    flag_manifest_path: Path = FLAG_MANIFEST_PATH,
) -> tuple[TraceSource, ...]:
    """Load exactly the frozen XRAGE source and 14 FLAG allowlist members."""

    xrage_path = xrage_path.resolve()
    flag_manifest_path = flag_manifest_path.resolve()
    if sha256_file(xrage_path) != XRAGE_SHA256:
        raise AdmissionError(f"XRAGE digest mismatch: {xrage_path}")
    if sha256_file(flag_manifest_path) != FLAG_MANIFEST_SHA256:
        raise AdmissionError(
            f"FLAG manifest digest mismatch: {flag_manifest_path}"
        )

    manifest = json.loads(flag_manifest_path.read_text(encoding="utf-8"))
    rows = [
        row
        for row in manifest.get("configurations", [])
        if str(row.get("kernel", "")).lower() == "gather"
    ]
    if tuple(row.get("id") for row in rows) != FLAG_EXPECTED_IDS:
        raise AdmissionError(
            "FLAG gather IDs/order differ from the frozen allowlist"
        )

    flag_records = [
        (
            str(row["id"]),
            (flag_manifest_path.parent / row["input"]).resolve(),
            str(row["input_sha256"]),
            int(row["pattern_length"]),
        )
        for row in rows
    ]
    validate_source_records(flag_records, expected_count=14)

    xrage_pattern = _read_single_gather(xrage_path)
    if len(xrage_pattern) != XRAGE_SOURCE_COUNT:
        raise AdmissionError("XRAGE source count mismatch")
    _validate_xrage_address_grounding(xrage_pattern)
    sources = [
        TraceSource(
            "xrage_gather0_20k",
            xrage_path,
            XRAGE_SHA256,
            XRAGE_SOURCE_COUNT,
            xrage_pattern,
        )
    ]
    for source_id, path, digest, source_count in flag_records:
        if sha256_file(path) != digest:
            raise AdmissionError(f"FLAG digest mismatch: {path}")
        pattern = _read_single_gather(path)
        if len(pattern) != source_count:
            raise AdmissionError(f"FLAG source count mismatch: {source_id}")
        sources.append(
            TraceSource(source_id, path, digest, source_count, pattern)
        )

    validate_source_records(
        [
            (source.source_id, source.path, source.sha256, source.source_count)
            for source in sources
        ],
        expected_count=15,
    )
    return tuple(sources)


def _validate_xrage_address_grounding(pattern: Sequence[int]) -> None:
    """Bind the XRAGE normalized indices to its frozen physical line trace."""

    path = XRAGE_GROUND_TRACE_PATH.resolve()
    if sha256_file(path) != XRAGE_GROUND_TRACE_SHA256:
        raise AdmissionError(f"XRAGE grounding trace digest mismatch: {path}")
    groups: dict[int, list[int]] = {}
    order: list[int] = []
    record = re.compile(r"instruction_tick=(\d+).* addr=(0x[0-9a-f]+) ")
    for line in path.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        match = record.search(line)
        if match is None:
            continue
        tick = int(match.group(1))
        if tick not in groups:
            groups[tick] = []
            order.append(tick)
        groups[tick].append(int(match.group(2), 16) // CACHE_LINE_BYTES)
    actual = [set(groups[tick]) for tick in order]
    expected = [
        {decode_index(index, base_line=XRAGE_BASE_LINE).line for index in tile}
        for tile in tiles(pattern)
    ]
    if actual != expected:
        raise AdmissionError(
            "XRAGE physical line grounding no longer matches B"
        )


@dataclass(frozen=True)
class Address:
    index: int
    byte_address: int
    line: int
    word: int
    channel: int
    column: int
    bank_group: int
    bank: int
    row: int
    row_slice: int

    @property
    def row_key(self) -> tuple[int, int]:
        return (self.row_slice, self.row)

    @property
    def partition(self) -> int:
        # Fixed hardware address bits; no data-dependent global bounds/prepass.
        return self.row & (PARTITIONS - 1)


def decode_index(index: int, base_line: int = 0) -> Address:
    """Decode A[index] under one explicit 64-byte-aligned base scenario."""

    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError("A index must be a nonnegative integer")
    if (
        isinstance(base_line, bool)
        or not isinstance(base_line, int)
        or base_line < 0
    ):
        raise ValueError("base line must be a nonnegative integer")
    byte_address = base_line * CACHE_LINE_BYTES + index * A_ELEMENT_BYTES
    line = byte_address // CACHE_LINE_BYTES
    word = (byte_address % CACHE_LINE_BYTES) // A_ELEMENT_BYTES
    channel = line & (CHANNELS - 1)
    column = (line >> 1) & ((1 << COLUMN_LINE_BITS) - 1)
    bank_group = (line >> (1 + COLUMN_LINE_BITS)) & (BANK_GROUPS - 1)
    bank = (line >> (1 + COLUMN_LINE_BITS + int(math.log2(BANK_GROUPS)))) & (
        BANKS_PER_GROUP - 1
    )
    row = line >> (
        1
        + COLUMN_LINE_BITS
        + int(math.log2(BANK_GROUPS))
        + int(math.log2(BANKS_PER_GROUP))
    )
    row_slice = (channel * BANK_GROUPS + bank_group) * BANKS_PER_GROUP + bank
    return Address(
        index,
        byte_address,
        line,
        word,
        channel,
        column,
        bank_group,
        bank,
        row,
        row_slice,
    )


@dataclass
class Descriptor:
    destination: int
    address: Address
    next_slot: int = -1
    response_ready: bool = False
    destination_owned: bool = True
    complete: bool = False


@dataclass(frozen=True)
class OwnershipToken:
    operation_generation: int
    subepoch_generation: int
    slot: int
    key: int


class FiniteOwnerTable:
    """Reusable bounded tags, not an oracle/global issue serial."""

    def __init__(self, capacity: int, operation_generation: int) -> None:
        if capacity <= 0 or not 0 <= operation_generation < (1 << 16):
            raise ValueError("invalid owner-table capacity or generation")
        self.capacity = capacity
        self.operation_generation = operation_generation
        self.subepoch_generation = 0
        self._entries: list[tuple[int, Any] | None] = [None] * capacity
        self._live_count = 0
        self.max_live = 0

    @property
    def live(self) -> int:
        return self._live_count

    def allocate(self, key: int, payload: Any) -> OwnershipToken:
        for slot, entry in enumerate(self._entries):
            if entry is None:
                self._entries[slot] = (key, payload)
                self._live_count += 1
                self.max_live = max(self.max_live, self._live_count)
                return OwnershipToken(
                    self.operation_generation,
                    self.subepoch_generation,
                    slot,
                    key,
                )
        raise ProtocolError("finite owner table is full")

    def complete(self, token: OwnershipToken) -> Any:
        if (
            token.operation_generation != self.operation_generation
            or token.subepoch_generation != self.subepoch_generation
            or not 0 <= token.slot < self.capacity
        ):
            raise ProtocolError("stale or invalid owner token")
        entry = self._entries[token.slot]
        if entry is None or entry[0] != token.key:
            raise ProtocolError("unowned, duplicate, or mismapped token")
        self._entries[token.slot] = None
        self._live_count -= 1
        return entry[1]

    def advance_subepoch(self) -> None:
        if self.live:
            raise ProtocolError("cannot advance with live owners")
        if self.subepoch_generation == MAX_LOGICAL_ELEMENTS:
            raise ProtocolError("subepoch generation bound exceeded")
        self.subepoch_generation += 1


@dataclass
class StructuralResult:
    policy: str
    destinations: int = 0
    a_requests: int = 0
    unique_lines: int = 0
    unique_rows: int = 0
    row_transitions: int = 0
    same_row_successors: int = 0
    drains: int = 0
    b_scan_words: int = 0
    b_scan_bytes: int = 0
    max_active_descriptors: int = 0
    max_active_lines: int = 0
    max_active_rows: int = 0
    max_rows_in_slice: int = 0
    max_response_owners: int = 0
    max_ack_owners: int = 0
    capacity_drains: int = 0
    row_table_drains: int = 0
    partition_counts: tuple[int, ...] = ()


class StructuralObserver:
    """Unbounded validation observer, explicitly outside candidate state."""

    def __init__(self, pattern: Sequence[int], policy: str) -> None:
        self.pattern = pattern
        self.result = StructuralResult(
            policy=policy, destinations=len(pattern)
        )
        self._retired: list[int | None] = [None] * len(pattern)
        self._unique_lines: set[int] = set()
        self._unique_rows: set[tuple[int, int]] = set()
        self._previous_row: tuple[int, int] | None = None

    def issue(self, address: Address) -> None:
        result = self.result
        result.a_requests += 1
        self._unique_lines.add(address.line)
        self._unique_rows.add(address.row_key)
        if self._previous_row is not None:
            if self._previous_row == address.row_key:
                result.same_row_successors += 1
            else:
                result.row_transitions += 1
        self._previous_row = address.row_key

    def retire(self, destination: int, source_index: int) -> None:
        if not 0 <= destination < len(self.pattern):
            raise ProtocolError("destination outside logical tile")
        if self._retired[destination] is not None:
            raise ProtocolError("destination retired more than once")
        if source_index != self.pattern[destination]:
            raise ProtocolError("response mapped to the wrong destination")
        self._retired[destination] = source_index

    def finish(self) -> StructuralResult:
        if any(value is None for value in self._retired):
            raise ProtocolError("one or more destinations were not retired")
        self.result.unique_lines = len(self._unique_lines)
        self.result.unique_rows = len(self._unique_rows)
        return self.result


def _address_order(descriptor: Descriptor) -> tuple[int, int, int, int]:
    address = descriptor.address
    return (
        address.row_slice,
        address.row,
        address.line,
        descriptor.destination,
    )


class CandidatePolicy:
    """Four-pass fixed-row-bit policy with only bounded candidate state."""

    def __init__(
        self, operation_generation: int = 0, base_line: int = 0
    ) -> None:
        self.operation_generation = operation_generation
        self.base_line = base_line
        self.active: list[Descriptor] = []
        self.active_lines: set[int] = set()
        self.rows_by_slice: list[set[int]] = [
            set() for _ in range(ROW_TABLE_SLICES)
        ]
        self.active_row_count = 0
        self.response_owners = FiniteOwnerTable(
            RESPONSE_OWNER_SLOTS, operation_generation
        )
        self.ack_owners = FiniteOwnerTable(
            ACK_OWNER_SLOTS, operation_generation
        )
        self.max_active_descriptors = 0
        self.max_active_lines = 0
        self.max_active_rows = 0
        self.max_rows_in_slice = 0
        self.capacity_drains = 0
        self.row_table_drains = 0
        self.drains = 0

    def _record_maxima(self) -> None:
        self.max_active_descriptors = max(
            self.max_active_descriptors, len(self.active)
        )
        self.max_active_lines = max(
            self.max_active_lines, len(self.active_lines)
        )
        self.max_active_rows = max(self.max_active_rows, self.active_row_count)
        self.max_rows_in_slice = max(
            self.max_rows_in_slice,
            max(len(rows) for rows in self.rows_by_slice),
        )

    def _admit(self, destination: int, source_index: int) -> str | None:
        address = decode_index(source_index, self.base_line)
        if len(self.active) == ACTIVE_DESCRIPTOR_CAPACITY:
            return "descriptor_capacity"
        slice_rows = self.rows_by_slice[address.row_slice]
        if address.row not in slice_rows and len(slice_rows) == ROWS_PER_SLICE:
            return "row_table_capacity"
        is_new_row = address.row not in slice_rows
        self.active.append(Descriptor(destination, address))
        self.active_lines.add(address.line)
        slice_rows.add(address.row)
        self.active_row_count += int(is_new_row)
        self._record_maxima()
        return None

    def _drain(self, observer: StructuralObserver) -> None:
        if not self.active:
            return
        ordered_slots = sorted(
            range(len(self.active)),
            key=lambda slot: _address_order(self.active[slot]),
        )
        cursor = 0
        while cursor < len(ordered_slots):
            first_slot = ordered_slots[cursor]
            line = self.active[first_slot].address.line
            group: list[int] = []
            while (
                cursor < len(ordered_slots)
                and self.active[ordered_slots[cursor]].address.line == line
            ):
                group.append(ordered_slots[cursor])
                cursor += 1
            for left, right in zip(group, group[1:]):
                self.active[left].next_slot = right
            response = self.response_owners.allocate(line, tuple(group))
            observer.issue(self.active[first_slot].address)
            response_slots = self.response_owners.complete(response)
            for slot in response_slots:
                descriptor = self.active[slot]
                descriptor.response_ready = True
                ack = self.ack_owners.allocate(descriptor.destination, slot)
                ack_slot = self.ack_owners.complete(ack)
                if ack_slot != slot or not descriptor.destination_owned:
                    raise ProtocolError("destination ACK ownership mismatch")
                observer.retire(
                    descriptor.destination, descriptor.address.index
                )
                descriptor.destination_owned = False
                descriptor.complete = True
        if any(
            not descriptor.complete or descriptor.destination_owned
            for descriptor in self.active
        ):
            raise ProtocolError(
                "drain ended with incomplete descriptor ownership"
            )
        self.active.clear()
        self.active_lines.clear()
        for rows in self.rows_by_slice:
            rows.clear()
        self.active_row_count = 0
        self.drains += 1
        self.response_owners.advance_subepoch()
        self.ack_owners.advance_subepoch()

    def run(self, pattern: Sequence[int]) -> StructuralResult:
        if not 0 <= len(pattern) <= MAX_LOGICAL_ELEMENTS:
            raise ValueError("logical tile length must be in [0, 16384]")
        observer = StructuralObserver(pattern, "bounded_rescan4")
        partition_counts = [0] * PARTITIONS
        for partition in range(PARTITIONS):
            for destination, source_index in enumerate(pattern):
                address = decode_index(source_index, self.base_line)
                if address.partition != partition:
                    continue
                partition_counts[partition] += 1
                reason = self._admit(destination, source_index)
                if reason is not None:
                    if reason == "descriptor_capacity":
                        self.capacity_drains += 1
                    else:
                        self.row_table_drains += 1
                    self._drain(observer)
                    retry_reason = self._admit(destination, source_index)
                    if retry_reason is not None:
                        raise ProtocolError(
                            "descriptor cannot fit an empty subepoch"
                        )
            self._drain(observer)
        result = observer.finish()
        result.drains = self.drains
        result.b_scan_words = PARTITIONS * len(pattern)
        result.b_scan_bytes = result.b_scan_words * B_WORD_BYTES
        result.max_active_descriptors = self.max_active_descriptors
        result.max_active_lines = self.max_active_lines
        result.max_active_rows = self.max_active_rows
        result.max_rows_in_slice = self.max_rows_in_slice
        result.max_response_owners = self.response_owners.max_live
        result.max_ack_owners = self.ack_owners.max_live
        result.capacity_drains = self.capacity_drains
        result.row_table_drains = self.row_table_drains
        result.partition_counts = tuple(partition_counts)
        return result

    def bounded_container_sizes(self) -> dict[str, int]:
        """Expose candidate storage for the hidden-state adversarial test."""

        return {
            "active": len(self.active),
            "active_lines": len(self.active_lines),
            "row_sets": sum(len(rows) for rows in self.rows_by_slice),
            "row_set_count": len(self.rows_by_slice),
            "response_entries": len(self.response_owners._entries),
            "ack_entries": len(self.ack_owners._entries),
        }


def _reference_drain(
    active: list[Descriptor], observer: StructuralObserver
) -> None:
    """Reference-only drain; it does not use CandidatePolicy or owner tables."""

    ordered = sorted(active, key=_address_order)
    previous_line: int | None = None
    for descriptor in ordered:
        if descriptor.address.line != previous_line:
            observer.issue(descriptor.address)
            previous_line = descriptor.address.line
        observer.retire(descriptor.destination, descriptor.address.index)


def run_native16(
    pattern: Sequence[int], base_line: int = 0
) -> StructuralResult:
    """Independent 16K admission/reference scheduler in the stated scope."""

    if not 0 <= len(pattern) <= MAX_LOGICAL_ELEMENTS:
        raise ValueError("logical tile length must be in [0, 16384]")
    observer = StructuralObserver(pattern, "native16")
    active: list[Descriptor] = []
    active_lines: set[int] = set()
    active_row_count = 0
    rows_by_slice = [set() for _ in range(ROW_TABLE_SLICES)]
    drains = row_drains = 0
    maxima = [0, 0, 0, 0]
    for destination, source_index in enumerate(pattern):
        address = decode_index(source_index, base_line)
        slice_rows = rows_by_slice[address.row_slice]
        if address.row not in slice_rows and len(slice_rows) == ROWS_PER_SLICE:
            _reference_drain(active, observer)
            active.clear()
            active_lines.clear()
            for rows in rows_by_slice:
                rows.clear()
            active_row_count = 0
            drains += 1
            row_drains += 1
            slice_rows = rows_by_slice[address.row_slice]
        is_new_row = address.row not in slice_rows
        active.append(Descriptor(destination, address))
        active_lines.add(address.line)
        slice_rows.add(address.row)
        active_row_count += int(is_new_row)
        maxima[0] = max(maxima[0], len(active))
        maxima[1] = max(maxima[1], len(active_lines))
        maxima[2] = max(maxima[2], active_row_count)
        maxima[3] = max(maxima[3], len(slice_rows))
    if active:
        _reference_drain(active, observer)
        drains += 1
    result = observer.finish()
    result.drains = drains
    result.b_scan_words = len(pattern)
    result.b_scan_bytes = len(pattern) * B_WORD_BYTES
    result.max_active_descriptors = maxima[0]
    result.max_active_lines = maxima[1]
    result.max_active_rows = maxima[2]
    result.max_rows_in_slice = maxima[3]
    result.row_table_drains = row_drains
    return result


def run_native4k_x4(
    pattern: Sequence[int], base_line: int = 0
) -> StructuralResult:
    """Independent four sequential 4K-epoch reference scheduler."""

    if not 0 <= len(pattern) <= MAX_LOGICAL_ELEMENTS:
        raise ValueError("logical tile length must be in [0, 16384]")
    observer = StructuralObserver(pattern, "native4k_x4")
    drains = row_drains = 0
    maxima = [0, 0, 0, 0]
    for start in range(0, len(pattern), ACTIVE_DESCRIPTOR_CAPACITY):
        stop = min(start + ACTIVE_DESCRIPTOR_CAPACITY, len(pattern))
        active: list[Descriptor] = []
        active_lines: set[int] = set()
        active_row_count = 0
        rows_by_slice = [set() for _ in range(ROW_TABLE_SLICES)]
        for destination in range(start, stop):
            address = decode_index(pattern[destination], base_line)
            slice_rows = rows_by_slice[address.row_slice]
            if (
                address.row not in slice_rows
                and len(slice_rows) == ROWS_PER_SLICE
            ):
                _reference_drain(active, observer)
                active.clear()
                active_lines.clear()
                for rows in rows_by_slice:
                    rows.clear()
                active_row_count = 0
                drains += 1
                row_drains += 1
                slice_rows = rows_by_slice[address.row_slice]
            is_new_row = address.row not in slice_rows
            active.append(Descriptor(destination, address))
            active_lines.add(address.line)
            slice_rows.add(address.row)
            active_row_count += int(is_new_row)
            maxima[0] = max(maxima[0], len(active))
            maxima[1] = max(maxima[1], len(active_lines))
            maxima[2] = max(maxima[2], active_row_count)
            maxima[3] = max(maxima[3], len(slice_rows))
        if active:
            _reference_drain(active, observer)
            drains += 1
    result = observer.finish()
    result.drains = drains
    result.b_scan_words = len(pattern)
    result.b_scan_bytes = len(pattern) * B_WORD_BYTES
    result.max_active_descriptors = maxima[0]
    result.max_active_lines = maxima[1]
    result.max_active_rows = maxima[2]
    result.max_rows_in_slice = maxima[3]
    result.row_table_drains = row_drains
    return result


def tiles(pattern: Sequence[int]) -> Iterator[Sequence[int]]:
    for start in range(0, len(pattern), MAX_LOGICAL_ELEMENTS):
        yield pattern[start : start + MAX_LOGICAL_ELEMENTS]


def aggregate_results(
    policy: str, results: Iterable[StructuralResult]
) -> StructuralResult:
    aggregate = StructuralResult(policy=policy)
    for result in results:
        for name in (
            "destinations",
            "a_requests",
            "row_transitions",
            "same_row_successors",
            "drains",
            "b_scan_words",
            "b_scan_bytes",
            "capacity_drains",
            "row_table_drains",
        ):
            setattr(
                aggregate,
                name,
                getattr(aggregate, name) + getattr(result, name),
            )
        for name in (
            "max_active_descriptors",
            "max_active_lines",
            "max_active_rows",
            "max_rows_in_slice",
            "max_response_owners",
            "max_ack_owners",
        ):
            setattr(
                aggregate,
                name,
                max(getattr(aggregate, name), getattr(result, name)),
            )
        # Sources are chunked into independent logical tiles.  Summing tile
        # unique counts is the correct per-operation structural work scope.
        aggregate.unique_lines += result.unique_lines
        aggregate.unique_rows += result.unique_rows
    return aggregate


def metadata_ledger() -> dict[str, dict[str, int]]:
    """Packed logical metadata accounting; no Python object-size claims."""

    fields = {
        "active_offset_descriptors": (ACTIVE_DESCRIPTOR_CAPACITY, 33),
        "active_line_entries": (ACTIVE_DESCRIPTOR_CAPACITY, 34),
        "row_table_entries": (ROW_TABLE_SLICES * ROWS_PER_SLICE, 60),
        "response_identity_entries": (RESPONSE_OWNER_SLOTS, 103),
        "ack_identity_entries": (ACK_OWNER_SLOTS, 46),
        "b_word_latch": (1, 32),
        "finite_control": (1, 66),
    }
    ledger: dict[str, dict[str, int]] = {}
    for name, (count, bits_each) in fields.items():
        bits = count * bits_each
        ledger[name] = {
            "count": count,
            "bits_each": bits_each,
            "bits": bits,
            "packed_bytes": math.ceil(bits / 8),
        }
    total_bits = sum(row["bits"] for row in ledger.values())
    ledger["total"] = {
        "count": 1,
        "bits_each": total_bits,
        "bits": total_bits,
        "packed_bytes": math.ceil(total_bits / 8),
    }
    return ledger


def _percent(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else 100.0 * numerator / denominator


def _gap_recovered(
    native16: int, native4: int, candidate: int
) -> float | None:
    """Fraction of native4's excess structural count removed by candidate."""

    return _percent(native4 - candidate, native4 - native16)


def evaluate_source(source: TraceSource, base_line: int) -> dict[str, Any]:
    native16_parts: list[StructuralResult] = []
    native4_parts: list[StructuralResult] = []
    candidate_parts: list[StructuralResult] = []
    for tile_number, tile in enumerate(tiles(source.pattern)):
        native16_parts.append(run_native16(tile, base_line))
        native4_parts.append(run_native4k_x4(tile, base_line))
        candidate_parts.append(
            CandidatePolicy(tile_number, base_line).run(tile)
        )
    native16 = aggregate_results("native16", native16_parts)
    native4 = aggregate_results("native4k_x4", native4_parts)
    candidate = aggregate_results("bounded_rescan4", candidate_parts)
    return {
        "source": {
            "id": source.source_id,
            "path": str(source.path),
            "sha256": source.sha256,
            "source_count": source.source_count,
            "logical_tiles": source.tile_count,
            "base_line_scenario": base_line,
            "base_grounding": (
                "frozen_xrage_physical_line_trace"
                if source.source_id == "xrage_gather0_20k"
                else "explicit_flag_sensitivity_scenario"
            ),
        },
        "native16": asdict(native16),
        "native4k_x4": asdict(native4),
        "bounded_rescan4": asdict(candidate),
        "comparison": {
            "candidate_a_request_overhead_vs_native16_pct": _percent(
                candidate.a_requests - native16.a_requests,
                native16.a_requests,
            ),
            "candidate_a_request_delta_vs_native4k_pct": _percent(
                candidate.a_requests - native4.a_requests,
                native4.a_requests,
            ),
            "candidate_a_request_gap_recovered_vs_native4k_pct": _gap_recovered(
                native16.a_requests,
                native4.a_requests,
                candidate.a_requests,
            ),
            "candidate_row_transition_gap_recovered_vs_native4k_pct": (
                _gap_recovered(
                    native16.row_transitions,
                    native4.row_transitions,
                    candidate.row_transitions,
                )
            ),
        },
    }


def _total_results(source_results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, dict[str, int]] = {}
    for policy in ("native16", "native4k_x4", "bounded_rescan4"):
        totals[policy] = {
            name: sum(int(row[policy][name]) for row in source_results)
            for name in (
                "destinations",
                "a_requests",
                "unique_lines",
                "unique_rows",
                "row_transitions",
                "same_row_successors",
                "drains",
                "b_scan_words",
                "b_scan_bytes",
                "capacity_drains",
                "row_table_drains",
            )
        }
    n16 = totals["native16"]
    n4 = totals["native4k_x4"]
    candidate = totals["bounded_rescan4"]
    return {
        "totals": totals,
        "total_comparison": {
            "candidate_a_request_overhead_vs_native16_pct": _percent(
                candidate["a_requests"] - n16["a_requests"], n16["a_requests"]
            ),
            "candidate_a_request_delta_vs_native4k_pct": _percent(
                candidate["a_requests"] - n4["a_requests"], n4["a_requests"]
            ),
            "candidate_a_request_gap_recovered_vs_native4k_pct": _gap_recovered(
                n16["a_requests"],
                n4["a_requests"],
                candidate["a_requests"],
            ),
            "candidate_row_transition_gap_recovered_vs_native4k_pct": (
                _gap_recovered(
                    n16["row_transitions"],
                    n4["row_transitions"],
                    candidate["row_transitions"],
                )
            ),
        },
    }


def evaluate_corpus(sources: Sequence[TraceSource]) -> dict[str, Any]:
    if len(sources) != 15:
        raise AdmissionError(
            "evaluation requires XRAGE plus all 14 FLAG sources"
        )
    xrage = [
        source for source in sources if source.source_id.startswith("xrage_")
    ]
    flags = [
        source for source in sources if source.source_id.startswith("flag_")
    ]
    if len(xrage) != 1 or len(flags) != 14:
        raise AdmissionError("evaluation source classes are incomplete")
    source_results = [evaluate_source(xrage[0], XRAGE_BASE_LINE)] + [
        evaluate_source(source, FLAG_BASE_LINE_SCENARIOS[0])
        for source in flags
    ]
    primary_totals = _total_results(source_results)
    flag_sensitivity = []
    for base_line in FLAG_BASE_LINE_SCENARIOS:
        scenario_results = (
            source_results[1:]
            if base_line == FLAG_BASE_LINE_SCENARIOS[0]
            else [evaluate_source(source, base_line) for source in flags]
        )
        flag_sensitivity.append(
            {
                "base_line": base_line,
                **_total_results(scenario_results),
            }
        )
    return {
        "schema": 1,
        "scope": {
            "max_logical_elements": MAX_LOGICAL_ELEMENTS,
            "active_descriptor_capacity": ACTIVE_DESCRIPTOR_CAPACITY,
            "partitions": PARTITIONS,
            "partition_key": "normalized_A_row_low_2_bits",
            "b_word_bytes": B_WORD_BYTES,
            "a_element_bytes": A_ELEMENT_BYTES,
            "cache_line_bytes": CACHE_LINE_BYTES,
            "row_table_slices": ROW_TABLE_SLICES,
            "rows_per_slice": ROWS_PER_SLICE,
            "xrage_base_line": XRAGE_BASE_LINE,
            "flag_base_line_scenarios": FLAG_BASE_LINE_SCENARIOS,
        },
        "metadata_ledger": metadata_ledger(),
        "sources": source_results,
        **primary_totals,
        "flag_alignment_sensitivity": flag_sensitivity,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--indent", type=int, default=2, help="deterministic JSON indentation"
    )
    args = parser.parse_args()
    result = evaluate_corpus(load_frozen_corpus())
    print(json.dumps(result, indent=args.indent, sort_keys=True))


if __name__ == "__main__":
    main()
