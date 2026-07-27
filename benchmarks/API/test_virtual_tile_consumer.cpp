#include "MAA.hpp"

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

#if !defined(GEM5)
#error "test_virtual_tile_consumer requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace {

constexpr int total_elements = 16384;
constexpr int guard_elements = 32;
constexpr double scale = 3.0;

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
    const std::string mode = argc > 1 ? argv[1] : "native";
    const int page_elements = argc > 2 ? std::atoi(argv[2]) : total_elements;
    if (mode != "native" && mode != "paged") {
        std::cerr << "mode must be native or paged" << std::endl;
        return 2;
    }
    if ((page_elements != 4096 && page_elements != total_elements) ||
        total_elements % page_elements != 0) {
        std::cerr << "page_elements must be 4096 or 16384" << std::endl;
        return 2;
    }
    if (TILE_SIZE != total_elements) {
        std::cerr << "test requires a 16K logical tile" << std::endl;
        return 2;
    }

    std::vector<double> source(total_elements * 8);
    std::vector<uint32_t> indices(total_elements);
    std::vector<double> backing_storage(
        total_elements + 2 * guard_elements, -1.0);
    std::vector<double> destination_storage(
        total_elements + 2 * guard_elements, -1.0);
    std::vector<double> fence_storage(1, 0.0);
    double *backing = backing_storage.data() + guard_elements;
    double *destination = destination_storage.data() + guard_elements;

    for (int i = 0; i < static_cast<int>(source.size()); ++i)
        source[i] = static_cast<double>(i * 17 + 3);
    for (int i = 0; i < total_elements; ++i)
        indices[i] = (i * 97 + 13) % source.size();

    std::cout << "VIRTUAL_TILE_CONSUMER_LAYOUT mode=" << mode
              << " page_elements=" << page_elements
              << " logical_elements=" << TILE_SIZE
              << " mem_size=" << static_cast<uint64_t>(MEM_SIZE)
              << std::endl;
    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(source.data(), source.data() + source.size());
    add_mem_region(indices.data(), indices.data() + indices.size());
    add_mem_region(backing_storage.data(),
                   backing_storage.data() + backing_storage.size());
    add_mem_region(destination_storage.data(),
                   destination_storage.data() + destination_storage.size());
    add_mem_region(fence_storage.data(),
                   fence_storage.data() + fence_storage.size());

    const int min_reg = get_new_reg<int>(0);
    const int max_reg = get_new_reg<int>(total_elements);
    const int stride_reg = get_new_reg<int>(1);
    const int scale_reg = get_new_reg<double>(scale);
    const int output_tile = get_new_tile<double>();

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
    if (mode == "native") {
        const int idx_tile = get_new_tile<uint32_t>();
        const int gathered_tile = get_new_tile<double>();
        for (int offset = 0; offset < total_elements;
             offset += page_elements) {
            const int count = std::min(page_elements,
                                       total_elements - offset);
            wait_ready(idx_tile);
            maa_const(0, min_reg);
            maa_const(count, max_reg);
            maa_stream_load<uint32_t>(
                indices.data() + offset, min_reg, max_reg, stride_reg,
                idx_tile);
            maa_indirect_load<double>(source.data(), idx_tile, gathered_tile);
            maa_alu_scalar<double>(gathered_tile, scale_reg, output_tile,
                                   Operation_t::MUL_OP);
            maa_stream_store<double>(destination + offset, min_reg, max_reg,
                                     stride_reg, output_tile);
        }
    } else {
        const int completion_tile = get_new_tile<double>();
        const int page_tile = get_new_tile<double>();

        maa_const(0, min_reg);
        maa_const(total_elements, max_reg);
        maa_indirect_load_virtual_index<double>(
            source.data(), indices.data(), completion_tile, backing,
            min_reg, max_reg, stride_reg);
        wait_ready(completion_tile);

        for (int offset = 0; offset < total_elements;
             offset += page_elements) {
            const int count = std::min(page_elements,
                                       total_elements - offset);
            wait_ready(page_tile);
            maa_const(0, min_reg);
            maa_const(count, max_reg);
            maa_stream_load<double>(backing + offset, min_reg, max_reg,
                                    stride_reg, page_tile);
            maa_alu_scalar<double>(page_tile, scale_reg, output_tile,
                                   Operation_t::MUL_OP);
            maa_stream_store<double>(destination + offset, min_reg, max_reg,
                                     stride_reg, output_tile);
        }
    }

    // A source tile becomes ready before its stream store finishes. Reusing it
    // as a destination creates a dependency fence that includes final stores.
    maa_const(0, min_reg);
    maa_const(1, max_reg);
    maa_stream_load<double>(fence_storage.data(), min_reg, max_reg, stride_reg,
                            output_tile);
    wait_ready(output_tile);
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    int errors = 0;
    uint64_t hash = 1469598103934665603ULL;
    for (int i = 0; i < total_elements; ++i) {
        const double gathered = source[indices[i]];
        const double expected = gathered * scale;
        if (destination[i] != expected && errors++ < 10) {
            std::cerr << "destination mismatch[" << i << "]: got "
                      << destination[i] << ", expected " << expected
                      << std::endl;
        }
        if (mode == "paged" && backing[i] != gathered && errors++ < 10) {
            std::cerr << "backing mismatch[" << i << "]: got "
                      << backing[i] << ", expected " << gathered << std::endl;
        }
        hash = hashValue(hash, destination[i]);
    }
    for (int i = 0; i < guard_elements; ++i) {
        const int suffix = guard_elements + total_elements + i;
        if (backing_storage[i] != -1.0 && errors++ < 10)
            std::cerr << "backing prefix guard corrupted[" << i << "]"
                      << std::endl;
        if (backing_storage[suffix] != -1.0 && errors++ < 10)
            std::cerr << "backing suffix guard corrupted[" << i << "]"
                      << std::endl;
        if (destination_storage[i] != -1.0 && errors++ < 10)
            std::cerr << "destination prefix guard corrupted[" << i << "]"
                      << std::endl;
        if (destination_storage[suffix] != -1.0 && errors++ < 10)
            std::cerr << "destination suffix guard corrupted[" << i << "]"
                      << std::endl;
    }

    std::cout << "VIRTUAL_TILE_CONSUMER_RESULT mode=" << mode
              << " page_elements=" << page_elements << " hash=" << hash
              << " errors=" << errors << std::endl;
    std::cout << "ROI Ended" << std::endl;
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}
