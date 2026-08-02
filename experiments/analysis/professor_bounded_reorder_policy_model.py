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
    dataclass,
    fields,
)
from itertools import groupby
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
SOURCE_WORD_BITS = 3
SOURCE_INDEX_BITS = SOURCE_LINE_BITS + SOURCE_WORD_BITS
ROW_KEY_BITS = 11
MAX_SOURCE_LINE = (1 << SOURCE_LINE_BITS) - 1
MAX_SOURCE_INDEX = (1 << SOURCE_INDEX_BITS) - 1
UINT64_MAX = (1 << 64) - 1
TRANSFER_LINE_BITS = 12
MAX_TRANSFER_LINE = (1 << TRANSFER_LINE_BITS) - 1
PARTITIONS = 4

XRAGE_SHA256 = "1a56db824f4fd58222d4246504e2a6fcdb0b691cd380ec18be5531ae76c1ccde"
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


def require_exact_uint(
    name: str, value: object, maximum: int, *, minimum: int = 0
) -> int:
    """Admit one packed unsigned identity without Python coercions."""
    if type(value) is not int or not minimum <= value <= maximum:
        raise ReplayError(f"{name} must be an exact integer in [{minimum}, {maximum}]")
    return value


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
    require_exact_uint("source line", source_line, MAX_SOURCE_LINE)
    require_exact_uint("source-line phase", source_line_phase, MAX_SOURCE_LINE)
    mapped = source_line + source_line_phase
    if not 0 <= mapped <= MAX_SOURCE_LINE:
        raise ReplayError(
            f"mapped source line {mapped} exceeds {SOURCE_LINE_BITS} bits"
        )
    return mapped & 1, (mapped >> 8) & 3, (mapped >> 10) & 3, mapped >> 12


def packed_bank_row_key(source_line: int, source_line_phase: int) -> int:
    channel, bank_group, bank, row = bank_row_key(source_line, source_line_phase)
    packed = (((channel << 2) | bank_group) << 2 | bank) << 6 | row
    if packed >= (1 << ROW_KEY_BITS):
        raise ReplayError("packed row key exceeds the charged 11-bit field")
    return packed


# A record is the logical contents of one 16-byte spilled descriptor:
# (A cache-line number, word offset in that line, destination position).
Record = tuple[int, int, int]


def make_record(destination: int, source_index: int) -> Record:
    require_exact_uint("record destination", destination, LOGICAL_ELEMENTS - 1)
    require_exact_uint("B source index", source_index, MAX_SOURCE_INDEX)
    source_line, source_word = divmod(source_index, WORDS_PER_LINE)
    return source_line, source_word, destination


def admit_record(record: Record) -> Record:
    if type(record) is not tuple or len(record) != 3:
        raise ReplayError("record must be an exact three-integer value tuple")
    source_line, source_word, destination = record
    return (
        require_exact_uint("record source line", source_line, MAX_SOURCE_LINE),
        require_exact_uint("record source word", source_word, WORDS_PER_LINE - 1),
        require_exact_uint("record destination", destination, LOGICAL_ELEMENTS - 1),
    )


