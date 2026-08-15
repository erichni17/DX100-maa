from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "experiments/scripts/run_gzp_result_context_gate.py"


def source() -> str:
    return RUNNER.read_text()


def test_gate_is_two_replica_context_only_and_uncapped():
    runner = source()
    assert "REPLICAS = (1, 2)" in runner
    assert 'ARMS = (("control", 32), ("treatment", 64))' in runner
    assert '"timeout_seconds": 0' in runner
    assert "subprocess.run(command, timeout=" not in runner
    assert '"only_treatment": "maa_soa_jit_active_contexts:32->64"' in runner
    assert "refusing evidence run from a dirty source tree" in runner


def test_gate_freezes_accepted_controls_and_checkpoint():
    runner = source()
    assert '"--maa_soa_jit_active_value_owners=64"' in runner
    assert 'pre_a = "--maa_soa_jit_pre_a_value_lookahead"' in runner
    assert (
        '"fixed_controls": "masked_index=1,pre_a=1,value_owners=64"' in runner
    )
    assert "tree_sha256(CHECKPOINT)" in runner
    assert "EXPECTED_SELECTOR_HASH" in runner


def test_gate_requires_exact_guest_and_hardware_ledgers():
    runner = source()
    for contract in (
        "EXPECTED_OUTPUT_HASH",
        "UME_REFERENCE_PASS",
        "UME_GZP_MASKED_INDEX_LEDGER",
        "IND_SoaJitAReadResponses",
        "IND_SoaJitAWriteResponses",
        "IND_SoaJitValueReadResponses",
        "IND_SoaJitTerminalCompletions",
        "fixed_result_payload_bytes",
        "incremental_result_total_nonpayload_bytes_vs_32",
        "incremental_result_total_state_bytes_vs_32",
    ):
        assert contract in runner
    assert 'virtual_trace.log").read_text' not in runner
    assert 'virtual_trace.log").open(errors="replace")' in runner
