#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
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
materializerDescriptor()
{
    Queue::Descriptor descriptor;
    descriptor.tokenTile = 7;
    descriptor.consumer.mode = Pipeline::Mode::MaterializePages;
    descriptor.consumer.generation = 11;
    descriptor.consumer.logicalElements = Pipeline::LogicalElements;
    descriptor.consumer.wordBytes = 8;
    descriptor.consumer.backingAddress = 0x100000;
    descriptor.consumer.backingRangeMin = 0x100000;
    descriptor.consumer.backingRangeMax = 0x120000;
    descriptor.consumer.backingRangeID = 3;
    return descriptor;
}

std::array<std::byte, Pipeline::LineBytes>
linePayload(uint64_t first)
{
    std::array<std::byte, Pipeline::LineBytes> payload{};
    for (uint8_t word = 0; word < Pipeline::LineBytes / sizeof(uint64_t);
         ++word) {
        const uint64_t value = first + word;
        std::memcpy(payload.data() + word * sizeof(value), &value,
                    sizeof(value));
    }
    return payload;
}

class BoundedCommitHarness
{
  public:
    static constexpr uint8_t CommitCount = Pipeline::LineBufferCount;
    static constexpr uint8_t DestinationPages = 2;
    using SPDPage =
        std::array<uint64_t, Pipeline::ProducerPageElements>;

    bool reserve(const Queue::Request &request, uint64_t readyTick,
                 uint8_t destination)
    {
        if (destination >= DestinationPages)
            return false;
        for (Slot &slot : slots) {
            if (slot.active)
                continue;
            slot.active = true;
            slot.readyTick = readyTick;
            slot.destination = destination;
            slot.request = request;
            return true;
        }
        return false;
    }

    uint16_t service(uint64_t now, Queue &queue,
                     const Queue::ContextKey &owner)
    {
        uint16_t committed = 0;
        const uint16_t pageLines = queue.producerPageLines(owner);
        for (Slot &slot : slots) {
            if (!slot.active || slot.readyTick > now)
                continue;
            const std::byte *payload = queue.bufferData(slot.request);
            CHECK(payload != nullptr);
            const uint16_t pageLine = slot.request.request.line % pageLines;
            const uint16_t firstElement =
                pageLine * Pipeline::LineBytes / sizeof(uint64_t);
            for (uint8_t word = 0;
                 word < Pipeline::LineBytes / sizeof(uint64_t); ++word) {
                uint64_t value = 0;
                std::memcpy(&value,
                            payload + word * sizeof(value), sizeof(value));
                pages[slot.destination][firstElement + word] = value;
                visible[slot.destination][firstElement + word] = true;
            }
            const Queue::Request request = slot.request;
            slot = {};
            CHECK(queue.completeMaterialize(request));
            ++committed;
        }
        return committed;
    }

    bool wordVisible(uint8_t destination, uint16_t element) const
    {
        return visible[destination][element];
    }

    uint64_t word(uint8_t destination, uint16_t element) const
    {
        return pages[destination][element];
    }

  private:
    struct Slot
    {
        bool active = false;
        uint64_t readyTick = 0;
        uint8_t destination = DestinationPages;
        Queue::Request request{};
    };

    std::array<Slot, CommitCount> slots{};
    std::array<SPDPage, DestinationPages> pages{};
    std::array<std::array<bool, Pipeline::ProducerPageElements>,
               DestinationPages>
        visible{};
};

