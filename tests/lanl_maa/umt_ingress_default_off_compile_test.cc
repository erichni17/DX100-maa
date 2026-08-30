// This translation unit intentionally does not define the observer macro.
// It validates the default production configuration has no observer API.
#include "mem/LANLMAA/UmtOrderedWaveStreamState.hh"

using namespace gem5::lanlmaa;

int
main()
{
    UmtOrderedWaveStreamState state;
    return state.groups() == 0 ? 0 : 1;
}
