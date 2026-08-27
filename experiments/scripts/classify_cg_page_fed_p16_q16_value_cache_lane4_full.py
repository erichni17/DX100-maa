#!/usr/bin/env python3
"""Create or validate the read-only full-CG p16/q16 lane-4 certificate.

The historical raw root is immutable.  This classifier launches no gem5
process and writes only four sealed files into a fresh external certificate
directory.  It proves that the registered service reached the obsolete
post-restore high-water check, then independently replays provenance,
numerical, mechanism, storage, retained-line, lane, and lane-1 certificate
checks before computing the first-ROI lane-1/lane-4 ratio.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
from decimal import (
    Decimal,
    localcontext,
)
from pathlib import Path
from typing import (
    Any,
    Iterable,
)

ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = (
    ROOT / "experiments/scripts/"
    "run_cg_page_fed_p16_q16_value_cache_lane4_full.py"
)
SPEC = importlib.util.spec_from_file_location(
    "cg_page_fed_p16q16_lane4_successor_runner", RUNNER_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load lane-4 full runner: {RUNNER_PATH}")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

RUNS_ROOT = Path("/data1/nier/dx100-runs")
RAW_ROOT = RUNS_ROOT / (
    "2026-08-26-cg-page-fed-p16q16-value-cache-lane4-full-r1"
)
LANE1_ROOT = runner.ACCEPTED_LANE1_ROOT
NUMERICAL_AUTHORITY_ROOT = runner.base.CERTIFICATE_ROOT
LANE_SELECTION_ROOT = runner.LANE_SELECTION_ROOT
DEFAULT_CERTIFICATE_ROOT = RUNS_ROOT / (
    "2026-08-26-cg-page-fed-p16q16-value-cache-lane4-full-" "certificate-r3"
)

SERVICE_UNIT = "dx100-cg-page-fed-p16q16-value-cache-lane4-full-r1.service"
SERVICE_INVOCATION_ID = "997f720c6de149aa83fee7ce5f75e81b"
SERVICE_BOOT_ID = "b2060699a6dc49eb926042ccb3779afe"
SERVICE_START_MONOTONIC_US = 3_121_120_430_696
SERVICE_EXIT_MONOTONIC_US = 3_132_223_044_057
REGISTERED_MAIN_PID = 3_758_672
REGISTERED_MAIN_START_TICKS = 312_112_040
REGISTERED_COMMAND = (
    "/usr/bin/python3 "
    "/data1/nier/worktrees/codex-sessions/"
    "hybrid-p16q16-lane4-full-20260826-20260826-162940-d020cacd/"
    "DX100-virtualization-selected-integration-cont-20260826/"
    "experiments/scripts/"
    "run_cg_page_fed_p16_q16_value_cache_lane4_full.py " + str(RAW_ROOT)
)

RAW_SOURCE_COMMIT = "f20d2412c434a0ecba27634179fac09a1771ce88"
RAW_MANIFEST_SCHEMA = "dx100.cg.page_fed_p16_q16_value_cache_lane4_full.v1"
MANIFEST_SCHEMA = (
    "dx100.cg.page_fed_p16_q16_value_cache_lane4_full_" "successor_manifest.v1"
)
CERTIFICATE_SCHEMA = (
    "dx100.cg.page_fed_p16_q16_value_cache_lane4_full_"
    "successor_certificate.v1"
)
VERDICT = "PASS_NUMERICAL_MECHANISM_CORRECT"
FIRST_ROI_SIMTICKS = 158_381_418_273
LANE1_SIMTICKS = 162_849_334_269
OBSERVED_ACTIVE_LANES = 43_840
OBSERVED_APPLY_HIGH_WATER = 43_478

RAW_PINNED_FILES = {
    "manifest": (
        RAW_ROOT / "manifest.json",
        "e112bbf706fe9dbff31f81f9a18d57268bbb92d03811a8bf451ae822434e93fd",
    ),
    "checkpoint_log": (
        RAW_ROOT / "checkpoint.log",
        "f942e41fa4f6c2e91ea303d26fb6e0786b1ba17c82686549432dea1ab8ffd541",
    ),
    "checkpoint_exit": (
        RAW_ROOT / "checkpoint.log.exit",
        "9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa",
    ),
    "restore_log": (
        RAW_ROOT / "run/restore.log",
        "153f5efbd0e6a55903643d50d7d3eebbd38c0ffee425613766c7a3b3db188ce9",
    ),
    "restore_exit": (
        RAW_ROOT / "run/restore.log.exit",
        "9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa",
    ),
    "stats": (
        RAW_ROOT / "run/stats.txt",
        "5939e068aef15b4a1e94a09d38a005112357fc2fc587695663c82fd0e2eb573a",
    ),
    "config_ini": (
        RAW_ROOT / "run/config.ini",
        "d4e13ad106136bbd484ff0de39ab812074351a015f1c9ce68451db7116a63a7d",
    ),
    "config_json": (
        RAW_ROOT / "run/config.json",
        "b190ea15ab5cc295e55d60b5bae947c3814b2e4ce595876595f1d36dde7f13dc",
    ),
    "artifacts_before": (
        RAW_ROOT / "input/artifact_sha256.before",
        "61b30c02d1a75b4d159059aabb5802e29fce6cb148bf48546f29ae25fb145863",
    ),
    "artifacts_after": (
        RAW_ROOT / "input/artifact_sha256.after",
        "61b30c02d1a75b4d159059aabb5802e29fce6cb148bf48546f29ae25fb145863",
    ),
    "checkpoint_before": (
        RAW_ROOT / "input/checkpoint.files.sha256.before",
        "d114b40ccce63ca4334d39d2787990290eaf9cacbd6fe901404bcc2898b2a1bc",
    ),
    "checkpoint_after": (
        RAW_ROOT / "input/checkpoint.files.sha256.after",
        "d114b40ccce63ca4334d39d2787990290eaf9cacbd6fe901404bcc2898b2a1bc",
    ),
    "source_commit_before": (
        RAW_ROOT / "input/source_commit.before",
        "1761e000c8d9b0a5d2fb038cf7386ce845fccd8ed8393162026c96e82e7d644a",
    ),
    "source_commit_after": (
        RAW_ROOT / "input/source_commit.after",
        "1761e000c8d9b0a5d2fb038cf7386ce845fccd8ed8393162026c96e82e7d644a",
    ),
    "source_status_before": (
        RAW_ROOT / "input/source_status.before",
        "62f3dbb481145b7614ecba787ce1c8833cdbf5447c3fe2cbbf17e2f7df5e4496",
    ),
    "source_status_after": (
        RAW_ROOT / "input/source_status.after",
        "62f3dbb481145b7614ecba787ce1c8833cdbf5447c3fe2cbbf17e2f7df5e4496",
    ),
    "compile_command": (
        RAW_ROOT / "input/compile_command.json",
        "5924c2be81e0a7fee80cf533b54563e4263e8be30458933364c6ba3e7842d7b7",
    ),
    "checkpoint_command": (
        RAW_ROOT / "input/checkpoint_command.json",
        "26d20078e783ec80dc1c0e0b0d4eed6cee31bc1089a5dd3940e357ba3014fad7",
    ),
    "restore_command": (
        RAW_ROOT / "input/restore_command.json",
        "688d0c0fa110050e71031eae8d74e3bd8ddc41bb9587f30c5d5987452368694d",
    ),
}

EXPECTED_HARDWARE_ACCOUNTING = {
    "active_apply_lanes_per_indirect_unit": 4,
    "active_value_owner_lines_per_unit": 32,
    "active_value_owner_payload_bytes_per_maa": 8192,
    "coherent_q_index_backing_bytes": 0,
    "external_coherent_backing_bytes": 524288,
    "fixed_apply_lane_owner_state_bytes": 32,
    "fixed_apply_lane_owners_per_maa": 16,
    "fixed_apply_lane_pool_state_bytes_per_maa": 576,
    "fixed_apply_lane_pool_state_bytes_per_unit": 144,
    "fixed_apply_lanes_per_indirect_unit": 4,
    "fixed_value_owner_lines_per_unit": 128,
    "fixed_value_owner_payload_bytes_per_maa": 32768,
    "host_payload_bytes": 0,
    "incremental_apply_lane_pool_bytes_vs_lane_1": 0,
    "incremental_apply_lane_ports_vs_lane_1": 0,
    "indirect_units_per_maa": 4,
    "new_control_bytes": 0,
    "new_payload_bytes": 0,
    "new_ports": 0,
    "physical_spd_payload_bytes": 524288,
    "product_backing_bytes": 262144,
    "value_owner_line_bytes": 64,
    "virtual_p_backing_bytes": 262144,
}

EXPECTED_GEOMETRY = {
    "coherent_q_index_backing_bytes": 0,
    "cores": 4,
    "external_coherent_backing_bytes": 524288,
    "host_payload_bytes": 0,
    "logical_tile_elements": 16384,
    "physical_spd_payload_bytes": 524288,
    "physical_tile_elements": 4096,
    "product_backing_bytes": 262144,
    "tiles_per_core": 8,
    "virtual_p_backing_bytes": 262144,
}

SELECTION_TEXT = {
    "treatment": runner.TREATMENT,
    "slice": "all_spmv_full_windows",
    "producer": "physical_page_mul_direct_index_admit",
    "logical": "16384",
    "physical": "4096",
    "external_coherent_backing_bytes": "524288",
    "physical_spd_payload_bytes": "524288",
    "logical_scheduler_reserved_lanes": "0",
    "logical_scheduler_reserved_lane_payload_bytes": "0",
    "host_payload_access": "0",
    "coherent_index_backing_bytes": "0",
    "p_gather_mode": "virtual_16k",
    "virtual_p_backing_bytes": "262144",
    "virtual_p_allocation_bytes": "262144",
    "product_publisher_lines": "0",
    "hidden_spill_bytes": "0",
    "global_fallbacks": "0",
    "p16_reorder_preserved": "1",
    "q16_reorder_preserved": "1",
    "performance_promotable": "0",
}

TERMINAL_EXTRA = {
    "fused_p16_product_windows": "0",
    "virtual_p_allocation_bytes": "262144",
    "virtual_p_write_bytes": "718274560",
    "virtual_p_read_bytes": "718274560",
    "product_publisher_lines": "11223040",
    "hidden_spill_bytes": "0",
    "global_fallbacks": "0",
}


class CertificateError(RuntimeError):
    """The read-only successor classifier rejected an input."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CertificateError(message)


