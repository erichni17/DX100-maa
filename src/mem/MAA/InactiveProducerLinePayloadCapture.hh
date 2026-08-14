#ifndef __MEM_MAA_INACTIVE_PRODUCER_LINE_PAYLOAD_CAPTURE_HH__
#define __MEM_MAA_INACTIVE_PRODUCER_LINE_PAYLOAD_CAPTURE_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

namespace gem5 {

/**
 * Direct-indexed, fixed-capacity retention for authoritative producer lines
 * whose full WriteResp arrived before that logical 4K page was active.
 *
 * This deliberately has no associative search or page replay walk on the
 * materializer path.  Capture and consumption calculate the same power-of-two
 * index from the exact token/generation/backing-line identity.  A different
 * live line at that index follows the configured equal-cost policy:
 * first-owner drops the new payload, while latest-owner overwrites the same
 * selected entry.  The displaced owner always retains coherent ReadBacking
 * fallback. `probe()` and `take()` inspect only that one entry.
 * One explicitly modeled write port and one read port each accept a
 * one-MAA-clock-cycle access. The `nowCycle` arguments are MAA ClockedObject
 * cycle ordinals, never raw gem5 ticks. A colliding write is dropped to
 * coherent fallback; a
 * colliding read reports PortBusy so the caller retries the same selected
 * line before it can issue ReadBacking.
 *
 * Retained bytes are private until the caller has copied them into an existing
 * charged materializer buffer and reserved its ordinary delayed SPD commit.
 * The capture never changes SPD state or dependent visibility itself.
 */
class InactiveProducerLinePayloadCapture
{
  public:
    static constexpr uint8_t SlotCount = 4;
    static constexpr uint8_t LogicalPageCount = 4;
    static constexpr uint16_t MaxEntries = 512;
    static constexpr uint16_t LineBytes = 64;
    static constexpr uint8_t WritePortCount = 1;
    static constexpr uint8_t ReadPortCount = 1;
    static constexpr uint8_t PortAccessCycles = 1;
    static constexpr uint16_t NoTokenTile =
        std::numeric_limits<uint16_t>::max();

    enum class ConflictPolicy : uint8_t
    {
        FirstOwner,
        LatestOwner,
    };

    struct Key
    {
        uint16_t tokenTile = NoTokenTile;
        uint64_t generation = 0;
        uint64_t backingAddress = 0;
    };

    struct Line
    {
        uint16_t line = 0;
        uint64_t transactionID = 0;
        const std::byte *payload = nullptr;
    };

    struct Summary
    {
        uint16_t capacity = 0;
        uint16_t storedLines = 0;
        uint16_t capturedLines = 0;
        uint16_t replayedLines = 0;
        uint16_t conflicts = 0;
        uint16_t drops = 0;
        uint16_t writePortDrops = 0;
        uint16_t firstOwnerConflicts = 0;
        uint16_t latestOwnerOverwrites = 0;
        uint16_t latestOwnerEvictions = 0;
        uint16_t highWater = 0;
        std::array<uint16_t, LogicalPageCount> capturedLinesPerPage{};
        std::array<uint16_t, LogicalPageCount> replayedLinesPerPage{};
        std::array<uint16_t, LogicalPageCount> conflictsPerPage{};
        std::array<uint16_t, LogicalPageCount> writePortDropsPerPage{};
    };

    enum class BeginResult : uint8_t
    {
        Disabled,
        Started,
        Replaced,
        Existing,
        Full,
        Stale,
        Invalid,
    };

    enum class CaptureResult : uint8_t
    {
        Disabled,
        Captured,
        Duplicate,
        Conflict,
        Overwritten,
        PortBusy,
        Untracked,
        Stale,
        Invalid,
    };

    enum class ProbeResult : uint8_t
    {
        Disabled,
        Hit,
        Miss,
        PortBusy,
        Untracked,
        Stale,
        Invalid,
    };

    /**
     * One-entry MAA-cycle lookup pipeline timing. `arm()` records the
     * selected-line tag/data result at the read port, and `ready()` exposes
     * it no earlier than the next MAA clock cycle. It intentionally carries
     * no request payload; the capture owns the one 64-byte output register.
     */
    class LookupPipeline
    {
      public:
        bool arm(uint64_t nowCycle, ProbeResult result)
        {
            if (active || (result != ProbeResult::Hit &&
                           result != ProbeResult::Miss))
                return false;
            active = true;
            readyCycle = nowCycle + PortAccessCycles;
            probeResult = result;
            return true;
        }

