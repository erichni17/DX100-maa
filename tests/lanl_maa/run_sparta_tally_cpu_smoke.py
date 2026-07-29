#!/usr/bin/env python3
"""Compile and validate the live SPARTA six-tally descriptor smoke."""

import argparse
import hashlib
import json
import math
import pathlib
import re
import shutil
import struct
import subprocess

NATIVE_BATCH_SCHEMA = "sparta-lanl-maa-thermal-grid-batch-v1"
NATIVE_BATCH_SOURCE_REVISION = "ca0ce28fd76080d8b2828db77adde14fdc382c76"
NATIVE_BATCH_KEYS = {
    "schema",
    "source_revision",
    "rank",
    "timestep",
    "native_particle_count",
    "eligible_particle_count",
    "item_count",
    "cell_count",
    "target_mixture_group",
    "max_abs_tally_error",
    "max_rel_tally_error",
    "application_tally_matches_batch",
    "items",
    "nonzero_cell_tallies",
}
HEX_BITS = re.compile(r"^[0-9a-f]{16}$")
TALLY_MISMATCH = re.compile(
    r"^LANL_MAA_TALLY_MISMATCH element=0x([0-9a-f]{8}) "
    r"observed=0x([0-9a-f]{16}) expected=0x([0-9a-f]{16})$"
)
NATIVE_RELATIVE_TOLERANCE = 1.0e-12


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_int(value, name):
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return value


def _require_number(value, name):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{name} must be a finite number")
    return value


def _decode_bits(value, name):
    if not isinstance(value, str) or not HEX_BITS.fullmatch(value):
        raise ValueError(f"{name} must be 16 lowercase hexadecimal digits")
    decoded = struct.unpack(">d", bytes.fromhex(value))[0]
    if not math.isfinite(decoded):
        raise ValueError(f"{name} must encode a finite FP64 value")
    return decoded


def _encode_bits(value):
    return struct.pack(">d", value).hex()


