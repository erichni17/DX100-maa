#!/usr/bin/env python3
"""Audit and run the fresh four-arm UME GZZ strict two-pass matrix.

The executable path is fail closed.  GZZ is selected only because its
production gradient phase has an unpredicated ``INDIR_LD_VIRTUAL_INDEX``
producer, an aligned coherent result backing, and a later page consumer.  A
run is accepted only when a fresh strict trace proves complete B admission,
zero A issues before admission closure, reordered A access, bounded result
storage, exact backing ACK closure, and the exact UME reference fingerprint.
GZP's selected masked/published SoA/JIT RMW flow is audited but is not relabelled
as this direct-result edge.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import (
    asdict,
    dataclass,
)
from pathlib import Path
from typing import (
    Any,
    Mapping,
    Sequence,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.scripts import (  # noqa: E402
    run_hybrid_equal_work_micro_matrix as base,
)

MatrixError = base.MatrixError
require = base.require

RUNNER_BASE_COMMIT = "19b648687c3ca16411b5942d0760c4c07a5e17de"
SIMULATOR_SOURCE_COMMIT = "9393ef52e47357d9192050e539e013b6ce64df23"
EXPECTED_GEM5_SHA256 = (
    "aa5c70b140b6fb66bfb9f4a28b34f009f025cf639eb288c01dbb91b0d2f609bb"
)
EXPECTED_RAMULATOR_SHA256 = (
    "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753"
)
EXPECTED_RAMULATOR_CONFIG_SHA256 = (
    "aca6e27b58afdfbfd80b7ec41c3f0e7e574a1fc7355a3512981ead823f68731b"
)
DEFAULT_BUILD_ROOT = Path(
    "/data1/nier/worktrees/"
    "DX100-virtualization-selected-integration-cont-20260826"
)
DEFAULT_GEM5 = DEFAULT_BUILD_ROOT / "build/X86/gem5.opt"
DEFAULT_RAMULATOR = (
    DEFAULT_BUILD_ROOT / "ext/ramulator2/ramulator2/libramulator.so"
)
DEFAULT_RAMULATOR_CONFIG = (
    DEFAULT_BUILD_ROOT / "ext/ramulator2/ramulator2/example_gem5_config.yaml"
)
CONFIG = ROOT / "configs/deprecated/example/se.py"

ELEMENTS = 16_384
PHYSICAL_ELEMENTS = 4_096
OUTPUT_ELEMENTS = ELEMENTS + 180_000
EXPECTED_OUTPUT_HASH = "7602200327591349891"
EXPECTED_ACTIVE_CORNERS = 15_564
RESULT_WORD_BOUND = 4_096
EXPECTED_BACKING_BYTES = ELEMENTS * 4
EXPECTED_BACKING_LINES = EXPECTED_BACKING_BYTES // 64
EXPECTED_PAGES = ELEMENTS // PHYSICAL_ELEMENTS

PRODUCTION_PATHS = (
    "benchmarks/UME/gradzatp.cpp",
    "benchmarks/UME/gradzatz.cpp",
    "benchmarks/API/MAA_gem5.hpp",
    "benchmarks/API/MAA_virtual_materialize.hpp",
    "src/mem/MAA/IndirectAccess.cc",
    "src/mem/MAA/MAA.cc",
    "src/mem/MAA/MAA.py",
    "configs/common/Options.py",
    "configs/common/MAAConfig.py",
    "configs/deprecated/example/se.py",
)


@dataclass(frozen=True)
class Arm:
    name: str
    guest: str
    selector: str | None
    logical: int
    physical: int
    strict: bool
    complete_line: bool
    combine_slots: int
    combine_words: int
    response_words: int
    expected_indirect_reads: int
    expected_indirect_rmws: int

    @property
    def result_words(self) -> int:
        return self.combine_words + self.response_words


ARMS = (
    Arm(
        "native16",
        "native16",
        None,
        16_384,
        16_384,
        False,
        False,
        512,
        3_584,
        512,
        2,
        2,
    ),
    Arm(
        "native4",
        "native4",
        None,
        4_096,
        4_096,
        False,
        False,
        512,
        3_584,
        512,
        8,
        8,
    ),
    Arm(
        "original_hybrid",
        "hybrid",
        "stream_control",
        16_384,
        4_096,
        False,
        False,
        512,
        3_584,
        512,
        5,
        8,
    ),
    Arm(
        "strict_bounded_hybrid",
        "hybrid",
        "token_stream_ld",
        16_384,
        4_096,
        True,
        True,
        2_048,
        3_072,
        1_024,
        5,
        8,
    ),
)


def sha256(path: Path) -> str:
    return base.sha256_file(path)


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def atomic_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def source(path: str) -> str:
    candidate = ROOT / path
    require(candidate.is_file() and not candidate.is_symlink(), path)
    return candidate.read_text(encoding="utf-8")


def require_tokens(path: str, tokens: Sequence[str]) -> None:
    text = source(path)
    missing = [token for token in tokens if token not in text]
    require(not missing, f"{path}: missing source tokens {missing}")


def verify_committed_production() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in PRODUCTION_PATHS:
        live = (ROOT / relative).read_bytes()
        committed = subprocess.check_output(
            [
                "git",
                "-C",
                str(ROOT),
                "show",
                f"{SIMULATOR_SOURCE_COMMIT}:{relative}",
            ]
        )
        require(live == committed, f"production source drift: {relative}")
        hashes[relative] = hashlib.sha256(live).hexdigest()
    return hashes


def source_contract() -> dict[str, Any]:
    require(
        deterministic_output_hash(ELEMENTS) == int(EXPECTED_OUTPUT_HASH),
        "frozen GZZ fingerprint no longer matches deterministic input",
    )
    require_tokens(
        "src/mem/MAA/IndirectAccess.cc",
        (
            "return maa->virtual_strict_two_phase && isVirtualLoad() &&",
            "isDirectIndexLoad() && !isSoaJitRmw();",
            "strict two-phase requires one complete",
            "event=strict_two_phase_admission_closed schema=2",
            "raw_b_buffered_words=0 a_issues=0",
            "complete-line-only mode requires an unpredicated",
            "insertVirtualCombineWord",
        ),
    )
    require_tokens(
        "src/mem/MAA/MAA.cc",
        (
            "virtual_combine_words + virtual_response_word_pool >",
            "physical_tile_elements",
            "event=strict_two_phase_timing schema=2",
            "order_ok=1 terminal=1",
        ),
    )
    require_tokens(
        "benchmarks/UME/gradzatz.cpp",
        (
            "alignas(64) static DATATYPE virtual_gather_backing",
            "maa_indirect_load_virtual_index<DATATYPE>(",
            "reinterpret_cast<uint32_t *>(c_to_p_map.data())",
            "virtual_gather_backing[omp_thread_id]",
            "if (gather_size == TILE_SIZE)",
            "maa_virtual_consumer_begin(virtual_consumer_mode, tile0)",
            "maa_virtual_consumer_load_page<DATATYPE>(",
            "maa_indirect_rmw_vector<DATATYPE>(\n                    zone_gradient.data()",
            "UME_OUTPUT_FP output_hash=",
            "UME_REFERENCE_PASS volume_errors=0 gradient_errors=0",
        ),
    )
    require_tokens(
        "benchmarks/UME/gradzatp.cpp",
        (
            "maa_indirect_load_virtual_index<DATATYPE>(",
            "UME_GZP_SOA_JIT_RMW",
            "maa_publish_spd_page_logical16_response_bearing",
            "maa_indirect_rmw_vector_soa_jit",
            "UME_GZP_TERMINAL treatment=",
        ),
    )
    gzz = source("benchmarks/UME/gradzatz.cpp")
    producer = gzz.index("maa_indirect_load_virtual_index<DATATYPE>(")
    begin = gzz.index("maa_virtual_consumer_begin", producer)
    page = gzz.index("maa_virtual_consumer_load_page<DATATYPE>", begin)
    gradient = gzz.index("zone_gradient.data(), tile3, tile5", page)
    require(producer < begin < page < gradient, "GZZ producer/consumer order")
    return {
        "status": "PASS",
        "simulator_source_commit": SIMULATOR_SOURCE_COMMIT,
        "runner_base_commit": RUNNER_BASE_COMMIT,
        "applications": [
            {
                "name": "GZP",
                "direct_result_edge_present": True,
                "selected_for_matrix": False,
                "reason": (
                    "The currently selected production treatment continues "
                    "through response-bearing page publication and masked "
                    "SoA/JIT RMW for two destinations. That is a distinct "
                    "published-source/RMW flow, not evidence for the direct "
                    "virtual-result edge."
                ),
            },
            {
                "name": "GZZ",
                "direct_result_edge_present": True,
                "selected_for_matrix": True,
                "reason": (
                    "The gradient phase issues one unpredicated full-span "
                    "direct-index virtual load to aligned coherent backing, "
                    "then consumes four physical pages before the existing "
                    "zone-gradient RMW. No fused opcode or new ABI is used."
                ),
            },
        ],
        "selection": "GZZ",
        "matrix_n": ELEMENTS,
        "expected_active_corners": EXPECTED_ACTIVE_CORNERS,
        "expected_output_hash": EXPECTED_OUTPUT_HASH,
        "physical_result_word_bound": RESULT_WORD_BOUND,
        "source_sha256": {
            path: sha256(ROOT / path) for path in PRODUCTION_PATHS
        },
    }


def deterministic_output_hash(elements: int) -> int:
    """Reproduce GZZ's index-stable FP32 hash for its fixed input."""
    padding = 90_000
    mask = (1 << 64) - 1
    value = 1_469_598_103_934_665_603
    for zone in range(elements + 2 * padding):
        corner = zone - padding
        active = 0 <= corner < elements and corner % 20 != 0
        volume = 1.0 if active else 0.0
        gradient = (
            float(padding + ((97 * corner + 13) % elements) + 1)
            if active
            else 0.0
        )
        for output_index, datum in (
            (zone * 2, volume),
            (zone * 2 + 1, gradient),
        ):
            bits = struct.unpack("<I", struct.pack("<f", datum))[0]
            value ^= ((output_index << 32) ^ bits) & mask
            value = (value * 1_099_511_628_211) & mask
    return value


