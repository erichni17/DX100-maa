#!/usr/bin/env python3

import argparse
import pathlib
import re
import shutil
import subprocess
import tempfile

UPDATE_OBJECTS = ("lanl_maa_a", "lanl_maa_b")


def read_stats(path):
    stats = {name: {} for name in (*UPDATE_OBJECTS, "final_verifier")}
    pattern = re.compile(
        r"^system\.(lanl_maa_a|lanl_maa_b|final_verifier)\.(\w+)\s+(\d+)\s"
    )
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            stats[match.group(1)][match.group(2)] = int(match.group(3))
    return stats


def validate_updater(name, stats):
    expected = {
        "logicalItems": 4,
        "logicalMemoryAccesses": 4,
        "updateCombinerHits": 1,
        "updateDrains": 3,
        "physicalAtomicUpdates": 3,
        "atomicAcknowledgements": 3,
        "atomicOldValuesReturned": 3,
        "updateOperationsAcknowledged": 4,
        "verificationReads": 1,
        "verificationFailures": 0,
        "responses": 4,
        "completionsRetired": 4,
    }
    return [
        f"{name}.{counter}: expected {value}, got {stats.get(counter)}"
        for counter, value in expected.items()
        if stats.get(counter) != value
    ]


def validate_retry(stats):
    errors = []
    total_failures = 0
    for name in UPDATE_OBJECTS:
        instance = stats[name]
        failures = instance.get("portSendFailures", 0)
        notifications = instance.get("portRetryNotifications", 0)
        resubmissions = instance.get("retryPacketResubmissions", 0)
        acceptances = instance.get("retryPacketAcceptances", 0)
        total_failures += failures
        if failures != notifications or notifications != resubmissions:
            errors.append(
                f"{name} retry imbalance: failures={failures}, "
                f"notifications={notifications}, "
                f"resubmissions={resubmissions}, acceptances={acceptances}"
            )
        if failures and (acceptances == 0 or acceptances > resubmissions):
            errors.append(
                f"{name} retry acceptance imbalance: "
                f"acceptances={acceptances}, resubmissions={resubmissions}"
            )
    if total_failures == 0:
        errors.append("multi-requester smoke did not exercise timing retry")
    return errors


def validate(stats):
    errors = []
    for name in UPDATE_OBJECTS:
        errors.extend(validate_updater(name, stats[name]))
    errors.extend(validate_retry(stats))

    verifier = stats["final_verifier"]
    expected_verifier = {
        "logicalItems": 4,
        "logicalMemoryAccesses": 4,
        "physicalLineReads": 1,
        "lineMergeHits": 3,
        "responsesFannedOut": 4,
        "responses": 1,
        "completionsRetired": 4,
        "verificationFailures": 0,
    }
    errors.extend(
        f"final_verifier.{counter}: expected {value}, "
        f"got {verifier.get(counter)}"
        for counter, value in expected_verifier.items()
        if verifier.get(counter) != value
    )
    if errors:
        raise RuntimeError(
            "LANLMAA atomic contention smoke failed:\n  " + "\n  ".join(errors)
        )


def validate_bad_oracle(stats, positive):
    errors = []
    for name in UPDATE_OBJECTS:
        for counter in (
            "logicalItems",
            "logicalMemoryAccesses",
            "updateCombinerHits",
            "updateDrains",
            "physicalAtomicUpdates",
            "atomicAcknowledgements",
            "atomicOldValuesReturned",
            "updateOperationsAcknowledged",
            "verificationReads",
            "verificationFailures",
            "responses",
            "completionsRetired",
        ):
            if stats[name].get(counter) != positive[name].get(counter):
                errors.append(
                    f"bad-oracle traffic changed: {name}.{counter}="
                    f"{stats[name].get(counter)} versus "
                    f"{positive[name].get(counter)}"
                )
    verifier = stats["final_verifier"]
    if verifier.get("verificationFailures") != 1:
        errors.append(
            "final_verifier.verificationFailures: expected 1, got "
            f"{verifier.get('verificationFailures')}"
        )
    if errors:
        raise RuntimeError(
            "LANLMAA bad-oracle atomic contention smoke failed:\n  "
            + "\n  ".join(errors)
        )


def build_image(root):
    compiler = shutil.which("cc")
    linker = shutil.which("ld")
    if not compiler or not linker:
        raise RuntimeError("LANLMAA contention smoke requires cc and ld")

    source_dir = pathlib.Path(__file__).resolve().parent
    object_path = root / "atomic_contention_image.o"
    image_path = root / "atomic_contention_image.elf"
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
            "gem5 atomic contention smoke failed:\n"
            + result.stdout
            + result.stderr
        )
    return read_stats(outdir / "stats.txt")


def run_smoke(args, root):
    image = build_image(root)
    positive = run_case(args, root, image, "m5out", [])
    validate(positive)
    bad_oracle = run_case(
        args, root, image, "m5out_bad_oracle", ["--corrupt-oracle"]
    )
    validate_bad_oracle(bad_oracle, positive)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument(
        "--config",
        default=pathlib.Path(__file__).with_name("atomic_contention_smoke.py"),
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
        with tempfile.TemporaryDirectory(
            prefix="lanl-maa-atomic-contention-"
        ) as root:
            run_smoke(args, pathlib.Path(root))

    print("LANLMAA atomic contention smoke: PASS")


if __name__ == "__main__":
    main()
