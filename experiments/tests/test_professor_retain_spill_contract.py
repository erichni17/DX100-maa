#!/usr/bin/env python3
"""Executable contract for the professor retain/spill design analysis.

This is a dependency-free, integer/event model.  It deliberately has no gem5
imports, clocks, latency model, or performance assertions.  A passing test is
correctness evidence for this contract only, never cycle or area evidence.
"""

from __future__ import annotations

import heapq
import struct
import unittest
from collections import OrderedDict
from dataclasses import dataclass
from typing import (
    Iterable,
    Iterator,
    Sequence,
)

N = 16_384
K = 4_096
INDEX_BYTES = 4
VALUE_BYTES = 8
LINE_BYTES = 64
RUNS = N // K


def ceil_div(value: int, divisor: int) -> int:
    if value < 0 or divisor <= 0:
        raise ValueError("ceil_div requires value >= 0 and divisor > 0")
    return (value + divisor - 1) // divisor


def packed_bytes(entries: int, bits_per_entry: int) -> int:
    if entries < 0 or bits_per_entry < 0:
        raise ValueError("packed dimensions cannot be negative")
    return ceil_div(entries * bits_per_entry, 8)


def lower_bound_ledger() -> dict[str, int]:
    """Return the downstream replay packed-state and semantic-byte ledger.

    The active/backing descriptor's meaningful fields are: 64-bit aligned A
    line, 14-bit original i, 3-bit FP64 word-in-line, 14-bit native issue
    serial, and one live bit.  That is exactly 96 packed bits.  The coherent
    wire image is intentionally 16 B so records are self-delimiting.  The
    generator of native issue serials is an analysis oracle and is not charged
    here, so this function must not be read as a sufficient exact-hardware
    total.
    """

    descriptor_bits = 64 + 14 + 3 + 14 + 1
    active_descriptors = packed_bytes(K, descriptor_bits)
    run_line_buffers = RUNS * LINE_BYTES
    # line, generation, transaction; run; 0..1023 line index; state
    run_buffer_tags = packed_bytes(RUNS, 64 + 64 + 64 + 2 + 10 + 3)
    # Merge heads are views into the four line buffers, not extra descriptors.
    merge_heads = 0
    run_cursors_counts = packed_bytes(1, RUNS * (13 + 13))
    global_control = packed_bytes(1, 64 + 64 + 15 + 3)
    # heap length/root/child and phase.  Swaps reserve one of the K descriptor
    # slots rather than allocating a K+1st producer descriptor.
    sort_control = packed_bytes(1, 13 + 12 + 13 + 3)
    completion_bitmap = packed_bytes(N, 1)
    page_scoreboard = packed_bytes(1, RUNS * 13 + RUNS + RUNS)
    # One deliberately serial A owner: 64-B payload plus exact identity and
    # bounded group cursor/count.
    a_response_owner = LINE_BYTES + packed_bytes(1, 64 + 64 + 64 + 15 + 15 + 2)
    # One deliberately serial C-line owner with byte-valid/dirty masks.
    c_line_owner = LINE_BYTES + packed_bytes(1, 64 + 64 + 64 + 8 + 8 + 3)
    on_chip = sum(
        (
            active_descriptors,
            run_line_buffers,
            run_buffer_tags,
            merge_heads,
            run_cursors_counts,
            global_control,
            sort_control,
            completion_bitmap,
            page_scoreboard,
            a_response_owner,
            c_line_owner,
        )
    )
    spilled = N - K
    return {
        "descriptor_meaningful_bits": descriptor_bits,
        "active_descriptor_bytes": active_descriptors,
        "run_line_buffer_bytes": run_line_buffers,
        "run_buffer_tag_bytes": run_buffer_tags,
        "merge_head_bytes": merge_heads,
        "run_cursor_count_bytes": run_cursors_counts,
        "global_control_bytes": global_control,
        "sort_control_bytes": sort_control,
        "completion_bitmap_bytes": completion_bitmap,
        "page_scoreboard_bytes": page_scoreboard,
        "a_response_owner_bytes": a_response_owner,
        "c_line_owner_bytes": c_line_owner,
        "downstream_replay_on_chip_bytes": on_chip,
        "wire_record_bytes": struct.calcsize("<QHHBB2x"),
        "spill_only_backing_footprint_bytes": spilled * 16,
        "spill_only_backing_read_write_bytes": spilled * 16 * 2,
        "full_run_backing_footprint_bytes": N * 16,
        "full_run_backing_read_write_bytes": N * 16 * 2,
        "one_b_scan_bytes": N * INDEX_BYTES,
        "four_b_scan_bytes": RUNS * N * INDEX_BYTES,
        "common_result_payload_bytes": N * VALUE_BYTES,
        "common_result_lines": ceil_div(N * VALUE_BYTES, LINE_BYTES),
        "two_slot_spd_payload_bytes": 2 * K * VALUE_BYTES,
    }


