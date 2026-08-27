#!/usr/bin/env python3
"""Run a matched control/strict non-fused p16 + page-fed-q16 CG gate.

The guest, checkpoint, selector, and all non-treatment simulator knobs are
identical.  The primary/default producer is the simple non-fused virtual p16
gather followed by four response-bearing product pages.  ``--producer=fused``
is retained only as a fusion-matched diagnostic.  The strict arm alone enables
``--maa_virtual_strict_two_phase``.
No native arm is launched: archived native evidence is joined only by the
handoff after this candidate's exact output and whole-window ledgers close.
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

ROOT = Path(__file__).resolve().parents[3]
FUSED_PATH = ROOT / "experiments/scripts/run_cg_fused_p16_product_q16.py"
SPEC = importlib.util.spec_from_file_location("fused_gate", FUSED_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {FUSED_PATH}")
fused = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fused)
base = fused.base

EXPECTED_WINDOWS = {256: 10, 1024: 65}
STRICT_STATS = (
    "IND_StrictTwoPhaseOperations",
    "IND_StrictTwoPhaseBFetchCycles",
    "IND_StrictTwoPhaseRowOffsetCycles",
    "IND_StrictTwoPhaseAIssueCycles",
    "IND_StrictTwoPhaseBackingCycles",
    "IND_StrictTwoPhasePageCycles",
    "IND_StrictTwoPhaseConsumerCycles",
    "IND_StrictTwoPhaseBFetchLines",
    "IND_StrictTwoPhaseDescriptors",
    "IND_StrictTwoPhaseAIssues",
    "IND_StrictTwoPhaseBackingIssues",
    "IND_StrictTwoPhasePagesReady",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def parse_kv(line: str) -> dict[str, str]:
    return dict(token.split("=", 1) for token in line.split() if "=" in token)


def integer(fields: dict[str, str], name: str) -> int:
    try:
        return int(fields[name], 0)
    except (KeyError, ValueError) as error:
        raise RuntimeError(f"missing integer {name}: {fields}") from error


def event_records(trace: Path, event: str) -> list[dict[str, str]]:
    marker = f"event={event} "
    return [
        parse_kv(line)
        for line in trace.read_text(errors="replace").splitlines()
        if marker in line
    ]


def validate_timing(fields: dict[str, str], page_fed: bool) -> None:
    require(
        fields.get("terminal") == "1"
        and fields.get("order_ok") == "1"
        and fields.get("exact_b_once") == "1"
        and fields.get("raw_b_retained_bytes") == "0"
        and fields.get("descriptor_backing_bytes") == "0"
        and fields.get("replay_passes") == "0"
        and fields.get("coherent_ack") == "1",
        f"strict timing flags did not close: {fields}",
    )
    require(
        integer(fields, "b_words") == 16384
        and integer(fields, "descriptors") == 16384
        and integer(fields, "pages_ready") == 4
        and integer(fields, "a_issues") == integer(fields, "a_responses")
        and integer(fields, "backing_issues")
        == integer(fields, "backing_acks"),
        f"strict timing work did not close: {fields}",
    )
    require(
        integer(fields, "A_FIRST_ISSUE")
        >= integer(fields, "ROW_OFFSET_LAST_INSERT"),
        f"A_FIRST_ISSUE < ROW_OFFSET_LAST_INSERT: {fields}",
    )
    if page_fed:
        require(
            integer(fields, "feeder_words") == 4096
            and integer(fields, "result_context_words") <= 4096,
            f"q16 physical capacity escaped 4K: {fields}",
        )
    else:
        require(
            integer(fields, "feeder_words") <= 4096
            and integer(fields, "result_words") <= 4096,
            f"p16 physical capacity escaped 4K: {fields}",
        )


def normalize_config(path: Path) -> str:
    result = []
    for line in fused.normalized_config(path).splitlines():
        if line.startswith("virtual_strict_two_phase="):
            line = "virtual_strict_two_phase=<TREATMENT>"
        result.append(line)
    return "\n".join(result) + "\n"


def compile_guest(guest: Path, cg_na: int) -> list[str]:
    command = [
        os.environ.get("CXX", "g++"),
        f"-I{ROOT / 'benchmarks/API'}",
        f"-I{ROOT / 'include'}",
        f"-I{ROOT / 'util/m5/src'}",
        "-std=c++17",
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
        str(base.SOURCE),
        "-o",
        str(guest),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    return command


def strict_restore_args(
    gem5: Path,
    guest: Path,
    selector: Path,
    checkpoint: Path,
    arm: Path,
    strict: bool,
) -> list[str]:
    fused.GEM5 = gem5
    command = fused.restore_args(guest, selector, checkpoint, arm)
    command[0] = str(gem5)
    command[3:3] = [
        "--debug-flags=MAAVirtualTrace,MAAMacroEvent,MAATrace",
        "--debug-file=strict_trace.log",
    ]
    if strict:
        command.append("--maa_virtual_strict_two_phase")
    return command


def validate_confirmation(path: Path, source_commit: str) -> None:
    result_path = path.resolve() / "result.json"
    require(result_path.is_file(), "NA=1024 requires an accepted NA=256 root")
    result = json.loads(result_path.read_text())
    require(
        result.get("schema") == "dx100.cg.strict_p16_q16.v1"
        and result.get("terminal") is True
        and result.get("decision") == "VALID_STRICT_REFERENCE"
        and result.get("cg_na") == 256
        and result.get("producer") == "page-fed"
        and result.get("scope") == "primary_simple_nonfused_reference"
        and result.get("source_commit") == source_commit
        and result.get("whole_windows") == EXPECTED_WINDOWS[256],
        "NA=256 root does not authorize a bounded NA=1024 confirmation",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument(
        "--gem5", type=Path, default=ROOT / "build/X86/gem5.opt"
    )
    parser.add_argument(
        "--cg-na", type=int, choices=EXPECTED_WINDOWS, default=256
    )
    parser.add_argument(
        "--producer", choices=("page-fed", "fused"), default="page-fed"
    )
    parser.add_argument("--confirm-from", type=Path)
    args = parser.parse_args(argv)
    if args.cg_na == 1024 and args.confirm_from is None:
        parser.error("NA=1024 requires --confirm-from accepted NA=256 root")
    if args.cg_na == 256 and args.confirm_from is not None:
        parser.error("--confirm-from is valid only for NA=1024")

    out = args.out.resolve()
    gem5 = args.gem5.resolve()
    require(
        out != ROOT and ROOT not in out.parents, "output must be outside Git"
    )
    require(
        not out.exists() or not any(out.iterdir()), f"nonempty output: {out}"
    )
    require(
        gem5.is_file() and os.access(gem5, os.X_OK), f"missing gem5: {gem5}"
    )
    before_status = base.source_status()
    require(
        len(before_status.splitlines()) == 1, "source worktree must be clean"
    )
    source_commit = base.source_commit()
    if args.cg_na == 1024:
        validate_confirmation(args.confirm_from, source_commit)
    fused.ACTIVE_CG_NA = args.cg_na
    treatment = (
        "page_fed_product_soa_jit"
        if args.producer == "page-fed"
        else "fused_p16_product_q16"
    )

    out.mkdir(parents=True, exist_ok=True)
    input_dir = out / "input"
    checkpoint = out / "checkpoint"
    input_dir.mkdir()
    checkpoint.mkdir()
    selector = input_dir / "p16_q16.selector"
    selector.write_text(f"token_stream_ld {treatment}\n")
    selector.chmod(0o444)
    guest = out / "cg_strict_fused_p16_q16_guest"
    compile_command = compile_guest(guest, args.cg_na)
    (input_dir / "compile_command.json").write_text(
        json.dumps(compile_command, indent=2) + "\n"
    )

    environment = dict(
        os.environ,
        LD_LIBRARY_PATH=str(fused.RAMULATOR.parent)
        + ":"
        + os.environ.get("LD_LIBRARY_PATH", ""),
        OMP_NUM_THREADS="4",
        OMP_PROC_BIND="false",
    )
    ldd = subprocess.check_output(
        ["ldd", str(gem5)], env=environment, text=True
    )
    ramulator_match = re.search(r"^[ \t]*libramulator\.so => (\S+)", ldd, re.M)
    require(
        ramulator_match is not None
        and Path(ramulator_match.group(1)).resolve()
        == fused.RAMULATOR.resolve(),
        "gem5 did not resolve the frozen Ramulator library",
    )
    immutable = (
        gem5,
        fused.RAMULATOR,
        guest,
        selector,
        Path(__file__).resolve(),
        ROOT / "experiments/scripts/strict_two_phase/"
        "run_cg_page_fed_p16_q16_strict.py",
        *base.GUEST_COMPILE_INPUTS,
        *base.RUNNER_CONFIG_INPUTS[1:],
    )
    artifacts_before = base.artifact_ledger(immutable)
    (input_dir / "artifact_sha256.before").write_text(artifacts_before)
    checkpoint_command = [
        str(gem5),
        "--listener-mode=off",
        f"--outdir={checkpoint}",
        str(base.CONFIG),
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
    base.run_logged(checkpoint_command, out / "checkpoint.log", environment)
    checkpoint_lines = (
        (out / "checkpoint.log").read_text(errors="replace").splitlines()
    )
    base.exactly_one(
        checkpoint_lines,
        r"^Exiting @ tick [0-9]+ because checkpoint$",
        "checkpoint terminal",
    )
    checkpoint_before = base.tree_ledger(checkpoint)
    (input_dir / "checkpoint_files.before").write_text(checkpoint_before)

    parsed: dict[str, dict] = {}
    commands: dict[str, list[str]] = {}
    for name, strict in (("control", False), ("strict", True)):
        arm = out / name
        arm.mkdir()
        command = strict_restore_args(
            gem5, guest, selector, checkpoint, arm, strict
        )
        commands[name] = command
        base.run_logged(command, arm / "restore.log", environment)
        lines = (arm / "restore.log").read_text(errors="replace").splitlines()
        require(
            not any(base.FATAL_RE.search(line) for line in lines),
            f"{name}: fatal",
        )
        base.exactly_one(
            lines,
            r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$",
            f"{name} terminal",
        )
        fingerprint = base.exactly_one(
            lines,
            rf"^CG_FINGERPRINT mode=MAA elements={args.cg_na} .* result=PASS$",
            f"{name} fingerprint",
        )
        terminal_line = base.exactly_one(
            lines,
            rf"^CG_LOGICAL16_RMW_TERMINAL treatment={treatment} .* result=PASS$",
            f"{name} CG terminal",
        )
        reductions = [
            line
            for line in lines
            if line.startswith(
                ("CG_REDUCTION_EVIDENCE ", "CG_OUTER_REDUCTION_EVIDENCE ")
            )
        ]
        require(len(reductions) == 11, f"{name}: incomplete reductions")
        terminal = fused.parse_kv(terminal_line)
        windows = fused.require_terminal(terminal, treatment)
        fused.require_config(arm / "config.ini")
        config_lines = (
            (arm / "config.ini").read_text(errors="replace").splitlines()
        )
        require(
            f"virtual_strict_two_phase={'true' if strict else 'false'}"
            in config_lines,
            f"{name}: strict config did not resolve",
        )
        stats = fused.require_stats(arm / "stats.txt", windows, treatment)
        stats.update(
            {
                name: base.stat_sum(arm / "stats.txt", name)
                for name in STRICT_STATS
            }
        )
        parsed[name] = {
            "fingerprint": fingerprint,
            "terminal_line": terminal_line,
            "terminal": terminal,
            "reductions": reductions,
            "stats": stats,
        }

    control = parsed["control"]
    strict = parsed["strict"]
    require(
        control["fingerprint"] == strict["fingerprint"], "fingerprint changed"
    )
    require(
        control["reductions"] == strict["reductions"], "reductions changed"
    )
    require(
        normalize_config(out / "control/config.ini")
        == normalize_config(out / "strict/config.ini"),
        "non-treatment config changed",
    )
    require(
        all(control["stats"][name] == 0 for name in STRICT_STATS),
        "control unexpectedly activated strict mode",
    )
    windows = EXPECTED_WINDOWS[args.cg_na]
    words = windows * 16384
    require(
        strict["stats"]["IND_StrictTwoPhaseOperations"] == 2 * windows
        and 2 * windows * 1024
        <= strict["stats"]["IND_StrictTwoPhaseBFetchLines"]
        <= windows * (1024 + 1025)
        and strict["stats"]["IND_StrictTwoPhaseDescriptors"] == 2 * words
        and strict["stats"]["IND_StrictTwoPhasePagesReady"] == 8 * windows
        and strict["stats"]["IND_StrictTwoPhaseAIssues"] > 0
        and strict["stats"]["IND_StrictTwoPhaseBackingIssues"] > 0,
        f"strict aggregate stats did not cover p16+q16: {strict['stats']}",
    )

    trace = out / "strict/strict_trace.log"
    p_timing = event_records(trace, "strict_two_phase_timing")
    q_timing = event_records(trace, "strict_page_fed_two_phase_timing")
    whole = event_records(trace, "strict_cg_p16_q16_window")
    fused_terminal = event_records(trace, "fused_p16_product_complete")
    product_terminal = event_records(trace, "spd_publish_terminal")
    product_response = event_records(trace, "strict_product_page_response")
    require(
        len(p_timing) == len(q_timing) == len(whole) == windows
        and len(fused_terminal) == (windows if args.producer == "fused" else 0)
        and len(product_terminal)
        == (0 if args.producer == "fused" else 4 * windows)
        and len(product_response)
        == (0 if args.producer == "fused" else 4 * windows),
        "strict trace does not contain exact p/q/whole/product terminals",
    )
    for fields in p_timing:
        validate_timing(fields, page_fed=False)
    for fields in q_timing:
        validate_timing(fields, page_fed=True)
    p_by_generation = {integer(row, "generation"): row for row in p_timing}
    q_by_generation = {integer(row, "generation"): row for row in q_timing}
    fused_by_generation = {
        integer(row, "generation"): row for row in fused_terminal
    }
    require(
        len(p_by_generation) == len(q_by_generation) == windows,
        "p/q generation reuse in trace",
    )
    if args.producer == "fused":
        require(
            len(fused_by_generation) == windows,
            "fused generation reuse in trace",
        )
    fingerprint_sha = sha256_text(strict["fingerprint"] + "\n")
    terminal_sha = sha256_text(strict["terminal_line"] + "\n")
    reductions_sha = sha256_text("\n".join(strict["reductions"]) + "\n")
    joined = []
    for ordinal, row in enumerate(whole):
        p_generation = integer(row, "p_generation")
        q_generation = integer(row, "q_generation")
        require(
            p_generation in p_by_generation
            and (
                args.producer != "fused" or p_generation in fused_by_generation
            )
            and q_generation in q_by_generation
            and row.get("p_terminal") == "1"
            and row.get("q_terminal") == "1"
            and row.get("p16_reorder") == "1"
            and row.get("q16_reorder") == "1"
            and row.get("direct4") == "0"
            and row.get("p_mode")
            == ("fused" if args.producer == "fused" else "nonfused")
            and row.get("drains") == "0"
            and row.get("fallbacks") == "0"
            and integer(row, "p_product_page_responses") == 4
            and integer(row, "q_product_deliveries") == 16384
            and integer(row, "q_value_read_issues")
            == integer(row, "q_value_read_responses")
            and integer(row, "q_value_read_responses")
            == integer(row, "q_value_fills")
            and integer(row, "p_A_FIRST_ISSUE")
            >= integer(row, "p_ROW_OFFSET_LAST_INSERT")
            and integer(row, "q_A_FIRST_ISSUE")
            >= integer(row, "q_ROW_OFFSET_LAST_INSERT"),
            f"whole-window link failed: {row}",
        )
        if args.producer == "fused":
            fused_row = fused_by_generation[p_generation]
            require(
                integer(fused_row, "source_ordinals") == 16384
                and integer(fused_row, "product_semantic_write_completions")
                == 16384
                and integer(fused_row, "offset_drains") == 0
                and integer(fused_row, "global_fallbacks") == 0,
                f"fused p16 terminal failed: {fused_row}",
            )
        else:
            product_backing = integer(row, "product_backing")
            page_rows = [
                product
                for product in product_response
                if integer(product, "core") == integer(row, "p_core")
                and integer(product, "backing") == product_backing
            ]
            require(
                len(page_rows) == 4
                and {integer(product, "page") for product in page_rows}
                == {0, 1, 2, 3}
                and len(
                    {integer(product, "generation") for product in page_rows}
                )
                == 4,
                "non-fused window lacks four linked product-page responses",
            )
        joined.append(
            {
                "schema": "dx100.cg.strict_p16_q16.window.v1",
                "window_ordinal": ordinal,
                "p_generation": p_generation,
                "q_generation": q_generation,
                "p_product_page_responses": 4,
                "p_terminal": True,
                "q_terminal": True,
                "p16_reorder_preserved": True,
                "q16_reorder_preserved": True,
                "producer": args.producer,
                "direct4_bypass": False,
                "drains": 0,
                "fallbacks": 0,
                "order_ok": True,
                "cg_numerical_terminal": True,
                "cg_fingerprint_sha256": fingerprint_sha,
                "cg_terminal_sha256": terminal_sha,
                "cg_reductions_sha256": reductions_sha,
            }
        )
    with (out / "whole_windows.jsonl").open("w", encoding="utf-8") as stream:
        for record in joined:
            stream.write(json.dumps(record, sort_keys=True) + "\n")

    checkpoint_after = base.tree_ledger(checkpoint)
    (input_dir / "checkpoint_files.after").write_text(checkpoint_after)
    require(checkpoint_before == checkpoint_after, "checkpoint changed")
    artifacts_after = base.artifact_ledger(immutable)
    (input_dir / "artifact_sha256.after").write_text(artifacts_after)
    require(artifacts_before == artifacts_after, "immutable artifact changed")
    require(
        base.source_status() == before_status
        and base.source_commit() == source_commit,
        "source changed during run",
    )
    control_ticks = control["stats"]["simTicks"]
    strict_ticks = strict["stats"]["simTicks"]
    result = {
        "schema": "dx100.cg.strict_p16_q16.v1",
        "terminal": True,
        "decision": "VALID_STRICT_REFERENCE",
        "cg_na": args.cg_na,
        "producer": args.producer,
        "scope": (
            "primary_simple_nonfused_reference"
            if args.producer == "page-fed"
            else "fusion_matched_diagnostic_only"
        ),
        "source_commit": source_commit,
        "gem5_sha256": base.sha256_file(gem5),
        "ramulator_sha256": base.sha256_file(fused.RAMULATOR),
        "guest_sha256": base.sha256_file(guest),
        "native_runs": 0,
        "direct4_runs": 0,
        "whole_windows": len(joined),
        "fingerprints_exact_equal": True,
        "deterministic_reductions_exact_equal": True,
        "strict_control_simTicks": control_ticks,
        "strict_reference_simTicks": strict_ticks,
        "control_over_strict": control_ticks / strict_ticks,
        "strict_stats": strict["stats"],
        "restore_commands": commands,
    }
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    ledger_targets = [
        path
        for path in sorted(out.rglob("*"))
        if path.is_file()
        and path.name not in {"raw_root.sha256", "gate.complete"}
    ]
    (out / "raw_root.sha256").write_text(
        "".join(
            f"{base.sha256_file(path)}  {path.relative_to(out)}\n"
            for path in ledger_targets
        )
    )
    ledger_sha = base.sha256_file(out / "raw_root.sha256")
    (out / "gate.complete").write_text(
        "COMPLETE_CG_STRICT_P16_Q16\n"
        "decision=VALID_STRICT_REFERENCE\n"
        "correctness=EXACT_MATCH\n"
        f"raw_root_sha256={ledger_sha}\n"
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
