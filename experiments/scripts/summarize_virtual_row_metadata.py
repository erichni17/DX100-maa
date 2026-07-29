#!/usr/bin/env python3
"""Validate and summarize a fixed-resource Row-Table metadata matrix."""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_virtual_case import validate_case as validate_case_evidence

POINT_RE = re.compile(r"^r(?P<rows>[1-9][0-9]*)_e(?P<entries>[1-9][0-9]*)$")
BASELINE = "r64_e8"
MATCHED_MANIFEST_KEYS = (
    "case",
    "mode",
    "logical_tile_elements",
    "page_elements",
    "physical_tile_elements",
    "row_table_slices",
    "virtual_grow_order",
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
MATCHED_RESULT_KEYS = (
    "case",
    "output_hash",
    "index_line_reads",
    "index_words",
    "row_table_slices",
    "row_table_unique_cache_lines",
    "row_table_unique_rows",
    "response_slots",
    "response_word_pool",
)


def require_equal(
    reference: dict[str, str],
    candidate: dict[str, str],
    keys: tuple[str, ...],
    label: str,
) -> None:
    for key in keys:
        if key not in reference or key not in candidate:
            raise ValueError(f"missing {label} key {key!r}")
        if reference[key] != candidate[key]:
            raise ValueError(
                f"mismatched {label} {key}: reference={reference[key]!r} "
                f"candidate={candidate[key]!r}"
            )


def collect(root: Path) -> list[dict[str, str]]:
    points = []
    for child in sorted(root.iterdir()):
        match = POINT_RE.match(child.name)
        if not child.is_dir() or match is None:
            continue
        evidence = validate_case_evidence(child)
        manifest = evidence["manifest"]
        result = evidence["result"]
        hashes = evidence["hashes"]
        rows = int(match.group("rows"))
        entries = int(match.group("entries"))
        if manifest.get("row_table_rows_per_slice") != str(rows):
            raise ValueError(f"manifest rows do not match {child.name}")
        if manifest.get("row_table_entries_per_subslice_row") != str(entries):
            raise ValueError(f"manifest entries do not match {child.name}")
        if result.get("row_table_rows_per_slice") != str(rows):
            raise ValueError(f"result rows do not match {child.name}")
        if result.get("row_table_entries_per_subslice_row") != str(entries):
            raise ValueError(f"result entries do not match {child.name}")
        points.append(
            {
                "label": child.name,
                "rows": str(rows),
                "entries": str(entries),
                "descriptor_slots": str(16 * rows * entries),
                "manifest": manifest,
                "result": result,
                "hashes": hashes,
            }
        )
    if not points:
        raise ValueError(f"no Row-Table points found in {root}")
    by_label = {point["label"]: point for point in points}
    if BASELINE not in by_label:
        raise ValueError(f"missing baseline {BASELINE}")
    baseline = by_label[BASELINE]
    for point in points:
        require_equal(
            baseline["manifest"],
            point["manifest"],
            MATCHED_MANIFEST_KEYS,
            "manifest",
        )
        require_equal(
            baseline["result"], point["result"], MATCHED_RESULT_KEYS, "result"
        )
        if baseline["hashes"] != point["hashes"]:
            raise ValueError(f"artifact hashes differ for {point['label']}")
    return sorted(
        points,
        key=lambda point: (
            -int(point["descriptor_slots"]),
            int(point["rows"]),
            int(point["entries"]),
        ),
    )


def summarize(points: list[dict[str, str]]) -> list[dict[str, str]]:
    baseline = next(point for point in points if point["label"] == BASELINE)
    baseline_ticks = int(baseline["result"]["simTicks"])
    rows = []
    for point in points:
        result = point["result"]
        ticks = int(result["simTicks"])
        rows.append(
            {
                "point": point["label"],
                "descriptor_slots": point["descriptor_slots"],
                "rows_per_slice": point["rows"],
                "entries_per_row": point["entries"],
                "simTicks": str(ticks),
                "latency_delta_percent": f"{(ticks / baseline_ticks - 1) * 100:.6f}",
                "row_table_full_events": result["row_table_full_events"],
                "virtual_build_rounds": result["virtual_build_rounds"],
                "row_table_cache_lines": result["row_table_cache_lines"],
                "row_table_rows_inserted": result["row_table_rows_inserted"],
                "source_reads": result["source_reads"],
                "write_issues": result["write_issues"],
                "dram_reads": result["dram_reads"],
                "dram_activates": result["dram_activates"],
                "dram_precharges": result["dram_precharges"],
                "output_hash": result["output_hash"],
            }
        )
    return rows


def render_tsv(rows: list[dict[str, str]]) -> str:
    fields = list(rows[0])
    output = ["\t".join(fields)]
    output.extend("\t".join(row[field] for field in fields) for row in rows)
    return "\n".join(output) + "\n"


def render_markdown(rows: list[dict[str, str]]) -> str:
    lines = [
        "# Virtual Row-Metadata Matrix",
        "",
        "All points use identical artifacts and exact matching output.",
        "",
        "| Point | Descriptor slots | Rows/slice | Entries/row | Latency | "
        "Delta | RT full | Build rounds | Source reads | Writes | ACT | PRE |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['point']} | {row['descriptor_slots']} | "
            f"{row['rows_per_slice']} | {row['entries_per_row']} | "
            f"{row['simTicks']} | {float(row['latency_delta_percent']):+.3f}% | "
            f"{row['row_table_full_events']} | {row['virtual_build_rounds']} | "
            f"{row['source_reads']} | {row['write_issues']} | "
            f"{row['dram_activates']} | {row['dram_precharges']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--tsv", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    rows = summarize(collect(args.root))
    tsv = render_tsv(rows)
    markdown = render_markdown(rows)
    if args.tsv:
        args.tsv.write_text(tsv)
    if args.markdown:
        args.markdown.write_text(markdown)
    print(markdown, end="")


if __name__ == "__main__":
    main()
