#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <new>

#include "mem/MAA/LogicalSPDCacheDatapath.hh"

#define private public
#include "mem/MAA/LogicalSPDCacheSlice.hh"

#undef private
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

using Slice = gem5::LogicalSPDCacheSlice;
using Datapath = gem5::LogicalSPDCacheDatapath;
using Transport = gem5::LogicalSPDCacheTransport;
using Peer = gem5::LogicalSPDCacheMockPeer;

uint64_t
bits(double value)
{
    uint64_t result = 0;
    static_assert(sizeof(result) == sizeof(value));
    std::memcpy(&result, &value, sizeof(result));
    return result;
}

double
apply(uint8_t operation, double left, double right)
{
    switch (operation) {
      case 0:
        return left + right;
      case 1:
        return left - right;
      case 2:
        return left * right;
      case 3:
        return left / right;
      case 4:
        return std::min(left, right);
      case 5:
        return std::max(left, right);
      default:
        std::abort();
    }
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
        allocation = std::aligned_alloc(Slice::BackingBytes, AllocationBytes);
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

    ~GuardedAlignedSpan()
    {
        std::free(allocation);
    }

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

struct VerticalHarness
{
    static constexpr uint64_t SourceBase = 0x100000;
    static constexpr uint64_t DestinationBase = 0x200000;

    Slice slice;
    Transport transport;
    Peer peer;
    GuardedAlignedSpan source;
    GuardedAlignedSpan destination;
    alignas(64)
        std::array<std::array<double, Slice::PageElements>, Slice::Slots>
            slots{};
    std::array<uint64_t, GuardedAlignedSpan::Elements> sourceBits{};
    std::array<uint8_t, Slice::Pages> fillSlots{};
    std::array<uint8_t, Slice::Pages> computeSourceSlots{};
    std::array<uint8_t, Slice::Pages> computeDestinationSlots{};
    std::array<uint8_t, Slice::Pages> writebackSlots{};
    uint64_t fillResponses = 0;
    uint64_t writeAcks = 0;
    const double scalar = 2.5;

    VerticalHarness()
    {
        for (std::size_t index = 0; index < GuardedAlignedSpan::Elements;
             ++index) {
            source.doubles()[index] =
                1.0 + static_cast<double>(index % 251) / 17.0;
            destination.doubles()[index] = -9000.0;
            sourceBits[index] = bits(source.doubles()[index]);
        }
        const uintptr_t sourceAddress =
            reinterpret_cast<uintptr_t>(source.data());
        const uintptr_t destinationAddress =
            reinterpret_cast<uintptr_t>(destination.data());
        CHECK(sourceAddress + Slice::BackingBytes <= destinationAddress ||
              destinationAddress + Slice::BackingBytes <= sourceAddress);
        CHECK(peer.registerBacking(SourceBase, source.data(),
                                   Slice::BackingBytes));
        CHECK(peer.registerBacking(DestinationBase, destination.data(),
                                   Slice::BackingBytes));
        CHECK(slice.initialize(0) == Slice::Status::Accepted);
        CHECK(slice.registerSource(0, {SourceBase, Slice::BackingBytes}) ==
              Slice::Status::Accepted);
        Slice::Admission admission;
        admission.sourceLogical = 0;
        admission.destinationLogical = 1;
        admission.destination = {DestinationBase, Slice::BackingBytes};
        admission.operation = 0;
        admission.scalarBits = bits(scalar);
        CHECK(slice.admit(admission) == Slice::Status::Accepted);
    }

    Transport::PageSpan slotSpan(uint8_t slot)
    {
        CHECK(slot < slots.size());
        return {reinterpret_cast<std::byte *>(slots[slot].data()),
                Transport::PageBytes};
    }

    void runPageAction(bool delayFinalWrite)
    {
        const Slice::PageAction action = slice.pendingPageAction();
        CHECK(action.valid);
        CHECK(slice.acceptPageAction(action) == Slice::Status::Accepted);
        const auto operation = action.operation == Slice::PageOperation::Fill
                                   ? Transport::Operation::Fill
                                   : Transport::Operation::Writeback;
        CHECK(transport.startAction(operation, action.descriptor,
                                    action.generation, action.page,
                                    action.slot, action.baseAddress,
                                    slotSpan(action.slot)) ==
              Transport::Status::Accepted);
        if (operation == Transport::Operation::Fill)
            fillSlots[action.page] = action.slot;
        else
            writebackSlots[action.page] = action.slot;

        uint8_t delayedRecord = Transport::NoRecord;
        while (transport.actionState() != Transport::ActionState::Free) {
            while (transport.creditsInUse() < Transport::ResponseCredits) {
                const auto sent =
                    peer.send(transport, slotSpan(action.slot), true);
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
                const auto key = transport.recordKey(record);
                if (delayFinalWrite &&
                    operation == Transport::Operation::Writeback &&
                    key.line == Transport::LinesPerPage - 1) {
                    delayedRecord = record;
                    continue;
                }
                const auto response = peer.respond(transport, record, true);
                if (operation == Transport::Operation::Fill) {
                    CHECK(response.status ==
                          Transport::Status::DeliveryPending);
                    ++fillResponses;
                    const auto completion = transport.commitDelivery(
                        response.ticket, slotSpan(action.slot));
                    CHECK(completion == Transport::Status::Accepted ||
                          completion == Transport::Status::Completed);
                } else {
                    CHECK(response.status == Transport::Status::Accepted ||
                          response.status == Transport::Status::Completed);
                    ++writeAcks;
                }
                progressed = true;
            }

            if (delayedRecord != Transport::NoRecord &&
                transport.ackCount() == Transport::LinesPerPage - 1) {
                CHECK(transport.issuedSetComplete());
                CHECK(!transport.ackSetComplete());
                CHECK(!transport.lineAcked(Transport::LinesPerPage - 1));
                CHECK(!slice.cacheController().pageIsReady(
                    action.controller.page));
                CHECK(slice.cacheController().slotPhase(action.slot) ==
                      Slice::Controller::Phase::Writeback);
                CHECK(slice.queueRefill(action.descriptor, action.page) ==
                      Slice::Status::Busy);
                CHECK(transport.startAction(
                          Transport::Operation::Fill, action.descriptor,
                          action.generation, action.page, action.slot,
                          action.baseAddress, slotSpan(action.slot)) ==
                      Transport::Status::Busy);
                CHECK(!slice.operationComplete());
                const auto finalResponse =
                    peer.respond(transport, delayedRecord, false);
                CHECK(finalResponse.status == Transport::Status::Completed);
                ++writeAcks;
                progressed = true;
            }
            CHECK(progressed ||
                  transport.actionState() == Transport::ActionState::Free);
        }
        CHECK(transport.drained());
        CHECK(slice.completePageAction(action) == Slice::Status::Accepted);
        if (operation == Transport::Operation::Writeback) {
            CHECK(slice.cacheController().pageIsReady(action.controller.page));
        }
    }

    void runCompute()
    {
        const Slice::ComputeAction compute = slice.pendingCompute();
        CHECK(compute.valid);
        CHECK(compute.sourceSlot != compute.destinationSlot);
        CHECK(slice.cacheController().slotIsPinned(compute.sourceSlot));
        CHECK(slice.cacheController().slotIsPinned(compute.destinationSlot));
        computeSourceSlots[compute.source.page] = compute.sourceSlot;
        computeDestinationSlots[compute.source.page] = compute.destinationSlot;
        auto stale = compute;
        ++stale.computeSerial;
        CHECK(slice.acceptCompute(stale) == Slice::Status::Stale);
        CHECK(slice.acceptCompute(compute) == Slice::Status::Accepted);
        CHECK(slice.cacheController().slotPhase(compute.destinationSlot) ==
              Slice::Controller::Phase::Computing);
        CHECK(Datapath::transform(
                  static_cast<Datapath::Operation>(compute.operation),
                  {slots[compute.sourceSlot].data(), Slice::PageElements},
                  {slots[compute.destinationSlot].data(), Slice::PageElements},
                  compute.scalarBits) == Datapath::Result::Accepted);
        CHECK(slice.completeCompute(stale) == Slice::Status::Stale);
        CHECK(slice.completeCompute(compute) == Slice::Status::Accepted);
        CHECK(!slice.cacheController().slotIsPinned(compute.sourceSlot));
        CHECK(!slice.cacheController().slotIsPinned(compute.destinationSlot));
        CHECK(slice.cacheController().slotPhase(compute.sourceSlot) ==
              Slice::Controller::Phase::Clean);
        CHECK(slice.cacheController().slotPhase(compute.destinationSlot) ==
              Slice::Controller::Phase::Dirty);
    }

    void runPositiveOperation()
    {
        for (uint8_t page = 0; page < Slice::Pages; ++page) {
            CHECK(slice.stage() == Slice::Stage::WaitingFill);
            runPageAction(false);
            CHECK(slice.stage() == Slice::Stage::ComputeReady);
            runCompute();
            CHECK(slice.stage() == Slice::Stage::WaitingWriteback);
            runPageAction(true);
        }
        CHECK(slice.operationComplete());
        CHECK(slice.descriptorComplete(1));
        CHECK(fillResponses == Slice::Pages * Slice::LinesPerPage);
        CHECK(writeAcks == Slice::Pages * Slice::LinesPerPage);
        CHECK(slice.counters().highLevelCompletions == 1);
        CHECK(slice.counters().pagesCompleted == Slice::Pages);
        CHECK(slice.counters().fillsCompleted == Slice::Pages);
        CHECK(slice.counters().writebacksCompleted == Slice::Pages);
        CHECK(slice.counters().computesStarted == Slice::Pages);
        CHECK(slice.counters().computesCompleted == Slice::Pages);
        CHECK((fillSlots == std::array<uint8_t, Slice::Pages>{0, 1, 0, 1}));
        CHECK((computeSourceSlots ==
               std::array<uint8_t, Slice::Pages>{0, 1, 0, 1}));
        CHECK((computeDestinationSlots ==
               std::array<uint8_t, Slice::Pages>{1, 0, 1, 0}));
        CHECK(computeDestinationSlots == writebackSlots);

        for (std::size_t index = 0; index < GuardedAlignedSpan::Elements;
             ++index) {
            CHECK(bits(source.doubles()[index]) == sourceBits[index]);
            CHECK(bits(destination.doubles()[index]) ==
                  bits(source.doubles()[index] + scalar));
        }
        CHECK(source.guardsExact());
        CHECK(destination.guardsExact());
    }

    void refillDestinationWithoutRepublish()
    {
        CHECK(slice.retireCompletedOperation() == Slice::Status::Accepted);
        const uint64_t highLevelCompletions =
            slice.counters().highLevelCompletions;
        for (uint8_t page = 0; page < Slice::Pages; ++page) {
            CHECK(slice.queueRefill(1, page) == Slice::Status::Accepted);
            const Slice::PageAction refill = slice.pendingPageAction();
            CHECK(refill.valid);
            CHECK(refill.operation == Slice::PageOperation::Fill);
            runPageAction(false);
            CHECK(!slice.refillActive());
            CHECK(std::memcmp(
                      slots[refill.slot].data(),
                      destination.doubles() +
                          static_cast<std::size_t>(page) * Slice::PageElements,
                      Slice::PageBytes) == 0);
        }
        CHECK(slice.counters().highLevelCompletions == highLevelCompletions);
        CHECK(slice.counters().refillCompletions == Slice::Pages);
    }
};

void
testExactFourPageVerticalSliceAndDestinationRefill()
{
    VerticalHarness harness;
    harness.runPositiveOperation();
    harness.refillDestinationWithoutRepublish();
}

void
testDatapathAllOperationsAndAliasing()
{
    const std::array<double, 8> source{
        -4.0, -1.0, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0};
    std::array<double, 8> destination{};
    for (uint8_t operation = 0; operation <= Slice::MaxScalarOperation;
         ++operation) {
        const double scalar = operation == 3 ? 2.0 : 2.5;
        CHECK(Datapath::transform(
                  static_cast<Datapath::Operation>(operation),
                  {source.data(), source.size()},
                  {destination.data(), destination.size()}, bits(scalar)) ==
              Datapath::Result::Accepted);
        for (std::size_t index = 0; index < source.size(); ++index) {
            CHECK(bits(destination[index]) ==
                  bits(apply(operation, source[index], scalar)));
        }
    }
    std::array<double, 8> alias{};
    CHECK(Datapath::transform(Datapath::Operation::Add,
                              {alias.data(), alias.size()},
                              {alias.data(), alias.size()}, bits(1.0)) ==
          Datapath::Result::Aliased);
    CHECK(Datapath::transform(Datapath::Operation::Add,
                              {source.data(), source.size()},
                              {destination.data(), destination.size() - 1},
                              bits(1.0)) == Datapath::Result::Invalid);
}

Slice::Admission
admission(uint64_t destinationBase)
{
    Slice::Admission request;
    request.sourceLogical = 0;
    request.destinationLogical = 1;
    request.destination = {destinationBase, Slice::BackingBytes};
    request.operation = 0;
    request.scalarBits = bits(1.5);
    return request;
}

void
testAdmissionExhaustionResetAndTeardown()
{
    Slice slice;
    CHECK(slice.initialize(0) == Slice::Status::Accepted);
    CHECK(slice.registerSource(0, {0x100000, Slice::BackingBytes}) ==
          Slice::Status::Accepted);
    const auto beforeDestination = slice.descriptor(1);
    auto malformed = admission(0x100000);
    CHECK(slice.admit(malformed) == Slice::Status::Invalid);
    CHECK(slice.descriptor(1).role == beforeDestination.role);
    malformed = admission(0x200000 + Slice::CacheLineBytes);
    CHECK(slice.admit(malformed) == Slice::Status::Invalid);
    malformed = admission(0x200000);
    malformed.operation = Slice::MaxScalarOperation + 1;
    CHECK(slice.admit(malformed) == Slice::Status::Invalid);
    malformed = admission(0x200000);
    malformed.dataType = 4;
    CHECK(slice.admit(malformed) == Slice::Status::Invalid);
    CHECK(!slice.activeOperation());

    Slice operationExhausted;
    CHECK(operationExhausted.initialize(0) == Slice::Status::Accepted);
    CHECK(operationExhausted.registerSource(
              0, {0x100000, Slice::BackingBytes}) == Slice::Status::Accepted);
    operationExhausted.lastOperationID =
        std::numeric_limits<uint32_t>::max();
    CHECK(operationExhausted.admit(admission(0x200000)) ==
          Slice::Status::Exhausted);
    CHECK(operationExhausted.descriptor(1).role ==
          Slice::DescriptorRole::Free);

    Slice serialExhausted;
    CHECK(serialExhausted.initialize(0) == Slice::Status::Accepted);
    CHECK(serialExhausted.registerSource(
              0, {0x100000, Slice::BackingBytes}) == Slice::Status::Accepted);
    serialExhausted.controller.lastMemorySerial =
        std::numeric_limits<uint64_t>::max() -
        Slice::OperationMemorySerials + 1;
    CHECK(serialExhausted.admit(admission(0x200000)) ==
          Slice::Status::Exhausted);
    CHECK(serialExhausted.descriptor(1).role ==
          Slice::DescriptorRole::Free);

    Slice generationExhausted;
    CHECK(generationExhausted.initialize(0) == Slice::Status::Accepted);
    CHECK(generationExhausted.registerSource(
              0, {0x100000, Slice::BackingBytes}) == Slice::Status::Accepted);
    generationExhausted.controller.descriptors[1].generation =
        std::numeric_limits<uint32_t>::max();
    CHECK(generationExhausted.admit(admission(0x200000)) ==
          Slice::Status::Exhausted);
    CHECK(generationExhausted.descriptor(1).role ==
          Slice::DescriptorRole::Free);

    Slice lifecycle;
    CHECK(lifecycle.initialize(3) == Slice::Status::Accepted);
    CHECK(lifecycle.registerSource(0, {0x100000, Slice::BackingBytes}) ==
          Slice::Status::Accepted);
    const uint32_t generation = lifecycle.descriptor(0).handle.generation;
    CHECK(lifecycle.reset() == Slice::Status::Accepted);
    CHECK(lifecycle.registerSource(0, {0x100000, Slice::BackingBytes}) ==
          Slice::Status::Accepted);
    CHECK(lifecycle.descriptor(0).handle.generation == generation + 1);
    CHECK(lifecycle.cleanupDescriptor(0) == Slice::Status::Accepted);
    CHECK(lifecycle.teardown() == Slice::Status::Accepted);
    CHECK(lifecycle.initialize(3) == Slice::Status::Sealed);
}

} // namespace

int
main()
{
    static_assert(Slice::LogicalDescriptors == 2);
    static_assert(Slice::Pages == 4);
    static_assert(Slice::Slots == 2);
    static_assert(Slice::LinesPerPage == 512);
    testExactFourPageVerticalSliceAndDestinationRefill();
    testDatapathAllOperationsAndAliasing();
    testAdmissionExhaustionResetAndTeardown();
    std::cout << "logical_spd_cache_vertical_slice_test: PASS"
              << " host_slice_size=" << sizeof(Slice)
              << " host_transport_size=" << sizeof(Transport)
              << " (host sizeof; not synthesized hardware)" << std::endl;
    return 0;
}
