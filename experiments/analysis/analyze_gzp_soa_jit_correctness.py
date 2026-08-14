#!/usr/bin/env python3
"""Fail-closed correctness report for the GZP SoA/JIT staging treatment."""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments/scripts"))
import run_general_hybrid_benchmark_matrix as provenance  # noqa: E402

sys.path.insert(0, str(ROOT / "experiments/analysis"))
import analyze_general_hybrid_benchmark_matrix as general  # noqa: E402

EXPECTED_HASH = "11225737641199706160"
EXPECTED_ELEMENTS = "1180000"
EXPECTED_FULL_WINDOWS = 61
EXPECTED_STAGED = 999424
EXPECTED_VOLUME_SOA_INSTRUCTIONS = 61
EXPECTED_BOTH_SOA_INSTRUCTIONS = 122


def one_marker(text: str, prefix: str) -> dict[str, str]:
    lines = [line for line in text.splitlines() if line.startswith(prefix)]
    if len(lines) != 1:
        raise ValueError(f"expected one {prefix!r} marker, got {len(lines)}")
    return general.fields(lines[0])


def sum_stat(stats: dict[str, float], suffix: str) -> int:
    pattern = re.compile(rf"system\.maa\.I\d+_{re.escape(suffix)}$")
    values = [
        value for name, value in stats.items() if pattern.fullmatch(name)
    ]
    if not values or any(not value.is_integer() for value in values):
        raise ValueError(f"missing or non-integral SoA stat {suffix}")
    return int(sum(values))


def validate_soa_trace(
    path: Path, expected_completions: int
) -> dict[str, int]:
    def pair(value: dict[str, str], name: str) -> tuple[int, int]:
        try:
            left, right = value[name].split("/", 1)
            return int(left, 0), int(right, 0)
        except (KeyError, ValueError) as error:
            raise ValueError(f"SoA trace has invalid {name}") from error

    completions = 0
    context_hwm_max = 0
    generations: set[tuple[int, int]] = set()
    for line in path.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        fields = general.fields(line)
        if fields.get("event") != "soa_jit_complete":
            continue
        completions += 1
        if int(fields.get("logical", "-1"), 0) != 16384:
            raise ValueError("SoA trace completion has wrong logical length")
        generation = (
            int(fields.get("unit", "-1"), 0),
            int(fields.get("generation", "-1"), 0),
        )
        if (
            generation in generations
            or generation[0] < 0
            or generation[1] <= 0
        ):
            raise ValueError("SoA trace repeated or has an invalid generation")
        generations.add(generation)
        selected = int(fields.get("selected", "-1"), 0)
        rejected = int(fields.get("predicate_rejected", "-1"), 0)
        if selected + rejected != 16384:
            raise ValueError(
                "SoA trace predicate classification did not close"
            )
        predicate = pair(fields, "predicate_lines")
        a_reads = pair(fields, "a_reads")
        value_reads = pair(fields, "value_reads")
        a_writes = pair(fields, "a_writes")
        if any(
            left != right
            for left, right in (predicate, a_reads, value_reads, a_writes)
        ):
            raise ValueError(
                "SoA trace request/response accounting did not close"
            )
        if predicate[0] != 1024 or a_reads[0] != a_writes[0]:
            raise ValueError("SoA trace predicate/A-line accounting differs")
        if (
            value_reads[0] != selected
            or int(fields.get("aliases", "-1"), 0) != selected
        ):
            raise ValueError(
                "SoA trace selected/value/alias accounting differs"
            )
        context_hwm = int(fields.get("context_hwm", "-1"), 0)
        if context_hwm < 1:
            raise ValueError("SoA trace has invalid context high-water")
        context_hwm_max = max(context_hwm_max, context_hwm)
    if completions != expected_completions:
        raise ValueError(
            f"expected {expected_completions} SoA completions, "
            f"got {completions}"
        )
    return {
        "trace_terminal_completions": completions,
        "trace_context_high_water_max": context_hwm_max,
    }


