#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <vector>

#include "mem/LANLMAA/UmeGradzatzModel.hh"

using namespace gem5::lanlmaa;

namespace
{

UmeGradzatzInput
validInput()
{
    UmeGradzatzInput input;
    input.cornerType = {1, -1, 1, 1, 0, 1, 1, 1};
    input.cornerToZone = {0, -1, 1, 2, 99, 1, 0, 2};
    input.cornerToPoint = {0, -1, 1, 2, 99, 3, 1, 0};
    input.cornerVolume = {1.0F, NAN, 2.0F, 3.0F,
                          NAN, 4.0F, 5.0F, 6.0F};
    input.pointGradient = {10.0F, 20.0F, 30.0F, 40.0F};
    input.zoneVolume.assign(4, 0.0F);
    input.zoneGradient.assign(4, 0.0F);
    return input;
}

bool
sameBits(const std::vector<float> &first, const std::vector<float> &second)
{
    return first.size() == second.size() &&
        std::memcmp(first.data(), second.data(),
                    first.size() * sizeof(float)) == 0;
}

std::vector<float>
sourceOrderGradient(const UmeGradzatzInput &input,
                    const std::vector<float> &volume)
{
    std::vector<float> result(input.zoneGradient.size(), 0.0F);
    for (size_t corner = 0; corner < input.cornerType.size(); ++corner) {
        if (input.cornerType[corner] < 1) {
            continue;
        }
        const size_t zone = static_cast<size_t>(input.cornerToZone[corner]);
        const size_t point = static_cast<size_t>(input.cornerToPoint[corner]);
        const double ratio = input.cornerVolume[corner] / volume[zone];
        result[zone] = static_cast<float>(
            static_cast<double>(result[zone]) +
            static_cast<double>(input.pointGradient[point]) * ratio);
    }
    return result;
}

} // anonymous namespace

int
main()
{
    {
        const auto input = validInput();
        const auto result = UmeGradzatzModel::execute(input);
        assert(result);
        const std::vector<float> expectedVolume = {6.0F, 6.0F, 9.0F, 0.0F};
        const auto expectedGradient =
            sourceOrderGradient(input, expectedVolume);
        assert(sameBits(result.zoneVolume, expectedVolume));
        assert(sameBits(result.zoneGradient, expectedGradient));
        assert(result.counters.cornersClassified == 8);
        assert(result.counters.activeCorners == 6);
        assert(result.counters.inactiveCorners == 2);
        assert(result.counters.distinctZones == 3);
        assert(result.counters.fp32VolumeAdds == 6);
        assert(result.counters.fp32Divides == 6);
        assert(result.counters.fp64GradientMultiplies == 6);
        assert(result.counters.fp64GradientAdds == 6);
        assert(result.counters.logicalUpdates == 12);
        assert(result.counters.consolidatedWrites == 6);
    }
    {
        auto input = validInput();
        input.cornerToPoint.back() = 4;
        const auto volume = input.zoneVolume;
        const auto gradient = input.zoneGradient;
        const auto result = UmeGradzatzModel::execute(input);
        assert(result.error == UmeGradzatzError::BadPointIndex);
        assert(sameBits(result.zoneVolume, volume));
        assert(sameBits(result.zoneGradient, gradient));
        assert(result.counters.consolidatedWrites == 0);
    }
    {
        auto input = validInput();
        input.cornerType = {1, 1};
        input.cornerToZone = {0, 0};
        input.cornerToPoint = {0, 0};
        input.cornerVolume = {1.0F, -1.0F};
        const auto result = UmeGradzatzModel::execute(input);
        assert(result.error == UmeGradzatzError::ZeroZoneVolume);
        assert(sameBits(result.zoneVolume, input.zoneVolume));
        assert(sameBits(result.zoneGradient, input.zoneGradient));
    }
    {
        auto input = validInput();
        input.zoneGradient[0] = 1.0F;
        auto result = UmeGradzatzModel::execute(input);
        assert(result.error == UmeGradzatzError::NonzeroOutput);

        input = validInput();
        input.cornerVolume[0] = std::numeric_limits<float>::max();
        input.cornerVolume[6] = std::numeric_limits<float>::max();
        result = UmeGradzatzModel::execute(input);
        assert(result.error == UmeGradzatzError::NonfiniteResult);

        input = validInput();
        input.cornerType.resize(UmeGradzatzMaximumCorners + 1, 0);
        input.cornerToZone.resize(UmeGradzatzMaximumCorners + 1, 0);
        input.cornerToPoint.resize(UmeGradzatzMaximumCorners + 1, 0);
        input.cornerVolume.resize(UmeGradzatzMaximumCorners + 1, 0.0F);
        result = UmeGradzatzModel::execute(input);
        assert(result.error == UmeGradzatzError::TooManyCorners);
    }
    {
        const float accumulator = 1.0F;
        const float pointGradient = 9.461832F;
        const float cornerVolume = 0.87381124F;
        const float zoneVolume = 6.6409407F;
        const float ratio = cornerVolume / zoneVolume;
        const float sourceMixed = static_cast<float>(
            static_cast<double>(accumulator) +
            static_cast<double>(pointGradient) * ratio);
        const volatile float fp32Product = pointGradient * ratio;
        const float allFp32 = accumulator + fp32Product;
        assert(sourceMixed != allFp32);
    }
    return 0;
}
