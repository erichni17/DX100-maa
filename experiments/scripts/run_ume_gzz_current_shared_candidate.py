#!/usr/bin/env python3
"""Run one current-source seven-tile GZZ strict candidate against sealed r6."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.scripts import (  # noqa: E402
    compare_gzz_shared_payload_phases as phase_compare,
)
from experiments.scripts import (  # noqa: E402
    run_ume_gzz_matched_consumer_matrix as matched,
)
from experiments.scripts import (  # noqa: E402
    run_ume_gzz_selector_bridge as bridge,
)
from experiments.scripts import run_ume_two_pass_matrix as base  # noqa: E402

AUTHORITY = Path(
    "/data1/nier/dx100-runs/2026-08-31-ume-gzz-matched-consumer-r6"
)
CURRENT_BASELINE = Path(
    "/data1/nier/dx100-runs/" "2026-09-01-ume-gzz-current-shared-candidate-r1"
)
DEFAULT_BUILD_ROOT = Path(
    "/data1/nier/worktrees/DX100-virtualization-selected-integration-cont-20260826"
)
DEFAULT_GEM5 = DEFAULT_BUILD_ROOT / "build/X86/gem5.opt"
EXPECTED_SIMULATOR_COMMIT = "dffa557381637cbb1a34411d737c7a83b5e493ae"
EXPECTED_GEM5_SHA256 = (
    "45206b3433449e10b26bbd8ff32281c06e533c101213097a27d50c364ca3c267"
)
ARM = next(arm for arm in base.ARMS if arm.name == "strict_bounded_hybrid")

TREATMENT_SOURCES = (
    "benchmarks/API/MAA_gem5.hpp",
    "benchmarks/API/MAA_virtual_materialize.hpp",
    "benchmarks/UME/gradzatz.cpp",
    "configs/common/MAAConfig.py",
    "configs/common/Options.py",
    "configs/deprecated/example/se.py",
    "src/mem/MAA/IndirectAccess.cc",
    "src/mem/MAA/IndirectAccess.hh",
    "src/mem/MAA/MAA.cc",
    "src/mem/MAA/MAA.hh",
    "src/mem/MAA/MAA.py",
    "src/mem/MAA/SharedPayloadTransfer.hh",
    "src/mem/MAA/SharedSourceOverlapScheduler.hh",
    "src/mem/MAA/VirtualCombinePayloadStore.hh",
    "src/mem/MAA/VirtualResponsePayloadStore.hh",
    "src/mem/MAA/VirtualSourceFanout.hh",
)


class CandidateError(RuntimeError):
    """Fail-closed current-source GZZ candidate error."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CandidateError(message)


def git_text(root: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *arguments], text=True
    ).strip()


def authority_identity() -> dict[str, str]:
    return {
        "artifacts_sha256": bridge.sha256(AUTHORITY / "artifacts.sha256"),
        "manifest_sha256": bridge.sha256(AUTHORITY / "manifest.json"),
        "result_sha256": bridge.sha256(AUTHORITY / "result.json"),
    }


def sealed_identity(root: Path) -> dict[str, str]:
    return {
        "artifacts_sha256": bridge.sha256(root / "artifacts.sha256"),
        "manifest_sha256": bridge.sha256(root / "manifest.json"),
        "result_sha256": bridge.sha256(root / "result.json"),
    }


