#ifndef __MEM_MAA_INACTIVE_PRODUCER_MASKED_FRAGMENT_RETENTION_HH__
#define __MEM_MAA_INACTIVE_PRODUCER_MASKED_FRAGMENT_RETENTION_HH__

#include <array>
#include <bitset>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

namespace gem5 {

/**
 * Bounded retention of masked producer WriteResp fragments for inactive
 * materializer pages.
 *
 * Four tokenTile[1:0]-selected descriptors own four static capacity
 * partitions. Within a partition, line[1:0] selects one of four independent
 * one-write-port banks and line[...:2] directly selects an entry. There are
 * no associative searches, maps, queues, or replacement scans. A collision
 * retains the first exact owner and poisons the incoming logical line.
 *
 * Each descriptor also owns one poison bit per possible logical line. Once a
 * fragment is lost, overlaps an accepted word, collides, or fails exact
 * lifetime authentication, that line cannot reconstruct later in the same
 * lifetime. A probe may hit only after the containing page is sealed by the
 * caller's setVirtualPageReady authority and the exact entry has a full word
 * mask. Misses leave the existing coherent backing path authoritative.
 */
class InactiveProducerMaskedFragmentRetention
{
  public:
    static constexpr uint8_t DescriptorCount = 4;
    static constexpr uint8_t PartitionCount = DescriptorCount;
    static constexpr uint8_t BankCount = 4;
    static constexpr uint8_t WritePortsPerBank = 1;
    static constexpr uint8_t ReadPortCount = 1;
    static constexpr uint8_t PortAccessCycles = 1;
    static constexpr uint8_t LogicalPageCount = 4;
    static constexpr uint16_t MinEntries = 512;
    static constexpr uint16_t MaxEntries = 4096;
    static constexpr uint16_t MaxLogicalLines = 2048;
    static constexpr uint16_t LineBytes = 64;
    static constexpr uint8_t MaxWordsPerLine = 16;
    static constexpr uint16_t NoTokenTile =
        std::numeric_limits<uint16_t>::max();

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

    struct Counters
    {
        uint64_t fragmentsAccepted = 0;
        uint64_t wordsMerged = 0;
        uint64_t reconstructedLines = 0;
        uint64_t replayHits = 0;
        uint64_t replayMisses = 0;
        uint64_t tagConflicts = 0;
        uint64_t overlapPoisons = 0;
        uint64_t writePortPoisons = 0;
        uint64_t staleUntrackedDrops = 0;
        uint64_t invalidPoisons = 0;
        uint64_t descriptorFailures = 0;
        uint64_t readPortStalls = 0;
        uint64_t clears = 0;
        uint16_t occupancy = 0;
        uint16_t occupancyHighWater = 0;
    };

    struct ClearResult
    {
        uint16_t discardedEntries = 0;
        uint16_t poisonedLines = 0;
        uint8_t survivingLatchedLines = 0;
        bool cleared = false;

        explicit operator bool() const { return cleared; }
    };

    enum class BeginResult : uint8_t
    {
        Disabled,
        Started,
        Replaced,
        Existing,
        Stale,
        Invalid,
    };

    enum class SealResult : uint8_t
    {
        Disabled,
        Sealed,
        Duplicate,
        Untracked,
        Stale,
        Invalid,
    };

    enum class CaptureResult : uint8_t
    {
        Disabled,
        Accepted,
        Reconstructed,
        AlreadyPoisoned,
        OverlapPoison,
        ConflictPoison,
        WritePortPoison,
        StalePoison,
        InvalidPoison,
        Untracked,
        Invalid,
    };

    enum class ProbeResult : uint8_t
    {
        Disabled,
        Hit,
        Miss,
        PortBusy,
        Invalid,
    };

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

