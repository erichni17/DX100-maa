#ifdef USE_MPI
#include "mpi.h"
#endif

#include "Spatter/Configuration.hh"
#include "Spatter/Input.hh"
#include <cstdint>
#include <cstring>
#include <vector>
#ifdef GEM5
#include <gem5/m5ops.h>
#endif

#define xstr(s) str(s)
#define str(s)  #s

void print_build_info(Spatter::ClArgs &cl) {
    std::cout << std::endl;
    std::cout << "Running Spatter version 1.1" << std::endl;
    std::cout << "Compiler: " << xstr(SPAT_CXX_NAME) << " ver. "
              << xstr(SPAT_CXX_VER) << std::endl;
    std::cout << "Backend: ";
    if (cl.backend.compare("serial") == 0)
        std::cout << "Serial" << std::endl;
    else if (cl.backend.compare("openmp") == 0)
        std::cout << "OpenMP" << std::endl;
    else if (cl.backend.compare("cuda") == 0)
        std::cout << "CUDA" << std::endl;

    std::cout << "Aggregate Results? ";
    if (cl.aggregate == true)
        std::cout << "YES" << std::endl;
    else
        std::cout << "NO" << std::endl;

#ifdef USE_CUDA
    int gpu_id = 0;
    if (cl.backend.compare("cuda") == 0) {
        int num_devices = 0;
        checkCudaErrors(cudaGetDeviceCount(&num_devices));

        struct cudaDeviceProp prop;
        checkCudaErrors(cudaGetDeviceProperties(&prop, gpu_id));

        std::cout << "Number of Devices: " << num_devices << std::endl;
        std::cout << "Device Name: " << prop.name << std::endl;
        std::cout << "Memory Clock Rage (KHz): " << prop.memoryClockRate
                  << std::endl;
        std::cout << "Memory Bus Width (bits): " << prop.memoryBusWidth
                  << std::endl;
        std::cout << "Peak Memory Bandwidth (GB/s): "
                  << 2.0 * prop.memoryClockRate * (prop.memoryBusWidth / 8) / 1.0e6
                  << std::endl;
    }
#endif

    std::cout << std::endl;
}

void alloc_MAA();

#ifdef GEM5
namespace {

constexpr size_t fingerprint_element_limit = 2097152;

uint64_t fingerprint_word(uint64_t hash, uint64_t word) {
    constexpr uint64_t fnv_prime = 1099511628211ULL;
    for (unsigned int shift = 0; shift < 64; shift += 8) {
        hash ^= (word >> shift) & 0xffULL;
        hash *= fnv_prime;
    }
    return hash;
}

uint64_t double_bits(double value) {
    uint64_t bits = 0;
    static_assert(sizeof(bits) == sizeof(value), "unexpected double width");
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}

void print_semantic_fingerprint(const Spatter::ConfigurationBase &config) {
    const size_t elements =
        std::min(config.pattern.size(), fingerprint_element_limit);
    uint64_t hash = 14695981039346656037ULL;
    hash = fingerprint_word(hash, config.id);
    hash = fingerprint_word(hash, elements);
    size_t checked = 0;
    size_t ambiguous = 0;
    size_t mismatches = 0;

    if (config.kernel == "gather") {
        for (size_t j = 0; j < elements; ++j) {
            const size_t index = config.pattern[j];
            if (index >= config.sparse.size() || j >= config.dense.size()) {
                ++mismatches;
                continue;
            }
            const uint64_t actual = double_bits(config.dense[j]);
            const uint64_t expected = double_bits(config.sparse[index]);
            if (actual != expected)
                ++mismatches;
            hash = fingerprint_word(hash, j);
            hash = fingerprint_word(hash, actual);
            ++checked;
        }
    } else if (config.kernel == "scatter") {
        // Repeated scatter destinations are race-compatible in Spatter. Accept
        // any value issued to that destination, while hashing the actual final
        // value so paired configurations must still produce identical output.
        std::vector<uint32_t> writers(config.sparse.size(), 0);
        std::vector<uint8_t> matched_writer(config.sparse.size(), 0);
        for (size_t j = 0; j < elements; ++j) {
            const size_t index = config.pattern[j];
            if (index >= config.sparse.size() || j >= config.dense.size()) {
                ++mismatches;
                continue;
            }
            ++writers[index];
        }
        for (size_t j = 0; j < elements; ++j) {
            const size_t index = config.pattern[j];
            if (index >= config.sparse.size() || j >= config.dense.size())
                continue;
            if (double_bits(config.sparse[index]) == double_bits(config.dense[j]))
                matched_writer[index] = 1;
        }
        for (size_t j = 0; j < elements; ++j) {
            const size_t index = config.pattern[j];
            if (index >= config.sparse.size() || writers[index] == 0)
                continue;
            const uint32_t writer_count = writers[index];
            writers[index] = 0;
            ++checked;
            if (writer_count > 1)
                ++ambiguous;
            if (!matched_writer[index])
                ++mismatches;
            hash = fingerprint_word(hash, index);
            hash = fingerprint_word(hash, double_bits(config.sparse[index]));
        }
    } else {
        ++mismatches;
    }

    std::cout << "SPATTER_FP config=" << config.id
              << " kernel=" << config.kernel
              << " elements=" << elements
              << " checked=" << checked
              << " ambiguous=" << ambiguous
              << " mismatches=" << mismatches
              << " hash=" << hash << std::endl;
}

} // namespace
#endif

int main(int argc, char **argv) {

#ifdef USE_MPI
    MPI_Init(&argc, &argv);

    int rank;
    int size;

    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &size);
#endif

    const unsigned long warmup_runs = 0;
    bool timed = 0;

    Spatter::ClArgs cl;
    if (Spatter::parse_input(argc, argv, cl) != 0)
        return -1;

#ifdef USE_MPI
    if (rank == 0) {
#endif
        if (cl.verbosity >= 1)
            print_build_info(cl);

        if (cl.verbosity >= 2)
            std::cout << cl;

        cl.report_header();
#ifdef USE_MPI
    }
#endif

#ifdef GEM5
    std::cout << "Checkpoint started" << std::endl;
    m5_checkpoint(0, 0);
    std::cout << "Checkpoint ended" << std::endl;
#endif

    alloc_MAA();
#ifdef MAA
    Spatter::setup_MAA();
#endif

#ifdef GEM5
    std::cout << "ROI started: " << std::endl;
    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
#endif

    int config_id = 0;
    for (std::unique_ptr<Spatter::ConfigurationBase> const &config : cl.configs) {
        assert(config->nruns == 1 && "Only one run is supported for now");
        std::cout << "Config " << config_id++ << "/" << cl.configs.size() << std::endl;

        if (config->run(timed, 0) != 0)
            return -1;

#ifndef GEM5
        config->report();
#endif
    }

#ifdef GEM5
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);
    std::cout << "ROI End!!!" << std::endl;

    // Replay after the performance dump so validation cannot inflate ROI cycles.
    std::cout << "Validation started" << std::endl;
    for (std::unique_ptr<Spatter::ConfigurationBase> const &config : cl.configs) {
        if (config->run(false, 0) != 0)
            return -1;
        print_semantic_fingerprint(*config);
    }
    std::cout << "Validation ended" << std::endl;
    m5_exit(0);
#endif

#ifdef USE_MPI
    MPI_Finalize();
#endif
}
