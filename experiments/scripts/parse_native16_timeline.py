#!/usr/bin/env python3
"""Extract and validate the matched native16 coarse MAA timeline."""

import argparse
import csv
import json
import re
from pathlib import Path

TRACE_PATTERN = re.compile(
    r"^(?P<tick>[0-9]+): global: (?P<unit_kind>[SIA])"
    r"\[(?P<unit>[0-9]+)\] (?P<edge>Start|End) "
    r"\[INSTR\[.* opcode\((?P<opcode>[^)]+)\)"
)
STAGES = {
    "STREAM_LD": "stream_load",
    "INDIR_LD_INDEX": "indirect_load",
    "ALU_SCALAR": "alu",
    "STREAM_ST": "stream_store",
}


def read_result(path: Path):
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(rows) != 1:
        raise ValueError(f"{path}: expected exactly one result row")
    return rows[0]


def parse_trace(path: Path):
    edges = {stage: {} for stage in STAGES.values()}
    identities = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            match = TRACE_PATTERN.match(line)
            if match is None or match["opcode"] not in STAGES:
                continue
            stage = STAGES[match["opcode"]]
            edge = match["edge"].lower()
            if edge in edges[stage]:
                raise ValueError(f"{path}: duplicate {stage} {edge}")
            identity = (match["unit_kind"], int(match["unit"]))
            if stage in identities and identities[stage] != identity:
                raise ValueError(f"{path}: {stage} changed execution unit")
            identities[stage] = identity
            edges[stage][edge] = int(match["tick"])

    for stage, interval in edges.items():
        if set(interval) != {"start", "end"}:
            raise ValueError(f"{path}: incomplete {stage} interval")
        if interval["start"] >= interval["end"]:
            raise ValueError(f"{path}: invalid {stage} interval")
        interval["duration_ticks"] = interval["end"] - interval["start"]
        interval["unit_kind"], interval["unit"] = identities[stage]
    return edges


def interval_overlap(left, right):
    return max(
        0,
        min(left["end"], right["end"]) - max(left["start"], right["start"]),
    )


def union_overlap(target, intervals):
    clipped = []
    for interval in intervals:
        start = max(target["start"], interval["start"])
        end = min(target["end"], interval["end"])
        if start < end:
            clipped.append((start, end))
    if not clipped:
        return 0
    ordered = sorted(clipped)
    current_start, current_end = ordered[0]
    total = 0
    for start, end in ordered[1:]:
        if start > current_end:
            total += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    return total + current_end - current_start


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeline-root", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-tsv", type=Path, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    timeline_root = args.timeline_root.resolve()
    reference_root = args.reference_root.resolve()
    timeline_result = read_result(timeline_root / "result.tsv")
    reference_result = read_result(reference_root / "result.tsv")
    for field in ("case", "output_hash", "simTicks"):
        if timeline_result[field] != reference_result[field]:
            raise ValueError(f"timeline/reference {field} mismatch")
    timeline_identity = (
        (timeline_root / "shared_checkpoint_identity.sha256")
        .read_text(encoding="utf-8")
        .split()[0]
    )
    reference_identity = (
        (reference_root / "shared_checkpoint_identity.sha256")
        .read_text(encoding="utf-8")
        .split()[0]
    )
    if timeline_identity != reference_identity:
        raise ValueError("timeline/reference checkpoint identity mismatch")

    intervals = parse_trace(timeline_root / "run" / "virtual_trace.log")
    producer = intervals["indirect_load"]
    alu = intervals["alu"]
    store = intervals["stream_store"]
    producer_consumer_overlap = union_overlap(producer, (alu, store))
    producer_store_overlap = interval_overlap(producer, store)
    store_after_producer = max(
        0, store["end"] - max(store["start"], producer["end"])
    )
    summary = {
        "schema": 1,
        "timeline_root": str(timeline_root),
        "reference_root": str(reference_root),
        "checkpoint_identity_sha256": timeline_identity,
        "case": timeline_result["case"],
        "output_hash": timeline_result["output_hash"],
        "simTicks": int(timeline_result["simTicks"]),
        "intervals": intervals,
        "native_producer_consumer_overlap_ticks": producer_consumer_overlap,
        "native_producer_stream_store_overlap_ticks": producer_store_overlap,
        "native_stream_store_after_producer_ticks": store_after_producer,
    }
    args.output_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    fields = ("stage", "start_tick", "end_tick", "duration_ticks")
    with args.output_tsv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fields, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        for stage in ("stream_load", "indirect_load", "alu", "stream_store"):
            interval = intervals[stage]
            writer.writerow(
                {
                    "stage": stage,
                    "start_tick": interval["start"],
                    "end_tick": interval["end"],
                    "duration_ticks": interval["duration_ticks"],
                }
            )


if __name__ == "__main__":
    main()
