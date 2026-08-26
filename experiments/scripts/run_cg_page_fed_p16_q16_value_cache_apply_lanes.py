#!/usr/bin/env python3
"""Fail-closed bounded cache-on page-fed p16/q16 apply-lane A/B.

One ordinary guest and one deferred checkpoint feed lane 1 and lane 4.  The
only restore-command/configuration delta is ``maa_soa_jit_apply_lanes``.
CG_NA=256 is the required screen; CG_NA=1024 is accepted only with a terminal
screen root that recorded lane 4 as exact and faster.  This is not a native or
full-CG experiment, and direct4 evidence is read only for reconciliation.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE_PATH = ROOT / "experiments/scripts/run_cg_direct4_product_page_fed_q16.py"
SPEC = importlib.util.spec_from_file_location(
    "cg_bounded_lane_gate", BASE_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load bounded CG gate: {BASE_PATH}")
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)

TREATMENT = "page_fed_product_soa_jit"
SCREEN_NA = 256
CONFIRM_NA = 1024
LANES = (1, 4)
FIXED_VALUE_OWNER_LINES = 128
ACTIVE_VALUE_OWNER_LINES = 32
VALUE_OWNER_LINE_BYTES = 64
INDIRECT_UNITS_PER_MAA = 4
FIXED_APPLY_LANES_PER_UNIT = 4
FIXED_APPLY_LANE_OWNER_BYTES = 32
FIXED_APPLY_LANE_POOL_BYTES_PER_UNIT = 144

# This predecessor is a mechanism reconciliation only.  Its p16=false result
# is expressly excluded from page-fed p-stage timing attribution.
DIRECT4_ROOT = Path(
    "/data1/nier/worktrees/codex-coordination/sessions/"
    "hybrid-q16-retained-apply-lanes-20260826-20260826-131017-d5eb8ebb/"
    "evidence/direct4-q16-apply-lanes-na256-r1"
)

CONSERVE_STATS = tuple(
    dict.fromkeys(
        base.APPLY_LANE_CONSERVED_STATS
        + (
            "IND_SoaJitPredicateRejected",
            "IND_SoaJitValueReadIssues",
            "IND_SoaJitValueReadResponses",
            "IND_SoaJitValueFills",
            "IND_SoaJitValueCachedResponses",
            "IND_SoaJitValueHits",
            "IND_SoaJitValueMergedWaiters",
            "IND_SoaJitValueEvictions",
            "IND_SoaJitValueStalls",
            "IND_SoaJitValueCacheHighWater",
            "IND_SoaJitLookaheadStalls",
            "IND_SoaJitContextStalls",
            "system.maa.port_cache_RD_packets",
            "system.maa.port_cache_WR_packets",
        )
    )
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def verify_raw_root(root: Path, gate_header: str) -> str:
    """Rehash a terminal raw root and bind its ledger to the expected gate."""
    root = root.resolve()
    ledger, gate = root / "raw_root.sha256", root / "gate.complete"
    require(
        ledger.is_file() and gate.is_file(), f"incomplete raw root: {root}"
    )
    seen: set[Path] = set()
    for number, line in enumerate(
        ledger.read_text(encoding="utf-8").splitlines(), 1
    ):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        require(match is not None, f"malformed raw ledger line {number}")
        relative = Path(match.group(2))  # type: ignore[union-attr]
        require(
            not relative.is_absolute()
            and ".." not in relative.parts
            and relative not in seen,
            "unsafe or duplicate raw ledger path",
        )
        seen.add(relative)
        artifact = root / relative
        require(artifact.is_file() and base.base.sha256_file(artifact) == match.group(1), f"raw ledger mismatch for {relative}")  # type: ignore[union-attr]
    require(
        Path("result.json") in seen, "raw ledger does not cover result.json"
    )
    digest = base.base.sha256_file(ledger)
    lines = gate.read_text(encoding="utf-8").splitlines()
    require(
        lines.count(gate_header) == 1
        and lines.count("correctness=EXACT_MATCH") == 1
        and lines.count(f"raw_root_sha256={digest}") == 1,
        "raw gate does not bind ledger",
    )
    return digest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--cg-na", type=int, default=SCREEN_NA)
    parser.add_argument("--confirm-from", type=Path)
    args = parser.parse_args(argv)
    if args.cg_na not in (SCREEN_NA, CONFIRM_NA):
        parser.error(
            "only CG_NA=256 screen or conditionally authorized 1024 is allowed"
        )
    if args.cg_na == SCREEN_NA and args.confirm_from is not None:
        parser.error(
            "CG_NA=256 is the first screen and takes no --confirm-from"
        )
    if args.cg_na == CONFIRM_NA and args.confirm_from is None:
        parser.error(
            "CG_NA=1024 requires a terminal exact-faster --confirm-from root"
        )
    return args


def require_confirmation(root: Path) -> str:
    """Authorize NA=1024 solely from this treatment's terminal NA=256 root."""
    root = root.resolve()
    ledger_sha = verify_raw_root(
        root, "COMPLETE_CG_PAGE_FED_P16_Q16_APPLY_LANES"
    )
    result = json.loads((root / "result.json").read_text(encoding="utf-8"))
    require(
        result.get("schema") == "dx100.cg.page_fed_p16_q16_apply_lanes.v1"
        and result.get("terminal") is True
        and result.get("cg_na") == SCREEN_NA
        and result.get("same_guest_treatment") == TREATMENT
        and result.get("sole_knob_delta") == "maa_soa_jit_apply_lanes"
        and result.get("decision") == "ACCEPT_EXACT_FASTER_ARM"
        and "lane_4"
        in result.get("performance", {}).get("exact_faster_arms", []),
        "CG_NA=1024 is not authorized by an exact-faster page-fed lane-4 screen",
    )
    return ledger_sha


