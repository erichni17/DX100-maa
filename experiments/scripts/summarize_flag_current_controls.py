#!/usr/bin/env python3
"""Compare audited FLAG complete-line results with current-binary controls."""

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
    require(path.is_file(), f"missing {path}")
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def integer(row: dict[str, str], field: str, owner: str) -> int:
    try:
        value = int(row[field])
    except (KeyError, ValueError) as error:
        raise SummaryError(f"invalid {field} for {owner}") from error
    require(value >= 0, f"negative {field} for {owner}")
    return value


def geomean(values: list[float]) -> float:
    require(values and all(value > 0 for value in values), "invalid geomean")
    return math.exp(sum(math.log(value) for value in values) / len(values))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("safe", type=Path)
    parser.add_argument("controls", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    safe_rows = read_tsv(args.safe)
    control_rows = read_tsv(args.controls)
    safe = {row["id"]: row for row in safe_rows}
    require(len(safe_rows) == len(safe) == 14, "expected 14 unique safe rows")
    expected_arms = {"fused16", "compact16", "direct4_small", "direct4_max"}
    controls: dict[tuple[str, str], dict[str, str]] = {}
    for row in control_rows:
        key = (row["id"], row["arm"])
        require(row["arm"] in expected_arms and key not in controls,
                f"invalid or duplicate control {key}")
        controls[key] = row
    require(len(controls) == 56, "expected 56 unique control rows")

    ratios = {arm: [] for arm in sorted(expected_arms)}
    rows: list[dict[str, object]] = []
    for case_id in sorted(safe):
        candidate = safe[case_id]
        length = integer(candidate, "length", case_id)
        safe_ticks = integer(candidate, "ticks", case_id)
        safe_writes = integer(candidate, "writes", case_id)
        safe_full = integer(candidate, "full", case_id)
        safe_partial = integer(candidate, "partial", case_id)
        require(safe_ticks > 0 and safe_writes == safe_full + safe_partial,
                f"invalid safe closure for {case_id}")
        require(safe_full == length // 8 and
                safe_partial == (1 if length % 8 else 0),
                f"invalid complete-line closure for {case_id}")
        output: dict[str, object] = {
            "id": case_id,
            "length": length,
            "safe_ticks": safe_ticks,
            "output_hash": candidate["hash"],
        }
        for arm in sorted(expected_arms):
            control = controls.get((case_id, arm))
            require(control is not None, f"missing {case_id}/{arm}")
            require(integer(control, "length", case_id) == length,
                    f"length mismatch for {case_id}/{arm}")
            require(control["hash"] == candidate["hash"],
                    f"output mismatch for {case_id}/{arm}")
            ticks = integer(control, "ticks", f"{case_id}/{arm}")
            writes = integer(control, "writes", f"{case_id}/{arm}")
            completions = integer(control, "completions", f"{case_id}/{arm}")
            require(ticks > 0 and writes == completions,
                    f"terminal closure failed for {case_id}/{arm}")
            ratio = safe_ticks / ticks
            ratios[arm].append(ratio)
            output[f"{arm}_ticks"] = ticks
            output[f"vs_{arm}_ratio"] = ratio
            if arm == "direct4_max":
                require(ticks == safe_ticks and writes == safe_writes and
                        integer(control, "full", case_id) == safe_full and
                        integer(control, "partial", case_id) == safe_partial,
                        f"complete-line guard changed timing/work for {case_id}")
        rows.append(output)

    means = {arm: geomean(values) for arm, values in ratios.items()}
    report = {
        "schema": "dx100.flag.current_controls.v1",
        "terminal": True,
        "configurations": len(rows),
        "guard_timing_neutral": True,
        "rows": rows,
        "geomean_ratio": means,
        "geomean_latency_change_pct": {
            arm: 100 * (ratio - 1) for arm, ratio in means.items()
        },
    }
    require(not args.output.exists(), f"output already exists: {args.output}")
    args.output.mkdir(parents=True)
    (args.output / "flag_current_controls.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    markdown = [
        "# FLAG Current-Binary Controls",
        "",
        "| Configuration | Complete-line 4K | vs fused16 | vs compact16 | vs bounded direct4 |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        markdown.append(
            f"| {row['id']} | {row['safe_ticks']:,} | "
            f"{100 * (row['vs_fused16_ratio'] - 1):+.3f}% | "
            f"{100 * (row['vs_compact16_ratio'] - 1):+.3f}% | "
            f"{100 * (row['vs_direct4_small_ratio'] - 1):+.3f}% |"
        )
    markdown.extend(["", "## Equal-Weight Geometric Mean", ""])
    for arm, ratio in means.items():
        markdown.append(f"- `vs_{arm}`: {100 * (ratio - 1):+.3f}% latency")
    markdown.append("- `direct4_max`: exact timing/work identity in all 14 cases")
    (args.output / "flag_current_controls.md").write_text(
        "\n".join(markdown) + "\n"
    )
    (args.output / "flag_current_controls.pass").write_text(
        "PASS_FLAG_CURRENT_CONTROLS\n"
    )
    print(json.dumps(report["geomean_latency_change_pct"], sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SummaryError as error:
        print(f"ERROR: {error}")
        raise SystemExit(1)
