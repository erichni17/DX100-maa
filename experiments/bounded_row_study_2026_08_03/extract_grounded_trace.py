#!/usr/bin/env python3
"""Fail-closed extractor for a future gem5 physical bounded-row trace.

The frozen 2026-08-02 MAAVirtualTrace logs do not contain these records and
must fail this extractor.  Producing them requires a new run by the production
source owner; this script does not synthesize or infer paddr placement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from bounded_row_model import (
    ACTIVE_ELEMENTS,
    LINES_PER_ROW,
    NUM_SLICES,
    ROWS_PER_SLICE,
    ApertureGeometry,
    Model,
    PhysicalRecord,
)

HEX64 = r"[0-9a-f]{64}"
META_RE = re.compile(
    rf"^BOUNDED_ROW_META schema=1 logical=(\d+) source=(\d+) "
    rf"word_bytes=8 index_bytes=4 source_commit=([0-9a-f]{{40}}) "
    rf"gem5_sha256=({HEX64}) benchmark_sha256=({HEX64}) "
    rf"checkpoint_sha256=({HEX64}) mapping=RoBaRaCoCh slices=16 "
    rf"rows_per_slice=32 lines_per_row=8 offset_entries=4096$"
)
APERTURE_RE = re.compile(
    r"^BOUNDED_ROW_APERTURE slice=(\d+) lower=(\d+) upper=(\d+)$"
)
RECORD_RE = re.compile(
    r"^BOUNDED_ROW_RECORD itr=(\d+) index=(\d+) "
    r"b_paddr=(0x[0-9a-f]+) a_paddr=(0x[0-9a-f]+) "
    r"ch=(\d+) rank=(\d+) bg=(\d+) bank=(\d+) row=(\d+) "
    r"col=(\d+) wid=(\d+) slice=(\d+) grow=(\d+)$"
)
ORACLE_RE = re.compile(r"^BOUNDED_ROW_ORACLE hash=(\d+) errors=(\d+)$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fail(message: str) -> None:
    raise SystemExit(
        f"FAIL: {message}; a new owner-run physical trace is required"
    )


def extract(path: Path) -> dict[str, object]:
    if not path.is_file():
        fail(f"missing trace {path}")

    meta: re.Match[str] | None = None
    aperture_lower: list[int | None] = [None] * NUM_SLICES
    aperture_upper: list[int | None] = [None] * NUM_SLICES
    records: list[PhysicalRecord] = []  # extracted evidence, not policy state
    oracle: re.Match[str] | None = None

    for raw_line in path.read_text(errors="strict").splitlines():
        line = raw_line.strip()
        if line.startswith("BOUNDED_ROW_META"):
            if meta is not None:
                fail("duplicate trace metadata")
            meta = META_RE.fullmatch(line)
            if meta is None:
                fail("malformed trace metadata")
        elif line.startswith("BOUNDED_ROW_APERTURE"):
            match = APERTURE_RE.fullmatch(line)
            if match is None:
                fail("malformed aperture record")
            slice_id, lower, upper = map(int, match.groups())
            if not 0 <= slice_id < NUM_SLICES:
                fail("aperture slice out of range")
            if aperture_lower[slice_id] is not None:
                fail("duplicate aperture slice")
            aperture_lower[slice_id] = lower
            aperture_upper[slice_id] = upper
        elif line.startswith("BOUNDED_ROW_RECORD"):
            match = RECORD_RE.fullmatch(line)
            if match is None:
                fail("malformed physical record")
            values = match.groups()
            record = PhysicalRecord(
                itr=int(values[0]),
                index=int(values[1]),
                b_paddr=int(values[2], 16),
                a_line_paddr=int(values[3], 16),
                channel=int(values[4]),
                rank=int(values[5]),
                bankgroup=int(values[6]),
                bank=int(values[7]),
                row=int(values[8]),
                column=int(values[9]),
                wid=int(values[10]),
            )
            exported_slice = int(values[11])
            exported_grow = int(values[12])
            if record.slice_id != exported_slice:
                fail("exported slice disagrees with native 16-slice mapping")
            if record.grow != exported_grow:
                fail("exported grow disagrees with native 16-slice mapping")
            records.append(record)
        elif line.startswith("BOUNDED_ROW_ORACLE"):
            if oracle is not None:
                fail("duplicate workload oracle")
            oracle = ORACLE_RE.fullmatch(line)
            if oracle is None:
                fail("malformed workload oracle")

    if meta is None:
        fail("trace metadata absent")
    if oracle is None:
        fail("exact workload oracle absent")
    if int(oracle.group(2)) != 0:
        fail("workload oracle reports errors")
    if any(value is None for value in aperture_lower + aperture_upper):
        fail("not all 16 physical aperture bounds were exported")

    logical = int(meta.group(1))
    source_elements = int(meta.group(2))
    if len(records) != logical:
        fail(f"physical record count {len(records)} != logical {logical}")
    geometry = ApertureGeometry(
        tuple(int(value) for value in aperture_lower),
        tuple(int(value) for value in aperture_upper),
    )
    # Model preflight validates every B index and decoded field.  No policy
    # state is constructed or mutated by this extractor.
    Model(
        logical_elements=logical,
        active_elements=ACTIVE_ELEMENTS,
        source_elements=source_elements,
    )._validate_trace(records, geometry)

    record_digest = hashlib.sha256()
    for record in records:
        record_digest.update(
            json.dumps(
                record.__dict__, sort_keys=True, separators=(",", ":")
            ).encode()
        )
        record_digest.update(b"\n")

    return {
        "schema": 1,
        "evidence_class": "gem5_physical_trace",
        "trace_path": str(path.resolve()),
        "trace_sha256": sha256(path),
        "record_sha256": record_digest.hexdigest(),
        "logical_elements": logical,
        "source_elements": source_elements,
        "record_count": len(records),
        "source_commit": meta.group(3),
        "gem5_sha256": meta.group(4),
        "benchmark_sha256": meta.group(5),
        "checkpoint_sha256": meta.group(6),
        "workload_oracle_hash": int(oracle.group(1)),
        "workload_errors": 0,
        "geometry": {
            "slices": NUM_SLICES,
            "rows_per_slice": ROWS_PER_SLICE,
            "row_slots": NUM_SLICES * ROWS_PER_SLICE,
            "lines_per_row": LINES_PER_ROW,
            "offset_entries": ACTIVE_ELEMENTS,
            "grow_lower": list(geometry.grow_lower),
            "grow_upper_exclusive": list(geometry.grow_upper_exclusive),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    args = parser.parse_args()
    print(json.dumps(extract(args.trace), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