def record_key(record: Record, source_line_phase: int) -> tuple[int, ...]:
    source_line, source_word, destination = admit_record(record)
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

    def __post_init__(self) -> None:
        self._admitted_values()

    def _admitted_values(self) -> dict[str, int]:
        return {
            field.name: require_exact_uint(
                f"metric {field.name}", getattr(self, field.name), UINT64_MAX
            )
            for field in fields(self)
        }

    def _commit_values(self, values: dict[str, int]) -> None:
        self.__dict__.update(values)

    def add(self, other: Metrics) -> None:
        if type(other) is not Metrics:
            raise ReplayError("metrics operand must be an exact Metrics value")
        current = self._admitted_values()
        incoming = other._admitted_values()
        high_waters = {
            "max_active_records",
            "max_merge_heads",
            "max_transfer_buffers",
        }
        result: dict[str, int] = {}
        for field in fields(self):
            name = field.name
            if name in high_waters:
                result[name] = max(current[name], incoming[name])
            else:
                result[name] = require_exact_uint(
                    f"combined metric {name}",
                    current[name] + incoming[name],
                    UINT64_MAX,
                )
        self._commit_values(result)

    def rendered(self) -> dict[str, int | float]:
        values = self._admitted_values()
        accounted_pairs = values["same_row_successors"] + values["row_transitions"]
        if accounted_pairs != values["successor_pairs"]:
            raise ReplayError("row successor accounting is inconsistent")
        result: dict[str, int | float] = values
        result["same_row_successor_rate"] = round(
            values["same_row_successors"] / values["successor_pairs"]
            if values["successor_pairs"]
            else 0.0,
            9,
        )
        return result


class CoverageProof:
    """A bounded replay-only exact-once and source-mapping observer."""

    def __init__(self, pattern: Sequence[int]):
        admitted_values: list[int] = []
        for source_index in pattern:
            if len(admitted_values) == LOGICAL_ELEMENTS:
                raise ReplayError("a logical tile must contain 1..16384 B words")
            admitted_values.append(
                require_exact_uint(
                    "admitted B source index", source_index, MAX_SOURCE_INDEX
                )
            )
        if not admitted_values:
            raise ReplayError("a logical tile must contain 1..16384 B words")
        admitted_pattern = tuple(admitted_values)
        # The proof owns this immutable identity snapshot.  It never aliases
        # a caller-owned list whose elements could change after admission.
        self.pattern = admitted_pattern
        self.seen_mask = 0
        self.seen_count = 0

    def _preview_observation(
        self, record: Record, seen_mask: int
    ) -> tuple[Record, int]:
        source_line, source_word, destination = admit_record(record)
        if not 0 <= destination < len(self.pattern):
            raise ReplayError("record destination is outside its logical tile")
        bit = 1 << destination
        if seen_mask & bit:
            raise ReplayError(f"destination {destination} replayed twice")
        observed_index = source_line * WORDS_PER_LINE + source_word
        if observed_index != self.pattern[destination]:
            raise ReplayError(f"destination {destination} lost its B identity")
        return (source_line, source_word, destination), seen_mask | bit

    def observe(self, record: Record) -> None:
        admitted_count = require_exact_uint(
            "coverage count", self.seen_count, len(self.pattern)
        )
        admitted_mask = require_exact_uint(
            "coverage mask", self.seen_mask, (1 << len(self.pattern)) - 1
        )
        _, next_mask = self._preview_observation(record, admitted_mask)
        next_count = require_exact_uint(
            "coverage count", admitted_count + 1, len(self.pattern)
        )
        self.seen_mask, self.seen_count = next_mask, next_count

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

    def __post_init__(self) -> None:
        require_exact_uint(
            "transfer generation", self.generation, UINT64_MAX, minimum=1
        )
        require_exact_uint("transfer serial", self.serial, UINT64_MAX, minimum=1)
        require_exact_uint("transfer direction", self.direction, 1)
        require_exact_uint("transfer line index", self.line_index, MAX_TRANSFER_LINE)


