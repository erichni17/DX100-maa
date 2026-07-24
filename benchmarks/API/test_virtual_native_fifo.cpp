#include "MAA.hpp"

#include <array>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <vector>

#if !defined(GEM5)
#error "test_virtual_native_fifo requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace {

constexpr int kWordsPerInstruction = 4096;
constexpr int kWordsPerLine = 16;
constexpr int kSourceWords = kWordsPerInstruction * 4;
constexpr int kNativeReaders = 5;
constexpr int kNativeIssueDelayIterations = 5000;

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
    std::array<int, kNativeReaders> native_idx_tiles;
    std::array<int, kNativeReaders> native_cond_tiles;
    for (int &tile : native_idx_tiles)
        tile = get_new_tile<int>();
    for (int &tile : native_cond_tiles)
        tile = get_new_tile<int>();
    std::array<int, kNativeReaders> native_output_tiles = {
        get_new_tile<int32_t>(),
        get_new_tile<int32_t>(),
        get_new_tile<int32_t>(),
        get_new_tile<int32_t>(),
        virtual_completion_tile,
    };

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);

    maa_stream_load<int>(virtual_indices.data(), min_reg, max_reg, stride_reg,
                         virtual_idx_tile);
    maa_stream_load<int>(virtual_conditions.data(), min_reg, max_reg,
                         stride_reg, virtual_cond_tile);
    for (int tile : native_idx_tiles) {
        maa_stream_load<int>(native_indices.data(), min_reg, max_reg,
                             stride_reg, tile);
    }
    for (int tile : native_cond_tiles) {
        maa_stream_load<int>(native_conditions.data(), min_reg, max_reg,
                             stride_reg, tile);
    }
    wait_ready(virtual_idx_tile);
    wait_ready(virtual_cond_tile);
    for (int tile : native_idx_tiles)
        wait_ready(tile);
    for (int tile : native_cond_tiles)
        wait_ready(tile);

    maa_indirect_load_virtual<int32_t>(
        source.data(), virtual_idx_tile, virtual_completion_tile,
        storage.data.data(), virtual_cond_tile);

    volatile uint64_t delay_sink = 0;
    for (int i = 0; i < kNativeIssueDelayIterations; ++i)
        delay_sink += static_cast<uint64_t>(i);
    asm volatile("" : : "r"(delay_sink) : "memory");

    // The first four reads queue behind the virtual owner. The fifth is held
    // in the IF by a destination WAW on the completion tile, then admitted
    // immediately after virtual completion while the older FIFO tail remains.
    for (int i = 0; i < kNativeReaders; ++i) {
        maa_indirect_load<int32_t>(
            storage.data.data(), native_idx_tiles[i],
            native_output_tiles[i], native_cond_tiles[i]);
    }

    for (int tile : native_output_tiles)
        wait_ready(tile);

    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    int errors = 0;
    std::array<int32_t *, kNativeReaders> native_outputs;
    for (int i = 0; i < kNativeReaders; ++i) {
        native_outputs[i] =
            get_cacheable_tile_pointer<int32_t>(native_output_tiles[i]);
    }
    for (int i = 0; i < kWordsPerInstruction; ++i) {
        const int32_t virtual_value =
            virtual_conditions[i] ? source[virtual_indices[i]] : -7;
        if (storage.data[i] != virtual_value && errors++ < 10) {
            std::cerr << "backing mismatch[" << i << "]: got "
                      << storage.data[i] << ", expected " << virtual_value
                      << std::endl;
        }
    }
    // Scan each tile contiguously so the stride prefetcher stays in range.
    for (int reader = 0; reader < kNativeReaders; ++reader) {
        for (int i = 0;
             i < kWordsPerInstruction && native_conditions[i]; ++i) {
            const int32_t virtual_value =
                virtual_conditions[i] ? source[virtual_indices[i]] : -7;
            if (native_outputs[reader][i] != virtual_value &&
                errors++ < 10) {
                std::cerr << "native reader " << reader << " mismatch["
                          << i << "]: got " << native_outputs[reader][i]
                          << ", expected " << virtual_value << std::endl;
            }
        }
    }
    for (int i = 0; i < kWordsPerLine; ++i) {
        if (storage.prefix[i] != -7 && errors++ < 10)
            std::cerr << "prefix guard corrupted[" << i << "]" << std::endl;
        if (storage.suffix[i] != -7 && errors++ < 10)
            std::cerr << "suffix guard corrupted[" << i << "]" << std::endl;
    }

    std::cout << "VIRTUAL_GATHER_RESULT n=" << kWordsPerInstruction
              << " pattern=native_fifo_depth errors=" << errors << std::endl;
    std::cout << "ROI Ended" << std::endl;
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}
