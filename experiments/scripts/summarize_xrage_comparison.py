#!/usr/bin/env python3
"""Validate completed XRAGE runs and emit a matched comparison report."""

import argparse
import configparser
import csv
import hashlib
import re
from pathlib import Path

from summarize_xrage_dram import (
    COMMANDS,
    parse_log,
)

PASS_RE = re.compile(
    r"^MAA_GATHER_VERIFY_PASS length=(\d+) hash=(\d+)$", re.MULTILINE
)
EXIT_RE = re.compile(
    r"Exiting @ tick \d+ because m5_exit instruction encountered"
)
FATAL_RE = re.compile(
    r"panic|fatal|segmentation fault|MAA_GATHER_VERIFY_FAIL", re.IGNORECASE
)
SIM_TICKS_RE = re.compile(r"^simTicks\s+(\d+)\s+", re.MULTILINE)
REQUIRED_RESULT_FIELDS = {
    "output_hash",
    "roi_simTicks",
    "final_simTicks",
    "stats_blocks",
    "virtual_write_issues",
    "virtual_write_completions",
    "virtual_pages_ready",
    "direct_index_words",
    "indirect_spd_read_cycles",
}
STAT_FIELDS = {
    "cache_read_packets": "system.maa.port_cache_RD_packets",
    "memory_read_packets": "system.maa.port_mem_RD_packets",
}
SUM_STAT_FIELDS = {
    "fill_cycles": r"system\.maa\.I\d+_IND_CyclesFill",
    "request_cycles": r"system\.maa\.I\d+_IND_CyclesRequest",
    "index_line_reads": r"system\.maa\.I\d+_IND_VirtIndexLineReads",
    "index_outstanding_merges": (
        r"system\.maa\.I\d+_IND_VirtIndexOutstandingMerges"
    ),
    "index_outstanding_wait_cycles": (
        r"system\.maa\.I\d+_IND_VirtIndexOutstandingWaitCycles"
    ),
    "index_line_high_water": r"system\.maa\.I\d+_IND_VirtIndexLineHighWater",
    "index_word_high_water": r"system\.maa\.I\d+_IND_VirtIndexWordHighWater",
    "row_table_full_events": r"system\.maa\.I\d+_IND_NumRTFull",
    "stream_instructions": r"system\.maa\.S\d+_STR_NumInsts",
    "stream_request_cycles": r"system\.maa\.S\d+_STR_CyclesRequest",
    "stream_rt_cycles": r"system\.maa\.S\d+_STR_CyclesRTAccess",
    "stream_spd_write_cycles": r"system\.maa\.S\d+_STR_CyclesSPDWriteAccess",
    "memory_controller_reads": r"system\.mem_ctrls\d+\.numReads::maa",
}
EXPECTED_GUEST_ARMS = {
    ("native", 1): "native16",
    ("native", 3): "native16x3",
    ("native_4k", 3): "native4x3",
    ("fused", 1): "fused16",
    ("fused_4k", 1): "fused4",
    ("compact", 1): "compact16",
    ("compact", 3): "compact16x3",
    ("direct_index_16k", 1): "direct4",
    ("direct_index_4k", 1): "direct4",
    ("direct_index_4k", 3): "direct4x3",
}


def fail(message: str) -> None:
    raise SystemExit(f"XRAGE comparison failed: {message}")


