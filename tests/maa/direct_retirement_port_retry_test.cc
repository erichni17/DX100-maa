#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>

#include "mem/MAA/DirectRetirementPortRetry.hh"
#include "mem/MAA/HybridConsumerContextQueue.hh"

namespace {

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << '\n';             \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

using Queue = gem5::HybridConsumerContextQueue;
using Pipeline = Queue::Pipeline;

struct RetryPacket
{
    Queue::Request request{};
    uint8_t callbackPort = Pipeline::PortCount;
};

using Retry = gem5::DirectRetirementPortRetry<RetryPacket>;

Queue::Descriptor
descriptor(uint16_t token_tile, uint64_t base)
{
    Queue::Descriptor result;
    result.tokenTile = token_tile;
    result.consumer.generation = token_tile + 1;
    result.consumer.logicalElements = Pipeline::LogicalElements;
    result.consumer.wordBytes = sizeof(double);
    result.consumer.backingAddress = base;
    result.consumer.backingRangeMin = base;
    result.consumer.backingRangeMax = base + 0x40000;
    result.consumer.backingRangeID = 1;
    result.consumer.destinationAddress = base + 0x80000;
    result.consumer.destinationRangeMin = base + 0x80000;
    result.consumer.destinationRangeMax = base + 0xc0000;
    result.consumer.destinationRangeID = 2;
    const uint64_t transaction_base = 100 + uint64_t{token_tile} * 4;
    result.consumer.producerTransactions = {
        {transaction_base, transaction_base + 1,
         transaction_base + 2, transaction_base + 3}};
    return result;
}

Pipeline::ProducerLineAck
lineAck(const Queue::Descriptor &descriptor)
{
    return {descriptor.consumer.generation, 0, 0xff, 0x1000};
}

std::array<std::byte, Pipeline::LineBytes>
payload(uint8_t value)
{
    std::array<std::byte, Pipeline::LineBytes> result{};
    result.fill(std::byte{value});
    return result;
}

void
testExactFixedSlotOwnership()
{
    Retry retry;
    std::array<RetryPacket, Retry::PortCount + 1> packets{};

    CHECK(!retry.arm(Retry::PortCount, &packets[0]));
    CHECK(!retry.arm(0, nullptr));
    CHECK(retry.arm(0, &packets[0]));
    CHECK(!retry.arm(0, &packets[1]));
    CHECK(retry.packet(0) == &packets[0]);
    CHECK(retry.count() == 1);

    for (uint8_t port = 1; port < Retry::PortCount; ++port)
        CHECK(retry.arm(port, &packets[port]));
    CHECK(retry.count() == Retry::PortCount);

    CHECK(!retry.release(1, &packets[0]));
    CHECK(!retry.release(0, &packets[1]));
    CHECK(retry.release(0, &packets[0]));
    CHECK(!retry.occupied(0));
    CHECK(retry.count() == Retry::PortCount - 1);
    for (uint8_t port = 1; port < Retry::PortCount; ++port)
        CHECK(retry.release(port, &packets[port]));
    CHECK(retry.count() == 0);
}

void
testBlockedBankDoesNotStopAnotherBank()
{
    Queue queue;
    // Contexts zero and one translate to bank zero; context two translates
    // to bank one. All three have the same first line ready.
    const std::array<Queue::Descriptor, 3> descriptors = {
        descriptor(0, 0x100000), descriptor(1, 0x400000),
        descriptor(2, 0x800040)};
    std::array<Queue::ContextKey, 3> keys{};
    for (std::size_t index = 0; index < descriptors.size(); ++index) {
        CHECK(queue.submit(descriptors[index], &keys[index]) ==
              Queue::SubmitResult::Accepted);
        CHECK(queue.notifyProducerLineWriteAck(
            keys[index], lineAck(descriptors[index])));
    }

    Retry retry;
    const Queue::Request blocked = queue.pendingRead();
    CHECK(blocked.owner.incarnation == keys[0].incarnation);
    CHECK(blocked.request.port == 0);
    RetryPacket blocked_packet{blocked, 0};
    CHECK(retry.arm(blocked_packet.callbackPort, &blocked_packet));
    // A refused packet owns the context credit while its exact packet waits.
    CHECK(queue.accept(blocked));

    const Queue::Request contender = queue.pendingRead();
    CHECK(contender.owner.incarnation == keys[1].incarnation);
    CHECK(contender.request.port == 0);
    RetryPacket contender_packet{contender, 0};
    CHECK(!retry.arm(contender_packet.callbackPort, &contender_packet));

    auto stale_owner = contender;
    ++stale_owner.owner.incarnation;
    CHECK(!queue.defer(stale_owner));
    auto stale_request = contender;
    ++stale_request.request.transactionID;
    CHECK(!queue.defer(stale_request));
    CHECK(queue.defer(contender));

    const Queue::Request eligible = queue.pendingRead();
    CHECK(eligible.owner.incarnation == keys[2].incarnation);
    CHECK(eligible.request.port == 1);
    CHECK(queue.accept(eligible));
    const auto eligible_payload = payload(0x5a);
    CHECK(queue.completeRead(eligible, eligible_payload.data(),
                             eligible_payload.size()));
    CHECK(retry.packet(0) == &blocked_packet);
    CHECK(retry.count() == 1);

    CHECK(!retry.release(1, &blocked_packet));
    CHECK(retry.release(0, &blocked_packet));
    const auto blocked_payload = payload(0xa5);
    CHECK(queue.completeRead(blocked, blocked_payload.data(),
                             blocked_payload.size()));
    CHECK(queue.assertInvariants());
}

void
testByteAccounting()
{
    static_assert(Retry::PortCount == 4);
    static_assert(Retry::chargedControlBytes() ==
                  Retry::PortCount * sizeof(RetryPacket *));
    static_assert(sizeof(Retry) == Retry::chargedControlBytes());
    CHECK(Retry::chargedControlBytes() == 4 * sizeof(RetryPacket *));
    std::cout << "direct retirement retry slots="
              << static_cast<unsigned>(Retry::PortCount)
              << " pointer_bytes=" << sizeof(RetryPacket *)
              << " control_bytes=" << Retry::chargedControlBytes() << '\n';
}

} // anonymous namespace

int
main()
{
    testExactFixedSlotOwnership();
    testBlockedBankDoesNotStopAnotherBank();
    testByteAccounting();
    std::cout << "direct retirement per-port retry tests passed\n";
    return 0;
}
