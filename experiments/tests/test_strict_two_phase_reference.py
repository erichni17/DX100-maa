from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_default_off_and_actual_p16_q16_issue_boundaries() -> None:
    simobject = text("src/mem/MAA/MAA.py")
    options = text("configs/common/Options.py")
    config = text("configs/common/MAAConfig.py")
    indirect = text("src/mem/MAA/IndirectAccess.cc")
    page_state = text("include/gem5/maa_page_fed_soa_abi.hh")
    assert (
        "virtual_strict_two_phase = Param.Bool(\n        False," in simobject
    )
    assert '"--maa_virtual_strict_two_phase"' in options
    assert 'opts["virtual_strict_two_phase"]' in config
    assert "strictTwoPhaseOperation()" in indirect
    assert "strictPageFedTwoPhaseOperation()" in indirect
    assert ".aIssue(curTick())" in indirect
    assert "authorizeAIssue" in indirect
    assert "strict page-fed A packet issue rejected" in indirect
    assert "authorizeAIssue" in page_state
    assert "!closed() || !executing()" in page_state


def test_exact_admission_backing_and_compact_timing_are_fail_closed() -> None:
    source = text("src/mem/MAA/IndirectAccess.cc")
    maa = text("src/mem/MAA/MAA.cc")
    for token in (
        "raw_b_retained_bytes=0",
        "descriptor_backing_bytes=0",
        "replay_passes=0",
        "coherent_ack=1",
        "event=strict_two_phase_timing",
        "event=strict_page_fed_two_phase_timing",
        "B_FETCH_BEGIN=",
        "ROW_OFFSET_LAST_INSERT=",
        "A_FIRST_ISSUE=",
        "BACKING_LAST_ACK=",
        "PAGE_LAST_READY=",
        "CONSUMER_END=",
        "order_ok=1 terminal=1",
    ):
        assert token in source or token in maa
    assert "strict two-phase cannot retain all" in source
    assert "strict two-phase physical RowTable" in source
    assert "virtual_idealized_write_ack" in maa
    assert "ResultCapacityTooLarge" in text(
        "src/mem/MAA/StrictTwoPhaseReference.hh"
    )


def test_negative_early_a_unit_is_executable() -> None:
    unit = text("tests/maa/strict_two_phase_reference_test.cc")
    runner = text("experiments/scripts/strict_two_phase/run_reference_unit.sh")
    assert "directReferenceActivelyRejectsEarlyA" in unit
    assert "pageFedPacketFenceRejectsEarlyA" in unit
    assert "authorizeAIssue(3) == PageResult::EarlyExecution" in unit
    assert "aIssue(101) == Result::EarlyAIssue" in unit
    assert "-fsanitize=address,undefined" in runner
    assert "optimized sanitize" in runner


def test_actual_cg_runner_requires_primary_nonfused_p16_q16_windows() -> None:
    runner = text(
        "experiments/scripts/strict_two_phase/"
        "run_cg_fused_p16_q16_strict.py"
    )
    primary = text(
        "experiments/scripts/strict_two_phase/"
        "run_cg_page_fed_p16_q16_strict.py"
    )
    assert "Primary Scott-style non-fused" in primary
    assert 'default="page-fed"' in runner
    assert '"page_fed_product_soa_jit"' in runner
    assert '"primary_simple_nonfused_reference"' in runner
    assert '"fusion_matched_diagnostic_only"' in runner
    assert "--maa_virtual_strict_two_phase" in runner
    assert "strict_cg_p16_q16_window" in runner
    assert "strict_page_fed_two_phase_timing" in runner
    assert "fused_p16_product_complete" in runner
    assert 'row.get("direct4") == "0"' in runner
    assert 'integer(row, "p_product_page_responses") == 4' in runner
    assert "strict_product_page_response" in runner
    assert 'integer(row, "q_product_deliveries") == 16384' in runner
    assert '"cg_numerical_terminal": True' in runner
    assert '"native_runs": 0' in runner
    assert "NA=1024 requires --confirm-from accepted NA=256 root" in runner