def read_manifest(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator or not key or key in values:
            fail(f"malformed or duplicate manifest line in {path}: {line!r}")
        values[key] = value
    return values


def read_result(path: Path) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(rows) != 1:
        fail(f"{path} contains {len(rows)} result rows instead of one")
    missing = REQUIRED_RESULT_FIELDS - set(rows[0])
    if missing:
        fail(f"{path} lacks result fields: {sorted(missing)}")
    return rows[0]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_artifacts(path: Path, cache: dict[tuple, str]) -> dict[Path, str]:
    verified: dict[Path, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        expected, separator, filename = line.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", expected):
            fail(f"malformed artifact checksum in {path}: {line!r}")
        artifact = Path(filename)
        if not artifact.is_file():
            fail(f"missing checksummed artifact: {artifact}")
        key = (artifact, artifact.stat().st_size, artifact.stat().st_mtime_ns)
        actual = cache.get(key)
        if actual is None:
            actual = sha256(artifact)
            cache[key] = actual
        if actual != expected:
            fail(f"checksum mismatch for {artifact}")
        verified[artifact] = actual
    return verified


def read_simulator_provenance(
    path: Path, digest_cache: dict[tuple, str]
) -> tuple[str, str]:
    manifest = read_manifest(path)
    source_commit = manifest.get("source_commit", "")
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        fail(
            f"simulator provenance has invalid source_commit={source_commit!r}"
        )
    artifacts_path = path.parent / "artifact_sha256.txt"
    if not artifacts_path.is_file():
        fail(f"simulator provenance lacks {artifacts_path}")
    artifacts = verify_artifacts(artifacts_path, digest_cache)
    simulators = {
        digest
        for artifact, digest in artifacts.items()
        if artifact.name.startswith("gem5")
    }
    if len(simulators) != 1:
        fail(
            "simulator provenance has "
            f"{len(simulators)} distinct gem5 artifact hashes"
        )
    return source_commit, simulators.pop()


def verify_source_provenance(
    label: str,
    dirty: bool,
    source_commit: str,
    simulator_hash: str,
    trusted_simulator: tuple[str, str] | None,
) -> None:
    if trusted_simulator is not None:
        trusted_commit, trusted_hash = trusted_simulator
        if source_commit != trusted_commit:
            fail(
                f"{label} source commit {source_commit} differs from frozen "
                f"simulator commit {trusted_commit}"
            )
        if simulator_hash != trusted_hash:
            fail(f"{label} gem5 hash differs from frozen simulator provenance")
        return
    if dirty:
        fail(f"{label} was produced from a dirty source worktree")


def first_stat(stats: str, name: str) -> int:
    match = re.search(rf"^{re.escape(name)}\s+(\d+)\s+", stats, re.MULTILINE)
    return int(match.group(1)) if match else 0


def sum_first_block_stats(stats: str, name_pattern: str) -> int:
    first_block = stats.split("---------- End Simulation Statistics", 1)[0]
    matches = re.findall(
        rf"^{name_pattern}\s+(\d+)\s+", first_block, re.MULTILINE
    )
    return sum(int(value) for value in matches)


def require_integer(row: dict[str, str], field: str, label: str) -> int:
    try:
        return int(row[field])
    except (KeyError, ValueError) as error:
        fail(f"{label} has invalid integer {field}={row.get(field)!r}")
        raise AssertionError from error


def expected_guest_arm(manifest: dict[str, str]) -> str | None:
    try:
        scale = int(manifest.get("result_scale", "1"))
    except ValueError:
        fail(f"invalid result_scale={manifest.get('result_scale')!r}")
    return EXPECTED_GUEST_ARMS.get((manifest.get("arm", ""), scale))


def read_run(
    label: str,
    root: Path,
    expected_channels: int,
    digest_cache: dict[tuple, str],
    trusted_simulator: tuple[str, str] | None,
) -> dict[str, object]:
    required = [
        root / "manifest.txt",
        root / "source_status.txt",
        root / "artifact_sha256.txt",
        root / "restore.exit",
        root / "restore.log",
        root / "result.tsv",
        root / "run" / "stats.txt",
        root / "run" / "config.ini",
    ]
    for path in required:
        if not path.is_file():
            fail(f"{label} is missing {path}")
    if (root / "restore.exit").read_text(encoding="utf-8").strip() != "0":
        fail(f"{label} restore did not exit zero")

    manifest = read_manifest(root / "manifest.txt")
    artifacts = verify_artifacts(root / "artifact_sha256.txt", digest_cache)
    simulators = {
        digest
        for path, digest in artifacts.items()
        if path.name.startswith("gem5")
    }
    if len(simulators) != 1:
        fail(f"{label} has {len(simulators)} distinct gem5 artifact hashes")
    simulator_hash = next(iter(simulators))
    source_dirty = bool(
        (root / "source_status.txt").read_text(encoding="utf-8")
    )
    verify_source_provenance(
        label,
        source_dirty,
        manifest.get("source_commit", ""),
        simulator_hash,
        trusted_simulator,
    )
    binaries = {
        digest
        for path, digest in artifacts.items()
        if path.name.startswith("spatter_maa")
    }
    if len(binaries) != 1:
        fail(f"{label} has {len(binaries)} distinct XRAGE binary hashes")
    result = read_result(root / "result.tsv")
    log = (root / "restore.log").read_text(encoding="utf-8", errors="replace")
    stats = (root / "run" / "stats.txt").read_text(encoding="utf-8")

    passes = PASS_RE.findall(log)
    if len(passes) != 1:
        fail(f"{label} has {len(passes)} exact-output markers instead of one")
    if FATAL_RE.search(log) or not EXIT_RE.search(log):
        fail(f"{label} has a fatal marker or lacks terminal m5_exit")
    output_length, output_hash = passes[0]
    guest_arm = manifest.get("guest_arm", "")
    if guest_arm:
        expected_arm = expected_guest_arm(manifest)
        if guest_arm != expected_arm:
            fail(
                f"{label} arm/guest-arm mismatch: "
                f"{manifest.get('arm', '')}/{guest_arm}"
            )
        if f"MAA XRAGE arm {guest_arm}" not in log:
            fail(f"{label} did not execute guest arm {guest_arm}")
        roi_position = log.find("ROI End!!!")
        exact_position = log.find("MAA_GATHER_VERIFY_PASS length=")
        if roi_position < 0 or exact_position <= roi_position:
            fail(f"{label} exact verification was not post-ROI")
    ticks = [int(value) for value in SIM_TICKS_RE.findall(stats)]
    if len(ticks) != 2 or ticks[0] <= 0 or ticks[1] < ticks[0]:
        fail(f"{label} has invalid first/final simTicks blocks: {ticks}")

    if result["output_hash"] != output_hash:
        fail(f"{label} result hash does not match its exact-output marker")
    if require_integer(result, "roi_simTicks", label) != ticks[0]:
        fail(f"{label} result first-ROI ticks do not match raw stats")
    if require_integer(result, "final_simTicks", label) != ticks[1]:
        fail(f"{label} result final ticks do not match raw stats")
    if require_integer(result, "stats_blocks", label) != 2:
        fail(f"{label} result does not attest exactly two stats blocks")

    writes = require_integer(result, "virtual_write_issues", label)
    completions = require_integer(result, "virtual_write_completions", label)
    index_words = require_integer(result, "direct_index_words", label)
    if writes != completions:
        fail(
            f"{label} has {writes} write issues but {completions} completions"
        )
    arm = manifest.get("arm", "")
    if arm.startswith("direct_index") and index_words != int(output_length):
        fail(
            f"{label} consumed {index_words} direct-index words for "
            f"{output_length} outputs"
        )
    if arm in {"native", "native_4k", "fused", "fused_4k"} and (
        writes or index_words
    ):
        fail(f"{label} unexpectedly activated virtual machinery")

    config = configparser.RawConfigParser(strict=False)
    config.read(root / "run" / "config.ini")
    if not config.has_section("system.maa"):
        fail(f"{label} config has no system.maa section")
    maa = config["system.maa"]
    logical = require_integer(manifest, "maa_logical_tile_elements", label)
    physical = require_integer(manifest, "physical_tile_elements", label)
    chunk = require_integer(manifest, "workload_chunk_elements", label)
    index_lines = int(manifest.get("virtual_index_buffer_lines", "1"))
    row_table_slices = int(manifest.get("initial_row_table_slices", "32"))
    row_table_rows = int(manifest.get("row_table_rows_per_slice", "64"))
    indirect_units = int(manifest.get("num_indirect_units_per_maa", "1"))
    grow_order = int(manifest.get("virtual_grow_order", "0"))
    native_issue_order = int(manifest.get("virtual_native_issue_order", "0"))
    if grow_order not in {0, 1} or native_issue_order not in {0, 1}:
        fail(f"{label} has a non-boolean virtual issue-order mode")
    if grow_order and native_issue_order:
        fail(f"{label} enables mutually exclusive virtual issue-order modes")
    if int(maa["num_tile_elements"]) != logical:
        fail(f"{label} config and manifest disagree on logical tile size")
    if int(maa["physical_tile_elements"]) != physical:
        fail(f"{label} config and manifest disagree on physical tile size")
    if int(maa["virtual_index_buffer_lines"]) != index_lines:
        fail(f"{label} config and manifest disagree on index-buffer depth")
    if int(maa["num_initial_row_table_slices"]) != row_table_slices:
        fail(f"{label} config and manifest disagree on row-table slices")
    if int(maa["num_row_table_rows_per_slice"]) != row_table_rows:
        fail(f"{label} config and manifest disagree on row-table rows")
    if int(maa["num_indirect_units_per_maa"]) != indirect_units:
        fail(f"{label} config and manifest disagree on indirect-unit count")
    if int(maa.getboolean("virtual_grow_order", fallback=False)) != grow_order:
        fail(f"{label} config and manifest disagree on virtual grow order")
    if (
        int(maa.getboolean("virtual_native_issue_order", fallback=False))
        != native_issue_order
    ):
        fail(f"{label} config and manifest disagree on native issue order")
    if f"MAA gather execution {output_length}/{chunk}" not in log:
        fail(f"{label} log does not attest its workload chunk size")

    try:
        dram = parse_log(root / "restore.log", expected_channels)
    except ValueError as error:
        fail(str(error))
        raise AssertionError from error
    dram_totals = {
        command: sum(channel[command] for channel in dram.values())
        for command in COMMANDS
    }
    input_path = Path(manifest.get("input", ""))
    if not input_path.is_file():
        fail(f"{label} manifest input is missing: {input_path}")

    return {
        "label": label,
        "source_worktree_dirty": int(source_dirty),
        "arm": arm,
        "source_commit": manifest.get("source_commit", ""),
        "gem5_sha256": simulators.pop(),
        "binary_sha256": binaries.pop(),
        "input": str(input_path.resolve()),
        "input_sha256": sha256(input_path),
        "output_length": int(output_length),
        "output_hash": output_hash,
        "logical_tile_elements": logical,
        "physical_tile_elements": physical,
        "workload_chunk_elements": chunk,
        "index_buffer_lines": index_lines,
        "initial_row_table_slices": row_table_slices,
        "row_table_rows_per_slice": row_table_rows,
        "num_indirect_units_per_maa": indirect_units,
        "virtual_grow_order": grow_order,
        "virtual_native_issue_order": native_issue_order,
        "roi_simTicks": ticks[0],
        "final_simTicks": ticks[1],
        "virtual_write_issues": writes,
        "virtual_write_completions": completions,
        "virtual_pages_ready": require_integer(
            result, "virtual_pages_ready", label
        ),
        "direct_index_words": index_words,
        "indirect_spd_read_cycles": require_integer(
            result, "indirect_spd_read_cycles", label
        ),
        **{
            field: first_stat(stats, name)
            for field, name in STAT_FIELDS.items()
        },
        **{
            field: sum_first_block_stats(stats, pattern)
            for field, pattern in SUM_STAT_FIELDS.items()
        },
        "dram_reads": dram_totals["RD"],
        "dram_activates": dram_totals["ACT"],
        "dram_precharges": dram_totals["PRE"],
    }


def parse_run(value: str) -> tuple[str, Path]:
    label, separator, path = value.partition("=")
    if (
        not separator
        or not label
        or not path
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", label)
    ):
        raise argparse.ArgumentTypeError("run must have the form LABEL=PATH")
    return label, Path(path).resolve()


def parse_pair(value: str) -> tuple[str, str, str]:
    name, separator, labels = value.partition("=")
    reference, comma, candidate = labels.partition(",")
    if (
        not separator
        or not comma
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", name)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", reference)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", candidate)
    ):
        raise argparse.ArgumentTypeError(
            "pair must have the form NAME=REFERENCE,CANDIDATE"
        )
    return name, reference, candidate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-channels", type=int, default=2)
    parser.add_argument("--require-shared-binary", action="store_true")
    parser.add_argument("--simulator-provenance", type=Path)
    parser.add_argument("--pair", action="append", type=parse_pair, default=[])
    parser.add_argument("runs", nargs="+", type=parse_run)
    args = parser.parse_args()
    if args.expected_channels <= 0:
        parser.error("--expected-channels must be positive")
    labels = [label for label, _ in args.runs]
    if len(labels) != len(set(labels)):
        parser.error("run labels must be unique")
    if args.baseline not in labels:
        parser.error("--baseline must name one of the runs")
    pair_names = [name for name, _, _ in args.pair]
    if len(pair_names) != len(set(pair_names)):
        parser.error("pair names must be unique")
    for name, reference, candidate in args.pair:
        if reference not in labels or candidate not in labels:
            parser.error(
                f"pair {name} refers to an unknown run: "
                f"{reference},{candidate}"
            )

    digest_cache: dict[tuple, str] = {}
    trusted_simulator = None
    if args.simulator_provenance is not None:
        trusted_simulator = read_simulator_provenance(
            args.simulator_provenance.resolve(), digest_cache
        )
    rows = [
        read_run(
            label,
            root,
            args.expected_channels,
            digest_cache,
            trusted_simulator,
        )
        for label, root in args.runs
    ]
    expected = rows[0]
    shared_fields = [
        "gem5_sha256",
        "input_sha256",
        "output_length",
        "output_hash",
    ]
    if args.require_shared_binary:
        shared_fields.append("binary_sha256")
    for row in rows[1:]:
        for field in shared_fields:
            if row[field] != expected[field]:
                fail(
                    f"{row['label']} {field}={row[field]} differs from "
                    f"{expected['label']} {expected[field]}"
                )

    baseline = next(row for row in rows if row["label"] == args.baseline)
    baseline_ticks = int(baseline["roi_simTicks"])
    for row in rows:
        ticks = int(row["roi_simTicks"])
        row[
            "latency_delta_vs_baseline_pct"
        ] = f"{100.0 * (ticks / baseline_ticks - 1.0):+.6f}"
        row[
            "throughput_delta_vs_baseline_pct"
        ] = f"{100.0 * (baseline_ticks / ticks - 1.0):+.6f}"

    rows_by_label = {str(row["label"]): row for row in rows}
    pair_rows = []
    for name, reference_label, candidate_label in args.pair:
        reference = rows_by_label[reference_label]
        candidate = rows_by_label[candidate_label]
        reference_ticks = int(reference["roi_simTicks"])
        candidate_ticks = int(candidate["roi_simTicks"])
        pair_rows.append(
            {
                "pair": name,
                "reference": reference_label,
                "candidate": candidate_label,
                "reference_ticks": reference_ticks,
                "candidate_ticks": candidate_ticks,
                "latency_delta_pct": (
                    f"{100.0 * (candidate_ticks / reference_ticks - 1.0):+.6f}"
                ),
                "throughput_delta_pct": (
                    f"{100.0 * (reference_ticks / candidate_ticks - 1.0):+.6f}"
                ),
                "dram_reads_delta": (
                    int(candidate["dram_reads"]) - int(reference["dram_reads"])
                ),
                "dram_activates_delta": (
                    int(candidate["dram_activates"])
                    - int(reference["dram_activates"])
                ),
                "dram_precharges_delta": (
                    int(candidate["dram_precharges"])
                    - int(reference["dram_precharges"])
                ),
                "roi_memory_reads_delta": (
                    int(candidate["memory_controller_reads"])
                    - int(reference["memory_controller_reads"])
                ),
            }
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    tsv = args.output_dir / "xrage_comparison.tsv"
    with tsv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    if pair_rows:
        pair_tsv = args.output_dir / "xrage_pairwise.tsv"
        with pair_tsv.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, list(pair_rows[0]), delimiter="\t")
            writer.writeheader()
            writer.writerows(pair_rows)

    markdown = [
        "# XRAGE Comparison",
        "",
        f"Baseline: `{args.baseline}`. All rows passed exact-output, artifact, "
        "terminal-exit, configuration, and two-channel Ramulator artifact "
        "validation.",
        "",
        "`Memory-controller reads` below come from the first ROI statistics "
        "block. Ramulator RD/ACT/PRE totals are emitted at process exit and "
        "include the post-ROI exact verifier, so they are full-run diagnostics "
        "rather than ROI attribution counters.",
        "",
        "| Run | Arm | Logical | Physical | B lines | Native order | First-ROI ticks | "
        "Latency vs. baseline | Throughput vs. baseline | Full-run Ramulator "
        "RD/ACT/PRE |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        markdown.append(
            f"| {row['label']} | {row['arm']} | "
            f"{row['logical_tile_elements']} | {row['physical_tile_elements']} | "
            f"{row['index_buffer_lines']} | "
            f"{row['virtual_native_issue_order']} | {row['roi_simTicks']} | "
            f"{row['latency_delta_vs_baseline_pct']}% | "
            f"{row['throughput_delta_vs_baseline_pct']}% | "
            f"{row['dram_reads']}/{row['dram_activates']}/"
            f"{row['dram_precharges']} |"
        )
    markdown.extend(
        [
            "",
            "## Pipeline Attribution Counters",
            "",
            "These stage counters can overlap and must not be added as wall-clock "
            "latency.",
            "",
            "| Run | Stream insts | B-stream request cycles | B-stream SPD-write "
            "cycles | Direct B lines | Indirect fill cycles | Indirect request "
            "cycles | Memory-controller reads |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in rows:
        markdown.append(
            f"| {row['label']} | {row['stream_instructions']} | "
            f"{row['stream_request_cycles']} | "
            f"{row['stream_spd_write_cycles']} | "
            f"{row['index_line_reads']} | {row['fill_cycles']} | "
            f"{row['request_cycles']} | {row['memory_controller_reads']} |"
        )
    if pair_rows:
        markdown.extend(
            [
                "",
                "## Pairwise Comparisons",
                "",
                "| Pair | Reference | Candidate | Latency delta | "
                "Throughput delta | First-ROI memory-read delta | Full-run "
                "Ramulator RD/ACT/PRE delta |",
                "|---|---|---|---:|---:|---:|---:|",
            ]
        )
        for pair in pair_rows:
            markdown.append(
                f"| {pair['pair']} | {pair['reference']} | "
                f"{pair['candidate']} | {pair['latency_delta_pct']}% | "
                f"{pair['throughput_delta_pct']}% | "
                f"{pair['roi_memory_reads_delta']:+d} | "
                f"{pair['dram_reads_delta']:+d}/"
                f"{pair['dram_activates_delta']:+d}/"
                f"{pair['dram_precharges_delta']:+d} |"
            )
    (args.output_dir / "xrage_comparison.md").write_text(
        "\n".join(markdown) + "\n", encoding="utf-8"
    )
    (args.output_dir / "xrage_comparison.pass").touch()
    print(f"PASS XRAGE comparison: {tsv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
