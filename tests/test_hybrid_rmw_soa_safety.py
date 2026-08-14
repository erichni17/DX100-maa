from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def between(source: str, first: str, last: str) -> str:
    begin = source.index(first)
    return source[begin : source.index(last, begin)]


def test_soa_memory_roles_cover_every_registered_region_and_local_hazard():
    interface = read("src/mem/MAA/IF.cc")
    accesses = between(
        interface,
        "Instruction::getMemoryAccesses",
        "Instruction::hasMemoryHazard",
    )
    assert "append(addrRangeID, AccessType::WRITE)" in accesses
    assert "append(backingAddrRangeID, AccessType::READ)" in accesses
    assert "append(indexAddrRangeID, AccessType::READ)" in accesses
    assert "append(predicateAddrRangeID, AccessType::READ)" in accesses
    assert "if (type == AccessType::WRITE)" in accesses

    hazards = between(
        interface, "Instruction::hasMemoryHazard", "Instruction::WordSize"
    )
    assert "mine[lhs].regionID == theirs[rhs].regionID" in hazards
    assert "mine[lhs].type == AccessType::WRITE" in hazards
    assert "theirs[rhs].type == AccessType::WRITE" in hazards
    assert "_instruction.hasMemoryHazard" in interface


def test_multi_maa_soa_permit_is_atomic_and_released_as_one_lifecycle():
    invalidator = read("src/mem/MAA/Invalidator.cc")
    acquire = between(
        invalidator,
        "Invalidator::getSoaJitAddrRegionPermit",
        "void Invalidator::transientInstruction",
    )
    assert "instruction->getMemoryAccesses(accesses)" in acquire
    assert (
        "std::array<Action, Instruction::MaxMemoryAccesses> actions" in acquire
    )
    assert acquire.index(
        "for (size_t index = 0; index < count; ++index)"
    ) < acquire.index("instruction->memoryPermitReserved = true")
    assert "soaTransientInstructions.push_back(instruction)" in acquire
    assert "scheduleTransientInstructionEvent(100)" in acquire
    assert "instruction->memoryPermitGranted = true" in acquire

    finish = between(
        invalidator,
        "Invalidator::finishSoaJitAddrRegionPermit",
        "int Invalidator::get_cl_id",
    )
    assert "instruction->getMemoryAccesses(accesses)" in finish
    assert "status = RGStatus::UsedModified" in finish
    assert "instruction->memoryPermitGranted = false" in finish
    assert "instruction->memoryPermitReserved = false" in finish

    interface = read("src/mem/MAA/IF.cc")
    ready = between(
        interface,
        "Instruction *IF::getReady",
        "void IF::finishInstructionCompute",
    )
    assert "maa->num_maas == 1 || maa->getAddrRegionPermit" in ready


def test_drain_fails_closed_for_queued_and_active_soa_protocol_state():
    maa = read("src/mem/MAA/MAA.cc")
    live = between(maa, "MAA::hasLiveSoaJitState", "DrainState\nMAA::drain")
    assert "ifile->hasLiveSoaJitRmw()" in live
    assert "my_instructions.begin()" in live
    assert "indirectAccessUnits[unit].hasLiveSoaJitState()" in live

    drain = between(maa, "MAA::drain()", "void\nMAA::drainResume")
    assert "panic_if(hasLiveSoaJitState()" in drain
    assert "serialization is" in drain
    assert drain.index("panic_if(hasLiveSoaJitState()") < drain.index(
        "return DrainState::Drained"
    )

    indirect = read("src/mem/MAA/IndirectAccess.cc")
    unit_live = between(
        indirect,
        "IndirectAccessUnit::hasLiveSoaJitState",
        "bool IndirectAccessUnit::serviceSoaJitBuild",
    )
    for state in (
        "soa_jit_operation_active",
        "my_instruction != nullptr",
        "soa_predicate_line.pending",
        "soa_predicate_line.valid",
        "!soaJitContextsEmpty()",
    ):
        assert state in unit_live


