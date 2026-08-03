#include <cstdlib>
#include <cstring>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <vector>

#include "mem/MAA/LogicalSPDCacheGem5Bridge.hh"
#include "tests/maa/support/logical_spd_cache_mock_peer.hh"

namespace {

using Bridge = gem5::LogicalSPDCacheGem5Bridge;
using Status = Bridge::LifecycleStatus;
using Runtime = Bridge::Runtime;
using Slice = Runtime::Slice;
using Transport = Runtime::Transport;
using Peer = gem5::LogicalSPDCacheMockPeer;

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": CHECK failed: " #condition << '\n';             \
            std::abort();                                                    \
        }                                                                    \
    } while (false)

uint64_t
bits(double value)
{
    uint64_t encoded = 0;
    static_assert(sizeof(encoded) == sizeof(value));
    std::memcpy(&encoded, &value, sizeof(encoded));
    return encoded;
}

Transport::Result
completeResponse(Runtime &runtime, Peer &peer, uint8_t record)
{
    const Transport::TransactionKey key = runtime.recordKey(record);
    Peer::ResponseBuild response = peer.makeResponse(record);
    CHECK(response.valid);
    const Transport::RequestPacket *request = peer.request(record);
    CHECK(request != nullptr);
    Transport::Result delivered = peer.deliver(
        runtime, record, response.handle, request->callbackPort);
    if (key.operation == Transport::Operation::Fill) {
        CHECK(delivered.status == Transport::Status::DeliveryPending);
        Transport::Result committed =
            runtime.commitDelivery(delivered.ticket);
        CHECK(committed.status == Transport::Status::Accepted ||
              committed.status == Transport::Status::Completed);
        return committed;
    }
    CHECK(delivered.status == Transport::Status::Accepted ||
          delivered.status == Transport::Status::Completed);
    return delivered;
}

void
runAction(Runtime &runtime, Peer &peer)
{
    CHECK(runtime.prepare().status == Transport::Status::Accepted);
    while (runtime.transportActionState() !=
           Transport::ActionState::Free) {
        while (runtime.creditsInUse() < Transport::ResponseCredits) {
            const Transport::Result sent = peer.send(runtime, true);
            if (sent.status != Transport::Status::SendAccepted)
                break;
        }
        bool progressed = false;
        for (uint8_t record = 0; record < Transport::RecordCount; ++record) {
            if (!peer.hasOutstanding(record))
                continue;
            (void)completeResponse(runtime, peer, record);
            progressed = true;
        }
        CHECK(progressed || runtime.transportActionState() ==
                                Transport::ActionState::Free);
    }
}

void
checkConstructionAndAdmissionClosure()
{
    Bridge bridge(4);
    CHECK(bridge.runtimeCount() == 4);
    CHECK(bridge.admissionClosed());
    CHECK(!bridge.nativeDrainIntegrated());
    CHECK(bridge.allQuiescent());

    std::vector<const Bridge::Runtime *> authorities;
    for (std::size_t maaId = 0; maaId < bridge.runtimeCount(); ++maaId) {
        authorities.push_back(&bridge.runtime(maaId));
        CHECK(bridge.generation(maaId) == 1);
        CHECK(bridge.runtimeIdentity(maaId) == maaId + 1);
        CHECK(bridge.quiescent(maaId));
        CHECK(bridge.destructionSafe(maaId));
        CHECK(!bridge.sealed(maaId));
    }
    for (std::size_t left = 0; left < authorities.size(); ++left) {
        for (std::size_t right = left + 1; right < authorities.size();
             ++right) {
            CHECK(authorities[left] != authorities[right]);
        }
    }
}

void
checkPartialConstructionFailure()
{
    std::size_t attempts = 0;
    bool threw = false;
    try {
        Bridge bridge(4, [&attempts](std::size_t maaId) {
            ++attempts;
            if (maaId == 2)
                throw std::runtime_error("injected construction failure");
            return std::make_unique<Bridge::Runtime>();
        });
        (void)bridge;
    } catch (const std::runtime_error &) {
        threw = true;
    }
    CHECK(threw);
    CHECK(attempts == 3);

    threw = false;
    try {
        Bridge bridge(2, [](std::size_t maaId) {
            return maaId == 0 ? std::make_unique<Bridge::Runtime>()
                              : std::unique_ptr<Bridge::Runtime>{};
        });
        (void)bridge;
    } catch (const std::runtime_error &) {
        threw = true;
    }
    CHECK(threw);
}

