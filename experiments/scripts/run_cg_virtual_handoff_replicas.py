#!/usr/bin/env python3
"""Run and validate the frozen CG virtual-handoff replica campaign."""

import argparse
import csv
import hashlib
import io
import json
import os
import shlex
import statistics
import subprocess
import sys
import time
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path

EXPECTED_SHA256 = {
    "gem5": "15813d45877c7ca34b3b08944e9a6f61f177a4317542aa6b98f80857fec94e3d",
    "precomputed_header": "f2b18716e4a2356c597c95ee3583549def72700f2cb3294b0fcaacca46dbe131",
    "control_binary": "5bbcfcec1a1a7f47b31dbaa9a5e37a574a5b06d0545e12f58e5ec4e676da270e",
    "virtual_binary": "656dcfca21d91d22e7ced2a380575f81920fff31d675d2af7b7e534f0014cc2a",
    "control_m5_cpt": "abc78ad7ff5d6477c5e4c535552f03cb7c64518930855a608efeb6a414eef11d",
    "control_pmem": "10c44fd8a5a52984d5120fc514d26d4302672ca7639a3e0b8efb3eae37b9ac65",
    "virtual_m5_cpt": "70bd7d793c849c14dc3913cab7549ce496e06467cd86194d8c13b8e57bc6b2fe",
    "virtual_pmem": "8daf8846bd519e58494bc1e046bd1ee516dfae7a03162f71b05d0d18044d4622",
}

# Corrected native CG is the numerical oracle. Raw hashes differ under benign
# floating-point reorderings, so the gate uses both 1e-5 and 1e-6 hashes.
EXPECTED_FINGERPRINT = {
    "x_q5": "88c0975669c7062d",
    "x_q6": "235baae2cde3472e",
    "z_q5": "9d0c4e827a12742b",
    "z_q6": "35dce54d02fd013a",
}

EXPECTED_VIRTUAL_WRITES = 52_675_689
GEOMETRY = {
    "combine_slots": 384,
    "response_slots": 96,
    "response_words": 480,
    "combine_ways": 4,
    "words_per_cycle": 4,
    "max_outstanding_writes": 64,
    "masked_writes": True,
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path, content):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content)
    temporary.replace(path)


def atomic_json(path, value):
    atomic_write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def parse_args():
    repo = Path(__file__).resolve().parents[2]
    default_campaign = (
        repo / "experiments/campaigns/2026-07-17_cg_virtual_handoff_replicas"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replicas", type=int, default=3)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--summarize-only", action="store_true")
    parser.add_argument("--campaign-root", type=Path, default=default_campaign)
    parser.add_argument(
        "--gem5",
        type=Path,
        default=Path(
            "/data1/nier/DX100/build/X86/gem5.opt.virtual_hw_15813d45877c"
        ),
    )
    parser.add_argument(
        "--ramulator-config",
        type=Path,
        default=Path(
            "/data1/nier/DX100/ext/ramulator2/ramulator2/"
            "example_gem5_config.yaml"
        ),
    )
    parser.add_argument(
        "--ramulator-lib-dir",
        type=Path,
        default=Path("/data1/nier/DX100/ext/ramulator2/ramulator2"),
    )
    parser.add_argument(
        "--virtual-checkpoint",
        type=Path,
        default=Path(
            "/data1/nier/DX100/experiments/campaigns/"
            "2026-07-16_cg_virtual_handoff_gate/virtual_fp_checkpoint"
        ),
    )
    args = parser.parse_args()
    if args.replicas < 1:
        parser.error("--replicas must be positive")
    if args.poll_seconds < 1:
        parser.error("--poll-seconds must be positive")
    args.repo = repo
    args.config = repo / "configs/deprecated/example/se.py"
    args.control_binary = repo / "benchmarks/NAS/cg/cg_maa_16K_fp_frozen"
    args.virtual_binary = (
        repo / "benchmarks/NAS/cg/cg_maa_16K_virtual_fp_frozen"
    )
    args.control_checkpoint = args.campaign_root / "control_fp_checkpoint"
    return args


def artifact_paths(args):
    return {
        "gem5": args.gem5,
        "precomputed_header": args.repo / "benchmarks/NAS/cg/cg_data_4C.h",
        "control_binary": args.control_binary,
        "virtual_binary": args.virtual_binary,
        "control_m5_cpt": (args.control_checkpoint / "cpt.52750422000/m5.cpt"),
        "control_pmem": (
            args.control_checkpoint
            / "cpt.52750422000/system.physmem.store0.pmem"
        ),
        "virtual_m5_cpt": (args.virtual_checkpoint / "cpt.52750568500/m5.cpt"),
        "virtual_pmem": (
            args.virtual_checkpoint
            / "cpt.52750568500/system.physmem.store0.pmem"
        ),
    }


