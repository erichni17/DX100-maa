#ifndef __MEM_MAA_COMPLETE_LINE_PAYLOAD_STAGING_HH__
#define __MEM_MAA_COMPLETE_LINE_PAYLOAD_STAGING_HH__

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <vector>

namespace gem5::maa
{

/**
 * Bounded metadata-only reads of complete virtual-combiner lines.
 *
 * Entries retain only the exact combiner slot incarnation and read progress;
 * payload bytes remain owned by VirtualCombinePayloadStore.  Reads share one
 * global words-per-cycle budget.  Finishing the final word in cycle C makes
 * the line coherent for issue at cycle C + 1, so an uncontended N-word line
 * spends exactly ceil(N / width) complete MAA cycles in this process.
 */
class CompleteLinePayloadStaging
{
  public:
    struct Identity
    {
        uint32_t slot = 0;
        uint64_t generation = 0;
    };

    struct Counters
    {
        uint64_t issues = 0;
        uint64_t completions = 0;
        uint64_t waitCycles = 0;
        uint32_t peakOccupancy = 0;
    };

    enum class Result : uint8_t
    {
        Ready,
        Waiting,
        Accepted,
        Disabled,
        InvalidConfiguration,
        Busy,
        NonMonotonicCycle,
        CycleOverflow,
        InvalidIdentity,
        StaleIdentity,
        MismatchedIdentity,
        NotStaged,
        NotReady,
        Imbalance,
    };

    static constexpr bool
    validWidth(uint32_t width)
    {
        return width == 0 || width == 1 || width == 2 || width == 4 ||
               width == 8;
    }

    Result
    configure(uint32_t width, size_t slots)
    {
        if (!validWidth(width) || slots == 0 ||
            slots > std::numeric_limits<uint32_t>::max())
            return Result::InvalidConfiguration;
        if (!empty())
            return Result::Busy;
        wordsPerCycle = static_cast<uint8_t>(width);
        entries.assign(width == 0 ? 0 : slots, Entry{});
        reset();
        return Result::Accepted;
    }

    void
    reset()
    {
        std::fill(entries.begin(), entries.end(), Entry{});
        stagingCounters = {};
        occupancyCount = 0;
        observedCycle = 0;
        readsThisCycle = 0;
        startsThisCycle = 0;
        waitCycle = 0;
        cycleValid = false;
        waitCycleValid = false;
    }

    Result
    stage(uint64_t cycle, const Identity &identity, uint32_t totalWords)
    {
        if (!enabled())
            return Result::Disabled;
        const Result cycleResult = beginCycle(cycle);
        if (cycleResult != Result::Accepted)
            return cycleResult;
        if (!validIdentity(identity) || totalWords == 0 || totalWords > 64)
            return Result::InvalidIdentity;

        Entry &entry = entries[identity.slot];
        if (entry.valid) {
            if (!sameIdentity(entry.identity, identity))
                return Result::StaleIdentity;
            if (entry.totalWords != totalWords)
                return Result::MismatchedIdentity;
        } else {
            if (startsThisCycle >= wordsPerCycle ||
                readsThisCycle >= wordsPerCycle) {
                recordWait(cycle);
                return Result::Busy;
            }
            entry.valid = true;
            entry.identity = identity;
            entry.totalWords = static_cast<uint8_t>(totalWords);
            ++startsThisCycle;
            ++occupancyCount;
            ++stagingCounters.issues;
            stagingCounters.peakOccupancy = std::max(
                stagingCounters.peakOccupancy, occupancyCount);
        }

        if (entry.readyCycle != 0) {
            if (entry.readyCycle <= cycle)
                return Result::Ready;
            recordWait(cycle);
            return Result::Waiting;
        }

        if (readsThisCycle == wordsPerCycle) {
            recordWait(cycle);
            return Result::Waiting;
        }
        const uint32_t available = wordsPerCycle - readsThisCycle;
        const uint32_t remaining = entry.totalWords - entry.completedWords;
        const uint32_t read = std::min(available, remaining);
        entry.completedWords += static_cast<uint8_t>(read);
        readsThisCycle += static_cast<uint8_t>(read);
        if (entry.completedWords == entry.totalWords) {
            if (cycle == std::numeric_limits<uint64_t>::max())
                return Result::CycleOverflow;
            entry.readyCycle = cycle + 1;
            ++stagingCounters.completions;
        }
        recordWait(cycle);
        return Result::Waiting;
    }

