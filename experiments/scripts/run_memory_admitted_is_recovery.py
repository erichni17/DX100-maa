#!/usr/bin/env python3
"""Admit full-class NAS IS tasks from measured memory headroom.

There is no fixed task-count limit. Active full-tile services keep the unused
part of their cgroup hard limit as a reservation. A new IS task reserves 64
binary GiB and starts only if the projected host headroom still exceeds ten
percent of physical RAM. Every admitted task gets an independent zero-swap,
60/64-GiB systemd service.

The controller is compatible with the already-live serial final workflow. It
selects far-end tiles first; run_is_smoke.sh holds an output-specific flock and
independently validates completed artifacts before reuse, so the authoritative
workflow cannot duplicate one of these simulations.
"""

import argparse
import fcntl
import json
import math
import os
import re
import subprocess
import time
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path

GIB_BYTES = 1 << 30
KIB_BYTES = 1 << 10
LIVE_STATES = {"active", "activating", "reloading", "deactivating"}
SUPPORT_UNIT_MARKERS = (
    "admitter",
    "completion-watcher",
    "memory-telemetry",
    "recorder",
)


class AdmissionError(RuntimeError):
    """The host or service state cannot safely admit another task."""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def append_log(path, message):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as output:
        output.write(f"{now_iso()} {message}\n")


def parse_integer_table(text):
    values = {}
    for raw in text.splitlines():
        fields = raw.split()
        if len(fields) < 2:
            continue
        try:
            values[fields[0].rstrip(":")] = int(fields[1])
        except ValueError:
            continue
    return values


def read_meminfo(proc_root=Path("/proc")):
    values = parse_integer_table((proc_root / "meminfo").read_text())
    try:
        return {
            "total_bytes": values["MemTotal"] * KIB_BYTES,
            "available_bytes": values["MemAvailable"] * KIB_BYTES,
        }
    except KeyError as error:
        raise AdmissionError(
            f"missing meminfo field: {error.args[0]}"
        ) from error


def read_swap_counters(proc_root=Path("/proc")):
    values = parse_integer_table((proc_root / "vmstat").read_text())
    try:
        return values["pswpin"], values["pswpout"]
    except KeyError as error:
        raise AdmissionError(
            f"missing vmstat field: {error.args[0]}"
        ) from error


def sample_swap(proc_root, seconds, sleeper=time.sleep):
    first = read_swap_counters(proc_root)
    if seconds:
        sleeper(seconds)
    second = read_swap_counters(proc_root)
    deltas = (second[0] - first[0], second[1] - first[1])
    if min(deltas) < 0:
        raise AdmissionError("swap counters decreased during admission sample")
    return {"pswpin_delta": deltas[0], "pswpout_delta": deltas[1]}


def parse_pressure(text, label):
    result = {}
    for raw in text.splitlines():
        fields = raw.split()
        if not fields:
            continue
        values = dict(
            field.split("=", 1) for field in fields[1:] if "=" in field
        )
        if "avg10" in values:
            result[fields[0]] = float(values["avg10"])
    if "some" not in result or "full" not in result:
        raise AdmissionError(f"incomplete memory PSI: {label}")
    return result


def read_host_pressure(proc_root=Path("/proc")):
    path = proc_root / "pressure/memory"
    return parse_pressure(path.read_text(), str(path))


def parse_properties(text):
    properties = {}
    for raw in text.splitlines():
        if "=" in raw:
            key, value = raw.split("=", 1)
            properties[key] = value
    return properties


