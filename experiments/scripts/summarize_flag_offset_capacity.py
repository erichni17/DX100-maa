#!/usr/bin/env python3
"""Compare FLAG full, Row-only, and bounded Row+Offset descriptor runs."""

import argparse
import csv
import json
import math
import re
from pathlib import Path

import summarize_flag_descriptor_capacity as descriptor


EXPECTED_SOURCE_COMMIT = "c5856ac30dc004348803828cbaa28485ec83b3d6"
OPTIONAL_FALSE_KEYS = {
    ("system.maa", "virtual_index_force_cache"),
    ("system.maa", "virtual_partition_keep_combiner"),
}
ALLOWED_ANALYSIS_DIRTY_PATHS = {
    "experiments/scripts/report_maa_storage.py",
    "experiments/tests/test_report_maa_storage.py",
}
DIFF_PATH_RE = re.compile(r"^diff --git a/(.+) b/(.+)$")


def fail(message: str) -> None:
    raise SystemExit(f"FLAG Offset-capacity comparison failed: {message}")


def normalized_config(path: Path) -> dict[tuple[str, str], str]:
    values = descriptor.normalized_maa_config(path)
    logical_key = ("system.maa", "num_tile_elements")
    offset_key = ("system.maa", "num_offset_table_entries")
    epoch_key = ("system.maa", "num_offset_table_epoch_entries")
    try:
        logical = int(values[logical_key])
        configured_offset = int(values.get(offset_key, "0"))
        configured_epoch = int(values.get(epoch_key, "0"))
    except (KeyError, ValueError):
        fail(f"invalid logical or Offset capacity in {path}")
    values[offset_key] = str(configured_offset or logical)
    values[epoch_key] = str(configured_epoch or int(values[offset_key]))
    for key in OPTIONAL_FALSE_KEYS:
        values[key] = values.get(key, "false")
    return values


def require_config_treatment(
    baseline: Path,
    candidate: Path,
    expected: list[tuple[str, str, str, str]],
) -> None:
    before = normalized_config(baseline)
    after = normalized_config(candidate)
    differences = []
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            differences.append((*key, before.get(key), after.get(key)))
    if differences != expected:
        fail(
            f"unexpected resolved configuration differences between {baseline} "
            f"and {candidate}: {differences}"
        )


def validate_result(case: Path) -> tuple[dict[str, str], int]:
    result_path = case / "result.tsv"
    result = descriptor.read_one_row_tsv(result_path)
    blocks, ticks, final_ticks = descriptor.stats_blocks_and_ticks(
        case / "run/stats.txt"
    )
    if (
        descriptor.integer(result, "stats_blocks", result_path) != blocks
        or descriptor.integer(result, "roi_simTicks", result_path) != ticks
        or descriptor.integer(result, "final_simTicks", result_path)
        != final_ticks
    ):
        fail(f"result table does not match stats in {case}")
    return result, ticks


def artifact_identity(case: Path, input_path: Path) -> tuple[str, str, str]:
    artifact_path = case / "artifact_sha256.txt"
    artifacts = descriptor.artifact_hashes(artifact_path)
    input_hash = descriptor.require_artifact_hash(
        artifacts, input_path, artifact_path
    )
    guest = descriptor.command_option(case / "restore.command", "--cmd")
    guest_hash = descriptor.require_artifact_hash(artifacts, guest, artifact_path)
    gem5 = [
        digest
        for path, digest in artifacts.items()
        if path.name in {"gem5.opt", "gem5.fast"}
    ]
    if len(gem5) != 1:
        fail(f"expected one simulator artifact in {artifact_path}")
    return input_hash, guest_hash, gem5[0]


def geometric_mean(values: list[float]) -> float:
    if not values or any(value <= 0 for value in values):
        fail("geometric mean requires positive observations")
    return math.exp(sum(math.log(value) for value in values) / len(values))


