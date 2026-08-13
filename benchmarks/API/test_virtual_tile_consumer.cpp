#include "MAA.hpp"

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

#if !defined(GEM5)
#error "test_virtual_tile_consumer requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

namespace {

constexpr int total_elements = 16384;
constexpr int guard_elements = 32;
constexpr size_t cache_line_bytes = 64;
constexpr size_t cache_line_elements = cache_line_bytes / sizeof(double);
constexpr size_t descriptor_spool_units = 4;
constexpr size_t descriptor_spool_slot_bytes =
    total_elements * 8 + 4 * 64;
constexpr size_t descriptor_spool_elements =
    descriptor_spool_units * descriptor_spool_slot_bytes / sizeof(double);
constexpr double scale = 3.0;
constexpr size_t cache_pollution_bytes = 32 * 1024 * 1024;

uint64_t
hashValue(uint64_t hash, double value)
{
    uint64_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    hash ^= bits;
    return hash * 1099511628211ULL;
}

} // namespace

int
main(int argc, char **argv)
{
    std::string mode = argc > 1 ? argv[1] : "native";
    int page_elements = argc > 2 ? std::atoi(argv[2]) : total_elements;
    const bool deferred_treatment = mode == "deferred";
    const std::string treatment_path =
        deferred_treatment && argc > 2 ? argv[2] : "";
    if (deferred_treatment)
        page_elements = 0;
    auto validate_treatment = [&]() -> bool {
    if (mode != "native" && mode != "native_direct" &&
        mode != "paged" && mode != "paged_overlap" &&
        mode != "paged_staged" && mode != "paged_staged_conditional" &&
        mode != "token_stream_ld" &&
        mode != "token_stream_ld_pingpong" &&
        mode != "transparent" && mode != "transparent_ready" &&
        mode != "transparent_displaced" && mode != "paged_displaced" &&
        mode != "transparent_reload_warm" &&
        mode != "transparent_reload_cold" &&
        mode != "paged_reload_warm" &&
        mode != "paged_reload_cold") {
        std::cerr << "mode must be native, native_direct, paged, "
                     "paged_overlap, "
                     "paged_staged, paged_staged_conditional, "
                     "token_stream_ld[_pingpong], transparent, "
                     "transparent_ready, transparent_displaced, "
                     "paged_displaced, transparent_reload_warm/"
                     "transparent_reload_cold, or paged_reload_warm/"
                     "paged_reload_cold"
                  << std::endl;
        return false;
    }
    if ((page_elements != 4096 && page_elements != total_elements) ||
        total_elements % page_elements != 0) {
        std::cerr << "page_elements must be 4096 or 16384" << std::endl;
        return false;
    }
    if ((mode == "transparent" || mode == "transparent_ready" ||
         mode == "transparent_displaced" || mode == "paged_displaced" ||
         mode == "transparent_reload_warm" ||
         mode == "transparent_reload_cold" ||
         mode == "token_stream_ld" ||
         mode == "token_stream_ld_pingpong") &&
        page_elements != 4096) {
        std::cerr << "cache-residency controls require four 4096-element pages"
                  << std::endl;
        return false;
    }
    return true;
    };
    if (!deferred_treatment && !validate_treatment())
        return 2;
    const bool native_4k_build =
        TILE_SIZE == 4096 && page_elements == 4096 &&
        (mode == "native" || mode == "native_direct");
    if (TILE_SIZE != total_elements && !native_4k_build) {
        std::cerr << "test requires a 16K logical tile, except for the "
                     "native 4K control"
                  << std::endl;
        return 2;
    }

    std::vector<double> source(total_elements * 8);
    std::vector<uint32_t> indices(total_elements);
    // The direct-retirement fast path operates on complete cache lines. Keep
    // explicit guards while aligning the two payload regions; arbitrary
    // unaligned application buffers remain on the existing safe fallback.
    std::vector<double> backing_storage(
        total_elements + 2 * guard_elements + cache_line_elements - 1 +
            descriptor_spool_elements,
        -1.0);
    std::vector<double> destination_storage(
        total_elements + 2 * guard_elements + cache_line_elements - 1,
        -1.0);
    std::vector<double> fence_storage(1, 0.0);
    const auto align_payload = [](double *candidate) {
        const uintptr_t address = reinterpret_cast<uintptr_t>(candidate);
        const uintptr_t aligned =
            (address + cache_line_bytes - 1) & ~(cache_line_bytes - 1);
        return reinterpret_cast<double *>(aligned);
    };
    double *backing =
        align_payload(backing_storage.data() + guard_elements);
    double *destination =
        align_payload(destination_storage.data() + guard_elements);

    for (int i = 0; i < static_cast<int>(source.size()); ++i)
        source[i] = static_cast<double>(i * 17 + 3);
    for (int i = 0; i < total_elements; ++i)
        indices[i] = (i * 97 + 13) % source.size();
    std::cout << "VIRTUAL_TILE_CONSUMER_LAYOUT mode=" << mode
              << " page_elements=" << page_elements
              << " logical_elements=" << TILE_SIZE
              << " mem_size=" << static_cast<uint64_t>(MEM_SIZE)
              << std::endl;
    std::cout << "VIRTUAL_TILE_CONSUMER_DESCRIPTOR_STORAGE units="
              << descriptor_spool_units
              << " slot_bytes=" << descriptor_spool_slot_bytes
              << " total_bytes="
              << descriptor_spool_units * descriptor_spool_slot_bytes
              << std::endl;
    std::cout << "VIRTUAL_TILE_CONSUMER_ALIGNMENT backing_mod64="
              << reinterpret_cast<uintptr_t>(backing) % cache_line_bytes
              << " destination_mod64="
              << reinterpret_cast<uintptr_t>(destination) % cache_line_bytes
              << std::endl;
    m5_checkpoint(0, 0);
    if (deferred_treatment) {
        std::ifstream treatment(treatment_path);
        std::string extra;
        if (!(treatment >> mode >> page_elements) || treatment >> extra) {
            std::cerr << "deferred treatment must contain exactly MODE PAGE"
                      << std::endl;
            return 2;
        }
        if (!validate_treatment())
            return 2;
        std::cout << "VIRTUAL_TILE_CONSUMER_TREATMENT mode=" << mode
                  << " page_elements=" << page_elements
                  << " source=deferred_file_v1" << std::endl;
    }
    const bool reload_only = mode == "paged_reload_warm" ||
                             mode == "paged_reload_cold" ||
                             mode == "transparent_reload_warm" ||
                             mode == "transparent_reload_cold";
    const bool cache_displaced = mode == "transparent_displaced" ||
                                 mode == "paged_displaced";
    const bool reload_cold = mode == "paged_reload_cold" ||
                             mode == "transparent_reload_cold";
    const bool pollute_cache = cache_displaced || reload_cold;
    const bool conditional_staged = mode == "paged_staged_conditional";
    std::vector<uint64_t> cache_pollution(
        pollute_cache ? cache_pollution_bytes / sizeof(uint64_t) : 1, 1);
    std::vector<uint32_t> conditions;
    if (conditional_staged) {
        conditions.resize(total_elements);
        for (int i = 0; i < total_elements; ++i)
            conditions[i] = i % 5 != 0;
    }

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(source.data(), source.data() + source.size());
    add_mem_region(indices.data(), indices.data() + indices.size());
    if (conditional_staged)
        add_mem_region(conditions.data(),
                       conditions.data() + conditions.size());
    add_mem_region(backing_storage.data(),
                   backing_storage.data() + backing_storage.size());
    add_mem_region(destination_storage.data(),
                   destination_storage.data() + destination_storage.size());
    add_mem_region(fence_storage.data(),
                   fence_storage.data() + fence_storage.size());

    const int min_reg = get_new_reg<int>(0);
    const int max_reg = get_new_reg<int>(total_elements);
    const int stride_reg = get_new_reg<int>(1);
    const int scale_reg = get_new_reg<double>(scale);
    const int page_min_reg = get_new_reg<int>(0);
    const int page_max_reg = get_new_reg<int>(4096);
    const int page_stride_reg = get_new_reg<int>(1);
    const int output_tile = get_new_tile<double>();

    if (!reload_only) {
        m5_work_begin(0, 0);
        m5_reset_stats(0, 0);
    }
    if (mode == "native" || mode == "native_direct") {
        const int idx_tile = get_new_tile<uint32_t>();
        const int gathered_tile = get_new_tile<double>();
        for (int offset = 0; offset < total_elements;
             offset += page_elements) {
            const int count = std::min(page_elements,
                                       total_elements - offset);
            maa_const(0, min_reg);
            maa_const(count, max_reg);
            if (mode == "native") {
                wait_ready(idx_tile);
                maa_stream_load<uint32_t>(
                    indices.data() + offset, min_reg, max_reg, stride_reg,
                    idx_tile);
                maa_indirect_load<double>(source.data(), idx_tile,
                                          gathered_tile);
            } else {
                maa_indirect_load_index<double>(
                    source.data(), indices.data() + offset, gathered_tile,
                    min_reg, max_reg, stride_reg);
            }
            maa_alu_scalar<double>(gathered_tile, scale_reg, output_tile,
                                   Operation_t::MUL_OP);
            maa_stream_store<double>(destination + offset, min_reg, max_reg,
                                     stride_reg, output_tile);
        }
    } else {
        const int completion_tile = get_new_tile<double>();
        const int page_tile = get_new_tile<double>();
        const int alternate_page_tile = get_new_tile<double>();

        maa_const(0, min_reg);
        maa_const(total_elements, max_reg);
        if (mode == "paged_staged" ||
            mode == "paged_staged_conditional") {
            const int idx_tile = get_new_tile<uint32_t>();
            const int cond_tile = conditional_staged
                                      ? get_new_tile<uint32_t>()
                                      : -1;
            maa_stream_load<uint32_t>(indices.data(), min_reg, max_reg,
                                      stride_reg, idx_tile);
            if (cond_tile != -1)
                maa_stream_load<uint32_t>(conditions.data(), min_reg,
                                          max_reg, stride_reg, cond_tile);
            maa_indirect_load_virtual<double>(source.data(), idx_tile,
                                              completion_tile, backing,
                                              cond_tile);
        } else {
            maa_indirect_load_virtual_index<double>(
                source.data(), indices.data(), completion_tile, backing,
                min_reg, max_reg, stride_reg);
        }
        const bool transparent = mode == "transparent" ||
                                 mode == "transparent_ready" ||
                                 mode == "transparent_displaced" ||
                                 mode == "transparent_reload_warm" ||
                                 mode == "transparent_reload_cold";
        const bool wait_before_consumer = mode == "transparent_ready" ||
                                          cache_displaced || reload_only;
        const bool overlap_pages = mode == "paged_overlap";
        const bool token_stream_ld = mode == "token_stream_ld";
        const bool token_stream_ld_pingpong =
            mode == "token_stream_ld_pingpong";
        if (wait_before_consumer) {
            // The ready control removes producer/consumer overlap without
            // changing the consumer. Displaced and reload-only modes share
            // this boundary before their matched cache walk or stats reset.
            wait_ready(completion_tile);
        }
        if (pollute_cache) {
            // Keep the 32 MiB walk identical for transparent and paged
            // consumers. Full-path displaced controls charge it in the ROI;
            // reload-only controls reset stats immediately afterward.
            volatile uint64_t sink = 0;
            constexpr size_t words_per_cache_line = 64 / sizeof(uint64_t);
            for (size_t i = 0; i < cache_pollution.size();
                 i += words_per_cache_line)
                sink += cache_pollution[i];
            asm volatile("" : : "r"(sink) : "memory");
            std::cout << "VIRTUAL_TILE_CONSUMER_POLLUTION bytes="
                      << cache_pollution_bytes << std::endl;
        }
        if (reload_only) {
            m5_work_begin(0, 0);
            m5_reset_stats(0, 0);
        }
        if (transparent) {
            // Application code submits one logical consumer.  Page-ready
            // gating, coherent backing reloads, physical-tile remapping, and
            // the native ALU/store chain are owned by the MAA controller.
            maa_virtual_tile_alu_scalar_store<double>(
                backing, destination, completion_tile, page_tile,
                output_tile, scale_reg, page_min_reg, page_max_reg,
                page_stride_reg, Operation_t::MUL_OP);
        } else if (!overlap_pages && !token_stream_ld &&
                   !token_stream_ld_pingpong &&
                   !wait_before_consumer) {
            wait_ready(completion_tile);
        }

        if (token_stream_ld_pingpong) {
            for (int offset = 0; offset < total_elements;
                 offset += 2 * page_elements) {
                const int first_count = std::min(
                    page_elements, total_elements - offset);
                const int second_offset = offset + page_elements;
                const int second_count = std::min(
                    page_elements, total_elements - second_offset);
                // The two tiles are unused on the first pair.  Waiting on
                // them before submitting the materializers unnecessarily
                // serializes admission behind the live producer.  Later
                // pairs still wait for their prior consumers to release the
                // physical pages before reuse.
                if (offset != 0) {
                    wait_ready(page_tile);
                    wait_ready(alternate_page_tile);
                }
                maa_const(0, min_reg);
                maa_const(first_count, max_reg);
                maa_stream_load_virtual_page<double>(
                    backing + offset, completion_tile, min_reg, max_reg,
                    stride_reg, page_tile);
                if (second_count > 0) {
                    maa_const(second_count, max_reg);
                    maa_stream_load_virtual_page<double>(
                        backing + second_offset, completion_tile, min_reg,
                        max_reg, stride_reg, alternate_page_tile);
                }
                maa_const(first_count, max_reg);
                maa_alu_scalar<double>(page_tile, scale_reg, output_tile,
                                       Operation_t::MUL_OP);
                maa_stream_store<double>(
                    destination + offset, min_reg, max_reg, stride_reg,
                    output_tile);
                if (second_count > 0) {
                    maa_const(second_count, max_reg);
                    maa_alu_scalar<double>(
                        alternate_page_tile, scale_reg, output_tile,
                        Operation_t::MUL_OP);
                    maa_stream_store<double>(
                        destination + second_offset, min_reg, max_reg,
                        stride_reg, output_tile);
                }
            }
            wait_ready(completion_tile);
        } else if (!transparent) {
            for (int offset = 0; offset < total_elements;
                 offset += page_elements) {
                const int count = std::min(page_elements,
                                           total_elements - offset);
                if (overlap_pages)
                    wait_virtual_page(completion_tile,
                                      offset / page_elements);
                // The first materialization owns a fresh page tile.  Admit it
                // immediately so exact producer WriteResp payloads can be
                // handed off while the gather is still running; only reused
                // pages need a readiness wait.
                if (!token_stream_ld || offset != 0)
                    wait_ready(page_tile);
                maa_const(0, min_reg);
                maa_const(count, max_reg);
                if (token_stream_ld) {
                    maa_stream_load_virtual_page<double>(
                        backing + offset, completion_tile, min_reg, max_reg,
                        stride_reg, page_tile);
                } else {
                    maa_stream_load<double>(backing + offset, min_reg,
                                            max_reg, stride_reg, page_tile);
                }
                maa_alu_scalar<double>(page_tile, scale_reg, output_tile,
                                       Operation_t::MUL_OP);
                maa_stream_store<double>(destination + offset, min_reg,
                                         max_reg, stride_reg, output_tile);
            }
            if (overlap_pages || token_stream_ld)
                wait_ready(completion_tile);
        }
    }

    // A source tile becomes ready before its stream store finishes. Reusing it
    // as a destination creates a dependency fence that includes final stores.
    maa_const(0, min_reg);
    maa_const(1, max_reg);
    maa_stream_load<double>(fence_storage.data(), min_reg, max_reg, stride_reg,
                            output_tile);
    wait_ready(output_tile);
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    int errors = 0;
    uint64_t hash = 1469598103934665603ULL;
    for (int i = 0; i < total_elements; ++i) {
        const double gathered = source[indices[i]];
        const bool selected = !conditional_staged || conditions[i] != 0;
        const double expected_backing = selected ? gathered : -1.0;
        const double expected = expected_backing * scale;
        if (destination[i] != expected && errors++ < 10) {
            std::cerr << "destination mismatch[" << i << "]: got "
                      << destination[i] << ", expected " << expected
                      << std::endl;
        }
        if (mode != "native" && mode != "native_direct" &&
            backing[i] != expected_backing && errors++ < 10) {
            std::cerr << "backing mismatch[" << i << "]: got "
                      << backing[i] << ", expected " << expected_backing
                      << std::endl;
        }
        hash = hashValue(hash, destination[i]);
    }
    for (int i = 0; i < guard_elements; ++i) {
        if (backing[i - guard_elements] != -1.0 && errors++ < 10)
            std::cerr << "backing prefix guard corrupted[" << i << "]"
                      << std::endl;
        if (backing[total_elements + i] != -1.0 && errors++ < 10)
            std::cerr << "backing suffix guard corrupted[" << i << "]"
                      << std::endl;
        if (destination[i - guard_elements] != -1.0 && errors++ < 10)
            std::cerr << "destination prefix guard corrupted[" << i << "]"
                      << std::endl;
        if (destination[total_elements + i] != -1.0 && errors++ < 10)
            std::cerr << "destination suffix guard corrupted[" << i << "]"
                      << std::endl;
    }

    std::cout << "VIRTUAL_TILE_CONSUMER_RESULT mode=" << mode
              << " page_elements=" << page_elements << " hash=" << hash
              << " errors=" << errors << std::endl;
    std::cout << "ROI Ended" << std::endl;
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}
