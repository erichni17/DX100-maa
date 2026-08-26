#!/usr/bin/env python3
"""Run one bounded CG direct4-product/q16 candidate pair.

One deterministic-reduction guest and one deferred checkpoint feed the matched
serial page-fed control and direct4-product/q16 treatment.  The default is
CG_NA=1024; bounded explicit sizes are supported through --cg-na.  There is no
native or full run, no timeout, and no per-access trace.
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
BASE_PATH = (
    ROOT / "experiments/scripts/run_cg_page_fed_reduction_order_diagnosis.py"
)
SPEC = importlib.util.spec_from_file_location("cg_reduction_gate", BASE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load hardened gate: {BASE_PATH}")
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)
HARDENED_REQUIRE_STATS = base.require_stats

DEFAULT_CG_NA = 1024
MAX_CG_NA = 32768
TREATMENTS = (
    ("control", "page_fed_product_soa_jit"),
    ("direct4_q16", "direct4_product_page_fed_q16"),
)


def require_config_8(config: Path, page_fed: bool) -> None:
    """Require the resolved eight-tile page-fed geometry exactly once."""
    if not page_fed:
        raise RuntimeError("both candidate-only arms must enable page-fed q16")
    lines = config.read_text(errors="replace").splitlines()
    required = {
        "page_fed_soa_jit=true",
        "num_maas=1",
        "num_indirect_units_per_maa=4",
        "num_tile_elements=16384",
        "physical_tile_elements=4096",
        "num_offset_table_entries=16384",
        "num_offset_table_epoch_entries=16384",
        "num_initial_row_table_slices=32",
        "soa_jit_predicate_active_credits=16",
        "soa_jit_active_value_owners=32",
    }
    missing = sorted(required.difference(lines))
    if missing:
        raise RuntimeError(f"resolved 8-tile config missing {missing}")
    tile_lines = [
        line for line in lines if line.startswith("num_tiles_per_core=")
    ]
    if tile_lines != ["num_tiles_per_core=8"]:
        raise RuntimeError(
            f"expected exactly one num_tiles_per_core=8, saw {tile_lines!r}"
        )
    controllers = sum(
        bool(re.fullmatch(r"\[system\.mem_ctrls[01]\]", line))
        for line in lines
    )
    if controllers != 2:
        raise RuntimeError(
            f"expected exactly two memory channels, saw {controllers}"
        )


def require_terminal_8(
    fields: dict[str, str], treatment: str, cg_na: int
) -> int:
    """Close one terminal against the explicitly selected bounded size."""
    if not 1 <= cg_na <= MAX_CG_NA:
        raise RuntimeError(f"terminal gate received forbidden CG_NA={cg_na}")
    integer_keys = (
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
        "logical_scheduler_reserved_lanes",
        "logical_scheduler_reserved_lane_payload_bytes",
        "host_payload_access",
        "coherent_index_backing_bytes",
        "virtual_p_backing_bytes",
        "virtual_backing_traffic_eliminated",
        "p16_reorder_preserved",
        "q16_reorder_preserved",
    )
    try:
        values = {key: int(fields[key]) for key in integer_keys}
    except (KeyError, ValueError) as error:
        raise RuntimeError(
            f"incomplete candidate terminal: {error}"
        ) from error
    windows = values["full_windows"]
    pages = windows * 4
    words = windows * 16384
    common = (
        windows > 0
        and values["staged_index_words"] == words
        and values["staged_value_words"] == 0
        and values["product_words"] == words
        and values["index_publish_pages"] == 0
        and values["value_publish_pages"] == 0
        and values["product_publish_pages"] == pages
        and values["logical_alu_vectors"] == 0
        and values["physical_alu_vectors"] == pages
        and values["logical_page_windows"] == 0
        and values["physical_page_product_windows"] == 0
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
        and values["logical_scheduler_reserved_lanes"] == 0
        and values["logical_scheduler_reserved_lane_payload_bytes"] == 0
        and values["host_payload_access"] == 0
        and values["coherent_index_backing_bytes"] == 0
        and values["q16_reorder_preserved"] == 1
    )
    if treatment == "page_fed_product_soa_jit":
        exact = (
            fields.get("p_gather_mode") == "virtual_16k"
            and values["page_fed_product_windows"] == windows
            and values["direct4_product_page_fed_q16_windows"] == 0
            and values["virtual_p_gather_windows"] == windows
            and values["physical_p_gather_pages"] == 0
            and values["virtual_p_backing_bytes"] == 262144
            and values["virtual_backing_traffic_eliminated"] == 0
            and values["p16_reorder_preserved"] == 1
            and values["external_coherent_backing_bytes"] == 524288
        )
    else:
        exact = (
            fields.get("p_gather_mode") == "physical_4k_direct"
            and values["page_fed_product_windows"] == 0
            and values["direct4_product_page_fed_q16_windows"] == windows
            and values["virtual_p_gather_windows"] == 0
            and values["physical_p_gather_pages"] == pages
            and values["virtual_p_backing_bytes"] == 0
            and values["virtual_backing_traffic_eliminated"] == 1
            and values["p16_reorder_preserved"] == 0
            and values["external_coherent_backing_bytes"] == 262144
        )
    if not common or not exact:
        raise RuntimeError(
            f"terminal closure failed for {treatment}: {fields}"
        )
    return windows


def require_stats_8(
    stats: Path, windows: int, page_fed: bool
) -> dict[str, int]:
    values = HARDENED_REQUIRE_STATS(stats, windows, page_fed)
    extra_names = (
        "IND_SoaJitPageFedCommandResponses",
        "IND_SoaJitPageFedAdmittedWords",
        "IND_SoaJitPageFedSpdIndexReads",
        "IND_SoaJitPageFedRowWrites",
        "IND_SoaJitPageFedCoherentIndexReadLines",
        "IND_SoaJitPageFedCoherentIndexWriteLines",
        "IND_SoaJitPageFedStateByteOperations",
    )
    values.update({name: base.stat_sum(stats, name) for name in extra_names})
    words = windows * 16384
    closed = (
        values["IND_SoaJitPageFedCommandResponses"] == windows * 5
        and values["IND_SoaJitPageFedAdmittedWords"] == words
        and values["IND_SoaJitPageFedSpdIndexReads"] == words
        and values["IND_SoaJitPageFedRowWrites"] == words
        and values["IND_SoaJitPageFedCoherentIndexReadLines"] == 0
        and values["IND_SoaJitPageFedCoherentIndexWriteLines"] == 0
        and values["IND_SoaJitPageFedStateByteOperations"] == windows * 16
    )
    if not closed:
        raise RuntimeError(f"q16 mechanism closure failed: {values}")
    return values


# parse_arm resolves these names in the imported module.  Replace the inherited
# ten-tile config gate before any arm is parsed while retaining the hardened
# coalescer delivery closure from 51ec728d.  The terminal gate is bound to the
# selected CG size in parse_arm below.
base.require_config = require_config_8
base.require_stats = require_stats_8


def parse_arm(arm: Path, cg_na: int, treatment: str) -> dict:
    """Parse one arm with the selected size in fingerprint and terminal gates."""
    if not 1 <= cg_na <= MAX_CG_NA:
        raise RuntimeError(
            f"CG_NA must be in 1..{MAX_CG_NA}; full CG is forbidden"
        )

    def selected_terminal(
        fields: dict[str, str], selected_treatment: str
    ) -> int:
        return require_terminal_8(fields, selected_treatment, cg_na)

    base.require_terminal = selected_terminal
    return base.parse_arm(arm, cg_na, treatment, True)


def restore_args(
    guest: Path, selector: Path, checkpoint: Path, arm: Path
) -> list[str]:
    args = base.restore_args(guest, selector, checkpoint, arm, True)
    replaced = 0
    for index, value in enumerate(args):
        if value == "--maa_num_tiles_per_core=10":
            args[index] = "--maa_num_tiles_per_core=8"
            replaced += 1
    if replaced != 1 or args.count("--maa_num_tiles_per_core=8") != 1:
        raise RuntimeError(
            "restore command did not resolve exactly one 8-tile knob"
        )
    return args


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--cg-na", type=int, default=DEFAULT_CG_NA)
    args = parser.parse_args(argv)
    if not 1 <= args.cg_na <= MAX_CG_NA:
        parser.error(f"CG_NA must be in 1..{MAX_CG_NA}; full CG is forbidden")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cg_na = args.cg_na
    out = args.out.resolve()
    if out == ROOT or ROOT in out.parents:
        raise SystemExit("output must be outside the source worktree")
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"refusing nonempty output: {out}")

    base.exact_hash(base.GEM5, base.GEM5_SHA256, "frozen page-fed gem5")
    base.exact_hash(base.RAMULATOR, base.RAMULATOR_SHA256, "frozen Ramulator")
    before_status = base.source_status()
    if len(before_status.splitlines()) != 1:
        raise SystemExit("refusing evidence from a dirty source worktree")
    before_commit = base.source_commit()

    input_dir = out / "input"
    checkpoint = out / "checkpoint"
    input_dir.mkdir(parents=True)
    checkpoint.mkdir()
    guest = out / "cg_direct4_product_page_fed_q16_guest"
    selector = input_dir / "treatment.selector"
    selector.write_text("token_stream_ld page_fed_product_soa_jit\n")
    selector.chmod(0o444)

    compile_args = [
        os.environ.get("CXX", "g++"),
        f"-I{ROOT / 'benchmarks/API'}",
        f"-I{ROOT / 'include'}",
        f"-I{ROOT / 'util/m5/src'}",
        "-std=c++11",
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
    subprocess.run(compile_args, cwd=ROOT, check=True)

    immutable_artifacts = (
        base.GEM5,
        base.RAMULATOR,
        guest,
        BASE_PATH,
        Path(__file__).resolve(),
        *base.GUEST_COMPILE_INPUTS,
        *base.RUNNER_CONFIG_INPUTS[1:],
    )
    (input_dir / "artifact_sha256.before").write_text(
        base.artifact_ledger(immutable_artifacts)
    )
    (input_dir / "source_status.before").write_text(before_status)
    (input_dir / "source_commit.before").write_text(before_commit + "\n")
    (input_dir / "compile_command.json").write_text(
        json.dumps(compile_args, indent=2) + "\n"
    )

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
    if (
        match is None
        or Path(match.group(1)).resolve() != base.RAMULATOR.resolve()
    ):
        raise RuntimeError("archived gem5 did not resolve frozen Ramulator")

    checkpoint_args = [
        str(base.GEM5),
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
    checkpoint_lines = (
        (out / "checkpoint.log").read_text(errors="replace").splitlines()
    )
    base.exactly_one(
        checkpoint_lines,
        r"^Exiting @ tick [0-9]+ because checkpoint$",
        "checkpoint terminal",
    )
    forbidden = (
        "CG_REDUCTION_EVIDENCE ",
        "CG_OUTER_REDUCTION_EVIDENCE ",
        "CG_FINGERPRINT ",
        "CG_LOGICAL16_RMW_TERMINAL ",
    )
    if any(line.startswith(forbidden) for line in checkpoint_lines):
        raise RuntimeError("checkpoint crossed deferred treatment boundary")
    checkpoint_before = base.tree_ledger(checkpoint)
    (input_dir / "checkpoint_files.before").write_text(checkpoint_before)

    parsed: dict[str, dict] = {}
    restore_commands: dict[str, list[str]] = {}
    for arm_name, treatment in TREATMENTS:
        selector.chmod(0o644)
        selector.write_text(f"token_stream_ld {treatment}\n")
        selector.chmod(0o444)
        arm = out / arm_name
        arm.mkdir()
        (arm / "selector.txt").write_text(selector.read_text())
        restore = restore_args(guest, selector, checkpoint, arm)
        restore_commands[arm_name] = restore
        base.run_logged(restore, arm / "restore.log", environment)
        parsed[arm_name] = parse_arm(arm, cg_na, treatment)

    control = parsed["control"]
    candidate = parsed["direct4_q16"]
    fingerprint_equal = (
        control["fingerprint_line"] == candidate["fingerprint_line"]
    )
    reduction_equal = (
        control["reduction_evidence"] == candidate["reduction_evidence"]
    )
    if len(control["reduction_evidence"]) != 11:
        raise RuntimeError("expected all 11 deterministic reduction records")
    if not fingerprint_equal or not reduction_equal:
        raise RuntimeError(
            "correctness mismatch; simTicks comparison forbidden"
        )
    if (
        control["terminal"]["full_windows"]
        != candidate["terminal"]["full_windows"]
    ):
        raise RuntimeError("treatment window counts differ")

    checkpoint_after = base.tree_ledger(checkpoint)
    (input_dir / "checkpoint_files.after").write_text(checkpoint_after)
    if checkpoint_before != checkpoint_after:
        raise RuntimeError("shared checkpoint changed during restores")
    after_artifacts = base.artifact_ledger(immutable_artifacts)
    (input_dir / "artifact_sha256.after").write_text(after_artifacts)
    if (input_dir / "artifact_sha256.before").read_text() != after_artifacts:
        raise RuntimeError("immutable artifact changed during experiment")
    after_status = base.source_status()
    after_commit = base.source_commit()
    (input_dir / "source_status.after").write_text(after_status)
    (input_dir / "source_commit.after").write_text(after_commit + "\n")
    if after_status != before_status or after_commit != before_commit:
        raise RuntimeError("source identity changed during experiment")

    control_ticks = control["stats"]["simTicks"]
    candidate_ticks = candidate["stats"]["simTicks"]
    result = {
        "schema": "dx100.cg.direct4_product_page_fed_q16.v1",
        "terminal": True,
        "candidate_only": True,
        "native_runs": 0,
        "full_cg_runs": 0,
        "timeout": "none",
        "cg_na": cg_na,
        "selected_cg_na": cg_na,
        "source_commit": before_commit,
        "gem5_sha256": base.GEM5_SHA256,
        "ramulator_sha256": base.RAMULATOR_SHA256,
        "guest_sha256": base.sha256_file(guest),
        "checkpoint_ledger_sha256": hashlib.sha256(
            checkpoint_before.encode()
        ).hexdigest(),
        "restore_commands": restore_commands,
        "fingerprint_raw_and_quantized_exact_equal": True,
        "deterministic_reduction_records": 11,
        "deterministic_reduction_bits_exact_equal": True,
        "p16_reorder_preserved_by_candidate": False,
        "q16_reorder_preserved_by_candidate": True,
        "performance": {
            "metric": "simTicks",
            "control": control_ticks,
            "direct4_q16": candidate_ticks,
            "control_over_candidate_speedup": control_ticks / candidate_ticks,
        },
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
        "COMPLETE_CG_DIRECT4_PRODUCT_PAGE_FED_Q16\n"
        "correctness=EXACT_MATCH\n"
        f"raw_root_sha256={ledger_sha}\n"
    )
    print(
        json.dumps(
            {
                "terminal": True,
                "cg_na": cg_na,
                "correctness": "EXACT_MATCH",
                "raw_root_sha256": ledger_sha,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
