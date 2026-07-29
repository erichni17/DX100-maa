#ifndef __MEM_LANLMAA_UMT_CORNER_SWEEP_MODEL_HH__
#define __MEM_LANLMAA_UMT_CORNER_SWEEP_MODEL_HH__

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <set>
#include <vector>

namespace gem5
{
namespace lanlmaa
{

constexpr uint32_t UmtSweepMaximumSelectedCorners = 16;
constexpr uint32_t UmtSweepMaximumGroups = 32;
constexpr uint32_t UmtSweepMaximumCornerGroups = 64;
constexpr uint32_t UmtSweepMaximumFacesPerCorner = 8;
constexpr uint64_t UmtSweepOperationWindowEntries = 64;

struct UmtCornerSweepDescriptor
{
    uint32_t cornerCount = 0;
    uint32_t zoneCount = 0;
    uint32_t fluxPointCount = 0;
    uint32_t totalGroups = 0;
    uint32_t selectedCornerCount = 0;
    uint32_t firstGroup = 0;
    uint32_t groupCount = 0;
    double tau = 0.0;
};

struct UmtSweepCorner
{
    uint32_t zone = 0;
    uint32_t faceOffset = 0;
    uint32_t faceCount = 0;
    double volume = 0.0;
    double normSum = 0.0;
};

struct UmtSweepFace
{
    uint32_t fluxPoint = 0;
    uint32_t ezCorner = 0;
    double fpNorm = 0.0;
    double ezNorm = 0.0;
};

struct UmtCornerSweepInput
{
    std::vector<uint32_t> cornerOrder;
    std::vector<UmtSweepCorner> corners;
    std::vector<UmtSweepFace> faces;
    std::vector<double> totalSource;
    std::vector<double> oldPsi;
    std::vector<double> totalCrossSection;
    std::vector<double> psi1;
};

enum class UmtCornerSweepError : uint8_t
{
    None = 0,
    Empty,
    TooManyCorners,
    TooManyGroups,
    TooManyCornerGroups,
    SourceExtent,
    BadGroupRange,
    BadCornerIndex,
    DuplicateCorner,
    BadZoneIndex,
    BadFaceCount,
    BadFaceRange,
    BadFaceIndex,
    CrossZoneFace,
    ZeroInteriorProjection,
    SelectedDependency,
    BadReverseFace,
    NonfiniteInput,
    NonpositivePhysics,
    NonfiniteResult
};

struct UmtCornerSweepCounters
{
    uint32_t cornersValidated = 0;
    uint32_t cornersCommitted = 0;
    uint64_t cornerGroupsProcessed = 0;
    uint64_t geometryFaceVisits = 0;
    uint64_t externalFaceVisits = 0;
    uint64_t interiorFaceVisits = 0;
    uint64_t reverseFaceSearchVisits = 0;
    uint64_t oppositeFaceVisits = 0;
    uint64_t sourceReads = 0;
    uint64_t crossSectionReads = 0;
    uint64_t fluxReads = 0;
    uint64_t uniqueFluxCacheLines = 0;
    uint64_t specialOppositeFaceUpdates = 0;
    uint64_t fallbackFaceUpdates = 0;
    uint64_t operationWindowFills = 0;
    uint64_t outputWrites = 0;
};

struct UmtCornerSweepResult
{
    UmtCornerSweepError error = UmtCornerSweepError::None;
    UmtCornerSweepCounters counters;
    std::vector<double> psi1;

    explicit operator bool() const
    {
        return error == UmtCornerSweepError::None;
    }
};

class UmtCornerSweepModel
{
  private:
    static bool
    finite(double value)
    {
        return std::isfinite(value);
    }

    static bool
    exactExtent(size_t outer, size_t inner, size_t actual)
    {
        return inner == 0 ? actual == 0 :
            outer <= std::numeric_limits<size_t>::max() / inner &&
            outer * inner == actual;
    }

