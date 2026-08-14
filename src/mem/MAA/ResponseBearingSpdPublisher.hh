#ifndef __MEM_MAA_RESPONSE_BEARING_SPD_PUBLISHER_HH__
#define __MEM_MAA_RESPONSE_BEARING_SPD_PUBLISHER_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

namespace gem5
{

/**
 * Bounded state for publishing complete SPD pages through timing writes.
 *
 * One instance is permanently bound to the first owner that starts it. Each
 * later publication for that owner must use a strictly newer, nonzero
 * generation. The caller admits lines in page/line order, but accepted writes
 * may complete in any order. A credit retains the authoritative 64-byte copy
 * from admission until its exact WriteResp.
 *
 * Refusal uses one retry gate: no other request may be prepared until the
 * refused credit is returned by retryRequest() and accepted. Repeated refusal
 * reuses that same credit and payload. The implementation contains only
 * compile-time arrays; in particular it has no operation-sized payload,
 * request queue, response bitmap, or dynamically allocated state.
 */
template <std::size_t ElementBytes = 4, std::size_t MaxPageCount = 4,
          std::size_t LineCreditCount = 8>
class ResponseBearingSpdPublisher
{
  public:
    static constexpr std::size_t LineBytes = 64;
    static constexpr std::size_t WordBytes = ElementBytes;
    static constexpr std::size_t PageElements = 4096;
    static constexpr std::size_t PageBytes = PageElements * WordBytes;
    static constexpr std::size_t LinesPerPage = PageBytes / LineBytes;
    static constexpr std::size_t MaxPages = MaxPageCount;
    static constexpr std::size_t Credits = LineCreditCount;

    static_assert(MaxPages != 0 && MaxPages <= 4);
    static_assert(Credits != 0);
    static_assert(WordBytes == 4 || WordBytes == 8);
    static_assert(LineBytes != 0 && PageBytes % LineBytes == 0);
    static_assert(MaxPages * LinesPerPage <=
                  std::numeric_limits<uint16_t>::max());

    using Payload = std::array<std::byte, LineBytes>;

    struct Identity
    {
        uint64_t owner = 0;
        uint64_t generation = 0;
        uint64_t address = 0;
        uint8_t page = 0;
        uint16_t line = 0;
    };

    struct Request
    {
        Identity identity{};
        const std::byte *payload = nullptr;
        std::size_t payloadBytes = 0;
    };

    struct WriteResponse
    {
        Identity identity{};
        bool isWriteResponse = true;
    };

    enum class BeginResult : uint8_t
    {
        Started,
        Busy,
        InvalidOwner,
        InvalidGeneration,
        InvalidPageCount,
        InvalidBaseAddress,
    };

    enum class EnqueueResult : uint8_t
    {
        Accepted,
        Inactive,
        Full,
        AllLinesEnqueued,
        WrongOwner,
        WrongGeneration,
        WrongPage,
        WrongLine,
        WrongAddress,
        NullPayload,
        WrongPayloadSize,
        OutOfOrder,
    };

    enum class RequestResult : uint8_t
    {
        Prepared,
        Inactive,
        NoReadyLine,
        RetryBlocked,
        AlreadyPrepared,
    };

    enum class SendResult : uint8_t
    {
        Accepted,
        Backpressured,
        Inactive,
        NoPreparedRequest,
        WrongRequest,
    };

    enum class RetryResult : uint8_t
    {
        Prepared,
        Inactive,
        NoRetryPending,
        AlreadyPrepared,
    };

    enum class AckResult : uint8_t
    {
        Accepted,
        Inactive,
        NotWriteResponse,
        WrongOwner,
        StaleGeneration,
        WrongGeneration,
        WrongPage,
        WrongLine,
        WrongAddress,
        NotOutstanding, // Duplicate, stale line, or never-issued line.
    };

    enum class ResetResult : uint8_t
    {
        Reset,
        Inactive,
        NotEmpty,
        Incomplete,
    };

    BeginResult begin(uint64_t owner, uint64_t generation,
                      uint64_t baseAddress, std::size_t pageCount)
    {
        if (activeFlag)
            return BeginResult::Busy;
        if (owner == 0)
            return BeginResult::InvalidOwner;
        if (generation == 0 ||
            (ownerBound &&
             (owner != boundOwner || generation <= lastGeneration))) {
            return ownerBound && owner != boundOwner
                ? BeginResult::InvalidOwner
                : BeginResult::InvalidGeneration;
        }
        if (pageCount == 0 || pageCount > MaxPages)
            return BeginResult::InvalidPageCount;
        if (baseAddress == 0 || baseAddress % LineBytes != 0 ||
            pageCount >
                (std::numeric_limits<uint64_t>::max() - baseAddress) /
                    PageBytes) {
            return BeginResult::InvalidBaseAddress;
        }

        clearRun();
        ownerBound = true;
        boundOwner = owner;
        lastGeneration = generation;
        activeGeneration = generation;
        publicationBase = baseAddress;
        publicationPages = static_cast<uint8_t>(pageCount);
        expectedLineCount = static_cast<uint16_t>(pageCount * LinesPerPage);
        activeFlag = true;
        return BeginResult::Started;
    }

