#!/usr/bin/env python3
"""Compare same-binary FLAG 8-way XOR7 and 16-way complete-line runs."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


class SummaryError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SummaryError(message)


def read_rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    result = {row["id"]: row for row in rows}
    require(len(rows) == len(result) == 14, f"expected 14 unique rows in {path}")
    return result


def integer(row: dict[str, str], field: str, owner: str) -> int:
    try:
        value = int(row[field])
    except (KeyError, ValueError) as error:
        raise SummaryError(f"invalid {field} for {owner}") from error
    require(value >= 0, f"negative {field} for {owner}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ways16", type=Path)
    parser.add_argument("xor8", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    ways16 = read_rows(args.ways16)
    xor8 = read_rows(args.xor8)
    require(ways16.keys() == xor8.keys(), "FLAG configuration sets differ")
    ratios = []
    rows = []
    for case_id in sorted(ways16):
        control = ways16[case_id]
        candidate = xor8[case_id]
        length = integer(control, "length", case_id)
        require(integer(candidate, "length", case_id) == length,
                f"length mismatch for {case_id}")
        require(control["hash"] == candidate["hash"],
                f"hash mismatch for {case_id}")
        expected_full = length // 8
        expected_partial = 1 if length % 8 else 0
        for name, row in (("16-way", control), ("XOR8", candidate)):
            full = integer(row, "full", case_id)
            partial = integer(row, "partial", case_id)
            writes = integer(row, "writes", case_id)
            require(full == expected_full and partial == expected_partial and
                    writes == full + partial,
                    f"{name} closure failed for {case_id}")
        control_ticks = integer(control, "ticks", case_id)
        candidate_ticks = integer(candidate, "ticks", case_id)
        require(control_ticks > 0 and candidate_ticks > 0,
                f"zero timing for {case_id}")
        ratio = candidate_ticks / control_ticks
        ratios.append(ratio)
        rows.append({
            "id": case_id,
            "length": length,
            "ways16_ticks": control_ticks,
            "xor8_ticks": candidate_ticks,
            "xor8_vs_ways16_ratio": ratio,
            "xor8_stall_cycles": integer(candidate, "stall_cycles", case_id),
            "xor8_peak_sum": integer(candidate, "peak_sum", case_id),
            "output_hash": candidate["hash"],
        })
    mean = math.exp(sum(math.log(value) for value in ratios) / len(ratios))
    report = {
        "schema": "dx100.flag.xor8.v1",
        "terminal": True,
        "configurations": len(rows),
        "geomean_ratio": mean,
        "geomean_latency_change_pct": 100 * (mean - 1),
        "rows": rows,
    }
    require(not args.output.exists(), f"output already exists: {args.output}")
    args.output.mkdir(parents=True)
    (args.output / "flag_xor8.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    markdown = [
        "# FLAG XOR8 vs 16-Way",
        "",
        f"Geometric-mean XOR8 latency change: {100 * (mean - 1):+.3f}%.",
        "",
        "| Configuration | 16-way ticks | XOR8 ticks | XOR8 delta |",
        "|---|---:|---:|---:|",
    ]
    for row in rows:
        markdown.append(
            f"| {row['id']} | {row['ways16_ticks']:,} | "
            f"{row['xor8_ticks']:,} | "
            f"{100 * (row['xor8_vs_ways16_ratio'] - 1):+.3f}% |"
        )
    (args.output / "flag_xor8.md").write_text("\n".join(markdown) + "\n")
    (args.output / "flag_xor8.pass").write_text("PASS_FLAG_XOR8\n")
    print(json.dumps({"geomean_latency_change_pct": 100 * (mean - 1)}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SummaryError, FileNotFoundError) as error:
        print(f"ERROR: {error}")
        raise SystemExit(1)
