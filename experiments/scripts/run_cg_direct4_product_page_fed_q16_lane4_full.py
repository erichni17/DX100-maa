#!/usr/bin/env python3
"""Run one fail-closed full direct4/q16 cache-on lane-4 CG candidate.

The runner compiles one CG_NA=150000 guest, creates one deferred checkpoint,
and restores exactly one direct4_product_page_fed_q16 candidate.  It never
runs native, lane 1, cache-off, or another control.  Four apply lanes are
selected from the already provisioned four-owner pool, so the treatment adds
no payload bytes, control bytes, or ports.  The accepted tolerant full-CG
certificate remains the numerical authority.  The frozen accepted lane-1
cache-on full result is opened and compared only after the lane-4 candidate
has independently passed every terminal, numerical, mechanism, provenance,
immutability, source-stability, value-retention, and active-lane gate.
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
    ROOT / "experiments/scripts/run_cg_direct4_product_page_fed_q16_full.py"
)
SPEC = importlib.util.spec_from_file_location(
    "cg_direct4_full_gate", BASE_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load full-CG evidence gate: {BASE_PATH}")
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)

TREATMENT = "direct4_product_page_fed_q16"
CG_NA = 150_000
APPLY_LANES = 4
EXPECTED_WINDOWS = 10_960
EXPECTED_A_LINES = 57_491
EXPECTED_WORDS = 179_568_640
PHYSICAL_SPD_PAYLOAD_BYTES = 524_288
PRODUCT_BACKING_BYTES = 262_144
VIRTUAL_P_BACKING_BYTES = 0
COHERENT_Q_INDEX_BACKING_BYTES = 0
FIXED_VALUE_OWNER_LINES = 128
ACTIVE_VALUE_OWNER_LINES = 32
VALUE_OWNER_LINE_BYTES = 64
INDIRECT_UNITS_PER_MAA = 4
FIXED_APPLY_LANES_PER_UNIT = 4
FIXED_APPLY_LANE_OWNER_BYTES = 32
FIXED_APPLY_LANE_POOL_BYTES_PER_UNIT = 144

LANE_SELECTION_ROOT = Path(
    "/data1/nier/worktrees/codex-coordination/sessions/"
    "hybrid-q16-retained-apply-lanes-20260826-20260826-131017-d5eb8ebb/"
    "evidence/direct4-q16-apply-lanes-na1024-r1"
)
LANE_SELECTION_HASHES = {
    "result.json": (
        "09616dbab874248b2c35f1f0b7c2912468d13f1ed7f822ac9603a97d126938f2"
    ),
    "gate.complete": (
        "e2cd9822ee8e8ddfd161dcbd8c5af4dc0b821d09e96bbcdf81d64f07c42b3d25"
    ),
    "raw_root.sha256": (
        "a6834f858bca1b1db0c22a341c6eda180b6549d9075039e802ddd51a26b901c4"
    ),
}

ACCEPTED_LANE1_ROOT = Path(
    "/data1/nier/dx100-runs/"
    "2026-08-26-cg-direct4-product-page-fed-q16-value-cache-full-r2"
)
ACCEPTED_LANE1_SIMTICKS = 123_968_991_971
ACCEPTED_LANE1_HASHES = {
    "result.json": (
        "270edfd1d868c5fcd257582136e04f4da679dc0a0119efa75d39eeaa797068a4"
    ),
    "gate.complete": (
        "b0052444d917aab24b4adc7e85aa7fe9a38992abbd2650020a64d3429a380b98"
    ),
    "certified_artifacts.sha256": (
        "0781f4e8f6d9cf85449241357bb17b86aa14540a967236b4bf37b010594b22d2"
    ),
    "manifest.json": (
        "954acad6fe9c23cae958b2b2e88419f1c364dd4eed217739d85666b6ff4e54c5"
    ),
    "run/stats.txt": (
        "fef764f87f42feecc5de27fadded16dcd3e46a3d3c6d4f23214555e2a18b73af"
    ),
    "run/config.ini": (
        "ec55e4818cfe189a21e943b2892b05af95f9d4dedec5f0d040d6f4721f98d607"
    ),
}

EXTRA_STAT_NAMES = (
    "IND_SoaJitValueEvictions",
    "IND_SoaJitValueStalls",
    "IND_SoaJitValueCacheHighWater",
    "IND_SoaJitLookaheadStalls",
    "IND_SoaJitContextStalls",
    "IND_SoaJitActiveApplyLanes",
    "IND_SoaJitApplyLaneHighWater",
    "system.maa.port_cache_RD_packets",
    "system.maa.port_cache_WR_packets",
    "system.maa.cycles_TOTAL",
    "system.maa.cycles_INDRMW",
    "system.maa.I0_IND_CyclesRequest",
)
VALUE_RETENTION_IDENTITY_STATS = (
    "IND_SoaJitValueReadIssues",
    "IND_SoaJitValueReadResponses",
    "IND_SoaJitValueFills",
    "IND_SoaJitValueCachedResponses",
    "IND_SoaJitValueHits",
    "IND_SoaJitValueMergedWaiters",
    "IND_SoaJitValueDeliveries",
    "IND_SoaJitValueEvictions",
    "IND_SoaJitValueStalls",
    "IND_SoaJitValueCacheHighWater",
    "IND_SoaJitLookaheadStalls",
    "IND_SoaJitContextStalls",
    "system.maa.port_cache_RD_packets",
    "system.maa.port_cache_WR_packets",
)
CONSERVED_STATS = tuple(
    name for name in base.STAT_NAMES if name != "simTicks"
) + (
    "IND_SoaJitValueEvictions",
    "IND_SoaJitValueStalls",
    "IND_SoaJitValueCacheHighWater",
    "IND_SoaJitLookaheadStalls",
    "IND_SoaJitContextStalls",
    "system.maa.port_cache_RD_packets",
    "system.maa.port_cache_WR_packets",
)


class GateError(base.GateError):
    """A fail-closed lane-4 full-CG evidence gate rejected the run."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


