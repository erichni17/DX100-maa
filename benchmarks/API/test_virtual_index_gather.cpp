#include "MAA.hpp"

#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>

#if !defined(GEM5)
#error "test_virtual_index_gather requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

int main(int argc, char **argv) {
    const int n = argc > 1 ? std::atoi(argv[1]) : TILE_SIZE;
    const std::string pattern = argc > 2 ? argv[2] : "random";
    if (n <= 0 || n > TILE_SIZE) {
        std::cerr << "n must be in (0, TILE_SIZE]" << std::endl;
        return 2;
    }
    if (pattern != "random" && pattern != "fanout" &&
        pattern != "same_line" && pattern != "line_revisit") {
        std::cerr << "pattern must be random, fanout, same_line, or "
                  << "line_revisit" << std::endl;
        return 2;
    }

    // The retry pattern must exceed the 8K-line Row-Table capacity so it
    // exercises drain/refill while a repeated source line is outstanding.
    const int source_elements = pattern == "line_revisit" ? n * 32 : n * 4;
    std::vector<int32_t> source_storage(source_elements + 16);
    const uintptr_t source_addr =
        (reinterpret_cast<uintptr_t>(source_storage.data()) + 63) &
        ~uintptr_t(63);
    int32_t *source = reinterpret_cast<int32_t *>(source_addr);
    std::vector<uint32_t> indices(n);
    std::vector<int32_t> backing_storage(n + 64, -1);
    constexpr int guard_words = 32;
    int32_t *backing = backing_storage.data() + guard_words;

    for (int i = 0; i < source_elements; ++i)
        source[i] = i * 17 + 3;
    for (int i = 0; i < n; ++i) {
        if (pattern == "fanout") {
            indices[i] = 13;
        } else if (pattern == "same_line") {
            indices[i] = static_cast<uint32_t>((i * 5 + 3) % 16);
        } else if (pattern == "line_revisit" && i % 64 == 0) {
            indices[i] = 13;
        } else {
            indices[i] =
                static_cast<uint32_t>((i * 97 + 13) % source_elements);
        }
    }

    std::cout << "VIRTUAL_GATHER_LAYOUT mem_size="
              << static_cast<uint64_t>(MEM_SIZE) << std::endl;
    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(source, source + source_elements);
    add_mem_region(indices.data(), indices.data() + indices.size());
    add_mem_region(backing_storage.data(),
                   backing_storage.data() + backing_storage.size());

    int min_reg = get_new_reg<int>(0);
    int max_reg = get_new_reg<int>(n);
    int stride_reg = get_new_reg<int>(1);
    int completion_tile = get_new_tile<int32_t>();

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
    maa_indirect_load_virtual_index<int32_t>(
        source, indices.data(), completion_tile, backing,
        min_reg, max_reg, stride_reg);
    wait_ready(completion_tile);
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    int errors = 0;
    uint64_t hash = 1469598103934665603ULL;
    for (int i = 0; i < n; ++i) {
        const int32_t expected = source[indices[i]];
        if (backing[i] != expected && errors++ < 10) {
            std::cerr << "mismatch[" << i << "]: got " << backing[i]
                      << ", expected " << expected << std::endl;
        }
        const uint32_t value = static_cast<uint32_t>(backing[i]);
        for (unsigned byte = 0; byte < sizeof(value); ++byte) {
            hash ^= (value >> (byte * 8)) & 0xff;
            hash *= 1099511628211ULL;
        }
    }
    for (int i = 0; i < guard_words; ++i) {
        if (backing_storage[i] != -1 && errors++ < 10)
            std::cerr << "prefix guard corrupted[" << i << "]" << std::endl;
    }
    for (int i = guard_words + n;
         i < static_cast<int>(backing_storage.size()); ++i) {
        if (backing_storage[i] != -1 && errors++ < 10)
            std::cerr << "suffix guard corrupted[" << i << "]" << std::endl;
    }

    std::cout << "VIRTUAL_GATHER_RESULT n=" << n
              << " pattern=" << pattern << " hash=" << hash
              << " errors=" << errors
              << std::endl;
    std::cout << "ROI Ended" << std::endl;
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}
