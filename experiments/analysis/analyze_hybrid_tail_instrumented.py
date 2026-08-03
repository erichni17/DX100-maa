#!/usr/bin/env python3
"""Fail-closed audit for the instrumented native/transparent tail pair."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hybrid_overhead_attribution import (  # noqa: E402
    AuditError,
    EXPECTED_OUTPUT_HASH,
    audit_run,
    first_stats,
    sha256,
    strict_events,
)


ARMS = ("native_direct_16k", "transparent_4k")
ACCEPTED_WORKLOAD_SHA256 = (
    "20fe15ca32cf6e307801fda427ac430bd99148be500647acf4cefb0959635880"
)


def require_exact(path: Path, value: bytes) -> None:
    if not path.is_file() or path.is_symlink() or path.read_bytes() != value:
        raise AuditError(f"{path}: missing or wrong fail-closed marker")


def audit_failed_attempt(path: Path | None) -> dict | None:
    if path is None:
        return None
    require_exact(path / "pair.exit", b"1\n")
    if (path / "pair.complete").exists():
        raise AuditError(f"{path}: failed attempt has completion marker")
    require_exact(path / "shared-checkpoint.exit", b"0\n")
    log = path / "native_direct_16k/restore.log"
    if not log.is_file() or (
        "deferred treatment must contain exactly MODE PAGE"
        not in log.read_text(errors="replace")
    ):
        raise AuditError(f"{path}: failed-attempt reason changed")
    if (path / "native_direct_16k/result.tsv").exists():
        raise AuditError(f"{path}: failed attempt unexpectedly has result")
    return {
        "path": str(path.resolve()),
        "pair_exit": 1,
        "completed_arms": 0,
        "failure_class": "checkpointed_selector_path_mismatch",
        "restore_log_sha256": sha256(log),
    }


def audit_pair(root: Path, require_accepted_workload: bool = True) -> dict:
    require_exact(root / "pair.exit", b"0\n")
    require_exact(root / "pair.complete", b"")
    require_exact(root / "shared-checkpoint.exit", b"0\n")
    runs = {arm: audit_run(root / arm) for arm in ARMS}
    native = runs[ARMS[0]]
    hybrid = runs[ARMS[1]]

    if [native["result"]["case"], hybrid["result"]["case"]] != list(ARMS):
        raise AuditError("fresh pair case identity mismatch")
    if {
        native["result"]["output_hash"], hybrid["result"]["output_hash"]
    } != {EXPECTED_OUTPUT_HASH}:
        raise AuditError("fresh pair exact-output mismatch")
    if native["manifest"]["source_commit"] != hybrid["manifest"]["source_commit"]:
        raise AuditError("fresh pair source commits differ")
    source_commit = (root / "input/source_commit").read_text().strip()
    if source_commit != native["manifest"]["source_commit"]:
        raise AuditError("pair-root and arm source commits differ")
    if native["checkpoint_identity_sha256"] != hybrid[
        "checkpoint_identity_sha256"
    ]:
        raise AuditError("fresh arms did not use one checkpoint")
    manifests = [
        (root / arm / "shared_checkpoint_files.sha256").read_bytes()
        for arm in ARMS
    ]
    authoritative = (root / "checkpoint_files.pre_treatment.sha256").read_bytes()
    if manifests[0] != manifests[1] or manifests[0] != authoritative:
        raise AuditError("fresh checkpoint manifests differ")

    labels = set(native["frozen_artifacts"]) & set(hybrid["frozen_artifacts"])
    for label in labels - {"dynamic_link_audit", "invocation"}:
        if native["frozen_artifacts"][label]["sha256"] != hybrid[
            "frozen_artifacts"
        ][label]["sha256"]:
            raise AuditError(f"fresh frozen artifact mismatch: {label}")
    if native["dynamic_links"]["normalized_sha256"] != hybrid[
        "dynamic_links"
    ]["normalized_sha256"]:
        raise AuditError("fresh dynamic-link resolutions differ")
    workload_sha256 = native["frozen_artifacts"]["workload_binary"]["sha256"]
    if require_accepted_workload and workload_sha256 != ACCEPTED_WORKLOAD_SHA256:
        raise AuditError("primary pair did not reuse accepted workload binary")

    if native["trace"]["tail_instrumentation"] != {"active": False}:
        raise AuditError("tail instrumentation unexpectedly active in native")
    tail = hybrid["trace"]["tail_instrumentation"]
    if not tail.get("active"):
        raise AuditError("tail instrumentation absent in transparent arm")

    events = strict_events(root / "transparent_4k/run/virtual_trace.log")
    grouped = defaultdict(list)
    for event in events:
        grouped[event["event"]].append(event)
    snapshots = grouped["transparent_blocker_snapshot"]
    summaries = grouped["transparent_blocker_summary"]
    retires = grouped["transparent_retire"]
    if not (len(snapshots) == len(summaries) == len(retires) == 1):
        raise AuditError("wrong blocker/retirement marker count")
    all_ready_tick = snapshots[0]["sim_tick"]
    summary_tick = summaries[0]["sim_tick"]
    retire_tick = retires[0]["sim_tick"]
    if summary_tick - all_ready_tick != tail["post_ready_total_ticks"]:
        raise AuditError("post-ready tick interval does not reconcile")
    if retire_tick != summary_tick:
        raise AuditError("controller retirement has nonzero bookkeeping gap")

    backing_completions = grouped["backing_write_complete"]
    if not backing_completions:
        raise AuditError("transparent arm has no backing-write completions")
    last_backing_complete = max(event["sim_tick"] for event in backing_completions)
    if last_backing_complete > all_ready_tick:
        raise AuditError("producer backing write completed after all-ready")

    native_stats = first_stats(root / "native_direct_16k/run/stats.txt")
    hybrid_stats = first_stats(root / "transparent_4k/run/stats.txt")
    native_final = int(native_stats["finalTick"])
    hybrid_final = int(hybrid_stats["finalTick"])
    if hybrid_final < retire_tick:
        raise AuditError("hybrid ROI endpoint precedes retirement")

    post = tail["post_ready_blocker_ticks"]
    post_total = tail["post_ready_total_ticks"]
    explained = post["stream_busy_ticks"] + post["alu_busy_ticks"]
    if explained != post_total:
        raise AuditError("post-ready stream/ALU residency has residual")
    zero_categories = (
        "producer_not_ready_ticks",
        "slot_owned_ticks",
        "serialization_ticks",
        "if_full_ticks",
        "other_ticks",
        "inactive_ticks",
        "runnable_ticks",
        "transition_ticks",
    )
    if any(post[name] != 0 for name in zero_categories):
        raise AuditError("unexpected nonzero post-ready blocker category")

    return {
        "path": str(root.resolve()),
        "source_commit": source_commit,
        "checkpoint_identity_sha256": native["checkpoint_identity_sha256"],
        "exact_output_hash": EXPECTED_OUTPUT_HASH,
        "accepted_workload_reused": workload_sha256 == ACCEPTED_WORKLOAD_SHA256,
        "workload_sha256": workload_sha256,
        "arms": {
            arm: {
                "simTicks": runs[arm]["simTicks"],
                "finalTick": int(
                    first_stats(root / arm / "run/stats.txt")["finalTick"]
                ),
                "result_sha256": runs[arm]["hashes"]["result.tsv"],
                "trace_sha256": runs[arm]["hashes"]["run/virtual_trace.log"],
                "completion": runs[arm]["completion"],
                "stream_instructions": {
                    "STRRD": int(
                        first_stats(root / arm / "run/stats.txt")[
                            "system.maa.numInst_STRRD"
                        ]
                    ),
                    "STRWR": int(
                        first_stats(root / arm / "run/stats.txt")[
                            "system.maa.numInst_STRWR"
                        ]
                    ),
                },
            }
            for arm in ARMS
        },
        "delta_simTicks": hybrid["simTicks"] - native["simTicks"],
        "tail": {
            "all_pages_ready_tick": all_ready_tick,
            "native_finalTick": native_final,
            "all_ready_minus_native_finalTick": all_ready_tick - native_final,
            "blocker_summary_tick": summary_tick,
            "retire_tick": retire_tick,
            "hybrid_finalTick": hybrid_final,
            "retire_to_hybrid_finalTick": hybrid_final - retire_tick,
            "post_ready_total_ticks": post_total,
            "post_ready_blocker_ticks": post,
            "stream_busy_percent": 100.0 * post["stream_busy_ticks"] / post_total,
            "alu_busy_percent": 100.0 * post["alu_busy_ticks"] / post_total,
            "consumer_acceptance": {
                "packets": tail["accepted_packets"],
                "pages": tail["pages"],
                "expected_field_semantics": "issued_packets_at_acceptance",
            },
            "producer_backing_writes": {
                "completions": len(backing_completions),
                "last_completion_tick": last_backing_complete,
                "post_ready_completions": 0,
            },
            "controller_backpressure_events": hybrid["trace"]["controller"][
                "backpressure_events"
            ],
        },
        "provenance": {
            "frozen_artifacts": hybrid["frozen_artifacts"],
            "ramulator": hybrid["ramulator_provenance"],
            "dynamic_links": hybrid["dynamic_links"],
            "checkpoint_manifest_sha256": sha256(
                root / "checkpoint_files.pre_treatment.sha256"
            ),
        },
    }


def build_report(
    pair: dict,
    accepted: dict,
    failed: dict | None,
    superseded_pair: dict | None = None,
) -> dict:
    accepted_tail = accepted["accepted_pair_tail"]
    return {
        "schema": "dx100.hybrid_tail_instrumented_pair.v1",
        "status": "completed_fail_closed",
        "run_counts": {
            "accepted_arms_reaudited": accepted["run_counts"][
                "accepted_gem5_arms_audited"
            ],
            "fresh_completed_arms": 2,
            "all_fresh_completed_arms": 2 if superseded_pair is None else 4,
            "fresh_completed_pairs": 1 if superseded_pair is None else 2,
            "fresh_arm_launches": (
                2 if superseded_pair is None else 4
            ) + (0 if failed is None else 1),
            "fresh_failed_arms": 0 if failed is None else 1,
            "fresh_failed_attempts": 0 if failed is None else 1,
            "observations_per_completed_arm": 1,
        },
        "accepted_reference": {
            "native_simTicks": accepted_tail["roi"]["native_simTicks"],
            "hybrid_simTicks": accepted_tail["roi"]["hybrid_simTicks"],
            "delta_simTicks": accepted_tail["roi"]["delta_simTicks"],
            "all_ready_minus_native_finalTick": accepted_tail["timeline"][
                "all_pages_ready_minus_native_roi_end_ticks"
            ],
            "causal_decomposition": False,
        },
        "fresh_pair": pair,
        "superseded_rebuilt_workload_pair": superseded_pair,
        "failed_attempt": failed,
        "hypothesis_separation": {
            "lost_a_request_reordering": (
                "not_treated; accepted geometry does not support loss, but "
                "ordering quality remains unresolved"
            ),
            "page_fill_backpressure": (
                "not_a_post_ready_blocker: producer-not-ready and controller "
                "backpressure are zero after all-ready"
            ),
            "per_page_consumer_serialization": (
                "residency_supported_not_speedup_proven: post-ready time "
                "reconciles exactly to STREAM and ALU busy; direct instruction "
                "counts are native STRRD/STRWR=1/1 and transparent=5/4"
            ),
            "backing_store_writes": (
                "no_direct_post_ready_completion: all recorded producer "
                "writes complete by all-ready; indirect perturbation unresolved"
            ),
            "final_retirement": (
                "zero controller bookkeeping gap; only the ROI epilogue remains"
            ),
        },
        "limitations": [
            "One completed observation per arm; no variance estimate.",
            "Residency counters identify occupied states but do not estimate an "
            "eliminable causal speedup.",
            "The fresh all-ready/native alignment differs from the accepted pair, "
            "so cross-arm endpoint alignment is not treated as causal or replicated.",
            "The consumer acceptance expected field records issued packets at each "
            "acceptance; terminal continuity is validated at 513 packets per page.",
            "The STREAM instruction counts support concentration of page refill/drain "
            "work in STREAM-busy residency, but do not make that counter causal.",
        ],
        "falsifiable_next_test": (
            "Hold the accepted workload binary and instrumented gem5 fixed, then "
            "intervene on one consumer service constraint only. The intervention "
            "must reduce STREAM-busy residency by the same simTick amount without "
            "creating producer-not-ready, IF-full, output, or provenance changes."
        ),
    }


def render_markdown(report: dict) -> str:
    pair = report["fresh_pair"]
    tail = pair["tail"]
    counts = report["run_counts"]
    native_stream = pair["arms"]["native_direct_16k"]["stream_instructions"]
    hybrid_stream = pair["arms"]["transparent_4k"]["stream_instructions"]
    return f"""# Instrumented hybrid-tail pair

