#ifndef __MEM_MAA_VIRTUAL_SOURCE_FANOUT_HH__
#define __MEM_MAA_VIRTUAL_SOURCE_FANOUT_HH__

#include <array>
#include <cstdint>

namespace gem5::maa
{

/**
 * Fixed metadata for duplicate consumers of one returned source cache line.
 *
 * A logical tile can reference the same cache-line word many times.  Shared
 * result payload stores that word once and retains it until its final logical
 * consumer.  The 16 fixed counters are sufficient for a 64-byte cache line;
 * 15 bits per counter are sufficient for the current 16K logical-tile bound.
 */
class VirtualSourceFanout
{
  public:
    static constexpr uint16_t MaxLineWords = 16;
    static constexpr uint16_t MaxLogicalUses = 16384;
    static constexpr uint16_t ScanWidth = 4;

    enum class Result : uint8_t
    {
        Accepted,
        InvalidGeometry,
        InvalidWord,
        Overflow,
        AlreadySealed,
        NotSealed,
        CountMismatch,
        Exhausted,
    };

    Result reset(uint16_t activeWords)
    {
        if (activeWords == 0 || activeWords > MaxLineWords)
            return Result::InvalidGeometry;
        active_words = activeWords;
        logical_uses = 0;
        remaining_uses = 0;
        payload_words = 0;
        sealed = false;
        uses_by_word.fill(0);
        return Result::Accepted;
    }

    Result observe(uint16_t word)
    {
        if (active_words == 0)
            return Result::InvalidGeometry;
        if (sealed)
            return Result::AlreadySealed;
        if (word >= active_words)
            return Result::InvalidWord;
        if (logical_uses == MaxLogicalUses ||
            uses_by_word[word] == MaxLogicalUses)
            return Result::Overflow;
        if (uses_by_word[word] == 0)
            ++payload_words;
        ++uses_by_word[word];
        ++logical_uses;
        ++remaining_uses;
        return Result::Accepted;
    }

    Result seal(uint16_t expectedLogicalUses)
    {
        if (sealed)
            return Result::AlreadySealed;
        if (expectedLogicalUses == 0 ||
            expectedLogicalUses != logical_uses || payload_words == 0)
            return Result::CountMismatch;
        sealed = true;
        return Result::Accepted;
    }

    Result consume(uint16_t word, bool &finalUse)
    {
        finalUse = false;
        if (!sealed)
            return Result::NotSealed;
        if (word >= active_words)
            return Result::InvalidWord;
        if (uses_by_word[word] == 0 || remaining_uses == 0)
            return Result::Exhausted;
        --uses_by_word[word];
        --remaining_uses;
        finalUse = uses_by_word[word] == 0;
        return Result::Accepted;
    }

    Result rollback(uint16_t word)
    {
        if (!sealed)
            return Result::NotSealed;
        if (word >= active_words)
            return Result::InvalidWord;
        if (remaining_uses >= logical_uses ||
            uses_by_word[word] == MaxLogicalUses)
            return Result::Overflow;
        ++uses_by_word[word];
        ++remaining_uses;
        return Result::Accepted;
    }

    uint16_t activeWords() const { return active_words; }
    uint16_t logicalUses() const { return logical_uses; }
    uint16_t remainingUses() const { return remaining_uses; }
    uint16_t payloadWords() const { return payload_words; }
    uint16_t uses(uint16_t word) const
    {
        return word < active_words ? uses_by_word[word] : 0;
    }
    uint16_t scanCycles() const
    {
        return static_cast<uint16_t>(
            (logical_uses + ScanWidth - 1) / ScanWidth);
    }
    bool isSealed() const { return sealed; }
    bool empty() const { return sealed && remaining_uses == 0; }

    static const char *resultName(Result result)
    {
        switch (result) {
          case Result::Accepted: return "accepted";
          case Result::InvalidGeometry: return "invalid-geometry";
          case Result::InvalidWord: return "invalid-word";
          case Result::Overflow: return "overflow";
          case Result::AlreadySealed: return "already-sealed";
          case Result::NotSealed: return "not-sealed";
          case Result::CountMismatch: return "count-mismatch";
          case Result::Exhausted: return "exhausted";
        }
        return "unknown";
    }

  private:
    uint16_t active_words = 0;
    uint16_t logical_uses = 0;
    uint16_t remaining_uses = 0;
    uint16_t payload_words = 0;
    bool sealed = false;
    std::array<uint16_t, MaxLineWords> uses_by_word{};
};

} // namespace gem5::maa

#endif // __MEM_MAA_VIRTUAL_SOURCE_FANOUT_HH__
