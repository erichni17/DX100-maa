#!/usr/bin/env python3

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import tempfile

INSTANCES = ("lanl_maa", "final_verifier", "submitter")
FIXED_METADATA = {
    "native_cell_count": 64,
    "state_expansion_factor": 16,
    "record_count": 1024,
    "record_bytes": 16384,
    "native_packed_cell_bytes": 512,
    "maximum_steps": 8,
    "descriptor_items": 8,
    "executed_record_visits": 41,
    "start_indices": [896, 384, 256, 320, 896, 704, 257, 641],
    "root_visits": [8, 4, 3, 3, 8, 6, 3, 6],
    "final_cells": [20, 60, 62, 9, 20, 13, 30, 11],
    "expected_results": [330, 189, 128, 13, 330, 51, 74, 137],
}


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


def validate_metadata(metadata):
    errors = []
    for name, expected in FIXED_METADATA.items():
        if metadata.get(name) != expected:
            errors.append(
                f"metadata {name}: expected {expected}, "
                f"got {metadata.get(name)}"
            )
    native_bytes = metadata.get("native_packed_cell_bytes")
    staging_bytes = metadata.get("record_bytes")
    if (
        not isinstance(native_bytes, int)
        or not isinstance(staging_bytes, int)
        or staging_bytes != 32 * native_bytes
    ):
        errors.append(
            "expected state-expanded records to occupy exactly 32x "
            f"native packed-cell bytes, got {staging_bytes}/{native_bytes}"
        )
    if errors:
        raise RuntimeError(
            "SPARTA descriptor staging metadata changed:\n  "
            + "\n  ".join(errors)
        )


def validate_stats(stats, metadata):
    errors = []
    accelerator = stats["lanl_maa"]
    items = metadata["descriptor_items"]
    visits = metadata["executed_record_visits"]
    expected = {
        "logicalItems": items,
        "logicalMemoryAccesses": visits,
        "responsesFannedOut": visits,
        "completionsRetired": items,
        "verificationFailures": 0,
        "continuationSteps": visits,
        "continuationExhaustions": 0,
        "descriptorDoorbells": 1,
        "descriptorBusyRejections": 1,
        "descriptorFetches": 1,
        "descriptorAddressLineReads": 1,
        "descriptorAddressesLoaded": items,
        "descriptorResultWrites": items,
        "descriptorCompletionWrites": 1,
        "descriptorErrors": 0,
    }
    for name, value in expected.items():
        check_equal(errors, accelerator, name, value)

    physical = accelerator.get("physicalLineReads")
    merges = accelerator.get("lineMergeHits")
    if physical is None or merges is None or physical + merges != visits:
        errors.append(
            "record accounting mismatch: "
            f"physical={physical}, merges={merges}, visits={visits}"
        )
    check_equal(errors, accelerator, "responses", physical)
    active_contexts = accelerator.get("activeContextHighWaterMark")
    if active_contexts is None or not 0 < active_contexts <= 4:
        errors.append(
            f"invalid active context high-water mark {active_contexts}"
        )

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

    submitter = stats["submitter"]
    check_equal(errors, submitter, "writesAccepted", 2)
    check_equal(errors, submitter, "responses", 2)
    if errors:
        raise RuntimeError(
            "LANLMAA SPARTA descriptor staging smoke failed:\n  "
            + "\n  ".join(errors)
        )


def build_staging(root):
    compiler = shutil.which("g++")
    assembler = shutil.which("cc")
    linker = shutil.which("ld")
    if not compiler or not assembler or not linker:
        raise RuntimeError("SPARTA staging smoke requires g++, cc, and ld")
    repo = pathlib.Path(__file__).resolve().parents[2]
    benchmark = root / "sparta_particle_cell_step"
    assembly = root / "sparta_descriptor_image.S"
    metadata_path = root / "sparta_descriptor_metadata.json"
    object_path = root / "sparta_descriptor_image.o"
    image = root / "sparta_descriptor_image.elf"

    subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            repo / "src",
            repo / "benchmarks/LANL/sparta_particle_cell_step.cc",
            "-o",
            benchmark,
        ],
        check=True,
    )
    benchmark_result = subprocess.run(
        [
            benchmark,
            "--particles",
            "256",
            "--cells",
            "64",
            "--visits",
            "8",
            "--window",
            "16",
            "--line-entries",
            "8",
            "--contexts",
            "4",
            "--combiner-entries",
            "16",
            "--combiner-banks",
            "4",
            "--descriptor-items",
            "8",
            "--seed",
            "0x535041525441",
            "--order",
            "sorted",
            "--emit-descriptor-assembly",
            assembly,
            "--emit-descriptor-metadata",
            metadata_path,
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    (root / "benchmark.stdout").write_text(
        benchmark_result.stdout, encoding="utf-8"
    )
    if "verification=PASS" not in benchmark_result.stdout:
        raise RuntimeError("SPARTA scalar/reference-model verification failed")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    validate_metadata(metadata)
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


def run_smoke(args, root):
    image, metadata_path, metadata = build_staging(root)
    outdir = root / "m5out"
    command = [
        str(args.gem5.resolve()),
        f"--outdir={outdir}",
        str(args.config.resolve()),
        f"--image={image}",
        f"--metadata={metadata_path}",
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    if args.outdir:
        (root / "gem5.stdout").write_text(result.stdout, encoding="utf-8")
        (root / "gem5.stderr").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            "gem5 SPARTA descriptor staging smoke failed:\n"
            + result.stdout
            + result.stderr
        )
    validate_stats(read_stats(outdir / "stats.txt"), metadata)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument(
        "--config",
        default=pathlib.Path(__file__).with_name(
            "descriptor_staging_smoke.py"
        ),
        type=pathlib.Path,
    )
    parser.add_argument(
        "--outdir",
        type=pathlib.Path,
        help="Preserve benchmark, staging view, logs, and m5out evidence",
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
            prefix="lanl-maa-sparta-descriptor-staging-"
        ) as root:
            run_smoke(args, pathlib.Path(root))

    print("LANLMAA SPARTA descriptor staging smoke: PASS")


if __name__ == "__main__":
    main()
