#!/usr/bin/env python3
"""Construct and enforce the frozen full-CG host numerical oracle.

This tool deliberately does not invoke gem5.  ``build`` compiles a one-shot,
single-threaded host BASE probe against the frozen class-C header and exports
the resulting binary32 x/z vectors.  ``verify`` is the gate a later full
candidate must pass; it only accepts a complete, hash-attested candidate
vector manifest and applies criteria written during construction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import (
    Any,
    Iterable,
)

SCHEMA = "dx100.cg.full.host_golden_oracle.v1"
CANDIDATE_SCHEMA = "dx100.cg.full.candidate_vector.v1"
NA = 150_000
VECTOR_BYTES = NA * 4
FROZEN_SOURCE_COMMIT = "5d51743bfca566c486c6786cf3b18e6d378d805a"
FROZEN_SOURCE_BLOB = "78f9d77983565fd6a0c32a3db627956f84b1cfdd"
FROZEN_HEADER_SHA256 = (
    "f2b18716e4a2356c597c95ee3583549def72700f2cb3294b0fcaacca46dbe131"
)
FROZEN_HEADER_BYTES = 992_830_458
FROZEN_REFERENCE_LOG = (
    "/data1/nier/dx100-runs/2026-08-11-cg-bounded-789cc703-full-v8/"
    "bounded4_cached/run.log"
)
FROZEN_REFERENCE_SHA256 = (
    "0fe931685c37695bc51c74288c67f1494a0c91a723f8e831efa0ac2a7515441c"
)
FROZEN_REFERENCE_FINGERPRINT = (
    "CG_FINGERPRINT mode=MAA elements=150000 x_raw=bb92babc1f9b29f0 "
    "z_raw=a8671e4e19f95711 x_q5=88c0975669c7062d x_q6=235baae2cde3472e "
    "z_q5=9d0c4e827a12742b z_q6=35dce54d02fd013a "
    "x_sum=-385.9469780116342 x_norm_sq=0.99999999995071809 "
    "z_sum=-1793.1550141340122 z_norm_sq=21.58640795548791 "
    "rnorm=0.0010975011901720496 zeta=109.99944232372989 "
    "nonfinite_x=0 nonfinite_z=0 result=PASS"
)

# These are policy, not fitted candidate observations.  A component must meet
# both maximum-error limits and neither count limit may be exceeded.
CRITERIA = {
    "vector": {
        "x": {
            "max_abs_error": 1.0e-5,
            "max_rel_error": 1.0e-3,
            "abs_error_count_max": 0,
            "rel_error_count_max": 0,
        },
        "z": {
            "max_abs_error": 1.0e-5,
            "max_rel_error": 1.0e-3,
            "abs_error_count_max": 0,
            "rel_error_count_max": 0,
        },
    },
    "residual": {"max_abs_error": 2.5e-7, "max_rel_error": 2.0e-3},
    "zeta": {"max_abs_error": 1.0e-8, "max_rel_error": 1.0e-10},
    "require_finite": True,
}


class OracleError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise OracleError(message)


def require_regular_file(path: Path, description: str) -> None:
    require(
        path.is_file() and not path.is_symlink(),
        f"missing regular {description}: {path}",
    )


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def load_json(path: Path, description: str) -> dict[str, Any]:
    require_regular_file(path, description)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OracleError(f"invalid {description}: {error}") from error
    require(isinstance(value, dict), f"{description} must be a JSON object")
    return value


def parse_fingerprint(log: str) -> dict[str, float]:
    lines = re.findall(r"^CG_FINGERPRINT .* result=PASS$", log, re.MULTILINE)
    require(
        len(lines) == 1,
        "host BASE log must contain exactly one passing fingerprint",
    )
    values = dict(
        item.split("=", 1) for item in lines[0].split()[1:] if "=" in item
    )
    try:
        return {"rnorm": float(values["rnorm"]), "zeta": float(values["zeta"])}
    except (KeyError, ValueError) as error:
        raise OracleError(
            "host BASE fingerprint lacks finite rnorm/zeta"
        ) from error


def relative_error(actual: float, reference: float) -> float:
    return abs(actual - reference) / max(abs(reference), 1.0e-30)


def frozen_provenance(
    repo: Path, header: Path, reference_log: Path
) -> dict[str, Any]:
    require_regular_file(header, "frozen class-C header")
    require(
        header.stat().st_size == FROZEN_HEADER_BYTES,
        "frozen class-C header byte count mismatch",
    )
    require(
        sha256(header) == FROZEN_HEADER_SHA256,
        "frozen class-C header hash mismatch",
    )
    require_regular_file(reference_log, "frozen reference log")
    require(
        sha256(reference_log) == FROZEN_REFERENCE_SHA256,
        "frozen reference log hash mismatch",
    )
    reference_text = reference_log.read_text(
        encoding="utf-8", errors="replace"
    )
    require(
        FROZEN_REFERENCE_FINGERPRINT in reference_text,
        "frozen reference identity mismatch",
    )
    source_blob = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "rev-parse",
            f"{FROZEN_SOURCE_COMMIT}:benchmarks/NAS/cg/cg.cpp",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    require(
        source_blob.returncode == 0
        and source_blob.stdout.strip() == FROZEN_SOURCE_BLOB,
        "frozen CG source identity is unavailable or mismatched",
    )
    return {
        "class": "C",
        "elements": NA,
        "header_sha256": FROZEN_HEADER_SHA256,
        "header_bytes": FROZEN_HEADER_BYTES,
        "source_commit": FROZEN_SOURCE_COMMIT,
        "source_blob": FROZEN_SOURCE_BLOB,
        "reference_log_sha256": FROZEN_REFERENCE_SHA256,
        "reference_fingerprint": FROZEN_REFERENCE_FINGERPRINT,
    }


def probe_source(frozen_source: Path) -> str:
    return f"""#include <cstdio>
