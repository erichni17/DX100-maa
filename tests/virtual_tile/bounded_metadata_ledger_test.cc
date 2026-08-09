#include <cassert>
#include <iostream>

#include "mem/MAA/BoundedMetadataLedger.hh"

using gem5::BoundedMetadataLedger;

int
main()
{
    const BoundedMetadataLedger ledger{
        4096, 4096, 512, 4096, 4096, 32};
    assert(ledger.wordBytes() == 32768);
    assert(ledger.offsetBytes() == 36864);
    assert(ledger.rowDirectoryBytes() == 5120);
    assert(ledger.rowLineBytes() == 73728);
    assert(ledger.reorderMetadataBytes() == 148480);
    assert(ledger.scratchpadPayloadBytes() == 524288);
    std::cout << "bounded_metadata_ledger_test: PASS\n";
    return 0;
}
