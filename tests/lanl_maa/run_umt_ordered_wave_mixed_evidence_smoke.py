#!/usr/bin/env python3
"""Build, run, and audit the mixed-ABI dense/error UMT evidence guest."""

import argparse
import hashlib
import json
import pathlib
import re
import shutil
import struct
import subprocess
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[2]
BAD_RECORD_VALUE = 18
POISON_BITS = 0x7FF0000000000001
RESULT_SENTINEL_BITS = 0xDEADBEEFCAFEF00D
CORNERS = 8
INPUT_PLANES = 16
EDGES = (
    (0, 1, 0.5),
    (0, 2, -0.25),
    (0, 4, 0.125),
    (1, 2, 0.75),
    (1, 3, -0.5),
    (2, 3, 0.25),
    (2, 5, 0.125),
    (3, 4, -0.75),
    (3, 6, 0.5),
    (4, 5, 0.25),
    (5, 6, -0.125),
    (6, 7, 0.5),
)


@dataclass(frozen=True)
class EvidenceCase:
    abi_version: int
    groups: int
    expect_error: bool = False


CASES = (
    EvidenceCase(4, 32),
    EvidenceCase(5, 64),
    EvidenceCase(4, 9),
    EvidenceCase(5, 33),
    EvidenceCase(4, 8, True),
)
SUCCESS_CASES = tuple(case for case in CASES if not case.expect_error)
BUILD_MANIFEST_SCHEMA = "lanl-maa-reproducible-gem5-build-v1"
TIMING_CONTRACT_SCHEMA = "lanl-maa-umt-ordered-wave-timing-contract-v2"
EVIDENCE_REPORT_SCHEMA = "lanl-maa-umt-ordered-wave-mixed-evidence-v2"
GUEST_COMPILE_FLAGS = (
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
)

# These cannot be derived from the fixed work matrix alone. Confirmation still
# requires exact integers from an external predeclared timing contract.
TIMING_COUNTER_REASONS = {
    "descriptorUmtBatchCycles": (
        "pipeline-active cycles depend on timing response and token issue "
        "placement; the fixed FP operation counts are exact instead"
    ),
    "descriptorUmtStateTokenBackpressureEvents": (
        "retry opportunities depend on line arrival and token retirement timing"
    ),
    "descriptorUmtStateFpIssueStallCycles": (
        "stall cycles depend on cycle-by-cycle token arbitration"
    ),
    "descriptorUmtStateInputBankWaitCycles": (
        "input bank conflicts depend on response arrival cycles"
    ),
    "descriptorUmtStatePipelineResultBankStallCycles": (
        "pipeline result conflicts depend on FP completion and bank arbitration"
    ),
    "descriptorUmtStateResultDrainBankWaitCycles": (
        "result drain waits depend on packet formation and bank arbitration"
    ),
    "descriptorUmtInputLineWaiterHoldLineCycles": (
        "D64 complete-line hold duration depends on waiter allocation timing"
    ),
    "lineTableHighWaterMark": (
        "line occupancy depends on cache response and allocation overlap"
    ),
    "controlStatusReads": (
        "guest polling iterations depend on simulated completion latency"
    ),
    "controlReadRequests": (
        "total control reads inherit the simulated status-poll count"
    ),
    "portSendFailures": (
        "downstream timing-request refusal count depends on memory-system "
        "contention"
    ),
    "portRetryNotifications": (
        "retry notification count depends on downstream request availability"
    ),
    "retryPacketResubmissions": (
        "retained-packet resubmission count depends on timing-request refusals"
    ),
    "retryPacketAcceptances": (
        "accepted retained-packet count depends on how many retry episodes "
        "occur"
    ),
}
PACKET_RETRY_COUNTERS = (
    "portSendFailures",
    "portRetryNotifications",
    "retryPacketResubmissions",
    "retryPacketAcceptances",
)
PACKET_RETRY_RELATIONSHIPS = (
    "portSendFailures == portRetryNotifications == "
    "retryPacketResubmissions",
    "0 <= retryPacketAcceptances <= retryPacketResubmissions",
    "(retryPacketAcceptances == 0) == (retryPacketResubmissions == 0)",
)

BUILD_MANIFEST_KEYS = {
    "schema",
    "status",
    "source_commit",
    "source_tree",
    "source_clean_before_and_after",
    "source_identity_unchanged",
    "command",
    "returncode",
    "started_at",
    "ended_at",
    "required_relink_observed",
    "target",
    "target_size",
    "target_mtime_ns",
    "gem5_sha256",
    "frozen_gem5",
    "frozen_gem5_sha256",
    "stdout_sha256",
    "stderr_sha256",
    "builder_sha256",
    "claim_boundary",
}


def sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def is_sha256(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)


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


def dense_index(source, destination):
    if not 0 <= source < destination < CORNERS:
        raise ValueError("ordered-wave edges must be forward and in range")
    return source * (2 * CORNERS - source - 1) // 2 + destination - source - 1


def edge_mask():
    mask = 0
    for source, destination, _coefficient in EDGES:
        mask |= 1 << dense_index(source, destination)
    return mask


def input_source(case_index, group, corner):
    return 16.0 + 4.0 * case_index + 2.0 * group + corner


def input_sigt_volume(corner):
    return 3.0 if corner % 2 == 0 else 2.0


def sum_area(corner):
    return 1.0 if corner % 2 == 0 else 2.0


def scalar_oracle(case_index, group):
    """Independent source-ordered scalar oracle for one active group."""
    source = [input_source(case_index, group, corner) for corner in range(8)]
    coefficients = {
        (edge_source, destination): coefficient
        for edge_source, destination, coefficient in EDGES
    }
    result = []
    for corner in range(CORNERS):
        flux = source[corner] / (sum_area(corner) + input_sigt_volume(corner))
        result.append(flux)
        for destination in range(corner + 1, CORNERS):
            coefficient = coefficients.get((corner, destination), 0.0)
            if coefficient:
                source[destination] += coefficient * flux
    return tuple(result)


def double_bits(value):
    return struct.unpack("<Q", struct.pack("<d", value))[0]


def oracle_sha256(case_index, groups):
    digest = hashlib.sha256()
    # Result memory is plane-major, so fingerprint in the same order.
    results = [scalar_oracle(case_index, group) for group in range(groups)]
    for corner in range(CORNERS):
        for group in range(groups):
            digest.update(
                struct.pack("<Q", double_bits(results[group][corner]))
            )
    return digest.hexdigest()


def exact_stats():
    success_groups = sum(case.groups for case in SUCCESS_CASES)
    success_line_packets = sum(
        (case.groups + 7) // 8 for case in SUCCESS_CASES
    )
    return {
        # Five submissions, with each post-first doorbell rearming a terminal.
        "descriptorDoorbells": len(CASES),
        "descriptorBusyRejections": 0,
        "descriptorRearms": len(CASES) - 1,
        "descriptorFetches": 4 * len(CASES),
        # Only the four successful descriptors publish results/completions.
        "descriptorResultWrites": CORNERS * success_groups,
        "descriptorUmtResultLineWrites": CORNERS * success_line_packets,
        "descriptorCompletionWrites": len(SUCCESS_CASES),
        "descriptorErrors": 1,
        "descriptorUmtD32Descriptors": sum(
            case.abi_version == 4 for case in CASES
        ),
        "descriptorUmtD64Descriptors": sum(
            case.abi_version == 5 for case in CASES
        ),
        # The error case accepts all source planes and seven denominator
        # tokens before active denominator plane zero, group seven poisons the
        # retained-work drain.
        "descriptorUmtGroupsLoaded": success_groups,
        "descriptorUmtInputReads": INPUT_PLANES * success_groups + 72,
        "descriptorUmtInputLineReads": (
            INPUT_PLANES * success_line_packets + 9
        ),
        "descriptorUmtStateInputWrites": CORNERS * success_groups + 64,
        "descriptorUmtStateDenominatorsConsumed": (
            CORNERS * success_groups + 7
        ),
        "descriptorUmtStateResultWrites": CORNERS * success_groups,
        "descriptorUmtStateResultReads": CORNERS * success_groups,
        "descriptorUmtStateCapacityErrors": 0,
        # Eight denominator adds plus one RMW add and multiply per live edge.
        "descriptorUmtFp64AddSubOperations": (
            (CORNERS + len(EDGES)) * success_groups
        ),
        "descriptorUmtFp64MultiplyOperations": len(EDGES) * success_groups,
        "descriptorUmtFp64DivideOperations": CORNERS * success_groups,
        "descriptorUmtBatches": len(SUCCESS_CASES),
        "descriptorUmtResultsComputed": CORNERS * success_groups,
        # Fixed capacity, occupancy, and explicitly-labeled cost floors.
        "descriptorUmtStateStoreHighWaterMark": 64,
        "descriptorUmtStateBankHighWaterMark": 16,
        "descriptorUmtStateTokenHighWaterMark": 16,
        "descriptorUmtStateAllocatedStoreBytes": 4608,
        "descriptorUmtStatePhysicalStoreBytes": 5120,
        "descriptorUmtStateResidualStoreBytes": 512,
        "descriptorUmtStateTokenLogicalBitsFloor": 7536,
        "descriptorUmtStateFunctionalControlLogicalBitsFloor": 657,
        "descriptorUmtStateBankSchedulerLogicalBitsFloor": 283,
        "descriptorUmtStateInstrumentationLogicalBitsFloor": 978,
        "descriptorUmtStateAuxiliaryLogicalBitsFloor": 16990,
        "descriptorUmtStatePhysicalStorePlusLogicalAuxiliaryBitsFloor": 57950,
        "activeContextHighWaterMark": 64,
        "operationTableHighWaterMark": 64,
        "lineWouldBlockCycles": 0,
        # The guest performs exactly one error-register read and no opcode read.
        "controlErrorReads": 1,
        "controlOpcodeReads": 0,
    }


