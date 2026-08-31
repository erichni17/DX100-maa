#!/usr/bin/env python3
"""Run exact GZZ controls with one matched MAA DIV/MUL consumer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.scripts import run_ume_two_pass_matrix as base  # noqa: E402

DEFAULT_GEM5 = ROOT / "build/X86/gem5.opt"
AUTHORITY = Path("/tmp/ume-gzz-two-pass-20260831-39080929")
DEFAULT_RAMULATOR = AUTHORITY / "inputs/libramulator.so"
DEFAULT_RAMULATOR_CONFIG = AUTHORITY / "inputs/ramulator.yaml"
ARMS = (base.ARMS[0], base.ARMS[1], base.ARMS[3])


class MatrixError(RuntimeError):
    """Fail-closed matched-consumer matrix error."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise MatrixError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    seen: set[str] = set()
    for line in (root / "artifacts.sha256").read_text().splitlines():
        digest, relative = line.split("  ", 1)
        require(relative not in seen, f"duplicate ledger entry: {relative}")
        seen.add(relative)
        path = root / relative
        require(path.is_file() and sha256(path) == digest, f"changed: {relative}")


def prepare(
    root: Path, gem5: Path, ramulator: Path, ramulator_config: Path
) -> dict[str, Any]:
    require(not root.exists(), f"output exists: {root}")
    require(
        not subprocess.check_output(
            ["git", "status", "--short"], cwd=ROOT, text=True
        ).strip(),
        "refusing launch from dirty worktree",
    )
    for path in (gem5, ramulator, ramulator_config):
        require(path.is_file(), f"missing input: {path}")
    root.mkdir(parents=True)
    inputs = root / "inputs"
    inputs.mkdir()
    frozen_gem5 = inputs / "gem5.opt"
    frozen_ramulator = inputs / "libramulator.so"
    frozen_config = inputs / "ramulator.yaml"
    gem5_hash = base.copy_stable(gem5, frozen_gem5)
    ramulator_hash = base.copy_stable(ramulator, frozen_ramulator)
    config_hash = base.copy_stable(ramulator_config, frozen_config)
    frozen_gem5.chmod(0o555)
    guests, build_commands = base.build_guests(inputs, ("-DUME_GZZ_MAA_PAGE_CONSUMER",))
    selectors: dict[str, Path | None] = {}
    for arm in ARMS:
        if arm.selector is None:
            selectors[arm.name] = None
            continue
        selector = inputs / f"{arm.name}.selector"
        selector.write_text(arm.selector + "\n")
        selector.chmod(0o444)
        selectors[arm.name] = selector
    manifest = {
        "schema": "dx100.ume_gzz_matched_consumer.campaign.v1",
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "same_simulator_binary": True,
        "matched_consumer": "maa_div_mul",
        "gem5_sha256": gem5_hash,
        "ramulator_sha256": ramulator_hash,
        "ramulator_config_sha256": config_hash,
        "guest_sha256": {name: sha256(path) for name, path in guests.items()},
        "build_commands": build_commands,
        "arms": [arm.name for arm in ARMS],
        "expected_output_hash": base.EXPECTED_OUTPUT_HASH,
    }
    base.atomic_json(root / "manifest.json", manifest)
    environment = dict(
        os.environ,
        LD_LIBRARY_PATH=str(inputs),
        OMP_NUM_THREADS="4",
        OMP_PROC_BIND="false",
    )
    ldd = subprocess.check_output(["ldd", str(frozen_gem5)], env=environment, text=True)
    (inputs / "gem5.ldd.txt").write_text(ldd)
    match = re.search(r"^[ \t]*libramulator\.so => (\S+)", ldd, re.M)
    require(match is not None, "candidate gem5 did not resolve Ramulator")
    require(
        Path(match.group(1)).resolve() == frozen_ramulator.resolve(),
        "candidate gem5 resolved the wrong Ramulator library",
    )
    return {
        "gem5": frozen_gem5,
        "ramulator_config": frozen_config,
        "guests": guests,
        "selectors": selectors,
        "manifest": manifest,
        "environment": environment,
    }


