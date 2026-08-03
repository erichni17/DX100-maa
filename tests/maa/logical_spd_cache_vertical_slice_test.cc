#include <sys/wait.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <new>

#include "mem/MAA/LogicalSPDCacheRuntime.hh"
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

using Runtime = gem5::LogicalSPDCacheRuntime;
using Slice = Runtime::Slice;
using Transport = Runtime::Transport;
using Datapath = Runtime::Datapath;
using Peer = gem5::LogicalSPDCacheMockPeer;

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

uint64_t
bits(double value)
{
    uint64_t result = 0;
    static_assert(sizeof(result) == sizeof(value));
    std::memcpy(&result, &value, sizeof(result));
    return result;
}

double
apply(Slice::Operation operation, double left, double right)
{
    switch (operation) {
      case Slice::Operation::Add:
        return left + right;
      case Slice::Operation::Sub:
        return left - right;
      case Slice::Operation::Mul:
        return left * right;
      case Slice::Operation::Div:
        return left / right;
      case Slice::Operation::Min:
        return std::min(left, right);
      case Slice::Operation::Max:
        return std::max(left, right);
    }
    std::abort();
}

class GuardedAlignedSpan
{
  public:
    static constexpr std::size_t Elements =
        Slice::Pages * Slice::PageElements;
    static constexpr std::size_t AllocationBytes = 3 * Slice::BackingBytes;
    static constexpr uint64_t BeforeValue = 0x1020304050607080ULL;
    static constexpr uint64_t AfterValue = 0x8877665544332211ULL;

    GuardedAlignedSpan()
    {
        allocation = std::aligned_alloc(Slice::BackingBytes,
                                        AllocationBytes);
        CHECK(allocation != nullptr);
        auto *raw = static_cast<std::byte *>(allocation);
        payload = raw + Slice::BackingBytes;
        before = ::new (payload - sizeof(uint64_t)) uint64_t(BeforeValue);
        after = ::new (payload + Slice::BackingBytes) uint64_t(AfterValue);
        values = reinterpret_cast<double *>(payload);
        for (std::size_t index = 0; index < Elements; ++index)
            ::new (values + index) double(0.0);
        CHECK(reinterpret_cast<uintptr_t>(payload) % Slice::BackingBytes == 0);
    }

    ~GuardedAlignedSpan() { std::free(allocation); }

    GuardedAlignedSpan(const GuardedAlignedSpan &) = delete;
    GuardedAlignedSpan &operator=(const GuardedAlignedSpan &) = delete;

    std::byte *data() { return payload; }
    const std::byte *data() const { return payload; }
    double *doubles() { return values; }
    const double *doubles() const { return values; }
    bool guardsExact() const
    {
        return *before == BeforeValue && *after == AfterValue;
    }

  private:
    void *allocation = nullptr;
    std::byte *payload = nullptr;
    uint64_t *before = nullptr;
    uint64_t *after = nullptr;
    double *values = nullptr;
};

struct Harness
{
    static constexpr uint64_t SourceBase = 0x100000;
    static constexpr uint64_t DestinationBase = 0x200000;

    Runtime runtime;
    Peer peer;
    GuardedAlignedSpan source;
    GuardedAlignedSpan destination;
    std::array<uint64_t, GuardedAlignedSpan::Elements> sourceBits{};
    uint64_t fillResponses = 0;
    uint64_t writeResponses = 0;
    const double scalar = 2.5;

    Harness()
    {
        for (std::size_t index = 0; index < GuardedAlignedSpan::Elements;
             ++index) {
            source.doubles()[index] =
                1.0 + static_cast<double>(index % 251) / 17.0;
            destination.doubles()[index] = -9000.0;
            sourceBits[index] = bits(source.doubles()[index]);
        }
        CHECK(peer.registerBacking(SourceBase, source.data(),
                                   Slice::BackingBytes));
        CHECK(peer.registerBacking(DestinationBase, destination.data(),
                                   Slice::BackingBytes));
        CHECK(runtime.initialize(0) == Slice::Status::Accepted);
        CHECK(runtime.registerSource(
                  0, {SourceBase, Slice::BackingBytes}) ==
              Slice::Status::Accepted);
        Slice::Admission admission;
        admission.sourceLogical = 0;
        admission.destinationLogical = 1;
        admission.destination = {DestinationBase, Slice::BackingBytes};
        admission.operation = Slice::Operation::Add;
        admission.scalarBits = bits(scalar);
        CHECK(runtime.admit(admission) == Slice::Status::Accepted);
    }

