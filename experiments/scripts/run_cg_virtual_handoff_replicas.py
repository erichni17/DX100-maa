#!/usr/bin/env python3
"""Run and validate the frozen CG virtual-handoff replica campaign."""

import argparse
import csv
import hashlib
import io
import json
import math
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
    "gem5": "f4e7491213bcfb2ede76be95f94d6483418e288ed1454556ffff3d24c6f9fe2e",
    "precomputed_header": "f2b18716e4a2356c597c95ee3583549def72700f2cb3294b0fcaacca46dbe131",
    "control_binary": "5bbcfcec1a1a7f47b31dbaa9a5e37a574a5b06d0545e12f58e5ec4e676da270e",
    "virtual_binary": "656dcfca21d91d22e7ced2a380575f81920fff31d675d2af7b7e534f0014cc2a",
    "control_m5_cpt": "abc78ad7ff5d6477c5e4c535552f03cb7c64518930855a608efeb6a414eef11d",
    "control_pmem": "10c44fd8a5a52984d5120fc514d26d4302672ca7639a3e0b8efb3eae37b9ac65",
    "virtual_m5_cpt": "70bd7d793c849c14dc3913cab7549ce496e06467cd86194d8c13b8e57bc6b2fe",
    "virtual_pmem": "8daf8846bd519e58494bc1e046bd1ee516dfae7a03162f71b05d0d18044d4622",
}
LEGACY_GEM5_SHA256 = (
    "15813d45877c7ca34b3b08944e9a6f61f177a4317542aa6b98f80857fec94e3d"
)

# The corrected native BASE/MAA gate established x_q5 as the exact semantic
# hash. Finer hashes are diagnostics because legal floating-point scheduling
# changes them.
EXPECTED_EXACT_FINGERPRINT = {
    "x_q5": "88c0975669c7062d",
}
EXPECTED_SCALARS = {
    "x_sum": (-385.9469780116342, 1.0e-7),
    "x_norm_sq": (0.99999999995060973, 1.0e-7),
    "z_sum": (-1793.1550141340122, 1.0e-6),
    "z_norm_sq": (21.586407955485896, 1.0e-6),
    "rnorm": (0.0010974915931001306, 0.01),
    "zeta": (109.99944232372989, 1.0e-10),
}

