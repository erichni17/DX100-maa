/*
MIT License

Copyright (c) 2021 Parallel Applications Modelling Group - GMAP
	GMAP website: https://gmap.pucrs.br

	Pontifical Catholic University of Rio Grande do Sul (PUCRS)
	Av. Ipiranga, 6681, Porto Alegre - Brazil, 90619-900

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

------------------------------------------------------------------------------

The original NPB 3.4.1 version was written in Fortran and belongs to:
	http://www.nas.nasa.gov/Software/NPB/

Authors of the Fortran code:
	M. Yarrow
	C. Kuszmaul
	H. Jin

------------------------------------------------------------------------------

The serial C++ version is a translation of the original NPB 3.4.1
Serial C++ version: https://github.com/GMAP/NPB-CPP/tree/master/NPB-SER

Authors of the C++ code:
	Dalvan Griebler <dalvangriebler@gmail.com>
	Gabriell Araujo <hexenoften@gmail.com>
 	Júnior Löff <loffjh@gmail.com>

------------------------------------------------------------------------------

The OpenMP version is a parallel implementation of the serial C++ version
OpenMP version: https://github.com/GMAP/NPB-CPP/tree/master/NPB-OMP

Authors of the OpenMP code:
	Júnior Löff <loffjh@gmail.com>

*/

#include "MAA.hpp"
#include <omp.h>

#include <atomic>
#include <cinttypes>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <string>

#ifdef _OPENMP
#include "omp.h"

#endif

#if !defined(FUNC) && !defined(GEM5) && !defined(GEM5_MAGIC)
#define GEM5
#endif

#if defined(FUNC)
#include "MAA_functional.hpp"
#elif defined(GEM5)
#include "MAA_gem5.hpp"
#include <gem5/m5ops.h>

#elif defined(GEM5_MAGIC)
#include "MAA_gem5_magic.hpp"
#endif

#ifdef MAA_GENERAL_VIRTUAL_CONSUMER
#include "MAA_virtual_materialize.hpp"
static MAAVirtualConsumerMode virtual_consumer_mode =
    MAAVirtualConsumerMode::StreamControl;
#endif

// #define DO_VERIFY
#define MAA_VER 2
// Reduce cgitmax to 4 so we can run the benchmark in a reasonable time
#define CGITMAX 4
// Run for one iteration so GEM5 does not die!
#define NITER 1

#ifndef MAA_CONSUMER_TILE_SIZE
#define MAA_CONSUMER_TILE_SIZE TILE_SIZE
#endif

static_assert(MAA_CONSUMER_TILE_SIZE > 0,
              "consumer tile size must be positive");
static_assert(MAA_CONSUMER_TILE_SIZE <= TILE_SIZE,
              "consumer tile cannot exceed the logical gather tile");
static_assert(TILE_SIZE % MAA_CONSUMER_TILE_SIZE == 0,
              "logical gather tile must contain whole consumer pages");

#ifdef CG_LOGICAL16_RMW
#if !defined(GEM5) || !defined(MAA) || !defined(MAA_GENERAL_VIRTUAL_CONSUMER)
#error "CG logical-16 RMW requires the gem5 general-hybrid MAA path"
#endif
#if TILE_SIZE != 16384 || MAA_CONSUMER_TILE_SIZE != 4096
#error "CG logical-16 RMW requires a 16K logical / 4K physical geometry"
#endif
static_assert(sizeof(int) == sizeof(uint32_t),
              "CG logical-16 RMW indices require 32-bit int");

enum class CgRmwTreatment
{
    Legacy4K,
    ResidualSoaJit,
    LogicalPageSoaJit,
};

struct CgTreatmentSelector
{
    MAAVirtualConsumerMode consumer;
    CgRmwTreatment rmw;
};

static CgRmwTreatment cg_rmw_treatment = CgRmwTreatment::Legacy4K;

// Ordinary coherent guest-memory producer buffers, not hidden MAA state.
constexpr size_t cg_logical_backing_bytes = TILE_SIZE * sizeof(float);
alignas(cg_logical_backing_bytes) static uint32_t
    cg_soa_indices[NUM_CORES][TILE_SIZE];
alignas(cg_logical_backing_bytes) static float
    cg_soa_values[NUM_CORES][TILE_SIZE];
#ifdef CG_LOGICAL_PAGE_RMW
alignas(cg_logical_backing_bytes) static float
    cg_soa_products[NUM_CORES][TILE_SIZE];
#endif
static uint64_t cg_soa_full_windows[NUM_CORES] = {};
static uint64_t cg_soa_index_words[NUM_CORES] = {};
static uint64_t cg_soa_value_words[NUM_CORES] = {};
static uint64_t cg_legacy_residual_words[NUM_CORES] = {};
#ifdef CG_LOGICAL_PAGE_RMW
static uint64_t cg_logical_page_windows[NUM_CORES] = {};
static uint64_t cg_logical_alu_vectors[NUM_CORES] = {};
static uint64_t cg_logical_product_words[NUM_CORES] = {};
static uint64_t cg_index_publish_pages[NUM_CORES] = {};
static uint64_t cg_value_publish_pages[NUM_CORES] = {};
static uint32_t cg_publish_generations[NUM_CORES] = {};
#endif
constexpr size_t cg_external_staging_bytes =
    sizeof(cg_soa_indices) + sizeof(cg_soa_values)
#ifdef CG_LOGICAL_PAGE_RMW
    + sizeof(cg_soa_products)
#endif
    ;

static const char *
cg_rmw_treatment_name(CgRmwTreatment treatment)
{
    switch (treatment) {
      case CgRmwTreatment::Legacy4K:
        return "legacy_4k";
      case CgRmwTreatment::ResidualSoaJit:
        return "residual_soa_jit";
      case CgRmwTreatment::LogicalPageSoaJit:
        return "logical_page_soa_jit";
    }
    std::abort();
}

static CgTreatmentSelector
read_cg_treatment_selector(const std::string &path)
{
    std::ifstream input(path);
    std::string consumer;
    std::string treatment;
    std::string extra;
    if (!(input >> consumer >> treatment) || input >> extra)
        throw std::runtime_error(
            "CG selector must contain exactly CONSUMER TREATMENT");

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
        throw std::runtime_error("invalid CG virtual consumer mode");

    CgRmwTreatment rmw;
    if (treatment == "legacy_4k")
        rmw = CgRmwTreatment::Legacy4K;
    else if (treatment == "residual_soa_jit")
        rmw = CgRmwTreatment::ResidualSoaJit;
    else if (treatment == "logical_page_soa_jit")
        rmw = CgRmwTreatment::LogicalPageSoaJit;
    else
        throw std::runtime_error(
            "CG RMW treatment must be legacy_4k, residual_soa_jit, or "
            "logical_page_soa_jit");
#ifndef CG_LOGICAL_PAGE_RMW
    if (rmw == CgRmwTreatment::LogicalPageSoaJit)
        throw std::runtime_error(
            "logical_page_soa_jit requires the opt-in CG logical-page build");
#endif
    return {consumer_mode, rmw};
}

#ifdef CG_LOGICAL_PAGE_RMW
static_assert(NUM_TILES_PER_CORE >= 10,
              "CG logical-page RMW requires 32 guest lanes plus 8 reserved "
              "logical-scheduler lanes");
static float *virtual_gather_backing_for_thread(int tid);

static void
cg_publish_index_value_page(int tid, int page_offset, int index_tile,
                            int value_tile, int index_completion_tile,
                            int value_completion_tile, int logical_page_reg,
                            int logical_offset_reg, int generation_reg)
{
    const uint32_t logical_page =
        static_cast<uint32_t>(page_offset / MAA_CONSUMER_TILE_SIZE);
    if (logical_page >= 4 ||
        page_offset != static_cast<int>(logical_page) *
                           MAA_CONSUMER_TILE_SIZE)
        std::abort();

    maa_const<uint32_t>(logical_page, logical_page_reg);
    maa_const<uint32_t>(static_cast<uint32_t>(page_offset),
                        logical_offset_reg);
    const uint32_t index_generation = ++cg_publish_generations[tid];
    if (index_generation == 0)
        std::abort();
    maa_const<uint32_t>(index_generation, generation_reg);
    maa_publish_spd_page_logical16_response_bearing<uint32_t>(
        cg_soa_indices[tid], logical_page, index_tile,
        index_completion_tile, logical_page_reg, logical_offset_reg,
        generation_reg);
    wait_ready(index_completion_tile);
    cg_index_publish_pages[tid]++;

    const uint32_t value_generation = ++cg_publish_generations[tid];
    if (value_generation == 0)
        std::abort();
    maa_const<uint32_t>(value_generation, generation_reg);
    maa_publish_spd_page_logical16_response_bearing<float>(
        cg_soa_values[tid], logical_page, value_tile,
        value_completion_tile, logical_page_reg, logical_offset_reg,
        generation_reg);
    wait_ready(value_completion_tile);
    cg_value_publish_pages[tid]++;
    cg_soa_index_words[tid] += MAA_CONSUMER_TILE_SIZE;
    cg_soa_value_words[tid] += MAA_CONSUMER_TILE_SIZE;
}

static void
cg_logical_multiply_rmw(int tid, float *destination, int min_reg,
                        int max_reg, int stride_reg, int completion_tile)
{
    // The virtual gather has already closed every coherent backing WriteResp,
    // and both page-local operands above were response-bearing publications.
    // This ordinary logical ALU_VECTOR serializes four native 4K page actions
    // and all product WriteResps before the no-result Row/Offset RMW consumes
    // the immutable coherent index/product spans in original offset order.
    maa_alu_vector_logical<float>(
        0, 1, 2, virtual_gather_backing_for_thread(tid), cg_soa_values[tid],
        cg_soa_products[tid], Operation_t::MUL_OP);
    cg_logical_alu_vectors[tid]++;
    cg_logical_product_words[tid] += TILE_SIZE;

    maa_const<int>(0, min_reg);
    maa_const<int>(TILE_SIZE, max_reg);
    maa_const<int>(1, stride_reg);
    maa_indirect_rmw_vector_soa_jit<float>(
        destination, cg_soa_indices[tid], cg_soa_products[tid], nullptr,
        min_reg, max_reg, stride_reg, completion_tile, Operation_t::ADD_OP);
    wait_ready(completion_tile);
    cg_soa_full_windows[tid]++;
    cg_logical_page_windows[tid]++;
}
#endif
#endif

#ifdef CG_FP_ENABLE
static uint64_t mix_fingerprint(uint64_t value) {
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31);
}

static uint64_t update_fingerprint(uint64_t hash, uint64_t index,
                                   uint64_t value) {
    hash ^= mix_fingerprint((index << 32) ^ value);
    hash = (hash << 19) | (hash >> 45);
    return hash * 0x9e3779b185ebca87ULL;
}

static uint64_t float_bits(float value) {
    uint32_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}

static int64_t quantize_float(float value, double scale) {
    return static_cast<int64_t>(
        std::llround(static_cast<double>(value) * scale));
}

static bool quantizable_float(float value, double scale) {
    return std::isfinite(value) &&
           std::fabs(static_cast<double>(value)) <=
               static_cast<double>(INT64_MAX) / scale;
}

