#include <cassert>
#include <cstdint>

#include "mem/LANLMAA/FaceComputeTiming.hh"

using gem5::lanlmaa::FaceComputeTiming;

int
main()
{
    const FaceComputeTiming disabled(0, 1, 1);
    assert(!disabled.enabled());
    assert(disabled.units() == 1);

    FaceComputeTiming single(16, 4, 1);
    assert(single.enabled());
    const auto first = single.issue(10);
    assert(first);
    assert(first->unit == 0);
    assert(first->completionCycle == 26);
    assert(single.nextIssueCycle(0) == 14);
    assert(!single.issue(13));
    const auto second = single.issue(14);
    assert(second);
    assert(second->unit == 0);
    assert(second->completionCycle == 30);

    FaceComputeTiming replicated(32, 8, 2);
    const auto lane0 = replicated.issue(20);
    const auto lane1 = replicated.issue(20);
    assert(lane0 && lane0->unit == 0 && lane0->completionCycle == 52);
    assert(lane1 && lane1->unit == 1 && lane1->completionCycle == 52);
    assert(!replicated.issue(20));
    assert(!replicated.issue(27));
    replicated.reset(100);
    assert(!replicated.issue(99));
    const auto resetLane0 = replicated.issue(100);
    const auto resetLane1 = replicated.issue(100);
    assert(resetLane0 && resetLane0->unit == 0);
    assert(resetLane1 && resetLane1->unit == 1);

    return 0;
}
