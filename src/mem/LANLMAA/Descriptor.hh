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
    DirectGather = 1,
    IndexedCellWalk = 2,
    PackedDirectionalCellWalk = 3,
    FaceMinMax = 4
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
    BadTargetAddress = 12,
    BadRecordGeometry = 13,
    BadTerminalIndex = 14,
    OverlappingInput = 15,
    ContinuationExhausted = 16,
    BadStartState = 17,
    BadRecordValue = 18
};

constexpr uint64_t PackedDirectionalCellMask = (uint64_t{1} << 24) - 1;
constexpr uint64_t PackedDirectionalDirectionBit = uint64_t{1} << 24;
constexpr size_t PackedDirectionalRemainingShift = 25;
constexpr uint64_t PackedDirectionalRemainingMask =
    ((uint64_t{1} << 32) - 1) << PackedDirectionalRemainingShift;
constexpr uint64_t PackedDirectionalStartReservedMask =
    ~((uint64_t{1} << 57) - 1);
constexpr uint64_t PackedDirectionalRecordReservedMask =
    ~((uint64_t{1} << 48) - 1);
constexpr uint64_t PackedDirectionalMaximumCells = uint64_t{1} << 24;
constexpr uint64_t FaceMinMaxCellMask = (uint64_t{1} << 31) - 1;
constexpr size_t FaceMinMaxHighCellShift = 31;
constexpr uint64_t FaceMinMaxActiveBit = uint64_t{1} << 62;
constexpr uint64_t FaceMinMaxReservedMask = uint64_t{1} << 63;
constexpr uint64_t FaceMinMaxMaximumCells = uint64_t{1} << 31;
constexpr uint64_t FaceMinMaxCellRecordBytes = 4 * sizeof(uint64_t);
constexpr uint64_t FaceMinMaxOutputArrays = 4;

