#!/usr/bin/env python3
"""Run repeated exact full-GZP dual-masked and reference arms."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from concurrent.futures import (
    Future,
    ThreadPoolExecutor,
)
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_general_hybrid_benchmark_matrix as common  # noqa: E402

DEFAULT_CONFIG = ROOT / "configs/deprecated/example/se.py"
DEFAULT_RAMULATOR = ROOT / "ext/ramulator2/ramulator2/example_gem5_config.yaml"
EXPECTED_OUTPUT_HASH = "11225737641199706160"
EXPECTED_INDEX_HASH = "15605778284598092602"
ELEMENTS = 1_000_000
REFERENCE_ELEMENTS = 1_180_000
WINDOW_ELEMENTS = 16_384
FULL_WINDOWS = ELEMENTS // WINDOW_ELEMENTS
FULL_VALUES = FULL_WINDOWS * WINDOW_ELEMENTS
GRADIENT_PAGES = FULL_WINDOWS * 4
GRADIENT_PUBLICATION_BYTES = FULL_VALUES * 4
PUBLISH_LINES = GRADIENT_PUBLICATION_BYTES // 64
EXPECTED_FULL_SELECTED = 949_411
EXPECTED_FULL_REJECTED = 50_013
FATAL = re.compile(r"\b(?:panic|fatal|segmentation fault|assertion)\b", re.I)

ARMS = (
    {
        "name": "native16",
        "profile": "native16",
        "binary": "native16",
        "checkpoint": "native16",
        "selector": None,
    },
    {
        "name": "native4",
        "profile": "native4",
        "binary": "native4",
        "checkpoint": "native4",
        "selector": None,
    },
    {
        "name": "volume_masked_index_owner64_pre_a_context64",
        "profile": "hybrid",
        "binary": "hybrid",
        "checkpoint": "hybrid-volume",
        "selector": "token_stream_ld volume_masked_index",
    },
    {
        "name": "dual_masked_index_owner64_pre_a_context64",
        "profile": "hybrid",
        "binary": "hybrid",
        "checkpoint": "hybrid-dual",
        "selector": "token_stream_ld dual_masked_index",
    },
)

HYBRID_OPTIONS = (
    "--maa_virtual_response_slots=1152",
    "--maa_virtual_response_word_pool=2304",
    "--maa_virtual_combine_slots=512",
    "--maa_virtual_combine_words=4096",
    "--maa_virtual_combine_ways=16",
    "--maa_virtual_words_per_cycle=4",
    "--maa_virtual_combine_banks=8",
    "--maa_virtual_index_buffer_lines=8",
    "--maa_soa_jit_active_contexts=64",
    "--maa_soa_jit_value_lookahead=8",
    "--maa_soa_jit_value_cache_enable",
    "--maa_soa_jit_predicate_active_credits=16",
    "--maa_soa_jit_active_value_owners=64",
    "--maa_soa_jit_apply_lanes=1",
    "--maa_soa_jit_pre_a_value_lookahead",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--gem5", required=True, type=Path)
    parser.add_argument("--ramulator-library", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--ramulator-config", type=Path, default=DEFAULT_RAMULATOR)
    parser.add_argument("--n", type=int, default=ELEMENTS)
    parser.add_argument("--replicas", type=int, default=3)
    parser.add_argument("--mem-channels", type=int, default=2)
    parser.add_argument("--l3-ports", type=int, default=4)
    parser.add_argument("--expected-gem5-sha256")
    parser.add_argument("--resume-existing", action="store_true")
    parser.add_argument("--max-parallel-restores", type=int, default=1)
    parser.add_argument("--adopt-native16-replica1-pid", type=int)
    parser.add_argument("--adopt-native16-replica1-start-time", type=int)
    parser.add_argument("--stopped-parent-pid", type=int)
    parser.add_argument("--stopped-parent-start-time", type=int)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.n != ELEMENTS:
        parser.error("the exact full-GZP contract requires --n=1000000")
    if args.replicas < 2:
        parser.error("--replicas must be at least two")
    if args.mem_channels < 1 or not 1 <= args.l3_ports <= 16:
        parser.error("invalid memory-channel or L3-port count")
    if not 1 <= args.max_parallel_restores <= 32:
        parser.error("--max-parallel-restores must be in [1,32]")
    adoption = (
        args.adopt_native16_replica1_pid,
        args.adopt_native16_replica1_start_time,
        args.stopped_parent_pid,
        args.stopped_parent_start_time,
    )
    if any(value is not None for value in adoption) and not all(
        value is not None for value in adoption
    ):
        parser.error("recovery adoption requires all PID/start-time fields")
    if args.execute and not args.expected_gem5_sha256:
        parser.error("--execute requires --expected-gem5-sha256")
    if args.expected_gem5_sha256 and not re.fullmatch(
        r"[0-9a-f]{64}", args.expected_gem5_sha256
    ):
        parser.error("--expected-gem5-sha256 must be lowercase hexadecimal")
    return args


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def atomic_json(path: Path, value: object) -> None:
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def git_output(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def compile_guest(
    path: Path, env: dict[str, str], tile_size: int, hybrid: bool
) -> list[str]:
    command = [
        env.get("CXX", "g++"),
        f"-I{ROOT / 'benchmarks/API'}",
        f"-I{ROOT / 'include'}",
        f"-I{ROOT / 'util/m5/src'}",
        "-std=c++11",
        "-O3",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wno-ignored-qualifiers",
        "-fopenmp",
        "-DGEM5",
        "-DMAA",
        "-DUME_FIXED_INPUT",
        "-DUME_OUTPUT_FINGERPRINT",
        "-DNUM_CORES=4",
        f"-DTILE_SIZE={tile_size}",
        "-DMAA_MEM_SIZE=0x80000000",
        str(ROOT / "util/m5/src/abi/x86/m5op.S"),
        str(ROOT / "benchmarks/UME/gradzatp.cpp"),
        "-o",
        str(path),
    ]
    if hybrid:
        insertion = command.index(str(ROOT / "util/m5/src/abi/x86/m5op.S"))
        command[insertion:insertion] = [
            "-DMAA_VIRTUAL_GATHER",
            "-DMAA_GENERAL_VIRTUAL_CONSUMER",
            "-DMAA_CONSUMER_TILE_SIZE=4096",
            "-DUME_GZP_SOA_JIT_RMW",
        ]
    subprocess.run(command, env=env, check=True)
    return command


def plan(args: argparse.Namespace) -> dict[str, object]:
    return {
        "schema": "dx100.gzp_dual_masked_rmw_plan.v1",
        "workload": "ume-gzp",
        "n": args.n,
        "replicas": args.replicas,
        "arms": list(ARMS),
        "shared_hybrid_checkpoint": False,
        "immutable_selector_per_hybrid_arm": True,
        "fixed_hybrid_controls": {
            "logical_elements": 16384,
            "physical_payload_elements": 4096,
            "active_value_owners": 64,
            "pre_a_value_lookahead": True,
            "active_contexts": 64,
        },
        "exact_output_hash": EXPECTED_OUTPUT_HASH,
        "simulated_metric": "simTicks",
        "host_time_metric_authorized": False,
        "timeout_seconds": 0,
        "max_parallel_restores": args.max_parallel_restores,
    }


def parse_fields(line: str) -> dict[str, str]:
    return dict(token.split("=", 1) for token in line.split()[1:] if "=" in token)


def exactly_one(lines: list[str], prefix: str, label: str) -> dict[str, str]:
    matches = [line for line in lines if line.startswith(prefix)]
    if len(matches) != 1:
        raise RuntimeError(f"{label}: expected one {prefix!r}, got {len(matches)}")
    return parse_fields(matches[0])


def first_stats(path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    active = False
    complete = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("---------- Begin Simulation Statistics"):
            if not active and not complete:
                active = True
            continue
        if line.startswith("---------- End Simulation Statistics") and active:
            complete = True
            break
        if not active:
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        try:
            result[fields[0]] = int(float(fields[1]))
        except (ValueError, OverflowError):
            pass
    if not complete or "simTicks" not in result:
        raise RuntimeError(f"missing complete first stats window: {path}")
    return result


def stat_sum(stats: dict[str, int], suffix: str) -> int:
    values = [value for name, value in stats.items() if name.endswith(suffix)]
    if not values:
        raise RuntimeError(f"missing stats suffix {suffix}")
    return sum(values)


def analyze_publisher_trace(path: Path) -> dict[str, int]:
    pages = [
        parse_fields(line)
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if "event=spd_publish_terminal " in line
    ]
    if len(pages) != GRADIENT_PAGES:
        raise RuntimeError("dual: response-bearing publisher terminal count differs")
    page_counts = {page: 0 for page in range(4)}
    for fields in pages:
        page = int(fields.get("logical_page", "-1"))
        offset = int(fields.get("logical_offset", "-1"))
        if (
            page not in page_counts
            or offset != page * 4096
            or int(fields.get("generation", "0")) <= 0
            or fields.get("issues") != "256"
            or fields.get("responses") != "256"
            or not 1 <= int(fields.get("credit_hwm", "0")) <= 8
        ):
            raise RuntimeError("dual: publisher page/order/response ledger differs")
        page_counts[page] += 1
    if set(page_counts.values()) != {FULL_WINDOWS}:
        raise RuntimeError(
            "dual: did not publish each logical page exactly once per window"
        )
    return {
        "publisher_terminals": len(pages),
        "publisher_lines": len(pages) * 256,
    }


def analyze_soa_trace(path: Path, expected: int) -> dict[str, int]:
    events = [
        parse_fields(line)
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if "event=soa_jit_complete " in line and "terminal=1" in line
    ]
    if len(events) != expected:
        raise RuntimeError(f"SoA/JIT terminal trace count {len(events)} != {expected}")
    selected = 0
    rejected = 0
    for event in events:
        if (
            event.get("predicate_mode") != "masked_index"
            or event.get("pre_a_enable") != "1"
            or event.get("active_value_owners") != "64"
            or event.get("active_contexts") != "64"
            or event.get("masked_index_compare_bits") != "32"
            or event.get("masked_index_additional_buffer_bytes") != "0"
        ):
            raise RuntimeError("SoA/JIT accepted-control or hardware trace differs")
        selected += int(event["selected"])
        rejected += int(event["predicate_rejected"])
    return {"selected": selected, "rejected": rejected}


def analyze_run(run: Path, arm: dict[str, object], replica: int) -> dict[str, object]:
    name = str(arm["name"])
    label = f"{name}/replica-{replica}"
    if (run / "restore.exit").read_text(encoding="utf-8").strip() != "0":
        raise RuntimeError(f"{label}: wrapper exit is not zero")
    log = (run / "restore.log").read_text(encoding="utf-8", errors="replace")
    if (
        len(
            re.findall(
                r"Exiting @ tick \d+ because m5_exit instruction encountered",
                log,
            )
        )
        != 1
    ):
        raise RuntimeError(f"{label}: unique m5_exit marker is absent")
    if FATAL.search(log):
        raise RuntimeError(f"{label}: fatal marker in restore log")
    lines = log.splitlines()
    output = exactly_one(lines, "UME_OUTPUT_FP ", label)
    reference = exactly_one(lines, "UME_REFERENCE_PASS ", label)
    if (
        output.get("output_hash") != EXPECTED_OUTPUT_HASH
        or output.get("nonfinite") != "0"
        or reference.get("point_volume_errors") != "0"
        or reference.get("point_gradient_errors") != "0"
        or reference.get("elements") != str(REFERENCE_ELEMENTS)
    ):
        raise RuntimeError(f"{label}: exact scalar reference gate failed")
    stats = first_stats(run / "gem5/stats.txt")
    record: dict[str, object] = {
        "arm": name,
        "replica": replica,
        "simTicks": stats["simTicks"],
        "output_hash": output["output_hash"],
        "reference_elements": int(reference["elements"]),
    }
    if arm["selector"] is None:
        record["soa_jit_instructions"] = 0
        return record

    ledger = exactly_one(lines, "UME_GZP_MASKED_INDEX_LEDGER ", label)
    terminal = exactly_one(lines, "UME_GZP_TERMINAL ", label)
    zero_fields = (
        "active_uint32_max",
        "active_illegal_index",
        "inactive_legal_index",
        "inactive_non_sentinel",
    )
    if (
        ledger.get("result") != "PASS"
        or ledger.get("exact_equivalence") != "1"
        or ledger.get("index_hash") != EXPECTED_INDEX_HASH
        or int(ledger.get("full_selected", "-1")) != EXPECTED_FULL_SELECTED
        or int(ledger.get("full_rejected", "-1")) != EXPECTED_FULL_REJECTED
        or any(ledger.get(field) != "0" for field in zero_fields)
        or terminal.get("predicate_publications") != "0"
        or terminal.get("predicate_publication_bytes") != "0"
        or terminal.get("result") != "PASS"
    ):
        raise RuntimeError(f"{label}: masked-index guest ledger failed")

    dual = name.startswith("dual_")
    expected_instructions = FULL_WINDOWS * (2 if dual else 1)
    trace = analyze_soa_trace(run / "gem5/virtual_trace.log", expected_instructions)
    if (
        stat_sum(stats, "IND_SoaJitInstructions") != expected_instructions
        or stat_sum(stats, "IND_SoaJitTerminalCompletions") != expected_instructions
        or stat_sum(stats, "IND_SoaJitPredicateLineReads") != 0
        or stat_sum(stats, "IND_SoaJitPredicateLineResponses") != 0
        or stat_sum(stats, "IND_SoaJitSelected") != trace["selected"]
        or stat_sum(stats, "IND_SoaJitPredicateRejected") != trace["rejected"]
        or trace["selected"] != EXPECTED_FULL_SELECTED * (2 if dual else 1)
        or trace["rejected"] != EXPECTED_FULL_REJECTED * (2 if dual else 1)
        or stat_sum(stats, "IND_SoaJitPreAValueIssues")
        != stat_sum(stats, "IND_SoaJitPreAValueUses")
        or stat_sum(stats, "IND_SoaJitPreAValueUses") <= 0
    ):
        raise RuntimeError(f"{label}: SoA/JIT runtime closure failed")

    record.update(
        {
            "soa_jit_instructions": expected_instructions,
            "selected": trace["selected"],
            "rejected": trace["rejected"],
            "index_hash": ledger["index_hash"],
        }
    )
    if not dual:
        expected_terminal = {
            "treatment": "volume_masked_index_soa_jit",
            "masked_index_windows": str(FULL_WINDOWS),
            "dual_masked_index_windows": "0",
            "published_predicates": "0",
            "published_gradient_values": "0",
            "gradient_publication_bytes": "0",
            "publisher": "masked_index_no_predicate_publication",
            "hardware_bytes": "0",
        }
        if any(terminal.get(key) != value for key, value in expected_terminal.items()):
            raise RuntimeError(f"{label}: selected volume treatment changed")
        return record

    dual_terminal = exactly_one(lines, "UME_GZP_DUAL_MASKED_TERMINAL ", label)
    expected_dual = {
        "result": "PASS",
        "windows": str(FULL_WINDOWS),
        "volume_issues": str(FULL_WINDOWS),
        "volume_completions": str(FULL_WINDOWS),
        "gradient_page_issues": str(GRADIENT_PAGES),
        "gradient_page_completions": str(GRADIENT_PAGES),
        "gradient_issues": str(FULL_WINDOWS),
        "gradient_completions": str(FULL_WINDOWS),
        "gradient_publication_bytes": str(GRADIENT_PUBLICATION_BYTES),
        "gradient_publication_lines": str(PUBLISH_LINES),
        "predicate_publication_bytes": "0",
        "masked_index_additional_buffer_bytes": "0",
        "publisher_guest_owners": "4",
        "publisher_instances": "1",
        "publisher_payload_bytes_per_instance": "512",
        "publisher_control_bytes_per_instance": "408",
        "publisher_total_bytes_per_instance": "920",
        "persistent_payload_bytes": "512",
        "persistent_control_bytes": "408",
        "persistent_total_bytes": "920",
        "coherent_gradient_backing_bytes": "262144",
        "coherent_gradient_backing_kind": "llc_dram_address_space",
    }
    expected_terminal = {
        "treatment": "dual_masked_index_soa_jit",
        "masked_index_windows": "0",
        "dual_masked_index_windows": str(FULL_WINDOWS),
        "published_predicates": "0",
        "published_gradient_values": str(FULL_VALUES),
        "gradient_publication_bytes": str(GRADIENT_PUBLICATION_BYTES),
        "publisher": "gradient_pages_response_bearing_no_predicate",
        "hardware_bytes": "920",
    }
    if any(dual_terminal.get(key) != value for key, value in expected_dual.items()):
        raise RuntimeError(f"{label}: dual terminal arithmetic/ordering failed")
    if any(terminal.get(key) != value for key, value in expected_terminal.items()):
        raise RuntimeError(f"{label}: dual treatment terminal failed")
    publisher = analyze_publisher_trace(run / "gem5/virtual_trace.log")
    if (
        stat_sum(stats, "STR_PublishIssues") != PUBLISH_LINES
        or stat_sum(stats, "STR_PublishAccepts") != PUBLISH_LINES
        or stat_sum(stats, "STR_PublishWriteResponses") != PUBLISH_LINES
        or stat_sum(stats, "STR_PublishTerminals") != GRADIENT_PAGES
        or publisher["publisher_lines"] != PUBLISH_LINES
    ):
        raise RuntimeError(f"{label}: publisher issue/WriteResp closure failed")
    record.update(publisher)
    record["gradient_publication_bytes"] = GRADIENT_PUBLICATION_BYTES
    record["gradient_publication_values"] = FULL_VALUES
    record["gradient_publication_lines"] = PUBLISH_LINES
    record["gradient_publication_write_responses"] = PUBLISH_LINES
    record["coherent_gradient_backing_bytes"] = 262144
    record["coherent_gradient_backing_kind"] = "llc_dram_address_space"
    record["persistent_payload_bytes"] = 512
    record["persistent_control_bytes"] = 408
    record["persistent_total_bytes"] = 920
    return record


def run_logged(command: list[str], log: Path, env: dict[str, str]) -> int:
    atomic_json(log.with_suffix(".command.json"), command)
    atomic_text(log.with_suffix(".command.txt"), shlex.join(command) + "\n")
    with log.open("wb") as output:
        result = subprocess.run(
            command, stdout=output, stderr=subprocess.STDOUT, env=env
        )
    atomic_text(log.with_suffix(".exit"), f"{result.returncode}\n")
    return result.returncode


def live_restore_for(run: Path) -> bool:
    pattern = f"--outdir={run / 'gem5'}"
    result = subprocess.run(
        ["pgrep", "-f", "--", pattern],
        check=False,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def resume_existing(args: argparse.Namespace) -> int:
    """Resume only absent restore keys from already frozen checkpoints."""
    if not args.out.is_dir():
        raise RuntimeError("--resume-existing requires an existing --out root")
    if (args.out / "campaign.exit").read_text(encoding="utf-8").strip() not in (
        "running",
        "recovery",
    ):
        raise RuntimeError("existing campaign is already terminal")

    def proc_identity(pid: int) -> dict[str, int | str]:
        contents = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = contents[contents.rfind(")") + 2 :].split()
        return {
            "pid": pid,
            "state": fields[0],
            "ppid": int(fields[1]),
            "start_time": int(fields[19]),
            "exit_code": int(fields[49]),
        }

    if args.adopt_native16_replica1_pid is not None:
        parent = proc_identity(args.stopped_parent_pid)
        child = proc_identity(args.adopt_native16_replica1_pid)
        if (
            parent["start_time"] != args.stopped_parent_start_time
            or parent["state"] not in ("T", "t")
            or child["start_time"] != args.adopt_native16_replica1_start_time
            or child["state"] != "Z"
            or child["ppid"] != args.stopped_parent_pid
            or child["exit_code"] != 0
        ):
            raise RuntimeError("stopped-parent/zombie-child adoption identity failed")
        adopted_run = args.out / "arms/native16/replica-1"
        adopted_log = (adopted_run / "restore.log").read_text(
            encoding="utf-8", errors="replace"
        )
        if (
            FATAL.search(adopted_log)
            or len(
                re.findall(
                    r"Exiting @ tick \d+ because m5_exit instruction encountered",
                    adopted_log,
                )
            )
            != 1
            or "UME_OUTPUT_FP output_hash=11225737641199706160 nonfinite=0"
            not in adopted_log
            or "UME_REFERENCE_PASS point_volume_errors=0 "
            "point_gradient_errors=0 elements=1180000" not in adopted_log
        ):
            raise RuntimeError("adopted native16 terminal log failed")
        first_stats(adopted_run / "gem5/stats.txt")
        adoption_record = {
            "schema": "dx100.gzp_restore_adoption.v1",
            "reason": "serial parent SIGSTOP before next claim",
            "parent": parent,
            "child": child,
            "restore_log_sha256": sha256(adopted_run / "restore.log"),
            "stats_sha256": sha256(adopted_run / "gem5/stats.txt"),
            "command_sha256": sha256(adopted_run / "restore.command.json"),
            "exact_output_hash": EXPECTED_OUTPUT_HASH,
        }
        atomic_json(adopted_run / "recovery-adoption.json", adoption_record)
        atomic_text(adopted_run / "restore.exit", "0\n")
    frozen = {
        "gem5": (args.out / "inputs/gem5.opt").resolve(),
        "ramulator_library": (args.out / "inputs/libramulator.so").resolve(),
        "ramulator_config": (args.out / "inputs/ramulator.yaml").resolve(),
        "native16": (args.out / "inputs/native16").resolve(),
        "native4": (args.out / "inputs/native4").resolve(),
        "hybrid": (args.out / "inputs/hybrid").resolve(),
    }
    config = (args.out / "inputs/configs/deprecated/example/se.py").resolve()
    checkpoints: dict[str, Path] = {
        name: (args.out / "checkpoints" / name / "gem5").resolve()
        for name in ("native16", "native4")
    }
    missing = [
        str(path)
        for path in (*frozen.values(), config, *checkpoints.values())
        if not path.exists()
    ]
    if missing:
        raise RuntimeError("recovery inputs are missing: " + ", ".join(missing))
    if (
        sha256(frozen["gem5"]) != args.expected_gem5_sha256
        or sha256(args.gem5) != args.expected_gem5_sha256
        or sha256(frozen["ramulator_library"]) != sha256(args.ramulator_library)
    ):
        raise RuntimeError("recovery simulator/library identity differs")

    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = "4"
    env["OMP_PROC_BIND"] = "false"
    env["LD_LIBRARY_PATH"] = str((args.out / "inputs").resolve())
    recovery_commit = git_output("rev-parse", "HEAD")
    corrected_hybrid = args.out / "inputs" / f"hybrid-{recovery_commit[:8]}"
    if corrected_hybrid.exists():
        raise RuntimeError("corrected recovery hybrid artifact already exists")
    corrected_compile_command = compile_guest(corrected_hybrid, env, 16384, True)
    corrected_hybrid.chmod(0o555)
    frozen["hybrid"] = corrected_hybrid.resolve()
    checkpoint_selectors: dict[str, Path] = {}

    def create_hybrid_checkpoint(item: tuple[str, str]) -> tuple[str, Path]:
        group, payload = item
        directory = args.out / "checkpoints" / group
        if directory.exists():
            raise RuntimeError(f"corrected checkpoint {group} already exists")
        directory.mkdir(parents=True)
        selector = directory / "selector.txt"
        atomic_text(selector, payload + "\n")
        selector.chmod(0o444)
        command = common.checkpoint_command(
            frozen["gem5"],
            config,
            directory / "gem5",
            frozen["hybrid"],
            f"{args.n} {selector.resolve()}",
        )
        if run_logged(command, directory / "checkpoint.log", env) != 0:
            raise RuntimeError(f"corrected checkpoint {group} failed")
        return group, selector

    corrected_specs = (
        ("hybrid-volume", "token_stream_ld volume_masked_index"),
        ("hybrid-dual", "token_stream_ld dual_masked_index"),
    )
    with ThreadPoolExecutor(max_workers=2) as checkpoint_executor:
        for group, selector in checkpoint_executor.map(
            create_hybrid_checkpoint, corrected_specs
        ):
            checkpoints[group] = (args.out / "checkpoints" / group / "gem5").resolve()
            checkpoint_selectors[group] = selector
    checkpoint_identities = {
        name: common.tree_identity(path) for name, path in checkpoints.items()
    }
    completed: dict[tuple[str, int], dict[str, object]] = {}
    pending: list[tuple[dict[str, object], int]] = []
    recovery_runs: list[dict[str, object]] = []
    for arm in ARMS:
        for replica in range(1, args.replicas + 1):
            key = (str(arm["name"]), replica)
            run = args.out / "arms" / key[0] / f"replica-{replica}"
            exit_path = run / "restore.exit"
            if exit_path.is_file():
                if exit_path.read_text(encoding="utf-8").strip() != "0":
                    raise RuntimeError(f"{key}: existing restore is nonzero")
                if live_restore_for(run):
                    raise RuntimeError(f"{key}: terminal artifact has a live PID")
                completed[key] = analyze_run(run, arm, replica)
                continue
            if run.exists() and any(run.iterdir()):
                if live_restore_for(run):
                    raise RuntimeError(f"{key}: restore PID is still live")
                raise RuntimeError(f"{key}: partial run lacks adopted exit evidence")
            pending.append((arm, replica))

    def execute(job: tuple[dict[str, object], int]) -> dict[str, object]:
        arm, replica = job
        name = str(arm["name"])
        run = args.out / "arms" / name / f"replica-{replica}"
        if live_restore_for(run):
            raise RuntimeError(f"{name}/{replica}: duplicate live restore")
        run.mkdir(parents=True, exist_ok=False)
        options = str(args.n)
        selector_hash = None
        if arm["selector"] is not None:
            treatment = str(arm["selector"]) + "\n"
            selector = checkpoint_selectors[str(arm["checkpoint"])]
            if (
                selector.read_text(encoding="utf-8") != treatment
                or selector.stat().st_mode & 0o222
            ):
                raise RuntimeError(f"{name}/{replica}: selector is not immutable")
            selector_hash = sha256(selector)
            atomic_text(run / "frozen_treatment.txt", treatment)
            options += f" {selector.resolve()}"
        command = common.restore_command(
            frozen["gem5"],
            config,
            run / "gem5",
            checkpoints[str(arm["checkpoint"])],
            frozen[str(arm["binary"])],
            options,
            str(arm["profile"]),
            frozen["ramulator_config"],
            args.mem_channels,
            args.l3_ports,
            list(HYBRID_OPTIONS) if arm["profile"] == "hybrid" else [],
        )
        if run_logged(command, run / "restore.log", env) != 0:
            raise RuntimeError(f"{name}/replica-{replica}: restore failed")
        if selector_hash is not None and sha256(selector) != selector_hash:
            raise RuntimeError(f"{name}/{replica}: selector changed during restore")
        row = analyze_run(run, arm, replica)
        recovery_runs.append(
            {
                "arm": name,
                "replica": replica,
                "selector": arm["selector"],
                "selector_sha256": selector_hash,
                "command_sha256": sha256(run / "restore.command.json"),
            }
        )
        return row

    atomic_text(args.out / "campaign.exit", "recovery\n")
    futures: dict[tuple[str, int], Future[dict[str, object]]] = {}
    with ThreadPoolExecutor(max_workers=args.max_parallel_restores) as executor:
        for job in pending:
            futures[(str(job[0]["name"]), job[1])] = executor.submit(execute, job)
        for key, future in futures.items():
            completed[key] = future.result()

    if len(completed) != len(ARMS) * args.replicas:
        raise RuntimeError("recovery did not close every arm/replica key")
    for name, checkpoint in checkpoints.items():
        if common.tree_identity(checkpoint) != checkpoint_identities[name]:
            raise RuntimeError(f"recovery changed checkpoint {name}")
    selector_identities = {}
    for group, payload in corrected_specs:
        selector = checkpoint_selectors[group]
        if (
            selector.read_text(encoding="utf-8") != payload + "\n"
            or selector.stat().st_mode & 0o222
        ):
            raise RuntimeError(f"recovery selector {group} changed")
        selector_identities[group] = {
            "path": str(selector.resolve()),
            "sha256": sha256(selector),
            "payload": payload,
        }

    def normalized_command(path: Path) -> list[str]:
        command = json.loads(path.read_text(encoding="utf-8"))
        result = []
        skip_option_value = False
        for argument in command:
            if skip_option_value:
                result.append(f"{args.n} IMMUTABLE_SELECTOR")
                skip_option_value = False
            elif argument == "--options":
                result.append(argument)
                skip_option_value = True
            elif argument.startswith("--outdir="):
                result.append("--outdir=RUN")
            elif argument.startswith("--checkpoint-dir="):
                result.append("--checkpoint-dir=CHECKPOINT")
            else:
                result.append(argument)
        return result

    hybrid_command_norms = {
        json.dumps(
            normalized_command(
                args.out
                / "arms"
                / str(arm["name"])
                / f"replica-{replica}"
                / "restore.command.json"
            )
        )
        for arm in ARMS
        if arm["selector"] is not None
        for replica in range(1, args.replicas + 1)
    }
    if len(hybrid_command_norms) != 1:
        raise RuntimeError("normalized hybrid restore commands differ")
    rows = [
        completed[(str(arm["name"]), replica)]
        for arm in ARMS
        for replica in range(1, args.replicas + 1)
    ]
    for arm in ARMS:
        replicas = [row for row in rows if row["arm"] == arm["name"]]
        invariant_keys = set(replicas[0]) - {"replica"}
        if (
            len(
                {
                    json.dumps(
                        {key: row[key] for key in invariant_keys}, sort_keys=True
                    )
                    for row in replicas
                }
            )
            != 1
        ):
            raise RuntimeError(f"{arm['name']}: exact replicas differ")
    first = {str(row["arm"]): row for row in rows if row["replica"] == 1}
    dual_ticks = int(first["dual_masked_index_owner64_pre_a_context64"]["simTicks"])
    comparisons = {}
    for baseline in (
        "volume_masked_index_owner64_pre_a_context64",
        "native16",
        "native4",
    ):
        baseline_ticks = int(first[baseline]["simTicks"])
        comparisons[f"{baseline}_over_dual"] = {
            "baseline_simTicks": baseline_ticks,
            "dual_simTicks": dual_ticks,
            "speedup": baseline_ticks / dual_ticks,
            "dual_improves": dual_ticks < baseline_ticks,
        }
    recovery = {
        "schema": "dx100.gzp_dual_masked_rmw_recovery.v1",
        "recovery_commit": git_output("rev-parse", "HEAD"),
        "original_launch_commit": "86bbbfbde3f565bd3e51c11a8c22da257856ba5b",
        "max_parallel_restores": args.max_parallel_restores,
        "reused_keys": [list(key) for key in sorted(set(completed) - set(futures))],
        "launched_keys": [list(key) for key in sorted(futures)],
        "corrected_hybrid_sha256": sha256(frozen["hybrid"]),
        "corrected_compile_command": corrected_compile_command,
        "selector_binding": "one immutable checkpointed Process.cmd path per hybrid arm",
        "selector_identities": selector_identities,
        "normalized_hybrid_command_delta": "outdir,checkpoint-dir,immutable-selector-payload only",
        "checkpoint_identities": checkpoint_identities,
        "runs": recovery_runs,
    }
    atomic_json(args.out / "recovery.json", recovery)
    atomic_json(args.out / "results.json", {"rows": rows, "comparisons": comparisons})
    with (args.out / "results.tsv").open("w", newline="") as output:
        fields = sorted({key for row in rows for key in row})
        writer = csv.DictWriter(output, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    atomic_text(
        args.out / "summary.txt",
        "\n".join(
            f"{name}_speedup={value['speedup']}" for name, value in comparisons.items()
        )
        + "\n",
    )
    atomic_text(args.out / "campaign.exit", "0\n")
    print("GZP_DUAL_MASKED_RMW_RECOVERY_PASS")
    return 0


def main() -> int:
    args = parse_args()
    if not args.execute:
        print(json.dumps(plan(args), indent=2, sort_keys=True))
        return 0
    required = (
        args.gem5,
        args.ramulator_library,
        args.config,
        args.ramulator_config,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        print("error: missing inputs: " + ", ".join(missing), file=sys.stderr)
        return 2
    if sha256(args.gem5) != args.expected_gem5_sha256:
        print(
            "error: gem5 SHA-256 does not match the execution pin",
            file=sys.stderr,
        )
        return 2
    if git_output("status", "--short", "--untracked-files=all"):
        print(
            "error: refusing evidence execution from a dirty source tree",
            file=sys.stderr,
        )
        return 2
    if args.resume_existing:
        try:
            return resume_existing(args)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            if args.out.is_dir():
                atomic_text(args.out / "campaign.exit", "1\n")
            print(f"error: recovery failed: {error}", file=sys.stderr)
            return 1
    if args.out.exists():
        print(f"error: refusing to overwrite {args.out}", file=sys.stderr)
        return 2
    try:
        args.out.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        print("error: raw evidence root must be outside Git", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True)
    atomic_text(args.out / "campaign.exit", "running\n")
    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = "4"
    env["OMP_PROC_BIND"] = "false"
    try:
        inputs = args.out / "inputs"
        inputs.mkdir()
        frozen: dict[str, Path] = {}
        identities: dict[str, dict[str, str]] = {}
        for name, source, destination in (
            ("gem5", args.gem5, "gem5.opt"),
            ("ramulator_library", args.ramulator_library, "libramulator.so"),
            ("ramulator_config", args.ramulator_config, "ramulator.yaml"),
        ):
            path = inputs / destination
            digest = common.copy_stable_artifact(source.resolve(), path)
            frozen[name] = path.resolve()
            identities[name] = {"path": str(path.resolve()), "sha256": digest}
        frozen_config, config_identity = common.freeze_config_tree(
            args.config, ROOT / "configs", inputs / "configs"
        )
        compile_commands: dict[str, list[str]] = {}
        for name, tile_size, hybrid in (
            ("native16", 16384, False),
            ("native4", 4096, False),
            ("hybrid", 16384, True),
        ):
            path = inputs / name
            compile_commands[name] = compile_guest(path, env, tile_size, hybrid)
            frozen[name] = path.resolve()
            identities[name] = {
                "path": str(path.resolve()),
                "sha256": sha256(path),
                "source_commit": git_output("rev-parse", "HEAD"),
                "tile_size": str(tile_size),
            }
        for name in ("gem5", "native16", "native4", "hybrid"):
            frozen[name].chmod(0o555)
        env["LD_LIBRARY_PATH"] = str(inputs.resolve())
        ldd = subprocess.run(
            ["ldd", str(frozen["gem5"])],
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        atomic_text(inputs / "gem5.ldd.txt", ldd.stdout + ldd.stderr)
        if ldd.returncode != 0 or str(frozen["ramulator_library"]) not in ldd.stdout:
            raise RuntimeError(
                "frozen gem5 did not resolve the frozen Ramulator library"
            )

        checkpoints: dict[str, dict[str, object]] = {}
        checkpoint_roots: dict[str, Path] = {}
        checkpoint_selectors: dict[str, Path] = {}
        for group, binary, profile, payload in (
            ("native16", "native16", "native16", None),
            ("native4", "native4", "native4", None),
            (
                "hybrid-volume",
                "hybrid",
                "hybrid",
                "token_stream_ld volume_masked_index",
            ),
            (
                "hybrid-dual",
                "hybrid",
                "hybrid",
                "token_stream_ld dual_masked_index",
            ),
        ):
            directory = args.out / "checkpoints" / group
            directory.mkdir(parents=True)
            options = str(args.n)
            selector_identity = None
            if payload is not None:
                selector = directory / "selector.txt"
                atomic_text(selector, payload + "\n")
                selector.chmod(0o444)
                checkpoint_selectors[group] = selector
                selector_identity = {
                    "path": str(selector.resolve()),
                    "sha256": sha256(selector),
                    "payload": payload,
                }
                options += f" {selector.resolve()}"
            command = common.checkpoint_command(
                frozen["gem5"],
                frozen_config,
                directory / "gem5",
                frozen[binary],
                options,
            )
            if run_logged(command, directory / "checkpoint.log", env) != 0:
                raise RuntimeError(f"checkpoint {group} failed")
            checkpoint_roots[group] = directory / "gem5"
            checkpoints[group] = {
                "profile": profile,
                "binary": binary,
                "tree": common.tree_identity(directory / "gem5"),
                "command_sha256": sha256(directory / "checkpoint.command.json"),
                "selector": selector_identity,
            }

        rows: list[dict[str, object]] = []
        runs: list[dict[str, object]] = []
        for arm in ARMS:
            for replica in range(1, args.replicas + 1):
                run = args.out / "arms" / str(arm["name"]) / f"replica-{replica}"
                run.mkdir(parents=True)
                options = str(args.n)
                selector_hash = None
                if arm["selector"] is not None:
                    selector = checkpoint_selectors[str(arm["checkpoint"])]
                    selector_hash = sha256(selector)
                    atomic_text(
                        run / "frozen_treatment.txt",
                        str(arm["selector"]) + "\n",
                    )
                    options += f" {selector.resolve()}"
                command = common.restore_command(
                    frozen["gem5"],
                    frozen_config,
                    run / "gem5",
                    checkpoint_roots[str(arm["checkpoint"])],
                    frozen[str(arm["binary"])],
                    options,
                    str(arm["profile"]),
                    frozen["ramulator_config"],
                    args.mem_channels,
                    args.l3_ports,
                    list(HYBRID_OPTIONS) if arm["profile"] == "hybrid" else [],
                )
                if run_logged(command, run / "restore.log", env) != 0:
                    raise RuntimeError(
                        f"{arm['name']}/replica-{replica} restore failed"
                    )
                if selector_hash is not None and sha256(selector) != selector_hash:
                    raise RuntimeError(
                        "immutable hybrid selector changed during restore"
                    )
                rows.append(analyze_run(run, arm, replica))
                runs.append(
                    {
                        "arm": arm["name"],
                        "replica": replica,
                        "checkpoint": arm["checkpoint"],
                        "selector": arm["selector"],
                        "selector_sha256": selector_hash,
                        "command_sha256": sha256(run / "restore.command.json"),
                    }
                )

        for group, checkpoint in checkpoint_roots.items():
            if common.tree_identity(checkpoint) != checkpoints[group]["tree"]:
                raise RuntimeError(f"checkpoint {group} changed during restores")
        for arm in ARMS:
            replicas = [row for row in rows if row["arm"] == arm["name"]]
            invariant_keys = set(replicas[0]) - {"replica"}
            snapshots = {
                json.dumps({key: row[key] for key in invariant_keys}, sort_keys=True)
                for row in replicas
            }
            if len(snapshots) != 1:
                raise RuntimeError(f"{arm['name']}: exact replicas differ")
        first = {str(row["arm"]): row for row in rows if row["replica"] == 1}
        dual_ticks = int(first["dual_masked_index_owner64_pre_a_context64"]["simTicks"])
        comparisons = {}
        for baseline in (
            "volume_masked_index_owner64_pre_a_context64",
            "native16",
            "native4",
        ):
            baseline_ticks = int(first[baseline]["simTicks"])
            comparisons[f"{baseline}_over_dual"] = {
                "baseline_simTicks": baseline_ticks,
                "dual_simTicks": dual_ticks,
                "speedup": baseline_ticks / dual_ticks,
                "dual_improves": dual_ticks < baseline_ticks,
            }
        manifest = {
            **plan(args),
            "schema": "dx100.gzp_dual_masked_rmw_matrix.v1",
            "source": {
                "commit": git_output("rev-parse", "HEAD"),
                "status": "clean",
                "gradzatp_sha256": sha256(ROOT / "benchmarks/UME/gradzatp.cpp"),
                "runner_sha256": sha256(Path(__file__)),
            },
            "artifacts": identities,
            "config_tree": {
                "path": str((inputs / "configs").resolve()),
                **config_identity,
            },
            "compile_commands": compile_commands,
            "checkpoints": checkpoints,
            "runs": runs,
            "provenance_permits_native_reference_comparison": True,
            "native_reference_reason": "all guests compiled from the same source commit with fixed input and exact output hash under the same gem5/config tree; only declared tile/payload profile differs",
        }
        atomic_json(args.out / "manifest.json", manifest)
        atomic_json(
            args.out / "results.json",
            {"rows": rows, "comparisons": comparisons},
        )
        with (args.out / "results.tsv").open("w", newline="") as output:
            fields = sorted({key for row in rows for key in row})
            writer = csv.DictWriter(output, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
        atomic_text(
            args.out / "summary.txt",
            "\n".join(
                f"{name}_speedup={value['speedup']}"
                for name, value in comparisons.items()
            )
            + "\n",
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        atomic_text(args.out / "campaign.exit", "1\n")
        print(f"error: {error}", file=sys.stderr)
        return 1
    atomic_text(args.out / "campaign.exit", "0\n")
    print((args.out / "results.tsv").read_text(encoding="utf-8"), end="")
    print((args.out / "summary.txt").read_text(encoding="utf-8"), end="")
    print("GZP_DUAL_MASKED_RMW_MATRIX_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
