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
    valid: list[int],
    pointer: int,
    future: dict[int, collections.deque[int]],
    plru_bits: int,
    set_begin: int,
    ways: int,
    position: int,
) -> int:
    require(valid, "no valid victim")
    order = sorted(valid, key=lambda index: (index - pointer) % ways)
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
    if policy.startswith("lookahead_"):
        horizon = int(policy.removeprefix("lookahead_"))
        require(horizon > 0, "lookahead must be positive")

        def bounded_score(index: int) -> tuple[int, int]:
            slot = slots[index]
            line = slot.line  # type: ignore[union-attr]
            positions = future[line]
            distance = positions[0] - position if positions else horizon + 1
            # Beyond the visible horizon all lines are indistinguishable;
            # prefer the fullest victim to maximize words per transaction.
            return min(distance, horizon + 1), slot.mask.bit_count()  # type: ignore[union-attr]

        return max(order, key=bounded_score)
    if policy == "tree_plru":
        return set_begin + plru_victim(plru_bits, ways)
    raise RuntimeError(f"unknown policy: {policy}")


def plru_victim(bits: int, ways: int) -> int:
    require(ways > 1 and ways & (ways - 1) == 0, "PLRU ways must be power of 2")
    node = 0
    way = 0
    span = ways
    while span > 1:
        direction = (bits >> node) & 1
        span //= 2
        if direction:
            way += span
        node = 2 * node + 1 + direction
    return way


def plru_touch(bits: int, way: int, ways: int) -> int:
    require(0 <= way < ways, "PLRU way is out of range")
    node = 0
    base = 0
    span = ways
    while span > 1:
        span //= 2
        direction = int(way >= base + span)
        victim_direction = 1 - direction
        if victim_direction:
            bits |= 1 << node
        else:
            bits &= ~(1 << node)
        if direction:
            base += span
        node = 2 * node + 1 + direction
    return bits


def replay_operation(
    events: list[tuple[int, int]],
    policy: str,
    capacity: int,
    ways: int = 4,
    set_xor_shift: int = 0,
    words_per_line: int = 16,
) -> ReplayResult:
    require(capacity > 0, "capacity must be positive")
    require(words_per_line > 0, "words per line must be positive")
    full_mask = (1 << words_per_line) - 1
    if ways == 0:
        ways = capacity
    require(
        ways > 0 and capacity % ways == 0,
        "ways must divide capacity",
    )
    if policy == "tree_plru":
        require(ways > 1 and ways & (ways - 1) == 0, "invalid PLRU geometry")
    num_sets = capacity // ways
    future: dict[int, collections.deque[int]] = collections.defaultdict(
        collections.deque
    )
    for position, (line, _) in enumerate(events):
        future[line].append(position)

    slots: list[Slot | None] = [None] * capacity
    by_line: dict[int, int] = {}
    pointers = [0] * num_sets
    plru = [0] * num_sets
    seen_words: set[tuple[int, int]] = set()
    result = ReplayResult(semantic_words=len(events))

    for position, (line, word) in enumerate(events):
        require(0 <= word < words_per_line, f"invalid word {word}")
        require(
            (line, word) not in seen_words, "duplicate logical output word"
        )
        seen_words.add((line, word))
        require(future[line].popleft() == position, "future queue diverged")

        index = by_line.get(line)
        line_number = line // 64
        if set_xor_shift:
            require(set_xor_shift > 0, "set XOR shift must be nonnegative")
            line_number ^= line_number >> set_xor_shift
        set_id = line_number % num_sets
        set_begin = set_id * ways
        set_end = set_begin + ways
        if index is None:
            index = next(
                (slot for slot in range(set_begin, set_end) if slots[slot] is None),
                None,
            )
            if index is None:
                valid = list(range(set_begin, set_end))
                index = choose_victim(
                    policy,
                    slots,
                    valid,
                    set_begin + pointers[set_id],
                    future,
                    plru[set_id],
                    set_begin,
                    ways,
                    position,
                )
                victim = slots[index]
                require(victim is not None, "selected empty victim")
                result.record(victim.mask, "eviction")
                del by_line[victim.line]
                pointers[set_id] = (index - set_begin + 1) % ways
            slots[index] = Slot(line=line, mask=0, last_use=position)
            by_line[line] = index

        slot = slots[index]
        require(slot is not None and slot.line == line, "line map diverged")
        bit = 1 << word
        require(not slot.mask & bit, "duplicate word in resident line")
        slot.mask |= bit
        slot.last_use = position
        if policy == "tree_plru":
            plru[set_id] = plru_touch(plru[set_id], index - set_begin, ways)
        if slot.mask == full_mask:
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
    parser.add_argument("--ways", type=int, default=4)
    parser.add_argument("--words-per-line", type=int, default=16)
    parser.add_argument("--set-xor-shift", type=int, default=0)
    parser.add_argument("--expected-operations", type=int)
    parser.add_argument("--expected-words", type=int, default=16_384)
    parser.add_argument("--expected-round-robin-writes", type=int)
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

    policies = (
        "round_robin",
        "fewest_words",
        "most_words",
        "lru",
        "tree_plru",
        "lookahead_32",
        "lookahead_128",
        "lookahead_512",
        "lookahead_2048",
        "belady",
    )
    report: dict[str, object] = {
        "schema": "dx100.virtual_combiner_reuse.v1",
        "trace": str(args.trace.resolve()),
        "capacity_lines": args.capacity,
        "ways": args.ways,
        "words_per_line": args.words_per_line,
        "set_xor_shift": args.set_xor_shift,
        "operations": len(operations),
        "semantic_words": sum(len(events) for events in operations.values()),
        "policies": {},
    }
    policy_report: dict[str, object] = {}
    for policy in policies:
        aggregate = ReplayResult()
        for events in operations.values():
            result = replay_operation(
                events,
                policy,
                args.capacity,
                args.ways,
                args.set_xor_shift,
                args.words_per_line,
            )
            for field in ReplayResult.__dataclass_fields__:
                setattr(
                    aggregate,
                    field,
                    getattr(aggregate, field) + getattr(result, field),
                )
        policy_report[policy] = aggregate.__dict__
    if args.expected_round_robin_writes is not None:
        observed = policy_report["round_robin"]["writes"]
        require(
            observed == args.expected_round_robin_writes,
            f"round-robin replay {observed} != {args.expected_round_robin_writes}",
        )
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