def load_native_batch(path):
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or set(document) != NATIVE_BATCH_KEYS:
        raise ValueError("native batch has an unexpected top-level shape")
    if document["schema"] != NATIVE_BATCH_SCHEMA:
        raise ValueError("native batch schema mismatch")
    if document["source_revision"] != NATIVE_BATCH_SOURCE_REVISION:
        raise ValueError("native batch source revision mismatch")
    for name in ("rank", "target_mixture_group"):
        if _require_int(document[name], name) != 0:
            raise ValueError(f"native batch {name} must be zero")
    timestep = _require_int(document["timestep"], "timestep")
    if timestep < 0:
        raise ValueError("native batch timestep must be nonnegative")
    for name in (
        "native_particle_count",
        "eligible_particle_count",
        "item_count",
    ):
        if _require_int(document[name], name) != 64:
            raise ValueError(f"native batch {name} must be 64")
    cells = _require_int(document["cell_count"], "cell_count")
    if cells < 1 or cells > 64:
        raise ValueError("native batch cell_count must be in [1, 64]")
    if (
        _require_number(document["max_abs_tally_error"], "max_abs_tally_error")
        != 0
        or _require_number(
            document["max_rel_tally_error"], "max_rel_tally_error"
        )
        != 0
    ):
        raise ValueError("native batch must match SPARTA tallies exactly")
    if document["application_tally_matches_batch"] is not True:
        raise ValueError("native batch lacks its SPARTA tally oracle")

    items = document["items"]
    if not isinstance(items, list) or len(items) != 64:
        raise ValueError("native batch must contain exactly 64 items")
    particle_indices = set()
    particle_ids = set()
    previous_cell = -1
    contributions = []
    sums = [[0.0] * 6 for _ in range(cells)]
    indices = []
    for item_number, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != {
            "particle_index",
            "particle_id",
            "cell",
            "contribution_bits",
        }:
            raise ValueError(f"native item {item_number} has an invalid shape")
        particle_index = _require_int(
            item["particle_index"], f"items[{item_number}].particle_index"
        )
        particle_id = _require_int(
            item["particle_id"], f"items[{item_number}].particle_id"
        )
        cell = _require_int(item["cell"], f"items[{item_number}].cell")
        if particle_index < 0 or particle_index >= 64:
            raise ValueError("native particle index is out of range")
        if particle_index in particle_indices or particle_id in particle_ids:
            raise ValueError("native particle identity is not unique")
        if cell < previous_cell or cell < 0 or cell >= cells:
            raise ValueError("native cells are not sorted and in range")
        bits = item["contribution_bits"]
        if not isinstance(bits, list) or len(bits) != 6:
            raise ValueError("native item must contain six contribution bits")
        values = [
            _decode_bits(value, f"items[{item_number}].contribution_bits")
            for value in bits
        ]
        if values[0] != 1.0:
            raise ValueError("native item count contribution must equal one")
        particle_indices.add(particle_index)
        particle_ids.add(particle_id)
        previous_cell = cell
        indices.append(cell)
        contributions.extend(bits)
        sums[cell] = [left + right for left, right in zip(sums[cell], values)]

    tallies = document["nonzero_cell_tallies"]
    populated_cells = sorted(set(indices))
    if not isinstance(tallies, list) or len(tallies) != len(populated_cells):
        raise ValueError(
            "native batch tally coverage does not match its cells"
        )
    expected_bits = ["0000000000000000"] * (cells * 6)
    for position, (cell, tally) in enumerate(zip(populated_cells, tallies)):
        if not isinstance(tally, dict) or set(tally) != {
            "cell",
            "batch_bits",
            "sparta_bits",
        }:
            raise ValueError(f"native tally {position} has an invalid shape")
        if _require_int(tally["cell"], f"tallies[{position}].cell") != cell:
            raise ValueError("native batch tally cells are not canonical")
        recomputed = [_encode_bits(value) for value in sums[cell]]
        if (
            tally["batch_bits"] != recomputed
            or tally["sparta_bits"] != recomputed
        ):
            raise ValueError("native batch tally bits fail recomputation")
        expected_bits[cell * 6 : (cell + 1) * 6] = recomputed
    return {
        "cell_count": cells,
        "indices": indices,
        "contribution_bits": contributions,
        "expected_bits": expected_bits,
        "source_revision": document["source_revision"],
        "timestep": timestep,
    }


def write_native_header(path, batch):
    def array(name, ctype, values, per_line):
        lines = [f"static const {ctype} {name}[{len(values)}] = {{"]
        for start in range(0, len(values), per_line):
            chunk = values[start : start + per_line]
            lines.append("    " + ", ".join(chunk) + ",")
        lines.append("};")
        return "\n".join(lines)

    indices = [f"UINT32_C({value})" for value in batch["indices"]]
    contributions = [
        f"UINT64_C(0x{value})" for value in batch["contribution_bits"]
    ]
    expected = [f"UINT64_C(0x{value})" for value in batch["expected_bits"]]
    text = "\n".join(
        (
            "#ifndef SPARTA_NATIVE_BATCH_H",
            "#define SPARTA_NATIVE_BATCH_H",
            "",
            "#include <stdint.h>",
            "",
            array("sparta_native_indices", "uint32_t", indices, 8),
            "",
            array(
                "sparta_native_contribution_bits", "uint64_t", contributions, 4
            ),
            "",
            array("sparta_native_expected_bits", "uint64_t", expected, 4),
            "",
            "#endif",
            "",
        )
    )
    path.write_text(text, encoding="utf-8")


