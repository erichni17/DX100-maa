#include <cassert>
#include <iostream>

#include "mem/MAA/ReorderSurvivalTracker.hh"

using gem5::ReorderSurvivalTracker;

namespace
{

void
testOne16KVisibilityDomain()
{
    ReorderSurvivalTracker tracker;
    tracker.begin(7);
    for (int i = 0; i < 16384; ++i) {
        assert(tracker.select(i));
        assert(tracker.admit());
    }
    // Issues establish line/row order; descriptor entries are credited once,
    // when the response chain is actually consumed.
    assert(tracker.issueLine(11));
    assert(tracker.issueEntries(8192));
    assert(tracker.issueLine(11));
    assert(tracker.issueEntries(8192));
    ReorderSurvivalTracker::Epoch epoch;
    assert(tracker.closeEpoch(true, epoch));
    assert(epoch.id == 0);
    assert(epoch.admissions == 16384);
    assert(epoch.rowTransitions == 0);
    assert(tracker.reconciled());
    assert(tracker.totalSelectedDescriptors == 16384);
    assert(tracker.preserves16K());
}

void
testIssueLineThenResponseEntriesCreditedExactlyOnce()
{
    ReorderSurvivalTracker tracker;
    tracker.begin(8);
    assert(tracker.select(0));
    assert(tracker.admit());
    assert(tracker.issueLine(11));
    assert(tracker.totalIssuedEntries == 0);
    assert(tracker.issueEntries(1));
    ReorderSurvivalTracker::Epoch epoch;
    assert(tracker.closeEpoch(true, epoch));
    assert(epoch.issuedLines == 1);
    assert(epoch.issuedEntries == 1);
    assert(tracker.reconciled());
}

void
testPartitionedEpochsAndTransitions()
{
    ReorderSurvivalTracker tracker;
    tracker.begin(9);
    ReorderSurvivalTracker::Epoch epoch;
    for (int pass = 0; pass < 4; ++pass) {
        for (int i = 0; i < 4096; ++i) {
            assert(tracker.select(
                (static_cast<uint64_t>(pass) << 32) | i));
            assert(tracker.admit());
        }
        assert(tracker.issueLine(10));
        assert(tracker.issueEntries(2048));
        assert(tracker.issueLine(12));
        assert(tracker.issueEntries(2048));
        if (pass != 3) {
            assert(tracker.markDrain(
                ReorderSurvivalTracker::DrainReason::PartitionBoundary));
            assert(tracker.closeEpoch(false, epoch));
            assert(epoch.id == static_cast<uint64_t>(pass));
            assert(epoch.partitionDrains == 1);
            assert(epoch.rowTransitions == 1);
        }
    }
    assert(tracker.closeEpoch(true, epoch));
    assert(tracker.reconciled());
    assert(!tracker.preserves16K());
    assert(tracker.epochs == 4);
    assert(tracker.maxJointAdmissions == 4096);
    assert(tracker.midInstructionDrains() == 3);
}

void
testRTFullEventsRemainWithinOffsetEpochAndMismatchFailsClosed()
{
    ReorderSurvivalTracker tracker;
    tracker.begin(11);
    assert(tracker.select(0));
    // A retry of the same selected iteration after pressure is not a second
    // logical descriptor.
    assert(tracker.select(0));
    assert(tracker.admit());
    assert(tracker.select(1));
    assert(tracker.admit());
    assert(tracker.issueLine(1));
    assert(tracker.issueEntries(1));
    assert(tracker.markDrain(
        ReorderSurvivalTracker::DrainReason::RowTableFull));
    assert(tracker.markDrain(
        ReorderSurvivalTracker::DrainReason::RowTableFull));
    ReorderSurvivalTracker::Epoch epoch;
    assert(!tracker.drainPending());
    assert(!tracker.closeEpoch(false, epoch));
    assert(!tracker.closeEpoch(true, epoch));
    assert(tracker.issueEntries(1));
    assert(tracker.closeEpoch(true, epoch));
    assert(epoch.rtFullDrains == 2);
    assert(epoch.maxJointAdmissions == 2);
    assert(tracker.totalSelectedDescriptors == 2);
    assert(tracker.reconciled());
    assert(!tracker.issueEntries(1));
}

} // anonymous namespace

int
main()
{
    testOne16KVisibilityDomain();
    testIssueLineThenResponseEntriesCreditedExactlyOnce();
    testPartitionedEpochsAndTransitions();
    testRTFullEventsRemainWithinOffsetEpochAndMismatchFailsClosed();
    std::cout << "reorder_survival_tracker_test: PASS\n";
    return 0;
}
