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
        "checkpoint": "hybrid",
        "selector": "token_stream_ld volume_masked_index",
    },
    {
        "name": "dual_masked_index_owner64_pre_a_context64",
        "profile": "hybrid",
        "binary": "hybrid",
        "checkpoint": "hybrid",
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
    parser.add_argument("--native16", required=True, type=Path)
    parser.add_argument("--native4", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--ramulator-config", type=Path, default=DEFAULT_RAMULATOR)
    parser.add_argument("--n", type=int, default=ELEMENTS)
    parser.add_argument("--replicas", type=int, default=3)
    parser.add_argument("--mem-channels", type=int, default=2)
    parser.add_argument("--l3-ports", type=int, default=4)
    parser.add_argument("--expected-gem5-sha256")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.n != ELEMENTS:
        parser.error("the exact full-GZP contract requires --n=1000000")
    if args.replicas < 2:
        parser.error("--replicas must be at least two")
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


def compile_hybrid(path: Path, env: dict[str, str]) -> list[str]:
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
        str(path),
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
        "shared_hybrid_checkpoint": True,
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
        "predicate_publication_bytes": "0",
        "masked_index_additional_buffer_bytes": "0",
        "publisher_instances": "4",
        "publisher_payload_bytes_per_instance": "512",
        "publisher_control_bytes_per_instance": "408",
        "publisher_total_bytes_per_instance": "920",
        "persistent_payload_bytes": "2048",
        "persistent_control_bytes": "1632",
        "persistent_total_bytes": "3680",
    }
    expected_terminal = {
        "treatment": "dual_masked_index_soa_jit",
        "masked_index_windows": "0",
        "dual_masked_index_windows": str(FULL_WINDOWS),
        "published_predicates": "0",
        "published_gradient_values": str(FULL_VALUES),
        "gradient_publication_bytes": str(GRADIENT_PUBLICATION_BYTES),
        "publisher": "gradient_pages_response_bearing_no_predicate",
        "hardware_bytes": "3680",
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
    record["persistent_payload_bytes"] = 2048
    record["persistent_control_bytes"] = 1632
    record["persistent_total_bytes"] = 3680
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


def main() -> int:
    args = parse_args()
    if not args.execute:
        print(json.dumps(plan(args), indent=2, sort_keys=True))
        return 0
    required = (
        args.gem5,
        args.ramulator_library,
        args.native16,
        args.native4,
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
            ("native16", args.native16, "native16"),
            ("native4", args.native4, "native4"),
            ("ramulator_config", args.ramulator_config, "ramulator.yaml"),
        ):
            path = inputs / destination
            digest = common.copy_stable_artifact(source.resolve(), path)
            frozen[name] = path.resolve()
            identities[name] = {"path": str(path.resolve()), "sha256": digest}
        frozen_config, config_identity = common.freeze_config_tree(
            args.config, ROOT / "configs", inputs / "configs"
        )
        hybrid = inputs / "hybrid"
        compile_command = compile_hybrid(hybrid, env)
        frozen["hybrid"] = hybrid.resolve()
        identities["hybrid"] = {
            "path": str(hybrid.resolve()),
            "sha256": sha256(hybrid),
        }
        for name in ("gem5", "native16", "native4", "hybrid"):
            frozen[name].chmod(0o555)
        env["LD_LIBRARY_PATH"] = str(inputs.resolve())

        checkpoints: dict[str, dict[str, object]] = {}
        checkpoint_roots: dict[str, Path] = {}
        selector = args.out / "checkpoints/hybrid/selector.txt"
        for group, binary, profile in (
            ("native16", "native16", "native16"),
            ("native4", "native4", "native4"),
            ("hybrid", "hybrid", "hybrid"),
        ):
            directory = args.out / "checkpoints" / group
            directory.mkdir(parents=True)
            options = str(args.n)
            if group == "hybrid":
                atomic_text(selector, "token_stream_ld volume_masked_index\n")
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
                    atomic_text(selector, str(arm["selector"]) + "\n")
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
                    raise RuntimeError("shared hybrid selector changed during restore")
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
            "compile_command": compile_command,
            "checkpoints": checkpoints,
            "runs": runs,
            "provenance_permits_native_reference_comparison": True,
            "native_reference_reason": "same source commit, gem5, config tree, fixed input and exact output hash; only declared payload profile/binary differs",
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
