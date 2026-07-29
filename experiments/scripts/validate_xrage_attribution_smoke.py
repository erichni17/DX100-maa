#!/usr/bin/env python3
"""Validate XRAGE attribution arms from raw artifacts, then summarize them."""

import configparser
import csv
import datetime
import hashlib
import re
import sys
from pathlib import Path

ARMS = {
    "native": (16384, 16384, 16384),
    "fused": (16384, 16384, 16384),
    "compact": (16384, 16384, 16384),
    "direct_index_16k": (16384, 16384, 16384),
    "direct_index_4k": (4096, 16384, 16384),
    "fused_4k": (4096, 4096, 4096),
}
RESULT_FIELDS = [
    "output_hash",
    "roi_simTicks",
    "final_simTicks",
    "stats_blocks",
    "virtual_write_issues",
    "virtual_write_completions",
    "virtual_pages_ready",
    "direct_index_words",
    "indirect_spd_read_cycles",
]
FATAL_RE = re.compile(
    r"panic|fatal|segmentation fault|MAA_GATHER_VERIFY_FAIL", re.IGNORECASE
)
PASS_RE = re.compile(
    r"^MAA_GATHER_VERIFY_PASS length=(\d+) hash=(\d+)$", re.MULTILINE
)
EXIT_RE = re.compile(
    r"Exiting @ tick \d+ because m5_exit instruction encountered"
)
STAT_RE = re.compile(r"^simTicks\s+(\d+)\s+", re.MULTILINE)
MECHANISM_STATS = {
    "virtual_write_issues": "system.maa.I0_IND_VirtWriteIssues",
    "virtual_write_completions": "system.maa.I0_IND_VirtWriteCompletions",
    "virtual_pages_ready": "system.maa.I0_IND_VirtPagesReady",
    "direct_index_words": "system.maa.I0_IND_VirtIndexWords",
    "indirect_spd_read_cycles": "system.maa.I0_IND_CyclesSPDReadAccess",
}
DIAGNOSTIC_STATS = {
    "cache_read_packets": "system.maa.port_cache_RD_packets",
    "cache_write_packets": "system.maa.port_cache_WR_packets",
    "memory_read_packets": "system.maa.port_mem_RD_packets",
    "indirect_fill_cycles": "system.maa.I0_IND_CyclesFill",
    "indirect_request_cycles": "system.maa.I0_IND_CyclesRequest",
    "source_words": "system.maa.I0_IND_NumWordsInserted",
    "source_cache_lines": "system.maa.I0_IND_NumCacheLineInserted",
    "source_rows": "system.maa.I0_IND_NumRowsInserted",
    "unique_source_words": "system.maa.I0_IND_NumUniqueWordsInserted",
    "unique_source_cache_lines": "system.maa.I0_IND_NumUniqueCacheLineInserted",
    "unique_source_rows": "system.maa.I0_IND_NumUniqueRowsInserted",
    "row_table_full_events": "system.maa.I0_IND_NumRTFull",
    "indirect_memory_reads": "system.maa.I0_IND_LoadsMemAccessing",
    "direct_index_line_reads": "system.maa.I0_IND_VirtIndexLineReads",
    "virtual_build_rounds": "system.maa.I0_IND_VirtBuildRounds",
}
DIAGNOSTIC_LOG_COUNTERS = {
    "dram_reads": "CH0_num_RD_commands_T",
    "dram_activates": "CH0_num_ACT_commands_T",
    "dram_precharges": "CH0_num_PRE_commands_T",
}
REORDER_DIAGNOSTICS = (
    "source_words",
    "source_cache_lines",
    "source_rows",
    "unique_source_words",
    "unique_source_cache_lines",
    "unique_source_rows",
    "row_table_full_events",
)
COMPARISONS = [
    ("fusion", "native", "fused"),
    ("compact_bypass", "fused", "compact"),
    ("direct_index_delta", "compact", "direct_index_16k"),
    ("physical_spd_16k_to_4k", "direct_index_16k", "direct_index_4k"),
    ("logical_reorder_16k_to_4k", "fused", "fused_4k"),
    ("direct_4k_vs_native_4k", "fused_4k", "direct_index_4k"),
]
CACHE_LINE_BYTES = 64
XRAGE_WORD_BYTES = 8
VIRTUAL_ARMS = {"compact", "direct_index_16k", "direct_index_4k"}
DIRECT_INDEX_ARMS = {"direct_index_16k", "direct_index_4k"}


def fail(message):
    raise SystemExit(f"XRAGE attribution validation failed: {message}")


def read_kv(path):
    values = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if not sep or not key:
            fail(f"malformed manifest line in {path}: {line!r}")
        values[key] = value
    return values


