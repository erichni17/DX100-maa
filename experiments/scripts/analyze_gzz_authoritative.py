#!/usr/bin/env python3
"""Validate the matched, seven-point authoritative GZZ tile cohort."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

TILES = (1024, 2048, 4096, 8192, 16384, 32768, 65536)
EXPECTED_HASH = "9234467062988358067"
EXPECTED_REFERENCE = (
    "UME_REFERENCE_PASS volume_errors=0 gradient_errors=0 elements=1180000"
)


def key_values(path: Path, separator: str = "=") -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(errors="replace").splitlines():
        key, found, value = line.partition(separator)
        if found:
            values[key] = value
    return values


def first_stat(path: Path, name: str) -> int | None:
    if not path.is_file():
        return None
    in_window = False
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("---------- Begin Simulation Statistics"):
            in_window = True
            continue
        if in_window and line.startswith(
            "---------- End Simulation Statistics"
        ):
            break
        fields = line.split()
        if in_window and len(fields) >= 2 and fields[0] == name:
            try:
                return int(float(fields[1]))
            except ValueError:
                return None
    return None


def latest_result(point: Path) -> dict[str, str]:
    paths = sorted(point.glob("results*.tsv"))
    rows: list[dict[str, str]] = []
    for path in paths:
        with path.open(newline="") as handle:
            rows.extend(csv.DictReader(handle, delimiter="\t"))
    return rows[-1] if rows else {}


def collect(run_root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for tile in TILES:
        point = run_root / f"t{tile}"
        result = latest_result(point)
        treatment = key_values(point / "treatment.txt")
        outdir = Path(result["outdir"]) if result.get("outdir") else None
        log_path = outdir / "run.log" if outdir else Path()
        stats_path = outdir / "stats.txt" if outdir else Path()
        log = (
            log_path.read_text(errors="replace") if log_path.is_file() else ""
        )
        sim_ticks = first_stat(stats_path, "simTicks")
        sim_insts = first_stat(stats_path, "simInsts")
        benchmark = (
            key_values(outdir / "benchmark_provenance.tsv", "\t")
            if outdir
            else {}
        )
        gem5 = (
            key_values(outdir / "gem5_provenance.tsv", "\t") if outdir else {}
        )
        hashes = re.findall(
            r"^UME_OUTPUT_FP output_hash=(\d+) nonfinite=0$", log, re.MULTILINE
        )
        output_hash = hashes[-1] if hashes else result.get("output_hash", "")
        reasons: list[str] = []
        if result.get("rc") != "0":
            reasons.append("wrapper rc is not 0")
        if not sim_ticks:
            reasons.append("first-ROI simTicks missing")
        if "m5_exit instruction encountered" not in log:
            reasons.append("clean m5_exit missing")
        if EXPECTED_REFERENCE not in log:
            reasons.append("exact scalar reference missing")
        if output_hash != EXPECTED_HASH:
            reasons.append("output fingerprint mismatch")
        if re.search(r"UME_.*_FAIL|panic:|fatal:", log):
            reasons.append("failure marker present")
        benchmark_sha = benchmark.get("sha256", "")
        if not benchmark_sha or benchmark_sha != treatment.get(
            "benchmark_sha256"
        ):
            reasons.append("benchmark SHA binding mismatch")
        checkpoint = benchmark.get("checkpoint", "")
        if benchmark_sha and f"_binsha_{benchmark_sha}" not in checkpoint:
            reasons.append("checkpoint is not keyed by benchmark SHA")
        gem5_sha = gem5.get("sha256", "")
        if not gem5_sha or gem5_sha != result.get("gem5_sha256"):
            reasons.append("gem5 SHA binding mismatch")
        rows.append(
            {
                "tile": tile,
                "tile_label": f"{tile // 1024}K",
                "status": "valid"
                if not reasons
                else ("pending" if not result else "invalid"),
                "notes": "; ".join(reasons),
                "rc": result.get("rc", ""),
                "simTicks": sim_ticks or "",
                "simInsts": sim_insts or "",
                "performance_16k": "",
                "output_hash": output_hash,
                "source_commit": treatment.get("source_commit", ""),
                "benchmark_sha256": benchmark_sha,
                "checkpoint": checkpoint,
                "gem5_sha256": gem5_sha,
                "outdir": str(outdir) if outdir else "",
            }
        )
    base = next(
        (
            int(row["simTicks"])
            for row in rows
            if row["tile"] == 16384 and row["status"] == "valid"
        ),
        None,
    )
    if base:
        for row in rows:
            if row["status"] == "valid":
                row["performance_16k"] = base / int(row["simTicks"])
    return rows


def validate_cohort(rows: list[dict[str, object]]) -> list[str]:
    issues: list[str] = []
    if any(row["status"] != "valid" for row in rows):
        issues.append("not all seven GZZ points are valid")
    for field in ("source_commit", "gem5_sha256"):
        values = {str(row[field]) for row in rows if row[field]}
        if len(values) != 1:
            issues.append(f"cohort does not share one {field}")
    if any(not row["benchmark_sha256"] for row in rows):
        issues.append("one or more benchmark binaries lack SHA binding")
    return issues


def write_outputs(
    run_root: Path, rows: list[dict[str, object]], issues: list[str]
) -> None:
    fields = list(rows[0])
    with (run_root / "gzz_authoritative.tsv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    payload = {"complete": not issues, "issues": issues, "rows": rows}
    (run_root / "gzz_authoritative.json").write_text(
        json.dumps(payload, indent=2) + "\n"
    )
    lines = [
        "# Authoritative GZZ tile sweep",
        "",
        f"Status: **{'complete' if not issues else 'incomplete'}**.",
        "",
        "| Tile | Status | simTicks | Performance vs. 16K |",
        "|---:|---|---:|---:|",
    ]
    for row in rows:
        performance = row["performance_16k"]
        rendered = (
            f"{performance:.3f}" if isinstance(performance, float) else ""
        )
        lines.append(
            f"| {row['tile_label']} | {row['status']} | {row['simTicks']} | {rendered} |"
        )
    if issues:
        lines.extend(["", "Issues:", *[f"- {issue}" for issue in issues]])
    (run_root / "README.md").write_text("\n".join(lines) + "\n")
    if not issues:
        result_rows = [latest_result(run_root / f"t{tile}") for tile in TILES]
        fields = list(result_rows[0])
        if any(not row or list(row) != fields for row in result_rows):
            raise ValueError("point result schemas do not match")
        destination = run_root / "promoted_results_provenance_v2.tsv"
        temporary = destination.with_name(f".{destination.name}.tmp")
        with temporary.open("w", newline="") as handle:
            writer = csv.DictWriter(
                handle, delimiter="\t", fieldnames=fields, lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(result_rows)
        temporary.replace(destination)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args()
    rows = collect(args.run_root)
    issues = validate_cohort(rows)
    write_outputs(args.run_root, rows, issues)
    print("complete" if not issues else "; ".join(issues))
    return 0 if not issues else 2


if __name__ == "__main__":
    raise SystemExit(main())
