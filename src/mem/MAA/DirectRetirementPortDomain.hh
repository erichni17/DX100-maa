#ifndef __MEM_MAA_DIRECT_RETIREMENT_PORT_DOMAIN_HH__
#define __MEM_MAA_DIRECT_RETIREMENT_PORT_DOMAIN_HH__

#include <cstddef>
#include <cstdint>

#include "mem/MAA/HybridConsumerPipeline.hh"

namespace gem5 {

/**
 * The direct-retirement retry table is deliberately four ports wide.  Direct
 * retirement is therefore valid only when its runtime cache-port domain is
 * exactly the same fixed four-port domain.
 */
class DirectRetirementPortDomain
{
  public:
    static constexpr uint8_t PortCount = HybridConsumerPipeline::PortCount;

    static constexpr bool eligible(unsigned numCores,
                                   std::size_t cacheSidePortCount)
    {
        return numCores == PortCount && cacheSidePortCount == PortCount;
    }

    static constexpr bool contains(uint8_t port)
    {
        return port < PortCount;
    }

    static constexpr bool harmlessInactiveWake(uint8_t port,
                                                uint8_t activeContexts)
    {
        return !contains(port) && activeContexts == 0;
    }
};

static_assert(DirectRetirementPortDomain::PortCount == 4);

} // namespace gem5

#endif // __MEM_MAA_DIRECT_RETIREMENT_PORT_DOMAIN_HH__
