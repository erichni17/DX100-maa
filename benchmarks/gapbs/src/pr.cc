// Copyright (c) 2015, The Regents of the University of California (Regents)
// See LICENSE.txt for license details

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <limits>
#include <omp.h>
#include <vector>

#include "MAA.hpp"
#include "benchmark.h"
#include "builder.h"
#include "command_line.h"
#include "graph.h"
#include "pvector.h"

#if !defined(FUNC) && !defined(GEM5) && !defined(GEM5_MAGIC)
#define GEM5
#endif

#ifdef GEM5
#include <gem5/m5ops.h>
#endif

#if defined(FUNC)
#include <MAA_functional.hpp>
#elif defined(GEM5)
#include <MAA_gem5.hpp>
#include <gem5/m5ops.h>
#elif defined(GEM5_MAGIC)
#include "MAA_gem5_magic.hpp"
#endif

int tiles1[NUM_CORES], tiles2[NUM_CORES], tiles3[NUM_CORES], tiles5[NUM_CORES], tilesi[NUM_CORES], tilesj[NUM_CORES];
int regs0[NUM_CORES], regs1[NUM_CORES], regs2[NUM_CORES], regs3[NUM_CORES], regs4[NUM_CORES], regs5[NUM_CORES], last_i_regs[NUM_CORES], last_j_regs[NUM_CORES];

/*
GAP Benchmark Suite
Kernel: PageRank (PR)
Author: Scott Beamer

Will return pagerank scores for all vertices once total change < epsilon

This PR implementation uses the traditional iterative approach. It performs
updates in the pull direction to remove the need for atomics, and it allows
new values to be immediately visible (like Gauss-Seidel method). The prior PR
implementation is still available in src/pr_spmv.cc.
*/

using namespace std;

typedef float ScoreT;
const float kDamp = 0.85;

#ifdef MAA_VIRTUAL_GATHER
alignas(64) static ScoreT virtual_gather_backing[NUM_CORES][TILE_SIZE];
#endif

#ifdef PR_FP_ENABLE
static uint64_t MixFingerprint(uint64_t value) {
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31);
}

static uint64_t UpdateFingerprint(uint64_t hash, uint64_t index,
                                  uint64_t value) {
    hash ^= MixFingerprint((index << 32) ^ value);
    hash = (hash << 19) | (hash >> 45);
    return hash * 0x9e3779b185ebca87ULL;
}

static uint32_t ScoreBits(ScoreT value) {
    uint32_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}
#endif

static inline void PrintScoreFingerprint(const pvector<ScoreT> &scores) {
#ifdef PR_FP_ENABLE
    double sum = 0.0;
    double absum = 0.0;
    double minv = std::numeric_limits<double>::infinity();
    double maxv = -std::numeric_limits<double>::infinity();
    uint64_t raw = 0xcbf29ce484222325ULL;
    uint64_t normalized_q5 = 0x6a09e667f3bcc909ULL;
    uint64_t normalized_q6 = 0x3c6ef372fe94f82bULL;
    uint64_t nonfinite = 0;
    uint64_t unquantizable = 0;
    for (size_t i = 0; i < scores.size(); i++) {
        double v = static_cast<double>(scores[i]);
        raw = UpdateFingerprint(raw, i, ScoreBits(scores[i]));
        if (std::isfinite(v)) {
            const double normalized = v * scores.size();
            const double max_normalized =
                static_cast<double>(std::numeric_limits<int64_t>::max()) /
                1.0e6;
            if (std::fabs(normalized) <= max_normalized) {
                normalized_q5 = UpdateFingerprint(
                    normalized_q5, i,
                    static_cast<uint64_t>(
                        std::llround(normalized * 1.0e5)));
                normalized_q6 = UpdateFingerprint(
                    normalized_q6, i,
                    static_cast<uint64_t>(
                        std::llround(normalized * 1.0e6)));
            } else {
                unquantizable++;
            }
        } else {
            nonfinite++;
        }
        sum += v;
        absum += std::fabs(v);
        minv = std::min(minv, v);
        maxv = std::max(maxv, v);
    }
    std::cout << std::scientific << std::setprecision(17)
              << "PR_FP"
              << " elements=" << scores.size()
              << " raw=" << std::hex << raw
              << " normalized_q5=" << normalized_q5
              << " normalized_q6=" << normalized_q6
              << std::dec
              << " sum=" << sum
              << " absum=" << absum
              << " min=" << minv
              << " max=" << maxv
              << " nonfinite=" << nonfinite
              << " unquantizable=" << unquantizable
              << std::defaultfloat << std::endl;
#endif
}

