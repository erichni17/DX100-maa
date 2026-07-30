#!/usr/bin/env python3
"""Generate deterministic top-input SAIF from a normalized operation profile."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

OPCODES = {"add_subtract": 0, "multiply": 2, "divide": 3}


@dataclass
class Activity:
    width: int

    def __post_init__(self) -> None:
        self.ones = [0] * self.width
        self.toggles = [0] * self.width
        self.previous = 0
        self.samples = 0

    def sample(self, value: int) -> None:
        if value < 0 or value >= (1 << self.width):
            raise ValueError("signal value exceeds width")
        changed = self.previous ^ value
        for bit in range(self.width):
            self.ones[bit] += (value >> bit) & 1
            self.toggles[bit] += (changed >> bit) & 1
        self.previous = value
        self.samples += 1


def choose_operation(
    remaining: dict[str, int], initial: dict[str, int]
) -> str:
    available = [name for name, count in remaining.items() if count]
    if not available:
        raise ValueError("no operation remains")
    return max(
        available, key=lambda name: (remaining[name] / initial[name], name)
    )


def net_record(
    name: str, bit: int | None, activity: Activity, duration: int, period: int
) -> str:
    index = 0 if bit is None else bit
    ones = activity.ones[index] * period
    zeros = duration - ones
    identifier = name if bit is None else f"{name}[{bit}]"
    return (
        f"        ({identifier} (T0 {zeros}) (T1 {ones}) (TX 0) "
        f"(TC {activity.toggles[index]}) (IG 0))"
    )


def generate(contract: dict, profile_name: str) -> str:
    profile = contract["profiles"][profile_name]
    initial = {
        name: int(count) for name, count in profile["operation_counts"].items()
    }
    if set(initial) != set(OPCODES) or any(
        count < 0 for count in initial.values()
    ):
        raise ValueError("invalid operation counts")
    if sum(initial.values()) == 0:
        raise ValueError("empty activity profile")
    period = int(contract["clock_period_ns"])
    drain = int(contract["idle_drain_cycles"])
    pool = [int(value, 16) for value in contract["operand_pool"]]
    if period <= 0 or drain < 1 or not pool:
        raise ValueError("invalid clock, drain, or operand pool")

    signals = {
        "nReset": Activity(1),
        "reqValid": Activity(1),
        "reqOp": Activity(2),
        "reqTag": Activity(6),
        "reqA": Activity(64),
        "reqB": Activity(64),
    }
    remaining = dict(initial)
    cycles = 0
    tag = 0
    operand = 0
    for _ in range(2):
        for activity in signals.values():
            activity.sample(0)
        cycles += 1
    while sum(remaining.values()):
        operation = choose_operation(remaining, initial)
        opcode = OPCODES[operation]
        a = pool[operand % len(pool)]
        b = pool[(operand * 5 + 3) % len(pool)]
        if operation == "divide" and b & 0x7FFFFFFFFFFFFFFF == 0:
            b = 0x3FF0000000000000
        signals["nReset"].sample(1)
        signals["reqValid"].sample(1)
        signals["reqOp"].sample(opcode)
        signals["reqTag"].sample(tag)
        signals["reqA"].sample(a)
        signals["reqB"].sample(b)
        remaining[operation] -= 1
        operand += 1
        tag = (tag + 1) & 63
        cycles += 1
    for _ in range(drain):
        signals["nReset"].sample(1)
        signals["reqValid"].sample(0)
        signals["reqOp"].sample(0)
        signals["reqTag"].sample(0)
        signals["reqA"].sample(0)
        signals["reqB"].sample(0)
        cycles += 1

    if any(activity.samples != cycles for activity in signals.values()):
        raise AssertionError("activity duration mismatch")
    duration = cycles * period
    records = [
        f"        (clock (T0 {duration // 2}) (T1 {duration - duration // 2}) "
        f"(TX 0) (TC {cycles * 2}) (IG 0))"
    ]
    for name, activity in signals.items():
        if activity.width == 1:
            records.append(net_record(name, None, activity, duration, period))
        else:
            records.extend(
                net_record(name, bit, activity, duration, period)
                for bit in range(activity.width)
            )
    body = "\n".join(records)
    return f"""(SAIFILE
  (SAIFVERSION "2.0")
  (DIRECTION "backward")
  (DESIGN "LanlFp64Portfolio1A1M8D")
  (DATE "2026-07-29")
  (VENDOR "DX100 LANL-MAA screening")
  (PROGRAM_NAME "generate_portfolio_saif.py")
  (VERSION "1")
  (DIVIDER /)
  (TIMESCALE 1 ns)
  (DURATION {duration})
  (INSTANCE TOP
    (INSTANCE LanlFp64Portfolio1A1M8D
      (NET
{body}
      )
    )
  )
)
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if args.profile not in contract.get("profiles", {}):
        raise ValueError("unknown activity profile")
    args.output.write_text(generate(contract, args.profile), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
