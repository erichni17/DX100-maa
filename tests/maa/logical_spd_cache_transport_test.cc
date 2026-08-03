#include <sys/wait.h>
#include <unistd.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <type_traits>

#include "mem/MAA/LogicalSPDCacheTransport.hh"
#include "tests/maa/support/logical_spd_cache_mock_peer.hh"

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

    explicit Fixture(
        Transport::Operation operation = Transport::Operation::Fill,
        Transport::IdBudget budget = Transport::IdBudget{},
        uint64_t peerPacketBudget = std::numeric_limits<uint64_t>::max())
        : transport(Transport::PortCount, Transport::LineBytes, budget),
          peer(peerPacketBudget)
    {
        for (std::size_t index = 0; index < backing.bytes.size(); ++index)
            backing.bytes[index] = std::byte((index * 37 + 11) & 0xff);
        for (std::size_t index = 0; index < slot.size(); ++index)
            slot[index] = std::byte((index * 13 + 5) & 0xff);
        CHECK(peer.registerBacking(Base, backing.bytes.data(),
                                   backing.bytes.size()));
        CHECK(transport.startAction(operation, 0, 1, 0, 0, Base,
                                    17, span()) ==
              Transport::Status::Accepted);
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
    CHECK(status.status == Transport::Status::Accepted ||
          status.status == Transport::Status::Completed);
}

template <class Function>
void
expectChildSuccess(Function function)
{
    const pid_t child = fork();
    CHECK(child >= 0);
    if (child == 0) {
        function();
        std::_Exit(0);
    }
    int status = 0;
    CHECK(waitpid(child, &status, 0) == child);
    CHECK(WIFEXITED(status));
    CHECK(WEXITSTATUS(status) == 0);
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
    static_assert(!std::is_assignable<Transport::CompletionIdentity &,
                                      Transport::CompletionIdentity>::value);
    static_assert(Transport::RecordCount == 8);
    static_assert(Transport::ResponseCredits == 4);

    Transport wrongPorts(8, Transport::LineBytes);
    alignas(64) std::array<std::byte, Transport::PageBytes> page{};
    const auto before = wrongPorts.auditSnapshot();
    CHECK(wrongPorts.startAction(Transport::Operation::Fill, 0, 1, 0, 0,
                                 Fixture::Base, 17,
                                 {page.data(), page.size()}) ==
          Transport::Status::InvalidGeometry);
    CHECK(wrongPorts.auditSnapshot() == before);

    Fixture fixture;
    uint8_t finalRecord = Transport::NoRecord;
    Transport::DeliveryTicket finalTicket{};
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
            const auto finalStaged = fixture.stage(finalRecord);
            CHECK(finalStaged.status == Transport::Status::DeliveryPending);
            finalTicket = finalStaged.ticket;
            CHECK(fixture.transport.ackCount() ==
                  Transport::LinesPerPage - 1);
            const auto completed = fixture.transport.commitDelivery(
                finalTicket, fixture.span());
            CHECK(completed.status == Transport::Status::Completed);
            CHECK(completed.completion.valid());
            CHECK(completed.completion.kind() ==
                  Transport::Operation::Fill);
            CHECK(completed.completion.id() != 0);
            CHECK(completed.completion.descriptorID() == 0);
            CHECK(completed.completion.descriptorGeneration() == 1);
            CHECK(completed.completion.pageID() == 0);
            CHECK(completed.completion.slotID() == 0);
            CHECK(completed.completion.controllerSerial() == 17);
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
    expectChildSuccess([] {
        Fixture wrongPort;
        const auto prepared = wrongPort.transport.prepare(wrongPort.span());
        CHECK(prepared.status == Transport::Status::Accepted);
        CHECK(wrongPort.transport.sendPrepared(false).status ==
              Transport::Status::SendRefused);
        CHECK(wrongPort.transport.recvReqRetry(
                  uint8_t((prepared.handle->callbackPort + 1) %
                          Transport::PortCount)) ==
              Transport::Status::ProductionStop);
        CHECK(wrongPort.transport.poisoned());
        CHECK(wrongPort.transport.recvReqRetry(
                  prepared.handle->callbackPort) ==
              Transport::Status::Poisoned);
        std::_Exit(0);
    });

    Fixture fixture;
    const auto prepared = fixture.transport.prepare(fixture.span());
    CHECK(prepared.status == Transport::Status::Accepted);
    CHECK(prepared.handle == fixture.transport.pendingHandle());
    const auto refused = fixture.transport.sendPrepared(false);
    CHECK(refused.status == Transport::Status::SendRefused);
    CHECK(refused.handle == prepared.handle);
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
    CHECK(fixture.transport.poisoned());
    const auto after = fixture.transport.auditSnapshot();
    CHECK(after.ackCount == before.ackCount);
    CHECK(after.lineBuffers == before.lineBuffers);
    CHECK(fixture.peer.hasOutstanding(record));
    CHECK(fixture.transport.abortAction(Transport::AbortCode::Caller) ==
          Transport::Status::Poisoned);
    CHECK(fixture.transport.prepare(fixture.span()).status ==
          Transport::Status::Poisoned);
    std::_Exit(0);
}

