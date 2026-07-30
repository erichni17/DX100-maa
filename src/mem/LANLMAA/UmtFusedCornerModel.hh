#ifndef __MEM_LANLMAA_UMT_FUSED_CORNER_MODEL_HH__
#define __MEM_LANLMAA_UMT_FUSED_CORNER_MODEL_HH__

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>

#include "mem/LANLMAA/UmtFusedCornerDescriptor.hh"

namespace gem5
{
namespace lanlmaa
{

struct UmtFusedCornerRecord
{
    double totalSource = 0.0;
    double oldPsi = 0.0;
    double crossSection = 0.0;
    std::array<double, 3> neighborTotalSource{};
    std::array<double, 3> neighborOldPsi{};
    std::array<double, 3> flux{};
};

// The live engine folds each total/old pair at ingestion so one paired
// operation/continuation entry retains eight FP64 values instead of all
// twelve input words.
struct UmtFusedCornerRetained
{
    double source = 0.0;
    double crossSection = 0.0;
    std::array<double, 3> neighborSource{};
    std::array<double, 3> flux{};
};

enum class UmtFusedCornerError : uint8_t
{
    None = 0,
    NonfiniteInput,
    NonpositivePhysics,
    NonfiniteResult
};

struct UmtFusedCornerResult
{
    double value = 0.0;
    UmtFusedCornerError error = UmtFusedCornerError::None;

    explicit operator bool() const
    {
        return error == UmtFusedCornerError::None;
    }
};

inline UmtFusedCornerResult
executeUmtFusedCornerRetained(
    const UmtFusedCornerDescriptor &descriptor,
    const UmtFusedCornerRetained &record)
{
    UmtFusedCornerResult result;
    if (!std::isfinite(record.source) ||
        !std::isfinite(record.crossSection)) {
        result.error = UmtFusedCornerError::NonfiniteInput;
        return result;
    }
    if (record.crossSection <= 0.0) {
        result.error = UmtFusedCornerError::NonpositivePhysics;
        return result;
    }
    for (size_t face = 0; face < 3; ++face) {
        if (!std::isfinite(record.neighborSource[face]) ||
            !std::isfinite(record.flux[face])) {
            result.error = UmtFusedCornerError::NonfiniteInput;
            return result;
        }
    }

    double ss = descriptor.volume * record.source;
    if (!std::isfinite(ss)) {
        result.error = UmtFusedCornerError::NonfiniteResult;
        return result;
    }
    for (size_t face = 0; face < 3; ++face) {
        ss -= descriptor.fpNorm[face] * record.flux[face];
        if (!std::isfinite(ss)) {
            result.error = UmtFusedCornerError::NonfiniteResult;
            return result;
        }
    }

    for (size_t face = 0; face < 3; ++face) {
        const double aez = descriptor.ezNorm[face];
        const size_t opposite = (face + 1) % 3;
        const double areaOpposite = -descriptor.fpNorm[opposite];
        const double psiOpposite = record.flux[opposite];
        const double sigv = record.crossSection * descriptor.volume;
        const double sigv2 = sigv * sigv;
        const double aez2 = aez * aez;
        const double gnum = aez2 *
            (1.82 * sigv2 + aez * (4.0 * sigv + 3.0 * aez));
        const double gden = descriptor.volume *
            (4.0 * sigv * sigv2 +
             aez * (6.0 * sigv2 +
                    2.0 * aez * (2.0 * sigv + aez)));
        const double sez =
            (descriptor.volume * gnum *
                 (record.crossSection * psiOpposite - record.source) +
             0.5 * aez * gden *
                 (record.source - record.neighborSource[face])) /
            (gnum + gden * record.crossSection);
        ss += 1.0 * sez;
        if (!std::isfinite(areaOpposite) || areaOpposite <= 0.0 ||
            !std::isfinite(psiOpposite) || !std::isfinite(sez) ||
            !std::isfinite(ss)) {
            result.error = UmtFusedCornerError::NonfiniteResult;
            return result;
        }
    }

    const double denominator =
        descriptor.normSum + record.crossSection * descriptor.volume;
    result.value = ss / denominator;
    if (!std::isfinite(denominator) || denominator <= 0.0 ||
        !std::isfinite(result.value)) {
        result.error = UmtFusedCornerError::NonfiniteResult;
    }
    return result;
}

inline UmtFusedCornerResult
executeUmtFusedCorner(const UmtFusedCornerDescriptor &descriptor,
                      const UmtFusedCornerRecord &record)
{
    UmtFusedCornerResult result;
    if (!std::isfinite(record.totalSource) ||
        !std::isfinite(record.oldPsi) ||
        !std::isfinite(record.crossSection)) {
        result.error = UmtFusedCornerError::NonfiniteInput;
        return result;
    }
    UmtFusedCornerRetained retained;
    retained.source =
        record.totalSource + descriptor.tau * record.oldPsi;
    retained.crossSection = record.crossSection;
    for (size_t face = 0; face < 3; ++face) {
        if (!std::isfinite(record.neighborTotalSource[face]) ||
            !std::isfinite(record.neighborOldPsi[face])) {
            result.error = UmtFusedCornerError::NonfiniteInput;
            return result;
        }
        retained.neighborSource[face] =
            record.neighborTotalSource[face] +
            descriptor.tau * record.neighborOldPsi[face];
        retained.flux[face] = record.flux[face];
    }
    if (!std::isfinite(retained.source) ||
        !std::all_of(
            retained.neighborSource.begin(), retained.neighborSource.end(),
            [](double value) { return std::isfinite(value); })) {
        result.error = UmtFusedCornerError::NonfiniteResult;
        return result;
    }
    return executeUmtFusedCornerRetained(descriptor, retained);
}

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_UMT_FUSED_CORNER_MODEL_HH__
