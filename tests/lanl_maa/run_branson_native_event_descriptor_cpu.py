#!/usr/bin/env python3
"""Run the native-derived Branson replay through bounded live descriptors."""

import argparse
import hashlib
import json
import pathlib
import shutil
import struct
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


def validate_input(path, metadata):
    data = path.read_bytes()
    if len(data) != metadata["input_bytes"]:
        raise RuntimeError("native-derived replay input size changed")
    header = struct.unpack_from("<8sIIIIQQQQQ", data)
    expected = (
        b"BNERPLY1",
        1,
        metadata["events"],
        metadata["roots"],
        metadata["cells"],
        64,
        64 + metadata["events"] * 32,
        64 + metadata["events"] * 32 + metadata["roots"] * 16,
        64
        + metadata["events"] * 32
        + metadata["roots"] * 16
        + metadata["cells"] * 8,
        metadata["input_bytes"],
    )
    if header != expected:
        raise RuntimeError(f"native-derived replay header changed: {header}")
    counts = [
        struct.unpack_from("<I", data, header[6] + root * 16 + 4)[0]
        for root in range(metadata["roots"])
    ]
    if sum(counts) != metadata["events"]:
        raise RuntimeError("native-derived root/event accounting changed")
    if max(counts) != metadata["maximum_events_per_root"]:
        raise RuntimeError("native-derived maximum root length changed")


