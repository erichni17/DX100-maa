#!/usr/bin/env python3
"""Fail-closed audit for bounded issue-ready hybrid-tail pairs."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_hybrid_tail_instrumented import (  # noqa: E402
    audit_pair as audit_attribution_pair,
)
from hybrid_overhead_attribution import (  # noqa: E402
    EXPECTED_OUTPUT_HASH,
    AuditError,
    audit_run,
    sha256,
)

ARMS = ("transparent_4k", "transparent_issue_ready_4k")
SERIALIZATION_RECORD = (
    "ordinal\tarm\tselector_absent_before\tselector_absent_after\n"
    "1\ttransparent_4k\t1\t1\n"
    "2\ttransparent_issue_ready_4k\t1\t1\n"
)
ACCEPTED_WORKLOAD_SHA256 = (
    "20fe15ca32cf6e307801fda427ac430bd99148be500647acf4cefb0959635880"
)
MATCHED_RESULT_FIELDS = (
    "output_hash",
    "simInsts",
    "index_line_reads",
    "index_words",
    "index_hwm",
    "feeder_descriptor_discards",
    "feeder_predicate_discards",
    "feeder_partition_discards",
    "physical_records",
    "physical_record_sha256",
    "index_filter_words",
    "index_filter_cycles",
    "index_filter_wait_events",
    "index_filter_wait_cycles",
    "write_issues",
    "write_completions",
    "indirect_spd_reads",
    "pages_ready",
    "pages_ready_before_source_drain",
    "stream_writes",
    "alu_compute_cycles",
    "page_ready_signals",
    "page_wait_reads",
    "page_wait_deferrals",
    "page_wait_responses",
    "row_table_slices",
    "row_table_rows_per_slice",
    "row_table_entries_per_subslice_row",
    "virtual_grow_order",
    "virtual_index_partitions",
    "virtual_index_range_passes",
    "virtual_index_range_policy",
    "virtual_index_range_boundaries",
    "virtual_index_force_cache",
    "virtual_partition_keep_combiner",
    "offset_table_entries",
    "offset_table_epoch_entries",
    "transparent_spd_mode",
    "virtual_index_filter_words_per_cycle",
    "require_index_filter_wait",
    "response_slots",
    "response_word_pool",
    "virtual_retirement_forward_latency_cycles",
    "virtual_retirement_forward_lines_per_cycle",
    "num_maas",
    "num_indirect_units_per_maa",
)


def require_exact(path: Path, value: bytes) -> None:
    if not path.is_file() or path.is_symlink() or path.read_bytes() != value:
        raise AuditError(f"{path}: missing or wrong fail-closed marker")


def canonical_int(result: dict[str, str], field: str) -> int:
    value = result.get(field, "")
    if not value.isdigit() or (len(value) > 1 and value.startswith("0")):
        raise AuditError(f"malformed result integer {field}={value!r}")
    return int(value)


def audit_page_readiness(path: Path, expect_pending: bool) -> dict:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(rows) != 4 or None in rows[0]:
        raise AuditError(f"{path}: expected four rectangular page rows")
    pages = [canonical_int(row, "page") for row in rows]
    if pages != [0, 1, 2, 3]:
        raise AuditError(f"{path}: noncanonical page order")
    pending = []
    for row in rows:
        if row.get("total_pages") != "4" or row.get("ready_count") != str(
            int(row["page"]) + 1
        ):
            raise AuditError(f"{path}: page-ready continuity mismatch")
        issued = canonical_int(row, "issued_words")
        completed = canonical_int(row, "completed_words")
        if completed > issued:
            raise AuditError(f"{path}: completion exceeds issue")
        pending.append(issued - completed)
    if expect_pending and not any(pending):
        raise AuditError(f"{path}: candidate exposed no pending data")
    if not expect_pending and any(pending):
        raise AuditError(f"{path}: control exposed pending data")
    return {"pending_words_by_page": pending, "pending_words": sum(pending)}


def audit_config_delta(control_path: Path, candidate_path: Path) -> dict:
    control = control_path.read_bytes()
    candidate = candidate_path.read_bytes()
    off = b"virtual_page_ready_on_issue=false\n"
    on = b"virtual_page_ready_on_issue=true\n"
    if control.count(off) != 1 or control.count(on) != 0:
        raise AuditError("control config has wrong issue-ready setting")
    if candidate.count(on) != 1 or candidate.count(off) != 0:
        raise AuditError("candidate config has wrong issue-ready setting")
    if control.replace(off, on) != candidate:
        raise AuditError("resolved configs differ beyond issue-ready setting")
    for required in (
        b"num_tile_elements=16384\n",
        b"physical_tile_elements=4096\n",
        b"virtual_max_outstanding_writes=64\n",
        b"num_indirect_units_per_maa=1\n",
        b"num_maas=1\n",
        b"virtual_retirement_forward_latency=1\n",
    ):
        if control.count(required) != 1 or candidate.count(required) != 1:
            raise AuditError(f"missing matched config geometry {required!r}")
    return {
        "only_resolved_config_delta": "virtual_page_ready_on_issue=false->true",
        "control_config_sha256": sha256(control_path),
        "candidate_config_sha256": sha256(candidate_path),
    }


def audit_pair(root: Path) -> dict:
    require_exact(root / "pair.exit", b"0\n")
    require_exact(root / "pair.complete", b"")
    require_exact(root / "shared-checkpoint.exit", b"0\n")
    require_exact(
        root / "unlike_arms.serialized.tsv", SERIALIZATION_RECORD.encode()
    )
    if (root / "shared_treatment.txt").exists():
        raise AuditError("shared treatment selector survived serialized pair")

    runs = {arm: audit_run(root / arm) for arm in ARMS}
    control = runs[ARMS[0]]
    candidate = runs[ARMS[1]]
    if [runs[arm]["result"]["case"] for arm in ARMS] != list(ARMS):
        raise AuditError("control/candidate case identity mismatch")
    if {runs[arm]["result"]["output_hash"] for arm in ARMS} != {
        EXPECTED_OUTPUT_HASH
    }:
        raise AuditError("control/candidate exact output mismatch")
    source_commit = (root / "input/source_commit").read_text().strip()
    if any(
        run["manifest"]["source_commit"] != source_commit
        for run in runs.values()
    ):
        raise AuditError("pair source commit mismatch")
    if len({run["checkpoint_identity_sha256"] for run in runs.values()}) != 1:
        raise AuditError("arms did not restore one checkpoint")
    authoritative = (
        root / "checkpoint_files.pre_treatment.sha256"
    ).read_bytes()
    for arm in ARMS:
        if (
            root / arm / "shared_checkpoint_files.sha256"
        ).read_bytes() != authoritative:
            raise AuditError(f"{arm}: checkpoint mutated")

    for label in (
        "gem5_binary",
        "workload_binary",
        "ramulator_library",
        "ramulator_provenance",
    ):
        if (
            control["frozen_artifacts"][label]["sha256"]
            != candidate["frozen_artifacts"][label]["sha256"]
        ):
            raise AuditError(f"frozen artifact mismatch: {label}")
    workload_sha = control["frozen_artifacts"]["workload_binary"]["sha256"]
    if workload_sha != ACCEPTED_WORKLOAD_SHA256:
        raise AuditError("pair did not reuse the accepted exact workload")
    if (
        control["dynamic_links"]["normalized_sha256"]
        != candidate["dynamic_links"]["normalized_sha256"]
    ):
        raise AuditError("dynamic-link resolutions differ")

    control_result = control["result"]
    candidate_result = candidate["result"]
    for field in MATCHED_RESULT_FIELDS:
        if control_result.get(field) != candidate_result.get(field):
            raise AuditError(f"unmatched result field: {field}")
    if (
        control_result.get("virtual_page_ready_on_issue") != "0"
        or candidate_result.get("virtual_page_ready_on_issue") != "1"
    ):
        raise AuditError("result treatment flag mismatch")
    if canonical_int(control_result, "index_words") != 16384:
        raise AuditError("full 16K reorder metadata was not preserved")
    if canonical_int(control_result, "physical_records") != 16384:
        raise AuditError("physical admission record count changed")
    if canonical_int(control_result, "write_issues") != canonical_int(
        control_result, "write_completions"
    ):
        raise AuditError("control retirement is incomplete")

    control_pages = audit_page_readiness(
        root / ARMS[0] / "page_readiness.tsv", False
    )
    candidate_pages = audit_page_readiness(
        root / ARMS[1] / "page_readiness.tsv", True
    )
    pending = canonical_int(candidate_result, "page_ready_pending_words")
    if pending != candidate_pages["pending_words"] or pending == 0:
        raise AuditError("candidate pending-word counter does not reconcile")
    if canonical_int(candidate_result, "pages_ready_with_pending_writes") == 0:
        raise AuditError("candidate exposed no page with pending writes")
    forwards = canonical_int(
        candidate_result, "virtual_retirement_stream_forwards"
    )
    if forwards == 0:
        raise AuditError("candidate observed no bounded line forwarding")
    scheduled = canonical_int(
        candidate_result, "virtual_retirement_stream_forward_scheduled"
    )
    copied_bytes = canonical_int(
        candidate_result, "virtual_retirement_stream_forward_copy_bytes"
    )
    forward_hwm = canonical_int(
        candidate_result, "virtual_retirement_stream_forward_queue_hwm"
    )
    forward_full = canonical_int(
        candidate_result, "virtual_retirement_stream_forward_queue_full"
    )
    if (
        scheduled == 0
        or forwards != scheduled
        or copied_bytes != scheduled * 64
        or not 0 < forward_hwm <= 64
        or forward_full != 0
    ):
        raise AuditError("bounded scheduled-forward counters do not reconcile")
    for field in (
        "virtual_retirement_native_deferrals",
        "virtual_retirement_queue_deferrals",
    ):
        if canonical_int(candidate_result, field) != 0:
            raise AuditError(f"candidate used fallback serialization: {field}")
    for field in (
        "pages_ready_with_pending_writes",
        "page_ready_pending_words",
        "virtual_retirement_stream_forwards",
        "virtual_retirement_stream_forward_scheduled",
        "virtual_retirement_stream_forward_copy_bytes",
        "virtual_retirement_stream_forward_queue_hwm",
        "virtual_retirement_stream_forward_queue_full",
    ):
        if canonical_int(control_result, field) != 0:
            raise AuditError(
                f"control unexpectedly activated mechanism: {field}"
            )

    config_delta = audit_config_delta(
        root / ARMS[0] / "run/config.ini",
        root / ARMS[1] / "run/config.ini",
    )
    control_ticks = control["simTicks"]
    candidate_ticks = candidate["simTicks"]
    delta = control_ticks - candidate_ticks
    speedup = 100.0 * delta / control_ticks
    if delta <= 0:
        raise AuditError("candidate did not causally improve simTicks")
    return {
        "path": str(root.resolve()),
        "source_commit": source_commit,
        "checkpoint_identity_sha256": control["checkpoint_identity_sha256"],
        "output_hash": EXPECTED_OUTPUT_HASH,
        "simTicks": {"control": control_ticks, "candidate": candidate_ticks},
        "delta_simTicks": delta,
        "speedup_percent": speedup,
        "mechanism_counters": {
            "pages_ready_with_pending_writes": canonical_int(
                candidate_result, "pages_ready_with_pending_writes"
            ),
            "page_ready_pending_words": pending,
            "stream_line_forwards": forwards,
            "scheduled_line_forwards": scheduled,
            "forward_copy_bytes": copied_bytes,
            "forward_queue_high_water": forward_hwm,
            "forward_queue_full": forward_full,
            "scheduled_minus_delivered": scheduled - forwards,
            "queue_empty_at_stats": scheduled == forwards,
            "native_retirement_deferrals": 0,
            "queue_retirement_deferrals": 0,
        },
        "matched_geometry": {
            "reorder_metadata_elements": 16384,
            "physical_payload_elements": 4096,
            "retirement_buffer_lines": 64,
            "retirement_buffer_bytes": 4096,
            "active_indirect_units": 1,
            "forward_latency_cycles": 1,
            "forward_lines_per_cycle": 1,
            "physical_record_sha256": control_result["physical_record_sha256"],
        },
        "config_delta": config_delta,
        "hashes": {
            arm: {
                "result_tsv": runs[arm]["hashes"]["result.tsv"],
                "stats": runs[arm]["hashes"]["run/stats.txt"],
                "trace": runs[arm]["hashes"]["run/virtual_trace.log"],
            }
            for arm in ARMS
        },
        "artifacts": {
            label: control["frozen_artifacts"][label]["sha256"]
            for label in (
                "gem5_binary",
                "workload_binary",
                "ramulator_library",
                "ramulator_provenance",
            )
        },
        "completion": {arm: runs[arm]["completion"] for arm in ARMS},
        "page_readiness": {
            "control": control_pages,
            "candidate": candidate_pages,
        },
    }


def build_report(pairs: list[dict], accepted: dict) -> dict:
    speedups = [pair["speedup_percent"] for pair in pairs]
    if min(speedups) < 2.0 and len(pairs) < 3:
        raise AuditError(
            "effect under 2%; at least three matched pairs required"
        )
    accepted_pair = accepted["fresh_pair"]
    accepted_raw = audit_attribution_pair(Path(accepted_pair["path"]))
    if accepted_raw["delta_simTicks"] != accepted_pair["delta_simTicks"]:
        raise AuditError("accepted raw pair no longer matches accepted audit")
    tail = accepted_raw["tail"]
    post = tail["post_ready_blocker_ticks"]
    return {
        "schema": "dx100.hybrid_tail_issue_ready.v1",
        "status": "causal_bounded_forwarding_validated",
        "accepted_tail_reconfirmed": {
            "native_simTicks": accepted_raw["arms"]["native_direct_16k"][
                "simTicks"
            ],
            "transparent_simTicks": accepted_raw["arms"]["transparent_4k"][
                "simTicks"
            ],
            "delta_simTicks": accepted_raw["delta_simTicks"],
            "post_all_ready_ticks": tail["post_ready_total_ticks"],
            "stream_busy_ticks": post["stream_busy_ticks"],
            "alu_busy_ticks": post["alu_busy_ticks"],
            "stream_plus_alu_reconciles_exactly": (
                post["stream_busy_ticks"] + post["alu_busy_ticks"]
                == tail["post_ready_total_ticks"]
            ),
            "producer_writes_after_all_ready": tail["producer_backing_writes"][
                "post_ready_completions"
            ],
        },
        "pairs": pairs,
        "summary": {
            "pair_count": len(pairs),
            "mean_speedup_percent": statistics.fmean(speedups),
            "min_speedup_percent": min(speedups),
            "max_speedup_percent": max(speedups),
            "repetitions_required": min(speedups) < 2.0,
            "repetitions_satisfied": min(speedups) >= 2.0 or len(pairs) >= 3,
        },
        "hardware_delta": {
            "added_control": (
                "per-page unforwardable-write counts, exact-address pending-line "
                "lookup/hit, and a STREAM forwarding mux"
            ),
            "timing_model": {
                "forward_latency_cycles": 1,
                "forward_bandwidth": "one 64-byte line per MAA cycle",
            },
            "area_evidence": {
                "classification": "target_semantic_packed_lower_bound",
                "new_scheduled_forward_entries": 64,
                "new_copied_payload_bytes": 4096,
                "metadata_bits_per_entry": 151,
                "metadata_bytes_per_entry_raw": 18.875,
                "metadata_bytes_per_entry_rounded": 19,
                "metadata_fields": (
                    "64-bit line address, 64-bit due tick, 8-bit STREAM unit, "
                    "6-bit FIFO sequence, 1 valid bit, 8 ownership/order bits"
                ),
                "metadata_bytes_total_rounded": 1216,
                "treatment_only_lower_bound_bytes": 5312,
                "existing_retirement_payload_bound_bytes": 4096,
                "coexisting_payload_only_bytes": 8192,
                "combined_total_bytes": None,
                "combined_total_reason": (
                    "retirement-map metadata and current simulator container "
                    "allocations are not measured"
                ),
                "simulator_retirement_representation": (
                    "unordered-map node plus OutstandingPacket retaining "
                    "Packet pointer, paddr, tick, command, cached/retirement/"
                    "sent flags, and dynamic requester/unit vectors"
                ),
                "simulator_scheduled_representation": (
                    "allocated 64-byte Packet/Request plus multiset node "
                    "retaining STREAM id, packet pointer, due tick and ordinal, "
                    "and a separate unordered address-set node"
                ),
                "simulator_allocation_bytes": None,
            },
            "teardown_safety": (
                "scheduled=delivered proves the table/address set drained at "
                "stats; the destructor panics if queue, address set, or event "
                "remains"
            ),
            "ordering": (
                "release only after every page write is issued and every pending "
                "write is a full line owned by the bounded retirement map; partial "
                "writes remain completion-gated"
            ),
            "fallback": (
                "a completed line is read through the cache; a pending full line is "
                "forwarded from its owned packet; any other conflict remains deferred"
            ),
        },
        "architecture_limitations": [
            (
                "One-MAA evidence only: the single global forwarding event/table "
                "serves at most one line per MAA-object clock, and multi-MAA "
                "replication or arbitration is not evaluated."
            ),
            (
                "Mid-treatment checkpoint serialization of the scheduled queue, "
                "address ownership set, and event is not implemented or tested."
            ),
            (
                "Area evidence is a target-semantic packed lower bound; current "
                "simulator dynamic-container allocation is not measured."
            ),
        ],
    }


def render_markdown(report: dict) -> str:
    accepted = report["accepted_tail_reconfirmed"]
    summary = report["summary"]
    rows = "\n".join(
        f"| {index} | {pair['simTicks']['control']:,} | "
        f"{pair['simTicks']['candidate']:,} | {pair['delta_simTicks']:,} | "
        f"{pair['speedup_percent']:.3f}% | "
        f"{pair['mechanism_counters']['page_ready_pending_words']:,} | "
        f"{pair['mechanism_counters']['stream_line_forwards']:,} |"
        for index, pair in enumerate(report["pairs"], 1)
    )
    return f"""# Bounded issue-ready hybrid-tail treatment

