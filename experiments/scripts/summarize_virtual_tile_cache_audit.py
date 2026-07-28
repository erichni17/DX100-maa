#!/usr/bin/env python3
"""Validate replicated virtual-tile cache audits and summarize the tradeoff."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

CASES = (
    "native_16k",
    "paged_staged_16k",
    "paged_16k",
    "paged_4k",
    "paged_reload_warm_4k",
    "paged_reload_cold_4k",
    "native_4k",
)
SIGNATURE_FIELDS = (
    "output_hash",
    "simTicks",
    "index_words",
    "index_hwm",
    "write_issues",
    "write_completions",
    "pages_ready",
    "pages_ready_before_source_drain",
    "first_page_ready_cycles",
    "all_pages_ready_cycles",
    "page_ready_span_cycles",
    "indirect_spd_reads",
    "stream_spd_reads",
    "stream_writes",
    "alu_compute_cycles",
    "l3_read_hits_maa",
    "l3_read_misses_maa",
    "memory_bytes_read_maa",
)


def read_rows(root: Path) -> tuple[dict[str, dict[str, str]], str, str]:
    result_files = sorted(root.glob("*/result.tsv"))
    if not result_files:
        raise SystemExit(f"no case result.tsv files under {root}")

    rows: dict[str, dict[str, str]] = {}
    gem5_hashes: set[str] = set()
    binary_hashes: set[str] = set()
    for result_file in result_files:
        with result_file.open(newline="", encoding="utf-8") as handle:
            parsed = list(csv.DictReader(handle, delimiter="\t"))
        if len(parsed) != 1:
            raise SystemExit(f"expected one row in {result_file}")
        row = parsed[0]
        case = row["case"]
        if case in rows:
            raise SystemExit(f"duplicate case {case} under {root}")
        rows[case] = row

        hashes = result_file.parent / "artifact_sha256.txt"
        lines = hashes.read_text(encoding="utf-8").splitlines()
        if len(lines) < 2:
            raise SystemExit(f"missing artifact hashes in {hashes}")
        gem5_hashes.add(lines[0].split()[0])
        binary_hashes.add(lines[1].split()[0])

    missing = set(CASES) - rows.keys()
    extra = rows.keys() - set(CASES)
    if missing or extra:
        raise SystemExit(
            f"case mismatch under {root}: missing={missing}, extra={extra}"
        )
    if len(gem5_hashes) != 1 or len(binary_hashes) != 1:
        raise SystemExit(f"mixed gem5/test binaries under {root}")
    return rows, gem5_hashes.pop(), binary_hashes.pop()


def pct(new: int, old: int) -> float:
    return (new / old - 1.0) * 100.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    replicas = []
    gem5_hashes: set[str] = set()
    binary_hashes: set[str] = set()
    for root in args.roots:
        rows, gem5_hash, binary_hash = read_rows(root)
        replicas.append(rows)
        gem5_hashes.add(gem5_hash)
        binary_hashes.add(binary_hash)
    if len(gem5_hashes) != 1 or len(binary_hashes) != 1:
        raise SystemExit("replicas use different gem5 or workload binaries")

    reference = replicas[0]
    for replica_index, rows in enumerate(replicas[1:], start=2):
        for case in CASES:
            for field in SIGNATURE_FIELDS:
                if rows[case][field] != reference[case][field]:
                    raise SystemExit(
                        f"replica {replica_index} differs for {case}.{field}: "
                        f"{rows[case][field]} != {reference[case][field]}"
                    )

    warm = reference["paged_reload_warm_4k"]
    cold = reference["paged_reload_cold_4k"]
    if (
        int(warm["l3_read_hits_maa"]) < 2000
        or int(warm["l3_read_misses_maa"]) > 16
    ):
        raise SystemExit("warm reload was not LLC-resident")
    if int(cold["l3_read_misses_maa"]) < 2000:
        raise SystemExit("cold reload was not displaced from the LLC")
    if int(cold["memory_bytes_read_maa"]) <= int(
        warm["memory_bytes_read_maa"]
    ):
        raise SystemExit("cold reload did not increase MAA DRAM bytes")

    ticks = {case: int(reference[case]["simTicks"]) for case in CASES}
    constructed_cold_ticks = (
        ticks["paged_4k"]
        - ticks["paged_reload_warm_4k"]
        + ticks["paged_reload_cold_4k"]
    )
    metrics = {
        "staged_warm_vs_native16_latency_pct": pct(
            ticks["paged_staged_16k"], ticks["native_16k"]
        ),
        "direct_warm16_vs_native16_latency_pct": pct(
            ticks["paged_16k"], ticks["native_16k"]
        ),
        "direct_warm4_vs_native16_latency_pct": pct(
            ticks["paged_4k"], ticks["native_16k"]
        ),
        "physical_4k_vs_16k_page_latency_pct": pct(
            ticks["paged_4k"], ticks["paged_16k"]
        ),
        "direct_feeder_vs_staged_latency_pct": pct(
            ticks["paged_16k"], ticks["paged_staged_16k"]
        ),
        "cold_vs_warm_reload_latency_pct": pct(
            ticks["paged_reload_cold_4k"], ticks["paged_reload_warm_4k"]
        ),
        "constructed_cold_4k_ticks": constructed_cold_ticks,
        "constructed_cold_4k_vs_native16_latency_pct": pct(
            constructed_cold_ticks, ticks["native_16k"]
        ),
        "warm_4k_vs_native4_latency_pct": pct(
            ticks["paged_4k"], ticks["native_4k"]
        ),
        "constructed_cold_4k_vs_native4_latency_pct": pct(
            constructed_cold_ticks, ticks["native_4k"]
        ),
    }

    args.output.mkdir(parents=True, exist_ok=False)
    payload = {
        "replicas": len(replicas),
        "gem5_sha256": next(iter(gem5_hashes)),
        "binary_sha256": next(iter(binary_hashes)),
        "ticks": ticks,
        "metrics": metrics,
        "cache_signatures": {
            "warm": {field: warm[field] for field in SIGNATURE_FIELDS[-3:]},
            "cold": {field: cold[field] for field in SIGNATURE_FIELDS[-3:]},
        },
        "roots": [str(root.resolve()) for root in args.roots],
    }
    (args.output / "summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    lines = [
        "# Virtual-Tile Cache-Residency Audit",
        "",
        f"All {len(replicas)} replicas were bit-for-bit identical for performance, "
        "correctness, mechanism, LLC, and DRAM signatures.",
        "",
        "| Full path | `simTicks` | Versus native 16K |",
        "|---|---:|---:|",
    ]
    for case in (
        "native_16k",
        "paged_staged_16k",
        "paged_16k",
        "paged_4k",
        "native_4k",
    ):
        relative = pct(ticks[case], ticks["native_16k"])
        lines.append(f"| {case} | {ticks[case]:,} | {relative:+.2f}% |")
    lines.extend(
        [
            "",
            "| Reload-only phase | `simTicks` | LLC hits | LLC misses |",
            "|---|---:|---:|---:|",
            f"| warm 4K pages | {ticks['paged_reload_warm_4k']:,} | "
            f"{warm['l3_read_hits_maa']} | {warm['l3_read_misses_maa']} |",
            f"| cold 4K pages | {ticks['paged_reload_cold_4k']:,} | "
            f"{cold['l3_read_hits_maa']} | {cold['l3_read_misses_maa']} |",
            "",
            "The warm 16K-on-4K result is an LLC-resident best case, not generic "
            "DRAM paging. After verified displacement, the same reload was "
            f"{metrics['cold_vs_warm_reload_latency_pct']:.2f}% slower.",
            "",
            "Replacing the warm reload phase in the full path with the replicated "
            f"cold phase gives {constructed_cold_ticks:,} constructed ticks, or "
            f"{metrics['constructed_cold_4k_vs_native16_latency_pct']:.2f}% slower "
            "than native 16K. This excludes the external LLC-pollution work itself; "
            "it measures the architectural consequence of nonresident backing.",
            "",
            "The staged-index warm path is the strict B-ingestion control. The direct "
            "index feeder is not hiding paging overhead in this matrix; it is "
            f"{metrics['direct_feeder_vs_staged_latency_pct']:.2f}% slower than the "
            "staged-index warm path at physical 16K.",
        ]
    )
    (args.output / "summary.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    (args.output / "virtual_tile_cache_audit_replicas.pass").touch()
    print(args.output / "summary.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
