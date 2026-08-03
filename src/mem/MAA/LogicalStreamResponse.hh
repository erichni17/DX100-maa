#ifndef __MEM_MAA_LOGICAL_STREAM_RESPONSE_HH__
#define __MEM_MAA_LOGICAL_STREAM_RESPONSE_HH__

#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <limits>

#include "base/types.hh"

namespace gem5 {

/**
 * Identity carried by every response-bearing logical stream packet.
 *
 * A physical address intentionally is not part of the transaction identity:
 * several controller transactions can legally visit the same cache line over
 * time.  The line address is instead checked against the fixed ledger owned
 * by the active transaction.
 */
enum class LogicalStreamAction : uint8_t
{
    None = 0,
    Fill,
    Writeback,
};

struct LogicalStreamTransactionTag
{
    static constexpr uint16_t InvalidMAA =
        std::numeric_limits<uint16_t>::max();
    static constexpr uint16_t InvalidLogical =
        std::numeric_limits<uint16_t>::max();
    static constexpr uint16_t InvalidPage =
        std::numeric_limits<uint16_t>::max();
    static constexpr int16_t InvalidSlot = -1;

    uint16_t maaID = InvalidMAA;
    uint64_t transactionID = 0;
    LogicalStreamAction action = LogicalStreamAction::None;
    uint16_t logicalID = InvalidLogical;
    uint16_t page = InvalidPage;
    uint64_t generation = 0;
    int16_t slot = InvalidSlot;

    bool valid() const {
        return maaID != InvalidMAA && transactionID != 0 &&
               action != LogicalStreamAction::None &&
               logicalID != InvalidLogical && page != InvalidPage &&
               generation != 0 && slot != InvalidSlot;
    }

    bool operator==(const LogicalStreamTransactionTag &other) const {
        return maaID == other.maaID && transactionID == other.transactionID &&
               action == other.action && logicalID == other.logicalID &&
               page == other.page && generation == other.generation &&
               slot == other.slot;
    }

