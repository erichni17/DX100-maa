#include "MAA.hpp"

#include <array>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <string>

#if !defined(GEM5)
#error "test_backed_rmw_reorder requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace {

constexpr int Logical = 16384;
constexpr int Physical = TILE_SIZE;
constexpr uint32_t Generation = 7;
constexpr size_t DescriptorSlotBytes = 3 * 4096 * 6;
constexpr size_t DescriptorUnits = 4;

alignas(64) std::array<int32_t, Logical> data;
alignas(64) std::array<int32_t, Logical> indices;
alignas(64) std::array<int32_t, Logical> values;
alignas(64) std::array<int32_t, Logical> predicates;
alignas(64) std::array<MAAIndirectRmwRecord, Logical> records;
alignas(64) std::array<uint8_t,
                       DescriptorSlotBytes * DescriptorUnits> descriptors;

uint64_t hashData()
{
    uint64_t hash = 1469598103934665603ULL;
    for (const int32_t value : data) {
        uint32_t bits;
        std::memcpy(&bits, &value, sizeof(bits));
        for (unsigned byte = 0; byte < sizeof(bits); ++byte) {
            hash ^= static_cast<uint8_t>(bits >> (byte * 8));
            hash *= 1099511628211ULL;
        }
    }
    return hash;
}

} // namespace

int main(int argc, char **argv)
{
    const std::string mode = argc > 1 ? argv[1] : "backed";
    if (mode != "native" && mode != "backed") {
        std::cerr << "usage: test_backed_rmw_reorder native|backed\n";
        return 2;
    }

    data.fill(100);
    descriptors.fill(0);
    for (int i = 0; i < Logical; ++i) {
        indices[i] = (i * 8191 + 37) & (Logical - 1);
        values[i] = i % 17 + 1;
        predicates[i] = (i % 5) != 0;
    }

    m5_checkpoint(0, 0);
    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(data.data(), data.data() + data.size());
    add_mem_region(indices.data(), indices.data() + indices.size());
    add_mem_region(values.data(), values.data() + values.size());
    add_mem_region(predicates.data(), predicates.data() + predicates.size());
    add_mem_region(records.data(), records.data() + records.size());
    add_mem_region(descriptors.data(),
                   descriptors.data() + descriptors.size());

    const int min_reg = get_new_reg<int>(0);
    const int max_reg = get_new_reg<int>(Logical);
    const int stride_reg = get_new_reg<int>(1);
    const int page_min_reg = get_new_reg<int>(0);
    const int page_max_reg = get_new_reg<int>(Physical);
    const int page_stride_reg = get_new_reg<int>(1);
    const int idx_tile = get_new_tile<int32_t>();
    const int value_tile = get_new_tile<int32_t>();
    const int predicate_tile = get_new_tile<int32_t>();
    const int completion_tile = get_new_tile<int32_t>();

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);

    if (mode == "backed") {
        // Publication is deliberately inside the ROI. These are ordinary
        // guest CPU stores, not host-side setup or zero-time simulator work.
        for (int page = 0; page < Logical; page += 4096) {
            for (int i = page; i < page + 4096; ++i) {
                records[i].index = static_cast<uint32_t>(indices[i]);
                records[i].predicate =
                    static_cast<uint32_t>(predicates[i]);
                records[i].generation = Generation;
                records[i].reserved0 = 0;
                uint32_t bits;
                std::memcpy(&bits, &values[i], sizeof(bits));
                records[i].value_bits = bits;
                records[i].reserved1 = 0;
            }
            std::atomic_thread_fence(std::memory_order_release);
        }
        maa_indirect_rmw_vector_backed<int32_t>(
            data.data(), records.data(), descriptors.data(),
            completion_tile, min_reg, max_reg, stride_reg,
            Operation_t::ADD_OP);
        wait_ready(completion_tile);
    } else {
        for (int offset = 0; offset < Logical; offset += Physical) {
            maa_stream_load<int32_t>(indices.data() + offset,
                                     page_min_reg, page_max_reg,
                                     page_stride_reg, idx_tile);
            maa_stream_load<int32_t>(values.data() + offset,
                                     page_min_reg, page_max_reg,
                                     page_stride_reg, value_tile);
            maa_stream_load<int32_t>(predicates.data() + offset,
                                     page_min_reg, page_max_reg,
                                     page_stride_reg, predicate_tile);
            wait_ready(idx_tile);
            wait_ready(value_tile);
            wait_ready(predicate_tile);
            maa_indirect_rmw_vector<int32_t>(
                data.data(), idx_tile, value_tile, Operation_t::ADD_OP,
                predicate_tile, completion_tile);
            wait_ready(completion_tile);
        }
    }

    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    int errors = 0;
    std::array<int32_t, Logical> expected;
    expected.fill(100);
    for (int i = 0; i < Logical; ++i) {
        if (predicates[i])
            expected[indices[i]] += values[i];
    }
    for (int i = 0; i < Logical; ++i) {
        if (data[i] != expected[i] && errors++ < 10) {
            std::cerr << "mismatch[" << i << "]: got " << data[i]
                      << ", expected " << expected[i] << '\n';
        }
    }
    std::cout << "BACKED_RMW_RESULT mode=" << mode
              << " logical=" << Logical << " physical=" << Physical
              << " generation=" << Generation << " hash=0x" << std::hex
              << hashData() << std::dec << " errors=" << errors << '\n';
    std::cout << "ROI Ended\n";
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}
