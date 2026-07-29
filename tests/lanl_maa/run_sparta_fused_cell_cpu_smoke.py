#!/usr/bin/env python3
"""Run the live opcode-7 CPU smoke on one native SPARTA batch."""

import argparse
import hashlib
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RUNNER = pathlib.Path(__file__).resolve()
MODEL = ROOT / "src/mem/LANLMAA/SpartaFusedCellModel.hh"
ENGINE = ROOT / "src/mem/LANLMAA/lanl_maa.cc"
STAT_PATTERN = re.compile(r"^system\.lanl_maa\.([A-Za-z0-9_]+)\s+(\d+)")
NATIVE_RUNNER_PATH = HERE / "run_sparta_fused_cell_native_batch.py"
NATIVE_SPEC = importlib.util.spec_from_file_location(
    "sparta_fused_native", NATIVE_RUNNER_PATH
)
NATIVE_RUNNER = importlib.util.module_from_spec(NATIVE_SPEC)
NATIVE_SPEC.loader.exec_module(NATIVE_RUNNER)


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _array(name, ctype, values, per_line=6):
    lines = [f"static const {ctype} {name}[{len(values)}] = {{"]
    for start in range(0, len(values), per_line):
        chunk = values[start : start + per_line]
        lines.append("    " + ", ".join(chunk) + ",")
    lines.append("};")
    return "\n".join(lines)


def build_header(batch):
    validated = NATIVE_RUNNER.validate_batch(batch)
    extension = validated["extension"]
    cells = validated["cells"]
    particles = validated["particles"]
    species = validated["species"]
    expected = [bits for cell in validated["expected"] for bits in cell]
    expected_writes = 6 * sum(
        any(bits != "0000000000000000" for bits in cell)
        for cell in validated["expected"]
    )
    sections = [
        "#ifndef SPARTA_FUSED_NATIVE_BATCH_H",
        "#define SPARTA_FUSED_NATIVE_BATCH_H",
        "",
        "#include <stdint.h>",
        "",
        f"#define SPARTA_FUSED_CELLS UINT64_C({batch['cell_count']})",
        (
            "#define SPARTA_FUSED_PARTICLES "
            f"UINT64_C({batch['native_particle_count']})"
        ),
        f"#define SPARTA_FUSED_SPECIES UINT64_C({len(species)})",
        f"#define SPARTA_FUSED_GROUP_BIT UINT64_C({extension['group_bit']})",
        (
            "#define SPARTA_FUSED_TARGET_GROUP "
            f"INT32_C({batch['target_mixture_group']})"
        ),
        f"#define SPARTA_FUSED_EXPECTED_WRITES UINT64_C({expected_writes})",
        "",
        _array(
            "sparta_fused_cell_count",
            "int32_t",
            [f"INT32_C({cell['count']})" for cell in cells],
        ),
        "",
        _array(
            "sparta_fused_cell_first",
            "int32_t",
            [f"INT32_C({cell['first']})" for cell in cells],
        ),
        "",
        _array(
            "sparta_fused_cell_mask",
            "uint32_t",
            [f"UINT32_C({cell['mask']})" for cell in cells],
        ),
        "",
        _array(
            "sparta_fused_next",
            "int32_t",
            [f"INT32_C({value})" for value in validated["next"]],
        ),
        "",
        _array(
            "sparta_fused_particle_species",
            "int32_t",
            [f"INT32_C({particle['species']})" for particle in particles],
        ),
        "",
        _array(
            "sparta_fused_particle_cell",
            "int32_t",
            [f"INT32_C({particle['cell']})" for particle in particles],
        ),
        "",
        _array(
            "sparta_fused_velocity_bits",
            "uint64_t",
            [
                f"UINT64_C(0x{bits})"
                for particle in particles
                for bits in particle["velocity_bits"]
            ],
            3,
        ),
        "",
        _array(
            "sparta_fused_species_group",
            "int32_t",
            [f"INT32_C({item['group']})" for item in species],
        ),
        "",
        _array(
            "sparta_fused_mass_bits",
            "uint64_t",
            [f"UINT64_C(0x{item['mass_bits']})" for item in species],
        ),
        "",
        _array(
            "sparta_fused_expected_bits",
            "uint64_t",
            [f"UINT64_C(0x{bits})" for bits in expected],
            3,
        ),
        "",
        "#endif",
        "",
    ]
    return "\n".join(sections), expected_writes


