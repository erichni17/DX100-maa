#ifndef __MEM_LANLMAA_UME_GRADZATP_MODEL_HH__
#define __MEM_LANLMAA_UME_GRADZATP_MODEL_HH__

#include <cmath>
#include <cstdint>
#include <limits>
#include <vector>

#include "mem/LANLMAA/UmeGradzatpDescriptor.hh"

namespace gem5
{
namespace lanlmaa
{

struct UmeGradzatpInput
{
    std::vector<int32_t> cornerType;
    std::vector<int32_t> cornerToZone;
    std::vector<int32_t> cornerToPoint;
    std::vector<float> cornerVolume;
    std::vector<float> cornerSurface;
    std::vector<float> zoneField;
    std::vector<float> pointVolume;
    std::vector<float> pointGradient;
};

enum class UmeGradzatpExecutionError : uint8_t
{
    None = 0,
    SourceExtent,
    BadZoneIndex,
    BadPointIndex,
    NonfiniteInput,
    NonzeroOutput,
    UnsafeAccumulationBound,
    NonfiniteResult
};

struct UmeGradzatpCounters
{
    uint32_t cornersValidated = 0;
    uint32_t activeCorners = 0;
    uint32_t inactiveCorners = 0;
    uint32_t zoneFieldGathers = 0;
    uint32_t outputZeroReads = 0;
    uint32_t fp32Multiplies = 0;
    uint32_t logicalFp32Updates = 0;
    uint32_t updateAcknowledgements = 0;
};

struct UmeGradzatpExecutionResult
{
    UmeGradzatpExecutionError error = UmeGradzatpExecutionError::None;
    UmeGradzatpCounters counters;
    std::vector<float> pointVolume;
    std::vector<float> pointGradient;

    explicit operator bool() const
    {
        return error == UmeGradzatpExecutionError::None;
    }
};

class UmeGradzatpModel
{
  private:
    static bool
    finite(float value)
    {
        return std::isfinite(value);
    }

  public:
    static UmeGradzatpExecutionResult
    execute(const UmeGradzatpDescriptor &descriptor,
            const UmeGradzatpInput &input)
    {
        UmeGradzatpExecutionResult result;
        result.pointVolume = input.pointVolume;
        result.pointGradient = input.pointGradient;
        if (input.cornerType.size() < descriptor.cornerCount ||
            input.cornerToZone.size() < descriptor.cornerCount ||
            input.cornerToPoint.size() < descriptor.cornerCount ||
            input.cornerVolume.size() < descriptor.cornerCount ||
            input.cornerSurface.size() < descriptor.cornerCount ||
            input.zoneField.size() < descriptor.zoneCount ||
            input.pointVolume.size() != descriptor.pointCount ||
            input.pointGradient.size() != descriptor.pointCount) {
            result.error = UmeGradzatpExecutionError::SourceExtent;
            return result;
        }

        for (uint32_t corner = 0; corner < descriptor.cornerCount;
             ++corner) {
            ++result.counters.cornersValidated;
            if (input.cornerType[corner] < 1) {
                ++result.counters.inactiveCorners;
                continue;
            }
            ++result.counters.activeCorners;
        }
        if (result.counters.activeCorners == 0) {
            return result;
        }

        const float safeMagnitude =
            std::numeric_limits<float>::max() /
            static_cast<float>(result.counters.activeCorners);
        for (uint32_t corner = 0; corner < descriptor.cornerCount;
             ++corner) {
            if (input.cornerType[corner] < 1) {
                continue;
            }
            const int32_t zone = input.cornerToZone[corner];
            const int32_t point = input.cornerToPoint[corner];
            if (zone < 0 || static_cast<uint32_t>(zone) >=
                    descriptor.zoneCount) {
                result.error = UmeGradzatpExecutionError::BadZoneIndex;
                return result;
            }
            if (point < 0 || static_cast<uint32_t>(point) >=
                    descriptor.pointCount) {
                result.error = UmeGradzatpExecutionError::BadPointIndex;
                return result;
            }
            const float volume = input.cornerVolume[corner];
            const float surface = input.cornerSurface[corner];
            const float zoneValue = input.zoneField[zone];
            const float pointVolume = input.pointVolume[point];
            const float pointGradient = input.pointGradient[point];
            if (!finite(volume) || !finite(surface) || !finite(zoneValue) ||
                !finite(pointVolume) || !finite(pointGradient)) {
                result.error = UmeGradzatpExecutionError::NonfiniteInput;
                return result;
            }
            if (pointVolume != 0.0F || pointGradient != 0.0F) {
                result.error = UmeGradzatpExecutionError::NonzeroOutput;
                return result;
            }
            const float gradient = surface * zoneValue;
            if (!finite(gradient)) {
                result.error = UmeGradzatpExecutionError::NonfiniteResult;
                return result;
            }
            if (std::fabs(volume) > safeMagnitude ||
                std::fabs(gradient) > safeMagnitude) {
                result.error =
                    UmeGradzatpExecutionError::UnsafeAccumulationBound;
                return result;
            }
            ++result.counters.zoneFieldGathers;
            result.counters.outputZeroReads += 2;
            ++result.counters.fp32Multiplies;
        }

        for (uint32_t corner = 0; corner < descriptor.cornerCount;
             ++corner) {
            if (input.cornerType[corner] < 1) {
                continue;
            }
            const uint32_t zone = static_cast<uint32_t>(
                input.cornerToZone[corner]);
            const uint32_t point = static_cast<uint32_t>(
                input.cornerToPoint[corner]);
            const float gradient =
                input.cornerSurface[corner] * input.zoneField[zone];
            result.pointVolume[point] += input.cornerVolume[corner];
            result.pointGradient[point] += gradient;
            if (!finite(result.pointVolume[point]) ||
                !finite(result.pointGradient[point])) {
                result.error = UmeGradzatpExecutionError::NonfiniteResult;
                return result;
            }
            result.counters.logicalFp32Updates += 2;
            result.counters.updateAcknowledgements += 2;
        }
        return result;
    }
};

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_UME_GRADZATP_MODEL_HH__
