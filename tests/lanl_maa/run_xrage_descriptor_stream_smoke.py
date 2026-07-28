#!/usr/bin/env python3

import argparse
import hashlib
import json
import pathlib
import shutil
import struct
import subprocess
import tempfile

from run_xrage_descriptor_rearm_smoke import (
    DESCRIPTOR_ADDRESS,
    DESCRIPTOR_ITEMS,
    MEMORY_BYTES,
    TARGET_BASE,
    TRACE_SHA256,
    check_equal,
    file_sha256,
    read_stats,
    read_trace,
    splitmix64,
    write_descriptor,
)

MAX_STREAM_DESCRIPTORS = 32
ADDRESS_VECTOR_BASE = 0x2000
RESULT_VECTOR_BASE = 0x8000
COMPLETION_RECORD_BASE = 0x10000


def validate(stats, chunks, physical_line_reads):
    errors = []
    logical_items = chunks * DESCRIPTOR_ITEMS
    accelerator = stats.get("lanl_maa", {})
    expected = {
        "logicalItems": logical_items,
        "logicalMemoryAccesses": logical_items,
        "physicalLineReads": physical_line_reads,
        "lineMergeHits": logical_items - physical_line_reads,
        "lineWouldBlockCycles": 0,
        "responses": physical_line_reads,
        "responsesFannedOut": logical_items,
        "completionsRetired": logical_items,
        "verificationFailures": 0,
        "descriptorDoorbells": chunks,
        "descriptorBusyRejections": 0,
        "descriptorRearms": chunks - 1,
        "descriptorFetches": chunks,
        "descriptorAddressLineReads": chunks * 8,
        "descriptorAddressesLoaded": logical_items,
        "descriptorResultWrites": logical_items,
        "descriptorCompletionWrites": chunks,
        "descriptorErrors": 0,
    }
    for name, value in expected.items():
        check_equal(errors, accelerator, name, value)
    if accelerator.get("physicalLineReads", 0) + accelerator.get(
        "lineMergeHits", 0
    ) != accelerator.get("logicalMemoryAccesses"):
        errors.append("descriptor-stream line accounting did not close")
    failures = accelerator.get("portSendFailures")
    notifications = accelerator.get("portRetryNotifications")
    resubmissions = accelerator.get("retryPacketResubmissions")
    if failures != notifications or notifications != resubmissions:
        errors.append(
            "accelerator retry obligations differ: "
            f"failures={failures}, notifications={notifications}, "
            f"resubmissions={resubmissions}"
        )

    sequencer = stats.get("sequencer", {})
    check_equal(errors, sequencer, "doorbellWritesAccepted", chunks)
    check_equal(errors, sequencer, "detailReadsAccepted", chunks)
    check_equal(errors, sequencer, "completedObservations", chunks)
    check_equal(errors, sequencer, "errorObservations", 0)
    check_equal(errors, sequencer, "completedDetailsValidated", chunks)
    check_equal(errors, sequencer, "errorDetailsValidated", 0)
    check_equal(errors, sequencer, "descriptorsAdvanced", chunks)
    status_reads = sequencer.get("statusReadsAccepted")
    busy = sequencer.get("busyObservations")
    completed = sequencer.get("completedObservations")
    if status_reads != busy + completed:
        errors.append("sequencer terminal-status accounting did not close")
    if busy is None or busy <= 0:
        errors.append("sequencer did not observe descriptor Busy")
    if sequencer.get("responses") != chunks * 2 + status_reads:
        errors.append("sequencer request/response accounting did not close")
    send_failures = sequencer.get("sendFailures")
    retry_notifications = sequencer.get("retryNotifications")
    retry_resubmissions = sequencer.get("retryResubmissions")
    if (
        send_failures != retry_notifications
        or retry_notifications != retry_resubmissions
    ):
        errors.append("sequencer retry obligations did not close")

    verified_items = logical_items + chunks * 4
    verifier = stats.get("final_verifier", {})
    check_equal(errors, verifier, "logicalItems", verified_items)
    check_equal(errors, verifier, "completionsRetired", verified_items)
    check_equal(errors, verifier, "verificationFailures", 0)
    if verifier.get("physicalLineReads", 0) + verifier.get(
        "lineMergeHits", 0
    ) != verified_items:
        errors.append("stream verifier line accounting did not close")
    verifier_failures = verifier.get("portSendFailures")
    verifier_notifications = verifier.get("portRetryNotifications")
    verifier_resubmissions = verifier.get("retryPacketResubmissions")
    if (
        verifier_failures != verifier_notifications
        or verifier_notifications != verifier_resubmissions
    ):
        errors.append("stream verifier retry obligations did not close")
    if errors:
        raise RuntimeError(
            "XRAGE descriptor stream failed:\n  " + "\n  ".join(errors)
        )


