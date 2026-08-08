#include <cstdlib>
#include <iostream>

#include "mem/MAA/LogicalSPDCacheLiveAdapterState.hh"
#include "mem/MAA/LogicalSPDCachePortProvenance.hh"

int
main()
{
    using Adapter = gem5::LogicalSPDCacheLiveAdapterState;
    using Ports = gem5::LogicalSPDCachePortProvenance;
    Adapter first;
    Adapter second;
    first.armRetry(1);
    second.armRetry(2);

    // A wrong response is rejected before it can mutate adapter state.
    if (Ports::responseMatches(1, 2) || first.retryPermitPending())
        return EXIT_FAILURE;
    // An unrelated response event grants no retry permit.
    if (first.consumeRetry(1) || second.consumeRetry(2))
        return EXIT_FAILURE;
    // A wrong retry port cannot enable either pending execution.
    first.notifyRetry(2);
    if (first.retryPermitPending() || first.consumeRetry(1))
        return EXIT_FAILURE;
    // Same-port retry enables exactly its execution and is one-shot.
    first.notifyRetry(1);
    if (!first.consumeRetry(1) || first.consumeRetry(1) ||
        second.retryPermitPending())
        return EXIT_FAILURE;
    // Different-port retries remain isolated even when both are pending.
    first.armRetry(1);
    second.notifyRetry(2);
    if (first.consumeRetry(1) || !second.consumeRetry(2))
        return EXIT_FAILURE;
    first.notifyRetry(1);
    if (!first.consumeRetry(1))
        return EXIT_FAILURE;
    std::cout << "PASS logical_spd_cache_live_adapter_state_test\n";
    return EXIT_SUCCESS;
}
