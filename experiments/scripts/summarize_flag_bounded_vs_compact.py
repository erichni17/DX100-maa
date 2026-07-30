#!/usr/bin/env python3
"""Aggregate validated FLAG bounded4-versus-compact16 comparisons."""

import argparse
import csv
import json
import math
from pathlib import Path

EXPECTED_CASES = 14
PAIR_NAME = "bounded_vs_compact"


def fail(message: str) -> None:
    raise SystemExit(f"FLAG bounded comparison failed: {message}")


def read_one_row(path: Path) -> dict[str, str]:
    if not path.is_file():
        fail(f"missing table: {path}")
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(rows) != 1:
        fail(f"{path} contains {len(rows)} rows instead of one")
    return rows[0]


def load_rows(comparisons: Path) -> list[dict[str, object]]:
    case_dirs = sorted(path for path in comparisons.iterdir() if path.is_dir())
    if len(case_dirs) != EXPECTED_CASES:
        fail(f"expected {EXPECTED_CASES} cases, found {len(case_dirs)}")

    rows: list[dict[str, object]] = []
    for case in case_dirs:
        if not (case / "xrage_comparison.pass").is_file():
            fail(f"missing comparison pass marker: {case}")
        pair = read_one_row(case / "xrage_pairwise.tsv")
        if (
            pair.get("pair") != PAIR_NAME
            or pair.get("reference") != "compact16"
            or pair.get("candidate") != "bounded4"
        ):
            fail(f"unexpected treatment pair in {case}")
        compact_ticks = int(pair["reference_ticks"])
        bounded_ticks = int(pair["candidate_ticks"])
        if compact_ticks <= 0 or bounded_ticks <= 0:
            fail(f"non-positive ROI ticks in {case}")
        ratio = bounded_ticks / compact_ticks
        with (case / "xrage_comparison.tsv").open(
            encoding="utf-8", newline=""
        ) as stream:
            arm_rows = {
                row["label"]: row
                for row in csv.DictReader(stream, delimiter="\t")
            }
        if set(arm_rows) != {"compact16", "bounded4"}:
            fail(f"unexpected comparison arms in {case}")
        output_lengths = {
            int(row["output_length"]) for row in arm_rows.values()
        }
        if len(output_lengths) != 1:
            fail(f"output lengths differ in {case}")
        output_length = output_lengths.pop()
        minimum_writes = (output_length + 7) // 8
        compact_writes = int(arm_rows["compact16"]["virtual_write_issues"])
        bounded_writes = int(arm_rows["bounded4"]["virtual_write_issues"])
        if compact_writes < minimum_writes or bounded_writes < minimum_writes:
            fail(
                f"retirement writes are below the dense-line minimum in {case}"
            )
        rows.append(
            {
                "id": case.name,
                "compact16_ticks": compact_ticks,
                "bounded4_ticks": bounded_ticks,
                "latency_ratio": ratio,
                "latency_delta_pct": 100.0 * (ratio - 1.0),
                "output_length": output_length,
                "minimum_c_writes": minimum_writes,
                "compact16_c_writes": compact_writes,
                "bounded4_c_writes": bounded_writes,
                "compact16_excess_c_writes": compact_writes - minimum_writes,
                "bounded4_excess_c_writes": bounded_writes - minimum_writes,
                "roi_memory_reads_delta": int(pair["roi_memory_reads_delta"]),
                "dram_reads_delta": int(pair["dram_reads_delta"]),
                "dram_activates_delta": int(pair["dram_activates_delta"]),
                "dram_precharges_delta": int(pair["dram_precharges_delta"]),
            }
        )
    return rows


def summarize(rows: list[dict[str, object]]) -> dict[str, object]:
    ratios = [float(row["latency_ratio"]) for row in rows]
    geomean = math.exp(sum(math.log(value) for value in ratios) / len(ratios))
    return {
        "cases": len(rows),
        "latency_geomean_ratio": geomean,
        "latency_minimum_ratio": min(ratios),
        "latency_maximum_ratio": max(ratios),
        "wins": sum(value < 1.0 for value in ratios),
        "ties": sum(value == 1.0 for value in ratios),
        "losses": sum(value > 1.0 for value in ratios),
        "roi_memory_reads_delta_sum": sum(
            int(row["roi_memory_reads_delta"]) for row in rows
        ),
        "compact16_excess_c_writes_sum": sum(
            int(row["compact16_excess_c_writes"]) for row in rows
        ),
        "bounded4_excess_c_writes_sum": sum(
            int(row["bounded4_excess_c_writes"]) for row in rows
        ),
    }


def write_tsv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=rows[0],
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("comparisons", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    comparisons = args.comparisons.resolve()
    if not comparisons.is_dir():
        fail(f"missing comparison directory: {comparisons}")
    rows = load_rows(comparisons)
    summary = summarize(rows)

    output = args.output_dir.resolve()
    if output.exists():
        fail(f"refusing to overwrite output: {output}")
    output.mkdir(parents=True)
    write_tsv(output / "bounded_vs_compact.tsv", rows)
    report = {
        "claim_scope": (
            "14 imported FLAG direct gathers; same frozen simulator and guest "
            "binary; bounded4 is a 16K logical gather with 4K physical SPD, "
            "Row capacity, Offset capacity, and Offset epoch"
        ),
        "comparisons": str(comparisons),
        "configurations": rows,
        "summary": summary,
    }
    (output / "bounded_vs_compact.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    lines = [
        "# FLAG Bounded4 vs. Compact16",
        "",
        "All cases first passed the fail-closed XRAGE pair comparator. Negative "
        "latency deltas mean bounded4 is faster.",
        "",
        "| Configuration | Compact16 ticks | Bounded4 ticks | Latency delta | "
        "Compact/bounded C writes | ROI memory-read delta |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['id']} | {int(row['compact16_ticks']):,} | "
            f"{int(row['bounded4_ticks']):,} | "
            f"{float(row['latency_delta_pct']):+.3f}% | "
            f"{int(row['compact16_c_writes']):,}/"
            f"{int(row['bounded4_c_writes']):,} | "
            f"{int(row['roi_memory_reads_delta']):+d} |"
        )
    lines.extend(
        [
            "",
            "## Summary",
            "",
            "- Equal-weight latency geometric-mean delta: "
            f"{100.0 * (float(summary['latency_geomean_ratio']) - 1.0):+.3f}%",
            "- Per-case range: "
            f"{100.0 * (float(summary['latency_minimum_ratio']) - 1.0):+.3f}% "
            "to "
            f"{100.0 * (float(summary['latency_maximum_ratio']) - 1.0):+.3f}%",
            "- Outcomes: "
            f"{summary['wins']} faster, {summary['ties']} tied, "
            f"{summary['losses']} slower",
            "- Excess C-line writes over the dense minimum: "
            f"{summary['compact16_excess_c_writes_sum']:,} compact16, "
            f"{summary['bounded4_excess_c_writes_sum']:,} bounded4",
            "",
        ]
    )
    (output / "bounded_vs_compact.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    (output / "bounded_vs_compact.pass").touch()
    print(
        "PASS FLAG bounded-vs-compact summary: "
        f"{summary['cases']} cases, "
        f"{100.0 * (float(summary['latency_geomean_ratio']) - 1.0):+.3f}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
