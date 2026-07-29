#!/usr/bin/env python3
"""Run the live opcode-7 CPU smoke on any validated native SPARTA batch."""

import argparse
import importlib.util
import json
import pathlib
import shutil
import subprocess

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RUNNER = pathlib.Path(__file__).resolve()
BASE_RUNNER_PATH = HERE / "run_sparta_fused_cell_cpu_smoke.py"
BASE_SPEC = importlib.util.spec_from_file_location(
    "sparta_fused_fixed_smoke", BASE_RUNNER_PATH
)
BASE_RUNNER = importlib.util.module_from_spec(BASE_SPEC)
BASE_SPEC.loader.exec_module(BASE_RUNNER)


def expected_metrics(batch):
    validated = BASE_RUNNER.NATIVE_RUNNER.validate_batch(batch)
    expected_writes = 6 * sum(
        any(bits != "0000000000000000" for bits in cell)
        for cell in validated["expected"]
    )
    eligible = batch["eligible_particle_count"]
    return {
        "descriptorFetches": 4,
        "descriptorErrors": 1,
        "descriptorCompletionWrites": 1,
        "descriptorSpartaFusedCellsLoaded": 2 * batch["cell_count"],
        "descriptorSpartaFusedParticlesVisited": (
            2 * batch["native_particle_count"]
        ),
        "descriptorSpartaFusedEligibleParticles": 2 * eligible,
        "descriptorSpartaFusedFp64Multiplies": 14 * eligible,
        "descriptorSpartaFusedFp64Adds": 16 * eligible,
        "descriptorSpartaFusedWritesAcknowledged": expected_writes,
        "descriptorResultWrites": expected_writes,
        "descriptorSpartaFusedPairBankAccesses": (
            8 * eligible + expected_writes // 6
        ),
    }


def read_stats(path, batch):
    stats = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = BASE_RUNNER.STAT_PATTERN.match(line)
        if match:
            stats[match.group(1)] = int(match.group(2))

    required = expected_metrics(batch)
    for name, expected in required.items():
        if stats.get(name) != expected:
            raise ValueError(
                f"unexpected {name}: {stats.get(name)} != {expected}"
            )

    zero_reads = stats.get("descriptorSpartaFusedTallyZeroReads")
    tally_words = 6 * batch["cell_count"]
    if zero_reads is None or not tally_words <= zero_reads < 2 * tally_words:
        raise ValueError(
            "unexpected descriptorSpartaFusedTallyZeroReads: "
            f"{zero_reads} is outside [{tally_words}, {2 * tally_words})"
        )
    required["descriptorSpartaFusedTallyZeroReads"] = zero_reads

    high_water = stats.get("activeContextHighWaterMark")
    if high_water is None or not 1 <= high_water <= 8:
        raise ValueError(
            f"unexpected activeContextHighWaterMark: {high_water}"
        )
    required["activeContextHighWaterMark"] = high_water

    conflict_cycles = stats.get("descriptorSpartaFusedPairBankConflictCycles")
    if conflict_cycles is None or conflict_cycles <= 0:
        raise ValueError(
            "expected the shared summary-pair bank conflict path to be active"
        )
    required["descriptorSpartaFusedPairBankConflictCycles"] = conflict_cycles

    retry_names = (
        "portSendFailures",
        "portRetryNotifications",
        "retryPacketResubmissions",
        "retryPacketAcceptances",
    )
    retry_counts = [stats.get(name) for name in retry_names]
    if retry_counts[0] is None or retry_counts[0] <= 0:
        raise ValueError(
            "expected the coherent timing retry path to be active"
        )
    if len(set(retry_counts)) != 1:
        raise ValueError(f"unbalanced retry counters: {retry_counts}")
    required.update(zip(retry_names, retry_counts))
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
    header_text, expected_writes = BASE_RUNNER.build_header(batch)
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
        "schema": "lanl-maa-sparta-fused-cell-cpu-smoke-v2",
        "status": "running",
        "batch_path": str(batch_path),
        "batch_sha256": BASE_RUNNER.file_sha256(batch_path),
        "expected_writes": expected_writes,
        "gem5_sha256": BASE_RUNNER.file_sha256(gem5),
        "engine_sha256": BASE_RUNNER.file_sha256(BASE_RUNNER.ENGINE),
        "model_sha256": BASE_RUNNER.file_sha256(BASE_RUNNER.MODEL),
        "runner_sha256": BASE_RUNNER.file_sha256(RUNNER),
        "base_runner_sha256": BASE_RUNNER.file_sha256(BASE_RUNNER_PATH),
        "source_sha256": BASE_RUNNER.file_sha256(source),
        "config_sha256": BASE_RUNNER.file_sha256(config),
        "metadata_sha256": BASE_RUNNER.file_sha256(metadata),
        "header_sha256": BASE_RUNNER.file_sha256(header),
        "binary_sha256": BASE_RUNNER.file_sha256(binary),
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
        report["metrics"] = read_stats(m5out / "stats.txt", batch)
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
