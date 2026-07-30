#include <cassert>
#include <cstddef>

#include "mem/LANLMAA/LineTableGeometry.hh"

using namespace gem5::lanlmaa;

namespace
{

void
testGeometryAndMapping()
{
    LineTableGeometry table(32, 4, 64);
    assert(table.valid());
    assert(table.banks() == 4);
    assert(table.ways() == 8);
    assert(table.bank(0x000) == 0);
    assert(table.bank(0x040) == 1);
    assert(table.bank(0x080) == 2);
    assert(table.bank(0x0c0) == 3);
    assert(table.bank(0x100) == 0);
    assert(table.begin(2) == 16);
    assert(table.end(2) == 24);
}

void
testOneDistinctAddressPerBankAndCycle()
{
    LineTableGeometry table(32, 4, 64);
    table.beginCycle();
    assert(table.access(0x000) == LineBankAccess::DistinctLine);
    assert(table.access(0x000) == LineBankAccess::SameLine);
    assert(table.access(0x040) == LineBankAccess::DistinctLine);
    assert(table.access(0x100) == LineBankAccess::Conflict);

    table.beginCycle();
    assert(table.access(0x100) == LineBankAccess::DistinctLine);
    assert(table.access(0x000) == LineBankAccess::Conflict);
}

void
testInvalidGeometryAndAccessFailClosed()
{
    assert(!LineTableGeometry(0, 4, 64).valid());
    assert(!LineTableGeometry(32, 0, 64).valid());
    assert(!LineTableGeometry(32, 3, 64).valid());
    assert(!LineTableGeometry(30, 4, 64).valid());
    assert(!LineTableGeometry(32, 4, 48).valid());

    LineTableGeometry table(32, 4, 64);
    table.beginCycle();
    assert(table.access(0x008) == LineBankAccess::Invalid);
    assert(table.access(0x000) == LineBankAccess::DistinctLine);
}

} // anonymous namespace

int
main()
{
    testGeometryAndMapping();
    testOneDistinctAddressPerBankAndCycle();
    testInvalidGeometryAndAccessFailClosed();
    return 0;
}
