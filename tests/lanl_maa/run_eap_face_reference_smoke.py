#!/usr/bin/env python3

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "benchmarks/LANL/eap_face_minmax.cc"


def parse_output(output):
    fields = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            fields[key] = value
    return fields


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run_case(binary, internal_mode, boundaries):
    command = [
        str(binary),
        "--faces",
        "4096",
        "--cells",
        "512",
        "--window",
        "64",
        "--internal-mode",
        internal_mode,
        "--boundaries",
        boundaries,
        "--seed",
        "0x4c414e4c",
    ]
    completed = subprocess.run(
        command, check=True, capture_output=True, text=True
    )
    fields = parse_output(completed.stdout)
    require(fields.get("verification") == "PASS", "verification failed")
    require(fields.get("internal_mode") == internal_mode, "mode mismatch")
    require(fields.get("boundary_source") == boundaries, "source mismatch")
    require(
        fields.get("read_logical_accesses")
        == fields.get("expected_read_logical_accesses"),
        "logical read count mismatch",
    )
    require(
        fields.get("update_logical") == fields.get("expected_update_logical"),
        "logical update count mismatch",
    )
    return fields


def main():
    parser = argparse.ArgumentParser(
        description="Compile and validate all EAP face reference branches"
    )
    parser.add_argument("--cxx", default=os.environ.get("CXX", "g++"))
    args = parser.parse_args()

    compiler = shutil.which(args.cxx)
    if compiler is None:
        raise RuntimeError(f"C++ compiler not found: {args.cxx}")

    with tempfile.TemporaryDirectory(prefix="eap-face-reference-") as temp:
        binary = Path(temp) / "eap_face_minmax"
        subprocess.run(
            [
                compiler,
                "-std=c++17",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I",
                str(ROOT / "src"),
                str(SOURCE),
                "-o",
                str(binary),
            ],
            check=True,
        )

        normal = run_case(binary, "normal", "none")
        guarded = run_case(binary, "rho-guard", "cell")
        pressure = run_case(binary, "pressure", "faceval")

        require(int(normal["internal_faces"]) > 0, "normal lacks internals")
        require(normal["low_boundary_faces"] == "0", "unexpected boundary")
        require(normal["high_boundary_faces"] == "0", "unexpected boundary")
        for branch in (guarded, pressure):
            require(int(branch["low_boundary_faces"]) > 0, "no low boundary")
            require(int(branch["high_boundary_faces"]) > 0, "no high boundary")
            require(int(branch["vacuum_internal_faces"]) > 0, "no vacuum")
        require(
            int(pressure["pressure_weighted_internal_faces"]) > 0,
            "pressure weighting branch was not exercised",
        )
        require(
            guarded["checksum"] != pressure["checksum"],
            "cell and faceval source cases unexpectedly matched",
        )

    print("LANLMAA EAP face reference branches: PASS")


if __name__ == "__main__":
    main()