static bool print_cg_fingerprint(const std::string &mode, const float x[],
                                 const float z[], int elements, double rnorm,
                                 double zeta) {
    uint64_t x_raw = 0xcbf29ce484222325ULL;
    uint64_t z_raw = 0x6a09e667f3bcc909ULL;
    uint64_t x_q5 = 0x3c6ef372fe94f82bULL;
    uint64_t x_q6 = 0xa54ff53a5f1d36f1ULL;
    uint64_t z_q5 = 0x510e527fade682d1ULL;
    uint64_t z_q6 = 0x9b05688c2b3e6c1fULL;
    uint64_t nonfinite_x = 0;
    uint64_t nonfinite_z = 0;
    double x_sum = 0.0;
    double x_norm_sq = 0.0;
    double z_sum = 0.0;
    double z_norm_sq = 0.0;

    for (int i = 0; i < elements; i++) {
        if (!std::isfinite(x[i]))
            nonfinite_x++;
        if (!std::isfinite(z[i]))
            nonfinite_z++;
        x_sum += x[i];
        x_norm_sq += static_cast<double>(x[i]) * x[i];
        z_sum += z[i];
        z_norm_sq += static_cast<double>(z[i]) * z[i];
        x_raw = update_fingerprint(x_raw, i, float_bits(x[i]));
        z_raw = update_fingerprint(z_raw, i, float_bits(z[i]));
        if (quantizable_float(x[i], 1.0e6)) {
            x_q5 = update_fingerprint(x_q5, i, quantize_float(x[i], 1.0e5));
            x_q6 = update_fingerprint(x_q6, i, quantize_float(x[i], 1.0e6));
        }
        if (quantizable_float(z[i], 1.0e6)) {
            z_q5 = update_fingerprint(z_q5, i, quantize_float(z[i], 1.0e5));
            z_q6 = update_fingerprint(z_q6, i, quantize_float(z[i], 1.0e6));
        }
    }
    const bool pass = nonfinite_x == 0 && nonfinite_z == 0 &&
                      std::isfinite(rnorm) && rnorm >= 0.0 &&
                      std::isfinite(zeta) &&
                      std::fabs(x_norm_sq - 1.0) <= 1.0e-4;
    printf("CG_FINGERPRINT mode=%s elements=%d x_raw=%016" PRIx64
           " z_raw=%016" PRIx64 " x_q5=%016" PRIx64
           " x_q6=%016" PRIx64 " z_q5=%016" PRIx64
           " z_q6=%016" PRIx64 " x_sum=%.17g x_norm_sq=%.17g"
           " z_sum=%.17g z_norm_sq=%.17g rnorm=%.17g zeta=%.17g"
           " nonfinite_x=%" PRIu64 " nonfinite_z=%" PRIu64
           " result=%s\n",
           mode.c_str(), elements, x_raw, z_raw, x_q5, x_q6, z_q5, z_q6,
           x_sum, x_norm_sq, z_sum, z_norm_sq, rnorm, zeta, nonfinite_x,
           nonfinite_z, pass ? "PASS" : "FAIL");
    return pass;
}
#endif

#ifdef MAA_VIRTUAL_GATHER
#ifdef MAA_BOUNDED_VIRTUAL_GATHER
constexpr size_t virtual_descriptor_spool_units = 4;
constexpr size_t virtual_descriptor_spool_slot_bytes =
    TILE_SIZE * 8 + 4 * 64;
constexpr size_t virtual_descriptor_spool_words =
    virtual_descriptor_spool_units * virtual_descriptor_spool_slot_bytes /
    sizeof(float);
alignas(TILE_SIZE * sizeof(float)) static float virtual_gather_storage[
    NUM_CORES * TILE_SIZE + virtual_descriptor_spool_words];

static float *
virtual_gather_backing_for_thread(int tid)
{
    return virtual_gather_storage + tid * TILE_SIZE;
}
#else
alignas(TILE_SIZE * sizeof(float)) static float
    virtual_gather_backing[NUM_CORES][TILE_SIZE];

static float *
virtual_gather_backing_for_thread(int tid)
{
    return virtual_gather_backing[tid];
}
#endif
#endif

/*
 * ---------------------------------------------------------------------
 * note: please observe that in the routine conj_grad_base three
 * implementations of the sparse matrix-vector multiply have
 * been supplied. the default matrix-vector multiply is not
 * loop unrolled. the alternate implementations are unrolled
 * to a depth of 2 and unrolled to a depth of 8. please
 * experiment with these to find the fastest for your particular
 * architecture. if reporting timing results, any of these three may
 * be used without penalty.
 * ---------------------------------------------------------------------
 * class specific parameters:
 * it appears here for reference only.
 * these are their values, however, this info is imported in the npbparams.h
 * include file, which is written by the sys/setparams.c program.
 * ---------------------------------------------------------------------
 */

typedef int boolean;

#define TRUE  1
#define FALSE 0

/*************/
/*  CLASS C  */
/*************/
#ifdef CG_NA
#define NA     CG_NA
#else
#define NA     (37500 * NUM_CORES)
#endif
#define NONZER 15
#define SHIFT  110.0
#define RCOND  1.0e-1

#define NZ          (NA * (NONZER + 1) * (NONZER + 1))
#define NAZ         (NA * (NONZER + 1))
#define T_INIT      0
#define T_BENCH     1
#define T_CONJ_GRAD 2
#define T_LAST      3

/* global variables */
// Total: 26MB
#ifndef USE_DATA_FROM_FILE

// Total: 26MB
// 8MB
static int colidx[NZ];
// 56KB
static int rowstr[NA + 1];
// 56KB
static int iv[NA];
// 56KB
static int arow[NA];
// 672KB
static int acol[NAZ];
// 1.3MB
static float aelt[NAZ];
// 16MB
static float a[NZ];
#else
#if NUM_CORES == 4
#include "cg_data_4C.h"

#elif NUM_CORES == 8
#include "cg_data_8C.h"

#elif NUM_CORES == 16
#include "cg_data_16C.h"

#else
#error
#endif
#endif
// 112KB
static float x[NA + 2];
// 112KB
static float z[NA + 2];
// 112KB
static float p[NA + 2];
// 112KB
static float q[NA + 2];
// 112KB
static float r[NA + 2];
static int naa;
static int nzz;
static int firstrow;
static int lastrow;
static int firstcol;
static int lastcol;
static double amult;
static double tran;

/* function prototypes */
static void conj_grad_base(int colidx[], int rowstr[], float x[], float z[], float a[], float p[], float q[], float r[], double *rnorm);
static void conj_grad_maa(int colidx[], int rowstr[], float x[], float z[], float a[], float p[], float q[], float r[], double *rnorm);
static int icnvrt(double x, int ipwr2);
static void makea(int n, int nz, float a[], int colidx[], int rowstr[], int firstrow, int lastrow, int firstcol, int lastcol, int arow[], int acol[][NONZER + 1], float aelt[][NONZER + 1], int iv[]);
static void sparse(float a[], int colidx[], int rowstr[], int n, int nz, int nozer, int arow[], int acol[][NONZER + 1], float aelt[][NONZER + 1], int firstrow, int lastrow, int nzloc[], double rcond, double shift);
static void sprnvc(int n, int nz, int nn1, double v[], int iv[]);
static void vecset(int n, double v[], int iv[], int *nzv, int i, double val);

#if defined(USE_POW)
#define r23 pow(0.5, 23.0)
#define r46 (r23 * r23)
#define t23 pow(2.0, 23.0)
#define t46 (t23 * t23)
#else
#define r23 (0.5 * 0.5 * 0.5 * 0.5 * 0.5 * 0.5 * 0.5 * 0.5 * 0.5 * 0.5 * 0.5 * 0.5 * 0.5 * 0.5 * 0.5 * 0.5 * 0.5 * 0.5 * 0.5 * 0.5 * 0.5 * 0.5 * 0.5)
#define r46 (r23 * r23)
#define t23 (2.0 * 2.0 * 2.0 * 2.0 * 2.0 * 2.0 * 2.0 * 2.0 * 2.0 * 2.0 * 2.0 * 2.0 * 2.0 * 2.0 * 2.0 * 2.0 * 2.0 * 2.0 * 2.0 * 2.0 * 2.0 * 2.0 * 2.0)
#define t46 (t23 * t23)
#endif

double randlc(double *x, double a) {
    double t1, t2, t3, t4, a1, a2, x1, x2, z;

    /*
	 * ---------------------------------------------------------------------
	 * break A into two parts such that A = 2^23 * A1 + A2.
	 * ---------------------------------------------------------------------
	 */
    t1 = r23 * a;
    a1 = (int)t1;
    a2 = a - t23 * a1;

    /*
	 * ---------------------------------------------------------------------
	 * break X into two parts such that X = 2^23 * X1 + X2, compute
	 * Z = A1 * X2 + A2 * X1  (mod 2^23), and then
	 * X = 2^23 * Z + A2 * X2  (mod 2^46).
	 * ---------------------------------------------------------------------
	 */
    t1 = r23 * (*x);
    x1 = (int)t1;
    x2 = (*x) - t23 * x1;
    t1 = a1 * x2 + a2 * x1;
    t2 = (int)(r23 * t1);
    z = t1 - t23 * t2;
    t3 = t23 * z + a2 * x2;
    t4 = (int)(r46 * t3);
    (*x) = t3 - t46 * t4;

    return (r46 * (*x));
}

void save_data_to_file() {
#if NUM_CORES == 4
    std::ofstream outfile("cg_data_4C.h");
    if (!outfile.is_open()) {
        std::cerr << "Error opening file for writing: cg_data_4C.h" << std::endl;
        exit(1);
    }
#elif NUM_CORES == 8
    std::ofstream outfile("cg_data_8C.h");
    if (!outfile.is_open()) {
        std::cerr << "Error opening file for writing: cg_data_8C.h" << std::endl;
        exit(1);
    }
#elif NUM_CORES == 16
    std::ofstream outfile("cg_data_16C.h");
    if (!outfile.is_open()) {
        std::cerr << "Error opening file for writing: cg_data_16C.h" << std::endl;
        exit(1);
    }
#else
#error
#endif

    // Preserve each binary32 value when generated data is compiled back in.
    outfile << std::setprecision(std::numeric_limits<float>::max_digits10);

    outfile << "#ifndef CG_DATA_H\n";
    outfile << "#define CG_DATA_H\n\n";

    // Write arrays
    outfile << "float a[NZ] = {";
    for (int i = 0; i < NZ; i++) {
        outfile << std::scientific << a[i];
        if (i != NZ - 1)
            outfile << ", ";
    }
    outfile << "};\n\n";

    outfile << "int colidx[NZ] = {";
    for (int i = 0; i < NZ; i++) {
        outfile << colidx[i];
        if (i != NZ - 1)
            outfile << ", ";
    }
    outfile << "};\n\n";

    outfile << "int rowstr[NA + 1] = {";
    for (int i = 0; i < NA + 1; i++) {
        outfile << rowstr[i];
        if (i != NA)
            outfile << ", ";
    }
    outfile << "};\n\n";

    outfile << "int arow[NA] = {";
    for (int i = 0; i < NA; i++) {
        outfile << arow[i];
        if (i != NA - 1)
            outfile << ", ";
    }
    outfile << "};\n\n";

    outfile << "int acol[NAZ] = {";
    for (int i = 0; i < NAZ; i++) {
        outfile << acol[i];
        if (i != NAZ - 1)
            outfile << ", ";
    }
    outfile << "};\n\n";

    outfile << "float aelt[NAZ] = {";
    for (int i = 0; i < NAZ; i++) {
        outfile << std::scientific << aelt[i];
        if (i != NAZ - 1)
            outfile << ", ";
    }
    outfile << "};\n\n";

    outfile << "int iv[NA] = {";
    for (int i = 0; i < NA; i++) {
        outfile << iv[i];
        if (i != NA - 1)
            outfile << ", ";
    }
    outfile << "};\n\n";

    outfile << "#endif // CG_DATA_H\n";
    outfile.close();
}

