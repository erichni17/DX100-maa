#!/usr/bin/env python3
"""Emit the fixed-area contract for the transparent-SPD comparison.

The byte counts distinguish payload/state arrays from C++ host-container
overhead.  They describe the exact configuration used by
run_virtual_tile_consumer_case.sh; no cache or DRAM capacity is charged as
MAA-local SRAM.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_ledger() -> dict:
    cores = 4
    visible_lanes_per_core = 8
    maas = 1
    physical_elements = 4096
    runtime_page_elements = 2048
    lane_bytes = physical_elements * 4
    visible_lanes = cores * visible_lanes_per_core
    runtime_slots = maas * 2
    runtime_payload_bytes = runtime_slots * runtime_page_elements * 8

    payload = {
        "visible_spd": {
            "lanes": visible_lanes,
            "bytes": visible_lanes * lane_bytes,
        },
        "private_logical_spd_runtime": {
            "fp64_slots": runtime_slots,
            "spd_lane_tiles": 0,
            "bytes": runtime_payload_bytes,
            "storage_owner": "LogicalSPDCacheRuntime",
            "used_by_this_path": False,
        },
        "total_maa_local_payload": {
            "visible_spd_lane_tiles": visible_lanes,
            "logical_runtime_fp64_slots": runtime_slots,
            "bytes": visible_lanes * lane_bytes + runtime_payload_bytes,
        },
        "descriptor_spans_within_visible_spd": {
            "producer_token_fp64": 2 * lane_bytes,
            "physical_input_fp64": 2 * lane_bytes,
            "out_of_place_output_fp64": 2 * lane_bytes,
            "additive_to_visible_total": False,
        },
        "coherent_memory_not_maa_sram": {
            "logical_backing_fp64": 16384 * 8,
            "destination_fp64": 16384 * 8,
        },
    }

    # Exact arrays allocated by SPD.cc for the configured physical capacity.
    spd_metadata = {
        "tile_status_u8": visible_lanes,
        "tile_dirty_bool": visible_lanes,
        "tile_ready_u16": visible_lanes * 2,
        "tile_size_u32": visible_lanes * 4,
        "element_finished_bool": visible_lanes * physical_elements,
        "read_write_port_busy_tick_u64": (4 + 4) * 8,
    }
    spd_metadata["total_bytes"] = sum(spd_metadata.values())

    # Exact semantic fields are 183 bytes.  The measured x86-64 C++ object is
    # 208 bytes after ABI padding (Descriptor 144 bytes, no request FIFO).
    controller_metadata = {
        "descriptor_semantic_fields": 127,
        "state_u8": 1,
        "producer_ready_4_bool": 4,
        "chunk_ready_8_bool": 8,
        "phase_8_u8": 8,
        "input_owner_2_i32": 8,
        "output_owner_2_i32": 8,
        "four_chunk_counters_i32": 16,
        "stream_and_alu_inflight_bool": 2,
        "transition_cycles_u8": 1,
        "semantic_total_bytes": 183,
        "x86_64_host_object_bytes": 208,
        "request_fifo_entries": 0,
        "request_value_bytes_when_returned": 40,
    }

    # These capacities already exist and are invariant across all three arms.
    queues = {
        "instruction_file_entries": cores * 8,
        "stream_request_table": {
            "addresses": 128,
            "entries_per_address": 16,
            "entry_semantic_bytes": 6,
            "entry_x86_64_bytes": 8,
        },
        "indirect_offset_table_entries": 16384,
        "initial_row_table": {
            "slices": 16,
            "rows_per_slice": 64,
            "entries_per_row": 8,
        },
        "virtual_response_slots": 96,
        "virtual_response_word_pool": 480,
        "virtual_combine_slots": 384,
        "virtual_combine_words": 4096,
        "virtual_max_outstanding_writes": 64,
        "direct_index_buffer_lines": 4,
        "cache_side_packet_credits": 512,
        "cpu_side_packet_credits": 512,
        "producer_page_ready_credits": 4,
    }

    # Payload/tag arrays behind the fixed table dimensions.  The Row Table
    # allocates all four DDR4 organizations (2/4/8/16 slices); every
    # organization contains 8192 entries, so this is not merely the active
    # 16-slice view.  Free-list indices are counted as state as well.
    bounded_table_arrays = {
        "stream_request_table": {
            "entry_fields_semantic": 128 * 16 * 6,
            "entry_array_x86_64": 128 * 16 * 8,
            "address_tags_u64": 128 * 8,
            "entry_counts_i32": 128 * 4,
            "free_slot_indices_i32": 128 * 4,
            "semantic_total_bytes": 128 * 16 * 6 + 128 * (8 + 4 + 4),
            "x86_64_array_total_bytes": 128 * 16 * 8 + 128 * (8 + 4 + 4),
        },
        "indirect_offset_table": {
            "entry_fields_3xi32": 16384 * 12,
            "valid_bool": 16384,
            "free_entry_indices_i32": 16384 * 4,
            "total_semantic_bytes": 16384 * (12 + 1 + 4),
        },
        "all_row_table_organizations": {
            "slice_counts": [2, 4, 8, 16],
            "entries_per_row": [64, 32, 16, 8],
            "total_rows": (2 + 4 + 8 + 16) * 64,
            "total_entries": 4 * 8192,
            "entry_fields_addr_u64_2xi32": 4 * 8192 * 16,
            "entry_valid_bool": 4 * 8192,
            "entry_claimed_bool": 4 * 8192,
            "row_grow_addr_u64": (2 + 4 + 8 + 16) * 64 * 8,
            "row_cursor_i32": (2 + 4 + 8 + 16) * 64 * 4,
            "slice_row_valid_bool": (2 + 4 + 8 + 16) * 64,
            "slice_row_sent_bool": (2 + 4 + 8 + 16) * 64,
            "request_sent_bool_per_slice": 2 + 4 + 8 + 16,
            "total_core_array_bytes": (
                4 * 8192 * (16 + 1 + 1)
                + (2 + 4 + 8 + 16) * 64 * (8 + 4 + 1 + 1)
                + (2 + 4 + 8 + 16)
            ),
        },
    }

    virtual_payload = {
        "response_slot_line_arrays": 96 * 64,
        "response_packed_word_pool_limit": 480 * 8,
        "combine_slot_line_arrays": 384 * 64,
        "direct_index_lines": 4 * 64,
        "response_slot_semantic_tags": 96 * 49,
        "combine_slot_semantic_tags": 384 * 11,
        "note": (
            "line arrays are physically present C++ storage; the packed pool "
            "is a configured occupancy limit on dynamic host vectors and is "
            "listed separately, not silently substituted for those arrays"
        ),
    }

    mmio = {
        "spd_size_aperture": visible_lanes * 2,
        "spd_ready_aperture": visible_lanes * 2,
        "virtual_page_ready_aperture": visible_lanes * 16 * 2,
        "scalar_register_aperture": cores * 8 * 4,
        "instruction_file_aperture": 64,
    }
    mmio["total_bytes"] = sum(mmio.values())

    common = {
        "allocated_visible_spd_bytes": visible_lanes * lane_bytes,
        "allocated_logical_spd_runtime_bytes": runtime_payload_bytes,
        "allocated_maa_local_payload_bytes": (
            visible_lanes * lane_bytes + runtime_payload_bytes
        ),
        "stream_units": maas,
        "alu_units": maas,
        "alu_lanes_per_unit": 16,
        "spd_read_ports": 4,
        "spd_write_ports": 4,
        "stream_words_per_cycle": 4,
        "memory_channels": 1,
        "stream_inflight_per_controller": 1,
        "alu_inflight_per_controller": 1,
        "completion": "retire only after the final STREAM_ST completes",
    }
    arms = {
        "serial_4k": {
            **common,
            "chunks": 4,
            "chunk_elements": 4096,
            "payload_slots_used": 1,
        },
        "serial_2k": {
            **common,
            "chunks": 8,
            "chunk_elements": 2048,
            "payload_slots_used": 1,
        },
        "pingpong_2k": {
            **common,
            "chunks": 8,
            "chunk_elements": 2048,
            "payload_slots_used": 2,
        },
    }

    return {
        "schema": 1,
        "payload": payload,
        "metadata": {
            "spd_arrays": spd_metadata,
            "transparent_controller": controller_metadata,
            "mmio_apertures": mmio,
            "maa_virtual_page_state": {
                "ready_bool": visible_lanes * 16,
                "generation_u64": visible_lanes * 8,
                "consumed_generation_u64": visible_lanes * 8,
                "backing_addr_u64": visible_lanes * 8,
                "word_size_i32": visible_lanes * 4,
                "total_semantic_bytes": visible_lanes * (16 + 8 + 8 + 8 + 4),
            },
            "host_container_caveat": (
                "std::vector/map/set allocator and pointer overhead is simulator "
                "implementation overhead, not a synthesized SRAM byte budget; "
                "all bounded payload limits and queue depths are listed"
            ),
        },
        "queues_and_credits": queues,
        "bounded_table_arrays": bounded_table_arrays,
        "virtual_payload_storage": virtual_payload,
        "arms": arms,
    }


def verify_sources(root: Path) -> None:
    checks = {
        "src/mem/MAA/TransparentSPDController.hh": [
            "PhysicalElements = 4096",
            "HalfElements = 2048",
            "MaxSlots = 2",
            "streamInFlight",
            "aluInFlight",
        ],
        "src/mem/MAA/MAA.py": [
            "num_tiles_per_core",
            "transparent_spd_mode",
            "virtual_response_slots",
            "virtual_combine_slots",
            "num_spd_read_ports_per_maa",
            "num_spd_write_ports_per_maa",
        ],
        "src/mem/MAA/LogicalSPDHiddenPayload.hh": [
            "LogicalSlotsPerMAA = 2",
            "PageElements = 2048",
            "SerialSlotElements = 4096",
            "FP64Bytes = 8",
            "PayloadBytesPerMAA ==\n              32768",
            "Accounting-only compatibility constants",
        ],
        "src/mem/MAA/LogicalSPDCacheRuntime.hh": [
            "std::array<double, Slice::PayloadElements> payload{}",
            "PrivatePayloadBits =",
            "PrivatePayloadBits == 262144",
        ],
        "src/mem/MAA/LogicalSPDCacheGem5Bridge.cc": [
            "std::make_unique<LogicalSPDCacheRuntime>(mode)",
            "runtimes.reserve(numMaas)",
        ],
    }
    for relative, needles in checks.items():
        text = (root / relative).read_text()
        missing = [needle for needle in needles if needle not in text]
        if missing:
            raise SystemExit(f"{relative}: source contract missing {missing}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[2])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    verify_sources(args.root.resolve())
    rendered = json.dumps(build_ledger(), indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
