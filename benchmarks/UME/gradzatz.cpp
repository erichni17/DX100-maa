#include <omp.h>

#include <algorithm> // For std::iota and std::fill
#include <atomic>
#include <cmath>     // For std::fabs
#include <cstdint>
#include <cstdlib>   // For rand()
#include <cstring>
#include <ctime>
#include <iostream>
#include <limits>
#include <string>
#include <vector>

using namespace std;

#define DATATYPE float

// #define VERIFY

#define DISTANCE_OTEHRS 85000
#define DISTANCE_P2C    80000
#define PADDING_LEN     90000
#define TOLERANCE       1e-3 // Tolerance for floating-point comparisons

std::vector<int> corner_type;
std::vector<int> c_to_z_map;
std::vector<int> c_to_p_map;
std::vector<DATATYPE> point_gradient;
std::vector<DATATYPE> corner_volume;

std::vector<DATATYPE> zone_volume;
std::vector<DATATYPE> zone_gradient;

std::vector<DATATYPE> zone_volume_exp;
std::vector<DATATYPE> zone_gradient_exp;

#ifdef MAA_VIRTUAL_GATHER
alignas(64) static DATATYPE virtual_gather_backing[NUM_CORES][TILE_SIZE];
#endif

#ifdef UME_GRADZATZ_VERIFY
static std::atomic<uint64_t> gather_verify_errors{0};
static std::atomic<uint64_t> gather_verify_lanes{0};
#endif

#if defined(UME_GRADZATZ_VERIFY) || \
    defined(UME_GRADZATZ_OUTPUT_FINGERPRINT)
static uint64_t expected_active_corners = 0;

static uint32_t value_bits(DATATYPE value) {
    uint32_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}

static uint64_t update_output_hash(uint64_t hash, uint64_t index,
                                   DATATYPE value) {
    hash ^= (index << 32) ^ value_bits(value);
    hash *= 1099511628211ULL;
    return hash;
}

static void build_scalar_reference() {
    std::fill(zone_volume_exp.begin(), zone_volume_exp.end(), 0.0f);
    std::fill(zone_gradient_exp.begin(), zone_gradient_exp.end(), 0.0f);
    expected_active_corners = 0;
    for (size_t c = 0; c < corner_type.size(); ++c) {
        if (corner_type[c] < 1)
            continue;
        expected_active_corners++;
        zone_volume_exp[c_to_z_map[c]] += corner_volume[c];
    }
    for (size_t c = 0; c < corner_type.size(); ++c) {
        if (corner_type[c] < 1)
            continue;
        const int z = c_to_z_map[c];
        const int p = c_to_p_map[c];
        const DATATYPE ratio = corner_volume[c] / zone_volume_exp[z];
        zone_gradient_exp[z] += point_gradient[p] * ratio;
    }
}

struct ReferenceErrors
{
    uint64_t volume = 0;
    uint64_t gradient = 0;
    uint64_t nonfinite = 0;
    uint64_t hash = 1469598103934665603ULL;
};

static ReferenceErrors check_scalar_reference() {
    ReferenceErrors errors;
    for (size_t z = 0; z < zone_volume.size(); ++z) {
        errors.nonfinite += !std::isfinite(zone_volume[z]);
        errors.nonfinite += !std::isfinite(zone_gradient[z]);
        if (value_bits(zone_volume[z]) != value_bits(zone_volume_exp[z]))
            errors.volume++;
        if (value_bits(zone_gradient[z]) != value_bits(zone_gradient_exp[z]))
            errors.gradient++;
        errors.hash = update_output_hash(errors.hash, z * 2, zone_volume[z]);
        errors.hash =
            update_output_hash(errors.hash, z * 2 + 1, zone_gradient[z]);
    }
    return errors;
}
#endif

#if !defined(FUNC) && !defined(GEM5) && !defined(GEM5_MAGIC)
#define GEM5
#endif

#if defined(FUNC)
#include <MAA_functional.hpp>

#elif defined(GEM5)
#include <MAA_gem5.hpp>

#include <gem5/m5ops.h>

#elif defined(GEM5_MAGIC)
#include "MAA_gem5_magic.hpp"
#endif
#ifdef MAA_GENERAL_VIRTUAL_CONSUMER
#include <MAA_virtual_materialize.hpp>

