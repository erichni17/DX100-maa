#!/usr/bin/env python3
"""Fail-closed UMT C++-fixture-to-RTL public-interface replay audit.

This is deliberately an evidence gate, not a trace-shaped success marker.
It freezes two P0 C++ fixtures per T/W cell, derives the public ingress plan
for every C++ decision cycle, and rejects an equivalence claim if that plan
cannot be presented by the shell.  The selected g8 fixtures expose such a
real mismatch immediately: the C++ model admits eight denominator operations
at its first decision boundary while the public RTL shell has two admission
ports.  We preserve that mismatch rather than splitting the C++ cycle or
pretending that a different RTL timeline is equivalent.

The accompanying simulation-only testbench emits a canonical JSON projection
at its pre-edge decision boundary.  Its P1 directed scenarios remain useful
for validating the projection channel itself; they are never relabeled as a
P0 C++/RTL equivalence result.
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
MANIFEST = ROOT / "tests/lanl_maa/umt_rtl_trace_fixture_manifest.json"
RTL = ROOT / "experiments/lanl_maa_fp64_physical/rtl"
TESTBENCH = (
    ROOT
    / "experiments/lanl_maa_fp64_physical/tests/lanl_umt_trace_replay_tb.v"
)
TOOLS = pathlib.Path(
    "/data1/nier/tools/lanl-maa-fp64-physical-20260729/iverilog/usr"
)
CELLS = ((24, 1), (24, 2), (32, 1), (32, 2))
SCENARIOS = ("sparse-g8.jsonl", "dense-g8.jsonl")
PROJECTION_SCHEMA = "lanl-maa-umt-rtl-projection-v1"


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


def sha256_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


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
    first, second = (
        directory / f"cxx-a-t{tokens}-w{width}",
        directory / f"cxx-b-t{tokens}-w{width}",
    )
    checked(
        [str(binary), str(first)], f"T{tokens}/W{width} C++ fixture emission A"
    )
    checked(
        [str(binary), str(second)],
        f"T{tokens}/W{width} C++ fixture emission B",
    )
    return first, second


def fixture_events(records):
    """The immutable event hash excludes cosmetic header labels only."""
    return [
        {
            "cycle": record["cycle"],
            "denominator_ingress": record["inputs"]["denominator_ingress"],
            "issues": record["issues"],
            "completion_ready": record["completion_ready"],
            "bank_word_changes": record["bank_word_changes"],
            "state": record["state"],
            "counters": record["counters"],
        }
        for record in records[1:]
    ]


def load_manifest():
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if value.get("schema") != "lanl-maa-umt-rtl-trace-fixture-manifest-v1":
        raise ValueError("fixture manifest schema is unsupported")
    if tuple(value.get("scenarios", ())) != SCENARIOS:
        raise ValueError(
            "fixture manifest does not pin the selected P0 portfolio"
        )
    if value.get("fixture_source_sha256") != sha256_file(TRACE_SOURCE):
        raise ValueError("fixture source SHA differs from immutable manifest")
    cells = {
        (item["compute_tokens"], item["fp_issue_width"]): item
        for item in value.get("cells", [])
    }
    if set(cells) != set(CELLS):
        raise ValueError(
            "fixture manifest does not cover exactly the four T/W cells"
        )
    return cells


def validate_fixture(first, second, scenario, expected):
    left, right = CHECKER.load_trace(first / scenario), CHECKER.load_trace(
        second / scenario
    )
    digest = CHECKER.semantic_digest(left)
    events = fixture_events(left)
    if left != right or digest != CHECKER.semantic_digest(right):
        raise ValueError(
            f"{scenario}: independently emitted C++ fixtures differ"
        )
    observed = {
        "trace_semantic_sha256": digest,
        "event_sha256": canonical_sha256(events),
        "cycles": len(events),
        "first_cycle": events[0]["cycle"],
        "max_denominator_ingress": max(
            len(item["denominator_ingress"]) for item in events
        ),
    }
    if observed != expected:
        raise ValueError(
            f"{scenario}: fixture manifest stale or tampered: {observed} != {expected}"
        )
    return left, observed


def public_interface_mapping(records):
    """Derive, rather than invent, the requested public ingress per cycle.

    Admission tags are the C++ first-free binding for the chosen fixture's
    first batch.  A list longer than two is a hard mapping failure: the RTL
    exports exactly admit0 and admit1, and carrying it into a later cycle
    changes the C++ pre-edge state, selected issues, counters, and digest.
    """
    mapping, failures = [], []
    for record in records[1:]:
        ingress = record["inputs"]["denominator_ingress"]
        selected_tags = [item["operation"] for item in ingress]
        if len(set(selected_tags)) != len(selected_tags):
            failures.append(
                {
                    "cycle": record["cycle"],
                    "reason": "non-unique_cxx_admission_tag",
                }
            )
        if len(ingress) > 2:
            failures.append(
                {
                    "cycle": record["cycle"],
                    "reason": "public_admission_width_exceeded",
                    "cxx_presented": len(ingress),
                    "rtl_public_capacity": 2,
                    "selected_cxx_admission_tags": selected_tags,
                }
            )
        mapping.append(
            {
                "cycle": record["cycle"],
                "presented_denominator_ingress": ingress,
                "selected_cxx_admission_tags": selected_tags,
                "expected_issues": record["issues"],
                "expected_completion_ready": record["completion_ready"],
                "expected_bank_word_changes": record["bank_word_changes"],
                "expected_state": record["state"],
                "expected_counters": record["counters"],
            }
        )
    return mapping, failures


def selected_issue_tags(records):
    tags = []
    for record in records[1:]:
        for issue in record["issues"]:
            if issue["valid"]:
                tags.append(issue["token"])
                if len(tags) == 2:
                    return tags
    raise ValueError("C++ fixture contains fewer than two selected issue tags")


def write_include(path, tokens, trace_sha, tags):
    if len(tags) != 2 or any(tag < 0 or tag >= tokens for tag in tags):
        raise ValueError(
            "selected C++ tags are outside the configured RTL cell"
        )
    words = [trace_sha[index : index + 16] for index in range(0, 64, 16)]
    path.write_text(
        "// Generated simulation-only fixture; never a production RTL include.\n"
        + "".join(
            f"localparam [63:0] CXX_P0_SHA{index} = 64'h{word};\n"
            for index, word in enumerate(words)
        )
        + f"localparam [5:0] CXX_P0_TAG0 = 6'd{tags[0]};\n"
        + f"localparam [5:0] CXX_P0_TAG1 = 6'd{tags[1]};\n"
        + f"localparam integer CXX_P0_TOKEN_CAPACITY = {tokens};\n",
        encoding="utf-8",
    )


def validate_projection(records):
    if not records:
        raise ValueError("RTL emitted no canonical projections")
    required = {
        "schema",
        "serial",
        "cycle",
        "kind",
        "presented",
        "accepted",
        "issues",
        "writeback",
        "state",
        "counters",
    }
    previous = -1
    for record in records:
        if set(record) != required or record["schema"] != PROJECTION_SCHEMA:
            raise ValueError("RTL projection schema is incomplete or stale")
        if record["serial"] != previous + 1:
            raise ValueError("RTL projection serials are not contiguous")
        previous = record["serial"]
        if len(record["issues"]) != 2 or len(record["writeback"]) != 4:
            raise ValueError(
                "RTL projection omits ordered issue/writeback lanes"
            )

        # JSON cannot carry Verilog X/Z safely.  Reject it anywhere in the
        # projection, including hierarchical observational state.
        def has_unknown(value, key=None):
            if isinstance(value, dict):
                return any(
                    has_unknown(item, name) for name, item in value.items()
                )
            if isinstance(value, list):
                return any(has_unknown(item) for item in value)
            return (
                isinstance(value, str)
                and key not in {"schema", "kind"}
                and any(letter in value.lower() for letter in ("x", "z"))
            )

        if has_unknown(record):
            raise ValueError("RTL projection contains unknown/high-Z state")
    return canonical_sha256(records)


def read_projection(stdout):
    prefix = "UMT_RTL_PROJECTION "
    records = []
    for line in stdout.splitlines():
        if line.startswith(prefix):
            try:
                records.append(json.loads(line[len(prefix) :]))
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"malformed RTL projection: {error.msg}"
                ) from error
    return records


def assert_projection_file(path, expected_digest, expected_count):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) != expected_count:
        raise ValueError("RTL projection has missing or extra decision cycles")
    if validate_projection(payload) != expected_digest:
        raise ValueError("RTL projection semantic SHA differs")


def projection_negative_tests(directory, records, digest):
    baseline = directory / "rtl-projection.json"
    baseline.write_text(
        json.dumps(records, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    assert_projection_file(baseline, digest, len(records))
    negatives = {}
    tampered = json.loads(baseline.read_text(encoding="utf-8"))
    tampered[0]["kind"] = "tampered"
    path = directory / "rtl-projection-tampered.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    try:
        assert_projection_file(path, digest, len(records))
    except ValueError:
        negatives["tamper"] = "rejected"
    else:
        raise RuntimeError("tampered RTL projection accepted")
    path = directory / "rtl-projection-missing-cycle.json"
    path.write_text(json.dumps(records[:-1]), encoding="utf-8")
    try:
        assert_projection_file(path, digest, len(records))
    except ValueError:
        negatives["missing_cycle"] = "rejected"
    else:
        raise RuntimeError("missing RTL projection cycle accepted")
    try:
        assert_projection_file(baseline, "0" * 64, len(records))
    except ValueError:
        negatives["stale_expected_sha"] = "rejected"
    else:
        raise RuntimeError("stale RTL projection SHA accepted")
    return negatives


def run_rtl(directory, tokens, width, trace_sha, tags):
    iverilog, vvp, ivl = (
        TOOLS / "bin/iverilog",
        TOOLS / "bin/vvp",
        TOOLS / "lib/x86_64-linux-gnu/ivl",
    )
    if not all(item.exists() for item in (iverilog, vvp, ivl / "ivl")):
        raise RuntimeError("pinned Icarus toolchain is unavailable")
    include_dir = directory / f"fixture-t{tokens}-w{width}"
    include_dir.mkdir()
    write_include(
        include_dir / "umt_trace_fixture.vh", tokens, trace_sha, tags
    )
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
            str(include_dir),
            "-P",
            f"lanl_umt_trace_replay_tb.COMPUTE_TOKENS={tokens}",
            "-P",
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
        f"T{tokens}/W{width} RTL projection compile",
    )
    result = checked(
        [str(vvp), "-M", str(ivl), str(image)],
        f"T{tokens}/W{width} RTL projection run",
    )
    marker = f"LANL_UMT_TRACE_REPLAY_PASS T{tokens}W{width}"
    if marker not in result.stdout:
        raise RuntimeError(f"T{tokens}/W{width}: missing terminal PASS marker")
    projections = read_projection(result.stdout)
    digest = validate_projection(projections)
    negatives = projection_negative_tests(directory, projections, digest)
    return {
        "records": len(projections),
        "semantic_sha256": digest,
        "negative_tests": negatives,
        "marker_and_exit": "passed",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    if shutil.which(args.cxx) is None:
        raise RuntimeError(f"C++ compiler not found: {args.cxx}")
    manifest = load_manifest()
    report = {"schema": "lanl-maa-umt-rtl-trace-replay-p2-v1", "cells": []}
    with tempfile.TemporaryDirectory(
        prefix="umt-rtl-trace-replay-p2-"
    ) as temporary:
        directory = pathlib.Path(temporary)
        for tokens, width in CELLS:
            first, second = compile_cpp(directory, tokens, width, args.cxx)
            cell = {"tokens": tokens, "issue_width": width, "fixtures": []}
            trace_sha_for_p1 = None
            tags_for_p1 = None
            for scenario in SCENARIOS:
                records, immutable = validate_fixture(
                    first,
                    second,
                    scenario,
                    manifest[(tokens, width)]["scenarios"][scenario],
                )
                mapping, failures = public_interface_mapping(records)
                if not failures:
                    raise RuntimeError(
                        f"{scenario}: expected a public interface incompatibility was not detected"
                    )
                # This exact compare is intentionally fail-closed before a
                # different-width RTL run can be mistaken for the C++ cycle.
                cell["fixtures"].append(
                    {
                        "scenario": scenario,
                        "immutable": immutable,
                        "mapping_sha256": canonical_sha256(mapping),
                        "equivalence": "failed_closed_public_interface_mismatch",
                        "mismatch": failures[0],
                    }
                )
                trace_sha_for_p1 = immutable["trace_semantic_sha256"]
                tags_for_p1 = selected_issue_tags(records)
            cell["rtl_projection_p1"] = run_rtl(
                directory, tokens, width, trace_sha_for_p1, tags_for_p1
            )
            cell[
                "status"
            ] = "p0_not_equivalent_p1_projection_channel_validated"
            report["cells"].append(cell)
    report["status"] = "passed_fail_closed_divergence_preserved"
    report[
        "equivalence_claim"
    ] = "rejected: C++ g8 first decision cycle has eight admissions; RTL public ingress capacity is two"
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