/* cg */
int main(int argc, char **argv) {

    if (argc < 2) {
        std::cout << "Usage: " << argv[0] << " [BASE|MAA]" << std::endl;
        return 1;
    }
    std::string mode = argv[1];
#ifdef MAA_GENERAL_VIRTUAL_CONSUMER
#ifdef CG_LOGICAL16_RMW
    if (mode != "MAA_DEFERRED" || argc != 3) {
        std::cerr << "CG logical-16 RMW requires MAA_DEFERRED and one "
                     "immutable selector path"
                  << std::endl;
        return 1;
    }
#endif
    const bool deferred_virtual_consumer = mode == "MAA_DEFERRED";
    const std::string virtual_consumer_selector =
        deferred_virtual_consumer && argc > 2 ? argv[2] : "";
    if (deferred_virtual_consumer && virtual_consumer_selector.empty()) {
        std::cerr << "MAA_DEFERRED requires a selector path" << std::endl;
        return 1;
    }
    if (deferred_virtual_consumer)
        mode = "MAA";
#endif
    std::cout << "Mode: " << mode << std::endl;

    int i, j, k, it;
    double zeta;
    double rnorm;
    double norm_temp1, norm_temp2;

    firstrow = 0;
    lastrow = NA - 1;
    firstcol = 0;
    lastcol = NA - 1;

#ifdef DO_VERIFY
    char class_npb;
    double zeta_verify_value;
    if (NA == 1400 && NONZER == 7 && NITER == 15 && SHIFT == 10.0) {
        class_npb = 'S';
        zeta_verify_value = 8.5971775078648;
    } else if (NA == 7000 && NONZER == 8 && NITER == 15 && SHIFT == 12.0) {
        class_npb = 'W';
        zeta_verify_value = 10.362595087124;
    } else if (NA == 14000 && NONZER == 11 && NITER == 15 && SHIFT == 20.0) {
        class_npb = 'A';
        zeta_verify_value = 17.130235054029;
    } else if (NA == 75000 && NONZER == 13 && NITER == 75 && SHIFT == 60.0) {
        class_npb = 'B';
        zeta_verify_value = 22.712745482631;
    } else if (NA == 150000 && NONZER == 15 && NITER == 75 && SHIFT == 110.0) {
        class_npb = 'C';
        zeta_verify_value = 28.973605592845;
    } else if (NA == 1500000 && NONZER == 21 && NITER == 100 && SHIFT == 500.0) {
        class_npb = 'D';
        zeta_verify_value = 52.514532105794;
    } else if (NA == 9000000 && NONZER == 26 && NITER == 100 && SHIFT == 1500.0) {
        class_npb = 'E';
        zeta_verify_value = 77.522164599383;
    } else {
        class_npb = 'U';
    }
#endif

    naa = NA;
    nzz = NZ;

    /* initialize random number generator */
    tran = 314159265.0;
    amult = 1220703125.0;
    zeta = randlc(&tran, amult);

#ifndef USE_DATA_FROM_FILE
    std::cout << "makea started!" << std::endl;
    makea(naa, nzz, a, colidx, rowstr, firstrow, lastrow, firstcol, lastcol, arow, (int(*)[NONZER + 1])(void *)acol, (float(*)[NONZER + 1])(void *)aelt, iv);
    std::cout << "makea finished!" << std::endl;
#else
    std::cout << "Using data from file!" << std::endl;
#endif
#ifdef DUMP_TO_FILE
    save_data_to_file();
    std::cout << "Dumped data to file!" << std::endl;
    exit(0);
#endif

#pragma omp parallel private(it, i, j, k)
    {
#pragma omp for schedule(dynamic, 16) nowait
        for (k = 0; k < NZ; k++) {
            colidx[k] = colidx[k] - firstcol;
        }

/* set starting vector to (1, 1, .... 1) */
#pragma omp for schedule(dynamic, 8) nowait
        for (i = 0; i < NA + 1; i++) {
            x[i] = 1.0;
        }

#pragma omp for schedule(dynamic, 8) nowait
        for (j = 0; j < lastcol - firstcol + 1; j++) {
            q[j] = 0.0;
            z[j] = 0.0;
            r[j] = 0.0;
            p[j] = 0.0;
        }
    }

#ifdef GEM5
#ifdef MAA_BOUNDED_VIRTUAL_GATHER
    std::cout << "CG_BOUNDED_VIRTUAL_LAYOUT logical=" << TILE_SIZE
              << " consumer=" << MAA_CONSUMER_TILE_SIZE
              << " maa_mem_size=" << MEM_SIZE
              << " payload_words=" << NUM_CORES * TILE_SIZE
              << " descriptor_units=" << virtual_descriptor_spool_units
              << " descriptor_slot_bytes="
              << virtual_descriptor_spool_slot_bytes
              << " registered_words="
              << NUM_CORES * TILE_SIZE + virtual_descriptor_spool_words
              << std::endl;
#endif
    m5_checkpoint(0, 0);
#ifdef MAA_GENERAL_VIRTUAL_CONSUMER
    if (deferred_virtual_consumer) {
        try {
#ifdef CG_LOGICAL16_RMW
            const CgTreatmentSelector selection =
                read_cg_treatment_selector(virtual_consumer_selector);
            virtual_consumer_mode = selection.consumer;
            cg_rmw_treatment = selection.rmw;
#else
            virtual_consumer_mode =
                maa_read_virtual_consumer_mode(virtual_consumer_selector);
#endif
            if (virtual_consumer_mode ==
                MAAVirtualConsumerMode::TokenStreamLoadPingPong)
                throw std::runtime_error(
                    "CG does not have two free alternating consumer tiles");
        } catch (const std::exception &error) {
            std::cerr << "CG virtual consumer selector: " << error.what()
                      << std::endl;
            return 1;
        }
    }
    std::cout << "CG_VIRTUAL_CONSUMER mode="
              << maa_virtual_consumer_mode_name(virtual_consumer_mode)
              << " logical=" << TILE_SIZE
              << " consumer=" << MAA_CONSUMER_TILE_SIZE << std::endl;
#ifdef CG_LOGICAL16_RMW
    std::cout << "CG_LOGICAL16_RMW_SELECTION treatment="
              << cg_rmw_treatment_name(cg_rmw_treatment)
              << " slice="
              << (cg_rmw_treatment == CgRmwTreatment::LogicalPageSoaJit
                      ? "all_spmv_full_windows"
                      : "residual_spmv")
              << " producer="
              << (cg_rmw_treatment == CgRmwTreatment::LogicalPageSoaJit
                      ? "response_bearing_spd_pages"
                      : "cpu_after_spd_completion")
              << " logical=" << TILE_SIZE
              << " physical=" << MAA_CONSUMER_TILE_SIZE
              << " external_staging_bytes="
              << cg_external_staging_bytes
              << " host_payload_access="
              << (cg_rmw_treatment == CgRmwTreatment::LogicalPageSoaJit
                      ? 0
                      : 1)
              << " performance_promotable=0" << std::endl;
#endif
#endif
#endif

    std::cout << "\n\n NAS Parallel Benchmarks 4.1 Parallel C++ version with OpenMP - CG Benchmark" << std::endl;
    std::cout << " Size: " << NA << std::endl;
    std::cout << " Iterations: " << NITER << std::endl;
    std::cout << " NUM_CORES: " << NUM_CORES << std::endl;

    alloc_MAA();
    init_MAA();

    /*
	 * ---------------------------------------------------------------------
	 * note: as a result of the above call to makea:
	 * values of j used in indexing rowstr go from 0 --> lastrow-firstrow
	 * values of colidx which are col indexes go from firstcol --> lastcol
	 * so:
	 * shift the col index vals from actual (firstcol --> lastcol)
	 * to local, i.e., (0 --> lastcol-firstcol)
	 * ---------------------------------------------------------------------
	 */
#pragma omp parallel private(it, i, j, k)
    {

#pragma omp single
        zeta = 0.0;

#ifdef GEM5
#pragma omp single
        {
            std::cout << "ROI started: " << omp_get_num_threads() << " threads" << std::endl;
            assert(omp_get_num_threads() == NUM_CORES);
            m5_work_begin(0, 0);
            m5_reset_stats(0, 0);
        }
#endif

        /*
		 * --------------------------------------------------------------------
		 * ---->
		 * main iteration for inverse power method
		 * ---->
		 * --------------------------------------------------------------------
		 */
        for (it = 1; it <= NITER; it++) {
            init_MAA();
            if (mode == "BASE")
                conj_grad_base(colidx, rowstr, x, z, a, p, q, r, &rnorm);
            else if (mode == "MAA")
                conj_grad_maa(colidx, rowstr, x, z, a, p, q, r, &rnorm);
            else {
                std::cerr << "Invalid mode: " << mode << ". Use 'BASE' or 'MAA'." << std::endl;
                exit(1);
#ifdef GEM5
                m5_exit(0);
#endif
            }

#pragma omp single
            {
                norm_temp1 = 0.0;
                norm_temp2 = 0.0;
            }

            /*
			 * --------------------------------------------------------------------
			 * zeta = shift + 1/(x.z)
			 * so, first: (x.z)
			 * also, find norm of z
			 * so, first: (z.z)
			 * --------------------------------------------------------------------
			 */
#pragma omp for reduction(+ : norm_temp1, norm_temp2)
            for (j = 0; j < lastcol - firstcol + 1; j++) {
                norm_temp1 += x[j] * z[j];
                norm_temp2 += z[j] * z[j];
            }
#pragma omp single
            {
                norm_temp2 = 1.0 / sqrt(norm_temp2);
                zeta = SHIFT + 1.0 / norm_temp1;
            }

#pragma omp master
            {
                if (it == 1) {
                    std::cout << "\n   iteration           ||r||                 zeta" << std::endl;
                }
                std::cout << "   " << it << "           " << rnorm << "                 " << zeta << std::endl;
            }
/* normalize z to obtain x */
#pragma omp for
            for (j = 0; j < lastcol - firstcol + 1; j++) {
                x[j] = norm_temp2 * z[j];
            }
        } /* end of main iter inv pow meth */
#ifdef GEM5
#pragma omp single
        {
            m5_dump_stats(0, 0);
            m5_work_end(0, 0);
            std::cout << "ROI End!!!" << std::endl;
#ifdef CG_FP_ENABLE
            std::cout << "Validation started" << std::endl;
            print_cg_fingerprint(mode, x, z, NA, rnorm, zeta);
            std::cout << "Validation ended" << std::endl;
#endif
#ifdef CG_LOGICAL16_RMW
            uint64_t full_windows = 0;
            uint64_t index_words = 0;
            uint64_t value_words = 0;
            uint64_t legacy_words = 0;
            uint64_t logical_windows = 0;
            uint64_t logical_alus = 0;
            uint64_t product_words = 0;
            uint64_t index_pages = 0;
            uint64_t value_pages = 0;
            for (int core = 0; core < NUM_CORES; ++core) {
                full_windows += cg_soa_full_windows[core];
                index_words += cg_soa_index_words[core];
                value_words += cg_soa_value_words[core];
                legacy_words += cg_legacy_residual_words[core];
#ifdef CG_LOGICAL_PAGE_RMW
                logical_windows += cg_logical_page_windows[core];
                logical_alus += cg_logical_alu_vectors[core];
                product_words += cg_logical_product_words[core];
                index_pages += cg_index_publish_pages[core];
                value_pages += cg_value_publish_pages[core];
#endif
            }
            const bool staged_counts_close =
                index_words == full_windows * TILE_SIZE &&
                value_words == full_windows * TILE_SIZE;
            bool treatment_used = false;
            if (cg_rmw_treatment == CgRmwTreatment::Legacy4K) {
                treatment_used = full_windows == 0 && index_words == 0 &&
                                 value_words == 0 && logical_windows == 0;
            } else if (cg_rmw_treatment ==
                       CgRmwTreatment::ResidualSoaJit) {
                treatment_used = full_windows > 0 && staged_counts_close &&
                                 logical_windows == 0;
            } else {
                treatment_used = full_windows > 0 &&
                    logical_windows == full_windows &&
                    logical_alus == full_windows && staged_counts_close &&
                    product_words == full_windows * TILE_SIZE &&
                    index_pages == full_windows * 4 &&
                    value_pages == full_windows * 4;
            }
            std::cout << "CG_LOGICAL16_RMW_TERMINAL treatment="
                      << cg_rmw_treatment_name(cg_rmw_treatment)
                      << " slice="
                      << (cg_rmw_treatment ==
                                  CgRmwTreatment::LogicalPageSoaJit
                              ? "all_spmv_full_windows"
                              : "residual_spmv")
                      << " full_windows=" << full_windows
                      << " staged_index_words=" << index_words
                      << " staged_value_words=" << value_words
                      << " product_words=" << product_words
                      << " index_publish_pages=" << index_pages
                      << " value_publish_pages=" << value_pages
                      << " logical_alu_vectors=" << logical_alus
                      << " logical_page_windows=" << logical_windows
                      << " legacy_words=" << legacy_words
                      << " producer="
                      << (cg_rmw_treatment ==
                                  CgRmwTreatment::LogicalPageSoaJit
                              ? "response_bearing_spd_pages"
                              : "cpu_after_spd_completion")
                      << " host_payload_access="
                      << (cg_rmw_treatment ==
                                  CgRmwTreatment::LogicalPageSoaJit
                              ? 0
                              : 1)
                      << " performance_promotable=0 result="
                      << (treatment_used ? "PASS" : "FAIL") << std::endl;
            if (!treatment_used)
                std::abort();
#endif
            m5_exit(0);
        }
#endif
    } /* end parallel */

    /*
	 * --------------------------------------------------------------------
	 * end of timed section
	 * --------------------------------------------------------------------
	 */

    std::cout << " Benchmark completed" << std::endl;

#ifdef DO_VERIFY
    double epsilon = 1.0e-4;
    double err = 0;
    if (class_npb != 'U') {
        err = fabs(zeta - zeta_verify_value) / zeta_verify_value;
        if (err <= epsilon) {
            std::cout << " VERIFICATION SUCCESSFUL" << std::endl;
            std::cout << " Zeta is    " << zeta << std::endl;
            std::cout << " Error is   " << err << std::endl;
        } else {
            std::cout << " VERIFICATION FAILED" << std::endl;
            std::cout << " Zeta                " << zeta << std::endl;
            std::cout << " The correct zeta is " << zeta_verify_value << std::endl;
        }
    } else {
        std::cout << " Problem size unknown" << std::endl;
        std::cout << " NO VERIFICATION PERFORMED" << std::endl;
    }
#endif

    return 0;
}

