#!/usr/bin/env python3
"""Replay SoA/JIT value requests through ideal bounded LRU owner pools.

This estimates line-read reuse only. It does not model response timing,
in-flight ownership, cache contention, or application speedup.
"""

from __future__ import annotations

import argparse
import json
from collections import OrderedDict
from pathlib import Path
from typing import TextIO


def fields(line: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for token in line.split():
        if "=" in token:
            key, value = token.split("=", 1)
            result[key] = value
    return result


def replay(stream: TextIO, capacities: list[int]) -> dict[str, object]:
    if not capacities or any(value <= 0 for value in capacities):
        raise ValueError("capacities must be positive")
    capacities = sorted(set(capacities))
    caches: dict[int, dict[int, tuple[int, OrderedDict[int, None]]]] = {
        capacity: {} for capacity in capacities
    }
    hits = {capacity: 0 for capacity in capacities}
    requests = 0
    observed_hits_or_merges = 0
    observed_fills = 0

    for line in stream:
        if "event=soa_jit_value_request " not in line:
            continue
        event = fields(line)
        try:
            unit = int(event["unit"], 0)
            generation = int(event["generation"], 0)
            paddr = int(event["paddr"], 0)
        except (KeyError, ValueError) as error:
            raise ValueError("malformed SoA/JIT value request") from error
        if unit < 0 or generation <= 0:
            raise ValueError("invalid SoA/JIT owner identity")
        action = event.get("action")
        if action in ("hit", "merge"):
            observed_hits_or_merges += 1
        elif action == "fill":
            observed_fills += 1
        else:
            raise ValueError(f"invalid SoA/JIT request action: {action!r}")

        requests += 1
        for capacity in capacities:
            state = caches[capacity].get(unit)
            if state is None or state[0] != generation:
                owner_cache: OrderedDict[int, None] = OrderedDict()
                caches[capacity][unit] = (generation, owner_cache)
            else:
                owner_cache = state[1]
            if paddr in owner_cache:
                hits[capacity] += 1
                owner_cache.move_to_end(paddr)
            else:
                owner_cache[paddr] = None
                if len(owner_cache) > capacity:
                    owner_cache.popitem(last=False)

    if requests == 0:
        raise ValueError("trace contains no SoA/JIT value requests")
    return {
        "schema": "dx100.soa_value_owner_reuse.v1",
        "scope": "ideal_lru_line_reuse_not_timing_or_speedup",
        "requests": requests,
        "observed": {
            "hits_or_merges": observed_hits_or_merges,
            "fills": observed_fills,
        },
        "capacities": [
            {
                "lines": capacity,
                "payload_bytes": capacity * 64,
                "ideal_hits": hits[capacity],
                "ideal_misses": requests - hits[capacity],
                "ideal_hit_fraction": hits[capacity] / requests,
            }
            for capacity in capacities
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    parser.add_argument(
        "--capacities", default="32,64,128,256", help="comma-separated lines"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    capacities = [int(value, 0) for value in args.capacities.split(",")]
    with args.trace.open(encoding="utf-8", errors="replace") as stream:
        report = replay(stream, capacities)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