class AckLedger:
    """One-entry response ledger used for spill/reload line transfers."""

    def __init__(self, generation: int):
        self._generation = require_exact_uint(
            "generation", generation, UINT64_MAX, minimum=1
        )
        self._next_serial = 1
        self._active_identity: tuple[int, int, int, int] | None = None

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def next_serial(self) -> int:
        return self._next_serial

    @next_serial.setter
    def next_serial(self, value: int) -> None:
        admitted = require_exact_uint("next serial", value, UINT64_MAX)
        self._next_serial = admitted

    @property
    def active(self) -> TransferTag | None:
        """Return a value copy without exposing the owned live identity."""
        if self._active_identity is None:
            return None
        return TransferTag(*self._active_identity)

    @staticmethod
    def _admit_response(response: TransferTag) -> tuple[int, int, int, int]:
        if type(response) is not TransferTag:
            raise ReplayError("LLC response must be an exact TransferTag")
        return (
            require_exact_uint(
                "response generation",
                response.generation,
                UINT64_MAX,
                minimum=1,
            ),
            require_exact_uint(
                "response serial", response.serial, UINT64_MAX, minimum=1
            ),
            require_exact_uint("response direction", response.direction, 1),
            require_exact_uint(
                "response line index", response.line_index, MAX_TRANSFER_LINE
            ),
        )

    def issue(self, direction: int, line_index: int) -> TransferTag:
        admitted_direction = require_exact_uint("direction", direction, 1)
        admitted_line = require_exact_uint("line index", line_index, MAX_TRANSFER_LINE)
        if self._active_identity is not None:
            raise ReplayError("transfer buffer reused before matching ACK")
        if self._next_serial == 0:
            raise ReplayError("uint64 transfer serial space is exhausted")
        admitted_serial = require_exact_uint(
            "next serial", self._next_serial, UINT64_MAX, minimum=1
        )
        admitted_generation = require_exact_uint(
            "generation", self._generation, UINT64_MAX, minimum=1
        )
        identity = (
            admitted_generation,
            admitted_serial,
            admitted_direction,
            admitted_line,
        )
        returned = TransferTag(*identity)
        next_serial = 0 if admitted_serial == UINT64_MAX else admitted_serial + 1
        self._next_serial, self._active_identity = next_serial, identity
        # The caller receives a distinct value object.  The ledger retains an
        # immutable tuple, so object.__setattr__ cannot rewrite live state.
        return returned

    def complete(self, response: TransferTag) -> None:
        identity = self._admit_response(response)
        if self._active_identity is None or identity != self._active_identity:
            raise ReplayError("stale, duplicate, or forged LLC response")
        self._active_identity = None

    def finish(self) -> None:
        if self._active_identity is not None:
            raise ReplayError("LLC response obligation remained live")


def transfer_lines(
    ledger: AckLedger, direction: int, lines: int, metrics: Metrics
) -> None:
    if type(ledger) is not AckLedger:
        raise ReplayError("transfer ledger must be an exact AckLedger")
    if type(metrics) is not Metrics:
        raise ReplayError("transfer metrics must be an exact Metrics value")
    admitted_direction = require_exact_uint("direction", direction, 1)
    admitted_lines = require_exact_uint(
        "transfer line count", lines, MAX_TRANSFER_LINE + 1
    )

    staged_ledger = AckLedger(ledger.generation)
    staged_ledger.next_serial = ledger.next_serial
    staged_ledger._active_identity = ledger._active_identity
    staged_metrics = Metrics(**metrics._admitted_values())
    for line_index in range(admitted_lines):
        tag = staged_ledger.issue(admitted_direction, line_index)
        staged_metrics.llc_transactions += 1
        # The static archive has no response timing.  The ordering model uses
        # a distinct, model-selected matching completion transition.
        staged_ledger.complete(tag)
        staged_metrics.llc_acks += 1
    if admitted_lines:
        staged_metrics.max_transfer_buffers = max(
            staged_metrics.max_transfer_buffers, 1
        )

    staged_values = staged_metrics._admitted_values()
    ledger._next_serial = staged_ledger.next_serial
    ledger._active_identity = staged_ledger._active_identity
    metrics._commit_values(staged_values)


