#!/usr/bin/env python3
"""Validate and summarize replicated virtual-tile result matrices."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path


class SummaryError(RuntimeError):
    pass


def read_matrix(root: Path) -> dict[str, dict[str, str]]:
    matrix = root / "matrix.tsv"
    if not matrix.is_file():
        raise SummaryError(f"missing matrix: {matrix}")
    if not list(root.glob("*_matrix.pass")):
        raise SummaryError(f"missing matrix pass marker: {root}")
    with matrix.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if not rows or any(key not in rows[0] for key in
                       ("case", "output_hash", "simTicks")):
        raise SummaryError(f"invalid matrix schema: {matrix}")
    result = {row["case"]: row for row in rows}
    if len(result) != len(rows):
        raise SummaryError(f"duplicate cases in {matrix}")
    if len({row["output_hash"] for row in rows}) != 1:
        raise SummaryError(f"correctness hashes differ within {matrix}")
    return result


def gem5_hash(root: Path, cases: set[str]) -> str:
    hashes = set()
    for case in cases:
        artifact = root / case / "artifact_sha256.txt"
        if not artifact.is_file():
            raise SummaryError(f"missing artifact manifest: {artifact}")
        first = artifact.read_text(encoding="utf-8").splitlines()[0].split()
        if len(first) < 2:
            raise SummaryError(f"invalid artifact manifest: {artifact}")
        hashes.add(first[0])
    if len(hashes) != 1:
        raise SummaryError(f"gem5 hashes differ within {root}")
    return hashes.pop()


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("matrices", nargs="+", type=Path)
    args = parser.parse_args()

    roots = [path.resolve(strict=True) for path in args.matrices]
    matrices = [read_matrix(root) for root in roots]
    cases = set(matrices[0])
    if args.baseline not in cases:
        raise SystemExit(f"baseline {args.baseline!r} is not in the matrix")
    if any(set(matrix) != cases for matrix in matrices[1:]):
        raise SystemExit("replicate case sets differ")
    hashes = {gem5_hash(root, cases) for root in roots}
    if len(hashes) != 1:
        raise SystemExit("replicates used different gem5 binaries")

    replica_rows = []
    summary = {}
    for case in sorted(cases):
        ticks = [int(matrix[case]["simTicks"]) for matrix in matrices]
        changes = []
        for matrix in matrices:
            baseline = int(matrix[args.baseline]["simTicks"])
            changes.append((int(matrix[case]["simTicks"]) / baseline - 1) * 100)
        summary[case] = {
            "simTicks_median": int(statistics.median(ticks)),
            "simTicks_min": min(ticks),
            "simTicks_max": max(ticks),
            "latency_change_pct_median": statistics.median(changes),
            "latency_change_pct_min": min(changes),
            "latency_change_pct_max": max(changes),
        }
        for index, (ticks_value, change) in enumerate(zip(ticks, changes), 1):
            replica_rows.append((index, case, ticks_value, change))

    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "replicas.tsv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(("replicate", "case", "simTicks",
                         "latency_change_pct"))
        writer.writerows(replica_rows)
    payload = {
        "schema_version": 1,
        "baseline": args.baseline,
        "gem5_sha256": hashes.pop(),
        "replicates": len(matrices),
        "matrix_roots": [str(root) for root in roots],
        "cases": summary,
    }
    write(args.out / "summary.json",
          json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