def validate_lane_selection_authority() -> dict[str, object]:
    """Pin the bounded exact lane-4 selection before authorizing a full run."""
    for name, digest in LANE_SELECTION_HASHES.items():
        base.exact_hash(
            LANE_SELECTION_ROOT / name, digest, f"lane-selection {name}"
        )
    require(
        (LANE_SELECTION_ROOT / "gate.complete").read_text(encoding="utf-8")
        == "COMPLETE_CG_DIRECT4_PRODUCT_PAGE_FED_Q16\n"
        "correctness=EXACT_MATCH\n"
        "decision=ACCEPT_ALL_EXACT_FASTER_ARMS_CONFIRMED\n"
        "raw_root_sha256=" + LANE_SELECTION_HASHES["raw_root.sha256"] + "\n",
        "lane-selection gate changed",
    )
    result = json.loads(
        (LANE_SELECTION_ROOT / "result.json").read_text(encoding="utf-8")
    )
    performance = result.get("performance", {})
    hardware = result.get("hardware_accounting", {})
    require(
        result.get("schema")
        == "dx100.cg.direct4_q16_apply_lanes_confirmation.v1"
        and result.get("terminal") is True
        and result.get("cg_na") == 1024
        and result.get("same_guest_treatment") == TREATMENT
        and result.get("sole_knob_delta") == "maa_soa_jit_apply_lanes"
        and result.get("selected_value_cache_enable") is True,
        "lane-selection identity changed",
    )
    require(
        isinstance(performance, dict)
        and performance.get("baseline_arm") == "lane_1"
        and "lane_4" in performance.get("exact_faster_arms", []),
        "lane-4 was not an accepted exact-faster arm",
    )
    expected_hardware = {
        "physical_spd_payload_bytes": PHYSICAL_SPD_PAYLOAD_BYTES,
        "external_coherent_backing_bytes": PRODUCT_BACKING_BYTES,
        "virtual_p_backing_bytes": VIRTUAL_P_BACKING_BYTES,
        "coherent_q_index_backing_bytes": COHERENT_Q_INDEX_BACKING_BYTES,
        "new_payload_bytes": 0,
        "new_control_bytes": 0,
        "new_ports": 0,
        "fixed_value_owner_lines_per_unit": FIXED_VALUE_OWNER_LINES,
        "active_value_owner_lines_per_unit": ACTIVE_VALUE_OWNER_LINES,
        "line_bytes": VALUE_OWNER_LINE_BYTES,
        "indirect_units_per_maa": INDIRECT_UNITS_PER_MAA,
        "fixed_apply_lanes_per_indirect_unit": FIXED_APPLY_LANES_PER_UNIT,
        "fixed_apply_lane_owner_state_bytes": FIXED_APPLY_LANE_OWNER_BYTES,
        "fixed_apply_lane_pool_state_bytes_per_unit": (
            FIXED_APPLY_LANE_POOL_BYTES_PER_UNIT
        ),
        "incremental_apply_lane_pool_bytes_across_arms": 0,
    }
    require(
        isinstance(hardware, dict)
        and all(
            hardware.get(key) == value
            for key, value in expected_hardware.items()
        )
        and hardware.get("active_apply_lanes_by_arm", {}).get("lane_4")
        == APPLY_LANES,
        "lane-selection fixed-hardware accounting changed",
    )
    return {
        "root": str(LANE_SELECTION_ROOT),
        "result_sha256": LANE_SELECTION_HASHES["result.json"],
        "gate_sha256": LANE_SELECTION_HASHES["gate.complete"],
        "raw_root_sha256": LANE_SELECTION_HASHES["raw_root.sha256"],
        "decision": "ACCEPT_ALL_EXACT_FASTER_ARMS_CONFIRMED",
        "selected_lane": APPLY_LANES,
    }


