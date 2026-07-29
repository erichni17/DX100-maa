#!/usr/bin/env python3
"""Extract per-channel and total XRAGE DRAM commands from raw logs."""

import argparse
import re
from pathlib import Path

STAT_RE = re.compile(
    r"^\s*CH(?P<channel>[0-9]+)_num_(?P<command>RD|ACT|PRE)_commands_T:"
    r"\s+(?P<value>[0-9]+)(?:\s|$)"
)
COMMANDS = ("RD", "ACT", "PRE")


def parse_log(log: Path, expected_channels: int) -> dict[int, dict[str, int]]:
    samples: dict[tuple[int, str], list[int]] = {}
    with log.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            match = STAT_RE.match(line)
            if match is None:
                continue
            key = (int(match.group("channel")), match.group("command"))
            samples.setdefault(key, []).append(int(match.group("value")))

    channels = set(range(expected_channels))
    observed = {channel for channel, _ in samples}
    if observed != channels:
        raise ValueError(
            f"{log}: expected channels {sorted(channels)}, "
            f"observed {sorted(observed)}"
        )

    result: dict[int, dict[str, int]] = {}
    for channel in sorted(channels):
        result[channel] = {}
        for command in COMMANDS:
            values = samples.get((channel, command), [])
            if not values:
                raise ValueError(f"{log}: missing CH{channel} {command} total")
            result[channel][command] = values[-1]
    return result


def write_summary(run: Path, values: dict[int, dict[str, int]]) -> None:
    output = run / "dram_commands_all.tsv"
    rows = ["channel\tdram_reads\tdram_activates\tdram_precharges"]
    totals = {command: 0 for command in COMMANDS}
    for channel, commands in sorted(values.items()):
        rows.append(
            f"CH{channel}\t{commands['RD']}\t{commands['ACT']}\t"
            f"{commands['PRE']}"
        )
        for command in COMMANDS:
            totals[command] += commands[command]
    rows.append(f"total\t{totals['RD']}\t{totals['ACT']}\t{totals['PRE']}")
    output.write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--expected-channels", type=int, default=2)
    args = parser.parse_args()
    if args.expected_channels <= 0:
        parser.error("--expected-channels must be positive")

    for run in args.runs:
        log = run / "restore.log"
        if not log.is_file():
            raise SystemExit(f"missing XRAGE restore log: {log}")
        try:
            values = parse_log(log, args.expected_channels)
        except ValueError as error:
            raise SystemExit(str(error)) from error
        write_summary(run, values)
        totals = {
            command: sum(channel[command] for channel in values.values())
            for command in COMMANDS
        }
        print(
            f"{run}: RD={totals['RD']} ACT={totals['ACT']} "
            f"PRE={totals['PRE']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
