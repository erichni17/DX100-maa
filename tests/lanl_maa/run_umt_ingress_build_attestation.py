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

BUILD_UNIT = "umt-ingress-trace-build-v5-20260830.service"
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
    "CCFLAGS_EXTRA=-DLANL_MAA_UMT_INGRESS_TRACE_TEST=1",
)
SOURCE_RELATIVES = (
    "src/mem/LANLMAA/UmtOrderedWaveIngressTrace.hh",
    "src/mem/LANLMAA/UmtOrderedWaveStreamState.hh",
    "src/mem/LANLMAA/lanl_maa.hh",
    "src/mem/LANLMAA/lanl_maa.cc",
    "tests/lanl_maa/umt_production_ingress_trace_test.cc",
    "tests/lanl_maa/run_umt_production_ingress_trace_gate.py",
)
PROTOCOL = "LANL_MAA_UMT_INGRESS_BUILD_ATTESTATION_V5"
SCHEMA = "lanl-maa-umt-ingress-build-attestation-v5"
# A target mention without an actual link command is not sufficient.  This
# intentionally rejects an unchanged/up-to-date incremental invocation.
RELINK_RE = re.compile(
    rb"(?m)^.*(?:Linking\s+|\s-o\s+)build/X86_UMT_T32_W2/gem5\.opt(?:\s|$)"
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
    stdout, stderr = evidence / "scons.stdout", evidence / "scons.stderr"
    evidence.mkdir(parents=True, exist_ok=False)
    with stdout.open("wb") as out, stderr.open("wb") as err:
        completed = subprocess.run(
            BUILD_ARGV,
            cwd=source,
            env=os.environ.copy(),
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
    gate = (
        "/usr/bin/python3",
        str(
            source / "tests/lanl_maa/run_umt_production_ingress_trace_gate.py"
        ),
        "--cxx",
        "g++",
    )
    with gate_stdout.open("wb") as out, gate_stderr.open("wb") as err:
        result = subprocess.run(
            gate, cwd=source, stdout=out, stderr=err, check=False
        )
    if result.returncode != 0:
        raise RuntimeError("observer gate failed")
    report_copy = evidence / "observer-report.json"
    report_copy.write_bytes(gate_stdout.read_bytes())
    try:
        if (
            json.loads(report_copy.read_text(encoding="utf-8")).get("status")
            != "passed"
        ):
            raise ValueError("observer report status")
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("observer gate report is invalid") from error
    value = {
        **common,
        "status": "passed",
        "build_argv": list(BUILD_ARGV),
        "build_environment": {},
        "build_returncode": 0,
        "required_relink_observed": True,
        "instrumentation_source_sha256": inputs,
        "build_artifacts": artifacts,
        "compiled_binary_markers": ["UMT_INGRESS kind=", "d64_hold cycle="],
        "observer_gate": {
            "command": list(gate),
            "returncode": 0,
            "report_sha256": sha256(report_copy),
        },
        "logs": {
            "scons_stdout_sha256": sha256(stdout),
            "scons_stderr_sha256": sha256(stderr),
            "observer_stdout_sha256": sha256(gate_stdout),
            "observer_stderr_sha256": sha256(gate_stderr),
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
