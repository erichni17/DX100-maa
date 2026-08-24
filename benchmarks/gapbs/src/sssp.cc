// Copyright (c) 2015, The Regents of the University of California (Regents)
// See LICENSE.txt for license details

#include <omp.h>

#include <algorithm>
#include <atomic>
#include <cinttypes>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <queue>
#include <vector>

#include "MAA.hpp"
#include "benchmark.h"
#include "builder.h"
#include "command_line.h"
#include "graph.h"
#include "platform_atomics.h"
#include "pvector.h"
#include "timer.h"

#if !defined(FUNC) && !defined(GEM5) && !defined(GEM5_MAGIC)
#define FUNC
#endif

#if defined(FUNC)
#include <MAA_functional.hpp>
#elif defined(GEM5)
#include <MAA_gem5.hpp>
#include <gem5/m5ops.h>
#elif defined(GEM5_MAGIC)
#include "MAA_gem5_magic.hpp"
#endif
#include <MAA_utility.hpp>
/*
GAP Benchmark Suite
Kernel: Single-source Shortest Paths (SSSP)
Author: Scott Beamer, Yunming Zhang

Returns array of distances for all vertices from given source vertex

This SSSP implementation makes use of the ∆-stepping algorithm [1]. The type
used for weights and distances (WeightT) is typedefined in benchmark.h. The
delta parameter (-d) should be set for each input graph. This implementation
incorporates a new bucket fusion optimization [2] that significantly reduces
the number of iterations (& barriers) needed.

The bins of width delta are actually all thread-local and of type std::vector,
so they can grow but are otherwise capacity-proportional. Each iteration is
done in two phases separated by barriers. In the first phase, the current
shared bin is processed by all threads. As they find vertices whose distance
they are able to improve, they add them to their thread-local bins. During this
phase, each thread also votes on what the next bin should be (smallest
non-empty bin). In the next phase, each thread copies its selected
thread-local bin into the shared bin.

Once a vertex is added to a bin, it is not removed, even if its distance is
later updated and, it now appears in a lower bin. We find ignoring vertices if
their distance is less than the min distance for the current bin removes
enough redundant work to be faster than removing the vertex from older bins.

The bucket fusion optimization [2] executes the next thread-local bin in
the same iteration if the vertices in the next thread-local bin have the
same priority as those in the current shared bin. This optimization greatly
reduces the number of iterations needed without violating the priority-based
execution order, leading to significant speedup on large diameter road networks.

[1] Ulrich Meyer and Peter Sanders. "δ-stepping: a parallelizable shortest path
    algorithm." Journal of Algorithms, 49(1):114–152, 2003.

[2] Yunming Zhang, Ajay Brahmakshatriya, Xinyi Chen, Laxman Dhulipala,
    Shoaib Kamil, Saman Amarasinghe, and Julian Shun. "Optimizing ordered graph
    algorithms with GraphIt." The 18th International Symposium on Code Generation
    and Optimization (CGO), pages 158-170, 2020.
*/

using namespace std;

const WeightT kDistInf = numeric_limits<WeightT>::max() / 2;
const size_t kMaxBin = numeric_limits<size_t>::max() / 2;
const size_t kBinSizeThreshold = 1000;

#ifdef SSSP_OLD_RESULT_HYBRID
#if TILE_SIZE != 16384 || MAA_CONSUMER_TILE_SIZE != 4096
#error "SSSP old-result hybrid requires 16K logical / 4K physical geometry"
#endif
static_assert(sizeof(WeightT) == sizeof(float),
              "SSSP old-result bit-order proof requires 32-bit distances");
static_assert(numeric_limits<float>::is_iec559,
              "SSSP old-result bit-order proof requires IEEE-754 FP32");

constexpr size_t kSsspLogicalWords = 16 * 1024;
constexpr size_t kSsspPhysicalWords = 4 * 1024;
constexpr size_t kSsspLogicalBytes = kSsspLogicalWords * sizeof(uint32_t);

// These are ordinary aligned coherent guest spans.  They are deliberately not
// hidden logical SPD payloads: four response-bearing physical-tile
// publications fill index/value pages, the predicate span is immutable, and
// the old-result span is visible only after the descriptor completion token.
alignas(kSsspLogicalBytes) static uint32_t
    sssp_hybrid_indices[NUM_CORES][kSsspLogicalWords];
alignas(kSsspLogicalBytes) static WeightT
    sssp_hybrid_values[NUM_CORES][kSsspLogicalWords];
alignas(kSsspLogicalBytes) static uint32_t
    sssp_hybrid_predicates[NUM_CORES][kSsspLogicalWords];
alignas(kSsspLogicalBytes) static WeightT
    sssp_hybrid_old_results[NUM_CORES][kSsspLogicalWords];

static uint64_t sssp_hybrid_eligible_windows[NUM_CORES] = {};
static uint64_t sssp_hybrid_routed_windows[NUM_CORES] = {};
static uint64_t sssp_hybrid_index_publish_pages[NUM_CORES] = {};
static uint64_t sssp_hybrid_value_publish_pages[NUM_CORES] = {};
static uint64_t sssp_hybrid_old_result_words[NUM_CORES] = {};
static uint64_t sssp_hybrid_legacy_words[NUM_CORES] = {};
static uint32_t sssp_hybrid_publish_generations[NUM_CORES] = {};

