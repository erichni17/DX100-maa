#!/usr/bin/env python3
"""Estimate Row-Table partition capacity from an exact gather mapping trace."""

import argparse
import csv
import math
import re
from collections import defaultdict
from pathlib import Path


INSERT_RE = re.compile(
    r"paddr\(0x(?P<paddr>[0-9a-f]+)\).*grow\(0x(?P<grow>[0-9a-f]+)\)"
    r".*to T\[(?P<slice>[0-9]+)\]"
)
RESULT_RE = re.compile(
    r"^VIRTUAL_TILE_CONSUMER_RESULT mode=paged_overlap "
    r"page_elements=4096 hash=(?P<hash>[0-9]+) errors=0$",
    re.MULTILINE,
)


def parse_trace(trace: Path) -> dict[tuple[int, int], set[int]]:
    line_mapping = {}
    groups = defaultdict(set)
    for line in trace.read_text().splitlines():
        if "fillRowTable: inserting vaddr" not in line:
            continue
        match = INSERT_RE.search(line)
        if match is None:
            raise ValueError(f"malformed Row-Table insertion: {line!r}")
        paddr = int(match.group("paddr"), 16)
        grow = int(match.group("grow"), 16)
        table_slice = int(match.group("slice"))
        cache_line = paddr // 64
        mapping = (table_slice, grow)
        previous = line_mapping.setdefault(cache_line, mapping)
        if previous != mapping:
            raise ValueError(
                f"cache line {cache_line:#x} maps to both {previous} and {mapping}"
            )
        groups[mapping].add(cache_line)
    if not groups:
        raise ValueError(f"no Row-Table insertions found in {trace}")
    return groups


def validate_run(run_root: Path, expected_hash: str) -> None:
    if (run_root / "restore.exit").read_text().strip() != "0":
        raise ValueError("mapping run did not exit successfully")
    matches = RESULT_RE.findall((run_root / "restore.log").read_text())
    if matches != [expected_hash]:
        raise ValueError(
            f"mapping run lacks one exact output marker for hash {expected_hash}"
        )


def group_weights(groups: dict[tuple[int, int], set[int]], entries: int):
    if entries <= 0:
        raise ValueError("entries per Row-Table row must be positive")
    return {
        key: math.ceil(len(cache_lines) / entries)
        for key, cache_lines in groups.items()
    }


def modulo_loads(weights: dict[tuple[int, int], int], partitions: int):
    loads = defaultdict(lambda: [0] * partitions)
    for (table_slice, grow), weight in weights.items():
        loads[table_slice][grow % partitions] += weight
    return loads


def greedy_loads(weights: dict[tuple[int, int], int], partitions: int):
    by_slice = defaultdict(list)
    for (table_slice, _grow), weight in weights.items():
        by_slice[table_slice].append(weight)
    loads = {}
    for table_slice, values in by_slice.items():
        bins = [0] * partitions
        for weight in sorted(values, reverse=True):
            target = min(range(partitions), key=lambda index: bins[index])
            bins[target] += weight
        loads[table_slice] = bins
    return loads


def summarize_loads(loads, rows_per_slice: int) -> dict[str, str]:
    values = [load for bins in loads.values() for load in bins]
    excess = [max(0, load - rows_per_slice) for load in values]
    return {
        "max_rows_in_slice_partition": str(max(values)),
        "overflowing_slice_partitions": str(sum(value > 0 for value in excess)),
        "total_excess_rows": str(sum(excess)),
        "fits_rows_per_slice": str(not any(excess)).lower(),
    }


def analyze(groups, entries: int, rows_per_slice: int, partitions: list[int]):
    weights = group_weights(groups, entries)
    unique_lines = len(set().union(*groups.values()))
    required_rows = sum(weights.values())
    rows = []
    for partition_count in partitions:
        if partition_count <= 0:
            raise ValueError("partition counts must be positive")
        for policy, loads in (
            ("grow_modulo", modulo_loads(weights, partition_count)),
            ("greedy_per_slice_oracle", greedy_loads(weights, partition_count)),
        ):
            rows.append(
                {
                    "policy": policy,
                    "partitions": str(partition_count),
                    "unique_source_lines": str(unique_lines),
                    "unique_grow_groups": str(len(groups)),
                    "required_row_slots": str(required_rows),
                    "available_row_slots_across_passes": str(
                        len(loads) * rows_per_slice * partition_count
                    ),
                    **summarize_loads(loads, rows_per_slice),
                }
            )
    return rows


def render_tsv(rows: list[dict[str, str]]) -> str:
    fields = list(rows[0])
    output = ["\t".join(fields)]
    output.extend("\t".join(row[field] for field in fields) for row in rows)
    return "\n".join(output) + "\n"


def parse_partitions(value: str) -> list[int]:
    try:
        values = [int(item) for item in value.split(",")]
    except ValueError as error:
        raise argparse.ArgumentTypeError("partitions must be integers") from error
    if not values or any(value <= 0 for value in values):
        raise argparse.ArgumentTypeError("partitions must be positive")
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--expected-hash", default="7228541527853630339")
    parser.add_argument("--entries-per-row", type=int, default=8)
    parser.add_argument("--rows-per-slice", type=int, default=32)
    parser.add_argument("--partitions", type=parse_partitions, default=[1, 2, 3, 4])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    validate_run(args.run_root, args.expected_hash)
    groups = parse_trace(args.run_root / "run/virtual_trace.log")
    rows = analyze(
        groups,
        args.entries_per_row,
        args.rows_per_slice,
        args.partitions,
    )
    output = render_tsv(rows)
    if args.output:
        args.output.write_text(output)
    print(output, end="")


if __name__ == "__main__":
    main()
