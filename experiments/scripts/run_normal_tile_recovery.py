#!/usr/bin/env python3
"""Run the non-IS tile recovery while the isolated IS gate is live."""

import argparse
import json
import os
import shutil
import subprocess
from collections import Counter
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path

from run_full_tile_recovery import (
    GIB_BYTES,
    append_log,
    atomic_json,
    conflicting_processes,
    read_limit,
    run_workflow,
    verify_cgroup,
    wait_for_admission,
    workflow_name,
    workflow_state_path,
)

SAFE_ID_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
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
    return outside_allowed_cgroups(conflicts, [allowed_cgroup])


def outside_allowed_cgroups(conflicts, allowed_cgroups):
    allowed = [path.resolve() for path in allowed_cgroups]
    unexpected = []
    for conflict in conflicts:
        actual = process_cgroup_directory(conflict["pid"])
        if actual is None:
            unexpected.append(conflict)
            continue
        actual = actual.resolve()
        if not any(path_is_within(actual, parent) for parent in allowed):
            unexpected.append(conflict)
    return unexpected


def path_is_within(path, parent):
    if path == parent:
        return True
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def verify_primary_task_states(
    workflow, primary_state, ownership_scope=None
):
    workflow_document = json.loads(workflow.read_text())
    task_ids = [task.get("id") for task in workflow_document.get("tasks", [])]
    if not task_ids or None in task_ids or len(set(task_ids)) != len(task_ids):
        raise SystemExit(
            "auxiliary workflow task ids are missing or duplicated"
        )
    state_document = json.loads(primary_state.read_text())
    if ownership_scope is not None:
        primary_scope = state_document.get("ownership_scope")
        if primary_scope != ownership_scope:
            raise SystemExit(
                "primary workflow does not share task ownership scope: "
                f"expected={ownership_scope!r} actual={primary_scope!r}"
            )
    primary_tasks = state_document.get("tasks", {})
    if not isinstance(primary_tasks, dict):
        raise SystemExit(
            f"primary workflow tasks are invalid: {primary_state}"
        )
    selected_states = {}
    for task_id in task_ids:
        task = primary_tasks.get(task_id)
        if not isinstance(task, dict) or "state" not in task:
            raise SystemExit(
                f"auxiliary task is absent from primary state: {task_id}"
            )
        selected_states[task_id] = task["state"]
    allowed_states = {"pending", "failed", "completed", "skipped", "running"}
    invalid = {
        task_id: state
        for task_id, state in selected_states.items()
        if state not in allowed_states
    }
    if invalid:
        raise SystemExit(
            "auxiliary tasks have invalid primary workflow states: "
            + json.dumps(invalid, sort_keys=True)
        )
    if ownership_scope is None:
        unsafe = {
            task_id: state
            for task_id, state in selected_states.items()
            if state == "running"
        }
        if unsafe:
            raise SystemExit(
                "auxiliary tasks are live in primary workflow: "
                + json.dumps(unsafe, sort_keys=True)
            )
    return selected_states


def verify_aggregate_memory_max(own_max, allowed_cgroups, maximum_gib):
    members = {"manager": own_max}
    for path in allowed_cgroups:
        value = read_limit(path / "memory.max")
        if value == "max":
            raise SystemExit(f"uncapped allowed cgroup: {path}")
        members[str(path)] = value
    total = sum(members.values())
    maximum = maximum_gib * GIB_BYTES
    if total > maximum:
        raise SystemExit(
            f"unsafe aggregate memory.max: total={total} limit={maximum}"
        )
    return {"members": members, "total": total, "limit": maximum}


