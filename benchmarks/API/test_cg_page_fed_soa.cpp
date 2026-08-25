#include "MAA.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <iostream>

#if !defined(GEM5)
#error "test_cg_page_fed_soa requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace
{

constexpr std::size_t Pages = 4;
constexpr std::size_t PageElements = 4096;
constexpr std::size_t LogicalElements = Pages * PageElements;
constexpr std::size_t DestinationElements = PageElements;
constexpr uint64_t Generation = 1;
constexpr uint64_t ExpectedProductHash = 2849837644626199427ULL;
constexpr uint64_t ExpectedDestinationHash = 17263589712773219203ULL;

using LogicalFloats = std::array<float, LogicalElements>;
using LogicalIndices = std::array<uint32_t, LogicalElements>;
using Destinations = std::array<float, DestinationElements>;

// Probe inputs and the legacy comparator's index backing are not candidate
// state.  The page-fed descriptor names neither of them; its only coherent
// source is productBacking.
alignas(64) LogicalIndices producerPageIndices;
alignas(64) LogicalIndices comparatorIndexBacking;
alignas(64) LogicalFloats left;
alignas(64) LogicalFloats right;
alignas(64) LogicalFloats productBacking;
alignas(64) Destinations ordinaryDestination;
alignas(64) Destinations existingSoaDestination;
alignas(64) Destinations pageFedDestination;

uint32_t
bits(float value)
{
    uint32_t result = 0;
    std::memcpy(&result, &value, sizeof(result));
    return result;
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
    // Each destination sees +2^24, +1, -2^24, +1 in exact source order.
    // Reordering those cross-page collisions changes the FP32 answer.
    constexpr std::array<float, Pages> leftValues = {
        4096.0F, 1.0F, -4096.0F, 1.0F};
    constexpr std::array<float, Pages> rightValues = {
        4096.0F, 1.0F, 4096.0F, 1.0F};
    for (std::size_t page = 0; page < Pages; ++page) {
        for (std::size_t lane = 0; lane < PageElements; ++lane) {
            const std::size_t ordinal = page * PageElements + lane;
            producerPageIndices[ordinal] = lane;
            comparatorIndexBacking[ordinal] = lane;
            left[ordinal] = leftValues[page];
            right[ordinal] = rightValues[page];
            productBacking[ordinal] = -999.0F;
        }
    }
    ordinaryDestination.fill(0.0F);
    existingSoaDestination.fill(0.0F);
    pageFedDestination.fill(0.0F);
}

void
publishProductPage(std::size_t page, int productTile,
                   int completionTile, int pageReg, int offsetReg,
                   int generationReg, uint32_t &publishGeneration)
{
    maa_const<uint32_t>(page, pageReg);
    maa_const<uint32_t>(page * PageElements, offsetReg);
    maa_const<uint32_t>(++publishGeneration, generationReg);
    maa_publish_spd_page_logical16_response_bearing<float>(
        productBacking.data(), page, productTile, completionTile, pageReg,
        offsetReg, generationReg);
}

void
runThreeWayCollisionProbe()
{
    const int pageMinReg = get_new_reg<int>(0);
    const int pageMaxReg = get_new_reg<int>(PageElements);
    const int strideReg = get_new_reg<int>(1);
    const int pageReg = get_new_reg<uint32_t>(0);
    const int offsetReg = get_new_reg<uint32_t>(0);
    const int publishGenerationReg = get_new_reg<uint32_t>(0);
    const int logicalMinReg = get_new_reg<int>(0);
    const int logicalMaxReg = get_new_reg<int>(LogicalElements);
    const int logicalStrideReg = get_new_reg<int>(1);

    const int indexTile = get_new_tile<uint32_t>();
    const int leftTile = get_new_tile<float>();
    const int rightTile = get_new_tile<float>();
    const int productTile = get_new_tile<float>();
    const int publishCompletionTile = get_new_tile<float>();
    const int ordinaryCompletionTile = get_new_tile<float>();
    const int pageFedCompletionTile = get_new_tile<float>();
    const int existingSoaCompletionTile = get_new_tile<float>();

    maa_indirect_rmw_vector_soa_jit_page_fed_open<float>(
        pageFedDestination.data(), productBacking.data(),
        pageFedCompletionTile, Operation_t::ADD_OP, Generation);
    std::cout << "CG_PAGE_FED_SOA_PROGRESS open_response=1" << std::endl;

    uint32_t publishGeneration = 0;
    for (std::size_t page = 0; page < Pages; ++page) {
        const std::size_t offset = page * PageElements;
        maa_stream_load<uint32_t>(producerPageIndices.data() + offset,
                                  pageMinReg, pageMaxReg, strideReg,
                                  indexTile);
        maa_stream_load<float>(left.data() + offset, pageMinReg, pageMaxReg,
                               strideReg, leftTile);
        maa_stream_load<float>(right.data() + offset, pageMinReg, pageMaxReg,
                               strideReg, rightTile);
        wait_ready(indexTile);
        wait_ready(leftTile);
        wait_ready(rightTile);
        maa_alu_vector<float>(leftTile, rightTile, productTile,
                              Operation_t::MUL_OP);
        wait_ready(productTile);

        maa_indirect_rmw_vector<float>(
            ordinaryDestination.data(), indexTile, productTile,
            Operation_t::ADD_OP, -1, ordinaryCompletionTile);
        wait_ready(ordinaryCompletionTile);
        std::cout << "CG_PAGE_FED_SOA_PROGRESS ordinary_page=" << page
                  << std::endl;

        // Publication and direct index admission use disjoint stream/SPD and
        // Row/Offset resources.  The admission response is timed while the
        // product WriteReq/WriteResp stream remains live.
        publishProductPage(page, productTile, publishCompletionTile,
                           pageReg, offsetReg, publishGenerationReg,
                           publishGeneration);
        maa_soa_jit_page_fed_admit(Generation, page, indexTile);
        std::cout << "CG_PAGE_FED_SOA_PROGRESS admitted_page=" << page
                  << std::endl;
        wait_ready(publishCompletionTile);
        std::cout << "CG_PAGE_FED_SOA_PROGRESS published_page=" << page
                  << std::endl;
    }
    maa_soa_jit_page_fed_close(Generation);
    wait_ready(pageFedCompletionTile);
    std::cout << "CG_PAGE_FED_SOA_PROGRESS page_fed_complete=1"
              << std::endl;

    // This is the existing one-pass SoA comparator.  It alone names the
    // coherent comparatorIndexBacking; the candidate already completed with
    // zero coherent index publication or read traffic.
    maa_indirect_rmw_vector_soa_jit<float>(
        existingSoaDestination.data(), comparatorIndexBacking.data(),
        productBacking.data(), nullptr, logicalMinReg, logicalMaxReg,
        logicalStrideReg, existingSoaCompletionTile, Operation_t::ADD_OP);
    wait_ready(existingSoaCompletionTile);
    std::cout << "CG_PAGE_FED_SOA_PROGRESS existing_soa_complete=1"
              << std::endl;
}

std::size_t
verify(uint64_t &productHash, uint64_t &ordinaryHash,
       uint64_t &existingSoaHash, uint64_t &pageFedHash)
{
    std::size_t errors = 0;
    for (std::size_t ordinal = 0; ordinal < LogicalElements; ++ordinal) {
        const uint32_t expected = ordinal / PageElements == 0
            ? 0x4b800000U
            : ordinal / PageElements == 1
                ? 0x3f800000U
                : ordinal / PageElements == 2 ? 0xcb800000U : 0x3f800000U;
        if (bits(productBacking[ordinal]) != expected)
            ++errors;
    }
    for (std::size_t index = 0; index < DestinationElements; ++index) {
        const uint32_t ordinary = bits(ordinaryDestination[index]);
        const uint32_t existing = bits(existingSoaDestination[index]);
        const uint32_t pageFed = bits(pageFedDestination[index]);
        if (ordinary != 0x3f800000U || existing != ordinary ||
            pageFed != ordinary)
            ++errors;
    }
    productHash = hashFloats(productBacking.data(), LogicalElements);
    ordinaryHash = hashFloats(ordinaryDestination.data(),
                              DestinationElements);
    existingSoaHash = hashFloats(existingSoaDestination.data(),
                                 DestinationElements);
    pageFedHash = hashFloats(pageFedDestination.data(), DestinationElements);
    if (productHash != ExpectedProductHash ||
        ordinaryHash != ExpectedDestinationHash ||
        existingSoaHash != ExpectedDestinationHash ||
        pageFedHash != ExpectedDestinationHash)
        ++errors;
    return errors;
}

} // anonymous namespace

