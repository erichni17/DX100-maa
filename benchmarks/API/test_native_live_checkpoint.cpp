#include "MAA.hpp"

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <vector>

#if !defined(GEM5)
#error "test_native_live_checkpoint requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace {

constexpr double Sentinel = -1.0;

double
sourceValue(int index)
{
    return static_cast<double>(index * 17 + 3) / 8.0;
}

uint32_t
indexValue(int index, int source_elements)
{
    return static_cast<uint32_t>((index * 97 + 13) % source_elements);
}

void
waitForDestinationProgress(const std::vector<double> &output)
{
    volatile const double *values = output.data();
    while (true) {
        for (size_t index = 0; index < output.size(); ++index) {
            if (values[index] != Sentinel)
                return;
        }
    }
}

void
hashDouble(uint64_t &hash, double value)
{
    uint64_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    for (int byte = 0; byte < 8; ++byte) {
        hash ^= (bits >> (byte * 8)) & 0xff;
        hash *= 1099511628211ULL;
    }
}

} // anonymous namespace

int
main(int argc, char **argv)
{
    const int n = argc > 1 ? std::atoi(argv[1]) : TILE_SIZE;
    if (n <= 0 || n > TILE_SIZE) {
        std::cerr << "usage: test_native_live_checkpoint N" << std::endl;
        return 2;
    }

    const int source_elements = n * 4;
    std::vector<double> source(source_elements);
    std::vector<uint32_t> indices(n);
    std::vector<double> output(n, Sentinel);
    for (int i = 0; i < source_elements; ++i)
        source[i] = sourceValue(i);
    for (int i = 0; i < n; ++i)
        indices[i] = indexValue(i, source_elements);

    std::cout << "NATIVE_LIVE_CHECKPOINT_LAYOUT mem_size="
              << static_cast<uint64_t>(MEM_SIZE) << " n=" << n
              << std::endl;
    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(source.data(), source.data() + source.size());
    add_mem_region(indices.data(), indices.data() + indices.size());
    add_mem_region(output.data(), output.data() + output.size());

    const int min_reg = get_new_reg<int>(0);
    const int max_reg = get_new_reg<int>(n);
    const int stride_reg = get_new_reg<int>(1);
    const int scale_reg = get_new_reg<double>(3.0);
    const int index_tile = get_new_tile<uint32_t>();
    const int gather_tile = get_new_tile<double>();
    const int transformed_tile = get_new_tile<double>();

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);

    // This is the unfused native control for the same operation as
    // INDIR_LD_VIRTUAL_SCALAR: index stream load, gather, FP64 scalar ALU,
    // then stream store.  Tile dependencies keep the commands ordered while
    // allowing the ordinary MAA pipeline to overlap their execution.
    maa_stream_load<uint32_t>(indices.data(), min_reg, max_reg, stride_reg,
                              index_tile);
    maa_indirect_load<double>(source.data(), index_tile, gather_tile);
    maa_alu_scalar<double>(gather_tile, scale_reg, transformed_tile,
                           Operation_t::MUL_OP);
    maa_stream_store<double>(output.data(), min_reg, max_reg, stride_reg,
                             transformed_tile);

    waitForDestinationProgress(output);
    // Keep the trigger genuinely live: even a flushed diagnostic write here
    // gives the accelerator enough simulated time to become quiescent before
    // gem5 asks its Drainables for their state.
    m5_checkpoint(0, 0);
    std::cout << "NATIVE_LIVE_DRAIN_RETURNED elements=" << output.size()
              << std::endl;

    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    int errors = 0;
    uint64_t hash = 1469598103934665603ULL;
    for (int i = 0; i < n; ++i) {
        const double expected = source[indices[i]] * 3.0;
        if (output[i] != expected && errors++ < 10) {
            std::cerr << "mismatch[" << i << "]: got " << output[i]
                      << ", expected " << expected << std::endl;
        }
        hashDouble(hash, output[i]);
    }
    std::cout << "NATIVE_LIVE_CHECKPOINT_RESULT n=" << n
              << " errors=" << errors << " hash=" << hash << std::endl;
    std::cout << "ROI Ended" << std::endl;
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}
