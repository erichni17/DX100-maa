#ifndef __MEM_MAA_COMPLETE_LINE_PAYLOAD_PORT_HH__
#define __MEM_MAA_COMPLETE_LINE_PAYLOAD_PORT_HH__

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <vector>

namespace gem5::maa
{

/**
 * Fixed metadata for timing complete combiner-line payload reads.
 *
 * Payload bytes and word references remain owned by the caller's combiner
 * payload store.  This object retains only an operation generation and one
 * generation/progress/ready tuple per combiner slot.  A returned ReadGrant
 * tells the caller which resident words consumed the shared port in the
 * current MAA cycle; it never copies or retains those words.
 *
 * A line becomes issue-ready on the cycle after its final read grant, so a
 * nonzero width adds exactly ceil(words-per-line / width) MAA cycles.
 *
 * A ready entry remains stable across downstream write-credit or address
 * retries.  The caller removes it only after the exact full-line write has
 * been accepted through issue().
 */
class CompleteLinePayloadPort
{
  public:
    static constexpr uint32_t MaxLineWords = 16;
    static constexpr uint32_t StartsPerCycle = 1;

    struct Identity
    {
        uint64_t operationGeneration = 0;
        uint64_t lineGeneration = 0;
        uint32_t slot = 0;
    };

    struct ReadGrant
    {
        uint32_t firstWord = 0;
        uint32_t words = 0;
        bool started = false;
        bool ready = false;
    };

    struct Counters
    {
        uint64_t starts = 0;
        uint64_t wordReads = 0;
        uint64_t readCycles = 0;
        uint64_t readyLines = 0;
        uint64_t issuedLines = 0;
        uint64_t readyRetryCycles = 0;
        uint32_t peakStartsPerCycle = 0;
        uint32_t peakWordReadsPerCycle = 0;
        uint32_t peakActiveLines = 0;
    };

    enum class Result : uint8_t
    {
        Accepted,
        Disabled,
        InvalidConfiguration,
        Busy,
        Inactive,
        NonMonotonicCycle,
        CycleOverflow,
        InvalidIdentity,
        StaleIdentity,
        MismatchedIdentity,
        NoBandwidth,
        NotReady,
        Imbalance,
    };

    static constexpr bool
    validWidth(uint32_t width)
    {
        return width == 0 || width == 1 || width == 2 || width == 4 ||
               width == 8;
    }

    static constexpr bool
    validLineWords(uint32_t words)
    {
        return words == 8 || words == 16;
    }

    Result
    configure(uint32_t width, size_t slots)
    {
        if (!validWidth(width) ||
            (width != 0 &&
             (slots == 0 ||
              slots > std::numeric_limits<uint32_t>::max()))) {
            return Result::InvalidConfiguration;
        }
        if (active || activeLines != 0)
            return Result::Busy;
        wordsPerCycle = width;
        entries.assign(width == 0 ? 0 : slots, {});
        clearOperationState();
        return Result::Accepted;
    }

    Result
    begin(uint64_t generation, uint32_t words_per_line)
    {
        if (!enabled())
            return Result::Disabled;
        if (active || activeLines != 0)
            return Result::Busy;
        if (generation == 0 || !validLineWords(words_per_line))
            return Result::InvalidConfiguration;
        for (auto &entry : entries)
            entry = Entry{};
        operationGeneration = generation;
        lineWords = words_per_line;
        active = true;
        portCounters = {};
        cycleValid = false;
        readyRetryCycleValid = false;
        return Result::Accepted;
    }

    Result
    access(uint64_t cycle, const Identity &identity, ReadGrant &grant)
    {
        grant = {};
        if (!enabled())
            return Result::Disabled;
        if (!active)
            return Result::Inactive;
        const Result identity_result = validateIdentity(identity);
        if (identity_result != Result::Accepted)
            return identity_result;
        const Result cycle_result = beginCycle(cycle);
        if (cycle_result != Result::Accepted)
            return cycle_result;

        Entry &entry = entries[identity.slot];
        if (entry.active && entry.lineGeneration != identity.lineGeneration)
            return Result::MismatchedIdentity;
        if (entry.progress == lineWords) {
            if (entry.readyCycle == 0)
                return Result::Imbalance;
            if (cycle >= entry.readyCycle && !entry.ready) {
                entry.ready = true;
                ++portCounters.readyLines;
            }
            grant.ready = entry.ready;
            return Result::Accepted;
        }
        if (!entry.active) {
            if (startsThisCycle >= StartsPerCycle ||
                wordReadsThisCycle >= wordsPerCycle) {
                return Result::NoBandwidth;
            }
            entry.active = true;
            entry.lineGeneration = identity.lineGeneration;
            ++activeLines;
            ++startsThisCycle;
            ++portCounters.starts;
            grant.started = true;
            portCounters.peakStartsPerCycle = std::max(
                portCounters.peakStartsPerCycle, startsThisCycle);
            portCounters.peakActiveLines = std::max(
                portCounters.peakActiveLines, activeLines);
        }

        const uint32_t available = wordsPerCycle - wordReadsThisCycle;
        if (available == 0)
            return Result::NoBandwidth;
        grant.firstWord = entry.progress;
        grant.words = std::min(available, lineWords - entry.progress);
        if (entry.progress + grant.words == lineWords &&
            cycle == std::numeric_limits<uint64_t>::max()) {
            return Result::CycleOverflow;
        }
        if (wordReadsThisCycle == 0)
            ++portCounters.readCycles;
        entry.progress += grant.words;
        wordReadsThisCycle += grant.words;
        portCounters.wordReads += grant.words;
        portCounters.peakWordReadsPerCycle = std::max(
            portCounters.peakWordReadsPerCycle, wordReadsThisCycle);
        if (entry.progress == lineWords)
            entry.readyCycle = cycle + 1;
        return Result::Accepted;
    }