def systemctl_show(unit, systemctl="systemctl"):
    completed = subprocess.run(
        [
            systemctl,
            "--user",
            "show",
            "--no-pager",
            "-p",
            "ActiveState",
            "-p",
            "ControlGroup",
            "-p",
            "MemoryCurrent",
            "-p",
            "MemoryPeak",
            "-p",
            "MemoryMax",
            "-p",
            "MemorySwapCurrent",
            unit,
        ],
        text=True,
        capture_output=True,
        timeout=10,
    )
    if completed.returncode != 0:
        return {
            "unit": unit,
            "active": False,
            "active_state": "not-found",
        }
    properties = parse_properties(completed.stdout)
    active_state = properties.get("ActiveState", "inactive")

    def integer(name, default=0):
        value = properties.get(name, str(default))
        if value in {"", "infinity", "max"}:
            return None
        try:
            return int(value)
        except ValueError as error:
            raise AdmissionError(
                f"invalid {name} for {unit}: {value!r}"
            ) from error

    return {
        "unit": unit,
        "active": active_state in LIVE_STATES,
        "active_state": active_state,
        "control_group": properties.get("ControlGroup", ""),
        "memory_current_bytes": integer("MemoryCurrent"),
        "memory_peak_bytes": integer("MemoryPeak"),
        "memory_max_bytes": integer("MemoryMax"),
        "memory_swap_current_bytes": integer("MemorySwapCurrent"),
    }


