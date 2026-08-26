#!/usr/bin/env python3
"""Matched NA=1024 serial/page-fed-product-overlap correctness gate.

The runner builds one deterministic-reduction guest, creates one deferred
checkpoint, and restores it for the old serial and new two-pass treatments.
It has no native/full arm and deliberately supplies no wall timeout.
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
BASE_RUNNER = (
    ROOT / "experiments/scripts/run_cg_page_fed_reduction_order_diagnosis.py"
)
BASE_SPEC = importlib.util.spec_from_file_location(
    "cg_reduction_base", BASE_RUNNER
)
if BASE_SPEC is None or BASE_SPEC.loader is None:
    raise RuntimeError(f"could not import matched-run base: {BASE_RUNNER}")
base = importlib.util.module_from_spec(BASE_SPEC)
BASE_SPEC.loader.exec_module(base)

SOURCE = ROOT / "benchmarks/NAS/cg/cg.cpp"
CONFIG = ROOT / "configs/deprecated/example/se.py"
RAMULATOR_CONFIG = ROOT / "ext/ramulator2/ramulator2/example_gem5_config.yaml"
RAMULATOR = Path(
    "/data1/nier/dx100-runs/2026-08-12-hybrid-line-handoff-8a5c7712/"
    "input/libramulator.so"
)
RAMULATOR_SHA256 = (
    "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753"
)
CG_NA = 1024
ARMS = (
    ("serial", "page_fed_product_soa_jit"),
    ("overlap", "page_fed_product_overlap_soa_jit"),
)


def require_product_overlap_mechanism(
    arm: Path, parsed: dict, treatment: str
) -> dict[str, int]:
    stats = arm / "stats.txt"
    names = (
        "IND_SoaJitPageFedProductReadySignals",
        "IND_SoaJitPageFedValueReadinessStalls",
        "IND_SoaJitPageFedFirstReadyTicks",
        "IND_SoaJitPageFedLastReadyTicks",
        "IND_SoaJitPageFedExecutionBeforeAllReady",
        "IND_SoaJitPageFedTerminalClosures",
    )
    values = {name: base.stat_sum(stats, name) for name in names}
    terminal = parsed["terminal"]
    windows = int(terminal["full_windows"])
    expected_pages = windows * 4
    common = (
        values["IND_SoaJitPageFedProductReadySignals"] == expected_pages
        and values["IND_SoaJitPageFedFirstReadyTicks"] > 0
        and values["IND_SoaJitPageFedLastReadyTicks"]
        >= values["IND_SoaJitPageFedFirstReadyTicks"]
        and values["IND_SoaJitPageFedTerminalClosures"] == windows
        and int(terminal["gather_q_overlap_attempts"]) == 0
        and int(terminal["physical_spd_payload_bytes"]) == 655360
    )
    if treatment == "page_fed_product_soa_jit":
        exact = (
            values["IND_SoaJitPageFedExecutionBeforeAllReady"] == 0
            and values["IND_SoaJitPageFedValueReadinessStalls"] == 0
            and int(terminal["page_fed_overlap_windows"]) == 0
            and int(terminal["gather_completion_waits"]) == 0
        )
    else:
        exact = (
            values["IND_SoaJitPageFedExecutionBeforeAllReady"] == windows
            and values["IND_SoaJitPageFedValueReadinessStalls"] > 0
            and int(terminal["page_fed_overlap_windows"]) == windows
            and int(terminal["gather_completion_waits"]) == windows
        )
    if not common or not exact:
        raise RuntimeError(
            f"product-overlap mechanism closure failed for {treatment}: "
            f"stats={values} terminal={terminal}"
        )
    return values


def compile_guest(guest: Path) -> None:
    args = [
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
        "-DCG_FP_ENABLE",
        "-DCG_DETERMINISTIC_REDUCTIONS",
        "-DCG_REDUCTION_EVIDENCE",
        f"-DCG_NA={CG_NA}",
        "-DNUM_CORES=4",
        "-DNUM_TILES_PER_CORE=10",
        "-DTILE_SIZE=16384",
        "-DMAA_MEM_SIZE=0x80000000",
        str(ROOT / "util/m5/src/abi/x86/m5op.S"),
        str(SOURCE),
        "-o",
        str(guest),
    ]
    subprocess.run(args, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument(
        "--gem5", type=Path, default=ROOT / "build/X86/gem5.opt"
    )
    args = parser.parse_args()
    out = args.out.resolve()
    gem5 = args.gem5.resolve()
    if out == ROOT or ROOT in out.parents:
        raise SystemExit("output must be outside the source worktree")
    if out.exists():
        raise SystemExit(f"refusing to overwrite output root: {out}")
    base.exact_hash(RAMULATOR, RAMULATOR_SHA256, "frozen Ramulator")
    if not gem5.is_file():
        raise SystemExit(f"missing candidate gem5: {gem5}")
    gem5_sha256 = base.sha256_file(gem5)

    before_status = base.source_status()
    before_commit = base.source_commit()
    input_dir = out / "input"
    checkpoint = out / "checkpoint"
    input_dir.mkdir(parents=True)
    checkpoint.mkdir()
    guest = out / "cg_page_fed_product_overlap_guest"
    selector = input_dir / "treatment.selector"
    selector.write_text("token_stream_ld page_fed_product_soa_jit\n")
    selector.chmod(0o444)
    compile_guest(guest)

    environment = os.environ.copy()
    environment["LD_LIBRARY_PATH"] = str(RAMULATOR.parent)
    ldd = subprocess.check_output(
        ["ldd", str(gem5)], env=environment, text=True
    )
    resolved = re.search(r"^[ \t]*libramulator\.so => (\S+)", ldd, re.M)
    if resolved is None or Path(resolved.group(1)).resolve() != RAMULATOR:
        raise RuntimeError("candidate gem5 did not resolve frozen Ramulator")

    immutable = (
        gem5,
        RAMULATOR,
        guest,
        *base.GUEST_COMPILE_INPUTS,
        *base.RUNNER_CONFIG_INPUTS,
        Path(__file__).resolve(),
    )
    (input_dir / "artifact_sha256.before").write_text(
        base.artifact_ledger(immutable)
    )
    (input_dir / "source_status.before").write_text(before_status)
    (input_dir / "source_commit.before").write_text(before_commit + "\n")

    checkpoint_args = [
        str(gem5),
        "--listener-mode=off",
        f"--outdir={checkpoint}",
        str(CONFIG),
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
    if any(
        line.startswith(("CG_FINGERPRINT ", "CG_LOGICAL16_RMW_TERMINAL "))
        for line in checkpoint_lines
    ):
        raise RuntimeError("checkpoint crossed deferred treatment boundary")
    checkpoint_before = base.tree_ledger(checkpoint)
    (input_dir / "checkpoint_files.before").write_text(checkpoint_before)

    base.GEM5 = gem5
    base.GEM5_SHA256 = gem5_sha256
    parsed: dict[str, dict] = {}
    commands: dict[str, list[str]] = {}
    mechanisms: dict[str, dict[str, int]] = {}
    for arm_name, treatment in ARMS:
        selector.chmod(0o644)
        selector.write_text(f"token_stream_ld {treatment}\n")
        selector.chmod(0o444)
        arm = out / arm_name
        arm.mkdir()
        (arm / "selector.txt").write_text(selector.read_text())
        command = base.restore_args(
            guest, selector, checkpoint, arm, page_fed=True
        )
        commands[arm_name] = command
        base.run_logged(command, arm / "restore.log", environment)
        parsed[arm_name] = base.parse_arm(arm, CG_NA, treatment, page_fed=True)
        mechanisms[arm_name] = require_product_overlap_mechanism(
            arm, parsed[arm_name], treatment
        )

    fingerprint_equal = (
        parsed["serial"]["fingerprint_line"]
        == parsed["overlap"]["fingerprint_line"]
    )
    reduction_equal = (
        parsed["serial"]["reduction_evidence"]
        == parsed["overlap"]["reduction_evidence"]
    )
    if not fingerprint_equal or not reduction_equal:
        raise RuntimeError("cross-arm fingerprint/reduction evidence mismatch")
    if (
        parsed["serial"]["terminal"]["full_windows"]
        != parsed["overlap"]["terminal"]["full_windows"]
    ):
        raise RuntimeError("cross-arm page-fed window count mismatch")
    allocation_fields = (
        "physical_spd_payload_bytes",
        "logical_scheduler_reserved_lanes",
        "logical_scheduler_reserved_lane_payload_bytes",
        "external_coherent_backing_bytes",
    )
    if any(
        parsed["serial"]["terminal"][field]
        != parsed["overlap"]["terminal"][field]
        for field in allocation_fields
    ):
        raise RuntimeError("cross-arm hardware allocation accounting mismatch")
    for name in parsed:
        config = (out / name / "config.ini").read_text(errors="replace")
        if config.splitlines().count("num_tiles_per_core=10") != 1:
            raise RuntimeError(f"{name}: not exactly one 10-tile allocation")

    checkpoint_after = base.tree_ledger(checkpoint)
    (input_dir / "checkpoint_files.after").write_text(checkpoint_after)
    if checkpoint_after != checkpoint_before:
        raise RuntimeError("shared checkpoint changed during restores")
    after_artifacts = base.artifact_ledger(immutable)
    (input_dir / "artifact_sha256.after").write_text(after_artifacts)
    if (input_dir / "artifact_sha256.before").read_text() != after_artifacts:
        raise RuntimeError("immutable artifact changed during matched pair")
    after_status = base.source_status()
    after_commit = base.source_commit()
    (input_dir / "source_status.after").write_text(after_status)
    (input_dir / "source_commit.after").write_text(after_commit + "\n")
    if after_status != before_status or after_commit != before_commit:
        raise RuntimeError("source identity changed during matched pair")

    # simTicks are exposed only after all correctness and closure gates above.
    result = {
        "schema": "dx100.cg.page_fed_product_overlap.v1",
        "terminal": True,
        "native_runs": 0,
        "full_cg_runs": 0,
        "timeout": "none",
        "cg_na": CG_NA,
        "source_commit": before_commit,
        "gem5_sha256": gem5_sha256,
        "ramulator_sha256": RAMULATOR_SHA256,
        "guest_sha256": base.sha256_file(guest),
        "checkpoint_ledger_sha256": hashlib.sha256(
            checkpoint_before.encode()
        ).hexdigest(),
        "gather_rowtable_overlap": "excluded_indirect_occupancy",
        "product_rmw_overlap": "validated",
        "allocation_accounting": {
            "configured_tiles_per_core": 10,
            "physical_spd_payload_bytes_per_candidate": 655360,
            "incremental_payload_bytes_vs_matched_serial": 0,
            "iso_area_vs_original_8_tile_dx100": False,
            "simulator_instrumentation_is_target_area": False,
        },
        "fingerprint_exact_equal": True,
        "reduction_evidence_exact_equal": True,
        "commands": commands,
        "mechanisms": mechanisms,
        "sim_ticks": {
            name: parsed[name]["stats"]["simTicks"] for name in parsed
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
    raw_ledger = "".join(
        f"{base.sha256_file(path)}  {path.relative_to(out)}\n"
        for path in ledger_targets
    )
    (out / "raw_root.sha256").write_text(raw_ledger)
    ledger_sha = base.sha256_file(out / "raw_root.sha256")
    (out / "gate.complete").write_text(
        "COMPLETE_CG_PAGE_FED_PRODUCT_OVERLAP\n"
        "outcome=PASS\n"
        f"raw_root_sha256={ledger_sha}\n"
    )
    print(
        json.dumps({"terminal": True, "result": "PASS", **result["sim_ticks"]})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
