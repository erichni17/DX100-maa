#!/usr/bin/env python3
"""Correctness-first promotion gate for the live four-run merge matrix."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from pathlib import Path

ARMS = ("native4", "current_paged4", "candidate")
INTEGER_FIELDS = {
    "simTicks",
    "physical_records",
    "source_issue_records",
    "source_issue_requests",
    "bounded_global_populations",
    "bounded_global_active_hwm",
    "bounded_global_descriptor_records",
    "bounded_global_descriptor_bytes",
    "bounded_global_sort_read_lines",
    "bounded_global_sorted_write_lines",
    "bounded_global_sort_comparisons",
    "bounded_global_merge_read_lines",
    "bounded_global_merge_comparisons",
    "bounded_global_head_hwm",
    "bounded_global_a_line_issues",
    "bounded_global_coalesced",
    "bounded_global_row_groups",
    "bounded_global_admissions",
    "bounded_global_retirements",
    "bounded_global_run_write_acks",
    "bounded_global_terminal_acks",
    "bounded_global_fallbacks",
    "bounded_global_backing_bytes",
}


def fail(message: str) -> None:
    raise SystemExit(f"bounded global merge evidence failure: {message}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_result(path: Path) -> dict[str, object]:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(rows) != 1:
        fail(f"{path} must contain exactly one result")
    result: dict[str, object] = dict(rows[0])
    missing = INTEGER_FIELDS - result.keys()
    if missing:
        fail(f"{path} lacks fields: {sorted(missing)}")
    for field in INTEGER_FIELDS:
        result[field] = int(str(result[field]))
    return result


def manifest(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


def reorder_semantics(path: Path) -> dict[str, object]:
    summaries = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if "event=reorder_summary" in line
    ]
    if not summaries:
        fail(f"{path} has no reorder terminal summary")
    totals = {
        "selected_descriptors": 0,
        "total_admitted": 0,
        "total_issued_entries": 0,
        "reconciled": 0,
    }
    for line in summaries:
        for key in totals:
            match = re.search(rf"\b{key}=(\d+)\b", line)
            if match is None:
                fail(f"reorder summary lacks {key}: {line}")
            totals[key] += int(match.group(1))
    canonical = "\n".join(sorted(summaries)).encode()
    totals["records"] = len(summaries)
    totals["sha256"] = hashlib.sha256(canonical).hexdigest()
    return totals


def main() -> None:
    if len(sys.argv) != 3:
        fail("usage: analyze_bounded_global_merge.py MATRIX_DIR OUTPUT_JSON")
    matrix = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve()
    results = {arm: read_result(matrix / arm / "result.tsv") for arm in ARMS}
    manifests = {arm: manifest(matrix / arm / "manifest.txt") for arm in ARMS}
    for arm in ARMS:
        if not (matrix / arm / "virtual_tile_consumer_case.pass").is_file():
            fail(f"{arm} lacks the runner completion marker")
        if results[arm]["simTicks"] <= 0:
            fail(f"{arm} has no positive simTicks")
        expected_physical_records = 0 if arm == "native4" else 16384
        if results[arm]["physical_records"] != expected_physical_records:
            fail(
                f"{arm} physical admission count is not "
                f"{expected_physical_records}"
            )

    output_hashes = {str(results[arm]["output_hash"]) for arm in ARMS}
    if len(output_hashes) != 1:
        fail(f"exact output hashes differ: {sorted(output_hashes)}")
    physical_hashes = {
        str(results[arm]["physical_record_sha256"])
        for arm in ARMS
        if arm != "native4"
    }
    if len(physical_hashes) != 1:
        fail("physical source/admission digests differ")
    bounded_hashes = {
        str(results[arm]["bounded_summary_histogram_sha256"])
        for arm in ("current_paged4", "candidate")
    }
    if len(bounded_hashes) != 1 or "none" in bounded_hashes:
        fail("candidate and bounded control summary digests differ")

    gem5_hash = sha256(matrix / "input" / "gem5.opt")
    workload_hash = sha256(matrix / "input" / "workload")
    ramulator_hash = sha256(matrix / "input" / "libramulator.so")
    checkpoint_hashes = set()
    source_diff_hashes = set()
    source_commits = set()
    for arm in ARMS:
        checkpoint_hashes.add(
            (matrix / arm / "shared_checkpoint_identity.sha256")
            .read_text(encoding="utf-8")
            .split()[0]
        )
        source_diff_hashes.add(sha256(matrix / arm / "source.diff"))
        source_commits.add(manifests[arm].get("gem5_source_commit", ""))
        if (matrix / arm / "source.diff").stat().st_size != 0:
            fail(f"{arm} was not launched from the clean checkpointed source")
        if (matrix / arm / "source_status.txt").read_text(encoding="utf-8"):
            fail(f"{arm} recorded a non-clean source status")
    if len(checkpoint_hashes) != 1:
        fail("arms do not restore the same treatment-neutral checkpoint")
    if len(source_diff_hashes) != 1 or len(source_commits) != 1:
        fail("arms do not share identical simulator source provenance")

    candidate = results["candidate"]
    expected = {
        "bounded_global_populations": 4,
        "bounded_global_active_hwm": 4096,
        "bounded_global_descriptor_records": 16384,
        "bounded_global_descriptor_bytes": 98304,
        "bounded_global_sort_read_lines": 1152,
        "bounded_global_sorted_write_lines": 1536,
        "bounded_global_merge_read_lines": 1536,
        "bounded_global_head_hwm": 4,
        "bounded_global_admissions": 16384,
        "bounded_global_retirements": 16384,
        "bounded_global_run_write_acks": 1536,
        "bounded_global_fallbacks": 0,
        "bounded_global_backing_bytes": 98304,
    }
    mismatches = {
        key: {"expected": value, "observed": candidate[key]}
        for key, value in expected.items()
        if candidate[key] != value
    }
    if mismatches:
        fail(f"candidate structural mismatch: {mismatches}")
    if (
        candidate["bounded_global_a_line_issues"]
        + candidate["bounded_global_coalesced"]
        != 16384
    ):
        fail("candidate A-line issue/coalescing accounting does not close")
    if candidate["bounded_global_terminal_acks"] <= 0:
        fail("candidate terminal ACK counter is empty")
    if candidate["bounded_global_sort_comparisons"] <= 0:
        fail("candidate Row/Offset sort comparison counter is empty")
    if candidate["bounded_global_merge_comparisons"] <= 0:
        fail("candidate four-head comparison counter is empty")

    reorder = {
        arm: reorder_semantics(matrix / arm / "run" / "virtual_trace.log")
        for arm in ARMS
    }
    for arm, semantics in reorder.items():
        for key in (
            "selected_descriptors",
            "total_admitted",
            "total_issued_entries",
        ):
            if semantics[key] != 16384:
                fail(f"{arm} reorder {key} is {semantics[key]}, not 16384")
        if semantics["reconciled"] != semantics["records"]:
            fail(f"{arm} has an unreconciled reorder summary")

    current_ticks = int(results["current_paged4"]["simTicks"])
    candidate_ticks = int(candidate["simTicks"])
    promoted = candidate_ticks < current_ticks
    payload = {
        "schema": "dx100.bounded_global_merge.live.v1",
        "promotion": {
            "promoted": promoted,
            "reason": (
                "candidate passed structural/correctness gates and improved simTicks"
                if promoted
                else "candidate passed structural/correctness gates but did not improve simTicks"
            ),
            "candidate_simTicks": candidate_ticks,
            "current_paged4_simTicks": current_ticks,
            "delta_simTicks": candidate_ticks - current_ticks,
        },
        "model_comparison": {
            "predicted_a_line_issues": 9523,
            "observed_a_line_issues": candidate[
                "bounded_global_a_line_issues"
            ],
            "a_line_delta": candidate["bounded_global_a_line_issues"] - 9523,
            "predicted_row_groups": 129,
            "observed_row_groups": candidate["bounded_global_row_groups"],
            "row_group_delta": candidate["bounded_global_row_groups"] - 129,
            "interpretation": "live RowTable and scheduler ordering is authoritative",
        },
        "provenance": {
            "source_commit": next(iter(source_commits)),
            "source_diff_sha256": next(iter(source_diff_hashes)),
            "gem5_sha256": gem5_hash,
            "workload_sha256": workload_hash,
            "ramulator_sha256": ramulator_hash,
            "checkpoint_sha256": next(iter(checkpoint_hashes)),
        },
        "digests": {
            "output_hash": next(iter(output_hashes)),
            "physical_source_sha256": next(iter(physical_hashes)),
            "bounded_summary_sha256": next(iter(bounded_hashes)),
            "source_issue_sha256": {
                arm: results[arm]["source_issue_sha256"] for arm in ARMS
            },
            "reorder": reorder,
        },
        "arms": results,
    }
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with (matrix / "summary.tsv").open("w", encoding="utf-8") as stream:
        stream.write(
            "arm\tsimTicks\toutput_hash\tfallbacks\ta_line_issues\trow_groups\n"
        )
        for arm in ARMS:
            row = results[arm]
            stream.write(
                f"{arm}\t{row['simTicks']}\t{row['output_hash']}\t"
                f"{row['bounded_global_fallbacks']}\t"
                f"{row['bounded_global_a_line_issues']}\t"
                f"{row['bounded_global_row_groups']}\n"
            )
    print(json.dumps(payload["promotion"], sort_keys=True))


if __name__ == "__main__":
    main()
