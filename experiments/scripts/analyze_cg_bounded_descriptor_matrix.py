#!/usr/bin/env python3
"""Validate and summarize a matched CG bounded-descriptor campaign."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


ARMS = ("native16", "native4", "bounded4_cached", "bounded4_bypass")
EXPECTED_CONFIG = {
    "native16": {
        "num_tile_elements": "16384",
        "physical_tile_elements": "16384",
        "num_row_table_rows_per_slice": "64",
        "num_offset_table_entries": "16384",
        "num_offset_table_epoch_entries": "16384",
        "virtual_index_partitions": "1",
        "virtual_index_range_passes": "false",
        "virtual_index_descriptor_spool": "false",
    },
    "native4": {
        "num_tile_elements": "4096",
        "physical_tile_elements": "4096",
        "num_row_table_rows_per_slice": "16",
        "num_offset_table_entries": "4096",
        "num_offset_table_epoch_entries": "4096",
        "virtual_index_partitions": "1",
        "virtual_index_range_passes": "false",
        "virtual_index_descriptor_spool": "false",
    },
    "bounded4_cached": {
        "num_tile_elements": "16384",
        "physical_tile_elements": "4096",
        "num_row_table_rows_per_slice": "16",
        "num_offset_table_entries": "4096",
        "num_offset_table_epoch_entries": "4096",
        "virtual_index_partitions": "64",
        "virtual_index_range_passes": "true",
        "virtual_index_descriptor_spool": "true",
        "virtual_descriptor_spool_source_bypass_cache": "false",
    },
    "bounded4_bypass": {
        "num_tile_elements": "16384",
        "physical_tile_elements": "4096",
        "num_row_table_rows_per_slice": "16",
        "num_offset_table_entries": "4096",
        "num_offset_table_epoch_entries": "4096",
        "virtual_index_partitions": "64",
        "virtual_index_range_passes": "true",
        "virtual_index_descriptor_spool": "true",
        "virtual_descriptor_spool_source_bypass_cache": "true",
    },
}

# CG changes floating-point reduction order when tile geometry changes. These
# bounds accept the observed legal drift while remaining much tighter than the
# benchmark's 1e-4 verification threshold.
RELATIVE_TOLERANCES = {
    "x_sum": 1.0e-8,
    "x_norm_sq": 1.0e-8,
    "z_sum": 1.0e-8,
    "z_norm_sq": 1.0e-8,
    "rnorm": 1.0e-3,
    "zeta": 1.0e-10,
}
FINGERPRINT_KEYS = (
    "mode",
    "elements",
    "x_raw",
    "z_raw",
    "x_q5",
    "x_q6",
    "z_q5",
    "z_q6",
    "x_sum",
    "x_norm_sq",
    "z_sum",
    "z_norm_sq",
    "rnorm",
    "zeta",
    "nonfinite_x",
    "nonfinite_z",
    "result",
)
MECHANISM_SUFFIXES = {
    "descriptor_scans": "IND_DescriptorSpoolBScans",
    "descriptor_external": "IND_DescriptorSpoolExternalDescriptors",
    "descriptor_line_writes": "IND_DescriptorSpoolLineWrites",
    "descriptor_write_bytes": "IND_DescriptorSpoolWriteBytes",
    "descriptor_line_reads": "IND_DescriptorSpoolLineReads",
    "descriptor_read_bytes": "IND_DescriptorSpoolReadBytes",
    "descriptor_read_credit_stalls": "IND_DescriptorSpoolReadCreditStalls",
    "descriptor_wait_cycles": "IND_DescriptorSpoolWithinPassDemandWaitCycles",
    "filter_words": "IND_VirtIndexFilterWords",
    "filter_cycles": "IND_VirtIndexFilterCycles",
    "filter_wait_cycles": "IND_VirtIndexFilterWaitCycles",
    "replay_passes": "IND_BoundedReplayPasses",
    "control_bytes": "IND_DescriptorSpoolControlBytes",
    "backing_bytes": "IND_DescriptorSpoolBackingBytes",
}


def fail(message: str) -> None:
    raise ValueError(message)


def read_config(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def positive_manifest_integer(
    manifest: dict[str, str], key: str, maximum: int
) -> int:
    try:
        value = int(manifest[key])
    except (KeyError, ValueError) as error:
        fail(f"manifest has invalid {key}: {error}")
    if not 1 <= value <= maximum:
        fail(f"manifest {key}={value} is outside [1,{maximum}]")
    return value


def first_stats(path: Path) -> dict[str, float]:
    section = 0
    values: dict[str, float] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("---------- Begin Simulation Statistics"):
            section += 1
            continue
        if section != 1:
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        try:
            values[fields[0]] = float(fields[1])
        except ValueError:
            continue
    return values


def parse_fingerprint(log_text: str, arm: str) -> dict[str, str]:
    lines = [line for line in log_text.splitlines() if line.startswith("CG_FINGERPRINT ")]
    if len(lines) != 1:
        fail(f"{arm}: expected one CG_FINGERPRINT, found {len(lines)}")
    values = dict(field.split("=", 1) for field in lines[0].split()[1:])
    missing = set(FINGERPRINT_KEYS) - values.keys()
    if missing:
        fail(f"{arm}: fingerprint missing {sorted(missing)}")
    if values["result"] != "PASS":
        fail(f"{arm}: fingerprint failed")
    if values["mode"] != "MAA" or values["elements"] != "150000":
        fail(f"{arm}: unexpected fingerprint problem identity")
    if values["nonfinite_x"] != "0" or values["nonfinite_z"] != "0":
        fail(f"{arm}: non-finite output")
    return values


def relative_error(value: float, reference: float) -> float:
    return abs(value - reference) / max(abs(reference), 1.0e-300)


def validate_fingerprint(arm: str, values: dict[str, str], reference: dict[str, str]) -> dict[str, float]:
    errors = {
        key: relative_error(float(values[key]), float(reference[key]))
        for key in RELATIVE_TOLERANCES
    }
    exceeded = {
        key: error
        for key, error in errors.items()
        if error > RELATIVE_TOLERANCES[key]
    }
    if exceeded:
        fail(f"{arm}: numerical drift exceeds bounds: {exceeded}")
    for key in ("x_q5", "z_q5"):
        if values[key] != reference[key]:
            fail(f"{arm}: coarse per-element fingerprint {key} differs from native4")
    return errors


def suffix_sum(stats: dict[str, float], suffix: str) -> int:
    return int(sum(value for name, value in stats.items() if name.endswith(suffix)))


def analyze(campaign: Path) -> dict:
    if not campaign.is_dir():
        fail(f"campaign is not a directory: {campaign}")
    manifest = read_config(campaign / "manifest.txt")
    expected_index_lines = positive_manifest_integer(
        manifest, "bounded_index_buffer_lines", 1024
    )
    rows: dict[str, dict] = {}
    for arm in ARMS:
        arm_dir = campaign / arm
        exit_code = int((arm_dir / "exit_code").read_text().strip())
        if exit_code != 0:
            fail(f"{arm}: simulator exit code {exit_code}")
        log_text = (arm_dir / "run.log").read_text(
            encoding="utf-8", errors="replace"
        )
        required_markers = (
            "ROI End!!!",
            "Validation started",
            "Validation ended",
            "because m5_exit instruction encountered",
        )
        for marker in required_markers:
            if log_text.count(marker) != 1:
                fail(f"{arm}: expected one {marker!r}")
        if any(token in log_text for token in ("panic:", "fatal:", "Program aborted")):
            fail(f"{arm}: fatal simulator marker")

        config = read_config(arm_dir / "run/config.ini")
        for key, expected in EXPECTED_CONFIG[arm].items():
            if config.get(key) != expected:
                fail(f"{arm}: {key}={config.get(key)!r}, expected {expected!r}")
        if config.get("virtual_index_buffer_lines") != str(
            expected_index_lines
        ):
            fail(
                f"{arm}: virtual_index_buffer_lines="
                f"{config.get('virtual_index_buffer_lines')!r}, expected "
                f"{expected_index_lines!r}"
            )

        stats = first_stats(arm_dir / "run/stats.txt")
        if stats.get("simTicks", 0) <= 0:
            fail(f"{arm}: missing positive first-section simTicks")
        mechanism = {
            field: suffix_sum(stats, suffix)
            for field, suffix in MECHANISM_SUFFIXES.items()
        }
        if arm.startswith("bounded4_"):
            for field in ("descriptor_scans", "descriptor_external", "replay_passes"):
                if mechanism[field] <= 0:
                    fail(f"{arm}: mechanism counter {field} is not positive")
        elif any(mechanism.values()):
            fail(f"{arm}: bounded mechanism counters are unexpectedly nonzero")

        rows[arm] = {
            "sim_ticks": int(stats["simTicks"]),
            "fingerprint": parse_fingerprint(log_text, arm),
            "mechanism": mechanism,
        }

    reference = rows["native4"]["fingerprint"]
    for arm in ("bounded4_cached", "bounded4_bypass"):
        rows[arm]["relative_numerical_error_vs_native4"] = validate_fingerprint(
            arm, rows[arm]["fingerprint"], reference
        )

    native16_ticks = rows["native16"]["sim_ticks"]
    native4_ticks = rows["native4"]["sim_ticks"]
    for arm, row in rows.items():
        ticks = row["sim_ticks"]
        row["latency_ratio_vs_native16"] = ticks / native16_ticks
        row["latency_ratio_vs_native4"] = ticks / native4_ticks
        row["speedup_vs_native4"] = native4_ticks / ticks

    cached = rows["bounded4_cached"]
    bypass = rows["bounded4_bypass"]
    return {
        "schema": 1,
        "campaign": str(campaign.resolve()),
        "status": "accepted",
        "bounded_index_buffer_lines": expected_index_lines,
        "correctness_contract": {
            "benchmark_fingerprint_pass": True,
            "coarse_per_element_reference": "native4 x_q5 and z_q5",
            "relative_tolerances": RELATIVE_TOLERANCES,
        },
        "rows": rows,
        "findings": {
            "native16_speedup_vs_native4": native4_ticks / native16_ticks,
            "bounded_cached_slowdown_vs_native4": cached["sim_ticks"] / native4_ticks,
            "bounded_cached_slowdown_vs_native16": cached["sim_ticks"] / native16_ticks,
            "bypass_changes_execution": not (
                cached["sim_ticks"] == bypass["sim_ticks"]
                and cached["fingerprint"] == bypass["fingerprint"]
                and cached["mechanism"] == bypass["mechanism"]
            ),
        },
    }


def write_outputs(campaign: Path, result: dict) -> None:
    (campaign / "analysis.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    columns = (
        "arm",
        "simTicks",
        "latency_vs_native16",
        "latency_vs_native4",
        "speedup_vs_native4",
        "x_q5",
        "z_q5",
        *MECHANISM_SUFFIXES.keys(),
    )
    lines = ["\t".join(columns)]
    for arm in ARMS:
        row = result["rows"][arm]
        fields = [
            arm,
            str(row["sim_ticks"]),
            f'{row["latency_ratio_vs_native16"]:.9f}',
            f'{row["latency_ratio_vs_native4"]:.9f}',
            f'{row["speedup_vs_native4"]:.9f}',
            row["fingerprint"]["x_q5"],
            row["fingerprint"]["z_q5"],
            *(str(row["mechanism"][field]) for field in MECHANISM_SUFFIXES),
        ]
        lines.append("\t".join(fields))
    (campaign / "results.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (campaign / "analysis.complete").touch()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    try:
        result = analyze(args.campaign)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.write:
        write_outputs(args.campaign, result)
    print(json.dumps(result["findings"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
