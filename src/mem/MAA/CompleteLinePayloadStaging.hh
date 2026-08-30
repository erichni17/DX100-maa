#ifndef __MEM_MAA_COMPLETE_LINE_PAYLOAD_STAGING_HH__
#define __MEM_MAA_COMPLETE_LINE_PAYLOAD_STAGING_HH__

#include <algorithm>
#include <cstdint>

namespace gem5::maa
{

class CompleteLinePayloadStaging
{
  public:
    // This is a read-port timing model. The payload remains in the bounded
    // combiner store; only one line identity and its progress are retained.
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

    bool configure(uint32_t width)
    {
        if (!validWidth(width) || active)
            return false;
        wordsPerCycle = width;
        reset();
        return true;
    }

    void reset()
    {
        active = false;
        staged = {};
        wordsRead = 0;
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
        if (active) {
            if (!(staged == identity)) {
                if (!blockedCycleValid || blockedCycle != cycle) {
                    ++stagingCounters.blockedCycles;
                    blockedCycle = cycle;
                    blockedCycleValid = true;
                }
                return Result::Busy;
            }
            return observe(cycle);
        }
        active = true;
        staged = identity;
        wordsRead = 0;
        lastCycle = cycle;
        cycleValid = true;
        blockedCycleValid = false;
        ++stagingCounters.starts;
        return Result::Accepted;
    }

    Result advance(const Identity &identity, uint64_t cycle)
    {
        if (!active || !(staged == identity))
            return Result::Stale;
        const Result observed = observe(cycle);
        if (observed != Result::Accepted)
            return observed;
        if (wordsRead == staged.totalWords)
            return Result::Accepted;
        const uint64_t elapsed = cycle - lastCycle;
        if (elapsed == 0)
            return Result::NotReady;
        const uint64_t remaining = staged.totalWords - wordsRead;
        const uint64_t cyclesNeeded =
            (remaining + wordsPerCycle - 1) / wordsPerCycle;
        const uint64_t used = std::min(elapsed, cyclesNeeded);
        wordsRead += std::min<uint64_t>(remaining, used * wordsPerCycle);
        stagingCounters.readCycles += used;
        lastCycle += used;
        return wordsRead == staged.totalWords ? Result::Accepted
                                               : Result::NotReady;
    }

    Result complete(const Identity &identity)
    {
        if (!active || !(staged == identity))
            return Result::Stale;
        if (wordsRead != staged.totalWords)
            return Result::NotReady;
        active = false;
        staged = {};
        wordsRead = 0;
        cycleValid = false;
        blockedCycleValid = false;
        ++stagingCounters.completions;
        return Result::Accepted;
    }

    bool enabled() const { return wordsPerCycle != 0; }
    bool isActive() const { return active; }
    uint32_t width() const { return wordsPerCycle; }
    uint32_t progress() const { return wordsRead; }
    const Identity &identity() const { return staged; }
    const Counters &counters() const { return stagingCounters; }

  private:
    static bool valid(const Identity &identity)
    {
        return identity.generation != 0 && identity.validWords != 0 &&
               identity.totalWords != 0 && identity.totalWords <= 16;
    }

    Result observe(uint64_t cycle)
    {
        if (cycleValid && cycle < lastCycle)
            return Result::NonMonotonicCycle;
        return Result::Accepted;
    }

    Identity staged{};
    Counters stagingCounters{};
    uint64_t lastCycle = 0;
    uint64_t blockedCycle = 0;
    uint32_t wordsPerCycle = 0;
    uint32_t wordsRead = 0;
    bool active = false;
    bool cycleValid = false;
    bool blockedCycleValid = false;
};

} // namespace gem5::maa

#endif
