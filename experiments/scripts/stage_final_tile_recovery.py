#!/usr/bin/env python3
"""Stage, but never launch, the final GAPBS and NAS IS recovery workflows."""

import argparse
import copy
import json
import sys
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path

import finalize_full_tile_sweep as finalizer

GAPBS_TASKS = (
    "gapbs-bc-t1024",
    "gapbs-bc-t2048",
    "gapbs-bc-t4096",
    "gapbs-bc-t8192",
    "gapbs-sssp-t2048",
)
IS_TASKS = (
    "nas-is-t1024",
    "nas-is-t4096",
    "nas-is-t8192",
    "nas-is-t32768",
    "nas-is-t65536",
)


class StageError(RuntimeError):
    pass


def load(path):
    document = json.loads(path.read_text())
    if not isinstance(document, dict):
        raise StageError(f"workflow is not a JSON object: {path}")
    return document


def task_map(*workflows):
    tasks = {}
    for workflow in workflows:
        for task in workflow.get("tasks", ()):
            tasks.setdefault(task.get("id"), task)
    return tasks


def guarded(
    task, guard, campaign_root, checkpoint_root, gem5_binary, needs=None
):
    result = copy.deepcopy(task)
    result["command"] = [str(guard), *result["command"]]
    result.setdefault("env", {})["DX100_POST_ROI_MODE"] = "anchored"
    result["env"]["CAMPAIGN_ROOT"] = str(campaign_root)
    result["env"]["CHECKPOINT_ROOT"] = str(checkpoint_root)
    result["env"]["DX100_GEM5_BIN"] = str(gem5_binary)
    result["env"]["DX100_SIMULATION_PLAN_VERSION"] = "tile-final-recovery-v3"
    if needs:
        result["needs"] = list(needs)
    else:
        result.pop("needs", None)
    return result


