#!/usr/bin/env python3
"""Bound same-tick direct-index request-generation work from a strict trace."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import re
from pathlib import Path

EVENT = " event=index_line_issue "
OPERATION_RE = re.compile(r"operation_tick=([0-9]+)")


class AnalysisError(RuntimeError):
    pass


def analyze(
    trace: Path,
    configured_depth: int,
    sim_ticks: int,
    ticks_per_cycle: float,
    widths: tuple[int, ...],
) -> dict[str, object]:
    if configured_depth <= 0 or sim_ticks <= 0 or ticks_per_cycle <= 0:
        raise AnalysisError(
            "depth, simTicks, and ticks/cycle must be positive"
        )
    if not widths or any(width <= 0 for width in widths):
        raise AnalysisError("issue widths must be positive")

    digest = hashlib.sha256()
    groups: collections.Counter[tuple[int, int]] = collections.Counter()
    operations: dict[int, list[int]] = collections.defaultdict(list)
    events = 0
    with trace.open("rb") as source:
        for raw in source:
            digest.update(raw)
            if EVENT.encode() not in raw:
                continue
            line = raw.decode(errors="replace")
            match = OPERATION_RE.search(line)
            if match is None:
                raise AnalysisError("index issue lacks operation identity")
            try:
                tick = int(line.split(":", 1)[0])
            except ValueError as error:
                raise AnalysisError("index issue lacks event tick") from error
            operation = int(match.group(1))
            groups[(operation, tick)] += 1
            operations[operation].append(tick)
            events += 1

    if events == 0:
        raise AnalysisError("trace has no direct-index issue events")
    bursts = list(groups.values())
    first_bursts = [
        groups[(operation, ticks[0])]
        for operation, ticks in operations.items()
    ]
    bounds = {}
    for width in widths:
        extra_cycles = sum(
            max(0, math.ceil(burst / width) - 1) for burst in bursts
        )
        extra_ticks = extra_cycles * ticks_per_cycle
        bounds[str(width)] = {
            "extra_generation_cycles_upper_bound": extra_cycles,
            "extra_sim_ticks_upper_bound": extra_ticks,
            "share_of_observed_simTicks_pct": 100 * extra_ticks / sim_ticks,
        }

    return {
        "schema": "dx100.direct_index_issue_width.v1",
        "trace": str(trace.resolve()),
        "trace_sha256": digest.hexdigest(),
        "configured_depth": configured_depth,
        "operations": len(operations),
        "issue_events": events,
        "unique_enqueue_ticks": len(groups),
        "maximum_same_tick_burst": max(bursts),
        "mean_events_per_enqueue_tick": events / len(groups),
        "initial_burst_min": min(first_bursts),
        "initial_burst_max": max(first_bursts),
        "operations_reaching_configured_depth": sum(
            max(
                burst
                for (operation_id, _), burst in groups.items()
                if operation_id == operation
            )
            == configured_depth
            for operation in operations
        ),
        "simTicks": sim_ticks,
        "ticks_per_cycle": ticks_per_cycle,
        "serialization_bounds": bounds,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--depth", type=int, required=True)
    parser.add_argument("--sim-ticks", type=int, required=True)
    parser.add_argument("--ticks-per-cycle", type=float, default=312.5)
    parser.add_argument("--widths", type=int, nargs="+", default=(1, 2, 4, 8))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = analyze(
        args.trace,
        args.depth,
        args.sim_ticks,
        args.ticks_per_cycle,
        tuple(args.widths),
    )
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
