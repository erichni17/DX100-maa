#!/usr/bin/env python3

import argparse
import json
import pathlib
import subprocess
import tempfile

from run_xrage_descriptor_rearm_smoke import (
    DESCRIPTOR_ITEMS,
    build_image,
    read_stats,
)
from run_xrage_descriptor_trace_smoke import read_trace

POLL_INTERVAL_CYCLES = 2
CASES = {
    "completed_polling": {
        "first_descriptor_valid": True,
        "busy_submission": False,
        "second_submission_cycle": 0,
        "verification_start_cycle": 6500,
        "verified_slots": (0, 1),
        "expected_terminal_errors": (0, 0),
        "logical_items": 128,
        "physical_line_reads": 22,
        "line_merge_hits": 106,
        "descriptor_errors": 0,
        "address_line_reads": 16,
        "result_writes": 128,
        "completion_writes": 2,
        "completed_observations": 2,
        "error_observations": 0,
    },
    "error_polling": {
        "first_descriptor_valid": False,
        "busy_submission": False,
        "second_submission_cycle": 0,
        "verification_start_cycle": 3500,
        "verified_slots": (1,),
        "expected_terminal_errors": (1, 0),
        "logical_items": 64,
        "physical_line_reads": 12,
        "line_merge_hits": 52,
        "descriptor_errors": 1,
        "address_line_reads": 8,
        "result_writes": 64,
        "completion_writes": 1,
        "completed_observations": 1,
        "error_observations": 1,
    },
}


def check_equal(errors, stats, name, expected):
    actual = stats.get(name)
    if actual != expected:
        errors.append(f"{name}: expected {expected}, got {actual}")


def validate(stats, case_name, case):
    errors = []
    accelerator = stats.get("lanl_maa", {})
    expected_accelerator = {
        "logicalItems": case["logical_items"],
        "logicalMemoryAccesses": case["logical_items"],
        "physicalLineReads": case["physical_line_reads"],
        "lineMergeHits": case["line_merge_hits"],
        "lineWouldBlockCycles": 0,
        "responses": case["physical_line_reads"],
        "responsesFannedOut": case["logical_items"],
        "completionsRetired": case["logical_items"],
        "verificationFailures": 0,
        "descriptorDoorbells": 2,
        "descriptorBusyRejections": 0,
        "descriptorRearms": 1,
        "descriptorFetches": 2,
        "descriptorAddressLineReads": case["address_line_reads"],
        "descriptorAddressesLoaded": case["logical_items"],
        "descriptorResultWrites": case["result_writes"],
        "descriptorCompletionWrites": case["completion_writes"],
        "descriptorErrors": case["descriptor_errors"],
    }
    for name, value in expected_accelerator.items():
        check_equal(errors, accelerator, name, value)
    if accelerator.get("physicalLineReads", 0) + accelerator.get(
        "lineMergeHits", 0
    ) != accelerator.get("logicalMemoryAccesses"):
        errors.append("descriptor line accounting did not close")
    if accelerator.get("portSendFailures") != accelerator.get(
        "portRetryNotifications"
    ) or accelerator.get("portRetryNotifications") != accelerator.get(
        "retryPacketResubmissions"
    ):
        errors.append("accelerator retry obligations did not close")

    sequencer = stats.get("sequencer", {})
    expected_sequencer = {
        "doorbellWritesAccepted": 2,
        "detailReadsAccepted": 2,
        "completedObservations": case["completed_observations"],
        "errorObservations": case["error_observations"],
        "completedDetailsValidated": case["completed_observations"],
        "errorDetailsValidated": case["error_observations"],
        "descriptorsAdvanced": 2,
    }
    for name, value in expected_sequencer.items():
        check_equal(errors, sequencer, name, value)
    status_reads = sequencer.get("statusReadsAccepted")
    busy = sequencer.get("busyObservations")
    completed = sequencer.get("completedObservations")
    error = sequencer.get("errorObservations")
    if status_reads != busy + completed + error:
        errors.append("sequencer status classifications did not close")
    if busy is None or busy == 0:
        errors.append("sequencer never observed the active Busy state")
    if sequencer.get("responses") != 4 + status_reads:
        errors.append("sequencer request/response accounting did not close")
    if sequencer.get("sendFailures") != sequencer.get(
        "retryNotifications"
    ) or sequencer.get("retryNotifications") != sequencer.get(
        "retryResubmissions"
    ):
        errors.append("sequencer retry obligations did not close")
    if sequencer.get("retryAcceptances", 0) > sequencer.get(
        "retryResubmissions", 0
    ):
        errors.append("sequencer retry acceptances exceed resubmissions")

    verified_items = DESCRIPTOR_ITEMS * len(case["verified_slots"]) + 4 * len(
        case["verified_slots"]
    )
    verifier = stats.get("final_verifier", {})
    check_equal(errors, verifier, "logicalItems", verified_items)
    check_equal(errors, verifier, "completionsRetired", verified_items)
    check_equal(errors, verifier, "verificationFailures", 0)
    if (
        verifier.get("physicalLineReads", 0) + verifier.get("lineMergeHits", 0)
        != verified_items
    ):
        errors.append("delayed verifier line accounting did not close")
    if errors:
        raise RuntimeError(
            f"XRAGE descriptor {case_name} failed:\n  " + "\n  ".join(errors)
        )


def build_polling_image(root, pattern, trace_path, case_name, case):
    image, metadata_path = build_image(
        root, pattern, trace_path, case_name, case
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.pop("busy_submission")
    metadata.pop("second_submission_cycle")
    metadata[
        "submission_protocol"
    ] = "doorbell, poll status until terminal, validate detail, next doorbell"
    metadata["expected_terminal_errors"] = list(
        case["expected_terminal_errors"]
    )
    metadata["poll_interval_cycles"] = POLL_INTERVAL_CYCLES
    metadata_path.write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    return image, metadata_path


def run_case(args, root, pattern, case_name, case):
    case_root = root / case_name
    case_root.mkdir()
    image, metadata = build_polling_image(
        case_root, pattern, args.trace.resolve(), case_name, case
    )
    outdir = case_root / "m5out"
    command = [
        str(args.gem5.resolve()),
        f"--outdir={outdir}",
        str(args.config.resolve()),
        f"--image={image}",
        f"--metadata={metadata}",
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    (case_root / "gem5.stdout").write_text(result.stdout, encoding="utf-8")
    (case_root / "gem5.stderr").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            f"gem5 XRAGE descriptor {case_name} failed:\n"
            + result.stdout
            + result.stderr
        )
    validate(read_stats(outdir / "stats.txt"), case_name, case)


def run_smoke(args, root):
    pattern = read_trace(args.trace.resolve())
    for case_name, case in CASES.items():
        run_case(args, root, pattern, case_name, case)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--trace", required=True, type=pathlib.Path)
    parser.add_argument(
        "--config",
        default=pathlib.Path(__file__).with_name(
            "xrage_descriptor_polling_smoke.py"
        ),
        type=pathlib.Path,
    )
    parser.add_argument("--outdir", type=pathlib.Path)
    args = parser.parse_args()

    if args.outdir:
        root = args.outdir.resolve()
        if root.exists():
            raise RuntimeError(f"refusing to reuse evidence directory: {root}")
        root.mkdir(parents=True)
        run_smoke(args, root)
    else:
        with tempfile.TemporaryDirectory(
            prefix="lanl-maa-xrage-descriptor-polling-"
        ) as root:
            run_smoke(args, pathlib.Path(root))
    print("LANLMAA XRAGE descriptor polling smoke: PASS")


if __name__ == "__main__":
    main()
