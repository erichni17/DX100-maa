#!/usr/bin/env python3
"""Run the exact matched GZP dual-masked physical-capacity matrix."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
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

ACCEPTED_DUAL_ROOT = Path(
    "/data1/nier/dx100-runs/2026-08-23-gzp-dual-masked-rmw-86bbbfb-r1"
)
ACCEPTED_BE77_ROOT = Path(
    "/data1/nier/dx100-runs/2026-08-23-api-backed-attribution-be77a62c-r1"
)
GEM5_SHA256 = (
    "44b6e86ebc86fd692ce02dcb2e1f627082ded10c741134ae49de5721e2edcb45"
)
GUEST_SHA256 = (
    "79f80081611f986e0ef07f79ba498b948f77189ec1f1edd4c5687f6912c06b76"
)
RAMULATOR_SHA256 = (
    "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753"
)
RAMULATOR_CONFIG_SHA256 = (
    "aca6e27b58afdfbfd80b7ec41c3f0e7e574a1fc7355a3512981ead823f68731b"
)
SELECTOR_SHA256 = (
    "d20df4072e9e62b710c6e228c585d463592b7b4183a6be18717affe8410af4cd"
)
CHECKPOINT_SHA256 = (
    "35fd8fb275763e3b14a9ee38265eb3d7ef702de0747a389beaee0f076d6cf862"
)
EXPECTED_OUTPUT_HASH = "11225737641199706160"
EXPECTED_INDEX_HASH = "15605778284598092602"
EXPECTED_PREDICATE_HASH = "10865783785176355512"
EXPECTED_FULL_SELECTED = 949_411
EXPECTED_FULL_REJECTED = 50_013
ELEMENTS = 1_000_000
REFERENCE_ELEMENTS = 1_180_000
FULL_WINDOWS = ELEMENTS // 16_384
FULL_VALUES = FULL_WINDOWS * 16_384
GRADIENT_PAGES = FULL_WINDOWS * 4
PUBLISH_LINES = FULL_VALUES * 4 // 64
GRADIENT_PUBLICATION_BYTES = FULL_VALUES * 4
EXPECTED_SOA_TERMINALS = FULL_WINDOWS * 2
PHYSICAL_TILE_COUNT = 32
PUBLISHER_BYTES = 920
COHERENT_BACKING_BYTES = 256 * 1024
FATAL = re.compile(r"\b(?:panic|fatal|segmentation fault|assertion)\b", re.I)

ARMS = (
    ("logical16_physical16", "native16", 16_384),
    ("logical16_physical4", "hybrid", 4_096),
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
    parser.add_argument("--replicas", type=int, default=3)
    parser.add_argument("--max-parallel-restores", type=int, default=6)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.replicas != 3:
        parser.error(
            "the accepted matrix contract requires exactly three replicas"
        )
    if args.max_parallel_restores != 6:
        parser.error(
            "the accepted matrix contract requires six concurrent restores"
        )
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


def plan(args: argparse.Namespace) -> dict[str, object]:
    payload16 = PHYSICAL_TILE_COUNT * 16_384 * 4
    payload4 = PHYSICAL_TILE_COUNT * 4_096 * 4
    return {
        "schema": "dx100.gzp_dual_capacity_plan.v1",
        "arms": [arm[0] for arm in ARMS],
        "replicas": 3,
        "max_parallel_restores": 6,
        "timeout_seconds": 0,
        "treatment_delta": "physical_tile_elements only (besides outdir)",
        "shared_checkpoint": True,
        "shared_guest": True,
        "shared_selector": "token_stream_ld dual_masked_index",
        "logical_tile_elements": 16_384,
        "physical_tile_payload_bytes": {
            "logical16_physical16": payload16,
            "logical16_physical4": payload4,
            "delta": payload16 - payload4,
        },
        "logical_metadata": {
            "row_table_slices": 16,
            "row_table_rows_per_slice": 64,
            "row_table_entries_per_subslice_row": 8,
            "offset_table_entries": 16_384,
            "offset_table_epoch_entries": 16_384,
        },
        "publisher_bytes_separate": PUBLISHER_BYTES,
        "coherent_backing_bytes_separate": COHERENT_BACKING_BYTES,
        "debug_flags": "MAAVirtualTrace,MAATrace",
        "performance_metric": "first ROI simTicks",
    }


def parse_fields(line: str) -> dict[str, str]:
    return dict(
        token.split("=", 1) for token in line.split()[1:] if "=" in token
    )


def exactly_one(lines: list[str], prefix: str, label: str) -> dict[str, str]:
    matches = [line for line in lines if line.startswith(prefix)]
    if len(matches) != 1:
        raise RuntimeError(
            f"{label}: expected one {prefix!r}, got {len(matches)}"
        )
    return parse_fields(matches[0])


def first_stats(path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    active = False
    complete = False
    with path.open("r", encoding="utf-8", errors="replace") as source:
        for line in source:
            if line.startswith("---------- Begin Simulation Statistics"):
                if not active and not complete:
                    active = True
                continue
            if (
                line.startswith("---------- End Simulation Statistics")
                and active
            ):
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


def semantic_digest(records: list[dict[str, str]]) -> str:
    encoded = json.dumps(
        records, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def analyze_trace(path: Path) -> dict[str, object]:
    publisher: list[dict[str, str]] = []
    soa: list[dict[str, str]] = []
    forbidden_fallback_events = 0
    with path.open("rb") as source:
        for raw in source:
            if b"event=spd_publish_terminal " in raw:
                fields = parse_fields(raw.decode("utf-8", errors="replace"))
                publisher.append(
                    {
                        key: fields.get(key, "")
                        for key in (
                            "unit",
                            "source",
                            "completion",
                            "logical_page",
                            "logical_offset",
                            "generation",
                            "issues",
                            "responses",
                        )
                    }
                )
            elif b"event=soa_jit_complete " in raw and b"terminal=1" in raw:
                fields = parse_fields(raw.decode("utf-8", errors="replace"))
                soa.append(
                    {
                        key: fields.get(key, "")
                        for key in (
                            "unit",
                            "generation",
                            "logical",
                            "selected",
                            "predicate_rejected",
                            "predicate_mode",
                            "masked_index_compare_bits",
                            "masked_index_additional_buffer_bytes",
                            "pre_a_enable",
                            "active_value_owners",
                            "active_contexts",
                        )
                    }
                )
            elif any(
                marker in raw
                for marker in (
                    b"event=page_materialization_fallback ",
                    b"event=page_materialization_dispatch_fallback ",
                    b"event=direct_retirement_fallback ",
                )
            ):
                forbidden_fallback_events += 1
    if len(publisher) != GRADIENT_PAGES:
        raise RuntimeError(
            f"publisher terminals {len(publisher)} != {GRADIENT_PAGES}"
        )
    if len(soa) != EXPECTED_SOA_TERMINALS:
        raise RuntimeError(
            f"SoA terminals {len(soa)} != {EXPECTED_SOA_TERMINALS}"
        )
    page_counts = {page: 0 for page in range(4)}
    for record in publisher:
        page = int(record["logical_page"])
        if (
            page not in page_counts
            or int(record["logical_offset"]) != page * 4_096
            or int(record["generation"]) <= 0
            or record["issues"] != "256"
            or record["responses"] != "256"
            or not record["unit"]
            or not record["source"]
            or not record["completion"]
        ):
            raise RuntimeError(
                "publisher source/page/order/response ledger differs"
            )
        page_counts[page] += 1
    if set(page_counts.values()) != {FULL_WINDOWS}:
        raise RuntimeError(
            "publisher did not close each logical page per window"
        )
    selected = 0
    rejected = 0
    for record in soa:
        if (
            record["predicate_mode"] != "masked_index"
            or record["masked_index_compare_bits"] != "32"
            or record["masked_index_additional_buffer_bytes"] != "0"
            or record["pre_a_enable"] != "1"
            or record["active_value_owners"] != "64"
            or record["active_contexts"] != "64"
        ):
            raise RuntimeError("SoA accepted-control/source ledger differs")
        selected += int(record["selected"])
        rejected += int(record["predicate_rejected"])
    if (
        selected != EXPECTED_FULL_SELECTED * 2
        or rejected != EXPECTED_FULL_REJECTED * 2
        or forbidden_fallback_events != 0
    ):
        raise RuntimeError("trace ledger closure or zero-fallback gate failed")
    return {
        "publisher_terminals": len(publisher),
        "publisher_source_order_sha256": semantic_digest(publisher),
        "soa_terminals": len(soa),
        "soa_source_order_sha256": semantic_digest(soa),
        "selected": selected,
        "rejected": rejected,
        "forbidden_fallback_events": forbidden_fallback_events,
    }


def analyze_run(
    run: Path, arm: str, replica: int, physical: int
) -> dict[str, object]:
    label = f"{arm}/replica-{replica}"
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
    ledger = exactly_one(lines, "UME_GZP_MASKED_INDEX_LEDGER ", label)
    dual = exactly_one(lines, "UME_GZP_DUAL_MASKED_TERMINAL ", label)
    terminal = exactly_one(lines, "UME_GZP_TERMINAL ", label)
    if output != {
        "output_hash": EXPECTED_OUTPUT_HASH,
        "nonfinite": "0",
    } or reference != {
        "point_volume_errors": "0",
        "point_gradient_errors": "0",
        "elements": str(REFERENCE_ELEMENTS),
    }:
        raise RuntimeError(f"{label}: exact output/reference gate failed")
    zero_index_fields = (
        "active_uint32_max",
        "active_illegal_index",
        "inactive_legal_index",
        "inactive_non_sentinel",
    )
    if (
        ledger.get("result") != "PASS"
        or ledger.get("exact_equivalence") != "1"
        or ledger.get("index_hash") != EXPECTED_INDEX_HASH
        or ledger.get("full_selected") != str(EXPECTED_FULL_SELECTED)
        or ledger.get("full_rejected") != str(EXPECTED_FULL_REJECTED)
        or any(ledger.get(field) != "0" for field in zero_index_fields)
        or terminal.get("treatment") != "dual_masked_index_soa_jit"
        or terminal.get("dual_masked_index_windows") != str(FULL_WINDOWS)
        or terminal.get("predicate_hash") != EXPECTED_PREDICATE_HASH
        or terminal.get("predicate_publications") != "0"
        or terminal.get("predicate_publication_bytes") != "0"
        or terminal.get("hardware_bytes") != str(PUBLISHER_BYTES)
        or terminal.get("result") != "PASS"
    ):
        raise RuntimeError(f"{label}: exact guest/index ledger failed")
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
        "publisher_total_bytes_per_instance": str(PUBLISHER_BYTES),
        "persistent_payload_bytes": "512",
        "persistent_control_bytes": "408",
        "persistent_total_bytes": str(PUBLISHER_BYTES),
        "coherent_gradient_backing_bytes": str(COHERENT_BACKING_BYTES),
        "coherent_gradient_backing_kind": "llc_dram_address_space",
    }
    if any(dual.get(key) != value for key, value in expected_dual.items()):
        raise RuntimeError(
            f"{label}: dual terminal arithmetic/accounting failed"
        )
    stats = first_stats(run / "gem5/stats.txt")
    trace = analyze_trace(run / "gem5/virtual_trace.log")
    zero_suffixes = (
        "page_materialization_admission_fallbacks",
        "page_materialization_dispatch_fallbacks",
        "page_materialization_page_fallback_lines",
        "page_materialization_staged_direct_fallback_lines",
        "direct_retirement_fallbacks",
        "direct_retirement_page_fallback_lines",
    )
    if (
        stat_sum(stats, "STR_PublishIssues") != PUBLISH_LINES
        or stat_sum(stats, "STR_PublishAccepts") != PUBLISH_LINES
        or stat_sum(stats, "STR_PublishWriteResponses") != PUBLISH_LINES
        or stat_sum(stats, "STR_PublishTerminals") != GRADIENT_PAGES
        or stat_sum(stats, "IND_SoaJitInstructions") != EXPECTED_SOA_TERMINALS
        or stat_sum(stats, "IND_SoaJitTerminalCompletions")
        != EXPECTED_SOA_TERMINALS
        or any(stat_sum(stats, suffix) != 0 for suffix in zero_suffixes)
    ):
        raise RuntimeError(f"{label}: stats work/fallback closure failed")
    return {
        "arm": arm,
        "replica": replica,
        "physical_tile_elements": physical,
        "first_roi_simTicks": stats["simTicks"],
        "output_hash": output["output_hash"],
        "reference_elements": int(reference["elements"]),
        "index_hash": ledger["index_hash"],
        "publisher_issues": PUBLISH_LINES,
        "publisher_accepts": PUBLISH_LINES,
        "publisher_write_responses": PUBLISH_LINES,
        "publisher_terminals": GRADIENT_PAGES,
        "soa_terminals": EXPECTED_SOA_TERMINALS,
        "fallbacks": 0,
        "publisher_bytes": PUBLISHER_BYTES,
        "coherent_backing_bytes": COHERENT_BACKING_BYTES,
        **trace,
    }


def make_restore_command(
    gem5: Path,
    config: Path,
    outdir: Path,
    checkpoint: Path,
    guest: Path,
    selector: Path,
    ramulator: Path,
    profile: str,
) -> list[str]:
    command = common.restore_command(
        gem5,
        config,
        outdir,
        checkpoint,
        guest,
        f"{ELEMENTS} {selector}",
        profile,
        ramulator,
        2,
        4,
        list(HYBRID_OPTIONS),
    )
    debug = "--debug-flags=MAAVirtualTrace"
    if command.count(debug) != 1:
        raise RuntimeError("restore debug flag is not unique")
    command[command.index(debug)] = "--debug-flags=MAAVirtualTrace,MAATrace"
    return command


def normalized_command(command: list[str]) -> list[str]:
    result = []
    for argument in command:
        if argument.startswith("--outdir="):
            result.append("--outdir=RUN")
        elif argument.startswith("--maa_physical_tile_elements="):
            result.append("--maa_physical_tile_elements=PHYSICAL")
        else:
            result.append(argument)
    return result


def command_delta(left: list[str], right: list[str]) -> list[dict[str, str]]:
    if len(left) != len(right):
        raise RuntimeError("restore command lengths differ")
    return [{"left": a, "right": b} for a, b in zip(left, right) if a != b]


def require_compatible_sources() -> dict[str, object]:
    simulator_paths = ("src", "configs", "SConstruct", "ext/ramulator2")
    guest_paths = (
        "benchmarks/UME/gradzatp.cpp",
        "benchmarks/API/MAA_gem5.hpp",
        "util/m5/src/abi/x86/m5op.S",
        "include/gem5/m5ops.h",
    )
    subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            "be77a62ca992507d9145fe0d44c9ed491c8310a2",
            "HEAD",
            "--",
            *simulator_paths,
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            "8285fd091665fb5c746a188663ee2f193e4a48dd",
            "HEAD",
            "--",
            *guest_paths,
        ],
        cwd=ROOT,
        check=True,
    )
    return {
        "simulator_reference_commit": "be77a62ca992507d9145fe0d44c9ed491c8310a2",
        "lead_equivalent_commit": "5ba5bfe6",
        "simulator_paths_byte_identical": list(simulator_paths),
        "guest_reference_commit": "8285fd091665fb5c746a188663ee2f193e4a48dd",
        "guest_paths_byte_identical": list(guest_paths),
    }


def freeze_checkpoint(source: Path, destination: Path) -> dict[str, object]:
    before = common.tree_identity(source)
    if before["sha256"] != CHECKPOINT_SHA256:
        raise RuntimeError("accepted dual checkpoint identity differs")
    shutil.copytree(source, destination)
    for path in sorted(destination.rglob("*"), reverse=True):
        if path.is_file():
            path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        elif path.is_dir():
            path.chmod(
                stat.S_IRUSR
                | stat.S_IXUSR
                | stat.S_IRGRP
                | stat.S_IXGRP
                | stat.S_IROTH
                | stat.S_IXOTH
            )
    after = common.tree_identity(destination)
    if after != before:
        raise RuntimeError("freeze-copied dual checkpoint identity differs")
    return after


def execute(args: argparse.Namespace) -> int:
    if args.out.exists():
        raise RuntimeError(f"refusing to overwrite evidence root: {args.out}")
    if git_output("status", "--short", "--untracked-files=all"):
        raise RuntimeError(
            "refusing evidence execution from a dirty source tree"
        )
    args.out.mkdir(parents=True)
    atomic_text(args.out / "campaign.exit", "running\n")
    atomic_json(
        args.out / "campaign.json", {"terminal": False, "passed": False}
    )
    try:
        compatibility = require_compatible_sources()
        gem5 = (ACCEPTED_BE77_ROOT / "inputs/gem5.opt").resolve()
        guest = (ACCEPTED_DUAL_ROOT / "inputs/hybrid-8285fd09").resolve()
        selector = (
            ACCEPTED_DUAL_ROOT / "checkpoints/hybrid-dual/selector.txt"
        ).resolve()
        ramulator_library = (
            ACCEPTED_DUAL_ROOT / "inputs/libramulator.so"
        ).resolve()
        ramulator_config = (
            ACCEPTED_DUAL_ROOT / "inputs/ramulator.yaml"
        ).resolve()
        config = (
            ACCEPTED_DUAL_ROOT / "inputs/configs/deprecated/example/se.py"
        ).resolve()
        expected = {
            gem5: GEM5_SHA256,
            guest: GUEST_SHA256,
            selector: SELECTOR_SHA256,
            ramulator_library: RAMULATOR_SHA256,
            ramulator_config: RAMULATOR_CONFIG_SHA256,
        }
        for path, digest in expected.items():
            if not path.is_file() or sha256(path) != digest:
                raise RuntimeError(f"accepted immutable input differs: {path}")
        if (
            selector.read_text(encoding="utf-8")
            != "token_stream_ld dual_masked_index\n"
        ):
            raise RuntimeError("accepted selector payload differs")
        if selector.stat().st_mode & 0o222 or guest.stat().st_mode & 0o222:
            raise RuntimeError("accepted selector or guest is mutable")
        source_checkpoint = ACCEPTED_DUAL_ROOT / "checkpoints/hybrid-dual/gem5"
        frozen_checkpoint = args.out / "frozen-checkpoint/gem5"
        frozen_checkpoint.parent.mkdir()
        checkpoint_identity = freeze_checkpoint(
            source_checkpoint, frozen_checkpoint
        )
        env = os.environ.copy()
        env["OMP_NUM_THREADS"] = "4"
        env["OMP_PROC_BIND"] = "false"
        env["LD_LIBRARY_PATH"] = str(ramulator_library.parent)
        ldd = subprocess.run(
            ["ldd", str(gem5)],
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        atomic_text(args.out / "gem5.ldd.txt", ldd.stdout + ldd.stderr)
        if ldd.returncode != 0 or str(ramulator_library) not in ldd.stdout:
            raise RuntimeError(
                "accepted gem5 does not resolve accepted Ramulator"
            )

        jobs: list[tuple[str, str, int, int, Path, list[str]]] = []
        for arm, profile, physical in ARMS:
            for replica in range(1, args.replicas + 1):
                run = args.out / "arms" / arm / f"replica-{replica}"
                command = make_restore_command(
                    gem5,
                    config,
                    run / "gem5",
                    frozen_checkpoint,
                    guest,
                    selector,
                    ramulator_config,
                    profile,
                )
                jobs.append((arm, profile, physical, replica, run, command))
        normalized = {json.dumps(normalized_command(job[-1])) for job in jobs}
        if len(normalized) != 1:
            raise RuntimeError(
                "normalized commands differ beyond outdir/physical"
            )
        sample16 = next(
            job[-1] for job in jobs if job[0] == "logical16_physical16"
        )
        sample4 = next(
            job[-1] for job in jobs if job[0] == "logical16_physical4"
        )
        delta = command_delta(sample16, sample4)
        if (
            len(delta) != 2
            or not any(
                item["left"].startswith("--outdir=")
                and item["right"].startswith("--outdir=")
                for item in delta
            )
            or not any(
                item
                == {
                    "left": "--maa_physical_tile_elements=16384",
                    "right": "--maa_physical_tile_elements=4096",
                }
                for item in delta
            )
        ):
            raise RuntimeError("capacity-arm command delta is not exact")

        def run_one(
            job: tuple[str, str, int, int, Path, list[str]]
        ) -> dict[str, object]:
            arm, _profile, physical, replica, run, command = job
            run.mkdir(parents=True)
            atomic_json(run / "restore.command.json", command)
            atomic_text(
                run / "restore.command.txt", shlex.join(command) + "\n"
            )
            with (run / "restore.log").open("wb") as output:
                result = subprocess.run(
                    command, env=env, stdout=output, stderr=subprocess.STDOUT
                )
            atomic_text(run / "restore.exit", f"{result.returncode}\n")
            if result.returncode != 0:
                raise RuntimeError(f"{arm}/replica-{replica}: restore failed")
            return analyze_run(run, arm, replica, physical)

        futures: dict[tuple[str, int], Future[dict[str, object]]] = {}
        with ThreadPoolExecutor(max_workers=6) as executor:
            for job in jobs:
                futures[(job[0], job[3])] = executor.submit(run_one, job)
            rows = [
                futures[(arm, replica)].result()
                for arm, _, _ in ARMS
                for replica in range(1, 4)
            ]
        if common.tree_identity(frozen_checkpoint) != checkpoint_identity:
            raise RuntimeError("shared checkpoint changed during restores")
        for path, digest in expected.items():
            if sha256(path) != digest:
                raise RuntimeError(
                    f"accepted input changed during restores: {path}"
                )
        invariant_fields = (
            "output_hash",
            "reference_elements",
            "index_hash",
            "publisher_issues",
            "publisher_accepts",
            "publisher_write_responses",
            "publisher_terminals",
            "soa_terminals",
            "fallbacks",
            "publisher_source_order_sha256",
            "soa_source_order_sha256",
        )
        invariants = {
            json.dumps(
                {field: row[field] for field in invariant_fields},
                sort_keys=True,
            )
            for row in rows
        }
        if len(invariants) != 1:
            raise RuntimeError(
                "capacity arms or replicas differ in exact ledgers"
            )
        for arm, _, _ in ARMS:
            arm_rows = [row for row in rows if row["arm"] == arm]
            if len({row["first_roi_simTicks"] for row in arm_rows}) != 1:
                raise RuntimeError(f"{arm}: replica simTicks differ")
        first = {row["arm"]: row for row in rows if row["replica"] == 1}
        ticks16 = int(first["logical16_physical16"]["first_roi_simTicks"])
        ticks4 = int(first["logical16_physical4"]["first_roi_simTicks"])
        payload16 = PHYSICAL_TILE_COUNT * 16_384 * 4
        payload4 = PHYSICAL_TILE_COUNT * 4_096 * 4
        report = {
            **plan(args),
            "schema": "dx100.gzp_dual_capacity_result.v1",
            "terminal": True,
            "passed": True,
            "source_commit": git_output("rev-parse", "HEAD"),
            "source_status": "clean",
            "compatibility": compatibility,
            "artifacts": {
                "gem5": {"path": str(gem5), "sha256": GEM5_SHA256},
                "guest": {"path": str(guest), "sha256": GUEST_SHA256},
                "selector": {
                    "path": str(selector),
                    "sha256": SELECTOR_SHA256,
                    "payload": "token_stream_ld dual_masked_index",
                },
                "ramulator_library": {
                    "path": str(ramulator_library),
                    "sha256": RAMULATOR_SHA256,
                },
                "ramulator_config": {
                    "path": str(ramulator_config),
                    "sha256": RAMULATOR_CONFIG_SHA256,
                },
                "shared_frozen_checkpoint": {
                    "path": str(frozen_checkpoint.resolve()),
                    "sha256": CHECKPOINT_SHA256,
                },
            },
            "normalized_command_sha256": hashlib.sha256(
                next(iter(normalized)).encode()
            ).hexdigest(),
            "exact_command_delta": delta,
            "rows": rows,
            "comparison": {
                "physical16_first_roi_simTicks": ticks16,
                "physical4_first_roi_simTicks": ticks4,
                "physical16_over_physical4_speedup": ticks16 / ticks4,
                "physical4_tick_delta": ticks4 - ticks16,
            },
            "hardware": {
                "physical16_spd_payload_bytes": payload16,
                "physical4_spd_payload_bytes": payload4,
                "physical_spd_payload_delta_bytes": payload16 - payload4,
                "fixed_logical_row_offset_metadata": plan(args)[
                    "logical_metadata"
                ],
                "publisher_instances": 1,
                "publisher_bytes_separate": PUBLISHER_BYTES,
                "coherent_backing_bytes_separate": COHERENT_BACKING_BYTES,
            },
        }
        atomic_json(args.out / "results.json", report)
        with (args.out / "results.tsv").open("w", newline="") as output:
            fields = sorted({key for row in rows for key in row})
            writer = csv.DictWriter(output, fields, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
        atomic_text(args.out / "campaign.exit", "0\n")
        atomic_json(
            args.out / "campaign.json", {"terminal": True, "passed": True}
        )
        print("GZP_DUAL_CAPACITY_MATRIX_PASS")
        return 0
    except BaseException as error:
        atomic_text(args.out / "campaign.exit", "1\n")
        atomic_json(
            args.out / "campaign.json",
            {"terminal": True, "passed": False, "error": str(error)},
        )
        raise


def main() -> int:
    args = parse_args()
    if not args.execute:
        print(json.dumps(plan(args), indent=2, sort_keys=True))
        return 0
    try:
        return execute(args)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
