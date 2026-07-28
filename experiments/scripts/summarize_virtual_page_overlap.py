#!/usr/bin/env python3
"""Validate and summarize the virtual-page overlap experiment."""

import csv
import json
import sys
from pathlib import Path

CASES = ("native_16k", "paged_4k", "paged_overlap_4k")
VIRTUAL_INVARIANTS = (
    "output_hash",
    "index_words",
    "index_hwm",
    "indirect_spd_reads",
    "pages_ready",
    "pages_ready_before_source_drain",
    "stream_spd_reads",
    "stream_writes",
    "page_ready_signals",
)


def load_result(root: Path, case: str) -> dict[str, str]:
    case_dir = root / case
    if not (case_dir / "virtual_tile_consumer_case.pass").is_file():
        raise SystemExit(f"{case}: missing validated pass marker")
    with (case_dir / "result.tsv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if len(rows) != 1 or rows[0]["case"] != case:
        raise SystemExit(f"{case}: malformed result.tsv")
    return rows[0]


def load_artifacts(root: Path, case: str) -> list[tuple[str, str]]:
    artifacts = []
    for line in (
        (root / case / "artifact_sha256.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    ):
        digest, path = line.split(maxsplit=1)
        artifacts.append((digest, Path(path).name))
    return artifacts


def percent(new: int, old: int) -> float:
    return (new / old - 1.0) * 100.0


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} EXPERIMENT_ROOT")
    root = Path(sys.argv[1]).resolve()
    rows = {case: load_result(root, case) for case in CASES}

    reference_artifacts = load_artifacts(root, CASES[0])
    for case in CASES[1:]:
        if load_artifacts(root, case) != reference_artifacts:
            raise SystemExit(f"{case}: artifact hashes differ from native_16k")

    output_hashes = {row["output_hash"] for row in rows.values()}
    if len(output_hashes) != 1:
        raise SystemExit("output hashes differ")

    sequential = rows["paged_4k"]
    overlap = rows["paged_overlap_4k"]
    for field in VIRTUAL_INVARIANTS:
        if sequential[field] != overlap[field]:
            raise SystemExit(
                f"virtual invariant {field} differs: "
                f"{sequential[field]} != {overlap[field]}"
            )
    for field in (
        "page_wait_reads",
        "page_wait_deferrals",
        "page_wait_responses",
    ):
        if int(sequential[field]) != 0:
            raise SystemExit(f"sequential control unexpectedly used {field}")
    pages = int(overlap["pages_ready"])
    if int(overlap["page_wait_reads"]) != pages:
        raise SystemExit("overlap did not read every page-ready register")
    if int(overlap["page_wait_responses"]) != pages:
        raise SystemExit("overlap did not receive every page-ready response")
    if int(overlap["page_wait_deferrals"]) <= 0:
        raise SystemExit("overlap never waited for an unfinished page")

    ticks = {case: int(rows[case]["simTicks"]) for case in CASES}
    native_gap = ticks["paged_4k"] - ticks["native_16k"]
    recovered = ticks["paged_4k"] - ticks["paged_overlap_4k"]
    metrics = {
        "ticks": ticks,
        "sequential_overhead_vs_native16_pct": percent(
            ticks["paged_4k"], ticks["native_16k"]
        ),
        "overlap_overhead_vs_native16_pct": percent(
            ticks["paged_overlap_4k"], ticks["native_16k"]
        ),
        "overlap_latency_change_vs_sequential_pct": percent(
            ticks["paged_overlap_4k"], ticks["paged_4k"]
        ),
        "overlap_throughput_change_vs_sequential_pct": percent(
            ticks["paged_4k"], ticks["paged_overlap_4k"]
        ),
        "native_gap_recovered_pct": 100.0 * recovered / native_gap
        if native_gap
        else 0.0,
        "sequential_write_issues": int(sequential["write_issues"]),
        "overlap_write_issues": int(overlap["write_issues"]),
        "overlap_deferred_page_waits": int(overlap["page_wait_deferrals"]),
        "sequential_alu_compute_cycles": int(sequential["alu_compute_cycles"]),
        "overlap_alu_compute_cycles": int(overlap["alu_compute_cycles"]),
    }
    (root / "overlap_summary.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Virtual Page Overlap Summary",
        "",
        "All cases used identical gem5/test artifacts and produced exact "
        "matching output.",
        "",
        "| Case | simTicks | Overhead vs native 16K |",
        "|---|---:|---:|",
        f"| native 16K | {ticks['native_16k']:,} | 0.000% |",
        f"| virtual 16K-on-4K, sequential | {ticks['paged_4k']:,} | "
        f"{metrics['sequential_overhead_vs_native16_pct']:.3f}% |",
        f"| virtual 16K-on-4K, page overlap | "
        f"{ticks['paged_overlap_4k']:,} | "
        f"{metrics['overlap_overhead_vs_native16_pct']:.3f}% |",
        "",
        f"Page overlap changes latency by "
        f"{metrics['overlap_latency_change_vs_sequential_pct']:.3f}% "
        f"and recovers {metrics['native_gap_recovered_pct']:.1f}% of the "
        "sequential virtual-to-native gap.",
        f"Retirement writes changed from {metrics['sequential_write_issues']:,} "
        f"to {metrics['overlap_write_issues']:,}; overlap therefore perturbs "
        "retirement timing and is not a gather-invariant comparison.",
    ]
    (root / "overlap_summary.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    (root / "virtual_page_overlap.pass").touch()
    print("\n".join(lines))


if __name__ == "__main__":
    main()
