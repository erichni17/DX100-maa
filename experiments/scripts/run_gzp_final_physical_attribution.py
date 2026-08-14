#!/usr/bin/env python3
"""Assemble the fail-closed final GZP attribution from accepted evidence."""

from __future__ import annotations

import argparse
import csv
import json
import re
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_general_hybrid_benchmark_matrix as provenance  # noqa: E402
import run_gzp_dual_logical16_one_window as candidate  # noqa: E402
import run_gzp_masked_index_pair as common  # noqa: E402

BASE_COMMIT = "0e8c6d9f6e4e59167255f5be95dab7fc81d7b3ca"
FULL_N = 1_000_000
LOGICAL_ELEMENTS = 16_384
FULL_WINDOWS = FULL_N // LOGICAL_ELEMENTS
REPLICAS = 2
EXPECTED_OUTPUT_HASH = "11225737641199706160"
EXPECTED_REFERENCE_ELEMENTS = "1180000"
SCHEDULE_ARMS = ("volume_only", "dual_logical16")
API_ARMS = (
    "soa_metadata16_physical4",
    "soa_metadata16_physical16",
)
CURRENT_PUBLISH_LINES_PER_WINDOW = 4 * 256
FUTURE_PHYSICAL16_REQUIRED_LINES_PER_WINDOW = 4096

