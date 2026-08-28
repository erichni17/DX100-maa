#!/usr/bin/env python3
"""Run one fail-closed strict, line-combined full-CG candidate.

The only simulated treatment is non-fused page-fed p16 followed by four
response-bearing product pages and page-fed q16.  The restore enables strict
two-phase ordering, masked/combined P writes, retained value lines, and four
apply lanes.  Native, direct4, controls, and second candidates are forbidden.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import (
    Any,
    Callable,
    Iterable,
)

ROOT = Path(__file__).resolve().parents[2]
LANE_RUNNER_PATH = (
    ROOT / "experiments/scripts/"
    "run_cg_page_fed_p16_q16_value_cache_lane4_full.py"
)
LANE_SPEC = importlib.util.spec_from_file_location(
    "cg_page_fed_lane4_full_gate", LANE_RUNNER_PATH
)
if LANE_SPEC is None or LANE_SPEC.loader is None:
    raise RuntimeError(f"cannot load lane-4 full gate: {LANE_RUNNER_PATH}")
lane = importlib.util.module_from_spec(LANE_SPEC)
LANE_SPEC.loader.exec_module(lane)
full = lane.full
base = lane.base

STRICT_GATE_PATH = (
    ROOT / "experiments/scripts/strict_two_phase/"
    "run_cg_fused_p16_q16_strict.py"
)
STRICT_SPEC = importlib.util.spec_from_file_location(
    "cg_strict_two_phase_gate", STRICT_GATE_PATH
)
if STRICT_SPEC is None or STRICT_SPEC.loader is None:
    raise RuntimeError(
        f"cannot load strict two-phase gate: {STRICT_GATE_PATH}"
    )
strict = importlib.util.module_from_spec(STRICT_SPEC)
STRICT_SPEC.loader.exec_module(strict)

TREATMENT = "page_fed_product_soa_jit"
CG_NA = 150_000
APPLY_LANES = 4
EXPECTED_WINDOWS = 10_960
EXPECTED_PRODUCT_PAGES = 43_840
EXPECTED_WORDS = 179_568_640
EXPECTED_A_LINES = 57_491

STRICT_REFERENCE_SESSION = Path(
    "/data1/nier/worktrees/codex-coordination/sessions/"
    "strict-two-phase-cg-reference-20260827-20260827-182028-096a7ac2"
)
STRICT_REFERENCE_WORKTREE = Path(
    "/data1/nier/worktrees/codex-sessions/"
    "strict-two-phase-cg-reference-20260827-20260827-182028-096a7ac2/"
    "DX100-virtualization-selected-integration-cont-20260826"
)
STRICT_GEM5 = STRICT_REFERENCE_WORKTREE / "build/X86/gem5.opt"
STRICT_GEM5_SHA256 = (
    "a78ad432b958b39fe008e496c709a7df4b2cbc4633fda2fad731260b6560148e"
)
STRICT_LINE_ROOT = (
    STRICT_REFERENCE_SESSION
    / "evidence/cg-strict-nonfused-na1024-line-combined-r2"
)
STRICT_LINE_HASHES = {
    "result.json": (
        "f3f7d3b89e9671f6f7f629a3413baedd5ab2722205c0c26a51ea3c210d8147e3"
    ),
    "gate.complete": (
        "2902e032763df1e4dad3052b1380b2bd03579172a1b7bfeec3caa6aa18d16c1c"
    ),
    "command.json": (
        "aea500f17299d34d63d0eb15a6cd21fb6e2b86d90b00cb193427c4ef3bfd2c80"
    ),
    "restore.log": (
        "ae2048e83aebd9fba650b0bf4c7033e4f61d9a1b0ebd61d467cb2bb9d079b656"
    ),
    "restore.log.exit": (
        "9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa"
    ),
    "stats.txt": (
        "76d34f078570f4346f3bef96ec22b3916d8d54bfbe1b12bdc75fa93b3ecd4306"
    ),
    "config.ini": (
        "194ded304d7be4a6aa8797ba91b4235f0b7d6d79283d80fa8e43f3e22eca34a9"
    ),
    "strict_trace.log": (
        "3274ace740ec379b71375f49ffd9575c28a81df47ef74c25ad23d63febfb68bc"
    ),
}

FROZEN_FULL_RAW_ROOT = Path(
    "/data1/nier/dx100-runs/"
    "2026-08-26-cg-page-fed-p16q16-value-cache-lane4-full-r1"
)
FROZEN_FULL_CERTIFICATE_ROOT = Path(
    "/data1/nier/dx100-runs/"
    "2026-08-26-cg-page-fed-p16q16-value-cache-lane4-full-certificate-r3"
)
FROZEN_FULL_CERTIFICATE_HASHES = {
    "manifest.json": (
        "5006e14d07782e93e968899c6b28b7e5fd9d34da23642825476b5739b99bd002"
    ),
    "certificate.json": (
        "a3892ba6d96ef899cf741be8674cd646eb350fe9a09df9e4a9e25431b96f0f93"
    ),
    "input_sha256.txt": (
        "670ec9e33e2d57be807fbf75d9cd3df1eea685fcbfbe405d0b5f3a75dc9f5258"
    ),
    "gate.complete": (
        "1b88b0e153360644fe7eb88c026893562d8807faf7becfe18d8b311011a74ff3"
    ),
}
FROZEN_RAW_HASHES = {
    "manifest.json": (
        "e112bbf706fe9dbff31f81f9a18d57268bbb92d03811a8bf451ae822434e93fd"
    ),
    "input/page_fed_product_soa_jit.selector": (
        "3d8b96c1a61734d3ee89d1593de4ce31dbf829447e1467d52e00a889ec99a7a0"
    ),
    "input/artifact_sha256.before": (
        "61b30c02d1a75b4d159059aabb5802e29fce6cb148bf48546f29ae25fb145863"
    ),
    "input/artifact_sha256.after": (
        "61b30c02d1a75b4d159059aabb5802e29fce6cb148bf48546f29ae25fb145863"
    ),
    "input/checkpoint.files.sha256.before": (
        "d114b40ccce63ca4334d39d2787990290eaf9cacbd6fe901404bcc2898b2a1bc"
    ),
    "input/checkpoint.files.sha256.after": (
        "d114b40ccce63ca4334d39d2787990290eaf9cacbd6fe901404bcc2898b2a1bc"
    ),
}
FROZEN_GEM5_SHA256 = base.GEM5_SHA256
FROZEN_GUEST_SHA256 = (
    "8b503da91ad84d3e18467adbe3a0e79b53155616e623cb838144e23acdc949b7"
)
FROZEN_ABI_SHA256 = (
    "e20a64b3ee6f8e6e99f4f31093ff4941bdeef123ea4b55e8b86ce7b39d29895f"
)
CURRENT_ABI = ROOT / "include/gem5/maa_page_fed_soa_abi.hh"
CURRENT_ABI_SHA256 = (
    "5d21cbb955307201d6fbcbcc2c8aa60e542154db56810c2e3cb22f1471d4eaa2"
)

STRICT_EXTRA_STATS = strict.STRICT_STATS + (
    "IND_NumOTEpochDrain",
    "IND_FusedP16Operations",
    "IND_FusedP16Epochs",
    "IND_FusedP16SourceOrdinals",
    "IND_FusedP16CoefficientReadIssues",
    "IND_FusedP16CoefficientReadResponses",
    "IND_FusedP16CoefficientFills",
    "IND_FusedP16CoefficientDeliveries",
    "IND_FusedP16MulAccepts",
    "IND_FusedP16MulCompletions",
    "IND_FusedP16ProductInsertions",
    "IND_FusedP16ProductWriteCompletions",
    "IND_FusedP16EpochDrains",
    "IND_FusedP16Fallbacks",
    "IND_FusedP16PublisherLines",
    "IND_FusedP16VirtualPBytes",
)
FUSED_ZERO_STATS = tuple(
    name for name in STRICT_EXTRA_STATS if name.startswith("IND_FusedP16")
)


class GateError(RuntimeError):
    """The strict line-combined full-CG gate rejected an input or run."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


