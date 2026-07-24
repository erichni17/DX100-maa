#!/usr/bin/env python3
"""Launch one transient service inside the aggregate full-tile memory slice.

The launcher serializes admission, verifies the installed slice, samples for
current swap activity, accounts for active legacy service caps, and then asks
the user systemd manager to start the service.  It never sets a runtime
timeout.
"""

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

import deploy_full_tile_slice as deployment
import preflight_full_tile_memory as memory

UNIT_RE = re.compile(r"^[A-Za-z0-9_.@:-]+$")


class LaunchError(RuntimeError):
    """A transient service was not safe to launch."""


class ActivationPending(LaunchError):
    """The requested unit has not finished entering its cgroup."""


def validate_request(unit, working_directory, high_gib, max_gib, command):
    if not unit or not UNIT_RE.fullmatch(unit) or "/" in unit:
        raise LaunchError(f"invalid transient unit name: {unit!r}")
    if high_gib <= 0 or max_gib <= 0:
        raise LaunchError("child memory limits must be positive")
    if high_gib > max_gib:
        raise LaunchError("child MemoryHigh cannot exceed MemoryMax")
    if max_gib > memory.FINAL_SLICE_CAP_GIB:
        raise LaunchError(
            "child MemoryMax cannot exceed the aggregate slice MemoryMax"
        )
    if not working_directory.is_dir():
        raise LaunchError(
            f"working directory does not exist: {working_directory}"
        )
    if not command:
        raise LaunchError("missing command after --")


def service_unit_name(unit):
    if unit.endswith(".service"):
        return unit
    if unit.endswith((".slice", ".scope", ".target", ".socket")):
        raise LaunchError("transient unit must be a service")
    return f"{unit}.service"


def build_systemd_run(
    *,
    systemd_run,
    unit,
    description,
    working_directory,
    high_gib,
    max_gib,
    command,
):
    return [
        systemd_run,
        "--user",
        "--collect",
        f"--slice={memory.TARGET_SLICE}",
        f"--unit={unit}",
        f"--description={description}",
        f"--working-directory={working_directory}",
        "--property=MemoryAccounting=yes",
        f"--property=MemoryHigh={high_gib}G",
        f"--property=MemoryMax={max_gib}G",
        "--property=MemorySwapMax=0",
        "--property=OOMPolicy=stop",
        "--property=KillMode=control-group",
        "--",
        *command,
    ]


def lock_path(uid=None):
    if uid is None:
        uid = os.getuid()
    runtime = Path("/run/user") / str(uid)
    try:
        runtime_stat = runtime.lstat()
    except OSError as error:
        raise LaunchError(
            f"user runtime directory is absent: {runtime}"
        ) from error
    if (
        not stat.S_ISDIR(runtime_stat.st_mode)
        or runtime_stat.st_uid != uid
        or runtime_stat.st_mode & 0o077
    ):
        raise LaunchError(
            f"user runtime directory is not private and owned: {runtime}"
        )
    if runtime.is_symlink():
        raise LaunchError(f"user runtime directory is absent: {runtime}")
    return runtime / "dx100-full-tile-sweep-admission.lock"