def read_result(path):
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(rows) != 1 or list(rows[0]) != RESULT_FIELDS:
        fail(f"invalid result schema or row count in {path}")
    return rows[0]


def verify_artifacts(path, digest_cache):
    for line in path.read_text().splitlines():
        expected, separator, filename = line.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", expected):
            fail(f"malformed checksum line in {path}: {line!r}")
        artifact = Path(filename)
        if not artifact.is_file():
            fail(f"missing checksummed artifact: {artifact}")
        cache_key = (
            artifact,
            artifact.stat().st_size,
            artifact.stat().st_mtime_ns,
        )
        actual = digest_cache.get(cache_key)
        if actual is None:
            digest = hashlib.sha256()
            with artifact.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            actual = digest.hexdigest()
            digest_cache[cache_key] = actual
        if actual != expected:
            fail(f"checksum mismatch for {artifact}")


def require_mechanism(arm, row):
    values = {key: int(row[key]) for key in RESULT_FIELDS[1:]}
    writes = values["virtual_write_issues"]
    completions = values["virtual_write_completions"]
    pages = values["virtual_pages_ready"]
    index_words = values["direct_index_words"]
    spd_reads = values["indirect_spd_read_cycles"]
    if writes != completions:
        fail(f"{arm} has {writes} write issues but {completions} completions")
    if arm in ("native", "fused", "fused_4k"):
        if writes != 0 or pages != 0 or index_words != 0:
            fail(f"{arm} unexpectedly activated virtual machinery")
    elif arm == "compact":
        if writes <= 0 or pages <= 0 or index_words != 0 or spd_reads <= 0:
            fail("compact arm did not use staged-index virtual retirement")
    else:
        if writes <= 0 or pages <= 0 or index_words != 20000 or spd_reads != 0:
            fail(f"{arm} did not use direct-index virtual retirement")


def require_raw_mechanism(stats, arm, row):
    for result_field, stat_name in MECHANISM_STATS.items():
        match = re.search(
            rf"^{re.escape(stat_name)}\s+(\d+)\s+", stats, re.MULTILINE
        )
        raw_value = int(match.group(1)) if match else 0
        if int(row[result_field]) != raw_value:
            fail(
                f"{arm} {result_field}={row[result_field]} "
                f"does not match raw {raw_value}"
            )


def read_first_stat(stats, name):
    match = re.search(rf"^{re.escape(name)}\s+(\d+)\s+", stats, re.MULTILINE)
    return int(match.group(1)) if match else 0


def read_final_log_counter(log, name):
    matches = re.findall(
        rf"^\s*{re.escape(name)}:\s+([0-9]+)(?:\s+#.*)?$",
        log,
        re.MULTILINE,
    )
    if not matches:
        fail(f"restore log has no {name} counter")
    return int(matches[-1])


def configured_payload_storage(arm, maa):
    logical = int(maa["num_tile_elements"])
    physical = int(maa["physical_tile_elements"])
    spd_banks = int(maa["num_tiles_per_core"]) * int(maa["num_cores"])
    spd_bytes = spd_banks * physical * 4
    virtual_active = arm in VIRTUAL_ARMS

    combine_slots = int(maa["virtual_combine_slots"])
    combine_words = int(maa["virtual_combine_words"])
    combine_bytes = combine_slots * CACHE_LINE_BYTES
    if combine_words:
        combine_bytes = min(combine_bytes, combine_words * XRAGE_WORD_BYTES)

    response_slots = int(maa["virtual_response_slots"])
    response_words = int(maa["virtual_response_words"])
    response_pool = int(maa["virtual_response_word_pool"])
    if response_pool:
        response_bytes = min(
            response_slots * CACHE_LINE_BYTES,
            response_pool * XRAGE_WORD_BYTES,
        )
    elif response_words:
        response_bytes = min(
            response_slots * CACHE_LINE_BYTES,
            response_slots * response_words * XRAGE_WORD_BYTES,
        )
    else:
        response_bytes = response_slots * CACHE_LINE_BYTES

    index_bytes = (
        int(maa["virtual_index_buffer_lines"]) * CACHE_LINE_BYTES
        if arm in DIRECT_INDEX_ARMS
        else 0
    )
    active_combine_bytes = combine_bytes if virtual_active else 0
    active_response_bytes = response_bytes if virtual_active else 0
    active_virtual_bytes = (
        active_combine_bytes + active_response_bytes + index_bytes
    )
    return {
        "arm": arm,
        "logical_elements": logical,
        "physical_elements": physical,
        "total_spd_banks": spd_banks,
        "spd_payload_bytes": spd_bytes,
        "active_virtual_combiner_payload_bytes": active_combine_bytes,
        "active_virtual_response_payload_bytes": active_response_bytes,
        "active_virtual_index_payload_bytes": index_bytes,
        "active_virtual_payload_bytes": active_virtual_bytes,
        "modeled_active_data_payload_bytes": spd_bytes + active_virtual_bytes,
        "reorder_entries_per_indirect_unit": logical,
    }


