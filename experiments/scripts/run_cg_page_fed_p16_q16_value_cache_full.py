#!/usr/bin/env python3
"""Run one evidence-grade full page-fed p16/q16 CG cache-on candidate.

This runner creates one deferred checkpoint and restores it exactly once for
page_fed_product_soa_jit with SoA/JIT value retention enabled.  It never runs
native, cache-off, direct4, or another predecessor/control arm.  The accepted
tolerant full-CG certificate is the sole numerical-policy authority, and the
accepted cache-off page-fed result is consulted only after the candidate has
passed every terminal, numerical, mechanism, provenance, immutability, and
source-stability gate.
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
BASE_PATH = ROOT / "experiments/scripts/run_cg_direct4_product_page_fed_q16_full.py"
SPEC = importlib.util.spec_from_file_location("cg_full_candidate_gate", BASE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load full-CG evidence gate: {BASE_PATH}")
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)

TREATMENT = "page_fed_product_soa_jit"
CG_NA = 150_000
EXPECTED_WINDOWS = 10_960
EXPECTED_Q_WINDOWS = 8_768
EXPECTED_RESIDUAL_WINDOWS = 2_192
EXPECTED_PAGES = 43_840
EXPECTED_WORDS = 179_568_640
EXPECTED_PUBLISH_LINES = 11_223_040
EXPECTED_A_LINES = 57_491
PHYSICAL_SPD_PAYLOAD_BYTES = 524_288
EXTERNAL_COHERENT_BACKING_BYTES = 524_288
VIRTUAL_P_BACKING_BYTES = 262_144
PRODUCT_BACKING_BYTES = 262_144
FIXED_VALUE_OWNER_LINES = 128
ACTIVE_VALUE_OWNER_LINES = 32
VALUE_OWNER_LINE_BYTES = 64
INDIRECT_UNITS_PER_MAA = 4


class GateError(base.GateError):
    """A fail-closed page-fed p16/q16 evidence gate rejected the run."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


def validate_terminal(fields: dict[str, str]) -> dict[str, int]:
    """Require exact full-CG p16/q16 page-fed terminal closure."""
    expected = {
        "full_windows": EXPECTED_WINDOWS,
        "staged_index_words": EXPECTED_WORDS,
        "staged_value_words": 0,
        "product_words": EXPECTED_WORDS,
        "index_publish_pages": 0,
        "value_publish_pages": 0,
        "product_publish_pages": EXPECTED_PAGES,
        "logical_alu_vectors": 0,
        "physical_alu_vectors": EXPECTED_PAGES,
        "logical_page_windows": 0,
        "physical_page_product_windows": 0,
        "page_fed_product_windows": EXPECTED_WINDOWS,
        "direct4_product_page_fed_q16_windows": 0,
        "virtual_p_gather_windows": EXPECTED_WINDOWS,
        "physical_p_gather_pages": 0,
        "page_fed_admit_pages": EXPECTED_PAGES,
        "page_fed_closes": EXPECTED_WINDOWS,
        "q_spmv_eligible_windows": EXPECTED_Q_WINDOWS,
        "q_spmv_routed_windows": EXPECTED_Q_WINDOWS,
        "residual_spmv_eligible_windows": EXPECTED_RESIDUAL_WINDOWS,
        "residual_spmv_routed_windows": EXPECTED_RESIDUAL_WINDOWS,
        "external_coherent_backing_bytes": EXTERNAL_COHERENT_BACKING_BYTES,
        "physical_spd_payload_bytes": PHYSICAL_SPD_PAYLOAD_BYTES,
        "logical_scheduler_reserved_lanes": 0,
        "logical_scheduler_reserved_lane_payload_bytes": 0,
        "host_payload_access": 0,
        "coherent_index_backing_bytes": 0,
        "virtual_p_backing_bytes": VIRTUAL_P_BACKING_BYTES,
        "virtual_backing_traffic_eliminated": 0,
        "p16_reorder_preserved": 1,
        "q16_reorder_preserved": 1,
    }
    try:
        actual = {key: int(fields[key]) for key in expected}
    except (KeyError, ValueError) as error:
        raise GateError(f"incomplete page-fed terminal: {error}") from error
    require(actual == expected, f"page-fed terminal mismatch: {actual}")
    exact_text = {
        "treatment": TREATMENT,
        "slice": "all_spmv_full_windows",
        "producer": "physical_page_mul_direct_index_admit",
        "p_gather_mode": "virtual_16k",
        "performance_promotable": "0",
        "result": "PASS",
    }
    require(
        all(fields.get(key) == value for key, value in exact_text.items()),
        "page-fed terminal text fields mismatch",
    )
    return actual


