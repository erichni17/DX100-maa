#include "MAA.hpp"

#include <array>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <vector>

#if !defined(GEM5)
#error "test_virtual_native_rmw requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace {

constexpr int kWordsPerInstruction = 4096;
constexpr int kWordsPerLine = 16;
constexpr int kSourceWords = kWordsPerInstruction * 4;
constexpr int kNativeReaders = 3;
constexpr int kNativeOperations = 4;
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
    std::array<int32_t, kWordsPerInstruction> increments;
    BackingStorage storage;

    for (int i = 0; i < kSourceWords; ++i)
        source[i] = i * 31 + 7;
    for (int i = 0; i < kWordsPerInstruction; ++i) {
        virtual_indices[i] = (i * 97 + 13) % kSourceWords;
        virtual_conditions[i] = i < kWordsPerLine;
        native_indices[i] = i;
        native_conditions[i] = i < kWordsPerLine;
        increments[i] = 1;
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
    add_mem_region(increments.data(), increments.data() + increments.size());
    add_mem_region(storage.data.data(),
                   storage.data.data() + storage.data.size());

    int min_reg = get_new_reg<int>(0);
    int max_reg = get_new_reg<int>(kWordsPerInstruction);
    int stride_reg = get_new_reg<int>(1);
    int virtual_idx_tile = get_new_tile<int>();
    int virtual_cond_tile = get_new_tile<int>();
    int virtual_completion_tile = get_new_tile<int32_t>();
    std::array<int, kNativeOperations> native_idx_tiles;
    std::array<int, kNativeOperations> native_cond_tiles;
    for (int &tile : native_idx_tiles)
        tile = get_new_tile<int>();
    for (int &tile : native_cond_tiles)
        tile = get_new_tile<int>();
    int increment_tile = get_new_tile<int32_t>();
    int rmw_old_tile = get_new_tile<int32_t>();
    std::array<int, kNativeReaders> native_output_tiles = {
        get_new_tile<int32_t>(),
        get_new_tile<int32_t>(),
        get_new_tile<int32_t>(),
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
    maa_stream_load<int32_t>(increments.data(), min_reg, max_reg, stride_reg,
                             increment_tile);
    wait_ready(virtual_idx_tile);
    wait_ready(virtual_cond_tile);
    for (int tile : native_idx_tiles)
        wait_ready(tile);
    for (int tile : native_cond_tiles)
        wait_ready(tile);
    wait_ready(increment_tile);

    maa_indirect_load_virtual<int32_t>(
        source.data(), virtual_idx_tile, virtual_completion_tile,
        storage.data.data(), virtual_cond_tile);

    volatile uint64_t delay_sink = 0;
    for (int i = 0; i < kNativeIssueDelayIterations; ++i)
        delay_sink += static_cast<uint64_t>(i);
    asm volatile("" : : "r"(delay_sink) : "memory");

    // These three requests queue behind the virtual owner. The RMW writeback
    // must complete before either following load is admitted.
    maa_indirect_rmw_vector<int32_t>(
        storage.data.data(), native_idx_tiles[0], increment_tile,
        Operation_t::ADD_OP, native_cond_tiles[0], rmw_old_tile);
    maa_indirect_load<int32_t>(
        storage.data.data(), native_idx_tiles[1], native_output_tiles[0],
        native_cond_tiles[1]);
    maa_indirect_load<int32_t>(
        storage.data.data(), native_idx_tiles[2], native_output_tiles[1],
        native_cond_tiles[2]);

    // Retirement releases the RMW at the FIFO head while two older native
    // requests remain queued. This late request must join the tail.
    wait_ready(virtual_completion_tile);
    maa_indirect_load<int32_t>(
        storage.data.data(), native_idx_tiles[3], native_output_tiles[2],
        native_cond_tiles[3]);

    wait_ready(rmw_old_tile);
    for (int tile : native_output_tiles)
        wait_ready(tile);

    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    int errors = 0;
    int32_t *rmw_old = get_cacheable_tile_pointer<int32_t>(rmw_old_tile);
    std::array<int32_t *, kNativeReaders> native_outputs;
    for (int i = 0; i < kNativeReaders; ++i) {
        native_outputs[i] =
            get_cacheable_tile_pointer<int32_t>(native_output_tiles[i]);
    }
    for (int i = 0; i < kWordsPerInstruction; ++i) {
        const int32_t virtual_value =
            virtual_conditions[i] ? source[virtual_indices[i]] : -7;
        const int32_t final_value =
            native_conditions[i] ? virtual_value + 1 : virtual_value;
        if (storage.data[i] != final_value && errors++ < 10) {
            std::cerr << "backing mismatch[" << i << "]: got "
                      << storage.data[i] << ", expected " << final_value
                      << std::endl;
        }
        if (native_conditions[i] && rmw_old[i] != virtual_value &&
            errors++ < 10) {
            std::cerr << "rmw old mismatch[" << i << "]: got "
                      << rmw_old[i] << ", expected " << virtual_value
                      << std::endl;
        }
        for (int reader = 0;
             reader < kNativeReaders && native_conditions[i]; ++reader) {
            if (native_outputs[reader][i] != final_value &&
                errors++ < 10) {
                std::cerr << "native reader " << reader << " mismatch["
                          << i << "]: got " << native_outputs[reader][i]
                          << ", expected " << final_value << std::endl;
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
              << " pattern=native_rmw_after_virtual errors=" << errors
              << std::endl;
    std::cout << "ROI Ended" << std::endl;
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}
