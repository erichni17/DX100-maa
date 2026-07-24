#include "MAA.hpp"

#include <array>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <vector>

#if !defined(GEM5)
#error "test_virtual_native_overlap requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace {

constexpr int kWordsPerInstruction = 4096;
constexpr int kWordsPerLine = 16;
constexpr int kSourceWords = kWordsPerInstruction * 4;
#ifndef NATIVE_ISSUE_DELAY_ITERATIONS
#define NATIVE_ISSUE_DELAY_ITERATIONS 5000
#endif
constexpr int kNativeIssueDelayIterations =
    NATIVE_ISSUE_DELAY_ITERATIONS;

struct alignas(64) BackingStorage {
    std::array<int32_t, kWordsPerLine> prefix;
    std::array<int32_t, kWordsPerInstruction> data;
    std::array<int32_t, kWordsPerLine> suffix;
};

} // namespace

int main(int argc, char **argv) {
    const int n = argc > 1 ? std::atoi(argv[1]) : kWordsPerInstruction;
    if (n != kWordsPerInstruction) {
        std::cerr << "n must be " << kWordsPerInstruction << std::endl;
        return 2;
    }

    std::vector<int32_t> source(kSourceWords);
    std::array<int32_t, kWordsPerInstruction> virtual_indices;
    std::array<int32_t, kWordsPerInstruction> virtual_conditions;
    std::array<int32_t, kWordsPerInstruction> native_indices;
    std::array<int32_t, kWordsPerInstruction> native_conditions;
    BackingStorage storage;

    for (int i = 0; i < kSourceWords; ++i)
        source[i] = i * 31 + 7;
    for (int i = 0; i < kWordsPerInstruction; ++i) {
        virtual_indices[i] = (i * 97 + 13) % kSourceWords;
        virtual_conditions[i] = i < kWordsPerLine;
        native_indices[i] = i;
        native_conditions[i] = i < kWordsPerLine;
    }
    storage.prefix.fill(-7);
    storage.data.fill(-7);
    storage.suffix.fill(-7);

    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();

    clear_mem_region();
    add_mem_region(source.data(), source.data() + source.size());
    add_mem_region(virtual_indices.data(),
                   virtual_indices.data() + virtual_indices.size());
    add_mem_region(virtual_conditions.data(),
                   virtual_conditions.data() + virtual_conditions.size());
    add_mem_region(native_indices.data(),
                   native_indices.data() + native_indices.size());
    add_mem_region(native_conditions.data(),
                   native_conditions.data() + native_conditions.size());
    add_mem_region(storage.data.data(),
                   storage.data.data() + storage.data.size());

    int min_reg = get_new_reg<int>(0);
    int max_reg = get_new_reg<int>(kWordsPerInstruction);
    int stride_reg = get_new_reg<int>(1);
    int virtual_idx_tile = get_new_tile<int>();
    int virtual_cond_tile = get_new_tile<int>();
    int virtual_completion_tile = get_new_tile<int32_t>();
    int native_idx_tile = get_new_tile<int>();
    int native_cond_tile = get_new_tile<int>();
    int native_output_tile = get_new_tile<int32_t>();

    // Force a dirty CPU copy so the test also exercises coherent ownership.
    storage.data.fill(-1);
    asm volatile("" : : "r"(storage.data.data()) : "memory");
    volatile int64_t residency_sink = 0;
    for (int i = 0; i < kWordsPerLine; ++i)
        residency_sink += source[virtual_indices[i]];
    asm volatile("" : : "r"(residency_sink) : "memory");

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);

    maa_stream_load<int>(virtual_indices.data(), min_reg, max_reg, stride_reg,
                         virtual_idx_tile);
    maa_stream_load<int>(virtual_conditions.data(), min_reg, max_reg,
                         stride_reg, virtual_cond_tile);
    maa_stream_load<int>(native_indices.data(), min_reg, max_reg, stride_reg,
                         native_idx_tile);
    maa_stream_load<int>(native_conditions.data(), min_reg, max_reg,
                         stride_reg, native_cond_tile);
    wait_ready(virtual_idx_tile);
    wait_ready(virtual_cond_tile);
    wait_ready(native_idx_tile);
    wait_ready(native_cond_tile);

    maa_indirect_load_virtual<int32_t>(
        source.data(), virtual_idx_tile, virtual_completion_tile,
        storage.data.data(), virtual_cond_tile);

    // The test runner holds retirement responses for 32,768 cycles. Keep the
    // CPU active long enough for retirement to acquire the line; the runner's
    // deferral-counter check proves whether the intended overlap occurred.
    volatile uint64_t delay_sink = 0;
    for (int i = 0; i < kNativeIssueDelayIterations; ++i)
        delay_sink += static_cast<uint64_t>(i);
    asm volatile("" : : "r"(delay_sink) : "memory");
    maa_indirect_load<int32_t>(storage.data.data(), native_idx_tile,
                               native_output_tile, native_cond_tile);

    wait_ready(native_output_tile);
    wait_ready(virtual_completion_tile);

    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    int errors = 0;
    int32_t *native_output =
        get_cacheable_tile_pointer<int32_t>(native_output_tile);
    for (int i = 0; i < kWordsPerInstruction; ++i) {
        const int32_t expected =
            virtual_conditions[i] ? source[virtual_indices[i]] : -1;
        if (storage.data[i] != expected && errors++ < 10) {
            std::cerr << "backing mismatch[" << i << "]: got "
                      << storage.data[i] << ", expected " << expected
                      << std::endl;
        }
        if (native_conditions[i] &&
            native_output[i] != expected && errors++ < 10) {
            std::cerr << "native mismatch[" << i << "]: got "
                      << native_output[i] << ", expected " << expected
                      << std::endl;
        }
    }
    for (int i = 0; i < kWordsPerLine; ++i) {
        if (storage.prefix[i] != -7 && errors++ < 10)
            std::cerr << "prefix guard corrupted[" << i << "]" << std::endl;
        if (storage.suffix[i] != -7 && errors++ < 10)
            std::cerr << "suffix guard corrupted[" << i << "]" << std::endl;
    }

    std::cout << "VIRTUAL_GATHER_RESULT n=" << kWordsPerInstruction
              << " pattern=native_after_virtual errors=" << errors
              << std::endl;
    std::cout << "ROI Ended" << std::endl;
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}