    Identity identity(std::size_t page, std::size_t line) const
    {
        Identity result;
        if (!activeFlag || page >= publicationPages ||
            line >= LinesPerPage) {
            return result;
        }
        result.owner = boundOwner;
        result.generation = activeGeneration;
        result.page = static_cast<uint8_t>(page);
        result.line = static_cast<uint16_t>(line);
        result.address = publicationBase + page * PageBytes +
                         line * LineBytes;
        return result;
    }

    EnqueueResult enqueue(const Identity &lineIdentity,
                          const std::byte *payload,
                          std::size_t payloadBytes)
    {
        if (!activeFlag)
            return EnqueueResult::Inactive;
        if (payload == nullptr)
            return EnqueueResult::NullPayload;
        if (payloadBytes != LineBytes)
            return EnqueueResult::WrongPayloadSize;
        const EnqueueResult identityResult = validateEnqueue(lineIdentity);
        if (identityResult != EnqueueResult::Accepted)
            return identityResult;
        if (enqueuedLineCount == expectedLineCount)
            return EnqueueResult::AllLinesEnqueued;

        const std::size_t credit = freeCredit();
        if (credit == Credits)
            return EnqueueResult::Full;

        const std::size_t expectedPage = enqueuedLineCount / LinesPerPage;
        const std::size_t expectedLine = enqueuedLineCount % LinesPerPage;
        if (lineIdentity.page != expectedPage ||
            lineIdentity.line != expectedLine) {
            return EnqueueResult::OutOfOrder;
        }

        credits[credit].identity = lineIdentity;
        credits[credit].ordinal = enqueuedLineCount;
        credits[credit].state = CreditState::Queued;
        std::memcpy(payloads[credit].data(), payload, LineBytes);
        ++enqueuedLineCount;
        ++occupiedCreditCount;
        if (occupiedCreditCount > highWaterCreditCount)
            highWaterCreditCount = occupiedCreditCount;
        return EnqueueResult::Accepted;
    }

    RequestResult prepareRequest(Request *request)
    {
        clearRequest(request);
        if (!activeFlag)
            return RequestResult::Inactive;
        if (preparedCredit != Credits)
            return RequestResult::AlreadyPrepared;
        if (retryCredit != Credits)
            return RequestResult::RetryBlocked;

        const std::size_t credit = oldestQueuedCredit();
        if (credit == Credits)
            return RequestResult::NoReadyLine;
        credits[credit].state = CreditState::Prepared;
        preparedCredit = credit;
        if (request != nullptr)
            *request = requestFor(credit);
        return RequestResult::Prepared;
    }

    SendResult recordSend(const Request &request, bool accepted)
    {
        if (!activeFlag)
            return SendResult::Inactive;
        if (preparedCredit == Credits)
            return SendResult::NoPreparedRequest;
        if (!sameRequest(request, preparedCredit))
            return SendResult::WrongRequest;

        const std::size_t credit = preparedCredit;
        preparedCredit = Credits;
        if (accepted) {
            credits[credit].state = CreditState::AwaitingResponse;
            ++issuedLineCount;
            return SendResult::Accepted;
        }
        credits[credit].state = CreditState::AwaitingRetry;
        retryCredit = credit;
        return SendResult::Backpressured;
    }

    RetryResult retryRequest(Request *request)
    {
        clearRequest(request);
        if (!activeFlag)
            return RetryResult::Inactive;
        if (preparedCredit != Credits)
            return RetryResult::AlreadyPrepared;
        if (retryCredit == Credits)
            return RetryResult::NoRetryPending;

        const std::size_t credit = retryCredit;
        retryCredit = Credits;
        credits[credit].state = CreditState::Prepared;
        preparedCredit = credit;
        if (request != nullptr)
            *request = requestFor(credit);
        return RetryResult::Prepared;
    }

    AckResult acknowledge(const WriteResponse &response)
    {
        if (!activeFlag)
            return AckResult::Inactive;
        if (!response.isWriteResponse)
            return AckResult::NotWriteResponse;
        const AckResult identityResult = validateResponse(response.identity);
        if (identityResult != AckResult::Accepted)
            return identityResult;

        const std::size_t credit = findCredit(
            response.identity, CreditState::AwaitingResponse);
        if (credit == Credits)
            return AckResult::NotOutstanding;

        credits[credit] = Credit{};
        payloads[credit].fill(std::byte{0});
        --occupiedCreditCount;
        ++acknowledgedLineCount;
        return AckResult::Accepted;
    }

