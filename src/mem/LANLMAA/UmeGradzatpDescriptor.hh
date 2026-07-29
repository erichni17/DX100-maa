#ifndef __MEM_LANLMAA_UME_GRADZATP_DESCRIPTOR_HH__
#define __MEM_LANLMAA_UME_GRADZATP_DESCRIPTOR_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>

#include "mem/LANLMAA/Descriptor.hh"

namespace gem5
{
namespace lanlmaa
{

constexpr size_t UmeGradzatpDescriptorBytes = 128;
constexpr uint16_t UmeGradzatpDescriptorVersion = 2;
constexpr uint8_t UmeGradzatpOpcode = 8;
constexpr uint32_t UmeGradzatpMaximumCorners = 64;
constexpr uint64_t UmeGradzatpAddressLimit = uint64_t{1} << 48;
constexpr uint64_t UmeGradzatpAbiFingerprint = 0x2ea3d5c8f3d18aecULL;

struct UmeGradzatpDescriptor
{
    uint32_t cornerCount = 0;
    uint32_t pointCount = 0;
    uint32_t zoneCount = 0;
    uint64_t cornerTypeBase = 0;
    uint64_t cornerToZoneBase = 0;
    uint64_t cornerToPointBase = 0;
    uint64_t cornerVolumeBase = 0;
    uint64_t cornerSurfaceBase = 0;
    uint64_t zoneFieldBase = 0;
    uint64_t pointVolumeBase = 0;
    uint64_t pointGradientBase = 0;
    uint64_t completionRecord = 0;
};

struct UmeGradzatpDescriptorDecodeResult
{
    UmeGradzatpDescriptor descriptor;
    DescriptorError error = DescriptorError::None;

