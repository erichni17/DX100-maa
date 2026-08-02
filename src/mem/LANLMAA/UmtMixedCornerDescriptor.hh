#ifndef __MEM_LANLMAA_UMT_MIXED_CORNER_DESCRIPTOR_HH__
#define __MEM_LANLMAA_UMT_MIXED_CORNER_DESCRIPTOR_HH__

#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>

#include "mem/LANLMAA/UmtFusedCornerDescriptor.hh"
#include "mem/LANLMAA/UmtMixedCornerModel.hh"

namespace gem5
{
namespace lanlmaa
{

constexpr size_t UmtMixedCornerDescriptorBytes = 128;
constexpr uint16_t UmtMixedCornerDescriptorVersion = 3;
constexpr uint8_t UmtMixedCornerOpcode = 10;
constexpr uint8_t UmtMixedEqualVolumesFlag = 1U << 0;
constexpr uint8_t UmtMixedAllSpecialFlag = 1U << 1;
constexpr uint8_t UmtMixedCornerRequiredFlags =
    UmtMixedEqualVolumesFlag | UmtMixedAllSpecialFlag;
constexpr uint8_t UmtMixedThreeFaceMask = 0x7;
constexpr uint32_t UmtMixedCornerMaximumGroups = 32;
constexpr uint64_t UmtMixedCornerAbiFingerprint = 0x8258c44e6c9b3f17ULL;

struct UmtMixedCornerDescriptor
{
    uint32_t groupCount = 0;
    uint32_t recordStride = 0;
    uint64_t recordBase = 0;
    uint64_t resultBase = 0;
    uint64_t completionRecord = 0;
    UmtMixedCornerGeometry geometry;
    uint8_t incomingMask = 0;
    uint8_t incidentMask = 0;
    uint8_t specialMask = 0;
};

struct UmtMixedCornerDescriptorDecodeResult
{
    UmtMixedCornerDescriptor descriptor;
    DescriptorError error = DescriptorError::None;

    explicit operator bool() const
    {
        return error == DescriptorError::None;
    }
};

inline UmtMixedCornerDescriptorDecodeResult
decodeUmtMixedCornerDescriptor(
    const std::array<uint8_t, UmtMixedCornerDescriptorBytes> &bytes)
{
    UmtMixedCornerDescriptorDecodeResult result;
    if (descriptorReadLe32(bytes.data()) != DescriptorMagic) {
        result.error = DescriptorError::BadMagic;
        return result;
    }
    if (descriptorReadLe16(bytes.data() + 4) !=
        UmtMixedCornerDescriptorVersion) {
        result.error = DescriptorError::BadVersion;
        return result;
    }
    if (bytes[6] != UmtMixedCornerOpcode) {
        result.error = DescriptorError::BadOpcode;
        return result;
    }
    if (bytes[7] != UmtMixedCornerRequiredFlags) {
        result.error = DescriptorError::UnsupportedFlags;
        return result;
    }

    auto &descriptor = result.descriptor;
    descriptor.groupCount = descriptorReadLe32(bytes.data() + 8);
    descriptor.recordStride = descriptorReadLe32(bytes.data() + 12);
    if (descriptor.groupCount == 0) {
        result.error = DescriptorError::Empty;
        return result;
    }
    if (descriptor.groupCount > UmtMixedCornerMaximumGroups) {
        result.error = DescriptorError::TooManyItems;
        return result;
    }
    if (descriptor.recordStride != UmtMixedCornerRecordBytes ||
        umtFusedCornerBatchCycles(descriptor.groupCount) == 0) {
        result.error = DescriptorError::BadRecordGeometry;
        return result;
    }

    descriptor.recordBase = descriptorReadLe64(bytes.data() + 16);
    descriptor.resultBase = descriptorReadLe64(bytes.data() + 24);
    descriptor.completionRecord = descriptorReadLe64(bytes.data() + 32);
    auto &geometry = descriptor.geometry;
    geometry.tau = umtFusedCornerDecodeFp64(bytes.data() + 40);
    geometry.currentVolume = umtFusedCornerDecodeFp64(bytes.data() + 48);
    geometry.currentNormSum = umtFusedCornerDecodeFp64(bytes.data() + 56);
    for (size_t face = 0; face < UmtMixedCornerFaceCount; ++face) {
        geometry.currentFpNorm[face] =
            umtFusedCornerDecodeFp64(bytes.data() + 64 + face * 8);
        geometry.signedEzNorm[face] =
            umtFusedCornerDecodeFp64(bytes.data() + 88 + face * 8);
        geometry.firstVolume[face] = geometry.currentVolume;
        geometry.oppositeActive[face] = 1;
    }
    if (descriptorReadLe64(bytes.data() + 112) !=
        UmtMixedCornerAbiFingerprint) {
        result.error = DescriptorError::BadRecordValue;
        return result;
    }
    descriptor.incomingMask = bytes[120];
    descriptor.incidentMask = bytes[121];
    descriptor.specialMask = bytes[122];
    if (bytes[123] != 0 || descriptorReadLe32(bytes.data() + 124) != 0) {
        result.error = DescriptorError::ReservedNonzero;
        return result;
    }
    if ((descriptor.incomingMask & ~UmtMixedThreeFaceMask) != 0 ||
        (descriptor.incidentMask & ~UmtMixedThreeFaceMask) != 0 ||
        descriptor.specialMask != UmtMixedThreeFaceMask) {
        result.error = DescriptorError::BadRecordValue;
        return result;
    }
    for (size_t face = 0; face < UmtMixedCornerFaceCount; ++face) {
        const uint8_t bit = 1U << face;
        if (((geometry.signedEzNorm[face] < 0.0) !=
             ((descriptor.incomingMask & bit) != 0)) ||
            ((geometry.currentFpNorm[face] < 0.0) !=
             ((descriptor.incidentMask & bit) != 0))) {
            result.error = DescriptorError::BadRecordValue;
            return result;
        }
    }
    if (!umtMixedCornerFiniteGeometry(geometry)) {
        result.error = DescriptorError::BadRecordValue;
        return result;
    }

    if (descriptor.recordBase % alignof(uint64_t) != 0 ||
        descriptor.resultBase % alignof(uint64_t) != 0 ||
        descriptor.completionRecord % alignof(uint64_t) != 0) {
        result.error = DescriptorError::MisalignedVector;
        return result;
    }
    std::array<UmtFusedCornerRange, 3> ranges;
    if (!umtFusedCornerScaledRange(
            descriptor.recordBase, descriptor.groupCount,
            descriptor.recordStride, UmtMixedCornerRecordBytes, ranges[0]) ||
        !umtFusedCornerScaledRange(
            descriptor.resultBase, descriptor.groupCount, sizeof(uint64_t),
            sizeof(uint64_t), ranges[1]) ||
        !umtFusedCornerScaledRange(
            descriptor.completionRecord, 1, 32, 32, ranges[2])) {
        result.error = DescriptorError::RangeOverflow;
        return result;
    }
    if (descriptorRangesOverlap(
            ranges[0].begin, ranges[0].end,
            ranges[1].begin, ranges[1].end) ||
        descriptorRangesOverlap(
            ranges[0].begin, ranges[0].end,
            ranges[2].begin, ranges[2].end)) {
        result.error = DescriptorError::OverlappingInput;
        return result;
    }
    if (descriptorRangesOverlap(
            ranges[1].begin, ranges[1].end,
            ranges[2].begin, ranges[2].end)) {
        result.error = DescriptorError::OverlappingOutput;
    }
    return result;
}

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_UMT_MIXED_CORNER_DESCRIPTOR_HH__
