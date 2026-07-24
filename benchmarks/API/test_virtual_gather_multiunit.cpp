#include "MAA.hpp"

#include <array>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <vector>

#if !defined(GEM5)
#error "test_virtual_gather_multiunit requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace {

constexpr int kWordsPerInstruction = 4096;
constexpr int kWordsPerLine = 16;
constexpr int kSourceWords = kWordsPerInstruction * 4;

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

    std::vector<int32_t> source0(kSourceWords);
    std::vector<int32_t> source1(kSourceWords);
    std::array<int32_t, kWordsPerInstruction> indices0;
    std::array<int32_t, kWordsPerInstruction> indices1;
    std::array<int32_t, kWordsPerInstruction> conditions0;
    std::array<int32_t, kWordsPerInstruction> conditions1;
    BackingStorage storage;

    for (int i = 0; i < kSourceWords; ++i) {
        source0[i] = i * 17 + 3;
        source1[i] = i * 29 + 11;
    }
    for (int i = 0; i < kWordsPerInstruction; ++i) {
        indices0[i] = (i * 97 + 13) % kSourceWords;
        indices1[i] = indices0[i];
        conditions0[i] = i < kWordsPerLine / 2;
        conditions1[i] =
            i >= kWordsPerLine / 2 && i < kWordsPerLine;
    }
    storage.prefix.fill(-7);
    storage.data.fill(-7);
    storage.suffix.fill(-7);

    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();

    clear_mem_region();
    add_mem_region(source0.data(), source0.data() + source0.size());
    add_mem_region(source1.data(), source1.data() + source1.size());
    add_mem_region(indices0.data(), indices0.data() + indices0.size());
    add_mem_region(indices1.data(), indices1.data() + indices1.size());
    add_mem_region(conditions0.data(),
                   conditions0.data() + conditions0.size());
    add_mem_region(conditions1.data(),
                   conditions1.data() + conditions1.size());
    add_mem_region(storage.data.data(),
                   storage.data.data() + storage.data.size());

    int min_reg = get_new_reg<int>(0);
    int max_reg = get_new_reg<int>(kWordsPerInstruction);
    int stride_reg = get_new_reg<int>(1);
    int idx0_tile = get_new_tile<int>();
    int idx1_tile = get_new_tile<int>();
    int cond0_tile = get_new_tile<int>();
    int cond1_tile = get_new_tile<int>();
    int completion0_tile = get_new_tile<int32_t>();
    int completion1_tile = get_new_tile<int32_t>();

    // Give the CPU dirty ownership before both MAA writers target this line.
    storage.data.fill(-1);
    asm volatile("" : : "r"(storage.data.data()) : "memory");
    volatile int64_t residency_sink = 0;
    for (int i = 0; i < kWordsPerLine; ++i) {
        residency_sink += source0[indices0[i]];
        residency_sink += source1[indices1[i]];
    }
    asm volatile("" : : "r"(residency_sink) : "memory");

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);

    maa_stream_load<int>(indices0.data(), min_reg, max_reg, stride_reg,
                         idx0_tile);
    maa_stream_load<int>(indices1.data(), min_reg, max_reg, stride_reg,
                         idx1_tile);
    maa_stream_load<int>(conditions0.data(), min_reg, max_reg, stride_reg,
                         cond0_tile);
    maa_stream_load<int>(conditions1.data(), min_reg, max_reg, stride_reg,
                         cond1_tile);
    wait_ready(idx0_tile);
    wait_ready(idx1_tile);
    wait_ready(cond0_tile);
    wait_ready(cond1_tile);

    maa_indirect_load_virtual<int32_t>(
        source0.data(), idx0_tile, completion0_tile, storage.data.data(),
        cond0_tile);
    maa_indirect_load_virtual<int32_t>(
        source1.data(), idx1_tile, completion1_tile, storage.data.data(),
        cond1_tile);
    wait_ready(completion0_tile);
    wait_ready(completion1_tile);

    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    int errors = 0;
    for (int i = 0; i < kWordsPerInstruction; ++i) {
        int32_t expected = -1;
        if (conditions0[i])
            expected = source0[indices0[i]];
        else if (conditions1[i])
            expected = source1[indices1[i]];
        if (storage.data[i] != expected && errors++ < 10) {
            std::cerr << "mismatch[" << i << "]: got " << storage.data[i]
                      << ", expected " << expected << std::endl;
        }
    }
    for (int i = 0; i < kWordsPerLine; ++i) {
        if (storage.prefix[i] != -7 && errors++ < 10)
            std::cerr << "prefix guard corrupted[" << i << "]" << std::endl;
        if (storage.suffix[i] != -7 && errors++ < 10)
            std::cerr << "suffix guard corrupted[" << i << "]" << std::endl;
    }

    std::cout << "VIRTUAL_GATHER_RESULT n=" << kWordsPerInstruction
              << " pattern=multiunit_same_line errors=" << errors << std::endl;
    std::cout << "ROI Ended" << std::endl;
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}
