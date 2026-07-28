#include <cassert>
#include <cstddef>

#include "mem/LANLMAA/BransonContextLimit.hh"

using gem5::lanlmaa::BransonContextLimit;

int
main()
{
    const BransonContextLimit disabled(64, 0);
    assert(disabled.valid());
    assert(disabled.capacity() == 64);
    assert(!disabled.enabled());
    assert(!disabled.wouldBlock(63));
    assert(disabled.wouldBlock(64));
    assert(!disabled.throttleWouldBlock(64));
    assert(!disabled.requiresDrain(false, 64));
    assert(disabled.requiresDrain(true, 64));

    const BransonContextLimit capped(64, 16);
    assert(capped.valid());
    assert(capped.capacity() == 16);
    assert(capped.enabled());
    assert(!capped.wouldBlock(15));
    assert(capped.wouldBlock(16));
    assert(capped.wouldBlock(17));
    assert(capped.throttleWouldBlock(16));
    assert(!capped.requiresDrain(false, 16));
    assert(capped.requiresDrain(true, 16));

    assert(!BransonContextLimit(0, 0).valid());
    assert(!BransonContextLimit(64, 65).valid());
    assert(BransonContextLimit(16, 16).valid());
    return 0;
}