def run_arm(root: Path, prepared: dict[str, Any], arm: base.Arm) -> None:
    checkpoint = root / "checkpoints" / arm.name
    checkpoint.mkdir(parents=True)
    options = base.arm_options(arm, prepared["selectors"][arm.name])
    command = base.checkpoint_command(
        prepared["gem5"], prepared["guests"][arm.guest], checkpoint / "gem5", options
    )
    rc = base.run_logged(command, checkpoint, "checkpoint", prepared["environment"])
    require(rc == 0, f"{arm.name}: checkpoint failed")
    require(
        "because checkpoint"
        in (checkpoint / "checkpoint.log").read_text(errors="replace"),
        f"{arm.name}: checkpoint marker",
    )
    identity = base.tree_identity(checkpoint / "gem5")
    base.atomic_json(checkpoint / "identity.json", identity)

    arm_root = root / "arms" / arm.name
    arm_root.mkdir(parents=True)
    restore = base.common_restore_command(
        prepared["gem5"],
        prepared["ramulator_config"],
        checkpoint / "gem5",
        prepared["guests"][arm.guest],
        options,
        arm_root / "run",
        arm,
    )
    rc = base.run_logged(restore, arm_root, "restore", prepared["environment"])
    require(rc == 0, f"{arm.name}: restore failed")
    require(
        base.tree_identity(checkpoint / "gem5")["sha256"] == identity["sha256"],
        f"{arm.name}: checkpoint mutated",
    )


def classify(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "manifest.json").read_text())
    classified = {arm.name: base.classify_arm(root, arm, manifest) for arm in ARMS}
    require(
        len({item["output_hash"] for item in classified.values()}) == 1,
        "cross-arm output mismatch",
    )
    for arm in ARMS:
        log = (root / "arms" / arm.name / "restore.log").read_text(errors="replace")
        require(
            "UME_GZZ_PAGE_CONSUMER mode=maa_div_mul "
            "physical_tiles_per_core=7 cpu_spd_payload_reads=0" in log,
            f"{arm.name}: matched consumer missing",
        )
        stats = base.base.first_stats_section(
            root / "arms" / arm.name / "run/stats.txt"
        )
        require(
            base.optional_sum(stats, "cpu_spd_out_of_range_rejections") == 0,
            f"{arm.name}: CPU SPD aperture rejection",
        )
    ticks = {name: item["counters"]["simTicks"] for name, item in classified.items()}
    return {
        "schema": "dx100.ume_gzz_matched_consumer.result.v1",
        "terminal": True,
        "decision": "ACCEPT_MATCHED_GZZ_CONSUMER_MATRIX",
        "same_simulator_binary": True,
        "instruction_consumer_matched": True,
        "output_hash": next(iter(classified.values()))["output_hash"],
        "arms": classified,
        "ticks": ticks,
        "speedups": {
            "strict_over_native16": ticks["native16"] / ticks["strict_bounded_hybrid"],
            "strict_over_native4": ticks["native4"] / ticks["strict_bounded_hybrid"],
        },
    }


def run(
    root: Path, gem5: Path, ramulator: Path, ramulator_config: Path
) -> dict[str, Any]:
    prepared = prepare(root, gem5, ramulator, ramulator_config)
    with ThreadPoolExecutor(max_workers=len(ARMS)) as pool:
        futures = [pool.submit(run_arm, root, prepared, arm) for arm in ARMS]
        for future in futures:
            future.result()
    result = classify(root)
    base.atomic_json(root / "result.json", result)
    base.atomic_text(
        root / "gate.complete",
        "COMPLETE_UME_GZZ_MATCHED_CONSUMER\ncorrectness=EXACT_REFERENCE\n",
    )
    write_ledger(root)
    verify_ledger(root)
    return result


def validate(root: Path) -> dict[str, Any]:
    verify_ledger(root)
    sealed = json.loads((root / "result.json").read_text())
    require(classify(root) == sealed, "sealed result changed")
    verify_ledger(root)
    return sealed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "validate", "preflight"))
    parser.add_argument("out", nargs="?", type=Path)
    parser.add_argument("--gem5", type=Path, default=DEFAULT_GEM5)
    parser.add_argument("--ramulator", type=Path, default=DEFAULT_RAMULATOR)
    parser.add_argument(
        "--ramulator-config", type=Path, default=DEFAULT_RAMULATOR_CONFIG
    )
    args = parser.parse_args()
    if args.command == "preflight":
        result: dict[str, Any] = {
            "arms": [arm.name for arm in ARMS],
            "matched_consumer": "maa_div_mul",
        }
    else:
        require(args.out is not None, f"{args.command} requires OUT")
        result = (
            run(
                args.out.resolve(),
                args.gem5.resolve(),
                args.ramulator.resolve(),
                args.ramulator_config.resolve(),
            )
            if args.command == "run"
            else validate(args.out.resolve())
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