    bool operator!=(const LogicalStreamTransactionTag &other) const {
        return !(*this == other);
    }
};

enum class LogicalStreamResponseKind : uint8_t
{
    Read,
    ReadEx,
    Write,
};

enum class LogicalStreamResponseResult : uint8_t
{
    Accepted,
    Completed,
    Stale,
    Duplicate,
    WrongKind,
    WrongTransaction,
    WrongPage,
    WrongSlot,
    WrongMAA,
    WrongAddress,
    WrongPacket,
    Invalid,
};

/**
 * Complete ownership result returned from MAA::recvTimingResp to its port.
 *
 * A timing response is never retried by this path.  Retired consumes the
 * exact map-owned packet and settles its port credit.  DroppedExtra consumes
 * a different, safely releasable tagged packet without touching the active
 * map entry or its credit.  FatalOwnedCorruption consumes and settles an
 * exact sent packet after Port.cc removes every owned pointer.
 * FatalOwnedNoPortCredit consumes an exact packet which never acquired the
 * callback port's credit (or arrived through the wrong wrapper).
 * FatalUnownedExtra consumes an unsafe non-exact packet without settling the
 * active port credit. All fatal dispositions terminate after wrapper
 * destruction.
 */
enum class TimingResponseDisposition : uint8_t
{
    Retired,
    DroppedExtra,
    FatalOwnedCorruption,
    FatalOwnedNoPortCredit,
    FatalUnownedExtra,
};

enum class TimingResponseCreditOwner : uint8_t
{
    None,
    CallbackPort,
    ExpectedCachePort,
};

/**
 * Select the sole port credit which an exact owned fatal response may settle.
 * An unsent packet and a memory-side request own no cache-port credit.  A
 * response on the expected cache port lets its wrapper settle locally; a
 * response on any other wrapper must settle the recorded cache port directly
 * and leave the callback wrapper's unrelated credit untouched.
 */
inline TimingResponseCreditOwner
classifyTimingResponseCreditOwner(bool requestSent,
                                  bool expectedCacheCredit,
                                  bool callbackIsExpectedPort)
{
    if (!requestSent || !expectedCacheCredit)
        return TimingResponseCreditOwner::None;
    return callbackIsExpectedPort
        ? TimingResponseCreditOwner::CallbackPort
        : TimingResponseCreditOwner::ExpectedCachePort;
}

struct TimingResponseWrapperDecision
{
    uint32_t creditValue = 0;
    bool creditChanged = false;
    bool settlePortCredit = false;
    bool deletePacket = true;
    bool sendRetry = false;
    bool failClosed = false;
    bool valid = true;
};

/**
 * Bounded port-credit transition shared by the real cache and memory
 * wrappers and the dependency-light host replay.
 */
inline TimingResponseWrapperDecision
decideTimingResponseWrapperUpdate(TimingResponseDisposition disposition,
                                  uint32_t currentCredit,
                                  bool tracksCredit)
{
    const bool settles =
        disposition == TimingResponseDisposition::Retired ||
        disposition == TimingResponseDisposition::FatalOwnedCorruption;
    const bool fatal =
        disposition == TimingResponseDisposition::FatalOwnedCorruption ||
        disposition == TimingResponseDisposition::FatalOwnedNoPortCredit ||
        disposition == TimingResponseDisposition::FatalUnownedExtra;
    if (tracksCredit && settles && currentCredit == 0) {
        return {currentCredit, false, true, true, false, true, false};
    }
    return {tracksCredit && settles ? currentCredit - 1 : currentCredit,
            tracksCredit && settles, settles, true, false, fatal, true};
}

/**
 * Invoke one complete RequestPort response callback.
 *
 * Both production wrappers call this exact helper.  Deletion precedes a
 * fail-closed callback so fatal corruption cannot leave the wrapper with
 * a live packet, and every returning path returns true: returning false would
 * make the ResponsePort retain the packet indefinitely pending recvRespRetry.
 */
template <typename Receive, typename CreditSettled, typename DeletePacket,
          typename AfterDelete, typename FailClosed>
inline bool
invokeTimingResponseWrapper(uint32_t *outstandingCredit, Receive &&receive,
                            CreditSettled &&creditSettled,
                            DeletePacket &&deletePacket,
                            AfterDelete &&afterDelete,
                            FailClosed &&failClosed)
{
    const TimingResponseDisposition disposition = receive();
    const bool tracksCredit = outstandingCredit != nullptr;
    const uint32_t currentCredit =
        tracksCredit ? *outstandingCredit : 0;
    const TimingResponseWrapperDecision decision =
        decideTimingResponseWrapperUpdate(disposition, currentCredit,
                                          tracksCredit);
    if (decision.valid && decision.creditChanged) {
        *outstandingCredit = decision.creditValue;
        creditSettled();
    }
    if (decision.deletePacket)
        deletePacket();
    afterDelete(disposition, decision.valid && !decision.failClosed);
    if (!decision.valid || decision.failClosed)
        failClosed(disposition, decision.valid);
    return true;
}

/**
 * Immutable input to Port.cc's logical-response ownership decision.
 *
 * The port owns an outstanding entry only when every field below agrees.
 * This small, packet-free representation keeps that decision host-testable:
 * the caller performs no erase, sender-state pop, or ledger acknowledgement
 * unless the returned decision authorizes both mutations.
 */
struct LogicalStreamResponseRoute
{
    bool hasOutstanding = false;
    bool outstandingIsLogical = false;
    bool hasLogicalSenderState = false;
    LogicalStreamTransactionTag expectedTag{};
    LogicalStreamTransactionTag receivedTag{};
    Addr outstandingAddress = 0;
    Addr senderLineAddress = 0;
    Addr responseAddress = 0;
    LogicalStreamResponseKind expectedKind = LogicalStreamResponseKind::Read;
    LogicalStreamResponseKind receivedKind = LogicalStreamResponseKind::Read;
    LogicalStreamResponseResult ledgerResult =
        LogicalStreamResponseResult::Accepted;
};

struct LogicalStreamResponseRouteDecision
{
    LogicalStreamResponseResult result = LogicalStreamResponseResult::Stale;
    bool retireOutstanding = false;
    bool popSenderState = false;

    bool accepts() const
    {
        return result == LogicalStreamResponseResult::Accepted;
    }

    bool authorizesOutstandingRetirement() const
    {
        return retireOutstanding;
    }

    bool authorizesSenderStatePop() const
    {
        return popSenderState;
    }
};

struct LogicalStreamResponseDispositionDecision
{
    LogicalStreamResponseResult result = LogicalStreamResponseResult::Stale;
    TimingResponseDisposition disposition =
        TimingResponseDisposition::DroppedExtra;
    bool retireOutstanding = false;
    bool popSenderState = false;
    bool settleStreamCredit = false;

