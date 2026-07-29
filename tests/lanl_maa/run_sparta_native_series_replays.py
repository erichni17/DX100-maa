#!/usr/bin/env python3
"""Run matched diagnostic and timing-clean replays for selected SPARTA batches."""

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


def read_case(path, timestep, cell_group, timing_clean, artifact_sha256):
    report = json.loads(path.read_text(encoding="utf-8"))
    if report["status"] != "validated":
        raise RuntimeError(f"replay is not validated: {path}")
    expected = {
        "native_batch_timestep": timestep,
        "sparta_cell_group": cell_group,
        "native_timing_clean": timing_clean,
        "native_batch_sha256": artifact_sha256,
    }
    for name, value in expected.items():
        if report.get(name) != value:
            raise RuntimeError(f"replay identity mismatch for {name}: {path}")
    metrics = report["metrics"]
    if metrics["logical_updates"] != 384 or metrics["validated_contributions"] != 384:
        raise RuntimeError(f"replay work conservation failed: {path}")
    if timing_clean:
        validation = metrics.get("native_tally_validation")
        if not validation or validation.get("passed") is not True:
            raise RuntimeError(f"timing replay lacks numerical validation: {path}")
    else:
        diagnostics = metrics.get("native_tally_diagnostics")
        if not diagnostics or diagnostics["max_relative_error"] > 1.0e-12:
            raise RuntimeError(f"diagnostic replay violates tolerance: {path}")
    return report


def summarize_step(step, reports, predicted_atomics):
    diagnostic_baseline = reports["diagnostic-baseline"]
    diagnostic_group = reports["diagnostic-cell-group"]
    timing_baseline = reports["timing-baseline"]
    timing_group = reports["timing-cell-group"]

    for policy, diagnostic, timing in (
        ("baseline", diagnostic_baseline, timing_baseline),
        ("cell-group", diagnostic_group, timing_group),
    ):
        for key in (
            "gem5_sha256",
            "native_batch_sha256",
            "native_header_sha256",
            "source_sha256",
        ):
            if diagnostic[key] != timing[key]:
                raise RuntimeError(
                    f"step {step} {policy} diagnostic/timing {key} mismatch"
                )
        for key in (
            "engine_cycles",
            "descriptor_cycles",
            "physical_atomic_updates",
            "physical_line_reads",
            "combiner_hits",
        ):
            if diagnostic["metrics"][key] != timing["metrics"][key]:
                raise RuntimeError(
                    f"step {step} {policy} diagnostics changed {key}"
                )

    group_metrics = timing_group["metrics"]
    baseline_metrics = timing_baseline["metrics"]
    if group_metrics["physical_atomic_updates"] != predicted_atomics:
        raise RuntimeError(
            f"step {step} grouped atomics disagree with pre-run prediction"
        )
    if group_metrics["cell_group_complete_drains"] != predicted_atomics:
        raise RuntimeError(f"step {step} grouped drain accounting did not close")
    if group_metrics["cell_group_forced_drains"] != 0:
        raise RuntimeError(f"step {step} unexpectedly forced a cell-group drain")

    baseline_numerics = diagnostic_baseline["metrics"][
        "native_tally_diagnostics"
    ]
    group_numerics = diagnostic_group["metrics"]["native_tally_diagnostics"]
    return {
        "timestep": step,
        "predicted_cell_group_physical_atomics": predicted_atomics,
        "baseline": {
            "cpu_cycles": baseline_metrics["cpu_cycles"],
            "cpu_committed_instructions": baseline_metrics[
                "cpu_committed_instructions"
            ],
            "engine_cycles": baseline_metrics["engine_cycles"],
            "descriptor_cycles": baseline_metrics["descriptor_cycles"],
            "physical_atomic_updates": baseline_metrics[
                "physical_atomic_updates"
            ],
            "physical_line_reads": baseline_metrics["physical_line_reads"],
            "combiner_hits": baseline_metrics["combiner_hits"],
            "bit_mismatch_count": baseline_numerics["bit_mismatch_count"],
            "max_ulp_distance": baseline_numerics["max_ulp_distance"],
            "max_relative_error": baseline_numerics["max_relative_error"],
        },
        "cell_group": {
            "cpu_cycles": group_metrics["cpu_cycles"],
            "cpu_committed_instructions": group_metrics[
                "cpu_committed_instructions"
            ],
            "engine_cycles": group_metrics["engine_cycles"],
            "descriptor_cycles": group_metrics["descriptor_cycles"],
            "physical_atomic_updates": group_metrics[
                "physical_atomic_updates"
            ],
            "physical_line_reads": group_metrics["physical_line_reads"],
            "combiner_hits": group_metrics["combiner_hits"],
            "cell_group_drain_deferrals": group_metrics[
                "cell_group_drain_deferrals"
            ],
            "bit_mismatch_count": group_numerics["bit_mismatch_count"],
            "max_ulp_distance": group_numerics["max_ulp_distance"],
            "max_relative_error": group_numerics["max_relative_error"],
        },
        "reductions_percent": {
            "cpu_cycles": reduction_percent(
                baseline_metrics["cpu_cycles"], group_metrics["cpu_cycles"]
            ),
            "cpu_committed_instructions": reduction_percent(
                baseline_metrics["cpu_committed_instructions"],
                group_metrics["cpu_committed_instructions"],
            ),
            "engine_cycles": reduction_percent(
                baseline_metrics["engine_cycles"],
                group_metrics["engine_cycles"],
            ),
            "descriptor_cycles": reduction_percent(
                baseline_metrics["descriptor_cycles"],
                group_metrics["descriptor_cycles"],
            ),
            "physical_atomic_updates": reduction_percent(
                baseline_metrics["physical_atomic_updates"],
                group_metrics["physical_atomic_updates"],
            ),
            "physical_line_reads": reduction_percent(
                baseline_metrics["physical_line_reads"],
                group_metrics["physical_line_reads"],
            ),
        },
    }


