#!/usr/bin/env python3
"""Audit source-request and retirement-write overlap in the first stats dump."""

import argparse
import json
from pathlib import Path


def parse_first_stats_section(path):
    metrics = {}
    in_first_section = False
    with path.open() as stats_file:
        for line in stats_file:
            if line.startswith("---------- Begin Simulation Statistics"):
                if in_first_section:
                    break
                in_first_section = True
                continue
            if not in_first_section or line.startswith(
                "---------- End Simulation"
            ):
                continue
            fields = line.split()
            if len(fields) >= 2:
                metrics[fields[0]] = fields[1]
    if not in_first_section:
        raise ValueError("stats file has no simulation statistics section")
    return metrics


def as_int(metrics, name):
    if name not in metrics:
        raise ValueError(f"missing required statistic: {name}")
    value = float(metrics[name])
    if not value.is_integer():
        raise ValueError(f"statistic is not an integer: {name}={value}")
    return int(value)


def audit_pipeline(metrics, unit="I0", require_overlap=False):
    prefix = f"system.maa.{unit}_IND_"
    request_cycles = as_int(metrics, prefix + "CyclesRequest")
    buckets = {
        "idle": as_int(metrics, prefix + "VirtPipelineCyclesIdle"),
        "source_only": as_int(
            metrics, prefix + "VirtPipelineCyclesSourceOnly"
        ),
        "write_only": as_int(metrics, prefix + "VirtPipelineCyclesWriteOnly"),
        "overlap": as_int(metrics, prefix + "VirtPipelineCyclesOverlap"),
    }
    issues = as_int(metrics, prefix + "VirtWriteIssues")
    completions = as_int(metrics, prefix + "VirtWriteCompletions")
    errors = []
    if any(value < 0 for value in buckets.values()):
        errors.append("pipeline bucket is negative")
    if sum(buckets.values()) != request_cycles:
        errors.append(
            "pipeline buckets do not partition request cycles: "
            f"{sum(buckets.values())} != {request_cycles}"
        )
    if issues != completions:
        errors.append(
            f"virtual writes do not balance: {issues} != {completions}"
        )
    if require_overlap and buckets["overlap"] == 0:
        errors.append("source requests and retirement writes never overlap")
    return {
        "valid": not errors,
        "unit": unit,
        "request_cycles": request_cycles,
        "pipeline_cycles": buckets,
        "write_issues": issues,
        "write_completions": completions,
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stats", type=Path)
    parser.add_argument("--unit", default="I0")
    parser.add_argument("--require-overlap", action="store_true")
    args = parser.parse_args()

    try:
        result = audit_pipeline(
            parse_first_stats_section(args.stats),
            unit=args.unit,
            require_overlap=args.require_overlap,
        )
    except (OSError, ValueError) as error:
        result = {"valid": False, "errors": [str(error)]}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
