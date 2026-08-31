#ifndef __MEM_MAA_COMPLETE_LINE_PAYLOAD_STAGING_HH__
#define __MEM_MAA_COMPLETE_LINE_PAYLOAD_STAGING_HH__

#include <algorithm>
#include <array>
#include <cstdint>
#include <vector>

namespace gem5::maa
{

class CompleteLinePayloadStaging
{
  public:
    static constexpr uint32_t MaxActiveLines = 16;
    static constexpr uint32_t MaxPayloadBanks = 64;

    // This is a read-port timing model. Payload remains in the bounded
    // combiner store; entries retain only identity and progress.
    struct Identity
    {
        uint64_t generation = 0;
        uint32_t slot = 0;
        uint64_t lineAddress = 0;
        uint16_t validWords = 0;
        uint8_t totalWords = 0;
        uint8_t bankCount = 0;
        std::array<uint8_t, MaxPayloadBanks> bankWords{};

        bool operator==(const Identity &other) const
        {
            return generation == other.generation && slot == other.slot &&
                   lineAddress == other.lineAddress &&
                   validWords == other.validWords &&
                   totalWords == other.totalWords &&
                   bankCount == other.bankCount &&
                   bankWords == other.bankWords;
        }
    };

    struct Counters
    {
        uint64_t starts = 0;
        uint64_t completions = 0;
        uint64_t readCycles = 0;
        uint64_t blockedCycles = 0;
        uint64_t scheduledWords = 0;
        uint64_t readWords = 0;
        uint64_t serialReadCycles = 0;
        uint64_t bankConflictCycles = 0;
        uint32_t peakActive = 0;
    };

    enum class Result : uint8_t
    {
        Accepted,
        Disabled,
        Busy,
        Invalid,
        Stale,
        NotReady,
        NonMonotonicCycle,
    };

    static constexpr bool validWidth(uint32_t width)
    {
        return width == 0 || width == 1 || width == 2 || width == 4 ||
               width == 8;
    }

    static constexpr bool validActiveLines(uint32_t lines)
    {
        return lines == 1 || lines == 2 || lines == 4 || lines == 8 ||
               lines == MaxActiveLines;
    }

    static constexpr bool validBanks(uint32_t banks)
    {
        return banks == 0 || banks == 1 || banks == 2 || banks == 4 ||
               banks == 8 || banks == 16 || banks == 32 ||
               banks == MaxPayloadBanks;
    }

    bool configure(uint32_t width, uint32_t lines = 1, uint32_t banks = 0)
    {
        if (!validWidth(width) || !validActiveLines(lines) ||
            !validBanks(banks) || isActive())
            return false;
        wordsPerCycle = width;
        activeLimit = lines;
        payloadBanks = banks;
        reset();
        return true;
    }

    void reset()
    {
        entries.assign(activeLimit, {});
        activeEntries = 0;
        serviceCursor = 0;
        lastCycle = 0;
        cycleValid = false;
        blockedCycle = 0;
        blockedCycleValid = false;
        stagingCounters = {};
    }

    Result claim(const Identity &identity, uint64_t cycle)
    {
        if (wordsPerCycle == 0)
            return Result::Disabled;
        if (!valid(identity) || identity.bankCount != payloadBanks)
            return Result::Invalid;
        const Result serviced = service(cycle);
        if (serviced != Result::Accepted)
            return serviced;
        if (find(identity) != MaxActiveLines)
            return Result::Accepted;
        if (activeEntries == activeLimit) {
            recordBlocked(cycle);
            return Result::Busy;
        }
        const uint32_t free = findFree();
        if (free == MaxActiveLines)
            return Result::Busy;
        entries[free].active = true;
        entries[free].identity = identity;
        entries[free].wordsRead = 0;
        entries[free].remainingBanks = identity.bankWords;
        ++activeEntries;
        ++stagingCounters.starts;
        stagingCounters.scheduledWords += identity.totalWords;
        stagingCounters.serialReadCycles += serialCycles(identity);
        stagingCounters.peakActive =
            std::max(stagingCounters.peakActive, activeEntries);
        return Result::Accepted;
    }

    Result advance(const Identity &identity, uint64_t cycle)
    {
        const uint32_t index = find(identity);
        if (index == MaxActiveLines)
            return Result::Stale;
        const Result serviced = service(cycle);
        if (serviced != Result::Accepted)
            return serviced;
        return ready(entries[index]) ? Result::Accepted : Result::NotReady;
    }

    Result complete(const Identity &identity)
    {
        const uint32_t index = find(identity);
        if (index == MaxActiveLines)
            return Result::Stale;
        if (!ready(entries[index]))
            return Result::NotReady;
        entries[index] = {};
        --activeEntries;
        ++stagingCounters.completions;
        return Result::Accepted;
    }

    bool enabled() const { return wordsPerCycle != 0; }
    bool isActive() const { return activeEntries != 0; }
    uint32_t width() const { return wordsPerCycle; }
    uint32_t activeCapacity() const { return activeLimit; }
    uint32_t banks() const { return payloadBanks; }
    uint32_t allocatedEntries() const { return entries.size(); }
    uint32_t activeCount() const { return activeEntries; }

    uint32_t progress() const
    {
        for (const auto &entry : entries) {
            if (entry.active)
                return entry.wordsRead;
        }
        return 0;
    }