    BeginResult begin(const Key &key, uint16_t lineCount,
                      uint16_t capacity, uint16_t *discardedEntries = nullptr)
    {
        if (discardedEntries != nullptr)
            *discardedEntries = 0;
        if (!validCapacity(capacity) || !validKey(key) || lineCount == 0 ||
            lineCount > MaxLogicalLines ||
            lineCount % LogicalPageCount != 0)
            return BeginResult::Invalid;
        if (capacity == 0)
            return BeginResult::Disabled;
        if (configuredCapacity != 0 && configuredCapacity != capacity)
            return BeginResult::Invalid;
        configuredCapacity = capacity;

        Descriptor &descriptor = descriptors[descriptorIndex(key.tokenTile)];
        if (!descriptor.active) {
            reset(descriptor, key, lineCount);
            return BeginResult::Started;
        }
        if (sameKey(descriptor.key, key))
            return descriptor.lineCount == lineCount
                ? BeginResult::Existing : BeginResult::Invalid;
        if (descriptor.key.tokenTile == key.tokenTile &&
            newer(descriptor.key, key))
            return BeginResult::Stale;

        if (discardedEntries != nullptr) {
            const bool latchSurvives =
                output.valid && sameKey(output.key, descriptor.key);
            *discardedEntries = descriptor.storedEntries - latchSurvives;
        }
        activeEntries -= descriptor.storedEntries;
        stats.occupancy = activeEntries;
        ++stats.descriptorFailures;
        reset(descriptor, key, lineCount);
        return BeginResult::Replaced;
    }

    SealResult sealPage(const Key &key, uint8_t page)
    {
        if (configuredCapacity == 0)
            return SealResult::Disabled;
        if (!validKey(key) || page >= LogicalPageCount)
            return SealResult::Invalid;
        Descriptor *descriptor = findExact(key);
        if (descriptor == nullptr) {
            ++stats.staleUntrackedDrops;
            const Descriptor &selected =
                descriptors[descriptorIndex(key.tokenTile)];
            return selected.active &&
                    selected.key.tokenTile == key.tokenTile
                ? SealResult::Stale : SealResult::Untracked;
        }
        if (descriptor->sealedPages.test(page))
            return SealResult::Duplicate;
        descriptor->sealedPages.set(page);
        return SealResult::Sealed;
    }

    CaptureResult capture(const Key &key, uint16_t line,
                          uint64_t transactionID, uint16_t wordMask,
                          uint8_t wordBytes, const std::byte *payload,
                          std::size_t payloadBytes, uint64_t nowCycle)
    {
        advanceWrites(nowCycle);
        if (configuredCapacity == 0)
            return CaptureResult::Disabled;
        if (!validKey(key)) {
            ++stats.staleUntrackedDrops;
            if (key.tokenTile != NoTokenTile) {
                Descriptor &selected =
                    descriptors[descriptorIndex(key.tokenTile)];
                if (selected.active && line < selected.lineCount) {
                    poison(selected, line);
                    ++stats.invalidPoisons;
                    return CaptureResult::InvalidPoison;
                }
            }
            return CaptureResult::Invalid;
        }

        Descriptor *descriptor = findExact(key);
        if (descriptor == nullptr) {
            ++stats.staleUntrackedDrops;
            Descriptor &selected = descriptors[descriptorIndex(key.tokenTile)];
            if (selected.active && line < selected.lineCount) {
                poison(selected, line);
                return CaptureResult::StalePoison;
            }
            return CaptureResult::Untracked;
        }
        if (line >= descriptor->lineCount || transactionID == 0 ||
            payload == nullptr || payloadBytes != LineBytes ||
            !validWordGeometry(wordBytes, wordMask)) {
            if (line < descriptor->lineCount)
                poison(*descriptor, line);
            ++stats.invalidPoisons;
            return CaptureResult::InvalidPoison;
        }
        if (descriptor->sealedPages.test(pageIndex(*descriptor, line))) {
            poison(*descriptor, line);
            ++stats.staleUntrackedDrops;
            return CaptureResult::StalePoison;
        }
        if (descriptor->poison.test(line))
            return CaptureResult::AlreadyPoisoned;

        const uint8_t bank = bankIndex(line);
        if (!reservePort(writePortNextAvailableCycle[bank], nowCycle)) {
            poison(*descriptor, line);
            ++stats.writePortPoisons;
            return CaptureResult::WritePortPoison;
        }

        const uint16_t selected = entryIndex(key, line);
        Entry resident = logicalEntry(selected);
        if (resident.valid && sameEntry(resident, key, line)) {
            if ((resident.wordMask & wordMask) != 0) {
                poison(*descriptor, line);
                invalidateExactEntry(selected, key, line);
                ++stats.overlapPoisons;
                return CaptureResult::OverlapPoison;
            }
            mergeWords(resident.payload, payload, wordMask, wordBytes);
            resident.wordMask |= wordMask;
            resident.transactionID = transactionID;
            armWrite(bank, selected, resident, nowCycle);
            accountAccepted(wordMask);
            if (resident.wordMask == fullMask(wordBytes)) {
                ++stats.reconstructedLines;
                return CaptureResult::Reconstructed;
            }
            return CaptureResult::Accepted;
        }

        if (resident.valid && entryOwnerActive(resident)) {
            poison(*descriptor, line);
            ++stats.tagConflicts;
            return CaptureResult::ConflictPoison;
        }

        Entry incoming;
        incoming.key = key;
        incoming.line = line;
        incoming.transactionID = transactionID;
        incoming.wordMask = wordMask;
        incoming.valid = true;
        mergeWords(incoming.payload, payload, wordMask, wordBytes);
        armWrite(bank, selected, incoming, nowCycle);
        ++descriptor->storedEntries;
        ++activeEntries;
        stats.occupancy = activeEntries;
        if (activeEntries > stats.occupancyHighWater)
            stats.occupancyHighWater = activeEntries;
        accountAccepted(wordMask);
        if (wordMask == fullMask(wordBytes)) {
            ++stats.reconstructedLines;
            return CaptureResult::Reconstructed;
        }
        return CaptureResult::Accepted;
    }