def validate_stats_values(values: dict[str, int]) -> None:
    """Require exact first-ROI work and bounded value-retention closure."""
    require(values["simTicks"] > 0, "first-ROI simTicks is not positive")
    exact = {
        "IND_SoaJitInstructions": EXPECTED_WINDOWS,
        "IND_SoaJitTerminalCompletions": EXPECTED_WINDOWS,
        "IND_SoaJitSelected": EXPECTED_WORDS,
        "IND_SoaJitAliasesApplied": EXPECTED_WORDS,
        "IND_SoaJitPredicateRejected": 0,
        "IND_SoaJitValueDeliveries": EXPECTED_WORDS,
        "IND_SoaJitAReadIssues": EXPECTED_A_LINES,
        "IND_SoaJitAReadResponses": EXPECTED_A_LINES,
        "IND_SoaJitAWriteIssues": EXPECTED_A_LINES,
        "IND_SoaJitAWriteResponses": EXPECTED_A_LINES,
        "IND_SoaJitPageFedOperations": EXPECTED_WINDOWS,
        "IND_SoaJitPageFedAdmitCommands": EXPECTED_PAGES,
        "IND_SoaJitPageFedCloseCommands": EXPECTED_WINDOWS,
        "IND_SoaJitPageFedCommandResponses": EXPECTED_PAGES + EXPECTED_WINDOWS,
        "IND_SoaJitPageFedAdmittedWords": EXPECTED_WORDS,
        "IND_SoaJitPageFedSpdIndexReads": EXPECTED_WORDS,
        "IND_SoaJitPageFedRowWrites": EXPECTED_WORDS,
        "IND_SoaJitPageFedCoherentIndexReadLines": 0,
        "IND_SoaJitPageFedCoherentIndexWriteLines": 0,
        "IND_SoaJitPageFedStateByteOperations": EXPECTED_WINDOWS * 16,
        "IND_SoaJitEpochDrains": 0,
        "IND_BoundedGlobalMergeFallbacks": 0,
        "STR_PublishIssues": EXPECTED_PUBLISH_LINES,
        "STR_PublishAccepts": EXPECTED_PUBLISH_LINES,
        "STR_PublishWriteResponses": EXPECTED_PUBLISH_LINES,
        "STR_PublishTerminals": EXPECTED_PAGES,
    }
    require(
        all(values.get(key) == value for key, value in exact.items()),
        "exact window/SoA/A/page-fed/publisher mechanism closure failed",
    )
    issues = values["IND_SoaJitValueReadIssues"]
    responses = values["IND_SoaJitValueReadResponses"]
    fills = values["IND_SoaJitValueFills"]
    cached = values["IND_SoaJitValueCachedResponses"]
    hits = values["IND_SoaJitValueHits"]
    merged = values["IND_SoaJitValueMergedWaiters"]
    deliveries = values["IND_SoaJitValueDeliveries"]
    require(
        issues > 0
        and responses > 0
        and fills > 0
        and issues == responses == fills == cached,
        "value issue/response/fill/cache closure failed",
    )
    require(
        hits > 0
        and issues < deliveries
        and merged >= 0
        and issues + hits + merged == deliveries,
        "value issue/hit/merge/delivery closure failed",
    )


def validate_stats(stats: Path) -> dict[str, int]:
    require(stats.is_file() and stats.stat().st_size > 0, "missing final stats")
    values = {name: base.first_stat_sum(stats, name) for name in base.STAT_NAMES}
    validate_stats_values(values)
    return values


def validate_restore(
    run: Path, native_fields: dict[str, str]
) -> tuple[dict[str, object], dict[str, float]]:
    log = run / "restore.log"
    lines = log.read_text(errors="replace").splitlines()
    require(
        not any(base.FATAL_RE.search(line) for line in lines),
        "fatal restore text",
    )
    base.exactly_one(
        lines,
        r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$",
        "m5 terminal",
    )
    require(sum(line == "ROI End!!!" for line in lines) == 1, "ROI did not close")
    terminal_line = base.exactly_one(
        lines,
        rf"^CG_LOGICAL16_RMW_TERMINAL treatment={TREATMENT} .* result=PASS$",
        "page-fed p16/q16 terminal",
    )
    terminal = validate_terminal(base.parse_kv(terminal_line))
    base.validate_config(run / "config.ini")
    stats = validate_stats(run / "stats.txt")
    _, candidate_fields = base.fingerprint_fields(log)
    deltas = base.validate_numerical(candidate_fields, native_fields)
    require(
        not any(path.name != "restore.log" for path in run.glob("*trace*.log")),
        "per-access trace artifact is forbidden",
    )
    return {
        "terminal": terminal,
        "terminal_line": terminal_line,
        "fingerprint": candidate_fields,
        "stats": stats,
    }, deltas


