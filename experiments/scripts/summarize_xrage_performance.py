#!/usr/bin/env python3
"""Validate matched non-verifier XRAGE runs and summarize performance."""

import argparse
import configparser
import csv
import hashlib
import re
import statistics
from pathlib import Path

from summarize_xrage_dram import (
    COMMANDS,
    parse_log,
)

FATAL_RE = re.compile(
    r"panic|fatal|segmentation fault|MAA_GATHER_VERIFY_(?:PASS|FAIL)",
    re.IGNORECASE,
)
EXACT_FATAL_RE = re.compile(
    r"panic|fatal|segmentation fault|MAA_GATHER_VERIFY_FAIL", re.IGNORECASE
)
EXIT_RE = re.compile(
    r"Exiting @ tick [0-9]+ because m5_exit instruction encountered"
)
EXACT_RE = re.compile(
    r"^MAA_GATHER_VERIFY_PASS length=([0-9]+) hash=([0-9]+)$",
    re.MULTILINE,
)
SIM_TICKS_RE = re.compile(r"^simTicks\s+([0-9]+)\s+", re.MULTILINE)
STAT_FIELDS = {
    "fill_cycles": "system.maa.I0_IND_CyclesFill",
    "request_cycles": "system.maa.I0_IND_CyclesRequest",
    "cache_read_packets": "system.maa.port_cache_RD_packets",
    "memory_read_packets": "system.maa.port_mem_RD_packets",
    "virtual_write_issues": "system.maa.I0_IND_VirtWriteIssues",
    "virtual_write_completions": "system.maa.I0_IND_VirtWriteCompletions",
    "virtual_pages_ready": "system.maa.I0_IND_VirtPagesReady",
    "direct_index_words": "system.maa.I0_IND_VirtIndexWords",
    "indirect_spd_read_cycles": "system.maa.I0_IND_CyclesSPDReadAccess",
    "source_words": "system.maa.I0_IND_NumWordsInserted",
    "source_rows": "system.maa.I0_IND_NumRowsInserted",
    "unique_source_rows": "system.maa.I0_IND_NumUniqueRowsInserted",
    "row_table_full_events": "system.maa.I0_IND_NumRTFull",
}
RESULT_FIELDS = [
    "replica",
    "roi_simTicks",
    "final_simTicks",
    "stats_blocks",
    "virtual_write_issues",
    "virtual_write_completions",
    "virtual_pages_ready",
    "direct_index_words",
    "indirect_spd_read_cycles",
]


