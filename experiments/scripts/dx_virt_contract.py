#!/usr/bin/env python3
"""Emit a fail-closed storage and dataflow contract for virtual gather runs."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import tempfile
from pathlib import Path

SCHEMA_VERSION = 1
LINE_BYTES = 64
SPD_WORD_BYTES = 4
COMBINE_ENTRY_BYTES = 72
RESPONSE_ID_BYTES = 8
RESPONSE_WORD_BYTES = 8
WRITE_TAG_BYTES = 8

DEFAULTS = {
    "num_cores": 4,
    "num_tiles_per_core": 8,
    "num_tile_elements": 16384,
    "physical_tile_elements": 0,
    "num_initial_row_table_slices": 32,
    "num_row_table_rows_per_slice": 64,
    "num_row_table_entries_per_subslice_row": 8,
    "virtual_combine_slots": 16,
    "virtual_combine_words": 256,
    "virtual_combine_ways": 0,
    "virtual_combine_banks": 0,
    "virtual_response_slots": 8,
    "virtual_response_word_pool": 128,
    "virtual_words_per_cycle": 1,
    "virtual_max_outstanding_writes": 8,
    "virtual_index_buffer_lines": 1,
}

INTEGER_KEYS = frozenset(DEFAULTS)


class ContractError(ValueError):
    pass


def parse_int(value: str, key: str) -> int:
    try:
        return int(value, 0)
    except ValueError as exc:
        raise ContractError(
            f"{key} must be an integer, got {value!r}"
        ) from exc


def find_maa_section(parser: configparser.ConfigParser) -> str:
    matches = [
        section
        for section in parser.sections()
        if parser.has_option(section, "num_tile_elements")
        and parser.has_option(section, "num_tiles_per_core")
    ]
    if len(matches) != 1:
        raise ContractError(
            "config must contain exactly one MAA section with "
            "num_tile_elements and num_tiles_per_core; found "
            f"{matches}"
        )
    return matches[0]


def load_configuration(path: Path | None) -> tuple[dict[str, int], dict]:
    values = dict(DEFAULTS)
    source = {
        "kind": "defaults",
        "path": None,
        "sha256": None,
        "section": None,
    }
    if path is None:
        return values, source

    path = path.resolve(strict=True)
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    with path.open("r", encoding="utf-8") as handle:
        parser.read_file(handle)
    section = find_maa_section(parser)
    for key in INTEGER_KEYS:
        if parser.has_option(section, key):
            values[key] = parse_int(parser.get(section, key), key)
    source = {
        "kind": "gem5_config_ini",
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "section": section,
    }
    return values, source


def apply_overrides(values: dict[str, int], overrides: list[str]) -> None:
    for item in overrides:
        if "=" not in item:
            raise ContractError(f"override must be KEY=VALUE, got {item!r}")
        key, raw = item.split("=", 1)
        if key not in INTEGER_KEYS:
            raise ContractError(f"unsupported override key {key!r}")
        values[key] = parse_int(raw, key)


def validate(values: dict[str, int], effective_entries: int | None) -> None:
    positive = INTEGER_KEYS - {
        "physical_tile_elements",
        "virtual_combine_ways",
        "virtual_combine_banks",
    }
    for key in sorted(positive):
        if values[key] <= 0:
            raise ContractError(f"{key} must be positive")
    for key in (
        "physical_tile_elements",
        "virtual_combine_ways",
        "virtual_combine_banks",
    ):
        if values[key] < 0:
            raise ContractError(f"{key} must be non-negative")

    logical = values["num_tile_elements"]
    physical = values["physical_tile_elements"] or logical
    if physical > logical:
        raise ContractError("physical_tile_elements exceeds num_tile_elements")
    slots = values["virtual_combine_slots"]
    ways = values["virtual_combine_ways"]
    if ways > slots:
        raise ContractError(
            "virtual_combine_ways exceeds virtual_combine_slots"
        )
    if ways and slots % ways:
        raise ContractError(
            "virtual_combine_slots must be divisible by virtual_combine_ways"
        )
    if effective_entries is not None and effective_entries <= 0:
        raise ContractError(
            "row-table effective entries per row must be positive"
        )


def build_contract(
    values: dict[str, int],
    source: dict,
    effective_entries_per_row: int | None,
) -> dict:
    validate(values, effective_entries_per_row)
    logical = values["num_tile_elements"]
    physical = values["physical_tile_elements"] or logical
    tiles = values["num_cores"] * values["num_tiles_per_core"]

    native_spd = tiles * logical * SPD_WORD_BYTES
    physical_spd = tiles * physical * SPD_WORD_BYTES
    native_completion = (tiles * logical + 7) // 8
    physical_completion = (tiles * physical + 7) // 8

    combine = values["virtual_combine_slots"] * COMBINE_ENTRY_BYTES
    responses = values["virtual_response_slots"] * RESPONSE_ID_BYTES
    response_words = values["virtual_response_word_pool"] * RESPONSE_WORD_BYTES
    write_tags = values["virtual_max_outstanding_writes"] * WRITE_TAG_BYTES
    write_payload = values["virtual_max_outstanding_writes"] * LINE_BYTES
    retirement = (
        combine + responses + response_words + write_tags + write_payload
    )
    index_payload = values["virtual_index_buffer_lines"] * LINE_BYTES

    native_total = native_spd + native_completion
    virtual_total = (
        physical_spd + physical_completion + retirement + index_payload
    )
    reduction = native_total - virtual_total

    configured_entries = values["num_row_table_entries_per_subslice_row"]
    entries_per_row = effective_entries_per_row or configured_entries
    capacity = (
        values["num_initial_row_table_slices"]
        * values["num_row_table_rows_per_slice"]
        * entries_per_row
    )
    capacity_kind = (
        "exact_user_supplied"
        if effective_entries_per_row
        else "configured_lower_bound"
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "configuration": {
            **values,
            "resolved_physical_tile_elements": physical,
            "total_tiles": tiles,
            "spd_word_bytes": SPD_WORD_BYTES,
            "cache_line_bytes": LINE_BYTES,
        },
        "dataflow": {
            "native": [
                "stream B/index memory into a complete SPD index tile",
                "insert derived A cache-line descriptors into Row/Word Tables",
                "issue A reads in Row-Table order",
                "write returned words into a distinct SPD destination tile",
            ],
            "compact_spd_index": [
                "retain the complete SPD B/index tile",
                "retain native Row/Word-Table scheduling",
                "combine returned words and retire them to backing memory",
            ],
            "direct_index_virtual": [
                "stream B cache lines through the bounded index feeder",
                "retain derived A descriptors in native Row/Word Tables",
                "combine returned words and retire them to backing memory",
                "use the destination tile ID only as a completion token",
            ],
        },
        "storage_bytes": {
            "native_logical": {
                "spd_payload": native_spd,
                "bitpacked_completion_target": native_completion,
                "counted_total": native_total,
            },
            "configured_virtual": {
                "spd_payload": physical_spd,
                "bitpacked_completion_target": physical_completion,
                "virtual_retirement": {
                    "combine_entries": combine,
                    "response_identities": responses,
                    "response_word_pool": response_words,
                    "write_tags": write_tags,
                    "retained_write_payload": write_payload,
                    "total": retirement,
                },
                "direct_index_payload": index_payload,
                "counted_total": virtual_total,
            },
            "reduction_vs_native": {
                "bytes": reduction,
                "percent": reduction * 100.0 / native_total,
            },
        },
        "reorder_contract": {
            "logical_iterations": logical,
            "direct_index_feeder_lines": values["virtual_index_buffer_lines"],
            "row_table_descriptor_capacity": capacity,
            "capacity_kind": capacity_kind,
            "effective_entries_per_row": entries_per_row,
            "preservation_claim": (
                "native-equivalent Row/Word-Table scheduling until capacity forces a drain"
            ),
            "not_claimed": "all logical iterations are simultaneously resident for every address distribution",
        },
        "exclusions": [
            "Row/Word Tables retained by both compared designs",
            "cache and buffer tags beyond explicit byte allowances",
            "ports, arbitration, routing, control, and physical design",
            "area, frequency, power, and energy",
            "support for non-direct-gather producer/consumer chains",
        ],
    }


def markdown(contract: dict) -> str:
    cfg = contract["configuration"]
    storage = contract["storage_bytes"]
    reorder = contract["reorder_contract"]
    retirement = storage["configured_virtual"]["virtual_retirement"]
    lines = [
        "# Virtual Gather Contract",
        "",
        "## Configuration",
        "",
        f"- Logical tile: {cfg['num_tile_elements']:,} elements.",
        f"- Physical tile: {cfg['resolved_physical_tile_elements']:,} elements.",
        f"- Scratchpad tiles: {cfg['total_tiles']}.",
        f"- Direct-index feeder: {reorder['direct_index_feeder_lines']} cache lines.",
        f"- Row-Table descriptor capacity ({reorder['capacity_kind']}): "
        f"{reorder['row_table_descriptor_capacity']:,}.",
        "",
        "## Counted Storage",
        "",
        "| Component | Bytes |",
        "|---|---:|",
        f"| Native logical SPD payload | {storage['native_logical']['spd_payload']:,} |",
        f"| Configured physical SPD payload | {storage['configured_virtual']['spd_payload']:,} |",
        f"| Configured completion-state target | {storage['configured_virtual']['bitpacked_completion_target']:,} |",
        f"| Virtual combine entries | {retirement['combine_entries']:,} |",
        f"| Virtual response identities | {retirement['response_identities']:,} |",
        f"| Virtual response-word pool | {retirement['response_word_pool']:,} |",
        f"| Virtual write tags | {retirement['write_tags']:,} |",
        f"| Retained write payload | {retirement['retained_write_payload']:,} |",
        f"| Direct-index payload | {storage['configured_virtual']['direct_index_payload']:,} |",
        f"| **Configured virtual counted total** | **{storage['configured_virtual']['counted_total']:,}** |",
        "",
        f"Counted reduction versus native: {storage['reduction_vs_native']['bytes']:,} bytes "
        f"({storage['reduction_vs_native']['percent']:.2f}%).",
        "",
        "## Reordering Claim",
        "",
        reorder["preservation_claim"] + ".",
        "",
        "Not claimed: " + reorder["not_claimed"] + ".",
        "",
        "## Exclusions",
        "",
    ]
    lines.extend(f"- {item}." for item in contract["exclusions"])
    return "\n".join(lines) + "\n"


def atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="gem5 config.ini")
    parser.add_argument(
        "--set", action="append", default=[], metavar="KEY=VALUE"
    )
    parser.add_argument(
        "--row-table-effective-entries-per-row",
        type=int,
        help="supply the derived entries/row when one slice covers multiple subslices",
    )
    parser.add_argument("--json", type=Path, dest="json_path")
    parser.add_argument("--markdown", type=Path, dest="markdown_path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        values, source = load_configuration(args.config)
        apply_overrides(values, args.set)
        contract = build_contract(
            values, source, args.row_table_effective_entries_per_row
        )
    except (ContractError, OSError, configparser.Error) as exc:
        raise SystemExit(f"dx-virt-contract: {exc}") from exc

    json_text = json.dumps(contract, indent=2, sort_keys=True) + "\n"
    if args.json_path:
        atomic_write(args.json_path, json_text)
    if args.markdown_path:
        atomic_write(args.markdown_path, markdown(contract))
    if not args.json_path and not args.markdown_path:
        print(json_text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
