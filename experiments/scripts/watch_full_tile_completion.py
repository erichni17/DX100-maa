#!/usr/bin/env python3
"""Run the tile-sweep finalizer on workflow state changes until terminal."""

import argparse
import json
import subprocess
import sys
import time
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path

TERMINAL_STATES = {"completed", "failed", "skipped"}
WORKFLOWS = (
    "dx100-full-tile-sweep-recovery2-normal-20260721",
    "dx100-full-tile-sweep-recovery2-is-gate-20260721",
    "dx100-full-tile-sweep-recovery2-is-20260721",
    "dx100-full-tile-sweep-recovery2-auxiliary-20260721",
)


def load(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def terminal(document):
    if not document or not document.get("tasks"):
        return False
    return all(
        task.get("state") in TERMINAL_STATES
        for task in document["tasks"].values()
    )


def signature(paths):
    values = []
    for path in paths:
        try:
            stat = path.stat()
            values.append((stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            values.append(None)
    return tuple(values)


def append_log(path, message):
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    with path.open("a") as output:
        output.write(f"{stamp} {message}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()
    if args.interval < 1:
        raise SystemExit("--interval must be positive")

    run_root = args.run_root.resolve()
    state_root = args.state_root.resolve()
    finalizer = (
        Path(__file__).resolve().with_name("finalize_full_tile_sweep.py")
    )
    state_paths = [
        state_root / "workflows" / f"{name}.json" for name in WORKFLOWS
    ]
    auxiliary_retry_manifest = (
        run_root / "recovery2-auxiliary-retry-manifest-v2.json"
    )
    auxiliary_retry_done = run_root / "recovery2-auxiliary-retry-done.json"
    signature_paths = [*state_paths, auxiliary_retry_done]
    watcher_log = run_root / "final/watcher.log"
    prior_signature = None
    append_log(watcher_log, f"watcher started pid={__import__('os').getpid()}")

    while True:
        current_signature = signature(signature_paths)
        documents = [load(path) for path in state_paths]
        retry_record = load(auxiliary_retry_done)
        retry_terminal = not auxiliary_retry_manifest.is_file() or bool(
            retry_record and retry_record.get("terminal") is True
        )
        all_terminal = (
            all(terminal(document) for document in documents)
            and retry_terminal
        )
        if current_signature != prior_signature or all_terminal:
            command = [
                sys.executable,
                str(finalizer),
                "--run-root",
                str(run_root),
                "--state-root",
                str(state_root),
            ]
            if not all_terminal:
                command.append("--allow-incomplete")
            completed = subprocess.run(command, text=True, capture_output=True)
            detail = completed.stdout.strip() or completed.stderr.strip()
            append_log(
                watcher_log,
                f"finalizer rc={completed.returncode} terminal={all_terminal} {detail}",
            )
            prior_signature = current_signature
            if all_terminal:
                append_log(watcher_log, "watcher terminal")
                return completed.returncode
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
