#!/usr/bin/env python3
"""Run recovery workflows only inside a verified memory-capped cgroup."""

import argparse
import json
import os
import shutil
import subprocess
import time
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path

GIB_BYTES = 1024**3
GIB_KIB = 1024**2


def append_log(path, message):
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    with path.open("a") as output:
        output.write(f"{stamp} {message}\n")


def atomic_json(path, document):
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


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


def cgroup_directory():
    with Path("/proc/self/cgroup").open() as source:
        for line in source:
            hierarchy, controllers, relative = line.rstrip().split(":", 2)
            if hierarchy == "0" and not controllers:
                return Path("/sys/fs/cgroup") / relative.lstrip("/")
    raise RuntimeError("unified cgroup path is absent from /proc/self/cgroup")


def read_limit(path):
    value = path.read_text().strip()
    return value if value == "max" else int(value)


def verify_cgroup(expected_high_gib, expected_max_gib):
    root = cgroup_directory()
    actual = {
        "path": str(root),
        "memory_high": read_limit(root / "memory.high"),
        "memory_max": read_limit(root / "memory.max"),
        "memory_swap_max": read_limit(root / "memory.swap.max"),
    }
    expected = {
        "memory_high": expected_high_gib * GIB_BYTES,
        "memory_max": expected_max_gib * GIB_BYTES,
        "memory_swap_max": 0,
    }
    for key, value in expected.items():
        if actual[key] != value:
            raise SystemExit(
                f"unsafe cgroup {key}: expected {value}, found {actual[key]}"
            )
    return actual


def workflow_name(path):
    document = json.loads(path.read_text())
    if not document.get("name") or not document.get("tasks"):
        raise SystemExit(f"invalid workflow document: {path}")
    return document["name"]


def workflow_state_path(state_root, name):
    return state_root / "workflows" / f"{name}.json"


def workflow_completed(path):
    if not path.exists():
        return False
    document = json.loads(path.read_text())
    tasks = document.get("tasks", {})
    return bool(tasks) and all(
        item.get("state") == "completed" for item in tasks.values()
    )


def proc_stat(pid):
    fields = (
        (Path("/proc") / str(pid) / "stat")
        .read_text()
        .rsplit(") ", 1)[1]
        .split()
    )
    return {"ppid": int(fields[1]), "start_time_ticks": int(fields[19])}


def ancestor_pids():
    ancestors = set()
    pid = os.getpid()
    while pid > 0 and pid not in ancestors:
        ancestors.add(pid)
        try:
            pid = proc_stat(pid)["ppid"]
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            break
    return ancestors


