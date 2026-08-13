#!/usr/bin/env python3
"""Build, run, and fail-closed validate the dual-ABI UMT poison-tail smoke."""

import argparse
import hashlib
import json
import pathlib
import re
import shutil
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
GROUP_COUNTS = [1, 7, 8, 9, 31, 32, 33, 63, 64]
D32_GROUP_COUNTS = [group for group in GROUP_COUNTS if group <= 32]
ISSUE2_D32_INPUT_LINE_READS = {
    1: 16,
    7: 16,
    8: 16,
    9: 32,
    31: 91,
    32: 88,
}


def sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, document):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def read_stats(path):
    stats = {}
    prefix = "system.lanl_maa."
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if line.startswith(prefix) and len(fields) >= 2:
            try:
                stats[fields[0][len(prefix) :]] = int(fields[1])
            except ValueError:
                pass
    return stats


def compile_guest(source, binary, group_counts, abi_version):
    compiler = shutil.which("cc")
    if not compiler:
        raise RuntimeError("UMT64 poison-tail smoke requires cc")
    command = [
        compiler,
        "-std=c11",
        "-O2",
        "-ffp-contract=off",
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
    ]
    command.append(f"-DUMT_ABI_VERSION={abi_version}")
    if len(group_counts) == 1:
        command.append(f"-DONLY_GROUP_COUNT={group_counts[0]}")
    elif group_counts != (
        D32_GROUP_COUNTS if abi_version == 4 else GROUP_COUNTS
    ):
        raise RuntimeError(
            "only one cold case or the exact ABI matrix is valid"
        )
    command.extend([str(source), "-o", str(binary)])
    subprocess.run(command, check=True)


