#!/usr/bin/env python3
"""Audit one explicit native or virtual DX100 gather storage contract."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import tempfile
from pathlib import Path

SCHEMA_VERSION = 2
LINE_BYTES = 64
SPD_WORD_BYTES = 4
COMBINE_MIN_BYTES = 74
SOURCE_RESERVATION_MIN_BYTES = 16
WRITE_TAG_MIN_BYTES = 8

MODES = {
    "native": {
        "index_residency": "scratchpad",
        "result_residency": "scratchpad",
        "completion_tile_role": "result",
        "steps": [
            "fill the complete index tile in scratchpad",
            "schedule derived source lines through Row/Offset Tables",
            "materialize returned values in the destination tile",
        ],
    },
    "compact_spd_index": {
        "index_residency": "scratchpad",
        "result_residency": "backing_memory",
        "completion_tile_role": "token",
        "steps": [
            "retain the complete index tile in scratchpad",
            "schedule derived source lines through Row/Offset Tables",
            "combine returned values and retire them to backing memory",
        ],
    },
    "direct_index_virtual": {
        "index_residency": "memory_stream",
        "result_residency": "backing_memory",
        "completion_tile_role": "token",
        "steps": [
            "stream bounded index lines from memory",
            "retain derived source descriptors in Row/Offset Tables",
            "combine returned values and retire them to backing memory",
        ],
    },
}

INTEGER_DEFAULTS = {
    "num_cores": 4,
    "num_tiles_per_core": 8,
    "num_tile_elements": 16384,
    "physical_tile_elements": 0,
    "num_initial_row_table_slices": 32,
    "num_row_table_rows_per_slice": 64,
    "num_row_table_entries_per_subslice_row": 8,
    "num_maas": 1,
    "num_indirect_units_per_maa": 1,
    "virtual_combine_slots": 16,
    "virtual_combine_words": 0,
    "virtual_combine_ways": 0,
    "virtual_combine_victim_policy": 0,
    "virtual_combine_banks": 0,
    "virtual_response_slots": 8,
    "virtual_response_words": 0,
    "virtual_response_word_pool": 0,
    "virtual_words_per_cycle": 0,
    "virtual_max_outstanding_writes": 32,
    "virtual_index_buffer_lines": 1,
    "virtual_index_partitions": 1,
    "virtual_index_filter_words_per_cycle": 0,
}
BOOL_DEFAULTS = {
    "virtual_masked_writes": False,
    "virtual_grow_order": False,
    "virtual_native_issue_order": False,
    "no_reorder": False,
    "reconfigure_row_table": False,
}


class ContractError(ValueError):
    pass


def parse_int(value: str, key: str) -> int:
    try:
        return int(value, 0)
    except ValueError as exc:
        raise ContractError(
            f"{key} must be an integer, got {value!r}"
        ) from exc


def parse_bool(value: str, key: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "yes", "1"}:
        return True
    if normalized in {"false", "no", "0"}:
        return False
    raise ContractError(f"{key} must be a boolean, got {value!r}")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_maa_section(parser: configparser.ConfigParser) -> str:
    matches = [
        section
        for section in parser.sections()
        if parser.has_option(section, "num_tile_elements")
        and parser.has_option(section, "num_tiles_per_core")
    ]
    if len(matches) != 1:
        raise ContractError(
            "config must contain exactly one MAA section; found "
            + repr(matches)
        )
    return matches[0]


def load_config(path: Path) -> tuple[dict, dict]:
    path = path.resolve(strict=True)
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    with path.open(encoding="utf-8") as handle:
        parser.read_file(handle)
    section = find_maa_section(parser)
    values = dict(INTEGER_DEFAULTS)
    values.update(BOOL_DEFAULTS)
    for key in INTEGER_DEFAULTS:
        if parser.has_option(section, key):
            values[key] = parse_int(parser.get(section, key), key)
    for key in BOOL_DEFAULTS:
        if parser.has_option(section, key):
            values[key] = parse_bool(parser.get(section, key), key)

    cache_sections = [
        item
        for item in parser.sections()
        if item.startswith("system.maa_retirement_caches")
        and parser.get(item, "type", fallback="") == "Cache"
    ]
    cache_data_bytes = sum(
        parse_int(parser.get(item, "size"), f"{item}.size")
        for item in cache_sections
    )
    cache_geometry = [
        {
            "section": item,
            "size_bytes": parse_int(parser.get(item, "size"), "size"),
            "assoc": parse_int(parser.get(item, "assoc"), "assoc"),
            "mshrs": parse_int(parser.get(item, "mshrs"), "mshrs"),
            "targets_per_mshr": parse_int(
                parser.get(item, "tgts_per_mshr"), "tgts_per_mshr"
            ),
            "write_buffers": parse_int(
                parser.get(item, "write_buffers"), "write_buffers"
            ),
        }
        for item in cache_sections
    ]
    return values, {
        "path": str(path),
        "sha256": sha256_bytes(path.read_bytes()),
        "maa_section": section,
        "retirement_caches": cache_geometry,
        "retirement_cache_data_bytes": cache_data_bytes,
    }


def load_case(path: Path) -> tuple[dict, Path]:
    path = path.resolve(strict=True)
    try:
        case = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid case JSON: {exc}") from exc
    if case.get("schema_version") != 1:
        raise ContractError("case manifest schema_version must be 1")
    for key in ("case_id", "mode", "config_ini", "instruction"):
        if key not in case:
            raise ContractError(f"case manifest is missing {key}")
    if case["mode"] not in MODES:
        raise ContractError(f"unsupported mode {case['mode']!r}")
    config_path = Path(case["config_ini"])
    if not config_path.is_absolute():
        config_path = path.parent / config_path
    return case, config_path


def validate(case: dict, values: dict) -> None:
    instruction = case["instruction"]
    for key in (
        "logical_iterations",
        "element_bytes",
        "index_residency",
        "result_residency",
        "completion_tile_role",
    ):
        if key not in instruction:
            raise ContractError(f"instruction is missing {key}")
    if instruction["logical_iterations"] <= 0:
        raise ContractError("logical_iterations must be positive")
    if instruction["logical_iterations"] > values["num_tile_elements"]:
        raise ContractError("logical_iterations exceeds logical tile capacity")
    if instruction["element_bytes"] not in {4, 8}:
        raise ContractError("element_bytes must be 4 or 8")
    expected = MODES[case["mode"]]
    for key in (
        "index_residency",
        "result_residency",
        "completion_tile_role",
    ):
        if instruction[key] != expected[key]:
            raise ContractError(
                f"{case['mode']} requires {key}={expected[key]!r}"
            )

    positive = {
        "num_cores",
        "num_tiles_per_core",
        "num_tile_elements",
        "num_initial_row_table_slices",
        "num_row_table_rows_per_slice",
        "num_row_table_entries_per_subslice_row",
        "num_maas",
        "num_indirect_units_per_maa",
        "virtual_combine_slots",
        "virtual_response_slots",
        "virtual_max_outstanding_writes",
        "virtual_index_buffer_lines",
        "virtual_index_partitions",
    }
    for key in positive:
        if values[key] <= 0:
            raise ContractError(f"{key} must be positive")
    for key in set(INTEGER_DEFAULTS) - positive:
        if values[key] < 0:
            raise ContractError(f"{key} must be non-negative")
    logical = values["num_tile_elements"]
    physical = values["physical_tile_elements"] or logical
    if physical > logical:
        raise ContractError("physical_tile_elements exceeds num_tile_elements")
    if not 1 <= values["virtual_index_buffer_lines"] <= 1024:
        raise ContractError("virtual_index_buffer_lines must be in [1,1024]")
    if not 1 <= values["virtual_index_partitions"] <= 64:
        raise ContractError("virtual_index_partitions must be in [1,64]")
    if (
        case["mode"] != "direct_index_virtual"
        and values["virtual_index_partitions"] != 1
    ):
        raise ContractError(
            "virtual_index_partitions requires direct_index_virtual mode"
        )
    ways = values["virtual_combine_ways"]
    slots = values["virtual_combine_slots"]
    if ways and slots % ways:
        raise ContractError("virtual_combine_slots must divide into ways")
    sets = 1 if ways == 0 else slots // ways
    banks = values["virtual_combine_banks"]
    if banks and ways == 0:
        raise ContractError("banked combiner requires finite associativity")
    if banks > sets:
        raise ContractError("virtual_combine_banks exceeds combiner sets")
    if values["virtual_combine_victim_policy"] not in {0, 1, 2}:
        raise ContractError("virtual_combine_victim_policy must be 0, 1, or 2")
    if case["mode"] != "native" and values["no_reorder"]:
        raise ContractError(
            "virtual reorder claim is invalid with no_reorder=true"
        )
    if values["virtual_grow_order"] and values["virtual_native_issue_order"]:
        raise ContractError(
            "virtual_grow_order and virtual_native_issue_order are mutually "
            "exclusive"
        )
    if (
        case["mode"] != "direct_index_virtual"
        and values["virtual_native_issue_order"]
    ):
        raise ContractError(
            "virtual_native_issue_order requires direct_index_virtual mode"
        )


def response_storage(values: dict, element_bytes: int) -> tuple[int, int, str]:
    slots = values["virtual_response_slots"]
    if values["virtual_response_word_pool"]:
        words = values["virtual_response_word_pool"]
        return words * 8, words * element_bytes, "shared_word_pool"
    if values["virtual_response_words"]:
        words = slots * values["virtual_response_words"]
        return words * 8, words * element_bytes, "fixed_words_per_slot"
    return slots * LINE_BYTES, slots * LINE_BYTES, "full_cache_line_per_slot"


def build_contract(case: dict, values: dict, source: dict) -> dict:
    validate(case, values)
    mode = case["mode"]
    instruction = case["instruction"]
    logical = values["num_tile_elements"]
    physical = values["physical_tile_elements"] or logical
    tiles = values["num_cores"] * values["num_tiles_per_core"]
    units = values["num_maas"] * values["num_indirect_units_per_maa"]
    native_spd = tiles * logical * SPD_WORD_BYTES
    physical_spd = tiles * physical * SPD_WORD_BYTES
    native_completion_target = (tiles * logical + 7) // 8
    physical_completion_target = (tiles * physical + 7) // 8
    simulator_completion = tiles * physical

    simulator_response, target_response, response_kind = response_storage(
        values, instruction["element_bytes"]
    )
    per_unit_simulator = {
        "combine_minimum": values["virtual_combine_slots"] * COMBINE_MIN_BYTES,
        "response_identities_minimum": (
            values["virtual_response_slots"] * SOURCE_RESERVATION_MIN_BYTES
        ),
        "response_payload": simulator_response,
        "write_tags_minimum": (
            values["virtual_max_outstanding_writes"] * WRITE_TAG_MIN_BYTES
        ),
        "retained_packet_payload_upper_bound": (
            values["virtual_max_outstanding_writes"] * LINE_BYTES
        ),
    }
    per_unit_target = dict(per_unit_simulator)
    per_unit_target["response_payload"] = target_response
    packet_payload_per_unit = per_unit_target.pop(
        "retained_packet_payload_upper_bound"
    )
    simulator_virtual_conservative = units * sum(per_unit_simulator.values())
    target_virtual_lower = units * sum(per_unit_target.values())
    target_packet_payload_upper = units * packet_payload_per_unit
    index_payload = (
        values["virtual_index_buffer_lines"] * LINE_BYTES * units
        if mode == "direct_index_virtual"
        else 0
    )
    cache_data = (
        source["retirement_cache_data_bytes"] if mode != "native" else 0
    )
    native_reference = native_spd + native_completion_target
    if mode == "native":
        target_lower = native_reference
        target_conservative = native_reference
    else:
        target_lower = (
            physical_spd
            + physical_completion_target
            + target_virtual_lower
            + index_payload
            + cache_data
        )
        target_conservative = target_lower + target_packet_payload_upper

    effective_entries = case.get("topology", {}).get(
        "row_table_effective_entries_per_row"
    )
    unique_line_capacity = None
    if effective_entries is not None:
        if not isinstance(effective_entries, int) or effective_entries <= 0:
            raise ContractError(
                "effective Row-Table entries/row must be positive"
            )
        unique_line_capacity = (
            values["num_initial_row_table_slices"]
            * values["num_row_table_rows_per_slice"]
            * effective_entries
        )

    if mode == "native":
        issue_order = "native_row_table"
        reorder_claim = (
            "native Row-Table issue order over the configured logical "
            "descriptor window until capacity forces a drain"
        )
    elif values["virtual_native_issue_order"]:
        issue_order = "native_claim_scan"
        reorder_claim = (
            "logical 16K descriptors remain live and are claimed in the "
            "native Row-Table scan order; equivalence still requires an "
            "instruction-level issue digest"
        )
    elif values["virtual_grow_order"]:
        issue_order = "bounded_grow_grouping"
        reorder_claim = (
            "experimental grow-address grouping within each bounded Build "
            "epoch; not equivalent to native issue across refill epochs"
        )
    else:
        issue_order = "bounded_row_id_scan"
        reorder_claim = (
            "bounded row-ID claim order over the logical descriptor window; "
            "does not preserve native cross-row grow-address issue order"
        )

    resolved_input = {
        "case": case,
        "config_sha256": source["sha256"],
        "configured_hardware": values,
    }
    resolved_sha = sha256_bytes(
        json.dumps(
            resolved_input, sort_keys=True, separators=(",", ":")
        ).encode()
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case["case_id"],
        "mode": mode,
        "resolved_input_sha256": resolved_sha,
        "source": source,
        "instruction": instruction,
        "configured_hardware": {
            **values,
            "resolved_physical_tile_elements": physical,
            "total_tiles": tiles,
            "total_indirect_units": units,
        },
        "active_dataflow": MODES[mode]["steps"],
        "simulator_allocation": {
            "physical_spd_payload_bytes": physical_spd,
            "completion_bool_array_bytes": simulator_completion,
            "per_indirect_unit_conservative_components": per_unit_simulator,
            "all_indirect_units_conservative_bytes": (
                simulator_virtual_conservative
            ),
            "direct_index_payload_bytes": index_payload,
            "retirement_cache_data_bytes": cache_data,
            "response_storage_kind": response_kind,
            "excludes": [
                "C++ object and container metadata",
                "cache tags and cache controller metadata",
                "network, packet, routing, and arbitration state beyond listed payload",
            ],
        },
        "target_hardware_budget": {
            "scope": "capacity_bounds_not_area_power_or_frequency",
            "native_reference_bytes": native_reference,
            "physical_spd_payload_bytes": (
                native_spd if mode == "native" else physical_spd
            ),
            "bitpacked_completion_bytes": (
                native_completion_target
                if mode == "native"
                else physical_completion_target
            ),
            "per_indirect_unit_minimum_bytes": (
                {} if mode == "native" else per_unit_target
            ),
            "all_indirect_units_minimum_bytes": (
                0 if mode == "native" else target_virtual_lower
            ),
            "retained_packet_payload_upper_bound_bytes": (
                0 if mode == "native" else target_packet_payload_upper
            ),
            "direct_index_payload_bytes": index_payload,
            "retirement_cache_data_bytes": cache_data,
            "counted_lower_bound_bytes": target_lower,
            "conservative_counted_bytes": target_conservative,
            "conservative_difference_vs_native_bytes": (
                target_conservative - native_reference
            ),
            "conservative_reduction_vs_native_percent": (
                (native_reference - target_conservative)
                * 100.0
                / native_reference
            ),
            "excludes": [
                "cache tags, MSHR payload, and cache control",
                "ports, interconnect, arbitration, and routing",
                "physical design, area, power, energy, and timing closure",
            ],
        },
        "reorder_resources": {
            "offset_iteration_capacity_per_unit": logical,
            "row_table_rows": (
                values["num_initial_row_table_slices"]
                * values["num_row_table_rows_per_slice"]
            ),
            "row_table_unique_line_capacity": unique_line_capacity,
            "configured_entries_per_subslice_row": values[
                "num_row_table_entries_per_subslice_row"
            ],
            "issue_order": issue_order,
            "virtual_grow_order": values["virtual_grow_order"],
            "virtual_native_issue_order": values[
                "virtual_native_issue_order"
            ],
            "direct_index_partitions": values["virtual_index_partitions"],
            "direct_index_filter_words_per_cycle": values[
                "virtual_index_filter_words_per_cycle"
            ],
            "index_scan_policy": (
                "dram_grow_modulo"
                if values["virtual_index_partitions"] > 1
                else "single_pass"
            ),
            "effective_reorder_window": (
                "bounded by logical iterations, unique-line capacity, and "
                "address distribution"
            ),
            "physical_spd_payload_window": physical,
            "logical_descriptor_window": logical,
            "direct_index_spd_role": (
                "completion_token_only"
                if mode == "direct_index_virtual"
                else "payload"
            ),
            "claim": reorder_claim,
        },
        "unsupported_scope": [
            "transparent producer/consumer paging",
            "scatter and read-modify-write virtualization",
            "whole-scratchpad replacement by the direct-gather opcode",
        ],
    }


def markdown(contract: dict) -> str:
    target = contract["target_hardware_budget"]
    reorder = contract["reorder_resources"]
    line_capacity = reorder["row_table_unique_line_capacity"]
    line_capacity_text = (
        str(line_capacity)
        if line_capacity is not None
        else "unknown without topology input"
    )
    lines = [
        f"# Virtual Gather Contract: {contract['case_id']}",
        "",
        f"- Mode: `{contract['mode']}`.",
        f"- Logical iterations: {contract['instruction']['logical_iterations']:,}.",
        "- Physical tile: "
        f"{contract['configured_hardware']['resolved_physical_tile_elements']:,} "
        "elements.",
        "- Indirect units provisioned: "
        f"{contract['configured_hardware']['total_indirect_units']}.",
        f"- Row-Table unique-line capacity: {line_capacity_text}.",
        "",
        "## Active Dataflow",
        "",
    ]
    lines.extend(
        f"{index}. {step}."
        for index, step in enumerate(contract["active_dataflow"], 1)
    )
    lines.extend(
        [
            "",
            "## Target Hardware Budget",
            "",
            f"- Native reference: {target['native_reference_bytes']:,} bytes.",
            "- Counted structural lower bound: "
            f"{target['counted_lower_bound_bytes']:,} bytes.",
            "- Conservative count with in-flight write payload: "
            f"{target['conservative_counted_bytes']:,} bytes.",
            "- Conservative reduction versus native: "
            f"{target['conservative_reduction_vs_native_percent']:.2f}%.",
            "",
            "This is a capacity ledger with explicit bounds, not an area, "
            "power, or timing result.",
        ]
    )
    return "\n".join(lines) + "\n"


def atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
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
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--json", type=Path, dest="json_path")
    parser.add_argument("--markdown", type=Path, dest="markdown_path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        case, config_path = load_case(args.case)
        values, source = load_config(config_path)
        source["case_manifest_path"] = str(args.case.resolve())
        source["case_manifest_sha256"] = sha256_bytes(args.case.read_bytes())
        contract = build_contract(case, values, source)
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
