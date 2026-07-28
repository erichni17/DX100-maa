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


def validate_positive(stats):
    accelerator = stats["lanl_maa"]
    expected = {
        "logicalItems": 12,
        "logicalMemoryAccesses": 12,
        "physicalLineReads": 3,
        "lineMergeHits": 9,
        "responses": 3,
        "responsesFannedOut": 12,
        "completionsRetired": 12,
        "verificationFailures": 0,
        "descriptorDoorbells": 1,
        "descriptorBusyRejections": 1,
        "descriptorFetches": 1,
        "descriptorAddressLineReads": 2,
        "descriptorAddressesLoaded": 12,
        "descriptorResultWrites": 12,
        "descriptorCompletionWrites": 1,
        "descriptorErrors": 0,
        "engineCycles": 65,
    }
    errors = [
        f"lanl_maa.{name}: expected {value}, got {accelerator.get(name)}"
        for name, value in expected.items()
        if accelerator.get(name) != value
    ]
    failures = accelerator.get("portSendFailures")
    notifications = accelerator.get("portRetryNotifications")
    resubmissions = accelerator.get("retryPacketResubmissions")
    acceptances = accelerator.get("retryPacketAcceptances")
    retry_balance = (failures, notifications, resubmissions, acceptances)
    if failures != 1 or retry_balance != (1, 1, 1, 1):
        errors.append(
            "accelerator retry imbalance: "
            f"failures={failures}, notifications={notifications}, "
            f"resubmissions={resubmissions}, acceptances={acceptances}"
        )

    submitter = stats["submitter"]
    for name, expected_value in ("writesAccepted", 2), ("responses", 2):
        if submitter.get(name) != expected_value:
            errors.append(
                f"submitter.{name}: expected {expected_value}, "
                f"got {submitter.get(name)}"
            )
    for name in ("sendFailures", "retryNotifications", "retryResubmissions"):
        if submitter.get(name) != 0:
            errors.append(
                f"submitter.{name}: expected 0, got {submitter.get(name)}"
            )

    verifier = stats["final_verifier"]
    verifier_expected = {
        "logicalItems": 16,
        "logicalMemoryAccesses": 16,
        "physicalLineReads": 3,
        "lineMergeHits": 13,
        "responsesFannedOut": 16,
        "completionsRetired": 16,
        "verificationFailures": 0,
    }
    errors.extend(
        f"final_verifier.{name}: expected {value}, got {verifier.get(name)}"
        for name, value in verifier_expected.items()
        if verifier.get(name) != value
    )
    if errors:
        raise RuntimeError(
            "LANLMAA descriptor gather smoke failed:\n  " + "\n  ".join(errors)
        )


def validate_negative(stats, name, address_line_reads=0):
    accelerator = stats["lanl_maa"]
    expected = {
        "logicalItems": 0,
        "logicalMemoryAccesses": 0,
        "physicalLineReads": 0,
        "descriptorDoorbells": 1,
        "descriptorFetches": 1,
        "descriptorAddressLineReads": address_line_reads,
        "descriptorAddressesLoaded": 0,
        "descriptorResultWrites": 0,
        "descriptorCompletionWrites": 0,
        "descriptorErrors": 1,
        "engineCycles": 0,
    }
    errors = [
        f"{name}.{counter}: expected {value}, got {accelerator.get(counter)}"
        for counter, value in expected.items()
        if accelerator.get(counter) != value
    ]
    submitter = stats["submitter"]
    if submitter.get("writesAccepted") != 1 or submitter.get("responses") != 1:
        errors.append(
            f"{name} submitter did not close one MMIO request/response"
        )
    if errors:
        raise RuntimeError(
            f"LANLMAA {name} descriptor smoke failed:\n  "
            + "\n  ".join(errors)
        )


def build_image(root):
    compiler = shutil.which("cc")
    linker = shutil.which("ld")
    if not compiler or not linker:
        raise RuntimeError("LANLMAA descriptor smoke requires cc and ld")
    source_dir = pathlib.Path(__file__).resolve().parent
    object_path = root / "descriptor_gather_image.o"
    image_path = root / "descriptor_gather_image.elf"
    subprocess.run(
        [
            compiler,
            "-c",
            source_dir / "descriptor_gather_image.S",
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
            f"gem5 descriptor case {name} failed:\n"
            + result.stdout
            + result.stderr
        )
    return read_stats(outdir / "stats.txt")


def run_smoke(args, root):
    image = build_image(root)
    validate_positive(run_case(args, root, image, "m5out", []))
    validate_negative(
        run_case(
            args,
            root,
            image,
            "m5out_bad_magic",
            ["--bad-magic"],
        ),
        "bad-magic",
    )
    validate_negative(
        run_case(
            args,
            root,
            image,
            "m5out_overlap_output",
            ["--overlap-output"],
        ),
        "overlap-output",
    )
    validate_negative(
        run_case(
            args,
            root,
            image,
            "m5out_bad_target",
            ["--bad-target"],
        ),
        "bad-target",
        address_line_reads=1,
    )
    validate_negative(
        run_case(
            args,
            root,
            image,
            "m5out_unmapped_target",
            ["--unmapped-target"],
        ),
        "unmapped-target",
        address_line_reads=1,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument(
        "--config",
        default=pathlib.Path(__file__).with_name("descriptor_gather_smoke.py"),
        type=pathlib.Path,
    )
    parser.add_argument(
        "--outdir",
        type=pathlib.Path,
        help="Preserve generated descriptor images, logs, and m5out evidence",
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
            prefix="lanl-maa-descriptor-gather-"
        ) as root:
            run_smoke(args, pathlib.Path(root))

    print("LANLMAA descriptor gather smoke: PASS")


if __name__ == "__main__":
    main()
