#!/usr/bin/env python3

import argparse
import json
import pathlib
import shutil
import subprocess
import tempfile

from run_sparta_descriptor_staging_smoke import (
    read_stats,
    validate_stats,
)

FIXED_METADATA = {
    "descriptor_opcode": 3,
    "native_cell_count": 64,
    "state_records_per_native_cell": 1,
    "record_count": 64,
    "record_bytes": 512,
    "native_packed_cell_bytes": 512,
    "maximum_steps": 8,
    "descriptor_items": 8,
    "executed_record_visits": 41,
    "start_states": [
        268435456,
        134217728,
        100663296,
        117440512,
        268435456,
        218103808,
        100663297,
        201326593,
    ],
    "root_visits": [8, 4, 3, 3, 8, 6, 3, 6],
    "final_cells": [20, 60, 62, 9, 20, 13, 30, 11],
    "expected_results": [330, 189, 128, 13, 330, 51, 74, 137],
}


def validate_metadata(metadata):
    errors = []
    for name, expected in FIXED_METADATA.items():
        if metadata.get(name) != expected:
            errors.append(
                f"metadata {name}: expected {expected}, "
                f"got {metadata.get(name)}"
            )
    native_bytes = metadata.get("native_packed_cell_bytes")
    record_bytes = metadata.get("record_bytes")
    if record_bytes != native_bytes:
        errors.append(
            "compact records must exactly match packed-cell bytes, "
            f"got {record_bytes}/{native_bytes}"
        )
    for index, state in enumerate(metadata.get("start_states", [])):
        visits = (state >> 25) & 0xFFFFFFFF
        reserved = state >> 57
        if visits != metadata["root_visits"][index] or reserved != 0:
            errors.append(
                f"invalid start state {index}: visits={visits}, "
                f"reserved={reserved}"
            )
    if errors:
        raise RuntimeError(
            "SPARTA compact descriptor metadata changed:\n  "
            + "\n  ".join(errors)
        )


def build_staging(root):
    compiler = shutil.which("g++")
    assembler = shutil.which("cc")
    linker = shutil.which("ld")
    if not compiler or not assembler or not linker:
        raise RuntimeError(
            "SPARTA compact smoke requires g++, cc, and ld"
        )
    repo = pathlib.Path(__file__).resolve().parents[2]
    benchmark = root / "sparta_particle_cell_step"
    assembly = root / "sparta_compact_descriptor_image.S"
    metadata_path = root / "sparta_compact_descriptor_metadata.json"
    object_path = root / "sparta_compact_descriptor_image.o"
    image = root / "sparta_compact_descriptor_image.elf"

    subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            repo / "src",
            repo / "benchmarks/LANL/sparta_particle_cell_step.cc",
            "-o",
            benchmark,
        ],
        check=True,
    )
    benchmark_result = subprocess.run(
        [
            benchmark,
            "--particles",
            "256",
            "--cells",
            "64",
            "--visits",
            "8",
            "--window",
            "16",
            "--line-entries",
            "8",
            "--contexts",
            "4",
            "--combiner-entries",
            "16",
            "--combiner-banks",
            "4",
            "--descriptor-items",
            "8",
            "--seed",
            "0x535041525441",
            "--order",
            "sorted",
            "--emit-compact-descriptor-assembly",
            assembly,
            "--emit-compact-descriptor-metadata",
            metadata_path,
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    (root / "benchmark.stdout").write_text(
        benchmark_result.stdout, encoding="utf-8"
    )
    if "verification=PASS" not in benchmark_result.stdout:
        raise RuntimeError("SPARTA scalar/reference-model verification failed")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    validate_metadata(metadata)
    subprocess.run([assembler, "-c", assembly, "-o", object_path], check=True)
    subprocess.run(
        [
            linker,
            "-T",
            repo / "tests/lanl_maa/gather_image.ld",
            "-o",
            image,
            object_path,
        ],
        check=True,
    )
    return image, metadata_path, metadata


def run_smoke(args, root):
    image, metadata_path, metadata = build_staging(root)
    outdir = root / "m5out"
    command = [
        str(args.gem5.resolve()),
        f"--outdir={outdir}",
        str(args.config.resolve()),
        f"--image={image}",
        f"--metadata={metadata_path}",
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    if args.outdir:
        (root / "gem5.stdout").write_text(result.stdout, encoding="utf-8")
        (root / "gem5.stderr").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            "gem5 SPARTA compact descriptor smoke failed:\n"
            + result.stdout
            + result.stderr
        )
    validate_stats(read_stats(outdir / "stats.txt"), metadata)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument(
        "--config",
        default=pathlib.Path(__file__).with_name(
            "descriptor_staging_smoke.py"
        ),
        type=pathlib.Path,
    )
    parser.add_argument(
        "--outdir",
        type=pathlib.Path,
        help="Preserve benchmark, compact image, logs, and m5out evidence",
    )
    args = parser.parse_args()

    if args.outdir:
        root = args.outdir.resolve()
        if root.exists():
            raise RuntimeError(f"refusing to reuse evidence directory: {root}")
        root.mkdir(parents=True)
        run_smoke(args, root)
    else:
        with tempfile.TemporaryDirectory(
            prefix="lanl-maa-sparta-compact-descriptor-"
        ) as root:
            run_smoke(args, pathlib.Path(root))

    print("LANLMAA SPARTA compact descriptor smoke: PASS")


if __name__ == "__main__":
    main()
