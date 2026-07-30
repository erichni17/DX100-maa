#!/usr/bin/env python3
"""Run and audit one native SPARTA opcode-7 process submission."""

import argparse
import hashlib
import json
import pathlib
import re
import shutil
import subprocess

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RUNNER = pathlib.Path(__file__).resolve()
ENGINE = ROOT / "src/mem/LANLMAA/lanl_maa.cc"
STAT_PATTERN = re.compile(r"^system\.lanl_maa\.([A-Za-z0-9_]+)\s+(\d+)")
FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{16}$")


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_submission(document, expected_timestep=1):
    required = {
        "schema": "sparta-lanl-maa-submission-v1",
        "timestep": expected_timestep,
        "rank": 0,
        "cell_count": 27,
        "particle_count": 64,
        "species_count": 1,
        "exact_words_checked": 162,
        "exact_words_matching": 162,
        "exact_match": True,
    }
    for name, expected in required.items():
        if document.get(name) != expected:
            raise ValueError(
                f"unexpected submission {name}: "
                f"{document.get(name)} != {expected}"
            )
    writes = document.get("expected_writes")
    if (
        not isinstance(writes, int)
        or writes < 144
        or writes > 162
        or writes % 6
    ):
        raise ValueError(f"unexpected fused write count: {writes}")
    if document.get("completion_writes") != writes:
        raise ValueError("completion does not acknowledge every fused write")
    scalar = document.get("scalar_fingerprint")
    accelerator = document.get("accelerator_fingerprint")
    if not isinstance(scalar, str) or not FINGERPRINT_PATTERN.fullmatch(
        scalar
    ):
        raise ValueError(f"invalid scalar fingerprint: {scalar}")
    if scalar != accelerator:
        raise ValueError(
            f"fingerprint mismatch: scalar={scalar} accelerator={accelerator}"
        )
    return writes, scalar


def read_stats(
    path, expected_writes, model_payload_overlay_ports=False
):
    stats = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = STAT_PATTERN.match(line)
        if match:
            stats[match.group(1)] = int(match.group(2))
    required = {
        "descriptorFetches": 2,
        "descriptorErrors": 0,
        "descriptorCompletionWrites": 1,
        "descriptorSpartaFusedCellsLoaded": 27,
        "descriptorSpartaFusedParticlesVisited": 64,
        "descriptorSpartaFusedEligibleParticles": 64,
        "descriptorSpartaFusedFp64Multiplies": 448,
        "descriptorSpartaFusedFp64Adds": 512,
        "descriptorSpartaFusedTallyZeroReads": 162,
        "descriptorSpartaFusedWritesAcknowledged": expected_writes,
        "descriptorResultWrites": expected_writes,
        "activeContextHighWaterMark": 8,
    }
    for name, expected in required.items():
        if stats.get(name) != expected:
            raise ValueError(
                f"unexpected {name}: {stats.get(name)} != {expected}"
            )
    if model_payload_overlay_ports:
        logical_items = stats.get("logicalItems")
        if logical_items is None or logical_items <= 0:
            raise ValueError("payload-overlay run admitted no logical items")
        for name in (
            "payloadOverlayCompletionWrites",
            "payloadOverlayRetirementReads",
        ):
            if stats.get(name) != logical_items:
                raise ValueError(
                    f"unexpected {name}: {stats.get(name)} "
                    f"!= {logical_items}"
                )
        for name in (
            "logicalItems",
            "payloadOverlayCompletionWrites",
            "payloadOverlayRetirementReads",
            "payloadOverlayCompletionBankConflictCycles",
            "payloadOverlayCompletionReadConflictCycles",
            "payloadOverlayCompletionWouldBlockCycles",
            "payloadOverlayCompletionQueueHighWaterMark",
        ):
            required[name] = stats.get(name)
    retry_names = (
        "portSendFailures",
        "portRetryNotifications",
        "retryPacketResubmissions",
        "retryPacketAcceptances",
    )
    retries = [stats.get(name) for name in retry_names]
    if any(value is None for value in retries) or len(set(retries)) != 1:
        raise ValueError(f"unbalanced retry counters: {retries}")
    required.update(zip(retry_names, retries))
    return {name: stats[name] for name in required}


