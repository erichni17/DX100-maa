#!/usr/bin/env python3
"""Recover a terminal CG full candidate after a branch-status-only wrapper failure."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

FATAL = re.compile(r"panic|fatal|assert|abort|segmentation fault|error:", re.I)
EXIT = re.compile(
    r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$", re.M
)
STATS_BEGIN = "---------- Begin Simulation Statistics"
STATS_END = "---------- End Simulation Statistics"
QUANTIZED_FIELDS = ("x_q5", "x_q6", "z_q5", "z_q6")
RELATIVE_BOUNDS = {
    "x_sum": 1.0e-8,
    "x_norm_sq": 1.0e-8,
    "z_sum": 1.0e-8,
    "z_norm_sq": 1.0e-8,
    "rnorm": 1.0e-3,
    "zeta": 1.0e-10,
}


class RecoveryError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def marker_values(line: str) -> dict[str, str]:
    return {
        key: value
        for token in line.split()
        if "=" in token
        for key, value in (token.split("=", 1),)
    }


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RecoveryError(message)


def ledger_entries(path: Path, relative_root: Path) -> list[tuple[str, Path]]:
    entries: list[tuple[str, Path]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split(maxsplit=1)
        require(
            len(fields) == 2 and re.fullmatch(r"[0-9a-f]{64}", fields[0])
            is not None,
            f"malformed {path.name} line {number}",
        )
        artifact = Path(fields[1].lstrip("*"))
        if not artifact.is_absolute():
            artifact = relative_root / artifact
        entries.append((fields[0], artifact))
    require(bool(entries), f"empty hash ledger: {path}")
    return entries


def verify_ledger(path: Path, relative_root: Path) -> None:
    for expected, artifact in ledger_entries(path, relative_root):
        require(artifact.is_file(), f"missing ledger artifact: {artifact}")
        require(sha256(artifact) == expected, f"hash mismatch: {artifact}")


def first_stats_section(stats: str) -> str:
    begin = stats.find(STATS_BEGIN)
    end = stats.find(STATS_END, begin + len(STATS_BEGIN))
    require(begin >= 0 and end > begin, "missing first statistics section")
    return stats[begin:end]


def first_stat(section: str, name: str) -> int:
    matches = re.findall(rf"^{re.escape(name)}\s+([0-9]+)\b", section, re.M)
    require(len(matches) == 1, f"requires one first-window {name}")
    value = int(matches[0])
    require(value > 0, f"first-window {name} must be positive")
    return value


def stat_sum(section: str, suffix: str) -> int:
    matches = re.findall(
        rf"^\S*_{re.escape(suffix)}\s+([0-9]+)\b", section, re.M
    )
    require(bool(matches), f"first statistics window lacks *_{suffix}")
    return sum(map(int, matches))


def relative_delta(candidate: str, reference: str) -> float:
    candidate_value = float(candidate)
    reference_value = float(reference)
    denominator = max(abs(reference_value), 1.0e-300)
    return abs(candidate_value - reference_value) / denominator


def one_line(log: str, prefix: str) -> str:
    matches = [line for line in log.splitlines() if line.startswith(prefix)]
    require(len(matches) == 1, f"requires one {prefix.strip()} marker")
    return matches[0]


def recover(root: Path, repo: Path) -> dict[str, object]:
    manifest_path = root / "manifest.txt"
    restore_path = root / "run/restore.log"
    stats_path = root / "run/stats.txt"
    config_path = root / "run/config.ini"
    require(root.is_dir(), f"missing evidence root: {root}")
    for path in (manifest_path, restore_path, stats_path, config_path):
        require(path.is_file() and path.stat().st_size > 0, f"missing {path}")
    for path in (
        root / "recovered_result.json",
        root / "recovered_result_sha256.txt",
        root / "RECOVERED_GATE.complete",
    ):
        require(not path.exists(), f"refusing to overwrite {path}")

    status = key_values(root / "EXPECTED_WRAPPER_RECOVERY.status")
    require(
        status
        == {
            "expected_wrapper_failure": (
                "source_status_ahead_count_changed_by_post_launch_commits"
            ),
            "simulation_inputs_changed": "false",
            "recovery": "independent_terminal_and_artifact_classifier_after_exit",
        },
        "missing or unexpected wrapper-recovery declaration",
    )
    require((root / "checkpoint.exit").read_text().strip() == "0", "bad checkpoint")
    require((root / "run/restore.exit").read_text().strip() == "0", "bad restore")
    require(not (root / "gate.complete").exists(), "ordinary gate already exists")

    source_before = (root / "input/source_status.before").read_text().splitlines()
    require(
        len(source_before) == 1 and source_before[0].startswith("## "),
        "launch worktree was dirty",
    )
    verify_ledger(root / "input/artifact_sha256.before", repo)
    verify_ledger(root / "input/checkpoint.files.sha256", root / "checkpoint")

    manifest = key_values(manifest_path)
    expected_manifest = {
        "schema": "dx100.cg.physical_page_product_soa_jit.v2",
        "size": "full",
        "cg_na": "150000",
        "comparison_contract": "correctness_only",
        "trace_mode": "disabled_full",
        "input_construction": "frozen_header",
        "arm": "hybrid_only",
        "comparison_arms": "0",
        "native_reruns": "0",
        "wall_timeout": "none",
        "logical_elements": "16384",
        "physical_tile_elements": "4096",
        "num_initial_row_table_slices": "32",
        "memory_channels": "2",
        "num_tiles_per_core": "8",
        "logical_tile_page_scheduler": "false",
        "logical_scheduler_reserved_lanes": "0",
        "external_coherent_backing_bytes": "786432",
        "physical_spd_payload_bytes": "524288",
        "logical_scheduler_reserved_lane_payload_bytes": "0",
        "hidden_logical_payload_bytes": "0",
        "host_payload_access": "0",
    }
    for key, expected in expected_manifest.items():
        require(manifest.get(key) == expected, f"manifest {key} != {expected}")
    reference_path = Path(manifest["reference_path"])
    require(
        sha256(reference_path) == manifest["reference_sha256"],
        "reference hash mismatch",
    )
    precomputed = Path(manifest["precomputed_data_path"])
    require(
        sha256(precomputed) == manifest["precomputed_data_sha256"],
        "precomputed input hash mismatch",
    )
    require(
        precomputed.stat().st_size == int(manifest["precomputed_data_bytes"]),
        "precomputed input size mismatch",
    )

    log = restore_path.read_text(encoding="utf-8")
    require(FATAL.search(log) is None, "fatal evidence in restore log")
    require(len(EXIT.findall(log)) == 1, "requires exactly one m5_exit")
    require(log.count("ROI End!!!") == 1, "requires exactly one ROI End")
    candidate_line = one_line(log, "CG_FINGERPRINT ")
    reference_line = manifest["reference_fingerprint"]
    candidate = marker_values(candidate_line)
    reference = marker_values(reference_line)
    require(candidate.get("elements") == "150000", "wrong fingerprint size")
    for values in (candidate, reference):
        require(values.get("result") == "PASS", "fingerprint did not pass")
        require(values.get("nonfinite_x") == "0", "nonfinite x result")
        require(values.get("nonfinite_z") == "0", "nonfinite z result")
    for field in QUANTIZED_FIELDS:
        require(candidate.get(field) == reference.get(field), f"{field} mismatch")
    deltas: dict[str, float] = {}
    for field, tolerance in RELATIVE_BOUNDS.items():
        require(field in candidate and field in reference, f"missing {field}")
        delta = relative_delta(candidate[field], reference[field])
        require(delta <= tolerance, f"{field} delta {delta} > {tolerance}")
        deltas[field] = delta

    selection_line = one_line(log, "CG_LOGICAL16_RMW_SELECTION ")
    selection = marker_values(selection_line)
    terminal_line = one_line(log, "CG_LOGICAL16_RMW_TERMINAL ")
    terminal = marker_values(terminal_line)
    for values, kind in ((selection, "selection"), (terminal, "terminal")):
        require(
            values.get("treatment") == "physical_page_product_soa_jit",
            f"wrong {kind} treatment",
        )
        require(
            values.get("producer") == "physical_page_mul_response_publish",
            f"wrong {kind} producer",
        )
        require(values.get("host_payload_access") == "0", f"host {kind} payload")
        require(values.get("performance_promotable") == "0", f"bad {kind} claim")
    require(terminal.get("result") == "PASS", "CG terminal did not pass")

    integer_fields = (
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
        "q_spmv_eligible_windows",
        "q_spmv_routed_windows",
        "residual_spmv_eligible_windows",
        "residual_spmv_routed_windows",
        "external_coherent_backing_bytes",
        "physical_spd_payload_bytes",
        "logical_scheduler_reserved_lanes",
        "logical_scheduler_reserved_lane_payload_bytes",
    )
    try:
        numbers = {field: int(terminal[field]) for field in integer_fields}
    except (KeyError, ValueError) as error:
        raise RecoveryError(f"invalid terminal integer: {error}") from error
    windows = numbers["full_windows"]
    require(windows > 0, "no full windows")
    require(numbers["logical_page_windows"] == 0, "logical scheduler used")
    require(numbers["logical_alu_vectors"] == 0, "logical ALU used")
    require(numbers["physical_page_product_windows"] == windows, "window mismatch")
    require(numbers["physical_alu_vectors"] == windows * 4, "physical ALU mismatch")
    for prefix in ("q_spmv", "residual_spmv"):
        eligible = numbers[f"{prefix}_eligible_windows"]
        routed = numbers[f"{prefix}_routed_windows"]
        require(eligible > 0 and routed == eligible, f"{prefix} routing mismatch")
    require(
        windows
        == numbers["q_spmv_routed_windows"]
        + numbers["residual_spmv_routed_windows"],
        "routed-window sum mismatch",
    )
    index_words = numbers["staged_index_words"]
    require(index_words == windows * 16384, "index-word mismatch")
    require(numbers["staged_value_words"] == 0, "unexpected staged values")
    require(numbers["product_words"] == index_words, "product-word mismatch")
    require(numbers["index_publish_pages"] == windows * 4, "index pages mismatch")
    require(numbers["value_publish_pages"] == 0, "unexpected value pages")
    require(
        numbers["product_publish_pages"] == numbers["index_publish_pages"],
        "product pages mismatch",
    )
    require(numbers["external_coherent_backing_bytes"] == 786432, "backing mismatch")
    require(numbers["physical_spd_payload_bytes"] == 524288, "SPD payload mismatch")
    require(numbers["logical_scheduler_reserved_lanes"] == 0, "reserved lanes")
    require(
        numbers["logical_scheduler_reserved_lane_payload_bytes"] == 0,
        "reserved lane payload",
    )

    config_lines = set(config_path.read_text(encoding="utf-8").splitlines())
    for resolved in (
        "num_maas=1",
        "num_tiles_per_core=8",
        "num_tile_elements=16384",
        "physical_tile_elements=4096",
        "logical_tile_page_scheduler=false",
        "num_offset_table_entries=16384",
        "num_offset_table_epoch_entries=16384",
        "num_initial_row_table_slices=32",
        "soa_jit_predicate_active_credits=16",
        "soa_jit_active_value_owners=32",
    ):
        require(resolved in config_lines, f"config lacks {resolved}")
    require(
        sum(line in {"[system.mem_ctrls0]", "[system.mem_ctrls1]"} for line in config_lines)
        == 2,
        "requires two memory controllers",
    )

    stats = stats_path.read_text(encoding="utf-8")
    section = first_stats_section(stats)
    sim_ticks = first_stat(section, "simTicks")
    instructions = stat_sum(section, "IND_SoaJitInstructions")
    terminals = stat_sum(section, "IND_SoaJitTerminalCompletions")
    selected = stat_sum(section, "IND_SoaJitSelected")
    rejected = stat_sum(section, "IND_SoaJitPredicateRejected")
    aliases = stat_sum(section, "IND_SoaJitAliasesApplied")
    fallbacks = stat_sum(section, "IND_BoundedGlobalMergeFallbacks")
    issues = stat_sum(section, "STR_PublishIssues")
    accepts = stat_sum(section, "STR_PublishAccepts")
    responses = stat_sum(section, "STR_PublishWriteResponses")
    publication_terminals = stat_sum(section, "STR_PublishTerminals")
    require(instructions == windows and terminals == windows, "SoA closure mismatch")
    require(selected == index_words and rejected == 0, "selection mismatch")
    require(aliases == index_words and fallbacks == 0, "alias/fallback mismatch")
    expected_pages = windows * 8
    expected_lines = expected_pages * 256
    require(issues == expected_lines, "publisher issue mismatch")
    require(accepts == issues and responses == issues, "publisher response mismatch")
    require(publication_terminals == expected_pages, "publisher terminal mismatch")

    return {
        "schema": "dx100.cg.physical_page_product_soa_jit.recovered.v1",
        "validation": "PASS",
        "recovery_reason": "branch_ahead_count_only",
        "performance_status": "correctness_only_unpromoted",
        "native_reruns": 0,
        "source_commit": manifest["source_commit"],
        "simTicks": sim_ticks,
        "logical_windows": windows,
        "q_spmv_eligible_routed": [
            numbers["q_spmv_eligible_windows"],
            numbers["q_spmv_routed_windows"],
        ],
        "residual_spmv_eligible_routed": [
            numbers["residual_spmv_eligible_windows"],
            numbers["residual_spmv_routed_windows"],
        ],
        "publisher_issue_accept_response": [issues, accepts, responses],
        "publisher_terminals": publication_terminals,
        "soa_jit_terminals_instructions": [terminals, instructions],
        "external_coherent_backing_bytes": 786432,
        "physical_spd_payload_bytes": 524288,
        "fingerprint_relative_deltas": deltas,
        "candidate_fingerprint": candidate_line,
        "reference_fingerprint": reference_line,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    args = parser.parse_args()
    root = args.root.resolve()
    repo = args.repo_root.resolve()
    result = recover(root, repo)
    result_path = root / "recovered_result.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    ledger_paths = (
        root / "manifest.txt",
        root / "input/artifact_sha256.before",
        root / "input/checkpoint.files.sha256",
        root / "input/source_status.before",
        root / "EXPECTED_WRAPPER_RECOVERY.status",
        root / "run/restore.log",
        root / "run/stats.txt",
        root / "run/config.ini",
        Path(__file__).resolve(),
        result_path,
    )
    ledger = "".join(f"{sha256(path)}  {path}\n" for path in ledger_paths)
    (root / "recovered_result_sha256.txt").write_text(ledger)
    (root / "RECOVERED_GATE.complete").write_text("PASS\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RecoveryError, ValueError) as error:
        raise SystemExit(f"CG recovery failed: {error}") from error
