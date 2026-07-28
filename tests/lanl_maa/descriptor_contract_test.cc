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

    auto bytes = validDescriptor();
    writeLe(bytes, 0, 0, 4);
    assert(decodeDescriptor(bytes, 8).error == DescriptorError::BadMagic);

    bytes = validDescriptor();
    writeLe(bytes, 4, 2, 2);
    assert(decodeDescriptor(bytes, 8).error == DescriptorError::BadVersion);

    bytes = validDescriptor();
    writeLe(bytes, 6, 2, 1);
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

    return 0;
}
