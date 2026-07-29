#!/usr/bin/env python3
"""Attribute FLAG performance to Offset scheduling and storage separately."""

import argparse
import json
from pathlib import Path

import summarize_flag_descriptor_capacity as descriptor


ARMS = {
    "epoch16_cap16": (16384, 16384),
    "epoch4_cap16": (16384, 4096),
    "epoch4_cap4": (4096, 4096),
}
COMMON_MANIFEST = {
    "arm": "direct_index_4k",
    "guest_arm": "direct4",
    "physical_tile_elements": "4096",
    "maa_logical_tile_elements": "16384",
    "workload_chunk_elements": "16384",
    "virtual_native_issue_order": "1",
    "virtual_index_buffer_lines": "128",
    "row_table_rows_per_slice": "16",
    "virtual_response_slots": "128",
    "virtual_response_word_pool": "480",
    "virtual_combine_slots": "384",
    "virtual_combine_words": "4096",
    "virtual_combine_ways": "4",
    "debug_flags": "MAAIssueDigest",
    "timeout": "none",
}


def fail(message: str) -> None:
    raise SystemExit(f"FLAG Offset-epoch attribution failed: {message}")


def normalized_config(path: Path) -> dict[tuple[str, str], str]:
    values = descriptor.normalized_maa_config(path)
    logical_key = ("system.maa", "num_tile_elements")
    capacity_key = ("system.maa", "num_offset_table_entries")
    epoch_key = ("system.maa", "num_offset_table_epoch_entries")
    try:
        logical = int(values[logical_key])
        capacity = int(values.get(capacity_key, "0")) or logical
        epoch = int(values.get(epoch_key, "0")) or capacity
    except (KeyError, ValueError):
        fail(f"invalid Offset configuration in {path}")
    values[capacity_key] = str(capacity)
    values[epoch_key] = str(epoch)
    return values


def config_differences(
    before_path: Path, after_path: Path
) -> list[tuple[str, str, str | None, str | None]]:
    before = normalized_config(before_path)
    after = normalized_config(after_path)
    return [
        (*key, before.get(key), after.get(key))
        for key in sorted(set(before) | set(after))
        if before.get(key) != after.get(key)
    ]


def require_clean_source(case: Path) -> None:
    for name in ("source_status.txt", "source.diff"):
        path = case / name
        if not path.is_file():
            fail(f"missing source snapshot: {path}")
        if path.read_text(encoding="utf-8").strip():
            fail(f"dirty source snapshot in {path}")


def read_dram(case: Path) -> dict[str, int]:
    path = case / "dram_commands.tsv"
    row = descriptor.read_one_row_tsv(path)
    return {
        key: descriptor.integer(row, key, path)
        for key in ("dram_reads", "dram_activates", "dram_precharges")
    }


def validate_case(
    name: str,
    case: Path,
    expected_source: str,
    expected_runner: str,
    expected_simulator: str,
    expected_output_hash: str,
) -> dict[str, object]:
    descriptor.validate_terminal(case, ("xrage_checkpoint_recovery.pass",))
    require_clean_source(case)
    manifest_path = case / "manifest.txt"
    manifest = descriptor.read_key_values(manifest_path)
    capacity, epoch = ARMS[name]
    descriptor.require_manifest_values(
        manifest,
        {
            **COMMON_MANIFEST,
            "source_commit": expected_source,
            "runner_source_commit": expected_runner,
            "offset_table_entries": str(capacity),
            "offset_table_epoch_entries": str(epoch),
        },
        manifest_path,
    )

    result_path = case / "result.tsv"
    result = descriptor.read_one_row_tsv(result_path)
    blocks, ticks, final_ticks = descriptor.stats_blocks_and_ticks(
        case / "run/stats.txt"
    )
    expected_result = {
        "stats_blocks": blocks,
        "roi_simTicks": ticks,
        "final_simTicks": final_ticks,
    }
    for key, value in expected_result.items():
        if descriptor.integer(result, key, result_path) != value:
            fail(f"{key} disagrees with stats in {case}")
    if result.get("output_hash") != expected_output_hash:
        fail(f"unexpected output hash in {case}")

    input_path = Path(manifest["input"]).resolve()
    artifacts_path = case / "artifact_sha256.txt"
    artifacts = descriptor.artifact_hashes(artifacts_path)
    input_hash = descriptor.require_artifact_hash(
        artifacts, input_path, artifacts_path
    )
    guest = descriptor.command_option(case / "restore.command", "--cmd")
    guest_hash = descriptor.require_artifact_hash(artifacts, guest, artifacts_path)
    simulator_hashes = {
        digest
        for path, digest in artifacts.items()
        if path.name in {"gem5.opt", "gem5.fast"}
    }
    if simulator_hashes != {expected_simulator}:
        fail(f"unexpected simulator artifact in {case}: {simulator_hashes}")

    return {
        "name": name,
        "case": str(case),
        "offset_capacity": capacity,
        "offset_epoch": epoch,
        "ticks": ticks,
        "final_ticks": final_ticks,
        "output_hash": expected_output_hash,
        "input_hash": input_hash,
        "guest_hash": guest_hash,
        "simulator_hash": expected_simulator,
        "write_issues": descriptor.integer(
            result, "virtual_write_issues", result_path
        ),
        "epoch_drains": descriptor.integer(
            result, "offset_table_epoch_drains", result_path
        ),
        "table_full_events": descriptor.integer(
            result, "offset_table_full_events", result_path
        ),
        "build_rounds": descriptor.integer(
            result, "virtual_build_rounds", result_path
        ),
        "source_requests": descriptor.issue_digest_requests(
            case / "run/xrage-debug.log"
        ),
        "issue_digest_sha256": descriptor.sha256(
            case / "run/xrage-debug.log"
        ),
        **read_dram(case),
    }


