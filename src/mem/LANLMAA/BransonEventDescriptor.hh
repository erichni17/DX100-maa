#ifndef __MEM_LANLMAA_BRANSON_EVENT_DESCRIPTOR_HH__
#define __MEM_LANLMAA_BRANSON_EVENT_DESCRIPTOR_HH__

#include <array>
#include <cstddef>
#include <cstdint>

#include "mem/LANLMAA/Descriptor.hh"

namespace gem5
{
namespace lanlmaa
{

constexpr uint8_t BransonEventReplayOpcode = 5;
constexpr uint64_t BransonEventRecordBytes = 32;
constexpr uint64_t BransonRootRecordBytes = 32;
constexpr uint64_t BransonTallyArrays = 2;
constexpr uint32_t BransonTerminalEvent = 0xffffffffU;

enum class BransonEventKind : uint8_t
{
    Scatter = 0,
    Boundary = 1,
    Reflect = 2,
    Census = 3,
    Exit = 4,
    Killed = 5,
    Pass = 6
};

struct BransonEventDescriptor
{
    uint32_t rootCount = 0;
    uint64_t rootBase = 0;
    uint64_t tallyBase = 0;
    uint64_t completionRecord = 0;
    uint64_t eventBase = 0;
    uint32_t eventCount = 0;
    uint32_t maximumEventsPerRoot = 0;
    uint32_t cellCount = 0;
};

struct BransonEventDescriptorDecodeResult
{
    BransonEventDescriptor descriptor;
    DescriptorError error = DescriptorError::None;

    explicit operator bool() const
    {
        return error == DescriptorError::None;
    }
};

inline BransonEventDescriptorDecodeResult
decodeBransonEventDescriptor(
    const std::array<uint8_t, DescriptorBytes> &bytes,
    uint32_t maximumRoots)
{
    BransonEventDescriptorDecodeResult result;
    if (descriptorReadLe32(bytes.data()) != DescriptorMagic) {
        result.error = DescriptorError::BadMagic;
        return result;
    }
    if (descriptorReadLe16(bytes.data() + 4) != DescriptorVersion) {
        result.error = DescriptorError::BadVersion;
        return result;
    }
    if (bytes[6] != BransonEventReplayOpcode) {
        result.error = DescriptorError::BadOpcode;
        return result;
    }
    if (bytes[7] != 0) {
        result.error = DescriptorError::UnsupportedFlags;
        return result;
    }

    auto &descriptor = result.descriptor;
    descriptor.rootCount = descriptorReadLe32(bytes.data() + 8);
    if (descriptor.rootCount == 0) {
        result.error = DescriptorError::Empty;
        return result;
    }
    if (descriptor.rootCount > maximumRoots) {
        result.error = DescriptorError::TooManyItems;
        return result;
    }
    if (descriptorReadLe32(bytes.data() + 12) != 0 ||
        descriptorReadLe32(bytes.data() + 60) != 0) {
        result.error = DescriptorError::ReservedNonzero;
        return result;
    }

    descriptor.rootBase = descriptorReadLe64(bytes.data() + 16);
    descriptor.tallyBase = descriptorReadLe64(bytes.data() + 24);
    descriptor.completionRecord = descriptorReadLe64(bytes.data() + 32);
    descriptor.eventBase = descriptorReadLe64(bytes.data() + 40);
    descriptor.eventCount = descriptorReadLe32(bytes.data() + 48);
    descriptor.maximumEventsPerRoot =
        descriptorReadLe32(bytes.data() + 52);
    descriptor.cellCount = descriptorReadLe32(bytes.data() + 56);
    if (descriptor.rootBase % BransonRootRecordBytes != 0 ||
        descriptor.tallyBase % sizeof(uint64_t) != 0 ||
        descriptor.completionRecord % sizeof(uint64_t) != 0 ||
        descriptor.eventBase % BransonEventRecordBytes != 0) {
        result.error = DescriptorError::MisalignedVector;
        return result;
    }
    if (descriptor.eventCount == 0 ||
        descriptor.maximumEventsPerRoot == 0 || descriptor.cellCount == 0) {
        result.error = DescriptorError::BadRecordGeometry;
        return result;
    }

    uint64_t rootEnd = 0;
    uint64_t tallyEnd = 0;
    uint64_t completionEnd = 0;
    uint64_t eventEnd = 0;
    if (!descriptorRange(
            descriptor.rootBase, descriptor.rootCount,
            BransonRootRecordBytes, rootEnd) ||
        !descriptorRange(
            descriptor.tallyBase,
            static_cast<uint64_t>(descriptor.cellCount) *
                BransonTallyArrays,
            sizeof(uint64_t), tallyEnd) ||
        !descriptorRange(
            descriptor.completionRecord, 1, 32, completionEnd) ||
        !descriptorRange(
            descriptor.eventBase, descriptor.eventCount,
            BransonEventRecordBytes, eventEnd)) {
        result.error = DescriptorError::RangeOverflow;
        return result;
    }
    if (descriptorRangesOverlap(
            descriptor.rootBase, rootEnd,
            descriptor.tallyBase, tallyEnd) ||
        descriptorRangesOverlap(
            descriptor.rootBase, rootEnd,
            descriptor.completionRecord, completionEnd) ||
        descriptorRangesOverlap(
            descriptor.rootBase, rootEnd,
            descriptor.eventBase, eventEnd) ||
        descriptorRangesOverlap(
            descriptor.tallyBase, tallyEnd,
            descriptor.completionRecord, completionEnd) ||
        descriptorRangesOverlap(
            descriptor.tallyBase, tallyEnd,
            descriptor.eventBase, eventEnd) ||
        descriptorRangesOverlap(
            descriptor.completionRecord, completionEnd,
            descriptor.eventBase, eventEnd)) {
        result.error = DescriptorError::OverlappingInput;
        return result;
    }
    return result;
}

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_BRANSON_EVENT_DESCRIPTOR_HH__
