#!/usr/bin/env python3
"""No-clobber service wrapper for one frozen UMT ingress micro arm.

The campaign harness records the only accepted argv for this program. This
wrapper owns creation of the arm directory, reserves every evidence/output
name before child admission, launches without a shell, and returns a failing
status if the child or any reserved identity fails.
"""

import argparse
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import sys

PYTHON = "/usr/bin/python3"
LAUNCH_SCHEMA = "lanl-maa-umt-ingress-arm-launch-v7"
OWNERSHIP_SCHEMA = "lanl-maa-umt-ingress-output-ownership-v7"
TERMINAL_SCHEMA = "lanl-maa-umt-ingress-arm-terminal-v7"
EVIDENCE_DIRECTORY = ".service-owned"
RECEIPT_NAMES = (
    "arm-launch.json",
    "arm-output-ownership.json",
    "arm-terminal.json",
)
CASES = ("d32-g16", "d32-g31", "d32-g32", "d64-g32")
LABEL_PREFIX = "lanl_maa_umt_ingress_micro"
CSV_HEADER = (
    b"# mpi ranks, Mem for PSI (kb), process rss mem (kb), "
    b"# solver unknowns (extents of PSI), total # flux iterations, "
    b"# time steps, walltime(seconds),energy check, "
    b"energy in radiation field, maximum electron temperature, "
    b"maximum radiation temperature, incident power, escaping power, "
    b"power absorbed, power emitted\n"
)


def sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_sha256(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def descriptor_identity(descriptor, path):
    value = os.fstat(descriptor)
    if not stat.S_ISREG(value.st_mode):
        raise RuntimeError(f"reserved path is not regular: {path}")
    return {
        "path": str(pathlib.Path(path).resolve()),
        "device": value.st_dev,
        "inode": value.st_ino,
    }


def path_identity(path):
    path = pathlib.Path(path)
    value = os.lstat(path)
    if not stat.S_ISREG(value.st_mode):
        raise RuntimeError(f"reserved path is not regular: {path}")
    return {
        "path": str(path.resolve()),
        "device": value.st_dev,
        "inode": value.st_ino,
    }


def reserve_file(path, initial=b"", mode=0o600):
    path = pathlib.Path(path)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, mode)
    try:
        offset = 0
        while offset < len(initial):
            offset += os.write(descriptor, initial[offset:])
        os.fsync(descriptor)
        return descriptor, descriptor_identity(descriptor, path)
    except BaseException:
        os.close(descriptor)
        raise


def write_reserved_json(descriptor, value):
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    if os.lseek(descriptor, 0, os.SEEK_END) != 0:
        raise RuntimeError("reserved receipt is not empty")
    offset = 0
    while offset < len(data):
        offset += os.write(descriptor, data[offset:])
    os.fsync(descriptor)


def identities_match(expected):
    try:
        return all(
            path_identity(value["path"])
            == {key: value[key] for key in ("path", "device", "inode")}
            for value in expected
        )
    except (FileNotFoundError, RuntimeError):
        return False


def proc_start_ticks():
    # Field 2 is parenthesized and may contain spaces, so split after its final
    # close parenthesis. Field 22 is then zero-based index 19 in the suffix.
    text = pathlib.Path("/proc/self/stat").read_text(encoding="ascii")
    suffix = text[text.rfind(")") + 2 :].split()
    value = suffix[19]
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise RuntimeError("wrapper /proc start ticks are invalid")
    return value


def service_argv(wrapper, arm_root, command_digest, gem5_argv):
    return [
        PYTHON,
        str(pathlib.Path(wrapper).resolve()),
        "--arm-root",
        str(pathlib.Path(arm_root).resolve()),
        "--gem5-argv-sha256",
        command_digest,
        "--",
        *gem5_argv,
    ]


def output_initial_bytes(relative, csv_relative):
    return CSV_HEADER if relative == csv_relative else b""


