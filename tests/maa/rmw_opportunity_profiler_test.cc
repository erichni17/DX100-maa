#include <cassert>

#include "mem/MAA/RMWOpportunityProfiler.hh"

using gem5::RMWOpportunityProfiler;

int
main()
{
    RMWOpportunityProfiler disabled;
    disabled.observe(0x1000, true);
    assert(disabled.summary().totalUpdates == 0);

    RMWOpportunityProfiler profiler(true);
    profiler.observe(0x1000, true);
    profiler.observe(0x1004, true);
    profiler.observe(0x1000, true);
    profiler.observe(0x1080, false);
    profiler.observeBaselineRequest();
    profiler.observeBaselineRequest();
    auto summary = profiler.summary();
    assert(summary.totalUpdates == 4);
    assert(summary.eligibleUpdates == 3);
    assert(summary.baselineRequests == 2);
    assert(summary.uniqueWords == 2);
    assert(summary.uniqueLines == 1);
    for (const auto &cache : summary.caches) {
        assert(cache.hits == 2);
        assert(cache.misses == 1);
        assert(cache.evictions == 0);
    }
    assert(summary.caches[0].capacityLines == 48);
    assert(summary.caches[1].capacityLines == 100);
    assert(summary.caches[2].capacityLines == 204);
    assert(summary.caches[3].capacityLines == 408);

    profiler.reset();
    assert(profiler.summary().baselineRequests == 0);
    // The 4-KiB model has 12 sets. These five lines collide in one
    // four-way set.
    for (uint64_t line = 0; line < 5; ++line)
        profiler.observe((line * 12) << 6, true);
    summary = profiler.summary();
    assert(summary.caches[0].misses == 5);
    assert(summary.caches[0].evictions == 1);
    assert(summary.caches[1].evictions == 0);

    profiler.setEnabled(false);
    profiler.observe(0x2000, true);
    assert(profiler.summary().totalUpdates == 0);
    return 0;
}
