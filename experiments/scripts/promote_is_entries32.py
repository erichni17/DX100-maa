#!/usr/bin/env python3
"""Run a fingerprinted NAS IS entries-8/32 promotion from one private checkpoint."""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ARMS = (("default", 8), ("entries32", 32))
WORK_STATS = {
    "system.maa.numInst": "maa_instructions",
    "system.maa.I0_IND_NumWordsInserted": "words_inserted",
    "system.maa.I0_IND_NumUniqueCacheLineInserted": "unique_cache_lines",
    "system.maa.I0_IND_NumRowsInserted": "rows_inserted",
    "system.maa.S0_STR_NumRTFull": "row_table_full",
}
INVARIANTS = ("maa_instructions", "words_inserted", "unique_cache_lines")


def now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path, document):
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n")
    temporary.replace(path)


def parse_stats(path):
    cycles = final_cycles = None
    values = {}
    for line in path.read_text(errors="replace").splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        if fields[0] == "system.switch_cpus0.numCycles":
            value = int(float(fields[1]))
            if cycles is None:
                cycles = value
            final_cycles = value
        elif fields[0] in WORK_STATS and WORK_STATS[fields[0]] not in values:
            values[WORK_STATS[fields[0]]] = int(float(fields[1]))
    return cycles, final_cycles, values


def relative_delta(left, right):
    return abs(left - right) / max(abs(left), abs(right), 1)


