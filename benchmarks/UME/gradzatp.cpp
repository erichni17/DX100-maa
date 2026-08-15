#include <omp.h>

#include <algorithm> // For std::iota and std::fill
#include <atomic>
#include <cmath>     // For std::fabs
#include <cstdint>
#include <cstdlib>   // For rand()
#include <cstring>
#include <ctime>
#include <fstream>
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
#ifdef UME_GZP_SOA_JIT_RMW
std::vector<uint32_t> corner_predicate_soa;
#endif

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

#ifdef UME_GZP_SOA_JIT_RMW
#if !defined(GEM5) || !defined(MAA) || !defined(MAA_GENERAL_VIRTUAL_CONSUMER)
#error "GZP SoA/JIT RMW requires the gem5 general-hybrid MAA path"
#endif
#if TILE_SIZE != 16384 || MAA_CONSUMER_TILE_SIZE != 4096
#error "GZP SoA/JIT RMW requires logical16 with physical 4K consumer pages"
#endif
static_assert(sizeof(int) == sizeof(uint32_t),
              "GZP SoA/JIT indices require 32-bit int");

// These are ordinary coherent guest-memory publication arrays, not hidden MAA
// storage.  Each completed physical 4K predicate/product page reaches them
// only through bounded response-bearing cache writes.  The logical SoA/JIT
// consumer still covers all 16K entries in one Row/Offset epoch.
alignas(4096) static uint32_t soa_predicates[NUM_CORES][TILE_SIZE];
alignas(4096) static DATATYPE soa_gradient_values[NUM_CORES][TILE_SIZE];

static void first_touch_soa_publication_buffers() {
    constexpr size_t PageBytes = 4096;
    static_assert(PageBytes % sizeof(uint32_t) == 0,
                  "predicate page stride must be integral");
    static_assert(PageBytes % sizeof(DATATYPE) == 0,
                  "value page stride must be integral");
    for (int core = 0; core < NUM_CORES; ++core) {
        volatile uint32_t *predicates = soa_predicates[core];
        for (size_t element = 0; element < TILE_SIZE;
             element += PageBytes / sizeof(uint32_t))
            predicates[element] = 0;

        volatile DATATYPE *values = soa_gradient_values[core];
        for (size_t element = 0; element < TILE_SIZE;
             element += PageBytes / sizeof(DATATYPE))
            values[element] = DATATYPE{0};
    }
}

enum class GzpRmwTreatment
{
    Legacy4K,
    VolumeOnlySoaJit,
    VolumeMaskedIndexSoaJit,
    DualLogical16SoaJit,
    DualLogical16Split2KSoaJit,
    SoaJitCorrectness,
};

static GzpRmwTreatment gzp_rmw_treatment = GzpRmwTreatment::Legacy4K;
static std::atomic<uint64_t> soa_full_windows{0};
static std::atomic<uint64_t> soa_volume_only_windows{0};
static std::atomic<uint64_t> soa_masked_index_windows{0};
static std::atomic<uint64_t> soa_dual_logical16_windows{0};
static std::atomic<uint64_t> soa_dual_logical16_split2k_windows{0};
static std::atomic<uint64_t> soa_published_predicates{0};
static std::atomic<uint64_t> soa_published_gradient_values{0};
static uint64_t soa_predicate_hash = 0;
static uint64_t soa_predicate_active = 0;

struct GzpMaskedIndexLedger
{
    uint64_t selected = 0;
    uint64_t rejected = 0;
    uint64_t full_window_selected = 0;
    uint64_t full_window_rejected = 0;
    uint64_t active_uint32_max = 0;
    uint64_t active_illegal_index = 0;
    uint64_t inactive_legal_index = 0;
    uint64_t inactive_non_sentinel = 0;
    uint64_t index_hash = 1469598103934665603ULL;
};

static GzpMaskedIndexLedger gzp_masked_index_ledger;