def plan() -> dict[str, Any]:
    audit = source_contract()
    return {
        "schema": "dx100.ume_gzz_two_pass.plan.v1",
        "audit": audit,
        "arms": [
            asdict(arm) | {"result_words": arm.result_words} for arm in ARMS
        ],
        "same_simulator_binary": True,
        "fresh_controls_required": True,
        "short_restore_elements": ELEMENTS,
        "max_parallel_restores_default": 4,
        "acceptance": {
            "output_hash": EXPECTED_OUTPUT_HASH,
            "reference_errors": 0,
            "strict_operations": 1,
            "strict_b_words": ELEMENTS,
            "strict_descriptors": ELEMENTS,
            "a_issues_at_admission_close": 0,
            "backing_semantic_bytes": EXPECTED_BACKING_BYTES,
            "result_words_at_most": RESULT_WORD_BOUND,
            "partial_result_writes": 0,
        },
    }


def copy_stable(source_path: Path, destination: Path) -> str:
    before = sha256(source_path)
    base.copy_reflink(source_path, destination)
    after = sha256(source_path)
    frozen = sha256(destination)
    require(before == after == frozen, f"unstable artifact: {source_path}")
    return frozen


def build_guests(
    root: Path, common_defines: tuple[str, ...] = ()
) -> tuple[dict[str, Path], list[list[str]]]:
    build = root / "build"
    build.mkdir()
    m5op_source = ROOT / "util/m5/build/x86/abi/x86/m5op.S"
    if not m5op_source.is_file():
        m5op_source = ROOT / "util/m5/src/abi/x86/m5op.S"
    require(m5op_source.is_file(), "missing m5op.S")
    common = [
        "g++",
        "-std=c++11",
        "-O3",
        "-Wall",
        "-g3",
        "-fopenmp",
        f"-I{ROOT / 'benchmarks/API'}",
        f"-I{ROOT / 'include'}",
        f"-I{ROOT / 'util/m5/src'}",
        "-DGEM5",
        "-DMAA",
        "-DNUM_CORES=4",
        "-DMAA_MEM_SIZE=0x80000000",
        "-DUME_GRADZATZ_FIXED_INPUT",
        "-DUME_GRADZATZ_OUTPUT_FINGERPRINT",
        f"-DUME_GRADZATZ_EXPECTED_N={ELEMENTS}",
        f"-DUME_GRADZATZ_EXPECTED_HASH={EXPECTED_OUTPUT_HASH}ULL",
        *common_defines,
    ]
    m5op = build / "m5op.o"
    commands = [
        [
            "g++",
            "-std=c++11",
            "-O3",
            "-Wall",
            "-g3",
            "-fopenmp",
            f"-I{ROOT / 'include'}",
            f"-I{ROOT / 'util/m5/src'}",
            "-DGEM5",
            "-c",
            str(m5op_source),
            "-o",
            str(m5op),
        ]
    ]
    binaries = {
        "native16": build / "gradzatz_native16",
        "native4": build / "gradzatz_native4",
        "hybrid": build / "gradzatz_hybrid",
    }
    source_path = ROOT / "benchmarks/UME/gradzatz.cpp"
    commands.extend(
        [
            *common,
            f"-DTILE_SIZE={tile}",
            *extra,
            str(m5op),
            str(source_path),
            "-o",
            str(binaries[name]),
        ]
        for name, tile, extra in (
            ("native16", 16_384, []),
            ("native4", 4_096, []),
            (
                "hybrid",
                16_384,
                [
                    "-DMAA_VIRTUAL_GATHER",
                    "-DMAA_GENERAL_VIRTUAL_CONSUMER",
                    "-DMAA_CONSUMER_TILE_SIZE=4096",
                ],
            ),
        )
    )
    atomic_json(root / "build.commands.json", commands)
    for index, command in enumerate(commands):
        log = root / f"build.{index}.log"
        with log.open("wb") as stream:
            completed = subprocess.run(
                command, stdout=stream, stderr=subprocess.STDOUT, check=False
            )
        require(completed.returncode == 0, f"guest build {index} failed")
    for path in binaries.values():
        require(path.is_file(), f"missing guest binary: {path}")
        path.chmod(0o555)
    return binaries, commands


