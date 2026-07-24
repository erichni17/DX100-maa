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
INITIAL_ENV = {
    "DX100_SANITIZED_LAUNCH": "1",
    "HOME": "/data1/nier/.dx-runtime-state",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
}
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


def fail(message: str) -> None:
    raise RuntimeError(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("expected_self_sha")
    parser.add_argument("expected_runner_sha")
    parser.add_argument("expected_verifier_sha")
    parser.add_argument("expected_reference_verifier_sha")
    parser.add_argument("expected_sim_commit")
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


def stage_approved(
    source: Path,
    destination: Path,
    expected: str,
    *,
    executable: bool,
) -> Path:
    if not SHA256_RE.fullmatch(expected):
        fail(f"malformed expected SHA-256 for {source}")
    reject_symlink_components(source)
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    destination_fd = -1
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            fail(f"approved source is not a regular file: {source}")
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
            fail(f"approved source changed while staging: {source}")
        if digest.hexdigest() != expected:
            fail(f"approved source hash mismatch: {source}")
        os.fchmod(destination_fd, 0o500 if executable else 0o400)
        os.fsync(destination_fd)
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
        os.close(source_fd)
    if file_sha256(destination) != expected:
        fail(f"staged approved file hash mismatch: {destination}")
    return destination


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
        return subprocess.run(
            [
                "/usr/bin/python3",
                "-I",
                f"/proc/self/fd/{descriptor}",
                *arguments,
            ],
            check=False,
            env=environment,
            pass_fds=(descriptor,),
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
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


def rename_noreplace(source: Path, destination: Path) -> None:
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
    at_fdcwd = -100
    rename_noreplace_flag = 1
    if (
        renameat2(
            at_fdcwd,
            os.fsencode(source),
            at_fdcwd,
            os.fsencode(destination),
            rename_noreplace_flag,
        )
        != 0
    ):
        error = ctypes.get_errno()
        fail(f"atomic campaign publication failed: {os.strerror(error)}")


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

    launcher = Path(__file__).resolve(strict=True)
    sim_root = launcher.parents[2]
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
    output = args.output.absolute()
    reject_symlink_components(output.parent)
    output.parent.resolve(strict=True)

    safe_runtime_root()
    execution_root = Path(tempfile.mkdtemp(prefix="launch.", dir=RUNTIME_ROOT))
    execution_root.chmod(0o700)
    home = execution_root / "home"
    home.mkdir(mode=0o700)
    environment = private_environment(home)
    verify_git_state(sim_root, args.expected_sim_commit, environment)

    staged = {
        "launcher": stage_approved(
            launcher,
            execution_root / "launcher.py",
            args.expected_self_sha,
            executable=True,
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
            finally:
                close_guard(guard)
            print(f"retirement-cache ablation already verified: {output}")
            return

        staging = Path(
            tempfile.mkdtemp(
                prefix=f"campaign.{output.name}.", dir=RUNTIME_ROOT
            )
        )
        staging.chmod(0o700)
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
            rename_noreplace(staging, output)
            parent_fd = os.open(
                output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
            )
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        finally:
            close_guard(guard)
        print(
            "completed, independently verified, and atomically published "
            f"retirement-cache ablation: {output}"
        )
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        try:
            shutil.rmtree(execution_root)
        except OSError:
            pass


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit(f"retirement-cache launch failed: {error}") from error
