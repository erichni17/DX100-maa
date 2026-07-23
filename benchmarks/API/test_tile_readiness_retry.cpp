#include "MAA.hpp"

#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <vector>

#if !defined(GEM5)
#error "test_tile_readiness_retry requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

int
main()
{
    std::vector<int32_t> source(TILE_SIZE);
    for (int i = 0; i < TILE_SIZE; ++i)
        source[i] = i * 17 + 3;

    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(source.data(), source.data() + source.size());

    const int min_reg = get_new_reg<int>(0);
    const int max_reg = get_new_reg<int>(TILE_SIZE);
    const int stride_reg = get_new_reg<int>(1);
    const int dst_tile = get_new_tile<int32_t>();
    volatile int32_t *result =
        get_cacheable_tile_pointer<volatile int32_t>(dst_tile);

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);

    // Deliberately omit wait_ready(): the first demand load must be retried
    // until the asynchronous stream fill makes the destination tile ready.
    maa_stream_load<int32_t>(
        source.data(), min_reg, max_reg, stride_reg, dst_tile);

    int errors = 0;
    uint64_t sum = 0;
    uint64_t hash = 1469598103934665603ULL;
    for (int i = 0; i < TILE_SIZE; ++i) {
        const uint32_t value = static_cast<uint32_t>(result[i]);
        const uint32_t expected = static_cast<uint32_t>(source[i]);
        if (value != expected && errors++ < 10) {
            std::cerr << "mismatch[" << i << "]: got " << value
                      << ", expected " << expected << std::endl;
        }
        sum += value;
        for (unsigned byte = 0; byte < sizeof(value); ++byte) {
            hash ^= (value >> (byte * 8)) & 0xff;
            hash *= 1099511628211ULL;
        }
    }

    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    std::cout << "TILE_READINESS_RETRY_RESULT elements=" << TILE_SIZE
              << " sum=" << sum << " hash=" << hash
              << " errors=" << errors << std::endl;
    std::cout << "ROI Ended" << std::endl;
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}