    bool retainedRequest(const Identity &lineIdentity, Request *request) const
    {
        clearRequest(request);
        if (!activeFlag)
            return false;
        for (std::size_t credit = 0; credit < Credits; ++credit) {
            if (credits[credit].state != CreditState::Free &&
                sameIdentity(credits[credit].identity, lineIdentity)) {
                if (request != nullptr)
                    *request = requestFor(credit);
                return true;
            }
        }
        return false;
    }

    ResetResult reset()
    {
        if (!activeFlag)
            return ResetResult::Inactive;
        if (!empty())
            return ResetResult::NotEmpty;
        if (!complete())
            return ResetResult::Incomplete;
        clearRun();
        return ResetResult::Reset;
    }

    bool active() const { return activeFlag; }
    // Live integration holds the producing SPD tile for the entire active
    // lifetime. Payloads are captured per line, but conservative source reuse
    // is authorized only after the final ACK and successful reset().
    bool sourceReusable() const { return !activeFlag; }
    bool empty() const { return occupiedCreditCount == 0; }
    bool retryPending() const { return retryCredit != Credits; }
    bool requestPrepared() const { return preparedCredit != Credits; }
    bool complete() const
    {
        return activeFlag && acknowledgedLineCount == expectedLineCount &&
               enqueuedLineCount == expectedLineCount && empty();
    }

    uint64_t owner() const { return ownerBound ? boundOwner : 0; }
    uint64_t generation() const
    {
        return activeFlag ? activeGeneration : 0;
    }
    uint64_t baseAddress() const { return publicationBase; }
    uint8_t pages() const { return publicationPages; }
    uint16_t expectedLines() const { return expectedLineCount; }
    uint16_t enqueuedLines() const { return enqueuedLineCount; }
    uint16_t issuedLines() const { return issuedLineCount; }
    uint16_t acknowledgedLines() const { return acknowledgedLineCount; }
    std::size_t occupiedCredits() const { return occupiedCreditCount; }
    std::size_t creditHighWater() const { return highWaterCreditCount; }

    static constexpr std::size_t chargedPayloadBytes()
    {
        return Credits * LineBytes;
    }

    static constexpr std::size_t chargedBytes()
    {
        return sizeof(ResponseBearingSpdPublisher);
    }

    static constexpr std::size_t chargedControlBytes()
    {
        return chargedBytes() - chargedPayloadBytes();
    }

    bool assertInvariants() const
    {
        std::size_t occupied = 0;
        std::size_t prepared = 0;
        std::size_t retrying = 0;
        for (std::size_t credit = 0; credit < Credits; ++credit) {
            const CreditState state = credits[credit].state;
            occupied += state != CreditState::Free;
            prepared += state == CreditState::Prepared;
            retrying += state == CreditState::AwaitingRetry;
            if (state != CreditState::Free &&
                (credits[credit].ordinal >= enqueuedLineCount ||
                 validateStored(credits[credit].identity) !=
                     AckResult::Accepted)) {
                return false;
            }
        }
        if (occupied != occupiedCreditCount || prepared > 1 || retrying > 1)
            return false;
        if ((preparedCredit == Credits) != (prepared == 0) ||
            (retryCredit == Credits) != (retrying == 0)) {
            return false;
        }
        if (preparedCredit != Credits &&
            credits[preparedCredit].state != CreditState::Prepared) {
            return false;
        }
        if (retryCredit != Credits &&
            credits[retryCredit].state != CreditState::AwaitingRetry) {
            return false;
        }
        if (!activeFlag)
            return occupied == 0 && expectedLineCount == 0 &&
                   enqueuedLineCount == 0 && issuedLineCount == 0 &&
                   acknowledgedLineCount == 0;
        return ownerBound && boundOwner != 0 && activeGeneration != 0 &&
               publicationPages != 0 && publicationPages <= MaxPages &&
               expectedLineCount == publicationPages * LinesPerPage &&
               acknowledgedLineCount <= issuedLineCount &&
               issuedLineCount <= enqueuedLineCount &&
               enqueuedLineCount <= expectedLineCount &&
               acknowledgedLineCount + occupiedCreditCount ==
                   enqueuedLineCount;
    }

  private:
    enum class CreditState : uint8_t
    {
        Free,
        Queued,
        Prepared,
        AwaitingRetry,
        AwaitingResponse,
    };

    struct Credit
    {
        Identity identity{};
        uint16_t ordinal = 0;
        CreditState state = CreditState::Free;
    };

    static bool sameIdentity(const Identity &left, const Identity &right)
    {
        return left.owner == right.owner &&
               left.generation == right.generation &&
               left.address == right.address && left.page == right.page &&
               left.line == right.line;
    }

