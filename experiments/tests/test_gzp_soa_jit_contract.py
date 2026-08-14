#!/usr/bin/env python3
"""Static contract checks for the correctness-first GZP integration."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_gzp_uses_exact_published_predicate_and_completion() -> None:
    source = text("benchmarks/UME/gradzatp.cpp")
    assert "corner_predicate_soa[i] = corner_type[i] > 0 ? 1U : 0U" in source
    assert "hash_soa_predicates(corner_predicate_soa)" in source
    assert "phase=pre_checkpoint" in source
    assert "volume_soa_jit" in source
    assert "corner_volume.data() + c" in source
    assert "corner_predicate_soa.data() + c" in source
    assert "const uint32_t selected = tile_cond_ptr[i] != 0" in source
    assert "soa_predicates[omp_thread_id]" in source
    assert "wait_ready(tile2);" in source
    assert source.index("wait_ready(tile2);") < source.index(
        "DATATYPE *tile2_ptr"
    )
    assert "0x7fc00001U" in source
    assert "wait_ready(soa_volume_completion_tiles" in source
    assert "wait_ready(soa_gradient_completion_tiles" in source
    assert "if (soa_both_full_window)" in source
    assert "if (soa_volume_only_full_window)" in source
    assert "gather_size == TILE_SIZE" in source


def test_runner_is_provenance_frozen_and_execution_gated() -> None:
    runner = text("experiments/scripts/run_gzp_soa_jit_correctness.py")
    for required in (
        "lead-optimized-gem5-sha256",
        "source_identity",
        "copy_stable_artifact",
        "config_tree",
        "tree_identity",
        '"simulated_metric": "simTicks"',
        '"host_time_metric_authorized": False',
        "token_stream_ld legacy_4k",
        "token_stream_ld volume_soa_jit",
        "token_stream_ld soa_jit",
        "extra-gem5-arg",
        "restore-arm-gem5-arg",
        '"gem5_args": gem5_args',
        "current_hybrid_vs_volume_only_soa_jit",
    ):
        assert required in runner


def test_analyzer_gates_volume_performance_and_staging_correctness() -> None:
    analyzer = text("experiments/analysis/analyze_gzp_soa_jit_correctness.py")
    for required in (
        "UME_GZP_TERMINAL",
        "IND_SoaJitTerminalCompletions",
        "IND_SoaJitPredicateLineResponses",
        "IND_SoaJitAWriteResponses",
        "validate_soa_trace",
        "EXPECTED_VOLUME_SOA_INSTRUCTIONS = 61",
        '"volume_only_windows": str(EXPECTED_FULL_WINDOWS)',
        '"current_hybrid_vs_volume_only_soa_jit": True',
        '"current_hybrid_vs_soa_jit_correctness": False',
        '"host_time_used": False',
        "speedup_current_over_volume_only",
    ):
        assert required in analyzer
    assert "host time is not used" in analyzer.lower()
