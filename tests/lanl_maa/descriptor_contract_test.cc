#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <limits>

#include "mem/LANLMAA/Descriptor.hh"

namespace
{

using gem5::lanlmaa::DescriptorBytes;
using gem5::lanlmaa::DescriptorError;
using gem5::lanlmaa::DescriptorMagic;
using gem5::lanlmaa::DescriptorOpcode;
using gem5::lanlmaa::DescriptorVersion;
using gem5::lanlmaa::FaceMinMaxBoundaryFaceValueFlag;
using gem5::lanlmaa::FaceMinMaxInternalMode;
using gem5::lanlmaa::decodeDescriptor;
using gem5::lanlmaa::faceMinMaxCellRecordBytes;
using gem5::lanlmaa::faceMinMaxInternalMode;
using gem5::lanlmaa::faceMinMaxUsesFaceValues;

void
writeLe(std::array<uint8_t, DescriptorBytes> &bytes, size_t offset,
        uint64_t value, size_t width)
{
    for (size_t index = 0; index < width; ++index) {
        bytes[offset + index] = (value >> (index * 8)) & 0xff;
    }
}

std::array<uint8_t, DescriptorBytes>
validDescriptor()
{
    std::array<uint8_t, DescriptorBytes> bytes{};
    writeLe(bytes, 0, DescriptorMagic, 4);
    writeLe(bytes, 4, DescriptorVersion, 2);
    writeLe(bytes, 6, 1, 1);
    writeLe(bytes, 8, 4, 4);
    writeLe(bytes, 16, 0x400, 8);
    writeLe(bytes, 24, 0x500, 8);
    writeLe(bytes, 32, 0x600, 8);
    return bytes;
}

std::array<uint8_t, DescriptorBytes>
validCellWalkDescriptor()
{
    auto bytes = validDescriptor();
    writeLe(
        bytes, 6, static_cast<uint8_t>(DescriptorOpcode::IndexedCellWalk), 1);
    writeLe(bytes, 40, 0x700, 8);
    writeLe(bytes, 48, 16, 4);
    writeLe(bytes, 52, 8, 4);
    writeLe(bytes, 56, 0xffffffffffffffff, 8);
    return bytes;
}

std::array<uint8_t, DescriptorBytes>
validPackedDirectionalDescriptor()
{
    auto bytes = validDescriptor();
    writeLe(
        bytes, 6,
        static_cast<uint8_t>(
            DescriptorOpcode::PackedDirectionalCellWalk),
        1);
    writeLe(bytes, 40, 0x700, 8);
    writeLe(bytes, 48, 16, 4);
    writeLe(bytes, 52, 8, 4);
    return bytes;
}

std::array<uint8_t, DescriptorBytes>
validFaceMinMaxDescriptor(uint8_t flags = 0)
{
    auto bytes = validDescriptor();
    writeLe(
        bytes, 6, static_cast<uint8_t>(DescriptorOpcode::FaceMinMax), 1);
    writeLe(bytes, 7, flags, 1);
    writeLe(bytes, 24, (flags & 0x3) == 0 ? 0x800 : 0x820, 8);
    writeLe(bytes, 40, 0x1000, 8);
    writeLe(bytes, 48, 16, 4);
    if ((flags & FaceMinMaxBoundaryFaceValueFlag) != 0) {
        writeLe(bytes, 52, 16, 4);
        writeLe(bytes, 56, 0x1400, 8);
    }
    return bytes;
}

} // anonymous namespace

