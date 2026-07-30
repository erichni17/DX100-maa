#ifndef __MEM_LANLMAA_UMT_FUSED_CORNER_DESCRIPTOR_HH__
#define __MEM_LANLMAA_UMT_FUSED_CORNER_DESCRIPTOR_HH__

#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

#include "mem/LANLMAA/Descriptor.hh"

namespace gem5
{
namespace lanlmaa
{

constexpr size_t UmtFusedCornerDescriptorBytes = 128;
constexpr uint16_t UmtFusedCornerDescriptorVersion = 2;
constexpr uint8_t UmtFusedCornerOpcode = 9;
constexpr uint8_t UmtFusedDirectThreeFaceFlag = 1U << 0;
constexpr uint32_t UmtFusedCornerRecordBytes = 96;
constexpr uint32_t UmtFusedCornerMaximumGroups = 32;
constexpr uint64_t UmtFusedCornerAddressLimit = uint64_t{1} << 48;
constexpr uint64_t UmtFusedCornerAbiFingerprint = 0x3b7345c85f10a927ULL;

struct UmtFusedCornerDescriptor
{
    uint32_t groupCount = 0;
    uint32_t recordStride = 0;
    uint64_t recordBase = 0;
    uint64_t resultBase = 0;
    uint64_t completionRecord = 0;
    double tau = 0.0;
    double volume = 0.0;
    double normSum = 0.0;
    std::array<double, 3> fpNorm{};
    std::array<double, 3> ezNorm{};
};

struct UmtFusedCornerDescriptorDecodeResult
{
    UmtFusedCornerDescriptor descriptor;
    DescriptorError error = DescriptorError::None;

    explicit operator bool() const
    {
        return error == DescriptorError::None;
    }
};

struct UmtFusedCornerRange
{
    uint64_t begin = 0;
    uint64_t end = 0;
};

inline double
umtFusedCornerDecodeFp64(const uint8_t *bytes)
{
    const uint64_t bits = descriptorReadLe64(bytes);
    double value = 0.0;
    static_assert(sizeof(value) == sizeof(bits));
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}

inline bool
umtFusedCornerScaledRange(uint64_t base, uint64_t count, uint64_t stride,
                          uint64_t tailBytes, UmtFusedCornerRange &range)
{
    if (count == 0 || stride == 0 || tailBytes == 0 ||
        count - 1 >
            (std::numeric_limits<uint64_t>::max() - tailBytes) / stride) {
        return false;
    }
    const uint64_t bytes = (count - 1) * stride + tailBytes;
    if (base >= UmtFusedCornerAddressLimit ||
        bytes > UmtFusedCornerAddressLimit - base) {
        return false;
    }
    range = {base, base + bytes};
    return true;
}

inline uint64_t
umtFusedCornerBatchCycles(uint32_t groups)
{
    // Deterministic schedules for the physically implemented 1A/1M/8D,
    // global-issue-width-one organization. Other sizes have no frozen
    // schedule and therefore are not accepted by this descriptor version.
    if (groups == 16) {
        return 1819;
    }
    if (groups == 32) {
        return 3595;
    }
    return 0;
}

inline UmtFusedCornerDescriptorDecodeResult
decodeUmtFusedCornerDescriptor(
    const std::array<uint8_t, UmtFusedCornerDescriptorBytes> &bytes)
{
    UmtFusedCornerDescriptorDecodeResult result;
    if (descriptorReadLe32(bytes.data()) != DescriptorMagic) {
        result.error = DescriptorError::BadMagic;
        return result;
    }
    if (descriptorReadLe16(bytes.data() + 4) !=
        UmtFusedCornerDescriptorVersion) {
        result.error = DescriptorError::BadVersion;
        return result;
    }
    if (bytes[6] != UmtFusedCornerOpcode) {
        result.error = DescriptorError::BadOpcode;
        return result;
    }
    if (bytes[7] != UmtFusedDirectThreeFaceFlag) {
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
    if (descriptor.groupCount > UmtFusedCornerMaximumGroups) {
        result.error = DescriptorError::TooManyItems;
        return result;
    }
    if (descriptor.recordStride != UmtFusedCornerRecordBytes ||
        umtFusedCornerBatchCycles(descriptor.groupCount) == 0) {
        result.error = DescriptorError::BadRecordGeometry;
        return result;
    }

    descriptor.recordBase = descriptorReadLe64(bytes.data() + 16);
    descriptor.resultBase = descriptorReadLe64(bytes.data() + 24);
    descriptor.completionRecord = descriptorReadLe64(bytes.data() + 32);
    descriptor.tau = umtFusedCornerDecodeFp64(bytes.data() + 40);
    descriptor.volume = umtFusedCornerDecodeFp64(bytes.data() + 48);
    descriptor.normSum = umtFusedCornerDecodeFp64(bytes.data() + 56);
    for (size_t face = 0; face < 3; ++face) {
        descriptor.fpNorm[face] =
            umtFusedCornerDecodeFp64(bytes.data() + 64 + face * 8);
        descriptor.ezNorm[face] =
            umtFusedCornerDecodeFp64(bytes.data() + 88 + face * 8);
    }
    if (descriptorReadLe64(bytes.data() + 112) !=
        UmtFusedCornerAbiFingerprint) {
        result.error = DescriptorError::BadRecordValue;
        return result;
    }
    if (descriptorReadLe64(bytes.data() + 120) != 0) {
        result.error = DescriptorError::ReservedNonzero;
        return result;
    }

    if (descriptor.recordBase % alignof(uint64_t) != 0 ||
        descriptor.resultBase % alignof(uint64_t) != 0 ||
        descriptor.completionRecord % alignof(uint64_t) != 0) {
        result.error = DescriptorError::MisalignedVector;
        return result;
    }
    if (!std::isfinite(descriptor.tau) ||
        !std::isfinite(descriptor.volume) || descriptor.volume <= 0.0 ||
        !std::isfinite(descriptor.normSum) || descriptor.normSum < 0.0) {
        result.error = DescriptorError::BadRecordValue;
        return result;
    }
    for (size_t face = 0; face < 3; ++face) {
        if (!std::isfinite(descriptor.fpNorm[face]) ||
            descriptor.fpNorm[face] >= 0.0 ||
            !std::isfinite(descriptor.ezNorm[face]) ||
            descriptor.ezNorm[face] <= 0.0) {
            result.error = DescriptorError::BadRecordValue;
            return result;
        }
    }

    std::array<UmtFusedCornerRange, 3> ranges;
    if (!umtFusedCornerScaledRange(
            descriptor.recordBase, descriptor.groupCount,
            descriptor.recordStride, UmtFusedCornerRecordBytes, ranges[0]) ||
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
        return result;
    }
    return result;
}

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_UMT_FUSED_CORNER_DESCRIPTOR_HH__