_DIGEST_CACHE: dict[Path, tuple[tuple[int, int, int], str]] = {}


def digest(path: Path) -> str:
    require(
        path.is_file() and not path.is_symlink(),
        f"not a regular input file: {path}",
    )
    before = path.stat()
    identity = (before.st_ino, before.st_size, before.st_mtime_ns)
    cached = _DIGEST_CACHE.get(path)
    if cached is not None and cached[0] == identity:
        return cached[1]
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            hasher.update(chunk)
    after = path.stat()
    require(
        identity == (after.st_ino, after.st_size, after.st_mtime_ns),
        f"input changed while hashing: {path}",
    )
    value = hasher.hexdigest()
    _DIGEST_CACHE[path] = (identity, value)
    return value


def json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CertificateError(f"invalid JSON: {path}: {error}") from error


def parse_kv_line(line: str) -> dict[str, str]:
    return {
        key: value
        for token in line.split()
        if "=" in token
        for key, value in (token.split("=", 1),)
    }


def exactly_one_prefixed(text: str, prefix: str) -> str:
    matches = [line for line in text.splitlines() if line.startswith(prefix)]
    require(len(matches) == 1, f"requires exactly one {prefix.strip()} marker")
    return matches[0]


def record_expected(
    snapshot: dict[str, str], path: Path, expected: str, label: str
) -> None:
    require(digest(path) == expected, f"pinned hash mismatch: {label}: {path}")
    snapshot[str(path)] = expected