class IssueObserver:
    """Count requests and row successors while proving exact replay."""

    def __init__(
        self,
        pattern: Sequence[int],
        source_line_phase: int,
        metrics: Metrics,
    ):
        admitted_phase = require_exact_uint(
            "source-line phase", source_line_phase, MAX_SOURCE_LINE
        )
        if type(metrics) is not Metrics:
            raise ReplayError("observer metrics must be an exact Metrics value")
        admitted_proof = CoverageProof(pattern)
        metrics._admitted_values()
        self.proof = admitted_proof
        self.source_line_phase = admitted_phase
        self.metrics = metrics
        self.previous_row: tuple[int, ...] | None = None

    @staticmethod
    def _increment(values: dict[str, int], name: str) -> None:
        values[name] = require_exact_uint(
            f"metric {name}", values[name] + 1, UINT64_MAX
        )

    def _preview_request_state(
        self,
        source_line: int,
        values: dict[str, int],
        previous_row: tuple[int, ...] | None,
    ) -> tuple[int, tuple[int, ...], dict[str, int]]:
        admitted_source_line = require_exact_uint(
            "request source line", source_line, MAX_SOURCE_LINE
        )
        row = bank_row_key(admitted_source_line, self.source_line_phase)
        updated = dict(values)
        if previous_row is None:
            self._increment(updated, "row_runs")
        else:
            self._increment(updated, "successor_pairs")
            if row == previous_row:
                self._increment(updated, "same_row_successors")
            else:
                self._increment(updated, "row_transitions")
                self._increment(updated, "row_runs")
        self._increment(updated, "a_requests")
        return admitted_source_line, row, updated

    def _issue_requests(
        self,
        requests: Iterable[tuple[int, Iterable[Record]]],
        *,
        active_records: int | None = None,
    ) -> None:
        next_metrics = self.metrics._admitted_values()
        next_previous_row = self.previous_row
        next_seen_count = require_exact_uint(
            "coverage count", self.proof.seen_count, len(self.proof.pattern)
        )
        next_seen_mask = require_exact_uint(
            "coverage mask",
            self.proof.seen_mask,
            (1 << len(self.proof.pattern)) - 1,
        )
        if active_records is not None:
            admitted_active = require_exact_uint(
                "active record count", active_records, ACTIVE_RECORDS
            )
            next_metrics["max_active_records"] = max(
                next_metrics["max_active_records"], admitted_active
            )

        for request in requests:
            if type(request) is not tuple or len(request) != 2:
                raise ReplayError(
                    "request must be an exact (source line, records) tuple"
                )
            source_line, records = request
            admitted_source_line, row, next_metrics = self._preview_request_state(
                source_line, next_metrics, next_previous_row
            )
            try:
                iterator = iter(records)
            except TypeError as error:
                raise ReplayError("request records must be iterable") from error

            observed = 0
            for record in iterator:
                admitted_record, candidate_mask = self.proof._preview_observation(
                    record, next_seen_mask
                )
                if admitted_record[0] != admitted_source_line:
                    raise ReplayError("A request received a different source line")
                next_seen_count = require_exact_uint(
                    "coverage count",
                    next_seen_count + 1,
                    len(self.proof.pattern),
                )
                next_seen_mask = candidate_mask
                observed += 1
            if observed == 0:
                raise ReplayError("empty A request is not implementable")
            next_previous_row = row

        self.metrics._commit_values(next_metrics)
        self.previous_row = next_previous_row
        self.proof.seen_mask, self.proof.seen_count = (
            next_seen_mask,
            next_seen_count,
        )

    def begin_request(self, source_line: int) -> None:
        values = self.metrics._admitted_values()
        _, row, next_metrics = self._preview_request_state(
            source_line, values, self.previous_row
        )
        self.metrics._commit_values(next_metrics)
        self.previous_row = row

    def observe_record(self, source_line: int, record: Record) -> None:
        admitted_source_line = require_exact_uint(
            "request source line", source_line, MAX_SOURCE_LINE
        )
        admitted_count = require_exact_uint(
            "coverage count", self.proof.seen_count, len(self.proof.pattern)
        )
        admitted_mask = require_exact_uint(
            "coverage mask",
            self.proof.seen_mask,
            (1 << len(self.proof.pattern)) - 1,
        )
        admitted_record, next_mask = self.proof._preview_observation(
            record, admitted_mask
        )
        if admitted_record[0] != admitted_source_line:
            raise ReplayError("A request received a different source line")
        next_count = require_exact_uint(
            "coverage count", admitted_count + 1, len(self.proof.pattern)
        )
        self.proof.seen_mask, self.proof.seen_count = next_mask, next_count

    def issue(self, source_line: int, records: Iterable[Record]) -> None:
        self._issue_requests(((source_line, records),))

    def finish(self) -> None:
        self.proof.finish()


