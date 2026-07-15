#!/usr/bin/env python3
"""Run a layered BASE/MAA CG semantic gate from one fingerprinted ELF."""

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MODES = ("BASE", "MAA")
SCALAR_TOLERANCES = {
    "zeta": 1.0e-10,
    "rnorm": 1.0e-2,
    "x_sum": 1.0e-7,
    "x_norm_sq": 1.0e-7,
    "z_sum": 1.0e-6,
    "z_norm_sq": 1.0e-6,
}
FP_RE = re.compile(
    r"^CG_FINGERPRINT mode=(BASE|MAA) elements=(\d+) "
    r"x_raw=([0-9a-f]{16}) z_raw=([0-9a-f]{16}) "
    r"x_q5=([0-9a-f]{16}) x_q6=([0-9a-f]{16}) "
    r"z_q5=([0-9a-f]{16}) z_q6=([0-9a-f]{16}) "
    r"x_sum=([-+0-9.eE]+) x_norm_sq=([-+0-9.eE]+) "
    r"z_sum=([-+0-9.eE]+) z_norm_sq=([-+0-9.eE]+) "
    r"rnorm=([-+0-9.eE]+) zeta=([-+0-9.eE]+) "
    r"nonfinite_x=(\d+) nonfinite_z=(\d+) "
    r"unquantizable_x=(\d+) unquantizable_z=(\d+) result=(PASS|FAIL)$",
    re.MULTILINE,
)


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


def run_logged(command, cwd, env, log_path, timeout, on_start):
    with log_path.open("w") as output:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
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


def available_memory_bytes():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("MemAvailable is missing from /proc/meminfo")


def parse_fingerprint(text):
    matches = FP_RE.findall(text)
    if len(matches) != 1:
        return None
    values = matches[0]
    keys = (
        "mode",
        "elements",
        "x_raw",
        "z_raw",
        "x_q5",
        "x_q6",
        "z_q5",
        "z_q6",
        "x_sum",
        "x_norm_sq",
        "z_sum",
        "z_norm_sq",
        "rnorm",
        "zeta",
        "nonfinite_x",
        "nonfinite_z",
        "unquantizable_x",
        "unquantizable_z",
        "result",
    )
    result = dict(zip(keys, values))
    for key in (
        "elements",
        "nonfinite_x",
        "nonfinite_z",
        "unquantizable_x",
        "unquantizable_z",
    ):
        result[key] = int(result[key])
    for key in ("x_sum", "x_norm_sq", "z_sum", "z_norm_sq", "rnorm", "zeta"):
        result[key] = float(result[key])
    return result


def relative_delta(left, right):
    return abs(left - right) / max(abs(left), abs(right), 1.0e-300)


def checkpoint_command(args, binary, outdir, mode):
    return [
        str(args.gem5_bin),
        "--listener-mode=off",
        f"--outdir={outdir}",
        str(args.se_config),
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
        mode,
        "--prog-interval=10000000",
    ]


