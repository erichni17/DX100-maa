#!/usr/bin/env python3
"""Run the exact shared-checkpoint GZP volume-only/dual-logical16 gate."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_general_hybrid_benchmark_matrix as matrix  # noqa: E402
import run_gzp_masked_index_pair as common  # noqa: E402

ARMS = (
    ("volume_only", "token_stream_ld volume_masked_index"),
    ("dual_logical16", "token_stream_ld dual_logical16"),
)
N = 16384
EXPECTED_OUTPUT_HASH = "12472729817211538253"
EXPECTED_PUBLISH_LINES = 4 * 256
SOA_LEDGER_PAIRS = (
    ("IND_SoaJitPredicateLineReads", "IND_SoaJitPredicateLineResponses"),
    ("IND_SoaJitAReadIssues", "IND_SoaJitAReadResponses"),
    ("IND_SoaJitValueReadIssues", "IND_SoaJitValueReadResponses"),
    ("IND_SoaJitAWriteIssues", "IND_SoaJitAWriteResponses"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--gem5", required=True, type=Path)
    parser.add_argument("--ramulator-library", required=True, type=Path)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/deprecated/example/se.py",
    )
    parser.add_argument(
        "--ramulator-config",
        type=Path,
        default=ROOT / "ext/ramulator2/ramulator2/example_gem5_config.yaml",
    )
    parser.add_argument("--mem-channels", type=int, default=2)
    parser.add_argument("--l3-ports", type=int, default=4)
    parser.add_argument(
        "--active-contexts", type=int, choices=(8, 16, 32), default=8
    )
    parser.add_argument(
        "--active-value-owners", type=int, choices=(32, 64, 128), default=32
    )
    parser.add_argument("--replicas", type=int, choices=range(1, 9), default=1)
    parser.add_argument("--expected-gem5-sha256")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.execute and not re.fullmatch(
        r"[0-9a-f]{64}", args.expected_gem5_sha256 or ""
    ):
        parser.error("--execute requires --expected-gem5-sha256")
    return args


def optional_sum(stats: dict[str, int], suffix: str) -> int:
    return sum(value for name, value in stats.items() if name.endswith(suffix))


def optional_max(stats: dict[str, int], suffix: str) -> int:
    return max(
        (value for name, value in stats.items() if name.endswith(suffix)),
        default=0,
    )


def analyze_run(name: str, run: Path) -> dict[str, int | str]:
    if (run / "restore.exit").read_text().strip() != "0":
        raise RuntimeError(f"{name}: restore wrapper failed")
    log = (run / "restore.log").read_text(errors="replace")
    if (
        len(
            re.findall(
                r"Exiting @ tick \d+ because m5_exit instruction encountered",
                log,
            )
        )
        != 1
    ):
        raise RuntimeError(f"{name}: missing unique m5_exit")
    if re.search(
        r"\b(?:panic|fatal|segmentation fault|Assertion)\b", log, re.I
    ):
        raise RuntimeError(f"{name}: fatal marker in restore log")
    lines = log.splitlines()
    output = common.parse_fields(common.exactly_one(lines, "UME_OUTPUT_FP "))
    reference = common.parse_fields(
        common.exactly_one(lines, "UME_REFERENCE_PASS ")
    )
    ledger = common.parse_fields(
        common.exactly_one(lines, "UME_GZP_MASKED_INDEX_LEDGER ")
    )
    terminal = common.parse_fields(
        common.exactly_one(lines, "UME_GZP_TERMINAL ")
    )
    if (
        output.get("output_hash") != EXPECTED_OUTPUT_HASH
        or output.get("nonfinite") != "0"
        or reference.get("point_volume_errors") != "0"
        or reference.get("point_gradient_errors") != "0"
        or reference.get("elements") != "196384"
    ):
        raise RuntimeError(f"{name}: exact FP32 output gate failed")
    zero_ledger = (
        "active_uint32_max",
        "active_illegal_index",
        "inactive_legal_index",
        "inactive_non_sentinel",
    )
    if (
        ledger.get("result") != "PASS"
        or ledger.get("exact_equivalence") != "1"
        or any(int(ledger.get(key, "-1")) != 0 for key in zero_ledger)
    ):
        raise RuntimeError(f"{name}: masked-index ledger failed")

    expected_instructions = 1 if name == "volume_only" else 2
    expected_selected = int(ledger["full_selected"]) * expected_instructions
    expected_rejected = int(ledger["full_rejected"]) * expected_instructions
    stats = common.first_stats(run / "gem5/stats.txt")
    soa = {
        suffix: common.sum_suffix(stats, suffix)
        for suffix in {item for pair in SOA_LEDGER_PAIRS for item in pair}
        | {
            "IND_SoaJitInstructions",
            "IND_SoaJitSelected",
            "IND_SoaJitPredicateRejected",
            "IND_SoaJitAliasesApplied",
            "IND_SoaJitValueDeliveries",
            "IND_SoaJitLookaheadIssues",
            "IND_SoaJitLookaheadResponses",
            "IND_SoaJitTerminalCompletions",
            "IND_SoaJitValueHits",
            "IND_SoaJitValueMergedWaiters",
            "IND_SoaJitValueFills",
            "IND_SoaJitValuePrefetchIssues",
            "IND_SoaJitValuePrefetchResponses",
            "IND_SoaJitPreAValueIssues",
            "IND_SoaJitPreAValueReadyAtAResponse",
            "IND_SoaJitPreAValueUses",
        }
    }
    if (
        soa["IND_SoaJitInstructions"] != expected_instructions
        or soa["IND_SoaJitTerminalCompletions"] != expected_instructions
        or soa["IND_SoaJitSelected"] != expected_selected
        or soa["IND_SoaJitPredicateRejected"] != expected_rejected
        or soa["IND_SoaJitAliasesApplied"] != expected_selected
        or soa["IND_SoaJitValueDeliveries"] != expected_selected
        or soa["IND_SoaJitLookaheadIssues"] != expected_selected
        or soa["IND_SoaJitLookaheadResponses"] != expected_selected
    ):
        raise RuntimeError(f"{name}: SoA/JIT terminal/value ledger failed")
    if any(soa[left] != soa[right] for left, right in SOA_LEDGER_PAIRS):
        raise RuntimeError(f"{name}: SoA/JIT request/response ledger failed")
    if (
        soa["IND_SoaJitPredicateLineReads"] != 0
        or soa["IND_SoaJitAReadIssues"] != soa["IND_SoaJitAWriteIssues"]
        or soa["IND_SoaJitValueReadIssues"]
        + soa["IND_SoaJitValueHits"]
        + soa["IND_SoaJitValueMergedWaiters"]
        != expected_selected
        or soa["IND_SoaJitValueFills"] != soa["IND_SoaJitValueReadResponses"]
        or soa["IND_SoaJitValuePrefetchIssues"] != 0
        or soa["IND_SoaJitValuePrefetchResponses"] != 0
        or soa["IND_SoaJitPreAValueIssues"] <= 0
        or soa["IND_SoaJitPreAValueIssues"] != soa["IND_SoaJitPreAValueUses"]
        or not 0
        < soa["IND_SoaJitPreAValueReadyAtAResponse"]
        <= soa["IND_SoaJitPreAValueUses"]
    ):
        raise RuntimeError(f"{name}: masked/value/A-line ledger failed")

    trace_path = run / "gem5/virtual_trace.log"
    if not trace_path.is_file():
        raise RuntimeError(f"{name}: missing virtual trace")
    trace_text = trace_path.read_text(errors="replace")
    soa_terminals = [
        common.parse_fields(line)
        for line in trace_text.splitlines()
        if "event=soa_jit_complete" in line and "terminal=1" in line
    ]
    if len(soa_terminals) != expected_instructions or any(
        entry.get("predicate_mode") != "masked_index"
        or int(entry.get("selected", "-1"))
        + int(entry.get("predicate_rejected", "-1"))
        != N
        or entry.get("a_reads", "0/1").split("/")[0]
        != entry.get("a_reads", "1/0").split("/")[-1]
        or entry.get("a_writes", "0/1").split("/")[0]
        != entry.get("a_writes", "1/0").split("/")[-1]
        for entry in soa_terminals
    ):
        raise RuntimeError(f"{name}: trace terminal generation ledger failed")

    expected_treatment = (
        "volume_masked_index_soa_jit"
        if name == "volume_only"
        else "dual_logical16_soa_jit"
    )
    expected_gradient_values = 0 if name == "volume_only" else N
    expected_publisher = (
        "masked_index_no_predicate_publication"
        if name == "volume_only"
        else "response_bearing_gradient_only"
    )
    required_terminal = {
        "treatment": expected_treatment,
        "masked_index_windows": "1" if name == "volume_only" else "0",
        "dual_logical16_windows": "0" if name == "volume_only" else "1",
        "published_predicates": "0",
        "published_gradient_values": str(expected_gradient_values),
        "published_gradient_bytes": str(expected_gradient_values * 4),
        "publisher": expected_publisher,
        "predicate_publications": "0",
        "predicate_publication_bytes": "0",
        "producer_staging_elements": "4096",
        "producer_staging_bytes": "16384",
        "publisher_credit_payload_bytes": "512",
        "coherent_gradient_backing_elements": "65536",
        "coherent_gradient_backing_bytes": "262144",
        "hidden_logical16_payload_bytes": "0",
        "cpu_untimed_copy_bytes": "0",
        "performance_promotable": "1",
        "result": "PASS",
    }
    if any(
        terminal.get(key) != value for key, value in required_terminal.items()
    ):
        raise RuntimeError(f"{name}: terminal/accounting contract failed")

    publish = {
        suffix: optional_sum(stats, suffix)
        for suffix in (
            "STR_PublishIssues",
            "STR_PublishAccepts",
            "STR_PublishWriteResponses",
            "STR_PublishTerminals",
            "STR_PublishRetries",
            "STR_PublishCreditStalls",
            "STR_PublishOverlapIssues",
        )
    }
    expected_lines = 0 if name == "volume_only" else EXPECTED_PUBLISH_LINES
    expected_terminals = 0 if name == "volume_only" else 4
    if (
        publish["STR_PublishIssues"] != expected_lines
        or publish["STR_PublishAccepts"] != expected_lines
        or publish["STR_PublishWriteResponses"] != expected_lines
        or publish["STR_PublishTerminals"] != expected_terminals
        or optional_max(stats, "STR_PublishCreditHWM")
        != (0 if name == "volume_only" else 8)
    ):
        raise RuntimeError(f"{name}: publisher WriteResp ledger failed")
    publish_trace_counts = {
        event: trace_text.count(f"event=spd_publish_{event} ")
        for event in ("issue", "accept", "response", "terminal")
    }
    if publish_trace_counts != {
        "issue": expected_lines,
        "accept": expected_lines,
        "response": expected_lines,
        "terminal": expected_terminals,
    }:
        raise RuntimeError(f"{name}: publisher trace ledger failed")

    expected_rmw_instructions = 5 if name == "volume_only" else 2
    rmw_instructions = optional_sum(stats, "numInst_INDRMW")
    rmw_cycles = optional_sum(stats, "cycles_INDRMW")
    if rmw_instructions != expected_rmw_instructions or rmw_cycles <= 0:
        raise RuntimeError(f"{name}: RMW instruction/cycle ledger failed")

    return {
        "arm": name,
        "simTicks": stats["simTicks"],
        "output_hash": output["output_hash"],
        "index_hash": ledger["index_hash"],
        "soa_instructions": expected_instructions,
        "soa_selected": soa["IND_SoaJitSelected"],
        "a_reads": soa["IND_SoaJitAReadIssues"],
        "a_write_responses": soa["IND_SoaJitAWriteResponses"],
        "pre_a_issues": soa["IND_SoaJitPreAValueIssues"],
        "pre_a_ready": soa["IND_SoaJitPreAValueReadyAtAResponse"],
        "indirect_instructions": common.sum_suffix(stats, "IND_NumInsts"),
        "stream_instructions": common.sum_suffix(stats, "STR_NumInsts"),
        "rmw_instructions": rmw_instructions,
        "rmw_cycles": rmw_cycles,
        "publish_lines": publish["STR_PublishIssues"],
        "publish_responses": publish["STR_PublishWriteResponses"],
        "publish_retries": publish["STR_PublishRetries"],
        "publish_credit_stalls": publish["STR_PublishCreditStalls"],
        "publish_overlap_issues": publish["STR_PublishOverlapIssues"],
        "published_gradient_bytes": expected_gradient_values * 4,
    }


def compare(
    rows: list[dict[str, int | str]], replicas: int
) -> dict[str, int | float | str | bool]:
    grouped = {
        arm: [row for row in rows if row["arm"] == arm] for arm, _ in ARMS
    }
    if any(len(grouped[arm]) != replicas for arm, _ in ARMS):
        raise RuntimeError("replica count mismatch")
    for replica in range(1, replicas + 1):
        baseline = grouped["volume_only"][replica - 1]
        candidate = grouped["dual_logical16"][replica - 1]
        for key in ("output_hash", "index_hash"):
            if baseline[key] != candidate[key]:
                raise RuntimeError(f"replica {replica} pair mismatch: {key}")
    for arm, _ in ARMS:
        reference = {
            key: value
            for key, value in grouped[arm][0].items()
            if key != "replica"
        }
        if any(
            {key: value for key, value in row.items() if key != "replica"}
            != reference
            for row in grouped[arm][1:]
        ):
            raise RuntimeError(f"{arm}: replicas are not deterministic")
    baseline = grouped["volume_only"][0]
    candidate = grouped["dual_logical16"][0]
    tick_delta = int(candidate["simTicks"]) - int(baseline["simTicks"])
    decision = "ACCEPT" if tick_delta < 0 else "REJECT"
    return {
        "decision": decision,
        "replicas": replicas,
        "deterministic_replicas": True,
        "baseline_simTicks": int(baseline["simTicks"]),
        "candidate_simTicks": int(candidate["simTicks"]),
        "candidate_minus_baseline_ticks": tick_delta,
        "baseline_over_candidate_speedup": int(baseline["simTicks"])
        / int(candidate["simTicks"]),
        "indirect_instruction_delta": int(candidate["indirect_instructions"])
        - int(baseline["indirect_instructions"]),
        "stream_instruction_delta": int(candidate["stream_instructions"])
        - int(baseline["stream_instructions"]),
        "baseline_rmw_instructions": int(baseline["rmw_instructions"]),
        "candidate_rmw_instructions": int(candidate["rmw_instructions"]),
        "rmw_instruction_delta": int(candidate["rmw_instructions"])
        - int(baseline["rmw_instructions"]),
        "baseline_rmw_cycles": int(baseline["rmw_cycles"]),
        "candidate_rmw_cycles": int(candidate["rmw_cycles"]),
        "rmw_cycle_delta": int(candidate["rmw_cycles"])
        - int(baseline["rmw_cycles"]),
        "publisher_serialization_observed": int(
            candidate["publish_credit_stalls"]
        )
        > 0,
        "publisher_overlap_issues": int(candidate["publish_overlap_issues"]),
        "exact_output": "PASS",
        "terminal_ledgers": "PASS",
        "write_response_ledgers": "PASS",
    }


def main() -> int:
    args = parse_args()
    plan = {
        "schema": "dx100.gzp_dual_logical16_one_window.v2",
        "n": N,
        "arms": [name for name, _ in ARMS],
        "shared_guest": True,
        "shared_checkpoint": True,
        "only_treatment": "GZP RMW selector",
        "logical_elements": 16384,
        "physical_spd_elements": 4096,
        "pre_a_value_lookahead": True,
        "active_contexts": args.active_contexts,
        "active_value_owners": args.active_value_owners,
        "replicas": args.replicas,
        "trace_flags": ["MAAVirtualTrace", "MAATrace"],
        "full_gzp_authorized": False,
    }
    if not args.execute:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    required = (
        args.gem5,
        args.ramulator_library,
        args.config,
        args.ramulator_config,
    )
    if any(not path.is_file() for path in required):
        raise SystemExit("missing gem5/config/Ramulator input")
    if common.source_status():
        raise SystemExit("evidence execution requires a clean source tree")
    if args.out.exists():
        raise SystemExit(f"refusing existing output: {args.out}")
    if common.sha256(args.gem5) != args.expected_gem5_sha256:
        raise SystemExit("gem5 SHA-256 mismatch")

    args.out.mkdir(parents=True)
    common.atomic_text(args.out / "campaign.exit", "running\n")
    env = os.environ.copy()
    env.update({"OMP_NUM_THREADS": "4", "OMP_PROC_BIND": "false"})
    library_path = str(args.ramulator_library.resolve().parent)
    env["LD_LIBRARY_PATH"] = library_path + (
        ":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else ""
    )
    try:
        inputs = args.out / "inputs"
        inputs.mkdir()
        guest = inputs / "gradzatp_dual_logical16"
        compile_command = common.compile_guest(guest, env)
        selector = inputs / "treatment.txt"
        common.atomic_text(selector, ARMS[0][1] + "\n")
        ramulator = inputs / "ramulator.yaml"
        shutil.copy2(args.ramulator_config, ramulator)
        frozen_config, config_identity = matrix.freeze_config_tree(
            args.config, ROOT / "configs", inputs / "configs"
        )
        checkpoint = args.out / "checkpoint"
        checkpoint.mkdir()
        checkpoint_command = matrix.checkpoint_command(
            args.gem5.resolve(),
            frozen_config,
            checkpoint,
            guest,
            f"{N} {selector}",
        )
        if common.run_logged(
            checkpoint_command, args.out / "checkpoint.log", env
        ):
            raise RuntimeError("checkpoint creation failed")
        checkpoint_log = (args.out / "checkpoint.log").read_text(
            errors="replace"
        )
        if (
            len(
                re.findall(
                    r"Exiting @ tick \d+ because checkpoint", checkpoint_log
                )
            )
            != 1
        ):
            raise RuntimeError("checkpoint marker is not unique")
        checkpoint_identity = matrix.tree_identity(checkpoint)
        extra = [
            "--maa_virtual_response_slots=1152",
            "--maa_virtual_response_word_pool=2304",
            "--maa_virtual_combine_slots=512",
            "--maa_virtual_combine_words=4096",
            "--maa_virtual_combine_ways=16",
            "--maa_virtual_words_per_cycle=4",
            "--maa_virtual_combine_banks=8",
            "--maa_virtual_index_buffer_lines=8",
            f"--maa_soa_jit_active_contexts={args.active_contexts}",
            "--maa_soa_jit_value_lookahead=8",
            "--maa_soa_jit_value_cache_enable",
            "--maa_soa_jit_predicate_active_credits=16",
            f"--maa_soa_jit_active_value_owners={args.active_value_owners}",
            "--maa_soa_jit_apply_lanes=1",
            "--maa_soa_jit_pre_a_value_lookahead",
        ]
        rows: list[dict[str, int | str]] = []
        run_records: list[dict[str, str]] = []
        for replica in range(1, args.replicas + 1):
            for name, payload in ARMS:
                run_name = name if args.replicas == 1 else f"{name}_r{replica}"
                run = args.out / "runs" / run_name
                run.mkdir(parents=True)
                common.atomic_text(selector, payload + "\n")
                common.atomic_text(
                    run / "frozen_treatment.txt", payload + "\n"
                )
                selector_hash = common.sha256(selector)
                command = matrix.restore_command(
                    args.gem5.resolve(),
                    frozen_config,
                    run / "gem5",
                    checkpoint,
                    guest,
                    f"{N} {selector}",
                    "hybrid",
                    ramulator,
                    args.mem_channels,
                    args.l3_ports,
                    extra,
                )
                virtual_trace_flag = "--debug-flags=MAAVirtualTrace"
                if command.count(virtual_trace_flag) != 1:
                    raise RuntimeError(
                        "restore command lost its virtual trace flag"
                    )
                command[
                    command.index(virtual_trace_flag)
                ] = "--debug-flags=MAAVirtualTrace,MAATrace"
                if matrix.tree_identity(checkpoint) != checkpoint_identity:
                    raise RuntimeError(
                        "shared checkpoint changed before restore"
                    )
                if common.run_logged(command, run / "restore.log", env):
                    raise RuntimeError(f"{run_name}: restore failed")
                if common.sha256(selector) != selector_hash:
                    raise RuntimeError(
                        f"{run_name}: selector changed during restore"
                    )
                if matrix.tree_identity(checkpoint) != checkpoint_identity:
                    raise RuntimeError(
                        "shared checkpoint changed during restore"
                    )
                config = (run / "gem5/config.ini").read_text()
                for required_config in (
                    "num_tile_elements=16384",
                    "physical_tile_elements=4096",
                    f"soa_jit_active_contexts={args.active_contexts}",
                    "soa_jit_pre_a_value_lookahead=true",
                    f"soa_jit_active_value_owners={args.active_value_owners}",
                ):
                    if required_config not in config:
                        raise RuntimeError(
                            f"{run_name}: missing {required_config}"
                        )
                row = analyze_run(name, run)
                row["replica"] = replica
                rows.append(row)
                run_records.append(
                    {
                        "arm": name,
                        "replica": replica,
                        "selector": payload,
                        "selector_sha256": selector_hash,
                        "command_sha256": common.sha256(
                            run / "restore.command.json"
                        ),
                    }
                )
        summary = compare(rows, args.replicas)
        manifest = {
            **plan,
            "source": {
                "commit": common.source_commit(),
                "status": "clean",
                "gradzatp_sha256": common.sha256(
                    ROOT / "benchmarks/UME/gradzatp.cpp"
                ),
                "runner_sha256": common.sha256(Path(__file__)),
            },
            "gem5": {
                "path": str(args.gem5.resolve()),
                "sha256": common.sha256(args.gem5),
            },
            "guest": {"path": str(guest), "sha256": common.sha256(guest)},
            "compile_command": compile_command,
            "ramulator_library": {
                "path": str(args.ramulator_library.resolve()),
                "sha256": common.sha256(args.ramulator_library),
            },
            "ramulator_config": {
                "path": str(ramulator),
                "sha256": common.sha256(ramulator),
            },
            "config_tree": config_identity,
            "checkpoint": checkpoint_identity,
            "runs": run_records,
            "simulated_metric": "simTicks",
            "host_time_metric_authorized": False,
        }
        common.atomic_json(args.out / "manifest.json", manifest)
        common.atomic_json(
            args.out / "results.json", {"rows": rows, "summary": summary}
        )
        with (args.out / "results.tsv").open("w", newline="") as output:
            writer = csv.DictWriter(
                output, fieldnames=list(rows[0]), delimiter="\t"
            )
            writer.writeheader()
            writer.writerows(rows)
        common.atomic_text(
            args.out / "summary.txt",
            "".join(f"{key}={value}\n" for key, value in summary.items()),
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        common.atomic_text(args.out / "campaign.exit", "1\n")
        print(f"error: {error}", file=sys.stderr)
        return 1
    common.atomic_text(args.out / "campaign.exit", "0\n")
    print((args.out / "results.tsv").read_text(), end="")
    print((args.out / "summary.txt").read_text(), end="")
    print("GZP_DUAL_LOGICAL16_ONE_WINDOW_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
