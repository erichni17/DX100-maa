#!/usr/bin/env python3
"""Recover the GZZ strict hybrid after selector and payload fixes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.scripts import run_ume_two_pass_matrix as base  # noqa: E402

AUTHORITY = Path("/tmp/ume-gzz-two-pass-20260831-39080929")
DEFAULT_GEM5 = ROOT / "build/X86/gem5.opt"
HYBRID_ARMS = tuple(arm for arm in base.ARMS if arm.selector is not None)
RECOVERY_ARMS = tuple(arm for arm in HYBRID_ARMS if arm.name == "strict_bounded_hybrid")


class BridgeError(RuntimeError):
    """Fail-closed GZZ selector-bridge error."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise BridgeError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_authority() -> dict[str, Any]:
    require(AUTHORITY.is_dir(), "missing rejected GZZ authority")
    manifest = json.loads((AUTHORITY / "manifest.json").read_text())
    require(
        manifest["gem5_sha256"] == base.EXPECTED_GEM5_SHA256
        and sha256(AUTHORITY / "inputs/gem5.opt") == base.EXPECTED_GEM5_SHA256,
        "authority simulator identity",
    )
    native = {}
    for arm in base.ARMS[:2]:
        native[arm.name] = base.classify_arm(AUTHORITY, arm, manifest)
    require(
        native["native16"]["output_hash"]
        == native["native4"]["output_hash"]
        == base.EXPECTED_OUTPUT_HASH,
        "native authority output mismatch",
    )
    failure = json.loads((AUTHORITY / "failure.json").read_text())
    require(
        failure.get("decision") == "REJECT"
        and "missing terminal m5_exit" in failure.get("reason", ""),
        "authority failed for an unexpected reason",
    )
    for arm in HYBRID_ARMS:
        log = (AUTHORITY / "arms" / arm.name / "restore.log").read_text(
            errors="replace"
        )
        require(
            "GZZ virtual consumer selector:" in log
            and "because m5_exit instruction encountered" not in log,
            f"{arm.name}: authority failure signature changed",
        )
    return {"manifest": manifest, "native": native}


def artifact_paths(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.name != "artifacts.sha256"
    )


def write_ledger(root: Path) -> None:
    lines = [
        f"{sha256(path)}  {path.relative_to(root)}" for path in artifact_paths(root)
    ]
    (root / "artifacts.sha256").write_text("\n".join(lines) + "\n")


def verify_ledger(root: Path) -> None:
    seen = set()
    for number, line in enumerate(
        (root / "artifacts.sha256").read_text().splitlines(), 1
    ):
        digest, relative = line.split("  ", 1)
        require(relative not in seen, f"duplicate ledger line {number}")
        seen.add(relative)
        path = root / relative
        require(
            path.is_file() and sha256(path) == digest,
            f"artifact changed: {relative}",
        )


def classify(root: Path) -> dict[str, Any]:
    authority = verify_authority()
    manifest_path = root / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text())
        if manifest_path.is_file()
        else authority["manifest"]
    )
    hybrids = {
        arm.name: base.classify_arm(root, arm, manifest) for arm in RECOVERY_ARMS
    }
    for arm in RECOVERY_ARMS:
        command = json.loads(
            (root / "arms" / arm.name / "restore.command.json").read_text()
        )
        simulator = Path(command[0])
        guest = Path(command[command.index("--cmd") + 1])
        require(
            simulator.is_file() and sha256(simulator) == manifest["gem5_sha256"],
            f"{arm.name}: simulator identity",
        )
        require(
            guest.is_file() and sha256(guest) == manifest["guest_sha256"][arm.guest],
            f"{arm.name}: guest identity",
        )
    require(
        len(
            {
                authority["native"]["native16"]["output_hash"],
                authority["native"]["native4"]["output_hash"],
                *(item["output_hash"] for item in hybrids.values()),
            }
        )
        == 1,
        "cross-arm output mismatch",
    )
    strict = hybrids["strict_bounded_hybrid"]["counters"]
    native16 = authority["native"]["native16"]["counters"]
    for field in ("numInst_INDRD", "numInst_INDRMW", "index_words"):
        require(strict[field] == native16[field], f"work differs: {field}")
    ticks = {
        **{
            name: item["counters"]["simTicks"]
            for name, item in authority["native"].items()
        },
        **{name: item["counters"]["simTicks"] for name, item in hybrids.items()},
    }
    return {
        "schema": "dx100.ume_gzz_shared_payload.v2",
        "terminal": True,
        "decision": "ACCEPT_GZZ_SHARED_PAYLOAD_STRICT",
        "authority": str(AUTHORITY),
        "native_controls_reused": True,
        "cross_binary_orientation_only": True,
        "hybrids": hybrids,
        "ticks": ticks,
        "comparisons": {
            "native16_over_strict": (
                ticks["native16"] / ticks["strict_bounded_hybrid"]
            ),
            "native4_over_strict": (ticks["native4"] / ticks["strict_bounded_hybrid"]),
        },
    }


