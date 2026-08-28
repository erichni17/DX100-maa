#!/usr/bin/env python3
"""Fail-closed three-arm backing read-for-ownership bracket micro.

The guest is deliberately dedicated to this diagnosis.  It checkpoints before
the arm selector is consumed, then runs one fixed strict logical16/physical4K
hybrid in three cache-state arms: cold, pre-ROI/reset (an idealized cache-state
bound), and preallocation charged inside ROI.  Raw evidence is outside Git.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LEAD_GEM5 = Path(
    "/data1/nier/worktrees/DX100-virtualization-selected-integration-cont-20260826/"
    "build/X86/gem5.opt"
)
EXPECTED_GEM5_SHA256 = (
    "182a6696a60983aa690fa6b4131592cff4408b380891fa31098f1f978cdada0d"
)
RAMULATOR = Path(
    "/data1/nier/dx100-runs/2026-08-12-hybrid-line-handoff-8a5c7712/input/"
    "libramulator.so"
)
RAMULATOR_CONFIG = ROOT / "ext/ramulator2/ramulator2/example_gem5_config.yaml"
CONFIG = ROOT / "configs/deprecated/example/se.py"
SOURCE = ROOT / "benchmarks/API/test_virtual_tile_backing_rfo.cpp"
ARMS = ("cold", "ideal", "charged")
FATAL = re.compile(r"panic|fatal|assert|abort|segmentation fault|error:", re.I)
EXIT_RE = re.compile(
    r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$"
)
CHECKPOINT_RE = re.compile(r"^Exiting @ tick [0-9]+ because checkpoint$")
RESULT_RE = re.compile(
    r"^BACKING_RFO_RESULT arm=(cold|ideal|charged) hash=([0-9]+) errors=0$"
)


class BracketError(RuntimeError):
    """A missing or mismatched invariant rejects the entire bracket."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise BracketError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def proc_start_ticks(pid: int) -> int:
    # proc stat's final field is starttime; comm may contain spaces in theory.
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
    return int(fields[19])


def run_logged(
    command: list[str], log: Path, env: dict[str, str]
) -> dict[str, Any]:
    started_ns = time.time_ns()
    with log.open("w", encoding="utf-8") as handle:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        pid = process.pid
        start_ticks = proc_start_ticks(pid)
        returncode = process.wait()
    return {
        "command": command,
        "pid": pid,
        "proc_start_ticks": start_ticks,
        "returncode": returncode,
        "pid_identity_absent": not Path(f"/proc/{pid}").exists(),
        "wall_seconds": (time.time_ns() - started_ns) / 1_000_000_000,
    }


