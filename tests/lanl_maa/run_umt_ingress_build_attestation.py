#!/usr/bin/env python3
"""Service-owned, fail-closed build attestation for the ingress observer.

This is deliberately a wrapper rather than an external log collector.  Its
only terminal success record is emitted by the process systemd launches, and
only after SCons, the artifact/source digests, and the observer gate pass.
"""

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

BUILD_UNIT = "umt-ingress-trace-build-v6-20260830.service"
SOURCE_ROOT = "/data1/nier/worktrees/DX100-umt-trace-replay-20260830"
TARGET_RELATIVE = "build/X86_UMT_T32_W2/gem5.opt"
CONFIG_RELATIVES = (
    "build/X86_UMT_T32_W2/config.hh",
    "build/X86_UMT_T32_W2/config.cc",
)
BUILD_ARGV = (
    "/usr/bin/scons",
    "--ignore-style",
    TARGET_RELATIVE,
    "-j4",
    "CPPDEFINES=LANL_MAA_UMT_INGRESS_TRACE_TEST",
)
SOURCE_RELATIVES = (
    "src/mem/LANLMAA/UmtOrderedWaveIngressTrace.hh",
    "src/mem/LANLMAA/UmtOrderedWaveStreamState.hh",
    "src/mem/LANLMAA/lanl_maa.hh",
    "src/mem/LANLMAA/lanl_maa.cc",
    "tests/lanl_maa/umt_production_ingress_trace_test.cc",
    "tests/lanl_maa/run_umt_production_ingress_trace_gate.py",
)
PROTOCOL = "LANL_MAA_UMT_INGRESS_BUILD_ATTESTATION_V6"
SCHEMA = "lanl-maa-umt-ingress-build-attestation-v6"
# A target mention without an actual link command is not sufficient.  This
# intentionally rejects an unchanged/up-to-date incremental invocation.
RELINK_RE = re.compile(
    rb"(?m)^.*(?:Linking\s+|\s-o\s+)build/X86_UMT_T32_W2/gem5\.opt(?:\s|$)"
)
SAFE_CHILD_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "LC_ALL": "C",
    "LANG": "C",
    "TZ": "UTC",
}
TOOL_AFFECTING_ENV_PREFIXES = (
    "CC",
    "CXX",
    "CPP",
    "CFLAGS",
    "CXXFLAGS",
    "CCFLAGS",
    "CPPFLAGS",
    "LDFLAGS",
    "LD",
    "AR",
    "RANLIB",
    "SCons",
    "SCONS",
    "PYTHON",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "CONDA",
    "PATH",
    "HOME",
    "TMP",
    "CCACHE",
    "SCCACHE",
    "DISTCC",
    "ICECC",
)


def sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def proc_start_ticks():
    # comm can contain spaces and ')' so stat field 22 is safest after the
    # final ')'.  The result is a kernel tick identity, not wall-clock text.
    raw = pathlib.Path("/proc/self/stat").read_text(encoding="ascii")
    tail = raw.rsplit(")", 1)[1].split()
    return tail[19]


def no_clobber_json(path, value):
    path = pathlib.Path(path)
    if path.exists():
        raise RuntimeError("refusing to overwrite wrapper evidence")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def marker(kind, **fields):
    return (
        PROTOCOL
        + " "
        + kind
        + " "
        + json.dumps(fields, sort_keys=True, separators=(",", ":"))
    )


def inherited_tool_affecting_names(environment):
    """Expose names/count only; values are never evidence or terminal output."""
    return sorted(
        name
        for name in environment
        if name.upper().startswith(TOOL_AFFECTING_ENV_PREFIXES)
    )


def evidence_artifact(evidence, name):
    path = evidence / name
    if not path.is_file() or path.parent != evidence:
        raise RuntimeError("wrapper evidence artifact path is not exact")
    return {"path": str(path), "sha256": sha256(path)}


