#include "MAA.hpp"

#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>

#if !defined(GEM5)
#error "test_logical_spd_cache_live requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace {

constexpr std::size_t Elements = 16384;
constexpr std::size_t Pages = 8;
constexpr std::size_t PageElements = 2048;
constexpr std::size_t BackingBytes = Elements * sizeof(double);
constexpr uint64_t ExpectedHash = 7303085050985348899ULL;

uint64_t
hashDouble(uint64_t hash, double value)
{
    uint64_t encoded = 0;
    std::memcpy(&encoded, &value, sizeof(encoded));
    for (unsigned byte = 0; byte < sizeof(encoded); ++byte) {
        hash ^= (encoded >> (byte * 8)) & 0xff;
        hash *= 1099511628211ULL;
    }
    return hash;
}

double *
allocateBacking()
{
    void *allocation = nullptr;
    if (posix_memalign(&allocation, BackingBytes, BackingBytes) != 0)
        return nullptr;
    return static_cast<double *>(allocation);
}

} // anonymous namespace

int
main()
{
    static_assert(Elements == TILE_SIZE, "smoke requires a 16K logical tile");
    double *source = allocateBacking();
    double *destination = allocateBacking();
    if (source == nullptr || destination == nullptr)
        return 2;

    for (std::size_t index = 0; index < Elements; ++index) {
        source[index] = 1.0 + static_cast<double>(index % 251);
        destination[index] = -1.0;
    }

    std::cout << "LOGICAL_SPD_CACHE_LIVE_LAYOUT elements=" << Elements
              << " pages=" << Pages
              << " page_elements=" << PageElements
              << " slots=2 slot_bytes=16384 payload_bytes=32768"
              << " hardware_bytes=32768 metadata_bytes=0 isoarea_timing_claim=0"
              << std::endl;

    // This checkpoint contains fixed input only.  MAA registration, scalar
    // setup, and the logical operation all occur after restore.
    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(source, source + Elements);
    add_mem_region(destination, destination + Elements);
    const int scalar = get_new_reg<double>(2.0);

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
    maa_alu_scalar_logical<double>(0, 1, source, destination, scalar,
                                   Operation_t::MUL_OP);

    uint64_t outputHash = 1469598103934665603ULL;
    std::size_t errors = 0;
    for (std::size_t index = 0; index < Elements; ++index) {
        const double expected = source[index] * 2.0;
        outputHash = hashDouble(outputHash, destination[index]);
        if (destination[index] != expected)
            ++errors;
    }
    std::cout << "LOGICAL_SPD_CACHE_LIVE_RESULT elements=" << Elements
              << " pages=" << Pages
              << " expected_hash=" << ExpectedHash
              << " output_hash=" << outputHash
              << " errors=" << errors << std::endl;
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);
    m5_exit(errors == 0 && outputHash == ExpectedHash ? 0 : 1);
    std::free(destination);
    std::free(source);
    return errors == 0 && outputHash == ExpectedHash ? 0 : 1;
}
