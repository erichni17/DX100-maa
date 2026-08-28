#!/usr/bin/env python3
"""Audit cross-application eligibility for CG strict line retirement.

The default action is deliberately read-only.  It proves the production
instruction/dataflow shape, records the application-specific storage contract,
and can revalidate existing evidence.  It never launches gem5 or a native
binary.  ``--launch-full`` is a fail-closed request: none of the audited
applications uses the CG virtual-result producer, so the request is rejected
with the application's exact non-applicability reason.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DEFAULT_EVIDENCE = {
    "is": Path(
        "/data1/nier/dx100-runs/"
        "2026-08-26-is-scalar-soa-full-certificate-r1"
    ),
    "hashjoin-pro": Path(
        "/data1/nier/dx100-runs/" "2026-08-24-hashjoin-pro-hardened-r1"
    ),
    "hashjoin-prh": Path(
        "/data1/nier/dx100-runs/" "2026-08-24-hashjoin-prh-hardened-r1"
    ),
    "sssp": Path(
        "/data1/nier/dx100-runs/" "2026-08-25-sssp-coherent-full-s22-r2"
    ),
}

BOUNDED_EVIDENCE = {
    "is": Path(
        "/data1/nier/dx100-runs/" "2026-08-24-is-scalar-soa-smoke-2a0bc33c-r1"
    ),
    "hashjoin": Path(
        "/data1/nier/dx100-runs/" "2026-08-24-hashjoin-hybrid-small-a77f77f1"
    ),
    "sssp": Path(
        "/data1/nier/dx100-runs/" "2026-08-25-sssp-coherent-small-fullcache-r2"
    ),
}


class ContractError(RuntimeError):
    """A fail-closed source, evidence, or launch-contract violation."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source(relative: str) -> str:
    path = ROOT / relative
    if not path.is_file() or path.is_symlink():
        raise ContractError(f"missing or unsafe production source: {relative}")
    return path.read_text(encoding="utf-8")


def require_tokens(relative: str, text: str, tokens: tuple[str, ...]) -> None:
    missing = [token for token in tokens if token not in text]
    if missing:
        raise ContractError(
            f"{relative} lost required contract tokens: {missing}"
        )


