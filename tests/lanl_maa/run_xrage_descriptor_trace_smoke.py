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

TRACE_SHA256 = (
    "1a56db824f4fd58222d4246504e2a6fcdb0b691cd380ec18be5531ae76c1ccde"
)
TRACE_ENTRIES = 2097152
WINDOW_ITEMS = 64
WINDOWS = (
    (
        "head",
        0,
        "6929711f4f49fbbde674fa80d5b8f5cd05f2140b75f1e747ea7467995cb1aa7b",
        10,
        5,
    ),
    (
        "middle",
        1048576,
        "31991c1a68af084a338bc5d1a2a2d01fc91c4f83050c3ca0a2ba2b9e84f74086",
        17,
        5,
    ),
    (
        "tail",
        2097088,
        "3442d2efad36a1b236f3f73a2654f8b9ce04dd94668e9727a1c059611166593b",
        14,
        0,
    ),
)
INSTANCES = ("lanl_maa", "final_verifier", "submitter")
MASK64 = (1 << 64) - 1
DESCRIPTOR_ADDRESS = 0x800
ADDRESS_VECTOR = 0x1000
RESULT_VECTOR = 0x1200
COMPLETION_RECORD = 0x1400
TARGET_BASE = 0x200000
MEMORY_BYTES = 16 * 1024 * 1024


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_trace(path):
    if file_sha256(path) != TRACE_SHA256:
        raise RuntimeError("XRAGE trace SHA-256 does not match pinned input")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) != 1:
        raise RuntimeError(
            "XRAGE trace must contain exactly one configuration"
        )
    configuration = payload[0]
    if (
        configuration.get("kernel") != "Gather"
        or configuration.get("count") != 1
    ):
        raise RuntimeError(
            "XRAGE configuration is not the pinned Gather/count=1"
        )
    pattern = configuration.get("pattern")
    if not isinstance(pattern, list) or len(pattern) != TRACE_ENTRIES:
        raise RuntimeError("XRAGE trace entry count changed")
    if any(
        isinstance(index, bool)
        or not isinstance(index, int)
        or not 0 <= index <= MASK64
        for index in pattern
    ):
        raise RuntimeError("XRAGE trace contains an invalid uint64 index")
    return pattern


def splitmix64(index):
    value = (index + 0x9E3779B97F4A7C15) & MASK64
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & MASK64
    return value ^ (value >> 31)


def read_stats(path):
    stats = {name: {} for name in INSTANCES}
    pattern = re.compile(
        r"^system\.(lanl_maa|final_verifier|submitter)\.(\w+)\s+(\d+)\s"
    )
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            stats[match.group(1)][match.group(2)] = int(match.group(3))
    return stats


def check_equal(errors, stats, name, expected):
    actual = stats.get(name)
    if actual != expected:
        errors.append(f"{name}: expected {expected}, got {actual}")


