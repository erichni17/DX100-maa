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

HybridConsumerPipeline::ProducerLineAck
lineAckFor(const HybridConsumerPipeline::Descriptor &descriptor,
           uint16_t line, uint16_t wordMask = 0xff,
           uint64_t transaction = 1000)
{
    return {descriptor.generation, line, wordMask, transaction + line};
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
    auto nextRead = pipeline.pendingRead();
    CHECK(nextRead.line == HybridConsumerPipeline::LineBufferCount &&
          nextRead.buffer == write0.buffer);
    CHECK(pipeline.accept(nextRead));
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
testLineWriteRespUnlocksBeforePageClosure()
{
    HybridConsumerPipeline pipeline;
    const auto descriptor = validDescriptor();
    CHECK(pipeline.submit(descriptor) ==
          HybridConsumerPipeline::SubmitResult::Accepted);

    auto stale = lineAckFor(descriptor, 17);
    stale.generation++;
    CHECK(!pipeline.notifyProducerLineWriteAck(stale));
    auto invalid = lineAckFor(descriptor, pipeline.lines());
    CHECK(!pipeline.notifyProducerLineWriteAck(invalid));
    invalid = lineAckFor(descriptor, 17);
    invalid.wordMask = 0;
    CHECK(!pipeline.notifyProducerLineWriteAck(invalid));
    invalid = lineAckFor(descriptor, 17);
    invalid.transactionID = 0;
    CHECK(!pipeline.notifyProducerLineWriteAck(invalid));

    const auto lineAck = lineAckFor(descriptor, 17);
    CHECK(pipeline.notifyProducerLineWriteAck(lineAck));
    CHECK(!pipeline.notifyProducerLineWriteAck(lineAck));
    CHECK(!pipeline.producerPageAcked(0));
    CHECK(pipeline.producerLineAckCount() == 1);
    CHECK(pipeline.producerPageFallbackLineCount() == 0);
    CHECK(pipeline.pendingRead().line == 17);

    const auto read = pipeline.pendingRead();
    CHECK(pipeline.accept(read));
    CHECK(pipeline.notifyProducerWriteAck(ackFor(descriptor, 0)));
    CHECK(pipeline.lineState(17) ==
          HybridConsumerPipeline::LineState::ReadInFlight);
    CHECK(pipeline.producerLineAckCount() == 1);
    CHECK(pipeline.producerPageFallbackLineCount() == 511);
    CHECK(pipeline.assertInvariants());
}

void
testPartialWriteResponsesNeedEveryUniqueWord()
{
    HybridConsumerPipeline pipeline;
    const auto descriptor = validDescriptor();
    CHECK(pipeline.submit(descriptor) ==
          HybridConsumerPipeline::SubmitResult::Accepted);
    CHECK(pipeline.notifyProducerLineWriteAck(
        lineAckFor(descriptor, 9, 0x03, 2000)));
    CHECK(pipeline.lineState(9) == HybridConsumerPipeline::LineState::Blocked);
    CHECK(pipeline.producerLineAckCount() == 0);
    CHECK(!pipeline.notifyProducerLineWriteAck(
        lineAckFor(descriptor, 9, 0x02, 3000)));
    CHECK(pipeline.notifyProducerLineWriteAck(
        lineAckFor(descriptor, 9, 0xfc, 4000)));
    CHECK(pipeline.lineState(9) ==
          HybridConsumerPipeline::LineState::ReadyForRead);
    CHECK(pipeline.producerLineAckCount() == 1);
}

void
testDirectMaterializationCompletionNeedsCompleteAuthenticatedLine()
{
    HybridConsumerPipeline pipeline;
    auto descriptor = validDescriptor();
    descriptor.mode = HybridConsumerPipeline::Mode::MaterializePages;
    CHECK(pipeline.submit(descriptor) ==
          HybridConsumerPipeline::SubmitResult::Accepted);
    CHECK(pipeline.beginMaterializationPage(0));
    CHECK(pipeline.notifyProducerLineWriteAck(
        lineAckFor(descriptor, 0, 0x03, 7000)));
    CHECK(pipeline.producerLineWordMask(0) == 0x03);
    CHECK(!pipeline.completeMaterializeDirect(0));
    CHECK(pipeline.notifyProducerLineWriteAck(
        lineAckFor(descriptor, 0, 0xfc, 8000)));
    CHECK(pipeline.producerLineWordMask(0) == 0xff);
    CHECK(pipeline.lineState(0) ==
          HybridConsumerPipeline::LineState::ReadyForRead);
    // No direct-commit slot means coherent ReadBacking remains legal.
    CHECK(pipeline.pendingRead().line == 0);
    CHECK(pipeline.beginMaterializeDirect(0));
    CHECK(pipeline.lineState(0) ==
          HybridConsumerPipeline::LineState::DirectMaterializeInFlight);
    CHECK(pipeline.pendingRead().kind == HybridConsumerPipeline::Kind::None);
    CHECK(pipeline.completeMaterializeDirect(0));
    CHECK(pipeline.lineState(0) == HybridConsumerPipeline::LineState::Done);
    CHECK(pipeline.completed() == 1);
    CHECK(pipeline.readsAccepted() == 0);
    CHECK(pipeline.assertInvariants());
}

void
testDirectMaterializationLastLineBounds()
{
    for (uint8_t wordBytes : {uint8_t{4}, uint8_t{8}}) {
        HybridConsumerPipeline pipeline;
        auto descriptor = validDescriptor(wordBytes);
        descriptor.mode = HybridConsumerPipeline::Mode::MaterializePages;
        CHECK(pipeline.submit(descriptor) ==
              HybridConsumerPipeline::SubmitResult::Accepted);
        CHECK(pipeline.beginMaterializationPage(0));
        const uint16_t lines = static_cast<uint16_t>(
            HybridConsumerPipeline::ProducerPageElements * wordBytes /
            HybridConsumerPipeline::LineBytes);
        const uint16_t line = lines - 1;
        const uint16_t mask = wordBytes == 4 ? 0xffff : 0x00ff;
        CHECK(pipeline.notifyProducerLineWriteAck(
            lineAckFor(descriptor, line, mask, 9000)));
        CHECK(pipeline.producerLineWordMask(line) == mask);
        CHECK(pipeline.beginMaterializeDirect(line));
        CHECK(pipeline.pendingRead().kind ==
              HybridConsumerPipeline::Kind::None);
        CHECK(pipeline.completeMaterializeDirect(line));
        CHECK(pipeline.lineState(line) ==
              HybridConsumerPipeline::LineState::Done);
        CHECK(pipeline.assertInvariants());
    }
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
        CHECK(pipeline.producerLineAckCount() == 0);
        CHECK(pipeline.producerPageFallbackLineCount() == expectedLines);
        CHECK(pipeline.assertInvariants());
        CHECK(pipeline.retire());
        CHECK(pipeline.getState() == HybridConsumerPipeline::State::Idle);
    }
}