    uint32_t progress(const Identity &identity) const
    {
        const uint32_t index = find(identity);
        return index == MaxActiveLines ? 0 : entries[index].wordsRead;
    }

    const Identity &identity() const
    {
        for (const auto &entry : entries) {
            if (entry.active)
                return entry.identity;
        }
        return emptyIdentity;
    }

    bool firstWithMask(uint16_t mask, Identity &identity) const
    {
        for (const auto &entry : entries) {
            if (entry.active && entry.identity.validWords == mask) {
                identity = entry.identity;
                return true;
            }
        }
        return false;
    }

    const Counters &counters() const { return stagingCounters; }

  private:
    struct Entry
    {
        Identity identity{};
        std::array<uint8_t, MaxPayloadBanks> remainingBanks{};
        uint32_t wordsRead = 0;
        uint32_t bankCursor = 0;
        bool active = false;
    };

    static bool valid(const Identity &identity)
    {
        if (identity.generation == 0 || identity.validWords == 0 ||
            identity.totalWords == 0 || identity.totalWords > 16 ||
            !validBanks(identity.bankCount))
            return false;
        uint32_t bank_words = 0;
        for (uint32_t bank = 0; bank < identity.bankWords.size(); ++bank) {
            if (bank >= identity.bankCount && identity.bankWords[bank] != 0)
                return false;
            bank_words += identity.bankWords[bank];
        }
        return identity.bankCount == 0 ? bank_words == 0
                                       : bank_words == identity.totalWords;
    }

    uint32_t serialCycles(const Identity &identity) const
    {
        uint32_t cycles =
            (identity.totalWords + wordsPerCycle - 1) / wordsPerCycle;
        if (identity.bankCount != 0) {
            for (uint32_t bank = 0; bank < identity.bankCount; ++bank)
                cycles = std::max<uint32_t>(cycles,
                                            identity.bankWords[bank]);
        }
        return cycles;
    }

    static bool ready(const Entry &entry)
    {
        return entry.wordsRead == entry.identity.totalWords;
    }

    uint32_t find(const Identity &identity) const
    {
        for (uint32_t index = 0; index < activeLimit; ++index) {
            if (entries[index].active && entries[index].identity == identity)
                return index;
        }
        return MaxActiveLines;
    }

    uint32_t findFree() const
    {
        for (uint32_t index = 0; index < activeLimit; ++index) {
            if (!entries[index].active)
                return index;
        }
        return MaxActiveLines;
    }

    void recordBlocked(uint64_t cycle)
    {
        if (!blockedCycleValid || blockedCycle != cycle) {
            ++stagingCounters.blockedCycles;
            blockedCycle = cycle;
            blockedCycleValid = true;
        }
    }

    Result service(uint64_t cycle)
    {
        if (!cycleValid) {
            lastCycle = cycle;
            cycleValid = true;
            return Result::Accepted;
        }
        if (cycle < lastCycle)
            return Result::NonMonotonicCycle;
        uint64_t elapsed = cycle - lastCycle;
        while (elapsed != 0) {
            uint32_t budget = wordsPerCycle;
            uint64_t used_banks = 0;
            bool read = false;
            uint32_t emptySearches = 0;
            while (budget != 0 && emptySearches < activeLimit) {
                auto &entry = entries[serviceCursor];
                serviceCursor = (serviceCursor + 1) % activeLimit;
                if (!entry.active || ready(entry)) {
                    ++emptySearches;
                    continue;
                }
                int selected_bank = -1;
                if (payloadBanks != 0) {
                    uint8_t selected_words = 0;
                    for (uint32_t offset = 0; offset < payloadBanks;
                         ++offset) {
                        const uint32_t bank =
                            (entry.bankCursor + offset) % payloadBanks;
                        if (entry.remainingBanks[bank] > selected_words &&
                            (used_banks & (uint64_t(1) << bank)) == 0) {
                            selected_bank = bank;
                            selected_words = entry.remainingBanks[bank];
                        }
                    }
                    if (selected_bank == -1) {
                        ++emptySearches;
                        continue;
                    }
                    --entry.remainingBanks[selected_bank];
                    entry.bankCursor =
                        (selected_bank + 1) % payloadBanks;
                    used_banks |= uint64_t(1) << selected_bank;
                }
                ++entry.wordsRead;
                ++stagingCounters.readWords;
                --budget;
                emptySearches = 0;
                read = true;
            }
            if (!read)
                break;
            if (payloadBanks != 0 && budget != 0 && hasUnreadWords())
                ++stagingCounters.bankConflictCycles;
            ++stagingCounters.readCycles;
            --elapsed;
        }
        lastCycle = cycle;
        return Result::Accepted;
    }

    bool hasUnreadWords() const
    {
        return std::any_of(entries.begin(), entries.end(),
                           [](const Entry &entry) {
                               return entry.active && !ready(entry);
                           });
    }

    std::vector<Entry> entries{};
    Identity emptyIdentity{};
    Counters stagingCounters{};
    uint64_t lastCycle = 0;
    uint64_t blockedCycle = 0;
    uint32_t wordsPerCycle = 0;
    uint32_t activeLimit = 1;
    uint32_t activeEntries = 0;
    uint32_t serviceCursor = 0;
    uint32_t payloadBanks = 0;
    bool cycleValid = false;
    bool blockedCycleValid = false;
};

} // namespace gem5::maa

#endif // __MEM_MAA_COMPLETE_LINE_PAYLOAD_STAGING_HH__