def validate_exact_stats(stats):
    expected = exact_stats()
    failures = {
        name: {"expected": value, "observed": stats.get(name)}
        for name, value in expected.items()
        if stats.get(name) != value
    }
    if failures:
        raise RuntimeError(f"mixed UMT exact stat mismatch: {failures}")
    return expected


def validate_packet_retry_counters(stats):
    observed = {}
    for name in PACKET_RETRY_COUNTERS:
        value = stats.get(name)
        if type(value) is not int or value < 0:
            raise RuntimeError(
                "mixed UMT packet retry counter is absent, noninteger, or "
                f"negative: {name}={value}"
            )
        observed[name] = value

    failures = observed["portSendFailures"]
    notifications = observed["portRetryNotifications"]
    resubmissions = observed["retryPacketResubmissions"]
    acceptances = observed["retryPacketAcceptances"]
    if failures != notifications or notifications != resubmissions:
        raise RuntimeError(
            "mixed UMT retry obligation accounting did not close: "
            f"{observed}"
        )
    if acceptances > resubmissions:
        raise RuntimeError(
            "mixed UMT retry acceptance exceeds resubmissions: " f"{observed}"
        )
    if (acceptances == 0) != (resubmissions == 0):
        raise RuntimeError(
            "mixed UMT terminal retry acceptance accounting did not close: "
            f"{observed}"
        )
    return observed


def observed_timing_counters(stats):
    observed = {}
    for name in TIMING_COUNTER_REASONS:
        value = stats.get(name)
        if type(value) is not int:
            raise RuntimeError(
                f"mixed UMT timing counter is absent or noninteger: "
                f"{name}={value}"
            )
        observed[name] = value

    retained_work = (
        set(TIMING_COUNTER_REASONS)
        - {
            "lineTableHighWaterMark",
            "controlStatusReads",
            "controlReadRequests",
        }
        - set(PACKET_RETRY_COUNTERS)
    )
    missing_work = {
        name: observed[name] for name in retained_work if observed[name] <= 0
    }
    if missing_work:
        raise RuntimeError(
            "mixed UMT timing evidence lacks retained pipeline/stall work: "
            f"{missing_work}"
        )
    if not 0 < observed["lineTableHighWaterMark"] <= 32:
        raise RuntimeError(
            "mixed UMT line occupancy is outside fixed capacity: "
            f"{observed['lineTableHighWaterMark']}"
        )
    if observed["controlStatusReads"] <= 0:
        raise RuntimeError("mixed UMT timing evidence lacks status polling")
    if observed["controlReadRequests"] != observed["controlStatusReads"] + 1:
        raise RuntimeError(
            "mixed UMT control-read accounting did not close: " f"{observed}"
        )
    validate_packet_retry_counters(observed)
    return observed


def validate_calibration(stats):
    return validate_exact_stats(stats), observed_timing_counters(stats)


def validate_timing_contract(document, build_manifest_sha256):
    if set(document) != {"schema", "build_manifest_sha256", "counters"}:
        raise RuntimeError("timing contract has missing or unknown fields")
    if document["schema"] != TIMING_CONTRACT_SCHEMA:
        raise RuntimeError("timing contract schema changed")
    if document["build_manifest_sha256"] != build_manifest_sha256:
        raise RuntimeError("timing contract does not bind the build manifest")
    counters = document["counters"]
    if not isinstance(counters, dict) or set(counters) != set(
        TIMING_COUNTER_REASONS
    ):
        raise RuntimeError("timing contract counter set changed")
    if any(type(value) is not int for value in counters.values()):
        raise RuntimeError("timing contract counters must be exact integers")
    # Apply the evidence and capacity invariants to the predeclared values too.
    observed_timing_counters(counters)
    return counters