def verify_current_sources(
    source_root: Path,
    gem5: Path,
    expected_commit: str = EXPECTED_SIMULATOR_COMMIT,
    expected_gem5_sha256: str = EXPECTED_GEM5_SHA256,
) -> dict[str, Any]:
    require(
        source_root.is_dir(), f"missing simulator source root: {source_root}"
    )
    require(gem5.is_file() and not gem5.is_symlink(), f"missing gem5: {gem5}")
    worktree_head = git_text(source_root, "rev-parse", "HEAD")
    commit = expected_commit
    subprocess.run(
        [
            "git",
            "-C",
            str(source_root),
            "cat-file",
            "-e",
            f"{commit}^{{commit}}",
        ],
        check=True,
    )
    require(worktree_head == commit, "simulator worktree is not exact commit")
    require(
        bridge.sha256(gem5) == expected_gem5_sha256,
        "current gem5 binary identity changed",
    )
    hashes: dict[str, str] = {}
    for relative in TREATMENT_SOURCES:
        build_source = source_root / relative
        runner_source = ROOT / relative
        require(
            build_source.is_file() and not build_source.is_symlink(),
            f"missing treatment source: {relative}",
        )
        require(
            runner_source.is_file() and not runner_source.is_symlink(),
            f"missing runner treatment source: {relative}",
        )
        committed = subprocess.check_output(
            ["git", "-C", str(source_root), "show", f"{commit}:{relative}"]
        )
        live = build_source.read_bytes()
        require(
            live == committed, f"uncommitted simulator treatment: {relative}"
        )
        require(
            runner_source.read_bytes() == live,
            f"runner/build treatment source mismatch: {relative}",
        )
        hashes[relative] = bridge.sha256(build_source)
    return {
        "commit": commit,
        "build_worktree_head_at_launch": worktree_head,
        "gem5_sha256": bridge.sha256(gem5),
        "treatment_sha256": hashes,
    }


def snapshot_treatment_sources(
    root: Path, source_root: Path, expected: dict[str, str]
) -> None:
    snapshot = root / "inputs/treatment_sources"
    for relative, digest in expected.items():
        destination = snapshot / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        require(
            base.copy_stable(source_root / relative, destination) == digest,
            f"treatment snapshot changed: {relative}",
        )
        destination.chmod(0o444)


def build_guest(root: Path) -> tuple[Path, list[list[str]]]:
    build = root / "inputs/build"
    build.mkdir(parents=True)
    m5op_source = ROOT / "util/m5/src/abi/x86/m5op.S"
    m5op = build / "m5op.o"
    guest = build / "gradzatz_hybrid"
    commands = [
        [
            "g++",
            "-std=c++11",
            "-O3",
            "-Wall",
            "-g3",
            "-fopenmp",
            f"-I{ROOT / 'include'}",
            f"-I{ROOT / 'util/m5/src'}",
            "-DGEM5",
            "-c",
            str(m5op_source),
            "-o",
            str(m5op),
        ],
        [
            "g++",
            "-std=c++11",
            "-O3",
            "-Wall",
            "-g3",
            "-fopenmp",
            f"-I{ROOT / 'benchmarks/API'}",
            f"-I{ROOT / 'include'}",
            f"-I{ROOT / 'util/m5/src'}",
            "-DGEM5",
            "-DMAA",
            "-DNUM_CORES=4",
            "-DMAA_MEM_SIZE=0x80000000",
            "-DUME_GRADZATZ_FIXED_INPUT",
            "-DUME_GRADZATZ_OUTPUT_FINGERPRINT",
            "-DUME_GRADZATZ_EXPECTED_N=16384",
            f"-DUME_GRADZATZ_EXPECTED_HASH={base.EXPECTED_OUTPUT_HASH}ULL",
            "-DUME_GZZ_MAA_PAGE_CONSUMER",
            "-DTILE_SIZE=16384",
            "-DMAA_VIRTUAL_GATHER",
            "-DMAA_GENERAL_VIRTUAL_CONSUMER",
            "-DMAA_CONSUMER_TILE_SIZE=4096",
            str(m5op),
            str(ROOT / "benchmarks/UME/gradzatz.cpp"),
            "-o",
            str(guest),
        ],
    ]
    for index, command in enumerate(commands):
        with (build / f"build.{index}.log").open("wb") as log:
            rc = subprocess.run(
                command, stdout=log, stderr=subprocess.STDOUT, check=False
            ).returncode
        require(rc == 0, f"guest build {index} failed")
    guest.chmod(0o555)
    return guest, commands


