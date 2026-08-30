#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>

#include "mem/MAA/VirtualBackingOwnership.hh"

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

using Ownership = gem5::maa::VirtualBackingOwnership;

void
testSpansAndOverlap()
{
    uint64_t end = 0;
    CHECK(Ownership::span(0x1000, 16384, 8, end));
    CHECK(end == 0x21000);
    CHECK(Ownership::overlaps(0x1000, end, 0x2000, 0x3000));
    CHECK(!Ownership::overlaps(0x1000, end, end, end + 64));
    CHECK(Ownership::overlaps(0x1000, end, end - 1, end + 64));
}

void
testInvalidAndWrappedSpans()
{
    uint64_t end = 0;
    CHECK(!Ownership::span(0, 0, 8, end));
    CHECK(!Ownership::span(0, 1, 0, end));
    CHECK(!Ownership::span(
        0, std::numeric_limits<uint64_t>::max(), 8, end));
    CHECK(!Ownership::span(
        std::numeric_limits<uint64_t>::max() - 3, 1, 8, end));
    CHECK(!Ownership::overlaps(5, 5, 0, 10));
}

} // anonymous namespace

int
main()
{
    testSpansAndOverlap();
    testInvalidAndWrappedSpans();
    std::cout << "virtual backing ownership tests passed\n";
    return 0;
}
