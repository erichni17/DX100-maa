#include <cstdint>
#include <cstdlib>
#include <iostream>

#include "../../benchmarks/gapbs/src/sssp_chunk_admission.hh"

using Tracker = sssp_chunk_admission::Tracker;

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;         \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

static void
mixedActiveSourceRejectsOnlyItsOwner()
{
    Tracker tracker;
    CHECK(tracker.reset(4));
    uint32_t first_epoch = 0;
    uint32_t first_owner = 0;
    uint32_t second_epoch = 0;
    uint32_t second_owner = 0;
    CHECK(tracker.observeDestination(
        0, false, 1, first_epoch, first_owner));
    CHECK(tracker.observeDestination(
        1, true, 1, second_epoch, second_owner));
    CHECK(tracker.safe(0));
    CHECK(!tracker.safe(1));
    CHECK(tracker.safe(2));
    CHECK(tracker.safe(3));
    CHECK(tracker.hasReason(1, Tracker::ActiveSource));
    CHECK(tracker.hasAnyReason(1));
    CHECK(!tracker.hasAnyReason(0));
    CHECK(tracker.count(Tracker::ActiveSource) == 1);
}

static void
crossOwnerRejectsEveryConflictingOwnerOnly()
{
    Tracker tracker;
    CHECK(tracker.reset(4));
    uint32_t epoch = 0;
    uint32_t owner = 0;
    CHECK(tracker.observeDestination(1, false, 7, epoch, owner));
    CHECK(tracker.observeDestination(2, false, 7, epoch, owner));
    CHECK(tracker.observeDestination(3, false, 7, epoch, owner));
    CHECK(tracker.safe(0));
    CHECK(!tracker.safe(1));
    CHECK(!tracker.safe(2));
    CHECK(!tracker.safe(3));
    CHECK(tracker.count(Tracker::CrossOwner) == 3);
    CHECK(tracker.hasAnyReason(1));
    CHECK(tracker.hasAnyReason(2));
    CHECK(tracker.hasAnyReason(3));
}

static void
overlappingReasonsCoverOwnersOnceWithoutChangingReasonTallies()
{
    Tracker tracker;
    CHECK(tracker.reset(2));
    uint32_t epoch = 0;
    uint32_t owner = 0;
    CHECK(tracker.observeDestination(0, true, 9, epoch, owner));
    CHECK(tracker.observeDestination(1, false, 9, epoch, owner));
    CHECK(tracker.count(Tracker::ActiveSource) == 1);
    CHECK(tracker.count(Tracker::CrossOwner) == 2);
    CHECK(tracker.hasReason(0, Tracker::ActiveSource));
    CHECK(tracker.hasReason(0, Tracker::CrossOwner));
    CHECK(tracker.hasAnyReason(0));
    CHECK(tracker.hasAnyReason(1));
}

static void
globalBoundsAndInvalidInputsFailClosed()
{
    Tracker tracker;
    CHECK(!tracker.reset(0));
    CHECK(tracker.reset(3));
    tracker.rejectAll(Tracker::Bounds);
    CHECK(tracker.count(Tracker::Bounds) == 3);
    uint32_t epoch = 0;
    uint32_t owner = 0;
    CHECK(!tracker.observeDestination(3, false, 1, epoch, owner));
    CHECK(!tracker.observeDestination(0, false, 0, epoch, owner));
    CHECK(!tracker.reject(3, Tracker::ActiveSource));
}

int
main()
{
    mixedActiveSourceRejectsOnlyItsOwner();
    crossOwnerRejectsEveryConflictingOwnerOnly();
    overlappingReasonsCoverOwnersOnceWithoutChangingReasonTallies();
    globalBoundsAndInvalidInputsFailClosed();
    std::cout << "PASS SSSP chunk admission\n";
    return 0;
}