def validate(stats, metadata):
    errors = []
    accelerator = stats["lanl_maa"]
    items = metadata["descriptor_items"]
    expected = {
        "logicalItems": items,
        "logicalMemoryAccesses": items,
        "responsesFannedOut": items,
        "completionsRetired": items,
        "verificationFailures": 0,
        "continuationSteps": 0,
        "continuationExhaustions": 0,
        "activeContextHighWaterMark": 0,
        "descriptorDoorbells": 1,
        "descriptorBusyRejections": 1,
        "descriptorFetches": 1,
        "descriptorAddressLineReads": 8,
        "descriptorAddressesLoaded": items,
        "descriptorResultWrites": items,
        "descriptorCompletionWrites": 1,
        "descriptorErrors": 0,
    }
    for name, value in expected.items():
        check_equal(errors, accelerator, name, value)

    physical = accelerator.get("physicalLineReads")
    merges = accelerator.get("lineMergeHits")
    check_equal(
        errors,
        accelerator,
        "lineBankConflictCycles",
        metadata["expected_line_bank_conflict_cycles"],
    )
    if physical is None or merges is None or physical + merges != items:
        errors.append(
            "gather accounting mismatch: "
            f"physical={physical}, merges={merges}, items={items}"
        )
    if physical is not None and physical < metadata["unique_target_lines"]:
        errors.append(
            f"physical reads {physical} below unique-line lower bound "
            f"{metadata['unique_target_lines']}"
        )
    check_equal(errors, accelerator, "responses", physical)

    failures = accelerator.get("portSendFailures")
    notifications = accelerator.get("portRetryNotifications")
    resubmissions = accelerator.get("retryPacketResubmissions")
    acceptances = accelerator.get("retryPacketAcceptances")
    if failures != notifications or notifications != resubmissions:
        errors.append(
            "retry obligation mismatch: "
            f"failures={failures}, notifications={notifications}, "
            f"resubmissions={resubmissions}"
        )
    if (
        acceptances is None
        or resubmissions is None
        or not 0 <= acceptances <= resubmissions
    ):
        errors.append(
            f"invalid retry acceptances={acceptances}, "
            f"resubmissions={resubmissions}"
        )

    verifier = stats["final_verifier"]
    check_equal(errors, verifier, "logicalItems", items + 4)
    check_equal(errors, verifier, "completionsRetired", items + 4)
    check_equal(errors, verifier, "verificationFailures", 0)
    verifier_physical = verifier.get("physicalLineReads")
    verifier_merges = verifier.get("lineMergeHits")
    if (
        verifier_physical is None
        or verifier_merges is None
        or verifier_physical + verifier_merges != items + 4
    ):
        errors.append("delayed result verifier accounting did not close")

    submitter = stats["submitter"]
    check_equal(errors, submitter, "writesAccepted", 2)
    check_equal(errors, submitter, "responses", 2)
    if errors:
        raise RuntimeError(
            f"XRAGE descriptor window {metadata['window_name']} failed:\n  "
            + "\n  ".join(errors)
        )


