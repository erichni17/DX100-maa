#ifndef __MEM_MAA_SOA_JIT_OLD_RESULT_BUFFER_HH__
#define __MEM_MAA_SOA_JIT_OLD_RESULT_BUFFER_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

namespace gem5
{

/**
 * Fixed cache-line buffers for guarded SoA/JIT old-value publication.
 *
 * A capture is keyed by its original logical ordinal, never by the reordered
 * A address.  Filling lines may be issued partially with byte enables; an
 * awaiting line keeps its payload and exact response identity until WriteResp.
 */
class SoaJitOldResultBuffer
{
  public:
    static constexpr size_t LineBytes = 64;
    static constexpr size_t WordBytes = sizeof(uint32_t);
    static constexpr size_t WordsPerLine = LineBytes / WordBytes;
    static constexpr size_t Credits = 8;
    static constexpr size_t MaxLogicalWords = 16 * 1024;
    static constexpr size_t MaxContexts = 64;

    enum class Result : uint8_t
    {
        Accepted,
        Inactive,
        Busy,
        InvalidGeneration,
        InvalidBase,
        InvalidLogicalWords,
        InvalidContext,
        InvalidOrdinal,
        NullValue,
        DuplicateOrdinal,
        LineAwaitingResponse,
        Full,
        NoReadyLine,
        SelectionAlreadyClosed,
        SelectionNotClosed,
        SelectionMismatch,
        CreditOutOfRange,
        NotOutstanding,
        WrongSequence,
        WrongAddress,
        WrongMask,
        NotComplete,
    };

    struct Identity
    {
        uint64_t generation = 0;
        uint64_t issueSequence = 0;
        uint64_t lineAddress = 0;
        uint16_t validWords = 0;
        uint8_t credit = 0;
    };

    struct Request
    {
        Identity identity{};
        const uint8_t *payload = nullptr;
        const uint16_t *contexts = nullptr;
    };

    Result begin(uint64_t generation, uint64_t resultBase,
                 size_t logicalWords)
    {
        if (active)
            return Result::Busy;
        if (generation == 0 || generation <= lastGeneration)
            return Result::InvalidGeneration;
        if (resultBase == 0 || resultBase % LineBytes != 0 ||
            logicalWords >
                (std::numeric_limits<uint64_t>::max() - resultBase) /
                    WordBytes)
            return Result::InvalidBase;
        if (logicalWords == 0 || logicalWords > MaxLogicalWords)
            return Result::InvalidLogicalWords;
        clearRun();
        active = true;
        activeGeneration = generation;
        lastGeneration = generation;
        baseAddress = resultBase;
        logicalWordCount = logicalWords;
        return Result::Accepted;
    }

    Result capture(uint64_t generation, uint16_t context, uint32_t ordinal,
                   const uint8_t *oldValue, size_t valueBytes)
    {
        if (!active)
            return Result::Inactive;
        if (generation != activeGeneration)
            return Result::InvalidGeneration;
        if (context >= MaxContexts)
            return Result::InvalidContext;
        if (ordinal >= logicalWordCount)
            return Result::InvalidOrdinal;
        if (oldValue == nullptr || valueBytes != WordBytes)
            return Result::NullValue;

        const uint64_t lineAddress =
            baseAddress + (ordinal / WordsPerLine) * LineBytes;
        const uint16_t wordMask = static_cast<uint16_t>(
            uint16_t{1} << (ordinal % WordsPerLine));
        Slot *slot = nullptr;
        for (auto &candidate : slots) {
            if (candidate.state == State::Free ||
                candidate.lineAddress != lineAddress)
                continue;
            if (candidate.state == State::AwaitingResponse)
                return Result::LineAwaitingResponse;
            if ((candidate.validWords & wordMask) != 0)
                return Result::DuplicateOrdinal;
            slot = &candidate;
            break;
        }
        if (slot == nullptr) {
            for (auto &candidate : slots) {
                if (candidate.state != State::Free)
                    continue;
                candidate = Slot{};
                candidate.state = State::Filling;
                candidate.lineAddress = lineAddress;
                candidate.age = ++nextAge;
                slot = &candidate;
                break;
            }
        }
        if (slot == nullptr)
            return Result::Full;

        const size_t word = ordinal % WordsPerLine;
        std::memcpy(slot->payload.data() + word * WordBytes, oldValue,
                    WordBytes);
        slot->contexts[word] = context;
        slot->validWords |= wordMask;
        ++capturedWords;
        if (occupied() > highWater)
            highWater = occupied();
        return Result::Accepted;
    }

    Result closeSelection(size_t selected, size_t rejected)
    {
        if (!active)
            return Result::Inactive;
        if (selectionClosed)
            return Result::SelectionAlreadyClosed;
        if (selected + rejected != logicalWordCount ||
            selected != capturedWords)
            return Result::SelectionMismatch;
        selectedWords = selected;
        rejectedWords = rejected;
        selectionClosed = true;
        return Result::Accepted;
    }

