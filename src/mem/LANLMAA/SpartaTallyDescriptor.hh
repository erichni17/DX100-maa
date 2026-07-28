#ifndef __MEM_LANLMAA_SPARTA_TALLY_DESCRIPTOR_HH__
#define __MEM_LANLMAA_SPARTA_TALLY_DESCRIPTOR_HH__

#include <array>
#include <cstddef>
#include <cstdint>

#include "mem/LANLMAA/Descriptor.hh"

namespace gem5
{
namespace lanlmaa
{

constexpr uint8_t SpartaTallyOpcode = 6;
constexpr uint32_t SpartaTallyChannels = 6;
constexpr uint64_t SpartaTallyCellIndexBytes = sizeof(uint32_t);
constexpr uint64_t SpartaTallyContributionRecordBytes =
    SpartaTallyChannels * sizeof(uint64_t);

struct SpartaTallyDescriptor
{
    uint32_t itemCount = 0;
    uint64_t cellIndexBase = 0;
    uint64_t tallyBase = 0;
    uint64_t completionRecord = 0;
    uint64_t contributionBase = 0;
    uint32_t cellCount = 0;
};

struct SpartaTallyDescriptorDecodeResult
{
    SpartaTallyDescriptor descriptor;
    DescriptorError error = DescriptorError::None;

    explicit operator bool() const
    {
        return error == DescriptorError::None;
    }
};

inline SpartaTallyDescriptorDecodeResult
decodeSpartaTallyDescriptor(
    const std::array<uint8_t, DescriptorBytes> &bytes,
    uint32_t maximumItems)
{
    SpartaTallyDescriptorDecodeResult result;
    if (descriptorReadLe32(bytes.data()) != DescriptorMagic) {
        result.error = DescriptorError::BadMagic;
        return result;
    }
    if (descriptorReadLe16(bytes.data() + 4) != DescriptorVersion) {
        result.error = DescriptorError::BadVersion;
        return result;
    }
    if (bytes[6] != SpartaTallyOpcode) {
        result.error = DescriptorError::BadOpcode;
        return result;
    }
    if (bytes[7] != 0) {
        result.error = DescriptorError::UnsupportedFlags;
        return result;
    }

    auto &descriptor = result.descriptor;
    descriptor.itemCount = descriptorReadLe32(bytes.data() + 8);
    if (descriptor.itemCount == 0) {
        result.error = DescriptorError::Empty;
        return result;
    }
    if (descriptor.itemCount > maximumItems) {
        result.error = DescriptorError::TooManyItems;
        return result;
    }
    if (descriptorReadLe32(bytes.data() + 12) != 0 ||
        descriptorReadLe64(bytes.data() + 56) != 0) {
        result.error = DescriptorError::ReservedNonzero;
        return result;
    }

    descriptor.cellIndexBase = descriptorReadLe64(bytes.data() + 16);
    descriptor.tallyBase = descriptorReadLe64(bytes.data() + 24);
    descriptor.completionRecord = descriptorReadLe64(bytes.data() + 32);
    descriptor.contributionBase = descriptorReadLe64(bytes.data() + 40);
    descriptor.cellCount = descriptorReadLe32(bytes.data() + 48);
    const uint32_t channels = descriptorReadLe32(bytes.data() + 52);
    if (descriptor.cellIndexBase % SpartaTallyCellIndexBytes != 0 ||
        descriptor.tallyBase % sizeof(uint64_t) != 0 ||
        descriptor.completionRecord % sizeof(uint64_t) != 0 ||
        descriptor.contributionBase % sizeof(uint64_t) != 0) {
        result.error = DescriptorError::MisalignedVector;
        return result;
    }
    if (descriptor.cellCount == 0 || channels != SpartaTallyChannels) {
        result.error = DescriptorError::BadRecordGeometry;
        return result;
    }

    uint64_t cellIndexEnd = 0;
    uint64_t tallyEnd = 0;
    uint64_t completionEnd = 0;
    uint64_t contributionEnd = 0;
    if (!descriptorRange(
            descriptor.cellIndexBase, descriptor.itemCount,
            SpartaTallyCellIndexBytes, cellIndexEnd) ||
        !descriptorRange(
            descriptor.tallyBase,
            static_cast<uint64_t>(descriptor.cellCount) *
                SpartaTallyChannels,
            sizeof(uint64_t), tallyEnd) ||
        !descriptorRange(
            descriptor.completionRecord, 1, 32, completionEnd) ||
        !descriptorRange(
            descriptor.contributionBase, descriptor.itemCount,
            SpartaTallyContributionRecordBytes, contributionEnd)) {
        result.error = DescriptorError::RangeOverflow;
        return result;
    }

    const std::array<std::array<uint64_t, 2>, 4> ranges = {{
        {descriptor.cellIndexBase, cellIndexEnd},
        {descriptor.tallyBase, tallyEnd},
        {descriptor.completionRecord, completionEnd},
        {descriptor.contributionBase, contributionEnd},
    }};
    for (size_t first = 0; first < ranges.size(); ++first) {
        for (size_t second = first + 1; second < ranges.size(); ++second) {
            if (descriptorRangesOverlap(
                    ranges[first][0], ranges[first][1],
                    ranges[second][0], ranges[second][1])) {
                result.error = DescriptorError::OverlappingInput;
                return result;
            }
        }
    }
    return result;
}

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_SPARTA_TALLY_DESCRIPTOR_HH__
