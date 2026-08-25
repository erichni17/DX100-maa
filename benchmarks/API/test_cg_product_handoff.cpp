#include "MAA.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <iostream>

#if !defined(GEM5)
#error "test_cg_product_handoff requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace {

constexpr std::size_t kPages = 4;
constexpr std::size_t kPageElements = 4096;
constexpr std::size_t kLogicalElements = kPages * kPageElements;
constexpr std::size_t kDestinationElements = kPageElements;
constexpr uint64_t kExpectedIndexHash = 14754458253095254915ULL;
constexpr uint64_t kExpectedProductHash = 2849837644626199427ULL;
constexpr uint64_t kExpectedDestinationHash = 17263589712773219203ULL;

using LogicalFloats = std::array<float, kLogicalElements>;
using LogicalIndices = std::array<uint32_t, kLogicalElements>;
using Destinations = std::array<float, kDestinationElements>;

alignas(64) LogicalFloats left;
alignas(64) LogicalFloats right;
alignas(64) LogicalIndices physical_indices;
// This coherent range is written by an ordinary STREAM_ST before publication.
// It is the diagnostic observation of the completed physical MUL result; the
// CPU never reads an SPD tile.
alignas(64) LogicalFloats prepublication_products;
alignas(64) LogicalIndices published_indices;
alignas(64) LogicalFloats published_products;
alignas(64) Destinations ordinary_destinations;
alignas(64) Destinations soa_destinations;

uint32_t
bits(float value)
{
    uint32_t result = 0;
    std::memcpy(&result, &value, sizeof(result));
    return result;
}

uint64_t
hashWords(const uint32_t *words, std::size_t count)
{
    uint64_t hash = 1469598103934665603ULL;
    for (std::size_t index = 0; index < count; ++index) {
        const uint32_t word = words[index];
        for (unsigned byte = 0; byte < sizeof(word); ++byte) {
            hash ^= (word >> (byte * 8)) & 0xffU;
            hash *= 1099511628211ULL;
        }
    }
    return hash;
}

uint64_t
hashFloats(const float *values, std::size_t count)
{
    uint64_t hash = 1469598103934665603ULL;
    for (std::size_t index = 0; index < count; ++index) {
        const uint32_t word = bits(values[index]);
        for (unsigned byte = 0; byte < sizeof(word); ++byte) {
            hash ^= (word >> (byte * 8)) & 0xffU;
            hash *= 1099511628211ULL;
        }
    }
    return hash;
}

void
initializeInputs()
{
    // Every destination receives this exact FP32 sequence, in source order:
    // +2^24, +1, -2^24, +1.  It is deliberately order-sensitive: serial
    // FP32 accumulation from zero produces 1.0f, while reordering can
    // produce 0.0f or 2.0f.  Each destination index recurs once per physical
    // page, so the four 4K pages have genuine cross-page collisions.
    constexpr std::array<float, kPages> kLeft = {
        4096.0F, 1.0F, -4096.0F, 1.0F};
    constexpr std::array<float, kPages> kRight = {
        4096.0F, 1.0F, 4096.0F, 1.0F};
    for (std::size_t page = 0; page < kPages; ++page) {
        for (std::size_t offset = 0; offset < kPageElements; ++offset) {
            const std::size_t logical = page * kPageElements + offset;
            left[logical] = kLeft[page];
            right[logical] = kRight[page];
            physical_indices[logical] = static_cast<uint32_t>(offset);
            prepublication_products[logical] = -999.0F;
            published_indices[logical] = UINT32_MAX;
            published_products[logical] = -999.0F;
        }
    }
    ordinary_destinations.fill(0.0F);
    soa_destinations.fill(0.0F);
}

