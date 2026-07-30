#!/usr/bin/env python3
"""Run and audit one native Branson opcode-5 tally replacement."""

import argparse
import hashlib
import json
import math
import pathlib
import re
import shutil
import subprocess

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RUNNER = pathlib.Path(__file__).resolve()
ENGINE = ROOT / "src/mem/LANLMAA/lanl_maa.cc"
STAT_PATTERN = re.compile(r"^system\.lanl_maa\.([A-Za-z0-9_]+)\s+(\d+)")
FLOAT_PATTERN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_text(root, *arguments):
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def dynamic_dependencies(binary):
    readelf = shutil.which("readelf")
    if readelf is None:
        raise RuntimeError("readelf is required to bind Branson dependencies")
    output = subprocess.run(
        [readelf, "-d", str(binary)],
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    dependencies = re.findall(r"Shared library: \[([^]]+)\]", output)
    if any(name.startswith("libmpi") for name in dependencies):
        raise ValueError("native process binary still links an MPI runtime")
    return dependencies


def read_scalar(lines, name):
    prefix = name + " "
    for line in lines:
        if line.startswith(prefix):
            return int(float(line.split()[1]))
    return None


def validate_submission(document, metadata):
    required = {
        "schema": "branson-lanl-maa-submission-v1",
        "roots": metadata["roots"],
        "events": metadata["events"],
        "cells": metadata["cells"],
        "descriptor_batches": metadata["descriptor_batches"],
        "maximum_events_per_root": metadata["maximum_events_per_root"],
        "tolerance": 1.0e-12,
        "tolerance_match": True,
        "single_rank_mpi_shim": True,
        "scalar_tally_updates_replaced": True,
    }
    for name, expected in required.items():
        if document.get(name) != expected:
            raise ValueError(
                f"unexpected submission {name}: "
                f"{document.get(name)} != {expected}"
            )
    for name in ("reference_fingerprint", "accelerator_fingerprint"):
        value = document.get(name)
        if not isinstance(value, str) or not re.fullmatch(
            r"[0-9a-f]{16}", value
        ):
            raise ValueError(f"invalid {name}: {value}")
    for name in ("exact_absorbed_cells", "exact_track_cells"):
        value = document.get(name)
        if not isinstance(value, int) or not 0 <= value <= metadata["cells"]:
            raise ValueError(f"invalid {name}: {value}")
    for name in ("maximum_absorbed_difference", "maximum_track_difference"):
        value = document.get(name)
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"invalid {name}: {value}")


