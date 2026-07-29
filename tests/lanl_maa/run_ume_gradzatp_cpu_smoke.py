#!/usr/bin/env python3
"""Prepare, launch, and validate the live UME gradzatp descriptor smoke."""

import argparse
import hashlib
import json
import pathlib
import re
import shutil
import subprocess

STAT = re.compile(r"^(\S+)\s+([-+0-9.eE]+)(?:\s|$)")


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_stats(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = STAT.match(line)
        if match:
            values[match.group(1)] = float(match.group(2))
    return values


def scalar(stats, name):
    full = "system.lanl_maa." + name
    value = stats.get(full)
    if value is None:
        raise RuntimeError(f"missing required statistic: {full}")
    if not value.is_integer():
        raise RuntimeError(f"nonintegral required statistic: {full}={value}")
    return int(value)


def validate_stats(path, metadata):
    stats = read_stats(path)
    success = metadata["successful_submissions"]
    rejected = metadata["rejected_submissions"]
    logical_updates = metadata["logical_fp32_updates"]
    exact = {
        "descriptorDoorbells": success + rejected,
        "descriptorRearms": success + rejected - 1,
        "descriptorCompletionWrites": success,
        "descriptorErrors": rejected,
        "descriptorUmeUpdatesAcknowledged": logical_updates,
        "updateOperationsAcknowledged": logical_updates,
        "atomicFp32AddUpdates": None,
        "physicalAtomicUpdates": None,
        "atomicAcknowledgements": None,
        "atomicOldValuesReturned": None,
    }
    observed = {name: scalar(stats, name) for name in exact}
    for name, expected in exact.items():
        if expected is not None and observed[name] != expected:
            raise RuntimeError(
                f"{name}: expected {expected}, observed {observed[name]}"
            )
    physical = observed["physicalAtomicUpdates"]
    if physical <= 0 or physical > logical_updates:
        raise RuntimeError(
            "invalid physical FP32 atomic count: "
            f"physical={physical}, logical={logical_updates}"
        )
    for name in (
        "atomicFp32AddUpdates",
        "atomicAcknowledgements",
        "atomicOldValuesReturned",
    ):
        if observed[name] != physical:
            raise RuntimeError(
                f"{name}: expected physical count {physical}, "
                f"observed {observed[name]}"
            )
    minimums = {
        "descriptorUmeCornersClassified": metadata["corners"] * success,
        "descriptorUmeActiveCorners": metadata["active_corners"] * success,
        "descriptorUmeInactiveCorners": (
            metadata["corners"] - metadata["active_corners"]
        )
        * success,
        "descriptorUmeCornersValidated": metadata["active_corners"] * success,
        "descriptorUmeZoneFieldGathers": metadata["active_corners"] * success,
        "descriptorUmeOutputZeroReads": 2
        * metadata["active_corners"]
        * success,
        "descriptorUmeFp32Multiplies": metadata["active_corners"] * success,
    }
    for name, minimum in minimums.items():
        observed[name] = scalar(stats, name)
        if observed[name] < minimum:
            raise RuntimeError(
                f"{name}: expected at least {minimum}, "
                f"observed {observed[name]}"
            )
    retry_names = (
        "portSendFailures",
        "portRetryNotifications",
        "retryPacketResubmissions",
        "retryPacketAcceptances",
    )
    retry = {name: scalar(stats, name) for name in retry_names}
    if not (
        retry["portSendFailures"]
        == retry["portRetryNotifications"]
        == retry["retryPacketResubmissions"]
        == retry["retryPacketAcceptances"]
    ):
        raise RuntimeError(f"timing retry accounting did not close: {retry}")
    sim_ticks = stats.get("simTicks")
    committed = stats.get("system.cpu.commitStats0.numInsts")
    if sim_ticks is None or sim_ticks <= 0:
        raise RuntimeError("simulation produced no positive simTicks")
    if committed is None or committed <= 0:
        raise RuntimeError("CPU retired no instructions")
    return {
        **observed,
        **retry,
        "simTicks": int(sim_ticks),
        "cpuCommittedInstructions": int(committed),
        "updateCombinerHits": scalar(stats, "updateCombinerHits"),
        "updateDrains": scalar(stats, "updateDrains"),
        "physicalLineReads": scalar(stats, "physicalLineReads"),
        "lineMergeHits": scalar(stats, "lineMergeHits"),
        "descriptorCycles": scalar(stats, "descriptorCycles"),
        "engineCycles": scalar(stats, "engineCycles"),
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
        default=here / "ume_gradzatp_cpu_smoke.py",
    )
    parser.add_argument(
        "--source",
        type=pathlib.Path,
        default=repo / "benchmarks/LANL/ume_gradzatp_cpu_smoke.c",
    )
    parser.add_argument(
        "--metadata",
        type=pathlib.Path,
        default=here / "ume_gradzatp_cpu_smoke.json",
    )
    parser.add_argument("--line-entries", type=int, default=16)
    parser.add_argument("--update-entries", type=int, default=64)
    parser.add_argument("--update-banks", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument(
        "--launch",
        action="store_true",
        help="launch a new or identity-matched prepared smoke",
    )
    args = parser.parse_args()

    gem5 = args.gem5.resolve(strict=True)
    source = args.source.resolve(strict=True)
    config = args.config.resolve(strict=True)
    metadata_path = args.metadata.resolve(strict=True)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    outdir = args.outdir.resolve()
    binary = outdir / "ume_gradzatp_cpu_smoke.elf"
    m5out = outdir / "m5out"
    report_path = outdir / "report.json"
    compiler = shutil.which("cc")
    if not compiler:
        raise RuntimeError("UME gradzatp smoke requires cc")
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
        str(source),
        "-o",
        str(binary),
    ]
    command = [
        str(gem5),
        f"--outdir={m5out}",
        str(config),
        f"--binary={binary}",
        f"--metadata={metadata_path}",
        f"--line-entries={args.line_entries}",
        f"--update-entries={args.update_entries}",
        f"--update-banks={args.update_banks}",
    ]
    identity = {
        "schema": "lanl-maa-ume-gradzatp-live-v1",
        "source_sha256": file_sha256(source),
        "config_sha256": file_sha256(config),
        "metadata_sha256": file_sha256(metadata_path),
        "gem5_sha256": file_sha256(gem5),
        "compile_command": compile_command,
        "command": command,
    }

    if outdir.exists():
        if not args.launch or not report_path.is_file():
            raise RuntimeError(
                f"refusing to reuse evidence directory: {outdir}"
            )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("status") != "prepared":
            raise RuntimeError("existing UME smoke is not in prepared state")
        for name, value in identity.items():
            if report.get(name) != value:
                raise RuntimeError(
                    f"prepared UME smoke identity changed: {name}"
                )
        if report.get("binary_sha256") != file_sha256(binary):
            raise RuntimeError("prepared UME smoke binary changed")
    else:
        outdir.mkdir(parents=True)
        subprocess.run(compile_command, check=True)
        report = {
            **identity,
            "status": "prepared",
            "binary_sha256": file_sha256(binary),
            "claim_boundary": (
                "UME gradzatp corner predicate, indexed gathers, one FP32 "
                "multiply, and two relaxed FP32 point reductions; synthetic "
                "FLAG-proxy microbenchmark only, not application speedup or "
                "synthesized physical cost."
            ),
        }
        report_path.write_text(json.dumps(report, indent=2) + "\n")
    if not args.launch:
        print(f"prepared UME gradzatp smoke: {report_path}")
        return

    report["status"] = "running"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    try:
        subprocess.run(
            command,
            check=True,
            timeout=args.timeout_seconds,
            stdout=(outdir / "stdout.log").open("w", encoding="utf-8"),
            stderr=(outdir / "stderr.log").open("w", encoding="utf-8"),
        )
        report["metrics"] = validate_stats(m5out / "stats.txt", metadata)
        report["status"] = "validated"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        report_path.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
