#!/usr/bin/env python3
"""Run and validate the native-derived Branson replay sensitivity matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
from typing import Any


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_output(text: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value if key == "verification" else int(value)
    return values


def validate_case(values: dict[str, Any]) -> None:
    if (
        values.get("verification") != "PASS"
        or values.get("native_physics_recomputed") != 0
    ):
        raise RuntimeError("replay did not pass or mislabeled native physics")
    events = values["events"]
    if values["event_line_reads"] + values["event_line_hits"] != events:
        raise RuntimeError("event-line accounting does not close")
    if values["residency_hits"] + values["residency_misses"] != events:
        raise RuntimeError("residency accounting does not close")
    logical_updates = values["logical_fp64_updates"]
    if logical_updates != 2 * events:
        raise RuntimeError("logical FP64 update accounting does not close")
    if (
        values["fp64_update_drains"] + values["fp64_combiner_hits"]
        != logical_updates
    ):
        raise RuntimeError("FP64 combiner accounting does not close")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=pathlib.Path)
    parser.add_argument("--input", required=True, type=pathlib.Path)
    parser.add_argument("--source", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    replay_input = args.input.resolve(strict=True)
    source = args.source.resolve(strict=True)
    common = [
        str(binary),
        "--input",
        str(replay_input),
        "--context-quantum",
        "4",
    ]
    cases = {
        "anchor": [],
        "quantum_1": ["--context-quantum", "1"],
        "quantum_16": ["--context-quantum", "16"],
        "contexts_4": ["--contexts", "4"],
        "contexts_8": ["--contexts", "8"],
        "contexts_32": ["--contexts", "32"],
        "event_lines_2": ["--event-lines", "2"],
        "event_lines_32": ["--event-lines", "32"],
        "residency_4": ["--residency-entries", "4"],
        "residency_32": ["--residency-entries", "32"],
        "residency_banks_1": ["--residency-banks", "1"],
        "residency_banks_4": ["--residency-banks", "4"],
        "combiner_4": ["--combiner-entries", "4"],
        "combiner_32": ["--combiner-entries", "32"],
    }
    results: dict[str, dict[str, Any]] = {}
    for name, overrides in cases.items():
        command = common + overrides
        completed = subprocess.run(
            command, text=True, capture_output=True, check=False
        )
        if completed.returncode != 0:
            raise RuntimeError(f"{name} failed: {completed.stderr.strip()}")
        values = parse_output(completed.stdout)
        validate_case(values)
        results[name] = values

    quantum_hits = [
        results[name]["residency_hits"]
        for name in ("quantum_1", "anchor", "quantum_16")
    ]
    event_line_hits = [
        results[name]["event_line_hits"]
        for name in ("quantum_1", "anchor", "quantum_16")
    ]
    if quantum_hits != sorted(quantum_hits) or event_line_hits != sorted(
        event_line_hits
    ):
        raise RuntimeError("quantum locality sensitivity is not monotonic")

    negative = subprocess.run(
        [str(binary), "--input", str(replay_input), "--corrupt-first-source"],
        text=True,
        capture_output=True,
        check=False,
    )
    if (
        negative.returncode != 2
        or "replay cell validation failed" not in negative.stderr
    ):
        raise RuntimeError("corrupt source-cell case did not fail closed")

    output = {
        "schema_version": 1,
        "status": "native-derived-branson-event-replay-reference-passed",
        "binary_sha256": sha256_file(binary),
        "input_sha256": sha256_file(replay_input),
        "source_sha256": sha256_file(source),
        "cases": results,
        "negative": {
            "name": "corrupt-first-source",
            "return_code": negative.returncode,
            "expected_error_observed": True,
            "published_output": False,
        },
        "claim_boundary": (
            "Trace-derived event replay with staged event outcomes; native "
            "Branson physics and performance are not represented."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "cases": len(results),
                "anchor_events": results["anchor"]["events"],
                "anchor_residency_hits": results["anchor"]["residency_hits"],
                "anchor_combiner_hits": results["anchor"][
                    "fp64_combiner_hits"
                ],
                "negative_passed": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