pvector<ScoreT> PageRankPullGS(const Graph &g, int max_iters, double epsilon = 0, bool logging_enabled = false) {
    int num_nodes = g.num_nodes();
    int num_edges = g.num_edges();
    std::cout << "PR: num_nodes: " << num_nodes << ", num_edges: " << num_edges << ", edge/node: " << (double)num_nodes / (double)num_edges << std::endl;

    const ScoreT init_score = 1.0f / num_nodes;
    const ScoreT base_score = (1.0f - kDamp) / num_nodes;
    pvector<ScoreT> scores(num_nodes, init_score);
    pvector<ScoreT> *outgoing_contribs[2];
    outgoing_contribs[0] = new pvector<ScoreT>(num_nodes);
    outgoing_contribs[1] = new pvector<ScoreT>(num_nodes);
    pvector<SGOffset> VertexOffsets = g.VertexOffsets(true);

    alloc_MAA();
    init_MAA();

#ifdef GEM5
#pragma omp parallel
    {
#pragma omp master
        {
            std::cout << "ROI started: " << omp_get_num_threads() << " threads" << std::endl;
            m5_work_begin(0, 0);
            m5_reset_stats(0, 0);
        }
    }
#endif

#pragma omp parallel for
    for (NodeID n = 0; n < num_nodes; n++)
        (*outgoing_contribs[0])[n] = init_score / g.out_degree(n);
    for (int iter = 0; iter < max_iters; iter++) {
        double error = 0;
        pvector<ScoreT> &curr_contrib = *outgoing_contribs[iter % 2];
        pvector<ScoreT> &next_contrib = *outgoing_contribs[(iter + 1) % 2];
#ifdef GEM5
        clear_mem_region();
        add_mem_region(VertexOffsets.beginp(), VertexOffsets.endp());                // 6
        add_mem_region(curr_contrib.beginp(), curr_contrib.endp());                  // 7
        add_mem_region(next_contrib.beginp(), next_contrib.endp());                  // 8
        add_mem_region(g.in_neighbors_, &g.in_neighbors_[VertexOffsets[num_nodes]]); // 9
        add_mem_region(scores.beginp(), scores.endp());                              // 10
#endif
#pragma omp parallel for reduction(+ : error) schedule(dynamic, 16384)
        for (NodeID u = 0; u < num_nodes; u++) {
            ScoreT incoming_total = 0;
            for (int j = VertexOffsets[u]; j < VertexOffsets[u + 1]; j++) {
                incoming_total += curr_contrib[g.in_neighbors_[j]];
            }
            ScoreT old_score = scores[u];
            scores[u] = base_score + kDamp * incoming_total;
            error += fabs(scores[u] - old_score);
            next_contrib[u] = scores[u] / g.out_degree(u);
        }
#ifdef GEM5
        clear_mem_region();
#endif
        if (logging_enabled)
            PrintStep(iter, error);
        if (error < epsilon)
            break;
    }
#ifdef GEM5
    clear_mem_region();
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);
    PrintScoreFingerprint(scores);
    std::cout << "ROI End!!!" << std::endl;
    m5_exit(0);
#endif
    return scores;
}