    Result
    recordReadyRetry(uint64_t cycle, const Identity &identity)
    {
        if (!enabled())
            return Result::Disabled;
        if (!active)
            return Result::Inactive;
        const Result identity_result = validateLiveIdentity(identity);
        if (identity_result != Result::Accepted)
            return identity_result;
        const Result cycle_result = beginCycle(cycle);
        if (cycle_result != Result::Accepted)
            return cycle_result;
        if (!entries[identity.slot].ready)
            return Result::NotReady;
        if (!readyRetryCycleValid || readyRetryCycle != cycle) {
            ++portCounters.readyRetryCycles;
            readyRetryCycle = cycle;
            readyRetryCycleValid = true;
        }
        return Result::Accepted;
    }

    Result
    issue(const Identity &identity)
    {
        if (!enabled())
            return Result::Disabled;
        if (!active)
            return Result::Inactive;
        const Result identity_result = validateLiveIdentity(identity);
        if (identity_result != Result::Accepted)
            return identity_result;
        Entry &entry = entries[identity.slot];
        if (!entry.ready)
            return Result::NotReady;
        entry = Entry{};
        --activeLines;
        ++portCounters.issuedLines;
        return Result::Accepted;
    }

    Result
    finish(uint64_t generation)
    {
        if (!enabled())
            return Result::Disabled;
        if (!active)
            return Result::Inactive;
        if (generation != operationGeneration)
            return Result::StaleIdentity;
        if (activeLines != 0 ||
            portCounters.starts != portCounters.readyLines ||
            portCounters.readyLines != portCounters.issuedLines ||
            portCounters.wordReads != portCounters.issuedLines * lineWords) {
            return Result::Imbalance;
        }
        active = false;
        operationGeneration = 0;
        lineWords = 0;
        cycleValid = false;
        readyRetryCycleValid = false;
        return Result::Accepted;
    }

    bool enabled() const { return wordsPerCycle != 0; }
    bool isActive() const { return active; }
    bool empty() const { return activeLines == 0; }
    uint32_t width() const { return wordsPerCycle; }
    uint32_t wordsPerLine() const { return lineWords; }
    uint32_t activeCount() const { return activeLines; }
    size_t capacity() const { return entries.size(); }
    uint64_t generation() const { return operationGeneration; }
    const Counters &counters() const { return portCounters; }

    static const char *
    resultName(Result result)
    {
        switch (result) {
          case Result::Accepted: return "accepted";
          case Result::Disabled: return "disabled";
          case Result::InvalidConfiguration: return "invalid_configuration";
          case Result::Busy: return "busy";
          case Result::Inactive: return "inactive";
          case Result::NonMonotonicCycle: return "non_monotonic_cycle";
          case Result::CycleOverflow: return "cycle_overflow";
          case Result::InvalidIdentity: return "invalid_identity";
          case Result::StaleIdentity: return "stale_identity";
          case Result::MismatchedIdentity: return "mismatched_identity";
          case Result::NoBandwidth: return "no_bandwidth";
          case Result::NotReady: return "not_ready";
          case Result::Imbalance: return "imbalance";
        }
        return "unknown";
    }

  private:
    struct Entry
    {
        uint64_t lineGeneration = 0;
        uint64_t readyCycle = 0;
        uint32_t progress = 0;
        bool active = false;
        bool ready = false;
    };

    Result
    validateIdentity(const Identity &identity) const
    {
        if (identity.operationGeneration == 0 ||
            identity.lineGeneration == 0 || identity.slot >= entries.size()) {
            return Result::InvalidIdentity;
        }
        if (identity.operationGeneration != operationGeneration)
            return Result::StaleIdentity;
        return Result::Accepted;
    }

    Result
    validateLiveIdentity(const Identity &identity) const
    {
        const Result result = validateIdentity(identity);
        if (result != Result::Accepted)
            return result;
        const Entry &entry = entries[identity.slot];
        if (!entry.active)
            return Result::StaleIdentity;
        if (entry.lineGeneration != identity.lineGeneration)
            return Result::MismatchedIdentity;
        return Result::Accepted;
    }

    Result
    beginCycle(uint64_t cycle)
    {
        if (cycleValid && cycle < observedCycle)
            return Result::NonMonotonicCycle;
        if (!cycleValid || cycle != observedCycle) {
            observedCycle = cycle;
            startsThisCycle = 0;
            wordReadsThisCycle = 0;
            cycleValid = true;
        }
        return Result::Accepted;
    }

    void
    clearOperationState()
    {
        operationGeneration = 0;
        observedCycle = 0;
        readyRetryCycle = 0;
        lineWords = 0;
        activeLines = 0;
        startsThisCycle = 0;
        wordReadsThisCycle = 0;
        active = false;
        cycleValid = false;
        readyRetryCycleValid = false;
        portCounters = {};
    }

    std::vector<Entry> entries;
    Counters portCounters{};
    uint64_t operationGeneration = 0;
    uint64_t observedCycle = 0;
    uint64_t readyRetryCycle = 0;
    uint32_t wordsPerCycle = 0;
    uint32_t lineWords = 0;
    uint32_t activeLines = 0;
    uint32_t startsThisCycle = 0;
    uint32_t wordReadsThisCycle = 0;
    bool active = false;
    bool cycleValid = false;
    bool readyRetryCycleValid = false;
};

} // namespace gem5::maa

#endif // __MEM_MAA_COMPLETE_LINE_PAYLOAD_PORT_HH__
