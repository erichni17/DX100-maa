#ifndef __MEM_MAA_VIRTUAL_COMBINE_LOOKUP_PIPELINE_HH__
#define __MEM_MAA_VIRTUAL_COMBINE_LOOKUP_PIPELINE_HH__

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <list>
#include <vector>

namespace gem5::maa
{

/**
 * Bounded fixed-latency pipeline for ordinary virtual-combiner lookups.
 *
 * A token owns a copied response word and the exact response-slot/Offset
 * identity that produced it.  Starts from the same MAA cycle receive the same
 * ready cycle, so latency does not serialize words.  The caller may complete
 * ready tokens independently when downstream combiner banks or capacity are
 * available.
 */
class VirtualCombineLookupPipeline
{
  public:
    static constexpr uint32_t MaxLatencyCycles = 8;

    struct Token
    {
        uint64_t operationGeneration = 0;
        uint64_t issueSequence = 0;
        uint32_t slotSequence = 0;
        uint32_t responseSlot = 0;
        int32_t offsetSlot = -1;
        int32_t iteration = -1;
        int32_t wordId = -1;
        int32_t pass = -1;
        uint8_t wordBytes = 0;
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
        Accepted,
        Disabled,
        InvalidConfiguration,
        Busy,
        Full,
        Inactive,
        NonMonotonicCycle,
        CycleOverflow,
        InvalidToken,
        StaleToken,
        MismatchedToken,
        NotReady,
        Imbalance,
    };

    static constexpr bool
    validLatency(uint32_t latency)
    {
        return latency <= MaxLatencyCycles;
    }

    Result
    configure(uint32_t latency, size_t capacity)
    {
        if (!validLatency(latency) ||
            (latency != 0 &&
             (capacity == 0 ||
              capacity > std::numeric_limits<uint32_t>::max())))
            return Result::InvalidConfiguration;
        if (active || !pending.empty())
            return Result::Busy;
        latencyCycles = latency;
        capacityLimit = latency == 0 ? 0 : capacity;
        pipelineCounters = {};
        cycleValid = false;
        waitCycleValid = false;
        return Result::Accepted;
    }

    Result
    begin(uint64_t generation)
    {
        if (latencyCycles == 0)
            return Result::Disabled;
        if (active || !pending.empty())
            return Result::Busy;
        if (generation == 0 || capacityLimit == 0)
            return Result::InvalidConfiguration;
        active = true;
        operationGeneration = generation;
        pipelineCounters = {};
        cycleValid = false;
        waitCycleValid = false;
        return Result::Accepted;
    }

    Result
    start(uint64_t cycle, const Token &token)
    {
        const Result cycle_result = observeCycle(cycle);
        if (cycle_result != Result::Accepted)
            return cycle_result;
        if (!active)
            return Result::Inactive;
        if (!validToken(token) ||
            token.operationGeneration != operationGeneration)
            return Result::InvalidToken;
        if (pending.size() >= capacityLimit)
            return Result::Full;
        if (cycle > std::numeric_limits<uint64_t>::max() - latencyCycles)
            return Result::CycleOverflow;
        const auto duplicate = std::find_if(
            pending.begin(), pending.end(), [&token](const Entry &entry) {
                return sameIdentity(entry.token, token);
            });
        if (duplicate != pending.end())
            return Result::MismatchedToken;
        pending.push_back({token, cycle + latencyCycles});
        ++pipelineCounters.issues;
        pipelineCounters.peakOccupancy = std::max(
            pipelineCounters.peakOccupancy,
            static_cast<uint32_t>(pending.size()));
        return Result::Accepted;
    }

    Result
    collectReady(uint64_t cycle, std::vector<Token> &ready)
    {
        ready.clear();
        const Result cycle_result = observeCycle(cycle);
        if (cycle_result != Result::Accepted)
            return cycle_result;
        if (!active)
            return Result::Inactive;
        for (const auto &entry : pending) {
            if (entry.readyCycle <= cycle)
                ready.push_back(entry.token);
        }
        return Result::Accepted;
    }