@dataclass(frozen=True, order=True)
class Descriptor:
    i: int
    slice_id: int
    grow: int
    line: int
    wid: int = 0

    def validate(self) -> None:
        if not (0 <= self.i < N):
            raise ValueError("logical i is outside the 16K operation")
        if min(self.slice_id, self.grow, self.line, self.wid) < 0:
            raise ValueError("descriptor fields cannot be negative")
        if self.wid >= LINE_BYTES // VALUE_BYTES:
            raise ValueError("FP64 word offset is outside a cache line")
        if self.line % LINE_BYTES:
            raise ValueError("A line must be 64-byte aligned")


@dataclass(frozen=True)
class SourceIssue:
    slice_id: int
    grow: int
    line: int
    destinations: tuple[tuple[int, int], ...]


def row_table_slice_order(
    physical: tuple[int, int, int, int],
    slice_org: tuple[int, int, int, int],
) -> list[int]:
    """Mirror IndirectAccess's bank,bg,rank,channel loop and flattening.

    Tuple order is channel, rank, bank-group, bank.  The returned permutation
    deduplicates modulo-folded physical banks exactly as the native constructor.
    """

    channels, ranks, bankgroups, banks = physical
    sch, srank, sbg, sbank = slice_org
    if min(*physical, *slice_org) <= 0:
        raise ValueError("organization dimensions must be positive")
    result: list[int] = []
    for bank in range(banks):
        for bankgroup in range(bankgroups):
            for rank in range(ranks):
                for channel in range(channels):
                    index = channel % sch
                    index = index * srank + rank % srank
                    index = index * sbg + bankgroup % sbg
                    index = index * sbank + bank % sbank
                    if index not in result:
                        result.append(index)
    expected = sch * srank * sbg * sbank
    if len(result) != expected:
        raise ValueError("slice organization is not covered by physical banks")
    return result