def run_arm(
    root: Path,
    gem5: Path,
    guest: Path,
    selector: Path,
    ramulator_config: Path,
    environment: dict[str, str],
) -> None:
    checkpoint = root / "checkpoints" / ARM.name
    checkpoint.mkdir(parents=True)
    options = base.arm_options(ARM, selector)
    command = base.checkpoint_command(
        gem5, guest, checkpoint / "gem5", options
    )
    rc = base.run_logged(command, checkpoint, "checkpoint", environment)
    require(rc == 0, "strict checkpoint failed")
    require(
        "because checkpoint"
        in (checkpoint / "checkpoint.log").read_text(errors="replace"),
        "strict checkpoint marker missing",
    )
    identity = base.tree_identity(checkpoint / "gem5")
    base.atomic_json(checkpoint / "identity.json", identity)

    arm_root = root / "arms" / ARM.name
    arm_root.mkdir(parents=True)
    restore = base.common_restore_command(
        gem5,
        ramulator_config,
        checkpoint / "gem5",
        guest,
        options,
        arm_root / "run",
        ARM,
    )
    rc = base.run_logged(restore, arm_root, "restore", environment)
    require(rc == 0, "strict restore failed")
    require(
        base.tree_identity(checkpoint / "gem5")["sha256"]
        == identity["sha256"],
        "strict checkpoint mutated",
    )


