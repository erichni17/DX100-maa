#!/usr/bin/env python3
"""Replay finite 4K-retained reorder policies on frozen gather fixtures.

This is an ordering, traffic, storage, and correctness model.  It is not a
timing model.  Every policy consumes B in sequential order, keeps at most one
4K record window on chip, and carries the original destination position with
the A-line identity.  Spill policies use an explicit one-line transaction
ledger whose state is not released until an exact response identity matches.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
from collections import OrderedDict
from dataclasses import (
    asdict,
    dataclass,
    fields,
)
from pathlib import Path
from typing import (
    Iterable,
    Iterator,
    Sequence,
)

SCHEMA = "dx100-professor-bounded-reorder-policy-replay-v1"

LOGICAL_ELEMENTS = 16_384
ACTIVE_RECORDS = 4_096
INDEX_BYTES = 4
WORD_BYTES = 8
CACHE_LINE_BYTES = 64
WORDS_PER_LINE = CACHE_LINE_BYTES // WORD_BYTES
RECORD_BYTES = 16
SOURCE_LINE_BITS = 18
ROW_KEY_BITS = 11
MAX_SOURCE_LINE = (1 << SOURCE_LINE_BITS) - 1
PARTITIONS = 4

XRAGE_SHA256 = (
    "1a56db824f4fd58222d4246504e2a6fcdb0b691cd380ec18be5531ae76c1ccde"
)
FLAG_SHA256 = frozenset(
    {
        "9f344be7df05084a33d1675e1cfa29fe60e0aa3740791b9900c74066e5443919",
        "1aea650887ee2e0424a0208039f32bd777886c6c746514fc7945b86b66c9f61c",
        "995cd9c0e9cfc37bdde92220e832162d6a5d5dbf837060c9d3e4cf87818f65ef",
        "5050da44959941078daa859c13420a7e83a9e0e5be2452f506e5f6fd64153cf2",
        "fadee14ce0da8334af2a3bf7d5416fc96bf5d1b5051aa3ed0bce445d71488488",
        "c5bad529c2dd45d23cee0bc10cfe5d109f2a971db1ade90a091a67dff641fe8c",
        "4863bc4ad276c6a7f3021fbd002bcc37d8c7c60b91502d2fd125d63269dfd11f",
        "549f83b4d28063b6240b4e6c1d424ee115142231017f304c26defa40d04ad471",
        "c7f8a957edf689cf92b9bcf14707f8f0ddacbaba6d6242557582a5204f5e274a",
        "82eb717150a0a321554788dac62bcf53b5460f87af1729dc3b72d22f61c8f2d5",
        "e68891544be79a293fe9c35f5209209e1e3d38cefc9403613f06a83f6e3c19a9",
        "dc2a28bfc7be88c1a99c98d8e3548d76bc569bc339abfb54831f71d43c0551e5",
        "b16c0f8aba0bf377d429c054b426683220c9d012817d605b36b901a04a4931ed",
        "5938c8bea649b29380e9f19b2fc70002d91ebcc72d9348dc3e9d8c7fc5cece17",
    }
)

POLICIES = (
    "row_bucket_rescan",
    "sorted_runs_merge",
    "range_spool_replay",
)

# The comparable bounded4 ledger established by the prior storage audit.
PHYSICAL_SPD_PAYLOAD_BYTES = 524_288
COMMON_NON_REORDER_BYTES = 62_162
BOUNDED_ROW_OFFSET_INVALIDATOR_BYTES = 66_688
GLOBAL_ON_CHIP_BUDGET_BYTES = 656_559


class ReplayError(RuntimeError):
    """A malformed source, impossible transition, or bound violation."""


def ceil_div(dividend: int, divisor: int) -> int:
    if dividend < 0 or divisor <= 0:
        raise ValueError("dividend must be non-negative and divisor positive")
    return (dividend + divisor - 1) // divisor


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def bank_row_key(source_line: int, source_line_phase: int) -> tuple[int, ...]:
    """Return the archived DDR4 RoBaRaCoCh row proxy (rank is fixed zero)."""
    mapped = source_line + source_line_phase
    if not 0 <= mapped <= MAX_SOURCE_LINE:
        raise ReplayError(
            f"mapped source line {mapped} exceeds {SOURCE_LINE_BITS} bits"
        )
    return mapped & 1, (mapped >> 8) & 3, (mapped >> 10) & 3, mapped >> 12


def packed_bank_row_key(source_line: int, source_line_phase: int) -> int:
    channel, bank_group, bank, row = bank_row_key(
        source_line, source_line_phase
    )
    packed = (((channel << 2) | bank_group) << 2 | bank) << 6 | row
    if packed >= (1 << ROW_KEY_BITS):
        raise ReplayError("packed row key exceeds the charged 11-bit field")
    return packed


# A record is the logical contents of one 16-byte spilled descriptor:
# (A cache-line number, word offset in that line, destination position).
Record = tuple[int, int, int]


def make_record(destination: int, source_index: int) -> Record:
    source_line, source_word = divmod(source_index, WORDS_PER_LINE)
    return source_line, source_word, destination


def record_key(record: Record, source_line_phase: int) -> tuple[int, ...]:
    source_line, source_word, destination = record
    return (
        *bank_row_key(source_line, source_line_phase),
        source_line,
        source_word,
        destination,
    )


@dataclass
class Metrics:
    logical_words: int = 0
    index_scan_words: int = 0
    b_scan_bytes: int = 0
    a_requests: int = 0
    same_row_successors: int = 0
    successor_pairs: int = 0
    row_transitions: int = 0
    row_runs: int = 0
    spill_record_bytes: int = 0
    llc_write_bytes: int = 0
    llc_read_bytes: int = 0
    llc_transactions: int = 0
    llc_acks: int = 0
    max_active_records: int = 0
    max_merge_heads: int = 0
    max_transfer_buffers: int = 0

    def add(self, other: Metrics) -> None:
        high_waters = {
            "max_active_records",
            "max_merge_heads",
            "max_transfer_buffers",
        }
        for field in fields(self):
            name = field.name
            if name in high_waters:
                value = max(getattr(self, name), getattr(other, name))
                setattr(self, name, value)
            else:
                setattr(self, name, getattr(self, name) + getattr(other, name))

    def rendered(self) -> dict[str, int | float]:
        accounted_pairs = self.same_row_successors + self.row_transitions
        if accounted_pairs != self.successor_pairs:
            raise ReplayError("row successor accounting is inconsistent")
        result: dict[str, int | float] = asdict(self)
        result["same_row_successor_rate"] = round(
            self.same_row_successors / self.successor_pairs
            if self.successor_pairs
            else 0.0,
            9,
        )
        return result


class CoverageProof:
    """A bounded replay-only exact-once and source-mapping observer."""

    def __init__(self, pattern: Sequence[int]):
        if not pattern or len(pattern) > LOGICAL_ELEMENTS:
            raise ReplayError("a logical tile must contain 1..16384 B words")
        self.pattern = pattern
        self.seen_mask = 0
        self.seen_count = 0

    def observe(self, record: Record) -> None:
        source_line, source_word, destination = record
        if not 0 <= destination < len(self.pattern):
            raise ReplayError("record destination is outside its logical tile")
        bit = 1 << destination
        if self.seen_mask & bit:
            raise ReplayError(f"destination {destination} replayed twice")
        observed_index = source_line * WORDS_PER_LINE + source_word
        if observed_index != self.pattern[destination]:
            raise ReplayError(f"destination {destination} lost its B identity")
        self.seen_mask |= bit
        self.seen_count += 1

    def finish(self) -> None:
        if self.seen_count != len(self.pattern):
            raise ReplayError("policy did not replay every destination")
        if self.seen_mask != (1 << len(self.pattern)) - 1:
            raise ReplayError("policy exact-once bitmap is incomplete")


@dataclass(frozen=True)
class TransferTag:
    generation: int
    serial: int
    direction: int
    line_index: int


class AckLedger:
    """One-entry response ledger used for spill/reload line transfers."""

    def __init__(self, generation: int):
        if generation <= 0:
            raise ReplayError("generation zero is never live")
        self.generation = generation
        self.next_serial = 1
        self.active: TransferTag | None = None

    def issue(self, direction: int, line_index: int) -> TransferTag:
        if self.active is not None:
            raise ReplayError("transfer buffer reused before matching ACK")
        if direction not in (0, 1) or not 0 <= line_index < 4096:
            raise ReplayError("invalid spill/reload transfer identity")
        tag = TransferTag(
            self.generation, self.next_serial, direction, line_index
        )
        self.next_serial += 1
        self.active = tag
        return tag

    def complete(self, response: TransferTag) -> None:
        if self.active is None or response != self.active:
            raise ReplayError("stale, duplicate, or forged LLC response")
        self.active = None

    def finish(self) -> None:
        if self.active is not None:
            raise ReplayError("LLC response obligation remained live")


def transfer_lines(
    ledger: AckLedger, direction: int, lines: int, metrics: Metrics
) -> None:
    for line_index in range(lines):
        tag = ledger.issue(direction, line_index)
        metrics.llc_transactions += 1
        # The static archive has no response timing.  The ordering model uses
        # a distinct, model-selected matching completion transition.
        ledger.complete(tag)
        metrics.llc_acks += 1
    if lines:
        metrics.max_transfer_buffers = max(metrics.max_transfer_buffers, 1)


class IssueObserver:
    """Count requests and row successors while proving exact replay."""

    def __init__(
        self,
        pattern: Sequence[int],
        source_line_phase: int,
        metrics: Metrics,
    ):
        self.proof = CoverageProof(pattern)
        self.source_line_phase = source_line_phase
        self.metrics = metrics
        self.previous_row: tuple[int, ...] | None = None

    def begin_request(self, source_line: int) -> None:
        row = bank_row_key(source_line, self.source_line_phase)
        if self.previous_row is None:
            self.metrics.row_runs += 1
        else:
            self.metrics.successor_pairs += 1
            if row == self.previous_row:
                self.metrics.same_row_successors += 1
            else:
                self.metrics.row_transitions += 1
                self.metrics.row_runs += 1
        self.previous_row = row
        self.metrics.a_requests += 1

    def observe_record(self, source_line: int, record: Record) -> None:
        if record[0] != source_line:
            raise ReplayError("A request received a different source line")
        self.proof.observe(record)

    def issue(self, source_line: int, records: Iterable[Record]) -> None:
        self.begin_request(source_line)
        observed = 0
        for record in records:
            self.observe_record(source_line, record)
            observed += 1
        if observed == 0:
            raise ReplayError("empty A request is not implementable")

    def finish(self) -> None:
        self.proof.finish()


def issue_first_seen_rows(
    records: Sequence[Record], observer: IssueObserver
) -> None:
    """Execute the existing direct4 row grouping for one 4K page."""
    rows: OrderedDict[
        tuple[int, ...], OrderedDict[int, list[Record]]
    ] = OrderedDict()
    for record in records:
        source_line = record[0]
        row = bank_row_key(source_line, observer.source_line_phase)
        rows.setdefault(row, OrderedDict()).setdefault(source_line, []).append(
            record
        )
    for lines in rows.values():
        for source_line, grouped in lines.items():
            observer.issue(source_line, grouped)


def issue_sorted_window(
    records: list[Record], observer: IssueObserver
) -> None:
    if not records:
        return
    if len(records) > ACTIVE_RECORDS:
        raise ReplayError("retained subset exceeded the 4K record capacity")
    observer.metrics.max_active_records = max(
        observer.metrics.max_active_records, len(records)
    )
    # The implementable policy is a fixed in-place heap sort.  Python's sort
    # supplies the same canonical order for replay; the charged record array
    # is fixed at 4096 entries and no sorted image is persistent policy state.
    records.sort(
        key=lambda record: record_key(record, observer.source_line_phase)
    )
    begin = 0
    while begin < len(records):
        source_line = records[begin][0]
        end = begin + 1
        while end < len(records) and records[end][0] == source_line:
            end += 1
        observer.issue(source_line, records[begin:end])
        begin = end


def replay_direct4(pattern: Sequence[int], source_line_phase: int) -> Metrics:
    metrics = Metrics(
        logical_words=len(pattern),
        index_scan_words=len(pattern),
        b_scan_bytes=len(pattern) * INDEX_BYTES,
    )
    observer = IssueObserver(pattern, source_line_phase, metrics)
    for begin in range(0, len(pattern), ACTIVE_RECORDS):
        page = [
            make_record(destination, pattern[destination])
            for destination in range(
                begin, min(begin + ACTIVE_RECORDS, len(pattern))
            )
        ]
        metrics.max_active_records = max(metrics.max_active_records, len(page))
        issue_first_seen_rows(page, observer)
    observer.finish()
    return metrics


def replay_row_bucket_rescan(
    pattern: Sequence[int], source_line_phase: int
) -> Metrics:
    metrics = Metrics(
        logical_words=len(pattern),
        index_scan_words=PARTITIONS * len(pattern),
        b_scan_bytes=PARTITIONS * len(pattern) * INDEX_BYTES,
    )
    observer = IssueObserver(pattern, source_line_phase, metrics)
    for partition in range(PARTITIONS):
        retained: list[Record] = []
        for destination, source_index in enumerate(pattern):
            record = make_record(destination, source_index)
            if (
                packed_bank_row_key(record[0], source_line_phase) % PARTITIONS
                != partition
            ):
                continue
            retained.append(record)
            if len(retained) == ACTIVE_RECORDS:
                issue_sorted_window(retained, observer)
                retained = []
        issue_sorted_window(retained, observer)
    observer.finish()
    return metrics


def build_sorted_runs(
    pattern: Sequence[int], source_line_phase: int
) -> list[list[Record]]:
    runs: list[list[Record]] = []
    for begin in range(0, len(pattern), ACTIVE_RECORDS):
        run = [
            make_record(destination, pattern[destination])
            for destination in range(
                begin, min(begin + ACTIVE_RECORDS, len(pattern))
            )
        ]
        run.sort(key=lambda record: record_key(record, source_line_phase))
        runs.append(run)
    if len(runs) > PARTITIONS:
        raise ReplayError("logical tile produced more than four sorted runs")
    return runs


def replay_sorted_runs_merge(
    pattern: Sequence[int], source_line_phase: int, generation: int
) -> Metrics:
    metrics = Metrics(
        logical_words=len(pattern),
        index_scan_words=len(pattern),
        b_scan_bytes=len(pattern) * INDEX_BYTES,
        spill_record_bytes=len(pattern) * RECORD_BYTES,
    )
    observer = IssueObserver(pattern, source_line_phase, metrics)
    ledger = AckLedger(generation)
    record_lines = ceil_div(len(pattern) * RECORD_BYTES, CACHE_LINE_BYTES)
    transfer_lines(ledger, 0, record_lines, metrics)
    metrics.llc_write_bytes = record_lines * CACHE_LINE_BYTES

    runs = build_sorted_runs(pattern, source_line_phase)
    metrics.max_active_records = max(len(run) for run in runs)
    metrics.max_merge_heads = len(runs)
    transfer_lines(ledger, 1, record_lines, metrics)
    metrics.llc_read_bytes = record_lines * CACHE_LINE_BYTES

    merged: Iterator[Record] = heapq.merge(
        *runs, key=lambda record: record_key(record, source_line_phase)
    )
    current_line: int | None = None
    for record in merged:
        if current_line is None:
            current_line = record[0]
            observer.begin_request(current_line)
        if record[0] != current_line:
            current_line = record[0]
            observer.begin_request(current_line)
        observer.observe_record(current_line, record)
    observer.finish()
    ledger.finish()
    return metrics


def range_for_key(
    minimum: int, maximum: int, partition: int
) -> tuple[int, int]:
    width = ceil_div(maximum - minimum + 1, PARTITIONS)
    lower = minimum + partition * width
    upper = maximum + 1 if partition == PARTITIONS - 1 else lower + width
    return lower, upper


def replay_range_spool(
    pattern: Sequence[int], source_line_phase: int, generation: int
) -> Metrics:
    metrics = Metrics(
        logical_words=len(pattern),
        index_scan_words=len(pattern),
        b_scan_bytes=len(pattern) * INDEX_BYTES,
        spill_record_bytes=len(pattern) * RECORD_BYTES,
    )
    observer = IssueObserver(pattern, source_line_phase, metrics)
    ledger = AckLedger(generation)

    # This list is the coherent LLC backing image, not on-chip policy state.
    spool: list[Record] = []
    minimum = (1 << ROW_KEY_BITS) - 1
    maximum = 0
    for destination, source_index in enumerate(pattern):
        record = make_record(destination, source_index)
        packed = packed_bank_row_key(record[0], source_line_phase)
        minimum = min(minimum, packed)
        maximum = max(maximum, packed)
        spool.append(record)

    record_lines = ceil_div(len(spool) * RECORD_BYTES, CACHE_LINE_BYTES)
    transfer_lines(ledger, 0, record_lines, metrics)
    metrics.llc_write_bytes = record_lines * CACHE_LINE_BYTES

    for partition in range(PARTITIONS):
        transfer_lines(ledger, 1, record_lines, metrics)
        metrics.llc_read_bytes += record_lines * CACHE_LINE_BYTES
        lower, upper = range_for_key(minimum, maximum, partition)
        retained: list[Record] = []
        for record in spool:
            packed = packed_bank_row_key(record[0], source_line_phase)
            if not lower <= packed < upper:
                continue
            retained.append(record)
            if len(retained) == ACTIVE_RECORDS:
                issue_sorted_window(retained, observer)
                retained = []
        issue_sorted_window(retained, observer)

    observer.finish()
    ledger.finish()
    return metrics


def state_contract() -> dict[str, object]:
    """Return the exact fixed-point persistent state and backing ledger."""
    row_bucket_control_bits = 2 + 15 + 13 + 1
    sorted_run_state_bytes = 66_013
    range_transfer_buffers_bytes = 2 * CACHE_LINE_BYTES
    range_transfer_tags_bits = 2 * (64 + 64 + 64 + 12 + 1 + 1 + 1 + 1)
    range_global_control_bits = (
        64 + 64 + 15 + 15 + 2 + 13 + 3 + ROW_KEY_BITS + ROW_KEY_BITS
    )
    range_added_bytes = (
        range_transfer_buffers_bytes
        + ceil_div(range_transfer_tags_bits, 8)
        + ceil_div(range_global_control_bits, 8)
    )

    policies = {
        "row_bucket_rescan": {
            "row_offset_invalidator_bytes": (
                BOUNDED_ROW_OFFSET_INVALIDATOR_BYTES
            ),
            "policy_control_bits": row_bucket_control_bits,
            "policy_control_bytes": ceil_div(row_bucket_control_bits, 8),
            "reorder_state_bytes": (
                BOUNDED_ROW_OFFSET_INVALIDATOR_BYTES
                + ceil_div(row_bucket_control_bits, 8)
            ),
            "llc_backing_capacity_bytes": 0,
        },
        "sorted_runs_merge": {
            "run_state_bytes": sorted_run_state_bytes,
            "independent_invalidator_bytes": 4_096,
            "reorder_state_bytes": sorted_run_state_bytes + 4_096,
            "llc_backing_capacity_bytes": LOGICAL_ELEMENTS * RECORD_BYTES,
        },
        "range_spool_replay": {
            "row_offset_invalidator_bytes": (
                BOUNDED_ROW_OFFSET_INVALIDATOR_BYTES
            ),
            "transfer_buffer_bytes": range_transfer_buffers_bytes,
            "transfer_tag_bits": range_transfer_tags_bits,
            "transfer_tag_bytes": ceil_div(range_transfer_tags_bits, 8),
            "global_control_bits": range_global_control_bits,
            "global_control_bytes": ceil_div(range_global_control_bits, 8),
            "added_spool_state_bytes": range_added_bytes,
            "reorder_state_bytes": (
                BOUNDED_ROW_OFFSET_INVALIDATOR_BYTES + range_added_bytes
            ),
            "llc_backing_capacity_bytes": LOGICAL_ELEMENTS * RECORD_BYTES,
        },
    }
    for policy in policies.values():
        policy["physical_spd_payload_bytes"] = PHYSICAL_SPD_PAYLOAD_BYTES
        policy["common_non_reorder_bytes"] = COMMON_NON_REORDER_BYTES
        policy["on_chip_total_bytes"] = (
            PHYSICAL_SPD_PAYLOAD_BYTES
            + COMMON_NON_REORDER_BYTES
            + int(policy["reorder_state_bytes"])
        )
        policy["global_on_chip_budget_bytes"] = GLOBAL_ON_CHIP_BUDGET_BYTES
        policy["within_global_budget"] = (
            int(policy["on_chip_total_bytes"]) <= GLOBAL_ON_CHIP_BUDGET_BYTES
        )

    observer_counter_fields = len(fields(Metrics))
    observer_bits = (
        LOGICAL_ELEMENTS + 15 + observer_counter_fields * 64 + 1 + ROW_KEY_BITS
    )
    return {
        "packing": (
            "bit-packed lower bound; component byte ceilings are explicit"
        ),
        "physical_payload": {
            "elements_per_lane_tile": ACTIVE_RECORDS,
            "lane_tiles": 32,
            "bytes_per_lane_element": 4,
            "bytes": PHYSICAL_SPD_PAYLOAD_BYTES,
        },
        "common_non_reorder_bytes": COMMON_NON_REORDER_BYTES,
        "global_on_chip_budget_bytes": GLOBAL_ON_CHIP_BUDGET_BYTES,
        "policies": policies,
        "replay_observer_state": {
            "completion_bitmap_bits": LOGICAL_ELEMENTS,
            "completion_count_bits": 15,
            "metrics_counter_fields": observer_counter_fields,
            "metrics_counter_bits": observer_counter_fields * 64,
            "previous_row_valid_and_key_bits": 1 + ROW_KEY_BITS,
            "total_bits": observer_bits,
            "total_bytes": ceil_div(observer_bits, 8),
            "classification": "evidence-only; excluded from policy budget",
        },
        "interpreter_boundary": (
            "Python object headers and immutable input JSON storage are not "
            "hardware; "
            "all logical policy arrays are capped at 16K backing or 4K on chip"
        ),
    }


def bounds_ok(policy: str, metrics: Metrics) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if metrics.max_active_records > ACTIVE_RECORDS:
        reasons.append("active record high-water exceeded 4096")
    if metrics.llc_transactions != metrics.llc_acks:
        reasons.append("LLC transaction/ACK counts differ")
    if policy == "row_bucket_rescan":
        if metrics.max_merge_heads or metrics.max_transfer_buffers:
            reasons.append(
                "rescan policy used an undeclared merge/transfer buffer"
            )
    elif policy == "sorted_runs_merge":
        if metrics.max_merge_heads > PARTITIONS:
            reasons.append("merge head high-water exceeded four")
        if metrics.max_transfer_buffers > 1:
            reasons.append("transfer high-water exceeded one")
    elif policy == "range_spool_replay":
        if metrics.max_merge_heads:
            reasons.append("range replay used an undeclared merge head")
        if metrics.max_transfer_buffers > 1:
            reasons.append("transfer high-water exceeded one")
    else:
        reasons.append("unknown policy")
    return not reasons, reasons


def strict_gate(
    policy: str, candidate: Metrics, direct4: Metrics
) -> dict[str, object]:
    contract = state_contract()["policies"][policy]
    bounded, reasons = bounds_ok(policy, candidate)
    request_improved = candidate.a_requests < direct4.a_requests
    # Absolute transitions are the primary locality proxy.  A normalized
    # successor rate is diagnostic only because removing duplicate requests
    # changes its denominator and can make a structurally better order look
    # worse.  Candidate and direct4 process the same number of logical tiles,
    # so fewer transitions is equivalent to fewer row runs/activations here.
    locality_improved = candidate.row_transitions < direct4.row_transitions
    if not request_improved:
        reasons.append("A request count is not strictly below direct4")
    if not locality_improved:
        reasons.append("absolute bank-row transitions are not below direct4")
    if not bool(contract["within_global_budget"]):
        reasons.append("exact on-chip state exceeds the global budget")
    if candidate.logical_words != direct4.logical_words:
        reasons.append("candidate and direct4 logical work differ")
    return {
        "pass": not reasons,
        "strictly_fewer_a_requests": request_improved,
        "strictly_fewer_bank_row_transitions": locality_improved,
        "same_row_successor_rate_is_diagnostic_only": True,
        "bounded_and_ack_complete": bounded,
        "within_global_on_chip_budget": bool(contract["within_global_budget"]),
        "reasons": reasons,
    }


def analyze_pattern(
    pattern: Sequence[int], source_line_phase: int
) -> dict[str, object]:
    if not pattern:
        raise ReplayError("gather pattern is empty")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in pattern
    ):
        raise ReplayError(
            "gather pattern contains a non-negative-integer violation"
        )
    maximum = max(pattern) // WORDS_PER_LINE + source_line_phase
    if maximum > MAX_SOURCE_LINE:
        raise ReplayError(
            "fixture plus A-base phase exceeds the 18-bit source field"
        )

    aggregate = {
        "direct4": Metrics(),
        **{policy: Metrics() for policy in POLICIES},
    }
    for generation, begin in enumerate(
        range(0, len(pattern), LOGICAL_ELEMENTS), start=1
    ):
        tile = pattern[begin : begin + LOGICAL_ELEMENTS]
        aggregate["direct4"].add(replay_direct4(tile, source_line_phase))
        aggregate["row_bucket_rescan"].add(
            replay_row_bucket_rescan(tile, source_line_phase)
        )
        aggregate["sorted_runs_merge"].add(
            replay_sorted_runs_merge(tile, source_line_phase, generation)
        )
        aggregate["range_spool_replay"].add(
            replay_range_spool(tile, source_line_phase, generation)
        )

    direct = aggregate["direct4"]
    return {
        "pattern_words": len(pattern),
        "logical_tiles": ceil_div(len(pattern), LOGICAL_ELEMENTS),
        "max_mapped_source_line": maximum,
        "policies": {
            name: metrics.rendered() for name, metrics in aggregate.items()
        },
        "gate": {
            policy: strict_gate(policy, aggregate[policy], direct)
            for policy in POLICIES
        },
    }


def load_gather(path: Path) -> list[int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) != 1:
        raise ReplayError(f"{path}: expected one-element JSON list")
    record = payload[0]
    if not isinstance(record, dict) or record.get("kernel") != "Gather":
        raise ReplayError(f"{path}: expected one Gather record")
    pattern = record.get("pattern")
    if not isinstance(pattern, list):
        raise ReplayError(f"{path}: Gather pattern is absent")
    return pattern


def verified_input(path: Path, expected: frozenset[str]) -> dict[str, object]:
    path = path.resolve(strict=True)
    digest = sha256(path)
    if digest not in expected:
        raise ReplayError(
            f"unrecognized frozen fixture SHA-256: {path}: {digest}"
        )
    return {"path": str(path), "sha256": digest}


def verified_flag_paths(root: Path) -> list[tuple[Path, dict[str, object]]]:
    root = root.resolve(strict=True)
    paths = sorted(root.glob("**/config_*_gather.json"))
    records = [(path, verified_input(path, FLAG_SHA256)) for path in paths]
    observed = [str(record["sha256"]) for _, record in records]
    if len(observed) != len(FLAG_SHA256) or set(observed) != set(FLAG_SHA256):
        raise ReplayError(
            "FLAG root must contain each of the 14 frozen gather fixtures "
            "exactly once"
        )
    for path, record in records:
        record["fixture_id"] = str(path.relative_to(root))
    return records


def aggregate_results(
    results: Sequence[dict[str, object]]
) -> dict[str, object]:
    totals = {name: Metrics() for name in ("direct4", *POLICIES)}
    for result in results:
        policies = result["policies"]
        for name, total in totals.items():
            raw = policies[name]
            metric = Metrics(
                **{
                    field.name: int(raw[field.name])
                    for field in fields(Metrics)
                }
            )
            total.add(metric)
    direct = totals["direct4"]
    return {
        "case_count": len(results),
        "pattern_words": sum(
            int(result["pattern_words"]) for result in results
        ),
        "policies": {
            name: metric.rendered() for name, metric in totals.items()
        },
        "aggregate_gate": {
            policy: strict_gate(policy, totals[policy], direct)
            for policy in POLICIES
        },
    }


def promotion_gate(
    xrage: dict[str, object] | None, flag_cases: Sequence[dict[str, object]]
) -> dict[str, object]:
    sources: list[tuple[str, dict[str, object]]] = []
    if xrage is not None:
        sources.append(("xrage", xrage))
    sources.extend(
        (str(case["fixture_id"]), case["analysis"]) for case in flag_cases
    )
    policies: dict[str, object] = {}
    for policy in POLICIES:
        failures = [
            {
                "source": name,
                "reasons": analysis["gate"][policy]["reasons"],
            }
            for name, analysis in sources
            if not analysis["gate"][policy]["pass"]
        ]
        policies[policy] = {
            "pass": not failures and bool(sources),
            "required_source_count": len(sources),
            "passed_source_count": len(sources) - len(failures),
            "failures": failures,
        }
    return {
        "rule": (
            "pass every supplied frozen fixture with strictly fewer A "
            "requests and strictly fewer absolute bank-row transitions/row "
            "runs than direct4, "
            "all bounds/ACKs satisfied, and on-chip state <= 656559 bytes"
        ),
        "policies": policies,
        "promoted": [
            policy for policy, result in policies.items() if result["pass"]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xrage", type=Path)
    parser.add_argument("--flag-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--xrage-source-line-phase", type=int, default=3_585)
    parser.add_argument("--flag-source-line-phase", type=int, default=0)
    args = parser.parse_args()
    if args.xrage is None and args.flag_root is None:
        parser.error(
            "at least one frozen XRAGE or FLAG fixture source is required"
        )

    output: dict[str, object] = {
        "schema": SCHEMA,
        "model_sha256": sha256(Path(__file__).resolve()),
        "scope": {
            "timing_prediction": False,
            "gem5_execution": False,
            "oracle_future_knowledge": False,
            "source_response_order": "model-selected deterministic order",
            "llc_completion": "distinct exact-tag ACK transition",
        },
        "state_contract": state_contract(),
    }
    xrage_analysis: dict[str, object] | None = None
    if args.xrage is not None:
        identity = verified_input(args.xrage, frozenset({XRAGE_SHA256}))
        xrage_analysis = analyze_pattern(
            load_gather(Path(identity["path"])), args.xrage_source_line_phase
        )
        output["xrage"] = {**identity, **xrage_analysis}

    flag_cases: list[dict[str, object]] = []
    if args.flag_root is not None:
        for path, identity in verified_flag_paths(args.flag_root):
            analysis = analyze_pattern(
                load_gather(path), args.flag_source_line_phase
            )
            flag_cases.append({**identity, "analysis": analysis})
        output["flag"] = {
            **aggregate_results([case["analysis"] for case in flag_cases]),
            "cases": flag_cases,
            "source_line_phase_limitation": (
                "FLAG fixture omits A-base phase; zero is the declared proxy"
            ),
        }

    output["promotion_gate"] = promotion_gate(xrage_analysis, flag_cases)
    rendered = json.dumps(output, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
