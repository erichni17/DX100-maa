#!/usr/bin/env python3
"""Run the non-IS tile recovery while the isolated IS gate is live."""

import argparse
import json
import os
import shutil
import subprocess
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path

from run_full_tile_recovery import (
    append_log,
    atomic_json,
    conflicting_processes,
    run_workflow,
    verify_cgroup,
    wait_for_admission,
    workflow_name,
    workflow_state_path,
)


def process_cgroup_directory(pid):
    try:
        with (Path("/proc") / str(pid) / "cgroup").open() as source:
            for line in source:
                hierarchy, controllers, relative = line.rstrip().split(":", 2)
                if hierarchy == "0" and not controllers:
                    return Path("/sys/fs/cgroup") / relative.lstrip("/")
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    return None


def outside_allowed_cgroup(conflicts, allowed_cgroup):
    allowed = allowed_cgroup.resolve()
    unexpected = []
    for conflict in conflicts:
        actual = process_cgroup_directory(conflict["pid"])
        if actual is None:
            unexpected.append(conflict)
            continue
        try:
            actual.resolve().relative_to(allowed)
        except ValueError:
            unexpected.append(conflict)
    return unexpected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--allowed-live-cgroup", type=Path, required=True)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--runtime", default="/home/nier/.local/bin/dx-runtime"
    )
    parser.add_argument("--parallel", type=int, default=8)
    parser.add_argument("--available-gib", type=int, default=128)
    parser.add_argument("--swap-quiet-seconds", type=int, default=300)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--expected-memory-high-gib", type=int, default=128)
    parser.add_argument("--expected-memory-max-gib", type=int, default=144)
    args = parser.parse_args()

    if (
        min(
            args.parallel,
            args.available_gib,
            args.swap_quiet_seconds,
            args.interval,
        )
        < 1
    ):
        raise SystemExit(
            "parallelism and admission thresholds must be positive"
        )
    if args.expected_memory_high_gib >= args.expected_memory_max_gib:
        raise SystemExit("memory.high must be below memory.max")

    state_root = args.state_root.resolve()
    workflow = args.workflow.resolve()
    run_root = args.run_root.resolve()
    allowed_live_cgroup = args.allowed_live_cgroup.resolve()
    stem = (
        "recovery2-normal-retry-manager"
        if args.retry_failed
        else "recovery2-normal-manager"
    )
    log = run_root / f"{stem}.log"
    status = run_root / f"{stem}-status.json"
    runtime = shutil.which(args.runtime)
    if runtime is None:
        raise SystemExit("dx-runtime is unavailable")
    if not workflow.is_file():
        raise SystemExit(f"workflow missing: {workflow}")
    name = workflow_name(workflow)
    state = workflow_state_path(state_root, name)
    if args.retry_failed:
        if not state.exists():
            raise SystemExit(f"retry state is absent: {state}")
        document = json.loads(state.read_text())
        task_states = [
            task.get("state") for task in document.get("tasks", {}).values()
        ]
        if not task_states or not all(
            item in {"completed", "failed", "skipped"} for item in task_states
        ):
            raise SystemExit(f"retry state is not terminal: {state}")
        if "failed" not in task_states and "skipped" not in task_states:
            raise SystemExit(
                f"retry state has no failed/skipped tasks: {state}"
            )
    elif state.exists():
        raise SystemExit(f"refusing duplicate workflow state: {state}")
    if not args.retry_failed and not allowed_live_cgroup.is_dir():
        raise SystemExit(
            f"allowed gate cgroup is absent: {allowed_live_cgroup}"
        )

    source_root = Path(__file__).resolve().parents[2]
    conflicts = conflicting_processes(source_root, run_root)
    if allowed_live_cgroup.is_dir():
        conflicts = outside_allowed_cgroup(conflicts, allowed_live_cgroup)
    if conflicts:
        raise SystemExit(
            "refusing launch with unexpected live owned processes: "
            + json.dumps(conflicts, sort_keys=True)
        )
    limits = verify_cgroup(
        args.expected_memory_high_gib,
        args.expected_memory_max_gib,
    )
    atomic_json(
        status,
        {
            "terminal": False,
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "cgroup": limits,
            "allowed_live_cgroup": str(allowed_live_cgroup),
            "phase": "admission",
        },
    )
    append_log(
        log,
        f"normal manager started cgroup={limits} "
        f"allowed_live_cgroup={allowed_live_cgroup}",
    )
    wait_for_admission(
        log,
        args.available_gib,
        args.swap_quiet_seconds,
        args.interval,
    )
    if args.retry_failed:
        command = [
            runtime,
            "--state-root",
            str(state_root),
            "workflow",
            "resume",
            name,
            "--retry-failed",
            "--max-parallel",
            str(args.parallel),
        ]
        append_log(
            log, f"workflow retry name={name} max_parallel={args.parallel}"
        )
        with log.open("a") as output:
            rc = subprocess.run(
                command, stdout=output, stderr=subprocess.STDOUT
            ).returncode
        append_log(log, f"workflow retry return name={name} rc={rc}")
    else:
        rc = run_workflow(runtime, state_root, workflow, args.parallel, log)
    atomic_json(
        status,
        {
            "terminal": True,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "cgroup": limits,
            "allowed_live_cgroup": str(allowed_live_cgroup),
            "phase": "terminal",
            "workflow_rc": rc,
        },
    )
    append_log(log, f"normal manager terminal workflow_rc={rc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
