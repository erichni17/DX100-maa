#!/usr/bin/env python3
"""Create an immutable-binary retry workflow for failed GAPBS sweep tasks."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def sha256_bytes(content):
    return hashlib.sha256(content).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def select_failed_gapbs_tasks(parent_workflow, parent_state):
    tasks_by_id = {
        task["id"]: task for task in parent_workflow.get("tasks", ())
    }
    selected = []
    for task_id, record in sorted(parent_state.get("tasks", {}).items()):
        if record.get("state") != "failed" or not task_id.startswith("gapbs-"):
            continue
        if record.get("pid") is not None:
            raise ValueError(f"failed task retains a live PID: {task_id}")
        try:
            selected.append(tasks_by_id[task_id])
        except KeyError as error:
            raise ValueError(
                f"failed task is absent from parent workflow: {task_id}"
            ) from error
    if not selected:
        raise ValueError("parent state has no failed GAPBS tasks")
    return selected


def repair_task(task, source_root, runner, gem5_binary, campaign_root):
    repaired = {
        **task,
        "command": list(task["command"]),
        "env": dict(task.get("env", {})),
    }
    repaired["cwd"] = str(source_root)
    repaired["command"][0] = str(runner)
    # Progress is an optional diagnostic, never a simulation wall-clock limit.
    repaired["command"][-1] = "0"
    repaired["env"]["DX100_SOURCE_ROOT"] = str(source_root)
    repaired["env"]["DX100_GEM5_BIN"] = str(gem5_binary)
    repaired["env"]["CAMPAIGN_ROOT"] = str(campaign_root)
    return repaired


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--parent-workflow", type=Path, required=True)
    parser.add_argument("--parent-state", type=Path, required=True)
    parser.add_argument("--gem5-binary", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    parent_workflow_path = args.parent_workflow.resolve()
    parent_state_path = args.parent_state.resolve()
    gem5_binary = args.gem5_binary.resolve()
    campaign_root = args.campaign_root.resolve()
    output = args.output.resolve()
    manifest_path = args.manifest.resolve()
    runner = source_root / "benchmarks/gapbs/run_gapbs_tile_smoke.sh"

    for path, label in (
        (source_root, "source root"),
        (runner, "GAPBS runner"),
        (gem5_binary, "gem5 binary"),
    ):
        if not path.exists():
            raise SystemExit(f"{label} is missing: {path}")
    if gem5_binary.is_symlink() or not gem5_binary.is_file():
        raise SystemExit("gem5 binary must be a regular non-symlink file")

    parent_workflow_bytes = parent_workflow_path.read_bytes()
    parent_state_bytes = parent_state_path.read_bytes()
    parent_workflow = json.loads(parent_workflow_bytes)
    parent_state = json.loads(parent_state_bytes)
    selected = select_failed_gapbs_tasks(parent_workflow, parent_state)
    repaired = [
        repair_task(
            task,
            source_root,
            runner,
            gem5_binary,
            campaign_root,
        )
        for task in selected
    ]
    workflow = {"version": 1, "name": args.name, "tasks": repaired}
    atomic_json(output, workflow)

    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source_root, text=True
    ).strip()
    manifest = {
        "schema_version": 1,
        "objective": (
            "Retry only failed GAPBS tile cells with the reviewed "
            "CPU-side retry repair"
        ),
        "source_root": str(source_root),
        "source_commit": source_commit,
        "runner": str(runner.resolve()),
        "runner_sha256": sha256_file(runner),
        "gem5_binary": str(gem5_binary),
        "gem5_sha256": sha256_file(gem5_binary),
        "parent_workflow": str(parent_workflow_path),
        "parent_workflow_sha256": sha256_bytes(parent_workflow_bytes),
        "parent_state": str(parent_state_path),
        "parent_state_sha256": sha256_bytes(parent_state_bytes),
        "workflow": str(output),
        "workflow_sha256": sha256_file(output),
        "campaign_root": str(campaign_root),
        "task_count": len(repaired),
        "task_ids": [task["id"] for task in repaired],
        "wall_clock_timeout_seconds": None,
    }
    atomic_json(manifest_path, manifest)
    print(
        json.dumps(
            {
                "workflow": str(output),
                "manifest": str(manifest_path),
                "gem5_sha256": manifest["gem5_sha256"],
                "tasks": manifest["task_count"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
