#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>

#include "mem/LANLMAA/UmtOrderedWaveDescriptor.hh"

using namespace gem5::lanlmaa;

static_assert(UmtOrderedWaveDescriptorBytes == 4 * DescriptorBytes);

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
validBytes(uint16_t version = UmtOrderedWaveD64DescriptorVersion)
{
    std::array<uint8_t, UmtOrderedWaveDescriptorBytes> bytes{};
    const bool d32 = version == UmtOrderedWaveD32DescriptorVersion;
    put(bytes, 0, DescriptorMagic, 4);
    put(bytes, 4, version, 2);
    put(bytes, 6, UmtOrderedWaveOpcode, 1);
    put(bytes, 7, UmtOrderedWaveFlags, 1);
    put(bytes, 8, d32 ? UmtOrderedWaveD32MaximumGroups :
        UmtOrderedWaveMaximumGroups, 4);
    put(bytes, 12, d32 ? UmtOrderedWaveD32PlaneStride :
        UmtOrderedWavePlaneStride, 4);
    put(bytes, 16, 0x1000, 8);
    put(bytes, 24, 0x4000, 8);
    put(bytes, 32, 0x5000, 8);
    put(bytes, 48, d32 ? UmtOrderedWaveD32AbiFingerprint :
        UmtOrderedWaveAbiFingerprint, 8);
    put(bytes, 56, UmtOrderedWaveCorners, 4);
    std::array<double, UmtOrderedWaveDenseCoefficients> coefficients{};
    for (size_t corner = 0;
         corner + 1 < UmtOrderedWaveCorners; ++corner) {
        coefficients[
            umtOrderedWaveCoefficientIndex(corner, corner + 1)] =
            0.25 + corner;
    }
    coefficients[umtOrderedWaveCoefficientIndex(0, 3)] = 0.5;
    coefficients[umtOrderedWaveCoefficientIndex(1, 4)] = 0.75;
    coefficients[umtOrderedWaveCoefficientIndex(2, 6)] = 1.25;
    coefficients[umtOrderedWaveCoefficientIndex(3, 7)] = 1.5;
    uint32_t edgeMask = 0;
    uint32_t edgeCount = 0;
    for (size_t index = 0; index < coefficients.size(); ++index) {
        if (coefficients[index] == 0.0)
            continue;
        edgeMask |= uint32_t{1} << index;
        put(bytes, 72 + edgeCount * sizeof(uint64_t),
            bits(coefficients[index]), sizeof(uint64_t));
        ++edgeCount;
    }
    put(bytes, 60, edgeCount, 4);
    put(bytes, 64, edgeMask, 4);
    for (size_t corner = 0; corner < UmtOrderedWaveCorners; ++corner) {
        put(bytes, UmtOrderedWaveSumAreaOffset +
                corner * sizeof(uint64_t),
            bits(2.0), sizeof(uint64_t));
    }
    return bytes;
}

} // anonymous namespace

