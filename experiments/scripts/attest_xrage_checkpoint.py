#!/usr/bin/env python3
"""Attest an XRAGE checkpoint whose parent runner lost its exit file."""

import argparse
import hashlib
import re
import subprocess
from pathlib import Path

EXIT_RE = re.compile(r"^Exiting @ tick ([0-9]+) because checkpoint$")
TIME_RE = re.compile(r"^wall=([0-9]+(?:\.[0-9]+)?) rss_kb=([0-9]+)$")
FATAL_RE = re.compile(r"panic|fatal|segmentation fault", re.IGNORECASE)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint_run", type=Path)
    args = parser.parse_args()
    run = args.checkpoint_run.resolve()

    required = [
        run / "manifest.txt",
        run / "artifact_sha256.txt",
        run / "checkpoint.command",
        run / "checkpoint.log",
        run / "checkpoint" / "config.ini",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("missing checkpoint evidence: " + ", ".join(missing))

    exit_file = run / "checkpoint.exit"
    if (
        exit_file.exists()
        and exit_file.read_text(encoding="utf-8").strip() != "0"
    ):
        raise SystemExit("checkpoint.exit records a nonzero status")

    artifact_check = subprocess.run(
        ["sha256sum", "--status", "-c", str(run / "artifact_sha256.txt")],
        check=False,
        cwd=run,
    )
    if artifact_check.returncode != 0:
        raise SystemExit("checkpoint artifact hashes do not match")

    log = (run / "checkpoint.log").read_text(
        encoding="utf-8", errors="replace"
    )
    if FATAL_RE.search(log):
        raise SystemExit("checkpoint log contains a fatal marker")
    exits = [EXIT_RE.match(line) for line in log.splitlines()]
    ticks = [int(match.group(1)) for match in exits if match is not None]
    timings = [TIME_RE.match(line) for line in log.splitlines()]
    timing_values = [match.groups() for match in timings if match is not None]
    if len(ticks) != 1 or len(timing_values) != 1:
        raise SystemExit(
            f"expected one checkpoint exit and timing marker, got "
            f"{len(ticks)} and {len(timing_values)}"
        )

    checkpoint_dirs = sorted(
        path.parent for path in (run / "checkpoint").glob("cpt.*/m5.cpt")
    )
    if len(checkpoint_dirs) != 1:
        raise SystemExit("expected exactly one populated cpt.* checkpoint")
    checkpoint_files = [
        checkpoint_dirs[0] / "m5.cpt",
        checkpoint_dirs[0] / "system.physmem.store0.pmem",
    ]
    if any(
        not path.is_file() or path.stat().st_size == 0
        for path in checkpoint_files
    ):
        raise SystemExit("checkpoint image is missing or empty")

    wall, rss_kb = timing_values[0]
    evidence = required + checkpoint_files
    rows = [
        "field\tvalue",
        "status\tpass",
        f"checkpoint_tick\t{ticks[0]}",
        f"wall_seconds\t{wall}",
        f"rss_kb\t{rss_kb}",
    ]
    for path in evidence:
        rows.append(f"sha256:{path.relative_to(run)}\t{sha256(path)}")
    output = run / "checkpoint_recovery_attestation.tsv"
    temporary = output.with_suffix(".tmp")
    temporary.write_text("\n".join(rows) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(f"PASS XRAGE checkpoint attestation: {run} tick={ticks[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
