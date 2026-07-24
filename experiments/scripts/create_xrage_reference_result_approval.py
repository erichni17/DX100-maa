#!/bin/false
"""Create an immutable post-run approval for a verified XRAGE reference."""

from __future__ import annotations

import argparse
import csv
import ctypes
import errno
import hashlib
import json
import os
import re
import struct
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IN_MODIFY = 0x00000002
IN_ATTRIB = 0x00000004
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_Q_OVERFLOW = 0x00004000
IN_IGNORED = 0x00008000
WATCH_MASK = (
    IN_MODIFY
    | IN_ATTRIB
    | IN_CLOSE_WRITE
    | IN_MOVED_FROM
    | IN_MOVED_TO
    | IN_CREATE
    | IN_DELETE
    | IN_DELETE_SELF
    | IN_MOVE_SELF
)
INOTIFY_EVENT = struct.Struct("iIII")
AUTH_ENV = {
    "HOME": "/data1/nier/.dx-runtime-state/retirement-cache-home",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
}


@dataclass
class CampaignGuard:
    campaign: Path
    campaign_descriptor: int
    watch_descriptor: int
    parent_watch: int
    tree_watches: set[int]
    device: int
    inode: int


def fail(message: str) -> None:
    raise RuntimeError(message)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def regular_file(path: Path, *, empty: bool | None = None) -> Path:
    if path.is_symlink() or not path.is_file():
        fail(f"required regular file is missing or symlinked: {path}")
    if empty is not None and (path.stat().st_size == 0) != empty:
        fail(f"unexpected empty state: {path}")
    return path


def reject_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            fail(f"path contains a symlink component: {path}")


def load_json(path: Path) -> dict[str, Any]:
    regular_file(path, empty=False)
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        fail(f"expected JSON object: {path}")
    return value


def nested(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            fail(f"JSON is missing {'.'.join(keys)}")
        current = current[key]
    return current


def add_watch(descriptor: int, path: Path) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.inotify_add_watch.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint32,
    ]
    libc.inotify_add_watch.restype = ctypes.c_int
    watch = libc.inotify_add_watch(descriptor, os.fsencode(path), WATCH_MASK)
    if watch < 0:
        error = ctypes.get_errno()
        fail(f"inotify_add_watch failed: {os.strerror(error)}")
    return watch