def prepare_retry_state(state_path, workflow_path):
    state = json.loads(state_path.read_text())
    workflow = json.loads(workflow_path.read_text())
    workflow_tasks = workflow.get("tasks", [])
    workflow_ids = [task.get("id") for task in workflow_tasks]
    state_tasks = state.get("tasks", {})
    if workflow.get("name") != state.get("name"):
        raise SystemExit("retry workflow name does not match workflow state")
    if (
        not workflow_ids
        or any(not task_id for task_id in workflow_ids)
        or len(workflow_ids) != len(set(workflow_ids))
        or set(workflow_ids) != set(state_tasks)
    ):
        raise SystemExit("retry workflow tasks do not match workflow state")
    states = [task.get("state") for task in state_tasks.values()]
    allowed = {"completed", "failed", "skipped", "pending", "running"}
    if not states or any(item not in allowed for item in states):
        raise SystemExit("retry workflow state contains an invalid task state")
    if all(item == "completed" for item in states):
        raise SystemExit("retry workflow state is already complete")
    previous = state.get("file")
    state["file"] = str(workflow_path)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state["retry_workflow_repointed_from"] = previous
    atomic_json(state_path, state)
    return {
        "previous": previous,
        "current": str(workflow_path),
        "states": dict(Counter(states)),
    }


def verify_runtime_ownership_support(runtime):
    completed = subprocess.run(
        [runtime, "workflow", "run", "--help"],
        text=True,
        capture_output=True,
    )
    if (
        completed.returncode != 0
        or "--ownership-scope" not in completed.stdout
    ):
        raise SystemExit(
            "dx-runtime does not support nonblocking task ownership; "
            "install the reviewed runtime update before using "
            "--ownership-scope"
        )


def validate_ownership_scope(value):
    if value is None:
        return None
    if (
        not value
        or len(value) > 96
        or value[0] in ".-"
        or any(character not in SAFE_ID_CHARS for character in value)
    ):
        raise SystemExit(f"invalid task ownership scope: {value!r}")
    return value