def source_contract() -> dict[str, Any]:
    """Bind the matrix to the executable production paths, not labels."""
    files = {
        "simulator": "src/mem/MAA/IndirectAccess.cc",
        "instruction": "src/mem/MAA/IF.hh",
        "instruction_access": "src/mem/MAA/IF.cc",
        "parameters": "src/mem/MAA/MAA.py",
        "old_result": "src/mem/MAA/SoaJitOldResultBuffer.hh",
        "api": "benchmarks/API/MAA_gem5.hpp",
        "is": "benchmarks/NAS/is/is.cpp",
        "hashjoin": "benchmarks/hashjoin/src/parallel_radix_join.cpp",
        "sssp": "benchmarks/gapbs/src/sssp.cc",
    }
    texts = {name: source(path) for name, path in files.items()}

    require_tokens(
        files["parameters"],
        texts["parameters"],
        (
            "virtual_masked_writes = Param.Bool(\n        False",
            "virtual_strict_two_phase = Param.Bool(\n        False",
        ),
    )
    require_tokens(
        files["simulator"],
        texts["simulator"],
        (
            "maa->virtual_strict_two_phase && isVirtualLoad()",
            "isDirectIndexLoad() && !isSoaJitRmw()",
            "maa->virtual_strict_two_phase && isSoaJitPageFedRmw()",
            "if (virtual_masked_writes && slot.valid_words != 0",
            "insertVirtualCombineWord",
            "private feeder copy populated from the B memory",
            "direct_index_words.erase(word)",
            "req->setByteEnable(byte_enable)",
            "soa_jit_old_result_buffer.acknowledge(identity)",
        ),
    )
    require_tokens(
        files["instruction"],
        texts["instruction"],
        (
            "isSoaJitScalarRmw()",
            "isSoaJitVectorRmw()",
            "hasSoaJitOldResult()",
        ),
    )
    require_tokens(
        files["instruction_access"],
        texts["instruction_access"],
        (
            "if (hasSoaJitOldResult())",
            "append(resultAddrRangeID, AccessType::WRITE)",
        ),
    )
    require_tokens(
        files["old_result"],
        texts["old_result"],
        (
            "LineBytes = 64",
            "original logical ordinal",
            "validWords",
            "WrongMask",
            "AwaitingResponse",
        ),
    )
    require_tokens(
        files["api"],
        texts["api"],
        (
            "maa_indirect_rmw_scalar_soa_jit",
            "no old-value result",
            "maa_indirect_rmw_vector_soa_jit_old_result",
            "old_values",
        ),
    )
    require_tokens(
        files["is"],
        texts["is"],
        (
            "maa_indirect_rmw_scalar_soa_jit<int>",
            "key_buff_ptr2 + i",
            "nullptr, scalar, minimum, maximum, stride",
            "predicate_words=0 value_words=0",
            "host_spd_reads=0 staging_bytes=0",
        ),
    )
    require_tokens(
        files["hashjoin"],
        texts["hashjoin"],
        (
            "HASHJOIN_HYBRID_LOGICAL_ELEMENTS = 16384",
            "HASHJOIN_HYBRID_PHYSICAL_ELEMENTS = 4096",
            "hybrid_soa_indices[lane] = HASH_BIT_MODULO(",
            "maa_indirect_rmw_scalar_soa_jit<int32_t>",
            "hybrid_soa_indices_base",
            "HASHJOIN_HYBRID_RESULT result=%ld",
        ),
    )
    if (
        texts["hashjoin"].count("maa_indirect_rmw_scalar_soa_jit<int32_t>")
        != 2
    ):
        raise ContractError("HashJoin must retain exactly two histogram sites")
    require_tokens(
        files["sssp"],
        texts["sssp"],
        (
            "maa_publish_spd_page_logical16_response_bearing<uint32_t>",
            "maa_publish_spd_page_logical16_response_bearing<WeightT>",
            "maa_indirect_rmw_vector_soa_jit_old_result(",
            "sssp_hybrid_old_results[tid][lane]",
            "page_finals.emplace(",
            "hidden_result_payload_bytes=0",
        ),
    )

    return {
        "status": "PASS",
        "files": {
            name: {
                "path": relative,
                "sha256": sha256_file(ROOT / relative),
            }
            for name, relative in files.items()
        },
    }


