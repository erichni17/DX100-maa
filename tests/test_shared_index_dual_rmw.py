#!/usr/bin/env python3
"""Fail-closed source contracts for shared-index dual-destination SoA/JIT."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_api_uses_reserved_words_and_has_no_logical_payload_pointer() -> None:
    api = source("benchmarks/API/MAA_gem5.hpp")
    assert "MAA_SOA_JIT_DUAL_MASKED_INDEX_MODE_TAG" in api
    assert "INSTR_secondary_baseaddr" in api
    assert "INSTR_secondary_backingaddr" in api
    begin = api.index("maa_indirect_rmw_vector_soa_jit_dual_masked_indices")
    dual = api[begin : api.index("template <class T1>", begin + 16)]
    for required in (
        "primary_data",
        "secondary_data",
        "indices",
        "primary_values",
        "secondary_values",
        "completion_tile",
    ):
        assert required in dual
    for forbidden in (
        "logical_operand",
        "logical_result",
        "old_value_tile",
        "const uint32_t *predicate",
    ):
        assert forbidden not in dual


def test_decode_waits_for_both_secondary_words_and_fails_closed() -> None:
    instruction = source("src/mem/MAA/IF.hh")
    decode = source("src/mem/MAA/CpuSidePort.cc")
    for required in (
        "soaJitDualDestination",
        "secondaryBaseAddr",
        "secondaryBackingAddr",
        "isSoaJitDualDestinationRmw",
    ):
        assert required in instruction
    assert "case 6:" in decode
    assert "case 7:" in decode
    assert "SoA/JIT dual destination" in decode
    assert "same cache-line word geometry" in decode
    assert (
        "scheduleDispatchInstructionEvent" in decode[decode.index("case 7:") :]
    )


def test_shared_index_is_retained_in_existing_offset_word_field() -> None:
    indirect = source("src/mem/MAA/IndirectAccess.cc")
    header = source("src/mem/MAA/IndirectAccess.hh")
    assert "int row_payload = wid" in indirect
    assert "isSoaJitDualDestinationRmw() ||" in indirect
    assert "std::memcpy(&row_payload, &idx, sizeof(idx))" in indirect
    assert "dualIndexFromOffset" in indirect
    assert "dualAWordFromIndex" in indirect
    assert "SoaJitDestinations = 2" in header
    assert (
        "std::array<std::array<uint8_t, 64>, SoaJitDestinations> aLine"
        in header
    )
    assert "std::array<Addr, SoaJitDestinations> aPaddr" in header
    assert "SoaJitContexts == 32" in header
    assert "SoaJitDualAResultPayloadBytes" in header
    assert "SoaJitMaxAResultPayloadBytes = 4096" in header
    assert "SoaJitDualAResultPayloadBytes <=" in header
    assert "SoaJitAuxiliaryOperandPayloadBytes" in header
    assert "SoaJitTransientWriteTransportPayloadBytes" in header
    assert (
        "logical16"
        not in header[
            header.index("struct SoaJitContext") : header.index("my_dst_tile")
        ]
    )


def test_terminal_requires_two_value_and_two_response_owned_a_streams() -> (
    None
):
    indirect = source("src/mem/MAA/IndirectAccess.cc")
    for required in (
        "soaJitDestinationCount()",
        "aReadPendingMask",
        "aWritePendingMask",
        "valueIssueMask",
        "valueReadyMask",
        "destinations=%u",
        "value_streams=%u",
        "external_ports_added=0",
        "hidden_logical16_payload_bytes=0",
        "physical_3p2ghz_realizability=unclaimed",
    ):
        assert required in indirect or required in source(
            "benchmarks/UME/gradzatp.cpp"
        )
    assert "soa_jit_a_read_issues != soa_jit_a_read_responses" in indirect
    assert "soa_jit_a_write_issues != soa_jit_a_write_responses" in indirect


def test_gzp_arm_is_default_off_and_issues_one_fused_rmw_per_window() -> None:
    guest = source("benchmarks/UME/gradzatp.cpp")
    assert 'treatment == "dual_shared_index"' in guest
    assert 'return "dual_shared_index_soa_jit"' in guest
    assert "maa_indirect_rmw_vector_soa_jit_dual_masked_indices<" in guest
    assert "soa_shared_index_windows" in guest
    assert "shared_index_builds=" in guest
    assert '<< " value_streams="' in guest
    assert "hidden_logical16_payload_bytes=0" in guest
    assert (
        "static GzpRmwTreatment gzp_rmw_treatment = GzpRmwTreatment::Legacy4K"
        in guest
    )


def test_one_window_gate_requires_mechanism_and_full_promotion_evidence() -> (
    None
):
    gate = source("experiments/scripts/run_gzp_dual_logical16_one_window.py")
    for required in (
        '"shared_index"',
        '"token_stream_ld dual_shared_index"',
        '"IND_VirtIndexLineReads"',
        '"IND_CyclesFill"',
        '"shared_index_builds"',
        '"value_streams"',
        '"external_ports_added"',
        '"a_result_payload_bytes"',
        '"auxiliary_operand_payload_bytes"',
        '"transient_write_transport_payload_bytes"',
        '"physical_3p2ghz_realizability"',
        '"--one-window-manifest"',
        '"full GZP shared-index execution requires accepted one-window evidence"',
        'decision = "ACCEPT" if tick_delta < 0 and mechanism_closed else "REJECT"',
        'int(candidate["a_result_payload_bytes"]) <= 4096',
    ):
        assert required in gate
