#include "MAA.hpp"

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

#if !defined(GEM5)
#error "test_fused_direct_transform requires the GEM5 API"
#endif

#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

extern "C" int omp_get_thread_num();

namespace {

constexpr double Sentinel = -1.0;

bool
waitForPartialOutput(const std::vector<double> &output,
                     const std::string &label, size_t probe)
{
    if (probe >= output.size())
        return false;
    volatile const double *values = output.data();
    while (values[probe] == Sentinel) {
    }
    std::cout << "FUSED_DIRECT_PARTIAL_OUTPUT label=" << label
              << " probe=" << probe << " elements=" << output.size()
              << std::endl;
    return true;
}

void
hashDouble(uint64_t &hash, double value)
{
    uint64_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    for (int byte = 0; byte < 8; ++byte) {
        hash ^= (bits >> (byte * 8)) & 0xff;
        hash *= 1099511628211ULL;
    }
}

double
sourceAValue(int index)
{
    return static_cast<double>(index * 17 + 3) / 8.0;
}

double
sourceXValue(int index)
{
    return static_cast<double>(index * 29 + 11) / 16.0;
}

uint32_t
indexBValue(int index, int n)
{
    return static_cast<uint32_t>((index * 97 + 13) % n);
}

uint32_t
indexJValue(int index, int n)
{
    return static_cast<uint32_t>((index * 53 + 7) % n);
}

enum class MultiPhase
{
    AConflict,
    BConflict,
    CConflict,
    Disjoint,
};

const char *
multiPhaseName(MultiPhase phase)
{
    switch (phase) {
      case MultiPhase::AConflict:
        return "a_overlap";
      case MultiPhase::BConflict:
        return "b_overlap";
      case MultiPhase::CConflict:
        return "c_overlap";
      case MultiPhase::Disjoint:
        return "disjoint";
    }
    return "invalid";
}

int
runMultiPhase(MultiPhase phase, int n, std::vector<double> &source_a,
              std::vector<uint64_t> &index_b_storage,
              std::vector<double> &destination_c,
              const std::vector<double> &source_x,
              const std::vector<uint32_t> &index_j,
              std::vector<double> &destination_d, uint64_t &hash)
{
    uint32_t *index_b =
        reinterpret_cast<uint32_t *>(index_b_storage.data());
    std::fill(index_b_storage.begin(), index_b_storage.end(), 0);
    for (int i = 0; i < n; ++i) {
        source_a[i] = sourceAValue(i);
        index_b[i] = indexBValue(i, n);
        destination_c[i] = Sentinel;
        destination_d[i] = Sentinel;
    }
    const size_t first_probe = static_cast<size_t>(
        std::min_element(index_b, index_b + n) - index_b);

    std::atomic<bool> release_second{false};
    std::atomic<bool> missed_live_trigger{false};
    const char *phase_name = multiPhaseName(phase);

#pragma omp parallel num_threads(2) shared(release_second, missed_live_trigger)
    {
        const int thread = omp_get_thread_num();
        const int min_reg = thread == 0 ? 0 : 8;
        const int max_reg = min_reg + 1;
        const int stride_reg = min_reg + 2;
        const int scale_reg = min_reg + 3;
        const int index_tile = thread == 0 ? 0 : 8;
        const int completion_tile = index_tile + 1;
        set_reg<int>(min_reg, 0);
        set_reg<int>(max_reg, n);
        set_reg<int>(stride_reg, 1);
        set_reg<double>(scale_reg, thread == 0 ? 3.0 : 5.0);
        if (thread == 0) {
            maa_stream_load<uint32_t>(index_b, min_reg, max_reg, stride_reg,
                                      index_tile);
        } else {
            maa_stream_load<uint32_t>(
                const_cast<uint32_t *>(index_j.data()), min_reg, max_reg,
                stride_reg, index_tile);
        }
        wait_ready(index_tile);

#pragma omp barrier
        if (thread == 0) {
            maa_indirect_load_virtual_scalar<double>(
                source_a.data(), index_b, index_tile, completion_tile,
                destination_c.data(), scale_reg, Operation_t::MUL_OP);
            const bool partial =
                waitForPartialOutput(destination_c, phase_name, first_probe);
            missed_live_trigger.store(!partial, std::memory_order_release);
            release_second.store(true, std::memory_order_release);
        } else {
            while (!release_second.load(std::memory_order_acquire)) {
            }
            double *second_source = const_cast<double *>(source_x.data());
            double *second_destination = destination_d.data();
            if (phase == MultiPhase::AConflict) {
                second_destination = source_a.data();
            } else if (phase == MultiPhase::BConflict) {
                second_destination =
                    reinterpret_cast<double *>(index_b_storage.data());
            } else if (phase == MultiPhase::CConflict) {
                second_source = destination_c.data();
            }
            maa_indirect_load_virtual_scalar<double>(
                second_source, const_cast<uint32_t *>(index_j.data()),
                index_tile, completion_tile, second_destination, scale_reg,
                Operation_t::MUL_OP);
        }
        wait_ready(completion_tile);
    }

    int errors = missed_live_trigger.load(std::memory_order_acquire) ? 1 : 0;
    for (int i = 0; i < n; ++i) {
        const double expected_c =
            sourceAValue(indexBValue(i, n)) * 3.0;
        if (destination_c[i] != expected_c && errors++ < 10) {
            std::cerr << phase_name << " C mismatch[" << i << "]: got "
                      << destination_c[i] << ", expected " << expected_c
                      << std::endl;
        }
        hashDouble(hash, destination_c[i]);

        double observed_second = destination_d[i];
        if (phase == MultiPhase::AConflict)
            observed_second = source_a[i];
        else if (phase == MultiPhase::BConflict)
            observed_second =
                reinterpret_cast<double *>(index_b_storage.data())[i];
        const double expected_second =
            phase == MultiPhase::CConflict
                ? destination_c[indexJValue(i, n)] * 5.0
                : sourceXValue(indexJValue(i, n)) * 5.0;
        if (observed_second != expected_second && errors++ < 10) {
            std::cerr << phase_name << " second mismatch[" << i
                      << "]: got " << observed_second << ", expected "
                      << expected_second << std::endl;
        }
        hashDouble(hash, observed_second);
    }
    std::cout << "FUSED_DIRECT_MULTIMAA_PHASE name=" << phase_name
              << " errors=" << errors << std::endl;
    return errors;
}

int
runMultiMAA(int n)
{
    std::vector<double> source_a(n);
    std::vector<uint64_t> index_b_storage(n);
    std::vector<double> destination_c(n, Sentinel);
    std::vector<double> source_x(n);
    std::vector<uint32_t> index_j(n);
    std::vector<double> destination_d(n, Sentinel);
    for (int i = 0; i < n; ++i) {
        source_x[i] = sourceXValue(i);
        index_j[i] = indexJValue(i, n);
    }

    std::cout << "VIRTUAL_GATHER64_LAYOUT mem_size="
              << static_cast<uint64_t>(MEM_SIZE) << " mode=multimaa"
              << std::endl;
    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(source_a.data(), source_a.data() + source_a.size());
    add_mem_region(index_b_storage.data(),
                   index_b_storage.data() + index_b_storage.size());
    add_mem_region(destination_c.data(),
                   destination_c.data() + destination_c.size());
    add_mem_region(source_x.data(), source_x.data() + source_x.size());
    add_mem_region(index_j.data(), index_j.data() + index_j.size());
    add_mem_region(destination_d.data(),
                   destination_d.data() + destination_d.size());

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
    uint64_t hash = 1469598103934665603ULL;
    int errors = 0;
    errors += runMultiPhase(MultiPhase::AConflict, n, source_a,
                            index_b_storage, destination_c, source_x, index_j,
                            destination_d, hash);
    errors += runMultiPhase(MultiPhase::BConflict, n, source_a,
                            index_b_storage, destination_c, source_x, index_j,
                            destination_d, hash);
    errors += runMultiPhase(MultiPhase::CConflict, n, source_a,
                            index_b_storage, destination_c, source_x, index_j,
                            destination_d, hash);
    errors += runMultiPhase(MultiPhase::Disjoint, n, source_a,
                            index_b_storage, destination_c, source_x, index_j,
                            destination_d, hash);
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    std::cout << "VIRTUAL_GATHER64_RESULT n=" << n
              << " pattern=multimaa errors=" << errors
              << " hash=" << hash << std::endl;
    std::cout << "ROI Ended" << std::endl;
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}

} // anonymous namespace