def compile_guest(binary: Path) -> list[str]:
    command = [
        os.environ.get("CXX", "g++"),
        f"-I{ROOT / 'benchmarks/API'}",
        f"-I{ROOT / 'include'}",
        f"-I{ROOT / 'util/m5/src'}",
        "-std=c++17",
        "-O3",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wno-ignored-qualifiers",
        "-Wno-unused-parameter",
        "-Wno-unused-function",
        "-DGEM5",
        "-DMAA",
        "-DMAA_VIRTUAL_GATHER",
        "-DMAA_GENERAL_VIRTUAL_CONSUMER",
        "-DNUM_CORES=4",
        "-DNUM_TILES_PER_CORE=8",
        "-DTILE_SIZE=16384",
        "-DMAA_MEM_SIZE=0x80000000",
        str(ROOT / "util/m5/src/abi/x86/m5op.S"),
        str(SOURCE),
        "-o",
        str(binary),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    return command


def fixed_args(
    gem5: Path, binary: Path, checkpoint: Path, arm_dir: Path, selector: Path
) -> list[str]:
    return [
        str(gem5),
        "--listener-mode=off",
        f"--outdir={arm_dir / 'run'}",
        "--debug-flags=MAAVirtualTrace",
        "--debug-file=hybrid_trace.log",
        str(CONFIG),
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
        str(RAMULATOR_CONFIG),
        "--mem-channels=1",
        "--maa",
        "--maa_num_maas=1",
        "--maa_num_indirect_units_per_maa=1",
        "--maa_num_tiles_per_core=8",
        "--maa_num_tile_elements=16384",
        "--maa_physical_tile_elements=4096",
        "--maa_num_initial_row_table_slices=16",
        "--maa_num_row_table_rows_per_slice=64",
        "--maa_num_row_table_entries_per_subslice_row=16",
        "--maa_num_offset_table_entries=16384",
        "--maa_num_offset_table_epoch_entries=16384",
        "--maa_virtual_combine_slots=16",
        "--maa_virtual_combine_words=0",
        "--maa_virtual_combine_ways=0",
        "--maa_virtual_combine_banks=0",
        "--maa_virtual_response_slots=8",
        "--maa_virtual_response_word_pool=0",
        "--maa_virtual_words_per_cycle=1",
        "--maa_virtual_max_outstanding_writes=32",
        "--maa_virtual_masked_writes",
        "--maa_virtual_index_buffer_lines=64",
        "--maa_virtual_strict_two_phase",
        "--cmd",
        str(binary),
        "--options",
        str(selector),
    ]


def checkpoint_args(
    gem5: Path, binary: Path, checkpoint: Path, selector: Path
) -> list[str]:
    return [
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
        str(binary),
        "--options",
        str(selector),
    ]


def stats_sections(path: Path) -> list[dict[str, float]]:
    sections: list[dict[str, float]] = []
    active: dict[str, float] | None = None
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        if line == "---------- Begin Simulation Statistics ----------":
            require(active is None, f"nested stats window: {path}")
            active = {}
        elif line == "---------- End Simulation Statistics   ----------":
            require(active is not None, f"orphan stats end: {path}")
            sections.append(active)
            active = None
        elif active is not None:
            fields = line.split()
            if len(fields) >= 2:
                try:
                    active[fields[0]] = float(fields[1])
                except ValueError:
                    pass
    require(active is None and sections, f"missing terminal stats: {path}")
    return sections


def terminal_stats(path: Path) -> dict[str, float]:
    eligible = [
        item
        for item in stats_sections(path)
        if item.get("simTicks", 0) > 0
        and item.get("system.maa.numInst_INDRD", 0) > 0
    ]
    require(
        len(eligible) == 1, f"expected one populated ROI stats window: {path}"
    )
    return eligible[0]


def exact(stats: dict[str, float], key: str) -> int:
    value = stats.get(key)
    require(
        value is not None and value.is_integer(), f"missing integer stat {key}"
    )
    return int(value)


def sum_suffix(stats: dict[str, float], suffix: str) -> int:
    values = [value for name, value in stats.items() if name.endswith(suffix)]
    require(
        values and all(value.is_integer() for value in values),
        f"missing integer suffix {suffix}",
    )
    return int(sum(values))


def l3_region(stats: dict[str, float], field: str) -> int:
    # Region 10 is the MAA backing write requestor in this fixed configuration.
    return exact(stats, f"system.l3.{field}_10::maa")


def ramulator_reads(stats: dict[str, float]) -> int:
    candidates = [
        "system.mem_ctrl.dram.readReqs",
        "system.mem_ctrl.readReqs",
        "system.mem_ctrl0.dram.readReqs",
        "system.mem_ctrl0.readReqs",
    ]
    for key in candidates:
        if key in stats:
            return exact(stats, key)
    names = [
        name for name in stats if name.endswith("total_num_read_requests")
    ]
    require(len(names) == 1, "missing unambiguous Ramulator read-request stat")
    return exact(stats, names[0])


def parse_event(lines: list[str], event: str) -> dict[str, str]:
    marker = f"event={event} "
    matches = []
    for line in lines:
        if marker in line:
            matches.append(
                {
                    token.split("=", 1)[0]: token.split("=", 1)[1]
                    for token in line[line.index(marker) :].split()
                    if "=" in token
                }
            )
    require(
        len(matches) == 1,
        f"expected exactly one {event}, found {len(matches)}",
    )
    return matches[0]


def validate_config(path: Path) -> None:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    with path.open(encoding="utf-8") as handle:
        parser.read_file(handle)
    require(parser.has_section("system.maa"), f"missing MAA config: {path}")
    values = dict(parser.items("system.maa"))
    expected = {
        "num_maas": "1",
        "num_indirect_units_per_maa": "1",
        "num_tiles_per_core": "8",
        "num_tile_elements": "16384",
        "physical_tile_elements": "4096",
        "num_initial_row_table_slices": "16",
        "num_row_table_entries_per_subslice_row": "16",
        "virtual_strict_two_phase": "true",
        "virtual_index_buffer_lines": "64",
        "virtual_combine_slots": "16",
        "virtual_combine_words": "0",
        "virtual_combine_ways": "0",
        "virtual_response_slots": "8",
        "virtual_response_word_pool": "0",
        "virtual_words_per_cycle": "1",
        "virtual_masked_writes": "true",
        "virtual_index_partitions": "1",
        "virtual_index_descriptor_spool": "false",
        "virtual_idealized_write_ack": "false",
    }
    for key, value in expected.items():
        require(
            values.get(key) == value,
            f"config mismatch {key}: {values.get(key)}",
        )


def validate_arm(
    root: Path, arm: str, checkpoint_digest: str
) -> dict[str, Any]:
    arm_dir = root / "arms" / arm
    process = json.loads((arm_dir / "process.json").read_text())
    require(
        process["returncode"] == 0 and process["pid_identity_absent"],
        f"{arm}: nonterminal process",
    )
    log = (arm_dir / "restore.log").read_text(
        encoding="utf-8", errors="strict"
    )
    lines = log.splitlines()
    require(
        sum(bool(EXIT_RE.fullmatch(line)) for line in lines) == 1,
        f"{arm}: m5 exit",
    )
    require(
        lines.count("ROI Ended") == 1 and not FATAL.search(log),
        f"{arm}: terminal log",
    )
    require(lines.count("BACKING_RFO_ARM arm=" + arm) == 1, f"{arm}: selector")
    result = [RESULT_RE.fullmatch(line) for line in lines]
    result = [match for match in result if match is not None]
    require(
        len(result) == 1 and result[0].group(1) == arm, f"{arm}: exact output"
    )
    prealloc = [
        line for line in lines if line.startswith("BACKING_RFO_PREALLOC ")
    ]
    require(len(prealloc) == 1, f"{arm}: preallocation marker")
    expected_lines = 0 if arm == "cold" else 2048
    require(
        f"lines={expected_lines}" in prealloc[0],
        f"{arm}: preallocation line count",
    )
    validate_config(arm_dir / "run/config.ini")
    stats = terminal_stats(arm_dir / "run/stats.txt")
    trace_lines = (
        (arm_dir / "run/hybrid_trace.log")
        .read_text(encoding="utf-8", errors="strict")
        .splitlines()
    )
    timing = parse_event(trace_lines, "strict_two_phase_timing")
    admission = parse_event(trace_lines, "strict_two_phase_admission_closed")
    for key, value in {
        "schema": "2",
        "logical": "16384",
        "physical": "4096",
        "feeder_words": "1024",
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
    }.items():
        require(timing.get(key) == value, f"{arm}: strict timing {key}")
    require(
        timing.get("a_issues") == timing.get("a_responses"),
        f"{arm}: A closure",
    )
    require(
        timing.get("backing_issues") == timing.get("backing_acks"),
        f"{arm}: backing ACK closure",
    )
    for key, value in {
        "b_words": "16384",
        "descriptors": "16384",
        "offsets": "16384",
        "raw_b_buffered_words": "0",
        "a_issues": "0",
    }.items():
        require(admission.get(key) == value, f"{arm}: admission {key}")
    counters = {
        "simTicks": exact(stats, "simTicks"),
        "backing_transactions": sum_suffix(
            stats, "IND_StrictTwoPhaseBackingIssues"
        ),
        "backing_acks": sum_suffix(stats, "IND_VirtWriteCompletions"),
        "strict_b_fetch_lines": sum_suffix(
            stats, "IND_StrictTwoPhaseBFetchLines"
        ),
        "strict_descriptors": sum_suffix(
            stats, "IND_StrictTwoPhaseDescriptors"
        ),
        "strict_a_issues": sum_suffix(stats, "IND_StrictTwoPhaseAIssues"),
        "strict_pages_ready": sum_suffix(
            stats, "IND_StrictTwoPhasePagesReady"
        ),
        "l3_maa_region_hits": l3_region(stats, "demandHits"),
        "l3_maa_region_misses": l3_region(stats, "demandMisses"),
        "l3_maa_region_miss_latency": l3_region(stats, "demandMissLatency"),
        "maa_cache_rd_packets": exact(
            stats, "system.maa.port_cache_RD_packets"
        ),
        "maa_cache_wr_packets": exact(
            stats, "system.maa.port_cache_WR_packets"
        ),
        "ramulator_read_requests": ramulator_reads(stats),
    }
    require(
        counters["backing_transactions"] == counters["backing_acks"],
        f"{arm}: stats backing closure",
    )
    require(
        counters["strict_b_fetch_lines"] in (1024, 1025), f"{arm}: B lines"
    )
    require(
        counters["strict_descriptors"] == 16384
        and counters["strict_a_issues"] > 0
        and counters["strict_pages_ready"] == 4,
        f"{arm}: strict work",
    )
    require(counters["simTicks"] > 0, f"{arm}: no ROI ticks")
    checkpoint_ledger = (
        (arm_dir / "checkpoint.identity.sha256").read_text().split()[0]
    )
    require(
        checkpoint_ledger == checkpoint_digest, f"{arm}: checkpoint changed"
    )
    return {
        "arm": arm,
        "output_hash": result[0].group(2),
        "counters": counters,
        "command_sha256": sha256(arm_dir / "command.json"),
        "config_sha256": sha256(arm_dir / "run/config.ini"),
        "restore_log_sha256": sha256(arm_dir / "restore.log"),
        "stats_sha256": sha256(arm_dir / "run/stats.txt"),
        "trace_sha256": sha256(arm_dir / "run/hybrid_trace.log"),
    }


def checkpoint_identity(checkpoint: Path, ledger: Path) -> str:
    entries = []
    for path in sorted(
        item for item in checkpoint.rglob("*") if item.is_file()
    ):
        entries.append(f"{sha256(path)}  {path.relative_to(checkpoint)}")
    ledger.write_text("\n".join(entries) + "\n")
    return sha256(ledger)


def write_artifact_ledger(root: Path) -> None:
    paths = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "artifact_sha256.txt"
    ]
    (root / "artifact_sha256.txt").write_text(
        "\n".join(
            f"{sha256(path)}  {path.relative_to(root)}" for path in paths
        )
        + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "out", type=Path, help="empty raw-evidence directory outside Git"
    )
    parser.add_argument("--gem5", type=Path, default=LEAD_GEM5)
    args = parser.parse_args()
    out = args.out.resolve()
    gem5 = args.gem5.resolve()
    require(
        out != ROOT and ROOT not in out.parents,
        "raw evidence must be outside Git",
    )
    require(
        not out.exists() or not any(out.iterdir()),
        f"nonempty evidence path: {out}",
    )
    require(
        gem5 == LEAD_GEM5.resolve(),
        "only the integrated lead gem5 is permitted",
    )
    require(
        gem5.is_file() and os.access(gem5, os.X_OK), f"missing gem5: {gem5}"
    )
    require(sha256(gem5) == EXPECTED_GEM5_SHA256, "lead gem5 SHA-256 mismatch")
    require(
        RAMULATOR.is_file() and RAMULATOR_CONFIG.is_file(),
        "missing Ramulator input",
    )
    require(
        not subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        ).stdout,
        "runner requires a clean source worktree",
    )

    out.mkdir(parents=True)
    (out / "input").mkdir()
    (out / "arms").mkdir()
    checkpoint = out / "checkpoint"
    checkpoint.mkdir()
    shared_selector = out / "input/shared.arm"
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = (
        str(RAMULATOR.parent) + ":" + env.get("LD_LIBRARY_PATH", "")
    )
    ldd = subprocess.check_output(["ldd", str(gem5)], env=env, text=True)
    (out / "input/gem5.ldd.txt").write_text(ldd)
    loaded = re.search(r"^\s*libramulator\.so => (\S+)", ldd, re.M)
    require(
        loaded is not None
        and Path(loaded.group(1)).resolve() == RAMULATOR.resolve(),
        "lead gem5 did not resolve the frozen Ramulator library",
    )
    binary = out / "input/test_virtual_tile_backing_rfo"
    compile_command = compile_guest(binary)
    write_json(out / "input/compile_command.json", compile_command)
    checkpoint_process = run_logged(
        checkpoint_args(gem5, binary, checkpoint, shared_selector),
        out / "checkpoint.log",
        env,
    )
    write_json(out / "checkpoint.process.json", checkpoint_process)
    checkpoint_log = (
        (out / "checkpoint.log").read_text(errors="strict").splitlines()
    )
    require(
        checkpoint_process["returncode"] == 0
        and checkpoint_process["pid_identity_absent"],
        "checkpoint process failed",
    )
    require(
        sum(bool(CHECKPOINT_RE.fullmatch(line)) for line in checkpoint_log)
        == 1,
        "checkpoint terminal marker",
    )
    require(
        checkpoint_log.count(
            "BACKING_RFO_LAYOUT logical=16384 physical=4096 backing_lines=2048 backing_mod64=0 destination_mod64=0"
        )
        == 1,
        "checkpoint layout",
    )
    checkpoint_digest = checkpoint_identity(
        checkpoint, out / "checkpoint.files.sha256"
    )
    (out / "checkpoint.identity.sha256").write_text(
        checkpoint_digest + "  checkpoint.files.sha256\n"
    )

    for arm in ARMS:
        arm_dir = out / "arms" / arm
        arm_dir.mkdir()
        selector = arm_dir / "arm.txt"
        selector.write_text(arm + "\n")
        selector.chmod(0o444)
        if shared_selector.exists():
            shared_selector.chmod(0o644)
        shared_selector.write_text(arm + "\n")
        shared_selector.chmod(0o444)
        command = fixed_args(
            gem5, binary, checkpoint, arm_dir, shared_selector
        )
        write_json(arm_dir / "command.json", command)
        process = run_logged(command, arm_dir / "restore.log", env)
        write_json(arm_dir / "process.json", process)
        (arm_dir / "checkpoint.identity.sha256").write_text(
            checkpoint_digest + "\n"
        )
        require(
            checkpoint_identity(
                checkpoint, arm_dir / "checkpoint.files.sha256"
            )
            == checkpoint_digest,
            f"{arm}: shared checkpoint mutated",
        )

    results = {arm: validate_arm(out, arm, checkpoint_digest) for arm in ARMS}
    require(
        len({result["output_hash"] for result in results.values()}) == 1,
        "output hashes differ",
    )
    cold = results["cold"]["counters"]
    ideal = results["ideal"]["counters"]
    charged = results["charged"]["counters"]
    require(
        cold["l3_maa_region_misses"] - ideal["l3_maa_region_misses"] == 2048,
        "ideal preallocation did not remove exactly 2048 MAA-region misses",
    )
    require(
        cold["l3_maa_region_misses"] - charged["l3_maa_region_misses"] == 2048,
        "charged preallocation did not remove exactly 2048 MAA-region misses",
    )
    require(
        ideal["simTicks"] < cold["simTicks"],
        "ideal cache-state arm did not win",
    )
    require(
        charged["simTicks"] < cold["simTicks"],
        "charged preallocation did not win",
    )
    summary = {
        "schema": "dx100.hybrid_backing_rfo_bracket.v1",
        "decision": "VALID_BRACKET",
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "lead_gem5": str(gem5),
        "gem5_sha256": sha256(gem5),
        "checkpoint_identity": checkpoint_digest,
        "results": results,
        "conclusion": {
            "extra_2048_maa_region_misses_disappear": True,
            "charged_preallocation_wins": True,
            "ideal_preallocation_is_cache_state_bound_not_architecture_claim": True,
        },
    }
    write_json(out / "summary.json", summary)
    write_artifact_ledger(out)
    # Rehash every listed artifact after sealing the ledger.
    for line in (out / "artifact_sha256.txt").read_text().splitlines():
        digest, relative = line.split("  ", 1)
        require(
            sha256(out / relative) == digest,
            f"artifact ledger changed: {relative}",
        )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BracketError, subprocess.CalledProcessError, OSError) as error:
        print(f"FAIL-CLOSED: {error}", file=os.sys.stderr)
        raise SystemExit(1)