static void
PublishSsspHybridPage(int tid, size_t logical_page, int index_tile,
                      int value_tile, int index_completion_tile,
                      int value_completion_tile, int page_reg, int offset_reg,
                      int generation_reg) {
    if (logical_page >= 4)
        abort();
    const uint32_t page = static_cast<uint32_t>(logical_page);
    const uint32_t offset = page * kSsspPhysicalWords;
    maa_const<uint32_t>(page, page_reg);
    maa_const<uint32_t>(offset, offset_reg);

    const uint32_t index_generation = ++sssp_hybrid_publish_generations[tid];
    if (index_generation == 0)
        abort();
    maa_const<uint32_t>(index_generation, generation_reg);
    maa_publish_spd_page_logical16_response_bearing<uint32_t>(
        sssp_hybrid_indices[tid], page, index_tile, index_completion_tile,
        page_reg, offset_reg, generation_reg);
    wait_ready(index_completion_tile);
    sssp_hybrid_index_publish_pages[tid]++;

    const uint32_t value_generation = ++sssp_hybrid_publish_generations[tid];
    if (value_generation == 0)
        abort();
    maa_const<uint32_t>(value_generation, generation_reg);
    maa_publish_spd_page_logical16_response_bearing<WeightT>(
        sssp_hybrid_values[tid], page, value_tile, value_completion_tile,
        page_reg, offset_reg, generation_reg);
    wait_ready(value_completion_tile);
    sssp_hybrid_value_publish_pages[tid]++;
}

static void
RunSsspHybridWindow(int tid, WeightT *dist, int num_nodes, WeightT delta,
                    vector<vector<NodeID>> &local_bins, int min_reg,
                    int max_reg, int stride_reg, int completion_tile) {
    atomic_thread_fence(memory_order_seq_cst);
    for (size_t lane = 0; lane < kSsspLogicalWords; ++lane) {
        if (sssp_hybrid_predicates[tid][lane] != 1 ||
            sssp_hybrid_indices[tid][lane] >=
                static_cast<uint32_t>(num_nodes) ||
            sssp_hybrid_values[tid][lane] < 0 ||
            sssp_hybrid_values[tid][lane] > kDistInf)
            abort();
    }

    maa_const<int>(0, min_reg);
    maa_const<int>(kSsspLogicalWords, max_reg);
    maa_const<int>(1, stride_reg);
    // For bit patterns [0, kDistInf], unsigned integer order and positive
    // finite IEEE-754 order are identical.  The iteration admission proof
    // below establishes those bounds, unique destinations, and source
    // immutability before this FP32-tagged MIN is allowed to run.
    maa_indirect_rmw_vector_soa_jit_old_result(
        reinterpret_cast<float *>(dist), sssp_hybrid_indices[tid],
        reinterpret_cast<float *>(sssp_hybrid_values[tid]),
        sssp_hybrid_predicates[tid],
        reinterpret_cast<float *>(sssp_hybrid_old_results[tid]), min_reg,
        max_reg, stride_reg, completion_tile, Operation_t::MIN_OP);
    wait_ready(completion_tile);
    atomic_thread_fence(memory_order_seq_cst);

    // Global destination uniqueness reduces the legacy post-RMW test
    //   candidate == final && old > final
    // exactly to old > candidate.  Scan in original logical offset order so
    // local-bin insertion and frontier order match the four legacy pages.
    for (size_t lane = 0; lane < kSsspLogicalWords; ++lane) {
        const WeightT candidate = sssp_hybrid_values[tid][lane];
        if (sssp_hybrid_old_results[tid][lane] > candidate) {
            const NodeID destination =
                static_cast<NodeID>(sssp_hybrid_indices[tid][lane]);
            const size_t dest_bin = candidate / delta;
            if (dest_bin >= local_bins.size())
                local_bins.resize(dest_bin + 1);
            local_bins[dest_bin].push_back(destination);
        }
    }
    sssp_hybrid_routed_windows[tid]++;
    sssp_hybrid_old_result_words[tid] += kSsspLogicalWords;
}
#endif

#ifdef SSSP_FP_ENABLE
static uint64_t MixFingerprint(uint64_t value) {
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31);
}

