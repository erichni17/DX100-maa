#!/usr/bin/env python3
"""Run the no-gem5 virtualization unit-gate suite fail closed."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

PASS_MARKER = "virtualization_unit_gates.pass"
SCRIPTS = Path("experiments/scripts")
TESTS = Path("experiments/tests")


@dataclass(frozen=True)
class Gate:
    """A host-only command that validates one virtualization contract."""

    name: str
    path: Path
    command_prefix: tuple[str, ...]

    def command(self, root: Path) -> list[str]:
        return [*self.command_prefix, str(root / self.path)]


REQUIRED_GATES = (
    Gate(
        "transparent_spd_controller",
        SCRIPTS / "run_transparent_spd_controller_unit.sh",
        ("bash",),
    ),
    Gate(
        "logical_spd_cache_controller",
        SCRIPTS / "run_logical_spd_cache_controller_unit.sh",
        ("bash",),
    ),
    Gate(
        "logical_spd_cache_abi",
        SCRIPTS / "run_logical_spd_cache_abi_unit.sh",
        ("bash",),
    ),
    Gate(
        "spd_cache_state_model",
        TESTS / "test_spd_cache_state_model.py",
        (sys.executable,),
    ),
)
RESPONSE_GATE = Gate(
    "logical_stream_response",
    SCRIPTS / "run_logical_stream_response_unit.sh",
    ("bash",),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="empty directory for gate evidence",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        help="repository root (defaults to this script's repository)",
    )
    parser.add_argument(
        "--require-response",
        action="store_true",
        help="fail if the logical stream response gate has not been added",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def source_metadata(root: Path) -> tuple[str, list[str]]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        status = subprocess.run(
            ["git", "status", "--short"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "unavailable", []
    source_commit = (
        commit.stdout.strip() if commit.returncode == 0 else "unavailable"
    )
    source_status = (
        status.stdout.splitlines() if status.returncode == 0 else []
    )
    return source_commit, source_status


def log_paths(output: Path, gate: Gate) -> tuple[Path, Path]:
    return (
        output / f"{gate.name}.stdout.log",
        output / f"{gate.name}.stderr.log",
    )


def missing_gate_result(
    root: Path, output: Path, gate: Gate
) -> dict[str, object]:
    stdout_path, stderr_path = log_paths(output, gate)
    message = f"missing required gate command: {gate.path}\n"
    stdout_path.write_text("", encoding="utf-8")
    stderr_path.write_text(message, encoding="utf-8")
    return {
        "command": gate.command(root),
        "elapsed_host_validation_seconds": 0.0,
        "log_paths": {
            "stderr": stderr_path.name,
            "stdout": stdout_path.name,
        },
        "return_code": None,
        "status": "failed",
    }


def run_gate(root: Path, output: Path, gate: Gate) -> dict[str, object]:
    command = gate.command(root)
    stdout_path, stderr_path = log_paths(output, gate)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            check=False,
        )
    except OSError as error:
        elapsed = time.monotonic() - started
        stdout_path.write_bytes(b"")
        stderr_path.write_text(
            f"could not execute gate: {error}\n", encoding="utf-8"
        )
        return {
            "command": command,
            "elapsed_host_validation_seconds": round(elapsed, 6),
            "log_paths": {
                "stderr": stderr_path.name,
                "stdout": stdout_path.name,
            },
            "return_code": None,
            "status": "failed",
        }
    elapsed = time.monotonic() - started
    stdout_path.write_bytes(completed.stdout)
    stderr_path.write_bytes(completed.stderr)
    return {
        "command": command,
        "elapsed_host_validation_seconds": round(elapsed, 6),
        "log_paths": {
            "stderr": stderr_path.name,
            "stdout": stdout_path.name,
        },
        "return_code": completed.returncode,
        "status": "passed" if completed.returncode == 0 else "failed",
    }


def prepare_output(path: Path, parser: argparse.ArgumentParser) -> Path:
    output = path.resolve()
    if output.exists() and not output.is_dir():
        parser.error(f"--out must be a directory: {output}")
    if output.exists() and any(output.iterdir()):
        parser.error(
            f"refusing to overwrite nonempty output directory: {output}"
        )
    output.mkdir(parents=True, exist_ok=True)
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    default_root = Path(__file__).resolve().parents[2]
    root = (args.repo or default_root).resolve()
    if not root.is_dir():
        parser.error(f"--repo must be a directory: {root}")
    output = prepare_output(args.out, parser)

    started = time.monotonic()
    source_commit, source_status = source_metadata(root)
    gates = list(REQUIRED_GATES)
    response_path = root / RESPONSE_GATE.path
    if response_path.is_file() or args.require_response:
        gates.append(RESPONSE_GATE)

    results = []
    for gate in gates:
        path = root / gate.path
        if path.is_file():
            result = run_gate(root, output, gate)
        else:
            result = missing_gate_result(root, output, gate)
        result["name"] = gate.name
        results.append(result)
        print(
            f"{'PASS' if result['status'] == 'passed' else 'FAIL'} {gate.name}"
        )

    passed = all(result["status"] == "passed" for result in results)
    summary = {
        "elapsed_host_validation_seconds": round(
            time.monotonic() - started, 6
        ),
        "gates": results,
        "pass_marker": PASS_MARKER if passed else None,
        "schema_version": 1,
        "source_commit": source_commit,
        "source_status": source_status,
        "status": "passed" if passed else "failed",
    }
    write_json(output / "summary.json", summary)
    if passed:
        (output / PASS_MARKER).touch()
    print(f"{'PASS' if passed else 'FAIL'} virtualization_unit_gates")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
