#include <cstdint>
#include <iostream>
#include <vector>

#include "sssp_coherent_fallback.hh"

#define CHECK(condition) \
    do { \
        if (!(condition)) { \
            std::cerr << "CHECK failed at line " << __LINE__ << ": " \
                      << #condition << '\n'; \
            return 1; \
        } \
    } while (false)

int
main()
{
    using namespace sssp_coherent_fallback;
    CHECK(SelectRemainingRoute(0) == RemainingRoute::kComplete);
    CHECK(SelectRemainingRoute(1) ==
          RemainingRoute::kReconstructCoherentTail);
    CHECK(SelectRemainingRoute(4095) ==
          RemainingRoute::kReconstructCoherentTail);
    CHECK(SelectRemainingRoute(4096) ==
          RemainingRoute::kPublishFullPage);
    CHECK(SelectRemainingRoute(16384) ==
          RemainingRoute::kPublishFullPage);
    for (std::size_t page = 0; page < 20; ++page)
        CHECK(BackingPageForObservedWords(page * kPhysicalWords) == page % 4);

    Counters counters;
    for (std::size_t page = 0; page < kPublicationArrays; ++page) {
        counters.recordPublicationIssue();
        counters.recordPublicationResponse();
    }
    counters.recordFallbackPage();
    counters.recordCoherentTail(37);
    CHECK(counters.responseClosure());
    CHECK(counters.legal());
    CHECK(counters.publication_words == 3 * 4096);
    CHECK(counters.fallback_consumed_words == 4096 + 37);
    CHECK(counters.predicate_restore_words == 4096);

    Counters illegal;
    illegal.recordHostSpdRead(4096);
    CHECK(illegal.host_spd_reads == 1);
    CHECK(illegal.illegal_host_spd_line_starts == 1);
    CHECK(!illegal.legal());

    const int frontier[] = {0, 1, 1, 2};
    const int offsets[] = {0, 1, 4, 6};
    const std::uint8_t active[] = {0, 1, 1};
    int cursor_pos = 0;
    int cursor_edge = -1;
    std::vector<int> visited;
    CHECK(ConsumeCursorWords(
        5, 4, frontier, offsets, active, 3, cursor_pos, cursor_edge,
        [&](int source, int edge, std::size_t) {
            visited.push_back(source * 10 + edge);
        }));
    CHECK((visited == std::vector<int>{11, 12, 13, 11, 12}));

    std::uint32_t indices[] = {1, 2, 1, 1, 2};
    int candidates[] = {70, 60, 50, 65, 55};
    int old_results[5] = {};
    int distances[] = {0, 100, 80};
    std::vector<std::uint32_t> winners;
    OrderedMinReplay(
        5, indices, candidates, old_results, distances,
        [&](std::uint32_t destination, int) {
            winners.push_back(destination);
        });
    CHECK(distances[1] == 50);
    CHECK(distances[2] == 55);
    CHECK((winners == std::vector<std::uint32_t>{1, 2}));

    std::cout << "SSSP_COHERENT_FALLBACK_HELPER_PASS\n";
    return 0;
}
