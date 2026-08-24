from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def test_fixed_two_region_payload_and_default_off_selection():
    pipeline = read("src/mem/MAA/SoaJitResultPipeline.hh")
    source = read("src/mem/MAA/IndirectAccess.cc")
    options = read("configs/common/Options.py")

    for contract in (
        "Regions = 2",
        "LinesPerRegion = 32",
        "RegionPayloadBytes == 2048",
        "FixedPayloadBytes == 4096",
        "incrementalPayloadBytesVsBaseline",
    ):
        assert contract in pipeline
    assert (
        "default=8"
        in options[options.index('"--maa_soa_jit_active_contexts"') :]
    )
    assert "choices=(8, 16, 32, 64)" in options
    assert "SoaJitContexts == SoaJitResultPipeline::MaxLines" in source
    assert "result payload must remain exactly 4 KiB" in source


def test_live_path_observes_exact_context_and_compact_credit_transitions():
    source = read("src/mem/MAA/IndirectAccess.cc")
    assert source.count("observeSoaJitResultPipeline();") >= 4
    assert source.index(
        "context->state = SoaJitContextState::AwaitARead"
    ) < source.index("observeSoaJitResultPipeline();")
    assert "context.state = SoaJitContextState::AwaitAWriteResp;" in source
    assert "soa_jit_write_retirement.awaitingResponses()" in source
    assert "curTick(), reads, writes, compact_writes" in source
    assert (
        "context.state = SoaJitContextState::Active;\n            observeSoaJitResultPipeline();"
        in source
    )
    assert (
        "context = SoaJitContext();\n    observeSoaJitResultPipeline();"
        in source
    )
    assert "soa_jit_a_write_responses++;" in source


def test_terminal_trace_closes_payload_overlap_and_byte_ledgers():
    source = read("src/mem/MAA/IndirectAccess.cc")
    assert "!soa_jit_result_pipeline.assertInvariants(" in source
    for field in (
        "fixed_result_payload_bytes",
        "active_result_payload_bytes",
        "fixed_lookahead_value_payload_bytes",
        "active_lookahead_value_payload_bytes",
        "incremental_lookahead_value_payload_bytes_vs_32",
        "fixed_max_transient_write_payload_bytes",
        "active_max_transient_write_payload_bytes",
        "incremental_max_transient_write_payload_bytes_vs_32",
        "fixed_result_contexts_bytes",
        "fixed_result_nonpayload_bytes",
        "incremental_result_contexts_bytes_vs_32",
        "incremental_result_nonpayload_bytes_vs_32",
        "incremental_result_waiter_mask_bytes_vs_32",
        "incremental_result_total_nonpayload_bytes_vs_32",
        "incremental_result_total_state_bytes_vs_32",
        "read_write_overlap_ticks",
        "dual_region_overlap_ticks",
        "serialized_write_only_ticks",
        "compact_write_hwm_total",
        "compact_region_attribution=none",
        "compact_write_outstanding_ticks",
        "terminal=1",
    ):
        assert field in source


def test_micro_is_two_rep_shared_checkpoint_exact_control_treatment():
    runner = read("experiments/scripts/run_soa_jit_result_pipeline_micro.sh")
    assert "for repetition in 1 2" in runner
    assert "for contexts in 32 64" in runner
    assert '--maa_soa_jit_active_contexts="$contexts"' in runner
    assert "--maa_soa_jit_active_value_owners=64" in runner
    assert "timeout_seconds=${DX100_TIMEOUT_SECONDS:-0}" in runner
    assert 'launcher=(timeout "$timeout_seconds")' in runner
    assert "checkpoint=" in runner
    assert "expected_hash=2761840269561229581" in runner
    assert "fixed_result_payload_bytes" in runner
    assert "fixed_lookahead_value_payload_bytes" in runner
    assert "fixed_max_transient_write_payload_bytes" in runner
    assert "fixed_result_contexts_bytes" in runner
    assert "incremental_result_nonpayload_bytes_vs_32" in runner
    assert "incremental_result_waiter_mask_bytes_vs_32" in runner
    assert "incremental_result_total_state_bytes_vs_32" in runner
    assert "read_write_overlap_ticks" in runner
    assert "dual_region_overlap_ticks" in runner
    assert "SOA_JIT_RESULT_PIPELINE_EVIDENCE_COMPLETE" in runner
