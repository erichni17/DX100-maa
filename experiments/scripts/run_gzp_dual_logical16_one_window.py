#!/usr/bin/env python3
"""Run the exact two-repetition shared-checkpoint GZP split-2K gate."""

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
    ("control_dual_logical16", "token_stream_ld dual_logical16"),
    ("treatment_split2k", "token_stream_ld dual_logical16_split2k"),
)
REPETITIONS = 2
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


def trace_events(
    trace_text: str, event: str
) -> list[tuple[int, dict[str, str]]]:
    """Return ordered MAATrace records, retaining their simulated tick."""
    records: list[tuple[int, dict[str, str]]] = []
    for line in trace_text.splitlines():
        if f"event={event} " not in line:
            continue
        match = re.match(r"\s*(\d+):", line)
        if match is None:
            raise RuntimeError(f"trace event lacks tick: {line}")
        records.append((int(match.group(1)), common.parse_fields(line)))
    return records


def require_trace_count(
    name: str, trace_text: str, event: str, expected: int
) -> list[tuple[int, dict[str, str]]]:
    records = trace_events(trace_text, event)
    if len(records) != expected:
        raise RuntimeError(
            f"{name}: {event} trace count {len(records)} != {expected}"
        )
    return records


def verify_split_dependency_timeline(
    name: str, trace_text: str
) -> dict[str, int]:
    """Prove fill-half1 occurs between half0 issue and its WriteResp fence."""
    issues = require_trace_count(
        name, trace_text, "spd_publish_issue", EXPECTED_PUBLISH_LINES
    )
    terminals = require_trace_count(
        name, trace_text, "spd_publish_terminal", 8
    )
    finishes = require_trace_count(name, trace_text, "split2k_alu_finish", 8)
    reserves = require_trace_count(
        name, trace_text, "split2k_owner_reserve", 8
    )
    releases = require_trace_count(
        name, trace_text, "split2k_owner_release", 8
    )

    if any(
        entry.get("split_2k") != "1"
        or entry.get("source_elements") != "2048"
        or entry.get("source_first") not in {"0", "2048"}
        for _, entry in issues + terminals
    ):
        raise RuntimeError(f"{name}: split publisher emitted a non-2K line")
    if any(
        entry.get("elements") != "2048"
        or entry.get("first") not in {"0", "2048"}
        for _, entry in finishes
    ):
        raise RuntimeError(f"{name}: split producer emitted a non-2K range")
    if any(
        entry.get("elements") != "2048" or entry.get("half") not in {"0", "1"}
        for _, entry in reserves
    ) or any(entry.get("write_resp") != "1" for _, entry in releases):
        raise RuntimeError(
            f"{name}: split source-owner lifetime contract failed"
        )

    first_half_issues = [
        tick for tick, entry in issues if entry.get("source_first") == "0"
    ]
    first_half_terminals = [
        tick for tick, entry in terminals if entry.get("source_first") == "0"
    ]
    second_half_finishes = [
        tick for tick, entry in finishes if entry.get("first") == "2048"
    ]
    overlap_witnesses = [
        finish_tick
        for finish_tick in second_half_finishes
        if any(issue_tick < finish_tick for issue_tick in first_half_issues)
        and any(
            finish_tick < terminal_tick
            for terminal_tick in first_half_terminals
        )
    ]
    if not overlap_witnesses:
        raise RuntimeError(
            f"{name}: no MAATrace witness of half1 fill while half0 WriteResp "
            "publication remained outstanding"
        )
    release_witnesses = [
        release_tick
        for release_tick, release in releases
        if any(
            terminal_tick <= release_tick
            and terminal.get("source") == release.get("source")
            and terminal.get("source_first")
            == str(int(release["half"]) * 2048)
            for terminal_tick, terminal in terminals
        )
    ]
    if len(release_witnesses) != 8:
        raise RuntimeError(
            f"{name}: source ownership released before WriteResp"
        )
    issue_overlap = sum(entry.get("overlap") == "1" for _, entry in issues)
    if issue_overlap == 0:
        raise RuntimeError(
            f"{name}: no publisher issue overlapped ALU activity"
        )
    return {
        "overlap_issue_tick": min(
            tick for tick, entry in issues if entry.get("overlap") == "1"
        ),
        "overlap_half1_finish_tick": min(overlap_witnesses),
        "half0_write_resp_terminal_tick": min(
            tick
            for tick in first_half_terminals
            if tick > min(overlap_witnesses)
        ),
        "owner_release_after_write_resp_count": len(release_witnesses),
    }


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

    # Both final arms are exactly the optimized masked-index dual RMW.
    expected_instructions = 2
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
        raise RuntimeError(f"{name}: missing MAATrace/MAAVirtualTrace log")
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
        raise RuntimeError(f"{name}: MAAVirtualTrace terminal ledger failed")

    split = name == "treatment_split2k"
    expected_treatment = (
        "dual_logical16_split2k_soa_jit" if split else "dual_logical16_soa_jit"
    )
    expected_publisher = (
        "response_bearing_gradient_split2k"
        if split
        else "response_bearing_gradient_only"
    )
    expected_terminals = 8 if split else 4
    required_terminal = {
        "treatment": expected_treatment,
        "masked_index_windows": "0",
        "dual_logical16_windows": "0" if split else "1",
        "dual_logical16_split2k_windows": "1" if split else "0",
        "published_predicates": "0",
        "published_gradient_values": str(N),
        "published_gradient_bytes": str(N * 4),
        "publisher": expected_publisher,
        "predicate_publications": "0",
        "predicate_publication_bytes": "0",
        "producer_staging_elements": "4096",
        "producer_staging_bytes": "16384",
        "producer_owner_regions": "2" if split else "1",
        "producer_owner_region_elements": "2048" if split else "4096",
        "split_owner_slots": "2",
        "split_owner_state_bytes": "8",
        "split_additional_spd_ports": "0",
        "split_additional_stream_ports": "0",
        "split_additional_alu_ports": "0",
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
    if (
        publish["STR_PublishIssues"] != EXPECTED_PUBLISH_LINES
        or publish["STR_PublishAccepts"] != EXPECTED_PUBLISH_LINES
        or publish["STR_PublishWriteResponses"] != EXPECTED_PUBLISH_LINES
        or publish["STR_PublishTerminals"] != expected_terminals
        or optional_max(stats, "STR_PublishCreditHWM") != 8
    ):
        raise RuntimeError(f"{name}: publisher WriteResp ledger failed")
    for event, expected in (
        ("issue", EXPECTED_PUBLISH_LINES),
        ("accept", EXPECTED_PUBLISH_LINES),
        ("response", EXPECTED_PUBLISH_LINES),
        ("terminal", expected_terminals),
    ):
        require_trace_count(name, trace_text, f"spd_publish_{event}", expected)

    timeline: dict[str, int] = {}
    if split:
        timeline = verify_split_dependency_timeline(name, trace_text)
    elif trace_events(trace_text, "split2k_alu_finish") or trace_events(
        trace_text, "split2k_owner_reserve"
    ):
        raise RuntimeError(
            f"{name}: default control unexpectedly enabled split mode"
        )

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
        "publish_lines": publish["STR_PublishIssues"],
        "publish_responses": publish["STR_PublishWriteResponses"],
        "publish_terminals": publish["STR_PublishTerminals"],
        "publish_retries": publish["STR_PublishRetries"],
        "publish_credit_stalls": publish["STR_PublishCreditStalls"],
        "publish_overlap_issues": publish["STR_PublishOverlapIssues"],
        "published_gradient_bytes": N * 4,
        **timeline,
    }


