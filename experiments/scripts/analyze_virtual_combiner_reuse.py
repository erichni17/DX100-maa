#!/usr/bin/env python3
"""Replay virtual-result insertions through fixed-capacity line policies."""

from __future__ import annotations

import argparse
import collections
import json
import re
from dataclasses import dataclass
from pathlib import Path

INSERT_RE = re.compile(
    r"event=virtual_combine_insert .*?unit=(?P<unit>\d+) "
    r"operation_tick=(?P<operation>\d+) itr=(?P<itr>\d+) "
    r"line=0x(?P<line>[0-9a-f]+) word=(?P<word>\d+)"
)
FULL_MASK = (1 << 16) - 1


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


@dataclass
class Slot:
    line: int
    mask: int
    last_use: int


@dataclass
class ReplayResult:
    writes: int = 0
    full_writes: int = 0
    eviction_writes: int = 0
    final_writes: int = 0
    semantic_words: int = 0
    written_words: int = 0

    def record(self, mask: int, reason: str) -> None:
        words = mask.bit_count()
        require(words > 0, "attempted empty write")
        self.writes += 1
        self.written_words += words
        if reason == "full":
            self.full_writes += 1
        elif reason == "eviction":
            self.eviction_writes += 1
        elif reason == "final":
            self.final_writes += 1
        else:
            raise RuntimeError(f"unknown write reason: {reason}")


def choose_victim(
    policy: str,
    slots: list[Slot | None],
    pointer: int,
    future: dict[int, collections.deque[int]],
) -> int:
    valid = [index for index, slot in enumerate(slots) if slot is not None]
    require(valid, "no valid victim")
    order = sorted(valid, key=lambda index: (index - pointer) % len(slots))
    if policy == "round_robin":
        return order[0]
    if policy == "fewest_words":
        return min(order, key=lambda index: slots[index].mask.bit_count())  # type: ignore[union-attr]
    if policy == "most_words":
        return max(order, key=lambda index: slots[index].mask.bit_count())  # type: ignore[union-attr]
    if policy == "lru":
        return min(order, key=lambda index: slots[index].last_use)  # type: ignore[union-attr]
    if policy == "belady":

        def next_use(index: int) -> int:
            line = slots[index].line  # type: ignore[union-attr]
            positions = future[line]
            return positions[0] if positions else 1 << 62

        return max(order, key=next_use)
    raise RuntimeError(f"unknown policy: {policy}")


def replay_operation(
    events: list[tuple[int, int]], policy: str, capacity: int
) -> ReplayResult:
    require(capacity > 0, "capacity must be positive")
    future: dict[int, collections.deque[int]] = collections.defaultdict(
        collections.deque
    )
    for position, (line, _) in enumerate(events):
        future[line].append(position)

    slots: list[Slot | None] = [None] * capacity
    by_line: dict[int, int] = {}
    pointer = 0
    seen_words: set[tuple[int, int]] = set()
    result = ReplayResult(semantic_words=len(events))

    for position, (line, word) in enumerate(events):
        require(0 <= word < 16, f"invalid word {word}")
        require(
            (line, word) not in seen_words, "duplicate logical output word"
        )
        seen_words.add((line, word))
        require(future[line].popleft() == position, "future queue diverged")

        index = by_line.get(line)
        if index is None:
            try:
                index = slots.index(None)
            except ValueError:
                index = choose_victim(policy, slots, pointer, future)
                victim = slots[index]
                require(victim is not None, "selected empty victim")
                result.record(victim.mask, "eviction")
                del by_line[victim.line]
                pointer = (index + 1) % capacity
            slots[index] = Slot(line=line, mask=0, last_use=position)
            by_line[line] = index

        slot = slots[index]
        require(slot is not None and slot.line == line, "line map diverged")
        bit = 1 << word
        require(not slot.mask & bit, "duplicate word in resident line")
        slot.mask |= bit
        slot.last_use = position
        if slot.mask == FULL_MASK:
            result.record(slot.mask, "full")
            slots[index] = None
            del by_line[line]

    for slot in slots:
        if slot is not None:
            result.record(slot.mask, "final")
    require(
        result.written_words == result.semantic_words, "word closure failed"
    )
    return result


def parse_operations(
    path: Path,
) -> dict[tuple[int, int], list[tuple[int, int]]]:
    operations: dict[tuple[int, int], list[tuple[int, int]]] = {}
    with path.open(errors="replace") as stream:
        for text in stream:
            if "event=virtual_combine_insert " not in text:
                continue
            match = INSERT_RE.search(text)
            require(
                match is not None,
                f"malformed insertion event: {text.rstrip()}",
            )
            key = (int(match["unit"]), int(match["operation"]))
            operations.setdefault(key, []).append(
                (int(match["line"], 16), int(match["word"]))
            )
    require(operations, "trace contains no insertion events")
    return operations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--capacity", type=int, default=16)
    parser.add_argument("--expected-operations", type=int)
    parser.add_argument("--expected-words", type=int, default=16_384)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    operations = parse_operations(args.trace)
    if args.expected_operations is not None:
        require(
            len(operations) == args.expected_operations,
            f"operations {len(operations)} != {args.expected_operations}",
        )
    for key, events in operations.items():
        require(
            len(events) == args.expected_words,
            f"operation {key} has {len(events)} words",
        )

    policies = ("round_robin", "fewest_words", "most_words", "lru", "belady")
    report: dict[str, object] = {
        "schema": "dx100.virtual_combiner_reuse.v1",
        "trace": str(args.trace.resolve()),
        "capacity_lines": args.capacity,
        "operations": len(operations),
        "semantic_words": sum(len(events) for events in operations.values()),
        "policies": {},
    }
    policy_report: dict[str, object] = {}
    for policy in policies:
        aggregate = ReplayResult()
        for events in operations.values():
            result = replay_operation(events, policy, args.capacity)
            for field in ReplayResult.__dataclass_fields__:
                setattr(
                    aggregate,
                    field,
                    getattr(aggregate, field) + getattr(result, field),
                )
        policy_report[policy] = aggregate.__dict__
    report["policies"] = policy_report
    report["infinite_capacity_writes"] = sum(
        len({line for line, _ in events}) for events in operations.values()
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded)
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
