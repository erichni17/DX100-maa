#!/usr/bin/env python3
"""Isolate strict ordering on the accepted CG page-fed path.

The sealed four-arm matrix already contains the strict page-fed observation and
all native/original controls. This successor launches exactly one additional
restore with the same page-fed selector and removes only
``--maa_virtual_strict_two_phase``. It therefore measures ordering policy,
not the larger legacy-versus-page-fed execution-path delta.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.scripts import (
    run_cg_strict_fourarm_matrix as base,
)  # noqa: E402

AUTHORITY = Path(
    "/data1/nier/worktrees/codex-coordination/sessions/"
    "cg-strict-fourarm-matrix-20260831-20260831-104028-a26c56c4/"
    "evidence/cg-strict-fourarm-na256-r5"
)
EXPECTED_AUTHORITY_LEDGER_SHA256 = (
    "1c13e2b93f489e6958d880fcfa0c55785e7847bb8932fee8dd01086eb2bc0881"
)
ARM = base.Arm("page_fed_nonstrict", "page_fed_product_soa_jit", 4_096, False)
STRICT_FLAG = "--maa_virtual_strict_two_phase"


class AblationError(RuntimeError):
    """Fail-closed CG strict-bit ablation error."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AblationError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_authority() -> dict[str, Any]:
    require(AUTHORITY.is_dir(), "missing sealed CG authority")
    require(
        sha256(AUTHORITY / "raw_root.sha256")
        == EXPECTED_AUTHORITY_LEDGER_SHA256,
        "authority ledger identity changed",
    )
    authority = base.validate_existing(AUTHORITY)
    strict = authority["arms"]["strict_two_pass"]
    require(strict["strict"] is True, "authority strict arm is not strict")
    require(
        strict["treatment"] == ARM.selector,
        "authority strict arm uses a different treatment",
    )
    return authority


def derived_command(out: Path) -> list[str]:
    command = json.loads(
        (AUTHORITY / "arms/strict_two_pass/command.json").read_text()
    )
    require(command.count(STRICT_FLAG) == 1, "strict flag count changed")
    command.remove(STRICT_FLAG)
    outdirs = [
        i for i, token in enumerate(command) if token.startswith("--outdir=")
    ]
    require(len(outdirs) == 1, "strict command outdir count changed")
    command[outdirs[0]] = f"--outdir={out / 'arms' / ARM.name}"
    strict_command = json.loads(
        (AUTHORITY / "arms/strict_two_pass/command.json").read_text()
    )
    require(
        base.normalize(command) == base.normalize(strict_command),
        "derived command changed outside strict policy and output",
    )
    return command


def artifact_paths(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.name != "artifacts.sha256"
    )


def write_ledger(root: Path) -> None:
    lines = [
        f"{sha256(path)}  {path.relative_to(root)}"
        for path in artifact_paths(root)
    ]
    (root / "artifacts.sha256").write_text("\n".join(lines) + "\n")


def verify_ledger(root: Path) -> None:
    ledger = root / "artifacts.sha256"
    require(ledger.is_file(), "missing ablation ledger")
    for number, line in enumerate(ledger.read_text().splitlines(), 1):
        digest, relative = line.split("  ", 1)
        path = root / relative
        require(
            path.is_file() and sha256(path) == digest,
            f"artifact changed at ledger line {number}: {relative}",
        )


def classify(root: Path, *, write_result: bool) -> dict[str, Any]:
    authority = verify_authority()
    strict = authority["arms"]["strict_two_pass"]
    candidate = base.validate_arm(root, ARM, strict, write_result=write_result)
    ticks = candidate["values"]["simTicks"]
    strict_ticks = strict["values"]["simTicks"]
    result = {
        "schema": "dx100.cg.strict_bit_ablation.v1",
        "terminal": True,
        "decision": "ACCEPT_PAGE_FED_STRICT_BIT_ABLATION",
        "authority": str(AUTHORITY),
        "same_page_fed_treatment": True,
        "only_policy_delta": "virtual_strict_two_phase",
        "page_fed_nonstrict": candidate,
        "strict_two_pass": strict,
        "comparisons": {
            "strict_latency_change_vs_page_fed_nonstrict": (
                strict_ticks / ticks - 1.0
            ),
            "page_fed_nonstrict_over_strict_speedup": ticks / strict_ticks,
        },
    }
    if write_result:
        (root / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n"
        )
    return result


def run(root: Path) -> dict[str, Any]:
    require(not root.exists(), f"output already exists: {root}")
    require(
        not subprocess.check_output(
            ["git", "status", "--short"], cwd=ROOT, text=True
        ).strip(),
        "refusing launch from dirty worktree",
    )
    verify_authority()
    arm_root = root / "arms" / ARM.name
    arm_root.mkdir(parents=True)
    selector = arm_root / "treatment.selector"
    selector.write_text(f"token_stream_ld {ARM.selector}\n")
    selector.chmod(0o444)
    command = derived_command(root)
    (arm_root / "command.json").write_text(
        json.dumps(command, indent=2) + "\n"
    )
    environment = dict(
        os.environ,
        LD_LIBRARY_PATH=str(AUTHORITY / "input"),
        OMP_NUM_THREADS="4",
        OMP_PROC_BIND="false",
    )
    process, _, record = base.launch_with_selector(
        command, selector, arm_root / "restore.log", environment
    )
    returncode = process.wait()
    record.update(
        {
            "returncode": returncode,
            "ended_ns": time.time_ns(),
            "pid_absent": base.proc_start_ticks(process.pid) is None,
        }
    )
    (arm_root / "restore.exit").write_text(f"{returncode}\n")
    (arm_root / "process.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n"
    )
    require(returncode == 0, "page-fed nonstrict restore failed")
    result = classify(root, write_result=True)
    (root / "gate.complete").write_text(
        "COMPLETE_CG_STRICT_BIT_ABLATION\n"
        "correctness=EXACT_FINGERPRINT_AND_REDUCTIONS\n"
        "treatment_delta=STRICT_POLICY_ONLY\n"
    )
    write_ledger(root)
    verify_ledger(root)
    return result


def validate(root: Path) -> dict[str, Any]:
    verify_ledger(root)
    sealed = json.loads((root / "result.json").read_text())
    recomputed = classify(root, write_result=False)
    require(recomputed == sealed, "sealed ablation result changed")
    verify_ledger(root)
    return sealed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "validate", "preflight"))
    parser.add_argument("out", nargs="?", type=Path)
    args = parser.parse_args()
    if args.command == "preflight":
        result: dict[str, Any] = {
            "authority": str(AUTHORITY),
            "authority_valid": bool(verify_authority()),
        }
    else:
        require(args.out is not None, f"{args.command} requires OUT")
        result = (
            run(args.out.resolve())
            if args.command == "run"
            else validate(args.out.resolve())
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