  public:
    static UmtCornerSweepResult
    execute(const UmtCornerSweepDescriptor &descriptor,
            const UmtCornerSweepInput &input)
    {
        UmtCornerSweepResult result;
        result.psi1 = input.psi1;
        if (descriptor.selectedCornerCount == 0 ||
            descriptor.groupCount == 0) {
            result.error = UmtCornerSweepError::Empty;
            return result;
        }
        if (descriptor.selectedCornerCount >
                UmtSweepMaximumSelectedCorners) {
            result.error = UmtCornerSweepError::TooManyCorners;
            return result;
        }
        if (descriptor.groupCount > UmtSweepMaximumGroups) {
            result.error = UmtCornerSweepError::TooManyGroups;
            return result;
        }
        const uint64_t cornerGroups =
            static_cast<uint64_t>(descriptor.selectedCornerCount) *
            descriptor.groupCount;
        if (cornerGroups > UmtSweepMaximumCornerGroups) {
            result.error = UmtCornerSweepError::TooManyCornerGroups;
            return result;
        }
        if (descriptor.cornerCount == 0 || descriptor.zoneCount == 0 ||
            descriptor.fluxPointCount < descriptor.cornerCount ||
            descriptor.totalGroups == 0 ||
            input.cornerOrder.size() != descriptor.selectedCornerCount ||
            input.corners.size() != descriptor.cornerCount ||
            !exactExtent(descriptor.cornerCount, descriptor.totalGroups,
                         input.totalSource.size()) ||
            !exactExtent(descriptor.cornerCount, descriptor.totalGroups,
                         input.oldPsi.size()) ||
            !exactExtent(descriptor.zoneCount, descriptor.totalGroups,
                         input.totalCrossSection.size()) ||
            !exactExtent(descriptor.fluxPointCount, descriptor.totalGroups,
                         input.psi1.size())) {
            result.error = UmtCornerSweepError::SourceExtent;
            return result;
        }
        if (descriptor.firstGroup >= descriptor.totalGroups ||
            descriptor.groupCount >
                descriptor.totalGroups - descriptor.firstGroup) {
            result.error = UmtCornerSweepError::BadGroupRange;
            return result;
        }
        if (!finite(descriptor.tau)) {
            result.error = UmtCornerSweepError::NonfiniteInput;
            return result;
        }

        std::vector<bool> selected(descriptor.cornerCount, false);
        for (const uint32_t corner : input.cornerOrder) {
            if (corner >= descriptor.cornerCount) {
                result.error = UmtCornerSweepError::BadCornerIndex;
                return result;
            }
            if (selected[corner]) {
                result.error = UmtCornerSweepError::DuplicateCorner;
                return result;
            }
            selected[corner] = true;
        }

        auto validCorner = [&](uint32_t index) {
            const auto &corner = input.corners[index];
            if (corner.zone >= descriptor.zoneCount) {
                result.error = UmtCornerSweepError::BadZoneIndex;
                return false;
            }
            if (corner.faceCount < 3 ||
                corner.faceCount > UmtSweepMaximumFacesPerCorner) {
                result.error = UmtCornerSweepError::BadFaceCount;
                return false;
            }
            if (corner.faceOffset > input.faces.size() ||
                corner.faceCount > input.faces.size() - corner.faceOffset) {
                result.error = UmtCornerSweepError::BadFaceRange;
                return false;
            }
            if (!finite(corner.volume) || !finite(corner.normSum)) {
                result.error = UmtCornerSweepError::NonfiniteInput;
                return false;
            }
            if (corner.volume <= 0.0 || corner.normSum < 0.0) {
                result.error = UmtCornerSweepError::NonpositivePhysics;
                return false;
            }
            return true;
        };

        std::set<uint32_t> validatedCorners;
        for (const uint32_t current : input.cornerOrder) {
            if (!validCorner(current)) {
                return result;
            }
            validatedCorners.insert(current);
            const auto &corner = input.corners[current];
            for (uint32_t localFace = 0; localFace < corner.faceCount;
                 ++localFace) {
                const auto &face = input.faces[corner.faceOffset + localFace];
                ++result.counters.geometryFaceVisits;
                if (face.fluxPoint >= descriptor.fluxPointCount ||
                    face.ezCorner >= descriptor.cornerCount ||
                    face.ezCorner == current) {
                    result.error = UmtCornerSweepError::BadFaceIndex;
                    return result;
                }
                if (!finite(face.fpNorm) || !finite(face.ezNorm)) {
                    result.error = UmtCornerSweepError::NonfiniteInput;
                    return result;
                }
                if (face.ezNorm == 0.0) {
                    result.error =
                        UmtCornerSweepError::ZeroInteriorProjection;
                    return result;
                }
                if (input.corners[face.ezCorner].zone != corner.zone) {
                    result.error = UmtCornerSweepError::CrossZoneFace;
                    return result;
                }
                if (selected[face.ezCorner]) {
                    result.error = UmtCornerSweepError::SelectedDependency;
                    return result;
                }
                if (!validCorner(face.ezCorner)) {
                    return result;
                }
                validatedCorners.insert(face.ezCorner);
                if (face.ezNorm < 0.0) {
                    const auto &upstream = input.corners[face.ezCorner];
                    if (upstream.faceCount != corner.faceCount) {
                        result.error = UmtCornerSweepError::BadReverseFace;
                        return result;
                    }
                    uint32_t matches = 0;
                    for (uint32_t reverse = 0; reverse < upstream.faceCount;
                         ++reverse) {
                        const auto &reverseFace =
                            input.faces[upstream.faceOffset + reverse];
                        if (reverseFace.ezCorner >= descriptor.cornerCount) {
                            result.error = UmtCornerSweepError::BadFaceIndex;
                            return result;
                        }
                        if (reverseFace.ezCorner == current) {
                            ++matches;
                        }
                    }
                    if (matches != 1) {
                        result.error = UmtCornerSweepError::BadReverseFace;
                        return result;
                    }
                }
            }
        }
        result.counters.cornersValidated = validatedCorners.size();

        std::set<uint64_t> fluxCacheLines;
        std::vector<double> shadow(cornerGroups, 0.0);
        const uint32_t groupEnd =
            descriptor.firstGroup + descriptor.groupCount;
        size_t shadowIndex = 0;
        for (const uint32_t current : input.cornerOrder) {
            const auto &corner = input.corners[current];
            for (uint32_t group = descriptor.firstGroup; group < groupEnd;
                 ++group) {
                const size_t currentValue =
                    static_cast<size_t>(current) * descriptor.totalGroups +
                    group;
                const size_t sigmaIndex =
                    static_cast<size_t>(corner.zone) *
                    descriptor.totalGroups + group;
                const double sigma = input.totalCrossSection[sigmaIndex];
                const double totalSource = input.totalSource[currentValue];
                const double oldPsi = input.oldPsi[currentValue];
                result.counters.sourceReads += 2;
                ++result.counters.crossSectionReads;
                if (!finite(sigma) || !finite(totalSource) ||
                    !finite(oldPsi)) {
                    result.error = UmtCornerSweepError::NonfiniteInput;
                    return result;
                }
                if (sigma <= 0.0) {
                    result.error = UmtCornerSweepError::NonpositivePhysics;
                    return result;
                }
                const double source = totalSource + descriptor.tau * oldPsi;
                double ss = corner.volume * source;
                if (!finite(source) || !finite(ss)) {
                    result.error = UmtCornerSweepError::NonfiniteResult;
                    return result;
                }

                for (uint32_t localFace = 0;
                     localFace < corner.faceCount; ++localFace) {
                    const auto &face =
                        input.faces[corner.faceOffset + localFace];
                    ++result.counters.externalFaceVisits;
                    if (face.fpNorm < 0.0) {
                        const size_t fluxIndex =
                            static_cast<size_t>(face.fluxPoint) *
                            descriptor.totalGroups + group;
                        const double flux = input.psi1[fluxIndex];
                        ++result.counters.fluxReads;
                        fluxCacheLines.insert(fluxIndex / 8);
                        if (!finite(flux)) {
                            result.error = UmtCornerSweepError::NonfiniteInput;
                            return result;
                        }
                        ss -= face.fpNorm * flux;
                        if (!finite(ss)) {
                            result.error =
                                UmtCornerSweepError::NonfiniteResult;
                            return result;
                        }
                    }
                }

                for (uint32_t localFace = 0;
                     localFace < corner.faceCount; ++localFace) {
                    const auto &face =
                        input.faces[corner.faceOffset + localFace];
                    ++result.counters.interiorFaceVisits;
                    const bool outgoing = face.ezNorm > 0.0;
                    const uint32_t firstCorner =
                        outgoing ? current : face.ezCorner;
                    const uint32_t secondCorner =
                        outgoing ? face.ezCorner : current;
                    const auto &first = input.corners[firstCorner];
                    double aez = outgoing ? face.ezNorm : -face.ezNorm;
                    const double multiplier = outgoing ? 1.0 : -1.0;

                    uint32_t oppositeStart = (localFace + 1) %
                        corner.faceCount;
                    if (!outgoing) {
                        const size_t upstreamFlux =
                            static_cast<size_t>(firstCorner) *
                            descriptor.totalGroups + group;
                        const double flux = input.psi1[upstreamFlux];
                        ++result.counters.fluxReads;
                        fluxCacheLines.insert(upstreamFlux / 8);
                        if (!finite(flux)) {
                            result.error =
                                UmtCornerSweepError::NonfiniteInput;
                            return result;
                        }
                        ss -= face.ezNorm * flux;
                        if (!finite(ss)) {
                            result.error =
                                UmtCornerSweepError::NonfiniteResult;
                            return result;
                        }
                        uint32_t matches = 0;
                        for (uint32_t reverse = 0;
                             reverse < first.faceCount; ++reverse) {
                            ++result.counters.reverseFaceSearchVisits;
                            const auto &reverseFace =
                                input.faces[first.faceOffset + reverse];
                            if (reverseFace.ezCorner >=
                                    descriptor.cornerCount) {
                                result.error =
                                    UmtCornerSweepError::BadFaceIndex;
                                return result;
                            }
                            if (reverseFace.ezCorner == secondCorner) {
                                oppositeStart =
                                    (reverse + 1) % first.faceCount;
                                ++matches;
                            }
                        }
                        if (matches != 1) {
                            result.error =
                                UmtCornerSweepError::BadReverseFace;
                            return result;
                        }
                    }

                    const size_t neighborValue =
                        static_cast<size_t>(face.ezCorner) *
                        descriptor.totalGroups + group;
                    const double neighborSource =
                        input.totalSource[neighborValue] +
                        descriptor.tau * input.oldPsi[neighborValue];
                    result.counters.sourceReads += 2;
                    if (!finite(input.totalSource[neighborValue]) ||
                        !finite(input.oldPsi[neighborValue])) {
                        result.error = UmtCornerSweepError::NonfiniteInput;
                        return result;
                    }
                    if (!finite(neighborSource)) {
                        result.error = UmtCornerSweepError::NonfiniteResult;
                        return result;
                    }
                    const double qq = outgoing ? source : neighborSource;
                    const double qez = outgoing ? neighborSource : source;

                    double areaOpposite = 0.0;
                    double psiOpposite = 0.0;
                    const uint32_t oppositeCount = first.faceCount - 2;
                    for (uint32_t candidate = 0;
                         candidate < oppositeCount; ++candidate) {
                        ++result.counters.oppositeFaceVisits;
                        const uint32_t opposite =
                            (oppositeStart + candidate) % first.faceCount;
                        const auto &oppositeFace =
                            input.faces[first.faceOffset + opposite];
                        if (oppositeFace.fluxPoint >=
                                descriptor.fluxPointCount) {
                            result.error =
                                UmtCornerSweepError::BadFaceIndex;
                            return result;
                        }
                        if (!finite(oppositeFace.fpNorm)) {
                            result.error =
                                UmtCornerSweepError::NonfiniteInput;
                            return result;
                        }
                        if (oppositeFace.fpNorm < 0.0) {
                            const size_t fluxIndex =
                                static_cast<size_t>(
                                    oppositeFace.fluxPoint) *
                                descriptor.totalGroups + group;
                            const double flux = input.psi1[fluxIndex];
                            ++result.counters.fluxReads;
                            fluxCacheLines.insert(fluxIndex / 8);
                            if (!finite(flux)) {
                                result.error =
                                    UmtCornerSweepError::NonfiniteInput;
                                return result;
                            }
                            areaOpposite -= oppositeFace.fpNorm;
                            psiOpposite -= oppositeFace.fpNorm * flux;
                        }
                    }

                    double sez = 0.0;
                    if (areaOpposite > 0.0) {
                        psiOpposite /= areaOpposite;
                        const double sigv = sigma * first.volume;
                        const double sigv2 = sigv * sigv;
                        const double aez2 = aez * aez;
                        const double gnum = aez2 *
                            (1.82 * sigv2 +
                             aez * (4.0 * sigv + 3.0 * aez));
                        const double gden = first.volume *
                            (4.0 * sigv * sigv2 +
                             aez * (6.0 * sigv2 +
                                    2.0 * aez *
                                    (2.0 * sigv + aez)));
                        sez = (first.volume * gnum *
                               (sigma * psiOpposite - qq) +
                               0.5 * aez * gden * (qq - qez)) /
                            (gnum + gden * sigma);
                        ++result.counters.specialOppositeFaceUpdates;
                    } else {
                        sez = 0.5 * aez * (qq - qez) / sigma;
                        ++result.counters.fallbackFaceUpdates;
                    }
                    ss += multiplier * sez;
                    if (!finite(areaOpposite) || !finite(psiOpposite) ||
                        !finite(sez) || !finite(ss)) {
                        result.error = UmtCornerSweepError::NonfiniteResult;
                        return result;
                    }
                }

                const double denominator =
                    corner.normSum + sigma * corner.volume;
                const double value = ss / denominator;
                if (!finite(denominator) || denominator <= 0.0 ||
                    !finite(value)) {
                    result.error = UmtCornerSweepError::NonfiniteResult;
                    return result;
                }
                shadow[shadowIndex++] = value;
                ++result.counters.cornerGroupsProcessed;
            }
        }

        const uint64_t faceOperations =
            result.counters.externalFaceVisits +
            result.counters.interiorFaceVisits +
            result.counters.reverseFaceSearchVisits +
            result.counters.oppositeFaceVisits;
        result.counters.operationWindowFills =
            (faceOperations + UmtSweepOperationWindowEntries - 1) /
            UmtSweepOperationWindowEntries;
        result.counters.uniqueFluxCacheLines = fluxCacheLines.size();

        shadowIndex = 0;
        for (const uint32_t current : input.cornerOrder) {
            for (uint32_t group = descriptor.firstGroup; group < groupEnd;
                 ++group) {
                const size_t output =
                    static_cast<size_t>(current) * descriptor.totalGroups +
                    group;
                result.psi1[output] = shadow[shadowIndex++];
                ++result.counters.outputWrites;
            }
            ++result.counters.cornersCommitted;
        }
        return result;
    }
};

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_UMT_CORNER_SWEEP_MODEL_HH__
