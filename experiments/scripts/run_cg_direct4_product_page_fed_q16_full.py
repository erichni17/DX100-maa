#!/usr/bin/env python3
"""Run one evidence-grade full direct4-product/page-fed-q16 CG candidate.

This runner creates one deferred checkpoint and restores it exactly once for
the direct4_product_page_fed_q16 treatment.  It never invokes a native,
predecessor, or page-fed control arm.  The tolerant successor certificate is
the sole numerical-policy authority; performance is emitted only after every
terminal, numerical, mechanism, provenance, and immutability check passes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
SOURCE_BASE_COMMIT = "19223ae642d602751a301843742ebb5c4d025406"
SOURCE = ROOT / "benchmarks/NAS/cg/cg.cpp"
CONFIG = ROOT / "configs/deprecated/example/se.py"
RAMULATOR_CONFIG = ROOT / "ext/ramulator2/ramulator2/example_gem5_config.yaml"
GEM5 = Path(
    "/data1/nier/dx100-binaries/"
    "gem5-page-fed-606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427.opt"
)
GEM5_SHA256 = (
    "606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427"
)
RAMULATOR = Path(
    "/data1/nier/dx100-runs/2026-08-12-hybrid-line-handoff-8a5c7712/"
    "input/libramulator.so"
)
RAMULATOR_SHA256 = (
    "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753"
)
CONTROL_ROOT = Path(
    "/data1/nier/dx100-runs/"
    "2026-08-25-cg-page-fed-application-full-31c00be8-r2"
)
CONTROL_SIMTICKS = 715_387_684_015
FROZEN_HEADER = CONTROL_ROOT / "input/cg_data_4C.h"
FROZEN_HEADER_SHA256 = (
    "f2b18716e4a2356c597c95ee3583549def72700f2cb3294b0fcaacca46dbe131"
)
FROZEN_HEADER_BYTES = 992_830_458
CERTIFICATE_ROOT = Path(
    "/data1/nier/dx100-runs/" "2026-08-26-cg-page-fed-full-reclassification-r1"
)
CERTIFICATE_FILES = {
    "manifest.json": (
        "42ef48cdbf5c04c13d7116d070dfc008867fd55c35db4254c60fb1b753927ee6"
    ),
    "certificate.json": (
        "cd78f8f252ea1e52672c8357044f38a5fa969192c460a1aa9fc4fb2d2090649a"
    ),
    "gate.complete": (
        "8382a8b2f856e109b8d97249ec27edf1d9a0c7d2f6fd349eef8aa3b8d1c47aaf"
    ),
}
NATIVE_LOG = Path(
    "/data1/nier/dx100-runs/2026-08-11-cg-bounded-789cc703-full-v8/"
    "native16/run.log"
)
NATIVE_LOG_SHA256 = (
    "99c08fcbe3b121a61db866af4a4aa926b0eaddf87ad516a944784b496404ca73"
)
NATIVE_STATS = NATIVE_LOG.parent / "run/stats.txt"
NATIVE_STATS_SHA256 = (
    "4122577993c17760b86462bb2bfcb1d87b7d33cf2e3f30a003139f586c0cc070"
)
TREATMENT = "direct4_product_page_fed_q16"
CG_NA = 150_000
EXPECTED_WINDOWS = 10_960
EXPECTED_Q_WINDOWS = 8_768
EXPECTED_RESIDUAL_WINDOWS = 2_192
EXPECTED_PAGES = 43_840
EXPECTED_WORDS = 179_568_640
EXPECTED_PUBLISH_LINES = 11_223_040
RELATIVE_BOUNDS = {
    "x_sum": 1.0e-8,
    "x_norm_sq": 1.0e-8,
    "z_sum": 1.0e-8,
    "z_norm_sq": 1.0e-8,
    "rnorm": 1.0e-3,
    "zeta": 1.0e-10,
}
RELATIVE_BOUND_TEXT = {
    "x_sum": "1e-8",
    "x_norm_sq": "1e-8",
    "z_sum": "1e-8",
    "z_norm_sq": "1e-8",
    "rnorm": "1e-3",
    "zeta": "1e-10",
}
FATAL_RE = re.compile(
    r"panic|fatal|assert|abort|segmentation fault|error:", re.IGNORECASE
)
GUEST_COMPILE_INPUTS = (
    SOURCE,
    ROOT / "benchmarks/API/MAA.hpp",
    ROOT / "benchmarks/API/MAA_gem5.hpp",
    ROOT / "benchmarks/API/MAA_virtual_materialize.hpp",
    ROOT / "include/gem5/m5ops.h",
    ROOT / "include/gem5/asm/generic/m5ops.h",
    ROOT / "include/gem5/maa_logical_spd_cache_abi.hh",
    ROOT / "include/gem5/maa_page_fed_soa_abi.hh",
    ROOT / "util/m5/src/abi/x86/m5op.S",
)
CONFIG_INPUTS = (
    CONFIG,
    RAMULATOR_CONFIG,
    ROOT / "configs/common/Options.py",
    ROOT / "configs/common/Simulation.py",
    ROOT / "configs/common/CacheConfig.py",
    ROOT / "configs/common/MemConfig.py",
    ROOT / "configs/common/MAAConfig.py",
    ROOT / "configs/common/MAA.py",
)


class GateError(RuntimeError):
    """A fail-closed evidence gate rejected the run."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def exact_hash(path: Path, expected: str, description: str) -> None:
    require(path.is_file(), f"missing {description}: {path}")
    require(sha256_file(path) == expected, f"hash mismatch for {description}")


