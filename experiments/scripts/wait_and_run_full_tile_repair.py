#!/usr/bin/env python3
"""Wait for memory headroom, then exec the durable tile-repair workflow."""

import argparse
import json
import os
import shutil
import time
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path

GIB_KIB = 1024 * 1024


def mem_available_kib():
    with Path("/proc/meminfo").open() as source:
        for line in source:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1])
    raise RuntimeError("MemAvailable is absent from /proc/meminfo")


def swap_counters():
    counters = {}
    with Path("/proc/vmstat").open() as source:
        for line in source:
            name, value = line.split()
            if name in {"pswpin", "pswpout"}:
                counters[name] = int(value)
    if counters.keys() != {"pswpin", "pswpout"}:
        raise RuntimeError("swap counters are absent from /proc/vmstat")
    return counters["pswpin"], counters["pswpout"]


def append_log(path, message):
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    with path.open("a") as output:
        output.write(f"{timestamp} {message}\n")


def workflow_exists(state_root, name):
    return (state_root / "workflows" / f"{name}.json").exists()


def select_parallelism(available_kib, reserve_gib, per_task_gib, maximum):
    usable_gib = available_kib / GIB_KIB - reserve_gib
    return min(maximum, max(0, int(usable_gib // per_task_gib)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--minimum-parallel", type=int, default=2)
    parser.add_argument("--maximum-parallel", type=int, default=8)
    parser.add_argument("--reserve-gib", type=int, default=32)
    parser.add_argument("--per-task-gib", type=int, default=16)
    parser.add_argument("--swap-quiet-seconds", type=int, default=300)
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()

    if not (1 <= args.minimum_parallel <= args.maximum_parallel):
        raise SystemExit("parallel limits are invalid")
    if (
        min(
            args.reserve_gib,
            args.per_task_gib,
            args.swap_quiet_seconds,
            args.interval,
        )
        < 1
    ):
        raise SystemExit("memory allowances and interval must be positive")

    state_root = args.state_root.resolve()
    workflow = args.workflow.resolve()
    run_root = args.run_root.resolve()
    log = run_root / "repair-capacity-supervisor.log"
    if not workflow.is_file():
        raise SystemExit(f"workflow missing: {workflow}")
    document = json.loads(workflow.read_text())
    if document.get("name") != args.name:
        raise SystemExit("workflow name does not match --name")
    if workflow_exists(state_root, args.name):
        raise SystemExit(f"workflow state already exists for {args.name}")
    runtime = shutil.which("dx-runtime")
    if runtime is None:
        raise SystemExit("dx-runtime is unavailable")

    append_log(
        log,
        "supervisor started "
        f"minimum={args.minimum_parallel} maximum={args.maximum_parallel} "
        f"reserve_gib={args.reserve_gib} per_task_gib={args.per_task_gib} "
        f"swap_quiet_seconds={args.swap_quiet_seconds}",
    )
    prior_swap = swap_counters()
    last_swap_activity = time.monotonic()
    last_reported = None
    while True:
        now = time.monotonic()
        current_swap = swap_counters()
        if current_swap != prior_swap:
            append_log(
                log,
                "swap activity "
                f"pswpin_delta={current_swap[0] - prior_swap[0]} "
                f"pswpout_delta={current_swap[1] - prior_swap[1]}",
            )
            prior_swap = current_swap
            last_swap_activity = now
        swap_quiet_for = now - last_swap_activity
        swap_is_quiet = swap_quiet_for >= args.swap_quiet_seconds
        available_kib = mem_available_kib()
        parallelism = select_parallelism(
            available_kib,
            args.reserve_gib,
            args.per_task_gib,
            args.maximum_parallel,
        )
        available_gib = available_kib / GIB_KIB
        bucket = int(available_gib // 8) * 8
        report_key = (bucket, swap_is_quiet)
        if report_key != last_reported:
            append_log(
                log,
                f"waiting available_gib={available_gib:.1f} "
                f"admissible_parallel={parallelism} "
                f"swap_quiet_for={swap_quiet_for:.0f}s",
            )
            last_reported = report_key
        if parallelism >= args.minimum_parallel and swap_is_quiet:
            if workflow_exists(state_root, args.name):
                raise SystemExit(f"workflow state appeared for {args.name}")
            append_log(
                log,
                f"launch available_gib={available_gib:.1f} "
                f"max_parallel={parallelism} swap_quiet_for={swap_quiet_for:.0f}s",
            )
            os.execv(
                runtime,
                [
                    runtime,
                    "--state-root",
                    str(state_root),
                    "workflow",
                    "run",
                    "--max-parallel",
                    str(parallelism),
                    str(workflow),
                ],
            )
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
