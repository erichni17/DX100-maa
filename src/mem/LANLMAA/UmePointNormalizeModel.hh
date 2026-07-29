#ifndef __MEM_LANLMAA_UME_POINT_NORMALIZE_MODEL_HH__
#define __MEM_LANLMAA_UME_POINT_NORMALIZE_MODEL_HH__

#include <cmath>
#include <cstdint>
#include <utility>
#include <vector>

namespace gem5
{
namespace lanlmaa
{

struct UmePointNormalizeInput
{
    std::vector<int32_t> pointType;
    std::vector<float> pointNormal;
    std::vector<float> pointVolume;
    std::vector<float> pointGradient;
};

enum class UmePointNormalizeError : uint8_t
{
    None = 0,
    SourceExtent,
    NonfiniteInput,
    ZeroVolume,
    NonfiniteResult
};

struct UmePointNormalizeCounters
{
    uint32_t pointsClassified = 0;
    uint32_t internalPoints = 0;
    uint32_t boundaryPoints = 0;
    uint32_t inactivePoints = 0;
    uint32_t pointVolumeReads = 0;
    uint32_t pointGradientReads = 0;
    uint32_t pointNormalReads = 0;
    uint32_t fp32Multiplies = 0;
    uint32_t fp32Divides = 0;
    uint32_t fp64Multiplies = 0;
    uint32_t fp64Subtracts = 0;
    uint32_t fp64Divides = 0;
    uint32_t resultWrites = 0;
};

struct UmePointNormalizeResult
{
    UmePointNormalizeError error = UmePointNormalizeError::None;
    UmePointNormalizeCounters counters;
    std::vector<float> pointGradient;

    explicit operator bool() const
    {
        return error == UmePointNormalizeError::None;
    }
};

class UmePointNormalizeModel
{
  private:
    static bool
    finite(float value)
    {
        return std::isfinite(value);
    }

  public:
    static UmePointNormalizeResult
    execute(const UmePointNormalizeInput &input)
    {
        UmePointNormalizeResult result;
        result.pointGradient = input.pointGradient;
        const size_t pointCount = input.pointType.size();
        if (input.pointNormal.size() != pointCount ||
            input.pointVolume.size() != pointCount ||
            input.pointGradient.size() != pointCount) {
            result.error = UmePointNormalizeError::SourceExtent;
            return result;
        }

        std::vector<float> candidate = input.pointGradient;
        for (size_t point = 0; point < pointCount; ++point) {
            ++result.counters.pointsClassified;
            const int32_t type = input.pointType[point];
            if (type <= 0 && type != -1) {
                ++result.counters.inactivePoints;
                continue;
            }

            const float volume = input.pointVolume[point];
            const float gradient = input.pointGradient[point];
            ++result.counters.pointVolumeReads;
            ++result.counters.pointGradientReads;
            if (!finite(volume) || !finite(gradient)) {
                result.error = UmePointNormalizeError::NonfiniteInput;
                return result;
            }
            if (volume == 0.0F) {
                result.error = UmePointNormalizeError::ZeroVolume;
                return result;
            }

            float normalized = 0.0F;
            if (type > 0) {
                ++result.counters.internalPoints;
                ++result.counters.fp32Divides;
                normalized = gradient / volume;
            } else {
                ++result.counters.boundaryPoints;
                ++result.counters.pointNormalReads;
                const float normal = input.pointNormal[point];
                if (!finite(normal)) {
                    result.error = UmePointNormalizeError::NonfiniteInput;
                    return result;
                }

                // Preserve the pinned C++ expression's mixed precision:
                // float*float first, then double multiply/subtract/divide.
                ++result.counters.fp32Multiplies;
                const double ppdot = gradient * normal;
                ++result.counters.fp64Multiplies;
                ++result.counters.fp64Subtracts;
                ++result.counters.fp64Divides;
                normalized = static_cast<float>(
                    (static_cast<double>(gradient) -
                     static_cast<double>(normal) * ppdot) /
                    static_cast<double>(volume));
            }
            if (!finite(normalized)) {
                result.error = UmePointNormalizeError::NonfiniteResult;
                return result;
            }
            candidate[point] = normalized;
        }

        result.counters.resultWrites =
            result.counters.internalPoints + result.counters.boundaryPoints;
        result.pointGradient = std::move(candidate);
        return result;
    }
};

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_UME_POINT_NORMALIZE_MODEL_HH__
