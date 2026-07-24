#!/usr/bin/env python3
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
import shlex
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
IN_ATTRIB = 0x00000004
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_Q_OVERFLOW = 0x00004000
IN_IGNORED = 0x00008000
LOCK_WATCH_MASK = (
    IN_ATTRIB
    | IN_MOVED_FROM
    | IN_MOVED_TO
    | IN_CREATE
    | IN_DELETE
    | IN_DELETE_SELF
    | IN_MOVE_SELF
)
INOTIFY_EVENT = struct.Struct("iIII")


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


CANDIDATES = (
    Candidate("reference", "1kB", 4, 1, 16, 16, 16),
    Candidate("targets1", "1kB", 4, 1, 16, 1, 16),
    Candidate("compact", "256B", 4, 1, 16, 1, 16),
)
PROMOTION_ORDER = ("compact", "targets1")


def fail(message: str) -> None:
    raise RuntimeError(message)


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


def copy_reflink(
    source: Path, destination: Path, *, recursive: bool = False
) -> None:
    command = ["cp", "-a", "--reflink=auto"]
    if recursive:
        command.append(str(source))
        command.append(str(destination))
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        command.extend((str(source), str(destination)))
    subprocess.run(command, check=True)


def stage_checkpoint(
    source_root: Path, manifest_name: str, destination_root: Path
) -> dict[Path, str]:
    source_dir, source_entries = checkpoint_payload(source_root, manifest_name)
    destination_root.mkdir(parents=True)
    copy_reflink(
        source_dir, destination_root / source_dir.name, recursive=True
    )
    staged_entries = {
        destination_root / path.relative_to(source_root): digest
        for path, digest in source_entries.items()
    }
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