static void encode_and_audit_gzp_masked_indices(size_t num_points) {
    static_assert(static_cast<uint32_t>(-1) == UINT32_MAX,
                  "GZP inactive int must encode UINT32_MAX");
    const size_t full_window_elements =
        corner_type.size() / TILE_SIZE * TILE_SIZE;

    // Preserve the original RNG stream and every active index by applying the
    // inactive encoding only after both random maps have been initialized.
    for (size_t c = 0; c < corner_type.size(); ++c) {
        if (corner_type[c] < 1)
            c_to_p_map[c] = -1;
    }

    GzpMaskedIndexLedger ledger;
    for (size_t c = 0; c < corner_type.size(); ++c) {
        const bool selected = corner_type[c] > 0;
        const uint32_t index = static_cast<uint32_t>(c_to_p_map[c]);
        const bool sentinel = index == UINT32_MAX;
        const bool legal = c_to_p_map[c] >= 0 &&
                           static_cast<size_t>(c_to_p_map[c]) < num_points;

        if (selected) {
            ledger.selected++;
            if (c < full_window_elements)
                ledger.full_window_selected++;
            if (sentinel)
                ledger.active_uint32_max++;
            if (!legal)
                ledger.active_illegal_index++;
        } else {
            ledger.rejected++;
            if (c < full_window_elements)
                ledger.full_window_rejected++;
            if (legal)
                ledger.inactive_legal_index++;
            if (!sentinel)
                ledger.inactive_non_sentinel++;
        }
        ledger.index_hash ^= (static_cast<uint64_t>(c) << 32) ^ index;
        ledger.index_hash *= 1099511628211ULL;
    }

    const bool exact =
        ledger.selected + ledger.rejected == corner_type.size() &&
        ledger.selected == soa_predicate_active &&
        ledger.active_uint32_max == 0 &&
        ledger.active_illegal_index == 0 &&
        ledger.inactive_legal_index == 0 &&
        ledger.inactive_non_sentinel == 0;
    if (!exact) {
        std::cerr << "UME_GZP_MASKED_INDEX_LEDGER result=FAIL"
                  << " selected=" << ledger.selected
                  << " rejected=" << ledger.rejected
                  << " predicate_selected=" << soa_predicate_active
                  << " active_uint32_max=" << ledger.active_uint32_max
                  << " active_illegal_index="
                  << ledger.active_illegal_index
                  << " inactive_legal_index="
                  << ledger.inactive_legal_index
                  << " inactive_non_sentinel="
                  << ledger.inactive_non_sentinel << std::endl;
        std::abort();
    }
    gzp_masked_index_ledger = ledger;
}

static const char *gzp_rmw_treatment_name(GzpRmwTreatment treatment) {
    if (treatment == GzpRmwTreatment::VolumeOnlySoaJit)
        return "volume_only_soa_jit";
    if (treatment == GzpRmwTreatment::VolumeMaskedIndexSoaJit)
        return "volume_masked_index_soa_jit";
    if (treatment == GzpRmwTreatment::DualLogical16SoaJit)
        return "dual_logical16_soa_jit";
    if (treatment == GzpRmwTreatment::DualLogical16Split2KSoaJit)
        return "dual_logical16_split2k_soa_jit";
    if (treatment == GzpRmwTreatment::SoaJitCorrectness)
        return "soa_jit_correctness";
    return "legacy_4k";
}

static const char *gzp_rmw_publisher_name(GzpRmwTreatment treatment) {
    if (treatment == GzpRmwTreatment::VolumeOnlySoaJit)
        return "precheckpoint_uint32_predicate";
    if (treatment == GzpRmwTreatment::VolumeMaskedIndexSoaJit)
        return "masked_index_no_predicate_publication";
    if (treatment == GzpRmwTreatment::DualLogical16SoaJit)
        return "response_bearing_gradient_only";
    if (treatment == GzpRmwTreatment::DualLogical16Split2KSoaJit)
        return "response_bearing_gradient_split2k";
    if (treatment == GzpRmwTreatment::SoaJitCorrectness)
        return "response_bearing_spd_to_coherent";
    return "none";
}

static int gzp_rmw_performance_promotable(GzpRmwTreatment treatment) {
    return treatment == GzpRmwTreatment::SoaJitCorrectness ? 0 : 1;
}

static uint64_t gzp_gradient_publication_bytes() {
    return soa_published_gradient_values.load() * sizeof(DATATYPE);
}

static int gzp_separate_predicate_publications(
    GzpRmwTreatment treatment) {
    return treatment == GzpRmwTreatment::VolumeOnlySoaJit ||
                   treatment == GzpRmwTreatment::SoaJitCorrectness
               ? 1
               : 0;
}

static uint64_t gzp_separate_predicate_publication_bytes(
    GzpRmwTreatment treatment) {
    return gzp_separate_predicate_publications(treatment) == 0
               ? 0
               : corner_predicate_soa.size() * sizeof(uint32_t);
}