def checkpoint_command(
    gem5: Path, guest: Path, outdir: Path, options: str
) -> list[str]:
    return [
        str(gem5),
        "--listener-mode=off",
        f"--outdir={outdir}",
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
        options,
    ]


def common_restore_command(
    gem5: Path,
    ramulator_config: Path,
    checkpoint: Path,
    guest: Path,
    options: str,
    outdir: Path,
    arm: Arm,
) -> list[str]:
    command = [
        str(gem5),
        "--listener-mode=off",
        f"--outdir={outdir}",
        "--debug-flags=MAAVirtualTrace,MAAMacroEvent",
        "--debug-file=contract_trace.log",
        str(CONFIG),
        "--cpu-type",
        "X86O3CPU",
        "-r",
        "1",
        "-n",
        "4",
        "--mem-size",
        "2GB",
        f"--checkpoint-dir={checkpoint}",
        "--sys-clock",
        "3.2GHz",
        "--cpu-clock",
        "3.2GHz",
        "--caches",
        "--l1d_size=32kB",
        "--l1d_assoc=8",
        "--l1d-hwp-type=StridePrefetcher",
        "--l1d_mshrs=16",
        "--l1d_write_buffers=8",
        "--l1i_size=32kB",
        "--l1i_assoc=8",
        "--l1i-hwp-type=StridePrefetcher",
        "--l1i_mshrs=16",
        "--l1i_write_buffers=8",
        "--l2cache",
        "--l2_size=256kB",
        "--l2_assoc=4",
        "--l2-hwp-type=StridePrefetcher",
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
        str(ramulator_config),
        "--mem-channels=2",
        "--maa",
        "--maa_num_maas=1",
        "--maa_num_indirect_units_per_maa=4",
        "--maa_num_tiles_per_core=8",
        f"--maa_num_tile_elements={arm.logical}",
        f"--maa_physical_tile_elements={arm.physical}",
        "--maa_num_initial_row_table_slices=32",
        "--maa_num_row_table_rows_per_slice=64",
        "--maa_num_row_table_entries_per_subslice_row=8",
        f"--maa_num_offset_table_entries={arm.logical}",
        f"--maa_num_offset_table_epoch_entries={arm.logical}",
        "--maa_virtual_grow_order",
        "--maa_virtual_index_force_cache",
        "--maa_virtual_index_buffer_lines=64",
        "--maa_virtual_masked_writes",
        f"--maa_virtual_combine_slots={arm.combine_slots}",
        f"--maa_virtual_combine_words={arm.combine_words}",
        "--maa_virtual_combine_ways=8",
        "--maa_virtual_combine_banks=4",
        "--maa_virtual_combine_set_xor_shift=7",
        "--maa_virtual_response_slots=128",
        f"--maa_virtual_response_word_pool={arm.response_words}",
        "--maa_virtual_words_per_cycle=4",
        "--maa_virtual_max_outstanding_writes=64",
        "--maa_direct_retirement_line_handoff",
        "--cmd",
        str(guest),
        "--options",
        options,
    ]
    insertion = command.index("--cmd")
    strict = [
        "--maa_virtual_strict_two_phase",
        "--maa_virtual_shared_result_payload",
        "--maa_virtual_complete_line_only",
        "--maa_virtual_page_ordered_combiner_drain",
        "--maa_virtual_combine_lookup_latency_cycles=3",
        "--maa_virtual_complete_line_drain_lines_per_cycle=1",
        "--maa_virtual_complete_line_payload_words_per_cycle=8",
        "--maa_virtual_complete_line_payload_active_lines=1",
        "--maa_virtual_complete_line_payload_banks=32",
    ]
    if arm.strict:
        command[insertion:insertion] = strict
    return command