def ledger_entries(path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        fields = line.split(maxsplit=1)
        require(
            len(fields) == 2
            and re.fullmatch(r"[0-9a-f]{64}", fields[0]) is not None,
            f"malformed ledger line {number}: {path}",
        )
        name = fields[1].lstrip("*")
        require(name not in seen, f"duplicate ledger entry: {path}: {name}")
        seen.add(name)
        entries.append((fields[0], name))
    require(entries, f"empty ledger: {path}")
    return entries


def verify_ledger(
    ledger: Path,
    base: Path,
    snapshot: dict[str, str],
    *,
    expected_entries: int,
) -> int:
    entries = ledger_entries(ledger)
    require(
        len(entries) == expected_entries,
        f"unexpected ledger entry count: {ledger}",
    )
    base = base.resolve()
    for expected, raw_name in entries:
        raw_path = Path(raw_name)
        path = raw_path if raw_path.is_absolute() else base / raw_path
        path = path.resolve()
        if not raw_path.is_absolute():
            require(
                path == base or base in path.parents,
                f"ledger path escapes root: {path}",
            )
        require(digest(path) == expected, f"ledger mismatch: {path}")
        snapshot[str(path)] = expected
    return len(entries)


def raw_tree_state() -> dict[str, tuple[int, int, int]]:
    state: dict[str, tuple[int, int, int]] = {}
    for path in sorted(RAW_ROOT.rglob("*")):
        require(not path.is_symlink(), f"raw root contains a symlink: {path}")
        if path.is_file():
            item = path.stat()
            state[str(path.relative_to(RAW_ROOT))] = (
                item.st_ino,
                item.st_size,
                item.st_mtime_ns,
            )
    require(state, "raw root is empty")
    return state


def validate_pinned_roots(raw_root: Path, lane1_root: Path) -> None:
    require(raw_root.resolve() == RAW_ROOT, "raw root is not exactly pinned")
    require(
        lane1_root.resolve() == LANE1_ROOT,
        "lane-1 control root is not exactly pinned",
    )
    for path in (
        RAW_ROOT,
        LANE1_ROOT,
        NUMERICAL_AUTHORITY_ROOT,
        LANE_SELECTION_ROOT,
    ):
        require(
            path.is_dir() and not path.is_symlink(), f"missing root: {path}"
        )


def validate_manifest(manifest: dict[str, Any]) -> None:
    expected = {
        "schema": RAW_MANIFEST_SCHEMA,
        "terminal": False,
        "candidate_only": True,
        "guest_runs": 1,
        "native_runs": 0,
        "lane_1_runs": 0,
        "cache_off_runs": 0,
        "direct4_runs": 0,
        "other_candidate_runs": 0,
        "trace": "disabled",
        "timeout": "none",
        "source_commit": RAW_SOURCE_COMMIT,
        "source_base_commit": runner.base.SOURCE_BASE_COMMIT,
        "cg_na": runner.CG_NA,
        "selector": runner.TREATMENT,
        "p16_reorder_preserved": True,
        "q16_reorder_preserved": True,
    }
    require(
        all(manifest.get(key) == value for key, value in expected.items()),
        "raw manifest identity changed",
    )
    require(
        manifest.get("geometry") == EXPECTED_GEOMETRY,
        "raw geometry/storage closure changed",
    )
    require(
        manifest.get("hardware_accounting") == EXPECTED_HARDWARE_ACCOUNTING,
        "raw lane/storage accounting changed",
    )
    numerical = manifest.get("numerical_authority", {})
    require(
        isinstance(numerical, dict)
        and numerical.get("root") == str(NUMERICAL_AUTHORITY_ROOT)
        and numerical.get("verdict") == VERDICT
        and numerical.get("relative_bounds")
        == runner.base.RELATIVE_BOUND_TEXT,
        "raw numerical authority identity changed",
    )
    selection = manifest.get("lane_selection_authority", {})
    require(
        isinstance(selection, dict)
        and selection.get("root") == str(LANE_SELECTION_ROOT)
        and selection.get("decision") == "ACCEPT_EXACT_FASTER_ARM"
        and selection.get("selected_lane") == 4,
        "raw lane-selection declaration changed",
    )
    comparison = manifest.get("post_pass_comparison", {})
    require(
        isinstance(comparison, dict)
        and comparison.get("root") == str(LANE1_ROOT)
        and comparison.get("expected_simTicks") == LANE1_SIMTICKS
        and comparison.get("read_only_after_candidate_pass") is True,
        "raw lane-1 control declaration changed",
    )


def validate_preflight(snapshot: dict[str, str]) -> dict[str, Any]:
    for label, (path, expected) in RAW_PINNED_FILES.items():
        record_expected(snapshot, path, expected, f"raw {label}")
    manifest = load_json(RAW_ROOT / "manifest.json")
    require(isinstance(manifest, dict), "raw manifest is not a mapping")
    validate_manifest(manifest)

    input_dir = RAW_ROOT / "input"
    commit_before = (input_dir / "source_commit.before").read_text().strip()
    commit_after = (input_dir / "source_commit.after").read_text().strip()
    require(
        commit_before == commit_after == RAW_SOURCE_COMMIT,
        "raw source commit preflight identity changed",
    )
    status_before = (input_dir / "source_status.before").read_text()
    status_after = (input_dir / "source_status.after").read_text()
    require(
        status_before == status_after
        and len(status_before.splitlines()) == 1
        and status_before.startswith(
            "## codex/session-hybrid-p16q16-lane4-full-"
        ),
        "raw source status was dirty or changed",
    )
    require(
        (input_dir / "artifact_sha256.before").read_text()
        == (input_dir / "artifact_sha256.after").read_text(),
        "raw artifact ledger changed across the run",
    )
    require(
        (input_dir / "checkpoint.files.sha256.before").read_text()
        == (input_dir / "checkpoint.files.sha256.after").read_text(),
        "raw checkpoint ledger changed across restore",
    )
    artifact_count = verify_ledger(
        input_dir / "artifact_sha256.before",
        RAW_ROOT,
        snapshot,
        expected_entries=33,
    )
    checkpoint_count = verify_ledger(
        input_dir / "checkpoint.files.sha256.before",
        RAW_ROOT / "checkpoint",
        snapshot,
        expected_entries=13,
    )
    commands = manifest.get("commands")
    require(isinstance(commands, dict), "raw manifest commands missing")
    for name in ("compile", "checkpoint", "restore"):
        require(
            load_json(input_dir / f"{name}_command.json")
            == commands.get(name),
            f"raw {name} command identity changed",
        )
    require(
        not any(
            (RAW_ROOT / name).exists()
            for name in (
                "result.json",
                "gate.complete",
                "certified_artifacts.sha256",
            )
        ),
        "raw root unexpectedly contains a terminal certificate",
    )
    return {
        "artifact_entries": artifact_count,
        "checkpoint_entries": checkpoint_count,
        "source_commit": RAW_SOURCE_COMMIT,
        "source_status_clean": True,
        "artifacts_immutable": True,
        "checkpoint_immutable": True,
    }


def query_journal_entries() -> list[dict[str, Any]]:
    command = [
        "journalctl",
        "--quiet",
        "--no-pager",
        "--output=json",
        f"--user-unit={SERVICE_UNIT}",
    ]
    try:
        output = subprocess.run(
            command, check=True, capture_output=True, text=True
        ).stdout
    except subprocess.CalledProcessError as error:
        raise CertificateError(
            f"cannot inspect terminal service journal: {error.stderr.strip()}"
        ) from error
    entries: list[dict[str, Any]] = []
    try:
        for line in output.splitlines():
            value = json.loads(line)
            require(isinstance(value, dict), "journal entry is not a mapping")
            entries.append(value)
    except json.JSONDecodeError as error:
        raise CertificateError(
            f"invalid service journal JSON: {error}"
        ) from error
    require(entries, "terminal service journal is empty")
    return entries


def validate_journal_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    started_message = (
        "Started DX100 full CG page-fed p16q16 value-cache lane4 "
        "candidate r1."
    )
    starts = [
        item
        for item in entries
        if item.get("MESSAGE") == started_message
        and item.get("USER_UNIT") == SERVICE_UNIT
    ]
    require(len(starts) == 1, "service journal start identity changed")
    start = starts[0]
    require(
        start.get("USER_INVOCATION_ID") == SERVICE_INVOCATION_ID
        and start.get("_BOOT_ID") == SERVICE_BOOT_ID
        and int(start.get("__MONOTONIC_TIMESTAMP", -1))
        == SERVICE_START_MONOTONIC_US,
        "service journal invocation/start identity changed",
    )

    process = [
        item
        for item in entries
        if item.get("_SYSTEMD_USER_UNIT") == SERVICE_UNIT
    ]
    require(len(process) == 14, "service journal traceback identity changed")
    require(
        all(
            item.get("_SYSTEMD_INVOCATION_ID") == SERVICE_INVOCATION_ID
            and item.get("_BOOT_ID") == SERVICE_BOOT_ID
            and item.get("_PID") == str(REGISTERED_MAIN_PID)
            and item.get("_CMDLINE") == REGISTERED_COMMAND
            for item in process
        ),
        "service journal process identity changed",
    )
    require(
        process[0].get("MESSAGE") == "Traceback (most recent call last):"
        and process[-1].get("MESSAGE")
        == "__main__.GateError: four-lane active/high-water closure failed",
        "service did not fail only at the obsolete high-water gate",
    )

    exits = [
        item
        for item in entries
        if item.get("MESSAGE_ID") == "98e322203f7a4ed290d09fe03c09fe15"
        and item.get("USER_UNIT") == SERVICE_UNIT
    ]
    results = [
        item
        for item in entries
        if item.get("MESSAGE_ID") == "d9b373ed55a64feb8242e02dbe79a49c"
        and item.get("USER_UNIT") == SERVICE_UNIT
    ]
    require(
        len(exits) == len(results) == 1,
        "service journal terminal record is not unique",
    )
    exit_entry = exits[0]
    result_entry = results[0]
    require(
        exit_entry.get("USER_INVOCATION_ID") == SERVICE_INVOCATION_ID
        and exit_entry.get("EXIT_CODE") == "exited"
        and exit_entry.get("EXIT_STATUS") == "1"
        and exit_entry.get("COMMAND") == "ExecStart",
        "service journal process-exit identity changed",
    )
    require(
        result_entry.get("USER_INVOCATION_ID") == SERVICE_INVOCATION_ID
        and result_entry.get("UNIT_RESULT") == "exit-code"
        and int(result_entry.get("__MONOTONIC_TIMESTAMP", -1))
        == SERVICE_EXIT_MONOTONIC_US,
        "service journal result identity changed",
    )
    require(
        len(entries) == 18,
        "service journal contains an unexpected invocation record",
    )

    ticks_per_second = int(os.sysconf("SC_CLK_TCK"))
    registered_start_us = (
        REGISTERED_MAIN_START_TICKS * 1_000_000 // ticks_per_second
    )
    require(
        abs(registered_start_us - SERVICE_START_MONOTONIC_US) < 1_000_000,
        "registered PID start identity does not match journal start",
    )
    normalized = [
        {
            "message": item.get("MESSAGE"),
            "message_id": item.get("MESSAGE_ID"),
            "monotonic_us": item.get("__MONOTONIC_TIMESTAMP"),
            "pid": item.get("_PID"),
            "invocation_id": item.get(
                "_SYSTEMD_INVOCATION_ID",
                item.get("USER_INVOCATION_ID"),
            ),
        }
        for item in entries
    ]
    journal_sha256 = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "unit": SERVICE_UNIT,
        "invocation_id": SERVICE_INVOCATION_ID,
        "boot_id": SERVICE_BOOT_ID,
        "registered_main_pid": REGISTERED_MAIN_PID,
        "registered_main_start_ticks": REGISTERED_MAIN_START_TICKS,
        "start_monotonic_us": SERVICE_START_MONOTONIC_US,
        "exit_monotonic_us": SERVICE_EXIT_MONOTONIC_US,
        "exec_main_code": "exited",
        "exec_main_status": 1,
        "result": "exit-code",
        "journal_entries": len(entries),
        "normalized_journal_sha256": journal_sha256,
        "obsolete_gate_failure_only": True,
        "terminal": True,
    }