def source_status() -> str:
    return subprocess.check_output(
        ["git", "status", "--short", "--branch"], cwd=ROOT, text=True
    )


def source_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def validate_source_base() -> None:
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", SOURCE_BASE_COMMIT, "HEAD"],
        cwd=ROOT,
        check=False,
    )
    require(completed.returncode == 0, "HEAD is not based on 19223ae6")


def parse_kv(line: str) -> dict[str, str]:
    return dict(re.findall(r"([A-Za-z0-9_]+)=([^ ]+)", line))


def exactly_one(lines: list[str], expression: str, description: str) -> str:
    regex = re.compile(expression)
    matches = [line for line in lines if regex.search(line)]
    require(
        len(matches) == 1, f"expected one {description}, saw {len(matches)}"
    )
    return matches[0]


def tree_ledger(root: Path) -> str:
    records = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            records.append(f"{sha256_file(path)}  {path.relative_to(root)}")
    require(bool(records), f"empty tree ledger for {root}")
    return "\n".join(records) + "\n"


def artifact_ledger(paths: Iterable[Path]) -> str:
    return "".join(f"{sha256_file(path)}  {path}\n" for path in paths)


def atomic_write(path: Path, contents: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    require(not temporary.exists(), f"stale temporary output: {temporary}")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def run_logged(
    command: list[str], log: Path, environment: dict[str, str]
) -> None:
    with log.open("w", encoding="utf-8") as output:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=False,
        )
    log.with_suffix(log.suffix + ".exit").write_text(
        f"{completed.returncode}\n", encoding="utf-8"
    )
    require(completed.returncode == 0, f"command failed; see {log}")


def validate_certificate() -> dict[str, object]:
    for name, digest in CERTIFICATE_FILES.items():
        exact_hash(CERTIFICATE_ROOT / name, digest, f"certificate {name}")
    gate = (CERTIFICATE_ROOT / "gate.complete").read_text(encoding="utf-8")
    require(
        gate == "PASS_NUMERICAL_MECHANISM_CORRECT\n"
        "manifest_sha256=" + CERTIFICATE_FILES["manifest.json"] + "\n"
        "certificate_sha256=" + CERTIFICATE_FILES["certificate.json"] + "\n"
        "input_sha256=066b423ac13e01e6c3dd4b35f8b6e00d562960cce0b283206405b8424acd6fa5\n",
        "certificate gate contents changed",
    )
    manifest = json.loads(
        (CERTIFICATE_ROOT / "manifest.json").read_text(encoding="utf-8")
    )
    certificate = json.loads(
        (CERTIFICATE_ROOT / "certificate.json").read_text(encoding="utf-8")
    )
    require(
        manifest.get("scalar_relative_tolerances") == RELATIVE_BOUND_TEXT,
        "certificate numerical limits changed",
    )
    require(
        certificate.get("verdict") == "PASS_NUMERICAL_MECHANISM_CORRECT",
        "certificate verdict is not PASS",
    )
    require(
        certificate.get("raw_or_quantized_exact") is False,
        "certificate unexpectedly claims raw/quantized equality",
    )
    require(
        certificate.get("official_nas_verification") is False,
        "certificate unexpectedly claims official NAS verification",
    )
    require(
        certificate.get("observations_per_full_configuration") == 1,
        "certificate observation count changed",
    )
    pinned = manifest.get("pinned_sha256", {})
    require(
        isinstance(pinned, dict)
        and pinned.get("native16_log") == NATIVE_LOG_SHA256
        and pinned.get("native16_stats") == NATIVE_STATS_SHA256,
        "certificate native16 pins changed",
    )
    roots = manifest.get("roots", {})
    require(
        isinstance(roots, dict)
        and roots.get("candidate") == str(CONTROL_ROOT),
        "certificate control root changed",
    )
    ticks = manifest.get("simTicks", {})
    require(
        isinstance(ticks, dict) and ticks.get("candidate") == CONTROL_SIMTICKS,
        "certificate control simTicks changed",
    )
    return {
        "root": str(CERTIFICATE_ROOT),
        "manifest_sha256": CERTIFICATE_FILES["manifest.json"],
        "certificate_sha256": CERTIFICATE_FILES["certificate.json"],
        "gate_sha256": CERTIFICATE_FILES["gate.complete"],
        "verdict": certificate["verdict"],
        "relative_bounds": RELATIVE_BOUND_TEXT,
    }


