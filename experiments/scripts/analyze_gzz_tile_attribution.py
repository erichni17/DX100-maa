#!/usr/bin/env python3
"""Validate and summarize the controlled GZZ tile-size attribution campaign."""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from pathlib import Path

EXPECTED_HASH = "9234467062988358067"
EXPECTED_REFERENCE = (
    "UME_REFERENCE_PASS volume_errors=0 gradient_errors=0 elements=1180000"
)
EXPECTED_COHORTS = (
    ("native_p16384", 16384, 16384, "native"),
    ("native_p32768", 32768, 32768, "native"),
    ("native_p65536", 65536, 65536, "native"),
    ("logical_p16384_l16384", 16384, 16384, "logical16"),
    ("logical_p32768_l16384", 32768, 16384, "logical16"),
    ("logical_p65536_l16384", 65536, 16384, "logical16"),
)
STAT_KEYS = (
    "simTicks",
    "simInsts",
    "system.maa.numInst",
    "system.maa.numInst_INDRD",
    "system.maa.numInst_INDRMW",
    "system.maa.numInst_STRRD",
    "system.maa.numInst_ALUS",
    "system.maa.numInst_INV",
    "system.maa.cycles_TOTAL",
    "system.maa.cycles_BUSY",
    "system.maa.cycles_IDLE",
)