def validate_direct4_reconciliation() -> dict[str, object]:
    """Pin existing direct4 evidence without importing its p-stage timing."""
    ledger_sha = verify_raw_root(
        DIRECT4_ROOT, "COMPLETE_CG_DIRECT4_PRODUCT_PAGE_FED_Q16"
    )
    result = json.loads(
        (DIRECT4_ROOT / "result.json").read_text(encoding="utf-8")
    )
    hardware = result.get("hardware_accounting", {})
    require(
        result.get("terminal") is True
        and result.get("cg_na") == SCREEN_NA
        and result.get("same_guest_treatment")
        == "direct4_product_page_fed_q16"
        and result.get("sole_knob_delta") == "maa_soa_jit_apply_lanes"
        and "lane_4"
        in result.get("performance", {}).get("exact_faster_arms", [])
        and hardware.get("new_payload_bytes") == 0
        and hardware.get("new_control_bytes") == 0
        and hardware.get("new_ports") == 0
        and hardware.get("incremental_apply_lane_pool_bytes_across_arms") == 0,
        "direct4 lane evidence is not a valid fixed-pool reconciliation authority",
    )
    return {
        "root": str(DIRECT4_ROOT),
        "raw_root_sha256": ledger_sha,
        "p16_reorder_preserved": False,
        "use": "fixed-pool/mechanism reconciliation only; no p-stage timing attribution",
    }


def compile_command(guest: Path, cg_na: int) -> list[str]:
    return [
        os.environ.get("CXX", "g++"),
        f"-I{ROOT / 'benchmarks/API'}",
        f"-I{ROOT / 'include'}",
        f"-I{ROOT / 'util/m5/src'}",
        "-std=c++11",
        "-O3",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wno-ignored-qualifiers",
        "-Wno-unused-parameter",
        "-Wno-unused-function",
        "-fopenmp",
        "-DGEM5",
        "-DMAA",
        "-DMAA_VIRTUAL_GATHER",
        "-DMAA_GENERAL_VIRTUAL_CONSUMER",
        "-DMAA_CONSUMER_TILE_SIZE=4096",
        "-DCG_LOGICAL16_RMW",
        "-DCG_LOGICAL_PAGE_RMW",
        "-DCG_PHYSICAL_PAGE_PRODUCT_ONLY",
        "-DCG_PAGE_FED_SOA_ONLY",
        "-DCG_FP_ENABLE",
        "-DCG_DETERMINISTIC_REDUCTIONS",
        "-DCG_REDUCTION_EVIDENCE",
        f"-DCG_NA={cg_na}",
        "-DNUM_CORES=4",
        "-DNUM_TILES_PER_CORE=8",
        "-DTILE_SIZE=16384",
        "-DMAA_MEM_SIZE=0x80000000",
        str(ROOT / "util/m5/src/abi/x86/m5op.S"),
        str(base.base.SOURCE),
        "-o",
        str(guest),
    ]


def checkpoint_command(
    guest: Path, selector: Path, checkpoint: Path
) -> list[str]:
    return [
        str(base.base.GEM5),
        "--listener-mode=off",
        f"--outdir={checkpoint}",
        str(base.base.CONFIG),
        "--cpu-type",
        "AtomicSimpleCPU",
        "-n",
        "4",
        "--mem-size",
        "2GB",
        "--max-checkpoints=1",
        "--cmd",
        str(guest),
        "--options",
        f"MAA_DEFERRED {selector}",
    ]


