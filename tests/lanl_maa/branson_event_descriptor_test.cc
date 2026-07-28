#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <limits>

#include "mem/LANLMAA/BransonEventDescriptor.hh"

namespace
{

using gem5::lanlmaa::BransonEventDescriptorDecodeResult;
using gem5::lanlmaa::BransonEventReplayOpcode;
using gem5::lanlmaa::DescriptorBytes;
using gem5::lanlmaa::DescriptorError;
using gem5::lanlmaa::DescriptorMagic;
using gem5::lanlmaa::DescriptorVersion;
using gem5::lanlmaa::decodeBransonEventDescriptor;
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
    writeLe(bytes, 6, BransonEventReplayOpcode, 1);
    writeLe(bytes, 8, 16, 4);
    writeLe(bytes, 16, 0x1000, 8);
    writeLe(bytes, 24, 0x2000, 8);
    writeLe(bytes, 32, 0x3000, 8);
    writeLe(bytes, 40, 0x4000, 8);
    writeLe(bytes, 48, 128, 4);
    writeLe(bytes, 52, 32, 4);
    writeLe(bytes, 56, 64, 4);
    return bytes;
}

void
expectError(
    const std::array<uint8_t, DescriptorBytes> &bytes,
    DescriptorError error)
{
    const BransonEventDescriptorDecodeResult result =
        decodeBransonEventDescriptor(bytes, 16);
    assert(!result);
    assert(result.error == error);
}

} // anonymous namespace

int
main()
{
    const auto valid = decodeBransonEventDescriptor(validDescriptor(), 16);
    assert(valid);
    assert(valid.descriptor.rootCount == 16);
    assert(valid.descriptor.rootBase == 0x1000);
    assert(valid.descriptor.tallyBase == 0x2000);
    assert(valid.descriptor.completionRecord == 0x3000);
    assert(valid.descriptor.eventBase == 0x4000);
    assert(valid.descriptor.eventCount == 128);
    assert(valid.descriptor.maximumEventsPerRoot == 32);
    assert(valid.descriptor.cellCount == 64);
    assert(decodeDescriptor(validDescriptor(), 16).error ==
           DescriptorError::BadOpcode);

    auto bytes = validDescriptor();
    writeLe(bytes, 0, 0, 4);
    expectError(bytes, DescriptorError::BadMagic);

    bytes = validDescriptor();
    writeLe(bytes, 4, 2, 2);
    expectError(bytes, DescriptorError::BadVersion);

    bytes = validDescriptor();
    writeLe(bytes, 6, 4, 1);
    expectError(bytes, DescriptorError::BadOpcode);

    bytes = validDescriptor();
    writeLe(bytes, 7, 1, 1);
    expectError(bytes, DescriptorError::UnsupportedFlags);

    bytes = validDescriptor();
    writeLe(bytes, 8, 0, 4);
    expectError(bytes, DescriptorError::Empty);

    bytes = validDescriptor();
    writeLe(bytes, 8, 17, 4);
    expectError(bytes, DescriptorError::TooManyItems);

    bytes = validDescriptor();
    writeLe(bytes, 12, 1, 4);
    expectError(bytes, DescriptorError::ReservedNonzero);

    bytes = validDescriptor();
    writeLe(bytes, 60, 1, 4);
    expectError(bytes, DescriptorError::ReservedNonzero);

    bytes = validDescriptor();
    writeLe(bytes, 16, 0x1008, 8);
    expectError(bytes, DescriptorError::MisalignedVector);

    bytes = validDescriptor();
    writeLe(bytes, 40, 0x4010, 8);
    expectError(bytes, DescriptorError::MisalignedVector);

    for (const size_t offset : {48UL, 52UL, 56UL}) {
        bytes = validDescriptor();
        writeLe(bytes, offset, 0, 4);
        expectError(bytes, DescriptorError::BadRecordGeometry);
    }

    bytes = validDescriptor();
    writeLe(bytes, 40, std::numeric_limits<uint64_t>::max() - 31, 8);
    expectError(bytes, DescriptorError::RangeOverflow);

    bytes = validDescriptor();
    writeLe(bytes, 24, 0x1080, 8);
    expectError(bytes, DescriptorError::OverlappingInput);

    bytes = validDescriptor();
    writeLe(bytes, 32, 0x2100, 8);
    expectError(bytes, DescriptorError::OverlappingInput);

    bytes = validDescriptor();
    writeLe(bytes, 40, 0x2080, 8);
    expectError(bytes, DescriptorError::OverlappingInput);

    return 0;
}
