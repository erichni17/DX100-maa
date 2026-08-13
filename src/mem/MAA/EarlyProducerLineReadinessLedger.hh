#ifndef __MEM_MAA_EARLY_PRODUCER_LINE_READINESS_LEDGER_HH__
#define __MEM_MAA_EARLY_PRODUCER_LINE_READINESS_LEDGER_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>

namespace gem5 {

/**
 * Compact pre-admission visibility state for XRAGE direct retirement.
 *
 * The four slots match the four direct-retirement consumer contexts.  Each
 * slot retains at most 32 distinct early lines, matching the default producer
 * write-credit window without assuming that admission occurs within one
 * window.  If more distinct lines arrive, the ledger rejects those events and
 * the existing exact page WriteResp fallback closes them after admission.
 * Thus capacity affects overlap only, never visibility correctness.
 *
 * An entry stores no payload.  It merges disjoint acknowledged word masks for
 * one line and retains the exact transaction which most recently advanced the
 * mask (therefore the closing transaction when the line becomes ready).
 * Storage is fixed and allocation-free.
 */
class EarlyProducerLineReadinessLedger
{
  public:
    static constexpr uint8_t SlotCount = 4;
    static constexpr uint8_t TrackedLinesPerSlot = 32;
    static constexpr uint16_t MaxLines = 2048;
    static constexpr uint8_t PackedLineBits = 11;
    static constexpr uint32_t PackedLineMask =
        (uint32_t{1} << PackedLineBits) - 1;
    static constexpr uint16_t NoTokenTile =
        std::numeric_limits<uint16_t>::max();

    struct Key
    {
        uint16_t tokenTile = NoTokenTile;
        uint64_t generation = 0;
        uint64_t backingAddress = 0;
    };

    struct LineAck
    {
        uint16_t line = 0;
        uint16_t wordMask = 0;
        uint64_t transactionID = 0;
    };

    struct ReplaySummary
    {
        uint16_t entries = 0;
        uint16_t readyLines = 0;
        bool overflowed = false;
    };

    enum class BeginResult : uint8_t
    {
        Started,
        Replaced,
        Existing,
        Full,
        Stale,
        Invalid,
    };

    enum class AckResult : uint8_t
    {
        RecordedPartial,
        LineReady,
        Duplicate,
        Overflow,
        Untracked,
        Stale,
        Invalid,
    };

    static constexpr std::size_t chargedEntryBytes()
    {
        // Structure-of-arrays avoids the four padding bytes of a native
        // {u64 transaction, u16 line, u16 mask} C++ structure.
        return SlotCount * TrackedLinesPerSlot *
            (sizeof(uint64_t) + sizeof(uint32_t));
    }

    static constexpr std::size_t chargedMetadataBytes()
    {
        return sizeof(EarlyProducerLineReadinessLedger) -
            chargedEntryBytes();
    }

    static constexpr std::size_t chargedTotalBytes()
    {
        return sizeof(EarlyProducerLineReadinessLedger);
    }

    BeginResult begin(const Key &key, uint16_t lineCount,
                      uint16_t fullWordMask)
    {
        if (!validKey(key) || lineCount == 0 || lineCount > MaxLines ||
            fullWordMask == 0)
            return BeginResult::Invalid;

        Slot *slot = findToken(key.tokenTile);
        if (slot != nullptr) {
            if (slot->key.generation > key.generation)
                return BeginResult::Stale;
            if (sameKey(slot->key, key))
                return slot->lineCount == lineCount &&
                        slot->fullWordMask == fullWordMask
                    ? BeginResult::Existing : BeginResult::Invalid;
            reset(*slot, key, lineCount, fullWordMask);
            return BeginResult::Replaced;
        }

        slot = firstInactive();
        if (slot == nullptr)
            return BeginResult::Full;
        reset(*slot, key, lineCount, fullWordMask);
        return BeginResult::Started;
    }