/*
 * ---------------------------------------------------------------------
 * floating point arrays here are named as in NPB1 spec discussion of
 * CG algorithm
 * ---------------------------------------------------------------------
 */
static void conj_grad_maa(int colidx[],
                          int rowstr[],
                          float x[],
                          float z[],
                          float a[],
                          float p[],
                          float q[],
                          float r[],
                          double *rnorm) {
    int j;
    int cgit, cgitmax;
    float alpha, beta, suml;
    static float d, sum, rho, rho0;
    int t0, t1, t2, t3, t4, t5, t6, t7;
    int r1, r2, r3, r4, r5, r6, r7;
#ifdef MAA_GENERAL_VIRTUAL_CONSUMER
    // The MAA scalar register file is shared by all four OpenMP issuers.
    // The ordinary per-thread allocation consumes 7 * 4 = 28 registers;
    // keep the three immutable page bounds shared so the general consumer
    // uses 31 registers rather than allocating 40.
    static int page_min_reg, page_max_reg, page_stride_reg;
#endif

    int tid = omp_get_thread_num();

#ifdef GEM5
#pragma omp single
    {
        clear_mem_region();
        add_mem_region(colidx, &colidx[NZ]);     // 6
        add_mem_region(rowstr, &rowstr[NA + 1]); // 7
        add_mem_region(a, &a[NZ]);               // 8
        add_mem_region(p, &p[NA + 2]);           // 9
        add_mem_region(q, &q[NA + 2]);           // 10
        add_mem_region(z, &z[NA + 2]);           // 11
        add_mem_region(r, &r[NA + 2]);           // 12
        add_mem_region(x, &x[NA + 2]);           // 13
#ifdef CG_LOGICAL16_RMW
        // Eight external producer regions bring CG's total to 17, below the
        // architectural 32-region maximum. Per-owner regions make accidental
        // overlap or cross-thread reuse fail the SoA/JIT span validation.
        for (int core = 0; core < NUM_CORES; ++core) {
            add_mem_region(cg_soa_indices[core],
                           cg_soa_indices[core] + TILE_SIZE);
            add_mem_region(cg_soa_values[core],
                           cg_soa_values[core] + TILE_SIZE);
#ifdef CG_LOGICAL_PAGE_RMW
            add_mem_region(cg_soa_products[core],
                           cg_soa_products[core] + TILE_SIZE);
#endif
        }
#endif
#ifdef MAA_VIRTUAL_GATHER
#ifdef MAA_BOUNDED_VIRTUAL_GATHER
        add_mem_region(virtual_gather_storage,
                       virtual_gather_storage +
                           NUM_CORES * TILE_SIZE +
                           virtual_descriptor_spool_words);
#else
        add_mem_region(&virtual_gather_backing[0][0],
                       &virtual_gather_backing[NUM_CORES - 1][TILE_SIZE]);
#endif
#endif
    }
#endif

    cgitmax = CGITMAX;
#pragma omp single nowait
    {
        rho = 0.0;
        sum = 0.0;
    }

    /* initialize the CG algorithm */
    const int total_thread_iters = NUM_CORES * 8;
    const int naa_plus1 = naa + 1;
    const int naa_plus1_divisible_by_32 = (int)(naa_plus1 / total_thread_iters) * total_thread_iters;
    const int lastrow_firstrow_plus1 = lastrow - firstrow + 1;
    const int lastcol_firstcol_plus1 = lastcol - firstcol + 1;
    const int lastcol_firstcol_plus1_divisible_by_32 = (int)(lastcol_firstcol_plus1 / total_thread_iters) * total_thread_iters;
    const int row_tile_size = MAA_CONSUMER_TILE_SIZE;
#if defined(MAA_BOUNDED_VIRTUAL_GATHER) || \
    defined(MAA_GENERAL_VIRTUAL_CONSUMER)
    // The bounded path pages even the final row block, so one loop can keep
    // the 16K gather window and clamp every ordinary consumer to 4K.
    const int lastrow_firstrow_plus1_divisible_by_64K =
        lastrow_firstrow_plus1;
#else
    const int lastrow_firstrow_plus1_divisible_by_64K =
        (lastrow_firstrow_plus1 / (NUM_CORES * row_tile_size)) *
        NUM_CORES * row_tile_size;
#endif
    const int tile_size = row_tile_size;
    float *my_q = &q[tid * 8];
    float *my_z = &z[tid * 8];
    float *my_r = &r[tid * 8];
    float *my_p = &p[tid * 8];
    float *my_x = &x[tid * 8];

    /* initialize the CG algorithm */
    for (j = 0; j < naa_plus1_divisible_by_32; j += total_thread_iters) {
        my_q[j + 0] = my_z[j + 0] = 0.0;
        my_q[j + 1] = my_z[j + 1] = 0.0;
        my_q[j + 2] = my_z[j + 2] = 0.0;
        my_q[j + 3] = my_z[j + 3] = 0.0;
        my_q[j + 4] = my_z[j + 4] = 0.0;
        my_q[j + 5] = my_z[j + 5] = 0.0;
        my_q[j + 6] = my_z[j + 6] = 0.0;
        my_q[j + 7] = my_z[j + 7] = 0.0;
        my_p[j + 0] = my_r[j + 0] = my_x[j + 0];
        my_p[j + 1] = my_r[j + 1] = my_x[j + 1];
        my_p[j + 2] = my_r[j + 2] = my_x[j + 2];
        my_p[j + 3] = my_r[j + 3] = my_x[j + 3];
        my_p[j + 4] = my_r[j + 4] = my_x[j + 4];
        my_p[j + 5] = my_r[j + 5] = my_x[j + 5];
        my_p[j + 6] = my_r[j + 6] = my_x[j + 6];
        my_p[j + 7] = my_r[j + 7] = my_x[j + 7];
    }
#pragma omp for schedule(dynamic) nowait
    for (j = naa_plus1_divisible_by_32; j < naa_plus1; j++) {
        q[j] = z[j] = 0.0;
        p[j] = r[j] = x[j];
    }

    /*
	 * --------------------------------------------------------------------
	 * rho = r.r
	 * now, obtain the norm of r: First, sum squares of r elements locally...
	 * --------------------------------------------------------------------
	 */
    float rho_tmp = 0.0;
    for (j = 0; j < lastcol_firstcol_plus1_divisible_by_32; j += total_thread_iters) {
        rho_tmp += my_r[j + 0] * my_r[j + 0];
        rho_tmp += my_r[j + 1] * my_r[j + 1];
        rho_tmp += my_r[j + 2] * my_r[j + 2];
        rho_tmp += my_r[j + 3] * my_r[j + 3];
        rho_tmp += my_r[j + 4] * my_r[j + 4];
        rho_tmp += my_r[j + 5] * my_r[j + 5];
        rho_tmp += my_r[j + 6] * my_r[j + 6];
        rho_tmp += my_r[j + 7] * my_r[j + 7];
    }
#pragma omp for schedule(dynamic) nowait
    for (j = lastcol_firstcol_plus1_divisible_by_32; j < lastcol_firstcol_plus1; j++) {
        rho_tmp += r[j] * r[j];
    }
#pragma omp critical
    {
        rho += rho_tmp;
        t0 = get_new_tile<int>();
        t1 = get_new_tile<int>();
        t2 = get_new_tile<int>();
        t3 = get_new_tile<int>();
        t4 = get_new_tile<int>();
        t5 = get_new_tile<int>();
        t6 = get_new_tile<int>();
        t7 = get_new_tile<int>();
        r1 = get_new_reg<int>(1);
        r2 = get_new_reg<int>();
        r3 = get_new_reg<int>();
        r4 = get_new_reg<int>();
        r5 = get_new_reg<int>();
        r6 = get_new_reg<int>();
        r7 = get_new_reg<int>();
    }

#pragma omp barrier
#ifdef MAA_GENERAL_VIRTUAL_CONSUMER
#pragma omp single
    {
        // These are read-only page-local bounds.  They must remain separate
        // from r2/r3/r1 while a virtual producer owns that live range.
        page_min_reg = get_new_reg<int>(0);
        page_max_reg = get_new_reg<int>(MAA_CONSUMER_TILE_SIZE);
        page_stride_reg = get_new_reg<int>(1);
    }
#endif

    /* the conj grad iteration loop */
    for (cgit = 1; cgit <= cgitmax; cgit++) {

        /*
		 * ---------------------------------------------------------------------
		 * q = A.p
		 * the partition submatrix-vector multiply: use workspace w
		 * ---------------------------------------------------------------------
		 *
		 * note: this version of the multiply is actually (slightly: maybe %5)
		 * faster on the sp2 on 16 nodes than is the unrolled-by-2 version
		 * below. on the Cray t3d, the reverse is TRUE, i.e., the
		 * unrolled-by-two version is some 10% faster.
		 * the unrolled-by-8 version below is significantly faster
		 * on the Cray t3d - overall speed of code is 1.5 times faster.
		 */

#pragma omp single nowait
        {
            d = 0.0;
            rho0 = rho;
            rho = 0.0;
        }

        // LOOP 1
        // for (j = 0; j < lastrow - firstrow + 1; j++) {
        //     suml = 0.0;
        //     for (k = rowstr[j]; k < rowstr[j + 1]; k++) {
        //         suml += a[k] * p[colidx[k]];
        //     }
        //     q[j] = suml;
        // }
        if (cgit != 1) {
            for (j = 0; j < naa_plus1_divisible_by_32; j += total_thread_iters) {
                my_q[j + 0] = 0.0;
                my_q[j + 1] = 0.0;
                my_q[j + 2] = 0.0;
                my_q[j + 3] = 0.0;
                my_q[j + 4] = 0.0;
                my_q[j + 5] = 0.0;
                my_q[j + 6] = 0.0;
                my_q[j + 7] = 0.0;
            }
#pragma omp for schedule(dynamic)
            for (j = naa_plus1_divisible_by_32; j < naa_plus1; j++) {
                q[j] = 0.0;
            }
        }

        maa_const<int>(lastrow_firstrow_plus1_divisible_by_64K, r5);
#pragma omp for nowait
        for (int j_base = 0;
             j_base < lastrow_firstrow_plus1_divisible_by_64K;
             j_base += row_tile_size) {
            int j_max =
                j_base + row_tile_size <
                        lastrow_firstrow_plus1_divisible_by_64K
                    ? j_base + row_tile_size
                    : lastrow_firstrow_plus1_divisible_by_64K;
            int k_base = rowstr[j_base];
            int k_max = rowstr[j_max];
            float *curr_q = &q[j_base];
#if defined(MAA_BOUNDED_VIRTUAL_GATHER) || \
    defined(MAA_GENERAL_VIRTUAL_CONSUMER)
            // Ordinary SPD streams are not virtualized. Rebase each row
            // pointer page so its physical tile positions remain 0..4K.
            maa_const<int>(0, r4);
            maa_const<int>(j_max - j_base, r5);
#else
            maa_const<int>(j_base, r4);
#endif
            maa_const<int>(k_max, r3);
            maa_const<int>(0, r6);
            maa_const<int>(-1, r7);
            // t2 = rowstr[j]
            // t3 = rowstr[j + 1]
#if defined(MAA_BOUNDED_VIRTUAL_GATHER) || \
    defined(MAA_GENERAL_VIRTUAL_CONSUMER)
            maa_stream_load<int>(&rowstr[j_base], r4, r5, r1, t2);
            maa_stream_load<int>(&rowstr[j_base + 1], r4, r5, r1, t3);
#else
            maa_stream_load<int>(rowstr, r4, r5, r1, t2);
            maa_stream_load<int>(&rowstr[1], r4, r5, r1, t3);
#endif
            // [t0 t1 t4 t5 t6 t7] available
            for (; k_base < k_max; k_base += TILE_SIZE) {
                const int gather_size = k_max - k_base < TILE_SIZE
                                            ? k_max - k_base
                                            : TILE_SIZE;
#ifdef MAA_BOUNDED_VIRTUAL_GATHER
                if (gather_size == TILE_SIZE) {
                    // Reorder one complete logical window, then consume its
                    // coherent backing through physical-sized SPD pages.
                    maa_const<int>(k_base, r2);
                    maa_const<int>(k_base + gather_size, r3);
                    maa_indirect_load_virtual_index<float>(
                        p, reinterpret_cast<uint32_t *>(colidx), t4,
                        virtual_gather_backing_for_thread(tid), r2, r3, r1);
                    wait_ready(t4);
                }
                for (int page_offset = 0; page_offset < gather_size;
                     page_offset += MAA_CONSUMER_TILE_SIZE) {
                    const int page_size =
                        gather_size - page_offset < MAA_CONSUMER_TILE_SIZE
                            ? gather_size - page_offset
                            : MAA_CONSUMER_TILE_SIZE;
                    maa_range_loop<int>(r6, r7, t2, t3, r1, t0, t1);

                    if (gather_size == TILE_SIZE) {
                        maa_const<int>(page_offset, r2);
                        maa_const<int>(page_offset + page_size, r3);
                        maa_stream_load<float>(
                            virtual_gather_backing_for_thread(tid), r2, r3,
                            r1, t4);
                    } else {
                        const int page_base = k_base + page_offset;
                        maa_const<int>(0, r2);
                        maa_const<int>(page_size, r3);
                        maa_stream_load<int>(&colidx[page_base], r2, r3,
                                             r1, t6);
                        maa_indirect_load<float>(p, t6, t4);
                    }

                    const int page_base = k_base + page_offset;
                    maa_const<int>(0, r2);
                    maa_const<int>(page_size, r3);
                    maa_stream_load<float>(&a[page_base], r2, r3, r1, t5);
                    maa_alu_vector<float>(t4, t5, t7,
                                          Operation_t::MUL_OP);
                    maa_indirect_rmw_vector(curr_q, t0, t7,
                                            Operation_t::ADD_OP);
                    wait_ready(t7);
                }
#elif defined(MAA_GENERAL_VIRTUAL_CONSUMER)
#ifdef CG_LOGICAL_PAGE_RMW
                const bool logical_page_full_window =
                    cg_rmw_treatment ==
                        CgRmwTreatment::LogicalPageSoaJit &&
                    gather_size == TILE_SIZE;
#else
                const bool logical_page_full_window = false;
#endif
                if (gather_size == TILE_SIZE) {
                    maa_const<int>(k_base, r2);
                    maa_const<int>(k_base + gather_size, r3);
                    maa_indirect_load_virtual_index<float>(
                        p, reinterpret_cast<uint32_t *>(colidx), t6,
                        virtual_gather_backing_for_thread(tid), r2, r3, r1);
                    if (logical_page_full_window)
                        wait_ready(t6);
                    else
                        maa_virtual_consumer_begin(virtual_consumer_mode, t6);
                }
                for (int page_offset = 0; page_offset < gather_size;
                     page_offset += MAA_CONSUMER_TILE_SIZE) {
                    const int page_size =
                        gather_size - page_offset < MAA_CONSUMER_TILE_SIZE
                            ? gather_size - page_offset
                            : MAA_CONSUMER_TILE_SIZE;
                    maa_range_loop<int>(r6, r7, t2, t3, r1, t0, t1);

                    const int page_base = k_base + page_offset;
#ifdef CG_LOGICAL_PAGE_RMW
                    if (logical_page_full_window) {
                        maa_const<int>(0, r2);
                        maa_const<int>(page_size, r3);
                        maa_stream_load<float>(&a[page_base], r2, r3, r1, t5);
                        wait_ready(t0);
                        wait_ready(t5);
                        cg_publish_index_value_page(
                            tid, page_offset, t0, t5, t1, t4, r4, r5, r2);
                        continue;
                    }
#endif
                    if (gather_size == TILE_SIZE) {
                        // Page bounds are immutable and disjoint from the
                        // live virtual-producer range in r2/r3/r1.
                        maa_virtual_consumer_load_page<float>(
                            virtual_consumer_mode,
                            virtual_gather_backing_for_thread(tid) +
                                page_offset,
                            t6, page_offset / MAA_CONSUMER_TILE_SIZE,
                            page_min_reg, page_max_reg, page_stride_reg, t4);
                    } else {
                        maa_const<int>(0, r2);
                        maa_const<int>(page_size, r3);
                        maa_stream_load<int>(&colidx[page_base], r2, r3,
                                             r1, t6);
                        maa_indirect_load<float>(p, t6, t4);
                    }

                    maa_const<int>(0, r2);
                    maa_const<int>(page_size, r3);
                    maa_stream_load<float>(&a[page_base], r2, r3, r1, t5);
                    maa_alu_vector<float>(t4, t5, t7,
                                          Operation_t::MUL_OP);
                    maa_indirect_rmw_vector(curr_q, t0, t7,
                                            Operation_t::ADD_OP);
                    wait_ready(t7);
                }
                if (gather_size == TILE_SIZE && !logical_page_full_window)
                    maa_virtual_consumer_end(virtual_consumer_mode, t6);
#ifdef CG_LOGICAL_PAGE_RMW
                if (logical_page_full_window)
                    cg_logical_multiply_rmw(tid, curr_q, r2, r3, r1, t7);
#endif
#else
                maa_const(k_base, r2);
                maa_range_loop<int>(r6, r7, t2, t3, r1, t0, t1);
                maa_stream_load<int>(colidx, r2, r3, r1, t6);
#ifdef MAA_VIRTUAL_GATHER
                maa_indirect_load_virtual<float>(
                    p, t6, t4, virtual_gather_backing_for_thread(tid));
                wait_ready(t4);
                maa_const<int>(0, r2);
                maa_const<int>(gather_size, r3);
                maa_stream_load<float>(virtual_gather_backing_for_thread(tid),
                                       r2, r3, r1, t4);
                maa_const<int>(k_base, r2);
                maa_const<int>(k_max, r3);
#else
                maa_indirect_load<float>(p, t6, t4);
#endif
                maa_stream_load<float>(a, r2, r3, r1, t5);
                maa_alu_vector<float>(t4, t5, t7, Operation_t::MUL_OP);
                maa_indirect_rmw_vector(curr_q, t0, t7,
                                        Operation_t::ADD_OP);
                wait_ready(t7);
#endif
            }
        }

#pragma omp for schedule(dynamic)
        for (int j_base = lastrow_firstrow_plus1_divisible_by_64K; j_base < lastrow_firstrow_plus1; j_base += tile_size) {
            int j_max = j_base + tile_size < lastrow_firstrow_plus1 ? j_base + tile_size : lastrow_firstrow_plus1;
            int k_base = rowstr[j_base];
            int k_max = rowstr[j_max];
            float *curr_q = &q[j_base];
            maa_const<int>(j_base, r4);
            maa_const<int>(j_max, r5);
            maa_const<int>(k_max, r3);
            maa_const<int>(0, r6);
            maa_const<int>(-1, r7);
            // t2 = rowstr[j]
            // t3 = rowstr[j + 1]
            maa_stream_load<int>(rowstr, r4, r5, r1, t2);
            maa_stream_load<int>(&rowstr[1], r4, r5, r1, t3);
            // [t0 t1 t4 t5 t6 t7] available
            for (; k_base < k_max; k_base += TILE_SIZE) {
                maa_const(k_base, r2);
                // t0 = j
                // t1 = k that is not needed
                maa_range_loop<int>(r6, r7, t2, t3, r1, t0, t1);
                // [t1 t4 t5 t6 t7] available

                // t6 = colidx[k]
                maa_stream_load<int>(colidx, r2, r3, r1, t6);
                // [t1 t4 t5 t7] available

                // t4 = p[colidx[k]]
                // free t6
                maa_indirect_load<float>(p, t6, t4);
                // [t1 t5 t6 t7] available

                // t5 = a[k]
                maa_stream_load<float>(a, r2, r3, r1, t5);
                // [t1 t6 t7] available

                // t7 = a[k] * p[colidx[k]]
                maa_alu_vector<float>(t4, t5, t7, Operation_t::MUL_OP);
                // [t1 t6] available

                // q[j] += t7
                maa_indirect_rmw_vector(curr_q, t0, t7, Operation_t::ADD_OP);
                wait_ready(t7);
            }
        }
#pragma omp barrier

        /*
		 * --------------------------------------------------------------------
		 * obtain p.q
		 * --------------------------------------------------------------------
		 */

        float d_tmp = 0.0;
        for (j = 0; j < lastcol_firstcol_plus1_divisible_by_32; j += total_thread_iters) {
            d_tmp += my_p[j + 0] * my_q[j + 0];
            d_tmp += my_p[j + 1] * my_q[j + 1];
            d_tmp += my_p[j + 2] * my_q[j + 2];
            d_tmp += my_p[j + 3] * my_q[j + 3];
            d_tmp += my_p[j + 4] * my_q[j + 4];
            d_tmp += my_p[j + 5] * my_q[j + 5];
            d_tmp += my_p[j + 6] * my_q[j + 6];
            d_tmp += my_p[j + 7] * my_q[j + 7];
        }
#pragma omp for schedule(dynamic) nowait
        for (j = lastcol_firstcol_plus1_divisible_by_32; j < lastcol_firstcol_plus1; j++) {
            d_tmp += p[j] * q[j];
        }
#pragma omp critical
        {
            d += d_tmp;
        }
#pragma omp barrier
        /*
		 * --------------------------------------------------------------------
		 * obtain alpha = rho / (p.q)
		 * -------------------------------------------------------------------
		 */
        alpha = rho0 / d;
        // std::cout << "alpha (" << alpha << ") = rho0 (" << rho0 << ") / d (" << d << ")" << std::endl;

        /*
		 * ---------------------------------------------------------------------
		 * obtain z = z + alpha*p
		 * and    r = r - alpha*q
		 * ---------------------------------------------------------------------
		 */

        float rho_tmp = 0.0;
        for (j = 0; j < lastcol_firstcol_plus1_divisible_by_32; j += total_thread_iters) {
            my_z[j + 0] += alpha * my_p[j + 0];
            my_z[j + 1] += alpha * my_p[j + 1];
            my_z[j + 2] += alpha * my_p[j + 2];
            my_z[j + 3] += alpha * my_p[j + 3];
            my_z[j + 4] += alpha * my_p[j + 4];
            my_z[j + 5] += alpha * my_p[j + 5];
            my_z[j + 6] += alpha * my_p[j + 6];
            my_z[j + 7] += alpha * my_p[j + 7];
            my_r[j + 0] -= alpha * my_q[j + 0];
            my_r[j + 1] -= alpha * my_q[j + 1];
            my_r[j + 2] -= alpha * my_q[j + 2];
            my_r[j + 3] -= alpha * my_q[j + 3];
            my_r[j + 4] -= alpha * my_q[j + 4];
            my_r[j + 5] -= alpha * my_q[j + 5];
            my_r[j + 6] -= alpha * my_q[j + 6];
            my_r[j + 7] -= alpha * my_q[j + 7];
            rho_tmp += my_r[j + 0] * my_r[j + 0];
            rho_tmp += my_r[j + 1] * my_r[j + 1];
            rho_tmp += my_r[j + 2] * my_r[j + 2];
            rho_tmp += my_r[j + 3] * my_r[j + 3];
            rho_tmp += my_r[j + 4] * my_r[j + 4];
            rho_tmp += my_r[j + 5] * my_r[j + 5];
            rho_tmp += my_r[j + 6] * my_r[j + 6];
            rho_tmp += my_r[j + 7] * my_r[j + 7];
        }
#pragma omp for schedule(dynamic) nowait
        for (j = lastcol_firstcol_plus1_divisible_by_32; j < lastcol_firstcol_plus1; j++) {
            z[j] += alpha * p[j];
            r[j] -= alpha * q[j];
            rho_tmp += r[j] * r[j];
        }
#pragma omp critical
        {
            rho += rho_tmp;
        }
#pragma omp barrier

        beta = rho / rho0;
        // std::cout << "beta (" << beta << ") = rho (" << rho << ") / rho0 (" << rho0 << ")" << std::endl;

        /*
		 * ---------------------------------------------------------------------
		 * p = r + beta*p
		 * ---------------------------------------------------------------------
		 */
        for (j = 0; j < lastcol_firstcol_plus1_divisible_by_32; j += total_thread_iters) {
            my_p[j + 0] = my_r[j + 0] + beta * my_p[j + 0];
            my_p[j + 1] = my_r[j + 1] + beta * my_p[j + 1];
            my_p[j + 2] = my_r[j + 2] + beta * my_p[j + 2];
            my_p[j + 3] = my_r[j + 3] + beta * my_p[j + 3];
            my_p[j + 4] = my_r[j + 4] + beta * my_p[j + 4];
            my_p[j + 5] = my_r[j + 5] + beta * my_p[j + 5];
            my_p[j + 6] = my_r[j + 6] + beta * my_p[j + 6];
            my_p[j + 7] = my_r[j + 7] + beta * my_p[j + 7];
        }
#pragma omp for schedule(dynamic)
        for (j = lastcol_firstcol_plus1_divisible_by_32; j < lastcol_firstcol_plus1; j++) {
            p[j] = r[j] + beta * p[j];
        }
    } /* end of do cgit=1, cgitmax */

    /*
	 * ---------------------------------------------------------------------
	 * compute residual norm explicitly: ||r|| = ||x - A.z||
	 * first, form A.z
	 * the partition submatrix-vector multiply
	 * ---------------------------------------------------------------------
	 */
    // LOOP 2
    // #pragma omp for nowait
    //     for (j = 0; j < lastrow - firstrow + 1; j++) {
    //         suml = 0.0;
    //         for (k = rowstr[j]; k < rowstr[j + 1]; k++) {
    //             suml += a[k] * z[colidx[k]];
    //         }
    //         r[j] = suml;
    //     }

    for (j = 0; j < naa_plus1_divisible_by_32; j += total_thread_iters) {
        my_r[j + 0] = 0.0;
        my_r[j + 1] = 0.0;
        my_r[j + 2] = 0.0;
        my_r[j + 3] = 0.0;
        my_r[j + 4] = 0.0;
        my_r[j + 5] = 0.0;
        my_r[j + 6] = 0.0;
        my_r[j + 7] = 0.0;
    }
#pragma omp for schedule(dynamic)
    for (j = naa_plus1_divisible_by_32; j < naa_plus1; j++) {
        r[j] = 0.0;
    }

    maa_const<int>(lastrow_firstrow_plus1_divisible_by_64K, r5);
#pragma omp for nowait
    for (int j_base = 0;
         j_base < lastrow_firstrow_plus1_divisible_by_64K;
         j_base += row_tile_size) {
        int j_max =
            j_base + row_tile_size <
                    lastrow_firstrow_plus1_divisible_by_64K
                ? j_base + row_tile_size
                : lastrow_firstrow_plus1_divisible_by_64K;
        int k_base = rowstr[j_base];
        int k_max = rowstr[j_max];
        float *curr_r = &r[j_base];
#if defined(MAA_BOUNDED_VIRTUAL_GATHER) || \
    defined(MAA_GENERAL_VIRTUAL_CONSUMER)
        // Keep the residual row-pointer streams in page-local SPD positions.
        maa_const<int>(0, r4);
        maa_const<int>(j_max - j_base, r5);
#else
        maa_const<int>(j_base, r4);
#endif
        maa_const<int>(k_max, r3);
        maa_const<int>(0, r6);
        maa_const<int>(-1, r7);
        // t2 = rowstr[j]
        // t3 = rowstr[j + 1]
#if defined(MAA_BOUNDED_VIRTUAL_GATHER) || \
    defined(MAA_GENERAL_VIRTUAL_CONSUMER)
        maa_stream_load<int>(&rowstr[j_base], r4, r5, r1, t2);
        maa_stream_load<int>(&rowstr[j_base + 1], r4, r5, r1, t3);
#else
        maa_stream_load<int>(rowstr, r4, r5, r1, t2);
        maa_stream_load<int>(&rowstr[1], r4, r5, r1, t3);
#endif
        // [t0 t1 t4 t5 t6 t7] available
        for (; k_base < k_max; k_base += TILE_SIZE) {
            const int gather_size = k_max - k_base < TILE_SIZE
                                        ? k_max - k_base
                                        : TILE_SIZE;
#ifdef MAA_BOUNDED_VIRTUAL_GATHER
            if (gather_size == TILE_SIZE) {
                maa_const<int>(k_base, r2);
                maa_const<int>(k_base + gather_size, r3);
                maa_indirect_load_virtual_index<float>(
                    z, reinterpret_cast<uint32_t *>(colidx), t4,
                    virtual_gather_backing_for_thread(tid), r2, r3, r1);
                wait_ready(t4);
            }
            for (int page_offset = 0; page_offset < gather_size;
                 page_offset += MAA_CONSUMER_TILE_SIZE) {
                const int page_size =
                    gather_size - page_offset < MAA_CONSUMER_TILE_SIZE
                        ? gather_size - page_offset
                        : MAA_CONSUMER_TILE_SIZE;
                maa_range_loop<int>(r6, r7, t2, t3, r1, t0, t1);

                if (gather_size == TILE_SIZE) {
                    maa_const<int>(page_offset, r2);
                    maa_const<int>(page_offset + page_size, r3);
                    maa_stream_load<float>(
                        virtual_gather_backing_for_thread(tid), r2, r3, r1,
                        t4);
                } else {
                    const int page_base = k_base + page_offset;
                    maa_const<int>(0, r2);
                    maa_const<int>(page_size, r3);
                    maa_stream_load<int>(&colidx[page_base], r2, r3, r1,
                                         t6);
                    maa_indirect_load<float>(z, t6, t4);
                }

                const int page_base = k_base + page_offset;
                maa_const<int>(0, r2);
                maa_const<int>(page_size, r3);
                maa_stream_load<float>(&a[page_base], r2, r3, r1, t5);
                maa_alu_vector<float>(t4, t5, t7,
                                      Operation_t::MUL_OP);
                maa_indirect_rmw_vector(curr_r, t0, t7,
                                        Operation_t::ADD_OP);
                wait_ready(t7);
            }
#elif defined(MAA_GENERAL_VIRTUAL_CONSUMER)
#ifdef CG_LOGICAL16_RMW
            const bool soa_residual_full_window =
                cg_rmw_treatment == CgRmwTreatment::ResidualSoaJit &&
                gather_size == TILE_SIZE;
#endif
#ifdef CG_LOGICAL_PAGE_RMW
            const bool logical_page_full_window =
                cg_rmw_treatment == CgRmwTreatment::LogicalPageSoaJit &&
                gather_size == TILE_SIZE;
#else
            const bool logical_page_full_window = false;
#endif
            if (gather_size == TILE_SIZE) {
                maa_const<int>(k_base, r2);
                maa_const<int>(k_base + gather_size, r3);
                maa_indirect_load_virtual_index<float>(
                    z, reinterpret_cast<uint32_t *>(colidx), t6,
                    virtual_gather_backing_for_thread(tid), r2, r3, r1);
                if (logical_page_full_window)
                    wait_ready(t6);
                else
                    maa_virtual_consumer_begin(virtual_consumer_mode, t6);
            }
            for (int page_offset = 0; page_offset < gather_size;
                 page_offset += MAA_CONSUMER_TILE_SIZE) {
                const int page_size =
                    gather_size - page_offset < MAA_CONSUMER_TILE_SIZE
                        ? gather_size - page_offset
                        : MAA_CONSUMER_TILE_SIZE;
                maa_range_loop<int>(r6, r7, t2, t3, r1, t0, t1);

                const int page_base = k_base + page_offset;
#ifdef CG_LOGICAL_PAGE_RMW
                if (logical_page_full_window) {
                    maa_const<int>(0, r2);
                    maa_const<int>(page_size, r3);
                    maa_stream_load<float>(&a[page_base], r2, r3, r1, t5);
                    wait_ready(t0);
                    wait_ready(t5);
                    cg_publish_index_value_page(
                        tid, page_offset, t0, t5, t1, t4, r4, r5, r2);
                    continue;
                }
#endif
                if (gather_size == TILE_SIZE) {
                    maa_virtual_consumer_load_page<float>(
                        virtual_consumer_mode,
                        virtual_gather_backing_for_thread(tid) + page_offset,
                        t6, page_offset / MAA_CONSUMER_TILE_SIZE,
                        page_min_reg, page_max_reg, page_stride_reg, t4);
                } else {
                    maa_const<int>(0, r2);
                    maa_const<int>(page_size, r3);
                    maa_stream_load<int>(&colidx[page_base], r2, r3, r1,
                                         t6);
                    maa_indirect_load<float>(z, t6, t4);
                }

                maa_const<int>(0, r2);
                maa_const<int>(page_size, r3);
                maa_stream_load<float>(&a[page_base], r2, r3, r1, t5);
                maa_alu_vector<float>(t4, t5, t7,
                                      Operation_t::MUL_OP);
#ifdef CG_LOGICAL16_RMW
                if (soa_residual_full_window) {
                    // These are exactly the index/value operands that the
                    // legacy page-local RMW would consume. Preserve their
                    // page and bit order in external guest memory, but do
                    // not expose the physical SPD page to a CPU cache
                    // stream: a sequential CPU read can prefetch element
                    // 4096, which is outside the 4K physical tile.
                    uint32_t *index_dst =
                        cg_soa_indices[tid] + page_offset;
                    float *value_dst = cg_soa_values[tid] + page_offset;
                    maa_const<int>(0, r2);
                    maa_const<int>(page_size, r3);
                    maa_stream_store<uint32_t>(index_dst, r2, r3, r1, t0);
                    maa_stream_store<float>(value_dst, r2, r3, r1, t7);
                    wait_ready(t0);
                    wait_ready(t7);
                    std::atomic_thread_fence(std::memory_order_seq_cst);
                    for (int word = 0; word < page_size; ++word) {
                        if (index_dst[word] >=
                            static_cast<uint32_t>(j_max - j_base)) {
                            std::cerr << "CG logical-16 producer index out of "
                                         "range: page="
                                      << page_offset << " word=" << word
                                      << " index=" << index_dst[word]
                                      << " rows=" << j_max - j_base
                                      << std::endl;
                            std::abort();
                        }
                    }
                    cg_soa_index_words[tid] += page_size;
                    cg_soa_value_words[tid] += page_size;
                } else
#endif
                {
                    maa_indirect_rmw_vector(curr_r, t0, t7,
                                            Operation_t::ADD_OP);
                    wait_ready(t7);
#ifdef CG_LOGICAL16_RMW
                    cg_legacy_residual_words[tid] += page_size;
#endif
                }
            }
            if (gather_size == TILE_SIZE && !logical_page_full_window)
                maa_virtual_consumer_end(virtual_consumer_mode, t6);
#ifdef CG_LOGICAL_PAGE_RMW
            if (logical_page_full_window)
                cg_logical_multiply_rmw(tid, curr_r, r2, r3, r1, t7);
#endif
#ifdef CG_LOGICAL16_RMW
            if (soa_residual_full_window) {
                // Staging is charged as coherent CPU traffic. This is a
                // correctness/provenance slice, not a promotable performance
                // treatment; the arrays remain immutable through completion.
                std::atomic_thread_fence(std::memory_order_seq_cst);
                maa_const<int>(0, r2);
                maa_const<int>(TILE_SIZE, r3);
                maa_const<int>(1, r1);
                maa_indirect_rmw_vector_soa_jit<float>(
                    curr_r, cg_soa_indices[tid], cg_soa_values[tid], nullptr,
                    r2, r3, r1, t7, Operation_t::ADD_OP);
                wait_ready(t7);
                cg_soa_full_windows[tid]++;
            }
#endif
#else
            maa_const(k_base, r2);
            maa_range_loop<int>(r6, r7, t2, t3, r1, t0, t1);
            maa_stream_load<int>(colidx, r2, r3, r1, t6);
#ifdef MAA_VIRTUAL_GATHER
            maa_indirect_load_virtual<float>(
                z, t6, t4, virtual_gather_backing_for_thread(tid));
            wait_ready(t4);
            maa_const<int>(0, r2);
            maa_const<int>(gather_size, r3);
            maa_stream_load<float>(virtual_gather_backing_for_thread(tid),
                                   r2, r3, r1, t4);
            maa_const<int>(k_base, r2);
            maa_const<int>(k_max, r3);
#else
            maa_indirect_load<float>(z, t6, t4);
#endif
            maa_stream_load<float>(a, r2, r3, r1, t5);
            maa_alu_vector<float>(t4, t5, t7, Operation_t::MUL_OP);
            maa_indirect_rmw_vector(curr_r, t0, t7,
                                    Operation_t::ADD_OP);
            wait_ready(t7);
#endif
        }
    }

#pragma omp for schedule(dynamic)
    for (int j_base = lastrow_firstrow_plus1_divisible_by_64K; j_base < lastrow_firstrow_plus1; j_base += tile_size) {
        int j_max = j_base + tile_size < lastrow_firstrow_plus1 ? j_base + tile_size : lastrow_firstrow_plus1;
        int k_base = rowstr[j_base];
        int k_max = rowstr[j_max];
        float *curr_r = &r[j_base];
        maa_const<int>(j_base, r4);
        maa_const<int>(j_max, r5);
        maa_const<int>(k_max, r3);
        maa_const<int>(0, r6);
        maa_const<int>(-1, r7);
        // t2 = rowstr[j]
        // t3 = rowstr[j + 1]
        maa_stream_load<int>(rowstr, r4, r5, r1, t2);
        maa_stream_load<int>(&rowstr[1], r4, r5, r1, t3);
        // [t0 t1 t4 t5 t6 t7] available
        for (; k_base < k_max; k_base += TILE_SIZE) {
            maa_const(k_base, r2);
            // t0 = j
            // t1 = k that is not needed
            maa_range_loop<int>(r6, r7, t2, t3, r1, t0, t1);
            // [t1 t4 t5 t6 t7] available

            // t6 = colidx[k]
            maa_stream_load<int>(colidx, r2, r3, r1, t6);
            // [t1 t4 t5 t7] available

            // t4 = z[colidx[k]]
            // free t6
            maa_indirect_load<float>(z, t6, t4);
            // [t1 t5 t6 t7] available

            // t5 = a[k]
            maa_stream_load<float>(a, r2, r3, r1, t5);
            // [t1 t6 t7] available

            // t7 = a[k] * z[colidx[k]]
            maa_alu_vector<float>(t4, t5, t7, Operation_t::MUL_OP);
            // [t1 t6] available

            // r[j] += t7
            maa_indirect_rmw_vector(curr_r, t0, t7, Operation_t::ADD_OP);
            wait_ready(t7);
        }
    }
#pragma omp barrier

    // #pragma omp for nowait
    //     for (int j_base = 0; j_base < lastrow_firstrow_plus1_divisible_by_64K; j_base += TILE_SIZE) {
    //         int j_max = j_base + TILE_SIZE < lastrow_firstrow_plus1_divisible_by_64K ? j_base + TILE_SIZE : lastrow_firstrow_plus1_divisible_by_64K;
    //         int j_curr = j_base;
    //         int k_base = rowstr[j_base];
    //         int k_max = rowstr[j_max];
    //         int curr_max_row_str = rowstr[j_base + 1];
    //         float suml = 0.0;
    //         maa_const(k_max, r3);
    //         for (; k_base < k_max; k_base += TILE_SIZE) {
    //             maa_const(k_base, r2);
    //             maa_stream_load<int>(colidx, r2, r3, r1, t1);
    //             maa_indirect_load<float>(z, t1, t0);
    //             int curr_tilek_size = k_max - k_base < TILE_SIZE ? k_max - k_base : TILE_SIZE;
    //             int k_curr = k_base;
    //             wait_ready(t0);
    //             for (int i = 0; i < curr_tilek_size; i++) {
    //                 if (k_curr == curr_max_row_str) {
    //                     r[j_curr] = suml;
    //                     // std::cout << "r[" << j_curr << "] = " << suml << std::endl;
    //                     suml = 0.0;
    //                     j_curr++;
    //                     curr_max_row_str = rowstr[j_curr + 1];
    //                 }
    //                 // std::cout << "suml(" << suml << ") += a[" << k_curr << "](" << a[k_curr] << ") * p[colidx[" << k_curr << "](" << colidx[k_curr] << ")](" << t0_ptr[i] << " or " << p[colidx[k_curr]] << ")" << std::endl;
    //                 suml += a[k_curr] * t0_ptr[i];
    //                 k_curr++;
    //             }
    //         }
    //         r[j_curr] = suml;
    //         // std::cout << "r[" << j_curr << "] = " << suml << std::endl;
    //     }

    // #pragma omp for schedule(dynamic)
    //     for (int j_base = lastrow_firstrow_plus1_divisible_by_64K; j_base < lastrow_firstrow_plus1; j_base += tile_size) {
    //         int j_max = j_base + tile_size < lastrow_firstrow_plus1 ? j_base + tile_size : lastrow_firstrow_plus1;
    //         int j_curr = j_base;
    //         int k_base = rowstr[j_base];
    //         int k_max = rowstr[j_max];
    //         int curr_max_row_str = rowstr[j_base + 1];
    //         float suml = 0.0;
    //         maa_const(k_max, r3);
    //         for (; k_base < k_max; k_base += TILE_SIZE) {
    //             maa_const(k_base, r2);
    //             maa_stream_load<int>(colidx, r2, r3, r1, t1);
    //             maa_indirect_load<float>(z, t1, t0);
    //             int curr_tilek_size = k_max - k_base < TILE_SIZE ? k_max - k_base : TILE_SIZE;
    //             int k_curr = k_base;
    //             wait_ready(t0);
    //             for (int i = 0; i < curr_tilek_size; i++) {
    //                 if (k_curr == curr_max_row_str) {
    //                     r[j_curr] = suml;
    //                     // std::cout << "r[" << j_curr << "] = " << suml << std::endl;
    //                     suml = 0.0;
    //                     j_curr++;
    //                     curr_max_row_str = rowstr[j_curr + 1];
    //                 }
    //                 // std::cout << "suml(" << suml << ") += a[" << k_curr << "](" << a[k_curr] << ") * p[colidx[" << k_curr << "](" << colidx[k_curr] << ")](" << t0_ptr[i] << " or " << p[colidx[k_curr]] << ")" << std::endl;
    //                 suml += a[k_curr] * t0_ptr[i];
    //                 k_curr++;
    //             }
    //         }
    //         r[j_curr] = suml;
    //         // std::cout << "r[" << j_curr << "] = " << suml << std::endl;
    //     }
    // #pragma omp barrier

    float sum_tmp = 0.0;
    for (j = 0; j < lastcol_firstcol_plus1_divisible_by_32; j += total_thread_iters) {
        suml = x[j] - r[j];
        sum_tmp += suml * suml;
        suml = x[j + 1] - r[j + 1];
        sum_tmp += suml * suml;
        suml = x[j + 2] - r[j + 2];
        sum_tmp += suml * suml;
        suml = x[j + 3] - r[j + 3];
        sum_tmp += suml * suml;
        suml = x[j + 4] - r[j + 4];
        sum_tmp += suml * suml;
        suml = x[j + 5] - r[j + 5];
        sum_tmp += suml * suml;
        suml = x[j + 6] - r[j + 6];
        sum_tmp += suml * suml;
        suml = x[j + 7] - r[j + 7];
        sum_tmp += suml * suml;
    }
#pragma omp for schedule(dynamic) nowait
    for (j = lastcol_firstcol_plus1_divisible_by_32; j < lastcol_firstcol_plus1; j++) {
        suml = x[j] - r[j];
        sum_tmp += suml * suml;
    }
#pragma omp critical
    {
        sum += sum_tmp;
    }
#pragma omp barrier

#pragma omp single
    *rnorm = sqrt(sum);

#ifdef GEM5
#pragma omp single
    {
        clear_mem_region();
    }
#endif
}

