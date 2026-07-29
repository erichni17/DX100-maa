#!/usr/bin/env python3
"""Compile and validate the live SPARTA six-tally descriptor smoke."""

import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_scalar(path, name):
    prefix = "system.lanl_maa." + name + " "
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return int(float(line.split()[1]))
    return None


def validate_stats(stats, metadata, mode):
    items = metadata["items"]
    channels = metadata["channels"]
    full = mode == "full"
    unsorted_error = mode == "unsorted-error"
    successful_submissions = (
        metadata["successful_submissions"]
        if full
        else 0
        if unsorted_error
        else 1
    )
    submissions = metadata["submissions"] if full else 1
    error_submissions = (
        metadata["error_submissions"] if full else 1 if unsorted_error else 0
    )
    logical_updates = items * channels * successful_submissions
    expected = {
        "descriptorDoorbells": submissions,
        "descriptorBusyRejections": 0,
        "descriptorRearms": submissions - 1,
        "descriptorFetches": submissions,
        "descriptorCompletionWrites": successful_submissions,
        "descriptorErrors": error_submissions,
        "descriptorSpartaContributionsReplayed": logical_updates,
        "descriptorSpartaUpdatesAcknowledged": logical_updates,
        "updateOperationsAcknowledged": logical_updates,
    }
    errors = []
    for name, value in expected.items():
        observed = read_scalar(stats, name)
        if observed != value:
            errors.append(f"{name}: expected {value}, observed {observed}")
    validated = read_scalar(stats, "descriptorSpartaContributionsValidated")
    if unsorted_error and validated != 0:
        errors.append(
            "unsorted cell-group input reached contribution validation: "
            f"observed={validated}"
        )
    elif validated is None or validated < logical_updates:
        errors.append(
            "validation pass did not cover both successful submissions: "
            f"observed={validated} minimum={logical_updates}"
        )
    loaded = read_scalar(stats, "descriptorSpartaItemsLoaded")
    expected_loaded = (
        items * 3 + items - 1 if full else 1 if unsorted_error else items
    )
    if loaded != expected_loaded:
        errors.append(
            f"descriptorSpartaItemsLoaded: expected {expected_loaded}, "
            f"observed {loaded}"
        )
    physical = read_scalar(stats, "physicalAtomicUpdates")
    acknowledgements = read_scalar(stats, "atomicAcknowledgements")
    fp64 = read_scalar(stats, "atomicFp64AddUpdates")
    if unsorted_error:
        if physical != 0:
            errors.append(
                f"unsorted error issued physical atomics: {physical}"
            )
    elif physical is None or physical <= 0 or physical > logical_updates:
        errors.append(f"invalid physical atomic count: {physical}")
    if acknowledgements != physical or fp64 != physical:
        errors.append(
            "physical FP64 atomic accounting did not close: "
            f"issued={physical} ack={acknowledgements} fp64={fp64}"
        )
    if errors:
        raise RuntimeError("; ".join(errors))
    return {
        "logical_updates": logical_updates,
        "validated_contributions": validated,
        "physical_atomic_updates": physical,
        "combiner_hits": read_scalar(stats, "updateCombinerHits"),
        "physical_line_reads": read_scalar(stats, "physicalLineReads"),
        "line_merge_hits": read_scalar(stats, "lineMergeHits"),
        "line_would_block_cycles": read_scalar(stats, "lineWouldBlockCycles"),
        "update_table_would_block_cycles": read_scalar(
            stats, "updateTableWouldBlockCycles"
        ),
        "update_address_busy_cycles": read_scalar(
            stats, "updateAddressBusyCycles"
        ),
        "pending_generations_allocated": read_scalar(
            stats, "descriptorSpartaPendingGenerationsAllocated"
        ),
        "pending_generation_drain_deferrals": read_scalar(
            stats, "spartaPendingGenerationDrainDeferrals"
        ),
        "cell_group_complete_drains": read_scalar(
            stats, "descriptorSpartaCellGroupCompleteDrains"
        ),
        "cell_group_drain_deferrals": read_scalar(
            stats, "descriptorSpartaCellGroupDrainDeferrals"
        ),
        "cell_group_forced_drains": read_scalar(
            stats, "descriptorSpartaCellGroupForcedDrains"
        ),
        "engine_cycles": read_scalar(stats, "engineCycles"),
        "descriptor_cycles": read_scalar(stats, "descriptorCycles"),
    }