    Result
    retire(uint64_t cycle, const Identity &identity)
    {
        if (!enabled())
            return Result::Disabled;
        const Result cycleResult = beginCycle(cycle);
        if (cycleResult != Result::Accepted)
            return cycleResult;
        if (!validIdentity(identity))
            return Result::InvalidIdentity;
        Entry &entry = entries[identity.slot];
        if (!entry.valid)
            return Result::NotStaged;
        if (!sameIdentity(entry.identity, identity))
            return Result::StaleIdentity;
        if (entry.readyCycle == 0 || entry.readyCycle > cycle)
            return Result::NotReady;
        entry = Entry{};
        --occupancyCount;
        return Result::Accepted;
    }

    Result
    finish() const
    {
        return empty() && stagingCounters.issues == stagingCounters.completions
            ? Result::Accepted : Result::Imbalance;
    }

    bool enabled() const { return wordsPerCycle != 0; }
    bool empty() const { return occupancyCount == 0; }
    size_t capacity() const { return entries.size(); }
    uint32_t width() const { return wordsPerCycle; }
    uint32_t occupancy() const { return occupancyCount; }
    uint32_t readsInCycle() const { return readsThisCycle; }
    uint32_t startsInCycle() const { return startsThisCycle; }
    const Counters &counters() const { return stagingCounters; }

    uint32_t
    completedWords(const Identity &identity) const
    {
        if (!validIdentity(identity))
            return 0;
        const Entry &entry = entries[identity.slot];
        return entry.valid && sameIdentity(entry.identity, identity)
            ? entry.completedWords : 0;
    }

    uint64_t
    readyCycle(const Identity &identity) const
    {
        if (!validIdentity(identity))
            return 0;
        const Entry &entry = entries[identity.slot];
        return entry.valid && sameIdentity(entry.identity, identity)
            ? entry.readyCycle : 0;
    }

    static const char *
    resultName(Result result)
    {
        switch (result) {
          case Result::Ready: return "ready";
          case Result::Waiting: return "waiting";
          case Result::Accepted: return "accepted";
          case Result::Disabled: return "disabled";
          case Result::InvalidConfiguration: return "invalid_configuration";
          case Result::Busy: return "busy";
          case Result::NonMonotonicCycle: return "non_monotonic_cycle";
          case Result::CycleOverflow: return "cycle_overflow";
          case Result::InvalidIdentity: return "invalid_identity";
          case Result::StaleIdentity: return "stale_identity";
          case Result::MismatchedIdentity: return "mismatched_identity";
          case Result::NotStaged: return "not_staged";
          case Result::NotReady: return "not_ready";
          case Result::Imbalance: return "imbalance";
        }
        return "unknown";
    }

  private:
    struct Entry
    {
        Identity identity{};
        uint64_t readyCycle = 0;
        uint8_t totalWords = 0;
        uint8_t completedWords = 0;
        bool valid = false;
    };

    bool
    validIdentity(const Identity &identity) const
    {
        return identity.generation != 0 && identity.slot < entries.size();
    }

    static bool
    sameIdentity(const Identity &left, const Identity &right)
    {
        return left.slot == right.slot &&
               left.generation == right.generation;
    }

    Result
    beginCycle(uint64_t cycle)
    {
        if (cycleValid && cycle < observedCycle)
            return Result::NonMonotonicCycle;
        if (!cycleValid || cycle != observedCycle) {
            observedCycle = cycle;
            readsThisCycle = 0;
            startsThisCycle = 0;
            cycleValid = true;
        }
        return Result::Accepted;
    }

    void
    recordWait(uint64_t cycle)
    {
        if (!waitCycleValid || waitCycle != cycle) {
            ++stagingCounters.waitCycles;
            waitCycle = cycle;
            waitCycleValid = true;
        }
    }

    std::vector<Entry> entries;
    Counters stagingCounters{};
    uint64_t observedCycle = 0;
    uint64_t waitCycle = 0;
    uint32_t occupancyCount = 0;
    uint8_t wordsPerCycle = 0;
    uint8_t readsThisCycle = 0;
    uint8_t startsThisCycle = 0;
    bool cycleValid = false;
    bool waitCycleValid = false;
};

} // namespace gem5::maa

#endif // __MEM_MAA_COMPLETE_LINE_PAYLOAD_STAGING_HH__