    void completeResponse(uint8_t record, bool expectFinal = false)
    {
        const Transport::TransactionKey key = runtime.recordKey(record);
        auto response = peer.makeResponse(record, true);
        CHECK(response.valid);
        const auto *request = peer.request(record);
        CHECK(request != nullptr);
        const Transport::Result delivered = peer.deliver(
            runtime, record, response.handle, request->callbackPort);
        if (key.operation == Transport::Operation::Fill) {
            CHECK(delivered.status == Transport::Status::DeliveryPending);
            const Transport::Result committed =
                runtime.commitDelivery(delivered.ticket);
            CHECK(committed.status == Transport::Status::Accepted ||
                  committed.status == Transport::Status::Completed);
            if (expectFinal) {
                CHECK(committed.status == Transport::Status::Completed);
                CHECK(committed.completion.valid());
                CHECK(committed.completion.controllerSerial() != 0);
            }
            ++fillResponses;
        } else {
            CHECK(delivered.status == Transport::Status::Accepted ||
                  delivered.status == Transport::Status::Completed);
            ++writeResponses;
            if (expectFinal) {
                CHECK(delivered.status == Transport::Status::Completed);
                CHECK(delivered.completion.valid());
                CHECK(delivered.completion.controllerSerial() != 0);
            }
        }
    }

    void runPageAction(bool delayFinalWrite = false)
    {
        const auto first = runtime.prepare();
        CHECK(first.status == Transport::Status::Accepted);
        const auto correlation = runtime.correlationSnapshot();
        CHECK(correlation.pageActive);
        const bool write = correlation.pageAction.operation ==
                           Slice::PageOperation::Writeback;
        uint8_t delayed = Transport::NoRecord;
        while (runtime.transportActionState() !=
               Transport::ActionState::Free) {
            while (runtime.creditsInUse() < Transport::ResponseCredits) {
                const Transport::Result sent = peer.send(runtime, true);
                if (sent.status != Transport::Status::SendAccepted)
                    break;
            }
            bool progressed = false;
            for (std::size_t offset = 0; offset < Transport::RecordCount;
                 ++offset) {
                const uint8_t record = static_cast<uint8_t>(
                    Transport::RecordCount - 1 - offset);
                if (!peer.hasOutstanding(record))
                    continue;
                const auto key = runtime.recordKey(record);
                if (delayFinalWrite && write &&
                    key.line == Transport::LinesPerPage - 1) {
                    delayed = record;
                    continue;
                }
                completeResponse(record);
                progressed = true;
            }
            if (delayed != Transport::NoRecord &&
                runtime.ackCount() == Transport::LinesPerPage - 1) {
                CHECK(!runtime.operationComplete());
                completeResponse(delayed, true);
                delayed = Transport::NoRecord;
                progressed = true;
            }
            CHECK(progressed || runtime.transportActionState() ==
                                     Transport::ActionState::Free);
        }
    }

    void finishOnePage(bool delayedWrite = false)
    {
        runPageAction();
        CHECK(runtime.driveCompute() == Slice::Status::Accepted);
        runPageAction(delayedWrite);
    }

    void checkSourceExact() const
    {
        for (std::size_t index = 0; index < sourceBits.size(); ++index)
            CHECK(bits(source.doubles()[index]) == sourceBits[index]);
        CHECK(source.guardsExact());
    }
};

void
checkAbortClean(const Harness &harness)
{
    const auto state = harness.runtime.sliceSnapshot();
    CHECK(harness.runtime.abortCompleted());
    CHECK(!harness.runtime.operationComplete());
    CHECK(!state.active);
    CHECK(!state.refillPending);
    CHECK(!state.memoryActionActive);
    CHECK(state.missQueueSize == 0);
    CHECK(state.activeLeases == 0);
    CHECK(state.descriptors[1].role == Slice::DescriptorRole::Free);
    CHECK(state.counters.highLevelCompletions == 0);
    for (const auto phase : state.slotPhases) {
        CHECK(phase == Slice::Controller::Phase::Empty ||
              phase == Slice::Controller::Phase::Clean);
    }
    CHECK(!harness.runtime.poisoned());
    CHECK(harness.destination.guardsExact());
    harness.checkSourceExact();
}

