#ifndef __MEM_MAA_LOGICAL_SPD_CACHE_GEM5_BRIDGE_HH__
#define __MEM_MAA_LOGICAL_SPD_CACHE_GEM5_BRIDGE_HH__

#include <cstddef>
#include <memory>
#include <vector>

#include "mem/MAA/LogicalSPDCacheRuntime.hh"

namespace gem5 {

/**
 * MAA-owned adapter boundary for the logical SPD-cache Runtime.
 *
 * The initial lifecycle slice deliberately keeps admission closed.  It makes
 * Runtime payload ownership live in the simulator without exposing an MMIO,
 * cache-packet, or native-map path before those owners are implemented.
 */
class LogicalSPDCacheGem5Bridge
{
  public:
    explicit LogicalSPDCacheGem5Bridge(std::size_t numMaas);
    ~LogicalSPDCacheGem5Bridge() = default;

    LogicalSPDCacheGem5Bridge(const LogicalSPDCacheGem5Bridge &) = delete;
    LogicalSPDCacheGem5Bridge &operator=(
        const LogicalSPDCacheGem5Bridge &) = delete;

    std::size_t runtimeCount() const { return runtimes.size(); }
    bool admissionClosed() const { return true; }

    const LogicalSPDCacheRuntime &runtime(std::size_t maaId) const;

  private:
    std::vector<std::unique_ptr<LogicalSPDCacheRuntime>> runtimes;
};

} // namespace gem5

#endif // __MEM_MAA_LOGICAL_SPD_CACHE_GEM5_BRIDGE_HH__
