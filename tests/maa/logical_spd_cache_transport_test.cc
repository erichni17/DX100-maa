#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>

#include "mem/MAA/LogicalSPDCacheTransport.hh"
#include "tests/maa/support/logical_spd_cache_mock_peer.hh"

namespace gem5 {

class LogicalSPDCacheTransportTestAccess
{
  public:
    static void setNextActionID(LogicalSPDCacheTransport &transport,
                                uint32_t value, bool exhausted = false)
    {
        transport.nextActionID = value;
        transport.actionIDsExhausted = exhausted;
    }

    static void setNextIncarnationID(LogicalSPDCacheTransport &transport,
                                     uint32_t value,
                                     bool exhausted = false)
    {
        transport.nextIncarnationID = value;
        transport.incarnationIDsExhausted = exhausted;
    }

    static void setAllRecordEpochs(LogicalSPDCacheTransport &transport,
                                   uint16_t value)
    {
        for (std::size_t index = 0; index < transport.records.size();
             ++index) {
            transport.records[index].epoch = value;
            transport.records[index].token.epoch = value;
        }
    }
};

} // namespace gem5

namespace {

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;         \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

using Transport = gem5::LogicalSPDCacheTransport;
using Peer = gem5::LogicalSPDCacheMockPeer;

struct alignas(Transport::PagesPerDescriptor * Transport::PageBytes) Backing
{
    std::array<std::byte,
               Transport::PagesPerDescriptor * Transport::PageBytes>
        bytes{};
};

struct Fixture
{
    static constexpr uint64_t Base = 0x100000;

    Transport transport;
    Peer peer;
    Backing backing{};
    alignas(64) std::array<std::byte, Transport::PageBytes> slot{};

    explicit Fixture(Transport::Operation operation =
                         Transport::Operation::Fill)
    {
        for (std::size_t index = 0; index < backing.bytes.size(); ++index)
            backing.bytes[index] = std::byte((index * 37 + 11) & 0xff);
        for (std::size_t index = 0; index < slot.size(); ++index)
            slot[index] = std::byte((index * 13 + 5) & 0xff);
        CHECK(peer.registerBacking(Base, backing.bytes.data(),
                                   backing.bytes.size()));
        CHECK(transport.startAction(operation, 0, 1, 0, 0, Base,
                                    span()) == Transport::Status::Accepted);
    }

    Transport::PageSpan span()
    {
        return {slot.data(), slot.size()};
    }

    uint8_t sendOne(bool accepted = true)
    {
        const auto result = peer.send(transport, span(), accepted);
        CHECK(result.status == (accepted ? Transport::Status::SendAccepted
                                         : Transport::Status::SendRefused));
        CHECK(result.record < Transport::RecordCount);
        return result.record;
    }

