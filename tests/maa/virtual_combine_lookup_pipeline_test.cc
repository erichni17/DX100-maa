#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <vector>

#include "mem/MAA/VirtualCombineLookupPipeline.hh"

namespace
{

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << '\n';             \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

using Pipeline = gem5::maa::VirtualCombineLookupPipeline;
using Result = Pipeline::Result;
using Token = Pipeline::Token;

Token
token(uint64_t generation, uint64_t issue, uint16_t slot,
      uint32_t slot_sequence, int offset, int iteration)
{
    Token value;
    value.operationGeneration = generation;
    value.issueSequence = issue;
    value.responseSlot = slot;
    value.slotSequence = slot_sequence;
    value.offsetSlot = offset;
    value.iteration = iteration;
    value.wordId = iteration % 16;
    value.pass = iteration / 16384;
    value.wordBytes = 4;
    return value;
}

void
testConfigurationAndExactLatency()
{
    Pipeline pipeline;
    CHECK(Pipeline::validLatency(0));
    CHECK(Pipeline::validLatency(8));
    CHECK(!Pipeline::validLatency(9));
    CHECK(pipeline.configure(9, 4) == Result::InvalidConfiguration);
    CHECK(pipeline.configure(2, 0) == Result::InvalidConfiguration);
    CHECK(pipeline.configure(0, 0) == Result::Accepted);
    CHECK(!pipeline.enabled() && pipeline.capacity() == 0);
    CHECK(pipeline.begin(1) == Result::Disabled);

    CHECK(pipeline.configure(2, 4) == Result::Accepted);
    CHECK(pipeline.begin(17) == Result::Accepted);
    const Token first = token(17, 0, 0, 0, 9, 31);
    const Token second = token(17, 1, 1, 0, 12, 44);
    CHECK(pipeline.start(100, first) == Result::Accepted);
    CHECK(pipeline.start(100, second) == Result::Accepted);

    std::vector<Token> ready;
    CHECK(pipeline.collectReady(100, ready) == Result::Accepted);
    CHECK(ready.empty());
    CHECK(pipeline.collectReady(101, ready) == Result::Accepted);
    CHECK(ready.empty());
    CHECK(pipeline.complete(101, first) == Result::NotReady);
    CHECK(pipeline.collectReady(102, ready) == Result::Accepted);
    CHECK(ready.size() == 2);
    CHECK(ready[0].responseSlot == 0 && ready[0].offsetSlot == 9);
    CHECK(ready[1].responseSlot == 1 && ready[1].offsetSlot == 12);
    CHECK(pipeline.complete(102, ready[1]) == Result::Accepted);
    CHECK(pipeline.complete(102, ready[0]) == Result::Accepted);
    CHECK(pipeline.finish(17) == Result::Accepted);
    CHECK(pipeline.counters().issues == 2);
    CHECK(pipeline.counters().completions == 2);
    CHECK(pipeline.counters().peakOccupancy == 2);
}

void
testPipelinedWidthAnd65536WordClosure()
{
    constexpr uint64_t generation = 99;
    constexpr uint32_t words = 65536;
    constexpr uint32_t width = 4;
    constexpr uint32_t latency = 8;
    Pipeline pipeline;
    CHECK(pipeline.configure(latency, width * latency) == Result::Accepted);
    CHECK(pipeline.begin(generation) == Result::Accepted);

    uint32_t issued = 0;
    uint32_t completed = 0;
    uint64_t cycle = 0;
    std::vector<Token> ready;
    while (completed != words) {
        CHECK(pipeline.collectReady(cycle, ready) == Result::Accepted);
        for (const Token &value : ready) {
            CHECK(value.issueSequence == static_cast<uint64_t>(
                      value.iteration));
            CHECK(pipeline.complete(cycle, value) == Result::Accepted);
            ++completed;
        }
        for (uint32_t lane = 0; lane < width && issued != words; ++lane) {
            const uint16_t slot = static_cast<uint16_t>(issued % 8);
            const uint32_t slot_sequence = issued / 8;
            const Token value = token(
                generation, issued, slot, slot_sequence,
                static_cast<int>(issued % 16384),
                static_cast<int>(issued));
            CHECK(pipeline.start(cycle, value) == Result::Accepted);
            ++issued;
        }
        ++cycle;
    }
    CHECK(issued == words);
    CHECK(pipeline.empty());
    CHECK(pipeline.counters().issues == words);
    CHECK(pipeline.counters().completions == words);
    CHECK(pipeline.counters().peakOccupancy == width * latency);
    CHECK(pipeline.finish(generation) == Result::Accepted);
}

void
testStaleMismatchCapacityAndTerminalFailures()
{
    Pipeline pipeline;
    CHECK(pipeline.configure(1, 2) == Result::Accepted);
    CHECK(pipeline.begin(7) == Result::Accepted);
    const Token first = token(7, 0, 0, 0, 3, 10);
    const Token second = token(7, 1, 1, 0, 4, 11);
    const Token third = token(7, 2, 1, 1, 5, 12);
    CHECK(pipeline.start(4, first) == Result::Accepted);
    CHECK(pipeline.start(4, second) == Result::Accepted);
    CHECK(pipeline.start(4, third) == Result::Full);
    CHECK(pipeline.finish(7) == Result::Imbalance);

    Token stale = first;
    stale.offsetSlot++;
    CHECK(pipeline.complete(5, stale) == Result::StaleToken);
    Token mismatch = first;
    mismatch.iteration++;
    CHECK(pipeline.complete(5, mismatch) == Result::MismatchedToken);
    CHECK(pipeline.complete(5, first) == Result::Accepted);
    CHECK(pipeline.recordWait(5) == Result::Accepted);
    CHECK(pipeline.recordWait(5) == Result::Accepted);
    CHECK(pipeline.counters().waitCycles == 1);
    CHECK(pipeline.complete(5, second) == Result::Accepted);
    CHECK(pipeline.complete(5, second) == Result::StaleToken);
    std::vector<Token> ready;
    CHECK(pipeline.collectReady(4, ready) ==
          Result::NonMonotonicCycle);
    CHECK(pipeline.finish(8) == Result::StaleToken);
    CHECK(pipeline.finish(7) == Result::Accepted);
}

} // anonymous namespace

int
main()
{
    testConfigurationAndExactLatency();
    testPipelinedWidthAnd65536WordClosure();
    testStaleMismatchCapacityAndTerminalFailures();
    std::cout << "virtual-combine lookup pipeline tests passed\n";
    return 0;
}