def classify(root: Path) -> dict[str, Any]:
    authority_before = authority_identity()
    current_before = sealed_identity(CURRENT_BASELINE)
    authority = matched.validate(AUTHORITY)
    bridge.verify_ledger(CURRENT_BASELINE)
    current_sealed = json.loads((CURRENT_BASELINE / "result.json").read_text())
    require(
        authority_identity() == authority_before,
        "sealed r6 authority changed during read-only validation",
    )
    require(
        sealed_identity(CURRENT_BASELINE) == current_before,
        "current baseline changed during read-only validation",
    )
    manifest = json.loads((root / "manifest.json").read_text())
    candidate = base.classify_arm(root, ARM, manifest)
    log = (root / "arms" / ARM.name / "restore.log").read_text(
        errors="replace"
    )
    require(
        "UME_GZZ_PAGE_CONSUMER mode=maa_div_mul "
        "physical_tiles_per_core=7 pingpong=0 cpu_spd_payload_reads=0" in log,
        "seven-tile MAA DIV/MUL guest marker missing",
    )
    r6_strict = authority["arms"][ARM.name]
    native16 = authority["arms"]["native16"]
    native4 = authority["arms"]["native4"]
    require(
        candidate["output_hash"]
        == r6_strict["output_hash"]
        == native16["output_hash"]
        == native4["output_hash"]
        == base.EXPECTED_OUTPUT_HASH,
        "exact output mismatch",
    )
    require(
        current_sealed["candidate"]["output_hash"] == base.EXPECTED_OUTPUT_HASH
        and current_sealed["candidate"]["counters"]["simTicks"] == 42_346_396,
        "current 42,346,396-tick baseline identity/result changed",
    )
    for field in ("numInst_INDRD", "numInst_INDRMW", "index_words"):
        require(
            candidate["counters"][field] == r6_strict["counters"][field],
            f"semantic mechanism changed: {field}",
        )
    trace_lines = (
        (root / "arms" / ARM.name / "run/contract_trace.log")
        .read_text(errors="replace")
        .splitlines()
    )
    shared = base.base.exactly_one_event(
        trace_lines, "shared_result_payload_complete"
    )
    for key, expected in {
        "schema": "1",
        "unit": "0",
        "capacity": "4096",
        "line_shadow_bytes": "0",
    }.items():
        require(shared.get(key) == expected, f"shared payload {key}")
    require(
        0 < int(shared["high_water"]) <= int(shared["capacity"]),
        "shared payload high-water bound",
    )
    require(int(shared["transfers"]) > 0, "shared payload transfer missing")
    require(
        int(shared["transfers"]) == 16_384 and int(shared["rollbacks"]) == 0,
        "exact shared transfer/rollback closure",
    )

    overlap = base.base.exactly_one_event(
        trace_lines, "fanout_overlap_complete"
    )
    require(overlap.get("schema") == "1", "overlap summary schema")
    require(int(overlap["resumes"]) > 0, "overlap resume missing")
    require(int(overlap["pending_hwm"]) == 1, "pending latch HWM")
    resume_events = [
        event
        for line in trace_lines
        if (event := base.base.parse_event(line, "fanout_overlap_resume"))
    ]
    require(
        len(resume_events) == int(overlap["resumes"]),
        "overlap resume trace/stat mismatch",
    )

    counters = candidate["counters"]
    strict_trace = candidate["strict_trace"]
    require(strict_trace is not None, "strict trace missing")
    require(
        counters["write_issues"]
        == counters["write_completions"]
        == int(strict_trace["backing_issues"])
        == int(strict_trace["backing_acks"]),
        "exact backing ACK closure",
    )
    require(
        counters["strict_operations"] == 1
        and counters["strict_descriptors"] == 16_384
        and counters["pages_ready"] == 4,
        "strict mechanism closure",
    )
    require(
        int(strict_trace["a_issues"])
        == int(strict_trace["a_responses"])
        == 1_025,
        "exact source issue/response closure",
    )
    require(
        counters["full_line_writes"] + counters["partial_writes"]
        == counters["write_issues"]
        and int(strict_trace["backing_transport_bytes"]) >= 65_536
        and int(strict_trace["backing_semantic_bytes"]) == 65_536,
        "exact semantic/transport backing closure",
    )

    candidate_stats = root / "arms" / ARM.name / "run/stats.txt"
    candidate_trace = root / "arms" / ARM.name / "run/contract_trace.log"
    current_stats = CURRENT_BASELINE / "arms" / ARM.name / "run/stats.txt"
    current_trace = (
        CURRENT_BASELINE / "arms" / ARM.name / "run/contract_trace.log"
    )
    phase_comparison = phase_compare.compare(
        current_stats, current_trace, candidate_stats, candidate_trace
    )
    require(
        phase_comparison["diagnosis"]["classification"]
        == "SOURCE_MLP_RECOVERED",
        "integrated phase comparator rejected source MLP recovery",
    )
    phase = phase_comparison["comparisons"]
    require(
        phase["simTicks"]["reference"] == 42_346_396,
        "integrated comparator used the wrong current baseline",
    )
    require(
        phase["IND_VirtResponseSlotHighWater"]["candidate"] > 1,
        "response HWM did not recover",
    )
    require(
        phase["IND_VirtSharedPayloadHighWater"]["candidate"] <= 4_096,
        "shared pool exceeded 4096 words",
    )
    require(
        phase["IND_VirtPendingSourceHighWater"]["candidate"] == 1
        and phase["IND_VirtFanoutOverlapResumes"]["candidate"] > 0,
        "single pending overlap mechanism missing",
    )
    require(
        phase["IND_StrictTwoPhaseAIssueCycles"]["ratio"] <= 0.50
        and phase["IND_StrictTwoPhaseBackingCycles"]["ratio"] <= 0.50
        and phase["simTicks"]["ratio"] <= 0.95,
        "A/backing/total improvement is not material",
    )
    require(
        abs(phase["IND_StrictTwoPhaseBFetchCycles"]["ratio"] - 1.0) <= 0.01
        and phase["IND_StrictTwoPhaseConsumerCycles"]["ratio"] <= 1.01,
        "B changed by more than 1% or consumer regressed",
    )

    current_ticks = counters["simTicks"]
    r6_ticks = r6_strict["counters"]["simTicks"]
    native16_ticks = native16["counters"]["simTicks"]
    native4_ticks = native4["counters"]["simTicks"]
    return {
        "schema": "dx100.ume_gzz_current_shared_candidate.result.v1",
        "terminal": True,
        "decision": "ACCEPT_SHARED_SOURCE_OVERLAP_REPAIR",
        "candidate_only": True,
        "simulated_arms": [ARM.name],
        "native_simulations": 0,
        "performance_attribution": False,
        "sealer_commit": git_text(ROOT, "rev-parse", "HEAD"),
        "sealer_sha256": bridge.sha256(Path(__file__)),
        "authority": str(AUTHORITY),
        "authority_identity": authority_before,
        "current_baseline": str(CURRENT_BASELINE),
        "current_baseline_identity": current_before,
        "candidate": candidate,
        "shared_payload_closure": shared,
        "line_shadow_bytes": 0,
        "fanout_overlap_closure": overlap,
        "integrated_phase_comparison": phase_comparison,
        "comparisons": {
            "current_ticks": current_ticks,
            "r6_strict_ticks": r6_ticks,
            "r6_strict_over_current": r6_ticks / current_ticks,
            "current_latency_change_vs_r6_strict": current_ticks / r6_ticks
            - 1.0,
            "frozen_native16_ticks": native16_ticks,
            "frozen_native4_ticks": native4_ticks,
            "frozen_native16_over_current_orientation": (
                native16_ticks / current_ticks
            ),
            "frozen_native4_over_current_orientation": native4_ticks
            / current_ticks,
        },
        "limitations": [
            "Exactly one current strict_bounded_hybrid arm was simulated; no "
            "fresh native baseline was run.",
            "The r6 strict and matched native controls use the sealed r6 binary, "
            "not the current candidate binary.",
            "All timing ratios are cross-binary historical comparisons or "
            "orientation only and do not support fresh-baseline attribution.",
            "One deterministic reduced-input GZZ observation; no repetition, "
            "full-scale application, or area claim.",
        ],
    }


