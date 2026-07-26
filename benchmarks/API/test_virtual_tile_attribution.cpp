#include "MAA.hpp"

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

#if !defined(GEM5)
#error "test_virtual_tile_attribution requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace {

constexpr int total_elements = 16384;
constexpr int guard_elements = 32;

uint64_t
hashValue(uint64_t hash, double value)
{
    uint64_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    hash ^= bits;
    return hash * 1099511628211ULL;
}

} // namespace

int
main(int argc, char **argv)
{
    const std::string mode = argc > 1 ? argv[1] : "native_fused";
    if (mode != "native_unfused" && mode != "native_fused" &&
        mode != "virtual_index") {
        std::cerr << "mode must be native_unfused, native_fused, or "
                  << "virtual_index" << std::endl;
        return 2;
    }
    if (mode == "virtual_index" && TILE_SIZE != total_elements) {
        std::cerr << "virtual_index requires a 16K logical tile" << std::endl;
        return 2;
    }
    if (total_elements % TILE_SIZE != 0) {
        std::cerr << "TILE_SIZE must divide 16384" << std::endl;
        return 2;
    }

    std::vector<double> source(total_elements * 8);
    std::vector<uint32_t> indices(total_elements);
    std::vector<double> destination_storage(
        total_elements + 2 * guard_elements, -1.0);
    double *destination = destination_storage.data() + guard_elements;

    for (int i = 0; i < static_cast<int>(source.size()); ++i)
        source[i] = static_cast<double>(i * 17 + 3);
    for (int i = 0; i < total_elements; ++i)
        indices[i] = (i * 97 + 13) % source.size();

    std::cout << "VIRTUAL_TILE_ATTRIBUTION_LAYOUT tile_size=" << TILE_SIZE
              << " total_elements=" << total_elements
              << " mem_size=" << static_cast<uint64_t>(MEM_SIZE)
              << std::endl;
    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(source.data(), source.data() + source.size());
    add_mem_region(indices.data(), indices.data() + indices.size());
    add_mem_region(destination_storage.data(),
                   destination_storage.data() + destination_storage.size());

    const int min_reg = get_new_reg<int>(0);
    const int max_reg = get_new_reg<int>(total_elements);
    const int stride_reg = get_new_reg<int>(1);
    const int idx_tile = mode == "virtual_index"
        ? -1
        : get_new_tile<uint32_t>();
    const int dst_tile = get_new_tile<double>();

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
    if (mode == "virtual_index") {
        maa_indirect_load_virtual_index<double>(
            source.data(), indices.data(), dst_tile, destination,
            min_reg, max_reg, stride_reg);
        wait_ready(dst_tile);
    } else {
        for (int offset = 0; offset < total_elements; offset += TILE_SIZE) {
            maa_const(offset, min_reg);
            maa_stream_load<uint32_t>(
                indices.data(), min_reg, max_reg, stride_reg, idx_tile);
            if (mode == "native_fused") {
                maa_indirect_load_spd_stream<double>(
                    source.data(), idx_tile, dst_tile, destination,
                    min_reg, max_reg, stride_reg);
            } else {
                maa_indirect_load<double>(source.data(), idx_tile, dst_tile);
                maa_stream_store<double>(
                    destination, min_reg, max_reg, stride_reg, dst_tile);
            }
            wait_ready(dst_tile);
        }
    }
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    int errors = 0;
    uint64_t hash = 1469598103934665603ULL;
    for (int i = 0; i < total_elements; ++i) {
        const double expected = source[indices[i]];
        if (destination[i] != expected && errors++ < 10) {
            std::cerr << "mismatch[" << i << "]: got " << destination[i]
                      << ", expected " << expected << std::endl;
        }
        hash = hashValue(hash, destination[i]);
    }
    for (int i = 0; i < guard_elements; ++i) {
        if (destination_storage[i] != -1.0 && errors++ < 10)
            std::cerr << "prefix guard corrupted[" << i << "]" << std::endl;
        const int suffix = guard_elements + total_elements + i;
        if (destination_storage[suffix] != -1.0 && errors++ < 10)
            std::cerr << "suffix guard corrupted[" << i << "]" << std::endl;
    }

    std::cout << "VIRTUAL_TILE_ATTRIBUTION_RESULT mode=" << mode
              << " tile_size=" << TILE_SIZE
              << " total_elements=" << total_elements
              << " hash=" << hash << " errors=" << errors << std::endl;
    std::cout << "ROI Ended" << std::endl;
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}