def list_active_full_tile_units(systemctl="systemctl"):
    completed = subprocess.run(
        [
            systemctl,
            "--user",
            "list-units",
            "--type=service",
            "--state=active,activating,reloading,deactivating",
            "--plain",
            "--no-legend",
            "--no-pager",
        ],
        text=True,
        capture_output=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise AdmissionError(
            "systemctl list-units failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    names = []
    for raw in completed.stdout.splitlines():
        fields = raw.split()
        if not fields:
            continue
        name = fields[0]
        if not name.startswith("dx100-full-tile-"):
            continue
        if any(marker in name for marker in SUPPORT_UNIT_MARKERS):
            continue
        names.append(name)
    return sorted(set(names))


def collect_reservations(systemctl="systemctl"):
    reservations = []
    for name in list_active_full_tile_units(systemctl):
        unit = systemctl_show(name, systemctl)
        if not unit.get("active"):
            continue
        maximum = unit.get("memory_max_bytes")
        current = unit.get("memory_current_bytes")
        if maximum is None or current is None:
            raise AdmissionError(f"active simulation unit is uncapped: {name}")
        if unit.get("memory_swap_current_bytes"):
            raise AdmissionError(f"active simulation unit uses swap: {name}")
        root = cgroup_path(unit.get("control_group", ""))
        events_path = root / "memory.events.local"
        if not events_path.exists():
            events_path = root / "memory.events"
        events = parse_integer_table(events_path.read_text())
        pressure_path = root / "memory.pressure"
        pressure = parse_pressure(
            pressure_path.read_text(), str(pressure_path)
        )
        bad_events = {
            key: events.get(key, 0)
            for key in ("max", "oom", "oom_kill", "oom_group_kill")
            if events.get(key, 0)
        }
        if bad_events:
            raise AdmissionError(
                f"active simulation unit has memory events: "
                f"{name}: {bad_events}"
            )
        if pressure["some"] > 0 or pressure["full"] > 0:
            raise AdmissionError(
                f"active simulation unit has memory PSI: "
                f"{name}: {pressure}"
            )
        unit["memory_events"] = events
        unit["memory_pressure"] = pressure
        unit["unconsumed_bytes"] = max(0, maximum - current)
        reservations.append(unit)
    return reservations


def compute_admission(
    meminfo, reservations, per_task_gib=64, reserve_percent=10
):
    if per_task_gib <= 0 or not 0 < reserve_percent < 100:
        raise AdmissionError("invalid admission budget")
    total = meminfo["total_bytes"]
    available = meminfo["available_bytes"]
    host_reserve = math.ceil(total * reserve_percent / 100)
    outstanding = sum(item["unconsumed_bytes"] for item in reservations)
    free_for_new = available - host_reserve - outstanding
    per_task = per_task_gib * GIB_BYTES
    slots = max(0, free_for_new // per_task)
    return {
        "mem_total_bytes": total,
        "mem_available_bytes": available,
        "host_reserve_bytes": host_reserve,
        "active_unconsumed_reservations_bytes": outstanding,
        "free_for_new_reservations_bytes": max(0, free_for_new),
        "per_task_reservation_bytes": per_task,
        "admissible_new_tasks": int(slots),
        "active_units": reservations,
    }


def cgroup_path(control_group):
    if not control_group.startswith("/"):
        raise AdmissionError(f"unsafe cgroup path: {control_group!r}")
    root = Path("/sys/fs/cgroup").resolve()
    candidate = (root / control_group.lstrip("/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise AdmissionError(
            f"cgroup path escapes root: {control_group!r}"
        ) from error
    return candidate


def tile_from_unit(unit_name):
    match = re.search(r"-t([0-9]+)-a[0-9]+\.service$", unit_name)
    if not match:
        raise AdmissionError(f"cannot recover tile from unit: {unit_name}")
    return int(match.group(1))


def record_telemetry(run_root, unit):
    control_group = unit.get("control_group", "")
    if not control_group:
        return
    root = cgroup_path(control_group)
    if not root.is_dir():
        return
    events_path = root / "memory.events.local"
    if not events_path.exists():
        events_path = root / "memory.events"
    events = parse_integer_table(events_path.read_text())
    current = int((root / "memory.current").read_text().strip())
    peak = int((root / "memory.peak").read_text().strip())
    swap_current = int((root / "memory.swap.current").read_text().strip())
    tile = tile_from_unit(unit["unit"])
    output = run_root / f"final-is-memory-t{tile}-cgroup.tsv"
    if not output.exists():
        output.write_text(
            "timestamp\tcurrent_bytes\tpeak_bytes\tswap_current_bytes\t"
            "high_events\tmax_events\toom_events\toom_kill_events\n"
        )
    with output.open("a") as stream:
        stream.write(
            "\t".join(
                str(value)
                for value in (
                    now_iso(),
                    current,
                    peak,
                    swap_current,
                    events.get("high", 0),
                    events.get("max", 0),
                    events.get("oom", 0),
                    events.get("oom_kill", 0),
                )
            )
            + "\n"
        )


def completed_artifact(run_root, tile, binary_sha):
    output = (
        run_root
        / "final-recovery/is"
        / f"t{tile}_gem5.opt.ovl_base_sha256_{binary_sha}"
    )
    log = output / "run.log"
    stats = output / "stats.txt"
    if not log.is_file() or not stats.is_file():
        return False
    if not log.stat().st_size or not stats.stat().st_size:
        return False
    text = log.read_text(errors="replace")
    required = (
        "DX100_ROI_ONLY_ANCHORED workload=nas-is-full",
        "IS_ROI_EXIT_POLICY dump_stats_anchor_m5_exit",
        "m5_exit instruction encountered",
    )
    if not all(marker in text for marker in required):
        return False
    if "panic:" in text or "fatal:" in text or "result=FAIL" in text:
        return False
    stats_text = stats.read_text(errors="replace")
    return any(
        line.startswith("simTicks ") for line in stats_text.splitlines()
    )


def launch_task(args, tile, unit):
    source = args.source_root
    ramulator_root = args.runtime_root / "ext/ramulator2/ramulator2"
    command = [
        args.systemd_run,
        "--user",
        "--no-block",
        "--collect",
        f"--unit={unit.removesuffix('.service')}",
        f"--description=DX100-memory-admitted-IS-{tile}",
        f"--working-directory={source}",
        "--property=MemoryHigh=60G",
        "--property=MemoryMax=64G",
        "--property=MemorySwapMax=0",
        "--property=MemoryAccounting=yes",
        "--property=CPUAccounting=yes",
        "--property=OOMPolicy=stop",
        "--property=KillMode=control-group",
        "--setenv=DX100_SIMULATION_LAUNCH_APPROVED=YES",
        "--setenv=DX100_SIMULATION_PLAN_VERSION=tile-final-recovery-v3",
        f"--setenv=CAMPAIGN_ROOT={args.run_root / 'final-recovery/is'}",
        f"--setenv=CHECKPOINT_ROOT={args.checkpoint_root}",
        f"--setenv=DX100_GEM5_BIN={args.gem5_bin}",
        "--setenv=DX100_POST_ROI_MODE=anchored",
        "--setenv=OMP_NUM_THREADS=4",
        "--setenv=OMP_PROC_BIND=false",
        f"--setenv=DX100_RUNTIME_ROOT={args.runtime_root}",
        f"--setenv=DX100_SOURCE_ROOT={source}",
        (
            "--setenv=DX100_RAMULATOR_CONFIG="
            f"{ramulator_root / 'example_gem5_config.yaml'}"
        ),
        f"--setenv=DX100_RAMULATOR_LIBDIR={ramulator_root}",
        (
            "--setenv=DX100_SE_CONFIG="
            f"{args.runtime_root / 'configs/deprecated/example/se.py'}"
        ),
        str(
            source
            / "experiments/scripts/require_simulation_launch_approval.sh"
        ),
        "/usr/bin/numactl",
        "--cpunodebind=1",
        "--preferred=1",
        str(source / "benchmarks/NAS/is/run_is_smoke.sh"),
        "gem5.opt.ovl_base",
        str(tile),
        "0",
        "0",
        "0",
        "0",
    ]
    completed = subprocess.run(
        command, text=True, capture_output=True, timeout=30
    )
    if completed.returncode != 0:
        raise AdmissionError(
            f"systemd launch failed for tile {tile}: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    return command


def initial_state(args):
    return {
        "schema_version": 1,
        "created_at": now_iso(),
        "terminal": False,
        "policy": {
            "fixed_task_limit": None,
            "per_is_reservation_gib": args.per_task_gib,
            "host_reserve_percent": args.reserve_percent,
            "memory_high_gib": 60,
            "memory_max_gib": 64,
            "memory_swap_max_gib": 0,
        },
        "tasks": {
            str(tile): {"state": "pending", "attempts": 0}
            for tile in args.tiles
        },
    }


def classify_task_state(task, artifact_complete, unit):
    """Prefer a live owned process over a concurrently completed artifact."""
    if unit and unit.get("active"):
        return "running"
    if artifact_complete:
        return "completed"
    if task.get("unit") and task.get("attempts", 0):
        return "failed"
    return task["state"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--checkpoint-root", type=Path, required=True)
    parser.add_argument("--gem5-bin", type=Path, required=True)
    parser.add_argument("--binary-sha", required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument(
        "--tiles", type=int, nargs="+", default=[65536, 32768, 8192, 4096]
    )
    parser.add_argument("--per-task-gib", type=int, default=64)
    parser.add_argument("--reserve-percent", type=int, default=10)
    parser.add_argument("--poll-seconds", type=float, default=30)
    parser.add_argument("--swap-sample-seconds", type=float, default=5)
    parser.add_argument(
        "--unit-prefix",
        default="dx100-full-tile-final-is-memory-20260727",
    )
    parser.add_argument("--systemctl", default="systemctl")
    parser.add_argument("--systemd-run", default="systemd-run")
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    parser.add_argument(
        "--lock",
        type=Path,
        default=Path(
            f"/run/user/{os.getuid()}/dx100-is-memory-admission.lock"
        ),
    )
    parser.add_argument(
        "--once", action="store_true", help="report without launching"
    )
    args = parser.parse_args(argv)

    args.run_root = args.run_root.resolve()
    args.source_root = args.source_root.resolve()
    args.runtime_root = args.runtime_root.resolve()
    args.checkpoint_root = args.checkpoint_root.resolve()
    args.gem5_bin = args.gem5_bin.resolve()
    args.state = args.state.resolve()
    log = args.state.with_suffix(".log")
    for path in (
        args.source_root,
        args.runtime_root,
        args.checkpoint_root,
        args.gem5_bin,
    ):
        if not path.exists():
            raise SystemExit(f"required path is absent: {path}")

    state = (
        json.loads(args.state.read_text())
        if args.state.exists()
        else initial_state(args)
    )
    args.lock.parent.mkdir(parents=True, exist_ok=True)

    while True:
        for tile_text, task in state["tasks"].items():
            if task["state"] == "completed":
                continue
            tile = int(tile_text)
            unit_name = task.get("unit")
            unit = (
                systemctl_show(unit_name, args.systemctl)
                if unit_name
                else None
            )
            artifact_complete = completed_artifact(
                args.run_root, tile, args.binary_sha
            )
            next_state = classify_task_state(task, artifact_complete, unit)
            if next_state == "running":
                task["state"] = "running"
                record_telemetry(args.run_root, unit)
            elif next_state == "completed":
                task["state"] = "completed"
                task["finished_at"] = now_iso()
            elif next_state == "failed":
                task["state"] = "failed"
                task["finished_at"] = now_iso()

        # JSON is written with sorted keys for deterministic provenance, so
        # dictionary iteration cannot preserve the requested launch order.
        pending = [
            tile
            for tile in args.tiles
            if state["tasks"][str(tile)]["state"] == "pending"
        ]
        running = [
            tile
            for tile in args.tiles
            if state["tasks"][str(tile)]["state"] == "running"
        ]
        if not pending and not running:
            state["terminal"] = True
            state["finished_at"] = now_iso()
            atomic_json(args.state, state)
            return (
                0
                if all(
                    task["state"] == "completed"
                    for task in state["tasks"].values()
                )
                else 1
            )

        with args.lock.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                meminfo = read_meminfo(args.proc_root)
                swap = sample_swap(args.proc_root, args.swap_sample_seconds)
                pressure = read_host_pressure(args.proc_root)
                reservations = collect_reservations(args.systemctl)
                report = compute_admission(
                    meminfo,
                    reservations,
                    args.per_task_gib,
                    args.reserve_percent,
                )
                report.update(
                    {
                        "timestamp": now_iso(),
                        "swap": swap,
                        "host_pressure": pressure,
                    }
                )
                blocked = bool(
                    swap["pswpin_delta"]
                    or swap["pswpout_delta"]
                    or pressure["some"] > 0
                    or pressure["full"] > 0
                )
                report["blocked_by_pressure"] = blocked
                state["last_admission"] = report
                atomic_json(args.state, state)
                if args.once:
                    print(json.dumps(report, indent=2, sort_keys=True))
                    return (
                        0
                        if (report["admissible_new_tasks"] and not blocked)
                        else 1
                    )
                slots = 0 if blocked else report["admissible_new_tasks"]
                # Admit only one task from each measurement.  The next loop
                # observes the new service's live consumption and remaining
                # cgroup reservation before admitting another task.  This
                # avoids turning a multi-slot calculation into a stale batch
                # decision while still allowing arbitrary concurrency as
                # memory becomes available.
                for tile in pending[: min(slots, 1)]:
                    task = state["tasks"][str(tile)]
                    attempt = task["attempts"] + 1
                    unit = f"{args.unit_prefix}-t{tile}-a{attempt}.service"
                    launch_task(args, tile, unit)
                    task.update(
                        {
                            "state": "running",
                            "attempts": attempt,
                            "unit": unit,
                            "started_at": now_iso(),
                        }
                    )
                    append_log(
                        log,
                        f"launched tile={tile} unit={unit} "
                        f"admissible_slots={slots}",
                    )
                    atomic_json(args.state, state)
            except (
                OSError,
                subprocess.SubprocessError,
                AdmissionError,
            ) as error:
                state["last_admission"] = {
                    "timestamp": now_iso(),
                    "blocked": True,
                    "error": str(error),
                }
                atomic_json(args.state, state)
                append_log(log, f"admission blocked error={error}")
                if args.once:
                    print(
                        json.dumps(
                            state["last_admission"],
                            indent=2,
                            sort_keys=True,
                        )
                    )
                    return 2

        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
