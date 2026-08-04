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
        for (uint32_t itr : selected[pass]) {
            const uint64_t grow =
                (static_cast<uint64_t>(itr) * 997 + 13) % grows;
            assert(tracker.recordAdmission(itr, grow, pass) ==
                   Result::Accepted);
        }
        for (auto itr = selected[pass].rbegin();
             itr != selected[pass].rend(); ++itr) {
            assert(tracker.recordRetirement(*itr, pass) == Result::Accepted);
        }
        assert(tracker.finishPass(pass) == Result::Accepted);
    }
    assert(tracker.admissions() == logical);
    assert(tracker.retirements() == logical);
    assert(tracker.finish() == Result::Accepted);
    assert(tracker.chargedBytes() == 4672);
}

void
testSkewRemainsExactAndExplicit()
{
    BoundedRangePassTracker tracker;
    assert(tracker.configure(16384, 4096, 4, 65536) == Result::Accepted);
    const uint64_t grow = 50000;
    const uint32_t selected_pass = tracker.passForGrow(grow);
    for (uint32_t pass = 0; pass < tracker.passes(); ++pass) {
        if (pass == selected_pass) {
            for (uint32_t itr = 0; itr < tracker.logical(); ++itr) {
                assert(tracker.recordAdmission(itr, grow, pass) ==
                       Result::Accepted);
                assert(tracker.recordRetirement(itr, pass) ==
                       Result::Accepted);
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
testFailuresAreClosed()
{
    BoundedRangePassTracker tracker;
    assert(tracker.configure(16384, 4097, 4, 65536) ==
           Result::InvalidConfiguration);
    assert(tracker.configure(16384, 4096, 3, 65536) ==
           Result::InvalidConfiguration);
    assert(tracker.configure(16384, 4096, 4, 65536) == Result::Accepted);
    const uint64_t grow = 7;
    assert(tracker.recordAdmission(0, grow, 1) == Result::WrongPass);
    assert(tracker.recordRetirement(0, 0) ==
           Result::RetirementBeforeAdmission);
    assert(tracker.recordAdmission(0, grow, 0) == Result::Accepted);
    assert(tracker.recordAdmission(0, grow, 0) ==
           Result::DuplicateAdmission);
    assert(tracker.recordRetirement(0, 0) == Result::Accepted);
    assert(tracker.recordRetirement(0, 0) ==
           Result::DuplicateRetirement);
    assert(tracker.finishPass(0) == Result::Accepted);
    assert(tracker.finish() == Result::Incomplete);
}

} // anonymous namespace

int
main()
{
    testRangesCoverGrowSpaceExactly();
    testExactOnceOutOfOrderRetirement();
    testSkewRemainsExactAndExplicit();
    testSourceRelativeRange();
    testFailuresAreClosed();
    std::cout << "bounded_range_pass_test: PASS\n";
    return 0;
}