def fail(message: str) -> None:
    raise SystemExit(f"XRAGE performance validation failed: {message}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_kv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator or not key or key in values:
            fail(f"malformed or duplicate manifest line in {path}: {line!r}")
        values[key] = value
    return values


def verify_hash_list(path: Path) -> dict[Path, str]:
    verified: dict[Path, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        expected, separator, filename = line.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", expected):
            fail(f"malformed checksum line in {path}: {line!r}")
        artifact = Path(filename)
        if not artifact.is_file() or sha256(artifact) != expected:
            fail(f"missing or changed artifact: {artifact}")
        verified[artifact] = expected
    return verified


def gem5_hash(artifacts: dict[Path, str], label: str) -> str:
    hashes = {
        digest
        for path, digest in artifacts.items()
        if path.name.startswith("gem5")
    }
    if len(hashes) != 1:
        fail(f"{label} has ambiguous gem5 artifacts")
    return hashes.pop()


def first_stat(stats: str, name: str) -> int:
    match = re.search(
        rf"^{re.escape(name)}\s+([0-9]+)\s+", stats, re.MULTILINE
    )
    return int(match.group(1)) if match else 0


def read_exact_reference(
    root: Path, expected_input_sha: str
) -> tuple[int, str, str, str, int, int, int]:
    required = [
        root / "manifest.txt",
        root / "source_status.txt",
        root / "artifact_sha256.txt",
        root / "restore.log",
        root / "result.tsv",
    ]
    if any(not path.is_file() for path in required):
        fail(f"incomplete exact reference: {root}")
    if not (
        (root / "xrage_attribution_smoke.pass").is_file()
        or (root / "xrage_checkpoint_recovery.pass").is_file()
    ):
        fail(f"exact reference has no pass marker: {root}")
    if (root / "source_status.txt").read_text(encoding="utf-8"):
        fail(f"exact reference was produced from a dirty worktree: {root}")
    artifacts = verify_hash_list(root / "artifact_sha256.txt")
    manifest = read_kv(root / "manifest.txt")
    exact_input = Path(manifest.get("input", ""))
    if not exact_input.is_file() or sha256(exact_input) != expected_input_sha:
        fail(f"exact reference input differs: {root}")
    log = (root / "restore.log").read_text(encoding="utf-8", errors="replace")
    matches = EXACT_RE.findall(log)
    if len(matches) != 1 or EXACT_FATAL_RE.search(log):
        fail(f"invalid exact-output evidence: {root}")
    try:
        exact_config = (
            manifest["arm"],
            int(manifest["maa_logical_tile_elements"]),
            int(manifest["physical_tile_elements"]),
            int(manifest["virtual_index_buffer_lines"]),
        )
    except (KeyError, ValueError) as error:
        fail(
            f"exact reference has incomplete mechanism metadata: {root}: {error}"
        )
    return (
        int(matches[0][0]),
        matches[0][1],
        gem5_hash(artifacts, f"exact reference {root}"),
        *exact_config,
    )


def read_run(label: str, root: Path, channels: int) -> dict[str, object]:
    required = [
        root / "manifest.txt",
        root / "source_status.txt",
        root / "artifact_sha256.txt",
        root / "checkpoint.exit",
        root / "checkpoint_sha256.txt",
        root / "results.tsv",
        root / "xrage_performance.pass",
    ]
    if any(not path.is_file() for path in required):
        fail(f"{label} is incomplete: {root}")
    if (root / "source_status.txt").read_text(encoding="utf-8"):
        fail(f"{label} was produced from a dirty worktree")
    if (root / "checkpoint.exit").read_text(encoding="utf-8").strip() != "0":
        fail(f"{label} checkpoint failed")
    artifacts = verify_hash_list(root / "artifact_sha256.txt")
    verify_hash_list(root / "checkpoint_sha256.txt")
    manifest = read_kv(root / "manifest.txt")
    replicas = int(manifest["replicas"])
    input_path = Path(manifest["input"])
    if not input_path.is_file():
        fail(f"{label} input is missing")
    input_sha = sha256(input_path)
    arm = manifest["arm"]
    (
        exact_length,
        exact_hash,
        exact_gem5_sha,
        exact_arm,
        exact_logical,
        exact_physical,
        exact_index_lines,
    ) = read_exact_reference(Path(manifest["exact_reference"]), input_sha)
    if (
        int(manifest["exact_length"]) != exact_length
        or manifest["exact_hash"] != exact_hash
    ):
        fail(f"{label} manifest does not match its exact reference")
    mechanism = (
        arm,
        int(manifest["maa_logical_tile_elements"]),
        int(manifest["physical_tile_elements"]),
        int(manifest["virtual_index_buffer_lines"]),
    )
    exact_mechanism = (
        exact_arm,
        exact_logical,
        exact_physical,
        exact_index_lines,
    )
    if mechanism != exact_mechanism:
        fail(
            f"{label} mechanism differs from exact reference: "
            f"performance={mechanism}, exact={exact_mechanism}"
        )

    with (root / "results.tsv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(rows) != replicas or list(rows[0]) != RESULT_FIELDS:
        fail(f"{label} has an invalid results table")

    replica_data = []
    for number, result in enumerate(rows, 1):
        run = root / f"replica_{number}"
        for path in (
            run / "restore.exit",
            run / "restore.log",
            run / "stats.txt",
            run / "config.ini",
        ):
            if not path.is_file():
                fail(f"{label} replica {number} lacks {path.name}")
        if (run / "restore.exit").read_text().strip() != "0":
            fail(f"{label} replica {number} failed")
        log = (run / "restore.log").read_text(errors="replace")
        stats = (run / "stats.txt").read_text(errors="replace")
        ticks = [int(value) for value in SIM_TICKS_RE.findall(stats)]
        if (
            FATAL_RE.search(log)
            or len(EXIT_RE.findall(log)) != 1
            or log.splitlines().count("ROI End!!!") != 1
            or len(ticks) != 2
            or ticks[0] <= 0
            or ticks[1] < ticks[0]
        ):
            fail(f"{label} replica {number} has invalid ROI evidence")
        if (
            f"MAA gather execution {exact_length}/{manifest['workload_chunk_elements']}"
            not in log
        ):
            fail(f"{label} replica {number} executed the wrong range")
        raw = {
            field: first_stat(stats, name)
            for field, name in STAT_FIELDS.items()
        }
        for field in RESULT_FIELDS[1:]:
            expected = (
                2
                if field == "stats_blocks"
                else (
                    ticks[0]
                    if field == "roi_simTicks"
                    else (
                        ticks[1] if field == "final_simTicks" else raw[field]
                    )
                )
            )
            if int(result[field]) != expected:
                fail(
                    f"{label} replica {number} {field} differs from raw stats"
                )
        if raw["virtual_write_issues"] != raw["virtual_write_completions"]:
            fail(f"{label} replica {number} has incomplete virtual writes")

        config = configparser.RawConfigParser(strict=False)
        config.read(run / "config.ini")
        if not config.has_section("system.maa"):
            fail(f"{label} replica {number} has no MAA configuration")
        maa = config["system.maa"]
        if (
            int(maa["num_tile_elements"])
            != int(manifest["maa_logical_tile_elements"])
            or int(maa["physical_tile_elements"])
            != int(manifest["physical_tile_elements"])
            or int(maa["virtual_index_buffer_lines"])
            != int(manifest["virtual_index_buffer_lines"])
        ):
            fail(
                f"{label} replica {number} configuration differs from manifest"
            )
        try:
            dram_by_channel = parse_log(run / "restore.log", channels)
        except ValueError as error:
            fail(f"{label} replica {number}: {error}")
        dram = {
            command: sum(
                values[command] for values in dram_by_channel.values()
            )
            for command in COMMANDS
        }
        replica_data.append({"roi_simTicks": ticks[0], **raw, **dram})

    tick_values = {int(row["roi_simTicks"]) for row in replica_data}
    if len(tick_values) != 1:
        fail(f"{label} replicas are not deterministic: {sorted(tick_values)}")
    for field in STAT_FIELDS:
        if len({int(row[field]) for row in replica_data}) != 1:
            fail(f"{label} replicas disagree on {field}")

    first = replica_data[0]
    if arm in {"native", "fused", "fused_4k"}:
        if first["virtual_write_issues"] or first["direct_index_words"]:
            fail(f"{label} unexpectedly used virtual machinery")
    elif arm == "compact":
        if (
            not first["virtual_write_issues"]
            or first["direct_index_words"]
            or not first["indirect_spd_read_cycles"]
        ):
            fail(f"{label} did not use compact staged-index retirement")
    elif arm.startswith("direct_index"):
        if (
            not first["virtual_write_issues"]
            or first["direct_index_words"] != exact_length
            or first["indirect_spd_read_cycles"]
        ):
            fail(f"{label} did not use direct-index retirement")
    else:
        fail(f"{label} has unsupported arm {arm}")

    performance_gem5_sha = gem5_hash(artifacts, label)
    if performance_gem5_sha != exact_gem5_sha:
        fail(f"{label} gem5 differs from its exact reference")
    return {
        "label": label,
        "arm": arm,
        "source_commit": manifest["source_commit"],
        "gem5_sha256": performance_gem5_sha,
        "binary_sha256": next(
            digest
            for path, digest in artifacts.items()
            if path == Path(manifest.get("binary", ""))
        )
        if "binary" in manifest
        else "",
        "input_sha256": input_sha,
        "exact_length": exact_length,
        "exact_hash": exact_hash,
        "logical_tile_elements": int(manifest["maa_logical_tile_elements"]),
        "physical_tile_elements": int(manifest["physical_tile_elements"]),
        "workload_chunk_elements": int(manifest["workload_chunk_elements"]),
        "index_buffer_lines": int(manifest["virtual_index_buffer_lines"]),
        "replicas": replicas,
        "roi_simTicks": int(statistics.median(tick_values)),
        **{field: int(first[field]) for field in STAT_FIELDS},
        "dram_reads": int(first["RD"]),
        "dram_activates": int(first["ACT"]),
        "dram_precharges": int(first["PRE"]),
    }


def parse_run(value: str) -> tuple[str, Path]:
    label, separator, path = value.partition("=")
    if not separator or not re.fullmatch(r"[A-Za-z0-9_.-]+", label):
        raise argparse.ArgumentTypeError("run must have the form LABEL=PATH")
    return label, Path(path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-channels", type=int, default=2)
    parser.add_argument("runs", nargs="+", type=parse_run)
    args = parser.parse_args()
    labels = [label for label, _ in args.runs]
    if len(labels) != len(set(labels)) or args.baseline not in labels:
        parser.error("labels must be unique and include --baseline")
    rows = [
        read_run(label, path, args.expected_channels)
        for label, path in args.runs
    ]
    reference = rows[0]
    for row in rows[1:]:
        for field in (
            "gem5_sha256",
            "input_sha256",
            "exact_length",
            "exact_hash",
        ):
            if row[field] != reference[field]:
                fail(
                    f"{row['label']} differs from {reference['label']} in {field}"
                )
    baseline = next(row for row in rows if row["label"] == args.baseline)
    baseline_ticks = int(baseline["roi_simTicks"])
    for row in rows:
        ticks = int(row["roi_simTicks"])
        row["latency_delta_vs_baseline_pct"] = (
            ticks / baseline_ticks - 1
        ) * 100
        row["throughput_delta_vs_baseline_pct"] = (
            baseline_ticks / ticks - 1
        ) * 100

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    with (output / "xrage_performance.tsv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(stream, delimiter="\t", fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        "# XRAGE Non-Verifier Performance",
        "",
        "Correctness comes from separate exact-verifier runs; timings contain only the application ROI.",
        "",
        "| Run | Arm | Logical | Physical | B lines | ROI ticks | Latency vs baseline |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['label']} | {row['arm']} | {row['logical_tile_elements']} | "
            f"{row['physical_tile_elements']} | {row['index_buffer_lines']} | "
            f"{row['roi_simTicks']} | {row['latency_delta_vs_baseline_pct']:+.6f}% |"
        )
    (output / "xrage_performance.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    (output / "xrage_performance.pass").touch()
    print(f"PASS XRAGE non-verifier comparison: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
