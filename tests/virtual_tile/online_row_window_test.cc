#include <cassert>
#include <cstdint>

#include "mem/MAA/OnlineRowWindow.hh"

using gem5::OnlineRowWindow;

static_assert(sizeof(OnlineRowWindow) <= OnlineRowWindow::chargedBytes());

int
main()
{
    using Result = OnlineRowWindow::Result;

    OnlineRowWindow window;
    assert(window.configure(8, 4, 4, 4) == Result::Accepted);
    assert(window.recordAdmission(11, 0, 1, 1) == Result::Accepted);
    assert(window.recordAdmission(12, 1, 2, 2) == Result::Accepted);
    assert(window.recordAdmission(11, 2, 3, 2) == Result::Accepted);
    assert(window.recordAdmission(13, 3, 4, 3) == Result::Accepted);
    auto victim = window.selectOldest();
    assert(victim.result == Result::Accepted);
    assert(victim.grow == 11);
    assert(victim.descriptors == 2);
    assert(victim.visits == OnlineRowWindow::MaxTrackedGrows);
    assert(window.recordVictim(victim) == Result::Accepted);
    assert(window.recordRetirement(2) == Result::Accepted);
    assert(window.recordRetirement(0) == Result::Accepted);

    assert(window.recordAdmission(11, 4, 3, 3) == Result::Accepted);
    assert(window.reopens() == 1);
    assert(window.recordAdmission(14, 5, 4, 4) == Result::Accepted);
    victim = window.selectOldest();
    assert(victim.grow == 12);
    assert(window.recordVictim(victim) == Result::Accepted);
    assert(window.recordRetirement(1) == Result::Accepted);
    assert(window.recordAdmission(15, 6, 4, 4) == Result::Accepted);
    victim = window.selectOldest();
    assert(victim.grow == 13);
    assert(window.recordVictim(victim) == Result::Accepted);
    assert(window.recordRetirement(3) == Result::Accepted);
    assert(window.recordAdmission(16, 7, 4, 4) == Result::Accepted);

    assert(window.recordRetirement(7) == Result::Accepted);
    assert(window.recordRetirement(4) == Result::Accepted);
    assert(window.recordRetirement(6) == Result::Accepted);
    assert(window.recordRetirement(5) == Result::Accepted);
    assert(window.finish(0, 0) == Result::Accepted);
    assert(window.totalAdmissions() == 8);
    assert(window.totalRetirements() == 8);
    assert(window.maxDescriptors() == 4);
    assert(window.maxLines() == 4);
    assert(window.maxRows() == 4);
    assert(window.victims() == 3);
    assert(window.visits() == 3 * OnlineRowWindow::MaxTrackedGrows);
    assert(OnlineRowWindow::chargedBytes() == 12416);

    OnlineRowWindow bad;
    assert(bad.configure(2, 1, 1, 1) == Result::Accepted);
    assert(bad.recordAdmission(1, 1, 1, 1) ==
           Result::NonSequentialAdmission);
    assert(bad.recordAdmission(1, 0, 1, 1) == Result::Accepted);
    assert(bad.recordAdmission(2, 1, 1, 1) == Result::DescriptorOverflow);
    assert(bad.recordRetirement(2) == Result::InvalidRetirement);
    assert(bad.finish(0, 0) == Result::Incomplete);

    OnlineRowWindow bounds;
    assert(bounds.configure(2, 2, 1, 1) == Result::Accepted);
    assert(bounds.recordAdmission(1, 0, 2, 1) == Result::LineOverflow);
    assert(bounds.recordAdmission(1, 0, 1, 2) == Result::RowOverflow);
    assert(bounds.recordAdmission(1, 0, 1, 1) == Result::Accepted);
    auto stale = bounds.selectOldest();
    assert(stale.result == Result::Accepted);
    stale.descriptors++;
    assert(bounds.recordVictim(stale) == Result::StaleVictim);

    OnlineRowWindow history;
    assert(history.configure(OnlineRowWindow::MaxTrackedGrows + 1,
                             OnlineRowWindow::MaxTrackedGrows + 1,
                             OnlineRowWindow::MaxLineSlots,
                             OnlineRowWindow::MaxRowDirectories) ==
           Result::Accepted);
    for (uint32_t iteration = 0;
         iteration < OnlineRowWindow::MaxTrackedGrows; ++iteration) {
        assert(history.recordAdmission(iteration, iteration, 1, 1) ==
               Result::Accepted);
    }
    assert(history.recordAdmission(OnlineRowWindow::MaxTrackedGrows,
                                   OnlineRowWindow::MaxTrackedGrows,
                                   1, 1) == Result::HistoryOverflow);

    assert(window.configure(16384, 4096, 4096, 512) == Result::Accepted);
    return 0;
}
