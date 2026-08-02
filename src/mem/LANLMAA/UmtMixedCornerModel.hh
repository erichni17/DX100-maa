#ifndef __MEM_LANLMAA_UMT_MIXED_CORNER_MODEL_HH__
#define __MEM_LANLMAA_UMT_MIXED_CORNER_MODEL_HH__

#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>

#include "mem/LANLMAA/UmtCornerSweepModel.hh"

namespace gem5
{
namespace lanlmaa
{

constexpr uint32_t UmtMixedCornerFaceCount = 3;
constexpr uint32_t UmtMixedCornerBaseRecordFp64Words = 12;
constexpr uint32_t UmtMixedCornerIncomingRecordFp64Words = 2;
constexpr uint32_t UmtMixedCornerRecordFp64Words = 18;
constexpr uint32_t UmtMixedCornerRecordBytes =
    UmtMixedCornerRecordFp64Words * sizeof(double);
constexpr uint32_t UmtMixedCornerRetainedFp64Words = 14;
constexpr uint32_t UmtMixedCornerRetainedBits =
    UmtMixedCornerRetainedFp64Words * sizeof(double) * 8;
constexpr uint32_t UmtMixedCornerOutgoingRetainedBits = 8 * sizeof(double) * 8;
constexpr uint32_t UmtMixedCornerRetainedDeltaBits =
    UmtMixedCornerRetainedBits - UmtMixedCornerOutgoingRetainedBits;
constexpr uint32_t UmtMixedCornerMaximumPairedContexts = 64;
constexpr uint32_t UmtMixedCornerMaximumRetainedDeltaBits =
    UmtMixedCornerRetainedDeltaBits * UmtMixedCornerMaximumPairedContexts;

struct UmtMixedCornerGeometry
{
    double tau = 0.0;
    double currentVolume = 0.0;
    double currentNormSum = 0.0;
    std::array<double, UmtMixedCornerFaceCount> currentFpNorm{};
    std::array<double, UmtMixedCornerFaceCount> signedEzNorm{};
    std::array<double, UmtMixedCornerFaceCount> firstVolume{};
    std::array<uint8_t, UmtMixedCornerFaceCount> oppositeActive{};
};

struct UmtMixedCornerRecord
{
    double totalSource = 0.0;
    double oldPsi = 0.0;
    double crossSection = 0.0;
    std::array<double, UmtMixedCornerFaceCount> neighborTotalSource{};
    std::array<double, UmtMixedCornerFaceCount> neighborOldPsi{};
    std::array<double, UmtMixedCornerFaceCount> currentFaceFlux{};
    std::array<double, UmtMixedCornerFaceCount> upstreamCornerFlux{};
    std::array<double, UmtMixedCornerFaceCount> oppositeFlux{};
};

static_assert(sizeof(UmtMixedCornerRecord) == UmtMixedCornerRecordBytes);

struct UmtMixedCornerRetained
{
    double source = 0.0;
    double crossSection = 0.0;
    std::array<double, UmtMixedCornerFaceCount> neighborSource{};
    std::array<double, UmtMixedCornerFaceCount> currentFaceFlux{};
    std::array<double, UmtMixedCornerFaceCount> upstreamCornerFlux{};
    std::array<double, UmtMixedCornerFaceCount> oppositeFlux{};
};

static_assert(sizeof(UmtMixedCornerRetained) ==
              UmtMixedCornerRetainedFp64Words * sizeof(double));

enum class UmtMixedCornerError : uint8_t
{
    None = 0,
    BadExtent,
    UnsupportedFaceCount,
    BadFaceIndex,
    BadReverseFace,
    InvalidGeometry,
    NonfiniteInput,
    NonpositivePhysics,
    NoncanonicalRecord,
    NonfiniteResult
};

struct UmtMixedCornerPlan
{
    UmtMixedCornerGeometry geometry;
    uint32_t currentCorner = 0;
    std::array<uint32_t, UmtMixedCornerFaceCount> neighborCorner{};
    std::array<uint32_t, UmtMixedCornerFaceCount> currentFluxPoint{};
    std::array<uint32_t, UmtMixedCornerFaceCount> upstreamCorner{};
    std::array<uint32_t, UmtMixedCornerFaceCount> oppositeFluxPoint{};
};

struct UmtMixedCornerPlanResult
{
    UmtMixedCornerPlan plan;
    UmtMixedCornerError error = UmtMixedCornerError::None;

