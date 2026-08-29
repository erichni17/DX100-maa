#include <cstdint>
#include <iostream>

#include "mem/MAA/DenseBackingLineTracker.hh"

namespace
{

using Tracker = gem5::maa::DenseBackingLineTracker;
using Result = Tracker::Result;

#define CHECK(condition) \
    do { \
        if (!(condition)) { \
            std::cerr << "check failed line " << __LINE__ << ": " \
                      << #condition << "\n"; \
            return 1; \
        } \
    } while (false)

} // anonymous namespace

int
main()
{
    Tracker tracker;
    CHECK(tracker.reset(0) == Result::Invalid);
    CHECK(tracker.reset(Tracker::MaxLines + 1) == Result::Invalid);
    CHECK(tracker.reset(Tracker::MaxLines) == Result::Accepted);
    CHECK(tracker.lines() == Tracker::MaxLines);
    CHECK(tracker.initializedLines() == 0);
    CHECK(!tracker.allInitialized());

    for (uint32_t line = 0; line < Tracker::MaxLines; ++line) {
        CHECK(tracker.validLine(line));
        CHECK(!tracker.initialized(line));
        CHECK(tracker.acknowledge(line) == Result::Accepted);
        CHECK(tracker.initialized(line));
        CHECK(tracker.acknowledge(line) == Result::Duplicate);
    }
    CHECK(!tracker.validLine(Tracker::MaxLines));
    CHECK(!tracker.initialized(Tracker::MaxLines));
    CHECK(tracker.acknowledge(Tracker::MaxLines) == Result::Invalid);
    CHECK(tracker.initializedLines() == Tracker::MaxLines);
    CHECK(tracker.allInitialized());

    CHECK(tracker.reset(17) == Result::Accepted);
    CHECK(tracker.lines() == 17);
    CHECK(tracker.initializedLines() == 0);
    for (uint32_t line = 0; line < 17; ++line)
        CHECK(tracker.acknowledge(line) == Result::Accepted);
    CHECK(tracker.allInitialized());
    CHECK(!tracker.initialized(17));

    CHECK(Tracker::fullLineTransport(false, false, 0));
    CHECK(Tracker::fullLineTransport(true, false, 0));
    CHECK(!Tracker::fullLineTransport(false, false, 0x1));
    CHECK(Tracker::fullLineTransport(true, false, 0x1));
    CHECK(!Tracker::fullLineTransport(true, true, 0x1));

    std::cout << "dense_backing_line_tracker_test: PASS\n";
    return 0;
}
