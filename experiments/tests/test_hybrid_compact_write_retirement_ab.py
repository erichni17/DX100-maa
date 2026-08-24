from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = "experiments/scripts/run_hybrid_compact_write_retirement_ab.sh"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_runner_is_exact_candidate_only_context8_ab():
    runner = read(RUNNER)
    assert "native_reruns=0" in runner
    assert "full_run_roots_touched=0" in runner
    assert "--maa_soa_jit_active_contexts=8" in runner
    assert "--maa_soa_jit_compact_write_retirement" in runner
    assert "--maa_num_tile_elements=16384" in runner
    assert "--maa_physical_tile_elements=4096" in runner
    assert "--maa_soa_jit_active_value_owners=64" in runner
    assert 'timeout "$' not in runner
    assert "launcher=(timeout" not in runner
    assert "run_pair sssp" in runner
    assert "run_pair hashjoin" in runner


def test_runner_freezes_inputs_binary_checkpoints_and_ramulator():
    runner = read(RUNNER)
    for contract in (
        "status --porcelain --untracked-files=all",
        "source_tree=$(git -C \"$root\" rev-parse 'HEAD^{tree}')",
        'source_archive_sha=$(git -C "$root" archive --format=tar HEAD',
        "offline_dependency_source=",
        "ramulator_spdlog_directory_sha256=",
        "ramulator_yaml_cpp_directory_sha256=",
        "util_m5op_s_sha256=",
        "gem5_sha=$(sha256sum",
        "expected_ramulator_sha="
        "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753",
        "b92252492af0fbae8b3a27d2e57d403cbbc2f03b830090ae767f50cac8904c3c",
        "9137ca242beb2b5a451ca592021047dfdf6da5f35efc53f34844c7d87de9f299",
        "checkpoint_identity",
        'sssp_checkpoint_after == "$sssp_checkpoint_before"',
        'hashjoin_checkpoint_after == "$hashjoin_checkpoint_before"',
        'realpath "$resolved_ramulator"',
    ):
        assert contract in runner


def test_runner_fail_closes_certificates_ledgers_and_gate():
    runner = read(RUNNER)
    for contract in (
        "SSSP_FINGERPRINT vertices=69633 reached=69633",
        "HASHJOIN_HYBRID_RESULT result=65536",
        "IND_SoaJitTerminalCompletions",
        "IND_SoaJitOldResultWriteResponses",
        "IND_SoaJitAReadResponses",
        "IND_SoaJitAWriteResponses",
        "IND_BoundedGlobalMergeFallbacks",
        "IND_SoaJitCompactWriteRetirementEnabled",
        "IND_SoaJitCompactWriteRetirementPersistentBits",
        "IND_SoaJitCompactWriteTransientPayloadHighWaterBytes",
        "performance_metric=first_simTicks",
        "at_least_one_kernel_regressed",
        "no_kernel_improved_at_least_0_5_pct",
        "terminal=pass",
    ):
        assert contract in runner


def test_runner_separates_persistent_hardware_and_transient_payload():
    runner = read(RUNNER)
    assert "persistent_tracker_bits_per_indirect_unit=1168" in runner
    assert "persistent_tracker_bytes_per_indirect_unit=146" in runner
    assert "persistent_tracker_bytes_four_units=584" in runner
    assert "transient_response_credit_tag_bits_per_packet=3" in runner
    assert (
        "max_transient_response_credit_tag_bits_per_indirect_unit=24" in runner
    )
    assert (
        "max_transient_response_credit_tag_bytes_per_indirect_unit=3" in runner
    )
    assert "max_transient_packet_payload_bytes_per_indirect_unit=512" in runner
    assert (
        "sender_state_mapping=credit_tag_indexes_persistent_tracker" in runner
    )
    assert (
        "sender_state_duplicate_fields="
        "generation_sequence_address_are_tracker_validation_metadata" in runner
    )