    bool accepts() const
    {
        return result == LogicalStreamResponseResult::Accepted &&
               disposition == TimingResponseDisposition::Retired;
    }
};

/**
 * Lifecycle events that can affect the stream packet counter.
 *
 * Deferred packets do not enter this lifecycle until Port.cc promotes them
 * into its outstanding map.  A rejected send is a retry, and a rejected
 * response includes every stale, duplicate, or identity-mismatched callback.
 */
enum class LogicalStreamCounterEvent : uint8_t
{
    Enqueued,
    SendRejected,
    SendAccepted,
    ResponseRejected,
    ResponseAccepted,
    ResponseAborted,
    UnsentPacketAborted,
};

struct LogicalStreamCounterDecision
{
    uint32_t value = 0;
    bool changed = false;
    bool valid = true;
};

/**
 * Pure command-specific ownership transition for
 * MAA::my_num_outstanding_stream_pkts.
 *
 * Read and ReadEx requests relinquish counter ownership when their request is
 * accepted by a memory-side port.  A response-bearing logical Write retains
 * ownership across its accepted send and relinquishes it only when the
 * fail-closed response route accepts the matching Write response.  Rejected
 * sends and responses never change the count.  Boundary failures also leave
 * the input unchanged, so neither unsigned increment overflow nor decrement
 * underflow is possible through this decision.
 */
inline LogicalStreamCounterDecision
decideLogicalStreamCounterUpdate(LogicalStreamResponseKind requestKind,
                                 LogicalStreamCounterEvent event,
                                 uint32_t currentValue)
{
    if (event == LogicalStreamCounterEvent::Enqueued) {
        if (currentValue == std::numeric_limits<uint32_t>::max())
            return {currentValue, false, false};
        return {currentValue + 1, true, true};
    }

    const bool readRequest = requestKind == LogicalStreamResponseKind::Read ||
                             requestKind == LogicalStreamResponseKind::ReadEx;
    const bool relinquishes =
        (readRequest && event == LogicalStreamCounterEvent::SendAccepted) ||
        event == LogicalStreamCounterEvent::UnsentPacketAborted ||
        (!readRequest &&
         (event == LogicalStreamCounterEvent::ResponseAccepted ||
          event == LogicalStreamCounterEvent::ResponseAborted));
    if (!relinquishes)
        return {currentValue, false, true};
    if (currentValue == 0)
        return {currentValue, false, false};
    return {currentValue - 1, true, true};
}

constexpr std::size_t ExpectedLogicalStreamResponseBytes = 64;
constexpr std::size_t MaxResponseSenderStateDepth = 64;

inline bool
hasExpectedLogicalStreamResponseSize(std::size_t responseBytes,
                                     std::size_t expectedBytes)
{
    return expectedBytes != 0 &&
           expectedBytes <= ExpectedLogicalStreamResponseBytes &&
           responseBytes == expectedBytes;
}

/**
 * Packet-free sender-state ownership proof shared by production and the host
 * replay.  Production records the exact predecessor chain when a request is
 * admitted.  A response may preserve precisely that bounded chain, with one
 * logical node at the top only for a logical request; it may not introduce,
 * remove, reorder, cycle, overrun, or share any node with another live MAA
 * packet.
 */
struct ResponseSenderStateShape
{
    bool boundedAcyclic = false;
    bool exactSnapshot = false;
    std::size_t logicalNodeCount = 0;
    bool logicalNodeAtTop = false;
    bool aliasedByOtherPacket = false;
};

struct ResponseSenderStateDecision
{
    bool valid = false;
    bool releaseLogicalTop = false;
    bool preservePredecessor = false;
};

struct ResponsePacketAliasShape
{
    std::size_t outstanding = 0;
    std::size_t deferred = 0;
    std::size_t cacheSend = 0;
    std::size_t memorySend = 0;
};

struct ResponsePacketAliasDecision
{
    bool acceptedExactOwner = false;
    bool droppableExtra = false;
    bool detachBeforeDestruction = false;
};

/**
 * Fail-closed ownership decision for a callback whose PacketPtr is no longer
 * present in the outstanding-address map.  Deferred entries have issued a
 * ledger line but do not own a port counter.  An unsent send-queue entry owns
 * both.  Any production alias makes the callback fatal rather than droppable;
 * cleanup is authorized only when all aliases agree on one logical owner.
 */
struct OrphanedLogicalPacketShape
{
    std::size_t deferredAliases = 0;
    std::size_t sendAliases = 0;
    bool uniqueLogicalOwner = false;
};

struct OrphanedLogicalPacketDecision
{
    bool fatal = false;
    bool detachAliases = false;
    bool abortLedger = false;
    bool settleCounter = false;
};

inline OrphanedLogicalPacketDecision
classifyOrphanedLogicalPacket(const OrphanedLogicalPacketShape &shape)
{
    const bool hasAliases = shape.deferredAliases != 0 ||
                            shape.sendAliases != 0;
    const bool oneLifecycle = shape.deferredAliases == 0 ||
                              shape.sendAliases == 0;
    const bool settle = hasAliases && oneLifecycle &&
                        shape.uniqueLogicalOwner;
    return {hasAliases, hasAliases, settle,
            settle && shape.sendAliases != 0};
}

/** Packet-free validation gate used before deferred queue ownership moves. */
struct DeferredPromotionShape
{
    bool packetPresent = false;
    bool requestPresent = false;
    bool addressMatches = false;
    bool commandMatches = false;
    bool ownerValid = false;
    bool routeValid = false;
    bool logicalIdentityMatches = false;
    bool senderStateMatches = false;
};

inline bool
canPromoteDeferredPacket(const DeferredPromotionShape &shape)
{
    return shape.packetPresent && shape.requestPresent &&
           shape.addressMatches && shape.commandMatches && shape.ownerValid &&
           shape.routeValid && shape.logicalIdentityMatches &&
           shape.senderStateMatches;
}

/**
 * Exact fatal-response cleanup which is independent of response corruption.
 * Reads relinquish their ordinary counter only when they never reached a
 * port.  Response-bearing writes retain both their port counter and retirement
 * metadata until every terminal callback, including a corrupt one.
 */
struct NormalFatalOwnerDecision
{
    bool settleOutstandingCounter = false;
    bool settleRetirementWrite = false;
};

inline NormalFatalOwnerDecision
decideNormalFatalOwnerSettlement(LogicalStreamResponseKind requestKind,
                                 bool requestSent,
                                 bool virtualRetirement)
{
    const bool write = requestKind == LogicalStreamResponseKind::Write;
    return {!requestSent || write, write && requestSent && virtualRetirement};
}

/**
 * Explicit MAA destruction boundary: live packet/ledger ownership is illegal.
 */
struct ResponseTeardownShape
{
    std::size_t mapOwners = 0;
    std::size_t deferredOwners = 0;
    std::size_t cacheSendOwners = 0;
    std::size_t memorySendOwners = 0;
    std::size_t activeLogicalLedgers = 0;
    uint64_t outstandingCounters = 0;
    bool pendingPostDeleteCompletion = false;
};

inline bool
canDestroyResponseSubstrate(const ResponseTeardownShape &shape)
{
    return shape.mapOwners == 0 && shape.deferredOwners == 0 &&
           shape.cacheSendOwners == 0 && shape.memorySendOwners == 0 &&
           shape.activeLogicalLedgers == 0 &&
           shape.outstandingCounters == 0 &&
           !shape.pendingPostDeleteCompletion;
}

inline ResponsePacketAliasDecision
classifyResponsePacketAliases(const ResponsePacketAliasShape &shape,
                              bool exactOwner, bool sent)
{
    const std::size_t total = shape.outstanding + shape.deferred +
                              shape.cacheSend + shape.memorySend;
    const bool exactAlias = exactOwner && shape.outstanding == 1;
    const bool pendingAlias = shape.deferred != 0 || shape.cacheSend != 0 ||
                              shape.memorySend != 0;
    return {exactAlias && sent && !pendingAlias,
            !exactOwner && total == 0,
            total != 0};
}

inline ResponseSenderStateDecision
classifyResponseSenderState(const ResponseSenderStateShape &shape,
                            bool logicalOwner)
{
    const std::size_t expectedLogicalNodes = logicalOwner ? 1 : 0;
    const bool logicalShapeValid =
        shape.logicalNodeCount == expectedLogicalNodes &&
        (!logicalOwner || shape.logicalNodeAtTop);
    const bool valid = shape.boundedAcyclic && shape.exactSnapshot &&
                       logicalShapeValid && !shape.aliasedByOtherPacket;
    return {valid, valid && logicalOwner, valid};
}

inline LogicalStreamResponseResult
classifyLogicalStreamTag(const LogicalStreamTransactionTag &expected,
                         const LogicalStreamTransactionTag &received)
{
    if (!expected.valid() || !received.valid())
        return LogicalStreamResponseResult::Invalid;
    if (received.maaID != expected.maaID)
        return LogicalStreamResponseResult::WrongMAA;
    if (received.action != expected.action)
        return LogicalStreamResponseResult::WrongKind;
    if (received.transactionID != expected.transactionID)
        return LogicalStreamResponseResult::WrongTransaction;
    if (received.logicalID != expected.logicalID ||
        received.page != expected.page ||
        received.generation != expected.generation) {
        return LogicalStreamResponseResult::WrongPage;
    }
    if (received.slot != expected.slot)
        return LogicalStreamResponseResult::WrongSlot;
    return LogicalStreamResponseResult::Accepted;
}

/**
 * Pure finite response-routing gate used by MAA::recvTimingResp.
 *
 * This intentionally makes rejection fail closed: an unowned callback or
 * any full-tag, address, or response-kind mismatch has no authority to
 * retire an outstanding entry or pop the sender-state stack.
 */
inline LogicalStreamResponseRouteDecision
classifyLogicalStreamResponseRoute(const LogicalStreamResponseRoute &route)
{
    LogicalStreamResponseResult result = LogicalStreamResponseResult::Accepted;
    if (!route.hasOutstanding || !route.outstandingIsLogical ||
        !route.hasLogicalSenderState) {
        result = LogicalStreamResponseResult::Stale;
    } else {
        result = classifyLogicalStreamTag(route.expectedTag,
                                          route.receivedTag);
        if (result == LogicalStreamResponseResult::Accepted &&
            (route.outstandingAddress != route.senderLineAddress ||
             route.responseAddress != route.outstandingAddress)) {
            result = LogicalStreamResponseResult::WrongAddress;
        }
        if (result == LogicalStreamResponseResult::Accepted &&
            route.expectedKind != route.receivedKind) {
            result = LogicalStreamResponseResult::WrongKind;
        }
    }
    if (result != LogicalStreamResponseResult::Accepted) {
        if (route.ledgerResult == LogicalStreamResponseResult::Duplicate)
            result = route.ledgerResult;
        return {result, false, false};
    }
    if (route.ledgerResult != LogicalStreamResponseResult::Accepted)
        return {route.ledgerResult, false, false};
    return {LogicalStreamResponseResult::Accepted, true, true};
}

/**
 * Add PacketPtr and sender-state ownership to the pure route decision.
 *
 * A different packet can never retire an address-owned request, even when it
 * copied a valid tag.  It is droppable only when its top logical sender state
 * is not referenced by any map-owned packet.  An exact corrupted packet has
 * no possible later response, so it authorizes bounded cleanup followed by a
 * fatal wrapper disposition instead of retaining a pointer that the wrapper
 * is about to destroy.
 */
inline LogicalStreamResponseDispositionDecision
classifyLogicalStreamResponseDisposition(
    const LogicalStreamResponseRoute &route, bool packetMatchesOutstanding,
    bool senderStateReleaseSafe)
{
    const LogicalStreamResponseRouteDecision routeDecision =
        classifyLogicalStreamResponseRoute(route);
    LogicalStreamResponseResult result = routeDecision.result;

    if (!packetMatchesOutstanding) {
        if (result == LogicalStreamResponseResult::Accepted)
            result = LogicalStreamResponseResult::WrongPacket;
        if (route.hasLogicalSenderState && senderStateReleaseSafe) {
            return {result, TimingResponseDisposition::DroppedExtra, false,
                    true, false};
        }
        return {result, TimingResponseDisposition::FatalUnownedExtra,
                false, false, false};
    }

    if (routeDecision.accepts() &&
        routeDecision.authorizesOutstandingRetirement() &&
        routeDecision.authorizesSenderStatePop() &&
        senderStateReleaseSafe) {
        return {LogicalStreamResponseResult::Accepted,
                TimingResponseDisposition::Retired, true, true, true};
    }
    return {result, TimingResponseDisposition::FatalOwnedCorruption, true,
            route.hasLogicalSenderState && senderStateReleaseSafe, true};
}

inline bool
isTerminalLogicalStreamResponse(LogicalStreamAction action,
                                LogicalStreamResponseKind kind)
{
    return (action == LogicalStreamAction::Fill &&
            kind == LogicalStreamResponseKind::Read) ||
           (action == LogicalStreamAction::Writeback &&
            kind == LogicalStreamResponseKind::Write);
}

/**
 * Fixed per-page response ledger for one controller-owned stream action.
 *
 * The first logical integration slice has 4096 elements per page and uses
 * 64-byte cache lines.  Eight-byte elements therefore need at most 512 line
 * entries.  The array is allocated with the stream unit and no callback can
 * allocate, grow, or create a second transaction ledger.
 */
class LogicalStreamResponseLedger
{
  public:
    static constexpr std::size_t PageElements = 4096;
    static constexpr std::size_t CacheLineBytes = 64;
    static constexpr std::size_t MaxLinesPerPage =
        PageElements * sizeof(uint64_t) / CacheLineBytes;