def run_arm(arm_root, expected_command_digest, gem5_argv):
    wrapper = pathlib.Path(__file__).resolve()
    root = pathlib.Path(arm_root).resolve()
    if (
        pathlib.Path(sys.executable).resolve()
        != pathlib.Path(PYTHON).resolve()
        or not re.fullmatch(r"[0-9a-f]{64}", expected_command_digest)
        or not gem5_argv
        or json_sha256(gem5_argv) != expected_command_digest
        or root.name not in CASES
        or "--dot-config=" not in gem5_argv
    ):
        raise RuntimeError("arm wrapper interpreter/command identity mismatch")

    # The service, not a preparatory launcher, owns this exact directory.
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(exist_ok=False)
    evidence_root, m5out = root / EVIDENCE_DIRECTORY, root / "m5out"
    evidence_root.mkdir(exist_ok=False)
    m5out.mkdir(exist_ok=False)
    csv_relative = f"{LABEL_PREFIX}_{root.name}.csv"
    output_relatives = (
        "gem5.stdout",
        "gem5.stderr",
        "app.stdout",
        "app.stderr",
        "debug.log",
        "submission.json",
        csv_relative,
        "m5out/stats.txt",
        "m5out/config.ini",
        "m5out/config.json",
    )

    output_fds, output_ownership = {}, {}
    receipt_fds, receipt_ownership = {}, {}
    try:
        for relative in output_relatives:
            descriptor, identity = reserve_file(
                root / relative,
                output_initial_bytes(relative, csv_relative),
            )
            output_ownership[relative] = {
                **identity,
                "initial_sha256": sha256(root / relative),
            }
            # Retain every reservation descriptor until terminal publication.
            # An unlinked file therefore cannot release/reuse its inode while
            # a racing replacement is being checked.
            output_fds[relative] = descriptor

        for name in RECEIPT_NAMES:
            descriptor, identity = reserve_file(
                evidence_root / name, mode=0o400
            )
            receipt_fds[name], receipt_ownership[name] = descriptor, identity

        argv = service_argv(wrapper, root, expected_command_digest, gem5_argv)
        ownership = {
            "schema": OWNERSHIP_SCHEMA,
            "status": "reserved_before_child",
            "arm_root": str(root),
            "evidence_root": str(evidence_root),
            "wrapper": str(wrapper),
            "wrapper_sha256": sha256(wrapper),
            "wrapper_pid": os.getpid(),
            "wrapper_proc_start_ticks": proc_start_ticks(),
            "wrapper_argv_sha256": json_sha256(argv),
            "gem5_argv_sha256": expected_command_digest,
            "outputs": output_ownership,
            "receipts": receipt_ownership,
        }
        write_reserved_json(
            receipt_fds["arm-output-ownership.json"], ownership
        )
        os.close(receipt_fds.pop("arm-output-ownership.json"))
        ownership_path = evidence_root / "arm-output-ownership.json"
        launch = {
            "schema": LAUNCH_SCHEMA,
            "status": "child_launch_authorized",
            "arm_root": str(root),
            "evidence_root": str(evidence_root),
            "wrapper": str(wrapper),
            "wrapper_sha256": ownership["wrapper_sha256"],
            "wrapper_pid": ownership["wrapper_pid"],
            "wrapper_proc_start_ticks": ownership["wrapper_proc_start_ticks"],
            "wrapper_argv": argv,
            "wrapper_argv_sha256": ownership["wrapper_argv_sha256"],
            "gem5_argv": gem5_argv,
            "gem5_argv_sha256": expected_command_digest,
            "output_ownership": {
                "path": str(ownership_path),
                "sha256": sha256(ownership_path),
            },
        }
        write_reserved_json(receipt_fds["arm-launch.json"], launch)
        os.close(receipt_fds.pop("arm-launch.json"))
        launch_path = evidence_root / "arm-launch.json"
        terminal_path = evidence_root / "arm-terminal.json"

        # Protect receipt names from deletion/replacement while the child runs.
        os.chmod(evidence_root, 0o500)
        preflight = list(output_ownership.values()) + list(
            receipt_ownership.values()
        )
        if not identities_match(preflight):
            gem5_returncode, status = None, "preflight_identity_failed"
            wrapper_returncode = 125
        else:
            stdout = os.fdopen(
                output_fds.pop("gem5.stdout"), "wb", closefd=True
            )
            stderr = os.fdopen(
                output_fds.pop("gem5.stderr"), "wb", closefd=True
            )
            try:
                try:
                    completed = subprocess.run(
                        gem5_argv,
                        stdin=subprocess.DEVNULL,
                        stdout=stdout,
                        stderr=stderr,
                        check=False,
                    )
                    gem5_returncode, status = completed.returncode, "exited"
                    wrapper_returncode = gem5_returncode
                except FileNotFoundError:
                    gem5_returncode, status = 127, "launch_failed"
                    wrapper_returncode = 127
                except OSError:
                    gem5_returncode, status = 126, "launch_failed"
                    wrapper_returncode = 126
                stdout.flush()
                stderr.flush()
                os.fsync(stdout.fileno())
                os.fsync(stderr.fileno())
            finally:
                stdout.close()
                stderr.close()

        output_identity_ok = identities_match(output_ownership.values())
        if not output_identity_ok:
            status, wrapper_returncode = "output_identity_failed", 125
        outputs = {}
        for relative, reservation in output_ownership.items():
            try:
                observed = path_identity(reservation["path"])
                outputs[relative] = {
                    **observed,
                    "sha256": sha256(reservation["path"]),
                    "reservation_identity_match": observed
                    == {
                        key: reservation[key]
                        for key in ("path", "device", "inode")
                    },
                }
            except (FileNotFoundError, RuntimeError):
                outputs[relative] = {
                    "path": reservation["path"],
                    "device": None,
                    "inode": None,
                    "sha256": None,
                    "reservation_identity_match": False,
                }
        terminal = {
            "schema": TERMINAL_SCHEMA,
            "status": status,
            "arm_root": str(root),
            "evidence_root": str(evidence_root),
            "wrapper": str(wrapper),
            "wrapper_sha256": launch["wrapper_sha256"],
            "wrapper_pid": launch["wrapper_pid"],
            "wrapper_proc_start_ticks": launch["wrapper_proc_start_ticks"],
            "wrapper_argv_sha256": launch["wrapper_argv_sha256"],
            "gem5_argv_sha256": expected_command_digest,
            "launch_evidence": {
                "path": str(launch_path),
                "sha256": sha256(launch_path),
            },
            "output_ownership": launch["output_ownership"],
            "gem5_returncode": gem5_returncode,
            "wrapper_returncode": wrapper_returncode,
            "outputs": outputs,
        }
        write_reserved_json(receipt_fds["arm-terminal.json"], terminal)
        os.close(receipt_fds.pop("arm-terminal.json"))
        if (
            path_identity(terminal_path)
            != receipt_ownership["arm-terminal.json"]
        ):
            return 125
        return wrapper_returncode
    finally:
        for descriptor in output_fds.values():
            os.close(descriptor)
        for descriptor in receipt_fds.values():
            os.close(descriptor)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm-root", required=True)
    parser.add_argument("--gem5-argv-sha256", required=True)
    parser.add_argument("gem5_argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.gem5_argv
    if not command or command[0] != "--" or len(command) == 1:
        raise RuntimeError("arm wrapper requires an exact argv after --")
    return run_arm(args.arm_root, args.gem5_argv_sha256, command[1:])


if __name__ == "__main__":
    raise SystemExit(main())
