from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def test_default_off_knob_is_wired_end_to_end():
    simobject = read("src/mem/MAA/MAA.py")
    options = read("configs/common/Options.py")
    config = read("configs/common/MAAConfig.py")
    maa_header = read("src/mem/MAA/MAA.hh")
    indirect_header = read("src/mem/MAA/IndirectAccess.hh")
    for text in (simobject, options, config, maa_header, indirect_header):
        assert "soa_jit_descriptor_value_carry" in text
    knob = simobject[simobject.index("soa_jit_descriptor_value_carry") :]
    assert "Param.Bool(\n        False," in knob[:200]
    option = options[options.index('"--maa_soa_jit_descriptor_value_carry"') :]
    assert 'action="store_true"' in option[:300]


def test_offset_payload_is_zero_growth_for_fp32_and_fp64():
    header = read("src/mem/MAA/Tables.hh")
    source = read("src/mem/MAA/Tables.cc")
    assert "static_assert(sizeof(OffsetTableEntry) == 16" in header
    assert "sizeof(itr) + sizeof(pass) == sizeof(value)" in header
    assert "setCarriedValue" in header
    assert "carriedValue" in header
    assert "insertCarried(uint64_t value" in header
    assert "entries[entry_id].setCarriedValue(value)" in source
    assert "insertCarried(Addr grow_addr" in header


def test_fill_owner_and_terminal_ledgers_are_exact_and_bounded():
    header = read("src/mem/MAA/IndirectAccess.hh")
    source = read("src/mem/MAA/IndirectAccess.cc")
    assert "std::array<uint8_t, 64> data" in header
    assert "SoaJitFillValueModeledBytes == 73" in header
    assert "sizeof(SoaJitFillValueLine) == 80" in header
    assert "readSoaJitCarriedValue" in source
    assert "receiveSoaJitCarriedValue" in source
    assert source.index("receiveSoaJitCarriedValue(addr") < source.index(
        "receiveSoaJitData(addr"
    )
    terminal = source[
        source.index("checkSoaJitTerminal") : source.index(
            "executeInstruction", source.index("checkSoaJitTerminal")
        )
    ]
    for invariant in (
        "soa_jit_carry_fill_read_issues !=\n"
        "                     soa_jit_carry_fill_read_responses",
        "soa_jit_carried_operands != soa_jit_selected",
        "soa_jit_carried_applies != soa_jit_selected",
        "soa_jit_value_read_issues != 0",
        "SoaJitFillValueState::Empty",
    ):
        assert invariant in terminal
    assert "carry_entry_incremental_bytes=0" in source
    assert "carry_unit_incremental_modeled_bytes=%lu" in source
    assert "carry_unit_host_bytes=%lu" in source


def test_request_uses_carried_bits_in_existing_chain_order():
    source = read("src/mem/MAA/IndirectAccess.cc")
    begin = source.index("issueSoaJitValueRead")
    request = source[begin : source.index("fillSoaJitLookahead", begin)]
    assert request.index("entry.carriedValue()") < request.index(
        "context.issueOffset = entry.next_itr"
    )
    assert "action=carry" in request
    apply_begin = source.index("serviceSoaJitLookahead")
    apply = source[apply_begin : source.index("issueSoaJitWrite", apply_begin)]
    assert "candidate.offset == context.nextOffset" in apply
    assert "offset_table->consume_entry(context.nextOffset)" in apply


def test_micro_is_shared_checkpoint_exact_two_rep_control_treatment():
    runner = read(
        "experiments/scripts/run_soa_jit_descriptor_value_carry_micro.sh"
    )
    assert "c8l8-checkpoint" in runner
    assert "for rep in 1 2" in runner
    assert "for arm in control treatment" in runner
    assert "--maa_soa_jit_descriptor_value_carry" in runner
    assert "output_hash=" in runner
    assert '"$expected_hash"' in runner
    assert "IND_CyclesFill" in runner
    assert "IND_CyclesRequest" in runner
    assert "IND_SoaJitValueReadIssues" in runner
    assert "carry_entry_incremental_bytes=0" in runner
    assert "carry_unit_incremental_modeled_bytes=73" in runner
    assert "carry_unit_host_bytes=80" in runner
    assert "SOA_JIT_DESCRIPTOR_VALUE_CARRY_MICRO_PASS" in runner
