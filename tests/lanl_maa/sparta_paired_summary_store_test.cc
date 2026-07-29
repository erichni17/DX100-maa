#include <cassert>
#include <cstdint>

#include "mem/LANLMAA/SpartaPairedSummaryStore.hh"

using gem5::lanlmaa::SharedOperationStore;
using gem5::lanlmaa::SpartaPairedSummaryStore;

int
main()
{
    SpartaPairedSummaryStore store;
    assert(store.size() == 0);
    assert(!store.configure(0));
    assert(!store.configure(SharedOperationStore.entries + 1));
    assert(store.configure(SharedOperationStore.entries));

    auto *entry = store.get(4);
    assert(entry != nullptr);
    entry->sums[0] = UINT64_C(0x3ff0000000000000);
    entry->eligible = 1;
    assert(!store.configure(SharedOperationStore.entries + 1));
    assert(store.size() == SharedOperationStore.entries);
    assert(store.get(4)->eligible == 1);

    store.beginCycle();
    assert(store.bankAvailable(0));
    assert(store.reserveAccess(0));
    assert(!store.bankAvailable(4));
    assert(!store.reserveAccess(4));
    assert(store.reserveAccess(1));
    assert(store.reserveAccess(2));
    assert(store.reserveAccess(3));
    assert(!store.reserveAccess(5));

    store.beginCycle();
    assert(store.reserveAccess(4));
    const auto *retained = store.get(4);
    assert(retained != nullptr);
    assert(retained->sums[0] == UINT64_C(0x3ff0000000000000));
    assert(retained->eligible == 1);

    store.clear();
    assert(store.size() == 0);
    assert(store.get(4) == nullptr);
    assert(!store.reserveAccess(4));
    return 0;
}