def git_text(root, *arguments):
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument("--binary", required=True, type=pathlib.Path)
    parser.add_argument("--input", required=True, type=pathlib.Path)
    parser.add_argument("--dependency", action="append", type=pathlib.Path)
    parser.add_argument("--metadata", required=True, type=pathlib.Path)
    parser.add_argument("--sparta-root", required=True, type=pathlib.Path)
    parser.add_argument("--sparta-commit", required=True)
    parser.add_argument("--outdir", required=True, type=pathlib.Path)
    parser.add_argument("--submission-timestep", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--model-payload-overlay-ports", action="store_true")
    arguments = parser.parse_args()

    if arguments.submission_timestep < 0:
        raise ValueError("submission timestep must be nonnegative")

    outdir = arguments.outdir.resolve()
    if outdir.exists():
        raise RuntimeError(f"refusing to reuse evidence directory: {outdir}")
    outdir.mkdir(parents=True)
    gem5 = arguments.gem5.resolve(strict=True)
    config = arguments.config.resolve(strict=True)
    source_binary = arguments.binary.resolve(strict=True)
    input_path = arguments.input.resolve(strict=True)
    metadata = arguments.metadata.resolve(strict=True)
    sparta_root = arguments.sparta_root.resolve(strict=True)
    dependencies = [
        path.resolve(strict=True) for path in (arguments.dependency or [])
    ]

    observed_commit = git_text(sparta_root, "rev-parse", "HEAD")
    if observed_commit != arguments.sparta_commit:
        raise ValueError(
            f"SPARTA commit mismatch: {observed_commit} "
            f"!= {arguments.sparta_commit}"
        )
    if git_text(sparta_root, "status", "--short"):
        raise ValueError("SPARTA worktree must be clean before evidence")

    binary = outdir / "spa_serial"
    shutil.copy2(source_binary, binary)
    submission_path = outdir / "sparta_submission.json"
    m5out = outdir / "m5out"
    command = [
        str(gem5),
        f"--outdir={m5out}",
        str(config),
        f"--binary={binary}",
        f"--input={input_path.name}",
        f"--cwd={input_path.parent}",
        f"--metadata={metadata}",
        f"--submission-report={submission_path}",
        f"--submission-timestep={arguments.submission_timestep}",
    ]
    if arguments.model_payload_overlay_ports:
        command.append("--model-payload-overlay-ports")
    report = {
        "schema": "lanl-maa-sparta-native-process-smoke-v1",
        "status": "running",
        "sparta_commit": observed_commit,
        "sparta_worktree_clean": True,
        "sparta_binary_sha256": file_sha256(binary),
        "gem5_sha256": file_sha256(gem5),
        "engine_sha256": file_sha256(ENGINE),
        "runner_sha256": file_sha256(RUNNER),
        "config_sha256": file_sha256(config),
        "input_path": str(input_path),
        "input_sha256": file_sha256(input_path),
        "dependencies": [
            {"path": str(path), "sha256": file_sha256(path)}
            for path in dependencies
        ],
        "metadata_sha256": file_sha256(metadata),
        "requested_submission_timestep": arguments.submission_timestep,
        "model_payload_overlay_ports": (
            arguments.model_payload_overlay_ports
        ),
        "command": command,
        "claim_boundary": (
            "One real SPARTA process copies bounded native CPU records into "
            "a coherent staging arena, submits opcode 7, and compares every "
            "returned tally word to its scalar result. This is not zero-copy "
            "integration, application performance, speedup, or RTL evidence."
        ),
    }
    report_path = outdir / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    try:
        with (outdir / "stdout.log").open("w", encoding="utf-8") as stdout:
            with (outdir / "stderr.log").open("w", encoding="utf-8") as stderr:
                result = subprocess.run(
                    command,
                    check=False,
                    timeout=arguments.timeout_seconds,
                    stdout=stdout,
                    stderr=stderr,
                )
        report["driver_return_code"] = result.returncode
        if result.returncode != 0:
            raise RuntimeError(
                f"native SPARTA gem5 process returned {result.returncode}"
            )
        stdout_text = (outdir / "stdout.log").read_text(encoding="utf-8")
        stderr_text = (outdir / "stderr.log").read_text(encoding="utf-8")
        if "LANLMAA_SIM_TERMINAL code=0 " not in stdout_text:
            raise ValueError("missing successful gem5 terminal marker")
        if "panic:" in stderr_text or "fatal:" in stderr_text:
            raise ValueError("gem5 stderr contains a panic or fatal marker")
        submission = json.loads(submission_path.read_text(encoding="utf-8"))
        expected_writes, fingerprint = validate_submission(
            submission, arguments.submission_timestep
        )
        marker = (
            "LANL_MAA_NATIVE_SUBMISSION "
            f"timestep={arguments.submission_timestep} "
            "cells=27 particles=64 "
            f"writes={expected_writes} fingerprint={fingerprint} exact=1"
        )
        if marker not in stdout_text:
            raise ValueError("missing exact native SPARTA submission marker")
        report["submission_sha256"] = file_sha256(submission_path)
        report["submission"] = submission
        report["metrics"] = read_stats(
            m5out / "stats.txt",
            expected_writes,
            arguments.model_payload_overlay_ports,
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
