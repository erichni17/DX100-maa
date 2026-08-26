#!/usr/bin/env python3
"""Run the exact cache-on CG_NA=256 p16/q16 control/fused pair.

One deterministic guest and one immutable checkpoint feed two serial restores.
No native or full workload is accepted.  Correctness, mechanism ledgers, and
the exact config delta close before first-ROI simTicks is compared.
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
SPEC = importlib.util.spec_from_file_location("cg_direct4_gate", BASE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load base gate: {BASE_PATH}")
direct = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(direct)
base = direct.base

CG_NA = 256
GEM5 = ROOT / "build/X86/gem5.opt"
RAMULATOR = base.RAMULATOR
ARMS = (
    ("control", "page_fed_product_soa_jit"),
    ("candidate", "fused_p16_product_q16"),
)
FINITE_KNOBS = (
    "--maa_virtual_combine_slots=16",
    "--maa_virtual_combine_ways=4",
    "--maa_virtual_combine_banks=4",
    "--maa_virtual_words_per_cycle=1",
    "--maa_virtual_response_slots=8",
    "--maa_virtual_response_words=0",
    "--maa_virtual_response_word_pool=0",
    "--maa_virtual_max_outstanding_writes=32",
    "--maa_soa_jit_value_prefetch_credits=0",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def parse_kv(line: str) -> dict[str, str]:
    return dict(field.split("=", 1) for field in line.split() if "=" in field)


def integer(fields: dict[str, str], name: str) -> int:
    try:
        return int(fields[name])
    except (KeyError, ValueError) as error:
        raise RuntimeError(f"missing integer terminal field {name}") from error


def require_config(config: Path) -> None:
    lines = config.read_text(errors="replace").splitlines()
    required = {
        "page_fed_soa_jit=true",
        "num_maas=1",
        "num_indirect_units_per_maa=4",
        "num_tiles_per_core=8",
        "num_tile_elements=16384",
        "physical_tile_elements=4096",
        "num_offset_table_entries=16384",
        "num_offset_table_epoch_entries=16384",
        "num_initial_row_table_slices=32",
        "virtual_combine_slots=16",
        "virtual_combine_ways=4",
        "virtual_combine_banks=4",
        "virtual_words_per_cycle=1",
        "virtual_response_slots=8",
        "virtual_response_words=0",
        "virtual_response_word_pool=0",
        "virtual_max_outstanding_writes=32",
        "soa_jit_value_cache_enable=true",
        "soa_jit_active_value_owners=32",
        "soa_jit_value_prefetch_credits=0",
    }
    require(
        not required.difference(lines),
        f"resolved config missing {sorted(required.difference(lines))}",
    )
    controllers = sum(
        bool(re.fullmatch(r"\[system\.mem_ctrls[01]\]", line))
        for line in lines
    )
    require(
        controllers == 2, f"expected two memory channels, saw {controllers}"
    )


def require_terminal(fields: dict[str, str], treatment: str) -> int:
    names = (
        "full_windows",
        "staged_index_words",
        "staged_value_words",
        "product_words",
        "index_publish_pages",
        "value_publish_pages",
        "product_publish_pages",
        "logical_alu_vectors",
        "physical_alu_vectors",
        "logical_page_windows",
        "physical_page_product_windows",
        "page_fed_product_windows",
        "fused_p16_product_windows",
        "direct4_product_page_fed_q16_windows",
        "virtual_p_gather_windows",
        "physical_p_gather_pages",
        "page_fed_admit_pages",
        "page_fed_closes",
        "q_spmv_eligible_windows",
        "q_spmv_routed_windows",
        "residual_spmv_eligible_windows",
        "residual_spmv_routed_windows",
        "external_coherent_backing_bytes",
        "physical_spd_payload_bytes",
        "host_payload_access",
        "coherent_index_backing_bytes",
        "virtual_p_backing_bytes",
        "virtual_p_allocation_bytes",
        "virtual_p_write_bytes",
        "virtual_p_read_bytes",
        "product_publisher_lines",
        "virtual_backing_traffic_eliminated",
        "p16_reorder_preserved",
        "q16_reorder_preserved",
        "hidden_spill_bytes",
        "global_fallbacks",
    )
    values = {name: integer(fields, name) for name in names}
    windows = values["full_windows"]
    words = windows * 16384
    pages = windows * 4
    common = (
        windows == 10
        and values["staged_index_words"] == words
        and values["staged_value_words"] == 0
        and values["product_words"] == words
        and values["index_publish_pages"] == 0
        and values["value_publish_pages"] == 0
        and values["logical_alu_vectors"] == 0
        and values["logical_page_windows"] == 0
        and values["physical_page_product_windows"] == 0
        and values["direct4_product_page_fed_q16_windows"] == 0
        and values["page_fed_admit_pages"] == pages
        and values["page_fed_closes"] == windows
        and values["q_spmv_eligible_windows"]
        == values["q_spmv_routed_windows"]
        and values["residual_spmv_eligible_windows"]
        == values["residual_spmv_routed_windows"]
        and values["q_spmv_routed_windows"]
        + values["residual_spmv_routed_windows"]
        == windows
        and values["physical_spd_payload_bytes"] == 524288
        and values["host_payload_access"] == 0
        and values["coherent_index_backing_bytes"] == 0
        and values["p16_reorder_preserved"] == 1
        and values["q16_reorder_preserved"] == 1
        and values["hidden_spill_bytes"] == 0
        and values["global_fallbacks"] == 0
    )
    if treatment == "page_fed_product_soa_jit":
        exact = (
            fields.get("p_gather_mode") == "virtual_16k"
            and values["page_fed_product_windows"] == windows
            and values["fused_p16_product_windows"] == 0
            and values["physical_alu_vectors"] == pages
            and values["product_publish_pages"] == pages
            and values["product_publisher_lines"] == pages * 256
            and values["virtual_p_gather_windows"] == windows
            and values["physical_p_gather_pages"] == 0
            and values["virtual_p_backing_bytes"] == 262144
            and values["virtual_p_allocation_bytes"] > 0
            and values["virtual_p_write_bytes"] == windows * 65536
            and values["virtual_p_read_bytes"] == windows * 65536
            and values["virtual_backing_traffic_eliminated"] == 0
            and values["external_coherent_backing_bytes"] == 524288
        )
    else:
        exact = (
            fields.get("p_gather_mode") == "fused_virtual_16k_product"
            and values["page_fed_product_windows"] == 0
            and values["fused_p16_product_windows"] == windows
            and values["physical_alu_vectors"] == 0
            and values["product_publish_pages"] == 0
            and values["product_publisher_lines"] == 0
            and values["virtual_p_gather_windows"] == 0
            and values["physical_p_gather_pages"] == 0
            and values["virtual_p_backing_bytes"] == 0
            and values["virtual_p_allocation_bytes"] == 0
            and values["virtual_p_write_bytes"] == 0
            and values["virtual_p_read_bytes"] == 0
            and values["virtual_backing_traffic_eliminated"] == 1
            and values["external_coherent_backing_bytes"] == 262144
        )
    require(common and exact, f"terminal closure failed: {fields}")
    return windows


def stat_sum_or_zero(stats: Path, name: str) -> int:
    try:
        return base.stat_sum(stats, name)
    except RuntimeError:
        return 0


def require_stats(stats: Path, windows: int, treatment: str) -> dict[str, int]:
    common_names = (
        "simTicks",
        "IND_SoaJitInstructions",
        "IND_SoaJitSelected",
        "IND_SoaJitAliasesApplied",
        "IND_SoaJitValueReadIssues",
        "IND_SoaJitValueReadResponses",
        "IND_SoaJitValueFills",
        "IND_SoaJitValueHits",
        "IND_SoaJitValueMergedWaiters",
        "IND_SoaJitValueDeliveries",
        "IND_SoaJitAReadIssues",
        "IND_SoaJitAReadResponses",
        "IND_SoaJitAWriteIssues",
        "IND_SoaJitAWriteResponses",
        "IND_SoaJitPageFedOperations",
        "IND_SoaJitPageFedAdmitCommands",
        "IND_SoaJitPageFedCloseCommands",
        "IND_SoaJitPageFedCommandResponses",
        "IND_SoaJitPageFedAdmittedWords",
        "IND_SoaJitPageFedSpdIndexReads",
        "IND_SoaJitPageFedRowWrites",
        "IND_SoaJitPageFedCoherentIndexReadLines",
        "IND_SoaJitPageFedCoherentIndexWriteLines",
        "IND_SoaJitEpochDrains",
        "IND_BoundedGlobalMergeFallbacks",
        "STR_PublishIssues",
        "STR_PublishWriteResponses",
    )
    values = {name: stat_sum_or_zero(stats, name) for name in common_names}
    words = windows * 16384
    pages = windows * 4
    q_closed = (
        values["simTicks"] > 0
        and values["IND_SoaJitInstructions"] == windows
        and values["IND_SoaJitSelected"] == words
        and values["IND_SoaJitAliasesApplied"] == words
        and values["IND_SoaJitValueDeliveries"] == words
        and values["IND_SoaJitValueReadIssues"]
        == values["IND_SoaJitValueReadResponses"]
        and values["IND_SoaJitValueReadResponses"]
        == values["IND_SoaJitValueFills"]
        and values["IND_SoaJitValueReadIssues"]
        + values["IND_SoaJitValueHits"]
        + values["IND_SoaJitValueMergedWaiters"]
        == words
        and values["IND_SoaJitAReadIssues"] > 0
        and values["IND_SoaJitAReadIssues"]
        == values["IND_SoaJitAReadResponses"]
        and values["IND_SoaJitAReadIssues"] == values["IND_SoaJitAWriteIssues"]
        and values["IND_SoaJitAWriteIssues"]
        == values["IND_SoaJitAWriteResponses"]
        and values["IND_SoaJitPageFedOperations"] == windows
        and values["IND_SoaJitPageFedAdmitCommands"] == pages
        and values["IND_SoaJitPageFedCloseCommands"] == windows
        and values["IND_SoaJitPageFedCommandResponses"] == windows * 5
        and values["IND_SoaJitPageFedAdmittedWords"] == words
        and values["IND_SoaJitPageFedSpdIndexReads"] == words
        and values["IND_SoaJitPageFedRowWrites"] == words
        and values["IND_SoaJitPageFedCoherentIndexReadLines"] == 0
        and values["IND_SoaJitPageFedCoherentIndexWriteLines"] == 0
        and values["IND_SoaJitEpochDrains"] == 0
        and values["IND_BoundedGlobalMergeFallbacks"] == 0
    )
    require(q_closed, f"q16 stats closure failed: {values}")

    fused_names = (
        "IND_FusedP16Operations",
        "IND_FusedP16Epochs",
        "IND_FusedP16SourceOrdinals",
        "IND_FusedP16CoefficientReadIssues",
        "IND_FusedP16CoefficientReadResponses",
        "IND_FusedP16CoefficientFills",
        "IND_FusedP16CoefficientDeliveries",
        "IND_FusedP16MulAccepts",
        "IND_FusedP16MulCompletions",
        "IND_FusedP16ProductInsertions",
        "IND_FusedP16ProductWriteCompletions",
        "IND_FusedP16EpochDrains",
        "IND_FusedP16Fallbacks",
        "IND_FusedP16PublisherLines",
        "IND_FusedP16VirtualPBytes",
    )
    values.update(
        {name: stat_sum_or_zero(stats, name) for name in fused_names}
    )
    if treatment == "fused_p16_product_q16":
        issues = values["IND_FusedP16CoefficientReadIssues"]
        fused_closed = (
            values["IND_FusedP16Operations"] == windows
            and values["IND_FusedP16Epochs"] == windows
            and values["IND_FusedP16SourceOrdinals"] == words
            and 1024 * windows <= issues <= words
            and values["IND_FusedP16CoefficientReadResponses"] == issues
            and values["IND_FusedP16CoefficientFills"] == issues
            and values["IND_FusedP16CoefficientDeliveries"] == words
            and values["IND_FusedP16MulAccepts"] == words
            and values["IND_FusedP16MulCompletions"] == words
            and values["IND_FusedP16ProductInsertions"] == words
            and values["IND_FusedP16ProductWriteCompletions"] == words
            and all(
                values[name] == 0
                for name in (
                    "IND_FusedP16EpochDrains",
                    "IND_FusedP16Fallbacks",
                    "IND_FusedP16PublisherLines",
                    "IND_FusedP16VirtualPBytes",
                    "STR_PublishIssues",
                    "STR_PublishWriteResponses",
                )
            )
        )
    else:
        fused_closed = all(values[name] == 0 for name in fused_names)
        fused_closed = fused_closed and (
            values["STR_PublishIssues"] == pages * 256
            and values["STR_PublishWriteResponses"] == pages * 256
        )
    require(fused_closed, f"producer stats closure failed: {values}")
    return values


def restore_args(
    guest: Path, selector: Path, checkpoint: Path, arm: Path
) -> list[str]:
    direct.base.GEM5 = GEM5
    args = direct.restore_args(
        guest, selector, checkpoint, arm, value_cache=True
    )
    require(args.count("--maa_soa_jit_value_cache_enable") == 1,
            "restore must enable the selected cache exactly once")
    require(args.count("--maa_soa_jit_active_value_owners=32") == 1,
            "restore must select the 32-owner pool exactly once")
    command_index = args.index("--cmd")
    args[command_index:command_index] = list(FINITE_KNOBS)
    return args


def normalized_config(path: Path) -> str:
    normalized = []
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("host_paths=") and "/fs/" in line:
            normalized.append(
                "host_paths=<ARM>/fs/" + line.rsplit("/fs/", 1)[1]
            )
        else:
            normalized.append(line)
    return "\n".join(normalized) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--cg-na", type=int, default=CG_NA)
    args = parser.parse_args(argv)
    if args.cg_na != CG_NA:
        parser.error(
            "this gate accepts only CG_NA=256; native/full are forbidden"
        )
    out = args.out.resolve()
    require(
        out != ROOT and ROOT not in out.parents,
        "output must be outside the source worktree",
    )
    require(
        not out.exists() or not any(out.iterdir()),
        f"refusing nonempty output: {out}",
    )
    require(
        GEM5.is_file() and os.access(GEM5, os.X_OK),
        f"missing current gem5 {GEM5}",
    )
    before_status = base.source_status()
    require(
        len(before_status.splitlines()) == 1,
        "refusing evidence from a dirty source worktree",
    )
    before_commit = base.source_commit()

    input_dir = out / "input"
    checkpoint = out / "checkpoint"
    input_dir.mkdir(parents=True)
    checkpoint.mkdir()
    selector = input_dir / "treatment.selector"
    selector.write_text("token_stream_ld page_fed_product_soa_jit\n")
    selector.chmod(0o444)
    guest = out / "cg_fused_p16_product_q16_guest"
    compile_args = [
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
        f"-DCG_NA={CG_NA}",
        "-DNUM_CORES=4",
        "-DNUM_TILES_PER_CORE=8",
        "-DTILE_SIZE=16384",
        "-DMAA_MEM_SIZE=0x80000000",
        str(ROOT / "util/m5/src/abi/x86/m5op.S"),
        str(base.SOURCE),
        "-o",
        str(guest),
    ]
    subprocess.run(compile_args, cwd=ROOT, check=True)

    immutable = (
        GEM5,
        RAMULATOR,
        guest,
        Path(__file__).resolve(),
        BASE_PATH,
        *base.GUEST_COMPILE_INPUTS,
        *base.RUNNER_CONFIG_INPUTS[1:],
    )
    artifacts_before = base.artifact_ledger(immutable)
    (input_dir / "artifact_sha256.before").write_text(artifacts_before)
    (input_dir / "source_status.before").write_text(before_status)
    (input_dir / "source_commit.before").write_text(before_commit + "\n")
    (input_dir / "compile_command.json").write_text(
        json.dumps(compile_args, indent=2) + "\n"
    )
    environment = dict(
        os.environ,
        LD_LIBRARY_PATH=str(RAMULATOR.parent)
        + ":"
        + os.environ.get("LD_LIBRARY_PATH", ""),
        OMP_NUM_THREADS="4",
        OMP_PROC_BIND="false",
    )
    ldd = subprocess.check_output(
        ["ldd", str(GEM5)], env=environment, text=True
    )
    match = re.search(r"^[ \t]*libramulator\.so => (\S+)", ldd, re.M)
    require(
        match is not None
        and Path(match.group(1)).resolve() == RAMULATOR.resolve(),
        "current gem5 did not resolve frozen Ramulator",
    )

    checkpoint_args = [
        str(GEM5),
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
    (input_dir / "checkpoint_command.json").write_text(
        json.dumps(checkpoint_args, indent=2) + "\n"
    )
    base.run_logged(checkpoint_args, out / "checkpoint.log", environment)
    base.exactly_one(
        (out / "checkpoint.log").read_text(errors="replace").splitlines(),
        r"^Exiting @ tick [0-9]+ because checkpoint$",
        "checkpoint terminal",
    )
    checkpoint_before = base.tree_ledger(checkpoint)
    (input_dir / "checkpoint_files.before").write_text(checkpoint_before)

    parsed: dict[str, dict] = {}
    commands: dict[str, list[str]] = {}
    for name, treatment in ARMS:
        selector.chmod(0o644)
        selector.write_text(f"token_stream_ld {treatment}\n")
        selector.chmod(0o444)
        arm = out / name
        arm.mkdir()
        (arm / "selector.txt").write_text(selector.read_text())
        command = restore_args(guest, selector, checkpoint, arm)
        commands[name] = command
        base.run_logged(command, arm / "restore.log", environment)
        lines = (arm / "restore.log").read_text(errors="replace").splitlines()
        require(
            not any(base.FATAL_RE.search(line) for line in lines),
            f"{name} contains fatal text",
        )
        base.exactly_one(
            lines,
            r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$",
            f"{name} terminal",
        )
        fingerprint = base.exactly_one(
            lines,
            rf"^CG_FINGERPRINT mode=MAA elements={CG_NA} .* result=PASS$",
            f"{name} fingerprint",
        )
        terminal_line = base.exactly_one(
            lines,
            rf"^CG_LOGICAL16_RMW_TERMINAL treatment={treatment} "
            r".* result=PASS$",
            f"{name} treatment terminal",
        )
        evidence = [
            line
            for line in lines
            if line.startswith(
                ("CG_REDUCTION_EVIDENCE ", "CG_OUTER_REDUCTION_EVIDENCE ")
            )
        ]
        require(
            len(evidence) == 11, f"{name} has {len(evidence)}/11 reductions"
        )
        terminal = parse_kv(terminal_line)
        windows = require_terminal(terminal, treatment)
        require_config(arm / "config.ini")
        stats = require_stats(arm / "stats.txt", windows, treatment)
        parsed[name] = {
            "fingerprint_line": fingerprint,
            "reduction_evidence": evidence,
            "terminal": terminal,
            "stats": stats,
        }

    control = parsed["control"]
    candidate = parsed["candidate"]
    require(
        control["fingerprint_line"] == candidate["fingerprint_line"],
        "raw/quantized fingerprint mismatch; performance is unreadable",
    )
    require(
        control["reduction_evidence"] == candidate["reduction_evidence"],
        "deterministic reduction mismatch; performance is unreadable",
    )
    require(
        control["terminal"]["full_windows"]
        == candidate["terminal"]["full_windows"],
        "p16 window count mismatch",
    )
    require(
        normalized_config(out / "control/config.ini")
        == normalized_config(out / "candidate/config.ini"),
        "resolved configs differ outside the guest selector",
    )

    checkpoint_after = base.tree_ledger(checkpoint)
    (input_dir / "checkpoint_files.after").write_text(checkpoint_after)
    require(checkpoint_before == checkpoint_after, "shared checkpoint changed")
    artifacts_after = base.artifact_ledger(immutable)
    (input_dir / "artifact_sha256.after").write_text(artifacts_after)
    require(artifacts_before == artifacts_after, "immutable artifact changed")
    require(
        base.source_status() == before_status
        and base.source_commit() == before_commit,
        "source identity changed during pair",
    )

    control_ticks = control["stats"]["simTicks"]
    candidate_ticks = candidate["stats"]["simTicks"]
    decision = "ACCEPT" if candidate_ticks < control_ticks else "REJECT"
    result = {
        "schema": "dx100.cg.fused_p16_product_q16.v1",
        "terminal": True,
        "decision": decision,
        "native_runs": 0,
        "full_cg_runs": 0,
        "cg_na": CG_NA,
        "source_commit": before_commit,
        "gem5_sha256": base.sha256_file(GEM5),
        "ramulator_sha256": base.sha256_file(RAMULATOR),
        "guest_sha256": base.sha256_file(guest),
        "checkpoint_ledger_sha256": hashlib.sha256(
            checkpoint_before.encode()
        ).hexdigest(),
        "fingerprints_exact_equal": True,
        "deterministic_reductions_exact_equal": True,
        "p16_epochs": int(candidate["terminal"]["full_windows"]),
        "virtual_p_bytes": 0,
        "publisher_lines": 0,
        "fallbacks": 0,
        "performance": {
            "metric": "simTicks",
            "control": control_ticks,
            "candidate": candidate_ticks,
            "control_over_candidate": control_ticks / candidate_ticks,
        },
        "restore_commands": commands,
        "arms": parsed,
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
        "COMPLETE_CG_FUSED_P16_PRODUCT_Q16\n"
        f"decision={decision}\ncorrectness=EXACT_MATCH\n"
        f"raw_root_sha256={ledger_sha}\n"
    )
    print(
        json.dumps(
            {
                "terminal": True,
                "decision": decision,
                "control_simTicks": control_ticks,
                "candidate_simTicks": candidate_ticks,
                "raw_root_sha256": ledger_sha,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
