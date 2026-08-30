#!/usr/bin/env python3
"""Compile and execute the ordered-wave state unit test in all four cells."""

import argparse
import json
import pathlib
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tests/lanl_maa/umt_ordered_wave_stream_state_test.cc"
CELLS = ((24, 1), (24, 2), (32, 1), (32, 2))
INVALID_CELLS = ((23, 1), (25, 1), (32, 0), (32, 3))


def compile_cell(cxx, directory, tokens, issue_width):
    output = directory / f"umt-stream-t{tokens}-w{issue_width}"
    command = [
        cxx,
        "-std=c++17",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-DLANL_MAA_UMT_VARIANT_TEST_CONFIG=1",
        f"-DLANL_MAA_UMT_COMPUTE_TOKENS={tokens}",
        f"-DLANL_MAA_UMT_FP_ISSUE_WIDTH={issue_width}",
        "-I",
        str(ROOT / "src"),
        str(SOURCE),
        "-o",
        str(output),
    ]
    completed = subprocess.run(
        command, text=True, capture_output=True, check=False
    )
    return output, command, completed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    cxx = shutil.which(args.cxx)
    if cxx is None:
        raise RuntimeError(f"C++ compiler not found: {args.cxx}")

    report = {"schema": "lanl-maa-umt-stream-variant-unit-gate-v1"}
    valid = []
    invalid = []
    with tempfile.TemporaryDirectory(prefix="umt-stream-variants-") as tmp:
        directory = pathlib.Path(tmp)
        for tokens, issue_width in CELLS:
            output, command, completed = compile_cell(
                cxx, directory, tokens, issue_width
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    f"valid T{tokens}/W{issue_width} compile failed:\n"
                    f"{completed.stderr}"
                )
            executed = subprocess.run(
                [str(output)], text=True, capture_output=True, check=False
            )
            if executed.returncode != 0:
                raise RuntimeError(
                    f"valid T{tokens}/W{issue_width} unit failed:\n"
                    f"{executed.stderr}"
                )
            valid.append(
                {
                    "tokens": tokens,
                    "issue_width": issue_width,
                    "compile_command": command,
                    "status": "passed",
                }
            )

        for tokens, issue_width in INVALID_CELLS:
            _, command, completed = compile_cell(
                cxx, directory, tokens, issue_width
            )
            if completed.returncode == 0:
                raise RuntimeError(
                    f"invalid T{tokens}/W{issue_width} compiled successfully"
                )
            invalid.append(
                {
                    "tokens": tokens,
                    "issue_width": issue_width,
                    "compile_command": command,
                    "status": "rejected",
                }
            )

    report["valid_cells"] = valid
    report["invalid_cells"] = invalid
    report["status"] = "passed"
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
