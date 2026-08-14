#!/usr/bin/env python3
"""Fail-closed correctness and contention report for a matched hybrid matrix."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from pathlib import Path

BEGIN = "---------- Begin Simulation Statistics ----------"
END = "---------- End Simulation Statistics   ----------"
FATAL = re.compile(
    r"(^|\b)(panic|fatal|assert(?:ion)? failed|abort|segmentation fault)(\b|:)",
    re.IGNORECASE,
)
EXIT = re.compile(
    r"Exiting @ tick \d+ because m5_exit instruction encountered"
)
FIELD = re.compile(r"([A-Za-z0-9_]+)=([^ ]+)")


def fields(line: str) -> dict[str, str]:
    return dict(FIELD.findall(line))


def one_marker(text: str, prefix: str) -> str:
    matches = [line for line in text.splitlines() if line.startswith(prefix)]
    if len(matches) != 1:
        raise ValueError(f"expected one {prefix!r} marker, got {len(matches)}")
    return matches[0]


def correctness(workload: str, text: str) -> tuple[str, dict[str, str]]:
    if workload == "api":
        line = one_marker(text, "VIRTUAL_TILE_CONSUMER_RESULT ")
        value = fields(line)
        if value.get("errors") != "0" or "hash" not in value:
            raise ValueError("API result is not exact-pass")
        return value["hash"], value
    if workload == "cg":
        line = one_marker(text, "CG_FINGERPRINT ")
        value = fields(line)
        if (
            value.get("result") != "PASS"
            or value.get("nonfinite_x") != "0"
            or value.get("nonfinite_z") != "0"
            or "x_q5" not in value
        ):
            raise ValueError("CG fingerprint is not an exact semantic pass")
        return value["x_q5"], value
    if workload in ("ume-gzp", "ume-gzz"):
        output = fields(one_marker(text, "UME_OUTPUT_FP "))
        reference = fields(one_marker(text, "UME_REFERENCE_PASS "))
        error_fields = [
            value for key, value in reference.items() if key.endswith("errors")
        ]
        if (
            output.get("nonfinite") != "0"
            or "output_hash" not in output
            or not error_fields
            or any(value != "0" for value in error_fields)
        ):
            raise ValueError("UME output/reference marker is not exact-pass")
        return output["output_hash"], {**output, **reference}
    if workload == "gapbs-pr":
        line = one_marker(text, "PR_FP ")
        value = fields(line)
        if (
            value.get("nonfinite") != "0"
            or value.get("unquantizable") != "0"
            or "normalized_q5" not in value
        ):
            raise ValueError("PageRank fingerprint is not exact semantic pass")
        return value["normalized_q5"], value
    if workload == "gapbs-bfs":
        line = one_marker(text, "BFS_FP ")
        value = fields(line)
        if value.get("invalid_chains") != "0" or value.get(
            "reached"
        ) != value.get("depth_reached"):
            raise ValueError("BFS certificate is not exact-pass")
        return line, value
    if workload == "xrage":
        line = one_marker(text, "MAA_GATHER_VERIFY_PASS ")
        value = fields(line)
        if "length" not in value or "hash" not in value:
            raise ValueError("XRAGE verifier marker is incomplete")
        return f"{value['length']}:{value['hash']}", value
    raise ValueError(f"unsupported workload: {workload}")


def first_stats(path: Path) -> dict[str, float]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if BEGIN not in text or END not in text:
        raise ValueError(f"{path}: no complete statistics window")
    block = text.split(BEGIN, 1)[1].split(END, 1)[0]
    result: dict[str, float] = {}
    for line in block.splitlines():
        words = line.split()
        if len(words) < 2:
            continue
        try:
            result[words[0]] = float(words[1])
        except ValueError:
            continue
    if result.get("simTicks", 0) <= 0:
        raise ValueError(f"{path}: first ROI window has no positive simTicks")
    return result


def exact_stat(stats: dict[str, float], suffix: str) -> float:
    return stats.get(f"system.maa.{suffix}", 0.0)


def sum_suffix(stats: dict[str, float], suffix: str) -> float:
    expression = re.compile(rf"system\.maa\.S\d+_{re.escape(suffix)}$")
    return sum(
        value for name, value in stats.items() if expression.fullmatch(name)
    )


def stream_report(stats: dict[str, float]) -> dict[str, float | str]:
    return {
        "num_stream_loads": exact_stat(stats, "numInst_STRRD"),
        "num_stream_stores": exact_stat(stats, "numInst_STRWR"),
        "aggregate_cycles_STRRD_raw": exact_stat(stats, "cycles_STRRD"),
        "aggregate_cycles_STRWR_raw": exact_stat(stats, "cycles_STRWR"),
        "all_stream_request_cycles": sum_suffix(stats, "STR_CyclesRequest"),
        "all_stream_spd_read_cycles": sum_suffix(
            stats, "STR_CyclesSPDReadAccess"
        ),
        "all_stream_spd_write_cycles": sum_suffix(
            stats, "STR_CyclesSPDWriteAccess"
        ),
        "counter_caveat": (
            "this source revision charges all stream completions to "
            "cycles_STRRD; cycles_STRWR is reported raw and is not a valid "
            "store-only latency"
        ),
    }


OPCODE_STATS = (
    "numInst_INDRD",
    "numInst_INDRMW",
    "numInst_STRRD",
    "numInst_ALUS",
    "numInst_ALUV",
    "cycles_INDRD",
    "cycles_INDRMW",
    "cycles_STRRD",
    "cycles_ALUS",
    "cycles_ALUV",
    "cycles_IDLE",
    "cycles_BUSY",
    "cycles_TOTAL",
)


def opcode_report(stats: dict[str, float]) -> dict[str, int]:
    report: dict[str, int] = {}
    for suffix in OPCODE_STATS:
        name = f"system.maa.{suffix}"
        # These gem5 counters are declared with statistics::nozero, so a
        # missing line in a complete ROI window means an exact zero.
        value = stats.get(name, 0.0)
        if not value.is_integer():
            raise ValueError(f"missing or non-integral opcode stat {name}")
        report[suffix] = int(value)
    return report


CONTEXT_EVENTS = {
    "page_materialization_submit",
    "page_materialization_page_ready",
    "page_materialization_summary",
    "page_materialization_retire",
    "page_materialization_producer_line_ready",
    "page_materialization_read_response",
    "page_materialization_line_commit",
    "page_materialization_inactive_payload_replay",
    "page_materialization_inactive_masked_replay",
}


def integer_field(value: dict[str, str], name: str, event: str) -> int:
    try:
        return int(value[name], 0)
    except (KeyError, ValueError) as error:
        raise ValueError(f"{event}: missing or invalid {name}") from error


def materializer_trace(
    path: Path,
) -> tuple[dict[str, int], dict[tuple[int, ...], dict[str, object]]]:
    """Parse the exact materializer lifecycle without inferring from traffic."""
    events: dict[str, list[dict[str, str]]] = {}
    contexts: dict[tuple[int, ...], dict[str, object]] = {}
    with path.open(encoding="utf-8", errors="replace") as trace:
        for line in trace:
            value = fields(line)
            event = value.get("event")
            if event is None or not event.startswith("page_materialization_"):
                continue
            if event == "page_materialization_summary":
                current = value.get("dispatch_fallbacks")
                legacy = value.get("global_dispatch_fallbacks")
                if current is None and legacy is not None:
                    value["dispatch_fallbacks"] = legacy
                elif (
                    current is not None
                    and legacy is not None
                    and int(current, 0) != int(legacy, 0)
                ):
                    raise ValueError(
                        "page_materialization_summary: conflicting "
                        "dispatch fallback fields"
                    )
            events.setdefault(event, []).append(value)
            if event not in CONTEXT_EVENTS:
                continue
            key = tuple(
                integer_field(value, name, event)
                for name in ("token", "generation", "incarnation")
            )
            context = contexts.setdefault(
                key,
                {
                    "submit_pages": [],
                    "ready_pages": [],
                    "retire_pages": [],
                    "summaries": [],
                    "activation_counts": [],
                    "new_contexts": 0,
                    "forwarded_lines": 0,
                    "fragment_accumulated_lines": 0,
                    "fragment_buffer_stalls": 0,
                    "nonforwarded_ready_lines": 0,
                    "cache_read_lines": 0,
                    "line_commits": 0,
                },
            )
            if event == "page_materialization_submit":
                context["submit_pages"].append(
                    integer_field(value, "page", event)
                )
                context["activation_counts"].append(
                    integer_field(value, "activation_count", event)
                )
                context["new_contexts"] += integer_field(
                    value, "new_context", event
                )
            elif event == "page_materialization_page_ready":
                context["ready_pages"].append(
                    integer_field(value, "page", event)
                )
            elif event == "page_materialization_retire":
                context["retire_pages"].append(
                    integer_field(value, "pages", event)
                )
            elif event == "page_materialization_summary":
                context["summaries"].append(value)
            elif event == "page_materialization_producer_line_ready":
                if integer_field(value, "forwarded", event) == 1:
                    context["forwarded_lines"] += 1
                else:
                    context["nonforwarded_ready_lines"] += 1
                context["fragment_accumulated_lines"] += int(
                    value.get("fragment_accumulated", "0"), 0
                )
                context["fragment_buffer_stalls"] += int(
                    value.get("fragment_buffer_stall", "0"), 0
                )
            elif event == "page_materialization_read_response":
                context["cache_read_lines"] += 1
            elif event == "page_materialization_line_commit":
                context["line_commits"] += 1
            elif event in (
                "page_materialization_inactive_payload_replay",
                "page_materialization_inactive_masked_replay",
            ):
                context["forwarded_lines"] += 1

    fallback_events = sum(
        len(values)
        for name, values in events.items()
        if name
        in (
            "page_materialization_fallback",
            "page_materialization_dispatch_fallback",
        )
    )
    report = {
        "materializer_submits": len(
            events.get("page_materialization_submit", [])
        ),
        "materializer_pages_ready": len(
            events.get("page_materialization_page_ready", [])
        ),
        "materializer_summaries": len(
            events.get("page_materialization_summary", [])
        ),
        "materializer_retires": len(
            events.get("page_materialization_retire", [])
        ),
        "materializer_activation_retries": len(
            events.get("page_materialization_activation_retry", [])
        ),
        "materializer_prearms": len(
            events.get("page_materialization_prearm", [])
        ),
        "materializer_prearm_activations": len(
            events.get("page_materialization_prearm_activate", [])
        ),
        "materializer_fallback_events": fallback_events,
        "materializer_admission_fallback_events": len(
            events.get("page_materialization_fallback", [])
        ),
        "materializer_dispatch_fallback_events": len(
            events.get("page_materialization_dispatch_fallback", [])
        ),
        "materializer_forwarded_lines": sum(
            int(context["forwarded_lines"]) for context in contexts.values()
        ),
        "materializer_fragment_accumulated_lines": sum(
            int(context["fragment_accumulated_lines"])
            for context in contexts.values()
        ),
        "materializer_fragment_buffer_stalls": sum(
            int(context["fragment_buffer_stalls"])
            for context in contexts.values()
        ),
        "materializer_staged_direct_lines": sum(
            int(summary.get("staged_direct_lines", "0"), 0)
            for context in contexts.values()
            for summary in context["summaries"]
        ),
        "materializer_nonforwarded_ready_lines": sum(
            int(context["nonforwarded_ready_lines"])
            for context in contexts.values()
        ),
        "materializer_cache_read_lines": sum(
            int(context["cache_read_lines"]) for context in contexts.values()
        ),
        "materializer_line_commits": sum(
            int(context["line_commits"]) for context in contexts.values()
        ),
        "materializer_contexts": len(contexts),
        "materializer_contexts_created": sum(
            int(context["new_contexts"]) for context in contexts.values()
        ),
        "materializer_contexts_reused": sum(
            int(context["new_contexts"]) == 0 for context in contexts.values()
        ),
        "materializer_contexts_closed": 0,
        "materializer_contexts_open": len(contexts),
        "materializer_activation_count_max": max(
            (
                activation
                for context in contexts.values()
                for activation in context["activation_counts"]
            ),
            default=0,
        ),
    }
    return report, contexts


MATERIALIZER_STATS = (
    "page_materialization_submissions",
    "page_materialization_pages",
    "page_materialization_retirements",
    "page_materialization_forwarded_lines",
    "page_materialization_cache_read_fallback_lines",
    "page_materialization_dispatch_fallbacks",
    "page_materialization_admission_fallbacks",
    "page_materialization_producer_line_acks",
    "page_materialization_page_fallback_lines",
)

OPTIONAL_MATERIALIZER_STATS = (
    "page_materialization_fragment_accumulated_lines",
    "page_materialization_fragment_buffer_stalls",
    "page_materialization_staged_direct_lines",
    "page_materialization_staged_direct_fragments",
    "page_materialization_staged_direct_fallback_lines",
)

OPTIONAL_RETENTION_STATS = (
    "page_materialization_inactive_payload_captures",
    "page_materialization_inactive_payload_replays",
    "page_materialization_inactive_payload_conflicts",
    "page_materialization_inactive_payload_drops",
    "page_materialization_inactive_payload_first_owner_conflicts",
    "page_materialization_inactive_payload_write_port_stalls",
    "page_materialization_inactive_payload_read_port_stalls",
    "page_materialization_inactive_payload_lookup_hits",
    "page_materialization_inactive_payload_lookup_misses",
    "page_materialization_inactive_payload_high_water",
    "page_materialization_inactive_payload_bytes",
    "page_materialization_inactive_payload_control_bytes",
    "page_materialization_inactive_masked_fragments_accepted",
    "page_materialization_inactive_masked_words_merged",
    "page_materialization_inactive_masked_lines_reconstructed",
    "page_materialization_inactive_masked_replay_hits",
    "page_materialization_inactive_masked_replay_misses",
    "page_materialization_inactive_masked_tag_conflicts",
    "page_materialization_inactive_masked_overlap_poison",
    "page_materialization_inactive_masked_write_port_poison",
    "page_materialization_inactive_masked_stale_untracked_drops",
    "page_materialization_inactive_masked_read_port_stalls",
    "page_materialization_inactive_masked_clears",
    "page_materialization_inactive_masked_high_water",
    "page_materialization_inactive_masked_bytes",
    "page_materialization_inactive_masked_control_bytes",
)


def materializer_stat(stats: dict[str, float], suffix: str) -> int:
    name = f"system.maa.{suffix}"
    if name not in stats or not stats[name].is_integer():
        raise ValueError(f"missing or non-integral materializer stat {name}")
    return int(stats[name])


def optional_materializer_stat(
    stats: dict[str, float], suffix: str
) -> int | None:
    name = f"system.maa.{suffix}"
    if name not in stats:
        return None
    if not stats[name].is_integer():
        raise ValueError(f"non-integral materializer stat {name}")
    return int(stats[name])


def validate_materializer(
    arm: str,
    workload: str,
    role: str,
    selector: str | None,
    stats: dict[str, float],
    trace_path: Path,
) -> dict[str, int]:
    report, contexts = materializer_trace(trace_path)
    selected_mode = selector.split()[0] if selector is not None else ""
    token_arm = role.startswith(
        "token_stream_ld_"
    ) or selected_mode.startswith("token_stream_ld")
    control_arm = role in (
        "ordinary_stream_control",
        "page_gated_stream_control",
    )
    if control_arm:
        if any(
            report[name] != 0
            for name in (
                "materializer_submits",
                "materializer_pages_ready",
                "materializer_summaries",
                "materializer_retires",
                "materializer_fallback_events",
            )
        ):
            raise ValueError(f"{arm}: control activated the materializer")
        for name in MATERIALIZER_STATS:
            if materializer_stat(stats, name) != 0:
                raise ValueError(f"{arm}: control has nonzero {name}")
        for name in OPTIONAL_MATERIALIZER_STATS:
            value = optional_materializer_stat(stats, name)
            if value not in (None, 0):
                raise ValueError(f"{arm}: control has nonzero {name}")
        return report
    if not token_arm:
        return report

    stat_values = {
        name: materializer_stat(stats, name) for name in MATERIALIZER_STATS
    }
    expected_stats = {
        "page_materialization_submissions": "materializer_submits",
        "page_materialization_pages": "materializer_pages_ready",
        "page_materialization_retirements": "materializer_retires",
        "page_materialization_forwarded_lines": "materializer_forwarded_lines",
        "page_materialization_cache_read_fallback_lines": "materializer_cache_read_lines",
        "page_materialization_dispatch_fallbacks": "materializer_dispatch_fallback_events",
        "page_materialization_admission_fallbacks": "materializer_admission_fallback_events",
    }
    for stat_name, value in stat_values.items():
        report[f"stat_{stat_name}"] = value
    for stat_name, trace_name in expected_stats.items():
        if stat_values[stat_name] != report[trace_name]:
            raise ValueError(
                f"{arm}: {stat_name}={stat_values[stat_name]} does not "
                f"match trace {trace_name}={report[trace_name]}"
            )
    optional_expected = {
        "page_materialization_fragment_accumulated_lines": (
            "materializer_fragment_accumulated_lines"
        ),
        "page_materialization_fragment_buffer_stalls": (
            "materializer_fragment_buffer_stalls"
        ),
        "page_materialization_staged_direct_lines": (
            "materializer_staged_direct_lines"
        ),
    }
    for stat_name, trace_name in optional_expected.items():
        value = optional_materializer_stat(stats, stat_name)
        if value is None:
            continue
        report[f"stat_{stat_name}"] = value
        if value != report[trace_name]:
            raise ValueError(
                f"{arm}: {stat_name}={value} does not match trace "
                f"{trace_name}={report[trace_name]}"
            )
    for stat_name in (
        "page_materialization_staged_direct_fragments",
        "page_materialization_staged_direct_fallback_lines",
    ):
        value = optional_materializer_stat(stats, stat_name)
        if value is not None:
            report[f"stat_{stat_name}"] = value
    for stat_name in OPTIONAL_RETENTION_STATS:
        value = optional_materializer_stat(stats, stat_name)
        if value is not None:
            report[f"stat_{stat_name}"] = value
    if (
        report["materializer_fallback_events"] != 0
        or stat_values["page_materialization_dispatch_fallbacks"] != 0
        or stat_values["page_materialization_admission_fallbacks"] != 0
    ):
        raise ValueError(
            f"{arm}: materializer fell back to ordinary STREAM_LD"
        )
    if role == "token_stream_ld_page0_prearm_correctness_control":
        prearms = report["materializer_prearms"]
        activations = report["materializer_prearm_activations"]
        if (
            prearms != 1
            or activations != 1
            or report["materializer_activation_retries"] != 0
        ):
            raise ValueError(
                f"{arm}: page-zero prearm did not queue and activate exactly"
            )
    # No direct-retirement descriptor is used by these non-fused token arms.
    if materializer_stat(stats, "direct_retirement_fallbacks") != 0:
        raise ValueError(f"{arm}: direct-retirement fallback stat is nonzero")
    report["stat_direct_retirement_fallbacks"] = 0

    expected_pages = [0, 1, 2, 3]
    activation_counts: list[int] = []
    closed = 0
    for key, context in contexts.items():
        submits = sorted(context["submit_pages"])
        ready = sorted(context["ready_pages"])
        retire_pages = context["retire_pages"]
        summaries = context["summaries"]
        new_contexts = int(context["new_contexts"])
        valid_context_origins = {1} if workload == "api" else {0, 1}
        if (
            submits != expected_pages
            or ready != expected_pages
            or retire_pages != [4]
            or len(summaries) != 1
            or new_contexts not in valid_context_origins
        ):
            raise ValueError(
                f"{arm}: materializer context {key} did not close"
            )
        summary = summaries[0]
        if (
            summary.get("exact_closure") != "1"
            or integer_field(summary, "pages", "summary") != 4
            or integer_field(summary, "dispatch_fallbacks", "summary") != 0
        ):
            raise ValueError(
                f"{arm}: context {key} lacks exact summary closure"
            )
        summary_lines = integer_field(summary, "lines", "summary")
        summary_forwarded = integer_field(
            summary, "forwarded_lines", "summary"
        )
        summary_staged_direct = int(summary.get("staged_direct_lines", "0"), 0)
        summary_cache_reads = integer_field(
            summary, "cache_read_fallback_lines", "summary"
        )
        summary_producer_acks = integer_field(
            summary, "producer_line_acks", "summary"
        )
        summary_page_fallbacks = integer_field(
            summary, "page_fallback_lines", "summary"
        )
        if (
            summary_forwarded != context["forwarded_lines"]
            or summary_cache_reads != context["cache_read_lines"]
            or summary_forwarded + summary_staged_direct + summary_cache_reads
            != summary_lines
            or summary_producer_acks + summary_page_fallbacks != summary_lines
            or context["line_commits"] != summary_lines
        ):
            raise ValueError(
                f"{arm}: context {key} line accounting did not close"
            )
        activation_counts.extend(context["activation_counts"])
        closed += 1
    report["materializer_contexts_closed"] = closed
    report["materializer_contexts_open"] = len(contexts) - closed
    if sorted(activation_counts) != list(range(1, len(activation_counts) + 1)):
        raise ValueError(f"{arm}: activation_count sequence did not close")

    if workload == "api":
        if (
            report["materializer_submits"] != 4
            or report["materializer_pages_ready"] != 4
            or report["materializer_retires"] != 1
            or closed != 1
        ):
            raise ValueError(
                f"{arm}: API requires exactly 4 submits, 4 page-ready "
                "events, 1 retire, and 1 closed context"
            )
    elif (
        report["materializer_submits"] != report["materializer_pages_ready"]
        or report["materializer_retires"] <= 0
        or report["materializer_retires"] != closed
        or report["materializer_summaries"] != closed
    ):
        raise ValueError(
            f"{arm}: workload submit/page-ready/retire contexts did not close"
        )
    return report


def read_exit(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError) as error:
        raise ValueError(f"invalid exit marker: {path}") from error


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def analyze(root: Path) -> dict[str, object]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "dx100.general_hybrid_matrix.v1":
        raise ValueError("unsupported or missing matrix manifest")
    workload = str(manifest["workload"])
    replicas = int(manifest["replicas"])
    arms = manifest["arms"]
    if not isinstance(arms, list) or replicas < 1:
        raise ValueError("invalid arm/replica contract")

    arm_names = [str(arm["name"]) for arm in arms]
    if len(arm_names) != len(set(arm_names)):
        raise ValueError("duplicate arm in manifest")
    required = {"native16", "native4"}
    if any(arm.get("profile") == "hybrid" for arm in arms):
        required |= {
            "hybrid_stream_control",
            "hybrid_page_gated",
            "hybrid_token_stream_ld",
        }
    if not required.issubset(arm_names):
        raise ValueError(
            f"missing required arms: {sorted(required - set(arm_names))}"
        )

    restore_runs = manifest.get("restore_runs")
    if not isinstance(restore_runs, list):
        raise ValueError("manifest lacks restore-run selector provenance")
    restore_metadata: dict[tuple[str, int], dict[str, object]] = {}
    for value in restore_runs:
        if not isinstance(value, dict):
            raise ValueError("invalid restore-run metadata")
        key = (str(value.get("arm")), int(value.get("replica", 0)))
        if key in restore_metadata:
            raise ValueError(f"duplicate restore-run metadata: {key}")
        restore_metadata[key] = value
    expected_runs = {
        (arm_name, replica)
        for arm_name in arm_names
        for replica in range(1, replicas + 1)
    }
    if set(restore_metadata) != expected_runs:
        raise ValueError("restore-run metadata does not match arm replicas")

    records: list[dict[str, object]] = []
    for arm in arms:
        arm_name = str(arm["name"])
        selector = arm.get("selector")
        for replica in range(1, replicas + 1):
            metadata = restore_metadata[(arm_name, replica)]
            run = root / "arms" / arm_name / f"replica-{replica}"
            if read_exit(run / "restore.exit") != 0:
                raise ValueError(f"{arm_name}/{replica}: nonzero restore exit")
            log_path = run / "restore.log"
            log = log_path.read_text(encoding="utf-8", errors="replace")
            if FATAL.search(log):
                raise ValueError(f"{arm_name}/{replica}: fatal text in log")
            if len(EXIT.findall(log)) != 1:
                raise ValueError(
                    f"{arm_name}/{replica}: expected exactly one terminal m5_exit"
                )
            if selector is not None:
                selector_value = metadata.get("selector_path")
                selector_hash = metadata.get("selector_sha256")
                if not isinstance(selector_value, str) or not isinstance(
                    selector_hash, str
                ):
                    raise ValueError(
                        f"{arm_name}/{replica}: missing selector provenance"
                    )
                selector_path = Path(selector_value).resolve()
                try:
                    selector_path.relative_to((root / "checkpoints").resolve())
                except ValueError as error:
                    raise ValueError(
                        f"{arm_name}/{replica}: selector is outside checkpoints"
                    ) from error
                if sha256_file(selector_path) != selector_hash:
                    raise ValueError(
                        f"{arm_name}/{replica}: selector hash mismatch"
                    )
                treatment = selector_path.read_text(encoding="utf-8").strip()
                if treatment != selector:
                    raise ValueError(
                        f"{arm_name}/{replica}: selector mismatch"
                    )
                selected_mode = str(selector).split()[0]
                if f"mode={selected_mode}" not in log:
                    raise ValueError(
                        f"{arm_name}/{replica}: no restored selector marker"
                    )
            elif (
                metadata.get("selector_path") is not None
                or metadata.get("selector_sha256") is not None
            ):
                raise ValueError(
                    f"{arm_name}/{replica}: native arm has selector metadata"
                )
            key, marker = correctness(workload, log)
            stats = first_stats(run / "gem5/stats.txt")
            role = str(arm["role"])
            mechanism = validate_materializer(
                f"{arm_name}/{replica}",
                workload,
                role,
                None if selector is None else str(selector),
                stats,
                run / "gem5/virtual_trace.log",
            )
            record: dict[str, object] = {
                "arm": arm_name,
                "replica": replica,
                "role": arm["role"],
                "profile": arm["profile"],
                "correctness_key": key,
                "correctness_marker": marker,
                "simTicks": int(stats["simTicks"]),
                **stream_report(stats),
                **opcode_report(stats),
                **mechanism,
            }
            records.append(record)

    keys = {str(record["correctness_key"]) for record in records}
    if len(keys) != 1:
        raise ValueError(
            f"cross-arm exact correctness mismatch: {sorted(keys)}"
        )
    native16_ticks = [
        int(record["simTicks"])
        for record in records
        if record["arm"] == "native16"
    ]
    native4_ticks = [
        int(record["simTicks"])
        for record in records
        if record["arm"] == "native4"
    ]
    native16 = sum(native16_ticks) / len(native16_ticks)
    native4 = sum(native4_ticks) / len(native4_ticks)
    native16_maa_total = sum(
        int(record["cycles_TOTAL"])
        for record in records
        if record["arm"] == "native16"
    ) / len(native16_ticks)
    native16_rmw_cycles = sum(
        int(record["cycles_INDRMW"])
        for record in records
        if record["arm"] == "native16"
    ) / len(native16_ticks)
    native16_rmw_insts = sum(
        int(record["numInst_INDRMW"])
        for record in records
        if record["arm"] == "native16"
    ) / len(native16_ticks)
    opportunity = native4 - native16
    for record in records:
        ticks = int(record["simTicks"])
        record["speedup_vs_native16"] = native16 / ticks
        record["latency_gap_pct_vs_native16"] = (
            (ticks / native16) - 1.0
        ) * 100.0
        record["speedup_vs_native4"] = native4 / ticks
        record["opportunity_recovered_pct"] = (
            ((native4 - ticks) / opportunity) * 100.0
            if opportunity > 0
            else None
        )
        maa_gap = int(record["cycles_TOTAL"]) - native16_maa_total
        rmw_gap = int(record["cycles_INDRMW"]) - native16_rmw_cycles
        record["maa_total_cycles_gap_vs_native16"] = maa_gap
        record["rmw_cycles_gap_vs_native16"] = rmw_gap
        record["rmw_gap_fraction_of_maa_total_gap"] = (
            rmw_gap / maa_gap if maa_gap > 0 else None
        )
        record["rmw_instruction_count_ratio_vs_native16"] = (
            int(record["numInst_INDRMW"]) / native16_rmw_insts
            if native16_rmw_insts > 0
            else None
        )

    return {
        "schema": "dx100.general_hybrid_analysis.v1",
        "status": "PASS",
        "workload": workload,
        "exact_correctness_key": next(iter(keys)),
        "records": records,
        "interpretation": {
            "token_stream_ld": "correctness control, not final treatment",
            "stream_store_contention": (
                "raw store count plus all-stream request/SPD occupancy; "
                "store-only cycles unavailable in this source revision"
            ),
            "rmw_gap_fraction": (
                "diagnostic ratio only; opcode cycle categories can overlap "
                "and must not be added as disjoint wall-clock intervals"
            ),
        },
    }


def write_outputs(root: Path, report: dict[str, object]) -> None:
    analysis = root / "analysis"
    analysis.mkdir(exist_ok=True)
    (analysis / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    records = report["records"]
    assert isinstance(records, list)
    columns = [
        "arm",
        "replica",
        "role",
        "profile",
        "correctness_key",
        "simTicks",
        "speedup_vs_native16",
        "latency_gap_pct_vs_native16",
        "speedup_vs_native4",
        "opportunity_recovered_pct",
        "numInst_INDRD",
        "numInst_INDRMW",
        "cycles_INDRD",
        "cycles_INDRMW",
        "cycles_TOTAL",
        "maa_total_cycles_gap_vs_native16",
        "rmw_cycles_gap_vs_native16",
        "rmw_gap_fraction_of_maa_total_gap",
        "rmw_instruction_count_ratio_vs_native16",
        "num_stream_loads",
        "num_stream_stores",
        "aggregate_cycles_STRRD_raw",
        "aggregate_cycles_STRWR_raw",
        "all_stream_request_cycles",
        "all_stream_spd_read_cycles",
        "all_stream_spd_write_cycles",
        "materializer_submits",
        "materializer_pages_ready",
        "materializer_retires",
        "materializer_summaries",
        "materializer_contexts",
        "materializer_contexts_created",
        "materializer_contexts_reused",
        "materializer_contexts_closed",
        "materializer_contexts_open",
        "materializer_activation_count_max",
        "materializer_activation_retries",
        "materializer_prearms",
        "materializer_prearm_activations",
        "materializer_fallback_events",
        "materializer_forwarded_lines",
        "materializer_fragment_accumulated_lines",
        "materializer_fragment_buffer_stalls",
        "materializer_staged_direct_lines",
        "materializer_cache_read_lines",
        "stat_page_materialization_fragment_accumulated_lines",
        "stat_page_materialization_fragment_buffer_stalls",
        "stat_page_materialization_staged_direct_lines",
        "stat_page_materialization_staged_direct_fragments",
        "stat_page_materialization_staged_direct_fallback_lines",
        "stat_page_materialization_inactive_masked_fragments_accepted",
        "stat_page_materialization_inactive_masked_words_merged",
        "stat_page_materialization_inactive_masked_lines_reconstructed",
        "stat_page_materialization_inactive_masked_replay_hits",
        "stat_page_materialization_inactive_masked_replay_misses",
        "stat_page_materialization_inactive_masked_tag_conflicts",
        "stat_page_materialization_inactive_masked_overlap_poison",
        "stat_page_materialization_inactive_masked_write_port_poison",
        "stat_page_materialization_inactive_masked_stale_untracked_drops",
        "stat_page_materialization_inactive_masked_read_port_stalls",
        "stat_page_materialization_inactive_masked_high_water",
        "stat_page_materialization_inactive_masked_bytes",
        "stat_page_materialization_inactive_masked_control_bytes",
        "stat_page_materialization_producer_line_acks",
        "stat_page_materialization_page_fallback_lines",
        "materializer_nonforwarded_ready_lines",
        "materializer_line_commits",
    ]
    with (analysis / "report.tsv").open(
        "w", newline="", encoding="utf-8"
    ) as out:
        writer = csv.DictWriter(
            out, fieldnames=columns, delimiter="\t", extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(records)
    lines = [
        "# General hybrid matched matrix",
        "",
        f"Status: **{report['status']}**. Exact correctness key: "
        f"`{report['exact_correctness_key']}`.",
        "",
        "`token_stream_ld` is a correctness control, not an optimized-treatment "
        "claim. `cycles_STRWR` is preserved raw; this source revision charges "
        "all stream completions to `cycles_STRRD`, so the table also exposes "
        "store instruction count and total stream request/SPD occupancy.",
        "",
        "| arm | rep | ticks | gap vs native16 | speedup vs native4 | INDRD/RMW insts | INDRD/RMW cycles | MAA total | RMW share of MAA gap | submits/ready/retire | forwarded/cache-read lines |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for record in records:
        rmw_fraction = record["rmw_gap_fraction_of_maa_total_gap"]
        rmw_fraction_text = (
            "n/a" if rmw_fraction is None else f"{float(rmw_fraction):.3f}"
        )
        lines.append(
            "| {arm} | {replica} | {simTicks} | "
            "{latency_gap_pct_vs_native16:.3f}% | {speedup_vs_native4:.6f} | "
            "{numInst_INDRD}/{numInst_INDRMW} | "
            "{cycles_INDRD}/{cycles_INDRMW} | {cycles_TOTAL} | "
            "{rmw_fraction_text} | "
            "{materializer_submits}/{materializer_pages_ready}/"
            "{materializer_retires} | "
            "{materializer_forwarded_lines}/"
            "{materializer_cache_read_lines} |".format(
                **record, rmw_fraction_text=rmw_fraction_text
            )
        )
    (analysis / "report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} MATRIX_ROOT", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).resolve()
    try:
        report = analyze(root)
        write_outputs(root, report)
    except (KeyError, OSError, TypeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"PASS: {len(report['records'])} exact matched runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
