#!/usr/bin/env python3
"""Sweep fixed-16-slot line-combiner controls from an accepted CG root.

Every arm restores the accepted NA=1024 guest/checkpoint with the frozen
gem5 binary.  It cannot alter payload/control capacities, SPD ports, logical
Row/Offset capacity, response storage, or use a native path.  Output roots
are deliberately separate, so independent restores can run concurrently.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
from dataclasses import (
    asdict,
    dataclass,
)
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from experiments.scripts.strict_two_phase import (
    run_cg_fused_p16_q16_strict as gate,
)
from experiments.scripts.strict_two_phase.run_cg_strict_line_combined import (
    verify_matched_root,
)

SLOTS = 16
RESPONSE_SLOTS = 8
EXTERNAL_PORTS = 4
BASELINE = (4, 4, 0, 32)
STRICT_ZERO_STATS = (
    "IND_NumOTEpochDrain",
    "IND_SoaJitEpochDrains",
    "IND_BoundedGlobalMergeFallbacks",
)


@dataclass(frozen=True)
class Arm:
    name: str
    ways: int
    banks: int
    victim_policy: int
    write_credits: int


# The three credit values are at or below the accepted 32-credit reservation;
# therefore they only change a bound, not allocated credit-tracking storage.
ARMS = (
    Arm("baseline_w4_b4_rr_c32", 4, 4, 0, 32),
    Arm("direct_w1_b1_rr_c32", 1, 1, 0, 32),
    Arm("set_w2_b2_fewest_c32", 2, 2, 1, 32),
    Arm("set_w4_b4_fewest_c32", 4, 4, 1, 32),
    Arm("set_w4_b4_most_c32", 4, 4, 2, 32),
    Arm("set_w8_b2_fewest_c32", 8, 2, 1, 32),
    Arm("set_w16_b1_most_c32", 16, 1, 2, 32),
    Arm("full_w0_b0_fewest_c32", 0, 0, 1, 32),
    Arm("credit_w4_b4_rr_c16", 4, 4, 0, 16),
    Arm("credit_w4_b4_rr_c8", 4, 4, 0, 8),
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def config_values(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in path.read_text(errors="replace").splitlines()
        if "=" in line
    )


def validate_arm_definition(arm: Arm) -> None:
    require(arm.ways in (0, 1, 2, 4, 8, 16), f"bad ways: {arm}")
    require(arm.ways == 0 or SLOTS % arm.ways == 0, f"bad geometry: {arm}")
    sets = 1 if arm.ways == 0 else SLOTS // arm.ways
    require(
        (arm.ways != 0 or arm.banks == 0) and 0 <= arm.banks <= sets,
        f"bad bank mapping: {arm}",
    )
    require(arm.victim_policy in (0, 1, 2), f"bad victim policy: {arm}")
    require(
        0 < arm.write_credits <= BASELINE[3], f"credit storage grows: {arm}"
    )


def set_arg(command: list[str], option: str, value: int) -> None:
    prefix = f"{option}="
    matches = [
        i for i, token in enumerate(command) if token.startswith(prefix)
    ]
    require(len(matches) <= 1, f"duplicate {option}: {matches}")
    if matches:
        command[matches[0]] = f"{prefix}{value}"
    else:
        command.append(f"{prefix}{value}")


def run_restore(
    command: list[str], out: Path, environment: dict[str, str]
) -> int:
    with (out / "restore.log").open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=environment,
            check=False,
        )
    (out / "restore.log.exit").write_text(f"{completed.returncode}\n")
    return completed.returncode


def exact_reference(matched: Path, cg_na: int) -> tuple[str, str, list[str]]:
    lines = (matched / "strict/restore.log").read_text().splitlines()
    fingerprint = gate.base.exactly_one(
        lines,
        rf"^CG_FINGERPRINT mode=MAA elements={cg_na} .* result=PASS$",
        "matched fingerprint",
    )
    terminal = gate.base.exactly_one(
        lines,
        r"^CG_LOGICAL16_RMW_TERMINAL "
        r"treatment=page_fed_product_soa_jit .* result=PASS$",
        "matched terminal",
    )
    reductions = [
        line
        for line in lines
        if line.startswith(
            ("CG_REDUCTION_EVIDENCE ", "CG_OUTER_REDUCTION_EVIDENCE ")
        )
    ]
    require(len(reductions) == 11, "matched reductions incomplete")
    return fingerprint, terminal, reductions


def validate_completed_arm(
    arm: Arm,
    out: Path,
    matched: Path,
    expected_windows: int,
    reference: tuple[str, str, list[str]],
    gem5_sha256: str,
    guest_sha256: str,
    baseline_config: dict[str, str],
    returncode: int,
) -> dict:
    require(returncode == 0, f"{arm.name}: restore return code {returncode}")
    log = out / "restore.log"
    require(
        log.is_file() and (out / "stats.txt").is_file(),
        f"{arm.name}: missing final evidence",
    )
    lines = log.read_text(errors="replace").splitlines()
    require(
        not any(gate.base.FATAL_RE.search(line) for line in lines),
        f"{arm.name}: fatal log text",
    )
    gate.base.exactly_one(
        lines,
        r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$",
        f"{arm.name}: m5 exit",
    )
    fingerprint = gate.base.exactly_one(
        lines,
        r"^CG_FINGERPRINT mode=MAA elements=1024 .* result=PASS$",
        f"{arm.name}: fingerprint",
    )
    terminal_line = gate.base.exactly_one(
        lines,
        r"^CG_LOGICAL16_RMW_TERMINAL "
        r"treatment=page_fed_product_soa_jit .* result=PASS$",
        f"{arm.name}: numerical terminal",
    )
    reductions = [
        line
        for line in lines
        if line.startswith(
            ("CG_REDUCTION_EVIDENCE ", "CG_OUTER_REDUCTION_EVIDENCE ")
        )
    ]
    require(
        (fingerprint, terminal_line, reductions) == reference,
        f"{arm.name}: exact CG output/reduction mismatch",
    )
    config = config_values(out / "config.ini")
    fixed = {
        "virtual_combine_slots": str(SLOTS),
        "virtual_combine_words": "0",
        "virtual_response_slots": str(RESPONSE_SLOTS),
        "virtual_response_words": "0",
        "virtual_response_word_pool": "0",
        "num_spd_read_ports_per_maa": str(EXTERNAL_PORTS),
        "num_spd_write_ports_per_maa": str(EXTERNAL_PORTS),
        "virtual_strict_two_phase": "true",
        "virtual_masked_writes": "true",
    }
    for key, value in fixed.items():
        require(
            config.get(key) == value, f"{arm.name}: changed protected {key}"
        )
    require(
        config.get("virtual_combine_ways") == str(arm.ways),
        f"{arm.name}: ways did not resolve",
    )
    require(
        config.get("virtual_combine_banks") == str(arm.banks),
        f"{arm.name}: banks did not resolve",
    )
    require(
        config.get("virtual_combine_victim_policy") == str(arm.victim_policy),
        f"{arm.name}: victim policy did not resolve",
    )
    require(
        config.get("virtual_max_outstanding_writes") == str(arm.write_credits),
        f"{arm.name}: credits did not resolve",
    )
    require(
        config.get("retirement_sides")
        == baseline_config.get("retirement_sides"),
        f"{arm.name}: external retirement ports changed",
    )
    stats = gate.fused.require_stats(
        out / "stats.txt", expected_windows, "page_fed_product_soa_jit"
    )
    stats.update(
        {
            name: gate.base.stat_sum(out / "stats.txt", name)
            for name in gate.STRICT_STATS + STRICT_ZERO_STATS
        }
    )
    require(
        all(stats[name] == 0 for name in STRICT_ZERO_STATS),
        f"{arm.name}: drain/fallback stats nonzero",
    )
    trace = out / "strict_trace.log"
    p = gate.event_records(trace, "strict_two_phase_timing")
    q = gate.event_records(trace, "strict_page_fed_two_phase_timing")
    whole = gate.event_records(trace, "strict_cg_p16_q16_window")
    products = gate.event_records(trace, "strict_product_page_response")
    writes = gate.event_records(trace, "backing_write_issue")
    require(
        (len(p), len(q), len(whole), len(products))
        == (
            expected_windows,
            expected_windows,
            expected_windows,
            4 * expected_windows,
        ),
        f"{arm.name}: incomplete 65/260 ledgers",
    )
    for row in p:
        gate.validate_timing(row, page_fed=False)
    for row in q:
        gate.validate_timing(row, page_fed=True)
    require(
        all(
            gate.integer(row, "drains") == 0
            and gate.integer(row, "fallbacks") == 0
            for row in whole
        ),
        f"{arm.name}: whole ledger drain/fallback",
    )
    require(
        writes and all(gate.integer(row, "bytes") == 64 for row in writes),
        f"{arm.name}: non-64-byte P write",
    )
    require(
        len(writes) == sum(gate.integer(row, "backing_issues") for row in p),
        f"{arm.name}: P write ledger mismatch",
    )
    provenance = {
        "gem5_sha256": gem5_sha256,
        "guest_sha256": guest_sha256,
        "matched_root": str(matched),
    }
    return {
        "arm": asdict(arm),
        "decision": "VALID_FIXED_STORAGE_ARM",
        "terminal": True,
        "simTicks": stats["simTicks"],
        "p_backing_write_issues": len(writes),
        "all_p_writes_64_bytes": True,
        "p_timing_records": len(p),
        "q_timing_records": len(q),
        "whole_window_records": len(whole),
        "product_response_records": len(products),
        "strict_stats": stats,
        "provenance": provenance,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matched_root", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--gem5", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help=(
            "validate completed independent arm roots without rerunning "
            "them"
        ),
    )
    args = parser.parse_args(argv)
    require(args.jobs > 0, "jobs must be positive")
    for arm in ARMS:
        validate_arm_definition(arm)
    matched, out, gem5 = (
        args.matched_root.resolve(),
        args.out.resolve(),
        args.gem5.resolve(),
    )
    require(
        (args.validate_only and out.is_dir())
        or (not args.validate_only and not out.exists()),
        "validate-only requires an existing output root; a run requires "
        "an absent one",
    )
    root = verify_matched_root(matched)
    require(root["cg_na"] == 1024, "only accepted NA=1024 root is legal")
    require(
        gate.base.sha256_file(gem5) == root["gem5_sha256"],
        "gem5 hash differs from accepted root",
    )
    guest = matched / "cg_strict_fused_p16_q16_guest"
    require(
        gate.base.sha256_file(guest) == root["guest_sha256"],
        "guest hash differs from accepted root",
    )
    expected_windows = gate.EXPECTED_WINDOWS[1024]
    gate.fused.ACTIVE_CG_NA = 1024
    reference = exact_reference(matched, 1024)
    baseline_config = config_values(matched / "strict/config.ini")
    environment = dict(
        os.environ,
        LD_LIBRARY_PATH=str(gate.fused.RAMULATOR.parent)
        + ":"
        + os.environ.get("LD_LIBRARY_PATH", ""),
        OMP_NUM_THREADS="4",
        OMP_PROC_BIND="false",
    )
    if not args.validate_only:
        out.mkdir(parents=True)
    executions: dict[str, tuple[Arm, Path, list[str]]] = {}
    for arm in ARMS:
        arm_out = out / arm.name
        if args.validate_only:
            command_path = arm_out / "command.json"
            require(
                command_path.is_file(), f"missing recorded command: {arm.name}"
            )
            command = json.loads(command_path.read_text())
        else:
            arm_out.mkdir()
            command = gate.strict_restore_args(
                gem5,
                guest,
                matched / "input/p16_q16.selector",
                matched / "checkpoint",
                arm_out,
                strict=True,
            )
            for option, value in (
                ("--maa_virtual_combine_slots", SLOTS),
                ("--maa_virtual_combine_ways", arm.ways),
                ("--maa_virtual_combine_banks", arm.banks),
                ("--maa_virtual_combine_victim_policy", arm.victim_policy),
                ("--maa_virtual_max_outstanding_writes", arm.write_credits),
            ):
                set_arg(command, option, value)
            require(
                "--maa_virtual_masked_writes" not in command,
                "masked writes unexpectedly pre-set",
            )
            command.append("--maa_virtual_masked_writes")
            (arm_out / "command.json").write_text(
                json.dumps(command, indent=2) + "\n"
            )
        executions[arm.name] = (arm, arm_out, command)
    returncodes: dict[str, int] = {}
    if args.validate_only:
        returncodes = {
            name: int((arm_out / "restore.log.exit").read_text())
            for name, (_, arm_out, _) in executions.items()
        }
    else:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(args.jobs, len(ARMS))
        ) as pool:
            futures = {
                pool.submit(run_restore, command, arm_out, environment): name
                for name, (_, arm_out, command) in executions.items()
            }
            for future in concurrent.futures.as_completed(futures):
                returncodes[futures[future]] = future.result()
    accepted, rejected = [], []
    for name, (arm, arm_out, _) in executions.items():
        try:
            accepted.append(
                validate_completed_arm(
                    arm,
                    arm_out,
                    matched,
                    expected_windows,
                    reference,
                    root["gem5_sha256"],
                    root["guest_sha256"],
                    baseline_config,
                    returncodes[name],
                )
            )
        except RuntimeError as error:
            rejected.append(
                {
                    "arm": asdict(arm),
                    "decision": "REJECTED",
                    "reason": str(error),
                    "returncode": returncodes.get(name),
                }
            )
    baseline = next(
        (item for item in accepted if item["arm"]["name"] == ARMS[0].name),
        None,
    )
    require(
        baseline is not None,
        "baseline did not validate; no comparison is legal",
    )
    for item in accepted:
        item["baseline_over_arm"] = baseline["simTicks"] / item["simTicks"]
        item["hardware_delta"] = {
            "slots": 0,
            "response_slots": 0,
            "spd_ports": 0,
            "external_retirement_ports": 0,
            "ways": item["arm"]["ways"] - BASELINE[0],
            "banks": item["arm"]["banks"] - BASELINE[1],
            "victim_policy": item["arm"]["victim_policy"] - BASELINE[2],
            "write_credit_bound": item["arm"]["write_credits"] - BASELINE[3],
        }
    report = {
        "schema": "dx100.cg.fixed_storage_combiner_sweep.v1",
        "terminal": True,
        "matched_root": str(matched),
        "accepted_root_result": root,
        "fixed_hardware": {
            "combiner_slots": SLOTS,
            "response_slots": RESPONSE_SLOTS,
            "spd_read_ports": EXTERNAL_PORTS,
            "spd_write_ports": EXTERNAL_PORTS,
            "external_retirement_ports": EXTERNAL_PORTS,
            "native_runs": 0,
        },
        "accepted": accepted,
        "rejected": rejected,
        "pareto": [
            item
            for item in accepted
            if not any(
                other["simTicks"] < item["simTicks"]
                and other["p_backing_write_issues"]
                <= item["p_backing_write_issues"]
                for other in accepted
            )
        ],
    }
    report_path = out / "report.json"
    if args.validate_only:
        require(report_path.is_file(), "validate-only report is missing")
        require(
            json.loads(report_path.read_text()) == report,
            "validate-only result differs from the sealed report",
        )
    else:
        report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