pvector<ScoreT> PageRankPullMAA(const Graph &g, int max_iters, double epsilon = 0, bool logging_enabled = false) {
    int num_nodes = g.num_nodes();
    int num_edges = g.num_edges();
    std::cout << "PR: num_nodes: " << num_nodes << ", num_edges: " << num_edges << ", edge/node: " << (double)num_nodes / (double)num_edges << std::endl;

    const ScoreT init_score = 1.0f / num_nodes;
    const ScoreT base_score = (1.0f - kDamp) / num_nodes;
    pvector<ScoreT> scores(num_nodes, init_score);
    pvector<ScoreT> *outgoing_contribs[2];
    outgoing_contribs[0] = new pvector<ScoreT>(num_nodes);
    outgoing_contribs[1] = new pvector<ScoreT>(num_nodes);
    pvector<SGOffset> VertexOffsets = g.VertexOffsets(true);

    ScoreT *incoming_totals = new ScoreT[num_nodes];
    memset(incoming_totals, 0, num_nodes * sizeof(ScoreT));

    alloc_MAA();
    init_MAA();

#pragma omp parallel
    {
#pragma omp critical
        {
            int tid = omp_get_thread_num();
            tiles1[tid] = get_new_tile<int>();
            tiles2[tid] = get_new_tile<int>();
            tiles3[tid] = get_new_tile<int>();
            tiles5[tid] = get_new_tile<int>();
            tilesi[tid] = get_new_tile<int>();
            tilesj[tid] = get_new_tile<int>();
            regs0[tid] = get_new_reg<int>();
            regs1[tid] = get_new_reg<int>();
            regs2[tid] = get_new_reg<int>();
            regs3[tid] = get_new_reg<int>();
            regs4[tid] = get_new_reg<int>();
            regs5[tid] = get_new_reg<int>();
            last_i_regs[tid] = get_new_reg<int>();
            last_j_regs[tid] = get_new_reg<int>();
        }
    }
#ifdef GEM5
#pragma omp parallel
    {
#pragma omp master
        {
            std::cout << "ROI started: " << omp_get_num_threads() << " threads" << std::endl;
            m5_work_begin(0, 0);
            m5_reset_stats(0, 0);
        }
    }
#endif

#pragma omp parallel for
    for (NodeID n = 0; n < num_nodes; n++)
        (*outgoing_contribs[0])[n] = init_score / g.out_degree(n);

    for (int iter = 0; iter < max_iters; iter++) {
        double error = 0;
        pvector<ScoreT> &curr_contrib = *outgoing_contribs[iter % 2];
        pvector<ScoreT> &next_contrib = *outgoing_contribs[(iter + 1) % 2];
#ifdef GEM5
        clear_mem_region();
        add_mem_region(VertexOffsets.beginp(), VertexOffsets.endp());                // 6
        add_mem_region(curr_contrib.beginp(), curr_contrib.endp());                  // 7
        add_mem_region(next_contrib.beginp(), next_contrib.endp());                  // 8
        add_mem_region(g.in_neighbors_, &g.in_neighbors_[VertexOffsets[num_nodes]]); // 9
        add_mem_region(scores.beginp(), scores.endp());                              // 10
        add_mem_region(incoming_totals, &incoming_totals[num_nodes]);                // 11
#ifdef MAA_VIRTUAL_GATHER
        add_mem_region(&virtual_gather_backing[0][0],
                       &virtual_gather_backing[NUM_CORES - 1][TILE_SIZE]);
#endif
#endif
#pragma omp parallel
        {
            int tilelb, tileub, tile3, tile5, tilei, tilej;
            int reg0, reg1, regOne, j_start_reg, j_end_reg;
            int last_i_reg, last_j_reg;
#ifdef MAA_VIRTUAL_GATHER
            int backing_start_reg;
#endif
            int tid = omp_get_thread_num();
            tilelb = tiles1[tid];
            tileub = tiles2[tid];
            tile3 = tiles3[tid];
            tile5 = tiles5[tid];
            tilei = tilesi[tid];
            tilej = tilesj[tid];
            reg0 = regs0[tid];
            reg1 = regs1[tid];
            regOne = regs2[tid];
            j_start_reg = regs3[tid];
            j_end_reg = regs4[tid];
#ifdef MAA_VIRTUAL_GATHER
            backing_start_reg = regs5[tid];
#endif
            last_i_reg = last_i_regs[tid];
            last_j_reg = last_j_regs[tid];

            maa_const<int>(1, regOne);
            maa_const<int>(num_nodes, reg1);
#ifdef MAA_VIRTUAL_GATHER
            maa_const<int>(0, backing_start_reg);
#endif

#pragma omp for schedule(dynamic) reduction(+ : error)
            for (int uidx = 0; uidx < num_nodes; uidx += TILE_SIZE) {
                maa_const<int>(uidx, reg0);
                // step1 load upper bounds of VertexOffsetsData using u
                maa_stream_load<int>(VertexOffsets.start_, reg0, reg1, regOne, tilelb);
                maa_stream_load<int>(&VertexOffsets.start_[1], reg0, reg1, regOne, tileub);
                // step2 do while using range loop api
                maa_const<int>(0, last_i_reg);
                maa_const<int>(-1, last_j_reg);
                ScoreT *scores_ptr = scores.data() + uidx;
                ScoreT *next_contrib_ptr = next_contrib.data() + uidx;
                ScoreT *incoming_total = incoming_totals + uidx;
                // step3 do while loop
                int j_max = VertexOffsets.start_[min(uidx + TILE_SIZE, num_nodes)];
                maa_const(j_max, j_end_reg);
                for (int j_base = VertexOffsets.start_[uidx]; j_base < j_max; j_base += TILE_SIZE) {
                    maa_const(j_base, j_start_reg);
                    maa_range_loop<int>(last_i_reg, last_j_reg, tilelb, tileub, regOne, tilei, tilej);
                    // first load g.in_neighbors_[j]
                    maa_stream_load<NodeID>(g.in_neighbors_, j_start_reg, j_end_reg, regOne, tile3);
                    // Transfer tilei, tile3
                    // then load curr_contrib[g.in_neighbors_[j]]
#ifdef MAA_VIRTUAL_GATHER
                    const int gather_size =
                        std::min(j_max - j_base, TILE_SIZE);
                    maa_indirect_load_virtual<ScoreT>(
                        curr_contrib.data(), tile3, tile5,
                        virtual_gather_backing[tid]);
                    wait_ready(tile5);
                    maa_const(gather_size, reg0);
                    maa_stream_load<ScoreT>(virtual_gather_backing[tid],
                                            backing_start_reg, reg0, regOne,
                                            tile5);
#else
                    maa_indirect_load<ScoreT>(curr_contrib.data(), tile3,
                                              tile5);
#endif
                    // then do rmw for incoming_total[itile]
                    maa_indirect_rmw_vector<ScoreT>(incoming_total, tilei, tile5, Operation_t::ADD_OP);
                    wait_ready(tile3);
                }
                wait_ready(tile5);
#pragma omp simd simdlen(4)
                for (NodeID u = 0; u < min(num_nodes - uidx, TILE_SIZE); u++) {
                    ScoreT old_score = scores_ptr[u];
                    scores_ptr[u] = base_score + kDamp * incoming_total[u];
                    error += fabs(scores_ptr[u] - old_score);
                    next_contrib_ptr[u] = scores_ptr[u] / g.out_degree(u + uidx);
                }
            }
        }
#ifdef GEM5
        clear_mem_region();
#endif
        if (logging_enabled)
            PrintStep(iter, error);
        if (error < epsilon)
            break;
    }
#ifdef GEM5
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);
    PrintScoreFingerprint(scores);
    std::cout << "ROI End!!!" << std::endl;
    m5_exit(0);
