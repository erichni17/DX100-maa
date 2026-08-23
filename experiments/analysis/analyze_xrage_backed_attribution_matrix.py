#!/usr/bin/env python3
"""Fail-closed analysis for the repeated four-arm XRAGE backed matrix."""

from __future__ import annotations

import configparser
import csv
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERAL_PATH = (
    ROOT / "experiments/analysis/analyze_general_hybrid_benchmark_matrix.py"
)
spec = importlib.util.spec_from_file_location(
    "general_hybrid_analyzer", GENERAL_PATH
)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load general hybrid analyzer helpers")
general = importlib.util.module_from_spec(spec)
spec.loader.exec_module(general)

EXPECTED_INPUT_SHA256 = (
    "70e3d82973d7a93300db950d2c81e9db5b6a37273b0f21da8344302ce53022d9"
)
EXPECTED_ARMS = ("native16", "native4", "backed16", "backed4")
EXPECTED_OPCODES = {
    "native16": (4, 4, 4, 4),
    "native4": (16, 16, 16, 16),
    # Token-bound page materializers are intercepted before ordinary stream
    # dispatch and therefore do not increment numInst_STRRD. Their dedicated
    # submit/page/retire counters below are the execution proof.
    "backed16": (4, 0, 16, 16),
    "backed4": (4, 0, 16, 16),
}
HARDWARE_REPORT_BOUNDARY = {
    "active_payload_capacity_bytes_semantics": (
        "payload-capacity subtotal only; not total hardware, area, or PPA"
    ),
    "matched_claim": (
        "exact 1,572,864-byte physical SPD payload reduction with a fixed "
        "instruction path and fixed logical16 metadata"
    ),
    "backed_pair_retains_identical_logical16_row_offset_metadata": True,
    "excluded_from_active_payload_capacity_bytes": [
        "descriptor, header, and readiness bits",
        "nonpayload tags and control beyond separately emitted materializer controls",
        "ports, arbitration, and wiring",
        "SRAM periphery",
        "synthesized area, power, and timing",
    ],
    "prohibited_interpretation": (
        "the 1,572,864-byte delta is not total DX100 hardware cost"
    ),
}
EXIT = re.compile(
    r"Exiting @ tick \d+ because m5_exit instruction encountered"
)
FATAL = re.compile(
    r"(^|\b)(panic|fatal|assert(?:ion)? failed|abort|segmentation fault)(\b|:)",
    re.IGNORECASE,
)
DIGEST = re.compile(
    r"unit=(\d+) instruction_tick=(\d+) count=(\d+) "
    r"fnv=0x([0-9a-fA-F]{16}) mix=0x([0-9a-fA-F]{16})"
)
ISSUE = re.compile(
    r"unit=(\d+) instruction_tick=(\d+) sequence=(\d+) "
    r"addr=(0x[0-9a-f]+) bounded=(\d) virtual=(\d) direct_index=(\d)"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_exit(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid exit marker {path}") from error


def artifact_hashes(path: Path) -> dict[str, str]:
    records: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        expected, raw_path = line.split(maxsplit=1)
        artifact = Path(raw_path)
        if not artifact.is_file() or sha256_file(artifact) != expected:
            raise ValueError(f"guest build artifact changed: {artifact}")
        records[artifact.name] = expected
    return records


def section(path: Path) -> configparser.SectionProxy:
    config = configparser.RawConfigParser(strict=False)
    config.read(path)
    if not config.has_section("system.maa"):
        raise ValueError(f"{path}: missing system.maa")
    return config["system.maa"]


def digest_records(path: Path) -> dict[int, list[tuple[int, int, int]]]:
    records: dict[int, list[tuple[int, int, int]]] = {}
    for match in DIGEST.finditer(
        path.read_text(encoding="utf-8", errors="replace")
    ):
        unit, _tick, count, fnv, mix = match.groups()
        records.setdefault(int(unit), []).append(
            (int(count), int(fnv, 16), int(mix, 16))
        )
    if not records:
        raise ValueError(f"{path}: missing MAAIssueDigest records")
    return records


def issue_trace_summary(path: Path) -> dict[str, int]:
    groups: dict[tuple[int, int], list[int]] = {}
    direct = 0
    nondirect = 0
    for match in ISSUE.finditer(
        path.read_text(encoding="utf-8", errors="replace")
    ):
        (
            unit,
            tick,
            sequence,
            _address,
            _bounded,
            _virtual,
            direct_index,
        ) = match.groups()
        key = (int(unit), int(tick))
        groups.setdefault(key, []).append(int(sequence))
        if int(direct_index):
            direct += 1
        else:
            nondirect += 1
    if not groups:
        raise ValueError(f"{path}: missing MAAIssueTrace records")
    for key, values in groups.items():
        if values != list(range(len(values))):
            raise ValueError(f"{path}: non-contiguous request order for {key}")
    return {
        "instructions": len(groups),
        "requests": direct + nondirect,
        "direct_index_requests": direct,
        "non_direct_index_requests": nondirect,
    }


def normalized_backed_command(path: Path) -> tuple[list[str], int]:
    command = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(command, list) or not all(
        isinstance(x, str) for x in command
    ):
        raise ValueError(f"{path}: invalid command JSON")
    physical = None
    normalized = []
    for argument in command:
        if argument.startswith("--outdir="):
            normalized.append("--outdir=RUN")
        elif argument.startswith("--maa_physical_tile_elements="):
            physical = int(argument.split("=", 1)[1])
            normalized.append("--maa_physical_tile_elements=PHYSICAL")
        else:
            normalized.append(argument)
    if physical is None:
        raise ValueError(f"{path}: physical capacity option missing")
    return normalized, physical


def materializer_capacity(trace: Path) -> dict[str, int]:
    submits = []
    for line in trace.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        value = general.fields(line)
        if value.get("event") != "page_materialization_submit":
            continue
        fields = (
            "line_buffer_bytes",
            "control_bytes",
            "direct_stage_control_bytes",
            "page_spd_bytes",
            "charged_two_page_spd_bytes",
        )
        try:
            submits.append({name: int(value[name], 0) for name in fields})
        except (KeyError, ValueError) as error:
            raise ValueError(
                "materializer submit lacks exact capacity fields"
            ) from error
    if not submits:
        raise ValueError("backed trace has no materializer capacity record")
    common = {
        key: {record[key] for record in submits}
        for key in (
            "line_buffer_bytes",
            "control_bytes",
            "direct_stage_control_bytes",
        )
    }
    if any(len(values) != 1 for values in common.values()):
        raise ValueError("materializer static capacity changed within one run")
    return {
        "submit_records": len(submits),
        **{key: next(iter(values)) for key, values in common.items()},
        "page_spd_bytes": max(record["page_spd_bytes"] for record in submits),
        "charged_two_page_spd_bytes": max(
            record["charged_two_page_spd_bytes"] for record in submits
        ),
    }


def exact_int(stats: dict[str, float], suffix: str) -> int:
    value = stats.get(f"system.maa.{suffix}", 0.0)
    if not value.is_integer():
        raise ValueError(f"non-integral system.maa.{suffix}")
    return int(value)


def hardware_capacity(
    maa: configparser.SectionProxy, backed: bool
) -> dict[str, int]:
    cores = int(maa["num_cores"])
    tiles_per_core = int(maa["num_tiles_per_core"])
    physical = int(maa["physical_tile_elements"])
    indirect_units = int(maa["num_maas"]) * int(
        maa["num_indirect_units_per_maa"]
    )
    spd = cores * tiles_per_core * physical * 4
    if not backed:
        return {
            "tiles_total": cores * tiles_per_core,
            "physical_spd_payload_bytes": spd,
            "direct_index_feeder_bytes": 0,
            "source_response_pool_bytes": 0,
            "destination_combiner_bytes": 0,
            "materializer_line_buffer_bytes": 0,
            "active_payload_capacity_bytes": spd,
        }
    index = int(maa["virtual_index_buffer_lines"]) * 64 * indirect_units
    response = int(maa["virtual_response_word_pool"]) * 8 * indirect_units
    combine = int(maa["virtual_combine_words"]) * 8 * indirect_units
    # Four fixed contexts x sixteen 64-byte credits. This is also emitted as
    # line_buffer_bytes in every materializer submit trace and cross-checked.
    materializer = 4 * 16 * 64
    return {
        "tiles_total": cores * tiles_per_core,
        "physical_spd_payload_bytes": spd,
        "direct_index_feeder_bytes": index,
        "source_response_pool_bytes": response,
        "destination_combiner_bytes": combine,
        "materializer_line_buffer_bytes": materializer,
        "active_payload_capacity_bytes": spd
        + index
        + response
        + combine
        + materializer,
    }


def analyze(root: Path) -> dict[str, object]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "dx100.xrage_backed_attribution_matrix.v1":
        raise ValueError("unsupported matrix manifest")
    replicas = int(manifest["replicas"])
    if replicas < 2 or manifest.get("timeout") is not None:
        raise ValueError("matrix is not repeated or declares a timeout")
    arms = manifest.get("arms")
    if (
        not isinstance(arms, list)
        or tuple(arm.get("name") for arm in arms) != EXPECTED_ARMS
    ):
        raise ValueError("matrix is not the exact four-arm contract")
    if manifest.get("fused_direct_sink") is not False:
        raise ValueError("fused/direct-sink mode was not kept separate")
    artifacts = manifest["artifacts"]
    for name, artifact in artifacts.items():
        path = Path(artifact["path"])
        if sha256_file(path) != artifact["sha256"]:
            raise ValueError(f"frozen artifact changed: {name}")
    if artifacts["workload_input"]["sha256"] != EXPECTED_INPUT_SHA256:
        raise ValueError(
            "XRAGE input identity does not match the accepted 64K case"
        )
    provenance = manifest.get("provenance")
    if not isinstance(provenance, dict) or not (
        provenance.get("simulator_source_tree_matches_lead") is True
        and provenance.get("guest_source_commit_in_lead_history") is True
    ):
        raise ValueError("simulator/guest source provenance is not lead-bound")
    simulator = json.loads(
        Path(artifacts["simulator_provenance"]["path"]).read_text(
            encoding="utf-8"
        )
    )
    if simulator.get("source_commit") != provenance["simulator_source_commit"]:
        raise ValueError("simulator source provenance commit mismatch")
    simulator_gem5 = simulator.get("artifacts", {}).get("gem5", {})
    if simulator_gem5.get("sha256") != artifacts["gem5"]["sha256"]:
        raise ValueError(
            "gem5 hash does not match accepted simulator provenance"
        )
    guest_manifest = {}
    for line in (
        Path(artifacts["guest_build_manifest"]["path"])
        .read_text(encoding="utf-8")
        .splitlines()
    ):
        key, value = line.split("=", 1)
        guest_manifest[key] = value
    if (
        guest_manifest.get("source_commit")
        != provenance["guest_source_commit"]
    ):
        raise ValueError("guest build source commit mismatch")
    guest_hashes = artifact_hashes(
        Path(artifacts["guest_build_artifacts"]["path"])
    )
    for key, name in (
        ("native16", "spatter_maa_xrage_runtime_verify_16K"),
        ("native4", "spatter_maa_xrage_runtime_verify_4K"),
    ):
        if guest_hashes.get(name) != artifacts[key]["sha256"]:
            raise ValueError(f"{key} does not match its guest build record")

    records: list[dict[str, object]] = []
    backed_digests: dict[
        tuple[str, int], dict[int, list[tuple[int, int, int]]]
    ] = {}
    backed_commands: dict[tuple[str, int], tuple[list[str], int]] = {}
    backed_capacities: dict[tuple[str, int], dict[str, int]] = {}
    arm_lookup = {str(arm["name"]): arm for arm in arms}
    for arm_name in EXPECTED_ARMS:
        arm = arm_lookup[arm_name]
        is_backed = arm_name.startswith("backed")
        for replica in range(1, replicas + 1):
            run = root / "arms" / arm_name / f"replica-{replica}"
            if read_exit(run / "restore.exit") != 0:
                raise ValueError(f"{arm_name}/{replica}: nonzero restore exit")
            log = (run / "restore.log").read_text(
                encoding="utf-8", errors="replace"
            )
            if FATAL.search(log) or len(EXIT.findall(log)) != 1:
                raise ValueError(
                    f"{arm_name}/{replica}: nonterminal or fatal log"
                )
            if f"MAA XRAGE arm {arm['guest_arm']}" not in log:
                raise ValueError(
                    f"{arm_name}/{replica}: wrong restored guest arm"
                )
            correctness_key, marker = general.correctness("xrage", log)
            stats = general.first_stats(run / "gem5/stats.txt")
            maa = section(run / "gem5/config.ini")
            resolved = {
                "num_tile_elements": str(arm["logical"]),
                "physical_tile_elements": str(arm["physical"]),
                "num_maas": "1",
                "num_indirect_units_per_maa": "1",
                "num_initial_row_table_slices": "32",
                "num_row_table_rows_per_slice": "64",
                "virtual_index_buffer_lines": "128",
                "virtual_response_word_pool": "1024",
                "virtual_combine_words": "4096",
                "transparent_spd_mode": "0",
                "direct_retirement_line_handoff": "true",
            }
            for key, expected in resolved.items():
                if maa.get(key) != expected:
                    raise ValueError(
                        f"{arm_name}/{replica}: {key}={maa.get(key)!r}, expected {expected!r}"
                    )
            opcodes = tuple(
                exact_int(stats, suffix)
                for suffix in (
                    "numInst_INDRD",
                    "numInst_STRRD",
                    "numInst_ALUS",
                    "numInst_STRWR",
                )
            )
            if opcodes != EXPECTED_OPCODES[arm_name]:
                raise ValueError(
                    f"{arm_name}/{replica}: wrong non-fused opcode signature {opcodes}"
                )
            direct_stats = {
                suffix: exact_int(stats, suffix)
                for suffix in (
                    "direct_retirement_descriptors",
                    "direct_retirement_read_issues",
                    "direct_retirement_alu_issues",
                    "direct_retirement_write_issues",
                    "direct_retirement_fallbacks",
                )
            }
            if any(direct_stats.values()):
                raise ValueError(
                    f"{arm_name}/{replica}: activated fused/direct-sink mechanism"
                )
            trace = run / "gem5/mechanism.log"
            issue = issue_trace_summary(trace)
            capacity_trace: dict[str, int] | None = None
            if is_backed:
                mechanism = general.validate_materializer(
                    f"{arm_name}/{replica}",
                    "xrage",
                    "nonfused_backed_direct_index",
                    "token_stream_ld",
                    stats,
                    trace,
                )
                expected_materializer = {
                    "materializer_submits": 16,
                    "materializer_pages_ready": 16,
                    "materializer_retires": 4,
                    "materializer_summaries": 4,
                    "materializer_fallback_events": 0,
                }
                for key, expected in expected_materializer.items():
                    if mechanism[key] != expected:
                        raise ValueError(
                            f"{arm_name}/{replica}: {key}={mechanism[key]}, expected {expected}"
                        )
                if issue != {
                    "instructions": 4,
                    "requests": 8638,
                    "direct_index_requests": 8638,
                    "non_direct_index_requests": 0,
                }:
                    raise ValueError(
                        f"{arm_name}/{replica}: wrong direct-index request signature {issue}"
                    )
                backed_digests[(arm_name, replica)] = digest_records(trace)
                backed_commands[
                    (arm_name, replica)
                ] = normalized_backed_command(run / "restore.command.json")
                capacity_trace = materializer_capacity(trace)
                backed_capacities[(arm_name, replica)] = capacity_trace
            else:
                mechanism, _contexts = general.materializer_trace(trace)
                if any(
                    mechanism[key]
                    for key in (
                        "materializer_submits",
                        "materializer_pages_ready",
                        "materializer_retires",
                        "materializer_fallback_events",
                    )
                ):
                    raise ValueError(
                        f"{arm_name}/{replica}: native control activated materializer"
                    )
            capacity = hardware_capacity(maa, is_backed)
            if (
                capacity_trace is not None
                and capacity_trace["line_buffer_bytes"]
                != capacity["materializer_line_buffer_bytes"]
            ):
                raise ValueError(
                    f"{arm_name}/{replica}: materializer payload accounting mismatch"
                )
            records.append(
                {
                    "arm": arm_name,
                    "replica": replica,
                    "correctness_key": correctness_key,
                    "correctness_marker": marker,
                    "roi_first_window_simTicks": int(stats["simTicks"]),
                    "numInst_INDRD": opcodes[0],
                    "numInst_STRRD": opcodes[1],
                    "numInst_ALUS": opcodes[2],
                    "numInst_STRWR": opcodes[3],
                    **issue,
                    **capacity,
                    "materializer_submits": mechanism["materializer_submits"],
                    "materializer_pages_ready": mechanism[
                        "materializer_pages_ready"
                    ],
                    "materializer_retires": mechanism["materializer_retires"],
                    "materializer_fallback_events": mechanism[
                        "materializer_fallback_events"
                    ],
                    **direct_stats,
                    "materializer_control_bytes_cpp_static": (
                        capacity_trace["control_bytes"]
                        if capacity_trace
                        else 0
                    ),
                    "materializer_direct_stage_control_bytes": (
                        capacity_trace["direct_stage_control_bytes"]
                        if capacity_trace
                        else 0
                    ),
                }
            )

    correctness = {str(record["correctness_key"]) for record in records}
    if len(correctness) != 1:
        raise ValueError(
            f"cross-arm exact output mismatch: {sorted(correctness)}"
        )
    reference_digest = backed_digests[("backed16", 1)]
    for key, value in backed_digests.items():
        if value != reference_digest:
            raise ValueError(
                f"{key}: backed source-request order digest mismatch"
            )
    reference_command = backed_commands[("backed16", 1)][0]
    for key, (command, physical) in backed_commands.items():
        if command != reference_command:
            raise ValueError(
                f"{key}: backed restore differs beyond output/physical capacity"
            )
        expected = 16384 if key[0] == "backed16" else 4096
        if physical != expected:
            raise ValueError(f"{key}: wrong physical treatment value")
    static_capacity = {
        (
            value["line_buffer_bytes"],
            value["control_bytes"],
            value["direct_stage_control_bytes"],
        )
        for value in backed_capacities.values()
    }
    if len(static_capacity) != 1:
        raise ValueError("backed pair changed materializer static capacity")

    by_arm: dict[str, list[int]] = {
        arm: [
            int(record["roi_first_window_simTicks"])
            for record in records
            if record["arm"] == arm
        ]
        for arm in EXPECTED_ARMS
    }
    medians = {
        arm: sorted(values)[len(values) // 2]
        if len(values) % 2
        else sum(sorted(values)[len(values) // 2 - 1 : len(values) // 2 + 1])
        / 2
        for arm, values in by_arm.items()
    }
    cap16 = next(record for record in records if record["arm"] == "backed16")
    cap4 = next(record for record in records if record["arm"] == "backed4")
    capacity_delta = int(cap16["active_payload_capacity_bytes"]) - int(
        cap4["active_payload_capacity_bytes"]
    )
    expected_delta = 32 * (16384 - 4096) * 4
    if capacity_delta != expected_delta:
        raise ValueError(
            "backed payload-capacity delta does not equal exact SPD delta"
        )
    analysis_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    analysis_status = subprocess.run(
        ["git", "status", "--short"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if analysis_status:
        raise ValueError("analysis requires a clean source worktree")
    return {
        "schema": "dx100.xrage_backed_attribution_analysis.v1",
        "status": "PASS",
        "exact_correctness_key": next(iter(correctness)),
        "metric": "first ROI simTicks only",
        "analysis_provenance": {
            "source_commit": analysis_commit,
            "analyzer_sha256": sha256_file(Path(__file__)),
            "source_status": "clean",
        },
        "records": records,
        "arm_roi_first_window_simTicks": by_arm,
        "arm_median_roi_first_window_simTicks": medians,
        "backed_request_order": {
            "strict_per_unit_per_instruction_digest_match": True,
            "instructions": 4,
            "source_requests": 8638,
            "scope_caveat": "commits to ordered requests within each instruction, not global completion order",
        },
        "backed_treatment_delta": {
            "only_resolved_knob": "physical_tile_elements",
            "backed16": 16384,
            "backed4": 4096,
            "physical_spd_payload_delta_bytes": expected_delta,
            "active_payload_capacity_delta_bytes": capacity_delta,
        },
        "hardware_report_boundary": HARDWARE_REPORT_BOUNDARY,
        "fused_direct_sink_separate": True,
    }


def write_outputs(root: Path, report: dict[str, object]) -> None:
    analysis = root / "analysis"
    analysis.mkdir(exist_ok=True)
    (analysis / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    records = report["records"]
    assert isinstance(records, list)
    columns = [
        "arm",
        "replica",
        "correctness_key",
        "roi_first_window_simTicks",
        "numInst_INDRD",
        "numInst_STRRD",
        "numInst_ALUS",
        "numInst_STRWR",
        "instructions",
        "requests",
        "direct_index_requests",
        "physical_spd_payload_bytes",
        "direct_index_feeder_bytes",
        "source_response_pool_bytes",
        "destination_combiner_bytes",
        "materializer_line_buffer_bytes",
        "active_payload_capacity_bytes",
        "materializer_submits",
        "materializer_pages_ready",
        "materializer_retires",
        "materializer_fallback_events",
        "direct_retirement_descriptors",
        "direct_retirement_read_issues",
        "direct_retirement_alu_issues",
        "direct_retirement_write_issues",
        "direct_retirement_fallbacks",
        "materializer_control_bytes_cpp_static",
        "materializer_direct_stage_control_bytes",
    ]
    with (analysis / "report.tsv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(
            stream, fieldnames=columns, delimiter="\t", extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(records)
    lines = [
        "# XRAGE backed-capacity attribution",
        "",
        f"Status: **{report['status']}**. Metric: {report['metric']}.",
        "",
        "| arm | replica | first-ROI simTicks | INDRD/STRRD/ALUS/STRWR | materializer S/P/R/F | direct-sink descriptors | SPD payload | active payload-capacity subtotal |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for record in records:
        lines.append(
            f"| {record['arm']} | {record['replica']} | {record['roi_first_window_simTicks']} | "
            f"{record['numInst_INDRD']}/{record['numInst_STRRD']}/{record['numInst_ALUS']}/{record['numInst_STRWR']} | "
            f"{record['materializer_submits']}/{record['materializer_pages_ready']}/"
            f"{record['materializer_retires']}/{record['materializer_fallback_events']} | "
            f"{record['direct_retirement_descriptors']} | "
            f"{record['physical_spd_payload_bytes']} B | {record['active_payload_capacity_bytes']} B |"
        )
    delta = report["backed_treatment_delta"]
    lines += [
        "",
        "The backed restores have one resolved treatment delta: "
        "`physical_tile_elements=16384` versus `4096`. Their exact global SPD "
        f"payload and payload-capacity-subtotal difference is {delta['physical_spd_payload_delta_bytes']} bytes. "
        "Both execute four direct-index instructions, sixteen controller-managed "
        "token-bound page materializations, sixteen ordinary scalar ALUs, and "
        "sixteen ordinary stream stores. The materializations do not increment "
        "ordinary `numInst_STRRD`; their dedicated submit/page/retire counters "
        "prove execution. All materializer and direct-retirement fallbacks are zero.",
        "",
        "`active_payload_capacity_bytes` is a payload-capacity subtotal, not "
        "total hardware or area. Backed16 and backed4 retain identical logical16 "
        "Row/Offset metadata. The subtotal excludes descriptor/header/readiness "
        "bits; nonpayload tags/control beyond the separately emitted materializer "
        "controls; ports, arbitration, wiring, and SRAM periphery; and synthesized "
        "area, power, or timing. The defensible matched claim is only the exact "
        f"{delta['physical_spd_payload_delta_bytes']}-byte physical SPD payload "
        "reduction with a fixed instruction path and fixed logical metadata; it "
        "is not total DX100 hardware cost.",
        "",
        "The prior fused/direct-sink optimization is excluded: transparent SPD "
        "mode is zero and direct-retirement descriptor/read/ALU/write counters are zero.",
    ]
    (analysis / "report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    (analysis / "report.pass").touch()


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} MATRIX_ROOT", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).resolve()
    try:
        report = analyze(root)
        write_outputs(root, report)
    except (KeyError, OSError, TypeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"PASS: {len(report['records'])} exact matched XRAGE runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
