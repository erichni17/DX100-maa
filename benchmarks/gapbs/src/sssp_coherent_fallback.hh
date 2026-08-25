#ifndef GAPBS_SSSP_COHERENT_FALLBACK_HH
#define GAPBS_SSSP_COHERENT_FALLBACK_HH

#include <algorithm>
#include <cstddef>
#include <cstdint>

namespace sssp_coherent_fallback {

constexpr std::size_t kPhysicalWords = 4096;
constexpr std::size_t kLogicalWords = 16384;
constexpr std::size_t kPublicationArrays = 3;
constexpr std::size_t kWordBytes = sizeof(std::uint32_t);

enum class RemainingRoute
{
    kComplete,
    kPublishFullPage,
    kReconstructCoherentTail,
};

constexpr RemainingRoute
SelectRemainingRoute(std::size_t remaining)
{
    return remaining == 0
               ? RemainingRoute::kComplete
               : remaining >= kPhysicalWords
                     ? RemainingRoute::kPublishFullPage
                     : RemainingRoute::kReconstructCoherentTail;
}

constexpr std::size_t
BackingPageForObservedWords(std::size_t observed_words)
{
    return (observed_words % kLogicalWords) / kPhysicalWords;
}

struct Counters
{
    std::uint64_t host_spd_reads = 0;
    std::uint64_t illegal_host_spd_line_starts = 0;
    std::int64_t max_host_spd_element = -1;
    std::uint64_t fallback_pages = 0;
    std::uint64_t publication_issue_pages = 0;
    std::uint64_t publication_response_pages = 0;
    std::uint64_t publication_words = 0;
    std::uint64_t fallback_consumed_words = 0;
    std::uint64_t predicate_restore_words = 0;
    std::uint64_t coherent_tail_batches = 0;
    std::uint64_t coherent_tail_words = 0;

    void recordHostSpdRead(std::size_t element)
    {
        ++host_spd_reads;
        max_host_spd_element = std::max(
            max_host_spd_element, static_cast<std::int64_t>(element));
        if ((element / 16) * 16 >= kPhysicalWords)
            ++illegal_host_spd_line_starts;
    }

    void recordPublicationIssue()
    {
        ++publication_issue_pages;
    }

    void recordPublicationResponse()
    {
        ++publication_response_pages;
    }

    void recordFallbackPage()
    {
        ++fallback_pages;
        publication_words += kPublicationArrays * kPhysicalWords;
        fallback_consumed_words += kPhysicalWords;
        predicate_restore_words += kPhysicalWords;
    }

    void recordCoherentTail(std::size_t words)
    {
        if (words == 0 || words >= kPhysicalWords)
            return;
        ++coherent_tail_batches;
        coherent_tail_words += words;
        fallback_consumed_words += words;
    }

    bool responseClosure() const
    {
        return publication_issue_pages == publication_response_pages &&
            publication_issue_pages == fallback_pages * kPublicationArrays;
    }

    bool legal() const
    {
        return host_spd_reads == 0 && illegal_host_spd_line_starts == 0 &&
            max_host_spd_element == -1 && responseClosure() &&
            publication_words ==
                fallback_pages * kPublicationArrays * kPhysicalWords &&
            predicate_restore_words == fallback_pages * kPhysicalWords;
    }
};

// Consume the exact active edge stream in range-loop order. Invalid/inactive
// frontier occurrences and exhausted adjacency lists are skipped identically
// to the accelerator's conditional range formation.
template <typename Node, typename Offset, typename Emit>
bool
ConsumeCursorWords(std::size_t words, int frontier_end, const Node *frontier,
                   const Offset *vertex_offsets,
                   const std::uint8_t *active_sources, int num_nodes,
                   int &cursor_pos, Offset &cursor_edge, Emit emit)
{
    std::size_t consumed = 0;
    while (consumed < words) {
        while (cursor_pos < frontier_end) {
            const Node source = frontier[cursor_pos];
            if (source < 0 || source >= num_nodes ||
                !active_sources[source]) {
                ++cursor_pos;
                cursor_edge = static_cast<Offset>(-1);
                continue;
            }
            const Offset begin = vertex_offsets[source];
            const Offset end = vertex_offsets[source + 1];
            if (cursor_edge < begin)
                cursor_edge = begin;
            if (cursor_edge >= end) {
                ++cursor_pos;
                cursor_edge = static_cast<Offset>(-1);
                continue;
            }
            break;
        }
        if (cursor_pos >= frontier_end)
            return false;

        const Node source = frontier[cursor_pos];
        emit(source, cursor_edge, consumed);
        ++cursor_edge;
        ++consumed;
    }
    return true;
}

template <typename Node, typename Offset>
bool
AdvanceCursorWords(std::size_t words, int frontier_end, const Node *frontier,
                   const Offset *vertex_offsets,
                   const std::uint8_t *active_sources, int num_nodes,
                   int &cursor_pos, Offset &cursor_edge)
{
    return ConsumeCursorWords(
        words, frontier_end, frontier, vertex_offsets, active_sources,
        num_nodes, cursor_pos, cursor_edge,
        [](Node, Offset, std::size_t) {});
}

// Exact ordered MIN followed by the batch-final reload/winner test. All
// operands and results reside in ordinary coherent backing.
template <typename Index, typename Value, typename Winner>
void
OrderedMinReplay(std::size_t words, const Index *indices,
                 const Value *candidates, Value *old_results, Value *dist,
                 Winner winner)
{
    for (std::size_t lane = 0; lane < words; ++lane) {
        const Index destination = indices[lane];
        const Value candidate = candidates[lane];
        const Value old_distance = dist[destination];
        old_results[lane] = old_distance;
        if (candidate < old_distance)
            dist[destination] = candidate;
    }
    for (std::size_t lane = 0; lane < words; ++lane) {
        const Index destination = indices[lane];
        const Value candidate = candidates[lane];
        const Value final_distance = dist[destination];
        if (candidate == final_distance && old_results[lane] > final_distance)
            winner(destination, final_distance);
    }
}

} // namespace sssp_coherent_fallback

#endif // GAPBS_SSSP_COHERENT_FALLBACK_HH