def restore_command(
    guest: Path, selector: Path, checkpoint: Path, arm: Path, lanes: int
) -> list[str]:
    command = base.restore_args(
        guest, selector, checkpoint, arm, value_cache=True, apply_lanes=lanes
    )
    require(
        command.count("--maa_soa_jit_value_cache_enable") == 1,
        "cache-on knob must occur once",
    )
    require(
        command.count("--maa_soa_jit_active_value_owners=32") == 1,
        "32 active owners must occur once",
    )
    require(
        command.count(f"--maa_soa_jit_apply_lanes={lanes}") == 1,
        "lane knob must occur once",
    )
    require(
        sum(item.startswith("--maa_soa_jit_apply_lanes=") for item in command)
        == 1,
        "only apply lanes may vary",
    )
    return command


def parse_arm(arm: Path, cg_na: int, lanes: int) -> dict[str, object]:
    parsed = base.parse_arm(
        arm, cg_na, TREATMENT, value_cache=True, apply_lanes=lanes
    )
    stats = parsed["stats"]
    instructions = stats["IND_SoaJitInstructions"]
    require(
        stats["IND_SoaJitActiveApplyLanes"] == instructions * lanes
        and stats["IND_SoaJitApplyLaneHighWater"] == instructions * lanes,
        f"lane {lanes} did not reach exact per-instruction active/high-water closure",
    )
    return parsed


