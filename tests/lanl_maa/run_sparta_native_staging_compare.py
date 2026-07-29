#!/usr/bin/env python3
"""Compare direct and native-list staging for one exact SPARTA batch."""

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def reduction_percent(baseline, candidate):
    if baseline <= 0:
        raise ValueError("reduction baseline must be positive")
    return 100.0 * (baseline - candidate) / baseline


def read_report(path, batch_sha256, timestep, list_staging, cell_group):
    report = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "status": "validated",
        "native_batch_sha256": batch_sha256,
        "native_batch_timestep": timestep,
        "sparta_native_batch": True,
        "sparta_native_list_staging": list_staging,
        "sparta_cell_group": cell_group,
        "native_timing_clean": True,
    }
    for name, value in expected.items():
        if report.get(name) != value:
            raise RuntimeError(f"staging report {name} mismatch: {path}")
    metrics = report["metrics"]
    if metrics["logical_updates"] != 384:
        raise RuntimeError(
            f"staging report logical work did not close: {path}"
        )
    if metrics["validated_contributions"] != 384:
        raise RuntimeError(
            f"staging report contributions did not close: {path}"
        )
    validation = metrics.get("native_tally_validation")
    if not validation or validation.get("passed") is not True:
        raise RuntimeError(
            f"staging report lacks numerical validation: {path}"
        )
    if cell_group:
        if metrics["cell_group_forced_drains"] != 0:
            raise RuntimeError(f"staging report forced a group drain: {path}")
        if (
            metrics["cell_group_complete_drains"]
            != metrics["physical_atomic_updates"]
        ):
            raise RuntimeError(
                f"staging report drain accounting failed: {path}"
            )
    return report


def compact_metrics(report):
    metrics = report["metrics"]
    return {
        name: metrics[name]
        for name in (
            "cpu_cycles",
            "cpu_committed_instructions",
            "engine_cycles",
            "descriptor_cycles",
            "logical_updates",
            "validated_contributions",
            "physical_atomic_updates",
            "physical_line_reads",
            "combiner_hits",
            "cell_group_complete_drains",
            "cell_group_forced_drains",
        )
    }


def main():
    here = pathlib.Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", required=True, type=pathlib.Path)
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--outdir", required=True, type=pathlib.Path)
    parser.add_argument(
        "--runner",
        type=pathlib.Path,
        default=here / "run_sparta_tally_cpu_smoke.py",
    )
    parser.add_argument("--expected-timestep", required=True, type=int)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()

    if args.expected_timestep < 0:
        parser.error("expected timestep must be nonnegative")
    batch = args.batch.resolve(strict=True)
    batch_document = json.loads(batch.read_text(encoding="utf-8"))
    if batch_document.get("timestep") != args.expected_timestep:
        parser.error("batch timestep disagrees with expected timestep")
    batch_sha256 = file_sha256(batch)
    gem5 = args.gem5.resolve(strict=True)
    runner = args.runner.resolve(strict=True)
    outdir = args.outdir.resolve()
    if outdir.exists():
        raise RuntimeError(f"refusing to reuse staging directory: {outdir}")
    outdir.mkdir(parents=True)
    report_path = outdir / "staging_report.json"
    result = {
        "schema": "lanl-maa-sparta-native-staging-compare-v1",
        "status": "running",
        "batch_path": str(batch),
        "batch_sha256": batch_sha256,
        "timestep": args.expected_timestep,
        "gem5_path": str(gem5),
        "gem5_sha256": file_sha256(gem5),
        "runner_path": str(runner),
        "runner_sha256": file_sha256(runner),
        "claim_boundary": (
            "One collision-active native batch; first/count/next traversal "
            "and staging-copy cost only, not SPARTA application speedup, "
            "native descriptor submission, or physical cost."
        ),
        "commands": [],
    }
    report_path.write_text(json.dumps(result, indent=2) + "\n")
    reports = {}
    try:
        for list_staging in (False, True):
            for cell_group in (False, True):
                case = (
                    ("native-list" if list_staging else "direct")
                    + ("-cell-group" if cell_group else "-baseline")
                )
                case_dir = outdir / case
                command = [
                    sys.executable,
                    str(runner),
                    "--gem5",
                    str(gem5),
                    "--outdir",
                    str(case_dir),
                    "--mode",
                    "sorted",
                    "--sparta-native-batch",
                    str(batch),
                    "--native-timing-clean",
                    "--timeout-seconds",
                    str(args.timeout_seconds),
                ]
                if list_staging:
                    command.append("--sparta-native-list-staging")
                if cell_group:
                    command.append("--sparta-cell-group")
                result["commands"].append(command)
                report_path.write_text(json.dumps(result, indent=2) + "\n")
                subprocess.run(command, check=True)
                reports[case] = read_report(
                    case_dir / "report.json",
                    batch_sha256,
                    args.expected_timestep,
                    list_staging,
                    cell_group,
                )

        identity_fields = (
            "gem5_sha256",
            "source_sha256",
            "metadata_sha256",
            "native_batch_sha256",
            "native_header_sha256",
        )
        reference = reports["direct-baseline"]
        for case, report in reports.items():
            for field in identity_fields:
                if report[field] != reference[field]:
                    raise RuntimeError(
                        f"{case} changed identity field {field}"
                    )

        mechanism_fields = (
            "engine_cycles",
            "descriptor_cycles",
            "logical_updates",
            "validated_contributions",
            "physical_atomic_updates",
            "physical_line_reads",
            "combiner_hits",
            "cell_group_complete_drains",
            "cell_group_forced_drains",
        )
        for policy in ("baseline", "cell-group"):
            direct = reports[f"direct-{policy}"]["metrics"]
            native_list = reports[f"native-list-{policy}"]["metrics"]
            for field in mechanism_fields:
                if direct[field] != native_list[field]:
                    raise RuntimeError(
                        f"native-list staging changed {policy} {field}"
                    )

        result["cases"] = {
            case: {
                "report_sha256": file_sha256(
                    outdir / case / "report.json"
                ),
                "binary_sha256": report["binary_sha256"],
                "metrics": compact_metrics(report),
            }
            for case, report in reports.items()
        }
        result["staging_overhead"] = {}
        for policy in ("baseline", "cell-group"):
            direct = reports[f"direct-{policy}"]["metrics"]
            native_list = reports[f"native-list-{policy}"]["metrics"]
            result["staging_overhead"][policy] = {
                "cpu_cycles": native_list["cpu_cycles"] - direct["cpu_cycles"],
                "cpu_committed_instructions": (
                    native_list["cpu_committed_instructions"]
                    - direct["cpu_committed_instructions"]
                ),
                "cpu_cycle_increase_percent": reduction_percent(
                    direct["cpu_cycles"], native_list["cpu_cycles"]
                )
                * -1.0,
            }
        direct_baseline = reports["direct-baseline"]["metrics"]
        direct_group = reports["direct-cell-group"]["metrics"]
        list_baseline = reports["native-list-baseline"]["metrics"]
        list_group = reports["native-list-cell-group"]["metrics"]
        result["cell_group_cpu_reduction_percent"] = {
            "direct": reduction_percent(
                direct_baseline["cpu_cycles"], direct_group["cpu_cycles"]
            ),
            "native_list": reduction_percent(
                list_baseline["cpu_cycles"], list_group["cpu_cycles"]
            ),
        }
        result["status"] = "validated"
    except Exception as error:
        result["status"] = "failed"
        result["error"] = str(error)
        raise
    finally:
        report_path.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