void
testAuthenticatedVerticalAll16KDelayedAckAndDestinationRefill()
{
    Harness harness;
    for (uint8_t page = 0; page < Slice::Pages; ++page)
        harness.finishOnePage(page == Slice::Pages - 1);

    CHECK(harness.runtime.operationComplete());
    CHECK(harness.runtime.descriptorComplete(1));
    CHECK(harness.fillResponses == Slice::Pages * Transport::LinesPerPage);
    CHECK(harness.writeResponses ==
          Slice::Pages * Transport::LinesPerPage);
    const auto completed = harness.runtime.sliceSnapshot();
    CHECK(completed.counters.highLevelCompletions == 1);
    CHECK(completed.counters.pagesCompleted == Slice::Pages);
    for (std::size_t index = 0; index < GuardedAlignedSpan::Elements;
         ++index) {
        CHECK(bits(harness.destination.doubles()[index]) ==
              bits(apply(Slice::Operation::Add,
                         harness.source.doubles()[index], harness.scalar)));
    }
    CHECK(harness.destination.guardsExact());
    harness.checkSourceExact();

    CHECK(harness.runtime.retireCompletedOperation() ==
          Slice::Status::Accepted);
    CHECK(harness.runtime.queueRefill(1, 3) == Slice::Status::Accepted);
    const uint64_t fillsBefore = harness.fillResponses;
    harness.runPageAction();
    CHECK(harness.fillResponses == fillsBefore + Transport::LinesPerPage);
    const auto refill = harness.runtime.sliceSnapshot();
    CHECK(refill.counters.refillCompletions == 1);
    const uint16_t slot = refill.slotIdentities[0].logical == 1 &&
                                  refill.slotIdentities[0].page == 3
                              ? 0
                              : 1;
    CHECK(refill.slotIdentities[slot].logical == 1);
    CHECK(refill.slotIdentities[slot].page == 3);
    const auto payload = harness.runtime.slotPayload(
        static_cast<uint8_t>(slot));
    CHECK(payload.size == Slice::PageBytes);
    CHECK(std::memcmp(payload.data,
                      harness.destination.data() + 3 * Slice::PageBytes,
                      Slice::PageBytes) == 0);
}

void
testAbortQueuedPendingRetryInflightAndDelivering()
{
    {
        Harness queued;
        CHECK(queued.runtime.abort(Slice::AbortCode::Caller) ==
              Slice::Status::Accepted);
        checkAbortClean(queued);
    }
    {
        Harness pending;
        CHECK(pending.runtime.prepare().status ==
              Transport::Status::Accepted);
        CHECK(pending.runtime.abort(Slice::AbortCode::Caller) ==
              Slice::Status::Accepted);
        checkAbortClean(pending);
    }
    {
        Harness retry;
        CHECK(retry.runtime.prepare().status == Transport::Status::Accepted);
        CHECK(retry.runtime.sendPrepared(false).status ==
              Transport::Status::SendRefused);
        CHECK(retry.runtime.abort(Slice::AbortCode::Caller) ==
              Slice::Status::Accepted);
        checkAbortClean(retry);
    }
    {
        Harness inflight;
        const auto sent = inflight.peer.send(inflight.runtime, true);
        CHECK(sent.status == Transport::Status::SendAccepted);
        CHECK(inflight.runtime.abort(Slice::AbortCode::Caller) ==
              Slice::Status::Busy);
        CHECK(!inflight.runtime.transportDrained());
        CHECK(inflight.runtime.reset() == Slice::Status::Busy);
        CHECK(inflight.runtime.teardown() == Slice::Status::Busy);
        CHECK(inflight.peer.respond(inflight.runtime, sent.record).status ==
              Transport::Status::AbortDrained);
        checkAbortClean(inflight);
    }
    {
        Harness delivering;
        const auto sent = delivering.peer.send(delivering.runtime, true);
        CHECK(sent.status == Transport::Status::SendAccepted);
        auto response = delivering.peer.makeResponse(sent.record, true);
        CHECK(response.valid);
        const auto *request = delivering.peer.request(sent.record);
        CHECK(request != nullptr);
        const auto staged = delivering.peer.deliver(
            delivering.runtime, sent.record, response.handle,
            request->callbackPort);
        CHECK(staged.status == Transport::Status::DeliveryPending);
        CHECK(delivering.runtime.abort(Slice::AbortCode::Caller) ==
              Slice::Status::Accepted);
        checkAbortClean(delivering);
    }
}

