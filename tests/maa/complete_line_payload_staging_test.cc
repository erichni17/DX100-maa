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
    CHECK(stage.allocatedEntries() == 1);
    CHECK(stage.claim(line, 100) == Stage::Result::Accepted);
    uint64_t cycle = 100;
    while (stage.advance(line, cycle) == Stage::Result::NotReady)
        ++cycle;
    const uint64_t expected = (words + width - 1) / width;
    CHECK(cycle == 100 + expected);
    CHECK(stage.progress() == words);
    CHECK(stage.counters().readCycles == expected);
    CHECK(stage.counters().scheduledWords == words);
    CHECK(stage.counters().readWords == words);
    CHECK(stage.counters().serialReadCycles == expected);
    CHECK(stage.complete(line) == Stage::Result::Accepted);
}

void
checkSharedWidthPipeline()
{
    Stage stage;
    CHECK(stage.configure(8, 8));
    CHECK(stage.allocatedEntries() == 8);
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
    CHECK(stage.counters().scheduledWords == 8);
    CHECK(stage.counters().readWords == 8);
    CHECK(stage.counters().serialReadCycles == 8);
    for (const auto &line : lines)
        CHECK(stage.complete(line) == Stage::Result::Accepted);
    CHECK(!stage.isActive());
    CHECK(stage.counters().starts == 8);
    CHECK(stage.counters().completions == 8);
}

void
checkBankedPayloadReads()
{
    Stage balanced;
    CHECK(balanced.configure(4, 1, 4));
    Stage::Identity line{13, 0, 0x6000, 0xff, 8};
    line.bankCount = 4;
    line.bankWords[0] = 2;
    line.bankWords[1] = 2;
    line.bankWords[2] = 2;
    line.bankWords[3] = 2;
    CHECK(balanced.claim(line, 10) == Stage::Result::Accepted);
    CHECK(balanced.advance(line, 11) == Stage::Result::NotReady);
    CHECK(balanced.advance(line, 12) == Stage::Result::Accepted);
    CHECK(balanced.counters().readCycles == 2);
    CHECK(balanced.counters().serialReadCycles == 2);
    CHECK(balanced.counters().bankConflictCycles == 0);
    CHECK(balanced.complete(line) == Stage::Result::Accepted);

    Stage conflicted;
    CHECK(conflicted.configure(4, 1, 4));
    line.slot = 1;
    line.lineAddress += 64;
    line.bankWords = {};
    line.bankWords[0] = 8;
    CHECK(conflicted.claim(line, 20) == Stage::Result::Accepted);
    CHECK(conflicted.advance(line, 28) == Stage::Result::Accepted);
    CHECK(conflicted.counters().readCycles == 8);
    CHECK(conflicted.counters().serialReadCycles == 8);
    CHECK(conflicted.counters().bankConflictCycles == 7);
    CHECK(conflicted.complete(line) == Stage::Result::Accepted);

    Stage imbalanced;
    CHECK(imbalanced.configure(4, 1, 8));
    Stage::Identity sparse{17, 2, 0x7000, 0x3f, 6};
    sparse.bankCount = 8;
    sparse.bankWords[3] = 1;
    sparse.bankWords[4] = 1;
    sparse.bankWords[5] = 1;
    sparse.bankWords[6] = 1;
    sparse.bankWords[7] = 2;
    CHECK(imbalanced.claim(sparse, 30) == Stage::Result::Accepted);
    CHECK(imbalanced.advance(sparse, 32) == Stage::Result::Accepted);
    CHECK(imbalanced.counters().readCycles == 2);
    CHECK(imbalanced.counters().serialReadCycles == 2);
    CHECK(imbalanced.complete(sparse) == Stage::Result::Accepted);
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
    checkBankedPayloadReads();
    std::cout << "complete-line payload staging tests passed\n";
}