def main():
    if len(sys.argv) != 2:
        fail("usage: validate_xrage_attribution_smoke.py RESULT_ROOT")
    root = Path(sys.argv[1]).resolve()
    digest_cache = {}
    rows = []
    diagnostics = []
    storage = []
    expected_hash = None
    expected_commit = None

    for arm, (physical, maa_logical, workload_chunk) in ARMS.items():
        arm_root = root / arm
        manifest = read_kv(arm_root / "manifest.txt")
        if manifest.get("arm") != arm:
            fail(f"{arm} manifest identifies {manifest.get('arm')!r}")
        if int(manifest.get("physical_tile_elements", -1)) != physical:
            fail(f"{arm} physical tile does not equal {physical}")
        if int(manifest.get("maa_logical_tile_elements", -1)) != maa_logical:
            fail(f"{arm} MAA logical capacity does not equal {maa_logical}")
        if int(manifest.get("workload_chunk_elements", -1)) != workload_chunk:
            fail(f"{arm} workload chunk does not equal {workload_chunk}")
        commit = manifest.get("source_commit")
        if not commit:
            fail(f"{arm} has no source commit")
        if expected_commit is None:
            expected_commit = commit
        elif commit != expected_commit:
            fail(f"{arm} source commit differs from {expected_commit}")
        if (arm_root / "source_status.txt").read_text():
            fail(f"{arm} source worktree was dirty")
        if (arm_root / "checkpoint.exit").read_text().strip() != "0":
            fail(f"{arm} checkpoint failed")
        if (arm_root / "restore.exit").read_text().strip() != "0":
            fail(f"{arm} restore failed")

        log = (arm_root / "restore.log").read_text(errors="replace")
        passes = PASS_RE.findall(log)
        if len(passes) != 1 or int(passes[0][0]) != 20000:
            fail(f"{arm} has invalid exact-verifier evidence")
        if FATAL_RE.search(log) or not EXIT_RE.search(log):
            fail(f"{arm} has a fatal marker or lacks terminal m5_exit")
        if f"MAA gather execution 20000/{workload_chunk}" not in log:
            fail(f"{arm} did not execute {workload_chunk}-element chunks")
        output_hash = passes[0][1]
        if expected_hash is None:
            expected_hash = output_hash
        elif output_hash != expected_hash:
            fail(f"{arm} hash {output_hash} differs from {expected_hash}")

        stats = (arm_root / "run" / "stats.txt").read_text()
        ticks = STAT_RE.findall(stats)
        if len(ticks) != 2:
            fail(f"{arm} has {len(ticks)} stats blocks instead of two")
        roi_ticks, final_ticks = map(int, ticks)
        if roi_ticks <= 0 or final_ticks < roi_ticks:
            fail(f"{arm} has invalid ROI/final ticks")

        result = read_result(arm_root / "result.tsv")
        if (
            result["output_hash"] != output_hash
            or int(result["roi_simTicks"]) != roi_ticks
            or int(result["final_simTicks"]) != final_ticks
            or int(result["stats_blocks"]) != 2
        ):
            fail(f"{arm} result.tsv does not match raw evidence")
        require_raw_mechanism(stats, arm, result)
        require_mechanism(arm, result)
        diagnostics.append(
            {
                "arm": arm,
                **{
                    field: read_first_stat(stats, stat_name)
                    for field, stat_name in DIAGNOSTIC_STATS.items()
                },
                **{
                    field: read_final_log_counter(log, counter_name)
                    for field, counter_name in DIAGNOSTIC_LOG_COUNTERS.items()
                },
            }
        )

        config = configparser.RawConfigParser(strict=False)
        config.read(arm_root / "run" / "config.ini")
        maa = config["system.maa"]
        if int(maa["num_tile_elements"]) != maa_logical:
            fail(f"{arm} config has wrong logical tile size")
        if int(maa["physical_tile_elements"]) != physical:
            fail(f"{arm} config has wrong physical tile size")
        manifest_grow_order = int(manifest.get("virtual_grow_order", "0"))
        if int(maa.getboolean("virtual_grow_order")) != manifest_grow_order:
            fail(f"{arm} config and manifest disagree on virtual grow order")
        manifest_native_order = int(
            manifest.get("virtual_native_issue_order", "0")
        )
        if manifest_grow_order not in {0, 1} or manifest_native_order not in {
            0,
            1,
        }:
            fail(f"{arm} has a non-boolean virtual issue-order mode")
        if manifest_grow_order and manifest_native_order:
            fail(f"{arm} enables mutually exclusive virtual issue-order modes")
        if (
            int(maa.getboolean("virtual_native_issue_order", fallback=False))
            != manifest_native_order
        ):
            fail(f"{arm} config and manifest disagree on native issue order")
        manifest_index_lines = int(
            manifest.get("virtual_index_buffer_lines", "1")
        )
        if int(maa["virtual_index_buffer_lines"]) != manifest_index_lines:
            fail(f"{arm} config and manifest disagree on index buffer lines")
        storage.append(configured_payload_storage(arm, maa))
        verify_artifacts(arm_root / "artifact_sha256.txt", digest_cache)
        rows.append({"arm": arm, **result})

    diagnostics_by_arm = {row["arm"]: row for row in diagnostics}
    for baseline, treatment in (
        ("native", "fused"),
        ("native", "direct_index_16k"),
        ("direct_index_16k", "direct_index_4k"),
    ):
        for field in REORDER_DIAGNOSTICS:
            baseline_value = diagnostics_by_arm[baseline][field]
            treatment_value = diagnostics_by_arm[treatment][field]
            if treatment_value != baseline_value:
                fail(
                    f"{treatment} {field}={treatment_value} differs from "
                    f"{baseline} {baseline_value}; source reorder accounting "
                    "is not matched"
                )

    fields = ["arm", *RESULT_FIELDS]
    with (root / "results.tsv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    diagnostic_fields = ["arm", *DIAGNOSTIC_STATS, *DIAGNOSTIC_LOG_COUNTERS]
    with (root / "mechanism.tsv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, diagnostic_fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(diagnostics)
    native_spd_bytes = next(
        row["spd_payload_bytes"] for row in storage if row["arm"] == "native"
    )
    for row in storage:
        saved = native_spd_bytes - row["modeled_active_data_payload_bytes"]
        row["modeled_payload_savings_vs_native_spd_bytes"] = saved
        row[
            "modeled_payload_savings_vs_native_spd_pct"
        ] = f"{100 * saved / native_spd_bytes:+.6f}"
    storage_fields = list(storage[0])
    with (root / "storage.tsv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, storage_fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(storage)
    (root / "storage_notes.txt").write_text(
        "Payload accounting only; this is not a synthesized area estimate.\n"
        "SPD payload is 4 bytes per configured bank element. XRAGE virtual "
        "payload assumes 8-byte values and applies configured slot/word "
        "limits.\n"
        "Excluded: tags, masks, queue/control bits, row-table storage, offset-"
        "table storage, ports, ALUs, wiring, and technology-dependent area.\n"
        "The offset/reorder table remains sized to logical_elements in every "
        "arm, even when the SPD payload is physically smaller.\n"
    )
    ticks_by_arm = {row["arm"]: int(row["roi_simTicks"]) for row in rows}
    comparison_fields = [
        "comparison",
        "baseline",
        "treatment",
        "baseline_simTicks",
        "treatment_simTicks",
        "latency_delta_pct",
        "throughput_delta_pct",
    ]
    comparisons = []
    for comparison, baseline, treatment in COMPARISONS:
        baseline_ticks = ticks_by_arm[baseline]
        treatment_ticks = ticks_by_arm[treatment]
        comparisons.append(
            {
                "comparison": comparison,
                "baseline": baseline,
                "treatment": treatment,
                "baseline_simTicks": baseline_ticks,
                "treatment_simTicks": treatment_ticks,
                "latency_delta_pct": (
                    f"{100 * (treatment_ticks / baseline_ticks - 1):+.6f}"
                ),
                "throughput_delta_pct": (
                    f"{100 * (baseline_ticks / treatment_ticks - 1):+.6f}"
                ),
            }
        )
    with (root / "attribution.tsv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, comparison_fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(comparisons)
    validator_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (root / "validation_manifest.txt").write_text(
        f"source_commit={expected_commit}\n"
        f"validator_sha256={validator_hash}\n"
        "validated_utc="
        f"{datetime.datetime.now(datetime.timezone.utc).isoformat()}\n"
    )
    (root / "xrage_attribution_smoke_matrix.pass").touch()
    print(
        "PASS XRAGE attribution matrix: "
        f"commit={expected_commit} hash={expected_hash}"
    )


if __name__ == "__main__":
    main()
