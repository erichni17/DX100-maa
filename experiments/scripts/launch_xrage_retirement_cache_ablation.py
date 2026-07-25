#!/bin/false
"""Launch and atomically publish the XRAGE retirement-cache ablation."""

from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
RUNTIME_ROOT = Path("/data1/nier/.dx-runtime-state/retirement-cache-ablation")
PUBLICATION_LOCK = RUNTIME_ROOT / "publication.lock"
TERMINATION_SIGNALS = frozenset((signal.SIGTERM, signal.SIGHUP, signal.SIGINT))
INITIAL_ENV = {
    "DX100_SANITIZED_LAUNCH": "1",
    "HOME": "/data1/nier/.dx-runtime-state",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
}
SEALED_BOOTSTRAP_SOURCE = """\
import fcntl
import hashlib
import os
import stat
import sys

if len(sys.argv) < 3:
    raise SystemExit("usage: bootstrap LAUNCHER EXPECTED_SHA [ARGS...]")
source_path = sys.argv[1]
expected_sha = sys.argv[2]
source_fd = os.open(
    source_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
)
memory_fd = -1
try:
    before = os.fstat(source_fd)
    if not stat.S_ISREG(before.st_mode):
        raise SystemExit("launcher source is not a regular file")
    memory_fd = os.memfd_create(
        "dx100-retirement-cache-launcher",
        os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
    )
    digest = hashlib.sha256()
    while True:
        block = os.read(source_fd, 1024 * 1024)
        if not block:
            break
        digest.update(block)
        view = memoryview(block)
        while view:
            written = os.write(memory_fd, view)
            view = view[written:]
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
        raise SystemExit("launcher source changed while authenticating")
    if digest.hexdigest() != expected_sha:
        raise SystemExit("launcher SHA-256 mismatch")
    seals = (
        fcntl.F_SEAL_SEAL
        | fcntl.F_SEAL_SHRINK
        | fcntl.F_SEAL_GROW
        | fcntl.F_SEAL_WRITE
    )
    fcntl.fcntl(memory_fd, fcntl.F_ADD_SEALS, seals)
    if fcntl.fcntl(memory_fd, fcntl.F_GET_SEALS) != seals:
        raise SystemExit("launcher memory file was not fully sealed")
    os.lseek(memory_fd, 0, os.SEEK_SET)
    os.set_inheritable(memory_fd, True)
finally:
    os.close(source_fd)
if memory_fd < 0:
    raise SystemExit("failed to create sealed launcher")
os.execve(
    "/usr/bin/python3",
    [
        "/usr/bin/python3",
        "-I",
        f"/proc/self/fd/{memory_fd}",
        expected_sha,
        *sys.argv[3:],
    ],
    os.environ,
)
"""
FORBIDDEN_ENV = {
    "BASH_ENV",
    "CDPATH",
    "ENV",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_WORK_TREE",
    "LD_AUDIT",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "PYTHONHOME",
    "PYTHONPATH",
}
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


@dataclass
class TreeGuard:
    root: Path
    root_fd: int
    watch_fd: int
    parent_watch: int
    tree_watches: set[int]
    device: int
    inode: int


@dataclass(frozen=True)
class DirectoryAnchor:
    path: Path
    descriptor: int
    device: int
    inode: int


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


