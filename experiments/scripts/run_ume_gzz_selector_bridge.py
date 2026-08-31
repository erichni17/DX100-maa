#!/usr/bin/env python3
"""Recover only the two GZZ hybrid arms with mounted selectors."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.scripts import run_ume_two_pass_matrix as base  # noqa: E402

AUTHORITY = Path("/tmp/ume-gzz-two-pass-20260831-39080929")
HYBRID_ARMS = tuple(arm for arm in base.ARMS if arm.selector is not None)


class BridgeError(RuntimeError):
    """Fail-closed GZZ selector-bridge error."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise BridgeError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_authority() -> dict[str, Any]:
    require(AUTHORITY.is_dir(), "missing rejected GZZ authority")
    manifest = json.loads((AUTHORITY / "manifest.json").read_text())
    require(
        manifest["gem5_sha256"] == base.EXPECTED_GEM5_SHA256
        and sha256(AUTHORITY / "inputs/gem5.opt") == base.EXPECTED_GEM5_SHA256,
        "authority simulator identity",
    )
    native = {}
    for arm in base.ARMS[:2]:
        native[arm.name] = base.classify_arm(AUTHORITY, arm, manifest)
    require(
        native["native16"]["output_hash"]
        == native["native4"]["output_hash"]
        == base.EXPECTED_OUTPUT_HASH,
        "native authority output mismatch",
    )
    failure = json.loads((AUTHORITY / "failure.json").read_text())
    require(
        failure.get("decision") == "REJECT"
        and "missing terminal m5_exit" in failure.get("reason", ""),
        "authority failed for an unexpected reason",
    )
    for arm in HYBRID_ARMS:
        log = (AUTHORITY / "arms" / arm.name / "restore.log").read_text(
            errors="replace"
        )
        require(
            "GZZ virtual consumer selector:" in log
            and "because m5_exit instruction encountered" not in log,
            f"{arm.name}: authority failure signature changed",
        )
    return {"manifest": manifest, "native": native}


def target_from(command: list[str]) -> Path:
    options = command[command.index("--options") + 1].split()
    require(len(options) == 2 and options[0] == str(base.ELEMENTS), "options")
    return Path(options[1])


def derive_command(arm: base.Arm, out: Path) -> tuple[list[str], Path]:
    command = json.loads(
        (AUTHORITY / "arms" / arm.name / "restore.command.json").read_text()
    )
    outdirs = [
        i for i, value in enumerate(command) if value.startswith("--outdir=")
    ]
    require(len(outdirs) == 1, f"{arm.name}: outdir count")
    command[outdirs[0]] = f"--outdir={out / 'arms' / arm.name / 'run'}"
    target = target_from(command)
    require(
        target == AUTHORITY / "inputs" / f"{arm.name}.selector",
        f"{arm.name}: selector target changed",
    )
    return command, target


def wrapped_command(
    command: list[str], selector: Path, target: Path
) -> list[str]:
    return [
        "/usr/bin/unshare",
        "-Urnm",
        "/bin/bash",
        "-c",
        'mount --bind "$1" "$2"; shift 2; exec "$@"',
        "gzz-selector",
        str(selector),
        str(target),
        *command,
    ]


def proc_start_ticks(pid: int) -> int | None:
    try:
        line = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return None
    close = line.rfind(")")
    fields = line[close + 2 :].split() if close >= 0 else []
    return int(fields[19]) if len(fields) > 19 else None


def artifact_paths(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.name != "artifacts.sha256"
    )


def write_ledger(root: Path) -> None:
    lines = [
        f"{sha256(path)}  {path.relative_to(root)}"
        for path in artifact_paths(root)
    ]
    (root / "artifacts.sha256").write_text("\n".join(lines) + "\n")


def verify_ledger(root: Path) -> None:
    seen = set()
    for number, line in enumerate(
        (root / "artifacts.sha256").read_text().splitlines(), 1
    ):
        digest, relative = line.split("  ", 1)
        require(relative not in seen, f"duplicate ledger line {number}")
        seen.add(relative)
        path = root / relative
        require(
            path.is_file() and sha256(path) == digest,
            f"artifact changed: {relative}",
        )


def prepare_classification_links(root: Path) -> None:
    for arm in HYBRID_ARMS:
        destination = root / "checkpoints" / arm.name
        destination.mkdir(parents=True)
        (destination / "gem5").symlink_to(
            AUTHORITY / "checkpoints" / arm.name / "gem5",
            target_is_directory=True,
        )
        (destination / "identity.json").write_text(
            (
                AUTHORITY / "checkpoints" / arm.name / "identity.json"
            ).read_text()
        )