def main():
    here = pathlib.Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", required=True, type=pathlib.Path)
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--outdir", required=True, type=pathlib.Path)
    parser.add_argument(
        "--runner",
        type=pathlib.Path,
        default=here / "run_sparta_tally_cpu_smoke.py",
    )
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()

    selection_path = args.selection.resolve(strict=True)
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    if selection.get("schema") != "lanl-maa-sparta-native-series-selection-v1" or \
            selection.get("status") != "validated":
        parser.error("selection is not a validated native SPARTA series")
    selected = selection.get("selected")
    if not isinstance(selected, list) or len(selected) < 2:
        parser.error("selection must contain at least two representatives")

    gem5 = args.gem5.resolve(strict=True)
    runner = args.runner.resolve(strict=True)
    outdir = args.outdir.resolve()
    if outdir.exists():
        raise RuntimeError(f"refusing to reuse campaign directory: {outdir}")
    outdir.mkdir(parents=True)
    campaign_path = outdir / "campaign_report.json"
    campaign = {
        "schema": "lanl-maa-sparta-native-series-replay-v1",
        "status": "running",
        "selection_path": str(selection_path),
        "selection_sha256": file_sha256(selection_path),
        "gem5_path": str(gem5),
        "gem5_sha256": file_sha256(gem5),
        "runner_path": str(runner),
        "runner_sha256": file_sha256(runner),
        "claim_boundary": (
            "Selected lightweight native-batch replays only; not SPARTA "
            "application speedup, native-scale amortization, or physical cost."
        ),
        "commands": [],
    }
    campaign_path.write_text(json.dumps(campaign, indent=2) + "\n")
    batch_by_step = {
        item["timestep"]: item for item in selection["batches"]
    }
    raw_reports = {}
    try:
        for selected_item in selected:
            timestep = selected_item["timestep"]
            batch = pathlib.Path(selected_item["path"]).resolve(strict=True)
            if file_sha256(batch) != selected_item["artifact_sha256"]:
                raise RuntimeError(f"selected batch hash changed: {batch}")
            raw_reports[timestep] = {}
            for timing_clean in (False, True):
                for cell_group in (False, True):
                    case = (
                        ("timing" if timing_clean else "diagnostic")
                        + ("-cell-group" if cell_group else "-baseline")
                    )
                    case_dir = outdir / f"step_{timestep:012d}" / case
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
                        "--timeout-seconds",
                        str(args.timeout_seconds),
                    ]
                    if timing_clean:
                        command.append("--native-timing-clean")
                    if cell_group:
                        command.append("--sparta-cell-group")
                    campaign["commands"].append(command)
                    campaign_path.write_text(
                        json.dumps(campaign, indent=2) + "\n", encoding="utf-8"
                    )
                    subprocess.run(command, check=True)
                    report_path = case_dir / "report.json"
                    raw_reports[timestep][case] = read_case(
                        report_path,
                        timestep,
                        cell_group,
                        timing_clean,
                        selected_item["artifact_sha256"],
                    )

        campaign["steps"] = []
        for selected_item in selected:
            timestep = selected_item["timestep"]
            summary = summarize_step(
                timestep,
                raw_reports[timestep],
                batch_by_step[timestep][
                    "predicted_cell_group_physical_atomics"
                ],
            )
            summary["selection_reasons"] = selected_item["reasons"]
            summary["artifact_sha256"] = selected_item["artifact_sha256"]
            summary["raw_report_sha256"] = {
                case: file_sha256(
                    outdir / f"step_{timestep:012d}" / case / "report.json"
                )
                for case in raw_reports[timestep]
            }
            campaign["steps"].append(summary)
        campaign["status"] = "validated"
    except Exception as error:
        campaign["status"] = "failed"
        campaign["error"] = str(error)
        raise
    finally:
        campaign_path.write_text(
            json.dumps(campaign, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
