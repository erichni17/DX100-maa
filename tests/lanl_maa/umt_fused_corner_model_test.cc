#include <array>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <limits>

#include "mem/LANLMAA/UmtFusedCornerModel.hh"
#include "umt_corner_sweep_record.hh"

using namespace gem5::lanlmaa;

namespace
{

void
writeLe32(std::array<uint8_t, UmtFusedCornerDescriptorBytes> &bytes,
          size_t offset, uint32_t value)
{
    for (size_t index = 0; index < sizeof(value); ++index) {
        bytes[offset + index] = value >> (index * 8);
    }
}

void
writeLe64(std::array<uint8_t, UmtFusedCornerDescriptorBytes> &bytes,
          size_t offset, uint64_t value)
{
    for (size_t index = 0; index < sizeof(value); ++index) {
        bytes[offset + index] = value >> (index * 8);
    }
}

void
writeFp64(std::array<uint8_t, UmtFusedCornerDescriptorBytes> &bytes,
          size_t offset, double value)
{
    uint64_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    writeLe64(bytes, offset, bits);
}

std::array<uint8_t, UmtFusedCornerDescriptorBytes>
validDescriptorBytes(uint32_t groups)
{
    std::array<uint8_t, UmtFusedCornerDescriptorBytes> bytes{};
    writeLe32(bytes, 0, DescriptorMagic);
    bytes[4] = UmtFusedCornerDescriptorVersion;
    bytes[5] = UmtFusedCornerDescriptorVersion >> 8;
    bytes[6] = UmtFusedCornerOpcode;
    bytes[7] = UmtFusedDirectThreeFaceFlag;
    writeLe32(bytes, 8, groups);
    writeLe32(bytes, 12, UmtFusedCornerRecordBytes);
    writeLe64(bytes, 16, 0x1000);
    writeLe64(bytes, 24, 0x3000);
    writeLe64(bytes, 32, 0x4000);
    writeFp64(bytes, 40, 0.25);
    writeFp64(bytes, 48, 2.0);
    writeFp64(bytes, 56, 3.0);
    writeFp64(bytes, 64, -1.0);
    writeFp64(bytes, 72, -2.0);
    writeFp64(bytes, 80, -3.0);
    writeFp64(bytes, 88, 0.5);
    writeFp64(bytes, 96, 0.75);
    writeFp64(bytes, 104, 1.0);
    writeLe64(bytes, 112, UmtFusedCornerAbiFingerprint);
    return bytes;
}

void
testDescriptor()
{
    auto bytes = validDescriptorBytes(16);
    auto decoded = decodeUmtFusedCornerDescriptor(bytes);
    assert(decoded);
    assert(decoded.descriptor.groupCount == 16);
    assert(umtFusedCornerBatchCycles(16) == 1819);
    assert(umtFusedCornerBatchCycles(32) == 3595);
    assert(umtFusedCornerBatchCycles(8) == 0);

    bytes = validDescriptorBytes(8);
    assert(decodeUmtFusedCornerDescriptor(bytes).error ==
           DescriptorError::BadRecordGeometry);
    bytes = validDescriptorBytes(33);
    assert(decodeUmtFusedCornerDescriptor(bytes).error ==
           DescriptorError::TooManyItems);
    bytes = validDescriptorBytes(16);
    bytes[7] = 0;
    assert(decodeUmtFusedCornerDescriptor(bytes).error ==
           DescriptorError::UnsupportedFlags);
    bytes = validDescriptorBytes(16);
    writeFp64(bytes, 88, -0.5);
    assert(decodeUmtFusedCornerDescriptor(bytes).error ==
           DescriptorError::BadRecordValue);
    bytes = validDescriptorBytes(16);
    writeLe64(bytes, 24, 0x1000);
    assert(decodeUmtFusedCornerDescriptor(bytes).error ==
           DescriptorError::OverlappingInput);
    bytes = validDescriptorBytes(16);
    writeLe64(bytes, 32, 0x3000);
    assert(decodeUmtFusedCornerDescriptor(bytes).error ==
           DescriptorError::OverlappingOutput);
}

bool
sameBits(double first, double second)
{
    return std::memcmp(&first, &second, sizeof(first)) == 0;
}

void
testNativeRecord(const char *path, uint32_t expectedGroups)
{
    std::ifstream input(path);
    assert(input);
    const auto parsed = parseUmtCornerSweepRecord(input);
    assert(parsed);
    const auto &nativeDescriptor = parsed.record.descriptor;
    const auto &nativeInput = parsed.record.input;
    assert(nativeDescriptor.groupCount == expectedGroups);
    assert(nativeInput.cornerOrder.size() == 1);
    const uint32_t currentIndex = nativeInput.cornerOrder.front();
    const auto &current = nativeInput.corners[currentIndex];
    assert(current.faceCount == 3);

    UmtFusedCornerDescriptor descriptor;
    descriptor.groupCount = expectedGroups;
    descriptor.recordStride = UmtFusedCornerRecordBytes;
    descriptor.tau = nativeDescriptor.tau;
    descriptor.volume = current.volume;
    descriptor.normSum = current.normSum;
    for (size_t face = 0; face < 3; ++face) {
        const auto &nativeFace =
            nativeInput.faces[current.faceOffset + face];
        assert(nativeFace.fpNorm < 0.0);
        assert(nativeFace.ezNorm > 0.0);
        descriptor.fpNorm[face] = nativeFace.fpNorm;
        descriptor.ezNorm[face] = nativeFace.ezNorm;
    }

    for (uint32_t group = 0; group < expectedGroups; ++group) {
        const size_t currentValue =
            static_cast<size_t>(currentIndex) *
            nativeDescriptor.totalGroups + group;
        UmtFusedCornerRecord record;
        record.totalSource = nativeInput.totalSource[currentValue];
        record.oldPsi = nativeInput.oldPsi[currentValue];
        record.crossSection = nativeInput.totalCrossSection[group];
        for (size_t face = 0; face < 3; ++face) {
            const auto &nativeFace =
                nativeInput.faces[current.faceOffset + face];
            const size_t neighborValue =
                static_cast<size_t>(nativeFace.ezCorner) *
                nativeDescriptor.totalGroups + group;
            record.neighborTotalSource[face] =
                nativeInput.totalSource[neighborValue];
            record.neighborOldPsi[face] = nativeInput.oldPsi[neighborValue];
            const size_t fluxValue =
                static_cast<size_t>(nativeFace.fluxPoint) *
                nativeDescriptor.totalGroups + group;
            record.flux[face] = nativeInput.psi1[fluxValue];
        }
        const auto result = executeUmtFusedCorner(descriptor, record);
        assert(result);
        assert(sameBits(result.value, parsed.record.nativeExpected[group]));
        UmtFusedCornerRetained retained;
        retained.source =
            record.totalSource + descriptor.tau * record.oldPsi;
        retained.crossSection = record.crossSection;
        for (size_t face = 0; face < 3; ++face) {
            retained.neighborSource[face] =
                record.neighborTotalSource[face] +
                descriptor.tau * record.neighborOldPsi[face];
            retained.flux[face] = record.flux[face];
        }
        const auto folded =
            executeUmtFusedCornerRetained(descriptor, retained);
        assert(folded);
        assert(sameBits(folded.value, parsed.record.nativeExpected[group]));
    }

    UmtFusedCornerRecord invalid;
    invalid.crossSection = 1.0;
    invalid.flux[1] = std::numeric_limits<double>::infinity();
    assert(executeUmtFusedCorner(descriptor, invalid).error ==
           UmtFusedCornerError::NonfiniteInput);
}

} // anonymous namespace

int
main(int argc, char **argv)
{
    assert(argc == 3);
    testDescriptor();
    testNativeRecord(argv[1], 32);
    testNativeRecord(argv[2], 16);
    return 0;
}
