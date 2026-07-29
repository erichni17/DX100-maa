#!/usr/bin/env python3

import argparse
import hashlib
import json
import pathlib
import re
import shutil
import struct
import subprocess
import tempfile

from run_xrage_descriptor_trace_smoke import (
    TRACE_ENTRIES,
    TRACE_SHA256,
    file_sha256,
    read_trace,
    splitmix64,
)

DESCRIPTOR_ADDRESS = 0x800
DESCRIPTOR_ITEMS = 64
TARGET_BASE = 0x200000
MEMORY_BYTES = 16 * 1024 * 1024
WINDOWS = (
    (
        "head",
        0,
        "6929711f4f49fbbde674fa80d5b8f5cd05f2140b75f1e747ea7467995cb1aa7b",
        10,
    ),
    (
        "next",
        64,
        "2d088e51c501e1416a4af7ed9b0549f758e03e686bcf52c74fe0a111812deb3f",
        12,
    ),
)
LAYOUT = (
    {
        "slot": 0,
        "address_vector": 0x1000,
        "result_vector": 0x1400,
        "completion_record": 0x1800,
    },
    {
        "slot": 1,
        "address_vector": 0x1200,
        "result_vector": 0x1600,
        "completion_record": 0x1820,
    },
)
CASES = {
    "completed_rearm": {
        "first_descriptor_valid": True,
        "busy_submission": True,
        "second_submission_cycle": 3000,
        "verification_start_cycle": 6500,
        "verified_slots": (0, 1),
        "logical_items": 128,
        "physical_line_reads": 22,
        "line_merge_hits": 106,
        "descriptor_errors": 0,
        "busy_rejections": 1,
        "address_line_reads": 16,
        "result_writes": 128,
        "completion_writes": 2,
    },
    "error_rearm": {
        "first_descriptor_valid": False,
        "busy_submission": False,
        "second_submission_cycle": 500,
        "verification_start_cycle": 3500,
        "verified_slots": (1,),
        "logical_items": 64,
        "physical_line_reads": 12,
        "line_merge_hits": 52,
        "descriptor_errors": 1,
        "busy_rejections": 0,
        "address_line_reads": 8,
        "result_writes": 64,
        "completion_writes": 1,
    },
}
INSTANCE_PATTERN = re.compile(r"^system\.(\w+)\.(\w+)\s+(\d+)\s")


def read_stats(path):
    stats = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = INSTANCE_PATTERN.match(line)
        if match:
            stats.setdefault(match.group(1), {})[match.group(2)] = int(
                match.group(3)
            )
    return stats


def check_equal(errors, stats, name, expected):
    actual = stats.get(name)
    if actual != expected:
        errors.append(f"{name}: expected {expected}, got {actual}")


def validate(stats, case_name, case):
    errors = []
    accelerator = stats.get("lanl_maa", {})
    expected = {
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
        "descriptorBusyRejections": case["busy_rejections"],
        "descriptorRearms": 1,
        "descriptorFetches": 2,
        "descriptorAddressLineReads": case["address_line_reads"],
        "descriptorAddressesLoaded": case["logical_items"],
        "descriptorResultWrites": case["result_writes"],
        "descriptorCompletionWrites": case["completion_writes"],
        "descriptorErrors": case["descriptor_errors"],
        "sharedOverlayModeAcquisitions": case["completion_writes"],
        "sharedOverlayReservationRejections": 0,
        "sharedOverlayTrafficAccepted": (
            case["address_line_reads"]
            + case["physical_line_reads"]
            + case["result_writes"]
            + case["completion_writes"]
        ),
        "sharedOverlayTrafficAcknowledged": (
            case["address_line_reads"]
            + case["physical_line_reads"]
            + case["result_writes"]
            + case["completion_writes"]
        ),
        "sharedOverlayDrains": case["completion_writes"],
        "sharedOverlayReleases": case["completion_writes"],
    }
    for name, value in expected.items():
        check_equal(errors, accelerator, name, value)
    if accelerator.get("physicalLineReads", 0) + accelerator.get(
        "lineMergeHits", 0
    ) != accelerator.get("logicalMemoryAccesses"):
        errors.append("descriptor line accounting did not close")
    failures = accelerator.get("portSendFailures")
    notifications = accelerator.get("portRetryNotifications")
    resubmissions = accelerator.get("retryPacketResubmissions")
    if failures != notifications or notifications != resubmissions:
        errors.append(
            "retry obligations differ: "
            f"failures={failures}, notifications={notifications}, "
            f"resubmissions={resubmissions}"
        )

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

    submitters = ["submitter0", "submitter1"]
    if case["busy_submission"]:
        submitters.append("busy_submitter")
    for name in submitters:
        submitter = stats.get(name, {})
        check_equal(errors, submitter, "writesAccepted", 1)
        check_equal(errors, submitter, "responses", 1)
        send_failures = submitter.get("sendFailures")
        retry_notifications = submitter.get("retryNotifications")
        retry_resubmissions = submitter.get("retryResubmissions")
        if send_failures != retry_notifications:
            errors.append(f"{name} retry notification mismatch")
        if retry_notifications != retry_resubmissions:
            errors.append(f"{name} retry resubmission mismatch")
    if errors:
        raise RuntimeError(
            f"XRAGE descriptor {case_name} failed:\n  " + "\n  ".join(errors)
        )


