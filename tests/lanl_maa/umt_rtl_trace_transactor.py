#!/usr/bin/env python3
"""P0 fixture transactor and fail-closed RTL replay gate.

The scheduler shell deliberately has no trace ports.  This tool consumes the
trusted C++ JSONL trace and writes a *simulation-only* include used by the
Verilog testbench.  The include pins the semantic SHA-256 and selected C++
token identities; the testbench uses those identities while exercising the
existing admission/completion/external interfaces.  It is not included by any
RTL wrapper, synthesis script, or cost flow.
"""

import argparse
import hashlib
import importlib.util
import json
import pathlib
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
TRACE_SOURCE = ROOT / "tests/lanl_maa/umt_cycle_trace_replay_test.cc"
TRACE_CHECKER_PATH = ROOT / "tests/lanl_maa/umt_cycle_trace_jsonl.py"
RTL = ROOT / "experiments/lanl_maa_fp64_physical/rtl"
TESTBENCH = (
    ROOT
    / "experiments/lanl_maa_fp64_physical/tests/lanl_umt_trace_replay_tb.v"
)
TOOLS = pathlib.Path(
    "/data1/nier/tools/lanl-maa-fp64-physical-20260729/iverilog/usr"
)
CELLS = ((24, 1), (24, 2), (32, 1), (32, 2))
SCENARIO = "dense-g8.jsonl"


def checker_module():
    spec = importlib.util.spec_from_file_location(
        "umt_cycle_trace_jsonl", TRACE_CHECKER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CHECKER = checker_module()


def checked(command, description):
    result = subprocess.run(
        command, text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise RuntimeError(
            f"{description} failed:\n{result.stdout}\n{result.stderr}"
        )
    return result


def compile_cpp(directory, tokens, width, cxx):
    binary = directory / f"cxx-t{tokens}-w{width}"
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
            str(TRACE_SOURCE),
            "-o",
            str(binary),
        ],
        f"T{tokens}/W{width} C++ fixture compile",
    )
    first = directory / f"cxx-a-t{tokens}-w{width}"
    second = directory / f"cxx-b-t{tokens}-w{width}"
    checked(
        [str(binary), str(first)], f"T{tokens}/W{width} C++ fixture emission A"
    )
    checked(
        [str(binary), str(second)],
        f"T{tokens}/W{width} C++ fixture emission B",
    )
    return first, second


def selected_tags(records):
    tags = []
    for record in records[1:]:
        for issue in record["issues"]:
            if issue["valid"]:
                tags.append(issue["token"])
                if len(tags) == 2:
                    return tags
    raise ValueError("fixture has fewer than two selected issue tags")


def write_include(path, records, digest):
    tags = selected_tags(records)
    header = records[0]
    if header["compute_tokens"] <= max(tags):
        raise ValueError(
            "selected C++ tag lies outside the configured capacity"
        )
    words = [digest[index : index + 16] for index in range(0, 64, 16)]
    path.write_text(
        "// Generated in a temporary directory by umt_rtl_trace_transactor.py.\n"
        "// It is a simulation fixture, never a production RTL include.\n"
        f"localparam [63:0] CXX_P0_SHA0 = 64'h{words[0]};\n"
        f"localparam [63:0] CXX_P0_SHA1 = 64'h{words[1]};\n"
        f"localparam [63:0] CXX_P0_SHA2 = 64'h{words[2]};\n"
        f"localparam [63:0] CXX_P0_SHA3 = 64'h{words[3]};\n"
        f"localparam [5:0] CXX_P0_TAG0 = 6'd{tags[0]};\n"
        f"localparam [5:0] CXX_P0_TAG1 = 6'd{tags[1]};\n"
        f"localparam integer CXX_P0_TOKEN_CAPACITY = {header['compute_tokens']};\n",
        encoding="utf-8",
    )


def validate_pair(first, second):
    left = CHECKER.load_trace(first)
    right = CHECKER.load_trace(second)
    digest = CHECKER.semantic_digest(left)
    if left != right or digest != CHECKER.semantic_digest(right):
        raise ValueError("independent C++ fixture emissions differ")
    return left, digest


def run_rtl(directory, tokens, width, include):
    iverilog = TOOLS / "bin/iverilog"
    vvp = TOOLS / "bin/vvp"
    ivl = TOOLS / "lib/x86_64-linux-gnu/ivl"
    if not all(item.exists() for item in (iverilog, vvp, ivl / "ivl")):
        raise RuntimeError("pinned Icarus toolchain is unavailable")
    image = directory / f"rtl-t{tokens}-w{width}"
    checked(
        [
            str(iverilog),
            "-B",
            str(ivl),
            "-g2005",
            "-Wall",
            "-Wno-sensitivity-entire-array",
            "-I",
            str(include.parent),
            f"-P",
            f"lanl_umt_trace_replay_tb.COMPUTE_TOKENS={tokens}",
            f"-P",
            f"lanl_umt_trace_replay_tb.FP_ISSUE_WIDTH={width}",
            "-s",
            "lanl_umt_trace_replay_tb",
            "-o",
            str(image),
            str(RTL / "LanlUmtTokenEntry.v"),
            str(RTL / "LanlUmtRotatingPriority.v"),
            str(RTL / "LanlUmtBank16x640.v"),
            str(RTL / "LanlUmtSchedulerShell.v"),
            str(TESTBENCH),
        ],
        f"T{tokens}/W{width} RTL trace testbench compile",
    )
    result = checked(
        [str(vvp), "-M", str(ivl), str(image)],
        f"T{tokens}/W{width} RTL trace replay",
    )
    lines = [
        line
        for line in result.stdout.splitlines()
        if line.startswith("UMT_RTL_TRACE")
    ]
    required = {
        "tag_cursor",
        "same_unit",
        "same_bank",
        "edge_before_result",
        "future_ready",
        "zero_skip",
        "masked_store",
        "completion_ready",
        "divide_plus_64",
        "divide_ii_32",
    }
    seen = {
        line.split(" kind=")[1].split()[0]
        for line in lines
        if " kind=" in line
    }
    if required - seen:
        raise RuntimeError(
            f"T{tokens}/W{width}: missing trace coverage {sorted(required - seen)}\n"
            f"trace output:\n{result.stdout}"
        )
    if "LANL_UMT_TRACE_REPLAY_PASS" not in result.stdout:
        raise RuntimeError(f"T{tokens}/W{width}: missing PASS marker")
    return {
        "tokens": tokens,
        "issue_width": width,
        "trace_records": len(lines),
        "coverage": sorted(seen),
        "status": "passed",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    if shutil.which(args.cxx) is None:
        raise RuntimeError(f"C++ compiler not found: {args.cxx}")
    report = {"schema": "lanl-maa-umt-rtl-trace-replay-p1-v1", "cells": []}
    with tempfile.TemporaryDirectory(prefix="umt-rtl-trace-replay-") as temp:
        directory = pathlib.Path(temp)
        for tokens, width in CELLS:
            first, second = compile_cpp(directory, tokens, width, args.cxx)
            records, digest = validate_pair(
                first / SCENARIO, second / SCENARIO
            )
            include_dir = directory / f"fixture-t{tokens}-w{width}"
            include_dir.mkdir()
            include = include_dir / "umt_trace_fixture.vh"
            write_include(include, records, digest)
            cell = run_rtl(directory, tokens, width, include)
            cell["cxx_scenario"] = SCENARIO
            cell["cxx_semantic_sha256"] = digest
            cell[
                "fixture_integrity"
            ] = "independent semantic digest recomputation"
            report["cells"].append(cell)
    report["status"] = "passed"
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
