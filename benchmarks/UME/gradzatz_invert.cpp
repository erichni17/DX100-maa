#include <omp.h>

#include <algorithm> // For std::iota and std::fill
#include <atomic>
#include <cassert>
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

// #define VERIFY

#define DATATYPE float

#define DISTANCE_OTEHRS 85000
#define DISTANCE_P2C    80000
#define PADDING_LEN     90000
#define TOLERANCE       1e-3 // Tolerance for floating-point comparisons

std::vector<int> c_to_z_map;
std::vector<int> c_to_p_map;

// Using CSR format for p_to_c_map
std::vector<int> p_to_c_indptr;  // Size: num_points + 1
std::vector<int> p_to_c_indices; // Flattened indices

// Using CSR format for z_to_c_map
std::vector<int> z_to_c_indptr;  // Size: num_zones + 1
std::vector<int> z_to_c_indices; // Flattened indices

std::vector<DATATYPE> corner_volume;
std::vector<DATATYPE> zone_volume_tmp;
std::vector<DATATYPE> point_normal;
std::vector<DATATYPE> point_gradient;

// outputs
std::vector<DATATYPE> zone_gradient;
std::vector<DATATYPE> zone_gradient_exp;
std::vector<DATATYPE> zone_volume_exp;

std::vector<int> zone_type;

#ifdef MAA_VIRTUAL_GATHER
alignas(64) static DATATYPE virtual_gather_backing[NUM_CORES][TILE_SIZE];
#endif

#ifdef UME_GRADZATZ_INVERT_VERIFY
static std::atomic<uint64_t> gather_verify_errors{0};
static std::atomic<uint64_t> gather_verify_lanes{0};
#endif

#if defined(UME_GRADZATZ_INVERT_VERIFY) || \
    defined(UME_GRADZATZ_INVERT_OUTPUT_FINGERPRINT)
static uint64_t expected_gather_lanes = 0;

static uint32_t
value_bits(DATATYPE value)
{
    uint32_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}

static uint64_t
update_output_hash(uint64_t hash, uint64_t index, DATATYPE value)
{
    hash ^= (index << 32) ^ value_bits(value);
    hash *= 1099511628211ULL;
    return hash;
}

static void
build_scalar_reference()
{
    std::fill(zone_volume_exp.begin(), zone_volume_exp.end(), 0.0f);
    std::fill(zone_gradient_exp.begin(), zone_gradient_exp.end(), 0.0f);
    expected_gather_lanes = 0;
    for (size_t z = 0; z < zone_volume_exp.size(); ++z) {
        for (int idx = z_to_c_indptr[z]; idx < z_to_c_indptr[z + 1]; ++idx) {
            const int c = z_to_c_indices[idx];
            zone_volume_exp[z] += corner_volume[c];
        }
        if (zone_type[z] < 1)
            continue;
        for (int idx = z_to_c_indptr[z]; idx < z_to_c_indptr[z + 1]; ++idx) {
            const int c = z_to_c_indices[idx];
            const DATATYPE ratio = corner_volume[c] / zone_volume_exp[z];
            zone_gradient_exp[z] += point_gradient[c_to_p_map[c]] * ratio;
        }
    }
    expected_gather_lanes = z_to_c_indices.size();
}

struct ReferenceErrors
{
    uint64_t volume = 0;
    uint64_t gradient = 0;
    uint64_t nonfinite = 0;
    uint64_t hash = 1469598103934665603ULL;
};