def tree_identity(path: Path) -> dict[str, Any]:
    files: dict[str, str] = {}
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        if not item.is_file():
            continue
        relative = str(item.relative_to(path))
        value = sha256(item)
        files[relative] = value
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(value.encode())
        digest.update(b"\n")
    require(files, f"empty checkpoint: {path}")
    return {"sha256": digest.hexdigest(), "files": files}


def run_logged(
    command: Sequence[str],
    directory: Path,
    stem: str,
    environment: Mapping[str, str],
) -> int:
    atomic_json(directory / f"{stem}.command.json", list(command))
    rc = base.run_command(
        command,
        directory / f"{stem}.log",
        environment,
        directory / f"{stem}.process.json",
    )
    atomic_text(directory / f"{stem}.exit", f"{rc}\n")
    return rc


def arm_options(arm: Arm, selector: Path | None) -> str:
    if selector is None:
        return str(ELEMENTS)
    return f"{ELEMENTS} {selector}"


def prepare(
    root: Path, gem5: Path, ramulator: Path, ramulator_config: Path
) -> dict[str, Any]:
    require(not root.exists(), f"refusing existing output: {root}")
    source_contract()
    production_hashes = verify_committed_production()
    require(sha256(gem5) == EXPECTED_GEM5_SHA256, "unexpected gem5 binary")
    require(
        sha256(ramulator) == EXPECTED_RAMULATOR_SHA256,
        "unexpected Ramulator library",
    )
    require(
        sha256(ramulator_config) == EXPECTED_RAMULATOR_CONFIG_SHA256,
        "unexpected Ramulator config",
    )
    root.mkdir(parents=True)
    inputs = root / "inputs"
    inputs.mkdir()
    frozen_gem5 = inputs / "gem5.opt"
    frozen_ramulator = inputs / "libramulator.so"
    frozen_config = inputs / "ramulator.yaml"
    copy_stable(gem5, frozen_gem5)
    copy_stable(ramulator, frozen_ramulator)
    copy_stable(ramulator_config, frozen_config)
    frozen_gem5.chmod(0o555)
    guests, build_commands = build_guests(inputs)
    environment = os.environ.copy()
    environment["LD_LIBRARY_PATH"] = str(inputs) + (
        ":" + environment["LD_LIBRARY_PATH"]
        if environment.get("LD_LIBRARY_PATH")
        else ""
    )
    environment["OMP_NUM_THREADS"] = "4"
    environment["OMP_PROC_BIND"] = "false"
    ldd = subprocess.check_output(
        ["ldd", str(frozen_gem5)], env=environment, text=True
    )
    atomic_text(inputs / "gem5.ldd.txt", ldd)
    match = re.search(r"^[ \t]*libramulator\.so => (\S+)", ldd, re.M)
    require(match is not None, "frozen gem5 did not resolve Ramulator")
    require(
        Path(match.group(1)).resolve() == frozen_ramulator.resolve(),
        "frozen gem5 resolved the wrong Ramulator library",
    )
    selectors: dict[str, Path | None] = {}
    for arm in ARMS:
        if arm.selector is None:
            selectors[arm.name] = None
            continue
        path = inputs / f"{arm.name}.selector"
        atomic_text(path, arm.selector + "\n")
        path.chmod(0o444)
        selectors[arm.name] = path.resolve()
    manifest = {
        "schema": "dx100.ume_gzz_two_pass.campaign.v1",
        "simulator_source_commit": SIMULATOR_SOURCE_COMMIT,
        "runner_base_commit": RUNNER_BASE_COMMIT,
        "same_simulator_binary": True,
        "gem5_sha256": sha256(frozen_gem5),
        "ramulator_sha256": sha256(frozen_ramulator),
        "ramulator_config_sha256": sha256(frozen_config),
        "production_sha256": production_hashes,
        "guest_sha256": {name: sha256(path) for name, path in guests.items()},
        "hybrid_guest_shared": sha256(guests["hybrid"]),
        "build_commands": build_commands,
        "arms": [
            asdict(arm) | {"result_words": arm.result_words} for arm in ARMS
        ],
        "input_elements": ELEMENTS,
        "expected_output_hash": EXPECTED_OUTPUT_HASH,
        "expected_active_corners": EXPECTED_ACTIVE_CORNERS,
        "parallel_restores": None,
    }
    atomic_json(root / "manifest.json", manifest)
    return {
        "gem5": frozen_gem5.resolve(),
        "ramulator": frozen_ramulator.resolve(),
        "ramulator_config": frozen_config.resolve(),
        "guests": guests,
        "selectors": selectors,
        "environment": environment,
        "manifest": manifest,
    }