def verify_artifacts(args):
    identities = {}
    for name, path in artifact_paths(args).items():
        if not path.is_file():
            raise RuntimeError(f"missing {name}: {path}")
        actual = sha256_file(path)
        expected = EXPECTED_SHA256[name]
        if actual != expected:
            raise RuntimeError(
                f"{name} SHA-256 mismatch: expected {expected}, got {actual}"
            )
        identities[name] = {"path": str(path), "sha256": actual}

    supplemental = {
        "se_config": args.config,
        "ramulator_config": args.ramulator_config,
        "ramulator_library": args.ramulator_lib_dir / "libramulator.so",
    }
    for name, path in supplemental.items():
        if not path.is_file():
            raise RuntimeError(f"missing {name}: {path}")
        identities[name] = {"path": str(path), "sha256": sha256_file(path)}
    return identities


def gem5_command(args, case, outdir):
    binary = args.control_binary if case == "control" else args.virtual_binary
    checkpoint = (
        args.control_checkpoint
        if case == "control"
        else args.virtual_checkpoint
    )
    return [
        str(args.gem5),
        "--listener-mode=off",
        f"--outdir={outdir}",
        str(args.config),
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
        "--maa_virtual_combine_slots",
        str(GEOMETRY["combine_slots"]),
        "--maa_virtual_response_slots",
        str(GEOMETRY["response_slots"]),
        "--maa_virtual_response_words",
        str(GEOMETRY["response_words"]),
        "--maa_virtual_combine_ways",
        str(GEOMETRY["combine_ways"]),
        "--maa_virtual_words_per_cycle",
        str(GEOMETRY["words_per_cycle"]),
        "--maa_virtual_max_outstanding_writes",
        str(GEOMETRY["max_outstanding_writes"]),
        "--maa_virtual_masked_writes",
        "--cmd",
        str(binary),
        "--options",
        "MAA",
        "--prog-interval=1000",
        "--checkpoint-dir",
        str(checkpoint),
    ]


def build_manifest(args, identities):
    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=args.repo, text=True
    ).strip()
    runs = []
    for case in ("control", "virtual"):
        for replica in range(1, args.replicas + 1):
            run_id = f"{case}_r{replica}"
            outdir = args.campaign_root / run_id
            runs.append(
                {
                    "case": case,
                    "command": gem5_command(args, case, outdir),
                    "outdir": str(outdir),
                    "replica": replica,
                    "run_id": run_id,
                }
            )
    return {
        "artifacts": identities,
        "created_at": utc_now(),
        "geometry": GEOMETRY,
        "host": os.uname().nodename,
        "replicas_per_case": args.replicas,
        "runs": runs,
        "source_commit": source_commit,
    }


def write_launcher(args, run):
    outdir = Path(run["outdir"])
    outdir.mkdir(parents=True, exist_ok=False)
    command = shlex.join(run["command"])
    old_library_path = os.environ.get("LD_LIBRARY_PATH", "")
    library_path = str(args.ramulator_lib_dir)
    if old_library_path:
        library_path += ":" + old_library_path
    launcher = outdir / "launch.sh"
    launcher.write_text(
        "#!/usr/bin/env bash\n"
        "set +e\n"
        f"export LD_LIBRARY_PATH={shlex.quote(library_path)}\n"
        "date +%s > started_epoch\n"
        f"{command} > run.log 2>&1\n"
        "rc=$?\n"
        "printf '%s\\n' \"$rc\" > exit_code.tmp\n"
        "mv exit_code.tmp exit_code\n"
        "date +%s > finished_epoch\n"
        'exit "$rc"\n'
    )
    launcher.chmod(0o755)
    atomic_json(outdir / "run_manifest.json", run)
    atomic_write(outdir / "command.txt", command + "\n")
    return launcher


def parse_scalar(value):
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def parse_first_stats(path):
    metrics = {}
    section_count = 0
    in_first = False
    with path.open(errors="replace") as stats:
        for line in stats:
            if "Begin Simulation Statistics" in line:
                section_count += 1
                in_first = section_count == 1
                continue
            if in_first and "End Simulation Statistics" in line:
                in_first = False
                continue
            if not in_first:
                continue
            fields = line.split()
            if len(fields) >= 2:
                metrics[fields[0]] = parse_scalar(fields[1])
    return metrics, section_count


