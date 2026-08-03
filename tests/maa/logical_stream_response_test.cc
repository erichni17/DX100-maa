#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
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
using CounterEvent = gem5::LogicalStreamCounterEvent;
using CounterDecision = gem5::LogicalStreamCounterDecision;
using Disposition = gem5::TimingResponseDisposition;
using CreditOwner = gem5::TimingResponseCreditOwner;
using DispositionDecision =
    gem5::LogicalStreamResponseDispositionDecision;
using WrapperDecision = gem5::TimingResponseWrapperDecision;
using AliasShape = gem5::ResponsePacketAliasShape;
using SenderShape = gem5::ResponseSenderStateShape;
using DeferredShape = gem5::DeferredPromotionShape;
using OrphanShape = gem5::OrphanedLogicalPacketShape;
using TeardownShape = gem5::ResponseTeardownShape;
using Preflight = gem5::ResponseMutationPreflight;

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

uint32_t
applyCounter(Kind kind, CounterEvent event, uint32_t value,
             bool expectChange)
{
    const CounterDecision decision =
        gem5::decideLogicalStreamCounterUpdate(kind, event, value);
    CHECK(decision.valid);
    CHECK(decision.changed == expectChange);
    return decision.value;
}

void
testCommandSpecificCounterOwnershipAndRetries()
{
    // Ordinary and logical reads have the same counter owner: the one
    // successful send. Failed attempts retain the count, and the accepted
    // response cannot debit it for a second time.
    for (Kind readKind : {Kind::Read, Kind::ReadEx}) {
        uint32_t count =
            applyCounter(readKind, CounterEvent::Enqueued, 0, true);
        CHECK(count == 1);
        count = applyCounter(readKind, CounterEvent::SendRejected, count,
                             false);
        count = applyCounter(readKind, CounterEvent::SendRejected, count,
                             false);
        CHECK(count == 1);
        count = applyCounter(readKind, CounterEvent::SendAccepted, count,
                             true);
        CHECK(count == 0);
        count = applyCounter(readKind, CounterEvent::ResponseAccepted, count,
                             false);
        count = applyCounter(readKind, CounterEvent::ResponseRejected, count,
                             false);
        CHECK(count == 0);
    }

    // A response-bearing logical write stays counted across both failed and
    // successful send attempts. Only its accepted Write response settles it.
    uint32_t writeCount =
        applyCounter(Kind::Write, CounterEvent::Enqueued, 0, true);
    CHECK(writeCount == 1);
    writeCount = applyCounter(Kind::Write, CounterEvent::SendRejected,
                              writeCount, false);
    writeCount = applyCounter(Kind::Write, CounterEvent::SendAccepted,
                              writeCount, false);
    CHECK(writeCount == 1);

    Route wrong = makeRoute();
    wrong.expectedKind = Kind::Write;
    wrong.receivedKind = Kind::ReadEx;
    const RouteDecision wrongDecision =
        gem5::classifyLogicalStreamResponseRoute(wrong);
    CHECK(wrongDecision.result == Result::WrongKind);
    writeCount = applyCounter(Kind::Write, CounterEvent::ResponseRejected,
                              writeCount, false);
    CHECK(writeCount == 1);

    writeCount = applyCounter(Kind::Write, CounterEvent::ResponseAccepted,
                              writeCount, true);
    CHECK(writeCount == 0);

    Route duplicate = makeRoute();
    duplicate.expectedKind = Kind::Write;
    duplicate.receivedKind = Kind::Write;
    duplicate.ledgerResult = Result::Duplicate;
    const RouteDecision duplicateDecision =
        gem5::classifyLogicalStreamResponseRoute(duplicate);
    CHECK(duplicateDecision.result == Result::Duplicate);
    writeCount = applyCounter(Kind::Write, CounterEvent::ResponseRejected,
                              writeCount, false);
    CHECK(writeCount == 0);

    // Even an impossible accepted settlement at zero fails closed without
    // wrapping UINT32_MAX. Nonzero settlement reaches the exact boundary.
    const CounterDecision underflow =
        gem5::decideLogicalStreamCounterUpdate(
            Kind::Write, CounterEvent::ResponseAccepted, 0);
    CHECK(!underflow.valid);
    CHECK(!underflow.changed);
    CHECK(underflow.value == 0);
    CHECK(applyCounter(Kind::Read, CounterEvent::SendAccepted, 2, true) == 1);
    CHECK(applyCounter(Kind::Write, CounterEvent::ResponseAccepted, 2, true) ==
          1);

    const uint32_t maximum = std::numeric_limits<uint32_t>::max();
    const CounterDecision overflow =
        gem5::decideLogicalStreamCounterUpdate(
            Kind::Read, CounterEvent::Enqueued, maximum);
    CHECK(!overflow.valid);
    CHECK(!overflow.changed);
    CHECK(overflow.value == maximum);

    const CounterDecision aborted =
        gem5::decideLogicalStreamCounterUpdate(
            Kind::Write, CounterEvent::ResponseAborted, 1);
    CHECK(aborted.valid);
    CHECK(aborted.changed);
    CHECK(aborted.value == 0);
    const CounterDecision abortedUnderflow =
        gem5::decideLogicalStreamCounterUpdate(
            Kind::Write, CounterEvent::ResponseAborted, 0);
    CHECK(!abortedUnderflow.valid);
    CHECK(!abortedUnderflow.changed);
    CHECK(abortedUnderflow.value == 0);

    // An exact fatal callback can own a request that never reached a port.
    // Every such request kind still owns its enqueue count exactly once.
    for (Kind kind : {Kind::Read, Kind::ReadEx, Kind::Write}) {
        const CounterDecision unsentAbort =
            gem5::decideLogicalStreamCounterUpdate(
                kind, CounterEvent::UnsentPacketAborted, 1);
        CHECK(unsentAbort.valid);
        CHECK(unsentAbort.changed);
        CHECK(unsentAbort.value == 0);
        const CounterDecision unsentUnderflow =
            gem5::decideLogicalStreamCounterUpdate(
                kind, CounterEvent::UnsentPacketAborted, 0);
        CHECK(!unsentUnderflow.valid);
        CHECK(!unsentUnderflow.changed);
        CHECK(unsentUnderflow.value == 0);
    }
    const CounterDecision sentReadAbort =
        gem5::decideLogicalStreamCounterUpdate(
            Kind::Read, CounterEvent::ResponseAborted, 0);
    CHECK(sentReadAbort.valid);
    CHECK(!sentReadAbort.changed);
}

