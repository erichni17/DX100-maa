#!/usr/bin/env python3

import argparse
import pathlib
import re
import subprocess
import tempfile

EXPECTED_EXACT = {
    "logicalItems": 12,
    "physicalLineReads": 3,
    "lineMergeHits": 9,
    "responses": 3,
    "responsesFannedOut": 12,
    "completionsRetired": 12,
    "verificationFailures": 0,
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument(
        "--config",
        default=pathlib.Path(__file__).with_name("gather_smoke.py"),
        type=pathlib.Path,
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="lanl-maa-gather-") as outdir:
        command = [
            str(args.gem5.resolve()),
            f"--outdir={outdir}",
            str(args.config.resolve()),
        ]
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(
                "gem5 gather smoke failed:\n" + result.stdout + result.stderr
            )
        validate(read_stats(pathlib.Path(outdir) / "stats.txt"))

    print("LANLMAA gather smoke: PASS")


if __name__ == "__main__":
    main()
