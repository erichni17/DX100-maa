#ifndef __MEM_MAA_COMPLETE_LINE_PAYLOAD_STAGING_HH__
#define __MEM_MAA_COMPLETE_LINE_PAYLOAD_STAGING_HH__

#include <algorithm>
#include <cstdint>
#include <vector>

namespace gem5::maa
{

class CompleteLinePayloadStaging
{
  public:
    static constexpr uint32_t MaxActiveLines = 16;

    // This is a read-port timing model. Payload remains in the bounded
    // combiner store; entries retain only identity and progress.
    struct Identity
    {
        uint64_t generation = 0;
        uint32_t slot = 0;
        uint64_t lineAddress = 0;
        uint16_t validWords = 0;
        uint8_t totalWords = 0;

        bool operator==(const Identity &other) const
        {
            return generation == other.generation && slot == other.slot &&
                   lineAddress == other.lineAddress &&
                   validWords == other.validWords &&
                   totalWords == other.totalWords;
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

    bool configure(uint32_t width, uint32_t lines = 1)
    {
        if (!validWidth(width) || !validActiveLines(lines) || isActive())
            return false;
        wordsPerCycle = width;
        activeLimit = lines;
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
        if (!valid(identity))
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
        ++activeEntries;
        ++stagingCounters.starts;
        stagingCounters.scheduledWords += identity.totalWords;
        stagingCounters.serialReadCycles +=
            (identity.totalWords + wordsPerCycle - 1) / wordsPerCycle;
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
        uint32_t wordsRead = 0;
        bool active = false;
    };

    static bool valid(const Identity &identity)
    {
        return identity.generation != 0 && identity.validWords != 0 &&
               identity.totalWords != 0 && identity.totalWords <= 16;
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
            bool read = false;
            uint32_t emptySearches = 0;
            while (budget != 0 && emptySearches < activeLimit) {
                auto &entry = entries[serviceCursor];
                serviceCursor = (serviceCursor + 1) % activeLimit;
                if (!entry.active || ready(entry)) {
                    ++emptySearches;
                    continue;
                }
                ++entry.wordsRead;
                ++stagingCounters.readWords;
                --budget;
                emptySearches = 0;
                read = true;
            }
            if (!read)
                break;
            ++stagingCounters.readCycles;
            --elapsed;
        }
        lastCycle = cycle;
        return Result::Accepted;
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
    bool cycleValid = false;
    bool blockedCycleValid = false;
};

} // namespace gem5::maa

#endif // __MEM_MAA_COMPLETE_LINE_PAYLOAD_STAGING_HH__
