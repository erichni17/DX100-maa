#include <cstdlib>
#include <iostream>
#include <type_traits>

#include "mem/MAA/LogicalStreamResponse.hh"

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;        \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

using Ledger = gem5::LogicalStreamResponseLedger;
using Tag = gem5::LogicalStreamTransactionTag;
using Action = gem5::LogicalStreamAction;
using Kind = gem5::LogicalStreamResponseKind;
using Result = gem5::LogicalStreamResponseResult;
using Route = gem5::LogicalStreamResponseRoute;
using RouteDecision = gem5::LogicalStreamResponseRouteDecision;

namespace {

Tag
makeTag(uint64_t transaction, Action action = Action::Writeback,
        uint16_t logical = 0, uint16_t page = 0, uint64_t generation = 1,
        int16_t slot = 0, uint16_t maa = 0)
{
    return {maa, transaction, action, logical, page, generation, slot};
}

Route
makeRoute()
{
    const Tag tag = makeTag(71, Action::Writeback, 2, 3, 5, 1, 4);
    return {true, true, true, tag, tag, 0x5000, 0x5000, 0x5000,
            Kind::ReadEx, Kind::ReadEx};
}

void
checkRejectedRoute(const Route &route, Result expected)
{
    const RouteDecision decision = gem5::classifyLogicalStreamResponseRoute(
        route);
    CHECK(decision.result == expected);
    CHECK(!decision.accepts());
    CHECK(!decision.authorizesOutstandingRetirement());
    CHECK(!decision.authorizesSenderStatePop());
}

void
testPortRouteOwnershipIsPureAndFailClosed()
{
    const Route valid = makeRoute();
    const RouteDecision accepted =
        gem5::classifyLogicalStreamResponseRoute(valid);
    CHECK(accepted.result == Result::Accepted);
    CHECK(accepted.accepts());
    CHECK(accepted.authorizesOutstandingRetirement());
    CHECK(accepted.authorizesSenderStatePop());

    Route mismatch = valid;
    ++mismatch.receivedTag.maaID;
    checkRejectedRoute(mismatch, Result::WrongMAA);
    mismatch = valid;
    mismatch.receivedTag.action = Action::Fill;
    checkRejectedRoute(mismatch, Result::WrongKind);
    mismatch = valid;
    ++mismatch.receivedTag.transactionID;
    checkRejectedRoute(mismatch, Result::WrongTransaction);
    mismatch = valid;
    ++mismatch.receivedTag.logicalID;
    checkRejectedRoute(mismatch, Result::WrongPage);
    mismatch = valid;
    ++mismatch.receivedTag.page;
    checkRejectedRoute(mismatch, Result::WrongPage);
    mismatch = valid;
    ++mismatch.receivedTag.generation;
    checkRejectedRoute(mismatch, Result::WrongPage);
    mismatch = valid;
    ++mismatch.receivedTag.slot;
    checkRejectedRoute(mismatch, Result::WrongSlot);
    mismatch = valid;
    mismatch.senderLineAddress += Ledger::CacheLineBytes;
    checkRejectedRoute(mismatch, Result::WrongAddress);
    mismatch = valid;
    mismatch.responseAddress += Ledger::CacheLineBytes;
    checkRejectedRoute(mismatch, Result::WrongAddress);
    mismatch = valid;
    mismatch.receivedKind = Kind::Read;
    checkRejectedRoute(mismatch, Result::WrongKind);
    mismatch = valid;
    mismatch.receivedKind = Kind::Write;
    checkRejectedRoute(mismatch, Result::WrongKind);
    mismatch = valid;
    mismatch.hasOutstanding = false;
    checkRejectedRoute(mismatch, Result::Stale);
    mismatch = valid;
    mismatch.outstandingIsLogical = false;
    checkRejectedRoute(mismatch, Result::Stale);
    mismatch = valid;
    mismatch.hasLogicalSenderState = false;
    checkRejectedRoute(mismatch, Result::Stale);

    // A duplicate ReadExResp can arrive after the matching address has been
    // reused by its WriteReq.  The route still denies both mutations, while
    // preserving the ledger's more precise duplicate classification.
    mismatch = valid;
    mismatch.expectedKind = Kind::Write;
    mismatch.ledgerResult = Result::Duplicate;
    checkRejectedRoute(mismatch, Result::Duplicate);
    mismatch = valid;
    mismatch.hasOutstanding = false;
    mismatch.ledgerResult = Result::Duplicate;
    checkRejectedRoute(mismatch, Result::Duplicate);
}

void
testFillDelayedReorderedDuplicateCallbacks()
{
    Ledger ledger;
    const Tag tag = makeTag(11, Action::Fill, 1, 2, 7, 0, 3);
    CHECK(ledger.begin(tag, 4) == Result::Accepted);
    for (gem5::Addr address : {0x1000, 0x1040, 0x1080, 0x10c0}) {
        CHECK(ledger.issueLine(tag, address, Kind::Read) == Result::Accepted);
    }

    // Responses may return in any order, but the final acknowledgement is
    // still the only completion point.
    CHECK(ledger.acceptResponse(tag, 0x1080, Kind::Read) == Result::Accepted);
    CHECK(ledger.acceptResponse(tag, 0x1000, Kind::Read) == Result::Accepted);
    CHECK(ledger.acceptResponse(tag, 0x10c0, Kind::Read) == Result::Accepted);
    CHECK(!ledger.isComplete());
    CHECK(ledger.acknowledgedLineCount() == 3);
    CHECK(ledger.acceptResponse(tag, 0x1040, Kind::Read) == Result::Completed);
    CHECK(ledger.isComplete());
    CHECK(ledger.acknowledgedLineCount() == 4);

    const std::size_t acknowledgements = ledger.acknowledgedLineCount();
    CHECK(ledger.acceptResponse(tag, 0x1040, Kind::Read) == Result::Duplicate);
    CHECK(ledger.acknowledgedLineCount() == acknowledgements);
    CHECK(ledger.counters().duplicate == 1);
}

void
testWritebackReadExCallbacksAreExactlyOnce()
{
    Ledger ledger;
    const Tag tag = makeTag(29, Action::Writeback, 0, 3, 17, 1, 2);
    CHECK(ledger.begin(tag, 2) == Result::Accepted);
    CHECK(ledger.issueLine(tag, 0x2000, Kind::ReadEx) == Result::Accepted);
    CHECK(ledger.issueLine(tag, 0x2040, Kind::ReadEx) == Result::Accepted);
    CHECK(!ledger.line(0).readExResponseReceived);
    CHECK(!ledger.line(0).terminalIssued);

    CHECK(ledger.acceptResponse(tag, 0x2000, Kind::ReadEx) ==
          Result::Accepted);
    CHECK(ledger.line(0).readExResponseReceived);
    CHECK(!ledger.line(0).terminalIssued);
    CHECK(ledger.acknowledgedLineCount() == 0);

    const std::size_t acknowledgements = ledger.acknowledgedLineCount();
    CHECK(ledger.acceptResponse(tag, 0x2000, Kind::ReadEx) ==
          Result::Duplicate);
    CHECK(ledger.acknowledgedLineCount() == acknowledgements);
    CHECK(ledger.line(0).readExResponseReceived);
    CHECK(!ledger.line(0).acknowledged);
    CHECK(ledger.counters().duplicate == 1);

    // A WriteResp cannot be armed until the matching ReadExResp was accepted.
    CHECK(ledger.issueLine(tag, 0x2040, Kind::Write) == Result::Invalid);
    CHECK(ledger.acceptResponse(tag, 0x2040, Kind::ReadEx) ==
          Result::Accepted);
    CHECK(ledger.issueLine(tag, 0x2000, Kind::Write) == Result::Accepted);
    CHECK(ledger.issueLine(tag, 0x2040, Kind::Write) == Result::Accepted);
    CHECK(ledger.line(0).terminalIssued);
    CHECK(ledger.line(1).terminalIssued);
    CHECK(ledger.acceptResponse(tag, 0x2040, Kind::Write) == Result::Accepted);
    CHECK(ledger.acknowledgedLineCount() == 1);
    CHECK(ledger.acceptResponse(tag, 0x2000, Kind::Write) ==
          Result::Completed);
    CHECK(ledger.isComplete());
}

void
testWritebackMismatchesCannotAcknowledgeTerminalLines()
{
    Ledger ledger;
    const Tag tag = makeTag(31, Action::Writeback, 4, 5, 19, 2, 3);
    CHECK(ledger.begin(tag, 1) == Result::Accepted);
    CHECK(ledger.issueLine(tag, 0x2400, Kind::ReadEx) == Result::Accepted);
    CHECK(ledger.acceptResponse(tag, 0x2400, Kind::ReadEx) ==
          Result::Accepted);
    CHECK(ledger.issueLine(tag, 0x2400, Kind::Write) == Result::Accepted);

    const auto checkRejected = [&ledger, &tag](Tag responseTag, Kind kind,
                                                Result expected) {
        const std::size_t acknowledgements = ledger.acknowledgedLineCount();
        CHECK(ledger.acceptResponse(responseTag, 0x2400, kind) == expected);
        CHECK(ledger.acknowledgedLineCount() == acknowledgements);
        CHECK(ledger.tag() == tag);
        CHECK(!ledger.line(0).acknowledged);
    };

    Tag wrong = tag;
    wrong.action = Action::Fill;
    checkRejected(wrong, Kind::Write, Result::WrongKind);
    wrong = tag;
    ++wrong.transactionID;
    checkRejected(wrong, Kind::Write, Result::WrongTransaction);
    wrong = tag;
    ++wrong.logicalID;
    checkRejected(wrong, Kind::Write, Result::WrongPage);
    wrong = tag;
    ++wrong.page;
    checkRejected(wrong, Kind::Write, Result::WrongPage);
    wrong = tag;
    ++wrong.generation;
    checkRejected(wrong, Kind::Write, Result::WrongPage);
    wrong = tag;
    ++wrong.slot;
    checkRejected(wrong, Kind::Write, Result::WrongSlot);
    wrong = tag;
    ++wrong.maaID;
    checkRejected(wrong, Kind::Write, Result::WrongMAA);
    checkRejected(tag, Kind::Read, Result::WrongKind);
    CHECK(ledger.acceptResponse(tag, 0x2440, Kind::Write) ==
          Result::WrongAddress);
    CHECK(ledger.acknowledgedLineCount() == 0);

    CHECK(ledger.acceptResponse(tag, 0x2400, Kind::Write) ==
          Result::Completed);
    CHECK(ledger.isComplete());
}

void
testStaleAndOldTransactionsCannotCompleteReusedAddress()
{
    Ledger ledger;
    const Tag oldTag = makeTag(41, Action::Writeback, 1, 1, 3, 0, 0);
    CHECK(ledger.begin(oldTag, 1) == Result::Accepted);
    CHECK(ledger.issueLine(oldTag, 0x3000, Kind::ReadEx) == Result::Accepted);
    CHECK(ledger.acceptResponse(oldTag, 0x3000, Kind::ReadEx) ==
          Result::Accepted);
    CHECK(ledger.issueLine(oldTag, 0x3000, Kind::Write) == Result::Accepted);
    CHECK(ledger.acceptResponse(oldTag, 0x3000, Kind::Write) ==
          Result::Completed);
    ledger.reset();

    CHECK(ledger.acceptResponse(oldTag, 0x3000, Kind::ReadEx) ==
          Result::Stale);
    CHECK(!ledger.isActive());
    CHECK(!ledger.isComplete());

    const Tag newTag = makeTag(42, Action::Writeback, 1, 1, 4, 0, 0);
    CHECK(ledger.begin(newTag, 1) == Result::Accepted);
    CHECK(ledger.issueLine(newTag, 0x3000, Kind::ReadEx) == Result::Accepted);
    CHECK(ledger.acceptResponse(oldTag, 0x3000, Kind::ReadEx) ==
          Result::WrongTransaction);
    CHECK(!ledger.line(0).readExResponseReceived);
    CHECK(ledger.acceptResponse(newTag, 0x3000, Kind::ReadEx) ==
          Result::Accepted);
    CHECK(ledger.issueLine(newTag, 0x3000, Kind::Write) == Result::Accepted);
    CHECK(ledger.acceptResponse(oldTag, 0x3000, Kind::Write) ==
          Result::WrongTransaction);
    CHECK(ledger.acknowledgedLineCount() == 0);
    CHECK(ledger.acceptResponse(newTag, 0x3000, Kind::Write) ==
          Result::Completed);
    CHECK(ledger.isComplete());
}

void
testFixedLedgerCapacityAndUntaggedPathExclusion()
{
    static_assert(Ledger::MaxLinesPerPage == 512,
                  "4096 eight-byte elements occupy 512 cache lines");
    static_assert(std::is_same_v<decltype(Ledger::LineState::issued), bool>);
    static_assert(
        std::is_same_v<decltype(Ledger::LineState::readExResponseReceived),
                       bool>);

    Ledger ledger;
    const Tag tag = makeTag(57, Action::Fill);
    CHECK(ledger.begin(tag, Ledger::MaxLinesPerPage) == Result::Accepted);
    for (std::size_t line = 0; line < Ledger::MaxLinesPerPage; ++line) {
        CHECK(ledger.issueLine(tag, 0x4000 + line * Ledger::CacheLineBytes,
                               Kind::Read) == Result::Accepted);
    }
    CHECK(ledger.issuedLineCount() == Ledger::MaxLinesPerPage);
    CHECK(ledger.issueLine(tag, 0xdead0000, Kind::Read) == Result::Invalid);
    CHECK(ledger.begin(tag, Ledger::MaxLinesPerPage + 1) == Result::Invalid);

    // An ordinary response-less WritebackDirty has no logical sender state or
    // logical outstanding ownership, so this gate grants it no logical route.
    Route untagged = makeRoute();
    untagged.outstandingIsLogical = false;
    untagged.hasLogicalSenderState = false;
    checkRejectedRoute(untagged, Result::Stale);
}

} // anonymous namespace

int
main()
{
    testPortRouteOwnershipIsPureAndFailClosed();
    testFillDelayedReorderedDuplicateCallbacks();
    testWritebackReadExCallbacksAreExactlyOnce();
    testWritebackMismatchesCannotAcknowledgeTerminalLines();
    testStaleAndOldTransactionsCannotCompleteReusedAddress();
    testFixedLedgerCapacityAndUntaggedPathExclusion();
    std::cout << "logical_stream_response_test: PASS" << std::endl;
    return 0;
}