def stage(run_root, source_root):
    run_root = run_root.resolve()
    source_root = source_root.resolve()
    guard = (
        source_root
        / "experiments/scripts/require_simulation_launch_approval.sh"
    )
    if not guard.is_file():
        raise StageError(f"launch guard is missing: {guard}")
    outputs = {
        "gapbs": run_root / "final-gapbs-recovery-workflow-v3.json",
        "is": run_root / "final-is-recovery-workflow-v3.json",
        "plan": run_root / "final-recovery-plan-v3.json",
        "supersession": run_root / "final-recovery-superseded-v2.json",
    }
    existing = [str(path) for path in outputs.values() if path.exists()]
    if existing:
        raise StageError(
            "refusing to overwrite staged artifacts: " + ", ".join(existing)
        )

    gapbs_source = load(run_root / "repair5-gapbs-retry-workflow.json")
    is_sources = [
        load(run_root / f"recovery4-is-node1-{lane}-workflow.json")
        for lane in ("low", "mid", "high")
    ]
    gapbs_map = task_map(gapbs_source)
    is_map = task_map(*is_sources)
    missing = [task for task in GAPBS_TASKS if task not in gapbs_map]
    missing += [task for task in IS_TASKS if task not in is_map]
    if missing:
        raise StageError("source workflows omit tasks: " + ", ".join(missing))

    candidate = Path(
        "/data1/nier/DX100/ckpt_cache/.gem5_snapshots/sha256/"
        "1ff4a396b98d6c838f695c4cbd631ca16e7ed12407365f17707bcf6df93e1343/gem5"
    )
    canonical = run_root / (
        "checkpoints_recovery2/.gem5_snapshots/sha256/"
        "bcc30842a2f26aad2a0cddc769381180f885c683c0be711e2feffb0ac56c18ab/gem5"
    )
    expected_hashes = {
        "candidate": (
            "1ff4a396b98d6c838f695c4cbd631ca16e7ed12407365f17707bcf6df93e1343"
        ),
        "canonical": (
            "bcc30842a2f26aad2a0cddc769381180f885c683c0be711e2feffb0ac56c18ab"
        ),
    }
    for label, binary in (("candidate", candidate), ("canonical", canonical)):
        if not binary.is_file():
            raise StageError(
                f"{label} frozen gem5 binary is missing: {binary}"
            )
        if finalizer.sha256(binary) != expected_hashes[label]:
            raise StageError(f"{label} frozen gem5 binary hash mismatch")

    gapbs_workflow = {
        "version": 1,
        "name": "dx100-full-tile-final-gapbs-recovery-v3-20260726",
        "tasks": [
            guarded(
                gapbs_map[task_id],
                guard,
                run_root / "final-recovery/gapbs",
                run_root / "checkpoints_final_roi_anchored_v3",
                canonical if task_id == "gapbs-sssp-t2048" else candidate,
            )
            for task_id in GAPBS_TASKS
        ],
    }
    is_tasks = []
    previous = None
    for task_id in IS_TASKS:
        is_tasks.append(
            guarded(
                is_map[task_id],
                guard,
                run_root / "final-recovery/is",
                run_root / "checkpoints_final_roi_anchored_v3",
                canonical,
                [previous] if previous else None,
            )
        )
        previous = task_id
    is_workflow = {
        "version": 1,
        "name": "dx100-full-tile-final-is-recovery-v3-20260726",
        "tasks": is_tasks,
    }
    finalizer.atomic_json(outputs["gapbs"], gapbs_workflow)
    finalizer.atomic_json(outputs["is"], is_workflow)
    plan = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "staged-not-authorized",
        "plan_version": "tile-final-recovery-v3",
        "simulation_launch_approval_required": True,
        "required_environment": "DX100_SIMULATION_LAUNCH_APPROVED=YES",
        "gapbs": {
            "workflow": str(outputs["gapbs"]),
            "workflow_sha256": finalizer.sha256(outputs["gapbs"]),
            "tasks": list(GAPBS_TASKS),
            "recommended_max_parallel": 4,
            "memory_high_gib": 80,
            "memory_max_gib": 96,
            "memory_swap_max_gib": 0,
            "exact_anchors": {
                "gapbs-bc-s22": {
                    "tile": 16384,
                    "gem5_sha256": expected_hashes["candidate"],
                },
                "gapbs-sssp-s22": {
                    "tile": 8192,
                    "gem5_sha256": expected_hashes["canonical"],
                },
            },
            "cgroup_telemetry": str(
                run_root / "final-gapbs-recovery-cgroup.tsv"
            ),
        },
        "is": {
            "workflow": str(outputs["is"]),
            "workflow_sha256": finalizer.sha256(outputs["is"]),
            "tasks": list(IS_TASKS),
            "recommended_max_parallel": 1,
            "memory_high_gib": 60,
            "memory_max_gib": 64,
            "memory_swap_max_gib": 0,
            "exact_anchors": {
                "nas-is-full": {
                    "tile": 16384,
                    "gem5_sha256": expected_hashes["canonical"],
                }
            },
            "cgroup_telemetry": str(run_root / "final-is-recovery-cgroup.tsv"),
        },
        "launch_note": (
            "Inspect live ownership, MemAvailable, app.slice usage, and five quiet "
            "vmstat minutes after the user explicitly approves a lane."
        ),
    }
    finalizer.atomic_json(outputs["plan"], plan)
    superseded = {}
    for version, suffix in (("v1", ""), ("v2", "-v2")):
        for label, stem in {
            "gapbs_workflow": "final-gapbs-recovery-workflow",
            "is_workflow": "final-is-recovery-workflow",
            "plan": "final-recovery-plan",
        }.items():
            path = run_root / f"{stem}{suffix}.json"
            if path.is_file():
                superseded[f"{version}_{label}"] = {
                    "path": str(path),
                    "sha256": finalizer.sha256(path),
                }
    finalizer.atomic_json(
        outputs["supersession"],
        {
            "schema_version": 1,
            "decision": "superseded-before-launch",
            "reason": (
                "v1 reused exact-validator checkpoint images; v2 fixed that "
                "but paired SSSP 2K with a different simulator hash than its "
                "exact 8K anchor; v3 fixes both before any launch"
            ),
            "superseded": superseded,
            "successor_plan": str(outputs["plan"]),
            "successor_plan_sha256": finalizer.sha256(outputs["plan"]),
        },
    )
    return {
        "ok": True,
        "status": plan["status"],
        **{key: str(value) for key, value in outputs.items()},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = stage(args.run_root, args.source_root)
    except (StageError, OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