def validate_config(config: Path) -> None:
    lines = config.read_text(errors="replace").splitlines()
    required = {
        "page_fed_soa_jit=true",
        "num_maas=1",
        "num_indirect_units_per_maa=4",
        "num_tiles_per_core=8",
        "num_tile_elements=16384",
        "physical_tile_elements=4096",
        "num_offset_table_entries=16384",
        "num_offset_table_epoch_entries=16384",
        "num_initial_row_table_slices=32",
        "soa_jit_predicate_active_credits=16",
        "soa_jit_active_value_owners=32",
    }
    require(not required.difference(lines), "resolved configuration mismatch")
    require(
        sum(line == "num_tiles_per_core=8" for line in lines) == 1,
        "resolved configuration does not contain exactly one eight-tile knob",
    )
    require(
        sum(
            bool(re.fullmatch(r"\[system\.mem_ctrls[01]\]", line))
            for line in lines
        )
        == 2,
        "resolved configuration does not contain exactly two memory channels",
    )


def validate_terminal(fields: dict[str, str]) -> dict[str, int]:
    expected = {
        "full_windows": EXPECTED_WINDOWS,
        "staged_index_words": EXPECTED_WORDS,
        "staged_value_words": 0,
        "product_words": EXPECTED_WORDS,
        "index_publish_pages": 0,
        "value_publish_pages": 0,
        "product_publish_pages": EXPECTED_PAGES,
        "logical_alu_vectors": 0,
        "physical_alu_vectors": EXPECTED_PAGES,
        "logical_page_windows": 0,
        "physical_page_product_windows": 0,
        "page_fed_product_windows": 0,
        "direct4_product_page_fed_q16_windows": EXPECTED_WINDOWS,
        "virtual_p_gather_windows": 0,
        "physical_p_gather_pages": EXPECTED_PAGES,
        "page_fed_admit_pages": EXPECTED_PAGES,
        "page_fed_closes": EXPECTED_WINDOWS,
        "q_spmv_eligible_windows": EXPECTED_Q_WINDOWS,
        "q_spmv_routed_windows": EXPECTED_Q_WINDOWS,
        "residual_spmv_eligible_windows": EXPECTED_RESIDUAL_WINDOWS,
        "residual_spmv_routed_windows": EXPECTED_RESIDUAL_WINDOWS,
        "external_coherent_backing_bytes": 262_144,
        "physical_spd_payload_bytes": 524_288,
        "logical_scheduler_reserved_lanes": 0,
        "logical_scheduler_reserved_lane_payload_bytes": 0,
        "host_payload_access": 0,
        "coherent_index_backing_bytes": 0,
        "virtual_p_backing_bytes": 0,
        "virtual_backing_traffic_eliminated": 1,
        "p16_reorder_preserved": 0,
        "q16_reorder_preserved": 1,
    }
    try:
        actual = {key: int(fields[key]) for key in expected}
    except (KeyError, ValueError) as error:
        raise GateError(f"incomplete direct4 terminal: {error}") from error
    require(actual == expected, f"direct4 terminal mismatch: {actual}")
    exact_text = {
        "treatment": TREATMENT,
        "slice": "all_spmv_full_windows",
        "producer": "direct4_physical_p_gather_product_publish_then_q16",
        "p_gather_mode": "physical_4k_direct",
        "performance_promotable": "0",
        "result": "PASS",
    }
    require(
        all(fields.get(key) == value for key, value in exact_text.items()),
        "direct4 terminal text fields mismatch",
    )
    return actual


def first_stat_sum(stats: Path, suffix: str) -> int:
    section = 0
    total = 0
    found = False
    for line in stats.read_text(errors="replace").splitlines():
        if line.startswith("---------- Begin Simulation Statistics"):
            section += 1
            continue
        if section == 1 and line.startswith(
            "---------- End Simulation Statistics"
        ):
            break
        fields = line.split()
        if (
            section == 1
            and len(fields) >= 2
            and (fields[0] == suffix or fields[0].endswith("_" + suffix))
        ):
            total += int(float(fields[1]))
            found = True
    require(found, f"missing first-ROI stat {suffix}")
    return total