def validate_confirmation(stats, timing_contract, build_manifest_sha256):
    expected = validate_exact_stats(stats)
    timing_expected = validate_timing_contract(
        timing_contract, build_manifest_sha256
    )
    timing_observed = observed_timing_counters(stats)
    failures = {
        name: {"expected": timing_expected[name], "observed": value}
        for name, value in timing_observed.items()
        if value != timing_expected[name]
    }
    if failures:
        raise RuntimeError(f"mixed UMT exact timing stat mismatch: {failures}")
    exact_timing = {
        name: {
            "expected": timing_expected[name],
            "observed": timing_observed[name],
            "why_external_contract": TIMING_COUNTER_REASONS[name],
        }
        for name in timing_expected
    }
    return expected, exact_timing


def timing_contract_candidate(stats, build_manifest_sha256):
    exact, observed = validate_calibration(stats)
    document = {
        "schema": TIMING_CONTRACT_SCHEMA,
        "build_manifest_sha256": build_manifest_sha256,
        "counters": observed,
    }
    return exact, document


def validation_disposition(mode):
    if mode == "confirmation":
        return {
            "status": "prerequisite_passed",
            "gate_scope": "mixed_umt_evidence_prerequisite",
            "prerequisite_gate_passed": True,
            "application_performance_promotion_eligible": False,
        }
    if mode == "calibration":
        return {
            "status": "calibration_only",
            "gate_scope": "mixed_umt_evidence_prerequisite",
            "prerequisite_gate_passed": False,
            "application_performance_promotion_eligible": False,
        }
    raise ValueError(f"unknown validation mode: {mode}")


def expected_case_matrix():
    return [
        {
            "abi_version": case.abi_version,
            "groups": case.groups,
            "expect_error": case.expect_error,
        }
        for case in CASES
    ]


def validate_build_manifest_document(document):
    if set(document) != BUILD_MANIFEST_KEYS:
        raise RuntimeError("build manifest has missing or unknown fields")
    if document["schema"] != BUILD_MANIFEST_SCHEMA:
        raise RuntimeError("build manifest schema changed")
    for name in (
        "gem5_sha256",
        "frozen_gem5_sha256",
        "stdout_sha256",
        "stderr_sha256",
        "builder_sha256",
    ):
        if not is_sha256(document[name]):
            raise RuntimeError(f"build manifest {name} is not SHA-256")
    for name in ("source_commit", "source_tree"):
        if not isinstance(document[name], str) or not re.fullmatch(
            r"[0-9a-f]{40}", document[name]
        ):
            raise RuntimeError(f"build manifest {name} is not a full SHA-1")
    if document["status"] != "passed" or document["returncode"] != 0:
        raise RuntimeError("build manifest does not record a passed build")
    for name in (
        "source_clean_before_and_after",
        "source_identity_unchanged",
        "required_relink_observed",
    ):
        if document[name] is not True:
            raise RuntimeError(f"build manifest {name} is not true")
    expected_command = [
        str(pathlib.Path("/usr/bin/scons").resolve()),
        "--ignore-style",
        "build/X86/gem5.opt",
        "-j4",
    ]
    if document["command"] != expected_command:
        raise RuntimeError("build manifest command is not the capped J4 build")
    if document["gem5_sha256"] != document["frozen_gem5_sha256"]:
        raise RuntimeError("build manifest target and frozen hashes differ")
    for name in ("target_size", "target_mtime_ns"):
        if type(document[name]) is not int or document[name] <= 0:
            raise RuntimeError(f"build manifest {name} is not positive")
    for name in (
        "started_at",
        "ended_at",
        "target",
        "frozen_gem5",
        "claim_boundary",
    ):
        if not isinstance(document[name], str) or not document[name]:
            raise RuntimeError(f"build manifest {name} is empty")
    return document