void
testFourPageABIAndPreRegistrationRetry()
{
    constexpr uint64_t root = 0x800000;
    constexpr uint8_t wordBytes = 8;
    constexpr uint64_t pageBytes =
        uint64_t{Pipeline::ProducerPageElements} * wordBytes;
    uint64_t activationCount = 0;
    for (uint8_t expected = 0; expected < Pipeline::ProducerPages;
         ++expected) {
        const uint64_t beforeRetry = activationCount;
        uint8_t page = Pipeline::NoProducerPage;
        const auto retry = Pipeline::classifyMaterializationAdmission(
            true, 0, 0, root + expected * pageBytes, 0,
            Pipeline::ProducerPageElements, 1, wordBytes, &page);
        CHECK(retry == Pipeline::MaterializationAdmission::Retry);
        CHECK(page == Pipeline::NoProducerPage);
        CHECK(activationCount == beforeRetry);

        const auto accepted = Pipeline::classifyMaterializationAdmission(
            true, 9, root, root + expected * pageBytes, 0,
            Pipeline::ProducerPageElements, 1, wordBytes, &page);
        CHECK(accepted == Pipeline::MaterializationAdmission::Accepted);
        ++activationCount;
        CHECK(page == expected);
    }
    CHECK(activationCount == Pipeline::ProducerPages);

    uint8_t page = Pipeline::NoProducerPage;
    CHECK(Pipeline::classifyMaterializationAdmission(
              true, 9, root, root + pageBytes + Pipeline::LineBytes, 0,
              Pipeline::ProducerPageElements, 1, wordBytes, &page) ==
          Pipeline::MaterializationAdmission::Fallback);
    CHECK(Pipeline::classifyMaterializationAdmission(
              true, 9, root, root + pageBytes,
              Pipeline::ProducerPageElements,
              2 * Pipeline::ProducerPageElements, 1, wordBytes, &page) ==
          Pipeline::MaterializationAdmission::Fallback);
    CHECK(Pipeline::classifyMaterializationAdmission(
              false, 9, root, root, 0,
              Pipeline::ProducerPageElements, 1, wordBytes, &page) ==
          Pipeline::MaterializationAdmission::Fallback);
}

void
testBoundedEarlyWakeupMilestones()
{
    constexpr uint16_t pageLines =
        Pipeline::ProducerPageElements * sizeof(uint64_t) /
        Pipeline::LineBytes;
    static_assert(pageLines == 512);
    CHECK(!Pipeline::isEarlyWakeupLine(127, pageLines, 0));
    CHECK(!Pipeline::isEarlyWakeupLine(0, pageLines,
                                       Pipeline::MaxEarlyWakeupBatches + 1));

    // Four early batches select 1/5, 2/5, 3/5, and 4/5 completion
    // milestones. The final line is left to the existing page-ready wakeup.
    constexpr std::array<uint16_t, 4> expected{{102, 204, 307, 409}};
    for (uint16_t line = 0; line < pageLines; ++line) {
        bool selected = false;
        for (const uint16_t expectedLine : expected)
            selected = selected || line == expectedLine;
        CHECK(Pipeline::isEarlyWakeupLine(line, pageLines, 4) == selected);
    }
    CHECK(!Pipeline::isEarlyWakeupLine(pageLines - 1, pageLines, 4));
    CHECK(!Pipeline::isEarlyWakeupLine(pageLines, pageLines, 4));
}

