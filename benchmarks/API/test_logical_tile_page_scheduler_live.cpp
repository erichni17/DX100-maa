#include "MAA.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>

#if !defined(GEM5)
#error "test_logical_tile_page_scheduler_live requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace
{

constexpr std::size_t Elements = 16384;
constexpr std::size_t BackingBytes = Elements * sizeof(float);
constexpr uint64_t AHash = 5238007371172236237ULL;
constexpr uint64_t BHash = 4619008359347519206ULL;
constexpr uint64_t UnaryHash = 8757546768500349369ULL;
constexpr uint64_t DistinctVectorHash = 1468879162217515462ULL;
constexpr uint64_t SelfVectorHash = 9332068828147211593ULL;
constexpr uint64_t CHash = 12485598873299661541ULL;
constexpr uint64_t UnaryGeneration2Hash = 16675341876698374373ULL;

float *
allocateBacking()
{
    void *allocation = nullptr;
    if (posix_memalign(&allocation, BackingBytes, BackingBytes) != 0)
        return nullptr;
    return static_cast<float *>(allocation);
}

uint64_t
hashBacking(const float *values)
{
    uint64_t hash = 1469598103934665603ULL;
    for (std::size_t index = 0; index < Elements; ++index) {
        uint32_t bits = 0;
        std::memcpy(&bits, values + index, sizeof(bits));
        for (unsigned byte = 0; byte < sizeof(bits); ++byte) {
            hash ^= (bits >> (byte * 8)) & 0xff;
            hash *= 1099511628211ULL;
        }
    }
    return hash;
}

} // anonymous namespace

int
main()
{
    static_assert(Elements == TILE_SIZE,
                  "live scheduler guest requires a 16K logical tile");
    std::array<float *, 9> buffers{};
    for (float *&buffer : buffers) {
        buffer = allocateBacking();
        if (buffer == nullptr)
            return 2;
    }
    float *a = buffers[0];
    float *b = buffers[1];
    float *unary = buffers[2];
    float *distinctVector = buffers[3];
    float *selfVector = buffers[4];
    float *denseStore = buffers[5];
    float *c = buffers[6];
    float *unaryGeneration2 = buffers[7];
    float *denseStoreGeneration2 = buffers[8];
    for (std::size_t index = 0; index < Elements; ++index) {
        a[index] = static_cast<float>(1 + index % 251);
        b[index] = static_cast<float>(2 + index % 127);
        c[index] = static_cast<float>(3 + index % 61);
        unary[index] = distinctVector[index] = selfVector[index] = -1.0F;
        denseStore[index] = unaryGeneration2[index] =
            denseStoreGeneration2[index] = -1.0F;
    }

    std::cout << "LOGICAL_PAGE_LIVE_LAYOUT elements=16384 pages=4 "
                 "page_elements=4096 reserved_frames=4 "
                 "reserved_lane_span=2 private_payload_bytes=0 native_arms=0"
              << std::endl;
    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    for (float *buffer : buffers)
        add_mem_region(buffer, buffer + Elements);
    const int completion = get_new_tile<float>();
    const int scalar2 = get_new_reg<float>(2.0F);
    const int scalar4 = get_new_reg<float>(4.0F);

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);

    maa_stream_load_logical<float>(a, 0, completion);
    wait_ready(completion);
    maa_stream_load_logical<float>(b, 1, completion);
    wait_ready(completion);
    maa_alu_scalar_logical<float>(0, 2, a, unary, scalar2,
                                  Operation_t::MUL_OP);
    maa_alu_vector_logical<float>(0, 1, 3, a, b, distinctVector,
                                  Operation_t::ADD_OP);
    maa_alu_vector_logical<float>(3, 3, 4, distinctVector, distinctVector,
                                  selfVector, Operation_t::ADD_OP);
    maa_stream_store_logical<float>(denseStore, 4, completion);
    wait_ready(completion);

    // Reuse logical descriptors zero and two with different aligned backing
    // spans.  Both must advance monotonically to generation two.
    maa_stream_load_logical<float>(c, 0, completion);
    wait_ready(completion);
    maa_alu_scalar_logical<float>(0, 2, c, unaryGeneration2, scalar4,
                                  Operation_t::MUL_OP);
    maa_stream_store_logical<float>(denseStoreGeneration2, 2, completion);
    wait_ready(completion);

    const std::array<uint64_t, 9> hashes{{
        hashBacking(a), hashBacking(b), hashBacking(unary),
        hashBacking(distinctVector), hashBacking(selfVector),
        hashBacking(denseStore), hashBacking(c),
        hashBacking(unaryGeneration2), hashBacking(denseStoreGeneration2),
    }};
    const std::array<uint64_t, 9> expected{{
        AHash, BHash, UnaryHash, DistinctVectorHash, SelfVectorHash,
        SelfVectorHash, CHash, UnaryGeneration2Hash,
        UnaryGeneration2Hash,
    }};
    std::size_t errors = 0;
    for (std::size_t index = 0; index < hashes.size(); ++index)
        errors += hashes[index] == expected[index] ? 0 : 1;

    std::cout << "LOGICAL_PAGE_LIVE_RESULT operations=9 generations=2 "
              << "a_hash=" << hashes[0] << " b_hash=" << hashes[1]
              << " unary_hash=" << hashes[2]
              << " distinct_vector_hash=" << hashes[3]
              << " self_vector_hash=" << hashes[4]
              << " dense_store_hash=" << hashes[5]
              << " c_hash=" << hashes[6]
              << " unary_generation2_hash=" << hashes[7]
              << " dense_store_generation2_hash=" << hashes[8]
              << " errors=" << errors << std::endl;
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);
    m5_exit(errors == 0 ? 0 : 1);
    for (float *buffer : buffers)
        std::free(buffer);
    return errors == 0 ? 0 : 1;
}
