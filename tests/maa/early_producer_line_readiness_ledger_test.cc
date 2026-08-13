#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <type_traits>

#include "mem/MAA/EarlyProducerLineReadinessLedger.hh"
#include "mem/MAA/HybridConsumerContextQueue.hh"

using gem5::EarlyProducerLineReadinessLedger;
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

using Ledger = EarlyProducerLineReadinessLedger;
using Queue = HybridConsumerContextQueue;
using Pipeline = Queue::Pipeline;

constexpr uint16_t FullMask = 0xff;
constexpr uint64_t LogicalBytes =
    static_cast<uint64_t>(Pipeline::LogicalElements) * 8;
constexpr uint16_t LinesPerProducerPage =
    Pipeline::MaxLines / Pipeline::ProducerPages;

Ledger::Key
key(uint16_t token, uint64_t generation, uint64_t backing)
{
    return {token, generation, backing};
}

Queue::Descriptor
descriptor(const Ledger::Key &owner)
{
    Queue::Descriptor result;
    result.tokenTile = owner.tokenTile;
    result.consumer.generation = owner.generation;
    result.consumer.logicalElements = Pipeline::LogicalElements;
    result.consumer.wordBytes = 8;
    result.consumer.backingAddress = owner.backingAddress;
    result.consumer.backingRangeMin = owner.backingAddress;
    result.consumer.backingRangeMax = owner.backingAddress + LogicalBytes;
    result.consumer.backingRangeID = 1;
    result.consumer.destinationAddress = owner.backingAddress + 0x100000;
    result.consumer.destinationRangeMin = result.consumer.destinationAddress;
    result.consumer.destinationRangeMax =
        result.consumer.destinationAddress + LogicalBytes;
    result.consumer.destinationRangeID = 2;
    return result;
}

void
testEarlyBeforeSubmitReplaysBeforePageFallback()
{
    Ledger ledger;
    const auto owner = key(3, 11, 0x100000);
    CHECK(ledger.begin(owner, Pipeline::MaxLines, FullMask) ==
          Ledger::BeginResult::Started);
    CHECK(ledger.acknowledge(owner, {0, FullMask, 101}) ==
          Ledger::AckResult::LineReady);
    CHECK(ledger.acknowledge(owner, {1, 0x0f, 102}) ==
          Ledger::AckResult::RecordedPartial);
    CHECK(ledger.acknowledge(owner, {1, 0xf0, 103}) ==
          Ledger::AckResult::LineReady);
    CHECK(ledger.acknowledge(owner, {2, 0x03, 104}) ==
          Ledger::AckResult::RecordedPartial);

    Queue queue;
    const auto desc = descriptor(owner);
    Queue::ContextKey context;
    CHECK(queue.submit(desc, &context) == Queue::SubmitResult::Accepted);
    Ledger::ReplaySummary replayed;
    CHECK(ledger.replay(
        owner,
        [&queue, &context, &owner](const Ledger::LineAck &ack) {
            return queue.notifyProducerLineWriteAck(
                context, {owner.generation, ack.line, ack.wordMask,
                          ack.transactionID});
        },
        &replayed));
    CHECK(replayed.entries == 3);
    CHECK(replayed.readyLines == 2);
    CHECK(!replayed.overflowed);

    // Applying page completion second closes only the other lines.  A line
    // cannot be counted both as exact early visibility and as page fallback.
    CHECK(queue.notifyProducerWriteAck(
        context, {owner.generation, 0, 201}));
    Queue::Snapshot snapshot;
    CHECK(queue.snapshot(context, &snapshot));
    CHECK(snapshot.producerLineAcks == 2);
    CHECK(snapshot.producerPageFallbackLines ==
          LinesPerProducerPage - 2);
    CHECK(snapshot.producerLineAcks + snapshot.producerPageFallbackLines ==
          LinesPerProducerPage);
    CHECK(ledger.clear(owner));
    CHECK(!ledger.active(owner));
}

void
testDuplicateAndStaleAcknowledgementsFailClosed()
{
    Ledger ledger;
    const auto owner = key(1, 7, 0x400000);
    CHECK(ledger.begin(owner, Pipeline::MaxLines, FullMask) ==
          Ledger::BeginResult::Started);
    CHECK(ledger.acknowledge(owner, {9, 0x0f, 301}) ==
          Ledger::AckResult::RecordedPartial);
    CHECK(ledger.acknowledge(owner, {9, 0x01, 301}) ==
          Ledger::AckResult::Duplicate);
    CHECK(ledger.acknowledge(key(1, 6, owner.backingAddress),
                             {9, 0xf0, 302}) ==
          Ledger::AckResult::Stale);
    CHECK(ledger.acknowledge(key(1, 7, owner.backingAddress + 64),
                             {9, 0xf0, 302}) ==
          Ledger::AckResult::Stale);
    CHECK(ledger.readyLineCount(owner) == 0);
    CHECK(ledger.acknowledge(owner, {9, 0xf0, 302}) ==
          Ledger::AckResult::LineReady);
    CHECK(ledger.acknowledge(owner, {9, FullMask, 303}) ==
          Ledger::AckResult::Duplicate);
    CHECK(ledger.readyLineCount(owner) == 1);
    CHECK(ledger.assertInvariants());
}