void
testAuthenticatedMaskedFragmentAccumulation()
{
    Queue queue;
    const auto descriptor = materializerDescriptor();
    Queue::ContextKey owner;
    CHECK(queue.submit(descriptor, &owner) == Queue::SubmitResult::Accepted);
    CHECK(queue.beginMaterializationPage(owner, 0));

    auto low = linePayload(0x100);
    auto high = linePayload(0x200);
    const Pipeline::ProducerLineAck lowAck{
        owner.generation, 0, 0x0f, 0x500};
    CHECK(queue.notifyProducerLineWriteAck(owner, lowAck));
    Queue::Request captured;
    CHECK(queue.captureMaterializationFragment(
              owner, lowAck, low.data(), low.size(), 1, &captured) ==
          Pipeline::FragmentCapture::Accumulated);
    CHECK(captured.request.kind == Pipeline::Kind::None);
    CHECK(queue.pendingRead(owner).request.kind == Pipeline::Kind::None);

    // The exact ACK is single-use. A duplicate response cannot mutate the
    // retained payload or manufacture completion.
    CHECK(!queue.notifyProducerLineWriteAck(owner, lowAck));
    CHECK(queue.captureMaterializationFragment(
              owner, lowAck, high.data(), high.size(), 1, &captured) ==
          Pipeline::FragmentCapture::Ineligible);

    const Pipeline::ProducerLineAck highAck{
        owner.generation, 0, 0xf0, 0x501};
    CHECK(queue.notifyProducerLineWriteAck(owner, highAck));
    CHECK(queue.captureMaterializationFragment(
              owner, highAck, high.data(), high.size(), 1, &captured) ==
          Pipeline::FragmentCapture::Captured);
    CHECK(captured.request.kind == Pipeline::Kind::ReadBacking);

    BoundedCommitHarness commits;
    CHECK(commits.reserve(captured, 20, 0));
    CHECK(commits.service(19, queue, owner) == 0);
    CHECK(!commits.wordVisible(0, 0));
    CHECK(!commits.wordVisible(0, 7));
    CHECK(commits.service(20, queue, owner) == 1);
    for (uint8_t word = 0; word < 4; ++word)
        CHECK(commits.word(0, word) == uint64_t{0x100} + word);
    for (uint8_t word = 4; word < 8; ++word)
        CHECK(commits.word(0, word) == uint64_t{0x200} + word);

    // A missing final response payload cannot complete from stale bytes. The
    // partial buffer is abandoned and the now-ready line remains a coherent
    // backing-read candidate.
    const Pipeline::ProducerLineAck partial{
        owner.generation, 1, 0x0f, 0x510};
    CHECK(queue.notifyProducerLineWriteAck(owner, partial));
    CHECK(queue.captureMaterializationFragment(
              owner, partial, low.data(), low.size(), 1, &captured) ==
          Pipeline::FragmentCapture::Accumulated);
    const Pipeline::ProducerLineAck missingPayload{
        owner.generation, 1, 0xf0, 0x511};
    CHECK(queue.notifyProducerLineWriteAck(owner, missingPayload));
    CHECK(queue.captureMaterializationFragment(
              owner, missingPayload, nullptr, 0, 1, &captured) ==
          Pipeline::FragmentCapture::Ineligible);
    Queue::Snapshot snapshot;
    CHECK(queue.snapshot(owner, &snapshot));
    CHECK(snapshot.creditsInUse == 0);
    CHECK(queue.pendingRead(owner).request.line == 1);

    // If an earlier authenticated fragment was not retained, accepting the
    // final fragment must report an incomplete sequence, release its private
    // buffer, and leave the line on coherent fallback. This is not a buffer
    // capacity stall.
    const Pipeline::ProducerLineAck missed{
        owner.generation, 2, 0x0f, 0x520};
    CHECK(queue.notifyProducerLineWriteAck(owner, missed));
    const Pipeline::ProducerLineAck finalOnly{
        owner.generation, 2, 0xf0, 0x521};
    CHECK(queue.notifyProducerLineWriteAck(owner, finalOnly));
    CHECK(queue.captureMaterializationFragment(
              owner, finalOnly, high.data(), high.size(), 1, &captured) ==
          Pipeline::FragmentCapture::Incomplete);
    CHECK(queue.snapshot(owner, &snapshot));
    CHECK(snapshot.creditsInUse == 0);
    CHECK(queue.pendingRead(owner).request.line == 1 ||
          queue.pendingRead(owner).request.line == 2);
}

void
testMaskedFragmentBoundAndFallbackSafety()
{
    Queue queue;
    const auto descriptor = materializerDescriptor();
    Queue::ContextKey owner;
    CHECK(queue.submit(descriptor, &owner) == Queue::SubmitResult::Accepted);
    CHECK(queue.beginMaterializationPage(owner, 0));
    const auto payload = linePayload(0x300);

    constexpr uint8_t limit = 2;
    for (uint16_t line = 0; line < limit; ++line) {
        const Pipeline::ProducerLineAck ack{
            owner.generation, line, 0x01,
            static_cast<uint64_t>(0x600 + line)};
        CHECK(queue.notifyProducerLineWriteAck(owner, ack));
        Queue::Request captured;
        CHECK(queue.captureMaterializationFragment(
                  owner, ack, payload.data(), payload.size(), limit,
                  &captured) == Pipeline::FragmentCapture::Accumulated);
    }
    const Pipeline::ProducerLineAck overflow{
        owner.generation, limit, 0x01, 0x700};
    CHECK(queue.notifyProducerLineWriteAck(owner, overflow));
    Queue::Request captured;
    CHECK(queue.captureMaterializationFragment(
              owner, overflow, payload.data(), payload.size(), limit,
              &captured) == Pipeline::FragmentCapture::NoBuffer);

    // Final page authority discards incomplete private fragments and exposes
    // all still-blocked lines only through the coherent cache-read path.
    CHECK(queue.notifyProducerWriteAck(
        owner, {owner.generation, 0, 0x800}));
    Queue::Snapshot snapshot;
    CHECK(queue.snapshot(owner, &snapshot));
    CHECK(snapshot.creditsInUse == 0);
    CHECK(queue.pendingRead(owner).request.kind ==
          Pipeline::Kind::ReadBacking);
}

