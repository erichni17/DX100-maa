#!/usr/bin/env python3
"""Extract named metrics from the first gem5 statistics dump only.

This intentionally fails rather than silently reading a later periodic/final
dump.  Stall-budget reports use it to keep a repeated stats file from mixing
windows with different reset boundaries.
"""

import argparse
import json
import math
import re
import sys
from pathlib import Path

BEGIN = "---------- Begin Simulation Statistics ----------"
END = "---------- End Simulation Statistics ----------"
STAT = re.compile(r"^(\S+)\s+([^\s#]+)(?:\s|$)")


def first_window(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    begins = [index for index, line in enumerate(lines) if line == BEGIN]
    if not begins:
        raise ValueError("no gem5 statistics window")
    start = begins[0] + 1
    stop = len(lines)
    for index in range(start, len(lines)):
        if lines[index] == END or lines[index] == BEGIN:
            stop = index
            break
    if stop == start:
        raise ValueError("first gem5 statistics window is empty")
    return lines[start:stop]


def extract(path: Path, names: list[str]) -> dict[str, int | float]:
    if not names:
        raise ValueError("at least one --metric is required")
    wanted = set(names)
    found: dict[str, int | float] = {}
    for line in first_window(path):
        match = STAT.match(line)
        if not match or match.group(1) not in wanted:
            continue
        name, raw = match.groups()
        if name in found:
            raise ValueError(f"duplicate metric in first window: {name}")
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f"non-numeric metric {name}: {raw}") from exc
        if not math.isfinite(value):
            raise ValueError(f"non-finite metric {name}: {raw}")
        found[name] = int(value) if value.is_integer() else value
    missing = [name for name in names if name not in found]
    if missing:
        raise ValueError(
            "missing metric(s) in first window: " + ", ".join(missing)
        )
    return {name: found[name] for name in names}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stats", type=Path)
    parser.add_argument("--metric", action="append", default=[])
    args = parser.parse_args()
    try:
        values = extract(args.stats, args.metric)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(values, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
