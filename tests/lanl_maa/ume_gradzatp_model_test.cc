#include <array>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>

#include "mem/LANLMAA/UmeGradzatpModel.hh"

using namespace gem5::lanlmaa;

namespace
{

void
writeLe32(std::array<uint8_t, UmeGradzatpDescriptorBytes> &bytes,
          size_t offset, uint32_t value)
{
    for (size_t index = 0; index < sizeof(value); ++index) {
        bytes[offset + index] = static_cast<uint8_t>(value >> (index * 8));
    }
}

void
writeLe64(std::array<uint8_t, UmeGradzatpDescriptorBytes> &bytes,
          size_t offset, uint64_t value)
{
    for (size_t index = 0; index < sizeof(value); ++index) {
        bytes[offset + index] = static_cast<uint8_t>(value >> (index * 8));
    }
}

std::array<uint8_t, UmeGradzatpDescriptorBytes>
validBytes()
{
    std::array<uint8_t, UmeGradzatpDescriptorBytes> bytes{};
    writeLe32(bytes, 0, DescriptorMagic);
    bytes[4] = static_cast<uint8_t>(UmeGradzatpDescriptorVersion);
    bytes[5] = static_cast<uint8_t>(UmeGradzatpDescriptorVersion >> 8);
    bytes[6] = UmeGradzatpOpcode;
    writeLe32(bytes, 8, 8);
    writeLe32(bytes, 12, 4);
    writeLe32(bytes, 16, 3);
    writeLe64(bytes, 24, 0x1000);
    writeLe64(bytes, 32, 0x2000);
    writeLe64(bytes, 40, 0x3000);
    writeLe64(bytes, 48, 0x4000);
    writeLe64(bytes, 56, 0x5000);
    writeLe64(bytes, 64, 0x6000);
    writeLe64(bytes, 72, 0x7000);
    writeLe64(bytes, 80, 0x8000);
    writeLe64(bytes, 88, 0x9000);
    writeLe64(bytes, 96, UmeGradzatpAbiFingerprint);
    return bytes;
}

UmeGradzatpDescriptor
modelDescriptor()
{
    auto descriptor = decodeUmeGradzatpDescriptor(validBytes());
    assert(descriptor);
    return descriptor.descriptor;
}

UmeGradzatpInput
modelInput()
{
    UmeGradzatpInput input;
    input.cornerType = {1, -1, 1, 1, 0, 1, 1, 1};
    input.cornerToZone = {0, -1, 1, 2, 99, 1, 0, 2};
    input.cornerToPoint = {0, -1, 1, 0, 99, 2, 1, 0};
    input.cornerVolume = {1.0F, NAN, 2.0F, 3.0F,
                          NAN, 4.0F, 5.0F, 6.0F};
    input.cornerSurface = {2.0F, NAN, 3.0F, 4.0F,
                           NAN, 5.0F, 6.0F, 7.0F};
    input.zoneField = {10.0F, 20.0F, 30.0F};
    input.pointVolume.assign(4, 0.0F);
    input.pointGradient.assign(4, 0.0F);
    return input;
}

} // anonymous namespace

