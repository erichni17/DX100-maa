#!/usr/bin/env python3
"""Compile and run the default-off UMT production-ingress observer gate."""

import argparse
import json
import pathlib
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tests/lanl_maa/umt_production_ingress_trace_test.cc"
DEFAULT_OFF_SOURCE = (
    ROOT / "tests/lanl_maa/umt_ingress_default_off_compile_test.cc"
)
CELLS = ((24, 1), (24, 2), (32, 1), (32, 2))


def checked(command, description):
    result = subprocess.run(
        command, text=True, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"{description} failed:\n{result.stderr}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    cxx = shutil.which(args.cxx)
    if cxx is None:
        raise RuntimeError(f"C++ compiler not found: {args.cxx}")
    report = {
        "schema": "lanl-maa-umt-production-ingress-trace-v1",
        "cells": [],
    }
    with tempfile.TemporaryDirectory(prefix="umt-production-ingress-") as temp:
        root = pathlib.Path(temp)
        for tokens, width in CELLS:
            output = root / f"umt-ingress-t{tokens}-w{width}"
            common = [
                cxx,
                "-std=c++17",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-DLANL_MAA_UMT_VARIANT_TEST_CONFIG=1",
                f"-DLANL_MAA_UMT_COMPUTE_TOKENS={tokens}",
                f"-DLANL_MAA_UMT_FP_ISSUE_WIDTH={width}",
                "-I",
                str(ROOT / "src"),
            ]
            checked(
                common
                + [
                    "-DLANL_MAA_UMT_INGRESS_TRACE_TEST=1",
                    str(SOURCE),
                    "-o",
                    str(output),
                ],
                f"T{tokens}/W{width} observer compile",
            )
            checked([str(output)], f"T{tokens}/W{width} observer execution")
            # The source also has a normal (macro absent) compilation shape,
            # proving the observer's state is not required by production.
            checked(
                common + ["-fsyntax-only", str(DEFAULT_OFF_SOURCE)],
                f"T{tokens}/W{width} default-off compilation shape",
            )
            report["cells"].append(
                {
                    "tokens": tokens,
                    "issue_width": width,
                    "waiter_counts": [1, 7, 8],
                    "abi_boundaries": ["D32", "D64"],
                    "two_lane_serialization": "rejected_by_trace_difference",
                    "default_off": "compiled_without_observer_macro",
                }
            )
    report["status"] = "passed"
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
