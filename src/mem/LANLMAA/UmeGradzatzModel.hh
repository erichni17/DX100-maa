#ifndef __MEM_LANLMAA_UME_GRADZATZ_MODEL_HH__
#define __MEM_LANLMAA_UME_GRADZATZ_MODEL_HH__

#include <cmath>
#include <cstdint>
#include <utility>
#include <vector>

namespace gem5
{
namespace lanlmaa
{

constexpr size_t UmeGradzatzMaximumCorners = 64;

struct UmeGradzatzInput
{
    std::vector<int32_t> cornerType;
    std::vector<int32_t> cornerToZone;
    std::vector<int32_t> cornerToPoint;
    std::vector<float> cornerVolume;
    std::vector<float> pointGradient;
    std::vector<float> zoneVolume;
    std::vector<float> zoneGradient;
};

enum class UmeGradzatzError : uint8_t
{
    None = 0,
    SourceExtent,
    TooManyCorners,
    BadZoneIndex,
    BadPointIndex,
    NonfiniteInput,
    NonzeroOutput,
    ZeroZoneVolume,
    NonfiniteResult
};

struct UmeGradzatzCounters
{
    uint32_t cornersClassified = 0;
    uint32_t activeCorners = 0;
    uint32_t inactiveCorners = 0;
    uint32_t distinctZones = 0;
    uint32_t fp32VolumeAdds = 0;
    uint32_t fp32Divides = 0;
    uint32_t fp64GradientMultiplies = 0;
    uint32_t fp64GradientAdds = 0;
    uint32_t logicalUpdates = 0;
    uint32_t consolidatedWrites = 0;
};

struct UmeGradzatzResult
{
    UmeGradzatzError error = UmeGradzatzError::None;
    UmeGradzatzCounters counters;
    std::vector<float> zoneVolume;
    std::vector<float> zoneGradient;

    explicit operator bool() const
    {
        return error == UmeGradzatzError::None;
    }
};

class UmeGradzatzModel
{
  private:
    static bool
    finite(float value)
    {
        return std::isfinite(value);
    }

  public:
    static UmeGradzatzResult
    execute(const UmeGradzatzInput &input)
    {
        UmeGradzatzResult result;
        result.zoneVolume = input.zoneVolume;
        result.zoneGradient = input.zoneGradient;
        const size_t cornerCount = input.cornerType.size();
        const size_t zoneCount = input.zoneVolume.size();
        const size_t pointCount = input.pointGradient.size();
        if (input.cornerToZone.size() != cornerCount ||
            input.cornerToPoint.size() != cornerCount ||
            input.cornerVolume.size() != cornerCount ||
            input.zoneGradient.size() != zoneCount || zoneCount == 0 ||
            pointCount == 0) {
            result.error = UmeGradzatzError::SourceExtent;
            return result;
        }
        if (cornerCount > UmeGradzatzMaximumCorners) {
            result.error = UmeGradzatzError::TooManyCorners;
            return result;
        }

        std::vector<float> shadowVolume(zoneCount, 0.0F);
        std::vector<float> shadowGradient(zoneCount, 0.0F);
        std::vector<bool> touched(zoneCount, false);
        for (size_t corner = 0; corner < cornerCount; ++corner) {
            ++result.counters.cornersClassified;
            if (input.cornerType[corner] < 1) {
                ++result.counters.inactiveCorners;
                continue;
            }
            ++result.counters.activeCorners;
            const int32_t zone = input.cornerToZone[corner];
            const int32_t point = input.cornerToPoint[corner];
            if (zone < 0 || static_cast<size_t>(zone) >= zoneCount) {
                result.error = UmeGradzatzError::BadZoneIndex;
                return result;
            }
            if (point < 0 || static_cast<size_t>(point) >= pointCount) {
                result.error = UmeGradzatzError::BadPointIndex;
                return result;
            }
            const size_t zoneIndex = static_cast<size_t>(zone);
            const float volume = input.cornerVolume[corner];
            const float pointValue = input.pointGradient[point];
            if (!finite(volume) || !finite(pointValue) ||
                !finite(input.zoneVolume[zoneIndex]) ||
                !finite(input.zoneGradient[zoneIndex])) {
                result.error = UmeGradzatzError::NonfiniteInput;
                return result;
            }
            if (input.zoneVolume[zoneIndex] != 0.0F ||
                input.zoneGradient[zoneIndex] != 0.0F) {
                result.error = UmeGradzatzError::NonzeroOutput;
                return result;
            }
            if (!touched[zoneIndex]) {
                touched[zoneIndex] = true;
                ++result.counters.distinctZones;
            }
            shadowVolume[zoneIndex] += volume;
            ++result.counters.fp32VolumeAdds;
            if (!finite(shadowVolume[zoneIndex])) {
                result.error = UmeGradzatzError::NonfiniteResult;
                return result;
            }
        }

        for (size_t zone = 0; zone < zoneCount; ++zone) {
            if (touched[zone] && shadowVolume[zone] == 0.0F) {
                result.error = UmeGradzatzError::ZeroZoneVolume;
                return result;
            }
        }

        for (size_t corner = 0; corner < cornerCount; ++corner) {
            if (input.cornerType[corner] < 1) {
                continue;
            }
            const size_t zone = static_cast<size_t>(
                input.cornerToZone[corner]);
            const size_t point = static_cast<size_t>(
                input.cornerToPoint[corner]);
            const float ratio =
                input.cornerVolume[corner] / shadowVolume[zone];
            ++result.counters.fp32Divides;
            if (!finite(ratio)) {
                result.error = UmeGradzatzError::NonfiniteResult;
                return result;
            }
            const double contribution =
                static_cast<double>(input.pointGradient[point]) * ratio;
            ++result.counters.fp64GradientMultiplies;
            const float next = static_cast<float>(
                static_cast<double>(shadowGradient[zone]) + contribution);
            ++result.counters.fp64GradientAdds;
            if (!finite(contribution) || !finite(next)) {
                result.error = UmeGradzatzError::NonfiniteResult;
                return result;
            }
            shadowGradient[zone] = next;
        }

        for (size_t zone = 0; zone < zoneCount; ++zone) {
            if (!touched[zone]) {
                continue;
            }
            result.zoneVolume[zone] = shadowVolume[zone];
            result.zoneGradient[zone] = shadowGradient[zone];
        }
        result.counters.logicalUpdates = 2 * result.counters.activeCorners;
        result.counters.consolidatedWrites = 2 * result.counters.distinctZones;
        return result;
    }
};

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_UME_GRADZATZ_MODEL_HH__
