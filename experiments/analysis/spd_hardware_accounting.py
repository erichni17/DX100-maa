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
        "tiles_data = new uint8_t[allocated_payload_bytes]",
        "element_finished = new bool[allocated_element_count]",
        "new std::vector<uint8_t>[visible_tile_count]",
        "delete[] tiles_data",
        "delete[] element_finished",
    ),
    "src/mem/MAA/LogicalSPDHiddenPayload.hh": (
        "LogicalSlotsPerMAA = 2",
        "PageElements = 2048",
        "FP64Bytes = 8",
        "PayloadBytesPerMAA =",
        "Accounting-only compatibility constants",
    ),
    "src/mem/MAA/LogicalSPDCacheRuntime.hh": (
        "std::array<double, Slice::PayloadElements> payload{}",
        "PrivatePayloadBits =",
        "Slice::PayloadBytes * 8",
    ),
    "src/mem/MAA/LogicalSPDCacheGem5Bridge.cc": (
        "std::make_unique<LogicalSPDCacheRuntime>(mode)",
        "runtimes.reserve(numMaas)",
    ),
    "src/mem/MAA/MAA.cc": (
        "spd = new SPD(",
        "delete spd;",
    ),
    "configs/common/MAAConfig.py": (
        '* opts["num_tile_elements"]',
        "virtual_page_ready_size",
    ),
    "src/mem/MAA/IndirectAccess.hh": (
        "std::vector<VirtualResponseSlot> virtual_response_slots",
        "VirtualResponsePayloadStore virtual_response_line_payloads",
        "std::vector<VirtualCombineSlot> virtual_combine_slots",
        "std::map<int, DirectIndexWord> direct_index_words",
    ),
    "src/mem/MAA/VirtualResponsePayloadStore.hh": (
        "static constexpr std::size_t LineBytes = 64",
        "if (!packed)",
        "lines.resize(slots)",
        "std::vector<Line> lines",
    ),
    "src/mem/MAA/IndirectAccess.cc": (
        "virtual_response_slots.resize(_virtual_response_slots)",
        "virtual_combine_slots.resize(_virtual_combine_slots)",
        "static_cast<size_t>(direct_index_buffer_lines)",
        "RT[i] = new RowTableSlice[current_num_RT_slices]",
    ),
}
SOURCE_DEFAULTS = {
    "response_slots": 8,
    "combine_slots": 16,
    "index_lines": 1,
}
CONSUMER_EXPERIMENT = {
    "response_slots": 96,
    "combine_slots": 384,
    "index_lines": 4,
    "response_word_pool": 480,
}
RUNTIME_LOGICAL_SLOTS_PER_MAA = 2
RUNTIME_PAGE_ELEMENTS = 2048
FP64_BYTES = 8
PACKED_PRIVATE_METADATA_LOWER_BOUND_BYTES = 1309


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
    response_words: int = 0,
    response_word_pool: int = 0,
    word_bytes: int = 4,
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
    if response_words < 0 or response_word_pool < 0:
        raise ValueError("packed response capacities must be nonnegative")
    if word_bytes not in (4, 8):
        raise ValueError("virtual response word bytes must be 4 or 8")
    if response_word_pool:
        response_mode = "packed-word-pool"
        response_payload_per_unit = response_word_pool * word_bytes
    elif response_words:
        response_mode = "packed-words-per-slot"
        response_payload_per_unit = (
            response_slots * response_words * word_bytes
        )
    else:
        response_mode = "unpacked-fixed-lines"
        response_payload_per_unit = response_slots * 64
    generic_per_unit = response_payload_per_unit + combine_slots * 64
    direct_per_unit = generic_per_unit + index_lines * 64
    return {
        "response_slots": response_slots,
        "response_payload_mode": response_mode,
        "response_words_per_slot": response_words,
        "response_word_pool": response_word_pool,
        "response_word_bytes": word_bytes,
        "response_line_bytes_per_indirect_unit": (
            response_slots * 64
            if response_mode == "unpacked-fixed-lines"
            else 0
        ),
        "response_packed_word_bytes_per_indirect_unit": (
            response_payload_per_unit
            if response_mode != "unpacked-fixed-lines"
            else 0
        ),
        "response_payload_bytes_per_indirect_unit": response_payload_per_unit,
        "inactive_response_line_bytes_per_indirect_unit": 0,
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
    maas: int = 4,
    response_words: int = 0,
    response_word_pool: int = 0,
    word_bytes: int = 4,
) -> dict:
    if cores <= 0 or tiles_per_core <= 0 or maas <= 0:
        raise ValueError("cores, tiles-per-core, and MAAs must be positive")
    tiles = cores * tiles_per_core
    native16 = payload_bytes(16384)
    native4 = payload_bytes(4096)
    visible_physical_spd = tiles * native4
    private_payload_per_maa = (
        RUNTIME_LOGICAL_SLOTS_PER_MAA * RUNTIME_PAGE_ELEMENTS * FP64_BYTES
    )
    private_payload_total = maas * private_payload_per_maa
    transparent_payload_total = visible_physical_spd + private_payload_total
    native_payload_total = tiles * native16
    selected_virtual = virtual_data_capacity(
        response_slots,
        combine_slots,
        index_lines,
        indirect_units,
        response_words,
        response_word_pool,
        word_bytes,
    )
    return {
        "source_checked": checked_sources(),
        "configuration": {
            "cores": cores,
            "tiles_per_core": tiles_per_core,
            "spd_lane_tiles": tiles,
            "maas": maas,
            "lane_bytes": 4,
            "cache_line_bytes": 64,
        },
        "spd_payload": {
            "native_16k_lane_tile_bytes": native16,
            "native_16k_total_bytes": native_payload_total,
            "native_4k_lane_tile_bytes": native4,
            "native_4k_total_bytes": visible_physical_spd,
            "transparent_visible_spd_payload_bytes": visible_physical_spd,
            "private_logical_spd_payload_bytes_per_maa": (
                private_payload_per_maa
            ),
            "private_logical_spd_payload_bytes": private_payload_total,
            "packed_private_logical_spd_metadata_lower_bound_bytes_per_maa": (
                PACKED_PRIVATE_METADATA_LOWER_BOUND_BYTES
            ),
            "packed_private_logical_spd_metadata_lower_bound_bytes": (
                maas * PACKED_PRIVATE_METADATA_LOWER_BOUND_BYTES
            ),
            "transparent_visible_plus_private_payload_bytes": (
                transparent_payload_total
            ),
            "transparent_payload_reduction_bytes": (
                native_payload_total - transparent_payload_total
            ),
            "transparent_payload_reduction_percent": (
                100.0
                * (native_payload_total - transparent_payload_total)
                / native_payload_total
            ),
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
                "are not synthesized-bit counts. Packed response modes do not "
                "also charge inactive fixed response lines."
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
            "spd_visible_payload": (
                "4 bytes per physical lane element (uint32_t)"
            ),
            "spd_private_payload": (
                "one Runtime-owned 4096-element FP64 bank x 8 bytes per MAA; "
                "Serial4K exposes one slot and PingPong2K exposes two 2048-element slots"
            ),
            "spd_private_metadata_lower_bound": (
                "1,309 packed semantic bytes per MAA, separate from the 32,768-byte payload"
            ),
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
    parser.add_argument("--maas", type=int, default=4)
    parser.add_argument("--response-words", type=int, default=0)
    parser.add_argument("--response-word-pool", type=int, default=0)
    parser.add_argument("--word-bytes", type=int, default=4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = ledger(
        cores=args.cores,
        tiles_per_core=args.tiles_per_core,
        response_slots=args.response_slots,
        combine_slots=args.combine_slots,
        index_lines=args.index_lines,
        indirect_units=args.indirect_units,
        maas=args.maas,
        response_words=args.response_words,
        response_word_pool=args.response_word_pool,
        word_bytes=args.word_bytes,
    )
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