    Result
    complete(uint64_t cycle, const Token &token)
    {
        const Result cycle_result = observeCycle(cycle);
        if (cycle_result != Result::Accepted)
            return cycle_result;
        if (!active)
            return Result::Inactive;
        auto entry = pending.begin();
        if (entry == pending.end() ||
            !sameIdentity(entry->token, token)) {
            entry = std::find_if(
                pending.begin(), pending.end(),
                [&token](const Entry &candidate) {
                    return sameIdentity(candidate.token, token);
                });
        }
        if (entry == pending.end())
            return Result::StaleToken;
        if (!sameToken(entry->token, token))
            return Result::MismatchedToken;
        if (entry->readyCycle > cycle)
            return Result::NotReady;
        if (entry == pending.begin())
            pending.pop_front();
        else
            pending.erase(entry);
        ++pipelineCounters.completions;
        return Result::Accepted;
    }

    Result
    recordWait(uint64_t cycle)
    {
        const Result cycle_result = observeCycle(cycle);
        if (cycle_result != Result::Accepted)
            return cycle_result;
        if (!active)
            return Result::Inactive;
        const bool has_ready = std::any_of(
            pending.begin(), pending.end(), [cycle](const Entry &entry) {
                return entry.readyCycle <= cycle;
            });
        if (!has_ready)
            return Result::NotReady;
        if (!waitCycleValid || waitCycle != cycle) {
            ++pipelineCounters.waitCycles;
            waitCycle = cycle;
            waitCycleValid = true;
        }
        return Result::Accepted;
    }

    Result
    finish(uint64_t generation)
    {
        if (!active)
            return Result::Inactive;
        if (generation != operationGeneration)
            return Result::StaleToken;
        if (!pending.empty() ||
            pipelineCounters.issues != pipelineCounters.completions)
            return Result::Imbalance;
        active = false;
        operationGeneration = 0;
        cycleValid = false;
        waitCycleValid = false;
        return Result::Accepted;
    }

    bool enabled() const { return latencyCycles != 0; }
    bool isActive() const { return active; }
    bool empty() const { return pending.empty(); }
    size_t occupancy() const { return pending.size(); }
    size_t capacity() const { return capacityLimit; }
    uint32_t latency() const { return latencyCycles; }
    uint64_t generation() const { return operationGeneration; }
    const Counters &counters() const { return pipelineCounters; }

    static const char *
    resultName(Result result)
    {
        switch (result) {
          case Result::Accepted: return "accepted";
          case Result::Disabled: return "disabled";
          case Result::InvalidConfiguration: return "invalid_configuration";
          case Result::Busy: return "busy";
          case Result::Full: return "full";
          case Result::Inactive: return "inactive";
          case Result::NonMonotonicCycle: return "non_monotonic_cycle";
          case Result::CycleOverflow: return "cycle_overflow";
          case Result::InvalidToken: return "invalid_token";
          case Result::StaleToken: return "stale_token";
          case Result::MismatchedToken: return "mismatched_token";
          case Result::NotReady: return "not_ready";
          case Result::Imbalance: return "imbalance";
        }
        return "unknown";
    }

  private:
    struct Entry
    {
        Token token{};
        uint64_t readyCycle = 0;
    };

    static bool
    validToken(const Token &token)
    {
        return token.operationGeneration != 0 && token.offsetSlot >= 0 &&
               token.iteration >= 0 && token.wordId >= 0 &&
               (token.wordBytes == 4 || token.wordBytes == 8);
    }

    static bool
    sameIdentity(const Token &left, const Token &right)
    {
        return left.operationGeneration == right.operationGeneration &&
               left.issueSequence == right.issueSequence &&
               left.slotSequence == right.slotSequence &&
               left.responseSlot == right.responseSlot &&
               left.offsetSlot == right.offsetSlot;
    }

    static bool
    sameToken(const Token &left, const Token &right)
    {
        return sameIdentity(left, right) &&
               left.iteration == right.iteration &&
               left.wordId == right.wordId && left.pass == right.pass &&
               left.wordBytes == right.wordBytes;
    }

    Result
    observeCycle(uint64_t cycle)
    {
        if (cycleValid && cycle < observedCycle)
            return Result::NonMonotonicCycle;
        observedCycle = cycle;
        cycleValid = true;
        return Result::Accepted;
    }

    std::list<Entry> pending;
    Counters pipelineCounters{};
    size_t capacityLimit = 0;
    uint64_t operationGeneration = 0;
    uint64_t observedCycle = 0;
    uint64_t waitCycle = 0;
    uint32_t latencyCycles = 0;
    bool active = false;
    bool cycleValid = false;
    bool waitCycleValid = false;
};

} // namespace gem5::maa

#endif // __MEM_MAA_VIRTUAL_COMBINE_LOOKUP_PIPELINE_HH__