void
testAbortReservedComputingDirtyWritebackAndBetweenPages()
{
    {
        Harness reserved;
        reserved.runPageAction();
        CHECK(reserved.runtime.sliceSnapshot().stage ==
              Slice::Stage::ComputeReady);
        CHECK(reserved.runtime.abort(Slice::AbortCode::Caller) ==
              Slice::Status::Accepted);
        checkAbortClean(reserved);
    }
    {
        Harness computing;
        computing.runPageAction();
        CHECK(computing.runtime.beginCompute() == Slice::Status::Accepted);
        CHECK(computing.runtime.sliceSnapshot().stage ==
              Slice::Stage::Computing);
        CHECK(computing.runtime.abort(Slice::AbortCode::Caller) ==
              Slice::Status::Accepted);
        checkAbortClean(computing);
    }
    {
        Harness dirty;
        dirty.runPageAction();
        CHECK(dirty.runtime.driveCompute() == Slice::Status::Accepted);
        CHECK(dirty.runtime.sliceSnapshot().stage ==
              Slice::Stage::WaitingWriteback);
        CHECK(dirty.runtime.abort(Slice::AbortCode::Caller) ==
              Slice::Status::Busy);
        CHECK(dirty.runtime.correlationSnapshot().abortFlush);
        dirty.runPageAction();
        checkAbortClean(dirty);
        for (std::size_t index = 0; index < Slice::PageElements; ++index) {
            CHECK(bits(dirty.destination.doubles()[index]) ==
                  bits(dirty.source.doubles()[index] + dirty.scalar));
        }
    }
    {
        Harness writeback;
        writeback.runPageAction();
        CHECK(writeback.runtime.driveCompute() == Slice::Status::Accepted);
        const auto sent = writeback.peer.send(writeback.runtime, true);
        CHECK(sent.status == Transport::Status::SendAccepted);
        CHECK(writeback.runtime.abort(Slice::AbortCode::Caller) ==
              Slice::Status::Busy);
        CHECK(writeback.peer.respond(writeback.runtime, sent.record).status ==
              Transport::Status::AbortDrained);
        CHECK(writeback.runtime.correlationSnapshot().abortFlush);
        writeback.runPageAction();
        checkAbortClean(writeback);
    }
    {
        Harness betweenPages;
        betweenPages.finishOnePage();
        CHECK(betweenPages.runtime.sliceSnapshot().page == 1);
        CHECK(betweenPages.runtime.abort(Slice::AbortCode::Caller) ==
              Slice::Status::Accepted);
        checkAbortClean(betweenPages);
        for (std::size_t index = 0; index < Slice::PageElements; ++index) {
            CHECK(bits(betweenPages.destination.doubles()[index]) ==
                  bits(betweenPages.source.doubles()[index] +
                       betweenPages.scalar));
        }
    }
}