void
checkExactCallbackIdentityAndReset()
{
    Bridge bridge(1);
    const Bridge::CallbackClaim claim = bridge.claimCallback(0);
    CHECK(claim.status == Status::Accepted);
    CHECK(claim.token.valid());
    CHECK(!bridge.quiescent(0));
    CHECK(bridge.reset(0) == Status::Busy);

    Bridge::CallbackToken wrongGeneration = claim.token;
    ++wrongGeneration.generation;
    CHECK(bridge.acknowledgeCallback(wrongGeneration) == Status::Stale);
    Bridge::CallbackToken wrongIdentity = claim.token;
    ++wrongIdentity.identity;
    CHECK(bridge.acknowledgeCallback(wrongIdentity) == Status::Stale);
    CHECK(!bridge.quiescent(0));

    CHECK(bridge.acknowledgeCallback(claim.token) == Status::Accepted);
    CHECK(bridge.acknowledgeCallback(claim.token) == Status::Stale);
    CHECK(bridge.quiescent(0));
    CHECK(bridge.reset(0) == Status::Accepted);
    CHECK(bridge.generation(0) == 2);
    CHECK(bridge.acknowledgeCallback(claim.token) == Status::Stale);

    const Bridge::CallbackClaim successor = bridge.claimCallback(0);
    CHECK(successor.status == Status::Accepted);
    CHECK(successor.token.generation == 2);
    CHECK(successor.token.identity > claim.token.identity);
    CHECK(bridge.acknowledgeCallback(successor.token) == Status::Accepted);
}

void
checkAbortRetainsDirtyOwnerUntilExactAck()
{
    Bridge bridge(1);
    const Bridge::CallbackClaim dirty =
        bridge.claimCallback(0, Bridge::CallbackKind::DirtyFlush);
    CHECK(dirty.status == Status::Accepted);
    CHECK(bridge.dirtyFlushPending(0));
    CHECK(bridge.requestAbort(0) == Status::Busy);
    CHECK(bridge.abortPending(0));
    CHECK(bridge.dirtyFlushPending(0));
    CHECK(bridge.progressAbort(0) == Status::Busy);

    Bridge::CallbackToken stale = dirty.token;
    ++stale.identity;
    CHECK(bridge.acknowledgeCallback(stale) == Status::Stale);
    CHECK(bridge.abortPending(0));
    CHECK(bridge.dirtyFlushPending(0));
    CHECK(!bridge.quiescent(0));

    CHECK(bridge.acknowledgeCallback(dirty.token) == Status::Accepted);
    CHECK(!bridge.abortPending(0));
    CHECK(!bridge.dirtyFlushPending(0));
    CHECK(bridge.quiescent(0));
    CHECK(bridge.progressAbort(0) == Status::Stale);
    CHECK(bridge.acknowledgeCallback(dirty.token) == Status::Stale);
}

