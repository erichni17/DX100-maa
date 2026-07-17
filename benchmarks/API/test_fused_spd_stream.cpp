#include "MAA.hpp"

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <vector>

#if !defined(GEM5)
#error "test_fused_spd_stream requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

int
main()
{
    constexpr int iterations = 4;
    constexpr int length = iterations * TILE_SIZE;
    constexpr int source_length = 8 * TILE_SIZE;

    std::vector<double> arena(source_length + length, -1.0);
    std::vector<int32_t> indices(length);
    double *source = arena.data();
    double *destination = source + source_length;

    for (int i = 0; i < source_length; ++i)
        source[i] = static_cast<double>(i * 17 + 3);
    for (int i = 0; i < length; ++i)
        indices[i] = (i * 97 + 13) % source_length;

    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(arena.data(), arena.data() + arena.size());
    add_mem_region(indices.data(), indices.data() + indices.size());

    const int min_reg = get_new_reg<int>(0);
    const int max_reg = get_new_reg<int>(length);
    const int stride_reg = get_new_reg<int>(1);
    const int idx_tile = get_new_tile<int32_t>();
    const int dst_tile = get_new_tile<double>();

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);

    for (int offset = 0; offset < length; offset += TILE_SIZE) {
        maa_const(offset, min_reg);
        maa_stream_load<int32_t>(
            indices.data(), min_reg, max_reg, stride_reg, idx_tile);
        maa_indirect_load_spd_stream<double>(
            source, idx_tile, dst_tile, destination,
            min_reg, max_reg, stride_reg);
        wait_ready(dst_tile);
    }

    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    int errors = 0;
    uint64_t hash = 1469598103934665603ULL;
    for (int i = 0; i < length; ++i) {
        const double expected = source[indices[i]];
        if (destination[i] != expected && errors++ < 10) {
            std::cerr << "mismatch[" << i << "]: got " << destination[i]
                      << ", expected " << expected << std::endl;
        }
        uint64_t bits;
        std::memcpy(&bits, &destination[i], sizeof(bits));
        hash ^= bits;
        hash *= 1099511628211ULL;
    }

    if (errors != 0) {
        std::cerr << "FUSED_SPD_STREAM_FAIL length=" << length
                  << " hash=" << hash << " errors=" << errors << std::endl;
        std::abort();
    }

    std::cout << "FUSED_SPD_STREAM_RESULT length=" << length
              << " hash=" << hash << " errors=0" << std::endl;
    m5_exit(0);
    return 0;
}
