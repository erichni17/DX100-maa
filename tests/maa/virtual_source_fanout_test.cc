#include <cstdint>
#include <cstdlib>
#include <iostream>

#include "mem/MAA/VirtualSourceFanout.hh"

using Fanout = gem5::maa::VirtualSourceFanout;
using Result = Fanout::Result;

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;         \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

static void
checkDuplicateFinalUseAndRollback()
{
    Fanout fanout;
    CHECK(fanout.reset(16) == Result::Accepted);
    CHECK(fanout.observe(3) == Result::Accepted);
    CHECK(fanout.observe(7) == Result::Accepted);
    CHECK(fanout.observe(3) == Result::Accepted);
    CHECK(fanout.observe(3) == Result::Accepted);
    CHECK(fanout.seal(4) == Result::Accepted);
    CHECK(fanout.logicalUses() == 4);
    CHECK(fanout.payloadWords() == 2);
    CHECK(fanout.scanCycles() == 1);

    bool final = true;
    CHECK(fanout.consume(3, final) == Result::Accepted && !final);
    CHECK(fanout.consume(7, final) == Result::Accepted && final);
    CHECK(fanout.rollback(7) == Result::Accepted);
    CHECK(fanout.consume(7, final) == Result::Accepted && final);
    CHECK(fanout.consume(3, final) == Result::Accepted && !final);
    CHECK(fanout.consume(3, final) == Result::Accepted && final);
    CHECK(fanout.empty());
    CHECK(fanout.consume(3, final) == Result::Exhausted);
}

static void
checkMaximumLogicalFanout()
{
    Fanout fanout;
    CHECK(fanout.reset(8) == Result::Accepted);
    for (uint32_t use = 0; use < Fanout::MaxLogicalUses; ++use)
        CHECK(fanout.observe(0) == Result::Accepted);
    CHECK(fanout.observe(0) == Result::Overflow);
    CHECK(fanout.seal(Fanout::MaxLogicalUses) == Result::Accepted);
    CHECK(fanout.payloadWords() == 1);
    CHECK(fanout.scanCycles() ==
          Fanout::MaxLogicalUses / Fanout::ScanWidth);
    bool final = false;
    for (uint32_t use = 0; use < Fanout::MaxLogicalUses; ++use) {
        CHECK(fanout.consume(0, final) == Result::Accepted);
        CHECK(final == (use + 1 == Fanout::MaxLogicalUses));
    }
    CHECK(fanout.empty());
}

static void
checkFailuresAreClosed()
{
    Fanout fanout;
    CHECK(fanout.reset(0) == Result::InvalidGeometry);
    CHECK(fanout.reset(17) == Result::InvalidGeometry);
    CHECK(fanout.reset(16) == Result::Accepted);
    CHECK(fanout.observe(16) == Result::InvalidWord);
    CHECK(fanout.observe(1) == Result::Accepted);
    CHECK(fanout.seal(2) == Result::CountMismatch);
    CHECK(fanout.seal(1) == Result::Accepted);
    CHECK(fanout.observe(1) == Result::AlreadySealed);
    CHECK(fanout.seal(1) == Result::AlreadySealed);
    bool final = false;
    CHECK(fanout.consume(16, final) == Result::InvalidWord);
    CHECK(fanout.rollback(1) == Result::Overflow);
}

int
main()
{
    checkDuplicateFinalUseAndRollback();
    checkMaximumLogicalFanout();
    checkFailuresAreClosed();
    std::cout << "PASS virtual source fanout\n";
    return 0;
}