def classify(root: Path) -> dict[str, Any]:
    authority = verify_authority()
    manifest = authority["manifest"]
    hybrids = {
        arm.name: base.classify_arm(root, arm, manifest) for arm in HYBRID_ARMS
    }
    require(
        len(
            {
                authority["native"]["native16"]["output_hash"],
                authority["native"]["native4"]["output_hash"],
                *(item["output_hash"] for item in hybrids.values()),
            }
        )
        == 1,
        "cross-arm output mismatch",
    )
    original = hybrids["original_hybrid"]["counters"]
    strict = hybrids["strict_bounded_hybrid"]["counters"]
    for field in ("numInst_INDRD", "numInst_INDRMW", "index_words"):
        require(
            original[field] == strict[field], f"hybrid work differs: {field}"
        )
    ticks = {
        **{
            name: item["counters"]["simTicks"]
            for name, item in authority["native"].items()
        },
        **{
            name: item["counters"]["simTicks"]
            for name, item in hybrids.items()
        },
    }
    return {
        "schema": "dx100.ume_gzz_selector_bridge.v1",
        "terminal": True,
        "decision": "ACCEPT_GZZ_SELECTOR_BRIDGE",
        "authority": str(AUTHORITY),
        "native_controls_reused": True,
        "hybrids": hybrids,
        "ticks": ticks,
        "comparisons": {
            "original_over_strict": (
                ticks["original_hybrid"] / ticks["strict_bounded_hybrid"]
            ),
            "native16_over_original": (
                ticks["native16"] / ticks["original_hybrid"]
            ),
            "native16_over_strict": (
                ticks["native16"] / ticks["strict_bounded_hybrid"]
            ),
        },
    }


def run(root: Path) -> dict[str, Any]:
    require(not root.exists(), f"output exists: {root}")
    require(
        not subprocess.check_output(
            ["git", "status", "--short"], cwd=ROOT, text=True
        ).strip(),
        "refusing launch from dirty worktree",
    )
    authority = verify_authority()
    root.mkdir(parents=True)
    (root / "authority.json").write_text(
        json.dumps(
            {
                "root": str(AUTHORITY),
                "manifest_sha256": sha256(AUTHORITY / "manifest.json"),
                "gem5_sha256": authority["manifest"]["gem5_sha256"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    prepare_classification_links(root)
    environment = dict(
        os.environ,
        LD_LIBRARY_PATH=str(AUTHORITY / "inputs"),
        OMP_NUM_THREADS="4",
        OMP_PROC_BIND="false",
    )
    processes: dict[
        str, tuple[subprocess.Popen[bytes], Any, dict[str, Any]]
    ] = {}
    for arm in HYBRID_ARMS:
        arm_root = root / "arms" / arm.name
        arm_root.mkdir(parents=True)
        selector = arm_root / "selector"
        selector.write_text(arm.selector + "\n")
        selector.chmod(0o444)
        command, target = derive_command(arm, root)
        (arm_root / "restore.command.json").write_text(
            json.dumps(command, indent=2) + "\n"
        )
        output = (arm_root / "restore.log").open("wb")
        process = subprocess.Popen(
            wrapped_command(command, selector, target),
            stdout=output,
            stderr=subprocess.STDOUT,
            env=environment,
        )
        start = proc_start_ticks(process.pid)
        require(start is not None, f"{arm.name}: missing process identity")
        processes[arm.name] = (
            process,
            output,
            {
                "pid": process.pid,
                "start_ticks": start,
                "started_ns": time.time_ns(),
            },
        )
    for arm in HYBRID_ARMS:
        process, output, record = processes[arm.name]
        returncode = process.wait()
        output.close()
        record.update(
            {
                "returncode": returncode,
                "ended_ns": time.time_ns(),
                "pid_absent": proc_start_ticks(process.pid) is None,
            }
        )
        arm_root = root / "arms" / arm.name
        (arm_root / "restore.exit").write_text(f"{returncode}\n")
        (arm_root / "restore.process.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n"
        )
        require(returncode == 0, f"{arm.name}: restore failed")
    result = classify(root)
    (root / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    (root / "gate.complete").write_text(
        "COMPLETE_UME_GZZ_SELECTOR_BRIDGE\ncorrectness=EXACT_REFERENCE\n"
    )
    write_ledger(root)
    verify_ledger(root)
    return result


def validate(root: Path) -> dict[str, Any]:
    verify_ledger(root)
    sealed = json.loads((root / "result.json").read_text())
    recomputed = classify(root)
    require(recomputed == sealed, "sealed GZZ result changed")
    verify_ledger(root)
    return sealed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "validate", "preflight"))
    parser.add_argument("out", nargs="?", type=Path)
    args = parser.parse_args()
    if args.command == "preflight":
        result: dict[str, Any] = {
            "authority": str(AUTHORITY),
            "authority_valid": bool(verify_authority()),
        }
    else:
        require(args.out is not None, f"{args.command} requires OUT")
        result = (
            run(args.out.resolve())
            if args.command == "run"
            else validate(args.out.resolve())
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