def build_hybrid_guest(root: Path) -> tuple[Path, list[list[str]]]:
    build = root / "inputs" / "build"
    build.mkdir(parents=True)
    m5op_source = ROOT / "util/m5/src/abi/x86/m5op.S"
    m5op = build / "m5op.o"
    guest = build / "gradzatz_hybrid"
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
        with (build / f"build.{index}.log").open("wb") as output:
            completed = subprocess.run(
                command, stdout=output, stderr=subprocess.STDOUT, check=False
            )
        require(completed.returncode == 0, f"hybrid guest build {index}")
    guest.chmod(0o555)
    return guest, commands


def run_hybrid_arm(
    root: Path,
    arm: base.Arm,
    guest: Path,
    selector: Path,
    gem5: Path,
    environment: dict[str, str],
) -> None:
    checkpoint = root / "checkpoints" / arm.name
    checkpoint.mkdir(parents=True)
    checkpoint_command = base.checkpoint_command(
        gem5,
        guest,
        checkpoint / "gem5",
        base.arm_options(arm, selector),
    )
    returncode = base.run_logged(
        checkpoint_command, checkpoint, "checkpoint", environment
    )
    require(returncode == 0, f"{arm.name}: checkpoint failed")
    log = (checkpoint / "checkpoint.log").read_text(errors="replace")
    require("because checkpoint" in log, f"{arm.name}: checkpoint marker")
    identity = base.tree_identity(checkpoint / "gem5")
    (checkpoint / "identity.json").write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n"
    )

    arm_root = root / "arms" / arm.name
    arm_root.mkdir(parents=True)
    command = base.common_restore_command(
        gem5,
        AUTHORITY / "inputs/ramulator.yaml",
        checkpoint / "gem5",
        guest,
        base.arm_options(arm, selector),
        arm_root / "run",
        arm,
    )
    returncode = base.run_logged(command, arm_root, "restore", environment)
    require(returncode == 0, f"{arm.name}: restore failed")
    require(
        base.tree_identity(checkpoint / "gem5")["sha256"] == identity["sha256"],
        f"{arm.name}: checkpoint mutated",
    )


def run(root: Path, gem5: Path) -> dict[str, Any]:
    require(not root.exists(), f"output exists: {root}")
    require(
        not subprocess.check_output(
            ["git", "status", "--short"], cwd=ROOT, text=True
        ).strip(),
        "refusing launch from dirty worktree",
    )
    authority = verify_authority()
    require(gem5.is_file(), f"missing simulator: {gem5}")
    simulator_sha256 = sha256(gem5)
    root.mkdir(parents=True)
    (root / "authority.json").write_text(
        json.dumps(
            {
                "root": str(AUTHORITY),
                "manifest_sha256": sha256(AUTHORITY / "manifest.json"),
                "native_control_gem5_sha256": authority["manifest"]["gem5_sha256"],
                "candidate_gem5_sha256": simulator_sha256,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (root / "inputs").mkdir()
    guest, build_commands = build_hybrid_guest(root)
    selectors = {}
    for arm in RECOVERY_ARMS:
        selector = root / "inputs" / f"{arm.name}.selector"
        selector.write_text(arm.selector + "\n")
        selector.chmod(0o444)
        selectors[arm.name] = selector
    manifest = dict(authority["manifest"])
    manifest["gem5_sha256"] = simulator_sha256
    manifest["guest_sha256"] = dict(manifest["guest_sha256"])
    manifest["guest_sha256"]["hybrid"] = sha256(guest)
    manifest["build_commands"] = build_commands
    manifest["runner_source_commit"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    manifest["selector_resolved_before_checkpoint"] = True
    manifest["native_controls_reused"] = True
    manifest["native_controls_cross_binary_default_off"] = True
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    environment = dict(
        os.environ,
        LD_LIBRARY_PATH=str(AUTHORITY / "inputs"),
        OMP_NUM_THREADS="4",
        OMP_PROC_BIND="false",
    )
    with ThreadPoolExecutor(max_workers=len(RECOVERY_ARMS)) as pool:
        futures = [
            pool.submit(
                run_hybrid_arm,
                root,
                arm,
                guest,
                selectors[arm.name],
                gem5,
                environment,
            )
            for arm in RECOVERY_ARMS
        ]
        for future in futures:
            future.result()
    result = classify(root)
    (root / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    (root / "gate.complete").write_text(
        "COMPLETE_UME_GZZ_SELECTOR_BRIDGE\ncorrectness=EXACT_REFERENCE\n"
    )
    write_ledger(root)
    verify_ledger(root)
    return result


def validate(root: Path) -> dict[str, Any]:
    verify_ledger(root)
    sealed = json.loads((root / "result.json").read_text())
    recomputed = classify(root)
    require(recomputed == sealed, "sealed GZZ result changed")
    verify_ledger(root)
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
            "authority_valid": bool(verify_authority()),
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
