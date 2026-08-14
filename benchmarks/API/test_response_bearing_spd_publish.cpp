#include "MAA.hpp"

#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>

#if !defined(GEM5)
#error "test_response_bearing_spd_publish requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace
{

constexpr std::size_t PageElements = 4096;
constexpr std::size_t LogicalElements = 4 * PageElements;
constexpr uint64_t ExpectedHash = 16924436845436167371ULL;

uint64_t
hashFloat(uint64_t hash, float value)
{
    uint32_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    for (unsigned byte = 0; byte < sizeof(bits); ++byte) {
        hash ^= (bits >> (byte * 8)) & 0xff;
        hash *= 1099511628211ULL;
    }
    return hash;
}

float *
allocateFloats(std::size_t elements)
{
    void *allocation = nullptr;
    if (posix_memalign(&allocation, 64, elements * sizeof(float)) != 0)
        return nullptr;
    return static_cast<float *>(allocation);
}

} // anonymous namespace

int
main()
{
    static_assert(TILE_SIZE == LogicalElements,
                  "smoke requires the GZP logical-16K ABI");
    float *source = allocateFloats(PageElements);
    float *backing = allocateFloats(LogicalElements);
    if (source == nullptr || backing == nullptr)
        return 2;
    for (std::size_t index = 0; index < PageElements; ++index)
        source[index] = static_cast<float>((index * 37 + 11) % 1009) / 8.0f;
    for (std::size_t index = 0; index < LogicalElements; ++index)
        backing[index] = -10000.0f - static_cast<float>(index);

    // The checkpoint contains only deterministic CPU input. MAA state and
    // address-region registration are created after restore.
    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(source, source + PageElements);
    add_mem_region(backing, backing + LogicalElements);

    const int loadMin = get_new_reg<int32_t>(0);
    const int loadMax = get_new_reg<int32_t>(PageElements);
    const int loadStride = get_new_reg<int32_t>(1);
    const int logicalPage = get_new_reg<uint32_t>(0);
    const int logicalOffset = get_new_reg<uint32_t>(0);
    const int generation = get_new_reg<uint32_t>(1);
    const int maaAllEqualReg = get_new_reg<uint32_t>(0);
    const int sourceTile = get_new_tile<float>();
    const int completionTile = get_new_tile<float>();
    const int verifyTile = get_new_tile<uint32_t>();
    const int compareTile = get_new_tile<uint32_t>();

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);

    maa_stream_load<float>(source, loadMin, loadMax, loadStride, sourceTile);
    wait_ready(sourceTile);
    maa_publish_spd_page_logical16_response_bearing<float>(
        backing, 0, sourceTile, completionTile, logicalPage,
        logicalOffset, generation);
    wait_ready(completionTile);

    uint64_t cpuHash = 1469598103934665603ULL;
    std::size_t cpuErrors = 0;
    for (std::size_t index = 0; index < PageElements; ++index) {
        cpuHash = hashFloat(cpuHash, backing[index]);
        uint32_t observed = 0;
        uint32_t expected = 0;
        std::memcpy(&observed, &backing[index], sizeof(observed));
        std::memcpy(&expected, &source[index], sizeof(expected));
        if (observed != expected)
            ++cpuErrors;
    }

    // Read the same coherent backing page through the normal MAA cache path,
    // then compare every returned SPD word bit-for-bit.
    maa_stream_load<uint32_t>(reinterpret_cast<uint32_t *>(backing),
                              loadMin, loadMax, loadStride, verifyTile);
    wait_ready(verifyTile);
    // Compare inside MAA as UINT32 so every FP32 bit, including sign and
    // exponent, participates.  Reducing the 4096 one-bit equality results
    // avoids speculative CPU reads beyond the 4K physical SPD boundary.
    maa_alu_vector<uint32_t>(verifyTile, sourceTile, compareTile,
                             Operation_t::EQ_OP);
    wait_ready(compareTile);
    maa_alu_reduce<uint32_t>(compareTile, maaAllEqualReg,
                             Operation_t::AND_OP);
    wait_ready(compareTile);
    const uint32_t maaAllEqual = get_reg<uint32_t>(maaAllEqualReg);
    const std::size_t maaErrors = maaAllEqual == 1 ? 0 : 1;

    const std::size_t errors = cpuErrors + maaErrors;
    std::cout << "RESPONSE_BEARING_SPD_PUBLISH_RESULT elements="
              << PageElements << " logical_page=0 logical_offset=0"
              << " generation=1 expected_hash=" << ExpectedHash
              << " cpu_hash=" << cpuHash
              << " maa_exact_words=" << PageElements
              << " maa_all_equal=" << maaAllEqual
              << " cpu_errors=" << cpuErrors
              << " maa_errors=" << maaErrors << " errors=" << errors
              << std::endl;
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);
    const bool pass = errors == 0 && cpuHash == ExpectedHash &&
                      maaAllEqual == 1;
    m5_exit(pass ? 0 : 1);
    std::free(backing);
    std::free(source);
    return pass ? 0 : 1;
}
