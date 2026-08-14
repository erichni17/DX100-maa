#!/usr/bin/env python3
"""Reconstruct the frozen hybrid-RMW SoA/JIT trace model.

The script does not run gem5.  It validates two completed matrix directories,
pairs each A-line read issue with its write issue/response, reconstructs the
deterministic guest's per-A-line value-reference chains, and evaluates explicitly
optimistic context schedules and value-line cache orderings.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import (
    OrderedDict,
    defaultdict,
)
from pathlib import Path

ARMS = (
    "ordinary_native16",
    "ordinary_native4",
    "soa_metadata16_physical16",
    "soa_metadata16_physical4",
)
SOA_ARM = "soa_metadata16_physical4"
CONTEXTS = (1, 2, 4, 8, 16)
CACHE_LINES = (0, 4, 8, 16)
LOGICAL = 16384
TARGET_WORDS = 1024
OPERATIONS = 2


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def kv_file(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def matrix(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def first_sim_ticks(path: Path) -> int:
    for line in path.read_text().splitlines():
        match = re.match(r"simTicks\s+(\d+)\s", line)
        if match:
            return int(match.group(1))
    raise ValueError(f"no simTicks in {path}")


def result_fields(path: Path) -> dict[str, str]:
    result_lines = [
        line
        for line in path.read_text().splitlines()
        if line.startswith("HYBRID_RMW_SOA_RESULT ")
    ]
    if len(result_lines) != 1:
        raise ValueError(f"expected one result line in {path}")
    fields = {}
    for token in result_lines[0].split()[1:]:
        key, value = token.split("=", 1)
        fields[key] = value
    text = path.read_text()
    if (
        "ROI Ended" not in text
        or "m5_exit instruction encountered" not in text
    ):
        raise ValueError(f"missing terminal marker in {path}")
    if re.search(r"\b(panic|fatal|timeout)\b", text, re.IGNORECASE):
        raise ValueError(f"failure marker in {path}")
    if (
        fields["errors"] != "0"
        or fields["output_hash"] != fields["expected_hash"]
    ):
        raise ValueError(f"correctness failure in {path}")
    return fields


def parse_event(line: str) -> tuple[int, dict[str, str]] | None:
    match = re.match(r"^(\d+): global: (.*)$", line)
    if not match or "event=" not in match.group(2):
        return None
    fields = {}
    for token in match.group(2).split():
        if "=" in token:
            key, value = token.split("=", 1)
            fields[key] = value
    return int(match.group(1)), fields


def guest_selected(operation: int) -> list[tuple[int, int]]:
    """Return (logical iteration, target line) in guest source order."""
    result = []
    for logical_itr in range(LOGICAL):
        order_slot = logical_itr & 63
        if order_slot < 4:
            index = 7
            selected = True
        elif logical_itr % 97 == 17:
            index = 9
            selected = False
        else:
            index = 32 + (
                (logical_itr * 37 + operation * 19) % (TARGET_WORDS - 32)
            )
            selected = ((logical_itr + operation * 3) % 11) != 0
        if selected:
            result.append((logical_itr, index // 16))
    return result


def guest_groups(operation: int) -> dict[int, list[int]]:
    groups: dict[int, list[int]] = defaultdict(list)
    for logical_itr, target_line in guest_selected(operation):
        groups[target_line].append(logical_itr)
    return dict(groups)


def trace_intervals(
    path: Path,
) -> tuple[list[dict], list[dict], list[dict], dict[str, float]]:
    active: dict[tuple[int, str], dict] = {}
    intervals = []
    stage_summaries = []
    complete = []
    index_active = set()
    index_issue_ticks = {}
    index_latencies = []
    index_high_water = 0
    for line in path.read_text().splitlines():
        parsed = parse_event(line)
        if parsed is None:
            continue
        tick, fields = parsed
        event = fields["event"]
        if event == "soa_jit_a_read_issue":
            key = (int(fields["generation"]), fields["addr"])
            if key in active:
                raise ValueError(f"duplicate active A line {key}")
            active[key] = {
                "generation": key[0],
                "operation": key[0] - 1,
                "operation_tick": int(fields["operation_tick"]),
                "a_addr": key[1],
                "head": int(fields["head"]),
                "aliases": int(fields["aliases"]),
                "read_issue": tick,
            }
        elif event == "soa_jit_a_write_issue":
            key = (int(fields["generation"]), fields["addr"])
            active[key]["write_issue"] = tick
        elif event == "soa_jit_a_write_response":
            key = (int(fields["generation"]), fields["addr"])
            record = active.pop(key)
            record["write_response"] = tick
            record["read_and_values_ticks"] = tick - record["read_issue"]
            record["write_response_ticks"] = tick - record["write_issue"]
            record["read_through_write_issue_ticks"] = (
                record["write_issue"] - record["read_issue"]
            )
            intervals.append(record)
        elif event == "index_line_issue":
            key = (fields["operation_tick"], fields["line"])
            if key in index_active:
                raise ValueError(f"duplicate active index line {key}")
            index_active.add(key)
            index_issue_ticks[key] = tick
            index_high_water = max(index_high_water, len(index_active))
        elif event == "index_line_response":
            key = (fields["operation_tick"], fields["line"])
            if key not in index_active:
                raise ValueError(f"unmatched index response {key}")
            index_active.remove(key)
            index_latencies.append(tick - index_issue_ticks.pop(key))
        elif event == "indirect_stage_summary":
            stage_summaries.append(
                {
                    key: int(value)
                    for key, value in fields.items()
                    if key.endswith("sim_ticks")
                }
            )
        elif event == "soa_jit_complete":
            complete.append({key: value for key, value in fields.items()})
    if active or index_active:
        raise ValueError("unclosed A or index intervals")
    index_summary = {
        "lines": len(index_latencies),
        "outstanding_high_water": index_high_water,
        "response_ticks_sum": sum(index_latencies),
        "response_ticks_mean": sum(index_latencies) / len(index_latencies),
        "response_ticks_min": min(index_latencies),
        "response_ticks_max": max(index_latencies),
    }
    return intervals, stage_summaries, complete, index_summary


def attach_guest_sequence(
    intervals: list[dict],
) -> list[tuple[int, int, int, int]]:
    """Return (operation, A target line, logical itr, value line) sequence."""
    sequence = []
    for operation in range(OPERATIONS):
        groups = guest_groups(operation)
        # With a full 16K fill and an initially empty OffsetTable, the entry ID
        # is the selected-iteration ordinal.  Thus each trace head identifies
        # the deterministic chain without relying on physical address layout.
        by_head = {}
        seen_target_lines = set()
        for selected_ordinal, (_, target_line) in enumerate(
            guest_selected(operation)
        ):
            if target_line not in seen_target_lines:
                by_head[selected_ordinal] = (target_line, groups[target_line])
                seen_target_lines.add(target_line)
        operation_intervals = [
            item for item in intervals if item["operation"] == operation
        ]
        if len(operation_intervals) != len(groups):
            raise ValueError("trace/guest A-line count mismatch")
        seen = set()
        for item in operation_intervals:
            if item["head"] not in by_head:
                raise ValueError(f"unmapped OffsetTable head {item['head']}")
            target_line, logical_itrs = by_head[item["head"]]
            if len(logical_itrs) != item["aliases"]:
                raise ValueError("trace/guest alias count mismatch")
            item["target_line"] = target_line
            item["first_logical_itr"] = logical_itrs[0]
            item["last_logical_itr"] = logical_itrs[-1]
            item["value_line_count"] = len({itr // 16 for itr in logical_itrs})
            item["logical_itrs"] = logical_itrs
            seen.add(target_line)
            sequence.extend(
                (operation, target_line, itr, itr // 16)
                for itr in logical_itrs
            )
        if seen != set(groups):
            raise ValueError("trace did not cover every deterministic A line")
    return sequence


def lpt(jobs: list[int], contexts: int) -> int:
    loads = [0] * contexts
    for job in sorted(jobs, reverse=True):
        slot = min(range(contexts), key=loads.__getitem__)
        loads[slot] += job
    return max(loads)


def lru_misses(lines: list[tuple[int, int]], capacity: int) -> int:
    if capacity == 0:
        return len(lines)
    resident: OrderedDict[tuple[int, int], None] = OrderedDict()
    misses = 0
    for line in lines:
        if line in resident:
            resident.move_to_end(line)
            continue
        misses += 1
        resident[line] = None
        if len(resident) > capacity:
            resident.popitem(last=False)
    return misses


def ordered_value_lines(
    intervals: list[dict], ordering: str
) -> list[tuple[int, int]]:
    result = []
    for operation in range(OPERATIONS):
        current = [
            item for item in intervals if item["operation"] == operation
        ]
        if ordering == "first_alias":
            current.sort(key=lambda item: item["first_logical_itr"])
        elif ordering != "trace":
            raise ValueError(ordering)
        for item in current:
            result.extend(
                (operation, itr // 16) for itr in item["logical_itrs"]
            )
    return result


def cache_table(
    intervals: list[dict],
) -> dict[str, dict[str, dict[str, float]]]:
    orderings = {
        "trace_row_offset": ordered_value_lines(intervals, "trace"),
        "first_alias_a_line": ordered_value_lines(intervals, "first_alias"),
        "source_stream_floor": [
            (operation, itr // 16)
            for operation in range(OPERATIONS)
            for itr, _ in guest_selected(operation)
        ],
    }
    result = {}
    for name, lines in orderings.items():
        per_cache = {}
        for capacity in CACHE_LINES:
            fills = lru_misses(lines, capacity)
            per_cache[str(capacity)] = {
                "physical_fills": fills,
                "fills_avoided_vs_no_cache": len(lines) - fills,
                "fill_reduction_fraction": (len(lines) - fills) / len(lines),
                "physical_fill_bytes": fills * 64,
            }
        result[name] = per_cache
    return result


def analyze(roots: list[Path], source: Path) -> dict:
    if len(roots) != 2:
        raise ValueError("exactly two matrix roots are required")
    root_records = []
    for root in roots:
        rows = matrix(root / "matrix.tsv")
        if tuple(row["arm"] for row in rows) != ARMS:
            raise ValueError(f"unexpected arms in {root}")
        manifest = kv_file(root / "manifest.txt")
        arms = {}
        for row in rows:
            arm = row["arm"]
            restore = root / "runs" / arm / "restore.log"
            stats = root / "runs" / arm / "stats.txt"
            fields = result_fields(restore)
            ticks = first_sim_ticks(stats)
            if ticks != int(row["simTicks"]):
                raise ValueError(f"matrix/stats simTicks mismatch for {arm}")
            if fields["output_hash"] != row["output_hash"]:
                raise ValueError(f"matrix/output hash mismatch for {arm}")
            arms[arm] = {"simTicks": ticks, "result": fields}
        soa_trace_hashes = {
            arm: sha256(root / "runs" / arm / "soa_jit_trace.log")
            for arm in (
                "soa_metadata16_physical16",
                "soa_metadata16_physical4",
            )
        }
        if len(set(soa_trace_hashes.values())) != 1:
            raise ValueError(f"physical-16/physical-4 traces differ in {root}")
        root_records.append(
            {
                "root": str(root),
                "manifest": manifest,
                "matrix_sha256": sha256(root / "matrix.tsv"),
                "soa_trace_sha256": soa_trace_hashes,
                "arms": arms,
            }
        )
    comparable = [
        {"manifest": record["manifest"], "arms": record["arms"]}
        for record in root_records
    ]
    if comparable[0] != comparable[1]:
        # Checkpoint paths are intentionally repetition-local.
        left = comparable[0]["manifest"].copy()
        right = comparable[1]["manifest"].copy()
        left.pop("soa_pair_checkpoint", None)
        right.pop("soa_pair_checkpoint", None)
        if left != right or comparable[0]["arms"] != comparable[1]["arms"]:
            raise ValueError("r1/r2 evidence differs")
    if (
        root_records[0]["soa_trace_sha256"]
        != root_records[1]["soa_trace_sha256"]
    ):
        raise ValueError("r1/r2 canonical traces differ")

    trace_path = roots[0] / "runs" / SOA_ARM / "soa_jit_trace.log"
    intervals, stages, complete, index_summary = trace_intervals(trace_path)
    sequence = attach_guest_sequence(intervals)
    if len(intervals) != 126 or len(sequence) != 29689:
        raise ValueError("unexpected reconstructed work count")
    if len(stages) != OPERATIONS or len(complete) != OPERATIONS:
        raise ValueError("unexpected terminal trace count")

    roi_ticks = root_records[0]["arms"][SOA_ARM]["simTicks"]
    service_ticks = sum(item["read_and_values_ticks"] for item in intervals)
    prewrite_ticks = sum(
        item["read_through_write_issue_ticks"] for item in intervals
    )
    write_response_ticks = sum(
        item["write_response_ticks"] for item in intervals
    )
    fill_ticks = sum(item["fill_sim_ticks"] for item in stages)
    build_ticks = sum(item["build_sim_ticks"] for item in stages)
    trace_total_ticks = sum(item["total_sim_ticks"] for item in stages)
    frontend_ticks = roi_ticks - trace_total_ticks
    pre_first_a_ticks = sum(
        min(
            item["read_issue"]
            for item in intervals
            if item["operation"] == operation
        )
        - min(
            item["operation_tick"]
            for item in intervals
            if item["operation"] == operation
        )
        for operation in range(OPERATIONS)
    )
    if service_ticks != sum(item["request_sim_ticks"] for item in stages):
        raise ValueError("service intervals do not cover request stages")
    if roi_ticks != fill_ticks + build_ticks + service_ticks + frontend_ticks:
        raise ValueError("ROI decomposition does not close")

    serial_ticks = fill_ticks + build_ticks + frontend_ticks
    scheduling = {}
    for contexts in CONTEXTS:
        lower_service = 0
        lpt_service = 0
        for operation in range(OPERATIONS):
            jobs = [
                item["read_and_values_ticks"]
                for item in intervals
                if item["operation"] == operation
            ]
            lower_service += max(
                (sum(jobs) + contexts - 1) // contexts, max(jobs)
            )
            lpt_service += lpt(jobs, contexts)
        scheduling[str(contexts)] = {
            "ideal_service_lower_ticks": lower_service,
            "lpt_service_ticks": lpt_service,
            "total_lower_ticks_with_fixed_serial": serial_ticks
            + lower_service,
            "total_lpt_ticks_with_fixed_serial": serial_ticks + lpt_service,
        }

    # Predicate responses are not individually traced.  Bracket a bounded
    # feeder-credit experiment by separating the measured index response work
    # from the unclassified remainder of the fill stage.  The index-only case
    # holds that remainder fixed; the dual-component case optimistically lets
    # both components scale perfectly but does not overlap the two streams.
    index_ticks = int(index_summary["response_ticks_sum"])
    nonindex_fill_ticks = fill_ticks - index_ticks
    feeder_projection = {}
    for credits in (1, 2, 4, 8):
        index_only = (
            nonindex_fill_ticks + (index_ticks + credits - 1) // credits
        )
        dual_component = (index_ticks + credits - 1) // credits + (
            nonindex_fill_ticks + credits - 1
        ) // credits
        feeder_projection[str(credits)] = {
            "index_only_fill_ticks": index_only,
            "dual_component_optimistic_fill_ticks": dual_component,
            "c8_lpt_total_with_index_only_fill_ticks": (
                frontend_ticks
                + build_ticks
                + scheduling["8"]["lpt_service_ticks"]
                + index_only
            ),
            "c8_lpt_total_with_dual_component_fill_ticks": (
                frontend_ticks
                + build_ticks
                + scheduling["8"]["lpt_service_ticks"]
                + dual_component
            ),
        }

    interval_output = []
    for item in intervals:
        public = {
            key: value for key, value in item.items() if key != "logical_itrs"
        }
        interval_output.append(public)
    sequence_text = "".join(
        f"{operation}\t{target_line}\t{itr}\t{value_line}\n"
        for operation, target_line, itr, value_line in sequence
    )
    return {
        "schema": "dx100.hybrid_rmw_soa_trace_model.v1",
        "units": "simTicks only",
        "source": {"path": str(source), "sha256": sha256(source)},
        "evidence": root_records,
        "measured": {
            "soa_roi_simTicks": roi_ticks,
            "a_line_intervals": len(intervals),
            "selected_value_reads": len(sequence),
            "a_line_reads": len(intervals),
            "a_line_writes": len(intervals),
            "fill_ticks": fill_ticks,
            "pre_first_a_ticks": pre_first_a_ticks,
            "pre_first_a_ticks_per_operation": [
                min(
                    item["read_issue"]
                    for item in intervals
                    if item["operation"] == operation
                )
                - min(
                    item["operation_tick"]
                    for item in intervals
                    if item["operation"] == operation
                )
                for operation in range(OPERATIONS)
            ],
            "post_fill_including_a_launch_ticks": service_ticks + build_ticks,
            "post_first_a_service_ticks": service_ticks,
            "build_ticks": build_ticks,
            "a_service_ticks": service_ticks,
            "a_read_plus_serial_value_ticks": prewrite_ticks,
            "a_write_response_ticks": write_response_ticks,
            "frontend_and_inter_instruction_ticks": frontend_ticks,
            "fixed_serial_ticks_for_projection": serial_ticks,
            "service_interval_min_ticks": min(
                item["read_and_values_ticks"] for item in intervals
            ),
            "service_interval_max_ticks": max(
                item["read_and_values_ticks"] for item in intervals
            ),
            "service_interval_mean_ticks": service_ticks / len(intervals),
            "direct_index_feeder": index_summary,
            "predicate_lines": sum(
                int(item["predicate_lines"].split("/")[0]) for item in complete
            ),
        },
        "optimistic_projection": {
            "context_scheduling": scheduling,
            "feeder_credits": feeder_projection,
            "value_line_lru": cache_table(intervals),
        },
        "reconstruction": {
            "sequence_rows": len(sequence),
            "sequence_columns": [
                "operation",
                "target_line",
                "logical_itr",
                "value_line",
            ],
            "sequence_sha256": hashlib.sha256(
                sequence_text.encode()
            ).hexdigest(),
            "intervals": interval_output,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("matrix_roots", nargs=2, type=Path)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("benchmarks/API/test_hybrid_rmw_soa.cpp"),
    )
    parser.add_argument(
        "--emit-sequence",
        type=Path,
        help="write the full reconstructed value-address sequence as TSV",
    )
    args = parser.parse_args()
    result = analyze(args.matrix_roots, args.source)
    if args.emit_sequence:
        intervals, _, _, _ = trace_intervals(
            args.matrix_roots[0] / "runs" / SOA_ARM / "soa_jit_trace.log"
        )
        sequence = attach_guest_sequence(intervals)
        with args.emit_sequence.open("w") as stream:
            stream.write("operation\ttarget_line\tlogical_itr\tvalue_line\n")
            for row in sequence:
                stream.write("\t".join(map(str, row)) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
