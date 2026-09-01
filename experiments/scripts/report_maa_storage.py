#!/usr/bin/env python3
"""Report modeled MAA storage from a frozen gem5 configuration."""

import argparse
import configparser
import hashlib
import json
import math
from pathlib import Path

# Exact packed RTL constants from InactiveProducerMaskedFragmentRetention.hh;
# these deliberately do not use or approximate host sizeof().
INACTIVE_MASKED_RETENTION_CAPACITIES = (0, 512, 1024, 2048, 4096)
INACTIVE_MASKED_DESCRIPTOR_COUNT = 4
INACTIVE_MASKED_BANK_COUNT = 4
INACTIVE_MASKED_MAX_LOGICAL_LINES = 2048
INACTIVE_MASKED_LINE_BITS = 64 * 8
INACTIVE_MASKED_KEY_BITS = 16 + 64 + 64 + 64
INACTIVE_MASKED_ENTRY_TAG_BITS = 1 + INACTIVE_MASKED_KEY_BITS + 16 + 16 + 64
INACTIVE_MASKED_DESCRIPTOR_BITS = 1 + INACTIVE_MASKED_KEY_BITS + 16 + 4 + 13
INACTIVE_MASKED_OUTPUT_TAG_BITS = 1 + INACTIVE_MASKED_KEY_BITS + 16 + 64
INACTIVE_MASKED_COUNTER_BITS = 13 * 64 + 2 * 13
INACTIVE_MASKED_LOOKUP_PIPELINE_BITS = (
    (16 + 64 + 64) + (2 + 16 + 5 + 3 + 64 + 16 + 64) + (64 + 64) + (1 + 64 + 3)
)
INACTIVE_MASKED_FALLBACK_REBIND_BITS = (
    4 * (1 + (16 + 64 + 64) + (2 + 16 + 5 + 3 + 64 + 16 + 64)) + 2
)
INACTIVE_MASKED_MAA_LOOKUP_CONTROL_BITS = (
    INACTIVE_MASKED_LOOKUP_PIPELINE_BITS + INACTIVE_MASKED_FALLBACK_REBIND_BITS
)
INACTIVE_MASKED_INCARNATION_BITS_PER_TOKEN = 64
SHARED_RESPONSE_MAX_LOGICAL_WORDS = 16
SHARED_RESPONSE_MAX_FANOUT_USES = 16384


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


def bits_to_bytes(bits: int) -> int:
    return (bits + 7) // 8


def format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.2f} {unit}" if unit != "B" else f"{value} B"
        amount /= 1024
    raise AssertionError("unreachable")