static MAAVirtualConsumerMode virtual_consumer_mode =
    MAAVirtualConsumerMode::StreamControl;
#endif
int tiles0[NUM_CORES], tiles1[NUM_CORES], tiles2[NUM_CORES];
int tiles3[NUM_CORES], tiles4[NUM_CORES];
int regs0[NUM_CORES], regs1[NUM_CORES], regs2[NUM_CORES];
#ifdef MAA_GENERAL_VIRTUAL_CONSUMER
int page_regs0[NUM_CORES], page_regs1[NUM_CORES], page_regs2[NUM_CORES];
#endif
#ifdef MAA_VIRTUAL_GATHER
int backing_start_regs[NUM_CORES], backing_end_regs[NUM_CORES];
#endif

void gradzatz() {
    int num_corners = corner_type.size();

#ifdef GEM5
    clear_mem_region();
    // Add memory regions for used arrays in this kernel
    add_mem_region(zone_gradient_exp.data(), zone_gradient_exp.data() + zone_gradient_exp.size()); // 6
    add_mem_region(zone_volume_exp.data(), zone_volume_exp.data() + zone_volume_exp.size());       // 7
    add_mem_region(c_to_p_map.data(), c_to_p_map.data() + c_to_p_map.size());                      // 8
    add_mem_region(corner_volume.data(), corner_volume.data() + corner_volume.size());             // 9
    add_mem_region(c_to_z_map.data(), c_to_z_map.data() + c_to_z_map.size());                      // 10
    add_mem_region(corner_type.data(), corner_type.data() + corner_type.size());                   // 11
    add_mem_region(point_gradient.data(), point_gradient.data() + point_gradient.size());          // 12
    std::cout << "ROI Begin" << std::endl;
    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
#endif
// Accumulate the zone volume
#pragma omp parallel
    {
#pragma omp for
        for (int c = 0; c < num_corners; ++c) {
            if (corner_type[c] < 1)
                continue; // Only operate on interior corners
            int z = c_to_z_map[c];
#pragma omp atomic
            zone_volume_exp[z] += corner_volume[c];
        }

// Accumulate the zone-centered gradient
#pragma omp for
        for (int c = 0; c < num_corners; ++c) {
            if (corner_type[c] < 1)
                continue; // Only operate on interior corners
            int z = c_to_z_map[c];
            int p = c_to_p_map[c];
            double c_z_vol_ratio = corner_volume[c] / zone_volume_exp[z];
#pragma omp atomic
            zone_gradient_exp[z] += point_gradient[p] * c_z_vol_ratio;
        }
    }

#ifdef GEM5
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);
    std::cout << "ROI Ended" << std::endl;
    m5_exit(0);
#endif
}

