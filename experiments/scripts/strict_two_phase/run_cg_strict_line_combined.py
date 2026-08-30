#!/usr/bin/env python3
"""Run one strict P-result feeder/retirement arm from an accepted pair."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from experiments.scripts.strict_two_phase import (
    run_cg_fused_p16_q16_strict as gate,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def verify_matched_root(root: Path) -> dict:
    result_path = root / "result.json"
    ledger = root / "raw_root.sha256"
    complete = root / "gate.complete"
    require(
        result_path.is_file() and ledger.is_file() and complete.is_file(),
        "matched root is incomplete",
    )
    result = json.loads(result_path.read_text())
    cg_na = result.get("cg_na")
    expected_windows = gate.EXPECTED_WINDOWS.get(cg_na, 0)
    require(
        result.get("schema") == "dx100.cg.strict_p16_q16.v1"
        and result.get("terminal") is True
        and result.get("decision") == "VALID_STRICT_REFERENCE"
        and result.get("producer") == "page-fed"
        and expected_windows != 0
        and result.get("whole_windows") == expected_windows
        and result.get("native_runs") == 0,
        "root is not the accepted non-fused matched strict pair",
    )
    gate_text = complete.read_text().splitlines()
    require(
        gate_text.count("COMPLETE_CG_STRICT_P16_Q16") == 1
        and gate_text.count("decision=VALID_STRICT_REFERENCE") == 1
        and gate_text.count("correctness=EXACT_MATCH") == 1,
        "matched gate does not bind exact correctness",
    )
    for number, line in enumerate(ledger.read_text().splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        require(match is not None, f"bad raw ledger line {number}")
        artifact = root / match.group(2)  # type: ignore[union-attr]
        require(
            artifact.is_file()
            and gate.base.sha256_file(artifact) == match.group(1),  # type: ignore[union-attr]
            f"raw artifact changed: {artifact}",
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matched_root", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument(
        "--gem5", type=Path, default=gate.ROOT / "build/X86/gem5.opt"
    )
    parser.add_argument(
        "--index-buffer-lines",
        type=int,
        choices=(1, 2, 4, 8, 16, 32, 64, 128),
        default=1,
        help=(
            "bounded direct-index feeder depth; one line preserves the "
            "reference"
        ),
    )
    parser.add_argument(
        "--index-issue-lines-per-cycle",
        type=int,
        choices=(1, 2, 4),
        default=1,
        help="finite direct-index request-generation width",
    )
    parser.add_argument(
        "--combine-slots",
        type=int,
        choices=(16, 32, 64, 128, 256, 512),
        default=16,
        help="bounded destination-combiner cache-line slots",
    )
    parser.add_argument(
        "--combine-words",
        type=int,
        default=0,
        help=(
            "shared destination payload words; zero derives full payload "
            "from line slots"
        ),
    )
    parser.add_argument(
        "--payload-words-per-cycle",
        type=int,
        choices=(0, 1, 2, 4, 8),
        default=0,
        help=(
            "complete-line payload words read per MAA cycle; CG words are "
            "four bytes"
        ),
    )
    parser.add_argument(
        "--stage-partial-payload",
        action="store_true",
        help="apply the finite payload port to masked partial lines",
    )
    parser.add_argument(
        "--classify-existing",
        action="store_true",
        help="classify an already completed output without rerunning gem5",
    )
    parser.add_argument(
        "--dense-write-allocate",
        action="store_true",
        help="use no-read first writes for dense virtual backing lines",
    )
    parser.add_argument(
        "--word-writes",
        action="store_true",
        help="retain baseline 4-byte P retirement instead of masked lines",
    )
    args = parser.parse_args(argv)
    matched = args.matched_root.resolve()
    out = args.out.resolve()
    result = verify_matched_root(matched)
    require(
        out.is_dir() if args.classify_existing else not out.exists(),
        (
            f"existing output is missing: {out}"
            if args.classify_existing
            else f"output exists: {out}"
        ),
    )
    require(
        0 <= args.combine_words <= 4096,
        "combiner payload words must be in [0,4096]",
    )
    require(
        not args.word_writes or args.payload_words_per_cycle == 0,
        "payload staging applies only to masked line retirement",
    )
    require(
        not args.stage_partial_payload
        or (args.payload_words_per_cycle != 0 and not args.word_writes),
        "partial payload staging requires finite masked-line staging",
    )
    require(
        len(gate.base.source_status().splitlines()) == 1, "source is dirty"
    )
    gem5 = args.gem5.resolve()
    guest = matched / "cg_strict_fused_p16_q16_guest"
    selector = matched / "input/p16_q16.selector"
    checkpoint = matched / "checkpoint"
    require(
        gem5.is_file()
        and guest.is_file()
        and selector.read_text()
        == "token_stream_ld page_fed_product_soa_jit\n"
        and checkpoint.is_dir(),
        "matched execution inputs are missing",
    )
    cg_na = int(result["cg_na"])
    expected_windows = gate.EXPECTED_WINDOWS[cg_na]
    gate.fused.ACTIVE_CG_NA = cg_na
    if not args.classify_existing:
        out.mkdir(parents=True)
    command = gate.strict_restore_args(
        gem5, guest, selector, checkpoint, out, strict=True
    )
    if not args.word_writes:
        command.append("--maa_virtual_masked_writes")
    command.append(
        f"--maa_virtual_index_buffer_lines={args.index_buffer_lines}"
    )
    command.append(
        "--maa_virtual_index_issue_lines_per_cycle="
        f"{args.index_issue_lines_per_cycle}"
    )
    command.append(f"--maa_virtual_combine_slots={args.combine_slots}")
    if args.combine_words != 0:
        command.append(f"--maa_virtual_combine_words={args.combine_words}")
    command.append(
        "--maa_virtual_complete_line_payload_words_per_cycle="
        f"{args.payload_words_per_cycle}"
    )
    if args.stage_partial_payload:
        command.append("--maa_virtual_complete_line_payload_stage_partial")
    if args.dense_write_allocate:
        command.append("--maa_virtual_dense_write_allocate")
    command_json = json.dumps(command, indent=2) + "\n"
    if args.classify_existing:
        require(
            (out / "command.json").read_text() == command_json,
            "existing command does not match requested treatment",
        )
    else:
        (out / "command.json").write_text(command_json)
    environment = dict(
        os.environ,
        LD_LIBRARY_PATH=str(gate.fused.RAMULATOR.parent)
        + ":"
        + os.environ.get("LD_LIBRARY_PATH", ""),
        OMP_NUM_THREADS="4",
        OMP_PROC_BIND="false",
    )
    if not args.classify_existing:
        gate.base.run_logged(command, out / "restore.log", environment)
    lines = (out / "restore.log").read_text(errors="replace").splitlines()
    require(
        not any(gate.base.FATAL_RE.search(line) for line in lines),
        "line-combined restore has fatal text",
    )
    gate.base.exactly_one(
        lines,
        r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$",
        "line-combined m5 exit",
    )
    fingerprint = gate.base.exactly_one(
        lines,
        rf"^CG_FINGERPRINT mode=MAA elements={cg_na} .* result=PASS$",
        "line-combined fingerprint",
    )
    terminal_line = gate.base.exactly_one(
        lines,
        r"^CG_LOGICAL16_RMW_TERMINAL treatment=page_fed_product_soa_jit .* result=PASS$",
        "line-combined terminal",
    )
    reference_lines = (matched / "strict/restore.log").read_text().splitlines()
    reference_fingerprint = gate.base.exactly_one(
        reference_lines,
        rf"^CG_FINGERPRINT mode=MAA elements={cg_na} .* result=PASS$",
        "matched fingerprint",
    )
    reference_terminal = gate.base.exactly_one(
        reference_lines,
        r"^CG_LOGICAL16_RMW_TERMINAL treatment=page_fed_product_soa_jit .* result=PASS$",
        "matched terminal",
    )
    reductions = [
        line
        for line in lines
        if line.startswith(
            ("CG_REDUCTION_EVIDENCE ", "CG_OUTER_REDUCTION_EVIDENCE ")
        )
    ]
    reference_reductions = [
        line
        for line in reference_lines
        if line.startswith(
            ("CG_REDUCTION_EVIDENCE ", "CG_OUTER_REDUCTION_EVIDENCE ")
        )
    ]
    require(
        fingerprint == reference_fingerprint
        and terminal_line == reference_terminal
        and reductions == reference_reductions
        and len(reductions) == 11,
        "line combining changed CG output or semantic work",
    )
    terminal = gate.fused.parse_kv(terminal_line)
    windows = gate.fused.require_terminal(terminal, "page_fed_product_soa_jit")
    require(
        windows == expected_windows,
        "line-combined arm did not close every matched window",
    )
    gate.fused.require_config(out / "config.ini", args.combine_slots)
    config = (out / "config.ini").read_text().splitlines()
    expected_masked = (
        "virtual_masked_writes=false"
        if args.word_writes
        else "virtual_masked_writes=true"
    )
    require(
        "virtual_strict_two_phase=true" in config
        and expected_masked in config
        and f"virtual_index_buffer_lines={args.index_buffer_lines}" in config
        and (
            "virtual_index_issue_lines_per_cycle="
            f"{args.index_issue_lines_per_cycle}"
        )
        in config
        and (
            "virtual_complete_line_payload_stage_partial="
            f"{str(args.stage_partial_payload).lower()}"
        )
        in config
        and f"virtual_combine_slots={args.combine_slots}" in config
        and f"virtual_combine_words={args.combine_words}" in config
        and (
            "virtual_complete_line_payload_words_per_cycle="
            f"{args.payload_words_per_cycle}"
        )
        in config
        and (
            "virtual_dense_write_allocate="
            f"{str(args.dense_write_allocate).lower()}"
        )
        in config,
        "strict feeder/retirement treatment did not resolve",
    )
    stats = gate.fused.require_stats(
        out / "stats.txt", windows, "page_fed_product_soa_jit"
    )
    stats.update(
        {
            name: gate.base.stat_sum(out / "stats.txt", name)
            for name in gate.STRICT_STATS
        }
    )
    issue_stats = {
        name: gate.base.stat_sum(out / "stats.txt", name)
        for name in (
            "IND_VirtIndexLineReads",
            "IND_VirtIndexIssueCycles",
            "IND_VirtIndexIssueWidthStalls",
            "IND_VirtIndexIssuePeak",
        )
    }
    direct_instructions = stats["IND_SoaJitInstructions"]
    require(
        issue_stats["IND_VirtIndexLineReads"] > 0
        and issue_stats["IND_VirtIndexIssueCycles"]
        >= (
            issue_stats["IND_VirtIndexLineReads"]
            + args.index_issue_lines_per_cycle
            - 1
        )
        // args.index_issue_lines_per_cycle
        and issue_stats["IND_VirtIndexIssueCycles"]
        <= issue_stats["IND_VirtIndexLineReads"]
        and issue_stats["IND_VirtIndexIssueWidthStalls"] > 0
        and 0
        < issue_stats["IND_VirtIndexIssuePeak"]
        <= direct_instructions * args.index_issue_lines_per_cycle,
        "finite direct-index issue-width counters are inconsistent",
    )
    stats.update(issue_stats)
    payload_stats = {
        name: gate.base.stat_sum(out / "stats.txt", name)
        for name in (
            "IND_VirtFullLineWrites",
            "IND_VirtPartialWrites",
            "IND_VirtCompleteLinePayloadStarts",
            "IND_VirtCompleteLinePayloadCompletions",
            "IND_VirtCompleteLinePayloadReadCycles",
            "IND_VirtCompleteLinePayloadBlockedCycles",
            "IND_VirtCompleteLinePayloadBackpressureCycles",
        )
    }
    if args.payload_words_per_cycle == 0:
        require(
            all(
                value == 0
                for name, value in payload_stats.items()
                if name
                not in ("IND_VirtFullLineWrites", "IND_VirtPartialWrites")
            ),
            "disabled CG payload staging recorded work",
        )
    else:
        full_lines = payload_stats["IND_VirtFullLineWrites"]
        expected_lines = full_lines
        if args.stage_partial_payload:
            expected_lines += payload_stats["IND_VirtPartialWrites"]
        require(
            expected_lines > 0
            and payload_stats["IND_VirtCompleteLinePayloadStarts"]
            == expected_lines
            and payload_stats["IND_VirtCompleteLinePayloadCompletions"]
            == expected_lines
            and payload_stats["IND_VirtCompleteLinePayloadReadCycles"] > 0,
            "finite CG payload staging did not close exactly",
        )
    stats.update(payload_stats)
    dense_initializations = gate.base.stat_sum(
        out / "stats.txt", "IND_VirtDenseInitializationWrites"
    )
    expected_dense_initializations = (
        expected_windows * 1024 if args.dense_write_allocate else 0
    )
    require(
        dense_initializations == expected_dense_initializations,
        "dense backing initialization count changed",
    )
    stats["IND_VirtDenseInitializationWrites"] = dense_initializations
    trace = out / "strict_trace.log"
    p_timing = gate.event_records(trace, "strict_two_phase_timing")
    q_timing = gate.event_records(trace, "strict_page_fed_two_phase_timing")
    whole = gate.event_records(trace, "strict_cg_p16_q16_window")
    products = gate.event_records(trace, "strict_product_page_response")
    writes = gate.event_records(trace, "backing_write_issue")
    require(
        (len(p_timing), len(q_timing), len(whole), len(products))
        == (
            expected_windows,
            expected_windows,
            expected_windows,
            4 * expected_windows,
        ),
        "line-combined strict trace is incomplete",
    )
    for row in p_timing:
        gate.validate_timing(row, page_fed=False)
    for row in q_timing:
        gate.validate_timing(row, page_fed=True)
    expected_write_bytes = 4 if args.word_writes else 64
    require(
        writes
        and all(
            gate.integer(row, "bytes") == expected_write_bytes
            for row in writes
        )
        and len(writes)
        == sum(gate.integer(row, "backing_issues") for row in p_timing),
        "P retirement write size or trace accounting changed",
    )
    if args.word_writes:
        require(
            len(writes) == expected_windows * 16384,
            "word retirement did not issue one write per logical P word",
        )
    else:
        require(
            len(writes) < expected_windows * 16384,
            "P retirement was not converted to combined lines",
        )
    matched_ticks = int(result["strict_reference_simTicks"])
    combined_ticks = stats["simTicks"]
    decision = (
        "VALID_STRICT_FEEDER_ATTRIBUTION"
        if args.word_writes
        else "VALID_LINE_COMBINED_ATTRIBUTION"
    )
    combined = {
        "schema": (
            "dx100.cg.strict_p16_q16.feeder.v1"
            if args.word_writes
            else "dx100.cg.strict_p16_q16.line_combined.v1"
        ),
        "terminal": True,
        "decision": decision,
        "promotable": False,
        "cg_na": cg_na,
        "matched_root": str(matched),
        "source_commit": gate.base.source_commit(),
        "gem5_sha256": gate.base.sha256_file(gem5),
        "guest_sha256": gate.base.sha256_file(guest),
        "native_runs": 0,
        "whole_windows": expected_windows,
        "fingerprints_exact_equal": True,
        "deterministic_reductions_exact_equal": True,
        "p_backing_write_issues": len(writes),
        "p_backing_write_bytes": expected_write_bytes,
        "all_p_writes_64_bytes": not args.word_writes,
        "retirement_mode": "word" if args.word_writes else "masked_line",
        "virtual_index_buffer_lines": args.index_buffer_lines,
        "virtual_index_issue_lines_per_cycle": (
            args.index_issue_lines_per_cycle
        ),
        "virtual_combine_slots": args.combine_slots,
        "virtual_combine_words": args.combine_words,
        "virtual_complete_line_payload_words_per_cycle": (
            args.payload_words_per_cycle
        ),
        "virtual_complete_line_payload_stage_partial": (
            args.stage_partial_payload
        ),
        "virtual_dense_write_allocate": args.dense_write_allocate,
        "matched_strict_simTicks": matched_ticks,
        "line_combined_simTicks": combined_ticks,
        "matched_over_line_combined": matched_ticks / combined_ticks,
        "strict_stats": stats,
    }
    (out / "result.json").write_text(json.dumps(combined, indent=2) + "\n")
    (out / "gate.complete").write_text(
        (
            "COMPLETE_CG_STRICT_FEEDER\n"
            if args.word_writes
            else "COMPLETE_CG_STRICT_LINE_COMBINED\n"
        )
        + f"decision={decision}\n"
        "correctness=EXACT_MATCH\n"
    )
    print(json.dumps(combined, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