static bool PrintSSSPFingerprint(const WGraph &g, NodeID source,
                                 const pvector<WeightT> &dist) {
    uint64_t hash_a = 0xcbf29ce484222325ULL;
    uint64_t hash_b = 0x6a09e667f3bcc909ULL;
    uint64_t distance_sum = 0;
    uint64_t reached = 0;
    WeightT max_distance = 0;
    uint64_t triangle_violations = 0;
    uint64_t missing_predecessors = 0;
    uint64_t nonpositive_weights = 0;
    uint64_t negative_distances = 0;
    vector<uint8_t> has_tight_predecessor(g.num_nodes(), 0);

    if (source >= 0 && source < g.num_nodes())
        has_tight_predecessor[source] = 1;
    for (NodeID u : g.vertices()) {
        uint64_t encoded = static_cast<uint32_t>(dist[u]);
        uint64_t indexed =
            (static_cast<uint64_t>(static_cast<uint32_t>(u)) << 32) |
            encoded;
        hash_a ^= indexed;
        hash_a *= 0x100000001b3ULL;
        hash_b ^= MixFingerprint(indexed);
        hash_b = (hash_b << 17) | (hash_b >> 47);
        hash_b *= 0x9e3779b185ebca87ULL;
        if (dist[u] != kDistInf) {
            reached++;
            if (dist[u] < 0)
                negative_distances++;
            distance_sum += static_cast<uint32_t>(dist[u]);
            max_distance = max(max_distance, dist[u]);
        }
    }

    for (NodeID u : g.vertices()) {
        if (dist[u] == kDistInf)
            continue;
        for (WNode wn : g.out_neigh(u)) {
            if (wn.w <= 0)
                nonpositive_weights++;
            int64_t candidate = static_cast<int64_t>(dist[u]) + wn.w;
            if (dist[wn.v] == kDistInf || candidate < dist[wn.v])
                triangle_violations++;
            if (dist[wn.v] != kDistInf && candidate == dist[wn.v])
                has_tight_predecessor[wn.v] = 1;
        }
    }
    for (NodeID v : g.vertices()) {
        if (dist[v] != kDistInf && !has_tight_predecessor[v])
            missing_predecessors++;
    }

    bool pass = source >= 0 && source < g.num_nodes() && dist[source] == 0 &&
                negative_distances == 0 && nonpositive_weights == 0 &&
                triangle_violations == 0 && missing_predecessors == 0;
    printf("SSSP_FINGERPRINT vertices=%" PRIu64 " reached=%" PRIu64
           " unreachable=%" PRIu64 " distance_sum=%" PRIu64
           " max_distance=%" PRId32 " hash_a=%016" PRIx64
           " hash_b=%016" PRIx64 " triangle_violations=%" PRIu64
           " missing_predecessors=%" PRIu64 " nonpositive_weights=%" PRIu64
           " negative_distances=%" PRIu64 " result=%s\n",
           static_cast<uint64_t>(g.num_nodes()), reached,
           static_cast<uint64_t>(g.num_nodes()) - reached, distance_sum,
           max_distance, hash_a, hash_b, triangle_violations,
           missing_predecessors, nonpositive_weights, negative_distances,
           pass ? "PASS" : "FAIL");
    return pass;
}
#endif

