#!/usr/bin/env python3
"""Inventory source-level DX100 tile producer/consumer chains.

This is deliberately a small, dependency-free source audit rather than a C++
compiler.  It removes comments, parses balanced MAA API calls, and connects a
tile use only to the latest lexical definition whose brace scope dominates the
use.  The JSON preserves every classified edge's endpoints and tile name so
inferred chains remain auditable without duplicating parsed call records.
It does not claim that a source call site executes, or how often it executes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
from collections import (
    Counter,
    defaultdict,
)
from pathlib import Path
from typing import (
    Any,
    Iterable,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx"}
DEFAULT_ROOTS = (
    "benchmarks/API",
    "benchmarks/NAS",
    "benchmarks/UME",
    "benchmarks/gapbs/src",
    "benchmarks/hashjoin/src",
    "benchmarks/spatter/src/Spatter",
)
EXCLUDED_FILES = {"benchmarks/API/MAA_compiler_api.cpp"}

PAPER_PROVENANCE = {
    "title": "DX100: A Programmable Data Access Accelerator for Indirection",
    "venue": "Proceedings of the 52nd Annual International Symposium on Computer Architecture (ISCA 2025)",
    "doi": "10.1145/3695053.3731015",
    "arxiv": "2505.23073v2",
    "verified_local_path": "/data1/nier/worktrees/dx100-research-weekly-synopsis-20260726/references/papers/dx100.pdf",
    "verified_local_sha256": "ec18bdc585f32e3da5c0fd467e686dd2137b3db88d4c327d510509213e7c44a3",
    "verified_date": "2026-08-08",
    "supporting_locations": {
        "scratchpad_as_intermediate_and_communication_storage": "pp. 3-4, Sections 3 and 3.1",
        "row_word_reordering_and_coalescing": "pp. 4-5, Section 3.2 and Figures 3-4",
        "tile_size_sweep_mechanisms": "p. 11, Section 6.4 and Figure 13",
        "scratchpad_area_and_power": "p. 11, Section 6.5 and Table 4",
    },
    "tile_sweep_interpretation": {
        "explicit_paper_mechanisms": [
            "duplicate-address coalescing reduces memory accesses",
            "larger reorder window improves DRAM row-buffer locality and bandwidth utilization",
        ],
        "not_supported_as_primary_explanation": "generic cache-capacity reuse or reusable result-tile residency",
    },
}


# Tile operand positions in the public API.  Roles distinguish payload from
# completion/control tokens; optional -1 operands are discarded below.
SPECS: dict[str, dict[str, Any]] = {
    "maa_stream_load": {
        "family": "stream_load",
        "outputs": {4: "result"},
        "inputs": {5: "condition"},
    },
    "maa_stream_prefetch": {
        "family": "stream_prefetch",
        "outputs": {4: "completion"},
        "inputs": {},
    },
    "maa_stream_store": {
        "family": "stream_store",
        "outputs": {},
        "inputs": {4: "value", 5: "condition"},
    },
    "maa_indirect_load": {
        "family": "indirect_load",
        "outputs": {2: "result"},
        "inputs": {1: "index", 3: "condition"},
    },
    "maa_indirect_load_virtual": {
        "family": "direct_gather",
        "outputs": {2: "completion"},
        "inputs": {1: "index", 4: "condition"},
    },
    "maa_indirect_load_virtual_index": {
        "family": "direct_index_gather",
        "outputs": {2: "completion"},
        "inputs": {7: "prefetch_token"},
    },
    "maa_indirect_load_virtual_index_prefetch": {
        "family": "direct_index_gather",
        "outputs": {2: "completion", 3: "prefetch_token"},
        "inputs": {},
    },
    "maa_indirect_load_index": {
        "family": "direct_index_load",
        "outputs": {2: "result"},
        "inputs": {},
    },
    "maa_indirect_load_spd_stream": {
        "family": "indirect_spd_stream",
        "outputs": {2: "result"},
        "inputs": {1: "index"},
    },
    "maa_indirect_store_vector": {
        "family": "indirect_store",
        "outputs": {4: "old_value"},
        "inputs": {1: "index", 2: "value", 3: "condition"},
    },
    "maa_indirect_store_scalar": {
        "family": "indirect_store",
        "outputs": {4: "old_value"},
        "inputs": {1: "index", 3: "condition"},
    },
    "maa_indirect_rmw_vector": {
        "family": "indirect_rmw",
        "outputs": {5: "old_value"},
        "inputs": {1: "index", 2: "value", 4: "condition"},
    },
    "maa_indirect_rmw_scalar": {
        "family": "indirect_rmw",
        "outputs": {5: "old_value"},
        "inputs": {1: "index", 4: "condition"},
    },
    "maa_range_loop": {
        "family": "range",
        "outputs": {5: "range_i", 6: "range_j"},
        "inputs": {2: "range_min", 3: "range_max", 7: "condition"},
    },
    "maa_alu_scalar": {
        "family": "alu_scalar",
        "outputs": {2: "result"},
        "inputs": {0: "value", 4: "condition"},
    },
    "maa_alu_vector": {
        "family": "alu_vector",
        "outputs": {2: "result"},
        "inputs": {0: "value", 1: "value", 4: "condition"},
    },
    "maa_alu_reduce": {
        "family": "alu_reduce",
        "outputs": {},
        "inputs": {0: "value", 3: "condition"},
    },
    "maa_virtual_tile_alu_scalar_store": {
        "family": "fused_scalar_direct_store",
        "outputs": {},
        "inputs": {2: "completion", 3: "physical_input", 4: "physical_output"},
    },
    "wait_ready": {
        "family": "cpu_wait",
        "outputs": {},
        "inputs": {0: "ready"},
    },
    "get_tile_size": {
        "family": "cpu_size",
        "outputs": {},
        "inputs": {0: "cpu_metadata"},
    },
    "get_cacheable_tile_pointer": {
        "family": "cpu_pointer",
        "outputs": {},
        "inputs": {0: "cpu_pointer"},
    },
    "get_noncacheable_tile_pointer": {
        "family": "cpu_pointer",
        "outputs": {},
        "inputs": {0: "cpu_pointer"},
    },
    "cpu_tile_payload_access": {
        "family": "cpu_payload",
        "outputs": {},
        "inputs": {0: "cpu_payload"},
    },
}

ALIASES = {
    "maa_indirect_rmw": "maa_indirect_rmw_vector",
    "maa_indirect_store": "maa_indirect_store_vector",
}

CALL_RE = re.compile(
    r"\b(maa_[A-Za-z0-9_]+|wait_ready|get_tile_size|"
    r"get_(?:non)?cacheable_tile_pointer)\s*(<[^;(){}]*>)?\s*\("
)
IDENTIFIER_RE = re.compile(r"^[A-Za-z_]\w*$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_revision() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def scrub_cpp(text: str) -> str:
    """Replace comments and quoted literal contents while retaining newlines."""
    out = list(text)
    i = 0
    state = "code"
    while i < len(text):
        pair = text[i : i + 2]
        char = text[i]
        if state == "code":
            if pair == "//":
                out[i] = out[i + 1] = " "
                state = "line_comment"
                i += 2
                continue
            if pair == "/*":
                out[i] = out[i + 1] = " "
                state = "block_comment"
                i += 2
                continue
            if char == '"':
                out[i] = " "
                state = "string"
            elif char == "'":
                out[i] = " "
                state = "char"
        elif state == "line_comment":
            if char == "\n":
                state = "code"
            else:
                out[i] = " "
        elif state == "block_comment":
            if pair == "*/":
                out[i] = out[i + 1] = " "
                state = "code"
                i += 2
                continue
            if char != "\n":
                out[i] = " "
        elif state in {"string", "char"}:
            quote = '"' if state == "string" else "'"
            if char == "\\":
                out[i] = " "
                if i + 1 < len(text):
                    if text[i + 1] != "\n":
                        out[i + 1] = " "
                    i += 2
                    continue
            if char == quote:
                out[i] = " "
                state = "code"
            elif char != "\n":
                out[i] = " "
        i += 1
    return "".join(out)


def split_arguments(body: str) -> list[str]:
    args: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(body):
        if char in "(<[{":
            depth += 1
        elif char in ")>]}" and depth:
            depth -= 1
        elif char == "," and depth == 0:
            args.append(body[start:index].strip())
            start = index + 1
    tail = body[start:].strip()
    if tail or body:
        args.append(tail)
    return args


def normalize_tile(expression: str) -> str | None:
    value = re.sub(r"\s+", "", expression)
    while value.startswith("(") and value.endswith(")"):
        value = value[1:-1]
    if value in {"-1", "NA_UINT8", "nullptr", "NULL"}:
        return None
    return value if IDENTIFIER_RE.fullmatch(value) else None


def discover_sources(roots: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for relative in roots:
        root = ROOT / relative
        for path in root.rglob("*"):
            rel = path.relative_to(ROOT).as_posix()
            if path.suffix in SOURCE_EXTENSIONS and rel not in EXCLUDED_FILES:
                paths.append(path)
    return sorted(set(paths))


def parse_calls(path: Path) -> tuple[list[dict[str, Any]], str]:
    original = path.read_text(encoding="utf-8", errors="replace")
    text = scrub_cpp(original)
    calls: list[dict[str, Any]] = []
    brace_path: list[int] = []
    next_brace = 0
    scan = 0
    ordinal = 0
    for match in CALL_RE.finditer(text):
        for char in text[scan : match.start()]:
            if char == "{":
                next_brace += 1
                brace_path.append(next_brace)
            elif char == "}" and brace_path:
                brace_path.pop()
        scan = match.start()
        depth = 1
        cursor = match.end()
        while cursor < len(text) and depth:
            if text[cursor] == "(":
                depth += 1
            elif text[cursor] == ")":
                depth -= 1
            cursor += 1
        if depth:
            raise ValueError(
                f"unterminated call at {path}:{text.count(chr(10), 0, match.start()) + 1}"
            )
        raw_name = match.group(1)
        name = ALIASES.get(raw_name, raw_name)
        if name not in SPECS:
            continue
        arguments = split_arguments(text[match.end() : cursor - 1])
        spec = SPECS[name]
        inputs = []
        outputs = []
        for position, role in spec["inputs"].items():
            if position < len(arguments):
                tile = normalize_tile(arguments[position])
                if tile is not None:
                    inputs.append(
                        {"position": position, "role": role, "tile": tile}
                    )
        for position, role in spec["outputs"].items():
            if position < len(arguments):
                tile = normalize_tile(arguments[position])
                if tile is not None:
                    outputs.append(
                        {"position": position, "role": role, "tile": tile}
                    )
        ordinal += 1
        calls.append(
            {
                "id": f"{path.relative_to(ROOT).as_posix()}:{text.count(chr(10), 0, match.start()) + 1}:{ordinal}",
                "file": path.relative_to(ROOT).as_posix(),
                "line": text.count("\n", 0, match.start()) + 1,
                "ordinal": ordinal,
                "name": name,
                "spelling": raw_name,
                "family": spec["family"],
                "template": (match.group(2) or "").strip("<> \t\n"),
                "arguments": arguments,
                "inputs": inputs,
                "outputs": outputs,
                "scope": list(brace_path),
                "start": match.start(),
                "end": cursor,
                "pointer_alias": None,
            }
        )
        if spec["family"] == "cpu_pointer":
            statement_start = max(
                text.rfind(";", 0, match.start()),
                text.rfind("{", 0, match.start()),
                text.rfind("}", 0, match.start()),
            )
            prefix = text[statement_start + 1 : match.start()]
            alias = re.search(r"([A-Za-z_]\w*)\s*=\s*$", prefix)
            if alias:
                calls[-1]["pointer_alias"] = alias.group(1)

    # A tile pointer is often bound before the MAA producer and dereferenced
    # only after wait_ready().  Preserve those raw payload sites explicitly;
    # pointer creation and readiness polling are not counted as data reads.
    synthetic: list[dict[str, Any]] = []
    for binding in calls:
        alias = binding.get("pointer_alias")
        if not alias or not binding["inputs"]:
            continue
        tile = binding["inputs"][0]["tile"]
        for access in re.finditer(rf"\b{re.escape(alias)}\s*\[", text):
            if access.start() <= binding["end"]:
                continue
            synthetic.append(
                {
                    "id": "",
                    "file": path.relative_to(ROOT).as_posix(),
                    "line": text.count("\n", 0, access.start()) + 1,
                    "ordinal": 0,
                    "name": "cpu_tile_payload_access",
                    "spelling": alias,
                    "family": "cpu_payload",
                    "template": "",
                    "arguments": [tile],
                    "inputs": [
                        {"position": 0, "role": "cpu_payload", "tile": tile}
                    ],
                    "outputs": [],
                    "scope": [],
                    "start": access.start(),
                    "end": access.end(),
                    "pointer_alias": alias,
                }
            )
    calls.extend(synthetic)
    calls.sort(key=lambda item: (item["start"], item["end"], item["name"]))
    # Recompute brace paths for all real and synthetic calls in one scan so
    # dominance IDs are stable.
    brace_path = []
    next_brace = 0
    scan = 0
    for ordinal, call in enumerate(calls, 1):
        for char in text[scan : call["start"]]:
            if char == "{":
                next_brace += 1
                brace_path.append(next_brace)
            elif char == "}" and brace_path:
                brace_path.pop()
        scan = call["start"]
        call["ordinal"] = ordinal
        call["scope"] = list(brace_path)
        call["id"] = f"{call['file']}:{call['line']}:{ordinal}"
    return calls, text


def dominates(definition: dict[str, Any], use: dict[str, Any]) -> bool:
    prefix = definition["scope"]
    scope = use["scope"]
    return (
        definition["file"] == use["file"]
        and definition["ordinal"] < use["ordinal"]
        and len(prefix) <= len(scope)
        and prefix == scope[: len(prefix)]
    )


def edge_class(
    producer: dict[str, Any], consumer: dict[str, Any], role: str
) -> str:
    pf = producer["family"]
    cf = consumer["family"]
    if (
        pf == "stream_load"
        and role == "index"
        and cf
        in {
            "indirect_load",
            "direct_gather",
            "indirect_spd_stream",
            "indirect_store",
            "indirect_rmw",
        }
    ):
        return "stream_index_to_indirect"
    if pf in {
        "indirect_load",
        "direct_index_load",
        "indirect_spd_stream",
    } and cf.startswith("alu_"):
        return "indirect_result_to_alu"
    if cf == "stream_store":
        return "result_to_stream_store"
    if cf == "cpu_payload":
        return "result_to_cpu_payload"
    if cf == "cpu_pointer":
        return "result_to_cpu_pointer_exposure"
    if cf == "cpu_wait":
        return "result_to_cpu_ready_wait"
    if cf == "cpu_size":
        return "result_to_cpu_size_read"
    if role == "condition":
        return "condition_operand"
    if cf == "range" or role.startswith("range_"):
        return "range_operand"
    if cf in {"indirect_store", "indirect_rmw"}:
        return "indirect_store_rmw_operand"
    return "other_tile_flow"


def connect_calls(
    calls: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    definitions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    edges: list[dict[str, Any]] = []
    outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for call in calls:
        for operand in call["inputs"]:
            candidates = [
                definition
                for definition in definitions[operand["tile"]]
                if dominates(definition["call"], call)
            ]
            if candidates:
                definition = max(
                    candidates, key=lambda item: item["call"]["ordinal"]
                )
                edge = {
                    "producer": definition["call"]["id"],
                    "producer_family": definition["call"]["family"],
                    "producer_output_role": definition["role"],
                    "consumer": call["id"],
                    "consumer_family": call["family"],
                    "consumer_input_role": operand["role"],
                    "tile": operand["tile"],
                }
                edge["class"] = edge_class(
                    definition["call"], call, operand["role"]
                )
                edges.append(edge)
                outgoing[definition["call"]["id"]].append(edge)
        # Inputs are resolved before outputs so in-place ALU gets the prior def.
        for operand in call["outputs"]:
            definitions[operand["tile"]].append(
                {"call": call, "role": operand["role"]}
            )
    return edges, outgoing


def site(call: dict[str, Any]) -> str:
    return f"{call['file']}:{call['line']}"


def benchmark_suite(path: str) -> str:
    parts = path.split("/")
    if len(parts) < 2:
        return path
    if parts[1] == "API":
        return "API"
    if parts[1] == "NAS" and len(parts) > 2:
        return f"NAS/{parts[2]}"
    return {
        "UME": "UME",
        "gapbs": "GAPBS",
        "hashjoin": "HashJoin",
        "spatter": "Spatter",
    }.get(parts[1], parts[1])


def production_site(site_label: str) -> bool:
    return not site_label.startswith("benchmarks/API/")


def call_lookup(calls: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {call["id"]: call for call in calls}


def terminal_sinks(calls: list[dict[str, Any]]) -> list[dict[str, str]]:
    result = []
    for call in calls:
        kind = None
        note = None
        if call["family"] == "direct_gather":
            kind = "direct_gather_to_backing"
            note = "result tile eliminated; index tile retained"
        elif call["family"] == "direct_index_gather":
            kind = "direct_index_gather_to_backing"
            note = "both index and result payload tiles eliminated"
        elif call["family"] == "direct_index_load":
            kind = "direct_index_to_result_tile"
            note = "index tile eliminated; result tile retained"
        elif call["family"] == "fused_scalar_direct_store":
            kind = "paged_scalar_transform_to_direct_store"
            note = "logical full result eliminated; physical input/output pages retained"
        elif call["family"] == "indirect_spd_stream":
            kind = "spd_mediated_gather_stream_store"
            note = "internal stream sink exists but result SPD tile is materialized"
        elif call["family"] in {
            "stream_store",
            "indirect_store",
            "indirect_rmw",
        }:
            kind = call["family"]
            note = "terminal memory side effect; operand tiles remain materialized"
        if kind:
            result.append({"site": site(call), "kind": kind, "note": note})
    return result


def legality(
    calls: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    outgoing: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    lookup = call_lookup(calls)
    index_candidates = []
    direct_result_candidates = []
    scalar_fusion_candidates = []
    storage_required = []

    for producer_id, producer_edges in outgoing.items():
        producer = lookup[producer_id]
        payload_edges = [
            edge
            for edge in producer_edges
            if edge["consumer_input_role"]
            not in {"ready", "cpu_metadata", "cpu_pointer"}
        ]
        if (
            producer["family"] == "stream_load"
            and len(payload_edges) == 1
            and payload_edges[0]["consumer_input_role"] == "index"
        ):
            consumer = lookup[payload_edges[0]["consumer"]]
            conditioned = any(
                item["role"] == "condition" for item in consumer["inputs"]
            )
            if conditioned:
                classification = "requires_predicated_direct_index_support"
            elif consumer["family"] in {"indirect_load", "direct_gather"}:
                classification = "legal_and_current_direct_index_api_shape"
            elif consumer["family"] == "indirect_spd_stream":
                classification = (
                    "legal_but_current_api_needs_direct_index_plus_sink_form"
                )
            else:
                classification = (
                    "legal_but_no_current_direct_index_store_rmw_opcode"
                )
            index_candidates.append(
                {
                    "producer": site(producer),
                    "consumer": site(consumer),
                    "consumer_family": consumer["family"],
                    "classification": classification,
                    "guard": "no CPU/cross-instruction/multiple use; memory B remains architectural and may be reread later",
                }
            )

        if (
            producer["family"] in {"indirect_load", "direct_index_load"}
            and len(payload_edges) == 1
        ):
            first = lookup[payload_edges[0]["consumer"]]
            if first["family"] == "stream_store":
                direct_result_candidates.append(
                    {
                        "producer": site(producer),
                        "sink": site(first),
                        "classification": "requires_proving_same_iteration_order_and_final_destination",
                    }
                )
            if first["family"] == "alu_scalar":
                second_edges = [
                    edge
                    for edge in outgoing.get(first["id"], [])
                    if edge["consumer_input_role"]
                    not in {"ready", "cpu_metadata", "cpu_pointer"}
                ]
                conditioned = any(
                    item["role"] == "condition" for item in first["inputs"]
                )
                if (
                    len(second_edges) == 1
                    and lookup[second_edges[0]["consumer"]]["family"]
                    == "stream_store"
                ):
                    sink = lookup[second_edges[0]["consumer"]]
                    scalar_fusion_candidates.append(
                        {
                            "gather": site(producer),
                            "transform": site(first),
                            "sink": site(sink),
                            "classification": "legal_simple_scalar_fusion_candidate"
                            if not conditioned
                            else "requires_masked_destination_semantics",
                            "guard": "pure elementwise scalar op, full overwrite, one direct sink, no CPU or repeated consumer",
                        }
                    )

        reasons = []
        if len(payload_edges) > 1:
            reasons.append("multiple_consumers")
        consumer_families = {edge["consumer_family"] for edge in payload_edges}
        if consumer_families & {
            "alu_vector",
            "alu_reduce",
            "range",
            "indirect_store",
            "indirect_rmw",
        }:
            reasons.append("cross_instruction_or_irregular_operand")
        if any(
            edge["class"]
            in {"result_to_cpu_payload", "result_to_cpu_pointer_exposure"}
            for edge in producer_edges
        ):
            reasons.append("cpu_payload_consumption")
        if reasons:
            storage_required.append(
                {
                    "producer": site(producer),
                    "reasons": sorted(set(reasons)),
                    "legal_storage": "physical SPD if capacity/lifetime permit, otherwise coherent LLC-backed logical storage with stable identity",
                }
            )
    return {
        "index_tile_elimination_candidates": index_candidates,
        "result_tile_direct_sink_candidates": direct_result_candidates,
        "scalar_transform_fusion_candidates": scalar_fusion_candidates,
        "spd_or_llc_backed_storage_required": storage_required,
    }


def storage_arithmetic() -> list[dict[str, Any]]:
    rows = []
    for physical, logical in ((4096, 16384), (16384, 65536)):
        for dtype, width in (("FP32", 4), ("FP64", 8)):
            physical_bytes = physical * width
            logical_bytes = logical * width
            rows.append(
                {
                    "physical_elements": physical,
                    "logical_elements": logical,
                    "pages_per_logical_tile": logical // physical,
                    "datatype": dtype,
                    "bytes_per_element": width,
                    "physical_page_bytes": physical_bytes,
                    "logical_tile_bytes": logical_bytes,
                    "simultaneous_source_destination_physical_bytes": 2
                    * physical_bytes,
                    "simultaneous_source_destination_logical_bytes": 2
                    * logical_bytes,
                    "physical_32bit_lanes": width // 4,
                }
            )
    return rows


def dynamic_evidence() -> list[dict[str, Any]]:
    evidence = []
    reorder_path = (
        ROOT / "experiments/evidence/2026-08-08_reorder_survival_smoke.json"
    )
    if reorder_path.exists():
        data = json.loads(reorder_path.read_text(encoding="utf-8"))
        row = data["row64_neutrality"]
        evidence.append(
            {
                "kind": "existing_reorder_survival_trace",
                "path": reorder_path.relative_to(ROOT).as_posix(),
                "sha256": sha256(reorder_path),
                "status": data["status"],
                "source_commit": data["source"]["live_visibility_fix_commit"],
                "simInsts": row["simInsts"],
                "maa_issued_line_instructions": row["issue_digest"]["count"],
                "selected_descriptors": row["measured_reorder_survival"][
                    "selected_descriptors"
                ],
                "rt_full_drains": row["measured_reorder_survival"][
                    "rt_full_drains"
                ],
                "scope": "one exact-output 16K direct-index virtual-gather microbenchmark; line issues are not API instruction counts",
            }
        )
    xrage_path = (
        ROOT / "experiments/analysis/xrage_direct_multiply_2026-08-03.tsv"
    )
    if xrage_path.exists():
        with xrage_path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        evidence.append(
            {
                "kind": "existing_validated_xrage_20k_trace",
                "path": xrage_path.relative_to(ROOT).as_posix(),
                "sha256": sha256(xrage_path),
                "rows": [
                    {
                        key: (
                            int(row[key]) if row[key].isdigit() else row[key]
                        )
                        for key in (
                            "label",
                            "maa_instructions",
                            "maa_indirect_instructions",
                            "maa_stream_read_instructions",
                            "maa_stream_write_instructions",
                            "maa_scalar_alu_instructions",
                            "cpu_committed_instructions",
                        )
                    }
                    for row in rows
                ],
                "scope": "validated deterministic 20K Spatter/XRAGE experiment from older frozen source; not extrapolated to all static sites",
            }
        )
    return evidence


def build_inventory(
    roots: Iterable[str], analyzed_revision: str | None = None
) -> dict[str, Any]:
    all_calls: list[dict[str, Any]] = []
    files = discover_sources(roots)
    scanned = []
    for path in files:
        calls, _ = parse_calls(path)
        if calls:
            scanned.append(
                {
                    "path": path.relative_to(ROOT).as_posix(),
                    "sha256": sha256(path),
                    "call_sites": len(calls),
                }
            )
            all_calls.extend(calls)
    # Calls connect only within one file; ordinal/scope already prevent cross-file edges.
    edges, outgoing = connect_calls(all_calls)
    lookup = call_lookup(all_calls)
    categories: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        categories[edge["class"]].append(
            f"{site(lookup[edge['producer']])} -> {site(lookup[edge['consumer']])} ({edge['tile']})"
        )
    multiple = []
    for producer_id, producer_edges in outgoing.items():
        payload = [
            edge
            for edge in producer_edges
            if edge["consumer_input_role"]
            not in {"ready", "cpu_metadata", "cpu_pointer"}
        ]
        if len(payload) > 1:
            multiple.append(
                {
                    "producer": site(lookup[producer_id]),
                    "consumer_count": len(payload),
                    "consumers": [
                        site(lookup[edge["consumer"]]) for edge in payload
                    ],
                }
            )
    call_counts = Counter(call["name"] for call in all_calls)
    family_counts = Counter(call["family"] for call in all_calls)
    sinks = terminal_sinks(all_calls)
    legal = legality(all_calls, edges, outgoing)
    production_edges = [
        edge
        for edge in edges
        if benchmark_suite(lookup[edge["producer"]]["file"]) != "API"
    ]
    production_sinks = [
        item for item in sinks if production_site(item["site"])
    ]
    index_candidates = legal["index_tile_elimination_candidates"]
    production_index_candidates = [
        item for item in index_candidates if production_site(item["producer"])
    ]
    storage_required = legal["spd_or_llc_backed_storage_required"]
    production_storage = [
        item for item in storage_required if production_site(item["producer"])
    ]
    result = {
        "schema": "dx100.tile_liveness_inventory.v1",
        "paper_provenance": PAPER_PROVENANCE,
        "method": {
            "kind": "comment-stripped balanced-call lexical dominance analysis",
            "static_not_dynamic": True,
            "edge_encoding": "categories entries are 'producer-file:line -> consumer-file:line (tile)'",
            "limitations": [
                "compile-time branches are all source-visible and may not coexist in one binary",
                "brace dominance rejects sibling-branch flow but is not a C++ control-flow or alias analysis",
                "raw tile-pointer subscript sites are payload visibility sites; this lexical audit does not classify each as a read versus write",
                "a legality candidate remains conditional on address/range/ordering equivalence stated in its guard",
            ],
        },
        "source": {
            "analyzed_revision": analyzed_revision or git_revision(),
            "roots": list(roots),
            "files": scanned,
        },
        "summary": {
            "translation_units_with_calls": len(scanned),
            "static_call_sites": len(all_calls),
            "maa_instruction_call_sites": sum(
                call["name"].startswith("maa_") for call in all_calls
            ),
            "api_visibility_call_sites": sum(
                not call["name"].startswith("maa_") for call in all_calls
            ),
            "tile_flow_edges": len(edges),
            "call_sites_by_api": dict(sorted(call_counts.items())),
            "call_sites_by_family": dict(sorted(family_counts.items())),
            "maa_call_sites_by_benchmark_suite": dict(
                sorted(
                    Counter(
                        benchmark_suite(call["file"])
                        for call in all_calls
                        if call["name"].startswith("maa_")
                    ).items()
                )
            ),
            "production_maa_instruction_call_sites": sum(
                call["name"].startswith("maa_")
                and benchmark_suite(call["file"]) != "API"
                for call in all_calls
            ),
            "edge_counts_by_class": dict(
                sorted(Counter(edge["class"] for edge in edges).items())
            ),
            "production_edge_counts_by_class": dict(
                sorted(
                    Counter(edge["class"] for edge in production_edges).items()
                )
            ),
            "terminal_sink_counts": dict(
                sorted(Counter(item["kind"] for item in sinks).items())
            ),
            "production_terminal_sink_counts": dict(
                sorted(
                    Counter(item["kind"] for item in production_sinks).items()
                )
            ),
            "multiple_consumer_producers": len(multiple),
            "legality_candidate_counts": {
                "index_tile_elimination": len(index_candidates),
                "index_tile_elimination_by_classification": dict(
                    sorted(
                        Counter(
                            item["classification"] for item in index_candidates
                        ).items()
                    )
                ),
                "production_index_tile_elimination": len(
                    production_index_candidates
                ),
                "production_index_tile_elimination_by_classification": dict(
                    sorted(
                        Counter(
                            item["classification"]
                            for item in production_index_candidates
                        ).items()
                    )
                ),
                "result_tile_direct_sink": len(
                    legal["result_tile_direct_sink_candidates"]
                ),
                "production_result_tile_direct_sink": sum(
                    production_site(item["producer"])
                    for item in legal["result_tile_direct_sink_candidates"]
                ),
                "scalar_transform_fusion": len(
                    legal["scalar_transform_fusion_candidates"]
                ),
                "production_scalar_transform_fusion": sum(
                    production_site(item["gather"])
                    for item in legal["scalar_transform_fusion_candidates"]
                ),
                "storage_required": len(storage_required),
                "production_storage_required": len(production_storage),
                "production_storage_reasons": dict(
                    sorted(
                        Counter(
                            reason
                            for item in production_storage
                            for reason in item["reasons"]
                        ).items()
                    )
                ),
            },
        },
        "categories": {
            key: sorted(value) for key, value in sorted(categories.items())
        },
        "multiple_consumers": multiple,
        "terminal_sinks": sinks,
        "legality_candidates": legal,
        "storage_arithmetic": storage_arithmetic(),
        "dynamic_evidence": dynamic_evidence(),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        action="append",
        dest="roots",
        help="repository-relative source root; repeatable",
    )
    parser.add_argument(
        "--analyzed-revision",
        help="revision label bound to separately hashed working-tree inputs",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--check",
        type=Path,
        help="fail if generated JSON differs from this artifact",
    )
    args = parser.parse_args()
    roots = tuple(args.roots or DEFAULT_ROOTS)
    result = build_inventory(roots, args.analyzed_revision)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.check:
        expected = args.check.read_text(encoding="utf-8")
        if expected != rendered:
            raise SystemExit(f"inventory differs from {args.check}")
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    elif not args.check:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
