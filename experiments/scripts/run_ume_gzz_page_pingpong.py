#!/usr/bin/env python3
"""Run one exact GZZ alternate-page-tile candidate against sealed r6."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.scripts import (
    run_ume_gzz_matched_consumer_matrix as matched,
)  # noqa: E402
from experiments.scripts import run_ume_gzz_selector_bridge as bridge  # noqa: E402
from experiments.scripts import run_ume_two_pass_matrix as base  # noqa: E402

AUTHORITY = Path("/data1/nier/dx100-runs/2026-08-31-ume-gzz-matched-consumer-r6")
DEFAULT_GEM5 = ROOT / "build/X86/gem5.opt"
ARM = base.ARMS[3]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise bridge.BridgeError(message)


def build_guest(root: Path) -> tuple[Path, list[list[str]]]:
    build = root / "inputs/build"
    build.mkdir(parents=True)
    m5op_source = ROOT / "util/m5/src/abi/x86/m5op.S"
    m5op = build / "m5op.o"
    guest = build / "gradzatz_hybrid_pingpong"
    commands = [
        [
            "g++",
            "-std=c++11",
            "-O3",
            "-Wall",
            "-g3",
            "-fopenmp",
            f"-I{ROOT / 'include'}",
            f"-I{ROOT / 'util/m5/src'}",
            "-DGEM5",
            "-c",
            str(m5op_source),
            "-o",
            str(m5op),
        ],
        [
            "g++",
            "-std=c++11",
            "-O3",
            "-Wall",
            "-g3",
            "-fopenmp",
            f"-I{ROOT / 'benchmarks/API'}",
            f"-I{ROOT / 'include'}",
            f"-I{ROOT / 'util/m5/src'}",
            "-DGEM5",
            "-DMAA",
            "-DNUM_CORES=4",
            "-DMAA_MEM_SIZE=0x80000000",
            "-DUME_GRADZATZ_FIXED_INPUT",
            "-DUME_GRADZATZ_OUTPUT_FINGERPRINT",
            "-DUME_GRADZATZ_EXPECTED_N=16384",
            f"-DUME_GRADZATZ_EXPECTED_HASH={base.EXPECTED_OUTPUT_HASH}ULL",
            "-DUME_GZZ_MAA_PAGE_CONSUMER",
            "-DUME_GZZ_PAGE_CONSUMER_PINGPONG",
            "-DTILE_SIZE=16384",
            "-DMAA_VIRTUAL_GATHER",
            "-DMAA_GENERAL_VIRTUAL_CONSUMER",
            "-DMAA_CONSUMER_TILE_SIZE=4096",
            str(m5op),
            str(ROOT / "benchmarks/UME/gradzatz.cpp"),
            "-o",
            str(guest),
        ],
    ]
    for index, command in enumerate(commands):
        with (build / f"build.{index}.log").open("wb") as log:
            rc = subprocess.run(
                command, stdout=log, stderr=subprocess.STDOUT, check=False
            ).returncode
        require(rc == 0, f"guest build {index} failed")
    guest.chmod(0o555)
    return guest, commands


def run_arm(
    root: Path,
    gem5: Path,
    guest: Path,
    selector: Path,
    ramulator_config: Path,
    environment: dict[str, str],
) -> None:
    checkpoint = root / "checkpoints" / ARM.name
    checkpoint.mkdir(parents=True)
    options = base.arm_options(ARM, selector)
    command = base.checkpoint_command(gem5, guest, checkpoint / "gem5", options)
    rc = base.run_logged(command, checkpoint, "checkpoint", environment)
    require(rc == 0, "ping-pong checkpoint failed")
    require(
        "because checkpoint"
        in (checkpoint / "checkpoint.log").read_text(errors="replace"),
        "ping-pong checkpoint marker",
    )
    identity = base.tree_identity(checkpoint / "gem5")
    base.atomic_json(checkpoint / "identity.json", identity)
    arm_root = root / "arms" / ARM.name
    arm_root.mkdir(parents=True)
    restore = base.common_restore_command(
        gem5,
        ramulator_config,
        checkpoint / "gem5",
        guest,
        options,
        arm_root / "run",
        ARM,
    )
    rc = base.run_logged(restore, arm_root, "restore", environment)
    require(rc == 0, "ping-pong restore failed")
    require(
        base.tree_identity(checkpoint / "gem5")["sha256"] == identity["sha256"],
        "ping-pong checkpoint mutated",
    )


def classify(root: Path) -> dict[str, Any]:
    authority = matched.validate(AUTHORITY)
    manifest = json.loads((root / "manifest.json").read_text())
    candidate = base.classify_arm(root, ARM, manifest)
    log = (root / "arms" / ARM.name / "restore.log").read_text(errors="replace")
    require(
        "UME_GZZ_PAGE_CONSUMER mode=maa_div_mul "
        "physical_tiles_per_core=8 pingpong=1 cpu_spd_payload_reads=0" in log,
        "ping-pong guest marker missing",
    )
    control = authority["arms"][ARM.name]
    require(candidate["output_hash"] == control["output_hash"], "output mismatch")
    for field in ("numInst_INDRD", "numInst_INDRMW", "index_words"):
        require(
            candidate["counters"][field] == control["counters"][field],
            f"semantic work changed: {field}",
        )
    control_ticks = control["counters"]["simTicks"]
    candidate_ticks = candidate["counters"]["simTicks"]
    return {
        "schema": "dx100.ume_gzz_page_pingpong.result.v1",
        "terminal": True,
        "decision": "ACCEPT" if candidate_ticks < control_ticks else "REJECT",
        "authority": str(AUTHORITY),
        "control_ticks": control_ticks,
        "candidate_ticks": candidate_ticks,
        "speedup": control_ticks / candidate_ticks,
        "latency_change": candidate_ticks / control_ticks - 1.0,
        "configured_tiles_per_core": 8,
        "control_used_tiles_per_core": 7,
        "candidate_used_tiles_per_core": 8,
        "candidate": candidate,
    }


def run(root: Path, gem5: Path) -> dict[str, Any]:
    require(not root.exists(), f"output exists: {root}")
    require(
        not subprocess.check_output(
            ["git", "status", "--short"], cwd=ROOT, text=True
        ).strip(),
        "refusing launch from dirty worktree",
    )
    matched.validate(AUTHORITY)
    root.mkdir(parents=True)
    inputs = root / "inputs"
    inputs.mkdir()
    frozen_gem5 = inputs / "gem5.opt"
    simulator_hash = base.copy_stable(gem5, frozen_gem5)
    frozen_gem5.chmod(0o555)
    frozen_ramulator = inputs / "libramulator.so"
    frozen_config = inputs / "ramulator.yaml"
    base.copy_stable(AUTHORITY / "inputs/libramulator.so", frozen_ramulator)
    base.copy_stable(AUTHORITY / "inputs/ramulator.yaml", frozen_config)
    guest, build_commands = build_guest(root)
    selector = inputs / "strict_bounded_hybrid.selector"
    selector.write_text(ARM.selector + "\n")
    selector.chmod(0o444)
    manifest = {
        "schema": "dx100.ume_gzz_page_pingpong.campaign.v1",
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "gem5_sha256": simulator_hash,
        "guest_sha256": {"hybrid": bridge.sha256(guest)},
        "build_commands": build_commands,
        "authority": str(AUTHORITY),
    }
    base.atomic_json(root / "manifest.json", manifest)
    environment = dict(
        os.environ,
        LD_LIBRARY_PATH=str(inputs),
        OMP_NUM_THREADS="4",
        OMP_PROC_BIND="false",
    )
    run_arm(root, frozen_gem5, guest, selector, frozen_config, environment)
    result = classify(root)
    base.atomic_json(root / "result.json", result)
    bridge.write_ledger(root)
    bridge.verify_ledger(root)
    return result


def validate(root: Path) -> dict[str, Any]:
    bridge.verify_ledger(root)
    sealed = json.loads((root / "result.json").read_text())
    require(classify(root) == sealed, "sealed ping-pong result changed")
    bridge.verify_ledger(root)
    return sealed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "validate", "preflight"))
    parser.add_argument("out", nargs="?", type=Path)
    parser.add_argument("--gem5", type=Path, default=DEFAULT_GEM5)
    args = parser.parse_args()
    if args.command == "preflight":
        result: dict[str, Any] = {
            "authority": str(AUTHORITY),
            "authority_valid": bool(matched.validate(AUTHORITY)),
        }
    else:
        require(args.out is not None, f"{args.command} requires OUT")
        result = (
            run(args.out.resolve(), args.gem5.resolve())
            if args.command == "run"
            else validate(args.out.resolve())
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
