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
#ifndef LOGICAL_SPD_CACHE_MODE
#define LOGICAL_SPD_CACHE_MODE 0
#endif
constexpr bool Serial4K = LOGICAL_SPD_CACHE_MODE == 0;
constexpr std::size_t Pages = Serial4K ? 4 : 8;
constexpr std::size_t PageElements = Serial4K ? 4096 : 2048;
#ifdef LOGICAL_SPD_CACHE_FP32
using Scalar = float;
#else
using Scalar = double;
#endif
constexpr std::size_t BackingBytes = Elements * sizeof(Scalar);
#ifdef LOGICAL_SPD_CACHE_FP32
constexpr uint64_t ExpectedHash = 6880529560763119881ULL;
#else
constexpr uint64_t ExpectedHash = 7303085050985348899ULL;
#endif

uint64_t
hashScalar(uint64_t hash, Scalar value)
{
    uint64_t encoded = 0;
    std::memcpy(&encoded, &value, sizeof(encoded));
    for (unsigned byte = 0; byte < sizeof(encoded); ++byte) {
        hash ^= (encoded >> (byte * 8)) & 0xff;
        hash *= 1099511628211ULL;
    }
    return hash;
}

Scalar *
allocateBacking()
{
    void *allocation = nullptr;
    if (posix_memalign(&allocation, BackingBytes, BackingBytes) != 0)
        return nullptr;
    return static_cast<Scalar *>(allocation);
}

} // anonymous namespace

int
main()
{
    static_assert(Elements == TILE_SIZE, "smoke requires a 16K logical tile");
    Scalar *source = allocateBacking();
    Scalar *destination = allocateBacking();
    if (source == nullptr || destination == nullptr)
        return 2;

    for (std::size_t index = 0; index < Elements; ++index) {
        source[index] = Scalar{1.0} + static_cast<Scalar>(index % 251);
        destination[index] = -1.0;
    }

    std::cout << "LOGICAL_SPD_CACHE_LIVE_LAYOUT elements=" << Elements
              << " pages=" << Pages
              << " page_elements=" << PageElements
              << " slots=" << (Serial4K ? 1 : 2)
              << " private_payload_bytes=32768"
              << " packed_private_metadata_lower_bound_bytes=1309"
              << " visible_spd_payload_additive=1 isoarea_timing_claim=0"
              << std::endl;

    // This checkpoint contains fixed input only.  MAA registration, scalar
    // setup, and the logical operation all occur after restore.
    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(source, source + Elements);
    add_mem_region(destination, destination + Elements);
    const int scalar = get_new_reg<Scalar>(Scalar{2.0});

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
    maa_alu_scalar_logical<Scalar>(0, 1, source, destination, scalar,
                                   Operation_t::MUL_OP);

    uint64_t outputHash = 1469598103934665603ULL;
    std::size_t errors = 0;
    for (std::size_t index = 0; index < Elements; ++index) {
        const Scalar expected = source[index] * Scalar{2.0};
        outputHash = hashScalar(outputHash, destination[index]);
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