def application_matrix() -> list[dict[str, Any]]:
    common_inapplicability = (
        "The CG treatment retires results from INDIR_LD_VIRTUAL_INDEX through "
        "insertVirtualCombineWord. This application issues a SoA/JIT RMW and "
        "has no virtual-gather result backing for --maa_virtual_masked_writes."
    )
    return [
        {
            "application": "NAS IS",
            "family": "is",
            "instruction": "maa_indirect_rmw_scalar_soa_jit<int>",
            "virtual_producer": (
                "No virtual result producer. The direct feeder reads each "
                "registered key_array word as the histogram index."
            ),
            "backing_path": (
                "key_array/key_buff_ptr2 is coherent index input; the scalar "
                "one is captured in a register; key_buff1_work is mutable A."
            ),
            "numeric_b": {
                "private_feeder_copy_dead_after_descriptor_insert": True,
                "application_reads_after_completion": False,
                "classification": "dead_after_row_offset_admission",
            },
            "result_semantics": {
                "old_result_required": False,
                "result_backing_required": False,
                "completion_only_tile": True,
            },
            "masked_64b_retirement": {
                "cg_virtual_retirement_applicable": False,
                "masked_64b_legal": None,
                "classification": "not_applicable_no_virtual_result",
                "note": (
                    "The histogram path already reads and rewrites coherent A "
                    "lines; that full-line RMW is not CG result retirement."
                ),
            },
            "strict_plus_masked_applicable": False,
            "non_applicability_reason": common_inapplicability,
            "bounded_evidence": str(BOUNDED_EVIDENCE["is"]),
            "full_evidence": str(DEFAULT_EVIDENCE["is"]),
        },
        {
            "application": "HashJoin PRO",
            "family": "hashjoin-pro",
            "instruction": "maa_indirect_rmw_scalar_soa_jit<int32_t>",
            "virtual_producer": (
                "No virtual result producer. Host code computes radix bucket "
                "indices into one registered per-thread coherent arena."
            ),
            "backing_path": (
                "hybrid_soa_indices is direct index input; scalar one is "
                "captured; histR/histS is mutable A. PRO has no shifted "
                "histogram pass."
            ),
            "numeric_b": {
                "private_feeder_copy_dead_after_descriptor_insert": True,
                "application_reads_after_completion": False,
                "classification": "dead_after_row_offset_admission",
            },
            "result_semantics": {
                "old_result_required": False,
                "result_backing_required": False,
                "completion_only_tile": True,
            },
            "masked_64b_retirement": {
                "cg_virtual_retirement_applicable": False,
                "masked_64b_legal": None,
                "classification": "not_applicable_no_virtual_result",
                "note": "Histogram A-line writes are ordinary full-line RMWs.",
            },
            "strict_plus_masked_applicable": False,
            "non_applicability_reason": common_inapplicability,
            "bounded_evidence": str(BOUNDED_EVIDENCE["hashjoin"]),
            "full_evidence": str(DEFAULT_EVIDENCE["hashjoin-pro"]),
        },
        {
            "application": "HashJoin PRH",
            "family": "hashjoin-prh",
            "instruction": "maa_indirect_rmw_scalar_soa_jit<int32_t>",
            "virtual_producer": (
                "The producer is identical to PRO: host-computed radix "
                "indices feed scalar SoA/JIT histogram RMWs."
            ),
            "backing_path": (
                "hybrid_soa_indices is reused after each completion; histR/"
                "histS is mutable A. The accepted full shifted pass is "
                "tail-only, so it contributes no second 16K producer."
            ),
            "numeric_b": {
                "private_feeder_copy_dead_after_descriptor_insert": True,
                "application_reads_after_completion": False,
                "classification": "dead_after_row_offset_admission",
            },
            "result_semantics": {
                "old_result_required": False,
                "result_backing_required": False,
                "completion_only_tile": True,
            },
            "masked_64b_retirement": {
                "cg_virtual_retirement_applicable": False,
                "masked_64b_legal": None,
                "classification": "not_applicable_no_virtual_result",
                "note": "Histogram A-line writes are ordinary full-line RMWs.",
            },
            "strict_plus_masked_applicable": False,
            "non_applicability_reason": common_inapplicability,
            "bounded_evidence": str(BOUNDED_EVIDENCE["hashjoin"]),
            "full_evidence": str(DEFAULT_EVIDENCE["hashjoin-prh"]),
        },
        {
            "application": "GAPBS SSSP",
            "family": "sssp",
            "instruction": "maa_indirect_rmw_vector_soa_jit_old_result",
            "virtual_producer": (
                "Four response-bearing physical-4K STREAM_ST publications "
                "materialize coherent index and value pages before the direct "
                "SoA/JIT MIN RMW; this is not INDIR_LD_VIRTUAL_INDEX."
            ),
            "backing_path": (
                "sssp_hybrid_indices, values, and predicates are coherent "
                "producer backing; sssp_hybrid_old_results is a separate "
                "64-byte-aligned architectural result span."
            ),
            "numeric_b": {
                "private_feeder_copy_dead_after_descriptor_insert": True,
                "application_reads_after_completion": True,
                "classification": (
                    "not_dead_at_application_boundary: indices and values are "
                    "reread to reconstruct page-local frontier winners"
                ),
            },
            "result_semantics": {
                "old_result_required": True,
                "result_backing_required": True,
                "completion_only_tile": True,
                "reason": (
                    "Each original logical ordinal needs its pre-update dist "
                    "for the legacy candidate==final && old>final test."
                ),
            },
            "masked_64b_retirement": {
                "cg_virtual_retirement_applicable": False,
                "masked_64b_legal": True,
                "same_mechanism_as_cg": False,
                "classification": "legal_distinct_old_result_publisher",
                "note": (
                    "SoaJitOldResultBuffer emits 64-byte WriteReqs with per-"
                    "word byte enables and exact generation/sequence/address/"
                    "mask WriteResp identity. It is independent of "
                    "--maa_virtual_masked_writes and cannot be removed."
                ),
            },
            "strict_plus_masked_applicable": False,
            "non_applicability_reason": (
                "SSSP's producer is response-bearing physical-page publish, "
                "and its result is a semantically live old-result stream. The "
                "CG virtual-result combiner would target the wrong producer "
                "and cannot replace the old-result backing."
            ),
            "bounded_evidence": str(BOUNDED_EVIDENCE["sssp"]),
            "full_evidence": str(DEFAULT_EVIDENCE["sssp"]),
        },
    ]


