#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>

#include "mem/MAA/HybridConsumerPipeline.hh"

using gem5::HybridConsumerPipeline;

namespace {

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;         \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

HybridConsumerPipeline::Descriptor
validDescriptor(uint8_t wordBytes = 8)
{
    HybridConsumerPipeline::Descriptor descriptor;
    descriptor.generation = 7;
    descriptor.logicalElements = HybridConsumerPipeline::LogicalElements;
    descriptor.wordBytes = wordBytes;
    descriptor.backingAddress = 0x100000;
    descriptor.backingRangeMin = 0x100000;
    descriptor.backingRangeMax = 0x140000;
    descriptor.backingRangeID = 3;
    descriptor.destinationAddress = 0x200000;
    descriptor.destinationRangeMin = 0x200000;
    descriptor.destinationRangeMax = 0x240000;
    descriptor.destinationRangeID = 4;
    descriptor.producerTransactions = {{101, 102, 103, 104}};
    return descriptor;
}

HybridConsumerPipeline::ProducerAck
ackFor(const HybridConsumerPipeline::Descriptor &descriptor, uint8_t page)
{
    return {descriptor.generation, page,
            descriptor.producerTransactions[page]};
}

void
ackAll(HybridConsumerPipeline &pipeline,
       const HybridConsumerPipeline::Descriptor &descriptor)
{
    for (uint8_t page = 0; page < HybridConsumerPipeline::ProducerPages;
         ++page)
        CHECK(pipeline.notifyProducerWriteAck(ackFor(descriptor, page)));
}

std::array<std::byte, HybridConsumerPipeline::LineBytes>
payload(uint8_t seed)
{
    std::array<std::byte, HybridConsumerPipeline::LineBytes> value{};
    for (std::size_t index = 0; index < value.size(); ++index)
        value[index] = std::byte(seed + static_cast<uint8_t>(index));
    return value;
}

void
driveLine(HybridConsumerPipeline &pipeline, uint16_t expectedLine)
{
    const auto read = pipeline.pendingRead();
    CHECK(read.kind == HybridConsumerPipeline::Kind::ReadBacking);
    CHECK(read.line == expectedLine);
    CHECK(read.size == HybridConsumerPipeline::LineBytes);
    CHECK(read.port == HybridConsumerPipeline::portForAddress(read.address));
    CHECK(pipeline.accept(read));
    const auto data = payload(static_cast<uint8_t>(expectedLine));
    CHECK(pipeline.completeRead(read, data.data(), data.size()));

    const auto compute = pipeline.pendingCompute();
    CHECK(compute.kind == HybridConsumerPipeline::Kind::Compute);
    CHECK(compute.line == expectedLine);
    CHECK(pipeline.accept(compute));
    pipeline.bufferData(compute.buffer)[0] = std::byte{0xa5};
    CHECK(pipeline.completeCompute(compute));

    const auto write = pipeline.pendingWrite();
    CHECK(write.kind == HybridConsumerPipeline::Kind::WriteDestination);
    CHECK(write.line == expectedLine);
    CHECK(write.buffer == compute.buffer);
    CHECK(pipeline.bufferData(write.buffer)[0] == std::byte{0xa5});
    CHECK(pipeline.accept(write));
    CHECK(!pipeline.completeWriteAck(compute));
    CHECK(pipeline.completeWriteAck(write));
}

void
testFiniteFourCreditOverlap()
{
    HybridConsumerPipeline pipeline;
    const auto descriptor = validDescriptor();
    CHECK(pipeline.submit(descriptor) ==
          HybridConsumerPipeline::SubmitResult::Accepted);
    CHECK(pipeline.getState() ==
          HybridConsumerPipeline::State::WaitingForProducer);
    CHECK(pipeline.pendingRead().kind == HybridConsumerPipeline::Kind::None);

    CHECK(pipeline.notifyProducerWriteAck(ackFor(descriptor, 0)));
    std::array<HybridConsumerPipeline::Request,
               HybridConsumerPipeline::LineBufferCount>
        reads{};
    const auto lineData = payload(9);
    for (uint8_t index = 0;
         index < HybridConsumerPipeline::LineBufferCount; ++index) {
        reads[index] = pipeline.pendingRead();
        CHECK(reads[index].line == index);
        CHECK(reads[index].buffer == index);
        CHECK(pipeline.accept(reads[index]));
    }
    CHECK(pipeline.pendingRead().kind == HybridConsumerPipeline::Kind::None);
    CHECK(pipeline.creditsInUse() ==
          HybridConsumerPipeline::LineBufferCount);
    CHECK(pipeline.creditHighWater() ==
          HybridConsumerPipeline::LineBufferCount);

    for (const auto &read : reads)
        CHECK(pipeline.completeRead(read, lineData.data(), lineData.size()));

    auto compute0 = pipeline.pendingCompute();
    CHECK(compute0.line == 0 && pipeline.accept(compute0));
    CHECK(pipeline.pendingCompute().kind ==
          HybridConsumerPipeline::Kind::None);
    CHECK(pipeline.completeCompute(compute0));
    auto write0 = pipeline.pendingWrite();
    CHECK(write0.line == 0 && pipeline.accept(write0));

    // A distinct buffer can use the single ALU while the write is in flight.
    auto compute1 = pipeline.pendingCompute();
    CHECK(compute1.line == 1 && pipeline.accept(compute1));
    CHECK(pipeline.completeCompute(compute1));
    CHECK(pipeline.completeWriteAck(write0));

    // Releasing the acknowledged write, rather than ALU completion, returns
    // the exact 64-byte credit to the next backing read.
    auto read4 = pipeline.pendingRead();
    CHECK(read4.line == 4 && read4.buffer == write0.buffer);
    CHECK(pipeline.accept(read4));
    CHECK(pipeline.assertInvariants());
}

void
testLateBoundProducerWriteRespIdentity()
{
    HybridConsumerPipeline pipeline;
    auto descriptor = validDescriptor();
    descriptor.producerTransactions.fill(0);
    CHECK(HybridConsumerPipeline::validate(descriptor) == nullptr);
    CHECK(pipeline.submit(descriptor) ==
          HybridConsumerPipeline::SubmitResult::Accepted);

    const HybridConsumerPipeline::ProducerAck first{
        descriptor.generation, 0, 0x8a5};
    CHECK(!pipeline.notifyProducerWriteAck(
        {descriptor.generation, 0, 0}));
    CHECK(pipeline.notifyProducerWriteAck(first));
    CHECK(!pipeline.notifyProducerWriteAck(
        {descriptor.generation, 0, first.transactionID + 1}));
    CHECK(pipeline.producerPageAcked(0));
    CHECK(!pipeline.producerPageAcked(1));
    const auto read = pipeline.pendingRead();
    CHECK(read.kind == HybridConsumerPipeline::Kind::ReadBacking);
    CHECK(read.line == 0);
}

void
testRetrySurvivesSchedulingPreferenceChange()
{
    HybridConsumerPipeline pipeline;
    const auto descriptor = validDescriptor();
    CHECK(pipeline.submit(descriptor) ==
          HybridConsumerPipeline::SubmitResult::Accepted);
    CHECK(pipeline.notifyProducerWriteAck(ackFor(descriptor, 0)));

    const auto data = payload(3);
    const auto read0 = pipeline.pendingRead();
    CHECK(pipeline.accept(read0));
    CHECK(pipeline.completeRead(read0, data.data(), data.size()));
    const auto compute0 = pipeline.pendingCompute();
    CHECK(pipeline.accept(compute0));
    CHECK(pipeline.completeCompute(compute0));
    const auto write0 = pipeline.pendingWrite();
    CHECK(pipeline.accept(write0));

    const auto read1 = pipeline.pendingRead();
    CHECK(read1.buffer == 1 && pipeline.accept(read1));
    const auto read2 = pipeline.pendingRead();
    CHECK(read2.buffer == 2 && pipeline.accept(read2));

    // This models a packet held for a cache-port retry. A different response
    // then frees buffer zero and changes pendingRead()'s preferred buffer.
    const auto retry = pipeline.pendingRead();
    CHECK(retry.buffer == 3);
    CHECK(pipeline.completeWriteAck(write0));
    CHECK(pipeline.pendingRead().buffer == 0);
    CHECK(pipeline.accept(retry));
    CHECK(pipeline.assertInvariants());
}

void
testNoSyntheticVisibilityOrAcknowledgement()
{
    HybridConsumerPipeline pipeline;
    const auto descriptor = validDescriptor();
    CHECK(pipeline.submit(descriptor) ==
          HybridConsumerPipeline::SubmitResult::Accepted);

    auto wrong = ackFor(descriptor, 0);
    wrong.transactionID++;
    CHECK(!pipeline.notifyProducerWriteAck(wrong));
    CHECK(!pipeline.producerPageAcked(0));
    CHECK(pipeline.pendingRead().kind == HybridConsumerPipeline::Kind::None);
    CHECK(pipeline.notifyProducerWriteAck(ackFor(descriptor, 0)));
    CHECK(!pipeline.notifyProducerWriteAck(ackFor(descriptor, 0)));

    const auto read = pipeline.pendingRead();
    CHECK(pipeline.accept(read));
    const auto data = payload(1);
    auto stale = read;
    stale.transactionID++;
    CHECK(!pipeline.completeRead(stale, data.data(), data.size()));
    CHECK(pipeline.lineState(read.line) ==
          HybridConsumerPipeline::LineState::ReadInFlight);
    CHECK(!pipeline.completeRead(read, data.data(), data.size() - 1));
    CHECK(pipeline.completeRead(read, data.data(), data.size()));

    const auto compute = pipeline.pendingCompute();
    CHECK(pipeline.accept(compute));
    CHECK(pipeline.completeCompute(compute));
    const auto write = pipeline.pendingWrite();
    CHECK(pipeline.accept(write));
    CHECK(pipeline.completed() == 0);
    CHECK(!pipeline.completeWriteAck(stale));
    CHECK(pipeline.completed() == 0);
    CHECK(pipeline.completeWriteAck(write));
    CHECK(pipeline.completed() == 1);
}

void
testCompleteBothWordGeometries()
{
    for (uint8_t wordBytes : {uint8_t{4}, uint8_t{8}}) {
        HybridConsumerPipeline pipeline;
        const auto descriptor = validDescriptor(wordBytes);
        CHECK(pipeline.submit(descriptor) ==
              HybridConsumerPipeline::SubmitResult::Accepted);
        ackAll(pipeline, descriptor);
        const uint16_t expectedLines = static_cast<uint16_t>(
            HybridConsumerPipeline::LogicalElements * wordBytes /
            HybridConsumerPipeline::LineBytes);
        CHECK(pipeline.lines() == expectedLines);
        for (uint16_t line = 0; line < expectedLines; ++line)
            driveLine(pipeline, line);
        CHECK(pipeline.complete());
        CHECK(pipeline.readsAccepted() == expectedLines);
        CHECK(pipeline.computesAccepted() == expectedLines);
        CHECK(pipeline.writesAccepted() == expectedLines);
        CHECK(pipeline.completed() == expectedLines);
        CHECK(pipeline.assertInvariants());
        CHECK(pipeline.retire());
        CHECK(pipeline.getState() == HybridConsumerPipeline::State::Idle);
    }
}

void
testValidationAndExactIdentity()
{
    CHECK(HybridConsumerPipeline::chargedPayloadBytes() == 256);
    CHECK(HybridConsumerPipeline::chargedControlBytes() > 0);
    CHECK(HybridConsumerPipeline::chargedTotalBytes() ==
          HybridConsumerPipeline::chargedPayloadBytes() +
              HybridConsumerPipeline::chargedControlBytes());
    auto descriptor = validDescriptor();
    CHECK(HybridConsumerPipeline::validate(descriptor) == nullptr);

    auto invalid = descriptor;
    invalid.producerTransactions[3] = invalid.producerTransactions[2];
    CHECK(HybridConsumerPipeline::validate(invalid) != nullptr);
    invalid = descriptor;
    invalid.destinationAddress = invalid.backingAddress;
    CHECK(HybridConsumerPipeline::validate(invalid) != nullptr);
    invalid = descriptor;
    invalid.backingAddress += 8;
    // The live selector uses this rejection to retain the existing
    // StreamAccess/RMW fallback for a partial or unaligned request.
    CHECK(HybridConsumerPipeline::validate(invalid) != nullptr);
    invalid = descriptor;
    invalid.generation = std::numeric_limits<uint64_t>::max();
    CHECK(HybridConsumerPipeline::validate(invalid) != nullptr);

    HybridConsumerPipeline pipeline;
    CHECK(pipeline.submit(descriptor) ==
          HybridConsumerPipeline::SubmitResult::Accepted);
    CHECK(pipeline.submit(descriptor) ==
          HybridConsumerPipeline::SubmitResult::Busy);
    CHECK(pipeline.notifyProducerWriteAck(ackFor(descriptor, 0)));
    const auto read = pipeline.pendingRead();
    auto forged = read;
    forged.address += HybridConsumerPipeline::LineBytes;
    CHECK(!pipeline.accept(forged));
    CHECK(pipeline.accept(read));
}

void
testTimingBoundsAreNotSchedulerClaims()
{
    constexpr uint64_t fill = 674828;
    constexpr uint64_t alu = 320512;
    constexpr uint64_t store = 4360716;
    constexpr uint64_t native16 = 40062748;
    constexpr uint64_t hybrid = 45282023;
    constexpr uint64_t target = 1213001;
    constexpr uint64_t maximumStrictTenPercentHybrid =
        native16 + (native16 - 1) / 10;
    CHECK(hybrid - native16 == 5219275);
    CHECK(maximumStrictTenPercentHybrid == 44069022);
    CHECK(hybrid - maximumStrictTenPercentHybrid == target);
    const auto observed = HybridConsumerPipeline::optimisticTimingBound(
        fill, alu, store, target);
    CHECK(observed.serializedTicks == 5356056);
    CHECK(observed.aluStoreEnvelopeTicks == 5035544);
    CHECK(observed.aluStoreSavingsUpperBoundTicks == 320512);
    CHECK(observed.threeStageEnvelopeTicks == 4360716);
    CHECK(observed.threeStageSavingsUpperBoundTicks == 995340);
    CHECK(!observed.aluStoreMeetsTarget);
    CHECK(!observed.threeStageMeetsTarget);

    // A full-line direct-write proxy equal to the observed source-read time
    // is explicitly an optimistic what-if, not a result of driving the model.
    const auto directProxy =
        HybridConsumerPipeline::optimisticReplacementBound(
            observed.serializedTicks, fill, alu, fill, target);
    CHECK(directProxy.candidateEnvelopeTicks == fill);
    CHECK(directProxy.savingsUpperBoundTicks == 4681228);
    CHECK(directProxy.meetsTarget);

    const auto saturated = HybridConsumerPipeline::optimisticTimingBound(
        std::numeric_limits<uint64_t>::max(), 2, 3, 1);
    CHECK(saturated.serializedTicks ==
          std::numeric_limits<uint64_t>::max());
}

} // anonymous namespace

int
main()
{
    testFiniteFourCreditOverlap();
    testLateBoundProducerWriteRespIdentity();
    testRetrySurvivesSchedulingPreferenceChange();
    testNoSyntheticVisibilityOrAcknowledgement();
    testCompleteBothWordGeometries();
    testValidationAndExactIdentity();
    testTimingBoundsAreNotSchedulerClaims();
    std::cout << "hybrid consumer pipeline tests passed\n";
    return 0;
}