    AckResult acknowledge(const Key &key, const LineAck &ack)
    {
        if (!validKey(key) || ack.transactionID == 0 || ack.wordMask == 0)
            return AckResult::Invalid;
        Slot *slot = findToken(key.tokenTile);
        if (slot == nullptr)
            return AckResult::Untracked;
        if (!sameKey(slot->key, key))
            return AckResult::Stale;
        if (ack.line >= slot->lineCount ||
            (ack.wordMask & ~slot->fullWordMask) != 0)
            return AckResult::Invalid;

        uint8_t empty = TrackedLinesPerSlot;
        for (uint8_t index = 0; index < TrackedLinesPerSlot; ++index) {
            uint64_t &transaction = slot->transactions[index];
            uint32_t &lineMask = slot->lineMasks[index];
            if (transaction == 0) {
                if (empty == TrackedLinesPerSlot)
                    empty = index;
                continue;
            }
            if (entryLine(lineMask) != ack.line)
                continue;
            const uint16_t oldWords = entryWordMask(lineMask);
            if ((oldWords & ack.wordMask) != 0)
                return AckResult::Duplicate;
            const bool wasReady = oldWords == slot->fullWordMask;
            const uint16_t newWords = oldWords | ack.wordMask;
            lineMask = packLineMask(ack.line, newWords);
            transaction = ack.transactionID;
            const bool isReady = newWords == slot->fullWordMask;
            if (!wasReady && isReady)
                ++slot->readyLines;
            return assertInvariants()
                ? (isReady ? AckResult::LineReady
                           : AckResult::RecordedPartial)
                : AckResult::Invalid;
        }

        if (empty == TrackedLinesPerSlot) {
            slot->overflowed = true;
            return AckResult::Overflow;
        }
        slot->transactions[empty] = ack.transactionID;
        slot->lineMasks[empty] = packLineMask(ack.line, ack.wordMask);
        ++slot->occupiedEntries;
        if (ack.wordMask == slot->fullWordMask)
            ++slot->readyLines;
        return assertInvariants()
            ? (ack.wordMask == slot->fullWordMask
                   ? AckResult::LineReady
                   : AckResult::RecordedPartial)
            : AckResult::Invalid;
    }

    template <class Receiver>
    bool replay(const Key &key, Receiver receiver,
                ReplaySummary *summary = nullptr) const
    {
        if (summary != nullptr)
            *summary = {};
        const Slot *slot = findExact(key);
        if (slot == nullptr)
            return false;
        ReplaySummary result;
        result.overflowed = slot->overflowed;
        for (uint8_t index = 0; index < TrackedLinesPerSlot; ++index) {
            const uint64_t transaction = slot->transactions[index];
            if (transaction == 0)
                continue;
            const uint32_t lineMask = slot->lineMasks[index];
            const uint16_t words = entryWordMask(lineMask);
            if (!receiver(LineAck{entryLine(lineMask), words,
                                  transaction}))
                return false;
            ++result.entries;
            result.readyLines += words == slot->fullWordMask;
        }
        if (summary != nullptr)
            *summary = result;
        return result.entries == slot->occupiedEntries &&
            result.readyLines == slot->readyLines;
    }

    bool clear(const Key &key)
    {
        Slot *slot = findExact(key);
        if (slot == nullptr)
            return false;
        *slot = Slot{};
        return assertInvariants();
    }

    bool active(const Key &key) const { return findExact(key) != nullptr; }

    uint16_t readyLineCount(const Key &key) const
    {
        const Slot *slot = findExact(key);
        return slot == nullptr ? 0 : slot->readyLines;
    }

    uint8_t activeSlots() const
    {
        uint8_t count = 0;
        for (const Slot &slot : slots)
            count += slot.active;
        return count;
    }