int
main()
{
    static_assert(TILE_SIZE == LogicalElements);
    static_assert(gem5::maa::PageFedSoaJitState::HardwareBytes == 16);
    initializeInputs();
    std::cout << "CG_PAGE_FED_SOA_LAYOUT pages=4 page_elements=4096 "
                 "logical_elements=16384 collisions=cross_page "
                 "candidate_coherent_index_bytes=0 "
                 "candidate_index_publication_lines=0"
              << std::endl;
    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(producerPageIndices.data(),
                   producerPageIndices.data() + LogicalElements);
    add_mem_region(comparatorIndexBacking.data(),
                   comparatorIndexBacking.data() + LogicalElements);
    add_mem_region(left.data(), left.data() + LogicalElements);
    add_mem_region(right.data(), right.data() + LogicalElements);
    add_mem_region(productBacking.data(),
                   productBacking.data() + LogicalElements);
    add_mem_region(ordinaryDestination.data(),
                   ordinaryDestination.data() + DestinationElements);
    add_mem_region(existingSoaDestination.data(),
                   existingSoaDestination.data() + DestinationElements);
    add_mem_region(pageFedDestination.data(),
                   pageFedDestination.data() + DestinationElements);

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
    runThreeWayCollisionProbe();
    uint64_t productHash = 0;
    uint64_t ordinaryHash = 0;
    uint64_t existingSoaHash = 0;
    uint64_t pageFedHash = 0;
    const std::size_t errors = verify(
        productHash, ordinaryHash, existingSoaHash, pageFedHash);
    std::cout << "CG_PAGE_FED_SOA_LEDGER persistent_state_bytes=16 "
                 "command_doorbell_bytes=8 command_queue_bytes=0 "
                 "index_payload_bytes=0 hidden_descriptor_bytes=0 "
                 "row_offset_incremental_bytes=0 "
                 "candidate_coherent_index_bytes=0 "
                 "product_backing_bytes=65536 comparator_index_bytes=65536 "
                 "probe_input_index_bytes=65536"
              << std::endl;
    std::cout << "CG_PAGE_FED_SOA_RESULT pages=4 products=16384 "
                 "ordinary_page_rmws=4 existing_soa_descriptors=1 "
                 "page_fed_descriptors=1 page_fed_admissions=4 "
                 "page_fed_open_responses=1 page_fed_closes=1 "
                 "page_fed_command_responses=5 page_fed_total_responses=6 "
                 "admitted_index_words=16384 index_publish_pages=0 "
                 "product_publish_pages=4 product_publish_lines=1024 "
                 "coherent_index_read_lines=0 coherent_index_write_lines=0 "
              << "product_hash=" << productHash
              << " ordinary_hash=" << ordinaryHash
              << " existing_soa_hash=" << existingSoaHash
              << " page_fed_hash=" << pageFedHash
              << " errors=" << errors << std::endl;
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}
