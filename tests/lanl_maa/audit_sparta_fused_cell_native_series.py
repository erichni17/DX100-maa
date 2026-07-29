#!/usr/bin/env python3
"""Audit a complete native-record SPARTA collision series."""

import argparse
import json
import pathlib

from run_sparta_fused_cell_native_batch import (
    file_sha256,
    validate_batch,
)

EXPECTED_TIMESTEPS = list(range(0, 65, 4))
PATTERN = "thermal_grid_step_*.json"


def audit_series(raw_directory, legacy_directory):
    raw_paths = sorted(raw_directory.glob(PATTERN))
    legacy_paths = sorted(legacy_directory.glob(PATTERN))
    if len(raw_paths) != len(EXPECTED_TIMESTEPS):
        raise ValueError("raw series does not contain exactly 17 batches")
    if [path.name for path in raw_paths] != [
        path.name for path in legacy_paths
    ]:
        raise ValueError("raw and legacy series filenames do not match")

    rows = []
    for raw_path, legacy_path in zip(raw_paths, legacy_paths):
        raw_document = json.loads(raw_path.read_text(encoding="utf-8"))
        legacy_document = json.loads(legacy_path.read_text(encoding="utf-8"))
        stripped = dict(raw_document)
        stripped.pop("native_record_extension", None)
        if stripped != legacy_document:
            raise ValueError(
                f"native extension changed legacy object: {raw_path.name}"
            )
        validated = validate_batch(raw_document)
        nonempty_cells = sum(cell["count"] > 0 for cell in validated["cells"])
        rows.append(
            {
                "timestep": raw_document["timestep"],
                "raw_path": str(raw_path.resolve()),
                "raw_sha256": file_sha256(raw_path),
                "legacy_path": str(legacy_path.resolve()),
                "legacy_sha256": file_sha256(legacy_path),
                "legacy_object_exact": True,
                "native_membership_exact_cover": True,
                "native_fields_reproduce_contribution_bits": True,
                "particle_count": raw_document["native_particle_count"],
                "eligible_particle_count": len(validated["selected"]),
                "cell_count": raw_document["cell_count"],
                "nonempty_cells": nonempty_cells,
                "fused_coherent_write_projection": nonempty_cells * 6,
            }
        )

    if [row["timestep"] for row in rows] != EXPECTED_TIMESTEPS:
        raise ValueError(
            "series timesteps are not 0..64 in increments of four"
        )
    return {
        "schema": "lanl-maa-sparta-fused-cell-native-series-audit-v1",
        "status": "validated",
        "raw_directory": str(raw_directory.resolve()),
        "legacy_directory": str(legacy_directory.resolve()),
        "batch_count": len(rows),
        "all_legacy_objects_exact": True,
        "all_native_memberships_exact_cover": True,
        "all_native_fields_reproduce_contribution_bits": True,
        "rows": rows,
        "claim_boundary": (
            "Native-record structure, eligibility, contribution, and legacy "
            "compatibility evidence only; coherent-write counts are static "
            "projections. No C++ execution, gem5 timing, physical cost, or "
            "speedup is claimed."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-directory", required=True, type=pathlib.Path)
    parser.add_argument("--legacy-directory", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    arguments = parser.parse_args()
    raw_directory = arguments.raw_directory.resolve(strict=True)
    legacy_directory = arguments.legacy_directory.resolve(strict=True)
    output = arguments.output.resolve()
    if output.exists():
        raise RuntimeError(f"refusing to overwrite output: {output}")
    report = audit_series(raw_directory, legacy_directory)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