def prepare(
    root: Path,
    gem5: Path,
    source_root: Path,
    expected_commit: str = EXPECTED_SIMULATOR_COMMIT,
    expected_gem5_sha256: str = EXPECTED_GEM5_SHA256,
) -> dict[str, Any]:
    require(not root.exists(), f"output exists: {root}")
    source_identity = verify_current_sources(
        source_root, gem5, expected_commit, expected_gem5_sha256
    )
    authority = matched.validate(AUTHORITY)
    bridge.verify_ledger(CURRENT_BASELINE)
    authority_hashes = authority_identity()
    root.mkdir(parents=True)
    inputs = root / "inputs"
    inputs.mkdir()
    frozen_gem5 = inputs / "gem5.opt"
    frozen_ramulator = inputs / "libramulator.so"
    frozen_config = inputs / "ramulator.yaml"
    require(
        base.copy_stable(gem5, frozen_gem5) == expected_gem5_sha256,
        "gem5 changed while freezing",
    )
    base.copy_stable(AUTHORITY / "inputs/libramulator.so", frozen_ramulator)
    base.copy_stable(AUTHORITY / "inputs/ramulator.yaml", frozen_config)
    frozen_gem5.chmod(0o555)
    snapshot_treatment_sources(
        root, source_root, source_identity["treatment_sha256"]
    )
    guest, build_commands = build_guest(root)
    selector = inputs / "strict_bounded_hybrid.selector"
    selector.write_text(ARM.selector + "\n")
    selector.chmod(0o444)
    environment = dict(
        os.environ,
        LD_LIBRARY_PATH=str(inputs),
        OMP_NUM_THREADS="4",
        OMP_PROC_BIND="false",
    )
    ldd = subprocess.check_output(
        ["ldd", str(frozen_gem5)], env=environment, text=True
    )
    (inputs / "gem5.ldd.txt").write_text(ldd)
    match = re.search(r"^[ \t]*libramulator\.so => (\S+)", ldd, re.M)
    require(match is not None, "current gem5 did not resolve Ramulator")
    require(
        Path(match.group(1)).resolve() == frozen_ramulator.resolve(),
        "current gem5 resolved the wrong Ramulator library",
    )
    manifest = {
        "schema": "dx100.ume_gzz_current_shared_candidate.campaign.v1",
        "runner_commit_at_launch": git_text(ROOT, "rev-parse", "HEAD"),
        "runner_sha256": bridge.sha256(Path(__file__)),
        "simulator_source_root": str(source_root),
        "simulator_source_commit": source_identity["commit"],
        "build_worktree_head_at_launch": source_identity[
            "build_worktree_head_at_launch"
        ],
        "gem5_sha256": source_identity["gem5_sha256"],
        "ramulator_sha256": bridge.sha256(frozen_ramulator),
        "ramulator_config_sha256": bridge.sha256(frozen_config),
        "treatment_sha256": source_identity["treatment_sha256"],
        "guest_sha256": {"hybrid": bridge.sha256(guest)},
        "build_commands": build_commands,
        "arms": [ARM.name],
        "native_simulations": 0,
        "timeout": "none",
        "authority": str(AUTHORITY),
        "authority_identity": authority_hashes,
        "current_baseline": str(CURRENT_BASELINE),
        "current_baseline_identity": sealed_identity(CURRENT_BASELINE),
        "authority_output_hash": authority["output_hash"],
        "expected_output_hash": base.EXPECTED_OUTPUT_HASH,
        "matched_consumer": "maa_div_mul",
        "configured_tiles_per_core": 8,
        "used_tiles_per_core": 7,
    }
    base.atomic_json(root / "manifest.json", manifest)
    return {
        "gem5": frozen_gem5,
        "ramulator_config": frozen_config,
        "guest": guest,
        "selector": selector,
        "environment": environment,
    }


