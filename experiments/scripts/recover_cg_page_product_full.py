#!/usr/bin/env python3
"""Recover a terminal CG full candidate after a branch-status-only wrapper failure."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
EXPECTED_ROOT_NAME = "2026-08-24-cg-page-product-full-precomputed-5d51743b-r2"
EXPECTED_SOURCE_COMMIT = "5d51743bfca566c486c6786cf3b18e6d378d805a"
EXPECTED_CHECKPOINT_LEDGER_SHA256 = (
    "bd4d88775b9e4a7776fa73aa7867de8b2d93ecbd965352cc92495447751eb508"
)
EXPECTED_REFERENCE = Path(
    "/data1/nier/dx100-runs/2026-08-11-cg-bounded-789cc703-full-v8/"
    "bounded4_cached/run.log"
)
EXPECTED_REFERENCE_SHA256 = (
    "0fe931685c37695bc51c74288c67f1494a0c91a723f8e831efa0ac2a7515441c"
)
EXPECTED_RAMULATOR = Path(
    "/data1/nier/dx100-runs/2026-08-12-hybrid-line-handoff-8a5c7712/"
    "input/libramulator.so"
)


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


def verify_ledger(path: Path, relative_root: Path) -> list[Path]:
    artifacts: list[Path] = []
    for expected, artifact in ledger_entries(path, relative_root):
        require(artifact.is_file(), f"missing ledger artifact: {artifact}")
        require(sha256(artifact) == expected, f"hash mismatch: {artifact}")
        artifacts.append(artifact)
    return artifacts


def expected_artifacts(root: Path, repo: Path) -> dict[Path, str]:
    return {
        Path(
            "/data1/nier/dx100-binaries/"
            "gem5-ef070d16bb1b25668fe80468693dade4eeaf1776a72fbc51d7a9ce070e5af483.opt"
        ): "ef070d16bb1b25668fe80468693dade4eeaf1776a72fbc51d7a9ce070e5af483",
        EXPECTED_RAMULATOR: (
            "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753"
        ),
        root / "bin/cg_physical_page_product": (
            "ff03d3ef89761bb956ecdca5030862d15d43ee0784dbe5bff364972b3523fb04"
        ),
        root / "input/physical_page_product_soa_jit.selector": (
            "61be146ef89cf032f3f52974f95ace0cdaea123748cfe91e04a3663955d13562"
        ),
        repo / "benchmarks/NAS/cg/cg.cpp": (
            "d254b68d34ff306a566f6b54256720314f3d1745b13284593b040e87ed544e60"
        ),
        repo / "configs/deprecated/example/se.py": (
            "aacc6e624b7ab0e7b032d5cb913974fa790efdca84598bf468c11f14b9575d0f"
        ),
        repo / "ext/ramulator2/ramulator2/example_gem5_config.yaml": (
            "aca6e27b58afdfbfd80b7ec41c3f0e7e574a1fc7355a3512981ead823f68731b"
        ),
        repo / "experiments/scripts/run_cg_logical_page_rmw_hybrid.sh": (
            "0276956040d539feb6b25a6272b7a89afd5b5e4b21b46a9d92250fac89c7cee8"
        ),
        EXPECTED_REFERENCE: EXPECTED_REFERENCE_SHA256,
        root / "input/cg_data_4C.h": (
            "f2b18716e4a2356c597c95ee3583549def72700f2cb3294b0fcaacca46dbe131"
        ),
    }


def verify_expected_artifact_ledger(root: Path, repo: Path) -> list[Path]:
    ledger = ledger_entries(root / "input/artifact_sha256.before", repo)
    actual = {artifact.resolve(): digest for digest, artifact in ledger}
    expected = {
        artifact.resolve(): digest
        for artifact, digest in expected_artifacts(root, repo).items()
    }
    require(actual == expected, "artifact ledger is not the pinned full-CG set")
    for artifact, digest in expected.items():
        require(artifact.is_file(), f"missing pinned artifact: {artifact}")
        require(sha256(artifact) == digest, f"pinned artifact mismatch: {artifact}")
    return list(expected)


def active_root_process(root: Path) -> str | None:
    needle = str(root).encode()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            command = (proc / "cmdline").read_bytes()
        except OSError:
            continue
        if needle not in command:
            continue
        if b"gem5" in command or b"run_cg_logical_page_rmw_hybrid.sh" in command:
            return f"pid={proc.name} cmdline={command.replace(chr(0).encode(), b' ').decode(errors='replace')}"
    return None


def snapshot(paths: list[Path]) -> dict[Path, str]:
    unique = dict.fromkeys(path.resolve() for path in paths)
    return {path: sha256(path) for path in unique}


def verify_snapshot(expected: dict[Path, str]) -> None:
    for path, digest in expected.items():
        require(path.is_file(), f"certified input disappeared: {path}")
        require(sha256(path) == digest, f"certified input changed: {path}")


def write_temporary(path: Path, contents: str) -> Path:
    temporary = path.with_name(path.name + ".tmp")
    require(not temporary.exists(), f"refusing stale temporary output: {temporary}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def atomic_write(path: Path, contents: str) -> None:
    temporary = write_temporary(path, contents)
    os.replace(temporary, path)


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


def branch_ahead(line: str) -> tuple[str, int]:
    match = re.fullmatch(r"(## .+) \[ahead ([0-9]+)\]", line)
    require(match is not None, "source status is not one clean ahead-only line")
    return match.group(1), int(match.group(2))


def recover(
    root: Path, repo: Path, *, allow_existing_seal: bool = False
) -> tuple[dict[str, object], dict[Path, str]]:
    manifest_path = root / "manifest.txt"
    restore_path = root / "run/restore.log"
    stats_path = root / "run/stats.txt"
    config_path = root / "run/config.ini"
    require(root.is_dir(), f"missing evidence root: {root}")
    require(root.name == EXPECTED_ROOT_NAME, "recovery root is not the pinned CG run")
    for path in (manifest_path, restore_path, stats_path, config_path):
        require(path.is_file() and path.stat().st_size > 0, f"missing {path}")
    seal_paths = (
        root / "recovered_result.json",
        root / "recovered_result_sha256.txt",
        root / "RECOVERED_GATE.complete",
    )
    for path in seal_paths:
        if allow_existing_seal:
            require(path.is_file(), f"missing sealed output: {path}")
        else:
            require(not path.exists(), f"refusing to overwrite {path}")

    running_status = root / "RUNNING.status"
    if running_status.exists():
        require(
            running_status.read_text(encoding="utf-8").strip() != "running",
            "RUNNING.status still reports running",
        )
    live_process = active_root_process(root)
    require(live_process is None, f"CG root still has a live process: {live_process}")
    require(
        not (root / "run/logical_page_trace.log").exists(),
        "disabled-full run unexpectedly contains a logical-page trace",
    )

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

    source_before_path = root / "input/source_status.before"
    source_after_path = root / "input/source_status.after"
    require(source_after_path.is_file(), "wrapper did not reach source-status check")
    source_before = source_before_path.read_text().splitlines()
    source_after = source_after_path.read_text().splitlines()
    require(
        len(source_before) == 1 and len(source_after) == 1,
        "source status contains working-tree changes",
    )
    before_branch, before_ahead = branch_ahead(source_before[0])
    after_branch, after_ahead = branch_ahead(source_after[0])
    require(before_branch == after_branch, "source branch changed during run")
    require(after_ahead > before_ahead, "source status did not change only by commits")
    artifact_paths = verify_expected_artifact_ledger(root, repo)
    checkpoint_ledger = root / "input/checkpoint.files.sha256"
    require(
        sha256(checkpoint_ledger) == EXPECTED_CHECKPOINT_LEDGER_SHA256,
        "checkpoint ledger identity mismatch",
    )
    checkpoint_paths = verify_ledger(checkpoint_ledger, root / "checkpoint")
    certified_paths = [
        manifest_path,
        restore_path,
        stats_path,
        config_path,
        root / "checkpoint.exit",
        root / "run/restore.exit",
        root / "EXPECTED_WRAPPER_RECOVERY.status",
        source_before_path,
        source_after_path,
        root / "input/artifact_sha256.before",
        checkpoint_ledger,
        *artifact_paths,
        *checkpoint_paths,
    ]
    initial_snapshot = snapshot(certified_paths)

    manifest = key_values(manifest_path)
    expected_manifest = {
        "schema": "dx100.cg.physical_page_product_soa_jit.v2",
        "size": "full",
        "cg_na": "150000",
        "source_commit": EXPECTED_SOURCE_COMMIT,
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
    require(reference_path == EXPECTED_REFERENCE, "reference path is not pinned")
    require(
        manifest["reference_sha256"] == EXPECTED_REFERENCE_SHA256
        and sha256(reference_path) == EXPECTED_REFERENCE_SHA256,
        "reference hash mismatch",
    )
    precomputed = Path(manifest["precomputed_data_path"])
    require(precomputed == root / "input/cg_data_4C.h", "input path is not pinned")
    require(
        manifest["precomputed_data_sha256"]
        == "f2b18716e4a2356c597c95ee3583549def72700f2cb3294b0fcaacca46dbe131"
        and sha256(precomputed) == manifest["precomputed_data_sha256"],
        "precomputed input hash mismatch",
    )
    require(
        precomputed.stat().st_size == int(manifest["precomputed_data_bytes"]),
        "precomputed input size mismatch",
    )
    require(
        Path(manifest["ramulator_library_path"]) == EXPECTED_RAMULATOR
        and manifest["ramulator_library_sha256"]
        == "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753",
        "Ramulator identity is not pinned",
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

    result = {
        "schema": "dx100.cg.physical_page_product_soa_jit.recovered.v1",
        "validation": "PASS",
        "recovery_reason": "branch_ahead_count_only",
        "source_ahead_before_after": [before_ahead, after_ahead],
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
    verify_snapshot(initial_snapshot)
    result["certified_input_snapshot_sha256"] = hashlib.sha256(
        "".join(
            f"{digest}  {path}\n"
            for path, digest in sorted(
                initial_snapshot.items(), key=lambda item: str(item[0])
            )
        ).encode()
    ).hexdigest()
    return result, initial_snapshot


def validate_seal(root: Path, repo: Path) -> dict[str, object]:
    gate = root / "RECOVERED_GATE.complete"
    result_path = root / "recovered_result.json"
    ledger = root / "recovered_result_sha256.txt"
    require(gate.read_text(encoding="utf-8") == "PASS\n", "bad recovered gate")
    verify_ledger(ledger, root)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    require(
        result.get("schema") == "dx100.cg.physical_page_product_soa_jit.recovered.v1"
        and result.get("validation") == "PASS"
        and result.get("performance_status") == "correctness_only_unpromoted"
        and result.get("native_reruns") == 0,
        "recovered result certificate is invalid",
    )
    regenerated, raw_snapshot = recover(
        root, repo, allow_existing_seal=True
    )
    require(result == regenerated, "sealed result disagrees with pinned raw evidence")
    verify_snapshot(raw_snapshot)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    repo = args.repo_root.resolve()
    if args.validate:
        print(json.dumps(validate_seal(root, repo), indent=2, sort_keys=True))
        return
    result, initial_snapshot = recover(root, repo)
    result_path = root / "recovered_result.json"
    ledger_path = root / "recovered_result_sha256.txt"
    gate_path = root / "RECOVERED_GATE.complete"
    result_contents = json.dumps(result, indent=2, sort_keys=True) + "\n"
    raw_ledger_paths = (
        root / "manifest.txt",
        root / "input/artifact_sha256.before",
        root / "input/checkpoint.files.sha256",
        root / "input/source_status.before",
        root / "input/source_status.after",
        root / "EXPECTED_WRAPPER_RECOVERY.status",
        root / "run/restore.log",
        root / "run/stats.txt",
        root / "run/config.ini",
        Path(__file__).resolve(),
    )
    ledger = "".join(
        f"{sha256(path)}  {path}\n" for path in raw_ledger_paths
    )
    result_digest = hashlib.sha256(result_contents.encode()).hexdigest()
    ledger += f"{result_digest}  {result_path}\n"
    result_temporary = write_temporary(result_path, result_contents)
    ledger_temporary = write_temporary(ledger_path, ledger)
    verify_snapshot(initial_snapshot)
    require(sha256(result_temporary) == result_digest, "temporary result changed")
    require(
        sha256(ledger_temporary) == hashlib.sha256(ledger.encode()).hexdigest(),
        "temporary ledger changed",
    )
    os.replace(result_temporary, result_path)
    os.replace(ledger_temporary, ledger_path)
    verify_snapshot(initial_snapshot)
    verify_ledger(ledger_path, root)
    atomic_write(gate_path, "PASS\n")
    validate_seal(root, repo)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RecoveryError, ValueError) as error:
        raise SystemExit(f"CG recovery failed: {error}") from error
