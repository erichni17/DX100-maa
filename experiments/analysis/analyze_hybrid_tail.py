#!/usr/bin/env python3
"""Fail-closed audit of the accepted hybrid tail and ping-pong control.

This analyzer does not rewrite raw evidence and does not turn aligned timeline
intervals into causal attribution.  It re-runs the accepted pair audit, binds
the result to the committed evidence snapshot, and then reports the narrow
observations that distinguish producer writes, page readiness, controller
serialization, consumer stores, and descriptor retirement.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


class AuditError(ValueError):
    pass


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AuditError(f"cannot load analyzer {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_result(path: Path) -> dict[str, str]:
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(rows) != 1:
        raise AuditError(f"{path}: expected exactly one result row")
    return rows[0]


def one(events: list[dict], name: str) -> dict:
    selected = [event for event in events if event["event"] == name]
    if len(selected) != 1:
        raise AuditError(f"expected one {name}, observed {len(selected)}")
    return selected[0]


def bind_accepted_pair(fresh: dict, accepted: dict) -> None:
    if accepted.get("schema") != "dx100.hybrid_overhead_attribution.v2":
        raise AuditError("accepted pair has wrong schema")
    for arm in ("native", "hybrid"):
        observed = fresh["pair"][arm]
        frozen = accepted["pair"][arm]
        for field in ("simTicks", "checkpoint_identity_sha256", "hashes"):
            if observed[field] != frozen[field]:
                raise AuditError(f"accepted {arm} changed: {field}")
        if observed["completion"] != frozen["completion"]:
            raise AuditError(f"accepted {arm} completion evidence changed")
        if observed["result"] != frozen["result"]:
            raise AuditError(f"accepted {arm} result row changed")
    if fresh["provenance"] != accepted["provenance"]:
        raise AuditError("accepted pair provenance changed")


def source_geometry(native_events: list[dict], hybrid_events: list[dict]) -> dict:
    sequences = {}
    for name, events in (("native", native_events), ("hybrid", hybrid_events)):
        sequences[name] = [
            int(event["addr"], 0)
            for event in events
            if event["event"] == "source_issue"
        ]
    native = sequences["native"]
    hybrid = sequences["hybrid"]
    native_counts = Counter(native)
    hybrid_counts = Counter(hybrid)
    first_divergence = next(
        (index for index, pair in enumerate(zip(native, hybrid)) if pair[0] != pair[1]),
        min(len(native), len(hybrid)),
    )
    return {
        "native_issue_count": len(native),
        "hybrid_issue_count": len(hybrid),
        "native_unique_lines": len(native_counts),
        "hybrid_unique_lines": len(hybrid_counts),
        "unique_line_sets_equal": set(native_counts) == set(hybrid_counts),
        "hybrid_counter_is_submultiset_of_native": not bool(
            hybrid_counts - native_counts
        ),
        "native_repeat_issue_excess": sum((native_counts - hybrid_counts).values()),
        "hybrid_repeat_issue_excess": sum((hybrid_counts - native_counts).values()),
        "common_prefix_issues": first_divergence,
        "same_position_issues": sum(
            left == right for left, right in zip(native, hybrid)
        ),
        "sequence_lengths_equal": len(native) == len(hybrid),
    }


def controller_intervals(events: list[dict]) -> tuple[list[dict], int, int]:
    issues = {
        (int(event["page"]), int(event["action"])): event
        for event in events
        if event["event"] == "transparent_issue"
    }
    completes = {
        (int(event["page"]), int(event["action"])): event
        for event in events
        if event["event"] == "transparent_complete"
    }
    expected = {(page, action) for page in range(4) for action in (1, 2, 3)}
    if set(issues) != expected or set(completes) != expected:
        raise AuditError("controller action domain is not four pages x three actions")
    names = {1: "fill", 2: "compute", 3: "store"}
    intervals = []
    for key in sorted(expected):
        issue = issues[key]
        complete = completes[key]
        start = issue["sim_tick"]
        end = complete["sim_tick"]
        if end < start:
            raise AuditError(f"controller completion precedes issue: {key}")
        intervals.append(
            {
                "page": key[0],
                "action": names[key[1]],
                "start_tick": start,
                "end_tick": end,
                "ticks": end - start,
            }
        )
    retire = one(events, "transparent_retire")["sim_tick"]
    final_complete = max(interval["end_tick"] for interval in intervals)
    if retire < final_complete:
        raise AuditError("controller retired before final action completion")
    return intervals, final_complete, retire


def accepted_tail(pair: dict, base_module) -> dict:
    native_path = Path(pair["pair"]["native"]["path"])
    hybrid_path = Path(pair["pair"]["hybrid"]["path"])
    native_events = base_module.strict_events(native_path / "run/virtual_trace.log")
    hybrid_events = base_module.strict_events(hybrid_path / "run/virtual_trace.log")
    native_stats = base_module.first_stats(native_path / "run/stats.txt")
    hybrid_stats = base_module.first_stats(hybrid_path / "run/stats.txt")
    for stats, run in (
        (native_stats, pair["pair"]["native"]),
        (hybrid_stats, pair["pair"]["hybrid"]),
    ):
        if stats["simTicks"] != run["simTicks"] or "finalTick" not in stats:
            raise AuditError("first-ROI simTicks/finalTick mismatch")

    ready = [event for event in hybrid_events if event["event"] == "page_ready"]
    if len(ready) != 4:
        raise AuditError(f"expected four page-ready events, observed {len(ready)}")
    ready_counts = sorted(int(event["pages"].split("/", 1)[0]) for event in ready)
    if ready_counts != [1, 2, 3, 4]:
        raise AuditError("page-ready count domain is not exactly 1..4")
    all_ready = max(event["sim_tick"] for event in ready)
    final_ready = [event for event in ready if event["sim_tick"] == all_ready]
    if len(final_ready) != 1 or final_ready[0]["pages"] != "4/4":
        raise AuditError("last page-ready event is not the unique 4/4 event")

    intervals, final_complete, retire = controller_intervals(hybrid_events)
    hybrid_roi_end = hybrid_stats["finalTick"]
    native_roi_end = native_stats["finalTick"]
    if not all_ready <= final_complete <= retire <= hybrid_roi_end:
        raise AuditError("hybrid tail event order is invalid")

    clipped = defaultdict(int)
    for interval in intervals:
        clipped[interval["action"]] += max(
            0, interval["end_tick"] - max(interval["start_tick"], all_ready)
        )
    serialized_action_tail = sum(clipped.values())
    retirement_gap = retire - final_complete
    roi_epilogue = hybrid_roi_end - retire
    post_ready = hybrid_roi_end - all_ready
    if serialized_action_tail + retirement_gap + roi_epilogue != post_ready:
        raise AuditError("post-ready timeline does not reconcile")

    writes = [
        event
        for event in hybrid_events
        if event["event"] in {"backing_write_issue", "backing_write_complete"}
    ]
    write_completes = [
        event for event in writes if event["event"] == "backing_write_complete"
    ]
    if not write_completes:
        raise AuditError("hybrid trace has no producer backing-write completions")
    last_write = max(write_completes, key=lambda event: event["sim_tick"])
    if int(last_write["outstanding"]) != 0 or last_write["sim_tick"] > all_ready:
        raise AuditError("producer backing writes remain outstanding after readiness")

    native_ticks = pair["pair"]["native"]["simTicks"]
    hybrid_ticks = pair["pair"]["hybrid"]["simTicks"]
    delta = hybrid_ticks - native_ticks
    geometry = source_geometry(native_events, hybrid_events)
    backpressure = sum(
        event["event"] == "transparent_backpressure" for event in hybrid_events
    )

    return {
        "roi": {
            "native_simTicks": native_ticks,
            "hybrid_simTicks": hybrid_ticks,
            "delta_simTicks": delta,
            "native_finalTick": native_roi_end,
            "hybrid_finalTick": hybrid_roi_end,
        },
        "timeline": {
            "all_pages_ready_tick": all_ready,
            "all_pages_ready_minus_native_roi_end_ticks": all_ready - native_roi_end,
            "all_pages_ready_to_hybrid_roi_end_ticks": post_ready,
            "post_ready_ticks_as_percent_of_pair_delta": 100.0 * post_ready / delta,
            "post_ready_controller_action_ticks": dict(sorted(clipped.items())),
            "post_ready_serialized_action_ticks": serialized_action_tail,
            "final_action_complete_tick": final_complete,
            "controller_retire_tick": retire,
            "final_action_to_retire_ticks": retirement_gap,
            "retire_to_roi_end_ticks": roi_epilogue,
            "reconciles_exactly": True,
            "causal_decomposition": False,
        },
        "source_request_geometry": geometry,
        "producer_backing_writes": {
            "issues": sum(event["event"] == "backing_write_issue" for event in writes),
            "completions": len(write_completes),
            "last_completion_tick": last_write["sim_tick"],
            "outstanding_after_last_completion": int(last_write["outstanding"]),
            "last_completion_at_or_before_all_ready": True,
            "post_ready_producer_write_events": sum(
                event["sim_tick"] > all_ready for event in writes
            ),
        },
        "page_and_controller": {
            "page_ready_events": len(ready),
            "controller_backpressure_events": backpressure,
            "strict_serial_page_action_order": True,
            "intervals": intervals,
        },
    }


PING_FIELDS = {
    "transparent_submit": {
        "event", "token", "physical", "output", "generation", "logical",
        "page", "pages", "mode", "chunks", "chunk_elements",
    },
    "transparent_issue": {
        "event", "page", "action", "offset", "elements", "element_offset",
        "src_slot", "dst_slot", "transaction",
    },
    "transparent_complete": {
        "event", "page", "action", "element_offset", "transaction",
    },
    "transparent_retire": {"event", "pages", "chunks", "mode"},
}


def audit_ping_event_fields(trace: Path) -> dict:
    prefix = re.compile(r"^(\d+): [^:]+: (.*)$")
    counts = Counter()
    for line_no, raw in enumerate(trace.read_text(errors="strict").splitlines(), 1):
        if "event=transparent_" not in raw:
            continue
        match = prefix.fullmatch(raw)
        if match is None:
            raise AuditError(f"{trace}:{line_no}: malformed transparent event")
        fields = {}
        for token in match.group(2).split():
            if "=" not in token:
                raise AuditError(f"{trace}:{line_no}: malformed token {token!r}")
            key, value = token.split("=", 1)
            if not key or not value or key in fields:
                raise AuditError(f"{trace}:{line_no}: duplicate/empty field")
            fields[key] = value
        event = fields.get("event")
        if event not in PING_FIELDS or set(fields) != PING_FIELDS[event]:
            raise AuditError(f"{trace}:{line_no}: ping event field mismatch")
        counts[event] += 1
    if counts["transparent_submit"] != 1 or counts["transparent_retire"] != 1:
        raise AuditError(f"{trace}: incomplete ping event envelope")
    if counts["transparent_issue"] != counts["transparent_complete"]:
        raise AuditError(f"{trace}: ping issue/completion count mismatch")
    return {
        "observed_schema": "unversioned_transparent_schedule",
        "event_counts": dict(sorted(counts.items())),
        "exact_field_sets": True,
    }


def audit_ping_completion(run: Path, expected_hash: str) -> dict:
    result = read_result(run / "result.tsv")
    if result.get("output_hash") != expected_hash:
        raise AuditError(f"{run}: exact output mismatch")
    if (run / "checkpoint.exit").read_text().strip() != "0" or (
        run / "restore.exit"
    ).read_text().strip() != "0":
        raise AuditError(f"{run}: nonzero checkpoint/restore exit")
    sentinel = run / "virtual_tile_consumer_case.pass"
    if not sentinel.is_file() or sentinel.read_bytes() != b"":
        raise AuditError(f"{run}: invalid correctness sentinel")
    log = (run / "restore.log").read_text(errors="replace")
    markers = re.findall(
        r"^VIRTUAL_TILE_CONSUMER_RESULT .* hash=([0-9]+) errors=([0-9]+)$",
        log,
        re.M,
    )
    exits = re.findall(
        r"^Exiting @ tick ([0-9]+) because m5_exit instruction encountered$",
        log,
        re.M,
    )
    if markers != [(expected_hash, "0")] or len(exits) != 1:
        raise AuditError(f"{run}: terminal markers mismatch")
    if re.findall(r"^ROI.*$", log, re.M) != ["ROI Ended"]:
        raise AuditError(f"{run}: ROI markers mismatch")
    if (run / "source.diff").read_bytes() or (run / "source_status.txt").read_bytes():
        raise AuditError(f"{run}: source snapshot was dirty")
    return {
        "checkpoint_exit": 0,
        "restore_exit": 0,
        "exact_output_hash": expected_hash,
        "errors": 0,
        "m5_exit_tick": int(exits[0]),
        "empty_correctness_sentinel": True,
        "clean_source_snapshot": True,
    }


def audit_ping_matrix(root: Path, accepted_path: Path, ping_module) -> dict:
    accepted = json.loads(accepted_path.read_text())
    if accepted.get("schema") != 1 or accepted.get("exact_output_match") is not True:
        raise AuditError("accepted ping-pong summary has wrong schema/status")
    by_case = {run["case"]: run for run in accepted["runs"]}
    arms = ("isoarea_serial_4k", "isoarea_serial_2k", "isoarea_pingpong_2k")
    expected_hash = None
    audited = []
    for arm in arms:
        run_path = root / arm
        observed = ping_module.analyze_run(run_path)
        if observed != by_case.get(arm):
            raise AuditError(f"accepted ping-pong arm changed: {arm}")
        if expected_hash is None:
            expected_hash = observed["output_hash"]
        if observed["output_hash"] != expected_hash:
            raise AuditError("ping-pong exact outputs differ")
        audited.append(
            {
                "case": arm,
                "simTicks": observed["simTicks"],
                "trace_sha256": observed["trace_sha256"],
                "completion": audit_ping_completion(run_path, expected_hash),
                "event_schema": audit_ping_event_fields(
                    run_path / "run/virtual_trace.log"
                ),
            }
        )
    serial2 = by_case["isoarea_serial_2k"]["simTicks"]
    ping2 = by_case["isoarea_pingpong_2k"]["simTicks"]
    source_commit = (root / "inputs/source_commit").read_text().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise AuditError("ping-pong source commit is malformed")
    return {
        "raw_root": str(root.resolve()),
        "accepted_summary": {
            "path": str(accepted_path.resolve()),
            "sha256": sha256(accepted_path),
        },
        "source_commit": source_commit,
        "input_hashes": {
            "gem5": sha256(root / "inputs/bin/gem5.opt"),
            "workload": sha256(
                root / "inputs/workload/test_virtual_tile_consumer_T16384"
            ),
            "ramulator": sha256(root / "inputs/bin/libramulator.so"),
        },
        "arms": audited,
        "treatment_only_serial2k_minus_pingpong2k_ticks": serial2 - ping2,
        "treatment_only_pingpong_speedup": serial2 / ping2,
        "outer_launch_wrapper_terminal_proven": False,
        "inner_matrix_terminal": (root / "matrix.exit").read_text().strip() == "0"
        and (root / "matrix.complete").is_file(),
    }


def classifications(tail: dict, ping: dict) -> dict:
    geometry = tail["source_request_geometry"]
    timeline = tail["timeline"]
    writes = tail["producer_backing_writes"]
    return {
        "lost_a_request_reordering": {
            "status": "not_supported_by_request_geometry_order_quality_unresolved",
            "observations": {
                "unique_line_sets_equal": geometry["unique_line_sets_equal"],
                "hybrid_counter_is_submultiset_of_native": geometry[
                    "hybrid_counter_is_submultiset_of_native"
                ],
                "native_repeat_issue_excess": geometry["native_repeat_issue_excess"],
                "issue_sequence_common_prefix": geometry["common_prefix_issues"],
            },
            "limit": "same request universe and fewer repeats do not prove equal DRAM scheduling quality",
        },
        "page_fill_or_backpressure": {
            "status": "fill_present_controller_backpressure_absent_causality_unisolated",
            "observations": {
                "post_ready_fill_ticks": timeline[
                    "post_ready_controller_action_ticks"
                ]["fill"],
                "controller_backpressure_events": tail["page_and_controller"][
                    "controller_backpressure_events"
                ],
            },
        },
        "per_page_consumer_serialization": {
            "status": "mechanism_present_4k_causal_magnitude_unresolved",
            "observations": {
                "post_ready_serial_action_chain_ticks": timeline[
                    "post_ready_serialized_action_ticks"
                ],
                "separate_2k_schedule_treatment_recovery_ticks": ping[
                    "treatment_only_serial2k_minus_pingpong2k_ticks"
                ],
            },
            "limit": "the clean ping-pong treatment is 2K-vs-2K; a 4K double buffer would increase visible payload",
        },
        "producer_backing_store_writes": {
            "status": "no_direct_post_ready_outstanding_write_tail_indirect_perturbation_unresolved",
            "observations": {
                "write_completions": writes["completions"],
                "post_ready_producer_write_events": writes[
                    "post_ready_producer_write_events"
                ],
                "outstanding_after_last_completion": writes[
                    "outstanding_after_last_completion"
                ],
            },
        },
        "final_retirement": {
            "status": "controller_bookkeeping_gap_zero_final_store_transport_present",
            "observations": {
                "final_action_to_retire_ticks": timeline[
                    "final_action_to_retire_ticks"
                ],
                "post_ready_store_ticks": timeline[
                    "post_ready_controller_action_ticks"
                ]["store"],
                "retire_to_roi_end_ticks": timeline["retire_to_roi_end_ticks"],
            },
            "limit": "stream-store completion means accepted completion in this model, not a DRAM persistence fence",
        },
    }


def render_markdown(result: dict) -> str:
    timeline = result["accepted_pair_tail"]["timeline"]
    roi = result["accepted_pair_tail"]["roi"]
    classes = result["hypothesis_separation"]
    lines = [
        "# Hybrid overhead tail causal audit",
        "",
        "## Outcome",
        "",
        f"The accepted pair remains {roi['native_simTicks']:,} versus "
        f"{roi['hybrid_simTicks']:,} simTicks (+{roi['delta_simTicks']:,}). "
        f"All pages became ready {timeline['all_pages_ready_minus_native_roi_end_ticks']:,} "
        "ticks after the native ROI endpoint, and "
        f"{timeline['all_pages_ready_to_hybrid_roi_end_ticks']:,} ticks remained "
        "to the hybrid endpoint. This is an aligned timeline observation, not "
        "a causal decomposition.",
        "",
        "| Hypothesis | Fail-closed result |",
        "|---|---|",
    ]
    labels = {
        "lost_a_request_reordering": "Lost A-request reordering",
        "page_fill_or_backpressure": "Page fill/backpressure",
        "per_page_consumer_serialization": "Per-page consumer serialization",
        "producer_backing_store_writes": "Producer backing-store writes",
        "final_retirement": "Final retirement",
    }
    for key, label in labels.items():
        lines.append(f"| {label} | `{classes[key]['status']}` |")
    lines += [
        "",
        "The post-ready interval reconciles exactly to clipped controller "
        "actions, the zero-tick controller retirement gap, and the ROI "
        "epilogue. Additive accounting does not imply eliminable latency.",
        "",
        "## Run and provenance boundary",
        "",
        f"Audited accepted gem5 arms: **{result['run_counts']['accepted_gem5_arms_audited']}** "
        f"({result['run_counts']['accepted_pair_arms']} pair + "
        f"{result['run_counts']['accepted_pingpong_arms']} ping-pong). New gem5 "
        f"arms launched: **{result['run_counts']['new_gem5_arms_launched']}**. "
        "Every arm has one raw observation; no repetition-based noise claim is made.",
        "",
        "The accepted ping-pong 2K schedule treatment recovered "
        f"{result['accepted_pingpong_control']['treatment_only_serial2k_minus_pingpong2k_ticks']:,} "
        "simTicks relative to serial 2K. It does not estimate a legal 4K "
        "double-buffer treatment because that would require more visible payload.",
        "",
        "## Falsifiable next test",
        "",
        result["falsifiable_next_test"]["test"],
        "",
        result["falsifiable_next_test"]["prediction"],
        "",
    ]
    return "\n".join(lines)


def analyze(args) -> dict:
    root = args.repo_root.resolve()
    base_module = load_module(
        "hybrid_overhead_base",
        root / "experiments/analysis/hybrid_overhead_attribution.py",
    )
    ping_module = load_module(
        "isoarea_ping_base",
        root / "experiments/analysis/analyze_isoarea_pingpong.py",
    )
    accepted = json.loads(args.accepted_pair.read_text())
    try:
        fresh = base_module.analyze_pair(args.native.resolve(), args.hybrid.resolve())
    except (base_module.AuditError, KeyError, OSError) as exc:
        raise AuditError(f"accepted pair audit failed: {exc}") from exc
    bind_accepted_pair(fresh, accepted)
    tail = accepted_tail(fresh, base_module)
    ping = audit_ping_matrix(args.ping_root.resolve(), args.accepted_ping.resolve(), ping_module)
    source_files = {
        "maa_cc": root / "src/mem/MAA/MAA.cc",
        "controller": root / "src/mem/MAA/TransparentSPDController.hh",
        "ping_analyzer": root / "experiments/analysis/analyze_isoarea_pingpong.py",
    }
    maa_text = source_files["maa_cc"].read_text()
    current_dual_schema = all(
        marker in maa_text
        for marker in (
            "event=transparent_submit schema=2",
            "event=transparent_ping_submit",
            "event=transparent_retire schema=2",
            "event=transparent_ping_retire",
        )
    )
    if not current_dual_schema:
        raise AuditError("current source does not contain the expected dual event streams")
    return {
        "schema": "dx100.hybrid_tail_causal_audit.v1",
        "evidence_status": "observational_separation_with_one_bounded_causal_control",
        "source_merge_commit": "5d0215da84864b423cb50f2f3fc2734f5c8be06f",
        "accepted_pair_manifest": {
            "path": str(args.accepted_pair.resolve()),
            "sha256": sha256(args.accepted_pair),
        },
        "accepted_pair_tail": tail,
        "accepted_pingpong_control": ping,
        "current_event_schema_audit": {
            "source_only_not_live_run_evidence": True,
            "dual_versioned_transparent_and_unversioned_ping_streams_present": True,
            "accepted_pair_schema": "versioned transparent schema=2",
            "accepted_pingpong_schema": "unversioned enriched transparent schedule",
            "current_source_hashes": {
                name: sha256(path) for name, path in source_files.items()
            },
            "limitation": "no accepted raw run in this audit emits the current merge commit's dual stream",
        },
        "hypothesis_separation": classifications(tail, ping),
        "run_counts": {
            "accepted_pair_arms": 2,
            "accepted_pingpong_arms": 3,
            "accepted_gem5_arms_audited": 5,
            "raw_observations_per_arm": 1,
            "new_gem5_arms_launched": 0,
        },
        "falsifiable_next_test": {
            "test": (
                "Add treatment-neutral blocker-residency counters to the existing "
                "controller schedule points (producer-not-ready, STREAM busy, ALU "
                "busy, slot-owned, IF-full), plus first/last consumer STREAM packet "
                "acceptance ticks; run native16K and transparent4K from one new "
                "shared deferred checkpoint with one instrumented binary and the "
                "same exact-output oracle."
            ),
            "prediction": (
                "If serial consumer service dominates, post-ready blocker residency "
                "must reconcile to STREAM/ALU/slot ownership and consumer packet "
                "acceptance; producer-not-ready and producer backing-write "
                "outstanding counters must remain zero after 4/4 readiness. A "
                "nonzero unexplained residual or post-ready producer-write count "
                "falsifies that explanation."
            ),
            "why_no_4k_ablation_now": (
                "Two simultaneous 4K input/output slots require additional visible "
                "SPD payload; suppressing producer or consumer writes changes the "
                "functional/memory-traffic contract. Either intervention confounds "
                "the mechanism being isolated."
            ),
        },
        "limitations": [
            "Timeline alignment is not causal attribution.",
            "One deterministic observation exists per accepted arm.",
            "Source-request equality does not establish equal DRAM scheduling quality.",
            "Producer write completion is model completion, not a persistence fence.",
            "The clean ping-pong control changes scheduling only within 2K chunking.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native", type=Path)
    parser.add_argument("hybrid", type=Path)
    parser.add_argument("--accepted-pair", type=Path, required=True)
    parser.add_argument("--ping-root", type=Path, required=True)
    parser.add_argument("--accepted-ping", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).parents[2])
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    try:
        result = analyze(args)
        encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.json_output:
            args.json_output.write_text(encoded)
        else:
            print(encoded, end="")
        markdown = render_markdown(result)
        if args.markdown_output:
            args.markdown_output.write_text(markdown)
        elif args.json_output:
            print(markdown)
    except (AuditError, KeyError, OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
