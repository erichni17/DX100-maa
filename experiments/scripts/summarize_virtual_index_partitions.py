#!/usr/bin/env python3
"""Fail closed while comparing virtual index-partition treatments."""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_virtual_case import validate_case as validate_case_evidence

LABEL_RE = re.compile(
    r"^r(?P<rows>[1-9][0-9]*)_e(?P<entries>[1-9][0-9]*)_"
    r"g(?P<grow>[01])_p(?P<partitions>[1-9][0-9]*)$"
)
MATCHED_MANIFEST_KEYS = (
    "case",
    "mode",
    "logical_tile_elements",
    "page_elements",
    "physical_tile_elements",
    "row_table_slices",
    "row_table_entries_per_subslice_row",
    "virtual_grow_order",
    "virtual_response_slots",
    "virtual_response_word_pool",
    "virtual_combine_slots",
    "virtual_combine_words",
    "virtual_combine_ways",
    "virtual_combine_victim_policy",
    "virtual_combine_banks",
    "virtual_index_filter_words_per_cycle",
    "source_commit",
    "timeout",
)


def load_case(path: Path) -> dict:
    match = LABEL_RE.match(path.name)
    if not path.is_dir() or match is None:
        raise ValueError(f"invalid treatment directory label: {path}")
    evidence = validate_case_evidence(path)
    manifest = evidence["manifest"]
    result = evidence["result"]
    dimensions = match.groupdict()
    expected = {
        "row_table_rows_per_slice": dimensions["rows"],
        "row_table_entries_per_subslice_row": dimensions["entries"],
        "virtual_grow_order": dimensions["grow"],
        "virtual_index_partitions": dimensions["partitions"],
    }
    for key, value in expected.items():
        if manifest.get(key) != value or result.get(key) != value:
            raise ValueError(f"{path.name}: mislabeled {key}")
    partitions = int(dimensions["partitions"])
    index_words = int(result["index_words"])
    if index_words != 16384 * partitions:
        raise ValueError(
            f"{path.name}: index words {index_words} do not prove {partitions} scans"
        )
    if result["write_issues"] != result["write_completions"]:
        raise ValueError(f"{path.name}: unbalanced retirement writes")
    return {
        "label": path.name,
        "manifest": manifest,
        "result": result,
        "hashes": evidence["hashes"],
        **dimensions,
    }


def require_same(
    reference: dict, candidate: dict, keys: tuple[str, ...]
) -> None:
    for key in keys:
        if reference.get(key) != candidate.get(key):
            raise ValueError(
                f"mismatched {key}: {reference.get(key)!r} != "
                f"{candidate.get(key)!r}"
            )


def collect(
    full_path: Path, constrained_path: Path, treatments: list[Path]
) -> list[dict]:
    points = [
        load_case(path) for path in [full_path, constrained_path, *treatments]
    ]
    full, constrained = points[:2]
    if int(full["rows"]) <= int(constrained["rows"]):
        raise ValueError(
            "full baseline must have more Row-Table rows than constrained"
        )
    if full["partitions"] != "1" or constrained["partitions"] != "1":
        raise ValueError("both baselines must be single-pass")
    for point in points[1:]:
        require_same(
            full["manifest"], point["manifest"], MATCHED_MANIFEST_KEYS
        )
        if full["hashes"] != point["hashes"]:
            raise ValueError(f"artifact hashes differ for {point['label']}")
        if full["result"]["output_hash"] != point["result"]["output_hash"]:
            raise ValueError(f"output hash differs for {point['label']}")
        if (
            full["result"]["row_table_unique_cache_lines"]
            != point["result"]["row_table_unique_cache_lines"]
        ):
            raise ValueError(
                f"unique source-line oracle differs for {point['label']}"
            )
    for point in points[2:]:
        if point["rows"] != constrained["rows"]:
            raise ValueError(
                f"{point['label']}: treatment rows differ from constrained"
            )
        if point["entries"] != constrained["entries"]:
            raise ValueError(
                f"{point['label']}: treatment entries differ from constrained"
            )
        if int(point["partitions"]) <= 1:
            raise ValueError(f"{point['label']}: treatment is not partitioned")
    return points


def percent_delta(value: int, reference: int) -> float:
    return (value / reference - 1.0) * 100.0


def summarize(points: list[dict]) -> list[dict[str, str]]:
    full_ticks = int(points[0]["result"]["simTicks"])
    constrained_ticks = int(points[1]["result"]["simTicks"])
    unique_lines = int(points[0]["result"]["row_table_unique_cache_lines"])
    rows = []
    for point in points:
        result = point["result"]
        ticks = int(result["simTicks"])
        source_reads = int(result["source_reads"])
        rows.append(
            {
                "point": point["label"],
                "descriptor_slots": str(
                    int(result["row_table_slices"])
                    * int(point["rows"])
                    * int(point["entries"])
                ),
                "index_scans": point["partitions"],
                "simTicks": str(ticks),
                "delta_vs_full_percent": f"{percent_delta(ticks, full_ticks):.6f}",
                "delta_vs_constrained_percent": f"{percent_delta(ticks, constrained_ticks):.6f}",
                "index_line_reads": result["index_line_reads"],
                "index_filter_words": result.get("index_filter_words", "0"),
                "index_filter_cycles": result.get("index_filter_cycles", "0"),
                "index_filter_wait_events": result.get(
                    "index_filter_wait_events", "0"
                ),
                "index_filter_wait_cycles": result.get(
                    "index_filter_wait_cycles", "0"
                ),
                "source_reads": str(source_reads),
                "source_read_amplification": f"{source_reads / unique_lines:.6f}",
                "row_table_full_events": result["row_table_full_events"],
                "virtual_build_rounds": result["virtual_build_rounds"],
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
        "# Virtual Index-Partition Comparison",
        "",
        "All points use identical artifacts and produce exact matching output.",
        "",
        (
            "| Point | Descriptors | B scans | Filter words | Charged cycles | "
            "Wait events | Exposed cycles | Ticks | vs full | vs constrained | "
            "A reads | A amplification | RT full | Build rounds |"
        ),
        (
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
            "---:|---:|---:|"
        ),
    ]
    for row in rows:
        lines.append(
            f"| {row['point']} | {row['descriptor_slots']} | {row['index_scans']} | "
            f"{row['index_filter_words']} | {row['index_filter_cycles']} | "
            f"{row['index_filter_wait_events']} | "
            f"{row['index_filter_wait_cycles']} | "
            f"{row['simTicks']} | "
            f"{float(row['delta_vs_full_percent']):+.3f}% | "
            f"{float(row['delta_vs_constrained_percent']):+.3f}% | "
            f"{row['source_reads']} | "
            f"{float(row['source_read_amplification']):.3f}x | "
            f"{row['row_table_full_events']} | {row['virtual_build_rounds']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", type=Path, required=True)
    parser.add_argument("--constrained", type=Path, required=True)
    parser.add_argument(
        "--treatment", type=Path, action="append", required=True
    )
    parser.add_argument("--tsv", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    rows = summarize(collect(args.full, args.constrained, args.treatment))
    tsv = render_tsv(rows)
    markdown = render_markdown(rows)
    if args.tsv:
        args.tsv.write_text(tsv)
    if args.markdown:
        args.markdown.write_text(markdown)
    print(markdown, end="")


if __name__ == "__main__":
    main()
