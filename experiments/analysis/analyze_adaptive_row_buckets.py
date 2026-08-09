#!/usr/bin/env python3
"""Analyze finite 4K row/address-bucket policies for a 16K gather.

Source cache-line identifiers are deliberately treated as a locality proxy.
DRAM-row fields are emitted only when an authenticated physical-admission trace
matches every logical iteration and source index in the input.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SCHEMA = "dx100.adaptive_row_bucket_policy.v1"
PHYSICAL_SCHEMA = "dx100.physical_admission.v1"
FROZEN_XRAGE_20K_SHA256 = (
    "7cb86c456e11f32ea4664510c43b519af6fac3e3bfa1bc86f95f330ca230c136"
)
DEFAULT_LOGICAL_ELEMENTS = 16_384
DEFAULT_ACTIVE_CAPACITY = 4_096
DEFAULT_SOURCE_ELEMENTS = 2_097_152
DEFAULT_LINE_BYTES = 64
DEFAULT_SOURCE_WORD_BYTES = 8
DEFAULT_INDEX_WORD_BYTES = 4
DEFAULT_STATE_BUDGET_BYTES = 1_024


class AnalysisError(ValueError):
    """Input or evidence did not satisfy the fail-closed contract."""


@dataclass(frozen=True)
class Atom:
    lower: int
    upper: int
    members: tuple[int, ...]
    duplicate_ordinal: tuple[int, int] | None = None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AnalysisError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def read_json(path: Path) -> object:
    try:
        return json.loads(
            path.read_text(encoding="utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnalysisError(f"cannot parse {path}: {exc}") from exc


def load_pattern(
    path: Path,
    logical_elements: int = DEFAULT_LOGICAL_ELEMENTS,
    *,
    require_frozen: bool = True,
) -> tuple[list[int], dict[str, object]]:
    if logical_elements <= 0:
        raise AnalysisError("logical elements must be positive")
    digest = sha256(path)
    if require_frozen and digest != FROZEN_XRAGE_20K_SHA256:
        raise AnalysisError(
            f"frozen XRAGE input hash mismatch: {digest}, expected "
            f"{FROZEN_XRAGE_20K_SHA256}"
        )
    document = read_json(path)
    if type(document) is not list or len(document) != 1:
        raise AnalysisError(
            "XRAGE input must contain exactly one configuration"
        )
    config = document[0]
    if type(config) is not dict or set(config) != {
        "kernel",
        "pattern",
        "count",
    }:
        raise AnalysisError("XRAGE configuration has unexpected fields")
    if config["kernel"] != "Gather" or config["count"] != 1:
        raise AnalysisError("expected one Gather execution")
    raw_pattern = config["pattern"]
    if type(raw_pattern) is not list or len(raw_pattern) < logical_elements:
        raise AnalysisError("XRAGE pattern is shorter than the logical window")
    if any(type(value) is not int or value < 0 for value in raw_pattern):
        raise AnalysisError("XRAGE pattern must contain nonnegative integers")
    pattern = raw_pattern[:logical_elements]
    return pattern, {
        "path": str(path.resolve()),
        "sha256": digest,
        "frozen_sha256_expected": FROZEN_XRAGE_20K_SHA256,
        "frozen_sha256_match": digest == FROZEN_XRAGE_20K_SHA256,
        "configuration_count": 1,
        "kernel": "Gather",
        "declared_count": 1,
        "pattern_elements": len(raw_pattern),
        "analyzed_elements": logical_elements,
    }


def _strict_int(value: object, name: str) -> int:
    if type(value) is not str or not value:
        raise AnalysisError(f"{name} must be a nonempty integer string")
    try:
        return int(value, 0)
    except ValueError as exc:
        raise AnalysisError(f"{name} is not an integer: {value!r}") from exc


def load_physical_records(
    trace: Path,
    validation_path: Path,
    pattern: list[int],
    *,
    source_word_bytes: int = DEFAULT_SOURCE_WORD_BYTES,
) -> tuple[list[dict[str, int]], dict[str, object]]:
    """Authenticate a physical trace and bind it to the analyzed input."""

    validation = read_json(validation_path)
    if (
        type(validation) is not dict
        or validation.get("schema") != PHYSICAL_SCHEMA
    ):
        raise AnalysisError("physical validation has the wrong schema")
    records_meta = validation.get("records")
    if type(records_meta) is not dict:
        raise AnalysisError("physical validation lacks records metadata")
    expected_hash = records_meta.get("sha256")
    actual_hash = sha256(trace)
    if expected_hash != actual_hash:
        raise AnalysisError("physical trace SHA-256 does not match validation")
    if validation.get("record_count") != len(pattern):
        raise AnalysisError(
            "physical validation record count does not match window"
        )

    records: list[dict[str, int] | None] = [None] * len(pattern)
    base_addresses: set[int] = set()
    for line_no, raw in enumerate(
        trace.read_text(encoding="utf-8", errors="strict").splitlines(), 1
    ):
        try:
            value = json.loads(raw, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, AnalysisError) as exc:
            raise AnalysisError(
                f"{trace}:{line_no}: invalid JSON: {exc}"
            ) from exc
        if type(value) is not dict or value.get("schema") != PHYSICAL_SCHEMA:
            raise AnalysisError(f"{trace}:{line_no}: wrong physical schema")
        itr = _strict_int(value.get("itr"), f"{trace}:{line_no}:itr")
        if itr < 0 or itr >= len(pattern) or records[itr] is not None:
            raise AnalysisError(
                f"{trace}:{line_no}: duplicate/out-of-range itr {itr}"
            )
        b_value = _strict_int(
            value.get("b_value"), f"{trace}:{line_no}:b_value"
        )
        if b_value != pattern[itr]:
            raise AnalysisError(
                f"{trace}:{line_no}: b_value {b_value} does not match input "
                f"pattern[{itr}]={pattern[itr]}"
            )
        a_paddr = _strict_int(
            value.get("a_paddr"), f"{trace}:{line_no}:a_paddr"
        )
        base_addresses.add(a_paddr - b_value * source_word_bytes)
        records[itr] = {
            "channel": _strict_int(value.get("channel"), "channel"),
            "rank": _strict_int(value.get("rank"), "rank"),
            "bank_group": _strict_int(value.get("bank_group"), "bank_group"),
            "bank": _strict_int(value.get("bank"), "bank"),
            "row": _strict_int(value.get("row"), "row"),
            "a_line_paddr": _strict_int(
                value.get("a_line_paddr"), "a_line_paddr"
            ),
        }
    if any(record is None for record in records):
        raise AnalysisError(
            "physical trace does not cover every logical iteration"
        )
    if len(base_addresses) != 1:
        raise AnalysisError(
            "physical trace has no single A base-address mapping"
        )
    return [record for record in records if record is not None], {
        "status": "authenticated_matching_input",
        "schema": PHYSICAL_SCHEMA,
        "trace_path": str(trace.resolve()),
        "trace_sha256": actual_hash,
        "validation_path": str(validation_path.resolve()),
        "validation_sha256": sha256(validation_path),
        "record_count": len(records),
        "a_base_paddr": next(iter(base_addresses)),
        "dram_row_metrics_available": True,
        "metric_boundary": (
            "decoded admission coordinates; not Ramulator ACT/PRE command counts"
        ),
    }


def load_physical_diagnostic_records(
    trace: Path, validation_path: Path
) -> tuple[list[dict[str, int]], dict[str, object]]:
    """Authenticate a separate physical diagnostic without claiming input identity."""

    validation = read_json(validation_path)
    if (
        type(validation) is not dict
        or validation.get("schema") != PHYSICAL_SCHEMA
    ):
        raise AnalysisError(
            "physical diagnostic validation has the wrong schema"
        )
    records_meta = validation.get("records")
    if type(records_meta) is not dict or records_meta.get("sha256") != sha256(
        trace
    ):
        raise AnalysisError(
            "physical diagnostic trace SHA-256 does not match validation"
        )
    record_count = validation.get("record_count")
    if type(record_count) is not int or record_count <= 0:
        raise AnalysisError("physical diagnostic record count is invalid")
    records: list[dict[str, int] | None] = [None] * record_count
    for line_no, raw in enumerate(
        trace.read_text(encoding="utf-8", errors="strict").splitlines(), 1
    ):
        try:
            value = json.loads(raw, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, AnalysisError) as exc:
            raise AnalysisError(
                f"{trace}:{line_no}: invalid JSON: {exc}"
            ) from exc
        if type(value) is not dict or value.get("schema") != PHYSICAL_SCHEMA:
            raise AnalysisError(f"{trace}:{line_no}: wrong physical schema")
        itr = _strict_int(value.get("itr"), f"{trace}:{line_no}:itr")
        if itr < 0 or itr >= record_count or records[itr] is not None:
            raise AnalysisError(
                f"{trace}:{line_no}: duplicate/out-of-range itr {itr}"
            )
        records[itr] = {
            "grow_addr": _strict_int(value.get("grow_addr"), "grow_addr"),
            "a_line_paddr": _strict_int(
                value.get("a_line_paddr"), "a_line_paddr"
            ),
            "channel": _strict_int(value.get("channel"), "channel"),
            "rank": _strict_int(value.get("rank"), "rank"),
            "bank_group": _strict_int(value.get("bank_group"), "bank_group"),
            "bank": _strict_int(value.get("bank"), "bank"),
            "row": _strict_int(value.get("row"), "row"),
        }
    if any(record is None for record in records):
        raise AnalysisError(
            "physical diagnostic does not cover every logical iteration"
        )
    return [record for record in records if record is not None], {
        "status": "authenticated_separate_physical_diagnostic",
        "schema": PHYSICAL_SCHEMA,
        "trace_path": str(trace.resolve()),
        "trace_sha256": sha256(trace),
        "validation_path": str(validation_path.resolve()),
        "validation_sha256": sha256(validation_path),
        "record_count": record_count,
        "relationship_to_frozen_xrage": (
            "separate workload diagnostic; not substituted for frozen XRAGE input"
        ),
    }


def _b_scan_model(
    *,
    elements: int,
    index_word_bytes: int,
    line_bytes: int,
    selection_scans: int,
    replay_scans: int,
) -> dict[str, int]:
    lines = math.ceil(elements * index_word_bytes / line_bytes)
    total = selection_scans + replay_scans
    initial_scan = min(selection_scans, 1)
    recursive_scans = max(0, selection_scans - initial_scan)
    return {
        "selection_full_scans": selection_scans,
        "initial_first_pass_scans": initial_scan,
        "recursive_boundary_refinement_scans": recursive_scans,
        "replay_scans": replay_scans,
        "total_full_scans": total,
        "b_lines_per_full_scan": lines,
        "total_b_line_reads": total * lines,
        "repeated_b_line_reads": max(0, total - 1) * lines,
        "scan_cycles_lower_bound_at_one_word_per_cycle": total * elements,
        "first_pass_scan_cycles_lower_bound": initial_scan * elements,
        "recursive_refinement_scan_cycles_lower_bound": recursive_scans
        * elements,
        "replay_scan_cycles_lower_bound": replay_scans * elements,
    }


def _dram_stats(
    members: list[int], physical: list[dict[str, int]]
) -> dict[str, int]:
    identities = [
        (
            physical[index]["channel"],
            physical[index]["rank"],
            physical[index]["bank_group"],
            physical[index]["bank"],
            physical[index]["row"],
        )
        for index in members
    ]
    transitions = sum(
        left != right for left, right in zip(identities, identities[1:])
    )
    return {
        "unique_decoded_dram_rows": len(set(identities)),
        "decoded_row_transitions_in_logical_filter_order": transitions,
    }


def _reject(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def summarize_policy(
    *,
    name: str,
    assignments: list[int],
    keys: list[int],
    capacity: int,
    state: dict[str, object],
    scan: dict[str, object],
    boundary_selection: dict[str, object],
    policy_kind: str,
    declared_passes: int | None = None,
    source_lines_are_proxy: bool = True,
    physical: list[dict[str, int]] | None = None,
    forced_rejects: Iterable[dict[str, str]] = (),
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    if len(assignments) != len(keys) or any(
        type(item) is not int for item in assignments
    ):
        raise AnalysisError(f"{name}: invalid assignment vector")
    assigned_ids = sorted(set(assignments))
    pass_count = (
        declared_passes if declared_passes is not None else len(assigned_ids)
    )
    if pass_count <= 0 or any(
        value < 0 or value >= pass_count for value in assigned_ids
    ):
        raise AnalysisError(
            f"{name}: pass identifier lies outside declared passes"
        )
    pass_ids = list(range(pass_count))
    passes = []
    global_lines = set(keys)
    dram_union: set[tuple[int, int, int, int, int]] = set()
    sum_unique_rows = 0
    for pass_id in pass_ids:
        members = [
            index
            for index, value in enumerate(assignments)
            if value == pass_id
        ]
        pass_keys = [keys[index] for index in members]
        item: dict[str, object] = {
            "pass": pass_id,
            "population": len(members),
            "unique_source_lines": len(set(pass_keys)),
            "source_line_reuse_hits": len(members) - len(set(pass_keys)),
            "source_line_lower": min(pass_keys) if pass_keys else None,
            "source_line_upper_inclusive": max(pass_keys)
            if pass_keys
            else None,
        }
        if physical is not None:
            item["dram_rows"] = _dram_stats(members, physical)
            rows = {
                (
                    physical[index]["channel"],
                    physical[index]["rank"],
                    physical[index]["bank_group"],
                    physical[index]["bank"],
                    physical[index]["row"],
                )
                for index in members
            }
            dram_union.update(rows)
            sum_unique_rows += len(rows)
        passes.append(item)

    populations = [int(item["population"]) for item in passes]
    unique_per_pass = [int(item["unique_source_lines"]) for item in passes]
    rejects = list(forced_rejects)
    if not populations or sum(populations) != len(keys):
        rejects.append(
            _reject("coverage_failure", "passes do not cover the window once")
        )
    if populations and max(populations) > capacity:
        rejects.append(
            _reject(
                "active_metadata_capacity_exceeded",
                f"maximum pass population {max(populations)} exceeds {capacity}",
            )
        )
    result: dict[str, object] = {
        "name": name,
        "kind": policy_kind,
        "status": "ACCEPT" if not rejects else "REJECT",
        "accepted_for_hardware_bounded_execution": not rejects,
        "pass_count": len(passes),
        "pass_populations": populations,
        "maximum_pass_population": max(populations) if populations else 0,
        "capacity_elements": capacity,
        "passes": passes,
        "source_line_coalescing": {
            "proxy_only": source_lines_are_proxy,
            "global_unique_lines": len(global_lines),
            "sum_unique_lines_across_passes": sum(unique_per_pass),
            "cross_pass_duplicate_line_requests": sum(unique_per_pass)
            - len(global_lines),
            "population_minus_unique_line_requests": len(keys)
            - sum(unique_per_pass),
        },
        "policy_state": state,
        "b_scan_model": scan,
        "boundary_selection": boundary_selection,
        "reject_conditions": rejects,
    }
    if physical is not None:
        result["dram_row_summary"] = {
            "available": True,
            "global_unique_decoded_rows": len(dram_union),
            "sum_unique_decoded_rows_across_passes": sum_unique_rows,
            "cross_pass_duplicate_decoded_rows": sum_unique_rows
            - len(dram_union),
            "not_command_metrics": True,
        }
    else:
        result["dram_row_summary"] = None
    if extra:
        result.update(extra)
    return result


def finite_table_crosscheck(
    assignments: list[int],
    records: list[dict[str, int]],
    pass_count: int,
    *,
    capacity: int = DEFAULT_ACTIVE_CAPACITY,
) -> dict[str, object]:
    """Replay finite 16x32x8 row/line tables; this is not gem5 timing."""

    rows_per_slice = 32
    lines_per_row = 8
    line_word_limit = 480
    rows: list[list[dict[str, object]]] = [[] for _ in range(16)]
    offsets = 0
    epochs = 0
    a_line_requests = 0
    drain_reasons = {
        "row_slot_limit": 0,
        "offset_limit": 0,
        "line_word_limit": 0,
    }
    peak_rows = 0
    peak_offsets = 0
    peak_lines = 0

    def drain() -> None:
        nonlocal rows, offsets, epochs, a_line_requests
        if offsets == 0:
            return
        a_line_requests += sum(
            len(row["lines"]) for slice_rows in rows for row in slice_rows
        )
        rows = [[] for _ in range(16)]
        offsets = 0
        epochs += 1

    def insert(record: dict[str, int]) -> str | None:
        nonlocal offsets, peak_rows, peak_offsets, peak_lines
        slice_id = record["bank_group"] * 4 + record["bank"]
        slice_rows = rows[slice_id]
        exact_row = None
        grow_row = None
        for row in slice_rows:
            if row["grow"] != record["grow_addr"]:
                continue
            lines = row["lines"]
            if record["a_line_paddr"] in lines:
                exact_row = row
                break
            if len(lines) < lines_per_row and grow_row is None:
                grow_row = row
        if exact_row is not None:
            lines = exact_row["lines"]
            if lines[record["a_line_paddr"]] >= line_word_limit:
                return "line_word_limit"
            if offsets >= capacity:
                return "offset_limit"
            lines[record["a_line_paddr"]] += 1
        else:
            if grow_row is None:
                if len(slice_rows) >= rows_per_slice:
                    return "row_slot_limit"
                grow_row = {"grow": record["grow_addr"], "lines": {}}
                slice_rows.append(grow_row)
            if offsets >= capacity:
                return "offset_limit"
            grow_row["lines"][record["a_line_paddr"]] = 1
        offsets += 1
        peak_offsets = max(peak_offsets, offsets)
        peak_rows = max(peak_rows, sum(len(item) for item in rows))
        peak_lines = max(
            peak_lines,
            sum(len(row["lines"]) for item in rows for row in item),
        )
        return None

    for pass_id in range(pass_count):
        for index, record in enumerate(records):
            if assignments[index] != pass_id:
                continue
            reason = insert(record)
            if reason is not None:
                drain_reasons[reason] += 1
                drain()
                retry = insert(record)
                if retry is not None:
                    raise AnalysisError(
                        f"finite diagnostic record failed after {reason} drain: {retry}"
                    )
        drain()
    return {
        "evidence_class": (
            "analytical_finite_table_replay_on_authenticated_physical_records"
        ),
        "gem5_timing_evidence": False,
        "epochs": epochs,
        "capacity_drains": sum(drain_reasons.values()),
        "drain_reasons": drain_reasons,
        "a_line_requests": a_line_requests,
        "peak_offsets": peak_offsets,
        "peak_row_slots": peak_rows,
        "peak_line_slots": peak_lines,
        "geometry": {
            "slices": 16,
            "rows_per_slice": rows_per_slice,
            "lines_per_row": lines_per_row,
            "offset_entries": capacity,
            "line_entries": capacity,
            "line_word_limit": line_word_limit,
        },
    }


def analyze_physical_grow_diagnostic(
    records: list[dict[str, int]],
    *,
    capacity: int = DEFAULT_ACTIVE_CAPACITY,
    state_budget: int = DEFAULT_STATE_BUDGET_BYTES,
    index_word_bytes: int = DEFAULT_INDEX_WORD_BYTES,
    line_bytes: int = DEFAULT_LINE_BYTES,
) -> dict[str, object]:
    """Compare indivisible whole-grow packing with stable split four-pass ranges."""

    if len(records) != DEFAULT_LOGICAL_ELEMENTS:
        raise AnalysisError(
            "physical grow diagnostic requires exactly 16K records"
        )
    groups: dict[int, list[int]] = {}
    for index, record in enumerate(records):
        groups.setdefault(record["grow_addr"], []).append(index)
    ordered_groups = sorted(groups.items())
    if any(len(members) > capacity for _, members in ordered_groups):
        whole_group_rejects = [
            _reject(
                "indivisible_grow_exceeds_capacity",
                "at least one whole grow group exceeds active metadata capacity",
            )
        ]
    else:
        whole_group_rejects = []
    line_ids = [record["a_line_paddr"] // line_bytes for record in records]
    common = {"index_word_bytes": index_word_bytes, "line_bytes": line_bytes}

    static_assignments = [-1] * len(records)
    for group_index, (_grow, members) in enumerate(ordered_groups):
        target = min(group_index * 4 // len(ordered_groups), 3)
        for member in members:
            static_assignments[member] = target
    static_policy = summarize_policy(
        name="physical_static_four_unsplit_grow_ranges",
        assignments=static_assignments,
        keys=line_ids,
        capacity=capacity,
        declared_passes=4,
        source_lines_are_proxy=False,
        policy_kind="physical_diagnostic_static_whole_grow_ranges",
        state={
            "charged_bytes": 18,
            "components": {
                "three_grow_boundaries": 9,
                "scan_and_pass_state": 9,
            },
        },
        scan=_b_scan_model(
            elements=len(records), selection_scans=1, replay_scans=4, **common
        ),
        boundary_selection={
            "cycles": len(ordered_groups),
            "comparators": 2,
            "comparator_evaluations": 2 * len(ordered_groups),
            "mechanism": "four equal-count ranges of observed whole grow values",
        },
        physical=records,
        forced_rejects=whole_group_rejects,
    )

    whole_assignments = [-1] * len(records)
    whole_descriptors = []
    pass_id = 0
    population = 0
    current_grows: list[int] = []
    for grow, members in ordered_groups:
        if population and population + len(members) > capacity:
            whole_descriptors.append(
                {
                    "pass": pass_id,
                    "grow_values": current_grows,
                    "population": population,
                }
            )
            pass_id += 1
            population = 0
            current_grows = []
        for member in members:
            whole_assignments[member] = pass_id
        population += len(members)
        current_grows.append(grow)
    whole_descriptors.append(
        {
            "pass": pass_id,
            "grow_values": current_grows,
            "population": population,
        }
    )
    whole_passes = pass_id + 1
    histogram_bytes = 256 * 2
    whole_state = histogram_bytes + 8 * 7 + 6
    whole_rejects = list(whole_group_rejects)
    if whole_state > state_budget:
        whole_rejects.append(
            _reject(
                "selector_state_budget_exceeded",
                f"whole-grow selector state {whole_state} B exceeds {state_budget} B",
            )
        )
    whole_policy = summarize_policy(
        name="physical_variable_pass_whole_grow_packing",
        assignments=whole_assignments,
        keys=line_ids,
        capacity=capacity,
        declared_passes=whole_passes,
        source_lines_are_proxy=False,
        policy_kind="physical_diagnostic_online_indivisible_grow_packing",
        state={
            "charged_bytes": whole_state,
            "components": {
                "reused_256x16bit_histogram": histogram_bytes,
                "eight_whole_grow_pass_descriptors": 56,
                "scan_cursor_selected_count_pass_flags": 6,
            },
            "budget_bytes": state_budget,
        },
        scan=_b_scan_model(
            elements=len(records),
            selection_scans=1,
            replay_scans=whole_passes,
            **common,
        ),
        boundary_selection={
            "cycles": 256,
            "comparators": 2,
            "comparator_evaluations": 512,
            "mechanism": "greedy adjacent whole-grow packing from histogram counts",
        },
        physical=records,
        forced_rejects=whole_rejects,
        extra={"whole_grow_pass_descriptors": whole_descriptors},
    )

    whole_policy["finite_model_crosscheck"] = finite_table_crosscheck(
        whole_assignments, records, whole_passes, capacity=capacity
    )

    split_assignments = [-1] * len(records)
    split_descriptors = []
    tail_grow, tail_members = min(
        ordered_groups, key=lambda item: (len(item[1]), -item[0])
    )
    main_groups = [item for item in ordered_groups if item[0] != tail_grow]
    main_groups.sort(key=lambda item: (-len(item[1]), item[0]))
    pairs: list[list[tuple[int, list[int]]]] = []
    while main_groups:
        first = main_groups.pop(0)
        fitting = [
            (index, item)
            for index, item in enumerate(main_groups)
            if len(first[1]) + len(item[1]) <= capacity
        ]
        if not fitting:
            raise AnalysisError(
                "cannot pair physical grow groups within capacity"
            )
        mate_index, mate = min(
            fitting, key=lambda pair: (-len(pair[1][1]), pair[1][0])
        )
        main_groups.pop(mate_index)
        pairs.append([first, mate])
    if len(pairs) != 4:
        raise AnalysisError(
            "paired grow diagnostic did not produce four main pairs"
        )
    gaps = [
        capacity - sum(len(members) for _, members in pair) for pair in pairs
    ]
    if sum(gaps) != len(tail_members):
        raise AnalysisError(
            "tail grow population does not exactly fill pair gaps"
        )
    tail_begin = 0
    for pass_id, pair in enumerate(pairs):
        for grow, members in pair:
            for member in members:
                split_assignments[member] = pass_id
            split_descriptors.append(
                {
                    "pass": pass_id,
                    "grow_value": grow,
                    "stable_occurrence_begin": 0,
                    "stable_occurrence_end_exclusive": len(members),
                    "population": len(members),
                }
            )
        tail_end = tail_begin + gaps[pass_id]
        for member in tail_members[tail_begin:tail_end]:
            split_assignments[member] = pass_id
        split_descriptors.append(
            {
                "pass": pass_id,
                "grow_value": tail_grow,
                "stable_occurrence_begin": tail_begin,
                "stable_occurrence_end_exclusive": tail_end,
                "population": gaps[pass_id],
            }
        )
        tail_begin = tail_end
    split_state = histogram_bytes + 4 * 11 + 6
    split_rejects = []
    if split_state > state_budget:
        split_rejects.append(
            _reject(
                "selector_state_budget_exceeded",
                f"split-grow selector state {split_state} B exceeds {state_budget} B",
            )
        )
    split_policy = summarize_policy(
        name="physical_paired_grow_plus_tail_split_four_pass",
        assignments=split_assignments,
        keys=line_ids,
        capacity=capacity,
        declared_passes=4,
        source_lines_are_proxy=False,
        policy_kind="physical_diagnostic_online_paired_grow_plus_stable_tail_split",
        state={
            "charged_bytes": split_state,
            "components": {
                "reused_256x16bit_histogram": histogram_bytes,
                "four_range_and_occurrence_quota_descriptors": 44,
                "scan_cursor_selected_count_pass_flags": 6,
            },
            "budget_bytes": state_budget,
        },
        scan=_b_scan_model(
            elements=len(records), selection_scans=1, replay_scans=4, **common
        ),
        boundary_selection={
            "cycles": 292,
            "comparators": 2,
            "comparator_evaluations": 548,
            "mechanism": (
                "256-counter walk plus bounded best-fit pairing of eight main grows; "
                "stable logical-iteration occurrence quotas split only the tail grow"
            ),
        },
        physical=records,
        forced_rejects=split_rejects,
        extra={
            "split_grow_segments": split_descriptors,
            "main_pair_populations": [capacity - gap for gap in gaps],
            "tail_grow_value": tail_grow,
            "tail_occurrence_gaps": gaps,
        },
    )
    split_policy["finite_model_crosscheck"] = finite_table_crosscheck(
        split_assignments, records, 4, capacity=capacity
    )

    sequential_assignments = [
        index // capacity for index in range(len(records))
    ]
    sequential_policy = summarize_policy(
        name="physical_sequential_iteration_chunks",
        assignments=sequential_assignments,
        keys=line_ids,
        capacity=capacity,
        declared_passes=4,
        source_lines_are_proxy=False,
        policy_kind="physical_diagnostic_fixed_iteration_chunks",
        state={
            "charged_bytes": 5,
            "components": {"scan_cursor_selected_count_chunk_flags": 5},
        },
        scan=_b_scan_model(
            elements=len(records), selection_scans=0, replay_scans=1, **common
        ),
        boundary_selection={
            "cycles": 0,
            "comparators": 0,
            "comparator_evaluations": 0,
            "mechanism": "fixed 4096-iteration rollover",
        },
        physical=records,
    )
    sequential_policy["finite_model_crosscheck"] = finite_table_crosscheck(
        sequential_assignments, records, 4, capacity=capacity
    )

    return {
        "metric_boundary": (
            "authenticated decoded physical admission coordinates; separate from "
            "frozen XRAGE and not DRAM command/timing evidence"
        ),
        "record_count": len(records),
        "unique_grow_values": len(ordered_groups),
        "grow_populations": [
            {"grow_addr": grow, "population": len(members)}
            for grow, members in ordered_groups
        ],
        "unique_physical_a_lines": len(set(line_ids)),
        "policies": [
            static_policy,
            whole_policy,
            split_policy,
            sequential_policy,
        ],
        "whole_vs_split_replay_delta": {
            "whole_grow_replay_scans": whole_passes,
            "split_grow_replay_scans": 4,
            "extra_whole_grow_replay_scans": whole_passes - 4,
            "extra_whole_grow_b_line_reads": (whole_passes - 4)
            * math.ceil(len(records) * index_word_bytes / line_bytes),
            "extra_whole_grow_scan_cycles_lower_bound": (whole_passes - 4)
            * len(records),
        },
    }


def iteration_chunks(
    keys: list[int],
    capacity: int,
    *,
    physical: list[dict[str, int]] | None = None,
    **common: object,
) -> dict[str, object]:
    assignments = [index // capacity for index in range(len(keys))]
    cursor_bytes = max(1, math.ceil(math.ceil(math.log2(len(keys) + 1)) / 8))
    count_bytes = max(1, math.ceil(math.ceil(math.log2(capacity + 1)) / 8))
    state_bytes = cursor_bytes + count_bytes + 1
    return summarize_policy(
        name="iteration_chunks",
        assignments=assignments,
        keys=keys,
        capacity=capacity,
        policy_kind="online_fixed_iteration_range",
        state={
            "charged_bytes": state_bytes,
            "components": {
                "scan_cursor": cursor_bytes,
                "selected_count": count_bytes,
                "chunk_id_and_flags": 1,
            },
            "materialized_16k_labels": False,
        },
        scan=_b_scan_model(
            elements=len(keys), selection_scans=0, replay_scans=1, **common
        ),
        boundary_selection={
            "cycles": 0,
            "comparators": 0,
            "comparator_evaluations": 0,
            "mechanism": "counter rollover at fixed iteration capacity",
        },
        declared_passes=math.ceil(len(keys) / capacity),
        physical=physical,
    )


def static_range(
    keys: list[int],
    capacity: int,
    source_lines: int,
    buckets: int,
    *,
    physical: list[dict[str, int]] | None = None,
    **common: object,
) -> dict[str, object]:
    assignments = [
        min(key * buckets // source_lines, buckets - 1) for key in keys
    ]
    key_bytes = max(1, math.ceil(max(1, (source_lines - 1).bit_length()) / 8))
    rejects = []
    if any(key >= source_lines for key in keys):
        rejects.append(
            _reject(
                "source_domain_violation", "proxy line exceeds source domain"
            )
        )
    return summarize_policy(
        name="static_full_array_range",
        assignments=assignments,
        keys=keys,
        capacity=capacity,
        policy_kind="online_static_contiguous_address_range",
        state={
            "charged_bytes": (buckets - 1) * key_bytes + 5,
            "components": {
                "upper_exclusive_boundaries": (buckets - 1) * key_bytes,
                "scan_cursor_selected_count_pass_flags": 5,
            },
            "materialized_16k_labels": False,
        },
        scan=_b_scan_model(
            elements=len(keys),
            selection_scans=0,
            replay_scans=buckets,
            **common,
        ),
        boundary_selection={
            "cycles": 0,
            "comparators": buckets - 1,
            "comparator_evaluations": 0,
            "mechanism": "descriptor-derived equal source-array quarters",
        },
        declared_passes=buckets,
        forced_rejects=rejects,
        physical=physical,
        extra={
            "upper_exclusive_proxy_boundaries": [
                math.ceil(source_lines * index / buckets)
                for index in range(1, buckets)
            ]
        },
    )


def modulo(
    keys: list[int],
    capacity: int,
    buckets: int,
    *,
    physical: list[dict[str, int]] | None = None,
    **common: object,
) -> dict[str, object]:
    return summarize_policy(
        name="source_line_modulo",
        assignments=[key % buckets for key in keys],
        keys=keys,
        capacity=capacity,
        policy_kind="online_static_noncontiguous_address_bucket",
        state={
            "charged_bytes": 5,
            "components": {"scan_cursor_selected_count_pass_flags": 5},
            "materialized_16k_labels": False,
        },
        scan=_b_scan_model(
            elements=len(keys),
            selection_scans=0,
            replay_scans=buckets,
            **common,
        ),
        boundary_selection={
            "cycles": 0,
            "comparators": 0,
            "comparator_evaluations": 0,
            "mechanism": "low address bits; no divider for power-of-two buckets",
        },
        declared_passes=buckets,
        physical=physical,
    )


def exact_offline_quantile(
    keys: list[int],
    capacity: int,
    buckets: int,
    source_lines: int,
    state_budget: int,
    *,
    physical: list[dict[str, int]] | None = None,
    **common: object,
) -> dict[str, object]:
    ordered = sorted(keys)
    boundaries = [ordered[index * capacity - 1] for index in range(1, buckets)]
    assignments = [bisect.bisect_left(boundaries, key) for key in keys]
    key_bytes = max(1, math.ceil(max(1, (source_lines - 1).bit_length()) / 8))
    state_bytes = len(keys) * key_bytes + len(boundaries) * key_bytes + 2
    rejects = [
        _reject(
            "offline_oracle_boundary_selection",
            "exact sorted quantiles are an upper diagnostic, not an online policy",
        )
    ]
    if state_bytes > state_budget:
        rejects.append(
            _reject(
                "selector_state_budget_exceeded",
                f"materialized-key lower bound {state_bytes} B exceeds {state_budget} B",
            )
        )
    return summarize_policy(
        name="exact_offline_source_line_quantile",
        assignments=assignments,
        keys=keys,
        capacity=capacity,
        policy_kind="offline_upper_diagnostic_only",
        state={
            "charged_bytes_lower_bound": state_bytes,
            "components": {
                "materialized_source_line_keys": len(keys) * key_bytes,
                "quantile_boundaries": len(boundaries) * key_bytes,
                "scan_cursor": 2,
            },
            "sorter_control_and_datapath_bytes": "unmodeled",
            "materialized_16k_labels": False,
            "budget_bytes": state_budget,
        },
        scan=_b_scan_model(
            elements=len(keys),
            selection_scans=1,
            replay_scans=buckets,
            **common,
        ),
        boundary_selection={
            "cycles": None,
            "comparators": None,
            "comparator_evaluations": None,
            "mechanism": "host-language exact sort; intentionally not a hardware cycle model",
        },
        declared_passes=buckets,
        forced_rejects=rejects,
        physical=physical,
        extra={"upper_inclusive_proxy_boundaries": boundaries},
    )


def coarse_radix_range(
    keys: list[int],
    capacity: int,
    source_lines: int,
    radix_bits: int,
    counter_bytes: int,
    state_budget: int,
    *,
    physical: list[dict[str, int]] | None = None,
    **common: object,
) -> dict[str, object]:
    if radix_bits <= 0 or radix_bits > 10:
        raise AnalysisError("radix bits must be in [1, 10]")
    if counter_bytes <= 0 or capacity >= 1 << (8 * counter_bytes):
        raise AnalysisError(
            "histogram counters cannot represent capacity plus one"
        )
    key_bits = max(1, (source_lines - 1).bit_length())
    domain_high = 1 << key_bits
    if any(key < 0 or key >= source_lines for key in keys):
        raise AnalysisError(
            "source proxy key lies outside the configured domain"
        )

    atoms: list[Atom] = []
    histogram_scans = 0
    histogram_counter_walk_cycles = 0
    split_nodes = 0
    duplicate_fallback_keys = 0
    maximum_depth = 0

    def split(
        lower: int, upper: int, members: tuple[int, ...], depth: int
    ) -> None:
        nonlocal histogram_scans, histogram_counter_walk_cycles
        nonlocal split_nodes, duplicate_fallback_keys, maximum_depth
        maximum_depth = max(maximum_depth, depth)
        if len(members) <= capacity:
            atoms.append(Atom(lower, upper, members))
            return
        if upper - lower == 1:
            duplicate_fallback_keys += 1
            for begin in range(0, len(members), capacity):
                chunk = members[begin : begin + capacity]
                atoms.append(
                    Atom(lower, upper, chunk, (begin, begin + len(chunk)))
                )
            return
        span_bits = (upper - lower).bit_length() - 1
        consumed = min(radix_bits, span_bits)
        fanout = 1 << consumed
        width = (upper - lower) // fanout
        bins: list[list[int]] = [[] for _ in range(fanout)]
        for index in members:
            bins[(keys[index] - lower) // width].append(index)
        histogram_scans += 1
        histogram_counter_walk_cycles += fanout
        split_nodes += 1
        for bin_id, child in enumerate(bins):
            if child:
                child_lower = lower + bin_id * width
                split(
                    child_lower, child_lower + width, tuple(child), depth + 1
                )

    split(0, domain_high, tuple(range(len(keys))), 0)
    atoms.sort(
        key=lambda atom: (
            atom.lower,
            -1
            if atom.duplicate_ordinal is None
            else atom.duplicate_ordinal[0],
        )
    )

    packed: list[list[Atom]] = []
    current: list[Atom] = []
    current_population = 0

    def flush() -> None:
        nonlocal current, current_population
        if current:
            packed.append(current)
            current = []
            current_population = 0

    for atom in atoms:
        if atom.duplicate_ordinal is not None:
            flush()
            packed.append([atom])
            continue
        if current and current_population + len(atom.members) > capacity:
            flush()
        current.append(atom)
        current_population += len(atom.members)
    flush()

    assignments = [-1] * len(keys)
    descriptors = []
    for pass_id, pass_atoms in enumerate(packed):
        members = [member for atom in pass_atoms for member in atom.members]
        for member in members:
            if assignments[member] != -1:
                raise AnalysisError("coarse radix assigned an iteration twice")
            assignments[member] = pass_id
        descriptors.append(
            {
                "pass": pass_id,
                "lower_proxy_line": min(atom.lower for atom in pass_atoms),
                "upper_proxy_line_exclusive": max(
                    atom.upper for atom in pass_atoms
                ),
                "population": len(members),
                "stable_duplicate_ordinal": (
                    pass_atoms[0].duplicate_ordinal
                    if len(pass_atoms) == 1
                    else None
                ),
            }
        )
    if any(value < 0 for value in assignments):
        raise AnalysisError("coarse radix failed to assign every iteration")

    key_bytes = max(1, math.ceil(key_bits / 8))
    iter_bytes = max(1, math.ceil(math.ceil(math.log2(len(keys) + 1)) / 8))
    max_descriptor_slots = 2 * math.ceil(len(keys) / capacity)
    descriptor_bytes_each = 2 * key_bytes + 2 * iter_bytes + 1
    stack_levels = math.ceil(key_bits / radix_bits)
    stack_node_bytes = 2 * key_bytes + counter_bytes + 1
    state_components = {
        "reused_histogram_counters": (1 << radix_bits) * counter_bytes,
        "pass_descriptors": max_descriptor_slots * descriptor_bytes_each,
        "bounded_recursion_stack": stack_levels * stack_node_bytes,
        "scan_cursor_selected_count_pass_flags": iter_bytes
        + counter_bytes
        + 2,
    }
    state_bytes = sum(state_components.values())
    rejects = []
    if len(packed) > max_descriptor_slots:
        rejects.append(
            _reject(
                "pass_descriptor_capacity_exceeded",
                f"needed {len(packed)} descriptors, provisioned {max_descriptor_slots}",
            )
        )
    if state_bytes > state_budget:
        rejects.append(
            _reject(
                "selector_state_budget_exceeded",
                f"coarse policy state {state_bytes} B exceeds {state_budget} B",
            )
        )
    return summarize_policy(
        name="online_coarse_histogram_radix_range",
        assignments=assignments,
        keys=keys,
        capacity=capacity,
        policy_kind="online_finite_adaptive_contiguous_address_range",
        state={
            "charged_bytes": state_bytes,
            "components": state_components,
            "histogram_bins": 1 << radix_bits,
            "counter_bytes": counter_bytes,
            "pass_descriptor_slots": max_descriptor_slots,
            "recursion_stack_levels": stack_levels,
            "budget_bytes": state_budget,
            "materialized_16k_labels": False,
        },
        scan=_b_scan_model(
            elements=len(keys),
            selection_scans=histogram_scans,
            replay_scans=len(packed),
            **common,
        ),
        boundary_selection={
            "cycles": histogram_counter_walk_cycles,
            "comparators": 2,
            "comparator_evaluations": histogram_counter_walk_cycles * 2,
            "mechanism": (
                "one counter read per cycle; nonzero and packed-capacity comparisons"
            ),
            "histogram_split_nodes": split_nodes,
            "histogram_full_scans": histogram_scans,
            "maximum_recursive_depth": maximum_depth,
        },
        forced_rejects=rejects,
        physical=physical,
        extra={
            "range_descriptors": descriptors,
            "recursive_split_termination": {
                "terminated": True,
                "maximum_depth": maximum_depth,
                "configured_maximum_depth": stack_levels,
                "over_capacity_identical_key_fallback": "stable_iteration_ordinal_chunks",
                "duplicate_fallback_key_count": duplicate_fallback_keys,
                "proof": (
                    "each radix split consumes at least one key bit; at singleton "
                    "range, stable occurrence chunks have population <= capacity"
                ),
            },
        },
    )


def analyze(
    pattern: list[int],
    *,
    active_capacity: int = DEFAULT_ACTIVE_CAPACITY,
    source_elements: int = DEFAULT_SOURCE_ELEMENTS,
    line_bytes: int = DEFAULT_LINE_BYTES,
    source_word_bytes: int = DEFAULT_SOURCE_WORD_BYTES,
    index_word_bytes: int = DEFAULT_INDEX_WORD_BYTES,
    radix_bits: int = 8,
    counter_bytes: int = 2,
    state_budget: int = DEFAULT_STATE_BUDGET_BYTES,
    physical: list[dict[str, int]] | None = None,
) -> dict[str, object]:
    if len(pattern) <= 0 or active_capacity <= 0:
        raise AnalysisError("pattern and capacity must be positive")
    if (
        line_bytes <= 0
        or source_word_bytes <= 0
        or line_bytes % source_word_bytes
    ):
        raise AnalysisError("source word size must divide the cache-line size")
    if source_elements <= max(pattern):
        raise AnalysisError(
            "source element domain does not contain every index"
        )
    words_per_line = line_bytes // source_word_bytes
    source_lines = math.ceil(source_elements / words_per_line)
    keys = [value // words_per_line for value in pattern]
    buckets = math.ceil(len(pattern) / active_capacity)
    if buckets != 4:
        raise AnalysisError("this analyzer requires the true-4K 16K geometry")
    common = {"index_word_bytes": index_word_bytes, "line_bytes": line_bytes}
    policies = [
        iteration_chunks(keys, active_capacity, physical=physical, **common),
        static_range(
            keys,
            active_capacity,
            source_lines,
            buckets,
            physical=physical,
            **common,
        ),
        modulo(keys, active_capacity, buckets, physical=physical, **common),
        exact_offline_quantile(
            keys,
            active_capacity,
            buckets,
            source_lines,
            state_budget,
            physical=physical,
            **common,
        ),
        coarse_radix_range(
            keys,
            active_capacity,
            source_lines,
            radix_bits,
            counter_bytes,
            state_budget,
            physical=physical,
            **common,
        ),
    ]
    return {
        "schema": SCHEMA,
        "geometry": {
            "logical_elements": len(pattern),
            "active_reorder_metadata_elements": active_capacity,
            "minimum_capacity_passes": buckets,
            "source_elements": source_elements,
            "source_word_bytes": source_word_bytes,
            "source_lines": source_lines,
            "index_word_bytes": index_word_bytes,
            "cache_line_bytes": line_bytes,
            "b_lines_per_full_scan": math.ceil(
                len(pattern) * index_word_bytes / line_bytes
            ),
            "selector_state_budget_bytes": state_budget,
        },
        "proxy_observation": {
            "metric": "source cache-line identifier = B[i] // 8 FP64 words",
            "dram_row_proof": False,
            "unique_source_proxy_lines_full_window": len(set(keys)),
            "warning": (
                "source cache-line IDs do not encode controller mapping, channel, "
                "bank, or DRAM row"
            ),
        },
        "policies": policies,
        "claims": {
            "performance": False,
            "magic_oracle_boundaries": False,
            "core_maa_behavior_changed": False,
            "hardware_feasibility": (
                "selector state, scans, comparisons, skew fallback, and finite "
                "termination are modeled; timing/area synthesis is not"
            ),
        },
        "global_reject_conditions": [
            "any pass population exceeds active reorder metadata capacity",
            "any logical iteration is missing or assigned more than once",
            "selector state exceeds the configured byte budget",
            "boundary selection depends on an unmodeled offline oracle",
            "recursive splitting lacks singleton-key termination",
            "DRAM-row claims are requested without a matching authenticated physical trace",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--logical-elements", type=int, default=DEFAULT_LOGICAL_ELEMENTS
    )
    parser.add_argument(
        "--active-capacity", type=int, default=DEFAULT_ACTIVE_CAPACITY
    )
    parser.add_argument(
        "--source-elements", type=int, default=DEFAULT_SOURCE_ELEMENTS
    )
    parser.add_argument("--radix-bits", type=int, default=8)
    parser.add_argument("--counter-bytes", type=int, default=2)
    parser.add_argument(
        "--state-budget-bytes", type=int, default=DEFAULT_STATE_BUDGET_BYTES
    )
    parser.add_argument("--physical-trace", type=Path)
    parser.add_argument("--physical-validation", type=Path)
    parser.add_argument("--physical-diagnostic-trace", type=Path)
    parser.add_argument("--physical-diagnostic-validation", type=Path)
    parser.add_argument(
        "--physical-unavailable-note",
        default=(
            "no authenticated physical admission/grow/row trace matching the frozen "
            "XRAGE 20K input was provided"
        ),
    )
    parser.add_argument("--allow-unfrozen-input", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if (args.physical_trace is None) != (args.physical_validation is None):
        raise SystemExit(
            "physical trace and validation must be supplied together"
        )
    if (args.physical_diagnostic_trace is None) != (
        args.physical_diagnostic_validation is None
    ):
        raise SystemExit(
            "physical diagnostic trace and validation must be supplied together"
        )
    try:
        pattern, input_evidence = load_pattern(
            args.input,
            args.logical_elements,
            require_frozen=not args.allow_unfrozen_input,
        )
        physical = None
        if args.physical_trace is not None:
            physical, physical_evidence = load_physical_records(
                args.physical_trace, args.physical_validation, pattern
            )
        else:
            physical_evidence = {
                "status": "not_available_for_frozen_input",
                "dram_row_metrics_available": False,
                "reason": args.physical_unavailable_note,
            }
        result = analyze(
            pattern,
            active_capacity=args.active_capacity,
            source_elements=args.source_elements,
            radix_bits=args.radix_bits,
            counter_bytes=args.counter_bytes,
            state_budget=args.state_budget_bytes,
            physical=physical,
        )
        if args.physical_diagnostic_trace is not None:
            (
                diagnostic_records,
                diagnostic_evidence,
            ) = load_physical_diagnostic_records(
                args.physical_diagnostic_trace,
                args.physical_diagnostic_validation,
            )
            diagnostic = analyze_physical_grow_diagnostic(
                diagnostic_records,
                capacity=args.active_capacity,
                state_budget=args.state_budget_bytes,
            )
            diagnostic["provenance"] = diagnostic_evidence
            result["physical_grow_diagnostic"] = diagnostic
        else:
            result["physical_grow_diagnostic"] = {
                "status": "not_provided",
                "policies": [],
            }
    except AnalysisError as exc:
        raise SystemExit(
            f"adaptive row-bucket analysis failed: {exc}"
        ) from exc
    result["input"] = input_evidence
    result["physical_trace"] = physical_evidence
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
