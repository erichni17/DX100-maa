#!/usr/bin/env python3

import argparse
import pathlib
import re
import shutil
import subprocess
import tempfile

INSTANCES = ("lanl_maa", "final_verifier", "submitter")


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


def require(stats, instance, expected, case):
    actual = stats[instance]
    errors = [
        f"{instance}.{name}: expected {value}, got {actual.get(name)}"
        for name, value in expected.items()
        if actual.get(name) != value
    ]
    if errors:
        raise RuntimeError(
            f"LANLMAA descriptor cell-walk {case} failed:\n  "
            + "\n  ".join(errors)
        )


def validate_positive(stats):
    require(
        stats,
        "lanl_maa",
        {
            "logicalItems": 6,
            "logicalMemoryAccesses": 14,
            "physicalLineReads": 10,
            "lineMergeHits": 4,
            "responses": 10,
            "responsesFannedOut": 14,
            "completionsRetired": 6,
            "verificationFailures": 0,
            "continuationSteps": 14,
            "continuationExhaustions": 0,
            "activeContextHighWaterMark": 2,
            "portSendFailures": 4,
            "portRetryNotifications": 4,
            "retryPacketResubmissions": 4,
            "retryPacketAcceptances": 2,
            "descriptorDoorbells": 1,
            "descriptorBusyRejections": 1,
            "descriptorFetches": 1,
            "descriptorAddressLineReads": 1,
            "descriptorAddressesLoaded": 6,
            "descriptorResultWrites": 6,
            "descriptorCompletionWrites": 1,
            "descriptorErrors": 0,
        },
        "positive",
    )
    require(
        stats,
        "final_verifier",
        {
            "logicalItems": 10,
            "completionsRetired": 10,
            "verificationFailures": 0,
        },
        "positive verifier",
    )
    require(
        stats,
        "submitter",
        {"writesAccepted": 2, "responses": 2},
        "positive submitter",
    )


def validate_negative(stats, case):
    common = {
        "descriptorDoorbells": 1,
        "descriptorFetches": 1,
        "descriptorResultWrites": 0,
        "descriptorCompletionWrites": 0,
        "descriptorErrors": 1,
    }
    expected = {
        "bad-initial": {
            "descriptorAddressLineReads": 1,
            "descriptorAddressesLoaded": 0,
            "logicalItems": 0,
            "physicalLineReads": 0,
            "responses": 0,
        },
        "bad-continuation": {
            "descriptorAddressLineReads": 1,
            "descriptorAddressesLoaded": 2,
            "logicalItems": 2,
            "physicalLineReads": 2,
            "responses": 2,
            "continuationSteps": 1,
            "continuationExhaustions": 0,
        },
        "overlap": {
            "descriptorAddressLineReads": 0,
            "descriptorAddressesLoaded": 0,
            "logicalItems": 0,
            "physicalLineReads": 0,
            "responses": 0,
        },
        "exhaust": {
            "descriptorAddressLineReads": 1,
            "descriptorAddressesLoaded": 1,
            "logicalItems": 1,
            "physicalLineReads": 1,
            "responses": 1,
            "continuationSteps": 1,
            "continuationExhaustions": 1,
        },
    }[case]
    require(stats, "lanl_maa", {**common, **expected}, case)
    require(
        stats,
        "submitter",
        {"writesAccepted": 1, "responses": 1},
        f"{case} submitter",
    )


def build_image(root):
    compiler = shutil.which("cc")
    linker = shutil.which("ld")
    if not compiler or not linker:
        raise RuntimeError("LANLMAA descriptor smoke requires cc and ld")
    source_dir = pathlib.Path(__file__).resolve().parent
    object_path = root / "descriptor_cell_walk_image.o"
    image_path = root / "descriptor_cell_walk_image.elf"
    subprocess.run(
        [
            compiler,
            "-c",
            source_dir / "descriptor_cell_walk_image.S",
            "-o",
            object_path,
        ],
        check=True,
    )
    subprocess.run(
        [
            linker,
            "-T",
            source_dir / "gather_image.ld",
            "-o",
            image_path,
            object_path,
        ],
        check=True,
    )
    return image_path


def run_case(args, root, image, case):
    outdir = root / case
    command = [
        str(args.gem5.resolve()),
        f"--outdir={outdir}",
        str(args.config.resolve()),
        f"--image={image}",
        f"--case={case}",
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    if args.outdir:
        (root / f"{case}.stdout").write_text(result.stdout, encoding="utf-8")
        (root / f"{case}.stderr").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            f"gem5 descriptor cell-walk case {case} failed:\n"
            + result.stdout
            + result.stderr
        )
    return read_stats(outdir / "stats.txt")


def run_smoke(args, root):
    image = build_image(root)
    validate_positive(run_case(args, root, image, "positive"))
    for case in ("bad-initial", "bad-continuation", "overlap", "exhaust"):
        validate_negative(run_case(args, root, image, case), case)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument(
        "--config",
        default=pathlib.Path(__file__).with_name(
            "descriptor_cell_walk_smoke.py"
        ),
        type=pathlib.Path,
    )
    parser.add_argument(
        "--outdir",
        type=pathlib.Path,
        help="Preserve generated images, logs, and m5out evidence",
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
            prefix="lanl-maa-descriptor-cell-walk-"
        ) as root:
            run_smoke(args, pathlib.Path(root))

    print("LANLMAA descriptor cell-walk smoke: PASS")


if __name__ == "__main__":
    main()