def write_tsv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0], delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def validate_source_snapshot(case: Path) -> list[str]:
    status_path = case / "source_status.txt"
    diff_path = case / "source.diff"
    if not status_path.is_file() or not diff_path.is_file():
        fail(f"missing source snapshot in {case}")
    dirty_paths = []
    for line in status_path.read_text(encoding="utf-8").splitlines():
        if len(line) < 4 or " -> " in line:
            fail(f"unsupported source-status record in {status_path}: {line!r}")
        dirty_paths.append(line[3:])
    diff_paths = set()
    for line in diff_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = DIFF_PATH_RE.fullmatch(line)
        if match is not None:
            if match.group(1) != match.group(2):
                fail(f"renamed source path in {diff_path}: {line}")
            diff_paths.add(match.group(1))
    dirty = set(dirty_paths) | diff_paths
    disallowed = dirty - ALLOWED_ANALYSIS_DIRTY_PATHS
    if disallowed:
        fail(f"execution-relevant or unknown dirty source in {case}: {sorted(disallowed)}")
    return sorted(dirty)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("row_only_campaign", type=Path)
    parser.add_argument("bounded_campaign", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    row_cases = descriptor.case_directories(
        args.row_only_campaign.resolve(), None
    )
    bounded_cases = descriptor.case_directories(
        args.bounded_campaign.resolve(), None
    )
    if set(row_cases) != set(bounded_cases):
        fail("Row-only and bounded campaign case IDs differ")

    rows: list[dict[str, object]] = []
    candidate_simulator_hash: str | None = None
    non_execution_dirty_paths: set[str] = set()
    for case_id in sorted(row_cases):
        row_case = row_cases[case_id]
        bounded_case = bounded_cases[case_id]
        row_manifest_path = row_case / "manifest.txt"
        bounded_manifest_path = bounded_case / "manifest.txt"
        row_manifest = descriptor.read_key_values(row_manifest_path)
        bounded_manifest = descriptor.read_key_values(bounded_manifest_path)
        full_case = Path(row_manifest.get("checkpoint_run", "")).resolve()
        if not full_case.is_dir():
            fail(f"missing full-descriptor checkpoint source for {case_id}")
        if Path(bounded_manifest.get("checkpoint_run", "")).resolve() != full_case:
            fail(f"bounded and Row-only runs use different checkpoints for {case_id}")

        descriptor.validate_terminal(full_case, ("xrage_attribution_smoke.pass",))
        descriptor.validate_terminal(row_case, ("xrage_checkpoint_recovery.pass",))
        descriptor.validate_terminal(
            bounded_case, ("xrage_checkpoint_recovery.pass",)
        )
        if bounded_manifest.get("source_commit") != EXPECTED_SOURCE_COMMIT:
            fail(f"unexpected bounded simulator source for {case_id}")
        if bounded_manifest.get("runner_source_commit") != EXPECTED_SOURCE_COMMIT:
            fail(f"unexpected bounded runner source for {case_id}")
        if bounded_manifest.get("checkpoint_retargeted") != "0":
            fail(f"bounded checkpoint was retargeted for {case_id}")
        for manifest, path in (
            (row_manifest, row_manifest_path),
            (bounded_manifest, bounded_manifest_path),
        ):
            if manifest.get("timeout") != "none":
                fail(f"a run used a wall-clock timeout in {path}")
        expected_bounded = {
            "arm": "direct_index_4k",
            "guest_arm": "direct4",
            "physical_tile_elements": "4096",
            "maa_logical_tile_elements": "16384",
            "workload_chunk_elements": "16384",
            "virtual_native_issue_order": "1",
            "virtual_index_buffer_lines": "128",
            "row_table_rows_per_slice": "16",
            "offset_table_entries": "4096",
            "virtual_response_slots": "128",
            "virtual_response_word_pool": "480",
            "virtual_combine_slots": "384",
            "virtual_combine_words": "4096",
            "virtual_combine_ways": "4",
            "debug_flags": "MAAIssueDigest",
        }
        descriptor.require_manifest_values(
            bounded_manifest, expected_bounded, bounded_manifest_path
        )

        require_config_treatment(
            full_case / "run/config.ini",
            row_case / "run/config.ini",
            [("system.maa", "num_row_table_rows_per_slice", "64", "16")],
        )
        require_config_treatment(
            row_case / "run/config.ini",
            bounded_case / "run/config.ini",
            [
                (
                    "system.maa",
                    "num_offset_table_entries",
                    "16384",
                    "4096",
                ),
                (
                    "system.maa",
                    "num_offset_table_epoch_entries",
                    "16384",
                    "4096",
                ),
            ],
        )

        full_result, full_ticks = validate_result(full_case)
        row_result, row_ticks = validate_result(row_case)
        bounded_result, bounded_ticks = validate_result(bounded_case)
        output_hashes = {
            full_result.get("output_hash"),
            row_result.get("output_hash"),
            bounded_result.get("output_hash"),
        }
        if len(output_hashes) != 1 or None in output_hashes:
            fail(f"exact output hashes differ for {case_id}")

        input_path = Path(bounded_manifest.get("input", "")).resolve()
        identities = [
            artifact_identity(case, input_path)
            for case in (full_case, row_case, bounded_case)
        ]
        if len({identity[0] for identity in identities}) != 1:
            fail(f"input hashes differ for {case_id}")
        if len({identity[1] for identity in identities}) != 1:
            fail(f"guest hashes differ for {case_id}")
        if candidate_simulator_hash is None:
            candidate_simulator_hash = identities[2][2]
        elif identities[2][2] != candidate_simulator_hash:
            fail(f"bounded simulator hashes differ for {case_id}")

        non_execution_dirty_paths.update(validate_source_snapshot(bounded_case))

        full_requests = descriptor.issue_digest_requests(
            full_case / "run/xrage-debug.log"
        )
        row_requests = descriptor.issue_digest_requests(
            row_case / "run/xrage-debug.log"
        )
        bounded_requests = descriptor.issue_digest_requests(
            bounded_case / "run/xrage-debug.log"
        )
        rows.append(
            {
                "id": case_id,
                "output_hash": next(iter(output_hashes)),
                "full_ticks": full_ticks,
                "row_only_ticks": row_ticks,
                "bounded_ticks": bounded_ticks,
                "row_only_vs_full": row_ticks / full_ticks,
                "bounded_vs_full": bounded_ticks / full_ticks,
                "bounded_vs_row_only": bounded_ticks / row_ticks,
                "full_source_requests": full_requests,
                "row_only_source_requests": row_requests,
                "bounded_source_requests": bounded_requests,
                "full_writes": descriptor.integer(
                    full_result,
                    "virtual_write_issues",
                    full_case / "result.tsv",
                ),
                "row_only_writes": descriptor.integer(
                    row_result,
                    "virtual_write_issues",
                    row_case / "result.tsv",
                ),
                "bounded_writes": descriptor.integer(
                    bounded_result,
                    "virtual_write_issues",
                    bounded_case / "result.tsv",
                ),
                "row_table_full_events": descriptor.integer(
                    bounded_result,
                    "row_table_full_events",
                    bounded_case / "result.tsv",
                ),
                "offset_table_full_events": descriptor.integer(
                    bounded_result,
                    "offset_table_full_events",
                    bounded_case / "result.tsv",
                ),
                "bounded_build_rounds": descriptor.integer(
                    bounded_result,
                    "virtual_build_rounds",
                    bounded_case / "result.tsv",
                ),
            }
        )

    summary = {
        "cases": len(rows),
        "row_only_vs_full_geomean": geometric_mean(
            [float(row["row_only_vs_full"]) for row in rows]
        ),
        "bounded_vs_full_geomean": geometric_mean(
            [float(row["bounded_vs_full"]) for row in rows]
        ),
        "bounded_vs_row_only_geomean": geometric_mean(
            [float(row["bounded_vs_row_only"]) for row in rows]
        ),
        "bounded_write_ratio_geomean": geometric_mean(
            [
                int(row["bounded_writes"]) / int(row["full_writes"])
                for row in rows
            ]
        ),
        "row_table_full_events_total": sum(
            int(row["row_table_full_events"]) for row in rows
        ),
        "offset_table_full_events_total": sum(
            int(row["offset_table_full_events"]) for row in rows
        ),
        "bounded_simulator_sha256": candidate_simulator_hash,
        "allowed_non_execution_dirty_paths": sorted(non_execution_dirty_paths),
    }

    output = args.output_dir.resolve()
    if output.exists():
        fail(f"refusing to overwrite output: {output}")
    output.mkdir(parents=True)
    write_tsv(output / "flag_offset_capacity.tsv", rows)
    report = {
        "row_only_campaign": str(args.row_only_campaign.resolve()),
        "bounded_campaign": str(args.bounded_campaign.resolve()),
        "config_treatments": [
            {
                "key": "system.maa.num_row_table_rows_per_slice",
                "full": 64,
                "row_only": 16,
            },
            {
                "key": "system.maa.num_offset_table_entries",
                "row_only": 16384,
                "bounded": 4096,
            },
            {
                "key": "system.maa.num_offset_table_epoch_entries",
                "row_only": 16384,
                "bounded": 4096,
            },
        ],
        "configurations": rows,
        "summary": summary,
    }
    (output / "flag_offset_capacity.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    markdown = [
        "# FLAG Offset Capacity",
        "",
        "All 14 configurations passed exact-output, terminal-exit, artifact, "
        "checkpoint, two-stat-block, and treatment-only configuration checks.",
        "",
        "| Configuration | Row-only vs full | Bounded vs full | Bounded vs Row-only | Bounded writes vs full | RT full | OT full |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        markdown.append(
            f"| {row['id']} | "
            f"{100 * (float(row['row_only_vs_full']) - 1):+.3f}% | "
            f"{100 * (float(row['bounded_vs_full']) - 1):+.3f}% | "
            f"{100 * (float(row['bounded_vs_row_only']) - 1):+.3f}% | "
            f"{100 * (int(row['bounded_writes']) / int(row['full_writes']) - 1):+.3f}% | "
            f"{int(row['row_table_full_events']):,} | "
            f"{int(row['offset_table_full_events']):,} |"
        )
    markdown.extend(
        [
            "",
            "## Equal-Weight Geometric Mean",
            "",
            "- Row-only versus full descriptors: "
            f"{100 * (summary['row_only_vs_full_geomean'] - 1):+.3f}%",
            "- Bounded Row+Offset versus full descriptors: "
            f"{100 * (summary['bounded_vs_full_geomean'] - 1):+.3f}%",
            "- Bounded Row+Offset versus Row-only: "
            f"{100 * (summary['bounded_vs_row_only_geomean'] - 1):+.3f}%",
            "- Bounded C-write issues versus full descriptors: "
            f"{100 * (summary['bounded_write_ratio_geomean'] - 1):+.3f}%",
            f"- Row-Table full events: {summary['row_table_full_events_total']:,}",
            f"- Offset-Table full events: {summary['offset_table_full_events_total']:,}",
            "",
            "The bounded design does not preserve one monolithic 16K reorder "
            "window. It creates reusable 4K descriptor epochs and drains an "
            "epoch before recycling its Offset slots.",
        ]
    )
    (output / "flag_offset_capacity.md").write_text(
        "\n".join(markdown) + "\n", encoding="utf-8"
    )
    (output / "flag_offset_capacity.pass").touch()
    print(
        "PASS FLAG Offset-capacity comparison: "
        f"{len(rows)} cases, bounded/full "
        f"{100 * (summary['bounded_vs_full_geomean'] - 1):+.3f}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
