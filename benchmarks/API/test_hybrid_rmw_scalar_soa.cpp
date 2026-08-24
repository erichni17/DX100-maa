#include "MAA.hpp"

#include <array>
#include <cstdint>
#include <cstring>
#include <iostream>

#if !defined(GEM5)
#error "test_hybrid_rmw_scalar_soa requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace
{

constexpr int Logical = 16 * 1024;
constexpr int TargetWords = 64;
using Indices = std::array<uint32_t, Logical>;
using Predicates = std::array<uint32_t, Logical>;

alignas(64) Indices indices;
alignas(64) Predicates predicates;
alignas(64) std::array<float, TargetWords> fpActual;
alignas(64) std::array<float, TargetWords> fpExpected;
alignas(64) std::array<int32_t, TargetWords> intActual;
alignas(64) std::array<int32_t, TargetWords> intExpected;

uint32_t bits(float value)
{
    uint32_t result = 0;
    std::memcpy(&result, &value, sizeof(result));
    return result;
}

void initialize()
{
    for (int word = 0; word < TargetWords; ++word) {
        fpActual[word] = fpExpected[word] =
            static_cast<float>(word - 17);
        intActual[word] = intExpected[word] = word - 17;
    }
    for (int logical = 0; logical < Logical; ++logical) {
        indices[logical] =
            ((logical / 3) * 13 + (logical % 11 == 0 ? 7 : 0)) %
            TargetWords;
        predicates[logical] = logical % 5 != 0 && logical % 29 != 0;
        if (!predicates[logical])
            continue;
        fpExpected[indices[logical]] += 1.25F;
        intExpected[indices[logical]] =
            intExpected[indices[logical]] > 23
                ? intExpected[indices[logical]] : 23;
    }
}

} // anonymous namespace

int main()
{
    static_assert(TILE_SIZE == Logical,
                  "scalar SoA/JIT smoke requires logical 16K tiles");
    initialize();
    std::cout << "HYBRID_RMW_SCALAR_SOA_LAYOUT logical=" << Logical
              << " tile=" << TILE_SIZE << std::endl;
    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(fpActual.data(), fpActual.data() + fpActual.size());
    add_mem_region(intActual.data(), intActual.data() + intActual.size());
    add_mem_region(indices.data(), indices.data() + indices.size());
    add_mem_region(predicates.data(), predicates.data() + predicates.size());

    const int minimum = get_new_reg<int32_t>(0);
    const int maximum = get_new_reg<int32_t>(Logical);
    const int stride = get_new_reg<int32_t>(1);
    const int fpScalar = get_new_reg<float>(1.25F);
    const int intScalar = get_new_reg<int32_t>(23);
    const int completion = get_new_tile<uint32_t>();

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
    maa_indirect_rmw_scalar_soa_jit<float>(
        fpActual.data(), indices.data(), predicates.data(), fpScalar,
        minimum, maximum, stride, completion, Operation_t::ADD_OP);
    wait_ready(completion);
    maa_indirect_rmw_scalar_soa_jit<int32_t>(
        intActual.data(), indices.data(), predicates.data(), intScalar,
        minimum, maximum, stride, completion, Operation_t::MAX_OP);
    wait_ready(completion);
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    int errors = 0;
    for (int word = 0; word < TargetWords; ++word) {
        if (bits(fpActual[word]) != bits(fpExpected[word]))
            ++errors;
        if (intActual[word] != intExpected[word])
            ++errors;
    }
    std::cout << "HYBRID_RMW_SCALAR_SOA_RESULT generations=2 "
              << "logical=" << Logical << " errors=" << errors
              << std::endl;
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}