def main():
    here = pathlib.Path(__file__).resolve().parent
    repo = here.parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--outdir", required=True, type=pathlib.Path)
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=here / "sparta_tally_cpu_smoke.py",
    )
    parser.add_argument(
        "--source",
        type=pathlib.Path,
        default=repo / "benchmarks/LANL/sparta_tally_cpu_smoke.c",
    )
    parser.add_argument(
        "--metadata",
        type=pathlib.Path,
        default=here / "sparta_tally_cpu_smoke.json",
    )
    parser.add_argument("--line-entries", type=int, default=16)
    parser.add_argument("--update-entries", type=int, default=64)
    parser.add_argument("--update-banks", type=int, default=8)
    parser.add_argument(
        "--cells", type=int, choices=(1, 2, 4, 8, 16, 32, 64), default=16
    )
    parser.add_argument(
        "--mode",
        choices=("full", "sorted", "shuffled", "unsorted-error"),
        default="full",
    )
    parser.add_argument("--sparta-pending-generation", action="store_true")
    parser.add_argument("--sparta-cell-group", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()
    if args.sparta_pending_generation and args.sparta_cell_group:
        parser.error("SPARTA pending-generation and cell-group are exclusive")

    outdir = args.outdir.resolve()
    if outdir.exists():
        raise RuntimeError(f"refusing to reuse evidence directory: {outdir}")
    outdir.mkdir(parents=True)
    compiler = shutil.which("cc")
    if not compiler:
        raise RuntimeError("SPARTA tally smoke requires cc")
    source = args.source.resolve(strict=True)
    metadata_path = args.metadata.resolve(strict=True)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    binary = outdir / "sparta_tally_cpu_smoke.elf"
    mode_number = {
        "full": 0,
        "sorted": 1,
        "shuffled": 2,
        "unsorted-error": 3,
    }[args.mode]
    if args.mode == "unsorted-error" and not args.sparta_cell_group:
        parser.error("unsorted-error mode requires --sparta-cell-group")
    compile_command = [
        compiler,
        "-std=c11",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-nostdlib",
        "-static",
        "-fno-pie",
        "-no-pie",
        "-fno-stack-protector",
        "-fno-builtin",
        "-Wl,--build-id=none",
        "-Wl,-e,_start",
        f"-DSPARTA_TALLY_MODE={mode_number}",
        f"-DSPARTA_TALLY_CELLS={args.cells}",
    ]
    if args.sparta_pending_generation:
        compile_command.append("-DSPARTA_TALLY_PENDING_GENERATION=1")
    if args.sparta_cell_group:
        compile_command.append("-DSPARTA_TALLY_CELL_GROUP=1")
    compile_command.extend([str(source), "-o", str(binary)])
    subprocess.run(compile_command, check=True)
    m5out = outdir / "m5out"
    command = [
        str(args.gem5.resolve(strict=True)),
        f"--outdir={m5out}",
        str(args.config.resolve(strict=True)),
        f"--binary={binary}",
        f"--metadata={metadata_path}",
        f"--line-entries={args.line_entries}",
        f"--update-entries={args.update_entries}",
        f"--update-banks={args.update_banks}",
    ]
    report = {
        "schema": "lanl-maa-sparta-six-tally-live-v1",
        "status": "running",
        "mode": args.mode,
        "cells": args.cells,
        "sparta_pending_generation": args.sparta_pending_generation,
        "sparta_cell_group": args.sparta_cell_group,
        "claim_boundary": (
            "SPARTA-derived six-channel scatter-add contract only; not a "
            "native SPARTA ABI, application speedup, or synthesized cost."
        ),
        "source_sha256": file_sha256(source),
        "metadata_sha256": file_sha256(metadata_path),
        "gem5_sha256": file_sha256(args.gem5.resolve(strict=True)),
        "binary_sha256": file_sha256(binary),
        "compile_command": [str(argument) for argument in compile_command],
        "command": command,
    }
    report_path = outdir / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    try:
        subprocess.run(
            command,
            check=True,
            timeout=args.timeout_seconds,
            stdout=(outdir / "stdout.log").open("w", encoding="utf-8"),
            stderr=(outdir / "stderr.log").open("w", encoding="utf-8"),
        )
        report["metrics"] = validate_stats(
            m5out / "stats.txt", metadata, args.mode
        )
        report["status"] = "validated"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        report_path.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