void
testMalformedForeignDuplicateAndStaleResponses()
{
    for (uint8_t value = static_cast<uint8_t>(Corruption::MissingToken);
         value <= static_cast<uint8_t>(Corruption::WrongPort); ++value) {
        expectChildSuccess([value] {
            runCorruptResponse(static_cast<Corruption>(value));
        });
    }

    expectChildSuccess([] {
        Fixture duplicateFixture;
        const uint8_t duplicateRecord = duplicateFixture.sendOne();
        auto first = duplicateFixture.peer.makeResponse(duplicateRecord,
                                                        false);
        auto duplicate = first;
        CHECK(first.valid && duplicate.valid);
        const uint8_t port =
            duplicateFixture.peer.request(duplicateRecord)->callbackPort;
        const auto staged = duplicateFixture.peer.deliver(
            duplicateFixture.transport, duplicateRecord, first.handle, port);
        CHECK(staged.status == Transport::Status::DeliveryPending);
        CHECK(duplicateFixture.transport.receive(duplicate.handle, port)
                  .status == Transport::Status::ProductionStop);
        CHECK(duplicateFixture.transport.poisoned());
        std::_Exit(0);
    });

    expectChildSuccess([] {
        Fixture staleFixture;
        const uint8_t oldRecord = staleFixture.sendOne();
        auto old = staleFixture.peer.makeResponse(oldRecord, true);
        auto release = old;
        CHECK(old.valid && release.valid);
        CHECK(staleFixture.transport.abortAction(
                  Transport::AbortCode::Caller) ==
              Transport::Status::Accepted);
        CHECK(staleFixture.peer.deliver(
                  staleFixture.transport, oldRecord, release.handle,
                  staleFixture.peer.request(oldRecord)->callbackPort)
                  .status == Transport::Status::AbortDrained);
        CHECK(staleFixture.transport.startAction(
                  Transport::Operation::Fill, 0, 1, 0, 0, Fixture::Base,
                  17, staleFixture.span()) == Transport::Status::Accepted);
        const uint8_t reused = staleFixture.sendOne();
        CHECK(reused == oldRecord);
        CHECK(staleFixture.transport.receive(
                  old.handle,
                  staleFixture.peer.request(reused)->callbackPort)
                  .status == Transport::Status::ProductionStop);
        CHECK(staleFixture.transport.poisoned());
        std::_Exit(0);
    });
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
        CHECK(fixture.transport.commitDelivery(ticket, fixture.span())
                  .status == Transport::Status::Accepted);
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
        Transport::Operation::Fill, 0, 1, 0, 0, Fixture::Base, 17,
        context.span);
    return context.succeed;
}

bool
passiveCopyFailure(void *)
{
    return false;
}