    ProbeResult probe(const Key &key, uint16_t line, uint8_t wordBytes,
                      uint64_t nowCycle, Line *result)
    {
        advanceWrites(nowCycle);
        if (result != nullptr)
            *result = {};
        if (configuredCapacity == 0)
            return ProbeResult::Disabled;
        if (!validKey(key) || !validWordBytes(wordBytes))
            return ProbeResult::Invalid;
        if (output.valid) {
            ++stats.readPortStalls;
            return ProbeResult::PortBusy;
        }
        if (!reservePort(readPortNextAvailableCycle, nowCycle)) {
            ++stats.readPortStalls;
            return ProbeResult::PortBusy;
        }

        Descriptor *descriptor = findExact(key);
        if (descriptor == nullptr || line >= descriptor->lineCount ||
            !descriptor->sealedPages.test(pageIndex(*descriptor, line)) ||
            descriptor->poison.test(line)) {
            ++stats.replayMisses;
            return ProbeResult::Miss;
        }
        // Read-before-write SRAM semantics: a write issued in this cycle is
        // still only in its bank input latch and cannot satisfy this read.
        const Entry resident = entries[entryIndex(key, line)];
        if (!resident.valid || !sameEntry(resident, key, line) ||
            resident.wordMask != fullMask(wordBytes)) {
            ++stats.replayMisses;
            return ProbeResult::Miss;
        }
        output.valid = true;
        output.key = key;
        output.line = line;
        output.transactionID = resident.transactionID;
        output.payload = resident.payload;
        ++stats.replayHits;
        if (result != nullptr) {
            result->line = line;
            result->transactionID = output.transactionID;
            result->payload = output.payload.data();
        }
        return ProbeResult::Hit;
    }

    bool take(const Key &key, uint16_t line, uint64_t transactionID,
              uint64_t nowCycle)
    {
        advanceWrites(nowCycle);
        if (!output.valid || !sameKey(output.key, key) ||
            output.line != line || output.transactionID != transactionID)
            return false;

        const uint16_t selected = entryIndex(key, line);
        const Entry resident = logicalEntry(selected);
        if (resident.valid && sameEntry(resident, key, line) &&
            resident.transactionID == transactionID) {
            entries[selected] = Entry{};
            Descriptor *descriptor = findExact(key);
            if (descriptor != nullptr) {
                --descriptor->storedEntries;
                --activeEntries;
                stats.occupancy = activeEntries;
            }
        }
        output = OutputLatch{};
        return true;
    }

    ClearResult clear(const Key &key)
    {
        Descriptor *descriptor = findExact(key);
        if (descriptor == nullptr)
            return {};
        const bool latchSurvives = output.valid && sameKey(output.key, key);
        const ClearResult result{
            static_cast<uint16_t>(
                descriptor->storedEntries - latchSurvives),
            static_cast<uint16_t>(descriptor->poison.count()),
            static_cast<uint8_t>(latchSurvives), true};
        activeEntries -= descriptor->storedEntries;
        stats.occupancy = activeEntries;
        ++stats.clears;
        *descriptor = Descriptor{};
        return result;
    }

    bool active(const Key &key) const { return findExact(key) != nullptr; }
    bool poisoned(const Key &key, uint16_t line) const
    {
        const Descriptor *descriptor = findExact(key);
        return descriptor != nullptr && line < descriptor->lineCount &&
            descriptor->poison.test(line);
    }
    bool pageSealed(const Key &key, uint8_t page) const
    {
        const Descriptor *descriptor = findExact(key);
        return descriptor != nullptr && page < LogicalPageCount &&
            descriptor->sealedPages.test(page);
    }
    Counters counters() const { return stats; }
    const std::byte *pipelinedPayload() const
    {
        return output.payload.data();
    }
    uint64_t pipelinedTransactionID() const
    {
        return output.valid ? output.transactionID : 0;
    }

