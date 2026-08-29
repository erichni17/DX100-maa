#include <cstdint>
#include <cstdlib>
#include <iostream>

#include "mem/MAA/CompleteLineDrainBudget.hh"

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

using Budget = gem5::maa::CompleteLineDrainBudget;

void
testLegalConfigurations()
{
    for (uint32_t limit : {0U, 1U, 2U, 4U, 8U}) {
        Budget budget;
        CHECK(Budget::validIssueWidth(limit));
        CHECK(budget.configure(limit));
        CHECK(budget.limit() == limit);
    }
    for (uint32_t limit : {3U, 5U, 7U, 9U, 16U}) {
        Budget budget;
        CHECK(!Budget::validIssueWidth(limit));
        CHECK(!budget.configure(limit));
    }
}

void
testFiniteWidthsAndCycleIdentity()
{
    for (uint32_t limit : {1U, 2U, 4U, 8U}) {
        Budget budget;
        CHECK(budget.configure(limit));
        for (uint32_t issued = 0; issued < limit; ++issued) {
            CHECK(budget.available(100));
            CHECK(budget.recordIssue(100));
        }
        CHECK(budget.issuedInCycle() == limit);
        CHECK(!budget.available(100));
        CHECK(!budget.available(100));
        CHECK(budget.counters().stallCycles == 1);

        CHECK(budget.available(101));
        CHECK(budget.issuedInCycle() == 0);
        CHECK(budget.recordIssue(101));
        CHECK(budget.counters().issuedLines == limit + 1);
        CHECK(budget.counters().peakLinesPerCycle == limit);
    }
}

void
testUnlimitedAndNonConsumingProbe()
{
    Budget budget;
    CHECK(budget.configure(0));
    for (uint32_t line = 0; line < 300; ++line) {
        CHECK(budget.available(77));
        CHECK(budget.recordIssue(77));
    }
    CHECK(budget.counters().issuedLines == 300);
    CHECK(budget.counters().stallCycles == 0);
    CHECK(budget.counters().peakLinesPerCycle == 300);

    CHECK(budget.configure(1));
    CHECK(budget.available(9));
    CHECK(budget.available(9));
    CHECK(budget.issuedInCycle() == 0);
    CHECK(budget.recordIssue(9));
    CHECK(!budget.recordIssue(9));
    CHECK(!budget.available(9));
    CHECK(budget.counters().issuedLines == 1);
    CHECK(budget.counters().stallCycles == 1);
}

void
testResetPreservesConfiguration()
{
    Budget budget;
    CHECK(budget.configure(2));
    CHECK(budget.recordIssue(1));
    CHECK(budget.recordIssue(1));
    CHECK(!budget.available(1));
    budget.reset();
    CHECK(budget.limit() == 2);
    CHECK(budget.counters().issuedLines == 0);
    CHECK(budget.counters().stallCycles == 0);
    CHECK(budget.counters().peakLinesPerCycle == 0);
    CHECK(budget.available(1));
    CHECK(budget.recordIssue(1));
}

} // anonymous namespace

int
main()
{
    testLegalConfigurations();
    testFiniteWidthsAndCycleIdentity();
    testUnlimitedAndNonConsumingProbe();
    testResetPreservesConfiguration();
    std::cout << "complete-line drain budget tests passed\n";
    return 0;
}