def read_tally_diagnostics(path, element_count=None):
    records = []
    elements = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("LANL_MAA_TALLY_MISMATCH"):
            continue
        match = TALLY_MISMATCH.fullmatch(line)
        if match is None:
            raise ValueError("malformed native tally mismatch diagnostic")
        element_text, observed_bits, expected_bits = match.groups()
        element = int(element_text, 16)
        if element_count is not None and element >= element_count:
            raise ValueError("native tally diagnostic element is out of range")
        if element in elements:
            raise ValueError("duplicate native tally mismatch diagnostic")
        elements.add(element)
        observed = _decode_bits(observed_bits, "observed tally")
        expected = _decode_bits(expected_bits, "expected tally")
        absolute_error = abs(observed - expected)
        relative_error = (
            absolute_error / abs(expected) if expected != 0.0 else None
        )
        observed_ordered = int(observed_bits, 16)
        expected_ordered = int(expected_bits, 16)
        if observed_ordered >> 63:
            observed_ordered = (~observed_ordered) & ((1 << 64) - 1)
        else:
            observed_ordered |= 1 << 63
        if expected_ordered >> 63:
            expected_ordered = (~expected_ordered) & ((1 << 64) - 1)
        else:
            expected_ordered |= 1 << 63
        records.append(
            {
                "element": element,
                "cell": element // 6,
                "channel": element % 6,
                "observed_bits": observed_bits,
                "expected_bits": expected_bits,
                "ulp_distance": abs(observed_ordered - expected_ordered),
                "absolute_error": absolute_error,
                "relative_error": relative_error,
            }
        )
    relative_errors = [
        record["relative_error"]
        for record in records
        if record["relative_error"] is not None
    ]
    if any(record["relative_error"] is None for record in records):
        raise ValueError("native accelerator changed an exact zero tally")
    maximum_relative = max(relative_errors, default=0.0)
    if maximum_relative > NATIVE_RELATIVE_TOLERANCE:
        raise ValueError("native tally mismatch exceeds relative tolerance")
    return {
        "comparison": "exact-zero-and-relative-nonzero",
        "relative_tolerance": NATIVE_RELATIVE_TOLERANCE,
        "bit_mismatch_count": len(records),
        "mismatch_elements": [record["element"] for record in records],
        "max_ulp_distance": max(
            (record["ulp_distance"] for record in records), default=0
        ),
        "max_absolute_error": max(
            (record["absolute_error"] for record in records), default=0.0
        ),
        "max_relative_error": maximum_relative,
        "records": records,
    }


