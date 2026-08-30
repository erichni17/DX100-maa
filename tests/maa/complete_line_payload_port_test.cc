#include <cstdint>
#include <cstdlib>
#include <iostream>

#include "mem/MAA/CompleteLinePayloadPort.hh"

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

using Port = gem5::maa::CompleteLinePayloadPort;
using Identity = Port::Identity;
using Result = Port::Result;

Identity
identity(uint64_t operation, uint64_t line, uint32_t slot)
{
    return Identity{operation, line, slot};
}

void
testConfigurationAndDisabledNeutrality()
{
    for (uint32_t width : {0U, 1U, 2U, 4U, 8U})
        CHECK(Port::validWidth(width));
    for (uint32_t width : {3U, 5U, 7U, 16U})
        CHECK(!Port::validWidth(width));
    CHECK(Port::validLineWords(8));
    CHECK(Port::validLineWords(16));
    CHECK(!Port::validLineWords(4));

    Port port;
    CHECK(port.configure(3, 2) == Result::InvalidConfiguration);
    CHECK(port.configure(1, 0) == Result::InvalidConfiguration);
    CHECK(port.configure(0, 0) == Result::Accepted);
    CHECK(!port.enabled() && port.capacity() == 0);
    CHECK(port.begin(1, 16) == Result::Disabled);
    Port::ReadGrant grant;
    CHECK(port.access(0, identity(1, 1, 0), grant) == Result::Disabled);
    CHECK(port.recordReadyRetry(0, identity(1, 1, 0)) ==
          Result::Disabled);
    CHECK(port.issue(identity(1, 1, 0)) == Result::Disabled);
    CHECK(port.finish(1) == Result::Disabled);
    CHECK(port.counters().starts == 0);
    CHECK(port.counters().wordReads == 0);
    CHECK(port.counters().issuedLines == 0);
}

void
testExactLineLatencyAndReadBounds()
{
    for (uint32_t line_words : {8U, 16U}) {
        for (uint32_t width : {1U, 2U, 4U, 8U}) {
            Port port;
            CHECK(port.configure(width, 4) == Result::Accepted);
            CHECK(port.begin(11, line_words) == Result::Accepted);
            const Identity line = identity(11, 19, 2);
            const uint32_t cycles = (line_words + width - 1) / width;
            uint32_t observed_reads = 0;
            for (uint32_t cycle = 0; cycle < cycles; ++cycle) {
                Port::ReadGrant grant;
                CHECK(port.access(100 + cycle, line, grant) ==
                      Result::Accepted);
                CHECK(grant.started == (cycle == 0));
                CHECK(grant.firstWord == observed_reads);
                CHECK(grant.words <= width);
                observed_reads += grant.words;
                CHECK(!grant.ready);
            }
            CHECK(observed_reads == line_words);
            Port::ReadGrant ready;
            CHECK(port.access(100 + cycles, line, ready) ==
                  Result::Accepted);
            CHECK(ready.words == 0 && ready.ready);
            CHECK(port.counters().starts == 1);
            CHECK(port.counters().wordReads == line_words);
            CHECK(port.counters().readCycles == cycles);
            CHECK(port.counters().peakStartsPerCycle == 1);
            CHECK(port.counters().peakWordReadsPerCycle == width);
            CHECK(port.counters().readyLines == 1);
            CHECK(port.issue(line) == Result::Accepted);
            CHECK(port.empty());
            CHECK(port.finish(11) == Result::Accepted);
        }
    }
}

void
testSharedWidthBoundAndReadyRetryProgress()
{
    Port port;
    CHECK(port.configure(4, 3) == Result::Accepted);
    CHECK(port.begin(27, 8) == Result::Accepted);
    const Identity first = identity(27, 1, 0);
    const Identity second = identity(27, 2, 1);
    Port::ReadGrant grant;
    CHECK(port.access(10, first, grant) == Result::Accepted);
    CHECK(grant.started && grant.words == 4 && !grant.ready);
    CHECK(port.access(10, second, grant) == Result::NoBandwidth);
    CHECK(!grant.started && grant.words == 0);

    CHECK(port.access(11, first, grant) == Result::Accepted);
    CHECK(!grant.started && grant.words == 4 && !grant.ready);
    CHECK(port.access(11, second, grant) == Result::NoBandwidth);

    CHECK(port.access(12, first, grant) == Result::Accepted);
    CHECK(grant.ready && grant.words == 0);
    CHECK(port.recordReadyRetry(12, first) == Result::Accepted);
    CHECK(port.recordReadyRetry(12, first) == Result::Accepted);
    CHECK(port.counters().readyRetryCycles == 1);
    CHECK(port.access(12, second, grant) == Result::Accepted);
    CHECK(grant.started && grant.words == 4 && !grant.ready);
    CHECK(port.issue(first) == Result::Accepted);
    CHECK(port.access(13, second, grant) == Result::Accepted);
    CHECK(grant.words == 4 && !grant.ready);
    CHECK(port.access(14, second, grant) == Result::Accepted);
    CHECK(grant.words == 0 && grant.ready);
    CHECK(port.issue(second) == Result::Accepted);
    CHECK(port.counters().starts == 2);
    CHECK(port.counters().wordReads == 16);
    CHECK(port.counters().peakWordReadsPerCycle == 4);
    CHECK(port.counters().peakActiveLines == 2);
    CHECK(port.finish(27) == Result::Accepted);
}

void
testExactIdentityAndTerminalFailures()
{
    Port port;
    CHECK(port.configure(8, 2) == Result::Accepted);
    CHECK(port.begin(31, 16) == Result::Accepted);
    const Identity line = identity(31, 7, 1);
    Port::ReadGrant grant;
    CHECK(port.access(3, line, grant) == Result::Accepted);
    CHECK(port.finish(31) == Result::Imbalance);

    CHECK(port.access(3, identity(31, 8, 1), grant) ==
          Result::MismatchedIdentity);
    CHECK(port.access(3, identity(30, 7, 1), grant) ==
          Result::StaleIdentity);
    CHECK(port.access(3, identity(31, 7, 2), grant) ==
          Result::InvalidIdentity);
    CHECK(port.issue(line) == Result::NotReady);
    CHECK(port.recordReadyRetry(3, line) == Result::NotReady);
    CHECK(port.access(2, line, grant) == Result::NonMonotonicCycle);

    CHECK(port.access(4, line, grant) == Result::Accepted);
    CHECK(!grant.ready);
    CHECK(port.access(5, line, grant) == Result::Accepted);
    CHECK(grant.ready);
    CHECK(port.issue(identity(31, 8, 1)) == Result::MismatchedIdentity);
    CHECK(port.issue(line) == Result::Accepted);
    CHECK(port.issue(line) == Result::StaleIdentity);
    CHECK(port.finish(30) == Result::StaleIdentity);
    CHECK(port.finish(31) == Result::Accepted);
}

} // anonymous namespace

int
main()
{
    testConfigurationAndDisabledNeutrality();
    testExactLineLatencyAndReadBounds();
    testSharedWidthBoundAndReadyRetryProgress();
    testExactIdentityAndTerminalFailures();
    std::cout << "complete-line payload port tests passed\n";
    return 0;
}