    static constexpr uint8_t descriptorIndexForToken(uint16_t tokenTile)
    {
        return static_cast<uint8_t>(tokenTile & (DescriptorCount - 1));
    }
    static constexpr uint8_t bankIndexForLine(uint16_t line)
    {
        return static_cast<uint8_t>(line & (BankCount - 1));
    }
    uint16_t selectedEntry(const Key &key, uint16_t line) const
    {
        return configuredCapacity == 0 ? 0 : entryIndex(key, line);
    }
    static constexpr uint16_t entriesPerPartition(uint16_t capacity)
    {
        return capacity / PartitionCount;
    }
    static constexpr uint16_t entriesPerBankPerPartition(uint16_t capacity)
    {
        return capacity / (PartitionCount * BankCount);
    }
    static constexpr bool validCapacity(uint16_t capacity)
    {
        return capacity == 0 || capacity == 512 || capacity == 1024 ||
            capacity == 2048 || capacity == 4096;
    }

    // Packed RTL accounting; host sizeof() is intentionally excluded.
    static constexpr std::size_t KeyBits = 16 + 64 + 64 + 64;
    static constexpr std::size_t EntryTagBits =
        1 + KeyBits + 16 + 16 + 64;
    static constexpr std::size_t DescriptorBits =
        1 + KeyBits + 16 + LogicalPageCount + 13;
    static constexpr std::size_t PoisonBits =
        DescriptorCount * MaxLogicalLines;
    static constexpr std::size_t OutputTagBits = 1 + KeyBits + 16 + 64;
    static constexpr std::size_t CounterBits = 13 * 64 + 2 * 13;
    static constexpr std::size_t MAALookupControlBits =
        (16 + 64 + 64) + (2 + 16 + 5 + 3 + 64 + 16 + 64) +
        (64 + 64) + (1 + 64 + 3);
    static constexpr std::size_t IncarnationBitsPerToken = 64;

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
        return capacity == 0 ? 0
            : static_cast<std::size_t>(capacity) * LineBytes * 8;
    }
    static constexpr std::size_t provisionedTagBits(uint16_t capacity)
    {
        return capacity == 0 ? 0
            : static_cast<std::size_t>(capacity) * EntryTagBits;
    }
    static constexpr std::size_t provisionedDescriptorBits(uint16_t capacity)
    {
        return capacity == 0 ? 0 : DescriptorCount * DescriptorBits;
    }
    static constexpr std::size_t provisionedPoisonBits(uint16_t capacity)
    {
        return capacity == 0 ? 0 : PoisonBits;
    }
    static constexpr std::size_t provisionedWritePortStateBits(
        uint16_t capacity)
    {
        return capacity == 0 ? 0
            : BankCount * (64 + 1 + 64 + indexBits(capacity) +
                           LineBytes * 8 + EntryTagBits);
    }
    static constexpr std::size_t provisionedReadPortStateBits(
        uint16_t capacity)
    {
        return capacity == 0 ? 0 : 64;
    }
    static constexpr std::size_t provisionedOutputBits(uint16_t capacity)
    {
        return capacity == 0 ? 0 : LineBytes * 8 + OutputTagBits;
    }
    static constexpr std::size_t provisionedControlBits(uint16_t capacity)
    {
        return capacity == 0 ? 0
            : provisionedTagBits(capacity) +
                provisionedDescriptorBits(capacity) +
                provisionedPoisonBits(capacity) +
                provisionedWritePortStateBits(capacity) +
                provisionedReadPortStateBits(capacity) +
                OutputTagBits + CounterBits + 13;
    }
    static constexpr std::size_t provisionedTotalBits(uint16_t capacity)
    {
        return capacity == 0 ? 0
            : provisionedPayloadBits(capacity) + LineBytes * 8 +
                provisionedControlBits(capacity);
    }
    static constexpr std::size_t provisionedMAAPersistentStateBits(
        uint16_t capacity, std::size_t tokenCount)
    {
        return capacity == 0 ? 0
            : tokenCount * IncarnationBitsPerToken;
    }
    static constexpr std::size_t provisionedCombinedTotalBits(
        uint16_t capacity, std::size_t tokenCount)
    {
        return capacity == 0 ? 0
            : provisionedTotalBits(capacity) + MAALookupControlBits +
                provisionedMAAPersistentStateBits(capacity, tokenCount);
    }
    static constexpr std::size_t provisionedPayloadBytes(uint16_t capacity)
    {
        return bitsToBytes(provisionedPayloadBits(capacity));
    }
    static constexpr std::size_t provisionedControlBytes(uint16_t capacity)
    {
        return bitsToBytes(provisionedControlBits(capacity));
    }
    static constexpr std::size_t provisionedCombinedTotalBytes(
        uint16_t capacity, std::size_t tokenCount)
    {
        return bitsToBytes(provisionedCombinedTotalBits(capacity, tokenCount));
    }