void
publishPhysicalPage(std::size_t page, int index_tile, int product_tile,
                    int index_completion_tile, int product_completion_tile,
                    int logical_page_reg, int logical_offset_reg,
                    int generation_reg, uint32_t &generation)
{
    const uint32_t logical_page = static_cast<uint32_t>(page);
    const uint32_t logical_offset =
        static_cast<uint32_t>(page * kPageElements);
    maa_const<uint32_t>(logical_page, logical_page_reg);
    maa_const<uint32_t>(logical_offset, logical_offset_reg);

    maa_const<uint32_t>(++generation, generation_reg);
    maa_publish_spd_page_logical16_response_bearing<uint32_t>(
        published_indices.data(), logical_page, index_tile,
        index_completion_tile, logical_page_reg, logical_offset_reg,
        generation_reg);
    wait_ready(index_completion_tile);

    maa_const<uint32_t>(++generation, generation_reg);
    maa_publish_spd_page_logical16_response_bearing<float>(
        published_products.data(), logical_page, product_tile,
        product_completion_tile, logical_page_reg, logical_offset_reg,
        generation_reg);
    wait_ready(product_completion_tile);
}

void
runOrdinaryFourPageRmws()
{
    const int min_reg = get_new_reg<int>(0);
    const int max_reg = get_new_reg<int>(kPageElements);
    const int stride_reg = get_new_reg<int>(1);
    const int logical_page_reg = get_new_reg<uint32_t>(0);
    const int logical_offset_reg = get_new_reg<uint32_t>(0);
    const int generation_reg = get_new_reg<uint32_t>(0);
    const int index_tile = get_new_tile<uint32_t>();
    const int left_tile = get_new_tile<float>();
    const int right_tile = get_new_tile<float>();
    const int product_tile = get_new_tile<float>();
    const int index_completion_tile = get_new_tile<uint32_t>();
    const int product_completion_tile = get_new_tile<float>();
    const int ordinary_completion_tile = get_new_tile<float>();
    uint32_t generation = 0;

    for (std::size_t page = 0; page < kPages; ++page) {
        const std::size_t offset = page * kPageElements;
        maa_stream_load<uint32_t>(physical_indices.data() + offset, min_reg,
                                  max_reg, stride_reg, index_tile);
        maa_stream_load<float>(left.data() + offset, min_reg, max_reg,
                               stride_reg, left_tile);
        maa_stream_load<float>(right.data() + offset, min_reg, max_reg,
                               stride_reg, right_tile);
        wait_ready(index_tile);
        wait_ready(left_tile);
        wait_ready(right_tile);
        maa_alu_vector<float>(left_tile, right_tile, product_tile,
                              Operation_t::MUL_OP);
        wait_ready(product_tile);

        // This response-closed coherent copy is intentionally before the
        // response-bearing product publication.  It observes the same t7-like
        // physical MUL tile that feeds the ordinary page-local RMW.
        maa_stream_store<float>(prepublication_products.data() + offset,
                                min_reg, max_reg, stride_reg, product_tile);
        wait_ready(product_tile);
        publishPhysicalPage(page, index_tile, product_tile,
                            index_completion_tile, product_completion_tile,
                            logical_page_reg, logical_offset_reg,
                            generation_reg, generation);

        maa_indirect_rmw_vector<float>(ordinary_destinations.data(),
                                       index_tile, product_tile,
                                       Operation_t::ADD_OP, -1,
                                       ordinary_completion_tile);
        wait_ready(ordinary_completion_tile);
    }
}

void
runOneUsefulLogicalSoaJitAdd()
{
    const int min_reg = get_new_reg<int>(0);
    const int max_reg = get_new_reg<int>(kLogicalElements);
    const int stride_reg = get_new_reg<int>(1);
    const int completion_tile = get_new_tile<float>();

    // Exactly one useful 16K selected set.  No page mask or second pass is
    // present: the coherent arrays are the four response-published pages.
    maa_indirect_rmw_vector_soa_jit<float>(
        soa_destinations.data(), published_indices.data(),
        published_products.data(), nullptr, min_reg, max_reg, stride_reg,
        completion_tile, Operation_t::ADD_OP);
    wait_ready(completion_tile);
}