def validate_soa_stats(
    stats: dict[str, float], expected_instructions: int
) -> dict[str, int]:
    soa = {
        suffix: sum_stat(stats, suffix)
        for suffix in (
            "IND_SoaJitInstructions",
            "IND_SoaJitSelected",
            "IND_SoaJitPredicateRejected",
            "IND_SoaJitPredicateLineReads",
            "IND_SoaJitPredicateLineResponses",
            "IND_SoaJitAReadIssues",
            "IND_SoaJitAReadResponses",
            "IND_SoaJitValueReadIssues",
            "IND_SoaJitValueReadResponses",
            "IND_SoaJitAliasesApplied",
            "IND_SoaJitAWriteIssues",
            "IND_SoaJitAWriteResponses",
            "IND_SoaJitContextHighWater",
            "IND_SoaJitTerminalCompletions",
        )
    }
    if (
        soa["IND_SoaJitInstructions"] != expected_instructions
        or soa["IND_SoaJitTerminalCompletions"] != expected_instructions
    ):
        raise ValueError("SoA/JIT instruction/terminal count differs")
    if (
        soa["IND_SoaJitSelected"] + soa["IND_SoaJitPredicateRejected"]
        != expected_instructions * 16384
    ):
        raise ValueError("SoA/JIT aggregate predicate classification differs")
    if soa["IND_SoaJitPredicateLineReads"] != expected_instructions * 1024:
        raise ValueError("SoA/JIT predicate line count differs")
    for left, right in (
        ("IND_SoaJitPredicateLineReads", "IND_SoaJitPredicateLineResponses"),
        ("IND_SoaJitAReadIssues", "IND_SoaJitAReadResponses"),
        ("IND_SoaJitValueReadIssues", "IND_SoaJitValueReadResponses"),
        ("IND_SoaJitAWriteIssues", "IND_SoaJitAWriteResponses"),
    ):
        if soa[left] != soa[right]:
            raise ValueError(f"SoA/JIT {left}/{right} differ")
    if (
        soa["IND_SoaJitValueReadIssues"] != soa["IND_SoaJitSelected"]
        or soa["IND_SoaJitAliasesApplied"] != soa["IND_SoaJitSelected"]
    ):
        raise ValueError("SoA/JIT selected/value/alias totals differ")
    if soa["IND_SoaJitAReadIssues"] != soa["IND_SoaJitAWriteIssues"]:
        raise ValueError("SoA/JIT A read/write issue totals differ")
    if soa["IND_SoaJitContextHighWater"] < expected_instructions:
        raise ValueError("SoA/JIT context high-water total is invalid")
    return soa


