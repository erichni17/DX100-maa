#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>

#include "mem/MAA/DirectProducerResultContextQueue.hh"

using gem5::DirectProducerResultContextQueue;

namespace {

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;         \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

using Queue = DirectProducerResultContextQueue;
using Handoff = Queue::Handoff;

Queue::Descriptor
descriptor(uint16_t token, uint64_t generation, uint64_t base)
{
    Queue::Descriptor result;
    result.producer.generation = generation;
    result.producer.tokenTile = token;
    result.producer.logicalElements = Handoff::LogicalElements;
    result.producer.rows = Handoff::ProducerRows;
    result.producer.rowOffsets = Handoff::ProducerRowOffsets;
    result.producer.wordBytes = Handoff::WordBytes;
    result.producer.isVirtualGather = true;
    result.producer.completionOnlyToken = true;

    result.consumer.generation = generation;
    result.consumer.tokenTile = token;
    result.consumer.scalarBits = Handoff::ScalarThreeBits;
    result.consumer.destinationAddress = base + 0x60000;
    result.consumer.destinationRangeMin = base + 0x60000;
    result.consumer.destinationRangeMax = base + 0x80000;
    result.consumer.destinationRangeID = 4;
    result.consumer.isFP64MultiplyStore = true;

    result.proof.source = {base, 0x20000, base, base + 0x20000, 1};
    result.proof.indices = {base + 0x20000, Handoff::IndexBytes,
                            base + 0x20000, base + 0x30000, 2};
    result.proof.intermediate = {
        base + 0x40000, Handoff::IntermediateBytes,
        base + 0x40000, base + 0x60000, 3};
    result.proof.destination = {
        result.consumer.destinationAddress, Handoff::IntermediateBytes,
        result.consumer.destinationRangeMin,
        result.consumer.destinationRangeMax,
        result.consumer.destinationRangeID};
    result.proof.intermediateDeadAfterConsumer = true;
    result.proof.producerTokenPrivateToConsumer = true;
    result.proof.noCpuOrPeerAccessUntilCompletion = true;
    result.proof.translationsAndAccessSideEffectsPrevalidated = true;
    result.proof.noHiddenPhysicalAliases = true;
    result.proof.noIntermediateWriteIssued = true;
    return result;
}

std::array<std::byte, Handoff::LineBytes>
linePayload(uint16_t line, uint8_t context)
{
    std::array<std::byte, Handoff::LineBytes> result{};
    for (uint8_t word = 0; word < Handoff::WordsPerLine; ++word) {
        const double value = 1000.0 * context + line + word / 16.0;
        std::memcpy(result.data() + word * sizeof(value), &value,
                    sizeof(value));
    }
    return result;
}

bool
sameKey(const Queue::ContextKey &lhs, const Queue::ContextKey &rhs)
{
    return lhs.tokenTile == rhs.tokenTile &&
        lhs.generation == rhs.generation &&
        lhs.incarnation == rhs.incarnation;
}

void
testEligibilityCapacityAndExactOwner()
{
    Queue queue;
    auto ineligible = descriptor(0, 1, 0x100000);
    ineligible.proof.intermediateDeadAfterConsumer = false;
    CHECK(queue.submit(ineligible, nullptr) ==
          Queue::SubmitResult::Ineligible);

    std::array<Queue::ContextKey, Queue::ContextCount> owners{};
    for (uint8_t context = 0; context < Queue::ContextCount; ++context) {
        CHECK(queue.submit(descriptor(
                  context, context + 1, 0x100000 + context * 0x100000),
                  &owners[context]) == Queue::SubmitResult::Accepted);
        CHECK(owners[context].tokenTile == context);
        CHECK(owners[context].incarnation != 0);
        if (context == 0) {
            auto aliasesLiveDestination = descriptor(10, 1, 0x900000);
            aliasesLiveDestination.proof.source = {
                0x160000, 0x20000, 0x160000, 0x180000, 8};
            CHECK(queue.submit(aliasesLiveDestination, nullptr) ==
                  Queue::SubmitResult::AliasConflict);

            auto aliasesLiveSource = descriptor(11, 1, 0xb00000);
            aliasesLiveSource.consumer.destinationAddress = 0x100000;
            aliasesLiveSource.consumer.destinationRangeMin = 0x100000;
            aliasesLiveSource.consumer.destinationRangeMax = 0x120000;
            aliasesLiveSource.consumer.destinationRangeID = 9;
            aliasesLiveSource.proof.destination = {
                0x100000, Handoff::IntermediateBytes,
                0x100000, 0x120000, 9};
            CHECK(queue.submit(aliasesLiveSource, nullptr) ==
                  Queue::SubmitResult::AliasConflict);
        }
    }
    CHECK(queue.activeContexts() == Queue::ContextCount);
    CHECK(queue.submit(descriptor(9, 1, 0x900000), nullptr) ==
          Queue::SubmitResult::Full);
    CHECK(queue.submit(descriptor(0, 1, 0x900000), nullptr) ==
          Queue::SubmitResult::Duplicate);

    auto stale = owners[0];
    ++stale.incarnation;
    const auto payload = linePayload(0, 0);
    CHECK(queue.acceptProducerWrite(
              stale, 0, 0xff, payload.data(), payload.size()) ==
          Handoff::ProducerWriteResult::Rejected);
    CHECK(queue.totalCreditsInUse() == 0);
    CHECK(queue.assertInvariants());
}

void
testOneSharedALUAndExactCallbacks()
{
    Queue queue;
    Queue::ContextKey left{};
    Queue::ContextKey right{};
    CHECK(queue.submit(descriptor(0, 1, 0x100000), &left) ==
          Queue::SubmitResult::Accepted);
    CHECK(queue.submit(descriptor(1, 1, 0x300000), &right) ==
          Queue::SubmitResult::Accepted);
    auto payload = linePayload(0, 0);
    CHECK(queue.acceptProducerWrite(
              left, 0, 0xff, payload.data(), payload.size()) ==
          Handoff::ProducerWriteResult::AcceptedLineReady);
    payload = linePayload(0, 1);
    CHECK(queue.acceptProducerWrite(
              right, 0, 0xff, payload.data(), payload.size()) ==
          Handoff::ProducerWriteResult::AcceptedLineReady);

    const auto leftALU = queue.pendingALU();
    CHECK(sameKey(leftALU.owner, left));
    CHECK(queue.acceptALU(leftALU));
    CHECK(queue.pendingALU().request.line == Handoff::Lines);
    auto staleALU = leftALU;
    ++staleALU.owner.incarnation;
    CHECK(!queue.completeALU(staleALU));
    CHECK(queue.completeALU(leftALU));

    const auto rightALU = queue.pendingALU();
    CHECK(sameKey(rightALU.owner, right));
    CHECK(queue.acceptALU(rightALU));
    CHECK(queue.completeALUExternally(rightALU));
    const auto leftStore = queue.pendingStore();
    CHECK(sameKey(leftStore.owner, left));
    CHECK(queue.acceptStore(leftStore));
    auto staleStore = leftStore;
    ++staleStore.owner.generation;
    CHECK(!queue.completeStoreAck(staleStore));
    CHECK(queue.completeStoreAck(leftStore));
    const auto rightStore = queue.pendingStore();
    CHECK(sameKey(rightStore.owner, right));
    CHECK(queue.assertInvariants());
}

void
testFourContextFullClosureAndStaleReuse()
{
    Queue queue;
    std::array<Queue::Descriptor, Queue::ContextCount> descriptors{};
    std::array<Queue::ContextKey, Queue::ContextCount> owners{};
    for (uint8_t context = 0; context < Queue::ContextCount; ++context) {
        descriptors[context] = descriptor(
            context, context + 1, 0x100000 + context * 0x100000);
        CHECK(queue.submit(descriptors[context], &owners[context]) ==
              Queue::SubmitResult::Accepted);
    }

    for (uint16_t line = 0; line < Handoff::Lines; ++line) {
        for (uint8_t context = 0; context < Queue::ContextCount; ++context) {
            const auto payload = linePayload(line, context);
            CHECK(queue.acceptProducerWrite(
                      owners[context], line, 0xff,
                      payload.data(), payload.size()) ==
                  Handoff::ProducerWriteResult::AcceptedLineReady);
        }
        for (uint8_t context = 0; context < Queue::ContextCount; ++context) {
            const auto alu = queue.pendingALU();
            CHECK(sameKey(alu.owner, owners[context]));
            CHECK(alu.request.line == line);
            CHECK(queue.acceptALU(alu));
            CHECK(queue.pendingALU().request.line == Handoff::Lines);
            CHECK(queue.completeALU(alu));
        }
        for (uint8_t context = 0; context < Queue::ContextCount; ++context) {
            const auto store = queue.pendingStore();
            CHECK(sameKey(store.owner, owners[context]));
            CHECK(store.request.line == line);
            CHECK(queue.acceptStore(store));
            CHECK(queue.completeStoreAck(store));
        }
    }

    CHECK(queue.totalCreditsInUse() == 0);
    const auto oldOwner = owners[0];
    for (uint8_t context = 0; context < Queue::ContextCount; ++context) {
        Queue::Snapshot snapshot;
        CHECK(queue.snapshot(owners[context], &snapshot));
        CHECK(snapshot.complete);
        CHECK(snapshot.producerWordsAccepted == Handoff::LogicalElements);
        CHECK(snapshot.storesAcked == Handoff::Lines);
        CHECK(snapshot.creditsInUse == 0);
        CHECK(snapshot.creditHighWater != 0);
        CHECK(queue.retire(owners[context]));
    }
    CHECK(queue.activeContexts() == 0);

    Queue::ContextKey replacement{};
    CHECK(queue.submit(descriptors[0], &replacement) ==
          Queue::SubmitResult::Accepted);
    CHECK(replacement.incarnation != oldOwner.incarnation);
    const auto payload = linePayload(0, 0);
    CHECK(queue.acceptProducerWrite(
              oldOwner, 0, 0xff, payload.data(), payload.size()) ==
          Handoff::ProducerWriteResult::Rejected);
    CHECK(queue.acceptProducerWrite(
              replacement, 0, 0xff, payload.data(), payload.size()) ==
          Handoff::ProducerWriteResult::AcceptedLineReady);
    CHECK(queue.assertInvariants());
    std::cout << "direct payload closure contexts=4 words=65536 lines=8192 "
              << "backing_writes=0 backing_reads=0 shared_alu=1\n";
}

void
testStorageCharge()
{
    CHECK(Queue::chargedPayloadBytes() == 4096);
    CHECK(Queue::chargedHandoffControlBytes() ==
          Queue::ContextCount * Handoff::chargedControlBytes());
    CHECK(Queue::chargedControlBytes() ==
          Queue::chargedHandoffControlBytes() +
              Queue::chargedQueueControlBytes());
    CHECK(Queue::chargedTotalBytes() == Queue::chargedPayloadBytes() +
          Queue::chargedControlBytes());
    std::cout << "direct payload storage payload="
              << Queue::chargedPayloadBytes()
              << " handoff_control="
              << Queue::chargedHandoffControlBytes()
              << " queue_control=" << Queue::chargedQueueControlBytes()
              << " total=" << Queue::chargedTotalBytes() << '\n';
}

} // anonymous namespace

int
main()
{
    testEligibilityCapacityAndExactOwner();
    testOneSharedALUAndExactCallbacks();
    testFourContextFullClosureAndStaleReuse();
    testStorageCharge();
    std::cout << "direct producer result context queue tests passed\n";
    return 0;
}
