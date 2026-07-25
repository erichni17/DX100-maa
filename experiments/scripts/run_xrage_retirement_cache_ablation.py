#!/usr/bin/python3
"""Screen and promote lower-cost XRAGE virtual-retirement cache settings."""

from __future__ import annotations

import argparse
import csv
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import re
import select
import shlex
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import (
    asdict,
    dataclass,
)
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
SIMULATION_LOCK = Path("/data1/nier/.dx100-virtual-simulation.lock")
TERMINATION_SIGNALS = frozenset((signal.SIGTERM, signal.SIGHUP, signal.SIGINT))
IN_ATTRIB = 0x00000004
IN_MODIFY = 0x00000002
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_Q_OVERFLOW = 0x00004000
IN_IGNORED = 0x00008000
IN_ISDIR = 0x40000000
LOCK_WATCH_MASK = (
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
CHECKPOINT_WATCH_MASK = LOCK_WATCH_MASK
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


@dataclass(frozen=True)
class Candidate:
    name: str
    size: str
    assoc: int
    response_latency: int
    mshrs: int
    targets_per_mshr: int
    write_buffers: int


@dataclass(frozen=True)
class SimulationLock:
    descriptor: int
    watch_descriptor: int
    device: int
    inode: int
    ctime_ns: int


@dataclass
class CheckpointGuard:
    descriptor: int
    root_descriptor: int
    parent_watch: int
    output_watch: int
    output: Path
    output_name: bytes
    checkpoint_name: bytes
    tree_watches: set[int]
    device: int
    inode: int


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_time: int
    process_group: int
    session: int


class TerminationRequested(RuntimeError):
    pass


@dataclass
class TerminationController:
    signal_number: int | None = None

    def __call__(self, signal_number: int, _frame: Any) -> None:
        if self.signal_number is not None:
            return
        self.signal_number = signal_number
        raise TerminationRequested(
            f"termination requested by signal {signal_number}"
        )


CANDIDATES = (
    Candidate("reference", "1kB", 4, 1, 16, 16, 16),
    Candidate("targets1", "1kB", 4, 1, 16, 1, 16),
    Candidate("compact", "256B", 4, 1, 16, 1, 16),
)
PROMOTION_ORDER = ("compact", "targets1")


def fail(message: str) -> None:
    raise RuntimeError(message)


def install_termination_handlers() -> TerminationController:
    controller = TerminationController()
    for signal_number in TERMINATION_SIGNALS:
        signal.signal(signal_number, controller)
    return controller


def restore_signal_mask(mask: set[signal.Signals]) -> None:
    signal.pthread_sigmask(signal.SIG_SETMASK, mask)


def regular_file(path: Path, *, executable: bool = False) -> Path:
    if path.is_symlink() or not path.is_file():
        fail(f"required regular file is missing or symlinked: {path}")
    if executable and not os.access(path, os.X_OK):
        fail(f"required file is not executable: {path}")
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


def empty_marker(path: Path) -> None:
    regular_file(path)
    if path.stat().st_size != 0:
        fail(f"marker is not empty: {path}")


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


def atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    parent = path.parent.resolve(strict=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
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


def expect_hash(label: str, path: Path, expected: str) -> None:
    regular_file(path)
    if not SHA256_RE.fullmatch(expected):
        fail(f"{label} has malformed expected SHA-256: {expected!r}")
    actual = file_sha256(path)
    if actual != expected:
        fail(f"{label} hash mismatch: expected={expected} actual={actual}")


def load_json(path: Path) -> dict[str, Any]:
    regular_file(path)
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
        fail(
            "cost worktree is not at the authorized commit: "
            f"expected={expected_commit} actual={head.strip()}"
        )
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
        fail("cost worktree is dirty")
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
    regular_file(path)
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


def parse_external_artifact_manifest(path: Path) -> dict[Path, str]:
    regular_file(path)
    entries: dict[Path, str] = {}
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
        if match is None:
            fail(f"malformed SHA-256 manifest line {line_number}: {path}")
        digest, raw_path = match.groups()
        recorded = Path(raw_path)
        if not recorded.is_absolute() or ".." in recorded.parts:
            fail(f"artifact manifest path must be absolute: {recorded}")
        reject_symlink_components(recorded)
        target = regular_file(recorded).resolve(strict=True)
        if target in entries:
            fail(f"duplicate manifest path: {target}")
        entries[target] = digest
    if not entries:
        fail(f"empty SHA-256 manifest: {path}")
    return entries


def verify_external_artifact_manifest(path: Path) -> dict[Path, str]:
    entries = parse_external_artifact_manifest(path)
    for target, expected in entries.items():
        expect_hash("manifest artifact", target, expected)
    return entries


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


def checkpoint_payload(
    checkpoint_root: Path, manifest_name: str
) -> tuple[Path, dict[Path, str]]:
    reject_symlink_components(checkpoint_root)
    manifest = contained_regular_file(checkpoint_root, Path(manifest_name))
    entries: dict[Path, str] = {}
    for line_number, line in enumerate(manifest.read_text().splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
        if match is None:
            fail(
                f"malformed checkpoint manifest line {line_number}: {manifest}"
            )
        digest, raw_path = match.groups()
        relative = Path(raw_path)
        target = contained_regular_file(checkpoint_root, relative)
        if target in entries:
            fail(f"duplicate checkpoint path in {manifest}: {relative}")
        entries[target] = digest
    checkpoint_dirs = {
        path.parent
        for path in checkpoint_root.glob("*/m5.cpt")
        if path.is_file() and not path.is_symlink()
    }
    if len(checkpoint_dirs) != 1:
        fail(f"expected one checkpoint payload under {checkpoint_root}")
    checkpoint_dir = checkpoint_dirs.pop()
    actual = {
        path
        for path in checkpoint_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    for path in checkpoint_dir.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            fail(f"unsupported checkpoint entry: {path}")
    if actual != set(entries):
        fail(f"checkpoint closure differs from manifest: {checkpoint_root}")
    for target, expected in entries.items():
        expect_hash("checkpoint payload", target, expected)
    return checkpoint_dir, entries


def secure_copy_approved(
    source: Path, destination: Path, expected_sha256: str
) -> None:
    if not SHA256_RE.fullmatch(expected_sha256):
        fail(f"malformed approved SHA-256 for {source}")
    reject_symlink_components(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    destination_fd = -1
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            fail(f"approved copy source is not regular: {source}")
        destination_fd = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | os.O_CLOEXEC,
            stat.S_IMODE(before.st_mode) & 0o555,
        )
        cloned = False
        try:
            # Linux FICLONE pins the opened inode and avoids a path-reopen race.
            fcntl.ioctl(destination_fd, 0x40049409, source_fd)
            cloned = True
        except OSError as error:
            if error.errno not in {
                errno.EBADF,
                errno.EINVAL,
                errno.ENOTTY,
                errno.EOPNOTSUPP,
                errno.EXDEV,
            }:
                raise
        if not cloned:
            digest = hashlib.sha256()
            while True:
                block = os.read(source_fd, 1024 * 1024)
                if not block:
                    break
                digest.update(block)
                view = memoryview(block)
                while view:
                    written = os.write(destination_fd, view)
                    view = view[written:]
            if digest.hexdigest() != expected_sha256:
                fail(f"approved copy source hash mismatch: {source}")
        os.fchmod(
            destination_fd,
            0o500 if before.st_mode & stat.S_IXUSR else 0o400,
        )
        os.fsync(destination_fd)
        after = os.fstat(source_fd)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_ctime_ns,
        ):
            fail(f"approved copy source changed while open: {source}")
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
        os.close(source_fd)
    expect_hash("staged approved copy", destination, expected_sha256)


def stage_checkpoint(
    source_root: Path, manifest_name: str, destination_root: Path
) -> dict[Path, str]:
    source_dir, source_entries = checkpoint_payload(source_root, manifest_name)
    destination_root.mkdir(parents=True)
    staged_entries: dict[Path, str] = {}
    for path, digest in source_entries.items():
        destination = destination_root / path.relative_to(source_root)
        secure_copy_approved(path, destination, digest)
        staged_entries[destination] = digest
    manifest = destination_root / "checkpoint_sha256.txt"
    with manifest.open("w", encoding="utf-8") as handle:
        for target, digest in sorted(
            staged_entries.items(), key=lambda item: str(item[0])
        ):
            handle.write(f"{digest}  {target.relative_to(destination_root)}\n")
    checkpoint_payload(destination_root, "checkpoint_sha256.txt")
    fresh_source_dir, fresh_source_entries = checkpoint_payload(
        source_root, manifest_name
    )
    if (
        fresh_source_dir != source_dir
        or fresh_source_entries != source_entries
    ):
        fail(f"source checkpoint changed during staging: {source_root}")
    return staged_entries


def verify_artifact_campaign(campaign: Path) -> None:
    if campaign.is_symlink() or not campaign.is_dir():
        fail(f"campaign is missing or symlinked: {campaign}")
    empty_marker(campaign / "campaign.pass")
    if (campaign / "campaign.fail").exists():
        fail(f"campaign has both pass and fail state: {campaign}")
    verify_external_artifact_manifest(campaign / "artifact_sha256.txt")


def correctness_marker(campaign: Path) -> str:
    verify_artifact_campaign(campaign)
    fields, rows = read_tsv(campaign / "results.tsv")
    expected_fields = (
        "arm",
        "rc",
        "marker_count",
        "roi_count",
        "write_issues",
        "write_completions",
        "valid",
        "marker",
    )
    if tuple(fields) != expected_fields:
        fail(f"unexpected correctness schema: {campaign / 'results.tsv'}")
    virtual = [row for row in rows if row["arm"] == "virtual"]
    if len(virtual) != 1:
        fail(f"correctness campaign lacks one virtual row: {campaign}")
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
        fail(f"correctness campaign has an invalid virtual row: {campaign}")
    run = campaign / "runs/virtual"
    for name in (
        "restore.command",
        "restore.exit",
        "restore.log",
        "result.tsv",
        "stats.txt",
        "config.ini",
        "config.json",
        "validation.pass",
    ):
        regular_file(run / name)
    if (run / "restore.exit").read_text().strip() != "0":
        fail(f"correctness virtual restore did not exit cleanly: {campaign}")
    return row["marker"]


def create_lock_watch() -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.inotify_init1.argtypes = [ctypes.c_int]
    libc.inotify_init1.restype = ctypes.c_int
    libc.inotify_add_watch.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint32,
    ]
    libc.inotify_add_watch.restype = ctypes.c_int
    descriptor = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
    if descriptor < 0:
        error = ctypes.get_errno()
        fail(f"inotify_init1 failed: {os.strerror(error)}")
    watch = libc.inotify_add_watch(
        descriptor,
        os.fsencode(SIMULATION_LOCK.parent),
        LOCK_WATCH_MASK,
    )
    if watch < 0:
        error = ctypes.get_errno()
        os.close(descriptor)
        fail(f"inotify_add_watch failed: {os.strerror(error)}")
    return descriptor


def create_checkpoint_guard(
    output: Path, checkpoint_name: str
) -> CheckpointGuard:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.inotify_init1.argtypes = [ctypes.c_int]
    libc.inotify_init1.restype = ctypes.c_int
    libc.inotify_add_watch.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint32,
    ]
    libc.inotify_add_watch.restype = ctypes.c_int
    descriptor = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
    if descriptor < 0:
        error = ctypes.get_errno()
        fail(f"checkpoint inotify_init1 failed: {os.strerror(error)}")
    parent_watch = libc.inotify_add_watch(
        descriptor,
        os.fsencode(output.parent),
        CHECKPOINT_WATCH_MASK,
    )
    if parent_watch < 0:
        error = ctypes.get_errno()
        os.close(descriptor)
        fail(f"checkpoint parent watch failed: {os.strerror(error)}")
    root_descriptor = os.open(
        output,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    watch = libc.inotify_add_watch(
        descriptor,
        os.fsencode(output),
        CHECKPOINT_WATCH_MASK,
    )
    if watch < 0:
        error = ctypes.get_errno()
        os.close(root_descriptor)
        os.close(descriptor)
        fail(f"checkpoint inotify_add_watch failed: {os.strerror(error)}")
    root_stat = os.fstat(root_descriptor)
    guard = CheckpointGuard(
        descriptor=descriptor,
        root_descriptor=root_descriptor,
        parent_watch=parent_watch,
        output_watch=watch,
        output=output,
        output_name=os.fsencode(output.name),
        checkpoint_name=os.fsencode(checkpoint_name),
        tree_watches=set(),
        device=root_stat.st_dev,
        inode=root_stat.st_ino,
    )
    verify_checkpoint_guard(guard)
    return guard


def add_checkpoint_tree_watches(
    guard: CheckpointGuard, checkpoint: Path
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.inotify_add_watch.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint32,
    ]
    libc.inotify_add_watch.restype = ctypes.c_int
    directories = [
        checkpoint,
        *sorted(path for path in checkpoint.rglob("*") if path.is_dir()),
    ]
    for directory in directories:
        reject_symlink_components(directory)
        watch = libc.inotify_add_watch(
            guard.descriptor,
            os.fsencode(directory),
            CHECKPOINT_WATCH_MASK,
        )
        if watch < 0:
            error = ctypes.get_errno()
            fail(
                "checkpoint tree inotify_add_watch failed: "
                f"{os.strerror(error)}"
            )
        guard.tree_watches.add(watch)


def verify_checkpoint_guard(
    guard: CheckpointGuard, *, allow_initialization: bool = False
) -> None:
    while True:
        try:
            data = os.read(guard.descriptor, 64 * 1024)
        except OSError as error:
            if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                break
            fail(f"checkpoint watch failed: {error}")
        if not data:
            fail("checkpoint watch closed unexpectedly")
        offset = 0
        while offset < len(data):
            if len(data) - offset < INOTIFY_EVENT.size:
                fail("checkpoint watch returned a truncated event")
            watch, mask, _, name_length = INOTIFY_EVENT.unpack_from(
                data, offset
            )
            offset += INOTIFY_EVENT.size
            if len(data) - offset < name_length:
                fail("checkpoint watch returned a truncated name")
            raw_name = data[offset : offset + name_length]
            offset += name_length
            name = raw_name.rstrip(b"\0")
            if mask & (IN_Q_OVERFLOW | IN_IGNORED):
                fail("checkpoint watch lost event coverage")
            if (watch == guard.parent_watch and name == guard.output_name) or (
                watch == guard.output_watch
                and mask & (IN_DELETE_SELF | IN_MOVE_SELF)
            ):
                fail("guarded run directory changed while in use")
            relevant = watch in guard.tree_watches or (
                watch == guard.output_watch and name == guard.checkpoint_name
            )
            if not relevant:
                continue
            operation_mask = mask & ~IN_ISDIR
            if allow_initialization and not operation_mask & ~(
                IN_CREATE | IN_ATTRIB | IN_MODIFY | IN_CLOSE_WRITE
            ):
                continue
            fail("restored checkpoint changed while in use")
    descriptor_stat = os.fstat(guard.root_descriptor)
    path_stat = guard.output.lstat()
    if (
        not guard.output.is_dir()
        or guard.output.is_symlink()
        or descriptor_stat.st_dev != guard.device
        or descriptor_stat.st_ino != guard.inode
        or path_stat.st_dev != guard.device
        or path_stat.st_ino != guard.inode
    ):
        fail("guarded run directory identity changed")


def verify_lock_watch(
    lock: SimulationLock, *, allow_initialization: bool = False
) -> None:
    while True:
        try:
            data = os.read(lock.watch_descriptor, 64 * 1024)
        except OSError as error:
            if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return
            fail(f"simulation lock watch failed: {error}")
        if not data:
            fail("simulation lock watch closed unexpectedly")
        offset = 0
        while offset < len(data):
            if len(data) - offset < INOTIFY_EVENT.size:
                fail("simulation lock watch returned a truncated event")
            _, mask, _, name_length = INOTIFY_EVENT.unpack_from(data, offset)
            offset += INOTIFY_EVENT.size
            if len(data) - offset < name_length:
                fail("simulation lock watch returned a truncated name")
            raw_name = data[offset : offset + name_length]
            offset += name_length
            name = raw_name.rstrip(b"\0")
            if mask & (IN_Q_OVERFLOW | IN_IGNORED):
                fail("simulation lock watch lost event coverage")
            if not name or name == os.fsencode(SIMULATION_LOCK.name):
                if allow_initialization and not mask & ~(
                    IN_CREATE | IN_ATTRIB
                ):
                    continue
                fail("simulation lock pathname changed while held")


def wait_for_capacity(
    minimum_available_kib: int, poll_seconds: int
) -> SimulationLock:
    while True:
        values: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, raw = line.split(":", 1)
            fields = raw.split()
            if fields and fields[0].isdigit():
                values[key] = int(fields[0])
        available = values.get("MemAvailable", 0)
        if available >= minimum_available_kib:
            descriptor: int | None = None
            watch_descriptor = create_lock_watch()
            try:
                descriptor = os.open(
                    SIMULATION_LOCK,
                    os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
                    0o600,
                )
                if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
                    os.fchmod(descriptor, 0o600)
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                descriptor_stat = os.fstat(descriptor)
                lock = SimulationLock(
                    descriptor=descriptor,
                    watch_descriptor=watch_descriptor,
                    device=descriptor_stat.st_dev,
                    inode=descriptor_stat.st_ino,
                    ctime_ns=descriptor_stat.st_ctime_ns,
                )
                verify_lock_watch(lock, allow_initialization=True)
                verify_lock_identity(lock)
                return lock
            except BlockingIOError:
                if descriptor is not None:
                    os.close(descriptor)
                os.close(watch_descriptor)
            except BaseException:
                if descriptor is not None:
                    os.close(descriptor)
                os.close(watch_descriptor)
                raise
        print(
            f"[{time.strftime('%Y-%m-%dT%H:%M:%S%z')}] waiting: "
            f"MemAvailable={available} KiB, required={minimum_available_kib} KiB",
            flush=True,
        )
        time.sleep(poll_seconds)


def verify_lock_identity(lock: SimulationLock) -> None:
    verify_lock_watch(lock)
    descriptor_stat = os.fstat(lock.descriptor)
    path_stat = SIMULATION_LOCK.lstat()
    if (
        not stat.S_ISREG(descriptor_stat.st_mode)
        or descriptor_stat.st_uid != os.getuid()
        or descriptor_stat.st_nlink != 1
        or stat.S_IMODE(descriptor_stat.st_mode) != 0o600
        or descriptor_stat.st_dev != lock.device
        or descriptor_stat.st_ino != lock.inode
        or descriptor_stat.st_ctime_ns != lock.ctime_ns
        or path_stat.st_dev != descriptor_stat.st_dev
        or path_stat.st_ino != descriptor_stat.st_ino
    ):
        fail(f"simulation lock identity is unsafe: {SIMULATION_LOCK}")


def write_command(path: Path, command: list[str]) -> None:
    path.write_text(shlex.join(command) + "\n", encoding="utf-8")


def process_identity(pid: int) -> ProcessIdentity | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return None
    close = raw.rfind(")")
    if close < 0:
        fail(f"malformed /proc stat for process {pid}")
    fields = raw[close + 2 :].split()
    if len(fields) < 20:
        fail(f"malformed /proc stat for process {pid}")
    return ProcessIdentity(
        pid=pid,
        start_time=int(fields[19]),
        process_group=int(fields[2]),
        session=int(fields[3]),
    )


def session_members(session_id: int) -> list[ProcessIdentity]:
    members: list[ProcessIdentity] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        identity = process_identity(int(entry.name))
        if identity is not None and identity.session == session_id:
            members.append(identity)
    return sorted(members, key=lambda member: member.pid)


def signal_session_members(session_id: int, signal_number: int) -> int:
    signaled = 0
    for member in session_members(session_id):
        try:
            descriptor = os.pidfd_open(member.pid)
        except ProcessLookupError:
            continue
        try:
            current = process_identity(member.pid)
            if current is None:
                continue
            if (
                current.start_time != member.start_time
                or current.session != session_id
            ):
                continue
            try:
                signal.pidfd_send_signal(descriptor, signal_number, None, 0)
            except ProcessLookupError:
                continue
            signaled += 1
        finally:
            os.close(descriptor)
    return signaled


def wait_for_session_exit(session_id: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while session_members(session_id):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def terminate_process_session(
    process: subprocess.Popen[Any],
    *,
    leader_start_time: int | None,
    session_id: int,
) -> None:
    current = process_identity(process.pid)
    if (
        leader_start_time is not None
        and current is not None
        and current.start_time != leader_start_time
    ):
        fail("refusing to signal a reused process identity")
    if current is not None and current.session != session_id:
        fail("refusing to signal a process with changed session identity")
    signal_session_members(session_id, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        signal_session_members(session_id, signal.SIGKILL)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            fail(f"simulation session leader survived SIGKILL: {session_id}")
    if not wait_for_session_exit(session_id, 10):
        signal_session_members(session_id, signal.SIGKILL)
        if not wait_for_session_exit(session_id, 10):
            fail(f"simulation session survived SIGKILL: {session_id}")


def terminate_unbound_new_session(process: subprocess.Popen[Any]) -> None:
    terminate_process_session(
        process, leader_start_time=None, session_id=process.pid
    )


def run_with_lock_monitor(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    log: Any,
    lock: SimulationLock,
    checkpoint_guard: CheckpointGuard,
    inputs_guard: CheckpointGuard,
) -> int:
    previous_mask = signal.pthread_sigmask(
        signal.SIG_BLOCK, TERMINATION_SIGNALS
    )
    process: subprocess.Popen[Any] | None = None
    leader: ProcessIdentity | None = None
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            preexec_fn=lambda: restore_signal_mask(previous_mask),
        )
        leader = process_identity(process.pid)
        if leader is None or not (
            leader.pid == leader.process_group == leader.session
        ):
            fail("simulation leader did not establish an owned process group")
    except BaseException:
        try:
            if process is not None:
                terminate_unbound_new_session(process)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        raise
    leader_start_time = leader.start_time
    session_id = leader.session
    try:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        while process.poll() is None:
            readable, _, _ = select.select(
                [
                    lock.watch_descriptor,
                    checkpoint_guard.descriptor,
                    inputs_guard.descriptor,
                ],
                [],
                [],
                1.0,
            )
            if lock.watch_descriptor in readable:
                verify_lock_identity(lock)
            if checkpoint_guard.descriptor in readable:
                verify_checkpoint_guard(checkpoint_guard)
            if inputs_guard.descriptor in readable:
                verify_checkpoint_guard(inputs_guard)
        return_code = int(process.wait())
        if session_members(session_id):
            if not wait_for_session_exit(session_id, 1):
                terminate_process_session(
                    process,
                    leader_start_time=leader_start_time,
                    session_id=session_id,
                )
                fail("simulation leader exited while descendants survived")
        verify_lock_identity(lock)
        verify_checkpoint_guard(checkpoint_guard)
        verify_checkpoint_guard(inputs_guard)
        return return_code
    except BaseException:
        terminate_process_session(
            process,
            leader_start_time=leader_start_time,
            session_id=session_id,
        )
        raise


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
                fail(f"stats section closes without opening: {path}")
            sections.append(current)
            current = None
        elif current is not None:
            current.append(line)
    if current is not None or len(sections) != 2:
        fail(f"expected exactly two complete stats sections: {path}")

    values: dict[str, int] = {
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
        elif re.fullmatch(r"system\.maa\.I[0-9]+_IND_VirtWriteIssues", name):
            key = "write_issues"
        elif re.fullmatch(
            r"system\.maa\.I[0-9]+_IND_VirtWriteCompletions", name
        ):
            key = "write_completions"
        elif re.fullmatch(
            r"system\.maa_retirement_caches[0-9]+\.demandHits_8::maa", name
        ):
            key = "cache_hits"
        elif re.fullmatch(
            r"system\.maa_retirement_caches[0-9]+\.demandMisses_8::maa", name
        ):
            key = "cache_misses"
        elif re.fullmatch(
            r"system\.maa_retirement_caches[0-9]+"
            r"\.writebacks_8::writebacks",
            name,
        ):
            key = "cache_writebacks"
        elif re.fullmatch(
            r"system\.maa_retirement_caches[0-9]+"
            r"\.demandMshrMisses_8::maa",
            name,
        ):
            key = "mshr_misses"
        elif re.fullmatch(
            r"system\.maa_retirement_caches[0-9]+"
            r"\.blockedCycles_T::no_mshrs",
            name,
        ):
            key = "blocked_no_mshrs_cycles"
        elif re.fullmatch(
            r"system\.maa_retirement_caches[0-9]+"
            r"\.blockedCauses_T::no_mshrs",
            name,
        ):
            key = "blocked_no_mshrs_events"
        elif re.fullmatch(
            r"system\.maa_retirement_caches[0-9]+" r"\.blockedCycles_T::no_wb",
            name,
        ):
            key = "blocked_no_wb_cycles"
        elif re.fullmatch(
            r"system\.maa_retirement_caches[0-9]+" r"\.blockedCauses_T::no_wb",
            name,
        ):
            key = "blocked_no_wb_events"
        else:
            continue
        if key in ("sim_ticks", "maa_cycles"):
            seen[key] = seen.get(key, 0) + 1
            values[key] = value
        else:
            values[key] += value
            seen[key] = seen.get(key, 0) + 1

    if seen.get("sim_ticks") != 1 or seen.get("maa_cycles") != 1:
        fail(f"missing unique timing metrics in first stats section: {path}")
    if seen.get("write_issues", 0) <= 0 or seen.get(
        "write_issues"
    ) != seen.get("write_completions"):
        fail(f"unbalanced virtual-write stat keys: {path}")
    for key in ("cache_misses", "cache_writebacks", "mshr_misses"):
        if seen.get(key) != 4:
            fail(f"expected four retirement-bank values for {key}: {path}")
    if seen.get("cache_hits", 0) not in (0, 4):
        fail(f"expected zero or four retirement-bank hit values: {path}")
    if values["sim_ticks"] <= 0 or values["maa_cycles"] <= 0:
        fail(f"nonpositive timing metrics: {path}")
    if (
        values["write_issues"] <= 0
        or values["write_issues"] != values["write_completions"]
        or values["cache_hits"] + values["cache_misses"]
        != values["write_issues"]
        or values["cache_writebacks"] > values["cache_misses"]
        or values["mshr_misses"] > values["cache_misses"]
    ):
        fail(f"virtual-retirement accounting is inconsistent: {path}")
    return values


def parse_cache_config(path: Path, candidate: Candidate) -> None:
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
        fail(f"expected four retirement caches in {path}")
    expected = {
        "size": str({"1kB": 1024, "256B": 256}[candidate.size]),
        "assoc": str(candidate.assoc),
        "response_latency": str(candidate.response_latency),
        "mshrs": str(candidate.mshrs),
        "tgts_per_mshr": str(candidate.targets_per_mshr),
        "write_buffers": str(candidate.write_buffers),
    }
    for bank in banks:
        for key, value in expected.items():
            if bank.get(key) != value:
                fail(
                    f"effective cache config mismatch for {key}: "
                    f"expected={value} actual={bank.get(key)!r}"
                )


def base_command(
    inputs: Path,
    output: Path,
    binary: Path,
    data: Path,
    candidate: Candidate,
) -> list[str]:
    gem5 = inputs / "bin/gem5.opt"
    config = inputs / "simulator/configs/deprecated/example/se.py"
    ramulator = inputs / "ramulator.yaml"
    return [
        str(gem5),
        "--listener-mode=off",
        f"--outdir={output}",
        str(config),
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
        str(ramulator),
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
        f"--maa_retirement_cache_size={candidate.size}",
        f"--maa_retirement_cache_assoc={candidate.assoc}",
        f"--maa_retirement_cache_response_latency={candidate.response_latency}",
        f"--maa_retirement_cache_mshrs={candidate.mshrs}",
        (
            "--maa_retirement_cache_targets_per_mshr="
            f"{candidate.targets_per_mshr}"
        ),
        f"--maa_retirement_cache_write_buffers={candidate.write_buffers}",
        "--cmd",
        str(binary),
        "--options",
        f"-f {data}",
    ]


def run_case(
    campaign: Path,
    inputs: Path,
    checkpoint_root: Path,
    binary: Path,
    data: Path,
    candidate: Candidate,
    phase: str,
    replica: int,
    expected_marker: str | None,
    simulation_lock: SimulationLock,
    inputs_guard: CheckpointGuard,
) -> dict[str, Any]:
    output = campaign / "runs" / phase / candidate.name / f"replica_{replica}"
    output.mkdir(parents=True)
    source_dir, source_entries = checkpoint_payload(
        checkpoint_root, "checkpoint_sha256.txt"
    )
    checkpoint_guard = create_checkpoint_guard(output, source_dir.name)
    for path, digest in source_entries.items():
        destination = output / path.relative_to(checkpoint_root)
        secure_copy_approved(path, destination, digest)
    verify_checkpoint_guard(checkpoint_guard, allow_initialization=True)
    copied_checkpoint = output / source_dir.name
    add_checkpoint_tree_watches(checkpoint_guard, copied_checkpoint)
    restore_manifest = output / "restore_checkpoint_sha256.txt"
    with restore_manifest.open("w", encoding="utf-8") as handle:
        for path, digest in sorted(
            source_entries.items(), key=lambda item: str(item[0])
        ):
            handle.write(f"{digest}  {path.relative_to(checkpoint_root)}\n")
    checkpoint_payload(output, "restore_checkpoint_sha256.txt")
    for path in (copied_checkpoint, *copied_checkpoint.rglob("*")):
        path.chmod(path.stat().st_mode | stat.S_IWUSR)
    verify_checkpoint_guard(checkpoint_guard, allow_initialization=True)
    checkpoint_payload(output, "restore_checkpoint_sha256.txt")
    verify_checkpoint_guard(checkpoint_guard)

    command = base_command(inputs, output, binary, data, candidate)
    write_command(output / "restore.command", command)
    environment = {
        "HOME": AUTH_ENV["HOME"],
        "LANG": "C",
        "LC_ALL": "C",
        "LD_LIBRARY_PATH": str(inputs / "lib"),
        "OMP_NUM_THREADS": "4",
        "OMP_PROC_BIND": "false",
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
    }
    started = time.monotonic()
    with (output / "restore.log").open("wb") as log:
        return_code = run_with_lock_monitor(
            command,
            cwd=inputs / "simulator",
            environment=environment,
            log=log,
            lock=simulation_lock,
            checkpoint_guard=checkpoint_guard,
            inputs_guard=inputs_guard,
        )
    wall_seconds = time.monotonic() - started
    (output / "restore.exit").write_text(f"{return_code}\n")
    checkpoint_payload(output, "restore_checkpoint_sha256.txt")
    verify_checkpoint_guard(checkpoint_guard)
    os.close(checkpoint_guard.root_descriptor)
    os.close(checkpoint_guard.descriptor)

    log_text = (output / "restore.log").read_text(errors="replace")
    roi_count = sum(line == "ROI End!!!" for line in log_text.splitlines())
    exit_count = log_text.count("because m5_exit instruction encountered")
    fatal_count = len(FATAL_RE.findall(log_text))
    markers = [
        line
        for line in log_text.splitlines()
        if MARKER_RE.fullmatch(line) is not None
    ]
    parse_error = ""
    stats = {
        "sim_ticks": -1,
        "maa_cycles": -1,
        "write_issues": -1,
        "write_completions": -1,
        "cache_hits": -1,
        "cache_misses": -1,
        "cache_writebacks": -1,
        "mshr_misses": -1,
        "blocked_no_mshrs_cycles": -1,
        "blocked_no_mshrs_events": -1,
        "blocked_no_wb_cycles": -1,
        "blocked_no_wb_events": -1,
    }
    try:
        stats = first_stats(output / "stats.txt")
        parse_cache_config(output / "config.ini", candidate)
    except RuntimeError as error:
        parse_error = str(error)
    valid = (
        return_code == 0
        and roi_count == 1
        and exit_count == 1
        and fatal_count == 0
        and not parse_error
        and (
            expected_marker is None
            or (len(markers) == 1 and markers[0] == expected_marker)
        )
    )
    result: dict[str, Any] = {
        "phase": phase,
        "candidate": candidate.name,
        "replica": replica,
        "return_code": return_code,
        "wall_seconds": round(wall_seconds, 6),
        "roi_count": roi_count,
        "exit_count": exit_count,
        "fatal_count": fatal_count,
        "marker": markers[0] if len(markers) == 1 else "NA",
        "parse_error": parse_error,
        "valid": int(valid),
        **stats,
    }
    (output / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    return result


def stage_inputs(
    args: argparse.Namespace,
    campaign: Path,
    expected_hashes: dict[str, str],
    reference_record: dict[str, Any],
) -> dict[str, Any]:
    inputs = campaign / "inputs"
    inputs.mkdir()
    reference_campaign = args.reference_campaign.resolve(strict=True)
    artifacts = {
        "gem5": regular_file(args.gem5.resolve(strict=True), executable=True),
        "ramulator_yaml": regular_file(
            args.ramulator_yaml.resolve(strict=True)
        ),
        "ramulator_lib": regular_file(args.ramulator_lib.resolve(strict=True)),
        "virtual_verify": regular_file(
            args.virtual_verify_bin.resolve(strict=True), executable=True
        ),
        "virtual_perf": regular_file(
            args.virtual_perf_bin.resolve(strict=True), executable=True
        ),
        "input_20k": regular_file(args.input_20k.resolve(strict=True)),
        "input_full": regular_file(args.input_full.resolve(strict=True)),
        "runner": regular_file(
            Path(__file__).resolve(strict=True), executable=True
        ),
        "launcher": regular_file(
            args.launcher.resolve(strict=True), executable=True
        ),
        "verifier": regular_file(
            args.ablation_verifier.resolve(strict=True), executable=True
        ),
        "reference_approval": regular_file(
            args.reference_approval.resolve(strict=True)
        ),
        "reference_result_approval": regular_file(
            args.reference_result_approval.resolve(strict=True)
        ),
        **{
            f"reference_config_{replica}": regular_file(
                reference_campaign
                / f"runs/virtual/replica_{replica}/config.ini"
            )
            for replica in (1, 2, 3)
        },
        "reference_results": regular_file(reference_campaign / "results.tsv"),
        "reference_source": regular_file(reference_campaign / "source.txt"),
        "reference_attribution": regular_file(
            reference_campaign / "attribution.tsv"
        ),
        "reference_staged_input_manifest": regular_file(
            reference_campaign / "staged_input_sha256.txt"
        ),
        "reference_evidence_manifest": regular_file(
            reference_campaign / "evidence_sha256.txt"
        ),
        "reference_checkpoint_manifest": regular_file(
            reference_campaign
            / "checkpoints/virtual/private_checkpoint_sha256.txt"
        ),
        "source_full_config": regular_file(
            args.source_full_correctness.resolve(strict=True)
            / "runs/virtual/config.ini"
        ),
        "reference_verifier": regular_file(
            args.reference_verifier.resolve(strict=True), executable=True
        ),
        "bfs_approval": regular_file(args.bfs_approval.resolve(strict=True)),
        "bfs_oracle": regular_file(args.bfs_oracle.resolve(strict=True)),
    }
    destinations = {
        "gem5": inputs / "bin/gem5.opt",
        "ramulator_yaml": inputs / "ramulator.yaml",
        "ramulator_lib": inputs / "lib/libramulator.so",
        "virtual_verify": inputs / "benchmark/xrage_virtual_verify",
        "virtual_perf": inputs / "benchmark/xrage_virtual",
        "input_20k": inputs / "benchmark/xrage_20k.json",
        "input_full": inputs / "benchmark/xrage_full.json",
        "runner": inputs / "runner.py",
        "launcher": inputs / "launcher.py",
        "verifier": inputs / "verifier.py",
        "reference_approval": inputs / "manifests/reference_approval.json",
        "reference_result_approval": (
            inputs / "manifests/reference_result_approval.json"
        ),
        **{
            f"reference_config_{replica}": (
                inputs
                / f"reference_evidence/virtual_config_replica_{replica}.ini"
            )
            for replica in (1, 2, 3)
        },
        "reference_results": inputs / "reference_evidence/results.tsv",
        "reference_source": inputs / "reference_evidence/source.txt",
        "reference_attribution": (
            inputs / "reference_evidence/attribution.tsv"
        ),
        "reference_staged_input_manifest": (
            inputs / "reference_evidence/staged_input_sha256.txt"
        ),
        "reference_evidence_manifest": (
            inputs / "reference_evidence/evidence_sha256.txt"
        ),
        "reference_checkpoint_manifest": (
            inputs / "reference_evidence/private_checkpoint_sha256.txt"
        ),
        "source_full_config": inputs / "reference/full_correctness_config.ini",
        "reference_verifier": inputs / "reference_verifier.py",
        "bfs_approval": inputs / "manifests/bfs_approval.json",
        "bfs_oracle": inputs / "manifests/bfs_oracle.json",
    }
    if set(artifacts) != set(expected_hashes):
        fail("expected artifact hash closure differs")
    for name, source in artifacts.items():
        secure_copy_approved(source, destinations[name], expected_hashes[name])

    sim_root = args.sim_root.resolve(strict=True)
    tracked_configs = subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(sim_root),
            "ls-tree",
            "-rz",
            "--name-only",
            args.expected_sim_commit,
            "--",
            "configs",
        ],
        check=True,
        env=AUTH_ENV,
        stdout=subprocess.PIPE,
    ).stdout.split(b"\0")
    for raw in tracked_configs:
        if not raw:
            continue
        relative = Path(os.fsdecode(raw))
        committed = subprocess.run(
            [
                "/usr/bin/git",
                "-C",
                str(sim_root),
                "show",
                f"{args.expected_sim_commit}:{relative}",
            ],
            check=True,
            env=AUTH_ENV,
            stdout=subprocess.PIPE,
        ).stdout
        destination = inputs / "simulator" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(committed)

    checkpoint_sources = {
        "screen": (
            args.source_20k.resolve(strict=True) / "checkpoints/virtual",
            "checkpoint_sha256.txt",
        ),
        "full_correctness": (
            args.source_full_correctness.resolve(strict=True)
            / "checkpoints/virtual",
            "checkpoint_sha256.txt",
        ),
        "full_performance": (
            args.reference_campaign.resolve(strict=True)
            / "checkpoints/virtual",
            "private_checkpoint_sha256.txt",
        ),
    }
    for name, (source_root, manifest_name) in checkpoint_sources.items():
        stage_checkpoint(
            source_root, manifest_name, inputs / "checkpoints" / name
        )
    _, staged_performance_entries = checkpoint_payload(
        inputs / "checkpoints/full_performance",
        "checkpoint_sha256.txt",
    )
    staged_performance = {
        str(path.relative_to(inputs / "checkpoints/full_performance")): digest
        for path, digest in staged_performance_entries.items()
    }
    if staged_performance != nested(
        reference_record,
        "evidence",
        "virtual_checkpoint_payload_sha256",
    ):
        fail("staged performance checkpoint differs from approval")

    manifest_entries: dict[str, str] = {}
    for path in sorted(inputs.rglob("*")):
        if path.is_symlink():
            fail(f"staged input contains a symlink: {path}")
        if path.is_file():
            manifest_entries[str(path.relative_to(inputs))] = file_sha256(path)
    manifest = campaign / "staged_input_sha256.json"
    manifest.write_text(
        json.dumps(manifest_entries, indent=2, sort_keys=True) + "\n"
    )
    for path in inputs.rglob("*"):
        mode = path.stat().st_mode
        path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    return {
        "paths": {name: str(path) for name, path in artifacts.items()},
        "sha256": expected_hashes,
        "staged_manifest_sha256": file_sha256(manifest),
    }


def verify_staged_inputs(
    campaign: Path, expected_manifest_sha256: str
) -> None:
    inputs = campaign / "inputs"
    manifest_path = regular_file(campaign / "staged_input_sha256.json")
    expect_hash(
        "staged input manifest",
        manifest_path,
        expected_manifest_sha256,
    )
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, dict) or not manifest:
        fail("staged input manifest is empty or malformed")
    actual_paths = {
        str(path.relative_to(inputs))
        for path in inputs.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual_paths != set(manifest):
        fail("staged input path closure changed")
    for relative, expected in manifest.items():
        expect_hash("staged input", inputs / relative, expected)


def evidence_entries(campaign: Path) -> dict[str, str]:
    manifest = regular_file(campaign / "evidence_sha256.txt")
    entries: dict[str, str] = {}
    for line_number, line in enumerate(manifest.read_text().splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
        if match is None:
            fail(f"malformed reference evidence line {line_number}")
        digest, raw_path = match.groups()
        path = Path(raw_path)
        if not path.is_absolute():
            path = campaign / path
        reject_symlink_components(path)
        resolved = regular_file(path.resolve(strict=True))
        try:
            relative = str(resolved.relative_to(campaign))
        except ValueError:
            fail(f"reference evidence escapes its campaign: {resolved}")
        if relative in entries:
            fail(f"duplicate reference evidence path: {relative}")
        entries[relative] = digest
    if not entries:
        fail("reference evidence manifest is empty")
    return entries


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


def reference_result_record(
    result_approval: Path,
    reference_campaign: Path,
    reference_approval: Path,
    reference_verifier: Path,
) -> dict[str, Any]:
    record = load_json(result_approval)
    if (
        record.get("schema_version") != 2
        or record.get("experiment_id")
        != "xrage-replicated-reference-result-v2"
        or Path(record.get("reference_campaign", "")).resolve(strict=True)
        != reference_campaign
        or record.get("reference_approval_sha256")
        != file_sha256(reference_approval)
        or record.get("reference_verifier_sha256")
        != file_sha256(reference_verifier)
    ):
        fail("reference-result approval identity differs")
    binary_commit = record.get("binary_simulator_commit")
    if not isinstance(binary_commit, str) or not re.fullmatch(
        r"[0-9a-f]{40}", binary_commit
    ):
        fail("reference-result binary commit is malformed")
    evidence = record.get("evidence")
    if not isinstance(evidence, dict):
        fail("reference-result evidence binding is malformed")
    expected_files = {
        "evidence_sha256.txt": evidence.get("manifest_sha256"),
        "results.tsv": evidence.get("results_sha256"),
        "source.txt": evidence.get("source_sha256"),
        "attribution.tsv": evidence.get("attribution_sha256"),
        "staged_input_sha256.txt": evidence.get(
            "staged_input_manifest_sha256"
        ),
        "checkpoints/virtual/private_checkpoint_sha256.txt": evidence.get(
            "virtual_checkpoint_manifest_sha256"
        ),
    }
    config_hashes = evidence.get("virtual_config_sha256")
    if not isinstance(config_hashes, dict) or set(config_hashes) != {
        "1",
        "2",
        "3",
    }:
        fail("reference-result config hash closure differs")
    approved_semantic = evidence.get("virtual_config_semantic_sha256")
    if not isinstance(approved_semantic, str) or not SHA256_RE.fullmatch(
        approved_semantic
    ):
        fail("reference-result semantic config hash is malformed")
    expected_files.update(
        {
            f"runs/virtual/replica_{replica}/config.ini": digest
            for replica, digest in config_hashes.items()
        }
    )
    if any(
        not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None
        for digest in expected_files.values()
    ):
        fail("reference-result evidence hash is malformed")
    empty_marker(reference_campaign / "campaign.pass")
    if (reference_campaign / "campaign.fail").exists():
        fail("reference campaign has a fail state")
    for relative, digest in expected_files.items():
        expect_hash(
            f"approved reference evidence {relative}",
            reference_campaign / relative,
            str(digest),
        )
    manifest_entries = evidence_entries(reference_campaign)
    for relative, digest in expected_files.items():
        if relative == "evidence_sha256.txt":
            continue
        if manifest_entries.get(relative) != digest:
            fail(f"reference evidence manifest differs: {relative}")
    semantic_hashes = {
        semantic_config_sha256(
            reference_campaign / f"runs/virtual/replica_{replica}/config.ini"
        )
        for replica in (1, 2, 3)
    }
    if semantic_hashes != {approved_semantic}:
        fail("reference virtual replica configs are not semantically equal")

    fields, rows = read_tsv(reference_campaign / "results.tsv")
    if not {"arm", "replica", "sim_ticks", "valid"}.issubset(fields):
        fail("reference campaign result schema is incomplete")
    virtual = [row for row in rows if row["arm"] == "virtual"]
    if (
        len(virtual) != 3
        or {row["replica"] for row in virtual} != {"1", "2", "3"}
        or any(row["valid"] != "1" for row in virtual)
    ):
        fail("reference campaign lacks exact valid virtual replicas 1,2,3")
    ticks = {row["sim_ticks"] for row in virtual}
    if len(ticks) != 1 or not next(iter(ticks)).isdigit():
        fail("reference virtual replicas are not deterministic")
    value = int(next(iter(ticks)))
    approved_replicas = record.get("virtual_replicas")
    expected_replicas = [
        {"replica": int(row["replica"]), "sim_ticks": int(row["sim_ticks"])}
        for row in sorted(virtual, key=lambda item: int(item["replica"]))
    ]
    if (
        value <= 0
        or record.get("virtual_sim_ticks") != value
        or approved_replicas != expected_replicas
    ):
        fail("reference-result tick approval differs")

    checkpoint_root = reference_campaign / "checkpoints/virtual"
    _, checkpoint_entries = checkpoint_payload(
        checkpoint_root, "private_checkpoint_sha256.txt"
    )
    approved_checkpoint = evidence.get("virtual_checkpoint_payload_sha256")
    actual_checkpoint = {
        str(path.relative_to(checkpoint_root)): digest
        for path, digest in checkpoint_entries.items()
    }
    if approved_checkpoint != actual_checkpoint:
        fail("reference-result checkpoint payload approval differs")
    return record


def write_results(campaign: Path, records: list[dict[str, Any]]) -> None:
    fields = [
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
    ]
    with (campaign / "results.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(
            {field: row[field] for field in fields} for row in records
        )


def evidence_manifest(campaign: Path) -> None:
    included: list[Path] = []
    for path in campaign.rglob("*"):
        if not path.is_file() or path.name in {
            "campaign.pass",
            "campaign.fail",
            "execution.complete",
            "evidence_sha256.txt",
        }:
            continue
        if "inputs" in path.relative_to(campaign).parts:
            continue
        included.append(path)
    with (campaign / "evidence_sha256.txt").open(
        "w", encoding="utf-8"
    ) as handle:
        for path in sorted(included):
            handle.write(
                f"{file_sha256(path)}  {path.relative_to(campaign)}\n"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim-root", type=Path, required=True)
    parser.add_argument("--expected-sim-commit", required=True)
    parser.add_argument("--launcher", type=Path, required=True)
    parser.add_argument("--ablation-verifier", type=Path, required=True)
    parser.add_argument("--gem5", type=Path, required=True)
    parser.add_argument("--ramulator-yaml", type=Path, required=True)
    parser.add_argument("--ramulator-lib", type=Path, required=True)
    parser.add_argument("--virtual-verify-bin", type=Path, required=True)
    parser.add_argument("--virtual-perf-bin", type=Path, required=True)
    parser.add_argument("--input-20k", type=Path, required=True)
    parser.add_argument("--input-full", type=Path, required=True)
    parser.add_argument("--source-20k", type=Path, required=True)
    parser.add_argument("--source-full-correctness", type=Path, required=True)
    parser.add_argument("--reference-campaign", type=Path, required=True)
    parser.add_argument("--reference-approval", type=Path, required=True)
    parser.add_argument(
        "--reference-result-approval", type=Path, required=True
    )
    parser.add_argument("--reference-verifier", type=Path, required=True)
    parser.add_argument("--bfs-campaign", type=Path, required=True)
    parser.add_argument("--bfs-approval", type=Path, required=True)
    parser.add_argument("--bfs-oracle", type=Path, required=True)
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
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--publication-name", required=True)
    parser.add_argument(
        "--minimum-available-kib", type=int, default=24 * 1024 * 1024
    )
    parser.add_argument("--capacity-poll-seconds", type=int, default=600)
    parser.add_argument("--screen-overhead-limit", type=float, default=0.01)
    parser.add_argument("--full-overhead-limit", type=float, default=0.01)
    return parser.parse_args()


def main() -> None:
    install_termination_handlers()
    args = parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_sim_commit):
        fail("--expected-sim-commit must be a full Git object ID")
    expected_sha_values = (
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
    if any(not SHA256_RE.fullmatch(value) for value in expected_sha_values):
        fail("all expected artifact hashes must be full SHA-256 values")
    if args.minimum_available_kib < 0 or args.capacity_poll_seconds <= 0:
        fail("capacity controls must be nonnegative/positive")
    for limit in (args.screen_overhead_limit, args.full_overhead_limit):
        if not 0 <= limit <= 0.10:
            fail("overhead limits must be between 0 and 0.10")

    sim_root = args.sim_root.resolve(strict=True)
    verify_git_state(sim_root, args.expected_sim_commit)
    current_commit = args.expected_sim_commit
    expect_hash(
        "authorized runner",
        Path(__file__).resolve(strict=True),
        args.expected_runner_sha,
    )
    expect_hash(
        "authorized launcher",
        args.launcher.resolve(strict=True),
        args.expected_launcher_sha,
    )
    expect_hash(
        "authorized ablation verifier",
        args.ablation_verifier.resolve(strict=True),
        args.expected_verifier_sha,
    )

    output = args.output.resolve()
    if (
        Path(args.publication_name).name != args.publication_name
        or re.fullmatch(
            rf"\.staging\.{re.escape(args.publication_name)}\.[0-9a-f]{{32}}",
            output.name,
        )
        is None
    ):
        fail("output staging name does not bind the publication name")
    if output.exists():
        info = output.lstat()
        if (
            output.is_symlink()
            or not output.is_dir()
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or any(output.iterdir())
        ):
            fail(f"precreated output staging directory is unsafe: {output}")
    else:
        output.mkdir(parents=True, mode=0o700)
    try:
        source_20k = args.source_20k.resolve(strict=True)
        source_full = args.source_full_correctness.resolve(strict=True)
        reference = args.reference_campaign.resolve(strict=True)
        bfs_campaign = args.bfs_campaign.resolve(strict=True)
        reference_verifier = args.reference_verifier.resolve(strict=True)
        reference_approval = args.reference_approval.resolve(strict=True)
        reference_result_approval = args.reference_result_approval.resolve(
            strict=True
        )
        bfs_approval = args.bfs_approval.resolve(strict=True)
        bfs_oracle = args.bfs_oracle.resolve(strict=True)
        expect_hash(
            "authorized reference verifier",
            reference_verifier,
            args.expected_reference_verifier_sha,
        )
        expect_hash(
            "authorized reference approval",
            reference_approval,
            args.expected_reference_approval_sha,
        )
        expect_hash(
            "authorized reference-result approval",
            reference_result_approval,
            args.expected_reference_result_approval_sha,
        )
        expect_hash(
            "authorized 20K input",
            args.input_20k.resolve(strict=True),
            args.expected_input_20k_sha,
        )
        expect_hash(
            "authorized BFS approval",
            bfs_approval,
            args.expected_bfs_approval_sha,
        )
        expect_hash(
            "authorized BFS oracle",
            bfs_oracle,
            args.expected_bfs_oracle_sha,
        )
        if (
            correctness_campaign_fingerprint(source_20k)
            != args.expected_source_20k_fingerprint
        ):
            fail("20K source correctness fingerprint differs")
        if (
            correctness_campaign_fingerprint(source_full)
            != args.expected_source_full_fingerprint
        ):
            fail("full source correctness fingerprint differs")
        marker_20k = correctness_marker(source_20k)
        marker_full = correctness_marker(source_full)
        reference_record = reference_result_record(
            reference_result_approval,
            reference,
            reference_approval,
            reference_verifier,
        )
        bfs_verification = run_python_fd(
            reference_verifier,
            args.expected_reference_verifier_sha,
            [
                "bfs",
                str(bfs_campaign),
                str(bfs_approval),
                "--oracle",
                str(bfs_oracle),
            ],
        )
        (output / "bfs_verification.log").write_text(
            bfs_verification.stdout, encoding="utf-8"
        )
        if bfs_verification.returncode != 0:
            fail("external upstream BFS verification failed")
        expect_hash(
            "reference verifier after dependency checks",
            reference_verifier,
            args.expected_reference_verifier_sha,
        )
        expect_hash(
            "BFS approval after verification",
            bfs_approval,
            args.expected_bfs_approval_sha,
        )
        expect_hash(
            "BFS oracle after verification",
            bfs_oracle,
            args.expected_bfs_oracle_sha,
        )
        upstream_ref_ticks = int(reference_record["virtual_sim_ticks"])
        approval = load_json(reference_approval)
        binary_commit = nested(approval, "candidate", "simulator_commit")
        if (
            binary_commit != reference_record["binary_simulator_commit"]
            or not isinstance(binary_commit, str)
            or not re.fullmatch(r"[0-9a-f]{40}", binary_commit)
        ):
            fail("reference binary-commit approvals differ")
        ancestry = subprocess.run(
            [
                "/usr/bin/git",
                "-C",
                str(sim_root),
                "merge-base",
                "--is-ancestor",
                binary_commit,
                current_commit,
            ],
            check=False,
            env=AUTH_ENV,
        )
        if ancestry.returncode != 0:
            fail("workflow/config commit does not descend from binary commit")
        changed_paths = str(
            git_output(
                sim_root,
                "diff",
                "--name-only",
                f"{binary_commit}..{current_commit}",
            )
        ).splitlines()
        if any(
            not (
                path.startswith("configs/") or path.startswith("experiments/")
            )
            for path in changed_paths
        ):
            fail("binary-incompatible source changed after the approved build")
        expected_hashes = {
            "gem5": nested(approval, "candidate", "gem5_sha256"),
            "ramulator_yaml": nested(
                approval, "candidate", "ramulator_sha256"
            ),
            "ramulator_lib": nested(
                approval, "candidate", "ramulator_library_sha256"
            ),
            "virtual_verify": nested(
                approval, "verifier_binaries", "virtual_sha256"
            ),
            "virtual_perf": nested(
                approval, "benchmark_binaries", "virtual_sha256"
            ),
            "input_20k": args.expected_input_20k_sha,
            "input_full": nested(approval, "workload", "input_sha256"),
            "runner": args.expected_runner_sha,
            "launcher": args.expected_launcher_sha,
            "verifier": args.expected_verifier_sha,
            "reference_approval": args.expected_reference_approval_sha,
            "reference_result_approval": (
                args.expected_reference_result_approval_sha
            ),
            **{
                f"reference_config_{replica}": nested(
                    reference_record,
                    "evidence",
                    "virtual_config_sha256",
                    str(replica),
                )
                for replica in (1, 2, 3)
            },
            "reference_results": nested(
                reference_record, "evidence", "results_sha256"
            ),
            "reference_source": nested(
                reference_record, "evidence", "source_sha256"
            ),
            "reference_attribution": nested(
                reference_record, "evidence", "attribution_sha256"
            ),
            "reference_staged_input_manifest": nested(
                reference_record,
                "evidence",
                "staged_input_manifest_sha256",
            ),
            "reference_evidence_manifest": nested(
                reference_record, "evidence", "manifest_sha256"
            ),
            "reference_checkpoint_manifest": nested(
                reference_record,
                "evidence",
                "virtual_checkpoint_manifest_sha256",
            ),
            "source_full_config": nested(
                approval,
                "correctness_campaign",
                "runs",
                "virtual",
                "config_ini_sha256",
            ),
            "reference_verifier": args.expected_reference_verifier_sha,
            "bfs_approval": args.expected_bfs_approval_sha,
            "bfs_oracle": args.expected_bfs_oracle_sha,
        }
        if any(
            not isinstance(value, str) or not SHA256_RE.fullmatch(value)
            for value in expected_hashes.values()
        ):
            fail("approved staged artifact hashes are malformed")
        source = {
            "schema_version": 1,
            "simulator_commit": current_commit,
            "workflow_config_commit": current_commit,
            "execution_root": str(output),
            "publication_name": args.publication_name,
            "binary_simulator_commit": binary_commit,
            "execution": "serial",
            "wall_clock_timeout": "none",
            "environment_policy": "sanitized-private-home-fd-exec-v3",
            "simulation_lock": str(SIMULATION_LOCK),
            "minimum_available_kib": args.minimum_available_kib,
            "screen_overhead_limit": args.screen_overhead_limit,
            "full_overhead_limit": args.full_overhead_limit,
            "upstream_reference_sim_ticks": upstream_ref_ticks,
            "reference_campaign": str(reference),
            "reference_result_approval": str(reference_result_approval),
            "bfs_campaign": str(bfs_campaign),
            "source_20k_campaign": str(source_20k),
            "source_full_correctness_campaign": str(source_full),
            "source_20k_fingerprint": args.expected_source_20k_fingerprint,
            "source_full_fingerprint": args.expected_source_full_fingerprint,
            "screen_expected_marker": marker_20k,
            "full_expected_marker": marker_full,
            "candidates": [asdict(candidate) for candidate in CANDIDATES],
            "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        staged = stage_inputs(args, output, expected_hashes, reference_record)
        source["source_artifacts"] = staged
        staged_manifest_sha256 = staged["staged_manifest_sha256"]
        if (
            correctness_campaign_fingerprint(source_20k)
            != args.expected_source_20k_fingerprint
            or correctness_campaign_fingerprint(source_full)
            != args.expected_source_full_fingerprint
        ):
            fail("a source correctness campaign changed during staging")
        (output / "source.json").write_text(
            json.dumps(source, indent=2, sort_keys=True) + "\n"
        )
        inputs_guard = create_checkpoint_guard(output, "inputs")
        add_checkpoint_tree_watches(inputs_guard, output / "inputs")
        verify_staged_inputs(output, staged_manifest_sha256)
        verify_checkpoint_guard(inputs_guard)

        simulation_lock = wait_for_capacity(
            args.minimum_available_kib, args.capacity_poll_seconds
        )
        try:
            records: list[dict[str, Any]] = []
            inputs = output / "inputs"
            screen_checkpoint = inputs / "checkpoints/screen"
            full_correctness_checkpoint = (
                inputs / "checkpoints/full_correctness"
            )
            full_performance_checkpoint = (
                inputs / "checkpoints/full_performance"
            )
            verify_bin = inputs / "benchmark/xrage_virtual_verify"
            perf_bin = inputs / "benchmark/xrage_virtual"
            input_20k = inputs / "benchmark/xrage_20k.json"
            input_full = inputs / "benchmark/xrage_full.json"

            screen_results: dict[str, dict[str, Any]] = {}
            for candidate in CANDIDATES:
                verify_lock_identity(simulation_lock)
                verify_staged_inputs(output, staged_manifest_sha256)
                result = run_case(
                    output,
                    inputs,
                    screen_checkpoint,
                    verify_bin,
                    input_20k,
                    candidate,
                    "screen_correctness",
                    1,
                    marker_20k,
                    simulation_lock,
                    inputs_guard,
                )
                records.append(result)
                screen_results[candidate.name] = result
                if result["valid"] != 1:
                    fail(f"invalid screen result for {candidate.name}")

            if screen_results["reference"]["valid"] != 1:
                fail("reference screen case is invalid")
            screen_reference = screen_results["reference"]["sim_ticks"]
            eligible = {
                name
                for name in PROMOTION_ORDER
                if screen_results[name]["valid"] == 1
                and screen_results[name]["sim_ticks"]
                <= screen_reference * (1 + args.screen_overhead_limit)
            }
            ref_ticks: int | None = upstream_ref_ticks if eligible else None
            candidate_correctness: dict[str, dict[str, Any]] = {}
            if eligible:
                first_eligible = next(
                    name for name in PROMOTION_ORDER if name in eligible
                )
                first_candidate = next(
                    item for item in CANDIDATES if item.name == first_eligible
                )
                verify_lock_identity(simulation_lock)
                verify_staged_inputs(output, staged_manifest_sha256)
                first_correctness = run_case(
                    output,
                    inputs,
                    full_correctness_checkpoint,
                    verify_bin,
                    input_full,
                    first_candidate,
                    "full_correctness",
                    1,
                    marker_full,
                    simulation_lock,
                    inputs_guard,
                )
                records.append(first_correctness)
                candidate_correctness[first_eligible] = first_correctness
                if first_correctness["valid"] != 1:
                    fail(f"full correctness failed for {first_candidate.name}")

            selection_rows: list[dict[str, Any]] = []
            promoted: Candidate | None = None
            promoted_ticks: int | None = None
            for name in PROMOTION_ORDER:
                candidate = next(
                    item for item in CANDIDATES if item.name == name
                )
                row: dict[str, Any] = {
                    "candidate": name,
                    "screen_eligible": int(name in eligible),
                    "full_correct": 0,
                    "full_deterministic": 0,
                    "reference_ticks": (
                        ref_ticks if ref_ticks is not None else "NA"
                    ),
                    "candidate_ticks": "NA",
                    "overhead_fraction": "NA",
                    "promoted": 0,
                }
                if name not in eligible:
                    selection_rows.append(row)
                    continue
                correctness = candidate_correctness.get(name)
                if correctness is None:
                    verify_lock_identity(simulation_lock)
                    verify_staged_inputs(output, staged_manifest_sha256)
                    correctness = run_case(
                        output,
                        inputs,
                        full_correctness_checkpoint,
                        verify_bin,
                        input_full,
                        candidate,
                        "full_correctness",
                        1,
                        marker_full,
                        simulation_lock,
                        inputs_guard,
                    )
                    records.append(correctness)
                    candidate_correctness[name] = correctness
                row["full_correct"] = correctness["valid"]
                if correctness["valid"] != 1:
                    fail(f"full correctness failed for {candidate.name}")
                performance: list[dict[str, Any]] = []
                for replica in range(1, 4):
                    verify_lock_identity(simulation_lock)
                    verify_staged_inputs(output, staged_manifest_sha256)
                    result = run_case(
                        output,
                        inputs,
                        full_performance_checkpoint,
                        perf_bin,
                        input_full,
                        candidate,
                        "full_performance",
                        replica,
                        None,
                        simulation_lock,
                        inputs_guard,
                    )
                    records.append(result)
                    performance.append(result)
                if any(result["valid"] != 1 for result in performance):
                    fail(f"full performance failed for {candidate.name}")
                ticks = {result["sim_ticks"] for result in performance}
                row["full_deterministic"] = int(len(ticks) == 1)
                if len(ticks) != 1:
                    selection_rows.append(row)
                    continue
                if ref_ticks is None:
                    fail("eligible candidate has no approved reference")
                candidate_ticks = int(next(iter(ticks)))
                overhead = candidate_ticks / ref_ticks - 1
                row["candidate_ticks"] = candidate_ticks
                row["overhead_fraction"] = f"{overhead:.9f}"
                if overhead <= args.full_overhead_limit:
                    row["promoted"] = 1
                    promoted = candidate
                    promoted_ticks = candidate_ticks
                    selection_rows.append(row)
                    break
                selection_rows.append(row)

            write_results(output, records)
            selection_fields = [
                "candidate",
                "screen_eligible",
                "full_correct",
                "full_deterministic",
                "reference_ticks",
                "candidate_ticks",
                "overhead_fraction",
                "promoted",
            ]
            with (output / "selection.tsv").open(
                "w", encoding="utf-8", newline=""
            ) as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=selection_fields, delimiter="\t"
                )
                writer.writeheader()
                writer.writerows(selection_rows)

            if promoted is None or promoted_ticks is None:
                atomic_write(output / "no_promotion", "", 0o444)
                summary = {
                    "status": "complete_without_promotion",
                    "reference_sim_ticks": ref_ticks,
                }
            else:
                if ref_ticks is None:
                    fail("promoted candidate has no approved reference")
                data_bytes_per_bank = {"1kB": 1024, "256B": 256}[promoted.size]
                summary = {
                    "status": "promoted",
                    "candidate": asdict(promoted),
                    "reference_sim_ticks": ref_ticks,
                    "candidate_sim_ticks": promoted_ticks,
                    "speedup": ref_ticks / promoted_ticks,
                    "elapsed_change_percent": (promoted_ticks / ref_ticks - 1)
                    * 100,
                    "retirement_cache_data_bytes_total": data_bytes_per_bank
                    * 4,
                    "retirement_cache_target_slots_total": (
                        promoted.mshrs * promoted.targets_per_mshr * 4
                    ),
                    "cost_scope": (
                        "retirement-cache data and target slots only; no "
                        "whole-DX100 area or power claim"
                    ),
                }
            (output / "summary.json").write_text(
                json.dumps(summary, indent=2, sort_keys=True) + "\n"
            )
            verify_staged_inputs(output, staged_manifest_sha256)
            verify_checkpoint_guard(inputs_guard)
            verify_lock_identity(simulation_lock)
            evidence_manifest(output)
            atomic_write(output / "execution.complete", "", 0o444)
        finally:
            os.close(simulation_lock.watch_descriptor)
            fcntl.flock(simulation_lock.descriptor, fcntl.LOCK_UN)
            os.close(simulation_lock.descriptor)
            os.close(inputs_guard.root_descriptor)
            os.close(inputs_guard.descriptor)
    except BaseException as error:
        atomic_write(
            output / "campaign.fail",
            f"{type(error).__name__}: {error}\n",
            0o444,
        )
        raise


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(f"retirement-cache ablation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
