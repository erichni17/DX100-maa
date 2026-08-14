#include <cstdint>
#include <cstdlib>
#include <iostream>

#include "mem/MAA/VirtualCombinerPageOrder.hh"

using gem5::VirtualCombinerPageOrder;

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;        \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

int
main()
{
    constexpr uint64_t begin = 0x100040;
    constexpr uint32_t word_bytes = 8;
    constexpr uint64_t page_bytes =
        VirtualCombinerPageOrder::ConsumerPageWords * word_bytes;
    constexpr uint64_t end = begin + 3 * page_bytes;

    uint32_t page = 99;
    CHECK(VirtualCombinerPageOrder::linePage(begin, begin, end, word_bytes,
                                             page));
    CHECK(page == 0);
    CHECK(VirtualCombinerPageOrder::linePage(begin + page_bytes, begin, end,
                                             word_bytes, page));
    CHECK(page == 1);
    CHECK(VirtualCombinerPageOrder::linePage(begin + 2 * page_bytes + 64,
                                             begin, end, word_bytes, page));
    CHECK(page == 2);

    // This is the production ready metadata: enqueue happens only when a
    // combiner line becomes full, and selection reads page heads rather than
    // comparing the 384 line slots.
    VirtualCombinerPageOrder ready;
    ready.reset(384);
    CHECK(ready.enqueue(383, 1));
    CHECK(ready.enqueue(7, 0));
    CHECK(ready.enqueue(9, 0));
    CHECK(ready.firstReady(page) == 7 && page == 0);
    CHECK(ready.hasReadyLater(page));
    CHECK(ready.retireFullLine(7));
    CHECK(ready.firstReady(page) == 9 && page == 0);
    CHECK(ready.retireFullLine(9));
    CHECK(ready.firstReady(page) == 383 && page == 1);
    CHECK(!ready.hasReadyLater(page));
    CHECK(ready.retireFullLine(383));
    CHECK(ready.firstReady(page) == -1);
    CHECK(!ready.enqueue(384, 0));
    CHECK(!ready.enqueue(0, VirtualCombinerPageOrder::MaxPages));

    // Both production victim paths retire the ready entry exactly when a full
    // line first leaves the full state.  A masked write has one transition;
    // word-at-a-time retirement has many writes but only its first word does.
    ready.reset(384);
    CHECK(ready.enqueue(12, 0));
    CHECK(ready.retireFullLine(12)); // masked victim write
    CHECK(!ready.retireFullLine(12));
    ready.reset(384);
    CHECK(ready.enqueue(13, 0));
    bool page_ready = true;
    unsigned ready_removals = 0;
    for (unsigned word = 0; word < 8; ++word) {
        if (page_ready) {
            CHECK(ready.retireFullLine(13));
            page_ready = false;
            ++ready_removals;
        }
    }
    CHECK(ready_removals == 1);
    CHECK(!ready.retireFullLine(13));

    // Range checks happen before subtraction: neither a pre-range line nor a
    // wrapped/empty range can become a false high-priority page.
    CHECK(!VirtualCombinerPageOrder::linePage(begin - 64, begin, end,
                                              word_bytes, page));
    CHECK(!VirtualCombinerPageOrder::linePage(end, begin, end, word_bytes,
                                              page));
    CHECK(!VirtualCombinerPageOrder::linePage(begin, end, begin, word_bytes,
                                              page));
    return 0;
}
