#include "mem/MAA/LogicalSPDCacheGem5Bridge.hh"

#include "base/logging.hh"

namespace gem5 {

LogicalSPDCacheGem5Bridge::LogicalSPDCacheGem5Bridge(std::size_t numMaas)
{
    panic_if(numMaas == 0, "Logical SPD bridge requires at least one MAA\n");
    runtimes.reserve(numMaas);
    for (std::size_t maaId = 0; maaId < numMaas; ++maaId)
        runtimes.emplace_back(std::make_unique<LogicalSPDCacheRuntime>());
}

const LogicalSPDCacheRuntime &
LogicalSPDCacheGem5Bridge::runtime(std::size_t maaId) const
{
    panic_if(maaId >= runtimes.size(),
             "Logical SPD Runtime index %zu exceeds count %zu\n",
             maaId, runtimes.size());
    return *runtimes[maaId];
}

} // namespace gem5
