// Dedicated backing-RFO bracket micro.  This deliberately does not modify
// test_virtual_tile_consumer, which is an accepted guest with a broader ABI.
#include "MAA.hpp"

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

#if !defined(GEM5)
#error "test_virtual_tile_backing_rfo requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace {

constexpr int kElements = 16 * 1024;
constexpr int kGuardElements = 32;
constexpr size_t kLineBytes = 64;
using Value = double;
constexpr int kLines = kElements * sizeof(Value) / kLineBytes;
static_assert(kLines == 2048, "the bracket requires exactly 2048 lines");

Value *alignLine(Value *candidate)
{
    const uintptr_t address = reinterpret_cast<uintptr_t>(candidate);
    const uintptr_t aligned = (address + kLineBytes - 1) & ~(kLineBytes - 1);
    return reinterpret_cast<Value *>(aligned);
}

uint64_t hashBytes(const void *data, size_t size)
{
    const auto *bytes = static_cast<const uint8_t *>(data);
    uint64_t hash = 1469598103934665603ULL;
    for (size_t i = 0; i < size; ++i) {
        hash ^= bytes[i];
        hash *= 1099511628211ULL;
    }
    return hash;
}

// One volatile read/write-self per 64-byte backing line gives the CPU a
// writable resident copy without changing the backing byte sequence.
uint64_t preallocateBacking(Value *backing)
{
    auto *bytes = reinterpret_cast<volatile uint8_t *>(backing);
    uint64_t sink = 0;
    for (size_t offset = 0; offset < kElements * sizeof(Value);
         offset += kLineBytes) {
        const uint8_t old = bytes[offset];
        bytes[offset] = old;
        sink += old;
    }
    asm volatile("" : : "r"(sink) : "memory");
    return sink;
}

bool readArm(const char *path, std::string *arm)
{
    std::ifstream selector(path);
    std::string extra;
    return selector >> *arm && !(selector >> extra) &&
           (*arm == "cold" || *arm == "ideal" || *arm == "charged");
}

bool validArm(const std::string &arm)
{
    return arm == "cold" || arm == "ideal" || arm == "charged";
}

} // namespace

