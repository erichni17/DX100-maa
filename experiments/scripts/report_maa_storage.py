#!/usr/bin/env python3
"""Report modeled MAA storage from a frozen gem5 configuration."""

import argparse
import configparser
import hashlib
import json
import math
from pathlib import Path


def fail(message: str) -> None:
    raise SystemExit(f"MAA storage report failed: {message}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def integer(section: configparser.SectionProxy, key: str) -> int:
    try:
        return int(section[key])
    except (KeyError, ValueError) as error:
        fail(f"invalid system.maa value for {key}: {error}")


def bits_for_values(count: int) -> int:
    if count <= 1:
        return 1
    return math.ceil(math.log2(count))


def format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.2f} {unit}" if unit != "B" else f"{value} B"
        amount /= 1024
    raise AssertionError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--mechanism",
        required=True,
        choices=("native", "compact", "direct-index", "generic-virtual"),
    )
    parser.add_argument("--word-bytes", type=int, choices=(4, 8), default=8)
    parser.add_argument(
        "--address-bits",
        type=int,
        default=64,
        help="conservative address/tag width used by the control-state ledger",
    )
    parser.add_argument(
        "--dram-subslices",
        type=int,
        required=True,
        help="channel x rank x bank-group x bank count",
    )
    parser.add_argument(
        "--dram-ranks",
        type=int,
        default=1,
        help="ranks per channel used to enumerate Row-Table organizations",
    )
    args = parser.parse_args()

    config_path = args.config.resolve()
    if not config_path.is_file():
        fail(f"configuration does not exist: {config_path}")
    config = configparser.RawConfigParser(strict=False)
    config.read(config_path)
    if not config.has_section("system.maa"):
        fail("configuration has no system.maa section")
    maa = config["system.maa"]

    cores = integer(maa, "num_cores")
    tiles_per_core = integer(maa, "num_tiles_per_core")
    logical = integer(maa, "num_tile_elements")
    physical = integer(maa, "physical_tile_elements") or logical
    try:
        offset_entries = int(maa.get("num_offset_table_entries", "0")) or logical
    except ValueError:
        fail("invalid system.maa value for num_offset_table_entries")
    try:
        offset_epoch_entries = (
            int(maa.get("num_offset_table_epoch_entries", "0"))
            or offset_entries
        )
    except ValueError:
        fail("invalid system.maa value for num_offset_table_epoch_entries")
    maas = integer(maa, "num_maas")
    indirect_per_maa = integer(maa, "num_indirect_units_per_maa")
    memory_channels = integer(maa, "num_memory_channels")
    initial_slices = integer(maa, "num_initial_row_table_slices")
    rows_per_slice = integer(maa, "num_row_table_rows_per_slice")
    entries_per_subslice_row = integer(
        maa, "num_row_table_entries_per_subslice_row"
    )
    combine_slots = integer(maa, "virtual_combine_slots")
    response_slots = integer(maa, "virtual_response_slots")
    response_words = integer(maa, "virtual_response_words")
    response_pool = integer(maa, "virtual_response_word_pool")
    index_lines = integer(maa, "virtual_index_buffer_lines")
    outstanding_writes = integer(maa, "virtual_max_outstanding_writes")
    native_issue_order = maa.getboolean("virtual_native_issue_order")

    positive = {
        "num_cores": cores,
        "num_tiles_per_core": tiles_per_core,
        "num_tile_elements": logical,
        "physical_tile_elements": physical,
        "num_offset_table_entries": offset_entries,
        "num_offset_table_epoch_entries": offset_epoch_entries,
        "num_maas": maas,
        "num_indirect_units_per_maa": indirect_per_maa,
        "num_memory_channels": memory_channels,
        "num_initial_row_table_slices": initial_slices,
        "num_row_table_rows_per_slice": rows_per_slice,
        "num_row_table_entries_per_subslice_row": entries_per_subslice_row,
        "dram_subslices": args.dram_subslices,
        "dram_ranks": args.dram_ranks,
        "address_bits": args.address_bits,
    }
    for name, value in positive.items():
        if value <= 0:
            fail(f"{name} must be positive, got {value}")
    if physical > logical or logical % physical:
        fail(
            "physical tile capacity must divide and not exceed logical capacity"
        )
    if not 1 <= offset_entries <= logical:
        fail("Offset-Table capacity must be within the logical tile capacity")
    if not 1 <= offset_epoch_entries <= offset_entries:
        fail("Offset-Table epoch capacity must be within table capacity")
    if args.dram_subslices % initial_slices:
        fail(
            "DRAM subslices must divide evenly across initial Row-Table slices"
        )
    minimum_slices = memory_channels * args.dram_ranks * 2
    if args.dram_subslices % minimum_slices:
        fail("DRAM subslices must divide the minimum Row-Table slice count")
    organization_ratio = args.dram_subslices // minimum_slices
    if organization_ratio & (organization_ratio - 1):
        fail("Row-Table organization ratio must be a power of two")
    allocated_slices = []
    slices = minimum_slices
    while slices <= args.dram_subslices:
        allocated_slices.append(slices)
        slices *= 2
    if initial_slices not in allocated_slices:
        fail("initial Row-Table slices are not an allocated organization")

    tiles = cores * tiles_per_core
    indirect_units = maas * indirect_per_maa
    native_spd_bytes = tiles * logical * 4
    physical_spd_bytes = tiles * physical * 4
    logical_aperture_bytes = native_spd_bytes
    unbacked_aperture_tail_bytes = tiles * (logical - physical) * 4
    invalidator_entries = logical_aperture_bytes // 64
    virtual_pages_used = logical // physical

    # C++ stores one 12-byte entry plus a byte-valid array entry per iteration.
    offset_model_bytes_per_unit = offset_entries * 13
    iteration_bits = bits_for_values(logical + 1)
    word_id_bits = bits_for_values(64 // args.word_bytes)
    offset_lower_bits = offset_entries * (iteration_bits + word_id_bits + 1)
    offset_lower_bytes_per_unit = math.ceil(offset_lower_bits / 8)

    entries_per_row = entries_per_subslice_row * (
        args.dram_subslices // initial_slices
    )
    active_rows = initial_slices * rows_per_slice
    active_row_entries = initial_slices * rows_per_slice * entries_per_row
    row_entry_lower_bits = 64 + 2 * iteration_bits + 1
    row_lower_bytes_per_unit = math.ceil(
        active_row_entries * row_entry_lower_bits / 8
    )
    row_header_lower_bits_per_unit = active_rows * (args.address_bits + 2)
    row_header_lower_bytes_per_unit = math.ceil(
        row_header_lower_bits_per_unit / 8
    )
    allocated_row_entries = 0
    allocated_rows = 0
    allocated_row_lower_bytes_per_unit = 0
    allocated_row_header_lower_bytes_per_unit = 0
    for config_slices in allocated_slices:
        config_entries_per_row = entries_per_subslice_row * (
            args.dram_subslices // config_slices
        )
        config_rows = config_slices * rows_per_slice
        config_entries = config_rows * config_entries_per_row
        allocated_rows += config_rows
        allocated_row_entries += config_entries
        allocated_row_lower_bytes_per_unit += math.ceil(
            config_entries * row_entry_lower_bits / 8
        )
        allocated_row_header_lower_bytes_per_unit += math.ceil(
            config_rows * (args.address_bits + 2) / 8
        )
    invalidator_lower_bytes = math.ceil(invalidator_entries / 8)
    native_element_ready_lower_bytes = math.ceil(tiles * logical / 8)
    physical_element_ready_lower_bytes = math.ceil(tiles * physical / 8)
    retained_descriptor_lower_bytes = (
        indirect_units
        * (
            offset_lower_bytes_per_unit
            + row_lower_bytes_per_unit
            + row_header_lower_bytes_per_unit
        )
        + invalidator_lower_bytes
    )
    allocated_claim_bits_per_unit = allocated_row_entries
    allocated_claim_bytes_per_unit = math.ceil(
        allocated_claim_bits_per_unit / 8
    )
    allocated_descriptor_lower_bytes = (
        indirect_units
        * (
            offset_lower_bytes_per_unit
            + allocated_row_lower_bytes_per_unit
            + allocated_row_header_lower_bytes_per_unit
            + allocated_claim_bytes_per_unit
        )
        + invalidator_lower_bytes
    )
    native_claim_bits_per_unit = (
        active_row_entries if native_issue_order else 0
    )
    native_claim_bytes_per_unit = math.ceil(native_claim_bits_per_unit / 8)

    # Hardware lower bounds for the bounded virtual structures. These count
    # tags and essential control, but not SRAM periphery, ports, or wiring.
    words_per_line = 64 // args.word_bytes
    index_words_per_line = 64 // 4
    row_slice_bits = bits_for_values(initial_slices)
    row_id_bits = bits_for_values(rows_per_slice)
    row_entry_bits = bits_for_values(entries_per_row)
    response_count_bits = bits_for_values(words_per_line + 1)
    response_pool_words = response_pool or (
        response_slots * (response_words or words_per_line)
    )
    response_pool_pointer_bits = bits_for_values(response_pool_words + 1)

    index_metadata_bits_per_unit = index_lines * (
        args.address_bits
        + iteration_bits
        + index_words_per_line
        + 2  # empty, pending, or ready
    )
    response_metadata_bits_per_unit = response_slots * (
        1  # valid
        + args.address_bits  # source-line tag
        + iteration_bits  # linked Offset-Table head
        + response_count_bits  # words retained in this response
        + response_count_bits  # next word to retire
        + response_pool_pointer_bits
        + row_slice_bits
        + row_id_bits
        + row_entry_bits
        + args.address_bits  # claimed DRAM grow
        + iteration_bits  # claimed chain head
    )
    combine_metadata_bits_per_unit = combine_slots * (
        1 + args.address_bits + words_per_line
    )
    combine_sets = (
        1
        if integer(maa, "virtual_combine_ways") == 0
        else (combine_slots // integer(maa, "virtual_combine_ways"))
    )
    combine_ways = integer(maa, "virtual_combine_ways") or combine_slots
    combine_replacement_bits_per_unit = combine_sets * bits_for_values(
        combine_ways
    )
    outstanding_write_bits_per_unit = outstanding_writes * (
        1 + args.address_bits
    )
    page_counter_bits_per_unit = virtual_pages_used * (5 * iteration_bits + 1)
    completion_increment_bits = tiles * max(0, virtual_pages_used - 1)

    if args.mechanism == "native":
        active_index_metadata_bits = 0
        active_response_metadata_bits = 0
        active_combine_metadata_bits = 0
        active_write_metadata_bits = 0
        active_page_counter_bits = 0
        active_claim_bits = 0
        active_completion_increment_bits = 0
    else:
        active_index_metadata_bits = (
            index_metadata_bits_per_unit
            if args.mechanism == "direct-index"
            else 0
        )
        active_response_metadata_bits = response_metadata_bits_per_unit
        active_combine_metadata_bits = (
            combine_metadata_bits_per_unit + combine_replacement_bits_per_unit
        )
        active_write_metadata_bits = outstanding_write_bits_per_unit
        active_page_counter_bits = page_counter_bits_per_unit
        active_claim_bits = native_claim_bits_per_unit
        active_completion_increment_bits = completion_increment_bits
    virtual_control_bits_per_unit = (
        active_index_metadata_bits
        + active_response_metadata_bits
        + active_combine_metadata_bits
        + active_write_metadata_bits
        + active_page_counter_bits
        + active_claim_bits
    )
    virtual_control_bytes_per_unit = math.ceil(
        virtual_control_bits_per_unit / 8
    )
    completion_increment_bytes = math.ceil(
        active_completion_increment_bits / 8
    )

    combine_payload_per_unit = combine_slots * 64
    if response_pool:
        response_payload_per_unit = response_pool * args.word_bytes
    elif response_words:
        response_payload_per_unit = (
            response_slots * response_words * args.word_bytes
        )
    else:
        response_payload_per_unit = response_slots * 64
    index_payload_per_unit = index_lines * 64
    configured_virtual_payload_per_unit = (
        combine_payload_per_unit
        + response_payload_per_unit
        + index_payload_per_unit
    )
    if args.mechanism == "native":
        active_index_payload = 0
        active_response_payload = 0
        active_combine_payload = 0
    elif args.mechanism == "direct-index":
        active_index_payload = index_payload_per_unit
        active_response_payload = response_payload_per_unit
        active_combine_payload = combine_payload_per_unit
    else:
        active_index_payload = 0
        active_response_payload = response_payload_per_unit
        active_combine_payload = combine_payload_per_unit
    active_virtual_payload_per_unit = (
        active_index_payload + active_response_payload + active_combine_payload
    )
    active_virtual_payload_total = (
        active_virtual_payload_per_unit * indirect_units
    )
    # The simulator keeps the legacy full-line array in every response slot
    # even when packed responses use the bounded word pool instead. A
    # specialized hardware implementation can union or remove this inactive
    # array, so keep it outside the active lower bound and expose it as a
    # separate conservative implementation view.
    inactive_cpp_response_line_bytes_per_unit = (
        response_slots * 64
        if args.mechanism != "native" and (response_pool or response_words)
        else 0
    )
    inactive_cpp_response_line_bytes_total = (
        inactive_cpp_response_line_bytes_per_unit * indirect_units
    )
    counted_payload = physical_spd_bytes + active_virtual_payload_total
    bounded_state_total = (
        counted_payload
        + virtual_control_bytes_per_unit * indirect_units
        + completion_increment_bytes
    )
    native_comparable_storage = (
        native_spd_bytes
        + native_element_ready_lower_bytes
        + retained_descriptor_lower_bytes
    )
    comparable_storage = (
        bounded_state_total
        + physical_element_ready_lower_bytes
        + retained_descriptor_lower_bytes
    )
    native_allocated_comparable_storage = (
        native_spd_bytes
        + native_element_ready_lower_bytes
        + allocated_descriptor_lower_bytes
    )
    allocated_comparable_storage = (
        bounded_state_total
        - native_claim_bytes_per_unit * indirect_units
        + physical_element_ready_lower_bytes
        + allocated_descriptor_lower_bytes
    )
    conservative_cpp_static_bounded_state = (
        bounded_state_total + inactive_cpp_response_line_bytes_total
    )
    conservative_cpp_static_comparable_storage = (
        comparable_storage + inactive_cpp_response_line_bytes_total
    )
    conservative_cpp_static_allocated_storage = (
        allocated_comparable_storage + inactive_cpp_response_line_bytes_total
    )

    report = {
        "provenance": {
            "config": str(config_path),
            "config_sha256": sha256(config_path),
            "mechanism": args.mechanism,
            "word_bytes": args.word_bytes,
            "dram_subslices": args.dram_subslices,
            "dram_ranks": args.dram_ranks,
            "address_bits": args.address_bits,
        },
        "configuration": {
            "cores": cores,
            "tiles_per_core": tiles_per_core,
            "tiles_total": tiles,
            "logical_elements_per_tile": logical,
            "physical_elements_per_tile": physical,
            "virtual_pages_used": virtual_pages_used,
            "indirect_units": indirect_units,
            "row_table_organizations_allocated": allocated_slices,
        },
        "scratchpad": {
            "native_logical_payload_bytes": native_spd_bytes,
            "physical_payload_bytes": physical_spd_bytes,
            "payload_reduction_pct": (
                1 - physical_spd_bytes / native_spd_bytes
            )
            * 100,
            "single_logical_address_aperture_bytes": logical_aperture_bytes,
            "logical_address_apertures": 2,
            "backed_prefix_bytes_per_aperture": physical_spd_bytes,
            "unbacked_tail_bytes_per_aperture": unbacked_aperture_tail_bytes,
            "backed_offsets_share_one_physical_spd": True,
            "element_ready_model_bytes": tiles * physical,
        },
        "retained_logical_metadata": {
            "invalidator_cache_line_entries": invalidator_entries,
            "invalidator_model_bytes": invalidator_entries,
            "invalidator_encoding_lower_bound_bytes": (
                invalidator_lower_bytes
            ),
            "completion_flags_used": tiles * virtual_pages_used,
            "completion_cpp_model_bytes_fixed_16_pages": tiles * 16,
            "logical_iteration_domain_per_indirect_unit": logical,
            "offset_entry_capacity_per_indirect_unit": offset_entries,
            "offset_epoch_capacity_per_indirect_unit": offset_epoch_entries,
            "offset_cpp_model_bytes_per_indirect_unit": (
                offset_model_bytes_per_unit
            ),
            "offset_encoding_lower_bound_bytes_per_indirect_unit": (
                offset_lower_bytes_per_unit
            ),
            "configured_row_entry_capacity_per_indirect_unit": (
                active_row_entries
            ),
            "configured_row_count_per_indirect_unit": active_rows,
            "allocated_row_entry_capacity_per_indirect_unit": (
                allocated_row_entries
            ),
            "allocated_row_count_per_indirect_unit": allocated_rows,
            "row_encoding_lower_bound_bytes_per_indirect_unit": (
                row_lower_bytes_per_unit
            ),
            "row_header_encoding_lower_bound_bytes_per_indirect_unit": (
                row_header_lower_bytes_per_unit
            ),
            "allocated_row_encoding_lower_bound_bytes_per_indirect_unit": (
                allocated_row_lower_bytes_per_unit
            ),
            "allocated_row_header_encoding_lower_bound_bytes_per_indirect_unit": (
                allocated_row_header_lower_bytes_per_unit
            ),
            "shared_descriptor_lower_bound_bytes": (
                retained_descriptor_lower_bytes
            ),
            "allocated_shared_descriptor_lower_bound_bytes": (
                allocated_descriptor_lower_bytes
            ),
            "native_order_claim_bits_per_indirect_unit": (
                native_claim_bits_per_unit
            ),
            "native_order_claim_bytes_per_indirect_unit": (
                native_claim_bytes_per_unit
            ),
            "native_order_claim_bytes_all_indirect_units": (
                native_claim_bytes_per_unit * indirect_units
            ),
            "allocated_claim_bitmap_bits_per_indirect_unit": (
                allocated_claim_bits_per_unit
            ),
            "allocated_claim_bitmap_bytes_per_indirect_unit": (
                allocated_claim_bytes_per_unit
            ),
        },
        "virtual_data_buffers": {
            "configured_index_feeder_bytes_per_indirect_unit": (
                index_payload_per_unit
            ),
            "configured_source_response_bytes_per_indirect_unit": (
                response_payload_per_unit
            ),
            "configured_destination_combiner_bytes_per_indirect_unit": (
                combine_payload_per_unit
            ),
            "configured_total_bytes_per_indirect_unit": (
                configured_virtual_payload_per_unit
            ),
            "active_index_feeder_bytes_per_indirect_unit": (
                active_index_payload
            ),
            "active_source_response_bytes_per_indirect_unit": (
                active_response_payload
            ),
            "active_destination_combiner_bytes_per_indirect_unit": (
                active_combine_payload
            ),
            "active_total_bytes_per_indirect_unit": (
                active_virtual_payload_per_unit
            ),
            "active_total_bytes_all_indirect_units": (
                active_virtual_payload_total
            ),
            "inactive_cpp_response_line_bytes_per_indirect_unit": (
                inactive_cpp_response_line_bytes_per_unit
            ),
            "inactive_cpp_response_line_bytes_all_indirect_units": (
                inactive_cpp_response_line_bytes_total
            ),
            "inactive_cpp_response_line_note": (
                "Legacy 64-byte arrays remain allocated in each C++ response "
                "slot while packed responses use the bounded word pool. They "
                "are not required by the selected hardware mode."
            ),
        },
        "incremental_virtual_control_lower_bound": {
            "index_feeder_metadata_bits_per_indirect_unit": (
                active_index_metadata_bits
            ),
            "source_response_metadata_bits_per_indirect_unit": (
                active_response_metadata_bits
            ),
            "destination_combiner_metadata_bits_per_indirect_unit": (
                active_combine_metadata_bits
            ),
            "outstanding_write_metadata_bits_per_indirect_unit": (
                active_write_metadata_bits
            ),
            "page_counter_bits_per_indirect_unit": (active_page_counter_bits),
            "native_order_claim_bits_per_indirect_unit": active_claim_bits,
            "metadata_bytes_per_indirect_unit": virtual_control_bytes_per_unit,
            "metadata_bytes_all_indirect_units": (
                virtual_control_bytes_per_unit * indirect_units
            ),
            "incremental_completion_bytes": completion_increment_bytes,
            "assumes_unified_request_response_slots": True,
        },
        "counted_payload": {
            "physical_spd_plus_virtual_buffers_bytes": counted_payload,
            "reduction_vs_native_spd_pct": (
                1 - counted_payload / native_spd_bytes
            )
            * 100,
        },
        "bounded_state_lower_bound": {
            "physical_spd_virtual_payload_and_control_bytes": (
                bounded_state_total
            ),
            "reduction_vs_native_spd_pct": (
                1 - bounded_state_total / native_spd_bytes
            )
            * 100,
        },
        "comparable_storage_lower_bound": {
            "scope": "fixed active Row-Table organization",
            "native_element_ready_bytes": native_element_ready_lower_bytes,
            "physical_element_ready_bytes": (
                physical_element_ready_lower_bytes
            ),
            "retained_shared_descriptor_bytes": (
                retained_descriptor_lower_bytes
            ),
            "native_total_bytes": native_comparable_storage,
            "configured_total_bytes": comparable_storage,
            "reduction_vs_native_pct": (
                1 - comparable_storage / native_comparable_storage
            )
            * 100,
        },
        "allocated_model_storage_lower_bound": {
            "scope": "all Row-Table organizations allocated by gem5",
            "retained_shared_descriptor_bytes": (
                allocated_descriptor_lower_bytes
            ),
            "native_total_bytes": native_allocated_comparable_storage,
            "configured_total_bytes": allocated_comparable_storage,
            "reduction_vs_native_pct": (
                1
                - allocated_comparable_storage
                / native_allocated_comparable_storage
            )
            * 100,
        },
        "conservative_cpp_static_storage_view": {
            "scope": (
                "candidate-only addition of inactive fixed response-line "
                "arrays; still excludes STL/container and allocator overhead"
            ),
            "inactive_fixed_response_line_bytes": (
                inactive_cpp_response_line_bytes_total
            ),
            "bounded_state_bytes": conservative_cpp_static_bounded_state,
            "comparable_configured_bytes": (
                conservative_cpp_static_comparable_storage
            ),
            "comparable_allocated_bytes": (
                conservative_cpp_static_allocated_storage
            ),
            "comparable_reduction_vs_native_pct": (
                1
                - conservative_cpp_static_comparable_storage
                / native_comparable_storage
            )
            * 100,
            "allocated_reduction_vs_native_pct": (
                1
                - conservative_cpp_static_allocated_storage
                / native_allocated_comparable_storage
            )
            * 100,
        },
        "excluded_from_counted_payload": [
            "Row/Offset metadata (included only in comparable lower bound)",
            "unbounded/general queues, arbitration, and non-capacity control",
            "cache tags, MSHRs, routing state, and outstanding packet payload",
            "ports, wiring, control, and synthesized memory periphery",
            "C++ STL node/vector objects and allocator overhead",
        ],
    }

    output = args.output_dir.resolve()
    if output.exists():
        fail(f"refusing to overwrite output: {output}")
    output.mkdir(parents=True)
    (output / "maa_storage.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# MAA Storage Ledger",
        "",
        f"Config SHA-256: `{report['provenance']['config_sha256']}`",
        f"Mechanism: `{args.mechanism}`",
        "",
        "| Item | Capacity |",
        "|---|---:|",
        f"| Native SPD payload | {format_bytes(native_spd_bytes)} |",
        f"| Configured physical SPD payload | {format_bytes(physical_spd_bytes)} |",
        f"| Active direct-index B feeder | {format_bytes(active_index_payload)} / indirect unit |",
        f"| Active source-response payload | {format_bytes(active_response_payload)} / indirect unit |",
        "| Inactive fixed C++ response-line arrays | "
        f"{format_bytes(inactive_cpp_response_line_bytes_per_unit)} / indirect unit |",
        f"| Active destination-combiner payload | {format_bytes(active_combine_payload)} / indirect unit |",
        "| Incremental virtual tags/control (lower bound) | "
        f"{format_bytes(virtual_control_bytes_per_unit)} / indirect unit |",
        "| Incremental completion state | "
        f"{format_bytes(completion_increment_bytes)} |",
        f"| Physical SPD + bounded virtual payload | {format_bytes(counted_payload)} |",
        "| Physical SPD + bounded payload/control lower bound | "
        f"{format_bytes(bounded_state_total)} |",
        "| Retained Row/Offset/invalidator lower bound | "
        f"{format_bytes(retained_descriptor_lower_bytes)} |",
        "| Allocated gem5 Row/Offset/invalidator lower bound | "
        f"{format_bytes(allocated_descriptor_lower_bytes)} |",
        "| Native readiness lower bound | "
        f"{format_bytes(native_element_ready_lower_bytes)} |",
        "| Configured physical readiness lower bound | "
        f"{format_bytes(physical_element_ready_lower_bytes)} |",
        "| Native comparable storage lower bound | "
        f"{format_bytes(native_comparable_storage)} |",
        "| Configured comparable storage lower bound | "
        f"{format_bytes(comparable_storage)} |",
        f"| Logical iteration domain | {logical:,} / indirect unit |",
        f"| Configured Offset-Table entry capacity | {offset_entries:,} / indirect unit |",
        f"| Configured Offset-Table epoch capacity | {offset_epoch_entries:,} / indirect unit |",
        f"| Configured Row-Table entry capacity | {active_row_entries:,} / indirect unit |",
        "| Allocated Row-Table entry capacity | "
        f"{allocated_row_entries:,} / indirect unit |",
        "| Native-order claimed-entry bitmap | "
        f"{format_bytes(native_claim_bytes_per_unit)} / indirect unit |",
        f"| Logical invalidator entries retained | {invalidator_entries:,} |",
        f"| Unbacked logical SPD tail | {format_bytes(unbacked_aperture_tail_bytes)} / address aperture |",
        "",
        f"Counted payload reduction versus native SPD: **{report['counted_payload']['reduction_vs_native_spd_pct']:.3f}%**.",
        "Bounded-state lower-bound reduction versus native SPD: "
        f"**{report['bounded_state_lower_bound']['reduction_vs_native_spd_pct']:.3f}%**.",
        "Comparable lower-bound reduction with retained descriptors and readiness: "
        f"**{report['comparable_storage_lower_bound']['reduction_vs_native_pct']:.3f}%**.",
        "Current gem5 allocation lower-bound reduction with all Row-Table "
        "organizations: "
        "**"
        f"{report['allocated_model_storage_lower_bound']['reduction_vs_native_pct']:.3f}%"
        "**.",
        "Conservative candidate-only reduction after also counting inactive "
        "fixed C++ response-line arrays: "
        "**"
        f"{report['conservative_cpp_static_storage_view']['comparable_reduction_vs_native_pct']:.3f}%"
        "**.",
        "",
        "This is a capacity ledger, not an area estimate. The comparable lower bound",
        "includes retained logical-sized Row/Offset/invalidator state and bit-packed",
        "readiness, but this does not prove native-equivalent descriptor lifetime or",
        "issue order. Essential tags and bounded control arrays are included as a",
        "bit-count lower bound; ports, arbitration, wiring, and memory periphery are",
        "still excluded. The conservative C++ view additionally counts the inactive",
        "fixed response-line arrays, but still does not estimate STL/allocator overhead.",
    ]
    (output / "maa_storage.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    (output / "maa_storage.pass").touch()
    print(f"PASS MAA storage ledger: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
