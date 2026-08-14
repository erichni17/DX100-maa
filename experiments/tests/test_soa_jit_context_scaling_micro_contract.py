from pathlib import Path


def test_context_scaling_sweep_is_exact_and_closes_all_ledgers():
    text = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_soa_jit_context_scaling_micro.sh"
    ).read_text()
    for contexts in ("8", "16", "32"):
        assert f"for contexts in 8 16 32" in text
    assert '--checkpoint-dir="$checkpoint"' in text
    assert "--maa_soa_jit_predicate_active_credits=16" in text
    assert "--maa_soa_jit_active_value_owners=32" in text
    assert "--maa_soa_jit_value_lookahead=8" in text
    assert "--maa_virtual_index_buffer_lines=8" in text
    assert "--maa_soa_jit_apply_lanes=1" in text
    assert "expected_hash=2761840269561229581" in text
    for ledger in (
        "predicate_ledger",
        "value_ledger",
        "fill_ledger",
        "issue_delivery_ledger",
        "response_delivery_ledger",
        "aread_ledger",
        "awrite_ledger",
        "terminal_ledger",
    ):
        assert ledger in text
    assert "fixed_contexts_bytes" in text
    assert "active_contexts_bytes" in text
