#include <omp.h>

#include <algorithm> // For std::iota and std::fill
#include <atomic>
#include <cmath>     // For std::fabs
#include <cstdint>
#include <cstdlib>   // For rand()
#include <cstring>
#include <ctime>
#include <iostream>
#include <string>
#include <vector>

using namespace std;

// #define VERIFY

#define DATATYPE float

#define DISTANCE_OTEHRS 85000
#define DISTANCE_P2C    80000
#define PADDING_LEN     90000
#define TOLERANCE       1e-3 // Tolerance for floating-point comparisons

std::vector<int> corner_type;
std::vector<int> c_to_z_map;
std::vector<int> c_to_p_map;
std::vector<DATATYPE> point_volume;
std::vector<DATATYPE> point_gradient;
std::vector<DATATYPE> corner_volume;
std::vector<DATATYPE> csurf;

std::vector<DATATYPE> zone_field;
std::vector<DATATYPE> point_normal;

std::vector<DATATYPE> point_volume_exp;
std::vector<DATATYPE> point_gradient_exp;

std::vector<int> point_type;
std::vector<int> zone_type;

#ifdef MAA_VIRTUAL_GATHER
alignas(64) static DATATYPE virtual_gather_backing[NUM_CORES][TILE_SIZE];
#endif

#if defined(UME_GATHER_VERIFY) || defined(UME_OUTPUT_FINGERPRINT)
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

static uint64_t hash_outputs(uint64_t &nonfinite) {
    uint64_t hash = 1469598103934665603ULL;
    nonfinite = 0;
    for (size_t i = 0; i < point_volume.size(); ++i) {
        if (!std::isfinite(point_volume[i]))
            nonfinite++;
        if (!std::isfinite(point_gradient[i]))
            nonfinite++;
        hash = update_output_hash(hash, i * 2, point_volume[i]);
        hash = update_output_hash(hash, i * 2 + 1, point_gradient[i]);
    }
    return hash;
}
#endif

#if defined(UME_GATHER_VERIFY) || defined(UME_FIXED_INPUT)
static void mark_points_without_active_corners() {
    std::vector<uint8_t> has_active_corner(point_type.size(), 0);
    for (size_t c = 0; c < corner_type.size(); ++c) {
        if (corner_type[c] > 0)
            has_active_corner[c_to_p_map[c]] = 1;
    }
    for (size_t p = 0; p < point_type.size(); ++p) {
        if (has_active_corner[p] == 0)
            point_type[p] = 0;
    }
}
#endif

#if defined(UME_GATHER_VERIFY) || defined(UME_OUTPUT_FINGERPRINT)
static std::atomic<uint64_t> gather_verify_errors{0};
static std::atomic<uint64_t> gather_verify_lanes{0};
static uint64_t expected_active_corners = 0;

static void build_scalar_reference() {
    std::fill(point_volume_exp.begin(), point_volume_exp.end(), 0.0f);
    std::fill(point_gradient_exp.begin(), point_gradient_exp.end(), 0.0f);
    expected_active_corners = 0;
    for (size_t c = 0; c < corner_type.size(); ++c) {
        if (corner_type[c] < 1)
            continue;
        expected_active_corners++;
        const int p = c_to_p_map[c];
        const int z = c_to_z_map[c];
        point_volume_exp[p] += corner_volume[c];
        point_gradient_exp[p] += csurf[c] * zone_field[z];
    }
    for (size_t p = 0; p < point_volume_exp.size(); ++p) {
        if (point_volume_exp[p] == 0.0f) {
            point_type[p] = 0;
            continue;
        }
        if (point_type[p] > 0) {
            point_gradient_exp[p] /= point_volume_exp[p];
        } else if (point_type[p] == -1) {
            const double ppdot = point_gradient_exp[p] * point_normal[p];
            point_gradient_exp[p] =
                (point_gradient_exp[p] - point_normal[p] * ppdot) /
                point_volume_exp[p];
        }
    }
}

struct ReferenceErrors
{
    uint64_t point_volume = 0;
    uint64_t point_gradient = 0;
};

