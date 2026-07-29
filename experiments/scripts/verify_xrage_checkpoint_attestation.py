#!/usr/bin/env python3
"""Verify a frozen XRAGE checkpoint against its recovery attestation."""

import argparse
import hashlib
from pathlib import Path


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
    attestation = run / "checkpoint_recovery_attestation.tsv"
    if not attestation.is_file():
        raise SystemExit("checkpoint recovery attestation is missing")

    rows = attestation.read_text(encoding="utf-8").splitlines()
    if not rows or rows[0] != "field\tvalue":
        raise SystemExit("checkpoint recovery attestation has no header")

    values: dict[str, str] = {}
    hashes: dict[Path, str] = {}
    for line in rows[1:]:
        try:
            field, value = line.split("\t", 1)
        except ValueError as error:
            raise SystemExit("malformed checkpoint attestation row") from error
        if field.startswith("sha256:"):
            relative = Path(field.removeprefix("sha256:"))
            if relative.is_absolute() or ".." in relative.parts:
                raise SystemExit("attested checkpoint path escapes its run")
            if relative in hashes:
                raise SystemExit(f"duplicate attested path: {relative}")
            hashes[relative] = value
        else:
            if field in values:
                raise SystemExit(f"duplicate attestation field: {field}")
            values[field] = value

    if values.get("status") != "pass":
        raise SystemExit("checkpoint recovery attestation did not pass")
    for field in ("checkpoint_tick", "rss_kb"):
        if not values.get(field, "").isdigit():
            raise SystemExit(f"invalid attestation field: {field}")
    try:
        if float(values.get("wall_seconds", "")) <= 0:
            raise ValueError
    except ValueError as error:
        raise SystemExit("invalid attestation field: wall_seconds") from error

    checkpoint_dirs = sorted(
        path.parent for path in (run / "checkpoint").glob("cpt.*/m5.cpt")
    )
    if len(checkpoint_dirs) != 1:
        raise SystemExit("expected exactly one populated cpt.* checkpoint")
    required = {
        Path("manifest.txt"),
        Path("artifact_sha256.txt"),
        Path("checkpoint.command"),
        Path("checkpoint.log"),
        Path("checkpoint/config.ini"),
        checkpoint_dirs[0].relative_to(run) / "m5.cpt",
        checkpoint_dirs[0].relative_to(run)
        / "system.physmem.store0.pmem",
    }
    missing = sorted(str(path) for path in required - hashes.keys())
    if missing:
        raise SystemExit("attestation omits: " + ", ".join(missing))

    for relative, expected in hashes.items():
        path = run / relative
        if not path.is_file() or sha256(path) != expected:
            raise SystemExit(f"attested checkpoint hash mismatch: {relative}")

    print(f"PASS XRAGE checkpoint recovery attestation: {run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