void
testProductionAliasAndSenderStateProofs()
{
    const auto exact = gem5::classifyResponsePacketAliases(
        AliasShape{1, 0, 0, 0}, true, true);
    CHECK(exact.acceptedExactOwner);
    CHECK(!exact.droppableExtra);
    CHECK(exact.detachBeforeDestruction);

    for (AliasShape retained : {
             AliasShape{1, 1, 0, 0},  // deferred address queue
             AliasShape{1, 0, 1, 0},  // cache send/retry queue
             AliasShape{1, 0, 0, 1},  // memory send/retry queue
             AliasShape{0, 1, 1, 1},  // corrupt ownerless pending aliases
         }) {
        const auto decision = gem5::classifyResponsePacketAliases(
            retained, retained.outstanding == 1, true);
        CHECK(!decision.acceptedExactOwner);
        CHECK(!decision.droppableExtra);
        CHECK(decision.detachBeforeDestruction);
    }
    const auto unsent = gem5::classifyResponsePacketAliases(
        AliasShape{1, 0, 1, 0}, true, false);
    CHECK(!unsent.acceptedExactOwner);
    CHECK(unsent.detachBeforeDestruction);
    const auto independentExtra = gem5::classifyResponsePacketAliases(
        AliasShape{}, false, false);
    CHECK(independentExtra.droppableExtra);
    CHECK(!independentExtra.detachBeforeDestruction);

    const auto legalNormal = gem5::classifyResponseSenderState(
        SenderShape{true, true, 0, false, false}, false);
    CHECK(legalNormal.valid);
    CHECK(legalNormal.preservePredecessor);
    CHECK(!legalNormal.releaseLogicalTop);
    const auto legalLegacyPredecessor = gem5::classifyResponseSenderState(
        SenderShape{true, true, 0, false, false}, false);
    CHECK(legalLegacyPredecessor.valid);
    CHECK(legalLegacyPredecessor.preservePredecessor);
    const auto legalLogical = gem5::classifyResponseSenderState(
        SenderShape{true, true, 1, true, false}, true);
    CHECK(legalLogical.valid);
    CHECK(legalLogical.releaseLogicalTop);
    CHECK(legalLogical.preservePredecessor);

    for (SenderShape rejected : {
             SenderShape{false, true, 0, false, false}, // cycle/over-depth
             SenderShape{true, false, 0, false, false}, // arbitrary node
             SenderShape{true, true, 1, true, false},   // logical in normal
             SenderShape{true, true, 0, false, true},  // aliased node
         }) {
        CHECK(!gem5::classifyResponseSenderState(rejected, false).valid);
    }
    for (SenderShape rejected : {
             SenderShape{false, true, 1, true, false},
             SenderShape{true, false, 1, true, false},
             SenderShape{true, true, 1, false, false},
             SenderShape{true, true, 2, true, false},
             SenderShape{true, true, 1, true, true},
         }) {
        CHECK(!gem5::classifyResponseSenderState(rejected, true).valid);
    }
}

