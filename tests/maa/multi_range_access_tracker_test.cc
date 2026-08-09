#include <cassert>
#include <iostream>

#include "mem/MAA/MultiRangeAccessTracker.hh"

using gem5::maa::MultiRangeAccessTracker;

int
main()
{
    using Access = MultiRangeAccessTracker::Access;
    using Mode = MultiRangeAccessTracker::Mode;

    MultiRangeAccessTracker tracker;
    int fused_maa0 = 0;
    int c_reader_maa1 = 0;
    int c_writer_maa1 = 0;
    int b_writer_maa1 = 0;
    int a_reader_maa1 = 0;
    int disjoint_writer_maa1 = 0;

    // Fused operation: READ(A=1), READ(B=2), WRITE(C=3).
    assert(tracker.tryAcquire(
        &fused_maa0, 0,
        {{1, Mode::Read}, {2, Mode::Read}, {3, Mode::Write}}));

    // Cross-MAA C read/write and write/write overlap must both be blocked.
    assert(!tracker.tryAcquire(&c_reader_maa1, 1, {{3, Mode::Read}}));
    assert(!tracker.tryAcquire(&c_writer_maa1, 1, {{3, Mode::Write}}));

    // The API-visible B input retains ordering through fused retirement.
    assert(!tracker.tryAcquire(&b_writer_maa1, 1, {{2, Mode::Write}}));

    // Read/read sharing and a wholly disjoint write remain concurrent.
    assert(tracker.tryAcquire(&a_reader_maa1, 1, {{1, Mode::Read}}));
    assert(tracker.tryAcquire(
        &disjoint_writer_maa1, 1, {{4, Mode::Write}}));

    assert(tracker.release(&a_reader_maa1));
    assert(tracker.release(&disjoint_writer_maa1));
    assert(tracker.release(&fused_maa0));

    // Once fused retirement releases the compound lease, C is writable.
    assert(tracker.tryAcquire(&c_writer_maa1, 1, {{3, Mode::Write}}));
    assert(tracker.release(&c_writer_maa1));
    assert(tracker.empty());

    // Self-aliasing B/C is normalized to one exclusive region lease.
    const auto normalized = MultiRangeAccessTracker::normalize(
        {Access{7, Mode::Read}, Access{7, Mode::Write}});
    assert(normalized.size() == 1);
    assert(normalized.front().region == 7);
    assert(normalized.front().mode == Mode::Write);

    std::cout << "MULTI_RANGE_ACCESS_TRACKER_PASS" << std::endl;
    return 0;
}