## Outcome

The fresh shared-checkpoint pair completed with exact output hash `{pair['exact_output_hash']}`: native **{pair['arms']['native_direct_16k']['simTicks']:,}** versus transparent **{pair['arms']['transparent_4k']['simTicks']:,}** simTicks (delta **{pair['delta_simTicks']:,}**).

After all four pages were ready, **{tail['post_ready_total_ticks']:,}** ticks remained to controller retirement. They reconcile exactly to STREAM busy **{tail['post_ready_blocker_ticks']['stream_busy_ticks']:,}** ({tail['stream_busy_percent']:.2f}%) plus ALU busy **{tail['post_ready_blocker_ticks']['alu_busy_ticks']:,}** ({tail['alu_busy_percent']:.2f}%); producer-not-ready, IF-full, slot-owned, serialization, runnable, transition, other, and inactive are all zero. This is blocker residency, not a causal speedup estimate.

All **{tail['producer_backing_writes']['completions']:,}** recorded producer backing writes completed by all-ready, controller backpressure was **{tail['controller_backpressure_events']}**, **{tail['consumer_acceptance']['packets']:,}** consumer packets were accepted (513/page), controller bookkeeping retirement took zero ticks, and the remaining ROI epilogue was **{tail['retire_to_hybrid_finalTick']:,}** ticks.

