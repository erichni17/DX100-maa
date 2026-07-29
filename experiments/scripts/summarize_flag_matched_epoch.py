#!/usr/bin/env python3
"""Separate FLAG Offset scheduling-epoch and storage-capacity effects."""

import argparse
import csv
import json
import math
from pathlib import Path

import summarize_flag_descriptor_capacity as descriptor
import summarize_flag_offset_epoch as epoch


EXPECTED_CASES = 14


def fail(message: str) -> None:
    raise SystemExit(f"FLAG matched-epoch comparison failed: {message}")


def geometric_mean(values: list[float]) -> float:
    if not values or any(value <= 0 for value in values):
        fail("geometric mean requires positive observations")
    return math.exp(sum(math.log(value) for value in values) / len(values))


def write_tsv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0], delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--epoch16-runner-commit", required=True)
    parser.add_argument("--cap16-runner-commit", required=True)
    parser.add_argument("--cap4-runner-commit", required=True)
    parser.add_argument("--simulator-sha256", required=True)
    args = parser.parse_args()

    campaign = args.campaign.resolve()
    cases_root = campaign / "cases"
    if not cases_root.is_dir():
        fail(f"missing cases directory: {cases_root}")
    case_dirs = sorted(path for path in cases_root.iterdir() if path.is_dir())
    if len(case_dirs) != EXPECTED_CASES:
        fail(f"expected {EXPECTED_CASES} cases, found {len(case_dirs)}")

    expected_diff = [
        (
            "system.maa",
            "num_offset_table_entries",
            "16384",
            "4096",
        )
    ]
    expected_schedule_diff = [
        (
            "system.maa",
            "num_offset_table_epoch_entries",
            "16384",
            "4096",
        )
    ]
    rows: list[dict[str, object]] = []
    for case_dir in case_dirs:
        epoch16_path = case_dir / "epoch16"
        cap16_path = case_dir / "cap16"
        cap4_path = case_dir / "cap4"
        cap16_result_path = cap16_path / "result.tsv"
        cap16_result = descriptor.read_one_row_tsv(cap16_result_path)
        output_hash = cap16_result.get("output_hash")
        if not output_hash:
            fail(f"missing output hash in {cap16_result_path}")
        epoch16 = epoch.validate_case(
            "epoch16_cap16",
            epoch16_path,
            args.source_commit,
            args.epoch16_runner_commit,
            args.simulator_sha256,
            output_hash,
        )
        cap16 = epoch.validate_case(
            "epoch4_cap16",
            cap16_path,
            args.source_commit,
            args.cap16_runner_commit,
            args.simulator_sha256,
            output_hash,
        )
        cap4 = epoch.validate_case(
            "epoch4_cap4",
            cap4_path,
            args.source_commit,
            args.cap4_runner_commit,
            args.simulator_sha256,
            output_hash,
        )
        differences = epoch.config_differences(
            cap16_path / "run/config.ini", cap4_path / "run/config.ini"
        )
        if differences != expected_diff:
            fail(f"unexpected treatment for {case_dir.name}: {differences}")
        schedule_differences = epoch.config_differences(
            epoch16_path / "run/config.ini", cap16_path / "run/config.ini"
        )
        if schedule_differences != expected_schedule_diff:
            fail(
                f"unexpected scheduling treatment for {case_dir.name}: "
                f"{schedule_differences}"
            )
        identities = {
            (record["input_hash"], record["guest_hash"], record["simulator_hash"])
            for record in (epoch16, cap16, cap4)
        }
        if len(identities) != 1:
            fail(f"artifact identity differs for {case_dir.name}")

        rows.append(
            {
                "id": case_dir.name,
                "output_hash": output_hash,
                "epoch16_ticks": epoch16["ticks"],
                "cap16_ticks": cap16["ticks"],
                "cap4_ticks": cap4["ticks"],
                "schedule_latency_ratio": (
                    cap16["ticks"] / epoch16["ticks"]
                ),
                "latency_ratio": cap4["ticks"] / cap16["ticks"],
                "combined_latency_ratio": (
                    cap4["ticks"] / epoch16["ticks"]
                ),
                "epoch16_writes": epoch16["write_issues"],
                "cap16_writes": cap16["write_issues"],
                "cap4_writes": cap4["write_issues"],
                "schedule_write_ratio": (
                    cap16["write_issues"] / epoch16["write_issues"]
                ),
                "write_ratio": cap4["write_issues"] / cap16["write_issues"],
                "cap16_epoch_drains": cap16["epoch_drains"],
                "cap4_epoch_drains": cap4["epoch_drains"],
                "cap16_table_full": cap16["table_full_events"],
                "cap4_table_full": cap4["table_full_events"],
                "cap16_source_requests": cap16["source_requests"],
                "cap4_source_requests": cap4["source_requests"],
                "issue_digest_identical": (
                    cap16["issue_digest_sha256"]
                    == cap4["issue_digest_sha256"]
                ),
                "dram_reads_identical": (
                    cap16["dram_reads"] == cap4["dram_reads"]
                ),
                "dram_activates_identical": (
                    cap16["dram_activates"] == cap4["dram_activates"]
                ),
                "dram_precharges_identical": (
                    cap16["dram_precharges"] == cap4["dram_precharges"]
                ),
            }
        )

    latency_ratios = [float(row["latency_ratio"]) for row in rows]
    schedule_latency_ratios = [
        float(row["schedule_latency_ratio"]) for row in rows
    ]
    combined_latency_ratios = [
        float(row["combined_latency_ratio"]) for row in rows
    ]
    write_ratios = [float(row["write_ratio"]) for row in rows]
    schedule_write_ratios = [
        float(row["schedule_write_ratio"]) for row in rows
    ]
    exact_behavior_cases = sum(
        bool(row["issue_digest_identical"])
        and bool(row["dram_reads_identical"])
        and bool(row["dram_activates_identical"])
        and bool(row["dram_precharges_identical"])
        and float(row["latency_ratio"]) == 1.0
        and float(row["write_ratio"]) == 1.0
        for row in rows
    )
    summary = {
        "cases": len(rows),
        "schedule_latency_geomean_ratio": geometric_mean(
            schedule_latency_ratios
        ),
        "latency_geomean_ratio": geometric_mean(latency_ratios),
        "latency_minimum_ratio": min(latency_ratios),
        "latency_maximum_ratio": max(latency_ratios),
        "write_geomean_ratio": geometric_mean(write_ratios),
        "schedule_write_geomean_ratio": geometric_mean(
            schedule_write_ratios
        ),
        "combined_latency_geomean_ratio": geometric_mean(
            combined_latency_ratios
        ),
        "exact_behavior_cases": exact_behavior_cases,
    }
    report = {
        "campaign": str(campaign),
        "claim_scope": (
            "FLAG direct gathers only; 4K physical SPD, 4K Row capacity, "
            "and 4K Offset scheduling epoch are fixed while Offset storage "
            "changes from 16K to 4K entries"
        ),
        "simulator_source_commit": args.source_commit,
        "runner_source_commits": {
            "epoch16": args.epoch16_runner_commit,
            "cap16": args.cap16_runner_commit,
            "cap4": args.cap4_runner_commit,
        },
        "simulator_sha256": args.simulator_sha256,
        "config_treatments": {
            "schedule_epoch": expected_schedule_diff,
            "storage_capacity": expected_diff,
        },
        "configurations": rows,
        "summary": summary,
    }

    output = args.output_dir.resolve()
    if output.exists():
        fail(f"refusing to overwrite output: {output}")
    output.mkdir(parents=True)
    write_tsv(output / "matched_epoch.tsv", rows)
    (output / "matched_epoch.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# FLAG Matched-Epoch Offset Capacity",
        "",
        "The schedule comparison changes only the Offset epoch from 16K to "
        "4K at 16K storage. The storage comparison then changes only Offset "
        "capacity from 16K to 4K at a fixed 4K epoch. Positive deltas mean "
        "the treatment is slower or issues more writes.",
        "",
        "| Configuration | 4K-epoch tick delta | 4K-capacity tick delta | "
        "4K-capacity write delta | Matched digest |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['id']} | "
            f"{100 * (float(row['schedule_latency_ratio']) - 1):+.3f}% | "
            f"{100 * (float(row['latency_ratio']) - 1):+.3f}% | "
            f"{100 * (float(row['write_ratio']) - 1):+.3f}% | "
            f"{row['issue_digest_identical']} |"
        )
    lines.extend(
        [
            "",
            "## Summary",
            "",
            "- Equal-weight latency geometric-mean delta: "
            f"{100 * (summary['latency_geomean_ratio'] - 1):+.3f}%",
            "- 4K scheduling-epoch latency geometric-mean delta: "
            f"{100 * (summary['schedule_latency_geomean_ratio'] - 1):+.3f}%",
            "- Combined epoch + storage latency geometric-mean delta: "
            f"{100 * (summary['combined_latency_geomean_ratio'] - 1):+.3f}%",
            "- Equal-weight write geometric-mean delta: "
            f"{100 * (summary['write_geomean_ratio'] - 1):+.3f}%",
            "- 4K scheduling-epoch write geometric-mean delta: "
            f"{100 * (summary['schedule_write_geomean_ratio'] - 1):+.3f}%",
            "- Exact behavior matches: "
            f"{summary['exact_behavior_cases']} / {summary['cases']}",
            "",
        ]
    )
    (output / "matched_epoch.md").write_text("\n".join(lines), encoding="utf-8")
    (output / "matched_epoch.pass").touch()
    print(
        "PASS FLAG matched-epoch comparison: "
        f"{len(rows)} cases, latency "
        f"{100 * (summary['latency_geomean_ratio'] - 1):+.3f}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