def parse_fingerprint(line):
    fields = {}
    for token in line.split()[1:]:
        if "=" in token:
            key, value = token.split("=", 1)
            fields[key] = value
    return fields


def read_int(path):
    return int(path.read_text().strip())


def validate_run(run):
    outdir = Path(run["outdir"])
    errors = []
    log_path = outdir / "run.log"
    stats_path = outdir / "stats.txt"
    exit_path = outdir / "exit_code"
    for path in (log_path, stats_path, exit_path):
        if not path.is_file():
            errors.append(f"missing {path.name}")
    if errors:
        return {**run, "errors": errors, "valid": False}

    exit_code = read_int(exit_path)
    log = log_path.read_text(errors="replace")
    metrics, section_count = parse_first_stats(stats_path)
    fingerprint_lines = [
        line for line in log.splitlines() if line.startswith("CG_FINGERPRINT ")
    ]
    fingerprint = (
        parse_fingerprint(fingerprint_lines[0])
        if len(fingerprint_lines) == 1
        else {}
    )

    if exit_code != 0:
        errors.append(f"exit code {exit_code}")
    if log.count("ROI End!!!") != 1:
        errors.append(f"ROI End count {log.count('ROI End!!!')}")
    if log.count("Validation started") != 1:
        errors.append("missing unique Validation started marker")
    if log.count("Validation ended") != 1:
        errors.append("missing unique Validation ended marker")
    if log.count("m5_exit instruction encountered") != 1:
        errors.append("missing unique normal m5 exit")
    if len(fingerprint_lines) != 1:
        errors.append(f"fingerprint count {len(fingerprint_lines)}")
    if section_count < 2:
        errors.append(
            f"statistics section count {section_count}, expected >=2"
        )
    if not isinstance(metrics.get("simTicks"), int):
        errors.append("missing integer first-dump simTicks")
    if fingerprint.get("result") != "PASS":
        errors.append("fingerprint did not report PASS")
    if fingerprint.get("mode") != "MAA":
        errors.append("fingerprint mode is not MAA")
    if fingerprint.get("elements") != "150000":
        errors.append("fingerprint element count is not 150000")
    for key, expected in EXPECTED_FINGERPRINT.items():
        if fingerprint.get(key) != expected:
            errors.append(
                f"{key} mismatch: expected {expected}, "
                f"got {fingerprint.get(key)}"
            )
    for key in ("nonfinite_x", "nonfinite_z"):
        if fingerprint.get(key) != "0":
            errors.append(f"{key} is not zero")

    bad_markers = (
        "gem5 has encountered a segmentation fault",
        "panic:",
        "fatal:",
        "Assertion failed",
        "Aborted",
    )
    for marker in bad_markers:
        if marker.lower() in log.lower():
            errors.append(f"failure marker: {marker}")

    write_issues = metrics.get("system.maa.I0_IND_VirtWriteIssues", 0)
    write_completions = metrics.get(
        "system.maa.I0_IND_VirtWriteCompletions", 0
    )
    response_stalls = metrics.get(
        "system.maa.I0_IND_VirtResponseWordPoolStalls", 0
    )
    if run["case"] == "virtual":
        if write_issues != EXPECTED_VIRTUAL_WRITES:
            errors.append(
                f"virtual write issues {write_issues}, "
                f"expected {EXPECTED_VIRTUAL_WRITES}"
            )
        if write_completions != EXPECTED_VIRTUAL_WRITES:
            errors.append(
                f"virtual write completions {write_completions}, "
                f"expected {EXPECTED_VIRTUAL_WRITES}"
            )
        if response_stalls != 0:
            errors.append(f"response word-pool stalls {response_stalls}")

    started = outdir / "started_epoch"
    finished = outdir / "finished_epoch"
    wall_seconds = None
    if started.is_file() and finished.is_file():
        wall_seconds = read_int(finished) - read_int(started)

    selected_metrics = {
        key: value
        for key, value in metrics.items()
        if key in ("simTicks", "simInsts", "system.maa.cycles_TOTAL")
        or "IND_Virt" in key
    }
    result = {
        **run,
        "errors": errors,
        "exit_code": exit_code,
        "fingerprint": fingerprint,
        "first_dump_metrics": selected_metrics,
        "stats_sections": section_count,
        "valid": not errors,
        "wall_seconds": wall_seconds,
    }
    atomic_json(outdir / "validation.json", result)
    return result


