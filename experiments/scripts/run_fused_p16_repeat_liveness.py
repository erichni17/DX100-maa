#!/usr/bin/env python3
"""Run exact serial 16/32/64 fused-p16 reset/liveness cases.

One deterministic guest and one treatment-neutral checkpoint feed the three
restores.  Each restore contains all repeated producer+q16 operations in one
ROI.  There is no timeout, comparison arm, application run, or per-access
trace.  The first nonterminal or incorrect case stops the size escalation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
GUEST_SOURCE = ROOT / "benchmarks/API/test_fused_p16_repeat_liveness.cpp"
CONFIG = ROOT / "configs/deprecated/example/se.py"
RAMULATOR_CONFIG = ROOT / "ext/ramulator2/ramulator2/example_gem5_config.yaml"
GEM5 = Path(
    "/data1/nier/worktrees/codex-sessions/"
    "hybrid-fused-p16-product-evidence-repair-2026082-20260826-160656-"
    "c4f154c5/DX100-virtualization-selected-integration-cont-20260826/"
    "build/X86/gem5.opt"
)
GEM5_SHA256 = (
    "271836b58d02d9d50a658cd5c7628e15559ca22d3a04477ab15475e3744dfd2e"
)
RAMULATOR = Path(
    "/data1/nier/dx100-runs/2026-08-12-hybrid-line-handoff-8a5c7712/"
    "input/libramulator.so"
)
RAMULATOR_SHA256 = (
    "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753"
)
PINNED_SIMULATOR_SOURCE = "4a4d91b8f176c33779804fbd163014593d89e737"
CASES = (16, 32, 64)
WORDS = 16384
PAGES = 4
FATAL_RE = re.compile(
    r"panic|fatal|assert|abort|segmentation fault|error:", re.IGNORECASE
)

POSITIVE_STATS = (
    "simTicks",
    "IND_FusedP16Operations",
    "IND_FusedP16Epochs",
    "IND_FusedP16SourceOrdinals",
    "IND_FusedP16CoefficientReadIssues",
    "IND_FusedP16CoefficientReadResponses",
    "IND_FusedP16CoefficientFills",
    "IND_FusedP16CoefficientDeliveries",
    "IND_FusedP16MulAccepts",
    "IND_FusedP16MulCompletions",
    "IND_FusedP16ProductInsertions",
    "IND_FusedP16ProductWriteCompletions",
    "IND_SoaJitInstructions",
    "IND_SoaJitTerminalCompletions",
    "IND_SoaJitSelected",
    "IND_SoaJitAliasesApplied",
    "IND_SoaJitValueReadIssues",
    "IND_SoaJitValueReadResponses",
    "IND_SoaJitValueFills",
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
)
ZERO_STATS = (
    "IND_FusedP16EpochDrains",
    "IND_FusedP16Fallbacks",
    "IND_FusedP16PublisherLines",
    "IND_FusedP16VirtualPBytes",
    "IND_NumOTEpochDrain",
    "IND_SoaJitEpochDrains",
    "IND_BoundedGlobalMergeFallbacks",
    "STR_PublishIssues",
    "STR_PublishWriteResponses",
    "IND_SoaJitPageFedCoherentIndexReadLines",
    "IND_SoaJitPageFedCoherentIndexWriteLines",
)
REQUIRED_STATS = (*POSITIVE_STATS, *ZERO_STATS)

GUEST_COMPILE_INPUTS = (
    GUEST_SOURCE,
    ROOT / "benchmarks/API/MAA.hpp",
    ROOT / "benchmarks/API/MAA_gem5.hpp",
    ROOT / "include/gem5/m5ops.h",
    ROOT / "include/gem5/asm/generic/m5ops.h",
    ROOT / "include/gem5/maa_page_fed_soa_abi.hh",
    ROOT / "util/m5/src/abi/x86/m5op.S",
)
CONFIG_INPUTS = (
    Path(__file__).resolve(),
    CONFIG,
    RAMULATOR_CONFIG,
    ROOT / "configs/common/__init__.py",
    ROOT / "configs/common/Benchmarks.py",
    ROOT / "configs/common/Options.py",
    ROOT / "configs/common/Simulation.py",
    ROOT / "configs/common/CacheConfig.py",
    ROOT / "configs/common/MemConfig.py",
    ROOT / "configs/common/MAAConfig.py",
    ROOT / "configs/common/MAA.py",
    ROOT / "configs/common/Caches.py",
    ROOT / "configs/common/CpuConfig.py",
    ROOT / "configs/common/HMC.py",
    ROOT / "configs/common/ObjectList.py",
    ROOT / "configs/common/cpu2000.py",
    ROOT / "configs/common/FileSystemConfig.py",
    ROOT / "configs/ruby/__init__.py",
    ROOT / "configs/ruby/Ruby.py",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def exact_hash(path: Path, expected: str, description: str) -> None:
    require(
        path.is_file() and sha256_file(path) == expected,
        f"missing or mismatched {description}: {path}",
    )


def source_status() -> str:
    return subprocess.check_output(
        ["git", "status", "--short", "--branch"], cwd=ROOT, text=True
    )


def source_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def artifact_ledger(paths: Iterable[Path]) -> str:
    return "".join(
        f"{sha256_file(path)}  {path.resolve()}\n" for path in paths
    )


def tree_ledger(root: Path) -> str:
    return "".join(
        f"{sha256_file(path)}  {path.relative_to(root)}\n"
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def run_logged(
    command: list[str], log: Path, environment: dict[str, str]
) -> None:
    with log.open("w") as output:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=False,
        )
    log.with_suffix(log.suffix + ".exit").write_text(
        f"{completed.returncode}\n"
    )
    require(
        completed.returncode == 0,
        f"command failed with {completed.returncode}; see {log}",
    )


def parse_kv(line: str) -> dict[str, str]:
    return dict(re.findall(r"([A-Za-z0-9_]+)=([^ ]+)", line))


def exactly_one(lines: list[str], expression: str, description: str) -> str:
    regex = re.compile(expression)
    matches = [line for line in lines if regex.search(line)]
    require(
        len(matches) == 1,
        f"expected exactly one {description}, found {len(matches)}",
    )
    return matches[0]


def read_stat_sections(stats: Path) -> list[list[tuple[str, int]]]:
    require(stats.is_file() and stats.stat().st_size > 0, "missing stats")
    sections: list[list[tuple[str, int]]] = []
    current: list[tuple[str, int]] | None = None
    for line in stats.read_text(errors="replace").splitlines():
        if line.startswith("---------- Begin Simulation Statistics"):
            require(current is None, "nested stats section")
            current = []
            continue
        if line.startswith("---------- End Simulation Statistics"):
            require(current is not None, "stats end without begin")
            sections.append(current)
            current = None
            continue
        if current is None:
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        try:
            value = int(float(fields[1]))
        except ValueError:
            continue
        current.append((fields[0], value))
    require(current is None and sections, "unterminated or absent stats")
    return sections


def stat_value(section: list[tuple[str, int]], suffix: str) -> int:
    values = [
        value
        for name, value in section
        if name == suffix or name.endswith("_" + suffix)
    ]
    require(values, f"required stat absent or renamed: {suffix}")
    return sum(values)


def required_stat_section(
    section: list[tuple[str, int]], names: tuple[str, ...] = REQUIRED_STATS
) -> dict[str, int]:
    return {name: stat_value(section, name) for name in names}


def require_operation_stats(values: dict[str, int]) -> dict[str, int]:
    issues = values["IND_FusedP16CoefficientReadIssues"]
    require(
        values["simTicks"] > 0
        and values["IND_FusedP16Operations"] == 1
        and values["IND_FusedP16Epochs"] == 1
        and values["IND_FusedP16SourceOrdinals"] == WORDS
        and 1024 <= issues <= WORDS
        and values["IND_FusedP16CoefficientReadResponses"] == issues
        and values["IND_FusedP16CoefficientFills"] == issues
        and values["IND_FusedP16CoefficientDeliveries"] == WORDS
        and values["IND_FusedP16MulAccepts"] == WORDS
        and values["IND_FusedP16MulCompletions"] == WORDS
        and values["IND_FusedP16ProductInsertions"] == WORDS
        and values["IND_FusedP16ProductWriteCompletions"] == WORDS,
        f"fused producer ledger did not close: {values}",
    )
    value_issues = values["IND_SoaJitValueReadIssues"]
    require(
        values["IND_SoaJitInstructions"] == 1
        and values["IND_SoaJitTerminalCompletions"] == 1
        and values["IND_SoaJitSelected"] == WORDS
        and values["IND_SoaJitAliasesApplied"] == WORDS
        and values["IND_SoaJitValueReadResponses"] == value_issues
        and values["IND_SoaJitValueFills"] == value_issues
        and value_issues
        + values["IND_SoaJitValueHits"]
        + values["IND_SoaJitValueMergedWaiters"]
        == WORDS
        and values["IND_SoaJitValueDeliveries"] == WORDS
        and values["IND_SoaJitAReadIssues"] > 0
        and values["IND_SoaJitAReadIssues"]
        == values["IND_SoaJitAReadResponses"]
        and values["IND_SoaJitAReadIssues"] == values["IND_SoaJitAWriteIssues"]
        and values["IND_SoaJitAWriteIssues"]
        == values["IND_SoaJitAWriteResponses"]
        and values["IND_SoaJitPageFedOperations"] == 1
        and values["IND_SoaJitPageFedAdmitCommands"] == PAGES
        and values["IND_SoaJitPageFedCloseCommands"] == 1
        and values["IND_SoaJitPageFedCommandResponses"] == PAGES + 1
        and values["IND_SoaJitPageFedAdmittedWords"] == WORDS
        and values["IND_SoaJitPageFedSpdIndexReads"] == WORDS
        and values["IND_SoaJitPageFedRowWrites"] == WORDS,
        f"q16 terminal ledger did not close: {values}",
    )
    require(
        all(values[name] == 0 for name in ZERO_STATS),
        f"required zero-state schema is nonzero: {values}",
    )
    return values


def operation_sections(stats: Path, expected: int) -> list[dict[str, int]]:
    parsed = [
        required_stat_section(section) for section in read_stat_sections(stats)
    ]
    operations = [
        require_operation_stats(values)
        for values in parsed
        if values["IND_FusedP16Operations"] == 1
    ]
    require(
        len(operations) == expected,
        f"stats contain {len(operations)}/{expected} operation windows",
    )
    nonoperations = [
        values for values in parsed if values["IND_FusedP16Operations"] != 1
    ]
    require(
        len(nonoperations) <= 1
        and all(
            values[name] == 0
            for values in nonoperations
            for name in REQUIRED_STATS
            if name != "simTicks"
        ),
        "stats contain a partial or accumulated operation window",
    )
    return operations


def verify_terminal_source_contract() -> dict[str, bool]:
    indirect = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
    alu = (ROOT / "src/mem/MAA/ALU.cc").read_text()
    maa = (ROOT / "src/mem/MAA/MAA.cc").read_text()
    checks = {
        "one_complete_epoch": "fused_p16_epochs != 1" in indirect,
        "response_owners_empty": "!response_owners_empty" in indirect,
        "combiner_empty": "!virtualCombinerEmpty()" in indirect,
        "coalescer_generation_cleared": (
            "!soa_jit_value_coalescer.clearGeneration(" in indirect
        ),
        "coalescer_invariants": (
            "!soa_jit_value_coalescer.assertInvariants()" in indirect
        ),
        "alu_exact_retirement": "finishDirectPair(" in maa,
        "alu_returns_idle": "state = Status::Idle;" in alu,
        "fresh_internal_generation": (
            "fused_p16_generation = ++fused_p16_generation_counter" in indirect
        ),
    }
    require(
        all(checks.values()), f"terminal source contract changed: {checks}"
    )
    source_diff = subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            PINNED_SIMULATOR_SOURCE,
            "--",
            "src",
            "configs",
            "include",
            "util",
            "benchmarks/API/MAA.hpp",
            "benchmarks/API/MAA_gem5.hpp",
        ],
        cwd=ROOT,
        check=False,
    )
    require(
        source_diff.returncode == 0,
        "current simulator/config/API schema differs from pinned gem5 source",
    )
    return checks


def require_config(config: Path) -> None:
    lines = config.read_text(errors="replace").splitlines()
    required = {
        "page_fed_soa_jit=true",
        "num_maas=1",
        "num_indirect_units_per_maa=1",
        "num_tiles_per_core=8",
        "num_tile_elements=16384",
        "physical_tile_elements=4096",
        "num_offset_table_entries=16384",
        "num_offset_table_epoch_entries=16384",
        "num_initial_row_table_slices=32",
        "virtual_combine_slots=16",
        "virtual_combine_ways=4",
        "virtual_combine_banks=4",
        "virtual_words_per_cycle=1",
        "virtual_response_slots=8",
        "virtual_response_words=0",
        "virtual_response_word_pool=0",
        "virtual_max_outstanding_writes=32",
        "soa_jit_value_cache_enable=true",
        "soa_jit_active_value_owners=32",
        "soa_jit_value_prefetch_credits=0",
    }
    require(
        not required.difference(lines),
        f"resolved config missing {sorted(required.difference(lines))}",
    )


def parse_case(case: Path, repeats: int) -> dict:
    lines = (case / "restore.log").read_text(errors="replace").splitlines()
    require(
        not any(FATAL_RE.search(line) for line in lines),
        f"repeat-{repeats} contains fatal text",
    )
    exactly_one(
        lines,
        r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$",
        f"repeat-{repeats} m5_exit terminal",
    )
    result = parse_kv(
        exactly_one(
            lines,
            rf"^FUSED_P16_REPEAT_RESULT operations={repeats} "
            rf"completed={repeats} .* errors=0$",
            f"repeat-{repeats} guest terminal",
        )
    )
    progress = [
        parse_kv(line)
        for line in lines
        if line.startswith("FUSED_P16_REPEAT_PROGRESS ")
    ]
    require(
        len(progress) == repeats,
        f"repeat-{repeats} has {len(progress)}/{repeats} progress markers",
    )
    producer_tokens = {int(fields["producer_token"]) for fields in progress}
    require(len(producer_tokens) == 1, "producer token changed within a case")
    input_hashes: list[str] = []
    for operation, fields in enumerate(progress, 1):
        require(
            fields.get("operation") == f"{operation}/{repeats}"
            and int(fields["producer_generation"]) == operation
            and int(fields["q_generation"]) == operation
            and fields["reference_hash"] == fields["product_hash"]
            and fields["product_hash"] == fields["q_hash"]
            and int(fields["errors"]) == 0,
            f"repeat-{repeats} operation {operation} marker failed: {fields}",
        )
        input_hashes.append(fields["input_hash"])
    require(
        len(set(input_hashes)) == repeats,
        f"repeat-{repeats} deterministic input hashes are not distinct",
    )
    stats = operation_sections(case / "stats.txt", repeats)
    require_config(case / "config.ini")
    return {
        "operations": repeats,
        "terminal": True,
        "producer_token": producer_tokens.pop(),
        "distinct_producer_generations": True,
        "distinct_q_generations": True,
        "rolling_hash": result["rolling_hash"],
        "progress": progress,
        "operation_stats": stats,
        "total_simTicks": sum(values["simTicks"] for values in stats),
    }


def classify_scaling(cases: dict[str, dict]) -> dict:
    totals = {
        int(name): case["total_simTicks"] for name, case in cases.items()
    }
    per_operation = {size: totals[size] / size for size in CASES}
    ratios = {
        "32_over_16": totals[32] / totals[16],
        "64_over_32": totals[64] / totals[32],
    }
    normalized_drift = (
        max(per_operation.values()) / min(per_operation.values()) - 1
    )
    within_case_tail_growth = {}
    for size in CASES:
        ticks = [
            entry["simTicks"] for entry in cases[str(size)]["operation_stats"]
        ]
        quarter = size // 4
        first = sum(ticks[:quarter]) / quarter
        last = sum(ticks[-quarter:]) / quarter
        within_case_tail_growth[str(size)] = last / first
    linear = (
        1.8 <= ratios["32_over_16"] <= 2.2
        and 1.8 <= ratios["64_over_32"] <= 2.2
        and normalized_drift <= 0.10
        and max(within_case_tail_growth.values()) <= 1.10
    )
    return {
        "metric": "sum_of_per_operation_simTicks",
        "totals": totals,
        "per_operation": per_operation,
        "ratios": ratios,
        "normalized_drift": normalized_drift,
        "within_case_last_quarter_over_first": within_case_tail_growth,
        "linear": linear,
        "state_leak": False,
        "classification": (
            "LINEAR_NO_STATE_LEAK"
            if linear
            else "NONLINEAR_COST_WITHOUT_TERMINAL_STATE_LEAK"
        ),
    }


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
    guest: Path, selector: Path, checkpoint: Path, case: Path
) -> list[str]:
    return [
        str(GEM5),
        "--listener-mode=off",
        f"--outdir={case}",
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
        "--maa_num_indirect_units_per_maa=1",
        "--maa_num_tiles_per_core=8",
        "--maa_num_tile_elements=16384",
        "--maa_physical_tile_elements=4096",
        "--maa_num_offset_table_entries=16384",
        "--maa_num_offset_table_epoch_entries=16384",
        "--maa_num_initial_row_table_slices=32",
        "--maa_virtual_combine_slots=16",
        "--maa_virtual_combine_ways=4",
        "--maa_virtual_combine_banks=4",
        "--maa_virtual_words_per_cycle=1",
        "--maa_virtual_response_slots=8",
        "--maa_virtual_response_words=0",
        "--maa_virtual_response_word_pool=0",
        "--maa_virtual_max_outstanding_writes=32",
        "--maa_page_fed_soa_jit",
        "--maa_soa_jit_value_cache_enable",
        "--maa_soa_jit_active_value_owners=32",
        "--maa_soa_jit_value_prefetch_credits=0",
        "--cmd",
        str(guest),
        "--options",
        f"MAA_DEFERRED {selector}",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    out = args.out.resolve()
    require(
        out != ROOT and ROOT not in out.parents,
        "output must be outside source",
    )
    require(
        not out.exists() or not any(out.iterdir()),
        f"refusing nonempty output: {out}",
    )
    exact_hash(GEM5, GEM5_SHA256, "pinned repaired gem5")
    exact_hash(RAMULATOR, RAMULATOR_SHA256, "pinned Ramulator")
    terminal_contract = verify_terminal_source_contract()
    before_status = source_status()
    require(
        len(before_status.splitlines()) == 1,
        "refusing evidence from a dirty source worktree",
    )
    before_commit = source_commit()

    input_dir = out / "input"
    checkpoint = out / "checkpoint"
    input_dir.mkdir(parents=True)
    checkpoint.mkdir()
    selector = input_dir / "repeat.selector"
    selector.write_text("16\n")
    selector.chmod(0o444)
    guest = out / "fused_p16_repeat_liveness_guest"
    compile_args = [
        os.environ.get("CXX", "g++"),
        f"-I{ROOT / 'benchmarks/API'}",
        f"-I{ROOT / 'include'}",
        f"-I{ROOT / 'util/m5/src'}",
        "-std=c++17",
        "-O3",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wno-ignored-qualifiers",
        "-Wno-unused-parameter",
        "-DGEM5",
        "-DMAA",
        "-DNUM_CORES=4",
        "-DNUM_TILES_PER_CORE=8",
        "-DTILE_SIZE=16384",
        "-DMAA_MEM_SIZE=0x80000000",
        str(ROOT / "util/m5/src/abi/x86/m5op.S"),
        str(GUEST_SOURCE),
        "-o",
        str(guest),
    ]
    subprocess.run(compile_args, cwd=ROOT, check=True)

    immutable = (GEM5, RAMULATOR, guest, *GUEST_COMPILE_INPUTS, *CONFIG_INPUTS)
    artifacts_before = artifact_ledger(immutable)
    (input_dir / "artifact_sha256.before").write_text(artifacts_before)
    (input_dir / "source_status.before").write_text(before_status)
    (input_dir / "source_commit.before").write_text(before_commit + "\n")
    (input_dir / "compile_command.json").write_text(
        json.dumps(compile_args, indent=2) + "\n"
    )

    environment = dict(
        os.environ,
        LD_LIBRARY_PATH=str(RAMULATOR.parent)
        + ":"
        + os.environ.get("LD_LIBRARY_PATH", ""),
        OMP_NUM_THREADS="1",
        OMP_PROC_BIND="false",
    )
    ldd = subprocess.check_output(
        ["ldd", str(GEM5)], env=environment, text=True
    )
    match = re.search(r"^[ \t]*libramulator\.so => (\S+)", ldd, re.M)
    require(
        match is not None
        and Path(match.group(1)).resolve() == RAMULATOR.resolve(),
        "pinned gem5 did not resolve pinned Ramulator",
    )

    checkpoint_args = checkpoint_command(guest, selector, checkpoint)
    (input_dir / "checkpoint_command.json").write_text(
        json.dumps(checkpoint_args, indent=2) + "\n"
    )
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
            line.startswith("FUSED_P16_REPEAT_PROGRESS ")
            for line in checkpoint_lines
        ),
        "checkpoint crossed the repeat selector boundary",
    )
    checkpoint_before = tree_ledger(checkpoint)
    (input_dir / "checkpoint_files.before").write_text(checkpoint_before)

    cases: dict[str, dict] = {}
    commands: dict[str, list[str]] = {}
    canonical_progress: list[dict[str, str]] | None = None
    for repeats in CASES:
        selector.chmod(0o644)
        selector.write_text(f"{repeats}\n")
        selector.chmod(0o444)
        case = out / f"repeat_{repeats}"
        case.mkdir()
        (case / "selector.txt").write_text(selector.read_text())
        command = restore_command(guest, selector, checkpoint, case)
        commands[str(repeats)] = command
        (case / "restore_command.json").write_text(
            json.dumps(command, indent=2) + "\n"
        )
        run_logged(command, case / "restore.log", environment)
        parsed = parse_case(case, repeats)
        progress = parsed["progress"]
        if canonical_progress is not None:
            for index, expected in enumerate(canonical_progress):
                actual = progress[index]
                for field in (
                    "input_hash",
                    "reference_hash",
                    "product_hash",
                    "q_hash",
                    "producer_token",
                    "producer_generation",
                    "q_generation",
                    "errors",
                ):
                    require(
                        actual[field] == expected[field],
                        f"repeat-{repeats} deterministic prefix differs at "
                        f"operation {index + 1} field {field}",
                    )
        canonical_progress = progress
        cases[str(repeats)] = parsed
        (out / "campaign.progress.json").write_text(
            json.dumps(
                {
                    "terminal_cases": [int(name) for name in cases],
                    "next_case": next(
                        (size for size in CASES if str(size) not in cases),
                        None,
                    ),
                },
                indent=2,
            )
            + "\n"
        )

    checkpoint_after = tree_ledger(checkpoint)
    (input_dir / "checkpoint_files.after").write_text(checkpoint_after)
    require(checkpoint_before == checkpoint_after, "shared checkpoint changed")
    artifacts_after = artifact_ledger(immutable)
    (input_dir / "artifact_sha256.after").write_text(artifacts_after)
    require(artifacts_before == artifacts_after, "immutable artifact changed")
    after_status = source_status()
    after_commit = source_commit()
    (input_dir / "source_status.after").write_text(after_status)
    (input_dir / "source_commit.after").write_text(after_commit + "\n")
    require(
        before_status == after_status and before_commit == after_commit,
        "source identity changed during campaign",
    )

    scaling = classify_scaling(cases)
    result = {
        "schema": "dx100.fused_p16_repeat_liveness.v1",
        "terminal": True,
        "decision": "ACCEPT_DIAGNOSTIC",
        "cases": list(CASES),
        "serial_fail_fast": True,
        "timeouts": 0,
        "application_runs": 0,
        "source_commit": before_commit,
        "pinned_simulator_source": PINNED_SIMULATOR_SOURCE,
        "gem5_sha256": sha256_file(GEM5),
        "ramulator_sha256": sha256_file(RAMULATOR),
        "guest_sha256": sha256_file(guest),
        "checkpoint_ledger_sha256": hashlib.sha256(
            checkpoint_before.encode()
        ).hexdigest(),
        "artifact_ledger_sha256": hashlib.sha256(
            artifacts_before.encode()
        ).hexdigest(),
        "required_zero_stat_schema": list(ZERO_STATS),
        "required_zero_stat_schema_present": True,
        "terminal_state_contract": terminal_contract,
        "operation_contract": {
            "producer_token_waited": True,
            "distinct_internal_generations": True,
            "p16_epoch_per_operation": 1,
            "words_per_operation": WORDS,
            "q_terminal_per_operation": True,
            "coalescer_combiner_alu_empty_per_operation": True,
            "drains_fallbacks_publisher_virtual_p": 0,
        },
        "scaling": scaling,
        "restore_commands": commands,
        "results": cases,
    }
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    ledger_targets = [
        path
        for path in sorted(out.rglob("*"))
        if path.is_file()
        and path.name not in {"raw_root.sha256", "gate.complete"}
    ]
    (out / "raw_root.sha256").write_text(
        "".join(
            f"{sha256_file(path)}  {path.relative_to(out)}\n"
            for path in ledger_targets
        )
    )
    raw_root_sha256 = sha256_file(out / "raw_root.sha256")
    (out / "gate.complete").write_text(
        "schema=dx100.fused_p16_repeat_liveness.gate.v1\n"
        "terminal=PASS\n"
        "decision=ACCEPT_DIAGNOSTIC\n"
        f"classification={scaling['classification']}\n"
        "completed_cases=16,32,64\n"
        f"raw_root_sha256={raw_root_sha256}\n"
    )
    print(
        json.dumps(
            {
                "terminal": True,
                "decision": result["decision"],
                "classification": scaling["classification"],
                "total_simTicks": scaling["totals"],
                "raw_root_sha256": raw_root_sha256,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
