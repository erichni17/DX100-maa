#ifndef __MEM_MAA_LOGICAL_SPD_HIDDEN_PAYLOAD_HH__
#define __MEM_MAA_LOGICAL_SPD_HIDDEN_PAYLOAD_HH__

#include <cstddef>

namespace gem5 {

/**
 * Accounting-only compatibility constants for the retired SPD hidden tail.
 *
 * LogicalSPDCacheRuntime is the sole owner of these bytes.  This header must
 * never provide an SPD tile mapping, allocation helper, or payload accessor.
 */
struct LogicalSPDPrivatePayloadAccounting
{
    static constexpr std::size_t LogicalSlotsPerMAA = 2;
    static constexpr std::size_t PageElements = 4096;
    static constexpr std::size_t FP64Bytes = 8;
    static constexpr std::size_t PayloadBytesPerMAA =
        LogicalSlotsPerMAA * PageElements * FP64Bytes;
};

static_assert(LogicalSPDPrivatePayloadAccounting::PayloadBytesPerMAA ==
              65536);

} // namespace gem5

#endif // __MEM_MAA_LOGICAL_SPD_HIDDEN_PAYLOAD_HH__