/*
 * ---------------------------------------------------------------------
 * floating point arrays here are named as in NPB1 spec discussion of
 * CG algorithm
 * ---------------------------------------------------------------------
 */
static void conj_grad_base(int colidx[],
                           int rowstr[],
                           float x[],
                           float z[],
                           float a[],
                           float p[],
                           float q[],
                           float r[],
                           double *rnorm) {
    int j, k;
    int cgit, cgitmax;
    float alpha, beta, suml;
    static float d, sum, rho, rho0;

#ifdef GEM5
#pragma omp single
    {
        clear_mem_region();
        add_mem_region(colidx, &colidx[NZ]);     // 6
        add_mem_region(rowstr, &rowstr[NA + 1]); // 7
        add_mem_region(a, &a[NZ]);               // 8
        add_mem_region(p, &p[NA + 2]);           // 9
        add_mem_region(q, &q[NA + 2]);           // 10
        add_mem_region(z, &z[NA + 2]);           // 11
        add_mem_region(r, &r[NA + 2]);           // 12
        add_mem_region(x, &x[NA + 2]);           // 13
    }
#endif

    cgitmax = CGITMAX;
#pragma omp single nowait
    {
        rho = 0.0;
        sum = 0.0;
    }
    /* initialize the CG algorithm */
#pragma omp for schedule(dynamic, 8)
    for (j = 0; j < naa + 1; j++) {
        q[j] = 0.0;
        z[j] = 0.0;
        r[j] = x[j];
        p[j] = r[j];
    }

    /*
	 * --------------------------------------------------------------------
	 * rho = r.r
	 * now, obtain the norm of r: First, sum squares of r elements locally...
	 * --------------------------------------------------------------------
	 */
#pragma omp for reduction(+ : rho)
    for (j = 0; j < lastcol - firstcol + 1; j++) {
        rho += r[j] * r[j];
    }

    /* the conj grad iteration loop */
    for (cgit = 1; cgit <= cgitmax; cgit++) {
        /*
		 * ---------------------------------------------------------------------
		 * q = A.p
		 * the partition submatrix-vector multiply: use workspace w
		 * ---------------------------------------------------------------------
		 *
		 * note: this version of the multiply is actually (slightly: maybe %5)
		 * faster on the sp2 on 16 nodes than is the unrolled-by-2 version
		 * below. on the Cray t3d, the reverse is TRUE, i.e., the
		 * unrolled-by-two version is some 10% faster.
		 * the unrolled-by-8 version below is significantly faster
		 * on the Cray t3d - overall speed of code is 1.5 times faster.
		 */

#pragma omp single nowait
        {
            d = 0.0;
            /*
			 * --------------------------------------------------------------------
			 * save a temporary of rho
			 * --------------------------------------------------------------------
			 */
            rho0 = rho;
            rho = 0.0;
        }

        // LOOP 1
#pragma omp for nowait
        for (j = 0; j < lastrow - firstrow + 1; j++) {
            suml = 0.0;
            for (k = rowstr[j]; k < rowstr[j + 1]; k++) {
                // std::cout << "suml(" << suml << ") += a[" << k << "](" << a[k] << ") * p[colidx[" << k << "](" << colidx[k] << ")] (" << p[colidx[k]] << ")" << std::endl;
                suml += a[k] * p[colidx[k]];
            }
            q[j] = suml;
            // std::cout << "q[" << j << "] = " << suml << std::endl;
        }

        /*
		 * --------------------------------------------------------------------
		 * obtain p.q
		 * --------------------------------------------------------------------
		 */

#pragma omp for reduction(+ : d)
        for (j = 0; j < lastcol - firstcol + 1; j++) {
            d += p[j] * q[j];
        }

        /*
		 * --------------------------------------------------------------------
		 * obtain alpha = rho / (p.q)
		 * -------------------------------------------------------------------
		 */
        alpha = rho0 / d;
        // std::cout << "alpha (" << alpha << ") = rho0 (" << rho0 << ") / d (" << d << ")" << std::endl;

        /*
		 * ---------------------------------------------------------------------
		 * obtain z = z + alpha*p
		 * and    r = r - alpha*q
		 * ---------------------------------------------------------------------
		 */

#pragma omp for reduction(+ : rho)
        for (j = 0; j < lastcol - firstcol + 1; j++) {
            z[j] += alpha * p[j];
            r[j] -= alpha * q[j];

            /*
			 * ---------------------------------------------------------------------
			 * rho = r.r
			 * now, obtain the norm of r: first, sum squares of r elements locally...
			 * ---------------------------------------------------------------------
			 */
            rho += r[j] * r[j];
        }

        /*
		 * ---------------------------------------------------------------------
		 * obtain beta
		 * ---------------------------------------------------------------------
		 */
        beta = rho / rho0;
        // std::cout << "beta (" << beta << ") = rho (" << rho << ") / rho0 (" << rho0 << ")" << std::endl;

/*
		 * ---------------------------------------------------------------------
		 * p = r + beta*p
		 * ---------------------------------------------------------------------
		 */
#pragma omp for
        for (j = 0; j < lastcol - firstcol + 1; j++) {
            p[j] = r[j] + beta * p[j];
        }
    } /* end of do cgit=1, cgitmax */

    /*
	 * ---------------------------------------------------------------------
	 * compute residual norm explicitly: ||r|| = ||x - A.z||
	 * first, form A.z
	 * the partition submatrix-vector multiply
	 * ---------------------------------------------------------------------
	 */
    // LOOP 2
#pragma omp for nowait
    for (j = 0; j < lastrow - firstrow + 1; j++) {
        suml = 0.0;
        for (k = rowstr[j]; k < rowstr[j + 1]; k++) {
            suml += a[k] * z[colidx[k]];
        }
        r[j] = suml;
        // std::cout << "r[" << j << "] = " << suml << std::endl;
    }

/*
	 * ---------------------------------------------------------------------
	 * at this point, r contains A.z
	 * ---------------------------------------------------------------------
	 */
#pragma omp for reduction(+ : sum)
    for (j = 0; j < lastcol - firstcol + 1; j++) {
        suml = x[j] - r[j];
        sum += suml * suml;
    }

#pragma omp single
    *rnorm = sqrt(sum);

#ifdef GEM5
#pragma omp single
    {
        clear_mem_region();
    }
#endif
}