    struct LineState
    {
        Addr address = 0;
        bool issued = false;
        bool readExResponseReceived = false;
        bool terminalIssued = false;
        bool acknowledged = false;
        bool aborted = false;
    };

    struct Counters
    {
        uint64_t stale = 0;
        uint64_t duplicate = 0;
        uint64_t wrongKind = 0;
        uint64_t wrongTransaction = 0;
        uint64_t wrongPage = 0;
        uint64_t wrongSlot = 0;
        uint64_t wrongMAA = 0;
        uint64_t wrongAddress = 0;
        uint64_t wrongPacket = 0;
        uint64_t invalid = 0;
    };

    LogicalStreamResponseResult begin(
        const LogicalStreamTransactionTag &tag, std::size_t lineCount)
    {
        if (!tag.valid() || lineCount == 0 || lineCount > MaxLinesPerPage)
            return reject(LogicalStreamResponseResult::Invalid);
        active = true;
        completed = false;
        transaction = tag;
        expectedLines = lineCount;
        issuedLines = 0;
        acknowledgedLines = 0;
        abortedLines = 0;
        for (LineState &line : lines)
            line = LineState{};
        return LogicalStreamResponseResult::Accepted;
    }

    void reset()
    {
        active = false;
        completed = false;
        transaction = {};
        expectedLines = 0;
        issuedLines = 0;
        acknowledgedLines = 0;
        abortedLines = 0;
        for (LineState &line : lines)
            line = LineState{};
    }

