#!/usr/bin/env python3
"""Classify supplied hybrid full-application evidence roots once, fail-closed.

This intentionally reads files only.  A root is *running* only when its owner
wrote ``RUNNING.status`` containing ``running``; a PID, process-exit note, or
the absence of either never establishes completion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Callable

FATAL = re.compile(r"panic|fatal|assert|abort|segmentation fault|error:", re.I)
EXIT = re.compile(
    r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$", re.M
)
STATS_BEGIN = "---------- Begin Simulation Statistics"
STATS_END = "---------- End Simulation Statistics"


def text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (text(path) or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def marker_values(line: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for token in line.split():
        if "=" in token:
            key, value = token.split("=", 1)
            values[key] = value
    return values


def require_exact_file(path: Path, expected: str, reasons: list[str]) -> None:
    if (text(path) or "").strip() != expected:
        reasons.append(f"{path.name} is not exact {expected!r}")


def verify_sha256_ledger(path: Path, reasons: list[str]) -> None:
    ledger = text(path)
    if not ledger:
        reasons.append(f"missing or unreadable {path.name}")
        return
    for number, line in enumerate(ledger.splitlines(), 1):
        fields = line.split(maxsplit=1)
        if len(fields) != 2 or not re.fullmatch(r"[0-9a-f]{64}", fields[0]):
            reasons.append(f"malformed {path.name} line {number}")
            continue
        artifact = Path(fields[1].lstrip("*"))
        if sha256(artifact) != fields[0]:
            reasons.append(f"hash mismatch from {path.name}: {artifact}")


def require_manifest_hashes(
    manifest: dict[str, str],
    pairs: tuple[tuple[str, str], ...],
    reasons: list[str],
) -> None:
    for path_key, hash_key in pairs:
        path_value, expected = manifest.get(path_key), manifest.get(hash_key)
        if not path_value or not expected:
            reasons.append(f"manifest lacks {path_key}/{hash_key}")
        elif sha256(Path(path_value)) != expected:
            reasons.append(f"manifest hash mismatch: {path_key}")


def require_manifest_executable(
    root: Path, manifest: dict[str, str], reasons: list[str]
) -> None:
    path_value, expected = manifest.get("gem5_path"), manifest.get(
        "gem5_sha256"
    )
    if not path_value or not expected:
        reasons.append("manifest lacks gem5_path/gem5_sha256")
        return
    if sha256(Path(path_value)) == expected:
        return
    recovery = key_values(root / "runtime_gem5_recovery.manifest")
    archived = recovery.get("archived_gem5_path")
    if (
        recovery.get("schema") != "dx100.runtime_executable_recovery.v1"
        or recovery.get("reason")
        != "lead_build_path_replaced_after_process_start"
        or recovery.get("live_exe_sha256") != expected
        or recovery.get("archived_gem5_sha256") != expected
        or recovery.get("simulation_state_changed") != "false"
        or not archived
        or sha256(Path(archived)) != expected
    ):
        reasons.append("manifest hash mismatch: gem5_path")


def running(root: Path) -> bool:
    return (text(root / "RUNNING.status") or "").strip() == "running"


def first_roi_ticks(stats: Path, reasons: list[str]) -> int | None:
    data = text(stats)
    if not data:
        reasons.append("missing or unreadable run/stats.txt")
        return None
    begin, end = data.find(STATS_BEGIN), data.find(STATS_END)
    if begin < 0 or end < begin:
        reasons.append("malformed first statistics window")
        return None
    matches = re.findall(r"^simTicks\s+([0-9]+)\b", data[begin:end], re.M)
    if len(matches) != 1 or int(matches[0]) <= 0:
        reasons.append("first statistics window lacks one positive simTicks")
        return None
    return int(matches[0])


def first_window_stat_sum(
    stats: Path, suffix: str, reasons: list[str]
) -> int | None:
    data = text(stats)
    if not data:
        reasons.append("missing or unreadable run/stats.txt")
        return None
    begin, end = data.find(STATS_BEGIN), data.find(STATS_END)
    if begin < 0 or end < begin:
        reasons.append("malformed first statistics window")
        return None
    matches = re.findall(
        rf"^\S*_{re.escape(suffix)}\s+([0-9]+)\b", data[begin:end], re.M
    )
    if not matches:
        reasons.append(f"first statistics window lacks *_{suffix}")
        return None
    return sum(map(int, matches))


def common(root: Path, log_name: str, reasons: list[str]) -> str | None:
    log = text(root / log_name)
    if not log:
        reasons.append(f"missing or unreadable {log_name}")
        return None
    if FATAL.search(log):
        reasons.append("simulator fatal evidence")
    if len(EXIT.findall(log)) != 1:
        reasons.append("requires exactly one m5_exit marker")
    return log


def classify_cg(root: Path) -> dict:
    reasons: list[str] = []
    log = common(root, "run/restore.log", reasons)
    result = text(root / "result.txt")
    require_exact_file(root / "run/restore.exit", "0", reasons)
    if (
        not result
        or "terminal=true\n" not in result
        or "correct=true\n" not in result
    ):
        reasons.append("missing CG terminal/correct result certificate")
    if not (root / "gate.complete").is_file():
        reasons.append("missing CG gate.complete")
    if (
        not log
        or len(re.findall(r"^CG_FINGERPRINT .* result=PASS$", log, re.M)) != 1
    ):
        reasons.append(
            "wrong CG fingerprint result"
            if log and "CG_FINGERPRINT" in log
            else "requires one passing CG fingerprint"
        )
    if (
        not log
        or len(
            re.findall(
                r"^CG_LOGICAL16_RMW_TERMINAL .* result=PASS$", log, re.M
            )
        )
        != 1
    ):
        reasons.append("requires one passing CG terminal")
    if not log or log.count("ROI End!!!") != 1:
        reasons.append("requires one CG ROI marker")
    manifest = key_values(root / "manifest.txt")
    for key, expected in (
        ("schema", "dx100.cg.physical_page_product_soa_jit.v2"),
        ("arm", "hybrid_only"),
        ("native_reruns", "0"),
        ("logical_elements", "16384"),
        ("physical_tile_elements", "4096"),
        ("hidden_logical_payload_bytes", "0"),
        ("host_payload_access", "0"),
    ):
        if manifest.get(key) != expected:
            reasons.append(f"CG manifest {key} is not {expected}")
    for resolved in (
        "num_tile_elements=16384",
        "physical_tile_elements=4096",
        "num_offset_table_entries=16384",
        "num_offset_table_epoch_entries=16384",
        "num_initial_row_table_slices=32",
    ):
        if resolved not in (text(root / "run/config.ini") or "").splitlines():
            reasons.append(f"CG config lacks {resolved}")
    verify_sha256_ledger(root / "result_sha256.txt", reasons)
    return result_for("cg", root, reasons)


def classify_is(root: Path) -> dict:
    reasons: list[str] = []
    log = common(root, "run/restore.log", reasons)
    require_exact_file(root / "run/restore.exit", "0", reasons)
    require_exact_file(root / "terminal.status", "PASS", reasons)
    if not log or log.count("ROI End!!!") != 1:
        reasons.append("requires one IS ROI marker")
    if not log or log.count("successfull: passed verification 6") != 1:
        reasons.append("requires exact NAS IS verification")
    if (
        not log
        or len(
            re.findall(
                r"^IS_SCALAR_SOA_JIT_TERMINAL .*result=PASS$", log, re.M
            )
        )
        != 1
    ):
        reasons.append("requires one passing IS terminal")
    if (
        not log
        or log.count(
            "IS_SCALAR_SOA_JIT_SELECTION compiled=1 treatment=scalar_soa_jit "
            "legacy_default=0"
        )
        != 1
    ):
        reasons.append("requires the scalar-SoA IS selection marker")
    manifest = key_values(root / "manifest.txt")
    require_manifest_hashes(
        manifest,
        (
            ("source_path", "source_sha256"),
            ("guest_path", "guest_sha256"),
            ("input_path", "input_sha256"),
        ),
        reasons,
    )
    require_manifest_executable(root, manifest, reasons)
    for key, expected in (
        ("action", "full"),
        ("logical_elements", "16384"),
        ("physical_tile_elements", "4096"),
        ("native_runs", "0"),
    ):
        if manifest.get(key) != expected:
            reasons.append(f"IS manifest {key} is not {expected}")
    result = text(root / "result.tsv") or ""
    if not re.search(
        r"^full\t[1-9][0-9]*\t2048\t2048\t33554432\t0\t", result, re.M
    ):
        reasons.append("requires the exact full IS result row")
    return result_for("is", root, reasons)


def classify_hashjoin(root: Path, kernel: str) -> dict:
    reasons: list[str] = []
    arm = root / kernel
    log = common(arm, "run/run.log", reasons)
    rows = text(root / "results.tsv")
    if rows:
        found = [
            line.split("\t")
            for line in rows.splitlines()[1:]
            if line.startswith(kernel + "\t")
        ]
        if found and (
            len(found) != 1 or len(found[0]) != 6 or found[0][1] != "2000000"
        ):
            reasons.append(f"malformed present {kernel} result row")
    if not log or log.count("HASHJOIN_HYBRID_RESULT result=2000000") != 1:
        reasons.append("requires exact HashJoin cardinality")
    markers = re.findall(r"^HASHJOIN_HYBRID_SOA_JIT .+$", log or "", re.M)
    if len(markers) != 1:
        reasons.append(f"requires one {kernel} hybrid mechanism marker")
        values: dict[str, str] = {}
    else:
        values = marker_values(markers[0])
        for key, expected in (
            ("enabled", "1"),
            ("physical_spd_elements", "4096"),
            ("logical_reorder_elements", "16384"),
            ("row_table_slices", "32"),
            ("indirect_units", "4"),
            ("candidate_only", "1"),
        ):
            if values.get(key) != expected:
                reasons.append(f"HashJoin marker {key} is not {expected}")
        try:
            first_eligible = int(values["first_eligible"])
            first_routed = int(values["first_routed"])
            second_eligible = int(values["second_eligible"])
            second_routed = int(values["second_routed"])
            eligible = int(values["eligible"])
            routed = int(values["routed"])
            if first_eligible <= 0 or first_routed != first_eligible:
                reasons.append("HashJoin first pass is not fully routed")
            if second_routed != second_eligible:
                reasons.append("HashJoin second pass is not fully routed")
            if kernel == "PRO" and (
                second_eligible != 0 or second_routed != 0
            ):
                reasons.append("PRO unexpectedly routed a shifted pass")
            coverage = key_values(arm / "mechanism.status")
            expected_coverage = (
                "not_applicable"
                if kernel == "PRO"
                else "tail_only"
                if second_eligible == 0
                else "routed"
            )
            if coverage.get("kernel") != kernel:
                reasons.append(
                    "HashJoin mechanism status lacks matching kernel"
                )
            if coverage.get("first_pass_coverage") != "routed":
                reasons.append("HashJoin first-pass coverage is not routed")
            if coverage.get("shifted_pass_coverage") != expected_coverage:
                reasons.append(
                    "HashJoin shifted-pass coverage disagrees with marker"
                )
            if coverage.get("second_eligible") != str(second_eligible) or (
                coverage.get("second_routed") != str(second_routed)
            ):
                reasons.append("HashJoin mechanism status does not close")
            if (
                routed <= 0
                or routed != eligible
                or routed != (first_routed + second_routed)
            ):
                reasons.append("HashJoin aggregate routing does not close")
        except (KeyError, ValueError):
            reasons.append("HashJoin marker has malformed routing fields")

    config = (text(arm / "run/config.ini") or "").splitlines()
    for resolved in (
        "num_tile_elements=16384",
        "physical_tile_elements=4096",
        "num_offset_table_entries=16384",
        "num_offset_table_epoch_entries=16384",
        "num_initial_row_table_slices=32",
    ):
        if resolved not in config:
            reasons.append(f"HashJoin config lacks {resolved}")

    stats = arm / "run/stats.txt"
    ledger_names = (
        "IND_SoaJitInstructions",
        "IND_SoaJitTerminalCompletions",
        "IND_SoaJitSelected",
        "IND_SoaJitPredicateRejected",
        "IND_SoaJitValueReadIssues",
        "IND_SoaJitValueReadResponses",
        "IND_SoaJitAliasesApplied",
        "IND_BoundedGlobalMergeFallbacks",
        "IND_SoaJitAReadIssues",
        "IND_SoaJitAReadResponses",
        "IND_SoaJitAWriteIssues",
        "IND_SoaJitAWriteResponses",
    )
    ledgers = {
        name: first_window_stat_sum(stats, name, reasons)
        for name in ledger_names
    }
    if values and all(value is not None for value in ledgers.values()):
        routed = int(values["routed"])
        if ledgers["IND_SoaJitInstructions"] != routed:
            reasons.append(
                "HashJoin instruction count differs from routed windows"
            )
        if ledgers["IND_SoaJitTerminalCompletions"] != routed:
            reasons.append(
                "HashJoin terminal count differs from routed windows"
            )
        if ledgers["IND_SoaJitSelected"] != routed * 16384:
            reasons.append("HashJoin selected-word count is not routed*16384")
        for name in (
            "IND_SoaJitPredicateRejected",
            "IND_SoaJitValueReadIssues",
            "IND_SoaJitValueReadResponses",
            "IND_BoundedGlobalMergeFallbacks",
        ):
            if ledgers[name] != 0:
                reasons.append(f"HashJoin {name} is not zero")
        selected = ledgers["IND_SoaJitSelected"]
        if ledgers["IND_SoaJitAliasesApplied"] != selected:
            reasons.append("HashJoin alias count differs from selected words")
        traffic = [
            ledgers["IND_SoaJitAReadIssues"],
            ledgers["IND_SoaJitAReadResponses"],
            ledgers["IND_SoaJitAWriteIssues"],
            ledgers["IND_SoaJitAWriteResponses"],
        ]
        if traffic[0] <= 0 or len(set(traffic)) != 1:
            reasons.append("HashJoin A read/write ledgers do not close")
    result = result_for(
        f"hashjoin-{kernel.lower()}", arm, reasons, display_root=root
    )
    if result["status"] == "terminal-valid":
        coverage = key_values(arm / "mechanism.status")
        shifted = coverage["shifted_pass_coverage"]
        result["exact_terminal_correctness"] = "pass"
        result["intended_mechanism_coverage"] = {
            "first_pass": "routed",
            "shifted_pass": shifted,
        }
        # This runner is candidate-only and deliberately has no matched
        # baseline.  A terminal result can never alone promote performance.
        result["performance_promotable"] = False
        result["performance_reason"] = (
            "candidate-only evidence has no matched performance baseline"
            if shifted == "routed"
            else "candidate-only evidence and no routed shifted-pass window"
        )
    else:
        result["exact_terminal_correctness"] = "not-established"
        result["intended_mechanism_coverage"] = "not-established"
        result["performance_promotable"] = False
    return result


def classify_sssp(root: Path) -> dict:
    reasons: list[str] = []
    log = common(root, "run/restore.log", reasons)
    for name in ("checkpoint.exit", "run/restore.exit"):
        if (text(root / name) or "").strip() != "0":
            reasons.append(f"{name} is not explicit zero")
    wrapper = key_values(root / "wrapper.status")
    if wrapper.get("exit_code") != "0":
        reasons.append("SSSP wrapper exit is not explicit zero")
    require_exact_file(root / "gate.complete", "PASS", reasons)
    result = key_values(root / "result.txt")
    if result.get("validation") != "PASS":
        reasons.append("missing SSSP passed wrapper/gate evidence")
    if (
        not log
        or len(re.findall(r"^SSSP_FINGERPRINT .* result=PASS$", log, re.M))
        != 1
    ):
        reasons.append("requires one passing SSSP fingerprint")
    if (
        not log
        or len(
            re.findall(
                r"^SSSP_OLD_RESULT_HYBRID_TERMINAL .*counts_close=1", log, re.M
            )
        )
        != 1
    ):
        reasons.append("requires closed SSSP old-result terminal")
    if not log or log.count("ROI End!!!") != 1:
        reasons.append("requires one SSSP ROI marker")
    stats = text(root / "run/stats.txt") or ""
    if stats.count(STATS_BEGIN) != 2 or stats.count(STATS_END) != 2:
        reasons.append("SSSP requires exactly two complete statistics windows")
    manifest = key_values(root / "candidate.manifest")
    require_manifest_hashes(
        manifest,
        (
            ("gem5_path", "gem5_sha256"),
            ("ramulator_library_path", "ramulator_library_sha256"),
            ("candidate_guest_path", "candidate_guest_sha256"),
            ("candidate_input_path", "candidate_input_sha256"),
        ),
        reasons,
    )
    for key, expected in (
        ("logical_elements", "16384"),
        ("physical_tile_elements", "4096"),
        ("offset_table_entries", "16384"),
        ("offset_table_epoch_entries", "16384"),
        ("row_table_slices", "32"),
        ("native_arms", "0"),
        ("full_graph", "true"),
        ("trace", "false"),
    ):
        if manifest.get(key) != expected:
            reasons.append(f"SSSP manifest {key} is not {expected}")
    before = text(root / "provenance/checkpoint.before.identity.sha256")
    after = text(root / "provenance/checkpoint.after.identity.sha256")
    if not before or before != after:
        reasons.append("SSSP checkpoint identity changed during restore")
    before_artifacts = root / "provenance/artifacts.before.sha256"
    after_artifacts = root / "provenance/artifacts.after.sha256"
    if text(before_artifacts) != text(after_artifacts):
        reasons.append("SSSP artifact hash ledger changed during restore")
    verify_sha256_ledger(after_artifacts, reasons)
    return result_for("sssp", root, reasons)


def result_for(
    workload: str,
    root: Path,
    reasons: list[str],
    *,
    display_root: Path | None = None,
) -> dict:
    # Performance evidence is deliberately unread until the workload-specific
    # terminal and correctness checks above have all passed.
    ticks = (
        first_roi_ticks(root / "run" / "stats.txt", reasons)
        if not reasons
        else None
    )
    correctness_failure = any(
        "fatal" in reason
        or reason.startswith("wrong ")
        or "hash mismatch" in reason
        or "unexpectedly routed" in reason
        for reason in reasons
    )
    status = "correctness-failed" if correctness_failure else "incomplete"
    if not reasons:
        status = "terminal-valid"
    elif not correctness_failure and running(root):
        status = "running"
    return {
        "workload": workload,
        "root": str(display_root or root),
        "status": status,
        "first_roi_simTicks": ticks if status == "terminal-valid" else None,
        "reasons": reasons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cg", type=Path)
    parser.add_argument("--is", dest="is_root", type=Path)
    parser.add_argument("--hashjoin-pro", type=Path)
    parser.add_argument("--hashjoin-prh", type=Path)
    parser.add_argument("--sssp", type=Path)
    parser.add_argument(
        "--baseline",
        type=Path,
        help="optional frozen JSON metadata; used only for displayed comparisons",
    )
    args = parser.parse_args()
    supplied: list[tuple[Path | None, Callable[[Path], dict]]] = [
        (args.cg, classify_cg),
        (args.is_root, classify_is),
        (args.hashjoin_pro, lambda root: classify_hashjoin(root, "PRO")),
        (args.hashjoin_prh, lambda root: classify_hashjoin(root, "PRH")),
        (args.sssp, classify_sssp),
    ]
    if not any(root for root, _ in supplied):
        parser.error("supply at least one explicit workload root")
    records = [fn(root) for root, fn in supplied if root]
    output = {
        "schema": "dx100.hybrid.full.classification.v1",
        "one_shot": True,
        "results": records,
    }
    if args.baseline:
        try:
            output["frozen_baseline_metadata"] = json.loads(
                args.baseline.read_text()
            )
        except (OSError, json.JSONDecodeError) as error:
            parser.error(f"invalid --baseline: {error}")
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