@contextmanager
def admission_lock(path=None, uid=None):
    if uid is None:
        uid = os.getuid()
    if path is None:
        path = lock_path(uid)
    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise LaunchError(f"cannot safely open admission lock: {path}") from error
    try:
        lock_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(lock_stat.st_mode)
            or lock_stat.st_uid != uid
            or lock_stat.st_mode & 0o077
        ):
            raise LaunchError(
                f"admission lock is not a private owned file: {path}"
            )
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def show_service(systemctl, unit):
    completed = subprocess.run(
        [
            systemctl,
            "--user",
            "show",
            "--no-pager",
            "--property=LoadState",
            "--property=ActiveState",
            "--property=Slice",
            "--property=ControlGroup",
            "--property=MemoryAccounting",
            "--property=MemoryHigh",
            "--property=MemoryMax",
            "--property=MemorySwapMax",
            unit,
        ],
        text=True,
        capture_output=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise LaunchError(
            f"systemctl show failed for {unit}: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    return memory.parse_properties(completed.stdout)


def require_unit_not_live(systemctl, unit):
    properties = show_service(systemctl, unit)
    if properties.get("ActiveState") in memory.LIVE_STATES:
        raise LaunchError(f"refusing duplicate live unit: {unit}")
    return properties


def read_exact_limit(cgroup, filename, expected):
    path = cgroup / filename
    try:
        value = int(path.read_text().strip())
    except (OSError, ValueError) as error:
        raise LaunchError(f"cannot verify {path}") from error
    if value != expected:
        raise LaunchError(
            f"{path} is {value}, expected {expected}"
        )


def verify_started_service_once(
    *,
    systemctl,
    unit,
    cgroup_root,
    high_gib,
    max_gib,
):
    properties = show_service(systemctl, unit)
    if properties.get("LoadState") != "loaded":
        raise ActivationPending(
            f"started unit is not loaded: {properties.get('LoadState')}"
        )
    active_state = properties.get("ActiveState")
    if active_state in {"activating", "reloading"}:
        raise ActivationPending(
            f"started unit is still {active_state}"
        )
    if active_state != "active":
        raise LaunchError(
            f"started unit is not active: {active_state}"
        )
    if properties.get("Slice") != memory.TARGET_SLICE:
        raise LaunchError(
            f"started unit escaped target slice: "
            f"{properties.get('Slice')!r}"
        )
    if properties.get("MemoryAccounting") != "yes":
        raise LaunchError("started unit lacks MemoryAccounting=yes")
    expected = {
        "MemoryHigh": high_gib * memory.GIB_BYTES,
        "MemoryMax": max_gib * memory.GIB_BYTES,
        "MemorySwapMax": 0,
    }
    for key, value in expected.items():
        try:
            actual = int(properties.get(key, ""))
        except ValueError as error:
            raise LaunchError(
                f"started unit has invalid {key}: "
                f"{properties.get(key)!r}"
            ) from error
        if actual != value:
            raise LaunchError(
                f"started unit has {key}={actual}, expected {value}"
            )
    relative = properties.get("ControlGroup", "")
    if not relative.startswith("/"):
        raise LaunchError("started unit lacks a cgroup")
    cgroup = memory.safe_cgroup_path(cgroup_root, relative)
    if not cgroup.is_dir():
        raise ActivationPending(f"started cgroup is absent: {cgroup}")
    read_exact_limit(cgroup, "memory.high", expected["MemoryHigh"])
    read_exact_limit(cgroup, "memory.max", expected["MemoryMax"])
    read_exact_limit(cgroup, "memory.swap.max", 0)
    return {
        "unit": unit,
        "active_state": "active",
        "slice": memory.TARGET_SLICE,
        "control_group": relative,
        "memory_high_gib": high_gib,
        "memory_max_gib": max_gib,
        "memory_swap_max_bytes": 0,
    }


def verify_aggregate_slice(systemctl, cgroup_root):
    properties = show_service(systemctl, memory.TARGET_SLICE)
    if properties.get("LoadState") != "loaded":
        raise LaunchError("aggregate slice is not loaded after launch")
    if properties.get("ActiveState") != "active":
        raise LaunchError("aggregate slice is not active after launch")
    if properties.get("MemoryAccounting") != "yes":
        raise LaunchError("aggregate slice lacks MemoryAccounting=yes")
    expected = {
        "MemoryHigh": memory.SLICE_HIGH_GIB * memory.GIB_BYTES,
        "MemoryMax": memory.FINAL_SLICE_CAP_GIB * memory.GIB_BYTES,
        "MemorySwapMax": 0,
    }
    for key, value in expected.items():
        try:
            actual = int(properties.get(key, ""))
        except ValueError as error:
            raise LaunchError(
                f"aggregate slice has invalid {key}: "
                f"{properties.get(key)!r}"
            ) from error
        if actual != value:
            raise LaunchError(
                f"aggregate slice has {key}={actual}, expected {value}"
            )
    relative = properties.get("ControlGroup", "")
    if not relative.startswith("/"):
        raise LaunchError("aggregate slice lacks a cgroup")
    cgroup = memory.safe_cgroup_path(cgroup_root, relative)
    if not cgroup.is_dir():
        raise LaunchError(f"aggregate cgroup is absent: {cgroup}")
    read_exact_limit(cgroup, "memory.high", expected["MemoryHigh"])
    read_exact_limit(cgroup, "memory.max", expected["MemoryMax"])
    read_exact_limit(cgroup, "memory.swap.max", 0)
    return {
        "unit": memory.TARGET_SLICE,
        "active_state": "active",
        "control_group": relative,
        "memory_high_gib": memory.SLICE_HIGH_GIB,
        "memory_max_gib": memory.FINAL_SLICE_CAP_GIB,
        "memory_swap_max_bytes": 0,
    }


def verify_started_service(
    *,
    systemctl,
    unit,
    cgroup_root,
    high_gib,
    max_gib,
    timeout_seconds,
    sleep=time.sleep,
):
    if timeout_seconds <= 0:
        raise LaunchError("activation verification timeout must be positive")
    deadline = time.monotonic() + timeout_seconds
    last_error = None
    while time.monotonic() < deadline:
        try:
            return verify_started_service_once(
                systemctl=systemctl,
                unit=unit,
                cgroup_root=cgroup_root,
                high_gib=high_gib,
                max_gib=max_gib,
            )
        except ActivationPending as error:
            last_error = error
            sleep(min(0.25, max(0, deadline - time.monotonic())))
    raise LaunchError(
        f"unit containment was not verified within {timeout_seconds}s: "
        f"{last_error}"
    )


def stop_owned_unit(systemctl, unit):
    completed = subprocess.run(
        [systemctl, "--user", "stop", unit],
        text=True,
        capture_output=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise LaunchError(
            f"compensating stop failed for {unit}: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )


def run_locked(args, command):
    service_unit = service_unit_name(args.unit)
    with admission_lock():
        require_unit_not_live(args.systemctl, service_unit)
        installed = deployment.verify_loaded_unit(
            args.systemctl,
            deployment.default_user_unit_directory()
            / memory.TARGET_SLICE,
        )
        report = memory.collect_report(
            cgroup_root=args.cgroup_root,
            systemctl=args.systemctl,
            explicit_legacy_units=args.legacy_units,
            legacy_patterns=tuple(
                args.legacy_patterns or memory.DEFAULT_LEGACY_PATTERNS
            ),
            proposed_slice_cap_gib=memory.FINAL_SLICE_CAP_GIB,
            swap_sample_seconds=args.swap_sample_seconds,
        )
        if not report["ok"]:
            raise LaunchError(
                "memory admission refused: "
                + ", ".join(report["violations"])
            )
        invocation = build_systemd_run(
            systemd_run=args.systemd_run,
            unit=args.unit,
            description=args.description,
            working_directory=args.working_directory.resolve(),
            high_gib=args.memory_high_gib,
            max_gib=args.memory_max_gib,
            command=command,
        )
        if args.dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "slice": installed,
                "preflight": report,
                "argv": invocation,
            }
        completed = subprocess.run(
            invocation,
            text=True,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise LaunchError(
                "systemd-run failed: "
                + (completed.stderr.strip() or completed.stdout.strip())
            )
        try:
            containment = verify_started_service(
                systemctl=args.systemctl,
                unit=service_unit,
                cgroup_root=args.cgroup_root,
                high_gib=args.memory_high_gib,
                max_gib=args.memory_max_gib,
                timeout_seconds=args.activation_timeout_seconds,
            )
            aggregate = verify_aggregate_slice(
                args.systemctl, args.cgroup_root
            )
            postflight = memory.collect_report(
                cgroup_root=args.cgroup_root,
                systemctl=args.systemctl,
                explicit_legacy_units=args.legacy_units,
                legacy_patterns=tuple(
                    args.legacy_patterns
                    or memory.DEFAULT_LEGACY_PATTERNS
                ),
                proposed_slice_cap_gib=memory.FINAL_SLICE_CAP_GIB,
                swap_sample_seconds=args.swap_sample_seconds,
            )
            if not postflight["ok"]:
                raise LaunchError(
                    "post-launch memory admission failed: "
                    + ", ".join(postflight["violations"])
                )
        except (
            LaunchError,
            memory.PreflightError,
            OSError,
            subprocess.SubprocessError,
        ) as error:
            try:
                stop_owned_unit(args.systemctl, service_unit)
            except LaunchError as stop_error:
                raise LaunchError(f"{error}; {stop_error}") from error
            raise LaunchError(
                f"{error}; newly launched unit was stopped"
            ) from error
        return {
            "ok": True,
            "dry_run": False,
            "unit": args.unit,
            "slice": memory.TARGET_SLICE,
            "preflight": report,
            "containment": containment,
            "aggregate_containment": aggregate,
            "postflight": postflight,
            "systemd_run_stdout": completed.stdout.strip(),
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument(
        "--working-directory", required=True, type=Path
    )
    parser.add_argument("--memory-high-gib", required=True, type=int)
    parser.add_argument("--memory-max-gib", required=True, type=int)
    parser.add_argument("--systemctl", default="systemctl")
    parser.add_argument("--systemd-run", default="systemd-run")
    parser.add_argument(
        "--cgroup-root",
        type=Path,
        default=Path("/sys/fs/cgroup"),
    )
    parser.add_argument(
        "--activation-timeout-seconds",
        type=float,
        default=30.0,
        help="control-plane verification window; not a simulation timeout",
    )
    parser.add_argument(
        "--swap-sample-seconds", type=float, default=5.0
    )
    parser.add_argument(
        "--legacy-unit",
        action="append",
        dest="legacy_units",
        help=(
            "add an explicit legacy service to auto-discovery "
            "(repeatable)"
        ),
    )
    parser.add_argument(
        "--legacy-pattern",
        action="append",
        dest="legacy_patterns",
        help="active legacy service glob for auto-discovery (repeatable)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="perform all live checks but do not call systemd-run",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    try:
        validate_request(
            args.unit,
            args.working_directory,
            args.memory_high_gib,
            args.memory_max_gib,
            command,
        )
        result = run_locked(args, command)
    except (
        LaunchError,
        deployment.DeploymentError,
        memory.PreflightError,
        OSError,
        subprocess.SubprocessError,
    ) as error:
        print(
            json.dumps(
                {"ok": False, "error": str(error)},
                indent=2,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
