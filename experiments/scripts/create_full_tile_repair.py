#!/usr/bin/env python3
"""Create a successor workflow for the failed GAPBS and UME sweep cells."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--parent-workflow", type=Path, required=True)
    parser.add_argument("--parent-manifest", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", default="dx100-full-tile-sweep-repair1-20260721")
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    parent_workflow = args.parent_workflow.resolve()
    parent_manifest = args.parent_manifest.resolve()
    run_root = args.run_root.resolve()
    checkpoint_root = run_root / "checkpoints_oracle_v2"
    original = json.loads(parent_workflow.read_text())
    selected = []
    parent_owned = {"ume-gradzatp-t65536", "ume-gradzatz-t65536"}
    for original_task in original["tasks"]:
        if not original_task["id"].startswith(("gapbs-", "ume-")):
            continue
        if original_task["id"] in parent_owned:
            continue
        repaired = dict(original_task)
        repaired["cwd"] = str(source_root)
        repaired["command"] = list(original_task["command"])
        if repaired["id"].startswith("gapbs-"):
            repaired["command"][0] = str(
                source_root / "benchmarks/gapbs/run_gapbs_tile_smoke.sh"
            )
        else:
            repaired["command"][0] = str(
                source_root / "benchmarks/UME/run_ume_tile_smoke.sh"
            )
        repaired["env"] = dict(original_task["env"])
        repaired["env"]["DX100_SOURCE_ROOT"] = str(source_root)
        repaired["env"]["CHECKPOINT_ROOT"] = str(checkpoint_root)
        family = "gapbs" if repaired["id"].startswith("gapbs-") else "ume"
        repaired["env"]["CAMPAIGN_ROOT"] = str(run_root / f"{family}_repair1")
        selected.append(repaired)

    workflow = {"version": 1, "name": args.name, "tasks": selected}
    write_json(args.output, workflow)
    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source_root, text=True
    ).strip()
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=source_root,
        text=True,
    ).strip()
    if dirty:
        raise SystemExit("source worktree has tracked changes; commit before launch")
    manifest = {
        "schema_version": 1,
        "objective": "Retry only GAPBS and UME cells after fail-closed harness repair",
        "source_root": str(source_root),
        "source_commit": source_commit,
        "parent_workflow": str(parent_workflow),
        "parent_workflow_sha256": sha256(parent_workflow),
        "parent_manifest": str(parent_manifest),
        "parent_manifest_sha256": sha256(parent_manifest),
        "workflow": str(args.output.resolve()),
        "workflow_sha256": sha256(args.output.resolve()),
        "checkpoint_root": str(checkpoint_root),
        "campaign_roots": {
            "gapbs": str(run_root / "gapbs_repair1"),
            "ume": str(run_root / "ume_repair1"),
        },
        "wall_clock_timeout_seconds": None,
        "task_count": len(selected),
        "task_ids": [item["id"] for item in selected],
        "parent_owned_tasks": sorted(parent_owned),
        "repairs": [
            "Build GAPBS converter and generate shared graph inputs atomically",
            "Build UME fixed-input fingerprint binaries and enforce exact scalar oracles",
            "Use fresh checkpoints so pre-ROI inputs match the repaired binaries",
            "Leave the parent's not-yet-started 64K UME cells with the parent to avoid duplicate simulations",
        ],
    }
    write_json(run_root / "repair1-manifest.json", manifest)
    print(json.dumps({"workflow": str(args.output), "tasks": len(selected)}))


if __name__ == "__main__":
    main()
