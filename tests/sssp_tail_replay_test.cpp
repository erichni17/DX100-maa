#include <algorithm>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <unordered_map>
#include <utility>
#include <vector>

#include "sssp_tail_replay.hh"
#include "sssp_tail_route.hh"

using sssp_tail_route::BatchRoute;
using sssp_tail_route::RouteCounters;

static void
testRoutesAndCoverage()
{
    const std::size_t boundaries[] = {
        0, 4095, 4096, 4097, 4133, 8192, 12288, 16384};
    const BatchRoute routes[] = {
        BatchRoute::kTerminal, BatchRoute::kBoundedSpd,
        BatchRoute::kBoundedSpd, BatchRoute::kExactCpu,
        BatchRoute::kExactCpu, BatchRoute::kExactCpu,
        BatchRoute::kExactCpu, BatchRoute::kExactCpu};
    for (std::size_t i = 0; i < 8; ++i)
        assert(sssp_tail_route::SelectBatchRoute(boundaries[i]) == routes[i]);

    RouteCounters single_batch;
    single_batch.recordProduced(16384);
    single_batch.recordExactCpu(16384);
    assert(single_batch.logical_windows == 0);
    assert(single_batch.exact_cpu_words == 16384);
    assert(single_batch.coverageCloses());

    RouteCounters four_pages;
    for (std::size_t page = 0;
         page < sssp_tail_route::kPagesPerLogicalWindow; ++page)
        four_pages.recordProduced(4096);
    four_pages.recordLogicalWindow();
    assert(four_pages.logical_windows == 1);
    assert(four_pages.accelerated_words == 16384);
    assert(four_pages.coverageCloses());

    RouteCounters guarded;
    assert(guarded.guardHostSpdAccess(0));
    assert(guarded.guardHostSpdAccess(4095));
    assert(guarded.guardHostSpdAccess(4096));
    assert(!guarded.guardHostSpdAccess(4097));
    assert(guarded.illegal_host_spd_attempts == 1);
    assert(!guarded.legal());
}

static void
testCursorInactiveRepeatedAndExhaustion()
{
    const int frontier[] = {-1, 0, 1, 2, 1, 3};
    const std::int64_t offsets[] = {0, 1, 3, 4, 6};
    const std::uint8_t active[] = {0, 1, 0, 1};
    int cursor_pos = 0;
    std::int64_t cursor_edge = -1;
    std::vector<std::pair<int, std::int64_t>> observed;
    const bool complete = sssp_tail_replay::ConsumeCursorWords(
        6, 6, frontier, offsets, active, 4, cursor_pos, cursor_edge,
        [&](int source, std::int64_t edge, std::size_t) {
            observed.emplace_back(source, edge);
        });
    const std::vector<std::pair<int, std::int64_t>> expected = {
        {1, 1}, {1, 2}, {1, 1}, {1, 2}, {3, 4}, {3, 5}};
    assert(complete);
    assert(observed == expected);
    assert(!sssp_tail_replay::AdvanceCursorWords(
        1, 6, frontier, offsets, active, 4, cursor_pos, cursor_edge));
}

static void
testOrderedMinOldResultsAndWinner()
{
    const std::uint32_t indices[] = {0, 0, 0, 1};
    const int candidates[] = {8, 5, 5, 7};
    int old_results[] = {0, 0, 0, 0};
    int dist[] = {10, 9};
    std::vector<std::pair<std::uint32_t, int>> winners;
    sssp_tail_replay::OrderedMinReplay(
        0, 4, indices, candidates, old_results, dist,
        [&](std::uint32_t destination, int final_distance) {
            winners.emplace_back(destination, final_distance);
        });
    assert(dist[0] == 5 && dist[1] == 7);
    assert(old_results[0] == 10 && old_results[1] == 8);
    assert(old_results[2] == 5 && old_results[3] == 9);
    const std::vector<std::pair<std::uint32_t, int>> expected = {
        {0, 5}, {1, 7}};
    assert(winners == expected);
}

static void
testOrderedMinAtEveryBoundary()
{
    const std::size_t boundaries[] = {
        0, 4095, 4096, 4097, 4133, 8192, 12288, 16384};
    for (std::size_t boundary : boundaries) {
        const std::size_t allocation = std::max<std::size_t>(boundary, 1);
        std::vector<std::uint32_t> indices(allocation);
        std::vector<int> candidates(allocation);
        std::vector<int> old_results(allocation, -1);
        std::vector<int> dist(allocation, 60000);
        for (std::size_t lane = 0; lane < boundary; ++lane) {
            indices[lane] = static_cast<std::uint32_t>(lane);
            candidates[lane] = 50000 - static_cast<int>(lane % 997);
        }
        std::size_t winners = 0;
        sssp_tail_replay::OrderedMinReplay(
            0, boundary, indices.data(), candidates.data(),
            old_results.data(), dist.data(),
            [&](std::uint32_t destination, int final_distance) {
                assert(final_distance == candidates[destination]);
                ++winners;
            });
        assert(winners == boundary);
        for (std::size_t lane = 0; lane < boundary; ++lane) {
            assert(old_results[lane] == 60000);
            assert(dist[lane] == candidates[lane]);
        }
    }
}

static void
testInterruptedPublishedReplayAndCrossPageDuplicates()
{
    // Two published pages are replayed before an interrupting exact batch.
    std::vector<std::pair<std::size_t, std::size_t>> replayed;
    sssp_tail_replay::ReplayPublishedPages(
        2, 4096, [&](std::size_t begin, std::size_t words) {
            replayed.emplace_back(begin, words);
        });
    const std::vector<std::pair<std::size_t, std::size_t>> expected_replay = {
        {0, 4096}, {4096, 4096}};
    assert(replayed == expected_replay);

    // Destination zero improves once in each physical page.  Page-local
    // reconstruction must preserve both legacy instruction winners.
    const std::uint32_t indices[] = {0, 1, 2, 3, 0, 4, 5, 6};
    const int candidates[] = {8, 11, 12, 13, 5, 14, 15, 16};
    const int old_results[] = {10, 20, 20, 20, 8, 20, 20, 20};
    std::unordered_map<std::uint32_t, int> finals;
    std::vector<std::pair<std::uint32_t, int>> winners;
    for (std::size_t page = 0; page < 2; ++page) {
        sssp_tail_replay::ReplayOldResultPage(
            page * 4, 4, indices, candidates, old_results, finals,
            [&](std::uint32_t destination, int final_distance) {
                if (destination == 0)
                    winners.emplace_back(destination, final_distance);
            });
    }
    const std::vector<std::pair<std::uint32_t, int>> expected_winners = {
        {0, 8}, {0, 5}};
    assert(winners == expected_winners);
}

int
main()
{
    testRoutesAndCoverage();
    testCursorInactiveRepeatedAndExhaustion();
    testOrderedMinOldResultsAndWinner();
    testOrderedMinAtEveryBoundary();
    testInterruptedPublishedReplayAndCrossPageDuplicates();
    std::cout << "SSSP_TAIL_REPLAY_TEST_PASS boundaries=8 cursor=closed "
                 "published_replay=ordered duplicate_pages=preserved\n";
}