int
main()
{
    const auto valid = decodeDescriptor(validDescriptor(), 8);
    assert(valid);
    assert(valid.descriptor.itemCount == 4);
    assert(valid.descriptor.addressVector == 0x400);
    assert(valid.descriptor.resultVector == 0x500);
    assert(valid.descriptor.completionRecord == 0x600);

    const auto cellWalk = decodeDescriptor(validCellWalkDescriptor(), 8);
    assert(cellWalk);
    assert(cellWalk.descriptor.opcode == DescriptorOpcode::IndexedCellWalk);
    assert(cellWalk.descriptor.recordBase == 0x700);
    assert(cellWalk.descriptor.recordCount == 16);
    assert(cellWalk.descriptor.maxSteps == 8);
    assert(cellWalk.descriptor.terminalIndex == 0xffffffffffffffff);

    const auto packed = decodeDescriptor(
        validPackedDirectionalDescriptor(), 8);
    assert(packed);
    assert(packed.descriptor.opcode ==
           DescriptorOpcode::PackedDirectionalCellWalk);
    assert(packed.descriptor.recordBase == 0x700);
    assert(packed.descriptor.recordCount == 16);
    assert(packed.descriptor.maxSteps == 8);
    assert(packed.descriptor.terminalIndex == 0);

    const auto face = decodeDescriptor(validFaceMinMaxDescriptor(), 8);
    assert(face);
    assert(face.descriptor.opcode == DescriptorOpcode::FaceMinMax);
    assert(face.descriptor.addressVector == 0x400);
    assert(face.descriptor.resultVector == 0x800);
    assert(face.descriptor.recordBase == 0x1000);
    assert(face.descriptor.recordCount == 16);
    assert(faceMinMaxInternalMode(face.descriptor) ==
           FaceMinMaxInternalMode::Normal);
    assert(faceMinMaxCellRecordBytes(face.descriptor) == 32);
    assert(!faceMinMaxUsesFaceValues(face.descriptor));

    const auto guarded = decodeDescriptor(validFaceMinMaxDescriptor(1), 8);
    assert(guarded);
    assert(faceMinMaxInternalMode(guarded.descriptor) ==
           FaceMinMaxInternalMode::DensityGuarded);
    assert(faceMinMaxCellRecordBytes(guarded.descriptor) == 40);

    const auto pressureFaceValue = decodeDescriptor(
        validFaceMinMaxDescriptor(
            2 | FaceMinMaxBoundaryFaceValueFlag),
        8);
    assert(pressureFaceValue);
    assert(faceMinMaxInternalMode(pressureFaceValue.descriptor) ==
           FaceMinMaxInternalMode::PressureWeighted);
    assert(faceMinMaxUsesFaceValues(pressureFaceValue.descriptor));
    assert(pressureFaceValue.descriptor.faceValueCount == 16);
    assert(pressureFaceValue.descriptor.faceValueBase == 0x1400);

    auto adjacentFaceRanges = validFaceMinMaxDescriptor(
        2 | FaceMinMaxBoundaryFaceValueFlag);
    writeLe(adjacentFaceRanges, 40, 0xaa0, 8);
    writeLe(adjacentFaceRanges, 56, 0xca0, 8);
    assert(decodeDescriptor(adjacentFaceRanges, 8));

    auto bytes = validDescriptor();
    writeLe(bytes, 0, 0, 4);
    assert(decodeDescriptor(bytes, 8).error == DescriptorError::BadMagic);

    bytes = validDescriptor();
    writeLe(bytes, 4, 2, 2);
    assert(decodeDescriptor(bytes, 8).error == DescriptorError::BadVersion);

    bytes = validDescriptor();
    writeLe(bytes, 6, 5, 1);
    assert(decodeDescriptor(bytes, 8).error == DescriptorError::BadOpcode);

    bytes = validDescriptor();
    writeLe(bytes, 7, 1, 1);
    assert(
        decodeDescriptor(bytes, 8).error == DescriptorError::UnsupportedFlags);

    bytes = validDescriptor();
    writeLe(bytes, 8, 0, 4);
    assert(decodeDescriptor(bytes, 8).error == DescriptorError::Empty);

    bytes = validDescriptor();
    writeLe(bytes, 8, 9, 4);
    assert(decodeDescriptor(bytes, 8).error == DescriptorError::TooManyItems);

    bytes = validDescriptor();
    writeLe(bytes, 12, 1, 4);
    assert(
        decodeDescriptor(bytes, 8).error == DescriptorError::ReservedNonzero);

    bytes = validDescriptor();
    writeLe(bytes, 24, 0x503, 8);
    assert(
        decodeDescriptor(bytes, 8).error == DescriptorError::MisalignedVector);

    bytes = validDescriptor();
    writeLe(bytes, 16, std::numeric_limits<uint64_t>::max() - 7, 8);
    assert(decodeDescriptor(bytes, 8).error == DescriptorError::RangeOverflow);

    bytes = validDescriptor();
    writeLe(bytes, 24, 0x410, 8);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::OverlappingOutput);

    bytes = validDescriptor();
    writeLe(bytes, 32, 0x518, 8);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::OverlappingOutput);

    bytes = validCellWalkDescriptor();
    writeLe(bytes, 40, 0x708, 8);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::BadRecordGeometry);

    bytes = validCellWalkDescriptor();
    writeLe(bytes, 48, 0, 4);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::BadRecordGeometry);

    bytes = validCellWalkDescriptor();
    writeLe(bytes, 52, 0, 4);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::BadRecordGeometry);

    bytes = validCellWalkDescriptor();
    writeLe(bytes, 56, 15, 8);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::BadTerminalIndex);

    bytes = validCellWalkDescriptor();
    writeLe(bytes, 40, 0x500, 8);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::OverlappingInput);

    bytes = validCellWalkDescriptor();
    writeLe(bytes, 40, std::numeric_limits<uint64_t>::max() - 15, 8);
    assert(decodeDescriptor(bytes, 8).error == DescriptorError::RangeOverflow);

    bytes = validPackedDirectionalDescriptor();
    writeLe(bytes, 40, 0x704, 8);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::BadRecordGeometry);

    bytes = validPackedDirectionalDescriptor();
    writeLe(bytes, 48, (uint64_t{1} << 24) + 1, 4);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::BadRecordGeometry);

    bytes = validPackedDirectionalDescriptor();
    writeLe(bytes, 56, 1, 8);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::BadRecordGeometry);

    bytes = validPackedDirectionalDescriptor();
    writeLe(bytes, 40, 0x500, 8);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::OverlappingInput);

    bytes = validFaceMinMaxDescriptor();
    writeLe(bytes, 24, 0x808, 8);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::MisalignedVector);

    bytes = validFaceMinMaxDescriptor();
    writeLe(bytes, 40, 0x1004, 8);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::BadRecordGeometry);

    bytes = validFaceMinMaxDescriptor();
    writeLe(bytes, 48, 0, 4);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::BadRecordGeometry);

    bytes = validFaceMinMaxDescriptor();
    writeLe(bytes, 48, (uint64_t{1} << 31) + 1, 4);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::BadRecordGeometry);

    bytes = validFaceMinMaxDescriptor();
    writeLe(bytes, 52, 1, 4);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::BadRecordGeometry);

    bytes = validFaceMinMaxDescriptor();
    writeLe(bytes, 56, 1, 8);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::BadRecordGeometry);

    bytes = validFaceMinMaxDescriptor();
    writeLe(bytes, 7, 3, 1);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::UnsupportedFlags);

    bytes = validFaceMinMaxDescriptor();
    writeLe(bytes, 7, 8, 1);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::UnsupportedFlags);

    bytes = validFaceMinMaxDescriptor(1);
    writeLe(bytes, 24, 0x800, 8);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::MisalignedVector);

    bytes = validFaceMinMaxDescriptor(
        2 | FaceMinMaxBoundaryFaceValueFlag);
    writeLe(bytes, 52, 0, 4);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::BadRecordGeometry);

    bytes = validFaceMinMaxDescriptor(
        2 | FaceMinMaxBoundaryFaceValueFlag);
    writeLe(bytes, 56, 0x1404, 8);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::BadRecordGeometry);

    bytes = validFaceMinMaxDescriptor(
        2 | FaceMinMaxBoundaryFaceValueFlag);
    writeLe(bytes, 56, 0x820, 8);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::OverlappingInput);

    bytes = validFaceMinMaxDescriptor(
        2 | FaceMinMaxBoundaryFaceValueFlag);
    writeLe(bytes, 56, std::numeric_limits<uint64_t>::max() - 7, 8);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::RangeOverflow);

    bytes = validFaceMinMaxDescriptor();
    writeLe(bytes, 40, 0x800, 8);
    assert(decodeDescriptor(bytes, 8).error ==
           DescriptorError::OverlappingInput);

    bytes = validFaceMinMaxDescriptor();
    writeLe(bytes, 40, std::numeric_limits<uint64_t>::max() - 7, 8);
    assert(decodeDescriptor(bytes, 8).error == DescriptorError::RangeOverflow);

    return 0;
}
