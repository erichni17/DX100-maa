from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_context64_remains_default_off_after_rejection():
    simobject = read("src/mem/MAA/MAA.py")
    options = read("configs/common/Options.py")
    assert "soa_jit_active_contexts = Param.Unsigned(\n        8," in simobject
    flag = options[options.index('"--maa_soa_jit_active_contexts"') :]
    assert "default=8" in flag[:500]
    assert "choices=(8, 16, 32, 64)" in flag[:500]


def test_small_ab_is_candidate_only_and_changes_one_geometry_knob():
    runner = read("experiments/scripts/run_hybrid_context64_small_ab.sh")
    assert "native_reruns=0" in runner
    assert "full_run_roots_touched=0" in runner
    assert "for contexts in 8 64" in runner
    assert '--maa_soa_jit_active_contexts="$contexts"' in runner
    assert "--maa_num_tile_elements=16384" in runner
    assert "--maa_physical_tile_elements=4096" in runner
    assert "--maa_soa_jit_active_value_owners=64" in runner
    assert "--maa_soa_jit_value_cache_enable" in runner
    assert "--maa_soa_jit_pre_a_value_lookahead" in runner


def test_small_ab_fail_closes_correctness_accounting_and_performance():
    runner = read("experiments/scripts/run_hybrid_context64_small_ab.sh")
    for marker in (
        "SSSP_FINGERPRINT vertices=69633 reached=69633",
        "HASHJOIN_HYBRID_RESULT result=65536",
        "IND_SoaJitTerminalCompletions",
        "IND_SoaJitOldResultCaptures",
        "IND_SoaJitOldResultWriteResponses",
        "IND_SoaJitAReadResponses",
        "IND_SoaJitAWriteResponses",
        "IND_SoaJitPreAValueUses",
        "IND_BoundedGlobalMergeFallbacks",
        "treatment -ge $control",
        'checkpoint_after == "$sssp_checkpoint_before"',
        'hashjoin_checkpoint_after == "$hashjoin_checkpoint_before"',
        "performance_metric=first_simTicks",
        "incremental_bytes_per_indirect_unit=30464",
        "incremental_bytes_four_units=121856",
        "incremental_a_lookahead_payload_bytes_four_units=28672",
        "incremental_fraction_of_873_28_kib_lower_bound_pct=13.6",
        "expected_ramulator_sha=76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753",
        'realpath "$resolved_ramulator"',
        "search_banking=none",
        "timing_qualified_3_2ghz=false",
        "at_least_one_kernel_not_faster",
        "terminal=pass\\ndecision=%s",
    ):
        assert marker in runner