def restore_command(
    guest: Path, selector: Path, checkpoint: Path, run: Path
) -> list[str]:
    args = base.restore_command(guest, selector, checkpoint, run)
    require(
        not any(
            value.startswith("--maa_soa_jit_apply_lanes=") for value in args
        ),
        "inherited restore unexpectedly selected apply lanes",
    )
    position = args.index("--cmd")
    args.insert(position, f"--maa_soa_jit_apply_lanes={APPLY_LANES}")
    return args


def validate_config(config: Path, expected_lanes: int = APPLY_LANES) -> None:
    base.validate_config(config)
    lines = config.read_text(errors="replace").splitlines()
    lane_lines = [
        line for line in lines if line.startswith("soa_jit_apply_lanes=")
    ]
    require(
        lane_lines == [f"soa_jit_apply_lanes={expected_lanes}"],
        "resolved configuration does not select lane "
        f"{expected_lanes} exactly once",
    )


def validate_stats_values(values: dict[str, int]) -> None:
    """Require complete full work, retained values, and four-lane use."""
    base.validate_stats_values(values)
    a_names = (
        "IND_SoaJitAReadIssues",
        "IND_SoaJitAReadResponses",
        "IND_SoaJitAWriteIssues",
        "IND_SoaJitAWriteResponses",
    )
    require(
        all(values.get(name) == EXPECTED_A_LINES for name in a_names),
        "exact full A-line closure failed",
    )
    instructions = values["IND_SoaJitInstructions"]
    require(
        instructions == EXPECTED_WINDOWS
        and values["IND_SoaJitActiveApplyLanes"] == instructions * APPLY_LANES
        and values["IND_SoaJitApplyLaneHighWater"]
        == instructions * APPLY_LANES,
        "four-lane active/high-water closure failed",
    )
    require(
        0
        < values["IND_SoaJitValueCacheHighWater"]
        <= instructions * ACTIVE_VALUE_OWNER_LINES,
        "bounded value-cache high-water closure failed",
    )
    require(
        values["IND_SoaJitValueEvictions"] >= 0
        and values["IND_SoaJitValueStalls"] == 0
        and values["IND_SoaJitLookaheadStalls"] == 0
        and values["IND_SoaJitContextStalls"] == 0,
        "value-retention stall closure failed",
    )


def validate_stats(stats: Path) -> dict[str, int]:
    require(
        stats.is_file() and stats.stat().st_size > 0, "missing final stats"
    )
    names = base.STAT_NAMES + EXTRA_STAT_NAMES
    values = {name: base.first_stat_sum(stats, name) for name in names}
    validate_stats_values(values)
    return values


def validate_restore(
    run: Path, authority_fields: dict[str, str]
) -> tuple[dict[str, object], dict[str, float]]:
    candidate, deltas = base.validate_restore(run, authority_fields)
    validate_config(run / "config.ini")
    candidate["stats"] = validate_stats(run / "stats.txt")
    return candidate, deltas


