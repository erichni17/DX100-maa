#ifndef __MEM_LANLMAA_FACE_COMPUTE_TIMING_HH__
#define __MEM_LANLMAA_FACE_COMPUTE_TIMING_HH__

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

struct FaceComputeIssue
{
    size_t unit = 0;
    uint64_t completionCycle = 0;
};

class FaceComputeTiming
{
  public:
    FaceComputeTiming(
        uint64_t latencyCycles, uint64_t initiationIntervalCycles,
        size_t units)
        : latencyCycles(latencyCycles),
          initiationIntervalCycles(initiationIntervalCycles),
          nextIssueCycles(units, 0)
    {
        assert(initiationIntervalCycles > 0);
        assert(units > 0);
    }

    bool enabled() const { return latencyCycles != 0; }
    size_t units() const { return nextIssueCycles.size(); }

    void
    reset(uint64_t cycle = 0)
    {
        std::fill(nextIssueCycles.begin(), nextIssueCycles.end(), cycle);
    }

    std::optional<FaceComputeIssue>
    issue(uint64_t cycle)
    {
        assert(enabled());
        for (size_t unit = 0; unit < nextIssueCycles.size(); ++unit) {
            if (nextIssueCycles[unit] > cycle) {
                continue;
            }
            assert(cycle <= std::numeric_limits<uint64_t>::max() -
                                initiationIntervalCycles);
            assert(cycle <=
                   std::numeric_limits<uint64_t>::max() - latencyCycles);
            nextIssueCycles[unit] = cycle + initiationIntervalCycles;
            return FaceComputeIssue{unit, cycle + latencyCycles};
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

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_FACE_COMPUTE_TIMING_HH__
