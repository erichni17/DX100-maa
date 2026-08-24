#include "MAA.hpp"

#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>

#if !defined(GEM5)
#error "test_hybrid_rmw_old_result requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace
{

constexpr int Logical = 16 * 1024;
constexpr int TargetWords = 64;
constexpr uint32_t RejectedSentinelBits = 0x7fc0a55aU;

alignas(64) std::array<uint32_t, Logical> indices;
alignas(64) std::array<uint32_t, Logical> predicates;
alignas(64) std::array<float, Logical> values;
alignas(64) std::array<float, Logical> oldActual;
alignas(64) std::array<float, Logical> oldExpected;
alignas(64) std::array<float, TargetWords> targetActual;
alignas(64) std::array<float, TargetWords> targetExpected;

uint32_t
bits(float value)
{
    uint32_t result = 0;
    std::memcpy(&result, &value, sizeof(result));
    return result;
}

float
rejectedSentinel()
{
    float value = 0.0F;
    uint32_t payload = RejectedSentinelBits;
    std::memcpy(&value, &payload, sizeof(value));
    return value;
}

void
initialize()
{
    for (int word = 0; word < TargetWords; ++word)
        targetActual[word] = targetExpected[word] =
            static_cast<float>(word - 11);
    for (int logical = 0; logical < Logical; ++logical) {
        // Repeated targets are intentional: each old result must reflect the
        // preceding ordered alias, not merely the line's initial value.
        indices[logical] =
            ((logical / 3) * 13 + (logical % 17 == 0 ? 5 : 0)) %
            TargetWords;
        predicates[logical] = logical % 5 != 0 && logical % 31 != 0;
        values[logical] = static_cast<float>((logical % 7) + 1) * 0.25F;
    }
}

void
prepareExpectedGeneration()
{
    oldActual.fill(rejectedSentinel());
    oldExpected.fill(rejectedSentinel());
    for (int logical = 0; logical < Logical; ++logical) {
        if (!predicates[logical])
            continue;
        const uint32_t index = indices[logical];
        oldExpected[logical] = targetExpected[index];
        targetExpected[index] += values[logical];
    }
}

int
checkGeneration()
{
    int errors = 0;
    for (int logical = 0; logical < Logical; ++logical) {
        if (predicates[logical]) {
            if (bits(oldActual[logical]) != bits(oldExpected[logical]))
                ++errors;
        } else if (bits(oldActual[logical]) != RejectedSentinelBits) {
            ++errors;
        }
    }
    for (int word = 0; word < TargetWords; ++word) {
        if (bits(targetActual[word]) != bits(targetExpected[word]))
            ++errors;
    }
    return errors;
}

} // anonymous namespace

int
main()
{
    static_assert(TILE_SIZE == Logical,
                  "old-result smoke requires a logical 16K tile");
    initialize();
    std::cout << "HYBRID_RMW_OLD_RESULT_LAYOUT logical=" << Logical
              << " physical_runtime=4096 result_backing_bytes="
              << sizeof(oldActual) << std::endl;
    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(targetActual.data(),
                   targetActual.data() + targetActual.size());
    add_mem_region(indices.data(), indices.data() + indices.size());
    add_mem_region(predicates.data(), predicates.data() + predicates.size());
    add_mem_region(values.data(), values.data() + values.size());
    add_mem_region(oldActual.data(), oldActual.data() + oldActual.size());

    const int minimum = get_new_reg<int32_t>(0);
    const int maximum = get_new_reg<int32_t>(Logical);
    const int stride = get_new_reg<int32_t>(1);
    const int completion = get_new_tile<uint32_t>();

    int errors = 0;
    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
    for (int generation = 1; generation <= 2; ++generation) {
        prepareExpectedGeneration();
        maa_indirect_rmw_vector_soa_jit_old_result(
            targetActual.data(), indices.data(), values.data(),
            predicates.data(), oldActual.data(), minimum, maximum, stride,
            completion, Operation_t::ADD_OP);
        wait_ready(completion);
        errors += checkGeneration();
        std::cout << "HYBRID_RMW_OLD_RESULT_GENERATION generation="
                  << generation << " errors=" << errors << std::endl;
    }
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    std::cout << "HYBRID_RMW_OLD_RESULT_RESULT generations=2 logical="
              << Logical << " errors=" << errors << std::endl;
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}