void gradzatz_MAA() {
    int num_corners = corner_type.size();

#ifdef GEM5
    clear_mem_region();
    // Add memory regions for used arrays in this kernel
    add_mem_region(zone_gradient.data(), zone_gradient.data() + zone_gradient.size());    // 6
    add_mem_region(zone_volume.data(), zone_volume.data() + zone_volume.size());          // 7
    add_mem_region(c_to_p_map.data(), c_to_p_map.data() + c_to_p_map.size());             // 8
    add_mem_region(corner_volume.data(), corner_volume.data() + corner_volume.size());    // 9
    add_mem_region(c_to_z_map.data(), c_to_z_map.data() + c_to_z_map.size());             // 10
    add_mem_region(corner_type.data(), corner_type.data() + corner_type.size());          // 11
    add_mem_region(point_gradient.data(), point_gradient.data() + point_gradient.size()); // 12
#ifdef MAA_VIRTUAL_GATHER
    for (int core = 0; core < NUM_CORES; ++core) {
        add_mem_region(virtual_gather_backing[core],
                       virtual_gather_backing[core] + TILE_SIZE);
    }
#endif
    std::cout << "ROI Begin" << std::endl;
    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
#endif
#pragma omp parallel
    {
        int reg0, reg1, reg2;
#if defined(MAA_VIRTUAL_GATHER) && !defined(MAA_GENERAL_VIRTUAL_CONSUMER)
        int backing_start_reg, backing_end_reg;
#endif
        int tile0, tile2, tile3, tile5, tileCond;
        int omp_thread_id = omp_get_thread_num();
        reg0 = regs0[omp_thread_id];
        reg1 = regs1[omp_thread_id];
        reg2 = regs2[omp_thread_id];
#ifdef MAA_GENERAL_VIRTUAL_CONSUMER
        const int page_min_reg = page_regs0[omp_thread_id];
        const int page_max_reg = page_regs1[omp_thread_id];
        const int page_stride_reg = page_regs2[omp_thread_id];
#endif
#if defined(MAA_VIRTUAL_GATHER) && !defined(MAA_GENERAL_VIRTUAL_CONSUMER)
        backing_start_reg = backing_start_regs[omp_thread_id];
        backing_end_reg = backing_end_regs[omp_thread_id];
#endif
        tile0 = tiles0[omp_thread_id];
        tile2 = tiles1[omp_thread_id];
        tileCond = tiles3[omp_thread_id];
        tile3 = tiles2[omp_thread_id];
        tile5 = tiles4[omp_thread_id];

        maa_const<int>(1, reg2);
        maa_const<int>(num_corners, reg1);
#if defined(MAA_VIRTUAL_GATHER) && !defined(MAA_GENERAL_VIRTUAL_CONSUMER)
        maa_const<int>(0, backing_start_reg);
#endif
#pragma omp for
        for (int c = 0; c < num_corners;
#ifdef MAA_GENERAL_VIRTUAL_CONSUMER
             c += MAA_CONSUMER_TILE_SIZE
#else
             c += TILE_SIZE
#endif
        ) {
            maa_const<int>(c, reg0);
#ifdef MAA_GENERAL_VIRTUAL_CONSUMER
            maa_const<int>(std::min(num_corners,
                                    c + MAA_CONSUMER_TILE_SIZE), reg1);
#endif
            // Step1: Load corner_type
            maa_stream_load<int>(corner_type.data(), reg0, reg1, reg2, tile0);
            // Step2: Perform comparison
            maa_alu_scalar<int>(tile0, reg2, tileCond, Operation_t::GTE_OP);
            // Step3: Load c_to_z_map
            maa_stream_load<int>(c_to_z_map.data(), reg0, reg1, reg2, tile3);
            // Transfer tile3
            // Step4: Load corner_volume[c]
            maa_stream_load<DATATYPE>(corner_volume.data(), reg0, reg1, reg2, tile0);
            // Step5: Accumulate the local zone volume
            maa_indirect_rmw_vector<DATATYPE>(zone_volume.data(), tile3, tile0, Operation_t::ADD_OP, tileCond);
            wait_ready(tile0);
        }

        // Accumulate the zone volume
        // for (int c = 0; c < num_corners; ++c) {
        //     if (corner_type[c] < 1)
        //         continue; // Only operate on interior corners
        //     int z = c_to_z_map[c];
        //     zone_volume[z] += corner_volume[c];
        // }

        // Accumulate the zone-centered gradient
        int *tileCondPtr = get_cacheable_tile_pointer<int>(tileCond);
        DATATYPE *tile5Ptr = get_cacheable_tile_pointer<DATATYPE>(tile5);
        DATATYPE *tile2Ptr = get_cacheable_tile_pointer<DATATYPE>(tile2);
#ifndef MAA_GENERAL_VIRTUAL_CONSUMER
        DATATYPE *tile0Ptr = get_cacheable_tile_pointer<DATATYPE>(tile0);
#endif
#ifdef UME_GRADZATZ_VERIFY
#ifndef MAA_GENERAL_VIRTUAL_CONSUMER
        int *tile5IndexPtr = get_cacheable_tile_pointer<int>(tile5);
#endif
        uint64_t local_gather_errors = 0;
        uint64_t local_gather_lanes = 0;
#endif
#pragma omp for
        for (int c = 0; c < num_corners; c += TILE_SIZE) {
            const int gather_size = std::min(num_corners - c, TILE_SIZE);
#ifdef MAA_GENERAL_VIRTUAL_CONSUMER
            maa_const<int>(c, reg0);
            maa_const<int>(c + gather_size, reg1);
            maa_indirect_load_virtual_index<DATATYPE>(
                point_gradient.data(),
                reinterpret_cast<uint32_t *>(c_to_p_map.data()), tile0,
                virtual_gather_backing[omp_thread_id], reg0, reg1, reg2);
            if (gather_size == TILE_SIZE)
                maa_virtual_consumer_begin(virtual_consumer_mode, tile0);
            else
                // Preserve an exact four-page materializer ABI: partial
                // logical tails drain, then reload through ordinary STREAM_LD.
                wait_ready(tile0);

            for (int page_offset = 0; page_offset < gather_size;
                 page_offset += MAA_CONSUMER_TILE_SIZE) {
                const int page_size =
                    std::min(gather_size - page_offset,
                             MAA_CONSUMER_TILE_SIZE);
                const int page_begin = c + page_offset;
                maa_const<int>(page_begin, reg0);
                maa_const<int>(page_begin + page_size, reg1);

                maa_stream_load<int>(corner_type.data(), reg0, reg1, reg2,
                                     tile5);
                maa_alu_scalar<int>(tile5, reg2, tileCond,
                                    Operation_t::GTE_OP);
                maa_stream_load<int>(c_to_z_map.data(), reg0, reg1, reg2,
                                     tile3, tileCond);
                maa_indirect_load<DATATYPE>(zone_volume.data(), tile3, tile2,
                                            tileCond);

                if (gather_size == TILE_SIZE) {
                    maa_virtual_consumer_load_page<DATATYPE>(
                        virtual_consumer_mode,
                        virtual_gather_backing[omp_thread_id] + page_offset,
                        tile0, page_offset / MAA_CONSUMER_TILE_SIZE,
                        page_min_reg, page_max_reg, page_stride_reg, tile5);
                } else {
                    maa_stream_load<DATATYPE>(
                        virtual_gather_backing[omp_thread_id] + page_offset,
                        page_min_reg, page_max_reg, page_stride_reg, tile5);
                }
                wait_ready(tile2);
                wait_ready(tile5);

#ifdef UME_GRADZATZ_VERIFY
                for (int i = 0; i < page_size; ++i) {
                    if (tileCondPtr[i] == 0)
                        continue;
                    if (value_bits(tile5Ptr[i]) !=
                        value_bits(point_gradient[
                            c_to_p_map[page_begin + i]]))
                        local_gather_errors++;
                    local_gather_lanes++;
                }
#endif

#pragma omp simd simdlen(4)
                for (int i = 0; i < page_size; ++i) {
                    if (tileCondPtr[i] == 1) {
                        const DATATYPE c_z_vol_ratio =
                            corner_volume[page_begin + i] / tile2Ptr[i];
                        tile5Ptr[i] = tile5Ptr[i] * c_z_vol_ratio;
                    }
                }
                maa_indirect_rmw_vector<DATATYPE>(
                    zone_gradient.data(), tile3, tile5,
                    Operation_t::ADD_OP, tileCond);
            }
            if (gather_size == TILE_SIZE)
                maa_virtual_consumer_end(virtual_consumer_mode, tile0);
#else
            maa_const<int>(c, reg0);
            // Step1: Load corner_type
            maa_stream_load<int>(corner_type.data(), reg0, reg1, reg2, tile0);
            // Step2: Perform comparison
            maa_alu_scalar<int>(tile0, reg2, tileCond, Operation_t::GTE_OP);
            // Step3: Load c_to_z_map
            maa_stream_load<int>(c_to_z_map.data(), reg0, reg1, reg2, tile3,
                                 tileCond); // tile3 -> z
            // Step4: Load c_to_p_map
            maa_stream_load<int>(c_to_p_map.data(), reg0, reg1, reg2, tile5,
                                 tileCond); // tile5 -> p
            // Step5: Load point_gradient[p]
#ifdef MAA_VIRTUAL_GATHER
            maa_indirect_load_virtual<DATATYPE>(
                point_gradient.data(), tile5, tile0,
                virtual_gather_backing[omp_thread_id], tileCond);
#else
            maa_indirect_load<DATATYPE>(point_gradient.data(), tile5, tile0,
                                        tileCond); // tile0 -> point_gradient
#endif
            // Transfer tileCond; step 3 is mapped to the same region.
            // Step6: Load zone_gradient[z]
            maa_indirect_load<DATATYPE>(zone_volume.data(), tile3, tile2,
                                        tileCond); // tile2 -> zone_volume
            wait_ready(tile2);
            wait_ready(tile0);
#ifdef MAA_VIRTUAL_GATHER
            maa_const<int>(gather_size, backing_end_reg);
            maa_stream_load<DATATYPE>(virtual_gather_backing[omp_thread_id],
                                      backing_start_reg, backing_end_reg, reg2,
                                      tile0);
            wait_ready(tile0);
#endif

#ifdef UME_GRADZATZ_VERIFY
            for (int i = 0; i < gather_size; ++i) {
                if (tileCondPtr[i] == 0)
                    continue;
                if (value_bits(tile0Ptr[i]) !=
                    value_bits(point_gradient[tile5IndexPtr[i]]))
                    local_gather_errors++;
                local_gather_lanes++;
            }
#endif

#pragma omp simd simdlen(4)
            for (int i = 0; i < gather_size; i++) {
                if (tileCondPtr[i] == 1) {
                    DATATYPE c_z_vol_ratio = corner_volume[c + i] / tile2Ptr[i];
                    tile5Ptr[i] = tile0Ptr[i] * c_z_vol_ratio;
                }
            }

            // Step8: Accumulate zone_gradient
            maa_indirect_rmw_vector<DATATYPE>(zone_gradient.data(), tile3, tile5, Operation_t::ADD_OP, tileCond);
#endif
        }
        wait_ready(tile5);
#ifdef UME_GRADZATZ_VERIFY
        gather_verify_errors.fetch_add(local_gather_errors,
                                       std::memory_order_relaxed);
        gather_verify_lanes.fetch_add(local_gather_lanes,
                                      std::memory_order_relaxed);
#endif
        // #pragma omp for
        // for (int c = 0; c < num_corners; ++c) {
        //     if (corner_type[c] < 1)
        //         continue; // Only operate on interior corners
        //     int z = c_to_z_map[c];
        //     int p = c_to_p_map[c];
        //     double c_z_vol_ratio = corner_volume[c] / zone_volume[z];
        //     zone_gradient[z] += point_gradient[p] * c_z_vol_ratio;
        // }
    }
#ifdef GEM5
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);
#if defined(UME_GRADZATZ_VERIFY) || \
    defined(UME_GRADZATZ_OUTPUT_FINGERPRINT)
    const ReferenceErrors reference = check_scalar_reference();
    const uint64_t reference_errors =
        reference.volume + reference.gradient;
