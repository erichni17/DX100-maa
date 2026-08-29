#!/usr/bin/env python3
"""Add same-binary native16/native4 controls to the dense hybrid pair."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.scripts import run_hybrid_dense_write_allocate_pair as pair
from experiments.scripts import run_hybrid_equal_work_micro_matrix as base

DENSE_ROOT = Path(
    "/data1/nier/dx100-runs/"
    "2026-08-29-hybrid-dense-write-allocate-pair-r4"
)
EXPECTED_DENSE = {
    "result.json": (
        "49f86c66498aa245936a03337f2ecdea0eec0841547d2c324aa816cea1e1ed7c"
    ),
    "artifacts.sha256": (
        "f700bc8be8636bccea22b08ea85c732c67f8aca83d09f325903b6e99b4f0e648"
    ),
}
NATIVE_ARMS = (
    base.ArmSpec(
        "native16", "native_direct", 16384, 16384, 16384, 64,
        False, 1, 1, 1,
    ),
    base.ArmSpec(
        "native4", "native_direct", 4096, 16384, 4096, 64,
        False, 4, 4, 4,
    ),
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise pair.PairError(message)


def verify_dense() -> dict[str, object]:
    for relative, expected in EXPECTED_DENSE.items():
        path = DENSE_ROOT / relative
        require(path.is_file() and pair.sha256(path) == expected,
                f"dense predecessor {relative} changed")
    pair.verify_ledger(DENSE_ROOT, DENSE_ROOT / "artifacts.sha256")
    result = json.loads((DENSE_ROOT / "result.json").read_text())
    require(
        result.get("terminal") is True
        and result.get("decision") == "VALID_DENSE_WRITE_ALLOCATE_PAIR"
        and result.get("same_binary") is True
        and result.get("same_checkpoint") is True,
        "dense predecessor is not accepted",
    )
    return result


def command_for(gem5: Path, arm: base.ArmSpec, out: Path) -> list[str]:
    command = json.loads(
        (pair.PREDECESSOR / "arms" / arm.name / "command.json").read_text()
    )
    command[0] = str(gem5)
    scripts = [
        i for i, token in enumerate(command)
        if token.endswith("/configs/deprecated/example/se.py")
    ]
    require(len(scripts) == 1, "expected one se.py command token")
    command[scripts[0]] = str(ROOT / "configs/deprecated/example/se.py")
    pair.set_option(command, "--outdir=", str(out))
    pair.set_option(
        command, "--maa_virtual_index_buffer_lines=", "64"
    )
    if not any(
        token.startswith("--maa_virtual_index_issue_lines_per_cycle=")
        for token in command
    ):
        command.append("--maa_virtual_index_issue_lines_per_cycle=1")
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--gem5", type=Path, required=True)
    parser.add_argument("--gem5-sha256", required=True)
    args = parser.parse_args()
    root = args.out.resolve()
    gem5 = args.gem5.resolve()
    require(not root.exists(), f"output exists: {root}")
    require(not base.source_status(), "source worktree is dirty")
    require(gem5.is_file() and pair.sha256(gem5) == args.gem5_sha256,
            "gem5 identity mismatch")
    pair.verify_predecessor()
    dense = verify_dense()
    root.mkdir(parents=True)
    (root / "arms").mkdir()
    environment = dict(os.environ)
    environment["LD_LIBRARY_PATH"] = (
        str(pair.RAMULATOR.parent) + ":"
        + environment.get("LD_LIBRARY_PATH", "")
    )
    environment["OMP_NUM_THREADS"] = "4"
    environment["OMP_PROC_BIND"] = "false"
    for arm in NATIVE_ARMS:
        arm_root = root / "arms" / arm.name
        arm_root.mkdir()
        treatment = arm_root / "treatment.txt"
        treatment.write_text(arm.treatment)
        command = command_for(gem5, arm, arm_root / "run")
        wrapper = pair.wrapped(root, treatment, command)
        (arm_root / "command.json").write_text(
            json.dumps(command, indent=2) + "\n"
        )
        (arm_root / "wrapped_command.json").write_text(
            json.dumps(wrapper, indent=2) + "\n"
        )
        (arm_root / "arm.json").write_text(
            json.dumps({"name": arm.name, "gem5_sha256": args.gem5_sha256},
                       indent=2, sort_keys=True) + "\n"
        )
        rc = base.run_command(
            wrapper, arm_root / "restore.log", environment,
            arm_root / "process.json",
        )
        (arm_root / "restore.exit").write_text(f"{rc}\n")
        require(rc == 0, f"{arm.name}: restore exited {rc}")

    pair.verify_predecessor()
    verify_dense()
    native = {
        arm.name: base.classify_arm(root, arm) for arm in NATIVE_ARMS
    }
    for name in native:
        stats = base.first_stats_section(root / "arms" / name / "run/stats.txt")
        require(
            base.summed_stat(stats, "IND_VirtDenseInitializationWrites") == 0,
            f"{name}: dense mechanism unexpectedly active",
        )
    dense_ticks = int(dense["arms"]["dense"]["counters"]["simTicks"])
    native16_ticks = int(native["native16"]["counters"]["simTicks"])
    native4_ticks = int(native["native4"]["counters"]["simTicks"])
    result = {
        "schema": "dx100.hybrid_dense_native_controls.v1",
        "terminal": True,
        "decision": "VALID_DENSE_NATIVE_CONTROLS",
        "gem5_sha256": args.gem5_sha256,
        "source_commit": base.source_commit(),
        "dense_predecessor": str(DENSE_ROOT),
        "same_binary": True,
        "same_checkpoint": True,
        "native": native,
        "dense_simTicks": dense_ticks,
        "dense_vs_native16_latency_change_pct": (
            100 * (dense_ticks / native16_ticks - 1)
        ),
        "dense_vs_native4_latency_change_pct": (
            100 * (dense_ticks / native4_ticks - 1)
        ),
        "native16_over_dense": native16_ticks / dense_ticks,
        "native4_over_dense": native4_ticks / dense_ticks,
    }
    (root / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    (root / "gate.complete").write_text(
        "VALID_DENSE_NATIVE_CONTROLS\ncorrectness=EXACT_MATCH\n"
    )
    pair.write_ledger(root)
    pair.verify_ledger(root, root / "artifacts.sha256")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (pair.PairError, base.MatrixError, OSError) as error:
        print(f"FAIL-CLOSED: {error}", file=os.sys.stderr)
        raise SystemExit(1)