static ReferenceErrors
check_scalar_reference()
{
    ReferenceErrors errors;
    for (size_t z = 0; z < zone_gradient.size(); ++z) {
        errors.nonfinite += !std::isfinite(zone_volume_tmp[z]);
        errors.nonfinite += !std::isfinite(zone_gradient[z]);
        if (value_bits(zone_volume_tmp[z]) != value_bits(zone_volume_exp[z]))
            errors.volume++;
        if (value_bits(zone_gradient[z]) != value_bits(zone_gradient_exp[z]))
            errors.gradient++;
        errors.hash =
            update_output_hash(errors.hash, z * 2, zone_volume_tmp[z]);
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
int tiles0[NUM_CORES], tiles1[NUM_CORES], tiles2[NUM_CORES], tiles3[NUM_CORES], tiles4[NUM_CORES], tilesi[NUM_CORES], tilesj[NUM_CORES];
int regs0[NUM_CORES], regs1[NUM_CORES], regs2[NUM_CORES], regs3[NUM_CORES], regs4[NUM_CORES], last_i_regs[NUM_CORES], last_j_regs[NUM_CORES];

void gradzatz_invert_CSR() {
    int num_zones = zone_gradient_exp.size();
    // Initialize zone_gradient_exp
    zone_gradient_exp.assign(num_zones, 0.0);
#ifdef GEM5
    clear_mem_region();
    // Add memory regions for used arrays in this kernel
    add_mem_region(zone_gradient_exp.data(), zone_gradient_exp.data() + zone_gradient_exp.size()); // 6
    add_mem_region(point_gradient.data(), point_gradient.data() + point_gradient.size());          // 7
    add_mem_region(corner_volume.data(), corner_volume.data() + corner_volume.size());             // 8
    add_mem_region(z_to_c_indices.data(), z_to_c_indices.data() + z_to_c_indices.size());          // 9
    add_mem_region(c_to_p_map.data(), c_to_p_map.data() + c_to_p_map.size());                      // 10
    add_mem_region(z_to_c_indptr.data(), z_to_c_indptr.data() + z_to_c_indptr.size());             // 11
    add_mem_region(zone_type.data(), zone_type.data() + zone_type.size());                         // 12
    std::cout << "ROI Begin" << std::endl;
    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
#endif
#pragma omp parallel
    {
#pragma omp for
        // For each zone, compute the gradient using CSR format
        for (int z = 0; z < num_zones; ++z) {
            if (zone_type[z] < 1)
                continue; // Only operate on local interior zones

            // Accumulate the local zone volume
            double zone_vol = 0.0;
            for (int idx = z_to_c_indptr[z]; idx < z_to_c_indptr[z + 1]; ++idx) {
                int c = z_to_c_indices[idx];
                zone_vol += corner_volume[c];
            }

            // Accumulate the zone-centered gradient
            for (int idx = z_to_c_indptr[z]; idx < z_to_c_indptr[z + 1]; ++idx) {
                int c = z_to_c_indices[idx];
                int p = c_to_p_map[c];
                double c_z_vol_ratio = corner_volume[c] / zone_vol;
#ifdef VERIFY
#pragma omp atomic
                zone_gradient_exp[z] += point_gradient[p] * c_z_vol_ratio;
#else
                zone_gradient_exp[z] += point_gradient[p] * c_z_vol_ratio;
#endif
            }
        }
    }
#ifdef GEM5
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);
    std::cout << "ROI Ended" << std::endl;
    m5_exit(0);
#endif
}

void gradzatz_invert_MAA_CSR() {
    int num_zones = zone_gradient.size();

#ifdef GEM5
    clear_mem_region();
    // Add memory regions for used arrays in this kernel
    add_mem_region(zone_gradient.data(), zone_gradient.data() + zone_gradient.size());       // 6
    add_mem_region(point_gradient.data(), point_gradient.data() + point_gradient.size());    // 7
    add_mem_region(corner_volume.data(), corner_volume.data() + corner_volume.size());       // 8
    add_mem_region(z_to_c_indices.data(), z_to_c_indices.data() + z_to_c_indices.size());    // 9
    add_mem_region(c_to_p_map.data(), c_to_p_map.data() + c_to_p_map.size());                // 10
    add_mem_region(z_to_c_indptr.data(), z_to_c_indptr.data() + z_to_c_indptr.size());       // 11
    add_mem_region(zone_type.data(), zone_type.data() + zone_type.size());                   // 12
    add_mem_region(zone_volume_tmp.data(), zone_volume_tmp.data() + zone_volume_tmp.size()); // 13
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
// For each zone, compute the gradient using CSR format
#pragma omp parallel
    {
        int reg0, reg1, reg2, reg3, reg4, last_i_reg, last_j_reg;
        int tile0, tilelb, tileub, tile3, tile5, tilei, tilej;
        int thread_id = omp_get_thread_num();
        tile5 = tiles0[thread_id];
        tilei = tiles1[thread_id];
        tilej = tiles2[thread_id];
        tile0 = tiles3[thread_id];
        tilelb = tiles4[thread_id];
        tileub = tilesi[thread_id];
        tile3 = tilesj[thread_id];
        reg0 = regs0[thread_id];
        reg1 = regs1[thread_id];
        reg2 = regs2[thread_id];
        reg3 = regs3[thread_id];
        reg4 = regs4[thread_id];
        last_i_reg = last_i_regs[thread_id];
        last_j_reg = last_j_regs[thread_id];
        maa_const<int>(num_zones, reg1);
        maa_const<int>(1, reg2);
        int *tilei_ptr = get_cacheable_tile_pointer<int>(tilei);
        DATATYPE *tile5_ptr = get_cacheable_tile_pointer<DATATYPE>(tile5);
        DATATYPE *tilej_ptr = get_cacheable_tile_pointer<DATATYPE>(tilej);
#ifdef UME_GRADZATZ_INVERT_VERIFY
        int *tile3_ptr = get_cacheable_tile_pointer<int>(tile3);
        uint64_t local_gather_errors = 0;
        uint64_t local_gather_lanes = 0;
#endif
#pragma omp for
        for (int zidx = 0; zidx < num_zones; zidx += TILE_SIZE) {
            maa_const<int>(zidx, reg0);
            // load lb and ub of z_to_c_indptr
            maa_stream_load<int>(z_to_c_indptr.data(), reg0, reg1, reg2, tilelb);
            maa_stream_load<int>(z_to_c_indptr.data() + 1, reg0, reg1, reg2, tileub);
            int curr_tilej_size = 0;
            DATATYPE *zone_vol = zone_volume_tmp.data() + zidx;
            maa_const<int>(0, last_i_reg);
            maa_const<int>(-1, last_j_reg);
            int idx_max = z_to_c_indptr[min(zidx + TILE_SIZE, num_zones)];
            int *curr_zone_type = zone_type.data() + zidx;
            float *curr_zone_gradient = zone_gradient.data() + zidx;
            maa_const(idx_max, reg3);
            for (int idx_base = z_to_c_indptr[zidx]; idx_base < idx_max; idx_base += TILE_SIZE) {
                maa_const(idx_base, reg4);
                maa_range_loop<int>(last_i_reg, last_j_reg, tilelb, tileub, reg2, tilei, tilej);
                // Step2: load c_to_z_map
                maa_stream_load<int>(z_to_c_indices.data(), reg4, reg3, reg2, tile0); // 0->c
                // Step3: load corner_volume[c]
                maa_indirect_load<DATATYPE>(corner_volume.data(), tile0, tile5);
                // Step4: accumulate zone_vol
                // Transfer tile4 assume range is after this line
                maa_indirect_rmw_vector<DATATYPE>(zone_vol, tilei, tile5, Operation_t::ADD_OP);
                wait_ready(tile0);
            }
            wait_ready(tile5);
            wait_ready(tilei);
            // double zone_vol = 0.0;
            // for (int idx = z_to_c_indptr[z]; idx < z_to_c_indptr[z + 1]; ++idx) {
            //     int c = z_to_c_indices[idx];
            //     zone_vol += corner_volume[c];
            // }

            maa_const<int>(0, last_i_reg);
            maa_const<int>(-1, last_j_reg);
            for (int idx_base = z_to_c_indptr[zidx]; idx_base < idx_max; idx_base += TILE_SIZE) {
                maa_const(idx_base, reg4);
                maa_range_loop<int>(last_i_reg, last_j_reg, tilelb, tileub, reg2, tilei, tilej);
                // Step2: load z_to_c_indices
                maa_stream_load<int>(z_to_c_indices.data(), reg4, reg3, reg2, tile0); // 0->c
                // Step3: load corner_volume[c]
                maa_indirect_load<DATATYPE>(corner_volume.data(), tile0, tile5); // 5->corner_volume
                // Transfer tile0 and tilej
                // Step4: load c_to_p_map
                maa_indirect_load<int>(c_to_p_map.data(), tile0, tile3); // 3->p
                // Step5: load point_gradient
#ifdef MAA_VIRTUAL_GATHER
                maa_indirect_load_virtual<DATATYPE>(
                    point_gradient.data(), tile3, tilej,
                    virtual_gather_backing[thread_id]);
#else
                maa_indirect_load<DATATYPE>(point_gradient.data(), tile3,
                                            tilej); // tilej->point_gradient
#endif
                curr_tilej_size = min(idx_max - idx_base, TILE_SIZE);
                wait_ready(tile0);
                wait_ready(tilei);
                wait_ready(tile3);
                wait_ready(tile5);
                wait_ready(tilej);
#ifdef MAA_VIRTUAL_GATHER
                maa_const<int>(0, reg0);
                maa_const<int>(curr_tilej_size, reg4);
                maa_stream_load<DATATYPE>(virtual_gather_backing[thread_id],
                                          reg0, reg4, reg2, tilej);
                wait_ready(tilej);
#endif
#ifdef UME_GRADZATZ_INVERT_VERIFY
                for (int j = 0; j < curr_tilej_size; ++j) {
                    if (value_bits(tilej_ptr[j]) !=
                        value_bits(point_gradient[tile3_ptr[j]]))
                        local_gather_errors++;
                    local_gather_lanes++;
                }
#endif
                for (int j = 0; j < curr_tilej_size; j++) {
                    if (curr_zone_type[tilei_ptr[j]] < 1)
                        continue; // Only operate on local interior zones
                    const DATATYPE corner = tile5_ptr[j];
                    const DATATYPE gradient = tilej_ptr[j];
                    const DATATYPE ratio = corner / zone_vol[tilei_ptr[j]];
                    curr_zone_gradient[tilei_ptr[j]] += gradient * ratio;
                }
            }
        }
        // for (int z = 0; z < num_zones; ++z) {
        //     if (zone_type[z] < 1)
        //         continue; // Only operate on local interior zones
        //             for (int idx = z_to_c_indptr[z]; idx < z_to_c_indptr[z + 1]; ++idx) {
        //                 int c = z_to_c_indices[idx];
        //                 int p = c_to_p_map[c];
        //                 double c_z_vol_ratio = corner_volume[c] / zone_vol;
        // #pragma omp atomic
        //                 zone_gradient_exp[z] += point_gradient[p] * c_z_vol_ratio;
        //             }
#ifdef UME_GRADZATZ_INVERT_VERIFY
        gather_verify_errors.fetch_add(local_gather_errors,
                                       std::memory_order_relaxed);
        gather_verify_lanes.fetch_add(local_gather_lanes,
                                      std::memory_order_relaxed);
#endif
    } // parallel
#ifdef GEM5
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);
#if defined(UME_GRADZATZ_INVERT_VERIFY) || \
    defined(UME_GRADZATZ_INVERT_OUTPUT_FINGERPRINT)
    const ReferenceErrors reference = check_scalar_reference();
    const uint64_t reference_errors =
        reference.volume + reference.gradient;
#ifdef UME_GRADZATZ_INVERT_VERIFY
    const uint64_t gather_errors = gather_verify_errors.load();
    const uint64_t gather_lanes = gather_verify_lanes.load();
    constexpr uint64_t expected_hash = 15373220985331308248ULL;
    if (gather_errors != 0 || gather_lanes != expected_gather_lanes ||
        reference_errors != 0 || reference.nonfinite != 0 ||
        reference.hash != expected_hash) {
        std::cerr << "UME_GRADZATZ_INVERT_VERIFY_FAIL gather_errors="
                  << gather_errors << " gather_lanes=" << gather_lanes
                  << " expected_lanes=" << expected_gather_lanes
                  << " reference_errors=" << reference_errors
                  << " volume_errors=" << reference.volume
                  << " gradient_errors=" << reference.gradient
                  << " elements=" << zone_gradient.size()
                  << " output_hash=" << reference.hash
                  << " nonfinite=" << reference.nonfinite << std::endl;
        std::abort();
    }
    std::cout << "UME_GRADZATZ_INVERT_VERIFY_PASS gather_errors=0 "
              << "gather_lanes=" << gather_lanes << " expected_lanes="
              << expected_gather_lanes
              << " reference_errors=0 volume_errors=0 gradient_errors=0"
              << " elements=" << zone_gradient.size()
              << " output_hash=" << reference.hash << " nonfinite=0"
              << std::endl;
#else
    constexpr uint64_t expected_hash =
        UME_GRADZATZ_INVERT_EXPECTED_HASH;
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
              << " elements=" << zone_gradient.size() << std::endl;
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

void init_inverse_map_CSR(int num_points, int num_zones, int num_corners) {
    // Build CSR format for p_to_c_map
    p_to_c_indptr.resize(num_points + 1, 0);
    std::vector<int> p_to_c_count(num_points, 0); // Temporary count array

    // First pass: count the number of entries per point
    for (int c = 0; c < num_corners; ++c) {
        int p = c_to_p_map[c];
        p_to_c_count[p]++;
    }

    // Build the indptr array
    p_to_c_indptr[0] = 0;
    for (int p = 0; p < num_points; ++p) {
        p_to_c_indptr[p + 1] = p_to_c_indptr[p] + p_to_c_count[p];
    }

    // Allocate indices array
    p_to_c_indices.resize(p_to_c_indptr[num_points]);

    // Reset count array to reuse it
    std::fill(p_to_c_count.begin(), p_to_c_count.end(), 0);

    // Second pass: fill the indices array
    for (int c = 0; c < num_corners; ++c) {
        int p = c_to_p_map[c];
        int idx = p_to_c_indptr[p] + p_to_c_count[p];
        p_to_c_indices[idx] = c;
        p_to_c_count[p]++;
    }

    // Build CSR format for z_to_c_map
    z_to_c_indptr.resize(num_zones + 1, 0);
    std::vector<int> z_to_c_count(num_zones, 0); // Temporary count array

    // First pass: count the number of entries per zone
    for (int c = 0; c < num_corners; ++c) {
        int z = c_to_z_map[c];
        z_to_c_count[z]++;
    }

    // Build the indptr array
    z_to_c_indptr[0] = 0;
    for (int z = 0; z < num_zones; ++z) {
        z_to_c_indptr[z + 1] = z_to_c_indptr[z] + z_to_c_count[z];
    }

    // Allocate indices array
    z_to_c_indices.resize(z_to_c_indptr[num_zones]);

    // Reset count array to reuse it
    z_to_c_count.assign(num_zones, 0);

    // Second pass: fill the indices array
    for (int c = 0; c < num_corners; ++c) {
        int z = c_to_z_map[c];
        int idx = z_to_c_indptr[z] + z_to_c_count[z];
        z_to_c_indices[idx] = c;
        z_to_c_count[z]++;
    }
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
    const int n = static_cast<int>(parsed_n);
#if defined(UME_GRADZATZ_INVERT_VERIFY) || \
    defined(UME_GRADZATZ_INVERT_FIXED_INPUT)
#ifdef UME_GRADZATZ_INVERT_VERIFY
    if (n != 4097) {
        std::cerr << "UME_GRADZATZ_INVERT_VERIFY requires n=4097"
                  << std::endl;
        return 2;
    }
#else
    if (n != UME_GRADZATZ_INVERT_EXPECTED_N) {
        std::cerr << "fixed-input binary requires n="
                  << UME_GRADZATZ_INVERT_EXPECTED_N << std::endl;
        return 2;
    }
#endif
#else
    srand((unsigned)time(NULL));
    float branch_bias = 0.95;
#endif

    int total_size = n;
    int num_points = total_size + 2 * PADDING_LEN;
    int num_zones = total_size + 2 * PADDING_LEN;
    int num_corners = total_size;

    corner_volume.resize(num_corners);
    c_to_z_map.resize(num_corners);
    c_to_p_map.resize(num_corners);

    zone_gradient.resize(num_zones);
    point_gradient.resize(num_points);

    zone_volume_tmp.resize(num_zones);
    point_normal.resize(num_points);
    zone_type.resize(num_zones);

    zone_gradient_exp.resize(num_zones);
    zone_volume_exp.resize(num_zones);

#if defined(UME_GRADZATZ_INVERT_VERIFY) || \
    defined(UME_GRADZATZ_INVERT_FIXED_INPUT)
    std::fill(zone_type.begin(), zone_type.end(), 0);
    for (int p = 0; p < num_points; ++p)
        point_gradient[p] = 0.5f + static_cast<float>(p % 31) * 0.125f;
    for (int c = 0; c < num_corners; ++c) {
        const int local_zone = c / 2;
        c_to_z_map[c] = PADDING_LEN + local_zone;
        c_to_p_map[c] = PADDING_LEN + ((97 * c + 13) % num_corners);
        zone_type[c_to_z_map[c]] = local_zone % 11 == 0 ? -1 : 1;
        corner_volume[c] = 1.0f + static_cast<float>(c % 7) * 0.125f;
    }
#else
    std::fill(zone_type.begin(), zone_type.end(), 1);

    // Initialize data arrays with random values
    for (int i = 0; i < num_points; ++i) {
        zone_type[i] = (rand() % 100 < branch_bias * 100) ? 1 : -1;
    }
    std::fill(point_gradient.begin(), point_gradient.end(), 1.0);
    std::fill(corner_volume.begin(), corner_volume.end(), 1.0);

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
        ;
    }
#endif
    std::fill(point_normal.begin(), point_normal.end(), 1.0f);
    std::fill(zone_volume_tmp.begin(), zone_volume_tmp.end(), 0.0f);
    std::fill(zone_gradient.begin(), zone_gradient.end(), 0.0f);
    std::fill(zone_gradient_exp.begin(), zone_gradient_exp.end(), 0.0f);
    std::fill(zone_volume_exp.begin(), zone_volume_exp.end(), 0.0f);

    init_inverse_map_CSR(num_points, num_zones, num_corners);
#if defined(UME_GRADZATZ_INVERT_VERIFY) || \
    defined(UME_GRADZATZ_INVERT_OUTPUT_FINGERPRINT)
    build_scalar_reference();
#endif

#ifdef GEM5
    cout << "Starting checkpoint" << endl;
    m5_checkpoint(0, 0);
    cout << "checkpoint done" << endl;
#endif
    alloc_MAA();
    init_MAA();

#ifndef MAA
    // Run original functions
    gradzatz_invert_CSR();
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
            tilesi[thread_id] = get_new_tile<int>();
            tilesj[thread_id] = get_new_tile<int>();
            regs0[thread_id] = get_new_reg<int>();
            regs1[thread_id] = get_new_reg<int>();
            regs2[thread_id] = get_new_reg<int>();
            regs3[thread_id] = get_new_reg<int>();
            regs4[thread_id] = get_new_reg<int>();
            last_i_regs[thread_id] = get_new_reg<int>();
            last_j_regs[thread_id] = get_new_reg<int>();
        }
    }
    gradzatz_invert_MAA_CSR();
#ifdef VERIFY
    gradzatz_invert_CSR();
    verify_results(zone_gradient_exp, zone_gradient, "zone_gradient");
#endif
#endif
#ifdef GEM5
    m5_exit(0);
#endif
    return 0;
}
