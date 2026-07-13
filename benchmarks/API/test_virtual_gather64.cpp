#include "MAA.hpp"

#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>

#if !defined(GEM5)
#error "test_virtual_gather64 requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

int main(int argc, char **argv) {
    const int n = argc > 1 ? std::atoi(argv[1]) : 4096;
    const std::string pattern = argc > 2 ? argv[2] : "random";
    if (n <= 0 || n > TILE_SIZE) {
        std::cerr << "n must be in (0, TILE_SIZE]" << std::endl;
        return 2;
    }

    std::vector<int64_t> source(n * 4);
    std::vector<int32_t> indices(n);
    std::vector<int64_t> backing_storage(n + 1024, -1);
    int64_t *backing = backing_storage.data() + (pattern == "page" ? 509 : 0);
    for (int i = 0; i < static_cast<int>(source.size()); ++i)
        source[i] = i * 17 + 3;
    for (int i = 0; i < n; ++i) {
        if (pattern == "fanout")
            indices[i] = 13;
        else
            indices[i] = (i * 97 + 13) % source.size();
    }
    if (pattern != "random" && pattern != "fanout" && pattern != "page" &&
        pattern != "native") {
        std::cerr << "pattern must be random, fanout, page, or native"
                  << std::endl;
        return 2;
    }

    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();

    clear_mem_region();
    add_mem_region(source.data(), source.data() + source.size());
    add_mem_region(indices.data(), indices.data() + indices.size());
    add_mem_region(backing, backing + n);

    int min_reg = get_new_reg<int>(0);
    int max_reg = get_new_reg<int>(n);
    int stride_reg = get_new_reg<int>(1);
    int idx_tile = get_new_tile<int>();
    int completion_tile = get_new_tile<int64_t>();

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);

    maa_stream_load<int>(indices.data(), min_reg, max_reg, stride_reg, idx_tile);
    if (pattern == "native")
        maa_indirect_load<int64_t>(source.data(), idx_tile, completion_tile);
    else
        maa_indirect_load_virtual<int64_t>(source.data(), idx_tile,
                                           completion_tile, backing);
    wait_ready(completion_tile);

    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    int errors = 0;
    int64_t *result = pattern == "native"
                          ? get_cacheable_tile_pointer<int64_t>(completion_tile)
                          : backing;
    for (int i = 0; i < n; ++i) {
        const int64_t expected = source[indices[i]];
        if (result[i] != expected && errors++ < 10)
            std::cerr << "mismatch[" << i << "]: got " << result[i]
                      << ", expected " << expected << std::endl;
    }
    std::cout << "VIRTUAL_GATHER64_RESULT n=" << n << " pattern=" << pattern
              << " errors=" << errors
              << std::endl;
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}
