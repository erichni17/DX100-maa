#!/usr/bin/env python3
"""Run one strict P-result cache-line-combined arm from an accepted pair."""

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
    args = parser.parse_args(argv)
    matched = args.matched_root.resolve()
    out = args.out.resolve()
    result = verify_matched_root(matched)
    require(not out.exists(), f"output exists: {out}")
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
    out.mkdir(parents=True)
    command = gate.strict_restore_args(
        gem5, guest, selector, checkpoint, out, strict=True
    )
    command.append("--maa_virtual_masked_writes")
    (out / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    environment = dict(
        os.environ,
        LD_LIBRARY_PATH=str(gate.fused.RAMULATOR.parent)
        + ":"
        + os.environ.get("LD_LIBRARY_PATH", ""),
        OMP_NUM_THREADS="4",
        OMP_PROC_BIND="false",
    )
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
    gate.fused.require_config(out / "config.ini")
    config = (out / "config.ini").read_text().splitlines()
    require(
        "virtual_strict_two_phase=true" in config
        and "virtual_masked_writes=true" in config,
        "line-combined treatment did not resolve",
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
    require(
        writes
        and all(gate.integer(row, "bytes") == 64 for row in writes)
        and len(writes)
        == sum(gate.integer(row, "backing_issues") for row in p_timing)
        and len(writes) < expected_windows * 16384,
        "P retirement was not converted from word writes to combined lines",
    )
    matched_ticks = int(result["strict_reference_simTicks"])
    combined_ticks = stats["simTicks"]
    combined = {
        "schema": "dx100.cg.strict_p16_q16.line_combined.v1",
        "terminal": True,
        "decision": "VALID_LINE_COMBINED_ATTRIBUTION",
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
        "all_p_writes_64_bytes": True,
        "matched_strict_simTicks": matched_ticks,
        "line_combined_simTicks": combined_ticks,
        "matched_over_line_combined": matched_ticks / combined_ticks,
        "strict_stats": stats,
    }
    (out / "result.json").write_text(json.dumps(combined, indent=2) + "\n")
    (out / "gate.complete").write_text(
        "COMPLETE_CG_STRICT_LINE_COMBINED\n"
        "decision=VALID_LINE_COMBINED_ATTRIBUTION\n"
        "correctness=EXACT_MATCH\n"
    )
    print(json.dumps(combined, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
