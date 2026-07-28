#ifndef __MEM_LANLMAA_BRANSON_EVENT_TIMING_HH__
#define __MEM_LANLMAA_BRANSON_EVENT_TIMING_HH__

#include <algorithm>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <optional>
#include <vector>

namespace gem5
{
namespace lanlmaa
{

struct BransonEventIssue
{
    size_t unit = 0;
    uint64_t completionCycle = 0;
};

class BransonEventTiming
{
  public:
    BransonEventTiming(
        uint64_t latencyCycles, uint64_t initiationIntervalCycles,
        size_t units)
        : latencyCycles(latencyCycles),
          initiationIntervalCycles(initiationIntervalCycles),
          nextIssueCycles(units, 0)
    {
        assert(latencyCycles > 0);
        assert(initiationIntervalCycles > 0);
        assert(units > 0);
    }

    size_t units() const { return nextIssueCycles.size(); }

    void
    reset(uint64_t cycle = 0)
    {
        std::fill(nextIssueCycles.begin(), nextIssueCycles.end(), cycle);
    }

    std::optional<BransonEventIssue>
    issue(uint64_t cycle)
    {
        for (size_t unit = 0; unit < nextIssueCycles.size(); ++unit) {
            if (nextIssueCycles[unit] > cycle) {
                continue;
            }
            assert(cycle <= std::numeric_limits<uint64_t>::max() -
                                initiationIntervalCycles);
            assert(cycle <=
                   std::numeric_limits<uint64_t>::max() - latencyCycles);
            nextIssueCycles[unit] = cycle + initiationIntervalCycles;
            return BransonEventIssue{unit, cycle + latencyCycles};
        }
        return std::nullopt;
    }

    uint64_t
    nextIssueCycle(size_t unit) const
    {
        assert(unit < nextIssueCycles.size());
        return nextIssueCycles[unit];
    }

  private:
    const uint64_t latencyCycles;
    const uint64_t initiationIntervalCycles;
    std::vector<uint64_t> nextIssueCycles;
};

class BransonContextScheduler
{
  public:
    BransonContextScheduler(size_t contexts, size_t quantum)
        : contextCount(contexts), quantum(quantum)
    {
        assert(contexts > 0);
        assert(quantum > 0);
    }

    void
    reset()
    {
        preferred = 0;
        preferredIssues = 0;
    }

    std::optional<size_t>
    select(const std::vector<bool> &ready, const std::vector<bool> &active)
    {
        assert(ready.size() == contextCount);
        assert(active.size() == contextCount);
        if (!active[preferred]) {
            bool found = false;
            for (size_t offset = 1; offset < contextCount; ++offset) {
                const size_t candidate =
                    (preferred + offset) % contextCount;
                if (active[candidate]) {
                    preferred = candidate;
                    preferredIssues = 0;
                    found = true;
                    break;
                }
            }
            if (!found) {
                return std::nullopt;
            }
        }
        if (ready[preferred]) {
            return preferred;
        }
        for (size_t offset = 1; offset < contextCount; ++offset) {
            const size_t candidate = (preferred + offset) % contextCount;
            if (active[candidate] && ready[candidate]) {
                return candidate;
            }
        }
        return std::nullopt;
    }

    void
    issued(size_t context)
    {
        assert(context < contextCount);
        if (context != preferred) {
            return;
        }
        ++preferredIssues;
        if (preferredIssues == quantum) {
            preferred = (preferred + 1) % contextCount;
            preferredIssues = 0;
        }
    }

    size_t preferredContext() const { return preferred; }
    size_t issuesInQuantum() const { return preferredIssues; }

  private:
    const size_t contextCount;
    const size_t quantum;
    size_t preferred = 0;
    size_t preferredIssues = 0;
};

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_BRANSON_EVENT_TIMING_HH__
