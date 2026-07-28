#!/usr/bin/env python3

import argparse
import pathlib
import re
import shutil
import subprocess
import tempfile

REDUCERS = ("relaxed_reducer", "strict_reducer")


def read_stats(path):
    stats = {name: {} for name in (*REDUCERS, "final_verifier")}
    pattern = re.compile(
        r"^system\.(relaxed_reducer|strict_reducer|final_verifier)\."
        r"(\w+)\s+(\d+)\s"
    )
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            stats[match.group(1)][match.group(2)] = int(match.group(3))
    return stats


def validate_reducer(name, stats):
    relaxed = name == "relaxed_reducer"
    drains = 1 if relaxed else 2
    expected = {
        "logicalItems": 2,
        "logicalMemoryAccesses": 2,
        "updateCombinerHits": 1 if relaxed else 0,
        "updateDrains": drains,
        "physicalAtomicUpdates": drains,
        "atomicAddUpdates": 0,
        "atomicMinUpdates": 0,
        "atomicMaxUpdates": 0,
        "atomicFp64AddUpdates": drains,
        "strictFp64Serializations": 0 if relaxed else 1,
        "atomicAcknowledgements": drains,
        "atomicOldValuesReturned": drains,
        "updateOperationsAcknowledged": 2,
        "verificationReads": 1,
        "verificationFailures": 0,
        "responses": drains + 1,
        "completionsRetired": 2,
    }
    errors = [
        f"{name}.{counter}: expected {value}, got {stats.get(counter)}"
        for counter, value in expected.items()
        if stats.get(counter) != value
    ]
    if not relaxed and stats.get("updateAddressBusyCycles", 0) == 0:
        errors.append("strict_reducer did not exercise same-address blocking")
    return errors


def validate_retry(stats):
    errors = []
    total_failures = 0
    for name in REDUCERS:
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
        errors.append("FP64 ADD smoke did not exercise timing retry")
    return errors


def validate(stats):
    errors = []
    for name in REDUCERS:
        errors.extend(validate_reducer(name, stats[name]))
    errors.extend(validate_retry(stats))
    verifier = stats["final_verifier"]
    expected_verifier = {
        "logicalItems": 2,
        "logicalMemoryAccesses": 2,
        "physicalLineReads": 1,
        "lineMergeHits": 1,
        "responsesFannedOut": 2,
        "responses": 1,
        "completionsRetired": 2,
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
            "LANLMAA FP64 ADD smoke failed:\n  " + "\n  ".join(errors)
        )


def compare_reducer_traffic(stats, positive):
    errors = []
    for name in REDUCERS:
        for counter in (
            "logicalItems",
            "logicalMemoryAccesses",
            "updateCombinerHits",
            "updateDrains",
            "physicalAtomicUpdates",
            "atomicFp64AddUpdates",
            "strictFp64Serializations",
            "atomicAcknowledgements",
            "atomicOldValuesReturned",
            "updateOperationsAcknowledged",
            "verificationReads",
            "responses",
            "completionsRetired",
        ):
            if stats[name].get(counter) != positive[name].get(counter):
                errors.append(
                    f"negative-case traffic changed: {name}.{counter}="
                    f"{stats[name].get(counter)} versus "
                    f"{positive[name].get(counter)}"
                )
    return errors


def validate_corrupt_fp_oracle(stats, positive):
    errors = compare_reducer_traffic(stats, positive)
    if stats["relaxed_reducer"].get("verificationFailures") != 1:
        errors.append(
            "relaxed_reducer.verificationFailures: expected 1, got "
            f"{stats['relaxed_reducer'].get('verificationFailures')}"
        )
    if stats["strict_reducer"].get("verificationFailures") != 0:
        errors.append(
            "strict_reducer.verificationFailures: expected 0, got "
            f"{stats['strict_reducer'].get('verificationFailures')}"
        )
    if stats["final_verifier"].get("verificationFailures") != 0:
        errors.append("corrupt FP oracle changed the final memory result")
    if errors:
        raise RuntimeError(
            "LANLMAA corrupt FP64 oracle case failed:\n  "
            + "\n  ".join(errors)
        )


def validate_corrupt_final_oracle(stats, positive):
    errors = compare_reducer_traffic(stats, positive)
    if stats["relaxed_reducer"].get("verificationFailures") != 0:
        errors.append("final-oracle corruption changed relaxed FP oracle")
    if stats["strict_reducer"].get("verificationFailures") != 0:
        errors.append("final-oracle corruption changed strict FP oracle")
    if stats["final_verifier"].get("verificationFailures") != 1:
        errors.append(
            "final_verifier.verificationFailures: expected 1, got "
            f"{stats['final_verifier'].get('verificationFailures')}"
        )
    if errors:
        raise RuntimeError(
            "LANLMAA corrupt final FP64 oracle case failed:\n  "
            + "\n  ".join(errors)
        )


def build_image(root):
    compiler = shutil.which("cc")
    linker = shutil.which("ld")
    if not compiler or not linker:
        raise RuntimeError("LANLMAA FP64 ADD smoke requires cc and ld")

    source_dir = pathlib.Path(__file__).resolve().parent
    object_path = root / "fp64_add_image.o"
    image_path = root / "fp64_add_image.elf"
    subprocess.run(
        [compiler, "-c", source_dir / "fp64_add_image.S", "-o", object_path],
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
            "gem5 FP64 ADD smoke failed:\n" + result.stdout + result.stderr
        )
    return read_stats(outdir / "stats.txt")


def run_invalid_case(args, root, image, name, option, expected):
    command = [
        str(args.gem5.resolve()),
        f"--outdir={root / name}",
        str(args.config.resolve()),
        f"--image={image}",
        option,
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    if args.outdir:
        (root / f"{name}.stdout").write_text(result.stdout, encoding="utf-8")
        (root / f"{name}.stderr").write_text(result.stderr, encoding="utf-8")
    output = result.stdout + result.stderr
    if result.returncode == 0 or expected not in output:
        raise RuntimeError(
            f"LANLMAA invalid FP64 case {name} did not fail closed:\n" + output
        )


def run_smoke(args, root):
    image = build_image(root)
    positive = run_case(args, root, image, "m5out", [])
    validate(positive)
    corrupt_fp = run_case(
        args, root, image, "m5out_corrupt_fp_oracle", ["--corrupt-fp-oracle"]
    )
    validate_corrupt_fp_oracle(corrupt_fp, positive)
    corrupt_final = run_case(
        args,
        root,
        image,
        "m5out_corrupt_final_oracle",
        ["--corrupt-final-oracle"],
    )
    validate_corrupt_final_oracle(corrupt_final, positive)
    run_invalid_case(
        args,
        root,
        image,
        "m5out_invalid_tolerance",
        "--invalid-tolerance",
        "FP64 absolute tolerance must be finite and nonnegative",
    )
    run_invalid_case(
        args,
        root,
        image,
        "m5out_nonfinite_operand",
        "--nonfinite-operand",
        "FP64 update operands must be finite",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument(
        "--config",
        default=pathlib.Path(__file__).with_name("fp64_add_smoke.py"),
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
        with tempfile.TemporaryDirectory(prefix="lanl-maa-fp64-add-") as root:
            run_smoke(args, pathlib.Path(root))

    print("LANLMAA FP64 ADD smoke: PASS")


if __name__ == "__main__":
    main()