/*
 * ---------------------------------------------------------------------
 * scale a double precision number x in (0,1) by a power of 2 and chop it
 * ---------------------------------------------------------------------
 */
static int icnvrt(double x, int ipwr2) {
    return (int)(ipwr2 * x);
}

/*
 * ---------------------------------------------------------------------
 * generate the test problem for benchmark 6
 * makea generates a sparse matrix with a
 * prescribed sparsity distribution
 *
 * parameter    type        usage
 *
 * input
 *
 * n            i           number of cols/rows of matrix
 * nz           i           nonzeros as declared array size
 * rcond        r*8         condition number
 * shift        r*8         main diagonal shift
 *
 * output
 *
 * a            r*8         array for nonzeros
 * colidx       i           col indices
 * rowstr       i           row pointers
 *
 * workspace
 *
 * iv, arow, acol i
 * aelt           r*8
 * ---------------------------------------------------------------------
 */
#ifndef USE_DATA_FROM_FILE
static void makea(int n, int nz, float a[], int colidx[], int rowstr[], int firstrow, int lastrow, int firstcol, int lastcol, int arow[], int acol[][NONZER + 1], float aelt[][NONZER + 1], int iv[]) {
    int iouter, ivelt, nzv, nn1;
    int ivc[NONZER + 1];
    double vc[NONZER + 1];

    /*
	 * --------------------------------------------------------------------
	 * nonzer is approximately  (int(sqrt(nnza /n)));
	 * --------------------------------------------------------------------
	 * nn1 is the smallest power of two not less than n
	 * --------------------------------------------------------------------
	 */
    nn1 = 1;
    do {
        nn1 = 2 * nn1;
    } while (nn1 < n);

    /*
	 * -------------------------------------------------------------------
	 * generate nonzero positions and save for the use in sparse
	 * -------------------------------------------------------------------
	 */
    for (iouter = 0; iouter < n; iouter++) {
        nzv = NONZER;
        sprnvc(n, nzv, nn1, vc, ivc);
        vecset(n, vc, ivc, &nzv, iouter + 1, 0.5);
        arow[iouter] = nzv;
        for (ivelt = 0; ivelt < nzv; ivelt++) {
            acol[iouter][ivelt] = ivc[ivelt] - 1;
            aelt[iouter][ivelt] = vc[ivelt];
        }
    }

    /*
	 * ---------------------------------------------------------------------
	 * ... make the sparse matrix from list of elements with duplicates
	 * (iv is used as  workspace)
	 * ---------------------------------------------------------------------
	 */
    sparse(a, colidx, rowstr, n, nz, NONZER, arow, acol, aelt, firstrow, lastrow, iv, RCOND, SHIFT);
}
#endif