def validate_evidence(roots: dict[str, Path]) -> dict[str, Any]:
    """Reuse the established read-only full-evidence validators."""
    from experiments.scripts import audit_hybrid_goal_completion as audit

    return {
        "is": audit.audit_is(roots["is"]),
        "hashjoin-pro": audit.audit_hashjoin(roots["hashjoin-pro"], "PRO"),
        "hashjoin-prh": audit.audit_hashjoin(roots["hashjoin-prh"], "PRH"),
        "sssp": audit.audit_sssp(roots["sssp"]),
    }


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    if path.exists():
        raise ContractError(f"refusing existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists():
        raise ContractError(f"refusing stale temporary output: {temporary}")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def build_result(
    roots: dict[str, Path], with_evidence: bool
) -> dict[str, Any]:
    matrix = application_matrix()
    return {
        "schema": "dx100.general_strict_linecombined_matrix.v1",
        "read_only": True,
        "native_runs": 0,
        "candidate_full_runs": 0,
        "treatment_defaults_off": True,
        "source_contract": source_contract(),
        "cg_reference": {
            "instruction": "INDIR_LD_VIRTUAL_INDEX",
            "producer": "A[B[i]] virtual gather to coherent result backing",
            "numeric_b_private_copy_dead_after_admission": True,
            "result_backing_required_until_consumer": True,
            "strict_order": "A_FIRST_ISSUE >= ROW_OFFSET_LAST_INSERT",
            "masked_retirement": (
                "64-byte virtual result writes with per-word byte enables"
            ),
            "evidence": (
                "experiments/analysis/"
                "strict_two_phase_cg_reference_2026-08-27.md"
            ),
        },
        "applications": matrix,
        "applicable_full_families": [],
        "launch_policy": {
            "default": "off",
            "decision": "NO_NEW_FULL_LAUNCH",
            "reason": (
                "No audited family reaches the CG virtual-result retirement "
                "edge. Existing candidate-only correctness evidence is reused; "
                "the live SSSP full run must not be duplicated."
            ),
        },
        "evidence_validation": (
            validate_evidence(roots) if with_evidence else "not_requested"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--validate-evidence",
        action="store_true",
        help="revalidate the existing full evidence roots read-only",
    )
    parser.add_argument(
        "--launch-full",
        choices=tuple(DEFAULT_EVIDENCE),
        action="append",
        default=[],
        help=(
            "request a full CG strict+masked treatment; all current choices "
            "fail closed as non-applicable"
        ),
    )
    for name, default in DEFAULT_EVIDENCE.items():
        parser.add_argument("--" + name + "-root", type=Path, default=default)
    args = parser.parse_args(argv)
    roots = {
        name: getattr(args, name.replace("-", "_") + "_root")
        for name in DEFAULT_EVIDENCE
    }
    matrix = {row["family"]: row for row in application_matrix()}
    if args.launch_full:
        reasons = [
            f"{family}: {matrix[family]['non_applicability_reason']}"
            for family in args.launch_full
        ]
        parser.error(
            "full launch rejected before execution; " + " | ".join(reasons)
        )
    try:
        result = build_result(roots, args.validate_evidence)
        if args.output is not None:
            output = args.output.resolve()
            if output == ROOT or ROOT in output.parents:
                raise ContractError("output must be outside the source tree")
            atomic_json(output, result)
        print(json.dumps(result, sort_keys=True))
    except ContractError as error:
        print(f"contract error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