STAT_NAMES = (
    "simTicks",
    "IND_SoaJitInstructions",
    "IND_SoaJitTerminalCompletions",
    "IND_SoaJitSelected",
    "IND_SoaJitAliasesApplied",
    "IND_SoaJitPredicateRejected",
    "IND_SoaJitValueReadIssues",
    "IND_SoaJitValueReadResponses",
    "IND_SoaJitValueFills",
    "IND_SoaJitValueCachedResponses",
    "IND_SoaJitValueHits",
    "IND_SoaJitValueMergedWaiters",
    "IND_SoaJitValueDeliveries",
    "IND_SoaJitAReadIssues",
    "IND_SoaJitAReadResponses",
    "IND_SoaJitAWriteIssues",
    "IND_SoaJitAWriteResponses",
    "IND_SoaJitPageFedOperations",
    "IND_SoaJitPageFedAdmitCommands",
    "IND_SoaJitPageFedCloseCommands",
    "IND_SoaJitPageFedCommandResponses",
    "IND_SoaJitPageFedAdmittedWords",
    "IND_SoaJitPageFedSpdIndexReads",
    "IND_SoaJitPageFedRowWrites",
    "IND_SoaJitPageFedCoherentIndexReadLines",
    "IND_SoaJitPageFedCoherentIndexWriteLines",
    "IND_SoaJitPageFedStateByteOperations",
    "IND_SoaJitEpochDrains",
    "IND_BoundedGlobalMergeFallbacks",
    "STR_PublishIssues",
    "STR_PublishAccepts",
    "STR_PublishWriteResponses",
    "STR_PublishTerminals",
)


def validate_stats_values(values: dict[str, int]) -> None:
    require(values["simTicks"] > 0, "first-ROI simTicks is not positive")
    exact = {
        "IND_SoaJitInstructions": EXPECTED_WINDOWS,
        "IND_SoaJitTerminalCompletions": EXPECTED_WINDOWS,
        "IND_SoaJitSelected": EXPECTED_WORDS,
        "IND_SoaJitAliasesApplied": EXPECTED_WORDS,
        "IND_SoaJitPredicateRejected": 0,
        "IND_SoaJitValueDeliveries": EXPECTED_WORDS,
        "IND_SoaJitPageFedOperations": EXPECTED_WINDOWS,
        "IND_SoaJitPageFedAdmitCommands": EXPECTED_PAGES,
        "IND_SoaJitPageFedCloseCommands": EXPECTED_WINDOWS,
        "IND_SoaJitPageFedCommandResponses": EXPECTED_PAGES + EXPECTED_WINDOWS,
        "IND_SoaJitPageFedAdmittedWords": EXPECTED_WORDS,
        "IND_SoaJitPageFedSpdIndexReads": EXPECTED_WORDS,
        "IND_SoaJitPageFedRowWrites": EXPECTED_WORDS,
        "IND_SoaJitPageFedCoherentIndexReadLines": 0,
        "IND_SoaJitPageFedCoherentIndexWriteLines": 0,
        "IND_SoaJitPageFedStateByteOperations": EXPECTED_WINDOWS * 16,
        "IND_SoaJitEpochDrains": 0,
        "IND_BoundedGlobalMergeFallbacks": 0,
        "STR_PublishIssues": EXPECTED_PUBLISH_LINES,
        "STR_PublishAccepts": EXPECTED_PUBLISH_LINES,
        "STR_PublishWriteResponses": EXPECTED_PUBLISH_LINES,
        "STR_PublishTerminals": EXPECTED_PAGES,
    }
    require(
        all(values.get(key) == value for key, value in exact.items()),
        "exact SoA/page-fed/publisher mechanism closure failed",
    )
    issues = values["IND_SoaJitValueReadIssues"]
    responses = values["IND_SoaJitValueReadResponses"]
    fills = values["IND_SoaJitValueFills"]
    cached = values["IND_SoaJitValueCachedResponses"]
    hits = values["IND_SoaJitValueHits"]
    merged = values["IND_SoaJitValueMergedWaiters"]
    deliveries = values["IND_SoaJitValueDeliveries"]
    require(
        issues > 0
        and responses > 0
        and fills > 0
        and issues == responses == fills == cached,
        "value issue/response/fill closure failed",
    )
    require(
        hits >= 0 and merged >= 0 and issues + hits + merged == deliveries,
        "value hit/merge/delivery closure failed",
    )
    a_values = [
        values["IND_SoaJitAReadIssues"],
        values["IND_SoaJitAReadResponses"],
        values["IND_SoaJitAWriteIssues"],
        values["IND_SoaJitAWriteResponses"],
    ]
    require(
        a_values[0] > 0 and len(set(a_values)) == 1, "A-line closure failed"
    )
    require(
        values["IND_SoaJitInstructions"]
        - values["IND_SoaJitTerminalCompletions"]
        == 0,
        "open SoA contexts remain",
    )


