#ifndef __MEM_MAA_LOGICAL_SPD_CACHE_PORT_PROVENANCE_HH__
#define __MEM_MAA_LOGICAL_SPD_CACHE_PORT_PROVENANCE_HH__

#include <cstdint>

namespace gem5 {

/** Sender state records only the expected port, never callback provenance. */
class LogicalSPDCachePortProvenance
{
  public:
    static constexpr bool responseMatches(uint8_t expected, uint8_t actual)
    {
        return expected == actual;
    }

    static constexpr bool retryMatches(uint8_t expected, uint8_t actual)
    {
        return expected == actual;
    }
};

} // namespace gem5

#endif // __MEM_MAA_LOGICAL_SPD_CACHE_PORT_PROVENANCE_HH__
