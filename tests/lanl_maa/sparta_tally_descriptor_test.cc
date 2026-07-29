#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <limits>

#include "mem/LANLMAA/SpartaTallyDescriptor.hh"

namespace
{

using gem5::lanlmaa::DescriptorBytes;
using gem5::lanlmaa::DescriptorError;
using gem5::lanlmaa::DescriptorMagic;
using gem5::lanlmaa::DescriptorVersion;
using gem5::lanlmaa::SpartaTallyChannels;
using gem5::lanlmaa::SpartaTallyDescriptorDecodeResult;
using gem5::lanlmaa::SpartaTallyOpcode;
using gem5::lanlmaa::SpartaTallyPendingGenerationFlag;
using gem5::lanlmaa::SpartaTallyCellGroupFlag;
using gem5::lanlmaa::decodeDescriptor;
using gem5::lanlmaa::decodeSpartaTallyDescriptor;

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
    writeLe(bytes, 6, SpartaTallyOpcode, 1);
    writeLe(bytes, 8, 16, 4);
    writeLe(bytes, 16, 0x1000, 8);
    writeLe(bytes, 24, 0x2000, 8);
    writeLe(bytes, 32, 0x3000, 8);
    writeLe(bytes, 40, 0x4000, 8);
    writeLe(bytes, 48, 64, 4);
    writeLe(bytes, 52, SpartaTallyChannels, 4);
    return bytes;
}

void
expectError(
    const std::array<uint8_t, DescriptorBytes> &bytes,
    DescriptorError error)
{
    const SpartaTallyDescriptorDecodeResult result =
        decodeSpartaTallyDescriptor(bytes, 16);
    assert(!result);
    assert(result.error == error);
}

} // anonymous namespace

int
main()
{
    const auto valid = decodeSpartaTallyDescriptor(validDescriptor(), 16);
    assert(valid);
    assert(valid.descriptor.itemCount == 16);
    assert(valid.descriptor.cellIndexBase == 0x1000);
    assert(valid.descriptor.tallyBase == 0x2000);
    assert(valid.descriptor.completionRecord == 0x3000);
    assert(valid.descriptor.contributionBase == 0x4000);
    assert(valid.descriptor.cellCount == 64);
    assert(!valid.descriptor.pendingGeneration);
    assert(!valid.descriptor.cellGroup);
    assert(decodeDescriptor(validDescriptor(), 16).error ==
           DescriptorError::BadOpcode);

    auto pendingBytes = validDescriptor();
    writeLe(pendingBytes, 7, SpartaTallyPendingGenerationFlag, 1);
    const auto pending = decodeSpartaTallyDescriptor(pendingBytes, 16);
    assert(pending);
    assert(pending.descriptor.pendingGeneration);
    assert(!pending.descriptor.cellGroup);

    auto groupBytes = validDescriptor();
    writeLe(groupBytes, 7, SpartaTallyCellGroupFlag, 1);
    const auto group = decodeSpartaTallyDescriptor(groupBytes, 16);
    assert(group);
    assert(!group.descriptor.pendingGeneration);
    assert(group.descriptor.cellGroup);

    auto exclusiveBytes = validDescriptor();
    writeLe(
        exclusiveBytes, 7,
        SpartaTallyPendingGenerationFlag | SpartaTallyCellGroupFlag, 1);
    expectError(exclusiveBytes, DescriptorError::UnsupportedFlags);

    auto bytes = validDescriptor();
    writeLe(bytes, 0, 0, 4);
    expectError(bytes, DescriptorError::BadMagic);

    bytes = validDescriptor();
    writeLe(bytes, 4, 2, 2);
    expectError(bytes, DescriptorError::BadVersion);

    bytes = validDescriptor();
    writeLe(bytes, 6, 5, 1);
    expectError(bytes, DescriptorError::BadOpcode);

    bytes = validDescriptor();
    writeLe(bytes, 7, 4, 1);
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
    writeLe(bytes, 56, 1, 8);
    expectError(bytes, DescriptorError::ReservedNonzero);

    for (const auto [offset, value] :
         std::array<std::array<uint64_t, 2>, 4>{{
             {16, 0x1002},
             {24, 0x2004},
             {32, 0x3004},
             {40, 0x4004},
         }}) {
        bytes = validDescriptor();
        writeLe(bytes, offset, value, 8);
        expectError(bytes, DescriptorError::MisalignedVector);
    }

    bytes = validDescriptor();
    writeLe(bytes, 48, 0, 4);
    expectError(bytes, DescriptorError::BadRecordGeometry);

    for (const uint32_t channels : {0U, 5U, 7U}) {
        bytes = validDescriptor();
        writeLe(bytes, 52, channels, 4);
        expectError(bytes, DescriptorError::BadRecordGeometry);
    }

    for (const size_t offset : {16UL, 24UL, 32UL, 40UL}) {
        bytes = validDescriptor();
        writeLe(
            bytes, offset,
            std::numeric_limits<uint64_t>::max() - 7, 8);
        expectError(bytes, DescriptorError::RangeOverflow);
    }

    for (const auto [offset, value] :
         std::array<std::array<uint64_t, 2>, 6>{{
             {24, 0x1000},
             {32, 0x1000},
             {40, 0x1000},
             {32, 0x2000},
             {40, 0x2000},
             {40, 0x3000},
         }}) {
        bytes = validDescriptor();
        writeLe(bytes, offset, value, 8);
        expectError(bytes, DescriptorError::OverlappingInput);
    }

    return 0;
}