def read_scalar(path, name):
    prefix = (
        name if name.startswith("system.") else "system.lanl_maa." + name
    ) + " "
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
        "cpu_cycles": read_scalar(stats, "system.cpu.numCycles"),
        "cpu_committed_instructions": read_scalar(
            stats, "system.cpu.commitStats0.numInsts"
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
    parser.add_argument("--cells", type=int)
    parser.add_argument("--first-cell-items", type=int)
    parser.add_argument(
        "--mode",
        choices=("full", "sorted", "shuffled", "unsorted-error"),
        default="full",
    )
    parser.add_argument("--sparta-pending-generation", action="store_true")
    parser.add_argument("--sparta-cell-group", action="store_true")
    parser.add_argument("--sparta-cell-list-staging", action="store_true")
    parser.add_argument("--sparta-native-batch", type=pathlib.Path)
    parser.add_argument("--native-timing-clean", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()
    if args.sparta_pending_generation and args.sparta_cell_group:
        parser.error("SPARTA pending-generation and cell-group are exclusive")
    native_path = None
    native_batch = None
    if args.sparta_native_batch is not None:
        if args.mode != "sorted":
            parser.error("--sparta-native-batch requires --mode sorted")
        if args.sparta_cell_list_staging:
            parser.error("native batch is already in SPARTA cell-list order")
        if args.first_cell_items is not None:
            parser.error("native batch excludes synthetic skew generation")
        try:
            native_path = args.sparta_native_batch.resolve(strict=True)
            native_batch = load_native_batch(native_path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            parser.error(f"invalid SPARTA native batch: {error}")
        if args.cells is not None and args.cells != native_batch["cell_count"]:
            parser.error("--cells disagrees with the native batch")
        cells = native_batch["cell_count"]
    else:
        if args.native_timing_clean:
            parser.error(
                "--native-timing-clean requires --sparta-native-batch"
            )
        cells = 16 if args.cells is None else args.cells
        if cells < 1 or cells > 64 or 64 % cells != 0:
            parser.error("--cells must be a positive divisor of 64")
    if args.first_cell_items is not None:
        if args.mode != "sorted" or cells != 2:
            parser.error("--first-cell-items requires --mode sorted --cells 2")
        if args.first_cell_items < 1 or args.first_cell_items >= 64:
            parser.error("--first-cell-items must be in [1, 63]")
    if args.sparta_cell_list_staging and args.mode != "sorted":
        parser.error("--sparta-cell-list-staging requires --mode sorted")

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
        f"-DSPARTA_TALLY_CELLS={cells}",
    ]
    if args.sparta_pending_generation:
        compile_command.append("-DSPARTA_TALLY_PENDING_GENERATION=1")
    if args.sparta_cell_group:
        compile_command.append("-DSPARTA_TALLY_CELL_GROUP=1")
    if args.first_cell_items is not None:
        compile_command.append(
            f"-DSPARTA_TALLY_FIRST_CELL_ITEMS={args.first_cell_items}"
        )
    if args.sparta_cell_list_staging:
        compile_command.append("-DSPARTA_TALLY_CELL_LIST_STAGING=1")
    native_header = None
    if native_batch is not None:
        native_header = outdir / "sparta_native_batch.h"
        write_native_header(native_header, native_batch)
        compile_command.extend(
            ("-DSPARTA_TALLY_NATIVE_BATCH=1", "-include", str(native_header))
        )
        if args.native_timing_clean:
            compile_command.append("-DSPARTA_TALLY_REPORT_MISMATCHES=0")
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
        "schema": (
            "lanl-maa-sparta-native-batch-live-v1"
            if native_batch is not None
            else "lanl-maa-sparta-six-tally-live-v1"
        ),
        "status": "running",
        "mode": args.mode,
        "cells": cells,
        "first_cell_items": args.first_cell_items,
        "sparta_pending_generation": args.sparta_pending_generation,
        "sparta_cell_group": args.sparta_cell_group,
        "sparta_cell_list_staging": args.sparta_cell_list_staging,
        "sparta_native_batch": native_batch is not None,
        "native_timing_clean": args.native_timing_clean,
        "claim_boundary": (
            "Exact 64-particle batch exported by pinned native SPARTA; "
            "lightweight descriptor timing only, not application speedup or "
            "synthesized cost."
            if native_batch is not None
            else "SPARTA-derived six-channel scatter-add contract only; not "
            "a native SPARTA ABI, application speedup, or synthesized cost."
        ),
        "source_sha256": file_sha256(source),
        "metadata_sha256": file_sha256(metadata_path),
        "gem5_sha256": file_sha256(args.gem5.resolve(strict=True)),
        "binary_sha256": file_sha256(binary),
        "compile_command": [str(argument) for argument in compile_command],
        "command": command,
    }
    if native_batch is not None:
        report["native_batch_path"] = str(native_path)
        report["native_batch_sha256"] = file_sha256(native_path)
        report["native_batch_source_revision"] = native_batch[
            "source_revision"
        ]
        report["native_batch_timestep"] = native_batch["timestep"]
        report["native_header_sha256"] = file_sha256(native_header)
        report["native_tally_acceptance"] = {
            "comparison": "exact-zero-and-relative-nonzero",
            "relative_tolerance": NATIVE_RELATIVE_TOLERANCE,
            "diagnostics_emitted": not args.native_timing_clean,
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
        if native_batch is not None:
            if args.native_timing_clean:
                report["metrics"]["native_tally_validation"] = {
                    "comparison": "exact-zero-and-relative-nonzero",
                    "relative_tolerance": NATIVE_RELATIVE_TOLERANCE,
                    "passed": True,
                    "diagnostics_emitted": False,
                }
            else:
                report["metrics"][
                    "native_tally_diagnostics"
                ] = read_tally_diagnostics(
                    outdir / "stderr.log", native_batch["cell_count"] * 6
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