def validate_registered_process_absence(
    proc_root: Path = Path("/proc"),
) -> None:
    require(
        not (proc_root / str(REGISTERED_MAIN_PID)).exists(),
        f"registered PID {REGISTERED_MAIN_PID} is live or reused",
    )
    raw_text = str(RAW_ROOT).encode()
    guest_text = str(
        RAW_ROOT / "bin/cg_page_fed_p16_q16_value_cache_lane4_full"
    ).encode()
    for entry in proc_root.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            command = (entry / "cmdline").read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        arguments = command.rstrip(b"\0").split(b"\0")
        require(
            raw_text not in arguments and guest_text not in arguments,
            f"conflicting raw-run process is live: {entry.name}",
        )


def validate_service_terminal(
    journal_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    terminal = validate_journal_entries(
        journal_entries
        if journal_entries is not None
        else query_journal_entries()
    )
    validate_registered_process_absence()
    terminal["registered_process_absent"] = True
    return terminal


def validate_numerical_authority(
    snapshot: dict[str, str],
) -> tuple[dict[str, Any], dict[str, str]]:
    for name, expected in runner.base.CERTIFICATE_FILES.items():
        record_expected(
            snapshot,
            NUMERICAL_AUTHORITY_ROOT / name,
            expected,
            f"numerical authority {name}",
        )
    input_ledger = NUMERICAL_AUTHORITY_ROOT / "input_sha256.txt"
    record_expected(
        snapshot,
        input_ledger,
        "066b423ac13e01e6c3dd4b35f8b6e00d562960cce0b283206405b8424acd6fa5",
        "numerical authority input ledger",
    )
    record_expected(
        snapshot,
        runner.base.NATIVE_LOG,
        runner.base.NATIVE_LOG_SHA256,
        "native16 numerical log",
    )
    record_expected(
        snapshot,
        runner.base.NATIVE_STATS,
        runner.base.NATIVE_STATS_SHA256,
        "native16 numerical stats",
    )
    try:
        authority = runner.base.validate_certificate()
        _, fields = runner.base.fingerprint_fields(runner.base.NATIVE_LOG)
    except runner.base.GateError as error:
        raise CertificateError(
            f"numerical authority rejected: {error}"
        ) from error
    return authority, fields


def validate_lane_selection_authority(
    snapshot: dict[str, str],
) -> dict[str, Any]:
    for name, expected in runner.LANE_SELECTION_HASHES.items():
        record_expected(
            snapshot,
            LANE_SELECTION_ROOT / name,
            expected,
            f"lane-selection {name}",
        )
    try:
        return runner.validate_lane_selection_authority()
    except runner.GateError as error:
        raise CertificateError(
            f"lane-selection authority rejected: {error}"
        ) from error


def validate_corrected_lane_accounting(values: dict[str, int]) -> None:
    instructions = values.get("IND_SoaJitInstructions")
    active = values.get("IND_SoaJitActiveApplyLanes")
    high_water = values.get("IND_SoaJitApplyLaneHighWater")
    require(
        instructions == runner.EXPECTED_WINDOWS,
        "lane accounting instruction count changed",
    )
    require(
        active == 4 * instructions,
        "active apply-lane sum is not exactly four per instruction",
    )
    require(
        3 * instructions < high_water <= 4 * instructions,
        "apply-lane high-water does not prove bounded four-lane use",
    )


def validate_selection(log_text: str) -> dict[str, str]:
    line = exactly_one_prefixed(log_text, "CG_LOGICAL16_RMW_SELECTION ")
    fields = parse_kv_line(line)
    require(
        all(fields.get(key) == value for key, value in SELECTION_TEXT.items()),
        "candidate selection/storage/reorder closure changed",
    )
    return fields


def validate_raw_candidate(
    authority_fields: dict[str, str],
) -> tuple[dict[str, Any], dict[str, float]]:
    require(
        (RAW_ROOT / "checkpoint.log.exit").read_text() == "0\n",
        "checkpoint wrapper exit is not zero",
    )
    checkpoint_text = (RAW_ROOT / "checkpoint.log").read_text(errors="replace")
    require(
        len(
            re.findall(
                r"^Exiting @ tick [0-9]+ because checkpoint$",
                checkpoint_text,
                re.MULTILINE,
            )
        )
        == 1,
        "checkpoint did not close exactly once",
    )
    require(
        "CG_FINGERPRINT " not in checkpoint_text
        and "CG_LOGICAL16_RMW_TERMINAL " not in checkpoint_text,
        "checkpoint crossed the deferred candidate boundary",
    )
    require(
        (RAW_ROOT / "run/restore.log.exit").read_text() == "0\n",
        "restore wrapper exit is not zero",
    )
    restore_text = (RAW_ROOT / "run/restore.log").read_text(errors="replace")
    selection = validate_selection(restore_text)
    try:
        candidate, deltas = runner.validate_restore(
            RAW_ROOT / "run", authority_fields
        )
    except (runner.GateError, runner.base.GateError) as error:
        raise CertificateError(f"raw candidate rejected: {error}") from error
    stats = candidate.get("stats")
    require(isinstance(stats, dict), "candidate stats are not a mapping")
    validate_corrected_lane_accounting(stats)
    require(
        stats.get("simTicks") == FIRST_ROI_SIMTICKS,
        "candidate first-ROI simTicks changed",
    )
    require(
        stats.get("IND_SoaJitActiveApplyLanes") == OBSERVED_ACTIVE_LANES
        and stats.get("IND_SoaJitApplyLaneHighWater")
        == OBSERVED_APPLY_HIGH_WATER,
        "observed lane accounting changed",
    )
    require(
        stats["IND_SoaJitValueReadIssues"]
        + stats["IND_SoaJitValueHits"]
        + stats["IND_SoaJitValueMergedWaiters"]
        == stats["IND_SoaJitValueDeliveries"]
        == runner.EXPECTED_WORDS,
        "exact value issue/hit/merge/delivery closure failed",
    )
    terminal_fields = parse_kv_line(candidate["terminal_line"])
    require(
        all(
            terminal_fields.get(key) == value
            for key, value in TERMINAL_EXTRA.items()
        ),
        "virtual-p/product publisher/storage closure changed",
    )
    candidate["selection"] = selection
    candidate["terminal_fields"] = terminal_fields
    candidate["restore_exit"] = 0
    candidate["m5_exit"] = True
    candidate["first_roi_simTicks"] = FIRST_ROI_SIMTICKS
    return candidate, deltas


def retained_line_identity(values: dict[str, int]) -> dict[str, Any]:
    issues = values.get("IND_SoaJitValueReadIssues")
    responses = values.get("IND_SoaJitValueReadResponses")
    fills = values.get("IND_SoaJitValueFills")
    cached = values.get("IND_SoaJitValueCachedResponses")
    hits = values.get("IND_SoaJitValueHits")
    merged = values.get("IND_SoaJitValueMergedWaiters")
    deliveries = values.get("IND_SoaJitValueDeliveries")
    require(
        isinstance(issues, int)
        and issues > 0
        and issues == responses == fills == cached,
        "retained-line issue/response/fill identity failed",
    )
    require(
        isinstance(hits, int)
        and hits > 0
        and isinstance(merged, int)
        and merged >= 0
        and issues + hits + merged == deliveries == runner.EXPECTED_WORDS,
        "retained-line issue/hit/merge/delivery identity failed",
    )
    return {
        "read_issues": issues,
        "read_responses": responses,
        "fills": fills,
        "cached_responses": cached,
        "hits": hits,
        "merged_waiters": merged,
        "deliveries": deliveries,
        "identity_closed": True,
    }


def validate_lane1_control(
    snapshot: dict[str, str], candidate: dict[str, Any]
) -> dict[str, Any]:
    for name, expected in runner.ACCEPTED_LANE1_HASHES.items():
        record_expected(
            snapshot,
            LANE1_ROOT / name,
            expected,
            f"lane-1 control {name}",
        )
    ledger = LANE1_ROOT / "certified_artifacts.sha256"
    count = verify_ledger(
        ledger,
        LANE1_ROOT,
        snapshot,
        expected_entries=13,
    )
    try:
        result, stats = runner.read_accepted_lane1_after_pass(VERDICT)
    except runner.GateError as error:
        raise CertificateError(f"lane-1 control rejected: {error}") from error
    candidate_stats = candidate.get("stats")
    require(
        isinstance(candidate_stats, dict),
        "candidate stats unavailable for retained-line comparison",
    )
    require(
        result.get("candidate", {}).get("terminal")
        == candidate.get("terminal"),
        "lane-1 and lane-4 terminal work/geometry differ",
    )
    require(
        all(
            candidate_stats.get(name) == stats.get(name)
            for name in runner.CROSS_ARM_WORK_IDENTITY_STATS
        ),
        "lane-4 candidate changed invariant work identity",
    )
    candidate_retained = retained_line_identity(candidate_stats)
    lane1_retained = retained_line_identity(stats)
    require(
        stats.get("simTicks") == LANE1_SIMTICKS,
        "lane-1 first-ROI arithmetic input changed",
    )
    return {
        "certificate_verified": True,
        "gate": result["gate"],
        "root": str(LANE1_ROOT),
        "result_sha256": runner.ACCEPTED_LANE1_HASHES["result.json"],
        "gate_sha256": runner.ACCEPTED_LANE1_HASHES["gate.complete"],
        "certified_artifact_entries": count,
        "selected_apply_lanes": 1,
        "first_roi_simTicks": stats["simTicks"],
        "terminal_work_geometry_exact": True,
        "invariant_work_identity_exact": True,
        "retained_line_closure_exact_per_arm": True,
        "cross_arm_value_counter_identity_required": False,
        "candidate_retained_line_identity": candidate_retained,
        "lane_1_retained_line_identity": lane1_retained,
        "p16_reorder_preserved": True,
        "q16_reorder_preserved": True,
    }


def ratio_record(numerator: int, denominator: int) -> dict[str, Any]:
    with localcontext() as context:
        context.prec = 50
        decimal = str(Decimal(numerator) / Decimal(denominator))
    return {
        "numerator": numerator,
        "denominator": denominator,
        "exact_fraction": f"{numerator}/{denominator}",
        "decimal_50_digit_context": decimal,
    }


def build_performance(
    candidate: dict[str, Any], lane1_control: dict[str, Any]
) -> dict[str, Any]:
    require(
        candidate.get("first_roi_simTicks") == FIRST_ROI_SIMTICKS,
        "candidate did not pass before arithmetic",
    )
    require(
        lane1_control.get("certificate_verified") is True
        and lane1_control.get("first_roi_simTicks") == LANE1_SIMTICKS,
        "lane-1 certificate was not verified before arithmetic",
    )
    return {
        "metric": "first_roi_simTicks",
        "baseline_arm": "accepted_lane_1_cache_on_full",
        "candidate_arm": "lane_4_cache_on_full",
        "observations_per_arm": 1,
        "lane_1_over_lane_4": ratio_record(LANE1_SIMTICKS, FIRST_ROI_SIMTICKS),
        "lane_4_tick_reduction_fraction": ratio_record(
            LANE1_SIMTICKS - FIRST_ROI_SIMTICKS, LANE1_SIMTICKS
        ),
    }


def validate_claims(certificate: dict[str, Any]) -> None:
    require(certificate.get("verdict") == VERDICT, "verdict changed")
    require(
        certificate.get("p16_reorder_preserved") is True
        and certificate.get("q16_reorder_preserved") is True,
        "p16/q16 reorder claim changed",
    )
    for name in (
        "native_speedup_claim",
        "iso_area_claim",
        "official_nas_verification",
        "full_promotion_claim",
    ):
        require(certificate.get(name) is False, f"forbidden claim: {name}")
    performance = certificate.get("performance")
    require(
        isinstance(performance, dict)
        and performance.get("lane_1_over_lane_4")
        == ratio_record(LANE1_SIMTICKS, FIRST_ROI_SIMTICKS),
        "performance arithmetic changed",
    )


def build_documents(
    preflight: dict[str, Any],
    service: dict[str, Any],
    authority: dict[str, Any],
    selection_authority: dict[str, Any],
    candidate: dict[str, Any],
    deltas: dict[str, float],
    lane1_control: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    performance = build_performance(candidate, lane1_control)
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "read_only_inputs": True,
        "gem5_runs_launched": 0,
        "raw_root_modified": False,
        "roots": {
            "raw_lane_4": str(RAW_ROOT),
            "accepted_lane_1": str(LANE1_ROOT),
            "numerical_authority": str(NUMERICAL_AUTHORITY_ROOT),
            "lane_selection_authority": str(LANE_SELECTION_ROOT),
        },
        "raw_pinned_sha256": {
            name: expected for name, (_, expected) in RAW_PINNED_FILES.items()
        },
        "preflight": preflight,
        "service_registration": {
            "unit": SERVICE_UNIT,
            "invocation_id": SERVICE_INVOCATION_ID,
            "boot_id": SERVICE_BOOT_ID,
            "main_pid": REGISTERED_MAIN_PID,
            "main_proc_start_ticks": REGISTERED_MAIN_START_TICKS,
        },
        "tolerant_numerical_bounds": runner.base.RELATIVE_BOUND_TEXT,
        "arithmetic_inputs": {
            "accepted_lane_1_first_roi_simTicks": LANE1_SIMTICKS,
            "candidate_lane_4_first_roi_simTicks": FIRST_ROI_SIMTICKS,
        },
    }
    certificate = {
        "schema": CERTIFICATE_SCHEMA,
        "verdict": VERDICT,
        "read_only_successor": True,
        "raw_root_modified": False,
        "gem5_runs_launched": 0,
        "raw_wrapper_exit": 1,
        "raw_wrapper_outcome": "obsolete_post_restore_high_water_gate_failure",
        "p16_reorder_preserved": True,
        "q16_reorder_preserved": True,
        "native_speedup_claim": False,
        "iso_area_claim": False,
        "official_nas_verification": False,
        "full_promotion_claim": False,
        "service_terminal": service,
        "numerical_authority": {
            "root": str(NUMERICAL_AUTHORITY_ROOT),
            "verdict": authority["verdict"],
            "relative_bounds": runner.base.RELATIVE_BOUND_TEXT,
        },
        "lane_selection_authority": selection_authority,
        "candidate": candidate,
        "numerical_deltas": deltas,
        "accepted_lane_1_control": lane1_control,
        "lane_accounting": {
            "instructions": runner.EXPECTED_WINDOWS,
            "active_apply_lanes": OBSERVED_ACTIVE_LANES,
            "active_apply_lanes_required": (
                "ActiveApplyLanes == 4 * instructions"
            ),
            "apply_lane_high_water": OBSERVED_APPLY_HIGH_WATER,
            "high_water_required": (
                "3 * instructions < HWM <= 4 * instructions"
            ),
            "at_least_one_operation_used_four_lanes": True,
        },
        "hardware_accounting": EXPECTED_HARDWARE_ACCOUNTING,
        "performance": performance,
    }
    validate_claims(certificate)
    return manifest, certificate


def audit_inputs(
    raw_root: Path = RAW_ROOT,
    lane1_root: Path = LANE1_ROOT,
    journal_entries: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    _DIGEST_CACHE.clear()
    validate_pinned_roots(raw_root, lane1_root)
    before = raw_tree_state()
    snapshot: dict[str, str] = {}
    for validation_source in (
        Path(__file__).resolve(),
        RUNNER_PATH,
        runner.FULL_PATH,
        runner.full.BASE_PATH,
    ):
        snapshot[str(validation_source)] = digest(validation_source)
    preflight = validate_preflight(snapshot)
    service = validate_service_terminal(journal_entries)
    authority, authority_fields = validate_numerical_authority(snapshot)
    selection_authority = validate_lane_selection_authority(snapshot)
    candidate, deltas = validate_raw_candidate(authority_fields)
    # The accepted lane-1 root is intentionally opened only after the raw
    # candidate has passed every independent terminal/numerical/mechanism gate.
    lane1_control = validate_lane1_control(snapshot, candidate)
    manifest, certificate = build_documents(
        preflight,
        service,
        authority,
        selection_authority,
        candidate,
        deltas,
        lane1_control,
    )
    require(before == raw_tree_state(), "classifier mutated the raw root")
    return manifest, certificate, snapshot


def input_ledger_text(snapshot: dict[str, str]) -> str:
    return "".join(
        f"{snapshot[label]}  {label}\n" for label in sorted(snapshot)
    )


def is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def validate_output_root(output: Path) -> Path:
    resolved = output.resolve()
    forbidden = (
        ROOT,
        RAW_ROOT,
        LANE1_ROOT,
        NUMERICAL_AUTHORITY_ROOT,
        LANE_SELECTION_ROOT,
    )
    require(
        all(not is_within(resolved, root.resolve()) for root in forbidden),
        "certificate directory must be external to source and evidence roots",
    )
    require(
        RUNS_ROOT.resolve() in resolved.parents,
        f"certificate directory must be under {RUNS_ROOT}",
    )
    return resolved


def write_exclusive(path: Path, contents: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(contents)
        stream.flush()
        os.fsync(stream.fileno())


def expected_gate(
    manifest_text: str, certificate_text: str, inputs_text: str
) -> str:
    return (
        f"{VERDICT}\n"
        f"manifest_sha256={hashlib.sha256(manifest_text.encode()).hexdigest()}\n"
        "certificate_sha256="
        f"{hashlib.sha256(certificate_text.encode()).hexdigest()}\n"
        f"input_sha256={hashlib.sha256(inputs_text.encode()).hexdigest()}\n"
        "raw_root_modified=false\n"
        "gem5_runs_launched=0\n"
    )


def create_certificate(output: Path) -> dict[str, Any]:
    output = validate_output_root(output)
    require(
        not output.exists(), f"refusing existing certificate root: {output}"
    )
    manifest, certificate, snapshot = audit_inputs()
    manifest_text = json_text(manifest)
    certificate_text = json_text(certificate)
    inputs_text = input_ledger_text(snapshot)
    output.mkdir(parents=True, mode=0o755)
    write_exclusive(output / "manifest.json", manifest_text)
    write_exclusive(output / "certificate.json", certificate_text)
    write_exclusive(output / "input_sha256.txt", inputs_text)
    # The terminal gate is deliberately the final write.
    write_exclusive(
        output / "gate.complete",
        expected_gate(manifest_text, certificate_text, inputs_text),
    )
    return certificate


def output_state(output: Path) -> dict[str, tuple[int, int, int, str]]:
    return {
        path.name: (
            path.stat().st_ino,
            path.stat().st_size,
            path.stat().st_mtime_ns,
            digest(path),
        )
        for path in output.iterdir()
    }


def validate_existing(output: Path) -> dict[str, Any]:
    output = validate_output_root(output)
    require(
        output.is_dir() and not output.is_symlink(),
        f"missing certificate root: {output}",
    )
    require(
        {path.name for path in output.iterdir()}
        == {
            "manifest.json",
            "certificate.json",
            "input_sha256.txt",
            "gate.complete",
        },
        "certificate artifact set changed",
    )
    before = output_state(output)
    manifest, certificate, snapshot = audit_inputs()
    manifest_text = json_text(manifest)
    certificate_text = json_text(certificate)
    inputs_text = input_ledger_text(snapshot)
    require(
        (output / "manifest.json").read_text() == manifest_text,
        "sealed manifest disagrees with inputs",
    )
    require(
        (output / "certificate.json").read_text() == certificate_text,
        "sealed certificate disagrees with inputs",
    )
    require(
        (output / "input_sha256.txt").read_text() == inputs_text,
        "sealed input ledger disagrees with inputs",
    )
    require(
        (output / "gate.complete").read_text()
        == expected_gate(manifest_text, certificate_text, inputs_text),
        "sealed gate changed or was not written last",
    )
    validate_claims(load_json(output / "certificate.json"))
    require(
        before == output_state(output), "--validate mutated certificate root"
    )
    return certificate


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "certificate_root",
        type=Path,
        nargs="?",
        default=DEFAULT_CERTIFICATE_ROOT,
    )
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args(argv)
    if args.validate:
        certificate = validate_existing(args.certificate_root)
    else:
        certificate = create_certificate(args.certificate_root)
    performance = certificate["performance"]["lane_1_over_lane_4"]
    print(
        f"{VERDICT} lane1/lane4="
        f"{performance['decimal_50_digit_context']} "
        f"certificate={args.certificate_root}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
