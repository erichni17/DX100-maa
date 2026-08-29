#!/usr/bin/env python3
"""Compare the 16-line hybrid with a bounded complete-line combiner."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.scripts import run_hybrid_dense_write_allocate_pair as pair
from experiments.scripts import run_hybrid_equal_work_micro_matrix as base

ARM = base.ArmSpec(
    "placeholder", "transparent", 4096, 16384, 4096, 64, True, 1, 4, 4
)
TREATMENTS = {
    "control16": {
        "slots": 16, "words": 0, "ways": 0, "response_words": 0,
        "result_words": 192
    },
    "safe512w16": {
        "slots": 512, "words": 1600, "ways": 16,
        "response_words": 64,
        "result_words": 1664
    },
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise pair.PairError(message)


def command_for(gem5: Path, out: Path, slots: int, words: int,
                ways: int, response_words: int) -> list[str]:
    command = json.loads(
        (pair.PREDECESSOR / "arms/hybrid64/command.json").read_text()
    )
    command[0] = str(gem5)
    scripts = [
        i for i, token in enumerate(command)
        if token.endswith("/configs/deprecated/example/se.py")
    ]
    require(len(scripts) == 1, "expected one se.py command token")
    command[scripts[0]] = str(ROOT / "configs/deprecated/example/se.py")
    pair.set_option(command, "--outdir=", str(out))
    pair.set_option(command, "--maa_virtual_combine_slots=", str(slots))
    pair.set_option(command, "--maa_virtual_combine_words=", str(words))
    pair.set_option(command, "--maa_virtual_combine_ways=", str(ways))
    pair.set_option(
        command, "--maa_virtual_response_word_pool=", str(response_words)
    )
    if response_words:
        command.append("--maa_virtual_complete_line_only")
    if not any(
        token.startswith("--maa_virtual_index_issue_lines_per_cycle=")
        for token in command
    ):
        command.append("--maa_virtual_index_issue_lines_per_cycle=1")
    return command


def ramulator_reads(log: str) -> int:
    values: dict[int, int] = {}
    for channel, count in re.findall(
        r"^\s*SYS([0-9]+)_total_num_read_requests_T:\s*([0-9]+)",
        log, re.MULTILINE,
    ):
        values[int(channel)] = int(count)
    require(values, "missing Ramulator read totals")
    return sum(values.values())


def classify(root: Path, name: str) -> dict[str, object]:
    treatment = TREATMENTS[name]
    spec = base.ArmSpec(
        name, ARM.mode, ARM.page_elements, ARM.logical_elements,
        ARM.physical_elements, ARM.feeder_lines, ARM.strict,
        ARM.expected_indirect_ops, ARM.expected_stream_writes,
        ARM.expected_scalar_ops,
    )
    item = base.classify_arm(
        root,
        spec,
        combine_slots=treatment["slots"],
        combine_words=treatment["words"],
        combine_ways=treatment["ways"],
        response_word_pool=treatment["response_words"],
        strict_result_words=treatment["result_words"],
        require_partial_retirement=name == "control16",
    )
    config = base.parse_config(root / "arms" / name / "run/config.ini")
    require(
        config.get("virtual_complete_line_only")
        == ("false" if name == "control16" else "true"),
        f"{name}: complete-line-only option did not resolve",
    )
    stats = base.first_stats_section(root / "arms" / name / "run/stats.txt")
    diagnostics = {
        "combine_line_hwm": base.summed_stat(
            stats, "IND_VirtCombineLineHighWater"
        ),
        "combine_word_hwm": base.summed_stat(
            stats, "IND_VirtCombineWordHighWater"
        ),
        "l3_backing_readex_misses": base.exact_stat(
            stats, "system.l3.ReadExReq_9.misses::maa"
        ) if name == "control16" else int(
            stats.get("system.l3.ReadExReq_9.misses::maa", 0)
        ),
        "l3_total_maa_misses": base.exact_stat(
            stats, "system.l3.demandMisses_T::maa"
        ),
        "l3_total_maa_miss_latency": base.exact_stat(
            stats, "system.l3.demandMissLatency_T::maa"
        ),
        "ramulator_reads": ramulator_reads(
            (root / "arms" / name / "restore.log").read_text()
        ),
    }
    require(
        diagnostics["combine_line_hwm"] <= treatment["slots"]
        and diagnostics["combine_word_hwm"]
        <= (treatment["words"] or treatment["slots"] * 8),
        f"{name}: combiner bound exceeded",
    )
    if name == "safe512w16":
        require(
            item["counters"]["full_writes"] == 2048
            and item["counters"]["partial_writes"] == 0,
            "safe512w16 did not emit exactly one complete write per line",
        )
    return {**item, "diagnostics": diagnostics}


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
    root.mkdir(parents=True)
    (root / "arms").mkdir()
    environment = dict(os.environ)
    environment["LD_LIBRARY_PATH"] = (
        str(pair.RAMULATOR.parent) + ":"
        + environment.get("LD_LIBRARY_PATH", "")
    )
    environment["OMP_NUM_THREADS"] = "4"
    environment["OMP_PROC_BIND"] = "false"
    for name, treatment in TREATMENTS.items():
        arm = root / "arms" / name
        arm.mkdir()
        selector = arm / "treatment.txt"
        selector.write_text(ARM.treatment)
        command = command_for(
            gem5,
            arm / "run",
            treatment["slots"],
            treatment["words"],
            treatment["ways"],
            treatment["response_words"],
        )
        wrapper = pair.wrapped(root, selector, command)
        (arm / "command.json").write_text(json.dumps(command, indent=2) + "\n")
        (arm / "wrapped_command.json").write_text(
            json.dumps(wrapper, indent=2) + "\n"
        )
        (arm / "arm.json").write_text(
            json.dumps(treatment, indent=2, sort_keys=True) + "\n"
        )
        rc = base.run_command(
            wrapper, arm / "restore.log", environment, arm / "process.json"
        )
        (arm / "restore.exit").write_text(f"{rc}\n")
        require(rc == 0, f"{name}: restore exited {rc}")

    arms = {name: classify(root, name) for name in TREATMENTS}
    control_ticks = int(arms["control16"]["counters"]["simTicks"])
    safe_ticks = int(arms["safe512w16"]["counters"]["simTicks"])
    result = {
        "schema": "dx100.hybrid_safe_combiner_pair.v1",
        "terminal": True,
        "decision": "VALID_SAFE_COMBINER_PAIR",
        "gem5_sha256": args.gem5_sha256,
        "source_commit": base.source_commit(),
        "same_binary": True,
        "same_checkpoint": True,
        "physical_result_word_bound": 4096,
        "arms": arms,
        "safe512w16_latency_change_pct": 100 * (safe_ticks / control_ticks - 1),
        "control_over_safe512w16": control_ticks / safe_ticks,
    }
    (root / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    (root / "gate.complete").write_text(
        "VALID_SAFE_COMBINER_PAIR\ncorrectness=EXACT_MATCH\n"
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
