#!/usr/bin/env python3
"""Fail closed on a 14-case FLAG complete-line campaign."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


class AuditError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def read_tsv(path: Path) -> list[dict[str, str]]:
    require(path.is_file(), f"missing {path}")
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def read_cases(path: Path) -> list[dict[str, str]]:
    require(path.is_file(), f"missing {path}")
    with path.open(newline="", encoding="utf-8") as stream:
        records = list(csv.reader(stream, delimiter="\t"))
    require(all(len(record) == 3 for record in records),
            f"invalid case list: {path}")
    return [
        {"id": record[0], "input": record[1], "length": record[2]}
        for record in records
    ]


def read_manifest(path: Path) -> dict[str, str]:
    require(path.is_file(), f"missing {path}")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        require(separator == "=" and key not in values, f"invalid {path}: {line}")
        values[key] = value
    return values


def integer(row: dict[str, str], field: str, owner: str) -> int:
    try:
        value = int(row[field])
    except (KeyError, ValueError) as error:
        raise AuditError(f"invalid {field} for {owner}") from error
    require(value >= 0, f"negative {field} for {owner}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--binary-sha256", required=True)
    args = parser.parse_args()

    require(re.fullmatch(r"[0-9a-f]{40}", args.source_commit) is not None,
            "invalid source commit")
    require(re.fullmatch(r"[0-9a-f]{64}", args.binary_sha256) is not None,
            "invalid binary hash")

    cases = read_cases(args.campaign / "cases.list")
    results = read_tsv(args.campaign / "results.tsv")
    require(len(cases) == len(results) == 14, "expected 14 FLAG gathers")
    by_id = {row["id"]: row for row in results}
    require(len(by_id) == 14, "duplicate top-level result ID")

    expected_manifest = {
        "source_commit": args.source_commit,
        "simulator_source_commit": args.source_commit,
        "arm": "direct_index_4k",
        "guest_arm": "direct4",
        "result_scale": "1",
        "direct_retirement_line_handoff": "0",
        "virtual_complete_line_only": "1",
        "physical_tile_elements": "4096",
        "maa_logical_tile_elements": "16384",
        "virtual_combine_slots": "2048",
        "virtual_combine_words": "3072",
        "virtual_combine_ways": "16",
        "virtual_response_word_pool": "1024",
        "num_indirect_units_per_maa": "1",
        "timeout": "none",
    }
    audited: list[dict[str, object]] = []
    for case in cases:
        case_id = case["id"]
        require(case_id in by_id, f"missing top-level result for {case_id}")
        root_row = by_id[case_id]
        case_root = args.campaign / "cases" / case_id
        require(case_root.is_dir(), f"missing case directory for {case_id}")
        require(integer(case, "length", case_id) ==
                integer(root_row, "length", case_id),
                f"length mismatch for {case_id}")
        manifest = read_manifest(case_root / "manifest.txt")
        require(
            Path(case["input"]).resolve() == Path(manifest["input"]).resolve(),
            f"input mismatch for {case_id}",
        )
        for key, expected in expected_manifest.items():
            require(manifest.get(key) == expected,
                    f"{case_id}: {key}={manifest.get(key)!r}, expected {expected!r}")

        require((case_root / "checkpoint.exit").read_text().strip() == "0",
                f"checkpoint failed for {case_id}")
        require((case_root / "restore.exit").read_text().strip() == "0",
                f"restore failed for {case_id}")
        require((case_root / "source_status.txt").read_text() == "",
                f"dirty source recorded for {case_id}")
        require((case_root / "xrage_attribution_smoke.pass").is_file(),
                f"missing verifier pass for {case_id}")
        log = (case_root / "restore.log").read_text()
        require("m5_exit instruction encountered" in log,
                f"missing terminal m5_exit for {case_id}")

        ledger = (case_root / "artifact_sha256.txt").read_text().splitlines()
        require(ledger and ledger[0].split(maxsplit=1)[0] == args.binary_sha256,
                f"binary hash mismatch for {case_id}")
        case_rows = read_tsv(case_root / "result.tsv")
        require(len(case_rows) == 1, f"expected one result row for {case_id}")
        row = case_rows[0]
        ticks = integer(row, "roi_simTicks", case_id)
        final_ticks = integer(row, "final_simTicks", case_id)
        require(ticks == integer(root_row, "ticks", case_id),
                f"ROI tick mismatch for {case_id}")
        require(row["output_hash"] == root_row["hash"],
                f"output hash mismatch for {case_id}")
        require(integer(row, "virtual_write_issues", case_id) ==
                integer(row, "virtual_write_completions", case_id),
                f"write ACK closure failed for {case_id}")

        stats_ticks = [
            int(match.group(1))
            for match in re.finditer(r"^simTicks\s+(\d+)\s", (case_root / "run/stats.txt").read_text(), re.MULTILINE)
        ]
        require(stats_ticks and stats_ticks[0] == ticks and
                stats_ticks[-1] == final_ticks,
                f"stats tick mismatch for {case_id}")
        length = integer(root_row, "length", case_id)
        writes = integer(root_row, "writes", case_id)
        full = integer(root_row, "full", case_id)
        partial = integer(root_row, "partial", case_id)
        require(writes == full + partial,
                f"write-count closure failed for {case_id}")
        require(full == length // 8 and partial == (1 if length % 8 else 0),
                f"complete-line/tail closure failed for {case_id}")
        audited.append({"id": case_id, "ticks": ticks, "hash": row["output_hash"]})

    report = {
        "schema": "dx100.flag.complete_line_campaign_audit.v1",
        "terminal": True,
        "source_commit": args.source_commit,
        "binary_sha256": args.binary_sha256,
        "configurations": len(audited),
        "cases": audited,
    }
    require(not args.output.exists(), f"output already exists: {args.output}")
    args.output.mkdir(parents=True)
    (args.output / "audit.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    (args.output / "audit.pass").write_text("PASS_FLAG_CAMPAIGN_AUDIT\n")
    print(json.dumps({"terminal": True, "configurations": len(audited)}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AuditError, FileNotFoundError) as error:
        print(f"ERROR: {error}")
        raise SystemExit(1)