def build_image(root, pattern, trace_path, chunks):
    assembler = shutil.which("cc")
    linker = shutil.which("ld")
    if not assembler or not linker:
        raise RuntimeError("XRAGE descriptor stream smoke requires cc and ld")
    repo = pathlib.Path(__file__).resolve().parents[2]
    windows = []
    for slot in range(chunks):
        offset = slot * DESCRIPTOR_ITEMS
        indices = pattern[offset : offset + DESCRIPTOR_ITEMS]
        if len(indices) != DESCRIPTOR_ITEMS:
            raise RuntimeError("XRAGE trace ended before the requested stream")
        address_vector = ADDRESS_VECTOR_BASE + slot * DESCRIPTOR_ITEMS * 8
        result_vector = RESULT_VECTOR_BASE + slot * DESCRIPTOR_ITEMS * 8
        completion_record = COMPLETION_RECORD_BASE + slot * 32
        packed = struct.pack("<64Q", *indices)
        windows.append(
            {
                "name": f"chunk_{slot:02d}",
                "slot": slot,
                "offset": offset,
                "address_vector": address_vector,
                "result_vector": result_vector,
                "completion_record": completion_record,
                "window_u64le_sha256": hashlib.sha256(packed).hexdigest(),
                "unique_target_lines": len({index // 8 for index in indices}),
                "indices": indices,
                "expected_results": [splitmix64(index) for index in indices],
            }
        )

    assembly = root / "descriptor_stream_image.S"
    object_path = root / "descriptor_stream_image.o"
    image = root / "descriptor_stream_image.elf"
    metadata_path = root / "metadata.json"
    with assembly.open("w", encoding="utf-8") as stream:
        stream.write(
            "    .section .data\n"
            "    .globl _lanl_maa_image_start\n"
            "_lanl_maa_image_start:\n"
            "    .balign 64\n"
            f"    .org 0x{DESCRIPTOR_ADDRESS:x}\n"
        )
        for window in windows:
            write_descriptor(stream, window, True)
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
                raise RuntimeError("XRAGE stream target exceeds smoke memory")
            stream.write(f"    .org 0x{address:x}\n")
            stream.write(f"    .quad 0x{splitmix64(index):016x}\n")

    metadata = {
        "schema_version": 1,
        "mapping": "status-driven XRAGE Spatter Gather descriptor stream",
        "source_path": str(trace_path),
        "source_sha256": TRACE_SHA256,
        "descriptor_table": DESCRIPTOR_ADDRESS,
        "descriptor_items": DESCRIPTOR_ITEMS,
        "descriptor_chunks": chunks,
        "submission_protocol": (
            "doorbell, poll status until Completed, validate slot, next"
        ),
        "poll_interval_cycles": 2,
        "verification_start_cycle": chunks * 3000 + 4000,
        "windows": windows,
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
    return image, metadata_path, windows


def run_smoke(args, root):
    trace = args.trace.resolve()
    if file_sha256(trace) != TRACE_SHA256:
        raise RuntimeError("XRAGE stream trace SHA-256 changed")
    if args.chunks < 2 or args.chunks > MAX_STREAM_DESCRIPTORS:
        raise RuntimeError(
            f"chunks must be in [2, {MAX_STREAM_DESCRIPTORS}]"
        )
    pattern = read_trace(trace)
    image, metadata, windows = build_image(
        root, pattern, trace, args.chunks
    )
    outdir = root / "m5out"
    command = [
        str(args.gem5.resolve()),
        f"--outdir={outdir}",
        str(args.config.resolve()),
        f"--image={image}",
        f"--metadata={metadata}",
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    (root / "gem5.stdout").write_text(result.stdout, encoding="utf-8")
    (root / "gem5.stderr").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            "gem5 XRAGE descriptor stream failed:\n"
            + result.stdout
            + result.stderr
        )
    physical_line_reads = sum(
        window["unique_target_lines"] for window in windows
    )
    validate(
        read_stats(outdir / "stats.txt"), args.chunks, physical_line_reads
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--trace", required=True, type=pathlib.Path)
    parser.add_argument("--chunks", default=32, type=int)
    parser.add_argument(
        "--config",
        default=pathlib.Path(__file__).with_name(
            "xrage_descriptor_stream_smoke.py"
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
            prefix="lanl-maa-xrage-descriptor-stream-"
        ) as root:
            run_smoke(args, pathlib.Path(root))
    print("LANLMAA XRAGE descriptor stream smoke: PASS")


if __name__ == "__main__":
    main()