def run_campaign(
    root: Path,
    gem5: Path,
    ramulator: Path,
    ramulator_config: Path,
    max_parallel: int,
) -> dict[str, Any]:
    prepared = prepare(root, gem5, ramulator, ramulator_config)
    atomic_text(root / "campaign.exit", "running\n")
    identities: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        group = root / "checkpoints" / arm.name
        group.mkdir(parents=True)
        command = checkpoint_command(
            prepared["gem5"],
            prepared["guests"][arm.guest],
            group / "gem5",
            arm_options(arm, prepared["selectors"][arm.name]),
        )
        rc = run_logged(command, group, "checkpoint", prepared["environment"])
        require(rc == 0, f"{arm.name}: checkpoint failed")
        log = (group / "checkpoint.log").read_text(errors="replace")
        require("because checkpoint" in log, f"{arm.name}: no checkpoint exit")
        identity = tree_identity(group / "gem5")
        identities[arm.name] = identity
        atomic_json(group / "identity.json", identity)

    def restore(arm: Arm) -> str | None:
        try:
            group = root / "checkpoints" / arm.name / "gem5"
            before = tree_identity(group)
            require(
                before["sha256"] == identities[arm.name]["sha256"],
                f"{arm.name}: checkpoint changed before restore",
            )
            arm_root = root / "arms" / arm.name
            arm_root.mkdir(parents=True)
            command = common_restore_command(
                prepared["gem5"],
                prepared["ramulator_config"],
                group,
                prepared["guests"][arm.guest],
                arm_options(arm, prepared["selectors"][arm.name]),
                arm_root / "run",
                arm,
            )
            rc = run_logged(
                command, arm_root, "restore", prepared["environment"]
            )
            require(rc == 0, f"{arm.name}: restore rc={rc}")
            after = tree_identity(group)
            require(
                after["sha256"] == identities[arm.name]["sha256"],
                f"{arm.name}: checkpoint mutated during restore",
            )
        except (OSError, subprocess.SubprocessError, MatrixError) as error:
            return f"{arm.name}: {error}"
        return None

    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        failures = list(pool.map(restore, ARMS))
    failures = [failure for failure in failures if failure]
    if failures:
        atomic_json(root / "failure.json", {"failures": failures})
        atomic_text(root / "campaign.exit", "1\n")
        raise MatrixError("; ".join(failures))
    manifest = prepared["manifest"]
    manifest["parallel_restores"] = max_parallel
    atomic_json(root / "manifest.json", manifest)
    result = validate_campaign(root)
    atomic_json(root / "result.json", result)
    atomic_text(root / "gate.complete", "PASS\n")
    atomic_text(root / "campaign.exit", "0\n")
    write_ledger(root)
    return result


def optional_sum(stats: Mapping[str, float], suffix: str) -> int:
    values = [value for name, value in stats.items() if name.endswith(suffix)]
    require(all(value.is_integer() for value in values), suffix)
    return int(sum(values))


