#!/usr/bin/env python3
"""Compare complete-line FLAG gathers with the accepted historical matrix."""

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


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def integer(row: dict[str, str], field: str) -> int:
    try:
        value = int(row[field])
    except (KeyError, ValueError) as error:
        raise SummaryError(f"invalid {field} for {row.get('id')}") from error
    require(value >= 0, f"negative {field} for {row.get('id')}")
    return value


def geometric_mean(values: list[float]) -> float:
    require(values and all(value > 0 for value in values), "invalid geomean")
    return math.exp(sum(math.log(value) for value in values) / len(values))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("historical", type=Path)
    parser.add_argument("safe", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    historical_rows = read_tsv(args.historical)
    safe_rows = read_tsv(args.safe)
    require(len(historical_rows) == len(safe_rows) == 14, "expected 14 rows")
    historical = {row["id"]: row for row in historical_rows}
    safe = {row["id"]: row for row in safe_rows}
    require(len(historical) == len(safe) == 14, "duplicate configuration ID")
    require(historical.keys() == safe.keys(), "configuration sets differ")

    rows: list[dict[str, object]] = []
    ratios: dict[str, list[float]] = {
        "vs_fused16": [],
        "vs_compact16": [],
        "vs_direct4": [],
    }
    for config_id in sorted(historical):
        old = historical[config_id]
        new = safe[config_id]
        length = integer(new, "length")
        require(
            length == integer(old, "pattern_length"),
            f"length changed for {config_id}",
        )
        writes = integer(new, "writes")
        full = integer(new, "full")
        partial = integer(new, "partial")
        require(writes == full + partial, f"write closure for {config_id}")
        require(
            partial == (0 if length % 8 == 0 else 1),
            f"tail closure for {config_id}",
        )
        ticks = integer(new, "ticks")
        require(
            ticks > 0 and new["hash"].isdigit(),
            f"terminal evidence for {config_id}",
        )
        references = {
            "fused16_ticks": integer(old, "fused16_ticks"),
            "compact16_ticks": integer(old, "compact16_ticks"),
            "direct4_ticks": integer(old, "direct4_ticks"),
        }
        comparisons = {
            f"vs_{name.removesuffix('_ticks')}": ticks / reference
            for name, reference in references.items()
        }
        for name, ratio in comparisons.items():
            ratios[name].append(ratio)
        rows.append(
            {
                "id": config_id,
                "length": length,
                "safe_ticks": ticks,
                "writes": writes,
                "full": full,
                "partial": partial,
                "output_hash": new["hash"],
                **references,
                **{
                    f"{name}_ratio": ratio
                    for name, ratio in comparisons.items()
                },
            }
        )

    geomean = {name: geometric_mean(values) for name, values in ratios.items()}
    report = {
        "schema": "dx100.flag.complete_line.v1",
        "terminal": True,
        "configurations": len(rows),
        "rows": rows,
        "geomean_ratio": geomean,
        "geomean_latency_change_pct": {
            name: 100 * (ratio - 1) for name, ratio in geomean.items()
        },
    }
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "flag_complete_line.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    fields = [
        "id",
        "length",
        "safe_ticks",
        "fused16_ticks",
        "compact16_ticks",
        "direct4_ticks",
        "vs_fused16_ratio",
        "vs_compact16_ratio",
        "vs_direct4_ratio",
        "writes",
        "full",
        "partial",
        "output_hash",
    ]
    with (args.output / "flag_complete_line.tsv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    markdown = [
        "# FLAG Complete-Line Summary",
        "",
        "| Configuration | Safe ticks | vs fused16 | vs compact16 | vs direct4 | Full / partial |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        markdown.append(
            f"| {row['id']} | {row['safe_ticks']:,} | "
            f"{100 * (row['vs_fused16_ratio'] - 1):+.3f}% | "
            f"{100 * (row['vs_compact16_ratio'] - 1):+.3f}% | "
            f"{100 * (row['vs_direct4_ratio'] - 1):+.3f}% | "
            f"{row['full']:,} / {row['partial']} |"
        )
    markdown.extend(["", "## Equal-Weight Geometric Mean", ""])
    for name, ratio in geomean.items():
        markdown.append(f"- `{name}`: {100 * (ratio - 1):+.3f}% latency")
    (args.output / "flag_complete_line.md").write_text(
        "\n".join(markdown) + "\n"
    )
    (args.output / "flag_complete_line.pass").write_text(
        "PASS_FLAG_COMPLETE_LINE\n"
    )
    print(json.dumps(report["geomean_latency_change_pct"], sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SummaryError as error:
        print(f"ERROR: {error}")
        raise SystemExit(1)
