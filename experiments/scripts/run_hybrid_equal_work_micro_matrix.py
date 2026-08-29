#!/usr/bin/env python3
"""Run and validate the exact equal-work DX100 hybrid API micro matrix.

The historical virtual-tile attribution matrix is not a same-binary matrix:
its native4 arm uses a ``TILE_SIZE=4096`` executable while the other arms use
``TILE_SIZE=16384``.  This driver reuses the deferred-treatment support in
``test_virtual_tile_consumer``.  One T16384 executable and one treatment-
neutral checkpoint therefore cover all four arms:

* native16: one direct-index 16K gather/multiply/store operation;
* native4: four direct-index 4K gather/multiply/store operations in the same
  T16K address aperture (logical16/physical4 geometry);
* hybrid1: one strict logical16/physical4 producer with a one-line feeder;
* hybrid64: the same producer with the selected 64-line feeder.

Raw evidence is intentionally written outside Git.  ``validate`` is read-only
and independently reclassifies every arm before checking the sealed result.
"""

from __future__ import annotations

import argparse
import configparser
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import (
    asdict,
    dataclass,
)
from pathlib import Path
from typing import (
    Iterable,
    Mapping,
    Sequence,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GEM5 = Path(
    "/data1/nier/worktrees/codex-sessions/"
    "retirement-ack-identity-hardening-20260827-20260827-234627-d624ed8d/"
    "DX100-virtualization-selected-integration-cont-20260826/"
    "build/X86/gem5.opt"
)
DEFAULT_RAMULATOR = Path(
    "/data1/nier/dx100-runs/"
    "2026-08-12-hybrid-line-handoff-8a5c7712/input/libramulator.so"
)
EXPECTED_GEM5_SHA256 = (
    "2a672ecaef6cd6a273004312d80fdad4446ae880f7b46b41458d0f4e59d37009"
)
EXPECTED_RAMULATOR_SHA256 = (
    "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753"
)
SIMULATOR_SOURCE_COMMIT = "6c180e391e738dfd83376bd88d68a2fcaf48b3cc"
TOTAL_ELEMENTS = 16_384
WORDS_PER_INDEX_LINE = 16
EXPECTED_OUTPUT_HASH = "7228541527853630339"
M5_EXIT_RE = re.compile(
    r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$"
)
RESULT_RE = re.compile(
    r"^VIRTUAL_TILE_CONSUMER_RESULT mode=(?P<mode>[a-z0-9_]+) "
    r"page_elements=(?P<page>[0-9]+) hash=(?P<hash>[0-9]+) errors=0$"
)
RUNTIME_PYTHON_PATHS = (
    "configs/deprecated/example/se.py",
    "configs/common/Options.py",
    "configs/common/MAAConfig.py",
    "configs/common/Simulation.py",
    "src/mem/MAA/MAA.py",
)


class MatrixError(RuntimeError):
    """Fail-closed matrix validation error."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise MatrixError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ArmSpec:
    name: str
    mode: str
    page_elements: int
    logical_elements: int
    physical_elements: int
    feeder_lines: int
    strict: bool
    expected_indirect_ops: int
    expected_stream_writes: int
    expected_scalar_ops: int

    @property
    def treatment(self) -> str:
        return f"{self.mode} {self.page_elements}\n"


ARMS = (
    ArmSpec(
        "native16",
        "native_direct",
        16_384,
        16_384,
        16_384,
        1,
        False,
        1,
        1,
        1,
    ),
    ArmSpec(
        "native4",
        "native_direct",
        4_096,
        16_384,
        4_096,
        1,
        False,
        4,
        4,
        4,
    ),
    ArmSpec(
        "hybrid1",
        "transparent",
        4_096,
        16_384,
        4_096,
        1,
        True,
        1,
        4,
        4,
    ),
    ArmSpec(
        "hybrid64",
        "transparent",
        4_096,
        16_384,
        4_096,
        64,
        True,
        1,
        4,
        4,
    ),
)
HYBRID_SEMANTIC_WORK_FIELDS = (
    "index_words",
    "strict_operations",
    "strict_b_fetch_lines",
    "strict_b_words",
    "strict_descriptors",
    "strict_a_issues",
    "strict_pages_ready",
    "strict_backing_semantic_bytes",
    "offset_epoch_drains",
)


def source_commit() -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()


def source_status() -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), "status", "--short"], text=True
    )


def committed_blob(path: str, commit: str) -> bytes:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), "show", f"{commit}:{path}"]
    )


def verify_runtime_python(commit: str) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in RUNTIME_PYTHON_PATHS:
        live = ROOT / relative
        require(live.is_file(), f"missing runtime Python source: {live}")
        live_bytes = live.read_bytes()
        committed = committed_blob(relative, commit)
        require(
            live_bytes == committed,
            f"runtime Python source differs from simulator commit: {relative}",
        )
        hashes[relative] = hashlib.sha256(live_bytes).hexdigest()
    return hashes


def preflight(gem5: Path, ramulator: Path) -> dict[str, object]:
    require(
        not source_status(), "refusing evidence launch from a dirty worktree"
    )
    require(gem5.is_file(), f"missing gem5 binary: {gem5}")
    require(ramulator.is_file(), f"missing Ramulator library: {ramulator}")
    require(
        sha256_file(gem5) == EXPECTED_GEM5_SHA256,
        "gem5 SHA-256 does not match the selected ACK-hardened binary",
    )
    require(
        sha256_file(ramulator) == EXPECTED_RAMULATOR_SHA256,
        "Ramulator SHA-256 does not match the frozen library",
    )
    runtime_hashes = verify_runtime_python(SIMULATOR_SOURCE_COMMIT)
    environment = os.environ.copy()
    library_path = str(ramulator.parent)
    prior = environment.get("LD_LIBRARY_PATH")
    environment["LD_LIBRARY_PATH"] = (
        f"{library_path}:{prior}" if prior else library_path
    )
    ldd = subprocess.check_output(
        ["ldd", str(gem5)], env=environment, text=True
    )
    match = re.search(r"^[ \t]*libramulator\.so => (\S+)", ldd, re.M)
    require(match is not None, "gem5 does not resolve libramulator.so")
    require(
        Path(match.group(1)).resolve() == ramulator.resolve(),
        "gem5 resolved a different Ramulator library",
    )
    return {
        "runner_source_commit": source_commit(),
        "simulator_source_commit": SIMULATOR_SOURCE_COMMIT,
        "runtime_python_sha256": runtime_hashes,
        "gem5_sha256": EXPECTED_GEM5_SHA256,
        "ramulator_sha256": EXPECTED_RAMULATOR_SHA256,
        "ldd": ldd,
    }


def proc_start_ticks(pid: int) -> int | None:
    path = Path(f"/proc/{pid}/stat")
    try:
        line = path.read_text()
    except FileNotFoundError:
        return None
    closing = line.rfind(")")
    require(closing >= 0, f"invalid /proc stat for pid {pid}")
    fields = line[closing + 2 :].split()
    require(len(fields) > 19, f"short /proc stat for pid {pid}")
    return int(fields[19])


def run_command(
    command: Sequence[str],
    log: Path,
    environment: Mapping[str, str],
    process_record: Path,
) -> int:
    with log.open("wb") as handle:
        process = subprocess.Popen(
            list(command),
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=dict(environment),
        )
        start_ticks = proc_start_ticks(process.pid)
        require(
            start_ticks is not None, "launched process lacks start identity"
        )
        record = {
            "pid": process.pid,
            "proc_start_ticks": start_ticks,
            "boot_id": Path("/proc/sys/kernel/random/boot_id")
            .read_text()
            .strip(),
            "observed_start_unix_ns": time.time_ns(),
            "command_sha256": hashlib.sha256(
                json.dumps(list(command), separators=(",", ":")).encode()
            ).hexdigest(),
        }
        process_record.write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n"
        )
        returncode = process.wait()
    record.update(
        {
            "returncode": returncode,
            "observed_end_unix_ns": time.time_ns(),
            "pid_identity_absent": proc_start_ticks(process.pid)
            != start_ticks,
        }
    )
    process_record.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n"
    )
    return returncode


def copy_reflink(source: Path, destination: Path) -> None:
    subprocess.run(
        [
            "cp",
            "--reflink=auto",
            "--preserve=mode,timestamps",
            str(source),
            str(destination),
        ],
        check=True,
    )


def common_restore_command(
    gem5: Path,
    workload: Path,
    ramulator_yaml: Path,
    checkpoint: Path,
    arm_out: Path,
    selector: Path,
    arm: ArmSpec,
) -> list[str]:
    command = [
        str(gem5),
        "--listener-mode=off",
        f"--outdir={arm_out / 'run'}",
        "--debug-flags=MAAVirtualTrace,MAAMacroEvent",
        "--debug-file=hybrid_trace.log",
        str(ROOT / "configs/deprecated/example/se.py"),
        "--cpu-type",
        "X86O3CPU",
        "-r",
        "1",
        "-n",
        "4",
        "--mem-size",
        "2GB",
        f"--checkpoint-dir={checkpoint}",
        "--sys-clock",
        "3.2GHz",
        "--cpu-clock",
        "3.2GHz",
        "--caches",
        "--l1d_size=32kB",
        "--l1d_assoc=8",
        "--l1d-hwp-type=StridePrefetcher",
        "--l1d_mshrs=16",
        "--l1d_write_buffers=8",
        "--l1i_size=32kB",
        "--l1i_assoc=8",
        "--l1i-hwp-type=StridePrefetcher",
        "--l1i_mshrs=16",
        "--l1i_write_buffers=8",
        "--l2cache",
        "--l2_size=256kB",
        "--l2_assoc=4",
        "--l2-hwp-type=StridePrefetcher",
        "--l2_mshrs=32",
        "--l2_write_buffers=16",
        "--l3cache",
        "--l3_size=8MB",
        "--l3_assoc=16",
        "--l3_mshrs=256",
        "--l3_write_buffers=128",
        "--l3_ports=4",
        "--cacheline_size=64",
        "--mem-type",
        "Ramulator2",
        "--ramulator-config",
        str(ramulator_yaml),
        "--mem-channels=2",
        "--maa",
        f"--maa_num_tile_elements={arm.logical_elements}",
        f"--maa_physical_tile_elements={arm.physical_elements}",
        "--maa_num_initial_row_table_slices=32",
        "--maa_num_row_table_rows_per_slice=64",
        "--maa_num_row_table_entries_per_subslice_row=8",
        f"--maa_num_offset_table_entries={arm.logical_elements}",
        f"--maa_num_offset_table_epoch_entries={arm.logical_elements}",
        "--maa_virtual_combine_slots=16",
        "--maa_virtual_combine_words=0",
        "--maa_virtual_combine_ways=0",
        "--maa_virtual_combine_banks=0",
        "--maa_virtual_response_slots=8",
        "--maa_virtual_response_word_pool=0",
        "--maa_virtual_words_per_cycle=1",
        "--maa_virtual_max_outstanding_writes=32",
        "--maa_virtual_masked_writes",
        f"--maa_virtual_index_buffer_lines={arm.feeder_lines}",
        "--cmd",
        str(workload),
        "--options",
        f"deferred {selector}",
    ]
    if arm.strict:
        command.insert(
            command.index("--cmd"), "--maa_virtual_strict_two_phase"
        )
    return command


def normalized_command(command: Sequence[str]) -> list[str]:
    omitted = (
        "--outdir=",
        "--maa_num_tile_elements=",
        "--maa_physical_tile_elements=",
        "--maa_num_offset_table_entries=",
        "--maa_num_offset_table_epoch_entries=",
        "--maa_virtual_index_buffer_lines=",
    )
    return [
        token
        for token in command
        if not token.startswith(omitted)
        and token != "--maa_virtual_strict_two_phase"
    ]


def first_stats_section(path: Path) -> dict[str, float]:
    require(
        path.is_file() and path.stat().st_size > 0, f"missing stats: {path}"
    )
    values: dict[str, float] = {}
    in_section = False
    closed = False
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        if line == "---------- Begin Simulation Statistics ----------":
            if not in_section and not closed:
                in_section = True
            continue
        if line == "---------- End Simulation Statistics   ----------":
            if in_section:
                closed = True
                break
            continue
        if not in_section:
            continue
        fields = line.split()
        if len(fields) >= 2:
            try:
                values[fields[0]] = float(fields[1])
            except ValueError:
                continue
    require(closed, f"unterminated first stats section: {path}")
    require(
        values.get("simTicks", 0) > 0, f"missing positive simTicks: {path}"
    )
    return values


def exact_stat(stats: Mapping[str, float], name: str) -> int:
    value = stats.get(name)
    require(
        value is not None and value.is_integer(),
        f"missing integer stat {name}",
    )
    return int(value)


def summed_stat(stats: Mapping[str, float], suffix: str) -> int:
    matches = [value for name, value in stats.items() if name.endswith(suffix)]
    require(matches, f"missing per-unit stat suffix {suffix}")
    require(
        all(value.is_integer() for value in matches),
        f"noninteger stat {suffix}",
    )
    return int(sum(matches))


def parse_config(path: Path) -> dict[str, str]:
    parser = configparser.ConfigParser(strict=True)
    with path.open(encoding="utf-8") as handle:
        parser.read_file(handle)
    require(
        parser.has_section("system.maa"), f"missing [system.maa] in {path}"
    )
    return dict(parser.items("system.maa"))


def parse_event(line: str, event: str) -> dict[str, str] | None:
    marker = f"event={event} "
    if marker not in line:
        return None
    fields: dict[str, str] = {}
    for token in line[line.index(marker) :].split():
        if "=" in token:
            key, value = token.split("=", 1)
            fields[key] = value
    return fields


def exactly_one_event(lines: Iterable[str], event: str) -> dict[str, str]:
    matches = [
        parsed for line in lines if (parsed := parse_event(line, event))
    ]
    require(
        len(matches) == 1, f"expected one {event} event, found {len(matches)}"
    )
    return matches[0]


def validate_process_record(path: Path, expected_returncode: int = 0) -> None:
    record = json.loads(path.read_text())
    require(record.get("pid", 0) > 0, f"invalid process PID: {path}")
    require(
        record.get("proc_start_ticks", 0) > 0,
        f"invalid process start identity: {path}",
    )
    require(
        record.get("returncode") == expected_returncode,
        f"process return code mismatch: {path}",
    )
    require(
        record.get("pid_identity_absent") is True,
        f"process identity remains live: {path}",
    )


def validate_masked_retirement(
    counters: Mapping[str, int], arm_name: str, require_partial: bool = True
) -> None:
    require(counters["write_issues"] > 0, f"{arm_name}: no retirement writes")
    require(
        counters["write_issues"] == counters["write_completions"],
        f"{arm_name}: retirement write closure",
    )
    require(
        counters["full_writes"] + counters["partial_writes"]
        == counters["write_issues"],
        f"{arm_name}: masked/full write accounting",
    )
    if require_partial:
        require(
            counters["partial_writes"] > 0,
            f"{arm_name}: masked retirement inactive",
        )
    else:
        require(
            counters["partial_writes"] == 0
            and counters["full_writes"] == counters["write_issues"],
            f"{arm_name}: retirement did not close as full lines",
        )


def validate_strict_fetch_lines(
    counter_lines: int, trace: Mapping[str, str], arm_name: str
) -> None:
    trace_lines = int(trace.get("b_lines", "0"))
    trace_responses = int(trace.get("b_responses", "0"))
    require(
        trace_lines in (1024, 1025),
        f"{arm_name}: strict B line count outside aligned/unaligned bound",
    )
    require(
        counter_lines == trace_lines == trace_responses,
        f"{arm_name}: strict B line/response accounting",
    )


def classify_arm(
    root: Path,
    arm: ArmSpec,
    combine_slots: int = 16,
    combine_words: int = 0,
    strict_result_words: int = 192,
    require_partial_retirement: bool = True,
) -> dict[str, object]:
    arm_root = root / "arms" / arm.name
    require(
        (arm_root / "restore.exit").read_text() == "0\n",
        f"{arm.name}: restore rc",
    )
    validate_process_record(arm_root / "process.json")
    restore_lines = (
        (arm_root / "restore.log")
        .read_text(encoding="utf-8", errors="strict")
        .splitlines()
    )
    terminal_count = sum(
        bool(M5_EXIT_RE.fullmatch(line)) for line in restore_lines
    )
    require(terminal_count == 1, f"{arm.name}: expected one m5_exit marker")
    require(
        restore_lines.count("ROI Ended") == 1, f"{arm.name}: missing ROI Ended"
    )
    require(
        restore_lines.count(
            "VIRTUAL_TILE_CONSUMER_TREATMENT "
            f"mode={arm.mode} page_elements={arm.page_elements} "
            "source=deferred_file_v1"
        )
        == 1,
        f"{arm.name}: deferred treatment mismatch",
    )
    lowered = "\n".join(restore_lines).lower()
    require(
        not re.search(
            r"panic|fatal|assert|abort|segmentation fault|error:", lowered
        ),
        f"{arm.name}: fatal text in restore log",
    )
    result_matches = [RESULT_RE.fullmatch(line) for line in restore_lines]
    result_matches = [match for match in result_matches if match is not None]
    require(len(result_matches) == 1, f"{arm.name}: expected one exact result")
    result_match = result_matches[0]
    require(
        result_match["mode"] == arm.mode, f"{arm.name}: result mode mismatch"
    )
    require(
        int(result_match["page"]) == arm.page_elements,
        f"{arm.name}: result page mismatch",
    )
    require(
        result_match["hash"] == EXPECTED_OUTPUT_HASH,
        f"{arm.name}: output hash mismatch",
    )

    config = parse_config(arm_root / "run/config.ini")
    expected_config = {
        "num_tile_elements": str(arm.logical_elements),
        "physical_tile_elements": str(arm.physical_elements),
        "num_initial_row_table_slices": "32",
        "num_offset_table_entries": str(arm.logical_elements),
        "num_offset_table_epoch_entries": str(arm.logical_elements),
        "virtual_index_buffer_lines": str(arm.feeder_lines),
        "virtual_masked_writes": "true",
        "virtual_strict_two_phase": "true" if arm.strict else "false",
        "virtual_index_partitions": "1",
        "virtual_index_range_passes": "false",
        "virtual_index_descriptor_spool": "false",
        "virtual_descriptor_spool_read_ahead": "false",
        "virtual_bounded_global_merge": "false",
        "virtual_idealized_write_ack": "false",
        "virtual_native_issue_order": "false",
        "virtual_combine_slots": str(combine_slots),
        "virtual_combine_words": str(combine_words),
        "virtual_combine_ways": "0",
        "virtual_response_slots": "8",
        "virtual_response_word_pool": "0",
        "virtual_words_per_cycle": "1",
        "virtual_max_outstanding_writes": "32",
        "no_reorder": "false",
        "reconfigure_row_table": "false",
    }
    for key, expected in expected_config.items():
        require(
            config.get(key) == expected, f"{arm.name}: config {key} mismatch"
        )

    stats = first_stats_section(arm_root / "run/stats.txt")
    counters = {
        "simTicks": exact_stat(stats, "simTicks"),
        "simInsts": exact_stat(stats, "simInsts"),
        "indirect_ops": exact_stat(stats, "system.maa.numInst_INDRD"),
        "stream_writes": exact_stat(stats, "system.maa.numInst_STRWR"),
        "scalar_ops": exact_stat(stats, "system.maa.numInst_ALUS"),
        "index_words": summed_stat(stats, "IND_VirtIndexWords"),
        "index_hwm": summed_stat(stats, "IND_VirtIndexWordHighWater"),
        "write_issues": summed_stat(stats, "IND_VirtWriteIssues"),
        "write_completions": summed_stat(stats, "IND_VirtWriteCompletions"),
        "full_writes": summed_stat(stats, "IND_VirtFullLineWrites"),
        "partial_writes": summed_stat(stats, "IND_VirtPartialWrites"),
        "strict_operations": summed_stat(
            stats, "IND_StrictTwoPhaseOperations"
        ),
        "strict_b_fetch_lines": summed_stat(
            stats, "IND_StrictTwoPhaseBFetchLines"
        ),
        "strict_descriptors": summed_stat(
            stats, "IND_StrictTwoPhaseDescriptors"
        ),
        "strict_a_issues": summed_stat(stats, "IND_StrictTwoPhaseAIssues"),
        "strict_backing_issues": summed_stat(
            stats, "IND_StrictTwoPhaseBackingIssues"
        ),
        "strict_pages_ready": summed_stat(
            stats, "IND_StrictTwoPhasePagesReady"
        ),
        "strict_b_words": 0,
        "strict_backing_semantic_bytes": 0,
        "strict_backing_transport_bytes": 0,
        "offset_epoch_drains": summed_stat(stats, "IND_NumOTEpochDrain"),
    }
    require(counters["simInsts"] > 0, f"{arm.name}: empty guest work")
    require(
        counters["indirect_ops"] == arm.expected_indirect_ops,
        f"{arm.name}: indirect op count",
    )
    require(
        counters["stream_writes"] == arm.expected_stream_writes,
        f"{arm.name}: stream write count",
    )
    require(
        counters["scalar_ops"] == arm.expected_scalar_ops,
        f"{arm.name}: scalar op count",
    )
    require(
        counters["index_words"] == TOTAL_ELEMENTS,
        f"{arm.name}: semantic index work",
    )
    require(counters["index_hwm"] > 0, f"{arm.name}: feeder never became live")

    strict_trace: dict[str, str] | None = None
    admission: dict[str, str] | None = None
    trace_path = arm_root / "run/hybrid_trace.log"
    trace_lines = trace_path.read_text(
        encoding="utf-8", errors="strict"
    ).splitlines()
    if arm.strict:
        validate_masked_retirement(
            counters, arm.name, require_partial_retirement
        )
        strict_trace = exactly_one_event(
            trace_lines, "strict_two_phase_timing"
        )
        admission = exactly_one_event(
            trace_lines, "strict_two_phase_admission_closed"
        )
        expected_trace = {
            "schema": "2",
            "logical": "16384",
            "physical": "4096",
            "feeder_words": str(arm.feeder_lines * WORDS_PER_INDEX_LINE),
            "result_words": str(strict_result_words),
            "b_words": "16384",
            "descriptors": "16384",
            "pages_ready": "4",
            "exact_b_once": "1",
            "raw_b_retained_bytes": "0",
            "descriptor_backing_bytes": "0",
            "replay_passes": "0",
            "coherent_ack": "1",
            "order_ok": "1",
            "terminal": "1",
        }
        for key, expected in expected_trace.items():
            require(
                strict_trace.get(key) == expected,
                f"{arm.name}: strict trace {key}",
            )
        validate_strict_fetch_lines(
            counters["strict_b_fetch_lines"], strict_trace, arm.name
        )
        require(
            strict_trace.get("a_responses") == strict_trace.get("a_issues"),
            f"{arm.name}: strict A issue/response closure",
        )
        require(
            int(strict_trace.get("backing_acks", "0"))
            == counters["write_completions"],
            f"{arm.name}: strict backing ACK/write completion closure",
        )
        require(
            int(strict_trace.get("backing_transport_bytes", "0"))
            == counters["write_issues"] * 64,
            f"{arm.name}: strict backing transport bytes",
        )
        require(
            strict_trace.get("backing_semantic_bytes") == "131072",
            f"{arm.name}: strict backing semantic bytes",
        )
        counters["strict_b_words"] = int(strict_trace["b_words"])
        counters["strict_backing_semantic_bytes"] = int(
            strict_trace["backing_semantic_bytes"]
        )
        counters["strict_backing_transport_bytes"] = int(
            strict_trace["backing_transport_bytes"]
        )
        expected_admission = {
            "schema": "2",
            "b_words": "16384",
            "descriptors": "16384",
            "offsets": "16384",
            "raw_b_buffered_words": "0",
            "a_issues": "0",
        }
        for key, expected in expected_admission.items():
            require(
                admission.get(key) == expected, f"{arm.name}: admission {key}"
            )
        require(
            counters["strict_operations"] == 1, f"{arm.name}: strict op count"
        )
        require(
            counters["strict_descriptors"] == TOTAL_ELEMENTS,
            f"{arm.name}: descriptor count",
        )
        require(counters["strict_a_issues"] > 0, f"{arm.name}: no A issues")
        require(
            counters["strict_backing_issues"] == counters["write_issues"],
            f"{arm.name}: strict backing/write identity",
        )
        require(
            counters["strict_pages_ready"] == 4,
            f"{arm.name}: strict page count",
        )
        require(
            counters["offset_epoch_drains"] == 0, f"{arm.name}: strict drain"
        )
    else:
        for key in (
            "write_issues",
            "write_completions",
            "full_writes",
            "partial_writes",
            "strict_operations",
            "strict_b_fetch_lines",
            "strict_descriptors",
            "strict_a_issues",
            "strict_backing_issues",
            "strict_pages_ready",
            "strict_b_words",
            "strict_backing_semantic_bytes",
            "strict_backing_transport_bytes",
            "offset_epoch_drains",
        ):
            require(counters[key] == 0, f"{arm.name}: unexpected {key}")

    return {
        "name": arm.name,
        "classification": "ACCEPT",
        "reason": (
            "terminal, exact output, exact semantic work, and mechanism "
            "gates pass"
        ),
        "spec": asdict(arm),
        "output_hash": result_match["hash"],
        "counters": counters,
        "strict_trace": strict_trace,
        "strict_admission": admission,
        "command_sha256": sha256_file(arm_root / "command.json"),
        "config_sha256": sha256_file(arm_root / "run/config.ini"),
        "restore_log_sha256": sha256_file(arm_root / "restore.log"),
        "stats_sha256": sha256_file(arm_root / "run/stats.txt"),
        "trace_sha256": sha256_file(trace_path),
    }


def classify_matrix(root: Path) -> dict[str, object]:
    manifest = json.loads((root / "manifest.json").read_text())
    require(
        manifest["schema"] == "dx100.hybrid_equal_work_micro.v1",
        "manifest schema",
    )
    require(
        manifest["gem5_sha256"] == EXPECTED_GEM5_SHA256, "manifest gem5 hash"
    )
    require(
        manifest["ramulator_sha256"] == EXPECTED_RAMULATOR_SHA256,
        "manifest Ramulator hash",
    )
    workload_hash = manifest["workload_sha256"]
    require(
        sha256_file(root / "input/workload") == workload_hash,
        "workload hash changed",
    )
    require(
        sha256_file(root / "input/gem5.opt") == EXPECTED_GEM5_SHA256,
        "frozen gem5 changed",
    )
    require(
        sha256_file(root / "input/libramulator.so")
        == EXPECTED_RAMULATOR_SHA256,
        "frozen Ramulator changed",
    )
    require((root / "checkpoint.exit").read_text() == "0\n", "checkpoint rc")
    validate_process_record(root / "checkpoint.process.json")
    checkpoint_lines = (
        (root / "checkpoint.log")
        .read_text(encoding="utf-8", errors="strict")
        .splitlines()
    )
    require(
        sum(
            bool(
                re.fullmatch(r"Exiting @ tick [0-9]+ because checkpoint", line)
            )
            for line in checkpoint_lines
        )
        == 1,
        "checkpoint terminal marker",
    )
    checkpoint_identity = sha256_file(root / "checkpoint.files.sha256")
    require(
        (root / "checkpoint.identity.sha256").read_text().split()[0]
        == checkpoint_identity,
        "checkpoint identity changed",
    )
    expected_checkpoint_files: set[str] = set()
    for line in (root / "checkpoint.files.sha256").read_text().splitlines():
        digest, relative = line.split("  ", 1)
        require(
            relative not in expected_checkpoint_files,
            "duplicate checkpoint file",
        )
        expected_checkpoint_files.add(relative)
        require(
            sha256_file(root / "checkpoint" / relative) == digest,
            f"checkpoint file changed: {relative}",
        )
    actual_checkpoint_files = {
        str(path.relative_to(root / "checkpoint"))
        for path in (root / "checkpoint").rglob("*")
        if path.is_file()
    }
    require(
        actual_checkpoint_files == expected_checkpoint_files,
        "checkpoint file set changed",
    )

    arms: dict[str, dict[str, object]] = {}
    for arm in ARMS:
        arm_manifest = json.loads(
            (root / "arms" / arm.name / "arm.json").read_text()
        )
        require(
            arm_manifest["workload_sha256"] == workload_hash,
            f"{arm.name}: workload hash",
        )
        require(
            arm_manifest["checkpoint_identity"] == checkpoint_identity,
            f"{arm.name}: checkpoint identity",
        )
        command = json.loads(
            (root / "arms" / arm.name / "command.json").read_text()
        )
        require(
            command[0] == str(root / "input/gem5.opt"),
            f"{arm.name}: frozen gem5 command",
        )
        require(
            str(root / "input/workload") in command,
            f"{arm.name}: frozen workload command",
        )
        arms[arm.name] = classify_arm(root, arm)

    normalized = {
        arm.name: normalized_command(
            json.loads((root / "arms" / arm.name / "command.json").read_text())
        )
        for arm in ARMS
    }
    require(
        len({json.dumps(value) for value in normalized.values()}) == 1,
        "non-treatment command mismatch",
    )
    require(
        len({item["output_hash"] for item in arms.values()}) == 1,
        "output hashes differ",
    )
    for field in HYBRID_SEMANTIC_WORK_FIELDS:
        require(
            arms["hybrid1"]["counters"][field]
            == arms["hybrid64"]["counters"][field],
            f"hybrid conserved work differs: {field}",
        )
    ticks = {
        name: int(result["counters"]["simTicks"])
        for name, result in arms.items()
    }
    comparisons = {
        "native16_over_native4": ticks["native16"] / ticks["native4"],
        "native16_over_hybrid1": ticks["native16"] / ticks["hybrid1"],
        "native16_over_hybrid64": ticks["native16"] / ticks["hybrid64"],
        "native4_over_hybrid1": ticks["native4"] / ticks["hybrid1"],
        "native4_over_hybrid64": ticks["native4"] / ticks["hybrid64"],
        "hybrid1_over_hybrid64": ticks["hybrid1"] / ticks["hybrid64"],
    }
    result = {
        "schema": "dx100.hybrid_equal_work_micro.result.v1",
        "terminal": True,
        "decision": "ACCEPT_ALL_FOUR_ARMS",
        "repetitions_per_arm": 1,
        "performance_metric": "simTicks",
        "same_binary": True,
        "same_checkpoint_input": True,
        "workload_sha256": workload_hash,
        "gem5_sha256": EXPECTED_GEM5_SHA256,
        "checkpoint_identity": checkpoint_identity,
        "arms": arms,
        "comparisons": comparisons,
        "limitations": [
            "one deterministic gem5 observation per arm",
            "microbenchmark evidence only; no full application was launched",
            "speed comparisons apply only to the exact frozen binary/config",
            "native4 is four exact 4K operations in the shared T16K "
            "logical aperture, not a true T4096/API-aperture run",
        ],
    }
    if root.joinpath("failure.json").is_file():
        result["prior_classifier_failure"] = json.loads(
            root.joinpath("failure.json").read_text()
        )
    return result


def write_matrix_table(root: Path, result: Mapping[str, object]) -> None:
    fields = (
        "simTicks",
        "simInsts",
        "indirect_ops",
        "stream_writes",
        "scalar_ops",
        "index_words",
        "index_hwm",
        "write_issues",
        "write_completions",
        "full_writes",
        "partial_writes",
        "strict_operations",
        "strict_b_fetch_lines",
        "strict_descriptors",
        "strict_a_issues",
        "strict_backing_issues",
        "strict_pages_ready",
        "strict_b_words",
        "strict_backing_semantic_bytes",
        "strict_backing_transport_bytes",
        "offset_epoch_drains",
    )
    with (root / "matrix.tsv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(("arm", "classification", "output_hash", *fields))
        for arm in ARMS:
            item = result["arms"][arm.name]
            counters = item["counters"]
            writer.writerow(
                (
                    arm.name,
                    item["classification"],
                    item["output_hash"],
                    *(counters[field] for field in fields),
                )
            )


def artifact_paths(root: Path) -> list[Path]:
    paths = [
        root / "manifest.json",
        root / "launch_manifest.json",
        root / "checkpoint.log",
        root / "checkpoint.exit",
        root / "checkpoint.command.json",
        root / "checkpoint.process.json",
        root / "checkpoint.files.sha256",
        root / "checkpoint.identity.sha256",
        root / "result.json",
        root / "matrix.tsv",
        root / "gate.complete",
        root / "input/gem5.opt",
        root / "input/workload",
        root / "input/libramulator.so",
        root / "input/ramulator.yaml",
        root / "input/gem5.ldd.txt",
    ]
    for arm in ARMS:
        base = root / "arms" / arm.name
        paths.extend(
            base / name
            for name in (
                "arm.json",
                "command.json",
                "restore.log",
                "restore.exit",
                "process.json",
                "treatment.txt",
                "run/config.ini",
                "run/stats.txt",
                "run/hybrid_trace.log",
            )
        )
    for optional in (root / "matrix.failed", root / "failure.json"):
        if optional.is_file():
            paths.append(optional)
    return paths


def write_ledger(root: Path) -> None:
    lines = []
    for path in artifact_paths(root):
        require(path.is_file(), f"missing ledger artifact: {path}")
        lines.append(f"{sha256_file(path)}  {path.relative_to(root)}")
    (root / "artifacts.sha256").write_text(
        "\n".join(sorted(lines)) + "\n", encoding="utf-8"
    )


def validate_ledger(root: Path) -> None:
    ledger = root / "artifacts.sha256"
    require(ledger.is_file(), "missing artifacts.sha256")
    seen: set[str] = set()
    for line in ledger.read_text().splitlines():
        digest, relative = line.split("  ", 1)
        require(
            re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
            "bad ledger digest",
        )
        require(relative not in seen, f"duplicate ledger path: {relative}")
        seen.add(relative)
        require(
            sha256_file(root / relative) == digest,
            f"ledger mismatch: {relative}",
        )
    expected = {str(path.relative_to(root)) for path in artifact_paths(root)}
    require(seen == expected, "ledger path set mismatch")


def write_sealed_result(root: Path, result: Mapping[str, object]) -> None:
    (root / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    write_matrix_table(root, result)
    prior_failure = (root / "failure.json").is_file()
    (root / "gate.complete").write_text(
        "ACCEPT_ALL_FOUR_ARMS\n"
        "same_binary=true\n"
        "same_checkpoint_input=true\n"
        "performance_metric=simTicks\n"
        "full_application_runs=0\n"
        f"prior_classifier_failure={str(prior_failure).lower()}\n"
    )
    write_ledger(root)


def execute(
    root: Path, gem5_source: Path, ramulator_source: Path
) -> dict[str, object]:
    require(not root.exists(), f"refusing to overwrite evidence root: {root}")
    authority = preflight(gem5_source.resolve(), ramulator_source.resolve())
    root.mkdir(parents=True)
    (root / "input").mkdir()
    (root / "arms").mkdir()
    try:
        copy_reflink(gem5_source, root / "input/gem5.opt")
        copy_reflink(ramulator_source, root / "input/libramulator.so")
        gem5 = root / "input/gem5.opt"
        ramulator = root / "input/libramulator.so"
        os.chmod(gem5, 0o555)
        build = root / "workload-build"
        subprocess.run(
            [
                str(
                    ROOT / "experiments/scripts/build_virtual_tile_consumer.sh"
                ),
                str(build),
            ],
            check=True,
        )
        workload = root / "input/workload"
        copy_reflink(build / "test_virtual_tile_consumer_T16384", workload)
        os.chmod(workload, 0o555)
        ramulator_yaml_source = (
            ROOT / "ext/ramulator2/ramulator2/example_gem5_config.yaml"
        )
        ramulator_yaml = root / "input/ramulator.yaml"
        copy_reflink(ramulator_yaml_source, ramulator_yaml)
        environment = os.environ.copy()
        library = str((root / "input").resolve())
        prior = environment.get("LD_LIBRARY_PATH")
        environment["LD_LIBRARY_PATH"] = (
            f"{library}:{prior}" if prior else library
        )
        environment["OMP_PROC_BIND"] = "false"
        environment["OMP_NUM_THREADS"] = "4"
        ldd = subprocess.check_output(
            ["ldd", str(gem5)], env=environment, text=True
        )
        (root / "input/gem5.ldd.txt").write_text(ldd)
        selector = (root / "treatment.txt").resolve()
        selector.write_text(ARMS[0].treatment)
        checkpoint = root / "checkpoint"
        checkpoint_command = [
            str(gem5),
            "--listener-mode=off",
            f"--outdir={checkpoint}",
            str(ROOT / "configs/deprecated/example/se.py"),
            "--cpu-type",
            "AtomicSimpleCPU",
            "-n",
            "4",
            "--mem-size",
            "2GB",
            "--max-checkpoints=1",
            "--cmd",
            str(workload),
            "--options",
            f"deferred {selector}",
        ]
        (root / "checkpoint.command.json").write_text(
            json.dumps(checkpoint_command, indent=2) + "\n"
        )
        checkpoint_rc = run_command(
            checkpoint_command,
            root / "checkpoint.log",
            environment,
            root / "checkpoint.process.json",
        )
        (root / "checkpoint.exit").write_text(f"{checkpoint_rc}\n")
        require(
            checkpoint_rc == 0, f"checkpoint failed with rc={checkpoint_rc}"
        )
        checkpoint_lines = (root / "checkpoint.log").read_text().splitlines()
        require(
            checkpoint_lines.count(
                "VIRTUAL_TILE_CONSUMER_LAYOUT mode=deferred page_elements=0 "
                "logical_elements=16384 mem_size=2147483648"
            )
            == 1,
            "shared checkpoint layout mismatch",
        )
        checkpoint_files = []
        for path in sorted(
            item for item in checkpoint.rglob("*") if item.is_file()
        ):
            checkpoint_files.append(
                f"{sha256_file(path)}  {path.relative_to(checkpoint)}"
            )
        (root / "checkpoint.files.sha256").write_text(
            "\n".join(checkpoint_files) + "\n"
        )
        checkpoint_identity = sha256_file(root / "checkpoint.files.sha256")
        (root / "checkpoint.identity.sha256").write_text(
            f"{checkpoint_identity}  checkpoint.files.sha256\n"
        )

        launch_manifest = {
            "schema": "dx100.hybrid_equal_work_micro.v1",
            **authority,
            "gem5_source_path": str(gem5_source.resolve()),
            "ramulator_source_path": str(ramulator_source.resolve()),
            "workload_source": str(
                ROOT / "benchmarks/API/test_virtual_tile_consumer.cpp"
            ),
            "workload_sha256": sha256_file(workload),
            "workload_source_sha256": sha256_file(
                ROOT / "benchmarks/API/test_virtual_tile_consumer.cpp"
            ),
            "workload_build_script_sha256": sha256_file(
                ROOT / "experiments/scripts/build_virtual_tile_consumer.sh"
            ),
            "ramulator_yaml_sha256": sha256_file(ramulator_yaml),
            "checkpoint_identity": checkpoint_identity,
            "arms": [asdict(arm) for arm in ARMS],
            "legacy_mismatch": (
                "run_virtual_tile_attribution_matrix.sh selects a T4096 "
                "binary only for native_fused_4k and T16384 otherwise"
            ),
            "native4_same_binary_caveat": (
                "A T16384 guest restored with num_tile_elements=4096 faults "
                "because its compile-time MAA aperture exceeds the logical4K "
                "mapping. The accepted same-binary native4 arm therefore "
                "executes four exact 4096-element operations with "
                "logical16/physical4 geometry; it is not a true T4096/API "
                "aperture result."
            ),
        }
        (root / "launch_manifest.json").write_text(
            json.dumps(launch_manifest, indent=2, sort_keys=True) + "\n"
        )

        commands: dict[str, list[str]] = {}
        for arm in ARMS:
            arm_root = root / "arms" / arm.name
            arm_root.mkdir()
            selector_tmp = root / f"treatment.{arm.name}.tmp"
            selector_tmp.write_text(arm.treatment)
            selector_tmp.replace(selector)
            (arm_root / "treatment.txt").write_text(arm.treatment)
            command = common_restore_command(
                gem5,
                workload,
                ramulator_yaml,
                checkpoint,
                arm_root,
                selector,
                arm,
            )
            commands[arm.name] = command
            (arm_root / "command.json").write_text(
                json.dumps(command, indent=2) + "\n"
            )
            arm_manifest = {
                "name": arm.name,
                "spec": asdict(arm),
                "workload_sha256": sha256_file(workload),
                "checkpoint_identity": checkpoint_identity,
                "treatment_sha256": sha256_file(arm_root / "treatment.txt"),
            }
            (arm_root / "arm.json").write_text(
                json.dumps(arm_manifest, indent=2, sort_keys=True) + "\n"
            )
            restore_rc = run_command(
                command,
                arm_root / "restore.log",
                environment,
                arm_root / "process.json",
            )
            (arm_root / "restore.exit").write_text(f"{restore_rc}\n")
            require(restore_rc == 0, f"{arm.name} failed with rc={restore_rc}")

        require(
            len(
                {
                    json.dumps(normalized_command(command))
                    for command in commands.values()
                }
            )
            == 1,
            "generated commands differ outside declared treatments",
        )
        manifest = launch_manifest
        (root / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        result = classify_matrix(root)
        write_sealed_result(root, result)
        return result
    except BaseException as error:
        (root / "matrix.failed").write_text("failed\n")
        (root / "failure.json").write_text(
            json.dumps(
                {"error_type": type(error).__name__, "message": str(error)},
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        raise


def validate(root: Path) -> dict[str, object]:
    validate_ledger(root)
    recomputed = classify_matrix(root)
    sealed = json.loads((root / "result.json").read_text())
    require(
        recomputed == sealed,
        "sealed result differs from independent classification",
    )
    return recomputed


def seal(root: Path) -> dict[str, object]:
    for path in (
        root / "result.json",
        root / "matrix.tsv",
        root / "gate.complete",
        root / "artifacts.sha256",
    ):
        require(
            not path.exists(), f"refusing to overwrite sealed artifact: {path}"
        )
    result = classify_matrix(root)
    write_sealed_result(root, result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    run_parser = subparsers.add_parser(
        "run", help="launch and classify the matrix"
    )
    run_parser.add_argument("out", type=Path)
    run_parser.add_argument("--gem5", type=Path, default=DEFAULT_GEM5)
    run_parser.add_argument(
        "--ramulator", type=Path, default=DEFAULT_RAMULATOR
    )
    validate_parser = subparsers.add_parser(
        "validate",
        help="read-only independent validation of an existing matrix",
    )
    validate_parser.add_argument("out", type=Path)
    seal_parser = subparsers.add_parser(
        "seal",
        help="classify and seal a completed raw matrix without rerunning gem5",
    )
    seal_parser.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "run":
            result = execute(args.out.resolve(), args.gem5, args.ramulator)
        elif args.action == "seal":
            result = seal(args.out.resolve())
        else:
            result = validate(args.out.resolve())
    except (
        MatrixError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
