from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def between(source: str, first: str, last: str) -> str:
    begin = source.index(first)
    return source[begin : source.index(last, begin)]


def test_default_off_knob_is_wired_end_to_end():
    simobject = read("src/mem/MAA/MAA.py")
    options = read("configs/common/Options.py")
    config = read("configs/common/MAAConfig.py")
    maa_header = read("src/mem/MAA/MAA.hh")
    maa_source = read("src/mem/MAA/MAA.cc")
    indirect_header = read("src/mem/MAA/IndirectAccess.hh")
    indirect_source = read("src/mem/MAA/IndirectAccess.cc")

    assert (
        "soa_jit_pre_a_value_lookahead = Param.Bool(\n        False,"
        in simobject
    )
    assert '"--maa_soa_jit_pre_a_value_lookahead"' in options
    assert 'opts["soa_jit_pre_a_value_lookahead"]' in config
    assert "bool soa_jit_pre_a_value_lookahead" in maa_header
    assert "p.soa_jit_pre_a_value_lookahead" in maa_source
    assert "_soa_jit_pre_a_value_lookahead" in indirect_header
    assert "_soa_jit_pre_a_value_lookahead" in indirect_source


def test_claimed_row_issues_exact_lookahead_only_after_identity_and_a_read():
    source = read("src/mem/MAA/IndirectAccess.cc")
    build = between(
        source,
        "bool IndirectAccessUnit::serviceSoaJitBuild()",
        "IndirectAccessUnit::issueSoaJitValueRead",
    )
    identity = build.index("context->generation = soa_jit_generation")
    await_a = build.index("context->state = SoaJitContextState::AwaitARead")
    a_read = build.index("createReadPacket(addr, rowtable_latency)")
    early_fill = build.index("fillSoaJitLookahead(context_index)")
    assert identity < await_a < a_read < early_fill
    assert "if (soa_jit_pre_a_value_lookahead)" in build

    issue = between(
        source,
        "IndirectAccessUnit::issueSoaJitValueRead",
        "IndirectAccessUnit::fillSoaJitLookahead",
    )
    assert "context.state == SoaJitContextState::AwaitARead" in issue
    assert "soa_jit_pre_a_value_lookahead && pre_a" in issue
    assert "soa_jit_pre_a_value_issues++" in issue
    assert "soa_jit_value_prefetch" not in issue


def test_pre_a_delivery_cannot_apply_before_authenticated_a_response():
    source = read("src/mem/MAA/IndirectAccess.cc")
    service = between(
        source,
        "IndirectAccessUnit::serviceSoaJitLookahead",
        "IndirectAccessUnit::applySoaJitValue",
    )
    assert "context.state == SoaJitContextState::AwaitARead" in service
    apply_loop = service[service.index("const size_t apply_start") :]
    assert "context.state != SoaJitContextState::Active" in apply_loop
    assert "AwaitARead" not in apply_loop
    assert "soa_jit_pre_a_value_uses++" in apply_loop

    receive = between(
        source,
        "bool IndirectAccessUnit::receiveSoaJitData",
        "bool IndirectAccessUnit::completeSoaJitWrite",
    )
    response = receive[
        receive.index(
            "context.state == SoaJitContextState::AwaitARead"
        ) : receive.index("Addr prefetch_vaddr")
    ]
    assert response.index("std::memcpy(context.aLine.data()") < response.index(
        "context.state = SoaJitContextState::Active"
    )
    assert response.index("context.preAUsesPending =") < response.index(
        "context.state = SoaJitContextState::Active"
    )
    assert "soa_jit_pre_a_value_ready_at_a_response" in response


def test_pre_a_counters_fail_closed_at_terminal_and_are_exported():
    source = read("src/mem/MAA/IndirectAccess.cc")
    header = read("src/mem/MAA/MAA.hh")
    stats = read("src/mem/MAA/MAA.cc")
    terminal = between(
        source,
        "void IndirectAccessUnit::checkSoaJitTerminal",
        "void IndirectAccessUnit::executeInstruction",
    )
    assert (
        "soa_jit_pre_a_value_issues !=\n                             soa_jit_pre_a_value_uses"
        in terminal
    )
    assert "soa_jit_pre_a_value_ready_at_a_response >" in terminal
    assert "soa_jit_pre_a_value_issues != 0" in terminal
    for name in (
        "IND_SoaJitPreAValueIssues",
        "IND_SoaJitPreAValueReadyAtAResponse",
        "IND_SoaJitPreAValueUses",
    ):
        assert name in header
        assert name in stats
        assert name in source


def test_paired_runner_uses_one_checkpoint_and_disables_sequential_prefetch():
    runner = read("experiments/scripts/run_soa_jit_pre_a_lookahead_matrix.sh")
    assert runner.count("make_checkpoint") == 2  # definition plus one call
    assert '"$out/checkpoint/soa16"' in runner
    assert "control_r1 control 1" in runner
    assert "treatment_r1 treatment 1" in runner
    assert "control_r2 control 2" in runner
    assert "treatment_r2 treatment 2" in runner
    assert "--maa_soa_jit_value_prefetch_credits=0" in runner
    assert "--maa_soa_jit_pre_a_value_lookahead" in runner
    assert "IND_SoaJitValuePrefetchIssues" in runner
    assert "IND_SoaJitPreAValueReadyAtAResponse" in runner
    assert "REJECT" in runner
