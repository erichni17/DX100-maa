#ifndef __MEM_MAA_VIRTUAL_BACKING_OWNERSHIP_HH__
#define __MEM_MAA_VIRTUAL_BACKING_OWNERSHIP_HH__

#include <cstdint>
#include <limits>

namespace gem5::maa
{

class VirtualBackingOwnership
{
  public:
    static bool
    span(uint64_t base, uint64_t elements, uint32_t wordBytes,
         uint64_t &end)
    {
        if (elements == 0 || wordBytes == 0 ||
            elements > std::numeric_limits<uint64_t>::max() / wordBytes)
            return false;
        const uint64_t bytes = elements * wordBytes;
        if (base > std::numeric_limits<uint64_t>::max() - bytes)
            return false;
        end = base + bytes;
        return end > base;
    }

    static bool
    overlaps(uint64_t leftBase, uint64_t leftEnd,
             uint64_t rightBase, uint64_t rightEnd)
    {
        return leftBase < leftEnd && rightBase < rightEnd &&
               leftBase < rightEnd && rightBase < leftEnd;
    }
};

} // namespace gem5::maa

#endif // __MEM_MAA_VIRTUAL_BACKING_OWNERSHIP_HH__
