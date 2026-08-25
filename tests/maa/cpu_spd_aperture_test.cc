#include <cstdlib>
#include <iostream>

#include "mem/MAA/CpuSpdAperture.hh"

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

using Aperture = gem5::maa::CpuSpdAperture;
using Disposition = Aperture::Disposition;

constexpr uint64_t LineBytes = 64;
constexpr uint64_t LogicalElements = 16384;
constexpr uint64_t PhysicalElements = 4096;
constexpr uint64_t ElementBytes = 4;
constexpr uint64_t LogicalBytes = LogicalElements * ElementBytes;
constexpr uint64_t PhysicalBytes = PhysicalElements * ElementBytes;

Aperture::Decision
classify(uint64_t offset, bool speculative, uint64_t packet_bytes = LineBytes,
         uint64_t line_bytes = LineBytes,
         uint64_t logical_elements = LogicalElements,
         uint64_t physical_elements = PhysicalElements)
{
    return Aperture::classify(
        offset, packet_bytes, line_bytes, logical_elements,
        physical_elements, ElementBytes, speculative);
}

void
testValidLastPhysicalLine()
{
    const auto decision = classify(PhysicalBytes - LineBytes, false);
    CHECK(decision.disposition == Disposition::Valid);
    CHECK(decision.tile == 0);
    CHECK(decision.tileOffset == PhysicalBytes - LineBytes);

    const auto second_tile = classify(
        LogicalBytes + PhysicalBytes - LineBytes, false);
    CHECK(second_tile.disposition == Disposition::Valid);
    CHECK(second_tile.tile == 1);
}

void
testElement4096Policy()
{
    const auto speculative = classify(PhysicalBytes, true);
    CHECK(speculative.disposition == Disposition::DropBoundaryPrefetch);
    CHECK(speculative.tileOffset == PhysicalBytes);

    const auto architectural = classify(PhysicalBytes, false);
    CHECK(architectural.disposition == Disposition::PhysicalOutOfRange);
    CHECK(architectural.tileOffset == PhysicalBytes);
}

void
testCrossingAndInvalidGeometryFailClosed()
{
    CHECK(classify(PhysicalBytes - 32, true, LineBytes, 32).disposition ==
          Disposition::InvalidGeometry);
    CHECK(classify(LogicalBytes - 32, true, LineBytes, 32).disposition ==
          Disposition::InvalidGeometry);
    CHECK(classify(PhysicalBytes, true, 32).disposition ==
          Disposition::InvalidGeometry);
    CHECK(classify(PhysicalBytes, true, LineBytes, LineBytes,
                   LogicalElements, LogicalElements + 1).disposition ==
          Disposition::InvalidGeometry);
    CHECK(Aperture::classify(
              PhysicalBytes, LineBytes, LineBytes, LogicalElements,
              PhysicalElements, 0, true).disposition ==
          Disposition::InvalidGeometry);

    // Deliberately use a geometry where the physical boundary is not a
    // cache-line boundary to exercise the crossing disposition directly.
    CHECK(classify(PhysicalBytes - LineBytes, true, LineBytes, LineBytes,
                   LogicalElements, PhysicalElements - 8).disposition ==
          Disposition::CrossesPhysicalPayload);

    constexpr uint64_t odd_logical_elements = LogicalElements - 8;
    const uint64_t odd_logical_bytes = odd_logical_elements * ElementBytes;
    const uint64_t crossing_offset =
        odd_logical_bytes & ~(LineBytes - 1);
    CHECK(classify(crossing_offset, true, LineBytes, LineBytes,
                   odd_logical_elements, PhysicalElements).disposition ==
          Disposition::CrossesLogicalTile);
}

} // anonymous namespace

int
main()
{
    testValidLastPhysicalLine();
    testElement4096Policy();
    testCrossingAndInvalidGeometryFailClosed();
    std::cout << "CPU_SPD_APERTURE_UNIT_PASS\n";
    return 0;
}
