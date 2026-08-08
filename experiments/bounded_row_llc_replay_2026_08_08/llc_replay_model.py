#!/usr/bin/env python3
"""Deterministic finite Row/Offset and coherent-LLC B-replay model.

The model consumes only ``dx100.physical_admission.v1`` records.  It compares
an idealized full-metadata arm, the implemented grow-modulo rescan policy, and
a prospective stable replay policy.  Replay deliberately uses the same
partition function and admission order as modulo; therefore any difference in
A traffic or ordering is a model failure rather than an alleged benefit.

This is a work/traffic model.  It does not predict gem5 ticks and cannot turn
the separately recorded offline balanced-range oracle into online hardware.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence


SCHEMA = "dx100.physical_admission.v1"
LOGICAL_ELEMENTS = 16_384
SOURCE_ELEMENTS = 131_072
PARTITIONS = 4
LINE_BYTES = 64
FINITE_OFFSETS = 4_096
FINITE_ROWS_PER_SLICE = 32
FULL_OFFSETS = 16_384
# A true 16K Row window has 16K line slots. The earlier hybrid control had
# only 8K (16 slices * 64 row slots * 8 lines) and is kept as measured context.
FULL_ROWS_PER_SLICE = 128
LINES_PER_ROW_SLOT = 8
SLICES = 16
SLICE_ORDER = (0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15)
OBSERVED_MODULO_SIM_TICKS = 62_456_646
OBSERVED_MODULO_CPU_CYCLES = 199_542
OBSERVED_MODULO_FILTER_CYCLES = 4_101
FILTER_WORDS_PER_CYCLE = 16
MATERIALITY_THRESHOLD_PCT = 5.0


class ModelError(ValueError):
    """Fail-closed input or model invariant violation."""


def _strict_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ModelError(f"{field} must not be bool")
    if isinstance(value, int):
        return value
    if not isinstance(value, str) or not value:
        raise ModelError(f"{field} must be an integer string")
    try:
        return int(value, 0)
    except ValueError as error:
        raise ModelError(f"{field} is not an integer: {value!r}") from error


@dataclass(frozen=True)
class Record:
    itr: int
    b_value: int
    b_paddr: int
    a_line_paddr: int
    native_slice: int
    row: int
    grow: int
    wid: int

    @property
    def partition(self) -> int:
        return self.grow % PARTITIONS


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_trace(path: Path, expected_sha256: str) -> list[Record]:
    actual_sha256 = sha256_file(path)
    if actual_sha256 != expected_sha256:
        raise ModelError(
            f"raw trace SHA-256 mismatch: {actual_sha256} != {expected_sha256}"
        )
    records: list[Record] = []
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as error:
                raise ModelError(f"invalid JSON at line {line_number}") from error
            if raw.get("schema") != SCHEMA or raw.get("event") != "physical_admission":
                raise ModelError(f"wrong physical schema/event at line {line_number}")
            record = Record(
                itr=_strict_int(raw.get("itr"), "itr"),
                b_value=_strict_int(raw.get("b_value"), "b_value"),
                b_paddr=_strict_int(raw.get("b_paddr"), "b_paddr"),
                a_line_paddr=_strict_int(raw.get("a_line_paddr"), "a_line_paddr"),
                native_slice=_strict_int(raw.get("native_slice"), "native_slice"),
                row=_strict_int(raw.get("row"), "row"),
                grow=_strict_int(raw.get("grow_addr"), "grow_addr"),
                wid=_strict_int(raw.get("wid"), "wid"),
            )
            if record.itr != len(records):
                raise ModelError(
                    f"iteration domain is not ascending/complete at {record.itr}"
                )
            if not 0 <= record.b_value < SOURCE_ELEMENTS:
                raise ModelError(f"B[{record.itr}] is outside the frozen source")
            if record.b_paddr % 4 or record.a_line_paddr % LINE_BYTES:
                raise ModelError(f"unaligned physical record at itr {record.itr}")
            if not 0 <= record.native_slice < SLICES:
                raise ModelError(f"invalid native slice at itr {record.itr}")
            if record.row != record.grow or not 0 <= record.wid < 8:
                raise ModelError(f"decoded row/grow/wid mismatch at itr {record.itr}")
            records.append(record)
    if len(records) != LOGICAL_ELEMENTS:
        raise ModelError(f"expected {LOGICAL_ELEMENTS} records, got {len(records)}")
    return records


def input_line_count(records: Sequence[Record]) -> int:
    return len({record.b_paddr // LINE_BYTES for record in records})


@dataclass
class RowSlot:
    grow: int
    lines: list[int]


class FiniteEpoch:
    """Fixed-geometry Row/Offset epoch with native first-free placement."""

    def __init__(self, offset_capacity: int, rows_per_slice: int):
        self.offset_capacity = offset_capacity
        self.rows_per_slice = rows_per_slice
        self.records: list[Record] = []
        self.rows: list[list[RowSlot]] = [[] for _ in range(SLICES)]

    def can_insert(self, record: Record) -> tuple[bool, str | None]:
        if len(self.records) >= self.offset_capacity:
            return False, "offset_capacity"
        slots = self.rows[record.native_slice]
        for slot in slots:
            if slot.grow == record.grow and record.a_line_paddr in slot.lines:
                return True, None
        for slot in slots:
            if slot.grow == record.grow and len(slot.lines) < LINES_PER_ROW_SLOT:
                return True, None
        if len(slots) >= self.rows_per_slice:
            return False, "row_capacity"
        return True, None

    def insert(self, record: Record) -> None:
        accepted, reason = self.can_insert(record)
        if not accepted:
            raise ModelError(f"insert without drain: {reason}")
        slots = self.rows[record.native_slice]
        for slot in slots:
            if slot.grow == record.grow and record.a_line_paddr in slot.lines:
                self.records.append(record)
                return
        for slot in slots:
            if slot.grow == record.grow and len(slot.lines) < LINES_PER_ROW_SLOT:
                slot.lines.append(record.a_line_paddr)
                self.records.append(record)
                return
        slots.append(RowSlot(record.grow, [record.a_line_paddr]))
        self.records.append(record)

    def issue_records(self) -> Iterator[Record]:
        by_line: dict[tuple[int, int, int], list[Record]] = {}
        for record in self.records:
            by_line.setdefault(
                (record.native_slice, record.grow, record.a_line_paddr), []
            ).append(record)
        for native_slice in SLICE_ORDER:
            for slot in self.rows[native_slice]:
                for line in slot.lines:
                    yield from by_line[(native_slice, slot.grow, line)]

    @property
    def line_requests(self) -> int:
        return sum(len(slot.lines) for rows in self.rows for slot in rows)

    @property
    def row_slots(self) -> int:
        return sum(len(rows) for rows in self.rows)

    @property
    def row_groups(self) -> int:
        return len(
            {
                (record.native_slice, record.grow)
                for record in self.records
            }
        )


@dataclass(frozen=True)
class Schedule:
    epochs: tuple[FiniteEpoch, ...]
    drain_causes: dict[str, int]
    issue_sha256: str
    placement_sha256: str

    @property
    def line_requests(self) -> int:
        return sum(epoch.line_requests for epoch in self.epochs)

    @property
    def row_groups(self) -> int:
        return sum(epoch.row_groups for epoch in self.epochs)

    @property
    def peak_offsets(self) -> int:
        return max(len(epoch.records) for epoch in self.epochs)

    @property
    def peak_row_slots(self) -> int:
        return max(epoch.row_slots for epoch in self.epochs)


def build_schedule(
    streams: Iterable[Sequence[Record]], offset_capacity: int, rows_per_slice: int
) -> Schedule:
    epochs: list[FiniteEpoch] = []
    drains = {"offset_capacity": 0, "row_capacity": 0, "partition_boundary": 0}
    placements: list[int] = []
    issue_digest = hashlib.sha256()
    for stream_number, stream in enumerate(streams):
        epoch = FiniteEpoch(offset_capacity, rows_per_slice)
        for record in stream:
            accepted, reason = epoch.can_insert(record)
            if not accepted:
                if not epoch.records or reason is None:
                    raise ModelError("record cannot fit in an empty finite epoch")
                epochs.append(epoch)
                drains[reason] += 1
                epoch = FiniteEpoch(offset_capacity, rows_per_slice)
                accepted, reason = epoch.can_insert(record)
                if not accepted:
                    raise ModelError(f"record remains illegal after drain: {reason}")
            epoch.insert(record)
            placements.append(record.itr)
        if epoch.records:
            epochs.append(epoch)
        if stream_number + 1:
            drains["partition_boundary"] += 1
    drains["partition_boundary"] = max(0, drains["partition_boundary"] - 1)
    if sorted(placements) != list(range(len(placements))):
        raise ModelError("schedule does not place every logical iteration exactly once")
    for epoch_number, epoch in enumerate(epochs):
        for record in epoch.issue_records():
            issue_digest.update(
                f"{epoch_number}:{record.native_slice}:{record.grow}:"
                f"{record.a_line_paddr:x}:{record.itr}\n".encode("ascii")
            )
    placement_digest = hashlib.sha256(
        b"".join(value.to_bytes(2, "little") for value in placements)
    ).hexdigest()
    return Schedule(tuple(epochs), drains, issue_digest.hexdigest(), placement_digest)


def partition_streams(records: Sequence[Record]) -> tuple[tuple[Record, ...], ...]:
    return tuple(
        tuple(record for record in records if record.partition == partition)
        for partition in range(PARTITIONS)
    )


def replay_traffic(
    records: Sequence[Record], record_bytes: int, current_partition: int = 0
) -> dict[str, int | str]:
    if record_bytes not in (4, 8):
        raise ModelError("only the audited packed32 and generic64 formats exist")
    if record_bytes == 4:
        if LOGICAL_ELEMENTS > (1 << 14) or SOURCE_ELEMENTS > (1 << 17):
            raise ModelError("packed32 exceeds the proven 14+17-bit format")
        record_format = "packed32_itr14_b17_one_spare_bit"
    else:
        record_format = "generic64_u32_itr_u32_b"
    queues = partition_streams(records)
    replay_populations = [
        len(queue) if partition != current_partition else 0
        for partition, queue in enumerate(queues)
    ]
    records_per_line = LINE_BYTES // record_bytes
    replay_lines = sum(
        math.ceil(population / records_per_line)
        for population in replay_populations
        if population
    )
    original_lines = input_line_count(records)
    # Full-line queue assembly avoids RFOs but requires one 64-byte tail buffer
    # per future partition. Coherence still charges the eventual dirty writeback.
    coherent_lines = original_lines + 3 * replay_lines
    fixed_backing_span = (PARTITIONS - 1) * LOGICAL_ELEMENTS * record_bytes
    return {
        "format": record_format,
        "record_bytes": record_bytes,
        "current_partition": current_partition,
        "replay_records": sum(replay_populations),
        "replay_populations": replay_populations,
        "valid_backing_bytes": sum(replay_populations) * record_bytes,
        "fixed_backing_address_span_bytes": fixed_backing_span,
        "queue_tail_buffer_bytes": (PARTITIONS - 1) * LINE_BYTES,
        "original_b_read_lines": original_lines,
        "backing_full_line_stores": replay_lines,
        "backing_replay_read_lines": replay_lines,
        "eventual_dirty_writeback_lines": replay_lines,
        "coherent_line_transfers": coherent_lines,
        "semantic_original_b_read_bytes": len(records) * 4,
        "semantic_backing_write_bytes": sum(replay_populations) * record_bytes,
        "semantic_backing_read_bytes": sum(replay_populations) * record_bytes,
    }


def schedule_summary(schedule: Schedule) -> dict[str, object]:
    return {
        "epochs": len(schedule.epochs),
        "epoch_populations": [len(epoch.records) for epoch in schedule.epochs],
        "drain_causes": schedule.drain_causes,
        "peak_offsets": schedule.peak_offsets,
        "peak_row_slots": schedule.peak_row_slots,
        "a_line_requests": schedule.line_requests,
        "dram_row_groups": schedule.row_groups,
        "issue_order_sha256": schedule.issue_sha256,
        "placement_order_sha256": schedule.placement_sha256,
    }


def evaluate(records: Sequence[Record], trace_sha256: str) -> dict[str, object]:
    partitions = partition_streams(records)
    full = build_schedule([records], FULL_OFFSETS, FULL_ROWS_PER_SLICE)
    modulo = build_schedule(partitions, FINITE_OFFSETS, FINITE_ROWS_PER_SLICE)
    replay = build_schedule(partitions, FINITE_OFFSETS, FINITE_ROWS_PER_SLICE)
    if modulo.issue_sha256 != replay.issue_sha256:
        raise ModelError("stable replay changed modulo A issue order")
    if modulo.placement_sha256 != replay.placement_sha256:
        raise ModelError("stable replay changed modulo logical placement order")
    modulo_b_lines = PARTITIONS * input_line_count(records)
    packed = replay_traffic(records, 4)
    generic = replay_traffic(records, 8)
    replay_selector_records = len(records) + int(packed["replay_records"])
    replay_selector_cycles = math.ceil(replay_selector_records / FILTER_WORDS_PER_CYCLE)
    zero_cost_filter_savings_cycles = max(
        0, OBSERVED_MODULO_FILTER_CYCLES - replay_selector_cycles
    )
    zero_cost_filter_latency_reduction_upper_bound_pct = round(
        100.0 * zero_cost_filter_savings_cycles / OBSERVED_MODULO_CPU_CYCLES, 6
    )
    zero_cost_filter_speedup_upper_bound_pct = round(
        100.0
        * (
            OBSERVED_MODULO_CPU_CYCLES
            / (OBSERVED_MODULO_CPU_CYCLES - zero_cost_filter_savings_cycles)
            - 1.0
        ),
        6,
    )
    for traffic in (packed, generic):
        traffic["delta_vs_modulo_b_line_transfers"] = (
            int(traffic["coherent_line_transfers"]) - modulo_b_lines
        )
        traffic["delta_vs_modulo_b_line_transfers_pct"] = round(
            100.0
            * (int(traffic["coherent_line_transfers"]) / modulo_b_lines - 1.0),
            6,
        )
    return {
        "schema": "dx100.bounded_row_llc_replay_model.v1",
        "evidence_class": "deterministic_work_and_traffic_model_no_timing_claim",
        "input": {
            "physical_schema": SCHEMA,
            "raw_trace_sha256": trace_sha256,
            "logical_elements": len(records),
            "source_elements": SOURCE_ELEMENTS,
            "b_input_lines": input_line_count(records),
        },
        "configuration": {
            "partitions": PARTITIONS,
            "partition_function": "grow_addr_mod_4",
            "finite_offsets": FINITE_OFFSETS,
            "finite_rows_per_slice": FINITE_ROWS_PER_SLICE,
            "full_offsets": FULL_OFFSETS,
            "full_rows_per_slice": FULL_ROWS_PER_SLICE,
            "slices": SLICES,
            "lines_per_row_slot": LINES_PER_ROW_SLOT,
            "line_bytes": LINE_BYTES,
            "ordering": "stable_input_then_native_slice_row_slot_line_slot",
        },
        "partition_populations": [len(partition) for partition in partitions],
        "arms": {
            "full_metadata": {
                **schedule_summary(full),
                "b_scans": 1,
                "b_read_lines": input_line_count(records),
                "backing_bytes": 0,
            },
            "bounded_modulo": {
                **schedule_summary(modulo),
                "b_scans": PARTITIONS,
                "b_read_lines": modulo_b_lines,
                "selector_words": PARTITIONS * len(records),
                "backing_bytes": 0,
            },
            "llc_replay": {
                **schedule_summary(replay),
                "original_b_scans": 1,
                "replay_subset_passes": PARTITIONS - 1,
                "selector_records": replay_selector_records,
                "selector_cycles_arithmetic_lower_bound": replay_selector_cycles,
                "a_order_exactly_matches_bounded_modulo": True,
                "packed32": packed,
                "generic64": generic,
            },
        },
        "gate": {
            "gem5_vertical_slice": False,
            "materiality_threshold_pct": MATERIALITY_THRESHOLD_PCT,
            "zero_cost_filter_only_latency_reduction_upper_bound_pct": (
                zero_cost_filter_latency_reduction_upper_bound_pct
            ),
            "zero_cost_filter_only_speedup_upper_bound_pct": (
                zero_cost_filter_speedup_upper_bound_pct
            ),
            "zero_cost_filter_cycles_eliminated_upper_bound": zero_cost_filter_savings_cycles,
            "reason": (
                "even a free replay engine can remove at most the unmatched selector "
                "cycles, below the materiality threshold; generic hardware records "
                "increase coherent B/backing line transfers, while packed32 is "
                "workload-specialized and leaves A requests, DRAM row groups, epochs, "
                "and ordering unchanged"
            ),
            "timing_or_speedup_claim": False,
            "observed_context": {
                "bounded_modulo_simTicks": OBSERVED_MODULO_SIM_TICKS,
                "bounded_modulo_cpu_cycles": OBSERVED_MODULO_CPU_CYCLES,
                "bounded_modulo_filter_cycles": OBSERVED_MODULO_FILTER_CYCLES,
                "filter_words_per_cycle": FILTER_WORDS_PER_CYCLE,
                "provenance": "0108d9b committed matched-oracle counters.tsv",
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--trace-sha256", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    records = load_trace(args.trace, args.trace_sha256)
    result = evaluate(records, args.trace_sha256)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