void
testExactPointerDispositionAndExtraPacketIsolation()
{
    for (Kind kind : {Kind::Read, Kind::ReadEx, Kind::Write}) {
        Route route = makeRoute();
        route.expectedKind = kind;
        route.receivedKind = kind;
        const DispositionDecision accepted =
            gem5::classifyLogicalStreamResponseDisposition(
                route, true, true);
        CHECK(accepted.accepts());
        CHECK(accepted.result == Result::Accepted);
        CHECK(accepted.disposition == Disposition::Retired);
        CHECK(accepted.retireOutstanding);
        CHECK(accepted.popSenderState);
        CHECK(accepted.settleStreamCredit);

        const DispositionDecision wrongPacket =
            gem5::classifyLogicalStreamResponseDisposition(
                route, false, true);
        CHECK(!wrongPacket.accepts());
        CHECK(wrongPacket.result == Result::WrongPacket);
        CHECK(wrongPacket.disposition == Disposition::DroppedExtra);
        CHECK(!wrongPacket.retireOutstanding);
        CHECK(wrongPacket.popSenderState);
        CHECK(!wrongPacket.settleStreamCredit);
    }

    Route duplicate = makeRoute();
    duplicate.ledgerResult = Result::Duplicate;
    const DispositionDecision duplicateExtra =
        gem5::classifyLogicalStreamResponseDisposition(
            duplicate, false, true);
    CHECK(duplicateExtra.result == Result::Duplicate);
    CHECK(duplicateExtra.disposition == Disposition::DroppedExtra);
    CHECK(!duplicateExtra.retireOutstanding);
    CHECK(duplicateExtra.popSenderState);
    CHECK(!duplicateExtra.settleStreamCredit);

    Route unowned = makeRoute();
    unowned.hasOutstanding = false;
    const DispositionDecision staleExtra =
        gem5::classifyLogicalStreamResponseDisposition(
            unowned, false, true);
    CHECK(staleExtra.result == Result::Stale);
    CHECK(staleExtra.disposition == Disposition::DroppedExtra);
    CHECK(!staleExtra.retireOutstanding);
    CHECK(staleExtra.popSenderState);

    const DispositionDecision aliasedExtra =
        gem5::classifyLogicalStreamResponseDisposition(
            unowned, false, false);
    CHECK(aliasedExtra.disposition == Disposition::FatalUnownedExtra);
    CHECK(!aliasedExtra.retireOutstanding);
    CHECK(!aliasedExtra.popSenderState);
    CHECK(!aliasedExtra.settleStreamCredit);

    for (Result corruption : {Result::WrongKind, Result::WrongTransaction,
                              Result::WrongAddress, Result::Invalid}) {
        Route exactCorrupt = makeRoute();
        exactCorrupt.ledgerResult = corruption;
        const DispositionDecision fatal =
            gem5::classifyLogicalStreamResponseDisposition(
                exactCorrupt, true, true);
        CHECK(!fatal.accepts());
        CHECK(fatal.result == corruption);
        CHECK(fatal.disposition == Disposition::FatalOwnedCorruption);
        CHECK(fatal.retireOutstanding);
        CHECK(fatal.popSenderState);
        CHECK(fatal.settleStreamCredit);
    }
}