def validate_stats(stats: Path) -> dict[str, int]:
    require(
        stats.is_file() and stats.stat().st_size > 0, "missing final stats"
    )
    values = {name: first_stat_sum(stats, name) for name in STAT_NAMES}
    validate_stats_values(values)
    return values


def fingerprint_fields(log: Path) -> tuple[str, dict[str, str]]:
    lines = log.read_text(errors="replace").splitlines()
    line = exactly_one(
        lines,
        rf"^CG_FINGERPRINT mode=MAA elements={CG_NA} .* result=PASS$",
        "passing full-CG fingerprint",
    )
    return line, parse_kv(line)


def relative_delta(candidate: str, reference: str) -> float:
    candidate_value = float(candidate)
    reference_value = float(reference)
    require(
        math.isfinite(candidate_value) and math.isfinite(reference_value),
        "nonfinite scalar fingerprint field",
    )
    denominator = max(abs(reference_value), 1.0e-300)
    return abs(candidate_value - reference_value) / denominator


def validate_numerical(
    candidate: dict[str, str], reference: dict[str, str]
) -> dict[str, float]:
    for fields in (candidate, reference):
        require(
            fields.get("result") == "PASS", "project-local fingerprint failed"
        )
        require(fields.get("nonfinite_x") == "0", "nonfinite x vector")
        require(fields.get("nonfinite_z") == "0", "nonfinite z vector")
    deltas: dict[str, float] = {}
    for field, bound in RELATIVE_BOUNDS.items():
        try:
            delta = relative_delta(candidate[field], reference[field])
        except KeyError as error:
            raise GateError(
                f"missing scalar fingerprint field {field}"
            ) from error
        require(
            delta <= bound, f"{field} relative delta {delta} exceeds {bound}"
        )
        deltas[field] = delta
    return deltas


def checkpoint_command(
    guest: Path, selector: Path, checkpoint: Path
) -> list[str]:
    return [
        str(GEM5),
        "--listener-mode=off",
        f"--outdir={checkpoint}",
        str(CONFIG),
        "--cpu-type",
        "AtomicSimpleCPU",
        "-n",
        "4",
        "--mem-size",
        "2GB",
        "--max-checkpoints=1",
        "--cmd",
        str(guest),
        "--options",
        f"MAA_DEFERRED {selector}",
    ]


def restore_command(
    guest: Path, selector: Path, checkpoint: Path, run: Path
) -> list[str]:
    return [
        str(GEM5),
        "--listener-mode=off",
        f"--outdir={run}",
        str(CONFIG),
        "--cpu-type",
        "X86O3CPU",
        "-r",
        "1",
        "-n",
        "4",
        "--mem-size",
        "2GB",
        "--checkpoint-dir",
        str(checkpoint),
        "--sys-clock",
        "3.2GHz",
        "--cpu-clock",
        "3.2GHz",
        "--caches",
        "--l1d_size=32kB",
        "--l1d_assoc=8",
        "--l1d_mshrs=16",
        "--l1d_write_buffers=8",
        "--l1i_size=32kB",
        "--l1i_assoc=8",
        "--l1i_mshrs=16",
        "--l1i_write_buffers=8",
        "--l2cache",
        "--l2_size=256kB",
        "--l2_assoc=4",
        "--l2_mshrs=32",
        "--l2_write_buffers=16",
        "--l3cache",
        "--l3_size=8MB",
        "--l3_assoc=16",
        "--l3_mshrs=256",
        "--l3_write_buffers=128",
        "--l3_ports=4",
        "--cacheline_size=64",
        "--mem-type",
        "Ramulator2",
        "--ramulator-config",
        str(RAMULATOR_CONFIG),
        "--mem-channels=2",
        "--maa",
        "--maa_num_maas=1",
        "--maa_num_indirect_units_per_maa=4",
        "--maa_num_tiles_per_core=8",
        "--maa_num_tile_elements=16384",
        "--maa_physical_tile_elements=4096",
        "--maa_num_offset_table_entries=16384",
        "--maa_num_offset_table_epoch_entries=16384",
        "--maa_num_initial_row_table_slices=32",
        "--maa_page_fed_soa_jit",
        "--maa_soa_jit_predicate_active_credits=16",
        "--maa_soa_jit_active_value_owners=32",
        "--cmd",
        str(guest),
        "--options",
        f"MAA_DEFERRED {selector}",
    ]


