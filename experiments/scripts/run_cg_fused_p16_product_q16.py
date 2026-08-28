#!/usr/bin/env python3
"""Run the exact cache-on bounded p16/q16 control/fused pair.

One deterministic guest and one immutable checkpoint feed two serial restores.
CG_NA=256 is the accepted successor screen.  CG_NA=1024 is available only when
``--confirm-from`` revalidates that exact successor raw root, authority, and
source schema.  No native or full workload is accepted.  Correctness,
mechanism ledgers, and the exact config delta close before first-ROI simTicks
is compared.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE_PATH = ROOT / "experiments/scripts/run_cg_direct4_product_page_fed_q16.py"
SPEC = importlib.util.spec_from_file_location("cg_direct4_gate", BASE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load base gate: {BASE_PATH}")
direct = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(direct)
base = direct.base

SCREEN_CG_NA = 256
CONFIRM_CG_NA = 1024
CG_NA = SCREEN_CG_NA
ACTIVE_CG_NA = SCREEN_CG_NA
EXPECTED_WINDOWS = {SCREEN_CG_NA: 10, CONFIRM_CG_NA: 65}
SUCCESSOR_AUTHORITY = (
    ROOT / "experiments/analysis/fused_p16_product_successor_2026-08-26.json"
)
SUCCESSOR_AUTHORITY_SHA256 = (
    "10cd9cf72458f9e8abf2ac1d399abdd9dba171b46d5ba8938a44704d14be9a7d"
)
SUCCESSOR_SOURCE_COMMIT = "4a4d91b8f176c33779804fbd163014593d89e737"
SUCCESSOR_GEM5_SHA256 = (
    "271836b58d02d9d50a658cd5c7628e15559ca22d3a04477ab15475e3744dfd2e"
)
SUCCESSOR_GEM5 = Path(
    "/data1/nier/worktrees/codex-sessions/"
    "hybrid-fused-p16-product-evidence-repair-2026082-20260826-160656-"
    "c4f154c5/DX100-virtualization-selected-integration-cont-20260826/"
    "build/X86/gem5.opt"
)
SUCCESSOR_SOURCE_SCHEMA_PATHS = (
    "src",
    "configs",
    "benchmarks",
    "include",
    "util",
)
GEM5 = SUCCESSOR_GEM5
RAMULATOR = base.RAMULATOR
ARMS = (
    ("control", "page_fed_product_soa_jit"),
    ("candidate", "fused_p16_product_q16"),
)
FINITE_KNOBS = (
    "--maa_virtual_combine_slots=16",
    "--maa_virtual_combine_ways=4",
    "--maa_virtual_combine_banks=4",
    "--maa_virtual_words_per_cycle=1",
    "--maa_virtual_response_slots=8",
    "--maa_virtual_response_words=0",
    "--maa_virtual_response_word_pool=0",
    "--maa_virtual_max_outstanding_writes=32",
    "--maa_soa_jit_value_prefetch_credits=0",
)
COMMON_STAT_SCHEMA = (
    "simTicks",
    "IND_SoaJitInstructions",
    "IND_SoaJitSelected",
    "IND_SoaJitAliasesApplied",
    "IND_SoaJitValueReadIssues",
    "IND_SoaJitValueReadResponses",
    "IND_SoaJitValueFills",
    "IND_SoaJitValueHits",
    "IND_SoaJitValueMergedWaiters",
    "IND_SoaJitValueDeliveries",
    "IND_SoaJitAReadIssues",
    "IND_SoaJitAReadResponses",
    "IND_SoaJitAWriteIssues",
    "IND_SoaJitAWriteResponses",
    "IND_SoaJitPageFedOperations",
    "IND_SoaJitPageFedAdmitCommands",
    "IND_SoaJitPageFedCloseCommands",
    "IND_SoaJitPageFedCommandResponses",
    "IND_SoaJitPageFedAdmittedWords",
    "IND_SoaJitPageFedSpdIndexReads",
    "IND_SoaJitPageFedRowWrites",
    "IND_SoaJitPageFedCoherentIndexReadLines",
    "IND_SoaJitPageFedCoherentIndexWriteLines",
    "IND_NumOTEpochDrain",
    "IND_SoaJitEpochDrains",
    "IND_BoundedGlobalMergeFallbacks",
    "STR_PublishIssues",
    "STR_PublishWriteResponses",
)
FUSED_STAT_SCHEMA = (
    "IND_FusedP16Operations",
    "IND_FusedP16Epochs",
    "IND_FusedP16SourceOrdinals",
    "IND_FusedP16CoefficientReadIssues",
    "IND_FusedP16CoefficientReadResponses",
    "IND_FusedP16CoefficientFills",
    "IND_FusedP16CoefficientDeliveries",
    "IND_FusedP16MulAccepts",
    "IND_FusedP16MulCompletions",
    "IND_FusedP16ProductInsertions",
    "IND_FusedP16ProductWriteCompletions",
    "IND_FusedP16EpochDrains",
    "IND_FusedP16Fallbacks",
    "IND_FusedP16PublisherLines",
    "IND_FusedP16VirtualPBytes",
)
REQUIRED_STAT_SCHEMA = (*COMMON_STAT_SCHEMA, *FUSED_STAT_SCHEMA)
REQUIRED_STAT_SCHEMA_SIZE = 43
HARDWARE_ACCOUNTING = {
    "selected_indirect_units": 4,
    "selected_if_slots": 32,
    "response_substate_bytes_total": 32,
    "lifecycle_semantic_bytes_total": 68,
    "lifecycle_cpp_bound_bytes_total": 96,
    "descriptor_closure_semantic_bytes_total": 32,
    "descriptor_closure_cpp_bound_bytes_total": 256,
    "tagged_alu_state_bytes": 8,
    "candidate_control_state_semantic_bytes_total": 140,
    "candidate_control_state_conservative_bytes_total": 392,
    "descriptor_payload_bytes_delta": 0,
    "row_offset_payload_bytes_delta": 0,
    "external_ports_delta": 0,
    "new_multipliers": 0,
    "guest_coherent_backing_bytes_delta": -262144,
    "virtual_p_write_bytes_removed_per_window": 65536,
    "virtual_p_read_bytes_removed_per_window": 65536,
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def parse_kv(line: str) -> dict[str, str]:
    return dict(field.split("=", 1) for field in line.split() if "=" in field)


def integer(fields: dict[str, str], name: str) -> int:
    try:
        return int(fields[name])
    except (KeyError, ValueError) as error:
        raise RuntimeError(f"missing integer terminal field {name}") from error


def verify_raw_root(root: Path, expected_digest: str) -> str:
    """Rehash the accepted root and bind its immutable ledger to its gate."""
    root = root.resolve()
    ledger = root / "raw_root.sha256"
    gate = root / "gate.complete"
    require(
        ledger.is_file() and gate.is_file(), f"incomplete raw root: {root}"
    )
    seen: set[Path] = set()
    for number, line in enumerate(
        ledger.read_text(encoding="utf-8").splitlines(), 1
    ):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        require(match is not None, f"malformed raw ledger line {number}")
        relative = Path(match.group(2))  # type: ignore[union-attr]
        require(
            not relative.is_absolute()
            and ".." not in relative.parts
            and relative not in seen,
            "unsafe or duplicate raw ledger path",
        )
        seen.add(relative)
        artifact = root / relative
        require(
            artifact.is_file()
            and not artifact.is_symlink()
            and base.sha256_file(artifact) == match.group(1),  # type: ignore[union-attr]
            f"raw ledger mismatch for {relative}",
        )
    require(len(seen) == 54, f"successor raw ledger has {len(seen)}/54 files")
    require(
        {
            Path("result.json"),
            Path("input/artifact_sha256.before"),
            Path("input/artifact_sha256.after"),
            Path("input/checkpoint_files.before"),
            Path("input/checkpoint_files.after"),
            Path("control/stats.txt"),
            Path("candidate/stats.txt"),
        }.issubset(seen),
        "successor raw ledger omits required evidence",
    )
    digest = base.sha256_file(ledger)
    require(digest == expected_digest, "successor raw ledger digest changed")
    gate_lines = gate.read_text(encoding="utf-8").splitlines()
    require(
        gate_lines.count("COMPLETE_CG_FUSED_P16_PRODUCT_Q16") == 1
        and gate_lines.count("decision=ACCEPT") == 1
        and gate_lines.count("correctness=EXACT_MATCH") == 1
        and gate_lines.count(f"raw_root_sha256={digest}") == 1,
        "successor gate does not bind an exact accepted raw ledger",
    )
    return digest


def validate_current_source_schema(source_commit: str) -> None:
    """Require content identity to the accepted source, not branch identity."""
    object_check = subprocess.run(
        ["git", "cat-file", "-e", f"{source_commit}^{{commit}}"],
        cwd=ROOT,
        check=False,
    )
    require(object_check.returncode == 0, "successor source commit is absent")
    source_diff = subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            source_commit,
            "--",
            *SUCCESSOR_SOURCE_SCHEMA_PATHS,
        ],
        cwd=ROOT,
        check=False,
    )
    require(
        source_diff.returncode == 0,
        "current simulator/guest/config source differs from successor schema",
    )


def validate_confirmation_document(
    root: Path, authority: dict, result: dict, ledger_sha: str
) -> None:
    """Validate the pinned authority and its terminal NA=256 result schema."""
    accepted = authority.get("cg_na256", {})
    performance = result.get("performance", {})
    require(
        authority.get("schema")
        == "dx100.analysis.fused_p16_product_successor.v1"
        and authority.get("decision") == "ACCEPT_BOUNDED_SUCCESSOR"
        and authority.get("bounded_successor_authority") is True
        and authority.get("general_promotion") is False
        and authority.get("native_runs") == 0
        and authority.get("full_cg_runs") == 0
        and authority.get("source_commit") == SUCCESSOR_SOURCE_COMMIT
        and authority.get("gem5_sha256") == SUCCESSOR_GEM5_SHA256
        and accepted.get("root") == str(root.resolve())
        and accepted.get("raw_root_sha256") == ledger_sha
        and accepted.get("required_stat_schema_fields")
        == REQUIRED_STAT_SCHEMA_SIZE
        and accepted.get("required_stat_schema_present") is True
        and accepted.get("fingerprints_exact_equal") is True
        and accepted.get("deterministic_reductions_exact_equal") is True
        and accepted.get("decision") == "ACCEPT",
        "successor authority does not authorize this NA=256 root",
    )
    require(
        result.get("schema") == "dx100.cg.fused_p16_product_q16.v1"
        and result.get("terminal") is True
        and result.get("decision") == "ACCEPT"
        and result.get("native_runs") == 0
        and result.get("full_cg_runs") == 0
        and result.get("cg_na") == SCREEN_CG_NA
        and result.get("source_commit") == SUCCESSOR_SOURCE_COMMIT
        and result.get("gem5_sha256") == SUCCESSOR_GEM5_SHA256
        and result.get("ramulator_sha256") == authority.get("ramulator_sha256")
        and result.get("fingerprints_exact_equal") is True
        and result.get("deterministic_reductions_exact_equal") is True
        and result.get("p16_epochs") == EXPECTED_WINDOWS[SCREEN_CG_NA]
        and result.get("required_stat_schema") == list(REQUIRED_STAT_SCHEMA)
        and len(result.get("required_stat_schema", []))
        == REQUIRED_STAT_SCHEMA_SIZE
        and result.get("required_stat_schema_present") is True
        and result.get("virtual_p_bytes") == 0
        and result.get("publisher_lines") == 0
        and result.get("fallbacks") == 0
        and performance.get("metric") == "simTicks"
        and performance.get("control")
        == accepted.get("performance", {}).get("control")
        and performance.get("candidate")
        == accepted.get("performance", {}).get("candidate")
        and performance.get("candidate", 0) < performance.get("control", 0),
        "successor result is not the exact/faster 43-field NA=256 gate",
    )


def require_confirmation(
    root: Path, authority_path: Path = SUCCESSOR_AUTHORITY
) -> dict[str, str]:
    """Authorize NA=1024 only from the hardened successor NA=256 root."""
    require(
        base.sha256_file(authority_path) == SUCCESSOR_AUTHORITY_SHA256,
        "successor authority JSON changed",
    )
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    expected_digest = authority.get("cg_na256", {}).get("raw_root_sha256")
    require(
        isinstance(expected_digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", expected_digest) is not None,
        "successor authority has no valid NA=256 raw-root digest",
    )
    root = root.resolve()
    ledger_sha = verify_raw_root(root, expected_digest)
    result = json.loads((root / "result.json").read_text(encoding="utf-8"))
    validate_confirmation_document(root, authority, result, ledger_sha)

    require(
        (root / "input/source_commit.before")
        .read_text(encoding="utf-8")
        .strip()
        == SUCCESSOR_SOURCE_COMMIT,
        "successor root source identity changed",
    )
    artifact_before = root / "input/artifact_sha256.before"
    artifact_after = root / "input/artifact_sha256.after"
    checkpoint_before = root / "input/checkpoint_files.before"
    checkpoint_after = root / "input/checkpoint_files.after"
    require(
        artifact_before.read_bytes() == artifact_after.read_bytes()
        and base.sha256_file(artifact_before)
        == authority["cg_na256"]["artifact_ledger_sha256"],
        "successor artifact ledger is not immutable",
    )
    require(
        checkpoint_before.read_bytes() == checkpoint_after.read_bytes()
        and base.sha256_file(checkpoint_before)
        == authority["cg_na256"]["checkpoint_ledger_sha256"],
        "successor checkpoint ledger is not immutable",
    )
    for relative in (
        "checkpoint.log.exit",
        "control/restore.log.exit",
        "candidate/restore.log.exit",
    ):
        require(
            (root / relative).read_text(encoding="utf-8").strip() == "0",
            f"successor child failed: {relative}",
        )
    validate_current_source_schema(SUCCESSOR_SOURCE_COMMIT)
    return {
        "root": str(root),
        "raw_root_sha256": ledger_sha,
        "authority_sha256": SUCCESSOR_AUTHORITY_SHA256,
        "source_commit": SUCCESSOR_SOURCE_COMMIT,
        "gem5_sha256": SUCCESSOR_GEM5_SHA256,
    }


def require_config(config: Path, combine_slots: int = 16) -> None:
    lines = config.read_text(errors="replace").splitlines()
    required = {
        "page_fed_soa_jit=true",
        "num_maas=1",
        "num_indirect_units_per_maa=4",
        "num_tiles_per_core=8",
        "num_tile_elements=16384",
        "physical_tile_elements=4096",
        "num_offset_table_entries=16384",
        "num_offset_table_epoch_entries=16384",
        "num_initial_row_table_slices=32",
        f"virtual_combine_slots={combine_slots}",
        "virtual_combine_ways=4",
        "virtual_combine_banks=4",
        "virtual_words_per_cycle=1",
        "virtual_response_slots=8",
        "virtual_response_words=0",
        "virtual_response_word_pool=0",
        "virtual_max_outstanding_writes=32",
        "soa_jit_value_cache_enable=true",
        "soa_jit_active_value_owners=32",
        "soa_jit_value_prefetch_credits=0",
    }
    require(
        not required.difference(lines),
        f"resolved config missing {sorted(required.difference(lines))}",
    )
    controllers = sum(
        bool(re.fullmatch(r"\[system\.mem_ctrls[01]\]", line))
        for line in lines
    )
    require(
        controllers == 2, f"expected two memory channels, saw {controllers}"
    )


def require_terminal(fields: dict[str, str], treatment: str) -> int:
    names = (
        "full_windows",
        "staged_index_words",
        "staged_value_words",
        "product_words",
        "index_publish_pages",
        "value_publish_pages",
        "product_publish_pages",
        "logical_alu_vectors",
        "physical_alu_vectors",
        "logical_page_windows",
        "physical_page_product_windows",
        "page_fed_product_windows",
        "fused_p16_product_windows",
        "direct4_product_page_fed_q16_windows",
        "virtual_p_gather_windows",
        "physical_p_gather_pages",
        "page_fed_admit_pages",
        "page_fed_closes",
        "q_spmv_eligible_windows",
        "q_spmv_routed_windows",
        "residual_spmv_eligible_windows",
        "residual_spmv_routed_windows",
        "external_coherent_backing_bytes",
        "physical_spd_payload_bytes",
        "host_payload_access",
        "coherent_index_backing_bytes",
        "virtual_p_backing_bytes",
        "virtual_p_allocation_bytes",
        "virtual_p_write_bytes",
        "virtual_p_read_bytes",
        "product_publisher_lines",
        "virtual_backing_traffic_eliminated",
        "p16_reorder_preserved",
        "q16_reorder_preserved",
        "hidden_spill_bytes",
        "global_fallbacks",
    )
    values = {name: integer(fields, name) for name in names}
    windows = values["full_windows"]
    words = windows * 16384
    pages = windows * 4
    expected = EXPECTED_WINDOWS[ACTIVE_CG_NA]
    common = (
        windows == expected
        and values["staged_index_words"] == words
        and values["staged_value_words"] == 0
        and values["product_words"] == words
        and values["index_publish_pages"] == 0
        and values["value_publish_pages"] == 0
        and values["logical_alu_vectors"] == 0
        and values["logical_page_windows"] == 0
        and values["physical_page_product_windows"] == 0
        and values["direct4_product_page_fed_q16_windows"] == 0
        and values["page_fed_admit_pages"] == pages
        and values["page_fed_closes"] == windows
        and values["q_spmv_eligible_windows"]
        == values["q_spmv_routed_windows"]
        and values["residual_spmv_eligible_windows"]
        == values["residual_spmv_routed_windows"]
        and values["q_spmv_routed_windows"]
        + values["residual_spmv_routed_windows"]
        == windows
        and values["physical_spd_payload_bytes"] == 524288
        and values["host_payload_access"] == 0
        and values["coherent_index_backing_bytes"] == 0
        and values["p16_reorder_preserved"] == 1
        and values["q16_reorder_preserved"] == 1
        and values["hidden_spill_bytes"] == 0
        and values["global_fallbacks"] == 0
    )
    if treatment == "page_fed_product_soa_jit":
        exact = (
            fields.get("p_gather_mode") == "virtual_16k"
            and values["page_fed_product_windows"] == windows
            and values["fused_p16_product_windows"] == 0
            and values["physical_alu_vectors"] == pages
            and values["product_publish_pages"] == pages
            and values["product_publisher_lines"] == pages * 256
            and values["virtual_p_gather_windows"] == windows
            and values["physical_p_gather_pages"] == 0
            and values["virtual_p_backing_bytes"] == 262144
            and values["virtual_p_allocation_bytes"] > 0
            and values["virtual_p_write_bytes"] == windows * 65536
            and values["virtual_p_read_bytes"] == windows * 65536
            and values["virtual_backing_traffic_eliminated"] == 0
            and values["external_coherent_backing_bytes"] == 524288
        )
    else:
        exact = (
            fields.get("p_gather_mode") == "fused_virtual_16k_product"
            and values["page_fed_product_windows"] == 0
            and values["fused_p16_product_windows"] == windows
            and values["physical_alu_vectors"] == 0
            and values["product_publish_pages"] == 0
            and values["product_publisher_lines"] == 0
            and values["virtual_p_gather_windows"] == 0
            and values["physical_p_gather_pages"] == 0
            and values["virtual_p_backing_bytes"] == 0
            and values["virtual_p_allocation_bytes"] == 0
            and values["virtual_p_write_bytes"] == 0
            and values["virtual_p_read_bytes"] == 0
            and values["virtual_backing_traffic_eliminated"] == 1
            and values["external_coherent_backing_bytes"] == 262144
        )
    require(
        common and windows == expected and exact,
        f"terminal closure failed: {fields}",
    )
    return windows


def require_stat_schema(stats: Path, names: tuple[str, ...]) -> dict[str, int]:
    """Read every first-ROI stat; absent/renamed fields are fatal."""
    return {name: base.stat_sum(stats, name) for name in names}


def require_stats(stats: Path, windows: int, treatment: str) -> dict[str, int]:
    values = require_stat_schema(stats, COMMON_STAT_SCHEMA)
    words = windows * 16384
    pages = windows * 4
    expected = EXPECTED_WINDOWS[ACTIVE_CG_NA]
    q_closed = (
        windows == expected
        and values["simTicks"] > 0
        and values["IND_SoaJitInstructions"] == windows
        and values["IND_SoaJitSelected"] == words
        and values["IND_SoaJitAliasesApplied"] == words
        and values["IND_SoaJitValueDeliveries"] == words
        and values["IND_SoaJitValueReadIssues"]
        == values["IND_SoaJitValueReadResponses"]
        and values["IND_SoaJitValueReadResponses"]
        == values["IND_SoaJitValueFills"]
        and values["IND_SoaJitValueReadIssues"]
        + values["IND_SoaJitValueHits"]
        + values["IND_SoaJitValueMergedWaiters"]
        == words
        and values["IND_SoaJitAReadIssues"] > 0
        and values["IND_SoaJitAReadIssues"]
        == values["IND_SoaJitAReadResponses"]
        and values["IND_SoaJitAReadIssues"] == values["IND_SoaJitAWriteIssues"]
        and values["IND_SoaJitAWriteIssues"]
        == values["IND_SoaJitAWriteResponses"]
        and values["IND_SoaJitPageFedOperations"] == windows
        and values["IND_SoaJitPageFedAdmitCommands"] == pages
        and values["IND_SoaJitPageFedCloseCommands"] == windows
        and values["IND_SoaJitPageFedCommandResponses"] == windows * 5
        and values["IND_SoaJitPageFedAdmittedWords"] == words
        and values["IND_SoaJitPageFedSpdIndexReads"] == words
        and values["IND_SoaJitPageFedRowWrites"] == words
        and values["IND_SoaJitPageFedCoherentIndexReadLines"] == 0
        and values["IND_SoaJitPageFedCoherentIndexWriteLines"] == 0
        and values["IND_NumOTEpochDrain"] == 0
        and values["IND_SoaJitEpochDrains"] == 0
        and values["IND_BoundedGlobalMergeFallbacks"] == 0
    )
    require(q_closed, f"q16 stats closure failed: {values}")

    values.update(require_stat_schema(stats, FUSED_STAT_SCHEMA))
    if treatment == "fused_p16_product_q16":
        issues = values["IND_FusedP16CoefficientReadIssues"]
        fused_closed = (
            values["IND_FusedP16Operations"] == windows
            and values["IND_FusedP16Epochs"] == windows
            and values["IND_FusedP16SourceOrdinals"] == words
            and 1024 * windows <= issues <= words
            and values["IND_FusedP16CoefficientReadResponses"] == issues
            and values["IND_FusedP16CoefficientFills"] == issues
            and values["IND_FusedP16CoefficientDeliveries"] == words
            and values["IND_FusedP16MulAccepts"] == words
            and values["IND_FusedP16MulCompletions"] == words
            and values["IND_FusedP16ProductInsertions"] == words
            and values["IND_FusedP16ProductWriteCompletions"] == words
            and all(
                values[name] == 0
                for name in (
                    "IND_FusedP16EpochDrains",
                    "IND_FusedP16Fallbacks",
                    "IND_FusedP16PublisherLines",
                    "IND_FusedP16VirtualPBytes",
                    "STR_PublishIssues",
                    "STR_PublishWriteResponses",
                )
            )
        )
    else:
        fused_closed = all(values[name] == 0 for name in FUSED_STAT_SCHEMA)
        fused_closed = fused_closed and (
            values["STR_PublishIssues"] == pages * 256
            and values["STR_PublishWriteResponses"] == pages * 256
        )
    require(fused_closed, f"producer stats closure failed: {values}")
    return values


def restore_args(
    guest: Path, selector: Path, checkpoint: Path, arm: Path
) -> list[str]:
    direct.base.GEM5 = GEM5
    args = direct.restore_args(
        guest, selector, checkpoint, arm, value_cache=True
    )
    require(
        args.count("--maa_soa_jit_value_cache_enable") == 1,
        "restore must enable the selected cache exactly once",
    )
    require(
        args.count("--maa_soa_jit_active_value_owners=32") == 1,
        "restore must select the 32-owner pool exactly once",
    )
    command_index = args.index("--cmd")
    args[command_index:command_index] = list(FINITE_KNOBS)
    return args


def normalized_config(path: Path) -> str:
    normalized = []
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("host_paths=") and "/fs/" in line:
            normalized.append(
                "host_paths=<ARM>/fs/" + line.rsplit("/fs/", 1)[1]
            )
        else:
            normalized.append(line)
    return "\n".join(normalized) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--cg-na", type=int, default=CG_NA)
    parser.add_argument("--confirm-from", type=Path)
    args = parser.parse_args(argv)
    global ACTIVE_CG_NA
    if args.cg_na not in (SCREEN_CG_NA, CONFIRM_CG_NA):
        parser.error(
            "this gate accepts only CG_NA=256 or confirmed CG_NA=1024; native/full are forbidden"
        )
    if args.cg_na == CONFIRM_CG_NA and args.confirm_from is None:
        parser.error("CG_NA=1024 requires --confirm-from accepted NA=256 root")
    if args.cg_na == SCREEN_CG_NA and args.confirm_from is not None:
        parser.error("--confirm-from is valid only with CG_NA=1024")
    ACTIVE_CG_NA = args.cg_na
    if ACTIVE_CG_NA == CONFIRM_CG_NA:
        require_confirmation(args.confirm_from)
    out = args.out.resolve()
    require(
        out != ROOT and ROOT not in out.parents,
        "output must be outside the source worktree",
    )
    require(
        not out.exists() or not any(out.iterdir()),
        f"refusing nonempty output: {out}",
    )
    require(
        GEM5.is_file() and os.access(GEM5, os.X_OK),
        f"missing current gem5 {GEM5}",
    )
    before_status = base.source_status()
    require(
        len(before_status.splitlines()) == 1,
        "refusing evidence from a dirty source worktree",
    )
    before_commit = base.source_commit()

    input_dir = out / "input"
    checkpoint = out / "checkpoint"
    input_dir.mkdir(parents=True)
    checkpoint.mkdir()
    selector = input_dir / "treatment.selector"
    selector.write_text("token_stream_ld page_fed_product_soa_jit\n")
    selector.chmod(0o444)
    guest = out / "cg_fused_p16_product_q16_guest"
    compile_args = [
        os.environ.get("CXX", "g++"),
        f"-I{ROOT / 'benchmarks/API'}",
        f"-I{ROOT / 'include'}",
        f"-I{ROOT / 'util/m5/src'}",
        "-std=c++17",
        "-O3",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wno-ignored-qualifiers",
        "-Wno-unused-parameter",
        "-Wno-unused-function",
        "-fopenmp",
        "-DGEM5",
        "-DMAA",
        "-DMAA_VIRTUAL_GATHER",
        "-DMAA_GENERAL_VIRTUAL_CONSUMER",
        "-DMAA_CONSUMER_TILE_SIZE=4096",
        "-DCG_LOGICAL16_RMW",
        "-DCG_LOGICAL_PAGE_RMW",
        "-DCG_PHYSICAL_PAGE_PRODUCT_ONLY",
        "-DCG_PAGE_FED_SOA_ONLY",
        "-DCG_FP_ENABLE",
        "-DCG_DETERMINISTIC_REDUCTIONS",
        "-DCG_REDUCTION_EVIDENCE",
        f"-DCG_NA={ACTIVE_CG_NA}",
        "-DNUM_CORES=4",
        "-DNUM_TILES_PER_CORE=8",
        "-DTILE_SIZE=16384",
        "-DMAA_MEM_SIZE=0x80000000",
        str(ROOT / "util/m5/src/abi/x86/m5op.S"),
        str(base.SOURCE),
        "-o",
        str(guest),
    ]
    subprocess.run(compile_args, cwd=ROOT, check=True)

    immutable = (
        GEM5,
        RAMULATOR,
        guest,
        Path(__file__).resolve(),
        BASE_PATH,
        *base.GUEST_COMPILE_INPUTS,
        *base.RUNNER_CONFIG_INPUTS[1:],
    )
    artifacts_before = base.artifact_ledger(immutable)
    (input_dir / "artifact_sha256.before").write_text(artifacts_before)
    (input_dir / "source_status.before").write_text(before_status)
    (input_dir / "source_commit.before").write_text(before_commit + "\n")
    (input_dir / "compile_command.json").write_text(
        json.dumps(compile_args, indent=2) + "\n"
    )
    environment = dict(
        os.environ,
        LD_LIBRARY_PATH=str(RAMULATOR.parent)
        + ":"
        + os.environ.get("LD_LIBRARY_PATH", ""),
        OMP_NUM_THREADS="4",
        OMP_PROC_BIND="false",
    )
    ldd = subprocess.check_output(
        ["ldd", str(GEM5)], env=environment, text=True
    )
    match = re.search(r"^[ \t]*libramulator\.so => (\S+)", ldd, re.M)
    require(
        match is not None
        and Path(match.group(1)).resolve() == RAMULATOR.resolve(),
        "current gem5 did not resolve frozen Ramulator",
    )

    checkpoint_args = [
        str(GEM5),
        "--listener-mode=off",
        f"--outdir={checkpoint}",
        str(base.CONFIG),
        "--cpu-type",
        "AtomicSimpleCPU",
        "-n",
        "4",
        "--mem-size",
        "2GB",
        "--max-checkpoints=1",
        "--cmd",
        str(guest),
        "--options",
        f"MAA_DEFERRED {selector}",
    ]
    (input_dir / "checkpoint_command.json").write_text(
        json.dumps(checkpoint_args, indent=2) + "\n"
    )
    base.run_logged(checkpoint_args, out / "checkpoint.log", environment)
    base.exactly_one(
        (out / "checkpoint.log").read_text(errors="replace").splitlines(),
        r"^Exiting @ tick [0-9]+ because checkpoint$",
        "checkpoint terminal",
    )
    checkpoint_before = base.tree_ledger(checkpoint)
    (input_dir / "checkpoint_files.before").write_text(checkpoint_before)

    parsed: dict[str, dict] = {}
    commands: dict[str, list[str]] = {}
    for name, treatment in ARMS:
        selector.chmod(0o644)
        selector.write_text(f"token_stream_ld {treatment}\n")
        selector.chmod(0o444)
        arm = out / name
        arm.mkdir()
        (arm / "selector.txt").write_text(selector.read_text())
        command = restore_args(guest, selector, checkpoint, arm)
        commands[name] = command
        base.run_logged(command, arm / "restore.log", environment)
        lines = (arm / "restore.log").read_text(errors="replace").splitlines()
        require(
            not any(base.FATAL_RE.search(line) for line in lines),
            f"{name} contains fatal text",
        )
        base.exactly_one(
            lines,
            r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$",
            f"{name} terminal",
        )
        fingerprint = base.exactly_one(
            lines,
            rf"^CG_FINGERPRINT mode=MAA elements={ACTIVE_CG_NA} .* result=PASS$",
            f"{name} fingerprint",
        )
        terminal_line = base.exactly_one(
            lines,
            rf"^CG_LOGICAL16_RMW_TERMINAL treatment={treatment} "
            r".* result=PASS$",
            f"{name} treatment terminal",
        )
        evidence = [
            line
            for line in lines
            if line.startswith(
                ("CG_REDUCTION_EVIDENCE ", "CG_OUTER_REDUCTION_EVIDENCE ")
            )
        ]
        require(
            len(evidence) == 11, f"{name} has {len(evidence)}/11 reductions"
        )
        terminal = parse_kv(terminal_line)
        windows = require_terminal(terminal, treatment)
        require_config(arm / "config.ini")
        stats = require_stats(arm / "stats.txt", windows, treatment)
        parsed[name] = {
            "fingerprint_line": fingerprint,
            "reduction_evidence": evidence,
            "terminal": terminal,
            "stats": stats,
        }

    control = parsed["control"]
    candidate = parsed["candidate"]
    require(
        control["fingerprint_line"] == candidate["fingerprint_line"],
        "raw/quantized fingerprint mismatch; performance is unreadable",
    )
    require(
        control["reduction_evidence"] == candidate["reduction_evidence"],
        "deterministic reduction mismatch; performance is unreadable",
    )
    require(
        control["terminal"]["full_windows"]
        == candidate["terminal"]["full_windows"],
        "p16 window count mismatch",
    )
    require(
        normalized_config(out / "control/config.ini")
        == normalized_config(out / "candidate/config.ini"),
        "resolved configs differ outside the guest selector",
    )

    checkpoint_after = base.tree_ledger(checkpoint)
    (input_dir / "checkpoint_files.after").write_text(checkpoint_after)
    require(checkpoint_before == checkpoint_after, "shared checkpoint changed")
    artifacts_after = base.artifact_ledger(immutable)
    (input_dir / "artifact_sha256.after").write_text(artifacts_after)
    require(artifacts_before == artifacts_after, "immutable artifact changed")
    require(
        base.source_status() == before_status
        and base.source_commit() == before_commit,
        "source identity changed during pair",
    )

    control_ticks = control["stats"]["simTicks"]
    candidate_ticks = candidate["stats"]["simTicks"]
    decision = "ACCEPT" if candidate_ticks < control_ticks else "REJECT"
    result = {
        "schema": "dx100.cg.fused_p16_product_q16.v1",
        "terminal": True,
        "decision": decision,
        "native_runs": 0,
        "full_cg_runs": 0,
        "cg_na": ACTIVE_CG_NA,
        "source_commit": before_commit,
        "gem5_sha256": base.sha256_file(GEM5),
        "ramulator_sha256": base.sha256_file(RAMULATOR),
        "guest_sha256": base.sha256_file(guest),
        "checkpoint_ledger_sha256": hashlib.sha256(
            checkpoint_before.encode()
        ).hexdigest(),
        "fingerprints_exact_equal": True,
        "deterministic_reductions_exact_equal": True,
        "p16_epochs": int(candidate["terminal"]["full_windows"]),
        "required_stat_schema": [*COMMON_STAT_SCHEMA, *FUSED_STAT_SCHEMA],
        "required_stat_schema_present": True,
        "virtual_p_bytes": 0,
        "publisher_lines": 0,
        "fallbacks": 0,
        "performance": {
            "metric": "simTicks",
            "control": control_ticks,
            "candidate": candidate_ticks,
            "control_over_candidate": control_ticks / candidate_ticks,
        },
        "restore_commands": commands,
        "arms": parsed,
    }
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    ledger_targets = [
        path
        for path in sorted(out.rglob("*"))
        if path.is_file()
        and path.name not in {"raw_root.sha256", "gate.complete"}
    ]
    (out / "raw_root.sha256").write_text(
        "".join(
            f"{base.sha256_file(path)}  {path.relative_to(out)}\n"
            for path in ledger_targets
        )
    )
    ledger_sha = base.sha256_file(out / "raw_root.sha256")
    (out / "gate.complete").write_text(
        "COMPLETE_CG_FUSED_P16_PRODUCT_Q16\n"
        f"decision={decision}\ncorrectness=EXACT_MATCH\n"
        f"raw_root_sha256={ledger_sha}\n"
    )
    print(
        json.dumps(
            {
                "terminal": True,
                "decision": decision,
                "control_simTicks": control_ticks,
                "candidate_simTicks": candidate_ticks,
                "raw_root_sha256": ledger_sha,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
