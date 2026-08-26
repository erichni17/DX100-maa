#!/usr/bin/env python3
"""Matched shared-checkpoint CG reduction-order diagnosis.

This runner is intentionally diagnostic-only.  It builds one four-thread
deterministic-reduction guest for an explicit, bounded CG_NA, creates one
deferred checkpoint, and restores that checkpoint for the physical-product and
page-fed treatments.  It never runs a native arm or a full CG input.
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
CONFIG = ROOT / "configs/deprecated/example/se.py"
RAMULATOR_CONFIG = ROOT / "ext/ramulator2/ramulator2/example_gem5_config.yaml"
SOURCE = ROOT / "benchmarks/NAS/cg/cg.cpp"
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
RUNNER_CONFIG_INPUTS = (
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
MAX_DIAGNOSTIC_CG_NA = 32768
TREATMENTS = (
    ("physical", "physical_page_product_soa_jit", False),
    ("page_fed", "page_fed_product_soa_jit", True),
)
FATAL_RE = re.compile(
    r"panic|fatal|assert|abort|segmentation fault|error:", re.IGNORECASE
)
EVIDENCE_RE = re.compile(
    r"^CG_REDUCTION_EVIDENCE phase=(initial_rho|d|rho|final_sum) "
    r"cgit=([0-4]) order=0,1,2,3 "
    r"p0=([0-9a-f]{8}) p1=([0-9a-f]{8}) "
    r"p2=([0-9a-f]{8}) p3=([0-9a-f]{8}) "
    r"result=([0-9a-f]{8})(?: (alpha|beta)=([0-9a-f]{8}))?$"
)
OUTER_EVIDENCE_RE = re.compile(
    r"^CG_OUTER_REDUCTION_EVIDENCE it=1 order=0,1,2,3 "
    r"xz0=[0-9a-f]{16} zz0=[0-9a-f]{16} "
    r"xz1=[0-9a-f]{16} zz1=[0-9a-f]{16} "
    r"xz2=[0-9a-f]{16} zz2=[0-9a-f]{16} "
    r"xz3=[0-9a-f]{16} zz3=[0-9a-f]{16} "
    r"xz_result=[0-9a-f]{16} zz_result=[0-9a-f]{16} "
    r"norm_scale=[0-9a-f]{16} zeta=[0-9a-f]{16}$"
)


def sha256_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def exact_hash(path: Path, expected: str, description: str) -> None:
    if not path.is_file() or sha256_file(path) != expected:
        raise RuntimeError(f"missing or mismatched {description}: {path}")


def source_status() -> str:
    return subprocess.check_output(
        ["git", "status", "--short", "--branch"], cwd=ROOT, text=True
    )


def source_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def run_logged(
    args: list[str], log: Path, environment: dict[str, str]
) -> None:
    with log.open("w") as output:
        completed = subprocess.run(
            args,
            cwd=ROOT,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=False,
        )
    log.with_suffix(log.suffix + ".exit").write_text(
        f"{completed.returncode}\n"
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed with {completed.returncode}; see {log}"
        )


def tree_ledger(root: Path) -> str:
    records = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            records.append(f"{sha256_file(path)}  {path.relative_to(root)}")
    return "\n".join(records) + "\n"


def artifact_ledger(paths: Iterable[Path]) -> str:
    return "".join(f"{sha256_file(path)}  {path}\n" for path in paths)


def parse_kv(line: str) -> dict[str, str]:
    return dict(re.findall(r"([A-Za-z0-9_]+)=([^ ]+)", line))


def exactly_one(lines: list[str], expression: str, description: str) -> str:
    regex = re.compile(expression)
    matches = [line for line in lines if regex.search(line)]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one {description}, found {len(matches)}"
        )
    return matches[0]


def stat_sum(stats: Path, suffix: str) -> int:
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
    if not found:
        raise RuntimeError(f"missing first-window stat {suffix} in {stats}")
    return total


def expected_evidence_shape(lines: list[str]) -> None:
    parsed = []
    for line in lines:
        match = EVIDENCE_RE.fullmatch(line)
        if match is None:
            raise RuntimeError(f"malformed reduction evidence: {line}")
        parsed.append((match.group(1), int(match.group(2)), match.group(8)))
    expected = [("initial_rho", 0, None)]
    for cgit in range(1, 5):
        expected.extend((("d", cgit, "alpha"), ("rho", cgit, "beta")))
    expected.append(("final_sum", 0, None))
    if parsed != expected:
        raise RuntimeError(
            f"reduction evidence phase/order mismatch: {parsed!r}"
        )


def expected_outer_evidence_shape(lines: list[str]) -> None:
    if len(lines) != 1 or OUTER_EVIDENCE_RE.fullmatch(lines[0]) is None:
        raise RuntimeError(f"outer reduction evidence mismatch: {lines!r}")


def require_config(config: Path, page_fed: bool) -> None:
    text = config.read_text(errors="replace").splitlines()
    required = {
        f"page_fed_soa_jit={'true' if page_fed else 'false'}",
        "num_maas=1",
        "num_indirect_units_per_maa=4",
        "num_tiles_per_core=10",
        "num_tile_elements=16384",
        "physical_tile_elements=4096",
        "num_offset_table_entries=16384",
        "num_offset_table_epoch_entries=16384",
        "num_initial_row_table_slices=32",
        "soa_jit_predicate_active_credits=16",
        "soa_jit_active_value_owners=32",
    }
    missing = sorted(required.difference(text))
    if missing:
        raise RuntimeError(f"resolved config missing {missing}")
    controllers = sum(
        bool(re.fullmatch(r"\[system\.mem_ctrls[01]\]", line)) for line in text
    )
    if controllers != 2:
        raise RuntimeError(
            f"expected exactly two memory channels, saw {controllers}"
        )


def require_terminal(fields: dict[str, str], treatment: str) -> int:
    keys = (
        "full_windows",
        "staged_index_words",
        "staged_value_words",
        "product_words",
        "index_publish_pages",
        "value_publish_pages",
        "product_publish_pages",
        "logical_alu_vectors",
        "physical_alu_vectors",
        "logical_page_windows",
        "physical_page_product_windows",
        "page_fed_product_windows",
        "page_fed_admit_pages",
        "page_fed_closes",
        "q_spmv_eligible_windows",
        "q_spmv_routed_windows",
        "residual_spmv_eligible_windows",
        "residual_spmv_routed_windows",
    )
    try:
        values = {key: int(fields[key]) for key in keys}
    except (KeyError, ValueError) as error:
        raise RuntimeError(f"incomplete terminal: {error}") from error
    windows = values["full_windows"]
    pages = windows * 4
    words = windows * 16384
    common = (
        windows > 0
        and values["staged_index_words"] == words
        and values["staged_value_words"] == 0
        and values["product_words"] == words
        and values["value_publish_pages"] == 0
        and values["product_publish_pages"] == pages
        and values["logical_alu_vectors"] == 0
        and values["physical_alu_vectors"] == pages
        and values["logical_page_windows"] == 0
        and values["q_spmv_eligible_windows"]
        == values["q_spmv_routed_windows"]
        and values["residual_spmv_eligible_windows"]
        == values["residual_spmv_routed_windows"]
        and values["q_spmv_routed_windows"]
        + values["residual_spmv_routed_windows"]
        == windows
    )
    if treatment == "physical_page_product_soa_jit":
        exact = (
            values["index_publish_pages"] == pages
            and values["physical_page_product_windows"] == windows
            and values["page_fed_product_windows"] == 0
            and values["page_fed_admit_pages"] == 0
            and values["page_fed_closes"] == 0
        )
    else:
        exact = (
            values["index_publish_pages"] == 0
            and values["physical_page_product_windows"] == 0
            and values["page_fed_product_windows"] == windows
            and values["page_fed_admit_pages"] == pages
            and values["page_fed_closes"] == windows
        )
    if not common or not exact:
        raise RuntimeError(
            f"terminal closure failed for {treatment}: {values}"
        )
    return windows


def require_stats(stats: Path, windows: int, page_fed: bool) -> dict[str, int]:
    names = (
        "simTicks",
        "IND_SoaJitInstructions",
        "IND_SoaJitTerminalCompletions",
        "IND_SoaJitSelected",
        "IND_SoaJitAliasesApplied",
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
        "IND_SoaJitEpochDrains",
        "IND_BoundedGlobalMergeFallbacks",
        "STR_PublishIssues",
        "STR_PublishAccepts",
        "STR_PublishWriteResponses",
        "STR_PublishTerminals",
    )
    values = {name: stat_sum(stats, name) for name in names}
    words = windows * 16384
    pages = windows * 4
    publisher_lines = pages * 256 * (1 if page_fed else 2)
    a_lines = values["IND_SoaJitAReadIssues"]
    closed = (
        values["simTicks"] > 0
        and values["IND_SoaJitInstructions"] == windows
        and values["IND_SoaJitTerminalCompletions"] == windows
        and values["IND_SoaJitSelected"] == words
        and values["IND_SoaJitAliasesApplied"] == words
        and values["IND_SoaJitValueDeliveries"] == words
        and values["IND_SoaJitValueReadIssues"] > 0
        and values["IND_SoaJitValueReadIssues"]
        == values["IND_SoaJitValueReadResponses"]
        and values["IND_SoaJitValueReadResponses"]
        == values["IND_SoaJitValueFills"]
        and values["IND_SoaJitValueCachedResponses"]
        <= values["IND_SoaJitValueFills"]
        and values["IND_SoaJitValueReadIssues"]
        + values["IND_SoaJitValueHits"]
        + values["IND_SoaJitValueMergedWaiters"]
        == words
        and a_lines > 0
        and values["IND_SoaJitAReadResponses"] == a_lines
        and values["IND_SoaJitAWriteIssues"] == a_lines
        and values["IND_SoaJitAWriteResponses"] == a_lines
        and values["IND_SoaJitEpochDrains"] == 0
        and values["IND_BoundedGlobalMergeFallbacks"] == 0
        and values["STR_PublishIssues"] == publisher_lines
        and values["STR_PublishAccepts"] == publisher_lines
        and values["STR_PublishWriteResponses"] == publisher_lines
        and values["STR_PublishTerminals"] == pages * (1 if page_fed else 2)
    )
    if page_fed:
        closed = closed and (
            values["IND_SoaJitPageFedOperations"] == windows
            and values["IND_SoaJitPageFedAdmitCommands"] == pages
            and values["IND_SoaJitPageFedCloseCommands"] == windows
        )
    else:
        closed = closed and all(
            values[key] == 0
            for key in (
                "IND_SoaJitPageFedOperations",
                "IND_SoaJitPageFedAdmitCommands",
                "IND_SoaJitPageFedCloseCommands",
            )
        )
    if not closed:
        raise RuntimeError(f"stats closure failed: {values}")
    return values


def parse_arm(arm: Path, cg_na: int, treatment: str, page_fed: bool) -> dict:
    restore = arm / "restore.log"
    lines = restore.read_text(errors="replace").splitlines()
    if any(FATAL_RE.search(line) for line in lines):
        raise RuntimeError(f"{arm}: fatal text in restore log")
    exactly_one(
        lines,
        r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$",
        "m5 terminal",
    )
    if sum(line == "ROI End!!!" for line in lines) != 1:
        raise RuntimeError(f"{arm}: expected exactly one ROI terminal")
    fingerprint_line = exactly_one(
        lines,
        rf"^CG_FINGERPRINT mode=MAA elements={cg_na} .* result=PASS$",
        "passing fingerprint",
    )
    terminal_line = exactly_one(
        lines,
        rf"^CG_LOGICAL16_RMW_TERMINAL treatment={treatment} .* result=PASS$",
        "passing treatment terminal",
    )
    inner_evidence = [
        line for line in lines if line.startswith("CG_REDUCTION_EVIDENCE ")
    ]
    outer_evidence = [
        line
        for line in lines
        if line.startswith("CG_OUTER_REDUCTION_EVIDENCE ")
    ]
    expected_evidence_shape(inner_evidence)
    expected_outer_evidence_shape(outer_evidence)
    evidence = [
        line
        for line in lines
        if line.startswith(
            ("CG_REDUCTION_EVIDENCE ", "CG_OUTER_REDUCTION_EVIDENCE ")
        )
    ]
    if evidence != inner_evidence + outer_evidence:
        raise RuntimeError(
            "outer reduction evidence did not follow inner evidence"
        )
    windows = require_terminal(parse_kv(terminal_line), treatment)
    stats = arm / "stats.txt"
    if not stats.is_file() or stats.stat().st_size == 0:
        raise RuntimeError(f"{arm}: missing nonempty final stats")
    require_config(arm / "config.ini", page_fed)
    stats_values = require_stats(stats, windows, page_fed)
    if any(
        path.name not in {"restore.log"} for path in arm.glob("*trace*.log")
    ):
        raise RuntimeError(f"{arm}: per-access trace artifact is forbidden")
    return {
        "fingerprint_line": fingerprint_line,
        "fingerprint": parse_kv(fingerprint_line),
        "reduction_evidence": evidence,
        "terminal": parse_kv(terminal_line),
        "stats": stats_values,
    }


def restore_args(
    guest: Path, selector: Path, checkpoint: Path, arm: Path, page_fed: bool
) -> list[str]:
    args = [
        str(GEM5),
        "--listener-mode=off",
        f"--outdir={arm}",
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
        "--maa_num_tiles_per_core=10",
        "--maa_num_tile_elements=16384",
        "--maa_physical_tile_elements=4096",
        "--maa_num_offset_table_entries=16384",
        "--maa_num_offset_table_epoch_entries=16384",
        "--maa_num_initial_row_table_slices=32",
        "--maa_soa_jit_predicate_active_credits=16",
        "--maa_soa_jit_active_value_owners=32",
    ]
    if page_fed:
        args.append("--maa_page_fed_soa_jit")
    return args + [
        "--cmd",
        str(guest),
        "--options",
        f"MAA_DEFERRED {selector}",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--cg-na", type=int, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if args.cg_na <= 0 or args.cg_na > MAX_DIAGNOSTIC_CG_NA:
        raise SystemExit(
            f"CG_NA must be in 1..{MAX_DIAGNOSTIC_CG_NA}; full CG is forbidden"
        )
    if out == ROOT or ROOT in out.parents:
        raise SystemExit("output must be outside the source worktree")
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"refusing nonempty output: {out}")

    exact_hash(GEM5, GEM5_SHA256, "frozen page-fed gem5")
    exact_hash(RAMULATOR, RAMULATOR_SHA256, "frozen Ramulator")
    before_status = source_status()
    if len(before_status.splitlines()) != 1:
        raise SystemExit("refusing evidence from a dirty source worktree")
    before_commit = source_commit()

    input_dir = out / "input"
    checkpoint = out / "checkpoint"
    input_dir.mkdir(parents=True)
    checkpoint.mkdir()
    guest = out / "cg_deterministic_reduction_guest"
    selector = input_dir / "treatment.selector"
    selector.write_text("token_stream_ld physical_page_product_soa_jit\n")
    selector.chmod(0o444)

    compile_args = [
        os.environ.get("CXX", "g++"),
        f"-I{ROOT / 'benchmarks/API'}",
        f"-I{ROOT / 'include'}",
        f"-I{ROOT / 'util/m5/src'}",
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
        "-DCG_FP_ENABLE",
        "-DCG_DETERMINISTIC_REDUCTIONS",
        "-DCG_REDUCTION_EVIDENCE",
        f"-DCG_NA={args.cg_na}",
        "-DNUM_CORES=4",
        "-DNUM_TILES_PER_CORE=10",
        "-DTILE_SIZE=16384",
        "-DMAA_MEM_SIZE=0x80000000",
        str(ROOT / "util/m5/src/abi/x86/m5op.S"),
        str(SOURCE),
        "-o",
        str(guest),
    ]
    subprocess.run(compile_args, cwd=ROOT, check=True)

    immutable_artifacts = (
        GEM5,
        RAMULATOR,
        guest,
        *GUEST_COMPILE_INPUTS,
        *RUNNER_CONFIG_INPUTS,
    )
    (input_dir / "artifact_sha256.before").write_text(
        artifact_ledger(immutable_artifacts)
    )
    (input_dir / "source_status.before").write_text(before_status)
    (input_dir / "source_commit.before").write_text(before_commit + "\n")
    (input_dir / "compile_command.json").write_text(
        json.dumps(compile_args, indent=2) + "\n"
    )

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
    resolved_match = re.search(r"^libramulator\.so => (\S+)", ldd_output, re.M)
    if (
        resolved_match is None
        or Path(resolved_match.group(1)).resolve() != RAMULATOR.resolve()
    ):
        raise RuntimeError(
            "archived gem5 did not resolve the frozen Ramulator"
        )
    checkpoint_args = [
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
    if any(
        line.startswith(
            (
                "CG_REDUCTION_EVIDENCE ",
                "CG_OUTER_REDUCTION_EVIDENCE ",
                "CG_FINGERPRINT ",
                "CG_LOGICAL16_RMW_TERMINAL ",
            )
        )
        for line in checkpoint_lines
    ):
        raise RuntimeError(
            "checkpoint crossed the deferred treatment boundary"
        )
    checkpoint_before = tree_ledger(checkpoint)
    (input_dir / "checkpoint_files.before").write_text(checkpoint_before)

    parsed = {}
    restore_commands = {}
    for arm_name, treatment, page_fed in TREATMENTS:
        selector.chmod(0o644)
        selector.write_text(f"token_stream_ld {treatment}\n")
        selector.chmod(0o444)
        arm = out / arm_name
        arm.mkdir()
        (arm / "selector.txt").write_text(selector.read_text())
        restore = restore_args(guest, selector, checkpoint, arm, page_fed)
        restore_commands[arm_name] = restore
        run_logged(restore, arm / "restore.log", environment)
        parsed[arm_name] = parse_arm(arm, args.cg_na, treatment, page_fed)

    fingerprint_equal = (
        parsed["physical"]["fingerprint_line"]
        == parsed["page_fed"]["fingerprint_line"]
    )
    physical_evidence = parsed["physical"]["reduction_evidence"]
    page_fed_evidence = parsed["page_fed"]["reduction_evidence"]
    reduction_evidence_equal = physical_evidence == page_fed_evidence
    first_evidence_difference = next(
        (
            {
                "record": index,
                "physical": physical_line,
                "page_fed": page_fed_line,
            }
            for index, (physical_line, page_fed_line) in enumerate(
                zip(physical_evidence, page_fed_evidence)
            )
            if physical_line != page_fed_line
        ),
        None,
    )
    physical_terminal = parsed["physical"]["terminal"]
    page_terminal = parsed["page_fed"]["terminal"]
    if physical_terminal["full_windows"] != page_terminal["full_windows"]:
        raise RuntimeError("treatment window counts differ")

    checkpoint_after = tree_ledger(checkpoint)
    (input_dir / "checkpoint_files.after").write_text(checkpoint_after)
    if checkpoint_before != checkpoint_after:
        raise RuntimeError("shared checkpoint changed during restores")
    after_artifacts = artifact_ledger(immutable_artifacts)
    (input_dir / "artifact_sha256.after").write_text(after_artifacts)
    if (input_dir / "artifact_sha256.before").read_text() != after_artifacts:
        raise RuntimeError("immutable artifact changed during diagnosis")
    after_status = source_status()
    after_commit = source_commit()
    (input_dir / "source_status.after").write_text(after_status)
    (input_dir / "source_commit.after").write_text(after_commit + "\n")
    if after_status != before_status or after_commit != before_commit:
        raise RuntimeError("source identity changed during diagnosis")

    result = {
        "schema": "dx100.cg.page_fed_reduction_order_diagnosis.v1",
        "terminal": True,
        "diagnostic_only": True,
        "native_runs": 0,
        "full_cg": False,
        "timeout": "none",
        "per_memory_access_traces": False,
        "cg_na": args.cg_na,
        "source_commit": before_commit,
        "gem5_sha256": GEM5_SHA256,
        "ramulator_sha256": RAMULATOR_SHA256,
        "guest_sha256": sha256_file(guest),
        "checkpoint_ledger_sha256": hashlib.sha256(
            checkpoint_before.encode()
        ).hexdigest(),
        "restore_commands": restore_commands,
        "fingerprint_exact_equal": fingerprint_equal,
        "reduction_partial_and_downstream_bits_exact_equal": (
            reduction_evidence_equal
        ),
        "first_reduction_evidence_difference": first_evidence_difference,
        "fingerprints": {name: parsed[name]["fingerprint"] for name in parsed},
        "reduction_evidence": {
            name: parsed[name]["reduction_evidence"] for name in parsed
        },
        "arms": parsed,
    }
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    ledger_targets = [
        path
        for path in sorted(out.rglob("*"))
        if path.is_file()
        and path.name not in {"raw_root.sha256", "gate.complete"}
    ]
    raw_ledger = "".join(
        f"{sha256_file(path)}  {path.relative_to(out)}\n"
        for path in ledger_targets
    )
    (out / "raw_root.sha256").write_text(raw_ledger)
    ledger_sha = sha256_file(out / "raw_root.sha256")
    outcome = (
        "MATCH" if fingerprint_equal and reduction_evidence_equal else "DIFFER"
    )
    (out / "gate.complete").write_text(
        "COMPLETE_REDUCTION_ORDER_DIAGNOSIS\n"
        f"outcome={outcome}\n"
        f"raw_root_sha256={ledger_sha}\n"
    )
    print(
        json.dumps(
            {
                "terminal": True,
                "cg_na": args.cg_na,
                "fingerprint_exact_equal": fingerprint_equal,
                "reduction_evidence_exact_equal": reduction_evidence_equal,
                "raw_root_sha256": ledger_sha,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