def one_marker(text: str, prefix: str, label: str) -> dict[str, str]:
    matches = [line for line in text.splitlines() if line.startswith(prefix)]
    require(len(matches) == 1, f"{label}: expected one {prefix.strip()}")
    return {
        key: value
        for token in matches[0].split()[1:]
        if "=" in token
        for key, value in [token.split("=", 1)]
    }


def classify_arm(
    root: Path, arm: Arm, manifest: Mapping[str, Any]
) -> dict[str, Any]:
    arm_root = root / "arms" / arm.name
    require((arm_root / "restore.exit").read_text() == "0\n", arm.name)
    base.validate_process_record(arm_root / "restore.process.json")
    log_path = arm_root / "restore.log"
    log = log_path.read_text(encoding="utf-8", errors="replace")
    require(
        len(re.findall(r"because m5_exit instruction encountered", log)) == 1,
        f"{arm.name}: missing terminal m5_exit",
    )
    lowered = log.lower()
    require(
        not re.search(
            r"panic:|fatal:|assertion .*failed|segmentation fault|abort",
            lowered,
        ),
        f"{arm.name}: fatal simulator text",
    )
    output = one_marker(log, "UME_OUTPUT_FP ", arm.name)
    reference = one_marker(log, "UME_REFERENCE_PASS ", arm.name)
    require(
        output == {"output_hash": EXPECTED_OUTPUT_HASH, "nonfinite": "0"},
        f"{arm.name}: output fingerprint",
    )
    require(
        reference
        == {
            "volume_errors": "0",
            "gradient_errors": "0",
            "elements": str(OUTPUT_ELEMENTS),
        },
        f"{arm.name}: scalar reference",
    )
    if arm.selector is not None:
        marker = one_marker(log, "UME_GZZ_VIRTUAL_CONSUMER ", arm.name)
        require(
            marker
            == {"mode": arm.selector, "logical": "16384", "consumer": "4096"},
            f"{arm.name}: guest selector",
        )
    stats = base.first_stats_section(arm_root / "run/stats.txt")
    counters = {
        "simTicks": base.exact_stat(stats, "simTicks"),
        "simInsts": base.exact_stat(stats, "simInsts"),
        "numInst_INDRD": base.exact_stat(stats, "system.maa.numInst_INDRD"),
        "numInst_INDRMW": base.exact_stat(stats, "system.maa.numInst_INDRMW"),
        "index_words": optional_sum(stats, "IND_VirtIndexWords"),
        "write_issues": optional_sum(stats, "IND_VirtWriteIssues"),
        "write_completions": optional_sum(stats, "IND_VirtWriteCompletions"),
        "full_line_writes": optional_sum(stats, "IND_VirtFullLineWrites"),
        "partial_writes": optional_sum(stats, "IND_VirtPartialWrites"),
        "pages_ready": optional_sum(stats, "IND_VirtPagesReady"),
        "strict_operations": optional_sum(
            stats, "IND_StrictTwoPhaseOperations"
        ),
        "strict_descriptors": optional_sum(
            stats, "IND_StrictTwoPhaseDescriptors"
        ),
        "strict_backing_issues": optional_sum(
            stats, "IND_StrictTwoPhaseBackingIssues"
        ),
        "complete_payload_starts": optional_sum(
            stats, "IND_VirtCompleteLinePayloadStarts"
        ),
        "complete_payload_completions": optional_sum(
            stats, "IND_VirtCompleteLinePayloadCompletions"
        ),
        "complete_payload_scheduled_words": optional_sum(
            stats, "IND_VirtCompleteLinePayloadScheduledWords"
        ),
        "complete_payload_read_words": optional_sum(
            stats, "IND_VirtCompleteLinePayloadReadWords"
        ),
    }
    require(
        counters["numInst_INDRD"] == arm.expected_indirect_reads,
        f"{arm.name}: indirect-read work changed",
    )
    require(
        counters["numInst_INDRMW"] == arm.expected_indirect_rmws,
        f"{arm.name}: indirect-RMW work changed",
    )
    config = base.parse_config(arm_root / "run/config.ini")
    expected_config = {
        "num_tile_elements": str(arm.logical),
        "physical_tile_elements": str(arm.physical),
        "num_initial_row_table_slices": "32",
        "num_offset_table_entries": str(arm.logical),
        "num_offset_table_epoch_entries": str(arm.logical),
        "virtual_combine_slots": str(arm.combine_slots),
        "virtual_combine_words": str(arm.combine_words),
        "virtual_response_word_pool": str(arm.response_words),
        "virtual_strict_two_phase": "true" if arm.strict else "false",
        "virtual_complete_line_only": "true" if arm.complete_line else "false",
        "no_reorder": "false",
        "reconfigure_row_table": "false",
    }
    for key, expected in expected_config.items():
        require(config.get(key) == expected, f"{arm.name}: config {key}")
    require(arm.result_words <= RESULT_WORD_BOUND, f"{arm.name}: result bound")
    trace_path = arm_root / "run/contract_trace.log"
    trace_lines = trace_path.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines()
    strict_trace = None
    admission = None
    strict_events = [
        parsed
        for line in trace_lines
        if (parsed := base.parse_event(line, "strict_two_phase_timing"))
    ]
    if arm.strict:
        require(
            len(strict_events) == 1,
            "strict path did not activate exactly once",
        )
        strict_trace = strict_events[0]
        admission = base.exactly_one_event(
            trace_lines, "strict_two_phase_admission_closed"
        )
        expected = {
            "schema": "2",
            "logical": "16384",
            "physical": "4096",
            "result_words": "4096",
            "b_words": "16384",
            "descriptors": "16384",
            "pages_ready": "4",
            "backing_semantic_bytes": str(EXPECTED_BACKING_BYTES),
            "exact_b_once": "1",
            "raw_b_retained_bytes": "0",
            "descriptor_backing_bytes": "0",
            "replay_passes": "0",
            "coherent_ack": "1",
            "order_ok": "1",
            "terminal": "1",
        }
        for key, value in expected.items():
            require(strict_trace.get(key) == value, f"strict trace {key}")
        for key, value in {
            "schema": "2",
            "b_words": "16384",
            "descriptors": "16384",
            "offsets": "16384",
            "raw_b_buffered_words": "0",
            "a_issues": "0",
        }.items():
            require(admission.get(key) == value, f"strict admission {key}")
        require(
            int(strict_trace["A_FIRST_ISSUE"])
            >= int(strict_trace["ROW_OFFSET_LAST_INSERT"]),
            "strict A issue preceded admission",
        )
        require(
            strict_trace["a_issues"] == strict_trace["a_responses"],
            "strict A response closure",
        )
        require(
            strict_trace["backing_issues"] == strict_trace["backing_acks"],
            "strict backing ACK closure",
        )
        require(counters["strict_operations"] == 1, "strict operation counter")
        require(
            counters["strict_descriptors"] == ELEMENTS, "strict descriptors"
        )
        require(
            counters["strict_backing_issues"] == counters["write_issues"],
            "strict/write issue identity",
        )
        require(
            counters["write_issues"]
            == counters["write_completions"]
            == counters["full_line_writes"]
            == EXPECTED_BACKING_LINES,
            "strict complete-line ACK closure",
        )
        require(counters["partial_writes"] == 0, "strict partial write")
        require(counters["pages_ready"] == EXPECTED_PAGES, "strict pages")
        require(
            counters["complete_payload_starts"]
            == counters["complete_payload_completions"]
            == EXPECTED_BACKING_LINES,
            "strict payload-line closure",
        )
        require(
            counters["complete_payload_scheduled_words"]
            == counters["complete_payload_read_words"]
            == ELEMENTS,
            "strict payload-word closure",
        )
    else:
        require(not strict_events, f"{arm.name}: unexpected strict activation")
        require(counters["strict_operations"] == 0, f"{arm.name}: strict work")
    if arm.selector is not None:
        require(counters["index_words"] == ELEMENTS, f"{arm.name}: B work")
        require(
            counters["write_issues"] == counters["write_completions"] > 0,
            f"{arm.name}: result backing closure",
        )
    return {
        "classification": "ACCEPT",
        "output_hash": output["output_hash"],
        "reference": reference,
        "counters": counters,
        "strict_trace": strict_trace,
        "strict_admission": admission,
        "result_storage_words": arm.result_words,
        "result_storage_bound_words": RESULT_WORD_BOUND,
        "checkpoint_sha256": json.loads(
            (root / "checkpoints" / arm.name / "identity.json").read_text()
        )["sha256"],
        "guest_sha256": manifest["guest_sha256"][arm.guest],
        "restore_log_sha256": sha256(log_path),
        "stats_sha256": sha256(arm_root / "run/stats.txt"),
        "trace_sha256": sha256(trace_path),
        "config_sha256": sha256(arm_root / "run/config.ini"),
    }


