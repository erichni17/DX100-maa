#!/usr/bin/env python3
"""Run the full-Class-B IS exit gate inside a verified memory cgroup."""

import argparse
import os
import shutil
import time
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--available-gib", type=int, default=96)
    parser.add_argument("--swap-quiet-seconds", type=int, default=300)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--expected-memory-high-gib", type=int, default=80)
    parser.add_argument("--expected-memory-max-gib", type=int, default=96)
    args = parser.parse_args()

    if (
        min(
            args.available_gib,
            args.swap_quiet_seconds,
            args.interval,
        )
        < 1
    ):
        raise SystemExit("admission thresholds must be positive")
    if args.expected_memory_high_gib >= args.expected_memory_max_gib:
        raise SystemExit("memory.high must be below memory.max")

    state_root = args.state_root.resolve()
    workflow = args.workflow.resolve()
    run_root = args.run_root.resolve()
    log = run_root / "recovery2-is-gate-manager.log"
    status = run_root / "recovery2-is-gate-manager-status.json"
    runtime = shutil.which("dx-runtime")
    if runtime is None:
        raise SystemExit("dx-runtime is unavailable")
    if not workflow.is_file():
        raise SystemExit(f"workflow missing: {workflow}")
    state = workflow_state_path(state_root, workflow_name(workflow))
    if state.exists():
        raise SystemExit(f"refusing duplicate workflow state: {state}")

    # systemd-run is asynchronous.  Let its short-lived launcher disappear so
    # ownership discovery cannot mistake the launcher for a live campaign.
    time.sleep(2)
    source_root = Path(__file__).resolve().parents[2]
    conflicts = conflicting_processes(source_root, run_root)
    if conflicts:
        raise SystemExit(
            f"refusing launch with live owned processes: {conflicts}"
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
            "phase": "admission",
        },
    )
    append_log(log, f"gate manager started cgroup={limits}")
    wait_for_admission(
        log,
        args.available_gib,
        args.swap_quiet_seconds,
        args.interval,
    )
    rc = run_workflow(runtime, state_root, workflow, 1, log)
    atomic_json(
        status,
        {
            "terminal": True,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "cgroup": limits,
            "phase": "terminal",
            "workflow_rc": rc,
        },
    )
    append_log(log, f"gate manager terminal workflow_rc={rc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
