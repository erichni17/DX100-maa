#!/usr/bin/env python3
"""Source-checked storage ledger for DX100's SPD and virtual-gather state.

This intentionally reports only byte counts whose element widths are explicit in
the source.  C++ container/node, ``int``, ``Addr``, ``Tick``, and allocator
costs are inventory items, not silently converted into hardware bits.
"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_SOURCE = {
    "src/mem/MAA/SPD.cc": (
        "num_tiles * physical_tile_elements * sizeof(uint32_t)",
        "element_finished = new bool[num_tiles * physical_tile_elements]",
        "waiting_units_funcs = new std::vector<uint8_t>[num_tiles]",
    ),
    "configs/common/MAAConfig.py": (
        '* opts["num_tile_elements"]',
        "virtual_page_ready_size",
    ),
    "src/mem/MAA/IndirectAccess.hh": (
        "std::array<uint8_t, 64> data{}",
        "std::vector<VirtualResponseSlot> virtual_response_slots",
        "std::vector<VirtualCombineSlot> virtual_combine_slots",
        "std::map<int, DirectIndexWord> direct_index_words",
    ),
    "src/mem/MAA/IndirectAccess.cc": (
        "virtual_response_slots.resize(_virtual_response_slots)",
        "virtual_combine_slots.resize(_virtual_combine_slots)",
        "static_cast<size_t>(direct_index_buffer_lines)",
        "RT[i] = new RowTableSlice[current_num_RT_slices]",
    ),
}
SOURCE_DEFAULTS = {"response_slots": 8, "combine_slots": 16, "index_lines": 1}
CONSUMER_EXPERIMENT = {
    "response_slots": 96,
    "combine_slots": 384,
    "index_lines": 4,
}


def checked_sources() -> list[str]:
    """Fail closed if this ledger no longer matches the allocation sources."""
    checked = []
    for relative, needles in REQUIRED_SOURCE.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        missing = [needle for needle in needles if needle not in text]
        if missing:
            raise SystemExit(f"source check failed for {relative}: {missing}")
        checked.append(relative)
    return checked


def payload_bytes(elements: int, word_bytes: int = 4) -> int:
    return elements * word_bytes


def virtual_data_capacity(
    response_slots: int,
    combine_slots: int,
    index_lines: int,
    indirect_units: int,
) -> dict:
    """Return explicit line-data capacity, excluding C++ metadata."""
    if any(
        value <= 0
        for value in (
            response_slots,
            combine_slots,
            index_lines,
            indirect_units,
        )
    ):
        raise ValueError(
            "virtual capacities and indirect-units must be positive"
        )
    generic_per_unit = (response_slots + combine_slots) * 64
    direct_per_unit = generic_per_unit + index_lines * 64
    return {
        "response_slots": response_slots,
        "response_line_bytes_per_indirect_unit": response_slots * 64,
        "combiner_slots": combine_slots,
        "combiner_line_bytes_per_indirect_unit": combine_slots * 64,
        "generic_virtual_data_bytes_per_indirect_unit": generic_per_unit,
        "generic_virtual_data_bytes_all_indirect_units": generic_per_unit
        * indirect_units,
        "direct_index_lines": index_lines,
        "direct_index_word_capacity_bytes_per_indirect_unit": index_lines * 64,
        "direct_virtual_data_bytes_per_indirect_unit": direct_per_unit,
        "direct_virtual_data_bytes_all_indirect_units": direct_per_unit
        * indirect_units,
        "indirect_units": indirect_units,
    }


def ledger(
    cores: int,
    tiles_per_core: int,
    response_slots: int = 8,
    combine_slots: int = 16,
    index_lines: int = 1,
    indirect_units: int = 4,
) -> dict:
    if cores <= 0 or tiles_per_core <= 0:
        raise ValueError("cores and tiles-per-core must be positive")
    tiles = cores * tiles_per_core
    native16 = payload_bytes(16384)
    native4 = payload_bytes(4096)
    selected_virtual = virtual_data_capacity(
        response_slots, combine_slots, index_lines, indirect_units
    )
    return {
        "source_checked": checked_sources(),
        "configuration": {
            "cores": cores,
            "tiles_per_core": tiles_per_core,
            "spd_lane_tiles": tiles,
            "lane_bytes": 4,
            "cache_line_bytes": 64,
        },
        "spd_payload": {
            "native_16k_lane_tile_bytes": native16,
            "native_16k_total_bytes": tiles * native16,
            "native_4k_lane_tile_bytes": native4,
            "native_4k_total_bytes": tiles * native4,
            "transparent_16k_logical_4k_physical_total_bytes": tiles * native4,
            "transparent_payload_reduction_bytes": tiles
            * (native16 - native4),
            "transparent_payload_reduction_percent": 75.0,
            "logical_aperture_bytes_per_address_range": tiles * native16,
            "address_ranges_for_payload": 2,
        },
        "fp64_tile_semantics": {
            "source_rule": "SPD checks 8-byte accesses by requiring tile_id + 1",
            "physical_lane_tiles_per_fp64_tile": 2,
            "one_4k_fp64_tile_bytes": 2 * native4,
            "two_4k_fp64_staging_tiles_bytes": 2 * 2 * native4,
            "one_native_16k_fp64_tile_bytes": 2 * native16,
            "two_native_16k_fp64_tiles_bytes": 2 * 2 * native16,
        },
        "selected_virtual_data_capacity": {
            **selected_virtual,
            "caveat": (
                "The index window is map-backed DirectIndexWord entries, not a "
                "64-byte C++ array.  64 B/line is the bounded 16xuint32_t data "
                "capacity; map/node bytes and packed-response vector allocation "
                "are not synthesized-bit counts."
            ),
        },
        "named_virtual_points": {
            "source_defaults": virtual_data_capacity(
                **SOURCE_DEFAULTS, indirect_units=indirect_units
            ),
            "matched_virtual_tile_consumer_experiment": virtual_data_capacity(
                **CONSUMER_EXPERIMENT, indirect_units=indirect_units
            ),
        },
        "explicit_width_accounting": {
            "spd_payload": "4 bytes per physical lane element (uint32_t)",
            "spd_element_finished": "one C++ bool per physical lane element; synthesis width is not fixed here",
            "spd_tile_scalars": "TileStatus:uint8_t, dirty:bool, ready:uint16_t, size:uint32_t per lane tile",
            "row_table": "Entry is Addr + int + int, plus valid and claimed bool arrays; Addr/int widths and padding are deliberately unresolved",
            "offset_table": "OffsetTableEntry is three int fields, plus bool valid and vector free list; int width/padding unresolved",
            "virtual_control": "tags, Addr/Tick/int fields, maps/sets/vectors, and their allocator overhead are not converted to hardware bits",
        },
        "simulator_only_or_not_hardware_bounded": [
            "SPD waiting_units_funcs and waiting_units_ids: vector contents grow with waiters",
            "IndirectAccess history maps keyed by address",
            "virtual_source_reservations map and virtual_retirement_write_pages map/vector payloads",
            "virtual_outstanding_write_lines set (count is guarded, node footprint is not a hardware allocation)",
            "MAA port outstanding/deferred unordered maps and deque payloads",
            "all STL capacity, node, allocator, and host pointer overhead",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cores", type=int, default=4)
    parser.add_argument("--tiles-per-core", type=int, default=8)
    parser.add_argument("--response-slots", type=int, default=8)
    parser.add_argument("--combine-slots", type=int, default=16)
    parser.add_argument("--index-lines", type=int, default=1)
    parser.add_argument("--indirect-units", type=int, default=4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = ledger(
        args.cores,
        args.tiles_per_core,
        args.response_slots,
        args.combine_slots,
        args.index_lines,
        args.indirect_units,
    )
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