def issue_first_seen_rows(records: Sequence[Record], observer: IssueObserver) -> None:
    """Execute the existing direct4 row grouping for one 4K page."""
    if type(observer) is not IssueObserver:
        raise ReplayError("issue observer must be an exact IssueObserver")
    if len(records) > ACTIVE_RECORDS:
        raise ReplayError("direct page exceeded the 4K record capacity")
    rows: OrderedDict[tuple[int, ...], OrderedDict[int, list[Record]]] = OrderedDict()
    for record in records:
        admitted_record = admit_record(record)
        source_line = admitted_record[0]
        row = bank_row_key(source_line, observer.source_line_phase)
        rows.setdefault(row, OrderedDict()).setdefault(source_line, []).append(
            admitted_record
        )
    requests = (
        (source_line, grouped)
        for lines in rows.values()
        for source_line, grouped in lines.items()
    )
    observer._issue_requests(requests)


def issue_sorted_window(records: list[Record], observer: IssueObserver) -> None:
    if type(records) is not list:
        raise ReplayError("retained record queue must be an exact list")
    if type(observer) is not IssueObserver:
        raise ReplayError("issue observer must be an exact IssueObserver")
    if not records:
        return
    if len(records) > ACTIVE_RECORDS:
        raise ReplayError("retained subset exceeded the 4K record capacity")
    # The implementable policy is a fixed in-place heap sort.  Python's sort
    # supplies the same canonical order for replay.  The temporary host image
    # validates before commit; the charged policy array remains fixed at 4096.
    ordered = sorted(
        (admit_record(record) for record in records),
        key=lambda record: record_key(record, observer.source_line_phase),
    )
    requests = (
        (source_line, grouped)
        for source_line, grouped in groupby(ordered, key=lambda record: record[0])
    )
    observer._issue_requests(requests, active_records=len(ordered))


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
            for destination in range(begin, min(begin + ACTIVE_RECORDS, len(pattern)))
        ]
        metrics.max_active_records = max(metrics.max_active_records, len(page))
        issue_first_seen_rows(page, observer)
    observer.finish()
    return metrics