/*
 * ---------------------------------------------------------------------
 * rows range from firstrow to lastrow
 * the rowstr pointers are defined for nrows = lastrow-firstrow+1 values
 * ---------------------------------------------------------------------
 */
static void sparse(float a[], int colidx[], int rowstr[], int n, int nz, int nozer, int arow[], int acol[][NONZER + 1], float aelt[][NONZER + 1], int firstrow, int lastrow, int nzloc[], double rcond, double shift) {
    int nrows;

    /*
	 * ---------------------------------------------------
	 * generate a sparse matrix from a list of
	 * [col, row, element] tri
	 * ---------------------------------------------------
	 */
    int i, j, j1, j2, nza, k, kk, nzrow, jcol;
    double size, scale, ratio, va;
    boolean goto_40;

    /*
	 * --------------------------------------------------------------------
	 * how many rows of result
	 * --------------------------------------------------------------------
	 */
    nrows = lastrow - firstrow + 1;

    /*
	 * --------------------------------------------------------------------
	 * ...count the number of triples in each row
	 * --------------------------------------------------------------------
	 */
    for (j = 0; j < nrows + 1; j++) {
        rowstr[j] = 0;
    }
    for (i = 0; i < n; i++) {
        for (nza = 0; nza < arow[i]; nza++) {
            j = acol[i][nza] + 1;
            rowstr[j] = rowstr[j] + arow[i];
        }
    }
    rowstr[0] = 0;
    for (j = 1; j < nrows + 1; j++) {
        rowstr[j] = rowstr[j] + rowstr[j - 1];
    }
    nza = rowstr[nrows] - 1;

    /*
	 * ---------------------------------------------------------------------
	 * ... rowstr(j) now is the location of the first nonzero
	 * of row j of a
	 * ---------------------------------------------------------------------
	 */
    if (nza > nz) {
        std::cout << "Space for matrix elements exceeded in sparse" << std::endl;
        std::cout << "nza, nzmax = " << nza << ", " << nz << std::endl;
        exit(-1);
    }

    /*
	 * ---------------------------------------------------------------------
	 * ... preload data pages
	 * ---------------------------------------------------------------------
	 */
    for (j = 0; j < nrows; j++) {
        for (k = rowstr[j]; k < rowstr[j + 1]; k++) {
            a[k] = 0.0;
            colidx[k] = -1;
        }
        nzloc[j] = 0;
    }

    /*
	 * ---------------------------------------------------------------------
	 * ... generate actual values by summing duplicates
	 * ---------------------------------------------------------------------
	 */
    size = 1.0;
    ratio = pow(rcond, (1.0 / (double)(n)));
    for (i = 0; i < n; i++) {
        for (nza = 0; nza < arow[i]; nza++) {
            j = acol[i][nza];

            scale = size * aelt[i][nza];
            for (nzrow = 0; nzrow < arow[i]; nzrow++) {
                jcol = acol[i][nzrow];
                va = aelt[i][nzrow] * scale;

                /*
				 * --------------------------------------------------------------------
				 * ... add the identity * rcond to the generated matrix to bound
				 * the smallest eigenvalue from below by rcond
				 * --------------------------------------------------------------------
				 */
                if (jcol == j && j == i) {
                    va = va + rcond - shift;
                }

                goto_40 = FALSE;
                for (k = rowstr[j]; k < rowstr[j + 1]; k++) {
                    if (colidx[k] > jcol) {
                        /*
						 * ----------------------------------------------------------------
						 * ... insert colidx here orderly
						 * ----------------------------------------------------------------
						 */
                        for (kk = rowstr[j + 1] - 2; kk >= k; kk--) {
                            if (colidx[kk] > -1) {
                                a[kk + 1] = a[kk];
                                colidx[kk + 1] = colidx[kk];
                            }
                        }
                        colidx[k] = jcol;
                        a[k] = 0.0;
                        goto_40 = TRUE;
                        break;
                    } else if (colidx[k] == -1) {
                        colidx[k] = jcol;
                        goto_40 = TRUE;
                        break;
                    } else if (colidx[k] == jcol) {
                        /*
						 * --------------------------------------------------------------
						 * ... mark the duplicated entry
						 * -------------------------------------------------------------
						 */
                        nzloc[j] = nzloc[j] + 1;
                        goto_40 = TRUE;
                        break;
                    }
                }
                if (goto_40 == FALSE) {
                    std::cout << "internal error in sparse: i=" << i << std::endl;
                    exit(-1);
                }
                a[k] = a[k] + va;
            }
        }
        size = size * ratio;
    }

    /*
	 * ---------------------------------------------------------------------
	 * ... remove empty entries and generate final results
	 * ---------------------------------------------------------------------
	 */
    for (j = 1; j < nrows; j++) {
        nzloc[j] = nzloc[j] + nzloc[j - 1];
    }

    for (j = 0; j < nrows; j++) {
        if (j > 0) {
            j1 = rowstr[j] - nzloc[j - 1];
        } else {
            j1 = 0;
        }
        j2 = rowstr[j + 1] - nzloc[j];
        nza = rowstr[j];
        for (k = j1; k < j2; k++) {
            a[k] = a[nza];
            colidx[k] = colidx[nza];
            nza = nza + 1;
        }
    }
    for (j = 1; j < nrows + 1; j++) {
        rowstr[j] = rowstr[j] - nzloc[j - 1];
    }
    nza = rowstr[nrows] - 1;
}