#endif
    return scores;
}

void PrintTopScores(const Graph &g, const pvector<ScoreT> &scores) {
    vector<pair<NodeID, ScoreT>> score_pairs(g.num_nodes());
    for (NodeID n = 0; n < g.num_nodes(); n++) {
        score_pairs[n] = make_pair(n, scores[n]);
    }
    int k = 5;
    vector<pair<ScoreT, NodeID>> top_k = TopK(score_pairs, k);
    for (auto kvp : top_k)
        cout << kvp.second << ":" << kvp.first << endl;
}

// Verifies by asserting a single serial iteration in push direction has
//   error < target_error
bool PRVerifier(const Graph &g, const pvector<ScoreT> &scores,
                double target_error) {
    const ScoreT base_score = (1.0f - kDamp) / g.num_nodes();
    pvector<ScoreT> incoming_sums(g.num_nodes(), 0);
    double error = 0;
    for (NodeID u : g.vertices()) {
        ScoreT outgoing_contrib = scores[u] / g.out_degree(u);
        for (NodeID v : g.out_neigh(u))
            incoming_sums[v] += outgoing_contrib;
    }
    for (NodeID n : g.vertices()) {
        error += fabs(base_score + kDamp * incoming_sums[n] - scores[n]);
        incoming_sums[n] = 0;
    }
    PrintTime("Total Error", error);
    return error < target_error;
}

int main(int argc, char *argv[]) {
    CLPageRank cli(argc, argv, "pagerank", 1e-4, 1);
    if (!cli.ParseArgs())
        return -1;
    Builder b(cli);
    Graph g = b.MakeGraph();
#ifdef GEM5
    std::cout << "Fake Checkpoint started" << std::endl;
    m5_checkpoint(0, 0);
    std::cout << "Fake Checkpoint ended" << std::endl;
#endif
    auto PRBound = [&cli](const Graph &g) {
#ifdef MAA
        return PageRankPullMAA(g, cli.max_iters(), cli.tolerance(), cli.logging_en());
#else
        return PageRankPullGS(g, cli.max_iters(), cli.tolerance(), cli.logging_en());
#endif
    };
    auto VerifierBound = [&cli](const Graph &g, const pvector<ScoreT> &scores) {
        return PRVerifier(g, scores, cli.tolerance());
    };
    BenchmarkKernel(cli, g, PRBound, PrintTopScores, VerifierBound);
    return 0;
}
