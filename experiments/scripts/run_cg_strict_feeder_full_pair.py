#!/usr/bin/env python3
"""Run a trace-free, same-checkpoint full-CG feeder-depth pair."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.scripts import classify_cg_strict_line_combined_full as seal
from experiments.scripts import run_cg_strict_line_combined_full as full

RAW = seal.RAW_ROOT
CERTIFICATE = Path(
    "/data1/nier/dx100-runs/"
    "2026-08-28-cg-strict-line-combined-full-certificate-r1"
)
DEPTHS = (1, 64)
EXECUTION_SOURCE_COMMIT = "097adc75b5fa704b7c76470f1f7d655fcb646d45"

WORK_STATS = (
    "IND_StrictTwoPhaseOperations",
    "IND_StrictTwoPhaseBFetchLines",
    "IND_StrictTwoPhaseDescriptors",
    "IND_StrictTwoPhaseAIssues",
    "IND_StrictTwoPhasePagesReady",
    "IND_NumOTEpochDrain",
    "IND_SoaJitInstructions",
    "IND_SoaJitSelected",
    "IND_SoaJitAliasesApplied",
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
    "STR_PublishAccepts",
    "STR_PublishWriteResponses",
    "STR_PublishTerminals",
)

TRANSPORT_STATS = (
    "IND_StrictTwoPhaseBackingIssues",
    "IND_VirtWriteIssues",
    "IND_VirtWriteCompletions",
    "IND_SoaJitAWriteIssues",
    "IND_SoaJitAWriteResponses",
    "system.maa.port_cache_RD_packets",
    "system.maa.port_cache_WR_packets",
)

TIMING_STATS = (
    "IND_StrictTwoPhaseBFetchCycles",
    "IND_StrictTwoPhaseRowOffsetCycles",
    "IND_StrictTwoPhaseAIssueCycles",
    "IND_StrictTwoPhaseBackingCycles",
    "IND_StrictTwoPhasePageCycles",
    "IND_StrictTwoPhaseConsumerCycles",
)


class PairError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PairError(message)


def sha256_file(path: Path) -> str:
    return full.sha256_file(path)


@dataclass(frozen=True)
class Arm:
    depth: int

    @property
    def name(self) -> str:
        return f"feeder{self.depth}"


def set_arg(command: list[str], option: str, value: int) -> None:
    prefix = f"{option}="
    matches = [
        index
        for index, token in enumerate(command)
        if token.startswith(prefix)
    ]
    require(len(matches) <= 1, f"duplicate option {option}")
    if matches:
        command[matches[0]] = f"{prefix}{value}"
    else:
        command.append(f"{prefix}{value}")


def remove_debug(command: list[str]) -> None:
    command[:] = [
        token
        for token in command
        if not token.startswith("--debug-flags=")
        and not token.startswith("--debug-file=")
    ]


def replace_outdir(command: list[str], out: Path) -> None:
    matches = [
        index
        for index, token in enumerate(command)
        if token.startswith("--outdir=")
    ]
    require(len(matches) == 1, "restore command lacks one output directory")
    command[matches[0]] = f"--outdir={out}"


def normalized_command(command: list[str]) -> list[str]:
    return [
        token
        for token in command
        if not token.startswith("--outdir=")
        and not token.startswith("--maa_virtual_index_buffer_lines=")
    ]


def verify_authority() -> dict:
    seal.validate_seal(CERTIFICATE)
    for name, digest in seal.RAW_HASHES.items():
        full.exact_hash(RAW / name, digest, f"raw full authority {name}")
    full.exact_hash(full.STRICT_GEM5, full.STRICT_GEM5_SHA256, "strict gem5")
    guest = RAW / "bin/cg_strict_line_combined_full"
    selector = RAW / "input/page_fed_product_soa_jit.selector"
    full.exact_hash(
        guest,
        "a30b5fe53992e8a118dcc10636f078d6433cd2a1375349a443c0e693ee351d03",
        "full guest",
    )
    full.exact_hash(
        selector,
        "3d8b96c1a61734d3ee89d1593de4ce31dbf829447e1467d52e00a889ec99a7a0",
        "selector",
    )
    before = RAW / "input/checkpoint.files.sha256.before"
    after = RAW / "input/checkpoint.files.sha256.after"
    require(
        before.read_bytes() == after.read_bytes(),
        "checkpoint ledger changed",
    )
    entries = full.verify_tree_ledger(RAW / "checkpoint", before)
    certificate = json.loads((CERTIFICATE / "certificate.json").read_text())
    require(
        certificate.get("verdict") == seal.VERDICT,
        "full certificate changed",
    )
    _, numerical = full.base.fingerprint_fields(full.base.NATIVE_LOG)
    return {
        "certificate": certificate,
        "numerical": numerical,
        "checkpoint_entries": entries,
        "guest": guest,
        "selector": selector,
    }


def command_for(arm: Arm, run: Path) -> list[str]:
    command = json.loads((RAW / "input/restore_command.json").read_text())
    require(isinstance(command, list), "raw restore command is not a list")
    remove_debug(command)
    replace_outdir(command, run)
    set_arg(command, "--maa_virtual_index_buffer_lines", arm.depth)
    required = (
        "--maa_virtual_strict_two_phase",
        "--maa_virtual_masked_writes",
        "--maa_soa_jit_value_cache_enable",
        "--maa_soa_jit_apply_lanes=4",
    )
    require(
        all(command.count(flag) == 1 for flag in required),
        "treatment changed",
    )
    return command


def validate_config(path: Path, depth: int) -> None:
    full.validate_config(path)
    lines = path.read_text(errors="replace").splitlines()
    require(
        lines.count(f"virtual_index_buffer_lines={depth}") == 1,
        f"feeder depth {depth} did not resolve",
    )


def stat_values(path: Path) -> dict[str, int]:
    values = full.lane.validate_stats(path)
    for name in full.STRICT_EXTRA_STATS:
        values[name] = full.base.first_stat_sum(path, name)
    for name in TRANSPORT_STATS:
        values[name] = full.base.first_stat_sum(path, name)
    return values


def validate_work(values: dict[str, int], expected: dict[str, int]) -> None:
    for name in WORK_STATS + full.FUSED_ZERO_STATS:
        require(
            values.get(name) == expected.get(name),
            f"conserved full work changed for {name}: "
            f"{values.get(name)} != {expected.get(name)}",
        )
    for name in TIMING_STATS:
        require(values.get(name, 0) > 0, f"empty timing counter {name}")


def validate_transport(values: dict[str, int]) -> None:
    require(
        values["IND_VirtWriteIssues"]
        == values["IND_VirtWriteCompletions"]
        > 0,
        "virtual backing issue/ACK closure failed",
    )
    require(
        values["IND_SoaJitAWriteIssues"]
        == values["IND_SoaJitAWriteResponses"]
        > 0,
        "q A-write issue/response closure failed",
    )
    require(
        values["IND_StrictTwoPhaseBackingIssues"]
        == values["IND_VirtWriteIssues"]
        + values["IND_SoaJitAWriteIssues"],
        "strict backing transactions do not reconcile",
    )


def validate_arm(
    arm: Arm,
    run: Path,
    returncode: int,
    authority: dict,
) -> dict:
    require(returncode == 0, f"{arm.name} exited {returncode}")
    log = run / "restore.log"
    lines = log.read_text(errors="replace").splitlines()
    require(
        not any(full.base.FATAL_RE.search(line) for line in lines),
        "fatal text",
    )
    full.base.exactly_one(
        lines,
        r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$",
        f"{arm.name} m5 exit",
    )
    require(
        sum(line == "ROI End!!!" for line in lines) == 1,
        "ROI did not close",
    )
    selection = full.validate_selection(lines)
    terminal_line = full.base.exactly_one(
        lines,
        rf"^CG_LOGICAL16_RMW_TERMINAL treatment={full.TREATMENT} "
        rf".* result=PASS$",
        f"{arm.name} terminal",
    )
    terminal = full.full.validate_terminal(
        full.base.parse_kv(terminal_line)
    )
    require(
        terminal["full_windows"] == full.EXPECTED_WINDOWS,
        "window count changed",
    )
    validate_config(run / "config.ini", arm.depth)
    values = stat_values(run / "stats.txt")
    expected = authority["certificate"]["candidate"]["stats"]
    validate_work(values, expected)
    validate_transport(values)
    _, fingerprint = full.base.fingerprint_fields(log)
    deltas = full.base.validate_numerical(fingerprint, authority["numerical"])
    require(
        not list(run.glob("*trace*.log")),
        "trace-free arm emitted a trace",
    )
    return {
        "depth": arm.depth,
        "terminal": True,
        "selection": selection,
        "full_windows": terminal["full_windows"],
        "simTicks": values["simTicks"],
        "fingerprint": fingerprint,
        "numerical_relative_deltas": deltas,
        "work_stats": {name: values[name] for name in WORK_STATS},
        "transport_stats": {
            name: values[name] for name in TRANSPORT_STATS
        },
        "timing_stats": {name: values[name] for name in TIMING_STATS},
    }


def run_arm(command: list[str], run: Path, environment: dict[str, str]) -> int:
    with (run / "restore.log").open("w") as output:
        completed = subprocess.run(
            command,
            stdout=output,
            stderr=subprocess.STDOUT,
            env=environment,
            check=False,
        )
    (run / "restore.log.exit").write_text(f"{completed.returncode}\n")
    return completed.returncode


def build_report(
    authority: dict,
    results: dict[str, dict],
    execution_source_commit: str,
    classifier_source_commit: str,
) -> dict:
    control = results["feeder1"]["simTicks"]
    candidate = results["feeder64"]["simTicks"]
    transport_delta = {
        name: (
            results["feeder64"]["transport_stats"][name]
            - results["feeder1"]["transport_stats"][name]
        )
        for name in TRANSPORT_STATS
    }
    timing_delta = {
        name: (
            results["feeder64"]["timing_stats"][name]
            - results["feeder1"]["timing_stats"][name]
        )
        for name in TIMING_STATS
    }
    return {
        "schema": "dx100.cg.strict_feeder_full_pair.v1",
        "terminal": True,
        "decision": (
            "ACCEPT_FASTER_64_LINE_FULL_OBSERVATION"
            if candidate < control
            else "REJECT_64_LINE_FULL_PERFORMANCE"
        ),
        "candidate_only_pair": True,
        "native_runs": 0,
        "direct4_runs": 0,
        "trace_runs": 0,
        "execution_source_commit": execution_source_commit,
        "classifier_source_commit": classifier_source_commit,
        "gem5_sha256": full.STRICT_GEM5_SHA256,
        "guest_sha256": sha256_file(authority["guest"]),
        "checkpoint_entries": authority["checkpoint_entries"],
        "arms": results,
        "transport_delta_64_minus_1": transport_delta,
        "timing_delta_64_minus_1": timing_delta,
        "control_over_candidate": control / candidate,
        "candidate_lower_latency_pct": (
            100 * (control - candidate) / control
        ),
        "mechanism_authority": str(CERTIFICATE),
    }


def publish_report(out: Path, runs: dict[str, Path], report: dict) -> None:
    outputs = (
        out / "result.json",
        out / "artifacts.sha256",
        out / "gate.complete",
    )
    require(not any(path.exists() for path in outputs), "output seal exists")
    outputs[0].write_text(json.dumps(report, indent=2) + "\n")
    ledger_lines = []
    for run in runs.values():
        for name in (
            "command.json",
            "config.ini",
            "restore.log",
            "restore.log.exit",
            "stats.txt",
        ):
            path = run / name
            ledger_lines.append(
                f"{sha256_file(path)}  {path.relative_to(out)}"
            )
    outputs[1].write_text("\n".join(sorted(ledger_lines)) + "\n")
    outputs[2].write_text(
        f"{report['decision']}\n"
        "correctness=PASS_NUMERICAL_MECHANISM_CORRECT\n"
        "native_runs=0\n"
    )


def validate_existing(out: Path) -> dict:
    require(out.is_dir() and not out.is_symlink(), "existing root is invalid")
    mechanism_diff = subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            EXECUTION_SOURCE_COMMIT,
            "HEAD",
            "--",
            "src/mem/MAA",
            "configs/common",
        ],
        cwd=ROOT,
        check=False,
    )
    require(
        mechanism_diff.returncode == 0,
        "mechanism source changed after pair execution",
    )
    authority = verify_authority()
    arms = [Arm(depth) for depth in DEPTHS]
    runs = {arm.name: out / arm.name for arm in arms}
    commands = {
        arm.name: json.loads((runs[arm.name] / "command.json").read_text())
        for arm in arms
    }
    for arm in arms:
        require(
            commands[arm.name] == command_for(arm, runs[arm.name]),
            f"recorded command changed for {arm.name}",
        )
    require(
        normalized_command(commands[arms[0].name])
        == normalized_command(commands[arms[1].name]),
        "existing pair differs by more than depth/output",
    )
    returncodes = {
        arm.name: int(
            (runs[arm.name] / "restore.log.exit").read_text().strip()
        )
        for arm in arms
    }
    results = {
        arm.name: validate_arm(
            arm, runs[arm.name], returncodes[arm.name], authority
        )
        for arm in arms
    }
    report = build_report(
        authority, results, EXECUTION_SOURCE_COMMIT, full.source_commit()
    )
    publish_report(out, runs, report)
    print(json.dumps(report, sort_keys=True))
    return report


def execute(out: Path) -> dict:
    require(not out.exists(), f"output exists: {out}")
    require(len(full.source_status().splitlines()) == 1, "source is dirty")
    authority = verify_authority()
    out.mkdir(parents=True)
    arms = [Arm(depth) for depth in DEPTHS]
    commands: dict[str, list[str]] = {}
    runs: dict[str, Path] = {}
    for arm in arms:
        run = out / arm.name
        run.mkdir()
        command = command_for(arm, run)
        commands[arm.name] = command
        runs[arm.name] = run
        (run / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    require(
        normalized_command(commands[arms[0].name])
        == normalized_command(commands[arms[1].name]),
        "pair differs by more than depth/output",
    )
    environment = dict(
        os.environ,
        LD_LIBRARY_PATH=str(full.base.RAMULATOR.parent)
        + ":"
        + os.environ.get("LD_LIBRARY_PATH", ""),
        OMP_NUM_THREADS="4",
        OMP_PROC_BIND="false",
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            arm.name: pool.submit(
                run_arm, commands[arm.name], runs[arm.name], environment
            )
            for arm in arms
        }
        returncodes = {
            name: future.result() for name, future in futures.items()
        }
    results = {
        arm.name: validate_arm(
            arm, runs[arm.name], returncodes[arm.name], authority
        )
        for arm in arms
    }
    report = build_report(
        authority, results, full.source_commit(), full.source_commit()
    )
    publish_report(out, runs, report)
    print(json.dumps(report, sort_keys=True))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--validate-existing", action="store_true")
    args = parser.parse_args(argv)
    if args.validate_existing:
        validate_existing(args.out.resolve())
    else:
        execute(args.out.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