def sha256_file(path: Path) -> str:
    return base.sha256_file(path)


def exact_hash(path: Path, digest: str, description: str) -> None:
    try:
        base.exact_hash(path, digest, description)
    except base.GateError as error:
        raise GateError(str(error)) from error


def json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def atomic_json(path: Path, value: Any) -> None:
    base.atomic_write(path, json_text(value))


def source_status() -> str:
    return base.source_status()


def source_commit() -> str:
    return base.source_commit()


def verify_tree_ledger(root: Path, ledger: Path) -> int:
    require(root.is_dir() and not root.is_symlink(), f"bad tree root: {root}")
    lines = ledger.read_text(encoding="utf-8").splitlines()
    require(bool(lines), f"empty tree ledger: {ledger}")
    seen: set[Path] = set()
    for number, line in enumerate(lines, 1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        require(match is not None, f"bad ledger line {number}: {ledger}")
        relative = Path(match.group(2))  # type: ignore[union-attr]
        require(
            not relative.is_absolute() and ".." not in relative.parts,
            f"unsafe ledger path: {relative}",
        )
        artifact = root / relative
        require(
            artifact.is_file()
            and not artifact.is_symlink()
            and sha256_file(artifact)
            == match.group(1),  # type: ignore[union-attr]
            f"tree ledger mismatch: {artifact}",
        )
        seen.add(relative)
    actual = {
        path.relative_to(root) for path in root.rglob("*") if path.is_file()
    }
    require(actual == seen, f"tree artifact set changed: {root}")
    return len(lines)


def validate_strict_selection_authority() -> dict[str, Any]:
    for name, digest in STRICT_LINE_HASHES.items():
        exact_hash(STRICT_LINE_ROOT / name, digest, f"strict authority {name}")
    gate_text = (STRICT_LINE_ROOT / "gate.complete").read_text(
        encoding="utf-8"
    )
    require(
        gate_text == "COMPLETE_CG_STRICT_LINE_COMBINED\n"
        "decision=VALID_LINE_COMBINED_ATTRIBUTION\n"
        "correctness=EXACT_MATCH\n",
        "strict line-combined gate changed",
    )
    result = json.loads(
        (STRICT_LINE_ROOT / "result.json").read_text(encoding="utf-8")
    )
    require(
        result.get("schema") == "dx100.cg.strict_p16_q16.line_combined.v1"
        and result.get("terminal") is True
        and result.get("decision") == "VALID_LINE_COMBINED_ATTRIBUTION"
        and result.get("promotable") is False
        and result.get("cg_na") == 1024
        and result.get("native_runs") == 0
        and result.get("whole_windows") == 65
        and result.get("fingerprints_exact_equal") is True
        and result.get("deterministic_reductions_exact_equal") is True
        and result.get("all_p_writes_64_bytes") is True
        and result.get("p_backing_write_issues") == 358_114
        and result.get("gem5_sha256") == STRICT_GEM5_SHA256,
        "strict line-combined selection identity changed",
    )
    command = json.loads(
        (STRICT_LINE_ROOT / "command.json").read_text(encoding="utf-8")
    )
    require(
        isinstance(command, list)
        and command.count("--maa_virtual_strict_two_phase") == 1
        and command.count("--maa_virtual_masked_writes") == 1
        and command.count("--maa_soa_jit_value_cache_enable") == 1
        and "direct4_product_page_fed_q16" not in " ".join(command),
        "strict line-combined command identity changed",
    )
    config = (
        (STRICT_LINE_ROOT / "config.ini")
        .read_text(errors="replace")
        .splitlines()
    )
    require(
        "virtual_strict_two_phase=true" in config
        and "virtual_masked_writes=true" in config,
        "strict line-combined resolved treatment changed",
    )
    return {
        "root": str(STRICT_LINE_ROOT),
        "result_sha256": STRICT_LINE_HASHES["result.json"],
        "gate_sha256": STRICT_LINE_HASHES["gate.complete"],
        "trace_sha256": STRICT_LINE_HASHES["strict_trace.log"],
        "decision": result["decision"],
        "exact_correctness": True,
        "all_p_writes_64_bytes": True,
    }


def validate_frozen_full_certificate() -> dict[str, Any]:
    for name, digest in FROZEN_FULL_CERTIFICATE_HASHES.items():
        exact_hash(
            FROZEN_FULL_CERTIFICATE_ROOT / name,
            digest,
            f"frozen full certificate {name}",
        )
    gate = (FROZEN_FULL_CERTIFICATE_ROOT / "gate.complete").read_text(
        encoding="utf-8"
    )
    require(
        gate == "PASS_NUMERICAL_MECHANISM_CORRECT\n"
        "manifest_sha256="
        + FROZEN_FULL_CERTIFICATE_HASHES["manifest.json"]
        + "\ncertificate_sha256="
        + FROZEN_FULL_CERTIFICATE_HASHES["certificate.json"]
        + "\ninput_sha256="
        + FROZEN_FULL_CERTIFICATE_HASHES["input_sha256.txt"]
        + "\nraw_root_modified=false\n"
        "gem5_runs_launched=0\n",
        "frozen full certificate gate changed",
    )
    certificate = json.loads(
        (FROZEN_FULL_CERTIFICATE_ROOT / "certificate.json").read_text(
            encoding="utf-8"
        )
    )
    require(
        certificate.get("verdict") == "PASS_NUMERICAL_MECHANISM_CORRECT"
        and certificate.get("raw_root_modified") is False
        and certificate.get("gem5_runs_launched") == 0
        and certificate.get("p16_reorder_preserved") is True
        and certificate.get("q16_reorder_preserved") is True,
        "frozen full successor certificate changed",
    )
    for name, digest in FROZEN_RAW_HASHES.items():
        exact_hash(FROZEN_FULL_RAW_ROOT / name, digest, f"frozen raw {name}")
    return {
        "root": str(FROZEN_FULL_CERTIFICATE_ROOT),
        "certificate_sha256": FROZEN_FULL_CERTIFICATE_HASHES[
            "certificate.json"
        ],
        "verdict": certificate["verdict"],
        "raw_root": str(FROZEN_FULL_RAW_ROOT),
    }


def checkpoint_reuse_decision() -> dict[str, Any]:
    certificate = validate_frozen_full_certificate()
    checkpoint_before = (
        FROZEN_FULL_RAW_ROOT / "input/checkpoint.files.sha256.before"
    )
    checkpoint_after = (
        FROZEN_FULL_RAW_ROOT / "input/checkpoint.files.sha256.after"
    )
    require(
        checkpoint_before.read_text(encoding="utf-8")
        == checkpoint_after.read_text(encoding="utf-8"),
        "frozen full checkpoint ledgers differ",
    )
    checkpoint_entries = verify_tree_ledger(
        FROZEN_FULL_RAW_ROOT / "checkpoint", checkpoint_before
    )
    checkpoint_log = (FROZEN_FULL_RAW_ROOT / "checkpoint.log").read_text(
        errors="replace"
    )
    require(
        len(
            re.findall(
                r"^Exiting @ tick [0-9]+ because checkpoint$",
                checkpoint_log,
                re.MULTILINE,
            )
        )
        == 1
        and "CG_FINGERPRINT " not in checkpoint_log
        and "CG_LOGICAL16_RMW_TERMINAL " not in checkpoint_log
        and "ROI End!!!" not in checkpoint_log,
        "frozen checkpoint is not treatment-neutral",
    )
    artifact_ledger = (
        FROZEN_FULL_RAW_ROOT / "input/artifact_sha256.before"
    ).read_text(encoding="utf-8")
    require(
        FROZEN_GUEST_SHA256
        + "  "
        + str(
            FROZEN_FULL_RAW_ROOT
            / "bin/cg_page_fed_p16_q16_value_cache_lane4_full"
        )
        in artifact_ledger
        and FROZEN_ABI_SHA256 + "  " in artifact_ledger,
        "frozen full guest/ABI ledger identity changed",
    )
    exact_hash(CURRENT_ABI, CURRENT_ABI_SHA256, "current strict guest ABI")
    reasons = []
    if FROZEN_GEM5_SHA256 != STRICT_GEM5_SHA256:
        reasons.append("gem5_sha256_mismatch")
    if FROZEN_ABI_SHA256 != CURRENT_ABI_SHA256:
        reasons.append("guest_abi_sha256_mismatch")
    require(bool(reasons), "unexpectedly reusable frozen checkpoint")
    return {
        "reuse_accepted": False,
        "reasons": reasons,
        "frozen_gem5_sha256": FROZEN_GEM5_SHA256,
        "required_gem5_sha256": STRICT_GEM5_SHA256,
        "frozen_guest_sha256": FROZEN_GUEST_SHA256,
        "frozen_guest_abi_sha256": FROZEN_ABI_SHA256,
        "required_guest_abi_sha256": CURRENT_ABI_SHA256,
        "treatment_neutral_checkpoint": True,
        "checkpoint_ledger_entries": checkpoint_entries,
        "new_treatment_neutral_checkpoint_required": True,
        "frozen_certificate": certificate,
    }


def validate_prelaunch() -> dict[str, Any]:
    base.validate_source_base()
    exact_hash(STRICT_GEM5, STRICT_GEM5_SHA256, "strict gem5")
    exact_hash(base.RAMULATOR, base.RAMULATOR_SHA256, "frozen Ramulator")
    exact_hash(
        base.FROZEN_HEADER,
        base.FROZEN_HEADER_SHA256,
        "frozen full-CG input header",
    )
    require(
        base.FROZEN_HEADER.stat().st_size == base.FROZEN_HEADER_BYTES,
        "frozen full-CG header size changed",
    )
    numerical = base.validate_certificate()
    lane_authority = lane.validate_lane_selection_authority()
    strict_authority = validate_strict_selection_authority()
    reuse = checkpoint_reuse_decision()
    status = source_status()
    require(
        len(status.splitlines()) == 1,
        "prelaunch requires a clean source worktree",
    )
    return {
        "schema": "dx100.cg.strict_line_combined_full_prelaunch.v1",
        "terminal": True,
        "candidate_only": True,
        "source_commit": source_commit(),
        "source_status": status.strip(),
        "cg_na": CG_NA,
        "gem5_sha256": STRICT_GEM5_SHA256,
        "ramulator_sha256": base.RAMULATOR_SHA256,
        "header_sha256": base.FROZEN_HEADER_SHA256,
        "numerical_authority": numerical,
        "lane_authority": lane_authority,
        "strict_line_combined_authority": strict_authority,
        "checkpoint_reuse": reuse,
        "authorized_candidate_restores": 1,
        "authorized_checkpoint_creations": 1,
        "native_runs": 0,
        "direct4_runs": 0,
    }


def checkpoint_command(
    guest: Path, selector: Path, checkpoint: Path
) -> list[str]:
    command = base.checkpoint_command(guest, selector, checkpoint)
    command[0] = str(STRICT_GEM5)
    return command


def restore_command(
    guest: Path, selector: Path, checkpoint: Path, run: Path
) -> list[str]:
    command = lane.restore_command(guest, selector, checkpoint, run)
    command[0] = str(STRICT_GEM5)
    command[3:3] = [
        "--debug-flags=MAAVirtualTrace,MAAMacroEvent,MAATrace",
        "--debug-file=strict_trace.log",
    ]
    command.extend(
        ["--maa_virtual_strict_two_phase", "--maa_virtual_masked_writes"]
    )
    required = (
        "--maa_soa_jit_value_cache_enable",
        "--maa_soa_jit_active_value_owners=32",
        "--maa_soa_jit_apply_lanes=4",
        "--maa_num_tiles_per_core=8",
        "--maa_virtual_strict_two_phase",
        "--maa_virtual_masked_writes",
    )
    require(
        all(command.count(value) == 1 for value in required),
        "restore treatment flags are not exact",
    )
    text = " ".join(command)
    require(
        "direct4_product_page_fed_q16" not in text
        and "--maa_fused_p16_product" not in text,
        "restore contains a forbidden direct4/fused treatment",
    )
    return command


def validate_config(config: Path) -> None:
    lane.validate_config(config)
    lines = config.read_text(errors="replace").splitlines()
    for key in (
        "virtual_strict_two_phase=true",
        "virtual_masked_writes=true",
        "soa_jit_value_cache_enable=true",
        "soa_jit_apply_lanes=4",
    ):
        require(lines.count(key) == 1, f"resolved config changed: {key}")


def _validate_whole(
    fields: dict[str, str]
) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    try:
        p_token = strict.integer(fields, "p_token")
        p_generation = strict.integer(fields, "p_generation")
        q_unit = strict.integer(fields, "q_unit")
        q_generation = strict.integer(fields, "q_generation")
        product_key = (
            strict.integer(fields, "p_core"),
            strict.integer(fields, "product_backing"),
        )
    except RuntimeError as error:
        raise GateError(str(error)) from error
    require(
        fields.get("terminal") == "1"
        and fields.get("order_ok") == "1"
        and fields.get("p_terminal") == "1"
        and fields.get("q_terminal") == "1"
        and fields.get("p16_reorder") == "1"
        and fields.get("q16_reorder") == "1"
        and fields.get("direct4") == "0"
        and fields.get("p_mode") == "nonfused"
        and fields.get("drains") == "0"
        and fields.get("fallbacks") == "0"
        and fields.get("cg_numerical_terminal") == "runner_join_required"
        and strict.integer(fields, "p_product_page_responses") == 4
        and strict.integer(fields, "q_product_deliveries") == 16_384
        and strict.integer(fields, "q_value_read_issues")
        == strict.integer(fields, "q_value_read_responses")
        == strict.integer(fields, "q_value_fills")
        and strict.integer(fields, "p_A_FIRST_ISSUE")
        >= strict.integer(fields, "p_ROW_OFFSET_LAST_INSERT")
        and strict.integer(fields, "q_A_FIRST_ISSUE")
        >= strict.integer(fields, "q_ROW_OFFSET_LAST_INSERT"),
        f"strict whole-window linkage failed: {fields}",
    )
    require(
        p_token // 8 == strict.integer(fields, "p_core"),
        f"p token/core identity changed: {fields}",
    )
    return (p_token, p_generation), (q_unit, q_generation), product_key


def scan_strict_trace(
    trace: Path,
    expected_windows: int = EXPECTED_WINDOWS,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    require(
        trace.is_file()
        and not trace.is_symlink()
        and trace.stat().st_size > 0,
        "missing nonempty strict trace",
    )
    counts = {
        "p_timing": 0,
        "q_timing": 0,
        "whole_windows": 0,
        "product_pages": 0,
        "p_backing_writes": 0,
        "lines_scanned": 0,
        "p_b_lines": 0,
        "q_b_lines": 0,
        "p_descriptors": 0,
        "q_descriptors": 0,
        "p_a_issues": 0,
        "q_a_issues": 0,
        "p_backing_issues": 0,
        "q_backing_issues": 0,
        "p_pages_ready": 0,
        "q_pages_ready": 0,
    }
    p_lifetimes: set[tuple[int, int]] = set()
    q_lifetimes: set[tuple[int, int]] = set()
    consumed_p: set[tuple[int, int]] = set()
    consumed_q: set[tuple[int, int]] = set()
    product_active: dict[tuple[int, int], dict[str, Any]] = {}
    product_complete: dict[tuple[int, int], int] = {}
    trace_digest = hashlib.sha256()

    with trace.open("rb") as stream:
        for raw_line in stream:
            trace_digest.update(raw_line)
            line = raw_line.decode("utf-8", errors="replace")
            counts["lines_scanned"] += 1
            if (
                progress is not None
                and counts["lines_scanned"] % 1_000_000 == 0
            ):
                progress(counts["lines_scanned"], counts["whole_windows"])
            if "event=strict_two_phase_timing " in line:
                fields = strict.parse_kv(line)
                try:
                    strict.validate_timing(fields, page_fed=False)
                    lifetime = (
                        strict.integer(fields, "token"),
                        strict.integer(fields, "generation"),
                    )
                except RuntimeError as error:
                    raise GateError(str(error)) from error
                require(
                    lifetime not in p_lifetimes,
                    f"duplicate p lifetime: {lifetime}",
                )
                p_lifetimes.add(lifetime)
                counts["p_timing"] += 1
                for target, field in (
                    ("p_b_lines", "b_lines"),
                    ("p_descriptors", "descriptors"),
                    ("p_a_issues", "a_issues"),
                    ("p_backing_issues", "backing_issues"),
                    ("p_pages_ready", "pages_ready"),
                ):
                    counts[target] += strict.integer(fields, field)
                continue
            if "event=strict_page_fed_two_phase_timing " in line:
                fields = strict.parse_kv(line)
                try:
                    strict.validate_timing(fields, page_fed=True)
                    lifetime = (
                        strict.integer(fields, "unit"),
                        strict.integer(fields, "generation"),
                    )
                except RuntimeError as error:
                    raise GateError(str(error)) from error
                require(
                    lifetime not in q_lifetimes,
                    f"duplicate q lifetime: {lifetime}",
                )
                q_lifetimes.add(lifetime)
                counts["q_timing"] += 1
                for target, field in (
                    ("q_b_lines", "b_lines"),
                    ("q_descriptors", "descriptors"),
                    ("q_a_issues", "a_issues"),
                    ("q_backing_issues", "backing_issues"),
                    ("q_pages_ready", "pages_ready"),
                ):
                    counts[target] += strict.integer(fields, field)
                continue
            if "event=strict_product_page_response " in line:
                fields = strict.parse_kv(line)
                key = (
                    strict.integer(fields, "core"),
                    strict.integer(fields, "backing"),
                )
                page = strict.integer(fields, "page")
                generation = strict.integer(fields, "generation")
                try:
                    reported, denominator = (
                        int(value) for value in fields["pages"].split("/", 1)
                    )
                except (KeyError, ValueError) as error:
                    raise GateError(
                        f"bad product lifecycle: {fields}"
                    ) from error
                state = product_active.setdefault(
                    key, {"pages": set(), "generations": set()}
                )
                require(
                    denominator == 4
                    and reported == len(state["pages"]) + 1
                    and page == reported - 1
                    and page not in state["pages"]
                    and generation not in state["generations"],
                    f"out-of-order product page response: {fields}",
                )
                state["pages"].add(page)
                state["generations"].add(generation)
                counts["product_pages"] += 1
                if reported == 4:
                    require(
                        state["pages"] == {0, 1, 2, 3}
                        and len(state["generations"]) == 4,
                        f"incomplete product page group: {state}",
                    )
                    product_complete[key] = product_complete.get(key, 0) + 1
                    product_active[key] = {
                        "pages": set(),
                        "generations": set(),
                    }
                continue
            if "event=strict_cg_p16_q16_window " in line:
                fields = strict.parse_kv(line)
                p_lifetime, q_lifetime, key = _validate_whole(fields)
                require(
                    p_lifetime in p_lifetimes
                    and q_lifetime in q_lifetimes
                    and p_lifetime not in consumed_p
                    and q_lifetime not in consumed_q,
                    "whole window does not own unique p/q generations: "
                    f"{fields}",
                )
                require(
                    product_complete.get(key, 0) > 0,
                    "whole window lacks response-bearing product pages: "
                    f"{fields}",
                )
                product_complete[key] -= 1
                consumed_p.add(p_lifetime)
                consumed_q.add(q_lifetime)
                counts["whole_windows"] += 1
                continue
            if "event=backing_write_issue " in line:
                fields = strict.parse_kv(line)
                require(
                    strict.integer(fields, "bytes") == 64,
                    f"non-64-byte P backing write: {fields}",
                )
                counts["p_backing_writes"] += 1

    require(
        counts["p_timing"]
        == counts["q_timing"]
        == counts["whole_windows"]
        == expected_windows,
        f"strict p/q/whole counts changed: {counts}",
    )
    require(
        counts["product_pages"] == 4 * expected_windows,
        f"product response count changed: {counts['product_pages']}",
    )
    require(
        counts["p_backing_writes"] == counts["p_backing_issues"] > 0,
        "P write trace/timing count mismatch",
    )
    require(
        counts["p_backing_writes"] < expected_windows * 16_384,
        "masked writes did not combine P retirement",
    )
    require(
        len(consumed_p) == len(consumed_q) == expected_windows
        and all(not state["pages"] for state in product_active.values())
        and all(value == 0 for value in product_complete.values()),
        "strict trace retained unconsumed generation/page state",
    )
    counts["trace_bytes"] = trace.stat().st_size
    counts["trace_sha256"] = trace_digest.hexdigest()
    return counts


def validate_strict_stats_values(
    values: dict[str, int], trace: dict[str, Any]
) -> None:
    exact = {
        "IND_StrictTwoPhaseOperations": 2 * EXPECTED_WINDOWS,
        "IND_StrictTwoPhaseBFetchLines": (
            trace["p_b_lines"] + trace["q_b_lines"]
        ),
        "IND_StrictTwoPhaseDescriptors": (
            trace["p_descriptors"] + trace["q_descriptors"]
        ),
        "IND_StrictTwoPhaseAIssues": (
            trace["p_a_issues"] + trace["q_a_issues"]
        ),
        "IND_StrictTwoPhaseBackingIssues": (
            trace["p_backing_issues"] + trace["q_backing_issues"]
        ),
        "IND_StrictTwoPhasePagesReady": (
            trace["p_pages_ready"] + trace["q_pages_ready"]
        ),
        "IND_NumOTEpochDrain": 0,
    }
    require(
        all(values.get(name) == expected for name, expected in exact.items()),
        "strict trace/stat work identity failed",
    )
    for name in (
        "IND_StrictTwoPhaseBFetchCycles",
        "IND_StrictTwoPhaseRowOffsetCycles",
        "IND_StrictTwoPhaseAIssueCycles",
        "IND_StrictTwoPhaseBackingCycles",
        "IND_StrictTwoPhasePageCycles",
        "IND_StrictTwoPhaseConsumerCycles",
    ):
        require(values.get(name, 0) > 0, f"empty strict timing stat: {name}")
    require(
        all(values.get(name) == 0 for name in FUSED_ZERO_STATS),
        "fused P16 counters are nonzero",
    )


def validate_selection(lines: list[str]) -> dict[str, str]:
    line = base.exactly_one(
        lines,
        rf"^CG_LOGICAL16_RMW_SELECTION treatment={TREATMENT} .*$",
        "candidate selection",
    )
    fields = base.parse_kv(line)
    expected = {
        "treatment": TREATMENT,
        "producer": "physical_page_mul_direct_index_admit",
        "p_gather_mode": "virtual_16k",
        "p16_reorder_preserved": "1",
        "q16_reorder_preserved": "1",
        "external_coherent_backing_bytes": "524288",
        "physical_spd_payload_bytes": "524288",
        "virtual_p_backing_bytes": "262144",
        "coherent_index_backing_bytes": "0",
        "host_payload_access": "0",
        "global_fallbacks": "0",
    }
    require(
        all(fields.get(name) == value for name, value in expected.items()),
        "candidate selection/reorder/storage identity changed",
    )
    return fields


def validate_restore(
    run: Path,
    authority_fields: dict[str, str],
    progress: Callable[[int, int], None] | None = None,
) -> tuple[dict[str, Any], dict[str, float]]:
    log = run / "restore.log"
    require(log.is_file() and log.stat().st_size > 0, "missing restore log")
    lines = log.read_text(errors="replace").splitlines()
    require(
        not any(base.FATAL_RE.search(line) for line in lines),
        "fatal restore text",
    )
    base.exactly_one(
        lines,
        r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$",
        "m5 terminal",
    )
    require(
        sum(line == "ROI End!!!" for line in lines) == 1, "ROI did not close"
    )
    selection = validate_selection(lines)
    terminal_line = base.exactly_one(
        lines,
        rf"^CG_LOGICAL16_RMW_TERMINAL treatment={TREATMENT} .* result=PASS$",
        "page-fed p16/q16 terminal",
    )
    terminal_fields = base.parse_kv(terminal_line)
    terminal = full.validate_terminal(terminal_fields)
    terminal_extra = {
        "fused_p16_product_windows": "0",
        "direct4_product_page_fed_q16_windows": "0",
        "virtual_p_allocation_bytes": "262144",
        "virtual_p_write_bytes": "718274560",
        "virtual_p_read_bytes": "718274560",
        "product_publisher_lines": "11223040",
        "hidden_spill_bytes": "0",
        "global_fallbacks": "0",
    }
    require(
        all(
            terminal_fields.get(name) == value
            for name, value in terminal_extra.items()
        ),
        "full terminal strict/nonfused/storage closure changed",
    )
    validate_config(run / "config.ini")
    stats = lane.validate_stats(run / "stats.txt")
    for name in STRICT_EXTRA_STATS:
        stats[name] = base.first_stat_sum(run / "stats.txt", name)
    trace = scan_strict_trace(run / "strict_trace.log", progress=progress)
    validate_strict_stats_values(stats, trace)
    _, candidate_fields = base.fingerprint_fields(log)
    numerical_deltas = base.validate_numerical(
        candidate_fields, authority_fields
    )
    return {
        "selection": selection,
        "terminal": terminal,
        "terminal_line": terminal_line,
        "fingerprint": candidate_fields,
        "stats": stats,
        "strict_trace": trace,
        "all_p_writes_64_bytes": True,
        "strict_ordering": True,
    }, numerical_deltas


def proc_start_ticks(pid: int) -> int | None:
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return int(text[text.rfind(")") + 2 :].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def write_progress(out: Path, stage: str, **fields: Any) -> None:
    record = {
        "schema": "dx100.cg.strict_line_combined_full_progress.v1",
        "stage": stage,
        "updated_unix_ns": time.time_ns(),
        **fields,
    }
    atomic_json(out / "progress.json", record)


def run_logged_with_progress(
    command: list[str],
    log: Path,
    environment: dict[str, str],
    out: Path,
    stage: str,
) -> None:
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        start_ticks = proc_start_ticks(process.pid)
        while True:
            try:
                returncode = process.wait(timeout=30)
                break
            except subprocess.TimeoutExpired:
                trace = log.parent / "strict_trace.log"
                write_progress(
                    out,
                    stage,
                    child_pid=process.pid,
                    child_proc_start_ticks=start_ticks,
                    elapsed_seconds=int(time.monotonic() - started),
                    log_bytes=log.stat().st_size,
                    trace_bytes=(
                        trace.stat().st_size if trace.exists() else 0
                    ),
                )
    log.with_suffix(log.suffix + ".exit").write_text(
        f"{returncode}\n", encoding="utf-8"
    )
    write_progress(
        out,
        stage + "_exited",
        child_pid=process.pid,
        child_proc_start_ticks=start_ticks,
        elapsed_seconds=int(time.monotonic() - started),
        returncode=returncode,
        log_bytes=log.stat().st_size,
    )
    require(returncode == 0, f"{stage} failed; see {log}")


def immutable_artifact_ledger(paths: Iterable[Path]) -> str:
    records = []
    for path in paths:
        require(
            path.is_file() and not path.is_symlink(),
            f"bad immutable artifact: {path}",
        )
        records.append(f"{sha256_file(path)}  {path}")
    require(bool(records), "empty immutable artifact ledger")
    return "\n".join(records) + "\n"


def seal_read_only(path: Path) -> None:
    path.chmod(0o444)


def execute(out: Path) -> dict[str, Any]:
    if out == ROOT or ROOT in out.parents:
        raise GateError("output must be outside the source worktree")
    require(not out.is_symlink(), "output root must not be a symlink")
    require(
        not out.exists() or not any(out.iterdir()),
        f"refusing nonempty output: {out}",
    )
    out.mkdir(parents=True, exist_ok=True)
    prelaunch = validate_prelaunch()
    before_status = source_status()
    before_commit = source_commit()
    write_progress(out, "prelaunch_passed", source_commit=before_commit)

    input_dir = out / "input"
    bin_dir = out / "bin"
    checkpoint = out / "checkpoint"
    run = out / "run"
    for directory in (input_dir, bin_dir, checkpoint, run):
        directory.mkdir(exist_ok=False)
    selector = input_dir / "page_fed_product_soa_jit.selector"
    selector.write_text(f"token_stream_ld {TREATMENT}\n", encoding="utf-8")
    selector.chmod(0o444)
    header = input_dir / "cg_data_4C.h"
    subprocess.run(
        ["cp", "--reflink=auto", str(base.FROZEN_HEADER), str(header)],
        check=True,
    )
    header.chmod(0o444)
    exact_hash(header, base.FROZEN_HEADER_SHA256, "copied full-CG header")
    require(
        header.stat().st_size == base.FROZEN_HEADER_BYTES,
        "copied full-CG header size changed",
    )

    guest = bin_dir / "cg_strict_line_combined_full"
    compile_args = base.compile_command(guest, input_dir)
    subprocess.run(compile_args, cwd=ROOT, check=True)
    checkpoint_args = checkpoint_command(guest, selector, checkpoint)
    restore_args = restore_command(guest, selector, checkpoint, run)
    write_progress(
        out,
        "guest_compiled",
        guest_sha256=sha256_file(guest),
        guest_abi_sha256=sha256_file(CURRENT_ABI),
    )

    library_path = str(base.RAMULATOR.parent)
    if os.environ.get("LD_LIBRARY_PATH"):
        library_path += ":" + os.environ["LD_LIBRARY_PATH"]
    environment = dict(
        os.environ,
        LD_LIBRARY_PATH=library_path,
        OMP_NUM_THREADS="4",
        OMP_PROC_BIND="false",
    )
    ldd_output = subprocess.check_output(
        ["ldd", str(STRICT_GEM5)], env=environment, text=True
    )
    match = re.search(r"^[ \t]*libramulator\.so => (\S+)", ldd_output, re.M)
    require(
        match is not None
        and Path(match.group(1)).resolve() == base.RAMULATOR.resolve(),
        "strict gem5 did not resolve frozen Ramulator",
    )

    authority_files = (
        tuple(
            base.CERTIFICATE_ROOT / name
            for name in sorted(base.CERTIFICATE_FILES)
        )
        + tuple(
            lane.LANE_SELECTION_ROOT / name
            for name in sorted(lane.LANE_SELECTION_HASHES)
        )
        + tuple(STRICT_LINE_ROOT / name for name in sorted(STRICT_LINE_HASHES))
        + tuple(
            FROZEN_FULL_CERTIFICATE_ROOT / name
            for name in sorted(FROZEN_FULL_CERTIFICATE_HASHES)
        )
    )
    immutable = (
        STRICT_GEM5,
        base.RAMULATOR,
        guest,
        selector,
        header,
        base.NATIVE_LOG,
        base.NATIVE_STATS,
        Path(__file__).resolve(),
        LANE_RUNNER_PATH,
        lane.FULL_PATH,
        full.BASE_PATH,
        STRICT_GATE_PATH,
        *base.GUEST_COMPILE_INPUTS,
        *base.CONFIG_INPUTS,
        *authority_files,
    )
    artifacts_before = immutable_artifact_ledger(immutable)
    artifact_before_path = input_dir / "artifact_sha256.before"
    artifact_before_path.write_text(artifacts_before, encoding="utf-8")
    (input_dir / "source_status.before").write_text(
        before_status, encoding="utf-8"
    )
    (input_dir / "source_commit.before").write_text(
        before_commit + "\n", encoding="utf-8"
    )
    for name, command in (
        ("compile", compile_args),
        ("checkpoint", checkpoint_args),
        ("restore", restore_args),
    ):
        (input_dir / f"{name}_command.json").write_text(
            json_text(command), encoding="utf-8"
        )

    manifest = {
        "schema": "dx100.cg.strict_line_combined_full_manifest.v1",
        "terminal": False,
        "candidate_only": True,
        "candidate_restores": 1,
        "checkpoint_creations": 1,
        "native_runs": 0,
        "direct4_runs": 0,
        "fused_runs": 0,
        "control_runs": 0,
        "other_candidate_runs": 0,
        "timeout": "none",
        "cg_na": CG_NA,
        "source_commit": before_commit,
        "selector": TREATMENT,
        "treatment": {
            "virtual_strict_two_phase": True,
            "virtual_masked_writes": True,
            "value_cache_enable": True,
            "active_value_owners": 32,
            "apply_lanes": APPLY_LANES,
            "producer": "nonfused_p16_four_response_pages_then_q16",
        },
        "expected": {
            "p_windows": EXPECTED_WINDOWS,
            "q_windows": EXPECTED_WINDOWS,
            "whole_windows": EXPECTED_WINDOWS,
            "product_page_responses": EXPECTED_PRODUCT_PAGES,
            "words": EXPECTED_WORDS,
            "a_lines": EXPECTED_A_LINES,
            "all_p_writes_64_bytes": True,
            "drains": 0,
            "fallbacks": 0,
        },
        "prelaunch": prelaunch,
        "commands": {
            "compile": compile_args,
            "checkpoint": checkpoint_args,
            "restore": restore_args,
        },
    }
    manifest_path = out / "manifest.json"
    manifest_path.write_text(json_text(manifest), encoding="utf-8")

    run_logged_with_progress(
        checkpoint_args,
        out / "checkpoint.log",
        environment,
        out,
        "checkpoint_running",
    )
    checkpoint_lines = (
        (out / "checkpoint.log").read_text(errors="replace").splitlines()
    )
    base.exactly_one(
        checkpoint_lines,
        r"^Exiting @ tick [0-9]+ because checkpoint$",
        "checkpoint terminal",
    )
    require(
        not any(
            line.startswith(
                ("CG_FINGERPRINT ", "CG_LOGICAL16_RMW_TERMINAL ", "ROI End!!!")
            )
            for line in checkpoint_lines
        ),
        "new checkpoint crossed the deferred treatment boundary",
    )
    checkpoint_before = base.tree_ledger(checkpoint)
    checkpoint_before_path = input_dir / "checkpoint.files.sha256.before"
    checkpoint_before_path.write_text(checkpoint_before, encoding="utf-8")
    write_progress(
        out,
        "checkpoint_complete",
        checkpoint_ledger_sha256=hashlib.sha256(
            checkpoint_before.encode()
        ).hexdigest(),
    )

    run_logged_with_progress(
        restore_args,
        run / "restore.log",
        environment,
        out,
        "restore_running",
    )
    require(
        (run / "restore.log.exit").read_text(encoding="utf-8") == "0\n",
        "restore wrapper exit is not zero",
    )
    write_progress(out, "validating_candidate")
    checkpoint_after = base.tree_ledger(checkpoint)
    checkpoint_after_path = input_dir / "checkpoint.files.sha256.after"
    checkpoint_after_path.write_text(checkpoint_after, encoding="utf-8")
    require(checkpoint_after == checkpoint_before, "new checkpoint changed")
    artifacts_after = immutable_artifact_ledger(immutable)
    artifact_after_path = input_dir / "artifact_sha256.after"
    artifact_after_path.write_text(artifacts_after, encoding="utf-8")
    require(artifacts_after == artifacts_before, "immutable artifact changed")
    after_status = source_status()
    after_commit = source_commit()
    (input_dir / "source_status.after").write_text(
        after_status, encoding="utf-8"
    )
    (input_dir / "source_commit.after").write_text(
        after_commit + "\n", encoding="utf-8"
    )
    require(after_status == before_status, "source status changed during run")
    require(after_commit == before_commit, "source commit changed during run")

    base.validate_certificate()
    lane.validate_lane_selection_authority()
    validate_strict_selection_authority()
    validate_frozen_full_certificate()
    exact_hash(
        base.FROZEN_HEADER,
        base.FROZEN_HEADER_SHA256,
        "frozen full-CG header after run",
    )
    exact_hash(base.NATIVE_LOG, base.NATIVE_LOG_SHA256, "numerical authority")
    _, authority_fields = base.fingerprint_fields(base.NATIVE_LOG)

    def trace_progress(lines: int, windows: int) -> None:
        write_progress(
            out,
            "validating_strict_trace",
            trace_lines_scanned=lines,
            whole_windows_seen=windows,
        )

    candidate, deltas = validate_restore(
        run, authority_fields, progress=trace_progress
    )
    sim_ticks = candidate["stats"]["simTicks"]
    require(isinstance(sim_ticks, int) and sim_ticks > 0, "invalid simTicks")
    result = {
        "schema": "dx100.cg.strict_line_combined_full_result.v1",
        "terminal": True,
        "verdict": "PASS_NUMERICAL_MECHANISM_CORRECT",
        "candidate_only": True,
        "observations": 1,
        "native_runs": 0,
        "direct4_runs": 0,
        "fused_runs": 0,
        "official_nas_verification": False,
        "native_speedup_claim": False,
        "direct4_claim": False,
        "source_commit": before_commit,
        "gem5_sha256": STRICT_GEM5_SHA256,
        "ramulator_sha256": base.RAMULATOR_SHA256,
        "guest_sha256": sha256_file(guest),
        "guest_abi_sha256": CURRENT_ABI_SHA256,
        "checkpoint_ledger_sha256": hashlib.sha256(
            checkpoint_before.encode()
        ).hexdigest(),
        "checkpoint_reused": False,
        "new_treatment_neutral_checkpoint": True,
        "numerical_authority": prelaunch["numerical_authority"],
        "numerical_relative_deltas_vs_authority": deltas,
        "strict_line_combined_authority": prelaunch[
            "strict_line_combined_authority"
        ],
        "first_roi_simTicks": sim_ticks,
        "candidate": candidate,
    }
    result_path = out / "result.json"
    base.atomic_write(result_path, json_text(result))
    certified_paths = (
        manifest_path,
        result_path,
        run / "restore.log",
        run / "restore.log.exit",
        run / "stats.txt",
        run / "config.ini",
        run / "strict_trace.log",
        checkpoint_before_path,
        checkpoint_after_path,
        artifact_before_path,
        artifact_after_path,
        input_dir / "source_status.before",
        input_dir / "source_status.after",
        input_dir / "source_commit.before",
        input_dir / "source_commit.after",
    )
    certified = immutable_artifact_ledger(certified_paths)
    certified_path = out / "certified_artifacts.sha256"
    certified_path.write_text(certified, encoding="utf-8")
    gate_text = (
        "PASS_NUMERICAL_MECHANISM_CORRECT\n"
        "candidate_only=true\n"
        "strict_ordering=PASS\n"
        "all_p_writes_64_bytes=true\n"
        f"result_sha256={sha256_file(result_path)}\n"
        f"certified_artifacts_sha256={sha256_file(certified_path)}\n"
    )
    gate_path = out / "gate.complete"
    base.atomic_write(gate_path, gate_text)
    for path in (
        checkpoint_before_path,
        checkpoint_after_path,
        artifact_before_path,
        artifact_after_path,
        certified_path,
        gate_path,
    ):
        seal_read_only(path)
    runtime_terminal = {
        "schema": "dx100.cg.strict_line_combined_full_runtime.v1",
        "terminal": True,
        "accepted": True,
        "verdict": result["verdict"],
        "simTicks": sim_ticks,
        "result_sha256": sha256_file(result_path),
        "gate_sha256": sha256_file(gate_path),
    }
    atomic_json(out / "runtime_terminal.json", runtime_terminal)
    write_progress(
        out,
        "accepted",
        simTicks=sim_ticks,
        result_sha256=runtime_terminal["result_sha256"],
    )
    return runtime_terminal


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path, nargs="?")
    parser.add_argument("--prelaunch-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.prelaunch_only:
        require(
            args.out is None, "--prelaunch-only does not take an output root"
        )
        print(json.dumps(validate_prelaunch(), sort_keys=True))
        return 0
    require(args.out is not None, "candidate output root is required")
    out = args.out.resolve()
    try:
        terminal = execute(out)
    except Exception as error:
        if out.exists() and out.is_dir() and not out.is_symlink():
            try:
                atomic_json(
                    out / "runtime_terminal.json",
                    {
                        "schema": (
                            "dx100.cg.strict_line_combined_full_runtime.v1"
                        ),
                        "terminal": True,
                        "accepted": False,
                        "error_type": type(error).__name__,
                        "error": str(error),
                    },
                )
                write_progress(
                    out,
                    "rejected",
                    error_type=type(error).__name__,
                    error=str(error),
                )
            except Exception:
                pass
        raise
    print(json.dumps(terminal, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