def validate_gate_report(value, source, target, target_sha256, inputs):
    expected_cells = [
        {
            "tokens": tokens,
            "issue_width": width,
            "waiter_counts": [1, 7, 8],
            "abi_boundaries": ["D32", "D64"],
            "two_lane_serialization": "rejected_by_trace_difference",
            "default_off": "compiled_without_observer_macro",
        }
        for tokens, width in ((24, 1), (24, 2), (32, 1), (32, 2))
    ]
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema",
            "status",
            "source_root",
            "input_source_sha256",
            "binary",
            "binary_sha256",
            "required_define",
            "compiled_binary_markers",
            "cells",
        }
        or value["schema"] != "lanl-maa-umt-production-ingress-trace-v2"
        or value["status"] != "passed"
        or pathlib.Path(value["source_root"]).resolve() != source
        or value["input_source_sha256"] != inputs
        or pathlib.Path(value["binary"]).resolve() != target
        or value["binary_sha256"] != target_sha256
        or value["required_define"] != "LANL_MAA_UMT_INGRESS_TRACE_TEST"
        or value["compiled_binary_markers"]
        != ["UMT_INGRESS kind=", "d64_hold cycle="]
        or value["cells"] != expected_cells
    ):
        raise RuntimeError(
            "observer gate report is not exact v2 source/binary evidence"
        )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--evidence-dir", required=True)
    args = parser.parse_args(argv)
    source, evidence = (
        pathlib.Path(args.source).resolve(),
        pathlib.Path(args.evidence_dir).resolve(),
    )
    invocation = os.environ.get("INVOCATION_ID", "")
    if (
        args.unit != BUILD_UNIT
        or source != pathlib.Path(SOURCE_ROOT)
        or not re.fullmatch(r"[0-9a-f]{32}", invocation)
    ):
        raise RuntimeError("wrapper identity/invocation binding is invalid")
    if evidence.exists() or not source.is_dir():
        raise RuntimeError("wrapper evidence/source path is invalid")
    pid, start = os.getpid(), proc_start_ticks()
    common = {
        "schema": SCHEMA,
        "unit": args.unit,
        "invocation_id": invocation,
        "wrapper_pid": pid,
        "wrapper_proc_start_ticks": start,
    }
    print(marker("START", **common), flush=True)
    inherited_names = inherited_tool_affecting_names(os.environ)
    stdout, stderr = evidence / "scons.stdout", evidence / "scons.stderr"
    evidence.mkdir(parents=True, exist_ok=False)
    with stdout.open("wb") as out, stderr.open("wb") as err:
        completed = subprocess.run(
            BUILD_ARGV,
            cwd=source,
            env=SAFE_CHILD_ENV,
            stdout=out,
            stderr=err,
            check=False,
        )
    if completed.returncode != 0:
        raise RuntimeError("SCons returned nonzero")
    if not RELINK_RE.search(stdout.read_bytes()):
        raise RuntimeError("SCons did not relink the exact target")
    target, config_hh, config_cc = source / TARGET_RELATIVE, *(
        source / x for x in CONFIG_RELATIVES
    )
    inputs = {
        relative: sha256(source / relative) for relative in SOURCE_RELATIVES
    }
    # The previous build had the apparently-correct argv but lacked these
    # compiled literals.  Treat their absence as a cache/flag propagation
    # failure; a separate C++ observer test cannot attest this gem5 binary.
    target_bytes = target.read_bytes()
    if (
        b"UMT_INGRESS kind=" not in target_bytes
        or b"d64_hold cycle=" not in target_bytes
    ):
        raise RuntimeError(
            "rebuilt gem5 lacks compiled ingress trace literals"
        )
    artifacts = {
        "gem5": sha256(target),
        "config_hh": sha256(config_hh),
        "config_cc": sha256(config_cc),
    }
    gate_stdout, gate_stderr = (
        evidence / "observer.stdout",
        evidence / "observer.stderr",
    )
    manifest = evidence / "observer-input-source-sha256.json"
    no_clobber_json(manifest, inputs)
    literal_scan = evidence / "target-config-literal-scan.json"
    no_clobber_json(
        literal_scan,
        {
            "target": str(target),
            "target_sha256": artifacts["gem5"],
            "config_hh": str(config_hh),
            "config_hh_sha256": artifacts["config_hh"],
            "config_cc": str(config_cc),
            "config_cc_sha256": artifacts["config_cc"],
            "compiled_binary_markers": [
                "UMT_INGRESS kind=",
                "d64_hold cycle=",
            ],
        },
    )
    gate = (
        "/usr/bin/python3",
        str(
            source / "tests/lanl_maa/run_umt_production_ingress_trace_gate.py"
        ),
        "--cxx",
        "g++",
        "--binary",
        str(target),
        "--binary-sha256",
        artifacts["gem5"],
        "--input-source-sha256",
        str(manifest),
    )
    with gate_stdout.open("wb") as out, gate_stderr.open("wb") as err:
        result = subprocess.run(
            gate,
            cwd=source,
            env=SAFE_CHILD_ENV,
            stdout=out,
            stderr=err,
            check=False,
        )
    if result.returncode != 0:
        raise RuntimeError("observer gate failed")
    report_copy = evidence / "observer-report.json"
    report_copy.write_bytes(gate_stdout.read_bytes())
    try:
        validate_gate_report(
            json.loads(report_copy.read_text(encoding="utf-8")),
            source,
            target,
            artifacts["gem5"],
            inputs,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("observer gate report is invalid") from error
    transcript = evidence / "observer-transcript.txt"
    transcript.write_text("status=0/SUCCESS\n", encoding="ascii")
    value = {
        **common,
        "status": "passed",
        "build_argv": list(BUILD_ARGV),
        "build_environment": {
            "sanitized": sorted(SAFE_CHILD_ENV),
            "inherited_tool_affecting_names": inherited_names,
            "inherited_tool_affecting_count": len(inherited_names),
        },
        "build_returncode": 0,
        "required_relink_observed": True,
        "instrumentation_source_sha256": inputs,
        "build_artifacts": artifacts,
        "compiled_binary_markers": ["UMT_INGRESS kind=", "d64_hold cycle="],
        "observer_gate": {
            "command": list(gate),
            "returncode": 0,
            "report": evidence_artifact(evidence, "observer-report.json"),
            "transcript": evidence_artifact(
                evidence, "observer-transcript.txt"
            ),
        },
        "evidence": {
            "scons_stdout": evidence_artifact(evidence, "scons.stdout"),
            "scons_stderr": evidence_artifact(evidence, "scons.stderr"),
            "observer_stdout": evidence_artifact(evidence, "observer.stdout"),
            "observer_stderr": evidence_artifact(evidence, "observer.stderr"),
            "observer_report": evidence_artifact(
                evidence, "observer-report.json"
            ),
            "observer_transcript": evidence_artifact(
                evidence, "observer-transcript.txt"
            ),
            "source_manifest": evidence_artifact(
                evidence, "observer-input-source-sha256.json"
            ),
            "target_config_literal_scan": evidence_artifact(
                evidence, "target-config-literal-scan.json"
            ),
        },
    }
    no_clobber_json(evidence / "attestation.json", value)
    print(
        marker("SUCCESS", **common, target_sha256=artifacts["gem5"]),
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            f"{PROTOCOL} FAILURE {type(error).__name__}",
            file=sys.stderr,
            flush=True,
        )
        raise
