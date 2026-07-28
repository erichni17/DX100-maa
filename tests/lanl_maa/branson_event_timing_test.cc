#include <cassert>
#include <cstdint>
#include <vector>

#include "mem/LANLMAA/BransonEventTiming.hh"

using gem5::lanlmaa::BransonContextScheduler;
using gem5::lanlmaa::BransonEventTiming;

int
main()
{
    BransonEventTiming single(4, 2, 1);
    const auto first = single.issue(10);
    assert(first && first->unit == 0 && first->completionCycle == 14);
    assert(!single.issue(11));
    const auto second = single.issue(12);
    assert(second && second->completionCycle == 16);

    BransonEventTiming replicated(16, 8, 2);
    const auto lane0 = replicated.issue(20);
    const auto lane1 = replicated.issue(20);
    assert(lane0 && lane0->unit == 0 && lane0->completionCycle == 36);
    assert(lane1 && lane1->unit == 1 && lane1->completionCycle == 36);
    assert(!replicated.issue(20));
    replicated.reset(100);
    assert(!replicated.issue(99));
    assert(replicated.issue(100)->unit == 0);

    BransonContextScheduler scheduler(4, 2);
    std::vector<bool> ready{true, true, true, true};
    std::vector<bool> active{true, true, true, true};
    assert(scheduler.select(ready, active) == 0);
    scheduler.issued(0);
    assert(scheduler.select(ready, active) == 0);
    scheduler.issued(0);
    assert(scheduler.preferredContext() == 1);
    assert(scheduler.issuesInQuantum() == 0);

    ready = {true, false, true, false};
    assert(scheduler.select(ready, active) == 2);
    scheduler.issued(2);
    assert(scheduler.preferredContext() == 1);
    ready[1] = true;
    assert(scheduler.select(ready, active) == 1);
    scheduler.issued(1);
    assert(scheduler.issuesInQuantum() == 1);
    scheduler.issued(1);
    assert(scheduler.preferredContext() == 2);

    ready = {false, false, false, false};
    assert(!scheduler.select(ready, active));
    active = {false, false, true, true};
    ready[2] = true;
    assert(scheduler.select(ready, active) == 2);
    assert(scheduler.preferredContext() == 2);
    active = {false, false, false, false};
    assert(!scheduler.select(ready, active));
    scheduler.reset();
    assert(scheduler.preferredContext() == 0);

    return 0;
}
