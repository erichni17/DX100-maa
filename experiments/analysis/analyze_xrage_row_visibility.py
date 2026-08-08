#!/usr/bin/env python3
"""Fail-closed analysis for the matched XRAGE row64/row128 diagnostic."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Iterable

from analyze_reorder_survival import AuditError
from analyze_reorder_survival import analyze as analyze_reorder

EXPECTED_ELEMENTS = 2_097_152
EXPECTED_HASH = 11014995430510232451
EXPECTED_INDIRECT_INSTRUCTIONS = 128
EXPECTED_SOURCE = "f60a5b8da5cbb1a355dbca99b1cb721b3980953a"
EXPECTED_ROWS = {"row64": 64, "row128": 128}
EXPECTED_DEBUG_FLAGS = {"MAAIssueDigest", "MAAReorderTrace"}
ISSUE_RE = re.compile(
    r"unit=(?P<unit>\d+) instruction_tick=(?P<tick>\d+) "
    r"count=(?P<count>\d+) fnv=0x(?P<fnv>[0-9a-fA-F]{16}) "
    r"mix=0x(?P<mix>[0-9a-fA-F]{16})"
)
DRAM_RE = re.compile(
    r"CH(?P<channel>\d+)_num_(?P<kind>RD|WR|ACT|PRE)_commands_T:\s+"
    r"(?P<value>\d+)"
)
PASS_RE = re.compile(
    rf"^MAA_GATHER_VERIFY_PASS length={EXPECTED_ELEMENTS} hash={EXPECTED_HASH}$",
    re.MULTILINE,
)
M5_EXIT_RE = re.compile(
    r"^Exiting @ tick \d+ because m5_exit instruction encountered$",
    re.MULTILINE,
)


class VisibilityError(ValueError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_kv(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line_no, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not raw or "=" not in raw:
            raise VisibilityError(
                f"{path}:{line_no}: malformed key/value line"
            )
        key, value = raw.split("=", 1)
        if not key or key in result:
            raise VisibilityError(f"{path}:{line_no}: duplicate/empty key")
        result[key] = value
    return result


def first_stats_block(path: Path) -> tuple[dict[str, str], int]:
    text = path.read_text(encoding="utf-8")
    blocks = text.count("---------- Begin Simulation Statistics ----------")
    if blocks != 2:
        raise VisibilityError(
            f"{path}: expected exactly two stats blocks, got {blocks}"
        )
    active = False
    values: dict[str, str] = {}
    for raw in text.splitlines():
        if raw.startswith("---------- Begin Simulation Statistics"):
            if active:
                raise VisibilityError(f"{path}: nested stats block")
            active = True
            continue
        if raw.startswith("---------- End Simulation Statistics") and active:
            break
        if active:
            fields = raw.split()
            if len(fields) >= 2:
                values[fields[0]] = fields[1]
    if not values:
        raise VisibilityError(f"{path}: empty first stats block")
    return values, blocks


def exact_int(value: str, context: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise VisibilityError(
            f"{context}: expected integer, got {value!r}"
        ) from exc
    if number < 0:
        raise VisibilityError(f"{context}: negative value")
    return number


def percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def distribution(values: Iterable[int]) -> dict[str, object]:
    samples = list(values)
    if not samples:
        raise VisibilityError("cannot summarize an empty distribution")
    counts = Counter(samples)
    return {
        "count": len(samples),
        "sum": sum(samples),
        "min": min(samples),
        "max": max(samples),
        "mean": statistics.fmean(samples),
        "median": statistics.median(samples),
        "p25_nearest_rank": percentile(samples, 0.25),
        "p50_nearest_rank": percentile(samples, 0.50),
        "p75_nearest_rank": percentile(samples, 0.75),
        "p90_nearest_rank": percentile(samples, 0.90),
        "p95_nearest_rank": percentile(samples, 0.95),
        "p99_nearest_rank": percentile(samples, 0.99),
        "histogram": {str(key): counts[key] for key in sorted(counts)},
    }


def issue_records(trace: Path) -> dict[tuple[int, int], dict[str, object]]:
    records: dict[tuple[int, int], dict[str, object]] = {}
    for line_no, raw in enumerate(
        trace.read_text(encoding="utf-8").splitlines(), 1
    ):
        match = ISSUE_RE.search(raw)
        if match is None:
            continue
        key = (int(match.group("unit")), int(match.group("tick")))
        if key in records:
            raise VisibilityError(
                f"{trace}:{line_no}: duplicate issue digest {key}"
            )
        records[key] = {
            "unit": key[0],
            "operation_tick": key[1],
            "issued_a_lines": int(match.group("count")),
            "fnv": f"0x{match.group('fnv').lower()}",
            "mix": f"0x{match.group('mix').lower()}",
        }
    if not records:
        raise VisibilityError(f"{trace}: no MAAIssueDigest records")
    return records


def reconcile_instruction_records(
    trace: Path, audit: dict[str, object]
) -> list[dict[str, object]]:
    issues = issue_records(trace)
    instructions = audit.get("instructions")
    if not isinstance(instructions, list):
        raise VisibilityError(
            f"{trace}: invalid reorder audit instruction list"
        )
    reconciled: list[dict[str, object]] = []
    seen: set[tuple[int, int]] = set()
    for instruction in instructions:
        if not isinstance(instruction, dict):
            raise VisibilityError(f"{trace}: malformed reorder instruction")
        key = (int(instruction["unit"]), int(instruction["operation_tick"]))
        if key in seen:
            raise VisibilityError(
                f"{trace}: duplicate reorder instruction {key}"
            )
        seen.add(key)
        issue = issues.get(key)
        if issue is None:
            raise VisibilityError(f"{trace}: missing issue digest for {key}")
        if int(instruction["total_issued_lines"]) != issue["issued_a_lines"]:
            raise VisibilityError(
                f"{trace}: issued-line mismatch for {key}: "
                f"reorder={instruction['total_issued_lines']} "
                f"digest={issue['issued_a_lines']}"
            )
        reconciled.append({**instruction, "issue_digest": issue})
    extra = sorted(set(issues) - seen)
    if extra:
        raise VisibilityError(
            f"{trace}: issue digests without reorder records: {extra}"
        )
    return reconciled


def dram_commands(log_text: str) -> dict[str, object]:
    per_channel: dict[int, dict[str, int]] = {}
    for match in DRAM_RE.finditer(log_text):
        channel = int(match.group("channel"))
        per_channel.setdefault(channel, {})[match.group("kind")] = int(
            match.group("value")
        )
    if not per_channel or any(
        set(row) != {"RD", "WR", "ACT", "PRE"} for row in per_channel.values()
    ):
        raise VisibilityError("incomplete final DRAM command totals")
    totals = {
        kind.lower(): sum(row[kind] for row in per_channel.values())
        for kind in ("RD", "WR", "ACT", "PRE")
    }
    return {
        "per_channel": {
            str(key): per_channel[key] for key in sorted(per_channel)
        },
        "aggregate": totals,
    }


def artifact_hash(artifact_file: Path, wanted: Path) -> str:
    resolved = wanted.resolve()
    matches: list[str] = []
    for raw in artifact_file.read_text(encoding="utf-8").splitlines():
        fields = raw.split(maxsplit=1)
        if len(fields) == 2 and Path(fields[1].strip()).resolve() == resolved:
            matches.append(fields[0])
    if len(matches) != 1:
        raise VisibilityError(
            f"{artifact_file}: expected one hash for {resolved}, got {len(matches)}"
        )
    if matches[0] != sha256(resolved):
        raise VisibilityError(f"{artifact_file}: stale hash for {resolved}")
    return matches[0]


def analyze_rep(
    rep: Path, label: str, frozen: dict[str, object]
) -> dict[str, object]:
    manifest = read_kv(rep / "manifest.txt")
    rows = EXPECTED_ROWS[label]
    expected_manifest = {
        "source_commit": EXPECTED_SOURCE,
        "arm": "direct_index_4k",
        "guest_arm": "direct4",
        "physical_tile_elements": "4096",
        "maa_logical_tile_elements": "16384",
        "virtual_grow_order": "1",
        "virtual_native_issue_order": "0",
        "virtual_index_buffer_lines": "128",
        "virtual_index_force_cache": "1",
        "virtual_index_partitions": "1",
        "virtual_index_filter_words_per_cycle": "0",
        "virtual_partition_keep_combiner": "0",
        "retirement_cache_size": "1kB",
        "initial_row_table_slices": "16",
        "row_table_rows_per_slice": str(rows),
        "offset_table_entries": "16384",
        "offset_table_epoch_entries": "16384",
        "num_indirect_units_per_maa": "1",
        "debug_flags": "MAAReorderTrace,MAAIssueDigest",
    }
    for key, expected in expected_manifest.items():
        if manifest.get(key) != expected:
            raise VisibilityError(
                f"{rep}: manifest {key}={manifest.get(key)!r}, expected {expected!r}"
            )
    if set(manifest["debug_flags"].split(",")) != EXPECTED_DEBUG_FLAGS:
        raise VisibilityError(f"{rep}: wrong debug flags")

    log_path = rep / "restore.log"
    stats_path = rep / "run" / "stats.txt"
    trace_path = rep / "run" / "xrage-debug.log"
    log_text = log_path.read_text(encoding="utf-8")
    if (
        len(PASS_RE.findall(log_text)) != 1
        or len(M5_EXIT_RE.findall(log_text)) != 1
    ):
        raise VisibilityError(
            f"{rep}: invalid exact-output or terminal markers"
        )
    if re.search(
        r"panic|fatal|segmentation fault|MAA_GATHER_VERIFY_FAIL",
        log_text,
        re.I,
    ):
        raise VisibilityError(f"{rep}: fatal marker in restore log")
    if (rep / "restore.exit").read_text(encoding="utf-8").strip() != "0":
        raise VisibilityError(f"{rep}: nonzero restore exit")

    stats, stats_blocks = first_stats_block(stats_path)
    sim_ticks = exact_int(stats.get("simTicks", ""), f"{stats_path}:simTicks")
    indirect_count = exact_int(
        stats.get("system.maa.numInst_INDRD", ""),
        f"{stats_path}:system.maa.numInst_INDRD",
    )
    if indirect_count != EXPECTED_INDIRECT_INSTRUCTIONS:
        raise VisibilityError(
            f"{rep}: expected {EXPECTED_INDIRECT_INSTRUCTIONS} indirect instructions, "
            f"got {indirect_count}"
        )
    per_unit_cycles: dict[str, dict[str, int]] = {}
    for name, value in stats.items():
        match = re.fullmatch(
            r"system\.maa\.I(\d+)_IND_Cycles(Fill|Request)", name
        )
        if match:
            per_unit_cycles.setdefault(match.group(1), {})[
                match.group(2).lower()
            ] = exact_int(value, f"{stats_path}:{name}")
    if not per_unit_cycles or any(
        set(row) != {"fill", "request"} for row in per_unit_cycles.values()
    ):
        raise VisibilityError(f"{rep}: incomplete Fill/Request cycle counters")

    try:
        audit = analyze_reorder(trace_path)
    except AuditError as exc:
        raise VisibilityError(f"{rep}: reorder audit failed: {exc}") from exc
    instructions = reconcile_instruction_records(trace_path, audit)
    if len(instructions) != indirect_count:
        raise VisibilityError(
            f"{rep}: reconciled {len(instructions)} instructions, stats report {indirect_count}"
        )

    artifacts = rep / "artifact_sha256.txt"
    identities = {
        name: artifact_hash(artifacts, Path(str(record["path"])))
        for name, record in frozen.items()
        if name in {"gem5", "workload", "input"}
    }
    checkpoint_identity = sha256(rep / "checkpoint_sha256.txt")

    fields = {
        "max_joint_admissions": [
            int(row["max_joint_admissions"]) for row in instructions
        ],
        "rt_full_drains": [int(row["rt_full_drains"]) for row in instructions],
        "issued_a_lines": [
            int(row["total_issued_lines"]) for row in instructions
        ],
        "row_transitions": [
            int(row["row_transitions"]) for row in instructions
        ],
    }
    return {
        "path": str(rep.resolve()),
        "rows_per_slice": rows,
        "output": {"elements": EXPECTED_ELEMENTS, "hash": EXPECTED_HASH},
        "terminal_m5_exit": True,
        "stats_blocks": stats_blocks,
        "roi_simTicks": sim_ticks,
        "indirect_instruction_count": indirect_count,
        "all_indirect_instructions_reconciled": True,
        "identity_hashes": identities,
        "checkpoint_manifest_sha256": checkpoint_identity,
        "trace_sha256": sha256(trace_path),
        "reorder_audit": audit,
        "issue_digest_count": len(instructions),
        "distributions": {
            key: distribution(value) for key, value in fields.items()
        },
        "dram_commands": dram_commands(log_text),
        "cycles": {
            "per_indirect_unit": per_unit_cycles,
            "fill_aggregate": sum(
                row["fill"] for row in per_unit_cycles.values()
            ),
            "request_aggregate": sum(
                row["request"] for row in per_unit_cycles.values()
            ),
        },
    }


def semantic_storage_delta() -> dict[str, object]:
    slices = (2, 4, 8, 16)
    columns = (64, 32, 16, 8)
    added_rows = sum(slices) * (128 - 64)
    added_entries = sum(
        count * (128 - 64) * width for count, width in zip(slices, columns)
    )
    row_bytes = 14
    entry_bytes = 18
    active_added_rows = 16 * (128 - 64)
    active_added_entries = 16 * (128 - 64) * 8
    return {
        "scope": "one MAA, one configured indirect unit; all four allocated RowTable organizations",
        "added_rows": added_rows,
        "added_entry_slots": added_entries,
        "semantic_bytes_per_row": row_bytes,
        "semantic_bytes_per_entry_slot": entry_bytes,
        "semantic_core_array_delta_bytes": added_rows * row_bytes
        + added_entries * entry_bytes,
        "semantic_core_array_delta_bits": 8
        * (added_rows * row_bytes + added_entries * entry_bytes),
        "active_16_slice_organization_delta_bytes": active_added_rows
        * row_bytes
        + active_added_entries * entry_bytes,
        "caveat": "Semantic core-array accounting only; excludes C++ padding/allocator overhead and is not synthesized area.",
    }


def analyze_root(root: Path) -> dict[str, object]:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "dx100.xrage_row_visibility_run.v1":
        raise VisibilityError("invalid or missing run manifest schema")
    if manifest.get("simulator_source_commit") != EXPECTED_SOURCE:
        raise VisibilityError("simulator source is not exact f60a5b8d")
    if manifest.get("execution") != "serialized":
        raise VisibilityError("matched arms were not explicitly serialized")
    frozen = manifest.get("frozen_artifacts")
    if not isinstance(frozen, dict):
        raise VisibilityError("missing frozen artifact identities")
    for name in ("gem5", "workload", "input", "ramulator"):
        record = frozen.get(name)
        if not isinstance(record, dict) or sha256(
            Path(str(record.get("path")))
        ) != record.get("sha256"):
            raise VisibilityError(f"frozen {name} identity changed")

    arms: dict[str, list[dict[str, object]]] = {}
    for label in ("row64", "row128"):
        arm_root = root / label
        reps = sorted(
            (path for path in arm_root.glob("rep*") if path.is_dir()),
            key=lambda path: int(path.name.removeprefix("rep")),
        )
        if not reps or [path.name for path in reps] != [
            f"rep{i}" for i in range(1, len(reps) + 1)
        ]:
            raise VisibilityError(
                f"{label}: missing/noncontiguous repetitions"
            )
        arms[label] = [analyze_rep(rep, label, frozen) for rep in reps]

    all_reps = [rep for reps in arms.values() for rep in reps]
    for identity in ("gem5", "workload", "input"):
        hashes = {str(rep["identity_hashes"][identity]) for rep in all_reps}
        if len(hashes) != 1:
            raise VisibilityError(
                f"{identity}: repetitions did not share one hash"
            )
    checkpoint_hashes = {
        str(rep["checkpoint_manifest_sha256"]) for rep in all_reps
    }
    if len(checkpoint_hashes) != 1:
        raise VisibilityError(
            "repetitions did not share one checkpoint identity"
        )

    ticks = {
        label: [int(rep["roi_simTicks"]) for rep in reps]
        for label, reps in arms.items()
    }
    row64_median = statistics.median(ticks["row64"])
    row128_median = statistics.median(ticks["row128"])
    delta_pct = 100.0 * (row128_median / row64_median - 1.0)
    low_delta = abs(delta_pct) < 2.0
    result_files_identical = {
        label: len(reps) >= 2
        and len(
            {
                sha256(root / label / f"rep{i}" / "result.tsv")
                for i in range(1, len(reps) + 1)
            }
        )
        == 1
        for label, reps in arms.items()
    }
    tick_identical = {
        label: len(set(values)) == 1 for label, values in ticks.items()
    }
    deterministic_replay = all(result_files_identical.values()) and all(
        tick_identical.values()
    )
    if low_delta:
        if len(arms["row64"]) < 2 or len(arms["row128"]) < 2:
            replication_status = "NEEDS_REPLAY_CHECK"
        elif deterministic_replay:
            replication_status = "STOPPED_AFTER_IDENTICAL_REPLAY"
        elif len(arms["row64"]) == 3 and len(arms["row128"]) == 3:
            replication_status = "THREE_REPS_NONDETERMINISTIC"
        else:
            raise VisibilityError(
                "sub-2% non-identical replay requires three reps per arm"
            )
    else:
        replication_status = "ONE_REP_SUFFICIENT_AT_OR_ABOVE_2_PERCENT"

    comparison = {
        "row64_roi_simTicks_median": row64_median,
        "row128_roi_simTicks_median": row128_median,
        "row128_minus_row64_simTicks": row128_median - row64_median,
        "row128_delta_percent_vs_row64": delta_pct,
        "under_2_percent": low_delta,
        "repetition_counts": {
            label: len(reps) for label, reps in arms.items()
        },
        "result_tsv_byte_identical_within_arm": result_files_identical,
        "roi_simTicks_identical_within_arm": tick_identical,
        "deterministic_replay": deterministic_replay,
        "replication_status": replication_status,
    }
    return {
        "schema": "dx100.xrage_row_visibility_evidence.v1",
        "status": "PASS"
        if replication_status != "NEEDS_REPLAY_CHECK"
        else "INCOMPLETE",
        "raw_root": str(root.resolve()),
        "run_manifest_sha256": sha256(manifest_path),
        "shared_identity": {
            "simulator_source_commit": EXPECTED_SOURCE,
            "binary_input_checkpoint_match": True,
            "checkpoint_manifest_sha256": next(iter(checkpoint_hashes)),
            "execution": "serialized",
        },
        "arms": arms,
        "comparison": comparison,
        "semantic_rowtable_storage_delta": semantic_storage_delta(),
        "interpretation_guard": (
            "Issue counts, issue digests, and row transitions are descriptive mechanism evidence; "
            "they do not by themselves establish causality for simTicks."
        ),
        "row128_label": "high-cost diagnostic; never the baseline",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = analyze_root(args.root.resolve())
    except (
        AuditError,
        VisibilityError,
        OSError,
        UnicodeError,
        ValueError,
        KeyError,
    ) as exc:
        parser.error(str(exc))
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if result["status"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