#ifdef UME_GRADZATZ_VERIFY
    const uint64_t gather_errors = gather_verify_errors.load();
    const uint64_t gather_lanes = gather_verify_lanes.load();
    constexpr uint64_t expected_hash = 5900127901050159227ULL;
    if (gather_errors != 0 || gather_lanes != expected_active_corners ||
        reference_errors != 0 || reference.nonfinite != 0 ||
        reference.hash != expected_hash) {
        std::cerr << "UME_GRADZATZ_VERIFY_FAIL gather_errors="
                  << gather_errors << " gather_lanes=" << gather_lanes
                  << " expected_lanes=" << expected_active_corners
                  << " reference_errors=" << reference_errors
                  << " volume_errors=" << reference.volume
                  << " gradient_errors=" << reference.gradient
                  << " elements=" << zone_volume.size()
                  << " output_hash=" << reference.hash
                  << " nonfinite=" << reference.nonfinite << std::endl;
        std::abort();
    }
    std::cout << "UME_GRADZATZ_VERIFY_PASS gather_errors=0 gather_lanes="
              << gather_lanes << " expected_lanes="
              << expected_active_corners
              << " reference_errors=0 volume_errors=0 gradient_errors=0"
              << " elements=" << zone_volume.size()
              << " output_hash=" << reference.hash << " nonfinite=0"
              << std::endl;