def read_accepted_lane1_after_pass(
    gate: str,
) -> tuple[dict[str, object], dict[str, int]]:
    """Open frozen lane-1 full only after independent candidate PASS."""
    require(
        gate == "PASS_NUMERICAL_MECHANISM_CORRECT",
        "refusing accepted lane-1 comparison before PASS",
    )
    for name, digest in ACCEPTED_LANE1_HASHES.items():
        base.exact_hash(
            ACCEPTED_LANE1_ROOT / name, digest, f"accepted lane-1 {name}"
        )
    require(
        (ACCEPTED_LANE1_ROOT / "gate.complete").read_text(encoding="utf-8")
        == "PASS_NUMERICAL_MECHANISM_CORRECT\n"
        "observations=1\n"
        "result_sha256=" + ACCEPTED_LANE1_HASHES["result.json"] + "\n"
        "certified_artifacts_sha256="
        + ACCEPTED_LANE1_HASHES["certified_artifacts.sha256"]
        + "\n",
        "accepted lane-1 gate changed",
    )
    result = json.loads(
        (ACCEPTED_LANE1_ROOT / "result.json").read_text(encoding="utf-8")
    )
    performance = result.get("performance", {})
    require(
        result.get("schema")
        == "dx100.cg.direct4_product_page_fed_q16_full_result.v1"
        and result.get("terminal") is True
        and result.get("gate") == "PASS_NUMERICAL_MECHANISM_CORRECT"
        and result.get("candidate_only") is True
        and result.get("p16_reorder_preserved") is False
        and result.get("q16_reorder_preserved") is True
        and result.get("selected_value_cache_enable") is True
        and isinstance(performance, dict)
        and performance.get("candidate") == ACCEPTED_LANE1_SIMTICKS,
        "accepted lane-1 result identity changed",
    )
    validate_config(ACCEPTED_LANE1_ROOT / "run/config.ini", expected_lanes=1)
    stats_path = ACCEPTED_LANE1_ROOT / "run/stats.txt"
    baseline_names = tuple(
        dict.fromkeys(
            CONSERVED_STATS
            + (
                "simTicks",
                "IND_SoaJitActiveApplyLanes",
                "IND_SoaJitApplyLaneHighWater",
            )
        )
    )
    stats = {
        name: base.first_stat_sum(stats_path, name) for name in baseline_names
    }
    require(
        stats["simTicks"] == ACCEPTED_LANE1_SIMTICKS
        and stats["IND_SoaJitActiveApplyLanes"] == EXPECTED_WINDOWS
        and stats["IND_SoaJitApplyLaneHighWater"] == EXPECTED_WINDOWS,
        "accepted lane-1 raw stats identity changed",
    )
    recorded = result.get("candidate", {}).get("stats", {})
    require(
        isinstance(recorded, dict)
        and all(recorded.get(name) == stats[name] for name in base.STAT_NAMES),
        "accepted lane-1 result/raw-stats mismatch",
    )
    return result, stats


