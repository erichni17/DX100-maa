#!/usr/bin/env python3
"""Lower-bound accounting for selected-subset, multi-pass gather reorder.

This model deliberately accounts for scans and bookkeeping, not simulated time.
It describes a 16K logical B stream whose A descriptors are retained in a
smaller active metadata window, then revisited by a deterministic selector.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass


def ceil_div(dividend: int, divisor: int) -> int:
    if dividend <= 0 or divisor <= 0:
        raise ValueError("dividend and divisor must be positive")
    return (dividend + divisor - 1) // divisor


@dataclass(frozen=True)
class HybridReorderInput:
    logical_elements: int = 16_384
    active_descriptor_entries: int = 4_096
    index_bytes: int = 4
    cache_line_bytes: int = 64
    selection_prepass: bool = False
    filter_words_per_cycle: int = 0
    spilled_descriptor_bytes: int = 0

    def validate(self) -> None:
        if self.logical_elements <= 0 or self.active_descriptor_entries <= 0:
            raise ValueError(
                "logical elements and active entries must be positive"
            )
        if self.index_bytes <= 0 or self.cache_line_bytes <= 0:
            raise ValueError("index and cache-line sizes must be positive")
        if (
            self.filter_words_per_cycle < 0
            or self.spilled_descriptor_bytes < 0
        ):
            raise ValueError(
                "throughput and spill record size cannot be negative"
            )


def analyze(point: HybridReorderInput) -> dict[str, int]:
    """Return conservative traffic/state counts for an exact selected subset.

    ``selection_prepass`` is required by policies that discover a balanced
    row-group-to-pass mapping from this tile rather than using a static function.
    ``spilled_descriptor_bytes`` is zero when later passes rescan B; otherwise
    it charges one LLC write and one LLC read per non-retained descriptor.
    """
    point.validate()
    passes = ceil_div(point.logical_elements, point.active_descriptor_entries)
    selection_bits = max(1, math.ceil(math.log2(passes)))
    index_bytes_per_scan = point.logical_elements * point.index_bytes
    index_lines_per_scan = ceil_div(
        index_bytes_per_scan, point.cache_line_bytes
    )
    total_scans = passes + int(point.selection_prepass)
    filter_words = point.logical_elements * total_scans
    spill_records = point.logical_elements - point.active_descriptor_entries
    spill_bytes_one_direction = spill_records * point.spilled_descriptor_bytes
    filter_cycles = (
        0
        if point.filter_words_per_cycle == 0
        else ceil_div(filter_words, point.filter_words_per_cycle)
    )
    return {
        "logical_elements": point.logical_elements,
        "active_descriptor_entries": point.active_descriptor_entries,
        "minimum_selected_passes": passes,
        "selection_prepass_scans": int(point.selection_prepass),
        "total_b_scans": total_scans,
        "index_bytes_per_scan": index_bytes_per_scan,
        "index_cache_lines_per_scan": index_lines_per_scan,
        "total_index_scan_bytes": total_scans * index_bytes_per_scan,
        "extra_index_scan_bytes_vs_one_pass": (total_scans - 1)
        * index_bytes_per_scan,
        "total_index_cache_line_reads": total_scans * index_lines_per_scan,
        "llc_residency_bytes_to_avoid_refetch": index_bytes_per_scan,
        "selection_label_bits": point.logical_elements * selection_bits,
        "selection_label_bytes": ceil_div(
            point.logical_elements * selection_bits, 8
        ),
        "completion_bitmap_bytes": ceil_div(point.logical_elements, 8),
        "filter_words": filter_words,
        "filter_cycles_if_serialized": filter_cycles,
        "spill_records_if_materialized": spill_records,
        "spill_bytes_one_direction": spill_bytes_one_direction,
        "spill_read_write_bytes": 2 * spill_bytes_one_direction,
    }


def render_markdown(result: dict[str, int]) -> str:
    return "\n".join(
        [
            "# Hybrid Reorder Cost Model",
            "",
            "| Quantity | Lower-bound count |",
            "|---|---:|",
            *[
                f"| {key.replace('_', ' ')} | {value:,} |"
                for key, value in result.items()
            ],
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logical-elements", type=int, default=16_384)
    parser.add_argument("--active-descriptor-entries", type=int, default=4_096)
    parser.add_argument("--index-bytes", type=int, default=4)
    parser.add_argument("--cache-line-bytes", type=int, default=64)
    parser.add_argument("--selection-prepass", action="store_true")
    parser.add_argument("--filter-words-per-cycle", type=int, default=0)
    parser.add_argument("--spilled-descriptor-bytes", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    values = vars(args).copy()
    emit_json = values.pop("json")
    result = analyze(HybridReorderInput(**values))
    print(
        json.dumps(result, indent=2, sort_keys=True)
        if emit_json
        else render_markdown(result),
        end="",
    )


if __name__ == "__main__":
    main()
