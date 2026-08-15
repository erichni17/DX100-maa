#!/usr/bin/env python3
"""Run the exact shared-checkpoint GZP shared-index dual-RMW gate."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_general_hybrid_benchmark_matrix as matrix  # noqa: E402
import run_gzp_masked_index_pair as common  # noqa: E402

ARMS = (
    ("volume_only", "token_stream_ld volume_masked_index"),
    ("shared_index", "token_stream_ld dual_shared_index"),
)
LOGICAL_ELEMENTS = 16384
EXPECTED_OUTPUT_HASH = {
    16384: "12472729817211538253",
    1_000_000: "11225737641199706160",
}
EXPECTED_REFERENCE_ELEMENTS = {16384: 196384, 1_000_000: 1180000}
PUBLISH_LINES_PER_WINDOW = 4 * 256
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
        "--active-contexts", type=int, choices=(8, 16, 32), default=32
    )
    parser.add_argument(
        "--active-value-owners", type=int, choices=(32, 64, 128), default=32
    )
    parser.add_argument("--replicas", type=int, choices=range(1, 9), default=1)
    parser.add_argument(
        "--n", type=int, choices=EXPECTED_OUTPUT_HASH, default=16384
    )
    parser.add_argument("--parallel-restores", action="store_true")
    parser.add_argument("--expected-gem5-sha256")
    parser.add_argument(
        "--one-window-manifest",
        type=Path,
        help="accepted n=16384 manifest required before full GZP",
    )
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.execute and not re.fullmatch(
        r"[0-9a-f]{64}", args.expected_gem5_sha256 or ""
    ):
        parser.error("--execute requires --expected-gem5-sha256")
    if args.n == 1_000_000 and args.one_window_manifest is None:
        parser.error(
            "full GZP shared-index execution requires accepted one-window evidence"
        )
    return args


def validate_one_window_manifest(
    args: argparse.Namespace,
) -> dict[str, object]:
    if args.n != 1_000_000:
        return {}
    try:
        manifest = json.loads(args.one_window_manifest.read_text())
        results_path = args.one_window_manifest.parent / "results.json"
        results = json.loads(results_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"invalid one-window promotion evidence: {error}")
    summary = results.get("summary", {})
    source = manifest.get("source", {})
    gem5 = manifest.get("gem5", {})
    if (
        manifest.get("schema") != "dx100.gzp_shared_index_gate.v4"
        or manifest.get("n") != LOGICAL_ELEMENTS
        or manifest.get("candidate") != "shared_index"
        or summary.get("decision") != "ACCEPT"
        or summary.get("mechanism_closed") is not True
        or source.get("commit") != common.source_commit()
        or gem5.get("sha256") != args.expected_gem5_sha256
    ):
        raise SystemExit(
            "full GZP shared-index execution requires accepted one-window evidence"
        )
    return {
        "manifest": str(args.one_window_manifest.resolve()),
        "manifest_sha256": common.sha256(args.one_window_manifest),
        "results_sha256": common.sha256(results_path),
    }


def optional_sum(stats: dict[str, int], suffix: str) -> int:
    return sum(value for name, value in stats.items() if name.endswith(suffix))


def optional_max(stats: dict[str, int], suffix: str) -> int:
    return max(
        (value for name, value in stats.items() if name.endswith(suffix)),
        default=0,
    )


def analyze_run(name: str, run: Path, n: int) -> dict[str, int | str]:
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
        output.get("output_hash") != EXPECTED_OUTPUT_HASH[n]
        or output.get("nonfinite") != "0"
        or reference.get("point_volume_errors") != "0"
        or reference.get("point_gradient_errors") != "0"
        or reference.get("elements") != str(EXPECTED_REFERENCE_ELEMENTS[n])
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

    full_windows = n // LOGICAL_ELEMENTS
    instruction_multiplier = 1
    expected_instructions = full_windows * instruction_multiplier
    expected_selected = int(ledger["full_selected"]) * instruction_multiplier
    expected_rejected = int(ledger["full_rejected"]) * instruction_multiplier
    expected_value_uses = expected_selected * (
        2 if name == "shared_index" else 1
    )
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
            "IND_VirtIndexLineReads",
            "IND_CyclesFill",
            "IND_CyclesRequest",
        }
    }
    if (
        soa["IND_SoaJitInstructions"] != expected_instructions
        or soa["IND_SoaJitTerminalCompletions"] != expected_instructions
        or soa["IND_SoaJitSelected"] != expected_selected
        or soa["IND_SoaJitPredicateRejected"] != expected_rejected
        or soa["IND_SoaJitAliasesApplied"] != expected_value_uses
        or soa["IND_SoaJitValueDeliveries"] != expected_value_uses
        or soa["IND_SoaJitLookaheadIssues"] != expected_value_uses
        or soa["IND_SoaJitLookaheadResponses"] != expected_value_uses
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
        != expected_value_uses
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
    soa_terminals: list[dict[str, str]] = []
    publish_trace_counts = {
        event: 0 for event in ("issue", "accept", "response", "terminal")
    }
    with trace_path.open(errors="replace") as trace:
        for line in trace:
            if "event=soa_jit_complete" in line and "terminal=1" in line:
                soa_terminals.append(common.parse_fields(line))
            for event in publish_trace_counts:
                if f"event=spd_publish_{event} " in line:
                    publish_trace_counts[event] += 1
    if len(soa_terminals) != expected_instructions or any(
        entry.get("predicate_mode") != "masked_index"
        or int(entry.get("selected", "-1"))
        + int(entry.get("predicate_rejected", "-1"))
        != LOGICAL_ELEMENTS
        or entry.get("a_reads", "0/1").split("/")[0]
        != entry.get("a_reads", "1/0").split("/")[-1]
        or entry.get("a_writes", "0/1").split("/")[0]
        != entry.get("a_writes", "1/0").split("/")[-1]
        or (
            name == "shared_index"
            and (
                entry.get("destinations") != "2"
                or entry.get("value_streams") != "2"
                or entry.get("shared_index_builds") != "1"
                or entry.get("a_result_payload_bytes") != "4096"
                or entry.get("max_a_result_payload_bytes") != "4096"
                or int(entry.get("auxiliary_operand_payload_bytes", "-1"))
                != 12288
                or entry.get("transient_write_transport_payload_bytes")
                != "4096"
                or entry.get("external_ports_added") != "0"
                or entry.get("hidden_logical16_payload_bytes") != "0"
                or entry.get("physical_3p2ghz_realizability") != "unclaimed"
            )
        )
        for entry in soa_terminals
    ):
        raise RuntimeError(f"{name}: trace terminal generation ledger failed")

    expected_treatment = (
        "volume_masked_index_soa_jit"
        if name == "volume_only"
        else "dual_shared_index_soa_jit"
    )
    expected_gradient_values = (
        0 if name == "volume_only" else full_windows * LOGICAL_ELEMENTS
    )
    expected_publisher = (
        "masked_index_no_predicate_publication"
        if name == "volume_only"
        else "response_bearing_gradient_only"
    )
    required_terminal = {
        "treatment": expected_treatment,
        "masked_index_windows": (
            str(full_windows) if name == "volume_only" else "0"
        ),
        "dual_logical16_windows": ("0"),
        "shared_index_windows": (
            "0" if name == "volume_only" else str(full_windows)
        ),
        "shared_index_builds": (
            "0" if name == "volume_only" else str(full_windows)
        ),
        "value_streams": "1" if name == "volume_only" else "2",
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
        "a_result_payload_bytes": "2048" if name == "volume_only" else "4096",
        "max_a_result_payload_bytes": "4096",
        "auxiliary_operand_payload_bytes": "12288",
        "transient_write_transport_payload_bytes": "4096",
        "context64_composable": "0",
        "external_ports_added": "0",
        "physical_3p2ghz_realizability": "unclaimed",
        "cpu_untimed_copy_bytes": "0",
        "performance_promotable": ("1" if name == "volume_only" else "0"),
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
    expected_lines = (
        0 if name == "volume_only" else full_windows * PUBLISH_LINES_PER_WINDOW
    )
    expected_terminals = 0 if name == "volume_only" else full_windows * 4
    if (
        publish["STR_PublishIssues"] != expected_lines
        or publish["STR_PublishAccepts"] != expected_lines
        or publish["STR_PublishWriteResponses"] != expected_lines
        or publish["STR_PublishTerminals"] != expected_terminals
        or optional_max(stats, "STR_PublishCreditHWM")
        != (0 if name == "volume_only" else 8)
    ):
        raise RuntimeError(f"{name}: publisher WriteResp ledger failed")
    if publish_trace_counts != {
        "issue": expected_lines,
        "accept": expected_lines,
        "response": expected_lines,
        "terminal": expected_terminals,
    }:
        raise RuntimeError(f"{name}: publisher trace ledger failed")

    tail_rmw_instructions = 2 if n % LOGICAL_ELEMENTS else 0
    expected_rmw_instructions = (
        full_windows * (5 if name == "volume_only" else 1)
        + tail_rmw_instructions
    )
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
        "index_line_reads": soa["IND_VirtIndexLineReads"],
        "fill_cycles": soa["IND_CyclesFill"],
        "request_cycles": soa["IND_CyclesRequest"],
        "a_result_payload_bytes": 2048 if name == "volume_only" else 4096,
        "auxiliary_operand_payload_bytes": 12288,
        "transient_write_transport_payload_bytes": 4096,
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
        candidate = grouped["shared_index"][replica - 1]
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
    candidate = grouped["shared_index"][0]
    tick_delta = int(candidate["simTicks"]) - int(baseline["simTicks"])
    mechanism_closed = (
        int(candidate["a_result_payload_bytes"]) <= 4096
        and int(candidate["index_line_reads"])
        == int(baseline["index_line_reads"])
        and int(candidate["publish_responses"])
        == int(candidate["publish_lines"])
        and int(candidate["a_reads"]) == int(candidate["a_write_responses"])
    )
    decision = "ACCEPT" if tick_delta < 0 and mechanism_closed else "REJECT"
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
        "baseline_fill_cycles": int(baseline["fill_cycles"]),
        "candidate_fill_cycles": int(candidate["fill_cycles"]),
        "fill_cycle_delta": int(candidate["fill_cycles"])
        - int(baseline["fill_cycles"]),
        "baseline_request_cycles": int(baseline["request_cycles"]),
        "candidate_request_cycles": int(candidate["request_cycles"]),
        "request_cycle_delta": int(candidate["request_cycles"])
        - int(baseline["request_cycles"]),
        "baseline_index_line_reads": int(baseline["index_line_reads"]),
        "candidate_index_line_reads": int(candidate["index_line_reads"]),
        "mechanism_closed": mechanism_closed,
        "a_result_payload_bytes": int(candidate["a_result_payload_bytes"]),
        "auxiliary_operand_payload_bytes": int(
            candidate["auxiliary_operand_payload_bytes"]
        ),
        "transient_write_transport_payload_bytes": int(
            candidate["transient_write_transport_payload_bytes"]
        ),
        "physical_3p2ghz_realizability": "unclaimed",
        "dual_response_lookup": "unbanked_associative_scan",
        "architecture_promotion_authorized": False,
        "exact_output": "PASS",
        "terminal_ledgers": "PASS",
        "write_response_ledgers": "PASS",
    }


def main() -> int:
    args = parse_args()
    plan = {
        "schema": "dx100.gzp_shared_index_gate.v4",
        "n": args.n,
        "candidate": "shared_index",
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
        "parallel_restores": args.parallel_restores,
        "trace_flags": ["MAAVirtualTrace", "MAATrace"],
        "full_gzp_requested": args.n == 1_000_000,
        "full_gzp_authorized": False,
        "physical_3p2ghz_realizability": "unclaimed",
        "dual_response_lookup": "unbanked_associative_scan",
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
    promotion_evidence = validate_one_window_manifest(args)
    plan["full_gzp_authorized"] = bool(promotion_evidence)

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
            f"{args.n} {selector}",
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
        jobs: list[dict[str, object]] = []
        for replica in range(1, args.replicas + 1):
            for name, payload in ARMS:
                run_name = name if args.replicas == 1 else f"{name}_r{replica}"
                run = args.out / "runs" / run_name
                run.mkdir(parents=True)
                frozen_selector = run / "frozen_treatment.txt"
                common.atomic_text(frozen_selector, payload + "\n")
                frozen_selector.chmod(0o444)
                selector_hash = common.sha256(frozen_selector)
                command = matrix.restore_command(
                    args.gem5.resolve(),
                    frozen_config,
                    run / "gem5",
                    checkpoint,
                    guest,
                    f"{args.n} {selector}",
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
                jobs.append(
                    {
                        "arm": name,
                        "payload": payload,
                        "replica": replica,
                        "run_name": run_name,
                        "run": run,
                        "frozen_selector": frozen_selector,
                        "selector_sha256": selector_hash,
                        "command": command,
                    }
                )

        if matrix.tree_identity(checkpoint) != checkpoint_identity:
            raise RuntimeError("shared checkpoint changed before restores")
        bwrap = shutil.which("bwrap")
        if args.parallel_restores and bwrap is None:
            raise RuntimeError(
                "parallel restores require bwrap selector isolation"
            )

        def execute_restore(job: dict[str, object]) -> dict[str, object]:
            run = job["run"]
            command = job["command"]
            if not isinstance(run, Path) or not isinstance(command, list):
                raise RuntimeError("invalid restore job")
            if args.parallel_restores:
                frozen_selector = job["frozen_selector"]
                if not isinstance(frozen_selector, Path):
                    raise RuntimeError("invalid frozen selector")
                command = [
                    str(bwrap),
                    "--die-with-parent",
                    "--bind",
                    "/",
                    "/",
                    "--ro-bind",
                    str(frozen_selector.resolve()),
                    str(selector.resolve()),
                    "--",
                    *command,
                ]
            else:
                common.atomic_text(selector, str(job["payload"]) + "\n")
                if common.sha256(selector) != job["selector_sha256"]:
                    raise RuntimeError(
                        f"{job['run_name']}: shared selector setup failed"
                    )
            if common.run_logged(command, run / "restore.log", env):
                raise RuntimeError(f"{job['run_name']}: restore failed")
            frozen_selector = job["frozen_selector"]
            if (
                not isinstance(frozen_selector, Path)
                or common.sha256(frozen_selector) != job["selector_sha256"]
            ):
                raise RuntimeError(f"{job['run_name']}: selector changed")
            if (
                not args.parallel_restores
                and common.sha256(selector) != job["selector_sha256"]
            ):
                raise RuntimeError(
                    f"{job['run_name']}: shared selector changed during restore"
                )
            return {
                "arm": job["arm"],
                "replica": job["replica"],
                "selector": job["payload"],
                "selector_sha256": job["selector_sha256"],
                "command_sha256": common.sha256(run / "restore.command.json"),
            }

        if args.parallel_restores:
            with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
                run_records = list(executor.map(execute_restore, jobs))
        else:
            run_records = [execute_restore(job) for job in jobs]
        if matrix.tree_identity(checkpoint) != checkpoint_identity:
            raise RuntimeError("shared checkpoint changed during restores")

        rows: list[dict[str, int | str]] = []
        for job in jobs:
            run = job["run"]
            if not isinstance(run, Path):
                raise RuntimeError("invalid completed restore job")
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
                        f"{job['run_name']}: missing {required_config}"
                    )
            row = analyze_run(str(job["arm"]), run, args.n)
            row["replica"] = int(job["replica"])
            rows.append(row)
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
            "one_window_promotion_evidence": promotion_evidence,
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
    print("GZP_SHARED_INDEX_EXACT_GATE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
