#include <array>
#include <cstdlib>
#include <iostream>

#include "mem/MAA/CompleteLinePayloadStaging.hh"

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__ << ": " #condition     \
                      << '\n';                                               \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

using Stage = gem5::maa::CompleteLinePayloadStaging;

Stage::Identity identity(uint32_t slot = 3)
{
    return {7, slot, 0x1000 + slot * 64, 0xff, 8};
}

void
checkWidth(uint32_t width, uint8_t words)
{
    Stage stage;
    Stage::Identity line{9, 1, 0x2000, 0xffff, words};
    CHECK(stage.configure(width));
    CHECK(stage.claim(line, 100) == Stage::Result::Accepted);
    uint64_t cycle = 100;
    while (stage.advance(line, cycle) == Stage::Result::NotReady)
        ++cycle;
    const uint64_t expected = (words + width - 1) / width;
    CHECK(cycle == 100 + expected);
    CHECK(stage.progress() == words);
    CHECK(stage.counters().readCycles == expected);
    CHECK(stage.complete(line) == Stage::Result::Accepted);
}

void
checkSharedWidthPipeline()
{
    Stage stage;
    CHECK(stage.configure(8, 8));
    std::array<Stage::Identity, 8> lines{};
    for (uint32_t index = 0; index < lines.size(); ++index) {
        lines[index] = {11, index, 0x4000 + index * 64, 1, 1};
        CHECK(stage.claim(lines[index], 40) == Stage::Result::Accepted);
    }
    CHECK(stage.activeCount() == 8);
    CHECK(stage.counters().peakActive == 8);
    CHECK(stage.claim({11, 9, 0x5000, 1, 1}, 40) == Stage::Result::Busy);
    for (const auto &line : lines)
        CHECK(stage.advance(line, 41) == Stage::Result::Accepted);
    CHECK(stage.counters().readCycles == 1);
    for (const auto &line : lines)
        CHECK(stage.complete(line) == Stage::Result::Accepted);
    CHECK(!stage.isActive());
    CHECK(stage.counters().starts == 8);
    CHECK(stage.counters().completions == 8);
}

int main()
{
    Stage stage;
    CHECK(stage.configure(0));
    CHECK(stage.claim(identity(), 10) == Stage::Result::Disabled);
    CHECK(!stage.configure(3));
    CHECK(stage.configure(4));
    CHECK(stage.claim(identity(), 10) == Stage::Result::Accepted);
    CHECK(stage.advance(identity(), 10) == Stage::Result::NotReady);
    CHECK(stage.advance(identity(), 11) == Stage::Result::NotReady);
    CHECK(stage.progress() == 4);
    CHECK(stage.claim(identity(4), 11) == Stage::Result::Busy);
    CHECK(stage.claim(identity(4), 11) == Stage::Result::Busy);
    CHECK(stage.counters().blockedCycles == 1);
    CHECK(stage.advance(identity(), 12) == Stage::Result::Accepted);
    CHECK(stage.complete(identity()) == Stage::Result::Accepted);
    CHECK(stage.counters().starts == 1);
    CHECK(stage.counters().completions == 1);
    CHECK(stage.counters().readCycles == 2);

    CHECK(stage.claim(identity(), 20) == Stage::Result::Accepted);
    CHECK(stage.advance(identity(), 19) ==
          Stage::Result::NonMonotonicCycle);
    CHECK(stage.advance(identity(), 22) == Stage::Result::Accepted);
    CHECK(stage.complete(identity()) == Stage::Result::Accepted);
    for (uint32_t width : {1U, 2U, 4U, 8U}) {
        checkWidth(width, 8);
        checkWidth(width, 16);
    }
    checkSharedWidthPipeline();
    std::cout << "complete-line payload staging tests passed\n";
}