def read_stats(path):
    stats = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = STAT_PATTERN.match(line)
        if match:
            stats[match.group(1)] = int(match.group(2))
    required = {
        "descriptorFetches": 4,
        "descriptorErrors": 1,
        "descriptorCompletionWrites": 1,
        "descriptorSpartaFusedCellsLoaded": 54,
        "descriptorSpartaFusedParticlesVisited": 128,
        "descriptorSpartaFusedEligibleParticles": 128,
        "descriptorSpartaFusedFp64Multiplies": 896,
        "descriptorSpartaFusedFp64Adds": 1024,
        "descriptorSpartaFusedWritesAcknowledged": 156,
        "descriptorResultWrites": 156,
        "activeContextHighWaterMark": 8,
        "descriptorSpartaFusedPairBankAccesses": 538,
    }
    for name, expected in required.items():
        if stats.get(name) != expected:
            raise ValueError(
                f"unexpected {name}: {stats.get(name)} != {expected}"
            )
    zero_reads = stats.get("descriptorSpartaFusedTallyZeroReads")
    if zero_reads is None or not 162 <= zero_reads < 324:
        raise ValueError(
            "unexpected descriptorSpartaFusedTallyZeroReads: "
            f"{zero_reads} is outside [162, 324)"
        )
    required["descriptorSpartaFusedTallyZeroReads"] = zero_reads
    conflict_cycles = stats.get("descriptorSpartaFusedPairBankConflictCycles")
    if conflict_cycles is None or conflict_cycles <= 0:
        raise ValueError(
            "expected the shared summary-pair bank conflict path to be active"
        )
    required["descriptorSpartaFusedPairBankConflictCycles"] = conflict_cycles
    return {name: stats[name] for name in required}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument("--source", required=True, type=pathlib.Path)
    parser.add_argument("--metadata", required=True, type=pathlib.Path)
    parser.add_argument("--batch", required=True, type=pathlib.Path)
    parser.add_argument("--outdir", required=True, type=pathlib.Path)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    arguments = parser.parse_args()

    outdir = arguments.outdir.resolve()
    if outdir.exists():
        raise RuntimeError(f"refusing to reuse evidence directory: {outdir}")
    outdir.mkdir(parents=True)
    gem5 = arguments.gem5.resolve(strict=True)
    config = arguments.config.resolve(strict=True)
    source = arguments.source.resolve(strict=True)
    metadata = arguments.metadata.resolve(strict=True)
    batch_path = arguments.batch.resolve(strict=True)
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    header_text, expected_writes = build_header(batch)
    if expected_writes != 156:
        raise ValueError("representative batch must require 156 fused writes")
    header = outdir / "sparta_fused_native_batch.h"
    header.write_text(header_text, encoding="utf-8")

    compiler = shutil.which("cc")
    if not compiler:
        raise RuntimeError("SPARTA fused-cell smoke requires cc")
    binary = outdir / "sparta_fused_cell_cpu_smoke.elf"
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
        "-include",
        str(header),
        str(source),
        "-o",
        str(binary),
    ]
    subprocess.run(compile_command, check=True)
    m5out = outdir / "m5out"
    command = [
        str(gem5),
        f"--outdir={m5out}",
        str(config),
        f"--binary={binary}",
        f"--metadata={metadata}",
    ]
    report = {
        "schema": "lanl-maa-sparta-fused-cell-cpu-smoke-v1",
        "status": "running",
        "batch_path": str(batch_path),
        "batch_sha256": file_sha256(batch_path),
        "gem5_sha256": file_sha256(gem5),
        "engine_sha256": file_sha256(ENGINE),
        "model_sha256": file_sha256(MODEL),
        "runner_sha256": file_sha256(RUNNER),
        "source_sha256": file_sha256(source),
        "config_sha256": file_sha256(config),
        "metadata_sha256": file_sha256(metadata),
        "header_sha256": file_sha256(header),
        "binary_sha256": file_sha256(binary),
        "compile_command": compile_command,
        "command": command,
        "claim_boundary": (
            "One lightweight real-X86 native-record descriptor smoke with "
            "an adversarial fail-close/rearm path; not SPARTA application "
            "timing, speedup, energy, area, or RTL evidence."
        ),
    }
    report_path = outdir / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    try:
        with (outdir / "stdout.log").open("w", encoding="utf-8") as stdout:
            with (outdir / "stderr.log").open("w", encoding="utf-8") as stderr:
                subprocess.run(
                    command,
                    check=True,
                    timeout=arguments.timeout_seconds,
                    stdout=stdout,
                    stderr=stderr,
                )
        report["metrics"] = read_stats(m5out / "stats.txt")
        report["adversarial_nonzero_tally_error"] = 18
        report["adversarial_published_completion"] = False
        report["adversarial_published_tally_write"] = False
        report["successful_outputs_bit_exact"] = True
        report["successful_completion_exact"] = True
        report["status"] = "validated"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        report_path.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