    bool isActive() const { return active; }
    bool isComplete() const { return active && completed; }
    const LogicalStreamTransactionTag &tag() const { return transaction; }
    std::size_t expectedLineCount() const { return expectedLines; }
    std::size_t issuedLineCount() const { return issuedLines; }
    std::size_t acknowledgedLineCount() const { return acknowledgedLines; }
    std::size_t abortedLineCount() const { return abortedLines; }
    const Counters &counters() const { return responseCounters; }
    const LineState &line(std::size_t index) const { return lines.at(index); }

    LogicalStreamResponseResult issueLine(
        const LogicalStreamTransactionTag &tag, Addr address,
        LogicalStreamResponseKind kind)
    {
        const LogicalStreamResponseResult tagResult = validateTag(tag);
        if (tagResult != LogicalStreamResponseResult::Accepted)
            return reject(tagResult);
        if (transaction.action == LogicalStreamAction::Fill) {
            if (kind != LogicalStreamResponseKind::Read)
                return reject(LogicalStreamResponseResult::WrongKind);
            if (issuedLines == expectedLines)
                return reject(LogicalStreamResponseResult::Invalid);
            if (findLine(address) != expectedLines)
                return reject(LogicalStreamResponseResult::Duplicate);
            lines[issuedLines] = {address, true, false, true, false};
            ++issuedLines;
            return LogicalStreamResponseResult::Accepted;
        }

        if (transaction.action != LogicalStreamAction::Writeback)
            return reject(LogicalStreamResponseResult::WrongKind);
        if (kind == LogicalStreamResponseKind::ReadEx) {
            if (issuedLines == expectedLines)
                return reject(LogicalStreamResponseResult::Invalid);
            if (findLine(address) != expectedLines)
                return reject(LogicalStreamResponseResult::Duplicate);
            lines[issuedLines] = {address, true, false, false, false};
            ++issuedLines;
            return LogicalStreamResponseResult::Accepted;
        }
        if (kind != LogicalStreamResponseKind::Write)
            return reject(LogicalStreamResponseResult::WrongKind);

        const std::size_t index = findLine(address);
        if (index == expectedLines)
            return reject(LogicalStreamResponseResult::WrongAddress);
        if (!lines[index].readExResponseReceived)
            return reject(LogicalStreamResponseResult::Invalid);
        if (lines[index].terminalIssued)
            return reject(LogicalStreamResponseResult::Duplicate);
        lines[index].terminalIssued = true;
        return LogicalStreamResponseResult::Accepted;
    }

