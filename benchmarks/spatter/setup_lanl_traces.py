#!/usr/bin/env python3
"""Install checksum-pinned LANL Spatter traces as isolated configurations."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tarfile
import tempfile
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = ROOT / "lanl-traces" / "manifest.json"
DEFAULT_ARCHIVE_ROOT = ROOT / "tests" / "test-data" / "lanl-archives"
DEFAULT_OUTPUT_ROOT = ROOT / "tests" / "test-data" / "lanl"
MAX_DX100_PATTERN_LENGTH = 2_097_152
MAX_DX100_PATTERN_INDEX = 2_147_483_647


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_url(repository: str, commit: str, trace: dict[str, Any]) -> str:
    owner_repo = repository.removeprefix("https://github.com/").rstrip("/")
    relative = "/".join(
        (trace["application"], trace["problem"], trace["archive"])
    )
    return (
        "https://media.githubusercontent.com/media/"
        f"{owner_repo}/{commit}/{relative}"
    )


def archive_path(root: Path, trace: dict[str, Any]) -> Path:
    nested = root / trace["application"] / trace["problem"] / trace["archive"]
    if nested.is_file():
        return nested
    return root / trace["archive"]


def download_archive(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{destination.name}.", dir=destination.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
        try:
            with urllib.request.urlopen(url, timeout=300) as response:
                while chunk := response.read(1024 * 1024):
                    temporary.write(chunk)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
    os.replace(temporary_path, destination)


def read_member(archive: Path, member_name: str) -> bytes:
    with tarfile.open(archive, mode="r:gz") as tar:
        matches = [
            member
            for member in tar.getmembers()
            if member.isfile() and Path(member.name).name == member_name
        ]
        if len(matches) != 1:
            raise ValueError(
                f"{archive}: expected one {member_name!r} member, found {len(matches)}"
            )
        stream = tar.extractfile(matches[0])
        if stream is None:
            raise ValueError(f"{archive}: could not read {matches[0].name}")
        return stream.read()


def validate_config(
    config: Any, source: str, index: int
) -> tuple[str, list[int]]:
    if not isinstance(config, dict):
        raise ValueError(f"{source}[{index}]: configuration must be an object")
    kernel = str(config.get("kernel", "")).lower()
    if kernel not in {"gather", "scatter"}:
        raise ValueError(f"{source}[{index}]: unsupported kernel {kernel!r}")
    pattern = config.get("pattern")
    if not isinstance(pattern, list) or not pattern:
        raise ValueError(
            f"{source}[{index}]: pattern must be a nonempty array"
        )
    if any(not isinstance(value, int) or value < 0 for value in pattern):
        raise ValueError(
            f"{source}[{index}]: pattern indices must be nonnegative ints"
        )
    if len(pattern) > MAX_DX100_PATTERN_LENGTH:
        raise ValueError(
            f"{source}[{index}]: pattern exceeds DX100's "
            f"{MAX_DX100_PATTERN_LENGTH}-element limit"
        )
    if max(pattern) > MAX_DX100_PATTERN_INDEX:
        raise ValueError(
            f"{source}[{index}]: index exceeds DX100's signed 32-bit limit"
        )
    if config.get("count", 1) != 1:
        raise ValueError(f"{source}[{index}]: DX100 requires count=1")
    return kernel, pattern


def write_json(path: Path, value: Any) -> str:
    payload = (json.dumps(value, separators=(",", ":")) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
    return sha256_bytes(payload)


def install_trace(
    trace: dict[str, Any], archive: Path, output_root: Path
) -> list[dict[str, Any]]:
    if archive.stat().st_size != trace["archive_size"]:
        raise ValueError(
            f"{archive}: size mismatch, expected {trace['archive_size']}, "
            f"got {archive.stat().st_size}"
        )
    actual_archive_hash = sha256_file(archive)
    if actual_archive_hash != trace["archive_sha256"]:
        raise ValueError(
            f"{archive}: SHA-256 mismatch, expected {trace['archive_sha256']}, "
            f"got {actual_archive_hash}"
        )

    raw = read_member(archive, trace["member"])
    actual_content_hash = sha256_bytes(raw)
    if actual_content_hash != trace["content_sha256"]:
        raise ValueError(
            f"{archive}: content SHA-256 mismatch, expected "
            f"{trace['content_sha256']}, got {actual_content_hash}"
        )
    configs = json.loads(raw)
    if (
        not isinstance(configs, list)
        or len(configs) != trace["configurations"]
    ):
        raise ValueError(
            f"{archive}: expected {trace['configurations']} configurations"
        )

    stem = trace["member"].removesuffix(".json")
    destination = output_root / trace["application"] / trace["problem"] / stem
    destination.mkdir(parents=True, exist_ok=True)
    expected_files: set[Path] = set()
    installed: list[dict[str, Any]] = []
    kernels: Counter[str] = Counter()

    for index, original in enumerate(configs):
        kernel, pattern = validate_config(original, trace["member"], index)
        kernels[kernel] += 1
        name = f"{trace['application']}_{trace['problem']}_{stem}_{index:02d}_{kernel}"
        config = dict(original)
        config.update({"name": name, "count": 1, "nruns": 1})
        config.setdefault("delta", 8)
        config.setdefault("wrap", 1)

        relative = (
            Path(trace["application"])
            / trace["problem"]
            / stem
            / f"config_{index:02d}_{kernel}.json"
        )
        output = output_root / relative
        expected_files.add(output)
        output_hash = write_json(output, [config])
        unique_indices = len(set(pattern))
        installed.append(
            {
                "id": name,
                "source_archive": trace["archive"],
                "source_member": trace["member"],
                "source_config": index,
                "kernel": kernel,
                "pattern_length": len(pattern),
                "pattern_max": max(pattern),
                "unique_indices": unique_indices,
                "duplicate_indices": len(pattern) - unique_indices,
                "input": relative.as_posix(),
                "input_sha256": output_hash,
            }
        )

    expected_kernels = Counter(trace["kernels"])
    if kernels != expected_kernels:
        raise ValueError(
            f"{archive}: kernel mix mismatch, expected {expected_kernels}, got {kernels}"
        )
    for stale in destination.glob("config_*.json"):
        if stale not in expected_files:
            stale.unlink()
    return installed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT
    )
    parser.add_argument(
        "--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="download missing archives from the pinned GitHub LFS repository",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = json.loads(args.manifest.read_text())
    if manifest.get("schema_version") != 1:
        raise ValueError(
            f"unsupported manifest schema: {manifest.get('schema_version')}"
        )
    source = manifest["source"]
    installed: list[dict[str, Any]] = []

    for trace in manifest["traces"]:
        archive = archive_path(args.archive_root, trace)
        if not archive.is_file():
            if not args.download:
                raise FileNotFoundError(
                    f"missing {archive}; supply --archive-root or use --download"
                )
            url = source_url(source["repository"], source["commit"], trace)
            print(f"[download] {url}", file=sys.stderr)
            download_archive(url, archive)
        installed.extend(install_trace(trace, archive, args.output_root))

    output_manifest = {
        "schema_version": 1,
        "source": source,
        "source_manifest_sha256": sha256_file(args.manifest),
        "validation_data_seed": 1,
        "configurations": installed,
    }
    manifest_hash = write_json(
        args.output_root / "manifest.json", output_manifest
    )
    counts = Counter(item["kernel"] for item in installed)
    print(
        "LANL_TRACE_INSTALL_PASS "
        f"configs={len(installed)} gathers={counts['gather']} "
        f"scatters={counts['scatter']} manifest_sha256={manifest_hash} "
        f"output={args.output_root}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        tarfile.TarError,
    ) as error:
        print(f"LANL_TRACE_INSTALL_FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