    /** Host-only exhaustive test diagnostic; no lifecycle path calls it. */
    bool assertInvariants() const
    {
        if (!validCapacity(configuredCapacity) ||
            activeEntries > configuredCapacity ||
            stats.occupancy != activeEntries ||
            stats.occupancyHighWater > configuredCapacity)
            return false;
        uint16_t counted = 0;
        for (uint8_t descriptorIndex = 0;
             descriptorIndex < DescriptorCount; ++descriptorIndex) {
            const Descriptor &descriptor = descriptors[descriptorIndex];
            if (!descriptor.active)
                continue;
            if (!validKey(descriptor.key) ||
                descriptorIndexForToken(descriptor.key.tokenTile) !=
                    descriptorIndex || descriptor.lineCount == 0 ||
                descriptor.lineCount > MaxLogicalLines)
                return false;
            uint16_t owned = 0;
            for (uint16_t entry = 0; entry < configuredCapacity; ++entry) {
                const Entry resident = logicalEntry(entry);
                owned += resident.valid &&
                    sameKey(resident.key, descriptor.key);
            }
            if (owned != descriptor.storedEntries)
                return false;
            counted += owned;
        }
        return counted == activeEntries;
    }

  private:
    struct Entry
    {
        std::array<std::byte, LineBytes> payload{};
        Key key{};
        uint16_t line = 0;
        uint16_t wordMask = 0;
        uint64_t transactionID = 0;
        bool valid = false;
    };

    struct Descriptor
    {
        Key key{};
        std::bitset<MaxLogicalLines> poison{};
        std::bitset<LogicalPageCount> sealedPages{};
        uint16_t lineCount = 0;
        uint16_t storedEntries = 0;
        bool active = false;
    };

    struct PendingWrite
    {
        Entry entry{};
        uint64_t completionCycle = 0;
        uint16_t index = 0;
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

