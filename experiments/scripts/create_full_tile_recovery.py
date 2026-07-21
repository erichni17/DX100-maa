#!/usr/bin/env python3
"""Create post-reboot normal and memory-heavy tile-sweep workflows."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

NORMAL_PREFIXES = ("gapbs-", "ume-", "nas-cg-", "xrage-")
PARENT_OWNED = {"ume-gradzatp-t65536", "ume-gradzatz-t65536"}
RUNNERS = {
    "gapbs": Path("benchmarks/gapbs/run_gapbs_tile_smoke.sh"),
    "ume": Path("benchmarks/UME/run_ume_tile_smoke.sh"),
    "cg": Path("benchmarks/NAS/cg/run_cg_tile_smoke.sh"),
    "is": Path("benchmarks/NAS/is/run_is_smoke.sh"),
    "xrage": Path("benchmarks/spatter/run_xrage_tile_smoke.sh"),
}


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


def family(task_id):
    if task_id.startswith("gapbs-"):
        return "gapbs"
    if task_id.startswith("ume-"):
        return "ume"
    if task_id.startswith("nas-cg-"):
        return "cg"
    if task_id.startswith("nas-is-"):
        return "is"
    if task_id.startswith("xrage-"):
        return "xrage"
    raise ValueError(f"unsupported recovery task: {task_id}")


def repair_task(original, source_root, run_root, checkpoint_root):
    repaired = dict(original)
    task_family = family(repaired["id"])
    repaired["cwd"] = str(source_root)
    repaired["command"] = list(original["command"])
    repaired["command"][0] = str(source_root / RUNNERS[task_family])
    if task_family == "is":
        # The old 10M setting produced multi-gigabyte progress logs.  This
        # remains frequent enough to prove forward progress without turning
        # logging into a second resource incident.
        repaired["command"][-1] = "1000000000000"
    repaired["env"] = dict(original["env"])
    repaired["env"]["DX100_SOURCE_ROOT"] = str(source_root)
    repaired["env"]["CHECKPOINT_ROOT"] = str(checkpoint_root)
    repaired["env"]["CAMPAIGN_ROOT"] = str(run_root / f"{task_family}_recovery2")
    return repaired


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--parent-workflow", type=Path, required=True)
    parser.add_argument("--parent-manifest", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--normal-output", type=Path, required=True)
    parser.add_argument("--is-gate-output", type=Path, required=True)
    parser.add_argument("--is-output", type=Path, required=True)
    parser.add_argument(
        "--normal-name",
        default="dx100-full-tile-sweep-recovery2-normal-20260721",
    )
    parser.add_argument(
        "--is-gate-name",
        default="dx100-full-tile-sweep-recovery2-is-gate-20260721",
    )
    parser.add_argument(
        "--is-name",
        default="dx100-full-tile-sweep-recovery2-is-20260721",
    )
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    parent_workflow = args.parent_workflow.resolve()
    parent_manifest = args.parent_manifest.resolve()
    run_root = args.run_root.resolve()
    checkpoint_root = run_root / "checkpoints_recovery2"
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=source_root,
        text=True,
    ).strip()
    if dirty:
        raise SystemExit("source worktree has tracked changes; commit before launch")
    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source_root, text=True
    ).strip()

    original = json.loads(parent_workflow.read_text())
    normal_tasks = []
    is_gate_tasks = []
    is_tasks = []
    for task in original["tasks"]:
        task_id = task["id"]
        if task_id in PARENT_OWNED:
            continue
        if task_id.startswith(NORMAL_PREFIXES):
            normal_tasks.append(
                repair_task(task, source_root, run_root, checkpoint_root)
            )
        elif task_id.startswith("nas-is-"):
            repaired = repair_task(task, source_root, run_root, checkpoint_root)
            if task_id == "nas-is-t16384":
                is_gate_tasks.append(repaired)
            else:
                is_tasks.append(repaired)

    normal_workflow = {
        "version": 1,
        "name": args.normal_name,
        "tasks": normal_tasks,
    }
    is_gate_workflow = {
        "version": 1,
        "name": args.is_gate_name,
        "tasks": is_gate_tasks,
    }
    is_workflow = {"version": 1, "name": args.is_name, "tasks": is_tasks}
    normal_output = args.normal_output.resolve()
    is_gate_output = args.is_gate_output.resolve()
    is_output = args.is_output.resolve()
    write_json(normal_output, normal_workflow)
    write_json(is_gate_output, is_gate_workflow)
    write_json(is_output, is_workflow)

    manifest = {
        "schema_version": 1,
        "objective": "Recover every invalid or interrupted full-tile cell after the host OOM reboot",
        "source_root": str(source_root),
        "source_commit": source_commit,
        "parent_workflow": str(parent_workflow),
        "parent_workflow_sha256": sha256(parent_workflow),
        "parent_manifest": str(parent_manifest),
        "parent_manifest_sha256": sha256(parent_manifest),
        "normal_workflow": str(normal_output),
        "normal_workflow_sha256": sha256(normal_output),
        "is_gate_workflow": str(is_gate_output),
        "is_gate_workflow_sha256": sha256(is_gate_output),
        "is_workflow": str(is_output),
        "is_workflow_sha256": sha256(is_output),
        "checkpoint_root": str(checkpoint_root),
        "campaign_roots": {
            name: str(run_root / f"{name}_recovery2")
            for name in ("gapbs", "ume", "cg", "is", "xrage")
        },
        "normal_max_parallel": 8,
        "is_max_parallel": 1,
        "memory_policy": {
            "host_admission_available_gib": 96,
            "swap_quiet_seconds": 300,
            "campaign_memory_high_gib": 220,
            "campaign_memory_max_gib": 240,
            "campaign_swap_max_bytes": 0,
        },
        "wall_clock_timeout_seconds": None,
        "normal_task_count": len(normal_tasks),
        "is_gate_task_count": len(is_gate_tasks),
        "is_task_count": len(is_tasks),
        "normal_task_ids": [item["id"] for item in normal_tasks],
        "is_gate_task_ids": [item["id"] for item in is_gate_tasks],
        "is_task_ids": [item["id"] for item in is_tasks],
        "parent_owned_tasks": sorted(PARENT_OWNED),
        "incident_evidence": {
            "pre_reboot_host_used_gib": 316,
            "pre_reboot_host_available_gib": 12,
            "pre_reboot_swap_used_gib": 2,
            "five_is_processes_rss_kib": 316161776,
            "host_rebooted_at": "2026-07-21T13:53:46-04:00",
        },
    }
    write_json(run_root / "recovery2-manifest.json", manifest)
    print(
        json.dumps(
            {
                "normal_workflow": str(normal_output),
                "normal_tasks": len(normal_tasks),
                "is_gate_workflow": str(is_gate_output),
                "is_gate_tasks": len(is_gate_tasks),
                "is_workflow": str(is_output),
                "is_tasks": len(is_tasks),
            }
        )
    )


if __name__ == "__main__":
    main()
