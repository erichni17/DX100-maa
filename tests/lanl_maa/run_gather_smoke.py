#!/usr/bin/env python3

import argparse
import pathlib
import re
import shutil
import subprocess
import tempfile

EXPECTED_EXACT = {
    "logicalItems": 12,
    "logicalMemoryAccesses": 12,
    "physicalLineReads": 3,
    "lineMergeHits": 9,
    "responses": 3,
    "responsesFannedOut": 12,
    "completionsRetired": 12,
    "verificationFailures": 0,
    "continuationSteps": 0,
    "continuationExhaustions": 0,
    "activeContextHighWaterMark": 0,
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

    if stats.get("lineWouldBlockCycles", 0) == 0:
        errors.append("lineWouldBlockCycles: expected exercised pressure")

    failures = stats.get("portSendFailures")
    retries = stats.get("portRetryNotifications")
    if failures is None or failures == 0:
        errors.append("portSendFailures: expected at least one refused send")
    if failures != retries:
        errors.append(
            "timing retry imbalance: "
            f"portSendFailures={failures}, portRetryNotifications={retries}"
        )

    if errors:
        raise RuntimeError(
            "LANLMAA gather smoke failed:\n  " + "\n  ".join(errors)
        )


def build_image(root):
    compiler = shutil.which("cc")
    linker = shutil.which("ld")
    if not compiler or not linker:
        raise RuntimeError("LANLMAA gather smoke requires cc and ld")

    source_dir = pathlib.Path(__file__).resolve().parent
    object_path = root / "gather_image.o"
    image_path = root / "gather_image.elf"
    subprocess.run(
        [compiler, "-c", source_dir / "gather_image.S", "-o", object_path],
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


def run_smoke(args, root):
    image = build_image(root)
    outdir = root / "m5out"
    command = [
        str(args.gem5.resolve()),
        f"--outdir={outdir}",
        str(args.config.resolve()),
        f"--image={image}",
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    if args.outdir:
        (root / "gem5.stdout").write_text(result.stdout, encoding="utf-8")
        (root / "gem5.stderr").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            "gem5 gather smoke failed:\n" + result.stdout + result.stderr
        )
    validate(read_stats(outdir / "stats.txt"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument(
        "--config",
        default=pathlib.Path(__file__).with_name("gather_smoke.py"),
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
        with tempfile.TemporaryDirectory(prefix="lanl-maa-gather-") as root:
            run_smoke(args, pathlib.Path(root))

    print("LANLMAA gather smoke: PASS")


if __name__ == "__main__":
    main()
