#include "MAA.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <string>

#if !defined(GEM5)
#error "test_fused_p16_repeat_liveness requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace
{

constexpr std::size_t MaxOperations = 64;
constexpr std::size_t Pages = 4;
constexpr std::size_t PageElements = 4096;
constexpr std::size_t LogicalElements = Pages * PageElements;
constexpr std::size_t SourceElements = 4096;
constexpr uint32_t Sentinel = 0x7fc00001U;

using Sources =
    std::array<std::array<float, SourceElements>, MaxOperations>;
using LogicalFloats =
    std::array<std::array<float, LogicalElements>, MaxOperations>;
using LogicalIndices =
    std::array<std::array<uint32_t, LogicalElements>, MaxOperations>;

alignas(64) Sources sources;
alignas(64) LogicalIndices colidx;
alignas(64) LogicalFloats coefficients;
alignas(64) LogicalFloats products;
alignas(64) LogicalFloats referenceProducts;
alignas(64) std::array<uint32_t, LogicalElements> qIndices;
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
hashWords(const uint32_t *values, std::size_t count)
{
    uint64_t hash = 1469598103934665603ULL;
    for (std::size_t index = 0; index < count; ++index) {
        const uint32_t word = values[index];
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

uint64_t
mixHash(uint64_t hash, uint64_t value)
{
    for (unsigned byte = 0; byte < sizeof(value); ++byte) {
        hash ^= (value >> (byte * 8)) & 0xffU;
        hash *= 1099511628211ULL;
    }
    return hash;
}

void
initialize()
{
    for (std::size_t ordinal = 0; ordinal < LogicalElements; ++ordinal)
        qIndices[ordinal] = static_cast<uint32_t>(ordinal);

    for (std::size_t operation = 0; operation < MaxOperations;
         ++operation) {
        for (std::size_t index = 0; index < SourceElements; ++index) {
            sources[operation][index] = fromBits(
                0x3e800001U + static_cast<uint32_t>(operation * 4096U) +
                static_cast<uint32_t>(index));
        }
        uint32_t pseudo = 0x6d2b79f5U;
        for (std::size_t ordinal = 0; ordinal < LogicalElements;
             ++ordinal) {
            if (ordinal < PageElements)
                colidx[operation][ordinal] = 7;
            else if (ordinal < 2 * PageElements)
                colidx[operation][ordinal] = 64 + (ordinal & 15U);
            else if (ordinal < 3 * PageElements)
                colidx[operation][ordinal] =
                    static_cast<uint32_t>((ordinal * 257U) & 4095U);
            else {
                pseudo ^= pseudo << 13;
                pseudo ^= pseudo >> 17;
                pseudo ^= pseudo << 5;
                colidx[operation][ordinal] = pseudo & 4095U;
            }
            coefficients[operation][ordinal] = fromBits(
                0x3f000001U + static_cast<uint32_t>(operation * 16384U) +
                static_cast<uint32_t>(ordinal));
            referenceProducts[operation][ordinal] =
                sources[operation][colidx[operation][ordinal]] *
                coefficients[operation][ordinal];
            products[operation][ordinal] = fromBits(Sentinel);
            qOutput[operation][ordinal] = 0.0f;
        }
    }
}

unsigned
readOperations(const char *selectorPath)
{
    std::ifstream selector(selectorPath);
    unsigned operations = 0;
    std::string extra;
    if (!(selector >> operations) || selector >> extra ||
        (operations != 16 && operations != 32 && operations != 64)) {
        std::cerr << "FUSED_P16_REPEAT_ERROR invalid_selector="
                  << selectorPath << std::endl;
        return 0;
    }
    return operations;
}

struct TilesAndRegisters
{
    int logicalMin;
    int logicalMax;
    int logicalStride;
    int pageMin;
    int pageMax;
    int pageStride;
    int fusedCompletion;
    int qCompletion;
    int qIndexTile;
};

TilesAndRegisters
allocateTilesAndRegisters()
{
    return {
        get_new_reg<int>(0),
        get_new_reg<int>(LogicalElements),
        get_new_reg<int>(1),
        get_new_reg<int>(0),
        get_new_reg<int>(PageElements),
        get_new_reg<int>(1),
        get_new_tile<float>(),
        get_new_tile<float>(),
        get_new_tile<uint32_t>(),
    };
}

void
runOperation(std::size_t operation, const TilesAndRegisters &ids)
{
    const uint64_t generation = operation + 1;
    maa_indirect_load_virtual_index_product_fp32(
        sources[operation].data(), colidx[operation].data(),
        coefficients[operation].data(), ids.fusedCompletion,
        products[operation].data(), ids.logicalMin, ids.logicalMax,
        ids.logicalStride);
    wait_ready(ids.fusedCompletion);

    maa_indirect_rmw_vector_soa_jit_page_fed_open<float>(
        qOutput[operation].data(), products[operation].data(),
        ids.qCompletion, Operation_t::ADD_OP, generation);
    for (std::size_t page = 0; page < Pages; ++page) {
        maa_stream_load<uint32_t>(
            qIndices.data() + page * PageElements, ids.pageMin,
            ids.pageMax, ids.pageStride, ids.qIndexTile);
        wait_ready(ids.qIndexTile);
        maa_soa_jit_page_fed_admit(generation, page, ids.qIndexTile);
    }
    maa_soa_jit_page_fed_close(generation);
    wait_ready(ids.qCompletion);
}

std::size_t
verifyOperation(std::size_t operation, uint64_t &inputHash,
                uint64_t &referenceHash, uint64_t &productHash,
                uint64_t &qHash)
{
    std::size_t errors = 0;
    std::size_t sentinels = 0;
    for (std::size_t ordinal = 0; ordinal < LogicalElements; ++ordinal) {
        const uint32_t expected = bits(referenceProducts[operation][ordinal]);
        const uint32_t product = bits(products[operation][ordinal]);
        const uint32_t q = bits(qOutput[operation][ordinal]);
        if (product == Sentinel)
            ++sentinels;
        if (product != expected || q != expected)
            ++errors;
    }
    inputHash = hashFloats(sources[operation].data(), SourceElements);
    inputHash = mixHash(
        inputHash,
        hashWords(colidx[operation].data(), LogicalElements));
    inputHash = mixHash(
        inputHash,
        hashFloats(coefficients[operation].data(), LogicalElements));
    referenceHash = hashFloats(
        referenceProducts[operation].data(), LogicalElements);
    productHash = hashFloats(products[operation].data(), LogicalElements);
    qHash = hashFloats(qOutput[operation].data(), LogicalElements);
    if (referenceHash != productHash || productHash != qHash ||
        sentinels != 0)
        ++errors;
    return errors;
}

} // anonymous namespace

int
main(int argc, char **argv)
{
    static_assert(TILE_SIZE == LogicalElements);
    initialize();
    std::cout << "FUSED_P16_REPEAT_LAYOUT logical=16384 pages=4 "
                 "page_elements=4096 source_elements=4096 max_operations=64 "
                 "segments=all_same,same_line,cross_page,pseudorandom "
                 "virtual_p_allocation_bytes=0 virtual_p_traffic_bytes=0 "
                 "product_publisher_lines=0 hidden_spill_bytes=0 "
                 "global_fallbacks=0"
              << std::endl;
    m5_checkpoint(0, 0);

    if (argc != 3 || std::string(argv[1]) != "MAA_DEFERRED") {
        std::cerr << "FUSED_P16_REPEAT_ERROR usage=MAA_DEFERRED_selector"
                  << std::endl;
        m5_exit(1);
        return 2;
    }
    const unsigned operations = readOperations(argv[2]);
    if (operations == 0) {
        m5_exit(1);
        return 2;
    }

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(&sources[0][0], &sources[0][0] + sources.size() *
                       SourceElements);
    add_mem_region(&colidx[0][0], &colidx[0][0] + colidx.size() *
                       LogicalElements);
    add_mem_region(&coefficients[0][0],
                   &coefficients[0][0] + coefficients.size() *
                       LogicalElements);
    add_mem_region(&products[0][0], &products[0][0] + products.size() *
                       LogicalElements);
    add_mem_region(qIndices.data(), qIndices.data() + qIndices.size());
    add_mem_region(&qOutput[0][0], &qOutput[0][0] + qOutput.size() *
                       LogicalElements);
    const TilesAndRegisters ids = allocateTilesAndRegisters();

    std::size_t totalErrors = 0;
    uint64_t rollingHash = 1469598103934665603ULL;
    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
    for (std::size_t operation = 0; operation < operations; ++operation) {
        runOperation(operation, ids);
        uint64_t inputHash = 0;
        uint64_t referenceHash = 0;
        uint64_t productHash = 0;
        uint64_t qHash = 0;
        const std::size_t errors = verifyOperation(
            operation, inputHash, referenceHash, productHash, qHash);
        totalErrors += errors;
        rollingHash = mixHash(rollingHash, inputHash);
        rollingHash = mixHash(rollingHash, productHash);
        std::cout << "FUSED_P16_REPEAT_PROGRESS operation="
                  << (operation + 1) << '/' << operations
                  << " producer_token=" << ids.fusedCompletion
                  << " producer_generation=" << (operation + 1)
                  << " q_generation=" << (operation + 1)
                  << " input_hash=" << inputHash
                  << " reference_hash=" << referenceHash
                  << " product_hash=" << productHash
                  << " q_hash=" << qHash << " errors=" << errors
                  << std::endl;
        m5_dump_reset_stats(0, 0);
    }
    std::cout << "FUSED_P16_REPEAT_RESULT operations=" << operations
              << " completed=" << operations
              << " rolling_hash=" << rollingHash
              << " errors=" << totalErrors << std::endl;
    m5_work_end(0, 0);
    m5_exit(totalErrors == 0 ? 0 : 1);
    return totalErrors == 0 ? 0 : 1;
}