def seal(root: Path) -> dict[str, Any]:
    require(root.is_dir(), f"missing completed output: {root}")
    for relative in ("result.json", "gate.complete", "artifacts.sha256"):
        require(
            not (root / relative).exists(), f"refusing to replace {relative}"
        )
    result = classify(root)
    base.atomic_json(root / "result.json", result)
    base.atomic_text(
        root / "gate.complete",
        "COMPLETE_UME_GZZ_CURRENT_SHARED_CANDIDATE\n"
        "correctness=EXACT_REFERENCE\n"
        "native_simulations=0\n"
        "timeout=none\n",
    )
    bridge.write_ledger(root)
    bridge.verify_ledger(root)
    return result


def run(
    root: Path,
    gem5: Path,
    source_root: Path,
    expected_commit: str = EXPECTED_SIMULATOR_COMMIT,
    expected_gem5_sha256: str = EXPECTED_GEM5_SHA256,
) -> dict[str, Any]:
    prepared = prepare(
        root, gem5, source_root, expected_commit, expected_gem5_sha256
    )
    run_arm(
        root,
        prepared["gem5"],
        prepared["guest"],
        prepared["selector"],
        prepared["ramulator_config"],
        prepared["environment"],
    )
    return seal(root)


def validate(root: Path) -> dict[str, Any]:
    bridge.verify_ledger(root)
    manifest = json.loads((root / "manifest.json").read_text())
    require(
        bridge.sha256(root / "inputs/gem5.opt") == manifest["gem5_sha256"],
        "sealed gem5 provenance",
    )
    for relative, expected in manifest["treatment_sha256"].items():
        require(
            bridge.sha256(root / "inputs/treatment_sources" / relative)
            == expected,
            f"sealed treatment source changed: {relative}",
        )
    require(
        authority_identity() == manifest["authority_identity"],
        "sealed r6 authority identity changed",
    )
    require(
        sealed_identity(CURRENT_BASELINE)
        == manifest["current_baseline_identity"],
        "sealed current baseline identity changed",
    )
    sealed = json.loads((root / "result.json").read_text())
    require(classify(root) == sealed, "sealed candidate result changed")
    bridge.verify_ledger(root)
    return sealed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("run", "seal", "validate", "preflight")
    )
    parser.add_argument("out", nargs="?", type=Path)
    parser.add_argument("--gem5", type=Path, default=DEFAULT_GEM5)
    parser.add_argument(
        "--gem5-source-root", type=Path, default=DEFAULT_BUILD_ROOT
    )
    parser.add_argument(
        "--expected-simulator-commit", default=EXPECTED_SIMULATOR_COMMIT
    )
    parser.add_argument("--expected-gem5-sha256", default=EXPECTED_GEM5_SHA256)
    args = parser.parse_args()
    if args.command == "preflight":
        source_identity = verify_current_sources(
            args.gem5_source_root.resolve(),
            args.gem5.resolve(),
            args.expected_simulator_commit,
            args.expected_gem5_sha256,
        )
        authority = matched.validate(AUTHORITY)
        result: dict[str, Any] = {
            "arm": ARM.name,
            "native_simulations": 0,
            "timeout": "none",
            "simulator": source_identity,
            "authority": str(AUTHORITY),
            "authority_terminal": authority["terminal"],
        }
    else:
        require(args.out is not None, f"{args.command} requires OUT")
        if args.command == "run":
            result = run(
                args.out.resolve(),
                args.gem5.resolve(),
                args.gem5_source_root.resolve(),
                args.expected_simulator_commit,
                args.expected_gem5_sha256,
            )
        elif args.command == "seal":
            result = seal(args.out.resolve())
        else:
            result = validate(args.out.resolve())
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
