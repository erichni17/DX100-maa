#ifndef __MEM_MAA_DIRECT_INDEX_FEEDER_HH__
#define __MEM_MAA_DIRECT_INDEX_FEEDER_HH__

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

namespace gem5::maa
{

/**
 * Fixed direct-index line store.
 *
 * This is a finite functional model of the B/index feeder storage, not a
 * C++-container proxy for hardware area.  A response writes one 64-byte line;
 * individual 32-bit words retain their logical owner until consumed.  The
 * configured issue width limits line allocation in each caller-supplied
 * cycle, while cache/memory response timing remains outside this class.
 */
class DirectIndexFeeder
{
  public:
    static constexpr size_t MaxLines = 128;
    static constexpr size_t LineBytes = 64;
    static constexpr size_t WordBytes = sizeof(uint32_t);
    static constexpr size_t WordsPerLine = LineBytes / WordBytes;
    static constexpr uint32_t InvalidOwner =
        std::numeric_limits<uint32_t>::max();

    enum class State : uint8_t
    {
        Free,
        Pending,
        Ready,
    };

    enum class Result : uint8_t
    {
        Accepted,
        InvalidConfiguration,
        InvalidReservation,
        Full,
        IssueWidthLimited,
        DuplicateTag,
        DuplicateOwner,
        NotFound,
        NotPending,
        NotReady,
        StalePhase,
        ValueChanged,
    };

    struct Reservation
    {
        uint32_t logical = InvalidOwner;
        uint8_t word = 0;
    };

    struct Word
    {
        uint32_t value = 0;
        uint64_t lineTag = 0;
        uint64_t wordAddress = 0;
        uint32_t phase = 0;
        uint32_t logical = InvalidOwner;
        uint8_t word = 0;
    };

    struct Counters
    {
        uint64_t linesIssued = 0;
        uint64_t issueCycles = 0;
        uint64_t issueWidthLimited = 0;
        uint8_t maxLinesIssuedPerCycle = 0;
    };

    static constexpr bool
    validIssueWidth(size_t width)
    {
        return width == 1 || width == 2 || width == 4;
    }

    Result
    configure(size_t capacity, size_t issue_width)
    {
        if (capacity == 0 || capacity > MaxLines ||
            !validIssueWidth(issue_width)) {
            return Result::InvalidConfiguration;
        }
        configuredCapacity = capacity;
        configuredIssueWidth = issue_width;
        reset();
        return Result::Accepted;
    }

    void
    reset()
    {
        for (auto &line : lines)
            clearLine(line);
        occupiedLines = 0;
        ownedWords = 0;
        validWords = 0;
        highWaterLines = 0;
        highWaterWords = 0;
        highWaterValidWords = 0;
        issueCycleValid = false;
        activeIssueCycle = 0;
        issuedThisCycle = 0;
        issueCounters = {};
    }

    Result
    allocate(uint64_t tag, uint32_t phase,
             const std::array<Reservation, WordsPerLine> &reservations,
             size_t count, uint64_t cycle)
    {
        if (configuredCapacity == 0 || count == 0 ||
            count > WordsPerLine || (tag % LineBytes) != 0) {
            return Result::InvalidReservation;
        }
        if (!issueCycleValid || activeIssueCycle != cycle) {
            activeIssueCycle = cycle;
            issueCycleValid = true;
            issuedThisCycle = 0;
        }
        if (issuedThisCycle >= configuredIssueWidth) {
            ++issueCounters.issueWidthLimited;
            return Result::IssueWidthLimited;
        }
        if (occupiedLines >= configuredCapacity)
            return Result::Full;
        if (findTag(tag) != nullptr)
            return Result::DuplicateTag;

        uint16_t reservation_mask = 0;
        for (size_t idx = 0; idx < count; ++idx) {
            const auto &reservation = reservations[idx];
            if (reservation.logical == InvalidOwner ||
                reservation.word >= WordsPerLine ||
                (reservation_mask & (uint16_t{1} << reservation.word))) {
                return Result::InvalidReservation;
            }
            if (findOwner(reservation.logical) != nullptr)
                return Result::DuplicateOwner;
            reservation_mask |= uint16_t{1} << reservation.word;
        }

        Line *line = firstFree();
        if (line == nullptr)
            return Result::Full;
        line->tag = tag;
        line->phase = phase;
        line->state = State::Pending;
        line->reservationMask = reservation_mask;
        line->validMask = 0;
        for (size_t idx = 0; idx < count; ++idx) {
            const auto &reservation = reservations[idx];
            line->owners[reservation.word] = reservation.logical;
        }
        ++occupiedLines;
        ownedWords += count;
        highWaterLines = std::max(highWaterLines, occupiedLines);
        highWaterWords = std::max(highWaterWords, ownedWords);
        if (issuedThisCycle == 0)
            ++issueCounters.issueCycles;
        ++issuedThisCycle;
        ++issueCounters.linesIssued;
        issueCounters.maxLinesIssuedPerCycle = std::max(
            issueCounters.maxLinesIssuedPerCycle, issuedThisCycle);
        return Result::Accepted;
    }