def normalized_pair(arm: Path, command: list[str]) -> tuple[str, list[str]]:
    return (
        base.normalized_apply_lane_config(arm / "config.ini"),
        base.normalized_apply_lane_command(command),
    )


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out = args.out.resolve()
    require(
        out != ROOT and ROOT not in out.parents,
        "output must be outside source worktree",
    )
    require(
        not out.exists() or not any(out.iterdir()),
        f"refusing nonempty output: {out}",
    )
    confirmation_sha = (
        require_confirmation(args.confirm_from) if args.confirm_from else None
    )
    direct4 = validate_direct4_reconciliation()
    base.base.exact_hash(base.base.GEM5, base.base.GEM5_SHA256, "frozen gem5")
    base.base.exact_hash(
        base.base.RAMULATOR, base.base.RAMULATOR_SHA256, "frozen Ramulator"
    )
    before_status, before_commit = (
        base.base.source_status(),
        base.base.source_commit(),
    )
    require(
        len(before_status.splitlines()) == 1,
        "refusing evidence from dirty source worktree",
    )

    input_dir, checkpoint = out / "input", out / "checkpoint"
    input_dir.mkdir(parents=True)
    checkpoint.mkdir()
    guest, selector = (
        out / "cg_page_fed_p16_q16_lane_guest",
        input_dir / "treatment.selector",
    )
    selector.write_text(f"token_stream_ld {TREATMENT}\n", encoding="utf-8")
    selector.chmod(0o444)
    compile_args = compile_command(guest, args.cg_na)
    subprocess.run(compile_args, cwd=ROOT, check=True)
    immutable = (
        base.base.GEM5,
        base.base.RAMULATOR,
        guest,
        BASE_PATH,
        Path(__file__).resolve(),
        *base.base.GUEST_COMPILE_INPUTS,
        *base.base.RUNNER_CONFIG_INPUTS[1:],
    )
    artifacts_before = base.base.artifact_ledger(immutable)
    (input_dir / "artifact_sha256.before").write_text(
        artifacts_before, encoding="utf-8"
    )
    (input_dir / "source_status.before").write_text(
        before_status, encoding="utf-8"
    )
    (input_dir / "source_commit.before").write_text(
        before_commit + "\n", encoding="utf-8"
    )
    (input_dir / "compile_command.json").write_text(
        json.dumps(compile_args, indent=2) + "\n", encoding="utf-8"
    )
    cp_args = checkpoint_command(guest, selector, checkpoint)
    (input_dir / "checkpoint_command.json").write_text(
        json.dumps(cp_args, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {
        "schema": "dx100.cg.page_fed_p16_q16_apply_lanes.v1",
        "terminal": False,
        "candidate_only": True,
        "native_runs": 0,
        "full_cg_runs": 0,
        "timeout": "none",
        "cg_na": args.cg_na,
        "same_guest_treatment": TREATMENT,
        "sole_knob_delta": "maa_soa_jit_apply_lanes",
        "p16_reorder_preserved": True,
        "q16_reorder_preserved": True,
        "direct4_reconciliation": direct4,
        "confirmation_source_raw_root_sha256": confirmation_sha,
        "geometry": {
            "cores": 4,
            "tiles_per_core": 8,
            "physical_spd_payload_bytes": 524288,
            "external_coherent_backing_bytes": 524288,
            "virtual_p_backing_bytes": 262144,
            "product_backing_bytes": 262144,
            "coherent_q_index_backing_bytes": 0,
            "host_payload_access": 0,
        },
        "fixed_pool": {
            "fixed_value_owner_lines_per_unit": FIXED_VALUE_OWNER_LINES,
            "active_value_owner_lines_per_unit": ACTIVE_VALUE_OWNER_LINES,
            "fixed_apply_lanes_per_indirect_unit": FIXED_APPLY_LANES_PER_UNIT,
            "fixed_apply_lane_owner_state_bytes": FIXED_APPLY_LANE_OWNER_BYTES,
            "fixed_apply_lane_pool_state_bytes_per_unit": FIXED_APPLY_LANE_POOL_BYTES_PER_UNIT,
            "new_payload_bytes": 0,
            "new_control_bytes": 0,
            "new_ports": 0,
            "incremental_apply_lane_pool_bytes_across_arms": 0,
        },
        "commands": {"compile": compile_args, "checkpoint": cp_args},
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    library_path = str(base.base.RAMULATOR.parent) + (
        (":" + os.environ["LD_LIBRARY_PATH"])
        if os.environ.get("LD_LIBRARY_PATH")
        else ""
    )
    environment = dict(
        os.environ,
        LD_LIBRARY_PATH=library_path,
        OMP_NUM_THREADS="4",
        OMP_PROC_BIND="false",
    )
    ldd = subprocess.check_output(
        ["ldd", str(base.base.GEM5)], env=environment, text=True
    )
    match = re.search(r"^[ \t]*libramulator\.so => (\S+)", ldd, re.M)
    require(
        match is not None
        and Path(match.group(1)).resolve() == base.base.RAMULATOR.resolve(),
        "gem5 did not resolve frozen Ramulator",
    )
    base.base.run_logged(cp_args, out / "checkpoint.log", environment)
    lines = (out / "checkpoint.log").read_text(errors="replace").splitlines()
    base.base.exactly_one(
        lines,
        r"^Exiting @ tick [0-9]+ because checkpoint$",
        "checkpoint terminal",
    )
    require(
        not any(
            line.startswith(
                (
                    "CG_REDUCTION_EVIDENCE ",
                    "CG_FINGERPRINT ",
                    "CG_LOGICAL16_RMW_TERMINAL ",
                )
            )
            for line in lines
        ),
        "checkpoint crossed deferred treatment boundary",
    )
    checkpoint_before = base.base.tree_ledger(checkpoint)
    (input_dir / "checkpoint_files.before").write_text(
        checkpoint_before, encoding="utf-8"
    )
    parsed: dict[str, dict[str, object]] = {}
    commands: dict[str, list[str]] = {}
    for lanes in LANES:
        name, arm = f"lane_{lanes}", out / f"lane_{lanes}"
        arm.mkdir()
        (arm / "selector.txt").write_text(
            selector.read_text(encoding="utf-8"), encoding="utf-8"
        )
        command = restore_command(guest, selector, checkpoint, arm, lanes)
        commands[name] = command
        (arm / "restore_command.json").write_text(
            json.dumps(command, indent=2) + "\n", encoding="utf-8"
        )
        base.base.run_logged(command, arm / "restore.log", environment)
        parsed[name] = parse_arm(arm, args.cg_na, lanes)
    control = parsed["lane_1"]
    require(
        all(
            arm["fingerprint_line"] == control["fingerprint_line"]
            for arm in parsed.values()
        ),
        "raw/quantized fingerprints differ",
    )
    require(
        all(
            arm["reduction_evidence"] == control["reduction_evidence"]
            and len(arm["reduction_evidence"]) == 11
            for arm in parsed.values()
        ),
        "deterministic reductions differ",
    )
    for name, arm in parsed.items():
        require(
            arm["terminal"] == control["terminal"],
            f"{name} changed p/product/q/page-fed terminal ledger",
        )
        require(
            (out / name / "selector.txt").read_text(encoding="utf-8")
            == f"token_stream_ld {TREATMENT}\n",
            f"{name} changed guest treatment",
        )
        for stat in CONSERVE_STATS:
            require(
                arm["stats"][stat] == control["stats"][stat],
                f"{name} changed conserved ledger {stat}",
            )
        require(
            normalized_pair(out / name, commands[name])
            == normalized_pair(out / "lane_1", commands["lane_1"]),
            f"{name} has non-lane command/config delta",
        )
    checkpoint_after = base.base.tree_ledger(checkpoint)
    (input_dir / "checkpoint_files.after").write_text(
        checkpoint_after, encoding="utf-8"
    )
    require(checkpoint_after == checkpoint_before, "shared checkpoint changed")
    artifacts_after = base.base.artifact_ledger(immutable)
    (input_dir / "artifact_sha256.after").write_text(
        artifacts_after, encoding="utf-8"
    )
    after_status, after_commit = (
        base.base.source_status(),
        base.base.source_commit(),
    )
    (input_dir / "source_status.after").write_text(
        after_status, encoding="utf-8"
    )
    (input_dir / "source_commit.after").write_text(
        after_commit + "\n", encoding="utf-8"
    )
    require(
        artifacts_after == artifacts_before
        and after_status == before_status
        and after_commit == before_commit,
        "source or artifact identity changed",
    )
    lane1, lane4 = (
        control["stats"]["simTicks"],
        parsed["lane_4"]["stats"]["simTicks"],
    )
    faster = lane4 < lane1
    decision = (
        "ACCEPT_EXACT_FASTER_ARM" if faster else "REJECT_NO_EXACT_FASTER_ARM"
    )
    result = {
        "schema": "dx100.cg.page_fed_p16_q16_apply_lanes.v1",
        "terminal": True,
        "decision": decision,
        "candidate_only": True,
        "native_runs": 0,
        "full_cg_runs": 0,
        "timeout": "none",
        "cg_na": args.cg_na,
        "same_guest_treatment": TREATMENT,
        "sole_knob_delta": "maa_soa_jit_apply_lanes",
        "p16_reorder_preserved": True,
        "q16_reorder_preserved": True,
        "selected_value_cache_enable": True,
        "source_commit": before_commit,
        "gem5_sha256": base.base.GEM5_SHA256,
        "ramulator_sha256": base.base.RAMULATOR_SHA256,
        "guest_sha256": base.base.sha256_file(guest),
        "checkpoint_ledger_sha256": hashlib.sha256(
            checkpoint_before.encode()
        ).hexdigest(),
        "direct4_reconciliation": direct4,
        "confirmation": {
            "root": str(args.confirm_from.resolve()),
            "raw_root_sha256": confirmation_sha,
        }
        if args.confirm_from
        else None,
        "hardware_accounting": manifest["fixed_pool"]
        | {
            "physical_spd_payload_bytes": 524288,
            "external_coherent_backing_bytes": 524288,
            "virtual_p_backing_bytes": 262144,
            "product_backing_bytes": 262144,
            "coherent_q_index_backing_bytes": 0,
            "host_payload_access": 0,
            "active_apply_lanes_by_arm": {"lane_1": 1, "lane_4": 4},
        },
        "performance": {
            "metric": "simTicks",
            "baseline_arm": "lane_1",
            "arms": {
                name: arm["stats"]["simTicks"] for name, arm in parsed.items()
            },
            "lane_1_over_lane_4_speedup": lane1 / lane4,
            "exact_faster_arms": ["lane_4"] if faster else [],
        },
        "mechanism": {
            name: {
                "cycles_indrmw": arm["stats"]["system.maa.cycles_INDRMW"],
                "cycles_request": arm["stats"][
                    "system.maa.I0_IND_CyclesRequest"
                ],
                "apply_high_water_sum": arm["stats"][
                    "IND_SoaJitApplyLaneHighWater"
                ],
                "instructions": arm["stats"]["IND_SoaJitInstructions"],
            }
            for name, arm in parsed.items()
        },
        "arms": parsed,
    }
    (out / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    targets = [
        path
        for path in sorted(out.rglob("*"))
        if path.is_file()
        and path.name not in {"raw_root.sha256", "gate.complete"}
    ]
    (out / "raw_root.sha256").write_text(
        "".join(
            f"{base.base.sha256_file(path)}  {path.relative_to(out)}\n"
            for path in targets
        ),
        encoding="utf-8",
    )
    ledger_sha = base.base.sha256_file(out / "raw_root.sha256")
    (out / "gate.complete").write_text(
        "COMPLETE_CG_PAGE_FED_P16_Q16_APPLY_LANES\ncorrectness=EXACT_MATCH\n"
        + f"decision={decision}\nraw_root_sha256={ledger_sha}\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "terminal": True,
                "cg_na": args.cg_na,
                "decision": decision,
                "raw_root_sha256": ledger_sha,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