def validate_repository_boundary(
    document, actual_commit, build_manifest_path, gem5
):
    if document["source_commit"] != actual_commit:
        raise RuntimeError("build manifest does not bind exact harness HEAD")
    actual_tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True
    ).strip()
    if document["source_tree"] != actual_tree:
        raise RuntimeError("build manifest does not bind exact source tree")
    expected_target = (ROOT / "build/X86/gem5.opt").resolve()
    if pathlib.Path(document["target"]).resolve() != expected_target:
        raise RuntimeError("build manifest target path changed")
    if pathlib.Path(document["frozen_gem5"]).resolve() != gem5:
        raise RuntimeError("build manifest does not name the supplied gem5")
    if build_manifest_path.parent != gem5.parent:
        raise RuntimeError("build manifest and frozen gem5 are separated")
    builder = ROOT / "tests/lanl_maa/run_reproducible_gem5_build.py"
    if document["builder_sha256"] != sha256(builder):
        raise RuntimeError("build manifest builder hash is stale")
    if document["frozen_gem5_sha256"] != sha256(gem5):
        raise RuntimeError("frozen gem5 hash differs from build manifest")


def compile_guest(source, binary, compiler):
    command = [
        compiler,
        *GUEST_COMPILE_FLAGS,
        str(source),
        "-o",
        str(binary),
    ]
    subprocess.run(command, check=True)
    return command