void
testCopyGuardFailureAndOrdinaryPreCopyAbort()
{
    {
        Fixture passive;
        const auto staged = passive.stage(passive.sendOne());
        const auto before = passive.slot;
        CHECK(passive.transport.commitDelivery(
                  staged.ticket, passive.span(), passiveCopyFailure, nullptr)
                  .status == Transport::Status::CopyFailed);
        CHECK(!passive.transport.poisoned());
        CHECK(!passive.transport.copyActive());
        CHECK(passive.slot == before);
        CHECK(passive.transport.abortAction(
                  Transport::AbortCode::Caller) ==
              Transport::Status::AbortDrained);
    }
    for (const bool hookResult : {false, true}) {
        expectChildSuccess([hookResult] {
            Fixture fixture;
            const uint8_t record = fixture.sendOne();
            const auto staged = fixture.stage(record);
            CHECK(staged.status == Transport::Status::DeliveryPending);
            const auto slotBefore = fixture.slot;
            const auto beforeCopy = fixture.transport.auditSnapshot();
            CopyHookContext hook{&fixture.transport, fixture.span(),
                                 Transport::Status::Invalid,
                                 Transport::Status::Invalid,
                                 Transport::Status::Invalid, hookResult};
            CHECK(fixture.transport.commitDelivery(
                      staged.ticket, fixture.span(), copyHook, &hook)
                      .status == Transport::Status::Poisoned);
            CHECK(hook.abortStatus == Transport::Status::ProductionStop);
            CHECK(hook.resetStatus == Transport::Status::Poisoned);
            CHECK(hook.startStatus == Transport::Status::Poisoned);
            CHECK(fixture.transport.copyActive());
            CHECK(fixture.transport.poisoned());
            CHECK(fixture.slot == slotBefore);
            CHECK(fixture.transport.recordState(record) ==
                  Transport::RecordState::Delivering);
            CHECK(fixture.transport.ackCount() == beforeCopy.ackCount);
            CHECK(fixture.transport.abortAction(
                      Transport::AbortCode::Caller) ==
                  Transport::Status::Poisoned);
            std::_Exit(0);
        });
    }

    Fixture ordinaryAbort;
    const auto staged = ordinaryAbort.stage(ordinaryAbort.sendOne());
    CHECK(staged.status == Transport::Status::DeliveryPending);
    const auto slotBefore = ordinaryAbort.slot;
    CHECK(ordinaryAbort.transport.abortAction(
              Transport::AbortCode::Caller) ==
          Transport::Status::AbortDrained);
    CHECK(!ordinaryAbort.transport.poisoned());
    CHECK(ordinaryAbort.transport.drained());
    CHECK(ordinaryAbort.slot == slotBefore);
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
    abortRemainder(fixture);

    alignas(64) std::array<std::byte, Transport::PageBytes> slot{};
    Transport incarnationExhausted(
        Transport::PortCount, Transport::LineBytes,
        {1, static_cast<uint32_t>(2 * Transport::LinesPerPage - 1),
         static_cast<uint32_t>(Transport::LinesPerPage)});
    const auto beforeIdentity = incarnationExhausted.auditSnapshot();
    CHECK(incarnationExhausted.startAction(
              Transport::Operation::Fill, 0, 1, 0, 0, Fixture::Base,
              17, {slot.data(), slot.size()}) ==
          Transport::Status::Exhausted);
    CHECK(incarnationExhausted.auditSnapshot() == beforeIdentity);

    Transport epochExhausted(
        Transport::PortCount, Transport::LineBytes,
        {1, static_cast<uint32_t>(2 * Transport::LinesPerPage),
         static_cast<uint32_t>(Transport::LinesPerPage - 1)});
    const auto beforeEpoch = epochExhausted.auditSnapshot();
    CHECK(epochExhausted.startAction(
              Transport::Operation::Fill, 0, 1, 0, 0, Fixture::Base,
              17, {slot.data(), slot.size()}) ==
          Transport::Status::Exhausted);
    CHECK(epochExhausted.auditSnapshot() == beforeEpoch);

    Transport actionExhausted(
        Transport::PortCount, Transport::LineBytes,
        {1, static_cast<uint32_t>(2 * Transport::LinesPerPage),
         static_cast<uint32_t>(Transport::LinesPerPage)});
    CHECK(actionExhausted.startAction(
              Transport::Operation::Fill, 0, 1, 0, 0, Fixture::Base,
              17, {slot.data(), slot.size()}) ==
          Transport::Status::Accepted);
    CHECK(actionExhausted.actionID() == 1);
    CHECK(actionExhausted.abortAction(Transport::AbortCode::Caller) ==
          Transport::Status::AbortDrained);
    const auto beforeAction = actionExhausted.auditSnapshot();
    CHECK(actionExhausted.startAction(
              Transport::Operation::Fill, 0, 1, 0, 0, Fixture::Base,
              17, {slot.data(), slot.size()}) ==
          Transport::Status::Exhausted);
    CHECK(actionExhausted.auditSnapshot() == beforeAction);

    Fixture peerIdentity(Transport::Operation::Fill,
                         Transport::IdBudget{}, 1);
    uint8_t record = peerIdentity.sendOne();
    auto finalPeerID = peerIdentity.peer.makeResponse(record, true);
    CHECK(finalPeerID.valid);
    CHECK(finalPeerID.handle.incarnation == 1);
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
    const auto secondStaged = peerIdentity.peer.deliver(
        peerIdentity.transport, record, samePacket.handle,
        peerIdentity.peer.request(record)->callbackPort);
    finishStagedFill(peerIdentity, secondStaged);
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
        CHECK(!delivering.transport.poisoned());
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

void
testEnumSpanAndMockWriteAcceptanceOrder()
{
    alignas(Transport::LineBytes)
        std::array<std::byte, Transport::PageBytes + Transport::LineBytes>
            raw{};
    Transport transport;
    const auto before = transport.auditSnapshot();
    CHECK(transport.startAction(
              static_cast<Transport::Operation>(0xff), 0, 1, 0, 0,
              Fixture::Base, 17, {raw.data(), Transport::PageBytes}) ==
          Transport::Status::Invalid);
    CHECK(transport.auditSnapshot() == before);
    CHECK(transport.startAction(
              Transport::Operation::Fill, 0, 1, 0, 0, Fixture::Base, 17,
              {nullptr, Transport::PageBytes}) == Transport::Status::Invalid);
    CHECK(transport.startAction(
              Transport::Operation::Fill, 0, 1, 0, 0, Fixture::Base, 17,
              {raw.data(), Transport::PageBytes - 1}) ==
          Transport::Status::Invalid);
    CHECK(transport.startAction(
              Transport::Operation::Fill, 0, 1, 0, 0, Fixture::Base, 17,
              {raw.data(), Transport::PageBytes + 1}) ==
          Transport::Status::Invalid);
    CHECK(transport.startAction(
              Transport::Operation::Fill, 0, 1, 0, 0, Fixture::Base, 17,
              {raw.data() + 1, Transport::PageBytes}) ==
          Transport::Status::Invalid);
    CHECK(transport.abortAction(static_cast<Transport::AbortCode>(0xff)) ==
          Transport::Status::Invalid);
    CHECK(transport.prepare(Transport::PageSpan{
                                raw.data(), Transport::PageBytes},
                            static_cast<Transport::FaultPoint>(0xff))
              .status == Transport::Status::Invalid);
    CHECK(transport.auditSnapshot() == before);

    CHECK(transport.startAction(
              Transport::Operation::Fill, 0, 1, 0, 0, Fixture::Base, 17,
              {raw.data(), Transport::PageBytes}) ==
          Transport::Status::Accepted);
    const auto active = transport.auditSnapshot();
    CHECK(transport.startAction(
              static_cast<Transport::Operation>(0xff), 0, 1, 0, 0,
              Fixture::Base, 17, {raw.data(), Transport::PageBytes}) ==
          Transport::Status::Invalid);
    CHECK(transport.abortAction(static_cast<Transport::AbortCode>(0xff)) ==
          Transport::Status::Invalid);
    CHECK(transport.auditSnapshot() == active);
    CHECK(transport.abortAction(Transport::AbortCode::Caller) ==
          Transport::Status::AbortDrained);

    expectChildSuccess([] {
        Fixture write(Transport::Operation::Writeback);
        const auto slotBefore = write.slot;
        const uint8_t record = write.sendOne();
        auto response = write.peer.makeResponse(record, true);
        CHECK(response.valid);
        const auto *request = write.peer.request(record);
        CHECK(request != nullptr);
        CHECK(std::memcmp(write.backing.bytes.data(), slotBefore.data(),
                          Transport::LineBytes) != 0);
        const uint8_t wrongPort = static_cast<uint8_t>(
            (request->callbackPort + 1) % Transport::PortCount);
        CHECK(write.peer.deliver(write.transport, record, response.handle,
                                 wrongPort)
                  .status == Transport::Status::ProductionStop);
        CHECK(std::memcmp(write.backing.bytes.data(), slotBefore.data(),
                          Transport::LineBytes) == 0);
        CHECK(write.transport.poisoned());
        std::_Exit(0);
    });
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
    testEnumSpanAndMockWriteAcceptanceOrder();
    std::cout << "logical_spd_cache_transport_test: PASS"
              << " host_transport_size=" << sizeof(Transport)
              << " (host sizeof; not synthesized hardware)" << std::endl;
    return 0;
}