int main(int argc, char **argv) {
    const int n = argc > 1 ? std::atoi(argv[1]) : 4097;
    const std::string mode = argc > 2 ? argv[2] : "exact";
    if (n <= 0 || n > TILE_SIZE ||
        (mode != "exact" && mode != "alias" && mode != "drain" &&
         mode != "reset" && mode != "multimaa")) {
        std::cerr <<
            "usage: test_fused_direct_transform N "
            "exact|alias|drain|reset|multimaa"
                  << std::endl;
        return 2;
    }

    if (mode == "multimaa")
        return runMultiMAA(n);

    std::vector<double> source(n * 4);
    std::vector<uint32_t> indices(n);
    std::vector<double> output(n, Sentinel);
    for (int i = 0; i < static_cast<int>(source.size()); ++i)
        source[i] = static_cast<double>(i * 17 + 3) / 8.0;
    for (int i = 0; i < n; ++i)
        indices[i] = (i * 97 + 13) % source.size();
    const size_t first_probe = static_cast<size_t>(
        std::min_element(indices.begin(), indices.end()) - indices.begin());
    double *destination =
        mode == "alias" ? source.data() : output.data();

    std::cout << "VIRTUAL_GATHER64_LAYOUT mem_size="
              << static_cast<uint64_t>(MEM_SIZE) << " mode=" << mode
              << std::endl;
    m5_checkpoint(0, 0);

    alloc_MAA();
    init_MAA();
    clear_mem_region();
    add_mem_region(source.data(), source.data() + source.size());
    add_mem_region(indices.data(), indices.data() + indices.size());
    if (mode != "alias")
        add_mem_region(output.data(), output.data() + output.size());

    const int min_reg = get_new_reg<int>(0);
    const int max_reg = get_new_reg<int>(n);
    const int stride_reg = get_new_reg<int>(1);
    const int scale_reg = get_new_reg<double>(3.0);
    const int index_tile = get_new_tile<int>();
    // The direct-sink result has no SPD payload.  One 32-bit tile ID remains
    // solely as the software-visible completion token.
    const int completion_tile = get_new_tile<uint32_t>();

    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
    maa_stream_load<uint32_t>(indices.data(), min_reg, max_reg, stride_reg,
                              index_tile);
    maa_indirect_load_virtual_scalar<double>(
        source.data(), indices.data(), index_tile, completion_tile,
        destination, scale_reg, Operation_t::MUL_OP);
    // The live-state gates must begin after the command has reached MAA, not
    // merely after the CPU has issued its memory-mapped command writes.
    if ((mode == "drain" || mode == "reset") &&
        !waitForPartialOutput(output, mode, first_probe))
        return 4;
    if (mode == "drain") {
        std::cout << "FUSED_DIRECT_LIVE_DRAIN_REQUEST" << std::endl;
        m5_checkpoint(0, 0);
        std::cout << "FUSED_DIRECT_LIVE_DRAIN_RETURNED" << std::endl;
    }
    if (mode == "reset") {
        std::cout << "FUSED_DIRECT_LIVE_RESET_REQUEST" << std::endl;
        m5_reset_stats(0, 0);
        std::cerr << "live fused stats reset unexpectedly returned"
                  << std::endl;
        return 3;
    }
    wait_ready(completion_tile);
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);

    int errors = 0;
    uint64_t hash = 1469598103934665603ULL;
    for (int i = 0; i < n; ++i) {
        const double expected = source[indices[i]] * 3.0;
        if (destination[i] != expected && errors++ < 10)
            std::cerr << "mismatch[" << i << "]: got " << destination[i]
                      << ", expected " << expected << std::endl;
        hashDouble(hash, destination[i]);
    }
    std::cout << "VIRTUAL_GATHER64_RESULT n=" << n << " pattern=" << mode
              << " errors=" << errors << " hash=" << hash << std::endl;
    std::cout << "ROI Ended" << std::endl;
    m5_exit(0);
    return errors == 0 ? 0 : 1;
}
