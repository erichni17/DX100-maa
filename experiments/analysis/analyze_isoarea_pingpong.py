#!/usr/bin/env python3
"""Validate and summarize measured iso-area transparent-controller runs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

EVENT_RE = re.compile(
    r"^(?P<tick>\d+):.*?event=(?P<event>transparent_\w+)(?P<body>.*)$"
)


def fields(body: str) -> dict[str, int | str]:
    result: dict[str, int | str] = {}
    for word in body.split():
        if "=" not in word:
            continue
        key, value = word.split("=", 1)
        try:
            result[key] = int(value, 0)
        except ValueError:
            result[key] = value
    return result


def read_result(run: Path) -> dict[str, str]:
    with (run / "result.tsv").open(newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if len(rows) != 1:
        raise ValueError(f"{run}: expected one result row")
    return rows[0]


def analyze_run(run: Path) -> dict:
    trace_path = run / "run/virtual_trace.log"
    starts: dict[tuple[int, int], tuple[int, dict]] = {}
    intervals: list[dict] = []
    submit = retire = None
    for line in trace_path.read_text(errors="replace").splitlines():
        match = EVENT_RE.match(line)
        if not match:
            continue
        tick = int(match.group("tick"))
        event = match.group("event")
        values = fields(match.group("body"))
        if event == "transparent_submit":
            if submit is not None:
                raise ValueError(f"{run}: duplicate submit")
            submit = {"tick": tick, **values}
        elif event == "transparent_issue":
            key = (int(values["page"]), int(values["action"]))
            if key in starts:
                raise ValueError(f"{run}: duplicate issue {key}")
            starts[key] = (tick, values)
        elif event == "transparent_complete":
            key = (int(values["page"]), int(values["action"]))
            if key not in starts:
                raise ValueError(f"{run}: completion without issue {key}")
            start, issue = starts.pop(key)
            if values.get("transaction") != issue.get("transaction"):
                raise ValueError(f"{run}: transaction mismatch {key}")
            intervals.append(
                {
                    "page": key[0],
                    "action": key[1],
                    "start": start,
                    "end": tick,
                    "ticks": tick - start,
                    "element_offset": issue["element_offset"],
                    "elements": issue["elements"],
                    "src_slot": issue["src_slot"],
                    "dst_slot": issue["dst_slot"],
                }
            )
        elif event == "transparent_retire":
            retire = {"tick": tick, **values}
    if submit is None or retire is None or starts:
        raise ValueError(f"{run}: incomplete controller trace")

    chunks = int(submit["chunks"])
    expected = {
        (page, action) for page in range(chunks) for action in (1, 2, 3)
    }
    actual = {(item["page"], item["action"]) for item in intervals}
    if actual != expected:
        raise ValueError(f"{run}: action set mismatch")

    by_key = {(item["page"], item["action"]): item for item in intervals}
    for page in range(chunks):
        fill, compute, store = (by_key[(page, action)] for action in (1, 2, 3))
        if fill["end"] > compute["start"] or compute["end"] > store["start"]:
            raise ValueError(f"{run}: page {page} data hazard violated")

    def overlap(left: dict, right: dict) -> int:
        return max(
            0,
            min(left["end"], right["end"])
            - max(left["start"], right["start"]),
        )

    stream = sorted(
        (i for i in intervals if i["action"] in (1, 3)),
        key=lambda item: item["start"],
    )
    alu = sorted(
        (i for i in intervals if i["action"] == 2),
        key=lambda item: item["start"],
    )
    for name, lane in (("STREAM", stream), ("ALU", alu)):
        for previous, current in zip(lane, lane[1:]):
            if overlap(previous, current):
                raise ValueError(f"{run}: overlapping {name} intervals")

    cross_overlaps = []
    for compute in alu:
        for transfer in stream:
            ticks = overlap(compute, transfer)
            if ticks and compute["page"] != transfer["page"]:
                cross_overlaps.append(
                    {
                        "compute_page": compute["page"],
                        "stream_page": transfer["page"],
                        "stream_action": transfer["action"],
                        "ticks": ticks,
                    }
                )

    result = read_result(run)
    digest = hashlib.sha256(trace_path.read_bytes()).hexdigest()
    return {
        "case": result["case"],
        "output_hash": result["output_hash"],
        "simTicks": int(result["simTicks"]),
        "requests": {
            "source_reads": int(result["source_reads"]),
            "dram_reads": int(result["dram_reads"]),
            "write_issues": int(result["write_issues"]),
            "write_completions": int(result["write_completions"]),
        },
        "rows": {
            "inserted": int(result["row_table_rows_inserted"]),
            "unique": int(result["row_table_unique_rows"]),
            "dram_activates": int(result["dram_activates"]),
            "dram_precharges": int(result["dram_precharges"]),
        },
        "mode": int(submit["mode"]),
        "chunks": chunks,
        "chunk_elements": int(submit["chunk_elements"]),
        "submit_tick": int(submit["tick"]),
        "retire_tick": int(retire["tick"]),
        "descriptor_interval_ticks": int(retire["tick"]) - int(submit["tick"]),
        "stage_ticks": {
            "fill": sum(i["ticks"] for i in intervals if i["action"] == 1),
            "compute": sum(i["ticks"] for i in intervals if i["action"] == 2),
            "store": sum(i["ticks"] for i in intervals if i["action"] == 3),
        },
        "interval_envelope_overlap_ticks": sum(
            i["ticks"] for i in cross_overlaps
        ),
        "interval_envelope_overlaps": cross_overlaps,
        "intervals": sorted(
            intervals, key=lambda item: (item["start"], item["action"])
        ),
        "trace_sha256": digest,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs=3, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    analyses = [analyze_run(run.resolve()) for run in args.runs]
    hashes = {item["output_hash"] for item in analyses}
    if len(hashes) != 1:
        raise SystemExit(f"exact-output mismatch: {sorted(hashes)}")
    payload = {"schema": 1, "exact_output_match": True, "runs": analyses}
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
