#!/usr/bin/env python3
"""Validate and summarize XRAGE virtual-combiner lookup latency."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


class SummaryError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SummaryError(message)


def integer(row: dict[str, str], field: str) -> int:
    try:
        value = int(row[field])
    except (KeyError, ValueError) as error:
        raise SummaryError(f"invalid {field} at latency {row.get('latency')}") from error
    require(value >= 0, f"negative {field}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--native16-ticks", type=int, required=True)
    args = parser.parse_args()

    with args.results.open(newline="", encoding="utf-8") as stream:
        input_rows = list(csv.DictReader(stream, delimiter="\t"))
    rows = {integer(row, "latency"): row for row in input_rows}
    require(len(rows) == len(input_rows) == 5 and set(rows) == {0, 1, 2, 3, 8},
            "expected latencies 0/1/2/3/8")
    require(args.native16_ticks > 0, "native16 ticks must be positive")
    hashes = {row["hash"] for row in rows.values()}
    require(len(hashes) == 1, "output hashes differ")
    baseline = integer(rows[0], "ticks")
    report_rows = []
    for latency in sorted(rows):
        row = rows[latency]
        ticks = integer(row, "ticks")
        issues = integer(row, "issues")
        completions = integer(row, "completions")
        wait_cycles = integer(row, "wait_cycles")
        peak = integer(row, "peak")
        require(ticks > 0, f"zero ticks at latency {latency}")
        if latency == 0:
            require(issues == completions == wait_cycles == peak == 0,
                    "disabled lookup pipeline recorded work")
        else:
            require(issues == completions == 65536 and 0 < peak <= 1024,
                    f"lookup closure failed at latency {latency}")
        report_rows.append({
            "latency": latency,
            "ticks": ticks,
            "delta_vs_latency0_pct": 100 * (ticks / baseline - 1),
            "delta_vs_native16_pct": 100 * (ticks / args.native16_ticks - 1),
            "issues": issues,
            "completions": completions,
            "wait_cycles": wait_cycles,
            "peak": peak,
        })

    report = {
        "schema": "dx100.xrage.lookup_latency.v1",
        "terminal": True,
        "output_hash": hashes.pop(),
        "latency0_ticks": baseline,
        "native16_ticks": args.native16_ticks,
        "rows": report_rows,
    }
    require(not args.output.exists(), f"output already exists: {args.output}")
    args.output.mkdir(parents=True)
    (args.output / "lookup_latency.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    markdown = [
        "# XRAGE Combiner Lookup Latency",
        "",
        "| Latency | simTicks | vs latency 0 | vs native16 | Wait cycles | Peak pending |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report_rows:
        markdown.append(
            f"| {row['latency']} | {row['ticks']:,} | "
            f"{row['delta_vs_latency0_pct']:+.3f}% | "
            f"{row['delta_vs_native16_pct']:+.3f}% | "
            f"{row['wait_cycles']:,} | {row['peak']} |"
        )
    (args.output / "lookup_latency.md").write_text("\n".join(markdown) + "\n")
    (args.output / "lookup_latency.pass").write_text("PASS_XRAGE_LOOKUP_LATENCY\n")
    print(json.dumps({
        str(row["latency"]): row["delta_vs_latency0_pct"]
        for row in report_rows
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SummaryError, FileNotFoundError) as error:
        print(f"ERROR: {error}")
        raise SystemExit(1)
