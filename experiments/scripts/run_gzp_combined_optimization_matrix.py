#!/usr/bin/env python3
"""Run the exact seven-restore GZP composition attribution matrix.

This runner is deliberately restore-only.  It reuses the frozen GZP masked
index checkpoint and guest, while taking the gem5 executable from the caller.
The treatment matrix is fixed: a replicated separate-predicate baseline, three
masked intermediate arms, and a replicated masked/pre-A/128-owner endpoint.
No wall-clock metric participates in the decision.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import (
    Any,
    Iterable,
)

FROZEN_ROOT = Path(
    "/data1/nier/dx100-runs/2026-08-14-gzp-masked-index-full-a3d0bba5-r1"
)
ELEMENTS = 1_000_000
WINDOW_ELEMENTS = 16_384
FULL_WINDOWS = 61
EXPECTED_OUTPUT_HASH = "11225737641199706160"
EXPECTED_INDEX_HASH = "15605778284598092602"
EXPECTED_PREDICATE_HASH = "10865783785176355512"
EXPECTED_SELECTED = 949_959
EXPECTED_REJECTED = 50_041
EXPECTED_FULL_SELECTED = 949_411
EXPECTED_FULL_REJECTED = 50_013
EXPECTED_REFERENCE_ELEMENTS = 1_180_000
EXPECTED_SEPARATE_PREDICATE_LINES = 62_525
FROZEN_ACTIVE_CONTEXTS = 8
COMPOSED_ACTIVE_CONTEXTS = 32
EXPECTED_FROZEN_MANIFEST_SHA256 = (
    "4a80c9e71fe26e4f7795e1abad88d288a18c206dc78f17171a4e27ad43208e69"
)
EXPECTED_TEMPLATE_SHA256 = (
    "7ab07341f3c1f950271c750b629bc5c55bdec5e927a274d8009244b9808ff4d9"
)
EXPECTED_GUEST_SHA256 = (
    "00980813e3bbcd74aec84d4352c545f5ff956485cac99c456fadfddfcab8ecda"
)
EXPECTED_CONFIG_SHA256 = (
    "aacc6e624b7ab0e7b032d5cb913974fa790efdca84598bf468c11f14b9575d0f"
)
EXPECTED_RAMULATOR_CONFIG_SHA256 = (
    "aca6e27b58afdfbfd80b7ec41c3f0e7e574a1fc7355a3512981ead823f68731b"
)
EXPECTED_CPT_SHA256 = (
    "6fdf40e0a1057e2f4f213b8378129d806bd064e1e3f3997d7c65fdc45aa5ab05"
)
EXPECTED_PMEM_SHA256 = (
    "50ea9574453d7a4e9d16a51b5567cf59e8459de32d22721f9872e68c2e4739e0"
)
FATAL_RE = re.compile(
    r"\b(?:panic|fatal|segmentation fault|assertion)\b", re.I
)


ARMS = (
    {
        "name": "baseline-separate-owner32-pre-a-off",
        "selector": "token_stream_ld volume_soa_jit",
        "predicate_mode": "separate_array",
        "treatment": "volume_only_soa_jit",
        "owners": 32,
        "pre_a": False,
        "replicas": ("replica-1", "replica-2"),
    },
    {
        "name": "masked-owner32-pre-a-off",
        "selector": "token_stream_ld volume_masked_index",
        "predicate_mode": "masked_index",
        "treatment": "volume_masked_index_soa_jit",
        "owners": 32,
        "pre_a": False,
        "replicas": ("replica-1",),
    },
    {
        "name": "masked-owner32-pre-a-on",
        "selector": "token_stream_ld volume_masked_index",
        "predicate_mode": "masked_index",
        "treatment": "volume_masked_index_soa_jit",
        "owners": 32,
        "pre_a": True,
        "replicas": ("replica-1",),
    },
    {
        "name": "masked-owner64-pre-a-on",
        "selector": "token_stream_ld volume_masked_index",
        "predicate_mode": "masked_index",
        "treatment": "volume_masked_index_soa_jit",
        "owners": 64,
        "pre_a": True,
        "replicas": ("replica-1",),
    },
    {
        "name": "masked-owner128-pre-a-on",
        "selector": "token_stream_ld volume_masked_index",
        "predicate_mode": "masked_index",
        "treatment": "volume_masked_index_soa_jit",
        "owners": 128,
        "pre_a": True,
        "replicas": ("replica-1", "replica-2"),
    },
)


def frozen_paths() -> dict[str, Path]:
    """Return paths beneath the one immutable evidence root."""
    return {
        "manifest": FROZEN_ROOT / "manifest.json",
        "template": FROZEN_ROOT / "runs/masked_index/restore.command.json",
        "checkpoint": FROZEN_ROOT / "checkpoint",
        "config": FROZEN_ROOT / "inputs/configs/deprecated/example/se.py",
        "guest": FROZEN_ROOT / "inputs/gradzatp_maa_16K_general_soa_jit_fp",
        "ramulator": FROZEN_ROOT / "inputs/ramulator.yaml",
        "masked_selector": FROZEN_ROOT
        / "runs/masked_index/frozen_treatment.txt",
        "separate_selector": FROZEN_ROOT
        / "runs/separate_predicate/frozen_treatment.txt",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gem5", required=True, type=Path)
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--expected-gem5-sha256",
        help="optional caller-provided pin for the supplied executable",
    )
    args = parser.parse_args()
    if args.expected_gem5_sha256 and not re.fullmatch(
        r"[0-9a-f]{64}", args.expected_gem5_sha256
    ):
        parser.error(
            "--expected-gem5-sha256 must be 64 lowercase hex characters"
        )
    return args


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value)
    temporary.replace(path)


def atomic_json(path: Path, value: object) -> None:
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def command_value(command: list[str], option: str) -> str:
    values = [
        argument.split("=", 1)[1]
        for argument in command
        if argument.startswith(option + "=")
    ]
    if len(values) != 1:
        raise RuntimeError(
            f"expected exactly one {option}= argument, got {len(values)}"
        )
    return values[0]


def replace_command_value(command: list[str], option: str, value: str) -> None:
    original = next(
        (
            argument
            for argument in command
            if argument.startswith(option + "=")
        ),
        None,
    )
    if (
        original is None
        or sum(argument.startswith(option + "=") for argument in command) != 1
    ):
        raise RuntimeError(f"cannot replace non-unique {option}= argument")
    command[command.index(original)] = f"{option}={value}"


def option_argument(command: list[str], option: str) -> str:
    positions = [
        index for index, argument in enumerate(command) if argument == option
    ]
    if len(positions) != 1 or positions[0] + 1 >= len(command):
        raise RuntimeError(f"expected exactly one {option} value")
    return command[positions[0] + 1]


def replace_option_argument(
    command: list[str], option: str, value: str
) -> None:
    positions = [
        index for index, argument in enumerate(command) if argument == option
    ]
    if len(positions) != 1 or positions[0] + 1 >= len(command):
        raise RuntimeError(f"cannot replace non-unique {option} value")
    command[positions[0] + 1] = value


def checkpoint_files(checkpoint: Path) -> tuple[Path, Path]:
    cpts = sorted(checkpoint.glob("cpt.*/m5.cpt"))
    if len(cpts) != 1:
        raise RuntimeError("frozen checkpoint must contain exactly one m5.cpt")
    pmem = cpts[0].with_name("system.physmem.store0.pmem")
    if not pmem.is_file():
        raise RuntimeError(
            "frozen checkpoint is missing system.physmem.store0.pmem"
        )
    return cpts[0], pmem


def template() -> list[str]:
    paths = frozen_paths()
    command = json.loads(paths["template"].read_text())
    if not isinstance(command, list) or not all(
        isinstance(argument, str) for argument in command
    ):
        raise RuntimeError("frozen restore command is not an argv string list")
    if command_value(command, "--checkpoint-dir") != str(paths["checkpoint"]):
        raise RuntimeError("frozen command does not bind the exact checkpoint")
    if command_value(command, "--outdir") != str(
        FROZEN_ROOT / "runs/masked_index/gem5"
    ):
        raise RuntimeError("frozen command has an unexpected output directory")
    if command_value(command, "--maa_soa_jit_active_value_owners") != "32":
        raise RuntimeError(
            "frozen command must start from the 32-owner control"
        )
    if command_value(command, "--maa_soa_jit_active_contexts") != str(
        FROZEN_ACTIVE_CONTEXTS
    ):
        raise RuntimeError(
            "frozen command must start from the archived 8-context control"
        )
    if "--maa_soa_jit_pre_a_value_lookahead" in command:
        raise RuntimeError("frozen command must start with pre-A disabled")
    if option_argument(command, "--cmd") != str(paths["guest"]):
        raise RuntimeError(
            "frozen command does not bind the immutable GZP guest"
        )
    if (
        option_argument(command, "--options")
        != f"{ELEMENTS} {FROZEN_ROOT / 'inputs/treatment.txt'}"
    ):
        raise RuntimeError(
            "frozen command does not bind the fixed full-GZP input"
        )
    config_arguments = [
        argument
        for argument in command
        if argument.endswith("/deprecated/example/se.py")
    ]
    if config_arguments != [str(paths["config"])]:
        raise RuntimeError("frozen command does not bind the archived se.py")
    if option_argument(command, "--ramulator-config") != str(
        paths["ramulator"]
    ):
        raise RuntimeError(
            "frozen command does not bind the archived Ramulator config"
        )
    return command


def verify_frozen_inputs(base: list[str]) -> dict[str, str]:
    """Fail closed if any input proving same-checkpoint comparability drifted."""
    paths = frozen_paths()
    cpt, pmem = checkpoint_files(paths["checkpoint"])
    expected = (
        (paths["manifest"], EXPECTED_FROZEN_MANIFEST_SHA256),
        (paths["template"], EXPECTED_TEMPLATE_SHA256),
        (paths["config"], EXPECTED_CONFIG_SHA256),
        (paths["guest"], EXPECTED_GUEST_SHA256),
        (paths["ramulator"], EXPECTED_RAMULATOR_CONFIG_SHA256),
        (cpt, EXPECTED_CPT_SHA256),
        (pmem, EXPECTED_PMEM_SHA256),
    )
    missing = [str(path) for path, _ in expected if not path.is_file()]
    if missing:
        raise RuntimeError("missing frozen input(s): " + ", ".join(missing))
    for path, expected_hash in expected:
        if sha256(path) != expected_hash:
            raise RuntimeError(f"frozen identity mismatch: {path}")
    if sha256(paths["template"]) != EXPECTED_TEMPLATE_SHA256:
        raise RuntimeError("frozen restore command identity mismatch")
    if command_value(base, "--checkpoint-dir") != str(paths["checkpoint"]):
        raise RuntimeError(
            "template checkpoint changed after identity validation"
        )
    selectors = {
        "separate": paths["separate_selector"],
        "masked": paths["masked_selector"],
    }
    expected_selectors = {
        "separate": "token_stream_ld volume_soa_jit",
        "masked": "token_stream_ld volume_masked_index",
    }
    for name, path in selectors.items():
        if (
            not path.is_file()
            or path.read_text().strip() != expected_selectors[name]
        ):
            raise RuntimeError(f"frozen {name} selector identity mismatch")
    return {
        "manifest_sha256": sha256(paths["manifest"]),
        "template_sha256": sha256(paths["template"]),
        "config_sha256": sha256(paths["config"]),
        "guest_sha256": sha256(paths["guest"]),
        "ramulator_config_sha256": sha256(paths["ramulator"]),
        "m5_cpt_sha256": sha256(cpt),
        "pmem_sha256": sha256(pmem),
    }


def run_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for arm in ARMS:
        for replica in arm["replicas"]:
            specs.append({**arm, "replica": replica})
    return specs


def campaign_plan(
    args: argparse.Namespace, base: list[str]
) -> dict[str, object]:
    del base
    paths = frozen_paths()
    return {
        "schema": "dx100.gzp_same_checkpoint_composition.v1",
        "scope": "full GZP same-checkpoint composition attribution",
        "execute_required": True,
        "frozen_root": str(FROZEN_ROOT),
        "shared_checkpoint": str(paths["checkpoint"]),
        "shared_guest": str(paths["guest"]),
        "shared_config": str(paths["config"]),
        "frozen_template_command": str(paths["template"]),
        "elements": ELEMENTS,
        "logical_window_elements": WINDOW_ELEMENTS,
        "full_windows": FULL_WINDOWS,
        "parallel_restores": len(run_specs()),
        "timeout_seconds": None,
        "fixed_active_contexts": COMPOSED_ACTIVE_CONTEXTS,
        "simulated_metric": "simTicks",
        "host_time_metric_authorized": False,
        "expected_output_hash": EXPECTED_OUTPUT_HASH,
        "caller_gem5": str(args.gem5.resolve()),
        "arms": [
            {
                "name": arm["name"],
                "selector": arm["selector"],
                "predicate_mode": arm["predicate_mode"],
                "owners": arm["owners"],
                "pre_a": arm["pre_a"],
                "replicas": list(arm["replicas"]),
            }
            for arm in ARMS
        ],
        "adjacent_deltas": [
            f"{ARMS[index]['name']} -> {ARMS[index + 1]['name']}"
            for index in range(len(ARMS) - 1)
        ],
    }


def materialize_command(
    base: list[str],
    gem5: Path,
    gem5_out: Path,
    selector: Path,
    spec: dict[str, Any],
) -> list[str]:
    command = list(base)
    command[0] = str(gem5.resolve())
    replace_command_value(command, "--outdir", str(gem5_out))
    replace_command_value(
        command, "--maa_soa_jit_active_value_owners", str(spec["owners"])
    )
    replace_command_value(
        command,
        "--maa_soa_jit_active_contexts",
        str(COMPOSED_ACTIVE_CONTEXTS),
    )
    replace_option_argument(command, "--options", f"{ELEMENTS} {selector}")
    pre_a_flag = "--maa_soa_jit_pre_a_value_lookahead"
    if spec["pre_a"]:
        insertion = command.index("--maa_soa_jit_value_cache_enable") + 1
        command.insert(insertion, pre_a_flag)
    elif pre_a_flag in command:
        raise RuntimeError("pre-A-off arm unexpectedly inherited pre-A")
    return command


def normalized_command(command: list[str]) -> list[str]:
    """Remove exactly the permitted per-arm materialization differences."""
    result: list[str] = []
    index = 0
    while index < len(command):
        argument = command[index]
        if argument.startswith("--outdir="):
            result.append("--outdir=<RUN>")
        elif argument.startswith("--maa_soa_jit_active_value_owners="):
            result.append("--maa_soa_jit_active_value_owners=<OWNERS>")
        elif argument == "--maa_soa_jit_pre_a_value_lookahead":
            # Presence is itself the permitted pre-A treatment delta.
            pass
        elif argument == "--options":
            result.extend((argument, "<ELEMENTS> <SELECTOR>"))
            index += 1
        else:
            result.append(argument)
        index += 1
    return result


def fields(line: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for token in line.split():
        if "=" in token:
            key, value = token.split("=", 1)
            result[key] = value
    return result


def exactly_one(
    lines: Iterable[str], prefix: str, label: str
) -> dict[str, str]:
    found = [line for line in lines if line.startswith(prefix)]
    if len(found) != 1:
        raise RuntimeError(
            f"{label}: expected one {prefix}, found {len(found)}"
        )
    return fields(found[0])


def integer(record: dict[str, str], key: str, label: str) -> int:
    try:
        return int(record[key])
    except (KeyError, ValueError) as error:
        raise RuntimeError(f"{label}: invalid integer {key}") from error


def parse_pair(
    value: str, label: str, fields_count: int = 2
) -> tuple[int, ...]:
    pieces = value.split("/")
    if len(pieces) != fields_count:
        raise RuntimeError(f"invalid {label} ledger: {value}")
    try:
        return tuple(int(piece) for piece in pieces)
    except ValueError as error:
        raise RuntimeError(f"invalid {label} ledger: {value}") from error


def first_stats(path: Path) -> dict[str, int]:
    stats: dict[str, int] = {}
    active = False
    complete = False
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("---------- Begin Simulation Statistics"):
            if not active and not complete:
                active = True
            continue
        if active and line.startswith("---------- End Simulation Statistics"):
            complete = True
            break
        if active:
            parts = line.split()
            if len(parts) >= 2:
                try:
                    stats[parts[0]] = int(float(parts[1]))
                except (ValueError, OverflowError):
                    pass
    if not complete or not stats:
        raise RuntimeError(f"missing complete first statistics window: {path}")
    return stats


def stat(stats: dict[str, int], suffix: str, label: str) -> int:
    values = [value for name, value in stats.items() if name.endswith(suffix)]
    if len(values) != 1:
        raise RuntimeError(
            f"{label}: expected one stat ending {suffix}, got {len(values)}"
        )
    return values[0]


TRACE_TO_STAT = {
    "selected": "IND_SoaJitSelected",
    "rejected": "IND_SoaJitPredicateRejected",
    "predicate_lines_issue": "IND_SoaJitPredicateLineReads",
    "predicate_lines_response": "IND_SoaJitPredicateLineResponses",
    "a_reads_issue": "IND_SoaJitAReadIssues",
    "a_reads_response": "IND_SoaJitAReadResponses",
    "value_reads_issue": "IND_SoaJitValueReadIssues",
    "value_reads_response": "IND_SoaJitValueReadResponses",
    "fills": "IND_SoaJitValueFills",
    "cached": "IND_SoaJitValueCachedResponses",
    "deliveries": "IND_SoaJitValueDeliveries",
    "lookahead_issue": "IND_SoaJitLookaheadIssues",
    "lookahead_response": "IND_SoaJitLookaheadResponses",
    "pre_a_issue": "IND_SoaJitPreAValueIssues",
    "pre_a_ready": "IND_SoaJitPreAValueReadyAtAResponse",
    "pre_a_uses": "IND_SoaJitPreAValueUses",
    "aliases": "IND_SoaJitAliasesApplied",
    "a_writes_issue": "IND_SoaJitAWriteIssues",
    "a_writes_response": "IND_SoaJitAWriteResponses",
}


def trace_totals(
    path: Path, spec: dict[str, Any], label: str
) -> dict[str, int]:
    """Require terminal trace closure for predicate, value, A, and pre-A traffic."""
    rows: list[dict[str, int]] = []
    for line in path.read_text(errors="replace").splitlines():
        if "event=soa_jit_complete" not in line or "terminal=1" not in line:
            continue
        event = fields(line)
        required = (
            "logical",
            "selected",
            "predicate_rejected",
            "predicate_mode",
            "masked_index_compare_bits",
            "masked_index_mode_state_bits",
            "masked_index_additional_buffer_bytes",
            "predicate_lines",
            "predicate_uses",
            "a_reads",
            "value_reads",
            "fills",
            "cached",
            "deliveries",
            "aliases",
            "lookahead",
            "pre_a_enable",
            "pre_a",
            "a_writes",
            "active_value_owners",
            "max_value_owners",
            "terminal",
        )
        if any(key not in event for key in required):
            raise RuntimeError(f"{label}: incomplete terminal SoA/JIT trace")
        if integer(event, "logical", label) != WINDOW_ELEMENTS:
            raise RuntimeError(f"{label}: terminal trace is not logical-16K")
        if event["predicate_mode"] != spec["predicate_mode"]:
            raise RuntimeError(f"{label}: predicate mode does not match arm")
        if integer(event, "active_value_owners", label) != spec["owners"]:
            raise RuntimeError(
                f"{label}: active value owners do not match arm"
            )
        if integer(event, "max_value_owners", label) != 128:
            raise RuntimeError(
                f"{label}: maximum value-owner capacity changed"
            )
        selected = integer(event, "selected", label)
        rejected = integer(event, "predicate_rejected", label)
        if selected + rejected != WINDOW_ELEMENTS:
            raise RuntimeError(
                f"{label}: terminal trace does not close its 16K window"
            )
        predicate_issue, predicate_response = parse_pair(
            event["predicate_lines"], "predicate-line"
        )
        a_read_issue, a_read_response = parse_pair(event["a_reads"], "A-read")
        value_issue, value_response = parse_pair(
            event["value_reads"], "value-read"
        )
        lookahead_issue, lookahead_response = parse_pair(
            event["lookahead"], "lookahead"
        )
        a_write_issue, a_write_response = parse_pair(
            event["a_writes"], "A-write"
        )
        pre_a_issue, pre_a_ready, pre_a_uses = parse_pair(
            event["pre_a"], "pre-A", fields_count=3
        )
        if any(
            issued != responded
            for issued, responded in (
                (predicate_issue, predicate_response),
                (a_read_issue, a_read_response),
                (value_issue, value_response),
                (lookahead_issue, lookahead_response),
                (a_write_issue, a_write_response),
            )
        ):
            raise RuntimeError(
                f"{label}: request/response traffic is not closed"
            )
        fills = integer(event, "fills", label)
        cached = integer(event, "cached", label)
        deliveries = integer(event, "deliveries", label)
        aliases = integer(event, "aliases", label)
        if value_issue != fills or fills != cached:
            raise RuntimeError(
                f"{label}: value read/fill/cache traffic is not closed"
            )
        if deliveries != selected or aliases != selected:
            raise RuntimeError(
                f"{label}: selected values do not close through delivery"
            )
        if spec["pre_a"]:
            if (
                event["pre_a_enable"] != "1"
                or pre_a_issue <= 0
                or pre_a_issue != pre_a_uses
                or not 0 <= pre_a_ready <= pre_a_issue
            ):
                raise RuntimeError(
                    f"{label}: active pre-A traffic is not closed"
                )
        elif event["pre_a_enable"] != "0" or any(
            (pre_a_issue, pre_a_ready, pre_a_uses)
        ):
            raise RuntimeError(f"{label}: pre-A-off arm issued pre-A traffic")
        masked = spec["predicate_mode"] == "masked_index"
        expected_compare_bits = 32 if masked else 0
        expected_mode_bits = 1 if masked else 0
        if (
            integer(event, "masked_index_compare_bits", label)
            != expected_compare_bits
            or integer(event, "masked_index_mode_state_bits", label)
            != expected_mode_bits
            or integer(event, "masked_index_additional_buffer_bytes", label)
            != 0
        ):
            raise RuntimeError(
                f"{label}: masked-index hardware ledger changed"
            )
        predicate_uses = integer(event, "predicate_uses", label)
        if masked:
            if predicate_issue != 0 or predicate_uses != 0:
                raise RuntimeError(
                    f"{label}: masked arm emitted predicate line traffic"
                )
        elif predicate_issue <= 0 or predicate_uses != WINDOW_ELEMENTS:
            raise RuntimeError(
                f"{label}: separate arm lost predicate line traffic"
            )
        rows.append(
            {
                "selected": selected,
                "rejected": rejected,
                "predicate_lines_issue": predicate_issue,
                "predicate_lines_response": predicate_response,
                "a_reads_issue": a_read_issue,
                "a_reads_response": a_read_response,
                "value_reads_issue": value_issue,
                "value_reads_response": value_response,
                "fills": fills,
                "cached": cached,
                "deliveries": deliveries,
                "lookahead_issue": lookahead_issue,
                "lookahead_response": lookahead_response,
                "pre_a_issue": pre_a_issue,
                "pre_a_ready": pre_a_ready,
                "pre_a_uses": pre_a_uses,
                "aliases": aliases,
                "a_writes_issue": a_write_issue,
                "a_writes_response": a_write_response,
            }
        )
    if len(rows) != FULL_WINDOWS:
        raise RuntimeError(
            f"{label}: expected {FULL_WINDOWS} terminal traces, got {len(rows)}"
        )
    totals = {key: sum(row[key] for row in rows) for key in TRACE_TO_STAT}
    if (
        totals["selected"] != EXPECTED_FULL_SELECTED
        or totals["rejected"] != EXPECTED_FULL_REJECTED
    ):
        raise RuntimeError(
            f"{label}: terminal selection totals do not match frozen ledger"
        )
    expected_predicate_lines = (
        0
        if spec["predicate_mode"] == "masked_index"
        else EXPECTED_SEPARATE_PREDICATE_LINES
    )
    if totals["predicate_lines_issue"] != expected_predicate_lines:
        raise RuntimeError(f"{label}: predicate-line total does not match arm")
    return totals


def analyze_run(
    run: Path, spec: dict[str, Any]
) -> dict[str, int | str | bool]:
    label = f"{spec['name']}/{spec['replica']}"
    if (run / "restore.exit").read_text().strip() != "0":
        raise RuntimeError(f"{label}: restore exit is not zero")
    log = (run / "restore.log").read_text(errors="replace")
    if (
        len(
            re.findall(
                r"Exiting @ tick \d+ because m5_exit instruction encountered",
                log,
            )
        )
        != 1
    ):
        raise RuntimeError(f"{label}: missing unique m5_exit marker")
    if FATAL_RE.search(log):
        raise RuntimeError(f"{label}: restore log contains a fatal marker")
    lines = log.splitlines()
    output = exactly_one(lines, "UME_OUTPUT_FP ", label)
    reference = exactly_one(lines, "UME_REFERENCE_PASS ", label)
    ledger = exactly_one(lines, "UME_GZP_MASKED_INDEX_LEDGER ", label)
    terminal = exactly_one(lines, "UME_GZP_TERMINAL ", label)
    if (
        output.get("output_hash") != EXPECTED_OUTPUT_HASH
        or output.get("nonfinite") != "0"
    ):
        raise RuntimeError(f"{label}: exact output hash gate failed")
    if (
        reference.get("point_volume_errors") != "0"
        or reference.get("point_gradient_errors") != "0"
        or integer(reference, "elements", label) != EXPECTED_REFERENCE_ELEMENTS
    ):
        raise RuntimeError(f"{label}: scalar reference gate failed")
    required_ledger = {
        "selected": EXPECTED_SELECTED,
        "rejected": EXPECTED_REJECTED,
        "full_selected": EXPECTED_FULL_SELECTED,
        "full_rejected": EXPECTED_FULL_REJECTED,
    }
    if (
        ledger.get("result") != "PASS"
        or ledger.get("exact_equivalence") != "1"
        or ledger.get("index_hash") != EXPECTED_INDEX_HASH
        or any(
            integer(ledger, key, label) != value
            for key, value in required_ledger.items()
        )
        or any(
            integer(ledger, key, label) != 0
            for key in (
                "active_uint32_max",
                "active_illegal_index",
                "inactive_legal_index",
                "inactive_non_sentinel",
            )
        )
    ):
        raise RuntimeError(f"{label}: selection/index ledger gate failed")
    expected_masked = spec["predicate_mode"] == "masked_index"
    expected_terminal = {
        "treatment": spec["treatment"],
        "full_windows": "0",
        "volume_only_windows": "0" if expected_masked else str(FULL_WINDOWS),
        "masked_index_windows": str(FULL_WINDOWS) if expected_masked else "0",
        "published_predicates": "0",
        "published_gradient_values": "0",
        "predicate_hash": EXPECTED_PREDICATE_HASH,
        "ledger_selected": str(EXPECTED_SELECTED),
        "ledger_rejected": str(EXPECTED_REJECTED),
        "ledger_full_selected": str(EXPECTED_FULL_SELECTED),
        "ledger_full_rejected": str(EXPECTED_FULL_REJECTED),
        "index_hash": EXPECTED_INDEX_HASH,
        "performance_promotable": "1",
        "result": "PASS",
    }
    if any(
        terminal.get(key) != value for key, value in expected_terminal.items()
    ):
        raise RuntimeError(f"{label}: terminal closure gate failed")
    if any(
        integer(terminal, key, label) != 0
        for key in (
            "active_uint32_max",
            "active_illegal_index",
            "inactive_legal_index",
            "inactive_non_sentinel",
        )
    ):
        raise RuntimeError(f"{label}: terminal index safety ledger failed")
    if expected_masked:
        publication = {
            "publisher": "masked_index_no_predicate_publication",
            "predicate_publications": "0",
            "predicate_publication_bytes": "0",
        }
    else:
        publication = {
            "publisher": "precheckpoint_uint32_predicate",
            "predicate_publications": "1",
            "predicate_publication_bytes": "4000000",
        }
    if any(terminal.get(key) != value for key, value in publication.items()):
        raise RuntimeError(f"{label}: predicate publication contract failed")
    totals = trace_totals(run / "gem5" / "virtual_trace.log", spec, label)
    stats = first_stats(run / "gem5" / "stats.txt")
    for trace_key, stat_suffix in TRACE_TO_STAT.items():
        if stat(stats, stat_suffix, label) != totals[trace_key]:
            raise RuntimeError(
                f"{label}: stats/trace mismatch for {stat_suffix}"
            )
    if stat(stats, "IND_SoaJitTerminalCompletions", label) != FULL_WINDOWS:
        raise RuntimeError(f"{label}: terminal completion count did not close")
    if (
        stat(stats, "IND_SoaJitActiveValueOwners", label)
        != spec["owners"] * FULL_WINDOWS
    ):
        raise RuntimeError(f"{label}: active value-owner ledger did not close")
    return {
        "arm": str(spec["name"]),
        "replica": str(spec["replica"]),
        "owners": int(spec["owners"]),
        "pre_a": bool(spec["pre_a"]),
        "predicate_mode": str(spec["predicate_mode"]),
        "simTicks": stat(stats, "simTicks", label),
        "output_hash": output["output_hash"],
        "reference_elements": integer(reference, "elements", label),
        "index_hash": ledger["index_hash"],
        "terminal_windows": FULL_WINDOWS,
        **totals,
    }


def arm_rows(
    rows: list[dict[str, int | str | bool]], arm: str
) -> list[dict[str, int | str | bool]]:
    return [row for row in rows if row["arm"] == arm]


DETERMINISM_KEYS = (
    "simTicks",
    "output_hash",
    "reference_elements",
    "index_hash",
    "terminal_windows",
    *TRACE_TO_STAT.keys(),
)


def require_deterministic(
    rows: list[dict[str, int | str | bool]], arm: str
) -> None:
    candidates = arm_rows(rows, arm)
    expected_replicas = next(
        item["replicas"] for item in ARMS if item["name"] == arm
    )
    if len(candidates) != len(expected_replicas):
        raise RuntimeError(f"{arm}: missing replica result")
    snapshots = {
        json.dumps({key: row[key] for key in DETERMINISM_KEYS}, sort_keys=True)
        for row in candidates
    }
    if len(snapshots) != 1:
        raise RuntimeError(f"{arm}: replicas are not deterministic")


def validate_matrix(
    rows: list[dict[str, int | str | bool]]
) -> tuple[list[dict[str, object]], bool, str]:
    if len(rows) != len(run_specs()):
        raise RuntimeError("matrix is missing an arm or replica")
    for key in (
        "output_hash",
        "reference_elements",
        "index_hash",
        "selected",
        "rejected",
    ):
        if len({row[key] for row in rows}) != 1:
            raise RuntimeError(
                f"same-checkpoint semantic invariant changed: {key}"
            )
    baseline = ARMS[0]["name"]
    endpoint = ARMS[-1]["name"]
    require_deterministic(rows, baseline)
    require_deterministic(rows, endpoint)
    baseline_ticks = int(arm_rows(rows, baseline)[0]["simTicks"])
    endpoint_rows = arm_rows(rows, endpoint)
    endpoint_beats_baseline = all(
        int(row["simTicks"]) < baseline_ticks for row in endpoint_rows
    )
    adjacent: list[dict[str, object]] = []
    for index in range(len(ARMS) - 1):
        control_arm = str(ARMS[index]["name"])
        treatment_arm = str(ARMS[index + 1]["name"])
        control = arm_rows(rows, control_arm)[0]
        treatment = arm_rows(rows, treatment_arm)[0]
        control_ticks = int(control["simTicks"])
        treatment_ticks = int(treatment["simTicks"])
        adjacent.append(
            {
                "control": control_arm,
                "treatment": treatment_arm,
                "metric": "simTicks",
                "host_time_metric_authorized": False,
                "control_simTicks": control_ticks,
                "treatment_simTicks": treatment_ticks,
                "delta_simTicks": treatment_ticks - control_ticks,
                "speedup": control_ticks / treatment_ticks,
                "improves": treatment_ticks < control_ticks,
            }
        )
    reason = (
        "both deterministic masked/pre-A/owner128 endpoint replicas beat the "
        "deterministic separate-predicate baseline by simTicks"
        if endpoint_beats_baseline
        else "the endpoint does not beat the deterministic baseline in both replicas"
    )
    return adjacent, endpoint_beats_baseline, reason


def validate_materialized_commands(
    records: list[dict[str, Any]], selectors: dict[str, Path]
) -> None:
    normal = normalized_command(records[0]["command"])
    for record in records:
        command = record["command"]
        spec = record["spec"]
        if normalized_command(command) != normal:
            raise RuntimeError(
                "materialized command changed outside allowed treatment fields"
            )
        if command_value(command, "--maa_soa_jit_active_value_owners") != str(
            spec["owners"]
        ):
            raise RuntimeError(
                "materialized owner capacity does not match arm"
            )
        if command_value(command, "--maa_soa_jit_active_contexts") != str(
            COMPOSED_ACTIVE_CONTEXTS
        ):
            raise RuntimeError(
                "materialized context capacity does not match optimized hybrid"
            )
        has_pre_a = "--maa_soa_jit_pre_a_value_lookahead" in command
        if has_pre_a != bool(spec["pre_a"]):
            raise RuntimeError("materialized pre-A mode does not match arm")
        if (
            option_argument(command, "--options")
            != f"{ELEMENTS} {selectors[spec['name']]}"
        ):
            raise RuntimeError("materialized selector path does not match arm")


def write_matrix(
    outdir: Path, rows: list[dict[str, int | str | bool]]
) -> None:
    atomic_json(
        outdir / "matrix.json",
        {
            "rows": rows,
            "simulated_metric": "simTicks",
            "host_time_metric_authorized": False,
        },
    )
    with (outdir / "matrix.tsv").open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(rows[0]), delimiter="\t"
        )
        writer.writeheader()
        writer.writerows(rows)


def execute(
    args: argparse.Namespace, base: list[str], plan: dict[str, object]
) -> int:
    if args.outdir.exists():
        raise SystemExit("--outdir must not exist")
    if not args.gem5.is_file() or not os.access(args.gem5, os.X_OK):
        raise SystemExit("--gem5 must name an executable file")
    frozen_hashes = verify_frozen_inputs(base)
    gem5_hash = sha256(args.gem5)
    if args.expected_gem5_sha256 and gem5_hash != args.expected_gem5_sha256:
        raise SystemExit(
            "caller-supplied gem5 SHA-256 does not match the requested pin"
        )
    args.outdir.mkdir(parents=True)
    selectors: dict[str, Path] = {}
    for arm in ARMS:
        selector = args.outdir / "selectors" / f"{arm['name']}.txt"
        selector.parent.mkdir(parents=True, exist_ok=True)
        atomic_text(selector, f"{arm['selector']}\n")
        selectors[str(arm["name"])] = selector
    environment = os.environ.copy()
    environment.update({"OMP_NUM_THREADS": "4", "OMP_PROC_BIND": "false"})
    records: list[dict[str, Any]] = []
    processes: list[tuple[dict[str, Any], subprocess.Popen[bytes], Any]] = []
    for spec in run_specs():
        run = args.outdir / "arms" / str(spec["name"]) / str(spec["replica"])
        gem5_out = run / "gem5"
        run.mkdir(parents=True)
        command = materialize_command(
            base, args.gem5, gem5_out, selectors[str(spec["name"])], spec
        )
        atomic_json(run / "restore.command.json", command)
        atomic_text(run / "restore.command.txt", shlex.join(command) + "\n")
        record = {
            "arm": spec["name"],
            "replica": spec["replica"],
            "owners": spec["owners"],
            "pre_a": spec["pre_a"],
            "selector_sha256": sha256(selectors[str(spec["name"])]),
            "command_sha256": sha256(run / "restore.command.json"),
            "command": command,
            "spec": spec,
        }
        log = (run / "restore.log").open("wb")
        processes.append(
            (
                record,
                subprocess.Popen(
                    command,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=environment,
                ),
                log,
            )
        )
    # Intentionally no timeout: the seven restores are launched before any wait.
    for record, process, log in processes:
        rc = process.wait()
        log.close()
        run = (
            args.outdir / "arms" / str(record["arm"]) / str(record["replica"])
        )
        atomic_text(run / "restore.exit", f"{rc}\n")
        records.append(record)
    atomic_json(
        args.outdir / "exits.json",
        {
            f"{record['arm']}/{record['replica']}": int(
                (
                    args.outdir
                    / "arms"
                    / str(record["arm"])
                    / str(record["replica"])
                    / "restore.exit"
                ).read_text()
            )
            for record in records
        },
    )
    try:
        validate_materialized_commands(records, selectors)
        rows = [
            analyze_run(
                args.outdir
                / "arms"
                / str(record["arm"])
                / str(record["replica"]),
                record["spec"],
            )
            for record in records
        ]
        write_matrix(args.outdir, rows)
        adjacent, promote, reason = validate_matrix(rows)
        atomic_json(
            args.outdir / "manifest.json",
            {
                **plan,
                "gem5": {
                    "path": str(args.gem5.resolve()),
                    "sha256": gem5_hash,
                    "expected_sha256": args.expected_gem5_sha256,
                },
                "frozen_hashes": frozen_hashes,
                "environment": {
                    "OMP_NUM_THREADS": environment["OMP_NUM_THREADS"],
                    "OMP_PROC_BIND": environment["OMP_PROC_BIND"],
                },
                "runs": [
                    {
                        key: value
                        for key, value in record.items()
                        if key not in {"command", "spec"}
                    }
                    for record in records
                ],
            },
        )
        atomic_json(
            args.outdir / "decision.json",
            {
                "decision": "PROMOTE" if promote else "REJECT",
                "reason": reason,
                "endpoint": ARMS[-1]["name"],
                "baseline": ARMS[0]["name"],
                "promotion_metric": "simTicks",
                "host_time_metric_authorized": False,
                "adjacent_deltas": adjacent,
            },
        )
    except Exception as error:
        atomic_json(
            args.outdir / "decision.json",
            {"decision": "REJECT", "reason": str(error)},
        )
        raise
    return 0


def main() -> int:
    args = parse_args()
    base = template()
    plan = campaign_plan(args, base)
    if not args.execute:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    return execute(args, base, plan)


if __name__ == "__main__":
    sys.exit(main())
