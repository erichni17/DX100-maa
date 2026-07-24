#!/usr/bin/env python3
"""Fail-closed verifier for tile-runner gem5 provenance sidecars."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shlex
import sys
from pathlib import Path


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_sidecar(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    with path.open(newline="") as source:
        for fields in csv.reader(source, delimiter="\t"):
            if len(fields) != 2 or not fields[0] or fields[0] in values:
                raise ValueError(f"invalid provenance sidecar: {path}")
            values[fields[0]] = fields[1]
    return values


def require_absolute(value: str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{label} is not an absolute path")
    return path


def require_sha256(value: str, label: str) -> str:
    if not SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} is not a lowercase SHA-256")
    return value


def command_binding(run_log: Path) -> tuple[Path, Path]:
    commands: list[list[str]] = []
    with run_log.open(errors="replace") as source:
        for line_number, line in enumerate(source, start=1):
            if line.startswith("command line: "):
                commands.append(
                    shlex.split(line.removeprefix("command line: "))
                )
            if line_number >= 1024:
                break
    if len(commands) != 1 or not commands[0]:
        raise ValueError(
            f"expected exactly one gem5 command line near start of {run_log}"
        )
    arguments = commands[0]
    outdirs: list[str] = []
    for index, argument in enumerate(arguments):
        if argument.startswith("--outdir="):
            outdirs.append(argument.split("=", 1)[1])
        elif argument == "--outdir" and index + 1 < len(arguments):
            outdirs.append(arguments[index + 1])
    if len(outdirs) != 1:
        raise ValueError("gem5 command must name exactly one output directory")
    return (
        require_absolute(arguments[0], "gem5 command binary"),
        require_absolute(outdirs[0], "gem5 command output directory"),
    )


def verify(arguments: argparse.Namespace) -> None:
    outdir = require_absolute(str(arguments.outdir), "output directory")
    resolved_path = require_absolute(
        str(arguments.resolved_path), "expected gem5 path"
    )
    expected_sha = require_sha256(
        arguments.sha256, "expected gem5 hash"
    )
    sidecar_path = outdir / "gem5_provenance.tsv"
    if not sidecar_path.is_file():
        raise ValueError(f"provenance sidecar is missing: {sidecar_path}")
    sidecar = read_sidecar(sidecar_path)
    if sidecar.get("requested_gbin") != arguments.requested_gbin:
        raise ValueError("requested gem5 label differs from provenance sidecar")
    if sidecar.get("resolved_path") != str(resolved_path):
        raise ValueError("resolved gem5 path differs from provenance sidecar")
    if sidecar.get("sha256") != expected_sha:
        raise ValueError("gem5 hash differs from provenance sidecar")

    command_binary, command_outdir = command_binding(outdir / "run.log")
    target_outdir = outdir.resolve(strict=True) if outdir.is_symlink() else outdir
    schema_version = sidecar.get("schema_version")
    if schema_version == "2":
        if sidecar.get("output_tag") != arguments.output_tag:
            raise ValueError("gem5 output tag differs from provenance sidecar")
        snapshot = require_absolute(
            sidecar.get("execution_snapshot", ""),
            "execution snapshot",
        )
        if snapshot.is_symlink() or not snapshot.is_file():
            raise ValueError(
                "execution snapshot is not a regular non-symlink file"
            )
        if snapshot.stat().st_mode & 0o222:
            raise ValueError("execution snapshot is writable")
        if sha256_file(snapshot) != expected_sha:
            raise ValueError("execution snapshot hash mismatch")
        expected_binary = snapshot
        expected_outdir = target_outdir
    elif schema_version == "1":
        manifest = require_absolute(
            sidecar.get("attestation_manifest", ""),
            "attestation manifest",
        )
        if manifest.is_symlink() or not manifest.is_file():
            raise ValueError(
                "attestation manifest is not a regular non-symlink file"
            )
        manifest_sha = require_sha256(
            sidecar.get("attestation_manifest_sha256", ""),
            "attestation manifest hash",
        )
        if sha256_file(manifest) != manifest_sha:
            raise ValueError("attestation manifest hash mismatch")
        document = json.loads(manifest.read_text())
        if not isinstance(document, dict):
            raise ValueError("attestation manifest is not a JSON object")
        if document.get("gem5_binary") != str(resolved_path):
            raise ValueError(
                "attestation manifest names a different gem5 binary"
            )
        if document.get("gem5_sha256") != expected_sha:
            raise ValueError("attestation manifest names a different gem5 hash")
        attested_outdir = require_absolute(
            sidecar.get("attested_command_outdir", ""),
            "attested command output directory",
        )
        if attested_outdir != target_outdir:
            raise ValueError(
                "attested command output directory does not match output"
            )
        expected_binary = resolved_path
        expected_outdir = attested_outdir
    else:
        raise ValueError("unsupported provenance sidecar schema")

    if command_binary != expected_binary:
        raise ValueError("run.log command names a different gem5 binary")
    if command_outdir != expected_outdir:
        raise ValueError("run.log command names a different output directory")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--resolved-path", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output-tag", required=True)
    parser.add_argument("--requested-gbin", required=True)
    return parser.parse_args()


def main() -> int:
    verify(parse_args())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
