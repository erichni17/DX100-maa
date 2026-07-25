#!/usr/bin/python3
"""Independently verify an XRAGE retirement-cache ablation campaign."""

from __future__ import annotations

import argparse
import csv
import ctypes
import errno
import hashlib
import json
import math
import os
import re
import shlex
import stat
import struct
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FATAL_RE = re.compile(
    r"panic|fatal|assert|abort|segmentation fault|error:|"
    r"MAA_GATHER_VERIFY_FAIL",
    re.IGNORECASE,
)
MARKER_RE = re.compile(
    r"^MAA_GATHER_VERIFY_PASS length=[1-9][0-9]* hash=[0-9]+$"
)
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
CAMPAIGN_WATCH_MASK = (
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
    "DX100_PRIVATE_HOME": os.environ.get(
        "DX100_PRIVATE_HOME",
        "/data1/nier/.dx-runtime-state/retirement-cache-test-home",
    ),
    "HOME": os.environ.get(
        "DX100_PRIVATE_HOME",
        "/data1/nier/.dx-runtime-state/retirement-cache-test-home",
    ),
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_CONFIG_COUNT": "4",
    "GIT_CONFIG_KEY_0": "core.fsmonitor",
    "GIT_CONFIG_VALUE_0": "false",
    "GIT_CONFIG_KEY_1": "core.hooksPath",
    "GIT_CONFIG_VALUE_1": "/dev/null",
    "GIT_CONFIG_KEY_2": "core.untrackedCache",
    "GIT_CONFIG_VALUE_2": "false",
    "GIT_CONFIG_KEY_3": "status.showUntrackedFiles",
    "GIT_CONFIG_VALUE_3": "all",
}
RESULT_FIELDS = (
    "phase",
    "candidate",
    "replica",
    "return_code",
    "sim_ticks",
    "maa_cycles",
    "write_issues",
    "write_completions",
    "cache_hits",
    "cache_misses",
    "cache_writebacks",
    "mshr_misses",
    "blocked_no_mshrs_cycles",
    "blocked_no_mshrs_events",
    "blocked_no_wb_cycles",
    "blocked_no_wb_events",
    "roi_count",
    "exit_count",
    "fatal_count",
    "marker",
    "parse_error",
    "wall_seconds",
    "valid",
)


@dataclass
class CampaignGuard:
    campaign: Path
    campaign_descriptor: int
    watch_descriptor: int
    parent_watch: int
    campaign_watch: int
    tree_watches: set[int]
    device: int
    inode: int
    protected_parent_names: set[bytes]
    absent_sibling: Path | None


def fail(message: str) -> None:
    raise SystemExit(f"ablation verification failed: {message}")


def add_inotify_watch(descriptor: int, path: Path, mask: int) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.inotify_add_watch.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint32,
    ]
    libc.inotify_add_watch.restype = ctypes.c_int
    watch = libc.inotify_add_watch(descriptor, os.fsencode(path), mask)
    if watch < 0:
        error = ctypes.get_errno()
        fail(f"inotify_add_watch failed: {os.strerror(error)}")
    return watch


def create_campaign_guard(campaign: Path) -> CampaignGuard:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.inotify_init1.argtypes = [ctypes.c_int]
    libc.inotify_init1.restype = ctypes.c_int
    watch_descriptor = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
    if watch_descriptor < 0:
        error = ctypes.get_errno()
        fail(f"inotify_init1 failed: {os.strerror(error)}")
    try:
        parent_watch = add_inotify_watch(
            watch_descriptor, campaign.parent, CAMPAIGN_WATCH_MASK
        )
        campaign_descriptor = os.open(
            campaign,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        try:
            campaign_watch = add_inotify_watch(
                watch_descriptor, campaign, CAMPAIGN_WATCH_MASK
            )
            tree_watches = {campaign_watch}
            for directory in sorted(
                path for path in campaign.rglob("*") if path.is_dir()
            ):
                if directory.is_symlink():
                    fail(f"campaign contains a symlink: {directory}")
                tree_watches.add(
                    add_inotify_watch(
                        watch_descriptor,
                        directory,
                        CAMPAIGN_WATCH_MASK,
                    )
                )
            descriptor_stat = os.fstat(campaign_descriptor)
            guard = CampaignGuard(
                campaign=campaign,
                campaign_descriptor=campaign_descriptor,
                watch_descriptor=watch_descriptor,
                parent_watch=parent_watch,
                campaign_watch=campaign_watch,
                tree_watches=tree_watches,
                device=descriptor_stat.st_dev,
                inode=descriptor_stat.st_ino,
                protected_parent_names={os.fsencode(campaign.name)},
                absent_sibling=None,
            )
            return guard
        except BaseException:
            os.close(campaign_descriptor)
            raise
    except BaseException:
        os.close(watch_descriptor)
        raise


def verify_campaign_guard(guard: CampaignGuard) -> None:
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
                and name in guard.protected_parent_names
            ):
                fail("campaign changed during independent verification")
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
        fail("campaign directory identity changed during verification")
    if guard.absent_sibling is not None and (
        guard.absent_sibling.exists() or guard.absent_sibling.is_symlink()
    ):
        fail("campaign publication sibling changed during verification")


def bind_publication_guard(
    guard: CampaignGuard, execution_root: Path, publication_name: str
) -> None:
    publication_path = execution_root.parent / publication_name
    absent_sibling = (
        execution_root
        if guard.campaign.name == publication_name
        else publication_path
    )
    guard.protected_parent_names.update(
        {
            os.fsencode(execution_root.name),
            os.fsencode(publication_name),
        }
    )
    guard.absent_sibling = absent_sibling
    verify_campaign_guard(guard)


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