def select_ownership_scope(*candidates):
    scopes = {
        validate_ownership_scope(value)
        for value in candidates
        if value is not None
    }
    if len(scopes) > 1:
        raise SystemExit(
            "task ownership scopes disagree: "
            + json.dumps(sorted(scopes))
        )
    return next(iter(scopes), None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument(
        "--allowed-live-cgroup",
        type=Path,
        action="append",
        required=True,
    )
    parser.add_argument("--aggregate-memory-max-gib", type=int)
    parser.add_argument("--primary-workflow-state", type=Path)
    parser.add_argument("--ownership-scope")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--artifact-stem")
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
    allowed_live_cgroups = [
        path.resolve() for path in args.allowed_live_cgroup
    ]
    primary_workflow_state = (
        args.primary_workflow_state.resolve()
        if args.primary_workflow_state is not None
        else None
    )
    stem = args.artifact_stem or (
        "recovery2-normal-retry-manager"
        if args.retry_failed
        else "recovery2-normal-manager"
    )
    if Path(stem).name != stem:
        raise SystemExit("artifact stem must be a filename stem")
    log = run_root / f"{stem}.log"
    status = run_root / f"{stem}-status.json"
    runtime = shutil.which(args.runtime)
    if runtime is None:
        raise SystemExit("dx-runtime is unavailable")
    if not workflow.is_file():
        raise SystemExit(f"workflow missing: {workflow}")
    if (
        primary_workflow_state is not None
        and not primary_workflow_state.is_file()
    ):
        raise SystemExit(
            f"primary workflow state missing: {primary_workflow_state}"
        )
    name = workflow_name(workflow)
    state = workflow_state_path(state_root, name)
    retry_state_document = None
    if args.retry_failed:
        if not state.exists():
            raise SystemExit(f"retry state is absent: {state}")
        retry_state_document = json.loads(state.read_text())
        task_states = [
            task.get("state")
            for task in retry_state_document.get("tasks", {}).values()
        ]
        if not task_states or all(item == "completed" for item in task_states):
            raise SystemExit(f"retry state has no incomplete tasks: {state}")
    elif state.exists():
        raise SystemExit(f"refusing duplicate workflow state: {state}")
    primary_state_document = (
        json.loads(primary_workflow_state.read_text())
        if primary_workflow_state is not None
        else {}
    )
    ownership_scope = select_ownership_scope(
        args.ownership_scope,
        retry_state_document.get("ownership_scope")
        if retry_state_document is not None
        else None,
        primary_state_document.get("ownership_scope"),
    )
    if ownership_scope is not None:
        verify_runtime_ownership_support(runtime)
    primary_task_states = None
    if primary_workflow_state is not None:
        primary_task_states = verify_primary_task_states(
            workflow,
            primary_workflow_state,
            ownership_scope,
        )
    missing_allowed = [
        path for path in allowed_live_cgroups if not path.is_dir()
    ]
    if not args.retry_failed and missing_allowed:
        raise SystemExit(
            "allowed live cgroup is absent: "
            + ", ".join(str(path) for path in missing_allowed)
        )

    source_root = Path(__file__).resolve().parents[2]
    conflicts = conflicting_processes(source_root, run_root)
    existing_allowed = [path for path in allowed_live_cgroups if path.is_dir()]
    if existing_allowed:
        conflicts = outside_allowed_cgroups(conflicts, existing_allowed)
    if conflicts:
        raise SystemExit(
            "refusing launch with unexpected live owned processes: "
            + json.dumps(conflicts, sort_keys=True)
        )
    retry_repoint = None
    if args.retry_failed:
        retry_repoint = prepare_retry_state(state, workflow)
    limits = verify_cgroup(
        args.expected_memory_high_gib,
        args.expected_memory_max_gib,
    )
    aggregate = None
    if args.aggregate_memory_max_gib is not None:
        if args.aggregate_memory_max_gib < 1:
            raise SystemExit("aggregate memory maximum must be positive")
        if len(existing_allowed) != len(allowed_live_cgroups):
            raise SystemExit(
                "aggregate verification requires every allowed cgroup"
            )
        aggregate = verify_aggregate_memory_max(
            limits["memory_max"],
            existing_allowed,
            args.aggregate_memory_max_gib,
        )
    allowed_record = [str(path) for path in allowed_live_cgroups]
    atomic_json(
        status,
        {
            "terminal": False,
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "cgroup": limits,
            "allowed_live_cgroups": allowed_record,
            "aggregate_memory_max": aggregate,
            "primary_workflow_state": str(primary_workflow_state)
            if primary_workflow_state is not None
            else None,
            "primary_task_states": primary_task_states,
            "ownership_scope": ownership_scope,
            "retry_workflow_repoint": retry_repoint,
            "phase": "admission",
        },
    )
    append_log(
        log,
        f"normal manager started cgroup={limits} "
        f"allowed_live_cgroups={allowed_record} aggregate={aggregate} "
        f"retry_workflow_repoint={retry_repoint} "
        f"ownership_scope={ownership_scope}",
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
        if ownership_scope is not None:
            command.extend(
                ["--ownership-scope", ownership_scope]
            )
        append_log(
            log, f"workflow retry name={name} max_parallel={args.parallel}"
        )
        with log.open("a") as output:
            rc = subprocess.run(
                command, stdout=output, stderr=subprocess.STDOUT
            ).returncode
        append_log(log, f"workflow retry return name={name} rc={rc}")
    else:
        rc = run_workflow(
            runtime,
            state_root,
            workflow,
            args.parallel,
            log,
            ownership_scope,
        )
    atomic_json(
        status,
        {
            "terminal": True,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "cgroup": limits,
            "allowed_live_cgroups": allowed_record,
            "aggregate_memory_max": aggregate,
            "primary_workflow_state": str(primary_workflow_state)
            if primary_workflow_state is not None
            else None,
            "primary_task_states": primary_task_states,
            "ownership_scope": ownership_scope,
            "retry_workflow_repoint": retry_repoint,
            "phase": "terminal",
            "workflow_rc": rc,
        },
    )
    append_log(log, f"normal manager terminal workflow_rc={rc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