def compare_after_pass(gate: str, candidate_ticks: int) -> dict[str, object]:
    """Read the accepted cache-off result only after the candidate passes."""
    require(gate == "PASS_NUMERICAL_MECHANISM_CORRECT", "comparison before PASS")
    require(candidate_ticks > 0, "candidate simTicks is not positive")
    base.exact_hash(
        base.CONTROL_ROOT / "run/stats.txt",
        "3b0654de30ea2a1024373d2cf23f98f84b01d96abcf7d6906ea82a4762351c23",
        "accepted cache-off page-fed full stats",
    )
    accepted_ticks = base.first_stat_sum(
        base.CONTROL_ROOT / "run/stats.txt", "simTicks"
    )
    require(
        accepted_ticks == base.CONTROL_SIMTICKS,
        "accepted cache-off page-fed simTicks changed",
    )
    return {
        "metric": "first_roi_simTicks",
        "candidate": candidate_ticks,
        "accepted_cache_off_page_fed_full": accepted_ticks,
        "accepted_cache_off_over_candidate_ratio": (accepted_ticks / candidate_ticks),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out = args.out.resolve()
    if out == ROOT or ROOT in out.parents:
        raise SystemExit("output must be outside the source worktree")
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"refusing nonempty output: {out}")

    base.validate_source_base()
    base.exact_hash(base.GEM5, base.GEM5_SHA256, "archived page-fed gem5")
    base.exact_hash(base.RAMULATOR, base.RAMULATOR_SHA256, "frozen Ramulator")
    base.exact_hash(
        base.FROZEN_HEADER,
        base.FROZEN_HEADER_SHA256,
        "precomputed CG header",
    )
    require(
        base.FROZEN_HEADER.stat().st_size == base.FROZEN_HEADER_BYTES,
        "header size mismatch",
    )
    base.exact_hash(base.NATIVE_LOG, base.NATIVE_LOG_SHA256, "numerical authority")
    base.exact_hash(
        base.NATIVE_STATS,
        base.NATIVE_STATS_SHA256,
        "numerical-authority stats",
    )
    certificate_identity = base.validate_certificate()
    before_status = base.source_status()
    require(
        len(before_status.splitlines()) == 1,
        "refusing candidate evidence from a dirty source worktree",
    )
    before_commit = base.source_commit()

    input_dir = out / "input"
    bin_dir = out / "bin"
    checkpoint = out / "checkpoint"
    run = out / "run"
    for directory in (input_dir, bin_dir, checkpoint, run):
        directory.mkdir(parents=True, exist_ok=False)
    selector = input_dir / "page_fed_product_soa_jit.selector"
    selector.write_text(f"token_stream_ld {TREATMENT}\n", encoding="utf-8")
    selector.chmod(0o444)
    header = input_dir / "cg_data_4C.h"
    subprocess.run(
        ["cp", "--reflink=auto", str(base.FROZEN_HEADER), str(header)],
        check=True,
    )
    header.chmod(0o444)
    base.exact_hash(header, base.FROZEN_HEADER_SHA256, "copied precomputed header")
    require(
        header.stat().st_size == base.FROZEN_HEADER_BYTES,
        "copied header size mismatch",
    )

    guest = bin_dir / "cg_page_fed_p16_q16_value_cache_full"
    compile_args = base.compile_command(guest, input_dir)
    checkpoint_args = base.checkpoint_command(guest, selector, checkpoint)
    restore_args = base.restore_command(guest, selector, checkpoint, run)
    require(
        restore_args.count("--maa_soa_jit_value_cache_enable") == 1,
        "restore command lacks exactly one value-cache enable",
    )
    require(
        "direct4_product_page_fed_q16" not in " ".join(restore_args),
        "restore command contains direct4 treatment",
    )
    subprocess.run(compile_args, cwd=ROOT, check=True)

    library_path = str(base.RAMULATOR.parent)
    if os.environ.get("LD_LIBRARY_PATH"):
        library_path += ":" + os.environ["LD_LIBRARY_PATH"]
    environment = dict(
        os.environ,
        LD_LIBRARY_PATH=library_path,
        OMP_NUM_THREADS="4",
        OMP_PROC_BIND="false",
    )
    ldd_output = subprocess.check_output(
        ["ldd", str(base.GEM5)], env=environment, text=True
    )
    match = re.search(r"^[ \t]*libramulator\.so => (\S+)", ldd_output, re.M)
    require(
        match is not None
        and Path(match.group(1)).resolve() == base.RAMULATOR.resolve(),
        "archived gem5 did not resolve frozen Ramulator",
    )

    immutable_artifacts = (
        base.GEM5,
        base.RAMULATOR,
        guest,
        selector,
        header,
        base.NATIVE_LOG,
        base.NATIVE_STATS,
        *(base.CERTIFICATE_ROOT / name for name in sorted(base.CERTIFICATE_FILES)),
        BASE_PATH,
        Path(__file__).resolve(),
        *base.GUEST_COMPILE_INPUTS,
        *base.CONFIG_INPUTS,
    )
    artifacts_before = base.artifact_ledger(immutable_artifacts)
    (input_dir / "artifact_sha256.before").write_text(
        artifacts_before, encoding="utf-8"
    )
    (input_dir / "source_status.before").write_text(before_status, encoding="utf-8")
    (input_dir / "source_commit.before").write_text(
        before_commit + "\n", encoding="utf-8"
    )
    for name, command in (
        ("compile", compile_args),
        ("checkpoint", checkpoint_args),
        ("restore", restore_args),
    ):
        (input_dir / f"{name}_command.json").write_text(
            json.dumps(command, indent=2) + "\n", encoding="utf-8"
        )
    manifest = {
        "schema": "dx100.cg.page_fed_p16_q16_value_cache_full.v1",
        "terminal": False,
        "candidate_only": True,
        "guest_runs": 1,
        "native_runs": 0,
        "cache_off_runs": 0,
        "direct4_runs": 0,
        "trace": "disabled",
        "timeout": "none",
        "source_base_commit": base.SOURCE_BASE_COMMIT,
        "source_commit": before_commit,
        "cg_na": CG_NA,
        "selector": TREATMENT,
        "geometry": {
            "cores": 4,
            "tiles_per_core": 8,
            "logical_tile_elements": 16_384,
            "physical_tile_elements": 4_096,
            "physical_spd_payload_bytes": PHYSICAL_SPD_PAYLOAD_BYTES,
            "external_coherent_backing_bytes": EXTERNAL_COHERENT_BACKING_BYTES,
            "virtual_p_backing_bytes": VIRTUAL_P_BACKING_BYTES,
            "product_backing_bytes": PRODUCT_BACKING_BYTES,
            "coherent_q_index_backing_bytes": 0,
            "host_payload_bytes": 0,
        },
        "reorder": {"p16_preserved": True, "q16_preserved": True},
        "value_retention": {
            "enabled": True,
            "new_payload_bytes": 0,
            "new_control_bytes": 0,
            "new_ports": 0,
            "fixed_value_owner_lines_per_unit": FIXED_VALUE_OWNER_LINES,
            "active_value_owner_lines_per_unit": ACTIVE_VALUE_OWNER_LINES,
            "line_bytes": VALUE_OWNER_LINE_BYTES,
            "indirect_units_per_maa": INDIRECT_UNITS_PER_MAA,
            "fixed_value_owner_payload_bytes_per_maa": (
                FIXED_VALUE_OWNER_LINES
                * VALUE_OWNER_LINE_BYTES
                * INDIRECT_UNITS_PER_MAA
            ),
            "active_value_owner_payload_bytes_per_maa": (
                ACTIVE_VALUE_OWNER_LINES
                * VALUE_OWNER_LINE_BYTES
                * INDIRECT_UNITS_PER_MAA
            ),
        },
        "precomputed_header": {
            "source": str(base.FROZEN_HEADER),
            "sha256": base.FROZEN_HEADER_SHA256,
            "bytes": base.FROZEN_HEADER_BYTES,
        },
        "accepted_cache_off_page_fed_full": {
            "root": str(base.CONTROL_ROOT),
            "simTicks": base.CONTROL_SIMTICKS,
            "comparison_policy": "only_after_candidate_PASS",
        },
        "certificate": certificate_identity,
        "commands": {
            "compile": compile_args,
            "checkpoint": checkpoint_args,
            "restore": restore_args,
        },
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # Execution starts only after every immutable identity and command exists.
    base.run_logged(checkpoint_args, out / "checkpoint.log", environment)
    checkpoint_lines = (out / "checkpoint.log").read_text(errors="replace").splitlines()
    base.exactly_one(
        checkpoint_lines,
        r"^Exiting @ tick [0-9]+ because checkpoint$",
        "checkpoint terminal",
    )
    require(
        not any(
            line.startswith(
                ("CG_FINGERPRINT ", "CG_LOGICAL16_RMW_TERMINAL ", "ROI End!!!")
            )
            for line in checkpoint_lines
        ),
        "checkpoint crossed deferred candidate boundary",
    )
    checkpoint_before = base.tree_ledger(checkpoint)
    (input_dir / "checkpoint.files.sha256.before").write_text(
        checkpoint_before, encoding="utf-8"
    )

    base.run_logged(restore_args, run / "restore.log", environment)

    # All validation precedes both comparison arithmetic and terminal outputs.
    require(
        (run / "restore.log.exit").read_text(encoding="utf-8").strip() == "0",
        "restore wrapper exit is not zero",
    )
    checkpoint_after = base.tree_ledger(checkpoint)
    (input_dir / "checkpoint.files.sha256.after").write_text(
        checkpoint_after, encoding="utf-8"
    )
    require(checkpoint_after == checkpoint_before, "checkpoint changed")
    artifacts_after = base.artifact_ledger(immutable_artifacts)
    (input_dir / "artifact_sha256.after").write_text(artifacts_after, encoding="utf-8")
    require(artifacts_after == artifacts_before, "immutable artifact changed")
    after_status = base.source_status()
    after_commit = base.source_commit()
    (input_dir / "source_status.after").write_text(after_status, encoding="utf-8")
    (input_dir / "source_commit.after").write_text(
        after_commit + "\n", encoding="utf-8"
    )
    require(after_status == before_status, "source status changed during run")
    require(after_commit == before_commit, "source commit changed during run")
    base.validate_certificate()
    base.exact_hash(
        base.FROZEN_HEADER,
        base.FROZEN_HEADER_SHA256,
        "precomputed CG header after run",
    )
    base.exact_hash(
        base.NATIVE_LOG,
        base.NATIVE_LOG_SHA256,
        "numerical authority after run",
    )
    _, authority_fields = base.fingerprint_fields(base.NATIVE_LOG)
    candidate, numerical_deltas = validate_restore(run, authority_fields)

    gate = "PASS_NUMERICAL_MECHANISM_CORRECT"
    sim_ticks = candidate["stats"]["simTicks"]  # type: ignore[index]
    performance = compare_after_pass(gate, sim_ticks)  # type: ignore[arg-type]
    result: dict[str, object] = {
        "schema": "dx100.cg.page_fed_p16_q16_value_cache_full_result.v1",
        "terminal": True,
        "gate": gate,
        "candidate_only": True,
        "observations": 1,
        "official_nas_verification": False,
        "native_speedup_claim": False,
        "direct4_claim": False,
        "iso_area_speedup_claim": False,
        "p16_reorder_preserved": True,
        "q16_reorder_preserved": True,
        "selected_value_cache_enable": True,
        "hardware_accounting": {
            "geometry": manifest["geometry"],
            "value_retention": manifest["value_retention"],
        },
        "source_commit": before_commit,
        "gem5_sha256": base.GEM5_SHA256,
        "ramulator_sha256": base.RAMULATOR_SHA256,
        "guest_sha256": base.sha256_file(guest),
        "checkpoint_ledger_sha256": hashlib.sha256(
            checkpoint_before.encode()
        ).hexdigest(),
        "certificate": certificate_identity,
        "numerical_relative_deltas_vs_authority": numerical_deltas,
        "performance": performance,
        "candidate": candidate,
    }
    certified_paths = [
        out / "manifest.json",
        run / "restore.log",
        run / "restore.log.exit",
        run / "stats.txt",
        run / "config.ini",
        input_dir / "checkpoint.files.sha256.before",
        input_dir / "checkpoint.files.sha256.after",
        input_dir / "artifact_sha256.before",
        input_dir / "artifact_sha256.after",
        input_dir / "source_status.before",
        input_dir / "source_status.after",
        input_dir / "source_commit.before",
        input_dir / "source_commit.after",
    ]
    certified_ledger = base.artifact_ledger(certified_paths)
    base.write_result_and_gate(out, result, certified_ledger)
    print(
        json.dumps(
            {
                "terminal": True,
                "gate": gate,
                "simTicks": sim_ticks,
                "accepted_cache_off_over_candidate_ratio": performance[
                    "accepted_cache_off_over_candidate_ratio"
                ],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