DEFAULT_GEOMETRY = {
    "combine_slots": 384,
    "combine_words": 4096,
    "response_slots": 96,
    "response_word_pool": 480,
    "combine_ways": 4,
    "combine_banks": 0,
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


def storage_accounting(geometry):
    core_bytes = (
        geometry["combine_slots"] * 72
        + geometry["response_slots"] * 8
        + geometry["response_word_pool"] * 8
        + geometry["max_outstanding_writes"] * 8
    )
    write_payload_bytes = geometry["max_outstanding_writes"] * 64
    return {
        "core_structure_bytes": core_bytes,
        "conservative_inflight_write_payload_bytes": write_payload_bytes,
        "conservative_total_bytes": core_bytes + write_payload_bytes,
    }


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
    parser.add_argument(
        "--cases",
        nargs="+",
        choices=("control", "virtual"),
        default=("control", "virtual"),
    )
    parser.add_argument("--campaign-root", type=Path, default=default_campaign)
    parser.add_argument("--control-checkpoint", type=Path)
    parser.add_argument(
        "--combine-slots",
        type=int,
        default=DEFAULT_GEOMETRY["combine_slots"],
    )
    parser.add_argument(
        "--combine-words",
        type=int,
        default=DEFAULT_GEOMETRY["combine_words"],
    )
    parser.add_argument(
        "--response-slots",
        type=int,
        default=DEFAULT_GEOMETRY["response_slots"],
    )
    parser.add_argument(
        "--response-word-pool",
        type=int,
        default=DEFAULT_GEOMETRY["response_word_pool"],
    )
    parser.add_argument(
        "--combine-ways",
        type=int,
        default=DEFAULT_GEOMETRY["combine_ways"],
    )
    parser.add_argument(
        "--combine-banks",
        type=int,
        default=DEFAULT_GEOMETRY["combine_banks"],
    )
    parser.add_argument(
        "--words-per-cycle",
        type=int,
        default=DEFAULT_GEOMETRY["words_per_cycle"],
    )
    parser.add_argument(
        "--max-outstanding-writes",
        type=int,
        default=DEFAULT_GEOMETRY["max_outstanding_writes"],
    )
    parser.add_argument(
        "--gem5",
        type=Path,
        default=repo
        / "build/X86/gem5.opt.virtual_banks_capped_f4e7491213bc",
    )
    parser.add_argument(
        "--expected-gem5-sha256",
        default=EXPECTED_SHA256["gem5"],
        help="Required SHA-256 for the selected simulator binary",
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
    if len(set(args.cases)) != len(args.cases):
        parser.error("--cases contains a duplicate")
    args.repo = repo
    args.config = repo / "configs/deprecated/example/se.py"
    args.control_binary = repo / "benchmarks/NAS/cg/cg_maa_16K_fp_frozen"
    args.virtual_binary = (
        repo / "benchmarks/NAS/cg/cg_maa_16K_virtual_fp_frozen"
    )
    if args.control_checkpoint is None:
        args.control_checkpoint = args.campaign_root / "control_fp_checkpoint"
    args.geometry = {
        "combine_slots": args.combine_slots,
        "combine_words": args.combine_words,
        "response_slots": args.response_slots,
        "response_word_pool": args.response_word_pool,
        "combine_ways": args.combine_ways,
        "combine_banks": args.combine_banks,
        "words_per_cycle": args.words_per_cycle,
        "max_outstanding_writes": args.max_outstanding_writes,
        "masked_writes": True,
    }
    for name, value in args.geometry.items():
        if name not in ("masked_writes", "combine_banks") and value <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.combine_banks < 0:
        parser.error("--combine-banks must be non-negative")
    if args.combine_slots % args.combine_ways != 0:
        parser.error("--combine-slots must be divisible by --combine-ways")
    combine_sets = args.combine_slots // args.combine_ways
    if args.combine_banks > combine_sets:
        parser.error("--combine-banks cannot exceed the number of sets")
    if len(args.expected_gem5_sha256) != 64 or any(
        character not in "0123456789abcdef"
        for character in args.expected_gem5_sha256
    ):
        parser.error("--expected-gem5-sha256 must be 64 lowercase hex digits")
    args.expected_sha256 = {
        **EXPECTED_SHA256,
        "gem5": args.expected_gem5_sha256,
    }
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
        expected = args.expected_sha256[name]
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


def verify_recorded_artifacts(manifest):
    identities = manifest.get("artifacts")
    if not isinstance(identities, dict):
        raise RuntimeError("campaign manifest has no artifact identities")
    expected_hashes = manifest.get(
        "expected_sha256",
        {**EXPECTED_SHA256, "gem5": LEGACY_GEM5_SHA256},
    )
    if set(expected_hashes) != set(EXPECTED_SHA256):
        raise RuntimeError(
            "campaign manifest has invalid expected SHA-256 keys"
        )
    for name, identity in identities.items():
        path = Path(identity["path"])
        if not path.is_file():
            raise RuntimeError(f"missing recorded {name}: {path}")
        actual = sha256_file(path)
        if actual != identity["sha256"]:
            raise RuntimeError(
                f"recorded {name} SHA-256 mismatch: "
                f"expected {identity['sha256']}, got {actual}"
            )
        if name in expected_hashes and actual != expected_hashes[name]:
            raise RuntimeError(f"{name} no longer matches frozen oracle")
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
        str(args.geometry["combine_slots"]),
        "--maa_virtual_combine_words",
        str(args.geometry["combine_words"]),
        "--maa_virtual_response_slots",
        str(args.geometry["response_slots"]),
        "--maa_virtual_response_word_pool",
        str(args.geometry["response_word_pool"]),
        "--maa_virtual_combine_ways",
        str(args.geometry["combine_ways"]),
        "--maa_virtual_combine_banks",
        str(args.geometry["combine_banks"]),
        "--maa_virtual_words_per_cycle",
        str(args.geometry["words_per_cycle"]),
        "--maa_virtual_max_outstanding_writes",
        str(args.geometry["max_outstanding_writes"]),
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
    for case in args.cases:
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
        "cases": list(args.cases),
        "expected_sha256": args.expected_sha256,
        "geometry": args.geometry,
        "host": os.uname().nodename,
        "replicas_per_case": args.replicas,
        "runs": runs,
        "source_commit": source_commit,
        "storage_accounting": storage_accounting(args.geometry),
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


def parse_instantiated_geometry(path, geometry):
    expected_keys = {
        "virtual_combine_slots": geometry["combine_slots"],
        "virtual_combine_words": geometry["combine_words"],
        "virtual_response_slots": geometry["response_slots"],
        "virtual_response_word_pool": geometry["response_word_pool"],
        "virtual_response_words": 0,
        "virtual_combine_ways": geometry["combine_ways"],
        "virtual_words_per_cycle": geometry["words_per_cycle"],
        "virtual_max_outstanding_writes": geometry["max_outstanding_writes"],
        "virtual_masked_writes": "true",
    }
    if "combine_banks" in geometry:
        expected_keys["virtual_combine_banks"] = geometry["combine_banks"]
    values = {}
    with path.open(errors="replace") as config:
        for line in config:
            key, separator, value = line.strip().partition("=")
            if separator and key in expected_keys:
                values[key] = parse_scalar(value)
    return expected_keys, values


def validate_run(run, geometry):
    outdir = Path(run["outdir"])
    errors = []
    log_path = outdir / "run.log"
    stats_path = outdir / "stats.txt"
    exit_path = outdir / "exit_code"
    config_path = outdir / "config.ini"
    run_manifest_path = outdir / "run_manifest.json"
    command_path = outdir / "command.txt"
    for path in (
        log_path,
        stats_path,
        exit_path,
        config_path,
        run_manifest_path,
        command_path,
    ):
        if not path.is_file():
            errors.append(f"missing {path.name}")
    if errors:
        return {**run, "errors": errors, "valid": False}

    exit_code = read_int(exit_path)
    log = log_path.read_text(errors="replace")
    metrics, section_count = parse_first_stats(stats_path)
    expected_geometry, instantiated_geometry = parse_instantiated_geometry(
        config_path, geometry
    )
    fingerprint_lines = [
        line for line in log.splitlines() if line.startswith("CG_FINGERPRINT ")
    ]
    fingerprint = (
        parse_fingerprint(fingerprint_lines[0])
        if len(fingerprint_lines) == 1
        else {}
    )

    recorded_run = json.loads(run_manifest_path.read_text())
    if recorded_run != run:
        errors.append("run_manifest.json does not match campaign manifest")
    expected_command = shlex.join(run["command"])
    if command_path.read_text().strip() != expected_command:
        errors.append("command.txt does not match campaign manifest")
    command_lines = [
        line.removeprefix("command line: ")
        for line in log.splitlines()
        if line.startswith("command line: ")
    ]
    if len(command_lines) != 1:
        errors.append(f"gem5 command-line count {len(command_lines)}")
    elif shlex.split(command_lines[0]) != run["command"]:
        errors.append("gem5 command line does not match campaign manifest")

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
    for key, expected in EXPECTED_EXACT_FINGERPRINT.items():
        if fingerprint.get(key) != expected:
            errors.append(
                f"{key} mismatch: expected {expected}, "
                f"got {fingerprint.get(key)}"
            )
    for key in ("nonfinite_x", "nonfinite_z"):
        if fingerprint.get(key) != "0":
            errors.append(f"{key} is not zero")
    for key, (expected, tolerance) in EXPECTED_SCALARS.items():
        try:
            actual = float(fingerprint[key])
        except (KeyError, ValueError):
            errors.append(f"missing numeric {key}")
            continue
        if not math.isclose(actual, expected, rel_tol=tolerance, abs_tol=0.0):
            errors.append(
                f"{key} mismatch: expected {expected} at rel_tol {tolerance}, "
                f"got {actual}"
            )
    for key, expected in expected_geometry.items():
        if instantiated_geometry.get(key) != expected:
            errors.append(
                f"{key} mismatch: expected {expected}, "
                f"got {instantiated_geometry.get(key)}"
            )

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
    response_high_water = metrics.get(
        "system.maa.I0_IND_VirtResponseWordHighWater", 0
    )
    combine_bank_accesses = metrics.get(
        "system.maa.I0_IND_VirtCombineBankAccesses", 0
    )
    combine_bank_conflicts = metrics.get(
        "system.maa.I0_IND_VirtCombineBankConflictCycles"
    )
    if run["case"] == "virtual":
        if not isinstance(write_issues, int) or write_issues <= 0:
            errors.append(f"invalid virtual write issue count {write_issues}")
        if write_completions != write_issues:
            errors.append(
                f"virtual write issue/completion mismatch: "
                f"{write_issues}/{write_completions}"
            )
        if (
            not isinstance(response_high_water, int)
            or response_high_water <= 0
        ):
            errors.append(
                f"inactive response word-pool high water {response_high_water}"
            )
        if geometry.get("combine_banks", 0) != 0:
            if (
                not isinstance(combine_bank_accesses, int)
                or combine_bank_accesses <= 0
            ):
                errors.append(
                    "inactive combine-bank access count "
                    f"{combine_bank_accesses}"
                )
            if (
                not isinstance(combine_bank_conflicts, int)
                or combine_bank_conflicts < 0
            ):
                errors.append(
                    "invalid combine-bank conflict count "
                    f"{combine_bank_conflicts}"
                )

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


def summarize(args, manifest):
    geometry = manifest["geometry"]
    results = [validate_run(run, geometry) for run in manifest["runs"]]
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
                "response_pool_high_water": metrics.get(
                    "system.maa.I0_IND_VirtResponseWordHighWater"
                ),
                "combine_bank_accesses": metrics.get(
                    "system.maa.I0_IND_VirtCombineBankAccesses"
                ),
                "combine_bank_conflict_cycles": metrics.get(
                    "system.maa.I0_IND_VirtCombineBankConflictCycles"
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
        "storage_accounting": storage_accounting(geometry),
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
        performance = {}
        if control:
            performance["control_simTicks"] = distribution(control)
        if virtual:
            performance["virtual_simTicks"] = distribution(virtual)
        if control and virtual:
            control_mean = statistics.fmean(control)
            virtual_mean = statistics.fmean(virtual)
            speedup = control_mean / virtual_mean
            performance.update(
                {
                    "paired_speedups": [
                        control[index] / virtual[index]
                        for index in range(min(len(control), len(virtual)))
                    ],
                    "speedup_percent": (speedup - 1.0) * 100.0,
                    "speedup_ratio": speedup,
                    "tick_reduction_percent": (
                        (control_mean - virtual_mean) / control_mean * 100.0
                    ),
                }
            )
        summary["performance"] = performance
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
    args.campaign_root.mkdir(parents=True, exist_ok=True)
    manifest_path = args.campaign_root / "campaign_manifest.json"
    if args.summarize_only:
        if not manifest_path.is_file():
            raise RuntimeError(
                f"missing frozen campaign manifest: {manifest_path}"
            )
        manifest = json.loads(manifest_path.read_text())
        verify_recorded_artifacts(manifest)
    else:
        identities = verify_artifacts(args)
        manifest = build_manifest(args, identities)
    if args.dry_run:
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0

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
        atomic_json(manifest_path, manifest)
        launch(args, manifest)

    valid, summary = summarize(args, manifest)
    print(json.dumps(summary.get("performance", {}), indent=2, sort_keys=True))
    print(f"campaign {summary['status']}")
    return 0 if valid else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(2)
