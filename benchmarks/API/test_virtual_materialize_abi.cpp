#include <cstdint>
#include <iostream>

#if !defined(GEM5)
#error "test_virtual_materialize_abi requires the gem5 API encoder"
#endif

#include "MAA_gem5.hpp"

extern "C" void
m5_add_mem_region(void *, void *, int8_t)
{}

extern "C" void
m5_clear_mem_region()
{}

int
main()
{
    constexpr int PageElements = 4096;
    constexpr int Pages = 4;
    alignas(64) float backing[Pages * PageElements]{};
    alignas(64) uint32_t registers[NUM_SCALAR_REGS]{};
    volatile uint64_t word0 = 0;
    volatile uint64_t word1 = 0;
    volatile uint64_t base = 0;
    volatile uint64_t unusedBacking = 0;
    volatile uint64_t unusedIndex = 0;

    REG_noncacheable = registers;
    INSTR_opcode_datatype_optype_tdst1_tdst2 = &word0;
    INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc = &word1;
    INSTR_baseaddr = &base;
    INSTR_backingaddr = &unusedBacking;
    INSTR_indexaddr = &unusedIndex;

    const int minReg = 1;
    const int maxReg = 2;
    const int strideReg = 3;
    const int completionToken = 7;
    const int destinationTile = 11;
    maa_const<int>(0, minReg);
    maa_const<int>(PageElements, maxReg);
    maa_const<int>(1, strideReg);

    uintptr_t emittedBases[Pages]{};
    int errors = 0;
    for (int page = 0; page < Pages; ++page) {
        maa_stream_load_virtual_page<float>(
            backing + page * PageElements, completionToken, minReg, maxReg,
            strideReg, destinationTile);
        emittedBases[page] = static_cast<uintptr_t>(base);

        const uint8_t opcode = static_cast<uint8_t>(word0 >> 32);
        const uint8_t token = static_cast<uint8_t>(word1 >> 56);
        const uint8_t encodedMin = static_cast<uint8_t>(word1 >> 24);
        const uint8_t encodedMax = static_cast<uint8_t>(word1 >> 16);
        if (opcode != OpcodeType::STREAM_LD || token != completionToken ||
            encodedMin != minReg || encodedMax != maxReg ||
            get_reg<int>(minReg) != 0 ||
            get_reg<int>(maxReg) != PageElements) {
            ++errors;
        }
    }

    const uintptr_t first = reinterpret_cast<uintptr_t>(backing);
    for (int page = 0; page < Pages; ++page) {
        const uintptr_t expected =
            first + page * PageElements * sizeof(float);
        if (emittedBases[page] != expected)
            ++errors;
        for (int prior = 0; prior < page; ++prior) {
            if (emittedBases[prior] == emittedBases[page])
                ++errors;
        }
    }

    std::cout << "VIRTUAL_MATERIALIZE_ABI pages=" << Pages
              << " local_min=" << get_reg<int>(minReg)
              << " local_max=" << get_reg<int>(maxReg)
              << " distinct_bases=" << (errors == 0 ? Pages : 0)
              << " errors=" << errors << std::endl;
    return errors == 0 ? 0 : 1;
}