std::size_t
verifyCoherentResults(uint64_t &prepublication_hash, uint64_t &published_hash,
                      uint64_t &ordinary_hash, uint64_t &soa_hash)
{
    std::size_t errors = 0;
    for (std::size_t logical = 0; logical < kLogicalElements; ++logical) {
        if (physical_indices[logical] != published_indices[logical])
            ++errors;
        if (bits(prepublication_products[logical]) !=
            bits(published_products[logical]))
            ++errors;
    }
    for (std::size_t destination = 0; destination < kDestinationElements;
         ++destination) {
        if (bits(ordinary_destinations[destination]) != 0x3f800000U)
            ++errors;
        if (bits(soa_destinations[destination]) != 0x3f800000U)
            ++errors;
        if (bits(ordinary_destinations[destination]) !=
            bits(soa_destinations[destination]))
            ++errors;
    }

    prepublication_hash = hashFloats(prepublication_products.data(),
                                     kLogicalElements);
    published_hash = hashFloats(published_products.data(), kLogicalElements);
    ordinary_hash = hashFloats(ordinary_destinations.data(),
                               kDestinationElements);
    soa_hash = hashFloats(soa_destinations.data(), kDestinationElements);
    if (hashWords(physical_indices.data(), kLogicalElements) !=
            kExpectedIndexHash ||
        hashWords(published_indices.data(), kLogicalElements) !=
            kExpectedIndexHash ||
        prepublication_hash != kExpectedProductHash ||
        published_hash != kExpectedProductHash ||
        ordinary_hash != kExpectedDestinationHash ||
        soa_hash != kExpectedDestinationHash) {
        ++errors;
    }
    return errors;
}

} // namespace

int
main()
{
    static_assert(TILE_SIZE == kLogicalElements,
                  "the SoA/JIT half must retain one 16K selected set");
    initializeInputs();
    std::cout << "CG_PRODUCT_HANDOFF_LAYOUT pages=4 page_elements=4096 "
                 "logical_elements=16384 physical_product_pages=4 "
                 "selected_sets=1 masked_passes=0 host_spd_reads=0"
              << std::endl;

    // The checkpoint contains deterministic coherent inputs only.  All MAA
    // state and all registered address regions are created after restore.
    m5_checkpoint(0, 0);
    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(left.data(), left.data() + kLogicalElements);
    add_mem_region(right.data(), right.data() + kLogicalElements);
    add_mem_region(physical_indices.data(),
                   physical_indices.data() + kLogicalElements);
    add_mem_region(prepublication_products.data(),
                   prepublication_products.data() + kLogicalElements);
    add_mem_region(published_indices.data(),
                   published_indices.data() + kLogicalElements);
    add_mem_region(published_products.data(),
                   published_products.data() + kLogicalElements);
    add_mem_region(ordinary_destinations.data(),
                   ordinary_destinations.data() + kDestinationElements);
    add_mem_region(soa_destinations.data(),
                   soa_destinations.data() + kDestinationElements);

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
    runOrdinaryFourPageRmws();
    runOneUsefulLogicalSoaJitAdd();
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    uint64_t prepublication_hash = 0;
    uint64_t published_hash = 0;
    uint64_t ordinary_hash = 0;
    uint64_t soa_hash = 0;
    const std::size_t errors = verifyCoherentResults(
        prepublication_hash, published_hash, ordinary_hash, soa_hash);
    std::cout << "CG_PRODUCT_HANDOFF_RESULT pages=4 products=16384 "
                 "published_index_words=16384 published_product_words=16384 "
                 "index_hash="
              << hashWords(published_indices.data(), kLogicalElements)
              << " prepublication_product_hash=" << prepublication_hash
              << " published_product_hash=" << published_hash
              << " ordinary_destination_hash=" << ordinary_hash
              << " soa_destination_hash=" << soa_hash
              << " exact_product_words=16384 exact_destination_words=4096 "
                 "ordinary_page_rmws=4 soa_jit_descriptors=1 "
                 "masked_passes=0 errors="
              << errors << std::endl;
    m5_exit(errors == 0 ? 0 : 1);
    return errors == 0 ? 0 : 1;
}
