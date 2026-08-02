#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>

#include "mem/LANLMAA/UmtOrderedWaveDescriptor.hh"

using namespace gem5::lanlmaa;

namespace
{

uint64_t
bits(double value)
{
    uint64_t word;
    std::memcpy(&word, &value, sizeof(word));
    return word;
}

void
put(std::array<uint8_t, UmtOrderedWaveDescriptorBytes> &bytes,
    size_t offset, uint64_t value, size_t width)
{
    for (size_t byte = 0; byte < width; ++byte) {
        bytes[offset + byte] = (value >> (8 * byte)) & 0xff;
    }
}

std::array<uint8_t, UmtOrderedWaveDescriptorBytes>
validBytes()
{
    std::array<uint8_t, UmtOrderedWaveDescriptorBytes> bytes{};
    put(bytes, 0, DescriptorMagic, 4);
    put(bytes, 4, UmtOrderedWaveDescriptorVersion, 2);
    put(bytes, 6, UmtOrderedWaveOpcode, 1);
    put(bytes, 7, UmtOrderedWaveEightCornerFlag, 1);
    put(bytes, 8, 32, 4);
    put(bytes, 12, UmtOrderedWaveRecordBytes, 4);
    put(bytes, 16, 0x1000, 8);
    put(bytes, 24, 0x4000, 8);
    put(bytes, 32, 0x5000, 8);
    put(bytes, 48, UmtOrderedWaveAbiFingerprint, 8);
    put(bytes, 56, UmtOrderedWaveCorners, 4);
    put(bytes, 60, UmtOrderedWaveCoefficients, 4);
    for (size_t corner = 0;
         corner + 1 < UmtOrderedWaveCorners; ++corner) {
        put(bytes,
            64 + 8 * umtOrderedWaveCoefficientIndex(corner, corner + 1),
            bits(0.25 + corner), 8);
    }
    put(bytes, 64 + 8 * umtOrderedWaveCoefficientIndex(0, 3),
        bits(0.5), 8);
    put(bytes, 64 + 8 * umtOrderedWaveCoefficientIndex(1, 4),
        bits(0.75), 8);
    put(bytes, 64 + 8 * umtOrderedWaveCoefficientIndex(2, 6),
        bits(1.25), 8);
    put(bytes, 64 + 8 * umtOrderedWaveCoefficientIndex(3, 7),
        bits(1.5), 8);
    return bytes;
}

} // anonymous namespace

int
main()
{
    auto bytes = validBytes();
    auto decoded = decodeUmtOrderedWaveDescriptor(bytes);
    assert(decoded);
    const auto counts =
        umtOrderedWaveDependencyDag(decoded.descriptor).counts();
    assert(counts.divide == 8);
    assert(counts.multiply == 11);
    assert(counts.addSub == 19);
    const auto schedule = umtOrderedWaveSchedule(decoded.descriptor);
    assert(schedule);
    assert(schedule.operations.divide == 256);
    assert(schedule.operations.multiply == 352);
    assert(schedule.operations.addSub == 608);

    UmtOrderedWaveRecord record;
    for (size_t corner = 0; corner < UmtOrderedWaveCorners; ++corner) {
        record.source[corner] = 1.0 + corner;
        record.sumArea[corner] = 2.0;
        record.sigtVolume[corner] = 3.0;
    }
    auto scalar = record;
    std::array<double, UmtOrderedWaveCorners> expected{};
    for (size_t corner = 0; corner < UmtOrderedWaveCorners; ++corner) {
        expected[corner] = scalar.source[corner] /
            (scalar.sumArea[corner] + scalar.sigtVolume[corner]);
        for (size_t destination = corner + 1;
             destination < UmtOrderedWaveCorners; ++destination) {
            scalar.source[destination] +=
                decoded.descriptor.coefficients[
                    umtOrderedWaveCoefficientIndex(corner, destination)] *
                expected[corner];
        }
    }
    const auto result =
        executeUmtOrderedWave(decoded.descriptor, record);
    assert(result);
    for (size_t corner = 0; corner < UmtOrderedWaveCorners; ++corner) {
        assert(std::memcmp(
                   &result.flux[corner], &expected[corner],
                   sizeof(double)) == 0);
    }

    bytes = validBytes();
    put(bytes, 8, 0, 4);
    assert(decodeUmtOrderedWaveDescriptor(bytes).error ==
           DescriptorError::Empty);
    bytes = validBytes();
    put(bytes, 12, 128, 4);
    assert(decodeUmtOrderedWaveDescriptor(bytes).error ==
           DescriptorError::BadRecordGeometry);
    bytes = validBytes();
    bytes[300] = 1;
    assert(decodeUmtOrderedWaveDescriptor(bytes).error ==
           DescriptorError::ReservedNonzero);
    bytes = validBytes();
    put(bytes, 64, UINT64_C(0x7ff8000000000000), 8);
    assert(decodeUmtOrderedWaveDescriptor(bytes).error ==
           DescriptorError::BadRecordValue);
    return 0;
}