    explicit operator bool() const
    {
        return error == DescriptorError::None;
    }
};

struct UmeGradzatpRange
{
    uint64_t begin = 0;
    uint64_t end = 0;
};

inline bool
umeGradzatpRange(uint64_t base, uint64_t count, uint64_t elementBytes,
                 UmeGradzatpRange &range)
{
    if (count == 0 || elementBytes == 0 ||
        count > std::numeric_limits<uint64_t>::max() / elementBytes) {
        return false;
    }
    const uint64_t bytes = count * elementBytes;
    if (base >= UmeGradzatpAddressLimit ||
        bytes > UmeGradzatpAddressLimit - base) {
        return false;
    }
    range = {base, base + bytes};
    return true;
}

inline UmeGradzatpDescriptorDecodeResult
decodeUmeGradzatpDescriptor(
    const std::array<uint8_t, UmeGradzatpDescriptorBytes> &bytes)
{
    UmeGradzatpDescriptorDecodeResult result;
    if (descriptorReadLe32(bytes.data()) != DescriptorMagic) {
        result.error = DescriptorError::BadMagic;
        return result;
    }
    if (descriptorReadLe16(bytes.data() + 4) !=
        UmeGradzatpDescriptorVersion) {
        result.error = DescriptorError::BadVersion;
        return result;
    }
    if (bytes[6] != UmeGradzatpOpcode) {
        result.error = DescriptorError::BadOpcode;
        return result;
    }
    if (bytes[7] != 0) {
        result.error = DescriptorError::UnsupportedFlags;
        return result;
    }

    auto &descriptor = result.descriptor;
    descriptor.cornerCount = descriptorReadLe32(bytes.data() + 8);
    descriptor.pointCount = descriptorReadLe32(bytes.data() + 12);
    descriptor.zoneCount = descriptorReadLe32(bytes.data() + 16);
    if (descriptor.cornerCount == 0 || descriptor.pointCount == 0 ||
        descriptor.zoneCount == 0) {
        result.error = DescriptorError::Empty;
        return result;
    }
    if (descriptor.cornerCount > UmeGradzatpMaximumCorners) {
        result.error = DescriptorError::TooManyItems;
        return result;
    }
    if (descriptorReadLe32(bytes.data() + 20) != 0) {
        result.error = DescriptorError::ReservedNonzero;
        return result;
    }

    descriptor.cornerTypeBase = descriptorReadLe64(bytes.data() + 24);
    descriptor.cornerToZoneBase = descriptorReadLe64(bytes.data() + 32);
    descriptor.cornerToPointBase = descriptorReadLe64(bytes.data() + 40);
    descriptor.cornerVolumeBase = descriptorReadLe64(bytes.data() + 48);
    descriptor.cornerSurfaceBase = descriptorReadLe64(bytes.data() + 56);
    descriptor.zoneFieldBase = descriptorReadLe64(bytes.data() + 64);
    descriptor.pointVolumeBase = descriptorReadLe64(bytes.data() + 72);
    descriptor.pointGradientBase = descriptorReadLe64(bytes.data() + 80);
    descriptor.completionRecord = descriptorReadLe64(bytes.data() + 88);
    if (descriptorReadLe64(bytes.data() + 96) !=
        UmeGradzatpAbiFingerprint) {
        result.error = DescriptorError::BadRecordValue;
        return result;
    }
    for (size_t offset = 104; offset < bytes.size(); ++offset) {
        if (bytes[offset] != 0) {
            result.error = DescriptorError::ReservedNonzero;
            return result;
        }
    }

    const std::array<uint64_t, 8> vectorBases = {
        descriptor.cornerTypeBase,
        descriptor.cornerToZoneBase,
        descriptor.cornerToPointBase,
        descriptor.cornerVolumeBase,
        descriptor.cornerSurfaceBase,
        descriptor.zoneFieldBase,
        descriptor.pointVolumeBase,
        descriptor.pointGradientBase,
    };
    for (const uint64_t base : vectorBases) {
        if (base % sizeof(uint32_t) != 0) {
            result.error = DescriptorError::MisalignedVector;
            return result;
        }
    }
    if (descriptor.completionRecord % sizeof(uint64_t) != 0) {
        result.error = DescriptorError::MisalignedVector;
        return result;
    }

    std::array<UmeGradzatpRange, 9> ranges;
    if (!umeGradzatpRange(
            descriptor.cornerTypeBase, descriptor.cornerCount,
            sizeof(uint32_t), ranges[0]) ||
        !umeGradzatpRange(
            descriptor.cornerToZoneBase, descriptor.cornerCount,
            sizeof(uint32_t), ranges[1]) ||
        !umeGradzatpRange(
            descriptor.cornerToPointBase, descriptor.cornerCount,
            sizeof(uint32_t), ranges[2]) ||
        !umeGradzatpRange(
            descriptor.cornerVolumeBase, descriptor.cornerCount,
            sizeof(uint32_t), ranges[3]) ||
        !umeGradzatpRange(
            descriptor.cornerSurfaceBase, descriptor.cornerCount,
            sizeof(uint32_t), ranges[4]) ||
        !umeGradzatpRange(
            descriptor.zoneFieldBase, descriptor.zoneCount,
            sizeof(uint32_t), ranges[5]) ||
        !umeGradzatpRange(
            descriptor.pointVolumeBase, descriptor.pointCount,
            sizeof(uint32_t), ranges[6]) ||
        !umeGradzatpRange(
            descriptor.pointGradientBase, descriptor.pointCount,
            sizeof(uint32_t), ranges[7]) ||
        !umeGradzatpRange(
            descriptor.completionRecord, 32, 1, ranges[8])) {
        result.error = DescriptorError::RangeOverflow;
        return result;
    }

    for (size_t output = 6; output < ranges.size(); ++output) {
        for (size_t other = 0; other < ranges.size(); ++other) {
            if (output == other ||
                (other >= 6 && other < output)) {
                continue;
            }
            if (descriptorRangesOverlap(
                    ranges[output].begin, ranges[output].end,
                    ranges[other].begin, ranges[other].end)) {
                result.error = other < 6 ?
                    DescriptorError::OverlappingInput :
                    DescriptorError::OverlappingOutput;
                return result;
            }
        }
    }
    return result;
}

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_UME_GRADZATP_DESCRIPTOR_HH__
