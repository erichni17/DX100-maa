#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <vector>

#include "mem/LANLMAA/UmePointNormalizeModel.hh"

using namespace gem5::lanlmaa;

namespace
{

UmePointNormalizeInput
validInput()
{
    UmePointNormalizeInput input;
    input.pointType = {1, -1, 0, -2};
    input.pointNormal = {NAN, 0.5F, NAN, NAN};
    input.pointVolume = {2.0F, 4.0F, NAN, NAN};
    input.pointGradient = {6.0F, 8.0F, NAN, NAN};
    return input;
}

bool
sourceVerifierReportsMismatch(float expected, float observed)
{
    return std::fabs(expected - observed) > 1.0e-3F;
}

bool
sameBits(const std::vector<float> &first, const std::vector<float> &second)
{
    return first.size() == second.size() &&
        std::memcmp(first.data(), second.data(),
                    first.size() * sizeof(float)) == 0;
}

} // anonymous namespace

int
main()
{
    {
        const auto input = validInput();
        const auto result = UmePointNormalizeModel::execute(input);
        assert(result);
        assert(result.pointGradient[0] == 3.0F);
        assert(result.pointGradient[1] == 1.5F);
        assert(std::isnan(result.pointGradient[2]));
        assert(std::isnan(result.pointGradient[3]));
        assert(result.counters.pointsClassified == 4);
        assert(result.counters.internalPoints == 1);
        assert(result.counters.boundaryPoints == 1);
        assert(result.counters.inactivePoints == 2);
        assert(result.counters.pointVolumeReads == 2);
        assert(result.counters.pointGradientReads == 2);
        assert(result.counters.pointNormalReads == 1);
        assert(result.counters.fp32Multiplies == 1);
        assert(result.counters.fp32Divides == 1);
        assert(result.counters.fp64Multiplies == 1);
        assert(result.counters.fp64Subtracts == 1);
        assert(result.counters.fp64Divides == 1);
        assert(result.counters.resultWrites == 2);
    }
    {
        auto input = validInput();
        input.pointType = {1, 1, 0, -2};
        input.pointNormal.assign(4, 0.0F);
        input.pointVolume = {2.0F, 0.0F, NAN, NAN};
        input.pointGradient = {6.0F, 8.0F, NAN, NAN};
        const auto original = input.pointGradient;
        const auto result = UmePointNormalizeModel::execute(input);
        assert(result.error == UmePointNormalizeError::ZeroVolume);
        assert(sameBits(result.pointGradient, original));
        assert(result.counters.resultWrites == 0);
    }
    {
        auto input = validInput();
        input.pointNormal[1] = std::numeric_limits<float>::infinity();
        auto result = UmePointNormalizeModel::execute(input);
        assert(result.error == UmePointNormalizeError::NonfiniteInput);
        assert(sameBits(result.pointGradient, input.pointGradient));

        input = validInput();
        input.pointGradient[1] = std::numeric_limits<float>::max();
        input.pointNormal[1] = 2.0F;
        result = UmePointNormalizeModel::execute(input);
        assert(result.error == UmePointNormalizeError::NonfiniteResult);
        assert(sameBits(result.pointGradient, input.pointGradient));
    }
    {
        auto input = validInput();
        input.pointVolume.pop_back();
        const auto result = UmePointNormalizeModel::execute(input);
        assert(result.error == UmePointNormalizeError::SourceExtent);
        assert(sameBits(result.pointGradient, input.pointGradient));
    }
    {
        const float sourceResult = 0.0F / 0.0F;
        assert(std::isnan(sourceResult));
        assert(!sourceVerifierReportsMismatch(sourceResult, 7.0F));

        constexpr uint32_t paddingPerSide = 90000;
        constexpr uint32_t corners = 64;
        constexpr uint32_t points = corners + 2 * paddingPerSide;
        static_assert(points - corners == 180000);
    }
    return 0;
}