def verify_lock_watch(lock: SimulationLock) -> None:
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
            try:
                descriptor = os.open(
                    SIMULATION_LOCK,
                    os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
                    0o600,
                )
                os.fchmod(descriptor, 0o600)
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                watch_descriptor = create_lock_watch()
                descriptor_stat = os.fstat(descriptor)
                lock = SimulationLock(
                    descriptor=descriptor,
                    watch_descriptor=watch_descriptor,
                    device=descriptor_stat.st_dev,
                    inode=descriptor_stat.st_ino,
                    ctime_ns=descriptor_stat.st_ctime_ns,
                )
                verify_lock_identity(lock)
                return lock
            except BlockingIOError:
                os.close(descriptor)
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
) -> dict[str, Any]:
    output = campaign / "runs" / phase / candidate.name / f"replica_{replica}"
    output.mkdir(parents=True)
    source_dir, source_entries = checkpoint_payload(
        checkpoint_root, "checkpoint_sha256.txt"
    )
    copy_reflink(source_dir, output / source_dir.name, recursive=True)
    restore_manifest = output / "restore_checkpoint_sha256.txt"
    with restore_manifest.open("w", encoding="utf-8") as handle:
        for path, digest in sorted(
            source_entries.items(), key=lambda item: str(item[0])
        ):
            handle.write(f"{digest}  {path.relative_to(checkpoint_root)}\n")
    checkpoint_payload(output, "restore_checkpoint_sha256.txt")
    copied_checkpoint = output / source_dir.name
    for path in (copied_checkpoint, *copied_checkpoint.rglob("*")):
        path.chmod(path.stat().st_mode | stat.S_IWUSR)

    command = base_command(inputs, output, binary, data, candidate)
    write_command(output / "restore.command", command)
    environment = {
        "HOME": "/tmp",
        "LANG": "C",
        "LC_ALL": "C",
        "LD_LIBRARY_PATH": str(inputs / "lib"),
        "OMP_NUM_THREADS": "4",
        "OMP_PROC_BIND": "false",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
    }
    started = time.monotonic()
    with (output / "restore.log").open("wb") as log:
        completed = subprocess.run(
            command,
            cwd=inputs / "simulator",
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    wall_seconds = time.monotonic() - started
    (output / "restore.exit").write_text(f"{completed.returncode}\n")
    checkpoint_payload(output, "restore_checkpoint_sha256.txt")

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
        completed.returncode == 0
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
        "return_code": completed.returncode,
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
) -> dict[str, Any]:
    inputs = campaign / "inputs"
    inputs.mkdir()
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
        "reference_approval": regular_file(
            args.reference_approval.resolve(strict=True)
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
        "reference_approval": inputs / "manifests/reference_approval.json",
        "reference_verifier": inputs / "reference_verifier.py",
        "bfs_approval": inputs / "manifests/bfs_approval.json",
        "bfs_oracle": inputs / "manifests/bfs_oracle.json",
    }
    if set(artifacts) != set(expected_hashes):
        fail("expected artifact hash closure differs")
    for name, source in artifacts.items():
        expect_hash(f"authorized {name}", source, expected_hashes[name])
        copy_reflink(source, destinations[name])
        expect_hash(
            f"staged {name}", destinations[name], expected_hashes[name]
        )

    sim_root = args.sim_root.resolve(strict=True)
    tracked_configs = subprocess.run(
        [
            "git",
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
        stdout=subprocess.PIPE,
    ).stdout.split(b"\0")
    for raw in tracked_configs:
        if not raw:
            continue
        relative = Path(os.fsdecode(raw))
        committed = subprocess.run(
            [
                "git",
                "-C",
                str(sim_root),
                "show",
                f"{args.expected_sim_commit}:{relative}",
            ],
            check=True,
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


def verify_staged_inputs(campaign: Path) -> None:
    inputs = campaign / "inputs"
    manifest = json.loads(
        regular_file(campaign / "staged_input_sha256.json").read_text()
    )
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


def reference_ticks(campaign: Path) -> int:
    fields, rows = read_tsv(campaign / "results.tsv")
    required = {"arm", "replica", "sim_ticks", "valid"}
    if not required.issubset(fields):
        fail("reference campaign result schema is incomplete")
    virtual = [row for row in rows if row["arm"] == "virtual"]
    if len(virtual) != 3 or any(row["valid"] != "1" for row in virtual):
        fail("reference campaign lacks three valid virtual replicas")
    ticks = {row["sim_ticks"] for row in virtual}
    if len(ticks) != 1 or not next(iter(ticks)).isdigit():
        fail("reference virtual replicas are not deterministic")
    value = int(next(iter(ticks)))
    if value <= 0:
        fail("reference simTicks is not positive")
    return value


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
    parser.add_argument("--reference-verifier", type=Path, required=True)
    parser.add_argument("--bfs-campaign", type=Path, required=True)
    parser.add_argument("--bfs-approval", type=Path, required=True)
    parser.add_argument("--bfs-oracle", type=Path, required=True)
    parser.add_argument("--expected-runner-sha", required=True)
    parser.add_argument("--expected-reference-verifier-sha", required=True)
    parser.add_argument("--expected-reference-approval-sha", required=True)
    parser.add_argument("--expected-input-20k-sha", required=True)
    parser.add_argument("--expected-bfs-approval-sha", required=True)
    parser.add_argument("--expected-bfs-oracle-sha", required=True)
    parser.add_argument("--expected-source-20k-fingerprint", required=True)
    parser.add_argument("--expected-source-full-fingerprint", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--minimum-available-kib", type=int, default=24 * 1024 * 1024
    )
    parser.add_argument("--capacity-poll-seconds", type=int, default=600)
    parser.add_argument("--screen-overhead-limit", type=float, default=0.01)
    parser.add_argument("--full-overhead-limit", type=float, default=0.01)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_sim_commit):
        fail("--expected-sim-commit must be a full Git object ID")
    expected_sha_values = (
        args.expected_runner_sha,
        args.expected_reference_verifier_sha,
        args.expected_reference_approval_sha,
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
    current_commit = subprocess.run(
        ["git", "-C", str(sim_root), "rev-parse", "HEAD"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    if current_commit != args.expected_sim_commit:
        fail(
            "cost worktree is not at the authorized commit: "
            f"expected={args.expected_sim_commit} actual={current_commit}"
        )
    status = subprocess.run(
        ["git", "-C", str(sim_root), "status", "--porcelain=v1"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout
    if status:
        fail("cost worktree is dirty")
    expect_hash(
        "authorized runner",
        Path(__file__).resolve(strict=True),
        args.expected_runner_sha,
    )

    output = args.output.resolve()
    if output.exists():
        fail(f"output already exists: {output}")
    output.mkdir(parents=True)
    try:
        source_20k = args.source_20k.resolve(strict=True)
        source_full = args.source_full_correctness.resolve(strict=True)
        reference = args.reference_campaign.resolve(strict=True)
        bfs_campaign = args.bfs_campaign.resolve(strict=True)
        reference_verifier = args.reference_verifier.resolve(strict=True)
        reference_approval = args.reference_approval.resolve(strict=True)
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
        reference_verification = subprocess.run(
            [
                str(reference_verifier),
                "xrage",
                str(reference),
                str(reference_approval),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        (output / "reference_verification.log").write_text(
            reference_verification.stdout, encoding="utf-8"
        )
        if reference_verification.returncode != 0:
            fail("external reference-campaign verification failed")
        expect_hash(
            "reference approval after verification",
            reference_approval,
            args.expected_reference_approval_sha,
        )
        bfs_verification = subprocess.run(
            [
                str(reference_verifier),
                "bfs",
                str(bfs_campaign),
                str(bfs_approval),
                "--oracle",
                str(bfs_oracle),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
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
        upstream_ref_ticks = reference_ticks(reference)
        approval = load_json(reference_approval)
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
            "reference_approval": args.expected_reference_approval_sha,
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
            "execution": "serial",
            "wall_clock_timeout": "none",
            "environment_policy": "fixed-minimal-v1",
            "simulation_lock": str(SIMULATION_LOCK),
            "minimum_available_kib": args.minimum_available_kib,
            "screen_overhead_limit": args.screen_overhead_limit,
            "full_overhead_limit": args.full_overhead_limit,
            "upstream_reference_sim_ticks": upstream_ref_ticks,
            "reference_campaign": str(reference),
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
        staged = stage_inputs(args, output, expected_hashes)
        source["source_artifacts"] = staged
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
        verify_staged_inputs(output)

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
                verify_staged_inputs(output)
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
            ref_ticks: int | None = None
            candidate_correctness: dict[str, dict[str, Any]] = {}
            if eligible:
                first_eligible = next(
                    name for name in PROMOTION_ORDER if name in eligible
                )
                first_candidate = next(
                    item for item in CANDIDATES if item.name == first_eligible
                )
                verify_lock_identity(simulation_lock)
                verify_staged_inputs(output)
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
                )
                records.append(first_correctness)
                candidate_correctness[first_eligible] = first_correctness
                if first_correctness["valid"] != 1:
                    fail(f"full correctness failed for {first_candidate.name}")

                reference_candidate = CANDIDATES[0]
                verify_lock_identity(simulation_lock)
                verify_staged_inputs(output)
                reference_correctness = run_case(
                    output,
                    inputs,
                    full_correctness_checkpoint,
                    verify_bin,
                    input_full,
                    reference_candidate,
                    "full_correctness",
                    1,
                    marker_full,
                )
                records.append(reference_correctness)
                if reference_correctness["valid"] != 1:
                    fail("fresh full reference correctness failed")
                reference_performance: list[dict[str, Any]] = []
                for replica in range(1, 4):
                    verify_lock_identity(simulation_lock)
                    verify_staged_inputs(output)
                    result = run_case(
                        output,
                        inputs,
                        full_performance_checkpoint,
                        perf_bin,
                        input_full,
                        reference_candidate,
                        "full_performance",
                        replica,
                        None,
                    )
                    records.append(result)
                    reference_performance.append(result)
                if any(
                    result["valid"] != 1 for result in reference_performance
                ):
                    fail("fresh full reference performance failed")
                fresh_reference_ticks = {
                    result["sim_ticks"] for result in reference_performance
                }
                if len(fresh_reference_ticks) != 1:
                    fail("fresh full reference replicas are not deterministic")
                ref_ticks = int(next(iter(fresh_reference_ticks)))

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
                    verify_staged_inputs(output)
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
                    )
                    records.append(correctness)
                    candidate_correctness[name] = correctness
                row["full_correct"] = correctness["valid"]
                if correctness["valid"] != 1:
                    fail(f"full correctness failed for {candidate.name}")
                performance: list[dict[str, Any]] = []
                for replica in range(1, 4):
                    verify_lock_identity(simulation_lock)
                    verify_staged_inputs(output)
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
                    fail("eligible candidate has no fresh reference")
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
                    fail("promoted candidate has no fresh reference")
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
            verify_staged_inputs(output)
            verify_lock_identity(simulation_lock)
            evidence_manifest(output)
            atomic_write(output / "execution.complete", "", 0o444)
        finally:
            os.close(simulation_lock.watch_descriptor)
            fcntl.flock(simulation_lock.descriptor, fcntl.LOCK_UN)
            os.close(simulation_lock.descriptor)
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