    Result issue(Request *request, bool forcePartial)
    {
        if (request != nullptr)
            *request = Request{};
        if (!active)
            return Result::Inactive;
        Slot *chosen = nullptr;
        for (auto &candidate : slots) {
            if (candidate.state == State::Filling &&
                candidate.validWords == std::numeric_limits<uint16_t>::max()) {
                if (chosen == nullptr || candidate.age < chosen->age)
                    chosen = &candidate;
            }
        }
        if (chosen == nullptr && forcePartial) {
            for (auto &candidate : slots) {
                if (candidate.state != State::Filling)
                    continue;
                if (chosen == nullptr || candidate.age < chosen->age)
                    chosen = &candidate;
            }
        }
        if (chosen == nullptr)
            return Result::NoReadyLine;
        if (nextIssueSequence == std::numeric_limits<uint64_t>::max())
            return Result::InvalidGeneration;

        const size_t credit = static_cast<size_t>(chosen - slots.data());
        chosen->state = State::AwaitingResponse;
        chosen->issueSequence = ++nextIssueSequence;
        ++issuedLines;
        if (request != nullptr) {
            request->identity = {activeGeneration, chosen->issueSequence,
                                 chosen->lineAddress, chosen->validWords,
                                 static_cast<uint8_t>(credit)};
            request->payload = chosen->payload.data();
            request->contexts = chosen->contexts.data();
        }
        return Result::Accepted;
    }

    Result acknowledge(const Identity &identity)
    {
        if (!active)
            return Result::Inactive;
        if (identity.generation != activeGeneration)
            return Result::InvalidGeneration;
        if (identity.credit >= Credits)
            return Result::CreditOutOfRange;
        Slot &slot = slots[identity.credit];
        if (slot.state != State::AwaitingResponse)
            return Result::NotOutstanding;
        if (identity.issueSequence != slot.issueSequence)
            return Result::WrongSequence;
        if (identity.lineAddress != slot.lineAddress)
            return Result::WrongAddress;
        if (identity.validWords != slot.validWords)
            return Result::WrongMask;
        slot = Slot{};
        ++acknowledgedLines;
        return Result::Accepted;
    }

    Result finish()
    {
        if (!active)
            return Result::Inactive;
        if (!complete())
            return Result::NotComplete;
        active = false;
        return Result::Accepted;
    }

    bool complete() const
    {
        return active && selectionClosed && capturedWords == selectedWords &&
               empty() && issuedLines == acknowledgedLines;
    }
    bool empty() const { return occupied() == 0; }
    size_t occupied() const
    {
        size_t count = 0;
        for (const auto &slot : slots)
            count += slot.state == State::Free ? 0 : 1;
        return count;
    }
    size_t filling() const
    {
        size_t count = 0;
        for (const auto &slot : slots)
            count += slot.state == State::Filling ? 1 : 0;
        return count;
    }
    size_t awaitingResponses() const
    {
        size_t count = 0;
        for (const auto &slot : slots)
            count += slot.state == State::AwaitingResponse ? 1 : 0;
        return count;
    }
    size_t captured() const { return capturedWords; }
    size_t rejected() const { return rejectedWords; }
    size_t issues() const { return issuedLines; }
    size_t responses() const { return acknowledgedLines; }
    size_t creditHighWater() const { return highWater; }
    uint64_t generation() const { return activeGeneration; }

  private:
    enum class State : uint8_t { Free, Filling, AwaitingResponse };
    struct Slot
    {
        std::array<uint8_t, LineBytes> payload{};
        std::array<uint16_t, WordsPerLine> contexts{};
        uint64_t lineAddress = 0;
        uint64_t issueSequence = 0;
        uint64_t age = 0;
        uint16_t validWords = 0;
        State state = State::Free;
    };

    void clearRun()
    {
        slots = {};
        baseAddress = 0;
        logicalWordCount = 0;
        activeGeneration = 0;
        nextIssueSequence = 0;
        nextAge = 0;
        capturedWords = 0;
        selectedWords = 0;
        rejectedWords = 0;
        issuedLines = 0;
        acknowledgedLines = 0;
        highWater = 0;
        selectionClosed = false;
    }

    std::array<Slot, Credits> slots{};
    uint64_t baseAddress = 0;
    size_t logicalWordCount = 0;
    uint64_t activeGeneration = 0;
    uint64_t lastGeneration = 0;
    uint64_t nextIssueSequence = 0;
    uint64_t nextAge = 0;
    size_t capturedWords = 0;
    size_t selectedWords = 0;
    size_t rejectedWords = 0;
    size_t issuedLines = 0;
    size_t acknowledgedLines = 0;
    size_t highWater = 0;
    bool active = false;
    bool selectionClosed = false;
};

static_assert(SoaJitOldResultBuffer::Credits == 8);
static_assert(SoaJitOldResultBuffer::WordBytes == sizeof(float));

} // namespace gem5

#endif // __MEM_MAA_SOA_JIT_OLD_RESULT_BUFFER_HH__
