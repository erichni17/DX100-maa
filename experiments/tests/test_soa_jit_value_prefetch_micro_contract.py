from pathlib import Path


def test_value_prefetch_sweep_is_exact_bounded_and_uncapped_by_default():
    root = Path(__file__).resolve().parents[2]
    runner = (
        root / "experiments/scripts/run_soa_jit_value_prefetch_micro.sh"
    ).read_text(encoding="utf-8")

    assert "for credits in 0 1 2 4 8" in runner
    assert "--maa_soa_jit_active_value_owners=32" in runner
    assert '--maa_soa_jit_value_prefetch_credits="$credits"' in runner
    assert "timeout_seconds=${DX100_TIMEOUT_SECONDS:-0}" in runner
    assert "IND_SoaJitValuePrefetchIssues" in runner
    assert "IND_SoaJitValuePrefetchResponses" in runner
    assert "IND_SoaJitValuePrefetchPromotions" in runner
    assert "IND_SoaJitValuePrefetchDiscards" in runner
    assert "pfi" in runner and "pfr" in runner
    assert "output_hash=" in runner
    assert "SOA_JIT_VALUE_PREFETCH_MICRO_PASS" in runner
