#ifndef __MEM_MAA_SOA_JIT_SAFETY_HH__
#define __MEM_MAA_SOA_JIT_SAFETY_HH__

#include <cstddef>
#include <cstdint>
#include <limits>

namespace gem5
{

class SoaJitSafety
{
  public:
    static constexpr std::size_t LocalLineBytes = 64;
    static constexpr std::size_t CompletionTokenBytes = sizeof(uint32_t);
    static constexpr uint32_t MaskedIndexInactive =
        std::numeric_limits<uint32_t>::max();
    static constexpr uint64_t MaskedIndexModeTag =
        std::numeric_limits<uint64_t>::max();
    static constexpr std::size_t MaskedIndexCompareBits = 32;
    static constexpr std::size_t MaskedIndexModeStateBits = 1;
    static constexpr std::size_t MaskedIndexAdditionalBufferBytes = 0;

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

    static constexpr bool
    maskedIndexMarkerOutsideLegalRange(uint64_t a_addr, uint64_t a_min,
                                       uint64_t a_max,
                                       std::size_t word_bytes)
    {
        if ((word_bytes != sizeof(uint32_t) &&
             word_bytes != sizeof(uint64_t)) ||
            a_addr < a_min || a_addr >= a_max ||
            a_max - a_addr < word_bytes)
            return false;
        const uint64_t legal_words = (a_max - a_addr) / word_bytes;
        return legal_words <= MaskedIndexInactive;
    }
};

} // namespace gem5

#endif // __MEM_MAA_SOA_JIT_SAFETY_HH__
