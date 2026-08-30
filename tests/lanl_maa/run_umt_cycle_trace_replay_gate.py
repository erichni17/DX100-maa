#!/usr/bin/env python3
"""Compile, emit, validate, compare, and tamper-test UMT P0 C++ traces."""

import argparse
import json
import pathlib
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tests/lanl_maa/umt_cycle_trace_replay_test.cc"
CHECKER = ROOT / "tests/lanl_maa/umt_cycle_trace_jsonl.py"
CELLS = ((24, 1), (24, 2), (32, 1), (32, 2))
SCENARIOS = tuple(
    f"{density}-g{groups}.jsonl"
    for density in ("sparse", "dense")
    for groups in (1, 8, 16, 32, 64)
)


def checked(command, description):
    result = subprocess.run(
        command, text=True, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"{description} failed:\n{result.stderr}")
    return result


def semantic_digest(trace):
    result = checked(
        ["python3", str(CHECKER), str(trace)],
        f"{trace.name} schema/integrity validation",
    )
    return json.loads(result.stdout)["semantic_digest"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    cxx = shutil.which(args.cxx)
    if cxx is None:
        raise RuntimeError(f"C++ compiler not found: {args.cxx}")
    report = {"schema": "lanl-maa-umt-cycle-trace-p0-gate-v1", "cells": []}
    with tempfile.TemporaryDirectory(
        prefix="umt-cycle-trace-p0-"
    ) as temporary:
        root = pathlib.Path(temporary)
        for tokens, width in CELLS:
            output = root / f"umt-cycle-trace-t{tokens}-w{width}"
            checked(
                [
                    cxx,
                    "-std=c++17",
                    "-O2",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-DLANL_MAA_UMT_VARIANT_TEST_CONFIG=1",
                    "-DLANL_MAA_UMT_CYCLE_TRACE_TEST=1",
                    f"-DLANL_MAA_UMT_COMPUTE_TOKENS={tokens}",
                    f"-DLANL_MAA_UMT_FP_ISSUE_WIDTH={width}",
                    "-I",
                    str(ROOT / "src"),
                    str(SOURCE),
                    "-o",
                    str(output),
                ],
                f"T{tokens}/W{width} compile",
            )
            trace_directory = root / f"traces-t{tokens}-w{width}-a"
            replay_directory = root / f"traces-t{tokens}-w{width}-b"
            checked(
                [str(output), str(trace_directory)],
                f"T{tokens}/W{width} trace emission",
            )
            checked(
                [str(output), str(replay_directory)],
                f"T{tokens}/W{width} independent trace emission",
            )
            if tuple(
                sorted(item.name for item in trace_directory.iterdir())
            ) != tuple(sorted(SCENARIOS)):
                raise RuntimeError(
                    f"T{tokens}/W{width}: incomplete P0 portfolio"
                )
            for scenario in SCENARIOS:
                trace = trace_directory / scenario
                digest = semantic_digest(trace)
                checked(
                    [
                        "python3",
                        str(CHECKER),
                        str(trace),
                        "--compare",
                        str(replay_directory / scenario),
                        "--expected-semantic-digest",
                        digest,
                    ],
                    f"T{tokens}/W{width} {scenario} replay/integrity compare",
                )
            tampered = trace_directory / "tampered.jsonl"
            text = (trace_directory / SCENARIOS[0]).read_text(encoding="utf-8")
            tampered.write_text(
                text.replace('"schema_version":1', '"schema_version":2', 1),
                encoding="utf-8",
            )
            rejected = subprocess.run(
                ["python3", str(CHECKER), str(tampered)],
                text=True,
                capture_output=True,
                check=False,
            )
            if rejected.returncode == 0:
                raise RuntimeError(
                    f"T{tokens}/W{width}: tampered header accepted"
                )
            report["cells"].append(
                {
                    "tokens": tokens,
                    "issue_width": width,
                    "scenarios": len(SCENARIOS),
                    "schema": "passed",
                    "repeat_compare": "passed",
                    "fixture_integrity": "semantic digest recomputed",
                    "tamper": "rejected",
                }
            )
    report["status"] = "passed"
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