pvector<WeightT> DeltaStepMAA(const WGraph &g, NodeID source, WeightT delta, bool logging_enabled = false) {
    int num_directed_edges = g.num_edges_directed();
    int num_nodes = g.num_nodes();

    std::cout << "SSSP: num_nodes: " << num_nodes << ", num_edges: " << num_directed_edges << ", edge/node: " << (double)num_nodes / (double)num_directed_edges << ", source: " << source << std::endl;
    pvector<WeightT> dist(num_nodes, kDistInf);
    pvector<NodeID> frontier(num_directed_edges);
    pvector<SGOffset> VertexOffsets = g.VertexOffsets();
    dist[source] = 0;
    // two element arrays for double buffering curr=iter&1, next=(iter+1)&1
    size_t shared_indexes[2] = {0, kMaxBin};
    size_t frontier_tails[2] = {1, 0};
    frontier[0] = source;
#ifdef SSSP_OLD_RESULT_HYBRID
    pvector<uint8_t> hybrid_active_sources(num_nodes, 0);
    pvector<uint32_t> hybrid_destination_epochs(num_nodes, 0);
    uint32_t hybrid_epoch = 0;
    bool hybrid_iteration_safe = false;
    for (int core = 0; core < NUM_CORES; ++core) {
        fill(sssp_hybrid_predicates[core],
             sssp_hybrid_predicates[core] + kSsspLogicalWords, 1U);
    }
#endif
    alloc_MAA();
    init_MAA();

#ifdef GEM5
    clear_mem_region();
    add_mem_region(frontier.beginp(), frontier.endp());                            // 6
    add_mem_region(dist.beginp(), dist.endp());                                    // 7
    add_mem_region(VertexOffsets.beginp(), VertexOffsets.endp());                  // 8
    add_mem_region(g.out_neighbors_, &g.out_neighbors_[VertexOffsets[num_nodes]]); // 9
#ifdef SSSP_OLD_RESULT_HYBRID
    add_mem_region(&sssp_hybrid_indices[0][0],
                   &sssp_hybrid_indices[0][0] +
                       NUM_CORES * kSsspLogicalWords);
    add_mem_region(&sssp_hybrid_values[0][0],
                   &sssp_hybrid_values[0][0] +
                       NUM_CORES * kSsspLogicalWords);
    add_mem_region(&sssp_hybrid_predicates[0][0],
                   &sssp_hybrid_predicates[0][0] +
                       NUM_CORES * kSsspLogicalWords);
    add_mem_region(&sssp_hybrid_old_results[0][0],
                   &sssp_hybrid_old_results[0][0] +
                       NUM_CORES * kSsspLogicalWords);
#endif
    std::cout << "ROI started: " << omp_get_num_threads() << " threads"
              << std::endl;
    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
#endif

#ifdef TIMER_ENABLED
    Timer t;
    t.Start();
#endif
#pragma omp parallel
    {
        int tilev, tileu, tile_ub_d, tile_lb_d, tilei, tile1, tileCond, tile2;
        int reg0, reg1, regOne, regTwo, reg2, last_i_reg, last_j_reg;
#ifdef SSSP_OLD_RESULT_HYBRID
        int hybrid_generation_reg;
#endif

#pragma omp critical
        {
            tilev = get_new_tile<int>();
            tileu = get_new_tile<int>();
            tile_ub_d = get_new_tile<int>();
            tile_lb_d = get_new_tile<int>();
            tilei = get_new_tile<int>();
            tile1 = get_new_tile<int>();
            tile2 = get_new_tile<int>();
            tileCond = get_new_tile<int>();
            reg0 = get_new_reg<int>();
            reg1 = get_new_reg<int>();
            reg2 = get_new_reg<int>();
            regOne = get_new_reg<int>(1);
            regTwo = get_new_reg<int>(2);
            last_i_reg = get_new_reg<int>();
            last_j_reg = get_new_reg<int>();
#ifdef SSSP_OLD_RESULT_HYBRID
            hybrid_generation_reg = get_new_reg<uint32_t>();
#endif
        }
        vector<vector<NodeID>> local_bins(0);
        size_t iter = 0;
        int *tilev_ptr = get_cacheable_tile_pointer<int>(tilev);
        int *tile1_ptr = get_cacheable_tile_pointer<int>(tile1);
        int *tilei_ptr = get_cacheable_tile_pointer<int>(tilei);
        while (shared_indexes[iter & 1] != kMaxBin) {
            size_t &curr_bin_index = shared_indexes[iter & 1];
            size_t &next_bin_index = shared_indexes[(iter + 1) & 1];
            size_t &curr_frontier_tail = frontier_tails[iter & 1];
            size_t &next_frontier_tail = frontier_tails[(iter + 1) & 1];
#ifdef SSSP_OLD_RESULT_HYBRID
#pragma omp single
            {
                // A logical window may replace four legacy physical RMWs only
                // if page aggregation cannot change any operand or winner.
                // Prove that once for the whole iteration: every active edge
                // has bounded nonnegative integer operands, destinations are
                // globally unique, and no destination names an active source.
                hybrid_iteration_safe = true;
                fill(hybrid_active_sources.begin(),
                     hybrid_active_sources.end(), 0);
                if (++hybrid_epoch == 0) {
                    fill(hybrid_destination_epochs.begin(),
                         hybrid_destination_epochs.end(), 0);
                    hybrid_epoch = 1;
                }
                int64_t lower_bound = -1;
                if (delta <= 0 ||
                    curr_bin_index >
                        static_cast<size_t>(kDistInf / max(delta, 1))) {
                    hybrid_iteration_safe = false;
                } else {
                    lower_bound = static_cast<int64_t>(delta) *
                        static_cast<int64_t>(curr_bin_index);
                }
                for (size_t pos = 0; pos < curr_frontier_tail; ++pos) {
                    const NodeID u = frontier[pos];
                    if (u < 0 || u >= num_nodes || dist[u] < 0 ||
                        dist[u] > kDistInf) {
                        hybrid_iteration_safe = false;
                        continue;
                    }
                    if (dist[u] >= lower_bound)
                        hybrid_active_sources[u] = 1;
                }
                for (size_t pos = 0; pos < curr_frontier_tail; ++pos) {
                    const NodeID u = frontier[pos];
                    if (u < 0 || u >= num_nodes ||
                        !hybrid_active_sources[u])
                        continue;
                    for (SGOffset edge = VertexOffsets[u];
                         edge < VertexOffsets[u + 1]; ++edge) {
                        const WNode wn = g.out_neighbors_[edge];
                        const int64_t candidate =
                            static_cast<int64_t>(dist[u]) + wn.w;
                        if (wn.v < 0 || wn.v >= num_nodes || wn.w <= 0 ||
                            candidate < 0 || candidate > kDistInf ||
                            dist[wn.v] < 0 || dist[wn.v] > kDistInf ||
                            hybrid_active_sources[wn.v] ||
                            hybrid_destination_epochs[wn.v] == hybrid_epoch) {
                            hybrid_iteration_safe = false;
                        } else {
                            hybrid_destination_epochs[wn.v] = hybrid_epoch;
                        }
                    }
                }
            }
#endif
            if ((int)curr_frontier_tail < NUM_CORES * 1024) {
#pragma omp master
                std::cout << "Starting DeltaStepMAA: " << curr_frontier_tail << " elements (base)" << std::endl;
#pragma omp for
                for (size_t i = 0; i < curr_frontier_tail; i++) {
                    NodeID u = frontier[i];
                    WeightT dist_u = dist[u];
                    if (dist_u >= delta * static_cast<WeightT>(curr_bin_index)) {
                        for (int j = VertexOffsets[u]; j < VertexOffsets[u + 1]; j++) {
                            WNode wn = g.out_neighbors_[j];
                            NodeID v = wn.v;
                            WeightT old_dist = dist[v];
                            WeightT new_dist = dist_u + wn.w;
                            while (new_dist < old_dist) {
                                if (compare_and_swap(dist[v], old_dist, new_dist)) {
                                    size_t dest_bin = new_dist / delta;
                                    if (dest_bin >= local_bins.size())
                                        local_bins.resize(dest_bin + 1);
                                    local_bins[dest_bin].push_back(v);
                                    // printf("Node[%d] = %d\n", v, new_dist);
                                    break;
                                }
                                old_dist = dist[v]; // swap failed, recheck dist update & retry
                            }
                        }
                    }
                } //
            } else {
                const int cft = (int)curr_frontier_tail;
#if TILE_SIZE == 65536
                const int tile_size = cft > NUM_CORES * 65536   ? 65536
                                      : cft > NUM_CORES * 32768 ? 32768
                                      : cft > NUM_CORES * 16384 ? 16384
                                      : cft > NUM_CORES * 8192  ? 8192
                                      : cft > NUM_CORES * 4096  ? 4096
                                      : cft > NUM_CORES * 2048  ? 2048
                                                                : 1024;
#elif TILE_SIZE == 32768
                const int tile_size = cft > NUM_CORES * 32768   ? 32768
                                      : cft > NUM_CORES * 16384 ? 16384
                                      : cft > NUM_CORES * 8192  ? 8192
                                      : cft > NUM_CORES * 4096  ? 4096
                                      : cft > NUM_CORES * 2048  ? 2048
                                                                : 1024;
#elif TILE_SIZE == 16384
                const int tile_size = cft > NUM_CORES * 16384  ? 16384
                                      : cft > NUM_CORES * 8192 ? 8192
                                      : cft > NUM_CORES * 4096 ? 4096
                                      : cft > NUM_CORES * 2048 ? 2048
                                                               : 1024;
#elif TILE_SIZE == 8192
                const int tile_size = cft > NUM_CORES * 8192   ? 8192
                                      : cft > NUM_CORES * 4096 ? 4096
                                      : cft > NUM_CORES * 2048 ? 2048
                                                               : 1024;
#elif TILE_SIZE == 4096
                const int tile_size = cft > NUM_CORES * 4096   ? 4096
                                      : cft > NUM_CORES * 2048 ? 2048
                                                               : 1024;
#elif TILE_SIZE == 2048
                const int tile_size = cft > NUM_CORES * 2048   ? 2048
                                      : cft > NUM_CORES * 1024 ? 1024
                                                               : 512;
#elif TILE_SIZE == 1024
                const int tile_size = cft > NUM_CORES * 1024 ? 1024
                                                             : 512;
#else
                assert(false);
#endif
                maa_const<int>(delta * static_cast<WeightT>(curr_bin_index), reg2);
#pragma omp master
                std::cout << "Starting DeltaStepMAA: " << cft << " elements (maa-" << tile_size << ")" << std::endl;
#pragma omp for
                for (int idx = 0; idx < cft; idx += tile_size) {
#ifdef SSSP_OLD_RESULT_HYBRID
                    const int idx_end = min(cft, idx + tile_size);
                    size_t hybrid_chunk_words = 0;
                    for (int pos = idx; pos < idx_end; ++pos) {
                        const NodeID u = frontier[pos];
                        if (u >= 0 && u < num_nodes &&
                            hybrid_active_sources[u]) {
                            hybrid_chunk_words +=
                                VertexOffsets[u + 1] - VertexOffsets[u];
                        }
                    }
                    const size_t hybrid_route_words =
                        (hybrid_chunk_words / kSsspLogicalWords) *
                        kSsspLogicalWords;
                    const int tid = omp_get_thread_num();
                    sssp_hybrid_eligible_windows[tid] +=
                        hybrid_chunk_words / kSsspLogicalWords;
                    size_t hybrid_observed_words = 0;
#endif
                    maa_const<int>(idx, reg0);
                    maa_const<int>((int)min(cft, idx + tile_size), reg1);
                    // streaming load u
                    maa_stream_load<int>(frontier.data(), reg0, reg1, regOne, tileu);
                    // load dist[u]
                    maa_indirect_load<WeightT>(dist.data(), tileu, tile1);
                    // alu ge on dist[u] and reg2
                    maa_alu_scalar<WeightT>(tile1, reg2, tileCond, Operation_t::GTE_OP);
                    // then load lower and upper bounds of VertexOffsets[u:u+TILE_SIZE] based on tileCond
                    maa_indirect_load<SGOffset>(VertexOffsets.data(), tileu, tile_lb_d, tileCond);
                    maa_indirect_load<SGOffset>(&VertexOffsets.data()[1], tileu, tile_ub_d, tileCond);
                    // do while loop
                    int curr_size = 0;
                    maa_const<int>(0, last_i_reg);
                    maa_const<int>(-1, last_j_reg);
                    do {
                        maa_range_loop<SGOffset>(last_i_reg, last_j_reg, tile_lb_d, tile_ub_d, regOne, tilei, tile1, tileCond);
                        // tile2 would be double of tile1
                        maa_alu_scalar<int>(tile1, regTwo, tile2, Operation_t::MUL_OP);
                        // load g.out_neighbors_ to node v
                        maa_indirect_load<NodeID>((NodeID *)g.out_neighbors_, tile2, tilev);
                        // load w
                        maa_indirect_load<WeightT>(((WeightT *)g.out_neighbors_ + 1), tile2, tileu);
                        // load u to tile0
                        maa_indirect_load<int>(frontier.data() + idx, tilei, tile2);
#pragma omp critical
                        {
                            // load dist[u] to tile1 using tilei
                            maa_indirect_load<WeightT>(dist.data(), tile2, tile1);
                            // do plus on dist[u] and w
                            maa_alu_vector<WeightT>(tile1, tileu, tile2, Operation_t::ADD_OP);
#ifdef SSSP_OLD_RESULT_HYBRID
                            wait_ready(tile2);
                            wait_ready(tilei);
                            curr_size = get_tile_size(tilei);
                            const bool route_page = hybrid_iteration_safe &&
                                hybrid_observed_words < hybrid_route_words;
                            if (route_page) {
                                if (curr_size !=
                                    static_cast<int>(kSsspPhysicalWords) ||
                                    hybrid_observed_words + curr_size >
                                        hybrid_route_words)
                                    abort();
                                const size_t logical_page =
                                    (hybrid_observed_words %
                                     kSsspLogicalWords) /
                                    kSsspPhysicalWords;
                                PublishSsspHybridPage(
                                    tid, logical_page, tilev, tile2, tile1,
                                    tileu, reg0, reg1,
                                    hybrid_generation_reg);
                                hybrid_observed_words += curr_size;
                                if (logical_page == 3) {
                                    RunSsspHybridWindow(
                                        tid, dist.data(), num_nodes, delta,
                                        local_bins, reg0, reg1, regOne, tilei);
                                }
                            }
#endif
#ifdef SSSP_OLD_RESULT_HYBRID
                            if (!route_page) {
#endif
                                // Ordered MIN returns each pre-update value.
                                maa_indirect_rmw_vector<WeightT>(
                                    dist.data(), tilev, tile2,
                                    Operation_t::MIN_OP, -1, tilei);
                                // Reload final distance after every alias.
                                maa_indirect_load<WeightT>(
                                    dist.data(), tilev, tile1);
                                wait_ready(tile1);
                                // new value = final distance
                                maa_alu_vector<WeightT>(
                                    tile2, tile1, tileu,
                                    Operation_t::EQ_OP);
                                // old value > final distance
                                maa_alu_vector<WeightT>(
                                    tilei, tile1, tile2,
                                    Operation_t::GT_OP);
                                // Both tests select the frontier winner.
                                maa_alu_vector<WeightT>(
                                    tile2, tileu, tilei,
                                    Operation_t::AND_OP);
                                wait_ready(tilei);
                                curr_size = get_tile_size(tilei);
                                for (int j = 0; j < curr_size; j++) {
                                    if (tilei_ptr[j]) {
                                        size_t dest_bin =
                                            tile1_ptr[j] / delta;
                                        if (dest_bin >= local_bins.size())
                                            local_bins.resize(dest_bin + 1);
                                        local_bins[dest_bin].push_back(
                                            tilev_ptr[j]);
                                    }
                                }
#ifdef SSSP_OLD_RESULT_HYBRID
                                hybrid_observed_words += curr_size;
                                sssp_hybrid_legacy_words[tid] += curr_size;
#endif
#ifdef SSSP_OLD_RESULT_HYBRID
                            }
#endif
                        }
                    } while (curr_size > 0);
#ifdef SSSP_OLD_RESULT_HYBRID
                    if (hybrid_iteration_safe &&
                        hybrid_observed_words != hybrid_chunk_words)
                        abort();
#endif
                }
            }
            if (curr_bin_index < local_bins.size() &&
                !local_bins[curr_bin_index].empty() &&
                local_bins[curr_bin_index].size() < kBinSizeThreshold) {
                assert(false);
#ifdef GEM5
                m5_exit(0);
#endif
            }
            for (size_t i = curr_bin_index; i < local_bins.size(); i++) {
                if (!local_bins[i].empty()) {
#pragma omp critical
                    next_bin_index = min(next_bin_index, i);
                    break;
                }
            }
#pragma omp barrier
#pragma omp single nowait
            {
#ifdef TIMER_ENABLED
                t.Stop();
                if (logging_enabled)
                    PrintStep(curr_bin_index, t.Millisecs(), curr_frontier_tail);
                t.Start();
#endif
                curr_bin_index = kMaxBin;
                curr_frontier_tail = 0;
            }
            if (next_bin_index < local_bins.size()) {
                size_t copy_start = fetch_and_add(next_frontier_tail, local_bins[next_bin_index].size());
                copy(local_bins[next_bin_index].begin(), local_bins[next_bin_index].end(), frontier.data() + copy_start);
                local_bins[next_bin_index].resize(0);
            }
            iter++;
#pragma omp barrier
        }
#pragma omp single
        if (logging_enabled)
            cout << "took " << iter << " iterations" << endl;
    }

#ifdef GEM5
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);
#ifdef SSSP_OLD_RESULT_HYBRID
    uint64_t eligible_windows = 0;
    uint64_t routed_windows = 0;
    uint64_t index_publish_pages = 0;
    uint64_t value_publish_pages = 0;
    uint64_t old_result_words = 0;
    uint64_t legacy_words = 0;
    for (int core = 0; core < NUM_CORES; ++core) {
        eligible_windows += sssp_hybrid_eligible_windows[core];
        routed_windows += sssp_hybrid_routed_windows[core];
        index_publish_pages += sssp_hybrid_index_publish_pages[core];
        value_publish_pages += sssp_hybrid_value_publish_pages[core];
        old_result_words += sssp_hybrid_old_result_words[core];
        legacy_words += sssp_hybrid_legacy_words[core];
    }
    const bool counts_close = routed_windows <= eligible_windows &&
        index_publish_pages == routed_windows * 4 &&
        value_publish_pages == routed_windows * 4 &&
        old_result_words == routed_windows * kSsspLogicalWords;
    std::cout << "SSSP_OLD_RESULT_HYBRID_TERMINAL treatment=old_result_hybrid"
              << " eligible_windows=" << eligible_windows
              << " routed_windows=" << routed_windows
              << " index_publish_pages=" << index_publish_pages
              << " value_publish_pages=" << value_publish_pages
              << " old_result_words=" << old_result_words
              << " legacy_words=" << legacy_words
              << " logical_reorder_words=" << kSsspLogicalWords
              << " physical_spd_words=" << kSsspPhysicalWords
              << " row_table_slices=32"
              << " predicate_span=coherent_aligned"
              << " old_result_span=coherent_aligned"
              << " host_spd_reads=0 hidden_result_payload_bytes=0"
              << " counts_close=" << (counts_close ? 1 : 0) << std::endl;
    if (!counts_close)
        abort();
