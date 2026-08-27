#!/usr/bin/env python3
"""Run one bounded, traced fused-p16/q16 forward-progress probe.

This is deliberately not a CG performance experiment.  It executes exactly
one 16K fused producer followed by one q16 consumer under the four-indirect-
unit geometry used by the NA=1024 confirmation.  A relative simulated-tick
ceiling and a short wall-clock watchdog bound both ordinary slowdowns and
same-tick event churn.  The watchdog periodically records a compact trace
snapshot, then classifies only signatures that the trace proves.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
GUEST_SOURCE = ROOT / "benchmarks/API/test_fused_p16_product.cpp"
CONFIG = ROOT / "configs/deprecated/example/se.py"
RAMULATOR_CONFIG = ROOT / "ext/ramulator2/ramulator2/example_gem5_config.yaml"
GEM5 = Path(
    "/data1/nier/worktrees/codex-sessions/"
    "hybrid-fused-p16-product-evidence-repair-2026082-20260826-160656-"
    "c4f154c5/DX100-virtualization-selected-integration-cont-20260826/"
    "build/X86/gem5.opt"
)
RAMULATOR = Path(
    "/data1/nier/dx100-runs/2026-08-12-hybrid-line-handoff-8a5c7712/"
    "input/libramulator.so"
)
PINNED_SOURCE = "4a4d91b8f176c33779804fbd163014593d89e737"
WORDS = 16_384
PAGES = 4
DEFAULT_REL_MAX_TICK = 8_000_000_000
DEFAULT_WALL_SECONDS = 180
DEFAULT_SAMPLE_SECONDS = 2.0

TRACE_LINE = re.compile(r"^(?P<tick>\d+): .*?event=(?P<event>\S+)(?: |$)")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def parse_trace(path: Path) -> list[dict[str, Any]]:
    """Return only structured trace events, tolerating partial watchdog reads."""
    if not path.is_file():
        return []
    return parse_trace_file_text(path.read_text(errors="replace"))


def parse_trace_file_text(text: str) -> list[dict[str, Any]]:
    """Parse a complete or partial MAAVirtualTrace text fragment."""
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = TRACE_LINE.match(line)
        if match is not None:
            records.append(
                {"tick": int(match["tick"]), "event": match["event"]}
            )
    return records


def classify_timeout(records: list[dict[str, Any]]) -> str:
    """Classify only a terminal trace signature; otherwise remain inconclusive."""
    if not records:
        return "NO_TRACE_PROGRESS"
    if any(
        record["event"] == "fused_p16_product_complete" for record in records
    ):
        return "PRODUCER_COMPLETED_BEFORE_WATCHDOG"

    last_progress = max(
        (
            index
            for index, record in enumerate(records)
            if record["event"] == "fused_p16_mul_complete"
        ),
        default=-1,
    )
    suffix = records[last_progress + 1 :]
    executes = [
        record for record in suffix if record["event"] == "indirect_execute"
    ]
    if (
        len(executes) >= 64
        and len({record["tick"] for record in executes}) == 1
    ):
        return "EVENT_EXPLOSION_SAME_TICK"

    stalls = [
        record for record in suffix if record["event"] == "indirect_stall"
    ]
    if len(stalls) >= 16 and len({record["tick"] for record in stalls}) >= 2:
        return "QUEUE_POLLING_FORWARD_PROGRESS_COLLAPSE"
    return "INCONCLUSIVE_TIMEOUT"


def trace_snapshot(path: Path, elapsed_seconds: float) -> dict[str, Any]:
    records = parse_trace(path)
    return {
        "elapsed_seconds": round(elapsed_seconds, 3),
        "trace_bytes": path.stat().st_size if path.is_file() else 0,
        "events": len(records),
        "last_tick": records[-1]["tick"] if records else None,
        "last_event": records[-1]["event"] if records else None,
        "mul_completions": sum(
            record["event"] == "fused_p16_mul_complete" for record in records
        ),
        "classification": classify_timeout(records),
    }


def restore_command(
    guest: Path, checkpoint: Path, out: Path, rel_max_tick: int
) -> list[str]:
    """Match the NA=1024 candidate geometry but constrain it to one micro op."""
    return [
        str(GEM5),
        "--listener-mode=off",
        f"--outdir={out}",
        "--debug-flags=MAAVirtualTrace",
        "--debug-file=forward_progress.trace",
        "--rel-max-tick",
        str(rel_max_tick),
        str(CONFIG),
        "--cpu-type",
        "X86O3CPU",
        "-r",
        "1",
        "-n",
        "4",
        "--mem-size",
        "2GB",
        "--checkpoint-dir",
        str(checkpoint),
        "--sys-clock",
        "3.2GHz",
        "--cpu-clock",
        "3.2GHz",
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
        "--mem-type",
        "Ramulator2",
        "--ramulator-config",
        str(RAMULATOR_CONFIG),
        "--mem-channels=2",
        "--maa",
        "--maa_num_maas=1",
        "--maa_num_indirect_units_per_maa=4",
        "--maa_num_tiles_per_core=8",
        "--maa_num_tile_elements=16384",
        "--maa_physical_tile_elements=4096",
        "--maa_num_offset_table_entries=16384",
        "--maa_num_offset_table_epoch_entries=16384",
        "--maa_num_initial_row_table_slices=32",
        "--maa_soa_jit_predicate_active_credits=16",
        "--maa_virtual_combine_slots=16",
        "--maa_virtual_combine_ways=4",
        "--maa_virtual_combine_banks=4",
        "--maa_virtual_words_per_cycle=1",
        "--maa_virtual_response_slots=8",
        "--maa_virtual_response_words=0",
        "--maa_virtual_response_word_pool=0",
        "--maa_virtual_max_outstanding_writes=32",
        "--maa_page_fed_soa_jit",
        "--maa_soa_jit_value_cache_enable",
        "--maa_soa_jit_active_value_owners=32",
        "--maa_soa_jit_value_prefetch_credits=0",
        "--cmd",
        str(guest),
    ]


def observe(
    command: list[str],
    log: Path,
    trace: Path,
    wall_seconds: int,
    sample_seconds: float,
) -> dict[str, Any]:
    """Run a local probe and kill only its own process group on watchdog expiry."""
    started = time.monotonic()
    snapshots: list[dict[str, Any]] = []
    with log.open("w") as output:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={
                **os.environ,
                "LD_LIBRARY_PATH": f"{RAMULATOR.parent}:{os.environ.get('LD_LIBRARY_PATH', '')}",
                "OMP_NUM_THREADS": "1",
            },
        )
        while process.poll() is None:
            elapsed = time.monotonic() - started
            snapshots.append(trace_snapshot(trace, elapsed))
            if elapsed >= wall_seconds:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                return {
                    "watchdog_expired": True,
                    "returncode": process.returncode,
                    "snapshots": snapshots,
                }
            time.sleep(sample_seconds)
    snapshots.append(trace_snapshot(trace, time.monotonic() - started))
    return {
        "watchdog_expired": False,
        "returncode": process.returncode,
        "snapshots": snapshots,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument(
        "--wall-seconds", type=int, default=DEFAULT_WALL_SECONDS
    )
    parser.add_argument(
        "--sample-seconds", type=float, default=DEFAULT_SAMPLE_SECONDS
    )
    parser.add_argument(
        "--rel-max-tick", type=int, default=DEFAULT_REL_MAX_TICK
    )
    args = parser.parse_args(argv)
    require(
        args.wall_seconds > 0
        and args.sample_seconds > 0
        and args.rel_max_tick > 0,
        "bounds must be positive",
    )
    out = args.out.resolve()
    require(
        not out.exists() or not any(out.iterdir()), "refusing nonempty output"
    )
    require(
        GEM5.is_file() and os.access(GEM5, os.X_OK),
        f"missing pinned gem5: {GEM5}",
    )
    require(RAMULATOR.is_file(), f"missing pinned Ramulator: {RAMULATOR}")
    schema = subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            PINNED_SOURCE,
            "--",
            "src",
            "configs",
            "benchmarks",
            "include",
            "util",
        ],
        cwd=ROOT,
        check=False,
    )
    require(
        schema.returncode == 0,
        "probe source/config differs from accepted schema",
    )

    out.mkdir(parents=True)
    checkpoint = out / "checkpoint"
    checkpoint.mkdir()
    guest = out / "fused_p16_product_probe_guest"
    compile_command = [
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
        "-DGEM5",
        "-DMAA",
        "-DNUM_CORES=4",
        "-DNUM_TILES_PER_CORE=8",
        "-DTILE_SIZE=16384",
        "-DMAA_MEM_SIZE=0x80000000",
        str(ROOT / "util/m5/src/abi/x86/m5op.S"),
        str(GUEST_SOURCE),
        "-o",
        str(guest),
    ]
    subprocess.run(compile_command, cwd=ROOT, check=True)
    checkpoint_command = [
        str(GEM5),
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
    ]
    with (out / "checkpoint.log").open("w") as log:
        checkpoint_result = subprocess.run(
            checkpoint_command,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    require(checkpoint_result.returncode == 0, "checkpoint failed")
    command = restore_command(
        guest, checkpoint, out / "run", args.rel_max_tick
    )
    (out / "command.json").write_text(
        json.dumps(
            {
                "compile": compile_command,
                "checkpoint": checkpoint_command,
                "restore": command,
            },
            indent=2,
        )
        + "\n"
    )
    run = out / "run"
    run.mkdir()
    observed = observe(
        command,
        run / "restore.log",
        run / "forward_progress.trace",
        args.wall_seconds,
        args.sample_seconds,
    )
    trace = run / "forward_progress.trace"
    records = parse_trace(trace)
    result = {
        "schema": "dx100.fused_p16.forward_progress_probe.v1",
        "words": WORDS,
        "pages": PAGES,
        "indirect_units": 4,
        "application_runs": 0,
        "rel_max_tick": args.rel_max_tick,
        "wall_seconds": args.wall_seconds,
        "observation": observed,
        "classification": classify_timeout(records),
    }
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return (
        0
        if not observed["watchdog_expired"] and observed["returncode"] == 0
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