    /**
     * Check a response without mutating the ledger.  Port routing calls this
     * before retiring its outstanding entry; acceptResponse performs the
     * matching one-time acknowledgement after that entry is removed.
     */
    LogicalStreamResponseResult validateResponse(
        const LogicalStreamTransactionTag &tag, Addr address,
        LogicalStreamResponseKind kind) const
    {
        const LogicalStreamResponseResult tagResult = validateTag(tag);
        if (tagResult != LogicalStreamResponseResult::Accepted)
            return tagResult;
        const std::size_t index = findLine(address);
        if (index == expectedLines)
            return LogicalStreamResponseResult::WrongAddress;
        if (lines[index].aborted)
            return LogicalStreamResponseResult::Duplicate;

        if (transaction.action == LogicalStreamAction::Fill) {
            if (kind != LogicalStreamResponseKind::Read)
                return LogicalStreamResponseResult::WrongKind;
            if (lines[index].acknowledged)
                return LogicalStreamResponseResult::Duplicate;
            return LogicalStreamResponseResult::Accepted;
        }
        if (transaction.action != LogicalStreamAction::Writeback)
            return LogicalStreamResponseResult::WrongKind;

        if (kind == LogicalStreamResponseKind::ReadEx) {
            if (lines[index].readExResponseReceived)
                return LogicalStreamResponseResult::Duplicate;
            return LogicalStreamResponseResult::Accepted;
        }
        if (kind != LogicalStreamResponseKind::Write)
            return LogicalStreamResponseResult::WrongKind;
        if (!lines[index].terminalIssued ||
            !lines[index].readExResponseReceived)
            return LogicalStreamResponseResult::Invalid;
        if (lines[index].acknowledged)
            return LogicalStreamResponseResult::Duplicate;
        return LogicalStreamResponseResult::Accepted;
    }