SOA_SUFFIXES = (
    "IND_SoaJitInstructions",
    "IND_SoaJitSelected",
    "IND_SoaJitPredicateRejected",
    "IND_SoaJitAliasesApplied",
    "IND_SoaJitValueDeliveries",
    "IND_SoaJitLookaheadIssues",
    "IND_SoaJitLookaheadResponses",
    "IND_SoaJitTerminalCompletions",
    "IND_SoaJitPredicateLineReads",
    "IND_SoaJitPredicateLineResponses",
    "IND_SoaJitAReadIssues",
    "IND_SoaJitAReadResponses",
    "IND_SoaJitAWriteIssues",
    "IND_SoaJitAWriteResponses",
    "IND_SoaJitValueReadIssues",
    "IND_SoaJitValueReadResponses",
    "IND_SoaJitValueFills",
    "IND_SoaJitValueHits",
    "IND_SoaJitValueMergedWaiters",
    "IND_SoaJitValuePrefetchIssues",
    "IND_SoaJitValuePrefetchResponses",
    "IND_SoaJitPreAValueIssues",
    "IND_SoaJitPreAValueReadyAtAResponse",
    "IND_SoaJitPreAValueUses",
)
PUBLISH_SUFFIXES = (
    "STR_PublishIssues",
    "STR_PublishAccepts",
    "STR_PublishWriteResponses",
    "STR_PublishTerminals",
    "STR_PublishRetries",
    "STR_PublishCreditStalls",
    "STR_PublishOverlapIssues",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--candidate-gate", required=True, type=Path)
    parser.add_argument("--native16-evidence", required=True, type=Path)
    parser.add_argument("--api-physical-evidence", required=True, type=Path)
    parser.add_argument("--expected-candidate-manifest-sha256")
    parser.add_argument("--expected-native16-manifest-sha256")
    parser.add_argument("--expected-api-manifest-sha256")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.execute:
        for option in (
            "expected_candidate_manifest_sha256",
            "expected_native16_manifest_sha256",
            "expected_api_manifest_sha256",
        ):
            value = getattr(args, option)
            if not re.fullmatch(r"[0-9a-f]{64}", value or ""):
                parser.error(
                    "--execute requires all three expected manifest SHA-256s"
                )
    return args


def audit_publisher_boundary() -> dict[str, object]:
    publisher = (
        ROOT / "src/mem/MAA/ResponseBearingSpdPublisher.hh"
    ).read_text()
    stream = (ROOT / "src/mem/MAA/StreamAccess.cc").read_text()
    api = (ROOT / "benchmarks/API/MAA_gem5.hpp").read_text()
    gzp = (ROOT / "benchmarks/UME/gradzatp.cpp").read_text()
    required = {
        "publisher_fixed_4096": "PageElements = 4096" in publisher,
        "runtime_rejects_non4096": (
            "maa->physical_tile_elements !=\n"
            "                         ResponsePublisher::PageElements"
        )
        in stream,
        "source_size_must_be_4096": (
            "maa->spd->getSize(my_src_tile) !=\n"
            "                 static_cast<int>(ResponsePublisher::PageElements)"
        )
        in stream,
        "source_capture_starts_at_zero": (
            "static_cast<std::size_t>(ordinal) * my_words_per_cl" in stream
        ),
        "guest_backing_page_stride_4096": (
            "logical16_backing + logical_page * 4096" in api
        ),
        "gzp_guard_is_physical4_only": (
            "MAA_CONSUMER_TILE_SIZE != 4096" in gzp
        ),
    }
    if not all(required.values()):
        missing = ", ".join(
            name for name, present in required.items() if not present
        )
        raise RuntimeError("publisher boundary audit changed: " + missing)
    return {
        **required,
        "same_instruction_physical16_supported": False,
        "current_fp32_lines_per_4k_publication": 256,
        "current_fp32_lines_per_logical16_window": (
            CURRENT_PUBLISH_LINES_PER_WINDOW
        ),
        "required_future_physical16_lines_per_window": (
            FUTURE_PHYSICAL16_REQUIRED_LINES_PER_WINDOW
        ),
        "blocker": (
            "a fair physical16 control needs guest source-offset publication "
            "and publisher/core support beyond this runner-only scope"
        ),
    }


def plan(args: argparse.Namespace) -> dict[str, object]:
    return {
        "schema": "dx100.gzp_final_attribution.v1",
        "required_base_commit": BASE_COMMIT,
        "n": FULL_N,
        "evidence": {
            "schedule_gain": {
                "pair": list(SCHEDULE_ARMS),
                "replicas_per_arm": REPLICAS,
                "scope": "physical4 dual-logical16 schedule change",
            },
            "end_to_end_ceiling": {
                "arm": "ordinary_native16",
                "must_match_candidate_gem5_and_config": True,
            },
            "virtualization_isolation": {
                "pair": list(API_ARMS),
                "scope": "accepted matched API physical4/physical16 pair",
            },
        },
        "same_instruction_gzp_physical16": {
            "included": False,
            "required_publisher_lines_per_window": (
                FUTURE_PHYSICAL16_REQUIRED_LINES_PER_WINDOW
            ),
            "reason": "requires source-offset publisher/core changes",
        },
        "selector_isolation": "immutable_per_arm_ro_bind_required",
        "timeouts": False,
        "simulated_metric": "simTicks",
        "host_time_metric_authorized": False,
        "inputs": {
            "candidate_gate": str(args.candidate_gate),
            "native16_evidence": str(args.native16_evidence),
            "api_physical_evidence": str(args.api_physical_evidence),
        },
    }


def sum_suffix(stats: dict[str, int], suffix: str) -> int:
    return sum(value for name, value in stats.items() if name.endswith(suffix))


def max_suffix(stats: dict[str, int], suffix: str) -> int:
    return max(
        (value for name, value in stats.items() if name.endswith(suffix)),
        default=0,
    )


def exact_hash(path: Path, expected: str, label: str) -> str:
    if not path.is_file():
        raise RuntimeError(f"missing {label}: {path}")
    actual = common.sha256(path)
    if actual != expected:
        raise RuntimeError(
            f"{label} SHA-256 mismatch: expected {expected}, got {actual}"
        )
    return actual


def verify_file_identity(identity: object, label: str) -> None:
    if not isinstance(identity, dict):
        raise RuntimeError(f"missing {label} identity")
    path = Path(str(identity.get("path", "")))
    expected = str(identity.get("sha256", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise RuntimeError(f"invalid {label} SHA-256 identity")
    exact_hash(path, expected, label)


def verify_tree_identity(path: Path, identity: object, label: str) -> None:
    if not isinstance(identity, dict):
        raise RuntimeError(f"missing {label} tree identity")
    actual = provenance.tree_identity(path)
    if actual != {
        "sha256": identity.get("sha256"),
        "files": identity.get("files"),
    }:
        raise RuntimeError(f"{label} tree identity changed")


def json_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def check_exact_output(log: str, label: str) -> None:
    lines = log.splitlines()
    output = common.parse_fields(common.exactly_one(lines, "UME_OUTPUT_FP "))
    reference = common.parse_fields(
        common.exactly_one(lines, "UME_REFERENCE_PASS ")
    )
    if (
        output.get("output_hash") != EXPECTED_OUTPUT_HASH
        or output.get("nonfinite") != "0"
        or reference.get("point_volume_errors") != "0"
        or reference.get("point_gradient_errors") != "0"
        or reference.get("elements") != EXPECTED_REFERENCE_ELEMENTS
    ):
        raise RuntimeError(f"{label}: exact full-GZP output/reference failed")


def verify_bound_selector(run: Path, expected_hash: str) -> None:
    selector = run / "frozen_treatment.txt"
    command_path = run / "restore.command.json"
    if not selector.is_file() or not command_path.is_file():
        raise RuntimeError(f"{run}: missing frozen selector or command")
    if common.sha256(selector) != expected_hash:
        raise RuntimeError(f"{run}: selector hash changed")
    if selector.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        raise RuntimeError(f"{run}: selector is writable")
    command = json.loads(command_path.read_text())
    if (
        not isinstance(command, list)
        or not command
        or Path(command[0]).name != "bwrap"
        or command.count("--ro-bind") != 1
        or str(selector.resolve()) not in command
        or any(Path(str(token)).name == "timeout" for token in command)
    ):
        raise RuntimeError(f"{run}: restore lacks immutable bind isolation")


def ledger_from_stats(stats: dict[str, int]) -> dict[str, int]:
    ledger = {suffix: sum_suffix(stats, suffix) for suffix in SOA_SUFFIXES}
    ledger.update(
        {suffix: sum_suffix(stats, suffix) for suffix in PUBLISH_SUFFIXES}
    )
    ledger["STR_PublishCreditHWM"] = max_suffix(stats, "STR_PublishCreditHWM")
    ledger["numInst_INDRMW"] = sum_suffix(stats, "numInst_INDRMW")
    ledger["cycles_INDRMW"] = sum_suffix(stats, "cycles_INDRMW")
    return ledger


def validate_complete_ledgers(arm: str, ledger: dict[str, int]) -> None:
    closed = (
        ("IND_SoaJitPredicateLineReads", "IND_SoaJitPredicateLineResponses"),
        ("IND_SoaJitAReadIssues", "IND_SoaJitAReadResponses"),
        ("IND_SoaJitAWriteIssues", "IND_SoaJitAWriteResponses"),
        ("IND_SoaJitValueReadIssues", "IND_SoaJitValueReadResponses"),
        ("IND_SoaJitLookaheadIssues", "IND_SoaJitLookaheadResponses"),
        ("STR_PublishIssues", "STR_PublishAccepts"),
        ("STR_PublishIssues", "STR_PublishWriteResponses"),
    )
    if any(ledger[left] != ledger[right] for left, right in closed):
        raise RuntimeError(f"{arm}: publisher/A/value ledger did not close")
    selected = ledger["IND_SoaJitSelected"]
    if (
        ledger["IND_SoaJitValueReadIssues"]
        + ledger["IND_SoaJitValueHits"]
        + ledger["IND_SoaJitValueMergedWaiters"]
        != selected
        or ledger["IND_SoaJitValueFills"]
        != ledger["IND_SoaJitValueReadResponses"]
        or ledger["IND_SoaJitValueDeliveries"] != selected
        or ledger["IND_SoaJitAliasesApplied"] != selected
        or ledger["IND_SoaJitPreAValueIssues"]
        != ledger["IND_SoaJitPreAValueUses"]
        or not 0
        < ledger["IND_SoaJitPreAValueReadyAtAResponse"]
        <= ledger["IND_SoaJitPreAValueUses"]
        or ledger["IND_SoaJitValuePrefetchIssues"]
        != ledger["IND_SoaJitValuePrefetchResponses"]
        or ledger["cycles_INDRMW"] <= 0
    ):
        raise RuntimeError(f"{arm}: RMW/A/value conservation failed")

    if arm == "volume_only":
        expected_instructions = FULL_WINDOWS
        expected_rmw = FULL_WINDOWS * 5 + 2
        expected_publish_lines = 0
        expected_publish_terminals = 0
        expected_hwm = 0
    else:
        expected_instructions = FULL_WINDOWS * 2
        expected_rmw = FULL_WINDOWS * 2 + 2
        expected_publish_lines = (
            FULL_WINDOWS * CURRENT_PUBLISH_LINES_PER_WINDOW
        )
        expected_publish_terminals = FULL_WINDOWS * 4
        expected_hwm = 8
    if (
        ledger["IND_SoaJitInstructions"] != expected_instructions
        or ledger["IND_SoaJitTerminalCompletions"] != expected_instructions
        or ledger["numInst_INDRMW"] != expected_rmw
        or ledger["STR_PublishIssues"] != expected_publish_lines
        or ledger["STR_PublishTerminals"] != expected_publish_terminals
        or ledger["STR_PublishCreditHWM"] != expected_hwm
    ):
        raise RuntimeError(f"{arm}: mechanism count differs")


def validate_candidate_gate(
    root: Path, expected_manifest_hash: str
) -> tuple[dict[str, object], list[dict[str, object]], dict[str, str]]:
    manifest_path = root / "manifest.json"
    results_path = root / "results.json"
    exact_hash(manifest_path, expected_manifest_hash, "candidate manifest")
    manifest = json_object(manifest_path)
    results = json_object(results_path)
    if (
        (root / "campaign.exit").read_text().strip() != "0"
        or manifest.get("schema") != "dx100.gzp_dual_logical16_gate.v3"
        or manifest.get("n") != FULL_N
        or manifest.get("replicas") != REPLICAS
        or manifest.get("arms") != list(SCHEDULE_ARMS)
        or manifest.get("physical_spd_elements") != 4096
        or manifest.get("simulated_metric") != "simTicks"
        or manifest.get("host_time_metric_authorized") is not False
    ):
        raise RuntimeError("candidate gate contract mismatch")
    rows_value = results.get("rows")
    if not isinstance(rows_value, list):
        raise RuntimeError("candidate results have no rows")
    rows = [dict(row) for row in rows_value if isinstance(row, dict)]
    if len(rows) != REPLICAS * len(SCHEDULE_ARMS):
        raise RuntimeError("candidate gate needs exactly two replicas per arm")

    run_records = manifest.get("runs")
    if not isinstance(run_records, list):
        raise RuntimeError("candidate manifest has no run records")
    records = {
        (str(item["arm"]), int(item["replica"])): item
        for item in run_records
        if isinstance(item, dict)
    }
    complete_rows: list[dict[str, object]] = []
    for row in rows:
        arm = str(row.get("arm"))
        replica = int(row.get("replica", 0))
        if arm not in SCHEDULE_ARMS or replica not in (1, 2):
            raise RuntimeError("candidate row has invalid arm/replica")
        run_name = f"{arm}_r{replica}"
        run = root / "runs" / run_name
        record = records.get((arm, replica))
        if record is None:
            raise RuntimeError(f"missing candidate run record {run_name}")
        verify_bound_selector(run, str(record["selector_sha256"]))
        if common.sha256(run / "restore.command.json") != record.get(
            "command_sha256"
        ):
            raise RuntimeError(f"{run_name}: restore command hash changed")
        checked = candidate.analyze_run(arm, run, FULL_N)
        for field in checked:
            if field != "replica" and row.get(field) != checked[field]:
                raise RuntimeError(f"{run_name}: analyzed {field} changed")
        log = (run / "restore.log").read_text(errors="replace")
        check_exact_output(log, run_name)
        stats = common.first_stats(run / "gem5/stats.txt")
        ledger = ledger_from_stats(stats)
        validate_complete_ledgers(arm, ledger)
        complete_rows.append({**row, "ledger": ledger})

    for arm in SCHEDULE_ARMS:
        replicas = sorted(
            (row for row in complete_rows if row["arm"] == arm),
            key=lambda row: int(row["replica"]),
        )
        left = {
            key: value
            for key, value in replicas[0].items()
            if key != "replica"
        }
        right = {
            key: value
            for key, value in replicas[1].items()
            if key != "replica"
        }
        if left != right:
            raise RuntimeError(
                f"{arm}: candidate replicas are not deterministic"
            )

    gem5 = manifest.get("gem5")
    config_tree = manifest.get("config_tree")
    if not isinstance(gem5, dict) or not isinstance(config_tree, dict):
        raise RuntimeError("candidate provenance is incomplete")
    verify_file_identity(gem5, "candidate gem5")
    verify_file_identity(manifest.get("guest"), "candidate guest")
    verify_file_identity(
        manifest.get("ramulator_library"), "candidate Ramulator library"
    )
    verify_file_identity(
        manifest.get("ramulator_config"), "candidate Ramulator config"
    )
    verify_tree_identity(
        root / "inputs/configs", config_tree, "candidate config"
    )
    verify_tree_identity(
        root / "checkpoint", manifest.get("checkpoint"), "candidate checkpoint"
    )
    identity = {
        "gem5_sha256": str(gem5.get("sha256")),
        "config_tree_sha256": str(config_tree.get("sha256")),
    }
    return manifest, complete_rows, identity


def find_native_report(root: Path) -> Path:
    choices = (
        root / "analysis/report.json",
        root / "analysis/gzp_soa_jit_correctness.json",
    )
    matches = [path for path in choices if path.is_file()]
    if len(matches) != 1:
        raise RuntimeError(
            "native16 evidence needs one supported analysis report"
        )
    return matches[0]


def validate_native16(
    root: Path, expected_manifest_hash: str, candidate_identity: dict[str, str]
) -> tuple[dict[str, object], dict[str, str]]:
    manifest_path = root / "manifest.json"
    exact_hash(manifest_path, expected_manifest_hash, "native16 manifest")
    manifest = json_object(manifest_path)
    report = json_object(find_native_report(root))
    records = report.get("records")
    if report.get("status") != "PASS" or not isinstance(records, list):
        raise RuntimeError("native16 analysis is not PASS")
    native = [
        record
        for record in records
        if isinstance(record, dict) and record.get("arm") == "native16"
    ]
    if not native:
        raise RuntimeError("native16 report has no native16 record")
    ticks: list[int] = []
    replica_records: list[dict[str, int]] = []
    for record in native:
        replica = int(record.get("replica", 0))
        run = root / "arms/native16" / f"replica-{replica}"
        if (run / "restore.exit").read_text().strip() != "0":
            raise RuntimeError("native16 restore wrapper failed")
        log = (run / "restore.log").read_text(errors="replace")
        check_exact_output(log, f"native16 replica {replica}")
        if log.count("m5_exit instruction encountered") != 1:
            raise RuntimeError("native16 lacks unique m5_exit")
        command = json.loads((run / "restore.command.json").read_text())
        if any(Path(str(token)).name == "timeout" for token in command):
            raise RuntimeError("native16 command used a timeout")
        stats = common.first_stats(run / "gem5/stats.txt")
        ticks_value = int(record.get("simTicks", 0))
        if ticks_value <= 0 or ticks_value != stats.get("simTicks"):
            raise RuntimeError("native16 simTicks changed")
        config = (run / "gem5/config.ini").read_text().splitlines()
        if (
            "num_tile_elements=16384" not in config
            or "physical_tile_elements=16384" not in config
        ):
            raise RuntimeError("native16 instantiated geometry changed")
        ticks.append(ticks_value)
        replica_records.append(
            {
                "replica": replica,
                "simTicks": ticks_value,
                "rmw_instructions": sum_suffix(stats, "numInst_INDRMW"),
                "rmw_cycles": sum_suffix(stats, "cycles_INDRMW"),
            }
        )

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise RuntimeError("native16 manifest has no artifacts")
    gem5 = artifacts.get("gem5")
    config_tree = artifacts.get("config_tree", manifest.get("config_tree"))
    if not isinstance(gem5, dict) or not isinstance(config_tree, dict):
        raise RuntimeError("native16 provenance is incomplete")
    verify_file_identity(gem5, "native16 gem5")
    verify_file_identity(artifacts.get("native16"), "native16 guest")
    verify_file_identity(
        artifacts.get("ramulator_library"), "native16 Ramulator library"
    )
    verify_file_identity(
        artifacts.get("ramulator_config"), "native16 Ramulator config"
    )
    config_path = Path(str(config_tree.get("path", root / "inputs/configs")))
    verify_tree_identity(config_path, config_tree, "native16 config")
    checkpoint_identities = manifest.get(
        "checkpoint_identity", manifest.get("checkpoints")
    )
    if not isinstance(checkpoint_identities, dict):
        raise RuntimeError("native16 checkpoint provenance is incomplete")
    native_checkpoint = checkpoint_identities.get("native16")
    if isinstance(native_checkpoint, dict) and "tree" in native_checkpoint:
        native_checkpoint = native_checkpoint["tree"]
    verify_tree_identity(
        root / "checkpoints/native16/gem5",
        native_checkpoint,
        "native16 checkpoint",
    )
    native_identity = {
        "gem5_sha256": str(gem5.get("sha256")),
        "config_tree_sha256": str(config_tree.get("sha256")),
    }
    if native_identity != candidate_identity:
        raise RuntimeError(
            "native16 is not matched to candidate gem5/config; run a separate "
            "native16 gate with the active candidate artifacts"
        )
    return {
        "simTicks": ticks,
        "mean_simTicks": sum(ticks) / len(ticks),
        "replicas": len(ticks),
        "records": replica_records,
        "exact_output": "PASS",
    }, native_identity


def parse_key_values(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator:
            result[key] = value
    return result


def validate_api_pair(
    root: Path, expected_manifest_hash: str
) -> dict[str, object]:
    manifest_path = root / "manifest.txt"
    exact_hash(manifest_path, expected_manifest_hash, "API manifest")
    manifest = parse_key_values(manifest_path)
    if (
        manifest.get("soa_pair_only_geometry_delta")
        != "physical_tile_elements"
    ):
        raise RuntimeError("API pair does not isolate physical geometry")
    with (root / "matrix.tsv").open(newline="") as source:
        rows = list(csv.DictReader(source, delimiter="\t"))
    selected = {
        row.get("arm"): row for row in rows if row.get("arm") in API_ARMS
    }
    if set(selected) != set(API_ARMS):
        raise RuntimeError("API evidence lacks the matched physical pair")
    physical4 = selected["soa_metadata16_physical4"]
    physical16 = selected["soa_metadata16_physical16"]
    for name, row, physical in (
        ("soa_metadata16_physical4", physical4, "4096"),
        ("soa_metadata16_physical16", physical16, "16384"),
    ):
        if (
            row.get("mode") != "soa"
            or row.get("logical") != "16384"
            or row.get("physical") != physical
            or not str(row.get("simTicks", "")).isdigit()
        ):
            raise RuntimeError(f"{name}: invalid API geometry/result")
        run = root / "runs" / name
        log = (run / "restore.log").read_text(errors="replace")
        if (
            log.count("m5_exit instruction encountered") != 1
            or "errors=0" not in log
        ):
            raise RuntimeError(f"{name}: API run did not complete exactly")
        stats = common.first_stats(run / "stats.txt")
        if int(row["simTicks"]) != stats.get("simTicks"):
            raise RuntimeError(f"{name}: API simTicks changed")
    if physical4.get("output_hash") != physical16.get("output_hash"):
        raise RuntimeError("API pair output hashes differ")
    guest_path = root / "bin/test_hybrid_rmw_soa_T16384"
    guest_hash = manifest.get("guest16_sha256")
    if guest_hash:
        exact_hash(guest_path, guest_hash, "API guest16")
    checkpoint_path = Path(manifest.get("soa_pair_checkpoint", ""))
    if not checkpoint_path.is_dir():
        raise RuntimeError("API pair checkpoint is missing")
    ticks4 = int(physical4["simTicks"])
    ticks16 = int(physical16["simTicks"])
    return {
        "physical4_simTicks": ticks4,
        "physical16_simTicks": ticks16,
        "physical4_over_physical16": ticks4 / ticks16,
        "output_hash": physical4["output_hash"],
        "scope": "API virtualization isolation only; not a GZP publisher result",
    }


def summarize(
    candidate_rows: list[dict[str, object]],
    native: dict[str, object],
    api: dict[str, object],
) -> dict[str, object]:
    ticks = {
        arm: sorted(
            int(row["simTicks"]) for row in candidate_rows if row["arm"] == arm
        )
        for arm in SCHEDULE_ARMS
    }
    if any(len(values) != REPLICAS for values in ticks.values()):
        raise RuntimeError("candidate summary lost a replica")
    old_mean = sum(ticks["volume_only"]) / REPLICAS
    dual_mean = sum(ticks["dual_logical16"]) / REPLICAS
    native_mean = float(native["mean_simTicks"])
    return {
        "status": "ATTRIBUTION_READY",
        "schedule_gain": {
            "old_hybrid_simTicks": ticks["volume_only"],
            "dual_hybrid_simTicks": ticks["dual_logical16"],
            "old_hybrid_mean_simTicks": old_mean,
            "dual_hybrid_mean_simTicks": dual_mean,
            "old_over_dual_speedup": old_mean / dual_mean,
            "old_minus_dual_ticks": old_mean - dual_mean,
            "scope": "schedule/publisher treatment at physical4",
        },
        "ordinary_native16_ceiling": {
            **native,
            "dual_over_native16_ticks": dual_mean - native_mean,
            "native16_over_dual_speedup": native_mean / dual_mean,
            "scope": "end-to-end ceiling, not isolated physical overhead",
        },
        "api_matched_physical_overhead": api,
        "same_instruction_gzp_physical16": {
            "available": False,
            "required_future_publisher_lines_per_window": (
                FUTURE_PHYSICAL16_REQUIRED_LINES_PER_WINDOW
            ),
            "reason": "source-offset publisher/core work is out of runner scope",
        },
        "simulated_metric": "simTicks",
        "host_time_metric_authorized": False,
    }


def outside_repository(path: Path) -> bool:
    resolved = path.resolve()
    root = ROOT.resolve()
    return resolved != root and root not in resolved.parents


def require_base_lineage() -> None:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", BASE_COMMIT, "HEAD"],
        cwd=ROOT,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"source does not descend from {BASE_COMMIT}")


def main() -> int:
    args = parse_args()
    campaign_plan = plan(args)
    if not args.execute:
        print(json.dumps(campaign_plan, indent=2, sort_keys=True))
        return 0
    if common.source_status():
        raise SystemExit("evidence assembly requires a clean source tree")
    if not outside_repository(args.out):
        raise SystemExit("--out must be outside the source repository")
    if args.out.exists():
        raise SystemExit(f"refusing existing output: {args.out}")
    args.out.mkdir(parents=True)
    common.atomic_text(args.out / "assembly.exit", "running\n")
    try:
        require_base_lineage()
        publisher_audit = audit_publisher_boundary()
        (
            candidate_manifest,
            candidate_rows,
            candidate_identity,
        ) = validate_candidate_gate(
            args.candidate_gate,
            args.expected_candidate_manifest_sha256,
        )
        native, native_identity = validate_native16(
            args.native16_evidence,
            args.expected_native16_manifest_sha256,
            candidate_identity,
        )
        api = validate_api_pair(
            args.api_physical_evidence,
            args.expected_api_manifest_sha256,
        )
        summary = summarize(candidate_rows, native, api)
        frozen = args.out / "evidence_manifests"
        frozen.mkdir()
        frozen_files = {
            "candidate_manifest": (
                args.candidate_gate / "manifest.json",
                frozen / "candidate_manifest.json",
            ),
            "candidate_results": (
                args.candidate_gate / "results.json",
                frozen / "candidate_results.json",
            ),
            "native16_manifest": (
                args.native16_evidence / "manifest.json",
                frozen / "native16_manifest.json",
            ),
            "native16_report": (
                find_native_report(args.native16_evidence),
                frozen / "native16_report.json",
            ),
            "api_manifest": (
                args.api_physical_evidence / "manifest.txt",
                frozen / "api_manifest.txt",
            ),
            "api_matrix": (
                args.api_physical_evidence / "matrix.tsv",
                frozen / "api_matrix.tsv",
            ),
        }
        frozen_identities = {}
        for name, (source, destination) in frozen_files.items():
            digest = provenance.copy_stable_artifact(source, destination)
            destination.chmod(0o444)
            frozen_identities[name] = {
                "path": str(destination),
                "sha256": digest,
            }
        manifest = {
            **campaign_plan,
            "source_commit": common.source_commit(),
            "publisher_boundary_audit": publisher_audit,
            "candidate_source": candidate_manifest.get("source"),
            "candidate_identity": candidate_identity,
            "native16_identity": native_identity,
            "frozen_evidence": frozen_identities,
        }
        common.atomic_json(args.out / "manifest.json", manifest)
        common.atomic_json(
            args.out / "results.json",
            {
                "candidate_rows": candidate_rows,
                "native16": native,
                "api_physical_pair": api,
                "summary": summary,
            },
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        common.atomic_text(args.out / "assembly.exit", "1\n")
        print(f"error: {error}", file=sys.stderr)
        return 1
    common.atomic_text(args.out / "assembly.exit", "0\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print("GZP_FINAL_ATTRIBUTION_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
