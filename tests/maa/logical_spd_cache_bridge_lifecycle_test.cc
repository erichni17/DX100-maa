#include <algorithm>
#include <array>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <vector>

#include "mem/MAA/LogicalSPDCacheGem5Bridge.hh"
#include "tests/maa/support/logical_spd_cache_mock_peer.hh"

namespace gem5 {

struct LogicalSPDCacheGem5BridgeTestAccess
{
    using Bridge = LogicalSPDCacheGem5Bridge;

    struct IncarnationBoundary
    {
        uint64_t penultimate = 0;
        uint64_t final = 0;
        bool exhausted = false;
        bool partialConstructionExhausted = false;
        std::size_t partialConstructionAttempts = 0;
    };

    static void
    setNextCallbackIdentity(Bridge &bridge, uint64_t identity)
    {
        bridge.nextCallbackIdentity = identity;
    }

    static void
    setGeneration(Bridge &bridge, std::size_t maaId, uint64_t generation)
    {
        bridge.lifecycle.at(maaId).generation = generation;
    }

    static IncarnationBoundary
    exerciseIncarnationBoundary()
    {
        const auto factory = [](std::size_t) {
            return std::make_unique<Bridge::Runtime>();
        };
        IncarnationBoundary result;
        Bridge::IncarnationSource boundary(
            std::numeric_limits<uint64_t>::max() - 1);
        {
            Bridge bridge(2, factory, boundary);
            result.penultimate = bridge.runtimeIdentity(0);
            result.final = bridge.runtimeIdentity(1);
        }
        try {
            Bridge bridge(1, factory, boundary);
            (void)bridge;
        } catch (const std::overflow_error &) {
            result.exhausted = true;
        }

        Bridge::IncarnationSource partial(
            std::numeric_limits<uint64_t>::max());
        try {
            Bridge bridge(2, [&result](std::size_t) {
                ++result.partialConstructionAttempts;
                return std::make_unique<Bridge::Runtime>();
            }, partial);
            (void)bridge;
        } catch (const std::overflow_error &) {
            result.partialConstructionExhausted = true;
        }
        return result;
    }
};

} // namespace gem5

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
checkConstructionAndAdmissionBoundary()
{
    Bridge bridge(4);
    CHECK(bridge.runtimeCount() == 4);
    CHECK(!bridge.admissionClosed());
    CHECK(!bridge.nativeDrainIntegrated());
    CHECK(bridge.allQuiescent());
    bridge.closeAdmission();
    CHECK(bridge.admissionClosed());
    CHECK(bridge.claimCallback(0).status == Status::Sealed);
    bridge.reopenAdmission();
    CHECK(!bridge.admissionClosed());
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

class LiveAdapterHarness
{
  public:
    static constexpr uint64_t SourceBase = 0x100000;
    static constexpr uint64_t DestinationBase = 0x200000;
    static constexpr std::size_t Elements =
        Slice::BackingBytes / sizeof(double);

    LiveAdapterHarness()
    {
        for (std::size_t index = 0; index < Elements;
             ++index) {
            source[index] = 1.0 + static_cast<double>(index % 97);
            destination[index] = -1.0;
        }
        claim = bridge.claimCallback(0);
        CHECK(claim.status == Status::Accepted);
        CHECK(bridge.registerSource(
                  claim.token, 0, {SourceBase, Slice::BackingBytes}) ==
              Slice::Status::Accepted);
        Slice::Admission admission;
        admission.sourceLogical = 0;
        admission.destinationLogical = 1;
        admission.destination = {DestinationBase, Slice::BackingBytes};
        admission.operation = Slice::Operation::Mul;
        admission.scalarBits = bits(2.0);
        CHECK(bridge.admit(claim.token, admission) ==
              Slice::Status::Accepted);
    }

    void run()
    {
        bool retryExercised = false;
        bool staleExercised = false;
        while (!bridge.operationComplete(claim.token)) {
            runAction(retryExercised, staleExercised);
            const Slice::Status computed =
                bridge.driveCompute(claim.token);
            CHECK(computed == Slice::Status::Accepted ||
                  computed == Slice::Status::NotReady);
        }
        CHECK(retryExercised);
        CHECK(staleExercised);
        CHECK(fillResponses ==
              Slice::Pages * Transport::LinesPerPage);
        CHECK(writeResponses ==
              Slice::Pages * Transport::LinesPerPage);
        for (std::size_t index = 0; index < Elements;
             ++index) {
            CHECK(destination[index] == source[index] * 2.0);
        }

        const Bridge::CallbackToken completed = claim.token;
        CHECK(bridge.completeOperation(completed) == Status::Accepted);
        CHECK(bridge.generation(0) == completed.generation + 1);
        CHECK(bridge.quiescent(0));
        CHECK(bridge.prepare(completed).status ==
              Transport::Status::Invalid);
        CHECK(bridge.reset(0) == Status::Accepted);
        CHECK(bridge.generation(0) == completed.generation + 2);
    }

  private:
    struct Outstanding
    {
        bool live = false;
        Transport::RequestPacket request{};
    };

    void issueOne(const Transport::Result &prepared,
                  bool &retryExercised)
    {
        CHECK(prepared.status == Transport::Status::Accepted);
        CHECK(prepared.handle != nullptr);
        const Transport::RequestPacket request = *prepared.handle;
        if (!retryExercised) {
            CHECK(bridge.sendPrepared(claim.token, false).status ==
                  Transport::Status::SendRefused);
            CHECK(bridge.recvReqRetry(claim.token,
                                      request.callbackPort) ==
                  Transport::Status::Accepted);
            retryExercised = true;
        }
        const Transport::Result sent =
            bridge.sendPrepared(claim.token, true);
        CHECK(sent.status == Transport::Status::SendAccepted);
        CHECK(sent.record < Transport::RecordCount);
        CHECK(!outstanding[sent.record].live);
        outstanding[sent.record] = {true, request};
    }

    void respond(uint8_t record, bool &staleExercised)
    {
        Outstanding &entry = outstanding[record];
        CHECK(entry.live);
        const Transport::RequestPacket &request = entry.request;
        const bool read = request.command == Transport::Command::ReadReq;
        const uint64_t base = read ? SourceBase : DestinationBase;
        CHECK(request.address >= base);
        const std::size_t offset = request.address - base;
        CHECK(offset <= Slice::BackingBytes - Transport::LineBytes);
        if (!read) {
            CHECK(request.data != nullptr);
            CHECK(request.dataSize == Transport::LineBytes);
            std::memcpy(reinterpret_cast<std::byte *>(destination.data()) +
                            offset,
                        request.data, Transport::LineBytes);
        } else {
            std::memcpy(responseData[record].data(),
                        reinterpret_cast<const std::byte *>(source.data()) +
                            offset,
                        Transport::LineBytes);
        }

        Transport::ReturnedHandle returned;
        returned.incarnation = ++nextResponseIdentity;
        returned.request = request.request;
        returned.requestIncarnation = request.request->incarnation;
        returned.token = request.token;
        returned.tokenDepth = request.tokenDepth;
        returned.tokenRecord = request.token->record;
        returned.tokenEpoch = request.token->epoch;
        returned.tokenActionID = request.token->actionID;
        returned.address = request.address;
        returned.command = read ? Transport::Command::ReadResp
                                : Transport::Command::WriteResp;
        returned.size = request.size;
        if (read) {
            returned.data = responseData[record].data();
            returned.dataSize = Transport::LineBytes;
        }

        if (!staleExercised) {
            Bridge::CallbackToken stale = claim.token;
            ++stale.identity;
            Transport::ReturnedHandle rejected = returned;
            CHECK(bridge.receive(stale, rejected,
                                 request.callbackPort).status ==
                  Transport::Status::Invalid);
            CHECK(!rejected.disposed);
            staleExercised = true;
        }
        const Transport::Result result = bridge.receive(
            claim.token, returned, request.callbackPort);
        CHECK(result.status == Transport::Status::Accepted ||
              result.status == Transport::Status::Completed);
        CHECK(returned.disposed);
        entry = Outstanding{};
        if (read)
            ++fillResponses;
        else
            ++writeResponses;
    }

    void runAction(bool &retryExercised, bool &staleExercised)
    {
        bool actionStarted = false;
        while (!actionStarted ||
               bridge.runtime(0).transportActionState() !=
                   Transport::ActionState::Free) {
            while (bridge.runtime(0).creditsInUse() <
                   Transport::ResponseCredits) {
                const Transport::Result prepared =
                    bridge.prepare(claim.token);
                if (prepared.status != Transport::Status::Accepted) {
                    CHECK(prepared.status ==
                              Transport::Status::NoCreditAvailable ||
                          prepared.status == Transport::Status::NoWork);
                    break;
                }
                actionStarted = true;
                issueOne(prepared, retryExercised);
            }
            bool progressed = false;
            for (uint8_t record = 0; record < Transport::RecordCount;
                 ++record) {
                if (!outstanding[record].live)
                    continue;
                respond(record, staleExercised);
                progressed = true;
            }
            CHECK(progressed ||
                  bridge.runtime(0).transportActionState() ==
                      Transport::ActionState::Free);
        }
    }

    Bridge bridge{1};
    Bridge::CallbackClaim claim{};
    std::array<double, Elements> source{};
    std::array<double, Elements> destination{};
    std::array<Outstanding, Transport::RecordCount> outstanding{};
    std::array<std::array<std::byte, Transport::LineBytes>,
               Transport::RecordCount>
        responseData{};
    uint64_t nextResponseIdentity = 1000;
    uint64_t fillResponses = 0;
    uint64_t writeResponses = 0;
};

void
checkLiveAdmissionFillComputeDirtyWritebackAndReset()
{
    LiveAdapterHarness harness;
    harness.run();
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
    Bridge::CallbackToken wrongRuntime = claim.token;
    ++wrongRuntime.runtimeIdentity;
    CHECK(bridge.acknowledgeCallback(wrongRuntime) == Status::Stale);
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
checkDestroyedBridgeTokenCannotAuthenticateReconstruction()
{
    Bridge::CallbackToken stale;
    {
        Bridge bridge(1);
        const Bridge::CallbackClaim first = bridge.claimCallback(0);
        CHECK(first.status == Status::Accepted);
        stale = first.token;
        CHECK(bridge.acknowledgeCallback(first.token) == Status::Accepted);
    }

    Bridge reconstructed(1);
    const Bridge::CallbackClaim successor = reconstructed.claimCallback(0);
    CHECK(successor.status == Status::Accepted);
    CHECK(successor.token.maaId == stale.maaId);
    CHECK(successor.token.generation == stale.generation);
    CHECK(successor.token.identity == stale.identity);
    CHECK(successor.token.runtimeIdentity != stale.runtimeIdentity);
    CHECK(reconstructed.acknowledgeCallback(stale) == Status::Stale);
    CHECK(!reconstructed.quiescent(0));
    CHECK(reconstructed.acknowledgeCallback(successor.token) ==
          Status::Accepted);
}

void
checkFiniteIdentityBoundariesFailClosed()
{
    const auto boundary =
        gem5::LogicalSPDCacheGem5BridgeTestAccess::
            exerciseIncarnationBoundary();
    CHECK(boundary.penultimate ==
          std::numeric_limits<uint64_t>::max() - 1);
    CHECK(boundary.final == std::numeric_limits<uint64_t>::max());
    CHECK(boundary.exhausted);
    CHECK(boundary.partialConstructionExhausted);
    CHECK(boundary.partialConstructionAttempts == 2);

    Bridge callbackBoundary(1);
    gem5::LogicalSPDCacheGem5BridgeTestAccess::setNextCallbackIdentity(
        callbackBoundary, std::numeric_limits<uint64_t>::max());
    const Bridge::CallbackClaim finalCallback =
        callbackBoundary.claimCallback(0);
    CHECK(finalCallback.status == Status::Accepted);
    CHECK(finalCallback.token.identity ==
          std::numeric_limits<uint64_t>::max());
    CHECK(callbackBoundary.acknowledgeCallback(finalCallback.token) ==
          Status::Accepted);
    CHECK(callbackBoundary.claimCallback(0).status ==
          Status::ProductionStop);
    CHECK(callbackBoundary.productionStopped(0));

    Bridge generationBoundary(1);
    gem5::LogicalSPDCacheGem5BridgeTestAccess::setGeneration(
        generationBoundary, 0, std::numeric_limits<uint64_t>::max());
    CHECK(generationBoundary.reset(0) == Status::ProductionStop);
    CHECK(generationBoundary.productionStopped(0));
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
        const Bridge::CallbackClaim callback = bridge.claimCallback(0);
        CHECK(callback.status == Status::Accepted);
        CHECK(bridge.requestAbort(0) == Status::Busy);
        CHECK(bridge.abortPending(0));
        CHECK(bridge.dirtyFlushPending(0));
        CHECK(authority->correlationSnapshot().abortFlush);
        CHECK(bridge.acknowledgeCallback(callback.token) == Status::Busy);
        CHECK(bridge.abortPending(0));
        CHECK(bridge.dirtyFlushPending(0));
        CHECK(authority->correlationSnapshot().abortFlush);
        CHECK(bridge.acknowledgeCallback(callback.token) == Status::Stale);

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
    CHECK(!bridge.admissionClosed());
}

} // anonymous namespace

int
main()
{
    checkConstructionAndAdmissionBoundary();
    checkLiveAdmissionFillComputeDirtyWritebackAndReset();
    checkPartialConstructionFailure();
    checkExactCallbackIdentityAndReset();
    checkDestroyedBridgeTokenCannotAuthenticateReconstruction();
    checkFiniteIdentityBoundariesFailClosed();
    checkAbortRetainsDirtyOwnerUntilExactAck();
    checkRuntimeDirtyFlushRetainedUntilExactAck();
    checkGuardedTeardown();
    checkImpossibleBridgeStateFailsClosed();
    std::cout << "logical SPD bridge lifecycle tests passed\n";
    return 0;
}