    /** Settle one exact issued ledger entry without accepting its response. */
    LogicalStreamResponseResult abortResponse(
        const LogicalStreamTransactionTag &tag, Addr address,
        LogicalStreamResponseKind kind)
    {
        const LogicalStreamResponseResult result =
            validateResponse(tag, address, kind);
        if (result != LogicalStreamResponseResult::Accepted)
            return reject(result);

        const std::size_t index = findLine(address);
        assert(index != expectedLines);
        assert(!lines[index].aborted);
        lines[index].aborted = true;
        ++abortedLines;
        return LogicalStreamResponseResult::Accepted;
    }

    /** Abort the unique active response owner at an exact line address. */
    LogicalStreamResponseResult abortOwnedResponse(Addr address)
    {
        if (!active)
            return reject(LogicalStreamResponseResult::Stale);
        const std::size_t index = findLine(address);
        if (index == expectedLines)
            return reject(LogicalStreamResponseResult::WrongAddress);
        LineState &lineState = lines[index];
        if (lineState.aborted || lineState.acknowledged)
            return reject(LogicalStreamResponseResult::Duplicate);

        const bool fillPending =
            transaction.action == LogicalStreamAction::Fill &&
            lineState.terminalIssued;
        const bool readExPending =
            transaction.action == LogicalStreamAction::Writeback &&
            !lineState.readExResponseReceived;
        const bool writePending =
            transaction.action == LogicalStreamAction::Writeback &&
            lineState.readExResponseReceived && lineState.terminalIssued;
        if (!fillPending && !readExPending && !writePending)
            return reject(LogicalStreamResponseResult::Invalid);
        lineState.aborted = true;
        ++abortedLines;
        return LogicalStreamResponseResult::Accepted;
    }

