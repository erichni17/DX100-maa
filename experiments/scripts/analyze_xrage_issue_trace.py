#!/usr/bin/env python3
"""Compare per-instruction A-request order from MAAIssueTrace logs."""

import argparse
import collections
import csv
import hashlib
import re
from pathlib import Path

TRACE_RE = re.compile(
    r"unit=(\d+) instruction_tick=(\d+) sequence=(\d+) "
    r"addr=(0x[0-9a-f]+) bounded=(\d) virtual=(\d) direct_index=(\d)"
)


def fail(message: str) -> None:
    raise SystemExit(f"XRAGE issue-trace validation failed: {message}")


def parse_arg(value: str) -> tuple[str, Path]:
    label, separator, path = value.partition("=")
    if not separator or not re.fullmatch(r"[A-Za-z0-9_.-]+", label):
        raise argparse.ArgumentTypeError("trace must have the form LABEL=PATH")
    return label, Path(path).resolve()


def sequence_hash(values: list[int]) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(value.to_bytes(8, byteorder="little"))
    return digest.hexdigest()


def read_trace(path: Path) -> list[list[int]]:
    if not path.is_file():
        fail(f"missing trace: {path}")
    groups: dict[
        tuple[int, int], list[tuple[int, int]]
    ] = collections.defaultdict(list)
    first_seen: dict[tuple[int, int], int] = {}
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
    ):
        match = TRACE_RE.search(line)
        if not match:
            continue
        unit, tick, sequence, address, _, _, _ = match.groups()
        key = (int(unit), int(tick))
        first_seen.setdefault(key, line_number)
        groups[key].append((int(sequence), int(address, 16)))
    if not groups:
        fail(f"trace has no MAAIssueTrace records: {path}")

    result: list[list[int]] = []
    for key in sorted(groups, key=first_seen.__getitem__):
        records = groups[key]
        sequences = [sequence for sequence, _ in records]
        if sequences != list(range(len(sequences))):
            fail(f"non-contiguous request sequence for {key} in {path}")
        result.append([address for _, address in records])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("traces", nargs="+", type=parse_arg)
    args = parser.parse_args()

    labels = [label for label, _ in args.traces]
    if len(labels) != len(set(labels)) or args.baseline not in labels:
        parser.error("labels must be unique and include --baseline")
    traces = {label: read_trace(path) for label, path in args.traces}
    baseline = traces[args.baseline]

    rows = []
    for label in labels:
        groups = traces[label]
        flattened = [address for group in groups for address in group]
        comparable = len(groups) == len(baseline) and all(
            len(actual) == len(reference)
            for actual, reference in zip(groups, baseline)
        )
        same_positions = 0
        total_positions = 0
        common_prefix = 0
        if comparable:
            for actual, reference in zip(groups, baseline):
                total_positions += len(reference)
                same_positions += sum(
                    left == right for left, right in zip(actual, reference)
                )
                for left, right in zip(actual, reference):
                    if left != right:
                        break
                    common_prefix += 1
        rows.append(
            {
                "label": label,
                "instructions": len(groups),
                "requests": len(flattened),
                "unique_addresses": len(set(flattened)),
                "ordered_sha256": sequence_hash(flattened),
                "multiset_sha256": sequence_hash(sorted(flattened)),
                "comparable_to_baseline": int(comparable),
                "same_position_fraction": (
                    f"{same_positions / total_positions:.9f}"
                    if total_positions
                    else ""
                ),
                "common_prefix_requests": common_prefix if comparable else "",
            }
        )

    baseline_row = next(row for row in rows if row["label"] == args.baseline)
    for row in rows:
        row["same_multiset_as_baseline"] = int(
            row["multiset_sha256"] == baseline_row["multiset_sha256"]
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(rows[0]), delimiter="\t"
        )
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
