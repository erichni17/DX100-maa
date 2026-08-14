#include <array>
#include <cstddef>
#include <cstdlib>
#include <iostream>

#include "mem/MAA/SoaJitSafety.hh"

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

using gem5::SoaJitSafety;

void
testFp32TypedAlignment()
{
    CHECK(SoaJitSafety::typedOperandsAligned(
        0x103c, 0x203c, 0x303c, 0x403c, sizeof(float)));
    CHECK(!SoaJitSafety::typedOperandsAligned(
        0x103e, 0x203c, 0x303c, 0x403c, sizeof(float)));
    CHECK(!SoaJitSafety::typedOperandsAligned(
        0x103c, 0x203e, 0x303c, 0x403c, sizeof(float)));
}

void
testFp64TypedAlignment()
{
    CHECK(SoaJitSafety::typedOperandsAligned(
        0x1038, 0x2038, 0x303c, 0x403c, sizeof(double)));
    CHECK(!SoaJitSafety::typedOperandsAligned(
        0x103c, 0x2038, 0x303c, 0x403c, sizeof(double)));
    CHECK(!SoaJitSafety::typedOperandsAligned(
        0x1038, 0x203c, 0x303c, 0x403c, sizeof(double)));
}

void
testUint32MetadataAlignment()
{
    CHECK(SoaJitSafety::typedOperandsAligned(
        0x1000, 0x2000, 0x303c, 0, sizeof(float)));
    CHECK(!SoaJitSafety::typedOperandsAligned(
        0x1000, 0x2000, 0x303e, 0x403c, sizeof(float)));
    CHECK(!SoaJitSafety::typedOperandsAligned(
        0x1000, 0x2000, 0x303c, 0x403e, sizeof(float)));
}

void
testOnlySupportedTypedWidths()
{
    CHECK(SoaJitSafety::CompletionTokenBytes == sizeof(uint32_t));
    CHECK(SoaJitSafety::LocalLineBytes == 64);
    CHECK(!SoaJitSafety::typedOperandsAligned(
        0x1000, 0x2000, 0x3000, 0x4000, 2));
    CHECK(!SoaJitSafety::typedOperandsAligned(
        0x1000, 0x2000, 0x3000, 0x4000, 16));
}

void
testCompletionTokenDoesNotTouchSecondTile()
{
    std::array<unsigned int, 2> ownership{};
    std::array<unsigned int, 2> status_updates{};
    const std::size_t tiles =
        SoaJitSafety::CompletionTokenBytes / sizeof(uint32_t);
    for (std::size_t tile = 0; tile < tiles; ++tile) {
        ownership[tile]++;
        status_updates[tile]++;
    }
    CHECK(tiles == 1);
    CHECK(ownership[0] == 1);
    CHECK(status_updates[0] == 1);
    CHECK(ownership[1] == 0);
    CHECK(status_updates[1] == 0);
}

void
testMaskedIndexMarkerAdmissionPreservesLegalIndices()
{
    constexpr uint64_t base = 0x1000;
    constexpr uint64_t word_bytes = sizeof(float);
    constexpr uint64_t marker = SoaJitSafety::MaskedIndexInactive;
    CHECK(SoaJitSafety::MaskedIndexModeTag == UINT64_MAX);
    CHECK(marker == UINT32_MAX);
    CHECK(SoaJitSafety::maskedIndexMarkerOutsideLegalRange(
        base, base, base + marker * word_bytes, word_bytes));
    CHECK(!SoaJitSafety::maskedIndexMarkerOutsideLegalRange(
        base, base, base + (marker + 1) * word_bytes, word_bytes));
    CHECK(!SoaJitSafety::maskedIndexMarkerOutsideLegalRange(
        base - 4, base, base + 4096, word_bytes));
    CHECK(SoaJitSafety::MaskedIndexCompareBits == 32);
    CHECK(SoaJitSafety::MaskedIndexModeStateBits == 1);
    CHECK(SoaJitSafety::MaskedIndexAdditionalBufferBytes == 0);
}

} // anonymous namespace

int
main()
{
    testFp32TypedAlignment();
    testFp64TypedAlignment();
    testUint32MetadataAlignment();
    testOnlySupportedTypedWidths();
    testCompletionTokenDoesNotTouchSecondTile();
    testMaskedIndexMarkerAdmissionPreservesLegalIndices();
    return 0;
}