    explicit operator bool() const
    {
        return error == UmtMixedCornerError::None;
    }
};

struct UmtMixedCornerRecordResult
{
    UmtMixedCornerRecord record;
    UmtMixedCornerError error = UmtMixedCornerError::None;

    explicit operator bool() const
    {
        return error == UmtMixedCornerError::None;
    }
};

struct UmtMixedCornerResult
{
    double value = 0.0;
    UmtMixedCornerError error = UmtMixedCornerError::None;

    explicit operator bool() const
    {
        return error == UmtMixedCornerError::None;
    }
};

inline bool
umtMixedCornerFiniteGeometry(const UmtMixedCornerGeometry &geometry)
{
    if (!std::isfinite(geometry.tau) ||
        !std::isfinite(geometry.currentVolume) ||
        geometry.currentVolume <= 0.0 ||
        !std::isfinite(geometry.currentNormSum) ||
        geometry.currentNormSum < 0.0) {
        return false;
    }
    for (size_t face = 0; face < UmtMixedCornerFaceCount; ++face) {
        if (!std::isfinite(geometry.currentFpNorm[face]) ||
            !std::isfinite(geometry.signedEzNorm[face]) ||
            geometry.signedEzNorm[face] == 0.0 ||
            !std::isfinite(geometry.firstVolume[face]) ||
            geometry.firstVolume[face] <= 0.0 ||
            geometry.oppositeActive[face] > 1) {
            return false;
        }
    }
    return true;
}

inline uint32_t
umtMixedCornerIncomingFaces(const UmtMixedCornerGeometry &geometry)
{
    uint32_t incoming = 0;
    for (double ezNorm : geometry.signedEzNorm) {
        incoming += ezNorm < 0.0;
    }
    return incoming;
}

inline uint32_t
umtMixedCornerPackedRecordBytes(const UmtMixedCornerGeometry &geometry)
{
    return (UmtMixedCornerBaseRecordFp64Words +
            UmtMixedCornerIncomingRecordFp64Words *
                umtMixedCornerIncomingFaces(geometry)) * sizeof(double);
}

inline uint32_t
umtMixedCornerRequiredRetainedBits(const UmtMixedCornerGeometry &geometry)
{
    return UmtMixedCornerOutgoingRetainedBits +
        umtMixedCornerIncomingFaces(geometry) * 2 * sizeof(double) * 8;
}

inline UmtMixedCornerPlanResult
buildUmtMixedCornerPlan(const UmtCornerSweepDescriptor &descriptor,
                        const UmtCornerSweepInput &input,
                        uint32_t currentCorner)
{
    UmtMixedCornerPlanResult result;
    if (descriptor.cornerCount == 0 ||
        currentCorner >= descriptor.cornerCount ||
        input.corners.size() < descriptor.cornerCount) {
        result.error = UmtMixedCornerError::BadExtent;
        return result;
    }

    const auto &current = input.corners[currentCorner];
    if (current.faceCount != UmtMixedCornerFaceCount) {
        result.error = UmtMixedCornerError::UnsupportedFaceCount;
        return result;
    }
    if (current.faceOffset > input.faces.size() ||
        current.faceCount > input.faces.size() - current.faceOffset) {
        result.error = UmtMixedCornerError::BadExtent;
        return result;
    }

    auto &plan = result.plan;
    auto &geometry = plan.geometry;
    plan.currentCorner = currentCorner;
    geometry.tau = descriptor.tau;
    geometry.currentVolume = current.volume;
    geometry.currentNormSum = current.normSum;

    for (uint32_t localFace = 0;
         localFace < UmtMixedCornerFaceCount; ++localFace) {
        const auto &face = input.faces[current.faceOffset + localFace];
        if (face.ezCorner >= descriptor.cornerCount ||
            face.fluxPoint >= descriptor.fluxPointCount) {
            result.error = UmtMixedCornerError::BadFaceIndex;
            return result;
        }
        if (!std::isfinite(face.fpNorm) ||
            !std::isfinite(face.ezNorm) || face.ezNorm == 0.0) {
            result.error = UmtMixedCornerError::InvalidGeometry;
            return result;
        }

        const bool outgoing = face.ezNorm > 0.0;
        const uint32_t firstCorner = outgoing ? currentCorner : face.ezCorner;
        const auto &first = input.corners[firstCorner];
        if (first.faceCount != UmtMixedCornerFaceCount) {
            result.error = UmtMixedCornerError::UnsupportedFaceCount;
            return result;
        }
        if (first.faceOffset > input.faces.size() ||
            first.faceCount > input.faces.size() - first.faceOffset) {
            result.error = UmtMixedCornerError::BadExtent;
            return result;
        }

        uint32_t opposite = (localFace + 1) % UmtMixedCornerFaceCount;
        if (!outgoing) {
            uint32_t matches = 0;
            for (uint32_t reverse = 0;
                 reverse < UmtMixedCornerFaceCount; ++reverse) {
                const auto &reverseFace =
                    input.faces[first.faceOffset + reverse];
                if (reverseFace.ezCorner >= descriptor.cornerCount ||
                    reverseFace.fluxPoint >= descriptor.fluxPointCount) {
                    result.error = UmtMixedCornerError::BadFaceIndex;
                    return result;
                }
                if (reverseFace.ezCorner == currentCorner) {
                    opposite = (reverse + 1) % UmtMixedCornerFaceCount;
                    ++matches;
                }
            }
            if (matches != 1) {
                result.error = UmtMixedCornerError::BadReverseFace;
                return result;
            }
        }

        const auto &oppositeFace = input.faces[first.faceOffset + opposite];
        if (oppositeFace.fluxPoint >= descriptor.fluxPointCount) {
            result.error = UmtMixedCornerError::BadFaceIndex;
            return result;
        }
        if (!std::isfinite(first.volume) || first.volume <= 0.0 ||
            !std::isfinite(oppositeFace.fpNorm)) {
            result.error = UmtMixedCornerError::InvalidGeometry;
            return result;
        }

        geometry.currentFpNorm[localFace] = face.fpNorm;
        geometry.signedEzNorm[localFace] = face.ezNorm;
        geometry.firstVolume[localFace] = first.volume;
        geometry.oppositeActive[localFace] = oppositeFace.fpNorm < 0.0;
        plan.neighborCorner[localFace] = face.ezCorner;
        plan.currentFluxPoint[localFace] = face.fluxPoint;
        plan.upstreamCorner[localFace] = outgoing ?
            std::numeric_limits<uint32_t>::max() : firstCorner;
        plan.oppositeFluxPoint[localFace] = oppositeFace.fluxPoint;
    }

    if (!umtMixedCornerFiniteGeometry(geometry)) {
        result.error = UmtMixedCornerError::InvalidGeometry;
    }
    return result;
}

inline UmtMixedCornerRecordResult
buildUmtMixedCornerRecord(const UmtCornerSweepDescriptor &descriptor,
                          const UmtCornerSweepInput &input,
                          const UmtMixedCornerPlan &plan,
                          uint32_t group)
{
    UmtMixedCornerRecordResult result;
    if (descriptor.totalGroups == 0 || group >= descriptor.totalGroups ||
        plan.currentCorner >= descriptor.cornerCount ||
        descriptor.fluxPointCount == 0) {
        result.error = UmtMixedCornerError::BadExtent;
        return result;
    }
    const size_t cornerValues =
        static_cast<size_t>(descriptor.cornerCount) * descriptor.totalGroups;
    const size_t zoneValues =
        static_cast<size_t>(descriptor.zoneCount) * descriptor.totalGroups;
    const size_t fluxValues =
        static_cast<size_t>(descriptor.fluxPointCount) *
        descriptor.totalGroups;
    if (input.totalSource.size() < cornerValues ||
        input.oldPsi.size() < cornerValues ||
        input.totalCrossSection.size() < zoneValues ||
        input.psi1.size() < fluxValues || descriptor.zoneCount != 1) {
        result.error = UmtMixedCornerError::BadExtent;
        return result;
    }

    auto &record = result.record;
    const size_t currentValue =
        static_cast<size_t>(plan.currentCorner) * descriptor.totalGroups +
        group;
    record.totalSource = input.totalSource[currentValue];
    record.oldPsi = input.oldPsi[currentValue];
    record.crossSection = input.totalCrossSection[group];

    for (size_t face = 0; face < UmtMixedCornerFaceCount; ++face) {
        if (plan.neighborCorner[face] >= descriptor.cornerCount ||
            plan.currentFluxPoint[face] >= descriptor.fluxPointCount ||
            plan.oppositeFluxPoint[face] >= descriptor.fluxPointCount) {
            result.error = UmtMixedCornerError::BadFaceIndex;
            return result;
        }
        const size_t neighborValue =
            static_cast<size_t>(plan.neighborCorner[face]) *
            descriptor.totalGroups + group;
        record.neighborTotalSource[face] = input.totalSource[neighborValue];
        record.neighborOldPsi[face] = input.oldPsi[neighborValue];
        if (plan.geometry.currentFpNorm[face] < 0.0) {
            const size_t fluxValue =
                static_cast<size_t>(plan.currentFluxPoint[face]) *
                descriptor.totalGroups + group;
            record.currentFaceFlux[face] = input.psi1[fluxValue];
        }
        if (plan.geometry.signedEzNorm[face] < 0.0) {
            if (plan.upstreamCorner[face] >= descriptor.cornerCount ||
                plan.upstreamCorner[face] >= descriptor.fluxPointCount) {
                result.error = UmtMixedCornerError::BadFaceIndex;
                return result;
            }
            const size_t upstreamValue =
                static_cast<size_t>(plan.upstreamCorner[face]) *
                descriptor.totalGroups + group;
            record.upstreamCornerFlux[face] = input.psi1[upstreamValue];
        }
        if (plan.geometry.signedEzNorm[face] < 0.0 &&
            plan.geometry.oppositeActive[face]) {
            const size_t oppositeValue =
                static_cast<size_t>(plan.oppositeFluxPoint[face]) *
                descriptor.totalGroups + group;
            record.oppositeFlux[face] = input.psi1[oppositeValue];
        }
    }
    return result;
}

inline UmtMixedCornerResult
executeUmtMixedCornerRetained(const UmtMixedCornerGeometry &geometry,
                              const UmtMixedCornerRetained &record)
{
    UmtMixedCornerResult result;
    if (!umtMixedCornerFiniteGeometry(geometry)) {
        result.error = UmtMixedCornerError::InvalidGeometry;
        return result;
    }
    if (!std::isfinite(record.source) ||
        !std::isfinite(record.crossSection)) {
        result.error = UmtMixedCornerError::NonfiniteInput;
        return result;
    }
    if (record.crossSection <= 0.0) {
        result.error = UmtMixedCornerError::NonpositivePhysics;
        return result;
    }
    for (size_t face = 0; face < UmtMixedCornerFaceCount; ++face) {
        if (!std::isfinite(record.neighborSource[face]) ||
            !std::isfinite(record.currentFaceFlux[face]) ||
            !std::isfinite(record.upstreamCornerFlux[face]) ||
            !std::isfinite(record.oppositeFlux[face])) {
            result.error = UmtMixedCornerError::NonfiniteInput;
            return result;
        }
        const bool outgoing = geometry.signedEzNorm[face] > 0.0;
        if ((geometry.currentFpNorm[face] >= 0.0 &&
             record.currentFaceFlux[face] != 0.0) ||
            (outgoing && record.upstreamCornerFlux[face] != 0.0) ||
            ((outgoing || !geometry.oppositeActive[face]) &&
             record.oppositeFlux[face] != 0.0)) {
            result.error = UmtMixedCornerError::NoncanonicalRecord;
            return result;
        }
    }

    double ss = geometry.currentVolume * record.source;
    if (!std::isfinite(ss)) {
        result.error = UmtMixedCornerError::NonfiniteResult;
        return result;
    }
    for (size_t face = 0; face < UmtMixedCornerFaceCount; ++face) {
        if (geometry.currentFpNorm[face] < 0.0) {
            ss -= geometry.currentFpNorm[face] *
                record.currentFaceFlux[face];
            if (!std::isfinite(ss)) {
                result.error = UmtMixedCornerError::NonfiniteResult;
                return result;
            }
        }
    }

    for (size_t face = 0; face < UmtMixedCornerFaceCount; ++face) {
        const bool outgoing = geometry.signedEzNorm[face] > 0.0;
        const double aez = outgoing ? geometry.signedEzNorm[face] :
            -geometry.signedEzNorm[face];
        const double multiplier = outgoing ? 1.0 : -1.0;
        if (!outgoing) {
            ss -= geometry.signedEzNorm[face] *
                record.upstreamCornerFlux[face];
            if (!std::isfinite(ss)) {
                result.error = UmtMixedCornerError::NonfiniteResult;
                return result;
            }
        }

        const double qq = outgoing ? record.source :
            record.neighborSource[face];
        const double qez = outgoing ? record.neighborSource[face] :
            record.source;
        double sez = 0.0;
        if (geometry.oppositeActive[face]) {
            const double volume = geometry.firstVolume[face];
            const double psiOpposite = outgoing ?
                record.currentFaceFlux[(face + 1) %
                                       UmtMixedCornerFaceCount] :
                record.oppositeFlux[face];
            const double sigv = record.crossSection * volume;
            const double sigv2 = sigv * sigv;
            const double aez2 = aez * aez;
            const double gnum = aez2 *
                (1.82 * sigv2 + aez * (4.0 * sigv + 3.0 * aez));
            const double gden = volume *
                (4.0 * sigv * sigv2 +
                 aez * (6.0 * sigv2 +
                        2.0 * aez * (2.0 * sigv + aez)));
            sez = (volume * gnum *
                   (record.crossSection * psiOpposite - qq) +
                   0.5 * aez * gden * (qq - qez)) /
                (gnum + gden * record.crossSection);
        } else {
            sez = 0.5 * aez * (qq - qez) / record.crossSection;
        }
        ss += multiplier * sez;
        if (!std::isfinite(sez) || !std::isfinite(ss)) {
            result.error = UmtMixedCornerError::NonfiniteResult;
            return result;
        }
    }

    const double denominator = geometry.currentNormSum +
        record.crossSection * geometry.currentVolume;
    result.value = ss / denominator;
    if (!std::isfinite(denominator) || denominator <= 0.0 ||
        !std::isfinite(result.value)) {
        result.error = UmtMixedCornerError::NonfiniteResult;
    }
    return result;
}

inline UmtMixedCornerResult
executeUmtMixedCorner(const UmtMixedCornerGeometry &geometry,
                      const UmtMixedCornerRecord &record)
{
    UmtMixedCornerResult result;
    if (!std::isfinite(record.totalSource) ||
        !std::isfinite(record.oldPsi) ||
        !std::isfinite(record.crossSection)) {
        result.error = UmtMixedCornerError::NonfiniteInput;
        return result;
    }
    UmtMixedCornerRetained retained;
    retained.source = record.totalSource + geometry.tau * record.oldPsi;
    retained.crossSection = record.crossSection;
    for (size_t face = 0; face < UmtMixedCornerFaceCount; ++face) {
        if (!std::isfinite(record.neighborTotalSource[face]) ||
            !std::isfinite(record.neighborOldPsi[face])) {
            result.error = UmtMixedCornerError::NonfiniteInput;
            return result;
        }
        retained.neighborSource[face] =
            record.neighborTotalSource[face] +
            geometry.tau * record.neighborOldPsi[face];
        retained.currentFaceFlux[face] = record.currentFaceFlux[face];
        retained.upstreamCornerFlux[face] = record.upstreamCornerFlux[face];
        retained.oppositeFlux[face] = record.oppositeFlux[face];
    }
    if (!std::isfinite(retained.source)) {
        result.error = UmtMixedCornerError::NonfiniteResult;
        return result;
    }
    return executeUmtMixedCornerRetained(geometry, retained);
}

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_UMT_MIXED_CORNER_MODEL_HH__
