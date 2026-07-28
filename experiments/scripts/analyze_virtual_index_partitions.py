#!/usr/bin/env python3
"""Estimate bounded metadata and index traffic for multi-pass gathers."""

import argparse
import csv
import math
from pathlib import Path
from typing import Iterable


def affine_indices(count: int, multiplier: int, addend: int, modulus: int) -> list[int]:
    if count <= 0 or modulus <= 0:
        raise ValueError("affine count and modulus must be positive")
    return [(iteration * multiplier + addend) % modulus for iteration in range(count)]


def read_indices(path: Path) -> list[int]:
    indices = []
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        try:
            index = int(value, 0)
        except ValueError as error:
            raise ValueError(f"invalid index at {path}:{line_number}: {value!r}") from error
        if index < 0:
            raise ValueError(f"negative index at {path}:{line_number}")
        indices.append(index)
    if not indices:
        raise ValueError(f"no indices found in {path}")
    return indices


def lines_touched(byte_count: int, line_bytes: int, base_offset: int = 0) -> int:
    if byte_count <= 0 or line_bytes <= 0:
        raise ValueError("byte count and cache-line size must be positive")
    if base_offset < 0 or base_offset >= line_bytes:
        raise ValueError("base offset must be within one cache line")
    return math.ceil((base_offset + byte_count) / line_bytes)


def assign_partition(
    source_line: int, source_lines: int, partitions: int, policy: str
) -> int:
    if source_line < 0 or source_line >= source_lines:
        raise ValueError(
            f"source line {source_line} is outside configured range [0, {source_lines})"
        )
    if policy == "range":
        return min(source_line * partitions // source_lines, partitions - 1)
    if policy == "modulo":
        return source_line % partitions
    raise ValueError(f"unknown partition policy: {policy}")


def analyze(
    indices: Iterable[int],
    source_elements: int,
    partitions: Iterable[int],
    word_bytes: int,
    index_bytes: int,
    line_bytes: int,
    word_capacity: int,
    row_descriptor_capacity: int,
    index_base_offset: int = 0,
    source_base_offset: int = 0,
    policies: Iterable[str] = ("range", "modulo"),
) -> list[dict[str, str]]:
    values = list(indices)
    if not values:
        raise ValueError("at least one index is required")
    if source_elements <= 0 or word_bytes <= 0 or index_bytes <= 0:
        raise ValueError("element counts and element sizes must be positive")
    if word_capacity <= 0 or row_descriptor_capacity <= 0:
        raise ValueError("metadata capacities must be positive")
    if any(index >= source_elements for index in values):
        raise ValueError("an index exceeds the configured source range")

    if source_base_offset < 0 or source_base_offset >= line_bytes:
        raise ValueError("source base offset must be within one cache line")
    source_lines = lines_touched(
        source_elements * word_bytes, line_bytes, source_base_offset
    )
    source_line_ids = [
        (source_base_offset + index * word_bytes) // line_bytes
        for index in values
    ]
    unique_source_lines = len(set(source_line_ids))
    index_lines_per_scan = lines_touched(
        len(values) * index_bytes, line_bytes, index_base_offset
    )
    rows = []
    for policy in policies:
        for partition_count in partitions:
            if partition_count <= 0:
                raise ValueError("partition counts must be positive")
            words_by_partition = [0] * partition_count
            lines_by_partition = [set() for _ in range(partition_count)]
            for source_line in source_line_ids:
                partition = assign_partition(
                    source_line, source_lines, partition_count, policy
                )
                words_by_partition[partition] += 1
                lines_by_partition[partition].add(source_line)
            unique_by_partition = [len(lines) for lines in lines_by_partition]
            rows.append(
                {
                    "policy": policy,
                    "partitions": str(partition_count),
                    "index_scans": str(partition_count),
                    "index_line_reads_lower_bound": str(
                        partition_count * index_lines_per_scan
                    ),
                    "extra_index_bytes": str(
                        (partition_count - 1) * len(values) * index_bytes
                    ),
                    "total_words": str(len(values)),
                    "unique_source_lines": str(unique_source_lines),
                    "source_line_requests_oracle": str(
                        sum(unique_by_partition)
                    ),
                    "max_words_per_partition": str(max(words_by_partition)),
                    "min_words_per_partition": str(min(words_by_partition)),
                    "max_unique_lines_per_partition": str(
                        max(unique_by_partition)
                    ),
                    "min_unique_lines_per_partition": str(
                        min(unique_by_partition)
                    ),
                    "fits_word_capacity": str(
                        max(words_by_partition) <= word_capacity
                    ).lower(),
                    "fits_row_descriptor_capacity": str(
                        max(unique_by_partition) <= row_descriptor_capacity
                    ).lower(),
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
        partitions = [int(item) for item in value.split(",")]
    except ValueError as error:
        raise argparse.ArgumentTypeError("partitions must be comma-separated integers") from error
    if not partitions or any(partition <= 0 for partition in partitions):
        raise argparse.ArgumentTypeError("partition counts must be positive")
    return partitions


def parse_policies(value: str) -> list[str]:
    policies = value.split(",")
    if not policies or any(policy not in ("range", "modulo") for policy in policies):
        raise argparse.ArgumentTypeError("policies must contain range and/or modulo")
    return policies


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--indices", type=Path)
    source.add_argument("--affine-count", type=int)
    parser.add_argument("--affine-multiplier", type=int, default=1)
    parser.add_argument("--affine-addend", type=int, default=0)
    parser.add_argument("--affine-modulus", type=int)
    parser.add_argument("--source-elements", type=int)
    parser.add_argument("--partitions", type=parse_partitions, default=[1, 2, 4, 8])
    parser.add_argument("--word-bytes", type=int, default=8)
    parser.add_argument("--index-bytes", type=int, default=4)
    parser.add_argument("--line-bytes", type=int, default=64)
    parser.add_argument("--index-base-offset", type=int, default=0)
    parser.add_argument("--source-base-offset", type=int, default=0)
    parser.add_argument(
        "--policies", type=parse_policies, default=["range", "modulo"]
    )
    parser.add_argument("--word-capacity", type=int, default=4096)
    parser.add_argument("--row-descriptor-capacity", type=int, default=4096)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.indices:
        indices = read_indices(args.indices)
        if args.source_elements is None:
            parser.error("--source-elements is required with --indices")
        source_elements = args.source_elements
    else:
        if args.affine_modulus is None:
            parser.error("--affine-modulus is required with --affine-count")
        indices = affine_indices(
            args.affine_count,
            args.affine_multiplier,
            args.affine_addend,
            args.affine_modulus,
        )
        source_elements = args.source_elements or args.affine_modulus

    rows = analyze(
        indices,
        source_elements,
        args.partitions,
        args.word_bytes,
        args.index_bytes,
        args.line_bytes,
        args.word_capacity,
        args.row_descriptor_capacity,
        args.index_base_offset,
        args.source_base_offset,
        args.policies,
    )
    output = render_tsv(rows)
    if args.output:
        args.output.write_text(output)
    print(output, end="")


if __name__ == "__main__":
    main()