        bool pending() const { return active; }
        bool ready(uint64_t nowCycle) const
        {
            return active && nowCycle >= readyCycle;
        }
        ProbeResult result() const { return probeResult; }
        uint64_t completionCycle() const { return readyCycle; }
        void clear() { *this = LookupPipeline{}; }

      private:
        bool active = false;
        uint64_t readyCycle = 0;
        ProbeResult probeResult = ProbeResult::Disabled;
    };

    BeginResult begin(const Key &key, uint16_t lineCount, uint16_t capacity,
                      ConflictPolicy policy = ConflictPolicy::FirstOwner)
    {
        if (!validKey(key) || lineCount == 0 ||
            lineCount % LogicalPageCount != 0 || !validCapacity(capacity))
            return BeginResult::Invalid;
        if (capacity == 0)
            return BeginResult::Disabled;
        if (configuredCapacity != 0 && configuredCapacity != capacity)
            return BeginResult::Invalid;
        if (configuredCapacity != 0 && configuredPolicy != policy)
            return BeginResult::Invalid;
        configuredCapacity = capacity;
        configuredPolicy = policy;
        Slot *slot = findToken(key.tokenTile);
        if (slot != nullptr) {
            if (slot->key.generation > key.generation)
                return BeginResult::Stale;
            if (sameKey(slot->key, key))
                return slot->lineCount == lineCount
                    ? BeginResult::Existing : BeginResult::Invalid;
            clearEntries(slot->key);
            reset(*slot, key, lineCount);
            return BeginResult::Replaced;
        }
        slot = firstInactive();
        if (slot == nullptr)
            return BeginResult::Full;
        reset(*slot, key, lineCount);
        return BeginResult::Started;
    }

    CaptureResult capture(const Key &key, uint16_t line,
                          uint64_t transactionID, const std::byte *payload,
                          std::size_t payloadBytes, uint64_t nowCycle)
    {
        if (configuredCapacity == 0)
            return CaptureResult::Disabled;
        if (!validKey(key) || transactionID == 0 || payload == nullptr ||
            payloadBytes != LineBytes)
            return CaptureResult::Invalid;
        Slot *slot = findToken(key.tokenTile);
        if (slot == nullptr)
            return CaptureResult::Untracked;
        if (!sameKey(slot->key, key))
            return CaptureResult::Stale;
        if (line >= slot->lineCount)
            return CaptureResult::Invalid;
        if (!reservePort(writePortNextAvailableCycle, nowCycle)) {
            ++slot->drops;
            ++slot->writePortDrops;
            ++slot->writePortDropsPerPage[pageIndex(*slot, line)];
            return CaptureResult::PortBusy;
        }
        Entry &entry = entries[index(key, line)];
        if (entry.valid) {
            if (sameEntry(entry, key, line))
                return CaptureResult::Duplicate;
            Slot *evicted = nullptr;
            if (configuredPolicy == ConflictPolicy::LatestOwner) {
                evicted = findExact(entry.key);
                if (evicted == nullptr || evicted->storedLines == 0)
                    return CaptureResult::Invalid;
            }
            ++slot->conflicts;
            ++slot->conflictsPerPage[pageIndex(*slot, line)];
            if (configuredPolicy == ConflictPolicy::FirstOwner) {
                ++slot->drops;
                ++slot->firstOwnerConflicts;
                return CaptureResult::Conflict;
            }
            --evicted->storedLines;
            ++evicted->drops;
            ++evicted->latestOwnerEvictions;
            ++slot->latestOwnerOverwrites;
            std::memcpy(entry.payload.data(), payload, LineBytes);
            entry.key = key;
            entry.line = line;
            entry.transactionID = transactionID;
            ++slot->storedLines;
            ++slot->capturedLines;
            ++slot->capturedLinesPerPage[pageIndex(*slot, line)];
            return CaptureResult::Overwritten;
        }
        std::memcpy(entry.payload.data(), payload, LineBytes);
        entry.key = key;
        entry.line = line;
        entry.transactionID = transactionID;
        entry.valid = true;
        ++slot->storedLines;
        ++slot->capturedLines;
        ++slot->capturedLinesPerPage[pageIndex(*slot, line)];
        ++activeEntries;
        if (activeEntries > highWater)
            highWater = activeEntries;
        return CaptureResult::Captured;
    }