def read_stats(path, metadata, model_payload_overlay_ports=False):
    lines = path.read_text(encoding="utf-8").splitlines()
    stats = {}
    for line in lines:
        match = STAT_PATTERN.match(line)
        if match:
            stats[match.group(1)] = int(match.group(2))
    events = metadata["events"]
    updates = metadata["logical_fp64_updates"]
    batches = metadata["descriptor_batches"]
    expected = {
        "descriptorDoorbells": batches,
        "descriptorBusyRejections": 0,
        "descriptorRearms": batches - 1,
        "descriptorFetches": batches,
        "descriptorCompletionWrites": batches,
        "descriptorErrors": 0,
        "descriptorBransonRootsLoaded": metadata["roots"],
        "descriptorBransonEventsValidated": events,
        "descriptorBransonEventsReplayed": events,
        "descriptorBransonUpdatesAcknowledged": updates,
        "descriptorBransonEventComputesQueued": updates,
        "descriptorBransonEventComputesIssued": updates,
        "descriptorBransonEventComputesCompleted": updates,
        "descriptorBransonEventComputesCancelled": 0,
        "descriptorBransonEventComputesCancelledInFlight": 0,
        "continuationSteps": updates,
        "continuationExhaustions": 0,
        "activeContextHighWaterMark": 16,
    }
    for name, value in expected.items():
        if stats.get(name) != value:
            raise ValueError(
                f"unexpected {name}: {stats.get(name)} != {value}"
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
    line_records = stats.get("physicalLineReads", 0) + stats.get(
        "lineMergeHits", 0
    )
    if line_records != updates:
        raise ValueError("event-record read accounting did not close")
    combined_updates = stats.get("updateCombinerHits", 0) + stats.get(
        "updateDrains", 0
    )
    if combined_updates != updates:
        raise ValueError("update-combiner accounting did not close")
    physical = [
        stats.get("updateDrains"),
        stats.get("physicalAtomicUpdates"),
        stats.get("atomicAcknowledgements"),
    ]
    if None in physical or len(set(physical)) != 1:
        raise ValueError(
            f"physical update accounting did not close: {physical}"
        )
    retries = [
        stats.get(name)
        for name in (
            "portSendFailures",
            "portRetryNotifications",
            "retryPacketResubmissions",
            "retryPacketAcceptances",
        )
    ]
    if None in retries or len(set(retries)) != 1:
        raise ValueError(f"retry accounting did not close: {retries}")
    committed = read_scalar(lines, "system.cpu.commitStats0.numInsts")
    ticks = read_scalar(lines, "simTicks")
    if committed is None or committed <= 0 or ticks is None or ticks <= 0:
        raise ValueError(
            "nonpositive CPU execution evidence: "
            f"insts={committed} ticks={ticks}"
        )
    return {
        **expected,
        "physicalAtomicUpdates": physical[0],
        "portRetries": retries[0],
        "cpuCommittedInstructions": committed,
        "simTicks": ticks,
        "logicalItems": stats.get("logicalItems"),
        "payloadOverlayCompletionWrites": stats.get(
            "payloadOverlayCompletionWrites"
        ),
        "payloadOverlayRetirementReads": stats.get(
            "payloadOverlayRetirementReads"
        ),
        "payloadOverlayCompletionBankConflictCycles": stats.get(
            "payloadOverlayCompletionBankConflictCycles"
        ),
        "payloadOverlayCompletionReadConflictCycles": stats.get(
            "payloadOverlayCompletionReadConflictCycles"
        ),
        "payloadOverlayCompletionWouldBlockCycles": stats.get(
            "payloadOverlayCompletionWouldBlockCycles"
        ),
        "payloadOverlayCompletionQueueHighWaterMark": stats.get(
            "payloadOverlayCompletionQueueHighWaterMark"
        ),
    }


def validate_application_output(text):
    if "Total Photons transported: 12000" not in text:
        raise ValueError("native Branson photon total is absent")
    residuals = []
    for name in ("Radiation conservation", "Material conservation"):
        matches = re.findall(
            rf"^{name}: ({FLOAT_PATTERN})$", text, re.MULTILINE
        )
        if len(matches) != 1:
            raise ValueError(
                f"expected one {name} record, observed {len(matches)}"
            )
        residuals.append(float(matches[0]))
    if not all(
        math.isfinite(value) and abs(value) <= 1.0e-8 for value in residuals
    ):
        raise ValueError(f"Branson conservation failed: {residuals}")
    return residuals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--binary", required=True, type=pathlib.Path)
    parser.add_argument("--input", required=True, type=pathlib.Path)
    parser.add_argument("--branson-root", required=True, type=pathlib.Path)
    parser.add_argument("--branson-commit", required=True)
    parser.add_argument("--dependency", action="append", type=pathlib.Path)
    parser.add_argument("--outdir", required=True, type=pathlib.Path)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--model-payload-overlay-ports", action="store_true")
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=HERE / "branson_native_process_cpu_smoke.py",
    )
    parser.add_argument(
        "--metadata",
        type=pathlib.Path,
        default=HERE / "branson_native_process_cpu_smoke.json",
    )
    arguments = parser.parse_args()

    outdir = arguments.outdir.resolve()
    if outdir.exists():
        raise RuntimeError(f"refusing to reuse evidence directory: {outdir}")
    gem5 = arguments.gem5.resolve(strict=True)
    config = arguments.config.resolve(strict=True)
    source_binary = arguments.binary.resolve(strict=True)
    source_input = arguments.input.resolve(strict=True)
    branson_root = arguments.branson_root.resolve(strict=True)
    metadata_path = arguments.metadata.resolve(strict=True)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    dependencies = [
        path.resolve(strict=True) for path in (arguments.dependency or [])
    ]

    observed_commit = git_text(branson_root, "rev-parse", "HEAD")
    if observed_commit != arguments.branson_commit:
        raise ValueError(
            "Branson commit mismatch: "
            f"{observed_commit} != {arguments.branson_commit}"
        )
    tracked_status = git_text(
        branson_root, "status", "--short", "--untracked-files=no"
    )
    if tracked_status:
        raise ValueError(
            "Branson tracked worktree must be clean before evidence"
        )
    simulator_commit = git_text(ROOT, "rev-parse", "HEAD")
    if git_text(ROOT, "status", "--short", "--untracked-files=no"):
        raise ValueError(
            "simulator tracked worktree must be clean before evidence"
        )

    outdir.mkdir(parents=True)
    binary = outdir / "BRANSON"
    input_path = outdir / source_input.name
    shutil.copy2(source_binary, binary)
    shutil.copy2(source_input, input_path)
    needed_libraries = dynamic_dependencies(binary)
    submission_path = outdir / "branson_submission.json"
    m5out = outdir / "m5out"
    command = [
        str(gem5),
        f"--outdir={m5out}",
        str(config),
        f"--binary={binary}",
        f"--input={input_path.name}",
        f"--cwd={outdir}",
        f"--metadata={metadata_path}",
        f"--submission-report={submission_path}",
    ]
    if arguments.model_payload_overlay_ports:
        command.append("--model-payload-overlay-ports")
    report = {
        "schema": "lanl-maa-branson-native-process-smoke-v1",
        "status": "running",
        "branson_commit": observed_commit,
        "branson_tracked_worktree_clean": True,
        "simulator_commit": simulator_commit,
        "simulator_tracked_worktree_clean": True,
        "model_payload_overlay_ports": (
            arguments.model_payload_overlay_ports
        ),
        "branson_binary_sha256": file_sha256(binary),
        "branson_needed_libraries": needed_libraries,
        "mpi_mode": metadata["mpi_mode"],
        "input_sha256": file_sha256(input_path),
        "gem5_sha256": file_sha256(gem5),
        "engine_sha256": file_sha256(ENGINE),
        "runner_sha256": file_sha256(RUNNER),
        "config_sha256": file_sha256(config),
        "metadata_sha256": file_sha256(metadata_path),
        "dependencies": [
            {"path": str(path), "sha256": file_sha256(path)}
            for path in dependencies
        ],
        "command": command,
        "claim_boundary": (
            "One real one-rank Branson process uses a fail-closed one-rank "
            "MPI shim and executes native photon physics, "
            "streams every captured event through opcode 5, replaces only its "
            "two scalar cell-tally updates, verifies returned tallies, and "
            "continues into native conservation checks. This is not photon-"
            "transport acceleration, speedup, production MPI, or RTL evidence."
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
                "native Branson gem5 process returned " f"{result.returncode}"
            )
        stdout_text = (outdir / "stdout.log").read_text(encoding="utf-8")
        stderr_text = (outdir / "stderr.log").read_text(encoding="utf-8")
        if "LANLMAA_SIM_TERMINAL code=0 " not in stdout_text:
            raise ValueError("missing successful gem5 terminal marker")
        if "panic:" in stderr_text or "fatal:" in stderr_text:
            raise ValueError("gem5 stderr contains a panic or fatal marker")
        submission = json.loads(submission_path.read_text(encoding="utf-8"))
        validate_submission(submission, metadata)
        marker = (
            f"LANL_MAA_BRANSON_SUBMISSION roots={metadata['roots']} "
            f"events={metadata['events']} cells={metadata['cells']} "
            f"batches={metadata['descriptor_batches']}"
        )
        if marker not in stdout_text:
            raise ValueError("missing native Branson submission marker")
        report["conservation_residuals"] = validate_application_output(
            stdout_text
        )
        report["submission"] = submission
        report["submission_sha256"] = file_sha256(submission_path)
        stats_path = m5out / "stats.txt"
        report["metrics"] = read_stats(
            stats_path, metadata, arguments.model_payload_overlay_ports
        )
        report["stats_sha256"] = file_sha256(stats_path)
        report["stdout_sha256"] = file_sha256(outdir / "stdout.log")
        report["stderr_sha256"] = file_sha256(outdir / "stderr.log")
        report["status"] = "validated"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        report_path.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