#endif
    clear_mem_region();
    std::cout << "ROI End!!!" << std::endl;
#ifdef SSSP_FP_ENABLE
    std::cout << "Validation started" << std::endl;
    PrintSSSPFingerprint(g, source, dist);
    std::cout << "Validation ended" << std::endl;
#endif
    m5_exit(0);
#endif
    return dist;
}

pvector<WeightT> DeltaStep(const WGraph &g, NodeID source, WeightT delta, bool logging_enabled = false) {
    int num_directed_edges = g.num_edges_directed();
    int num_nodes = g.num_nodes();

    std::cout << "SSSP: num_nodes: " << num_nodes << ", num_edges: " << num_directed_edges << ", edge/node: " << (double)num_nodes / (double)num_directed_edges << ", source: " << source << std::endl;
    pvector<WeightT> dist(num_nodes, kDistInf);
    pvector<NodeID> frontier(num_directed_edges);
    pvector<SGOffset> VertexOffsets = g.VertexOffsets();
    dist[source] = 0;
    // two element arrays for double buffering curr=iter&1, next=(iter+1)&1
    size_t shared_indexes[2] = {0, kMaxBin};
    size_t frontier_tails[2] = {1, 0};
    frontier[0] = source;
    alloc_MAA();
    init_MAA();

#ifdef GEM5
    clear_mem_region();
    add_mem_region(frontier.beginp(), frontier.endp());                            // 6
    add_mem_region(dist.beginp(), dist.endp());                                    // 7
    add_mem_region(VertexOffsets.beginp(), VertexOffsets.endp());                  // 8
    add_mem_region(g.out_neighbors_, &g.out_neighbors_[VertexOffsets[num_nodes]]); // 9
    std::cout << "ROI started: " << omp_get_num_threads() << " threads" << std::endl;
    m5_work_begin(0, 0);
    m5_reset_stats(0, 0);
#endif

#ifdef TIMER_ENABLED
    Timer t;
    t.Start();
#endif
#pragma omp parallel
    {
        vector<vector<NodeID>> local_bins(0);
        size_t iter = 0;
        while (shared_indexes[iter & 1] != kMaxBin) {
            size_t &curr_bin_index = shared_indexes[iter & 1];
            size_t &next_bin_index = shared_indexes[(iter + 1) & 1];
            size_t &curr_frontier_tail = frontier_tails[iter & 1];
            size_t &next_frontier_tail = frontier_tails[(iter + 1) & 1];
#pragma omp master
            std::cout << "Starting DeltaStep: " << curr_frontier_tail << " elements (base)" << std::endl;
#pragma omp for nowait schedule(dynamic, 64)
            for (size_t i = 0; i < curr_frontier_tail; i++) {
                NodeID u = frontier[i];
                WeightT dist_u = dist[u];
                if (dist_u >= delta * static_cast<WeightT>(curr_bin_index)) {
                    for (int j = VertexOffsets[u]; j < VertexOffsets[u + 1]; j++) {
                        WNode wn = g.out_neighbors_[j];
                        NodeID v = wn.v;
                        WeightT old_dist = dist[v];
                        WeightT new_dist = dist_u + wn.w;
                        while (new_dist < old_dist) {
                            if (compare_and_swap(dist[v], old_dist, new_dist)) {
                                size_t dest_bin = new_dist / delta;
                                if (dest_bin >= local_bins.size())
                                    local_bins.resize(dest_bin + 1);
                                local_bins[dest_bin].push_back(v);
                                break;
                            }
                            old_dist = dist[v]; // swap failed, recheck dist update & retry
                        }
                    }
                }
            }
            if (curr_bin_index < local_bins.size() &&
                !local_bins[curr_bin_index].empty() &&
                local_bins[curr_bin_index].size() < kBinSizeThreshold) {
                assert(false);
            }
            for (size_t i = curr_bin_index; i < local_bins.size(); i++) {
                if (!local_bins[i].empty()) {
#pragma omp critical
                    next_bin_index = min(next_bin_index, i);
                    break;
                }
            }
#pragma omp barrier
#pragma omp single nowait
            {
#ifdef TIMER_ENABLED
                t.Stop();
                if (logging_enabled)
                    PrintStep(curr_bin_index, t.Millisecs(), curr_frontier_tail);
                t.Start();
#endif
                curr_bin_index = kMaxBin;
                curr_frontier_tail = 0;
            }
            if (next_bin_index < local_bins.size()) {
                size_t copy_start = fetch_and_add(next_frontier_tail, local_bins[next_bin_index].size());
                copy(local_bins[next_bin_index].begin(),
                     local_bins[next_bin_index].end(), frontier.data() + copy_start);
                local_bins[next_bin_index].resize(0);
            }
            iter++;
#pragma omp barrier
        }
#pragma omp single
        if (logging_enabled)
            cout << "took " << iter << " iterations" << endl;
    }

#ifdef GEM5
    m5_dump_stats(0, 0);
    m5_work_end(0, 0);
    clear_mem_region();
    std::cout << "ROI End!!!" << std::endl;
    m5_exit(0);
#endif
    return dist;
}