void
testDatapathRejectsBeforeMutationAndSpecialValues()
{
    alignas(double)
        std::array<double, Datapath::PageElements> source{};
    alignas(double)
        std::array<double, Datapath::PageElements> destination{};
    for (std::size_t index = 0; index < source.size(); ++index) {
        source[index] = static_cast<double>(index) / 13.0;
        destination[index] = -static_cast<double>(index) - 7.0;
    }
    const auto exact = destination;
    const auto expectUnchanged = [&](Datapath::Operation operation,
                                     Datapath::ConstSpan input,
                                     Datapath::Span output) {
        CHECK(Datapath::transform(operation, input, output, bits(2.0)) !=
              Datapath::Result::Accepted);
        CHECK(destination == exact);
    };
    expectUnchanged(Datapath::Operation::Add,
                    {nullptr, Datapath::PageElements},
                    {destination.data(), Datapath::PageElements});
    expectUnchanged(Datapath::Operation::Add,
                    {source.data(), Datapath::PageElements},
                    {nullptr, Datapath::PageElements});
    expectUnchanged(Datapath::Operation::Add,
                    {source.data(), Datapath::PageElements - 1},
                    {destination.data(), Datapath::PageElements});
    expectUnchanged(Datapath::Operation::Add,
                    {source.data(), Datapath::PageElements + 1},
                    {destination.data(), Datapath::PageElements});
    expectUnchanged(Datapath::Operation::Add,
                    {source.data(), Datapath::PageElements},
                    {destination.data(), Datapath::PageElements - 1});
    expectUnchanged(Datapath::Operation::Add,
                    {source.data(), Datapath::PageElements},
                    {destination.data(), Datapath::PageElements + 1});
    expectUnchanged(static_cast<Datapath::Operation>(0xff),
                    {source.data(), Datapath::PageElements},
                    {destination.data(), Datapath::PageElements});
    expectUnchanged(Datapath::Operation::Add,
                    {destination.data(), Datapath::PageElements},
                    {destination.data(), Datapath::PageElements});
    const auto *misalignedSource = reinterpret_cast<const double *>(
        reinterpret_cast<const std::byte *>(source.data()) + 1);
    expectUnchanged(Datapath::Operation::Add,
                    {misalignedSource, Datapath::PageElements},
                    {destination.data(), Datapath::PageElements});
    auto *misalignedDestination = reinterpret_cast<double *>(
        reinterpret_cast<std::byte *>(destination.data()) + 1);
    CHECK(Datapath::transform(
              Datapath::Operation::Add,
              {source.data(), Datapath::PageElements},
              {misalignedDestination, Datapath::PageElements}, bits(2.0)) ==
          Datapath::Result::Invalid);
    CHECK(destination == exact);
    const auto *overflowSource = reinterpret_cast<const double *>(
        std::numeric_limits<uintptr_t>::max() - sizeof(double));
    expectUnchanged(Datapath::Operation::Add,
                    {overflowSource, Datapath::PageElements},
                    {destination.data(), Datapath::PageElements});
    auto *overflowDestination = reinterpret_cast<double *>(
        std::numeric_limits<uintptr_t>::max() - sizeof(double));
    expectUnchanged(Datapath::Operation::Add,
                    {source.data(), Datapath::PageElements},
                    {overflowDestination, Datapath::PageElements});

    source.fill(1.0);
    destination.fill(7.0);
    source[0] = std::numeric_limits<double>::quiet_NaN();
    source[1] = std::numeric_limits<double>::infinity();
    source[2] = -std::numeric_limits<double>::infinity();
    source[3] = -0.0;
    source[4] = 0.0;
    CHECK(Datapath::transform(
              Datapath::Operation::Add,
              {source.data(), source.size()},
              {destination.data(), destination.size()}, bits(0.0)) ==
          Datapath::Result::Accepted);
    CHECK(std::isnan(destination[0]));
    CHECK(std::isinf(destination[1]) && destination[1] > 0.0);
    CHECK(std::isinf(destination[2]) && destination[2] < 0.0);
    CHECK(!std::signbit(destination[3]));

    source.fill(1.0);
    source[0] = std::numeric_limits<double>::quiet_NaN();
    source[1] = -0.0;
    source[2] = 0.0;
    CHECK(Datapath::transform(
              Datapath::Operation::Min,
              {source.data(), source.size()},
              {destination.data(), destination.size()}, bits(0.0)) ==
          Datapath::Result::Accepted);
    CHECK(std::isnan(destination[0]));
    CHECK(std::signbit(destination[1]));
    CHECK(!std::signbit(destination[2]));
    CHECK(Datapath::transform(
              Datapath::Operation::Max,
              {source.data(), source.size()},
              {destination.data(), destination.size()},
              bits(std::numeric_limits<double>::quiet_NaN())) ==
          Datapath::Result::Accepted);
    CHECK(std::isnan(destination[0]));
    CHECK(std::signbit(destination[1]));
}