def test_completion_token_is_one_32_bit_tile_for_every_datatype():
    interface = read("src/mem/MAA/IF.cc")
    safety = read("src/mem/MAA/SoaJitSafety.hh")
    dst2 = between(
        interface,
        "else if (tile_id == dst2SpdID)",
        "int Instruction::WordSize",
    )
    assert "return SoaJitSafety::CompletionTokenBytes;" in dst2
    assert "return WordSize();" not in dst2
    assert "CompletionTokenBytes = sizeof(uint32_t)" in safety

    for consumer in (
        "instruction->getWordSize(instruction->dst2SpdID)",
        "_instruction.getWordSize(tile)",
        "instruction.getWordSize(first)",
    ):
        assert consumer in interface or consumer in read("src/mem/MAA/MAA.cc")
    assert "completion_only_tiles[maa_id][tile_id]" in interface


def test_typed_alignment_negative_cases_are_rejected_before_dispatch():
    port = read("src/mem/MAA/CpuSidePort.cc")
    safety = read("src/mem/MAA/SoaJitSafety.hh")
    word_five = between(port, "case 5:", "default:")
    for expression in (
        "a_addr % word_bytes",
        "value_addr % word_bytes",
        "index_addr % alignof(uint32_t)",
        "predicate_addr % alignof(uint32_t)",
    ):
        assert expression in safety
    assert "SoaJitSafety::typedOperandsAligned" in word_five
    assert word_five.index(
        "Rejected misaligned typed SoA/JIT"
    ) < word_five.index("my_instruction_recvs[instruction_id] = true")

    def admitted(word_size, a, values, indices, predicate):
        return (
            a % word_size == 0
            and values % word_size == 0
            and indices % 4 == 0
            and (predicate == 0 or predicate % 4 == 0)
        )

    assert admitted(4, 0x1000, 0x2000, 0x3000, 0x4000)
    assert admitted(8, 0x1000, 0x2000, 0x3000, 0)
    for negative in (
        (4, 0x1002, 0x2000, 0x3000, 0),  # FP32 A
        (4, 0x1000, 0x2002, 0x3000, 0),  # FP32 values
        (4, 0x1000, 0x2000, 0x3002, 0),  # uint32 indices
        (4, 0x1000, 0x2000, 0x3000, 0x4002),  # uint32 predicate
        (8, 0x1004, 0x2000, 0x3000, 0),  # FP64 A
        (8, 0x1000, 0x2004, 0x3000, 0),  # FP64 values
    ):
        assert not admitted(*negative)

    indirect = read("src/mem/MAA/IndirectAccess.cc")
    validator = between(
        indirect, "validateSoaJitAddressSpans", "ensureSoaPredicate"
    )
    assert "SoaJitSafety::typedOperandsAligned" in validator


def test_terminal_ledger_is_not_resettable_statistics_state():
    header = read("src/mem/MAA/IndirectAccess.hh")
    ledger = between(
        header, "bool soa_jit_operation_active", "int my_dst_tile"
    )
    for field in (
        "soa_jit_selected",
        "soa_jit_predicate_rejected",
        "soa_jit_a_read_issues",
        "soa_jit_value_read_responses",
        "soa_jit_a_write_responses",
    ):
        assert field in ledger

    indirect = read("src/mem/MAA/IndirectAccess.cc")
    terminal = between(
        indirect,
        "checkSoaJitTerminal",
        "void IndirectAccessUnit::executeInstruction",
    )
    assert "maa->stats" not in terminal
    assert "soa_jit_operation_active" in indirect

    maa = read("src/mem/MAA/MAA.cc")
    reset = between(
        maa, "void MAA::resetStats()", "#define MAKE_INDIRECT_STAT_NAME"
    )
    assert "soa_jit_" not in reset.lower()