void
testRealWrapperInvocationAndCreditLifetime()
{
    const auto run = [](Disposition disposition, uint32_t *credit,
                        int &receiveCount, int &settleCount,
                        int &deleteCount, int &fatalCount,
                        bool &deletePrecededFatal) {
        const bool fatalDisposition =
            disposition == Disposition::FatalOwnedCorruption ||
            disposition == Disposition::FatalOwnedNoPortCredit ||
            disposition == Disposition::FatalUnownedExtra;
        const bool creditUnderflow =
            credit != nullptr &&
            (disposition == Disposition::Retired ||
             disposition == Disposition::FatalOwnedCorruption) &&
            *credit == 0;
        return gem5::invokeTimingResponseWrapper(
            credit,
            [&]() {
                ++receiveCount;
                return disposition;
            },
            [&]() { ++settleCount; },
            [&]() { ++deleteCount; },
            [&](Disposition received, bool commitOwnerCompletion) {
                CHECK(received == disposition);
                CHECK(deleteCount == 1);
                CHECK(commitOwnerCompletion ==
                      (!fatalDisposition && !creditUnderflow));
            },
            [&](Disposition received, bool valid) {
                CHECK(received == disposition);
                CHECK(!valid || fatalDisposition);
                deletePrecededFatal = deleteCount == 1;
                ++fatalCount;
            });
    };

    uint32_t credit = 1;
    int receives = 0;
    int settles = 0;
    int deletions = 0;
    int fatals = 0;
    bool deletePrecededFatal = false;
    CHECK(run(Disposition::Retired, &credit, receives, settles, deletions,
              fatals, deletePrecededFatal));
    CHECK(credit == 0);
    CHECK(receives == 1);
    CHECK(settles == 1);
    CHECK(deletions == 1);
    CHECK(fatals == 0);

    credit = 1;
    receives = settles = deletions = fatals = 0;
    CHECK(run(Disposition::DroppedExtra, &credit, receives, settles,
              deletions, fatals, deletePrecededFatal));
    CHECK(credit == 1);
    CHECK(receives == 1);
    CHECK(settles == 0);
    CHECK(deletions == 1);
    CHECK(fatals == 0);

    credit = 1;
    receives = settles = deletions = fatals = 0;
    deletePrecededFatal = false;
    CHECK(run(Disposition::FatalOwnedCorruption, &credit, receives, settles,
              deletions, fatals, deletePrecededFatal));
    CHECK(credit == 0);
    CHECK(receives == 1);
    CHECK(settles == 1);
    CHECK(deletions == 1);
    CHECK(fatals == 1);
    CHECK(deletePrecededFatal);

    // Exact unsent corruption owns its map/stream count but no accepted port
    // credit, so it must not debit an unrelated live cache credit.
    credit = 1;
    receives = settles = deletions = fatals = 0;
    deletePrecededFatal = false;
    CHECK(run(Disposition::FatalOwnedNoPortCredit, &credit, receives,
              settles, deletions, fatals, deletePrecededFatal));
    CHECK(credit == 1);
    CHECK(settles == 0);
    CHECK(deletions == 1);
    CHECK(fatals == 1);
    CHECK(deletePrecededFatal);

    // An unsafe non-exact packet is also terminal, but it has no authority to
    // settle the active request's credit.
    credit = 1;
    receives = settles = deletions = fatals = 0;
    deletePrecededFatal = false;
    CHECK(run(Disposition::FatalUnownedExtra, &credit, receives, settles,
              deletions, fatals, deletePrecededFatal));
    CHECK(credit == 1);
    CHECK(receives == 1);
    CHECK(settles == 0);
    CHECK(deletions == 1);
    CHECK(fatals == 1);
    CHECK(deletePrecededFatal);

    // An impossible accepted response at zero is destroyed and fails closed
    // without wrapping the unsigned credit counter.
    credit = 0;
    receives = settles = deletions = fatals = 0;
    deletePrecededFatal = false;
    CHECK(run(Disposition::Retired, &credit, receives, settles, deletions,
              fatals, deletePrecededFatal));
    CHECK(credit == 0);
    CHECK(receives == 1);
    CHECK(settles == 0);
    CHECK(deletions == 1);
    CHECK(fatals == 1);
    CHECK(deletePrecededFatal);

    const uint32_t maximum = std::numeric_limits<uint32_t>::max();
    const WrapperDecision maxDrop =
        gem5::decideTimingResponseWrapperUpdate(
            Disposition::DroppedExtra, maximum, true);
    CHECK(maxDrop.valid);
    CHECK(!maxDrop.creditChanged);
    CHECK(maxDrop.creditValue == maximum);
    CHECK(!maxDrop.settlePortCredit);
    CHECK(maxDrop.deletePacket);
    CHECK(!maxDrop.sendRetry);
    const WrapperDecision maxRetire =
        gem5::decideTimingResponseWrapperUpdate(
            Disposition::Retired, maximum, true);
    CHECK(maxRetire.valid);
    CHECK(maxRetire.creditChanged);
    CHECK(maxRetire.creditValue == maximum - 1);

    // The memory wrapper invokes the same executor without a local credit.
    receives = settles = deletions = fatals = 0;
    CHECK(run(Disposition::Retired, nullptr, receives, settles, deletions,
              fatals, deletePrecededFatal));
    CHECK(receives == 1);
    CHECK(settles == 0);
    CHECK(deletions == 1);
    CHECK(fatals == 0);
}