#else
    constexpr uint64_t expected_hash = UME_GRADZATZ_EXPECTED_HASH;
    if (reference_errors != 0 || reference.nonfinite != 0 ||
        reference.hash != expected_hash) {
        std::cerr << "UME_OUTPUT_FP_FAIL output_hash=" << reference.hash
                  << " expected_hash=" << expected_hash
                  << " reference_volume_errors=" << reference.volume
                  << " reference_gradient_errors=" << reference.gradient
                  << " nonfinite=" << reference.nonfinite << std::endl;
        std::abort();
    }
    std::cout << "UME_OUTPUT_FP output_hash=" << reference.hash
              << " nonfinite=0" << std::endl;
    std::cout << "UME_REFERENCE_PASS volume_errors=0 gradient_errors=0"
              << " elements=" << zone_volume.size() << std::endl;
#endif
#endif
    std::cout << "ROI Ended" << std::endl;
    m5_exit(0);
#endif
}

#ifdef VERIFY
// Verification function
void verify_results(const std::vector<DATATYPE> &original, const std::vector<DATATYPE> &Res, const std::string &vector_name) {
    bool success = true;
    if (original.size() != Res.size()) {
        cout << "Size mismatch in " << vector_name << ": original size " << original.size()
             << ", Res size " << Res.size() << endl;
        success = false;
    } else {
        for (size_t i = 0; i < original.size(); ++i) {
            if (std::fabs(original[i] - Res[i]) > TOLERANCE) {
                cout << "Mismatch in " << vector_name << " at index " << i << ": original " << original[i]
                     << ", Res " << Res[i] << endl;
                success = false;
                break;
            }
        }
    }
    if (success) {
        cout << vector_name << " verification passed." << endl;
    } else {
        cout << vector_name << " verification failed." << endl;
    }
}
#endif

