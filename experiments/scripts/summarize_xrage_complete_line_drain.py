#!/usr/bin/env python3
"""Validate and summarize the XRAGE complete-line drain-width sweep."""

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
        raise SummaryError(f"invalid {field} at width {row.get('width')}") from error
    require(value >= 0, f"negative {field} at width {row.get('width')}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--legacy-ticks", type=int, required=True)
    parser.add_argument("--native16-ticks", type=int, required=True)
    args = parser.parse_args()

    with args.results.open(newline="", encoding="utf-8") as stream:
        input_rows = list(csv.DictReader(stream, delimiter="\t"))
    rows = {integer(row, "width"): row for row in input_rows}
    require(len(input_rows) == len(rows) == 5 and set(rows) == {0, 1, 2, 4, 8},
            "expected exactly widths 0/1/2/4/8")
    require(args.legacy_ticks > 0 and args.native16_ticks > 0,
            "reference ticks must be positive")

    hashes = {row["hash"] for row in rows.values()}
    require(len(hashes) == 1, "output hashes differ")
    baseline = integer(rows[0], "ticks")
    report_rows = []
    for width in sorted(rows):
        row = rows[width]
        ticks = integer(row, "ticks")
        issued = integer(row, "issued")
        stalls = integer(row, "stall_cycles")
        peak = integer(row, "peak")
        require(ticks > 0 and issued == 8192 and peak > 0,
                f"terminal mechanism closure failed at width {width}")
        if width != 0:
            require(peak <= width, f"peak exceeds width {width}")
        report_rows.append({
            "width": width,
            "ticks": ticks,
            "delta_vs_width0_pct": 100 * (ticks / baseline - 1),
            "delta_vs_native16_pct": 100 * (ticks / args.native16_ticks - 1),
            "issued": issued,
            "stall_cycles": stalls,
            "peak": peak,
            "stats_sha256": row["stats_sha256"],
        })

    report = {
        "schema": "dx100.xrage.complete_line_drain.v1",
        "terminal": True,
        "output_hash": hashes.pop(),
        "legacy_ticks": args.legacy_ticks,
        "native16_ticks": args.native16_ticks,
        "width0_ticks": baseline,
        "width0_delta_vs_legacy_pct": 100 * (baseline / args.legacy_ticks - 1),
        "rows": report_rows,
    }
    require(not args.output.exists(), f"output already exists: {args.output}")
    args.output.mkdir(parents=True)
    (args.output / "drain_width.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    markdown = [
        "# XRAGE Complete-Line Drain Width",
        "",
        f"Current width-0 versus legacy: {report['width0_delta_vs_legacy_pct']:+.3f}% latency.",
        "",
        "| Width (lines/cycle) | simTicks | vs width 0 | vs native16 | Budget stalls | Peak |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report_rows:
        markdown.append(
            f"| {row['width']} | {row['ticks']:,} | "
            f"{row['delta_vs_width0_pct']:+.3f}% | "
            f"{row['delta_vs_native16_pct']:+.3f}% | "
            f"{row['stall_cycles']:,} | {row['peak']} |"
        )
    (args.output / "drain_width.md").write_text("\n".join(markdown) + "\n")
    (args.output / "drain_width.pass").write_text("PASS_XRAGE_DRAIN_WIDTH\n")
    print(json.dumps({
        str(row["width"]): row["delta_vs_width0_pct"] for row in report_rows
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SummaryError, FileNotFoundError) as error:
        print(f"ERROR: {error}")
        raise SystemExit(1)
