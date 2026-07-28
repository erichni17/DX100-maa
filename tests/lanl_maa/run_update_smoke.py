#!/usr/bin/env python3

import argparse
import pathlib
import re
import shutil
import subprocess
import tempfile


def read_stats(path):
    stats = {}
    pattern = re.compile(r"^system\.lanl_maa\.(\w+)\s+(\d+)\s")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            stats[match.group(1)] = int(match.group(2))
    return stats


def validate(stats):
    expected = {
        "logicalItems": 13,
        "logicalMemoryAccesses": 13,
        "completionsRetired": 13,
        "verificationFailures": 0,
        "updateOperationsAcknowledged": 13,
        "verificationReads": 7,
    }
    errors = [
        f"{name}: expected {value}, got {stats.get(name)}"
        for name, value in expected.items()
        if stats.get(name) != value
    ]

    reads = stats.get("physicalUpdateReads")
    writes = stats.get("physicalUpdateWrites")
    acknowledgements = stats.get("writeAcknowledgements")
    drains = stats.get("updateDrains")
    hits = stats.get("updateCombinerHits")
    if not reads or reads != writes or writes != acknowledgements:
        errors.append(
            "read/write/ack imbalance: "
            f"reads={reads}, writes={writes}, acknowledgements={acknowledgements}"
        )
    if drains != writes:
        errors.append(
            f"drain/write imbalance: drains={drains}, writes={writes}"
        )
    if drains is None or hits is None or drains + hits != 13:
        errors.append(
            f"update conservation failed: drains={drains}, combiner_hits={hits}"
        )
    if stats.get("updateTableWouldBlockCycles", 0) == 0:
        errors.append("update table pressure was not exercised")
    failures = stats.get("portSendFailures")
    retries = stats.get("portRetryNotifications")
    resubmissions = stats.get("retryPacketResubmissions")
    acceptances = stats.get("retryPacketAcceptances")
    if not failures or failures != retries or retries != resubmissions:
        errors.append(
            "timing retry imbalance: "
            f"failures={failures}, notifications={retries}, "
            f"resubmissions={resubmissions}"
        )
    if not acceptances or acceptances > resubmissions:
        errors.append(
            "retry acceptance imbalance: "
            f"acceptances={acceptances}, resubmissions={resubmissions}"
        )
    responses = stats.get("responses")
    if responses != 2 * drains + 7:
        errors.append(
            f"response conservation failed: responses={responses}, drains={drains}"
        )

    if errors:
        raise RuntimeError(
            "LANLMAA update smoke failed:\n  " + "\n  ".join(errors)
        )


def validate_bad_oracle(stats, positive):
    errors = []
    expected = {
        "logicalItems": 13,
        "logicalMemoryAccesses": 13,
        "completionsRetired": 13,
        "updateOperationsAcknowledged": 13,
        "verificationReads": 7,
        "verificationFailures": 1,
    }
    for name, value in expected.items():
        if stats.get(name) != value:
            errors.append(f"{name}: expected {value}, got {stats.get(name)}")
    for name in (
        "updateCombinerHits",
        "updateDrains",
        "physicalUpdateReads",
        "physicalUpdateWrites",
        "writeAcknowledgements",
    ):
        if stats.get(name) != positive.get(name):
            errors.append(
                f"bad-oracle traffic changed: {name}="
                f"{stats.get(name)} versus {positive.get(name)}"
            )
    if errors:
        raise RuntimeError(
            "LANLMAA bad-oracle smoke failed:\n  " + "\n  ".join(errors)
        )


def build_image(root):
    compiler = shutil.which("cc")
    linker = shutil.which("ld")
    if not compiler or not linker:
        raise RuntimeError("LANLMAA update smoke requires cc and ld")

    source_dir = pathlib.Path(__file__).resolve().parent
    object_path = root / "update_image.o"
    image_path = root / "update_image.elf"
    subprocess.run(
        [compiler, "-c", source_dir / "update_image.S", "-o", object_path],
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


def run_case(args, root, image, name, extra_args):
    outdir = root / name
    command = [
        str(args.gem5.resolve()),
        f"--outdir={outdir}",
        str(args.config.resolve()),
        f"--image={image}",
        *extra_args,
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    if args.outdir:
        (root / f"{name}.stdout").write_text(result.stdout, encoding="utf-8")
        (root / f"{name}.stderr").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            "gem5 update smoke failed:\n" + result.stdout + result.stderr
        )
    return read_stats(outdir / "stats.txt")


def run_invalid_config(args, root, image):
    name = "m5out_invalid_config"
    command = [
        str(args.gem5.resolve()),
        f"--outdir={root / name}",
        str(args.config.resolve()),
        f"--image={image}",
        "--invalid-banks",
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    if args.outdir:
        (root / f"{name}.stdout").write_text(result.stdout, encoding="utf-8")
        (root / f"{name}.stderr").write_text(result.stderr, encoding="utf-8")
    expected = "update entries must divide evenly into nonzero banks"
    output = result.stdout + result.stderr
    if (
        result.returncode == 0
        or expected not in output
        or "<extra arg>" in output
    ):
        raise RuntimeError(
            "LANLMAA invalid-bank configuration did not fail closed:\n"
            + result.stdout
            + result.stderr
        )


def run_smoke(args, root):
    image = build_image(root)
    positive = run_case(args, root, image, "m5out", [])
    validate(positive)
    bad_oracle = run_case(
        args, root, image, "m5out_bad_oracle", ["--corrupt-oracle"]
    )
    validate_bad_oracle(bad_oracle, positive)
    run_invalid_config(args, root, image)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument(
        "--config",
        default=pathlib.Path(__file__).with_name("update_smoke.py"),
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
        with tempfile.TemporaryDirectory(prefix="lanl-maa-update-") as root:
            run_smoke(args, pathlib.Path(root))

    print("LANLMAA update smoke: PASS")


if __name__ == "__main__":
    main()