static ReferenceErrors report_reference_errors() {
    ReferenceErrors errors;
    uint64_t reported = 0;
    for (size_t i = 0; i < point_volume.size(); ++i) {
        const uint32_t volume_bits = value_bits(point_volume[i]);
        const uint32_t expected_volume_bits = value_bits(point_volume_exp[i]);
        if (volume_bits != expected_volume_bits) {
            errors.point_volume++;
            if (reported++ < 16) {
                std::cerr << "UME_GRADZATP_VOLUME_MISMATCH index=" << i
                          << " actual_bits=" << volume_bits
                          << " expected_bits=" << expected_volume_bits
                          << " actual=" << point_volume[i]
                          << " expected=" << point_volume_exp[i]
                          << std::endl;
            }
        }

        const uint32_t gradient_bits = value_bits(point_gradient[i]);
        const uint32_t expected_gradient_bits =
            value_bits(point_gradient_exp[i]);
        if (gradient_bits != expected_gradient_bits) {
            errors.point_gradient++;
            if (reported++ < 16) {
                std::cerr << "UME_GRADZATP_GRADIENT_MISMATCH index=" << i
                          << " actual_bits=" << gradient_bits
                          << " expected_bits=" << expected_gradient_bits
                          << " actual=" << point_gradient[i]
                          << " expected=" << point_gradient_exp[i]
                          << std::endl;
            }
        }
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
int tiles3[NUM_CORES], tiles4[NUM_CORES], tiles5[NUM_CORES];
int regs0[NUM_CORES], regs1[NUM_CORES], regs2[NUM_CORES];
#ifdef MAA_GENERAL_VIRTUAL_CONSUMER
int page_regs0[NUM_CORES], page_regs1[NUM_CORES], page_regs2[NUM_CORES];
#endif
int regs3[NUM_CORES], regs4[NUM_CORES];

void gradzatp() {
    int pll = point_volume_exp.size();
    int cl = corner_type.size();

#ifdef GEM5
    clear_mem_region();
    // Add memory regions for used arrays in this kernel
    add_mem_region(point_volume_exp.data(), point_volume_exp.data() + point_volume_exp.size());       // 6
    add_mem_region(point_gradient_exp.data(), point_gradient_exp.data() + point_gradient_exp.size()); // 7
    add_mem_region(corner_volume.data(), corner_volume.data() + corner_volume.size());                // 8
    add_mem_region(csurf.data(), csurf.data() + csurf.size());                                        // 9
    add_mem_region(zone_field.data(), zone_field.data() + zone_field.size());                         // 10
    add_mem_region(c_to_p_map.data(), c_to_p_map.data() + c_to_p_map.size());                         // 11
    add_mem_region(c_to_z_map.data(), c_to_z_map.data() + c_to_z_map.size());                         // 12
    add_mem_region(point_type.data(), point_type.data() + point_type.size());                         // 13
    add_mem_region(corner_type.data(), corner_type.data() + corner_type.size());                      // 14
    add_mem_region(point_normal.data(), point_normal.data() + point_normal.size());                   // 15
    std::cout << "ROI Begin" << std::endl;
    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
#endif
#pragma omp parallel
    {
#pragma omp for
        for (int c = 0; c < cl; ++c) {
            if (corner_type[c] < 1)
                continue; // Only operate on interior corners
            int const z = c_to_z_map[c];
            int const p = c_to_p_map[c];
#pragma omp atomic
            point_volume_exp[p] += corner_volume[c];
#pragma omp atomic
            point_gradient_exp[p] += csurf[c] * zone_field[z];
        }
        /*
        Divide by point control volume to get gradient. If a point is on the outer
        perimeter of the mesh (POINT_TYPE=-1), subtract the outward normal component
        of the gradient using the point normals.
        */
#pragma omp for
        for (int p = 0; p < pll; ++p) {
            if (point_type[p] > 0) {
                // Internal point
                point_gradient_exp[p] /= point_volume_exp[p];
            } else if (point_type[p] == -1) {
                double const ppdot = point_gradient_exp[p] * point_normal[p];
                point_gradient_exp[p] = (point_gradient_exp[p] - point_normal[p] * ppdot) / point_volume_exp[p];
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

void gradzatp_MAA() {
    int pll = point_volume.size();
    int cl = corner_type.size();

#ifdef GEM5
    clear_mem_region();
    // Add memory regions for used arrays in this kernel
    add_mem_region(point_volume.data(), point_volume.data() + point_volume.size());       // 6
    add_mem_region(point_gradient.data(), point_gradient.data() + point_gradient.size()); // 7
    add_mem_region(corner_volume.data(), corner_volume.data() + corner_volume.size());    // 8
    add_mem_region(csurf.data(), csurf.data() + csurf.size());                            // 9
    add_mem_region(zone_field.data(), zone_field.data() + zone_field.size());             // 10
    add_mem_region(c_to_p_map.data(), c_to_p_map.data() + c_to_p_map.size());             // 11
    add_mem_region(c_to_z_map.data(), c_to_z_map.data() + c_to_z_map.size());             // 12
    add_mem_region(point_type.data(), point_type.data() + point_type.size());             // 13
    add_mem_region(corner_type.data(), corner_type.data() + corner_type.size());          // 14
#ifdef MAA_VIRTUAL_GATHER
    for (int core = 0; core < NUM_CORES; ++core) {
        add_mem_region(virtual_gather_backing[core],
                       virtual_gather_backing[core] + TILE_SIZE);
    }
#else
    add_mem_region(point_normal.data(),
                   point_normal.data() + point_normal.size()); // 15
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
        int tile0, tile1, tile2, tile3, tile4, tileCond;
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
        backing_start_reg = regs3[omp_thread_id];
        backing_end_reg = regs4[omp_thread_id];
#endif
        tile0 = tiles0[omp_thread_id];
        tile1 = tiles1[omp_thread_id];
        tile2 = tiles2[omp_thread_id];
        tileCond = tiles4[omp_thread_id];
        tile3 = tiles3[omp_thread_id];
        tile4 = tiles5[omp_thread_id];
        maa_const<int>(1, reg2);
        maa_const<int>(cl, reg1);
#if defined(MAA_VIRTUAL_GATHER) && !defined(MAA_GENERAL_VIRTUAL_CONSUMER)
        maa_const<int>(0, backing_start_reg);
#endif
#ifdef UME_GATHER_VERIFY
        int *tile_cond_ptr = get_cacheable_tile_pointer<int>(tileCond);
#ifndef MAA_GENERAL_VIRTUAL_CONSUMER
        int *tile3_ptr = get_cacheable_tile_pointer<int>(tile3);
#endif
        DATATYPE *tile0_ptr = get_cacheable_tile_pointer<DATATYPE>(tile0);
        uint64_t local_gather_errors = 0;
        uint64_t local_gather_lanes = 0;
#endif
#pragma omp for
        for (int c = 0; c < cl; c += TILE_SIZE) {
#if defined(MAA_VIRTUAL_GATHER) || defined(UME_GATHER_VERIFY)
            const int gather_size = std::min(cl - c, TILE_SIZE);
#endif
#ifdef MAA_GENERAL_VIRTUAL_CONSUMER
            maa_const<int>(c, reg0);
            maa_const<int>(c + gather_size, reg1);
            maa_indirect_load_virtual_index<DATATYPE>(
                zone_field.data(),
                reinterpret_cast<uint32_t *>(c_to_z_map.data()), tile3,
                virtual_gather_backing[omp_thread_id], reg0, reg1, reg2);
            if (gather_size == TILE_SIZE)
                maa_virtual_consumer_begin(virtual_consumer_mode, tile3);
            else
                // The materializer ABI is exactly four complete 4K pages.
                // Drain a partial tail and use the ordinary stream control so
                // it cannot silently enter the mechanism fallback path.
                wait_ready(tile3);

            for (int page_offset = 0; page_offset < gather_size;
                 page_offset += MAA_CONSUMER_TILE_SIZE) {
                const int page_size =
                    std::min(gather_size - page_offset,
                             MAA_CONSUMER_TILE_SIZE);
                const int page_begin = c + page_offset;
                maa_const<int>(page_begin, reg0);
                maa_const<int>(page_begin + page_size, reg1);

                // The condition, maps, RMW, ALU, and final RMW remain ordinary
                // operations on a physical 4K tile.  Only the backing reload
                // is varied by the deferred consumer selector.
                maa_stream_load<int>(corner_type.data(), reg0, reg1, reg2,
                                     tile0);
                maa_alu_scalar<int>(tile0, reg2, tileCond,
                                    Operation_t::GTE_OP);
                maa_stream_load<int>(c_to_p_map.data(), reg0, reg1, reg2,
                                     tile4, tileCond);
                maa_stream_load<DATATYPE>(corner_volume.data(), reg0, reg1,
                                          reg2, tile0, tileCond);
                maa_indirect_rmw_vector<DATATYPE>(
                    point_volume.data(), tile4, tile0,
                    Operation_t::ADD_OP, tileCond);
                maa_stream_load<DATATYPE>(csurf.data(), reg0, reg1, reg2,
                                          tile1, tileCond);

                if (gather_size == TILE_SIZE) {
                    maa_virtual_consumer_load_page<DATATYPE>(
                        virtual_consumer_mode,
                        virtual_gather_backing[omp_thread_id] + page_offset,
                        tile3, page_offset / MAA_CONSUMER_TILE_SIZE,
                        page_min_reg, page_max_reg, page_stride_reg, tile0);
                } else {
                    maa_stream_load<DATATYPE>(
                        virtual_gather_backing[omp_thread_id] + page_offset,
                        page_min_reg, page_max_reg, page_stride_reg, tile0);
                }
#ifdef UME_GATHER_VERIFY
                wait_ready(tile0);
                for (int i = 0; i < page_size; ++i) {
                    if (tile_cond_ptr[i] == 0)
                        continue;
                    if (value_bits(tile0_ptr[i]) !=
                        value_bits(zone_field[
                            c_to_z_map[page_begin + i]]))
                        local_gather_errors++;
                    local_gather_lanes++;
                }
#endif
                maa_alu_vector<DATATYPE>(tile1, tile0, tile2,
                                          Operation_t::MUL_OP, tileCond);
                maa_indirect_rmw_vector<DATATYPE>(
                    point_gradient.data(), tile4, tile2,
                    Operation_t::ADD_OP, tileCond);
                wait_ready(tile1);
            }
            if (gather_size == TILE_SIZE)
                maa_virtual_consumer_end(virtual_consumer_mode, tile3);
#else
            maa_const<int>(c, reg0);
            // Step1: Load corner_type
            maa_stream_load<int>(corner_type.data(), reg0, reg1, reg2, tile0);
            // Step2: Perform comparison
            maa_alu_scalar<int>(tile0, reg2, tileCond, Operation_t::GTE_OP);
            // for (int i = 0; i < TILE_SIZE; i++){
            //     cout << "tileCond[" << i << "]: " << tile_cond_ptr[i] << endl;
            // }
            // Step3: Load c_to_z_map and c_to_p_map
            maa_stream_load<int>(c_to_z_map.data(), reg0, reg1, reg2, tile3, tileCond);
            maa_stream_load<int>(c_to_p_map.data(), reg0, reg1, reg2, tile4, tileCond);

            // Step4: Load corner_volume[c], zone_field[z], and csurf[c]
            maa_stream_load<DATATYPE>(corner_volume.data(), reg0, reg1, reg2, tile0, tileCond);
            maa_indirect_rmw_vector<DATATYPE>(point_volume.data(), tile4, tile0, Operation_t::ADD_OP, tileCond);
            // transfer tile4, tileCond, tile3
#ifdef MAA_VIRTUAL_GATHER
            maa_indirect_load_virtual<DATATYPE>(
                zone_field.data(), tile3, tile0,
                virtual_gather_backing[omp_thread_id], tileCond);
#else
            maa_indirect_load<DATATYPE>(zone_field.data(), tile3, tile0,
                                        tileCond);
#endif
            maa_stream_load<DATATYPE>(csurf.data(), reg0, reg1, reg2, tile1,
                                      tileCond);
#ifdef MAA_VIRTUAL_GATHER
            wait_ready(tile0);
            maa_const<int>(gather_size, backing_end_reg);
            maa_stream_load<DATATYPE>(virtual_gather_backing[omp_thread_id],
                                      backing_start_reg, backing_end_reg, reg2,
                                      tile0);
#endif
#ifdef UME_GATHER_VERIFY
            wait_ready(tile0);
            for (int i = 0; i < gather_size; ++i) {
                if (tile_cond_ptr[i] == 0)
                    continue;
                if (value_bits(tile0_ptr[i]) !=
                    value_bits(zone_field[tile3_ptr[i]]))
                    local_gather_errors++;
                local_gather_lanes++;
            }
#endif
            // DO ALU operation
            maa_alu_vector<DATATYPE>(tile1, tile0, tile2, Operation_t::MUL_OP, tileCond);
            // rmw to point_gradient
            maa_indirect_rmw_vector<DATATYPE>(point_gradient.data(), tile4, tile2, Operation_t::ADD_OP, tileCond);
            wait_ready(tile1);
            // if (corner_type[c] < 1)
            //     continue; // Only operate on interior corners
            // int const z = c_to_z_map[c];
            // int const p = c_to_p_map[c];
            // point_volume[p] += corner_volume[c];
            // point_gradient[p] += csurf[c] * zone_field[z];
#endif
        }
        wait_ready(tile2);

        // Do not normalize while another thread still has an RMW in flight.
#pragma omp barrier
/*
        Divide by point control volume to get gradient. If a point is on the outer
        perimeter of the mesh (POINT_TYPE=-1), subtract the outward normal component
        of the gradient using the point normals.
        */
#pragma omp for
        for (int p = 0; p < pll; ++p) {
            if (point_type[p] > 0) {
                // Internal point
                point_gradient[p] /= point_volume[p];
            } else if (point_type[p] == -1) {
                double const ppdot = point_gradient[p] * point_normal[p];
                point_gradient[p] = (point_gradient[p] - point_normal[p] * ppdot) / point_volume[p];
            }
        } // for
#ifdef UME_GATHER_VERIFY
        gather_verify_errors.fetch_add(local_gather_errors,
                                       std::memory_order_relaxed);
        gather_verify_lanes.fetch_add(local_gather_lanes,
                                      std::memory_order_relaxed);
#endif
    } // omp parallel
#ifdef GEM5
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);
#if defined(UME_GATHER_VERIFY) || defined(UME_OUTPUT_FINGERPRINT)
    uint64_t nonfinite;
    const uint64_t output_hash = hash_outputs(nonfinite);
    const ReferenceErrors reference_errors = report_reference_errors();
    const uint64_t reference_error_count =
        reference_errors.point_volume + reference_errors.point_gradient;
#endif
#ifdef UME_GATHER_VERIFY
    const uint64_t gather_errors = gather_verify_errors.load();
    const uint64_t gather_lanes = gather_verify_lanes.load();
    if (gather_errors != 0 || gather_lanes != expected_active_corners ||
        reference_error_count != 0 || nonfinite != 0) {
        std::cerr << "UME_GRADZATP_VERIFY_FAIL gather_errors="
                  << gather_errors << " gather_lanes=" << gather_lanes
                  << " expected_lanes=" << expected_active_corners
                  << " reference_errors=" << reference_error_count
                  << " volume_errors=" << reference_errors.point_volume
                  << " gradient_errors=" << reference_errors.point_gradient
                  << " elements=" << point_volume.size()
                  << " output_hash=" << output_hash
                  << " nonfinite=" << nonfinite << std::endl;
        std::abort();
    }
    std::cout << "UME_GRADZATP_VERIFY_PASS gather_errors=0 gather_lanes="
              << gather_lanes << " expected_lanes="
              << expected_active_corners
              << " reference_errors=0 elements=" << point_volume.size()
              << " output_hash=" << output_hash << " nonfinite=0"
              << std::endl;
#elif defined(UME_OUTPUT_FINGERPRINT)
    if (reference_error_count != 0 || nonfinite != 0) {
        std::cerr << "UME_OUTPUT_FP_FAIL output_hash=" << output_hash
                  << " reference_volume_errors="
                  << reference_errors.point_volume
                  << " reference_gradient_errors="
                  << reference_errors.point_gradient
                  << " nonfinite=" << nonfinite << std::endl;
        std::abort();
    }
    std::cout << "UME_OUTPUT_FP output_hash=" << output_hash
              << " nonfinite=" << nonfinite << std::endl;
    std::cout << "UME_REFERENCE_PASS point_volume_errors=0 "
              << "point_gradient_errors=0 elements=" << point_volume.size()
              << std::endl;
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

#if defined(UME_GATHER_VERIFY) || defined(UME_FIXED_INPUT)
    srand(1);
#else
    srand((unsigned)time(NULL));
#endif
    int n = stoi(argv[1]);
#ifdef MAA_GENERAL_VIRTUAL_CONSUMER
    if (argc != 3) {
        std::cerr << "general virtual consumer requires a deferred selector "
                     "path after n"
                  << std::endl;
        return 2;
    }
    const std::string virtual_consumer_selector = argv[2];
#endif
    float branch_bias = 0.95;

    int total_size = n;
    int num_points = total_size + 2 * PADDING_LEN;
    int num_zones = total_size + 2 * PADDING_LEN;
    int num_corners = total_size;

    corner_type.resize(num_corners);
    csurf.resize(num_corners);
    corner_volume.resize(num_corners);
    c_to_z_map.resize(num_corners);
    c_to_p_map.resize(num_corners);

    point_volume.resize(num_points);
    point_gradient.resize(num_points);

    zone_field.resize(num_zones);
    point_normal.resize(num_points);
    point_type.resize(num_points);
    zone_type.resize(num_zones);

    point_volume_exp.resize(num_points);
    point_gradient_exp.resize(num_points);

    // Initialize point_type and zone_type (assuming all internal for simplicity)
    std::fill(point_type.begin(), point_type.end(), 1);
    std::fill(zone_type.begin(), zone_type.end(), 1);

    // Initialize data arrays with random values
    for (int i = 0; i < num_corners; ++i) {
        corner_type[i] = (rand() % 100 < branch_bias * 100) ? 1 : -1;
    }
    std::fill(point_normal.begin(), point_normal.end(), 1.0);
#if defined(UME_GATHER_VERIFY) || defined(UME_FIXED_INPUT)
    std::fill(corner_volume.begin(), corner_volume.end(), 1.0f);
    std::fill(csurf.begin(), csurf.end(), 1.0f);
    for (int i = 0; i < num_zones; ++i)
        zone_field[i] = static_cast<float>(i + 1);
#else
    std::fill(corner_volume.begin(), corner_volume.end(), 1.0);
    std::fill(csurf.begin(), csurf.end(), 1.0);
    std::fill(zone_field.begin(), zone_field.end(), 1.0);
#endif

    std::fill(point_volume.begin(), point_volume.end(), 0.0);
    std::fill(point_gradient.begin(), point_gradient.end(), 0.0);

    std::fill(point_volume_exp.begin(), point_volume_exp.end(), 0.0);
    std::fill(point_gradient_exp.begin(), point_gradient_exp.end(), 0.0);

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

#if defined(UME_GATHER_VERIFY) || defined(UME_FIXED_INPUT)
    mark_points_without_active_corners();
#endif
#if defined(UME_GATHER_VERIFY) || defined(UME_OUTPUT_FINGERPRINT)
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
                "GZP does not have two free alternating consumer tiles");
    } catch (const std::exception &error) {
        std::cerr << "GZP virtual consumer selector: " << error.what()
                  << std::endl;
        return 2;
    }
    std::cout << "UME_GZP_VIRTUAL_CONSUMER mode="
              << maa_virtual_consumer_mode_name(virtual_consumer_mode)
              << " logical=" << TILE_SIZE
              << " consumer=" << MAA_CONSUMER_TILE_SIZE << std::endl;
#endif
#endif
    alloc_MAA();
    init_MAA();

#ifndef MAA
    // Run original functions
    gradzatp();
#else

#pragma omp parallel
    {
#pragma omp critical
        {
            int thread_id = omp_get_thread_num();
            tiles0[thread_id] = get_new_tile<int>();
            tiles1[thread_id] = get_new_tile<int>();
            tiles2[thread_id] = get_new_tile<int>();
            tiles3[thread_id] = get_new_tile<int>();
            tiles4[thread_id] = get_new_tile<int>();
            tiles5[thread_id] = get_new_tile<int>();
            regs0[thread_id] = get_new_reg<int>();
            regs1[thread_id] = get_new_reg<int>();
            regs2[thread_id] = get_new_reg<int>();
#ifdef MAA_GENERAL_VIRTUAL_CONSUMER
            page_regs0[thread_id] = get_new_reg<int>(0);
            page_regs1[thread_id] = get_new_reg<int>(MAA_CONSUMER_TILE_SIZE);
            page_regs2[thread_id] = get_new_reg<int>(1);
#endif
#ifdef MAA_VIRTUAL_GATHER
            regs3[thread_id] = get_new_reg<int>();
            regs4[thread_id] = get_new_reg<int>();
#endif
        }
    }
    gradzatp_MAA();
#ifdef VERIFY
    gradzatp();
    verify_results(point_volume_exp, point_volume, "point_volume");
    verify_results(point_gradient_exp, point_gradient, "point_gradient");
#endif
#endif
#ifdef GEM5
    m5_exit(0);
#endif
    return 0;
}