def compare_after_pass(
    gate: str, candidate: dict[str, object]
) -> dict[str, object]:
    """Compare only a fully passing lane-4 candidate with frozen lane 1."""
    baseline_result, baseline_stats = read_accepted_lane1_after_pass(gate)
    candidate_stats = candidate.get("stats", {})
    require(
        isinstance(candidate_stats, dict), "candidate stats are not a mapping"
    )
    require(
        all(
            candidate_stats.get(name) == baseline_stats[name]
            for name in CONSERVED_STATS
        ),
        "lane-4 candidate changed conserved/value-retention identity",
    )
    require(
        candidate.get("terminal")
        == baseline_result.get("candidate", {}).get("terminal"),
        "lane-4 candidate changed terminal work/geometry identity",
    )
    candidate_ticks = candidate_stats.get("simTicks")
    require(
        isinstance(candidate_ticks, int) and candidate_ticks > 0,
        "candidate simTicks is not positive",
    )
    return {
        "metric": "first_roi_simTicks",
        "baseline_arm": "accepted_lane_1_cache_on_full",
        "candidate_arm": "lane_4_cache_on_full",
        "accepted_lane_1": ACCEPTED_LANE1_SIMTICKS,
        "candidate": candidate_ticks,
        "lane_1_over_lane_4_ratio": ACCEPTED_LANE1_SIMTICKS / candidate_ticks,
        "lane_4_tick_reduction_fraction": (
            ACCEPTED_LANE1_SIMTICKS - candidate_ticks
        )
        / ACCEPTED_LANE1_SIMTICKS,
        "conserved_stats_exact": True,
        "value_retention_identity_exact": all(
            candidate_stats[name] == baseline_stats[name]
            for name in VALUE_RETENTION_IDENTITY_STATS
        ),
        "baseline": {
            "root": str(ACCEPTED_LANE1_ROOT),
            "result_sha256": ACCEPTED_LANE1_HASHES["result.json"],
            "gate_sha256": ACCEPTED_LANE1_HASHES["gate.complete"],
            "stats_sha256": ACCEPTED_LANE1_HASHES["run/stats.txt"],
            "resolved_apply_lanes": 1,
        },
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
    base.exact_hash(
        base.NATIVE_LOG, base.NATIVE_LOG_SHA256, "numerical authority"
    )
    base.exact_hash(
        base.NATIVE_STATS, base.NATIVE_STATS_SHA256, "authority stats"
    )
    certificate_identity = base.validate_certificate()
    selection_identity = validate_lane_selection_authority()
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
    selector = input_dir / "direct4_product_page_fed_q16.selector"
    selector.write_text(f"token_stream_ld {TREATMENT}\n", encoding="utf-8")
    selector.chmod(0o444)
    header = input_dir / "cg_data_4C.h"
    subprocess.run(
        ["cp", "--reflink=auto", str(base.FROZEN_HEADER), str(header)],
        check=True,
    )
    header.chmod(0o444)
    base.exact_hash(
        header, base.FROZEN_HEADER_SHA256, "copied precomputed CG header"
    )
    require(
        header.stat().st_size == base.FROZEN_HEADER_BYTES,
        "copied header size mismatch",
    )

    guest = bin_dir / "cg_direct4_product_page_fed_q16_lane4_full"
    compile_args = base.compile_command(guest, input_dir)
    checkpoint_args = base.checkpoint_command(guest, selector, checkpoint)
    restore_args = restore_command(guest, selector, checkpoint, run)
    require(
        restore_args.count("--maa_soa_jit_value_cache_enable") == 1
        and restore_args.count("--maa_soa_jit_active_value_owners=32") == 1
        and restore_args.count("--maa_num_tiles_per_core=8") == 1
        and restore_args.count("--maa_soa_jit_apply_lanes=4") == 1,
        "candidate restore treatment is not exact",
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

    selection_files = tuple(
        LANE_SELECTION_ROOT / name for name in sorted(LANE_SELECTION_HASHES)
    )
    immutable_artifacts = (
        base.GEM5,
        base.RAMULATOR,
        guest,
        selector,
        header,
        base.NATIVE_LOG,
        base.NATIVE_STATS,
        *(
            base.CERTIFICATE_ROOT / name
            for name in sorted(base.CERTIFICATE_FILES)
        ),
        *selection_files,
        Path(__file__).resolve(),
        BASE_PATH,
        *base.GUEST_COMPILE_INPUTS,
        *base.CONFIG_INPUTS,
    )
    artifacts_before = base.artifact_ledger(immutable_artifacts)
    (input_dir / "artifact_sha256.before").write_text(
        artifacts_before, encoding="utf-8"
    )
    (input_dir / "source_status.before").write_text(
        before_status, encoding="utf-8"
    )
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

    hardware_accounting = {
        "physical_spd_payload_bytes": PHYSICAL_SPD_PAYLOAD_BYTES,
        "external_product_backing_bytes": PRODUCT_BACKING_BYTES,
        "virtual_p_backing_bytes": VIRTUAL_P_BACKING_BYTES,
        "coherent_q_index_backing_bytes": COHERENT_Q_INDEX_BACKING_BYTES,
        "new_payload_bytes": 0,
        "new_control_bytes": 0,
        "new_ports": 0,
        "fixed_value_owner_lines_per_unit": FIXED_VALUE_OWNER_LINES,
        "active_value_owner_lines_per_unit": ACTIVE_VALUE_OWNER_LINES,
        "value_owner_line_bytes": VALUE_OWNER_LINE_BYTES,
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
        "active_apply_lanes_per_indirect_unit": APPLY_LANES,
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
        "incremental_apply_lane_pool_bytes_vs_lane_1": 0,
    }
    manifest = {
        "schema": "dx100.cg.direct4_product_page_fed_q16_lane4_full.v1",
        "terminal": False,
        "candidate_only": True,
        "guest_runs": 1,
        "native_runs": 0,
        "lane_1_runs": 0,
        "cache_off_runs": 0,
        "other_candidate_runs": 0,
        "trace": "disabled",
        "timeout": "none",
        "source_base_commit": base.SOURCE_BASE_COMMIT,
        "source_commit": before_commit,
        "cg_na": CG_NA,
        "selector": TREATMENT,
        "p16_reorder_preserved": False,
        "q16_reorder_preserved": True,
        "geometry": {
            "cores": 4,
            "tiles_per_core": 8,
            "logical_tile_elements": 16384,
            "physical_tile_elements": 4096,
            "physical_spd_payload_bytes": PHYSICAL_SPD_PAYLOAD_BYTES,
            "external_product_backing_bytes": PRODUCT_BACKING_BYTES,
            "virtual_p_backing_bytes": VIRTUAL_P_BACKING_BYTES,
            "coherent_q_index_backing_bytes": COHERENT_Q_INDEX_BACKING_BYTES,
        },
        "hardware_accounting": hardware_accounting,
        "precomputed_header": {
            "source": str(base.FROZEN_HEADER),
            "sha256": base.FROZEN_HEADER_SHA256,
            "bytes": base.FROZEN_HEADER_BYTES,
        },
        "lane_selection_authority": selection_identity,
        "numerical_authority": certificate_identity,
        "post_pass_comparison": {
            "root": str(ACCEPTED_LANE1_ROOT),
            "expected_simTicks": ACCEPTED_LANE1_SIMTICKS,
            "result_sha256": ACCEPTED_LANE1_HASHES["result.json"],
            "read_only_after_candidate_pass": True,
        },
        "commands": {
            "compile": compile_args,
            "checkpoint": checkpoint_args,
            "restore": restore_args,
        },
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # The one checkpoint and one candidate restore begin only after identities,
    # commands and immutable ledgers are recorded.  Neither has a timeout.
    base.run_logged(checkpoint_args, out / "checkpoint.log", environment)
    checkpoint_lines = (
        (out / "checkpoint.log").read_text(errors="replace").splitlines()
    )
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

    # Every independent candidate gate precedes the lane-1 read/comparison and
    # both terminal outputs.  A failed candidate therefore emits no comparison.
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
    (input_dir / "artifact_sha256.after").write_text(
        artifacts_after, encoding="utf-8"
    )
    require(artifacts_after == artifacts_before, "immutable artifact changed")
    after_status = base.source_status()
    after_commit = base.source_commit()
    (input_dir / "source_status.after").write_text(
        after_status, encoding="utf-8"
    )
    (input_dir / "source_commit.after").write_text(
        after_commit + "\n", encoding="utf-8"
    )
    require(after_status == before_status, "source status changed during run")
    require(after_commit == before_commit, "source commit changed during run")
    base.validate_certificate()
    validate_lane_selection_authority()
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
    performance = compare_after_pass(gate, candidate)
    result: dict[str, object] = {
        "schema": "dx100.cg.direct4_product_page_fed_q16_lane4_full_result.v1",
        "terminal": True,
        "gate": gate,
        "candidate_only": True,
        "observations": 1,
        "native_runs": 0,
        "lane_1_runs": 0,
        "cache_off_runs": 0,
        "official_nas_verification": False,
        "native_speedup_claim": False,
        "full_promotion_claim": False,
        "p16_reorder_preserved": False,
        "q16_reorder_preserved": True,
        "selected_value_cache_enable": True,
        "selected_apply_lanes": APPLY_LANES,
        "hardware_accounting": hardware_accounting,
        "source_commit": before_commit,
        "gem5_sha256": base.GEM5_SHA256,
        "ramulator_sha256": base.RAMULATOR_SHA256,
        "guest_sha256": base.sha256_file(guest),
        "checkpoint_ledger_sha256": hashlib.sha256(
            checkpoint_before.encode()
        ).hexdigest(),
        "lane_selection_authority": selection_identity,
        "numerical_authority": certificate_identity,
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
                "simTicks": performance["candidate"],
                "lane_1_over_lane_4_ratio": performance[
                    "lane_1_over_lane_4_ratio"
                ],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
