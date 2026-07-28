#!/usr/bin/env python3
"""Validate and compare an equalized native-direct/virtual destination pair."""

import argparse
import csv
from pathlib import Path


MATCHED_MANIFEST_KEYS = (
    "logical_tile_elements",
    "row_table_slices",
    "virtual_grow_order",
    "virtual_response_slots",
    "virtual_response_word_pool",
    "source_commit",
    "timeout",
)
OPTIONAL_MATCHED_MANIFEST_KEYS = (
    "virtual_combine_slots",
    "virtual_combine_words",
    "virtual_combine_ways",
    "virtual_combine_victim_policy",
    "virtual_combine_banks",
)
MATCHED_RESULT_KEYS = (
    "output_hash",
    "index_line_reads",
    "index_words",
    "row_table_slices",
    "virtual_grow_order",
    "response_slots",
    "response_word_pool",
)
MATCHED_ARTIFACTS = (
    "gem5.opt",
    "test_virtual_tile_consumer_T16384",
    "se.py",
    "example_gem5_config.yaml",
    "run_virtual_tile_consumer_case.sh",
    "test_virtual_tile_consumer.cpp",
    "IndirectAccess.cc",
    "IndirectAccess.hh",
    "source.diff",
    "source_status.txt",
)


def read_manifest(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            raise ValueError(f"invalid manifest line in {path}: {line!r}")
        values[key] = value
    return values


def read_result(path: Path) -> dict[str, str]:
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(rows) != 1:
        raise ValueError(f"expected one result row in {path}, found {len(rows)}")
    return rows[0]


def read_hashes(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        digest, artifact = line.split(maxsplit=1)
        name = Path(artifact).name
        if name in values:
            raise ValueError(f"duplicate artifact basename {name!r} in {path}")
        values[name] = digest
    return values


def require_equal(native: dict[str, str], virtual: dict[str, str],
                  keys: tuple[str, ...], label: str) -> None:
    for key in keys:
        if key not in native or key not in virtual:
            raise ValueError(f"missing {label} key {key!r}")
        if native[key] != virtual[key]:
            raise ValueError(
                f"mismatched {label} {key}: native={native[key]!r} "
                f"virtual={virtual[key]!r}"
            )


def integer_delta(native: dict[str, str], virtual: dict[str, str],
                  key: str) -> int:
    return int(virtual[key]) - int(native[key])


def compare(native_dir: Path, virtual_dir: Path) -> dict[str, str]:
    native_manifest = read_manifest(native_dir / "manifest.txt")
    virtual_manifest = read_manifest(virtual_dir / "manifest.txt")
    native_result = read_result(native_dir / "result.tsv")
    virtual_result = read_result(virtual_dir / "result.tsv")
    native_hashes = read_hashes(native_dir / "artifact_sha256.txt")
    virtual_hashes = read_hashes(virtual_dir / "artifact_sha256.txt")

    if native_manifest.get("case") != "native_direct_16k":
        raise ValueError("native input is not native_direct_16k")
    if virtual_manifest.get("case") != "paged_overlap_4k":
        raise ValueError("virtual input is not paged_overlap_4k")
    require_equal(native_manifest, virtual_manifest, MATCHED_MANIFEST_KEYS,
                  "manifest")
    for key in OPTIONAL_MATCHED_MANIFEST_KEYS:
        if key in native_manifest or key in virtual_manifest:
            require_equal(native_manifest, virtual_manifest, (key,),
                          "manifest")
    require_equal(native_result, virtual_result, MATCHED_RESULT_KEYS, "result")
    require_equal(native_hashes, virtual_hashes, MATCHED_ARTIFACTS, "artifact")

    native_ticks = int(native_result["simTicks"])
    virtual_ticks = int(virtual_result["simTicks"])
    if native_ticks <= 0 or virtual_ticks <= 0:
        raise ValueError("simTicks must be positive")

    return {
        "native_case": native_result["case"],
        "virtual_case": virtual_result["case"],
        "output_hash": native_result["output_hash"],
        "native_ticks": str(native_ticks),
        "virtual_ticks": str(virtual_ticks),
        "tick_delta": str(virtual_ticks - native_ticks),
        "virtual_overhead_percent": f"{(virtual_ticks / native_ticks - 1) * 100:.6f}",
        "source_read_delta": str(integer_delta(native_result, virtual_result,
                                                "source_reads")),
        "backing_write_delta": str(integer_delta(native_result, virtual_result,
                                                  "write_issues")),
        "dram_read_delta": str(integer_delta(native_result, virtual_result,
                                              "dram_reads")),
        "dram_activate_delta": str(integer_delta(native_result, virtual_result,
                                                  "dram_activates")),
        "dram_precharge_delta": str(integer_delta(native_result, virtual_result,
                                                   "dram_precharges")),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("native_dir", type=Path)
    parser.add_argument("virtual_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    summary = compare(args.native_dir, args.virtual_dir)
    output = "\t".join(summary) + "\n" + "\t".join(summary.values()) + "\n"
    if args.output:
        args.output.write_text(output)
    print(output, end="")


if __name__ == "__main__":
    main()