int main(int argc, char **argv)
{
    if (argc != 2) {
        std::cerr << "usage: test_virtual_tile_backing_rfo ARM_FILE"
                  << std::endl;
        return 2;
    }
    if (TILE_SIZE != kElements) {
        std::cerr << "requires TILE_SIZE=16384" << std::endl;
        return 2;
    }

    std::vector<Value> source(kElements * 8);
    std::vector<uint32_t> index(kElements);
    std::vector<Value> backing_storage(
        kElements + 2 * kGuardElements + kLineBytes / sizeof(Value), -1.0);
    std::vector<Value> destination_storage(
        kElements + 2 * kGuardElements + kLineBytes / sizeof(Value), -1.0);
    std::vector<Value> input(1, 3.0);
    std::vector<Value> fence(1, 0.0);
    Value *backing = alignLine(backing_storage.data() + kGuardElements);
    Value *destination =
        alignLine(destination_storage.data() + kGuardElements);
    for (int i = 0; i < static_cast<int>(source.size()); ++i)
        source[i] = static_cast<Value>(i * 17 + 3);
    for (int i = 0; i < kElements; ++i)
        index[i] = (i * 97 + 13) % source.size();

    std::cout << "BACKING_RFO_LAYOUT logical=16384 physical=4096 "
              << "backing_lines=" << kLines
              << " backing_mod64="
              << reinterpret_cast<uintptr_t>(backing) % kLineBytes
              << " destination_mod64="
              << reinterpret_cast<uintptr_t>(destination) % kLineBytes
              << std::endl;
    m5_checkpoint(0, 0);

    std::string arm(argv[1]);
    // se.py passes its --options string verbatim as argv[1].  Accept the
    // explicit token used by the runner, while retaining file parsing for
    // stand-alone replay from an immutable selector artifact.
    if (!validArm(arm) && !readArm(argv[1], &arm)) {
        std::cerr << "arm must be cold, ideal, charged, or a selector file"
                  << std::endl;
        return 2;
    }
    std::cout << "BACKING_RFO_ARM arm=" << arm << std::endl;

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(source.data(), source.data() + source.size());
    add_mem_region(index.data(), index.data() + index.size());
    add_mem_region(backing_storage.data(),
                   backing_storage.data() + backing_storage.size());
    add_mem_region(destination_storage.data(),
                   destination_storage.data() + destination_storage.size());
    add_mem_region(input.data(), input.data() + input.size());
    add_mem_region(fence.data(), fence.data() + fence.size());

    const uint64_t before = hashBytes(backing, kElements * sizeof(Value));
    uint64_t prealloc_sink = 0;
    if (arm == "ideal") {
        prealloc_sink = preallocateBacking(backing);
        const uint64_t after = hashBytes(backing, kElements * sizeof(Value));
        if (before != after) {
            std::cerr << "preallocation changed backing bytes" << std::endl;
            return 1;
        }
        m5_reset_stats(0, 0);
        m5_work_begin(0, 0);
    } else {
        m5_reset_stats(0, 0);
        m5_work_begin(0, 0);
        if (arm == "charged") {
            prealloc_sink = preallocateBacking(backing);
            const uint64_t after =
                hashBytes(backing, kElements * sizeof(Value));
            if (before != after) {
                std::cerr << "preallocation changed backing bytes"
                          << std::endl;
                return 1;
            }
        }
    }
    std::cout << "BACKING_RFO_PREALLOC arm=" << arm
              << " lines=" << (arm == "cold" ? 0 : kLines)
              << " bytes=" << (arm == "cold" ? 0 : kLines * kLineBytes)
              << " hash_before=" << before
              << " sink=" << prealloc_sink << std::endl;

    const int min_reg = get_new_reg<int>(0);
    const int max_reg = get_new_reg<int>(kElements);
    const int stride_reg = get_new_reg<int>(1);
    const int page_min_reg = get_new_reg<int>(0);
    const int page_max_reg = get_new_reg<int>(4096);
    const int page_stride_reg = get_new_reg<int>(1);
    const int scale_reg = get_new_reg<Value>(input[0]);
    const int completion_tile = get_new_tile<Value>();
    const int page_tile = get_new_tile<Value>();
    const int output_tile = get_new_tile<Value>();

    maa_indirect_load_virtual_index<Value>(source.data(), index.data(),
                                           completion_tile, backing, min_reg,
                                           max_reg, stride_reg);
    maa_virtual_tile_alu_scalar_store<Value>(
        backing, destination, completion_tile, page_tile, output_tile,
        scale_reg, page_min_reg, page_max_reg, page_stride_reg,
        Operation_t::MUL_OP);

    maa_const(0, min_reg);
    maa_const(1, max_reg);
    maa_stream_load<Value>(fence.data(), min_reg, max_reg, stride_reg,
                           output_tile);
    wait_ready(output_tile);
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    int errors = 0;
    uint64_t output_hash = 1469598103934665603ULL;
    for (int i = 0; i < kElements; ++i) {
        const Value expected_backing = source[index[i]];
        const Value expected = expected_backing * input[0];
        if (backing[i] != expected_backing && errors++ < 10)
            std::cerr << "backing mismatch[" << i << "]" << std::endl;
        if (destination[i] != expected && errors++ < 10)
            std::cerr << "destination mismatch[" << i << "]" << std::endl;
        uint64_t bits = 0;
        std::memcpy(&bits, &destination[i], sizeof(bits));
        output_hash ^= bits;
        output_hash *= 1099511628211ULL;
    }
    for (int i = 0; i < kGuardElements; ++i) {
        if (backing[i - kGuardElements] != -1.0 && errors++ < 10)
            std::cerr << "backing prefix guard corrupted" << std::endl;
        if (backing[kElements + i] != -1.0 && errors++ < 10)
            std::cerr << "backing suffix guard corrupted" << std::endl;
        if (destination[i - kGuardElements] != -1.0 && errors++ < 10)
            std::cerr << "destination prefix guard corrupted" << std::endl;
        if (destination[kElements + i] != -1.0 && errors++ < 10)
            std::cerr << "destination suffix guard corrupted" << std::endl;
    }
    std::cout << "BACKING_RFO_RESULT arm=" << arm << " hash=" << output_hash
              << " errors=" << errors << std::endl;
    std::cout << "ROI Ended" << std::endl;
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}