def validate_campaign(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "manifest.json").read_text())
    require(
        manifest["schema"] == "dx100.ume_gzz_two_pass.campaign.v1", "schema"
    )
    require(
        sha256(root / "inputs/gem5.opt")
        == manifest["gem5_sha256"]
        == EXPECTED_GEM5_SHA256,
        "gem5 provenance",
    )
    for arm in ARMS:
        identity = json.loads(
            (root / "checkpoints" / arm.name / "identity.json").read_text()
        )
        require(
            tree_identity(root / "checkpoints" / arm.name / "gem5")["sha256"]
            == identity["sha256"],
            f"{arm.name}: checkpoint identity",
        )
    classified = {arm.name: classify_arm(root, arm, manifest) for arm in ARMS}
    require(
        len({item["output_hash"] for item in classified.values()}) == 1,
        "cross-arm output mismatch",
    )
    original = classified["original_hybrid"]["counters"]
    strict = classified["strict_bounded_hybrid"]["counters"]
    for field in ("numInst_INDRD", "numInst_INDRMW", "index_words"):
        require(
            original[field] == strict[field], f"hybrid work differs: {field}"
        )
    ticks = {
        name: item["counters"]["simTicks"] for name, item in classified.items()
    }
    return {
        "schema": "dx100.ume_gzz_two_pass.result.v1",
        "terminal": True,
        "decision": "ACCEPT_FRESH_GZZ_FOUR_ARM",
        "workload": "UME GZZ",
        "input_elements": ELEMENTS,
        "performance_metric": "simTicks",
        "same_simulator_binary": True,
        "gem5_sha256": manifest["gem5_sha256"],
        "simulator_source_commit": SIMULATOR_SOURCE_COMMIT,
        "runner_base_commit": RUNNER_BASE_COMMIT,
        "arms": classified,
        "ticks": ticks,
        "comparisons": {
            "native16_over_native4": ticks["native16"] / ticks["native4"],
            "native16_over_original_hybrid": (
                ticks["native16"] / ticks["original_hybrid"]
            ),
            "native16_over_strict_bounded_hybrid": (
                ticks["native16"] / ticks["strict_bounded_hybrid"]
            ),
            "original_over_strict_bounded": (
                ticks["original_hybrid"] / ticks["strict_bounded_hybrid"]
            ),
        },
        "limitations": [
            "one deterministic 16K-window observation per arm",
            "GZZ evidence only; GZP masked/published SoA/JIT RMW is not covered",
            "original_hybrid preserves the original stream-control schedule but "
            "normalizes response+combiner payload to the same 4K-word bound",
            "no full-application or synthesis/area claim",
        ],
    }