void
testExactResponseCreditOwnerRouting()
{
    const auto classify = gem5::classifyTimingResponseCreditOwner;

    // Unsent packets and memory-side requests never debit a cache wrapper.
    CHECK(classify(false, true, true) == CreditOwner::None);
    CHECK(classify(false, true, false) == CreditOwner::None);
    CHECK(classify(true, false, true) == CreditOwner::None);
    CHECK(classify(true, false, false) == CreditOwner::None);

    // A callback on the exact cache port settles locally. A cache-owned
    // response arriving through memory or another cache port settles the
    // recorded owner instead and cannot debit the callback's foreign credit.
    CHECK(classify(true, true, true) == CreditOwner::CallbackPort);
    CHECK(classify(true, true, false) ==
          CreditOwner::ExpectedCachePort);
}

void
testExactResponseSizeOnCacheAndMemoryWrappers()
{
    const std::size_t maximum = std::numeric_limits<std::size_t>::max();
    for (Kind kind : {Kind::Read, Kind::ReadEx, Kind::Write}) {
        for (std::size_t expected : {std::size_t{64}, std::size_t{8}}) {
            Route route = makeRoute();
            route.expectedKind = kind;
            route.receivedKind = kind;
            CHECK(gem5::classifyLogicalStreamResponseRoute(route).accepts());
            CHECK(gem5::hasExpectedLogicalStreamResponseSize(expected,
                                                             expected));
            for (std::size_t wrong : {std::size_t{0}, std::size_t{1},
                                      expected - 1, expected + 1,
                                      maximum - 1, maximum}) {
                CHECK(!gem5::hasExpectedLogicalStreamResponseSize(wrong,
                                                                  expected));
                for (bool cacheWrapper : {false, true}) {
                    uint32_t credit = 1;
                    uint32_t *tracked = cacheWrapper ? &credit : nullptr;
                    int deleted = 0;
                    int fatal = 0;
                    CHECK(gem5::invokeTimingResponseWrapper(
                        tracked,
                        []() {
                            return Disposition::FatalOwnedCorruption;
                        },
                        []() {},
                        [&]() { ++deleted; },
                        [&](Disposition, bool commitOwnerCompletion) {
                            CHECK(deleted == 1);
                            CHECK(!commitOwnerCompletion);
                        },
                        [&](Disposition, bool valid) {
                            CHECK(valid);
                            CHECK(deleted == 1);
                            ++fatal;
                        }));
                    CHECK(deleted == 1);
                    CHECK(fatal == 1);
                    CHECK(credit == (cacheWrapper ? 0U : 1U));
                }
            }
        }
    }
    CHECK(!gem5::hasExpectedLogicalStreamResponseSize(64, 0));
    CHECK(!gem5::hasExpectedLogicalStreamResponseSize(65, 65));
}