def validate(stats, group_counts, abi_version):
    groups = sum(group_counts)
    packet_multipliers = sum((group + 7) // 8 for group in group_counts)
    input_line_reads = (
        sum(ISSUE2_D32_INPUT_LINE_READS[group] for group in group_counts)
        if abi_version == 4
        else 16 * packet_multipliers
    )
    expected = {
        "descriptorDoorbells": len(group_counts),
        "descriptorRearms": len(group_counts) - 1,
        "descriptorFetches": 4 * len(group_counts),
        "descriptorResultWrites": 8 * groups,
        "descriptorUmtResultLineWrites": 8 * packet_multipliers,
        "descriptorUmtD32Descriptors": (
            len(group_counts) if abi_version == 4 else 0
        ),
        "descriptorUmtD64Descriptors": (
            len(group_counts) if abi_version == 5 else 0
        ),
        "descriptorCompletionWrites": len(group_counts),
        "descriptorErrors": 0,
        "descriptorUmtGroupsLoaded": groups,
        "descriptorUmtInputReads": 16 * groups,
        "descriptorUmtStateInputWrites": 8 * groups,
        "descriptorUmtStateDenominatorsConsumed": 8 * groups,
        "descriptorUmtStateResultWrites": 8 * groups,
        "descriptorUmtStateResultReads": 8 * groups,
        "descriptorUmtStateCapacityErrors": 0,
        "descriptorUmtStateStoreHighWaterMark": max(group_counts),
        "descriptorUmtStateBankHighWaterMark": (max(group_counts) + 3) // 4,
        "descriptorUmtStateTokenHighWaterMark": min(32, max(group_counts)),
        "descriptorUmtStateAllocatedStoreBytes": 4608,
        "descriptorUmtStatePhysicalStoreBytes": 5120,
        "descriptorUmtStateResidualStoreBytes": 512,
        "descriptorUmtStateTokenLogicalBitsFloor": 15072,
        "descriptorUmtStateFunctionalControlLogicalBitsFloor": 657,
        "descriptorUmtStateBankSchedulerLogicalBitsFloor": 283,
        "descriptorUmtStateInstrumentationLogicalBitsFloor": 1106,
        "descriptorUmtStateAuxiliaryLogicalBitsFloor": 17118,
        "descriptorUmtStatePhysicalStorePlusLogicalAuxiliaryBitsFloor": 58078,
        "descriptorUmtInputLineReads": input_line_reads,
        "descriptorUmtFp64AddSubOperations": 8 * groups,
        "descriptorUmtFp64MultiplyOperations": 0,
        "descriptorUmtFp64DivideOperations": 8 * groups,
        "descriptorUmtBatches": len(group_counts),
        "descriptorUmtResultsComputed": 8 * groups,
        "activeContextHighWaterMark": max(group_counts),
        "operationTableHighWaterMark": max(group_counts),
        "lineWouldBlockCycles": 0,
    }
    failures = {
        name: {"expected": value, "observed": stats.get(name)}
        for name, value in expected.items()
        if stats.get(name) != value
    }
    if failures:
        raise RuntimeError(f"UMT64 poison-tail stat mismatch: {failures}")
    line_high_water = stats.get("lineTableHighWaterMark")
    if line_high_water is None or not 0 < line_high_water <= 32:
        raise RuntimeError(
            "UMT64 poison-tail line-table high-water is absent or outside "
            f"the 32-entry capacity: {line_high_water}"
        )
    token_high_water = stats["descriptorUmtStateTokenHighWaterMark"]
    bounded = {
        "lineTableHighWaterMark": line_high_water,
        "descriptorUmtStateTokenHighWaterMark": token_high_water,
    }
    for name in ("controlReadRequests", "controlStatusReads"):
        value = stats.get(name)
        if value is None or value <= 0:
            raise RuntimeError(
                f"UMT64 poison-tail control-read counter is absent or zero: "
                f"{name}={value}"
            )
        bounded[name] = value
    for name in ("controlOpcodeReads", "controlErrorReads"):
        value = stats.get(name)
        if value is None or value < 0:
            raise RuntimeError(
                "UMT64 poison-tail optional control-read counter is absent "
                f"or negative: {name}={value}"
            )
        bounded[name] = value
    if bounded["controlReadRequests"] < bounded["controlStatusReads"]:
        raise RuntimeError(
            "UMT64 poison-tail status reads exceed total control reads: "
            f"{bounded}"
        )
    batch_cycles = stats.get("descriptorUmtBatchCycles")
    if batch_cycles is None or batch_cycles <= len(group_counts):
        raise RuntimeError(
            "UMT measured pipeline cycles are absent or still a per-batch "
            f"placeholder: {batch_cycles}"
        )
    bounded["descriptorUmtBatchCycles"] = batch_cycles
    input_hold_cycles = stats.get("descriptorUmtInputLineWaiterHoldLineCycles")
    if abi_version == 4:
        if input_hold_cycles != 0:
            raise RuntimeError(
                "D32 unexpectedly used complete-line hold: "
                f"{input_hold_cycles}"
            )
    elif len(group_counts) > 1 and (
        input_hold_cycles is None or input_hold_cycles <= 0
    ):
        raise RuntimeError("D64 full matrix did not exercise line holding")
    bounded["descriptorUmtInputLineWaiterHoldLineCycles"] = input_hold_cycles
    if len(group_counts) > 1:
        for name in (
            "descriptorUmtStateTokenBackpressureEvents",
            "descriptorUmtStateFpIssueStallCycles",
            "descriptorUmtStateInputBankWaitCycles",
            "descriptorUmtStatePipelineResultBankStallCycles",
            "descriptorUmtStateResultDrainBankWaitCycles",
        ):
            value = stats.get(name)
            if value is None or value <= 0:
                raise RuntimeError(
                    "UMT full matrix did not exercise required pressure: "
                    f"{name}={value}"
                )
            bounded[name] = value
    return expected, bounded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument("--source", required=True, type=pathlib.Path)
    parser.add_argument("--output-root", required=True, type=pathlib.Path)
    parser.add_argument("--expected-gem5-sha256", required=True)
    parser.add_argument("--expected-gem5-source-commit", required=True)
    parser.add_argument("--abi-version", type=int, choices=(4, 5), default=5)
    parser.add_argument(
        "--group-counts",
        default=",".join(map(str, GROUP_COUNTS)),
        help="exact full matrix or one cold diagnostic group count",
    )
    args = parser.parse_args()

    try:
        group_counts = [int(value) for value in args.group_counts.split(",")]
    except ValueError as error:
        raise RuntimeError("invalid group-count list") from error
    if (
        not group_counts
        or len(set(group_counts)) != len(group_counts)
        or not set(group_counts).issubset(GROUP_COUNTS)
    ):
        raise RuntimeError(
            "group counts are empty, duplicated, or outside the gate"
        )
    if args.abi_version == 4 and max(group_counts) > 32:
        raise RuntimeError("ABI v4 poison-tail cases may not exceed 32 groups")

    if args.output_root.exists():
        raise RuntimeError(f"refusing to overwrite {args.output_root}")
    actual_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    status = subprocess.check_output(
        ["git", "status", "--short"], cwd=ROOT, text=True
    ).strip()
    if status:
        raise RuntimeError("poison-tail harness worktree is not clean")
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_gem5_source_commit):
        raise RuntimeError("expected gem5 source commit is not a full SHA-1")
    ancestry = subprocess.run(
        [
            "git",
            "merge-base",
            "--is-ancestor",
            args.expected_gem5_source_commit,
            "HEAD",
        ],
        cwd=ROOT,
        check=False,
    )
    if ancestry.returncode != 0:
        raise RuntimeError("gem5 source commit is not a harness ancestor")
    changed_paths = set(
        subprocess.check_output(
            [
                "git",
                "diff",
                "--name-only",
                f"{args.expected_gem5_source_commit}..HEAD",
            ],
            cwd=ROOT,
            text=True,
        ).splitlines()
    )
    expected_harness_paths = {
        "benchmarks/LANL/umt_ordered_wave_poison_tail_smoke.c",
        "tests/lanl_maa/run_umt_ordered_wave_poison_tail_smoke.py",
        "tests/lanl_maa/umt_ordered_wave_poison_tail_smoke.py",
    }
    if not changed_paths.issubset(expected_harness_paths):
        raise RuntimeError(
            "harness commits after gem5 changed production source: "
            f"{sorted(changed_paths)}"
        )
    actual_gem5_sha = sha256(args.gem5.resolve())
    if actual_gem5_sha != args.expected_gem5_sha256:
        raise RuntimeError("gem5 identity mismatch")

    args.output_root.mkdir(parents=False)
    binary = args.output_root / "umt64_poison_tail.elf"
    compile_guest(
        args.source.resolve(), binary, group_counts, args.abi_version
    )
    metadata = {
        "schema": "lanl-maa-umt64-poison-tail-v1",
        "group_counts": group_counts,
        "abi_version": args.abi_version,
        "sum_groups": sum(group_counts),
        "inactive_record_bits": "0x7ff0000000000001",
        "inactive_result_bits": "0xdeadbeefcafef00d",
        "simulator_commit": args.expected_gem5_source_commit,
        "harness_commit": actual_commit,
        "gem5_sha256": actual_gem5_sha,
        "config_sha256": sha256(args.config.resolve()),
        "source_sha256": sha256(args.source.resolve()),
        "guest_sha256": sha256(binary),
    }
    metadata_path = args.output_root / "metadata.json"
    write_json(metadata_path, metadata)
    outdir = args.output_root / "m5out"
    command = [
        str(args.gem5.resolve()),
        f"--outdir={outdir}",
        str(args.config.resolve()),
        f"--binary={binary}",
        f"--metadata={metadata_path}",
    ]
    completed = subprocess.run(command, text=True, capture_output=True)
    (args.output_root / "stdout.log").write_text(
        completed.stdout, encoding="utf-8"
    )
    (args.output_root / "stderr.log").write_text(
        completed.stderr, encoding="utf-8"
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"gem5 failed with {completed.returncode}; see {args.output_root}"
        )
    if "LANLMAA_UMT64_POISON_TAIL_TERMINAL code=0" not in completed.stdout:
        raise RuntimeError("gem5 output lacks the exact poison-tail terminal")
    stats_path = outdir / "stats.txt"
    expected_stats, bounded_stats = validate(
        read_stats(stats_path), group_counts, args.abi_version
    )
    report = {
        **metadata,
        "status": "passed",
        "terminal": True,
        "returncode": completed.returncode,
        "command": command,
        "expected_stats": expected_stats,
        "bounded_stats": bounded_stats,
        "stats_sha256": sha256(stats_path),
        "stdout_sha256": sha256(args.output_root / "stdout.log"),
        "stderr_sha256": sha256(args.output_root / "stderr.log"),
        "claim_boundary": (
            "Live dual-ABI functional, bounded-store, token, and stall-counter "
            "evidence; no application-speedup, energy, RTL, or physical-design "
            "claim."
        ),
    }
    report_path = args.output_root / "report.json"
    write_json(report_path, report)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