class NativeEpoch:
    """Structural model of native first-insertion Row/Offset behavior.

    It models complete fill/forced-drain epochs and not cycle-level response
    timing or response-driven refill.  Therefore it is an ordering oracle for
    the stated structural schedule, not a universal gem5 trace oracle.
    """

    def __init__(
        self,
        slice_order: Sequence[int],
        rows_per_slice: int,
        lines_per_row: int,
    ) -> None:
        if not slice_order or len(set(slice_order)) != len(slice_order):
            raise ValueError("slice_order must be a nonempty permutation")
        if rows_per_slice <= 0 or lines_per_row <= 0:
            raise ValueError("RowTable capacities must be positive")
        self.slice_order = tuple(slice_order)
        self.rows_per_slice = rows_per_slice
        self.lines_per_row = lines_per_row
        # Each row is [grow, OrderedDict[line, list[Descriptor]]].
        self.rows: dict[int, list[list[object]]] = {
            slice_id: [] for slice_id in slice_order
        }
        self.offset_occupancy = 0

    def empty(self) -> bool:
        return self.offset_occupancy == 0

    def insert(self, descriptor: Descriptor) -> bool:
        descriptor.validate()
        if descriptor.slice_id not in self.rows:
            raise ValueError(
                "descriptor names a slice outside the permutation"
            )
        rows = self.rows[descriptor.slice_id]
        # Native step 1: find the already present (grow,line) in row-slot order.
        for grow, lines in rows:
            if grow == descriptor.grow and descriptor.line in lines:
                lines[descriptor.line].append(descriptor)
                self.offset_occupancy += 1
                return True
        # Native step 2: first same-grow row with a free line column.
        for grow, lines in rows:
            if grow == descriptor.grow and len(lines) < self.lines_per_row:
                lines[descriptor.line] = [descriptor]
                self.offset_occupancy += 1
                return True
        # Native step 3: first free row slot.
        if len(rows) == self.rows_per_slice:
            return False
        lines: OrderedDict[int, list[Descriptor]] = OrderedDict()
        lines[descriptor.line] = [descriptor]
        rows.append([descriptor.grow, lines])
        self.offset_occupancy += 1
        return True

    def _local_issues(self, slice_id: int) -> list[SourceIssue]:
        rows = self.rows[slice_id]
        consumed: set[int] = set()
        local: list[SourceIssue] = []
        # find_next_grow_addr picks the first still-valid row.  All rows with
        # that grow are then drained in row-slot order, line insertion order.
        for row_id, (grow, _lines) in enumerate(rows):
            if row_id in consumed:
                continue
            for peer_id, (peer_grow, peer_lines) in enumerate(rows):
                if peer_id in consumed or peer_grow != grow:
                    continue
                for line, descriptors in peer_lines.items():
                    local.append(
                        SourceIssue(
                            slice_id,
                            grow,
                            line,
                            tuple((item.i, item.wid) for item in descriptors),
                        )
                    )
                consumed.add(peer_id)
        return local

    def drain(self) -> list[SourceIssue]:
        local = {
            slice_id: iter(self._local_issues(slice_id))
            for slice_id in self.slice_order
        }
        heads: dict[int, SourceIssue] = {}
        result: list[SourceIssue] = []
        # Build takes at most one local head from each slice on each traversal.
        while True:
            progressed = False
            for slice_id in self.slice_order:
                if slice_id not in heads:
                    try:
                        heads[slice_id] = next(local[slice_id])
                    except StopIteration:
                        continue
                result.append(heads.pop(slice_id))
                progressed = True
            if not progressed:
                break
        return result


def native_issue_trace(
    descriptors: Sequence[Descriptor],
    offset_capacity: int,
    slice_order: Sequence[int],
    rows_per_slice: int = 64,
    lines_per_row: int = 8,
) -> list[SourceIssue]:
    """Build structural drain epochs and retry the insertion forcing a drain."""

    if offset_capacity <= 0:
        raise ValueError("offset_capacity must be positive")
    epoch = NativeEpoch(slice_order, rows_per_slice, lines_per_row)
    result: list[SourceIssue] = []
    for descriptor in descriptors:
        if epoch.offset_occupancy == offset_capacity:
            result.extend(epoch.drain())
            epoch = NativeEpoch(slice_order, rows_per_slice, lines_per_row)
        if not epoch.insert(descriptor):
            if epoch.empty():
                raise AssertionError("descriptor cannot fit an empty RowTable")
            result.extend(epoch.drain())
            epoch = NativeEpoch(slice_order, rows_per_slice, lines_per_row)
            if not epoch.insert(descriptor):
                raise AssertionError("failed insertion was not retryable")
    if not epoch.empty():
        result.extend(epoch.drain())
    return result


def chunk_draining_greedy(
    descriptors: Sequence[Descriptor],
    capacity: int,
    slice_order: Sequence[int],
) -> list[SourceIssue]:
    """The tempting future-blind policy: drain each full admitted chunk."""

    result: list[SourceIssue] = []
    for start in range(0, len(descriptors), capacity):
        result.extend(
            native_issue_trace(
                descriptors[start : start + capacity],
                capacity,
                slice_order,
                rows_per_slice=capacity,
                lines_per_row=capacity,
            )
        )
    return result


@dataclass(frozen=True, order=True)
class RunRecord:
    issue_serial: int
    i: int
    line: int
    wid: int


