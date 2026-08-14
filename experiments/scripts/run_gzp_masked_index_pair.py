#!/usr/bin/env python3
"""Run a same-checkpoint GZP volume predicate/masked-index pair."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_general_hybrid_benchmark_matrix as common  # noqa: E402

DEFAULT_CONFIG = ROOT / "configs/deprecated/example/se.py"
DEFAULT_RAMULATOR = ROOT / "ext/ramulator2/ramulator2/example_gem5_config.yaml"
ARMS = (
    ("separate_predicate", "token_stream_ld volume_soa_jit"),
    ("masked_index", "token_stream_ld volume_masked_index"),
)
EXPECTED_FULL_HASH = "11225737641199706160"
MASKED_INDEX_GEM5_COMMIT = "866150f94fa6944433e5ef12115a1e948137c105"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--gem5", required=True, type=Path)
    parser.add_argument("--ramulator-library", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--ramulator-config", type=Path, default=DEFAULT_RAMULATOR
    )
    parser.add_argument("--n", type=int, default=16384)
    parser.add_argument("--mem-channels", type=int, default=2)
    parser.add_argument("--l3-ports", type=int, default=4)
    parser.add_argument("--expected-gem5-sha256")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.n < 16384:
        parser.error("--n must contain at least one complete 16K window")
    if args.mem_channels < 1 or not 1 <= args.l3_ports <= 16:
        parser.error("invalid memory-channel or L3-port count")
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
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value)
    temporary.replace(path)


def atomic_json(path: Path, value: object) -> None:
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def run_logged(command: list[str], log: Path, env: dict[str, str]) -> int:
    atomic_json(log.with_suffix(".command.json"), command)
    atomic_text(log.with_suffix(".command.txt"), shlex.join(command) + "\n")
    with log.open("wb") as output:
        result = subprocess.run(
            command, stdout=output, stderr=subprocess.STDOUT, env=env
        )
    atomic_text(log.with_suffix(".exit"), f"{result.returncode}\n")
    return result.returncode


def source_status() -> str:
    return subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def source_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def compile_guest(guest: Path, env: dict[str, str]) -> list[str]:
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
        "-DMAA_VIRTUAL_GATHER",
        "-DMAA_GENERAL_VIRTUAL_CONSUMER",
        "-DMAA_CONSUMER_TILE_SIZE=4096",
        "-DUME_GZP_SOA_JIT_RMW",
        "-DUME_FIXED_INPUT",
        "-DUME_OUTPUT_FINGERPRINT",
        "-DNUM_CORES=4",
        "-DTILE_SIZE=16384",
        "-DMAA_MEM_SIZE=0x80000000",
        str(ROOT / "util/m5/src/abi/x86/m5op.S"),
        str(ROOT / "benchmarks/UME/gradzatp.cpp"),
        "-o",
        str(guest),
    ]
    result = subprocess.run(command, env=env, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"guest compile failed with rc={result.returncode}")
    return command


def parse_fields(line: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for token in line.split()[1:]:
        if "=" in token:
            key, value = token.split("=", 1)
            fields[key] = value
    return fields


def exactly_one(lines: list[str], prefix: str) -> str:
    matches = [line for line in lines if line.startswith(prefix)]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {prefix!r}, found {len(matches)}")
    return matches[0]


def first_stats(path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    active = False
    complete = False
    for line in path.read_text().splitlines():
        if line.startswith("---------- Begin Simulation Statistics"):
            if active or complete:
                continue
            active = True
            continue
        if line.startswith("---------- End Simulation Statistics") and active:
            complete = True
            break
        if not active:
            continue
        fields = line.split()
        if len(fields) >= 2:
            try:
                result[fields[0]] = int(float(fields[1]))
            except ValueError:
                pass
    if not complete or not result:
        raise RuntimeError(f"missing complete first stats window: {path}")
    return result


def sum_suffix(stats: dict[str, int], suffix: str) -> int:
    values = [value for name, value in stats.items() if name.endswith(suffix)]
    if not values:
        raise RuntimeError(f"missing stats suffix {suffix}")
    return sum(values)


def analyze_run(name: str, run: Path, n: int) -> dict[str, int | str]:
    log_path = run / "restore.log"
    stats_path = run / "gem5/stats.txt"
    trace_path = run / "gem5/virtual_trace.log"
    if (run / "restore.exit").read_text().strip() != "0":
        raise RuntimeError(f"{name}: wrapper exit is not zero")
    if not stats_path.is_file() or not trace_path.is_file():
        raise RuntimeError(f"{name}: missing stats or trace")
    log_text = log_path.read_text(errors="replace")
    lines = log_text.splitlines()
    if (
        len(
            re.findall(
                r"Exiting @ tick \d+ because m5_exit instruction encountered",
                log_text,
            )
        )
        != 1
    ):
        raise RuntimeError(f"{name}: missing unique m5_exit marker")
    if re.search(
        r"\b(?:panic|fatal|segmentation fault|Assertion)\b", log_text, re.I
    ):
        raise RuntimeError(f"{name}: fatal marker in restore log")

    output = parse_fields(exactly_one(lines, "UME_OUTPUT_FP "))
    reference = parse_fields(exactly_one(lines, "UME_REFERENCE_PASS "))
    ledger = parse_fields(exactly_one(lines, "UME_GZP_MASKED_INDEX_LEDGER "))
    terminal = parse_fields(exactly_one(lines, "UME_GZP_TERMINAL "))
    if (
        output.get("nonfinite") != "0"
        or reference.get("point_volume_errors") != "0"
        or reference.get("point_gradient_errors") != "0"
    ):
        raise RuntimeError(f"{name}: exact output/reference gate failed")
    zero_ledger = (
        "active_uint32_max",
        "active_illegal_index",
        "inactive_legal_index",
        "inactive_non_sentinel",
    )
    if (
        ledger.get("result") != "PASS"
        or ledger.get("exact_equivalence") != "1"
        or any(int(ledger.get(key, "-1")) != 0 for key in zero_ledger)
    ):
        raise RuntimeError(f"{name}: guest masked-index ledger failed")
    selected = int(ledger["full_selected"])
    rejected = int(ledger["full_rejected"])
    full_windows = n // 16384
    if selected + rejected != full_windows * 16384:
        raise RuntimeError(f"{name}: guest classification is incomplete")

    stats = first_stats(stats_path)
    runtime_selected = sum_suffix(stats, "IND_SoaJitSelected")
    runtime_rejected = sum_suffix(stats, "IND_SoaJitPredicateRejected")
    predicate_lines = sum_suffix(stats, "IND_SoaJitPredicateLineReads")
    predicate_responses = sum_suffix(stats, "IND_SoaJitPredicateLineResponses")
    if runtime_selected != selected or runtime_rejected != rejected:
        raise RuntimeError(f"{name}: guest/runtime classification mismatch")
    if sum_suffix(stats, "IND_SoaJitInstructions") != full_windows:
        raise RuntimeError(f"{name}: unexpected SoA/JIT instruction count")
    if predicate_lines != predicate_responses:
        raise RuntimeError(f"{name}: predicate request/response mismatch")
    expected_mode = (
        "separate_array" if name == "separate_predicate" else "masked_index"
    )
    trace_fields = [
        parse_fields(line)
        for line in trace_path.read_text(errors="replace").splitlines()
        if "event=soa_jit_complete" in line and "terminal=1" in line
    ]
    if len(trace_fields) != full_windows or any(
        field.get("predicate_mode") != expected_mode
        or int(field.get("selected", "-1"))
        + int(field.get("predicate_rejected", "-1"))
        != 16384
        for field in trace_fields
    ):
        raise RuntimeError(f"{name}: terminal generation ledger failed")
    if name == "separate_predicate":
        if predicate_lines != full_windows * 1024:
            raise RuntimeError(f"{name}: unexpected predicate-line count")
        if (
            terminal.get("treatment") != "volume_only_soa_jit"
            or terminal.get("predicate_publications") != "1"
        ):
            raise RuntimeError(
                f"{name}: treatment/publication contract failed"
            )
    else:
        if predicate_lines != 0:
            raise RuntimeError(f"{name}: predicate traffic was not eliminated")
        if (
            terminal.get("treatment") != "volume_masked_index_soa_jit"
            or terminal.get("predicate_publications") != "0"
        ):
            raise RuntimeError(
                f"{name}: treatment/publication contract failed"
            )
        if any(
            field.get("masked_index_compare_bits") != "32"
            or field.get("masked_index_mode_state_bits") != "1"
            or field.get("masked_index_additional_buffer_bytes") != "0"
            for field in trace_fields
        ):
            raise RuntimeError(f"{name}: hardware-cost ledger failed")
    if (
        terminal.get("published_predicates") != "0"
        or terminal.get("result") != "PASS"
    ):
        raise RuntimeError(f"{name}: unexpected live predicate publication")

    return {
        "arm": name,
        "simTicks": stats["simTicks"],
        "fill_cycles": sum_suffix(stats, "IND_CyclesFill"),
        "request_cycles": sum_suffix(stats, "IND_CyclesRequest"),
        "index_lines": sum_suffix(stats, "IND_VirtIndexLineReads"),
        "predicate_lines": predicate_lines,
        "predicate_publications": int(terminal["predicate_publications"]),
        "predicate_publication_bytes": int(
            terminal["predicate_publication_bytes"]
        ),
        "selected": runtime_selected,
        "rejected": runtime_rejected,
        "output_hash": output["output_hash"],
        "index_hash": ledger["index_hash"],
        "full_windows": full_windows,
    }


def compare(
    rows: list[dict[str, int | str]], n: int
) -> dict[str, int | str | float]:
    baseline, masked = rows
    for key in (
        "selected",
        "rejected",
        "output_hash",
        "index_hash",
        "full_windows",
    ):
        if baseline[key] != masked[key]:
            raise RuntimeError(f"unmatched pair field {key}")
    if n == 1_000_000 and baseline["output_hash"] != EXPECTED_FULL_HASH:
        raise RuntimeError("full fixed-input output hash changed")
    predicate_lines_avoided = int(baseline["predicate_lines"]) - int(
        masked["predicate_lines"]
    )
    predicate_publications_avoided = int(
        baseline["predicate_publications"]
    ) - int(masked["predicate_publications"])
    expected_predicate_lines = n // 16384 * 1024
    if (
        predicate_lines_avoided != expected_predicate_lines
        or predicate_publications_avoided != 1
    ):
        raise RuntimeError("predicate elimination delta is not exact")
    return {
        "simTicks_delta": int(masked["simTicks"]) - int(baseline["simTicks"]),
        "baseline_over_masked_speedup": int(baseline["simTicks"])
        / int(masked["simTicks"]),
        "fill_cycles_delta": int(masked["fill_cycles"])
        - int(baseline["fill_cycles"]),
        "request_cycles_delta": int(masked["request_cycles"])
        - int(baseline["request_cycles"]),
        "predicate_lines_avoided": predicate_lines_avoided,
        "predicate_bytes_avoided": predicate_lines_avoided * 64,
        "predicate_publications_avoided": predicate_publications_avoided,
        "predicate_publication_bytes_avoided": int(
            baseline["predicate_publication_bytes"]
        )
        - int(masked["predicate_publication_bytes"]),
        "incremental_compare_bits": 32,
        "incremental_mode_state_bits": 1,
        "incremental_buffer_bytes": 0,
        "output_hash": baseline["output_hash"],
    }


def main() -> int:
    args = parse_args()
    plan = {
        "schema": "dx100.gzp_masked_index_pair.v1",
        "n": args.n,
        "arms": [name for name, _selector in ARMS],
        "shared_guest": True,
        "shared_checkpoint": True,
        "only_treatment": "SoA/JIT word-five predicate mode",
        "geometry": {"logical": 16384, "physical_spd": 4096},
        "hardware_cost": {
            "compare_bits": 32,
            "mode_state_bits": 1,
            "buffer_bytes": 0,
        },
    }
    if not args.execute:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    required = (
        args.gem5,
        args.ramulator_library,
        args.config,
        args.ramulator_config,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("missing inputs: " + ", ".join(missing))
    if source_status():
        raise SystemExit("evidence execution requires a clean source tree")
    if args.out.exists():
        raise SystemExit(f"refusing existing output: {args.out}")
    if sha256(args.gem5) != args.expected_gem5_sha256:
        raise SystemExit("gem5 SHA-256 does not match the required identity")

    args.out.mkdir(parents=True)
    atomic_text(args.out / "campaign.exit", "running\n")
    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = "4"
    env["OMP_PROC_BIND"] = "false"
    library_path = str(args.ramulator_library.resolve().parent)
    if env.get("LD_LIBRARY_PATH"):
        library_path += ":" + env["LD_LIBRARY_PATH"]
    env["LD_LIBRARY_PATH"] = library_path
    try:
        inputs = args.out / "inputs"
        inputs.mkdir()
        guest = inputs / "gradzatp_maa_16K_general_soa_jit_fp"
        compile_command = compile_guest(guest, env)
        selector = inputs / "treatment.txt"
        atomic_text(selector, ARMS[0][1] + "\n")
        ramulator = inputs / "ramulator.yaml"
        shutil.copy2(args.ramulator_config, ramulator)
        frozen_config, config_identity = common.freeze_config_tree(
            args.config, ROOT / "configs", inputs / "configs"
        )
        checkpoint = args.out / "checkpoint"
        checkpoint.mkdir()
        checkpoint_command = common.checkpoint_command(
            args.gem5.resolve(),
            frozen_config,
            checkpoint,
            guest,
            f"{args.n} {selector}",
        )
        if (
            run_logged(checkpoint_command, args.out / "checkpoint.log", env)
            != 0
        ):
            raise RuntimeError("checkpoint creation failed")
        checkpoint_log = (args.out / "checkpoint.log").read_text(
            errors="replace"
        )
        if (
            len(
                re.findall(
                    r"Exiting @ tick \d+ because checkpoint", checkpoint_log
                )
            )
            != 1
        ):
            raise RuntimeError("checkpoint terminal marker is not unique")
        checkpoint_identity = common.tree_identity(checkpoint)

        extra = [
            "--maa_virtual_response_slots=1152",
            "--maa_virtual_response_word_pool=2304",
            "--maa_virtual_combine_slots=512",
            "--maa_virtual_combine_words=4096",
            "--maa_virtual_combine_ways=16",
            "--maa_virtual_words_per_cycle=4",
            "--maa_virtual_combine_banks=8",
            "--maa_virtual_index_buffer_lines=8",
            "--maa_soa_jit_active_contexts=8",
            "--maa_soa_jit_value_lookahead=8",
            "--maa_soa_jit_value_cache_enable",
            "--maa_soa_jit_predicate_active_credits=16",
            "--maa_soa_jit_active_value_owners=32",
            "--maa_soa_jit_apply_lanes=1",
        ]
        rows: list[dict[str, int | str]] = []
        runs: list[dict[str, object]] = []
        for name, payload in ARMS:
            run = args.out / "runs" / name
            run.mkdir(parents=True)
            atomic_text(selector, payload + "\n")
            atomic_text(run / "frozen_treatment.txt", payload + "\n")
            selector_hash = sha256(selector)
            command = common.restore_command(
                args.gem5.resolve(),
                frozen_config,
                run / "gem5",
                checkpoint,
                guest,
                f"{args.n} {selector}",
                "hybrid",
                ramulator,
                args.mem_channels,
                args.l3_ports,
                extra,
            )
            if (
                common.tree_identity(checkpoint)["sha256"]
                != checkpoint_identity["sha256"]
            ):
                raise RuntimeError("shared checkpoint changed before restore")
            if run_logged(command, run / "restore.log", env) != 0:
                raise RuntimeError(f"{name} restore failed")
            if sha256(selector) != selector_hash:
                raise RuntimeError(f"{name} selector changed during restore")
            if (
                common.tree_identity(checkpoint)["sha256"]
                != checkpoint_identity["sha256"]
            ):
                raise RuntimeError("shared checkpoint changed during restore")
            rows.append(analyze_run(name, run, args.n))
            runs.append(
                {
                    "arm": name,
                    "selector": payload,
                    "selector_sha256": selector_hash,
                    "command_sha256": sha256(run / "restore.command.json"),
                }
            )
        summary = compare(rows, args.n)
        manifest = {
            **plan,
            "source": {
                "commit": source_commit(),
                "status": "clean",
                "gradzatp_sha256": sha256(
                    ROOT / "benchmarks/UME/gradzatp.cpp"
                ),
                "runner_sha256": sha256(Path(__file__)),
            },
            "gem5": {
                "path": str(args.gem5.resolve()),
                "sha256": sha256(args.gem5),
                "masked_index_source_commit": MASKED_INDEX_GEM5_COMMIT,
            },
            "guest": {"path": str(guest), "sha256": sha256(guest)},
            "compile_command": compile_command,
            "ramulator_library": {
                "path": str(args.ramulator_library.resolve()),
                "sha256": sha256(args.ramulator_library),
            },
            "ramulator_config": {
                "path": str(ramulator),
                "sha256": sha256(ramulator),
            },
            "config_tree": config_identity,
            "checkpoint": checkpoint_identity,
            "checkpoint_command_sha256": sha256(
                args.out / "checkpoint.command.json"
            ),
            "runs": runs,
            "simulated_metric": "simTicks",
            "host_time_metric_authorized": False,
        }
        atomic_json(args.out / "manifest.json", manifest)
        atomic_json(
            args.out / "results.json", {"rows": rows, "summary": summary}
        )
        with (args.out / "results.tsv").open("w", newline="") as output:
            writer = csv.DictWriter(
                output, fieldnames=list(rows[0]), delimiter="\t"
            )
            writer.writeheader()
            writer.writerows(rows)
        atomic_text(
            args.out / "summary.txt",
            "".join(f"{key}={value}\n" for key, value in summary.items()),
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        atomic_text(args.out / "campaign.exit", "1\n")
        print(f"error: {error}", file=sys.stderr)
        return 1
    atomic_text(args.out / "campaign.exit", "0\n")
    print((args.out / "results.tsv").read_text(), end="")
    print((args.out / "summary.txt").read_text(), end="")
    print("GZP_MASKED_INDEX_PAIR_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