def compare(
    rows: list[dict[str, int | str]], repetition: int
) -> dict[str, int | float | str]:
    if [row["arm"] for row in rows] != [name for name, _ in ARMS]:
        raise RuntimeError(
            f"replica {repetition}: invalid control/treatment order"
        )
    control, treatment = rows
    for key in ("output_hash", "index_hash"):
        if control[key] != treatment[key]:
            raise RuntimeError(f"replica {repetition}: pair mismatch: {key}")
    tick_delta = int(treatment["simTicks"]) - int(control["simTicks"])
    decision = "ACCEPT" if tick_delta < 0 else "REJECT"
    return {
        "replicate": repetition,
        "decision": decision,
        "control_simTicks": int(control["simTicks"]),
        "treatment_simTicks": int(treatment["simTicks"]),
        "treatment_minus_control_ticks": tick_delta,
        "control_over_treatment_speedup": int(control["simTicks"])
        / int(treatment["simTicks"]),
        "indirect_instruction_delta": int(treatment["indirect_instructions"])
        - int(control["indirect_instructions"]),
        "stream_instruction_delta": int(treatment["stream_instructions"])
        - int(control["stream_instructions"]),
        "publisher_serialization_observed": int(
            treatment["publish_credit_stalls"]
        )
        > 0,
        "publisher_overlap_issues": int(treatment["publish_overlap_issues"]),
        "strict_overlap_proven": "PASS",
        "exact_output": "PASS",
        "terminal_ledgers": "PASS",
        "write_response_ledgers": "PASS",
    }


