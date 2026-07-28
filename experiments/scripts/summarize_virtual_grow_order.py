#!/usr/bin/env python3
"""Validate and summarize legacy versus grow-grouped virtual issue."""

import csv
import json
import sys
from pathlib import Path


INVARIANTS = (
    "output_hash",
    "index_words",
    "indirect_spd_reads",
    "pages_ready",
    "stream_spd_reads",
    "stream_writes",
    "page_ready_signals",
    "page_wait_reads",
    "page_wait_responses",
    "row_table_slices",
    "response_slots",
    "response_word_pool",
)


def load_result(root: Path, treatment: str) -> dict[str, str]:
    case_dir = root / treatment
    if not (case_dir / "virtual_tile_consumer_case.pass").is_file():
        raise SystemExit(f"{treatment}: missing validated pass marker")
    with (case_dir / "result.tsv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if len(rows) != 1 or rows[0]["case"] != "paged_4k":
        raise SystemExit(f"{treatment}: malformed result.tsv")
    return rows[0]


def artifact_hashes(root: Path, treatment: str) -> list[tuple[str, str]]:
    values = []
    for line in (root / treatment / "artifact_sha256.txt").read_text(
        encoding="utf-8"
    ).splitlines():
        digest, path = line.split(maxsplit=1)
        values.append((digest, Path(path).name))
    return values


def change(new: int, old: int) -> float:
    return (new / old - 1.0) * 100.0


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} EXPERIMENT_ROOT")
    root = Path(sys.argv[1]).resolve()
    legacy = load_result(root, "legacy")
    grouped = load_result(root, "grow_grouped")
    if artifact_hashes(root, "legacy") != artifact_hashes(root, "grow_grouped"):
        raise SystemExit("treatments used different artifacts")
    for field in INVARIANTS:
        if legacy[field] != grouped[field]:
            raise SystemExit(
                f"mechanism invariant {field} differs: "
                f"{legacy[field]} != {grouped[field]}"
            )
    if legacy["virtual_grow_order"] != "0":
        raise SystemExit("legacy treatment enabled grow ordering")
    if grouped["virtual_grow_order"] != "1":
        raise SystemExit("grow-grouped treatment did not enable grow ordering")
    for name, row in (("legacy", legacy), ("grow_grouped", grouped)):
        if row["write_issues"] != row["write_completions"]:
            raise SystemExit(f"{name}: unbalanced retirement writes")

    fields = {
        "simTicks": "latency",
        "dram_activates": "DRAM activates",
        "dram_precharges": "DRAM precharges",
        "dram_reads": "DRAM reads",
        "row_table_cache_lines": "Row-Table cache lines inserted",
        "source_reads": "source cache-line reads",
        "response_slot_hwm": "response-slot high water",
        "response_word_hwm": "response-word high water",
        "response_pool_stalls": "response-pool stalls",
        "row_table_full_events": "Row-Table full events",
        "virtual_build_rounds": "virtual build rounds",
        "write_issues": "retirement writes",
    }
    values = {
        field: {
            "legacy": int(legacy[field]),
            "grow_grouped": int(grouped[field]),
        }
        for field in fields
    }
    metrics = {
        field + "_change_pct": change(pair["grow_grouped"], pair["legacy"])
        if pair["legacy"]
        else 0.0
        for field, pair in values.items()
    }
    summary = {"values": values, "changes": metrics}
    (root / "grow_order_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Virtual Grow-Order Summary",
        "",
        "Both treatments used identical artifacts and produced exact matching output.",
        "",
        "| Metric | Legacy row scan | Grow grouped | Change |",
        "|---|---:|---:|---:|",
    ]
    for field, label in fields.items():
        pair = values[field]
        lines.append(
            f"| {label} | {pair['legacy']:,} | {pair['grow_grouped']:,} | "
            f"{metrics[field + '_change_pct']:+.3f}% |"
        )
    (root / "grow_order_summary.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    (root / "virtual_grow_order.pass").touch()
    print("\n".join(lines))


if __name__ == "__main__":
    main()