def ledger_paths(root: Path) -> list[Path]:
    paths = [
        root / "manifest.json",
        root / "campaign.exit",
        root / "result.json",
        root / "gate.complete",
        root / "build.commands.json",
        root / "inputs/gem5.opt",
        root / "inputs/libramulator.so",
        root / "inputs/ramulator.yaml",
        root / "inputs/gem5.ldd.txt",
    ]
    paths.extend(sorted((root / "inputs/build").glob("gradzatz_*")))
    for arm in ARMS:
        checkpoint = root / "checkpoints" / arm.name
        paths.extend(
            checkpoint / name
            for name in (
                "checkpoint.command.json",
                "checkpoint.log",
                "checkpoint.exit",
                "checkpoint.process.json",
                "identity.json",
            )
        )
        run = root / "arms" / arm.name
        paths.extend(
            run / name
            for name in (
                "restore.command.json",
                "restore.log",
                "restore.exit",
                "restore.process.json",
                "run/config.ini",
                "run/stats.txt",
                "run/contract_trace.log",
            )
        )
    return paths


def write_ledger(root: Path) -> None:
    lines = []
    for path in ledger_paths(root):
        require(path.is_file(), f"missing ledger artifact: {path}")
        lines.append(f"{sha256(path)}  {path.relative_to(root)}")
    atomic_text(root / "artifacts.sha256", "\n".join(sorted(lines)) + "\n")


def validate_ledger(root: Path) -> None:
    seen: set[str] = set()
    for line in (root / "artifacts.sha256").read_text().splitlines():
        digest, relative = line.split("  ", 1)
        require(relative not in seen, f"duplicate ledger path: {relative}")
        seen.add(relative)
        require(
            sha256(root / relative) == digest, f"ledger mismatch: {relative}"
        )
    expected = {str(path.relative_to(root)) for path in ledger_paths(root)}
    require(seen == expected, "ledger path set")


def record_rejection(root: Path, reason: str) -> None:
    """Make a failed execute/validation attempt terminal and auditable."""
    if not root.is_dir():
        return
    atomic_json(
        root / "failure.json",
        {
            "schema": "dx100.ume_gzz_two_pass.rejection.v1",
            "decision": "REJECT",
            "reason": reason,
            "strict_activation_accepted": False,
            "full_run_authorized": False,
        },
    )
    atomic_text(root / "campaign.exit", "1\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--validate", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--gem5", type=Path, default=DEFAULT_GEM5)
    parser.add_argument(
        "--ramulator-library", type=Path, default=DEFAULT_RAMULATOR
    )
    parser.add_argument(
        "--ramulator-config", type=Path, default=DEFAULT_RAMULATOR_CONFIG
    )
    parser.add_argument("--max-parallel-restores", type=int, default=4)
    args = parser.parse_args(argv)
    if args.execute and args.validate is not None:
        parser.error("--execute and --validate are mutually exclusive")
    if args.execute and args.out is None:
        parser.error("--execute requires --out")
    if not args.execute and args.out is not None:
        parser.error("--out requires --execute")
    if args.max_parallel_restores < 1 or args.max_parallel_restores > 4:
        parser.error("--max-parallel-restores must be in [1, 4]")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.execute:
            result = run_campaign(
                args.out.resolve(),
                args.gem5.resolve(),
                args.ramulator_library.resolve(),
                args.ramulator_config.resolve(),
                args.max_parallel_restores,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
        elif args.validate is not None:
            result = validate_campaign(args.validate.resolve())
            validate_ledger(args.validate.resolve())
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(json.dumps(plan(), indent=2, sort_keys=True))
    except (OSError, subprocess.SubprocessError, MatrixError) as error:
        failed_root = args.out if args.execute else args.validate
        if failed_root is not None:
            record_rejection(failed_root.resolve(), str(error))
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