def relative(after: int, before: int) -> float:
    if before <= 0 or after <= 0:
        fail("relative comparison requires positive observations")
    return after / before - 1.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("epoch16_cap16", type=Path)
    parser.add_argument("epoch4_cap16", type=Path)
    parser.add_argument("epoch4_cap4", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument(
        "--runner-commit",
        help="runner commit; defaults to the simulator source commit",
    )
    parser.add_argument("--simulator-sha256", required=True)
    parser.add_argument("--output-hash", required=True)
    args = parser.parse_args()

    cases = {
        name: getattr(args, name).resolve()
        for name in ARMS
    }
    records = {
        name: validate_case(
            name,
            case,
            args.source_commit,
            args.runner_commit or args.source_commit,
            args.simulator_sha256,
            args.output_hash,
        )
        for name, case in cases.items()
    }

    expected_schedule_diff = [
        (
            "system.maa",
            "num_offset_table_epoch_entries",
            "16384",
            "4096",
        )
    ]
    expected_storage_diff = [
        (
            "system.maa",
            "num_offset_table_entries",
            "16384",
            "4096",
        )
    ]
    config_paths = {
        name: case / "run/config.ini" for name, case in cases.items()
    }
    schedule_diff = config_differences(
        config_paths["epoch16_cap16"], config_paths["epoch4_cap16"]
    )
    storage_diff = config_differences(
        config_paths["epoch4_cap16"], config_paths["epoch4_cap4"]
    )
    if schedule_diff != expected_schedule_diff:
        fail(f"schedule arms differ outside epoch treatment: {schedule_diff}")
    if storage_diff != expected_storage_diff:
        fail(f"storage arms differ outside capacity treatment: {storage_diff}")

    identities = {
        (record["input_hash"], record["guest_hash"], record["simulator_hash"])
        for record in records.values()
    }
    if len(identities) != 1:
        fail("input, guest, or simulator identity differs across arms")

    a = records["epoch16_cap16"]
    b = records["epoch4_cap16"]
    c = records["epoch4_cap4"]
    effects = {
        "schedule_only_ticks_delta": relative(b["ticks"], a["ticks"]),
        "storage_only_ticks_delta": relative(c["ticks"], b["ticks"]),
        "combined_ticks_delta": relative(c["ticks"], a["ticks"]),
        "schedule_only_writes_delta": relative(
            b["write_issues"], a["write_issues"]
        ),
        "storage_only_writes_delta": relative(
            c["write_issues"], b["write_issues"]
        ),
        "matched_epoch_issue_digest_identical": (
            b["issue_digest_sha256"] == c["issue_digest_sha256"]
        ),
    }
    report = {
        "claim_scope": (
            "single FLAG static_2d/001 config_00_gather direct4 case; "
            "negative deltas are improvements"
        ),
        "records": records,
        "effects": effects,
        "config_treatments": {
            "schedule_only": schedule_diff,
            "storage_only": storage_diff,
        },
    }

    output = args.output_dir.resolve()
    if output.exists():
        fail(f"refusing to overwrite output: {output}")
    output.mkdir(parents=True)
    (output / "attribution.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# FLAG Offset Capacity/Epoch Attribution",
        "",
        "Negative deltas mean fewer simulated ticks or writes.",
        "",
        "| Arm | Capacity | Epoch | ROI ticks | Writes | Epoch drains | Table full |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ARMS:
        record = records[name]
        lines.append(
            f"| {name} | {record['offset_capacity']:,} | "
            f"{record['offset_epoch']:,} | {record['ticks']:,} | "
            f"{record['write_issues']:,} | {record['epoch_drains']:,} | "
            f"{record['table_full_events']:,} |"
        )
    lines.extend(
        [
            "",
            "| Effect | ROI tick delta | Write delta |",
            "|---|---:|---:|",
            "| 4K scheduling epoch, 16K storage | "
            f"{effects['schedule_only_ticks_delta'] * 100:+.3f}% | "
            f"{effects['schedule_only_writes_delta'] * 100:+.3f}% |",
            "| 4K storage, matched 4K epoch | "
            f"{effects['storage_only_ticks_delta'] * 100:+.3f}% | "
            f"{effects['storage_only_writes_delta'] * 100:+.3f}% |",
            "| Combined 4K epoch + storage | "
            f"{effects['combined_ticks_delta'] * 100:+.3f}% | n/a |",
            "",
            "Matched-epoch MAA issue digests identical: "
            f"**{effects['matched_epoch_issue_digest_identical']}**.",
            "",
        ]
    )
    (output / "attribution.md").write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