    static constexpr bool sameKey(const Key &lhs, const Key &rhs)
    {
        return lhs.tokenTile == rhs.tokenTile &&
            lhs.generation == rhs.generation &&
            lhs.incarnation == rhs.incarnation &&
            lhs.backingAddress == rhs.backingAddress;
    }
    static constexpr bool sameEntry(const Entry &entry, const Key &key,
                                    uint16_t line)
    {
        return entry.valid && entry.line == line && sameKey(entry.key, key);
    }
    static constexpr bool validKey(const Key &key)
    {
        return key.tokenTile != NoTokenTile && key.generation != 0 &&
            key.incarnation != 0 && key.backingAddress != 0;
    }
    static constexpr bool newer(const Key &resident, const Key &candidate)
    {
        return resident.incarnation > candidate.incarnation ||
            (resident.incarnation == candidate.incarnation &&
             resident.generation > candidate.generation);
    }
    static constexpr uint8_t descriptorIndex(uint16_t tokenTile)
    {
        return descriptorIndexForToken(tokenTile);
    }
    static constexpr uint8_t bankIndex(uint16_t line)
    {
        return bankIndexForLine(line);
    }
    static constexpr bool validWordBytes(uint8_t wordBytes)
    {
        return wordBytes == 4 || wordBytes == 8;
    }
    static constexpr uint16_t fullMask(uint8_t wordBytes)
    {
        return wordBytes == 4 ? std::numeric_limits<uint16_t>::max()
                              : uint16_t{0xff};
    }
    static constexpr bool validWordGeometry(uint8_t wordBytes,
                                            uint16_t wordMask)
    {
        return validWordBytes(wordBytes) && wordMask != 0 &&
            (wordMask & ~fullMask(wordBytes)) == 0;
    }
    static uint8_t popcount(uint16_t value)
    {
        uint8_t count = 0;
        while (value != 0) {
            count += value & 1U;
            value >>= 1;
        }
        return count;
    }
    uint16_t entryIndex(const Key &key, uint16_t line) const
    {
        const uint16_t perPartition =
            entriesPerPartition(configuredCapacity);
        const uint16_t perBank =
            entriesPerBankPerPartition(configuredCapacity);
        return descriptorIndex(key.tokenTile) * perPartition +
            bankIndex(line) * perBank +
            ((line >> 2) & (perBank - 1));
    }
    static uint8_t pageIndex(const Descriptor &descriptor, uint16_t line)
    {
        return static_cast<uint8_t>(
            line / (descriptor.lineCount / LogicalPageCount));
    }
    Descriptor *findExact(const Key &key)
    {
        Descriptor &descriptor = descriptors[descriptorIndex(key.tokenTile)];
        return descriptor.active && sameKey(descriptor.key, key)
            ? &descriptor : nullptr;
    }
    const Descriptor *findExact(const Key &key) const
    {
        const Descriptor &descriptor =
            descriptors[descriptorIndex(key.tokenTile)];
        return descriptor.active && sameKey(descriptor.key, key)
            ? &descriptor : nullptr;
    }
    bool entryOwnerActive(const Entry &entry) const
    {
        return findExact(entry.key) != nullptr;
    }
    void reset(Descriptor &descriptor, const Key &key, uint16_t lineCount)
    {
        descriptor = Descriptor{};
        descriptor.key = key;
        descriptor.lineCount = lineCount;
        descriptor.active = true;
    }
    static bool reservePort(uint64_t &nextAvailable, uint64_t nowCycle)
    {
        if (nowCycle < nextAvailable)
            return false;
        nextAvailable = nowCycle + PortAccessCycles;
        return true;
    }
    static void mergeWords(std::array<std::byte, LineBytes> &destination,
                           const std::byte *source, uint16_t wordMask,
                           uint8_t wordBytes)
    {
        const uint8_t words = LineBytes / wordBytes;
        for (uint8_t word = 0; word < words; ++word) {
            if ((wordMask & (uint16_t{1} << word)) == 0)
                continue;
            std::memcpy(destination.data() + word * wordBytes,
                        source + word * wordBytes, wordBytes);
        }
    }
    void poison(Descriptor &descriptor, uint16_t line)
    {
        descriptor.poison.set(line);
    }
    void accountAccepted(uint16_t wordMask)
    {
        ++stats.fragmentsAccepted;
        stats.wordsMerged += popcount(wordMask);
    }
    void armWrite(uint8_t bank, uint16_t index, const Entry &entry,
                  uint64_t nowCycle)
    {
        pendingWrites[bank].active = true;
        pendingWrites[bank].completionCycle = nowCycle + PortAccessCycles;
        pendingWrites[bank].index = index;
        pendingWrites[bank].entry = entry;
    }
    void advanceWrites(uint64_t nowCycle)
    {
        for (PendingWrite &pending : pendingWrites) {
            if (!pending.active || pending.completionCycle > nowCycle)
                continue;
            entries[pending.index] = pending.entry;
            pending = PendingWrite{};
        }
    }
    Entry logicalEntry(uint16_t index) const
    {
        for (const PendingWrite &pending : pendingWrites) {
            if (pending.active && pending.index == index)
                return pending.entry;
        }
        return entries[index];
    }
    void invalidateExactEntry(uint16_t index, const Key &key, uint16_t line)
    {
        Entry resident = logicalEntry(index);
        if (!sameEntry(resident, key, line))
            return;
        entries[index] = Entry{};
        for (PendingWrite &pending : pendingWrites) {
            if (pending.active && pending.index == index)
                pending = PendingWrite{};
        }
        Descriptor *descriptor = findExact(key);
        if (descriptor != nullptr) {
            --descriptor->storedEntries;
            --activeEntries;
            stats.occupancy = activeEntries;
        }
    }

    uint16_t configuredCapacity = 0;
    uint16_t activeEntries = 0;
    uint64_t readPortNextAvailableCycle = 0;
    std::array<uint64_t, BankCount> writePortNextAvailableCycle{};
    std::array<Descriptor, DescriptorCount> descriptors{};
    std::array<Entry, MaxEntries> entries{};
    std::array<PendingWrite, BankCount> pendingWrites{};
    OutputLatch output{};
    Counters stats{};
};

} // namespace gem5

#endif // __MEM_MAA_INACTIVE_PRODUCER_MASKED_FRAGMENT_RETENTION_HH__
