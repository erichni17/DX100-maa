#!/usr/bin/env python3
"""Run a same-binary dense backing write-allocation API micro pair."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.scripts import run_hybrid_equal_work_micro_matrix as base

PREDECESSOR = Path(
    "/data1/nier/worktrees/codex-coordination/sessions/"
    "hybrid-equal-work-micro-20260828-20260828-094827-85a96b10/"
    "evidence/hybrid-equal-work-micro-r4"
)
SELECTOR = PREDECESSOR / "treatment.txt"
BWRAP = Path("/usr/bin/bwrap")
RAMULATOR = PREDECESSOR / "input/libramulator.so"
EXPECTED_PREDECESSOR = {
    "artifacts.sha256": (
        "d6bd4adcf1fdd22cc24884ab9421070125087ef556dfeb1462d6c98056873f82"
    ),
    "result.json": (
        "d44609f28a30e46648dca4febfe7ff0b43d47fe08140dbb356c5597ebe01b870"
    ),
}
ARM = base.ArmSpec(
    "placeholder", "transparent", 4096, 16384, 4096, 64, True, 1, 4, 4
)


class PairError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PairError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_ledger(root: Path, ledger: Path) -> None:
    seen: set[str] = set()
    for number, line in enumerate(ledger.read_text().splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        require(match is not None, f"bad ledger line {number}")
        relative = match.group(2)  # type: ignore[union-attr]
        require(relative not in seen, f"duplicate ledger path {relative}")
        seen.add(relative)
        path = root / relative
        require(path.is_file() and sha256(path) == match.group(1),
                f"artifact changed: {relative}")  # type: ignore[union-attr]


def verify_predecessor() -> None:
    for relative, expected in EXPECTED_PREDECESSOR.items():
        path = PREDECESSOR / relative
        require(path.is_file() and sha256(path) == expected,
                f"predecessor {relative} changed")
    verify_ledger(PREDECESSOR, PREDECESSOR / "artifacts.sha256")


def set_option(command: list[str], prefix: str, value: str) -> None:
    matches = [i for i, token in enumerate(command) if token.startswith(prefix)]
    require(len(matches) == 1, f"expected one option {prefix}")
    command[matches[0]] = prefix + value


def command_for(gem5: Path, out: Path, dense: bool) -> list[str]:
    command = json.loads(
        (PREDECESSOR / "arms/hybrid64/command.json").read_text()
    )
    require(isinstance(command, list), "predecessor command is not a list")
    command[0] = str(gem5)
    scripts = [
        index for index, token in enumerate(command)
        if token.endswith("/configs/deprecated/example/se.py")
    ]
    require(len(scripts) == 1, "expected one se.py command token")
    command[scripts[0]] = str(ROOT / "configs/deprecated/example/se.py")
    set_option(command, "--outdir=", str(out))
    if not any(
        token.startswith("--maa_virtual_index_issue_lines_per_cycle=")
        for token in command
    ):
        command.append("--maa_virtual_index_issue_lines_per_cycle=1")
    if dense:
        command.append("--maa_virtual_dense_write_allocate")
    return command


def wrapped(root: Path, treatment: Path, command: list[str]) -> list[str]:
    return [
        str(BWRAP), "--die-with-parent", "--ro-bind", "/", "/",
        "--bind", str(root), str(root),
        "--ro-bind", str(treatment), str(SELECTOR), *command,
    ]


def write_line_accesses(stats: dict[str, float]) -> int:
    return int(sum(
        value for name, value in stats.items()
        if re.fullmatch(
            r"system\.maa_retirement_caches[0-9]+\."
            r"WriteLineReq_T\.accesses::maa", name
        )
    ))


def ramulator_reads(log: str) -> int:
    values: dict[int, int] = {}
    for channel, count in re.findall(
        r"^\s*SYS([0-9]+)_total_num_read_requests_T:\s*([0-9]+)",
        log, re.MULTILINE,
    ):
        values[int(channel)] = int(count)
    require(values, "missing Ramulator read totals")
    return sum(values.values())


def classify(root: Path, name: str, dense: bool) -> dict[str, object]:
    spec = base.ArmSpec(
        name, ARM.mode, ARM.page_elements, ARM.logical_elements,
        ARM.physical_elements, ARM.feeder_lines, ARM.strict,
        ARM.expected_indirect_ops, ARM.expected_stream_writes,
        ARM.expected_scalar_ops,
    )
    classified = base.classify_arm(root, spec)
    arm = root / "arms" / name
    config = base.parse_config(arm / "run/config.ini")
    require(
        config.get("virtual_dense_write_allocate")
        == ("true" if dense else "false"),
        f"{name}: dense option did not resolve",
    )
    stats = base.first_stats_section(arm / "run/stats.txt")
    dense_writes = base.summed_stat(
        stats, "IND_VirtDenseInitializationWrites"
    )
    require(dense_writes == (2048 if dense else 0),
            f"{name}: dense initialization count {dense_writes}")
    trace = (arm / "run/hybrid_trace.log").read_text(errors="strict")
    trace_dense = len(re.findall(
        r"event=backing_write_issue .* dense_initialize=1 ", trace
    ))
    require(trace_dense == dense_writes, f"{name}: dense trace count")
    diagnostics = {
        "dense_initialization_writes": dense_writes,
        "write_line_accesses": write_line_accesses(stats),
        "l3_maa_hits": base.exact_stat(stats, "system.l3.demandHits_T::maa"),
        "l3_maa_misses": base.exact_stat(
            stats, "system.l3.demandMisses_T::maa"
        ),
        "l3_maa_miss_latency": base.exact_stat(
            stats, "system.l3.demandMissLatency_T::maa"
        ),
        "maa_cache_reads": base.exact_stat(
            stats, "system.maa.port_cache_RD_packets"
        ),
        "maa_cache_writes": base.exact_stat(
            stats, "system.maa.port_cache_WR_packets"
        ),
        "ramulator_reads": ramulator_reads(
            (arm / "restore.log").read_text(errors="strict")
        ),
    }
    return {**classified, "diagnostics": diagnostics}


def artifact_paths(root: Path) -> list[Path]:
    return [
        path for path in sorted(root.rglob("*"))
        if path.is_file() and path != root / "artifacts.sha256"
    ]


def write_ledger(root: Path) -> None:
    (root / "artifacts.sha256").write_text("\n".join(
        f"{sha256(path)}  {path.relative_to(root)}"
        for path in artifact_paths(root)
    ) + "\n")


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
    require(gem5.is_file() and sha256(gem5) == args.gem5_sha256,
            "gem5 identity mismatch")
    require(BWRAP.is_file() and RAMULATOR.is_file(), "missing runtime input")
    verify_predecessor()
    root.mkdir(parents=True)
    (root / "arms").mkdir()
    environment = dict(os.environ)
    environment["LD_LIBRARY_PATH"] = (
        str(RAMULATOR.parent) + ":" + environment.get("LD_LIBRARY_PATH", "")
    )
    environment["OMP_NUM_THREADS"] = "4"
    environment["OMP_PROC_BIND"] = "false"
    for name, dense in (("control", False), ("dense", True)):
        arm = root / "arms" / name
        arm.mkdir()
        treatment = arm / "treatment.txt"
        treatment.write_text(ARM.treatment)
        command = command_for(gem5, arm / "run", dense)
        wrapper = wrapped(root, treatment, command)
        (arm / "command.json").write_text(json.dumps(command, indent=2) + "\n")
        (arm / "wrapped_command.json").write_text(
            json.dumps(wrapper, indent=2) + "\n"
        )
        (arm / "arm.json").write_text(json.dumps({
            "name": name,
            "dense": dense,
            "gem5_sha256": args.gem5_sha256,
        }, indent=2, sort_keys=True) + "\n")
        rc = base.run_command(
            wrapper, arm / "restore.log", environment,
            arm / "process.json",
        )
        (arm / "restore.exit").write_text(f"{rc}\n")
        require(rc == 0, f"{name}: restore exited {rc}")

    verify_predecessor()
    arms = {
        "control": classify(root, "control", False),
        "dense": classify(root, "dense", True),
    }
    control_ticks = int(arms["control"]["counters"]["simTicks"])
    dense_ticks = int(arms["dense"]["counters"]["simTicks"])
    require(
        arms["control"]["output_hash"] == arms["dense"]["output_hash"],
        "output hash changed",
    )
    result = {
        "schema": "dx100.hybrid_dense_write_allocate_pair.v1",
        "terminal": True,
        "decision": "VALID_DENSE_WRITE_ALLOCATE_PAIR",
        "gem5_sha256": args.gem5_sha256,
        "source_commit": base.source_commit(),
        "predecessor": str(PREDECESSOR),
        "same_binary": True,
        "same_checkpoint": True,
        "same_semantic_work": True,
        "arms": arms,
        "dense_latency_change_pct": 100 * (dense_ticks / control_ticks - 1),
        "control_over_dense": control_ticks / dense_ticks,
    }
    (root / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    (root / "gate.complete").write_text(
        "VALID_DENSE_WRITE_ALLOCATE_PAIR\ncorrectness=EXACT_MATCH\n"
    )
    write_ledger(root)
    verify_ledger(root, root / "artifacts.sha256")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PairError, base.MatrixError, OSError) as error:
        print(f"FAIL-CLOSED: {error}", file=os.sys.stderr)
        raise SystemExit(1)