void print_usage(std::string name) {
    cout << "Usage: " << name << " [n]" << endl;
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        print_usage(argv[0]);
        return 1;
    }

    char *end = nullptr;
    const long long parsed_n = std::strtoll(argv[1], &end, 10);
    if (end == argv[1] || *end != '\0' || parsed_n <= 0 ||
        parsed_n > std::numeric_limits<int>::max() - 2 * PADDING_LEN) {
        std::cerr << "n must be a positive padded 32-bit size" << std::endl;
        return 2;
    }
    int n = static_cast<int>(parsed_n);
#ifdef MAA_GENERAL_VIRTUAL_CONSUMER
    if (argc != 3) {
        std::cerr << "general virtual consumer requires a deferred selector "
                     "path after n"
                  << std::endl;
        return 2;
    }
    const std::string virtual_consumer_selector = argv[2];
#endif
#if defined(UME_GRADZATZ_VERIFY) || defined(UME_GRADZATZ_FIXED_INPUT)
#ifdef UME_GRADZATZ_VERIFY
    if (n != 4097) {
        std::cerr << "UME_GRADZATZ_VERIFY requires n=4097" << std::endl;
        return 2;
    }
#else
    if (n != UME_GRADZATZ_EXPECTED_N) {
        std::cerr << "fixed-input binary requires n="
                  << UME_GRADZATZ_EXPECTED_N << std::endl;
        return 2;
    }
#endif
#else
    srand((unsigned)time(NULL));
#endif
#if !defined(UME_GRADZATZ_VERIFY) && !defined(UME_GRADZATZ_FIXED_INPUT)
    float branch_bias = 0.95;
#endif

    int total_size = n;
    int num_points = total_size + 2 * PADDING_LEN;
    int num_zones = total_size + 2 * PADDING_LEN;
    int num_corners = total_size;

    corner_type.resize(num_corners);
    corner_volume.resize(num_corners);
    c_to_z_map.resize(num_corners);
    c_to_p_map.resize(num_corners);

    zone_gradient.resize(num_zones);
    point_gradient.resize(num_points);

    zone_volume.resize(num_zones);

    zone_gradient_exp.resize(num_zones);
    zone_volume_exp.resize(num_zones);

#if defined(UME_GRADZATZ_VERIFY) || defined(UME_GRADZATZ_FIXED_INPUT)
    for (int i = 0; i < num_corners; ++i) {
        corner_type[i] = i % 20 != 0 ? 1 : -1;
        corner_volume[i] = 1.0f;
        c_to_z_map[i] = PADDING_LEN + i;
        c_to_p_map[i] =
            PADDING_LEN + ((97 * i + 13) % num_corners);
    }
    for (int i = 0; i < num_points; ++i)
        point_gradient[i] = static_cast<float>(i + 1);
#else
    // Initialize data arrays with random values
    for (int i = 0; i < num_corners; ++i) {
        corner_type[i] = (rand() % 100 < branch_bias * 100) ? 1 : -1;
    }
    std::fill(point_gradient.begin(), point_gradient.end(), 1.0);
    std::fill(corner_volume.begin(), corner_volume.end(), 1.0);
#endif

    std::fill(zone_volume.begin(), zone_volume.end(), 0.0);
    std::fill(zone_gradient.begin(), zone_gradient.end(), 0.0);

    std::fill(zone_gradient_exp.begin(), zone_gradient_exp.end(), 0.0);
    std::fill(zone_volume_exp.begin(), zone_volume_exp.end(), 0.0);

