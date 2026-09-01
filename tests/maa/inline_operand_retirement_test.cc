#include <cassert>
#include <cstdint>

#include <gem5/maa_page_fed_soa_abi.hh>

#include "mem/MAA/InlineOperandRetirement.hh"

using gem5::maa::InlineOperandPageFedABI;
using gem5::maa::InlineOperandRetirementState;
using gem5::maa::InlineRetirementRecord;

int
main()
{
    InlineOperandPageFedABI::Command command;
    assert(InlineOperandPageFedABI::decode(
        InlineOperandPageFedABI::encodeAdmitPair(7, 3, 9, 10), command));
    assert(command.action ==
           InlineOperandPageFedABI::Action::AdmitPair);
    assert(command.page == 3 && command.indexTile == 9 &&
           command.valueTile == 10 && command.generation == 7);
    assert(InlineOperandPageFedABI::decode(
        InlineOperandPageFedABI::encodeAck(7, 2047), command));
    assert(command.action ==
           InlineOperandPageFedABI::Action::AckRetirementLine);
    assert(command.retirementLine == 2047);
    assert(!InlineOperandPageFedABI::decode(
        InlineOperandPageFedABI::encodeClose(0), command));

    InlineOperandRetirementState state;
    assert(state.open(false, 1) ==
           InlineOperandRetirementState::Result::Disabled);
    assert(state.open(true, 1) ==
           InlineOperandRetirementState::Result::Accepted);
    InlineRetirementRecord records[8]{};
    for (uint32_t i = 0; i < 8; ++i) {
        records[i].destination = i;
        records[i].valueBits = 0x3f800000U + i;
    }
    uint8_t first = 0;
    assert(state.reserve(8, first) ==
           InlineOperandRetirementState::Result::Accepted);
    assert(state.fill(first, records, 8) ==
           InlineOperandRetirementState::Result::Accepted);
    assert(state.acknowledge(1, 0) ==
           InlineOperandRetirementState::Result::EarlyVisibility);
    assert(state.markWriteIssued(first) ==
           InlineOperandRetirementState::Result::Accepted);
    assert(state.acknowledge(1, 0) ==
           InlineOperandRetirementState::Result::EarlyVisibility);
    assert(state.markWriteResponse(first) ==
           InlineOperandRetirementState::Result::Accepted);
    assert(state.visible(1, 0));
    assert(state.acknowledge(2, 0) ==
           InlineOperandRetirementState::Result::StaleGeneration);
    assert(state.acknowledge(1, 0) ==
           InlineOperandRetirementState::Result::Accepted);

    uint8_t cancelled = 0;
    assert(state.reserve(1, cancelled) ==
           InlineOperandRetirementState::Result::Accepted);
    assert(state.cancelReservation(cancelled) ==
           InlineOperandRetirementState::Result::Accepted);

    uint8_t credits[InlineOperandRetirementState::Credits]{};
    for (size_t i = 0; i < InlineOperandRetirementState::Credits; ++i)
        assert(state.reserve(1, credits[i]) ==
               InlineOperandRetirementState::Result::Accepted);
    uint8_t overflow = 0;
    assert(state.reserve(1, overflow) ==
           InlineOperandRetirementState::Result::Full);
    for (size_t i = 0; i < InlineOperandRetirementState::Credits; ++i) {
        assert(state.fill(credits[i], records, 1) ==
               InlineOperandRetirementState::Result::Accepted);
        assert(state.markWriteIssued(credits[i]) ==
               InlineOperandRetirementState::Result::Accepted);
        assert(state.markWriteResponse(credits[i]) ==
               InlineOperandRetirementState::Result::Accepted);
        assert(state.acknowledge(1, i + 1) ==
               InlineOperandRetirementState::Result::Accepted);
    }
    assert(state.close() ==
           InlineOperandRetirementState::Result::Accepted);
    assert(state.finish() ==
           InlineOperandRetirementState::Result::Accepted);
    assert(state.recordCount() == 16);
    assert(state.issuedLines() == 9 && state.respondedLines() == 9 &&
           state.ackedLines() == 9);
    assert(state.creditHighWater() ==
           InlineOperandRetirementState::Credits);
    assert(state.open(true, 1) ==
           InlineOperandRetirementState::Result::StaleGeneration);
    return 0;
}