def run_logged(command, cwd, env, log_path, timeout, on_start):
    with log_path.open("w") as output:
        process = subprocess.Popen(
            command, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        on_start(process.pid)
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            return 124


def checkpoint_command(args, outdir):
    return [
        str(args.gem5_bin), "--listener-mode=off", f"--outdir={outdir}",
        str(args.se_config), "--cpu-type", "AtomicSimpleCPU", "-n", "4",
        "--mem-size", "16GB", "--max-checkpoints=1", "--cmd",
        str(args.binary), "--options", "MAA",
    ]


def run_command(args, outdir, entries):
    return [
        str(args.gem5_bin), "--listener-mode=off", f"--outdir={outdir}",
        str(args.se_config), "--cpu-type", "X86O3CPU", "-r", "1", "-n", "4",
        "--mem-size", "16GB", "--sys-clock", "3.2GHz", "--cpu-clock", "3.2GHz",
        "--caches", "--l1d_size=32kB", "--l1d_assoc=8",
        "--l1d-hwp-type=StridePrefetcher", "--l1d_mshrs=16",
        "--l1d_write_buffers=8", "--l1i_size=32kB", "--l1i_assoc=8",
        "--l1i-hwp-type=StridePrefetcher", "--l1i_mshrs=16",
        "--l1i_write_buffers=8", "--l2cache", "--l2_size=256kB",
        "--l2_assoc=4", "--l2-hwp-type=StridePrefetcher", "--l2_mshrs=32",
        "--l2_write_buffers=16", "--l3cache", "--l3_size=8MB",
        "--l3_assoc=16", "--l3_mshrs=256", "--l3_write_buffers=128",
        "--l3_ports", "4", "--cacheline_size=64", "--mem-type", "Ramulator2",
        "--ramulator-config", str(args.ramulator_config), "--mem-channels", "2",
        "--maa_ncbus_width", "32", "--maa", "--maa_num_maas", "1",
        "--maa_num_tile_elements", "16384", "--maa_l2_uncacheable",
        "--maa_l3_uncacheable", "--maa_num_initial_row_table_slices", "32",
        "--maa_num_row_table_entries_per_subslice_row", str(entries),
        "--maa_num_row_table_rows_per_slice", "64", "--cmd", str(args.binary),
        "--options", "MAA", "--prog-interval=1000",
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--dx100", required=True, type=Path)
    parser.add_argument("--gem5-bin", required=True, type=Path)
    parser.add_argument("--ramulator-config", required=True, type=Path)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--key-header", required=True, type=Path)
    parser.add_argument("--checkpoint-timeout", type=int, default=7200)
    parser.add_argument("--run-timeout", type=int, default=14400)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.se_config = args.dx100 / "configs/deprecated/example/se.py"
    required = (args.gem5_bin, args.ramulator_config, args.binary, args.key_header, args.se_config)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        parser.error("missing inputs: " + ", ".join(missing))
    if not os.access(args.gem5_bin, os.X_OK) or not os.access(args.binary, os.X_OK):
        parser.error("gem5 and IS binary must be executable")

    checkpoint_dir = args.runtime / "checkpoint"
    commands = {
        "checkpoint": checkpoint_command(args, checkpoint_dir),
        "arms": {arm: run_command(args, args.runtime / "runs" / arm, entries)
                 for arm, entries in ARMS},
    }
    if args.dry_run:
        print(json.dumps(commands, indent=2))
        return 0
    args.runtime.mkdir(parents=True, exist_ok=False)
    assets = args.runtime / "assets"
    assets.mkdir()
    private_binary, private_header = assets / args.binary.name, assets / args.key_header.name
    shutil.copy2(args.binary, private_binary)
    shutil.copy2(args.key_header, private_header)
    args.binary = private_binary
    commands["checkpoint"] = checkpoint_command(args, checkpoint_dir)
    commands["arms"] = {arm: run_command(args, args.runtime / "runs" / arm, entries)
                        for arm, entries in ARMS}

    status_path = args.runtime / "status.json"
    status = {
        "terminal": False, "success": False, "phase": "checkpoint",
        "started_at": now(), "workload": "NAS IS Class B", "tile_elements": 16384,
        "comparison": "entries 8 versus 32; rows per slice fixed at 64",
        "runtime": str(args.runtime), "gem5_bin": str(args.gem5_bin),
        "gem5_sha256": sha256(args.gem5_bin), "binary": str(args.binary),
        "binary_sha256": sha256(args.binary), "key_header": str(private_header),
        "key_header_sha256": sha256(private_header), "arms": {},
    }
    lock = threading.Lock()

    def publish():
        with lock:
            atomic_json(status_path, status)

    publish()
    env = dict(os.environ, OMP_PROC_BIND="false", OMP_NUM_THREADS="4")
    checkpoint_dir.mkdir()
    checkpoint_record = {"outdir": str(checkpoint_dir), "started_at": now(), "terminal": False}
    status["checkpoint"] = checkpoint_record
    publish()
    rc = run_logged(
        commands["checkpoint"], args.dx100, env, checkpoint_dir / "run.log",
        args.checkpoint_timeout,
        lambda pid: (checkpoint_record.update(pid=pid), publish()),
    )
    numbered = sorted(checkpoint_dir.glob("cpt.[0-9]*"))
    checkpoint_record.update(returncode=rc, terminal=True, finished_at=now())
    if rc != 0 or len(numbered) != 1:
        status.update(terminal=True, phase="checkpoint_failed", finished_at=now(),
                      error=f"checkpoint rc={rc}, directories={len(numbered)}")
        publish()
        return 1
    source_checkpoint = numbered[0]
    checkpoint_record.update(
        path=str(source_checkpoint), m5_sha256=sha256(source_checkpoint / "m5.cpt"),
        pmem_sha256=sha256(source_checkpoint / "system.physmem.store0.pmem"),
    )
    status["phase"] = "promotion"
    publish()

    def run_arm(arm, entries):
        outdir = args.runtime / "runs" / arm
        outdir.mkdir(parents=True)
        copied = outdir / source_checkpoint.name
        shutil.copytree(source_checkpoint, copied)
        record = {
            "entries": entries, "rows_per_slice": 64, "outdir": str(outdir),
            "started_at": now(), "terminal": False,
            "checkpoint_m5_sha256": sha256(copied / "m5.cpt"),
            "checkpoint_pmem_sha256": sha256(copied / "system.physmem.store0.pmem"),
        }
        with lock:
            status["arms"][arm] = record
        publish()
        rc = run_logged(
            commands["arms"][arm], args.dx100, env, outdir / "run.log", args.run_timeout,
            lambda pid: (record.update(pid=pid), publish()),
        )
        text = (outdir / "run.log").read_text(errors="replace")
        cycles, final_cycles, work = parse_stats(outdir / "stats.txt")
        config = (outdir / "config.ini").read_text(errors="replace")
        marker = re.findall(r"^IS_VERIFY passed=(\d+) expected=(\d+) result=(PASS|FAIL)$", text, re.MULTILINE)
        record.update(
            terminal=True, finished_at=now(), returncode=rc, cycles=cycles,
            final_cycles=final_cycles, work=work, verification=marker[-1] if marker else None,
            verification_pass=(len(marker) == 1 and marker[0] == ("6", "6", "PASS")),
            normal_exit="m5_exit instruction encountered" in text,
            fatal_marker=bool(re.search(r"fatal:|panic:|segmentation fault|assertion.*failed", text, re.I)),
            roi_end_count=text.count("ROI End!!!"),
            validation_started_count=text.count("Validation started"),
            validation_ended_count=text.count("Validation ended"),
            config_verified=(
                f"num_row_table_entries_per_subslice_row={entries}" in config
                and "num_row_table_rows_per_slice=64" in config
                and "num_initial_row_table_slices=32" in config
                and "num_tile_elements=16384" in config
            ),
        )
        publish()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(run_arm, arm, entries) for arm, entries in ARMS]
            for future in futures:
                future.result()
    except BaseException as error:
        status.update(terminal=True, phase="promotion_failed", finished_at=now(),
                      error=f"{type(error).__name__}: {error}")
        publish()
        raise

    baseline, candidate = status["arms"]["default"], status["arms"]["entries32"]
    deltas = {key: relative_delta(baseline["work"][key], candidate["work"][key])
              for key in INVARIANTS if key in baseline["work"] and key in candidate["work"]}
    status["invariant_relative_deltas"] = deltas
    if baseline.get("cycles") and candidate.get("cycles"):
        status["speedup"] = baseline["cycles"] / candidate["cycles"]
    valid = all(
        arm.get("returncode") == 0 and arm.get("cycles") and arm.get("verification_pass")
        and arm.get("normal_exit") and not arm.get("fatal_marker") and arm.get("config_verified")
        and arm.get("checkpoint_m5_sha256") == checkpoint_record["m5_sha256"]
        and arm.get("checkpoint_pmem_sha256") == checkpoint_record["pmem_sha256"]
        and arm.get("roi_end_count") == 1 and arm.get("validation_started_count") == 1
        and arm.get("validation_ended_count") == 1 for arm in (baseline, candidate)
    )
    status.update(
        terminal=True, phase="complete", finished_at=now(),
        success=(valid and len(deltas) == len(INVARIANTS)
                 and all(delta <= 0.002 for delta in deltas.values())),
    )
    publish()
    return 0 if status["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
