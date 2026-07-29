#!/usr/bin/env python3
"""Validate XRAGE attribution arms from raw artifacts, then summarize them."""

import configparser
import csv
import hashlib
import re
import sys
from pathlib import Path

ARMS = {
    "native": (16384, 16384),
    "fused": (16384, 16384),
    "compact": (16384, 16384),
    "direct_index_16k": (16384, 16384),
    "direct_index_4k": (4096, 16384),
    "fused_4k": (4096, 4096),
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
    "direct_index_cache_responses",
    "direct_index_mem_responses",
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
    "direct_index_cache_responses": "system.maa.I0_IND_VirtIndexCacheResponses",
    "direct_index_mem_responses": "system.maa.I0_IND_VirtIndexMemResponses",
    "indirect_spd_read_cycles": "system.maa.I0_IND_CyclesSPDReadAccess",
}


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


def require_mechanism(arm, row, direct_index_force_cache):
    values = {key: int(row[key]) for key in RESULT_FIELDS[1:]}
    writes = values["virtual_write_issues"]
    completions = values["virtual_write_completions"]
    pages = values["virtual_pages_ready"]
    index_words = values["direct_index_words"]
    index_cache_responses = values["direct_index_cache_responses"]
    index_mem_responses = values["direct_index_mem_responses"]
    spd_reads = values["indirect_spd_read_cycles"]
    if writes != completions:
        fail(f"{arm} has {writes} write issues but {completions} completions")
    if arm in ("native", "fused", "fused_4k"):
        if (
            writes != 0
            or pages != 0
            or index_words != 0
            or index_cache_responses != 0
            or index_mem_responses != 0
        ):
            fail(f"{arm} unexpectedly activated virtual machinery")
    elif arm == "compact":
        if (
            writes <= 0
            or pages <= 0
            or index_words != 0
            or spd_reads <= 0
            or index_cache_responses != 0
            or index_mem_responses != 0
        ):
            fail("compact arm did not use staged-index virtual retirement")
    else:
        expected_cache = 20000 // 16 if direct_index_force_cache else 0
        expected_mem = 0 if direct_index_force_cache else 20000 // 16
        if (
            writes <= 0
            or pages <= 0
            or index_words != 20000
            or index_cache_responses != expected_cache
            or index_mem_responses != expected_mem
            or spd_reads != 0
        ):
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


def main():
    if len(sys.argv) != 2:
        fail("usage: validate_xrage_attribution_smoke.py RESULT_ROOT")
    root = Path(sys.argv[1]).resolve()
    digest_cache = {}
    rows = []
    expected_hash = None
    expected_commit = None

    for arm, (physical, workload_chunk) in ARMS.items():
        arm_root = root / arm
        manifest = read_kv(arm_root / "manifest.txt")
        if manifest.get("arm") != arm:
            fail(f"{arm} manifest identifies {manifest.get('arm')!r}")
        if int(manifest.get("physical_tile_elements", -1)) != physical:
            fail(f"{arm} physical tile does not equal {physical}")
        if int(manifest.get("maa_logical_tile_elements", -1)) != 16384:
            fail(f"{arm} MAA logical capacity does not equal 16384")
        if int(manifest.get("workload_chunk_elements", -1)) != workload_chunk:
            fail(f"{arm} workload chunk does not equal {workload_chunk}")
        direct_index_force_cache = manifest.get(
            "direct_index_force_cache", "0"
        )
        if direct_index_force_cache not in ("0", "1"):
            fail(f"{arm} has invalid direct-index route selector")
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
        require_mechanism(arm, result, direct_index_force_cache == "1")

        config = configparser.RawConfigParser(strict=False)
        config.read(arm_root / "run" / "config.ini")
        maa = config["system.maa"]
        if int(maa["num_tile_elements"]) != 16384:
            fail(f"{arm} config has wrong logical tile size")
        if int(maa["physical_tile_elements"]) != physical:
            fail(f"{arm} config has wrong physical tile size")
        verify_artifacts(arm_root / "artifact_sha256.txt", digest_cache)
        rows.append({"arm": arm, **result})

    fields = ["arm", *RESULT_FIELDS]
    with (root / "results.tsv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    (root / "xrage_attribution_smoke_matrix.pass").touch()
    print(
        "PASS XRAGE attribution matrix: "
        f"commit={expected_commit} hash={expected_hash}"
    )


if __name__ == "__main__":
    main()
