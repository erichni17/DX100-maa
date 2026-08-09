#include "mem/MAA/BoundedRangePass.hh"

#include <cassert>
#include <cstdint>
#include <iostream>
#include <vector>

using gem5::BoundedRangePassTracker;

namespace
{

using Result = BoundedRangePassTracker::Result;

void
testRangesCoverGrowSpaceExactly()
{
    BoundedRangePassTracker tracker;
    assert(tracker.configure(16384, 4096, 4, 65539) == Result::Accepted);
    uint64_t cursor = 0;
    for (uint32_t pass = 0; pass < tracker.passes(); ++pass) {
        const auto range = tracker.range(pass);
        assert(range.lower == cursor);
        assert(range.lower < range.upper);
        for (uint64_t grow = range.lower; grow < range.upper; ++grow)
            assert(tracker.passForGrow(grow) == pass);
        cursor = range.upper;
    }
    assert(cursor == tracker.grows());
    assert(tracker.passForGrow(tracker.grows()) ==
           BoundedRangePassTracker::MaxPasses);
}

void
testExactOnceOutOfOrderRetirement()
{
    constexpr uint32_t logical = 16384;
    constexpr uint64_t grows = 65536;
    BoundedRangePassTracker tracker;
    assert(tracker.configure(logical, 4096, 4, grows) == Result::Accepted);

    std::array<std::vector<uint32_t>, 4> selected;
    for (uint32_t itr = 0; itr < logical; ++itr) {
        const uint64_t grow = (static_cast<uint64_t>(itr) * 997 + 13) % grows;
        selected[tracker.passForGrow(grow)].push_back(itr);
    }
    for (uint32_t pass = 0; pass < tracker.passes(); ++pass) {
        for (uint32_t itr = 0; itr < logical; ++itr)
            assert(tracker.recordInspection(itr, pass) == Result::Accepted);
        for (size_t begin = 0; begin < selected[pass].size();
             begin += tracker.active()) {
            const size_t end = std::min(
                begin + tracker.active(), selected[pass].size());
            for (size_t i = begin; i < end; ++i) {
                const uint32_t itr = selected[pass][i];
                const uint64_t grow =
                    (static_cast<uint64_t>(itr) * 997 + 13) % grows;
                assert(tracker.recordAdmission(itr, grow, pass) ==
                       Result::Accepted);
            }
            for (size_t i = end; i > begin; --i) {
                assert(tracker.recordRetirement(
                           selected[pass][i - 1], pass) == Result::Accepted);
            }
            if (end != selected[pass].size())
                assert(tracker.recordDrain(pass) == Result::Accepted);
        }
        assert(tracker.finishPass(pass) == Result::Accepted);
    }
    assert(tracker.admissions() == logical);
    assert(tracker.retirements() == logical);
    assert(tracker.finish() == Result::Accepted);
    assert(tracker.chargedBytes() == 2919);
}

void
testSemanticByteAccountingIsFieldComplete()
{
    BoundedRangePassTracker tracker;
    assert(tracker.configure(16384, 4096, 4, 65536) == Result::Accepted);
    const auto bytes = tracker.semanticByteBreakdown();

    // Exhaustive logical-identity checking is trace-side, not live state.
    assert(bytes.identityBitmaps == 0);
    // Seven 64-entry uint32 arrays: totals, expected/actual scan cursors, and
    // drain epochs.
    assert(bytes.passCounters == 1792);
    // passFinished: one semantic byte for each of 64 pass states.
    assert(bytes.passFinished == 64);
    // passRanges: 64 {uint64_t lower, uint64_t upper} records.
    assert(bytes.passRanges == 1024);
    // Two mode flags; logical/active/pass counts; grow bounds; global counts.
    assert(bytes.scalarConfig == 39);
    assert(bytes.total() == 2919);
    assert(tracker.chargedBytes() == bytes.total());
}

void
testPassGroupedInspectionContract()
{
    BoundedRangePassTracker tracker;
    const std::array<uint32_t, 4> populations{3, 1, 2, 2};
    assert(tracker.configureSelectedPopulations(
               8, 4, populations.size(),
               [&](uint32_t pass) { return populations[pass]; }) ==
           Result::Accepted);
    const std::array<std::array<uint32_t, 3>, 4> selected{{
        {{7, 2, 5}}, {{0, 0, 0}}, {{1, 6, 0}}, {{3, 4, 0}}}};
    for (uint32_t pass = 0; pass < populations.size(); ++pass) {
        for (uint32_t index = 0; index < populations[pass]; ++index) {
            const uint32_t itr = selected[pass][index];
            assert(tracker.recordSelectedInspection(itr, pass) ==
                   Result::Accepted);
            assert(tracker.recordSelectedAdmission(itr, pass) ==
                   Result::Accepted);
            assert(tracker.recordRetirement(itr, pass) == Result::Accepted);
        }
        assert(tracker.finishPass(pass) == Result::Accepted);
    }
    assert(tracker.finish() == Result::Accepted);

    BoundedRangePassTracker invalid;
    assert(invalid.configureSelectedPopulations(
               8, 4, 4, [](uint32_t) { return 1; }) ==
           Result::InvalidConfiguration);
}

void
testSkewRemainsExactAndExplicit()
{
    BoundedRangePassTracker tracker;
    assert(tracker.configure(16384, 4096, 4, 65536) == Result::Accepted);
    const uint64_t grow = 50000;
    const uint32_t selected_pass = tracker.passForGrow(grow);
    for (uint32_t pass = 0; pass < tracker.passes(); ++pass) {
        for (uint32_t itr = 0; itr < tracker.logical(); ++itr)
            assert(tracker.recordInspection(itr, pass) == Result::Accepted);
        if (pass == selected_pass) {
            for (uint32_t itr = 0; itr < tracker.logical(); ++itr) {
                assert(tracker.recordAdmission(itr, grow, pass) ==
                       Result::Accepted);
                assert(tracker.recordRetirement(itr, pass) ==
                       Result::Accepted);
                if ((itr + 1) % tracker.active() == 0 &&
                    itr + 1 != tracker.logical())
                    assert(tracker.recordDrain(pass) == Result::Accepted);
            }
        }
        assert(tracker.finishPass(pass) == Result::Accepted);
    }
    assert(tracker.admissionsForPass(selected_pass) == tracker.logical());
    assert(tracker.finish() == Result::Accepted);
}

void
testSourceRelativeRange()
{
    BoundedRangePassTracker tracker;
    assert(tracker.configureRange(16384, 4096, 4, 13, 22) ==
           Result::Accepted);
    assert(tracker.lowerGrow() == 13);
    assert(tracker.upperGrow() == 22);
    assert(tracker.range(0).lower == 13);
    assert(tracker.range(3).upper == 22);
    assert(tracker.passForGrow(12) == BoundedRangePassTracker::MaxPasses);
    assert(tracker.passForGrow(22) == BoundedRangePassTracker::MaxPasses);
    for (uint64_t grow = 13; grow < 22; ++grow) {
        const uint32_t pass = tracker.passForGrow(grow);
        const auto range = tracker.range(pass);
        assert(range.lower <= grow && grow < range.upper);
    }
}

void
testExplicitBalancedRanges()
{
    BoundedRangePassTracker tracker;
    const std::vector<BoundedRangePassTracker::Range> ranges{
        {13, 15}, {15, 17}, {17, 19}, {19, 22}};
    assert(tracker.configureRanges(16384, 4096, ranges) ==
           Result::Accepted);
    for (uint32_t pass = 0; pass < ranges.size(); ++pass) {
        assert(tracker.range(pass).lower == ranges[pass].lower);
        assert(tracker.range(pass).upper == ranges[pass].upper);
        for (uint64_t grow = ranges[pass].lower;
             grow < ranges[pass].upper; ++grow) {
            assert(tracker.passForGrow(grow) == pass);
        }
    }
    auto broken = ranges;
    broken[2].lower++;
    assert(tracker.configureRanges(16384, 4096, broken) ==
           Result::InvalidConfiguration);
}

void
testFailuresAreClosed()
{
    BoundedRangePassTracker tracker;
    assert(tracker.configure(16384, 4097, 4, 65536) ==
           Result::InvalidConfiguration);
    assert(tracker.configure(16384, 4096, 3, 65536) ==
           Result::InvalidConfiguration);
    assert(tracker.configure(16384, 4096, 4, 65536) == Result::Accepted);
    assert(tracker.recordInspection(1, 0) ==
           Result::InspectionOutOfOrder);
    assert(tracker.recordInspection(0, 0) == Result::Accepted);
    const uint64_t grow = 7;
    assert(tracker.recordAdmission(0, grow, 1) == Result::WrongPass);
    assert(tracker.recordRetirement(0, 0) ==
           Result::RetirementBeforeAdmission);
    assert(tracker.recordAdmission(0, grow, 0) == Result::Accepted);
    assert(tracker.recordRetirement(0, 0) == Result::Accepted);
    assert(tracker.recordRetirement(0, 0) ==
           Result::RetirementBeforeAdmission);
    assert(tracker.recordDrain(0) == Result::Accepted);
    assert(tracker.recordInspection(0, 0) ==
           Result::InspectionOutOfOrder);
    assert(tracker.finishPass(0) == Result::PassIncomplete);
    assert(tracker.finish() == Result::Incomplete);
}

void
testEpochOverflowRequiresExplicitDrain()
{
    BoundedRangePassTracker tracker;
    assert(tracker.configure(8, 4, 2, 2) == Result::Accepted);
    for (uint32_t itr = 0; itr < 8; ++itr)
        assert(tracker.recordInspection(itr, 0) == Result::Accepted);
    for (uint32_t itr = 0; itr < 4; ++itr)
        assert(tracker.recordAdmission(itr, 0, 0) == Result::Accepted);
    assert(tracker.recordAdmission(4, 0, 0) == Result::EpochOverflow);
    assert(tracker.recordDrain(0) == Result::DrainIncomplete);
    for (uint32_t itr = 0; itr < 4; ++itr)
        assert(tracker.recordRetirement(itr, 0) == Result::Accepted);
    assert(tracker.recordDrain(0) == Result::Accepted);
    assert(tracker.recordAdmission(4, 0, 0) == Result::Accepted);
}

} // anonymous namespace

int
main()
{
    testRangesCoverGrowSpaceExactly();
    testExactOnceOutOfOrderRetirement();
    testSemanticByteAccountingIsFieldComplete();
    testPassGroupedInspectionContract();
    testSkewRemainsExactAndExplicit();
    testSourceRelativeRange();
    testExplicitBalancedRanges();
    testFailuresAreClosed();
    testEpochOverflowRequiresExplicitDrain();
    std::cout << "bounded_range_pass_test: PASS\n";
    return 0;
}