void PrintSSSPStats(const WGraph &g, const pvector<WeightT> &dist) {
    auto NotInf = [](WeightT d) { return d != kDistInf; };
    int64_t num_reached = count_if(dist.begin(), dist.end(), NotInf);
    cout << "SSSP Tree reaches " << num_reached << " nodes" << endl;
}

// Compares against simple serial implementation
bool SSSPVerifier(const WGraph &g, NodeID source, const pvector<WeightT> &dist_to_test) {
    // Serial Dijkstra implementation to get oracle distances
    pvector<WeightT> oracle_dist(g.num_nodes(), kDistInf);
    oracle_dist[source] = 0;
    typedef pair<WeightT, NodeID> WN;
    priority_queue<WN, vector<WN>, greater<WN>> mq;
    mq.push(make_pair(0, source));
    while (!mq.empty()) {
        WeightT td = mq.top().first;
        NodeID u = mq.top().second;
        mq.pop();
        if (td == oracle_dist[u]) {
            for (WNode wn : g.out_neigh(u)) {
                if (td + wn.w < oracle_dist[wn.v]) {
                    oracle_dist[wn.v] = td + wn.w;
                    mq.push(make_pair(td + wn.w, wn.v));
                }
            }
        }
    }
    // Report any mismatches
    bool all_ok = true;
    for (NodeID n : g.vertices()) {
        if (dist_to_test[n] != oracle_dist[n]) {
            cout << n << ": " << dist_to_test[n] << " != " << oracle_dist[n] << endl;
            all_ok = false;
        }
    }
    return all_ok;
}

int main(int argc, char *argv[]) {
    CLDelta<WeightT> cli(argc, argv, "single-source shortest-path");
    if (!cli.ParseArgs())
        return -1;
    WeightedBuilder b(cli);
    WGraph g = b.MakeGraph();
    SourcePicker<WGraph> sp(g, cli.start_vertex());
#ifdef GEM5
    std::cout << "Fake Checkpoint started" << std::endl;
    m5_checkpoint(0, 0);
    std::cout << "Fake Checkpoint ended" << std::endl;
#endif
    auto SSSPBound = [&sp, &cli](const WGraph &g) {
#ifdef MAA
        return DeltaStepMAA(g, sp.PickNext(), cli.delta(), cli.logging_en());
#else
        return DeltaStep(g, sp.PickNext(), cli.delta(), cli.logging_en());
#endif
    };
    SourcePicker<WGraph> vsp(g, cli.start_vertex());
    auto VerifierBound = [&vsp](const WGraph &g, const pvector<WeightT> &dist) {
        return SSSPVerifier(g, vsp.PickNext(), dist);
    };
    BenchmarkKernel(cli, g, SSSPBound, PrintSSSPStats, VerifierBound);
    return 0;
}
