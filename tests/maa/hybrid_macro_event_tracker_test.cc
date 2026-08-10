#include <cassert>
#include <cstdint>
#include <iostream>

#include "mem/MAA/HybridMacroEventTracker.hh"

int
main()
{
    using Tracker = gem5::HybridMacroEventTracker;
    using Stage = Tracker::Stage;

    Tracker tracker;
    assert(tracker.begin(100));
    assert(!tracker.begin(100));
    assert(tracker.sample(110));
    assert(tracker.issue(Stage::PageFill, 110, 2));
    assert(tracker.traffic(Stage::PageFill, 3, 192));
    assert(tracker.issue(Stage::ALU, 120));
    assert(tracker.complete(Stage::PageFill, 140));
    assert(tracker.retry(Stage::StreamStore, 145));
    assert(tracker.complete(Stage::ALU, 150));
    assert(tracker.issue(Stage::StreamStore, 150));
    assert(tracker.traffic(Stage::StreamStore, 4, 256));
    assert(tracker.complete(Stage::StreamStore, 180));
    assert(tracker.finish(190));

    const auto &record = tracker.result();
    const auto &fill = record.stages[static_cast<size_t>(Stage::PageFill)];
    const auto &alu = record.stages[static_cast<size_t>(Stage::ALU)];
    const auto &store =
        record.stages[static_cast<size_t>(Stage::StreamStore)];
    assert(record.startTick == 100);
    assert(record.endTick == 190);
    assert(record.overlapTicks == 20);
    assert(record.exposedIdleTicks == 20);
    assert(record.activeStageHighWater == 2);
    assert(fill.firstIssueTick == 110);
    assert(fill.lastIssueTick == 110);
    assert(fill.lastCompleteTick == 140);
    assert(fill.activeTicks == 30);
    assert(fill.issues == 1 && fill.completions == 1);
    assert(fill.lines == 3 && fill.bytes == 192);
    assert(fill.queueHighWater == 2);
    assert(alu.firstIssueTick == 120);
    assert(alu.lastCompleteTick == 150);
    assert(alu.activeTicks == 30);
    assert(store.firstIssueTick == 150);
    assert(store.lastCompleteTick == 180);
    assert(store.activeTicks == 30);
    assert(store.retries == 1);
    assert(store.lines == 4 && store.bytes == 256);

    Tracker invalid;
    assert(!invalid.sample(1));
    assert(invalid.begin(0));
    assert(!invalid.complete(Stage::PageFill, 1));
    assert(invalid.issue(Stage::PageFill, 1));
    assert(!invalid.finish(2));
    assert(invalid.complete(Stage::PageFill, 2));
    assert(invalid.finish(2));

    std::cout << "hybrid_macro_event_tracker_test: PASS\n";
    return 0;
}
