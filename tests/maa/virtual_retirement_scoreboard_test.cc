#include <cstdint>
#include <iostream>

#include "mem/MAA/VirtualRetirementScoreboard.hh"

namespace
{

using Scoreboard = gem5::maa::VirtualRetirementScoreboard;
using Result = Scoreboard::Result;

#define CHECK(condition) \
    do { \
        if (!(condition)) { \
            std::cerr << "check failed line " << __LINE__ << ": " \
                      << #condition << "\n"; \
            return 1; \
        } \
    } while (false)

Scoreboard::Metadata
metadata(uint64_t generation, int page)
{
    Scoreboard::Metadata value;
    value.generation = generation;
    value.backingLine = page * 256;
    value.backingWordMask = 0x00ff;
    value.pageCount = 1;
    value.pageWords[0] = {page, 8};
    return value;
}

} // anonymous namespace

int
main()
{
    Scoreboard scoreboard;
    CHECK(scoreboard.reset(0) == Result::Invalid);
    CHECK(scoreboard.reset(Scoreboard::MaxEntries + 1) == Result::Invalid);
    CHECK(scoreboard.reset(Scoreboard::MaxEntries) == Result::Accepted);

    for (uint32_t entry = 0; entry < Scoreboard::MaxEntries; ++entry) {
        CHECK(scoreboard.insert(0x1000 + entry * 64,
                                metadata(entry + 1, entry % 4)) ==
              Result::Accepted);
    }
    CHECK(scoreboard.full());
    CHECK(scoreboard.size() == Scoreboard::MaxEntries);
    CHECK(scoreboard.insert(0x1000, metadata(99, 0)) == Result::Duplicate);
    CHECK(scoreboard.insert(0x9000, metadata(99, 0)) == Result::Full);
    CHECK(scoreboard.reset(16) == Result::Busy);

    const auto *found = scoreboard.find(0x1000 + 17 * 64);
    CHECK(found != nullptr);
    CHECK(found->generation == 18);
    CHECK(found->backingLine == 256);
    CHECK(found->backingWordMask == 0x00ff);
    CHECK(found->pageCount == 1);
    CHECK(found->pageWords[0].page == 1);
    CHECK(found->pageWords[0].words == 8);

    Scoreboard::Metadata retired;
    CHECK(scoreboard.take(0xdeadbeef, retired) == Result::NotFound);
    CHECK(scoreboard.take(0x1000 + 17 * 64, retired) == Result::Accepted);
    CHECK(retired.generation == 18);
    CHECK(!scoreboard.contains(0x1000 + 17 * 64));
    CHECK(scoreboard.size() == Scoreboard::MaxEntries - 1);
    CHECK(scoreboard.take(0x1000 + 17 * 64, retired) == Result::NotFound);
    CHECK(scoreboard.insert(0x9000, metadata(99, 3)) == Result::Accepted);

    for (uint32_t entry = 0; entry < Scoreboard::MaxEntries; ++entry) {
        const uint64_t key = entry == 17 ? 0x9000 : 0x1000 + entry * 64;
        CHECK(scoreboard.take(key, retired) == Result::Accepted);
    }
    CHECK(scoreboard.empty());
    CHECK(scoreboard.reset(16) == Result::Accepted);
    CHECK(scoreboard.capacity() == 16);

    auto invalid = metadata(1, 0);
    invalid.backingWordMask = 0;
    CHECK(scoreboard.insert(1, invalid) == Result::Invalid);
    invalid = metadata(1, 0);
    invalid.pageCount = Scoreboard::MaxPagesPerEntry + 1;
    CHECK(scoreboard.insert(1, invalid) == Result::Invalid);

    std::cout << "virtual_retirement_scoreboard_test: PASS\n";
    return 0;
}