def compile_command(guest: Path, input_dir: Path) -> list[str]:
    return [
        os.environ.get("CXX", "g++"),
        f"-I{ROOT / 'benchmarks/API'}",
        f"-I{ROOT / 'include'}",
        f"-I{ROOT / 'util/m5/src'}",
        f"-I{input_dir}",
        "-std=c++11",
        "-O3",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wno-ignored-qualifiers",
        "-Wno-unused-parameter",
        "-Wno-unused-function",
        "-fopenmp",
        "-DGEM5",
        "-DMAA",
        "-DMAA_VIRTUAL_GATHER",
        "-DMAA_GENERAL_VIRTUAL_CONSUMER",
        "-DMAA_CONSUMER_TILE_SIZE=4096",
        "-DCG_LOGICAL16_RMW",
        "-DCG_LOGICAL_PAGE_RMW",
        "-DCG_PHYSICAL_PAGE_PRODUCT_ONLY",
        "-DCG_PAGE_FED_SOA_ONLY",
        "-DCG_FP_ENABLE",
        "-DUSE_DATA_FROM_FILE",
        "-DCG_NA=150000",
        "-DNUM_CORES=4",
        "-DNUM_TILES_PER_CORE=8",
        "-DTILE_SIZE=16384",
        "-DMAA_MEM_SIZE=0x80000000",
        str(ROOT / "util/m5/src/abi/x86/m5op.S"),
        str(SOURCE),
        "-o",
        str(guest),
    ]


def validate_restore(
    run: Path, native_fields: dict[str, str]
) -> tuple[dict[str, object], dict[str, float]]:
    log = run / "restore.log"
    lines = log.read_text(errors="replace").splitlines()
    require(
        not any(FATAL_RE.search(line) for line in lines), "fatal restore text"
    )
    exactly_one(
        lines,
        r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$",
        "m5 terminal",
    )
    require(
        sum(line == "ROI End!!!" for line in lines) == 1, "ROI did not close"
    )
    terminal_line = exactly_one(
        lines,
        rf"^CG_LOGICAL16_RMW_TERMINAL treatment={TREATMENT} .* result=PASS$",
        "direct4 terminal",
    )
    terminal = validate_terminal(parse_kv(terminal_line))
    validate_config(run / "config.ini")
    stats = validate_stats(run / "stats.txt")
    _, candidate_fields = fingerprint_fields(log)
    deltas = validate_numerical(candidate_fields, native_fields)
    require(
        not any(
            path.name != "restore.log" for path in run.glob("*trace*.log")
        ),
        "per-access trace artifact is forbidden",
    )
    return {
        "terminal": terminal,
        "terminal_line": terminal_line,
        "fingerprint": candidate_fields,
        "stats": stats,
    }, deltas