    Transport::Result stage(uint8_t record, bool replacement = true)
    {
        auto response = peer.makeResponse(record, replacement);
        CHECK(response.valid);
        const auto *request = peer.request(record);
        CHECK(request != nullptr);
        return peer.deliver(transport, record, response.handle,
                            request->callbackPort);
    }
};

void
finishStagedFill(Fixture &fixture, const Transport::Result &staged)
{
    CHECK(staged.status == Transport::Status::DeliveryPending);
    const auto status =
        fixture.transport.commitDelivery(staged.ticket, fixture.span());
    CHECK(status == Transport::Status::Accepted ||
          status == Transport::Status::Completed);
}

void
abortRemainder(Fixture &fixture)
{
    const auto status =
        fixture.transport.abortAction(Transport::AbortCode::Caller);
    CHECK(status == Transport::Status::AbortDrained ||
          status == Transport::Status::AlreadyDrained);
    CHECK(fixture.transport.drained());
}

void
testFixedGeometryLedgerAndExact512Sets()
{
    static_assert(Transport::PackedLogicalStateBits == 529441);
    static_assert(Transport::PackedLogicalStateBytes == 66181);
    static_assert(Transport::AlignedHardwareProjectionBytes == 66324);
    static_assert(Transport::RecordCount == 8);
    static_assert(Transport::ResponseCredits == 4);

    Transport wrongPorts(8, Transport::LineBytes);
    alignas(64) std::array<std::byte, Transport::PageBytes> page{};
    const auto before = wrongPorts.auditSnapshot();
    CHECK(wrongPorts.startAction(Transport::Operation::Fill, 0, 1, 0, 0,
                                 Fixture::Base, {page.data(), page.size()}) ==
          Transport::Status::InvalidGeometry);
    CHECK(wrongPorts.auditSnapshot() == before);

    Fixture fixture;
    uint8_t finalRecord = Transport::NoRecord;
    Transport::Result finalStaged;
    while (fixture.transport.actionState() != Transport::ActionState::Free) {
        while (fixture.transport.creditsInUse() <
               Transport::ResponseCredits) {
            const auto sent = fixture.peer.send(fixture.transport,
                                                fixture.span(), true);
            if (sent.status != Transport::Status::SendAccepted)
                break;
        }
        bool progressed = false;
        for (std::size_t offset = 0; offset < Transport::RecordCount;
             ++offset) {
            const uint8_t record = static_cast<uint8_t>(
                Transport::RecordCount - 1 - offset);
            if (!fixture.peer.hasOutstanding(record))
                continue;
            const auto key = fixture.transport.recordKey(record);
            if (key.line == Transport::LinesPerPage - 1) {
                finalRecord = record;
                continue;
            }
            finishStagedFill(fixture, fixture.stage(record));
            progressed = true;
        }
        if (finalRecord != Transport::NoRecord &&
            fixture.transport.ackCount() == Transport::LinesPerPage - 1) {
            CHECK(fixture.transport.issuedSetComplete());
            CHECK(!fixture.transport.ackSetComplete());
            CHECK(!fixture.transport.lineAcked(Transport::LinesPerPage - 1));
            finalStaged = fixture.stage(finalRecord);
            CHECK(finalStaged.status == Transport::Status::DeliveryPending);
            CHECK(fixture.transport.ackCount() ==
                  Transport::LinesPerPage - 1);
            CHECK(fixture.transport.commitDelivery(finalStaged.ticket,
                                                    fixture.span()) ==
                  Transport::Status::Completed);
            progressed = true;
        }
        CHECK(progressed ||
              fixture.transport.actionState() == Transport::ActionState::Free);
    }
    CHECK(fixture.transport.drained());
    CHECK(std::memcmp(fixture.slot.data(), fixture.backing.bytes.data(),
                      Transport::PageBytes) == 0);
}

void
testSendRefusalExactRetryAndReplacementResponse()
{
    Fixture fixture;
    const auto prepared = fixture.transport.prepare(fixture.span());
    CHECK(prepared.status == Transport::Status::Accepted);
    CHECK(prepared.handle == fixture.transport.pendingHandle());
    const auto refused = fixture.transport.sendPrepared(false);
    CHECK(refused.status == Transport::Status::SendRefused);
    CHECK(refused.handle == prepared.handle);
    const auto waitSnapshot = fixture.transport.auditSnapshot();
    CHECK(fixture.transport.recvReqRetry(
              uint8_t((prepared.handle->callbackPort + 1) %
                      Transport::PortCount)) ==
          Transport::Status::WrongRetryPort);
    CHECK(fixture.transport.auditSnapshot() == waitSnapshot);
    CHECK(fixture.transport.recvReqRetry(prepared.handle->callbackPort) ==
          Transport::Status::Accepted);
    const auto accepted = fixture.peer.send(fixture.transport, fixture.span(),
                                            true);
    CHECK(accepted.status == Transport::Status::SendAccepted);
    CHECK(accepted.handle == prepared.handle);

    auto replacement = fixture.peer.makeResponse(accepted.record, true);
    CHECK(replacement.valid);
    replacement.handle.command = Transport::Command::ReadRespWithInvalidate;
    const auto delivered = fixture.peer.deliver(
        fixture.transport, accepted.record, replacement.handle,
        accepted.handle->callbackPort);
    CHECK(delivered.status == Transport::Status::DeliveryPending);
    CHECK(replacement.handle.disposed);
    finishStagedFill(fixture, delivered);
    abortRemainder(fixture);
}

enum class Corruption : uint8_t
{
    MissingToken,
    ResidualToken,
    CopiedToken,
    CorruptToken,
    WrongRequest,
    WrongRequestIncarnation,
    WrongCommand,
    WrongSize,
    WrongAddress,
    WrongPayload,
    WrongPort,
};

void
runCorruptResponse(Corruption corruption)
{
    Fixture fixture;
    const uint8_t record = fixture.sendOne();
    auto built = fixture.peer.makeResponse(record, true);
    CHECK(built.valid);
    auto &returned = built.handle;
    Transport::RouteToken copied{};
    Transport::RequestIdentity wrongRequest{};
    uint8_t callbackPort = fixture.peer.request(record)->callbackPort;
    switch (corruption) {
      case Corruption::MissingToken:
        returned.tokenDepth = 0;
        returned.token = nullptr;
        break;
      case Corruption::ResidualToken:
        returned.tokenDepth = 2;
        break;
      case Corruption::CopiedToken:
        copied = *returned.token;
        returned.token = &copied;
        break;
      case Corruption::CorruptToken:
        ++returned.tokenEpoch;
        break;
      case Corruption::WrongRequest:
        wrongRequest.incarnation = returned.request->incarnation;
        returned.request = &wrongRequest;
        break;
      case Corruption::WrongRequestIncarnation:
        ++returned.requestIncarnation;
        break;
      case Corruption::WrongCommand:
        returned.command = Transport::Command::WriteResp;
        break;
      case Corruption::WrongSize:
        --returned.size;
        break;
      case Corruption::WrongAddress:
        returned.address += Transport::LineBytes;
        break;
      case Corruption::WrongPayload:
        returned.data = nullptr;
        returned.dataSize = 0;
        break;
      case Corruption::WrongPort:
        callbackPort = uint8_t((callbackPort + 1) % Transport::PortCount);
        break;
    }
    const auto before = fixture.transport.auditSnapshot();
    CHECK(fixture.peer.deliver(fixture.transport, record, returned,
                               callbackPort)
              .status == Transport::Status::ProductionStop);
    CHECK(!returned.disposed);
    CHECK(fixture.transport.auditSnapshot() == before);
    CHECK(fixture.peer.hasOutstanding(record));

    finishStagedFill(fixture, fixture.stage(record));
    abortRemainder(fixture);
}

void
testMalformedForeignDuplicateAndStaleResponses()
{
    for (uint8_t value = static_cast<uint8_t>(Corruption::MissingToken);
         value <= static_cast<uint8_t>(Corruption::WrongPort); ++value) {
        runCorruptResponse(static_cast<Corruption>(value));
    }

    Fixture duplicateFixture;
    const uint8_t duplicateRecord = duplicateFixture.sendOne();
    auto first = duplicateFixture.peer.makeResponse(duplicateRecord, false);
    auto duplicate = first;
    CHECK(first.valid && duplicate.valid);
    const uint8_t port =
        duplicateFixture.peer.request(duplicateRecord)->callbackPort;
    const auto staged = duplicateFixture.peer.deliver(
        duplicateFixture.transport, duplicateRecord, first.handle, port);
    CHECK(staged.status == Transport::Status::DeliveryPending);
    const auto beforeDuplicate = duplicateFixture.transport.auditSnapshot();
    CHECK(duplicateFixture.transport.receive(duplicate.handle, port).status ==
          Transport::Status::ProductionStop);
    CHECK(duplicateFixture.transport.auditSnapshot() == beforeDuplicate);
    finishStagedFill(duplicateFixture, staged);
    abortRemainder(duplicateFixture);

    Fixture staleFixture;
    const uint8_t oldRecord = staleFixture.sendOne();
    auto old = staleFixture.peer.makeResponse(oldRecord, true);
    auto release = old;
    CHECK(old.valid && release.valid);
    CHECK(staleFixture.transport.abortAction(Transport::AbortCode::Caller) ==
          Transport::Status::Accepted);
    CHECK(staleFixture.peer.deliver(
              staleFixture.transport, oldRecord, release.handle,
              staleFixture.peer.request(oldRecord)->callbackPort)
              .status == Transport::Status::AbortDrained);
    CHECK(staleFixture.transport.startAction(
              Transport::Operation::Fill, 0, 1, 0, 0, Fixture::Base,
              staleFixture.span()) == Transport::Status::Accepted);
    const uint8_t reused = staleFixture.sendOne();
    CHECK(reused == oldRecord);
    const auto beforeStale = staleFixture.transport.auditSnapshot();
    CHECK(staleFixture.transport.receive(
              old.handle,
              staleFixture.peer.request(reused)->callbackPort)
              .status == Transport::Status::ProductionStop);
    CHECK(staleFixture.transport.auditSnapshot() == beforeStale);
    finishStagedFill(staleFixture, staleFixture.stage(reused));
    abortRemainder(staleFixture);
}

void
testFourDelayedFillsRetainCreditsAndBlockFifth()
{
    Fixture fixture;
    std::array<Transport::DeliveryTicket, Transport::ResponseCredits>
        tickets{};
    for (std::size_t index = 0; index < Transport::ResponseCredits; ++index) {
        const uint8_t record = fixture.sendOne();
        const auto staged = fixture.stage(record);
        CHECK(staged.status == Transport::Status::DeliveryPending);
        tickets[index] = staged.ticket;
    }
    CHECK(fixture.transport.creditsInUse() == Transport::ResponseCredits);
    const auto before = fixture.transport.auditSnapshot();
    CHECK(fixture.peer.send(fixture.transport, fixture.span(), true).status ==
          Transport::Status::NoCreditAvailable);
    CHECK(fixture.transport.auditSnapshot() == before);
    for (const auto &ticket : tickets) {
        CHECK(fixture.transport.commitDelivery(ticket, fixture.span()) ==
              Transport::Status::Accepted);
    }
    CHECK(fixture.transport.creditsInUse() == 0);
    abortRemainder(fixture);
}

struct CopyHookContext
{
    Transport *transport = nullptr;
    Transport::PageSpan span{};
    Transport::Status abortStatus = Transport::Status::Invalid;
    Transport::Status resetStatus = Transport::Status::Invalid;
    Transport::Status startStatus = Transport::Status::Invalid;
    bool succeed = true;
};

bool
copyHook(void *opaque)
{
    auto &context = *static_cast<CopyHookContext *>(opaque);
    context.abortStatus =
        context.transport->abortAction(Transport::AbortCode::Caller);
    context.resetStatus = context.transport->reset();
    context.startStatus = context.transport->startAction(
        Transport::Operation::Fill, 0, 1, 0, 0, Fixture::Base, context.span);
    return context.succeed;
}

void
testCopyGuardFailureAndOrdinaryPreCopyAbort()
{
    Fixture fixture;
    const uint8_t record = fixture.sendOne();
    const auto staged = fixture.stage(record);
    CHECK(staged.status == Transport::Status::DeliveryPending);
    const auto beforeCopy = fixture.transport.auditSnapshot();
    CopyHookContext failed{&fixture.transport, fixture.span(),
                           Transport::Status::Invalid,
                           Transport::Status::Invalid,
                           Transport::Status::Invalid, false};
    CHECK(fixture.transport.commitDelivery(staged.ticket, fixture.span(),
                                           copyHook, &failed) ==
          Transport::Status::CopyFailed);
    CHECK(failed.abortStatus == Transport::Status::CopyActive);
    CHECK(failed.resetStatus == Transport::Status::CopyActive);
    CHECK(failed.startStatus == Transport::Status::CopyActive);
    CHECK(!fixture.transport.copyActive());
    CHECK(fixture.transport.recordState(record) ==
          Transport::RecordState::Delivering);
    CHECK(fixture.transport.ackCount() == beforeCopy.ackCount);
    CHECK(fixture.transport.abortAction(Transport::AbortCode::Caller) ==
          Transport::Status::AbortDrained);
    CHECK(fixture.transport.drained());

    Fixture successful;
    const auto successStage = successful.stage(successful.sendOne());
    CopyHookContext hook{&successful.transport, successful.span()};
    CHECK(successful.transport.commitDelivery(successStage.ticket,
                                              successful.span(), copyHook,
                                              &hook) ==
          Transport::Status::Accepted);
    CHECK(hook.abortStatus == Transport::Status::CopyActive);
    CHECK(hook.resetStatus == Transport::Status::CopyActive);
    CHECK(hook.startStatus == Transport::Status::CopyActive);
    abortRemainder(successful);
}

void
testMaterializationFaultsAndIdentityExhaustionAreAtomic()
{
    Fixture fixture;
    for (Transport::FaultPoint fault :
         {Transport::FaultPoint::RequestIdentity,
          Transport::FaultPoint::LineSnapshot,
          Transport::FaultPoint::RequestPacket}) {
        const auto before = fixture.transport.auditSnapshot();
        CHECK(fixture.transport.prepare(fixture.span(), fault).status ==
              Transport::Status::FaultInjected);
        CHECK(fixture.transport.auditSnapshot() == before);
    }
    gem5::LogicalSPDCacheTransportTestAccess::setNextIncarnationID(
        fixture.transport, std::numeric_limits<uint32_t>::max());
    const auto beforeIdentity = fixture.transport.auditSnapshot();
    CHECK(fixture.transport.prepare(fixture.span()).status ==
          Transport::Status::Exhausted);
    CHECK(fixture.transport.auditSnapshot() == beforeIdentity);
    abortRemainder(fixture);

    Transport epochExhausted;
    alignas(64) std::array<std::byte, Transport::PageBytes> slot{};
    gem5::LogicalSPDCacheTransportTestAccess::setAllRecordEpochs(
        epochExhausted, std::numeric_limits<uint16_t>::max() - 63);
    const auto beforeEpoch = epochExhausted.auditSnapshot();
    CHECK(epochExhausted.startAction(
              Transport::Operation::Fill, 0, 1, 0, 0, Fixture::Base,
              {slot.data(), slot.size()}) == Transport::Status::Exhausted);
    CHECK(epochExhausted.auditSnapshot() == beforeEpoch);

    Transport actionExhausted;
    gem5::LogicalSPDCacheTransportTestAccess::setNextActionID(
        actionExhausted, std::numeric_limits<uint32_t>::max());
    CHECK(actionExhausted.startAction(
              Transport::Operation::Fill, 0, 1, 0, 0, Fixture::Base,
              {slot.data(), slot.size()}) == Transport::Status::Accepted);
    CHECK(actionExhausted.actionID() ==
          std::numeric_limits<uint32_t>::max());
    CHECK(actionExhausted.abortAction(Transport::AbortCode::Caller) ==
          Transport::Status::AbortDrained);
    const auto beforeAction = actionExhausted.auditSnapshot();
    CHECK(actionExhausted.startAction(
              Transport::Operation::Fill, 0, 1, 0, 0, Fixture::Base,
              {slot.data(), slot.size()}) == Transport::Status::Exhausted);
    CHECK(actionExhausted.auditSnapshot() == beforeAction);

    Fixture peerIdentity;
    uint8_t record = peerIdentity.sendOne();
    peerIdentity.peer.setNextPeerPacketIDForTest(
        std::numeric_limits<uint64_t>::max());
    auto finalPeerID = peerIdentity.peer.makeResponse(record, true);
    CHECK(finalPeerID.valid);
    CHECK(finalPeerID.handle.incarnation ==
          std::numeric_limits<uint64_t>::max());
    auto staged = peerIdentity.peer.deliver(
        peerIdentity.transport, record, finalPeerID.handle,
        peerIdentity.peer.request(record)->callbackPort);
    finishStagedFill(peerIdentity, staged);
    record = peerIdentity.sendOne();
    const auto beforePeerExhaustion = peerIdentity.transport.auditSnapshot();
    CHECK(!peerIdentity.peer.makeResponse(record, true).valid);
    CHECK(peerIdentity.transport.auditSnapshot() == beforePeerExhaustion);
    auto samePacket = peerIdentity.peer.makeResponse(record, false);
    CHECK(samePacket.valid);
    staged = peerIdentity.peer.deliver(
        peerIdentity.transport, record, samePacket.handle,
        peerIdentity.peer.request(record)->callbackPort);
    finishStagedFill(peerIdentity, staged);
    abortRemainder(peerIdentity);
}

void
testAbortEveryOwnerStateResponderSilenceResetAndTeardown()
{
    {
        Fixture queued;
        CHECK(queued.transport.abortAction(Transport::AbortCode::Caller) ==
              Transport::Status::AbortDrained);
    }
    {
        Fixture pending;
        CHECK(pending.transport.prepare(pending.span()).status ==
              Transport::Status::Accepted);
        CHECK(pending.transport.recordState(
                  pending.transport.pendingRecord()) ==
              Transport::RecordState::PendingSend);
        CHECK(pending.transport.abortAction(Transport::AbortCode::Caller) ==
              Transport::Status::AbortDrained);
    }
    {
        Fixture retry;
        retry.sendOne(false);
        CHECK(retry.transport.abortAction(Transport::AbortCode::Caller) ==
              Transport::Status::AbortDrained);
    }
    {
        Fixture inflight;
        const uint8_t record = inflight.sendOne();
        CHECK(inflight.transport.abortAction(Transport::AbortCode::Caller) ==
              Transport::Status::Accepted);
        CHECK(!inflight.transport.drained());
        CHECK(inflight.transport.reset() == Transport::Status::Busy);
        CHECK(inflight.transport.seal() == Transport::Status::Busy);
        CHECK(inflight.peer.respond(inflight.transport, record).status ==
              Transport::Status::AbortDrained);
        CHECK(inflight.transport.drained());
        CHECK(inflight.transport.reset() == Transport::Status::Accepted);
        CHECK(inflight.transport.seal() == Transport::Status::Accepted);
        CHECK(inflight.transport.abortAction(Transport::AbortCode::Caller) ==
              Transport::Status::Sealed);
    }
    {
        Fixture delivering;
        const auto staged = delivering.stage(delivering.sendOne());
        CHECK(staged.status == Transport::Status::DeliveryPending);
        const auto slotBefore = delivering.slot;
        CHECK(delivering.transport.abortAction(
                  Transport::AbortCode::Caller) ==
              Transport::Status::AbortDrained);
        CHECK(delivering.slot == slotBefore);
        CHECK(delivering.transport.commitDelivery(staged.ticket,
                                                  delivering.span()) ==
              Transport::Status::ProductionStop);
    }
    {
        Fixture writeback(Transport::Operation::Writeback);
        const uint8_t record = writeback.sendOne();
        CHECK(writeback.transport.abortAction(Transport::AbortCode::Caller) ==
              Transport::Status::Accepted);
        CHECK(writeback.peer.respond(writeback.transport, record).status ==
              Transport::Status::AbortDrained);
        CHECK(writeback.transport.drained());
    }
}

} // namespace

int
main()
{
    testFixedGeometryLedgerAndExact512Sets();
    testSendRefusalExactRetryAndReplacementResponse();
    testMalformedForeignDuplicateAndStaleResponses();
    testFourDelayedFillsRetainCreditsAndBlockFifth();
    testCopyGuardFailureAndOrdinaryPreCopyAbort();
    testMaterializationFaultsAndIdentityExhaustionAreAtomic();
    testAbortEveryOwnerStateResponderSilenceResetAndTeardown();
    std::cout << "logical_spd_cache_transport_test: PASS"
              << " host_transport_size=" << sizeof(Transport)
              << " (host sizeof; not synthesized hardware)" << std::endl;
    return 0;
}