void
testRetirementOwnerRejectionOccursAfterDestruction()
{
    struct OwnerRejected
    {};
    uint32_t credit = 1;
    int deleted = 0;
    int ownerChecks = 0;
    int failClosed = 0;
    bool rejected = false;
    try {
        gem5::invokeTimingResponseWrapper(
            &credit,
            []() { return Disposition::Retired; },
            []() {},
            [&]() { ++deleted; },
            [&](Disposition, bool commitOwnerCompletion) {
                CHECK(commitOwnerCompletion);
                CHECK(credit == 0);
                CHECK(deleted == 1);
                ++ownerChecks;
                throw OwnerRejected{};
            },
            [&](Disposition, bool) { ++failClosed; });
    } catch (const OwnerRejected &) {
        rejected = true;
    }
    CHECK(rejected);
    CHECK(credit == 0);
    CHECK(deleted == 1);
    CHECK(ownerChecks == 1);
    CHECK(failClosed == 0);
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
testFatalOwnedLedgerEntriesAreSettled()
{
    Ledger fill;
    const Tag fillTag = makeTag(73, Action::Fill);
    CHECK(fill.begin(fillTag, 1) == Result::Accepted);
    CHECK(fill.issueLine(fillTag, 0x1800, Kind::Read) == Result::Accepted);
    CHECK(fill.abortResponse(fillTag, 0x1800, Kind::Read) ==
          Result::Accepted);
    CHECK(fill.abortedLineCount() == 1);
    CHECK(fill.acknowledgedLineCount() == 0);
    CHECK(fill.validateResponse(fillTag, 0x1800, Kind::Read) ==
          Result::Duplicate);

    Ledger ownedFill;
    CHECK(ownedFill.begin(fillTag, 1) == Result::Accepted);
    CHECK(ownedFill.issueLine(fillTag, 0x1840, Kind::Read) ==
          Result::Accepted);
    CHECK(ownedFill.abortOwnedResponse(0x1840) == Result::Accepted);
    CHECK(ownedFill.abortedLineCount() == 1);

    Ledger writebackRead;
    const Tag writeTag = makeTag(74, Action::Writeback);
    CHECK(writebackRead.begin(writeTag, 1) == Result::Accepted);
    CHECK(writebackRead.issueLine(writeTag, 0x1c00, Kind::ReadEx) ==
          Result::Accepted);
    CHECK(writebackRead.abortResponse(writeTag, 0x1c00, Kind::ReadEx) ==
          Result::Accepted);
    CHECK(writebackRead.abortedLineCount() == 1);
    CHECK(writebackRead.validateResponse(writeTag, 0x1c00, Kind::ReadEx) ==
          Result::Duplicate);

    Ledger writebackWrite;
    CHECK(writebackWrite.begin(writeTag, 1) == Result::Accepted);
    CHECK(writebackWrite.issueLine(writeTag, 0x2000, Kind::ReadEx) ==
          Result::Accepted);
    CHECK(writebackWrite.acceptResponse(writeTag, 0x2000, Kind::ReadEx) ==
          Result::Accepted);
    CHECK(writebackWrite.issueLine(writeTag, 0x2000, Kind::Write) ==
          Result::Accepted);
    CHECK(writebackWrite.abortResponse(writeTag, 0x2000, Kind::Write) ==
          Result::Accepted);
    CHECK(writebackWrite.abortedLineCount() == 1);
    CHECK(writebackWrite.validateResponse(writeTag, 0x2000, Kind::Write) ==
          Result::Duplicate);
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

void
testOrphanedDeferredAndSendOwnersAreFatalAndSettled()
{
    const auto deferred = gem5::classifyOrphanedLogicalPacket(
        OrphanShape{1, 0, true});
    CHECK(deferred.fatal);
    CHECK(deferred.detachAliases);
    CHECK(deferred.abortLedger);
    CHECK(!deferred.settleCounter);

    const auto send = gem5::classifyOrphanedLogicalPacket(
        OrphanShape{0, 2, true});
    CHECK(send.fatal);
    CHECK(send.detachAliases);
    CHECK(send.abortLedger);
    CHECK(send.settleCounter);

    // Two different lifecycle owners cannot authorize a guessed settlement.
    const auto ambiguous = gem5::classifyOrphanedLogicalPacket(
        OrphanShape{1, 1, true});
    CHECK(ambiguous.fatal);
    CHECK(ambiguous.detachAliases);
    CHECK(!ambiguous.abortLedger);
    CHECK(!ambiguous.settleCounter);

    const auto independent = gem5::classifyOrphanedLogicalPacket(
        OrphanShape{});
    CHECK(!independent.fatal);
    CHECK(!independent.detachAliases);
}

void
testNormalFatalOwnersSettleEveryRequestKind()
{
    for (Kind kind : {Kind::Read, Kind::ReadEx}) {
        const auto unsent = gem5::decideNormalFatalOwnerSettlement(
            kind, false, false);
        CHECK(unsent.settleOutstandingCounter);
        CHECK(unsent.abortReadOwner);
        CHECK(!unsent.abortRetirementWrite);

        const auto sent = gem5::decideNormalFatalOwnerSettlement(
            kind, true, false);
        CHECK(!sent.settleOutstandingCounter);
        CHECK(sent.abortReadOwner);
        CHECK(!sent.abortRetirementWrite);
    }

    const auto corruptRetirement =
        gem5::decideNormalFatalOwnerSettlement(Kind::Write, true, true);
    CHECK(corruptRetirement.settleOutstandingCounter);
    CHECK(!corruptRetirement.abortReadOwner);
    CHECK(corruptRetirement.abortRetirementWrite);
    const auto unsentWrite =
        gem5::decideNormalFatalOwnerSettlement(Kind::Write, false, true);
    CHECK(unsentWrite.settleOutstandingCounter);
    CHECK(!unsentWrite.abortReadOwner);
    CHECK(unsentWrite.abortRetirementWrite);
}

void
testSameLineRmwContinuationPrecedesDeferredFifo()
{
    CHECK(gem5::mustBypassDeferredForContinuation(
        true, Kind::Write, true, true));
    CHECK(!gem5::mustBypassDeferredForContinuation(
        false, Kind::Write, true, true));
    CHECK(!gem5::mustBypassDeferredForContinuation(
        true, Kind::ReadEx, true, true));
    CHECK(!gem5::mustBypassDeferredForContinuation(
        true, Kind::Write, false, true));
    CHECK(!gem5::mustBypassDeferredForContinuation(
        true, Kind::Write, true, false));

    // The continuation takes the just-released address; unrelated ordinary
    // traffic remains in its original FIFO order for later promotion.
    const int ordinaryFifo[] = {11, 12, 13};
    CHECK(ordinaryFifo[0] == 11);
    CHECK(ordinaryFifo[1] == 12);
    CHECK(ordinaryFifo[2] == 13);
}

void
testCreditAndCounterPreflightPrecedesEveryAcceptedMutation()
{
    const Preflight valid{true, true, true, true, true, true, true};
    CHECK(gem5::canMutateAcceptedResponse(valid));
    for (std::size_t field = 0; field < 7; ++field) {
        Preflight rejected = valid;
        bool *const fields[] = {
            &rejected.ownerValid,
            &rejected.aliasesValid,
            &rejected.senderStateValid,
            &rejected.routeValid,
            &rejected.ledgerValid,
            &rejected.counterValid,
            &rejected.creditValid,
        };
        *fields[field] = false;
        uint8_t unrelated[] = {0x11, 0x22, 0x33, 0x44};
        const uint8_t before[] = {0x11, 0x22, 0x33, 0x44};
        CHECK(!gem5::canMutateAcceptedResponse(rejected));
        for (std::size_t byte = 0; byte < sizeof(unrelated); ++byte)
            CHECK(unrelated[byte] == before[byte]);
    }
}

void
testInvalidMapOwnerMetadataCannotBypassLedgerOrCounterCleanup()
{
    Ledger ledger;
    const Tag discoverable = makeTag(901, Action::Fill, 1, 2, 3, 0, 7);
    CHECK(ledger.begin(discoverable, 1) == Result::Accepted);
    CHECK(ledger.issueLine(discoverable, 0x7100, Kind::Read) ==
          Result::Accepted);

    // Map maaID/funcUnit vectors may be corrupt; the immutable transaction tag
    // and exact line still identify one ledger owner.
    CHECK(ledger.abortOwnedResponse(0x7100) == Result::Accepted);
    CHECK(ledger.abortedLineCount() == 1);
    CHECK(ledger.abortOwnedResponse(0x7100) == Result::Duplicate);
    const CounterDecision counter = gem5::decideLogicalStreamCounterUpdate(
        Kind::Read, CounterEvent::UnsentPacketAborted, 1);
    CHECK(counter.valid);
    CHECK(counter.changed);
    CHECK(counter.value == 0);
}

void
testDeferredPromotionIsAtomicOrTerminal()
{
    const DeferredShape valid{true, true, true, true, true, true, true, true,
                              true, true, true, true, true};
    CHECK(gem5::canPromoteDeferredPacket(valid));
    CHECK(gem5::decideDeferredPromotion(valid, false).retainRetryableOwner);
    CHECK(!gem5::decideDeferredPromotion(valid, false).terminalCleanup);
    for (std::size_t field = 0; field < 13; ++field) {
        DeferredShape malformed = valid;
        bool *const fields[] = {
            &malformed.packetPresent,
            &malformed.requestPresent,
            &malformed.addressMatches,
            &malformed.commandMatches,
            &malformed.ownerValid,
            &malformed.routeValid,
            &malformed.logicalIdentityMatches,
            &malformed.senderStateMatches,
            &malformed.exactPortValid,
            &malformed.admissionCapacity,
            &malformed.counterHeadroom,
            &malformed.mapSlotFree,
            &malformed.uniqueDeferredOwner,
        };
        *fields[field] = false;
        CHECK(!gem5::canPromoteDeferredPacket(malformed));
        const auto terminal = gem5::decideDeferredPromotion(malformed, false);
        CHECK(terminal.detachDeferred);
        CHECK(!terminal.retainRetryableOwner);
        CHECK(terminal.terminalCleanup);
    }
}

void
testTeardownRejectsEverySurvivingOwnerClass()
{
    CHECK(gem5::canDestroyResponseSubstrate(TeardownShape{}));
    for (TeardownShape live : {
             TeardownShape{1, 0, 0, 0, 0, 0, 0, 0, false},
             TeardownShape{0, 2, 0, 0, 0, 0, 0, 0, false},
             TeardownShape{0, 0, 3, 0, 0, 0, 0, 0, false},
             TeardownShape{0, 0, 0, 4, 0, 0, 0, 0, false},
             TeardownShape{0, 0, 0, 0, 1, 0, 0, 0, false},
             TeardownShape{0, 0, 0, 0, 0, 1, 0, 0, false},
             TeardownShape{0, 0, 0, 0, 0, 0, 1, 0, false},
             TeardownShape{0, 0, 0, 0, 0, 0, 0, 1, false},
             TeardownShape{0, 0, 0, 0, 0, 0, 0, 0, true},
         }) {
        CHECK(!gem5::canDestroyResponseSubstrate(live));
    }
}

} // anonymous namespace

int
main()
{
    testCommandSpecificCounterOwnershipAndRetries();
    testProductionAliasAndSenderStateProofs();
    testExactPointerDispositionAndExtraPacketIsolation();
    testRealWrapperInvocationAndCreditLifetime();
    testExactResponseCreditOwnerRouting();
    testExactResponseSizeOnCacheAndMemoryWrappers();
    testRetirementOwnerRejectionOccursAfterDestruction();
    testPortRouteOwnershipIsPureAndFailClosed();
    testFillDelayedReorderedDuplicateCallbacks();
    testFatalOwnedLedgerEntriesAreSettled();
    testWritebackReadExCallbacksAreExactlyOnce();
    testWritebackMismatchesCannotAcknowledgeTerminalLines();
    testStaleAndOldTransactionsCannotCompleteReusedAddress();
    testFixedLedgerCapacityAndUntaggedPathExclusion();
    testOrphanedDeferredAndSendOwnersAreFatalAndSettled();
    testNormalFatalOwnersSettleEveryRequestKind();
    testSameLineRmwContinuationPrecedesDeferredFifo();
    testCreditAndCounterPreflightPrecedesEveryAcceptedMutation();
    testInvalidMapOwnerMetadataCannotBypassLedgerOrCounterCleanup();
    testDeferredPromotionIsAtomicOrTerminal();
    testTeardownRejectsEverySurvivingOwnerClass();
    std::cout << "logical_stream_response_test: PASS" << std::endl;
    return 0;
}