def oracle_materialize_native_records(
    descriptors: Sequence[Descriptor], issues: Sequence[SourceIssue]
) -> list[RunRecord]:
    """Analysis oracle: label records from an already-built native trace.

    This is deliberately not a candidate bounded implementation.  Producing
    ``issues`` already solved the full native ordering problem.  Tests use this
    helper only for downstream immutable-run merge and lifecycle validation;
    its serial-derivation state/work is not charged by lower_bound_ledger().
    """

    expected = {(item.i, item.wid, item.line) for item in descriptors}
    records: list[RunRecord] = []
    for serial, issue in enumerate(issues):
        for i, wid in issue.destinations:
            records.append(RunRecord(serial, i, issue.line, wid))
    observed = {(item.i, item.wid, item.line) for item in records}
    if observed != expected or len(records) != len(descriptors):
        raise AssertionError(
            "native record materialization lost or duplicated i"
        )
    return records


def immutable_runs(
    records: Sequence[RunRecord], chunk: int
) -> list[tuple[RunRecord, ...]]:
    if chunk <= 0:
        raise ValueError("chunk must be positive")
    return [
        tuple(sorted(records[start : start + chunk]))
        for start in range(0, len(records), chunk)
    ]


def checked_merge(
    runs: Sequence[Sequence[RunRecord]], expected_records: int
) -> Iterator[RunRecord]:
    """Merge immutable runs and fail closed on truncation/duplication/order."""

    if expected_records < 0:
        raise ValueError("expected_records cannot be negative")
    heap: list[tuple[RunRecord, int, int]] = []
    for run_id, run in enumerate(runs):
        if any(run[pos] > run[pos + 1] for pos in range(len(run) - 1)):
            raise AssertionError("run is not immutable sorted input")
        if run:
            heapq.heappush(heap, (run[0], run_id, 0))
    seen: set[int] = set()
    count = 0
    previous: RunRecord | None = None
    while heap:
        record, run_id, position = heapq.heappop(heap)
        if previous is not None and record < previous:
            raise AssertionError("merge order regressed")
        if record.i in seen:
            raise AssertionError("duplicate logical i in immutable runs")
        seen.add(record.i)
        previous = record
        count += 1
        yield record
        next_position = position + 1
        if next_position < len(runs[run_id]):
            heapq.heappush(
                heap, (runs[run_id][next_position], run_id, next_position)
            )
    if count != expected_records:
        raise AssertionError(
            f"iterator exhausted after {count}, expected {expected_records}"
        )


def serial_range_spool(
    records: Sequence[RunRecord], capacity: int
) -> Iterator[tuple[RunRecord, ...]]:
    """A bounded exact alternative using a replayable descriptor/B image.

    Each pass selects a finite native-serial range.  It is correct but may
    rescan the immutable input many times; the capacity assertion is the point.
    """

    if capacity <= 0:
        raise ValueError("capacity must be positive")
    max_serial = max((record.issue_serial for record in records), default=-1)
    start = 0
    emitted: set[int] = set()
    while start <= max_serial:
        selected = tuple(
            sorted(
                record
                for record in records
                if start <= record.issue_serial < start + capacity
            )
        )
        if len(selected) > capacity:
            # Equal-line fanout can exceed K records.  An exact implementation
            # streams that group from backing under one A response instead of
            # pretending all destinations are resident descriptors.
            raise AssertionError(
                "serial range exceeds finite descriptor spool"
            )
        for record in selected:
            if record.i in emitted:
                raise AssertionError("spool emitted an i twice")
            emitted.add(record.i)
        yield selected
        start += capacity
    if len(emitted) != len(records):
        raise AssertionError("spool missed a record")


@dataclass(frozen=True)
class Transaction:
    generation: int
    serial: int
    kind: str
    line: int