def restore_command(args, binary, outdir, mode):
    return [
        str(args.gem5_bin),
        "--listener-mode=off",
        f"--outdir={outdir}",
        str(args.se_config),
        "--cpu-type",
        "X86O3CPU",
        "-r",
        "1",
        "-n",
        "4",
        "--mem-size",
        "2GB",
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
        "--l3_ports",
        "4",
        "--cacheline_size=64",
        "--mem-type",
        "Ramulator2",
        "--ramulator-config",
        str(args.ramulator_config),
        "--mem-channels",
        "2",
        "--maa_ncbus_width",
        "32",
        "--maa",
        "--maa_num_maas",
        "1",
        "--maa_num_tile_elements",
        "16384",
        "--maa_l2_uncacheable",
        "--maa_l3_uncacheable",
        "--maa_num_initial_row_table_slices",
        "32",
        "--cmd",
        str(binary),
        "--options",
        mode,
        "--prog-interval=1000",
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--dx100", required=True, type=Path)
    parser.add_argument("--gem5-bin", required=True, type=Path)
    parser.add_argument("--ramulator-config", required=True, type=Path)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--data-header", required=True, type=Path)
    parser.add_argument(
        "--expected-x-q5",
        required=True,
        type=lambda value: value
        if re.fullmatch(r"[0-9a-f]{16}", value)
        else parser.error("--expected-x-q5 must be 16 lowercase hex digits"),
        help="independently qualified x-vector hash for this exact matrix",
    )
    parser.add_argument("--checkpoint-timeout", type=int, default=43200)
    parser.add_argument("--run-timeout", type=int, default=259200)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.se_config = args.dx100 / "configs/deprecated/example/se.py"
    required = (
        args.gem5_bin,
        args.ramulator_config,
        args.binary,
        args.data_header,
        args.se_config,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        parser.error("missing inputs: " + ", ".join(missing))
    available = available_memory_bytes()
    if not args.dry_run and available < 64 * 1024**3:
        parser.error("require at least 64 GiB available memory")

    commands = {
        "checkpoints": {
            mode: checkpoint_command(
                args,
                args.binary,
                args.runtime / "checkpoints" / mode.lower(),
                mode,
            )
            for mode in MODES
        },
        "runs": {
            mode: restore_command(
                args, args.binary, args.runtime / "runs" / mode.lower(), mode
            )
            for mode in MODES
        },
    }
    if args.dry_run:
        print(json.dumps(commands, indent=2))
        return 0

    args.runtime.mkdir(parents=True, exist_ok=False)
    assets = args.runtime / "assets"
    assets.mkdir()
    binary = assets / args.binary.name
    header = assets / args.data_header.name
    shutil.copy2(args.binary, binary)
    shutil.copy2(args.data_header, header)
    commands = {
        "checkpoints": {
            mode: checkpoint_command(
                args, binary, args.runtime / "checkpoints" / mode.lower(), mode
            )
            for mode in MODES
        },
        "runs": {
            mode: restore_command(
                args, binary, args.runtime / "runs" / mode.lower(), mode
            )
            for mode in MODES
        },
    }
    status_path = args.runtime / "status.json"
    status = {
        "terminal": False,
        "finalized": False,
        "success": False,
        "semantic_agreement": False,
        "performance_interpretable": False,
        "phase": "checkpoints",
        "started_at": now(),
        "workload": "NAS CG shortened Class C layered BASE/MAA gate",
        "runtime": str(args.runtime),
        "binary": str(binary),
        "binary_sha256": sha256(binary),
        "data_header": str(header),
        "data_header_sha256": sha256(header),
        "gem5_sha256": sha256(args.gem5_bin),
        "launch_memory_available_bytes": available,
        "comparison_policy": {
            "expected_elements": 150000,
            "expected_x_q5": args.expected_x_q5,
            "required_exact": ["elements", "x_q5"],
            "scalar_relative_tolerances": SCALAR_TOLERANCES,
            "diagnostic_only": ["x_raw", "z_raw", "x_q6", "z_q5", "z_q6"],
            "basis": "repeated native BASE/MAA Class-C runs",
        },
        "commands": commands,
        "checkpoints": {},
        "runs": {},
    }
    lock = threading.RLock()

    def publish():
        with lock:
            atomic_json(status_path, status)

    def update_record(record, **values):
        with lock:
            record.update(values)
            atomic_json(status_path, status)

    publish()
    env = dict(os.environ, OMP_PROC_BIND="false", OMP_NUM_THREADS="4")
    try:

        def create_checkpoint(mode):
            outdir = args.runtime / "checkpoints" / mode.lower()
            outdir.mkdir(parents=True)
            record = {
                "mode": mode,
                "outdir": str(outdir),
                "started_at": now(),
                "terminal": False,
            }
            with lock:
                status["checkpoints"][mode] = record
            publish()
            rc = run_logged(
                commands["checkpoints"][mode],
                args.dx100,
                env,
                outdir / "run.log",
                args.checkpoint_timeout,
                lambda pid: update_record(record, pid=pid),
            )
            text = (outdir / "run.log").read_text(errors="replace")
            numbered = sorted(outdir.glob("cpt.[0-9]*"))
            update_record(
                record,
                returncode=rc,
                terminal=True,
                finished_at=now(),
                directories=len(numbered),
                used_precomputed_data="Using data from file!" in text,
                entered_makea="makea started!" in text,
                mode_marker=f"Mode: {mode}" in text,
            )
            if (
                rc != 0
                or len(numbered) != 1
                or not record["used_precomputed_data"]
                or record["entered_makea"]
                or not record["mode_marker"]
            ):
                raise RuntimeError(
                    f"{mode} checkpoint failed rc={rc}, directories={len(numbered)}"
                )
            source = numbered[0]
            update_record(
                record,
                path=str(source),
                m5_sha256=sha256(source / "m5.cpt"),
                pmem_sha256=sha256(source / "system.physmem.store0.pmem"),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(create_checkpoint, mode) for mode in MODES
            ]
            for future in futures:
                future.result()

        status["phase"] = "runs"
        publish()

        def run_mode(mode):
            checkpoint = status["checkpoints"][mode]
            source = Path(checkpoint["path"])
            outdir = args.runtime / "runs" / mode.lower()
            outdir.mkdir(parents=True)
            copied = outdir / source.name
            shutil.copytree(source, copied)
            record = {
                "mode": mode,
                "outdir": str(outdir),
                "started_at": now(),
                "terminal": False,
                "checkpoint_m5_sha256": sha256(copied / "m5.cpt"),
                "checkpoint_pmem_sha256": sha256(
                    copied / "system.physmem.store0.pmem"
                ),
            }
            with lock:
                status["runs"][mode] = record
            publish()
            rc = run_logged(
                commands["runs"][mode],
                args.dx100,
                env,
                outdir / "run.log",
                args.run_timeout,
                lambda pid: update_record(record, pid=pid),
            )
            text = (outdir / "run.log").read_text(errors="replace")
            config_path = outdir / "config.ini"
            config = (
                config_path.read_text(errors="replace")
                if config_path.is_file()
                else ""
            )
            fingerprint = parse_fingerprint(text)
            update_record(
                record,
                returncode=rc,
                terminal=True,
                finished_at=now(),
                fingerprint=fingerprint,
                fingerprint_pass=(
                    fingerprint is not None
                    and fingerprint["mode"] == mode
                    and fingerprint["elements"] == 150000
                    and fingerprint["x_q5"] == args.expected_x_q5
                    and fingerprint["result"] == "PASS"
                ),
                normal_exit="m5_exit instruction encountered" in text,
                fatal_marker=bool(
                    re.search(
                        r"fatal:|panic:|segmentation fault|assertion.*failed",
                        text,
                        re.I,
                    )
                ),
                roi_end_count=text.count("ROI End!!!"),
                validation_started_count=text.count("Validation started"),
                validation_ended_count=text.count("Validation ended"),
                config_verified=(
                    "num_tile_elements=16384" in config
                    and "num_initial_row_table_slices=32" in config
                ),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(run_mode, mode) for mode in MODES]
            for future in futures:
                future.result()

        base, maa = status["runs"]["BASE"], status["runs"]["MAA"]
        valid = all(
            run.get("returncode") == 0
            and run.get("fingerprint_pass")
            and run.get("normal_exit")
            and not run.get("fatal_marker")
            and run.get("roi_end_count") == 1
            and run.get("validation_started_count") == 1
            and run.get("validation_ended_count") == 1
            and run.get("config_verified")
            and run.get("checkpoint_m5_sha256")
            == status["checkpoints"][run["mode"]]["m5_sha256"]
            and run.get("checkpoint_pmem_sha256")
            == status["checkpoints"][run["mode"]]["pmem_sha256"]
            for run in (base, maa)
        )
        bf, mf = base.get("fingerprint"), maa.get("fingerprint")
        exact = {
            key: bf is not None and mf is not None and bf[key] == mf[key]
            for key in ("elements", "x_q5")
        }
        deltas = {
            key: relative_delta(bf[key], mf[key])
            for key in SCALAR_TOLERANCES
            if bf is not None and mf is not None
        }
        status["exact_comparisons"] = exact
        status["scalar_relative_deltas"] = deltas
        agreement = (
            valid
            and all(exact.values())
            and len(deltas) == len(SCALAR_TOLERANCES)
            and all(
                deltas[key] <= limit
                for key, limit in SCALAR_TOLERANCES.items()
            )
        )
        status.update(
            terminal=True,
            finalized=True,
            success=agreement,
            semantic_agreement=agreement,
            phase="complete",
            finished_at=now(),
        )
        publish()
        return 0 if agreement else 1
    except BaseException as error:
        status.update(
            terminal=True,
            finalized=True,
            success=False,
            semantic_agreement=False,
            phase="failed",
            finished_at=now(),
            error=f"{type(error).__name__}: {error}",
        )
        publish()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
