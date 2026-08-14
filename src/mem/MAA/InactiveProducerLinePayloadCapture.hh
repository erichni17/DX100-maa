#ifndef __MEM_MAA_INACTIVE_PRODUCER_LINE_PAYLOAD_CAPTURE_HH__
#define __MEM_MAA_INACTIVE_PRODUCER_LINE_PAYLOAD_CAPTURE_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

namespace gem5 {

/**
 * Fixed-capacity, direct-indexed retention for authoritative producer lines
 * whose logical 4K page is not active yet.
 *
 * There are exactly four direct-mapped lifetime descriptors. tokenTile[1:0]
 * selects one descriptor and the complete lifetime tag is checked there; no
 * descriptor CAM or four-way search exists. Beginning a colliding lifetime
 * replaces that descriptor in O(1). The displaced lifetime and all of its
 * still-tagged RAM lines fall back coherently. Retirement invalidates only its
 * selected descriptor. Stale RAM tags are intentionally left in place and
 * reclaimed when a later write selects the same RAM index.
 *
 * The payload array is one direct-index RAM with one synchronous read port and
 * one synchronous write port. Each accepts at most one access per MAA cycle
 * and completes in one cycle. The model buffers one pending write until its
 * completion edge, which gives an exact read-before-write result when both
 * ports select the same index in one cycle, independent of call order.
 * A probe requires the selected exact lifetime descriptor before it may hit a
 * RAM tag. A displaced or retired lifetime therefore takes the ordinary timed
 * miss path even while its stale RAM tag awaits O(1) reclamation. A hit copies
 * the selected old/new RAM value into the sole 64-byte output register. That
 * register, including its exact lifetime/line/transaction tag, remains
 * authoritative until take(): a later latest-owner write or descriptor
 * replacement can neither change the replayed bytes nor cause take() to
 * consume the replacement.
 *
 * Retained bytes remain private until the caller copies a completed hit into
 * an already charged materializer buffer and schedules ordinary delayed SPD
 * visibility. Misses use the unchanged coherent ReadBacking path.
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
        uint64_t incarnation = 0;
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
        Full, // Kept for trace ABI; direct mapping never returns Full.
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
        OverwrittenLatched,
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

    /** One-entry MAA-cycle timing latch; payload lives in the output latch. */
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
                      ConflictPolicy policy = ConflictPolicy::FirstOwner,
                      uint16_t *displacedLines = nullptr)
    {
        if (displacedLines != nullptr)
            *displacedLines = 0;
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

        Slot &slot = slots[descriptorIndex(key.tokenTile)];
        if (!slot.active) {
            reset(slot, key, lineCount);
            return BeginResult::Started;
        }
        if (sameKey(slot.key, key))
            return slot.lineCount == lineCount ? BeginResult::Existing
                                               : BeginResult::Invalid;
        if (slot.key.tokenTile == key.tokenTile && newer(slot.key, key))
            return BeginResult::Stale;

        // Constant-time lazy invalidation: replacing the one selected
        // descriptor never walks or clears the payload RAM.
        if (displacedLines != nullptr) {
            const bool latchedLineSurvives = output.valid &&
                sameKey(output.key, slot.key) &&
                output.line < slot.lineCount &&
                sameEntry(logicalEntry(index(output.key, output.line)),
                          output.key, output.line) &&
                logicalEntry(index(output.key, output.line)).transactionID ==
                    output.transactionID;
            *displacedLines = slot.storedLines - latchedLineSurvives;
        }
        reset(slot, key, lineCount);
        return BeginResult::Replaced;
    }

    CaptureResult capture(const Key &key, uint16_t line,
                          uint64_t transactionID, const std::byte *payload,
                          std::size_t payloadBytes, uint64_t nowCycle)
    {
        advanceWrite(nowCycle);
        if (configuredCapacity == 0)
            return CaptureResult::Disabled;
        if (!validKey(key) || transactionID == 0 || payload == nullptr ||
            payloadBytes != LineBytes)
            return CaptureResult::Invalid;
        Slot *slot = findExact(key);
        if (slot == nullptr) {
            const Slot &selected = slots[descriptorIndex(key.tokenTile)];
            return selected.active &&
                    selected.key.tokenTile == key.tokenTile
                ? CaptureResult::Stale : CaptureResult::Untracked;
        }
        if (line >= slot->lineCount)
            return CaptureResult::Invalid;
        if (!reservePort(writePortNextAvailableCycle, nowCycle)) {
            ++slot->drops;
            ++slot->writePortDrops;
            ++slot->writePortDropsPerPage[pageIndex(*slot, line)];
            return CaptureResult::PortBusy;
        }

        const uint16_t selectedIndex = index(key, line);
        const Entry &resident = entries[selectedIndex];
        if (!resident.valid) {
            armWrite(selectedIndex, key, line, transactionID, payload,
                     nowCycle);
            accountCapture(*slot, line);
            ++validEntries;
            if (validEntries > highWater)
                highWater = validEntries;
            return CaptureResult::Captured;
        }
        if (sameEntry(resident, key, line)) {
            // A line is exact down to its closing WriteResp transaction.
            return resident.transactionID == transactionID
                ? CaptureResult::Duplicate : CaptureResult::Invalid;
        }

        Slot *residentOwner = findExact(resident.key);
        if (residentOwner == nullptr) {
            // The selected RAM tag is stale because its direct lifetime
            // descriptor was retired/replaced. Reclaim it without a scan,
            // conflict, occupancy change, or policy-dependent arbitration.
            armWrite(selectedIndex, key, line, transactionID, payload,
                     nowCycle);
            accountCapture(*slot, line);
            return CaptureResult::Captured;
        }

        ++slot->conflicts;
        ++slot->conflictsPerPage[pageIndex(*slot, line)];
        if (configuredPolicy == ConflictPolicy::FirstOwner) {
            ++slot->drops;
            ++slot->firstOwnerConflicts;
            return CaptureResult::Conflict;
        }

        const bool residentLatched = output.valid &&
            sameKey(output.key, resident.key) &&
            output.line == resident.line &&
            output.transactionID == resident.transactionID;
        --residentOwner->storedLines;
        if (!residentLatched) {
            ++residentOwner->drops;
            ++residentOwner->latestOwnerEvictions;
        }
        ++slot->latestOwnerOverwrites;
        armWrite(selectedIndex, key, line, transactionID, payload, nowCycle);
        accountCapture(*slot, line);
        return residentLatched ? CaptureResult::OverwrittenLatched
                               : CaptureResult::Overwritten;
    }

    ProbeResult probe(const Key &key, uint16_t line, uint64_t nowCycle,
                      Line *result)
    {
        advanceWrite(nowCycle);
        if (result != nullptr)
            *result = {};
        if (configuredCapacity == 0)
            return ProbeResult::Disabled;
        if (!validKey(key))
            return ProbeResult::Invalid;
        const Slot *slot = findExact(key);
        if (slot != nullptr && line >= slot->lineCount)
            return ProbeResult::Invalid;
        // The single output register is occupied until its exact hit is
        // consumed. A caller cannot overwrite authoritative replay bytes with
        // a later probe, even after the physical read port becomes free.
        if (output.valid)
            return ProbeResult::PortBusy;
        if (!reservePort(readPortNextAvailableCycle, nowCycle))
            return ProbeResult::PortBusy;

        // Descriptor replacement/clear lazily leaves RAM tags in place. The
        // exact direct-mapped descriptor is the O(1) coherence authority, so
        // an absent descriptor must not authenticate one of those stale tags.
        // It still consumes the ordinary synchronous read opportunity and is
        // exposed by LookupPipeline only after the same one-cycle miss delay.
        if (slot == nullptr)
            return ProbeResult::Miss;

        const Entry &entry = entries[index(key, line)];
        output = OutputLatch{};
        if (!entry.valid || !sameEntry(entry, key, line))
            return ProbeResult::Miss;
        std::memcpy(output.payload.data(), entry.payload.data(), LineBytes);
        output.key = key;
        output.line = line;
        output.transactionID = entry.transactionID;
        output.valid = true;
        if (result != nullptr)
            *result = {line, entry.transactionID, output.payload.data()};
        return ProbeResult::Hit;
    }

    /** Consume the authoritative output latch, never a replacement RAM tag. */
    bool take(const Key &key, uint16_t line, uint64_t transactionID,
              uint64_t nowCycle)
    {
        advanceWrite(nowCycle);
        if (configuredCapacity == 0 || !output.valid ||
            output.transactionID != transactionID || output.line != line ||
            !sameKey(output.key, key))
            return false;

        const uint16_t selectedIndex = index(key, line);
        Entry &entry = entries[selectedIndex];
        const bool pendingReplacement = pendingWrite.active &&
            pendingWrite.index == selectedIndex;
        if (sameEntry(entry, key, line) &&
            entry.transactionID == transactionID) {
            entry = Entry{};
            if (!pendingReplacement)
                --validEntries;
            Slot *slot = findExact(key);
            if (slot != nullptr && !pendingReplacement)
                --slot->storedLines;
        }
        Slot *slot = findExact(key);
        if (slot != nullptr) {
            ++slot->replayedLines;
            ++slot->replayedLinesPerPage[pageIndex(*slot, line)];
        }
        output = OutputLatch{};
        return true;
    }

    /** O(1): invalidate only the direct-mapped lifetime descriptor. */
    bool clear(const Key &key)
    {
        Slot *slot = findExact(key);
        if (slot == nullptr)
            return false;
        *slot = Slot{};
        return true;
    }

    bool active(const Key &key) const { return findExact(key) != nullptr; }

    Summary summary(const Key &key) const
    {
        Summary result;
        result.capacity = configuredCapacity;
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

    uint16_t occupancy() const { return validEntries; }
    uint16_t occupancyHighWater() const { return highWater; }
    ConflictPolicy conflictPolicy() const { return configuredPolicy; }
    const std::byte *pipelinedPayload() const { return output.payload.data(); }
    uint64_t pipelinedTransactionID() const
    {
        return output.valid ? output.transactionID : 0;
    }

    static constexpr uint8_t descriptorIndexForToken(uint16_t tokenTile)
    {
        return static_cast<uint8_t>(tokenTile & (SlotCount - 1));
    }

    uint16_t selectedEntry(const Key &key, uint16_t line) const
    {
        return configuredCapacity == 0 ? 0 : index(key, line);
    }

    static constexpr bool validCapacity(uint16_t capacity)
    {
        return capacity == 0 ||
            (capacity >= 64 && capacity <= MaxEntries &&
             (capacity & (capacity - 1)) == 0);
    }

    static constexpr const char *conflictPolicyName(ConflictPolicy policy)
    {
        return policy == ConflictPolicy::FirstOwner ? "first-owner"
                                                    : "latest-owner";
    }

    // Packed RTL lower-bound equations. These never use host sizeof().
    static constexpr std::size_t KeyBits = 16 + 64 + 64 + 64;
    static constexpr std::size_t EntryTagBits =
        KeyBits + 16 + 64 + 1;
    static constexpr std::size_t DescriptorBits =
        1 + KeyBits + 16 + 9 * 16 +
        4 * LogicalPageCount * 16;
    static constexpr std::size_t OutputTagBits = EntryTagBits;
    static constexpr std::size_t OutputPayloadBits = LineBytes * 8;
    static constexpr std::size_t GlobalControlBits = 10 + 1 + 10 + 10;
    // Context owner + complete materializer request + payload-only identity
    // suffix + one-cycle hit/miss timing state.
    static constexpr std::size_t MAALookupControlBits =
        (16 + 64 + 64) + (2 + 16 + 5 + 3 + 64 + 16 + 64) +
        (64 + 64) + (1 + 64 + 3);
    static constexpr std::size_t PayloadIncarnationBitsPerToken = 64;

    static constexpr std::size_t bitsToBytes(std::size_t bits)
    {
        return (bits + 7) / 8;
    }

    static constexpr std::size_t indexBits(uint16_t capacity)
    {
        std::size_t bits = 0;
        while (capacity > 1) {
            capacity >>= 1;
            ++bits;
        }
        return bits;
    }

    static constexpr std::size_t provisionedPayloadBits(uint16_t capacity)
    {
        return static_cast<std::size_t>(capacity) * LineBytes * 8;
    }

    static constexpr std::size_t provisionedPayloadBytes(uint16_t capacity)
    {
        return bitsToBytes(provisionedPayloadBits(capacity));
    }

    static constexpr std::size_t provisionedTagBits(uint16_t capacity)
    {
        return static_cast<std::size_t>(capacity) * EntryTagBits;
    }

    static constexpr std::size_t provisionedTagBytes(uint16_t capacity)
    {
        return bitsToBytes(provisionedTagBits(capacity));
    }

    static constexpr std::size_t provisionedDescriptorBits(uint16_t capacity)
    {
        return capacity == 0 ? 0 : SlotCount * DescriptorBits;
    }

    static constexpr std::size_t provisionedReadPortStateBits(
        uint16_t capacity)
    {
        return capacity == 0 ? 0 : 64;
    }

    static constexpr std::size_t provisionedWritePortStateBits(
        uint16_t capacity)
    {
        // next-available cycle + pending valid/completion/index + complete
        // one-entry write input latch (payload plus exact tag).
        return capacity == 0 ? 0
            : 64 + 1 + 64 + indexBits(capacity) +
                OutputPayloadBits + EntryTagBits;
    }

    static constexpr std::size_t provisionedOutputTagBits(uint16_t capacity)
    {
        return capacity == 0 ? 0 : OutputTagBits;
    }

    static constexpr std::size_t
    provisionedReadPipelinePayloadBytes(uint16_t capacity)
    {
        return capacity == 0 ? 0 : LineBytes;
    }

    static constexpr std::size_t provisionedControlBits(uint16_t capacity)
    {
        return capacity == 0 ? 0
            : provisionedTagBits(capacity) +
                provisionedDescriptorBits(capacity) +
                provisionedReadPortStateBits(capacity) +
                provisionedWritePortStateBits(capacity) +
                provisionedOutputTagBits(capacity) + GlobalControlBits;
    }

    static constexpr std::size_t provisionedControlBytes(uint16_t capacity)
    {
        return bitsToBytes(provisionedControlBits(capacity));
    }

    static constexpr std::size_t provisionedTotalBits(uint16_t capacity)
    {
        return provisionedPayloadBits(capacity) +
            (capacity == 0 ? 0 : OutputPayloadBits) +
            provisionedControlBits(capacity);
    }

    static constexpr std::size_t provisionedTotalBytes(uint16_t capacity)
    {
        return bitsToBytes(provisionedTotalBits(capacity));
    }

    static constexpr std::size_t provisionedMAAPersistentStateBits(
        uint16_t capacity, std::size_t tokenCount)
    {
        return capacity == 0 ? 0
            : tokenCount * PayloadIncarnationBitsPerToken;
    }

    static constexpr std::size_t provisionedMAAControlBits(
        uint16_t capacity, std::size_t tokenCount)
    {
        return capacity == 0 ? 0
            : provisionedControlBits(capacity) + MAALookupControlBits +
                provisionedMAAPersistentStateBits(capacity, tokenCount);
    }

    static constexpr std::size_t provisionedCombinedTotalBits(
        uint16_t capacity, std::size_t tokenCount)
    {
        return capacity == 0 ? 0
            : provisionedPayloadBits(capacity) + OutputPayloadBits +
                provisionedMAAControlBits(capacity, tokenCount);
    }

    static constexpr std::size_t provisionedCombinedTotalBytes(
        uint16_t capacity, std::size_t tokenCount)
    {
        return bitsToBytes(provisionedCombinedTotalBits(capacity, tokenCount));
    }

    /** Host-only exhaustive diagnostic; never called by lifecycle methods. */
    bool assertInvariants() const
    {
        if (!validCapacity(configuredCapacity) || validEntries >
                configuredCapacity || highWater > configuredCapacity)
            return false;
        uint16_t entriesInUse = 0;
        for (uint16_t i = 0; i < configuredCapacity; ++i) {
            const Entry &entry = logicalEntry(i);
            if (!entry.valid)
                continue;
            if (!validKey(entry.key) || entry.transactionID == 0 ||
                i != index(entry.key, entry.line))
                return false;
            ++entriesInUse;
        }
        if (entriesInUse != validEntries)
            return false;
        for (uint8_t i = 0; i < SlotCount; ++i) {
            const Slot &slot = slots[i];
            if (!slot.active)
                continue;
            if (!validKey(slot.key) ||
                descriptorIndex(slot.key.tokenTile) != i ||
                slot.lineCount == 0 ||
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
                owned += logicalEntry(entry).valid &&
                    sameKey(logicalEntry(entry).key, slot.key);
            if (owned != slot.storedLines)
                return false;
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

    struct PendingWrite
    {
        Entry entry{};
        uint16_t index = 0;
        uint64_t completionCycle = 0;
        bool active = false;
    };

    struct OutputLatch
    {
        std::array<std::byte, LineBytes> payload{};
        Key key{};
        uint16_t line = 0;
        uint64_t transactionID = 0;
        bool valid = false;
    };

    static constexpr uint8_t descriptorIndex(uint16_t tokenTile)
    {
        return descriptorIndexForToken(tokenTile);
    }

    static bool validKey(const Key &key)
    {
        return key.tokenTile != NoTokenTile && key.generation != 0 &&
            key.incarnation != 0;
    }

    static bool sameKey(const Key &lhs, const Key &rhs)
    {
        return lhs.tokenTile == rhs.tokenTile &&
            lhs.generation == rhs.generation &&
            lhs.incarnation == rhs.incarnation &&
            lhs.backingAddress == rhs.backingAddress;
    }

    static bool newer(const Key &lhs, const Key &rhs)
    {
        return lhs.generation > rhs.generation ||
            (lhs.generation == rhs.generation &&
             lhs.incarnation > rhs.incarnation);
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

    uint16_t index(const Key &key, uint16_t line) const
    {
        const uint64_t mixed = (key.backingAddress >> 6) ^ key.generation ^
            (key.incarnation << 7) ^
            (static_cast<uint64_t>(key.tokenTile) << 17) ^ line;
        return static_cast<uint16_t>(mixed & (configuredCapacity - 1));
    }

    static bool reservePort(uint64_t &nextAvailableCycle, uint64_t nowCycle)
    {
        if (nextAvailableCycle > nowCycle)
            return false;
        nextAvailableCycle = nowCycle + PortAccessCycles;
        return true;
    }

    static void reset(Slot &slot, const Key &key, uint16_t lineCount)
    {
        slot = Slot{};
        slot.active = true;
        slot.key = key;
        slot.lineCount = lineCount;
    }

    static void accountCapture(Slot &slot, uint16_t line)
    {
        ++slot.storedLines;
        ++slot.capturedLines;
        ++slot.capturedLinesPerPage[pageIndex(slot, line)];
    }

    void armWrite(uint16_t selectedIndex, const Key &key, uint16_t line,
                  uint64_t transactionID, const std::byte *payload,
                  uint64_t nowCycle)
    {
        pendingWrite = PendingWrite{};
        std::memcpy(pendingWrite.entry.payload.data(), payload, LineBytes);
        pendingWrite.entry.key = key;
        pendingWrite.entry.line = line;
        pendingWrite.entry.transactionID = transactionID;
        pendingWrite.entry.valid = true;
        pendingWrite.index = selectedIndex;
        pendingWrite.completionCycle = nowCycle + PortAccessCycles;
        pendingWrite.active = true;
    }

    void advanceWrite(uint64_t nowCycle)
    {
        if (!pendingWrite.active ||
            pendingWrite.completionCycle > nowCycle)
            return;
        entries[pendingWrite.index] = pendingWrite.entry;
        pendingWrite = PendingWrite{};
    }

    const Entry &logicalEntry(uint16_t selectedIndex) const
    {
        return pendingWrite.active && pendingWrite.index == selectedIndex
            ? pendingWrite.entry : entries[selectedIndex];
    }

    Slot *findExact(const Key &key)
    {
        Slot &slot = slots[descriptorIndex(key.tokenTile)];
        return slot.active && sameKey(slot.key, key) ? &slot : nullptr;
    }

    const Slot *findExact(const Key &key) const
    {
        const Slot &slot = slots[descriptorIndex(key.tokenTile)];
        return slot.active && sameKey(slot.key, key) ? &slot : nullptr;
    }

    std::array<Entry, MaxEntries> entries{};
    std::array<Slot, SlotCount> slots{};
    PendingWrite pendingWrite{};
    OutputLatch output{};
    uint16_t configuredCapacity = 0;
    ConflictPolicy configuredPolicy = ConflictPolicy::FirstOwner;
    uint16_t validEntries = 0;
    uint16_t highWater = 0;
    uint64_t writePortNextAvailableCycle = 0;
    uint64_t readPortNextAvailableCycle = 0;
};

static_assert(InactiveProducerLinePayloadCapture::MaxEntries == 512);
static_assert(InactiveProducerLinePayloadCapture::LineBytes == 64);
static_assert(InactiveProducerLinePayloadCapture::SlotCount == 4);
static_assert((InactiveProducerLinePayloadCapture::SlotCount &
               (InactiveProducerLinePayloadCapture::SlotCount - 1)) == 0);
static_assert(InactiveProducerLinePayloadCapture::PortAccessCycles == 1);
static_assert(InactiveProducerLinePayloadCapture::KeyBits == 208);
static_assert(InactiveProducerLinePayloadCapture::EntryTagBits == 289);
static_assert(InactiveProducerLinePayloadCapture::DescriptorBits == 625);
static_assert(InactiveProducerLinePayloadCapture::MAALookupControlBits ==
              510);
static_assert(
    InactiveProducerLinePayloadCapture::PayloadIncarnationBitsPerToken == 64);

} // namespace gem5

#endif // __MEM_MAA_INACTIVE_PRODUCER_LINE_PAYLOAD_CAPTURE_HH__