void
checkRuntimeDirtyFlushRetainedUntilExactAck()
{
    constexpr uint64_t SourceBase = 0x100000;
    constexpr uint64_t DestinationBase = 0x200000;
    void *sourceAllocation =
        std::aligned_alloc(Slice::BackingBytes, Slice::BackingBytes);
    void *destinationAllocation =
        std::aligned_alloc(Slice::BackingBytes, Slice::BackingBytes);
    CHECK(sourceAllocation != nullptr);
    CHECK(destinationAllocation != nullptr);
    auto *source = static_cast<std::byte *>(sourceAllocation);
    auto *destination = static_cast<std::byte *>(destinationAllocation);
    std::memset(source, 0x3c, Slice::BackingBytes);
    std::memset(destination, 0, Slice::BackingBytes);

    Runtime *authority = nullptr;
    {
        Bridge bridge(1, [&authority](std::size_t) {
            auto runtime = std::make_unique<Runtime>();
            authority = runtime.get();
            return runtime;
        });
        Peer peer;
        CHECK(peer.registerBacking(
            SourceBase, source, Slice::BackingBytes));
        CHECK(peer.registerBacking(
            DestinationBase, destination, Slice::BackingBytes));
        CHECK(authority->registerSource(
                  0, {SourceBase, Slice::BackingBytes}) ==
              Slice::Status::Accepted);
        Slice::Admission admission;
        admission.sourceLogical = 0;
        admission.destinationLogical = 1;
        admission.destination = {DestinationBase, Slice::BackingBytes};
        admission.operation = Slice::Operation::Add;
        admission.scalarBits = bits(1.0);
        CHECK(authority->admit(admission) == Slice::Status::Accepted);

        runAction(*authority, peer);
        CHECK(authority->driveCompute() == Slice::Status::Accepted);
        CHECK(bridge.requestAbort(0) == Status::Busy);
        CHECK(bridge.abortPending(0));
        CHECK(bridge.dirtyFlushPending(0));
        CHECK(authority->correlationSnapshot().abortFlush);

        CHECK(authority->prepare().status == Transport::Status::Accepted);
        uint8_t delayed = Transport::NoRecord;
        while (authority->transportActionState() !=
               Transport::ActionState::Free) {
            while (authority->creditsInUse() < Transport::ResponseCredits) {
                const Transport::Result sent = peer.send(*authority, true);
                if (sent.status != Transport::Status::SendAccepted)
                    break;
            }
            bool progressed = false;
            for (uint8_t record = 0; record < Transport::RecordCount;
                 ++record) {
                if (!peer.hasOutstanding(record))
                    continue;
                if (authority->recordKey(record).line ==
                    Transport::LinesPerPage - 1) {
                    delayed = record;
                    continue;
                }
                (void)completeResponse(*authority, peer, record);
                progressed = true;
            }
            if (delayed != Transport::NoRecord &&
                authority->ackCount() == Transport::LinesPerPage - 1) {
                CHECK(bridge.abortPending(0));
                CHECK(bridge.dirtyFlushPending(0));
                CHECK(!authority->abortCompleted());
                const Transport::Result exact =
                    completeResponse(*authority, peer, delayed);
                CHECK(exact.status == Transport::Status::Completed);
                delayed = Transport::NoRecord;
                progressed = true;
            }
            CHECK(progressed || authority->transportActionState() ==
                                    Transport::ActionState::Free);
        }
        CHECK(authority->abortCompleted());
        CHECK(bridge.abortPending(0));
        CHECK(bridge.progressAbort(0) == Status::Accepted);
        CHECK(!bridge.abortPending(0));
        CHECK(!bridge.dirtyFlushPending(0));
        CHECK(bridge.quiescent(0));
    }
    std::free(destinationAllocation);
    std::free(sourceAllocation);
}

void
checkGuardedTeardown()
{
    Bridge bridge(2);
    const Bridge::CallbackClaim live = bridge.claimCallback(1);
    CHECK(live.status == Status::Accepted);
    CHECK(bridge.teardown(1) == Status::Busy);
    CHECK(!bridge.sealed(1));
    CHECK(!bridge.destructionSafe(1));

    CHECK(bridge.acknowledgeCallback(live.token) == Status::Accepted);
    CHECK(bridge.teardown(1) == Status::Accepted);
    CHECK(bridge.sealed(1));
    CHECK(bridge.destructionSafe(1));
    CHECK(bridge.teardown(1) == Status::Sealed);
    CHECK(bridge.reset(1) == Status::Sealed);
    CHECK(bridge.claimCallback(1).status == Status::Sealed);

    CHECK(bridge.teardown(0) == Status::Accepted);
    CHECK(bridge.sealed(0));
}

void
checkImpossibleBridgeStateFailsClosed()
{
    Bridge bridge(1);
    const Bridge::CallbackClaim impossible = bridge.claimCallback(
        0, static_cast<Bridge::CallbackKind>(0xff));
    CHECK(impossible.status == Status::ProductionStop);
    CHECK(!impossible.token.valid());
    CHECK(bridge.productionStopped(0));
    CHECK(!bridge.quiescent(0));
    CHECK(bridge.reset(0) == Status::ProductionStop);
    CHECK(bridge.requestAbort(0) == Status::ProductionStop);
    CHECK(bridge.claimCallback(0).status == Status::ProductionStop);
    CHECK(bridge.admissionClosed());
}

} // anonymous namespace

int
main()
{
    checkConstructionAndAdmissionClosure();
    checkPartialConstructionFailure();
    checkExactCallbackIdentityAndReset();
    checkAbortRetainsDirtyOwnerUntilExactAck();
    checkRuntimeDirtyFlushRetainedUntilExactAck();
    checkGuardedTeardown();
    checkImpossibleBridgeStateFailsClosed();
    std::cout << "logical SPD bridge lifecycle tests passed\n";
    return 0;
}
