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
constexpr int TargetRows = 128;
constexpr int RowStrideWords = 65536;
constexpr int TargetSpanWords =
    (TargetRows - 1) * RowStrideWords + 1;
constexpr uint32_t RejectedSentinelBits = 0x7fc0a55aU;
constexpr uint64_t ExpectedResultHash = 16970917775049394563ULL;

alignas(64) std::array<uint32_t, Logical> indices;
alignas(64) std::array<uint32_t, Logical> predicates;
alignas(64) std::array<float, Logical> values;
alignas(64) std::array<float, Logical> oldActual;
alignas(64) std::array<float, Logical> oldExpected;
alignas(64) std::array<float, TargetSpanWords> targetActual;
alignas(64) std::array<float, TargetSpanWords> targetExpected;

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
    targetActual.fill(0.0F);
    targetExpected.fill(0.0F);
    for (int target = 0; target < TargetRows; ++target) {
        const int word = target * RowStrideWords;
        targetActual[word] = targetExpected[word] =
            static_cast<float>(target - TargetRows / 2);
    }
    for (int logical = 0; logical < Logical; ++logical) {
        // 128 addresses in one bank/slice advance by 262144 bytes each,
        // exceeding the 64 row entries in the original 2-channel/32-slice
        // table. Repeated passes over each target make old-result order
        // observable across the forced drain and the second generation.
        indices[logical] = (logical % TargetRows) * RowStrideWords;
        predicates[logical] = 1;
        values[logical] = 1.0F;
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
    for (int target = 0; target < TargetRows; ++target) {
        const int word = target * RowStrideWords;
        if (bits(targetActual[word]) != bits(targetExpected[word]))
            ++errors;
    }
    return errors;
}

uint64_t
resultHash()
{
    uint64_t hash = 1469598103934665603ULL;
    const auto fold = [&hash](uint32_t word) {
        hash ^= word;
        hash *= 1099511628211ULL;
    };
    for (int logical = 0; logical < Logical; ++logical)
        fold(bits(oldActual[logical]));
    for (int target = 0; target < TargetRows; ++target)
        fold(bits(targetActual[target * RowStrideWords]));
    return hash;
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
              << sizeof(oldActual) << " target_rows=" << TargetRows
              << " row_stride_bytes=" << RowStrideWords * sizeof(float)
              << std::endl;
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

    const uint64_t hash = resultHash();
    if (hash != ExpectedResultHash)
        ++errors;
    std::cout << "HYBRID_RMW_OLD_RESULT_RESULT generations=2 logical="
              << Logical << " result_hash=" << hash
              << " errors=" << errors << std::endl;
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}
