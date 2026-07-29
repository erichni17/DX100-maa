#!/usr/bin/env python3
"""Compare validated FLAG direct-gather descriptor-capacity campaigns."""

import argparse
import configparser
import csv
import hashlib
import json
import math
import re
import shlex
from pathlib import Path


EXPECTED_CASES = 14
FATAL_RE = re.compile(r"panic|fatal|segmentation fault|MAA_GATHER_VERIFY_FAIL", re.I)
DIGEST_COUNT_RE = re.compile(r"\bcount=(\d+)\b")
ALLOWED_CONFIG_CHANGE = (
    "system.maa",
    "num_row_table_rows_per_slice",
    "64",
    "16",
)
OPTIONAL_FALSE_KEYS = {
    ("system.maa", "virtual_index_force_cache"),
    ("system.maa", "virtual_partition_keep_combiner"),
}


def fail(message: str) -> None:
    raise SystemExit(f"FLAG descriptor-capacity comparison failed: {message}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_key_values(path: Path) -> dict[str, str]:
    if not path.is_file():
        fail(f"missing key-value file: {path}")
    values: dict[str, str] = {}
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line:
            continue
        if "=" not in raw_line:
            fail(f"malformed line {number} in {path}")
        key, value = raw_line.split("=", 1)
        if not key or key in values:
            fail(f"invalid or duplicate key {key!r} in {path}")
        values[key] = value
    return values


def read_one_row_tsv(path: Path) -> dict[str, str]:
    if not path.is_file():
        fail(f"missing TSV: {path}")
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(rows) != 1 or not rows[0]:
        fail(f"expected exactly one data row in {path}")
    return rows[0]


def integer(row: dict[str, str], key: str, path: Path) -> int:
    try:
        value = int(row[key])
    except (KeyError, ValueError):
        fail(f"missing or invalid {key} in {path}")
    if value < 0:
        fail(f"negative {key} in {path}")
    return value


def artifact_hashes(path: Path) -> dict[Path, str]:
    if not path.is_file():
        fail(f"missing artifact manifest: {path}")
    artifacts: dict[Path, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            fail(f"malformed artifact line {number} in {path}")
        artifact = Path(match.group(2)).resolve()
        if artifact in artifacts:
            fail(f"duplicate artifact {artifact} in {path}")
        artifacts[artifact] = match.group(1)
    return artifacts


def command_option(path: Path, option: str) -> Path:
    if not path.is_file():
        fail(f"missing command record: {path}")
    tokens = shlex.split(path.read_text(encoding="utf-8"))
    for index, token in enumerate(tokens):
        if token == option and index + 1 < len(tokens):
            return Path(tokens[index + 1]).resolve()
        if token.startswith(option + "="):
            return Path(token.split("=", 1)[1]).resolve()
    fail(f"{option} is absent from {path}")


def require_artifact_hash(
    artifacts: dict[Path, str], artifact: Path, manifest: Path
) -> str:
    try:
        return artifacts[artifact.resolve()]
    except KeyError:
        fail(f"{artifact} is absent from {manifest}")


def normalized_maa_config(path: Path) -> dict[tuple[str, str], str]:
    if not path.is_file():
        fail(f"missing resolved gem5 configuration: {path}")
    config = configparser.RawConfigParser(strict=False)
    config.read(path)
    values = {
        (section, key): value
        for section in config.sections()
        if section == "system.maa" or section.startswith("system.maa_")
        for key, value in config[section].items()
    }
    if not values:
        fail(f"no MAA configuration found in {path}")
    return values


def validate_config_pair(baseline: Path, candidate: Path) -> None:
    baseline_values = normalized_maa_config(baseline)
    candidate_values = normalized_maa_config(candidate)
    all_keys = set(baseline_values) | set(candidate_values)
    differences = []
    for key in sorted(all_keys):
        before = baseline_values.get(key)
        after = candidate_values.get(key)
        if key in OPTIONAL_FALSE_KEYS:
            before = before or "false"
            after = after or "false"
        if before != after:
            differences.append((*key, before, after))
    if differences != [ALLOWED_CONFIG_CHANGE]:
        fail(
            "resolved MAA configuration differs outside the descriptor "
            f"capacity treatment: {differences}"
        )


def stats_blocks_and_ticks(path: Path) -> tuple[int, int, int]:
    if not path.is_file():
        fail(f"missing gem5 stats: {path}")
    ticks = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == "simTicks":
            try:
                ticks.append(int(fields[1]))
            except ValueError:
                fail(f"invalid simTicks in {path}")
    if len(ticks) != 2 or ticks[0] <= 0 or ticks[1] < ticks[0]:
        fail(f"expected two ordered simTicks blocks in {path}, found {ticks}")
    return len(ticks), ticks[0], ticks[-1]


def validate_terminal(case: Path, marker_names: tuple[str, ...]) -> None:
    if not any((case / marker).is_file() for marker in marker_names):
        fail(f"missing pass marker in {case}")
    exit_path = case / "restore.exit"
    if not exit_path.is_file() or exit_path.read_text(encoding="ascii").strip() != "0":
        fail(f"nonzero or missing restore exit in {case}")
    log_path = case / "restore.log"
    if not log_path.is_file():
        fail(f"missing restore log in {case}")
    log = log_path.read_text(encoding="utf-8", errors="replace")
    if "MAA_GATHER_VERIFY_PASS " not in log:
        fail(f"missing exact-output verifier in {case}")
    if not re.search(r"Exiting @ tick .* because m5_exit instruction encountered", log):
        fail(f"missing terminal m5_exit in {case}")
    if FATAL_RE.search(log):
        fail(f"fatal marker found in {case}")


def issue_digest_requests(path: Path) -> int:
    if not path.is_file():
        fail(f"missing MAA issue digest: {path}")
    counts = [
        int(match.group(1))
        for match in DIGEST_COUNT_RE.finditer(
            path.read_text(encoding="utf-8", errors="replace")
        )
    ]
    if not counts:
        fail(f"no MAA issue digest records in {path}")
    return sum(counts)


def require_manifest_values(
    manifest: dict[str, str], expected: dict[str, str], path: Path
) -> None:
    differences = {
        key: (manifest.get(key), value)
        for key, value in expected.items()
        if manifest.get(key) != value
    }
    if differences:
        fail(f"unexpected mechanism configuration in {path}: {differences}")


def geometric_mean(values: list[float]) -> float:
    if not values or any(value <= 0 for value in values):
        fail("geometric mean requires positive observations")
    return math.exp(sum(math.log(value) for value in values) / len(values))


def case_directories(root: Path, arm: str | None) -> dict[str, Path]:
    cases_root = root / "cases"
    if not cases_root.is_dir():
        fail(f"missing campaign cases directory: {cases_root}")
    cases: dict[str, Path] = {}
    for entry in sorted(cases_root.iterdir()):
        if not entry.is_dir():
            continue
        case = entry / arm if arm else entry
        if not case.exists():
            fail(f"missing {arm} arm for {entry.name}")
        cases[entry.name] = case.resolve()
    if len(cases) != EXPECTED_CASES:
        fail(f"expected {EXPECTED_CASES} cases in {root}, found {len(cases)}")
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline_campaign", type=Path)
    parser.add_argument("candidate_campaign", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    baseline_cases = case_directories(args.baseline_campaign.resolve(), "direct4")
    candidate_cases = case_directories(args.candidate_campaign.resolve(), None)
    if set(baseline_cases) != set(candidate_cases):
        fail("baseline and candidate case IDs differ")

    rows: list[dict[str, object]] = []
    baseline_expected = {
        "arm": "direct_index_4k",
        "guest_arm": "direct4",
        "physical_tile_elements": "4096",
        "maa_logical_tile_elements": "16384",
        "workload_chunk_elements": "16384",
        "virtual_grow_order": "0",
        "virtual_native_issue_order": "1",
        "virtual_index_buffer_lines": "128",
        "initial_row_table_slices": "32",
        "row_table_rows_per_slice": "64",
        "num_indirect_units_per_maa": "1",
        "debug_flags": "MAAIssueDigest",
    }
    candidate_expected = {
        **baseline_expected,
        "row_table_rows_per_slice": "16",
        "virtual_index_force_cache": "0",
        "virtual_index_partitions": "1",
        "virtual_index_filter_words_per_cycle": "0",
        "virtual_partition_keep_combiner": "0",
        "retirement_cache_size": "1kB",
        "virtual_combine_slots": "384",
        "virtual_combine_words": "4096",
        "virtual_combine_ways": "4",
        "virtual_response_slots": "128",
        "virtual_response_word_pool": "480",
        "virtual_words_per_cycle": "4",
    }

    for case_id in sorted(baseline_cases):
        baseline = baseline_cases[case_id]
        candidate = candidate_cases[case_id]
        validate_terminal(baseline, ("xrage_attribution_smoke.pass",))
        validate_terminal(candidate, ("xrage_checkpoint_recovery.pass",))

        baseline_manifest_path = baseline / "manifest.txt"
        candidate_manifest_path = candidate / "manifest.txt"
        baseline_manifest = read_key_values(baseline_manifest_path)
        candidate_manifest = read_key_values(candidate_manifest_path)
        require_manifest_values(baseline_manifest, baseline_expected, baseline_manifest_path)
        require_manifest_values(candidate_manifest, candidate_expected, candidate_manifest_path)
        if Path(candidate_manifest.get("checkpoint_run", "")).resolve() != baseline:
            fail(f"candidate checkpoint does not come from baseline for {case_id}")
        if candidate_manifest.get("checkpoint_retargeted") != "0":
            fail(f"candidate checkpoint was retargeted for {case_id}")
        if candidate_manifest.get("timeout") != "none" or baseline_manifest.get("timeout") != "none":
            fail(f"a run used a wall-clock timeout for {case_id}")

        baseline_input = Path(baseline_manifest["input"]).resolve()
        candidate_input = Path(candidate_manifest["input"]).resolve()
        if baseline_input != candidate_input:
            fail(f"input paths differ for {case_id}")
        baseline_artifact_path = baseline / "artifact_sha256.txt"
        candidate_artifact_path = candidate / "artifact_sha256.txt"
        baseline_artifacts = artifact_hashes(baseline_artifact_path)
        candidate_artifacts = artifact_hashes(candidate_artifact_path)
        input_hash = require_artifact_hash(
            baseline_artifacts, baseline_input, baseline_artifact_path
        )
        if require_artifact_hash(candidate_artifacts, candidate_input, candidate_artifact_path) != input_hash:
            fail(f"input hashes differ for {case_id}")
        baseline_guest = command_option(baseline / "restore.command", "--cmd")
        candidate_guest = command_option(candidate / "restore.command", "--cmd")
        guest_hash = require_artifact_hash(
            baseline_artifacts, baseline_guest, baseline_artifact_path
        )
        if require_artifact_hash(candidate_artifacts, candidate_guest, candidate_artifact_path) != guest_hash:
            fail(f"guest binary hashes differ for {case_id}")

        validate_config_pair(baseline / "run/config.ini", candidate / "run/config.ini")
        baseline_result_path = baseline / "result.tsv"
        candidate_result_path = candidate / "result.tsv"
        baseline_result = read_one_row_tsv(baseline_result_path)
        candidate_result = read_one_row_tsv(candidate_result_path)
        if baseline_result.get("output_hash") != candidate_result.get("output_hash"):
            fail(f"exact output hashes differ for {case_id}")
        baseline_blocks, baseline_ticks, baseline_final = stats_blocks_and_ticks(
            baseline / "run/stats.txt"
        )
        candidate_blocks, candidate_ticks, candidate_final = stats_blocks_and_ticks(
            candidate / "run/stats.txt"
        )
        for result, result_path, blocks, ticks, final in (
            (baseline_result, baseline_result_path, baseline_blocks, baseline_ticks, baseline_final),
            (candidate_result, candidate_result_path, candidate_blocks, candidate_ticks, candidate_final),
        ):
            if (
                integer(result, "stats_blocks", result_path) != blocks
                or integer(result, "roi_simTicks", result_path) != ticks
                or integer(result, "final_simTicks", result_path) != final
            ):
                fail(f"result table does not match stats in {result_path}")

        baseline_dram = read_one_row_tsv(baseline / "dram_commands.tsv")
        candidate_dram = read_one_row_tsv(candidate / "dram_commands.tsv")
        baseline_requests = issue_digest_requests(baseline / "run/xrage-debug.log")
        candidate_requests = issue_digest_requests(candidate / "run/xrage-debug.log")
        rows.append(
            {
                "id": case_id,
                "output_hash": baseline_result["output_hash"],
                "input_sha256": input_hash,
                "guest_sha256": guest_hash,
                "baseline_ticks": baseline_ticks,
                "candidate_ticks": candidate_ticks,
                "latency_ratio": candidate_ticks / baseline_ticks,
                "baseline_source_requests": baseline_requests,
                "candidate_source_requests": candidate_requests,
                "source_request_ratio": candidate_requests / baseline_requests,
                "row_table_full_events": integer(
                    candidate_result, "row_table_full_events", candidate_result_path
                ),
                "candidate_build_rounds": integer(
                    candidate_result, "virtual_build_rounds", candidate_result_path
                ),
                "baseline_writes": integer(
                    baseline_result, "virtual_write_issues", baseline_result_path
                ),
                "candidate_writes": integer(
                    candidate_result, "virtual_write_issues", candidate_result_path
                ),
                "baseline_dram_reads_ch0": integer(
                    baseline_dram, "dram_reads", baseline / "dram_commands.tsv"
                ),
                "candidate_dram_reads_ch0": integer(
                    candidate_dram, "dram_reads", candidate / "dram_commands.tsv"
                ),
                "baseline_dram_activates_ch0": integer(
                    baseline_dram, "dram_activates", baseline / "dram_commands.tsv"
                ),
                "candidate_dram_activates_ch0": integer(
                    candidate_dram, "dram_activates", candidate / "dram_commands.tsv"
                ),
                "baseline_dram_precharges_ch0": integer(
                    baseline_dram, "dram_precharges", baseline / "dram_commands.tsv"
                ),
                "candidate_dram_precharges_ch0": integer(
                    candidate_dram, "dram_precharges", candidate / "dram_commands.tsv"
                ),
            }
        )

    ratios = [float(row["latency_ratio"]) for row in rows]
    request_ratios = [float(row["source_request_ratio"]) for row in rows]
    summary = {
        "cases": len(rows),
        "latency_geomean_ratio": geometric_mean(ratios),
        "latency_minimum_ratio": min(ratios),
        "latency_maximum_ratio": max(ratios),
        "source_request_geomean_ratio": geometric_mean(request_ratios),
        "row_table_full_events_total": sum(
            int(row["row_table_full_events"]) for row in rows
        ),
    }

    output = args.output_dir.resolve()
    if output.exists():
        fail(f"refusing to overwrite output: {output}")
    output.mkdir(parents=True)
    fieldnames = list(rows[0])
    with (output / "flag_descriptor_capacity.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    report = {
        "baseline_campaign": str(args.baseline_campaign.resolve()),
        "candidate_campaign": str(args.candidate_campaign.resolve()),
        "baseline_campaign_sha256": sha256(
            args.baseline_campaign.resolve() / "summary/flag_gather_generalization.json"
        ),
        "config_treatment": {
            "key": "system.maa.num_row_table_rows_per_slice",
            "baseline": 64,
            "candidate": 16,
        },
        "configurations": rows,
        "summary": summary,
    }
    (output / "flag_descriptor_capacity.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    markdown = [
        "# FLAG Descriptor Capacity",
        "",
        "All 14 pairs passed exact-output, terminal, artifact, checkpoint, "
        "two-ROI-block, and resolved-configuration checks. The only resolved "
        "MAA configuration change is 64 to 16 Row-Table rows per slice, "
        "reducing active descriptor capacity from 16K to 4K entries.",
        "",
        "| Configuration | Latency | Source requests | RT full | Writes |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        markdown.append(
            f"| {row['id']} | {100 * (float(row['latency_ratio']) - 1):+.3f}% | "
            f"{100 * (float(row['source_request_ratio']) - 1):+.3f}% | "
            f"{int(row['row_table_full_events']):,} | "
            f"{int(row['candidate_writes']):,} |"
        )
    markdown.extend(
        [
            "",
            "## Equal-Weight Geometric Mean",
            "",
            f"- Latency: {100 * (summary['latency_geomean_ratio'] - 1):+.3f}%",
            "- Source requests: "
            f"{100 * (summary['source_request_geomean_ratio'] - 1):+.3f}%",
            f"- Row-Table full events: {summary['row_table_full_events_total']:,}",
            "",
            "Source-request digests are counted, not required to match: the "
            "descriptor-capacity treatment intentionally changes legal issue "
            "order and can add retry work. Exact guest output remains required.",
        ]
    )
    (output / "flag_descriptor_capacity.md").write_text(
        "\n".join(markdown) + "\n", encoding="utf-8"
    )
    (output / "flag_descriptor_capacity.pass").touch()
    print(
        "PASS FLAG descriptor-capacity comparison: "
        f"{len(rows)} cases, latency {100 * (summary['latency_geomean_ratio'] - 1):+.3f}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
