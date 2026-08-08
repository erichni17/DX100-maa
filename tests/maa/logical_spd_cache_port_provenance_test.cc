#include <cstdlib>
#include <iostream>

#include "mem/MAA/LogicalSPDCachePortProvenance.hh"

int
main()
{
    using Provenance = gem5::LogicalSPDCachePortProvenance;
    constexpr uint8_t expected = 2;
    if (!Provenance::responseMatches(expected, 2) ||
        Provenance::responseMatches(expected, 1) ||
        !Provenance::retryMatches(expected, 2) ||
        Provenance::retryMatches(expected, 3)) {
        std::cerr << "FAIL logical SPD callback-port provenance\n";
        return EXIT_FAILURE;
    }
    std::cout << "PASS logical_spd_cache_port_provenance_test\n";
    return EXIT_SUCCESS;
}