def build_image(
    root,
    indices,
    name,
    offset,
    expected_hash,
    unique_lines,
    expected_bank_conflicts,
    source_path,
):
    assembler = shutil.which("cc")
    linker = shutil.which("ld")
    if not assembler or not linker:
        raise RuntimeError("XRAGE descriptor smoke requires cc and ld")
    repo = pathlib.Path(__file__).resolve().parents[2]
    packed = struct.pack(f"<{len(indices)}Q", *indices)
    actual_hash = hashlib.sha256(packed).hexdigest()
    if actual_hash != expected_hash:
        raise RuntimeError(f"XRAGE {name} window hash changed")
    actual_unique_lines = len({index // 8 for index in indices})
    if actual_unique_lines != unique_lines:
        raise RuntimeError(f"XRAGE {name} unique-line count changed")

    addresses = [TARGET_BASE + index * 8 for index in indices]
    if any(address + 8 > MEMORY_BYTES for address in addresses):
        raise RuntimeError("XRAGE target does not fit the smoke memory range")
    expected = [splitmix64(index) for index in indices]
    if any(value == 0 for value in expected):
        raise RuntimeError("XRAGE nonzero target oracle produced zero")

    assembly = root / "descriptor_image.S"
    object_path = root / "descriptor_image.o"
    image = root / "descriptor_image.elf"
    metadata_path = root / "metadata.json"
    with assembly.open("w", encoding="utf-8") as stream:
        stream.write(
            "    .section .data\n"
            "    .balign 64\n"
            f"    .org 0x{DESCRIPTOR_ADDRESS:x}\n"
            "    .quad 0x0001000131414d4c\n"
            f"    .quad {len(indices)}\n"
            f"    .quad 0x{ADDRESS_VECTOR:x}\n"
            f"    .quad 0x{RESULT_VECTOR:x}\n"
            f"    .quad 0x{COMPLETION_RECORD:x}\n"
            "    .zero 24\n"
            f"    .org 0x{ADDRESS_VECTOR:x}\n"
        )
        for address in addresses:
            stream.write(f"    .quad 0x{address:x}\n")
        stream.write(
            f"    .org 0x{RESULT_VECTOR:x}\n"
            f"    .zero {len(indices) * 8}\n"
            f"    .org 0x{COMPLETION_RECORD:x}\n"
            "    .zero 32\n"
        )
        for index in sorted(set(indices)):
            stream.write(f"    .org 0x{TARGET_BASE + index * 8:x}\n")
            stream.write(f"    .quad 0x{splitmix64(index):016x}\n")

    metadata = {
        "schema_version": 1,
        "mapping": (
            "XRAGE Spatter Gather trace window with synthetic nonzero "
            "per-index values"
        ),
        "source_path": str(source_path),
        "source_sha256": TRACE_SHA256,
        "source_configuration": 0,
        "source_kernel": "Gather",
        "source_count": 1,
        "source_entries": TRACE_ENTRIES,
        "window_name": name,
        "window_offset": offset,
        "window_u64le_sha256": actual_hash,
        "descriptor_address": DESCRIPTOR_ADDRESS,
        "address_vector": ADDRESS_VECTOR,
        "result_vector": RESULT_VECTOR,
        "completion_record": COMPLETION_RECORD,
        "target_base": TARGET_BASE,
        "descriptor_items": len(indices),
        "unique_indices": len(set(indices)),
        "unique_target_lines": actual_unique_lines,
        "expected_line_bank_conflict_cycles": expected_bank_conflicts,
        "value_oracle": "SplitMix64(index), modulo 2^64",
        "trace_indices": indices,
        "expected_results": expected,
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
    return image, metadata_path, metadata


def run_case(args, root, pattern, window):
    name, offset, expected_hash, unique_lines, expected_bank_conflicts = window
    case_root = root / name
    case_root.mkdir()
    indices = pattern[offset : offset + WINDOW_ITEMS]
    if len(indices) != WINDOW_ITEMS:
        raise RuntimeError(f"XRAGE {name} window is truncated")
    image, metadata_path, metadata = build_image(
        case_root,
        indices,
        name,
        offset,
        expected_hash,
        unique_lines,
        expected_bank_conflicts,
        args.trace.resolve(),
    )
    outdir = case_root / "m5out"
    command = [
        str(args.gem5.resolve()),
        f"--outdir={outdir}",
        str(args.config.resolve()),
        f"--image={image}",
        f"--metadata={metadata_path}",
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    (case_root / "gem5.stdout").write_text(result.stdout, encoding="utf-8")
    (case_root / "gem5.stderr").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            f"gem5 XRAGE descriptor window {name} failed:\n"
            + result.stdout
            + result.stderr
        )
    validate(read_stats(outdir / "stats.txt"), metadata)


def run_smoke(args, root):
    pattern = read_trace(args.trace.resolve())
    for window in WINDOWS:
        run_case(args, root, pattern, window)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--trace", required=True, type=pathlib.Path)
    parser.add_argument(
        "--config",
        default=pathlib.Path(__file__).with_name(
            "xrage_descriptor_trace_smoke.py"
        ),
        type=pathlib.Path,
    )
    parser.add_argument(
        "--outdir",
        type=pathlib.Path,
        help=(
            "Preserve trace-window images, metadata, logs, and m5out evidence"
        ),
    )
    args = parser.parse_args()

    if args.outdir:
        root = args.outdir.resolve()
        if root.exists():
            raise RuntimeError(f"refusing to reuse evidence directory: {root}")
        root.mkdir(parents=True)
        run_smoke(args, root)
    else:
        with tempfile.TemporaryDirectory(
            prefix="lanl-maa-xrage-descriptor-trace-"
        ) as root:
            run_smoke(args, pathlib.Path(root))

    print("LANLMAA XRAGE descriptor trace smoke: PASS")


if __name__ == "__main__":
    main()
