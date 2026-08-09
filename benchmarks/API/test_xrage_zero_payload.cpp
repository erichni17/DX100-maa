#include "MAA.hpp"

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <string>
#include <vector>

#if !defined(GEM5)
#error "test_xrage_zero_payload requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace {

uint64_t
bits(double value)
{
    uint64_t result;
    std::memcpy(&result, &value, sizeof(result));
    return result;
}

double
fromBits(uint64_t value)
{
    double result;
    std::memcpy(&result, &value, sizeof(result));
    return result;
}

void
hashWord(uint64_t &hash, double value)
{
    const uint64_t value_bits = bits(value);
    for (unsigned byte = 0; byte < sizeof(value_bits); ++byte) {
        hash ^= (value_bits >> (byte * 8)) & 0xff;
        hash *= 1099511628211ULL;
    }
}

} // anonymous namespace

int
main(int argc, char **argv)
{
    const int requested = argc > 1 ? std::atoi(argv[1]) : 4096;
    const std::string mode = argc > 2 ? argv[2] : "suite";
    const bool known_mode = mode == "suite" || mode == "split" ||
        mode == "too_large" || mode == "ac_alias" || mode == "bc_alias" ||
        mode == "drain" || mode == "reset";
    if (requested <= 0 || !known_mode) {
        std::cerr << "usage: test_xrage_zero_payload N "
                     "suite|split|too_large|ac_alias|bc_alias|drain|reset"
                  << std::endl;
        return 2;
    }
    if (mode != "split" && mode != "too_large" && requested > 4096) {
        std::cerr << "single-descriptor modes require N <= 4096" << std::endl;
        return 2;
    }

    const int largest = std::max(requested, 4096);
    const int source_elements = largest * 4 + 64;
    std::vector<double> source(source_elements);
    std::vector<uint32_t> indices(largest);
    constexpr int guard_words = 16;
    const uint64_t guard_bits = 0x7ff8deadbeef0042ULL;
    const double guard = fromBits(guard_bits);
    std::vector<double> output_storage(largest + 2 * guard_words, guard);
    double *output = output_storage.data() + guard_words;
    std::vector<double> reference(largest);

    for (int i = 0; i < source_elements; ++i) {
        const int signed_value = (i * 37 + 11) % 200003 - 100001;
        source[i] = static_cast<double>(signed_value) / 32.0;
    }
    source[0] = 0.0;
    source[1] = -0.0;
    source[2] = fromBits(0x7ff8000000000042ULL);
    source[3] = std::numeric_limits<double>::infinity();
    source[4] = -std::numeric_limits<double>::infinity();
    for (int i = 0; i < largest; ++i) {
        if (i % 19 == 0)
            indices[i] = 13; // fanout and duplicate
        else if (i % 23 == 0)
            indices[i] = static_cast<uint32_t>((i * 5 + 3) % 8); // one line
        else if (i % 29 == 0)
            indices[i] = static_cast<uint32_t>(((i / 29) * 8 + 7) %
                                               source_elements); // boundary
        else
            indices[i] = static_cast<uint32_t>(
                (static_cast<uint64_t>(i) * 1103515245ULL + 12345) %
                source_elements);
    }

    // The B/C negative deliberately gives the same registered bytes both
    // types. Hardware must reject before any output can corrupt future B.
    std::vector<uint64_t> bc_storage(
        (static_cast<size_t>(largest) * sizeof(double) + 7) / 8, 0);
    uint32_t *bc_indices =
        reinterpret_cast<uint32_t *>(bc_storage.data());
    double *bc_output = reinterpret_cast<double *>(bc_storage.data());
    for (int i = 0; i < largest; ++i)
        bc_indices[i] = indices[i];

    std::cout << "VIRTUAL_GATHER64_LAYOUT mem_size="
              << static_cast<uint64_t>(MEM_SIZE) << " mode=" << mode
              << " strict_limit=4096" << std::endl;
    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(source.data(), source.data() + source.size());
    if (mode == "bc_alias") {
        add_mem_region(bc_storage.data(),
                       bc_storage.data() + bc_storage.size());
    } else {
        add_mem_region(indices.data(), indices.data() + indices.size());
        if (mode != "ac_alias")
            add_mem_region(output_storage.data(),
                           output_storage.data() + output_storage.size());
    }

    const int min_reg = get_new_reg<int>(0);
    const int max_reg = get_new_reg<int>(1);
    const int stride_reg = get_new_reg<int>(1);
    const int scalar_reg = get_new_reg<double>(3.0);
    const int completion_tile = get_new_tile<uint32_t>();

    int errors = 0;
    int descriptors = 0;
    uint64_t logical_words = 0;
    uint64_t hash = 1469598103934665603ULL;
    auto submit = [&](int begin, int count, double scalar, double *destination,
                      uint32_t *index_base) {
        set_reg<int>(min_reg, begin);
        set_reg<int>(max_reg, begin + count);
        set_reg<int>(stride_reg, 1);
        set_reg<double>(scalar_reg, scalar);
        for (int i = 0; i < count; ++i)
            reference[i] = source[index_base[begin + i]] * scalar;
        maa_indirect_load_virtual_index_scalar<double>(
            source.data(), index_base, completion_tile, destination,
            min_reg, max_reg, stride_reg, scalar_reg, Operation_t::MUL_OP);
        descriptors++;
        logical_words += count;
    };
    auto verify = [&](int count, double *destination) {
        for (int i = 0; i < count; ++i) {
            if (bits(destination[i]) != bits(reference[i])) {
                if (errors < 10)
                    std::cerr << "bit mismatch[" << i << "]: got 0x"
                              << std::hex << bits(destination[i])
                              << " expected 0x" << bits(reference[i])
                              << std::dec << std::endl;
                errors++;
            }
            hashWord(hash, destination[i]);
        }
    };

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
    if (mode == "suite") {
        const int counts[] = {1, 15, 16, 17, 4095, 4096};
        const double scalars[] = {
            3.0, -2.0, -0.0, fromBits(0x3ff0000000000001ULL), 0.5, -3.0};
        for (unsigned test = 0; test < sizeof(counts) / sizeof(counts[0]);
             ++test) {
            std::fill(output, output + counts[test], guard);
            submit(0, counts[test], scalars[test], output, indices.data());
            wait_ready(completion_tile);
            verify(counts[test], output);
        }
    } else if (mode == "split") {
        for (int begin = 0; begin < requested; begin += 4096) {
            const int count = std::min(4096, requested - begin);
            submit(begin, count, 3.0, output + begin, indices.data());
            wait_ready(completion_tile);
            verify(count, output + begin);
        }
    } else {
        uint32_t *index_base =
            mode == "bc_alias" ? bc_indices : indices.data();
        double *destination = mode == "ac_alias" ? source.data() :
            (mode == "bc_alias" ? bc_output : output);
        submit(0, requested, 3.0, destination, index_base);
        if (mode == "drain") {
            std::cout << "XRAGE_ZERO_PAYLOAD_LIVE_DRAIN_REQUEST" << std::endl;
            m5_checkpoint(0, 0);
            std::cerr << "live zero-payload checkpoint unexpectedly returned"
                      << std::endl;
            return 3;
        }
        if (mode == "reset") {
            std::cout << "XRAGE_ZERO_PAYLOAD_LIVE_RESET_REQUEST" << std::endl;
            m5_reset_stats(0, 0);
            std::cerr << "live zero-payload stats reset unexpectedly returned"
                      << std::endl;
            return 3;
        }
        wait_ready(completion_tile);
        verify(requested, destination);
    }
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    if (mode != "ac_alias" && mode != "bc_alias") {
        for (int i = 0; i < guard_words; ++i) {
            if (bits(output_storage[i]) != guard_bits ||
                bits(output_storage[guard_words + largest + i]) !=
                    guard_bits) {
                if (errors < 10)
                    std::cerr << "output guard corrupted at " << i
                              << std::endl;
                errors++;
            }
        }
    }

    std::cout << "VIRTUAL_GATHER64_RESULT mode=" << mode
              << " logical=" << logical_words
              << " descriptors=" << descriptors << " hash=" << hash
              << " errors=" << errors << std::endl;
    std::cout << "XRAGE_ZERO_PAYLOAD_RESULT mode=" << mode
              << " logical=" << logical_words
              << " descriptors=" << descriptors << " hash=" << hash
              << " errors=" << errors << std::endl;
    std::cout << "ROI Ended" << std::endl;
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}