def fail(message: str) -> None:
    raise RuntimeError(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("expected_self_sha")
    parser.add_argument("expected_runner_sha")
    parser.add_argument("expected_verifier_sha")
    parser.add_argument("expected_reference_verifier_sha")
    parser.add_argument("expected_sim_commit")
    parser.add_argument("sim_root", type=Path)
    parser.add_argument("bfs_campaign", type=Path)
    parser.add_argument("bfs_approval", type=Path)
    parser.add_argument("expected_bfs_approval_sha")
    parser.add_argument("bfs_oracle", type=Path)
    parser.add_argument("expected_bfs_oracle_sha")
    parser.add_argument("expected_input_20k_sha")
    parser.add_argument("reference_approval", type=Path)
    parser.add_argument("expected_reference_approval_sha")
    parser.add_argument("reference_result_approval", type=Path)
    parser.add_argument("expected_reference_result_approval_sha")
    parser.add_argument("reference_campaign", type=Path)
    parser.add_argument("expected_source_20k_fingerprint")
    parser.add_argument("expected_source_full_fingerprint")
    parser.add_argument("output", type=Path)
    return parser.parse_args()


def check_initial_environment() -> None:
    if not sys.flags.isolated:
        fail("launcher requires /usr/bin/python3 -I")
    if any(name in os.environ for name in FORBIDDEN_ENV):
        fail("launcher inherited a forbidden environment variable")
    for name, expected in INITIAL_ENV.items():
        if os.environ.get(name) != expected:
            fail(
                "invoke through the approved env -i launcher contract: "
                f"{name} differs"
            )
    unexpected = set(os.environ) - set(INITIAL_ENV)
    if unexpected:
        fail(
            "invoke through the approved env -i launcher contract: "
            f"unexpected environment {sorted(unexpected)}"
        )


def private_environment(home: Path) -> dict[str, str]:
    return {
        "DX100_PRIVATE_HOME": str(home),
        "HOME": str(home),
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


def reject_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            fail(f"path contains a symlink component: {path}")


def safe_runtime_root() -> None:
    RUNTIME_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = RUNTIME_ROOT.lstat()
    if (
        RUNTIME_ROOT.is_symlink()
        or not RUNTIME_ROOT.is_dir()
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        fail(f"unsafe runtime root: {RUNTIME_ROOT}")


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


def stage_approved_descriptor(
    source_fd: int,
    destination: Path,
    expected: str,
    *,
    executable: bool,
    label: str,
) -> Path:
    if not SHA256_RE.fullmatch(expected):
        fail(f"malformed expected SHA-256 for {label}")
    destination_fd = -1
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            fail(f"approved source is not a regular file: {label}")
        os.lseek(source_fd, 0, os.SEEK_SET)
        destination_fd = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | os.O_CLOEXEC,
            0o500 if executable else 0o400,
        )
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
            fail(f"approved source changed while staging: {label}")
        if digest.hexdigest() != expected:
            fail(f"approved source hash mismatch: {label}")
        os.fchmod(destination_fd, 0o500 if executable else 0o400)
        os.fsync(destination_fd)
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
    if file_sha256(destination) != expected:
        fail(f"staged approved file hash mismatch: {destination}")
    return destination


def stage_approved(
    source: Path,
    destination: Path,
    expected: str,
    *,
    executable: bool,
) -> Path:
    reject_symlink_components(source)
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        return stage_approved_descriptor(
            source_fd,
            destination,
            expected,
            executable=executable,
            label=str(source),
        )
    finally:
        os.close(source_fd)


def stage_running_launcher(destination: Path, expected: str) -> Path:
    match = re.fullmatch(r"/proc/self/fd/([0-9]+)", __file__)
    if match is None:
        fail("launcher must execute from the sealed bootstrap descriptor")
    inherited_fd = int(match.group(1))
    descriptor = os.dup(inherited_fd)
    try:
        required_seals = (
            fcntl.F_SEAL_SEAL
            | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_WRITE
        )
        try:
            observed_seals = fcntl.fcntl(descriptor, fcntl.F_GET_SEALS)
        except OSError as error:
            fail(f"running launcher is not a sealed memory file: {error}")
        if observed_seals != required_seals:
            fail("running launcher does not have all required seals")
        return stage_approved_descriptor(
            descriptor,
            destination,
            expected,
            executable=True,
            label="sealed running launcher",
        )
    finally:
        os.close(descriptor)


def install_termination_handlers() -> TerminationController:
    controller = TerminationController()
    for signal_number in TERMINATION_SIGNALS:
        signal.signal(signal_number, controller)
    return controller


def process_start_time(pid: int) -> int:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return -1
    close = raw.rfind(")")
    if close < 0:
        fail(f"malformed /proc stat for process {pid}")
    fields = raw[close + 2 :].split()
    if len(fields) < 20:
        fail(f"malformed /proc stat for process {pid}")
    return int(fields[19])


def terminate_supervised_child(
    process: subprocess.Popen[str], start_time: int
) -> None:
    if process.poll() is not None:
        process.wait()
        return
    current_start = process_start_time(process.pid)
    if current_start != start_time:
        fail("refusing to signal a changed child process identity")
    try:
        process.send_signal(signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    while process.poll() is None:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            current_start = process_start_time(process.pid)
            if current_start not in {-1, start_time}:
                fail("supervised child identity changed during termination")


def terminate_unbound_child(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        process.wait()
        return
    process.terminate()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        fail("newly spawned child did not terminate")


def restore_signal_mask(mask: set[signal.Signals]) -> None:
    signal.pthread_sigmask(signal.SIG_SETMASK, mask)


def git_output(
    repository: Path, environment: dict[str, str], *arguments: str
) -> str:
    return subprocess.run(
        ["/usr/bin/git", "-C", str(repository), *arguments],
        check=True,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout


def verify_git_state(
    repository: Path, expected_commit: str, environment: dict[str, str]
) -> None:
    if (
        git_output(
            repository, environment, "rev-parse", "--verify", "HEAD^{commit}"
        ).strip()
        != expected_commit
    ):
        fail("workflow/config commit differs")
    status = git_output(
        repository,
        environment,
        "-c",
        "status.showUntrackedFiles=all",
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    if status:
        fail("workflow/config worktree is dirty")
    if git_output(
        repository,
        environment,
        "for-each-ref",
        "--format=%(refname)",
        "refs/replace",
    ).strip():
        fail("Git replacement refs are forbidden")
    raw_common = git_output(
        repository, environment, "rev-parse", "--git-common-dir"
    ).strip()
    common = Path(raw_common)
    if not common.is_absolute():
        common = repository / common
    if (common.resolve(strict=True) / "info/grafts").exists():
        fail("legacy Git grafts are forbidden")


def run_python_fd(
    script: Path,
    expected: str,
    arguments: list[str],
    environment: dict[str, str],
    *,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    descriptor = os.open(script, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if fd_sha256(descriptor) != expected:
            fail(f"authorized Python program changed: {script}")
        command = [
            "/usr/bin/python3",
            "-I",
            f"/proc/self/fd/{descriptor}",
            *arguments,
        ]
        previous_mask = signal.pthread_sigmask(
            signal.SIG_BLOCK, TERMINATION_SIGNALS
        )
        process: subprocess.Popen[str] | None = None
        start_time = -1
        try:
            process = subprocess.Popen(
                command,
                env=environment,
                pass_fds=(descriptor,),
                text=True,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.STDOUT if capture else None,
                preexec_fn=lambda: restore_signal_mask(previous_mask),
            )
            start_time = process_start_time(process.pid)
            if start_time < 0 and process.poll() is None:
                fail("could not bind supervised child identity")
        except BaseException:
            try:
                if process is not None:
                    terminate_unbound_child(process)
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
            raise
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
            if start_time < 0:
                return_code = process.poll()
                stdout, _ = process.communicate()
                return subprocess.CompletedProcess(
                    command, return_code, stdout=stdout
                )
            stdout, _ = process.communicate()
        except BaseException:
            terminate_supervised_child(process, start_time)
            raise
        return subprocess.CompletedProcess(
            command, process.returncode, stdout=stdout
        )
    finally:
        os.close(descriptor)


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


def create_tree_guard(root: Path) -> TreeGuard:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.inotify_init1.argtypes = [ctypes.c_int]
    libc.inotify_init1.restype = ctypes.c_int
    watch_fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
    if watch_fd < 0:
        error = ctypes.get_errno()
        fail(f"inotify_init1 failed: {os.strerror(error)}")
    root_fd = -1
    try:
        parent_watch = add_watch(watch_fd, root.parent)
        root_fd = os.open(
            root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        tree_watches = {add_watch(watch_fd, root)}
        for directory in sorted(
            path for path in root.rglob("*") if path.is_dir()
        ):
            if directory.is_symlink():
                fail(f"campaign contains a symlink: {directory}")
            tree_watches.add(add_watch(watch_fd, directory))
        info = os.fstat(root_fd)
        guard = TreeGuard(
            root=root,
            root_fd=root_fd,
            watch_fd=watch_fd,
            parent_watch=parent_watch,
            tree_watches=tree_watches,
            device=info.st_dev,
            inode=info.st_ino,
        )
        verify_tree_guard(guard)
        return guard
    except BaseException:
        if root_fd >= 0:
            os.close(root_fd)
        os.close(watch_fd)
        raise


def verify_tree_guard(guard: TreeGuard) -> None:
    while True:
        try:
            data = os.read(guard.watch_fd, 64 * 1024)
        except OSError as error:
            if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                break
            fail(f"campaign watch failed: {error}")
        if not data:
            fail("campaign watch closed unexpectedly")
        offset = 0
        while offset < len(data):
            watch, mask, _, name_length = INOTIFY_EVENT.unpack_from(
                data, offset
            )
            offset += INOTIFY_EVENT.size
            name = data[offset : offset + name_length].rstrip(b"\0")
            offset += name_length
            if mask & (IN_Q_OVERFLOW | IN_IGNORED):
                fail("campaign watch lost event coverage")
            if watch in guard.tree_watches or (
                watch == guard.parent_watch
                and name == os.fsencode(guard.root.name)
            ):
                fail("sealed campaign changed during verification")
    descriptor = os.fstat(guard.root_fd)
    current = guard.root.lstat()
    if (
        guard.root.is_symlink()
        or not guard.root.is_dir()
        or descriptor.st_dev != guard.device
        or descriptor.st_ino != guard.inode
        or current.st_dev != guard.device
        or current.st_ino != guard.inode
    ):
        fail("sealed campaign identity changed")


def close_guard(guard: TreeGuard) -> None:
    os.close(guard.root_fd)
    os.close(guard.watch_fd)


def create_empty(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o400,
    )
    try:
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def seal_tree(root: Path) -> None:
    for path in sorted(
        root.rglob("*"), key=lambda item: len(item.parts), reverse=True
    ):
        if path.is_symlink():
            fail(f"campaign contains a symlink: {path}")
        mode = path.stat().st_mode
        if path.is_file():
            path.chmod(0o500 if mode & 0o111 else 0o400)
        elif path.is_dir():
            path.chmod(0o500)
        else:
            fail(f"campaign contains an unsupported node: {path}")
    root.chmod(0o500)


def open_directory_anchor(path: Path) -> DirectoryAnchor:
    reject_symlink_components(path)
    canonical = path.resolve(strict=True)
    descriptor = os.open(
        canonical,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    info = os.fstat(descriptor)
    return DirectoryAnchor(
        path=canonical,
        descriptor=descriptor,
        device=info.st_dev,
        inode=info.st_ino,
    )


def verify_directory_anchor(anchor: DirectoryAnchor) -> None:
    descriptor = os.fstat(anchor.descriptor)
    current = anchor.path.lstat()
    if (
        anchor.path.is_symlink()
        or not anchor.path.is_dir()
        or descriptor.st_dev != anchor.device
        or descriptor.st_ino != anchor.inode
        or current.st_dev != anchor.device
        or current.st_ino != anchor.inode
    ):
        fail(f"publication directory identity changed: {anchor.path}")


def verify_private_directory_anchor(anchor: DirectoryAnchor) -> None:
    verify_directory_anchor(anchor)
    info = os.fstat(anchor.descriptor)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        fail(f"publication directory is not owner-private: {anchor.path}")


def create_private_staging(parent: DirectoryAnchor, output_name: str) -> Path:
    verify_private_directory_anchor(parent)
    if Path(output_name).name != output_name:
        fail("staging output name must be a single path component")
    for _ in range(128):
        name = f".staging.{output_name}.{os.urandom(16).hex()}"
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent.descriptor)
        except FileExistsError:
            continue
        info = os.stat(name, dir_fd=parent.descriptor, follow_symlinks=False)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            fail("new staging directory identity is unsafe")
        os.fsync(parent.descriptor)
        verify_private_directory_anchor(parent)
        return parent.path / name
    fail("could not allocate a private staging directory")


def verify_child_identity(
    parent: DirectoryAnchor, name: str, device: int, inode: int
) -> None:
    try:
        current = os.stat(
            name, dir_fd=parent.descriptor, follow_symlinks=False
        )
    except FileNotFoundError:
        fail(f"publication source disappeared: {name}")
    if (
        not stat.S_ISDIR(current.st_mode)
        or current.st_dev != device
        or current.st_ino != inode
    ):
        fail(f"publication child identity changed: {name}")


def rename_noreplace(
    source_name: str,
    destination_name: str,
    source_parent: DirectoryAnchor,
    destination_parent: DirectoryAnchor,
    *,
    expected_device: int,
    expected_inode: int,
) -> None:
    if (
        Path(source_name).name != source_name
        or Path(destination_name).name != destination_name
    ):
        fail("publication names must be single path components")
    verify_child_identity(
        source_parent, source_name, expected_device, expected_inode
    )
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        fail("renameat2 is required for no-clobber publication")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    rename_noreplace_flag = 1
    if (
        renameat2(
            source_parent.descriptor,
            os.fsencode(source_name),
            destination_parent.descriptor,
            os.fsencode(destination_name),
            rename_noreplace_flag,
        )
        != 0
    ):
        error = ctypes.get_errno()
        fail(f"atomic campaign publication failed: {os.strerror(error)}")
    verify_child_identity(
        destination_parent,
        destination_name,
        expected_device,
        expected_inode,
    )


def verifier_arguments(
    args: argparse.Namespace,
    campaign: Path,
    certificate: Path,
    sim_root: Path,
    source_20k: Path,
    source_full: Path,
    staged: dict[str, Path],
) -> list[str]:
    return [
        str(campaign),
        "--certificate-output",
        str(certificate),
        "--sim-root",
        str(sim_root),
        "--expected-sim-commit",
        args.expected_sim_commit,
        "--expected-launcher-sha",
        args.expected_self_sha,
        "--expected-verifier-sha",
        args.expected_verifier_sha,
        "--expected-runner-sha",
        args.expected_runner_sha,
        "--expected-reference-verifier-sha",
        args.expected_reference_verifier_sha,
        "--expected-reference-approval-sha",
        args.expected_reference_approval_sha,
        "--expected-reference-result-approval-sha",
        args.expected_reference_result_approval_sha,
        "--expected-input-20k-sha",
        args.expected_input_20k_sha,
        "--expected-bfs-approval-sha",
        args.expected_bfs_approval_sha,
        "--expected-bfs-oracle-sha",
        args.expected_bfs_oracle_sha,
        "--expected-source-20k-fingerprint",
        args.expected_source_20k_fingerprint,
        "--expected-source-full-fingerprint",
        args.expected_source_full_fingerprint,
        "--expected-source-20k",
        str(source_20k),
        "--expected-source-full",
        str(source_full),
        "--expected-reference-campaign",
        str(args.reference_campaign.resolve(strict=True)),
        "--expected-bfs-campaign",
        str(args.bfs_campaign.resolve(strict=True)),
    ]


def verify_certificate(
    certificate: Path,
    campaign: Path,
    args: argparse.Namespace,
) -> None:
    if certificate.is_symlink() or not certificate.is_file():
        fail("verification certificate is missing or symlinked")
    record: Any = json.loads(certificate.read_text())
    campaign_stat = campaign.lstat()
    expected = {
        "schema_version": 1,
        "experiment_id": "xrage-retirement-cache-verification-v1",
        "campaign_device": campaign_stat.st_dev,
        "campaign_inode": campaign_stat.st_ino,
        "simulator_commit": args.expected_sim_commit,
        "launcher_sha256": args.expected_self_sha,
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
    if not isinstance(record, dict):
        fail("verification certificate is malformed")
    for key, value in expected.items():
        if record.get(key) != value:
            fail(f"verification certificate differs for {key}")


def main() -> None:
    install_termination_handlers()
    check_initial_environment()
    args = parse_args()
    hashes = (
        args.expected_self_sha,
        args.expected_runner_sha,
        args.expected_verifier_sha,
        args.expected_reference_verifier_sha,
        args.expected_bfs_approval_sha,
        args.expected_bfs_oracle_sha,
        args.expected_input_20k_sha,
        args.expected_reference_approval_sha,
        args.expected_reference_result_approval_sha,
        args.expected_source_20k_fingerprint,
        args.expected_source_full_fingerprint,
    )
    if any(SHA256_RE.fullmatch(value) is None for value in hashes):
        fail("all expected hashes must be full SHA-256 values")
    if COMMIT_RE.fullmatch(args.expected_sim_commit) is None:
        fail("expected simulator commit must be a full Git object ID")

    sim_root = args.sim_root.resolve(strict=True)
    runner = (
        sim_root / "experiments/scripts/run_xrage_retirement_cache_ablation.py"
    )
    verifier = (
        sim_root
        / "experiments/scripts/verify_xrage_retirement_cache_ablation.py"
    )
    reference_verifier = Path(
        "/data1/nier/worktrees/dx100-research-virtual-suite-20260717/"
        "experiments/scripts/verify_virtual_campaign.py"
    )
    primary = Path("/data1/nier/worktrees/DX100-virtual-suite-20260717")
    source_20k = (
        primary / "experiments/campaigns/"
        "2026-07-24_xrage_20k_correctness_coherent_cd140bb"
    )
    source_full = (
        primary / "experiments/campaigns/"
        "2026-07-24_xrage_full_correctness_coherent_cd140bb"
    )
    input_20k = Path(
        "/data1/nier/DX100/experiments/inputs/xrage_gather0_20k.json"
    )
    requested_output = args.output.absolute()
    if requested_output.name in {"", ".", ".."}:
        fail("output must name a child of an existing directory")
    output_parent = open_directory_anchor(requested_output.parent)
    verify_private_directory_anchor(output_parent)
    output = output_parent.path / requested_output.name

    safe_runtime_root()
    execution_root = Path(tempfile.mkdtemp(prefix="launch.", dir=RUNTIME_ROOT))
    execution_root.chmod(0o700)
    home = execution_root / "home"
    home.mkdir(mode=0o700)
    environment = private_environment(home)
    verify_git_state(sim_root, args.expected_sim_commit, environment)

    staged = {
        "launcher": stage_running_launcher(
            execution_root / "launcher.py",
            args.expected_self_sha,
        ),
        "runner": stage_approved(
            runner,
            execution_root / "runner.py",
            args.expected_runner_sha,
            executable=True,
        ),
        "verifier": stage_approved(
            verifier,
            execution_root / "verifier.py",
            args.expected_verifier_sha,
            executable=True,
        ),
        "reference_verifier": stage_approved(
            reference_verifier,
            execution_root / "reference_verifier.py",
            args.expected_reference_verifier_sha,
            executable=True,
        ),
        "bfs_approval": stage_approved(
            args.bfs_approval.resolve(strict=True),
            execution_root / "bfs_approval.json",
            args.expected_bfs_approval_sha,
            executable=False,
        ),
        "bfs_oracle": stage_approved(
            args.bfs_oracle.resolve(strict=True),
            execution_root / "bfs_oracle.json",
            args.expected_bfs_oracle_sha,
            executable=False,
        ),
        "reference_approval": stage_approved(
            args.reference_approval.resolve(strict=True),
            execution_root / "reference_approval.json",
            args.expected_reference_approval_sha,
            executable=False,
        ),
        "reference_result_approval": stage_approved(
            args.reference_result_approval.resolve(strict=True),
            execution_root / "reference_result_approval.json",
            args.expected_reference_result_approval_sha,
            executable=False,
        ),
        "input_20k": stage_approved(
            input_20k,
            execution_root / "xrage_20k.json",
            args.expected_input_20k_sha,
            executable=False,
        ),
    }

    lock_fd = os.open(
        PUBLICATION_LOCK,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
    )
    try:
        lock_info = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(lock_info.st_mode)
            or lock_info.st_uid != os.getuid()
            or stat.S_IMODE(lock_info.st_mode) != 0o600
        ):
            fail("unsafe publication lock")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        verify_private_directory_anchor(output_parent)
        if output.exists() or output.is_symlink():
            if (
                output.is_symlink()
                or not output.is_dir()
                or not (output / "campaign.pass").is_file()
                or (output / "campaign.fail").exists()
            ):
                fail(f"output exists without a clean pass state: {output}")
            certificate = execution_root / "existing-certificate.json"
            guard = create_tree_guard(output)
            try:
                completed = run_python_fd(
                    staged["verifier"],
                    args.expected_verifier_sha,
                    verifier_arguments(
                        args,
                        output,
                        certificate,
                        sim_root,
                        source_20k,
                        source_full,
                        staged,
                    ),
                    environment,
                    capture=True,
                )
                if completed.returncode != 0:
                    fail(
                        "existing campaign verification failed: "
                        + (completed.stdout or "").strip()
                    )
                verify_tree_guard(guard)
                verify_certificate(certificate, output, args)
                verify_private_directory_anchor(output_parent)
            finally:
                close_guard(guard)
            print(f"retirement-cache ablation already verified: {output}")
            return

        staging = create_private_staging(output_parent, output.name)
        reference_inputs = (
            args.reference_campaign.resolve(strict=True) / "inputs"
        )
        runner_arguments = [
            "--sim-root",
            str(sim_root),
            "--expected-sim-commit",
            args.expected_sim_commit,
            "--expected-launcher-sha",
            args.expected_self_sha,
            "--expected-verifier-sha",
            args.expected_verifier_sha,
            "--expected-runner-sha",
            args.expected_runner_sha,
            "--expected-reference-verifier-sha",
            args.expected_reference_verifier_sha,
            "--expected-reference-approval-sha",
            args.expected_reference_approval_sha,
            "--expected-reference-result-approval-sha",
            args.expected_reference_result_approval_sha,
            "--expected-input-20k-sha",
            args.expected_input_20k_sha,
            "--expected-bfs-approval-sha",
            args.expected_bfs_approval_sha,
            "--expected-bfs-oracle-sha",
            args.expected_bfs_oracle_sha,
            "--expected-source-20k-fingerprint",
            args.expected_source_20k_fingerprint,
            "--expected-source-full-fingerprint",
            args.expected_source_full_fingerprint,
            "--launcher",
            str(staged["launcher"]),
            "--ablation-verifier",
            str(staged["verifier"]),
            "--gem5",
            str(reference_inputs / "bin/gem5.opt"),
            "--ramulator-yaml",
            str(reference_inputs / "ramulator.yaml"),
            "--ramulator-lib",
            str(reference_inputs / "lib/libramulator.so"),
            "--virtual-verify-bin",
            str(reference_inputs / "benchmark/xrage_virtual_verify"),
            "--virtual-perf-bin",
            str(reference_inputs / "benchmark/xrage_virtual"),
            "--input-20k",
            str(staged["input_20k"]),
            "--input-full",
            str(reference_inputs / "benchmark/xrage_input.json"),
            "--source-20k",
            str(source_20k),
            "--source-full-correctness",
            str(source_full),
            "--reference-campaign",
            str(args.reference_campaign.resolve(strict=True)),
            "--reference-approval",
            str(staged["reference_approval"]),
            "--reference-result-approval",
            str(staged["reference_result_approval"]),
            "--reference-verifier",
            str(staged["reference_verifier"]),
            "--bfs-campaign",
            str(args.bfs_campaign.resolve(strict=True)),
            "--bfs-approval",
            str(staged["bfs_approval"]),
            "--bfs-oracle",
            str(staged["bfs_oracle"]),
            "--output",
            str(staging),
            "--publication-name",
            output.name,
        ]
        completed = run_python_fd(
            staged["runner"],
            args.expected_runner_sha,
            runner_arguments,
            environment,
        )
        if completed.returncode != 0:
            fail(
                f"ablation runner failed; private evidence remains at {staging}"
            )
        verify_git_state(sim_root, args.expected_sim_commit, environment)
        if (
            not (staging / "execution.complete").is_file()
            or (staging / "campaign.fail").exists()
        ):
            fail("runner returned without a clean execution-complete state")

        create_empty(staging / "campaign.pass")
        seal_tree(staging)
        certificate = execution_root / "certificate.json"
        guard = create_tree_guard(staging)
        try:
            completed = run_python_fd(
                staged["verifier"],
                args.expected_verifier_sha,
                verifier_arguments(
                    args,
                    staging,
                    certificate,
                    sim_root,
                    source_20k,
                    source_full,
                    staged,
                ),
                environment,
                capture=True,
            )
            if completed.returncode != 0:
                fail(
                    "independent verification failed: "
                    + (completed.stdout or "").strip()
                )
            verify_tree_guard(guard)
            verify_certificate(certificate, staging, args)
            verify_git_state(sim_root, args.expected_sim_commit, environment)
            verify_tree_guard(guard)
            verify_private_directory_anchor(output_parent)
            rename_noreplace(
                staging.name,
                output.name,
                output_parent,
                output_parent,
                expected_device=guard.device,
                expected_inode=guard.inode,
            )
            os.fsync(output_parent.descriptor)
            verify_private_directory_anchor(output_parent)
        finally:
            close_guard(guard)
        print(
            "completed, independently verified, and atomically published "
            f"retirement-cache ablation: {output}"
        )
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        os.close(output_parent.descriptor)
        try:
            shutil.rmtree(execution_root)
        except OSError:
            pass


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit(f"retirement-cache launch failed: {error}") from error
