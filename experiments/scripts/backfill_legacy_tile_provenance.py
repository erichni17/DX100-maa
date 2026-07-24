#!/usr/bin/env python3
"""Attest legacy tile outputs to an immutable campaign gem5 manifest.

The old tile runners recorded the executed command in run.log but did not
write gem5_provenance.tsv.  This helper performs a deliberately narrow,
one-time migration.  It is dry-run by default and writes only when all of
these bindings agree:

* the supplied manifest has the caller-supplied SHA-256;
* the manifest's current gem5 binary still has the manifest's SHA-256;
* run.log names that exact binary and that exact output directory; and
* no runner currently owns the legacy output lock.

The sidecar attests binary identity only.  Tile wrappers still independently
validate workload-specific correctness, final stats, and clean m5_exit before
reusing an output.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
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


def load_manifest(path: Path, expected_sha256: str) -> tuple[Path, str]:
    actual_manifest_sha = sha256_file(path)
    if actual_manifest_sha != expected_sha256:
        raise ValueError(
            f"manifest SHA mismatch: expected={expected_sha256} "
            f"actual={actual_manifest_sha} path={path}"
        )
    document = json.loads(path.read_text())
    binary = Path(document["gem5_binary"])
    binary_sha = document["gem5_sha256"]
    if not binary.is_absolute() or not SHA256_RE.fullmatch(binary_sha):
        raise ValueError("manifest gem5_binary/gem5_sha256 is invalid")
    if not binary.is_file() or binary.is_symlink():
        raise ValueError(f"manifest gem5 binary is not a regular file: {binary}")
    actual_binary_sha = sha256_file(binary)
    if actual_binary_sha != binary_sha:
        raise ValueError(
            f"gem5 binary SHA mismatch: manifest={binary_sha} "
            f"actual={actual_binary_sha} path={binary}"
        )
    return binary.resolve(), binary_sha


def command_binding(run_log: Path) -> tuple[Path, Path] | None:
    with run_log.open(errors="replace") as stream:
        for line_number, line in enumerate(stream, start=1):
            if line.startswith("command line: "):
                arguments = shlex.split(line.removeprefix("command line: "))
                if not arguments:
                    return None
                outdirs = [
                    argument.removeprefix("--outdir=")
                    for argument in arguments
                    if argument.startswith("--outdir=")
                ]
                if len(outdirs) != 1:
                    return None
                return Path(arguments[0]).resolve(), Path(outdirs[0]).resolve()
            if line_number >= 1000:
                break
    return None


def sidecar_text(
    *,
    binary: Path,
    binary_sha: str,
    manifest: Path,
    manifest_sha: str,
    outdir: Path,
) -> str:
    return "".join(
        (
            "schema_version\t1\n",
            f"requested_gbin\t{binary.name}\n",
            f"resolved_path\t{binary}\n",
            f"sha256\t{binary_sha}\n",
            f"attestation_manifest\t{manifest}\n",
            f"attestation_manifest_sha256\t{manifest_sha}\n",
            f"attested_command_outdir\t{outdir}\n",
        )
    )


def write_atomic(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o644,
    )
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def inspect_output(
    *,
    outdir: Path,
    binary: Path,
    binary_sha: str,
    manifest: Path,
    manifest_sha: str,
    apply: bool,
) -> str:
    sidecar = outdir / "gem5_provenance.tsv"
    if sidecar.exists():
        return "existing"
    run_log = outdir / "run.log"
    if not run_log.is_file():
        return "no-run-log"
    binding = command_binding(run_log)
    if binding != (binary, outdir.resolve()):
        return "command-mismatch"

    lock_path = outdir.parent / f".{outdir.name}.run.lock"
    if not apply and not lock_path.exists():
        return "would-write"
    lock_flags = os.O_RDWR | (os.O_CREAT if apply else 0)
    lock_descriptor = os.open(lock_path, lock_flags, 0o644)
    try:
        try:
            fcntl.flock(
                lock_descriptor,
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            return "live-owner"
        if sidecar.exists():
            return "existing"
        if apply:
            write_atomic(
                sidecar,
                sidecar_text(
                    binary=binary,
                    binary_sha=binary_sha,
                    manifest=manifest,
                    manifest_sha=manifest_sha,
                    outdir=outdir.resolve(),
                ),
            )
            return "written"
        return "would-write"
    finally:
        os.close(lock_descriptor)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--expected-manifest-sha256",
        required=True,
    )
    parser.add_argument(
        "--campaign-root",
        type=Path,
        action="append",
        required=True,
    )
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    if not SHA256_RE.fullmatch(arguments.expected_manifest_sha256):
        parser.error("--expected-manifest-sha256 must be 64 lowercase hex digits")
    return arguments


def main() -> int:
    arguments = parse_args()
    manifest = arguments.manifest.resolve()
    binary, binary_sha = load_manifest(
        manifest,
        arguments.expected_manifest_sha256,
    )
    counts: dict[str, int] = {}
    for campaign_root in arguments.campaign_root:
        campaign_root = campaign_root.resolve()
        if not campaign_root.is_dir():
            raise ValueError(f"campaign root is not a directory: {campaign_root}")
        for outdir in sorted(campaign_root.iterdir()):
            if not outdir.is_dir() or outdir.is_symlink():
                continue
            outcome = inspect_output(
                outdir=outdir,
                binary=binary,
                binary_sha=binary_sha,
                manifest=manifest,
                manifest_sha=arguments.expected_manifest_sha256,
                apply=arguments.apply,
            )
            counts[outcome] = counts.get(outcome, 0) + 1
            if outcome in {
                "would-write",
                "written",
                "command-mismatch",
                "live-owner",
            }:
                print(f"{outcome}\t{outdir}")
    print(
        json.dumps(
            {
                "apply": arguments.apply,
                "binary": str(binary),
                "binary_sha256": binary_sha,
                "counts": counts,
                "manifest": str(manifest),
                "manifest_sha256": arguments.expected_manifest_sha256,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
