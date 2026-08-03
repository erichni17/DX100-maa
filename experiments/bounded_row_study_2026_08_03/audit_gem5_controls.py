#!/usr/bin/env python3
"""Fail-closed audit of the frozen native16/native4K gem5 controls."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL: {message}")


def main() -> None:
    here = Path(__file__).resolve().parent
    default_manifest = here / "gem5_control_evidence.json"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=default_manifest)
    parser.add_argument("--evidence-root", type=Path)
    args = parser.parse_args()
    evidence = json.loads(args.manifest.read_text())
    root = args.evidence_root or Path(evidence["evidence_root"])

    for control in evidence["controls"]:
        directory = root / control["directory"]
        require(directory.is_dir(), f"missing control directory {directory}")
        restore_log = directory / "restore.log"
        stats = directory / "run" / "stats.txt"
        require(
            sha256(restore_log) == control["restore_log_sha256"],
            f"{control['name']} restore log hash changed",
        )
        require(
            sha256(stats) == control["stats_sha256"],
            f"{control['name']} stats hash changed",
        )
        require(
            int((directory / "restore.exit").read_text().strip())
            == control["wrapper_exit"],
            f"{control['name']} wrapper did not return the recorded status",
        )
        log = restore_log.read_text(errors="replace")
        require(
            control["exact_output"] in log,
            f"{control['name']} exact output marker is absent",
        )
        require(
            control["terminal_marker"] in log,
            f"{control['name']} terminal m5_exit marker is absent",
        )
        require(
            not any(marker in log.lower() for marker in ("panic:", "fatal:")),
            f"{control['name']} contains a panic/fatal marker",
        )
        with (directory / "result.tsv").open(newline="") as stream:
            row = next(csv.DictReader(stream, delimiter="\t"))
        checks = {
            "simTicks": control["simTicks"],
            "row_table_cache_lines": control["a_line_descriptors"],
            "row_table_unique_cache_lines": control["unique_a_lines"],
            "row_table_rows_inserted": control["row_descriptors"],
            "row_table_unique_rows": control["unique_rows"],
        }
        for field, expected in checks.items():
            require(
                int(row[field]) == expected,
                f"{control['name']} {field} changed: {row[field]} != {expected}",
            )

    print(
        "PASS frozen gem5 controls: exact outputs, terminal markers, hashes, "
        "simTicks, and mechanism counters match"
    )


if __name__ == "__main__":
    main()