def replay_row_bucket_rescan(pattern: Sequence[int], source_line_phase: int) -> Metrics:
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
    if len(pattern) > LOGICAL_ELEMENTS:
        raise ReplayError("sorted-run input exceeded one 16K logical tile")
    runs: list[list[Record]] = []
    for begin in range(0, len(pattern), ACTIVE_RECORDS):
        run = [
            make_record(destination, pattern[destination])
            for destination in range(begin, min(begin + ACTIVE_RECORDS, len(pattern)))
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
    for source_line, grouped in groupby(merged, key=lambda record: record[0]):
        observer.issue(source_line, grouped)
    observer.finish()
    ledger.finish()
    return metrics


def range_for_key(minimum: int, maximum: int, partition: int) -> tuple[int, int]:
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
            "row_offset_invalidator_bytes": (BOUNDED_ROW_OFFSET_INVALIDATOR_BYTES),
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
            "row_offset_invalidator_bytes": (BOUNDED_ROW_OFFSET_INVALIDATOR_BYTES),
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
    observer_b_snapshot_bits = LOGICAL_ELEMENTS * SOURCE_INDEX_BITS
    observer_bits = (
        observer_b_snapshot_bits
        + LOGICAL_ELEMENTS
        + 15
        + observer_counter_fields * 64
        + 1
        + ROW_KEY_BITS
    )
    return {
        "packing": ("bit-packed lower bound; component byte ceilings are explicit"),
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
            "admitted_b_snapshot_entries": LOGICAL_ELEMENTS,
            "admitted_b_snapshot_bits_per_entry": SOURCE_INDEX_BITS,
            "admitted_b_snapshot_bits": observer_b_snapshot_bits,
            "completion_bitmap_bits": LOGICAL_ELEMENTS,
            "completion_count_bits": 15,
            "metrics_counter_fields": observer_counter_fields,
            "metrics_counter_bits": observer_counter_fields * 64,
            "previous_row_valid_and_key_bits": 1 + ROW_KEY_BITS,
            "total_bits": observer_bits,
            "total_bytes": ceil_div(observer_bits, 8),
            "classification": "evidence-only; excluded from policy budget",
        },
        "serial_exhaustion_contract": {
            "next_serial_bits": 64,
            "exhausted_sentinel": 0,
            "serial_zero_issued": False,
            "extra_exhaustion_flag_bits": 0,
        },
        "transaction_contract": {
            "admission": "exact type and range before state mutation",
            "request_scope": "complete request and every record",
            "commit": "staged coupled-state commit after full validation",
            "rejection_preserves": (
                "metrics, previous row, coverage, queue, and ACK ledger"
            ),
            "returned_transfer_identity": "distinct value copy",
        },
        "interpreter_boundary": (
            "Python object headers are not hardware; the admitted immutable "
            "B snapshot is charged above as bounded evidence-only state, and "
            "all policy arrays are capped at 16K backing or 4K on chip"
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
            reasons.append("rescan policy used an undeclared merge/transfer buffer")
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


def strict_gate(policy: str, candidate: Metrics, direct4: Metrics) -> dict[str, object]:
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
    require_exact_uint("source-line phase", source_line_phase, MAX_SOURCE_LINE)
    if not pattern:
        raise ReplayError("gather pattern is empty")
    for value in pattern:
        require_exact_uint("gather source index", value, MAX_SOURCE_INDEX)
    maximum = max(pattern) // WORDS_PER_LINE + source_line_phase
    if maximum > MAX_SOURCE_LINE:
        raise ReplayError("fixture plus A-base phase exceeds the 18-bit source field")

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
        "policies": {name: metrics.rendered() for name, metrics in aggregate.items()},
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
        raise ReplayError(f"unrecognized frozen fixture SHA-256: {path}: {digest}")
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


def aggregate_results(results: Sequence[dict[str, object]]) -> dict[str, object]:
    totals = {name: Metrics() for name in ("direct4", *POLICIES)}
    for result in results:
        policies = result["policies"]
        for name, total in totals.items():
            raw = policies[name]
            metric = Metrics(
                **{field.name: int(raw[field.name]) for field in fields(Metrics)}
            )
            total.add(metric)
    direct = totals["direct4"]
    return {
        "case_count": len(results),
        "pattern_words": sum(int(result["pattern_words"]) for result in results),
        "policies": {name: metric.rendered() for name, metric in totals.items()},
        "aggregate_gate": {
            policy: strict_gate(policy, totals[policy], direct) for policy in POLICIES
        },
    }


def promotion_gate(
    xrage: dict[str, object] | None, flag_cases: Sequence[dict[str, object]]
) -> dict[str, object]:
    sources: list[tuple[str, dict[str, object]]] = []
    if xrage is not None:
        sources.append(("xrage", xrage))
    sources.extend((str(case["fixture_id"]), case["analysis"]) for case in flag_cases)
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
        "promoted": [policy for policy, result in policies.items() if result["pass"]],
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
        parser.error("at least one frozen XRAGE or FLAG fixture source is required")

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
            analysis = analyze_pattern(load_gather(path), args.flag_source_line_phase)
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