def main() -> int:
    args = parse_args()
    plan = {
        "schema": "dx100.gzp_split2k_one_window.v1",
        "n": N,
        "repetitions": REPETITIONS,
        "arms": [name for name, _ in ARMS],
        "shared_guest": True,
        "shared_checkpoint": True,
        "only_treatment": "strict split-2K producer ownership",
        "logical_elements": 16384,
        "physical_spd_elements": 4096,
        "producer_owner_regions": 2,
        "producer_owner_region_elements": 2048,
        "optimized_hybrid": {
            "soa_jit_active_contexts": 32,
            "soa_jit_active_value_owners": 64,
            "soa_jit_pre_a_value_lookahead": True,
            "masked_indices": True,
        },
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
        guest = inputs / "gradzatp_split2k"
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
            "--maa_soa_jit_active_contexts=32",
            "--maa_soa_jit_value_lookahead=8",
            "--maa_soa_jit_value_cache_enable",
            "--maa_soa_jit_predicate_active_credits=16",
            "--maa_soa_jit_active_value_owners=64",
            "--maa_soa_jit_apply_lanes=1",
            "--maa_soa_jit_pre_a_value_lookahead",
        ]
        rows: list[dict[str, int | str]] = []
        comparisons: list[dict[str, int | float | str]] = []
        run_records: list[dict[str, str | int]] = []
        for repetition in range(REPETITIONS):
            pair_rows: list[dict[str, int | str]] = []
            for name, payload in ARMS:
                run = args.out / "runs" / f"replica_{repetition}" / name
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
                    raise RuntimeError(
                        f"replica {repetition} {name}: restore failed"
                    )
                if common.sha256(selector) != selector_hash:
                    raise RuntimeError(
                        f"replica {repetition} {name}: selector changed"
                    )
                if matrix.tree_identity(checkpoint) != checkpoint_identity:
                    raise RuntimeError(
                        "shared checkpoint changed during restore"
                    )
                config = (run / "gem5/config.ini").read_text()
                for required_config in (
                    "num_tile_elements=16384",
                    "physical_tile_elements=4096",
                    "soa_jit_active_contexts=32",
                    "soa_jit_active_value_owners=64",
                    "soa_jit_pre_a_value_lookahead=true",
                ):
                    if required_config not in config:
                        raise RuntimeError(
                            f"{name}: missing {required_config}"
                        )
                row = analyze_run(name, run)
                row["replicate"] = repetition
                rows.append(row)
                pair_rows.append(row)
                run_records.append(
                    {
                        "replicate": repetition,
                        "arm": name,
                        "selector": payload,
                        "selector_sha256": selector_hash,
                        "command_sha256": common.sha256(
                            run / "restore.command.json"
                        ),
                    }
                )
            comparisons.append(compare(pair_rows, repetition))
        overall_decision = (
            "ACCEPT"
            if all(summary["decision"] == "ACCEPT" for summary in comparisons)
            else "REJECT"
        )
        summary: dict[str, str | int | list[dict[str, int | float | str]]] = {
            "decision": overall_decision,
            "repetitions": REPETITIONS,
            "same_checkpoint": "PASS",
            "optimized_hybrid_fixed": "PASS",
            "replica_summaries": comparisons,
        }
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
            # The treatment alone carries the dependency-timeline witness.
            # Preserve that evidence in the TSV without treating it as an
            # accidental schema mismatch with the fixed control arm.
            fieldnames = list(
                dict.fromkeys(key for row in rows for key in row)
            )
            writer = csv.DictWriter(
                output, fieldnames=fieldnames, delimiter="\t"
            )
            writer.writeheader()
            writer.writerows(rows)
        common.atomic_text(
            args.out / "summary.txt",
            "".join(
                f"{key}={json.dumps(value, sort_keys=True) if isinstance(value, list) else value}\n"
                for key, value in summary.items()
            ),
        )
        if overall_decision != "ACCEPT":
            raise RuntimeError(
                "one or more exact replica gates did not improve simTicks"
            )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        common.atomic_text(args.out / "campaign.exit", "1\n")
        print(f"error: {error}", file=sys.stderr)
        return 1
    common.atomic_text(args.out / "campaign.exit", "0\n")
    print((args.out / "results.tsv").read_text(), end="")
    print((args.out / "summary.txt").read_text(), end="")
    print("GZP_SPLIT2K_ONE_WINDOW_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
