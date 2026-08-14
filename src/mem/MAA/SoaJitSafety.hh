#ifndef __MEM_MAA_SOA_JIT_SAFETY_HH__
#define __MEM_MAA_SOA_JIT_SAFETY_HH__

#include <cstddef>
#include <cstdint>

namespace gem5
{

class SoaJitSafety
{
  public:
    static constexpr std::size_t LocalLineBytes = 64;
    static constexpr std::size_t CompletionTokenBytes = sizeof(uint32_t);

    static constexpr bool
    typedOperandsAligned(uint64_t a_addr, uint64_t value_addr,
                         uint64_t index_addr, uint64_t predicate_addr,
                         std::size_t word_bytes)
    {
        return (word_bytes == sizeof(uint32_t) ||
                word_bytes == sizeof(uint64_t)) &&
               a_addr % word_bytes == 0 &&
               value_addr % word_bytes == 0 &&
               index_addr % alignof(uint32_t) == 0 &&
               (predicate_addr == 0 ||
                predicate_addr % alignof(uint32_t) == 0);
    }
};

} // namespace gem5

#endif // __MEM_MAA_SOA_JIT_SAFETY_HH__