    bool
    hasPending(uint64_t tag) const
    {
        const Line *line = findTag(tag);
        return line != nullptr && line->state == State::Pending;
    }

    Result
    respond(uint64_t tag, const uint8_t *data, size_t bytes)
    {
        Line *line = findTag(tag);
        if (line == nullptr)
            return Result::NotFound;
        if (line->state != State::Pending)
            return Result::NotPending;
        if (data == nullptr || bytes != LineBytes)
            return Result::InvalidReservation;
        std::memcpy(line->payload.data(), data, LineBytes);
        line->validMask = line->reservationMask;
        line->state = State::Ready;
        validWords += wordsForTag(tag);
        highWaterValidWords = std::max(highWaterValidWords, validWords);
        return Result::Accepted;
    }

    Result
    read(uint32_t logical, uint32_t expected_phase, Word &word) const
    {
        const Line *line = nullptr;
        size_t word_id = 0;
        const Result found = locateOwner(logical, line, word_id);
        if (found != Result::Accepted)
            return found;
        if (line->state != State::Ready ||
            !(line->validMask & (uint16_t{1} << word_id))) {
            return Result::NotReady;
        }
        if (line->phase != expected_phase)
            return Result::StalePhase;
        word = Word{line->payload[word_id], line->tag,
                    line->tag + word_id * WordBytes, line->phase,
                    logical, static_cast<uint8_t>(word_id)};
        return Result::Accepted;
    }

    Result
    consume(uint32_t logical, uint32_t expected_value,
            uint32_t expected_phase, bool poison)
    {
        Line *line = nullptr;
        size_t word_id = 0;
        const Result found = locateOwner(logical, line, word_id);
        if (found != Result::Accepted)
            return found;
        const uint16_t mask = uint16_t{1} << word_id;
        if (line->state != State::Ready || !(line->validMask & mask))
            return Result::NotReady;
        if (line->phase != expected_phase)
            return Result::StalePhase;
        if (line->payload[word_id] != expected_value)
            return Result::ValueChanged;
        if (poison)
            line->payload[word_id] = 0xd15ca4dU;
        line->validMask &= ~mask;
        line->reservationMask &= ~mask;
        line->owners[word_id] = InvalidOwner;
        --ownedWords;
        --validWords;
        if (line->reservationMask == 0) {
            clearLine(*line);
            --occupiedLines;
        }
        return Result::Accepted;
    }

    bool empty() const
    {
        return occupiedLines == 0 && ownedWords == 0 && validWords == 0;
    }
    bool full() const { return occupiedLines == configuredCapacity; }
    size_t capacity() const { return configuredCapacity; }
    size_t issueWidth() const { return configuredIssueWidth; }
    size_t linesUsed() const { return occupiedLines; }
    size_t
    pendingLines() const
    {
        return countState(State::Pending);
    }
    size_t
    readyLines() const
    {
        return countState(State::Ready);
    }
    size_t wordsOwned() const { return ownedWords; }
    size_t wordsValid() const { return validWords; }
    size_t maxLinesUsed() const { return highWaterLines; }
    size_t maxWordsOwned() const { return highWaterWords; }
    size_t maxWordsValid() const { return highWaterValidWords; }
    size_t
    wordsForTag(uint64_t tag) const
    {
        const Line *line = findTag(tag);
        if (line == nullptr)
            return 0;
        size_t count = 0;
        uint16_t mask = line->reservationMask;
        while (mask != 0) {
            count += mask & 1;
            mask >>= 1;
        }
        return count;
    }
    const Counters &counters() const { return issueCounters; }

    static constexpr size_t
    packedPayloadBits(size_t capacity)
    {
        return capacity * LineBytes * 8;
    }

    static constexpr size_t
    packedControlBits(size_t capacity, size_t address_bits,
                      size_t logical_owner_bits)
    {
        // Per line: tag, three-state encoding, reservation mask,
        // payload-valid mask, and one logical owner for each physical word.
        // The phase is shared because phase advance requires an empty feeder.
        // Per-feeder control also includes occupancy and current-cycle issue
        // count.  This packed semantic count is deliberately unrelated to
        // sizeof(Line), STL layout, or the host ABI.
        return capacity *
                   (address_bits + 2 + 2 * WordsPerLine +
                    WordsPerLine * logical_owner_bits) +
               bitsForValues(capacity + 1) + bitsForValues(5) + 32;
    }