void
testGeometryEnumAndJointLifecycleGates()
{
    Runtime wrongGeometry(8, Transport::LineBytes);
    CHECK(!wrongGeometry.geometryValid());
    CHECK(wrongGeometry.initialize(0) == Slice::Status::Invalid);

    Runtime spans;
    CHECK(spans.initialize(1) == Slice::Status::Accepted);
    CHECK(spans.registerSource(
              0, {Harness::SourceBase, Slice::BackingBytes - 1}) ==
          Slice::Status::Invalid);
    CHECK(spans.registerSource(
              0, {Harness::SourceBase + Transport::LineBytes,
                  Slice::BackingBytes}) == Slice::Status::Invalid);
    CHECK(spans.registerSource(
              0, {std::numeric_limits<uint64_t>::max() -
                          Slice::BackingBytes + 2,
                  Slice::BackingBytes}) == Slice::Status::Invalid);
    CHECK(spans.registerSource(
              0, {Harness::SourceBase, Slice::BackingBytes}) ==
          Slice::Status::Accepted);
    Slice::Admission invalidSpan;
    invalidSpan.sourceLogical = 0;
    invalidSpan.destinationLogical = 1;
    invalidSpan.destination =
        {Harness::DestinationBase, Slice::BackingBytes + 1};
    CHECK(spans.admit(invalidSpan) == Slice::Status::Invalid);
    invalidSpan.destination = {Harness::SourceBase, Slice::BackingBytes};
    CHECK(spans.admit(invalidSpan) == Slice::Status::Invalid);

    Harness enumHarness;
    Slice::Admission forged;
    forged.sourceLogical = 0;
    forged.destinationLogical = 1;
    forged.destination = {Harness::DestinationBase, Slice::BackingBytes};
    forged.operation = static_cast<Slice::Operation>(0xff);
    CHECK(enumHarness.runtime.admit(forged) == Slice::Status::Invalid);
    CHECK(enumHarness.runtime.abort(static_cast<Slice::AbortCode>(0xff)) ==
          Slice::Status::Invalid);
    CHECK(!enumHarness.runtime.poisoned());
    CHECK(enumHarness.runtime.requestDrain() == Slice::Status::Accepted);
    CHECK(enumHarness.runtime.reset() == Slice::Status::Busy);
    CHECK(enumHarness.runtime.teardown() == Slice::Status::Busy);
    CHECK(enumHarness.runtime.abort(Slice::AbortCode::Caller) ==
          Slice::Status::Accepted);
    CHECK(enumHarness.runtime.drained());
    CHECK(enumHarness.runtime.reset() == Slice::Status::Accepted);
    CHECK(enumHarness.runtime.teardown() == Slice::Status::Accepted);
    CHECK(enumHarness.runtime.prepare().status ==
          Transport::Status::Sealed);
    CHECK(enumHarness.runtime.registerSource(
              0, {Harness::SourceBase, Slice::BackingBytes}) ==
          Slice::Status::Sealed);
}

