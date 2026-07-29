#!/usr/bin/env python3
"""Validate a native SPARTA batch series and select replay representatives."""

import argparse
import hashlib
import importlib.util
import json
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
RUNNER_PATH = HERE / "run_sparta_tally_cpu_smoke.py"
SPEC = importlib.util.spec_from_file_location(
    "sparta_tally_runner", RUNNER_PATH
)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)
NAME = re.compile(r"^thermal_grid_step_([0-9]{12})\.json$")


def summarize_batch(path, batch):
    occupancies = [0] * batch["cell_count"]
    segments = 0
    previous = None
    for item, cell in enumerate(batch["indices"]):
        occupancies[cell] += 1
        if item % 4 == 0 or cell != previous:
            segments += 1
        previous = cell
    populated = [value for value in occupancies if value]
    contribution_fingerprint = hashlib.sha256(
        "".join(batch["contribution_bits"]).encode("ascii")
    ).hexdigest()
    grouped_updates = segments * 6
    return {
        "timestep": batch["timestep"],
        "path": str(path.resolve()),
        "artifact_sha256": RUNNER.file_sha256(path),
        "contribution_fingerprint_sha256": contribution_fingerprint,
        "cell_count": batch["cell_count"],
        "populated_cells": len(populated),
        "empty_cells": batch["cell_count"] - len(populated),
        "minimum_nonzero_occupancy": min(populated),
        "maximum_occupancy": max(populated),
        "occupancy_sum_squares": sum(value * value for value in occupancies),
        "occupancies": occupancies,
        "four_item_cell_segments": segments,
        "predicted_cell_group_physical_atomics": grouped_updates,
        "predicted_cell_group_combiner_hits": 384 - grouped_updates,
    }


def select_representatives(summaries, count=5):
    selected = {}

    def add(summary, reason):
        selected.setdefault(summary["timestep"], []).append(reason)

    ordered = sorted(summaries, key=lambda item: item["timestep"])
    add(ordered[0], "first-timestep")
    add(ordered[-1], "last-timestep")
    add(
        min(
            ordered,
            key=lambda item: (
                item["predicted_cell_group_physical_atomics"],
                item["timestep"],
            ),
        ),
        "minimum-predicted-group-atomics",
    )
    add(
        max(
            ordered,
            key=lambda item: (
                item["predicted_cell_group_physical_atomics"],
                -item["timestep"],
            ),
        ),
        "maximum-predicted-group-atomics",
    )
    add(
        max(
            ordered,
            key=lambda item: (
                item["maximum_occupancy"],
                item["occupancy_sum_squares"],
                -item["timestep"],
            ),
        ),
        "maximum-native-occupancy-skew",
    )

    count = min(count, len(ordered))
    while len(selected) < count:
        unselected = [
            item for item in ordered if item["timestep"] not in selected
        ]
        candidate = max(
            unselected,
            key=lambda item: (
                min(abs(item["timestep"] - timestep) for timestep in selected),
                -item["timestep"],
            ),
        )
        add(candidate, "maximum-temporal-distance-fill")

    by_timestep = {item["timestep"]: item for item in ordered}
    return [
        {
            "timestep": timestep,
            "path": by_timestep[timestep]["path"],
            "artifact_sha256": by_timestep[timestep]["artifact_sha256"],
            "reasons": reasons,
        }
        for timestep, reasons in sorted(selected.items())
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-dir", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--representatives", type=int, default=5)
    args = parser.parse_args()
    if args.representatives < 2:
        parser.error("--representatives must be at least two")
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError(f"refusing to overwrite series report: {output}")
    batch_dir = args.batch_dir.resolve(strict=True)
    paths = sorted(batch_dir.glob("thermal_grid_step_*.json"))
    if len(paths) < 2:
        raise RuntimeError(
            "native series requires at least two batch artifacts"
        )

    summaries = []
    timesteps = set()
    cell_count = None
    for path in paths:
        match = NAME.fullmatch(path.name)
        if match is None:
            raise RuntimeError(
                f"noncanonical native batch filename: {path.name}"
            )
        batch = RUNNER.load_native_batch(path)
        filename_timestep = int(match.group(1))
        if batch["timestep"] != filename_timestep:
            raise RuntimeError("native batch filename and timestep disagree")
        if batch["timestep"] in timesteps:
            raise RuntimeError("native batch series repeats a timestep")
        timesteps.add(batch["timestep"])
        if cell_count is None:
            cell_count = batch["cell_count"]
        elif batch["cell_count"] != cell_count:
            raise RuntimeError("native batch series changes cell count")
        summaries.append(summarize_batch(path, batch))

    summaries.sort(key=lambda item: item["timestep"])
    report = {
        "schema": "lanl-maa-sparta-native-series-selection-v1",
        "status": "validated",
        "batch_directory": str(batch_dir),
        "batch_count": len(summaries),
        "timesteps": [item["timestep"] for item in summaries],
        "cell_count": cell_count,
        "selection_policy": [
            "first and last timestep",
            "minimum and maximum predicted tagged-cell-group atomics",
            "maximum native occupancy skew",
            "maximum temporal distance until the requested count is met",
        ],
        "batches": summaries,
        "selected": select_representatives(
            summaries, min(args.representatives, len(summaries))
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
