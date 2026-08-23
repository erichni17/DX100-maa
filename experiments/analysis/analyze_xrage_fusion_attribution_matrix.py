#!/usr/bin/env python3
"""Fail-closed analysis for XRAGE fused direct-sink attribution."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKED_ANALYZER_PATH = (
    ROOT / "experiments/analysis/analyze_xrage_backed_attribution_matrix.py"
)
GENERAL_PATH = (
    ROOT / "experiments/analysis/analyze_general_hybrid_benchmark_matrix.py"
)


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backed = load("xrage_backed_analyzer_for_fusion", BACKED_ANALYZER_PATH)
general = load("general_hybrid_analyzer_for_xrage_fusion", GENERAL_PATH)

EXPECTED_MANIFEST_SHA256 = (
    "a5c9efdbf955fcd24e58b72bdaefb9a93210f9cb27eba1a6365281011be3754d"
)
EXPECTED_REPORT_SHA256 = (
    "346ec9d1d92973eac170296c134d629a5326ef191624c7adb35dfcae8e3e8d50"
)
EXPECTED_GUEST_COMMIT = "95a6836e8070cf0daeae579375f2c9e2df4ed73b"
EXPECTED_SIMULATOR_COMMIT = "be77a62ca992507d9145fe0d44c9ed491c8310a2"
EXPECTED_INPUT_SHA256 = backed.EXPECTED_INPUT_SHA256
REUSED_ARMS = ("native16", "native4", "backed4")
ACCEPTED_ROW_IDENTITIES = {
    (
        "native16",
        1,
    ): "7b7190f4d669e3c5b3c055b52cfeb0d55af0006ef92ec781d2ab85d36a4a766c",
    (
        "native16",
        2,
    ): "e20070aeaa31e32b24e100584f00e211c4b9cd3c49121fa92430a7877060e91f",
    (
        "native4",
        1,
    ): "0ba534330fa8621699017cb07fe70476092b6895b5ef14dcf5f9d272beb156f1",
    (
        "native4",
        2,
    ): "dd146ba667fd28a117c39f4405501046e79ea8b0ec56f18d9a05a62c1cdac2bc",
    (
        "backed4",
        1,
    ): "58619062b3b5c50efba38070aa4515f15ce852c00ef6ce2f36c584055ae6263b",
    (
        "backed4",
        2,
    ): "092b8a31740d0beb272f9af5395d36dfc643e2eeebf95f173baf462d2c7ce0a0",
}
ROW_IDENTITY_FILES = (
    "restore.exit",
    "restore.log",
    "restore.command.json",
    "gem5/config.ini",
    "gem5/stats.txt",
    "gem5/mechanism.log",
)
DIRECT_OPCODES = (4, 0, 0, 0)
DIRECT_CLOSURE = {
    "direct_retirement_descriptors": 4,
    "direct_retirement_producer_acks": 16,
    "direct_retirement_producer_line_acks": 8192,
    "direct_retirement_early_line_overflows": 0,
    "direct_retirement_page_fallback_lines": 0,
    "direct_retirement_read_issues": 8192,
    "direct_retirement_read_responses": 8192,
    "direct_retirement_alu_issues": 8192,
    "direct_retirement_alu_completions": 8192,
    "direct_retirement_write_issues": 8192,
    "direct_retirement_write_responses": 8192,
    "direct_retirement_context_high_water": 4,
    "direct_retirement_context_full_stalls": 0,
    "direct_retirement_fallbacks": 0,
    "direct_retirement_payload_bytes": 4096,
    "direct_retirement_control_bytes": 26912,
}
MATERIALIZER_ACTIVITY_STATS = (
    "page_materialization_submissions",
    "page_materialization_pages",
    "page_materialization_retirements",
    "page_materialization_forwarded_lines",
    "page_materialization_cache_read_fallback_lines",
    "page_materialization_dispatch_fallbacks",
    "page_materialization_admission_fallbacks",
    "page_materialization_producer_line_acks",
    "page_materialization_page_fallback_lines",
    "page_materialization_fragment_accumulated_lines",
    "page_materialization_fragment_buffer_stalls",
    "page_materialization_staged_direct_lines",
    "page_materialization_staged_direct_fragments",
    "page_materialization_staged_direct_fallback_lines",
)
SUMMARY_EVENT = "direct_retirement_summary"
HARDWARE_REPORT_BOUNDARY = {
    "direct_handoff_incremental_payload_bytes": 4096,
    "direct_handoff_incremental_control_bytes": 26912,
    "semantics": (
        "C++ modeled persistent payload/control charge plus separately listed "
        "payload-capacity subtotal; not synthesized area, PPA, or total DX100 cost"
    ),
    "excluded": [
        "ports, arbitration, and wiring",
        "SRAM periphery and physical implementation overhead",
        "synthesized area, power, and timing",
    ],
    "control_view_caveat": (
        "direct and backed control views overlap configurable structures and "
        "must not be subtracted as an area delta"
    ),
}


def read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def exact_artifact(record: object, expected: str, label: str) -> Path:
    if not isinstance(record, dict) or record.get("sha256") != expected:
        raise ValueError(f"{label}: manifest identity mismatch")
    path = Path(str(record.get("path", "")))
    if not path.is_file() or backed.sha256_file(path) != expected:
        raise ValueError(f"{label}: immutable artifact changed")
    return path


def row_identity(root: Path) -> str:
    digest = hashlib.sha256()
    for relative in ROW_IDENTITY_FILES:
        path = root / relative
        if not path.is_file():
            raise ValueError(f"accepted row lacks {relative}")
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(backed.sha256_file(path).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def ordered_issue_requests(
    path: Path,
) -> list[list[tuple[int, int, int, int]]]:
    groups: dict[tuple[int, int], list[tuple[int, int, int, int]]] = {}
    order: list[tuple[int, int]] = []
    for match in backed.ISSUE.finditer(
        path.read_text(encoding="utf-8", errors="replace")
    ):
        (
            unit,
            tick,
            sequence,
            address,
            bounded_flag,
            virtual,
            direct_index,
        ) = match.groups()
        key = (int(unit), int(tick))
        if key not in groups:
            groups[key] = []
            order.append(key)
        values = groups[key]
        if int(sequence) != len(values):
            raise ValueError(
                f"{path}: non-contiguous request sequence for {key}"
            )
        values.append(
            (
                int(address, 16),
                int(bounded_flag),
                int(virtual),
                int(direct_index),
            )
        )
    if not order:
        raise ValueError(f"{path}: no issue-order records")
    return [groups[key] for key in order]


def normalized_direct_command(path: Path) -> list[str]:
    command = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(command, list) or not all(
        isinstance(x, str) for x in command
    ):
        raise ValueError(f"{path}: invalid command JSON")
    normalized = []
    for argument in command:
        if argument.startswith("--outdir="):
            normalized.append("--outdir=RUN")
        else:
            normalized.append(argument)
    return normalized


def direct_summaries(path: Path) -> list[dict[str, str]]:
    summaries = []
    for line in path.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        fields = general.fields(line)
        if fields.get("event") == SUMMARY_EVENT:
            summaries.append(fields)
    if len(summaries) != 4:
        raise ValueError(f"{path}: expected four direct-retirement summaries")
    owners: set[tuple[int, int, int]] = set()
    for value in summaries:
        try:
            owner = tuple(
                int(value[name], 0)
                for name in ("token", "generation", "incarnation")
            )
            closure = {
                name: int(value[name], 0)
                for name in (
                    "reads",
                    "computes",
                    "writes",
                    "line_acks",
                    "page_fallback_lines",
                    "fallback_count",
                )
            }
        except (KeyError, ValueError) as error:
            raise ValueError(f"{path}: malformed direct summary") from error
        if owner in owners:
            raise ValueError(f"{path}: duplicate direct descriptor owner")
        owners.add(owner)
        if closure != {
            "reads": 2048,
            "computes": 2048,
            "writes": 2048,
            "line_acks": 2048,
            "page_fallback_lines": 0,
            "fallback_count": 0,
        }:
            raise ValueError(f"{path}: direct summary failed exact closure")
    return summaries


def direct_capacity(maa) -> dict[str, int]:
    base = backed.hardware_capacity(maa, True)
    base.pop("materializer_line_buffer_bytes")
    base["direct_handoff_payload_bytes"] = 4096
    base["active_payload_capacity_bytes"] = (
        base["physical_spd_payload_bytes"]
        + base["direct_index_feeder_bytes"]
        + base["source_response_pool_bytes"]
        + base["destination_combiner_bytes"]
        + base["direct_handoff_payload_bytes"]
    )
    return base


def validate_accepted(
    root: Path, manifest: dict[str, object]
) -> tuple[dict[str, object], Path]:
    accepted = Path(str(manifest.get("accepted_root", "")))
    accepted_manifest_path = accepted / "manifest.json"
    accepted_report_path = accepted / "analysis/report.json"
    if backed.sha256_file(accepted_manifest_path) != EXPECTED_MANIFEST_SHA256:
        raise ValueError("accepted manifest hash mismatch")
    if backed.sha256_file(accepted_report_path) != EXPECTED_REPORT_SHA256:
        raise ValueError("accepted report hash mismatch")
    accepted_manifest = read_json(accepted_manifest_path)
    accepted_report = read_json(accepted_report_path)
    if accepted_report.get("status") != "PASS":
        raise ValueError("accepted report is not PASS")
    provenance = accepted_manifest.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("accepted provenance missing")
    if provenance.get("guest_source_commit") != EXPECTED_GUEST_COMMIT:
        raise ValueError("accepted guest source commit mismatch")
    if provenance.get("simulator_source_commit") != EXPECTED_SIMULATOR_COMMIT:
        raise ValueError("accepted simulator source commit mismatch")
    artifacts = accepted_manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("accepted artifacts missing")
    exact_artifact(
        artifacts.get("workload_input"), EXPECTED_INPUT_SHA256, "input"
    )
    exact_artifact(
        artifacts.get("gem5"),
        "44b6e86ebc86fd692ce02dcb2e1f627082ded10c741134ae49de5721e2edcb45",
        "gem5",
    )
    report_records = accepted_report.get("records")
    if not isinstance(report_records, list):
        raise ValueError("accepted report records missing")
    controls = [
        record for record in report_records if record.get("arm") in REUSED_ARMS
    ]
    if len(controls) != 6:
        raise ValueError(
            "accepted report lacks two exact replicas per reused arm"
        )
    if {record.get("arm") for record in controls} != set(REUSED_ARMS):
        raise ValueError("accepted report arm identity mismatch")
    if {record.get("correctness_key") for record in controls} != {
        "65536:5576400619275092867"
    }:
        raise ValueError("accepted controls do not share exact output")
    for key, expected in ACCEPTED_ROW_IDENTITIES.items():
        row = accepted / "arms" / key[0] / f"replica-{key[1]}"
        if row_identity(row) != expected:
            raise ValueError(f"accepted {key[0]}/{key[1]} row changed")
    return accepted_report, accepted


def analyze(root: Path) -> dict[str, object]:
    manifest = read_json(root / "manifest.json")
    if manifest.get("schema") != "dx100.xrage_fusion_attribution_matrix.v1":
        raise ValueError("unsupported or missing fusion manifest")
    if manifest.get("timeout") is not None:
        raise ValueError("direct attribution must not have a timeout")
    if manifest.get("accepted_reused_arms") != list(REUSED_ARMS):
        raise ValueError("wrong accepted control arm set")
    replicas = int(manifest.get("replicas", 0))
    if replicas < 2 or int(manifest.get("max_parallel_restores", 0)) < 2:
        raise ValueError("direct arm is not repeated and concurrent")
    provenance = manifest.get("provenance")
    if not isinstance(provenance, dict) or provenance != {
        "guest_source_commit": EXPECTED_GUEST_COMMIT,
        "guest_source_tree_matches_lead": True,
        "simulator_source_commit": EXPECTED_SIMULATOR_COMMIT,
        "simulator_source_tree_matches_lead": True,
    }:
        raise ValueError("fusion provenance does not match accepted sources")
    accepted_report, accepted_root = validate_accepted(root, manifest)

    checkpoint_command_path = root / "checkpoint/checkpoint.command.json"
    checkpoint_command = json.loads(
        checkpoint_command_path.read_text(encoding="utf-8")
    )
    if not isinstance(checkpoint_command, list):
        raise ValueError("direct checkpoint command is invalid")
    options_positions = [
        i for i, value in enumerate(checkpoint_command) if value == "--options"
    ]
    if len(options_positions) != 1:
        raise ValueError("direct checkpoint lacks one immutable argv")
    checkpoint_argv = checkpoint_command[options_positions[0] + 1]
    if checkpoint_argv.count("--maa-arm direct4x3") != 1:
        raise ValueError("direct checkpoint argv does not bind direct4x3")
    if "backedx3" in checkpoint_argv:
        raise ValueError("direct checkpoint argv contains backed selector")
    if backed.sha256_file(checkpoint_command_path) != manifest.get(
        "checkpoint_command_sha256"
    ):
        raise ValueError("direct checkpoint command identity mismatch")
    if backed.read_exit(root / "checkpoint/checkpoint.exit") != 0:
        raise ValueError("direct checkpoint failed")
    checkpoint_identity = general.tree_identity(root / "checkpoint/gem5")
    if checkpoint_identity != manifest.get("checkpoint_identity"):
        raise ValueError("direct checkpoint changed after execution")

    accepted_backed_trace = (
        accepted_root / "arms/backed4/replica-1/gem5/mechanism.log"
    )
    accepted_requests = ordered_issue_requests(accepted_backed_trace)
    accepted_digests = backed.digest_records(accepted_backed_trace)
    if sum(len(group) for group in accepted_requests) != 8638:
        raise ValueError("accepted backed4 request evidence changed")

    direct_records: list[dict[str, object]] = []
    direct_commands: list[list[str]] = []
    direct_request_orders: list[list[list[tuple[int, int, int, int]]]] = []
    direct_digests: list[dict[int, list[tuple[int, int, int]]]] = []
    restore_runs = manifest.get("restore_runs")
    if not isinstance(restore_runs, list) or len(restore_runs) != replicas:
        raise ValueError("restore command provenance is incomplete")
    for replica in range(1, replicas + 1):
        run = root / "direct4x3" / f"replica-{replica}"
        if backed.read_exit(run / "restore.exit") != 0:
            raise ValueError(f"direct4x3/{replica}: nonzero restore exit")
        command_path = run / "restore.command.json"
        record = restore_runs[replica - 1]
        if not isinstance(record, dict) or record.get("replica") != replica:
            raise ValueError(
                f"direct4x3/{replica}: restore provenance order mismatch"
            )
        if backed.sha256_file(command_path) != record.get("command_sha256"):
            raise ValueError(f"direct4x3/{replica}: command identity mismatch")
        command = normalized_direct_command(command_path)
        direct_commands.append(command)
        if command.count("--maa_transparent_spd_mode=3") != 1:
            raise ValueError(
                f"direct4x3/{replica}: direct sink mode not exact"
            )
        if "--maa_transparent_spd_mode=0" in command:
            raise ValueError(
                f"direct4x3/{replica}: non-fused mode remained active"
            )
        log = (run / "restore.log").read_text(
            encoding="utf-8", errors="replace"
        )
        if backed.FATAL.search(log) or len(backed.EXIT.findall(log)) != 1:
            raise ValueError(f"direct4x3/{replica}: nonterminal or fatal log")
        if "MAA XRAGE arm direct4x3" not in log:
            raise ValueError(
                f"direct4x3/{replica}: restored argv is not direct4x3"
            )
        correctness_key, marker = general.correctness("xrage", log)
        stats = general.first_stats(run / "gem5/stats.txt")
        maa = backed.section(run / "gem5/config.ini")
        expected_config = {
            "num_tile_elements": "16384",
            "physical_tile_elements": "4096",
            "transparent_spd_mode": "3",
            "direct_retirement_line_handoff": "true",
            "virtual_index_buffer_lines": "128",
            "virtual_response_word_pool": "1024",
            "virtual_combine_words": "4096",
        }
        for key, expected in expected_config.items():
            if maa.get(key) != expected:
                raise ValueError(
                    f"direct4x3/{replica}: {key}={maa.get(key)!r}, expected {expected!r}"
                )
        opcodes = tuple(
            backed.exact_int(stats, suffix)
            for suffix in (
                "numInst_INDRD",
                "numInst_STRRD",
                "numInst_ALUS",
                "numInst_STRWR",
            )
        )
        if opcodes != DIRECT_OPCODES:
            raise ValueError(
                f"direct4x3/{replica}: wrong fused opcode signature {opcodes}"
            )
        closure = {
            suffix: backed.exact_int(stats, suffix)
            for suffix in DIRECT_CLOSURE
        }
        if closure != DIRECT_CLOSURE:
            raise ValueError(
                f"direct4x3/{replica}: direct retirement did not close exactly"
            )
        trace = run / "gem5/mechanism.log"
        summaries = direct_summaries(trace)
        materializer, contexts = general.materializer_trace(trace)
        if contexts or any(materializer.values()):
            raise ValueError(
                f"direct4x3/{replica}: backed materializer trace is active"
            )
        materializer_stats = {
            suffix: backed.exact_int(stats, suffix)
            for suffix in MATERIALIZER_ACTIVITY_STATS
        }
        if any(materializer_stats.values()):
            raise ValueError(
                f"direct4x3/{replica}: backed materializer stat is active"
            )
        request_order = ordered_issue_requests(trace)
        digests = backed.digest_records(trace)
        if request_order != accepted_requests or digests != accepted_digests:
            raise ValueError(
                f"direct4x3/{replica}: source request order differs from accepted backed4"
            )
        direct_request_orders.append(request_order)
        direct_digests.append(digests)
        capacity = direct_capacity(maa)
        if (
            capacity["direct_handoff_payload_bytes"]
            != closure["direct_retirement_payload_bytes"]
        ):
            raise ValueError(
                f"direct4x3/{replica}: direct payload accounting mismatch"
            )
        direct_records.append(
            {
                "arm": "direct4x3",
                "replica": replica,
                "correctness_key": correctness_key,
                "correctness_marker": marker,
                "roi_first_window_simTicks": int(stats["simTicks"]),
                "numInst_INDRD": opcodes[0],
                "numInst_STRRD": opcodes[1],
                "numInst_ALUS": opcodes[2],
                "numInst_STRWR": opcodes[3],
                "source_request_instructions": len(request_order),
                "source_requests": sum(len(group) for group in request_order),
                "direct_summary_count": len(summaries),
                **closure,
                **capacity,
                "backed_materializer_trace_events": 0,
                "backed_materializer_activity_stats": 0,
            }
        )
    if any(command != direct_commands[0] for command in direct_commands[1:]):
        raise ValueError(
            "direct replica restore commands differ beyond output path"
        )
    if any(
        order != direct_request_orders[0]
        for order in direct_request_orders[1:]
    ):
        raise ValueError("direct replica request orders differ")
    if any(digest != direct_digests[0] for digest in direct_digests[1:]):
        raise ValueError("direct replica request digests differ")

    accepted_records = accepted_report["records"]
    controls = [
        record for record in accepted_records if record["arm"] in REUSED_ARMS
    ]
    correctness = {
        record["correctness_key"] for record in controls + direct_records
    }
    if correctness != {"65536:5576400619275092867"}:
        raise ValueError(
            f"cross-arm exact output mismatch: {sorted(correctness)}"
        )
    medians: dict[str, float] = {}
    for arm in (*REUSED_ARMS, "direct4x3"):
        values = sorted(
            int(record["roi_first_window_simTicks"])
            for record in controls + direct_records
            if record["arm"] == arm
        )
        medians[arm] = (
            float(values[len(values) // 2])
            if len(values) % 2
            else sum(values[len(values) // 2 - 1 : len(values) // 2 + 1]) / 2
        )
    direct_ticks = medians["direct4x3"]
    comparisons = {
        "direct4x3_vs_backed4_fixed_physical4_speedup": medians["backed4"]
        / direct_ticks,
        "direct4x3_vs_native16_speedup": medians["native16"] / direct_ticks,
        "direct4x3_vs_native4_speedup": medians["native4"] / direct_ticks,
        "decisive_attribution": "fusion/direct-sink vs non-fused backed path at fixed physical4",
        "virtualization_claim_permitted": False,
    }
    analysis_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if subprocess.run(
        ["git", "status", "--short"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout:
        raise ValueError("analysis requires a clean source worktree")
    return {
        "schema": "dx100.xrage_fusion_attribution_analysis.v1",
        "status": "PASS",
        "metric": "first ROI simTicks only",
        "exact_correctness_key": next(iter(correctness)),
        "analysis_provenance": {
            "source_commit": analysis_commit,
            "analyzer_sha256": backed.sha256_file(Path(__file__)),
            "source_status": "clean",
        },
        "accepted_control_provenance": {
            "root": str(accepted_root),
            "manifest_sha256": EXPECTED_MANIFEST_SHA256,
            "report_sha256": EXPECTED_REPORT_SHA256,
            "guest_source_commit": EXPECTED_GUEST_COMMIT,
            "simulator_source_commit": EXPECTED_SIMULATOR_COMMIT,
            "row_identities": {
                f"{arm}/replica-{replica}": value
                for (arm, replica), value in ACCEPTED_ROW_IDENTITIES.items()
            },
        },
        "records": controls + direct_records,
        "arm_median_roi_first_window_simTicks": medians,
        "comparisons": comparisons,
        "request_order": {
            "exact_address_order_match_direct4x3_to_backed4": True,
            "exact_digest_match_direct4x3_to_backed4": True,
            "instructions": len(accepted_requests),
            "source_requests": sum(len(group) for group in accepted_requests),
            "scope_caveat": "exact source request order per instruction; not global completion interleaving",
        },
        "direct_retirement_closure": DIRECT_CLOSURE,
        "backed_materializer_active_in_direct_arm": False,
        "hardware_report_boundary": HARDWARE_REPORT_BOUNDARY,
    }


def write_outputs(root: Path, report: dict[str, object]) -> None:
    analysis = root / "analysis"
    analysis.mkdir(exist_ok=True)
    (analysis / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    records = report["records"]
    assert isinstance(records, list)
    columns = (
        "arm",
        "replica",
        "roi_first_window_simTicks",
        "correctness_key",
        "numInst_INDRD",
        "numInst_STRRD",
        "numInst_ALUS",
        "numInst_STRWR",
        "direct_retirement_descriptors",
        "direct_retirement_read_issues",
        "direct_retirement_read_responses",
        "direct_retirement_alu_issues",
        "direct_retirement_alu_completions",
        "direct_retirement_write_issues",
        "direct_retirement_write_responses",
        "direct_retirement_fallbacks",
        "direct_retirement_payload_bytes",
        "direct_retirement_control_bytes",
        "active_payload_capacity_bytes",
    )
    with (analysis / "report.tsv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(
            stream, fieldnames=columns, delimiter="\t", extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(records)
    medians = report["arm_median_roi_first_window_simTicks"]
    comparisons = report["comparisons"]
    closure = report["direct_retirement_closure"]
    direct = next(record for record in records if record["arm"] == "direct4x3")
    lines = [
        "# XRAGE fused direct-sink attribution",
        "",
        f"Status: **{report['status']}**. Metric: {report['metric']}.",
        "",
        "| Arm | Median first-ROI simTicks | Role |",
        "|---|---:|---|",
        f"| native16 | {medians['native16']:.0f} | ordinary logical16/physical16 control |",
        f"| native4 | {medians['native4']:.0f} | ordinary logical4/physical4 control |",
        f"| backed4 | {medians['backed4']:.0f} | non-fused backed logical16/physical4 |",
        f"| direct4x3 | {medians['direct4x3']:.0f} | fused direct-sink logical16/physical4 |",
        "",
        "The decisive fixed-physical4 attribution is direct4x3 versus backed4: "
        f"{comparisons['direct4x3_vs_backed4_fixed_physical4_speedup']:.6f}x. "
        "This is fusion/direct-sink attribution, not a virtualization gain.",
        "",
        f"All rows have exact output key `{report['exact_correctness_key']}`. "
        "Each direct replica exactly matches accepted backed4's 8,638 source "
        "requests in address order within all four instructions and matches "
        "the accepted count/FNV/mix digests. Global completion interleaving is "
        "outside that request-order claim.",
        "",
        "The direct path closes exactly at "
        f"{closure['direct_retirement_descriptors']} descriptors and "
        f"{closure['direct_retirement_read_issues']}/"
        f"{closure['direct_retirement_alu_issues']}/"
        f"{closure['direct_retirement_write_issues']} read/ALU/write issues, "
        "with equal response/completion counts, 8,192 exact producer line ACKs, "
        "zero page-fallback lines, and zero descriptor fallback. It has no "
        "materializer lifecycle events or materializer activity stats.",
        "",
        "Hardware accounting: the direct arm has a 524,288-byte physical SPD "
        "payload, 8,192-byte direct-index feeder, 8,192-byte source-response "
        "pool, 32,768-byte destination combiner, and 4,096-byte direct-handoff "
        f"payload, for a {direct['active_payload_capacity_bytes']:,}-byte active "
        "payload-capacity subtotal. The separately emitted direct-handoff "
        f"control view is {direct['direct_retirement_control_bytes']:,} bytes. "
        "These are modeled C++ capacity/control charges, not synthesized area, "
        "power, timing, or total DX100 hardware cost; ports, arbitration, wiring, "
        "SRAM periphery, and physical overhead remain excluded. The direct and "
        "backed control views overlap configurable structures and are not an area delta.",
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
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"PASS: {len(report['records'])} exact XRAGE attribution rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
