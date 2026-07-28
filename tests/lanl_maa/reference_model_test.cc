#include <cassert>
#include <cstdint>
#include <cstring>
#include <limits>
#include <vector>

#include "mem/LANLMAA/ReferenceModel.hh"

using namespace gem5::lanlmaa;

namespace
{

std::vector<uint8_t>
lineWithValues(uint64_t first, uint64_t second)
{
    std::vector<uint8_t> line(64, 0);
    std::memcpy(line.data(), &first, sizeof(first));
    std::memcpy(line.data() + sizeof(first), &second, sizeof(second));
    return line;
}

template <class T>
uint64_t
bitsOf(T value)
{
    static_assert(sizeof(T) == sizeof(uint64_t));
    uint64_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}

template <class T>
T
fromBits(uint64_t bits)
{
    static_assert(sizeof(T) == sizeof(uint64_t));
    T value;
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}

void
testLineMergeAndOrderedRetirement()
{
    Configuration configuration;
    configuration.operationEntries = 4;
    configuration.lineEntries = 2;
    configuration.continuationContexts = 1;
    configuration.combinerEntries = 2;
    configuration.acknowledgementCredits = 1;
    ReadContinuationModel model(configuration);

    assert(model.admitRead(1, 0, 8) == Admission::Accepted);
    assert(model.admitRead(2, 8, 8) == Admission::Accepted);
    assert(model.admitRead(3, 64, 8) == Admission::Accepted);

    auto first = model.nextLineRequest();
    assert(first && first->lineAddress == 0);
    assert(first->requestedByteMask == 0xffff);
    assert(model.returnLine(0, lineWithValues(11, 22)));

    auto completion = model.popRetired();
    assert(completion && completion->logicalTag == 1);
    assert(completion->value == 11);
    completion = model.popRetired();
    assert(completion && completion->logicalTag == 2);
    assert(completion->value == 22);

    auto second = model.nextLineRequest();
    assert(second && second->lineAddress == 64);
    assert(model.returnLine(64, lineWithValues(33, 0)));
    completion = model.popRetired();
    assert(completion && completion->logicalTag == 3);
    assert(completion->value == 33);

    const auto &counters = model.counters();
    assert(counters.logicalItemsAdmitted == 3);
    assert(counters.logicalMemoryAccesses == 3);
    assert(counters.physicalLineReads == 2);
    assert(counters.lineMergeHits == 1);
    assert(counters.responsesFannedOut == 3);
    assert(counters.completionsRetired == 3);
}

void
testBackpressureDoesNotDropWork()
{
    Configuration configuration;
    configuration.operationEntries = 2;
    configuration.lineEntries = 1;
    configuration.continuationContexts = 1;
    configuration.combinerEntries = 1;
    configuration.acknowledgementCredits = 1;
    ReadContinuationModel model(configuration);

    assert(model.admitRead(1, 0, 8) == Admission::Accepted);
    assert(model.admitRead(2, 64, 8) == Admission::WouldBlock);
    assert(model.outstandingOperations() == 1);
    auto request = model.nextLineRequest();
    assert(request && request->lineAddress == 0);
    assert(model.returnLine(0, lineWithValues(7, 0)));
    assert(model.popRetired()->logicalTag == 1);

    assert(model.admitRead(2, 64, 8) == Admission::Accepted);
    request = model.nextLineRequest();
    assert(request && request->lineAddress == 64);
    assert(model.returnLine(64, lineWithValues(9, 0)));
    assert(model.popRetired()->value == 9);
    assert(model.counters().lineWouldBlock == 1);
    assert(model.counters().logicalItemsAdmitted == 2);
}

void
testExplicitContinuationAndContextPressure()
{
    Configuration configuration;
    configuration.operationEntries = 3;
    configuration.lineEntries = 2;
    configuration.continuationContexts = 1;
    configuration.combinerEntries = 1;
    configuration.acknowledgementCredits = 1;
    ReadContinuationModel model(configuration);

    assert(model.admitRead(10, 0, 8, true) == Admission::Accepted);
    assert(model.admitRead(11, 64, 8, true) == Admission::WouldBlock);
    auto request = model.nextLineRequest();
    assert(request && request->lineAddress == 0);
    assert(model.returnLine(0, lineWithValues(64, 0)));
    assert(model.continuationValue(10) == 64);

    assert(model.reissueContinuation(10, 64, 8) == Admission::Accepted);
    request = model.nextLineRequest();
    assert(request && request->lineAddress == 64);
    assert(model.returnLine(64, lineWithValues(1234, 0)));
    assert(model.continuationValue(10) == 1234);
    assert(model.finishContinuation(10));
    auto completion = model.popRetired();
    assert(completion && completion->logicalTag == 10);
    assert(completion->value == 1234);
    assert(model.outstandingContexts() == 0);

    assert(model.admitRead(11, 64, 8, true) == Admission::Accepted);
    assert(model.counters().contextWouldBlock == 1);
    assert(model.counters().continuationSteps == 1);
    assert(model.counters().activeContextHighWater == 1);
}

void
testInvalidReadBoundariesFailClosed()
{
    ReadContinuationModel model;
    assert(model.admitRead(1, 60, 8) == Admission::Invalid);
    assert(model.admitRead(1, 0, 3) == Admission::Invalid);
    assert(model.outstandingOperations() == 0);
    assert(!model.nextLineRequest());
    assert(model.counters().invalidAdmissions == 2);
}

void
testRelaxedCombineAndExplicitAcknowledgement()
{
    Configuration configuration;
    configuration.operationEntries = 4;
    configuration.lineEntries = 2;
    configuration.continuationContexts = 1;
    configuration.combinerEntries = 2;
    configuration.acknowledgementCredits = 1;
    UpdateCombinerModel model(configuration);

    assert(model.admitUpdate(
               1, 0x100, 4, DataType::Uint64, UpdateOperation::Add,
               Ordering::Relaxed) == Admission::Accepted);
    assert(model.admitUpdate(
               2, 0x100, 5, DataType::Uint64, UpdateOperation::Add,
               Ordering::Relaxed) == Admission::Accepted);
    assert(model.admitUpdate(
               3, 0x100, 7, DataType::Uint64, UpdateOperation::Add,
               Ordering::Strict) == Admission::Accepted);

    auto first = model.drainNext();
    assert(first && first->valueBits == 9 && first->participants == 2);
    assert(!model.drainNext());
    assert(model.acknowledge(first->drainId));

    auto second = model.drainNext();
    assert(second && second->valueBits == 7 && second->participants == 1);
    assert(model.acknowledge(second->drainId));
    assert(model.outstandingEntries() == 0);

    const auto &counters = model.counters();
    assert(counters.logicalUpdatesAdmitted == 3);
    assert(counters.logicalUpdatesCompleted == 3);
    assert(counters.updateConflicts == 2);
    assert(counters.combinerHits == 1);
    assert(counters.strictOrderSerializations == 1);
    assert(counters.acknowledgementWouldBlock == 1);
    assert(counters.acknowledgements == 2);
}

void
testDataTypesMinMaxAndOverflow()
{
    Configuration configuration;
    configuration.operationEntries = 4;
    configuration.lineEntries = 2;
    configuration.continuationContexts = 1;
    configuration.combinerEntries = 4;
    configuration.acknowledgementCredits = 2;
    UpdateCombinerModel model(configuration);

    assert(model.admitUpdate(
               1, 0x100, bitsOf<int64_t>(-2), DataType::Int64,
               UpdateOperation::Min, Ordering::Relaxed) ==
           Admission::Accepted);
    assert(model.admitUpdate(
               2, 0x100, bitsOf<int64_t>(-5), DataType::Int64,
               UpdateOperation::Min, Ordering::Relaxed) ==
           Admission::Accepted);
    auto minimum = model.drainNext();
    assert(minimum && fromBits<int64_t>(minimum->valueBits) == -5);
    assert(model.acknowledge(minimum->drainId));

    assert(model.admitUpdate(
               3, 0x200, bitsOf<double>(2.5), DataType::Float64,
               UpdateOperation::Max, Ordering::Relaxed) ==
           Admission::Accepted);
    assert(model.admitUpdate(
               4, 0x200, bitsOf<double>(4.5), DataType::Float64,
               UpdateOperation::Max, Ordering::Relaxed) ==
           Admission::Accepted);
    auto maximum = model.drainNext();
    assert(maximum && fromBits<double>(maximum->valueBits) == 4.5);
    assert(model.acknowledge(maximum->drainId));

    assert(model.admitUpdate(
               5, 0x300, std::numeric_limits<uint64_t>::max(),
               DataType::Uint64, UpdateOperation::Add, Ordering::Relaxed,
               OverflowPolicy::Fault) == Admission::Accepted);
    assert(model.admitUpdate(
               6, 0x300, 1, DataType::Uint64, UpdateOperation::Add,
               Ordering::Relaxed, OverflowPolicy::Fault) ==
           Admission::Invalid);
    auto unchanged = model.drainNext();
    assert(unchanged &&
           unchanged->valueBits == std::numeric_limits<uint64_t>::max());
}

void
testInvalidUpdateEnumsAndOverwriteOrderingFailClosed()
{
    UpdateCombinerModel model;
    assert(model.admitUpdate(
               1, 0x100, 1, static_cast<DataType>(99),
               UpdateOperation::Add, Ordering::Strict) == Admission::Invalid);
    assert(model.admitUpdate(
               1, 0x100, 1, DataType::Uint64,
               static_cast<UpdateOperation>(99), Ordering::Strict) ==
           Admission::Invalid);
    assert(model.admitUpdate(
               1, 0x100, 1, DataType::Uint64,
               UpdateOperation::Overwrite, Ordering::Relaxed) ==
           Admission::Invalid);
    assert(model.outstandingEntries() == 0);
    assert(!model.drainNext());
    assert(model.counters().invalidAdmissions == 3);
}

} // anonymous namespace

int
main()
{
    testLineMergeAndOrderedRetirement();
    testBackpressureDoesNotDropWork();
    testExplicitContinuationAndContextPressure();
    testInvalidReadBoundariesFailClosed();
    testRelaxedCombineAndExplicitAcknowledgement();
    testDataTypesMinMaxAndOverflow();
    testInvalidUpdateEnumsAndOverwriteOrderingFailClosed();
    return 0;
}