def validate_stats(stats, metadata):
    roots = metadata["roots"]
    events = metadata["events"]
    batches = metadata["descriptor_batches"]
    logical_updates = 2 * events
    errors = []
    expected = {
        "descriptorDoorbells": batches,
        "descriptorBusyRejections": 0,
        "descriptorRearms": batches - 1,
        "descriptorFetches": batches,
        "descriptorCompletionWrites": batches,
        "descriptorErrors": 0,
        "descriptorBransonRootsLoaded": roots,
        "descriptorBransonEventsValidated": events,
        "descriptorBransonEventsReplayed": events,
        "descriptorBransonUpdatesAcknowledged": logical_updates,
        "descriptorBransonEventComputesQueued": 2 * events,
        "descriptorBransonEventComputesIssued": 2 * events,
        "descriptorBransonEventComputesCompleted": 2 * events,
        "descriptorBransonEventComputesCancelled": 0,
        "descriptorBransonEventComputesCancelledInFlight": 0,
        "continuationSteps": 2 * events,
        "continuationExhaustions": 0,
        "updateOperationsAcknowledged": logical_updates,
    }
    for name, value in expected.items():
        require_equal(errors, stats, name, value)
    physical_updates = read_scalar(
        stats, "system.lanl_maa.physicalAtomicUpdates"
    )
    acknowledgements = read_scalar(
        stats, "system.lanl_maa.atomicAcknowledgements"
    )
    if physical_updates is None or physical_updates <= 0:
        errors.append(f"invalid physical atomic count: {physical_updates}")
    if acknowledgements != physical_updates:
        errors.append(
            "physical atomic acknowledgement accounting did not close: "
            f"issued={physical_updates}, acknowledged={acknowledgements}"
        )
    physical_reads = read_scalar(stats, "system.lanl_maa.physicalLineReads")
    line_merges = read_scalar(stats, "system.lanl_maa.lineMergeHits")
    if (
        physical_reads is None
        or line_merges is None
        or (physical_reads + line_merges != 2 * events)
    ):
        errors.append(
            "two-pass event-read accounting did not close: "
            f"reads={physical_reads}, merges={line_merges}, "
            f"expected={2 * events}"
        )
    for name in (
        "updateDrains",
        "atomicFp64AddUpdates",
        "atomicOldValuesReturned",
    ):
        value = read_scalar(stats, "system.lanl_maa." + name)
        if value != physical_updates:
            errors.append(
                f"{name}: expected {physical_updates}, observed {value}"
            )
    committed = read_scalar(stats, "system.cpu.commitStats0.numInsts")
    ticks = read_scalar(stats, "simTicks")
    if committed is None or committed <= 0:
        errors.append("CPU retired no instructions")
    if ticks is None or ticks <= 0:
        errors.append("simulation produced no positive simTicks")
    if errors:
        raise RuntimeError(
            "native Branson descriptor checks failed:\n  "
            + "\n  ".join(errors)
        )
    metric_names = (
        "physicalLineReads",
        "lineMergeHits",
        "operationWouldBlockCycles",
        "lineWouldBlockCycles",
        "contextWouldBlockCycles",
        "descriptorCycles",
        "engineCycles",
        "bransonEventComputeWouldBlockCycles",
        "bransonEventComputeActiveCycles",
        "activeBransonEventComputeHighWaterMark",
        "activeContextHighWaterMark",
        "updateCombinerHits",
        "updateDrains",
        "updateTableWouldBlockCycles",
        "updateAddressBusyCycles",
        "portSendFailures",
        "portRetryNotifications",
        "retryPacketResubmissions",
        "retryPacketAcceptances",
        "responses",
    )
    mechanism = {
        name: read_scalar(stats, "system.lanl_maa." + name)
        for name in metric_names
    }
    missing = [name for name, value in mechanism.items() if value is None]
    if missing:
        raise RuntimeError(f"missing mechanism counters: {missing}")
    if not (
        mechanism["portSendFailures"]
        == mechanism["portRetryNotifications"]
        == mechanism["retryPacketResubmissions"]
    ):
        raise RuntimeError("timing retry accounting did not close")
    if not (
        0
        <= mechanism["retryPacketAcceptances"]
        <= mechanism["retryPacketResubmissions"]
    ):
        raise RuntimeError("invalid timing retry acceptance accounting")
    cache_metrics = {
        "maa_cache_accesses": read_scalar(
            stats, "system.maa_cache.overallAccesses_T::total"
        ),
        "maa_cache_misses": read_scalar(
            stats, "system.maa_cache.overallMisses_T::total"
        ),
        "maa_cache_replacements": read_scalar(
            stats, "system.maa_cache.replacements_T"
        ),
        "membus_snoops": read_scalar(stats, "system.membus.snoops"),
    }
    missing_cache = [
        name for name, value in cache_metrics.items() if value is None
    ]
    if missing_cache:
        raise RuntimeError(
            f"missing cache/coherence counters: {missing_cache}"
        )
    return {
        "roots": roots,
        "events": events,
        "descriptor_batches": batches,
        "logical_fp64_updates": logical_updates,
        "physical_atomic_updates": physical_updates,
        "atomic_acknowledgements": acknowledgements,
        "cpu_committed_instructions": committed,
        "sim_ticks": ticks,
        **mechanism,
        **cache_metrics,
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
        default=repo / "benchmarks/LANL/branson_native_event_descriptor_cpu.c",
    )
    parser.add_argument(
        "--metadata",
        type=pathlib.Path,
        default=here / "branson_native_event_descriptor_cpu.json",
    )
    parser.add_argument(
        "--input",
        type=pathlib.Path,
        default=(
            repo
            / "benchmarks/LANL/inputs/branson_native_event_replay_t1_v1.bin"
        ),
    )
    parser.add_argument("--contexts", type=int, default=64)
    parser.add_argument("--context-quantum", type=int, default=4)
    parser.add_argument("--event-compute-latency", type=int, default=4)
    parser.add_argument(
        "--event-compute-initiation-interval", type=int, default=1
    )
    parser.add_argument("--event-compute-units", type=int, default=1)
    parser.add_argument("--line-entries", type=int, default=32)
    parser.add_argument("--update-entries", type=int, default=64)
    parser.add_argument("--update-banks", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()

    outdir = args.outdir.resolve()
    if outdir.exists():
        raise RuntimeError(f"refusing to reuse evidence directory: {outdir}")
    outdir.mkdir(parents=True)
    compiler = shutil.which("cc")
    if not compiler:
        raise RuntimeError("native Branson descriptor replay requires cc")

    metadata_path = args.metadata.resolve(strict=True)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    replay_input = args.input.resolve(strict=True)
    if file_sha256(replay_input) != metadata["input_sha256"]:
        raise RuntimeError("native-derived replay input hash changed")
    validate_input(replay_input, metadata)
    if args.contexts <= 0 or args.contexts > metadata["operation_entries"]:
        raise RuntimeError("contexts must fit the fixed operation window")
    if (
        args.line_entries <= 0
        or args.update_entries <= 0
        or args.update_banks <= 0
        or args.update_entries % args.update_banks != 0
    ):
        raise RuntimeError("invalid line/update structure geometry")

    source = args.source.resolve(strict=True)
    binary = outdir / "branson_native_event_descriptor_cpu.elf"
    input_define = f'-DBRANSON_REPLAY_INPUT="{replay_input}"'
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
        input_define,
        str(source),
        "-o",
        str(binary),
    ]
    subprocess.run(compile_command, check=True)

    m5out = outdir / "m5out"
    config = args.config.resolve(strict=True)
    command = [
        str(args.gem5.resolve(strict=True)),
        f"--outdir={m5out}",
        str(config),
        f"--binary={binary}",
        f"--metadata={metadata_path}",
        f"--contexts={args.contexts}",
        f"--context-quantum={args.context_quantum}",
        f"--event-compute-latency={args.event_compute_latency}",
        "--event-compute-initiation-interval="
        f"{args.event_compute_initiation_interval}",
        f"--event-compute-units={args.event_compute_units}",
        f"--line-entries={args.line_entries}",
        f"--update-entries={args.update_entries}",
        f"--update-banks={args.update_banks}",
    ]
    report_data = {
        "schema": "lanl-maa-branson-native-event-descriptor-v1",
        "status": "running",
        "claim_boundary": (
            "Native-derived event outcomes replayed in sixteen independent "
            "descriptor windows; native event physics, whole-trace atomicity, "
            "CPU speedup, and physical datapath cost are not established."
        ),
        "correctness": (
            "All 961 roots and 8,199 events complete; each 64-root-or-smaller "
            "window checks its completion record; all 12,000 final FP64 tally "
            "values match the embedded native oracle within 1e-12 times the "
            "maximum of one and the observed/expected magnitudes."
        ),
        "window_contract": {
            "operation_entries": metadata["operation_entries"],
            "continuation_contexts": args.contexts,
            "maximum_roots_per_descriptor": metadata["max_descriptor_items"],
            "coherent_line_entries": args.line_entries,
            "update_combiner_entries": args.update_entries,
            "update_combiner_banks": args.update_banks,
            "descriptor_batches": metadata["descriptor_batches"],
            "whole_trace_transactional_rollback": False,
        },
        "runner": str(pathlib.Path(__file__).resolve()),
        "runner_sha256": file_sha256(pathlib.Path(__file__).resolve()),
        "gem5": str(args.gem5.resolve()),
        "gem5_sha256": file_sha256(args.gem5.resolve()),
        "source": str(source),
        "source_sha256": file_sha256(source),
        "config": str(config),
        "config_sha256": file_sha256(config),
        "metadata": str(metadata_path),
        "metadata_sha256": file_sha256(metadata_path),
        "input": str(replay_input),
        "input_sha256": file_sha256(replay_input),
        "binary_sha256": file_sha256(binary),
        "compile_command": compile_command,
        "simulation_command": command,
    }
    report = outdir / "report.json"
    report.write_text(json.dumps(report_data, indent=2) + "\n")
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=args.timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        report_data["status"] = "failed"
        report_data[
            "failure"
        ] = f"gem5 exceeded {args.timeout_seconds} seconds"
        report.write_text(json.dumps(report_data, indent=2) + "\n")
        raise RuntimeError(report_data["failure"]) from error
    (outdir / "gem5.stdout").write_text(result.stdout, encoding="utf-8")
    (outdir / "gem5.stderr").write_text(result.stderr, encoding="utf-8")
    report_data["returncode"] = result.returncode
    stats = m5out / "stats.txt"
    try:
        if result.returncode != 0:
            raise RuntimeError(
                "native Branson descriptor replay failed:\n"
                + result.stdout
                + result.stderr
            )
        if "LANLMAA_SIM_TERMINAL code=0 " not in result.stdout:
            raise RuntimeError("gem5 emitted no successful terminal marker")
        if not stats.is_file() or stats.stat().st_size == 0:
            raise RuntimeError("gem5 produced no nonempty final stats.txt")
        report_data["metrics"] = validate_stats(stats, metadata)
        report_data["status"] = "passed"
    except Exception as error:
        report_data["status"] = "failed"
        report_data["failure"] = str(error)
        report.write_text(json.dumps(report_data, indent=2) + "\n")
        raise
    report.write_text(json.dumps(report_data, indent=2) + "\n")
    print("LANLMAA native Branson event descriptor replay: PASS")


if __name__ == "__main__":
    main()
