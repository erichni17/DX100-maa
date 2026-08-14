#include "MAA.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <string>

#if !defined(GEM5)
#error "test_hybrid_rmw_soa requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace {

constexpr int kLogical = 16384;
constexpr int kTargetWords = 1024;
constexpr int kOperations = 2;
constexpr int kOrderIndex = 7;
constexpr int kFalsePredicateIndex = 9;

using LogicalFloat = std::array<float, kLogical>;
using LogicalWord = std::array<uint32_t, kLogical>;
using Target = std::array<float, kTargetWords>;

alignas(64) LogicalFloat values[kOperations];
alignas(64) LogicalWord indices[kOperations];
alignas(64) LogicalWord predicates[kOperations];
alignas(64) Target actual;
alignas(64) Target expected;

uint32_t
bits(float value)
{
    uint32_t result = 0;
    std::memcpy(&result, &value, sizeof(result));
    return result;
}

uint64_t
hashTarget(const Target &target)
{
    uint64_t hash = 1469598103934665603ULL;
    for (float value : target) {
        const uint32_t word = bits(value);
        for (int byte = 0; byte < 4; ++byte) {
            hash ^= static_cast<uint8_t>(word >> (byte * 8));
            hash *= 1099511628211ULL;
        }
    }
    return hash;
}

void
initializeInputs(uint64_t &selected, uint64_t &rejected)
{
    selected = 0;
    rejected = 0;
    for (int word = 0; word < kTargetWords; ++word)
        actual[word] = expected[word] = static_cast<float>((word % 13) - 6);

    for (int operation = 0; operation < kOperations; ++operation) {
        for (int i = 0; i < kLogical; ++i) {
            const int order_slot = i & 63;
            if (order_slot < 4) {
                indices[operation][i] = kOrderIndex;
                static constexpr float ordered_values[4] = {
                    16777216.0F, 1.0F, -16777216.0F, 1.0F};
                values[operation][i] = ordered_values[order_slot];
                predicates[operation][i] = 1;
            } else if (i % 97 == 17) {
                indices[operation][i] = kFalsePredicateIndex;
                values[operation][i] = 1048576.0F + operation;
                predicates[operation][i] = 0;
            } else {
                indices[operation][i] =
                    32 + ((i * 37 + operation * 19) %
                          (kTargetWords - 32));
                values[operation][i] =
                    static_cast<float>(((i * 11 + operation * 7) % 29) - 14) /
                    8.0F;
                predicates[operation][i] =
                    ((i + operation * 3) % 11) != 0;
            }
            if (predicates[operation][i]) {
                expected[indices[operation][i]] += values[operation][i];
                ++selected;
            } else {
                ++rejected;
            }
        }
    }
}

void
runOrdinary()
{
    const int min_reg = get_new_reg<int>(0);
    const int max_reg = get_new_reg<int>(0);
    const int stride_reg = get_new_reg<int>(1);
    const int index_tile = get_new_tile<uint32_t>();
    const int value_tile = get_new_tile<float>();
    const int predicate_tile = get_new_tile<uint32_t>();
    const int old_value_tile = get_new_tile<float>();

    for (int operation = 0; operation < kOperations; ++operation) {
        for (int begin = 0; begin < kLogical; begin += TILE_SIZE) {
            const int end = std::min(begin + TILE_SIZE, kLogical);
            set_reg<int>(min_reg, begin);
            set_reg<int>(max_reg, end);
            maa_stream_load<uint32_t>(indices[operation].data(), min_reg,
                                      max_reg, stride_reg, index_tile);
            maa_stream_load<float>(values[operation].data(), min_reg,
                                   max_reg, stride_reg, value_tile);
            maa_stream_load<uint32_t>(predicates[operation].data(), min_reg,
                                      max_reg, stride_reg, predicate_tile);
            wait_ready(index_tile);
            wait_ready(value_tile);
            wait_ready(predicate_tile);
            maa_indirect_rmw_vector<float>(
                actual.data(), index_tile, value_tile, Operation_t::ADD_OP,
                predicate_tile, old_value_tile);
            wait_ready(old_value_tile);
        }
    }
}

void
runSoa()
{
    const int min_reg = get_new_reg<int>(0);
    const int max_reg = get_new_reg<int>(kLogical);
    const int stride_reg = get_new_reg<int>(1);
    const int completion_tile = get_new_tile<float>();
    for (int operation = 0; operation < kOperations; ++operation) {
        maa_indirect_rmw_vector_soa_jit<float>(
            actual.data(), indices[operation].data(),
            values[operation].data(), predicates[operation].data(),
            min_reg, max_reg, stride_reg, completion_tile,
            Operation_t::ADD_OP);
        wait_ready(completion_tile);
    }
}

} // namespace

int
main(int argc, char **argv)
{
    const std::string mode = argc > 1 ? argv[1] : "soa";
    if (mode != "ordinary" && mode != "soa") {
        std::cerr << "mode must be ordinary or soa" << std::endl;
        return 2;
    }
    if (mode == "soa" && TILE_SIZE != kLogical) {
        std::cerr << "SoA test requires TILE_SIZE=16384" << std::endl;
        return 2;
    }

    uint64_t selected = 0;
    uint64_t rejected = 0;
    initializeInputs(selected, rejected);
    std::cout << "HYBRID_RMW_SOA_LAYOUT mem_size="
              << static_cast<uint64_t>(MEM_SIZE)
              << " logical=" << kLogical << " tile=" << TILE_SIZE
              << " mode=" << mode << std::endl;
    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(actual.data(), actual.data() + actual.size());
    for (int operation = 0; operation < kOperations; ++operation) {
        add_mem_region(indices[operation].data(),
                       indices[operation].data() + kLogical);
        add_mem_region(values[operation].data(),
                       values[operation].data() + kLogical);
        add_mem_region(predicates[operation].data(),
                       predicates[operation].data() + kLogical);
    }

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
    if (mode == "ordinary")
        runOrdinary();
    else
        runSoa();
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    int errors = 0;
    for (int word = 0; word < kTargetWords; ++word) {
        if (bits(actual[word]) == bits(expected[word]))
            continue;
        if (errors++ < 10) {
            std::cerr << "mismatch[" << word << "]: got 0x" << std::hex
                      << bits(actual[word]) << ", expected 0x"
                      << bits(expected[word]) << std::dec << std::endl;
        }
    }
    const uint64_t actual_hash = hashTarget(actual);
    const uint64_t expected_hash = hashTarget(expected);
    if (actual_hash != expected_hash)
        ++errors;
    std::cout << "HYBRID_RMW_SOA_RESULT mode=" << mode
              << " tile=" << TILE_SIZE << " logical=" << kLogical
              << " operations=" << kOperations
              << " selected=" << selected << " rejected=" << rejected
              << " expected_hash=" << expected_hash
              << " output_hash=" << actual_hash
              << " order_word=0x" << std::hex << bits(actual[kOrderIndex])
              << " false_predicate_word=0x"
              << bits(actual[kFalsePredicateIndex]) << std::dec
              << " errors=" << errors << std::endl;
    std::cout << "ROI Ended" << std::endl;
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}
