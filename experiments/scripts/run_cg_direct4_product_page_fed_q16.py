#!/usr/bin/env python3
"""Run one bounded CG direct4-product/q16 candidate pair.

One deterministic-reduction guest and one deferred checkpoint feed the matched
serial page-fed control and direct4-product/q16 treatment.  The default is
CG_NA=1024; bounded explicit sizes are supported through --cg-na.  There is no
native or full run, no timeout, and no per-access trace.  The optional
--value-cache-pair instead holds direct4/q16 fixed and isolates retention in
the already provisioned bounded SoA/JIT value-owner pool.  The optional
--publisher-pingpong-pair holds that cache on and compares the serial direct4
schedule with two disjoint four-tile producer groups.
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
MAX_PINGPONG_CG_NA = 4096
TREATMENTS = (
    ("control", "page_fed_product_soa_jit"),
    ("direct4_q16", "direct4_product_page_fed_q16"),
)
SELECTED_TREATMENTS = (
    ("control", "page_fed_product_soa_jit", False),
    ("direct4_q16", "direct4_product_page_fed_q16", True),
)
VALUE_CACHE_TREATMENTS = (
    ("cache_off", "direct4_product_page_fed_q16", False),
    ("cache_on", "direct4_product_page_fed_q16", True),
)
PUBLISHER_PINGPONG_TREATMENTS = (
    ("serial", "direct4_product_page_fed_q16", True),
    (
        "pingpong",
        "direct4_product_page_fed_q16_pingpong",
        True,
    ),
)
FIXED_VALUE_OWNER_LINES = 128
ACTIVE_VALUE_OWNER_LINES = 32
VALUE_OWNER_LINE_BYTES = 64
INDIRECT_UNITS_PER_MAA = 4
_expected_value_cache: bool | None = None


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
    if _expected_value_cache is not None:
        expected = (
            "soa_jit_value_cache_enable="
            f"{'true' if _expected_value_cache else 'false'}"
        )
        cache_lines = [
            line
            for line in lines
            if line.startswith("soa_jit_value_cache_enable=")
        ]
        if cache_lines != [expected]:
            raise RuntimeError(
                f"expected exactly one {expected}, saw {cache_lines!r}"
            )
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
    elif treatment in {
        "direct4_product_page_fed_q16",
        "direct4_product_page_fed_q16_pingpong",
    }:
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
    else:
        raise RuntimeError(f"unrecognized candidate treatment {treatment}")
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
        "IND_SoaJitValueEvictions",
        "IND_SoaJitValueStalls",
        "IND_SoaJitValueCacheHighWater",
        "IND_SoaJitLookaheadStalls",
        "IND_SoaJitContextStalls",
        "IND_SoaJitPageFedCommandResponses",
        "IND_SoaJitPageFedAdmittedWords",
        "IND_SoaJitPageFedSpdIndexReads",
        "IND_SoaJitPageFedRowWrites",
        "IND_SoaJitPageFedCoherentIndexReadLines",
        "IND_SoaJitPageFedCoherentIndexWriteLines",
        "IND_SoaJitPageFedStateByteOperations",
        "STR_PublishRetries",
        "STR_PublishCreditStalls",
        "STR_PublishOverlapIssues",
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
        and values["STR_PublishRetries"] == 0
        and values["STR_PublishCreditStalls"] > 0
    )
    if not closed:
        raise RuntimeError(f"q16 mechanism closure failed: {values}")
    exact_names = (
        "system.maa.cycles_TOTAL",
        "system.maa.cycles_INDRMW",
        "system.maa.port_cache_RD_packets",
        "system.maa.port_cache_WR_packets",
        "system.maa.I0_IND_CyclesRequest",
    )
    values.update(
        {name: first_stat_exact(stats, name) for name in exact_names}
    )
    return values


def first_stat_exact(stats: Path, name: str) -> int:
    """Read one exact name from the first and only accepted ROI window."""
    section = 0
    found: list[int] = []
    for line in stats.read_text(errors="replace").splitlines():
        if line.startswith("---------- Begin Simulation Statistics"):
            section += 1
            continue
        if section == 1 and line.startswith(
            "---------- End Simulation Statistics"
        ):
            break
        fields = line.split()
        if section == 1 and len(fields) >= 2 and fields[0] == name:
            found.append(int(float(fields[1])))
    if len(found) != 1:
        raise RuntimeError(
            f"expected one first-window stat {name}, saw {len(found)}"
        )
    return found[0]


# parse_arm resolves these names in the imported module.  Replace the inherited
# ten-tile config gate before any arm is parsed while retaining the hardened
# coalescer delivery closure from 51ec728d.  The terminal gate is bound to the
# selected CG size in parse_arm below.
base.require_config = require_config_8
base.require_stats = require_stats_8


def parse_arm(
    arm: Path,
    cg_na: int,
    treatment: str,
    value_cache: bool | None = None,
) -> dict:
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
    global _expected_value_cache
    _expected_value_cache = value_cache
    try:
        return base.parse_arm(arm, cg_na, treatment, True)
    finally:
        _expected_value_cache = None


def restore_args(
    guest: Path,
    selector: Path,
    checkpoint: Path,
    arm: Path,
    value_cache: bool = False,
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
    if value_cache:
        args.append("--maa_soa_jit_value_cache_enable")
    return args


def normalized_cache_pair_config(config: Path) -> str:
    """Normalize only the declared cache bit and run-local redirect paths."""
    normalized = []
    for line in config.read_text(errors="replace").splitlines():
        if line.startswith("soa_jit_value_cache_enable="):
            normalized.append("soa_jit_value_cache_enable=<TREATMENT>")
        elif line.startswith("host_paths=") and "/fs/" in line:
            normalized.append(
                "host_paths=<ARM>/fs/" + line.rsplit("/fs/", 1)[1]
            )
        else:
            normalized.append(line)
    return "\n".join(normalized) + "\n"


def normalized_direct4_terminal(terminal: dict[str, str]) -> dict[str, str]:
    """Remove labels while retaining every mechanism/accounting field."""
    return {
        key: value
        for key, value in terminal.items()
        if key not in {"treatment", "result"}
    }


def classify_value_cache_pair(control: dict, candidate: dict) -> str:
    """Classify only an exact, work-conserving value-retention pair."""
    control_stats = control["stats"]
    candidate_stats = candidate["stats"]
    conserved_names = (
        "IND_SoaJitSelected",
        "IND_SoaJitValueDeliveries",
        "IND_SoaJitAReadIssues",
        "IND_SoaJitAWriteIssues",
        "STR_PublishIssues",
    )
    if any(
        control_stats[name] != candidate_stats[name]
        for name in conserved_names
    ):
        raise RuntimeError("value-cache pair changed conserved work")
    traffic_reduced = (
        candidate_stats["IND_SoaJitValueReadIssues"]
        < control_stats["IND_SoaJitValueReadIssues"]
        and candidate_stats["system.maa.port_cache_RD_packets"]
        < control_stats["system.maa.port_cache_RD_packets"]
        and candidate_stats["IND_SoaJitValueHits"] > 0
    )
    performance_improved = (
        candidate_stats["simTicks"] < control_stats["simTicks"]
    )
    return (
        "ACCEPT_TRAFFIC_AND_PERFORMANCE"
        if traffic_reduced and performance_improved
        else "REJECT_NO_MATCHED_BENEFIT"
    )


def classify_publisher_pingpong_pair(serial: dict, pingpong: dict) -> str:
    """Accept only a work-conserving overlap mechanism with lower simTicks."""
    serial_stats = serial["stats"]
    pingpong_stats = pingpong["stats"]
    conserved_names = (
        "IND_SoaJitInstructions",
        "IND_SoaJitTerminalCompletions",
        "IND_SoaJitSelected",
        "IND_SoaJitAliasesApplied",
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
        "IND_SoaJitPageFedStateByteOperations",
        "IND_SoaJitEpochDrains",
        "IND_BoundedGlobalMergeFallbacks",
        "STR_PublishIssues",
        "STR_PublishAccepts",
        "STR_PublishWriteResponses",
        "STR_PublishTerminals",
        "STR_PublishRetries",
    )
    if any(
        serial_stats[name] != pingpong_stats[name] for name in conserved_names
    ):
        raise RuntimeError("publisher ping-pong pair changed conserved work")
    overlap_proven = (
        serial_stats["STR_PublishOverlapIssues"] == 0
        and pingpong_stats["STR_PublishOverlapIssues"] > 0
    )
    performance_improved = (
        pingpong_stats["simTicks"] < serial_stats["simTicks"]
    )
    return (
        "ACCEPT_OVERLAP_AND_PERFORMANCE"
        if overlap_proven and performance_improved
        else "REJECT_NO_MATCHED_BENEFIT"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--cg-na", type=int, default=DEFAULT_CG_NA)
    pair = parser.add_mutually_exclusive_group()
    pair.add_argument(
        "--value-cache-pair",
        action="store_true",
        help=(
            "hold direct4/q16 fixed and compare the bounded value cache "
            "disabled versus enabled"
        ),
    )
    pair.add_argument(
        "--publisher-pingpong-pair",
        action="store_true",
        help=(
            "hold the bounded value cache on and compare serial direct4 "
            "against disjoint four-tile publisher ping-pong"
        ),
    )
    args = parser.parse_args(argv)
    if not 1 <= args.cg_na <= MAX_CG_NA:
        parser.error(f"CG_NA must be in 1..{MAX_CG_NA}; full CG is forbidden")
    if args.publisher_pingpong_pair and args.cg_na > MAX_PINGPONG_CG_NA:
        parser.error(
            "publisher ping-pong evidence is bounded to "
            f"CG_NA<={MAX_PINGPONG_CG_NA}; full CG is forbidden"
        )
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
    initial_treatment = (
        "direct4_product_page_fed_q16"
        if args.value_cache_pair or args.publisher_pingpong_pair
        else "page_fed_product_soa_jit"
    )
    selector.write_text(f"token_stream_ld {initial_treatment}\n")
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
    if args.publisher_pingpong_pair:
        arm_specs = PUBLISHER_PINGPONG_TREATMENTS
    elif args.value_cache_pair:
        arm_specs = VALUE_CACHE_TREATMENTS
    else:
        arm_specs = SELECTED_TREATMENTS
    for arm_name, treatment, value_cache in arm_specs:
        selector.chmod(0o644)
        selector.write_text(f"token_stream_ld {treatment}\n")
        selector.chmod(0o444)
        arm = out / arm_name
        arm.mkdir()
        (arm / "selector.txt").write_text(selector.read_text())
        restore = restore_args(
            guest, selector, checkpoint, arm, value_cache=value_cache
        )
        restore_commands[arm_name] = restore
        base.run_logged(restore, arm / "restore.log", environment)
        parsed[arm_name] = parse_arm(
            arm, cg_na, treatment, value_cache=value_cache
        )

    if args.publisher_pingpong_pair:
        control_name = "serial"
        candidate_name = "pingpong"
    elif args.value_cache_pair:
        control_name = "cache_off"
        candidate_name = "cache_on"
    else:
        control_name = "control"
        candidate_name = "direct4_q16"
    control = parsed[control_name]
    candidate = parsed[candidate_name]
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
    if args.value_cache_pair:
        if control["terminal"] != candidate["terminal"]:
            raise RuntimeError("value-cache pair changed the guest mechanism")
        if normalized_cache_pair_config(out / "cache_off/config.ini") != (
            normalized_cache_pair_config(out / "cache_on/config.ini")
        ):
            raise RuntimeError(
                "value-cache pair has a non-treatment config difference"
            )
    elif args.publisher_pingpong_pair:
        if normalized_direct4_terminal(control["terminal"]) != (
            normalized_direct4_terminal(candidate["terminal"])
        ):
            raise RuntimeError(
                "publisher ping-pong changed the guest mechanism"
            )
        if normalized_cache_pair_config(out / "serial/config.ini") != (
            normalized_cache_pair_config(out / "pingpong/config.ini")
        ):
            raise RuntimeError(
                "publisher ping-pong pair has a non-treatment config difference"
            )

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
    decision = None
    if args.value_cache_pair:
        decision = classify_value_cache_pair(control, candidate)
    elif args.publisher_pingpong_pair:
        decision = classify_publisher_pingpong_pair(control, candidate)
    result = {
        "schema": (
            "dx100.cg.direct4_q16_publisher_pingpong.v1"
            if args.publisher_pingpong_pair
            else (
                "dx100.cg.direct4_q16_value_cache.v1"
                if args.value_cache_pair
                else "dx100.cg.direct4_product_page_fed_q16.v1"
            )
        ),
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
        "isolated_treatment": (
            "direct4_four_tile_group_pingpong"
            if args.publisher_pingpong_pair
            else (
                "soa_jit_value_cache_enable"
                if args.value_cache_pair
                else "direct4_product_page_fed_q16"
            )
        ),
        "fingerprint_raw_and_quantized_exact_equal": True,
        "deterministic_reduction_records": 11,
        "deterministic_reduction_bits_exact_equal": True,
        "p16_reorder_preserved_by_candidate": False,
        "q16_reorder_preserved_by_candidate": True,
        "selected_value_cache_enable": True,
        "performance": {
            "metric": "simTicks",
            "control": control_ticks,
            candidate_name: candidate_ticks,
            "control_over_candidate_speedup": control_ticks / candidate_ticks,
        },
        "arms": parsed,
    }
    result["hardware_accounting"] = {
        "physical_spd_payload_bytes": 524288,
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
    }
    if args.value_cache_pair:
        result["decision"] = decision
        result["traffic"] = {
            "metric": "first_roi_cache_read_packets",
            "cache_off": control["stats"]["system.maa.port_cache_RD_packets"],
            "cache_on": candidate["stats"]["system.maa.port_cache_RD_packets"],
            "value_read_issues_off": control["stats"][
                "IND_SoaJitValueReadIssues"
            ],
            "value_read_issues_on": candidate["stats"][
                "IND_SoaJitValueReadIssues"
            ],
        }
    elif args.publisher_pingpong_pair:
        result["decision"] = decision
        result["publisher_overlap"] = {
            "metric": "STR_PublishOverlapIssues",
            "serial": control["stats"]["STR_PublishOverlapIssues"],
            "pingpong": candidate["stats"]["STR_PublishOverlapIssues"],
            "serial_retries": control["stats"]["STR_PublishRetries"],
            "pingpong_retries": candidate["stats"]["STR_PublishRetries"],
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
    decision_line = f"decision={decision}\n" if decision is not None else ""
    (out / "gate.complete").write_text(
        "COMPLETE_CG_DIRECT4_PRODUCT_PAGE_FED_Q16\n"
        "correctness=EXACT_MATCH\n"
        + decision_line
        + f"raw_root_sha256={ledger_sha}\n"
    )
    print(
        json.dumps(
            {
                "terminal": True,
                "cg_na": cg_na,
                "correctness": "EXACT_MATCH",
                "decision": decision,
                "raw_root_sha256": ledger_sha,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
