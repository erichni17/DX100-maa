#!/usr/bin/env python3
"""Build, run, and fail-closed validate the ABI-v5 UMT64 poison-tail smoke."""

import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
GROUP_COUNTS = [1, 7, 8, 9, 31, 32, 33, 63, 64]


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


def compile_guest(source, binary, group_counts):
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
    if len(group_counts) == 1:
        command.append(f"-DONLY_GROUP_COUNT={group_counts[0]}")
    elif group_counts != GROUP_COUNTS:
        raise RuntimeError("only one cold case or the exact full matrix is valid")
    command.extend([str(source), "-o", str(binary)])
    subprocess.run(command, check=True)


def validate(stats, group_counts):
    groups = sum(group_counts)
    packet_multipliers = sum((group + 7) // 8 for group in group_counts)
    expected = {
        "descriptorDoorbells": len(group_counts),
        "descriptorRearms": len(group_counts) - 1,
        "descriptorFetches": 4 * len(group_counts),
        "descriptorResultWrites": 8 * groups,
        "descriptorUmtResultLineWrites": 8 * packet_multipliers,
        "descriptorCompletionWrites": len(group_counts),
        "descriptorErrors": 0,
        "descriptorUmtGroupsLoaded": groups,
        "descriptorUmtInputReads": 16 * groups,
        "descriptorUmtInputLineReads": 16 * packet_multipliers,
        "descriptorUmtFp64AddSubOperations": 8 * groups,
        "descriptorUmtFp64MultiplyOperations": 0,
        "descriptorUmtFp64DivideOperations": 8 * groups,
        "descriptorUmtBatches": len(group_counts),
        "descriptorUmtResultsComputed": 8 * groups,
        "activeContextHighWaterMark": max(group_counts),
        "lineWouldBlockCycles": 0,
    }
    failures = {
        name: {"expected": value, "observed": stats.get(name)}
        for name, value in expected.items()
        if stats.get(name) != value
    }
    if failures:
        raise RuntimeError(f"UMT64 poison-tail stat mismatch: {failures}")
    return expected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument("--source", required=True, type=pathlib.Path)
    parser.add_argument("--output-root", required=True, type=pathlib.Path)
    parser.add_argument("--expected-gem5-sha256", required=True)
    parser.add_argument("--expected-gem5-source-commit", required=True)
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
        raise RuntimeError("group counts are empty, duplicated, or outside the gate")

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
    if changed_paths != expected_harness_paths:
        raise RuntimeError(
            "harness branch changed production source or lacks exact tests: "
            f"{sorted(changed_paths)}"
        )
    actual_gem5_sha = sha256(args.gem5.resolve())
    if actual_gem5_sha != args.expected_gem5_sha256:
        raise RuntimeError("gem5 identity mismatch")

    args.output_root.mkdir(parents=False)
    binary = args.output_root / "umt64_poison_tail.elf"
    compile_guest(args.source.resolve(), binary, group_counts)
    metadata = {
        "schema": "lanl-maa-umt64-poison-tail-v1",
        "group_counts": group_counts,
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
    expected_stats = validate(read_stats(stats_path), group_counts)
    report = {
        **metadata,
        "status": "passed",
        "terminal": True,
        "returncode": completed.returncode,
        "command": command,
        "expected_stats": expected_stats,
        "stats_sha256": sha256(stats_path),
        "stdout_sha256": sha256(args.output_root / "stdout.log"),
        "stderr_sha256": sha256(args.output_root / "stderr.log"),
        "claim_boundary": (
            "Live ABI-v5 functional full/partial-tail evidence only; no "
            "physical-storage, timing, energy, or application-speedup claim."
        ),
    }
    report_path = args.output_root / "report.json"
    write_json(report_path, report)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