    EnqueueResult validateEnqueue(const Identity &lineIdentity) const
    {
        if (lineIdentity.owner != boundOwner)
            return EnqueueResult::WrongOwner;
        if (lineIdentity.generation != activeGeneration)
            return EnqueueResult::WrongGeneration;
        if (lineIdentity.page >= publicationPages)
            return EnqueueResult::WrongPage;
        if (lineIdentity.line >= LinesPerPage)
            return EnqueueResult::WrongLine;
        if (lineIdentity.address !=
            identity(lineIdentity.page, lineIdentity.line).address) {
            return EnqueueResult::WrongAddress;
        }
        return EnqueueResult::Accepted;
    }

    AckResult validateStored(const Identity &lineIdentity) const
    {
        if (lineIdentity.owner != boundOwner)
            return AckResult::WrongOwner;
        if (lineIdentity.generation != activeGeneration)
            return AckResult::WrongGeneration;
        if (lineIdentity.page >= publicationPages)
            return AckResult::WrongPage;
        if (lineIdentity.line >= LinesPerPage)
            return AckResult::WrongLine;
        if (lineIdentity.address !=
            identity(lineIdentity.page, lineIdentity.line).address) {
            return AckResult::WrongAddress;
        }
        return AckResult::Accepted;
    }

    AckResult validateResponse(const Identity &lineIdentity) const
    {
        if (lineIdentity.owner != boundOwner)
            return AckResult::WrongOwner;
        if (lineIdentity.generation < activeGeneration)
            return AckResult::StaleGeneration;
        if (lineIdentity.generation != activeGeneration)
            return AckResult::WrongGeneration;
        if (lineIdentity.page >= publicationPages)
            return AckResult::WrongPage;
        if (lineIdentity.line >= LinesPerPage)
            return AckResult::WrongLine;
        if (lineIdentity.address !=
            identity(lineIdentity.page, lineIdentity.line).address) {
            return AckResult::WrongAddress;
        }
        return AckResult::Accepted;
    }

    std::size_t freeCredit() const
    {
        for (std::size_t credit = 0; credit < Credits; ++credit) {
            if (credits[credit].state == CreditState::Free)
                return credit;
        }
        return Credits;
    }

    std::size_t oldestQueuedCredit() const
    {
        std::size_t selected = Credits;
        uint16_t selectedOrdinal = std::numeric_limits<uint16_t>::max();
        for (std::size_t credit = 0; credit < Credits; ++credit) {
            if (credits[credit].state == CreditState::Queued &&
                credits[credit].ordinal < selectedOrdinal) {
                selected = credit;
                selectedOrdinal = credits[credit].ordinal;
            }
        }
        return selected;
    }

    std::size_t findCredit(const Identity &lineIdentity,
                           CreditState state) const
    {
        for (std::size_t credit = 0; credit < Credits; ++credit) {
            if (credits[credit].state == state &&
                sameIdentity(credits[credit].identity, lineIdentity)) {
                return credit;
            }
        }
        return Credits;
    }

    Request requestFor(std::size_t credit) const
    {
        return {credits[credit].identity, payloads[credit].data(), LineBytes};
    }

    bool sameRequest(const Request &request, std::size_t credit) const
    {
        return sameIdentity(request.identity, credits[credit].identity) &&
               request.payload == payloads[credit].data() &&
               request.payloadBytes == LineBytes;
    }

    static void clearRequest(Request *request)
    {
        if (request != nullptr)
            *request = Request{};
    }

    void clearRun()
    {
        activeFlag = false;
        activeGeneration = 0;
        publicationBase = 0;
        publicationPages = 0;
        expectedLineCount = 0;
        enqueuedLineCount = 0;
        issuedLineCount = 0;
        acknowledgedLineCount = 0;
        occupiedCreditCount = 0;
        highWaterCreditCount = 0;
        preparedCredit = Credits;
        retryCredit = Credits;
        credits.fill(Credit{});
        for (auto &payload : payloads)
            payload.fill(std::byte{0});
    }

    bool ownerBound = false;
    bool activeFlag = false;
    uint64_t boundOwner = 0;
    uint64_t lastGeneration = 0;
    uint64_t activeGeneration = 0;
    uint64_t publicationBase = 0;
    uint8_t publicationPages = 0;
    uint16_t expectedLineCount = 0;
    uint16_t enqueuedLineCount = 0;
    uint16_t issuedLineCount = 0;
    uint16_t acknowledgedLineCount = 0;
    std::size_t occupiedCreditCount = 0;
    std::size_t highWaterCreditCount = 0;
    std::size_t preparedCredit = Credits;
    std::size_t retryCredit = Credits;
    std::array<Credit, Credits> credits{};
    std::array<Payload, Credits> payloads{};
};

} // namespace gem5

#endif // __MEM_MAA_RESPONSE_BEARING_SPD_PUBLISHER_HH__