def first_stats(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    in_first = False
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("---------- Begin Simulation Statistics"):
            if in_first:
                break
            in_first = True
            continue
        if in_first and line.startswith(
            "---------- End Simulation Statistics"
        ):
            break
        if not in_first:
            continue
        fields = line.split()
        if len(fields) >= 2 and fields[0] in STAT_KEYS:
            try:
                values[fields[0]] = int(float(fields[1]))
            except ValueError:
                pass
    return values


def parse_results(cohort: Path) -> dict[str, str]:
    result_files = sorted(cohort.glob("results*.tsv"))
    if not result_files:
        return {}
    rows: list[dict[str, str]] = []
    for result_file in result_files:
        with result_file.open(newline="") as handle:
            rows.extend(csv.DictReader(handle, delimiter="\t"))
    return rows[-1] if rows else {}


def locate_outdir(cohort: Path, result: dict[str, str]) -> Path | None:
    recorded = result.get("outdir")
    if recorded:
        candidate = Path(recorded)
        if candidate.is_dir():
            return candidate
    candidates = sorted(cohort.glob("gradzatz_n1000000_t*_m2GB_*"))
    return candidates[-1] if candidates else None


def trace_metrics(path: Path) -> dict[str, int]:
    request_ticks: list[int] = []
    dispatch_failures = 0
    for line in path.read_text(errors="replace").splitlines():
        if "failed to dipatch!" in line:
            dispatch_failures += 1
        if "recvTimingReq: INSTR[" not in line:
            continue
        tick, separator, _ = line.partition(":")
        if separator and tick.isdigit():
            request_ticks.append(int(tick))
    gaps = [
        right - left for left, right in zip(request_ticks, request_ticks[1:])
    ]
    metrics = {
        "trace_requests": len(request_ticks),
        "trace_dispatch_failures": dispatch_failures,
        "trace_span_ticks": (
            request_ticks[-1] - request_ticks[0]
            if len(request_ticks) > 1
            else 0
        ),
        "trace_interarrival_p50_ticks": int(statistics.median(gaps))
        if gaps
        else 0,
        "trace_interarrival_p95_ticks": 0,
        "trace_interarrival_max_ticks": max(gaps) if gaps else 0,
    }
    if gaps:
        ordered = sorted(gaps)
        metrics["trace_interarrival_p95_ticks"] = ordered[
            min(len(ordered) - 1, int(0.95 * len(ordered)))
        ]
    return metrics


def collect(run_root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for name, physical, logical, treatment in EXPECTED_COHORTS:
        cohort = run_root / name
        result = parse_results(cohort) if cohort.is_dir() else {}
        outdir = locate_outdir(cohort, result) if cohort.is_dir() else None
        log_path = outdir / "run.log" if outdir else None
        stats_path = outdir / "stats.txt" if outdir else None
        trace_path = outdir / "maa_controller.trace" if outdir else None
        log = (
            log_path.read_text(errors="replace")
            if log_path and log_path.is_file()
            else ""
        )
        stats = (
            first_stats(stats_path)
            if stats_path and stats_path.is_file()
            else {}
        )
        rc = result.get("rc", "")
        output_hash_match = re.findall(
            r"^UME_OUTPUT_FP output_hash=(\d+) nonfinite=0$", log, re.MULTILINE
        )
        output_hash = (
            output_hash_match[-1]
            if output_hash_match
            else result.get("output_hash", "")
        )
        reasons: list[str] = []
        if rc != "0":
            reasons.append("wrapper rc is not 0")
        if not stats.get("simTicks"):
            reasons.append("first-ROI simTicks missing")
        if "m5_exit instruction encountered" not in log:
            reasons.append("clean m5_exit missing")
        if EXPECTED_REFERENCE not in log:
            reasons.append("exact scalar reference missing")
        if output_hash != EXPECTED_HASH:
            reasons.append("output fingerprint mismatch")
        if re.search(r"UME_.*_FAIL|panic:|fatal:", log):
            reasons.append("failure marker present")
        row: dict[str, object] = {
            "cohort": name,
            "treatment": treatment,
            "physical_tile": physical,
            "logical_chunk": logical,
            "status": "valid"
            if not reasons
            else ("pending" if not result else "invalid"),
            "notes": "; ".join(reasons),
            "rc": rc,
            "output_hash": output_hash,
            "outdir": str(outdir) if outdir else "",
        }
        row.update({key: stats.get(key, "") for key in STAT_KEYS})
        row.update(
            trace_metrics(trace_path)
            if trace_path and trace_path.is_file()
            else {
                "trace_requests": "",
                "trace_dispatch_failures": "",
                "trace_span_ticks": "",
                "trace_interarrival_p50_ticks": "",
                "trace_interarrival_p95_ticks": "",
                "trace_interarrival_max_ticks": "",
            }
        )
        rows.append(row)
    return rows


def add_comparisons(rows: list[dict[str, object]]) -> dict[str, object]:
    valid = {
        (str(row["treatment"]), int(row["physical_tile"])): row
        for row in rows
        if row["status"] == "valid"
    }
    comparisons: dict[str, object] = {}
    for metric in (
        "simTicks",
        "simInsts",
        "system.maa.cycles_IDLE",
        "system.maa.cycles_BUSY",
    ):
        metric_result: dict[str, object] = {}
        for physical in (32768, 65536):
            keys = (
                ("native", 16384),
                ("native", physical),
                ("logical16", 16384),
                ("logical16", physical),
            )
            if not all(
                key in valid and valid[key].get(metric) != "" for key in keys
            ):
                continue
            native_base = int(valid[("native", 16384)][metric])
            native_large = int(valid[("native", physical)][metric])
            logical_base = int(valid[("logical16", 16384)][metric])
            logical_large = int(valid[("logical16", physical)][metric])
            native_excess = native_large - native_base
            logical_excess = logical_large - logical_base
            recovery = None
            if native_excess > 0:
                recovery = 1.0 - logical_excess / native_excess
            metric_result[str(physical)] = {
                "native_excess": native_excess,
                "logical16_excess": logical_excess,
                "fraction_recovered": recovery,
            }
        comparisons[metric] = metric_result
    ticks = comparisons.get("simTicks", {})
    complete = len(valid) == len(EXPECTED_COHORTS)
    recoveries = [
        item.get("fraction_recovered")
        for item in ticks.values()
        if item.get("fraction_recovered") is not None
    ]
    if complete and len(recoveries) == 2 and min(recoveries) >= 0.75:
        verdict = (
            "logical feed granularity explains most of the 32K/64K regression"
        )
    elif complete:
        verdict = "logical feed granularity alone does not explain most of the regression"
    else:
        verdict = "pending complete valid controlled cohort"
    return {"complete": complete, "verdict": verdict, "metrics": comparisons}


def write_tsv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = [
        "cohort",
        "treatment",
        "physical_tile",
        "logical_chunk",
        "status",
        "notes",
        "rc",
        "simTicks",
        "simInsts",
        "system.maa.cycles_TOTAL",
        "system.maa.cycles_BUSY",
        "system.maa.cycles_IDLE",
        "system.maa.numInst",
        "trace_requests",
        "trace_dispatch_failures",
        "trace_span_ticks",
        "trace_interarrival_p50_ticks",
        "trace_interarrival_p95_ticks",
        "trace_interarrival_max_ticks",
        "output_hash",
        "outdir",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle, delimiter="\t", fieldnames=fields, extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(
    path: Path, rows: list[dict[str, object]], summary: dict[str, object]
) -> None:
    lines = [
        "# GZZ tile-size attribution",
        "",
        f"Verdict: **{summary['verdict']}**.",
        "",
        "| Treatment | Physical | Logical | Status | simTicks | MAA busy | MAA idle | simInsts |",
        "|---|---:|---:|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {treatment} | {physical_tile} | {logical_chunk} | {status} | {simTicks} | {busy} | {idle} | {simInsts} |".format(
                busy=row.get("system.maa.cycles_BUSY", ""),
                idle=row.get("system.maa.cycles_IDLE", ""),
                **row,
            )
        )
    lines += ["", "## Controlled loss recovery", ""]
    for physical, values in summary["metrics"].get("simTicks", {}).items():
        recovery = values["fraction_recovered"]
        lines.append(
            f"- {int(physical) // 1024}K: native excess {values['native_excess']:,} ticks; "
            f"logical-16K excess {values['logical16_excess']:,}; recovered {recovery:.1%}."
        )
    lines += [
        "",
        "Acceptance requires wrapper rc=0, first-ROI stats, clean `m5_exit`, the exact scalar-reference marker, and output hash `9234467062988358067` for every point.",
        "",
    ]
    path.write_text("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-root",
        type=Path,
        default=Path(
            "/data1/nier/dx100-runs/2026-08-03-gzz-tile-attribution-v2"
        ),
    )
    args = parser.parse_args()
    args.run_root.mkdir(parents=True, exist_ok=True)
    rows = collect(args.run_root)
    summary = add_comparisons(rows)
    write_tsv(args.run_root / "gzz_attribution.tsv", rows)
    (args.run_root / "gzz_attribution.json").write_text(
        json.dumps({"rows": rows, "summary": summary}, indent=2) + "\n"
    )
    write_markdown(args.run_root / "gzz_attribution.md", rows, summary)
    print(summary["verdict"])
    return 0 if summary["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