def analyze(root: Path) -> dict[str, object]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "dx100.gzp_soa_jit_matrix.v1":
        raise ValueError("unsupported or missing GZP matrix manifest")
    if manifest.get("source_status") != "clean":
        raise ValueError("matrix source was not clean")
    expected_comparisons = {
        "current_hybrid_vs_volume_only_soa_jit": True,
        "current_hybrid_vs_soa_jit_correctness": False,
    }
    if manifest.get("performance_comparisons") != expected_comparisons:
        raise ValueError("invalid performance-comparison authorization")
    expected_arms = [
        "native16",
        "native4",
        "current_hybrid",
        "volume_only_soa_jit",
        "soa_jit_correctness",
    ]
    arm_names = [str(arm.get("name", "")) for arm in manifest.get("arms", [])]
    if arm_names != expected_arms:
        raise ValueError("GZP matrix does not contain the exact five arms")
    source = manifest.get("source", {})
    if not re.fullmatch(r"[0-9a-f]{40}", str(source.get("commit", ""))):
        raise ValueError("missing source commit identity")
    source_files = source.get("files", {})
    if len(source_files) < 4 or any(
        not re.fullmatch(r"[0-9a-f]{64}", str(value))
        for value in source_files.values()
    ):
        raise ValueError("missing source-file SHA-256 identities")
    artifacts = manifest.get("artifacts", {})
    for name in (
        "gem5",
        "ramulator_library",
        "ramulator_config",
        "native16",
        "native4",
        "hybrid",
    ):
        identity = artifacts.get(name, {})
        if not re.fullmatch(r"[0-9a-f]{64}", str(identity.get("sha256", ""))):
            raise ValueError(f"missing frozen SHA-256 for {name}")
        artifact = Path(str(identity.get("path", "")))
        if (
            not artifact.is_file()
            or provenance.sha256_file(artifact) != identity["sha256"]
        ):
            raise ValueError(f"frozen artifact changed for {name}")
    config_hash = str(manifest.get("config_tree", {}).get("sha256", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", config_hash):
        raise ValueError("missing frozen config-tree SHA-256")
    config_path = Path(str(manifest.get("config_tree", {}).get("path", "")))
    if provenance.tree_identity(config_path)["sha256"] != config_hash:
        raise ValueError("frozen config tree changed")
    for group in ("native16", "native4", "hybrid"):
        checkpoint = manifest.get("checkpoints", {}).get(group, {})
        command = manifest.get("checkpoint_commands", {}).get(group, {})
        if not re.fullmatch(
            r"[0-9a-f]{64}", str(checkpoint.get("sha256", ""))
        ):
            raise ValueError(f"missing checkpoint identity for {group}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(command.get("sha256", ""))):
            raise ValueError(
                f"missing checkpoint command identity for {group}"
            )
        checkpoint_path = root / "checkpoints" / group / "gem5"
        if provenance.tree_identity(checkpoint_path) != checkpoint:
            raise ValueError(f"checkpoint changed for {group}")
        command_path = Path(str(command.get("path", "")))
        if (
            not command_path.is_file()
            or provenance.sha256_file(command_path) != command["sha256"]
        ):
            raise ValueError(f"checkpoint command changed for {group}")

    run_records = {
        (str(run["arm"]), int(run["replica"])): run
        for run in manifest.get("runs", [])
    }

    records: list[dict[str, object]] = []
    keys: set[str] = set()
    predicate_identities: set[tuple[str, str]] = set()
    replicas = int(manifest["replicas"])
    for arm in manifest["arms"]:
        name = str(arm["name"])
        selector = arm["selector"]
        for replica in range(1, replicas + 1):
            run = root / "arms" / name / f"replica-{replica}"
            run_identity = run_records.get((name, replica), {})
            expected_gem5_args = [
                *manifest.get("extra_gem5_args", []),
                *manifest.get("restore_arm_gem5_args", {}).get(name, []),
            ]
            if run_identity.get("gem5_args") != expected_gem5_args:
                raise ValueError(
                    f"{name}/{replica}: restore gem5 settings differ"
                )
            if not re.fullmatch(
                r"[0-9a-f]{64}", str(run_identity.get("command_sha256", ""))
            ):
                raise ValueError(f"{name}/{replica}: missing command identity")
            command_path = Path(str(run_identity.get("command_path", "")))
            if (
                not command_path.is_file()
                or provenance.sha256_file(command_path)
                != run_identity["command_sha256"]
            ):
                raise ValueError(f"{name}/{replica}: command identity changed")
            if selector is not None:
                selector_path = run / "treatment.txt"
                if (
                    selector_path.read_text(encoding="utf-8").strip()
                    != selector
                ):
                    raise ValueError(
                        f"{name}/{replica}: selector content changed"
                    )
                if provenance.sha256_file(selector_path) != run_identity.get(
                    "selector_sha256"
                ):
                    raise ValueError(
                        f"{name}/{replica}: selector identity changed"
                    )
            if general.read_exit(run / "restore.exit") != 0:
                raise ValueError(f"{name}/{replica}: nonzero restore exit")
            log = (run / "restore.log").read_text(
                encoding="utf-8", errors="replace"
            )
            if (
                general.FATAL.search(log)
                or len(general.EXIT.findall(log)) != 1
            ):
                raise ValueError(
                    f"{name}/{replica}: incomplete/fatal terminal log"
                )
            key, correctness = general.correctness("ume-gzp", log)
            if (
                key != EXPECTED_HASH
                or correctness.get("elements") != EXPECTED_ELEMENTS
            ):
                raise ValueError(
                    f"{name}/{replica}: wrong exact output identity"
                )
            keys.add(key)
            stats = general.first_stats(run / "gem5/stats.txt")
            rmw = general.opcode_report(stats)["numInst_INDRMW"]
            mechanism = general.validate_materializer(
                f"{name}/{replica}",
                "ume-gzp",
                "token_stream_ld_correctness_control"
                if selector
                else "native_control",
                selector,
                stats,
                run / "gem5/virtual_trace.log",
            )
            record: dict[str, object] = {
                "arm": name,
                "replica": replica,
                "simTicks": int(stats["simTicks"]),
                "output_hash": key,
                "numInst_INDRMW": rmw,
                "materializer_retires": mechanism["materializer_retires"],
            }
            if selector is not None:
                treatment = one_marker(log, "UME_GZP_RMW_TREATMENT ")
                predicate = one_marker(log, "UME_GZP_PREDICATE_BUFFER ")
                terminal = one_marker(log, "UME_GZP_TERMINAL ")
                if (
                    terminal.get("result") != "PASS"
                    or predicate.get("elements") != "1000000"
                    or predicate.get("semantic") != "corner_type_gt_0"
                    or predicate.get("phase") != "pre_checkpoint"
                    or predicate.get("immutable") != "1"
                    or not predicate.get("hash", "").isdigit()
                ):
                    raise ValueError(
                        f"{name}/{replica}: invalid GZP "
                        "terminal/predicate marker"
                    )
                active = int(predicate.get("active", "-1"))
                if not 0 < active < 1_000_000:
                    raise ValueError(
                        f"{name}/{replica}: invalid active predicate count"
                    )
                if terminal.get("predicate_hash") != predicate["hash"]:
                    raise ValueError(
                        f"{name}/{replica}: predicate hash changed in ROI"
                    )
                predicate_identities.add((predicate["hash"], str(active)))
                if name == "current_hybrid":
                    expected_terminal = {
                        "treatment": "legacy_4k",
                        "full_windows": "0",
                        "volume_only_windows": "0",
                        "staged_predicates": "0",
                        "staged_gradient_values": "0",
                        "publisher": "none",
                        "performance_promotable": "1",
                        "result": "PASS",
                    }
                    if (
                        treatment.get("mode") != "legacy_4k"
                        or any(
                            terminal.get(key) != value
                            for key, value in expected_terminal.items()
                        )
                        or rmw != 490
                    ):
                        raise ValueError(
                            "current hybrid did not preserve the 4K RMW path"
                        )
                    if any(
                        sum_stat(stats, suffix) != 0
                        for suffix in (
                            "IND_SoaJitInstructions",
                            "IND_SoaJitTerminalCompletions",
                        )
                    ):
                        raise ValueError(
                            "legacy arm unexpectedly executed SoA/JIT"
                        )
                elif name == "volume_only_soa_jit":
                    expected_terminal = {
                        "treatment": "volume_only_soa_jit",
                        "full_windows": "0",
                        "volume_only_windows": str(EXPECTED_FULL_WINDOWS),
                        "staged_predicates": "0",
                        "staged_gradient_values": "0",
                        "publisher": "precheckpoint_uint32_predicate",
                        "performance_promotable": "1",
                        "result": "PASS",
                    }
                    if treatment.get("mode") != "volume_only_soa_jit" or any(
                        terminal.get(key) != value
                        for key, value in expected_terminal.items()
                    ):
                        raise ValueError(
                            "volume-only marker does not match the frozen "
                            "treatment"
                        )
                    if rmw != 307:
                        raise ValueError(
                            f"volume-only arm has {rmw} RMWs, expected 307"
                        )
                    soa = validate_soa_stats(
                        stats, EXPECTED_VOLUME_SOA_INSTRUCTIONS
                    )
                    record.update(soa)
                    record.update(
                        validate_soa_trace(
                            run / "gem5/virtual_trace.log",
                            EXPECTED_VOLUME_SOA_INSTRUCTIONS,
                        )
                    )
                elif name == "soa_jit_correctness":
                    expected_terminal = {
                        "treatment": "soa_jit_correctness",
                        "full_windows": str(EXPECTED_FULL_WINDOWS),
                        "volume_only_windows": "0",
                        "staged_predicates": str(EXPECTED_STAGED),
                        "staged_gradient_values": str(EXPECTED_STAGED),
                        "publisher": "cpu_after_spd_completion",
                        "performance_promotable": "0",
                        "result": "PASS",
                    }
                    if treatment.get("mode") != "soa_jit_correctness" or any(
                        terminal.get(key) != value
                        for key, value in expected_terminal.items()
                    ):
                        raise ValueError(
                            "SoA/JIT marker does not match the frozen "
                            "treatment"
                        )
                    if rmw != 124:
                        raise ValueError(
                            f"SoA/JIT arm has {rmw} RMWs, expected 124"
                        )
                    soa = validate_soa_stats(
                        stats, EXPECTED_BOTH_SOA_INSTRUCTIONS
                    )
                    record.update(soa)
                    record.update(
                        validate_soa_trace(
                            run / "gem5/virtual_trace.log",
                            EXPECTED_BOTH_SOA_INSTRUCTIONS,
                        )
                    )
            records.append(record)
    if keys != {EXPECTED_HASH}:
        raise ValueError("cross-arm exact output fingerprints differ")
    if len(predicate_identities) != 1:
        raise ValueError("hybrid arms disclosed different predicate buffers")
    current_ticks = [
        int(record["simTicks"])
        for record in records
        if record["arm"] == "current_hybrid"
    ]
    volume_ticks = [
        int(record["simTicks"])
        for record in records
        if record["arm"] == "volume_only_soa_jit"
    ]
    current_median = statistics.median(current_ticks)
    volume_median = statistics.median(volume_ticks)
    return {
        "schema": "dx100.gzp_soa_jit_analysis.v2",
        "status": "PASS",
        "exact_output_hash": EXPECTED_HASH,
        "records": records,
        "simulated_metric": "simTicks",
        "host_time_used": False,
        "performance_comparisons": manifest["performance_comparisons"],
        "performance": {
            "metric": "simTicks",
            "current_hybrid_median": current_median,
            "volume_only_soa_jit_median": volume_median,
            "speedup_current_over_volume_only": (
                current_median / volume_median
            ),
        },
        "blocker": (
            "response-bearing SPD publisher is not wired to a guest opcode"
        ),
    }


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} MATRIX_ROOT", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).resolve()
    try:
        report = analyze(root)
        output = root / "analysis"
        output.mkdir(exist_ok=True)
        (output / "gzp_soa_jit_correctness.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        performance = report["performance"]
        lines = [
            "# GZP SoA/JIT matrix",
            "",
            "Status: **PASS**. All arms have exact output hash "
            f"`{EXPECTED_HASH}`.",
            "",
            "The current-hybrid versus volume-only comparison is authorized "
            "because its predicate is immutable and published before the "
            "checkpoint. The two-RMW CPU-staging arm remains "
            "correctness-only.",
            "",
            "The simulated `simTicks` median speedup (current / volume-only) "
            "is `{:.6f}x` ({} / {}). Host time is not used.".format(
                performance["speedup_current_over_volume_only"],
                performance["current_hybrid_median"],
                performance["volume_only_soa_jit_median"],
            ),
            "",
            "| arm | replica | simTicks | RMW instructions |",
            "|---|---:|---:|---:|",
        ]
        for record in report["records"]:
            lines.append(
                "| {arm} | {replica} | {simTicks} | "
                "{numInst_INDRMW} |".format(**record)
            )
        (output / "gzp_soa_jit_correctness.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
    except (KeyError, OSError, TypeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"PASS: {len(report['records'])} exact GZP runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