    bool assertInvariants() const
    {
        uint8_t activeCount = 0;
        for (const Slot &slot : slots) {
            if (!slot.active) {
                if (slot.key.generation != 0 || slot.lineCount != 0 ||
                    slot.fullWordMask != 0 || slot.occupiedEntries != 0 ||
                    slot.readyLines != 0 || slot.overflowed)
                    return false;
                continue;
            }
            ++activeCount;
            if (!validKey(slot.key) || slot.lineCount == 0 ||
                slot.lineCount > MaxLines || slot.fullWordMask == 0 ||
                slot.occupiedEntries > TrackedLinesPerSlot ||
                slot.readyLines > slot.occupiedEntries)
                return false;
            uint16_t occupied = 0;
            uint16_t ready = 0;
            for (uint8_t index = 0; index < TrackedLinesPerSlot; ++index) {
                const uint64_t transaction = slot.transactions[index];
                const uint32_t lineMask = slot.lineMasks[index];
                if (transaction == 0) {
                    if (lineMask != 0)
                        return false;
                    continue;
                }
                const uint16_t line = entryLine(lineMask);
                const uint16_t words = entryWordMask(lineMask);
                if (line >= slot.lineCount || words == 0 ||
                    (words & ~slot.fullWordMask) != 0)
                    return false;
                ++occupied;
                ready += words == slot.fullWordMask;
                for (uint8_t other = 0;
                     other < TrackedLinesPerSlot; ++other) {
                    if (index != other && slot.transactions[other] != 0 &&
                        line == entryLine(slot.lineMasks[other]))
                        return false;
                }
            }
            if (occupied != slot.occupiedEntries || ready != slot.readyLines)
                return false;
            for (const Slot &other : slots) {
                if (&slot != &other && other.active &&
                    slot.key.tokenTile == other.key.tokenTile)
                    return false;
            }
        }
        return activeCount <= SlotCount;
    }

  private:
    struct Slot
    {
        std::array<uint64_t, TrackedLinesPerSlot> transactions{};
        std::array<uint32_t, TrackedLinesPerSlot> lineMasks{};
        Key key{};
        uint16_t lineCount = 0;
        uint16_t fullWordMask = 0;
        uint16_t occupiedEntries = 0;
        uint16_t readyLines = 0;
        bool active = false;
        bool overflowed = false;
    };

    static bool validKey(const Key &key)
    {
        return key.tokenTile != NoTokenTile && key.generation != 0;
    }

    static uint32_t packLineMask(uint16_t line, uint16_t wordMask)
    {
        return line | (static_cast<uint32_t>(wordMask) << PackedLineBits);
    }

    static uint16_t entryLine(uint32_t lineMask)
    {
        return static_cast<uint16_t>(lineMask & PackedLineMask);
    }

    static uint16_t entryWordMask(uint32_t lineMask)
    {
        return static_cast<uint16_t>(lineMask >> PackedLineBits);
    }

    static bool sameKey(const Key &lhs, const Key &rhs)
    {
        return lhs.tokenTile == rhs.tokenTile &&
            lhs.generation == rhs.generation &&
            lhs.backingAddress == rhs.backingAddress;
    }

    static void reset(Slot &slot, const Key &key, uint16_t lineCount,
                      uint16_t fullWordMask)
    {
        slot = Slot{};
        slot.active = true;
        slot.key = key;
        slot.lineCount = lineCount;
        slot.fullWordMask = fullWordMask;
    }

    Slot *findToken(uint16_t tokenTile)
    {
        for (Slot &slot : slots)
            if (slot.active && slot.key.tokenTile == tokenTile)
                return &slot;
        return nullptr;
    }

    const Slot *findExact(const Key &key) const
    {
        for (const Slot &slot : slots)
            if (slot.active && sameKey(slot.key, key))
                return &slot;
        return nullptr;
    }

    Slot *findExact(const Key &key)
    {
        for (Slot &slot : slots)
            if (slot.active && sameKey(slot.key, key))
                return &slot;
        return nullptr;
    }

    Slot *firstInactive()
    {
        for (Slot &slot : slots)
            if (!slot.active)
                return &slot;
        return nullptr;
    }

    std::array<Slot, SlotCount> slots{};
};

static_assert(EarlyProducerLineReadinessLedger::SlotCount == 4);
static_assert(EarlyProducerLineReadinessLedger::TrackedLinesPerSlot == 32);
static_assert(EarlyProducerLineReadinessLedger::MaxLines == 2048);
static_assert(EarlyProducerLineReadinessLedger::PackedLineMask == 2047);
static_assert(EarlyProducerLineReadinessLedger::chargedEntryBytes() == 1536);
static_assert(EarlyProducerLineReadinessLedger::chargedMetadataBytes() == 160);
static_assert(EarlyProducerLineReadinessLedger::chargedTotalBytes() == 1696);
static_assert(EarlyProducerLineReadinessLedger::chargedTotalBytes() ==
              EarlyProducerLineReadinessLedger::chargedEntryBytes() +
                  EarlyProducerLineReadinessLedger::chargedMetadataBytes());

} // namespace gem5

#endif // __MEM_MAA_EARLY_PRODUCER_LINE_READINESS_LEDGER_HH__