#if !defined(UME_GRADZATZ_VERIFY) && !defined(UME_GRADZATZ_FIXED_INPUT)
    // Initialize c_to_p_map and c_to_z_map with random valid indices
    for (int c = DISTANCE_OTEHRS; c < num_corners + DISTANCE_OTEHRS; ++c) {
        int idx = c - DISTANCE_OTEHRS;
        int rand_offset = rand() % (2 * DISTANCE_OTEHRS + 1) - DISTANCE_OTEHRS;
        c_to_p_map[idx] = c + rand_offset;
        if (c_to_p_map[idx] < 0)
            assert(false && "c_to_p_map[c] < 0");
        else if (c_to_p_map[idx] >= num_points)
            assert(false && "c_to_p_map[c] >= num_points");

        rand_offset = rand() % (2 * DISTANCE_OTEHRS + 1) - DISTANCE_OTEHRS;
        c_to_z_map[idx] = c + rand_offset;
        if (c_to_z_map[idx] < 0)
            assert(false && "c_to_z_map[c] < 0");
        else if (c_to_z_map[idx] >= num_zones)
            assert(false && "c_to_z_map[c] >= num_zones");
    }
#endif
#if defined(UME_GRADZATZ_VERIFY) || \
    defined(UME_GRADZATZ_OUTPUT_FINGERPRINT)
    build_scalar_reference();
#endif

#ifdef GEM5
    cout << "Starting checkpoint" << endl;
    m5_checkpoint(0, 0);
    cout << "checkpoint done" << endl;
#ifdef MAA_GENERAL_VIRTUAL_CONSUMER
    try {
        virtual_consumer_mode =
            maa_read_virtual_consumer_mode(virtual_consumer_selector);
        if (virtual_consumer_mode ==
            MAAVirtualConsumerMode::TokenStreamLoadPingPong)
            throw std::runtime_error(
                "GZZ does not have two free alternating consumer tiles");
    } catch (const std::exception &error) {
        std::cerr << "GZZ virtual consumer selector: " << error.what()
                  << std::endl;
        return 2;
    }
    std::cout << "UME_GZZ_VIRTUAL_CONSUMER mode="
              << maa_virtual_consumer_mode_name(virtual_consumer_mode)
              << " logical=" << TILE_SIZE
              << " consumer=" << MAA_CONSUMER_TILE_SIZE << std::endl;
#endif
#endif
    alloc_MAA();
    init_MAA();

#ifndef MAA
    // Run original functions
    gradzatz();
#else

#pragma omp parallel
    {
#pragma omp single
        {
            if (omp_get_num_threads() != NUM_CORES) {
                std::cerr << "OpenMP team size must equal NUM_CORES"
                          << std::endl;
                std::abort();
            }
        }
#pragma omp critical
        {
            int thread_id = omp_get_thread_num();
            tiles0[thread_id] = get_new_tile<int>();
            tiles1[thread_id] = get_new_tile<int>();
            tiles2[thread_id] = get_new_tile<int>();
            tiles3[thread_id] = get_new_tile<int>();
            tiles4[thread_id] = get_new_tile<int>();
            regs0[thread_id] = get_new_reg<int>();
            regs1[thread_id] = get_new_reg<int>();
            regs2[thread_id] = get_new_reg<int>();
#ifdef MAA_GENERAL_VIRTUAL_CONSUMER
            page_regs0[thread_id] = get_new_reg<int>(0);
            page_regs1[thread_id] = get_new_reg<int>(MAA_CONSUMER_TILE_SIZE);
            page_regs2[thread_id] = get_new_reg<int>(1);
#endif
#ifdef MAA_VIRTUAL_GATHER
            backing_start_regs[thread_id] = get_new_reg<int>();
            backing_end_regs[thread_id] = get_new_reg<int>();
#endif
        }
    }
    gradzatz_MAA();
#ifdef VERIFY
    gradzatz();
    verify_results(zone_gradient_exp, zone_gradient, "zone_gradient");
    verify_results(zone_volume_exp, zone_volume, "zone_volume");
#endif
#endif
#ifdef GEM5
    m5_exit(0);
#endif
    return 0;
}
