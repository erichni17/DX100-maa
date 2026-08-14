from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def test_explicit_abi_tag_does_not_change_ordinary_predicate_semantics():
    api = read("benchmarks/API/MAA_gem5.hpp")
    interface = read("src/mem/MAA/IF.hh")
    port = read("src/mem/MAA/CpuSidePort.cc")
    assert "MAA_SOA_JIT_MASKED_INDEX_MODE_TAG = UINT64_MAX" in api
    assert "maa_indirect_rmw_vector_soa_jit_masked_indices" in api
    assert "*INSTR_predicateaddr = (uint64_t)predicates" in api
    assert "*INSTR_predicateaddr = MAA_SOA_JIT_MASKED_INDEX_MODE_TAG" in api
    assert "bool soaJitMaskedIndex" in interface
    assert "data == SoaJitSafety::MaskedIndexModeTag" in port
    assert "current_instruction->soaJitMaskedIndex ? 0 : data" in port
    assert "if (current_instruction->predicateAddr != 0)" in port
    assert "getAddrRegion(current_instruction->predicateAddr)" in port


def test_marker_is_admitted_only_when_outside_exact_registered_a_range():
    safety = read("src/mem/MAA/SoaJitSafety.hh")
    indirect = read("src/mem/MAA/IndirectAccess.cc")
    assert "MaskedIndexInactive" in safety
    assert "maskedIndexMarkerOutsideLegalRange" in safety
    assert "legal_words <= MaskedIndexInactive" in safety
    decode = indirect[indirect.index("case Status::Decode") :]
    assert (
        decode.index("validateSoaJitAddressSpans();")
        < decode.index("maskedIndexMarkerOutsideLegalRange")
        < decode.index("soa_jit_generation = soa_jit_next_generation++")
    )
    assert "masked-index sentinel can name a legal A word" in decode


def test_classification_reuses_the_sequential_index_word_and_keeps_ledgers():
    source = read("src/mem/MAA/IndirectAccess.cc")
    predicate = source[
        source.index("soaPredicateValue") : source.index(
            "discardSoaPredicateIfDone"
        )
    ]
    assert "isSoaJitMaskedIndexRmw()" in predicate
    assert (
        "peekDirectIndex(itr) != SoaJitSafety::MaskedIndexInactive"
        in predicate
    )
    assert "itr != my_i" in predicate
    terminal = source[
        source.index("checkSoaJitTerminal") : source.index(
            "void IndirectAccessUnit::executeInstruction"
        )
    ]
    assert "soa_jit_selected + soa_jit_predicate_rejected" in terminal
    assert "expected_predicate_uses" in terminal
    trace = source[source.index('"event=soa_jit_complete') :]
    assert "predicate_mode=%s" in trace
    assert "masked_index_compare_bits=%lu" in trace
    assert "masked_index_mode_state_bits=%lu" in trace
    assert "masked_index_additional_buffer_bytes=%lu" in trace


def test_gzp_like_micro_has_duplicate_fp_order_and_shared_selector_checkpoint():
    guest = read("benchmarks/API/test_hybrid_rmw_soa.cpp")
    runner = read("experiments/scripts/run_soa_jit_masked_index_micro.sh")
    assert "16777216.0F, 1.0F, -16777216.0F, 1.0F" in guest
    assert "indices[operation][i] = UINT32_MAX" in guest
    assert "readModeSelector" in guest
    assert 'make_checkpoint "selector $selector"' in runner
    assert "run_arm separate_predicate soa" in runner
    assert "run_arm masked_index soa-masked-index" in runner
    assert "IND_CyclesFill" in runner
    assert "IND_SoaJitPredicateLineReads" in runner
    assert "bytes_avoided" in runner