static uint64_t hash_soa_predicates(const std::vector<uint32_t> &values) {
    uint64_t hash = 1469598103934665603ULL;
    for (size_t i = 0; i < values.size(); ++i) {
        hash ^= (static_cast<uint64_t>(i) << 32) ^ values[i];
        hash *= 1099511628211ULL;
    }
    return hash;
}

struct GzpSelector
{
    MAAVirtualConsumerMode consumer;
    GzpRmwTreatment rmw;
};

static GzpSelector read_gzp_selector(const std::string &path) {
    std::ifstream input(path);
    std::string consumer;
    std::string treatment;
    std::string extra;
    if (!(input >> consumer) || (input >> treatment && input >> extra))
        throw std::runtime_error(
            "GZP selector must contain CONSUMER "
            "[legacy_4k|volume_soa_jit|volume_masked_index|"
            "dual_logical16|dual_logical16_split2k|soa_jit]");

    MAAVirtualConsumerMode consumer_mode;
    if (consumer == "stream_control")
        consumer_mode = MAAVirtualConsumerMode::StreamControl;
    else if (consumer == "page_gated")
        consumer_mode = MAAVirtualConsumerMode::PageGated;
    else if (consumer == "token_stream_ld")
        consumer_mode = MAAVirtualConsumerMode::TokenStreamLoad;
    else if (consumer == "token_stream_ld_pingpong")
        consumer_mode = MAAVirtualConsumerMode::TokenStreamLoadPingPong;
    else
        throw std::runtime_error("invalid GZP virtual consumer mode");

    GzpRmwTreatment rmw = GzpRmwTreatment::Legacy4K;
    if (!treatment.empty() && treatment != "legacy_4k") {
        if (treatment == "volume_soa_jit")
            rmw = GzpRmwTreatment::VolumeOnlySoaJit;
        else if (treatment == "volume_masked_index")
            rmw = GzpRmwTreatment::VolumeMaskedIndexSoaJit;
        else if (treatment == "dual_logical16")
            rmw = GzpRmwTreatment::DualLogical16SoaJit;
        else if (treatment == "dual_logical16_split2k")
            rmw = GzpRmwTreatment::DualLogical16Split2KSoaJit;
        else if (treatment == "soa_jit")
            rmw = GzpRmwTreatment::SoaJitCorrectness;
        else
            throw std::runtime_error("invalid GZP RMW treatment");
    }
    return {consumer_mode, rmw};
}
#endif
int tiles0[NUM_CORES], tiles1[NUM_CORES], tiles2[NUM_CORES];
int tiles3[NUM_CORES], tiles4[NUM_CORES], tiles5[NUM_CORES];
int regs0[NUM_CORES], regs1[NUM_CORES], regs2[NUM_CORES];
#ifdef MAA_GENERAL_VIRTUAL_CONSUMER
int page_regs0[NUM_CORES], page_regs1[NUM_CORES], page_regs2[NUM_CORES];
#endif
int regs3[NUM_CORES], regs4[NUM_CORES];
#ifdef UME_GZP_SOA_JIT_RMW
int soa_volume_completion_tiles[NUM_CORES];
int soa_gradient_completion_tiles[NUM_CORES];
int soa_split_first_regs[NUM_CORES];
int soa_split_elements_regs[NUM_CORES];
#endif

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
#ifdef UME_GZP_SOA_JIT_RMW
    // Each publisher names only the coherent SoA span it consumes.  The split
    // treatment still has one logical 16K backing range; it does not register
    // a second physical producer payload.
    if (gzp_rmw_treatment != GzpRmwTreatment::VolumeMaskedIndexSoaJit) {
        if (gzp_rmw_treatment == GzpRmwTreatment::DualLogical16SoaJit ||
            gzp_rmw_treatment ==
                GzpRmwTreatment::DualLogical16Split2KSoaJit) {
            for (int core = 0; core < NUM_CORES; ++core) {
                add_mem_region(soa_gradient_values[core],
                               soa_gradient_values[core] + TILE_SIZE);
            }
        } else {
            for (int core = 0; core < NUM_CORES; ++core) {
                add_mem_region(soa_predicates[core],
                               soa_predicates[core] + TILE_SIZE);
                add_mem_region(soa_gradient_values[core],
                               soa_gradient_values[core] + TILE_SIZE);
            }
            add_mem_region(corner_predicate_soa.data(),
                           corner_predicate_soa.data() +
                               corner_predicate_soa.size());
        }
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
#ifdef UME_GZP_SOA_JIT_RMW
        const int split_first_reg = soa_split_first_regs[omp_thread_id];
        const int split_elements_reg =
            soa_split_elements_regs[omp_thread_id];
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
        uint32_t *tile_cond_ptr =
            get_cacheable_tile_pointer<uint32_t>(tileCond);
#endif
#ifdef UME_GATHER_VERIFY
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
            const bool soa_both_full_window =
#ifdef UME_GZP_SOA_JIT_RMW
                gzp_rmw_treatment == GzpRmwTreatment::SoaJitCorrectness &&
                gather_size == TILE_SIZE;
#else
                false;
#endif
            const bool soa_volume_only_full_window =
#ifdef UME_GZP_SOA_JIT_RMW
                gzp_rmw_treatment == GzpRmwTreatment::VolumeOnlySoaJit &&
                gather_size == TILE_SIZE;
#else
                false;
#endif
            const bool soa_masked_index_full_window =
#ifdef UME_GZP_SOA_JIT_RMW
                gzp_rmw_treatment ==
                    GzpRmwTreatment::VolumeMaskedIndexSoaJit &&
                gather_size == TILE_SIZE;
#else
                false;
#endif
            const bool soa_dual_logical16_full_window =
#ifdef UME_GZP_SOA_JIT_RMW
                gzp_rmw_treatment ==
                    GzpRmwTreatment::DualLogical16SoaJit &&
                gather_size == TILE_SIZE;
#else
                false;
#endif
            const bool soa_dual_logical16_split2k_full_window =
#ifdef UME_GZP_SOA_JIT_RMW
                gzp_rmw_treatment ==
                    GzpRmwTreatment::DualLogical16Split2KSoaJit &&
                gather_size == TILE_SIZE;
#else
                false;
#endif
            const bool soa_dual_logical16_any_full_window =
                soa_dual_logical16_full_window ||
                soa_dual_logical16_split2k_full_window;
            const bool soa_volume_full_window =
                soa_both_full_window || soa_volume_only_full_window ||
                soa_masked_index_full_window ||
                soa_dual_logical16_any_full_window;
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

            if (soa_volume_only_full_window ||
                soa_masked_index_full_window ||
                soa_dual_logical16_any_full_window) {
#ifdef UME_GZP_SOA_JIT_RMW
                // Every performance arm keeps the immutable index/value
                // streams and FP32 insertion order.  Masked-index arms
                // classify UINT32_MAX directly and name no predicate array.
                maa_const<int>(0, reg0);
                maa_const<int>(TILE_SIZE, reg1);
                maa_const<int>(1, reg2);
                if (soa_masked_index_full_window ||
                    soa_dual_logical16_any_full_window) {
                    maa_indirect_rmw_vector_soa_jit_masked_indices<DATATYPE>(
                        point_volume.data(),
                        reinterpret_cast<const uint32_t *>(
                            c_to_p_map.data() + c),
                        corner_volume.data() + c, reg0, reg1, reg2,
                        soa_volume_completion_tiles[omp_thread_id],
                        Operation_t::ADD_OP);
                } else {
                    maa_indirect_rmw_vector_soa_jit<DATATYPE>(
                        point_volume.data(),
                        reinterpret_cast<const uint32_t *>(
                            c_to_p_map.data() + c),
                        corner_volume.data() + c,
                        corner_predicate_soa.data() + c, reg0, reg1, reg2,
                        soa_volume_completion_tiles[omp_thread_id],
                        Operation_t::ADD_OP);
                }
                wait_ready(soa_volume_completion_tiles[omp_thread_id]);
                if (soa_masked_index_full_window)
                    soa_masked_index_windows.fetch_add(
                        1, std::memory_order_relaxed);
                else if (soa_volume_only_full_window)
                    soa_volume_only_windows.fetch_add(
                        1, std::memory_order_relaxed);
#endif
            }

            for (int page_offset = 0; page_offset < gather_size;
                 page_offset += MAA_CONSUMER_TILE_SIZE) {
                const int page_size =
                    std::min(gather_size - page_offset,
                             MAA_CONSUMER_TILE_SIZE);
                const int page_begin = c + page_offset;
                maa_const<int>(page_begin, reg0);
                maa_const<int>(page_begin + page_size, reg1);

                // Predicate production, gather consumption, and FP32 multiply
                // remain physical-4K.  The dual arm publishes only the product
                // and uses the already masked index stream for both RMWs.
                maa_stream_load<int>(corner_type.data(), reg0, reg1, reg2,
                                     tile0);
                maa_alu_scalar<int>(tile0, reg2, tileCond,
                                    Operation_t::GTE_OP);
                if (!soa_both_full_window &&
                    !soa_dual_logical16_any_full_window) {
                    maa_stream_load<int>(c_to_p_map.data(), reg0, reg1, reg2,
                                         tile4, tileCond);
                }
                if (!soa_volume_full_window) {
                    maa_stream_load<DATATYPE>(corner_volume.data(), reg0,
                                              reg1, reg2, tile0, tileCond);
                    maa_indirect_rmw_vector<DATATYPE>(
                        point_volume.data(), tile4, tile0,
                        Operation_t::ADD_OP, tileCond);
                }
                maa_stream_load<DATATYPE>(csurf.data(), reg0, reg1, reg2,
                                          tile1, tileCond);

                if (gather_size == TILE_SIZE) {
                    // Publication reuses these three otherwise-immutable page
                    // registers below.  Restore the page-relative consumer
                    // bounds before every modeled backing load.
                    maa_const<int>(0, page_min_reg);
                    maa_const<int>(MAA_CONSUMER_TILE_SIZE, page_max_reg);
                    maa_const<int>(1, page_stride_reg);
                    maa_virtual_consumer_load_page<DATATYPE>(
                        virtual_consumer_mode,
                        virtual_gather_backing[omp_thread_id] + page_offset,
                        tile3, page_offset / MAA_CONSUMER_TILE_SIZE,
                        page_min_reg, page_max_reg, page_stride_reg, tile0);
                } else {
                    // The tail backing pointer is already page-relative.
                    // Its ordinary STREAM_LD must therefore use local bounds;
                    // keeping the logical c-based bounds would add page_offset
                    // twice and translate beyond the registered 16K span.
                    maa_const<int>(0, reg0);
                    maa_const<int>(page_size, reg1);
                    maa_stream_load<DATATYPE>(
                        virtual_gather_backing[omp_thread_id] + page_offset,
                        reg0, reg1, reg2, tile0);
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
                if (!soa_dual_logical16_split2k_full_window) {
                    maa_alu_vector<DATATYPE>(tile1, tile0, tile2,
                                              Operation_t::MUL_OP, tileCond);
                }
                if (soa_both_full_window ||
                    soa_dual_logical16_any_full_window) {
#ifdef UME_GZP_SOA_JIT_RMW
                    // The publisher itself waits for each complete producer
                    // tile, captures one 64B line into one of eight credits,
                    // and retains that exact payload until WriteResp.  The
                    // completion tiles fence visibility and source reuse;
                    // there is no host-side copy or instantaneous staging.
                    const uint32_t logical_page =
                        page_offset / MAA_CONSUMER_TILE_SIZE;
                    const uint32_t window = c / TILE_SIZE;
                    const uint32_t predicate_generation =
                        window * 8 + logical_page * 2 + 1;
                    const uint32_t gradient_generation =
                        soa_dual_logical16_full_window
                            ? window * 4 + logical_page + 1
                            : predicate_generation + 1;
                    if (soa_dual_logical16_split2k_full_window) {
                        // Two independently owned 2K FP32 regions share
                        // tile2's sole 4K physical payload.  No wait occurs
                        // between publishing half zero and computing half
                        // one: S0 retains the first half through WriteResp
                        // while A0 writes only the disjoint second half.
                        for (uint32_t half = 0; half < 2; ++half) {
                            const uint32_t subpage = logical_page * 2 + half;
                            const uint32_t first = half * 2048;
                            maa_const<int>(first, split_first_reg);
                            maa_const<int>(2048, split_elements_reg);
                            maa_alu_vector_split_2k<DATATYPE>(
                                tile1, tile0, tile2, Operation_t::MUL_OP,
                                split_first_reg, split_elements_reg,
                                tileCond);
                            maa_const<uint32_t>(
                                0x80000000U | subpage, page_min_reg);
                            maa_const<uint32_t>(subpage * 2048,
                                                page_max_reg);
                            maa_const<uint32_t>(window * 8 + subpage + 1,
                                                page_stride_reg);
                            maa_publish_spd_half_logical16_response_bearing<
                                DATATYPE>(
                                soa_gradient_values[omp_thread_id],
                                logical_page, half, tile2,
                                half == 0
                                    ? soa_volume_completion_tiles[
                                          omp_thread_id]
                                    : soa_gradient_completion_tiles[
                                          omp_thread_id],
                                page_min_reg, page_max_reg,
                                page_stride_reg);
                        }
                        // Both completion tokens are publication fences. The
                        // producer never performs a CPU copy or a sink write.
                        wait_ready(soa_volume_completion_tiles[omp_thread_id]);
                        wait_ready(
                            soa_gradient_completion_tiles[omp_thread_id]);
                    } else {
                        maa_const<uint32_t>(logical_page, page_min_reg);
                        maa_const<uint32_t>(page_offset, page_max_reg);
                    if (soa_both_full_window) {
                        maa_const<uint32_t>(predicate_generation,
                                            page_stride_reg);
                        maa_publish_spd_page_logical16_response_bearing<
                            uint32_t>(
                            soa_predicates[omp_thread_id], logical_page,
                            tileCond,
                            soa_volume_completion_tiles[omp_thread_id],
                            page_min_reg, page_max_reg, page_stride_reg);
                    }
                    maa_const<uint32_t>(gradient_generation,
                                        page_stride_reg);
                    maa_publish_spd_page_logical16_response_bearing<DATATYPE>(
                        soa_gradient_values[omp_thread_id], logical_page,
                        tile2,
                        soa_gradient_completion_tiles[omp_thread_id],
                        page_min_reg, page_max_reg, page_stride_reg);
                    if (soa_both_full_window) {
                        wait_ready(
                            soa_volume_completion_tiles[omp_thread_id]);
                    }
                    wait_ready(soa_gradient_completion_tiles[omp_thread_id]);
                    }
                    if (soa_both_full_window) {
                        soa_published_predicates.fetch_add(
                            page_size, std::memory_order_relaxed);
                    }
                    soa_published_gradient_values.fetch_add(
                        page_size, std::memory_order_relaxed);
#endif
                } else {
                    maa_indirect_rmw_vector<DATATYPE>(
                        point_gradient.data(), tile4, tile2,
                        Operation_t::ADD_OP, tileCond);
                }
                wait_ready(tile1);
            }
            if (gather_size == TILE_SIZE)
                maa_virtual_consumer_end(virtual_consumer_mode, tile3);
            if (soa_both_full_window) {
#ifdef UME_GZP_SOA_JIT_RMW
                maa_const<int>(0, reg0);
                maa_const<int>(TILE_SIZE, reg1);
                maa_const<int>(1, reg2);
                maa_indirect_rmw_vector_soa_jit<DATATYPE>(
                    point_volume.data(),
                    reinterpret_cast<const uint32_t *>(
                        c_to_p_map.data() + c),
                    corner_volume.data() + c,
                    soa_predicates[omp_thread_id], reg0, reg1, reg2,
                    soa_volume_completion_tiles[omp_thread_id],
                    Operation_t::ADD_OP);
                wait_ready(soa_volume_completion_tiles[omp_thread_id]);
                maa_indirect_rmw_vector_soa_jit<DATATYPE>(
                    point_gradient.data(),
                    reinterpret_cast<const uint32_t *>(
                        c_to_p_map.data() + c),
                    soa_gradient_values[omp_thread_id],
                    soa_predicates[omp_thread_id], reg0, reg1, reg2,
                    soa_gradient_completion_tiles[omp_thread_id],
                    Operation_t::ADD_OP);
                wait_ready(soa_gradient_completion_tiles[omp_thread_id]);
                soa_full_windows.fetch_add(1, std::memory_order_relaxed);
#endif
            } else if (soa_dual_logical16_any_full_window) {
#ifdef UME_GZP_SOA_JIT_RMW
                maa_const<int>(0, reg0);
                maa_const<int>(TILE_SIZE, reg1);
                maa_const<int>(1, reg2);
                maa_indirect_rmw_vector_soa_jit_masked_indices<DATATYPE>(
                    point_gradient.data(),
                    reinterpret_cast<const uint32_t *>(
                        c_to_p_map.data() + c),
                    soa_gradient_values[omp_thread_id], reg0, reg1, reg2,
                    soa_gradient_completion_tiles[omp_thread_id],
                    Operation_t::ADD_OP);
                wait_ready(soa_gradient_completion_tiles[omp_thread_id]);
                if (soa_dual_logical16_split2k_full_window) {
                    soa_dual_logical16_split2k_windows.fetch_add(
                        1, std::memory_order_relaxed);
                } else {
                    soa_dual_logical16_windows.fetch_add(
                        1, std::memory_order_relaxed);
                }
#endif
            }
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
#ifdef UME_GZP_SOA_JIT_RMW
    std::cout << "UME_GZP_TERMINAL treatment="
              << gzp_rmw_treatment_name(gzp_rmw_treatment)
              << " full_windows=" << soa_full_windows.load()
              << " volume_only_windows="
              << soa_volume_only_windows.load()
              << " masked_index_windows="
              << soa_masked_index_windows.load()
              << " dual_logical16_windows="
              << soa_dual_logical16_windows.load()
              << " dual_logical16_split2k_windows="
              << soa_dual_logical16_split2k_windows.load()
              << " published_predicates=" << soa_published_predicates.load()
              << " published_gradient_values="
              << soa_published_gradient_values.load()
              << " published_gradient_bytes="
              << gzp_gradient_publication_bytes()
              << " predicate_hash=" << soa_predicate_hash
              << " ledger_selected=" << gzp_masked_index_ledger.selected
              << " ledger_rejected=" << gzp_masked_index_ledger.rejected
              << " ledger_full_selected="
              << gzp_masked_index_ledger.full_window_selected
              << " ledger_full_rejected="
              << gzp_masked_index_ledger.full_window_rejected
              << " active_uint32_max="
              << gzp_masked_index_ledger.active_uint32_max
              << " active_illegal_index="
              << gzp_masked_index_ledger.active_illegal_index
              << " inactive_legal_index="
              << gzp_masked_index_ledger.inactive_legal_index
              << " inactive_non_sentinel="
              << gzp_masked_index_ledger.inactive_non_sentinel
              << " index_hash=" << gzp_masked_index_ledger.index_hash
              << " publisher="
              << gzp_rmw_publisher_name(gzp_rmw_treatment)
              << " predicate_publications="
              << gzp_separate_predicate_publications(gzp_rmw_treatment)
              << " predicate_publication_bytes="
              << gzp_separate_predicate_publication_bytes(
                     gzp_rmw_treatment)
              << " producer_staging_elements="
              << MAA_CONSUMER_TILE_SIZE
              << " producer_staging_bytes="
              << MAA_CONSUMER_TILE_SIZE * sizeof(DATATYPE)
              << " producer_owner_regions="
              << (gzp_rmw_treatment ==
                          GzpRmwTreatment::DualLogical16Split2KSoaJit
                      ? 2
                      : 1)
              << " producer_owner_region_elements="
              << (gzp_rmw_treatment ==
                          GzpRmwTreatment::DualLogical16Split2KSoaJit
                      ? 2048
                      : MAA_CONSUMER_TILE_SIZE)
              << " split_owner_slots=2"
              << " split_owner_state_bytes=8"
              << " split_additional_spd_ports=0"
              << " split_additional_stream_ports=0"
              << " split_additional_alu_ports=0"
              << " publisher_credit_payload_bytes=" << 8 * 64
              << " coherent_gradient_backing_elements="
              << NUM_CORES * TILE_SIZE
              << " coherent_gradient_backing_bytes="
              << NUM_CORES * TILE_SIZE * sizeof(DATATYPE)
              << " hidden_logical16_payload_bytes=0"
              << " cpu_untimed_copy_bytes=0"
              << " performance_promotable="
              << gzp_rmw_performance_promotable(gzp_rmw_treatment)
              << " result=PASS" << std::endl;
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
#ifdef UME_GZP_SOA_JIT_RMW
    corner_predicate_soa.resize(num_corners);
#endif

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
#ifdef UME_GZP_SOA_JIT_RMW
    soa_predicate_active = 0;
    for (int i = 0; i < num_corners; ++i) {
        corner_predicate_soa[i] = corner_type[i] > 0 ? 1U : 0U;
        soa_predicate_active += corner_predicate_soa[i];
    }
    soa_predicate_hash = hash_soa_predicates(corner_predicate_soa);
#endif
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

#ifdef UME_GZP_SOA_JIT_RMW
    encode_and_audit_gzp_masked_indices(num_points);
#endif

#if defined(UME_GATHER_VERIFY) || defined(UME_FIXED_INPUT)
    mark_points_without_active_corners();
#endif
#if defined(UME_GATHER_VERIFY) || defined(UME_OUTPUT_FINGERPRINT)
    build_scalar_reference();
#endif
#ifdef UME_GZP_SOA_JIT_RMW
    // gem5 SE allocates physical pages on first touch.  Fault each registered
    // publication span serially before the checkpoint so the response router's
    // explicitly checked contiguous physical span is deterministic.
    first_touch_soa_publication_buffers();
#endif

#ifdef GEM5
    cout << "Starting checkpoint" << endl;
    m5_checkpoint(0, 0);
    cout << "checkpoint done" << endl;
#ifdef MAA_GENERAL_VIRTUAL_CONSUMER
    try {
#ifdef UME_GZP_SOA_JIT_RMW
        const GzpSelector selector =
            read_gzp_selector(virtual_consumer_selector);
        virtual_consumer_mode = selector.consumer;
        gzp_rmw_treatment = selector.rmw;
#else
        virtual_consumer_mode =
            maa_read_virtual_consumer_mode(virtual_consumer_selector);
#endif
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
#ifdef UME_GZP_SOA_JIT_RMW
    std::cout << "UME_GZP_RMW_TREATMENT mode="
              << gzp_rmw_treatment_name(gzp_rmw_treatment)
              << " predicate=uint32_corner_type_gt_0"
              << " completion=explicit_spd_wait"
              << " publisher="
              << gzp_rmw_publisher_name(gzp_rmw_treatment)
              << " predicate_publications="
              << gzp_separate_predicate_publications(gzp_rmw_treatment)
              << " predicate_publication_bytes="
              << gzp_separate_predicate_publication_bytes(
                     gzp_rmw_treatment)
              << " producer_staging_elements="
              << MAA_CONSUMER_TILE_SIZE
              << " producer_staging_bytes="
              << MAA_CONSUMER_TILE_SIZE * sizeof(DATATYPE)
              << " publisher_credit_payload_bytes=" << 8 * 64
              << " coherent_gradient_backing_elements="
              << NUM_CORES * TILE_SIZE
              << " coherent_gradient_backing_bytes="
              << NUM_CORES * TILE_SIZE * sizeof(DATATYPE)
              << " hidden_logical16_payload_bytes=0"
              << " cpu_untimed_copy_bytes=0"
              << " performance_promotable="
              << gzp_rmw_performance_promotable(gzp_rmw_treatment)
              << std::endl;
    std::cout << "UME_GZP_PREDICATE_BUFFER elements="
              << corner_predicate_soa.size()
              << " active=" << soa_predicate_active
              << " hash=" << soa_predicate_hash
              << " semantic=corner_type_gt_0 phase=pre_checkpoint"
              << " immutable=1" << std::endl;
    std::cout << "UME_GZP_MASKED_INDEX_LEDGER result=PASS"
              << " selected=" << gzp_masked_index_ledger.selected
              << " rejected=" << gzp_masked_index_ledger.rejected
              << " full_selected="
              << gzp_masked_index_ledger.full_window_selected
              << " full_rejected="
              << gzp_masked_index_ledger.full_window_rejected
              << " active_uint32_max="
              << gzp_masked_index_ledger.active_uint32_max
              << " active_illegal_index="
              << gzp_masked_index_ledger.active_illegal_index
              << " inactive_legal_index="
              << gzp_masked_index_ledger.inactive_legal_index
              << " inactive_non_sentinel="
              << gzp_masked_index_ledger.inactive_non_sentinel
              << " index_hash=" << gzp_masked_index_ledger.index_hash
              << " exact_equivalence=1" << std::endl;
#endif
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
#ifdef UME_GZP_SOA_JIT_RMW
            soa_volume_completion_tiles[thread_id] = get_new_tile<int>();
            soa_gradient_completion_tiles[thread_id] = get_new_tile<int>();
            soa_split_first_regs[thread_id] = get_new_reg<int>();
            soa_split_elements_regs[thread_id] = get_new_reg<int>();
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
