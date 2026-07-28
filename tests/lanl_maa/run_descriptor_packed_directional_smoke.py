#!/usr/bin/env python3

import argparse
import pathlib
import re
import shutil
import subprocess
import tempfile

INSTANCES = ("lanl_maa", "submitter")


def read_stats(path):
    stats = {name: {} for name in INSTANCES}
    pattern = re.compile(r"^system\.(lanl_maa|submitter)\.(\w+)\s+(\d+)\s")
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
            f"LANLMAA packed directional {case} failed:\n  "
            + "\n  ".join(errors)
        )


def validate(stats, case):
    common = {
        "descriptorDoorbells": 1,
        "descriptorFetches": 1,
        "descriptorAddressLineReads": 1,
        "descriptorResultWrites": 0,
        "descriptorCompletionWrites": 0,
        "descriptorErrors": 1,
        "continuationExhaustions": 0,
    }
    cases = {
        "reserved-start": {
            "descriptorAddressesLoaded": 0,
            "logicalItems": 0,
            "physicalLineReads": 0,
            "responses": 0,
            "continuationSteps": 0,
        },
        "over-max": {
            "descriptorAddressesLoaded": 0,
            "logicalItems": 0,
            "physicalLineReads": 0,
            "responses": 0,
            "continuationSteps": 0,
        },
        "bad-neighbor": {
            "descriptorAddressesLoaded": 1,
            "logicalItems": 1,
            "physicalLineReads": 1,
            "responses": 1,
            "continuationSteps": 1,
        },
        "bad-record": {
            "descriptorAddressesLoaded": 1,
            "logicalItems": 1,
            "physicalLineReads": 1,
            "responses": 1,
            "continuationSteps": 0,
        },
    }
    require(stats, "lanl_maa", {**common, **cases[case]}, case)
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
        raise RuntimeError("packed directional smoke requires cc and ld")
    source_dir = pathlib.Path(__file__).resolve().parent
    object_path = root / "descriptor_packed_directional_image.o"
    image_path = root / "descriptor_packed_directional_image.elf"
    subprocess.run(
        [
            compiler,
            "-c",
            source_dir / "descriptor_packed_directional_image.S",
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
            f"gem5 packed directional case {case} failed:\n"
            + result.stdout
            + result.stderr
        )
    validate(read_stats(outdir / "stats.txt"), case)


def run_smoke(args, root):
    image = build_image(root)
    for case in ("reserved-start", "over-max", "bad-neighbor", "bad-record"):
        run_case(args, root, image, case)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument(
        "--config",
        default=pathlib.Path(__file__).with_name(
            "descriptor_packed_directional_smoke.py"
        ),
        type=pathlib.Path,
    )
    parser.add_argument(
        "--outdir",
        type=pathlib.Path,
        help="Preserve generated image, logs, and m5out evidence",
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
            prefix="lanl-maa-packed-directional-"
        ) as root:
            run_smoke(args, pathlib.Path(root))

    print("LANLMAA packed directional adversarial smoke: PASS")


if __name__ == "__main__":
    main()