    ProbeResult probe(const Key &key, uint16_t line, uint64_t nowCycle,
                      Line *result)
    {
        if (result != nullptr)
            *result = {};
        if (configuredCapacity == 0)
            return ProbeResult::Disabled;
        if (!validKey(key))
            return ProbeResult::Invalid;
        const Slot *slot = findToken(key.tokenTile);
        if (slot != nullptr && sameKey(slot->key, key) &&
            line >= slot->lineCount)
            return ProbeResult::Invalid;
        // Even if this owner was not allocated a lifetime descriptor (all
        // four slots were busy) or its old generation remains in a slot, the
        // configured direct-indexed RAM performs one exact-tag lookup. That
        // makes this an ordinary timed Miss instead of a zero-time bypass.
        if (!reservePort(readPortNextAvailableCycle, nowCycle))
            return ProbeResult::PortBusy;
        const Entry &entry = entries[index(key, line)];
        if (!entry.valid || !sameEntry(entry, key, line))
            return ProbeResult::Miss;
        std::memcpy(readPipelinePayload.data(), entry.payload.data(),
                    LineBytes);
        if (result != nullptr)
            *result = {line, entry.transactionID, readPipelinePayload.data()};
        return ProbeResult::Hit;
    }

    /** Consume exactly the line previously probed by the materializer. */
    bool take(const Key &key, uint16_t line)
    {
        if (configuredCapacity == 0)
            return false;
        Slot *slot = findExact(key);
        if (slot == nullptr || line >= slot->lineCount)
            return false;
        Entry &entry = entries[index(key, line)];
        if (!entry.valid || !sameEntry(entry, key, line))
            return false;
        entry = Entry{};
        --slot->storedLines;
        ++slot->replayedLines;
        ++slot->replayedLinesPerPage[pageIndex(*slot, line)];
        --activeEntries;
        return true;
    }

    bool clear(const Key &key)
    {
        Slot *slot = findExact(key);
        if (slot == nullptr)
            return false;
        clearEntries(key);
        *slot = Slot{};
        return assertInvariants();
    }

    bool active(const Key &key) const { return findExact(key) != nullptr; }

    Summary summary(const Key &key) const
    {
        Summary result;
        result.capacity = configuredCapacity;
        result.highWater = highWater;
        const Slot *slot = findExact(key);
        if (slot == nullptr)
            return result;
        result.storedLines = slot->storedLines;
        result.capturedLines = slot->capturedLines;
        result.replayedLines = slot->replayedLines;
        result.conflicts = slot->conflicts;
        result.drops = slot->drops;
        result.writePortDrops = slot->writePortDrops;
        result.firstOwnerConflicts = slot->firstOwnerConflicts;
        result.latestOwnerOverwrites = slot->latestOwnerOverwrites;
        result.latestOwnerEvictions = slot->latestOwnerEvictions;
        result.capturedLinesPerPage = slot->capturedLinesPerPage;
        result.replayedLinesPerPage = slot->replayedLinesPerPage;
        result.conflictsPerPage = slot->conflictsPerPage;
        result.writePortDropsPerPage = slot->writePortDropsPerPage;
        return result;
    }

    uint16_t occupancy() const { return activeEntries; }
    uint16_t occupancyHighWater() const { return highWater; }
    ConflictPolicy conflictPolicy() const { return configuredPolicy; }
    const std::byte *pipelinedPayload() const
    {
        return readPipelinePayload.data();
    }

    static constexpr bool validCapacity(uint16_t capacity)
    {
        return capacity == 0 ||
            (capacity <= MaxEntries && (capacity & (capacity - 1)) == 0);
    }

    static constexpr const char *conflictPolicyName(ConflictPolicy policy)
    {
        return policy == ConflictPolicy::FirstOwner
            ? "first-owner" : "latest-owner";
    }

    static constexpr std::size_t provisionedPayloadBytes(uint16_t capacity)
    {
        return static_cast<std::size_t>(capacity) * LineBytes;
    }

    static constexpr std::size_t provisionedTagBytes(uint16_t capacity)
    {
        // Exact token/generation/backing allocation, backing-line ID, and
        // closing producer WriteResp transaction per direct-indexed entry.
        return static_cast<std::size_t>(capacity) *
            (sizeof(uint16_t) + sizeof(uint64_t) * 2 + sizeof(uint16_t) +
             sizeof(uint64_t) + sizeof(bool));
    }

    static constexpr std::size_t
    provisionedReadPipelinePayloadBytes(uint16_t capacity)
    {
        return capacity == 0 ? 0 : LineBytes;
    }