def case_metadata():
    documents = []
    for index, case in enumerate(CASES):
        documents.append(
            {
                "index": index,
                "abi_version": case.abi_version,
                "plane_words": 32 if case.abi_version == 4 else 64,
                "groups": case.groups,
                "expect_error": case.expect_error,
                "oracle_sha256": (
                    None
                    if case.expect_error
                    else oracle_sha256(index, case.groups)
                ),
            }
        )
    return documents


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument("--source", required=True, type=pathlib.Path)
    parser.add_argument("--output-root", required=True, type=pathlib.Path)
    parser.add_argument("--build-manifest", required=True, type=pathlib.Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--timing-contract", type=pathlib.Path)
    mode.add_argument("--calibrate-timing-contract", action="store_true")
    args = parser.parse_args()

    if args.output_root.exists():
        raise RuntimeError(f"refusing to overwrite {args.output_root}")
    actual_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    status = subprocess.check_output(
        ["git", "status", "--short"], cwd=ROOT, text=True
    ).strip()
    if status:
        raise RuntimeError("mixed UMT evidence worktree is not clean")

    source = args.source.resolve()
    config = args.config.resolve()
    gem5 = args.gem5.resolve()
    expected_source = (
        ROOT / "benchmarks/LANL/umt_ordered_wave_mixed_evidence_smoke.c"
    ).resolve()
    expected_config = (
        ROOT / "tests/lanl_maa/umt_ordered_wave_mixed_evidence_smoke.py"
    ).resolve()
    if source != expected_source or config != expected_config:
        raise RuntimeError("mixed UMT source or config path changed")

    build_manifest_path = args.build_manifest.resolve()
    build_manifest = validate_build_manifest_document(
        read_json_object(build_manifest_path, "build manifest")
    )
    build_manifest_sha256 = sha256(build_manifest_path)
    validate_repository_boundary(
        build_manifest, actual_commit, build_manifest_path, gem5
    )

    compiler = shutil.which("cc")
    if not compiler:
        raise RuntimeError("mixed UMT evidence smoke requires cc")
    compiler = str(pathlib.Path(compiler).resolve())
    artifact_hashes = {
        "gem5_sha256": sha256(gem5),
        "config_sha256": sha256(config),
        "source_sha256": sha256(source),
        "compiler_sha256": sha256(compiler),
    }
    if build_manifest["frozen_gem5_sha256"] != artifact_hashes["gem5_sha256"]:
        raise RuntimeError("build manifest frozen gem5 artifact mismatch")

    validation_mode = (
        "calibration" if args.calibrate_timing_contract else "confirmation"
    )
    timing_contract = None
    timing_contract_path = None
    timing_contract_sha256 = None
    if validation_mode == "confirmation":
        timing_contract_path = args.timing_contract.resolve()
        timing_contract = read_json_object(
            timing_contract_path, "timing contract"
        )
        timing_contract_sha256 = sha256(timing_contract_path)
        # Reject a malformed or differently-bound contract before simulation.
        validate_timing_contract(timing_contract, build_manifest_sha256)

    args.output_root.mkdir(parents=False)
    binary = args.output_root / "umt_ordered_wave_mixed_evidence.elf"
    compile_command = compile_guest(source, binary, compiler)
    actual_guest_sha256 = sha256(binary)
    metadata = {
        "schema": EVIDENCE_REPORT_SCHEMA,
        "validation_mode": validation_mode,
        "cases": case_metadata(),
        "edge_count": len(EDGES),
        "edge_mask": f"0x{edge_mask():08x}",
        "edges": [
            {
                "source": source,
                "destination": destination,
                "coefficient": coefficient,
            }
            for source, destination, coefficient in EDGES
        ],
        "bad_active_value": {
            "case_index": len(CASES) - 1,
            "plane": 8,
            "group": 7,
            "bits": f"0x{POISON_BITS:016x}",
            "expected_error": BAD_RECORD_VALUE,
        },
        "inactive_record_bits": f"0x{POISON_BITS:016x}",
        "inactive_result_bits": f"0x{RESULT_SENTINEL_BITS:016x}",
        "build_manifest": build_manifest,
        "build_manifest_sha256": build_manifest_sha256,
        "timing_contract_sha256": timing_contract_sha256,
        "simulator_commit": build_manifest["source_commit"],
        "harness_commit": actual_commit,
        "gem5_sha256": artifact_hashes["gem5_sha256"],
        "config_sha256": artifact_hashes["config_sha256"],
        "source_sha256": artifact_hashes["source_sha256"],
        "compiler_sha256": artifact_hashes["compiler_sha256"],
        "guest_sha256": actual_guest_sha256,
        "guest_compile_command": compile_command,
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
    terminal = "LANLMAA_UMT_MIXED_EVIDENCE_TERMINAL code=0"
    if terminal not in completed.stdout:
        raise RuntimeError("gem5 output lacks the exact mixed UMT terminal")

    stats_path = outdir / "stats.txt"
    stats = read_stats(stats_path)
    candidate_path = None
    candidate_sha256 = None
    exact_timing_stats = None
    calibration_timing_stats = None
    if validation_mode == "confirmation":
        expected, exact_timing_stats = validate_confirmation(
            stats, timing_contract, build_manifest_sha256
        )
    else:
        expected, candidate = timing_contract_candidate(
            stats, build_manifest_sha256
        )
        calibration_timing_stats = candidate["counters"]
        candidate_path = args.output_root / "timing-contract.candidate.json"
        write_json(candidate_path, candidate)
        candidate_sha256 = sha256(candidate_path)
    disposition = validation_disposition(validation_mode)
    report = {
        **metadata,
        **disposition,
        "terminal": True,
        "returncode": completed.returncode,
        "command": command,
        "exact_stats": expected,
        "exact_timing_stats": exact_timing_stats,
        "timing_contract_path": (
            str(timing_contract_path) if timing_contract_path else None
        ),
        "timing_contract": timing_contract,
        "calibration_timing_stats": calibration_timing_stats,
        "candidate_timing_contract_path": (
            str(candidate_path) if candidate_path else None
        ),
        "candidate_timing_contract_sha256": candidate_sha256,
        "packet_retry_accounting": {
            "counters": validate_packet_retry_counters(stats),
            "closed_form_relationships": list(PACKET_RETRY_RELATIONSHIPS),
            "exactly_bound_by_timing_contract": (
                validation_mode == "confirmation"
            ),
        },
        "unasserted_timing_counters": {
            "descriptorCycles": (
                "includes descriptor traffic, cache latency, execution, and drain"
            ),
            "engineCycles": (
                "includes the surrounding descriptor engine duty window"
            ),
        },
        "stats_sha256": sha256(stats_path),
        "stdout_sha256": sha256(args.output_root / "stdout.log"),
        "stderr_sha256": sha256(args.output_root / "stderr.log"),
        "error_termination": (
            "One guest is unambiguous: it observes four Completion terminals, "
            "then admits seven denominator tokens before requiring "
            "Error/BadRecordValue with an untouched final completion record "
            "and result arena before process exit."
        ),
        "calibration_boundary": (
            "Calibration emits a candidate external timing contract but is "
            "never a prerequisite pass; only a later confirmation run with a "
            "predeclared exact contract can report prerequisite_passed. "
            "Neither mode establishes application performance promotion."
        ),
        "claim_boundary": (
            "Prerequisite-only live mixed-ABI terminal-rearm, fixed "
            "dense-graph scalar-oracle, poison-tail, fail-closed error-drain, "
            "exact-work, packet-retry, occupancy, and cost-floor evidence; no "
            "application-speedup, performance-promotion, total-area, energy, "
            "RTL, or physical-design claim."
        ),
    }
    report_path = args.output_root / "report.json"
    write_json(report_path, report)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