def create_guard(campaign: Path) -> CampaignGuard:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.inotify_init1.argtypes = [ctypes.c_int]
    libc.inotify_init1.restype = ctypes.c_int
    watch_descriptor = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
    if watch_descriptor < 0:
        error = ctypes.get_errno()
        fail(f"inotify_init1 failed: {os.strerror(error)}")
    try:
        parent_watch = add_watch(watch_descriptor, campaign.parent)
        campaign_descriptor = os.open(
            campaign,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        try:
            tree_watches = {add_watch(watch_descriptor, campaign)}
            for directory in sorted(
                path for path in campaign.rglob("*") if path.is_dir()
            ):
                if directory.is_symlink():
                    fail(f"campaign contains a symlink: {directory}")
                tree_watches.add(add_watch(watch_descriptor, directory))
            descriptor_stat = os.fstat(campaign_descriptor)
            guard = CampaignGuard(
                campaign=campaign,
                campaign_descriptor=campaign_descriptor,
                watch_descriptor=watch_descriptor,
                parent_watch=parent_watch,
                tree_watches=tree_watches,
                device=descriptor_stat.st_dev,
                inode=descriptor_stat.st_ino,
            )
            verify_guard(guard)
            return guard
        except BaseException:
            os.close(campaign_descriptor)
            raise
    except BaseException:
        os.close(watch_descriptor)
        raise


def verify_guard(guard: CampaignGuard) -> None:
    while True:
        try:
            data = os.read(guard.watch_descriptor, 64 * 1024)
        except OSError as error:
            if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                break
            fail(f"campaign watch failed: {error}")
        if not data:
            fail("campaign watch closed unexpectedly")
        offset = 0
        while offset < len(data):
            if len(data) - offset < INOTIFY_EVENT.size:
                fail("campaign watch returned a truncated event")
            watch, mask, _, name_length = INOTIFY_EVENT.unpack_from(
                data, offset
            )
            offset += INOTIFY_EVENT.size
            if len(data) - offset < name_length:
                fail("campaign watch returned a truncated name")
            raw_name = data[offset : offset + name_length]
            offset += name_length
            name = raw_name.rstrip(b"\0")
            if mask & (IN_Q_OVERFLOW | IN_IGNORED):
                fail("campaign watch lost event coverage")
            if watch in guard.tree_watches or (
                watch == guard.parent_watch
                and name == os.fsencode(guard.campaign.name)
            ):
                fail("reference campaign changed during approval")
    descriptor_stat = os.fstat(guard.campaign_descriptor)
    path_stat = guard.campaign.lstat()
    if (
        not guard.campaign.is_dir()
        or guard.campaign.is_symlink()
        or descriptor_stat.st_dev != guard.device
        or descriptor_stat.st_ino != guard.inode
        or path_stat.st_dev != guard.device
        or path_stat.st_ino != guard.inode
    ):
        fail("reference campaign directory identity changed")


def read_results(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    regular_file(path, empty=False)
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)
    if reader.fieldnames is None or not rows:
        fail("reference results are empty")
    if any(
        None in row or any(value is None for value in row.values())
        for row in rows
    ):
        fail("reference results are malformed")
    return reader.fieldnames, rows


def evidence_entries(campaign: Path) -> dict[str, str]:
    manifest = regular_file(campaign / "evidence_sha256.txt", empty=False)
    entries: dict[str, str] = {}
    for line_number, line in enumerate(manifest.read_text().splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
        if match is None:
            fail(f"malformed evidence line {line_number}")
        digest, raw_path = match.groups()
        path = Path(raw_path)
        if not path.is_absolute():
            path = campaign / path
        reject_symlink_components(path)
        resolved = regular_file(path.resolve(strict=True))
        try:
            relative = str(resolved.relative_to(campaign))
        except ValueError:
            fail(f"evidence path escapes campaign: {resolved}")
        if relative in entries:
            fail(f"duplicate evidence path: {relative}")
        if file_sha256(resolved) != digest:
            fail(f"evidence hash differs: {relative}")
        entries[relative] = digest
    if not entries:
        fail("reference evidence manifest is empty")
    return entries


def checkpoint_entries(root: Path) -> dict[str, str]:
    manifest = regular_file(
        root / "private_checkpoint_sha256.txt", empty=False
    )
    entries: dict[str, str] = {}
    for line_number, line in enumerate(manifest.read_text().splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
        if match is None:
            fail(f"malformed checkpoint line {line_number}")
        digest, raw_path = match.groups()
        path = Path(raw_path)
        if path.is_absolute() or ".." in path.parts:
            fail(f"unsafe checkpoint path: {path}")
        reject_symlink_components(root / path)
        target = regular_file((root / path).resolve(strict=True))
        try:
            relative = str(target.relative_to(root))
        except ValueError:
            fail(f"checkpoint path escapes root: {target}")
        if relative in entries or file_sha256(target) != digest:
            fail(f"checkpoint evidence differs: {relative}")
        entries[relative] = digest
    actual = {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and path.name != "private_checkpoint_sha256.txt"
    }
    if actual != set(entries):
        fail("checkpoint payload closure differs")
    return entries


def atomic_write(path: Path, content: str) -> None:
    parent = path.parent.resolve(strict=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o444)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("reference_campaign", type=Path)
    parser.add_argument("reference_approval", type=Path)
    parser.add_argument("reference_verifier", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--expected-reference-approval-sha256", required=True)
    parser.add_argument("--expected-reference-verifier-sha256", required=True)
    args = parser.parse_args()

    campaign = args.reference_campaign.resolve(strict=True)
    approval = args.reference_approval.resolve(strict=True)
    verifier = args.reference_verifier.resolve(strict=True)
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        fail(f"output already exists: {output}")
    reject_symlink_components(output.parent)
    regular_file(approval, empty=False)
    regular_file(verifier, empty=False)
    expected_approval_sha = args.expected_reference_approval_sha256
    expected_verifier_sha = args.expected_reference_verifier_sha256
    if (
        SHA256_RE.fullmatch(expected_approval_sha) is None
        or SHA256_RE.fullmatch(expected_verifier_sha) is None
    ):
        fail("expected trust-anchor hashes must be full SHA-256 values")
    if file_sha256(approval) != expected_approval_sha:
        fail("reference approval differs from its trust anchor")
    if file_sha256(verifier) != expected_verifier_sha:
        fail("reference verifier differs from its trust anchor")
    regular_file(campaign / "campaign.pass", empty=True)
    if (campaign / "campaign.fail").exists():
        fail("reference campaign has a fail state")
    for path in campaign.rglob("*"):
        if path.is_symlink():
            fail(f"reference campaign contains a symlink: {path}")

    guard = create_guard(campaign)
    try:
        completed = subprocess.run(
            [
                "/usr/bin/python3",
                "-I",
                str(verifier),
                "xrage",
                str(campaign),
                str(approval),
            ],
            check=False,
            env=AUTH_ENV,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if completed.returncode != 0:
            fail("reference verifier failed: " + completed.stdout.strip())
        verify_guard(guard)
        if file_sha256(approval) != expected_approval_sha:
            fail("reference approval changed during result approval")
        if file_sha256(verifier) != expected_verifier_sha:
            fail("reference verifier changed during result approval")
        manifest_entries = evidence_entries(campaign)
        fields, rows = read_results(campaign / "results.tsv")
        if not {"arm", "replica", "sim_ticks", "valid"}.issubset(fields):
            fail("reference results lack required fields")
        virtual = [row for row in rows if row["arm"] == "virtual"]
        if (
            len(virtual) != 3
            or {row["replica"] for row in virtual} != {"1", "2", "3"}
            or any(row["valid"] != "1" for row in virtual)
        ):
            fail("reference lacks valid virtual replicas 1,2,3")
        ticks = {row["sim_ticks"] for row in virtual}
        if len(ticks) != 1 or not next(iter(ticks)).isdigit():
            fail("reference virtual replicas are not deterministic")
        sim_ticks = int(next(iter(ticks)))
        if sim_ticks <= 0:
            fail("reference simTicks is not positive")

        selected = {
            "results.tsv": campaign / "results.tsv",
            "source.txt": campaign / "source.txt",
            "attribution.tsv": campaign / "attribution.tsv",
            "staged_input_sha256.txt": (campaign / "staged_input_sha256.txt"),
            "checkpoints/virtual/private_checkpoint_sha256.txt": (
                campaign / "checkpoints/virtual/private_checkpoint_sha256.txt"
            ),
        }
        configs = {
            str(replica): (
                campaign / f"runs/virtual/replica_{replica}/config.ini"
            )
            for replica in (1, 2, 3)
        }
        for relative, path in {
            **selected,
            **{
                f"runs/virtual/replica_{replica}/config.ini": path
                for replica, path in configs.items()
            },
        }.items():
            regular_file(path, empty=False)
            if manifest_entries.get(relative) != file_sha256(path):
                fail(f"selected reference evidence differs: {relative}")

        approval_json = load_json(approval)
        binary_commit = nested(approval_json, "candidate", "simulator_commit")
        if (
            not isinstance(binary_commit, str)
            or re.fullmatch(r"[0-9a-f]{40}", binary_commit) is None
        ):
            fail("reference binary commit is malformed")
        checkpoint_root = campaign / "checkpoints/virtual"
        record = {
            "schema_version": 1,
            "experiment_id": "xrage-replicated-reference-result-v1",
            "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "reference_campaign": str(campaign),
            "reference_approval_sha256": expected_approval_sha,
            "reference_verifier_sha256": expected_verifier_sha,
            "binary_simulator_commit": binary_commit,
            "virtual_sim_ticks": sim_ticks,
            "virtual_replicas": [
                {"replica": replica, "sim_ticks": sim_ticks}
                for replica in (1, 2, 3)
            ],
            "evidence": {
                "manifest_sha256": file_sha256(
                    campaign / "evidence_sha256.txt"
                ),
                "results_sha256": file_sha256(selected["results.tsv"]),
                "source_sha256": file_sha256(selected["source.txt"]),
                "attribution_sha256": file_sha256(selected["attribution.tsv"]),
                "staged_input_manifest_sha256": file_sha256(
                    selected["staged_input_sha256.txt"]
                ),
                "virtual_checkpoint_manifest_sha256": file_sha256(
                    selected[
                        "checkpoints/virtual/" "private_checkpoint_sha256.txt"
                    ]
                ),
                "virtual_checkpoint_payload_sha256": checkpoint_entries(
                    checkpoint_root
                ),
                "virtual_config_sha256": {
                    replica: file_sha256(path)
                    for replica, path in configs.items()
                },
            },
        }
        verify_guard(guard)
        if file_sha256(approval) != expected_approval_sha:
            fail("reference approval changed before publication")
        if file_sha256(verifier) != expected_verifier_sha:
            fail("reference verifier changed before publication")
        atomic_write(
            output,
            json.dumps(record, indent=2, sort_keys=True) + "\n",
        )
        verify_guard(guard)
    finally:
        os.close(guard.campaign_descriptor)
        os.close(guard.watch_descriptor)
    print(output)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        raise SystemExit(
            f"reference-result approval failed: {error}"
        ) from error
