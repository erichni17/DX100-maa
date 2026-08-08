#!/usr/bin/env python3
"""Deterministic finite Row/Offset and coherent-LLC descriptor-spool model.

The model consumes only ``dx100.physical_admission.v1`` records. It compares an
overprovisioned offline 16K ordering diagnostic, professor-style cached-B
rescans, stable partition replay, and a one-scan finite descriptor spool. The
requested four 4K runs are checked against physical Row geometry before the
model forms finite subruns and a bounded head merge. The offline diagnostic is
not the measured 64-row/slice native control and cannot establish its order.

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
CONFIGURED_MERGE_HEADS = 8
LINE_BYTES = 64
FINITE_OFFSETS = 4_096
FINITE_ROWS_PER_SLICE = 32
DIAGNOSTIC_OFFSETS = 16_384
# This deliberately overprovisioned offline ordering diagnostic avoids row
# drains on the accepted trace. It is not the measured 64-row/slice control.
DIAGNOSTIC_ROWS_PER_SLICE = 128
NATIVE_CONTROL_ROWS_PER_SLICE = 64
LINES_PER_ROW_SLOT = 8
SLICES = 16
SLICE_ORDER = (0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15)
AUG3_MODULO_SIM_TICKS = 62_456_646
AUG3_MODULO_CPU_CYCLES = 199_542
AUG3_MODULO_FILTER_CYCLES = 4_101
FILTER_WORDS_PER_CYCLE = 16
MATERIALITY_THRESHOLD_PCT = 5.0
AUG3_FULL_FILL_CYCLES = 26_209
AUG3_FULL_REQUEST_CYCLES = 113_320
AUG3_FULL_RT_FULL_EVENTS = 859
AUG3_FULL_BUILD_ROUNDS = 102
AUG3_MODULO_FILL_CYCLES = 75_308
AUG3_MODULO_REQUEST_CYCLES = 98_261
AUG3_MODULO_FILTER_VISITS = 65_537
TRACE_CONTROL_SIM_TICKS = 40_159_152
TRACE_CONTROL_FILL_CYCLES = 13_306
TRACE_CONTROL_REQUEST_CYCLES = 107_076
TRACE_CONTROL_RT_FULL_EVENTS = 845
TRACE_CONTROL_BUILD_ROUNDS = 103
DESCRIPTOR_RECORD_BYTES = 8
PHYSICAL_ADDRESS_BITS = 33
LLC_BUS_BYTES_PER_CYCLE = 32
LLC_HIT_LATENCY_CYCLES = 42
GENERAL_SUBRUN_UPPER_BOUND = (
    PARTITIONS * math.ceil(FINITE_OFFSETS / FINITE_ROWS_PER_SLICE)
)


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
            if record.a_line_paddr >= 1 << PHYSICAL_ADDRESS_BITS:
                raise ModelError(
                    f"A physical address exceeds {PHYSICAL_ADDRESS_BITS} bits "
                    f"at itr {record.itr}"
                )
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
    expected_itrs: list[int] = []
    issue_digest = hashlib.sha256()
    for stream_number, stream in enumerate(streams):
        epoch = FiniteEpoch(offset_capacity, rows_per_slice)
        for record in stream:
            expected_itrs.append(record.itr)
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
    if len(set(expected_itrs)) != len(expected_itrs):
        raise ModelError("schedule input contains duplicate logical iterations")
    if sorted(placements) != sorted(expected_itrs):
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


def descriptor_sort_key(record: Record) -> tuple[int, int, int, int]:
    return (
        SLICE_ORDER.index(record.native_slice),
        record.grow,
        record.a_line_paddr,
        record.itr,
    )


def build_descriptor_runs(
    records: Sequence[Record], run_records: int = FINITE_OFFSETS
) -> tuple[tuple[Record, ...], ...]:
    if run_records <= 0 or len(records) % run_records:
        raise ModelError("descriptor runs must exactly cover the logical input")
    runs = tuple(
        tuple(sorted(records[start : start + run_records], key=descriptor_sort_key))
        for start in range(0, len(records), run_records)
    )
    if len(runs) != PARTITIONS or any(len(run) > FINITE_OFFSETS for run in runs):
        raise ModelError("descriptor spool is not four finite 4K runs")
    return runs


def finite_descriptor_subruns(
    records: Sequence[Record],
) -> tuple[tuple[tuple[Record, ...], ...], tuple[Schedule, ...]]:
    """Split each nominal 4K chunk at its own finite row-pressure drains."""

    subruns: list[tuple[Record, ...]] = []
    schedules: list[Schedule] = []
    for start in range(0, len(records), FINITE_OFFSETS):
        schedule = build_schedule(
            [records[start : start + FINITE_OFFSETS]],
            FINITE_OFFSETS,
            FINITE_ROWS_PER_SLICE,
        )
        schedules.append(schedule)
        subruns.extend(
            tuple(sorted(epoch.records, key=descriptor_sort_key))
            for epoch in schedule.epochs
        )
    runs = tuple(subruns)
    if any(len(run) > FINITE_OFFSETS for run in runs):
        raise ModelError("finite descriptor run exceeds the Offset window")
    if sum(len(run) for run in runs) != len(records):
        raise ModelError("finite descriptor runs do not exactly cover the input")
    if len(runs) > CONFIGURED_MERGE_HEADS:
        raise ModelError(
            f"trace needs {len(runs)} merge heads, configured fail-closed "
            f"limit is {CONFIGURED_MERGE_HEADS}"
        )
    return runs, tuple(schedules)


def nominal_four_run_geometry(records: Sequence[Record]) -> dict[str, object]:
    """Check whether four fixed 4K chunks fit the 32-row/slice geometry."""

    chunks: list[dict[str, object]] = []
    for chunk_number, start in enumerate(range(0, len(records), FINITE_OFFSETS)):
        chunk = records[start : start + FINITE_OFFSETS]
        line_sets: dict[tuple[int, int], set[int]] = {}
        for record in chunk:
            line_sets.setdefault((record.native_slice, record.grow), set()).add(
                record.a_line_paddr
            )
        rows_by_slice = []
        for native_slice in range(SLICES):
            rows_by_slice.append(
                sum(
                    math.ceil(len(lines) / LINES_PER_ROW_SLOT)
                    for (slice_id, _), lines in line_sets.items()
                    if slice_id == native_slice
                )
            )
        chunks.append(
            {
                "chunk": chunk_number,
                "records": len(chunk),
                "row_groups": len(line_sets),
                "rows_by_slice": rows_by_slice,
                "total_row_slots_required": sum(rows_by_slice),
                "max_row_slots_in_one_slice": max(rows_by_slice),
                "fits_32_rows_per_slice": max(rows_by_slice)
                <= FINITE_ROWS_PER_SLICE,
            }
        )
    return {
        "requested_runs": PARTITIONS,
        "requested_records_per_run": FINITE_OFFSETS,
        "row_slots_available_total": SLICES * FINITE_ROWS_PER_SLICE,
        "row_slots_available_per_slice": FINITE_ROWS_PER_SLICE,
        "chunks": chunks,
        "hardware_legal": all(
            bool(chunk["fits_32_rows_per_slice"]) for chunk in chunks
        ),
    }


def merge_descriptor_runs(
    runs: Sequence[Sequence[Record]],
) -> tuple[Record, ...]:
    if not 1 <= len(runs) <= CONFIGURED_MERGE_HEADS:
        raise ModelError(
            f"k-way merge requires one to {CONFIGURED_MERGE_HEADS} finite runs"
        )
    heads = [0] * len(runs)
    merged: list[Record] = []
    while True:
        available = [
            index for index in range(len(runs))
            if heads[index] < len(runs[index])
        ]
        if not available:
            break
        selected = min(
            available,
            key=lambda index: descriptor_sort_key(runs[index][heads[index]]),
        )
        merged.append(runs[selected][heads[selected]])
        heads[selected] += 1
    if sorted(record.itr for record in merged) != list(range(len(merged))):
        raise ModelError("descriptor merge lost or duplicated result iterations")
    return tuple(merged)


def _stage_budget_comparison(
    total_cycles: int, baseline_cycles: int
) -> dict[str, float | int]:
    return {
        "analytical_stage_sum_cycles": total_cycles,
        "analytical_headroom_vs_aug3_modulo_cycles": baseline_cycles - total_cycles,
        "analytical_headroom_vs_aug3_modulo_stage_sum_pct": round(
            100.0 * (1.0 - total_cycles / baseline_cycles), 6
        ),
        "gem5_latency_claim": False,
    }


def descriptor_spool_model(records: Sequence[Record]) -> dict[str, object]:
    requested_four_run = nominal_four_run_geometry(records)
    runs, chunk_schedules = finite_descriptor_subruns(records)
    merged = merge_descriptor_runs(runs)
    mapping_digest = hashlib.sha256()
    issue_digest = hashlib.sha256()
    line_fanout: dict[int, int] = {}
    for record in merged:
        mapping_digest.update(
            f"{record.itr}:{record.wid}:{record.a_line_paddr:x}\n".encode("ascii")
        )
        issue_digest.update(
            f"{record.native_slice}:{record.grow}:{record.a_line_paddr:x}:"
            f"{record.itr}:{record.wid}\n".encode("ascii")
        )
        line_fanout[record.a_line_paddr] = line_fanout.get(record.a_line_paddr, 0) + 1

    descriptor_bytes = len(records) * DESCRIPTOR_RECORD_BYTES
    descriptor_lines = sum(
        math.ceil(len(run) * DESCRIPTOR_RECORD_BYTES / LINE_BYTES)
        for run in runs
    )
    descriptor_backing_bytes = descriptor_lines * LINE_BYTES
    transfer_cycles = math.ceil(
        descriptor_backing_bytes / LLC_BUS_BYTES_PER_CYCLE
    )
    baseline_cycles = AUG3_MODULO_FILL_CYCLES + AUG3_MODULO_REQUEST_CYCLES
    sensitivity: dict[str, object] = {}
    for records_per_cycle in (1, 2, 4):
        engine_cycles = math.ceil(len(records) / records_per_cycle)
        append_cycles = (
            max(engine_cycles, transfer_cycles)
            + len(runs) * LLC_HIT_LATENCY_CYCLES
        )
        merge_cycles = (
            max(engine_cycles, transfer_cycles) + LLC_HIT_LATENCY_CYCLES
        )
        queue_cycles = append_cycles + merge_cycles
        analytical_fill_budget = AUG3_FULL_FILL_CYCLES + queue_cycles
        serialized_writeback = transfer_cycles + LLC_HIT_LATENCY_CYCLES
        request_cases: dict[str, object] = {}
        for name, request_cycles in (
            ("aug3_modulo_request", AUG3_MODULO_REQUEST_CYCLES),
            ("aug3_full_control_request", AUG3_FULL_REQUEST_CYCLES),
        ):
            critical = analytical_fill_budget + request_cycles
            with_writeback = critical + serialized_writeback
            request_cases[name] = {
                "request_cycles": request_cycles,
                "stage_budget_without_serialized_writeback": _stage_budget_comparison(
                    critical, baseline_cycles
                ),
                "stage_budget_with_serialized_writeback": _stage_budget_comparison(
                    with_writeback, baseline_cycles
                ),
            }
        sensitivity[str(records_per_cycle)] = {
            "descriptor_records_per_cycle": records_per_cycle,
            "per_phase_engine_cycles": engine_cycles,
            "per_phase_llc_transfer_cycles": transfer_cycles,
            "append_run_startup_latency_cycles": (
                len(runs) * LLC_HIT_LATENCY_CYCLES
            ),
            "merge_startup_latency_cycles": LLC_HIT_LATENCY_CYCLES,
            "append_plus_merge_cycles": queue_cycles,
            "analytical_fill_budget_cycles": analytical_fill_budget,
            "analytical_fill_headroom_vs_aug3_modulo_cycles": (
                AUG3_MODULO_FILL_CYCLES - analytical_fill_budget
            ),
            "serialized_dirty_writeback_cycles": serialized_writeback,
            "request_cases": request_cases,
        }

    required_rate = next(
        rate
        for rate in (1, 2, 4)
        if sensitivity[str(rate)]["request_cases"]["aug3_full_control_request"]
        ["stage_budget_with_serialized_writeback"]
        ["analytical_headroom_vs_aug3_modulo_stage_sum_pct"]
        >= MATERIALITY_THRESHOLD_PCT
    )
    run_record_prefixes: list[int] = []
    run_base_byte_offsets: list[int] = []
    next_record = 0
    next_byte = 0
    for run in runs:
        run_record_prefixes.append(next_record)
        run_base_byte_offsets.append(next_byte)
        next_record += len(run)
        next_byte += math.ceil(
            len(run) * DESCRIPTOR_RECORD_BYTES / LINE_BYTES
        ) * LINE_BYTES
    return {
        "mechanism": "one_b_scan_finite_sorted_runs_bounded_head_merge",
        "requested_four_run_policy": {
            **requested_four_run,
            "decision": (
                "accept"
                if requested_four_run["hardware_legal"]
                else "reject_row_geometry_overflow"
            ),
        },
        "modeled_policy": "one_scan_finite_descriptor_subruns",
        "run_count": len(runs),
        "run_populations": [len(run) for run in runs],
        "run_formation": {
            "b_scans": 1,
            "merge_passes": 1,
            "nominal_4k_chunks": PARTITIONS,
            "subruns_per_nominal_chunk": [
                len(schedule.epochs) for schedule in chunk_schedules
            ],
            "general_subrun_upper_bound": GENERAL_SUBRUN_UPPER_BOUND,
            "configured_live_merge_head_limit": CONFIGURED_MERGE_HEADS,
            "overflow_behavior": "fail_closed_no_uncharged_hierarchical_merge",
            "row_pressure_drains": sum(
                schedule.drain_causes["row_capacity"]
                for schedule in chunk_schedules
            ),
            "offset_pressure_drains": sum(
                schedule.drain_causes["offset_capacity"]
                for schedule in chunk_schedules
            ),
            "row_slots_per_run": [
                epoch.row_slots
                for schedule in chunk_schedules
                for epoch in schedule.epochs
            ],
            "row_groups_per_run": [
                epoch.row_groups
                for schedule in chunk_schedules
                for epoch in schedule.epochs
            ],
            "max_row_slots_in_one_slice_per_run": [
                max(len(rows) for rows in epoch.rows)
                for schedule in chunk_schedules
                for epoch in schedule.epochs
            ],
        },
        "total_finite_records": sum(len(run) for run in runs),
        "record_format": {
            "bytes": DESCRIPTOR_RECORD_BYTES,
            "a_line_index_bits": 27,
            "logical_iteration_bits": 14,
            "wid_bits": 3,
            "used_bits": 44,
            "reserved_bits": 20,
            "address_contract": "33-bit physical byte address, 64-byte aligned",
        },
        "result_iteration_mapping": {
            "records": len(merged),
            "stable_itr_and_wid_embedded": True,
            "mapping_sha256": mapping_digest.hexdigest(),
            "max_a_line_fanout": max(line_fanout.values()),
            "unbounded_mapping_state": False,
        },
        "merge": {
            "heads": len(runs),
            "head_descriptor_bytes": len(runs) * DESCRIPTOR_RECORD_BYTES,
            "head_index_bytes": len(runs) * 2,
            "tail_index_bytes": len(runs) * 2,
            "head_valid_bytes": len(runs),
            "line_read_buffers_bytes": len(runs) * LINE_BYTES,
            "append_line_buffer_bytes": LINE_BYTES,
            "bounded_control_and_buffers_bytes": (
                len(runs) * DESCRIPTOR_RECORD_BYTES
                + len(runs) * 2
                + len(runs) * 2
                + len(runs)
                + len(runs) * LINE_BYTES
                + LINE_BYTES
            ),
            "queue_valid_record_prefixes": run_record_prefixes,
            "queue_base_byte_offsets_aligned": run_base_byte_offsets,
            "queue_head_indices_initial": [0] * len(runs),
            "queue_tail_indices_immutable": [len(run) for run in runs],
            "merged_mapping_records": len(merged),
            "coalesced_a_line_requests": len(line_fanout),
            "dram_row_groups": len({(record.native_slice, record.grow) for record in merged}),
            "issue_mapping_sha256": issue_digest.hexdigest(),
            "native_issue_order_relation": "unknown_not_reconstructed",
        },
        "traffic": {
            "original_b_scan_records": len(records),
            "original_b_read_lines": input_line_count(records),
            "classification_visits": len(records),
            "descriptor_append_records": len(records),
            "descriptor_valid_bytes": descriptor_bytes,
            "descriptor_backing_bytes_with_subrun_padding": (
                descriptor_backing_bytes
            ),
            "descriptor_padding_bytes": descriptor_backing_bytes - descriptor_bytes,
            "descriptor_append_line_bytes": descriptor_backing_bytes,
            "descriptor_append_lines": descriptor_lines,
            "descriptor_read_records": len(records),
            "descriptor_read_line_bytes": descriptor_backing_bytes,
            "descriptor_read_lines": descriptor_lines,
            "eventual_dirty_writeback_line_bytes": descriptor_backing_bytes,
            "eventual_dirty_writeback_lines": descriptor_lines,
            "coherent_line_transfers": input_line_count(records) + 3 * descriptor_lines,
            "llc_bus_bytes_per_cycle": LLC_BUS_BYTES_PER_CYCLE,
            "configured_llc_hit_latency_cycles": LLC_HIT_LATENCY_CYCLES,
        },
        "calibration_assumptions": {
            "one_scan_fill_anchor_cycles": AUG3_FULL_FILL_CYCLES,
            "one_scan_fill_anchor_source": "separate_aug3_full_control_calibration",
            "physical_trace_control_fill_cycles": TRACE_CONTROL_FILL_CYCLES,
            "physical_trace_control_not_used_as_stage_anchor": True,
            "finite_run_sort_uses_existing_row_offset_storage": True,
            "sort_emit_is_charged_at_descriptor_records_per_cycle": True,
            "row_slots_are_not_assumed_pre_sorted": True,
            "bounded_sort_selector_candidates_per_slice": (
                FINITE_ROWS_PER_SLICE * LINES_PER_ROW_SLOT
            ),
            "request_is_bracketed_not_predicted": True,
            "dirty_writeback_is_reported_both_off_and_on_critical_path": True,
        },
        "analytical_stage_budget_sensitivity": sensitivity,
        "required_records_per_cycle_for_analytical_5pct_stage_headroom": required_rate,
        "configured_bus_record_ceiling_per_cycle": (
            LLC_BUS_BYTES_PER_CYCLE // DESCRIPTOR_RECORD_BYTES
        ),
    }


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
    diagnostic = build_schedule(
        [records], DIAGNOSTIC_OFFSETS, DIAGNOSTIC_ROWS_PER_SLICE
    )
    modulo = build_schedule(partitions, FINITE_OFFSETS, FINITE_ROWS_PER_SLICE)
    replay = build_schedule(partitions, FINITE_OFFSETS, FINITE_ROWS_PER_SLICE)
    if modulo.issue_sha256 != replay.issue_sha256:
        raise ModelError("stable replay changed modulo A issue order")
    if modulo.placement_sha256 != replay.placement_sha256:
        raise ModelError("stable replay changed modulo logical placement order")
    modulo_b_lines = PARTITIONS * input_line_count(records)
    packed = replay_traffic(records, 4)
    generic = replay_traffic(records, 8)
    spool = descriptor_spool_model(records)
    replay_selector_records = len(records) + int(packed["replay_records"])
    replay_selector_cycles = math.ceil(replay_selector_records / FILTER_WORDS_PER_CYCLE)
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
        "schema": "dx100.bounded_row_llc_replay_model.v3",
        "evidence_class": (
            "deterministic_work_traffic_and_analytical_stage_budget_"
            "no_candidate_timing_claim"
        ),
        "input": {
            "physical_schema": SCHEMA,
            "raw_trace_sha256": trace_sha256,
            "logical_elements": len(records),
            "source_elements": SOURCE_ELEMENTS,
            "b_input_lines": input_line_count(records),
        },
        "evidence_sources": {
            "physical_trace_control_aug8": {
                "campaign_root": (
                    "/data1/nier/dx100-runs/2026-08-08-virtualization-sprint/"
                    "hybrid-control-explicit-0108d9b"
                ),
                "case": "native_direct_16k",
                "raw_trace_sha256": trace_sha256,
                "result_tsv_sha256": (
                    "63f85392c64d77c8f6a8fead906d6b5c482d63c56cd0830f16adabb95ef0237a"
                ),
                "stats_txt_sha256": (
                    "e2152f3a6971c55ef595de5affb254e40cdcece6f8004c580f49bdcc8ea13e9b"
                ),
                "simTicks": TRACE_CONTROL_SIM_TICKS,
                "fill_cycles": TRACE_CONTROL_FILL_CYCLES,
                "request_cycles": TRACE_CONTROL_REQUEST_CYCLES,
                "row_table_full_events": TRACE_CONTROL_RT_FULL_EVENTS,
                "virtual_build_rounds": TRACE_CONTROL_BUILD_ROUNDS,
                "rows_per_slice": NATIVE_CONTROL_ROWS_PER_SLICE,
                "offset_entries": LOGICAL_ELEMENTS,
                "role": "physical_record_and_geometry_source_not_stage_calibration",
            },
            "matched_timing_calibration_aug3": {
                "campaign_root": (
                    "/data1/nier/dx100-runs/2026-08-03-virtualization-"
                    "integration/bounded-matched-oracle-f281637"
                ),
                "role": "separate_matched_stage_budget_calibration",
                "full_control": {
                    "case": "hybrid_full_metadata",
                    "result_tsv_sha256": (
                        "a9ff96a2f68f9b7a39ca928b5a0d2312bd3de2f362142f0ef72bb54c880abd2c"
                    ),
                    "stats_txt_sha256": (
                        "f61aa5520dc4e16275f7c6bc0c2c54d8276f0766b21a7a7761fa418dbce36607"
                    ),
                    "simTicks": 51_504_776,
                    "fill_cycles": AUG3_FULL_FILL_CYCLES,
                    "request_cycles": AUG3_FULL_REQUEST_CYCLES,
                    "row_table_full_events": AUG3_FULL_RT_FULL_EVENTS,
                    "virtual_build_rounds": AUG3_FULL_BUILD_ROUNDS,
                    "rows_per_slice": NATIVE_CONTROL_ROWS_PER_SLICE,
                    "offset_entries": LOGICAL_ELEMENTS,
                },
                "bounded_modulo": {
                    "case": "bounded_modulo_4k",
                    "result_tsv_sha256": (
                        "05a4ed98474bcaa3f3bb7732674fe7ab66655f250ad1b817105656a5005d35e7"
                    ),
                    "stats_txt_sha256": (
                        "2663a08734b002530f1006730c9077f42699bc78f8eb11abb50fd3ba930f129c"
                    ),
                    "simTicks": AUG3_MODULO_SIM_TICKS,
                    "cpu_cycles": AUG3_MODULO_CPU_CYCLES,
                    "fill_cycles": AUG3_MODULO_FILL_CYCLES,
                    "request_cycles": AUG3_MODULO_REQUEST_CYCLES,
                    "filter_visits": AUG3_MODULO_FILTER_VISITS,
                    "filter_cycles": AUG3_MODULO_FILTER_CYCLES,
                },
                "delta_modulo_minus_full": {
                    "fill_cycles": (
                        AUG3_MODULO_FILL_CYCLES - AUG3_FULL_FILL_CYCLES
                    ),
                    "request_cycles": (
                        AUG3_MODULO_REQUEST_CYCLES - AUG3_FULL_REQUEST_CYCLES
                    ),
                    "fill_plus_request_cycles": (
                        AUG3_MODULO_FILL_CYCLES
                        + AUG3_MODULO_REQUEST_CYCLES
                        - AUG3_FULL_FILL_CYCLES
                        - AUG3_FULL_REQUEST_CYCLES
                    ),
                },
            },
        },
        "configuration": {
            "partitions": PARTITIONS,
            "partition_function": "grow_addr_mod_4",
            "finite_offsets": FINITE_OFFSETS,
            "finite_rows_per_slice": FINITE_ROWS_PER_SLICE,
            "diagnostic_offsets": DIAGNOSTIC_OFFSETS,
            "diagnostic_rows_per_slice": DIAGNOSTIC_ROWS_PER_SLICE,
            "native_control_offsets": LOGICAL_ELEMENTS,
            "native_control_rows_per_slice": NATIVE_CONTROL_ROWS_PER_SLICE,
            "slices": SLICES,
            "lines_per_row_slot": LINES_PER_ROW_SLOT,
            "line_bytes": LINE_BYTES,
            "diagnostic_ordering": (
                "offline_stable_input_then_fixed_slice_row_slot_line_slot"
            ),
            "descriptor_spool": {
                "record_bytes": DESCRIPTOR_RECORD_BYTES,
                "llc_bus_bytes_per_cycle": LLC_BUS_BYTES_PER_CYCLE,
                "llc_hit_latency_cycles": LLC_HIT_LATENCY_CYCLES,
                "configured_live_merge_heads": CONFIGURED_MERGE_HEADS,
                "general_subrun_upper_bound": GENERAL_SUBRUN_UPPER_BOUND,
            },
        },
        "partition_populations": [len(partition) for partition in partitions],
        "arms": {
            "unlimited_16k_ordering_diagnostic": {
                **schedule_summary(diagnostic),
                "b_scans": 1,
                "b_read_lines": input_line_count(records),
                "backing_bytes": 0,
                "offline_ordering_diagnostic": True,
                "actual_native_control": False,
                "one_epoch_is_measured": False,
                "model_rows_per_slice": DIAGNOSTIC_ROWS_PER_SLICE,
            },
            "bounded_modulo_geometry_model": {
                **schedule_summary(modulo),
                "b_scans": PARTITIONS,
                "b_read_lines": modulo_b_lines,
                "selector_words": PARTITIONS * len(records),
                "backing_bytes": 0,
                "timing_source": "none_geometry_only",
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
            "sorted_descriptor_spool": spool,
        },
        "gate": {
            "gem5_vertical_slice_recommended": False,
            "candidate_timing_measured": False,
            "policy_decision": (
                "reject_repeated_scans_and_reject_exact_four_run_policy; "
                "retain_eight_subrun_spool_as_untimed_followup"
            ),
            "materiality_threshold_pct": MATERIALITY_THRESHOLD_PCT,
            "reason": (
                "the separate Aug-3 matched calibration attributes +49,099 cycles "
                "to repeated-pass Fill and -15,059 to Request; the Aug-8 physical "
                "trace shows the requested four 4K sorted runs become eight "
                "subruns, while the two-record/cycle result remains an analytical "
                "cross-campaign stage budget, not gem5 latency"
            ),
            "analytical_followup": {
                "policy": "one_scan_eight_finite_descriptor_subruns",
                "minimum_assumed_sort_emit_and_merge_rate_records_per_cycle": 2,
                "requires_gem5_timing_before_any_latency_claim": True,
            },
            "timing_or_speedup_claim": False,
            "evidence_separation": {
                "geometry_source": "physical_trace_control_aug8",
                "analytical_stage_calibration_source": (
                    "matched_timing_calibration_aug3"
                ),
                "mixed_control_claim": False,
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