int
main()
{
    {
        const auto decoded = decodeUmeGradzatpDescriptor(validBytes());
        assert(decoded);
        assert(decoded.descriptor.cornerCount == 8);
        assert(decoded.descriptor.pointCount == 4);
        assert(decoded.descriptor.zoneCount == 3);
        assert(decoded.descriptor.cornerTypeBase == 0x1000);
        assert(decoded.descriptor.pointGradientBase == 0x8000);
    }
    {
        auto bytes = validBytes();
        bytes[0] ^= 1;
        assert(decodeUmeGradzatpDescriptor(bytes).error ==
               DescriptorError::BadMagic);
        bytes = validBytes();
        bytes[4] = 1;
        assert(decodeUmeGradzatpDescriptor(bytes).error ==
               DescriptorError::BadVersion);
        bytes = validBytes();
        bytes[6] = 7;
        assert(decodeUmeGradzatpDescriptor(bytes).error ==
               DescriptorError::BadOpcode);
        bytes = validBytes();
        bytes[7] = 1;
        assert(decodeUmeGradzatpDescriptor(bytes).error ==
               DescriptorError::UnsupportedFlags);
        bytes = validBytes();
        writeLe64(bytes, 96, 0);
        assert(decodeUmeGradzatpDescriptor(bytes).error ==
               DescriptorError::BadRecordValue);
        bytes = validBytes();
        bytes[127] = 1;
        assert(decodeUmeGradzatpDescriptor(bytes).error ==
               DescriptorError::ReservedNonzero);
    }
    {
        auto bytes = validBytes();
        writeLe32(bytes, 8, 0);
        assert(decodeUmeGradzatpDescriptor(bytes).error ==
               DescriptorError::Empty);
        bytes = validBytes();
        writeLe32(bytes, 8, UmeGradzatpMaximumCorners + 1);
        assert(decodeUmeGradzatpDescriptor(bytes).error ==
               DescriptorError::TooManyItems);
        bytes = validBytes();
        writeLe64(bytes, 48, 0x4001);
        assert(decodeUmeGradzatpDescriptor(bytes).error ==
               DescriptorError::MisalignedVector);
        bytes = validBytes();
        writeLe64(bytes, 64, UmeGradzatpAddressLimit - 4);
        writeLe32(bytes, 16, 2);
        assert(decodeUmeGradzatpDescriptor(bytes).error ==
               DescriptorError::RangeOverflow);
        bytes = validBytes();
        writeLe64(bytes, 72, 0x1000);
        assert(decodeUmeGradzatpDescriptor(bytes).error ==
               DescriptorError::OverlappingInput);
        bytes = validBytes();
        writeLe64(bytes, 80, 0x7000);
        assert(decodeUmeGradzatpDescriptor(bytes).error ==
               DescriptorError::OverlappingOutput);
    }
    {
        const auto result = UmeGradzatpModel::execute(
            modelDescriptor(), modelInput());
        assert(result);
        assert(result.counters.cornersValidated == 8);
        assert(result.counters.activeCorners == 6);
        assert(result.counters.inactiveCorners == 2);
        assert(result.counters.zoneFieldGathers == 6);
        assert(result.counters.outputZeroReads == 12);
        assert(result.counters.fp32Multiplies == 6);
        assert(result.counters.logicalFp32Updates == 12);
        assert(result.counters.updateAcknowledgements == 12);
        const std::array<float, 4> expectedVolume = {10.0F, 7.0F, 4.0F, 0.0F};
        const std::array<float, 4> expectedGradient = {
            350.0F, 120.0F, 100.0F, 0.0F};
        for (size_t point = 0; point < expectedVolume.size(); ++point) {
            assert(result.pointVolume[point] == expectedVolume[point]);
            assert(result.pointGradient[point] == expectedGradient[point]);
        }
    }
    {
        auto input = modelInput();
        input.cornerType.pop_back();
        auto result = UmeGradzatpModel::execute(modelDescriptor(), input);
        assert(result.error == UmeGradzatpExecutionError::SourceExtent);

        input = modelInput();
        input.cornerToZone[0] = -1;
        result = UmeGradzatpModel::execute(modelDescriptor(), input);
        assert(result.error == UmeGradzatpExecutionError::BadZoneIndex);
        assert(result.pointVolume == input.pointVolume);
        assert(result.pointGradient == input.pointGradient);

        input = modelInput();
        input.cornerToPoint[0] = 4;
        result = UmeGradzatpModel::execute(modelDescriptor(), input);
        assert(result.error == UmeGradzatpExecutionError::BadPointIndex);

        input = modelInput();
        input.pointVolume[0] = 1.0F;
        result = UmeGradzatpModel::execute(modelDescriptor(), input);
        assert(result.error == UmeGradzatpExecutionError::NonzeroOutput);

        input = modelInput();
        input.zoneField[0] = std::numeric_limits<float>::infinity();
        result = UmeGradzatpModel::execute(modelDescriptor(), input);
        assert(result.error == UmeGradzatpExecutionError::NonfiniteInput);

        input = modelInput();
        input.cornerSurface[0] = std::numeric_limits<float>::max();
        input.zoneField[0] = 2.0F;
        result = UmeGradzatpModel::execute(modelDescriptor(), input);
        assert(result.error == UmeGradzatpExecutionError::NonfiniteResult);

        input = modelInput();
        input.cornerVolume[0] = std::numeric_limits<float>::max();
        result = UmeGradzatpModel::execute(modelDescriptor(), input);
        assert(result.error ==
               UmeGradzatpExecutionError::UnsafeAccumulationBound);
    }
    return 0;
}
