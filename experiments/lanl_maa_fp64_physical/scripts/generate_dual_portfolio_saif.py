#!/usr/bin/env python3
"""Generate conflict-free two-slot top-input SAIF sensitivity profiles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from generate_portfolio_saif import Activity, OPCODES, net_record


def resource_class(operation: str) -> str:
    if operation == "add_subtract":
        return "add"
    if operation == "multiply":
        return "multiply"
    return "divide"


def choose_operation(
    remaining: dict[str, int], initial: dict[str, int],
    excluded_resource: str | None = None,
) -> str | None:
    available = [
        name
        for name, count in remaining.items()
        if count and (excluded_resource is None or
                      resource_class(name) != excluded_resource or
                      resource_class(name) == "divide")
    ]
    if not available:
        return None
    return max(available, key=lambda name: (remaining[name] / initial[name], name))


def generate(contract: dict, profile_name: str) -> str:
    profile = contract["profiles"][profile_name]
    initial = {
        name: int(count) for name, count in profile["operation_counts"].items()
    }
    if set(initial) != set(OPCODES) or any(count < 0 for count in initial.values()):
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
        **{
            f"req{slot}{name}": Activity(width)
            for slot in range(2)
            for name, width in (
                ("Valid", 1), ("Op", 2), ("Tag", 6),
                ("A", 64), ("B", 64),
            )
        },
    }
    cycles = 0
    tag = 0
    operand = 0

    def sample_slot(slot: int, operation: str | None) -> None:
        nonlocal tag, operand
        if operation is None:
            for name in ("Valid", "Op", "Tag", "A", "B"):
                signals[f"req{slot}{name}"].sample(0)
            return
        a = pool[operand % len(pool)]
        b = pool[(operand * 5 + 3) % len(pool)]
        if operation == "divide" and b & 0x7FFFFFFFFFFFFFFF == 0:
            b = 0x3FF0000000000000
        signals[f"req{slot}Valid"].sample(1)
        signals[f"req{slot}Op"].sample(OPCODES[operation])
        signals[f"req{slot}Tag"].sample(tag)
        signals[f"req{slot}A"].sample(a)
        signals[f"req{slot}B"].sample(b)
        tag = (tag + 1) & 63
        operand += 1

    for _ in range(2):
        signals["nReset"].sample(0)
        sample_slot(0, None)
        sample_slot(1, None)
        cycles += 1
    remaining = dict(initial)
    while sum(remaining.values()):
        first = choose_operation(remaining, initial)
        if first is None:
            raise AssertionError("nonempty profile produced no first operation")
        remaining[first] -= 1
        second = choose_operation(
            remaining, initial, excluded_resource=resource_class(first))
        if second is not None:
            remaining[second] -= 1
        signals["nReset"].sample(1)
        sample_slot(0, first)
        sample_slot(1, second)
        cycles += 1
    for _ in range(drain):
        signals["nReset"].sample(1)
        sample_slot(0, None)
        sample_slot(1, None)
        cycles += 1

    if any(activity.samples != cycles for activity in signals.values()):
        raise AssertionError("activity duration mismatch")
    duration = cycles * period
    records = [
        f"        (clock (T0 {duration // 2}) "
        f"(T1 {duration - duration // 2}) (TX 0) "
        f"(TC {cycles * 2}) (IG 0))"
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
  (DESIGN "LanlFp64Portfolio2S1A1M8D")
  (DATE "2026-07-30")
  (VENDOR "DX100 LANL-MAA screening")
  (PROGRAM_NAME "generate_dual_portfolio_saif.py")
  (VERSION "1")
  (DIVIDER /)
  (TIMESCALE 1 ns)
  (DURATION {duration})
  (INSTANCE TOP
    (INSTANCE LanlFp64Portfolio2S1A1M8D
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
