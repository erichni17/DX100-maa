#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <type_traits>

#include "mem/MAA/LogicalStreamResponse.hh"

namespace {

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;         \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

using Ledger = gem5::LogicalStreamResponseLedger;
using Tag = gem5::LogicalStreamTransactionTag;
using Action = gem5::LogicalStreamAction;
using Kind = gem5::LogicalStreamResponseKind;
using Result = gem5::LogicalStreamResponseResult;

Tag
makeTag(uint64_t transaction, Action action = Action::Writeback,
        uint16_t logical = 0, uint16_t page = 0, uint64_t generation = 1,
        int16_t slot = 0, uint16_t maa = 0)
{
    return {maa, transaction, action, logical, page, generation, slot};
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
    CHECK(ledger.acceptResponse(tag, 0x1080, Kind::Read, true) ==
          Result::Accepted);
    CHECK(ledger.acceptResponse(tag, 0x1000, Kind::Read, true) ==
          Result::Accepted);
    CHECK(ledger.acceptResponse(tag, 0x10c0, Kind::Read, true) ==
          Result::Accepted);
    CHECK(!ledger.isComplete());
    CHECK(ledger.acknowledgedLineCount() == 3);
    CHECK(ledger.acceptResponse(tag, 0x1040, Kind::Read, true) ==
          Result::Completed);
    CHECK(ledger.isComplete());
    CHECK(ledger.acknowledgedLineCount() == 4);

    const std::size_t acknowledgements = ledger.acknowledgedLineCount();
    CHECK(ledger.acceptResponse(tag, 0x1040, Kind::Read, true) ==
          Result::Duplicate);
    CHECK(ledger.acknowledgedLineCount() == acknowledgements);
    CHECK(ledger.counters().duplicate == 1);
}

void
testWritebackRejectsWrongIdentityWithoutMutation()
{
    Ledger ledger;
    const Tag tag = makeTag(29, Action::Writeback, 0, 3, 17, 1, 2);
    CHECK(ledger.begin(tag, 2) == Result::Accepted);
    CHECK(ledger.issueLine(tag, 0x2000, Kind::Write) == Result::Accepted);
    CHECK(ledger.issueLine(tag, 0x2040, Kind::Write) == Result::Accepted);

    // A writeback's source ReadEx response is authenticated, but it is not a
    // terminal line acknowledgement.  Only WriteResp settles the ledger.
    CHECK(ledger.acceptResponse(tag, 0x2000, Kind::Read, false) ==
          Result::Accepted);
    CHECK(ledger.acknowledgedLineCount() == 0);

    const auto checkRejected = [&ledger, &tag](Tag responseTag,
                                                Kind kind, Result expected) {
        const std::size_t acknowledgements = ledger.acknowledgedLineCount();
        CHECK(ledger.acceptResponse(responseTag, 0x2000, kind, true) ==
              expected);
        CHECK(ledger.acknowledgedLineCount() == acknowledgements);
        CHECK(ledger.tag() == tag);
    };

    Tag wrong = tag;
    wrong.action = Action::Fill;
    checkRejected(wrong, Kind::Write, Result::WrongKind);
    wrong = tag;
    ++wrong.transactionID;
    checkRejected(wrong, Kind::Write, Result::WrongTransaction);
    wrong = tag;
    ++wrong.page;
    checkRejected(wrong, Kind::Write, Result::WrongPage);
    wrong = tag;
    ++wrong.slot;
    checkRejected(wrong, Kind::Write, Result::WrongSlot);
    wrong = tag;
    ++wrong.maaID;
    checkRejected(wrong, Kind::Write, Result::WrongMAA);
    checkRejected(tag, Kind::Read, Result::WrongKind);
    CHECK(ledger.acceptResponse(tag, 0x2080, Kind::Write, true) ==
          Result::WrongAddress);

    const auto &counters = ledger.counters();
    CHECK(counters.wrongKind == 2);
    CHECK(counters.wrongTransaction == 1);
    CHECK(counters.wrongPage == 1);
    CHECK(counters.wrongSlot == 1);
    CHECK(counters.wrongMAA == 1);
    CHECK(counters.wrongAddress == 1);
    CHECK(ledger.acknowledgedLineCount() == 0);

    CHECK(ledger.acceptResponse(tag, 0x2040, Kind::Write, true) ==
          Result::Accepted);
    CHECK(ledger.acceptResponse(tag, 0x2000, Kind::Write, true) ==
          Result::Completed);
    CHECK(ledger.isComplete());
}

void
testOldResponseCannotCompleteReusedAddressTransaction()
{
    Ledger ledger;
    const Tag oldTag = makeTag(41, Action::Writeback, 1, 1, 3, 0, 0);
    CHECK(ledger.begin(oldTag, 1) == Result::Accepted);
    CHECK(ledger.issueLine(oldTag, 0x3000, Kind::Write) == Result::Accepted);
    CHECK(ledger.acceptResponse(oldTag, 0x3000, Kind::Write, true) ==
          Result::Completed);
    ledger.reset();

    const Tag newTag = makeTag(42, Action::Writeback, 1, 1, 4, 0, 0);
    CHECK(ledger.begin(newTag, 1) == Result::Accepted);
    CHECK(ledger.issueLine(newTag, 0x3000, Kind::Write) == Result::Accepted);
    CHECK(ledger.acceptResponse(oldTag, 0x3000, Kind::Write, true) ==
          Result::WrongTransaction);
    CHECK(!ledger.isComplete());
    CHECK(ledger.acknowledgedLineCount() == 0);
    CHECK(ledger.acceptResponse(newTag, 0x3000, Kind::Write, true) ==
          Result::Completed);
    CHECK(ledger.isComplete());
}

void
testStaleResponseAfterTransactionReset()
{
    Ledger ledger;
    const Tag tag = makeTag(48, Action::Fill);
    CHECK(ledger.begin(tag, 1) == Result::Accepted);
    CHECK(ledger.issueLine(tag, 0x3800, Kind::Read) == Result::Accepted);
    ledger.reset();

    CHECK(ledger.acceptResponse(tag, 0x3800, Kind::Read, true) ==
          Result::Stale);
    CHECK(!ledger.isActive());
    CHECK(!ledger.isComplete());
    CHECK(ledger.acknowledgedLineCount() == 0);
    CHECK(ledger.counters().stale == 1);
}

void
testFixedLedgerCapacityAndExactIssueCount()
{
    static_assert(Ledger::MaxLinesPerPage == 512,
                  "4096 eight-byte elements occupy 512 cache lines");
    static_assert(std::is_same_v<decltype(Ledger::LineState::issued), bool>);

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
}

} // anonymous namespace

int
main()
{
    testFillDelayedReorderedDuplicateCallbacks();
    testWritebackRejectsWrongIdentityWithoutMutation();
    testOldResponseCannotCompleteReusedAddressTransaction();
    testStaleResponseAfterTransactionReset();
    testFixedLedgerCapacityAndExactIssueCount();
    std::cout << "logical_stream_response_test: PASS" << std::endl;
    return 0;
}
