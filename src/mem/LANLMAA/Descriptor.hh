#ifndef __MEM_LANLMAA_DESCRIPTOR_HH__
#define __MEM_LANLMAA_DESCRIPTOR_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>

namespace gem5
{
namespace lanlmaa
{

constexpr size_t DescriptorBytes = 64;
constexpr uint32_t DescriptorMagic = 0x31414d4c; // "LMA1" little-endian.
constexpr uint16_t DescriptorVersion = 1;

enum class DescriptorOpcode : uint8_t
{
    DirectGather = 1
};

enum class DescriptorError : uint8_t
{
    None = 0,
    BadMagic = 1,
    BadVersion = 2,
    BadOpcode = 3,
    UnsupportedFlags = 4,
    Empty = 5,
    TooManyItems = 6,
    MisalignedVector = 7,
    RangeOverflow = 8,
    ReservedNonzero = 9,
    OverlappingOutput = 10,
    UnsafeAddressRange = 11,
    BadTargetAddress = 12
};

struct Descriptor
{
    DescriptorOpcode opcode = DescriptorOpcode::DirectGather;
    uint32_t itemCount = 0;
    uint64_t addressVector = 0;
    uint64_t resultVector = 0;
    uint64_t completionRecord = 0;
};

struct DescriptorDecodeResult
{
    Descriptor descriptor;
    DescriptorError error = DescriptorError::None;

    explicit operator bool() const
    {
        return error == DescriptorError::None;
    }
};

inline uint16_t
descriptorReadLe16(const uint8_t *bytes)
{
    return static_cast<uint16_t>(bytes[0]) |
        (static_cast<uint16_t>(bytes[1]) << 8);
}

inline uint32_t
descriptorReadLe32(const uint8_t *bytes)
{
    uint32_t value = 0;
    for (size_t index = 0; index < sizeof(value); ++index) {
        value |= static_cast<uint32_t>(bytes[index]) << (index * 8);
    }
    return value;
}

inline uint64_t
descriptorReadLe64(const uint8_t *bytes)
{
    uint64_t value = 0;
    for (size_t index = 0; index < sizeof(value); ++index) {
        value |= static_cast<uint64_t>(bytes[index]) << (index * 8);
    }
    return value;
}

inline bool
descriptorRange(uint64_t base, uint64_t count, uint64_t elementBytes,
                uint64_t &end)
{
    if (count > std::numeric_limits<uint64_t>::max() / elementBytes) {
        return false;
    }
    const uint64_t bytes = count * elementBytes;
    if (base > std::numeric_limits<uint64_t>::max() - bytes) {
        return false;
    }
    end = base + bytes;
    return true;
}

inline bool
descriptorRangesOverlap(uint64_t firstBegin, uint64_t firstEnd,
                        uint64_t secondBegin, uint64_t secondEnd)
{
    return firstBegin < secondEnd && secondBegin < firstEnd;
}

inline DescriptorDecodeResult
decodeDescriptor(const std::array<uint8_t, DescriptorBytes> &bytes,
                 uint32_t maxItems)
{
    DescriptorDecodeResult result;
    if (descriptorReadLe32(bytes.data()) != DescriptorMagic) {
        result.error = DescriptorError::BadMagic;
        return result;
    }
    if (descriptorReadLe16(bytes.data() + 4) != DescriptorVersion) {
        result.error = DescriptorError::BadVersion;
        return result;
    }
    if (bytes[6] != static_cast<uint8_t>(DescriptorOpcode::DirectGather)) {
        result.error = DescriptorError::BadOpcode;
        return result;
    }
    if (bytes[7] != 0) {
        result.error = DescriptorError::UnsupportedFlags;
        return result;
    }

    result.descriptor.opcode = DescriptorOpcode::DirectGather;
    result.descriptor.itemCount = descriptorReadLe32(bytes.data() + 8);
    if (result.descriptor.itemCount == 0) {
        result.error = DescriptorError::Empty;
        return result;
    }
    if (result.descriptor.itemCount > maxItems) {
        result.error = DescriptorError::TooManyItems;
        return result;
    }
    if (descriptorReadLe32(bytes.data() + 12) != 0 ||
        descriptorReadLe64(bytes.data() + 40) != 0 ||
        descriptorReadLe64(bytes.data() + 48) != 0 ||
        descriptorReadLe64(bytes.data() + 56) != 0) {
        result.error = DescriptorError::ReservedNonzero;
        return result;
    }

    result.descriptor.addressVector = descriptorReadLe64(bytes.data() + 16);
    result.descriptor.resultVector = descriptorReadLe64(bytes.data() + 24);
    result.descriptor.completionRecord =
        descriptorReadLe64(bytes.data() + 32);
    if (result.descriptor.addressVector % sizeof(uint64_t) != 0 ||
        result.descriptor.resultVector % sizeof(uint64_t) != 0 ||
        result.descriptor.completionRecord % sizeof(uint64_t) != 0) {
        result.error = DescriptorError::MisalignedVector;
        return result;
    }

    uint64_t addressEnd = 0;
    uint64_t resultEnd = 0;
    uint64_t completionEnd = 0;
    if (!descriptorRange(result.descriptor.addressVector,
                         result.descriptor.itemCount, sizeof(uint64_t),
                         addressEnd) ||
        !descriptorRange(result.descriptor.resultVector,
                         result.descriptor.itemCount, sizeof(uint64_t),
                         resultEnd) ||
        !descriptorRange(result.descriptor.completionRecord, 1, 32,
                         completionEnd)) {
        result.error = DescriptorError::RangeOverflow;
        return result;
    }
    if (descriptorRangesOverlap(
            result.descriptor.addressVector, addressEnd,
            result.descriptor.resultVector, resultEnd) ||
        descriptorRangesOverlap(
            result.descriptor.addressVector, addressEnd,
            result.descriptor.completionRecord, completionEnd) ||
        descriptorRangesOverlap(
            result.descriptor.resultVector, resultEnd,
            result.descriptor.completionRecord, completionEnd)) {
        result.error = DescriptorError::OverlappingOutput;
        return result;
    }
    return result;
}

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_DESCRIPTOR_HH__
