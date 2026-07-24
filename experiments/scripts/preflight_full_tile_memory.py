#!/usr/bin/env python3
"""One-shot memory admission check for the DX100 full tile sweep.

This command is deliberately read-only. It neither changes systemd state nor
waits for the host to become safe.
"""

import argparse
import fnmatch
import json
import os
import subprocess
import sys
import time
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path

GIB_BYTES = 1 << 30
KIB_BYTES = 1 << 10
FINAL_SLICE_CAP_GIB = 272
SLICE_HIGH_GIB = 256
TRANSITION_LIMIT_GIB = 296
MIN_AVAILABLE_GIB = 50
LIVE_STATES = {"active", "activating", "reloading", "deactivating"}
OOM_EVENT_KEYS = ("max", "oom", "oom_kill", "oom_group_kill")
TARGET_SLICE = "dx100-full-tile-sweep.slice"
DEFAULT_LEGACY_PATTERNS = (
    "dx100-full-tile-*.service",
    "dx100-is-exit-gate-*.service",
)

DEFAULT_LEGACY_UNITS = (
    "dx100-full-tile-normal-retry-recovery2-20260721.service",
    "dx100-full-tile-auxiliary-retry-recovery2-20260721.service",
    "dx100-full-tile-t8-surge-recovery2-20260722.service",
    "dx100-full-tile-xrage64-recovery2-20260722.service",
    "dx100-full-tile-recovery2-20260721.service",
    "dx100-full-tile-is1k-parallel-recovery3-20260723.service",
    "dx100-full-tile-repair3-bfs1k-gate-20260723.service",
    "dx100-full-tile-repair3-ume16k-compat-20260723.service",
)


class PreflightError(RuntimeError):
    """The host state could not be checked safely."""


def parse_integer_table(text):
    values = {}
    for raw in text.splitlines():
        fields = raw.split()
        if len(fields) >= 2:
            try:
                values[fields[0].rstrip(":")] = int(fields[1])
            except ValueError:
                continue
    return values


def parse_meminfo(text):
    values = parse_integer_table(text)
    required = ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")
    missing = [key for key in required if key not in values]
    if missing:
        raise PreflightError(
            "missing /proc/meminfo fields: " + ", ".join(missing)
        )
    if values["SwapFree"] > values["SwapTotal"]:
        raise PreflightError("SwapFree exceeds SwapTotal")
    return {
        "mem_total_kib": values["MemTotal"],
        "mem_total_bytes": values["MemTotal"] * KIB_BYTES,
        "mem_available_kib": values["MemAvailable"],
        "mem_available_bytes": values["MemAvailable"] * KIB_BYTES,
        "swap_total_kib": values["SwapTotal"],
        "swap_current_kib": values["SwapTotal"] - values["SwapFree"],
        "swap_current_bytes": (
            values["SwapTotal"] - values["SwapFree"]
        )
        * KIB_BYTES,
    }


def ninety_percent_ceiling(mem_total_bytes):
    if mem_total_bytes <= 0:
        raise PreflightError("MemTotal must be positive")
    return Fraction(mem_total_bytes * 9, 10)


def exact_decimal(value):
    return str(Decimal(value.numerator) / Decimal(value.denominator))


def gib_decimal(byte_count):
    if isinstance(byte_count, Fraction):
        byte_count = (
            Decimal(byte_count.numerator)
            / Decimal(byte_count.denominator)
        )
    else:
        byte_count = Decimal(byte_count)
    return str(byte_count / Decimal(GIB_BYTES))


def parse_properties(text):
    properties = {}
    for raw in text.splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        properties[key] = value
    return properties


def memory_max_gib(value, unit):
    if value in {"", "infinity", "max"}:
        raise PreflightError(f"active legacy unit is uncapped: {unit}")
    try:
        byte_count = int(value)
    except ValueError as error:
        raise PreflightError(
            f"invalid MemoryMax for {unit}: {value!r}"
        ) from error
    if byte_count <= 0 or byte_count % GIB_BYTES:
        raise PreflightError(
            f"MemoryMax is not an exact binary GiB value for {unit}: "
            f"{byte_count}"
        )
    return byte_count // GIB_BYTES


def unit_from_properties(unit, properties):
    active_state = properties.get("ActiveState", "inactive")
    active = active_state in LIVE_STATES
    record = {
        "unit": unit,
        "load_state": properties.get("LoadState", "unknown"),
        "active_state": active_state,
        "active": active,
        "control_group": properties.get("ControlGroup", ""),
        "slice": properties.get("Slice", ""),
        "memory_current_bytes": None,
        "memory_max_bytes": None,
        "memory_max_gib": 0,
    }
    if not active:
        return record
    maximum = properties.get("MemoryMax", "")
    maximum_gib = memory_max_gib(maximum, unit)
    current = properties.get("MemoryCurrent", "")
    try:
        current_bytes = int(current)
    except ValueError as error:
        raise PreflightError(
            f"invalid MemoryCurrent for {unit}: {current!r}"
        ) from error
    if not record["control_group"].startswith("/"):
        raise PreflightError(f"active legacy unit lacks a cgroup: {unit}")
    record.update(
        {
            "memory_current_bytes": current_bytes,
            "memory_max_bytes": maximum_gib * GIB_BYTES,
            "memory_max_gib": maximum_gib,
        }
    )
    return record


