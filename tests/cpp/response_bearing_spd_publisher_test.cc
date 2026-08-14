#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <type_traits>

#include "mem/MAA/ResponseBearingSpdPublisher.hh"

namespace
{

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << '\n';             \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

using Publisher = gem5::ResponseBearingSpdPublisher<4, 4, 4>;
using OnePagePublisher = gem5::ResponseBearingSpdPublisher<4, 1, 2>;
using Fp64Publisher = gem5::ResponseBearingSpdPublisher<8, 1, 4>;

Publisher::Payload
payload(uint8_t seed)
{
    Publisher::Payload result{};
    for (std::size_t i = 0; i < result.size(); ++i)
        result[i] = std::byte{static_cast<uint8_t>(seed + i)};
    return result;
}

bool
sameIdentity(const Publisher::Identity &left,
             const Publisher::Identity &right)
{
    return left.owner == right.owner &&
           left.generation == right.generation &&
           left.address == right.address && left.page == right.page &&
           left.line == right.line;
}

template <class P>
typename P::Payload
linePayload(uint16_t ordinal, uint64_t generation)
{
    typename P::Payload result{};
    for (std::size_t byte = 0; byte < result.size(); ++byte) {
        result[byte] = std::byte{static_cast<uint8_t>(
            ordinal * 17 + generation * 3 + byte)};
    }
    return result;
}

template <class P>
void
publishRemaining(P &publisher)
{
    while (!publisher.complete()) {
        while (publisher.enqueuedLines() < publisher.expectedLines() &&
               publisher.occupiedCredits() < P::Credits) {
            const uint16_t ordinal = publisher.enqueuedLines();
            const std::size_t page = ordinal / P::LinesPerPage;
            const std::size_t line = ordinal % P::LinesPerPage;
            auto bytes = linePayload<P>(ordinal, publisher.generation());
            CHECK(publisher.enqueue(publisher.identity(page, line),
                                    bytes.data(), bytes.size()) ==
                  P::EnqueueResult::Accepted);
        }

        typename P::Request request;
        if (publisher.prepareRequest(&request) ==
            P::RequestResult::Prepared) {
            CHECK(publisher.recordSend(request, true) ==
                  P::SendResult::Accepted);
            CHECK(publisher.acknowledge({request.identity, true}) ==
                  P::AckResult::Accepted);
        }
        CHECK(publisher.assertInvariants());
    }
}

void
testSuccessAndResetOnlyWhenComplete()
{
    OnePagePublisher publisher;
    CHECK(publisher.begin(7, 1, 0x100000, 1) ==
          OnePagePublisher::BeginResult::Started);
    CHECK(OnePagePublisher::PageElements == 4096);
    CHECK(OnePagePublisher::PageBytes == 16 * 1024);
    CHECK(OnePagePublisher::LinesPerPage == 256);
    CHECK(publisher.expectedLines() == 256);
    CHECK(publisher.empty());
    CHECK(publisher.reset() == OnePagePublisher::ResetResult::Incomplete);

    auto bytes = linePayload<OnePagePublisher>(0, 1);
    CHECK(publisher.enqueue(publisher.identity(0, 0), bytes.data(),
                            bytes.size()) ==
          OnePagePublisher::EnqueueResult::Accepted);
    CHECK(publisher.reset() == OnePagePublisher::ResetResult::NotEmpty);
    publishRemaining(publisher);
    CHECK(publisher.complete());
    CHECK(publisher.acknowledgedLines() == publisher.expectedLines());
    CHECK(publisher.reset() == OnePagePublisher::ResetResult::Reset);
    CHECK(!publisher.active());
    CHECK(publisher.owner() == 7);
    CHECK(publisher.assertInvariants());
}

void
testBackpressureRetryAndPayloadPersistence()
{
    Publisher publisher;
    CHECK(publisher.begin(11, 3, 0x200000, 1) ==
          Publisher::BeginResult::Started);
    auto source = payload(0x20);
    const auto exactCopy = source;
    const auto identity = publisher.identity(0, 0);
    CHECK(publisher.enqueue(identity, source.data(), source.size()) ==
          Publisher::EnqueueResult::Accepted);
    source.fill(std::byte{0xff});

    Publisher::Request request;
    CHECK(publisher.prepareRequest(&request) ==
          Publisher::RequestResult::Prepared);
    CHECK(request.payload != source.data());
    CHECK(std::memcmp(request.payload, exactCopy.data(), exactCopy.size()) ==
          0);
    CHECK(publisher.recordSend(request, false) ==
          Publisher::SendResult::Backpressured);
    CHECK(publisher.retryPending());
    CHECK(publisher.prepareRequest(&request) ==
          Publisher::RequestResult::RetryBlocked);

    Publisher::Request retry;
    CHECK(publisher.retryRequest(&retry) == Publisher::RetryResult::Prepared);
    CHECK(sameIdentity(retry.identity, identity));
    CHECK(std::memcmp(retry.payload, exactCopy.data(), exactCopy.size()) == 0);
    CHECK(publisher.recordSend(retry, false) ==
          Publisher::SendResult::Backpressured);
    CHECK(publisher.retryRequest(&retry) == Publisher::RetryResult::Prepared);
    CHECK(std::memcmp(retry.payload, exactCopy.data(), exactCopy.size()) == 0);
    CHECK(publisher.recordSend(retry, true) ==
          Publisher::SendResult::Accepted);

    Publisher::Request retained;
    CHECK(publisher.retainedRequest(identity, &retained));
    CHECK(std::memcmp(retained.payload, exactCopy.data(), exactCopy.size()) ==
          0);
    CHECK(publisher.acknowledge({identity, true}) ==
          Publisher::AckResult::Accepted);
    CHECK(!publisher.retainedRequest(identity, &retained));
    CHECK(publisher.assertInvariants());
}

void
testOutOfOrderResponsesAndAllLineCompletion()
{
    Publisher publisher;
    CHECK(publisher.begin(13, 9, 0x300000, 1) ==
          Publisher::BeginResult::Started);
    std::array<Publisher::Identity, Publisher::Credits> issued{};
    for (std::size_t line = 0; line < Publisher::Credits; ++line) {
        auto bytes = linePayload<Publisher>(line, publisher.generation());
        issued[line] = publisher.identity(0, line);
        CHECK(publisher.enqueue(issued[line], bytes.data(), bytes.size()) ==
              Publisher::EnqueueResult::Accepted);
        Publisher::Request request;
        CHECK(publisher.prepareRequest(&request) ==
              Publisher::RequestResult::Prepared);
        CHECK(publisher.recordSend(request, true) ==
              Publisher::SendResult::Accepted);
    }
    auto blockedBytes =
        linePayload<Publisher>(Publisher::Credits, publisher.generation());
    CHECK(publisher.enqueue(publisher.identity(0, Publisher::Credits),
                            blockedBytes.data(), blockedBytes.size()) ==
          Publisher::EnqueueResult::Full);
    CHECK(publisher.enqueuedLines() == Publisher::Credits);
    const std::array<std::size_t, Publisher::Credits> order = {2, 0, 3, 1};
    for (const std::size_t line : order) {
        CHECK(publisher.acknowledge({issued[line], true}) ==
              Publisher::AckResult::Accepted);
    }
    CHECK(!publisher.complete());
    CHECK(publisher.acknowledgedLines() == Publisher::Credits);
    publishRemaining(publisher);
    CHECK(publisher.complete());
    CHECK(publisher.assertInvariants());
}

void
testDuplicateStaleAndWrongResponseRejection()
{
    Publisher publisher;
    CHECK(publisher.begin(17, 12, 0x400000, 2) ==
          Publisher::BeginResult::Started);
    auto bytes = payload(0x40);
    const auto exact = publisher.identity(0, 0);
    CHECK(publisher.enqueue(exact, bytes.data(), bytes.size()) ==
          Publisher::EnqueueResult::Accepted);
    Publisher::Request request;
    CHECK(publisher.prepareRequest(&request) ==
          Publisher::RequestResult::Prepared);
    CHECK(publisher.recordSend(request, true) ==
          Publisher::SendResult::Accepted);

    const auto acknowledgedBefore = publisher.acknowledgedLines();
    auto response = Publisher::WriteResponse{exact, false};
    CHECK(publisher.acknowledge(response) ==
          Publisher::AckResult::NotWriteResponse);
    response = {exact, true};
    ++response.identity.owner;
    CHECK(publisher.acknowledge(response) == Publisher::AckResult::WrongOwner);
    response = {exact, true};
    --response.identity.generation;
    CHECK(publisher.acknowledge(response) ==
          Publisher::AckResult::StaleGeneration);
    response = {exact, true};
    ++response.identity.generation;
    CHECK(publisher.acknowledge(response) ==
          Publisher::AckResult::WrongGeneration);
    response = {exact, true};
    response.identity.page = publisher.pages();
    CHECK(publisher.acknowledge(response) == Publisher::AckResult::WrongPage);
    response = {exact, true};
    response.identity.line = Publisher::LinesPerPage;
    CHECK(publisher.acknowledge(response) == Publisher::AckResult::WrongLine);
    response = {exact, true};
    response.identity.address += Publisher::LineBytes;
    CHECK(publisher.acknowledge(response) ==
          Publisher::AckResult::WrongAddress);
    response = {publisher.identity(0, 1), true};
    CHECK(publisher.acknowledge(response) ==
          Publisher::AckResult::NotOutstanding);
    CHECK(publisher.acknowledgedLines() == acknowledgedBefore);
    Publisher::Request retained;
    CHECK(publisher.retainedRequest(exact, &retained));
    CHECK(std::memcmp(retained.payload, bytes.data(), bytes.size()) == 0);

    CHECK(publisher.acknowledge({exact, true}) ==
          Publisher::AckResult::Accepted);
    CHECK(publisher.acknowledge({exact, true}) ==
          Publisher::AckResult::NotOutstanding);
    CHECK(publisher.acknowledgedLines() == acknowledgedBefore + 1);
    CHECK(publisher.assertInvariants());
}

void
testGenerationReuseIsRejectedAndOldResponseStaysStale()
{
    OnePagePublisher publisher;
    CHECK(publisher.begin(23, 100, 0x500000, 1) ==
          OnePagePublisher::BeginResult::Started);
    const auto oldIdentity = publisher.identity(0, 0);
    publishRemaining(publisher);
    CHECK(publisher.reset() == OnePagePublisher::ResetResult::Reset);

    CHECK(publisher.begin(23, 100, 0x500000, 1) ==
          OnePagePublisher::BeginResult::InvalidGeneration);
    CHECK(publisher.begin(23, 99, 0x500000, 1) ==
          OnePagePublisher::BeginResult::InvalidGeneration);
    CHECK(publisher.begin(24, 101, 0x500000, 1) ==
          OnePagePublisher::BeginResult::InvalidOwner);
    CHECK(publisher.begin(23, 101, 0x500000, 1) ==
          OnePagePublisher::BeginResult::Started);

    auto bytes = linePayload<OnePagePublisher>(0, 101);
    const auto currentIdentity = publisher.identity(0, 0);
    CHECK(publisher.enqueue(currentIdentity, bytes.data(), bytes.size()) ==
          OnePagePublisher::EnqueueResult::Accepted);
    OnePagePublisher::Request request;
    CHECK(publisher.prepareRequest(&request) ==
          OnePagePublisher::RequestResult::Prepared);
    CHECK(publisher.recordSend(request, true) ==
          OnePagePublisher::SendResult::Accepted);
    CHECK(publisher.acknowledge({oldIdentity, true}) ==
          OnePagePublisher::AckResult::StaleGeneration);
    CHECK(publisher.acknowledge({currentIdentity, true}) ==
          OnePagePublisher::AckResult::Accepted);
    CHECK(publisher.assertInvariants());
}

void
testFp32FourPageIdentityAndChargedByteAccounting()
{
    Publisher publisher;
    CHECK(publisher.begin(31, 1, 0x600000, 4) ==
          Publisher::BeginResult::Started);
    CHECK(publisher.expectedLines() == 4 * Publisher::LinesPerPage);
    const auto last = publisher.identity(3, Publisher::LinesPerPage - 1);
    CHECK(last.page == 3);
    CHECK(last.line == Publisher::LinesPerPage - 1);
    CHECK(last.address == 0x600000 + 4 * Publisher::PageBytes -
                              Publisher::LineBytes);

    static_assert(std::is_trivially_copyable<Publisher>::value);
    static_assert(Publisher::chargedPayloadBytes() ==
                  Publisher::Credits * Publisher::LineBytes);
    static_assert(Publisher::chargedBytes() == sizeof(Publisher));
    static_assert(Publisher::chargedControlBytes() +
                      Publisher::chargedPayloadBytes() ==
                  Publisher::chargedBytes());
    static_assert(Publisher::chargedPayloadBytes() == 256);
    static_assert(Publisher::chargedControlBytes() == 248);
    static_assert(Publisher::chargedBytes() == 504);
    CHECK(Publisher::chargedPayloadBytes() == 256);
    CHECK(Publisher::chargedBytes() != 4 * Publisher::PageBytes);
    publishRemaining(publisher);
    CHECK(publisher.acknowledgedLines() == 4 * Publisher::LinesPerPage);
    CHECK(publisher.complete());
    std::cout << "response-bearing SPD publisher credits="
              << Publisher::Credits
              << " payload_bytes=" << Publisher::chargedPayloadBytes()
              << " control_bytes=" << Publisher::chargedControlBytes()
              << " total_bytes=" << Publisher::chargedBytes() << '\n';
}

void
testFp64GeometryAlignmentCompletionAndAccounting()
{
    Fp64Publisher publisher;
    Fp64Publisher misaligned;
    constexpr uint64_t base = 0x700040;
    static_assert(Fp64Publisher::WordBytes == 8);
    static_assert(Fp64Publisher::PageElements == 4096);
    static_assert(Fp64Publisher::PageBytes == 32 * 1024);
    static_assert(Fp64Publisher::LinesPerPage == 512);
    CHECK(base % Fp64Publisher::LineBytes == 0);
    CHECK(base % Fp64Publisher::PageBytes != 0);
    CHECK(misaligned.begin(37, 1, base + 1, 1) ==
          Fp64Publisher::BeginResult::InvalidBaseAddress);
    CHECK(publisher.begin(37, 1, base, 1) ==
          Fp64Publisher::BeginResult::Started);
    CHECK(publisher.expectedLines() == 512);
    const auto last =
        publisher.identity(0, Fp64Publisher::LinesPerPage - 1);
    CHECK(last.line == 511);
    CHECK(last.address == base + Fp64Publisher::PageBytes -
                              Fp64Publisher::LineBytes);
    publishRemaining(publisher);
    CHECK(publisher.acknowledgedLines() == 512);
    CHECK(publisher.complete());

    static_assert(Fp64Publisher::chargedPayloadBytes() ==
                  Fp64Publisher::Credits * Fp64Publisher::LineBytes);
    static_assert(Fp64Publisher::chargedPayloadBytes() == 256);
    static_assert(Fp64Publisher::chargedControlBytes() == 248);
    static_assert(Fp64Publisher::chargedBytes() == 504);
    CHECK(Fp64Publisher::chargedBytes() != Fp64Publisher::PageBytes);
    std::cout << "FP64 response-bearing SPD publisher lines_per_page="
              << Fp64Publisher::LinesPerPage
              << " payload_bytes=" << Fp64Publisher::chargedPayloadBytes()
              << " control_bytes=" << Fp64Publisher::chargedControlBytes()
              << " total_bytes=" << Fp64Publisher::chargedBytes() << '\n';
}

} // anonymous namespace

int
main()
{
    testSuccessAndResetOnlyWhenComplete();
    testBackpressureRetryAndPayloadPersistence();
    testOutOfOrderResponsesAndAllLineCompletion();
    testDuplicateStaleAndWrongResponseRejection();
    testGenerationReuseIsRejectedAndOldResponseStaysStale();
    testFp32FourPageIdentityAndChargedByteAccounting();
    testFp64GeometryAlignmentCompletionAndAccounting();
    std::cout << "response-bearing SPD publisher tests passed\n";
    return 0;
}