void
testMaskedFragmentDefaultOffPreservesCacheFallback()
{
    Queue queue;
    const auto descriptor = materializerDescriptor();
    Queue::ContextKey owner;
    CHECK(queue.submit(descriptor, &owner) == Queue::SubmitResult::Accepted);
    CHECK(queue.beginMaterializationPage(owner, 0));
    const auto payload = linePayload(0x400);
    Queue::Request captured;
    for (const auto ack : {
             Pipeline::ProducerLineAck{owner.generation, 0, 0x0f, 0x900},
             Pipeline::ProducerLineAck{owner.generation, 0, 0xf0, 0x901}}) {
        CHECK(queue.notifyProducerLineWriteAck(owner, ack));
        CHECK(queue.captureMaterializationFragment(
                  owner, ack, payload.data(), payload.size(), 0,
                  &captured) == Pipeline::FragmentCapture::Disabled);
    }
    Queue::Snapshot snapshot;
    CHECK(queue.snapshot(owner, &snapshot));
    CHECK(snapshot.creditsInUse == 0);
    CHECK(snapshot.producerLineAcks == 1);
    CHECK(queue.pendingRead(owner).request.line == 0);
}

void
makeMaterializationLineReady(Queue &queue, const Queue::ContextKey &owner,
                             uint16_t line, uint64_t transaction)
{
    const Pipeline::ProducerLineAck ack{
        owner.generation, line, 0xff, transaction};
    CHECK(queue.notifyProducerLineWriteAck(owner, ack));
}

void
testInactiveLookupMissRebindsAfterUnrelatedCapture()
{
    Queue queue;
    Queue::ContextKey owner;
    CHECK(queue.submit(materializerDescriptor(), &owner) ==
          Queue::SubmitResult::Accepted);
    CHECK(queue.beginMaterializationPage(owner, 0));
    makeMaterializationLineReady(queue, owner, 0, 0x1000);
    const Queue::Request stale = queue.pendingRead(owner);

    // The one-cycle lookup does not reserve stale.buffer.  An unrelated
    // producer response can use it before the miss reaches cache fallback.
    makeMaterializationLineReady(queue, owner, 1, 0x1001);
    const auto bytes = linePayload(0x810);
    Queue::Request unrelated;
    CHECK(queue.captureMaterializationLine(owner, 1, bytes.data(),
                                           bytes.size(), &unrelated));
    CHECK(unrelated.request.buffer == stale.request.buffer);

    Queue::Request rebound;
    CHECK(queue.rebindMaterializationRead(stale, &rebound) ==
          Queue::MaterializationReadRebind::Rebound);
    CHECK(rebound.owner.tokenTile == stale.owner.tokenTile);
    CHECK(rebound.owner.generation == stale.owner.generation);
    CHECK(rebound.owner.incarnation == stale.owner.incarnation);
    CHECK(rebound.request.line == stale.request.line);
    CHECK(rebound.request.address == stale.request.address);
    CHECK(rebound.request.transactionID == stale.request.transactionID);
    CHECK(rebound.request.buffer != stale.request.buffer);
}

void
testInactiveLookupMissDiscardsSameLineDirectCapture()
{
    Queue queue;
    Queue::ContextKey owner;
    CHECK(queue.submit(materializerDescriptor(), &owner) ==
          Queue::SubmitResult::Accepted);
    CHECK(queue.beginMaterializationPage(owner, 0));
    makeMaterializationLineReady(queue, owner, 3, 0x1010);
    const Queue::Request stale = queue.pendingRead(owner);
    const auto bytes = linePayload(0x820);
    Queue::Request captured;

    // This models the retained-payload hit path: it remains direct capture
    // and closes the line before a concurrently completed miss may issue.
    CHECK(queue.captureMaterializationLine(owner, 3, bytes.data(),
                                           bytes.size(), &captured));
    Queue::Request rebound;
    CHECK(queue.rebindMaterializationRead(stale, &rebound) ==
          Queue::MaterializationReadRebind::Closed);
    CHECK(rebound.request.kind == Pipeline::Kind::None);
}

