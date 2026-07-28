#!/usr/bin/env python3

import argparse
import pathlib
import re
import shutil
import subprocess
import tempfile

EXPECTED_EXACT = {
    "logicalItems": 4,
    "logicalMemoryAccesses": 11,
    "responsesFannedOut": 11,
    "completionsRetired": 4,
    "verificationFailures": 0,
    "continuationSteps": 11,
    "continuationExhaustions": 0,
    "activeContextHighWaterMark": 2,
}


def read_stats(path):
    stats = {}
    pattern = re.compile(r"^system\.lanl_maa\.(\w+)\s+(\d+)\s")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            stats[match.group(1)] = int(match.group(2))
    return stats


def validate(stats):
    errors = []
    for name, expected in EXPECTED_EXACT.items():
        actual = stats.get(name)
        if actual != expected:
            errors.append(f"{name}: expected {expected}, got {actual}")

    physical = stats.get("physicalLineReads")
    merges = stats.get("lineMergeHits")
    if physical is None or merges is None or physical + merges != 11:
        errors.append(
            f"access conservation failed: physical={physical}, merges={merges}"
        )
    if stats.get("contextWouldBlockCycles", 0) == 0:
        errors.append("contextWouldBlockCycles: expected exercised pressure")
    failures = stats.get("portSendFailures")
    retries = stats.get("portRetryNotifications")
    if failures != retries:
        errors.append(
            "timing retry imbalance: "
            f"portSendFailures={failures}, portRetryNotifications={retries}"
        )

    if errors:
        raise RuntimeError(
            "LANLMAA cell-walk smoke failed:\n  " + "\n  ".join(errors)
        )


def validate_exhaustion(stats):
    expected = {
        "logicalItems": 4,
        "logicalMemoryAccesses": 4,
        "responsesFannedOut": 4,
        "completionsRetired": 4,
        "verificationFailures": 4,
        "continuationSteps": 4,
        "continuationExhaustions": 4,
        "activeContextHighWaterMark": 2,
    }
    errors = [
        f"{name}: expected {value}, got {stats.get(name)}"
        for name, value in expected.items()
        if stats.get(name) != value
    ]
    physical = stats.get("physicalLineReads")
    merges = stats.get("lineMergeHits")
    if physical is None or merges is None or physical + merges != 4:
        errors.append(
            f"exhaustion conservation failed: physical={physical}, merges={merges}"
        )
    if stats.get("contextWouldBlockCycles", 0) == 0:
        errors.append("exhaustion case did not exercise context pressure")
    if errors:
        raise RuntimeError(
            "LANLMAA exhaustion smoke failed:\n  " + "\n  ".join(errors)
        )


def build_image(root):
    compiler = shutil.which("cc")
    linker = shutil.which("ld")
    if not compiler or not linker:
        raise RuntimeError("LANLMAA cell-walk smoke requires cc and ld")

    source_dir = pathlib.Path(__file__).resolve().parent
    object_path = root / "cell_walk_image.o"
    image_path = root / "cell_walk_image.elf"
    subprocess.run(
        [compiler, "-c", source_dir / "cell_walk_image.S", "-o", object_path],
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


def run_case(args, root, image, case_name, extra_args, validator):
    outdir = root / case_name
    command = [
        str(args.gem5.resolve()),
        f"--outdir={outdir}",
        str(args.config.resolve()),
        f"--image={image}",
        *extra_args,
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    if args.outdir:
        (root / f"{case_name}.stdout").write_text(
            result.stdout, encoding="utf-8"
        )
        (root / f"{case_name}.stderr").write_text(
            result.stderr, encoding="utf-8"
        )
    if result.returncode != 0:
        raise RuntimeError(
            "gem5 cell-walk smoke failed:\n" + result.stdout + result.stderr
        )
    validator(read_stats(outdir / "stats.txt"))


def run_smoke(args, root):
    image = build_image(root)
    run_case(args, root, image, "m5out", [], validate)
    run_case(
        args,
        root,
        image,
        "m5out_exhaustion",
        ["--max-steps=1", "--expect-exhaustion"],
        validate_exhaustion,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument(
        "--config",
        default=pathlib.Path(__file__).with_name("cell_walk_smoke.py"),
        type=pathlib.Path,
    )
    parser.add_argument(
        "--outdir",
        type=pathlib.Path,
        help="Preserve the generated image, logs, and m5out evidence",
    )
    args = parser.parse_args()

    if args.outdir:
        root = args.outdir.resolve()
        if root.exists():
            raise RuntimeError(f"refusing to reuse evidence directory: {root}")
        root.mkdir(parents=True)
        run_smoke(args, root)
    else:
        with tempfile.TemporaryDirectory(prefix="lanl-maa-cell-walk-") as root:
            run_smoke(args, pathlib.Path(root))

    print("LANLMAA cell-walk smoke: PASS")


if __name__ == "__main__":
    main()