struct Descriptor
{
    DescriptorOpcode opcode = DescriptorOpcode::DirectGather;
    uint32_t itemCount = 0;
    uint64_t addressVector = 0;
    uint64_t resultVector = 0;
    uint64_t completionRecord = 0;
    uint64_t recordBase = 0;
    uint32_t recordCount = 0;
    uint32_t maxSteps = 0;
    uint64_t terminalIndex = 0;
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

inline bool
descriptorIsRecordWalk(DescriptorOpcode opcode)
{
    return opcode == DescriptorOpcode::IndexedCellWalk ||
           opcode == DescriptorOpcode::PackedDirectionalCellWalk;
}

inline bool
descriptorHasRecordRange(DescriptorOpcode opcode)
{
    return descriptorIsRecordWalk(opcode) ||
           opcode == DescriptorOpcode::FaceMinMax;
}

inline uint64_t
descriptorRecordBytes(DescriptorOpcode opcode)
{
    if (opcode == DescriptorOpcode::IndexedCellWalk) {
        return 2 * sizeof(uint64_t);
    }
    if (opcode == DescriptorOpcode::PackedDirectionalCellWalk) {
        return sizeof(uint64_t);
    }
    return FaceMinMaxCellRecordBytes;
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
    const auto rawOpcode = bytes[6];
    if (rawOpcode != static_cast<uint8_t>(DescriptorOpcode::DirectGather) &&
        rawOpcode !=
            static_cast<uint8_t>(DescriptorOpcode::IndexedCellWalk) &&
        rawOpcode != static_cast<uint8_t>(
            DescriptorOpcode::PackedDirectionalCellWalk) &&
        rawOpcode != static_cast<uint8_t>(DescriptorOpcode::FaceMinMax)) {
        result.error = DescriptorError::BadOpcode;
        return result;
    }
    if (bytes[7] != 0) {
        result.error = DescriptorError::UnsupportedFlags;
        return result;
    }

    result.descriptor.opcode = static_cast<DescriptorOpcode>(rawOpcode);
    result.descriptor.itemCount = descriptorReadLe32(bytes.data() + 8);
    if (result.descriptor.itemCount == 0) {
        result.error = DescriptorError::Empty;
        return result;
    }
    if (result.descriptor.itemCount > maxItems) {
        result.error = DescriptorError::TooManyItems;
        return result;
    }
    if (descriptorReadLe32(bytes.data() + 12) != 0) {
        result.error = DescriptorError::ReservedNonzero;
        return result;
    }

    if (result.descriptor.opcode == DescriptorOpcode::DirectGather) {
        if (descriptorReadLe64(bytes.data() + 40) != 0 ||
            descriptorReadLe64(bytes.data() + 48) != 0 ||
            descriptorReadLe64(bytes.data() + 56) != 0) {
            result.error = DescriptorError::ReservedNonzero;
            return result;
        }
    } else if (descriptorIsRecordWalk(result.descriptor.opcode)) {
        result.descriptor.recordBase = descriptorReadLe64(bytes.data() + 40);
        result.descriptor.recordCount = descriptorReadLe32(bytes.data() + 48);
        result.descriptor.maxSteps = descriptorReadLe32(bytes.data() + 52);
        result.descriptor.terminalIndex =
            descriptorReadLe64(bytes.data() + 56);
        const uint64_t recordBytes = descriptorRecordBytes(
            result.descriptor.opcode);
        if (result.descriptor.recordBase % recordBytes != 0 ||
            result.descriptor.recordCount == 0 ||
            result.descriptor.maxSteps == 0) {
            result.error = DescriptorError::BadRecordGeometry;
            return result;
        }
        if (result.descriptor.opcode == DescriptorOpcode::IndexedCellWalk) {
            if (result.descriptor.terminalIndex <
                result.descriptor.recordCount) {
                result.error = DescriptorError::BadTerminalIndex;
                return result;
            }
        } else {
            if (result.descriptor.recordCount >
                    PackedDirectionalMaximumCells ||
                result.descriptor.terminalIndex != 0) {
                result.error = DescriptorError::BadRecordGeometry;
                return result;
            }
        }
    } else {
        result.descriptor.recordBase = descriptorReadLe64(bytes.data() + 40);
        result.descriptor.recordCount = descriptorReadLe32(bytes.data() + 48);
        result.descriptor.maxSteps = descriptorReadLe32(bytes.data() + 52);
        result.descriptor.terminalIndex =
            descriptorReadLe64(bytes.data() + 56);
        if (result.descriptor.recordBase % sizeof(uint64_t) != 0 ||
            result.descriptor.recordCount == 0 ||
            result.descriptor.recordCount > FaceMinMaxMaximumCells ||
            result.descriptor.maxSteps != 0 ||
            result.descriptor.terminalIndex != 0) {
            result.error = DescriptorError::BadRecordGeometry;
            return result;
        }
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

    if (result.descriptor.opcode == DescriptorOpcode::FaceMinMax &&
        result.descriptor.resultVector % FaceMinMaxCellRecordBytes != 0) {
        result.error = DescriptorError::MisalignedVector;
        return result;
    }

    uint64_t addressEnd = 0;
    uint64_t resultEnd = 0;
    uint64_t completionEnd = 0;
    uint64_t recordEnd = 0;
    if (!descriptorRange(result.descriptor.addressVector,
                         result.descriptor.itemCount, sizeof(uint64_t),
                         addressEnd) ||
        !descriptorRange(
            result.descriptor.resultVector,
            result.descriptor.opcode == DescriptorOpcode::FaceMinMax ?
                result.descriptor.recordCount : result.descriptor.itemCount,
            result.descriptor.opcode == DescriptorOpcode::FaceMinMax ?
                FaceMinMaxCellRecordBytes : sizeof(uint64_t),
            resultEnd) ||
        !descriptorRange(result.descriptor.completionRecord, 1, 32,
                         completionEnd) ||
        (descriptorHasRecordRange(result.descriptor.opcode) &&
         !descriptorRange(result.descriptor.recordBase,
                          result.descriptor.opcode ==
                                  DescriptorOpcode::FaceMinMax ?
                              result.descriptor.recordCount *
                                  FaceMinMaxOutputArrays :
                              result.descriptor.recordCount,
                          descriptorRecordBytes(result.descriptor.opcode),
                          recordEnd))) {
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
    if (descriptorHasRecordRange(result.descriptor.opcode) &&
        (descriptorRangesOverlap(
             result.descriptor.addressVector, addressEnd,
             result.descriptor.recordBase, recordEnd) ||
         descriptorRangesOverlap(
             result.descriptor.resultVector, resultEnd,
             result.descriptor.recordBase, recordEnd) ||
         descriptorRangesOverlap(
             result.descriptor.completionRecord, completionEnd,
             result.descriptor.recordBase, recordEnd))) {
        result.error = DescriptorError::OverlappingInput;
        return result;
    }
    return result;
}

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_DESCRIPTOR_HH__
