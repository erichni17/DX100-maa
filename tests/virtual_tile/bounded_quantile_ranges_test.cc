#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <utility>
#include <vector>

#include "mem/MAA/BoundedQuantileRanges.hh"

using gem5::BoundedQuantileRanges;
using gem5::BoundedGrowPassPlan;

namespace
{

void
appendPopulation(std::vector<std::pair<uint32_t, uint32_t>> &records,
                 uint32_t first, uint32_t unique, uint32_t population)
{
    const uint32_t base = population / unique;
    const uint32_t extra = population % unique;
    for (uint32_t i = 0; i < unique; ++i)
        records.emplace_back(first + i, base + (i < extra));
}

void
testFrozenXrageSourceLineDiagnostic()
{
    std::vector<std::pair<uint32_t, uint32_t>> records;
    appendPopulation(records, 36930 - 522, 522, 4096);
    appendPopulation(records, 37514 - 526, 526, 4096);
    appendPopulation(records, 38101 - 524, 524, 4096);
    appendPopulation(records, 38101, 597, 4096);
    assert(records.size() == 2169);

    auto visit = [&records](auto consumer) {
        for (const auto &[key, count] : records)
            consumer(key, count);
    };
    BoundedQuantileRanges quantiles;
    assert(quantiles.configure(16384, 4096, 4, visit) ==
           BoundedQuantileRanges::Result::Accepted);
    assert(quantiles.records() == 2169);
    assert(quantiles.range(0).upper == 36930);
    assert(quantiles.range(1).upper == 37514);
    assert(quantiles.range(2).upper == 38101);
    for (uint32_t pass = 0; pass < 4; ++pass)
        assert(quantiles.population(pass) == 4096);
}

void
testHotLineRequiresFallback()
{
    const std::vector<std::pair<uint32_t, uint32_t>> records{
        {7, 4097}, {8, 4095}, {9, 4096}, {10, 4096}};
    auto visit = [&records](auto consumer) {
        for (const auto &[key, count] : records)
            consumer(key, count);
    };
    BoundedQuantileRanges quantiles;
    assert(quantiles.configure(16384, 4096, 4, visit) ==
           BoundedQuantileRanges::Result::BucketOverflow);
}

void
testAuthenticatedPhysicalGrowPackingUsesFivePasses()
{
    const std::vector<std::pair<uint32_t, uint32_t>> records{
        {13, 1785}, {14, 2058}, {15, 2026},
        {16, 2028}, {17, 2026}, {18, 2027},
        {19, 2028}, {20, 2026}, {21, 380}};
    auto visit = [&records](auto consumer) {
        for (const auto &[key, count] : records)
            consumer(key, count);
    };
    BoundedQuantileRanges packed;
    assert(packed.configurePacked(16384, 4096, 64, visit) ==
           BoundedQuantileRanges::Result::Accepted);
    assert(packed.records() == 9);
    assert(packed.passes() == 5);
    const std::array<uint32_t, 5> expected{
        3843, 4054, 4053, 4054, 380};
    for (uint32_t pass = 0; pass < expected.size(); ++pass) {
        assert(packed.population(pass) == expected[pass]);
        assert(packed.population(pass) <= 4096);
    }
}

void
testAuthenticatedPhysicalGrowPlanSplitsOnlyGrow21()
{
    const std::vector<std::pair<uint32_t, uint32_t>> records{
        {13, 1785}, {14, 2058}, {15, 2026},
        {16, 2028}, {17, 2026}, {18, 2027},
        {19, 2028}, {20, 2026}, {21, 380}};
    auto visit = [&records](auto consumer) {
        for (const auto &[key, count] : records)
            consumer(key, count);
    };
    BoundedGrowPassPlan plan;
    assert(plan.configure(16384, 4096, 64, visit) ==
           BoundedGrowPassPlan::Result::Accepted);
    assert(plan.passes() == 4);
    assert(plan.records() == 9);
    assert(plan.splitKey() == 21);
    assert(plan.splitPopulation() == 380);
    const std::array<uint32_t, 4> expectedQuotas{10, 41, 44, 285};
    uint32_t ordinal = 0;
    for (uint32_t pass = 0; pass < 4; ++pass) {
        assert(plan.population(pass) == 4096);
        assert(plan.splitQuota(pass) == expectedQuotas[pass]);
        for (uint32_t i = 0; i < expectedQuotas[pass]; ++i)
            assert(plan.passFor(21, ordinal++) == pass);
    }
    assert(ordinal == 380);
    assert(plan.passFor(21, ordinal) == BoundedGrowPassPlan::MaxPasses);
    assert(plan.chargedBytes() == 1121);
    assert(BoundedGrowPassPlan::modeledReductionVisits(
               4096, plan.operations()) == 4096 + plan.operations());
}

void
testSplitOrdinalCommitsOnlyAfterForcedRetry()
{
    const std::vector<std::pair<uint32_t, uint32_t>> records{
        {13, 1785}, {14, 2058}, {15, 2026},
        {16, 2028}, {17, 2026}, {18, 2027},
        {19, 2028}, {20, 2026}, {21, 380}};
    auto visit = [&records](auto consumer) {
        for (const auto &[key, count] : records)
            consumer(key, count);
    };
    BoundedGrowPassPlan plan;
    assert(plan.configure(16384, 4096, 64, visit) ==
           BoundedGrowPassPlan::Result::Accepted);

    const std::array<uint32_t, 4> expectedQuotas{10, 41, 44, 285};
    for (uint32_t pass = 0; pass < plan.passes(); ++pass) {
        uint32_t committed = 0;
        uint32_t selected = 0;
        bool forced_retry = false;
        while (committed < plan.splitPopulation()) {
            const uint32_t observed = committed;
            const uint32_t assigned = plan.passFor(21, observed);
            if (assigned == pass && !forced_retry) {
                // Model an admission failure/drain. No commit occurs, so the
                // retried descriptor must retain its ordinal and assignment.
                forced_retry = true;
                assert(committed == observed);
                assert(plan.passFor(21, committed) == assigned);
                continue;
            }
            selected += assigned == pass;
            assert(plan.commitSplitOrdinal(observed, committed) ==
                   BoundedGrowPassPlan::Result::Accepted);
        }
        assert(forced_retry);
        assert(selected == expectedQuotas[pass]);
        const uint32_t stale = committed - 1;
        assert(plan.commitSplitOrdinal(stale, committed) ==
               BoundedGrowPassPlan::Result::StaleReplayOrdinal);
        assert(committed == plan.splitPopulation());
        assert(plan.commitSplitOrdinal(committed, committed) ==
               BoundedGrowPassPlan::Result::ReplayOrdinalOverflow);
    }
}

} // anonymous namespace

int
main()
{
    testFrozenXrageSourceLineDiagnostic();
    testHotLineRequiresFallback();
    testAuthenticatedPhysicalGrowPackingUsesFivePasses();
    testAuthenticatedPhysicalGrowPlanSplitsOnlyGrow21();
    testSplitOrdinalCommitsOnlyAfterForcedRetry();
    std::cout << "bounded_quantile_ranges_test: PASS\n";
    return 0;
}
