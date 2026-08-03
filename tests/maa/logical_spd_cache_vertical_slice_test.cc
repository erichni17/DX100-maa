#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <utility>
#include <vector>

#include "mem/MAA/LogicalSPDCacheSlice.hh"

namespace gem5 {

class LogicalSPDCacheSliceTestAccess
{
  public:
    static void setGeneration(LogicalSPDCacheSlice &slice, uint16_t logical,
                              uint32_t generation)
    {
        slice.controller.descriptors[logical].generation = generation;
    }

    static void setMemorySerial(LogicalSPDCacheSlice &slice,
                                uint64_t serial)
    {
        slice.controller.lastMemorySerial = serial;
    }

    static void setOperationID(LogicalSPDCacheSlice &slice, uint64_t id)
    {
        slice.nextOperationID = id;
    }

    static void setProducerTransaction(LogicalSPDCacheSlice &slice,
                                       uint64_t transaction)
    {
        slice.lastProducerTransaction = transaction;
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

using Slice = gem5::LogicalSPDCacheSlice;

uint64_t
bits(double value)
{
    uint64_t result = 0;
    static_assert(sizeof(result) == sizeof(value));
    std::memcpy(&result, &value, sizeof(result));
    return result;
}

double
fromBits(uint64_t value)
{
    double result = 0;
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

Slice::BackingSpan
span(uint64_t base)
{
    return {base, base - 0x1000, base + Slice::LogicalBytes + 0x1000, 1};
}

struct Harness
{
    struct GuardedLogical
    {
        uint64_t before = 0x1020304050607080ULL;
        std::array<double, Slice::PageElements * Slice::Pages> values{};
        uint64_t after = 0x8877665544332211ULL;
    };

    Slice slice;
    GuardedLogical source{};
    GuardedLogical destination{};
    std::array<std::array<double, Slice::PageElements>, Slice::Slots> slots{};
    std::array<uint16_t, Slice::Pages> fillSlots{};
    std::array<uint16_t, Slice::Pages> computeSourceSlots{};
    std::array<uint16_t, Slice::Pages> computeDestinationSlots{};
    std::array<uint16_t, Slice::Pages> writebackSlots{};
    uint8_t operation = 0;
    double scalar = 0;
    uint64_t sourceBase = 0x100000;
    uint64_t destinationBase = 0x200000;
    bool injectedBadResponses = false;

    Harness(uint8_t op, double scalarValue)
        : operation(op), scalar(scalarValue)
    {
        slice.initialize(1);
        for (std::size_t index = 0; index < source.values.size(); ++index) {
            source.values[index] =
                1.0 + static_cast<double>(index % 251) / 17.0;
            destination.values[index] = -9000.0;
        }
        CHECK(slice.registerSource(0, span(sourceBase),
                                   Slice::Float64DataType) ==
              Slice::RegisterResult::Accepted);
        CHECK(slice.descriptor(0).producerTransaction != 0);
        Slice::Admission admission;
        admission.sourceLogical = 0;
        admission.destinationLogical = 1;
        admission.destination = span(destinationBase);
        admission.operation = operation;
        admission.scalarBits = bits(scalar);
        CHECK(slice.admit(admission) == Slice::AdmitResult::Accepted);
    }

    void copyFillLine(const Slice::PendingMemoryAction &action,
                      std::size_t line)
    {
        const std::size_t page = action.controller.page.page;
        const std::size_t first = line *
            (Slice::CacheLineBytes / sizeof(double));
        std::copy_n(source.values.begin() +
                        page * Slice::PageElements + first,
                    Slice::CacheLineBytes / sizeof(double),
                    slots[action.controller.slot].begin() + first);
    }

    void copyWriteLine(const Slice::PendingMemoryAction &action,
                       std::size_t line)
    {
        const std::size_t page = action.controller.page.page;
        const std::size_t first = line *
            (Slice::CacheLineBytes / sizeof(double));
        std::copy_n(slots[action.controller.slot].begin() + first,
                    Slice::CacheLineBytes / sizeof(double),
                    destination.values.begin() +
                        page * Slice::PageElements + first);
    }

    void injectWrongResponses(const Slice::PendingMemoryAction &action,
                              uint64_t address,
                              gem5::LogicalStreamResponseKind kind)
    {
        if (injectedBadResponses)
            return;
        injectedBadResponses = true;
        const uint64_t before = slice.counters().lineResponses;
        auto wrong = action.tag;
        ++wrong.maaID;
        CHECK(slice.response(wrong, address, kind) ==
              gem5::LogicalStreamResponseResult::WrongMAA);
        wrong = action.tag;
        ++wrong.transactionID;
        CHECK(slice.response(wrong, address, kind) ==
              gem5::LogicalStreamResponseResult::WrongTransaction);
        wrong = action.tag;
        wrong.action = gem5::LogicalStreamAction::Writeback;
        CHECK(slice.response(wrong, address, kind) ==
              gem5::LogicalStreamResponseResult::WrongKind);
        wrong = action.tag;
        ++wrong.logicalID;
        CHECK(slice.response(wrong, address, kind) ==
              gem5::LogicalStreamResponseResult::WrongPage);
        wrong = action.tag;
        ++wrong.page;
        CHECK(slice.response(wrong, address, kind) ==
              gem5::LogicalStreamResponseResult::WrongPage);
        wrong = action.tag;
        ++wrong.generation;
        CHECK(slice.response(wrong, address, kind) ==
              gem5::LogicalStreamResponseResult::WrongPage);
        wrong = action.tag;
        ++wrong.slot;
        CHECK(slice.response(wrong, address, kind) ==
              gem5::LogicalStreamResponseResult::WrongSlot);
        CHECK(slice.response(action.tag,
                             address + Slice::LogicalBytes, kind) ==
              gem5::LogicalStreamResponseResult::WrongAddress);
        const auto wrongKind =
            kind == gem5::LogicalStreamResponseKind::Read
                ? gem5::LogicalStreamResponseKind::Write
                : gem5::LogicalStreamResponseKind::Read;
        CHECK(slice.response(action.tag, address, wrongKind) ==
              gem5::LogicalStreamResponseResult::WrongKind);
        CHECK(slice.counters().lineResponses == before);
    }

    void runMemoryAction(bool delayLastWriteResponse)
    {
        const Slice::PendingMemoryAction advertised =
            slice.pendingMemoryAction();
        CHECK(advertised.valid);
        CHECK(advertised.controller ==
              slice.pendingMemoryAction().controller);
        auto forged = advertised;
        ++forged.controller.serial;
        CHECK(slice.acceptMemoryAction(forged) ==
              Slice::Controller::ActionResult::Stale);
        CHECK(slice.pendingMemoryAction().controller == advertised.controller);
        CHECK(slice.acceptMemoryAction(advertised) ==
              Slice::Controller::ActionResult::Accepted);

        const bool fill = advertised.controller.kind ==
            Slice::Controller::ActionKind::Fill;
        const auto responseKind =
            fill ? gem5::LogicalStreamResponseKind::Read
                 : gem5::LogicalStreamResponseKind::Write;
        const std::size_t page = advertised.controller.page.page;
        if (fill)
            fillSlots[page] = advertised.controller.slot;
        else
            writebackSlots[page] = advertised.controller.slot;

        struct Issued
        {
            std::size_t line;
            uint64_t address;
        };
        std::vector<Issued> held;
        while (true) {
            std::vector<Issued> window;
            while (slice.canIssueLine()) {
                const auto line = slice.pendingLine();
                CHECK(line.valid);
                const uint64_t physicalAddress = line.virtualAddress;
                CHECK(slice.issueLine(line.index, physicalAddress,
                                      line.kind) ==
                      gem5::LogicalStreamResponseResult::Accepted);
                if (!fill)
                    copyWriteLine(advertised, line.index);
                window.push_back({line.index, physicalAddress});
            }
            CHECK(window.size() <= Slice::LineWindow);
            if (window.empty())
                break;
            CHECK(!slice.canIssueLine());
            for (auto it = window.rbegin(); it != window.rend(); ++it) {
                if (delayLastWriteResponse && !fill &&
                    it->line == Slice::LinesPerPage - 1) {
                    held.push_back(*it);
                    continue;
                }
                injectWrongResponses(advertised, it->address, responseKind);
                if (fill)
                    copyFillLine(advertised, it->line);
                const auto result = slice.response(
                    advertised.tag, it->address, responseKind);
                CHECK(result == gem5::LogicalStreamResponseResult::Accepted ||
                      result == gem5::LogicalStreamResponseResult::Completed);
                if (it->line == 0) {
                    CHECK(slice.response(
                              advertised.tag, it->address, responseKind) ==
                          gem5::LogicalStreamResponseResult::Duplicate);
                }
            }
        }

        if (!held.empty()) {
            CHECK(held.size() == 1);
            const auto identity = advertised.controller.page;
            CHECK(!slice.cacheController().pageIsReady(identity));
            CHECK(slice.cacheController().slotPhase(
                      advertised.controller.slot) ==
                  Slice::Controller::Phase::Writeback);
            CHECK(!slice.pendingMemoryAction().valid);
            CHECK(slice.currentPage() == page);
            CHECK(slice.response(advertised.tag, held[0].address,
                                 gem5::LogicalStreamResponseKind::Write) ==
                  gem5::LogicalStreamResponseResult::Completed);
        }

        CHECK(slice.response(
                  advertised.tag, advertised.backingBase, responseKind) ==
              gem5::LogicalStreamResponseResult::Stale);
    }

    void runCompute()
    {
        const auto compute = slice.pendingCompute();
        CHECK(compute.valid);
        const std::size_t page = compute.source.page;
        computeSourceSlots[page] = compute.sourceSlot;
        computeDestinationSlots[page] = compute.destinationSlot;
        CHECK(compute.sourceSlot != compute.destinationSlot);
        auto forged = compute;
        ++forged.transactionID;
        CHECK(slice.acceptCompute(forged) == Slice::ComputeResult::Stale);
        CHECK(slice.acceptCompute(compute) == Slice::ComputeResult::Accepted);
        CHECK(slice.acceptCompute(compute) == Slice::ComputeResult::Stale);
        for (std::size_t index = 0; index < Slice::PageElements; ++index) {
            slots[compute.destinationSlot][index] = apply(
                compute.operation, slots[compute.sourceSlot][index],
                fromBits(compute.scalarBits));
        }
        CHECK(slice.completeCompute(forged) == Slice::ComputeResult::Stale);
        CHECK(slice.completeCompute(compute) ==
              Slice::ComputeResult::Accepted);
        CHECK(slice.completeCompute(compute) == Slice::ComputeResult::Stale);
    }

    void run()
    {
        for (std::size_t page = 0; page < Slice::Pages; ++page) {
            CHECK(slice.stage() == Slice::Stage::WaitingFill);
            runMemoryAction(false);
            CHECK(slice.stage() == Slice::Stage::ComputeReady);
            runCompute();
            CHECK(slice.stage() == Slice::Stage::WaitingWriteback);
            runMemoryAction(true);
            CHECK(slice.cacheController().pageIsReady(
                slice.cacheController().identity(
                    slice.descriptor(1).handle, page)));
        }
        CHECK(slice.operationComplete());
        for (std::size_t index = 0; index < source.values.size(); ++index)
            CHECK(bits(destination.values[index]) ==
                  bits(apply(operation, source.values[index], scalar)));
        CHECK(source.before == 0x1020304050607080ULL);
        CHECK(source.after == 0x8877665544332211ULL);
        CHECK(destination.before == 0x1020304050607080ULL);
        CHECK(destination.after == 0x8877665544332211ULL);
        CHECK((fillSlots == std::array<uint16_t, Slice::Pages>{0, 1, 0, 1}));
        CHECK((computeSourceSlots ==
               std::array<uint16_t, Slice::Pages>{0, 1, 0, 1}));
        CHECK((computeDestinationSlots ==
               std::array<uint16_t, Slice::Pages>{1, 0, 1, 0}));
        CHECK(computeDestinationSlots == writebackSlots);
        CHECK(slice.counters().fillsAccepted == Slice::Pages);
        CHECK(slice.counters().writebacksAccepted == Slice::Pages);
        CHECK(slice.counters().computeIssues == Slice::Pages);
        CHECK(slice.counters().computeCompletions == Slice::Pages);
        CHECK(slice.counters().pagesCompleted == Slice::Pages);
        CHECK(slice.counters().lineWindowHighWater == Slice::LineWindow);
        CHECK(slice.counters().lineIssues ==
              2 * Slice::Pages * Slice::LinesPerPage);
        CHECK(slice.counters().lineResponses ==
              2 * Slice::Pages * Slice::LinesPerPage);
    }
};

void
testFourPagesAllOperationsAndExactFP64()
{
    for (uint8_t operation = 0; operation <= Slice::MaxScalarOperation;
         ++operation) {
        const double scalar = operation == 3 ? 3.25 : 2.5;
        Harness harness(operation, scalar);
        harness.run();
        CHECK(harness.slice.retireCompletedOperation());
        CHECK(!harness.slice.retireCompletedOperation());
        CHECK(harness.slice.counters().operationsCompleted == 1);
    }
}

void
testAdmissionAtomicityAndExhaustion()
{
    Slice slice;
    slice.initialize(0);
    CHECK(slice.registerSource(0, span(0x100000), Slice::Float64DataType) ==
          Slice::RegisterResult::Accepted);
    Slice::Admission request;
    request.sourceLogical = 0;
    request.destinationLogical = 1;
    request.destination = span(0x200000);
    request.operation = 0;
    request.scalarBits = bits(1.5);

    auto malformed = request;
    ++malformed.destination.base;
    CHECK(slice.admit(malformed) == Slice::AdmitResult::Invalid);
    malformed = request;
    malformed.destination = span(0x100000 + Slice::CacheLineBytes);
    CHECK(slice.admit(malformed) == Slice::AdmitResult::Overlap);
    malformed = request;
    malformed.destinationLogical = 0;
    CHECK(slice.admit(malformed) == Slice::AdmitResult::Invalid);
    malformed = request;
    malformed.operation = Slice::MaxScalarOperation + 1;
    CHECK(slice.admit(malformed) == Slice::AdmitResult::Invalid);
    malformed = request;
    malformed.dataType = 4;
    CHECK(slice.admit(malformed) == Slice::AdmitResult::Invalid);
    malformed = request;
    malformed.destination.rangeEnd = malformed.destination.base +
                                     Slice::LogicalBytes - 1;
    CHECK(slice.admit(malformed) == Slice::AdmitResult::Invalid);
    CHECK(!slice.activeOperation());
    CHECK(slice.operationID() == 0);
    CHECK(slice.descriptor(1).role == Slice::DescriptorRole::Free);

    Slice producerSerialExhausted;
    producerSerialExhausted.initialize(0);
    gem5::LogicalSPDCacheSliceTestAccess::setProducerTransaction(
        producerSerialExhausted, std::numeric_limits<uint64_t>::max());
    CHECK(producerSerialExhausted.registerSource(
              0, span(0x100000), Slice::Float64DataType) ==
          Slice::RegisterResult::SerialExhausted);
    CHECK(producerSerialExhausted.descriptor(0).role ==
          Slice::DescriptorRole::Free);

    Slice operationExhausted;
    operationExhausted.initialize(0);
    CHECK(operationExhausted.registerSource(
              0, span(0x100000), Slice::Float64DataType) ==
          Slice::RegisterResult::Accepted);
    gem5::LogicalSPDCacheSliceTestAccess::setOperationID(
        operationExhausted, std::numeric_limits<uint64_t>::max());
    CHECK(operationExhausted.admit(request) ==
          Slice::AdmitResult::SerialExhausted);
    CHECK(operationExhausted.descriptor(1).role ==
          Slice::DescriptorRole::Free);

    Slice serialExhausted;
    serialExhausted.initialize(0);
    CHECK(serialExhausted.registerSource(
              0, span(0x100000), Slice::Float64DataType) ==
          Slice::RegisterResult::Accepted);
    gem5::LogicalSPDCacheSliceTestAccess::setMemorySerial(
        serialExhausted,
        std::numeric_limits<uint64_t>::max() -
            Slice::OperationMemorySerials + 1);
    CHECK(serialExhausted.admit(request) ==
          Slice::AdmitResult::SerialExhausted);
    CHECK(serialExhausted.descriptor(1).role ==
          Slice::DescriptorRole::Free);

    Slice sourceGenerationExhausted;
    sourceGenerationExhausted.initialize(0);
    gem5::LogicalSPDCacheSliceTestAccess::setGeneration(
        sourceGenerationExhausted, 0,
        std::numeric_limits<uint32_t>::max());
    CHECK(sourceGenerationExhausted.registerSource(
              0, span(0x100000), Slice::Float64DataType) ==
          Slice::RegisterResult::GenerationExhausted);

    Slice destinationGenerationExhausted;
    destinationGenerationExhausted.initialize(0);
    CHECK(destinationGenerationExhausted.registerSource(
              0, span(0x100000), Slice::Float64DataType) ==
          Slice::RegisterResult::Accepted);
    gem5::LogicalSPDCacheSliceTestAccess::setGeneration(
        destinationGenerationExhausted, 1,
        std::numeric_limits<uint32_t>::max());
    CHECK(destinationGenerationExhausted.admit(request) ==
          Slice::AdmitResult::GenerationExhausted);
    CHECK(destinationGenerationExhausted.descriptor(1).role ==
          Slice::DescriptorRole::Free);
}

void
testDrainCleanupAndCounterBoundaries()
{
    Harness harness(0, 4.0);
    harness.slice.noteActionBackpressure();
    CHECK(harness.slice.counters().actionBackpressure == 1);
    harness.slice.requestDrain();
    Slice::Admission second;
    second.sourceLogical = 0;
    second.destinationLogical = 1;
    second.destination = span(0x300000);
    CHECK(harness.slice.admit(second) == Slice::AdmitResult::Draining);
    CHECK(!harness.slice.drained());
    CHECK(harness.slice.cleanupDescriptor(0) == Slice::CleanupResult::Busy);
    harness.run();
    CHECK(!harness.slice.drained());
    CHECK(harness.slice.retireCompletedOperation());
    CHECK(harness.slice.drained());
    CHECK(harness.slice.cleanupDescriptor(0) ==
          Slice::CleanupResult::Accepted);
    CHECK(harness.slice.cleanupDescriptor(1) ==
          Slice::CleanupResult::Accepted);
    CHECK(harness.slice.cleanupDescriptor(1) == Slice::CleanupResult::Stale);

    const auto underflow = gem5::decideLogicalStreamCounterUpdate(
        gem5::LogicalStreamResponseKind::Write,
        gem5::LogicalStreamCounterEvent::ResponseAccepted, 0);
    CHECK(!underflow.valid);
    CHECK(!underflow.changed);
    CHECK(underflow.value == 0);
    const auto overflow = gem5::decideLogicalStreamCounterUpdate(
        gem5::LogicalStreamResponseKind::Read,
        gem5::LogicalStreamCounterEvent::Enqueued,
        std::numeric_limits<uint32_t>::max());
    CHECK(!overflow.valid);
    CHECK(!overflow.changed);
}

} // namespace

int
main()
{
    static_assert(Slice::LogicalDescriptors == 2);
    static_assert(Slice::Pages == 4);
    static_assert(Slice::Slots == 2);
    static_assert(Slice::LineWindow == 8);
    testFourPagesAllOperationsAndExactFP64();
    testAdmissionAtomicityAndExhaustion();
    testDrainCleanupAndCounterBoundaries();
    std::cout << "logical_spd_cache_vertical_slice_test: PASS"
              << " slice_bytes=" << sizeof(Slice)
              << " controller_bytes=" << sizeof(Slice::Controller)
              << std::endl;
    return 0;
}
