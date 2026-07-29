#!/usr/bin/env python3
"""Validate and summarize Row-Table capacity versus issue-order controls."""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_virtual_case import validate_case

POINTS = {
    "full_legacy": (64, 0),
    "full_grow": (64, 1),
    "half_legacy": (32, 0),
    "half_grow": (32, 1),
}
MATCHED_MANIFEST = (
    "case",
    "mode",
    "logical_tile_elements",
    "page_elements",
    "physical_tile_elements",
    "row_table_slices",
    "row_table_entries_per_subslice_row",
    "virtual_index_partitions",
    "virtual_index_filter_words_per_cycle",
    "virtual_response_slots",
    "virtual_response_word_pool",
    "virtual_combine_slots",
    "virtual_combine_words",
    "virtual_combine_ways",
    "virtual_combine_victim_policy",
    "virtual_combine_banks",
    "source_commit",
    "timeout",
)
MATCHED_RESULT = (
    "case",
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
    "row_table_entries_per_subslice_row",
    "virtual_index_partitions",
    "response_slots",
    "response_word_pool",
)
REPORT_FIELDS = (
    "point",
    "descriptor_slots",
    "rows_per_slice",
    "grow_order",
    "simTicks",
    "latency_vs_full_legacy_pct",
    "grow_vs_matched_legacy_pct",
    "row_table_cache_lines",
    "source_reads",
    "row_table_full_events",
    "virtual_build_rounds",
    "write_issues",
    "dram_reads",
    "dram_activates",
    "dram_precharges",
    "output_hash",
)


def require_equal(reference, candidate, fields, label):
    for field in fields:
        if reference.get(field) != candidate.get(field):
            raise ValueError(
                f"{label} {field} differs: {reference.get(field)!r} != "
                f"{candidate.get(field)!r}"
            )


def collect(root: Path):
    evidence = {}
    for label, (rows, grow) in POINTS.items():
        point = validate_case(root / label)
        manifest = point["manifest"]
        result = point["result"]
        if manifest.get("row_table_rows_per_slice") != str(rows):
            raise ValueError(f"{label}: manifest has the wrong Row-Table rows")
        if manifest.get("virtual_grow_order") != str(grow):
            raise ValueError(f"{label}: manifest has the wrong issue order")
        if result.get("row_table_rows_per_slice") != str(rows):
            raise ValueError(f"{label}: result has the wrong Row-Table rows")
        if result.get("virtual_grow_order") != str(grow):
            raise ValueError(f"{label}: result has the wrong issue order")
        evidence[label] = point

    reference = evidence["full_legacy"]
    for label, point in evidence.items():
        require_equal(
            reference["manifest"],
            point["manifest"],
            MATCHED_MANIFEST,
            f"{label} manifest",
        )
        require_equal(
            reference["result"],
            point["result"],
            MATCHED_RESULT,
            f"{label} result",
        )
        if reference["hashes"] != point["hashes"]:
            raise ValueError(f"{label}: frozen artifact hashes differ")
        if (
            point["result"]["write_issues"]
            != point["result"]["write_completions"]
        ):
            raise ValueError(f"{label}: retirement writes are unbalanced")
    return evidence


def summarize(evidence):
    baseline = int(evidence["full_legacy"]["result"]["simTicks"])
    rows = []
    for label, (capacity_rows, grow) in POINTS.items():
        result = evidence[label]["result"]
        ticks = int(result["simTicks"])
        legacy = int(
            evidence["full_legacy" if capacity_rows == 64 else "half_legacy"][
                "result"
            ]["simTicks"]
        )
        rows.append(
            {
                "point": label,
                "descriptor_slots": str(16 * capacity_rows * 8),
                "rows_per_slice": str(capacity_rows),
                "grow_order": str(grow),
                "simTicks": str(ticks),
                "latency_vs_full_legacy_pct": (
                    f"{100.0 * (ticks / baseline - 1.0):+.6f}"
                ),
                "grow_vs_matched_legacy_pct": (
                    f"{100.0 * (ticks / legacy - 1.0):+.6f}"
                ),
                **{field: result[field] for field in REPORT_FIELDS[7:]},
            }
        )
    return rows


def render_markdown(rows):
    lines = [
        "# Row-Table Capacity x Issue-Order Matrix",
        "",
        "All four points passed raw-artifact, exact-output, resolved-config, "
        "and balanced-write validation.",
        "",
        "| Point | Descriptors | Grow order | simTicks | vs. full legacy | "
        "vs. matched legacy | Source reads | ACT | PRE |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['point']} | {row['descriptor_slots']} | "
            f"{row['grow_order']} | {row['simTicks']} | "
            f"{row['latency_vs_full_legacy_pct']}% | "
            f"{row['grow_vs_matched_legacy_pct']}% | "
            f"{row['source_reads']} | {row['dram_activates']} | "
            f"{row['dram_precharges']} |"
        )
    return "\n".join(lines) + "\n"


def main():
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} EXPERIMENT_ROOT")
    root = Path(sys.argv[1]).resolve()
    rows = summarize(collect(root))
    with (root / "summary.tsv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(stream, REPORT_FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    markdown = render_markdown(rows)
    (root / "summary.md").write_text(markdown, encoding="utf-8")
    (root / "virtual_row_grow_matrix.pass").touch()
    print(markdown, end="")


if __name__ == "__main__":
    main()
