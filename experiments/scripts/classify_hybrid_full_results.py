#!/usr/bin/env python3
"""Classify supplied hybrid full-application evidence roots once, fail-closed.

This intentionally reads files only.  A root is *running* only when its owner
wrote ``RUNNING.status`` containing ``running``; a PID, process-exit note, or
the absence of either never establishes completion.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Callable

FATAL = re.compile(r"panic|fatal|assert|abort|segmentation fault|error:", re.I)
EXIT = re.compile(
    r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$", re.M
)
STATS_BEGIN = "---------- Begin Simulation Statistics"
STATS_END = "---------- End Simulation Statistics"


def text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def running(root: Path) -> bool:
    return (text(root / "RUNNING.status") or "").strip() == "running"


def first_roi_ticks(stats: Path, reasons: list[str]) -> int | None:
    data = text(stats)
    if not data:
        reasons.append("missing or unreadable run/stats.txt")
        return None
    begin, end = data.find(STATS_BEGIN), data.find(STATS_END)
    if begin < 0 or end < begin:
        reasons.append("malformed first statistics window")
        return None
    matches = re.findall(r"^simTicks\s+([0-9]+)\b", data[begin:end], re.M)
    if len(matches) != 1 or int(matches[0]) <= 0:
        reasons.append("first statistics window lacks one positive simTicks")
        return None
    return int(matches[0])


def common(root: Path, log_name: str, reasons: list[str]) -> str | None:
    log = text(root / log_name)
    if not log:
        reasons.append(f"missing or unreadable {log_name}")
        return None
    if FATAL.search(log):
        reasons.append("simulator fatal evidence")
    if len(EXIT.findall(log)) != 1:
        reasons.append("requires exactly one m5_exit marker")
    return log


def classify_cg(root: Path) -> dict:
    reasons: list[str] = []
    log = common(root, "run/restore.log", reasons)
    result = text(root / "result.txt")
    if (
        not result
        or "terminal=true\n" not in result
        or "correct=true\n" not in result
    ):
        reasons.append("missing CG terminal/correct result certificate")
    if not (root / "gate.complete").is_file():
        reasons.append("missing CG gate.complete")
    if (
        not log
        or len(re.findall(r"^CG_FINGERPRINT .* result=PASS$", log, re.M)) != 1
    ):
        reasons.append(
            "wrong CG fingerprint result"
            if log and "CG_FINGERPRINT" in log
            else "requires one passing CG fingerprint"
        )
    if (
        not log
        or len(
            re.findall(
                r"^CG_LOGICAL16_RMW_TERMINAL .* result=PASS$", log, re.M
            )
        )
        != 1
    ):
        reasons.append("requires one passing CG terminal")
    if not log or log.count("ROI End!!!") != 1:
        reasons.append("requires one CG ROI marker")
    return result_for("cg", root, reasons)


def classify_is(root: Path) -> dict:
    reasons: list[str] = []
    log = common(root, "run/restore.log", reasons)
    if not log or log.count("ROI End!!!") != 1:
        reasons.append("requires one IS ROI marker")
    if not log or log.count("successfull: passed verification 6") != 1:
        reasons.append("requires exact NAS IS verification")
    if (
        not log
        or len(
            re.findall(
                r"^IS_SCALAR_SOA_JIT_TERMINAL .*result=PASS$", log, re.M
            )
        )
        != 1
    ):
        reasons.append("requires one passing IS terminal")
    return result_for("is", root, reasons)


def classify_hashjoin(root: Path, kernel: str) -> dict:
    reasons: list[str] = []
    arm = root / kernel
    log = common(arm, "run/run.log", reasons)
    rows = text(root / "results.tsv")
    if not rows:
        reasons.append("missing HashJoin results.tsv")
    else:
        found = [
            line.split("\t")
            for line in rows.splitlines()[1:]
            if line.startswith(kernel + "\t")
        ]
        if len(found) != 1 or len(found[0]) != 6 or found[0][1] != "2000000":
            reasons.append(
                f"requires one exact {kernel} result row with 2000000 matches"
            )
    if (
        not log
        or len(
            re.findall(
                rf"^HASHJOIN_HYBRID_TERMINAL kernel={kernel} .*result=PASS$",
                log,
                re.M,
            )
        )
        != 1
    ):
        reasons.append(f"requires one passing {kernel} terminal")
    if (
        not log
        or len(re.findall(r"^Hash join result: 2000000$", log, re.M)) != 1
    ):
        reasons.append("requires exact HashJoin cardinality")
    if not log or log.count("ROI End!!!") != 1:
        reasons.append("requires one HashJoin ROI marker")
    return result_for(
        f"hashjoin-{kernel.lower()}", arm, reasons, display_root=root
    )


def classify_sssp(root: Path) -> dict:
    reasons: list[str] = []
    log = common(root, "run/restore.log", reasons)
    for name in ("checkpoint.exit", "run/restore.exit"):
        if (text(root / name) or "").strip() != "0":
            reasons.append(f"{name} is not explicit zero")
    if not (root / "gate.complete").is_file() or "validation=PASS" not in (
        text(root / "result.txt") or ""
    ):
        reasons.append("missing SSSP passed wrapper/gate evidence")
    if (
        not log
        or len(re.findall(r"^SSSP_FINGERPRINT .* result=PASS$", log, re.M))
        != 1
    ):
        reasons.append("requires one passing SSSP fingerprint")
    if (
        not log
        or len(
            re.findall(
                r"^SSSP_OLD_RESULT_HYBRID_TERMINAL .*counts_close=1", log, re.M
            )
        )
        != 1
    ):
        reasons.append("requires closed SSSP old-result terminal")
    if not log or log.count("ROI End!!!") != 1:
        reasons.append("requires one SSSP ROI marker")
    stats = text(root / "run/stats.txt") or ""
    if stats.count(STATS_BEGIN) != 2 or stats.count(STATS_END) != 2:
        reasons.append("SSSP requires exactly two complete statistics windows")
    return result_for("sssp", root, reasons)


def result_for(
    workload: str,
    root: Path,
    reasons: list[str],
    *,
    display_root: Path | None = None,
) -> dict:
    # Performance evidence is deliberately unread until the workload-specific
    # terminal and correctness checks above have all passed.
    ticks = (
        first_roi_ticks(root / "run" / "stats.txt", reasons)
        if not reasons
        else None
    )
    status = (
        "correctness-failed"
        if any("fatal" in r or r.startswith("wrong ") for r in reasons)
        else "incomplete"
    )
    if not reasons:
        status = "terminal-valid"
    elif running(root):
        status = "running"
    return {
        "workload": workload,
        "root": str(display_root or root),
        "status": status,
        "first_roi_simTicks": ticks if status == "terminal-valid" else None,
        "reasons": reasons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cg", type=Path)
    parser.add_argument("--is", dest="is_root", type=Path)
    parser.add_argument("--hashjoin-pro", type=Path)
    parser.add_argument("--hashjoin-prh", type=Path)
    parser.add_argument("--sssp", type=Path)
    parser.add_argument(
        "--baseline",
        type=Path,
        help="optional frozen JSON metadata; used only for displayed comparisons",
    )
    args = parser.parse_args()
    supplied: list[tuple[Path | None, Callable[[Path], dict]]] = [
        (args.cg, classify_cg),
        (args.is_root, classify_is),
        (args.hashjoin_pro, lambda root: classify_hashjoin(root, "PRO")),
        (args.hashjoin_prh, lambda root: classify_hashjoin(root, "PRH")),
        (args.sssp, classify_sssp),
    ]
    if not any(root for root, _ in supplied):
        parser.error("supply at least one explicit workload root")
    records = [fn(root) for root, fn in supplied if root]
    output = {
        "schema": "dx100.hybrid.full.classification.v1",
        "one_shot": True,
        "results": records,
    }
    if args.baseline:
        try:
            output["frozen_baseline_metadata"] = json.loads(
                args.baseline.read_text()
            )
        except (OSError, json.JSONDecodeError) as error:
            parser.error(f"invalid --baseline: {error}")
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
