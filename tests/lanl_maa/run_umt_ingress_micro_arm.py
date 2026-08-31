#!/usr/bin/env python3
"""No-clobber service wrapper for one frozen UMT ingress micro arm.

The campaign harness records the only accepted argv for this program.  This
wrapper owns creation of the arm directory and gem5 streams, launches without
a shell, writes cross-bound launch/terminal receipts, and returns gem5's exit
status.  Existing evidence is never reused or overwritten.
"""

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

PYTHON = "/usr/bin/python3"
LAUNCH_SCHEMA = "lanl-maa-umt-ingress-arm-launch-v7"
TERMINAL_SCHEMA = "lanl-maa-umt-ingress-arm-terminal-v7"


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


def exclusive_json(path, value):
    """Create a JSON receipt exactly once, without a replace race."""
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    descriptor = os.open(
        pathlib.Path(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        # fdopen owns the descriptor after construction.
        raise


def exclusive_stream(path):
    descriptor = os.open(
        pathlib.Path(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644
    )
    return os.fdopen(descriptor, "wb", closefd=True)


def proc_start_ticks():
    # Field 2 is parenthesized and may contain spaces, so split after its final
    # close parenthesis.  Field 22 is then zero-based index 19 in the suffix.
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


def run_arm(arm_root, expected_command_digest, gem5_argv):
    wrapper = pathlib.Path(__file__).resolve()
    root = pathlib.Path(arm_root).resolve()
    if (
        pathlib.Path(sys.executable).resolve()
        != pathlib.Path(PYTHON).resolve()
        or not re.fullmatch(r"[0-9a-f]{64}", expected_command_digest)
        or not gem5_argv
        or json_sha256(gem5_argv) != expected_command_digest
    ):
        raise RuntimeError("arm wrapper interpreter/command identity mismatch")

    # The service, not a preparatory launcher, owns this exact directory.
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(exist_ok=False)
    stdout_path, stderr_path = root / "gem5.stdout", root / "gem5.stderr"
    launch_path, terminal_path = (
        root / "arm-launch.json",
        root / "arm-terminal.json",
    )
    argv = service_argv(wrapper, root, expected_command_digest, gem5_argv)
    launch = {
        "schema": LAUNCH_SCHEMA,
        "status": "child_launch_authorized",
        "arm_root": str(root),
        "wrapper": str(wrapper),
        "wrapper_sha256": sha256(wrapper),
        "wrapper_pid": os.getpid(),
        "wrapper_proc_start_ticks": proc_start_ticks(),
        "wrapper_argv": argv,
        "wrapper_argv_sha256": json_sha256(argv),
        "gem5_argv": gem5_argv,
        "gem5_argv_sha256": expected_command_digest,
        "gem5_stdout": str(stdout_path),
        "gem5_stderr": str(stderr_path),
    }

    # Open both streams with O_EXCL before admitting the child.  If an attacker
    # wins either name, the arm fails without executing gem5.
    with exclusive_stream(stdout_path) as stdout, exclusive_stream(
        stderr_path
    ) as stderr:
        exclusive_json(launch_path, launch)
        try:
            completed = subprocess.run(
                gem5_argv,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                check=False,
            )
            returncode, status = completed.returncode, "exited"
        except FileNotFoundError:
            returncode, status = 127, "launch_failed"
        except OSError:
            returncode, status = 126, "launch_failed"
        stdout.flush()
        stderr.flush()
        os.fsync(stdout.fileno())
        os.fsync(stderr.fileno())

    terminal = {
        "schema": TERMINAL_SCHEMA,
        "status": status,
        "arm_root": str(root),
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
        "gem5_returncode": returncode,
        "gem5_stdout": {
            "path": str(stdout_path),
            "sha256": sha256(stdout_path),
        },
        "gem5_stderr": {
            "path": str(stderr_path),
            "sha256": sha256(stderr_path),
        },
    }
    exclusive_json(terminal_path, terminal)
    return returncode


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