    static const char *resultName(Result result)
    {
        switch (result) {
          case Result::Accepted: return "accepted";
          case Result::InvalidConfiguration: return "invalid_configuration";
          case Result::InvalidReservation: return "invalid_reservation";
          case Result::Full: return "full";
          case Result::IssueWidthLimited: return "issue_width_limited";
          case Result::DuplicateTag: return "duplicate_tag";
          case Result::DuplicateOwner: return "duplicate_owner";
          case Result::NotFound: return "not_found";
          case Result::NotPending: return "not_pending";
          case Result::NotReady: return "not_ready";
          case Result::StalePhase: return "stale_phase";
          case Result::ValueChanged: return "value_changed";
        }
        return "unknown";
    }

  private:
    struct Line
    {
        std::array<uint32_t, WordsPerLine> payload{};
        std::array<uint32_t, WordsPerLine> owners{};
        uint64_t tag = 0;
        uint32_t phase = 0;
        uint16_t reservationMask = 0;
        uint16_t validMask = 0;
        State state = State::Free;
    };

    static constexpr size_t
    bitsForValues(size_t values)
    {
        size_t bits = 0;
        size_t encoded = values > 0 ? values - 1 : 0;
        while (encoded != 0) {
            ++bits;
            encoded >>= 1;
        }
        return std::max<size_t>(bits, 1);
    }

    static void
    clearLine(Line &line)
    {
        line.payload.fill(0);
        line.owners.fill(InvalidOwner);
        line.tag = 0;
        line.phase = 0;
        line.reservationMask = 0;
        line.validMask = 0;
        line.state = State::Free;
    }

    Line *
    firstFree()
    {
        for (size_t idx = 0; idx < configuredCapacity; ++idx) {
            if (lines[idx].state == State::Free)
                return &lines[idx];
        }
        return nullptr;
    }

    size_t
    countState(State state) const
    {
        size_t count = 0;
        for (size_t idx = 0; idx < configuredCapacity; ++idx)
            count += lines[idx].state == state;
        return count;
    }

    Line *
    findTag(uint64_t tag)
    {
        for (size_t idx = 0; idx < configuredCapacity; ++idx) {
            if (lines[idx].state != State::Free && lines[idx].tag == tag)
                return &lines[idx];
        }
        return nullptr;
    }

    const Line *
    findTag(uint64_t tag) const
    {
        for (size_t idx = 0; idx < configuredCapacity; ++idx) {
            if (lines[idx].state != State::Free && lines[idx].tag == tag)
                return &lines[idx];
        }
        return nullptr;
    }

    const Line *
    findOwner(uint32_t logical) const
    {
        const Line *line = nullptr;
        size_t word = 0;
        return locateOwner(logical, line, word) == Result::Accepted
            ? line : nullptr;
    }

    Result
    locateOwner(uint32_t logical, const Line *&found_line,
                size_t &found_word) const
    {
        for (size_t idx = 0; idx < configuredCapacity; ++idx) {
            const Line &line = lines[idx];
            if (line.state == State::Free)
                continue;
            for (size_t word = 0; word < WordsPerLine; ++word) {
                const uint16_t mask = uint16_t{1} << word;
                if ((line.reservationMask & mask) &&
                    line.owners[word] == logical) {
                    found_line = &line;
                    found_word = word;
                    return Result::Accepted;
                }
            }
        }
        return Result::NotFound;
    }

    Result
    locateOwner(uint32_t logical, Line *&found_line, size_t &found_word)
    {
        const Line *line = nullptr;
        const Result result = locateOwner(logical, line, found_word);
        found_line = const_cast<Line *>(line);
        return result;
    }

    std::array<Line, MaxLines> lines{};
    size_t configuredCapacity = 0;
    size_t configuredIssueWidth = 1;
    size_t occupiedLines = 0;
    size_t ownedWords = 0;
    size_t validWords = 0;
    size_t highWaterLines = 0;
    size_t highWaterWords = 0;
    size_t highWaterValidWords = 0;
    bool issueCycleValid = false;
    uint64_t activeIssueCycle = 0;
    uint8_t issuedThisCycle = 0;
    Counters issueCounters{};
};

static_assert(DirectIndexFeeder::WordsPerLine == 16);
static_assert(DirectIndexFeeder::MaxLines == 128);

} // namespace gem5::maa

#endif // __MEM_MAA_DIRECT_INDEX_FEEDER_HH__
