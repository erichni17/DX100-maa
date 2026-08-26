#!/usr/bin/env python3
"""Paired, bounded schedule diagnosis for the CG page-fed candidate.

Each CG_NA builds one generic page-fed-capable guest, creates one deferred
checkpoint, then restores that exact checkpoint twice.  The restore pair only
differs in the selector's treatment text and ``--maa_page_fed_soa_jit``.
It is deliberately diagnostic evidence, never a performance gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GEM5 = Path(
    "/data1/nier/dx100-binaries/gem5-page-fed-606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427.opt"
)
GEM5_SHA = "606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427"
RAMULATOR = Path(
    "/data1/nier/dx100-runs/2026-08-12-hybrid-line-handoff-8a5c7712/input/libramulator.so"
)
RAMULATOR_SHA = (
    "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753"
)
SIZES = (1024, 4096, 16384, 32768)
DEBUG_FLAGS = "MAAIssueDigest,MAAMacroEvent"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(
    args: list[str],
    *,
    output: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    if output is None:
        subprocess.run(args, cwd=ROOT, check=True, env=env)
    else:
        with output.open("w") as log:
            subprocess.run(
                args,
                cwd=ROOT,
                check=True,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
            )


def source_status() -> str:
    return subprocess.check_output(
        ["git", "status", "--short", "--branch"], cwd=ROOT, text=True
    )


def parse_kv(line: str) -> dict[str, str]:
    return dict(re.findall(r"([A-Za-z0-9_]+)=([^ ]+)", line))


def one(lines: list[str], pattern: str, description: str) -> str:
    matches = [line for line in lines if re.search(pattern, line)]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {description}, found {len(matches)}")
    return matches[0]


def stat_sum(stats: Path, suffix: str) -> int:
    active = False
    total = 0
    found = False
    for line in stats.read_text(errors="replace").splitlines():
        if line.startswith("---------- Begin Simulation Statistics"):
            active = True
            continue
        if active and line.startswith("---------- End Simulation Statistics"):
            break
        fields = line.split()
        if (
            active
            and len(fields) >= 2
            and (fields[0] == suffix or fields[0].endswith("_" + suffix))
        ):
            total += int(float(fields[1]))
            found = True
    if not found:
        raise RuntimeError(f"missing stats suffix {suffix}")
    return total


def parse_arm(arm: Path, na: int, treatment: str) -> dict[str, object]:
    restore = (arm / "restore.log").read_text(errors="replace").splitlines()
    if any(
        re.search(
            r"panic|fatal|assert|abort|segmentation fault|error:", x, re.I
        )
        for x in restore
    ):
        raise RuntimeError(f"{arm}: simulator error")
    one(
        restore,
        r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$",
        "m5 exit",
    )
    fingerprint = one(
        restore,
        rf"^CG_FINGERPRINT .* elements={na} .* result=PASS$",
        "passing fingerprint",
    )
    terminal = one(
        restore,
        rf"^CG_LOGICAL16_RMW_TERMINAL treatment={treatment} .* result=PASS$",
        "passing treatment terminal",
    )
    issue = (arm / "issue_digest.log").read_text(errors="replace").splitlines()
    issues = [parse_kv(x) for x in issue if " fnv=0x" in x and " count=" in x]
    macros = [parse_kv(x) for x in issue if "event=hybrid_producer_macro" in x]
    if not issues or not macros:
        raise RuntimeError(
            f"{arm}: compact digest instrumentation did not close"
        )
    stats = arm / "stats.txt"
    if not stats.is_file() or stats.stat().st_size == 0:
        raise RuntimeError(f"{arm}: missing final stats")
    terminal_fields = parse_kv(terminal)
    required_terminal = (
        "full_windows",
        "staged_index_words",
        "product_words",
        "product_publish_pages",
        "physical_alu_vectors",
    )
    if any(key not in terminal_fields for key in required_terminal):
        raise RuntimeError(f"{arm}: incomplete treatment terminal")
    return {
        "fingerprint": parse_kv(fingerprint),
        "fingerprint_line": fingerprint,
        "terminal": terminal_fields,
        "issue_digest": issues,
        "macro": macros,
        "stats": {
            key: stat_sum(stats, key)
            for key in (
                "IND_SoaJitInstructions",
                "IND_SoaJitTerminalCompletions",
                "IND_SoaJitSelected",
                "IND_SoaJitAliasesApplied",
                "IND_SoaJitValueReadIssues",
                "IND_SoaJitValueReadResponses",
                "IND_SoaJitAReadIssues",
                "IND_SoaJitAReadResponses",
                "IND_SoaJitAWriteIssues",
                "IND_SoaJitAWriteResponses",
                "IND_SoaJitEpochDrains",
                "IND_BoundedGlobalMergeFallbacks",
                "STR_PublishIssues",
                "STR_PublishAccepts",
                "STR_PublishWriteResponses",
                "STR_PublishTerminals",
            )
        },
        "simTicks": stat_sum(stats, "simTicks"),
    }


def compact(view: dict[str, object]) -> dict[str, object]:
    """Stable evidence projection; raw roots retain the original logs."""
    return {
        "fingerprint": {
            key: view["fingerprint"].get(key)
            for key in ("x_q5", "x_q6", "z_q5", "z_q6", "result")
        },
        "terminal": view["terminal"],
        "source_issue_digest": view["issue_digest"],
        "rowtable_macro_projection": [
            {
                key: event.get(key)
                for key in (
                    "operation_tick",
                    "row_offset_insertions",
                    "offset_pressure_events",
                    "row_pressure_events",
                    "a_lines",
                    "a_bytes",
                    "a_retries",
                )
            }
            for event in view["macro"]
        ],
        "producer_macro": view["macro"],
        "stats": view["stats"],
        "simTicks": view["simTicks"],
    }


def compare(
    physical: dict[str, object], page_fed: dict[str, object]
) -> dict[str, object]:
    # Fingerprints are the localization stop condition.  All remaining fields
    # report exactly which compact stage projection first differs.
    fp_keys = ("x_q5", "x_q6", "z_q5", "z_q6")
    fingerprint_equal = all(
        physical["fingerprint"].get(k) == page_fed["fingerprint"].get(k)
        for k in fp_keys
    )
    source_projection = lambda view: [
        {
            key: value
            for key, value in record.items()
            if key != "instruction_tick"
        }
        for record in view["issue_digest"]
    ]
    source_timing = [
        record.get("instruction_tick") for record in physical["issue_digest"]
    ] == [
        record.get("instruction_tick") for record in page_fed["issue_digest"]
    ]
    physical_lines = physical["stats"]["STR_PublishIssues"]
    page_fed_lines = page_fed["stats"]["STR_PublishIssues"]
    physical_expected_lines = 256 * (
        int(physical["terminal"]["index_publish_pages"])
        + int(physical["terminal"]["product_publish_pages"])
    )
    page_fed_expected_lines = 256 * int(
        page_fed["terminal"]["product_publish_pages"]
    )
    publication_closure = (
        physical_lines == physical_expected_lines
        and page_fed_lines == page_fed_expected_lines
        and physical["stats"]["STR_PublishAccepts"] == physical_lines
        and physical["stats"]["STR_PublishWriteResponses"] == physical_lines
        and page_fed["stats"]["STR_PublishAccepts"] == page_fed_lines
        and page_fed["stats"]["STR_PublishWriteResponses"] == page_fed_lines
        and physical["stats"]["IND_SoaJitValueReadResponses"]
        == page_fed["stats"]["IND_SoaJitValueReadResponses"]
    )
    return {
        "quantized_fingerprint_equal": fingerprint_equal,
        "source_issue_order_digest_equal": source_projection(physical)
        == source_projection(page_fed),
        "source_issue_timing_equal": source_timing,
        "rowtable_admission_projection_equal": (
            [
                {
                    key: event.get(key)
                    for key in (
                        "row_offset_insertions",
                        "offset_pressure_events",
                        "row_pressure_events",
                        "a_lines",
                        "a_bytes",
                        "a_retries",
                    )
                }
                for event in physical["macro"]
            ]
            == [
                {
                    key: event.get(key)
                    for key in (
                        "row_offset_insertions",
                        "offset_pressure_events",
                        "row_pressure_events",
                        "a_lines",
                        "a_bytes",
                        "a_retries",
                    )
                }
                for event in page_fed["macro"]
            ]
        ),
        "a_line_and_alias_closure_equal": all(
            physical["stats"][key] == page_fed["stats"][key]
            for key in (
                "IND_SoaJitAliasesApplied",
                "IND_SoaJitAReadIssues",
                "IND_SoaJitAReadResponses",
                "IND_SoaJitAWriteIssues",
                "IND_SoaJitAWriteResponses",
            )
        ),
        "product_publication_value_delivery_closes": publication_closure,
        "physical_publisher_lines": physical_lines,
        "page_fed_publisher_lines": page_fed_lines,
        "epoch_drain_equal": physical["stats"]["IND_SoaJitEpochDrains"]
        == page_fed["stats"]["IND_SoaJitEpochDrains"],
    }


def restore_args(
    guest: Path, selector: Path, checkpoint: Path, arm: Path, page_fed: bool
) -> list[str]:
    args = [
        str(GEM5),
        "--listener-mode=off",
        f"--outdir={arm}",
        f"--debug-flags={DEBUG_FLAGS}",
        "--debug-file=issue_digest.log",
        str(ROOT / "configs/deprecated/example/se.py"),
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
        str(ROOT / "ext/ramulator2/ramulator2/example_gem5_config.yaml"),
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


def run_size(out: Path, na: int, status_before: str) -> dict[str, object]:
    case = out / f"na{na}"
    (case / "input").mkdir(parents=True)
    guest = case / "guest"
    # The deferred guest checkpoints this pathname before it reads the
    # selector.  Restoring with another --options string does not replace it;
    # treatment is therefore the one permitted input delta, atomically applied
    # at this same checkpointed selector path between the serial arms.
    selector = case / "input/treatment.selector"
    selector.write_text("token_stream_ld physical_page_product_soa_jit\n")
    selector.chmod(0o444)
    compile_args = [
        "g++",
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
        f"-DCG_NA={na}",
        "-DNUM_CORES=4",
        "-DNUM_TILES_PER_CORE=10",
        "-DTILE_SIZE=16384",
        "-DMAA_MEM_SIZE=0x80000000",
        str(ROOT / "util/m5/src/abi/x86/m5op.S"),
        str(ROOT / "benchmarks/NAS/cg/cg.cpp"),
        "-o",
        str(guest),
    ]
    command(compile_args)
    checkpoint = case / "checkpoint"
    checkpoint.mkdir()
    checkpoint_cmd = [
        str(GEM5),
        "--listener-mode=off",
        f"--outdir={checkpoint}",
        str(ROOT / "configs/deprecated/example/se.py"),
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
    environments = dict(os.environ, LD_LIBRARY_PATH=str(RAMULATOR.parent))
    command(checkpoint_cmd, output=case / "checkpoint.log", env=environments)
    if not re.search(
        r"^Exiting @ tick [0-9]+ because checkpoint$",
        (case / "checkpoint.log").read_text(errors="replace"),
        re.M,
    ):
        raise RuntimeError(f"NA={na}: deferred checkpoint did not close")
    checkpoint_hash = hashlib.sha256(
        "".join(
            f"{p.relative_to(checkpoint)}:{digest(p)}\n"
            for p in sorted(checkpoint.rglob("*"))
            if p.is_file()
        ).encode()
    ).hexdigest()
    # Subprocess inherits only the needed frozen-library search prefix.
    for arm_name, selection, enabled in (
        ("physical", "physical_page_product_soa_jit", False),
        ("page_fed", "page_fed_product_soa_jit", True),
    ):
        selector.chmod(0o644)
        selector.write_text(f"token_stream_ld {selection}\n")
        selector.chmod(0o444)
        arm = case / arm_name
        arm.mkdir()
        with (arm / "restore.log").open("w") as log:
            subprocess.run(
                restore_args(guest, selector, checkpoint, arm, enabled),
                cwd=ROOT,
                check=True,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=environments,
            )
    physical = parse_arm(
        case / "physical", na, "physical_page_product_soa_jit"
    )
    page_fed = parse_arm(case / "page_fed", na, "page_fed_product_soa_jit")
    if source_status() != status_before:
        raise RuntimeError(f"NA={na}: source tree changed during run")
    return {
        "cg_na": na,
        "checkpoint_sha256": checkpoint_hash,
        "guest_sha256": digest(guest),
        "physical": compact(physical),
        "page_fed": compact(page_fed),
        "comparison": compare(physical, page_fed),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--sizes", default=",".join(map(str, SIZES)))
    parser.add_argument("--prior-control", type=Path)
    args = parser.parse_args()
    sizes = tuple(int(value) for value in args.sizes.split(","))
    if not sizes or any(value <= 0 for value in sizes):
        raise SystemExit("sizes must contain positive CG_NA values")
    if sizes[0] != 1024:
        if args.prior_control is None:
            raise SystemExit("a non-1024 start requires --prior-control")
        prior = json.loads(args.prior_control.read_text())
        accepted = any(
            run.get("cg_na") == 1024
            and run.get("comparison", {}).get("quantized_fingerprint_equal")
            for run in prior.get("runs", [])
        )
        if not accepted:
            raise SystemExit(
                "prior control lacks an accepted NA=1024 fingerprint"
            )
    if args.out.exists() and any(args.out.iterdir()):
        raise SystemExit(f"refusing nonempty output: {args.out}")
    if digest(GEM5) != GEM5_SHA or digest(RAMULATOR) != RAMULATOR_SHA:
        raise SystemExit(
            "archived page-fed gem5 or frozen Ramulator hash mismatch"
        )
    status_before = source_status()
    args.out.mkdir(parents=True, exist_ok=True)
    runs = []
    for na in sizes:
        result = run_size(args.out, na, status_before)
        runs.append(result)
        (args.out / "diagnosis.json").write_text(
            json.dumps(
                {
                    "schema": "dx100.cg.page_fed_schedule_diagnosis.v1",
                    "candidate_only": True,
                    "native_reruns": 0,
                    "timeout": "none",
                    "debug_instrumentation": DEBUG_FLAGS,
                    "prior_control": str(args.prior_control)
                    if args.prior_control
                    else None,
                    "runs": runs,
                },
                indent=2,
            )
            + "\n"
        )
        if not result["comparison"]["quantized_fingerprint_equal"]:
            break
    (args.out / "raw_root.sha256").write_text(
        "\n".join(
            f"{digest(p)}  {p.relative_to(args.out)}"
            for p in sorted(args.out.rglob("*"))
            if p.is_file()
        )
        + "\n"
    )
    print(
        json.dumps(
            {
                "completed_sizes": [item["cg_na"] for item in runs],
                "first_fingerprint_divergence": next(
                    (
                        item["cg_na"]
                        for item in runs
                        if not item["comparison"][
                            "quantized_fingerprint_equal"
                        ]
                    ),
                    None,
                ),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