void
testPackedSemanticLedgerIndependently()
{
    using Ledger = Runtime::PackedSemanticLedger;
    static_assert(Ledger::ControllerDescriptors == 74);
    static_assert(Ledger::ControllerSlots == 334);
    static_assert(Ledger::ControllerMissQueue == 140);
    static_assert(Ledger::ControllerLeases == 672);
    static_assert(Ledger::ControllerBits ==
                  74 + 334 + 140 + 672 + 3 + 64);
    static_assert(Ledger::SliceDescriptors == 438);
    static_assert(Ledger::SliceOverwriteReservation == 336);
    static_assert(Ledger::SliceActiveOperation == 508);
    static_assert(Ledger::SliceControllerMemoryAction == 139);
    static_assert(Ledger::SlicePageAction == 305);
    static_assert(Ledger::SliceBits ==
                  1287 + 438 + 508 + 305 + 1 + 35 + 1 + 32 + 64 + 16 +
                      7);
    static_assert(Ledger::SliceInstrumentationCounterBits == 768);
    static_assert(Ledger::TransportTransactionKey == 46);
    static_assert(Ledger::TransportRequestPacket == 143);
    static_assert(Ledger::TransportRecords == 3192);
    static_assert(Ledger::TransportFifo == 42);
    static_assert(Ledger::TransportLineBuffers == 2048);
    static_assert(Ledger::TransportPageAction == 1247);
    static_assert(Ledger::TransportBits == 6715);
    static_assert(Ledger::RuntimePageCorrelation == 339);
    static_assert(Ledger::SliceComputeAction == 236);
    static_assert(Ledger::RuntimeComputeCorrelation == 237);
    static_assert(Ledger::RuntimeCorrelationBits == 579);
    static_assert(Ledger::PrivatePayloadBits == 524288);
    static_assert(Ledger::PackedBits == 2694 + 6715 + 579 + 524288);
    static_assert(Ledger::PackedBytes == 66785);
    static_assert(Ledger::PythonReferenceLowerBoundBytes == 66181);
    CHECK(sizeof(Runtime) >= Ledger::PackedBytes);
}

struct RuntimeCopyHookContext
{
    Runtime *runtime = nullptr;
    Slice::Status abortStatus = Slice::Status::Invalid;
};

bool
runtimeCopyHook(void *opaque)
{
    auto &context = *static_cast<RuntimeCopyHookContext *>(opaque);
    context.abortStatus = context.runtime->abort(Slice::AbortCode::Caller);
    return true;
}

void
testCompositionCopyReentryPoisonsBeforeOuterCopy()
{
    expectChildSuccess([] {
        Harness harness;
        const auto sent = harness.peer.send(harness.runtime, true);
        CHECK(sent.status == Transport::Status::SendAccepted);
        auto response = harness.peer.makeResponse(sent.record, true);
        CHECK(response.valid);
        const auto *request = harness.peer.request(sent.record);
        CHECK(request != nullptr);
        const auto staged = harness.peer.deliver(
            harness.runtime, sent.record, response.handle,
            request->callbackPort);
        CHECK(staged.status == Transport::Status::DeliveryPending);
        const auto correlation = harness.runtime.correlationSnapshot();
        const auto payload = harness.runtime.slotPayload(
            correlation.pageAction.slot);
        std::array<std::byte, Slice::PageBytes> before{};
        std::memcpy(before.data(), payload.data, payload.size);
        const uint16_t ackBefore = harness.runtime.ackCount();
        RuntimeCopyHookContext hook{&harness.runtime};
        CHECK(harness.runtime.commitDelivery(
                  staged.ticket, runtimeCopyHook, &hook)
                  .status == Transport::Status::Poisoned);
        CHECK(hook.abortStatus == Slice::Status::ProductionStop);
        CHECK(harness.runtime.poisoned());
        CHECK(harness.runtime.ackCount() == ackBefore);
        CHECK(std::memcmp(before.data(), payload.data, payload.size) == 0);
        CHECK(!harness.runtime.operationComplete());
        CHECK(harness.runtime.abort(Slice::AbortCode::Caller) ==
              Slice::Status::Poisoned);
        std::_Exit(0);
    });
}

} // namespace

int
main()
{
    testAuthenticatedVerticalAll16KDelayedAckAndDestinationRefill();
    testAbortQueuedPendingRetryInflightAndDelivering();
    testAbortReservedComputingDirtyWritebackAndBetweenPages();
    testDatapathRejectsBeforeMutationAndSpecialValues();
    testGeometryEnumAndJointLifecycleGates();
    testPackedSemanticLedgerIndependently();
    testCompositionCopyReentryPoisonsBeforeOuterCopy();
    std::cout << "logical_spd_cache_vertical_slice_test: PASS"
              << " packed_semantic_lower_bound="
              << Runtime::PackedSemanticLedger::PackedBytes
              << " python_reference_lower_bound="
              << Runtime::PackedSemanticLedger::PythonReferenceLowerBoundBytes
              << " host_runtime_size=" << sizeof(Runtime)
              << " (host sizeof; not synthesized hardware size)"
              << std::endl;
    return 0;
}
