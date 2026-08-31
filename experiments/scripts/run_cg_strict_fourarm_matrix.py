#!/usr/bin/env python3
"""Run the smallest exact four-arm CG comparison.

The guest is compiled once with a deferred selector.  A checkpoint is taken
before that selector is read; restore processes receive per-arm selectors on
the same inherited file descriptor, so all four restores can run in parallel
without a mutable shared selector race.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GEM5 = Path(
    "/data1/nier/worktrees/DX100-virtualization-selected-integration-cont-20260826"
    "/build/X86/gem5.opt"
)
DEFAULT_RAMULATOR = Path(
    "/data1/nier/dx100-runs/2026-08-12-hybrid-line-handoff-8a5c7712/input"
    "/libramulator.so"
)
DEFAULT_OUT = Path(
    "/data1/nier/worktrees/codex-coordination/sessions/"
    "cg-strict-fourarm-matrix-20260831-20260831-104028-a26c56c4/evidence/"
    "cg-strict-fourarm-na256-r1"
)
GEM5_SHA256 = (
    "aa5c70b140b6fb66bfb9f4a28b34f009f025cf639eb288c01dbb91b0d2f609bb"
)
RAMULATOR_SHA256 = (
    "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753"
)
SIMULATOR_SOURCE_COMMIT = "9393ef52e47357d9192050e539e013b6ce64df23"
CG_NA = 256
TILE = 16_384
PHYSICAL = 4_096
SELECTOR_TARGET = "/tmp/cg_strict_fourarm_selector_20260831"
M5_EXIT = re.compile(
    r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$"
)
FINGERPRINT = re.compile(r"^CG_FINGERPRINT .* elements=256 .* result=PASS$")
TERMINAL = re.compile(
    r"^CG_LOGICAL16_RMW_TERMINAL treatment=(\S+) .* result=PASS$"
)
FATAL = re.compile(
    r"(?:panic:|fatal:|Program aborted|Segmentation fault|assertion failed)",
    re.I,
)


class MatrixError(RuntimeError):
    """Fail-closed evidence error."""


def require(value: bool, message: str) -> None:
    if not value:
        raise MatrixError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stat_sum(path: Path, suffix: str) -> int:
    total = 0
    for line in path.read_text(errors="replace").splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0].endswith(suffix):
            try:
                total += int(float(fields[1]))
            except ValueError:
                pass
    return total


def first_stat(path: Path, name: str) -> int:
    section = 0
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("---------- Begin Simulation Statistics"):
            section += 1
        elif section == 1 and line.split()[:1] == [name]:
            return int(float(line.split()[1]))
    raise MatrixError(f"missing first-window stat {name} in {path}")


def parse_kv(line: str) -> dict[str, str]:
    return dict(item.split("=", 1) for item in line.split()[1:] if "=" in item)


def proc_start_ticks(pid: int) -> int | None:
    try:
        line = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return None
    close = line.rfind(")")
    if close < 0:
        return None
    fields = line[close + 2 :].split()
    return int(fields[19]) if len(fields) > 19 else None


def boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def immutable_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    destination.chmod(0o555 if os.access(source, os.X_OK) else 0o444)


@dataclass(frozen=True)
class Arm:
    name: str
    selector: str
    physical: int
    strict: bool


ARMS = (
    Arm("native16", "native_16k", 16_384, False),
    Arm("native4x4", "native_4kx4", 4_096, False),
    Arm("original_hybrid", "legacy_4k", 4_096, False),
    Arm("strict_two_pass", "page_fed_product_soa_jit", 4_096, True),
)


def runtime_hashes(commit: str) -> dict[str, str]:
    paths = (
        "configs/deprecated/example/se.py",
        "configs/common/Options.py",
        "configs/common/MAAConfig.py",
        "configs/common/Simulation.py",
        "src/mem/MAA/MAA.py",
    )
    result = {}
    for relative in paths:
        live = ROOT / relative
        committed = subprocess.check_output(
            ["git", "show", f"{commit}:{relative}"], cwd=ROOT
        )
        require(
            live.read_bytes() == committed, f"runtime source drift: {relative}"
        )
        result[relative] = sha256(live)
    return result


def run_logged(
    command: list[str],
    log: Path,
    environment: dict[str, str],
    cwd: Path | None = None,
) -> tuple[int, dict[str, object]]:
    started = time.time_ns()
    with log.open("wb") as output:
        process = subprocess.Popen(
            command,
            stdout=output,
            stderr=subprocess.STDOUT,
            env=environment,
            cwd=cwd,
        )
        ticks = proc_start_ticks(process.pid)
        require(
            ticks is not None, f"process {process.pid} has no start identity"
        )
        record = {
            "pid": process.pid,
            "start_ticks": ticks,
            "boot_id": boot_id(),
            "started_ns": started,
        }
        returncode = process.wait()
    record.update(
        {
            "returncode": returncode,
            "ended_ns": time.time_ns(),
            "pid_absent": proc_start_ticks(process.pid) is None,
        }
    )
    return returncode, record


def launch_with_selector(
    command: list[str], selector: Path, log: Path, environment: dict[str, str]
) -> tuple[subprocess.Popen[bytes], object, dict[str, object]]:
    wrapped = [
        "/usr/bin/unshare",
        "-Urnm",
        "/bin/bash",
        "-c",
        f'mount --bind "$1" {SELECTOR_TARGET}; shift; exec "$@"',
        "cg-selector",
        str(selector),
        *command,
    ]
    output = log.open("wb")
    process = subprocess.Popen(
        wrapped,
        stdout=output,
        stderr=subprocess.STDOUT,
        env=environment,
    )
    output.close()
    ticks = proc_start_ticks(process.pid)
    require(ticks is not None, f"process {process.pid} has no start identity")
    record = {"pid": process.pid, "start_ticks": ticks, "boot_id": boot_id()}
    return process, None, record


def common_command(
    gem5: Path,
    out: Path,
    checkpoint: Path,
    guest: Path,
    ramulator_yaml: Path,
    physical: int,
    strict: bool,
    trace: bool,
) -> list[str]:
    command = [
        str(gem5),
        "--listener-mode=off",
        f"--outdir={out}",
        *(
            [
                "--debug-flags=MAAVirtualTrace",
                "--debug-file=strict_trace.log",
            ]
            if trace
            else []
        ),
        str(ROOT / "configs/deprecated/example/se.py"),
        "--cpu-type=X86O3CPU",
        "-r",
        "1",
        "-n",
        "4",
        "--mem-size=2GB",
        "--sys-clock=3.2GHz",
        "--cpu-clock=3.2GHz",
        "--caches",
        "--l1d_size=32kB",
        "--l1d_assoc=8",
        "--l1d_mshrs=16",
        "--l1d_write_buffers=8",
        "--l1i_size=32kB",
        "--l1i_assoc=8",
        "--l1i_mshrs=16",
        "--l1i_write_buffers=8",
        "--l2cache",
        "--l2_size=256kB",
        "--l2_assoc=4",
        "--l2_mshrs=32",
        "--l2_write_buffers=16",
        "--l3cache",
        "--l3_size=8MB",
        "--l3_assoc=16",
        "--l3_mshrs=256",
        "--l3_write_buffers=128",
        "--l3_ports=4",
        "--cacheline_size=64",
        "--mem-type=Ramulator2",
        f"--ramulator-config={ramulator_yaml}",
        "--mem-channels=2",
        "--maa",
        "--maa_num_maas=1",
        "--maa_num_indirect_units_per_maa=4",
        "--maa_num_tiles_per_core=8",
        f"--maa_num_tile_elements={TILE}",
        f"--maa_physical_tile_elements={physical}",
        "--maa_num_initial_row_table_slices=32",
        "--maa_num_row_table_rows_per_slice=64",
        "--maa_num_row_table_entries_per_subslice_row=8",
        "--maa_num_offset_table_entries=16384",
        "--maa_num_offset_table_epoch_entries=16384",
        "--maa_ncbus_width=32",
        "--maa_page_fed_soa_jit",
        "--maa_soa_jit_value_cache_enable",
        "--maa_soa_jit_apply_lanes=4",
        "--maa_virtual_index_buffer_lines=64",
        "--maa_virtual_index_issue_lines_per_cycle=1",
        "--maa_virtual_combine_slots=16",
        "--maa_virtual_combine_ways=4",
        "--maa_virtual_combine_banks=4",
        "--maa_virtual_response_slots=8",
        "--maa_virtual_response_word_pool=0",
        "--maa_virtual_words_per_cycle=1",
        "--maa_virtual_max_outstanding_writes=32",
        "--maa_virtual_complete_line_payload_words_per_cycle=8",
        "--maa_virtual_complete_line_payload_active_lines=1",
        "--maa_virtual_complete_line_payload_banks=32",
        "--maa_virtual_complete_line_payload_stage_partial",
        "--maa_virtual_masked_writes",
        "--checkpoint-dir",
        str(checkpoint),
        "--cmd",
        str(guest),
        "--options",
        f"MAA_DEFERRED {SELECTOR_TARGET}",
    ]
    if strict:
        command.append("--maa_virtual_strict_two_phase")
    return command


def normalize(command: list[str]) -> list[str]:
    hidden = (
        "--outdir=",
        "--maa_physical_tile_elements=",
        "--debug-flags=",
        "--debug-file=",
    )
    return [
        token
        for token in command
        if not token.startswith(hidden)
        and token != "--maa_virtual_strict_two_phase"
    ]


def write_tree_ledger(root: Path, output: Path) -> str:
    lines = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.name in {"raw_root.sha256", "matrix.complete"}:
            continue
        lines.append(f"{sha256(path)}  {path.relative_to(root)}")
    output.write_text("\n".join(lines) + "\n")
    return sha256(output)


def verify_ledger(root: Path) -> str:
    ledger = root / "raw_root.sha256"
    require(ledger.is_file(), "missing immutable raw ledger")
    for line in ledger.read_text().splitlines():
        digest, relative = line.split("  ", 1)
        path = root / relative
        require(
            path.is_file() and sha256(path) == digest,
            f"changed raw artifact: {path}",
        )
    return sha256(ledger)


def parse_terminal(log: Path, treatment: str) -> dict[str, str]:
    lines = log.read_text(errors="replace").splitlines()
    require(
        not any(FATAL.search(line) for line in lines), f"fatal text in {log}"
    )
    require(
        sum(bool(M5_EXIT.match(line)) for line in lines) == 1,
        f"m5 exit count: {log}",
    )
    require(lines.count("ROI End!!!") == 1, f"ROI count: {log}")
    fingerprints = [line for line in lines if FINGERPRINT.match(line)]
    require(len(fingerprints) == 1, f"fingerprint count: {log}")
    terminals = [line for line in lines if TERMINAL.match(line)]
    require(
        len(terminals) == 1
        and terminals[0].split()[1].split("=", 1)[1] == treatment,
        f"terminal treatment: {log}",
    )
    reductions = [
        line
        for line in lines
        if line.startswith(
            ("CG_REDUCTION_EVIDENCE ", "CG_OUTER_REDUCTION_EVIDENCE ")
        )
    ]
    require(len(reductions) == 11, f"reduction count: {log}")
    return {
        "fingerprint": fingerprints[0],
        "terminal": terminals[0],
        "reductions": reductions,
        "lines": lines,
    }


def validate_arm(
    root: Path, arm: Arm, reference: dict[str, object] | None
) -> dict[str, object]:
    arm_root = root / "arms" / arm.name
    log = arm_root / "restore.log"
    require(
        int((arm_root / "restore.exit").read_text()) == 0,
        f"{arm.name} exit code",
    )
    parsed = parse_terminal(log, arm.selector)
    if reference is not None:
        require(
            parsed["fingerprint"] == reference["fingerprint"],
            f"{arm.name} CG fingerprint differs",
        )
        require(
            parsed["reductions"] == reference["reductions"],
            f"{arm.name} reductions differ",
        )
    config = {}
    for line in (arm_root / "config.ini").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            config[key] = value
    expected = {
        "num_tile_elements": "16384",
        "physical_tile_elements": str(arm.physical),
        "num_initial_row_table_slices": "32",
        "num_offset_table_entries": "16384",
        "num_offset_table_epoch_entries": "16384",
        "virtual_index_buffer_lines": "64",
        "virtual_index_issue_lines_per_cycle": "1",
        "virtual_combine_slots": "16",
        "virtual_combine_ways": "4",
        "virtual_combine_banks": "4",
        "virtual_response_slots": "8",
        "virtual_words_per_cycle": "1",
        "virtual_max_outstanding_writes": "32",
        "virtual_complete_line_payload_words_per_cycle": "8",
        "virtual_complete_line_payload_active_lines": "1",
        "virtual_complete_line_payload_banks": "32",
        "virtual_complete_line_payload_stage_partial": "true",
        "virtual_masked_writes": "true",
        "virtual_strict_two_phase": str(arm.strict).lower(),
    }
    for key, value in expected.items():
        require(
            config.get(key) == value,
            f"{arm.name} config {key}={config.get(key)!r}",
        )
    stats_path = arm_root / "stats.txt"
    values = {
        "simTicks": first_stat(stats_path, "simTicks"),
        "simInsts": first_stat(stats_path, "simInsts"),
        "indirect_ops": stat_sum(stats_path, "numInst_INDRD"),
        "stream_writes": stat_sum(stats_path, "numInst_STRWR"),
        "alu_ops": stat_sum(stats_path, "numInst_ALUS"),
        "index_words": stat_sum(stats_path, "IND_VirtIndexWords"),
        "write_issues": stat_sum(stats_path, "IND_VirtWriteIssues"),
        "write_completions": stat_sum(stats_path, "IND_VirtWriteCompletions"),
        "full_writes": stat_sum(stats_path, "IND_VirtFullLineWrites"),
        "partial_writes": stat_sum(stats_path, "IND_VirtPartialWrites"),
        "strict_ops": stat_sum(stats_path, "IND_StrictTwoPhaseOperations"),
        "strict_b_lines": stat_sum(
            stats_path, "IND_StrictTwoPhaseBFetchLines"
        ),
        "strict_descriptors": stat_sum(
            stats_path, "IND_StrictTwoPhaseDescriptors"
        ),
        "strict_a_issues": stat_sum(stats_path, "IND_StrictTwoPhaseAIssues"),
        "strict_backing": stat_sum(
            stats_path, "IND_StrictTwoPhaseBackingIssues"
        ),
        "strict_pages": stat_sum(stats_path, "IND_StrictTwoPhasePagesReady"),
        "offset_drains": stat_sum(stats_path, "IND_NumOTEpochDrain"),
        "payload_starts": stat_sum(
            stats_path, "IND_VirtCompleteLinePayloadStarts"
        ),
        "payload_completions": stat_sum(
            stats_path, "IND_VirtCompleteLinePayloadCompletions"
        ),
        "payload_read_cycles": stat_sum(
            stats_path, "IND_VirtCompleteLinePayloadReadCycles"
        ),
        "payload_serial_cycles": stat_sum(
            stats_path, "IND_VirtCompleteLinePayloadSerialReadCycles"
        ),
        "payload_scheduled_words": stat_sum(
            stats_path, "IND_VirtCompleteLinePayloadScheduledWords"
        ),
        "payload_read_words": stat_sum(
            stats_path, "IND_VirtCompleteLinePayloadReadWords"
        ),
        "payload_bank_conflicts": stat_sum(
            stats_path, "IND_VirtCompleteLinePayloadBankConflictCycles"
        ),
        "payload_backpressure": stat_sum(
            stats_path, "IND_VirtCompleteLinePayloadBackpressureCycles"
        ),
        "virtual_index_lines": stat_sum(stats_path, "IND_VirtIndexLineReads"),
    }
    require(
        values["simInsts"] > 0 and values["index_words"] in {0, 163840},
        f"{arm.name} index work",
    )
    require(
        values["write_issues"] == values["write_completions"],
        f"{arm.name} write ACK closure",
    )
    require(
        values["payload_starts"] == values["payload_completions"],
        f"{arm.name} payload closure",
    )
    require(
        values["payload_scheduled_words"] == values["payload_read_words"],
        f"{arm.name} payload word closure",
    )
    require(
        values["payload_read_cycles"] == values["payload_serial_cycles"],
        f"{arm.name} single-line payload serialization",
    )
    if arm.strict:
        require(
            values["strict_ops"] > 0 and values["strict_b_lines"] == 20_480,
            f"{arm.name} strict B work",
        )
        require(
            values["strict_descriptors"] == 327_680
            and values["strict_a_issues"] > 0,
            f"{arm.name} strict descriptors/A",
        )
        require(values["offset_drains"] == 0, f"{arm.name} offset drain")
        require(
            values["partial_writes"] > 0 and values["full_writes"] >= 0,
            f"{arm.name} partial retirement inactive",
        )
        require(
            values["payload_backpressure"] == 0,
            f"{arm.name} payload backpressure",
        )
        trace = arm_root / "strict_trace.log"
        trace_lines = trace.read_text(errors="replace").splitlines()
        timing = [
            parse_kv(line)
            for line in trace_lines
            if "event=strict_two_phase_timing " in line
            or "event=strict_page_fed_two_phase_timing " in line
        ]
        require(len(timing) == 20, f"{arm.name} strict timing events")
        require(
            all(
                int(row["A_FIRST_ISSUE"]) >= int(row["ROW_OFFSET_LAST_INSERT"])
                and row["order_ok"] == "1"
                and row["terminal"] == "1"
                for row in timing
            ),
            f"{arm.name} B-close-before-A",
        )
    result = {
        "arm": arm.name,
        "treatment": arm.selector,
        "physical": arm.physical,
        "strict": arm.strict,
        "values": values,
        "fingerprint": parsed["fingerprint"],
        "reductions": parsed["reductions"],
    }
    (arm_root / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    return result


def run_matrix(
    out: Path, gem5_source: Path, ramulator_source: Path
) -> dict[str, object]:
    require(not out.exists(), f"output already exists: {out}")
    require(
        not subprocess.check_output(
            ["git", "status", "--short"], cwd=ROOT, text=True
        ).strip(),
        "dirty source worktree",
    )
    require(
        gem5_source.is_file() and sha256(gem5_source) == GEM5_SHA256,
        "gem5 binary identity",
    )
    require(
        ramulator_source.is_file()
        and sha256(ramulator_source) == RAMULATOR_SHA256,
        "Ramulator identity",
    )
    out.mkdir(parents=True)
    (out / "input").mkdir()
    (out / "arms").mkdir()
    immutable_copy(gem5_source, out / "input/gem5.opt")
    immutable_copy(ramulator_source, out / "input/libramulator.so")
    immutable_copy(
        ROOT / "ext/ramulator2/ramulator2/example_gem5_config.yaml",
        out / "input/ramulator.yaml",
    )
    gem5 = out / "input/gem5.opt"
    ramulator_yaml = out / "input/ramulator.yaml"
    environment = dict(
        os.environ,
        LD_LIBRARY_PATH=str(out / "input"),
        OMP_NUM_THREADS="4",
        OMP_PROC_BIND="false",
    )
    compile_command = [
        os.environ.get("CXX", "g++"),
        "-Ibenchmarks/API",
        "-Iinclude",
        "-Iutil/m5/src",
        "-std=c++17",
        "-O3",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wno-ignored-qualifiers",
        "-Wno-unused-parameter",
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
        "-DCG_NA=256",
        "-DNUM_CORES=4",
        "-DNUM_TILES_PER_CORE=8",
        "-DTILE_SIZE=16384",
        "-DMAA_MEM_SIZE=0x80000000",
        "util/m5/src/abi/x86/m5op.S",
        "benchmarks/NAS/cg/cg.cpp",
        "-o",
        str(out / "input/cg_guest"),
    ]
    subprocess.run(compile_command, cwd=ROOT, check=True)
    (out / "input/compile_command.json").write_text(
        json.dumps(compile_command, indent=2) + "\n"
    )
    guest = out / "input/cg_guest"
    guest.chmod(0o555)
    selector = out / "input/checkpoint.selector"
    selector.write_text("token_stream_ld legacy_4k\n")
    selector.chmod(0o444)
    Path(SELECTOR_TARGET).write_text("token_stream_ld legacy_4k\n")
    checkpoint = out / "checkpoint"
    checkpoint_command = [
        str(gem5),
        "--listener-mode=off",
        f"--outdir={checkpoint}",
        str(ROOT / "configs/deprecated/example/se.py"),
        "--cpu-type=AtomicSimpleCPU",
        "-n",
        "4",
        "--mem-size=2GB",
        "--max-checkpoints=1",
        "--cmd",
        str(guest),
        "--options",
        f"MAA_DEFERRED {SELECTOR_TARGET}",
    ]
    rc, record = run_logged(
        checkpoint_command, out / "checkpoint.log", environment
    )
    (out / "checkpoint.exit").write_text(f"{rc}\n")
    (out / "checkpoint.process.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n"
    )
    checkpoint_dirs = [
        p
        for p in checkpoint.glob("cpt.*")
        if p.is_dir() and p.name[4:].isdigit()
    ]
    require(
        rc == 0 and len(checkpoint_dirs) == 1,
        "checkpoint did not complete exactly once",
    )
    checkpoint_ledger = out / "input/checkpoint.files.sha256"
    write_tree_ledger(checkpoint, checkpoint_ledger)
    commands: dict[str, list[str]] = {}
    processes: dict[
        str, tuple[subprocess.Popen[bytes], Path, dict[str, object]]
    ] = {}
    for arm in ARMS:
        arm_root = out / "arms" / arm.name
        arm_root.mkdir()
        arm_selector = arm_root / "treatment.selector"
        arm_selector.write_text(f"token_stream_ld {arm.selector}\n")
        arm_selector.chmod(0o444)
        command = common_command(
            gem5,
            arm_root,
            checkpoint,
            guest,
            ramulator_yaml,
            arm.physical,
            arm.strict,
            arm.strict,
        )
        commands[arm.name] = command
        process, _, record = launch_with_selector(
            command, arm_selector, arm_root / "restore.log", environment
        )
        processes[arm.name] = (process, arm_root / "restore.log", record)
        (arm_root / "command.json").write_text(
            json.dumps(command, indent=2) + "\n"
        )
    for arm in ARMS:
        process, log, record = processes[arm.name]
        rc = process.wait()
        record.update(
            {
                "returncode": rc,
                "ended_ns": time.time_ns(),
                "pid_absent": proc_start_ticks(process.pid) is None,
            }
        )
        (out / "arms" / arm.name / "restore.exit").write_text(f"{rc}\n")
        (out / "arms" / arm.name / "process.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n"
        )
        require(rc == 0, f"{arm.name} restore failed; see {log}")
    results: dict[str, dict[str, object]] = {}
    reference = None
    for arm in ARMS:
        result = validate_arm(out, arm, reference)
        if reference is None:
            reference = result
        results[arm.name] = result
    normalized = {
        name: normalize(command) for name, command in commands.items()
    }
    require(
        len({json.dumps(value) for value in normalized.values()}) == 1,
        "non-treatment command drift",
    )
    strict_storage = out / "arms/strict_two_pass/storage"
    strict_storage.mkdir(parents=True)
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "experiments/scripts/report_maa_storage.py"),
            str(out / "arms/strict_two_pass/config.ini"),
            "--output-dir",
            str(strict_storage),
            "--mechanism",
            "generic-virtual",
            "--word-bytes",
            "4",
            "--dram-subslices",
            "32",
            "--dram-ranks",
            "2",
        ],
        check=True,
    )
    storage = json.loads((strict_storage / "maa_storage.json").read_text())
    virtual = storage["virtual_data_buffers"]
    require(
        virtual["destination_combiner_word_pool_per_indirect_unit"] == 256,
        "combiner bound",
    )
    require(
        virtual["source_response_slots_per_indirect_unit"] == 8,
        "response slot bound",
    )
    require(
        virtual["active_total_bytes_per_indirect_unit"] > 0,
        "empty bounded storage",
    )
    require(
        virtual["destination_combiner_word_pool_per_indirect_unit"] + 8 * 16
        <= 4096,
        "result storage exceeds physical budget",
    )
    manifest = {
        "schema": "dx100.cg.strict_fourarm_matrix.v1",
        "cg_na": CG_NA,
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "simulator_source_commit": SIMULATOR_SOURCE_COMMIT,
        "gem5_sha256": GEM5_SHA256,
        "ramulator_sha256": RAMULATOR_SHA256,
        "runtime_python_sha256": runtime_hashes(SIMULATOR_SOURCE_COMMIT),
        "same_guest": sha256(guest),
        "same_checkpoint_identity": sha256(checkpoint_ledger),
        "arms": results,
        "treatment_delta": {
            arm.name: {
                "selector": arm.selector,
                "physical_tile_elements": arm.physical,
                "strict_two_phase": arm.strict,
            }
            for arm in ARMS
        },
        "bounded_storage": {
            "combiner_words_per_unit": virtual[
                "destination_combiner_word_pool_per_indirect_unit"
            ],
            "response_slots_per_unit": virtual[
                "source_response_slots_per_indirect_unit"
            ],
            "payload_words_per_cycle": 8,
            "payload_banks": 32,
            "active_lines": 1,
            "physical_result_budget_words": 4096,
        },
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    write_tree_ledger(out, out / "raw_root.sha256")
    (out / "matrix.complete").write_text(
        "COMPLETE_CG_STRICT_FOURARM\ncorrectness=EXACT_FINGERPRINT_AND_REDUCTIONS\n"
    )
    return manifest


def validate_existing(out: Path) -> dict[str, object]:
    verify_ledger(out)
    manifest = json.loads((out / "manifest.json").read_text())
    require(
        manifest["schema"] == "dx100.cg.strict_fourarm_matrix.v1",
        "manifest schema",
    )
    require((out / "matrix.complete").is_file(), "matrix is not terminal")
    reference = manifest["arms"]["native16"]
    for arm in ARMS:
        validate_arm(out, arm, reference if arm.name != "native16" else None)
    verify_ledger(out)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "validate"))
    parser.add_argument("out", type=Path)
    parser.add_argument("--gem5", type=Path, default=DEFAULT_GEM5)
    parser.add_argument("--ramulator", type=Path, default=DEFAULT_RAMULATOR)
    args = parser.parse_args(argv)
    result = (
        run_matrix(
            args.out.resolve(), args.gem5.resolve(), args.ramulator.resolve()
        )
        if args.command == "run"
        else validate_existing(args.out.resolve())
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
