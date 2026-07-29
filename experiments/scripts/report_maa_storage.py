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
        "--dram-subslices",
        type=int,
        required=True,
        help="channel x rank x bank-group x bank count",
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
    maas = integer(maa, "num_maas")
    indirect_per_maa = integer(maa, "num_indirect_units_per_maa")
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

    positive = {
        "num_cores": cores,
        "num_tiles_per_core": tiles_per_core,
        "num_tile_elements": logical,
        "physical_tile_elements": physical,
        "num_maas": maas,
        "num_indirect_units_per_maa": indirect_per_maa,
        "num_initial_row_table_slices": initial_slices,
        "num_row_table_rows_per_slice": rows_per_slice,
        "num_row_table_entries_per_subslice_row": entries_per_subslice_row,
        "dram_subslices": args.dram_subslices,
    }
    for name, value in positive.items():
        if value <= 0:
            fail(f"{name} must be positive, got {value}")
    if physical > logical or logical % physical:
        fail(
            "physical tile capacity must divide and not exceed logical capacity"
        )
    if args.dram_subslices % initial_slices:
        fail(
            "DRAM subslices must divide evenly across initial Row-Table slices"
        )

    tiles = cores * tiles_per_core
    indirect_units = maas * indirect_per_maa
    native_spd_bytes = tiles * logical * 4
    physical_spd_bytes = tiles * physical * 4
    logical_aperture_bytes = native_spd_bytes
    unbacked_aperture_tail_bytes = tiles * (logical - physical) * 4
    invalidator_entries = logical_aperture_bytes // 64
    virtual_pages_used = logical // physical

    # C++ stores one 12-byte entry plus a byte-valid array entry per iteration.
    offset_model_bytes_per_unit = logical * 13
    iteration_bits = bits_for_values(logical + 1)
    word_id_bits = bits_for_values(64 // args.word_bytes)
    offset_lower_bits = logical * (iteration_bits + word_id_bits + 1)
    offset_lower_bytes_per_unit = math.ceil(offset_lower_bits / 8)

    entries_per_row = entries_per_subslice_row * (
        args.dram_subslices // initial_slices
    )
    active_row_entries = initial_slices * rows_per_slice * entries_per_row
    row_entry_lower_bits = 64 + 2 * iteration_bits + 1
    row_lower_bytes_per_unit = math.ceil(
        active_row_entries * row_entry_lower_bits / 8
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
    counted_payload = physical_spd_bytes + active_virtual_payload_total

    report = {
        "provenance": {
            "config": str(config_path),
            "config_sha256": sha256(config_path),
            "mechanism": args.mechanism,
            "word_bytes": args.word_bytes,
            "dram_subslices": args.dram_subslices,
        },
        "configuration": {
            "cores": cores,
            "tiles_per_core": tiles_per_core,
            "tiles_total": tiles,
            "logical_elements_per_tile": logical,
            "physical_elements_per_tile": physical,
            "virtual_pages_used": virtual_pages_used,
            "indirect_units": indirect_units,
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
            "completion_flags_used": tiles * virtual_pages_used,
            "completion_cpp_model_bytes_fixed_16_pages": tiles * 16,
            "offset_entries_per_indirect_unit": logical,
            "offset_cpp_model_bytes_per_indirect_unit": (
                offset_model_bytes_per_unit
            ),
            "offset_encoding_lower_bound_bytes_per_indirect_unit": (
                offset_lower_bytes_per_unit
            ),
            "configured_row_entry_capacity_per_indirect_unit": (
                active_row_entries
            ),
            "row_encoding_lower_bound_bytes_per_indirect_unit": (
                row_lower_bytes_per_unit
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
        },
        "counted_payload": {
            "physical_spd_plus_virtual_buffers_bytes": counted_payload,
            "reduction_vs_native_spd_pct": (
                1 - counted_payload / native_spd_bytes
            )
            * 100,
        },
        "excluded_from_counted_payload": [
            "Row/Offset metadata (reported separately and retained at logical size)",
            "tags, masks, valid bits, queues, maps, and arbitration",
            "cache tags, MSHRs, routing state, and outstanding packet payload",
            "ports, wiring, control, and synthesized memory periphery",
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
        f"| Active destination-combiner payload | {format_bytes(active_combine_payload)} / indirect unit |",
        f"| Physical SPD + bounded virtual payload | {format_bytes(counted_payload)} |",
        f"| Logical Offset entries retained | {logical:,} / indirect unit |",
        f"| Configured Row-Table entry capacity | {active_row_entries:,} / indirect unit |",
        f"| Logical invalidator entries retained | {invalidator_entries:,} |",
        f"| Unbacked logical SPD tail | {format_bytes(unbacked_aperture_tail_bytes)} / address aperture |",
        "",
        f"Counted payload reduction versus native SPD: **{report['counted_payload']['reduction_vs_native_spd_pct']:.3f}%**.",
        "",
        "This is a capacity ledger, not an area estimate. Row/Offset metadata remains",
        "logical-sized, but this does not prove native-equivalent descriptor lifetime or",
        "issue order. Tags, queues, ports, control, wiring, and memory periphery are",
        "excluded from the counted payload.",
    ]
    (output / "maa_storage.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    (output / "maa_storage.pass").touch()
    print(f"PASS MAA storage ledger: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
