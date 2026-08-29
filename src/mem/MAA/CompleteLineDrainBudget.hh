#ifndef __MEM_MAA_COMPLETE_LINE_DRAIN_BUDGET_HH__
#define __MEM_MAA_COMPLETE_LINE_DRAIN_BUDGET_HH__

#include <algorithm>
#include <cstdint>

namespace gem5::maa
{

/**
 * Fixed issue-width state for complete virtual-combiner line retirement.
 *
 * The caller supplies the MAA cycle identity.  Availability probes do not
 * consume a token, so a failed downstream write admission can leave the
 * budget unchanged.  Only a successfully created full-line write is recorded.
 */
class CompleteLineDrainBudget
{
  public:
    struct Counters
    {
        uint64_t issuedLines = 0;
        uint64_t stallCycles = 0;
        uint32_t peakLinesPerCycle = 0;
    };

    static constexpr bool
    validIssueWidth(uint32_t width)
    {
        return width == 0 || width == 1 || width == 2 || width == 4 ||
               width == 8;
    }

    bool
    configure(uint32_t width)
    {
        if (!validIssueWidth(width))
            return false;
        issueLimit = static_cast<uint8_t>(width);
        reset();
        return true;
    }

    void
    reset()
    {
        cycleValid = false;
        activeCycle = 0;
        issuedThisCycle = 0;
        stallRecorded = false;
        drainCounters = {};
    }

    bool
    available(uint64_t cycle)
    {
        beginCycle(cycle);
        if (issueLimit == 0 || issuedThisCycle < issueLimit)
            return true;
        if (!stallRecorded) {
            ++drainCounters.stallCycles;
            stallRecorded = true;
        }
        return false;
    }

    bool
    recordIssue(uint64_t cycle)
    {
        beginCycle(cycle);
        if (issueLimit != 0 && issuedThisCycle >= issueLimit)
            return false;
        ++issuedThisCycle;
        ++drainCounters.issuedLines;
        drainCounters.peakLinesPerCycle = std::max(
            drainCounters.peakLinesPerCycle, issuedThisCycle);
        return true;
    }

    uint32_t limit() const { return issueLimit; }
    uint32_t issuedInCycle() const { return issuedThisCycle; }
    const Counters &counters() const { return drainCounters; }

  private:
    void
    beginCycle(uint64_t cycle)
    {
        if (cycleValid && activeCycle == cycle)
            return;
        cycleValid = true;
        activeCycle = cycle;
        issuedThisCycle = 0;
        stallRecorded = false;
    }

    uint64_t activeCycle = 0;
    uint8_t issueLimit = 0;
    uint32_t issuedThisCycle = 0;
    bool cycleValid = false;
    bool stallRecorded = false;
    Counters drainCounters{};
};

} // namespace gem5::maa

#endif // __MEM_MAA_COMPLETE_LINE_DRAIN_BUDGET_HH__
