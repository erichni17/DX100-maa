#!/usr/bin/env python3
"""Fail fast when an XRAGE timing arm does not match its exact reference."""

import argparse
import hashlib
import re
from pathlib import Path

EXACT_RE = re.compile(
    r"^MAA_GATHER_VERIFY_PASS length=([0-9]+) hash=([0-9]+)$",
    re.MULTILINE,
)
FATAL_RE = re.compile(
    r"panic|fatal|segmentation fault|MAA_GATHER_VERIFY_FAIL", re.IGNORECASE
)


def fail(message: str) -> None:
    raise SystemExit(f"XRAGE exact-reference validation failed: {message}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_manifest(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator or not key or key in values:
            fail(f"malformed or duplicate manifest line: {line!r}")
        values[key] = value
    return values


def verify_hashes(path: Path) -> dict[Path, str]:
    verified: dict[Path, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        expected, separator, filename = line.partition("  ")
        artifact = Path(filename)
        if (
            not separator
            or not re.fullmatch(r"[0-9a-f]{64}", expected)
            or not artifact.is_file()
            or sha256(artifact) != expected
        ):
            fail(f"missing, changed, or malformed artifact: {filename!r}")
        verified[artifact] = expected
    return verified


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exact_run", type=Path)
    parser.add_argument("--arm", required=True)
    parser.add_argument("--logical", type=int, required=True)
    parser.add_argument("--physical", type=int, required=True)
    parser.add_argument("--index-lines", type=int, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--gem5", type=Path, required=True)
    args = parser.parse_args()

    required = {
        "manifest": args.exact_run / "manifest.txt",
        "source status": args.exact_run / "source_status.txt",
        "artifact hashes": args.exact_run / "artifact_sha256.txt",
        "restore log": args.exact_run / "restore.log",
        "result": args.exact_run / "result.tsv",
    }
    for label, path in required.items():
        if not path.is_file():
            fail(f"exact reference lacks {label}: {path}")
    if not (
        (args.exact_run / "xrage_attribution_smoke.pass").is_file()
        or (args.exact_run / "xrage_checkpoint_recovery.pass").is_file()
    ):
        fail("exact reference lacks a pass marker")
    if required["source status"].read_text(encoding="utf-8"):
        fail("exact reference was produced from a dirty worktree")
    if not args.input.is_file() or not args.gem5.is_file():
        fail("input or gem5 binary is missing")

    artifacts = verify_hashes(required["artifact hashes"])
    manifest = read_manifest(required["manifest"])
    try:
        actual_mechanism = (
            manifest["arm"],
            int(manifest["maa_logical_tile_elements"]),
            int(manifest["physical_tile_elements"]),
            int(manifest["virtual_index_buffer_lines"]),
        )
    except (KeyError, ValueError) as error:
        fail(f"exact reference has incomplete mechanism metadata: {error}")
    expected_mechanism = (
        args.arm,
        args.logical,
        args.physical,
        args.index_lines,
    )
    if actual_mechanism != expected_mechanism:
        fail(
            "mechanism mismatch: "
            f"expected={expected_mechanism}, exact={actual_mechanism}"
        )

    exact_input = Path(manifest.get("input", ""))
    if not exact_input.is_file() or sha256(exact_input) != sha256(args.input):
        fail("performance input differs from exact-reference input")
    gem5_hashes = {
        digest
        for path, digest in artifacts.items()
        if path.name.startswith("gem5")
    }
    if len(gem5_hashes) != 1 or sha256(args.gem5) not in gem5_hashes:
        fail("performance gem5 differs from exact-reference gem5")

    log = required["restore log"].read_text(encoding="utf-8", errors="replace")
    matches = EXACT_RE.findall(log)
    if len(matches) != 1 or FATAL_RE.search(log):
        fail("exact reference lacks one clean exact-output marker")
    print(f"PASS length={matches[0][0]} hash={matches[0][1]}")


if __name__ == "__main__":
    main()