    static constexpr std::size_t provisionedControlBytes(uint16_t capacity)
    {
        // Four producer lifetime descriptors, capacity/occupancy state, and
        // explicit next-available MAA-cycle registers for finite RAM ports.
        // Entry tags are separately visible through provisionedTagBytes().
        return provisionedTagBytes(capacity) +
            SlotCount * (sizeof(uint16_t) + sizeof(uint64_t) * 2 +
                         sizeof(uint16_t) * (10 + LogicalPageCount * 4) +
                         sizeof(bool)) +
            sizeof(uint16_t) * 3 +
            sizeof(uint8_t) +
            (WritePortCount + ReadPortCount) * sizeof(uint64_t);
    }

    static constexpr std::size_t provisionedTotalBytes(uint16_t capacity)
    {
        return provisionedPayloadBytes(capacity) +
            provisionedReadPipelinePayloadBytes(capacity) +
            provisionedControlBytes(capacity);
    }

    bool assertInvariants() const
    {
        if (!validCapacity(configuredCapacity) || activeEntries >
                configuredCapacity || highWater > configuredCapacity)
            return false;
        uint16_t entriesInUse = 0;
        for (uint16_t index = 0; index < configuredCapacity; ++index) {
            const Entry &entry = entries[index];
            if (!entry.valid)
                continue;
            const Slot *slot = findExact(entry.key);
            if (slot == nullptr || entry.line >= slot->lineCount ||
                entry.transactionID == 0 || index !=
                    this->index(entry.key, entry.line))
                return false;
            ++entriesInUse;
        }
        if (entriesInUse != activeEntries)
            return false;
        for (uint8_t slotIndex = 0; slotIndex < SlotCount; ++slotIndex) {
            const Slot &slot = slots[slotIndex];
            if (!slot.active) {
                if (slot.key.generation != 0 || slot.lineCount != 0 ||
                    slot.storedLines != 0 || slot.capturedLines != 0 ||
                    slot.replayedLines != 0 || slot.conflicts != 0 ||
                    slot.drops != 0 || slot.writePortDrops != 0 ||
                    slot.firstOwnerConflicts != 0 ||
                    slot.latestOwnerOverwrites != 0 ||
                    slot.latestOwnerEvictions != 0 ||
                    anyNonzero(slot.capturedLinesPerPage) ||
                    anyNonzero(slot.replayedLinesPerPage) ||
                    anyNonzero(slot.conflictsPerPage) ||
                    anyNonzero(slot.writePortDropsPerPage))
                    return false;
                continue;
            }
            if (!validKey(slot.key) || slot.lineCount == 0 ||
                slot.replayedLines > slot.capturedLines ||
                slot.drops != slot.firstOwnerConflicts +
                    slot.writePortDrops + slot.latestOwnerEvictions ||
                slot.conflicts != slot.firstOwnerConflicts +
                    slot.latestOwnerOverwrites ||
                sum(slot.capturedLinesPerPage) != slot.capturedLines ||
                sum(slot.replayedLinesPerPage) != slot.replayedLines ||
                sum(slot.conflictsPerPage) != slot.conflicts ||
                sum(slot.writePortDropsPerPage) != slot.writePortDrops)
                return false;
            uint16_t owned = 0;
            for (uint16_t entry = 0; entry < configuredCapacity; ++entry)
                owned += entries[entry].valid &&
                    sameKey(entries[entry].key, slot.key);
            if (owned != slot.storedLines)
                return false;
            for (uint8_t other = slotIndex + 1; other < SlotCount; ++other) {
                if (slots[other].active &&
                    slots[other].key.tokenTile == slot.key.tokenTile)
                    return false;
            }
        }
        return true;
    }

  private:
    struct Slot
    {
        Key key{};
        uint16_t lineCount = 0;
        uint16_t storedLines = 0;
        uint16_t capturedLines = 0;
        uint16_t replayedLines = 0;
        uint16_t conflicts = 0;
        uint16_t drops = 0;
        uint16_t writePortDrops = 0;
        uint16_t firstOwnerConflicts = 0;
        uint16_t latestOwnerOverwrites = 0;
        uint16_t latestOwnerEvictions = 0;
        std::array<uint16_t, LogicalPageCount> capturedLinesPerPage{};
        std::array<uint16_t, LogicalPageCount> replayedLinesPerPage{};
        std::array<uint16_t, LogicalPageCount> conflictsPerPage{};
        std::array<uint16_t, LogicalPageCount> writePortDropsPerPage{};
        bool active = false;
    };

