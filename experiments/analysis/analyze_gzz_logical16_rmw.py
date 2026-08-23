#!/usr/bin/env python3
"""Fail-closed validation for the full GZZ logical-16 volume RMW arm."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.scripts.run_gzp_combined_optimization_matrix import (  # noqa: E402
    TRACE_TO_STAT,
    fields,
    first_stats,
    integer,
    parse_pair,
    stat,
)

ARM = "volume_logical16"
WINDOW_ELEMENTS = 16_384
FULL_WINDOWS = 61
FULL_SELECTED = 949_452
FULL_REJECTED = 49_972
TOTAL_SELECTED = 950_000
TOTAL_REJECTED = 50_000
EXPECTED_OUTPUT_HASH = "9234467062988358067"
EXPECTED_RMW_INSTRUCTIONS = 307


def one_marker(text: str, prefix: str, label: str) -> dict[str, str]:
    matches = [line for line in text.splitlines() if line.startswith(prefix)]
    if len(matches) != 1:
        raise RuntimeError(
            f"{label}: expected one {prefix.strip()} marker, got {len(matches)}"
        )
    return fields(matches[0])


def exact_output(text: str, label: str) -> None:
    output = one_marker(text, "UME_OUTPUT_FP ", label)
    reference = one_marker(text, "UME_REFERENCE_PASS ", label)
    if output.get("output_hash") != EXPECTED_OUTPUT_HASH:
        raise RuntimeError(f"{label}: unexpected output hash")
    if output.get("nonfinite") != "0":
        raise RuntimeError(f"{label}: non-finite output")
    if (
        reference.get("volume_errors") != "0"
        or reference.get("gradient_errors") != "0"
        or reference.get("elements") != "1000128"
    ):
        raise RuntimeError(f"{label}: scalar reference did not pass exactly")


def terminal_marker(text: str, label: str) -> dict[str, int | str]:
    marker = one_marker(text, "UME_GZZ_RMW_TERMINAL ", label)
    expected = {
        "treatment": "volume_masked_index_soa_jit",
        "logical16_volume_windows": str(FULL_WINDOWS),
        "selected": str(TOTAL_SELECTED),
        "rejected": str(TOTAL_REJECTED),
        "active_sentinel": "0",
        "inactive_non_sentinel": "0",
        "hidden_logical16_payload_bytes": "0",
        "result": "PASS",
    }
    for key, value in expected.items():
        if marker.get(key) != value:
            raise RuntimeError(
                f"{label}: terminal {key}={marker.get(key)!r}, expected {value!r}"
            )
    return {
        "index_hash": marker["index_hash"],
        "selected": int(marker["selected"]),
        "rejected": int(marker["rejected"]),
    }


def trace_totals(path: Path, label: str) -> dict[str, int]:
    rows: list[dict[str, int]] = []
    for line in path.open(errors="replace"):
        if "event=soa_jit_complete" not in line or "terminal=1" not in line:
            continue
        event = fields(line)
        for name in (
            "logical",
            "selected",
            "predicate_rejected",
            "predicate_mode",
            "masked_index_additional_buffer_bytes",
            "predicate_lines",
            "predicate_uses",
            "a_reads",
            "value_reads",
            "fills",
            "cached",
            "deliveries",
            "aliases",
            "lookahead",
            "pre_a",
            "a_writes",
            "active_contexts",
            "active_value_owners",
        ):
            if name not in event:
                raise RuntimeError(f"{label}: incomplete terminal trace")
        if (
            integer(event, "logical", label) != WINDOW_ELEMENTS
            or event["predicate_mode"] != "masked_index"
            or integer(event, "masked_index_additional_buffer_bytes", label)
            != 0
            or integer(event, "active_contexts", label) != 64
            or integer(event, "active_value_owners", label) != 64
        ):
            raise RuntimeError(
                f"{label}: logical geometry or treatment changed"
            )

        selected = integer(event, "selected", label)
        rejected = integer(event, "predicate_rejected", label)
        predicate_issue, predicate_response = parse_pair(
            event["predicate_lines"], "predicate-line"
        )
        a_read_issue, a_read_response = parse_pair(event["a_reads"], "A-read")
        value_issue, value_response = parse_pair(
            event["value_reads"], "value-read"
        )
        lookahead_issue, lookahead_response = parse_pair(
            event["lookahead"], "lookahead"
        )
        pre_a_issue, pre_a_ready, pre_a_uses = parse_pair(
            event["pre_a"], "pre-A", fields_count=3
        )
        a_write_issue, a_write_response = parse_pair(
            event["a_writes"], "A-write"
        )
        fills = integer(event, "fills", label)
        cached = integer(event, "cached", label)
        deliveries = integer(event, "deliveries", label)
        aliases = integer(event, "aliases", label)
        if selected + rejected != WINDOW_ELEMENTS:
            raise RuntimeError(f"{label}: logical window does not close")
        if any(
            issue != response
            for issue, response in (
                (predicate_issue, predicate_response),
                (a_read_issue, a_read_response),
                (value_issue, value_response),
                (lookahead_issue, lookahead_response),
                (a_write_issue, a_write_response),
            )
        ):
            raise RuntimeError(f"{label}: request/response ledger is open")
        if (
            predicate_issue != 0
            or integer(event, "predicate_uses", label) != 0
            or value_issue != fills
            or fills != cached
            or deliveries != selected
            or aliases != selected
            or pre_a_issue != pre_a_uses
            or not 0 <= pre_a_ready <= pre_a_issue
        ):
            raise RuntimeError(
                f"{label}: terminal value/predicate ledger is invalid"
            )
        rows.append(
            {
                "selected": selected,
                "rejected": rejected,
                "predicate_lines_issue": predicate_issue,
                "predicate_lines_response": predicate_response,
                "a_reads_issue": a_read_issue,
                "a_reads_response": a_read_response,
                "value_reads_issue": value_issue,
                "value_reads_response": value_response,
                "fills": fills,
                "cached": cached,
                "deliveries": deliveries,
                "lookahead_issue": lookahead_issue,
                "lookahead_response": lookahead_response,
                "pre_a_issue": pre_a_issue,
                "pre_a_ready": pre_a_ready,
                "pre_a_uses": pre_a_uses,
                "aliases": aliases,
                "a_writes_issue": a_write_issue,
                "a_writes_response": a_write_response,
            }
        )
    if len(rows) != FULL_WINDOWS:
        raise RuntimeError(
            f"{label}: expected {FULL_WINDOWS} terminal windows, got {len(rows)}"
        )
    totals = {name: sum(row[name] for row in rows) for name in TRACE_TO_STAT}
    if (
        totals["selected"] != FULL_SELECTED
        or totals["rejected"] != FULL_REJECTED
    ):
        raise RuntimeError(f"{label}: frozen full-window selection changed")
    return totals


def validate_run(run: Path, replica: int) -> dict[str, Any]:
    label = f"{ARM}/replica-{replica}"
    if (run / "restore.exit").read_text().strip() != "0":
        raise RuntimeError(f"{label}: restore did not exit zero")
    log = (run / "restore.log").read_text(errors="replace")
    if log.count("because m5_exit instruction encountered") != 1:
        raise RuntimeError(f"{label}: expected one terminal m5_exit")
    exact_output(log, label)
    marker = terminal_marker(log, label)
    totals = trace_totals(run / "gem5" / "virtual_trace.log", label)
    stats = first_stats(run / "gem5" / "stats.txt")
    for trace_name, stat_suffix in TRACE_TO_STAT.items():
        if stat(stats, stat_suffix, label) != totals[trace_name]:
            raise RuntimeError(
                f"{label}: stats/trace mismatch for {stat_suffix}"
            )
    if stat(stats, "IND_SoaJitTerminalCompletions", label) != FULL_WINDOWS:
        raise RuntimeError(f"{label}: SoA/JIT terminal count changed")
    if stat(stats, "numInst_INDRMW", label) != EXPECTED_RMW_INSTRUCTIONS:
        raise RuntimeError(f"{label}: RMW instruction count changed")
    return {
        "replica": replica,
        "simTicks": stat(stats, "simTicks", label),
        "index_hash": marker["index_hash"],
        **totals,
    }


def analyze(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("workload") != "ume-gzz":
        raise RuntimeError("manifest is not UME GZZ")
    arms = {arm["name"]: arm for arm in manifest["arms"]}
    arm = arms.get(ARM)
    if (
        arm is None
        or arm.get("selector") != "token_stream_ld volume_masked_index"
    ):
        raise RuntimeError("missing exact volume_logical16 treatment")
    replicas = int(manifest.get("replicas", 0))
    if replicas < 2:
        raise RuntimeError("at least two replicas are required")
    rows = [
        validate_run(root / "arms" / ARM / f"replica-{replica}", replica)
        for replica in range(1, replicas + 1)
    ]
    if len({row["simTicks"] for row in rows}) != 1:
        raise RuntimeError("treatment replicas are not deterministic")
    if len({row["index_hash"] for row in rows}) != 1:
        raise RuntimeError("treatment index hashes differ")
    report = {
        "decision": "PASS",
        "arm": ARM,
        "replicas": replicas,
        "simTicks": rows[0]["simTicks"],
        "rows": rows,
    }
    (root / "gzz_logical16_validation.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    try:
        report = analyze(args.root.resolve())
    except (KeyError, OSError, RuntimeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
