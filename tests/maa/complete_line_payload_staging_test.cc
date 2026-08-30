#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <iostream>

#include "mem/MAA/CompleteLinePayloadStaging.hh"

namespace
{

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << '\n';             \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

using Staging = gem5::maa::CompleteLinePayloadStaging;
using Identity = Staging::Identity;
using Result = Staging::Result;

void
testConfigurationAndDisabledNeutrality()
{
    for (uint32_t width : {0U, 1U, 2U, 4U, 8U}) {
        Staging staging;
        CHECK(Staging::validWidth(width));
        CHECK(staging.configure(width, 16) == Result::Accepted);
        CHECK(staging.width() == width);
        CHECK(staging.capacity() == (width == 0 ? 0 : 16));
    }
    for (uint32_t width : {3U, 5U, 7U, 9U, 16U}) {
        Staging staging;
        CHECK(!Staging::validWidth(width));
        CHECK(staging.configure(width, 16) ==
              Result::InvalidConfiguration);
    }

    Staging disabled;
    CHECK(disabled.configure(0, 4) == Result::Accepted);
    CHECK(disabled.stage(100, {0, 1}, 16) == Result::Disabled);
    CHECK(disabled.retire(100, {0, 1}) == Result::Disabled);
    CHECK(disabled.empty());
    CHECK(disabled.counters().issues == 0);
    CHECK(disabled.counters().completions == 0);
    CHECK(disabled.counters().waitCycles == 0);
    CHECK(disabled.counters().peakOccupancy == 0);
}

void
testExactUncontendedLatency()
{
    for (uint32_t words : {8U, 16U}) {
        for (uint32_t width : {1U, 2U, 4U, 8U}) {
            Staging staging;
            const Identity identity{0, 7};
            CHECK(staging.configure(width, 2) == Result::Accepted);
            const uint64_t cycles = (words + width - 1) / width;
            for (uint64_t cycle = 0; cycle < cycles; ++cycle) {
                CHECK(staging.stage(cycle, identity, words) ==
                      Result::Waiting);
                CHECK(staging.readsInCycle() <= width);
                CHECK(staging.startsInCycle() <= width);
                CHECK(staging.completedWords(identity) ==
                      std::min<uint64_t>((cycle + 1) * width, words));
            }
            CHECK(staging.readyCycle(identity) == cycles);
            CHECK(staging.stage(cycles - 1, identity, words) ==
                  Result::Waiting);
            CHECK(staging.stage(cycles, identity, words) == Result::Ready);
            CHECK(staging.retire(cycles, identity) == Result::Accepted);
            CHECK(staging.finish() == Result::Accepted);
            CHECK(staging.counters().issues == 1);
            CHECK(staging.counters().completions == 1);
            CHECK(staging.counters().waitCycles == cycles);
            CHECK(staging.counters().peakOccupancy == 1);
        }
    }
}

void
testGlobalReadAndStartBounds()
{
    Staging staging;
    CHECK(staging.configure(2, 4) == Result::Accepted);
    const Identity first{0, 1};
    const Identity second{1, 1};
    const Identity third{2, 1};

    CHECK(staging.stage(10, first, 1) == Result::Waiting);
    CHECK(staging.stage(10, second, 1) == Result::Waiting);
    CHECK(staging.stage(10, third, 1) == Result::Busy);
    CHECK(staging.readsInCycle() == 2);
    CHECK(staging.startsInCycle() == 2);
    CHECK(staging.occupancy() == 2);
    CHECK(staging.counters().peakOccupancy == 2);
    CHECK(staging.stage(11, first, 1) == Result::Ready);
    CHECK(staging.stage(11, second, 1) == Result::Ready);
    CHECK(staging.retire(11, first) == Result::Accepted);
    CHECK(staging.retire(11, second) == Result::Accepted);
    CHECK(staging.stage(11, third, 1) == Result::Waiting);
    CHECK(staging.startsInCycle() == 1);
    CHECK(staging.readsInCycle() == 1);
    CHECK(staging.stage(12, third, 1) == Result::Ready);
    CHECK(staging.retire(12, third) == Result::Accepted);
    CHECK(staging.finish() == Result::Accepted);
}

void
testExactIdentityAndRetryRetention()
{
    Staging staging;
    const Identity current{1, 9};
    CHECK(staging.configure(4, 2) == Result::Accepted);
    CHECK(staging.stage(20, current, 8) == Result::Waiting);
    CHECK(staging.stage(20, {1, 10}, 8) == Result::StaleIdentity);
    CHECK(staging.stage(20, current, 16) == Result::MismatchedIdentity);
    CHECK(staging.retire(20, current) == Result::NotReady);
    CHECK(staging.retire(20, {1, 10}) == Result::StaleIdentity);
    CHECK(staging.stage(21, current, 8) == Result::Waiting);
    CHECK(staging.readyCycle(current) == 22);

    // A downstream issue denial leaves the exact ready entry intact.  Any
    // number of retries can observe it without consuming more reads.
    CHECK(staging.stage(22, current, 8) == Result::Ready);
    CHECK(staging.stage(22, current, 8) == Result::Ready);
    CHECK(staging.readsInCycle() == 0);
    CHECK(staging.occupancy() == 1);
    CHECK(staging.retire(22, current) == Result::Accepted);
    CHECK(staging.retire(22, current) == Result::NotStaged);
    CHECK(staging.stage(21, {0, 1}, 8) == Result::NonMonotonicCycle);
    CHECK(staging.finish() == Result::Accepted);
}

void
testResetPreservesConfiguration()
{
    Staging staging;
    CHECK(staging.configure(8, 3) == Result::Accepted);
    CHECK(staging.stage(1, {2, 1}, 16) == Result::Waiting);
    CHECK(staging.finish() == Result::Imbalance);
    staging.reset();
    CHECK(staging.width() == 8);
    CHECK(staging.capacity() == 3);
    CHECK(staging.empty());
    CHECK(staging.counters().issues == 0);
    CHECK(staging.stage(1, {2, 2}, 8) == Result::Waiting);
    CHECK(staging.stage(2, {2, 2}, 8) == Result::Ready);
    CHECK(staging.retire(2, {2, 2}) == Result::Accepted);
}

} // anonymous namespace

int
main()
{
    testConfigurationAndDisabledNeutrality();
    testExactUncontendedLatency();
    testGlobalReadAndStartBounds();
    testExactIdentityAndRetryRetention();
    testResetPreservesConfiguration();
    std::cout << "complete-line payload staging tests passed\n";
    return 0;
}
