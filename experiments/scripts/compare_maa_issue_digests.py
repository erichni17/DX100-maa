#!/usr/bin/env python3
"""Compare compact per-instruction MAA source-request digests."""

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

DIGEST_RE = re.compile(
    r"unit=(?P<unit>\d+) instruction_tick=(?P<tick>\d+) "
    r"count=(?P<count>\d+) fnv=0x(?P<fnv>[0-9a-fA-F]{16}) "
    r"mix=0x(?P<mix>[0-9a-fA-F]{16})"
)


def fail(message: str) -> None:
    raise SystemExit(f"MAA issue-digest comparison failed: {message}")


def parse_arm(value: str) -> tuple[str, Path]:
    if "=" not in value:
        fail(f"arm must use LABEL=LOG syntax: {value}")
    label, raw_path = value.split("=", 1)
    if not label or not re.fullmatch(r"[A-Za-z0-9_.-]+", label):
        fail(f"invalid arm label: {label}")
    path = Path(raw_path).resolve()
    if not path.is_file():
        fail(f"issue-digest log does not exist: {path}")
    return label, path


def read_digests(path: Path) -> dict[int, list[dict[str, int]]]:
    records: dict[int, list[dict[str, int]]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = DIGEST_RE.search(line)
        if match is None:
            continue
        unit = int(match.group("unit"))
        records[unit].append(
            {
                "tick": int(match.group("tick")),
                "count": int(match.group("count")),
                "fnv": int(match.group("fnv"), 16),
                "mix": int(match.group("mix"), 16),
            }
        )
    if not records:
        fail(f"no MAAIssueDigest records found in {path}")
    return dict(records)


def comparable(record: dict[str, int]) -> tuple[int, int, int]:
    return record["count"], record["fnv"], record["mix"]


def logical_sequence(
    units: dict[int, list[dict[str, int]]],
) -> list[dict[str, int]]:
    records = [
        {**record, "unit": unit}
        for unit, unit_records in units.items()
        for record in unit_records
    ]
    return sorted(records, key=lambda record: (record["tick"], record["unit"]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--allow-per-instruction-unit-reassignment", action="store_true"
    )
    parser.add_argument("arms", nargs="+", metavar="LABEL=LOG")
    args = parser.parse_args()

    arms: dict[str, dict[str, object]] = {}
    for raw_arm in args.arms:
        label, path = parse_arm(raw_arm)
        if label in arms:
            fail(f"duplicate arm label: {label}")
        arms[label] = {"path": str(path), "units": read_digests(path)}
    if args.baseline not in arms:
        fail(f"baseline arm is missing: {args.baseline}")

    baseline_units = arms[args.baseline]["units"]
    assert isinstance(baseline_units, dict)
    comparisons = []
    all_match = True
    for label, arm in arms.items():
        if label == args.baseline:
            continue
        units = arm["units"]
        assert isinstance(units, dict)
        unit_set_match = set(units) == set(baseline_units)
        baseline_logical = logical_sequence(baseline_units)
        candidate_logical = logical_sequence(units)
        logical_sequence_match = [comparable(record) for record in baseline_logical] == [
            comparable(record) for record in candidate_logical
        ]
        logical_requests = (
            sum(record["count"] for record in baseline_logical)
            if logical_sequence_match
            else 0
        )
        first_mismatch = None
        compared_instructions = 0
        total_requests = 0
        if unit_set_match:
            for unit in sorted(baseline_units):
                baseline_records = baseline_units[unit]
                candidate_records = units[unit]
                shared = min(len(baseline_records), len(candidate_records))
                compared_instructions += shared
                for ordinal in range(shared):
                    baseline_record = baseline_records[ordinal]
                    candidate_record = candidate_records[ordinal]
                    if comparable(baseline_record) != comparable(
                        candidate_record
                    ):
                        first_mismatch = {
                            "unit": unit,
                            "instruction_ordinal": ordinal,
                            "baseline": baseline_record,
                            "candidate": candidate_record,
                        }
                        break
                    total_requests += baseline_record["count"]
                if first_mismatch is not None:
                    break
                if len(baseline_records) != len(candidate_records):
                    first_mismatch = {
                        "unit": unit,
                        "instruction_ordinal": shared,
                        "baseline_instruction_count": len(baseline_records),
                        "candidate_instruction_count": len(candidate_records),
                    }
                    break
        match = unit_set_match and first_mismatch is None
        accepted = match or (
            args.allow_per_instruction_unit_reassignment
            and logical_sequence_match
        )
        all_match &= accepted
        comparisons.append(
            {
                "baseline": args.baseline,
                "candidate": label,
                "match": match,
                "accepted": accepted,
                "unit_set_match": unit_set_match,
                "logical_sequence_match": logical_sequence_match,
                "logical_instructions": len(baseline_logical),
                "logical_source_requests": logical_requests,
                "compared_instructions": compared_instructions,
                "matched_source_requests": total_requests,
                "first_mismatch": first_mismatch,
            }
        )

    output = args.output_dir.resolve()
    if output.exists():
        fail(f"refusing to overwrite output: {output}")
    output.mkdir(parents=True)
    report = {"baseline": args.baseline, "arms": arms, "comparisons": comparisons}
    (output / "maa_issue_digest_comparison.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# MAA Source-Request Digest Comparison",
        "",
        "Per-instruction matching ignores functional-unit assignment and does "
        "not prove the global interleaving of requests from concurrent units.",
        "",
        "| Baseline | Candidate | Strict match | Per-instruction match | "
        "Instructions | Source requests |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for comparison in comparisons:
        lines.append(
            f"| {comparison['baseline']} | {comparison['candidate']} | "
            f"{'yes' if comparison['match'] else 'no'} | "
            f"{'yes' if comparison['logical_sequence_match'] else 'no'} | "
            f"{comparison['logical_instructions']:,} | "
            f"{comparison['logical_source_requests']:,} |"
        )
    (output / "maa_issue_digest_comparison.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    if not all_match:
        fail("one or more source-request digest streams differ")
    if all(comparison["match"] for comparison in comparisons):
        (output / "maa_issue_digest_comparison.pass").touch()
    else:
        (output / "maa_issue_digest_per_instruction.pass").touch()
    print(f"PASS MAA source-request digest comparison: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
