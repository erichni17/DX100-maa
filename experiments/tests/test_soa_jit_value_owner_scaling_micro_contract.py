from pathlib import Path


def test_value_owner_sweep_is_exact_bounded_and_uncapped_by_default():
    root = Path(__file__).resolve().parents[2]
    runner = (
        root / "experiments/scripts/run_soa_jit_value_owner_scaling_micro.sh"
    ).read_text(encoding="utf-8")

    assert "for owners in 32 64 128 256" in runner
    assert "--maa_soa_jit_active_contexts=32" in runner
    assert "--maa_soa_jit_value_lookahead=8" in runner
    assert "timeout_seconds=${DX100_TIMEOUT_SECONDS:-0}" in runner
    assert "if [[ $timeout_seconds -gt 0 ]]" in runner
    assert "IND_SoaJitValueReadIssues" in runner
    assert "IND_SoaJitValueReadResponses" in runner
    assert "IND_SoaJitAWriteResponses" in runner
    assert "output_hash=" in runner
    assert "active_value_owner_payload_bytes" in runner
    assert "SOA_JIT_VALUE_OWNER_SCALING_MICRO_PASS" in runner