def query_unit(unit, systemctl="systemctl"):
    completed = subprocess.run(
        [
            systemctl,
            "--user",
            "show",
            "--no-pager",
            "--property=LoadState",
            "--property=ActiveState",
            "--property=ControlGroup",
            "--property=MemoryCurrent",
            "--property=MemoryMax",
            "--property=Slice",
            unit,
        ],
        text=True,
        capture_output=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise PreflightError(
            f"systemctl show failed for {unit}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    return unit_from_properties(unit, parse_properties(completed.stdout))


def discover_legacy_unit_names(
    systemctl="systemctl",
    patterns=DEFAULT_LEGACY_PATTERNS,
):
    """Return active DX100 services that are not already in the target slice."""
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
        raise PreflightError(
            "systemctl list-units failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    names = []
    for raw in completed.stdout.splitlines():
        fields = raw.split()
        if not fields:
            continue
        name = fields[0]
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns):
            names.append(name)
    return sorted(set(names))


def discover_legacy_units(
    systemctl="systemctl",
    patterns=DEFAULT_LEGACY_PATTERNS,
):
    units = [
        query_unit(name, systemctl)
        for name in discover_legacy_unit_names(systemctl, patterns)
    ]
    return [
        unit
        for unit in units
        if unit["active"] and unit["slice"] != TARGET_SLICE
    ]


def collect_legacy_units(
    systemctl="systemctl",
    patterns=DEFAULT_LEGACY_PATTERNS,
    explicit_units=None,
):
    units_by_name = {
        unit["unit"]: unit
        for unit in discover_legacy_units(systemctl, patterns)
    }
    for name in explicit_units or ():
        unit = query_unit(name, systemctl)
        if not unit["active"] or unit["slice"] != TARGET_SLICE:
            units_by_name[name] = unit
    return [units_by_name[name] for name in sorted(units_by_name)]


def sample_vmstat(proc_root, interval_seconds, sleep=time.sleep):
    if interval_seconds < 0:
        raise PreflightError("swap sample interval cannot be negative")
    path = proc_root / "vmstat"
    first = parse_integer_table(path.read_text())
    if interval_seconds:
        sleep(interval_seconds)
    second = parse_integer_table(path.read_text())
    result = {}
    for key in ("pswpin", "pswpout"):
        if key not in first or key not in second:
            raise PreflightError(f"missing {key} in {path}")
        delta = second[key] - first[key]
        if delta < 0:
            raise PreflightError(f"{key} decreased during swap sample")
        result[key] = second[key]
        result[f"{key}_delta"] = delta
    return result


def parse_pressure(text, source):
    result = {}
    for raw in text.splitlines():
        fields = raw.split()
        if not fields:
            continue
        row = {}
        for field in fields[1:]:
            if "=" not in field:
                continue
            key, value = field.split("=", 1)
            row[key] = value
        if "avg10" not in row:
            raise PreflightError(f"missing avg10 in memory PSI: {source}")
        try:
            result[fields[0]] = Decimal(row["avg10"])
        except InvalidOperation as error:
            raise PreflightError(
                f"invalid memory PSI in {source}: {row['avg10']!r}"
            ) from error
    if "some" not in result or "full" not in result:
        raise PreflightError(f"incomplete memory PSI: {source}")
    return result


def read_integer(path):
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError) as error:
        raise PreflightError(f"cannot read integer from {path}") from error


def safe_cgroup_path(cgroup_root, control_group):
    root = cgroup_root.resolve()
    candidate = (root / control_group.lstrip("/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise PreflightError(
            f"cgroup escapes cgroup root: {control_group!r}"
        ) from error
    return candidate


def read_cgroup_health(
    label,
    path,
    expected_memory_max=None,
    enforce_swap=True,
    enforce_events=True,
    hierarchical_events=False,
):
    if not path.is_dir():
        raise PreflightError(f"active cgroup is absent: {label}: {path}")
    pressure_path = path / "memory.pressure"
    try:
        pressure = parse_pressure(pressure_path.read_text(), str(path))
    except OSError as error:
        raise PreflightError(f"cannot read {pressure_path}") from error
    events_path = path / "memory.events"
    if not hierarchical_events:
        local_events = path / "memory.events.local"
        if local_events.exists():
            events_path = local_events
    try:
        events = parse_integer_table(events_path.read_text())
    except OSError as error:
        raise PreflightError(f"cannot read {events_path}") from error
    missing = [key for key in OOM_EVENT_KEYS if key not in events]
    if missing:
        raise PreflightError(
            f"missing memory event fields for {label}: {', '.join(missing)}"
        )
    swap_current = read_integer(path / "memory.swap.current")
    cgroup_memory_max = (path / "memory.max").read_text().strip()
    if expected_memory_max is not None:
        try:
            cgroup_memory_max_bytes = int(cgroup_memory_max)
        except ValueError as error:
            raise PreflightError(
                f"uncapped or invalid cgroup memory.max for {label}: "
                f"{cgroup_memory_max!r}"
            ) from error
        if cgroup_memory_max_bytes != expected_memory_max:
            raise PreflightError(
                f"MemoryMax/cgroup mismatch for {label}: "
                f"systemd={expected_memory_max} "
                f"cgroup={cgroup_memory_max_bytes}"
            )
    return {
        "label": label,
        "path": str(path),
        "psi_some_avg10": pressure["some"],
        "psi_full_avg10": pressure["full"],
        "events": {key: events[key] for key in OOM_EVENT_KEYS},
        "events_path": str(events_path),
        "swap_current_bytes": swap_current,
        "enforce_swap": enforce_swap,
        "enforce_events": enforce_events,
    }


def evaluate(
    meminfo,
    vmstat,
    host_pressure,
    legacy_units,
    cgroup_health,
    proposed_slice_cap_gib=FINAL_SLICE_CAP_GIB,
):
    if proposed_slice_cap_gib < 0:
        raise PreflightError("proposed slice cap cannot be negative")
    active_units = [unit for unit in legacy_units if unit["active"]]
    legacy_sum_gib = sum(unit["memory_max_gib"] for unit in active_units)
    exact_90 = ninety_percent_ceiling(meminfo["mem_total_bytes"])
    exact_90_floor = exact_90.numerator // exact_90.denominator
    fixed_limit_bytes = TRANSITION_LIMIT_GIB * GIB_BYTES
    transition_limit_bytes = min(fixed_limit_bytes, exact_90_floor)
    remaining_bytes = max(
        0, transition_limit_bytes - legacy_sum_gib * GIB_BYTES
    )
    safe_slice_cap_gib = min(
        FINAL_SLICE_CAP_GIB, remaining_bytes // GIB_BYTES
    )
    proposed_bytes = proposed_slice_cap_gib * GIB_BYTES
    transition_sum_bytes = legacy_sum_gib * GIB_BYTES + proposed_bytes

    violations = []
    if legacy_sum_gib > TRANSITION_LIMIT_GIB:
        violations.append("active_legacy_hard_sum_exceeds_296_gib")
    if transition_sum_bytes > fixed_limit_bytes:
        violations.append("transition_hard_sum_exceeds_296_gib")
    if transition_sum_bytes > exact_90_floor:
        violations.append("transition_hard_sum_exceeds_host_90_percent")
    if meminfo["mem_available_bytes"] < MIN_AVAILABLE_GIB * GIB_BYTES:
        violations.append("mem_available_below_50_gib")
    warnings = []
    if meminfo["swap_current_bytes"]:
        warnings.append("host_swap_occupied")
    if vmstat.get("pswpin_delta", 0) or vmstat.get("pswpout_delta", 0):
        violations.append("host_swap_activity_nonzero")
    for kind in ("some", "full"):
        if host_pressure[kind] > 0:
            violations.append(f"host_memory_psi_{kind}_active")
    for health in cgroup_health:
        label = health["label"]
        if health["swap_current_bytes"]:
            if health.get("enforce_swap", True):
                violations.append(f"cgroup_swap_current_nonzero:{label}")
            else:
                warnings.append(f"cgroup_swap_occupied:{label}")
        for kind in ("some", "full"):
            if health[f"psi_{kind}_avg10"] > 0:
                violations.append(f"cgroup_memory_psi_{kind}_active:{label}")
        for event in OOM_EVENT_KEYS:
            if health["events"][event]:
                message = f"cgroup_memory_event:{label}:{event}"
                if health.get("enforce_events", True):
                    violations.append(message)
                else:
                    warnings.append(message)

    return {
        "ok": not violations,
        "host": {
            **meminfo,
            "mem_total_gib": gib_decimal(meminfo["mem_total_bytes"]),
            "mem_available_gib": gib_decimal(
                meminfo["mem_available_bytes"]
            ),
            "ninety_percent_ceiling_exact_bytes": exact_decimal(exact_90),
            "ninety_percent_ceiling_floor_bytes": exact_90_floor,
            "ninety_percent_ceiling_gib": gib_decimal(exact_90),
            "pswpin_pages": vmstat.get("pswpin", 0),
            "pswpout_pages": vmstat.get("pswpout", 0),
            "pswpin_delta_pages": vmstat.get("pswpin_delta", 0),
            "pswpout_delta_pages": vmstat.get("pswpout_delta", 0),
            "psi_some_avg10": str(host_pressure["some"]),
            "psi_full_avg10": str(host_pressure["full"]),
        },
        "legacy": {
            "configured_units": len(legacy_units),
            "active_units": len(active_units),
            "active_hard_sum_gib": legacy_sum_gib,
            "units": legacy_units,
        },
        "slice": {
            "memory_high_gib": SLICE_HIGH_GIB,
            "configured_cap_gib": FINAL_SLICE_CAP_GIB,
            "transition_limit_gib": TRANSITION_LIMIT_GIB,
            "proposed_cap_gib": proposed_slice_cap_gib,
            "safe_slice_cap_gib": safe_slice_cap_gib,
            "transition_hard_sum_gib": (
                legacy_sum_gib + proposed_slice_cap_gib
            ),
        },
        "warnings": warnings,
        "violations": violations,
    }


def collect_report(
    *,
    proc_root=Path("/proc"),
    cgroup_root=Path("/sys/fs/cgroup"),
    systemctl="systemctl",
    uid=None,
    explicit_legacy_units=None,
    legacy_patterns=DEFAULT_LEGACY_PATTERNS,
    proposed_slice_cap_gib=FINAL_SLICE_CAP_GIB,
    swap_sample_seconds=5.0,
    sleep=time.sleep,
):
    if uid is None:
        uid = os.getuid()
    meminfo = parse_meminfo((proc_root / "meminfo").read_text())
    vmstat = sample_vmstat(proc_root, swap_sample_seconds, sleep=sleep)
    host_pressure = parse_pressure(
        (proc_root / "pressure/memory").read_text(),
        str(proc_root / "pressure/memory"),
    )
    units = collect_legacy_units(
        systemctl, legacy_patterns, explicit_legacy_units
    )

    health = []
    user_cgroup = (
        cgroup_root
        / "user.slice"
        / f"user-{uid}.slice"
        / f"user@{uid}.service"
    )
    health.append(
        read_cgroup_health(
            "user-manager",
            user_cgroup,
            enforce_swap=False,
            enforce_events=False,
            hierarchical_events=True,
        )
    )
    for unit in units:
        if not unit["active"]:
            continue
        path = safe_cgroup_path(cgroup_root, unit["control_group"])
        health.append(
            read_cgroup_health(
                unit["unit"], path, unit["memory_max_bytes"]
            )
        )

    target = query_unit(TARGET_SLICE, systemctl)
    if target["active"]:
        path = safe_cgroup_path(cgroup_root, target["control_group"])
        health.append(
            read_cgroup_health(
                TARGET_SLICE,
                path,
                FINAL_SLICE_CAP_GIB * GIB_BYTES,
                hierarchical_events=True,
            )
        )

    return evaluate(
        meminfo,
        vmstat,
        host_pressure,
        units,
        health,
        proposed_slice_cap_gib,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--legacy-unit",
        action="append",
        dest="legacy_units",
        help=(
            "add an explicit legacy unit to auto-discovery (repeatable)"
        ),
    )
    parser.add_argument(
        "--legacy-pattern",
        action="append",
        dest="legacy_patterns",
        help=(
            "active service glob used by auto-discovery "
            "(repeatable; defaults to DX100 tile services)"
        ),
    )
    parser.add_argument(
        "--proposed-slice-cap-gib",
        type=int,
        default=FINAL_SLICE_CAP_GIB,
    )
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    parser.add_argument(
        "--cgroup-root", type=Path, default=Path("/sys/fs/cgroup")
    )
    parser.add_argument("--systemctl", default="systemctl")
    parser.add_argument("--uid", type=int, default=os.getuid())
    parser.add_argument(
        "--swap-sample-seconds",
        type=float,
        default=5.0,
        help="seconds over which to detect current pswpin/pswpout activity",
    )
    args = parser.parse_args(argv)

    try:
        report = collect_report(
            proc_root=args.proc_root,
            cgroup_root=args.cgroup_root,
            systemctl=args.systemctl,
            uid=args.uid,
            explicit_legacy_units=args.legacy_units,
            legacy_patterns=tuple(
                args.legacy_patterns or DEFAULT_LEGACY_PATTERNS
            ),
            proposed_slice_cap_gib=args.proposed_slice_cap_gib,
            swap_sample_seconds=args.swap_sample_seconds,
        )
    except (OSError, subprocess.SubprocessError, PreflightError) as error:
        print(
            json.dumps(
                {"ok": False, "error": str(error)},
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