## Outcome

The accepted tail was reaudited from raw evidence: native {accepted['native_simTicks']:,} versus transparent {accepted['transparent_simTicks']:,} simTicks, with {accepted['post_all_ready_ticks']:,} ticks after all-ready. STREAM ({accepted['stream_busy_ticks']:,}) plus ALU ({accepted['alu_busy_ticks']:,}) explains that interval exactly, and producer write completions after all-ready are {accepted['producer_writes_after_all_ready']}.

| Pair | Control simTicks | Candidate simTicks | Saved ticks | Speedup | Pending words at release | Forwarded lines |
|---:|---:|---:|---:|---:|---:|---:|
{rows}

Mean speedup is **{summary['mean_speedup_percent']:.3f}%** across {summary['pair_count']} matched pair(s). Every arm has exact output hash `{report['pairs'][0]['output_hash']}`, full 16K reorder metadata, 4K payload capacity, identical physical-admission records, one immutable checkpoint, and only the issue-ready config bit differs.

## Hardware delta and safety

The 4K-element physical SPD payload is unchanged. The mechanism uses the existing finite one-unit 64-line retirement map and explicitly adds a 64-entry scheduled-forward table. The treatment-only packed-design lower bound is 5,312 bytes: 4,096 copied data bytes plus 1,216 metadata bytes (151 raw bits = 18.875 bytes, rounded to 19 bytes per entry). Retirement and scheduled payload can coexist for 8,192 payload bytes, but no combined-map total is claimed: the existing `OutstandingPacket` requester vectors and map nodes, and the scheduled `Packet`/`Request`, multiset, and address-set nodes are dynamic simulator representations whose allocation was not measured.

Each hit pays one configured MAA cycle after `max(curTick, request_tick)`, and the single global mux/event serves at most one 64-byte line per MAA-object cycle. A page releases only after all writes are issued and every pending write is a full line owned by the retirement map; partial writes remain acknowledgment-gated. The candidate audit requires pending-page exposure, scheduled=delivered, a high-water mark no greater than 64, no queue-full event, and zero fallback retirement deferrals; teardown also panics unless its event, queue, and address set are empty.

This is one-MAA evidence. Multi-MAA replication/arbitration is not evaluated, and mid-treatment checkpoint serialization of the scheduled queue, address set, and event is not implemented or tested. Neither feature is exercised by this pair.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pair_roots", nargs="+", type=Path)
    parser.add_argument("--accepted-audit", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(
        [audit_pair(root) for root in args.pair_roots],
        json.loads(args.accepted_audit.read_text()),
    )
    args.json_output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    args.markdown_output.write_text(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
