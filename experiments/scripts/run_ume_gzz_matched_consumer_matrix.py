#!/usr/bin/env python3
"""Run fresh exact-output GZZ controls with matched MAA page arithmetic.

One guest is compiled with an opt-in benchmark selector and restored as
native16, native4x4, and strict logical16/physical4.  Every selector is read
before its checkpoint.  The runner freezes the current simulator, Ramulator,
guest, selector, and checkpoint identities and accepts performance only when
exact output and the predicted gather/arithmetic mechanism counters pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import (
    asdict,
    dataclass,
)
from pathlib import Path
from typing import (
    Any,
    Mapping,
    Sequence,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.scripts import run_ume_two_pass_matrix as legacy  # noqa: E402

MatrixError = legacy.MatrixError
require = legacy.require

BASE_COMMIT = "e69f432bd911f2a68e256bf6d78493c3392fb312"
EXPECTED_GEM5_SHA256 = (
    "d1f8a3d5a736ef645849efee6323f1a6aa8cdd392bdff8b9aeb4d0d4adc6db47"
)
EXPECTED_RAMULATOR_SHA256 = legacy.EXPECTED_RAMULATOR_SHA256
EXPECTED_RAMULATOR_CONFIG_SHA256 = legacy.EXPECTED_RAMULATOR_CONFIG_SHA256
DEFAULT_BUILD_ROOT = legacy.DEFAULT_BUILD_ROOT
DEFAULT_GEM5 = DEFAULT_BUILD_ROOT / "build/X86/gem5.opt"
DEFAULT_RAMULATOR = (
    DEFAULT_BUILD_ROOT / "ext/ramulator2/ramulator2/libramulator.so"
)
DEFAULT_RAMULATOR_CONFIG = (
    DEFAULT_BUILD_ROOT / "ext/ramulator2/ramulator2/example_gem5_config.yaml"
)

ELEMENTS = legacy.ELEMENTS
OUTPUT_ELEMENTS = legacy.OUTPUT_ELEMENTS
EXPECTED_OUTPUT_HASH = legacy.EXPECTED_OUTPUT_HASH
EXPECTED_ACTIVE_CORNERS = legacy.EXPECTED_ACTIVE_CORNERS
EXPECTED_BACKING_BYTES = legacy.EXPECTED_BACKING_BYTES
EXPECTED_BACKING_LINES = legacy.EXPECTED_BACKING_LINES
EXPECTED_PAGES = legacy.EXPECTED_PAGES
RESULT_WORD_BOUND = legacy.RESULT_WORD_BOUND

STRICT_PRODUCTION_PATHS = (
    "benchmarks/API/MAA_gem5.hpp",
    "benchmarks/API/MAA_virtual_materialize.hpp",
    "src/mem/MAA/IndirectAccess.cc",
    "src/mem/MAA/MAA.cc",
    "src/mem/MAA/MAA.py",
    "configs/common/Options.py",
    "configs/common/MAAConfig.py",
    "configs/deprecated/example/se.py",
)


@dataclass(frozen=True)
class Arm:
    name: str
    selector: str
    logical: int
    physical: int
    strict: bool
    complete_line: bool
    combine_slots: int
    combine_words: int
    response_words: int
    arithmetic_pages: int
    expected_indirect_reads: int
    expected_indirect_rmws: int
    expected_scalar_alus: int
    expected_vector_alus: int
    gather: str

    @property
    def guest(self) -> str:
        return "matched"

    @property
    def result_words(self) -> int:
        return self.combine_words + self.response_words


ARMS = (
    Arm(
        "native16",
        "native16",
        16_384,
        16_384,
        False,
        False,
        512,
        3_584,
        512,
        1,
        2,
        2,
        2,
        2,
        "native",
    ),
    Arm(
        "native4x4",
        "native4x4",
        16_384,
        4_096,
        False,
        False,
        512,
        3_584,
        512,
        4,
        8,
        8,
        8,
        8,
        "native",
    ),
    Arm(
        "strict_logical16_physical4",
        "strict_logical16_physical4",
        16_384,
        4_096,
        True,
        True,
        2_048,
        3_072,
        1_024,
        4,
        5,
        8,
        8,
        8,
        "virtual",
    ),
)


def sha256(path: Path) -> str:
    return legacy.sha256(path)


def atomic_text(path: Path, value: str) -> None:
    legacy.atomic_text(path, value)


def atomic_json(path: Path, value: Any) -> None:
    legacy.atomic_json(path, value)


def committed_source() -> dict[str, str]:
    require(
        not subprocess.check_output(
            ["git", "status", "--short"], cwd=ROOT, text=True
        ).strip(),
        "refusing evidence launch from a dirty worktree",
    )
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    paths = (
        "benchmarks/UME/gradzatz.cpp",
        "experiments/scripts/run_ume_gzz_matched_consumer_matrix.py",
    )
    hashes: dict[str, str] = {}
    for relative in paths:
        live = (ROOT / relative).read_bytes()
        committed = subprocess.check_output(
            ["git", "show", f"{commit}:{relative}"], cwd=ROOT
        )
        require(live == committed, f"uncommitted source: {relative}")
        hashes[relative] = hashlib.sha256(live).hexdigest()
    return {"runner_source_commit": commit, **hashes}


def source_contract() -> dict[str, Any]:
    source = (ROOT / "benchmarks/UME/gradzatz.cpp").read_text()
    required = (
        "UME_GRADZATZ_MATCHED_PAGE_ARITHMETIC",
        'treatment == "native16"',
        'treatment == "native4x4"',
        'treatment == "strict_logical16_physical4"',
        "read_gzz_matched_treatment(virtual_consumer_selector)",
        "m5_checkpoint(0, 0)",
        "void gradzatz_MAA_matched_native()",
        "maa_indirect_load_virtual_index<DATATYPE>(",
        "Operation_t::DIV_OP",
        "Operation_t::MUL_OP",
        "UME_GZZ_MATCHED_SELECTOR treatment=",
    )
    missing = [token for token in required if token not in source]
    require(not missing, f"matched benchmark contract missing {missing}")
    native_begin = source.index("void gradzatz_MAA_matched_native()")
    native_end = source.index("#ifdef VERIFY", native_begin)
    native = source[native_begin:native_end]
    require(
        "maa_indirect_load<DATATYPE>(" in native
        and "point_gradient.data()" in native
        and "maa_indirect_load_virtual" not in native,
        "matched native helper changed gather semantics",
    )
    require(
        source.index("read_gzz_matched_treatment(virtual_consumer_selector)")
        < source.index("m5_checkpoint(0, 0)"),
        "selector is not resolved before checkpoint",
    )
    production_hashes = {}
    for relative in STRICT_PRODUCTION_PATHS:
        live = (ROOT / relative).read_bytes()
        baseline = subprocess.check_output(
            ["git", "show", f"{BASE_COMMIT}:{relative}"], cwd=ROOT
        )
        require(
            live == baseline,
            f"strict shared-payload production drift: {relative}",
        )
        production_hashes[relative] = hashlib.sha256(live).hexdigest()
    require(
        legacy.deterministic_output_hash(ELEMENTS)
        == int(EXPECTED_OUTPUT_HASH),
        "fixed-input output fingerprint changed",
    )
    return {
        "status": "PASS",
        "base_commit": BASE_COMMIT,
        "selector_resolved_before_checkpoint": True,
        "one_guest": True,
        "native_gather_opcode_preserved": True,
        "strict_control_flow_separate_from_native_helper": True,
        "strict_production_sha256": production_hashes,
    }


def plan() -> dict[str, Any]:
    return {
        "schema": "dx100.ume_gzz_matched_consumer.plan.v1",
        "audit": source_contract(),
        "same_simulator_binary": True,
        "same_guest_binary": True,
        "historical_controls_reused": False,
        "input_elements": ELEMENTS,
        "expected_output_hash": EXPECTED_OUTPUT_HASH,
        "arms": [
            asdict(arm) | {"result_words": arm.result_words} for arm in ARMS
        ],
        "acceptance": {
            "exact_cross_arm_output": True,
            "vector_alus_per_arithmetic_page": 2,
            "native_gather": ["native16", "native4x4"],
            "strict_operations": 1,
            "strict_descriptors": ELEMENTS,
            "strict_pages_ready": EXPECTED_PAGES,
            "strict_a_issues_at_admission_close": 0,
        },
    }


def build_guest(root: Path) -> tuple[Path, list[list[str]]]:
    build = root / "build"
    build.mkdir()
    m5op_source = ROOT / "util/m5/build/x86/abi/x86/m5op.S"
    if not m5op_source.is_file():
        m5op_source = ROOT / "util/m5/src/abi/x86/m5op.S"
    require(m5op_source.is_file(), "missing m5op.S")
    m5op = build / "m5op.o"
    guest = build / "gradzatz_matched"
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
            f"-DUME_GRADZATZ_EXPECTED_N={ELEMENTS}",
            f"-DUME_GRADZATZ_EXPECTED_HASH={EXPECTED_OUTPUT_HASH}ULL",
            "-DTILE_SIZE=16384",
            "-DMAA_VIRTUAL_GATHER",
            "-DMAA_GENERAL_VIRTUAL_CONSUMER",
            "-DMAA_CONSUMER_TILE_SIZE=4096",
            "-DUME_GRADZATZ_MATCHED_PAGE_ARITHMETIC",
            str(m5op),
            str(ROOT / "benchmarks/UME/gradzatz.cpp"),
            "-o",
            str(guest),
        ],
    ]
    atomic_json(root / "build.commands.json", commands)
    for index, command in enumerate(commands):
        with (root / f"build.{index}.log").open("wb") as output:
            completed = subprocess.run(
                command, stdout=output, stderr=subprocess.STDOUT, check=False
            )
        require(completed.returncode == 0, f"guest build {index} failed")
    require(guest.is_file(), "matched guest was not built")
    guest.chmod(0o555)
    return guest.resolve(), commands


def arm_options(selector: Path) -> str:
    return f"{ELEMENTS} {selector}"


def last_trace_tick(path: Path) -> tuple[int, int] | None:
    """Return the last flushed trace tick and file size without scanning it."""
    try:
        size = path.stat().st_size
        if size == 0:
            return None
        with path.open("rb") as stream:
            stream.seek(max(0, size - 64 * 1024))
            tail = stream.read().decode(errors="ignore")
    except FileNotFoundError:
        return None
    matches = re.findall(r"(?:^|\n)([0-9]+):", tail)
    return (int(matches[-1]), size) if matches else None


def run_restore_bounded(
    command: Sequence[str],
    directory: Path,
    environment: Mapping[str, str],
    *,
    timeout_seconds: int,
    no_progress_seconds: int,
) -> int:
    """Run one restore with PID identity and a trace-tick progress guard."""
    atomic_json(directory / "restore.command.json", list(command))
    log = directory / "restore.log"
    record_path = directory / "restore.process.json"
    trace = directory / "run/contract_trace.log"
    with log.open("wb") as output:
        process = subprocess.Popen(
            list(command),
            stdout=output,
            stderr=subprocess.STDOUT,
            env=dict(environment),
        )
        start_ticks = legacy.base.proc_start_ticks(process.pid)
        require(start_ticks is not None, "restore lacks process identity")
        started = time.monotonic()
        record: dict[str, Any] = {
            "pid": process.pid,
            "proc_start_ticks": start_ticks,
            "boot_id": Path("/proc/sys/kernel/random/boot_id")
            .read_text()
            .strip(),
            "observed_start_unix_ns": time.time_ns(),
            "command_sha256": hashlib.sha256(
                json.dumps(list(command), separators=(",", ":")).encode()
            ).hexdigest(),
            "timeout_seconds": timeout_seconds,
            "no_progress_seconds": no_progress_seconds,
        }
        atomic_json(record_path, record)
        last_tick: int | None = None
        last_size = 0
        last_progress = time.monotonic()
        failure: str | None = None
        while process.poll() is None:
            time.sleep(5)
            now = time.monotonic()
            observation = last_trace_tick(trace)
            if observation is not None:
                tick, size = observation
                if tick != last_tick:
                    last_tick = tick
                    last_progress = now
                elif (
                    size > last_size
                    and now - last_progress >= no_progress_seconds
                ):
                    failure = (
                        f"trace tick {tick} did not advance for "
                        f"{no_progress_seconds}s while trace grew"
                    )
                    break
                last_size = size
            if now - started >= timeout_seconds:
                failure = f"restore exceeded {timeout_seconds}s wall timeout"
                break
        if failure is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        returncode = process.wait()
    record.update(
        {
            "returncode": returncode,
            "observed_end_unix_ns": time.time_ns(),
            "pid_identity_absent": (
                legacy.base.proc_start_ticks(process.pid) != start_ticks
            ),
            "guard_failure": failure,
            "last_trace_tick": last_tick,
        }
    )
    atomic_json(record_path, record)
    atomic_text(directory / "restore.exit", f"{returncode}\n")
    require(failure is None, failure or "restore progress guard")
    return returncode


def prepare(
    root: Path, gem5: Path, ramulator: Path, ramulator_config: Path
) -> dict[str, Any]:
    require(not root.exists(), f"refusing existing output: {root}")
    audit = source_contract()
    source_identity = committed_source()
    require(
        gem5.is_file() and sha256(gem5) == EXPECTED_GEM5_SHA256,
        "unexpected gem5 binary",
    )
    require(
        ramulator.is_file() and sha256(ramulator) == EXPECTED_RAMULATOR_SHA256,
        "unexpected Ramulator library",
    )
    require(
        ramulator_config.is_file()
        and sha256(ramulator_config) == EXPECTED_RAMULATOR_CONFIG_SHA256,
        "unexpected Ramulator config",
    )
    root.mkdir(parents=True)
    inputs = root / "inputs"
    inputs.mkdir()
    frozen_gem5 = inputs / "gem5.opt"
    frozen_ramulator = inputs / "libramulator.so"
    frozen_config = inputs / "ramulator.yaml"
    legacy.copy_stable(gem5, frozen_gem5)
    legacy.copy_stable(ramulator, frozen_ramulator)
    legacy.copy_stable(ramulator_config, frozen_config)
    frozen_gem5.chmod(0o555)
    frozen_ramulator.chmod(0o555)
    frozen_config.chmod(0o444)
    guest, commands = build_guest(inputs)
    selectors: dict[str, Path] = {}
    selector_sha256: dict[str, str] = {}
    for arm in ARMS:
        selector = inputs / f"{arm.name}.selector"
        atomic_text(selector, arm.selector + "\n")
        selector.chmod(0o444)
        selectors[arm.name] = selector.resolve()
        selector_sha256[arm.name] = sha256(selector)
    environment = dict(os.environ)
    environment["LD_LIBRARY_PATH"] = str(inputs) + (
        ":" + environment["LD_LIBRARY_PATH"]
        if environment.get("LD_LIBRARY_PATH")
        else ""
    )
    environment["OMP_NUM_THREADS"] = "4"
    environment["OMP_PROC_BIND"] = "false"
    ldd = subprocess.check_output(
        ["ldd", str(frozen_gem5)], env=environment, text=True
    )
    atomic_text(inputs / "gem5.ldd.txt", ldd)
    match = re.search(r"^[ \t]*libramulator\.so => (\S+)", ldd, re.M)
    require(match is not None, "frozen gem5 did not resolve Ramulator")
    require(
        Path(match.group(1)).resolve() == frozen_ramulator.resolve(),
        "frozen gem5 resolved the wrong Ramulator library",
    )
    manifest = {
        "schema": "dx100.ume_gzz_matched_consumer.campaign.v1",
        "base_commit": BASE_COMMIT,
        "source_identity": source_identity,
        "source_contract": audit,
        "gem5_sha256": sha256(frozen_gem5),
        "ramulator_sha256": sha256(frozen_ramulator),
        "ramulator_config_sha256": sha256(frozen_config),
        "guest_sha256": sha256(guest),
        "selector_sha256": selector_sha256,
        "build_commands": commands,
        "same_simulator_binary": True,
        "same_guest_binary": True,
        "historical_controls_reused": False,
        "selector_resolved_before_checkpoint": True,
        "input_elements": ELEMENTS,
        "expected_output_hash": EXPECTED_OUTPUT_HASH,
        "arms": [
            asdict(arm) | {"result_words": arm.result_words} for arm in ARMS
        ],
    }
    atomic_json(root / "manifest.json", manifest)
    return {
        "gem5": frozen_gem5.resolve(),
        "ramulator": frozen_ramulator.resolve(),
        "config": frozen_config.resolve(),
        "guest": guest,
        "selectors": selectors,
        "environment": environment,
        "manifest": manifest,
    }


def run_campaign(
    root: Path,
    gem5: Path,
    ramulator: Path,
    ramulator_config: Path,
    max_parallel: int,
    restore_timeout_seconds: int,
    no_progress_seconds: int,
) -> dict[str, Any]:
    prepared = prepare(root, gem5, ramulator, ramulator_config)
    atomic_text(root / "campaign.exit", "running\n")
    identities: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        checkpoint = root / "checkpoints" / arm.name
        checkpoint.mkdir(parents=True)
        command = legacy.checkpoint_command(
            prepared["gem5"],
            prepared["guest"],
            checkpoint / "gem5",
            arm_options(prepared["selectors"][arm.name]),
        )
        rc = legacy.run_logged(
            command, checkpoint, "checkpoint", prepared["environment"]
        )
        require(rc == 0, f"{arm.name}: checkpoint failed")
        log = (checkpoint / "checkpoint.log").read_text(errors="replace")
        require("because checkpoint" in log, f"{arm.name}: checkpoint marker")
        identity = legacy.tree_identity(checkpoint / "gem5")
        identities[arm.name] = identity
        atomic_json(checkpoint / "identity.json", identity)

    def restore(arm: Arm) -> str | None:
        try:
            checkpoint = root / "checkpoints" / arm.name / "gem5"
            require(
                legacy.tree_identity(checkpoint)["sha256"]
                == identities[arm.name]["sha256"],
                f"{arm.name}: checkpoint changed before restore",
            )
            arm_root = root / "arms" / arm.name
            arm_root.mkdir(parents=True)
            command = legacy.common_restore_command(
                prepared["gem5"],
                prepared["config"],
                checkpoint,
                prepared["guest"],
                arm_options(prepared["selectors"][arm.name]),
                arm_root / "run",
                arm,
            )
            rc = run_restore_bounded(
                command,
                arm_root,
                prepared["environment"],
                timeout_seconds=restore_timeout_seconds,
                no_progress_seconds=no_progress_seconds,
            )
            require(rc == 0, f"{arm.name}: restore rc={rc}")
            require(
                legacy.tree_identity(checkpoint)["sha256"]
                == identities[arm.name]["sha256"],
                f"{arm.name}: checkpoint mutated during restore",
            )
        except (OSError, subprocess.SubprocessError, MatrixError) as error:
            return f"{arm.name}: {error}"
        return None

    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        failures = [failure for failure in pool.map(restore, ARMS) if failure]
    if failures:
        raise MatrixError("; ".join(failures))
    result = validate_campaign(root)
    atomic_json(root / "result.json", result)
    atomic_text(
        root / "gate.complete",
        "ACCEPT_FRESH_GZZ_MATCHED_CONSUMER\ncorrectness=EXACT_REFERENCE\n",
    )
    atomic_text(root / "campaign.exit", "0\n")
    write_ledger(root)
    validate_ledger(root)
    return result


def one_marker(text: str, prefix: str, arm: str) -> dict[str, str]:
    return legacy.one_marker(text, prefix, arm)


def optional_sum(stats: Mapping[str, float], suffix: str) -> int:
    return legacy.optional_sum(stats, suffix)


def classify_arm(
    root: Path, arm: Arm, manifest: Mapping[str, Any]
) -> dict[str, Any]:
    arm_root = root / "arms" / arm.name
    require((arm_root / "restore.exit").read_text() == "0\n", arm.name)
    legacy.base.validate_process_record(arm_root / "restore.process.json")
    log_path = arm_root / "restore.log"
    log = log_path.read_text(errors="replace")
    require(
        len(re.findall(r"because m5_exit instruction encountered", log)) == 1,
        f"{arm.name}: missing terminal m5_exit",
    )
    require(
        not re.search(
            r"panic:|fatal:|assertion .*failed|segmentation fault|abort",
            log,
            re.I,
        ),
        f"{arm.name}: fatal simulator text",
    )
    output = one_marker(log, "UME_OUTPUT_FP ", arm.name)
    reference = one_marker(log, "UME_REFERENCE_PASS ", arm.name)
    require(
        output == {"output_hash": EXPECTED_OUTPUT_HASH, "nonfinite": "0"},
        f"{arm.name}: output fingerprint",
    )
    require(
        reference
        == {
            "volume_errors": "0",
            "gradient_errors": "0",
            "elements": str(OUTPUT_ELEMENTS),
        },
        f"{arm.name}: scalar reference",
    )
    selector = one_marker(log, "UME_GZZ_MATCHED_SELECTOR ", arm.name)
    require(
        selector
        == {
            "treatment": arm.selector,
            "logical": "16384",
            "consumer": str(16_384 // arm.arithmetic_pages),
            "gather": arm.gather,
        },
        f"{arm.name}: selector marker",
    )
    consumer = one_marker(log, "UME_GZZ_PAGE_CONSUMER ", arm.name)
    require(
        consumer
        == {
            "mode": "maa_div_mul",
            "treatment": arm.selector,
            "arithmetic_pages": str(arm.arithmetic_pages),
            "physical_tiles_per_core": "7",
            "cpu_spd_payload_reads": "0",
            "gather": arm.gather,
        },
        f"{arm.name}: page-consumer marker",
    )
    stats = legacy.base.first_stats_section(arm_root / "run/stats.txt")
    counters = {
        "simTicks": legacy.base.exact_stat(stats, "simTicks"),
        "simInsts": legacy.base.exact_stat(stats, "simInsts"),
        "numInst_INDRD": legacy.base.exact_stat(
            stats, "system.maa.numInst_INDRD"
        ),
        "numInst_INDRMW": legacy.base.exact_stat(
            stats, "system.maa.numInst_INDRMW"
        ),
        "numInst_ALUS": legacy.base.exact_stat(
            stats, "system.maa.numInst_ALUS"
        ),
        "numInst_ALUV": legacy.base.exact_stat(
            stats, "system.maa.numInst_ALUV"
        ),
        "index_words": optional_sum(stats, "IND_VirtIndexWords"),
        "write_issues": optional_sum(stats, "IND_VirtWriteIssues"),
        "write_completions": optional_sum(stats, "IND_VirtWriteCompletions"),
        "full_line_writes": optional_sum(stats, "IND_VirtFullLineWrites"),
        "partial_writes": optional_sum(stats, "IND_VirtPartialWrites"),
        "pages_ready": optional_sum(stats, "IND_VirtPagesReady"),
        "strict_operations": optional_sum(
            stats, "IND_StrictTwoPhaseOperations"
        ),
        "strict_descriptors": optional_sum(
            stats, "IND_StrictTwoPhaseDescriptors"
        ),
        "strict_backing_issues": optional_sum(
            stats, "IND_StrictTwoPhaseBackingIssues"
        ),
        "payload_starts": optional_sum(
            stats, "IND_VirtCompleteLinePayloadStarts"
        ),
        "payload_completions": optional_sum(
            stats, "IND_VirtCompleteLinePayloadCompletions"
        ),
        "payload_scheduled_words": optional_sum(
            stats, "IND_VirtCompleteLinePayloadScheduledWords"
        ),
        "payload_read_words": optional_sum(
            stats, "IND_VirtCompleteLinePayloadReadWords"
        ),
    }
    expected_work = {
        "numInst_INDRD": arm.expected_indirect_reads,
        "numInst_INDRMW": arm.expected_indirect_rmws,
        "numInst_ALUS": arm.expected_scalar_alus,
        "numInst_ALUV": arm.expected_vector_alus,
    }
    for field, expected in expected_work.items():
        require(counters[field] == expected, f"{arm.name}: {field} work")
    require(
        counters["numInst_ALUV"] == 2 * arm.arithmetic_pages,
        f"{arm.name}: DIV/MUL page signature",
    )
    expected_virtual_index_words = ELEMENTS if arm.gather == "virtual" else 0
    require(
        counters["index_words"] == expected_virtual_index_words,
        f"{arm.name}: gather index work",
    )
    config = legacy.base.parse_config(arm_root / "run/config.ini")
    expected_config = {
        "num_tile_elements": str(arm.logical),
        "physical_tile_elements": str(arm.physical),
        "virtual_strict_two_phase": "true" if arm.strict else "false",
        "virtual_complete_line_only": "true" if arm.complete_line else "false",
        "virtual_combine_words": str(arm.combine_words),
        "virtual_response_word_pool": str(arm.response_words),
        "no_reorder": "false",
        "reconfigure_row_table": "false",
    }
    for key, expected in expected_config.items():
        require(config.get(key) == expected, f"{arm.name}: config {key}")
    trace_path = arm_root / "run/contract_trace.log"
    trace_lines = trace_path.read_text(errors="replace").splitlines()
    strict_events = [
        event
        for line in trace_lines
        if (event := legacy.base.parse_event(line, "strict_two_phase_timing"))
    ]
    strict_trace = None
    admission = None
    if arm.strict:
        require(len(strict_events) == 1, f"{arm.name}: strict activation")
        strict_trace = strict_events[0]
        admission = legacy.base.exactly_one_event(
            trace_lines, "strict_two_phase_admission_closed"
        )
        for key, value in {
            "schema": "2",
            "logical": "16384",
            "physical": "4096",
            "result_words": "4096",
            "b_words": "16384",
            "descriptors": "16384",
            "pages_ready": "4",
            "backing_semantic_bytes": str(EXPECTED_BACKING_BYTES),
            "exact_b_once": "1",
            "raw_b_retained_bytes": "0",
            "descriptor_backing_bytes": "0",
            "replay_passes": "0",
            "coherent_ack": "1",
            "order_ok": "1",
            "terminal": "1",
        }.items():
            require(strict_trace.get(key) == value, f"strict trace {key}")
        for key, value in {
            "schema": "2",
            "b_words": "16384",
            "descriptors": "16384",
            "offsets": "16384",
            "raw_b_buffered_words": "0",
            "a_issues": "0",
        }.items():
            require(admission.get(key) == value, f"strict admission {key}")
        require(
            int(strict_trace["A_FIRST_ISSUE"])
            >= int(strict_trace["ROW_OFFSET_LAST_INSERT"]),
            "strict A issue preceded admission",
        )
        require(
            strict_trace["a_issues"] == strict_trace["a_responses"],
            "strict A response closure",
        )
        require(
            strict_trace["backing_issues"] == strict_trace["backing_acks"],
            "strict backing ACK closure",
        )
        require(counters["strict_operations"] == 1, "strict operation counter")
        require(
            counters["strict_descriptors"] == ELEMENTS, "strict descriptors"
        )
        require(
            counters["write_issues"]
            == counters["write_completions"]
            == counters["full_line_writes"]
            == EXPECTED_BACKING_LINES,
            "strict complete-line ACK closure",
        )
        require(counters["partial_writes"] == 0, "strict partial writes")
        require(
            counters["pages_ready"] == EXPECTED_PAGES, "strict pages ready"
        )
        require(
            counters["payload_starts"]
            == counters["payload_completions"]
            == EXPECTED_BACKING_LINES,
            "strict payload line closure",
        )
        require(
            counters["payload_scheduled_words"]
            == counters["payload_read_words"]
            == ELEMENTS,
            "strict payload word closure",
        )
    else:
        require(not strict_events, f"{arm.name}: unexpected strict trace")
        require(counters["strict_operations"] == 0, f"{arm.name}: strict work")
        require(
            counters["write_issues"] == counters["write_completions"] == 0,
            f"{arm.name}: virtual backing writes on native gather",
        )
    checkpoint_identity = json.loads(
        (root / "checkpoints" / arm.name / "identity.json").read_text()
    )
    return {
        "classification": "ACCEPT",
        "output_hash": output["output_hash"],
        "reference": reference,
        "selector": selector,
        "consumer": consumer,
        "counters": counters,
        "strict_trace": strict_trace,
        "strict_admission": admission,
        "gem5_sha256": manifest["gem5_sha256"],
        "guest_sha256": manifest["guest_sha256"],
        "selector_sha256": manifest["selector_sha256"][arm.name],
        "checkpoint_sha256": checkpoint_identity["sha256"],
        "restore_log_sha256": sha256(log_path),
        "stats_sha256": sha256(arm_root / "run/stats.txt"),
        "config_sha256": sha256(arm_root / "run/config.ini"),
        "trace_sha256": sha256(trace_path),
    }


def validate_campaign(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "manifest.json").read_text())
    require(
        manifest["schema"] == "dx100.ume_gzz_matched_consumer.campaign.v1",
        "campaign schema",
    )
    require(
        sha256(root / "inputs/gem5.opt")
        == manifest["gem5_sha256"]
        == EXPECTED_GEM5_SHA256,
        "gem5 identity",
    )
    require(
        sha256(root / "inputs/build/gradzatz_matched")
        == manifest["guest_sha256"],
        "guest identity",
    )
    for arm in ARMS:
        require(
            sha256(root / "inputs" / f"{arm.name}.selector")
            == manifest["selector_sha256"][arm.name],
            f"{arm.name}: selector identity",
        )
        identity = json.loads(
            (root / "checkpoints" / arm.name / "identity.json").read_text()
        )
        require(
            legacy.tree_identity(root / "checkpoints" / arm.name / "gem5")[
                "sha256"
            ]
            == identity["sha256"],
            f"{arm.name}: checkpoint identity",
        )
    classified = {arm.name: classify_arm(root, arm, manifest) for arm in ARMS}
    require(
        len({item["output_hash"] for item in classified.values()}) == 1,
        "cross-arm output mismatch",
    )
    require(
        len({item["guest_sha256"] for item in classified.values()}) == 1,
        "cross-arm guest mismatch",
    )
    ticks = {
        name: item["counters"]["simTicks"] for name, item in classified.items()
    }
    strict_name = "strict_logical16_physical4"
    return {
        "schema": "dx100.ume_gzz_matched_consumer.result.v1",
        "terminal": True,
        "decision": "ACCEPT_FRESH_GZZ_MATCHED_CONSUMER",
        "correctness": "EXACT_CROSS_ARM_AND_SCALAR_REFERENCE",
        "performance_metric": "simTicks",
        "same_simulator_binary": True,
        "same_guest_binary": True,
        "fresh_checkpoints": True,
        "historical_controls_reused": False,
        "gem5_sha256": manifest["gem5_sha256"],
        "guest_sha256": manifest["guest_sha256"],
        "arms": classified,
        "ticks": ticks,
        "comparisons": {
            "native16_over_strict": ticks["native16"] / ticks[strict_name],
            "native4x4_over_strict": ticks["native4x4"] / ticks[strict_name],
            "native16_over_native4x4": ticks["native16"] / ticks["native4x4"],
        },
        "limitations": [
            "one deterministic 16K-window observation per arm",
            "GZZ only; no full-application or variability claim",
            "native16 naturally uses one 16K arithmetic page while native4x4 "
            "and strict use four 4K arithmetic pages",
        ],
    }


def ledger_paths(root: Path) -> list[Path]:
    paths = [
        root / "manifest.json",
        root / "campaign.exit",
        root / "result.json",
        root / "gate.complete",
        root / "inputs/gem5.opt",
        root / "inputs/libramulator.so",
        root / "inputs/ramulator.yaml",
        root / "inputs/gem5.ldd.txt",
        root / "inputs/build/gradzatz_matched",
        root / "inputs/build.commands.json",
    ]
    for arm in ARMS:
        paths.append(root / "inputs" / f"{arm.name}.selector")
        checkpoint = root / "checkpoints" / arm.name
        paths.extend(
            checkpoint / name
            for name in (
                "checkpoint.command.json",
                "checkpoint.log",
                "checkpoint.exit",
                "checkpoint.process.json",
                "identity.json",
            )
        )
        arm_root = root / "arms" / arm.name
        paths.extend(
            arm_root / name
            for name in (
                "restore.command.json",
                "restore.log",
                "restore.exit",
                "restore.process.json",
                "run/config.ini",
                "run/stats.txt",
                "run/contract_trace.log",
            )
        )
    return paths


def write_ledger(root: Path) -> None:
    lines = []
    for path in ledger_paths(root):
        require(path.is_file(), f"missing ledger artifact: {path}")
        lines.append(f"{sha256(path)}  {path.relative_to(root)}")
    atomic_text(root / "artifacts.sha256", "\n".join(sorted(lines)) + "\n")


def validate_ledger(root: Path) -> None:
    seen: set[str] = set()
    for line in (root / "artifacts.sha256").read_text().splitlines():
        digest, relative = line.split("  ", 1)
        require(relative not in seen, f"duplicate ledger path: {relative}")
        seen.add(relative)
        require(
            sha256(root / relative) == digest, f"ledger mismatch: {relative}"
        )
    expected = {str(path.relative_to(root)) for path in ledger_paths(root)}
    require(seen == expected, "ledger path set")


def record_rejection(root: Path, reason: str) -> None:
    if not root.is_dir():
        return
    atomic_json(
        root / "failure.json",
        {
            "schema": "dx100.ume_gzz_matched_consumer.rejection.v1",
            "decision": "REJECT",
            "reason": reason,
            "performance_promotable": False,
        },
    )
    atomic_text(root / "campaign.exit", "1\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--validate", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--gem5", type=Path, default=DEFAULT_GEM5)
    parser.add_argument(
        "--ramulator-library", type=Path, default=DEFAULT_RAMULATOR
    )
    parser.add_argument(
        "--ramulator-config", type=Path, default=DEFAULT_RAMULATOR_CONFIG
    )
    parser.add_argument("--max-parallel-restores", type=int, default=3)
    parser.add_argument("--restore-timeout-seconds", type=int, default=1800)
    parser.add_argument("--no-progress-seconds", type=int, default=60)
    args = parser.parse_args(argv)
    if args.execute and args.validate is not None:
        parser.error("--execute and --validate are mutually exclusive")
    if args.execute and args.out is None:
        parser.error("--execute requires --out")
    if not args.execute and args.out is not None:
        parser.error("--out requires --execute")
    if args.max_parallel_restores < 1 or args.max_parallel_restores > 3:
        parser.error("--max-parallel-restores must be in [1, 3]")
    if args.restore_timeout_seconds < 60:
        parser.error("--restore-timeout-seconds must be at least 60")
    if args.no_progress_seconds < 30:
        parser.error("--no-progress-seconds must be at least 30")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.execute:
            result = run_campaign(
                args.out.resolve(),
                args.gem5.resolve(),
                args.ramulator_library.resolve(),
                args.ramulator_config.resolve(),
                args.max_parallel_restores,
                args.restore_timeout_seconds,
                args.no_progress_seconds,
            )
        elif args.validate is not None:
            result = validate_campaign(args.validate.resolve())
            validate_ledger(args.validate.resolve())
        else:
            result = plan()
        print(json.dumps(result, indent=2, sort_keys=True))
    except (OSError, subprocess.SubprocessError, MatrixError) as error:
        failed = args.out if args.execute else args.validate
        if failed is not None:
            record_rejection(failed.resolve(), str(error))
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