#include <cstdlib>
#include <cmath>
#include <fstream>
#include <string>
#define main cg_frozen_original_main
#include "{frozen_source}"
#undef main

static void seek_to(std::ifstream &in, const char *needle) {{
  std::string matched; const size_t size = std::char_traits<char>::length(needle); char ch;
  while (in.get(ch)) {{
    if (ch == needle[matched.size()]) matched += ch; else matched.assign(ch == needle[0] ? 1 : 0, ch);
    if (matched.size() == size) return;
  }}
  std::fprintf(stderr, "missing frozen-header array: %s\\n", needle); std::exit(80);
}}
static std::string next_number(std::ifstream &in) {{
  char ch; std::string number;
  while (in.get(ch) && !(ch == '-' || ch == '+' || (ch >= '0' && ch <= '9'))) {{}}
  if (!in) {{ std::fprintf(stderr, "truncated frozen header\\n"); std::exit(81); }}
  number += ch;
  while (in.get(ch) && (ch == '+' || ch == '-' || ch == '.' || ch == 'e' || ch == 'E' || (ch >= '0' && ch <= '9'))) number += ch;
  return number;
}}
static void load_frozen_arrays(const char *path) {{
  std::ifstream in(path, std::ios::binary); if (!in) {{ std::perror(path); std::exit(82); }}
  seek_to(in, "float a[NZ] = {{");
  for (int i = 0; i < NZ; ++i) a[i] = std::strtof(next_number(in).c_str(), nullptr);
  seek_to(in, "int colidx[NZ] = {{");
  for (int i = 0; i < NZ; ++i) colidx[i] = static_cast<int>(std::strtol(next_number(in).c_str(), nullptr, 10));
  seek_to(in, "int rowstr[NA + 1] = {{");
  for (int i = 0; i <= NA; ++i) rowstr[i] = static_cast<int>(std::strtol(next_number(in).c_str(), nullptr, 10));
}}
int main() {{
  load_frozen_arrays("../input/cg_data_4C.h");
  naa = NA; nzz = NZ; firstrow = firstcol = 0; lastrow = lastcol = NA - 1;
  for (int i = 0; i < NA + 1; ++i) {{ x[i] = 1.0f; q[i] = z[i] = r[i] = p[i] = 0.0f; }}
  double rnorm = 0.0, norm_temp1 = 0.0, norm_temp2 = 0.0, zeta = 0.0;
  #pragma omp parallel num_threads(1) shared(rnorm, norm_temp1, norm_temp2, zeta)
  {{
    conj_grad_base(colidx, rowstr, x, z, a, p, q, r, &rnorm);
    #pragma omp for reduction(+ : norm_temp1, norm_temp2)
    for (int j = 0; j < NA; ++j) {{ norm_temp1 += x[j] * z[j]; norm_temp2 += z[j] * z[j]; }}
    #pragma omp single
    {{ norm_temp2 = 1.0 / std::sqrt(norm_temp2); zeta = SHIFT + 1.0 / norm_temp1; }}
    #pragma omp for
    for (int j = 0; j < NA; ++j) x[j] = norm_temp2 * z[j];
  }}
  if (!print_cg_fingerprint("BASE", x, z, NA, rnorm, zeta)) return 91;
  std::ofstream xo("x.f32le", std::ios::binary), zo("z.f32le", std::ios::binary);
  if (!xo || !zo) return 92;
  xo.write(reinterpret_cast<const char *>(x), NA * sizeof(float));
  zo.write(reinterpret_cast<const char *>(z), NA * sizeof(float));
  return (xo && zo) ? 0 : 93;
}}\n"""


def build(args: argparse.Namespace) -> None:
    repo = args.repo.resolve()
    output = args.output.resolve()
    header = args.precomputed_header.resolve()
    reference_log = args.reference_log.resolve()
    require(repo.is_dir(), f"not a repository directory: {repo}")
    require(
        not output.exists() or not any(output.iterdir()),
        f"refusing nonempty output: {output}",
    )
    provenance = frozen_provenance(repo, header, reference_log)
    output.mkdir(parents=True, exist_ok=True)
    try:
        (output / "input").mkdir()
        (output / "vectors").mkdir()
        (output / "build").mkdir()
        frozen_header = output / "input/cg_data_4C.h"
        shutil.copyfile(header, frozen_header)
        os.chmod(frozen_header, 0o444)
        # Preserve the exact frozen source blob, rather than silently using HEAD.
        frozen_source = output / "input/cg.cpp"
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "show",
                f"{FROZEN_SOURCE_COMMIT}:benchmarks/NAS/cg/cg.cpp",
            ],
            stdout=frozen_source.open("wb"),
            stderr=subprocess.PIPE,
            check=False,
        )
        require(
            result.returncode == 0, "could not materialize frozen CG source"
        )
        require(
            subprocess.run(
                ["git", "hash-object", str(frozen_source)],
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            == FROZEN_SOURCE_BLOB,
            "materialized CG source blob mismatch",
        )
        probe = output / "build/oracle_probe.cpp"
        probe.write_text(probe_source(frozen_source), encoding="utf-8")
        binary = output / "build/oracle_probe"
        command = [
            args.cxx,
            "-std=c++11",
            "-O2",
            "-DFUNC",
            "-DCG_FP_ENABLE",
            "-DCG_NA=150000",
            "-DNUM_CORES=4",
            "-DNUM_TILES_PER_CORE=8",
            "-DTILE_SIZE=16384",
            "-fopenmp",
            f"-I{repo / 'benchmarks/API'}",
            str(probe),
            "-o",
            str(binary),
        ]
        (output / "build/compile_command.json").write_text(
            json.dumps(command) + "\n"
        )
        compiled = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=args.compile_timeout,
        )
        (output / "build/compile.log").write_text(
            compiled.stdout + compiled.stderr, encoding="utf-8"
        )
        require(compiled.returncode == 0, "host BASE probe compilation failed")
        run = subprocess.run(
            [str(binary)],
            cwd=output / "vectors",
            text=True,
            capture_output=True,
            env={**os.environ, "OMP_NUM_THREADS": "1", "OMP_DYNAMIC": "FALSE"},
            timeout=args.run_timeout,
        )
        host_log = run.stdout + run.stderr
        (output / "host_base.log").write_text(host_log, encoding="utf-8")
        require(run.returncode == 0, "host BASE probe failed")
        scalars = parse_fingerprint(host_log)
        require(
            all(math.isfinite(value) for value in scalars.values()),
            "host BASE produced nonfinite scalar",
        )
        vectors: dict[str, Any] = {}
        for name in ("x", "z"):
            vector = output / f"vectors/{name}.f32le"
            require_regular_file(vector, f"host {name} vector")
            require(
                vector.stat().st_size == VECTOR_BYTES,
                f"host {name} vector byte count mismatch",
            )
            vectors[name] = {
                "path": f"vectors/{name}.f32le",
                "sha256": sha256(vector),
                "elements": NA,
                "format": "binary32-le",
            }
        document = {
            "schema": SCHEMA,
            "provenance": provenance,
            "host_execution": {
                "mode": "BASE",
                "omp_num_threads": 1,
                "compiler": args.cxx,
                "compile_command_sha256": hashlib.sha256(
                    json.dumps(command).encode()
                ).hexdigest(),
                "host_log_sha256": sha256(output / "host_base.log"),
            },
            "criteria": CRITERIA,
            "vectors": vectors,
            "scalars": scalars,
        }
        write_json(output / "golden_oracle.json", document)
        write_json(
            output / "criteria.json",
            {"schema": SCHEMA, "criteria": CRITERIA, "provenance": provenance},
        )
    except Exception:
        # Keep failing construction visibly incomplete; no gate may consume an
        # output directory without golden_oracle.json.
        raise


def safe_relative(parent: Path, value: Any, description: str) -> Path:
    require(
        isinstance(value, str) and value and not Path(value).is_absolute(),
        f"invalid {description} path",
    )
    resolved = (parent / value).resolve()
    require(
        resolved.is_relative_to(parent.resolve()),
        f"escaping {description} path",
    )
    return resolved


def vector_values(path: Path) -> Iterable[float]:
    with path.open("rb") as stream:
        while block := stream.read(4 * 16384):
            require(len(block) % 4 == 0, "truncated vector word")
            yield from struct.unpack("<" + "f" * (len(block) // 4), block)


def checked_vector(parent: Path, entry: Any, name: str) -> Path:
    require(isinstance(entry, dict), f"candidate {name} vector record missing")
    require(
        entry.get("format") == "binary32-le" and entry.get("elements") == NA,
        f"candidate {name} vector shape/format mismatch",
    )
    path = safe_relative(parent, entry.get("path"), f"candidate {name} vector")
    require_regular_file(path, f"candidate {name} vector")
    require(
        path.stat().st_size == VECTOR_BYTES,
        f"candidate {name} vector byte count mismatch",
    )
    require(
        entry.get("sha256") == sha256(path),
        f"candidate {name} vector hash mismatch",
    )
    return path


def compare_vector(
    reference: Path, candidate: Path, policy: dict[str, float | int]
) -> dict[str, float | int]:
    maximum_abs = maximum_rel = 0.0
    abs_count = rel_count = nonfinite = 0
    for expected, actual in zip(
        vector_values(reference), vector_values(candidate), strict=True
    ):
        if not math.isfinite(expected) or not math.isfinite(actual):
            nonfinite += 1
            continue
        absolute = abs(actual - expected)
        relative = absolute / max(abs(expected), 1.0e-30)
        maximum_abs, maximum_rel = max(maximum_abs, absolute), max(
            maximum_rel, relative
        )
        abs_count += absolute > float(policy["max_abs_error"])
        rel_count += relative > float(policy["max_rel_error"])
    require(nonfinite == 0, "candidate contains nonfinite vector values")
    require(
        maximum_abs <= float(policy["max_abs_error"]),
        "candidate maximum absolute vector error exceeds policy",
    )
    require(
        maximum_rel <= float(policy["max_rel_error"]),
        "candidate maximum relative vector error exceeds policy",
    )
    require(
        abs_count <= int(policy["abs_error_count_max"]),
        "candidate absolute vector error count exceeds policy",
    )
    require(
        rel_count <= int(policy["rel_error_count_max"]),
        "candidate relative vector error count exceeds policy",
    )
    return {
        "max_abs_error": maximum_abs,
        "max_rel_error": maximum_rel,
        "abs_error_count": abs_count,
        "rel_error_count": rel_count,
    }


def verify(args: argparse.Namespace) -> None:
    golden_path = args.golden_oracle.resolve()
    golden = load_json(golden_path, "golden oracle")
    require(golden.get("schema") == SCHEMA, "unsupported golden oracle schema")
    require(
        golden.get("criteria") == CRITERIA,
        "golden oracle criteria differ from predeclared policy",
    )
    candidate_path = args.candidate_manifest.resolve()
    candidate = load_json(candidate_path, "candidate vector manifest")
    require(
        candidate.get("schema") == CANDIDATE_SCHEMA,
        "unsupported candidate vector schema",
    )
    require(
        candidate.get("provenance") == golden.get("provenance"),
        "candidate provenance does not exactly match golden oracle",
    )
    require(
        isinstance(candidate.get("vectors"), dict), "candidate vectors missing"
    )
    require(
        isinstance(candidate.get("scalars"), dict), "candidate scalars missing"
    )
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "golden_oracle_sha256": sha256(golden_path),
        "result": "PASS",
        "vectors": {},
    }
    for name in ("x", "z"):
        reference = safe_relative(
            golden_path.parent,
            golden["vectors"][name]["path"],
            f"golden {name} vector",
        )
        require(
            sha256(reference) == golden["vectors"][name]["sha256"],
            f"golden {name} vector hash mismatch",
        )
        candidate_vector = checked_vector(
            candidate_path.parent, candidate["vectors"].get(name), name
        )
        result["vectors"][name] = compare_vector(
            reference, candidate_vector, CRITERIA["vector"][name]
        )
    for scalar in ("rnorm", "zeta"):
        actual, expected = candidate["scalars"].get(scalar), golden[
            "scalars"
        ].get(scalar)
        require(
            isinstance(actual, (int, float)) and math.isfinite(actual),
            f"candidate {scalar} missing/nonfinite",
        )
        absolute, relative = abs(actual - expected), relative_error(
            actual, expected
        )
        policy = CRITERIA["residual" if scalar == "rnorm" else "zeta"]
        require(
            absolute <= policy["max_abs_error"]
            and relative <= policy["max_rel_error"],
            f"candidate {scalar} exceeds predeclared criterion",
        )
        result[scalar] = {"abs_error": absolute, "rel_error": relative}
    output = args.output.resolve()
    require(
        not output.exists(), f"refusing existing verification output: {output}"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, result)


def parser() -> argparse.ArgumentParser:
    tool = argparse.ArgumentParser(description=__doc__)
    commands = tool.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser(
        "build", help="build immutable host BASE golden vectors"
    )
    build_parser.add_argument("--repo", type=Path, required=True)
    build_parser.add_argument("--precomputed-header", type=Path, required=True)
    build_parser.add_argument(
        "--reference-log", type=Path, default=Path(FROZEN_REFERENCE_LOG)
    )
    build_parser.add_argument("--output", type=Path, required=True)
    build_parser.add_argument("--cxx", default="g++")
    build_parser.add_argument("--compile-timeout", type=int, default=1800)
    build_parser.add_argument("--run-timeout", type=int, default=1800)
    build_parser.set_defaults(func=build)
    verify_parser = commands.add_parser(
        "verify", help="fail-closed candidate vector gate"
    )
    verify_parser.add_argument("--golden-oracle", type=Path, required=True)
    verify_parser.add_argument(
        "--candidate-manifest", type=Path, required=True
    )
    verify_parser.add_argument("--output", type=Path, required=True)
    verify_parser.set_defaults(func=verify)
    return tool


def main() -> int:
    args = parser().parse_args()
    try:
        args.func(args)
    except (
        OracleError,
        OSError,
        subprocess.SubprocessError,
        TimeoutError,
    ) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