def contained_regular_file(root: Path, relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        fail(f"unsafe relative path under {root}: {relative}")
    reject_symlink_components(root)
    target = root / relative
    reject_symlink_components(target)
    resolved_root = root.resolve(strict=True)
    resolved = target.resolve(strict=True)
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        fail(f"path escapes its root: root={root} path={relative}")
    return regular_file(resolved)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fd_sha256(descriptor: int) -> str:
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        block = os.read(descriptor, 1024 * 1024)
        if not block:
            break
        digest.update(block)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest.hexdigest()


def run_python_fd(
    script: Path,
    expected_sha256: str,
    arguments: list[str],
) -> subprocess.CompletedProcess[str]:
    descriptor = os.open(script, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if fd_sha256(descriptor) != expected_sha256:
            fail(f"authorized Python program changed: {script}")
        return subprocess.run(
            [
                "/usr/bin/python3",
                "-I",
                f"/proc/self/fd/{descriptor}",
                *arguments,
            ],
            check=False,
            env=AUTH_ENV,
            pass_fds=(descriptor,),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    finally:
        os.close(descriptor)


def verify_directory_identity(
    path: Path, descriptor: int, device: int, inode: int
) -> None:
    current = path.lstat()
    opened = os.fstat(descriptor)
    if (
        path.is_symlink()
        or not path.is_dir()
        or current.st_dev != device
        or current.st_ino != inode
        or opened.st_dev != device
        or opened.st_ino != inode
    ):
        fail(f"certificate publication directory changed: {path}")


def link_fd_noreplace(
    descriptor: int, parent_fd: int, destination_name: str
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    linkat = libc.linkat
    linkat.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    linkat.restype = ctypes.c_int
    if (
        linkat(
            -100,  # AT_FDCWD
            os.fsencode(f"/proc/self/fd/{descriptor}"),
            parent_fd,
            os.fsencode(destination_name),
            0x400,  # AT_SYMLINK_FOLLOW
        )
        != 0
    ):
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            fail(
                "certificate output was created concurrently: "
                f"{destination_name}"
            )
        fail(
            "could not publish certificate from its open inode: "
            f"{os.strerror(error)}"
        )


def atomic_write_noreplace(path: Path, content: str) -> None:
    requested = path.absolute()
    if requested.name in {"", ".", ".."}:
        fail("certificate output must have a safe file name")
    reject_symlink_components(requested.parent)
    parent = requested.parent.resolve(strict=True)
    parent_fd = os.open(
        parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    parent_info = os.fstat(parent_fd)
    descriptor = -1
    try:
        descriptor = os.open(
            ".",
            os.O_WRONLY | os.O_TMPFILE | os.O_CLOEXEC,
            0o400,
            dir_fd=parent_fd,
        )
        encoded = content.encode("utf-8")
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
        verify_directory_identity(
            parent, parent_fd, parent_info.st_dev, parent_info.st_ino
        )
        link_fd_noreplace(descriptor, parent_fd, requested.name)
        source_info = os.fstat(descriptor)
        destination_info = os.stat(
            requested.name, dir_fd=parent_fd, follow_symlinks=False
        )
        if (
            source_info.st_dev != destination_info.st_dev
            or source_info.st_ino != destination_info.st_ino
        ):
            fail("published certificate inode differs from the open source")
        os.fsync(parent_fd)
        verify_directory_identity(
            parent, parent_fd, parent_info.st_dev, parent_info.st_ino
        )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)


def publish_verification_certificate(
    certificate: Path, content: str, campaign_guard: CampaignGuard
) -> None:
    atomic_write_noreplace(certificate, content)
    certificate_info = certificate.lstat()
    try:
        verify_campaign_guard(campaign_guard)
    except BaseException:
        try:
            current = certificate.lstat()
            if (
                not certificate.is_symlink()
                and stat.S_ISREG(current.st_mode)
                and current.st_dev == certificate_info.st_dev
                and current.st_ino == certificate_info.st_ino
            ):
                certificate.unlink()
        except FileNotFoundError:
            pass
        raise


def validate_certificate_output(
    certificate: Path,
    campaign: Path,
    execution_root: Path,
    publication_name: str,
) -> None:
    protected_publication_paths = {
        execution_root,
        execution_root.parent / publication_name,
    }
    if certificate in protected_publication_paths:
        fail("verification certificate collides with a publication path")
    try:
        certificate.relative_to(campaign)
    except ValueError:
        pass
    else:
        fail("verification certificate must be outside the campaign")


def correctness_campaign_fingerprint(campaign: Path) -> str:
    reject_symlink_components(campaign)
    campaign = campaign.resolve(strict=True)
    required = [
        campaign / "campaign.pass",
        campaign / "artifact_sha256.txt",
        campaign / "results.tsv",
        campaign / "source.txt",
    ]
    for arm in ("native", "fused", "virtual"):
        required.extend(
            campaign / "runs" / arm / name
            for name in (
                "restore.command",
                "restore.exit",
                "restore.log",
                "result.tsv",
                "stats.txt",
                "config.ini",
                "config.json",
                "validation.pass",
            )
        )
    checkpoint_manifests = sorted(
        campaign.glob("checkpoints/*/checkpoint_sha256.txt")
    )
    if {path.parent.name for path in checkpoint_manifests} != {
        "native",
        "fused",
        "virtual",
    }:
        fail(f"correctness checkpoint-manifest closure differs: {campaign}")
    digest = hashlib.sha256()
    for path in sorted((*required, *checkpoint_manifests)):
        reject_symlink_components(path)
        regular_file(path)
        relative = str(path.relative_to(campaign)).encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(file_sha256(path)))
    return digest.hexdigest()


def expect_hash(path: Path, expected: str) -> None:
    regular_file(path)
    if not SHA256_RE.fullmatch(expected) or file_sha256(path) != expected:
        fail(f"SHA-256 mismatch: {path}")


def load_json(path: Path) -> dict[str, Any]:
    regular_file(path, empty=False)
    try:
        value = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        fail(f"invalid JSON in {path}: {error}")
    if not isinstance(value, dict):
        fail(f"expected JSON object: {path}")
    return value


def git_output(
    repository: Path, *arguments: str, text: bool = True
) -> str | bytes:
    return subprocess.run(
        ["/usr/bin/git", "-C", str(repository), *arguments],
        check=True,
        env=AUTH_ENV,
        text=text,
        stdout=subprocess.PIPE,
    ).stdout


def verify_git_state(repository: Path, expected_commit: str) -> None:
    head = str(
        git_output(repository, "rev-parse", "--verify", "HEAD^{commit}")
    )
    if head.strip() != expected_commit:
        fail("workflow/config worktree commit differs")
    if str(
        git_output(
            repository,
            "-c",
            "status.showUntrackedFiles=all",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
    ):
        fail("workflow/config worktree is dirty")
    replacements = str(
        git_output(
            repository,
            "for-each-ref",
            "--format=%(refname)",
            "refs/replace",
        )
    )
    if replacements.strip():
        fail("Git replacement refs are forbidden")
    raw_common = str(
        git_output(repository, "rev-parse", "--git-common-dir")
    ).strip()
    common = Path(raw_common)
    if not common.is_absolute():
        common = repository / common
    common = common.resolve(strict=True)
    if (common / "info/grafts").exists():
        fail("legacy Git grafts are forbidden")


def nested(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            fail(f"JSON is missing {'.'.join(keys)}")
        current = current[key]
    return current


def read_tsv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    regular_file(path, empty=False)
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)
    if reader.fieldnames is None or not rows:
        fail(f"empty TSV: {path}")
    if any(
        None in row or any(value is None for value in row.values())
        for row in rows
    ):
        fail(f"malformed TSV: {path}")
    return reader.fieldnames, rows


def verify_evidence(campaign: Path) -> None:
    manifest = regular_file(campaign / "evidence_sha256.txt", empty=False)
    covered: set[Path] = set()
    for line_number, line in enumerate(manifest.read_text().splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
        if match is None:
            fail(f"malformed evidence line {line_number}")
        digest, raw = match.groups()
        relative = Path(raw)
        if relative.is_absolute() or ".." in relative.parts:
            fail(f"unsafe evidence path: {relative}")
        path = (campaign / relative).resolve(strict=True)
        try:
            path.relative_to(campaign)
        except ValueError:
            fail(f"evidence path escapes campaign: {path}")
        if path in covered:
            fail(f"duplicate evidence path: {path}")
        expect_hash(path, digest)
        covered.add(path)
    expected = {
        path.resolve()
        for path in campaign.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and "inputs" not in path.relative_to(campaign).parts
        and path.name
        not in {
            "campaign.pass",
            "campaign.fail",
            "execution.complete",
            "evidence_sha256.txt",
        }
    }
    if covered != expected:
        fail("evidence manifest does not exactly cover non-input evidence")


def verify_staged_inputs(campaign: Path) -> dict[str, str]:
    root = campaign / "inputs"
    manifest = load_json(campaign / "staged_input_sha256.json")
    actual = {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual != set(manifest):
        fail("staged input closure differs")
    for relative, expected in manifest.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            fail("staged input manifest has invalid types")
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            fail(f"unsafe staged path: {relative}")
        expect_hash(root / path, expected)
    return {str(key): str(value) for key, value in manifest.items()}


def verify_checkpoint(root: Path, manifest_name: str) -> dict[str, str]:
    reject_symlink_components(root)
    manifest = contained_regular_file(root, Path(manifest_name))
    if manifest.stat().st_size == 0:
        fail(f"empty checkpoint manifest: {manifest}")
    entries: dict[Path, str] = {}
    for line_number, line in enumerate(manifest.read_text().splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
        if match is None:
            fail(f"malformed checkpoint line {line_number}: {manifest}")
        digest, raw = match.groups()
        relative = Path(raw)
        path = contained_regular_file(root, relative)
        if path in entries:
            fail(f"duplicate checkpoint path: {relative}")
        entries[path] = digest
    checkpoint_dirs = {
        path.parent
        for path in root.glob("*/m5.cpt")
        if path.is_file() and not path.is_symlink()
    }
    if len(checkpoint_dirs) != 1:
        fail(f"expected one checkpoint directory: {root}")
    checkpoint_dir = checkpoint_dirs.pop()
    actual = {
        path
        for path in checkpoint_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual != set(entries):
        fail(f"checkpoint closure differs: {root}")
    for path, digest in entries.items():
        expect_hash(path, digest)
    return {
        str(path.relative_to(root)): digest for path, digest in entries.items()
    }


def verify_source_correctness(
    campaign: Path, expected_fingerprint: str
) -> str:
    if correctness_campaign_fingerprint(campaign) != expected_fingerprint:
        fail(f"source correctness fingerprint differs: {campaign}")
    regular_file(campaign / "campaign.pass", empty=True)
    if (campaign / "campaign.fail").exists():
        fail(f"source correctness campaign has fail state: {campaign}")
    fields, rows = read_tsv(campaign / "results.tsv")
    if tuple(fields) != (
        "arm",
        "rc",
        "marker_count",
        "roi_count",
        "write_issues",
        "write_completions",
        "valid",
        "marker",
    ):
        fail(f"source correctness schema differs: {campaign}")
    virtual = [row for row in rows if row["arm"] == "virtual"]
    if len(virtual) != 1:
        fail(f"source correctness lacks one virtual row: {campaign}")
    row = virtual[0]
    if (
        row["rc"] != "0"
        or row["marker_count"] != "1"
        or row["roi_count"] != "1"
        or row["valid"] != "1"
        or not MARKER_RE.fullmatch(row["marker"])
        or not row["write_issues"].isdigit()
        or row["write_issues"] == "0"
        or row["write_issues"] != row["write_completions"]
    ):
        fail(f"source correctness virtual row is invalid: {campaign}")
    verify_checkpoint(
        campaign / "checkpoints/virtual", "checkpoint_sha256.txt"
    )
    return row["marker"]


def semantic_config_sha256(path: Path) -> str:
    section = ""
    normalized: list[str] = []
    for line in regular_file(path).read_text().splitlines():
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        if re.fullmatch(
            r"system\.redirect_paths[0-9]+", section
        ) and line.startswith("host_paths="):
            line = "host_paths=<REPLICA_RUNTIME_ROOT>"
        normalized.append(line)
    return hashlib.sha256(("\n".join(normalized) + "\n").encode()).hexdigest()


def staged_reference_result(
    campaign: Path,
    source: dict[str, Any],
    expected_reference_approval_sha: str,
    expected_reference_verifier_sha: str,
) -> dict[str, Any]:
    result_approval = load_json(
        campaign / "inputs/manifests/reference_result_approval.json"
    )
    if (
        result_approval.get("schema_version") != 2
        or result_approval.get("experiment_id")
        != "xrage-replicated-reference-result-v2"
        or result_approval.get("reference_campaign")
        != source.get("reference_campaign")
        or result_approval.get("reference_approval_sha256")
        != expected_reference_approval_sha
        or result_approval.get("reference_verifier_sha256")
        != expected_reference_verifier_sha
        or result_approval.get("binary_simulator_commit")
        != source.get("binary_simulator_commit")
    ):
        fail("staged reference-result approval identity differs")
    evidence = result_approval.get("evidence")
    if not isinstance(evidence, dict):
        fail("staged reference-result evidence is malformed")
    config_hashes = evidence.get("virtual_config_sha256")
    if not isinstance(config_hashes, dict) or set(config_hashes) != {
        "1",
        "2",
        "3",
    }:
        fail("staged reference-result config closure differs")
    config_paths = {
        str(replica): (
            campaign
            / "inputs/reference_evidence"
            / f"virtual_config_replica_{replica}.ini"
        )
        for replica in (1, 2, 3)
    }
    for replica, path in config_paths.items():
        expect_hash(path, str(config_hashes[replica]))
    semantic_hash = evidence.get("virtual_config_semantic_sha256")
    if (
        not isinstance(semantic_hash, str)
        or SHA256_RE.fullmatch(semantic_hash) is None
        or {semantic_config_sha256(path) for path in config_paths.values()}
        != {semantic_hash}
    ):
        fail("staged reference replica configs are not semantically equal")
    raw_evidence = {
        "manifest_sha256": (
            campaign / "inputs/reference_evidence/evidence_sha256.txt"
        ),
        "results_sha256": (campaign / "inputs/reference_evidence/results.tsv"),
        "source_sha256": campaign / "inputs/reference_evidence/source.txt",
        "attribution_sha256": (
            campaign / "inputs/reference_evidence/attribution.tsv"
        ),
        "staged_input_manifest_sha256": (
            campaign / "inputs/reference_evidence/staged_input_sha256.txt"
        ),
        "virtual_checkpoint_manifest_sha256": (
            campaign
            / "inputs/reference_evidence/private_checkpoint_sha256.txt"
        ),
    }
    for key, path in raw_evidence.items():
        expected = evidence.get(key)
        if not isinstance(expected, str):
            fail(f"staged reference-result hash is malformed: {key}")
        expect_hash(path, expected)

    fields, rows = read_tsv(raw_evidence["results_sha256"])
    virtual = [row for row in rows if row.get("arm") == "virtual"]
    if (
        not {"arm", "replica", "sim_ticks", "valid"}.issubset(fields)
        or len(virtual) != 3
        or {row["replica"] for row in virtual} != {"1", "2", "3"}
        or any(row["valid"] != "1" for row in virtual)
        or {row["sim_ticks"] for row in virtual}
        != {str(result_approval.get("virtual_sim_ticks"))}
    ):
        fail("staged raw reference results differ from approval")
    checkpoint_root = campaign / "inputs/checkpoints/full_performance"
    staged_checkpoint = verify_checkpoint(
        checkpoint_root, "checkpoint_sha256.txt"
    )
    if evidence.get("virtual_checkpoint_payload_sha256") != staged_checkpoint:
        fail("staged reference checkpoint approval differs")
    replicas = result_approval.get("virtual_replicas")
    if (
        not isinstance(replicas, list)
        or replicas
        != [
            {
                "replica": replica,
                "sim_ticks": result_approval.get("virtual_sim_ticks"),
            }
            for replica in (1, 2, 3)
        ]
        or not isinstance(result_approval.get("virtual_sim_ticks"), int)
        or int(result_approval["virtual_sim_ticks"]) <= 0
    ):
        fail("staged reference-result replica approval differs")
    return result_approval


def first_stats(path: Path) -> dict[str, int]:
    sections: list[list[str]] = []
    current: list[str] | None = None
    for line in regular_file(path).read_text().splitlines():
        if line.startswith("---------- Begin Simulation Statistics"):
            if current is not None:
                fail(f"nested stats section: {path}")
            current = []
        elif line.startswith("---------- End Simulation Statistics"):
            if current is None:
                fail(f"unmatched stats terminator: {path}")
            sections.append(current)
            current = None
        elif current is not None:
            current.append(line)
    if current is not None or len(sections) != 2:
        fail(f"expected exactly two stats sections: {path}")

    values = {
        "sim_ticks": -1,
        "maa_cycles": -1,
        "write_issues": 0,
        "write_completions": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "cache_writebacks": 0,
        "mshr_misses": 0,
        "blocked_no_mshrs_cycles": 0,
        "blocked_no_mshrs_events": 0,
        "blocked_no_wb_cycles": 0,
        "blocked_no_wb_events": 0,
    }
    seen: dict[str, int] = {}
    patterns = (
        ("write_issues", r"system\.maa\.I[0-9]+_IND_VirtWriteIssues"),
        (
            "write_completions",
            r"system\.maa\.I[0-9]+_IND_VirtWriteCompletions",
        ),
        (
            "cache_hits",
            r"system\.maa_retirement_caches[0-9]+\.demandHits_8::maa",
        ),
        (
            "cache_misses",
            r"system\.maa_retirement_caches[0-9]+\.demandMisses_8::maa",
        ),
        (
            "cache_writebacks",
            r"system\.maa_retirement_caches[0-9]+"
            r"\.writebacks_8::writebacks",
        ),
        (
            "mshr_misses",
            r"system\.maa_retirement_caches[0-9]+"
            r"\.demandMshrMisses_8::maa",
        ),
        (
            "blocked_no_mshrs_cycles",
            r"system\.maa_retirement_caches[0-9]+"
            r"\.blockedCycles_T::no_mshrs",
        ),
        (
            "blocked_no_mshrs_events",
            r"system\.maa_retirement_caches[0-9]+"
            r"\.blockedCauses_T::no_mshrs",
        ),
        (
            "blocked_no_wb_cycles",
            r"system\.maa_retirement_caches[0-9]+" r"\.blockedCycles_T::no_wb",
        ),
        (
            "blocked_no_wb_events",
            r"system\.maa_retirement_caches[0-9]+" r"\.blockedCauses_T::no_wb",
        ),
    )
    for line in sections[0]:
        fields = line.split()
        if len(fields) < 2 or not re.fullmatch(r"-?[0-9]+", fields[1]):
            continue
        name, raw = fields[:2]
        value = int(raw)
        if name == "simTicks":
            key = "sim_ticks"
        elif name == "system.maa.cycles_TOTAL":
            key = "maa_cycles"
        else:
            key = next(
                (
                    candidate
                    for candidate, pattern in patterns
                    if re.fullmatch(pattern, name)
                ),
                "",
            )
        if not key:
            continue
        seen[key] = seen.get(key, 0) + 1
        if key in ("sim_ticks", "maa_cycles"):
            values[key] = value
        else:
            values[key] += value
    if seen.get("sim_ticks") != 1 or seen.get("maa_cycles") != 1:
        fail(f"missing unique timing metrics: {path}")
    if seen.get("write_issues", 0) <= 0 or seen.get(
        "write_issues"
    ) != seen.get("write_completions"):
        fail(f"unbalanced virtual-write stat keys: {path}")
    for key in ("cache_misses", "cache_writebacks", "mshr_misses"):
        if seen.get(key) != 4:
            fail(f"expected four values for {key}: {path}")
    if seen.get("cache_hits", 0) not in (0, 4):
        fail(f"expected zero or four cache-hit values: {path}")
    if (
        values["sim_ticks"] <= 0
        or values["maa_cycles"] <= 0
        or values["write_issues"] <= 0
        or values["write_issues"] != values["write_completions"]
        or values["cache_hits"] + values["cache_misses"]
        != values["write_issues"]
        or values["cache_writebacks"] > values["cache_misses"]
        or values["mshr_misses"] > values["cache_misses"]
    ):
        fail(f"inconsistent virtual-retirement accounting: {path}")
    return values


def candidate_map(source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = source.get("candidates")
    if not isinstance(raw, list):
        fail("source candidate list is malformed")
    candidates: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            fail("source candidate is malformed")
        candidates[item["name"]] = item
    if tuple(candidates) != ("reference", "targets1", "compact"):
        fail("source candidate matrix or ordering changed")
    expected = {
        "reference": ("1kB", 4, 1, 16, 16, 16),
        "targets1": ("1kB", 4, 1, 16, 1, 16),
        "compact": ("256B", 4, 1, 16, 1, 16),
    }
    for name, values in expected.items():
        item = candidates[name]
        actual = (
            item.get("size"),
            item.get("assoc"),
            item.get("response_latency"),
            item.get("mshrs"),
            item.get("targets_per_mshr"),
            item.get("write_buffers"),
        )
        if actual != values:
            fail(f"candidate definition changed: {name}")
    return candidates


def committed_config_hashes(repository: Path, commit: str) -> dict[str, str]:
    raw_paths = subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(repository),
            "ls-tree",
            "-rz",
            "--name-only",
            commit,
            "--",
            "configs",
        ],
        check=True,
        env=AUTH_ENV,
        stdout=subprocess.PIPE,
    ).stdout.split(b"\0")
    expected: dict[str, str] = {}
    for raw in raw_paths:
        if not raw:
            continue
        relative = Path(os.fsdecode(raw))
        content = subprocess.run(
            [
                "/usr/bin/git",
                "-C",
                str(repository),
                "show",
                f"{commit}:{relative}",
            ],
            check=True,
            env=AUTH_ENV,
            stdout=subprocess.PIPE,
        ).stdout
        expected[str(Path("simulator") / relative)] = hashlib.sha256(
            content
        ).hexdigest()
    if not expected:
        fail("authorized simulator commit contains no configs")
    return expected


def command_difference(actual: list[str], expected: list[str]) -> str:
    for index in range(max(len(actual), len(expected))):
        actual_token = actual[index] if index < len(actual) else "<missing>"
        expected_token = (
            expected[index] if index < len(expected) else "<missing>"
        )
        if actual_token != expected_token:
            return (
                f"token={index} actual={actual_token!r} "
                f"expected={expected_token!r} "
                f"actual_length={len(actual)} expected_length={len(expected)}"
            )
    return "unknown difference"


def expected_command(
    campaign: Path,
    execution_root: Path,
    root: Path,
    phase: str,
    candidate: dict[str, Any],
) -> list[str]:
    inputs = execution_root / "inputs"
    execution_run_root = execution_root / root.relative_to(campaign)
    binary = (
        inputs / "benchmark/xrage_virtual"
        if phase == "full_performance"
        else inputs / "benchmark/xrage_virtual_verify"
    )
    data = (
        inputs / "benchmark/xrage_20k.json"
        if phase == "screen_correctness"
        else inputs / "benchmark/xrage_full.json"
    )
    return [
        str(inputs / "bin/gem5.opt"),
        "--listener-mode=off",
        f"--outdir={execution_run_root}",
        str(inputs / "simulator/configs/deprecated/example/se.py"),
        "--cpu-type",
        "X86O3CPU",
        "-r",
        "1",
        "-n",
        "4",
        "--mem-size",
        "2GB",
        "--sys-clock",
        "3.2GHz",
        "--cpu-clock",
        "3.2GHz",
        "--caches",
        "--l1d_size=32kB",
        "--l1d_assoc=8",
        "--l1d-hwp-type=StridePrefetcher",
        "--l1d_mshrs=16",
        "--l1d_write_buffers=8",
        "--l1i_size=32kB",
        "--l1i_assoc=8",
        "--l1i-hwp-type=StridePrefetcher",
        "--l1i_mshrs=16",
        "--l1i_write_buffers=8",
        "--l2cache",
        "--l2_size=256kB",
        "--l2_assoc=4",
        "--l2-hwp-type=StridePrefetcher",
        "--l2_mshrs=32",
        "--l2_write_buffers=16",
        "--l3cache",
        "--l3_size=8MB",
        "--l3_assoc=16",
        "--l3_mshrs=256",
        "--l3_write_buffers=128",
        "--l3_ports=4",
        "--cacheline_size=64",
        "--mem-type",
        "Ramulator2",
        "--ramulator-config",
        str(inputs / "ramulator.yaml"),
        "--mem-channels=2",
        "--maa_ncbus_width=32",
        "--maa",
        "--maa_num_maas=1",
        "--maa_num_tile_elements=16384",
        "--maa_l2_uncacheable",
        "--maa_l3_uncacheable",
        "--maa_num_initial_row_table_slices=32",
        "--maa_virtual_combine_slots=384",
        "--maa_virtual_combine_words=4096",
        "--maa_virtual_combine_ways=4",
        "--maa_virtual_combine_banks=4",
        "--maa_virtual_response_slots=96",
        "--maa_virtual_response_word_pool=480",
        "--maa_virtual_words_per_cycle=4",
        "--maa_virtual_max_outstanding_writes=64",
        "--maa_virtual_masked_writes",
        f"--maa_retirement_cache_size={candidate['size']}",
        f"--maa_retirement_cache_assoc={candidate['assoc']}",
        (
            "--maa_retirement_cache_response_latency="
            f"{candidate['response_latency']}"
        ),
        f"--maa_retirement_cache_mshrs={candidate['mshrs']}",
        (
            "--maa_retirement_cache_targets_per_mshr="
            f"{candidate['targets_per_mshr']}"
        ),
        (
            "--maa_retirement_cache_write_buffers="
            f"{candidate['write_buffers']}"
        ),
        "--cmd",
        str(binary),
        "--options",
        f"-f {data}",
    ]


def verify_cache_config(path: Path, candidate: dict[str, Any]) -> None:
    sections: dict[str, dict[str, str]] = {}
    section: str | None = None
    for line in regular_file(path).read_text().splitlines():
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            sections[section] = {}
        elif section is not None and "=" in line:
            key, value = line.split("=", 1)
            sections[section][key] = value
    banks = [
        values
        for name, values in sections.items()
        if re.fullmatch(r"system\.maa_retirement_caches[0-9]+", name)
    ]
    if len(banks) != 4:
        fail(f"expected four effective retirement caches: {path}")
    expected = {
        "size": str({"1kB": 1024, "256B": 256}[str(candidate["size"])]),
        "assoc": str(candidate["assoc"]),
        "response_latency": str(candidate["response_latency"]),
        "mshrs": str(candidate["mshrs"]),
        "tgts_per_mshr": str(candidate["targets_per_mshr"]),
        "write_buffers": str(candidate["write_buffers"]),
    }
    for bank in banks:
        for key, value in expected.items():
            if bank.get(key) != value:
                fail(
                    f"effective cache config differs for {key}: "
                    f"expected={value} actual={bank.get(key)!r} path={path}"
                )


def runtime_config_identity(path: Path) -> dict[str, str]:
    sections: dict[str, dict[str, str]] = {}
    section: str | None = None
    for line in regular_file(path).read_text().splitlines():
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            sections[section] = {}
        elif section is not None and "=" in line:
            key, value = line.split("=", 1)
            sections[section][key] = value
    workload = sections.get("system.cpu0.workload")
    if workload is None or not {"cmd", "cwd", "executable"}.issubset(workload):
        fail(f"workload identity is incomplete in config: {path}")
    command = shlex.split(workload["cmd"])
    if (
        len(command) != 3
        or command[1] != "-f"
        or command[0] != workload["executable"]
    ):
        fail(f"unexpected workload command in config: {path}")
    controller_names = {"system.mem_ctrls0", "system.mem_ctrls1"}
    if not controller_names.issubset(sections):
        fail(f"memory-controller identity is incomplete in config: {path}")
    ramulator_paths = {
        sections[name].get("config_path") for name in controller_names
    }
    if None in ramulator_paths or len(ramulator_paths) != 1:
        fail(f"Ramulator config identity differs across controllers: {path}")
    return {
        "executable": workload["executable"],
        "input": command[2],
        "cwd": workload["cwd"],
        "ramulator_config": str(next(iter(ramulator_paths))),
    }


def authorized_execution_root(campaign: Path, source: dict[str, Any]) -> Path:
    raw_root = source.get("execution_root")
    publication_name = source.get("publication_name")
    if (
        not isinstance(raw_root, str)
        or not isinstance(publication_name, str)
        or Path(publication_name).name != publication_name
    ):
        fail("publication identity is malformed")
    execution_root = Path(raw_root)
    if (
        not execution_root.is_absolute()
        or execution_root.parent != campaign.parent
        or re.fullmatch(
            rf"\.staging\.{re.escape(publication_name)}\.[0-9a-f]{{32}}",
            execution_root.name,
        )
        is None
    ):
        fail("execution-root identity is malformed")
    if campaign.name == execution_root.name:
        if campaign != execution_root:
            fail("prepublication execution-root path differs")
        publication_path = campaign.parent / publication_name
        if publication_path.exists() or publication_path.is_symlink():
            fail("prepublication publication path already exists")
    elif campaign.name == publication_name:
        if execution_root.exists() or execution_root.is_symlink():
            fail("postpublication execution-root path still exists")
    else:
        fail("campaign name does not match its publication identity")
    return execution_root


def expected_staged_runtime(
    execution_root: Path, phase: str
) -> dict[str, str]:
    inputs = execution_root / "inputs"
    if phase == "screen_correctness":
        binary = inputs / "benchmark/xrage_virtual_verify"
        data = inputs / "benchmark/xrage_20k.json"
    elif phase == "full_correctness":
        binary = inputs / "benchmark/xrage_virtual_verify"
        data = inputs / "benchmark/xrage_full.json"
    elif phase == "full_performance":
        binary = inputs / "benchmark/xrage_virtual"
        data = inputs / "benchmark/xrage_full.json"
    else:
        fail(f"unknown config phase: {phase}")
    return {
        "executable": str(binary),
        "input": str(data),
        "cwd": str(inputs / "simulator"),
        "ramulator_config": str(inputs / "ramulator.yaml"),
    }


def normalized_treatment_config(
    path: Path, expected_runtime: dict[str, str]
) -> str:
    if runtime_config_identity(path) != expected_runtime:
        fail(f"runtime artifact identity differs in config: {path}")
    lines = regular_file(path).read_text().splitlines()
    retirement_cache_sizes: list[int] = []
    section = ""
    for line in lines:
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif re.fullmatch(
            r"system\.maa_retirement_caches[0-9]+", section
        ) and line.startswith("size="):
            try:
                retirement_cache_sizes.append(int(line.split("=", 1)[1]))
            except ValueError:
                fail(f"invalid retirement-cache size in config: {path}")
    if len(retirement_cache_sizes) != 4:
        fail(f"expected four retirement-cache sizes in config: {path}")
    retirement_cache_bytes = sum(retirement_cache_sizes)

    normalized: list[str] = []
    section = ""
    for line in lines:
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        if "=" in line:
            key, value = line.split("=", 1)
            if (
                re.fullmatch(r"system\.redirect_paths[0-9]+", section)
                and key == "host_paths"
            ):
                line = "host_paths=<RUNTIME_ROOT>"
            elif section == "system.cpu0.workload" and key == "cmd":
                line = "cmd=<WORKLOAD_EXECUTABLE> -f <WORKLOAD_INPUT>"
            elif section == "system.cpu0.workload" and key in {
                "cwd",
                "executable",
            }:
                line = f"{key}=<WORKLOAD_{key.upper()}>"
            elif (
                re.fullmatch(r"system\.mem_ctrls[0-9]+", section)
                and key == "config_path"
            ):
                line = "config_path=<RAMULATOR_CONFIG>"
            elif (
                re.fullmatch(r"system\.maa_retirement_caches[0-9]+", section)
                and key in {"size", "tgts_per_mshr"}
            ) or (
                re.fullmatch(
                    r"system\.maa_retirement_caches[0-9]+"
                    r"\.tags(?:\.indexing_policy)?",
                    section,
                )
                and key == "size"
            ):
                line = f"{key}=<RETIREMENT_CACHE_TREATMENT>"
            elif (
                section
                in {
                    "system.membus.snoop_filter",
                    "system.tol3bus.snoop_filter",
                }
                and key == "max_capacity"
            ):
                try:
                    base_capacity = int(value) - retirement_cache_bytes
                except ValueError:
                    fail(f"invalid snoop-filter capacity in config: {path}")
                line = f"max_capacity=<BASE_CAPACITY:{base_capacity}>"
        normalized.append(line)
    return "\n".join(normalized) + "\n"


def verify_case(
    campaign: Path,
    execution_root: Path,
    source: dict[str, Any],
    candidates: dict[str, dict[str, Any]],
    row: dict[str, str],
) -> None:
    phase = row["phase"]
    candidate_name = row["candidate"]
    if candidate_name not in candidates:
        fail(f"unknown candidate in results: {candidate_name}")
    if not row["replica"].isdigit() or int(row["replica"]) <= 0:
        fail("invalid replica number")
    root = (
        campaign
        / "runs"
        / phase
        / candidate_name
        / f"replica_{row['replica']}"
    )
    result = load_json(root / "result.json")
    for field in RESULT_FIELDS:
        expected = str(result[field])
        if field == "wall_seconds":
            if not math.isclose(
                float(row[field]),
                float(result[field]),
                rel_tol=0,
                abs_tol=1e-6,
            ):
                fail(f"wall_seconds differs in {root}")
        elif row[field] != expected:
            fail(f"result.tsv differs from result.json for {field}: {root}")
    if row["valid"] != "1" or row["return_code"] != "0" or row["parse_error"]:
        fail(f"published campaign contains an invalid run: {root}")
    if (root / "restore.exit").read_text().strip() != "0":
        fail(f"restore exit file is nonzero: {root}")
    verify_checkpoint(root, "restore_checkpoint_sha256.txt")
    stats = first_stats(root / "stats.txt")
    for key, value in stats.items():
        if row[key] != str(value):
            fail(f"recomputed {key} differs: {root}")
    verify_cache_config(root / "config.ini", candidates[candidate_name])

    log = regular_file(root / "restore.log").read_text(errors="replace")
    lines = log.splitlines()
    if (
        sum(line == "ROI End!!!" for line in lines) != 1
        or log.count("because m5_exit instruction encountered") != 1
        or FATAL_RE.search(log) is not None
    ):
        fail(f"restore log terminal conditions are invalid: {root}")
    markers = [line for line in lines if MARKER_RE.fullmatch(line)]
    if phase == "screen_correctness":
        expected_marker = source["screen_expected_marker"]
    elif phase == "full_correctness":
        expected_marker = source["full_expected_marker"]
    elif phase == "full_performance":
        expected_marker = None
    else:
        fail(f"unknown phase: {phase}")
    if expected_marker is not None and markers != [expected_marker]:
        fail(f"exact output marker differs: {root}")

    item = candidates[candidate_name]
    command = shlex.split(regular_file(root / "restore.command").read_text())
    expected = expected_command(campaign, execution_root, root, phase, item)
    if command != expected:
        fail(
            f"restore command differs: {root}: "
            f"{command_difference(command, expected)}"
        )


def verify_selection(
    campaign: Path,
    execution_root: Path,
    source: dict[str, Any],
    results: list[dict[str, str]],
    candidates: dict[str, dict[str, Any]],
    reference_result: dict[str, Any],
) -> None:
    fields, rows = read_tsv(campaign / "selection.tsv")
    expected_fields = (
        "candidate",
        "screen_eligible",
        "full_correct",
        "full_deterministic",
        "reference_ticks",
        "candidate_ticks",
        "overhead_fraction",
        "promoted",
    )
    if tuple(fields) != expected_fields:
        fail("selection TSV schema differs")
    if not 1 <= len(rows) <= 2:
        fail("selection must evaluate one or two promotion candidates")
    order = ["compact", "targets1"]
    if [row["candidate"] for row in rows] != order[: len(rows)]:
        fail("selection candidate order differs")

    screen = {
        row["candidate"]: row
        for row in results
        if row["phase"] == "screen_correctness"
    }
    screen_rows = [
        row for row in results if row["phase"] == "screen_correctness"
    ]
    if (
        len(screen_rows) != 3
        or set(screen) != set(candidates)
        or any(row["replica"] != "1" for row in screen_rows)
    ):
        fail("screen matrix is incomplete")
    screen_runtime = expected_staged_runtime(
        execution_root, "screen_correctness"
    )
    screen_reference_config = normalized_treatment_config(
        campaign / "runs/screen_correctness/reference/replica_1/config.ini",
        screen_runtime,
    )
    for name in ("targets1", "compact"):
        candidate_config = normalized_treatment_config(
            campaign / f"runs/screen_correctness/{name}/replica_1/config.ini",
            screen_runtime,
        )
        if candidate_config != screen_reference_config:
            fail(f"screen config differs beyond the treatment: {name}")
    reference_screen_ticks = int(screen["reference"]["sim_ticks"])
    screen_limit = float(source["screen_overhead_limit"])
    eligible_names = {
        name
        for name in ("compact", "targets1")
        if int(screen[name]["sim_ticks"])
        <= reference_screen_ticks * (1 + screen_limit)
    }
    reference_correctness = [
        row
        for row in results
        if row["phase"] == "full_correctness"
        and row["candidate"] == "reference"
    ]
    reference_performance = [
        row
        for row in results
        if row["phase"] == "full_performance"
        and row["candidate"] == "reference"
    ]
    if reference_correctness or reference_performance:
        fail("cost ablation redundantly reran the approved full reference")
    reference_ticks: int | None = (
        int(reference_result["virtual_sim_ticks"]) if eligible_names else None
    )
    reference_correctness_config: str | None = None
    reference_performance_config: str | None = None
    if eligible_names:
        reference_correctness_path = (
            campaign / "inputs/reference/full_correctness_config.ini"
        )
        reference_correctness_config = normalized_treatment_config(
            reference_correctness_path,
            runtime_config_identity(reference_correctness_path),
        )
        reference_performance_path = (
            campaign / "inputs/reference_evidence/virtual_config_replica_1.ini"
        )
        reference_performance_config = normalized_treatment_config(
            reference_performance_path,
            runtime_config_identity(reference_performance_path),
        )

    promoted_rows = []
    for row in rows:
        name = row["candidate"]
        eligible = int(name in eligible_names)
        if row["screen_eligible"] != str(eligible):
            fail(f"screen eligibility was computed incorrectly: {name}")
        expected_reference_ticks = (
            str(reference_ticks) if reference_ticks is not None else "NA"
        )
        if row["reference_ticks"] != expected_reference_ticks:
            fail(f"selection uses the wrong reference ticks: {name}")
        full_correctness = [
            item
            for item in results
            if item["phase"] == "full_correctness"
            and item["candidate"] == name
        ]
        full_performance = [
            item
            for item in results
            if item["phase"] == "full_performance"
            and item["candidate"] == name
        ]
        if eligible == 0:
            if full_correctness or full_performance:
                fail(f"ineligible candidate received full runs: {name}")
            if (
                row["full_correct"] != "0"
                or row["full_deterministic"] != "0"
                or row["candidate_ticks"] != "NA"
                or row["overhead_fraction"] != "NA"
                or row["promoted"] != "0"
            ):
                fail(f"ineligible candidate selection fields differ: {name}")
            continue
        if len(full_correctness) != 1 or full_correctness[0]["valid"] != "1":
            fail(f"eligible candidate lacks exact full correctness: {name}")
        if row["full_correct"] != "1":
            fail(f"full correctness classification differs: {name}")
        if (
            full_correctness[0]["replica"] != "1"
            or len(full_performance) != 3
            or {row["replica"] for row in full_performance} != {"1", "2", "3"}
        ):
            fail(f"eligible full candidate lacks three replicas: {name}")
        if (
            reference_ticks is None
            or reference_correctness_config is None
            or reference_performance_config is None
        ):
            fail(f"eligible candidate has no approved reference: {name}")
        candidate_correctness_config = normalized_treatment_config(
            campaign / f"runs/full_correctness/{name}/replica_1/config.ini",
            expected_staged_runtime(execution_root, "full_correctness"),
        )
        if candidate_correctness_config != reference_correctness_config:
            fail(f"full correctness config differs beyond treatment: {name}")
        for item in full_performance:
            candidate_performance_config = normalized_treatment_config(
                campaign
                / f"runs/full_performance/{name}"
                / f"replica_{item['replica']}/config.ini",
                expected_staged_runtime(execution_root, "full_performance"),
            )
            if candidate_performance_config != reference_performance_config:
                fail(
                    f"full performance config differs beyond treatment: {name}"
                )
        ticks = {item["sim_ticks"] for item in full_performance}
        deterministic = int(len(ticks) == 1)
        if row["full_deterministic"] != str(deterministic):
            fail(f"full determinism classification differs: {name}")
        if not deterministic:
            if (
                row["candidate_ticks"] != "NA"
                or row["overhead_fraction"] != "NA"
                or row["promoted"] != "0"
            ):
                fail(
                    f"nondeterministic candidate selection fields differ: {name}"
                )
            continue
        candidate_ticks = int(next(iter(ticks)))
        overhead = candidate_ticks / reference_ticks - 1
        if row["candidate_ticks"] != str(candidate_ticks) or not math.isclose(
            float(row["overhead_fraction"]), overhead, abs_tol=5e-10
        ):
            fail(f"full overhead calculation differs: {name}")
        promoted = int(overhead <= float(source["full_overhead_limit"]))
        if row["promoted"] != str(promoted):
            fail(f"promotion decision differs: {name}")
        if promoted:
            promoted_rows.append(row)
    if len(promoted_rows) > 1:
        fail("multiple candidates were promoted")
    if len(rows) == 1 and not promoted_rows:
        fail("selection stopped before evaluating the fallback candidate")
    if promoted_rows and rows[-1] != promoted_rows[0]:
        fail("selection continued after promotion")
    selected_names = {row["candidate"] for row in rows}
    extras = [
        row
        for row in results
        if row["phase"] != "screen_correctness"
        and row["candidate"] != "reference"
        and row["candidate"] not in selected_names
    ]
    if extras:
        fail("full runs exist for a candidate outside the selection trace")

    summary = load_json(campaign / "summary.json")
    if promoted_rows:
        if reference_ticks is None:
            fail("promoted campaign has no approved reference")
        if (campaign / "no_promotion").exists():
            fail("promoted campaign also has no_promotion")
        row = promoted_rows[0]
        name = row["candidate"]
        item = candidates[name]
        candidate_ticks = int(row["candidate_ticks"])
        if (
            summary.get("status") != "promoted"
            or summary.get("candidate") != item
            or summary.get("reference_sim_ticks") != reference_ticks
            or summary.get("candidate_sim_ticks") != candidate_ticks
            or not math.isclose(
                float(summary.get("speedup", 0)),
                reference_ticks / candidate_ticks,
                rel_tol=0,
                abs_tol=1e-12,
            )
            or not math.isclose(
                float(summary.get("elapsed_change_percent", 0)),
                (candidate_ticks / reference_ticks - 1) * 100,
                rel_tol=0,
                abs_tol=1e-12,
            )
        ):
            fail("promoted summary does not recompute")
        expected_bytes = {"1kB": 4096, "256B": 1024}[str(item["size"])]
        expected_targets = (
            int(item["mshrs"]) * int(item["targets_per_mshr"]) * 4
        )
        if (
            summary.get("retirement_cache_data_bytes_total") != expected_bytes
            or summary.get("retirement_cache_target_slots_total")
            != expected_targets
        ):
            fail("promoted cost counts are wrong")
    else:
        regular_file(campaign / "no_promotion", empty=True)
        if (
            summary.get("status") != "complete_without_promotion"
            or summary.get("reference_sim_ticks") != reference_ticks
        ):
            fail("no-promotion summary differs")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign", type=Path)
    parser.add_argument("--certificate-output", type=Path, required=True)
    parser.add_argument("--sim-root", type=Path, required=True)
    parser.add_argument("--expected-sim-commit", required=True)
    parser.add_argument("--expected-launcher-sha", required=True)
    parser.add_argument("--expected-verifier-sha", required=True)
    parser.add_argument("--expected-runner-sha", required=True)
    parser.add_argument("--expected-reference-verifier-sha", required=True)
    parser.add_argument("--expected-reference-approval-sha", required=True)
    parser.add_argument(
        "--expected-reference-result-approval-sha", required=True
    )
    parser.add_argument("--expected-input-20k-sha", required=True)
    parser.add_argument("--expected-bfs-approval-sha", required=True)
    parser.add_argument("--expected-bfs-oracle-sha", required=True)
    parser.add_argument("--expected-source-20k-fingerprint", required=True)
    parser.add_argument("--expected-source-full-fingerprint", required=True)
    parser.add_argument("--expected-source-20k", type=Path, required=True)
    parser.add_argument("--expected-source-full", type=Path, required=True)
    parser.add_argument(
        "--expected-reference-campaign", type=Path, required=True
    )
    parser.add_argument("--expected-bfs-campaign", type=Path, required=True)
    parser.add_argument(
        "--expected-screen-overhead-limit", type=float, default=0.01
    )
    parser.add_argument(
        "--expected-full-overhead-limit", type=float, default=0.01
    )
    parser.add_argument(
        "--expected-minimum-available-kib",
        type=int,
        default=24 * 1024 * 1024,
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_sim_commit):
        fail("expected simulator commit is malformed")
    expected_hashes = (
        args.expected_launcher_sha,
        args.expected_verifier_sha,
        args.expected_runner_sha,
        args.expected_reference_verifier_sha,
        args.expected_reference_approval_sha,
        args.expected_reference_result_approval_sha,
        args.expected_input_20k_sha,
        args.expected_bfs_approval_sha,
        args.expected_bfs_oracle_sha,
        args.expected_source_20k_fingerprint,
        args.expected_source_full_fingerprint,
    )
    if any(SHA256_RE.fullmatch(value) is None for value in expected_hashes):
        fail("an expected authorization hash is malformed")
    expected_source_20k = args.expected_source_20k.resolve(strict=True)
    expected_source_full = args.expected_source_full.resolve(strict=True)
    expected_reference_campaign = args.expected_reference_campaign.absolute()
    expected_bfs_campaign = args.expected_bfs_campaign.resolve(strict=True)
    if args.campaign.is_symlink():
        fail("campaign path is symlinked")
    campaign = args.campaign.resolve(strict=True)
    campaign_guard = create_campaign_guard(campaign)
    regular_file(campaign / "execution.complete", empty=True)
    regular_file(campaign / "campaign.pass", empty=True)
    if (campaign / "campaign.fail").exists():
        fail("campaign has a fail state")
    for path in campaign.rglob("*"):
        if path.is_symlink():
            fail(f"campaign contains a symlink: {path}")
    verify_evidence(campaign)
    staged_manifest = verify_staged_inputs(campaign)

    source = load_json(campaign / "source.json")
    if (
        source.get("schema_version") != 1
        or source.get("execution") != "serial"
        or source.get("wall_clock_timeout") != "none"
        or source.get("environment_policy")
        != "sanitized-private-home-fd-exec-v3"
        or source.get("simulation_lock")
        != "/data1/nier/.dx100-virtual-simulation.lock"
        or source.get("simulator_commit") != args.expected_sim_commit
        or source.get("workflow_config_commit") != args.expected_sim_commit
        or source.get("minimum_available_kib")
        != args.expected_minimum_available_kib
        or source.get("screen_overhead_limit")
        != args.expected_screen_overhead_limit
        or source.get("full_overhead_limit")
        != args.expected_full_overhead_limit
        or Path(source.get("source_20k_campaign", "")).resolve(strict=True)
        != expected_source_20k
        or Path(source.get("source_full_correctness_campaign", "")).resolve(
            strict=True
        )
        != expected_source_full
        or Path(source.get("reference_campaign", "")).absolute()
        != expected_reference_campaign
        or Path(source.get("bfs_campaign", "")).resolve(strict=True)
        != expected_bfs_campaign
        or source.get("source_20k_fingerprint")
        != args.expected_source_20k_fingerprint
        or source.get("source_full_fingerprint")
        != args.expected_source_full_fingerprint
    ):
        fail("source policy or identity is invalid")
    execution_root = authorized_execution_root(campaign, source)
    bind_publication_guard(
        campaign_guard, execution_root, source["publication_name"]
    )
    screen_marker = verify_source_correctness(
        expected_source_20k, args.expected_source_20k_fingerprint
    )
    full_marker = verify_source_correctness(
        expected_source_full, args.expected_source_full_fingerprint
    )
    if (
        source.get("screen_expected_marker") != screen_marker
        or source.get("full_expected_marker") != full_marker
    ):
        fail("source exact-output marker binding differs")
    candidates = candidate_map(source)
    source_artifacts = source.get("source_artifacts")
    if not isinstance(source_artifacts, dict) or not isinstance(
        source_artifacts.get("sha256"), dict
    ):
        fail("source artifact binding is malformed")
    staged_by_name = {
        "gem5": campaign / "inputs/bin/gem5.opt",
        "ramulator_yaml": campaign / "inputs/ramulator.yaml",
        "ramulator_lib": campaign / "inputs/lib/libramulator.so",
        "virtual_verify": campaign / "inputs/benchmark/xrage_virtual_verify",
        "virtual_perf": campaign / "inputs/benchmark/xrage_virtual",
        "input_20k": campaign / "inputs/benchmark/xrage_20k.json",
        "input_full": campaign / "inputs/benchmark/xrage_full.json",
        "runner": campaign / "inputs/runner.py",
        "launcher": campaign / "inputs/launcher.py",
        "verifier": campaign / "inputs/verifier.py",
        "reference_approval": (
            campaign / "inputs/manifests/reference_approval.json"
        ),
        "reference_result_approval": (
            campaign / "inputs/manifests/reference_result_approval.json"
        ),
        **{
            f"reference_config_{replica}": (
                campaign
                / "inputs/reference_evidence"
                / f"virtual_config_replica_{replica}.ini"
            )
            for replica in (1, 2, 3)
        },
        "reference_results": (
            campaign / "inputs/reference_evidence/results.tsv"
        ),
        "reference_source": (
            campaign / "inputs/reference_evidence/source.txt"
        ),
        "reference_attribution": (
            campaign / "inputs/reference_evidence/attribution.tsv"
        ),
        "reference_staged_input_manifest": (
            campaign / "inputs/reference_evidence/staged_input_sha256.txt"
        ),
        "reference_evidence_manifest": (
            campaign / "inputs/reference_evidence/evidence_sha256.txt"
        ),
        "reference_checkpoint_manifest": (
            campaign
            / "inputs/reference_evidence/private_checkpoint_sha256.txt"
        ),
        "source_full_config": (
            campaign / "inputs/reference/full_correctness_config.ini"
        ),
        "reference_verifier": campaign / "inputs/reference_verifier.py",
        "bfs_approval": campaign / "inputs/manifests/bfs_approval.json",
        "bfs_oracle": campaign / "inputs/manifests/bfs_oracle.json",
    }
    if set(source_artifacts["sha256"]) != set(staged_by_name):
        fail("source artifact name closure differs")
    staged_approval = load_json(staged_by_name["reference_approval"])
    expect_hash(
        staged_by_name["reference_result_approval"],
        args.expected_reference_result_approval_sha,
    )
    staged_result_approval = load_json(
        staged_by_name["reference_result_approval"]
    )
    trusted_staged_hashes = {
        "gem5": nested(staged_approval, "candidate", "gem5_sha256"),
        "ramulator_yaml": nested(
            staged_approval, "candidate", "ramulator_sha256"
        ),
        "ramulator_lib": nested(
            staged_approval, "candidate", "ramulator_library_sha256"
        ),
        "virtual_verify": nested(
            staged_approval, "verifier_binaries", "virtual_sha256"
        ),
        "virtual_perf": nested(
            staged_approval, "benchmark_binaries", "virtual_sha256"
        ),
        "runner": args.expected_runner_sha,
        "launcher": args.expected_launcher_sha,
        "verifier": args.expected_verifier_sha,
        "reference_approval": args.expected_reference_approval_sha,
        "reference_result_approval": (
            args.expected_reference_result_approval_sha
        ),
        **{
            f"reference_config_{replica}": nested(
                staged_result_approval,
                "evidence",
                "virtual_config_sha256",
                str(replica),
            )
            for replica in (1, 2, 3)
        },
        "reference_results": nested(
            staged_result_approval, "evidence", "results_sha256"
        ),
        "reference_source": nested(
            staged_result_approval, "evidence", "source_sha256"
        ),
        "reference_attribution": nested(
            staged_result_approval, "evidence", "attribution_sha256"
        ),
        "reference_staged_input_manifest": nested(
            staged_result_approval,
            "evidence",
            "staged_input_manifest_sha256",
        ),
        "reference_evidence_manifest": nested(
            staged_result_approval, "evidence", "manifest_sha256"
        ),
        "reference_checkpoint_manifest": nested(
            staged_result_approval,
            "evidence",
            "virtual_checkpoint_manifest_sha256",
        ),
        "source_full_config": nested(
            staged_approval,
            "correctness_campaign",
            "runs",
            "virtual",
            "config_ini_sha256",
        ),
        "reference_verifier": args.expected_reference_verifier_sha,
        "input_20k": args.expected_input_20k_sha,
        "input_full": nested(staged_approval, "workload", "input_sha256"),
        "bfs_approval": args.expected_bfs_approval_sha,
        "bfs_oracle": args.expected_bfs_oracle_sha,
    }
    if any(
        not isinstance(expected, str) or SHA256_RE.fullmatch(expected) is None
        for expected in trusted_staged_hashes.values()
    ):
        fail("an approval-derived staged hash is malformed")
    for name, expected in trusted_staged_hashes.items():
        if source_artifacts["sha256"].get(name) != expected:
            fail(f"source authorization hash differs: {name}")
    staged_manifest_sha = source_artifacts.get("staged_manifest_sha256")
    if (
        not isinstance(staged_manifest_sha, str)
        or SHA256_RE.fullmatch(staged_manifest_sha) is None
        or file_sha256(campaign / "staged_input_sha256.json")
        != staged_manifest_sha
    ):
        fail("recorded staged-input manifest hash differs")
    for name, path in staged_by_name.items():
        expected = trusted_staged_hashes[name]
        expect_hash(path, expected)
        relative = str(path.relative_to(campaign / "inputs"))
        if staged_manifest.get(relative) != expected:
            fail(f"staged manifest authorization hash differs: {name}")

    staged_verifier = campaign / "inputs/reference_verifier.py"
    reference_result = staged_reference_result(
        campaign,
        source,
        args.expected_reference_approval_sha,
        args.expected_reference_verifier_sha,
    )
    reference_ticks = int(reference_result["virtual_sim_ticks"])
    if source.get("upstream_reference_sim_ticks") != reference_ticks:
        fail("source upstream-reference simTicks differs")
    bfs_campaign = expected_bfs_campaign
    bfs_verification = run_python_fd(
        staged_verifier,
        args.expected_reference_verifier_sha,
        [
            "bfs",
            str(bfs_campaign),
            str(campaign / "inputs/manifests/bfs_approval.json"),
            "--oracle",
            str(campaign / "inputs/manifests/bfs_oracle.json"),
        ],
    )
    if bfs_verification.returncode != 0:
        fail(
            "external BFS dependency verification no longer passes: "
            + bfs_verification.stdout.strip()
        )

    simulator_root = args.sim_root.resolve(strict=True)
    # FD execution exposes /proc/self/fd/N as __file__; resolve it to the
    # already-open, private staged inode before applying regular-file checks.
    expect_hash(
        Path(__file__).resolve(strict=True), args.expected_verifier_sha
    )
    verify_git_state(simulator_root, args.expected_sim_commit)
    binary_commit = nested(staged_approval, "candidate", "simulator_commit")
    if binary_commit != source.get(
        "binary_simulator_commit"
    ) or binary_commit != reference_result.get("binary_simulator_commit"):
        fail("binary-build commit binding differs")
    ancestry = subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(simulator_root),
            "merge-base",
            "--is-ancestor",
            str(binary_commit),
            args.expected_sim_commit,
        ],
        check=False,
        env=AUTH_ENV,
    )
    if ancestry.returncode != 0:
        fail("workflow/config commit does not descend from binary commit")
    changed_paths = str(
        git_output(
            simulator_root,
            "diff",
            "--name-only",
            f"{binary_commit}..{args.expected_sim_commit}",
        )
    ).splitlines()
    if any(
        not (path.startswith("configs/") or path.startswith("experiments/"))
        for path in changed_paths
    ):
        fail("binary-incompatible source changed after the approved build")
    expected_configs = committed_config_hashes(
        simulator_root, args.expected_sim_commit
    )
    for relative, expected in expected_configs.items():
        if staged_manifest.get(relative) != expected:
            fail(f"staged simulator config differs: {relative}")

    checkpoint_bindings = {
        "screen": (
            expected_source_20k / "checkpoints/virtual",
            "checkpoint_sha256.txt",
        ),
        "full_correctness": (
            expected_source_full / "checkpoints/virtual",
            "checkpoint_sha256.txt",
        ),
    }
    checkpoint_paths: set[str] = set()
    for name, (
        trusted_root,
        trusted_manifest_name,
    ) in checkpoint_bindings.items():
        trusted_entries = verify_checkpoint(
            trusted_root, trusted_manifest_name
        )
        staged_root = campaign / "inputs/checkpoints" / name
        staged_entries = verify_checkpoint(
            staged_root, "checkpoint_sha256.txt"
        )
        if staged_entries != trusted_entries:
            fail(f"staged checkpoint differs from trusted source: {name}")
        checkpoint_paths.add(
            str(Path("checkpoints") / name / "checkpoint_sha256.txt")
        )
        checkpoint_paths.update(
            str(Path("checkpoints") / name / relative)
            for relative in staged_entries
        )
    performance_root = campaign / "inputs/checkpoints/full_performance"
    performance_entries = verify_checkpoint(
        performance_root, "checkpoint_sha256.txt"
    )
    if (
        reference_result["evidence"]["virtual_checkpoint_payload_sha256"]
        != performance_entries
    ):
        fail("staged full-performance checkpoint differs from approval")
    checkpoint_paths.add("checkpoints/full_performance/checkpoint_sha256.txt")
    checkpoint_paths.update(
        str(Path("checkpoints/full_performance") / relative)
        for relative in performance_entries
    )

    core_paths = {
        str(path.relative_to(campaign / "inputs"))
        for path in staged_by_name.values()
    }
    authorized_paths = core_paths | set(expected_configs) | checkpoint_paths
    if set(staged_manifest) != authorized_paths:
        fail("staged input closure is not externally authorized")

    fields, rows = read_tsv(campaign / "results.tsv")
    if tuple(fields) != RESULT_FIELDS:
        fail("results TSV schema differs")
    identities = {
        (row["phase"], row["candidate"], row["replica"]) for row in rows
    }
    if len(identities) != len(rows):
        fail("results contain duplicate run identities")
    for row in rows:
        verify_case(campaign, execution_root, source, candidates, row)
    verify_selection(
        campaign,
        execution_root,
        source,
        rows,
        candidates,
        reference_result,
    )
    certificate = args.certificate_output.absolute()
    validate_certificate_output(
        certificate,
        campaign,
        execution_root,
        source["publication_name"],
    )
    record = {
        "schema_version": 1,
        "experiment_id": "xrage-retirement-cache-verification-v1",
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "campaign_device": campaign_guard.device,
        "campaign_inode": campaign_guard.inode,
        "simulator_commit": args.expected_sim_commit,
        "launcher_sha256": args.expected_launcher_sha,
        "runner_sha256": args.expected_runner_sha,
        "verifier_sha256": args.expected_verifier_sha,
        "evidence_manifest_sha256": file_sha256(
            campaign / "evidence_sha256.txt"
        ),
        "staged_input_manifest_sha256": file_sha256(
            campaign / "staged_input_sha256.json"
        ),
        "source_sha256": file_sha256(campaign / "source.json"),
        "results_sha256": file_sha256(campaign / "results.tsv"),
        "selection_sha256": file_sha256(campaign / "selection.tsv"),
        "summary_sha256": file_sha256(campaign / "summary.json"),
    }
    verify_campaign_guard(campaign_guard)
    publish_verification_certificate(
        certificate,
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        campaign_guard,
    )
    os.close(campaign_guard.campaign_descriptor)
    os.close(campaign_guard.watch_descriptor)
    print(f"XRAGE retirement-cache ablation verified: {campaign}")


if __name__ == "__main__":
    main()