def conflicting_processes(source_root, run_root):
    conflicts = []
    ignored = ancestor_pids()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) in ignored:
            continue
        try:
            command = (
                (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode()
            )
            identity = proc_stat(int(entry.name))
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        owned = str(source_root) in command or str(run_root) in command
        workload = (
            "gem5.opt" in command
            or "dx-runtime" in command
            or ("run_" in command and "_tile" in command)
        )
        if owned and workload:
            conflicts.append(
                {
                    "pid": int(entry.name),
                    "start_time_ticks": identity["start_time_ticks"],
                    "command": command,
                }
            )
    return conflicts


def wait_for_admission(log, available_gib, quiet_seconds, interval):
    prior_swap = swap_counters()
    quiet_since = time.monotonic()
    prior_report = None
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
            quiet_since = now
        current_available = mem_available_kib() / GIB_KIB
        quiet_for = now - quiet_since
        admitted = (
            current_available >= available_gib and quiet_for >= quiet_seconds
        )
        report = (int(current_available // 8), admitted)
        if report != prior_report:
            append_log(
                log,
                f"admission available_gib={current_available:.1f} "
                f"required_gib={available_gib} swap_quiet_for={quiet_for:.0f}s "
                f"admitted={admitted}",
            )
            prior_report = report
        if admitted:
            return
        time.sleep(interval)


def run_workflow(runtime, state_root, workflow, parallelism, log):
    command = [
        runtime,
        "--state-root",
        str(state_root),
        "workflow",
        "run",
        "--max-parallel",
        str(parallelism),
        str(workflow),
    ]
    append_log(
        log, f"workflow launch path={workflow} max_parallel={parallelism}"
    )
    with log.open("a") as output:
        completed = subprocess.run(
            command, stdout=output, stderr=subprocess.STDOUT
        )
    append_log(
        log, f"workflow return path={workflow} rc={completed.returncode}"
    )
    return completed.returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--normal-workflow", type=Path, required=True)
    parser.add_argument("--is-workflow", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument(
        "--runtime", default="/home/nier/.local/bin/dx-runtime"
    )
    parser.add_argument(
        "--is-gate-name",
        default="dx100-full-tile-sweep-recovery2-is-gate-20260721",
    )
    parser.add_argument("--normal-parallel", type=int, default=8)
    parser.add_argument("--is-parallel", type=int, default=1)
    parser.add_argument("--available-gib", type=int, default=96)
    parser.add_argument("--swap-quiet-seconds", type=int, default=300)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--expected-memory-high-gib", type=int, default=220)
    parser.add_argument("--expected-memory-max-gib", type=int, default=240)
    args = parser.parse_args()

    if (
        min(
            args.normal_parallel,
            args.is_parallel,
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
    normal_workflow = args.normal_workflow.resolve()
    is_workflow = args.is_workflow.resolve()
    run_root = args.run_root.resolve()
    log = run_root / "recovery2-manager.log"
    status = run_root / "recovery2-manager-status.json"
    runtime = shutil.which(args.runtime)
    if runtime is None:
        raise SystemExit("dx-runtime is unavailable")
    for path in (normal_workflow, is_workflow):
        if not path.is_file():
            raise SystemExit(f"workflow missing: {path}")

    names = [workflow_name(normal_workflow), workflow_name(is_workflow)]
    existing = [
        str(workflow_state_path(state_root, name))
        for name in names
        if workflow_state_path(state_root, name).exists()
    ]
    if existing:
        raise SystemExit(f"refusing duplicate workflow state: {existing}")
    gate_state = workflow_state_path(state_root, args.is_gate_name)
    if not workflow_completed(gate_state):
        raise SystemExit(f"IS exit gate is not completed: {gate_state}")
    source_root = Path(__file__).resolve().parents[2]
    conflicts = conflicting_processes(source_root, run_root)
    if conflicts:
        raise SystemExit(
            "refusing launch with live owned processes: "
            + json.dumps(conflicts, sort_keys=True)
        )
    limits = verify_cgroup(
        args.expected_memory_high_gib, args.expected_memory_max_gib
    )
    atomic_json(
        status,
        {
            "terminal": False,
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "cgroup": limits,
            "phase": "admission-normal",
        },
    )
    append_log(
        log, f"manager started pid={os.getpid()} cgroup={json.dumps(limits)}"
    )
    watcher_script = (
        Path(__file__).resolve().with_name("watch_full_tile_completion.py")
    )
    watcher_command = [
        os.environ.get("PYTHON", "/usr/bin/python3"),
        str(watcher_script),
        "--run-root",
        str(run_root),
        "--state-root",
        str(state_root),
    ]
    with log.open("a") as output:
        watcher = subprocess.Popen(
            watcher_command, stdout=output, stderr=subprocess.STDOUT
        )
    append_log(log, f"validation watcher started pid={watcher.pid}")

    wait_for_admission(
        log, args.available_gib, args.swap_quiet_seconds, args.interval
    )
    normal_rc = run_workflow(
        runtime, state_root, normal_workflow, args.normal_parallel, log
    )
    atomic_json(
        status,
        {
            "terminal": False,
            "pid": os.getpid(),
            "cgroup": limits,
            "phase": "admission-is",
            "normal_rc": normal_rc,
        },
    )
    wait_for_admission(
        log, args.available_gib, args.swap_quiet_seconds, args.interval
    )
    is_rc = run_workflow(
        runtime, state_root, is_workflow, args.is_parallel, log
    )
    watcher_rc = watcher.wait()
    final = {
        "terminal": True,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "cgroup": limits,
        "phase": "terminal",
        "normal_rc": normal_rc,
        "is_rc": is_rc,
        "watcher_rc": watcher_rc,
    }
    atomic_json(status, final)
    append_log(
        log,
        f"manager terminal normal_rc={normal_rc} is_rc={is_rc} "
        f"watcher_rc={watcher_rc}",
    )
    return 0 if normal_rc == 0 and is_rc == 0 and watcher_rc == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
