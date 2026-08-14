from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_owner_scaling_gate_is_shared_checkpoint_two_replica_and_default_off():
    runner = (
        ROOT / "experiments/scripts/run_soa_jit_value_owner_scaling_micro.sh"
    ).read_text()

    assert "owners_control=32" in runner
    assert "owners_treatment=64" in runner
    assert "run_arm control_r1 1 32; run_arm treatment_r1 1 64" in runner
    assert "run_arm control_r2 2 32; run_arm treatment_r2 2 64" in runner
    assert '"$out/checkpoint/soa16"' in runner
    assert "shared_checkpoint_m5_cpt_sha256" in runner
    assert "soa_jit_pre_a_value_lookahead=false" in runner
    assert "maa_soa_jit_value_prefetch_credits=0" in runner


def test_owner_scaling_gate_closes_traffic_and_rejects_nonbeneficial_capacity():
    runner = (
        ROOT / "experiments/scripts/run_soa_jit_value_owner_scaling_micro.sh"
    ).read_text()

    for counter in (
        "IND_SoaJitValueReadIssues",
        "IND_SoaJitValueReadResponses",
        "IND_SoaJitAReadIssues",
        "IND_SoaJitAWriteResponses",
        "IND_SoaJitValueEvictions",
        "IND_SoaJitContextStalls",
        "IND_SoaJitTerminalCompletions",
    ):
        assert counter in runner
    assert (
        "decision_requires_lower_simTicks_and_lower_evictions_in_both_replicas"
        in runner
    )
    assert "decision=REJECT" in runner