class Lifecycle:
    """Finite fail-closed ownership/ACK model for one logical gather."""

    def __init__(self, logical_elements: int, capacity: int) -> None:
        if logical_elements <= 0 or capacity <= 0:
            raise ValueError("lifecycle dimensions must be positive")
        self.n = logical_elements
        self.capacity = capacity
        self.generation = 1
        self.next_serial = 0
        self.active_descriptors = 0
        self.backing_writes: dict[Transaction, bool] = {}
        self.backing_reads: dict[Transaction, bool] = {}
        self.frozen = False
        self.a_pending: dict[Transaction, tuple[int, ...]] = {}
        self.a_accepted: set[Transaction] = set()
        self.completed_i: set[int] = set()
        self.c_writes: dict[Transaction, bool] = {}
        self.page_counts = [0] * ceil_div(logical_elements, K)

    def _transaction(self, kind: str, line: int) -> Transaction:
        transaction = Transaction(
            self.generation, self.next_serial, kind, line
        )
        self.next_serial += 1
        return transaction

    def reserve_descriptors(self, count: int) -> None:
        if count < 0 or self.active_descriptors + count > self.capacity:
            raise AssertionError("active descriptor capacity exceeded")
        self.active_descriptors += count

    def release_descriptors(self, count: int) -> None:
        if count < 0 or count > self.active_descriptors:
            raise AssertionError("descriptor release is not conserved")
        self.active_descriptors -= count

    def write_backing_line(self, line: int) -> Transaction:
        if self.frozen:
            raise AssertionError("immutable run modified after freeze")
        if any(
            item.line == line and not acked
            for item, acked in self.backing_writes.items()
        ):
            raise AssertionError("run buffer reused before matching write ACK")
        transaction = self._transaction("run-write", line)
        self.backing_writes[transaction] = False
        return transaction

    def ack_backing(self, transaction: Transaction) -> None:
        if transaction.generation != self.generation:
            raise AssertionError("stale backing ACK")
        if transaction not in self.backing_writes:
            raise AssertionError("unknown backing ACK")
        if self.backing_writes[transaction]:
            raise AssertionError("duplicate backing ACK")
        self.backing_writes[transaction] = True

    def freeze_runs(self) -> None:
        if not self.backing_writes or not all(self.backing_writes.values()):
            raise AssertionError("run freeze before matching write ACKs")
        self.frozen = True

    def read_backing_line(self, line: int) -> Transaction:
        if not self.frozen:
            raise AssertionError("run read before durable freeze")
        if self.backing_reads:
            raise AssertionError("bounded run-read buffer unavailable")
        transaction = self._transaction("run-read", line)
        self.backing_reads[transaction] = False
        return transaction

    def retry_backing_read(self, transaction: Transaction) -> Transaction:
        if (
            transaction not in self.backing_reads
            or self.backing_reads[transaction]
        ):
            raise AssertionError("retry has no matching unaccepted run read")
        return transaction

    def accept_backing_read(self, transaction: Transaction) -> None:
        if (
            transaction not in self.backing_reads
            or self.backing_reads[transaction]
        ):
            raise AssertionError(
                "run-read acceptance is missing or duplicated"
            )
        self.backing_reads[transaction] = True

    def respond_backing_read(
        self, transaction: Transaction, line: int
    ) -> None:
        if transaction.generation != self.generation:
            raise AssertionError("stale run-read response")
        if (
            transaction not in self.backing_reads
            or not self.backing_reads[transaction]
        ):
            raise AssertionError("run-read response has no accepted owner")
        if transaction.line != line:
            raise AssertionError(
                "run-read response remapped to the wrong line"
            )
        del self.backing_reads[transaction]

    def issue_a(self, line: int, destinations: Sequence[int]) -> Transaction:
        if not self.frozen:
            raise AssertionError("A issue before durable run freeze")
        if not destinations or len(self.a_pending) >= 1:
            raise AssertionError("bounded A response owner unavailable")
        if len(set(destinations)) != len(destinations):
            raise AssertionError("duplicate destination in one A owner")
        if any(
            i < 0 or i >= self.n or i in self.completed_i for i in destinations
        ):
            raise AssertionError("invalid or already completed destination")
        transaction = self._transaction("a-read", line)
        self.a_pending[transaction] = tuple(destinations)
        return transaction

    def retry_a(self, transaction: Transaction) -> Transaction:
        if transaction not in self.a_pending or transaction in self.a_accepted:
            raise AssertionError("retry has no matching unaccepted A request")
        return transaction

    def accept_a(self, transaction: Transaction) -> None:
        if transaction not in self.a_pending or transaction in self.a_accepted:
            raise AssertionError("A acceptance is missing or duplicated")
        self.a_accepted.add(transaction)

    def respond_a(
        self, transaction: Transaction, response_line: int
    ) -> tuple[int, ...]:
        if transaction.generation != self.generation:
            raise AssertionError("stale A response generation")
        if (
            transaction not in self.a_pending
            or transaction not in self.a_accepted
        ):
            raise AssertionError("A response has no accepted owner")
        if transaction.line != response_line:
            raise AssertionError("A response remapped to the wrong line")
        destinations = self.a_pending.pop(transaction)
        self.a_accepted.remove(transaction)
        return destinations

    def complete_i(self, i: int) -> None:
        if i < 0 or i >= self.n or i in self.completed_i:
            raise AssertionError(
                "logical result is missing identity or duplicated"
            )
        self.completed_i.add(i)
        self.page_counts[i // K] += 1
        if self.page_counts[i // K] > min(K, self.n - (i // K) * K):
            raise AssertionError("page completion count overflow")

    def publish_c_line(self, line: int) -> Transaction:
        transaction = self._transaction("c-write", line)
        if any(
            item.line == line and not acked
            for item, acked in self.c_writes.items()
        ):
            raise AssertionError("C owner reused before matching response")
        self.c_writes[transaction] = False
        return transaction

    def ack_c(self, transaction: Transaction) -> None:
        if transaction.generation != self.generation:
            raise AssertionError("stale C write ACK")
        if transaction not in self.c_writes or self.c_writes[transaction]:
            raise AssertionError("unknown or duplicate C write ACK")
        self.c_writes[transaction] = True

    def page_ready(self, page: int) -> bool:
        target = min(K, self.n - page * K)
        return self.page_counts[page] == target and all(
            acked
            for transaction, acked in self.c_writes.items()
            if transaction.line // (K * VALUE_BYTES) == page
        )

    def checkpoint(self) -> dict[str, object]:
        if (
            self.backing_reads
            or self.a_pending
            or any(not acked for acked in self.c_writes.values())
        ):
            raise AssertionError("checkpoint with volatile response ownership")
        if any(not acked for acked in self.backing_writes.values()):
            raise AssertionError("checkpoint with unacknowledged run writes")
        return {
            "generation": self.generation,
            "next_serial": self.next_serial,
            "completed_i": tuple(sorted(self.completed_i)),
            "page_counts": tuple(self.page_counts),
            "frozen": self.frozen,
        }

    def restart(self, checkpoint: dict[str, object]) -> None:
        if checkpoint["generation"] != self.generation:
            raise AssertionError("checkpoint generation mismatch")
        self.generation += 1
        self.next_serial = int(checkpoint["next_serial"])
        self.completed_i = set(checkpoint["completed_i"])
        self.page_counts = list(checkpoint["page_counts"])
        self.frozen = bool(checkpoint["frozen"])
        self.backing_writes.clear()
        self.backing_reads.clear()
        self.a_pending.clear()
        self.a_accepted.clear()
        self.c_writes.clear()


class ProfessorRetainSpillContractTest(unittest.TestCase):
    def test_exact_integer_ledger(self) -> None:
        ledger = lower_bound_ledger()
        self.assertEqual(ledger["descriptor_meaningful_bits"], 96)
        self.assertEqual(ledger["active_descriptor_bytes"], 49_152)
        self.assertEqual(ledger["run_line_buffer_bytes"], 256)
        self.assertEqual(ledger["run_buffer_tag_bytes"], 104)
        self.assertEqual(ledger["merge_head_bytes"], 0)
        self.assertEqual(ledger["run_cursor_count_bytes"], 13)
        self.assertEqual(ledger["global_control_bytes"], 19)
        self.assertEqual(ledger["sort_control_bytes"], 6)
        self.assertEqual(ledger["completion_bitmap_bytes"], 2_048)
        self.assertEqual(ledger["page_scoreboard_bytes"], 8)
        self.assertEqual(ledger["a_response_owner_bytes"], 92)
        self.assertEqual(ledger["c_line_owner_bytes"], 91)
        self.assertEqual(ledger["downstream_replay_on_chip_bytes"], 51_789)
        self.assertEqual(ledger["wire_record_bytes"], 16)
        self.assertEqual(ledger["spill_only_backing_footprint_bytes"], 196_608)
        self.assertEqual(
            ledger["spill_only_backing_read_write_bytes"], 393_216
        )
        self.assertEqual(ledger["full_run_backing_footprint_bytes"], 262_144)
        self.assertEqual(ledger["full_run_backing_read_write_bytes"], 524_288)
        self.assertEqual(ledger["one_b_scan_bytes"], 65_536)
        self.assertEqual(ledger["four_b_scan_bytes"], 262_144)
        self.assertEqual(ledger["common_result_payload_bytes"], 131_072)
        self.assertEqual(ledger["common_result_lines"], 2_048)
        self.assertEqual(ledger["two_slot_spd_payload_bytes"], 65_536)

    def test_bank_interleaved_slice_permutation(self) -> None:
        self.assertEqual(
            row_table_slice_order((2, 1, 4, 2), (2, 1, 2, 1)),
            [0, 2, 1, 3],
        )

    def test_native_round_robin_first_insert_duplicates_and_response_map(
        self,
    ) -> None:
        records = [
            Descriptor(0, 0, 9, 0x1000, 0),
            Descriptor(1, 0, 9, 0x1040, 1),
            Descriptor(2, 1, 7, 0x2000, 2),
            Descriptor(3, 0, 9, 0x1000, 3),
            Descriptor(4, 1, 8, 0x2040, 4),
        ]
        issues = native_issue_trace(records, 16, [0, 1], 4, 2)
        self.assertEqual(
            [item.line for item in issues], [0x1000, 0x2000, 0x1040, 0x2040]
        )
        self.assertEqual(issues[0].destinations, ((0, 0), (3, 3)))
        mapped: dict[int, int] = {}
        for issue in reversed(issues):  # adversarial response arrival
            for i, wid in issue.destinations:
                self.assertNotIn(i, mapped)
                mapped[i] = issue.line + wid * VALUE_BYTES
        self.assertEqual(
            mapped, {0: 0x1000, 1: 0x1048, 2: 0x2010, 3: 0x1018, 4: 0x2060}
        )

    def test_early_offset_and_row_capacity_drains(self) -> None:
        offset_records = [
            Descriptor(i, 0, 0, 0x1000 + i * 64) for i in range(5)
        ]
        issues = native_issue_trace(offset_records, 2, [0], 8, 8)
        self.assertEqual(
            [item.line for item in issues],
            [0x1000, 0x1040, 0x1080, 0x10C0, 0x1100],
        )
        row_skew = [Descriptor(i, 0, i, 0x2000 + i * 64) for i in range(5)]
        # Two rows per slice forces deterministic 2+2+1 early epochs.
        skew_issues = native_issue_trace(row_skew, 16, [0], 2, 1)
        self.assertEqual(
            [item.line for item in skew_issues],
            [0x2000, 0x2040, 0x2080, 0x20C0, 0x2100],
        )

    def test_minimal_future_slice_counterexample_to_greedy_drain(self) -> None:
        # K=2, N=3 is minimal: with at most K records no eviction/issue choice
        # is forced.  The unseen slice-1 head must interleave before slice-0's
        # second head in the full native epoch.
        records = [
            Descriptor(0, 0, 0, 0x1000),
            Descriptor(1, 0, 0, 0x1040),
            Descriptor(2, 1, 0, 0x2000),
        ]
        native = native_issue_trace(records, 3, [0, 1], 3, 3)
        greedy = chunk_draining_greedy(records, 2, [0, 1])
        self.assertEqual(
            [item.line for item in native], [0x1000, 0x2000, 0x1040]
        )
        self.assertEqual(
            [item.line for item in greedy], [0x1000, 0x1040, 0x2000]
        )
        self.assertNotEqual(greedy, native)

    def test_keep_top_half_without_external_image_loses_work(self) -> None:
        records = [
            RunRecord(serial, serial, 0x1000 + serial * 64, 0)
            for serial in range(4)
        ]
        retained = heapq.nlargest(2, records)
        self.assertEqual([item.i for item in retained], [3, 2])
        self.assertEqual(
            {item.i for item in records} - {item.i for item in retained},
            {0, 1},
        )
        with self.assertRaisesRegex(AssertionError, "iterator exhausted"):
            list(checked_merge([sorted(retained)], expected_records=4))

    def test_immutable_runs_recover_exact_order_and_fail_on_exhaustion(
        self,
    ) -> None:
        records = [
            Descriptor(i, i % 2, (i // 3) % 2, 0x1000 + (i % 5) * 64, i % 8)
            for i in range(12)
        ]
        issues = native_issue_trace(records, 12, [0, 1], 8, 3)
        image = oracle_materialize_native_records(records, issues)
        runs = immutable_runs(image, 3)
        merged = list(checked_merge(runs, len(records)))
        self.assertEqual(merged, sorted(image))
        self.assertEqual({item.i for item in merged}, set(range(12)))
        truncated = [run for run in runs]
        truncated[-1] = truncated[-1][:-1]
        with self.assertRaisesRegex(AssertionError, "iterator exhausted"):
            list(checked_merge(truncated, len(records)))

    def test_finite_range_spool_capacity_and_skew(self) -> None:
        records = [RunRecord(i, i, 0x1000 + i * 64, 0) for i in range(17)]
        batches = list(serial_range_spool(records, 4))
        self.assertEqual([len(batch) for batch in batches], [4, 4, 4, 4, 1])
        self.assertLessEqual(max(map(len, batches)), 4)
        # One A line with five destinations is streamed from backing; pretending
        # the entire equal-serial group fits a four-descriptor spool fails closed.
        skew = [RunRecord(0, i, 0x3000, i % 8) for i in range(5)]
        with self.assertRaisesRegex(
            AssertionError, "exceeds finite descriptor spool"
        ):
            list(serial_range_spool(skew, 4))

    def test_retry_remap_stale_generation_and_ack_conservation(self) -> None:
        life = Lifecycle(8, 4)
        life.reserve_descriptors(4)
        with self.assertRaisesRegex(AssertionError, "capacity exceeded"):
            life.reserve_descriptors(1)
        life.release_descriptors(4)
        write = life.write_backing_line(0x8000)
        with self.assertRaisesRegex(AssertionError, "reused before matching"):
            life.write_backing_line(0x8000)
        with self.assertRaisesRegex(
            AssertionError, "before matching write ACKs"
        ):
            life.freeze_runs()
        life.ack_backing(write)
        with self.assertRaisesRegex(AssertionError, "duplicate backing ACK"):
            life.ack_backing(write)
        life.freeze_runs()
        run_read = life.read_backing_line(0x8000)
        self.assertEqual(life.retry_backing_read(run_read), run_read)
        life.accept_backing_read(run_read)
        with self.assertRaisesRegex(AssertionError, "wrong line"):
            life.respond_backing_read(run_read, 0x8040)
        life.respond_backing_read(run_read, 0x8000)
        read = life.issue_a(0x1000, [0, 1])
        self.assertEqual(life.retry_a(read), read)
        self.assertEqual(
            life.next_serial, 3
        )  # retries allocated no new serial
        life.accept_a(read)
        with self.assertRaisesRegex(AssertionError, "wrong line"):
            life.respond_a(read, 0x1040)
        self.assertEqual(life.respond_a(read, 0x1000), (0, 1))
        life.complete_i(0)
        life.complete_i(1)
        with self.assertRaisesRegex(AssertionError, "duplicated"):
            life.complete_i(1)
        c_write = life.publish_c_line(0)
        with self.assertRaisesRegex(AssertionError, "reused before matching"):
            life.publish_c_line(0)
        with self.assertRaisesRegex(
            AssertionError, "volatile response ownership"
        ):
            life.checkpoint()
        life.ack_c(c_write)
        checkpoint = life.checkpoint()
        stale = Transaction(life.generation, 999, "a-read", 0x4000)
        life.restart(checkpoint)
        with self.assertRaisesRegex(
            AssertionError, "stale A response generation"
        ):
            life.respond_a(stale, 0x4000)

    def test_page_publication_requires_all_results_and_write_acks(
        self,
    ) -> None:
        life = Lifecycle(4, 4)
        write = life.write_backing_line(0x8000)
        life.ack_backing(write)
        life.freeze_runs()
        for i in range(4):
            life.complete_i(i)
        c_write = life.publish_c_line(0)
        self.assertFalse(life.page_ready(0))
        life.ack_c(c_write)
        self.assertTrue(life.page_ready(0))


if __name__ == "__main__":
    unittest.main()
