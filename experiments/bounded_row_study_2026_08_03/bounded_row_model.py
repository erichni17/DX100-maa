#!/usr/bin/env python3
"""Executable trace model for an honest 16K-logical/4K-active reorder.

The treatment makes four sequential passes over coherent B and admits only a
contiguous interval of decoded DRAM rows in each pass.  It never retains more
than K descriptors.  An overfull interval drains in K-entry epochs rather than
overflowing or creating a hidden N-entry spill.

This is an ordering, traffic, correctness, and capacity model.  It deliberately
does not predict gem5 ticks.  Validation-only Python lists, hashes, and sets are
oracles around the modeled mechanism; they are not charged as hardware state
and are never consulted to select or order requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import OrderedDict
from dataclasses import (
    asdict,
    dataclass,
)
from typing import (
    Iterable,
    Iterator,
)

LOGICAL_ELEMENTS = 16_384
ACTIVE_ELEMENTS = 4_096
INDEX_BYTES = 4
CACHE_LINE_BYTES = 64
FILTER_WORDS_PER_CYCLE = 16

# The frozen lower-bound ledger documented before this experiment.  It uses a
# 32-tile, 4K-physical SPD and explicitly finite feeder/response/combiner state.
BOUNDED4_COMMON_STATE_BYTES = 653_138 - 66_688
FULL16_ON_4K_TOTAL_BYTES = 842_482
FULL16_PHYSICAL_SPD_DELTA_BYTES = 32 * (16_384 - 4_096) * 4


def ceil_div(value: int, divisor: int) -> int:
    if value < 0 or divisor <= 0:
        raise ValueError("value must be nonnegative and divisor positive")
    return (value + divisor - 1) // divisor


def bits_for_values(values: int) -> int:
    if values <= 1:
        return 1
    return math.ceil(math.log2(values))


@dataclass(frozen=True)
class TraceSpec:
    name: str
    word_bytes: int
    source_elements: int
    source_line_offset: int = 17
    source_word_offset: int = 0

    @staticmethod
    def named(name: str, logical_elements: int) -> TraceSpec:
        if name == "tile_consumer_fp64":
            # benchmarks/API/test_virtual_tile_consumer.cpp:79-109
            # The frozen matched control observes 9,523 unique source lines;
            # a two-word base offset reproduces that recorded allocator
            # alignment without influencing row selection or issue order.
            return TraceSpec(name, 8, logical_elements * 8, 17, 2)
        if name in {
            "virtual_index_random_fp32",
            "virtual_index_fanout_fp32",
            "virtual_index_same_line_fp32",
            "virtual_index_line_revisit_fp32",
        }:
            # benchmarks/API/test_virtual_index_gather.cpp:31-54
            multiplier = 32 if name.endswith("line_revisit_fp32") else 4
            return TraceSpec(name, 4, logical_elements * multiplier)
        raise ValueError(f"unknown deterministic trace: {name}")

    def indices(self, logical_elements: int) -> Iterator[int]:
        for i in range(logical_elements):
            if self.name == "virtual_index_fanout_fp32":
                yield 13
            elif self.name == "virtual_index_same_line_fp32":
                yield (i * 5 + 3) % 16
            elif (
                self.name == "virtual_index_line_revisit_fp32" and i % 64 == 0
            ):
                yield 13
            else:
                yield (i * 97 + 13) % self.source_elements


@dataclass(frozen=True)
class DecodedAddress:
    line: int
    row_key: int


@dataclass(frozen=True)
class Decoder:
    """DDR4_8Gb_x8 RoBaRaCoCh geometry at 64-byte transactions.

    One decoded row key is one (row, bank-group, bank) tuple.  With four bank
    groups, four banks/group, and 128 cache lines/DRAM row, its monotonic
    linearization is simply absolute_line // 128.  Partition bounds come from
    the registered A aperture, not from observed B values.
    """

    columns_per_row: int = 128
    bank_groups: int = 4
    banks_per_group: int = 4

    def decode_source_index(
        self, spec: TraceSpec, index: int
    ) -> DecodedAddress:
        words_per_line = CACHE_LINE_BYTES // spec.word_bytes
        line = (
            spec.source_line_offset
            + (index + spec.source_word_offset) // words_per_line
        )
        return DecodedAddress(line=line, row_key=line // self.columns_per_row)

    def aperture_rows(self, spec: TraceSpec) -> tuple[int, int]:
        words_per_line = CACHE_LINE_BYTES // spec.word_bytes
        source_lines = ceil_div(
            spec.source_elements + spec.source_word_offset, words_per_line
        )
        first = spec.source_line_offset // self.columns_per_row
        last = (
            spec.source_line_offset + source_lines - 1
        ) // self.columns_per_row
        return first, last


@dataclass(frozen=True)
class Record:
    itr: int
    index: int
    line: int
    row_key: int


@dataclass
class RunResult:
    policy: str
    trace: str
    logical_elements: int
    active_limit: int
    peak_active_descriptors: int
    active_bound_respected: bool
    issue_lines: list[int]
    issue_rows: list[int]
    issue_order_sha256: str
    a_line_requests: int
    unique_a_lines: int
    row_transitions: int
    epochs: int
    capacity_drain_barriers: int
    partition_barriers: int
    b_passes: int
    b_scan_bytes: int
    b_line_reads: int
    b_reread_bytes: int
    b_reread_lines: int
    filter_words: int
    selector_cycles_lower_bound: int
    response_placements: int
    missing_placements: int
    duplicate_placements: int
    output_hash: int
    reorder_metadata_lower_bound_bytes: int
    partition_control_bytes: int
    mechanism_total_lower_bound_bytes: int | None
    expected_stall_sources: list[str]

    def summary(self) -> dict[str, object]:
        result = asdict(self)
        result.pop("issue_lines")
        result.pop("issue_rows")
        return result


class Model:
    def __init__(
        self,
        logical_elements: int = LOGICAL_ELEMENTS,
        active_limit: int = ACTIVE_ELEMENTS,
        filter_words_per_cycle: int = FILTER_WORDS_PER_CYCLE,
    ) -> None:
        if logical_elements <= 0 or active_limit <= 0:
            raise ValueError(
                "logical and active element counts must be positive"
            )
        if logical_elements % active_limit:
            raise ValueError(
                "logical elements must be divisible by active limit"
            )
        if filter_words_per_cycle <= 0:
            raise ValueError("filter throughput must be finite and positive")
        self.n = logical_elements
        self.k = active_limit
        self.partitions = logical_elements // active_limit
        self.filter_words_per_cycle = filter_words_per_cycle
        self.decoder = Decoder()

    def records(self, spec: TraceSpec) -> Iterator[Record]:
        for itr, index in enumerate(spec.indices(self.n)):
            decoded = self.decoder.decode_source_index(spec, index)
            yield Record(itr, index, decoded.line, decoded.row_key)

    @staticmethod
    def _issue_epoch(buffer: list[Record]) -> tuple[list[int], list[int]]:
        # This mirrors the native semantic grouping: rows and cache lines are
        # allocated on first encounter; duplicate lines append iterations.
        # The model does not sort with future knowledge.
        rows: OrderedDict[int, OrderedDict[int, None]] = OrderedDict()
        for record in buffer:
            rows.setdefault(record.row_key, OrderedDict()).setdefault(
                record.line, None
            )
        lines: list[int] = []
        issue_rows: list[int] = []
        for row, row_lines in rows.items():
            for line in row_lines:
                lines.append(line)
                issue_rows.append(row)
        return lines, issue_rows

    def _row_partition(self, spec: TraceSpec, row_key: int) -> int:
        first, last = self.decoder.aperture_rows(spec)
        rows = last - first + 1
        if not first <= row_key <= last:
            raise AssertionError(
                "decoded source row escaped registered aperture"
            )
        return min(
            self.partitions - 1, (row_key - first) * self.partitions // rows
        )

    def _metadata_bytes(self, word_bytes: int, active: int) -> int:
        iteration_bits = bits_for_values(self.n + 1)
        word_id_bits = bits_for_values(CACHE_LINE_BYTES // word_bytes)
        offset = ceil_div(active * (iteration_bits + word_id_bits + 1), 8)
        row_entries = ceil_div(active * (64 + 2 * iteration_bits + 1), 8)
        # Eight line entries per row matches the native experiment geometry.
        row_headers = ceil_div(ceil_div(active, 8) * (64 + 2), 8)
        invalidator_lines = 32 * self.n * 4 // CACHE_LINE_BYTES
        invalidator = ceil_div(invalidator_lines, 8)
        return offset + row_entries + row_headers + invalidator

    def _finish(
        self,
        policy: str,
        spec: TraceSpec,
        active_limit: int,
        issue_lines: list[int],
        issue_rows: list[int],
        epochs: int,
        capacity_drains: int,
        partition_barriers: int,
        b_passes: int,
        peak_active: int,
        placement_counts: list[int],
        outputs: list[int],
    ) -> RunResult:
        digest = hashlib.sha256()
        for line in issue_lines:
            digest.update(line.to_bytes(8, "little"))
        output_hash = 1469598103934665603
        mask = (1 << (spec.word_bytes * 8)) - 1
        for value in outputs:
            for byte in (value & mask).to_bytes(spec.word_bytes, "little"):
                output_hash ^= byte
                output_hash = (output_hash * 1099511628211) & ((1 << 64) - 1)
        b_bytes_per_pass = self.n * INDEX_BYTES
        b_lines_per_pass = ceil_div(b_bytes_per_pass, CACHE_LINE_BYTES)
        filter_words = self.n * b_passes if policy == "bounded_rows" else 0
        metadata = self._metadata_bytes(spec.word_bytes, active_limit)
        partition_control = 4 if policy == "bounded_rows" else 0
        total: int | None = None
        if (
            self.n == LOGICAL_ELEMENTS
            and self.k == ACTIVE_ELEMENTS
            and spec.word_bytes == 8
        ):
            if policy == "native4k":
                total = 653_138
            elif policy == "bounded_rows":
                total = 653_142
            elif policy == "native16":
                total = (
                    FULL16_ON_4K_TOTAL_BYTES + FULL16_PHYSICAL_SPD_DELTA_BYTES
                )
        expected_stalls: list[str] = []
        if policy == "native4k":
            expected_stalls.append("three call/epoch barriers")
        if policy == "bounded_rows":
            expected_stalls.extend(
                [
                    f"{partition_barriers} partition barriers",
                    f"{capacity_drains} skew/capacity drain barriers",
                    f">={ceil_div(filter_words, self.filter_words_per_cycle)} serialized selector cycles",
                    f"{b_lines_per_pass * (b_passes - 1)} coherent B-line rereads",
                ]
            )
        return RunResult(
            policy=policy,
            trace=spec.name,
            logical_elements=self.n,
            active_limit=active_limit,
            peak_active_descriptors=peak_active,
            active_bound_respected=peak_active <= active_limit,
            issue_lines=issue_lines,
            issue_rows=issue_rows,
            issue_order_sha256=digest.hexdigest(),
            a_line_requests=len(issue_lines),
            unique_a_lines=len(set(issue_lines)),
            row_transitions=sum(
                left != right
                for left, right in zip(issue_rows, issue_rows[1:])
            ),
            epochs=epochs,
            capacity_drain_barriers=capacity_drains,
            partition_barriers=partition_barriers,
            b_passes=b_passes,
            b_scan_bytes=b_bytes_per_pass * b_passes,
            b_line_reads=b_lines_per_pass * b_passes,
            b_reread_bytes=b_bytes_per_pass * (b_passes - 1),
            b_reread_lines=b_lines_per_pass * (b_passes - 1),
            filter_words=filter_words,
            selector_cycles_lower_bound=ceil_div(
                filter_words, self.filter_words_per_cycle
            ),
            response_placements=sum(count > 0 for count in placement_counts),
            missing_placements=sum(count == 0 for count in placement_counts),
            duplicate_placements=sum(
                max(0, count - 1) for count in placement_counts
            ),
            output_hash=output_hash,
            reorder_metadata_lower_bound_bytes=metadata,
            partition_control_bytes=partition_control,
            mechanism_total_lower_bound_bytes=total,
            expected_stall_sources=expected_stalls,
        )

    def run(self, policy: str, spec: TraceSpec) -> RunResult:
        if policy not in {"native16", "native4k", "bounded_rows"}:
            raise ValueError(f"unknown policy: {policy}")
        active_limit = self.n if policy == "native16" else self.k
        issue_lines: list[int] = []
        issue_rows: list[int] = []
        placement_counts = [
            0
        ] * self.n  # validation oracle, not mechanism state
        outputs = [0] * self.n  # architectural C oracle, not mechanism state
        peak_active = 0
        epochs = 0
        capacity_drains = 0

        def consume(buffer: list[Record]) -> None:
            nonlocal peak_active, epochs
            if not buffer:
                return
            if len(buffer) > active_limit:
                raise AssertionError(
                    "policy overflowed its active descriptor bound"
                )
            peak_active = max(peak_active, len(buffer))
            lines, rows = self._issue_epoch(buffer)
            issue_lines.extend(lines)
            issue_rows.extend(rows)
            for record in buffer:
                placement_counts[record.itr] += 1
                outputs[record.itr] = record.index * 17 + 3
            epochs += 1

        if policy == "native16":
            consume(list(self.records(spec)))
            b_passes = 1
            partition_barriers = 0
        elif policy == "native4k":
            buffer: list[Record] = []
            for record in self.records(spec):
                buffer.append(record)
                if len(buffer) == active_limit:
                    consume(buffer)
                    buffer = []
            consume(buffer)
            b_passes = 1
            partition_barriers = self.partitions - 1
        else:
            for partition in range(self.partitions):
                buffer = []
                partition_epochs = 0
                for record in self.records(spec):
                    if self._row_partition(spec, record.row_key) != partition:
                        continue
                    buffer.append(record)
                    if len(buffer) == active_limit:
                        consume(buffer)
                        partition_epochs += 1
                        buffer = []
                if buffer:
                    consume(buffer)
                    partition_epochs += 1
                capacity_drains += max(0, partition_epochs - 1)
            b_passes = self.partitions
            partition_barriers = self.partitions - 1

        expected = [index * 17 + 3 for index in spec.indices(self.n)]
        if outputs != expected:
            raise AssertionError(
                "response placement did not reproduce C[i]=A[B[i]]"
            )
        result = self._finish(
            policy,
            spec,
            active_limit,
            issue_lines,
            issue_rows,
            epochs,
            capacity_drains,
            partition_barriers,
            b_passes,
            peak_active,
            placement_counts,
            outputs,
        )
        if result.missing_placements or result.duplicate_placements:
            raise AssertionError(
                "policy did not place every logical result exactly once"
            )
        return result


def compare(model: Model, spec: TraceSpec) -> dict[str, object]:
    runs = {
        policy: model.run(policy, spec)
        for policy in ("native16", "native4k", "bounded_rows")
    }
    baseline = runs["native16"].issue_lines
    ordering: dict[str, dict[str, object]] = {}
    for policy, run in runs.items():
        length = max(len(baseline), len(run.issue_lines))
        same = sum(a == b for a, b in zip(baseline, run.issue_lines))
        prefix = 0
        for a, b in zip(baseline, run.issue_lines):
            if a != b:
                break
            prefix += 1
        ordering[policy] = {
            "request_count": len(run.issue_lines),
            "same_position_fraction_vs_native16": 1.0
            if length == 0
            else same / length,
            "common_prefix_requests_vs_native16": prefix,
        }
    candidate = runs["bounded_rows"]
    control = runs["native4k"]
    strict_a_improvement = candidate.a_line_requests < control.a_line_requests
    strict_row_improvement = (
        candidate.row_transitions < control.row_transitions
    )
    return {
        "trace": asdict(spec),
        "policies": {name: run.summary() for name, run in runs.items()},
        "ordering": ordering,
        "decision": {
            "strictly_reduces_a_requests_vs_native4k": strict_a_improvement,
            "strictly_reduces_row_transitions_vs_native4k": strict_row_improvement,
            "passes_trace_gate": strict_a_improvement
            and strict_row_improvement,
            "gem5_timing_claim": "not modeled",
        },
    }


DEFAULT_TRACES = (
    "tile_consumer_fp64",
    "virtual_index_random_fp32",
    "virtual_index_fanout_fp32",
    "virtual_index_same_line_fp32",
    "virtual_index_line_revisit_fp32",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", choices=DEFAULT_TRACES, action="append")
    parser.add_argument(
        "--logical-elements", type=int, default=LOGICAL_ELEMENTS
    )
    parser.add_argument("--active-elements", type=int, default=ACTIVE_ELEMENTS)
    parser.add_argument(
        "--filter-words-per-cycle", type=int, default=FILTER_WORDS_PER_CYCLE
    )
    args = parser.parse_args()
    model = Model(
        args.logical_elements,
        args.active_elements,
        args.filter_words_per_cycle,
    )
    names = args.trace or list(DEFAULT_TRACES)
    report = {
        "schema": 1,
        "model": {
            "logical_elements": model.n,
            "active_elements": model.k,
            "partitions": model.partitions,
            "index_bytes": INDEX_BYTES,
            "cache_line_bytes": CACHE_LINE_BYTES,
            "filter_words_per_cycle": model.filter_words_per_cycle,
            "selection": "static contiguous decoded-row aperture intervals",
            "issue_rule": "native-like first-seen row then first-seen line",
            "oracle_issue_order": False,
            "external_descriptor_spill_bytes": 0,
        },
        "comparisons": [
            compare(model, TraceSpec.named(name, model.n)) for name in names
        ],
        "scale_note": {
            "logical_elements": 65_536,
            "active_elements": 16_384,
            "partitions": 4,
            "b_scan_bytes": 1_048_576,
            "b_reread_bytes": 786_432,
            "b_line_reads": 16_384,
            "filter_words": 262_144,
            "selector_cycles_lower_bound_at_16_words_per_cycle": 16_384,
            "fp64_reorder_metadata_lower_bound_bytes": 279_040,
            "partition_control_bytes": 5,
            "implementation_status": "arithmetic mapping only; not implemented",
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