def inactive_masked_retention_accounting(
    capacity: int, token_count: int
) -> dict[str, int]:
    """Mirror InactiveProducerMaskedFragmentRetention packed equations."""
    if capacity not in INACTIVE_MASKED_RETENTION_CAPACITIES:
        fail(
            "inactive_page_masked_fragment_retention_lines must be zero or "
            "one of 512/1024/2048/4096, got "
            f"{capacity}"
        )

    if capacity == 0:
        return {
            "capacity_entries": 0,
            "entries_per_partition": 0,
            "entries_per_bank_per_partition": 0,
            "index_bits": 0,
            "payload_bits": 0,
            "output_payload_bits": 0,
            "payload_and_output_bytes": 0,
            "ram_tag_bits": 0,
            "descriptor_bits": 0,
            "poison_bits": 0,
            "write_port_state_bits": 0,
            "read_port_state_bits": 0,
            "output_tag_bits": 0,
            "counter_bits": 0,
            "configured_capacity_bits": 0,
            "control_bits": 0,
            "control_bytes": 0,
            "lookup_pipeline_control_bits": 0,
            "fallback_rebind_control_bits": 0,
            "maa_lookup_control_bits": 0,
            "persistent_token_incarnation_bits": 0,
            "combined_total_bits": 0,
            "combined_total_bytes": 0,
        }

    index_bits = capacity.bit_length() - 1
    payload_bits = capacity * INACTIVE_MASKED_LINE_BITS
    ram_tag_bits = capacity * INACTIVE_MASKED_ENTRY_TAG_BITS
    descriptor_bits = (
        INACTIVE_MASKED_DESCRIPTOR_COUNT * INACTIVE_MASKED_DESCRIPTOR_BITS
    )
    poison_bits = (
        INACTIVE_MASKED_DESCRIPTOR_COUNT * INACTIVE_MASKED_MAX_LOGICAL_LINES
    )
    write_port_state_bits = INACTIVE_MASKED_BANK_COUNT * (
        64
        + 1
        + 64
        + index_bits
        + INACTIVE_MASKED_LINE_BITS
        + INACTIVE_MASKED_ENTRY_TAG_BITS
    )
    read_port_state_bits = 64
    configured_capacity_bits = 13
    control_bits = (
        ram_tag_bits
        + descriptor_bits
        + poison_bits
        + write_port_state_bits
        + read_port_state_bits
        + INACTIVE_MASKED_OUTPUT_TAG_BITS
        + INACTIVE_MASKED_COUNTER_BITS
        + configured_capacity_bits
    )
    persistent_token_incarnation_bits = (
        token_count * INACTIVE_MASKED_INCARNATION_BITS_PER_TOKEN
    )
    combined_total_bits = (
        payload_bits
        + INACTIVE_MASKED_LINE_BITS
        + control_bits
        + INACTIVE_MASKED_MAA_LOOKUP_CONTROL_BITS
        + persistent_token_incarnation_bits
    )
    return {
        "capacity_entries": capacity,
        "entries_per_partition": (
            capacity // INACTIVE_MASKED_DESCRIPTOR_COUNT
        ),
        "entries_per_bank_per_partition": (
            capacity
            // (INACTIVE_MASKED_DESCRIPTOR_COUNT * INACTIVE_MASKED_BANK_COUNT)
        ),
        "index_bits": index_bits,
        "payload_bits": payload_bits,
        "output_payload_bits": INACTIVE_MASKED_LINE_BITS,
        "payload_and_output_bytes": bits_to_bytes(
            payload_bits + INACTIVE_MASKED_LINE_BITS
        ),
        "ram_tag_bits": ram_tag_bits,
        "descriptor_bits": descriptor_bits,
        "poison_bits": poison_bits,
        "write_port_state_bits": write_port_state_bits,
        "read_port_state_bits": read_port_state_bits,
        "output_tag_bits": INACTIVE_MASKED_OUTPUT_TAG_BITS,
        "counter_bits": INACTIVE_MASKED_COUNTER_BITS,
        "configured_capacity_bits": configured_capacity_bits,
        "control_bits": control_bits,
        "control_bytes": bits_to_bytes(control_bits),
        "lookup_pipeline_control_bits": (INACTIVE_MASKED_LOOKUP_PIPELINE_BITS),
        "fallback_rebind_control_bits": (INACTIVE_MASKED_FALLBACK_REBIND_BITS),
        "maa_lookup_control_bits": (INACTIVE_MASKED_MAA_LOOKUP_CONTROL_BITS),
        "persistent_token_incarnation_bits": (
            persistent_token_incarnation_bits
        ),
        "combined_total_bits": combined_total_bits,
        "combined_total_bytes": bits_to_bytes(combined_total_bits),
    }


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
        offset_entries = (
            int(maa.get("num_offset_table_entries", "0")) or logical
        )
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
    combine_words = integer(maa, "virtual_combine_words")
    response_slots = integer(maa, "virtual_response_slots")
    response_words = integer(maa, "virtual_response_words")
    response_pool = integer(maa, "virtual_response_word_pool")
    shared_result_payload = maa.getboolean(
        "virtual_shared_result_payload", fallback=False
    )
    index_lines = integer(maa, "virtual_index_buffer_lines")
    try:
        index_issue_lines_per_cycle = int(
            maa.get("virtual_index_issue_lines_per_cycle", "1")
        )
    except ValueError:
        fail(
            "invalid system.maa value for virtual_index_issue_lines_per_cycle"
        )
    outstanding_writes = integer(maa, "virtual_max_outstanding_writes")
    native_issue_order = maa.getboolean("virtual_native_issue_order")
    direct_retirement_line_handoff = maa.getboolean(
        "direct_retirement_line_handoff", fallback=False
    )
    dense_write_allocate = maa.getboolean(
        "virtual_dense_write_allocate", fallback=False
    )
    complete_line_only = maa.getboolean(
        "virtual_complete_line_only", fallback=False
    )
    if complete_line_only and (
        combine_words == 0
        or response_pool == 0
        or combine_words + response_pool > physical
    ):
        fail(
            "virtual_complete_line_only requires explicit combiner/response "
            "word pools within physical_tile_elements"
        )
    if shared_result_payload and (
        combine_words == 0
        or response_pool == 0
        or combine_words + response_pool > physical
    ):
        fail(
            "shared virtual-result payload requires explicit nonzero "
            "combiner/response capacities within physical storage"
        )
    if shared_result_payload and logical > SHARED_RESPONSE_MAX_FANOUT_USES:
        fail(
            "shared virtual-result payload supports at most 16384 logical "
            "uses per response word"
        )
    try:
        inactive_masked_retention_entries = int(
            maa.get("inactive_page_masked_fragment_retention_lines", "0")
        )
    except ValueError:
        fail(
            "invalid system.maa value for "
            "inactive_page_masked_fragment_retention_lines"
        )
    if (
        inactive_masked_retention_entries
        not in INACTIVE_MASKED_RETENTION_CAPACITIES
    ):
        fail(
            "inactive_page_masked_fragment_retention_lines must be zero or "
            "one of 512/1024/2048/4096, got "
            f"{inactive_masked_retention_entries}"
        )
    if (
        inactive_masked_retention_entries
        and not direct_retirement_line_handoff
    ):
        fail(
            "inactive masked-fragment retention requires "
            "direct_retirement_line_handoff=true"
        )
    if inactive_masked_retention_entries:
        try:
            inactive_payload_capture_lines = int(
                maa.get("inactive_page_payload_capture_lines", "0")
            )
        except ValueError:
            fail(
                "invalid system.maa value for "
                "inactive_page_payload_capture_lines"
            )
        if inactive_payload_capture_lines != 0:
            fail(
                "inactive full-line payload capture and masked-fragment "
                "retention are mutually exclusive"
            )

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
    if not 1 <= index_lines <= 128:
        fail("virtual_index_buffer_lines must be in [1,128]")
    if index_issue_lines_per_cycle not in (1, 2, 4):
        fail("virtual_index_issue_lines_per_cycle must be 1, 2, or 4")
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
    inactive_masked_retention = inactive_masked_retention_accounting(
        inactive_masked_retention_entries, tiles
    )
    inactive_masked_retention_bytes = inactive_masked_retention[
        "combined_total_bytes"
    ]
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
    index_owner_bits = bits_for_values(logical)
    row_slice_bits = bits_for_values(initial_slices)
    row_id_bits = bits_for_values(rows_per_slice)
    row_entry_bits = bits_for_values(entries_per_row)
    response_count_bits = bits_for_values(words_per_line + 1)
    response_pool_words = response_pool or (
        response_slots * (response_words or words_per_line)
    )
    response_pool_pointer_bits = bits_for_values(response_pool_words + 1)
    effective_combine_words = combine_words or (combine_slots * words_per_line)
    shared_result_allocator_words = (
        effective_combine_words + response_pool if shared_result_payload else 0
    )
    combine_allocator_words = (
        shared_result_allocator_words
        if shared_result_payload
        else effective_combine_words
    )
    combine_reference_bits = bits_for_values(combine_allocator_words)
    shared_response_word_count = SHARED_RESPONSE_MAX_LOGICAL_WORDS
    shared_response_word_reference_bits_per_slot = (
        shared_response_word_count
        * bits_for_values(shared_result_allocator_words)
        if shared_result_payload
        else 0
    )
    shared_response_fanout_counter_bits_per_slot = (
        shared_response_word_count
        * bits_for_values(SHARED_RESPONSE_MAX_FANOUT_USES + 1)
        if shared_result_payload
        else 0
    )

    index_line_metadata_bits = (
        args.address_bits  # physical response-match tag
        + 2  # empty, pending, or ready
        + index_words_per_line  # reserved/owned words while pending or ready
        + index_words_per_line  # response payload-valid words
        + index_words_per_line * index_owner_bits
    )
    index_global_control_bits = (
        bits_for_values(index_lines + 1)  # occupied-line count
        + bits_for_values(5)  # current-cycle issue count, width <= 4
        + 32  # phase shared by all live lines; phase advances only when empty
    )
    index_metadata_bits_per_unit = (
        index_lines * index_line_metadata_bits + index_global_control_bits
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
        + shared_response_word_reference_bits_per_slot
        + shared_response_fanout_counter_bits_per_slot
    )
    combine_metadata_bits_per_unit = combine_slots * (
        1
        + args.address_bits
        + words_per_line
        + words_per_line * combine_reference_bits
    )
    combine_allocator_bits_per_unit = combine_allocator_words * (
        1 + combine_reference_bits
    ) + bits_for_values(combine_allocator_words + 1)
    combine_sets = (
        1
        if integer(maa, "virtual_combine_ways") == 0
        else (combine_slots // integer(maa, "virtual_combine_ways"))
    )
    combine_ways = integer(maa, "virtual_combine_ways") or combine_slots
    combine_replacement_bits_per_unit = combine_sets * bits_for_values(
        combine_ways
    )
    combine_global_payload_victim_bits_per_unit = (
        bits_for_values(combine_slots) if effective_combine_words else 0
    )
    virtual_retirement_metadata_bytes_per_entry = 44
    virtual_retirement_identity_allocator_bytes_per_unit = 8
    virtual_retirement_scoreboard_bytes_per_unit = (
        outstanding_writes * virtual_retirement_metadata_bytes_per_entry
        + virtual_retirement_identity_allocator_bytes_per_unit
    )
    outstanding_write_bits_per_unit = (
        virtual_retirement_scoreboard_bytes_per_unit * 8
    )
    page_counter_bits_per_unit = virtual_pages_used * (5 * iteration_bits + 1)
    dense_backing_line_bits_per_unit = (
        math.ceil(logical * args.word_bytes / 64)
        if dense_write_allocate and args.mechanism != "native"
        else 0
    )
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
            combine_metadata_bits_per_unit
            + combine_allocator_bits_per_unit
            + combine_replacement_bits_per_unit
            + combine_global_payload_victim_bits_per_unit
        )
        active_write_metadata_bits = outstanding_write_bits_per_unit
        active_page_counter_bits = page_counter_bits_per_unit
        active_claim_bits = native_claim_bits_per_unit
        active_completion_increment_bits = completion_increment_bits
    shared_spill_bitmap_bits_per_unit = (
        math.ceil(logical * args.word_bytes / 64)
        if shared_result_payload and args.mechanism != "native"
        else 0
    )
    shared_spill_bitmap_bytes_per_unit = math.ceil(
        shared_spill_bitmap_bits_per_unit / 8
    )
    virtual_control_bits_per_unit = (
        active_index_metadata_bits
        + active_response_metadata_bits
        + active_combine_metadata_bits
        + active_write_metadata_bits
        + active_page_counter_bits
        + active_claim_bits
        + dense_backing_line_bits_per_unit
        + (1 if complete_line_only and args.mechanism != "native" else 0)
        + shared_spill_bitmap_bits_per_unit
    )
    virtual_control_bytes_per_unit = math.ceil(
        virtual_control_bits_per_unit / 8
    )
    completion_increment_bytes = math.ceil(
        active_completion_increment_bits / 8
    )

    combine_payload_per_unit = effective_combine_words * args.word_bytes
    if shared_result_payload:
        response_storage_mode = "shared-result-allocator"
        response_payload_per_unit = 0
        shared_result_payload_per_unit = (
            shared_result_allocator_words * args.word_bytes
        )
    elif response_pool:
        response_storage_mode = (
            "shared-packed-word-pool"
            if shared_result_payload
            else "packed-word-pool"
        )
        response_payload_per_unit = response_pool * args.word_bytes
        shared_result_payload_per_unit = 0
    elif response_words:
        response_storage_mode = "packed-words-per-slot"
        response_payload_per_unit = (
            response_slots * response_words * args.word_bytes
        )
        shared_result_payload_per_unit = 0
    else:
        response_storage_mode = "unpacked-fixed-lines"
        response_payload_per_unit = response_slots * 64
        shared_result_payload_per_unit = 0
    index_payload_per_unit = index_lines * 64
    configured_virtual_payload_per_unit = (
        (
            shared_result_payload_per_unit
            if shared_result_payload
            else combine_payload_per_unit
        )
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
        active_combine_payload = (
            shared_result_payload_per_unit
            if shared_result_payload
            else combine_payload_per_unit
        )
    else:
        active_index_payload = 0
        active_response_payload = response_payload_per_unit
        active_combine_payload = (
            shared_result_payload_per_unit
            if shared_result_payload
            else combine_payload_per_unit
        )
    active_virtual_payload_per_unit = (
        active_index_payload + active_response_payload + active_combine_payload
    )
    active_virtual_payload_total = (
        active_virtual_payload_per_unit * indirect_units
    )
    # Shared mode deliberately selects the unpacked C++ response-line store,
    # even though its line payload is a simulator-only functional shadow and
    # not part of the packed hardware allocator modeled above.
    excluded_cpp_response_line_shadow_bytes_per_unit = (
        response_slots * 64 if shared_result_payload else 0
    )
    excluded_cpp_response_line_shadow_bytes_total = (
        excluded_cpp_response_line_shadow_bytes_per_unit * indirect_units
    )

    # Direct retirement owns a second, finite set of credits only when the
    # line-handoff path is enabled.  Keep this separate from the indirect
    # virtual buffers above: none of these terms recharges their index,
    # response, combine, or outstanding-write payloads.
    #
    # The C++ constants are verified by the focused charge helpers/unit
    # output on the 64-bit ABI: queue payload/control = 4096/17728 B,
    # four retry pointers = 32 B, and the early-line ledger = 1696 B.
    # Execution and request-record footprints are direct sizeof() charges in
    # MAA.cc; their 456-B and 72-B record sizes are deliberately kept in the
    # conservative implementation view rather than claimed as hardware.
    direct_contexts = 4
    direct_request_records = 64
    direct_queue_payload_bytes = 4096
    direct_queue_control_bytes = 17728
    direct_execution_record_cpp_bytes = 456
    direct_request_record_cpp_bytes = 72
    direct_retry_slots = 4
    direct_retry_slot_cpp_bytes = 8
    direct_retry_cpp_bytes = direct_retry_slots * direct_retry_slot_cpp_bytes
    direct_early_line_ledger_bytes = 1696
    direct_cpp_static_bytes = (
        direct_queue_payload_bytes
        + direct_queue_control_bytes
        + direct_contexts * direct_execution_record_cpp_bytes
        + direct_request_records * direct_request_record_cpp_bytes
        + direct_retry_cpp_bytes
        + direct_early_line_ledger_bytes
    )
    # Densely packed semantic floor.  It intentionally does not infer an RTL
    # encoding for queue scheduler/control state; that state is shown in the
    # C++ static view instead.
    direct_execution_record_hardware_bytes = 112
    direct_request_record_hardware_bytes = 50
    direct_hardware_lower_bound_bytes = (
        direct_queue_payload_bytes
        + direct_contexts * direct_execution_record_hardware_bytes
        + direct_request_records * direct_request_record_hardware_bytes
        + direct_retry_cpp_bytes
        + direct_early_line_ledger_bytes
    )
    if not direct_retirement_line_handoff:
        direct_cpp_static_bytes = 0
        direct_hardware_lower_bound_bytes = 0
    counted_payload = physical_spd_bytes + active_virtual_payload_total
    bounded_state_total = (
        counted_payload
        + virtual_control_bytes_per_unit * indirect_units
        + completion_increment_bytes
        + direct_hardware_lower_bound_bytes
        + inactive_masked_retention_bytes
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
        bounded_state_total
        + direct_cpp_static_bytes
        - direct_hardware_lower_bound_bytes
    )
    conservative_cpp_static_comparable_storage = (
        comparable_storage
        + direct_cpp_static_bytes
        - direct_hardware_lower_bound_bytes
    )
    conservative_cpp_static_allocated_storage = (
        allocated_comparable_storage
        + direct_cpp_static_bytes
        - direct_hardware_lower_bound_bytes
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
            "direct_retirement_line_handoff": direct_retirement_line_handoff,
            "virtual_dense_write_allocate": dense_write_allocate,
            "virtual_complete_line_only": complete_line_only,
            "inactive_page_masked_fragment_retention_lines": (
                inactive_masked_retention_entries
            ),
            "virtual_index_buffer_lines": index_lines,
            "virtual_index_issue_lines_per_cycle": (
                index_issue_lines_per_cycle
            ),
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
            "shared_result_payload": shared_result_payload,
            "shared_pressure_spill_bitmap_bits_per_indirect_unit": (
                shared_spill_bitmap_bits_per_unit
            ),
            "shared_pressure_spill_bitmap_bytes_per_indirect_unit": (
                shared_spill_bitmap_bytes_per_unit
            ),
            "shared_result_payload_words_per_indirect_unit": (
                shared_result_allocator_words
            ),
            "shared_result_allocator_bytes_per_indirect_unit": (
                shared_result_payload_per_unit
            ),
            "source_response_storage_mode": response_storage_mode,
            "source_response_slots_per_indirect_unit": response_slots,
            "unpacked_line_bytes_per_slot": (
                64 if response_storage_mode == "unpacked-fixed-lines" else 0
            ),
            "packed_word_bytes": args.word_bytes,
            "packed_words_per_slot": response_words,
            "packed_word_pool_per_indirect_unit": response_pool,
            "configured_index_feeder_bytes_per_indirect_unit": (
                index_payload_per_unit
            ),
            "configured_source_response_bytes_per_indirect_unit": (
                response_payload_per_unit
            ),
            "configured_source_response_bytes_all_indirect_units": (
                response_payload_per_unit * indirect_units
            ),
            "configured_destination_combiner_bytes_per_indirect_unit": (
                shared_result_payload_per_unit
                if shared_result_payload
                else combine_payload_per_unit
            ),
            "destination_combiner_line_tags_per_indirect_unit": (
                combine_slots
            ),
            "destination_combiner_word_pool_per_indirect_unit": (
                combine_allocator_words
            ),
            "destination_combiner_reference_bits": combine_reference_bits,
            "configured_total_bytes_per_indirect_unit": (
                configured_virtual_payload_per_unit
            ),
            "active_index_feeder_bytes_per_indirect_unit": (
                active_index_payload
            ),
            "active_source_response_bytes_per_indirect_unit": (
                active_response_payload
            ),
            "active_source_response_bytes_all_indirect_units": (
                active_response_payload * indirect_units
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
            "excluded_cpp_response_line_shadow_bytes_per_indirect_unit": (
                excluded_cpp_response_line_shadow_bytes_per_unit
            ),
            "excluded_cpp_response_line_shadow_bytes_all_indirect_units": (
                excluded_cpp_response_line_shadow_bytes_total
            ),
            "excluded_cpp_response_line_shadow_note": (
                "Shared-result mode configures one unpacked 64-byte C++ "
                "response line per slot as simulator-only functional shadow; "
                "it is excluded from the modeled hardware allocator."
            ),
        },
        "incremental_virtual_control_lower_bound": {
            "source_response_metadata_bits_per_slot": (
                active_response_metadata_bits // response_slots
            ),
            "index_feeder_metadata_bits_per_indirect_unit": (
                active_index_metadata_bits
            ),
            "index_feeder_metadata_bits_per_line": (
                index_line_metadata_bits
                if args.mechanism == "direct-index"
                else 0
            ),
            "index_feeder_global_control_bits_per_indirect_unit": (
                index_global_control_bits
                if args.mechanism == "direct-index"
                else 0
            ),
            "index_feeder_logical_owner_bits_per_word": (
                index_owner_bits if args.mechanism == "direct-index" else 0
            ),
            "index_feeder_supported_capacity_lines": (
                128 if args.mechanism == "direct-index" else 0
            ),
            "index_feeder_fixed_max_payload_bits": (
                128 * 64 * 8 if args.mechanism == "direct-index" else 0
            ),
            "index_feeder_fixed_max_control_bits": (
                128 * index_line_metadata_bits
                + bits_for_values(128 + 1)
                + bits_for_values(5)
                + 32
                if args.mechanism == "direct-index"
                else 0
            ),
            "index_feeder_packed_accounting_note": (
                "Semantic packed bits for fixed line payload/tag/state, "
                "per-word reservation/validity/ownership, shared phase, and "
                "finite issue control; excludes C++ sizeof and host padding."
            ),
            "source_response_metadata_bits_per_indirect_unit": (
                active_response_metadata_bits
            ),
            "shared_response_word_reference_bits_per_slot": (
                shared_response_word_reference_bits_per_slot
                if args.mechanism != "native"
                else 0
            ),
            "shared_response_word_reference_bits_per_indirect_unit": (
                response_slots * shared_response_word_reference_bits_per_slot
                if args.mechanism != "native"
                else 0
            ),
            "shared_response_fanout_counter_bits_per_slot": (
                shared_response_fanout_counter_bits_per_slot
                if args.mechanism != "native"
                else 0
            ),
            "shared_response_fanout_counter_bits_per_indirect_unit": (
                response_slots * shared_response_fanout_counter_bits_per_slot
                if args.mechanism != "native"
                else 0
            ),
            "shared_response_fanout_max_uses_per_word": (
                SHARED_RESPONSE_MAX_FANOUT_USES if shared_result_payload else 0
            ),
            "destination_combiner_metadata_bits_per_indirect_unit": (
                active_combine_metadata_bits
            ),
            "destination_combiner_allocator_bits_per_indirect_unit": (
                combine_allocator_bits_per_unit
                if args.mechanism != "native"
                else 0
            ),
            (
                "destination_combiner_global_payload_"
                "victim_bits_per_indirect_unit"
            ): (
                combine_global_payload_victim_bits_per_unit
                if args.mechanism != "native"
                else 0
            ),
            "outstanding_write_metadata_bits_per_indirect_unit": (
                active_write_metadata_bits
            ),
            "dense_backing_initialized_bits_per_indirect_unit": (
                dense_backing_line_bits_per_unit
            ),
            "dense_backing_initialized_fixed_max_bits_per_indirect_unit": (
                2048 if dense_write_allocate else 0
            ),
            "complete_line_only_control_bits_per_indirect_unit": (
                1 if complete_line_only and args.mechanism != "native" else 0
            ),
            "virtual_retirement_metadata_bytes_per_entry": (
                virtual_retirement_metadata_bytes_per_entry
                if args.mechanism != "native"
                else 0
            ),
            "virtual_retirement_identity_allocator_bytes_per_unit": (
                virtual_retirement_identity_allocator_bytes_per_unit
                if args.mechanism != "native"
                else 0
            ),
            "virtual_retirement_scoreboard_bytes_per_indirect_unit": (
                virtual_retirement_scoreboard_bytes_per_unit
                if args.mechanism != "native"
                else 0
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
        "direct_retirement_line_handoff_state": {
            "enabled": direct_retirement_line_handoff,
            "scope": (
                "additional bounded direct-retirement state only; excludes "
                "all indirect virtual index/response/combine buffers already "
                "charged elsewhere in this report"
            ),
            "hardware_lower_bound": {
                "queue_line_payload_bytes": (
                    direct_queue_payload_bytes
                    if direct_retirement_line_handoff
                    else 0
                ),
                "execution_records": direct_contexts
                if direct_retirement_line_handoff
                else 0,
                "packed_execution_bytes_per_record": (
                    direct_execution_record_hardware_bytes
                    if direct_retirement_line_handoff
                    else 0
                ),
                "request_records": (
                    direct_request_records
                    if direct_retirement_line_handoff
                    else 0
                ),
                "packed_request_bytes_per_record": (
                    direct_request_record_hardware_bytes
                    if direct_retirement_line_handoff
                    else 0
                ),
                "per_port_retry_slots": (
                    direct_retry_slots if direct_retirement_line_handoff else 0
                ),
                "retry_slot_bytes_64_bit_abi": (
                    direct_retry_slot_cpp_bytes
                    if direct_retirement_line_handoff
                    else 0
                ),
                "early_line_ledger_bytes": (
                    direct_early_line_ledger_bytes
                    if direct_retirement_line_handoff
                    else 0
                ),
                "producer_line_metadata_bytes_per_entry": 0,
                "producer_identity_allocator_bytes": 0,
                "producer_line_metadata_bytes": 0,
                "shared_virtual_retirement_scoreboard_bytes": (
                    virtual_retirement_scoreboard_bytes_per_unit
                    * indirect_units
                    if direct_retirement_line_handoff
                    else 0
                ),
                "total_bytes": direct_hardware_lower_bound_bytes,
                "excludes": (
                    "queue scheduler/control encoding and C++ object padding; "
                    "this is a dense semantic lower bound, not an RTL area estimate"
                ),
            },
            "conservative_cpp_static_view": {
                "queue_payload_bytes": (
                    direct_queue_payload_bytes
                    if direct_retirement_line_handoff
                    else 0
                ),
                "queue_control_bytes": (
                    direct_queue_control_bytes
                    if direct_retirement_line_handoff
                    else 0
                ),
                "execution_records": direct_contexts
                if direct_retirement_line_handoff
                else 0,
                "execution_bytes_per_record": (
                    direct_execution_record_cpp_bytes
                    if direct_retirement_line_handoff
                    else 0
                ),
                "request_records": (
                    direct_request_records
                    if direct_retirement_line_handoff
                    else 0
                ),
                "request_bytes_per_record": (
                    direct_request_record_cpp_bytes
                    if direct_retirement_line_handoff
                    else 0
                ),
                "per_port_retry_slots": (
                    direct_retry_slots if direct_retirement_line_handoff else 0
                ),
                "retry_slot_bytes_64_bit_abi": (
                    direct_retry_slot_cpp_bytes
                    if direct_retirement_line_handoff
                    else 0
                ),
                "retry_slots_bytes": direct_retry_cpp_bytes
                if direct_retirement_line_handoff
                else 0,
                "early_line_ledger_bytes": (
                    direct_early_line_ledger_bytes
                    if direct_retirement_line_handoff
                    else 0
                ),
                "producer_line_metadata_bytes_per_entry": 0,
                "producer_identity_allocator_bytes": 0,
                "producer_line_metadata_bytes": 0,
                "shared_virtual_retirement_scoreboard_bytes": (
                    virtual_retirement_scoreboard_bytes_per_unit
                    * indirect_units
                    if direct_retirement_line_handoff
                    else 0
                ),
                "total_bytes": direct_cpp_static_bytes,
                "assumptions": (
                    "C++ static 64-bit ABI view from the fixed MAA.cc arrays; "
                    "still excludes STL/container and allocator overhead"
                ),
            },
        },
        "inactive_masked_fragment_retention_state": {
            "enabled": inactive_masked_retention_entries != 0,
            "scope": (
                "one MAA-shared packed hardware charge; includes the shared "
                "lookup pipeline, exact four-slot fallback-rebind table, and "
                "one persistent 64-bit incarnation per token tile"
            ),
            "descriptor_partitions": (
                INACTIVE_MASKED_DESCRIPTOR_COUNT
                if inactive_masked_retention_entries
                else 0
            ),
            "write_banks": (
                INACTIVE_MASKED_BANK_COUNT
                if inactive_masked_retention_entries
                else 0
            ),
            "token_tiles": tiles if inactive_masked_retention_entries else 0,
            "packed_hardware_accounting": inactive_masked_retention,
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
                "active payload plus fixed-width lower bounds; packed-word "
                "STL/container and allocator overhead remains excluded"
            ),
            "excluded_simulator_only_response_line_shadow_bytes": (
                excluded_cpp_response_line_shadow_bytes_total
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
            "indirect virtual index/response/combine payload already charged above",
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
        "| Excluded simulator-only C++ response-line shadow | "
        f"{format_bytes(excluded_cpp_response_line_shadow_bytes_per_unit)} / indirect unit |",
        f"| Active destination-combiner payload | {format_bytes(active_combine_payload)} / indirect unit |",
        "| Incremental virtual tags/control (lower bound) | "
        f"{format_bytes(virtual_control_bytes_per_unit)} / indirect unit |",
        "| Incremental completion state | "
        f"{format_bytes(completion_increment_bytes)} |",
        "| Direct-retirement handoff hardware lower bound | "
        f"{format_bytes(direct_hardware_lower_bound_bytes)} |",
        "| Direct-retirement handoff conservative C++ static view | "
        f"{format_bytes(direct_cpp_static_bytes)} |",
        "| Inactive masked-fragment retention packed hardware | "
        f"{format_bytes(inactive_masked_retention_bytes)} |",
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
        "Conservative C++ static-view reduction (excluding simulator-only "
        "shared response-line shadows): "
        "**"
        f"{report['conservative_cpp_static_storage_view']['comparable_reduction_vs_native_pct']:.3f}%"
        "**.",
        "",
        "This is a capacity ledger, not an area estimate. The comparable lower bound",
        "includes retained logical-sized Row/Offset/invalidator state and bit-packed",
        "readiness, but this does not prove native-equivalent descriptor lifetime or",
        "issue order. Essential tags and bounded control arrays are included as a",
        "bit-count lower bound; ports, arbitration, wiring, and memory periphery are",
        "still excluded. Shared-result mode separately reports its unpacked C++",
        "response-line shadow as simulator-only state excluded from the hardware",
        "and conservative views; when direct_retirement_line_handoff=true it additionally counts",
        "the fixed direct-retirement queue, execution records, request records, retry",
        "slots, early-line ledger, and producer-line metadata. The direct handoff",
        "views deliberately exclude indirect virtual buffers already charged above.",
        "Enabled inactive masked-fragment retention is charged once as one shared",
        "packed structure, including its lookup/fallback control and persistent",
        "per-token incarnation state; the default capacity of zero charges nothing.",
    ]
    (output / "maa_storage.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    (output / "maa_storage.pass").touch()
    print(f"PASS MAA storage ledger: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
