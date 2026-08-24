#!/usr/bin/env python3

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the immutable DX100 physical-tile baseline"
    )
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text())
    if manifest.get("schema") != "dx100.physical_tile_sweep_baseline.v1":
        raise SystemExit("unsupported baseline schema")
    if manifest.get("valid_points") != 77:
        raise SystemExit("baseline does not contain all 77 valid points")

    root = Path(manifest["root"])
    for name, record in manifest["files"].items():
        path = root / record["path"]
        if not path.is_file():
            raise SystemExit(f"missing {name} baseline file: {path}")
        actual = sha256(path)
        if actual != record["sha256"]:
            raise SystemExit(
                f"{name} baseline hash mismatch: {actual} != {record['sha256']}"
            )

    validation = json.loads(
        (root / manifest["files"]["validation"]["path"]).read_text()
    )
    if validation.get("point_counts", {}).get("valid") != 77:
        raise SystemExit("validation report does not retain 77 valid points")
    if not validation.get("binary_cohort", {}).get("safe"):
        raise SystemExit("baseline simulator cohort is not safe")
    if not validation.get("memory_safety", {}).get("safe"):
        raise SystemExit("baseline memory-safety gate is not safe")

    print(
        "FROZEN_TILE_SWEEP_BASELINE_PASS "
        f"points={manifest['valid_points']} workloads={manifest['workloads']} "
        f"reference_tile={manifest['native_reference_tile']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
