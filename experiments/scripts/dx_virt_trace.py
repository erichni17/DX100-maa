#!/usr/bin/env python3
"""Summarize a virtual-gather run as a validated macro-event timeline."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

COUNTERS = {
    "rt_full": "IND_NumRTFull",
    "build_rounds": "IND_VirtBuildRounds",
    "index_line_reads": "IND_VirtIndexLineReads",
    "index_line_high_water": "IND_VirtIndexLineHighWater",
    "index_words": "IND_VirtIndexWords",
    "index_word_high_water": "IND_VirtIndexWordHighWater",
    "index_filter_words": "IND_VirtIndexFilterWords",
    "index_filter_cycles": "IND_VirtIndexFilterCycles",
    "response_slot_high_water": "IND_VirtResponseSlotHighWater",
    "response_word_high_water": "IND_VirtResponseWordHighWater",
    "response_word_pool_stalls": "IND_VirtResponseWordPoolStalls",
    "combine_line_high_water": "IND_VirtCombineLineHighWater",
    "combine_word_high_water": "IND_VirtCombineWordHighWater",
    "full_line_writes": "IND_VirtFullLineWrites",
    "partial_writes": "IND_VirtPartialWrites",
    "write_issues": "IND_VirtWriteIssues",
    "write_completions": "IND_VirtWriteCompletions",
    "outstanding_write_high_water": "IND_VirtOutstandingWriteHighWater",
    "source_only_cycles": "IND_VirtPipelineCyclesSourceOnly",
    "write_only_cycles": "IND_VirtPipelineCyclesWriteOnly",
    "overlap_cycles": "IND_VirtPipelineCyclesOverlap",
    "idle_cycles": "IND_VirtPipelineCyclesIdle",
    "request_build_cycles": "IND_VirtRequestCyclesBuild",
    "request_source_flight_cycles": "IND_VirtRequestCyclesSourceFlight",
    "request_retained_cycles": "IND_VirtRequestCyclesRetained",
    "request_write_cycles": "IND_VirtRequestCyclesWrites",
    "request_final_drain_cycles": "IND_VirtRequestCyclesFinalDrain",
}
READ_COUNTERS = (
    "IND_LoadsCacheHitResponding",
    "IND_LoadsCacheHitAccessing",
    "IND_LoadsMemAccessing",
)


class TraceError(RuntimeError):
    pass


def parse_manifest(path: Path) -> dict[str, str]:
    values = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def parse_result(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if len(rows) != 1:
        raise TraceError(
            f"expected one result row in {path}, found {len(rows)}"
        )
    return rows[0]


def parse_first_stats(path: Path) -> tuple[dict[str, int], int, int]:
    if not path.is_file():
        raise TraceError(f"missing stats: {path}")
    active = False
    complete = False
    values = {key: 0 for key in COUNTERS}
    reads = 0
    sim_ticks = 0
    sim_insts = 0
    for line in path.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        if line.startswith("---------- Begin Simulation Statistics"):
            if active:
                raise TraceError("nested statistics sections")
            active = True
            continue
        if line.startswith("---------- End Simulation Statistics") and active:
            complete = True
            break
        if not active:
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        name, raw = fields[:2]
        try:
            value = int(float(raw))
        except ValueError:
            continue
        if name == "simTicks":
            sim_ticks = value
        elif name == "simInsts":
            sim_insts = value
        for key, suffix in COUNTERS.items():
            if name.endswith(suffix):
                values[key] += value
                break
        if any(name.endswith(suffix) for suffix in READ_COUNTERS):
            reads += value
    if not complete:
        raise TraceError("first statistics section is incomplete")
    if sim_ticks <= 0 or sim_insts <= 0:
        raise TraceError("first statistics section has no completed ROI")
    values["total_indirect_reads"] = reads
    values["source_reads"] = reads - values["index_line_reads"]
    return values, sim_ticks, sim_insts


def build_summary(case_dir: Path) -> dict:
    case_dir = case_dir.resolve(strict=True)
    manifest = parse_manifest(case_dir / "manifest.txt")
    result = parse_result(case_dir / "result.tsv")
    stats, sim_ticks, sim_insts = parse_first_stats(case_dir / "run/stats.txt")
    expected_words = int(manifest.get("elements", result.get("elements", "0")))
    pipeline_total = sum(
        stats[key]
        for key in (
            "source_only_cycles",
            "write_only_cycles",
            "overlap_cycles",
            "idle_cycles",
        )
    )
    invariants = {
        "completed_roi": sim_ticks > 0 and sim_insts > 0,
        "index_words_match": expected_words == 0
        or stats["index_words"] == expected_words,
        "source_reads_positive": stats["source_reads"] > 0,
        "writes_balanced": stats["write_issues"] > 0
        and stats["write_issues"] == stats["write_completions"],
        "no_spd_index_payload": int(result.get("spd_read_cycles", "0")) == 0,
    }
    if not all(invariants.values()):
        failed = [key for key, passed in invariants.items() if not passed]
        raise TraceError("mechanism invariants failed: " + ", ".join(failed))
    overlap_fraction = (
        stats["overlap_cycles"] / pipeline_total if pipeline_total else 0.0
    )
    return {
        "schema_version": 1,
        "case_dir": str(case_dir),
        "case": manifest,
        "performance": {"simTicks": sim_ticks, "simInsts": sim_insts},
        "timeline": [
            {
                "phase": "index_feeder",
                "cache_line_reads": stats["index_line_reads"],
                "words_delivered": stats["index_words"],
                "line_high_water": stats["index_line_high_water"],
                "word_high_water": stats["index_word_high_water"],
            },
            {
                "phase": "reorder_and_build",
                "build_rounds": stats["build_rounds"],
                "row_table_full_events": stats["rt_full"],
                "build_cycles": stats["request_build_cycles"],
            },
            {
                "phase": "source_fetch",
                "cache_line_reads": stats["source_reads"],
                "response_slot_high_water": stats["response_slot_high_water"],
                "response_word_high_water": stats["response_word_high_water"],
                "word_pool_stalls": stats["response_word_pool_stalls"],
            },
            {
                "phase": "backing_retirement",
                "write_issues": stats["write_issues"],
                "write_completions": stats["write_completions"],
                "full_line_writes": stats["full_line_writes"],
                "partial_writes": stats["partial_writes"],
                "combine_line_high_water": stats["combine_line_high_water"],
                "combine_word_high_water": stats["combine_word_high_water"],
            },
        ],
        "pipeline": {
            "source_only_cycles": stats["source_only_cycles"],
            "write_only_cycles": stats["write_only_cycles"],
            "overlap_cycles": stats["overlap_cycles"],
            "idle_cycles": stats["idle_cycles"],
            "overlap_fraction": overlap_fraction,
        },
        "invariants": invariants,
        "raw_counters": stats,
    }


def markdown(summary: dict) -> str:
    lines = [
        f"# Virtual Gather Trace: {summary['case'].get('case', summary['case_dir'])}",
        "",
        f"- simTicks: {summary['performance']['simTicks']}",
        f"- source/write overlap: {summary['pipeline']['overlap_fraction']:.1%}",
        "",
        "| Phase | Evidence |",
        "|---|---|",
    ]
    for phase in summary["timeline"]:
        name = phase["phase"]
        evidence = ", ".join(
            f"{key}={value}" for key, value in phase.items() if key != "phase"
        )
        lines.append(f"| {name} | {evidence} |")
    lines.extend(["", "All fail-closed mechanism invariants passed.", ""])
    return "\n".join(lines)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    try:
        summary = build_summary(args.case_dir)
    except (OSError, ValueError, TraceError) as exc:
        raise SystemExit(f"dx-virt-trace: {exc}") from exc
    encoded = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    if args.json:
        write(args.json, encoded)
    if args.markdown:
        write(args.markdown, markdown(summary))
    if not args.json and not args.markdown:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