def write_descriptor(stream, layout, valid):
    header = 0x0001000131414D4C if valid else 0x0001000131414D00
    stream.write(
        f"    .quad 0x{header:016x}\n"
        f"    .quad {DESCRIPTOR_ITEMS}\n"
        f"    .quad 0x{layout['address_vector']:x}\n"
        f"    .quad 0x{layout['result_vector']:x}\n"
        f"    .quad 0x{layout['completion_record']:x}\n"
        "    .zero 24\n"
    )


def build_image(root, pattern, trace_path, case_name, case):
    assembler = shutil.which("cc")
    linker = shutil.which("ld")
    if not assembler or not linker:
        raise RuntimeError("XRAGE descriptor rearm smoke requires cc and ld")
    repo = pathlib.Path(__file__).resolve().parents[2]
    windows = []
    for definition, layout in zip(WINDOWS, LAYOUT):
        name, offset, expected_hash, unique_lines = definition
        indices = pattern[offset : offset + DESCRIPTOR_ITEMS]
        packed = struct.pack("<64Q", *indices)
        actual_hash = hashlib.sha256(packed).hexdigest()
        if actual_hash != expected_hash:
            raise RuntimeError(f"XRAGE {name} rearm window hash changed")
        if len({index // 8 for index in indices}) != unique_lines:
            raise RuntimeError(f"XRAGE {name} rearm line count changed")
        windows.append(
            {
                **layout,
                "name": name,
                "offset": offset,
                "window_u64le_sha256": actual_hash,
                "unique_target_lines": unique_lines,
                "indices": indices,
                "expected_results": [splitmix64(index) for index in indices],
            }
        )

    assembly = root / "descriptor_rearm_image.S"
    object_path = root / "descriptor_rearm_image.o"
    image = root / "descriptor_rearm_image.elf"
    metadata_path = root / "metadata.json"
    with assembly.open("w", encoding="utf-8") as stream:
        stream.write(
            "    .section .data\n"
            "    .globl _lanl_maa_image_start\n"
            "_lanl_maa_image_start:\n"
            "    .balign 64\n"
            f"    .org 0x{DESCRIPTOR_ADDRESS:x}\n"
        )
        write_descriptor(stream, windows[0], case["first_descriptor_valid"])
        write_descriptor(stream, windows[1], True)
        for window in windows:
            stream.write(f"    .org 0x{window['address_vector']:x}\n")
            for index in window["indices"]:
                stream.write(f"    .quad 0x{TARGET_BASE + index * 8:x}\n")
        for window in windows:
            stream.write(
                f"    .org 0x{window['result_vector']:x}\n"
                f"    .zero {DESCRIPTOR_ITEMS * 8}\n"
            )
        for window in windows:
            stream.write(
                f"    .org 0x{window['completion_record']:x}\n"
                "    .zero 32\n"
            )
        for index in sorted(
            {index for window in windows for index in window["indices"]}
        ):
            address = TARGET_BASE + index * 8
            if address + 8 > MEMORY_BYTES:
                raise RuntimeError("XRAGE rearm target exceeds smoke memory")
            stream.write(f"    .org 0x{address:x}\n")
            stream.write(f"    .quad 0x{splitmix64(index):016x}\n")

    metadata = {
        "schema_version": 1,
        "case": case_name,
        "mapping": (
            "two XRAGE Spatter Gather windows through reusable descriptors"
        ),
        "source_path": str(trace_path),
        "source_sha256": TRACE_SHA256,
        "source_entries": TRACE_ENTRIES,
        "descriptor_table": DESCRIPTOR_ADDRESS,
        "descriptor_items": DESCRIPTOR_ITEMS,
        "first_descriptor_valid": case["first_descriptor_valid"],
        "busy_submission": case["busy_submission"],
        "second_submission_cycle": case["second_submission_cycle"],
        "verification_start_cycle": case["verification_start_cycle"],
        "windows": windows,
        "verified_windows": [
            window
            for window in windows
            if window["slot"] in case["verified_slots"]
        ],
        "value_oracle": "SplitMix64(index), modulo 2^64",
    }
    metadata_path.write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    subprocess.run([assembler, "-c", assembly, "-o", object_path], check=True)
    subprocess.run(
        [
            linker,
            "-T",
            repo / "tests/lanl_maa/gather_image.ld",
            "-o",
            image,
            object_path,
        ],
        check=True,
    )
    return image, metadata_path


def run_case(args, root, pattern, case_name, case):
    case_root = root / case_name
    case_root.mkdir()
    image, metadata = build_image(
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
    if file_sha256(args.trace.resolve()) != TRACE_SHA256:
        raise RuntimeError("XRAGE rearm trace SHA-256 changed")
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
            "xrage_descriptor_rearm_smoke.py"
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
            prefix="lanl-maa-xrage-descriptor-rearm-"
        ) as root:
            run_smoke(args, pathlib.Path(root))
    print("LANLMAA XRAGE descriptor rearm smoke: PASS")


if __name__ == "__main__":
    main()