void
testGenerationReuseAndRetirementCleanup()
{
    Ledger ledger;
    const auto oldOwner = key(2, 20, 0x800000);
    const auto newOwner = key(2, 21, 0xa00000);
    CHECK(ledger.begin(oldOwner, Pipeline::MaxLines, FullMask) ==
          Ledger::BeginResult::Started);
    CHECK(ledger.acknowledge(oldOwner, {17, FullMask, 401}) ==
          Ledger::AckResult::LineReady);
    CHECK(ledger.begin(newOwner, Pipeline::MaxLines, FullMask) ==
          Ledger::BeginResult::Replaced);
    CHECK(!ledger.active(oldOwner));
    CHECK(ledger.active(newOwner));
    CHECK(ledger.readyLineCount(newOwner) == 0);
    CHECK(ledger.acknowledge(oldOwner, {17, FullMask, 402}) ==
          Ledger::AckResult::Stale);
    CHECK(ledger.begin(oldOwner, Pipeline::MaxLines, FullMask) ==
          Ledger::BeginResult::Stale);
    CHECK(ledger.clear(newOwner));
    CHECK(ledger.activeSlots() == 0);
    CHECK(!ledger.clear(newOwner));
    CHECK(ledger.assertInvariants());
}

void
testOverflowUsesExactPageFallback()
{
    Ledger ledger;
    const auto owner = key(4, 30, 0xc00000);
    CHECK(ledger.begin(owner, Pipeline::MaxLines, FullMask) ==
          Ledger::BeginResult::Started);
    for (uint16_t line = 0; line < Ledger::TrackedLinesPerSlot; ++line) {
        CHECK(ledger.acknowledge(
                  owner, {line, FullMask, uint64_t{500} + line}) ==
              Ledger::AckResult::LineReady);
    }
    CHECK(ledger.acknowledge(
              owner, {Ledger::TrackedLinesPerSlot, FullMask, 600}) ==
          Ledger::AckResult::Overflow);

    Queue queue;
    const auto desc = descriptor(owner);
    Queue::ContextKey context;
    CHECK(queue.submit(desc, &context) == Queue::SubmitResult::Accepted);
    Ledger::ReplaySummary replayed;
    CHECK(ledger.replay(
        owner,
        [&queue, &context, &owner](const Ledger::LineAck &ack) {
            return queue.notifyProducerLineWriteAck(
                context, {owner.generation, ack.line, ack.wordMask,
                          ack.transactionID});
        },
        &replayed));
    CHECK(replayed.entries == Ledger::TrackedLinesPerSlot);
    CHECK(replayed.readyLines == Ledger::TrackedLinesPerSlot);
    CHECK(replayed.overflowed);
    CHECK(queue.notifyProducerWriteAck(
        context, {owner.generation, 0, 701}));
    Queue::Snapshot snapshot;
    CHECK(queue.snapshot(context, &snapshot));
    CHECK(snapshot.producerLineAcks == Ledger::TrackedLinesPerSlot);
    CHECK(snapshot.producerPageFallbackLines ==
          LinesPerProducerPage - Ledger::TrackedLinesPerSlot);
    CHECK(snapshot.producerLineAcks + snapshot.producerPageFallbackLines ==
          LinesPerProducerPage);
}

void
testBoundedStorageAbi()
{
    constexpr std::size_t NativePhysicalTileBytes = 4096U * 4U;
    constexpr std::size_t NativeFp64ResultBytes =
        2U * NativePhysicalTileBytes;
    constexpr std::size_t NativeDefaultSpdBytes =
        32U * NativePhysicalTileBytes;
    static_assert(std::is_standard_layout_v<Ledger>);
    static_assert(std::is_trivially_copyable_v<Ledger>);
    CHECK(Ledger::SlotCount == Queue::ContextCount);
    CHECK(Ledger::MaxLines == Pipeline::MaxLines);
    CHECK(Ledger::PackedLineMask == Pipeline::MaxLines - 1);
    CHECK(Ledger::chargedEntryBytes() == 1536);
    CHECK(Ledger::chargedMetadataBytes() == 160);
    CHECK(Ledger::chargedTotalBytes() == 1696);
    CHECK(Ledger::chargedTotalBytes() == sizeof(Ledger));
    CHECK(Ledger::chargedTotalBytes() == Ledger::chargedEntryBytes() +
          Ledger::chargedMetadataBytes());
    CHECK(Ledger::chargedTotalBytes() < Queue::chargedPayloadBytes());
    CHECK(Ledger::chargedTotalBytes() < Queue::chargedControlBytes());
    CHECK(Ledger::chargedTotalBytes() < NativePhysicalTileBytes);

    Ledger ledger;
    for (uint16_t slot = 0; slot < Ledger::SlotCount; ++slot) {
        CHECK(ledger.begin(key(slot, 1, 0x100000 + slot * 0x200000),
                           Pipeline::MaxLines, FullMask) ==
              Ledger::BeginResult::Started);
    }
    CHECK(ledger.begin(key(9, 1, 0xf00000), Pipeline::MaxLines,
                       FullMask) == Ledger::BeginResult::Full);
    CHECK(ledger.activeSlots() == Ledger::SlotCount);
    std::cout << "early-line ledger storage entries="
              << Ledger::chargedEntryBytes()
              << " metadata=" << Ledger::chargedMetadataBytes()
              << " total=" << Ledger::chargedTotalBytes()
              << " hybrid_payload=" << Queue::chargedPayloadBytes()
              << " hybrid_control=" << Queue::chargedControlBytes()
              << " native_4k_element_tile=" << NativePhysicalTileBytes
              << " native_fp64_result=" << NativeFp64ResultBytes
              << " native_4k_element_spd=" << NativeDefaultSpdBytes
              << '\n';
}

} // anonymous namespace

int
main()
{
    testEarlyBeforeSubmitReplaysBeforePageFallback();
    testDuplicateAndStaleAcknowledgementsFailClosed();
    testGenerationReuseAndRetirementCleanup();
    testOverflowUsesExactPageFallback();
    testBoundedStorageAbi();
    std::cout << "early producer line readiness ledger tests passed\n";
    return 0;
}
