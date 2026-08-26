#!/usr/bin/env python3
"""Run one bounded CG direct4-product/q16 candidate pair.

One deterministic-reduction guest and one deferred checkpoint feed the matched
serial page-fed control and direct4-product/q16 treatment.  The default is
CG_NA=1024; bounded explicit sizes are supported through --cg-na.  There is no
native or full run, no timeout, and no per-access trace.  The optional cache
pair modes hold either direct4/q16 or page-fed p16/q16 fixed and isolate
retention in the already provisioned bounded SoA/JIT value-owner pool.  The
apply-lane modes hold direct4/q16 and value retention fixed while selecting
one, two, or four active lanes from the same fixed four-lane hardware pool.
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
SELECTED_TREATMENTS = (
    ("control", "page_fed_product_soa_jit", False),
    ("direct4_q16", "direct4_product_page_fed_q16", True),
)
VALUE_CACHE_TREATMENTS = (
    ("cache_off", "direct4_product_page_fed_q16", False),
    ("cache_on", "direct4_product_page_fed_q16", True),
)
PAGE_FED_VALUE_CACHE_TREATMENTS = (
    ("cache_off", "page_fed_product_soa_jit", False),
    ("cache_on", "page_fed_product_soa_jit", True),
)
APPLY_LANE_SWEEP_TREATMENTS = (
    ("lane_1", "direct4_product_page_fed_q16", True, 1),
    ("lane_2", "direct4_product_page_fed_q16", True, 2),
    ("lane_4", "direct4_product_page_fed_q16", True, 4),
)
FIXED_VALUE_OWNER_LINES = 128
ACTIVE_VALUE_OWNER_LINES = 32
VALUE_OWNER_LINE_BYTES = 64
INDIRECT_UNITS_PER_MAA = 4
FIXED_APPLY_LANES_PER_UNIT = 4
FIXED_APPLY_LANE_OWNER_BYTES = 32
FIXED_APPLY_LANE_POOL_BYTES_PER_UNIT = 144
_expected_value_cache: bool | None = None
_expected_apply_lanes: int | None = None

APPLY_LANE_CONSERVED_STATS = (
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
    "IND_SoaJitEpochDrains",
    "IND_BoundedGlobalMergeFallbacks",
    "STR_PublishIssues",
    "STR_PublishAccepts",
    "STR_PublishWriteResponses",
    "STR_PublishTerminals",
    "IND_SoaJitPageFedCommandResponses",
    "IND_SoaJitPageFedAdmittedWords",
    "IND_SoaJitPageFedSpdIndexReads",
    "IND_SoaJitPageFedRowWrites",
    "IND_SoaJitPageFedCoherentIndexReadLines",
    "IND_SoaJitPageFedCoherentIndexWriteLines",
    "IND_SoaJitPageFedStateByteOperations",
    "system.maa.port_cache_WR_packets",
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
    if _expected_apply_lanes is not None:
        expected = f"soa_jit_apply_lanes={_expected_apply_lanes}"
        lane_lines = [
            line for line in lines if line.startswith("soa_jit_apply_lanes=")
        ]
        if lane_lines != [expected]:
            raise RuntimeError(
                f"expected exactly one {expected}, saw {lane_lines!r}"
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
        "IND_SoaJitValueEvictions",
        "IND_SoaJitValueStalls",
        "IND_SoaJitValueCacheHighWater",
        "IND_SoaJitLookaheadStalls",
        "IND_SoaJitContextStalls",
        "IND_SoaJitActiveApplyLanes",
        "IND_SoaJitApplyLaneHighWater",
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
    if _expected_apply_lanes is not None:
        instructions = values["IND_SoaJitInstructions"]
        active_lane_sum = values["IND_SoaJitActiveApplyLanes"]
        apply_hwm_sum = values["IND_SoaJitApplyLaneHighWater"]
        lane_closed = (
            instructions == windows
            and active_lane_sum == instructions * _expected_apply_lanes
            and instructions <= apply_hwm_sum
            and apply_hwm_sum <= instructions * _expected_apply_lanes
        )
        if _expected_apply_lanes > 1:
            lane_closed = lane_closed and apply_hwm_sum > instructions
        if not lane_closed:
            raise RuntimeError(
                "apply-lane parallelism closure failed: "
                f"lanes={_expected_apply_lanes} instructions={instructions} "
                f"active_sum={active_lane_sum} hwm_sum={apply_hwm_sum}"
            )
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
    apply_lanes: int | None = None,
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
    global _expected_apply_lanes, _expected_value_cache
    _expected_value_cache = value_cache
    _expected_apply_lanes = apply_lanes
    try:
        return base.parse_arm(arm, cg_na, treatment, True)
    finally:
        _expected_value_cache = None
        _expected_apply_lanes = None


def restore_args(
    guest: Path,
    selector: Path,
    checkpoint: Path,
    arm: Path,
    value_cache: bool = False,
    apply_lanes: int | None = None,
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
    if apply_lanes is not None:
        if apply_lanes not in (1, 2, 4):
            raise RuntimeError(
                f"invalid active apply-lane count {apply_lanes}"
            )
        args.append(f"--maa_soa_jit_apply_lanes={apply_lanes}")
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


def normalized_cache_pair_command(command: list[str]) -> list[str]:
    """Normalize the arm output path and remove only the cache treatment."""
    normalized = []
    for value in command:
        if value.startswith("--outdir="):
            normalized.append("--outdir=<ARM>")
        elif value != "--maa_soa_jit_value_cache_enable":
            normalized.append(value)
    return normalized


def normalized_apply_lane_config(config: Path) -> str:
    """Normalize only the declared active-lane count and arm redirect paths."""
    normalized = []
    for line in config.read_text(errors="replace").splitlines():
        if line.startswith("soa_jit_apply_lanes="):
            normalized.append("soa_jit_apply_lanes=<TREATMENT>")
        elif line.startswith("host_paths=") and "/fs/" in line:
            normalized.append(
                "host_paths=<ARM>/fs/" + line.rsplit("/fs/", 1)[1]
            )
        else:
            normalized.append(line)
    return "\n".join(normalized) + "\n"


def normalized_apply_lane_command(command: list[str]) -> list[str]:
    """Normalize the arm path and exactly one active-lane treatment value."""
    normalized = []
    for value in command:
        if value.startswith("--outdir="):
            normalized.append("--outdir=<ARM>")
        elif value.startswith("--maa_soa_jit_apply_lanes="):
            normalized.append("--maa_soa_jit_apply_lanes=<TREATMENT>")
        else:
            normalized.append(value)
    return normalized


def verify_raw_root(root: Path) -> str:
    """Revalidate one frozen raw-root ledger before using it as a gate."""
    root = root.resolve()
    ledger = root / "raw_root.sha256"
    gate = root / "gate.complete"
    if not ledger.is_file() or not gate.is_file():
        raise RuntimeError(f"incomplete confirmation root: {root}")
    seen: set[Path] = set()
    for number, line in enumerate(ledger.read_text().splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            raise RuntimeError(f"malformed raw ledger line {number}")
        relative = Path(match.group(2))
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative in seen
        ):
            raise RuntimeError(
                f"unsafe or duplicate raw ledger path {relative}"
            )
        seen.add(relative)
        artifact = root / relative
        if not artifact.is_file() or base.sha256_file(artifact) != match.group(
            1
        ):
            raise RuntimeError(f"raw ledger mismatch for {relative}")
    if Path("result.json") not in seen:
        raise RuntimeError("raw ledger does not cover result.json")
    ledger_sha = base.sha256_file(ledger)
    gate_lines = gate.read_text().splitlines()
    if (
        gate_lines.count("COMPLETE_CG_DIRECT4_PRODUCT_PAGE_FED_Q16") != 1
        or gate_lines.count("correctness=EXACT_MATCH") != 1
        or gate_lines.count(f"raw_root_sha256={ledger_sha}") != 1
    ):
        raise RuntimeError("confirmation gate does not bind the raw ledger")
    return ledger_sha


def validate_confirmation_source(root: Path, lane: int) -> str:
    """Require a terminal NA=256 sweep that named this exact faster lane."""
    ledger_sha = verify_raw_root(root)
    result = json.loads((root.resolve() / "result.json").read_text())
    expected_arm = f"lane_{lane}"
    if not (
        result.get("schema") == "dx100.cg.direct4_q16_apply_lanes.v1"
        and result.get("terminal") is True
        and result.get("cg_na") == 256
        and result.get("decision") == "ACCEPT_EXACT_FASTER_ARM"
        and expected_arm
        in result.get("performance", {}).get("exact_faster_arms", [])
    ):
        raise RuntimeError(
            f"NA=1024 confirmation is not authorized for {expected_arm}"
        )
    return ledger_sha


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
        "--page-fed-value-cache-pair",
        action="store_true",
        help=(
            "hold page_fed_product_soa_jit fixed with p16/q16 preserved and "
            "compare the bounded value cache disabled versus enabled"
        ),
    )
    pair.add_argument(
        "--apply-lane-sweep",
        action="store_true",
        help=(
            "at CG_NA=256, hold cache-on direct4/q16 fixed and sweep active "
            "apply lanes 1, 2, and 4"
        ),
    )
    pair.add_argument(
        "--apply-lane-confirm",
        type=int,
        nargs="+",
        choices=(2, 4),
        help=(
            "at CG_NA=1024, compare lane 1 with only the listed exact faster "
            "lanes from one validated CG_NA=256 sweep"
        ),
    )
    parser.add_argument(
        "--confirm-from",
        type=Path,
        help="terminal CG_NA=256 apply-lane raw root authorizing confirmation",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.cg_na <= MAX_CG_NA:
        parser.error(f"CG_NA must be in 1..{MAX_CG_NA}; full CG is forbidden")
    if args.apply_lane_sweep and args.cg_na != 256:
        parser.error("the first apply-lane sweep must use CG_NA=256")
    if args.apply_lane_confirm is not None:
        if args.cg_na != 1024:
            parser.error("apply-lane confirmation must use CG_NA=1024")
        if args.confirm_from is None:
            parser.error("--apply-lane-confirm requires --confirm-from")
        if len(set(args.apply_lane_confirm)) != len(args.apply_lane_confirm):
            parser.error("apply-lane confirmation contains a duplicate lane")
    elif args.confirm_from is not None:
        parser.error("--confirm-from requires --apply-lane-confirm")
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

    apply_lane_mode = (
        args.apply_lane_sweep or args.apply_lane_confirm is not None
    )
    confirmation_source_ledger = None
    if args.apply_lane_confirm is not None:
        confirmation_ledgers = {
            validate_confirmation_source(args.confirm_from, lane)
            for lane in args.apply_lane_confirm
        }
        if len(confirmation_ledgers) != 1:
            raise RuntimeError(
                "confirmation lanes did not share one NA=256 root"
            )
        confirmation_source_ledger = confirmation_ledgers.pop()

    input_dir = out / "input"
    checkpoint = out / "checkpoint"
    input_dir.mkdir(parents=True)
    checkpoint.mkdir()
    guest = out / "cg_direct4_product_page_fed_q16_guest"
    selector = input_dir / "treatment.selector"
    value_cache_pair = args.value_cache_pair or args.page_fed_value_cache_pair
    initial_treatment = "page_fed_product_soa_jit"
    if args.value_cache_pair or apply_lane_mode:
        initial_treatment = "direct4_product_page_fed_q16"
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
    if args.apply_lane_sweep:
        arm_specs = APPLY_LANE_SWEEP_TREATMENTS
    elif args.apply_lane_confirm is not None:
        arm_specs = (APPLY_LANE_SWEEP_TREATMENTS[0],) + tuple(
            next(
                spec for spec in APPLY_LANE_SWEEP_TREATMENTS if spec[3] == lane
            )
            for lane in args.apply_lane_confirm
        )
    elif args.value_cache_pair:
        arm_specs = tuple((*spec, None) for spec in VALUE_CACHE_TREATMENTS)
    elif args.page_fed_value_cache_pair:
        arm_specs = tuple(
            (*spec, None) for spec in PAGE_FED_VALUE_CACHE_TREATMENTS
        )
    else:
        arm_specs = tuple((*spec, None) for spec in SELECTED_TREATMENTS)
    for arm_name, treatment, value_cache, apply_lanes in arm_specs:
        selector.chmod(0o644)
        selector.write_text(f"token_stream_ld {treatment}\n")
        selector.chmod(0o444)
        arm = out / arm_name
        arm.mkdir()
        (arm / "selector.txt").write_text(selector.read_text())
        restore = restore_args(
            guest,
            selector,
            checkpoint,
            arm,
            value_cache=value_cache,
            apply_lanes=apply_lanes,
        )
        restore_commands[arm_name] = restore
        base.run_logged(restore, arm / "restore.log", environment)
        parsed[arm_name] = parse_arm(
            arm,
            cg_na,
            treatment,
            value_cache=value_cache,
            apply_lanes=apply_lanes,
        )

    if apply_lane_mode:
        control_name = "lane_1"
        candidate_name = list(parsed)[-1]
    else:
        control_name = "cache_off" if value_cache_pair else "control"
        candidate_name = "cache_on" if value_cache_pair else "direct4_q16"
    control = parsed[control_name]
    candidate = parsed[candidate_name]
    fingerprint_equal = all(
        arm["fingerprint_line"] == control["fingerprint_line"]
        for arm in parsed.values()
    )
    reduction_equal = all(
        arm["reduction_evidence"] == control["reduction_evidence"]
        for arm in parsed.values()
    )
    if any(len(arm["reduction_evidence"]) != 11 for arm in parsed.values()):
        raise RuntimeError(
            "expected all 11 deterministic reduction records per arm"
        )
    if not fingerprint_equal or not reduction_equal:
        raise RuntimeError(
            "correctness mismatch; simTicks comparison forbidden"
        )
    if any(
        arm["terminal"]["full_windows"] != control["terminal"]["full_windows"]
        for arm in parsed.values()
    ):
        raise RuntimeError("treatment window counts differ")
    if value_cache_pair:
        expected_selector = f"token_stream_ld {arm_specs[0][1]}\n"
        for arm_name in (control_name, candidate_name):
            if (out / arm_name / "selector.txt").read_text() != (
                expected_selector
            ):
                raise RuntimeError(
                    "value-cache pair changed the deferred guest treatment"
                )
        if control["terminal"] != candidate["terminal"]:
            raise RuntimeError("value-cache pair changed the guest mechanism")
        if normalized_cache_pair_config(out / "cache_off/config.ini") != (
            normalized_cache_pair_config(out / "cache_on/config.ini")
        ):
            raise RuntimeError(
                "value-cache pair has a non-treatment config difference"
            )
        control_command = restore_commands[control_name]
        candidate_command = restore_commands[candidate_name]
        cache_option = "--maa_soa_jit_value_cache_enable"
        if control_command.count(cache_option) != 0:
            raise RuntimeError(
                "cache-off arm unexpectedly enables value cache"
            )
        if candidate_command.count(cache_option) != 1:
            raise RuntimeError(
                "cache-on arm lacks exactly one value-cache knob"
            )
        if normalized_cache_pair_command(control_command) != (
            normalized_cache_pair_command(candidate_command)
        ):
            raise RuntimeError(
                "value-cache pair has a non-treatment command difference"
            )
    if apply_lane_mode:
        expected_selector = "token_stream_ld direct4_product_page_fed_q16\n"
        control_config = normalized_apply_lane_config(
            out / f"{control_name}/config.ini"
        )
        control_command = normalized_apply_lane_command(
            restore_commands[control_name]
        )
        for arm_name, _, value_cache, apply_lanes in arm_specs:
            if not value_cache or apply_lanes not in (1, 2, 4):
                raise RuntimeError(
                    "apply-lane arm lost its fixed cache-on treatment"
                )
            if (
                out / arm_name / "selector.txt"
            ).read_text() != expected_selector:
                raise RuntimeError(
                    "apply-lane sweep changed the guest treatment"
                )
            if parsed[arm_name]["terminal"] != control["terminal"]:
                raise RuntimeError(
                    "apply-lane sweep changed the guest mechanism"
                )
            for name in APPLY_LANE_CONSERVED_STATS:
                if parsed[arm_name]["stats"][name] != control["stats"][name]:
                    raise RuntimeError(
                        f"apply-lane sweep changed conserved stat {name}"
                    )
            if normalized_apply_lane_config(
                out / f"{arm_name}/config.ini"
            ) != (control_config):
                raise RuntimeError(
                    "apply-lane sweep has a non-treatment config difference"
                )
            command = restore_commands[arm_name]
            lane_option = f"--maa_soa_jit_apply_lanes={apply_lanes}"
            if command.count("--maa_soa_jit_value_cache_enable") != 1:
                raise RuntimeError(
                    "apply-lane arm must enable value retention once"
                )
            if (
                command.count(lane_option) != 1
                or sum(
                    value.startswith("--maa_soa_jit_apply_lanes=")
                    for value in command
                )
                != 1
            ):
                raise RuntimeError(
                    "apply-lane arm lacks its sole exact lane knob"
                )
            if normalized_apply_lane_command(command) != control_command:
                raise RuntimeError(
                    "apply-lane sweep has a non-treatment command difference"
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
    value_cache_decision = None
    if value_cache_pair:
        value_cache_decision = classify_value_cache_pair(control, candidate)
    apply_lane_decision = None
    exact_faster_arms: list[str] = []
    if apply_lane_mode:
        exact_faster_arms = [
            name
            for name, arm in parsed.items()
            if name != "lane_1" and arm["stats"]["simTicks"] < control_ticks
        ]
        if args.apply_lane_confirm is not None:
            requested_arms = {
                f"lane_{lane}" for lane in args.apply_lane_confirm
            }
            apply_lane_decision = (
                "ACCEPT_ALL_EXACT_FASTER_ARMS_CONFIRMED"
                if set(exact_faster_arms) == requested_arms
                else "REJECT_UNCONFIRMED_ARM"
            )
        else:
            apply_lane_decision = (
                "ACCEPT_EXACT_FASTER_ARM"
                if exact_faster_arms
                else "REJECT_NO_EXACT_FASTER_ARM"
            )
    selected_treatment = arm_specs[0][1]
    page_fed_cache_pair = args.page_fed_value_cache_pair
    result = {
        "schema": (
            "dx100.cg.direct4_q16_apply_lanes_confirmation.v1"
            if args.apply_lane_confirm is not None
            else (
                "dx100.cg.direct4_q16_apply_lanes.v1"
                if args.apply_lane_sweep
                else (
                    "dx100.cg.page_fed_p16_q16_value_cache.v1"
                    if page_fed_cache_pair
                    else (
                        "dx100.cg.direct4_q16_value_cache.v1"
                        if args.value_cache_pair
                        else "dx100.cg.direct4_product_page_fed_q16.v1"
                    )
                )
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
            "soa_jit_apply_lanes"
            if apply_lane_mode
            else (
                "soa_jit_value_cache_enable"
                if value_cache_pair
                else "direct4_product_page_fed_q16"
            )
        ),
        "same_guest_treatment": (
            selected_treatment if value_cache_pair or apply_lane_mode else None
        ),
        "sole_knob_delta": (
            "maa_soa_jit_apply_lanes"
            if apply_lane_mode
            else ("soa_jit_value_cache_enable" if value_cache_pair else None)
        ),
        "fingerprint_raw_and_quantized_exact_equal": True,
        "deterministic_reduction_records": 11,
        "deterministic_reduction_bits_exact_equal": True,
        "p16_reorder_preserved_by_candidate": page_fed_cache_pair,
        "q16_reorder_preserved_by_candidate": True,
        "selected_value_cache_enable": True,
        "performance": (
            {
                "metric": "simTicks",
                "baseline_arm": "lane_1",
                "arms": {
                    name: arm["stats"]["simTicks"]
                    for name, arm in parsed.items()
                },
                "lane_1_over_arm_speedup": {
                    name: control_ticks / arm["stats"]["simTicks"]
                    for name, arm in parsed.items()
                    if name != "lane_1"
                },
                "exact_faster_arms": exact_faster_arms,
            }
            if apply_lane_mode
            else {
                "metric": "simTicks",
                "control": control_ticks,
                candidate_name: candidate_ticks,
                "control_over_candidate_speedup": control_ticks
                / candidate_ticks,
            }
        ),
        "arms": parsed,
    }
    if args.apply_lane_confirm is not None:
        result["confirmation"] = {
            "authorized_by_na256_root": str(args.confirm_from.resolve()),
            "authorized_raw_root_sha256": confirmation_source_ledger,
            "requested_lanes": args.apply_lane_confirm,
            "one_guest_and_checkpoint": True,
        }
    result["hardware_accounting"] = {
        "physical_spd_payload_bytes": 524288,
        "external_coherent_backing_bytes": int(
            candidate["terminal"]["external_coherent_backing_bytes"]
        ),
        "virtual_p_backing_bytes": int(
            candidate["terminal"]["virtual_p_backing_bytes"]
        ),
        "coherent_q_index_backing_bytes": int(
            candidate["terminal"]["coherent_index_backing_bytes"]
        ),
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
        "fixed_apply_lanes_per_indirect_unit": FIXED_APPLY_LANES_PER_UNIT,
        "fixed_apply_lane_owners_per_maa": (
            FIXED_APPLY_LANES_PER_UNIT * INDIRECT_UNITS_PER_MAA
        ),
        "fixed_apply_lane_owner_state_bytes": FIXED_APPLY_LANE_OWNER_BYTES,
        "fixed_apply_lane_pool_state_bytes_per_unit": (
            FIXED_APPLY_LANE_POOL_BYTES_PER_UNIT
        ),
        "fixed_apply_lane_pool_state_bytes_per_maa": (
            FIXED_APPLY_LANE_POOL_BYTES_PER_UNIT * INDIRECT_UNITS_PER_MAA
        ),
        "incremental_apply_lane_pool_bytes_across_arms": 0,
        "active_apply_lanes_by_arm": (
            {name: lanes for name, _, _, lanes in arm_specs}
            if apply_lane_mode
            else None
        ),
    }
    if value_cache_pair:
        result["decision"] = value_cache_decision
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
    if apply_lane_mode:
        result["decision"] = apply_lane_decision
        result["mechanism"] = {
            name: {
                "cycles_indrmw": arm["stats"]["system.maa.cycles_INDRMW"],
                "cycles_request": arm["stats"][
                    "system.maa.I0_IND_CyclesRequest"
                ],
                "value_read_issues": arm["stats"]["IND_SoaJitValueReadIssues"],
                "value_hits": arm["stats"]["IND_SoaJitValueHits"],
                "cache_read_packets": arm["stats"][
                    "system.maa.port_cache_RD_packets"
                ],
                "cache_write_packets": arm["stats"][
                    "system.maa.port_cache_WR_packets"
                ],
                "apply_high_water_sum": arm["stats"][
                    "IND_SoaJitApplyLaneHighWater"
                ],
                "instructions": arm["stats"]["IND_SoaJitInstructions"],
                "apply_high_water_mean": (
                    arm["stats"]["IND_SoaJitApplyLaneHighWater"]
                    / arm["stats"]["IND_SoaJitInstructions"]
                ),
            }
            for name, arm in parsed.items()
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
    decision = apply_lane_decision if apply_lane_mode else value_cache_decision
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
