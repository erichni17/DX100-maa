#!/usr/bin/env python3
"""Fail-closed summary for the matched NAS-IS force-cache experiment."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

BEGIN_STATS = "---------- Begin Simulation Statistics ----------"
END_STATS = "---------- End Simulation Statistics   ----------"
REQUIRED_LOG_MARKERS = (
    "ROI End!!!",
    "successfull: passed verification",
    "m5_exit instruction encountered",
)


def read_manifest(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        key, value = line.split("\t", 1)
        if key in result:
            raise ValueError(f"duplicate manifest key: {key}")
        result[key] = value
    required = {
        "source_commit",
        "gem5_sha256",
        "workload_sha256",
        "frozen_input_sha256",
        "checkpoint_tick",
    }
    if missing := required - result.keys():
        raise ValueError(f"missing manifest keys: {sorted(missing)}")
    return result


def first_stats_dump(path: Path) -> str:
    text = path.read_text()
    begin = text.find(BEGIN_STATS)
    end = text.find(END_STATS, begin + len(BEGIN_STATS))
    if begin < 0 or end < 0:
        raise ValueError(f"missing complete first stats dump: {path}")
    return text[begin + len(BEGIN_STATS) : end]


def scalar(stats: str, name: str) -> int:
    matches = re.findall(rf"^{re.escape(name)}\s+(\d+)\b", stats, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError(f"expected one {name}, found {len(matches)}")
    return int(matches[0])


def sum_maa_stat(stats: str, stem: str) -> int:
    pattern = rf"^system\.l3\.{re.escape(stem)}(?:_\d+)?::maa\s+(\d+)\b"
    return sum(map(int, re.findall(pattern, stats, re.MULTILINE)))


def write_audit(log: str) -> dict[str, int | float]:
    lines = [
        line
        for line in log.splitlines()
        if line.startswith("WRITE_ADDR_AUDIT ")
    ]
    if len(lines) != 1:
        raise ValueError(f"expected one WRITE_ADDR_AUDIT, found {len(lines)}")
    fields: dict[str, int | float] = {}
    for key, value in re.findall(r"(\w+)=([0-9.]+)", lines[0]):
        fields[key] = float(value) if "." in value else int(value)
    for key in ("writes", "unique_cl", "unique_rows", "transitions"):
        if key not in fields:
            raise ValueError(f"WRITE_ADDR_AUDIT missing {key}")
    return fields


def normalized_config(path: Path) -> tuple[str, bool]:
    lines = path.read_text().splitlines()
    values = [line for line in lines if line.startswith("force_cache_access=")]
    if len(values) != 1 or values[0] not in {
        "force_cache_access=false",
        "force_cache_access=true",
    }:
        raise ValueError(f"invalid force_cache_access in {path}")
    enabled = values[0].endswith("true")
    normalized = "\n".join(
        "force_cache_access=<TREATMENT>"
        if line.startswith("force_cache_access=")
        else line
        for line in lines
    )
    return normalized, enabled


def arm(root: Path, name: str) -> dict[str, object]:
    path = root / name
    if (path / "wrapper.exit").read_text().strip() != "0":
        raise ValueError(f"{name}: nonzero wrapper exit")
    if (path / "terminal.status").read_text().strip() != "PASS":
        raise ValueError(f"{name}: missing PASS terminal status")
    log = (path / "run.log").read_text()
    for marker in REQUIRED_LOG_MARKERS:
        if log.count(marker) != 1:
            raise ValueError(f"{name}: expected one {marker!r}")
    stats = first_stats_dump(path / "stats.txt")
    config, force_cache = normalized_config(path / "config.ini")
    audit = write_audit(log)
    writes = int(audit["writes"])
    unique_lines = int(audit["unique_cl"])
    return {
        "name": name,
        "force_cache_access": force_cache,
        "normalized_config": config,
        "sim_ticks": scalar(stats, "simTicks"),
        "l3_maa_demand_hits": sum_maa_stat(stats, "demandHits"),
        "l3_maa_demand_misses": sum_maa_stat(stats, "demandMisses"),
        "global_write_audit": audit,
        "global_writes_per_unique_line": writes / unique_lines,
    }


def summarize(root: Path) -> dict[str, object]:
    manifest = read_manifest(root / "manifest.tsv")
    control = arm(root, "control")
    treatment = arm(root, "treatment")
    if control["force_cache_access"] or not treatment["force_cache_access"]:
        raise ValueError(
            "control/treatment force_cache_access polarity is wrong"
        )
    if control.pop("normalized_config") != treatment.pop("normalized_config"):
        raise ValueError("configs differ beyond force_cache_access")

    control_ticks = int(control["sim_ticks"])
    treatment_ticks = int(treatment["sim_ticks"])
    control_writes = int(control["global_write_audit"]["writes"])
    treatment_writes = int(treatment["global_write_audit"]["writes"])
    return {
        "schema": 1,
        "manifest": manifest,
        "control": control,
        "treatment": treatment,
        "comparison": {
            "treatment_latency_change_pct": (
                treatment_ticks / control_ticks - 1.0
            )
            * 100.0,
            "treatment_global_dram_write_change_pct": (
                treatment_writes / control_writes - 1.0
            )
            * 100.0,
        },
        "classification": {
            "correctness_and_comparability": "PASS",
            "force_cache_routing_activation": "PASS",
            "histogram_target_residency": "UNRESOLVED",
            "reason": (
                "WRITE_ADDR_AUDIT is global, not filtered to NAS-IS histogram "
                "target lines; latency and global writes are observations only."
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = summarize(args.run_root)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