The directly observed STREAM instruction counts were native STRRD/STRWR **{native_stream['STRRD']}/{native_stream['STRWR']}** and transparent **{hybrid_stream['STRRD']}/{hybrid_stream['STRWR']}**. This supports the interpretation that page refill/drain work is concentrated in STREAM-busy residency, but it does not make the residency counter causal.

## Evidence boundary

Accepted arms reaudited: **{counts['accepted_arms_reaudited']}**. Fresh arm launches: **{counts['fresh_arm_launches']}** (**{counts['all_fresh_completed_arms']}** completed across {counts['fresh_completed_pairs']} pairs, **{counts['fresh_failed_arms']}** failed before result). The primary reported pair has **{counts['fresh_completed_arms']}** arms and reuses the accepted workload binary; the first completed pair used a rebuilt binary and is retained as superseded evidence. There is one observation per completed arm and no variance claim.

The accepted pair's all-ready point was 298,915 ticks after the native endpoint; in the fresh pair it was {abs(tail['all_ready_minus_native_finalTick']):,} ticks {'after' if tail['all_ready_minus_native_finalTick'] >= 0 else 'before'} it. That alignment is not replicated and is not used as causal evidence.

## Falsifiable next test

{report['falsifiable_next_test']}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pair_root", type=Path)
    parser.add_argument("--accepted-audit", type=Path, required=True)
    parser.add_argument("--failed-attempt", type=Path)
    parser.add_argument("--superseded-pair", type=Path)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    accepted = json.loads(args.accepted_audit.read_text())
    superseded = None
    if args.superseded_pair is not None:
        superseded = audit_pair(
            args.superseded_pair, require_accepted_workload=False
        )
        if superseded["accepted_workload_reused"]:
            raise AuditError("superseded pair unexpectedly used accepted workload")
    report = build_report(
        audit_pair(args.pair_root), accepted,
        audit_failed_attempt(args.failed_attempt), superseded,
    )
    args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    args.markdown_output.write_text(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