def write_result_and_gate(
    out: Path,
    result: dict[str, object],
    certified_ledger: str,
) -> None:
    """Seal terminal outputs only after the caller has completed every gate."""
    result_text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    atomic_write(out / "result.json", result_text)
    result_sha = sha256_file(out / "result.json")
    atomic_write(out / "certified_artifacts.sha256", certified_ledger)
    ledger_sha = sha256_file(out / "certified_artifacts.sha256")
    atomic_write(
        out / "gate.complete",
        "PASS_NUMERICAL_MECHANISM_CORRECT\n"
        "observations=1\n"
        f"result_sha256={result_sha}\n"
        f"certified_artifacts_sha256={ledger_sha}\n",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out = args.out.resolve()
    if out == ROOT or ROOT in out.parents:
        raise SystemExit("output must be outside the source worktree")
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"refusing nonempty output: {out}")

    validate_source_base()
    exact_hash(GEM5, GEM5_SHA256, "archived page-fed gem5")
    exact_hash(RAMULATOR, RAMULATOR_SHA256, "frozen Ramulator")
    exact_hash(FROZEN_HEADER, FROZEN_HEADER_SHA256, "precomputed CG header")
    require(
        FROZEN_HEADER.stat().st_size == FROZEN_HEADER_BYTES,
        "header size mismatch",
    )
    exact_hash(NATIVE_LOG, NATIVE_LOG_SHA256, "native16 numerical log")
    exact_hash(NATIVE_STATS, NATIVE_STATS_SHA256, "native16 stats")
    certificate_identity = validate_certificate()
    before_status = source_status()
    require(
        len(before_status.splitlines()) == 1,
        "refusing candidate evidence from a dirty source worktree",
    )
    before_commit = source_commit()

    input_dir = out / "input"
    bin_dir = out / "bin"
    checkpoint = out / "checkpoint"
    run = out / "run"
    for directory in (input_dir, bin_dir, checkpoint, run):
        directory.mkdir(parents=True, exist_ok=False)
    selector = input_dir / "direct4_product_page_fed_q16.selector"
    selector.write_text(f"token_stream_ld {TREATMENT}\n", encoding="utf-8")
    selector.chmod(0o444)
    header = input_dir / "cg_data_4C.h"
    subprocess.run(
        ["cp", "--reflink=auto", str(FROZEN_HEADER), str(header)], check=True
    )
    header.chmod(0o444)
    exact_hash(header, FROZEN_HEADER_SHA256, "copied precomputed CG header")
    require(
        header.stat().st_size == FROZEN_HEADER_BYTES,
        "copied header size mismatch",
    )

    guest = bin_dir / "cg_direct4_product_page_fed_q16_full"
    compile_args = compile_command(guest, input_dir)
    checkpoint_args = checkpoint_command(guest, selector, checkpoint)
    restore_args = restore_command(guest, selector, checkpoint, run)
    subprocess.run(compile_args, cwd=ROOT, check=True)

    library_path = str(RAMULATOR.parent)
    if os.environ.get("LD_LIBRARY_PATH"):
        library_path += ":" + os.environ["LD_LIBRARY_PATH"]
    environment = dict(
        os.environ,
        LD_LIBRARY_PATH=library_path,
        OMP_NUM_THREADS="4",
        OMP_PROC_BIND="false",
    )
    ldd_output = subprocess.check_output(
        ["ldd", str(GEM5)], env=environment, text=True
    )
    match = re.search(r"^[ \t]*libramulator\.so => (\S+)", ldd_output, re.M)
    require(
        match is not None
        and Path(match.group(1)).resolve() == RAMULATOR.resolve(),
        "archived gem5 did not resolve frozen Ramulator",
    )

    immutable_artifacts = (
        GEM5,
        RAMULATOR,
        guest,
        selector,
        header,
        NATIVE_LOG,
        NATIVE_STATS,
        *(CERTIFICATE_ROOT / name for name in sorted(CERTIFICATE_FILES)),
        Path(__file__).resolve(),
        *GUEST_COMPILE_INPUTS,
        *CONFIG_INPUTS,
    )
    artifacts_before = artifact_ledger(immutable_artifacts)
    (input_dir / "artifact_sha256.before").write_text(
        artifacts_before, encoding="utf-8"
    )
    (input_dir / "source_status.before").write_text(
        before_status, encoding="utf-8"
    )
    (input_dir / "source_commit.before").write_text(
        before_commit + "\n", encoding="utf-8"
    )
    (input_dir / "compile_command.json").write_text(
        json.dumps(compile_args, indent=2) + "\n", encoding="utf-8"
    )
    (input_dir / "checkpoint_command.json").write_text(
        json.dumps(checkpoint_args, indent=2) + "\n", encoding="utf-8"
    )
    (input_dir / "restore_command.json").write_text(
        json.dumps(restore_args, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {
        "schema": "dx100.cg.direct4_product_page_fed_q16_full.v1",
        "terminal": False,
        "candidate_only": True,
        "guest_runs": 1,
        "native_runs": 0,
        "physical_predecessor_runs": 0,
        "page_fed_control_runs": 0,
        "trace": "disabled",
        "timeout": "none",
        "source_base_commit": SOURCE_BASE_COMMIT,
        "source_commit": before_commit,
        "cg_na": CG_NA,
        "selector": TREATMENT,
        "geometry": {
            "cores": 4,
            "tiles_per_core": 8,
            "logical_tile_elements": 16384,
            "physical_tile_elements": 4096,
            "physical_spd_payload_bytes": 524288,
            "external_coherent_backing_bytes": 262144,
        },
        "precomputed_header": {
            "source": str(FROZEN_HEADER),
            "sha256": FROZEN_HEADER_SHA256,
            "bytes": FROZEN_HEADER_BYTES,
        },
        "control": {"root": str(CONTROL_ROOT), "simTicks": CONTROL_SIMTICKS},
        "certificate": certificate_identity,
        "commands": {
            "compile": compile_args,
            "checkpoint": checkpoint_args,
            "restore": restore_args,
        },
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # Simulator execution begins only after all identities, commands, and the
    # complete immutable-artifact ledger have been recorded above.
    run_logged(checkpoint_args, out / "checkpoint.log", environment)
    checkpoint_lines = (
        (out / "checkpoint.log").read_text(errors="replace").splitlines()
    )
    exactly_one(
        checkpoint_lines,
        r"^Exiting @ tick [0-9]+ because checkpoint$",
        "checkpoint terminal",
    )
    require(
        not any(
            line.startswith(
                ("CG_FINGERPRINT ", "CG_LOGICAL16_RMW_TERMINAL ", "ROI End!!!")
            )
            for line in checkpoint_lines
        ),
        "checkpoint crossed deferred candidate boundary",
    )
    checkpoint_before = tree_ledger(checkpoint)
    (input_dir / "checkpoint.files.sha256.before").write_text(
        checkpoint_before, encoding="utf-8"
    )

    run_logged(restore_args, run / "restore.log", environment)

    # Terminal/process, checkpoint, source, certificate, numerical, and
    # mechanism validation all precede result.json and gate.complete.
    require(
        (run / "restore.log.exit").read_text(encoding="utf-8").strip() == "0",
        "restore wrapper exit is not zero",
    )
    checkpoint_after = tree_ledger(checkpoint)
    (input_dir / "checkpoint.files.sha256.after").write_text(
        checkpoint_after, encoding="utf-8"
    )
    require(
        checkpoint_after == checkpoint_before,
        "checkpoint changed during restore",
    )
    artifacts_after = artifact_ledger(immutable_artifacts)
    (input_dir / "artifact_sha256.after").write_text(
        artifacts_after, encoding="utf-8"
    )
    require(artifacts_after == artifacts_before, "immutable artifact changed")
    after_status = source_status()
    after_commit = source_commit()
    (input_dir / "source_status.after").write_text(
        after_status, encoding="utf-8"
    )
    (input_dir / "source_commit.after").write_text(
        after_commit + "\n", encoding="utf-8"
    )
    require(after_status == before_status, "source status changed during run")
    require(after_commit == before_commit, "source commit changed during run")
    validate_certificate()
    exact_hash(
        FROZEN_HEADER, FROZEN_HEADER_SHA256, "precomputed CG header after run"
    )
    exact_hash(
        NATIVE_LOG, NATIVE_LOG_SHA256, "native16 numerical log after run"
    )
    _, native_fields = fingerprint_fields(NATIVE_LOG)
    candidate, numerical_deltas = validate_restore(run, native_fields)

    sim_ticks = candidate["stats"]["simTicks"]  # type: ignore[index]
    ratio = CONTROL_SIMTICKS / sim_ticks  # type: ignore[operator]
    result: dict[str, object] = {
        "schema": "dx100.cg.direct4_product_page_fed_q16_full_result.v1",
        "terminal": True,
        "gate": "PASS_NUMERICAL_MECHANISM_CORRECT",
        "candidate_only": True,
        "observations": 1,
        "official_nas_verification": False,
        "native_speedup_claim": False,
        "iso_area_speedup_claim": False,
        "p16_reorder_preserved": False,
        "q16_reorder_preserved": True,
        "source_commit": before_commit,
        "gem5_sha256": GEM5_SHA256,
        "ramulator_sha256": RAMULATOR_SHA256,
        "guest_sha256": sha256_file(guest),
        "checkpoint_ledger_sha256": hashlib.sha256(
            checkpoint_before.encode()
        ).hexdigest(),
        "certificate": certificate_identity,
        "numerical_relative_deltas_vs_native16": numerical_deltas,
        "performance": {
            "metric": "first_roi_simTicks",
            "candidate": sim_ticks,
            "accepted_page_fed_control": CONTROL_SIMTICKS,
            "control_over_candidate_ratio": ratio,
        },
        "candidate": candidate,
    }
    certified_paths = [
        out / "manifest.json",
        run / "restore.log",
        run / "restore.log.exit",
        run / "stats.txt",
        run / "config.ini",
        input_dir / "checkpoint.files.sha256.before",
        input_dir / "checkpoint.files.sha256.after",
        input_dir / "artifact_sha256.before",
        input_dir / "artifact_sha256.after",
        input_dir / "source_status.before",
        input_dir / "source_status.after",
        input_dir / "source_commit.before",
        input_dir / "source_commit.after",
    ]
    certified_ledger = artifact_ledger(certified_paths)
    write_result_and_gate(out, result, certified_ledger)
    print(
        json.dumps(
            {
                "terminal": True,
                "gate": result["gate"],
                "simTicks": sim_ticks,
                "control_over_candidate_ratio": ratio,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
