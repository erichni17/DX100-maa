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
using gem5::lanlmaa::decodeDescriptor;

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

    auto bytes = validDescriptor();
    writeLe(bytes, 0, 0, 4);
    assert(decodeDescriptor(bytes, 8).error == DescriptorError::BadMagic);

    bytes = validDescriptor();
    writeLe(bytes, 4, 2, 2);
    assert(decodeDescriptor(bytes, 8).error == DescriptorError::BadVersion);

    bytes = validDescriptor();
    writeLe(bytes, 6, 3, 1);
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

    return 0;
}