void
testInactiveLookupMissWaitsForFreshCredit()
{
    Queue queue;
    Queue::ContextKey owner;
    CHECK(queue.submit(materializerDescriptor(), &owner) ==
          Queue::SubmitResult::Accepted);
    CHECK(queue.beginMaterializationPage(owner, 0));
    makeMaterializationLineReady(queue, owner, 0, 0x1020);
    const Queue::Request stale = queue.pendingRead(owner);
    const auto bytes = linePayload(0x830);
    std::array<Queue::Request, Pipeline::LineBufferCount> captured{};
    for (uint16_t line = 1; line <= Pipeline::LineBufferCount; ++line) {
        makeMaterializationLineReady(queue, owner, line, 0x1020 + line);
        CHECK(queue.captureMaterializationLine(owner, line, bytes.data(),
                                               bytes.size(),
                                               &captured[line - 1]));
    }

    Queue::Request rebound;
    CHECK(queue.rebindMaterializationRead(stale, &rebound) ==
          Queue::MaterializationReadRebind::NoCredit);
    CHECK(queue.completeMaterialize(captured[0]));
    CHECK(queue.rebindMaterializationRead(stale, &rebound) ==
          Queue::MaterializationReadRebind::Rebound);
    CHECK(rebound.request.line == 0);
}

void
testInactiveLookupMissRebindClosesExactPacketRequest()
{
    Queue queue;
    Queue::ContextKey owner;
    CHECK(queue.submit(materializerDescriptor(), &owner) ==
          Queue::SubmitResult::Accepted);
    CHECK(queue.beginMaterializationPage(owner, 0));
    makeMaterializationLineReady(queue, owner, 0, 0x1030);
    const Queue::Request stale = queue.pendingRead(owner);
    const auto bytes = linePayload(0x840);
    Queue::Request unrelated;
    makeMaterializationLineReady(queue, owner, 1, 0x1031);
    CHECK(queue.captureMaterializationLine(owner, 1, bytes.data(),
                                           bytes.size(), &unrelated));

    Queue::Request rebound;
    CHECK(queue.rebindMaterializationRead(stale, &rebound) ==
          Queue::MaterializationReadRebind::Rebound);
    CHECK(queue.accept(rebound));
    CHECK(!queue.accept(stale));
    CHECK(queue.completeRead(rebound, bytes.data(), bytes.size()));
    CHECK(queue.completeMaterialize(rebound));
    Queue::Snapshot snapshot;
    CHECK(queue.snapshot(owner, &snapshot));
    CHECK(snapshot.readsAccepted == 2);
    CHECK(snapshot.completed == 1);
}

void
driveCacheReadPage(Queue &queue, const Queue::ContextKey &owner,
                   uint8_t destination, uint64_t &tick,
                   BoundedCommitHarness &commits)
{
    const uint8_t page = queue.materializationPage(owner);
    CHECK(page < Pipeline::ProducerPages);
    while (!queue.materializationPageComplete(owner, page)) {
        const auto request = queue.pendingRead(owner);
        CHECK(request.request.kind == Pipeline::Kind::ReadBacking);
        CHECK(queue.accept(request));
        const auto payload = linePayload(request.request.line + 1);
        CHECK(queue.completeRead(request, payload.data(), payload.size()));
        CHECK(commits.reserve(request, ++tick, destination));
        CHECK(commits.service(tick, queue, owner) == 1);
    }
}