    struct Entry
    {
        std::array<std::byte, LineBytes> payload{};
        Key key{};
        uint16_t line = 0;
        uint64_t transactionID = 0;
        bool valid = false;
    };

    static bool validKey(const Key &key)
    {
        return key.tokenTile != NoTokenTile && key.generation != 0;
    }

    static bool sameKey(const Key &lhs, const Key &rhs)
    {
        return lhs.tokenTile == rhs.tokenTile &&
            lhs.generation == rhs.generation &&
            lhs.backingAddress == rhs.backingAddress;
    }

    static bool sameEntry(const Entry &entry, const Key &key, uint16_t line)
    {
        return entry.valid && entry.line == line && sameKey(entry.key, key);
    }

    static uint8_t pageIndex(const Slot &slot, uint16_t line)
    {
        return static_cast<uint8_t>(
            line / (slot.lineCount / LogicalPageCount));
    }

    static uint16_t sum(const std::array<uint16_t, LogicalPageCount> &values)
    {
        uint16_t total = 0;
        for (uint16_t value : values)
            total += value;
        return total;
    }

    static bool anyNonzero(
        const std::array<uint16_t, LogicalPageCount> &values)
    {
        for (uint16_t value : values)
            if (value != 0)
                return true;
        return false;
    }

    uint16_t index(const Key &key, uint16_t line) const
    {
        // All permitted capacities are powers of two. Mix immutable identity
        // fields without a scan or victim arbitration.
        const uint64_t mixed = (key.backingAddress >> 6) ^ key.generation ^
            (static_cast<uint64_t>(key.tokenTile) << 17) ^ line;
        return static_cast<uint16_t>(mixed & (configuredCapacity - 1));
    }

    template <std::size_t PortCount>
    static bool reservePort(
        std::array<uint64_t, PortCount> &nextAvailableCycle,
        uint64_t nowCycle)
    {
        for (uint8_t port = 0; port < PortCount; ++port) {
            if (nextAvailableCycle[port] > nowCycle)
                continue;
            nextAvailableCycle[port] = nowCycle + PortAccessCycles;
            return true;
        }
        return false;
    }

    static void reset(Slot &slot, const Key &key, uint16_t lineCount)
    {
        slot = Slot{};
        slot.active = true;
        slot.key = key;
        slot.lineCount = lineCount;
    }

    void clearEntries(const Key &key)
    {
        for (uint16_t index = 0; index < configuredCapacity; ++index) {
            Entry &entry = entries[index];
            if (!entry.valid || !sameKey(entry.key, key))
                continue;
            entry = Entry{};
            --activeEntries;
        }
    }

    Slot *findToken(uint16_t tokenTile)
    {
        for (Slot &slot : slots)
            if (slot.active && slot.key.tokenTile == tokenTile)
                return &slot;
        return nullptr;
    }

    const Slot *findToken(uint16_t tokenTile) const
    {
        for (const Slot &slot : slots)
            if (slot.active && slot.key.tokenTile == tokenTile)
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

    const Slot *findExact(const Key &key) const
    {
        for (const Slot &slot : slots)
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

    std::array<Entry, MaxEntries> entries{};
    std::array<Slot, SlotCount> slots{};
    uint16_t configuredCapacity = 0;
    ConflictPolicy configuredPolicy = ConflictPolicy::FirstOwner;
    uint16_t activeEntries = 0;
    uint16_t highWater = 0;
    std::array<uint64_t, WritePortCount> writePortNextAvailableCycle{};
    std::array<uint64_t, ReadPortCount> readPortNextAvailableCycle{};
    // Fixed one-line output register, held stable while LookupPipeline is
    // pending and copied into an existing charged materializer buffer only on
    // lookup completion.
    std::array<std::byte, LineBytes> readPipelinePayload{};
};

static_assert(InactiveProducerLinePayloadCapture::MaxEntries == 512);
static_assert(InactiveProducerLinePayloadCapture::LineBytes == 64);
static_assert(InactiveProducerLinePayloadCapture::LogicalPageCount == 4);
static_assert(InactiveProducerLinePayloadCapture::PortAccessCycles == 1);
static_assert(
    InactiveProducerLinePayloadCapture::provisionedPayloadBytes(64) == 4096);
static_assert(
    InactiveProducerLinePayloadCapture::provisionedPayloadBytes(512) == 32768);
static_assert(InactiveProducerLinePayloadCapture::provisionedTagBytes(512) ==
              14848);

} // namespace gem5

#endif // __MEM_MAA_INACTIVE_PRODUCER_LINE_PAYLOAD_CAPTURE_HH__
