from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def between(source: str, first: str, last: str) -> str:
    begin = source.index(first)
    return source[begin : source.index(last, begin)]


def test_fixed_sixteen_slot_state_and_exact_byte_charge():
    header = read("src/mem/MAA/IndirectAccess.hh")
    state = between(
        header, "SoaPredicateMaxLines", "enum class SoaJitContextState"
    )
    assert "SoaPredicateMaxLines = 16" in state
    for field in (
        "Addr blockVaddr",
        "Addr blockPaddr",
        "uint64_t generation",
        "bool pending",
        "bool valid",
        "std::array<uint8_t, SoaPredicateLineDataBytes> data",
    ):
        assert field in state
    assert "std::array<SoaPredicateLine, SoaPredicateMaxLines>" in state
    assert "2 * sizeof(Addr) + sizeof(uint64_t) + 2 * sizeof(bool)" in state
    assert "SoaPredicateMaxLines * SoaPredicateLineStateBytes" in state
    # 16 * (vaddr8 + paddr8 + generation8 + pending1 + valid1 + data64).
    assert 16 * (8 + 8 + 8 + 1 + 1 + 64) == 1440
    assert "std::vector" not in state


def test_runtime_knob_is_end_to_end_and_admission_checked():
    simobject = read("src/mem/MAA/MAA.py")
    options = read("configs/common/Options.py")
    config = read("configs/common/MAAConfig.py")
    maa_header = read("src/mem/MAA/MAA.hh")
    maa_source = read("src/mem/MAA/MAA.cc")
    indirect_header = read("src/mem/MAA/IndirectAccess.hh")
    indirect_source = read("src/mem/MAA/IndirectAccess.cc")
    assert "soa_jit_predicate_active_credits = Param.Unsigned" in simobject
    assert '"--maa_soa_jit_predicate_active_credits"' in options
    assert "choices=(1, 4, 8, 16)" in options
    assert 'opts["soa_jit_predicate_active_credits"]' in config
    assert "unsigned int soa_jit_predicate_active_credits" in maa_header
    assert "p.soa_jit_predicate_active_credits" in maa_source
    assert "soa_jit_predicate_active_credits != 16" in maa_source
    assert "_soa_jit_predicate_active_credits" in indirect_header
    assert "_soa_jit_predicate_active_credits" in indirect_source


def test_feeder_is_bounded_ordered_and_one_packet_per_slot():
    source = read("src/mem/MAA/IndirectAccess.cc")
    service = between(
        source,
        "IndirectAccessUnit::serviceSoaPredicateFeeder",
        "bool IndirectAccessUnit::ensureSoaPredicate",
    )
    assert "candidate = itr" in service
    assert "candidate < my_max" in service
    assert (
        "used < static_cast<size_t>(soa_jit_predicate_active_credits)"
        in service
    )
    assert "findSoaPredicateLine(block_vaddr)" in service
    assert "line.blockPaddr == block_paddr" in service
    assert "free_line->generation = soa_jit_generation" in service
    assert service.count("createSoaPredicateReadPacket") == 1
    assert "std::vector" not in service

    consume = between(
        source,
        "bool IndirectAccessUnit::soaPredicateValue",
        "void IndirectAccessUnit::discardSoaPredicateIfDone",
    )
    assert "soaSourcePosition(itr)" in consume
    assert "line->generation != soa_jit_generation" in consume
    assert "soa_jit_predicate_uses++" in consume


def test_response_generation_duplicate_and_unknown_routes_fail_closed():
    source = read("src/mem/MAA/IndirectAccess.cc")
    receive = between(
        source,
        "IndirectAccessUnit::receiveSoaPredicate",
        "uint32_t IndirectAccessUnit::peekDirectIndex",
    )
    assert "candidate.blockPaddr == addr" in receive
    assert "if (line == soa_predicate_lines.end())" in receive
    assert "unknown predicate response paddr" in receive
    assert "return false" in receive
    assert "!line->pending || line->valid" in receive
    assert "line->generation != soa_jit_generation" in receive
    assert receive.index("accountReadResponse") < receive.index(
        "soa_jit_predicate_line_responses++"
    )

    port = read("src/mem/MAA/Port.cc")
    assert "already in the" in port
    assert "my_outstanding_pkt_map[paddr].maaIDs[i] == maaID" in port


def test_reset_drain_terminal_stats_and_traces_cover_every_slot():
    source = read("src/mem/MAA/IndirectAccess.cc")
    header = read("src/mem/MAA/MAA.hh")
    stats = read("src/mem/MAA/MAA.cc")
    assert source.count("for (auto &line : soa_predicate_lines)") >= 1
    assert "!soaPredicateLinesEmpty()" in source
    terminal = between(
        source,
        "checkSoaJitTerminal",
        "void IndirectAccessUnit::executeInstruction",
    )
    for invariant in (
        "soa_jit_predicate_line_issues !=",
        "soa_jit_predicate_line_responses",
        "soa_jit_predicate_line_hits != expected_predicate_uses",
        "soa_jit_predicate_uses != expected_predicate_uses",
        "soa_jit_predicate_feeder_high_water >",
    ):
        assert invariant in terminal
    for counter in (
        "IND_SoaJitPredicateLineHits",
        "IND_SoaJitPredicateUses",
        "IND_SoaJitPredicateFeederStalls",
        "IND_SoaJitPredicateActiveCredits",
        "IND_SoaJitPredicateFeederHighWater",
        "IND_SoaJitPredicateFeederStateBytes",
    ):
        assert counter in header
        assert counter in stats
    for event in (
        "event=soa_jit_predicate_issue",
        "event=soa_jit_predicate_response",
        "event=soa_jit_predicate_hit",
        "event=soa_jit_predicate_use",
        "event=soa_jit_predicate_stall",
    ):
        assert event in source


def test_focused_runner_has_exact_credit_matrix_and_provenance():
    runner = read("experiments/scripts/run_soa_jit_predicate_feeder_matrix.sh")
    assert "for credits in 1 4 8 16" in runner
    assert '--maa_soa_jit_predicate_active_credits="$credits"' in runner
    assert "expected_hash=2761840269561229581" in runner
    assert "IND_SoaJitPredicateLineResponses" in runner
    assert "IND_SoaJitPredicateFeederHighWater" in runner
    assert "$issues -eq 2048 && $responses -eq 2048" in runner
    assert "$hits -eq 32768 && $uses -eq 32768" in runner
    assert "source_commit=" in runner
    assert "benchmark_source_sha256=" in runner
    assert "runner_source_sha256=" in runner
    assert "gem5_sha256=" in runner
    assert "guest_sha256=" in runner
    assert "se_config_sha256=" in runner
    assert "ramulator_config_sha256=" in runner
    assert "config_ini_sha256=" in runner
    assert "command_sha256=" in runner
    assert "simTicks" in runner