void
testLateAckForwardingCommitTickAndTwoChargedPages()
{
    Queue queue;
    const auto descriptor = materializerDescriptor();
    Queue::ContextKey owner;
    CHECK(queue.submit(descriptor, &owner) == Queue::SubmitResult::Accepted);
    CHECK(queue.mode(owner) == Pipeline::Mode::MaterializePages);
    CHECK(queue.beginMaterializationPage(owner, 0));
    CHECK(queue.pendingRead(owner).request.kind == Pipeline::Kind::None);

    // Admission precedes the producer ACK. The exact full-line WriteResp can
    // be copied into one charged queue buffer, but not made SPD-visible until
    // the separately reserved SPD-port completion tick.
    const Pipeline::ProducerLineAck lateLine{
        owner.generation, 0, 0xff, 0xabc};
    CHECK(queue.notifyProducerLineWriteAck(owner, lateLine));
    auto forwardedPayload = linePayload(0x100);
    Queue::Request forwarded;
    CHECK(queue.captureMaterializationLine(
        owner, 0, forwardedPayload.data(), forwardedPayload.size(),
        &forwarded));
    BoundedCommitHarness commits;
    CHECK(commits.reserve(forwarded, 50, 0));
    forwardedPayload.fill(std::byte{0});
    CHECK(commits.service(49, queue, owner) == 0);
    CHECK(!commits.wordVisible(0, 0));
    CHECK(!queue.materializationPageComplete(owner, 0));
    CHECK(commits.service(50, queue, owner) == 1);
    CHECK(commits.wordVisible(0, 0));
    CHECK(commits.word(0, 0) == 0x100);

    // A later whole-page WriteResp is only fallback authority for lines not
    // already made visible by exact line ACKs.
    CHECK(queue.notifyProducerWriteAck(
        owner, {owner.generation, 0, 0xdef}));
    uint64_t tick = 50;
    driveCacheReadPage(queue, owner, 0, tick, commits);
    CHECK(queue.materializationPageComplete(owner, 0));

    // The same 16K token/generation lifetime materializes a second ordinary
    // physical SPD page. Both destinations are real, separately charged SPD
    // storage; the mechanism does not instantiate another STREAM unit.
    CHECK(queue.beginMaterializationPage(owner, 1));
    CHECK(queue.pendingRead(owner).request.kind == Pipeline::Kind::None);
    CHECK(queue.notifyProducerWriteAck(
        owner, {owner.generation, 1, 0x1234}));
    driveCacheReadPage(queue, owner, 1, tick, commits);
    CHECK(queue.materializationPageComplete(owner, 1));
    CHECK(commits.wordVisible(1, 0));

    // Four producer pages retain one exact 16K reorder lifetime while two
    // ordinary charged SPD pages alternate as downstream consumers release
    // them. No third page or second STREAM execution resource is implied.
    for (uint8_t page = 2; page < Pipeline::ProducerPages; ++page) {
        CHECK(queue.beginMaterializationPage(owner, page));
        CHECK(queue.notifyProducerWriteAck(
            owner, {owner.generation, page,
                    static_cast<uint64_t>(0x2000 + page)}));
        driveCacheReadPage(queue, owner, page % 2, tick, commits);
        CHECK(queue.materializationPageComplete(owner, page));
    }

    Queue::Snapshot snapshot;
    CHECK(queue.snapshot(owner, &snapshot));
    CHECK(snapshot.complete);
    CHECK(snapshot.completed == snapshot.lines);
    CHECK(snapshot.producerLineAcks == 1);
    CHECK(snapshot.producerPageFallbackLines ==
          snapshot.lines - 1);
    CHECK(snapshot.readsAccepted == snapshot.lines);
    CHECK(snapshot.creditsInUse == 0);
    CHECK(Queue::chargedPayloadBytes() == 4096);
    constexpr uint64_t onePageBytes =
        uint64_t{Pipeline::ProducerPageElements} * sizeof(uint64_t);
    CHECK(2 * onePageBytes == 65536);
    CHECK(queue.retire(owner));

    std::cout << "page materializer late_ack=1 commit_tick=50 "
              << "queue_payload_bytes=" << Queue::chargedPayloadBytes()
              << " charged_two_page_spd_bytes=" << 2 * onePageBytes
              << " submissions=4 pages=4 retirements=1 "
              << "forwarded_lines=1 cache_read_fallback_lines="
              << snapshot.lines - 1
              << " dispatch_fallbacks=0 exact_closure=1 "
              << "stream_units_added=0\n";
}

} // anonymous namespace

int
main()
{
    testFourPageABIAndPreRegistrationRetry();
    testBoundedEarlyWakeupMilestones();
    testAuthenticatedMaskedFragmentAccumulation();
    testMaskedFragmentBoundAndFallbackSafety();
    testMaskedFragmentDefaultOffPreservesCacheFallback();
    testInactiveLookupMissRebindsAfterUnrelatedCapture();
    testInactiveLookupMissDiscardsSameLineDirectCapture();
    testInactiveLookupMissWaitsForFreshCredit();
    testInactiveLookupMissRebindClosesExactPacketRequest();
    testLateAckForwardingCommitTickAndTwoChargedPages();
    std::cout << "hybrid page materializer tests passed\n";
    return 0;
}
