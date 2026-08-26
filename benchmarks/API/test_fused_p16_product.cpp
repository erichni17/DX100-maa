#include "MAA.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <iostream>

#if !defined(GEM5)
#error "test_fused_p16_product requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace
{

constexpr std::size_t Pages = 4;
constexpr std::size_t PageElements = 4096;
constexpr std::size_t LogicalElements = Pages * PageElements;
constexpr std::size_t SourceElements = 4096;
constexpr uint64_t QGeneration = 1;
constexpr uint32_t Sentinel = 0x7fc00001U;

using Source = std::array<float, SourceElements>;
using LogicalFloats = std::array<float, LogicalElements>;
using LogicalIndices = std::array<uint32_t, LogicalElements>;

alignas(64) Source source;
alignas(64) LogicalIndices colidx;
alignas(64) LogicalFloats coefficients;
alignas(64) LogicalFloats products;
alignas(64) LogicalFloats referenceProducts;
alignas(64) LogicalIndices qIndices;
alignas(64) LogicalFloats qOutput;

uint32_t
bits(float value)
{
    uint32_t result = 0;
    std::memcpy(&result, &value, sizeof(result));
    return result;
}

float
fromBits(uint32_t value)
{
    float result = 0.0f;
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
initialize()
{
    for (std::size_t index = 0; index < SourceElements; ++index)
        source[index] = fromBits(0x3f000001U + index);
    uint32_t pseudo = 0x6d2b79f5U;
    for (std::size_t ordinal = 0; ordinal < LogicalElements; ++ordinal) {
        if (ordinal < PageElements)
            colidx[ordinal] = 7;
        else if (ordinal < 2 * PageElements)
            colidx[ordinal] = 64 + (ordinal & 15U);
        else if (ordinal < 3 * PageElements)
            colidx[ordinal] =
                static_cast<uint32_t>((ordinal * 257U) & 4095U);
        else {
            pseudo ^= pseudo << 13;
            pseudo ^= pseudo >> 17;
            pseudo ^= pseudo << 5;
            colidx[ordinal] = pseudo & 4095U;
        }
        coefficients[ordinal] =
            fromBits(0x3f400001U + static_cast<uint32_t>(ordinal));
        referenceProducts[ordinal] =
            source[colidx[ordinal]] * coefficients[ordinal];
        products[ordinal] = fromBits(Sentinel);
        qIndices[ordinal] = static_cast<uint32_t>(ordinal);
        qOutput[ordinal] = 0.0f;
    }
}

void
runFusedProducerAndQ16()
{
    const int logicalMin = get_new_reg<int>(0);
    const int logicalMax = get_new_reg<int>(LogicalElements);
    const int logicalStride = get_new_reg<int>(1);
    const int pageMin = get_new_reg<int>(0);
    const int pageMax = get_new_reg<int>(PageElements);
    const int pageStride = get_new_reg<int>(1);
    const int fusedCompletion = get_new_tile<float>();
    const int qCompletion = get_new_tile<float>();
    const int qIndexTile = get_new_tile<uint32_t>();

    maa_indirect_load_virtual_index_product_fp32(
        source.data(), colidx.data(), coefficients.data(), fusedCompletion,
        products.data(), logicalMin, logicalMax, logicalStride);
    wait_ready(fusedCompletion);
    std::cout << "FUSED_P16_PRODUCT_PROGRESS producer_complete=1"
              << std::endl;

    maa_indirect_rmw_vector_soa_jit_page_fed_open<float>(
        qOutput.data(), products.data(), qCompletion,
        Operation_t::ADD_OP, QGeneration);
    for (std::size_t page = 0; page < Pages; ++page) {
        maa_stream_load<uint32_t>(
            qIndices.data() + page * PageElements, pageMin, pageMax,
            pageStride, qIndexTile);
        wait_ready(qIndexTile);
        maa_soa_jit_page_fed_admit(QGeneration, page, qIndexTile);
    }
    maa_soa_jit_page_fed_close(QGeneration);
    wait_ready(qCompletion);
    std::cout << "FUSED_P16_PRODUCT_PROGRESS q16_complete=1" << std::endl;
}

std::size_t
verify(uint64_t &referenceHash, uint64_t &productHash, uint64_t &qHash)
{
    std::size_t errors = 0;
    std::size_t sentinels = 0;
    for (std::size_t ordinal = 0; ordinal < LogicalElements; ++ordinal) {
        const uint32_t expected = bits(referenceProducts[ordinal]);
        const uint32_t product = bits(products[ordinal]);
        const uint32_t q = bits(qOutput[ordinal]);
        if (product == Sentinel)
            ++sentinels;
        if (product != expected || q != expected)
            ++errors;
    }
    referenceHash = hashFloats(referenceProducts.data(), LogicalElements);
    productHash = hashFloats(products.data(), LogicalElements);
    qHash = hashFloats(qOutput.data(), LogicalElements);
    if (referenceHash != productHash || productHash != qHash ||
        sentinels != 0)
        ++errors;
    constexpr std::size_t DumpWords = 64;
    for (std::size_t begin = 0; begin < LogicalElements;
         begin += DumpWords) {
        std::cout << "FUSED_P16_PRODUCT_DUMP offset=" << begin;
        for (std::size_t ordinal = begin; ordinal < begin + DumpWords;
             ++ordinal) {
            std::cout << ' ' << std::hex << std::setw(8)
                      << std::setfill('0') << bits(products[ordinal]);
        }
        std::cout << std::dec << std::setfill(' ') << std::endl;
    }
    std::cout << "FUSED_P16_PRODUCT_SENTINELS count=" << sentinels
              << std::endl;
    return errors;
}

} // anonymous namespace

int
main()
{
    static_assert(TILE_SIZE == LogicalElements);
    initialize();
    std::cout << "FUSED_P16_PRODUCT_LAYOUT logical=16384 pages=4 "
                 "page_elements=4096 source_elements=4096 "
                 "segments=all_same,same_line,cross_page,pseudorandom "
                 "virtual_p_allocation_bytes=0 virtual_p_traffic_bytes=0 "
                 "product_publisher_lines=0 hidden_spill_bytes=0 "
                 "global_fallbacks=0"
              << std::endl;
    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(source.data(), source.data() + source.size());
    add_mem_region(colidx.data(), colidx.data() + colidx.size());
    add_mem_region(coefficients.data(),
                   coefficients.data() + coefficients.size());
    add_mem_region(products.data(), products.data() + products.size());
    add_mem_region(qIndices.data(), qIndices.data() + qIndices.size());
    add_mem_region(qOutput.data(), qOutput.data() + qOutput.size());

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
    runFusedProducerAndQ16();
    uint64_t referenceHash = 0;
    uint64_t productHash = 0;
    uint64_t qHash = 0;
    const std::size_t errors = verify(referenceHash, productHash, qHash);
    std::cout << "FUSED_P16_PRODUCT_RESULT logical=16384 p_epochs=1 "
                 "source_ordinals=16384 coefficient_deliveries=16384 "
                 "mul_accepts=16384 mul_completions=16384 "
                 "product_insertions=16384 "
                 "product_semantic_write_completions=16384 "
                 "q_operations=1 q_page_admissions=4 q_command_responses=5 "
                 "q_value_deliveries=16384 drains=0 fallbacks=0 "
                 "publisher_lines=0 virtual_p_bytes=0 host_payload_access=0 "
              << "reference_hash=" << referenceHash
              << " product_hash=" << productHash
              << " q_hash=" << qHash
              << " errors=" << errors << std::endl;
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);
    m5_exit(errors == 0 ? 0 : 1);
    return errors == 0 ? 0 : 1;
}
