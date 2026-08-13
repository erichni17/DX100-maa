#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>

#include "mem/MAA/HybridConsumerContextQueue.hh"

using gem5::HybridConsumerContextQueue;

namespace {

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;         \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

using Queue = HybridConsumerContextQueue;
using Pipeline = Queue::Pipeline;

Queue::Descriptor
descriptor(uint16_t token_tile, uint64_t generation, uint64_t base)
{
    Queue::Descriptor result;
    result.tokenTile = token_tile;
    result.consumer.generation = generation;
    result.consumer.logicalElements = Pipeline::LogicalElements;
    result.consumer.wordBytes = 8;
    result.consumer.backingAddress = base;
    result.consumer.backingRangeMin = base;
    result.consumer.backingRangeMax = base + 0x40000;
    result.consumer.backingRangeID = 3;
    result.consumer.destinationAddress = base + 0x80000;
    result.consumer.destinationRangeMin = base + 0x80000;
    result.consumer.destinationRangeMax = base + 0xc0000;
    result.consumer.destinationRangeID = 4;
    const uint64_t transaction_base = 100 + uint64_t{token_tile} * 4;
    result.consumer.producerTransactions = {
        {transaction_base, transaction_base + 1,
         transaction_base + 2, transaction_base + 3}};
    return result;
}

Pipeline::ProducerAck
pageAck(const Queue::Descriptor &descriptor, uint8_t page)
{
    return {descriptor.consumer.generation, page,
            descriptor.consumer.producerTransactions[page]};
}

Pipeline::ProducerLineAck
lineAck(const Queue::Descriptor &descriptor, uint16_t line)
{
    return {descriptor.consumer.generation, line, 0xff,
            uint64_t{0x1000} + line};
}

std::array<std::byte, Pipeline::LineBytes>
payload(uint8_t value)
{
    std::array<std::byte, Pipeline::LineBytes> result{};
    result.fill(std::byte{value});
    return result;
}

void
driveOneLine(Queue &queue, const Queue::ContextKey &owner,
             uint16_t expected_line)
{
    const auto read = queue.pendingRead();
    CHECK(read.owner.tokenTile == owner.tokenTile);
    CHECK(read.owner.generation == owner.generation);
    CHECK(read.owner.incarnation == owner.incarnation);
    CHECK(read.request.kind == Pipeline::Kind::ReadBacking);
    CHECK(read.request.line == expected_line);
    CHECK(queue.accept(read));
    const auto data = payload(static_cast<uint8_t>(expected_line));
    CHECK(queue.completeRead(read, data.data(), data.size()));

    const auto compute = queue.pendingCompute();
    CHECK(compute.owner.incarnation == owner.incarnation);
    CHECK(compute.request.kind == Pipeline::Kind::Compute);
    CHECK(compute.request.line == expected_line);
    CHECK(queue.accept(compute));
    CHECK(queue.bufferData(compute) != nullptr);
    queue.bufferData(compute)[0] = std::byte{0xa5};
    CHECK(queue.completeCompute(compute));

    const auto write = queue.pendingWrite();
    CHECK(write.owner.incarnation == owner.incarnation);
    CHECK(write.request.kind == Pipeline::Kind::WriteDestination);
    CHECK(write.request.line == expected_line);
    CHECK(queue.accept(write));
    CHECK(!queue.completeWriteAck(compute));
    CHECK(queue.completeWriteAck(write));
}

void
testFourContextsRetainIndependentLineVisibility()
{
    Queue queue;
    std::array<Queue::Descriptor, Queue::ContextCount> descriptors{};
    std::array<Queue::ContextKey, Queue::ContextCount> keys{};
    for (uint8_t index = 0; index < Queue::ContextCount; ++index) {
        descriptors[index] = descriptor(
            index, 1, 0x100000 + index * 0x200000);
        CHECK(queue.submit(descriptors[index], &keys[index]) ==
              Queue::SubmitResult::Accepted);
        CHECK(keys[index].generation == 1);
        CHECK(keys[index].incarnation != 0);
    }
    CHECK(queue.activeContexts() == Queue::ContextCount);
    CHECK(queue.submit(descriptor(9, 1, 0x2000000), nullptr) ==
          Queue::SubmitResult::Full);

    // Four tiles may all be at generation one: the tile ID disambiguates
    // ownership. Only descriptor zero gets its first line ready here.
    CHECK(queue.notifyProducerLineWriteAck(keys[0],
                                           lineAck(descriptors[0], 0)));
    const auto first = queue.pendingRead();
    CHECK(first.owner.tokenTile == keys[0].tokenTile);
    CHECK(first.request.line == 0);
    driveOneLine(queue, keys[0], 0);

    // Descriptor zero has no further visible line. Page readiness for one
    // therefore selects descriptor one, demonstrating retained contexts.
    CHECK(queue.notifyProducerWriteAck(keys[1],
                                       pageAck(descriptors[1], 0)));
    const auto second = queue.pendingRead();
    CHECK(second.owner.tokenTile == keys[1].tokenTile);
    CHECK(second.owner.generation == keys[1].generation);
    CHECK(second.owner.incarnation == keys[1].incarnation);
    CHECK(second.request.line == 0);
    CHECK(queue.assertInvariants());
}

void
testFailClosedGenerationAndIncarnationRouting()
{
    Queue queue;
    const auto first_descriptor = descriptor(7, 13, 0x100000);
    Queue::ContextKey first{};
    CHECK(queue.submit(first_descriptor, &first) ==
          Queue::SubmitResult::Accepted);
    CHECK(queue.submit(first_descriptor, nullptr) ==
          Queue::SubmitResult::Duplicate);

    auto wrong_generation = first;
    ++wrong_generation.generation;
    auto wrong_tile = first;
    ++wrong_tile.tokenTile;
    auto wrong_incarnation = first;
    ++wrong_incarnation.incarnation;
    CHECK(!queue.notifyProducerLineWriteAck(
        wrong_generation, lineAck(first_descriptor, 0)));
    CHECK(!queue.notifyProducerLineWriteAck(
        wrong_tile, lineAck(first_descriptor, 0)));
    CHECK(!queue.notifyProducerLineWriteAck(
        wrong_incarnation, lineAck(first_descriptor, 0)));
    CHECK(queue.pendingRead().request.kind == Pipeline::Kind::None);

    CHECK(queue.notifyProducerLineWriteAck(
        first, lineAck(first_descriptor, 0)));
    const auto read = queue.pendingRead();
    CHECK(queue.accept(read));
    const auto data = payload(1);
    auto stale_response = read;
    ++stale_response.owner.incarnation;
    CHECK(!queue.completeRead(stale_response, data.data(), data.size()));
    CHECK(queue.completeRead(read, data.data(), data.size()));
    CHECK(queue.assertInvariants());
}

void
testOneSharedAluAcrossContexts()
{
    Queue queue;
    const auto left = descriptor(0, 1, 0x100000);
    const auto right = descriptor(1, 1, 0x400000);
    Queue::ContextKey left_key{};
    Queue::ContextKey right_key{};
    CHECK(queue.submit(left, &left_key) ==
          Queue::SubmitResult::Accepted);
    CHECK(queue.submit(right, &right_key) ==
          Queue::SubmitResult::Accepted);
    CHECK(queue.notifyProducerLineWriteAck(left_key, lineAck(left, 0)));
    CHECK(queue.notifyProducerLineWriteAck(right_key, lineAck(right, 0)));

    const auto left_read = queue.pendingRead();
    CHECK(queue.accept(left_read));
    const auto data = payload(2);
    CHECK(queue.completeRead(left_read, data.data(), data.size()));
    const auto right_read = queue.pendingRead();
    CHECK(right_read.owner.tokenTile == right_key.tokenTile);
    CHECK(queue.accept(right_read));
    CHECK(queue.completeRead(right_read, data.data(), data.size()));

    const auto left_compute = queue.pendingCompute();
    CHECK(left_compute.owner.tokenTile == left_key.tokenTile);
    CHECK(queue.accept(left_compute));
    // A second context cannot claim a new ALU while this one is active.
    CHECK(queue.pendingCompute().request.kind == Pipeline::Kind::None);
    CHECK(queue.completeCompute(left_compute));
    const auto right_compute = queue.pendingCompute();
    CHECK(right_compute.owner.tokenTile == right_key.tokenTile);
    CHECK(queue.assertInvariants());
}

void
testRoundRobinSharedCacheArbitration()
{
    Queue queue;
    const auto left = descriptor(0, 1, 0x100000);
    const auto right = descriptor(1, 1, 0x400000);
    Queue::ContextKey left_key{};
    Queue::ContextKey right_key{};
    CHECK(queue.submit(left, &left_key) ==
          Queue::SubmitResult::Accepted);
    CHECK(queue.submit(right, &right_key) ==
          Queue::SubmitResult::Accepted);
    CHECK(queue.notifyProducerWriteAck(left_key, pageAck(left, 0)));
    CHECK(queue.notifyProducerWriteAck(right_key, pageAck(right, 0)));

    const auto left_read = queue.pendingRead();
    CHECK(left_read.owner.tokenTile == left_key.tokenTile);
    CHECK(queue.accept(left_read));
    const auto data = payload(3);
    CHECK(queue.completeRead(left_read, data.data(), data.size()));
    // Left has hundreds of other ready lines, but it cannot monopolize the
    // shared cache-port issue opportunity ahead of the ready right context.
    const auto right_read = queue.pendingRead();
    CHECK(right_read.owner.tokenTile == right_key.tokenTile);
    CHECK(right_read.request.line == 0);
    CHECK(queue.assertInvariants());
}

void
testRetirementRejectsStaleSlotReuse()
{
    Queue queue;
    const auto old_descriptor = descriptor(2, 9, 0x100000);
    Queue::ContextKey old_key{};
    CHECK(queue.submit(old_descriptor, &old_key) ==
          Queue::SubmitResult::Accepted);
    for (uint8_t page = 0; page < Pipeline::ProducerPages; ++page) {
        CHECK(queue.notifyProducerWriteAck(
            old_key, pageAck(old_descriptor, page)));
    }
    for (uint16_t line = 0; line < Pipeline::MaxLines; ++line)
        driveOneLine(queue, old_key, line);
    CHECK(queue.retire(old_key));
    CHECK(!queue.active(old_key));

    // The incarnation rejects late traffic even if a broken upstream producer
    // reuses a retired token/generation pair.
    Queue::ContextKey replacement{};
    CHECK(queue.submit(old_descriptor, &replacement) ==
          Queue::SubmitResult::Accepted);
    CHECK(replacement.incarnation != old_key.incarnation);
    CHECK(!queue.notifyProducerLineWriteAck(
        old_key, lineAck(old_descriptor, 0)));
    CHECK(queue.notifyProducerLineWriteAck(
        replacement, lineAck(old_descriptor, 0)));
    CHECK(queue.assertInvariants());
}

void
testExactFourDescriptorClosureWithoutPageFallback()
{
    Queue queue;
    std::array<Queue::Descriptor, Queue::ContextCount> descriptors{};
    std::array<Queue::ContextKey, Queue::ContextCount> keys{};
    for (uint8_t context = 0; context < Queue::ContextCount; ++context) {
        descriptors[context] = descriptor(
            context, context + 1, 0x100000 + context * 0x200000);
        CHECK(queue.submit(descriptors[context], &keys[context]) ==
              Queue::SubmitResult::Accepted);
        for (uint16_t line = 0; line < Pipeline::MaxLines; ++line) {
            CHECK(queue.notifyProducerLineWriteAck(
                keys[context], lineAck(descriptors[context], line)));
        }
    }

    uint32_t closed_lines = 0;
    while (closed_lines <
           Queue::ContextCount * static_cast<uint32_t>(Pipeline::MaxLines)) {
        const auto read = queue.pendingRead();
        CHECK(read.request.kind == Pipeline::Kind::ReadBacking);
        CHECK(queue.accept(read));
        const auto data = payload(static_cast<uint8_t>(read.request.line));
        CHECK(queue.completeRead(read, data.data(), data.size()));

        const auto compute = queue.pendingCompute();
        CHECK(compute.request.kind == Pipeline::Kind::Compute);
        CHECK(queue.accept(compute));
        // A second compute cannot be exposed while the one shared ALU owns
        // this request, even though all four contexts are runnable.
        CHECK(queue.pendingCompute().request.kind == Pipeline::Kind::None);
        CHECK(queue.completeCompute(compute));

        const auto write = queue.pendingWrite();
        CHECK(write.request.kind == Pipeline::Kind::WriteDestination);
        CHECK(queue.accept(write));
        CHECK(queue.completeWriteAck(write));
        ++closed_lines;
    }
    CHECK(closed_lines == 8192);
    CHECK(queue.totalCreditsInUse() == 0);

    for (uint8_t context = 0; context < Queue::ContextCount; ++context) {
        Queue::Snapshot snapshot;
        CHECK(queue.snapshot(keys[context], &snapshot));
        CHECK(snapshot.complete);
        CHECK(snapshot.lines == Pipeline::MaxLines);
        CHECK(snapshot.completed == Pipeline::MaxLines);
        CHECK(snapshot.readsAccepted == Pipeline::MaxLines);
        CHECK(snapshot.computesAccepted == Pipeline::MaxLines);
        CHECK(snapshot.writesAccepted == Pipeline::MaxLines);
        CHECK(snapshot.producerLineAcks == Pipeline::MaxLines);
        CHECK(snapshot.producerPageFallbackLines == 0);
        CHECK(snapshot.creditsInUse == 0);
        CHECK(queue.retire(keys[context]));
    }
    CHECK(queue.activeContexts() == 0);
    std::cout << "exact four-context closure lines=" << closed_lines
              << " descriptor_2_4_page_fallback_lines=0"
              << " shared_alu=1\n";
}

void
testStorageCharge()
{
    CHECK(Queue::chargedPayloadBytes() == 4096);
    CHECK(Queue::chargedPipelineControlBytes() ==
          4 * Pipeline::chargedControlBytes());
    CHECK(Queue::chargedControlBytes() ==
          Queue::chargedPipelineControlBytes() +
              Queue::chargedQueueControlBytes());
    CHECK(Queue::chargedTotalBytes() == Queue::chargedPayloadBytes() +
          Queue::chargedControlBytes());
    std::cout << "context queue storage payload="
              << Queue::chargedPayloadBytes()
              << " pipeline_control="
              << Queue::chargedPipelineControlBytes()
              << " queue_control=" << Queue::chargedQueueControlBytes()
              << " total=" << Queue::chargedTotalBytes() << '\n';
}

} // anonymous namespace

int
main()
{
    testFourContextsRetainIndependentLineVisibility();
    testFailClosedGenerationAndIncarnationRouting();
    testOneSharedAluAcrossContexts();
    testRoundRobinSharedCacheArbitration();
    testRetirementRejectsStaleSlotReuse();
    testExactFourDescriptorClosureWithoutPageFallback();
    testStorageCharge();
    std::cout << "hybrid consumer context queue tests passed\n";
    return 0;
}