/*
 * ---------------------------------------------------------------------
 * generate a sparse n-vector (v, iv)
 * having nzv nonzeros
 *
 * mark(i) is set to 1 if position i is nonzero.
 * mark is all zero on entry and is reset to all zero before exit
 * this corrects a performance bug found by John G. Lewis, caused by
 * reinitialization of mark on every one of the n calls to sprnvc
 * ---------------------------------------------------------------------
 */
static void sprnvc(int n, int nz, int nn1, double v[], int iv[]) {
    int nzv, ii, i;
    double vecelt, vecloc;

    nzv = 0;

    while (nzv < nz) {
        vecelt = randlc(&tran, amult);

        /*
		 * --------------------------------------------------------------------
		 * generate an integer between 1 and n in a portable manner
		 * --------------------------------------------------------------------
		 */
        vecloc = randlc(&tran, amult);
        i = icnvrt(vecloc, nn1) + 1;
        if (i > n) {
            continue;
        }

        /*
		 * --------------------------------------------------------------------
		 * was this integer generated already?
		 * --------------------------------------------------------------------
		 */
        boolean was_gen = FALSE;
        for (ii = 0; ii < nzv; ii++) {
            if (iv[ii] == i) {
                was_gen = TRUE;
                break;
            }
        }
        if (was_gen) {
            continue;
        }
        v[nzv] = vecelt;
        iv[nzv] = i;
        nzv = nzv + 1;
    }
}

/*
 * --------------------------------------------------------------------
 * set ith element of sparse vector (v, iv) with
 * nzv nonzeros to val
 * --------------------------------------------------------------------
 */
static void vecset(int n, double v[], int iv[], int *nzv, int i, double val) {
    int k;
    boolean set;

    set = FALSE;
    for (k = 0; k < *nzv; k++) {
        if (iv[k] == i) {
            v[k] = val;
            set = TRUE;
        }
    }
    if (set == FALSE) {
        v[*nzv] = val;
        iv[*nzv] = i;
        *nzv = *nzv + 1;
    }
}
