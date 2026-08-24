#ifndef GAPBS_SSSP_TAIL_REPLAY_HH
#define GAPBS_SSSP_TAIL_REPLAY_HH

#include <algorithm>
#include <cstddef>
#include <cstdint>

namespace sssp_tail_replay {

// Consume the exact edge stream selected by the accelerator's frontier
// predicate.  Invalid/inactive/repeated frontier entries and exhausted
// adjacency lists are skipped exactly as the range-loop formation does.
// Returning false is a fail-closed cursor-exhaustion result; production
// callers abort rather than silently inventing or dropping a word.
template <typename Node, typename Offset, typename Emit>
bool
ConsumeCursorWords(std::size_t words, int frontier_end,
                   const Node *frontier, const Offset *vertex_offsets,
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
AdvanceCursorWords(std::size_t words, int frontier_end,
                   const Node *frontier, const Offset *vertex_offsets,
                   const std::uint8_t *active_sources, int num_nodes,
                   int &cursor_pos, Offset &cursor_edge)
{
    return ConsumeCursorWords(
        words, frontier_end, frontier, vertex_offsets, active_sources,
        num_nodes, cursor_pos, cursor_edge,
        [](Node, Offset, std::size_t) {});
}

// Replay a sequence of already-published physical pages in publication order.
// The cursor was advanced when each page was published, so replay deliberately
// does not touch it a second time.
template <typename Replay>
void
ReplayPublishedPages(std::size_t pages, std::size_t physical_words,
                     Replay replay)
{
    for (std::size_t page = 0; page < pages; ++page)
        replay(page * physical_words, physical_words);
}

// Exact ordered MIN: capture each lane's pre-update value, apply MIN in lane
// order, then reload the batch-final destination and emit the same winner test
// as the legacy accelerator sequence.
template <typename Index, typename Value, typename Winner>
void
OrderedMinReplay(std::size_t begin, std::size_t words,
                 const Index *indices, const Value *candidates,
                 Value *old_results, Value *dist, Winner winner)
{
    const std::size_t end = begin + words;
    for (std::size_t lane = begin; lane < end; ++lane) {
        const Index destination = indices[lane];
        const Value candidate = candidates[lane];
        const Value old_distance = dist[destination];
        old_results[lane] = old_distance;
        if (candidate < old_distance)
            dist[destination] = candidate;
    }
    for (std::size_t lane = begin; lane < end; ++lane) {
        const Index destination = indices[lane];
        const Value candidate = candidates[lane];
        const Value final_distance = dist[destination];
        if (candidate == final_distance &&
            old_results[lane] > final_distance)
            winner(destination, final_distance);
    }
}

// Reconstruct one physical page from accelerator-provided ordered old results.
// Keeping the map page-local preserves the legacy instruction boundary when a
// destination occurs in more than one published page.
template <typename Index, typename Value, typename Map, typename Winner>
void
ReplayOldResultPage(std::size_t begin, std::size_t words,
                    const Index *indices, const Value *candidates,
                    const Value *old_results, Map &page_finals,
                    Winner winner)
{
    const std::size_t end = begin + words;
    page_finals.clear();
    for (std::size_t lane = end; lane-- > begin;) {
        const Index destination = indices[lane];
        page_finals.emplace(
            destination, std::min(old_results[lane], candidates[lane]));
    }
    for (std::size_t lane = begin; lane < end; ++lane) {
        const Index destination = indices[lane];
        const Value candidate = candidates[lane];
        const Value final_distance = page_finals.at(destination);
        if (candidate == final_distance && old_results[lane] > final_distance)
            winner(destination, final_distance);
    }
}

} // namespace sssp_tail_replay

#endif // GAPBS_SSSP_TAIL_REPLAY_HH