def distribution(values):
    return {
        "count": len(values),
        "max": max(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "min": min(values),
        "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
    }


def summarize(args, runs):
    results = [validate_run(run) for run in runs]
    rows = []
    for result in results:
        metrics = result.get("first_dump_metrics", {})
        fingerprint = result.get("fingerprint", {})
        rows.append(
            {
                "run_id": result["run_id"],
                "case": result["case"],
                "replica": result["replica"],
                "valid": result["valid"],
                "exit_code": result.get("exit_code"),
                "simTicks": metrics.get("simTicks"),
                "simInsts": metrics.get("simInsts"),
                "maa_cycles": metrics.get("system.maa.cycles_TOTAL"),
                "virtual_write_issues": metrics.get(
                    "system.maa.I0_IND_VirtWriteIssues"
                ),
                "virtual_write_completions": metrics.get(
                    "system.maa.I0_IND_VirtWriteCompletions"
                ),
                "response_pool_stalls": metrics.get(
                    "system.maa.I0_IND_VirtResponseWordPoolStalls"
                ),
                "x_q5": fingerprint.get("x_q5"),
                "x_q6": fingerprint.get("x_q6"),
                "z_q5": fingerprint.get("z_q5"),
                "z_q6": fingerprint.get("z_q6"),
                "rnorm": fingerprint.get("rnorm"),
                "zeta": fingerprint.get("zeta"),
                "wall_seconds": result.get("wall_seconds"),
                "errors": "; ".join(result["errors"]),
            }
        )

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0]), delimiter="\t")
    writer.writeheader()
    writer.writerows(rows)
    atomic_write(args.campaign_root / "results.tsv", output.getvalue())

    valid = all(result["valid"] for result in results)
    summary = {
        "completed_at": utc_now(),
        "results": results,
        "status": "PASS" if valid else "FAIL",
    }
    if valid:
        control = [
            result["first_dump_metrics"]["simTicks"]
            for result in results
            if result["case"] == "control"
        ]
        virtual = [
            result["first_dump_metrics"]["simTicks"]
            for result in results
            if result["case"] == "virtual"
        ]
        control_mean = statistics.fmean(control)
        virtual_mean = statistics.fmean(virtual)
        speedup = control_mean / virtual_mean
        summary["performance"] = {
            "control_simTicks": distribution(control),
            "paired_speedups": [
                control[index] / virtual[index]
                for index in range(min(len(control), len(virtual)))
            ],
            "speedup_percent": (speedup - 1.0) * 100.0,
            "speedup_ratio": speedup,
            "tick_reduction_percent": (
                (control_mean - virtual_mean) / control_mean * 100.0
            ),
            "virtual_simTicks": distribution(virtual),
        }
    atomic_json(args.campaign_root / "terminal_summary.json", summary)
    atomic_write(
        args.campaign_root / "campaign_exit_code", "0\n" if valid else "1\n"
    )
    return valid, summary


def launch(args, manifest):
    processes = {}
    for run in manifest["runs"]:
        launcher = write_launcher(args, run)
        processes[run["run_id"]] = subprocess.Popen(
            [str(launcher)], cwd=Path(run["outdir"]), start_new_session=True
        )
        print(f"launched {run['run_id']} pid={processes[run['run_id']].pid}")
    sys.stdout.flush()

    last_report = 0.0
    while processes:
        for run_id, process in list(processes.items()):
            return_code = process.poll()
            if return_code is not None:
                print(f"finished {run_id} rc={return_code}")
                del processes[run_id]
        now = time.monotonic()
        if processes and now - last_report >= args.poll_seconds:
            print("active: " + ", ".join(sorted(processes)))
            last_report = now
        sys.stdout.flush()
        if processes:
            time.sleep(min(args.poll_seconds, 10))


def main():
    args = parse_args()
    identities = verify_artifacts(args)
    manifest = build_manifest(args, identities)
    if args.dry_run:
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0

    args.campaign_root.mkdir(parents=True, exist_ok=True)
    if not args.summarize_only:
        occupied = [
            run["outdir"]
            for run in manifest["runs"]
            if Path(run["outdir"]).exists()
        ]
        if occupied:
            raise RuntimeError(
                "refusing to overwrite existing run directories: "
                + ", ".join(occupied)
            )
        atomic_json(args.campaign_root / "campaign_manifest.json", manifest)
        launch(args, manifest)

    valid, summary = summarize(args, manifest["runs"])
    print(json.dumps(summary.get("performance", {}), indent=2, sort_keys=True))
    print(f"campaign {summary['status']}")
    return 0 if valid else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(2)
