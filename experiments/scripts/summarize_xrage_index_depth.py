#!/usr/bin/env python3
"""Select a capacity/performance knee from a validated XRAGE feeder sweep."""

import argparse
import csv
import hashlib
import json
from pathlib import Path

REQUIRED_FIELDS = {
    "label",
    "arm",
    "gem5_sha256",
    "binary_sha256",
    "input_sha256",
    "output_hash",
    "logical_tile_elements",
    "physical_tile_elements",
    "index_buffer_lines",
    "virtual_native_issue_order",
    "roi_simTicks",
}
SHARED_FIELDS = {
    "gem5_sha256",
    "binary_sha256",
    "input_sha256",
    "output_hash",
    "logical_tile_elements",
    "physical_tile_elements",
    "virtual_native_issue_order",
}


def fail(message: str) -> None:
    raise SystemExit(f"XRAGE index-depth summary failed: {message}")


def positive_integer(row: dict[str, str], field: str) -> int:
    try:
        value = int(row[field])
    except (KeyError, ValueError):
        fail(f"{row.get('label', '<unknown>')} has invalid {field}")
    if value <= 0:
        fail(f"{row.get('label', '<unknown>')} has non-positive {field}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("comparison", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--line-bytes", type=int, default=64)
    parser.add_argument("--tolerance-pct", type=float, default=0.25)
    args = parser.parse_args()
    comparison = args.comparison.resolve()
    if args.line_bytes <= 0:
        parser.error("--line-bytes must be positive")
    if args.tolerance_pct < 0:
        parser.error("--tolerance-pct must be non-negative")
    if not comparison.is_file():
        fail(f"comparison is missing: {comparison}")
    pass_marker = comparison.parent / "xrage_comparison.pass"
    if not pass_marker.is_file():
        fail(f"validated comparison marker is missing: {pass_marker}")

    with comparison.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        missing = REQUIRED_FIELDS - set(reader.fieldnames or [])
        if missing:
            fail(f"comparison lacks fields: {sorted(missing)}")
        rows = [row for row in reader if row["arm"].startswith("direct_index")]
    if len(rows) < 2:
        fail("comparison must contain at least two direct-index rows")

    expected = rows[0]
    for row in rows[1:]:
        for field in SHARED_FIELDS:
            if row[field] != expected[field]:
                fail(
                    f"{row['label']} {field}={row[field]} differs from "
                    f"{expected['label']} {expected[field]}"
                )

    parsed = []
    depths = set()
    for row in rows:
        depth = positive_integer(row, "index_buffer_lines")
        ticks = positive_integer(row, "roi_simTicks")
        if depth in depths:
            fail(f"duplicate direct-index depth: {depth}")
        depths.add(depth)
        parsed.append(
            {
                "label": row["label"],
                "index_buffer_lines": depth,
                "feeder_payload_bytes_per_indirect_unit": (
                    depth * args.line_bytes
                ),
                "roi_simTicks": ticks,
            }
        )
    parsed.sort(key=lambda row: row["index_buffer_lines"])
    best_ticks = min(row["roi_simTicks"] for row in parsed)
    previous = None
    for row in parsed:
        row["latency_above_best_pct"] = 100.0 * (
            row["roi_simTicks"] / best_ticks - 1.0
        )
        if previous is None:
            row["latency_delta_vs_previous_pct"] = None
            row["payload_delta_vs_previous_bytes"] = None
        else:
            row["latency_delta_vs_previous_pct"] = 100.0 * (
                row["roi_simTicks"] / previous["roi_simTicks"] - 1.0
            )
            row["payload_delta_vs_previous_bytes"] = (
                row["feeder_payload_bytes_per_indirect_unit"]
                - previous["feeder_payload_bytes_per_indirect_unit"]
            )
        previous = row

    eligible = [
        row
        for row in parsed
        if row["latency_above_best_pct"] <= args.tolerance_pct + 1e-12
    ]
    recommendation = min(eligible, key=lambda row: row["index_buffer_lines"])
    plateau_pairs = [
        [left["index_buffer_lines"], right["index_buffer_lines"]]
        for left, right in zip(parsed, parsed[1:])
        if left["roi_simTicks"] == right["roi_simTicks"]
    ]
    report = {
        "provenance": {
            "comparison": str(comparison),
            "comparison_sha256": sha256(comparison),
            "pass_marker": str(pass_marker),
        },
        "selection": {
            "tolerance_pct": args.tolerance_pct,
            "best_roi_simTicks": best_ticks,
            "recommended_index_buffer_lines": recommendation[
                "index_buffer_lines"
            ],
            "recommended_payload_bytes_per_indirect_unit": recommendation[
                "feeder_payload_bytes_per_indirect_unit"
            ],
            "recommended_latency_above_best_pct": recommendation[
                "latency_above_best_pct"
            ],
            "equal_tick_plateau_pairs": plateau_pairs,
        },
        "rows": parsed,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "xrage_index_depth.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    markdown = [
        "# XRAGE Direct-Index Feeder Depth",
        "",
        f"Selection tolerance: `{args.tolerance_pct:.3f}%` above the best "
        "first-ROI `simTicks` result.",
        "",
        "| Depth (lines) | Payload / indirect unit | First-ROI ticks | "
        "Latency above best | Latency vs. previous |",
        "|---:|---:|---:|---:|---:|",
    ]
    for row in parsed:
        delta = row["latency_delta_vs_previous_pct"]
        delta_text = "n/a" if delta is None else f"{delta:+.6f}%"
        markdown.append(
            f"| {row['index_buffer_lines']} | "
            f"{row['feeder_payload_bytes_per_indirect_unit']} B | "
            f"{row['roi_simTicks']} | "
            f"{row['latency_above_best_pct']:+.6f}% | {delta_text} |"
        )
    markdown.extend(
        [
            "",
            f"Recommended depth: **{recommendation['index_buffer_lines']} "
            "lines** "
            f"({recommendation['feeder_payload_bytes_per_indirect_unit']} B "
            "per indirect unit).",
        ]
    )
    if plateau_pairs:
        markdown.append(f"Equal-tick plateaus: `{plateau_pairs}`.")
    (args.output_dir / "xrage_index_depth.md").write_text(
        "\n".join(markdown) + "\n", encoding="utf-8"
    )
    (args.output_dir / "xrage_index_depth.pass").touch()
    print(
        "PASS XRAGE index-depth summary: "
        f"recommended={recommendation['index_buffer_lines']} lines"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
