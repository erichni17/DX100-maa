#include "MAA.hpp"

#include <cstdint>
#include <iostream>
#include <vector>

#if !defined(GEM5)
#error "test_cpu_spd_prefetch_boundary requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace
{

constexpr int PhysicalElements = 4096;

#ifndef CPU_SPD_NEGATIVE_ARM
#define CPU_SPD_NEGATIVE_ARM 0
#endif

} // anonymous namespace

int
main()
{
    std::vector<uint32_t> source(PhysicalElements);
    for (int element = 0; element < PhysicalElements; ++element)
        source[element] = static_cast<uint32_t>(element * 17 + 3);

    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(source.data(), source.data() + source.size());
    const int min_reg = get_new_reg<int>(0);
    const int max_reg = get_new_reg<int>(PhysicalElements);
    const int stride_reg = get_new_reg<int>(1);
    const int tile_id = get_new_tile<uint32_t>();
    volatile uint32_t *tile =
        get_cacheable_tile_pointer<volatile uint32_t>(tile_id);

    maa_stream_load<uint32_t>(
        source.data(), min_reg, max_reg, stride_reg, tile_id);
    wait_ready(tile_id);

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);

    uint64_t sum = 0;
    uint32_t last = 0;
    for (int element = 0; element < PhysicalElements; ++element) {
        last = tile[element];
        sum += last;
    }

    constexpr uint64_t ExpectedSum = 142583808;
    constexpr uint32_t ExpectedLast = 69618;
#if CPU_SPD_NEGATIVE_ARM
    if (sum != ExpectedSum || last != ExpectedLast) {
        std::cout << "CPU_SPD_PREFETCH_BOUNDARY_NEGATIVE_PRECONDITION_FAIL"
                  << std::endl;
        m5_exit(0);
        return 1;
    }
    std::cout << "CPU_SPD_PREFETCH_BOUNDARY_NEGATIVE "
              << "scan_sum=" << sum << " scan_last=" << last
              << " next=architectural_element4096" << std::endl;
    m5_dump_stats(0, 0);
    __asm__ __volatile__("mfence;" ::: "memory");
    const uint32_t observed = tile[PhysicalElements];
    std::cout << "CPU_SPD_PREFETCH_BOUNDARY_NEGATIVE_OBSERVED value="
              << observed << std::endl;
    m5_exit(0);
    return 2;
#else
    std::cout << "CPU_SPD_PREFETCH_BOUNDARY guest_elements="
              << PhysicalElements << " sum=" << sum << " last=" << last
              << " result="
              << ((sum == ExpectedSum && last == ExpectedLast) ?
                      "PASS" : "FAIL")
              << std::endl;
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);
    m5_exit(0);
    return sum == ExpectedSum && last == ExpectedLast ? 0 : 1;
#endif
}
