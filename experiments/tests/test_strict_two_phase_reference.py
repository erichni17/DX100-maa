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
    assert "my_index_min < 0 || my_index_stride != 1" in indirect
    assert "my_index_min != 0 || my_index_stride" not in indirect
    assert "my_instruction->backingAddrRangeID < 0" in indirect
    assert "authorizeAIssue" in page_state
    assert "!closed() || !executing()" in page_state
    assert "strict_page_fed_terminal_recorded" in indirect
    assert "!strict_page_fed_terminal_recorded" in indirect
    terminal_begin = indirect.index(
        "void IndirectAccessUnit::checkSoaJitTerminal()"
    )
    terminal_end = indirect.index(
        "void\nIndirectAccessUnit::checkFusedP16Terminal()",
        terminal_begin,
    )
    terminal = indirect[terminal_begin:terminal_end]
    guard = terminal.index("!strict_page_fed_terminal_recorded")
    complete = terminal.index("completeStrictP16Q16Window")
    latch = terminal.index("strict_page_fed_terminal_recorded = true")
    assert guard < complete < latch


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


def test_strict_lifecycle_uses_current_identity_and_erases_every_owner() -> (
    None
):
    indirect = text("src/mem/MAA/IndirectAccess.cc")
    maa = text("src/mem/MAA/MAA.cc")
    header = text("src/mem/MAA/IndirectAccess.hh")

    fields = (
        "strict_page_fed_b_first_tick",
        "strict_page_fed_b_last_tick",
        "strict_page_fed_row_first_tick",
        "strict_page_fed_row_last_tick",
        "strict_page_fed_close_tick",
        "strict_page_fed_a_first_issue_tick",
        "strict_page_fed_a_last_issue_tick",
        "strict_page_fed_a_last_response_tick",
        "strict_page_fed_backing_first_issue_tick",
        "strict_page_fed_backing_last_issue_tick",
        "strict_page_fed_backing_last_ack_tick",
        "strict_page_fed_consumer_begin_tick",
        "strict_page_fed_consumer_end_tick",
    )
    for field in fields:
        assert f"Tick {field} = 0" in header
        assert f"{field} = 0;" in indirect
    assert "bool strict_page_fed_terminal_recorded = false" in header
    assert "strict_page_fed_terminal_recorded = false;" in indirect

    decode = indirect.index("my_base_addr = my_instruction->baseAddr")
    reset = indirect.index("strict_page_fed_b_first_tick = 0", decode)
    direct = indirect.index("if (isDirectIndexLoad())", reset)
    assert decode < reset < direct
    assert "my_instruction->backingAddrRangeID < 0" in indirect
    assert "virtualPageBackingAddr[tokenTileID]" in maa
    assert "my_instruction->core_id" in indirect

    for owner in (
        "strictTwoPhaseReferences",
        "strictTwoPhasePendingConsumerBegins",
        "strictP16ByQ16",
        "strictProductPageResponses",
    ):
        assert owner in maa
    assert "coreHasUnconsumedProducer" in maa
    assert "producerOwners != 1" in maa
    assert "strictTwoPhaseReferences.erase(timeline)" in maa
    assert "strictProductPageResponses.erase" in maa
    assert "strictP16ByQ16.erase(link)" in maa
    assert "strict p16/q16 terminal failed lifecycle erasure" in maa
    assert "strict two-phase token %d reused before producer/consumer" in maa

    # Unrelated indirect instructions cannot mutate strict state: both
    # predicates derive from the current instruction's opcode/mode.
    predicates = indirect[
        indirect.index(
            "bool IndirectAccessUnit::strictTwoPhaseOperation"
        ) : indirect.index("void IndirectAccessUnit::accountReadResponse")
    ]
    assert "isVirtualLoad()" in predicates
    assert "isDirectIndexLoad()" in predicates
    assert "isSoaJitPageFedRmw()" in predicates


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


def test_product_page_response_groups_allow_backing_reuse() -> None:
    from experiments.scripts.strict_two_phase.run_cg_fused_p16_q16_strict import (
        group_product_page_responses,
    )

    records = []
    for window in range(2):
        for page in range(4):
            records.append(
                {
                    "core": "3",
                    "backing": "0xc0000",
                    "page": str(page),
                    "generation": str(window * 4 + page + 1),
                    "pages": f"{page + 1}/4",
                }
            )
    groups = group_product_page_responses(records)
    assert len(groups[(3, 0xC0000)]) == 2
    assert all(len(group) == 4 for group in groups[(3, 0xC0000)])

    broken = list(records[:3]) + [dict(records[2])]
    try:
        group_product_page_responses(broken)
    except RuntimeError:
        pass
    else:
        raise AssertionError("duplicate product page was accepted")


def test_line_combined_arm_is_strict_and_same_checkpoint_matched() -> None:
    runner = text(
        "experiments/scripts/strict_two_phase/"
        "run_cg_strict_line_combined.py"
    )
    assert "verify_matched_root" in runner
    assert "--maa_virtual_masked_writes" in runner
    assert "--index-buffer-lines" in runner
    assert "--word-writes" in runner
    assert "--maa_virtual_index_buffer_lines=" in runner
    assert 'f"virtual_index_buffer_lines={args.index_buffer_lines}"' in runner
    assert 'choices=(1, 2, 4, 8, 16, 32, 64, 128)' in runner
    assert '"virtual_index_buffer_lines"' in runner
    assert '"VALID_STRICT_FEEDER_ATTRIBUTION"' in runner
    assert '"retirement_mode"' in runner
    assert "virtual_strict_two_phase=true" in runner
    assert "virtual_masked_writes=true" in runner
    assert "expected_write_bytes = 4 if args.word_writes else 64" in runner
    assert 'gate.integer(row, "bytes") == expected_write_bytes' in runner
    assert "len(writes) < expected_windows * 16384" in runner
    assert "gate.EXPECTED_WINDOWS[cg_na]" in runner
    assert '"native_runs": 0' in runner
    assert '"promotable": False' in runner
    assert '"execution_source_commit"' in text(
        "experiments/scripts/strict_two_phase/"
        "run_cg_fused_p16_q16_strict.py"
    )
    assert '"promotable_ordering_evidence"' in text(
        "experiments/scripts/strict_two_phase/"
        "run_cg_fused_p16_q16_strict.py"
    )
