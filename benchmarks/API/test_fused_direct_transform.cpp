#include "MAA.hpp"

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

#if !defined(GEM5)
#error "test_fused_direct_transform requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

int main(int argc, char **argv) {
    const int n = argc > 1 ? std::atoi(argv[1]) : 4097;
    const std::string mode = argc > 2 ? argv[2] : "exact";
    if (n <= 0 || n > TILE_SIZE ||
        (mode != "exact" && mode != "alias" && mode != "drain" &&
         mode != "reset")) {
        std::cerr <<
            "usage: test_fused_direct_transform N exact|alias|drain|reset"
                  << std::endl;
        return 2;
    }

    std::vector<double> source(n * 4);
    std::vector<uint32_t> indices(n);
    std::vector<double> output(n, -1.0);
    for (int i = 0; i < static_cast<int>(source.size()); ++i)
        source[i] = static_cast<double>(i * 17 + 3) / 8.0;
    for (int i = 0; i < n; ++i)
        indices[i] = (i * 97 + 13) % source.size();
    double *destination =
        mode == "alias" ? source.data() : output.data();

    std::cout << "VIRTUAL_GATHER64_LAYOUT mem_size="
              << static_cast<uint64_t>(MEM_SIZE) << " mode=" << mode
              << std::endl;
    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(source.data(), source.data() + source.size());
    add_mem_region(indices.data(), indices.data() + indices.size());
    if (mode != "alias")
        add_mem_region(output.data(), output.data() + output.size());

    const int min_reg = get_new_reg<int>(0);
    const int max_reg = get_new_reg<int>(n);
    const int stride_reg = get_new_reg<int>(1);
    const int scale_reg = get_new_reg<double>(3.0);
    const int index_tile = get_new_tile<int>();
    // The direct-sink result has no SPD payload.  One 32-bit tile ID remains
    // solely as the software-visible completion token.
    const int completion_tile = get_new_tile<uint32_t>();

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
    maa_stream_load<uint32_t>(indices.data(), min_reg, max_reg, stride_reg,
                              index_tile);
    maa_indirect_load_virtual_scalar<double>(
        source.data(), indices.data(), index_tile, completion_tile,
        destination, scale_reg, Operation_t::MUL_OP);
    if (mode == "drain") {
        std::cout << "FUSED_DIRECT_LIVE_DRAIN_REQUEST" << std::endl;
        m5_checkpoint(0, 0);
        std::cerr << "live fused checkpoint unexpectedly returned"
                  << std::endl;
        return 3;
    }
    if (mode == "reset") {
        std::cout << "FUSED_DIRECT_LIVE_RESET_REQUEST" << std::endl;
        m5_reset_stats(0, 0);
        std::cerr << "live fused stats reset unexpectedly returned"
                  << std::endl;
        return 3;
    }
    wait_ready(completion_tile);
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    int errors = 0;
    uint64_t hash = 1469598103934665603ULL;
    for (int i = 0; i < n; ++i) {
        const double expected = source[indices[i]] * 3.0;
        if (destination[i] != expected && errors++ < 10)
            std::cerr << "mismatch[" << i << "]: got " << destination[i]
                      << ", expected " << expected << std::endl;
        uint64_t bits;
        std::memcpy(&bits, destination + i, sizeof(bits));
        for (int byte = 0; byte < 8; ++byte) {
            hash ^= (bits >> (byte * 8)) & 0xff;
            hash *= 1099511628211ULL;
        }
    }
    std::cout << "VIRTUAL_GATHER64_RESULT n=" << n << " pattern=" << mode
              << " errors=" << errors << " hash=" << hash << std::endl;
    std::cout << "ROI Ended" << std::endl;
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}
