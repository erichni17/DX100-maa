#!/usr/bin/env python3
"""Build, run, and fail-closed validate the dual-ABI UMT poison-tail smoke."""

import argparse
import json
import pathlib
import shutil
import subprocess

from umt_factorial_evidence import (
    FACTORIAL_HARNESS_PATHS,
    cell_from_document,
    sha256,
    static_cell_stats,
    validate_build_manifest_document,
    validate_build_manifest_files,
    validate_dual_issue,
    validate_repository_boundary,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
D64_GROUP_COUNTS = (1, 7, 8, 9, 31, 32, 33, 63, 64)
D32_GROUP_COUNTS = (1, 7, 8, 9, 16, 24, 31, 32)
LINE_READ_CONTRACT_SCHEMA = "lanl-maa-umt-poison-line-contract-v1"
REPORT_SCHEMA = "lanl-maa-umt64-poison-tail-v2"


def write_json(path, document):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def read_json_object(path, label):
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read {label}: {path}") from error
    if not isinstance(document, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return document


def validate_group_request(group_counts, abi_version, calibration):
    if not group_counts or len(set(group_counts)) != len(group_counts):
        raise RuntimeError("group counts are empty or duplicated")
    allowed = D32_GROUP_COUNTS if abi_version == 4 else D64_GROUP_COUNTS
    if not set(group_counts).issubset(allowed):
        raise RuntimeError("group count is outside the poison-tail gate")
    if len(group_counts) != 1 and tuple(group_counts) != allowed:
        raise RuntimeError(
            "only one cold case or the exact ABI matrix is valid"
        )
    if calibration and abi_version != 4:
        raise RuntimeError("line-read calibration is defined only for D32")


def validation_disposition(calibration, group_counts=None, abi_version=None):
    if calibration:
        return {
            "status": "calibration_only",
            "validation_mode": "d32_line_read_calibration",
            "prerequisite_gate_passed": False,
            "application_performance_promotion_eligible": False,
        }
    full_matrix = tuple(group_counts or ()) == (
        D32_GROUP_COUNTS if abi_version == 4 else D64_GROUP_COUNTS
    )
    return {
        "status": (
            "prerequisite_passed" if full_matrix else "diagnostic_passed"
        ),
        "validation_mode": "confirmation",
        "prerequisite_gate_passed": full_matrix,
        "application_performance_promotion_eligible": False,
    }


def line_read_contract_candidate(
    stats, build_manifest_sha256, cell, group_counts
):
    observed = stats.get("descriptorUmtInputLineReads")
    minimum = 16 * sum((group + 7) // 8 for group in group_counts)
    maximum = 16 * sum(group_counts)
    if type(observed) is not int or not minimum <= observed <= maximum:
        raise RuntimeError(
            "D32 line-read calibration is outside physical bounds: "
            f"observed={observed} bounds=[{minimum}, {maximum}]"
        )
    return {
        "schema": LINE_READ_CONTRACT_SCHEMA,
        "build_manifest_sha256": build_manifest_sha256,
        "cell": cell.document(),
        "abi_version": 4,
        "group_counts": list(group_counts),
        "descriptorUmtInputLineReads": observed,
    }


def validate_line_read_contract(
    document, build_manifest_sha256, cell, group_counts
):
    expected_keys = {
        "schema",
        "build_manifest_sha256",
        "cell",
        "abi_version",
        "group_counts",
        "descriptorUmtInputLineReads",
    }
    if not isinstance(document, dict) or set(document) != expected_keys:
        raise RuntimeError(
            "D32 line-read contract has missing or unknown fields"
        )
    if document["schema"] != LINE_READ_CONTRACT_SCHEMA:
        raise RuntimeError("D32 line-read contract schema changed")
    if document["build_manifest_sha256"] != build_manifest_sha256:
        raise RuntimeError(
            "D32 line-read contract does not bind build manifest"
        )
    if cell_from_document(document["cell"]) != cell:
        raise RuntimeError("D32 line-read contract cell mismatches build")
    if document["abi_version"] != 4 or document["group_counts"] != list(
        group_counts
    ):
        raise RuntimeError("D32 line-read contract workload mismatches run")
    value = document["descriptorUmtInputLineReads"]
    minimum = 16 * sum((group + 7) // 8 for group in group_counts)
    maximum = 16 * sum(group_counts)
    if type(value) is not int or not minimum <= value <= maximum:
        raise RuntimeError("D32 line-read contract value is outside bounds")
    return value


def read_stats(path):
    stats = {}
    prefix = "system.lanl_maa."
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if line.startswith(prefix) and len(fields) >= 2:
            try:
                stats[fields[0][len(prefix) :]] = int(fields[1])
            except ValueError:
                pass
    return stats


def compile_guest(source, binary, group_counts, abi_version):
    compiler = shutil.which("cc")
    if not compiler:
        raise RuntimeError("UMT64 poison-tail smoke requires cc")
    command = [
        compiler,
        "-std=c11",
        "-O2",
        "-ffp-contract=off",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-nostdlib",
        "-static",
        "-fno-pie",
        "-no-pie",
        "-fno-stack-protector",
        "-fno-builtin",
        "-Wl,--build-id=none",
        "-Wl,-e,_start",
    ]
    command.append(f"-DUMT_ABI_VERSION={abi_version}")
    if len(group_counts) == 1:
        command.append(f"-DONLY_GROUP_COUNT={group_counts[0]}")
    elif tuple(group_counts) != (
        D32_GROUP_COUNTS if abi_version == 4 else D64_GROUP_COUNTS
    ):
        raise RuntimeError(
            "only one cold case or the exact ABI matrix is valid"
        )
    command.extend([str(source), "-o", str(binary)])
    subprocess.run(command, check=True)


def expected_stats(group_counts, abi_version, cell, input_line_reads=None):
    groups = sum(group_counts)
    packet_multipliers = sum((group + 7) // 8 for group in group_counts)
    if abi_version == 5:
        input_line_reads = 16 * packet_multipliers
    expected = {
        "descriptorDoorbells": len(group_counts),
        "descriptorRearms": len(group_counts) - 1,
        "descriptorFetches": 4 * len(group_counts),
        "descriptorResultWrites": 8 * groups,
        "descriptorUmtResultLineWrites": 8 * packet_multipliers,
        "descriptorUmtD32Descriptors": (
            len(group_counts) if abi_version == 4 else 0
        ),
        "descriptorUmtD64Descriptors": (
            len(group_counts) if abi_version == 5 else 0
        ),
        "descriptorCompletionWrites": len(group_counts),
        "descriptorErrors": 0,
        "descriptorUmtGroupsLoaded": groups,
        "descriptorUmtInputReads": 16 * groups,
        "descriptorUmtStateInputWrites": 8 * groups,
        "descriptorUmtStateDenominatorsConsumed": 8 * groups,
        "descriptorUmtStateResultWrites": 8 * groups,
        "descriptorUmtStateResultReads": 8 * groups,
        "descriptorUmtStateCapacityErrors": 0,
        "descriptorUmtStateStoreHighWaterMark": max(group_counts),
        "descriptorUmtStateBankHighWaterMark": (max(group_counts) + 3) // 4,
        "descriptorUmtStateTokenHighWaterMark": min(
            cell.compute_tokens, max(group_counts)
        ),
        "descriptorUmtFp64AddSubOperations": 8 * groups,
        "descriptorUmtFp64MultiplyOperations": 0,
        "descriptorUmtFp64DivideOperations": 8 * groups,
        "descriptorUmtStateFpOperationsIssued": 16 * groups,
        "descriptorUmtBatches": len(group_counts),
        "descriptorUmtResultsComputed": 8 * groups,
        "activeContextHighWaterMark": max(group_counts),
        "operationTableHighWaterMark": max(group_counts),
        "lineWouldBlockCycles": 0,
        **static_cell_stats(cell),
    }
    if input_line_reads is not None:
        expected["descriptorUmtInputLineReads"] = input_line_reads
    return expected


def validate(
    stats,
    group_counts,
    abi_version,
    cell,
    input_line_reads=None,
    calibration=False,
):
    groups = sum(group_counts)
    if abi_version == 4 and not calibration and input_line_reads is None:
        raise RuntimeError("D32 confirmation requires a line-read contract")
    expected = expected_stats(
        group_counts, abi_version, cell, input_line_reads
    )
    failures = {
        name: {"expected": value, "observed": stats.get(name)}
        for name, value in expected.items()
        if stats.get(name) != value
    }
    if failures:
        raise RuntimeError(f"UMT64 poison-tail stat mismatch: {failures}")
    line_high_water = stats.get("lineTableHighWaterMark")
    if line_high_water is None or not 0 < line_high_water <= 32:
        raise RuntimeError(
            "UMT64 poison-tail line-table high-water is absent or outside "
            f"the 32-entry capacity: {line_high_water}"
        )
    token_high_water = stats["descriptorUmtStateTokenHighWaterMark"]
    bounded = {
        "lineTableHighWaterMark": line_high_water,
        "descriptorUmtStateTokenHighWaterMark": token_high_water,
    }
    if calibration:
        candidate = line_read_contract_candidate(
            stats, "0" * 64, cell, group_counts
        )
        bounded["descriptorUmtInputLineReads"] = candidate[
            "descriptorUmtInputLineReads"
        ]
        bounded["minimumDescriptorUmtInputLineReads"] = 16 * sum(
            (group + 7) // 8 for group in group_counts
        )
        bounded["maximumDescriptorUmtInputLineReads"] = 16 * groups
    for name in ("controlReadRequests", "controlStatusReads"):
        value = stats.get(name)
        if value is None or value <= 0:
            raise RuntimeError(
                f"UMT64 poison-tail control-read counter is absent or zero: "
                f"{name}={value}"
            )
        bounded[name] = value
    for name in ("controlOpcodeReads", "controlErrorReads"):
        value = stats.get(name)
        if value is None or value < 0:
            raise RuntimeError(
                "UMT64 poison-tail optional control-read counter is absent "
                f"or negative: {name}={value}"
            )
        bounded[name] = value
    if bounded["controlReadRequests"] < bounded["controlStatusReads"]:
        raise RuntimeError(
            "UMT64 poison-tail status reads exceed total control reads: "
            f"{bounded}"
        )
    batch_cycles = stats.get("descriptorUmtBatchCycles")
    if batch_cycles is None or batch_cycles <= len(group_counts):
        raise RuntimeError(
            "UMT measured pipeline cycles are absent or still a per-batch "
            f"placeholder: {batch_cycles}"
        )
    bounded["descriptorUmtBatchCycles"] = batch_cycles
    input_hold_cycles = stats.get("descriptorUmtInputLineWaiterHoldLineCycles")
    if abi_version == 4:
        if input_hold_cycles != 0:
            raise RuntimeError(
                "D32 unexpectedly used complete-line hold: "
                f"{input_hold_cycles}"
            )
    elif len(group_counts) > 1 and (
        input_hold_cycles is None or input_hold_cycles <= 0
    ):
        raise RuntimeError("D64 full matrix did not exercise line holding")
    bounded["descriptorUmtInputLineWaiterHoldLineCycles"] = input_hold_cycles
    bounded["dual_issue"] = validate_dual_issue(
        stats, cell, require_exercised=(groups > 1)
    )
    bank_conflicts = stats.get("descriptorUmtStateBankReadConflictCycles")
    writeback_stalls = stats.get("descriptorUmtStateWritebackStallCycles")
    combined_stalls = stats.get(
        "descriptorUmtStatePipelineResultBankStallCycles"
    )
    if (
        type(bank_conflicts) is not int
        or type(writeback_stalls) is not int
        or type(combined_stalls) is not int
        or bank_conflicts < 0
        or writeback_stalls < 0
        or bank_conflicts > combined_stalls
        or writeback_stalls > combined_stalls
        or combined_stalls > bank_conflicts + writeback_stalls
    ):
        raise RuntimeError(
            "UMT poison-tail split result-bank accounting did not close: "
            f"bank={bank_conflicts}, writeback={writeback_stalls}, "
            f"combined={combined_stalls}"
        )
    divider_no_lane = stats.get("descriptorUmtStateDividerNoLaneCycles")
    if (
        type(divider_no_lane) is not int
        or divider_no_lane < 0
        or divider_no_lane > batch_cycles
    ):
        raise RuntimeError(
            "UMT poison-tail divider-no-lane cycles exceed active cycles: "
            f"divider={divider_no_lane}, active={batch_cycles}"
        )
    bounded.update(
        {
            "descriptorUmtStateBankReadConflictCycles": bank_conflicts,
            "descriptorUmtStateWritebackStallCycles": writeback_stalls,
            "descriptorUmtStatePipelineResultBankStallCycles": (
                combined_stalls
            ),
            "descriptorUmtStateDividerNoLaneCycles": divider_no_lane,
        }
    )
    if len(group_counts) > 1:
        for name in (
            "descriptorUmtStateTokenBackpressureEvents",
            "descriptorUmtStateFpIssueStallCycles",
            "descriptorUmtStateInputBankWaitCycles",
            "descriptorUmtStatePipelineResultBankStallCycles",
            "descriptorUmtStateResultDrainBankWaitCycles",
        ):
            value = stats.get(name)
            if value is None or value <= 0:
                raise RuntimeError(
                    "UMT full matrix did not exercise required pressure: "
                    f"{name}={value}"
                )
            bounded[name] = value
    return expected, bounded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument("--source", required=True, type=pathlib.Path)
    parser.add_argument("--output-root", required=True, type=pathlib.Path)
    parser.add_argument("--build-manifest", required=True, type=pathlib.Path)
    parser.add_argument("--abi-version", type=int, choices=(4, 5), default=5)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--line-read-contract", type=pathlib.Path)
    mode.add_argument("--calibrate-line-read-contract", action="store_true")
    parser.add_argument(
        "--group-counts",
        default=",".join(map(str, D64_GROUP_COUNTS)),
        help="exact full matrix or one cold diagnostic group count",
    )
    args = parser.parse_args()

    try:
        group_counts = [int(value) for value in args.group_counts.split(",")]
    except ValueError as error:
        raise RuntimeError("invalid group-count list") from error
    validate_group_request(
        group_counts, args.abi_version, args.calibrate_line_read_contract
    )
    if args.abi_version == 5 and args.line_read_contract is not None:
        raise RuntimeError("D64 does not accept a D32 line-read contract")
    if (
        args.abi_version == 4
        and not args.calibrate_line_read_contract
        and args.line_read_contract is None
    ):
        raise RuntimeError(
            "D32 confirmation requires an independent line-read contract"
        )

    if args.output_root.exists():
        raise RuntimeError(f"refusing to overwrite {args.output_root}")
    actual_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    source = args.source.resolve()
    config = args.config.resolve()
    gem5 = args.gem5.resolve()
    expected_source = (
        ROOT / "benchmarks/LANL/umt_ordered_wave_poison_tail_smoke.c"
    ).resolve()
    expected_config = (
        ROOT / "tests/lanl_maa/umt_ordered_wave_poison_tail_smoke.py"
    ).resolve()
    if source != expected_source or config != expected_config:
        raise RuntimeError("poison-tail source or config path changed")

    build_manifest_path = args.build_manifest.resolve()
    build_manifest = read_json_object(build_manifest_path, "build manifest")
    cell = validate_build_manifest_document(build_manifest)
    file_cell, source_root = validate_build_manifest_files(
        build_manifest, build_manifest_path, gem5
    )
    if file_cell != cell:
        raise RuntimeError("build manifest cell validation disagrees")
    validate_repository_boundary(
        build_manifest, source_root, ROOT, FACTORIAL_HARNESS_PATHS
    )
    build_manifest_sha256 = sha256(build_manifest_path)

    line_contract = None
    line_contract_path = None
    line_contract_sha256 = None
    input_line_reads = None
    if args.line_read_contract is not None:
        line_contract_path = args.line_read_contract.resolve()
        line_contract = read_json_object(
            line_contract_path, "D32 line-read contract"
        )
        line_contract_sha256 = sha256(line_contract_path)
        input_line_reads = validate_line_read_contract(
            line_contract, build_manifest_sha256, cell, group_counts
        )

    args.output_root.mkdir(parents=False)
    binary = args.output_root / "umt64_poison_tail.elf"
    compile_guest(source, binary, group_counts, args.abi_version)
    metadata = {
        "schema": REPORT_SCHEMA,
        "group_counts": group_counts,
        "abi_version": args.abi_version,
        "validation_mode": (
            "d32_line_read_calibration"
            if args.calibrate_line_read_contract
            else "confirmation"
        ),
        "sum_groups": sum(group_counts),
        "inactive_record_bits": "0x7ff0000000000001",
        "inactive_result_bits": "0xdeadbeefcafef00d",
        "build_manifest": build_manifest,
        "build_manifest_sha256": build_manifest_sha256,
        "cell": cell.document(),
        "line_read_contract_sha256": line_contract_sha256,
        "simulator_commit": build_manifest["source_commit"],
        "harness_commit": actual_commit,
        "gem5_sha256": sha256(gem5),
        "config_sha256": sha256(config),
        "source_sha256": sha256(source),
        "guest_sha256": sha256(binary),
    }
    metadata_path = args.output_root / "metadata.json"
    write_json(metadata_path, metadata)
    outdir = args.output_root / "m5out"
    command = [
        str(gem5),
        f"--outdir={outdir}",
        str(config),
        f"--binary={binary}",
        f"--metadata={metadata_path}",
    ]
    completed = subprocess.run(command, text=True, capture_output=True)
    (args.output_root / "stdout.log").write_text(
        completed.stdout, encoding="utf-8"
    )
    (args.output_root / "stderr.log").write_text(
        completed.stderr, encoding="utf-8"
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"gem5 failed with {completed.returncode}; see {args.output_root}"
        )
    if "LANLMAA_UMT64_POISON_TAIL_TERMINAL code=0" not in completed.stdout:
        raise RuntimeError("gem5 output lacks the exact poison-tail terminal")
    stats_path = outdir / "stats.txt"
    stats = read_stats(stats_path)
    expected_stats, bounded_stats = validate(
        stats,
        group_counts,
        args.abi_version,
        cell,
        input_line_reads=input_line_reads,
        calibration=args.calibrate_line_read_contract,
    )
    candidate = None
    candidate_path = None
    candidate_sha256 = None
    if args.calibrate_line_read_contract:
        candidate = line_read_contract_candidate(
            stats, build_manifest_sha256, cell, group_counts
        )
        candidate_path = args.output_root / "line-read-contract.candidate.json"
        write_json(candidate_path, candidate)
        candidate_sha256 = sha256(candidate_path)
    disposition = validation_disposition(
        args.calibrate_line_read_contract, group_counts, args.abi_version
    )
    report = {
        **metadata,
        **disposition,
        "terminal": True,
        "returncode": completed.returncode,
        "command": command,
        "expected_stats": expected_stats,
        "bounded_stats": bounded_stats,
        "line_read_contract_path": (
            str(line_contract_path) if line_contract_path else None
        ),
        "line_read_contract": line_contract,
        "candidate_line_read_contract_path": (
            str(candidate_path) if candidate_path else None
        ),
        "candidate_line_read_contract_sha256": candidate_sha256,
        "stats_sha256": sha256(stats_path),
        "stdout_sha256": sha256(args.output_root / "stdout.log"),
        "stderr_sha256": sha256(args.output_root / "stderr.log"),
        "calibration_boundary": (
            "D32 calibration measures bounded physical line traffic and emits "
            "a build-manifest- and cell-bound candidate, but never passes the "
            "prerequisite. Confirmation requires that external predeclared "
            "candidate. D64 line traffic is derived from complete-line ABI "
            "packets and needs no calibrated traffic oracle."
        ),
        "claim_boundary": (
            "Cell-bound live dual-ABI functional, bounded-store, token, "
            "issue, "
            "selector/route, cost-floor, and stall-counter evidence; no "
            "application-speedup, energy, RTL, or physical-design claim."
        ),
    }
    report_path = args.output_root / "report.json"
    write_json(report_path, report)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
