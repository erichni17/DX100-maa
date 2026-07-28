#!/usr/bin/env python3

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
    prefix = name + " "
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return int(float(line.split()[1]))
    return None


def require_equal(errors, stats, name, expected):
    actual = read_scalar(stats, "system.lanl_maa." + name)
    if actual != expected:
        errors.append(f"{name}: expected {expected}, observed {actual}")


def validate(stats, adversarial_rearm):
    errors = []
    expected = {
        "descriptorDoorbells": 3 if adversarial_rearm else 1,
        "descriptorBusyRejections": 0,
        "descriptorRearms": 2 if adversarial_rearm else 0,
        "descriptorFetches": 3 if adversarial_rearm else 1,
        "descriptorCompletionWrites": 1,
        "descriptorErrors": 2 if adversarial_rearm else 0,
        "descriptorBransonEventsReplayed": 12,
        "descriptorBransonUpdatesAcknowledged": 24,
        "physicalAtomicUpdates": 24,
        "atomicAcknowledgements": 24,
        "continuationExhaustions": 0,
    }
    for name, value in expected.items():
        require_equal(errors, stats, name, value)

    validated = read_scalar(
        stats, "system.lanl_maa.descriptorBransonEventsValidated"
    )
    if (
        validated is None
        or validated < 12
        or (not adversarial_rearm and validated != 12)
    ):
        errors.append(
            "descriptorBransonEventsValidated: expected at least 12, "
            f"observed {validated}"
        )
    roots = read_scalar(stats, "system.lanl_maa.descriptorBransonRootsLoaded")
    expected_roots = 12 if adversarial_rearm else 4
    if roots != expected_roots:
        errors.append(
            "descriptorBransonRootsLoaded: expected "
            f"{expected_roots}, observed {roots}"
        )
    queued = read_scalar(
        stats, "system.lanl_maa.descriptorBransonEventComputesQueued"
    )
    issued = read_scalar(
        stats, "system.lanl_maa.descriptorBransonEventComputesIssued"
    )
    completed = read_scalar(
        stats, "system.lanl_maa.descriptorBransonEventComputesCompleted"
    )
    cancelled = read_scalar(
        stats, "system.lanl_maa.descriptorBransonEventComputesCancelled"
    )
    cancelled_in_flight = read_scalar(
        stats,
        "system.lanl_maa." "descriptorBransonEventComputesCancelledInFlight",
    )
    compute_counts = (
        queued,
        issued,
        completed,
        cancelled,
        cancelled_in_flight,
    )
    if any(value is None for value in compute_counts):
        errors.append(f"missing event-compute counters: {compute_counts}")
    else:
        if queued != completed + cancelled:
            errors.append(
                "queued event-compute accounting did not close: "
                f"queued={queued}, completed={completed}, "
                f"cancelled={cancelled}"
            )
        if issued != completed + cancelled_in_flight:
            errors.append(
                "issued event-compute accounting did not close: "
                f"issued={issued}, completed={completed}, "
                f"cancelled_in_flight={cancelled_in_flight}"
            )
    committed = read_scalar(stats, "system.cpu.commitStats0.numInsts")
    if committed is None or committed <= 0:
        errors.append("CPU retired no instructions")
    ticks = read_scalar(stats, "simTicks")
    if ticks is None or ticks <= 0:
        errors.append("simulation produced no positive simTicks")
    if errors:
        raise RuntimeError(
            "Branson event descriptor evidence checks failed:\n  "
            + "\n  ".join(errors)
        )
    metric_names = (
        "physicalLineReads",
        "lineMergeHits",
        "contextWouldBlockCycles",
        "descriptorCycles",
        "engineCycles",
        "bransonEventComputeWouldBlockCycles",
        "bransonEventComputeActiveCycles",
        "activeBransonEventComputeHighWaterMark",
    )
    mechanism = {
        name: read_scalar(stats, "system.lanl_maa." + name)
        for name in metric_names
    }
    if any(value is None for value in mechanism.values()):
        raise RuntimeError(f"missing mechanism counters: {mechanism}")
    return {
        "descriptor_branson_events_validated": validated,
        "descriptor_branson_events_replayed": 12,
        "descriptor_branson_updates_acknowledged": 24,
        "descriptor_branson_event_computes_cancelled": cancelled,
        "descriptor_branson_event_computes_cancelled_in_flight": (
            cancelled_in_flight
        ),
        "descriptor_branson_event_computes_queued": queued,
        "descriptor_branson_event_computes_issued": issued,
        "descriptor_branson_event_computes_completed": completed,
        "cpu_committed_instructions": committed,
        "sim_ticks": ticks,
        **mechanism,
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
        default=here / "branson_event_descriptor_cpu_smoke.py",
    )
    parser.add_argument(
        "--source",
        type=pathlib.Path,
        default=repo / "benchmarks/LANL/branson_event_descriptor_cpu.c",
    )
    parser.add_argument(
        "--metadata",
        type=pathlib.Path,
        default=here / "branson_event_descriptor_cpu_smoke.json",
    )
    parser.add_argument("--contexts", type=int, default=4)
    parser.add_argument("--context-quantum", type=int, default=4)
    parser.add_argument("--event-compute-latency", type=int, default=4)
    parser.add_argument(
        "--event-compute-initiation-interval", type=int, default=1
    )
    parser.add_argument("--event-compute-units", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--success-only", action="store_true")
    args = parser.parse_args()

    outdir = args.outdir.resolve()
    if outdir.exists():
        raise RuntimeError(f"refusing to reuse evidence directory: {outdir}")
    outdir.mkdir(parents=True)
    compiler = shutil.which("cc")
    if not compiler:
        raise RuntimeError("Branson event smoke requires cc")

    binary = outdir / "branson_event_descriptor_cpu.elf"
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
        f"-DBRANSON_ADVERSARIAL_REARM={0 if args.success_only else 1}",
        str(args.source.resolve()),
        "-o",
        str(binary),
    ]
    subprocess.run(compile_command, check=True)

    m5out = outdir / "m5out"
    command = [
        str(args.gem5.resolve()),
        f"--outdir={m5out}",
        str(args.config.resolve()),
        f"--binary={binary}",
        f"--metadata={args.metadata.resolve()}",
        f"--contexts={args.contexts}",
        f"--context-quantum={args.context_quantum}",
        f"--event-compute-latency={args.event_compute_latency}",
        "--event-compute-initiation-interval="
        f"{args.event_compute_initiation_interval}",
        f"--event-compute-units={args.event_compute_units}",
    ]
    provenance = {
        "schema": "lanl-maa-branson-event-cpu-smoke-v1",
        "runner": str(pathlib.Path(__file__).resolve()),
        "runner_sha256": file_sha256(pathlib.Path(__file__).resolve()),
        "gem5": str(args.gem5.resolve()),
        "gem5_sha256": file_sha256(args.gem5.resolve()),
        "source": str(args.source.resolve()),
        "source_sha256": file_sha256(args.source.resolve()),
        "config": str(args.config.resolve()),
        "config_sha256": file_sha256(args.config.resolve()),
        "metadata": str(args.metadata.resolve()),
        "metadata_sha256": file_sha256(args.metadata.resolve()),
        "binary_sha256": file_sha256(binary),
        "adversarial_rearm": not args.success_only,
        "compile_command": compile_command,
        "simulation_command": command,
        "status": "running",
    }
    report = outdir / "report.json"
    report.write_text(json.dumps(provenance, indent=2) + "\n")

    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=args.timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        provenance["status"] = "failed"
        provenance["failure"] = f"gem5 exceeded {args.timeout_seconds} seconds"
        report.write_text(json.dumps(provenance, indent=2) + "\n")
        raise RuntimeError(provenance["failure"]) from error
    (outdir / "gem5.stdout").write_text(result.stdout, encoding="utf-8")
    (outdir / "gem5.stderr").write_text(result.stderr, encoding="utf-8")
    provenance["returncode"] = result.returncode
    stats = m5out / "stats.txt"
    try:
        if result.returncode != 0:
            raise RuntimeError(
                "gem5 Branson event descriptor smoke failed:\n"
                + result.stdout
                + result.stderr
            )
        if not stats.is_file() or stats.stat().st_size == 0:
            raise RuntimeError("gem5 produced no nonempty final stats.txt")
        provenance["metrics"] = validate(
            stats, adversarial_rearm=not args.success_only
        )
        provenance["status"] = "passed"
    except Exception as error:
        provenance["status"] = "failed"
        provenance["failure"] = str(error)
        report.write_text(json.dumps(provenance, indent=2) + "\n")
        raise
    report.write_text(json.dumps(provenance, indent=2) + "\n")
    print("LANLMAA Branson event descriptor CPU smoke: PASS")


if __name__ == "__main__":
    main()