    LogicalStreamResponseResult acceptResponse(
        const LogicalStreamTransactionTag &tag, Addr address,
        LogicalStreamResponseKind kind)
    {
        const LogicalStreamResponseResult result =
            validateResponse(tag, address, kind);
        if (result != LogicalStreamResponseResult::Accepted)
            return reject(result);

        const std::size_t index = findLine(address);
        assert(index != expectedLines);
        if (!isTerminalLogicalStreamResponse(transaction.action, kind)) {
            assert(transaction.action == LogicalStreamAction::Writeback);
            assert(kind == LogicalStreamResponseKind::ReadEx);
            assert(!lines[index].readExResponseReceived);
            lines[index].readExResponseReceived = true;
            return LogicalStreamResponseResult::Accepted;
        }
        assert(!lines[index].acknowledged);
        lines[index].acknowledged = true;
        ++acknowledgedLines;
        if (acknowledgedLines != expectedLines)
            return LogicalStreamResponseResult::Accepted;
        completed = true;
        return LogicalStreamResponseResult::Completed;
    }

    void recordRejected(LogicalStreamResponseResult result)
    {
        assert(result != LogicalStreamResponseResult::Accepted &&
               result != LogicalStreamResponseResult::Completed);
        reject(result);
    }

  private:
    LogicalStreamResponseResult validateTag(
        const LogicalStreamTransactionTag &tag) const
    {
        if (!tag.valid())
            return LogicalStreamResponseResult::Invalid;
        if (!active)
            return LogicalStreamResponseResult::Stale;
        if (tag.maaID != transaction.maaID)
            return LogicalStreamResponseResult::WrongMAA;
        if (tag.action != transaction.action)
            return LogicalStreamResponseResult::WrongKind;
        if (tag.transactionID != transaction.transactionID)
            return LogicalStreamResponseResult::WrongTransaction;
        if (tag.logicalID != transaction.logicalID ||
            tag.page != transaction.page ||
            tag.generation != transaction.generation) {
            return LogicalStreamResponseResult::WrongPage;
        }
        if (tag.slot != transaction.slot)
            return LogicalStreamResponseResult::WrongSlot;
        return LogicalStreamResponseResult::Accepted;
    }

    std::size_t findLine(Addr address) const
    {
        for (std::size_t index = 0; index < issuedLines; ++index) {
            if (lines[index].address == address)
                return index;
        }
        return expectedLines;
    }

    LogicalStreamResponseResult reject(LogicalStreamResponseResult result)
    {
        switch (result) {
          case LogicalStreamResponseResult::Stale:
            ++responseCounters.stale;
            break;
          case LogicalStreamResponseResult::Duplicate:
            ++responseCounters.duplicate;
            break;
          case LogicalStreamResponseResult::WrongKind:
            ++responseCounters.wrongKind;
            break;
          case LogicalStreamResponseResult::WrongTransaction:
            ++responseCounters.wrongTransaction;
            break;
          case LogicalStreamResponseResult::WrongPage:
            ++responseCounters.wrongPage;
            break;
          case LogicalStreamResponseResult::WrongSlot:
            ++responseCounters.wrongSlot;
            break;
          case LogicalStreamResponseResult::WrongMAA:
            ++responseCounters.wrongMAA;
            break;
          case LogicalStreamResponseResult::WrongAddress:
            ++responseCounters.wrongAddress;
            break;
          case LogicalStreamResponseResult::WrongPacket:
            ++responseCounters.wrongPacket;
            break;
          case LogicalStreamResponseResult::Invalid:
            ++responseCounters.invalid;
            break;
          case LogicalStreamResponseResult::Accepted:
          case LogicalStreamResponseResult::Completed:
            assert(false);
            break;
        }
        return result;
    }

    std::array<LineState, MaxLinesPerPage> lines{};
    LogicalStreamTransactionTag transaction{};
    std::size_t expectedLines = 0;
    std::size_t issuedLines = 0;
    std::size_t acknowledgedLines = 0;
    std::size_t abortedLines = 0;
    bool active = false;
    bool completed = false;
    Counters responseCounters{};
};

} // namespace gem5

#endif // __MEM_MAA_LOGICAL_STREAM_RESPONSE_HH__