void
testValidationAndExactIdentity()
{
    CHECK(HybridConsumerPipeline::chargedPayloadBytes() == 1024);
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
testTwoDisjointMaterializationPagesShareCreditsExactly()
{
    auto defaultDescriptor = validDescriptor();
    defaultDescriptor.mode = HybridConsumerPipeline::Mode::MaterializePages;
    HybridConsumerPipeline serialized;
    CHECK(serialized.submit(defaultDescriptor) ==
          HybridConsumerPipeline::SubmitResult::Accepted);
    CHECK(serialized.activeMaterializationPageCapacity() == 1);
    CHECK(serialized.beginMaterializationPage(0));
    CHECK(!serialized.beginMaterializationPage(2));
    CHECK(serialized.activeMaterializationPageCount() == 1);

    auto descriptor = defaultDescriptor;
    descriptor.activeMaterializationPages = 2;
    HybridConsumerPipeline pipeline;
    CHECK(pipeline.submit(descriptor) ==
          HybridConsumerPipeline::SubmitResult::Accepted);
    CHECK(pipeline.beginMaterializationPage(0));
    CHECK(pipeline.beginMaterializationPage(2));
    CHECK(!pipeline.beginMaterializationPage(0));
    CHECK(!pipeline.beginMaterializationPage(1));
    CHECK(pipeline.activeMaterializationPageCount() == 2);
    CHECK(pipeline.materializationPageActive(0));
    CHECK(!pipeline.materializationPageActive(1));
    CHECK(pipeline.materializationPageActive(2));

    const uint16_t pageLines = pipeline.producerPageLines();
    const uint16_t pageTwoLine = 2 * pageLines;
    CHECK(pipeline.notifyProducerLineWriteAck(
        lineAckFor(descriptor, 0, 0xff, 0x12000)));
    CHECK(pipeline.notifyProducerLineWriteAck(
        lineAckFor(descriptor, pageTwoLine, 0xff, 0x13000)));

    const auto bytes = payload(0x31);
    const auto pageZeroRead = pipeline.pendingRead();
    CHECK(pageZeroRead.line == 0);
    CHECK(pipeline.accept(pageZeroRead));
    CHECK(pipeline.completeRead(pageZeroRead, bytes.data(), bytes.size()));
    const auto pageTwoRead = pipeline.pendingRead();
    CHECK(pageTwoRead.line == pageTwoLine);
    CHECK(pipeline.accept(pageTwoRead));
    CHECK(pipeline.completeRead(pageTwoRead, bytes.data(), bytes.size()));

    auto crossPageAlias = pageTwoRead;
    crossPageAlias.line = pageZeroRead.line;
    CHECK(!pipeline.completeMaterialize(crossPageAlias));
    CHECK(pipeline.completeMaterialize(pageZeroRead));
    CHECK(!pipeline.completeMaterialize(pageZeroRead));

    // Retire exactly page zero while page two still owns a distinct line and
    // credit. No response from the closed page can alias that live owner.
    for (uint16_t line = 1; line < pageLines; ++line) {
        CHECK(pipeline.notifyProducerLineWriteAck(
            lineAckFor(descriptor, line, 0xff, 0x14000)));
        CHECK(pipeline.beginMaterializeDirect(line));
        CHECK(pipeline.completeMaterializeDirect(line));
    }
    CHECK(pipeline.materializationPageComplete(0));
    CHECK(!pipeline.materializationPageActive(0));
    CHECK(pipeline.materializationPageActive(2));
    CHECK(pipeline.activeMaterializationPageCount() == 1);
    CHECK(pipeline.creditsInUse() == 1);
    CHECK(!pipeline.completeRead(pageZeroRead, bytes.data(), bytes.size()));
    CHECK(pipeline.completeMaterialize(pageTwoRead));
    CHECK(pipeline.creditsInUse() == 0);
    CHECK(pipeline.beginMaterializationPage(1));
    CHECK(pipeline.activeMaterializationPageCount() == 2);
    CHECK(pipeline.assertInvariants());

    auto invalid = descriptor;
    invalid.activeMaterializationPages = 0;
    CHECK(HybridConsumerPipeline::validate(invalid) != nullptr);
    invalid.activeMaterializationPages = 3;
    CHECK(HybridConsumerPipeline::validate(invalid) != nullptr);
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
    testLineWriteRespUnlocksBeforePageClosure();
    testPartialWriteResponsesNeedEveryUniqueWord();
    testDirectMaterializationCompletionNeedsCompleteAuthenticatedLine();
    testDirectMaterializationLastLineBounds();
    testRetrySurvivesSchedulingPreferenceChange();
    testNoSyntheticVisibilityOrAcknowledgement();
    testCompleteBothWordGeometries();
    testValidationAndExactIdentity();
    testTwoDisjointMaterializationPagesShareCreditsExactly();
    testTimingBoundsAreNotSchedulerClaims();
    std::cout << "hybrid consumer pipeline tests passed\n";
    return 0;
}