int
main()
{
    assert(umtOrderedWaveWordsToLineBoundary(0x1000, 16, 64) == 8);
    assert(umtOrderedWaveWordsToLineBoundary(0x1038, 16, 64) == 1);
    assert(umtOrderedWaveWordsToLineBoundary(0x1040, 7, 64) == 7);
    assert(umtOrderedWaveWordsToLineBoundary(0x1004, 24, 64) == 0);

    auto bytes = validBytes();
    auto decoded = decodeUmtOrderedWaveDescriptor(bytes);
    assert(decoded);
    assert(decoded.descriptor.abiVersion ==
           UmtOrderedWaveD64DescriptorVersion);
    const auto counts =
        umtOrderedWaveDependencyDag(decoded.descriptor).counts();
    assert(counts.divide == 8);
    assert(counts.multiply == 11);
    assert(counts.addSub == 19);
    const auto resources = umtOrderedWaveResources();
    assert(resources.globalIssueWidth == 1);
    assert(resources.addSubUnits == 1);
    assert(resources.multiplyUnits == 1);
    assert(resources.divideUnits == 8);
    assert(resources.divideLatency == 64);
    assert(resources.divideInitiationInterval == 32);
    const auto schedule = umtOrderedWaveSchedule(decoded.descriptor);
    assert(schedule);
    assert(schedule.operations.divide == 512);
    assert(schedule.operations.multiply == 704);
    assert(schedule.operations.addSub == 1216);

    UmtOrderedWaveCompletionCursor cursor;
    assert(cursor.advance(8, UmtOrderedWaveMaximumGroups));
    assert(cursor.group == 8 && cursor.corner == 0 && !cursor.complete());
    assert(cursor.advance(56, UmtOrderedWaveMaximumGroups));
    assert(cursor.group == 0 && cursor.corner == 1 && !cursor.complete());
    for (size_t corner = 1; corner < UmtOrderedWaveCorners; ++corner) {
        assert(cursor.advance(UmtOrderedWaveMaximumGroups,
                              UmtOrderedWaveMaximumGroups));
    }
    assert(cursor.group == UmtOrderedWaveMaximumGroups);
    assert(cursor.complete());
    assert(!cursor.advance(1, UmtOrderedWaveMaximumGroups));

    auto d32Bytes = validBytes(UmtOrderedWaveD32DescriptorVersion);
    auto d32Decoded = decodeUmtOrderedWaveDescriptor(d32Bytes);
    assert(d32Decoded);
    assert(d32Decoded.descriptor.abiVersion ==
           UmtOrderedWaveD32DescriptorVersion);
    assert(d32Decoded.descriptor.groupCount ==
           UmtOrderedWaveD32MaximumGroups);
    assert(d32Decoded.descriptor.recordStride ==
           UmtOrderedWaveD32PlaneStride);
    put(d32Bytes, 8, UmtOrderedWaveD32MaximumGroups + 1, 4);
    assert(decodeUmtOrderedWaveDescriptor(d32Bytes).error ==
           DescriptorError::TooManyItems);

    UmtOrderedWaveRecord record;
    for (size_t corner = 0; corner < UmtOrderedWaveCorners; ++corner) {
        record.source[corner] = 1.0 + corner;
        record.sigtVolume[corner] = 3.0;
    }
    auto scalar = record;
    std::array<double, UmtOrderedWaveCorners> expected{};
    for (size_t corner = 0; corner < UmtOrderedWaveCorners; ++corner) {
        expected[corner] = scalar.source[corner] /
            (decoded.descriptor.sumArea[corner] +
             scalar.sigtVolume[corner]);
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

    UmtOrderedWaveDescriptor lineDescriptor;
    lineDescriptor.recordBase = 0x2008;
    lineDescriptor.recordStride = UmtOrderedWavePlaneStride;
    lineDescriptor.groupCount = 64;
    assert(umtOrderedWaveExpectedLineWaiters(
               lineDescriptor, 0, 0x2000, 64) == 7);
    assert(umtOrderedWaveExpectedLineWaiters(
               lineDescriptor, 0, 0x2040, 64) == 8);
    assert(umtOrderedWaveExpectedLineWaiters(
               lineDescriptor, 0, 0x2200, 64) == 1);
    lineDescriptor.groupCount = 33;
    assert(umtOrderedWaveExpectedLineWaiters(
               lineDescriptor, 3, 0x2600, 64) == 7);
    assert(umtOrderedWaveExpectedLineWaiters(
               lineDescriptor, 3, 0x2640, 64) == 8);
    assert(umtOrderedWaveExpectedLineWaiters(
               lineDescriptor, 3, 0x2700, 64) == 2);
    assert(umtOrderedWaveExpectedLineWaiters(
               lineDescriptor, UmtOrderedWaveRecordFp64Words,
               0x2000, 64) == 0);
    assert(umtOrderedWaveExpectedLineWaiters(
               lineDescriptor, 0, 0x2008, 64) == 0);

    bytes = validBytes();
    put(bytes, 8, UmtOrderedWaveMaximumGroups + 1, 4);
    assert(decodeUmtOrderedWaveDescriptor(bytes).error ==
           DescriptorError::TooManyItems);
    bytes = validBytes();
    put(bytes, 4, UmtOrderedWaveD32DescriptorVersion - 1, 2);
    assert(decodeUmtOrderedWaveDescriptor(bytes).error ==
           DescriptorError::BadVersion);
    bytes = validBytes();
    put(bytes, 48, UmtOrderedWaveAbiFingerprint ^ 1, 8);
    assert(decodeUmtOrderedWaveDescriptor(bytes).error ==
           DescriptorError::BadRecordValue);
    bytes = validBytes();
    put(bytes, 24, 0x2ff8, 8);
    assert(decodeUmtOrderedWaveDescriptor(bytes).error ==
           DescriptorError::OverlappingInput);
    bytes = validBytes();
    put(bytes, 32, 0x4ff8, 8);
    assert(decodeUmtOrderedWaveDescriptor(bytes).error ==
           DescriptorError::OverlappingOutput);
    bytes = validBytes();
    put(bytes, 8, 0, 4);
    assert(decodeUmtOrderedWaveDescriptor(bytes).error ==
           DescriptorError::Empty);
    bytes = validBytes();
    put(bytes, 12, UmtOrderedWaveRecordBytes, 4);
    assert(decodeUmtOrderedWaveDescriptor(bytes).error ==
           DescriptorError::BadRecordGeometry);
    bytes = validBytes();
    bytes[240] = 1;
    assert(decodeUmtOrderedWaveDescriptor(bytes).error ==
           DescriptorError::ReservedNonzero);
    bytes = validBytes();
    put(bytes, 72, UINT64_C(0x7ff8000000000000), 8);
    assert(decodeUmtOrderedWaveDescriptor(bytes).error ==
           DescriptorError::BadRecordValue);
    bytes = validBytes();
    put(bytes, UmtOrderedWaveSumAreaOffset,
        UINT64_C(0x7ff8000000000000), 8);
    assert(decodeUmtOrderedWaveDescriptor(bytes).error ==
           DescriptorError::BadRecordValue);
    bytes = validBytes();
    put(bytes, 60, 12, 4);
    assert(decodeUmtOrderedWaveDescriptor(bytes).error ==
           DescriptorError::BadRecordValue);
    return 0;
}
