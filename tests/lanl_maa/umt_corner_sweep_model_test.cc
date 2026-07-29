#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <vector>

#include "mem/LANLMAA/UmtCornerSweepModel.hh"

using namespace gem5::lanlmaa;

namespace
{

struct Fixture
{
    UmtCornerSweepDescriptor descriptor;
    UmtCornerSweepInput input;
};

size_t
valueIndex(const Fixture &value, uint32_t point, uint32_t group)
{
    return static_cast<size_t>(point) * value.descriptor.totalGroups + group;
}

Fixture
fixture()
{
    Fixture value;
    value.descriptor.cornerCount = 4;
    value.descriptor.zoneCount = 1;
    value.descriptor.fluxPointCount = 8;
    value.descriptor.totalGroups = 4;
    value.descriptor.selectedCornerCount = 1;
    value.descriptor.firstGroup = 1;
    value.descriptor.groupCount = 2;
    value.descriptor.tau = 0.25;
    value.input.cornerOrder = {0};
    value.input.corners = {
        {0, 0, 3, 2.0, 3.0},
        {0, 3, 3, 1.25, 2.0},
        {0, 6, 3, 1.5, 2.5},
        {0, 9, 3, 0.75, 1.5},
    };
    value.input.faces = {
        {4, 1, -2.0, 0.8},
        {5, 2, 0.5, -0.6},
        {6, 3, -1.0, 0.4},
        {4, 0, 1.0, -0.8},
        {5, 2, -0.25, 0.2},
        {6, 3, 0.5, -0.1},
        {5, 0, 0.3, 0.6},
        {7, 1, -1.5, 0.2},
        {6, 3, 0.4, -0.2},
        {6, 0, 0.7, -0.4},
        {4, 1, -0.2, 0.3},
        {7, 2, 0.6, 0.1},
    };

    const size_t cornerValues =
        value.descriptor.cornerCount * value.descriptor.totalGroups;
    value.input.totalSource.assign(cornerValues, NAN);
    value.input.oldPsi.assign(cornerValues, NAN);
    for (uint32_t corner = 0; corner < value.descriptor.cornerCount;
         ++corner) {
        for (uint32_t group = value.descriptor.firstGroup;
             group < value.descriptor.firstGroup +
                 value.descriptor.groupCount; ++group) {
            value.input.totalSource[valueIndex(value, corner, group)] =
                0.75 + 0.5 * corner + 0.125 * group;
            value.input.oldPsi[valueIndex(value, corner, group)] =
                -0.25 + 0.125 * corner - 0.0625 * group;
        }
    }
    value.input.totalCrossSection.assign(value.descriptor.totalGroups, NAN);
    value.input.totalCrossSection[1] = 1.5;
    value.input.totalCrossSection[2] = 2.0;

    value.input.psi1.assign(
        value.descriptor.fluxPointCount * value.descriptor.totalGroups,
        NAN);
    for (uint32_t group = value.descriptor.firstGroup;
         group < value.descriptor.firstGroup + value.descriptor.groupCount;
         ++group) {
        value.input.psi1[valueIndex(value, 0, group)] =
            99.0 + group;
        value.input.psi1[valueIndex(value, 2, group)] =
            0.5 + 0.125 * group;
        value.input.psi1[valueIndex(value, 4, group)] =
            1.0 + 0.25 * group;
        value.input.psi1[valueIndex(value, 6, group)] =
            2.0 - 0.125 * group;
        value.input.psi1[valueIndex(value, 7, group)] =
            -0.5 + 0.0625 * group;
    }
    return value;
}

Fixture
twoZoneFixture()
{
    auto value = fixture();
    const auto originalCorners = value.input.corners;
    const auto originalFaces = value.input.faces;
    const auto originalSource = value.input.totalSource;
    const auto originalOldPsi = value.input.oldPsi;
    const auto originalSigma = value.input.totalCrossSection;
    const auto originalPsi1 = value.input.psi1;
    value.descriptor.cornerCount = 8;
    value.descriptor.zoneCount = 2;
    value.descriptor.fluxPointCount = 16;
    value.descriptor.selectedCornerCount = 2;
    value.input.cornerOrder = {0, 4};
    for (auto &face : value.input.faces) {
        face.fluxPoint += 4;
    }
    for (const auto &original : originalCorners) {
        auto corner = original;
        corner.zone = 1;
        corner.faceOffset += originalFaces.size();
        value.input.corners.push_back(corner);
    }
    for (const auto &original : originalFaces) {
        auto face = original;
        face.fluxPoint += 8;
        face.ezCorner += 4;
        value.input.faces.push_back(face);
    }
    value.input.totalSource.insert(value.input.totalSource.end(),
                                   originalSource.begin(),
                                   originalSource.end());
    value.input.oldPsi.insert(value.input.oldPsi.end(),
                              originalOldPsi.begin(), originalOldPsi.end());
    value.input.totalCrossSection.insert(
        value.input.totalCrossSection.end(), originalSigma.begin(),
        originalSigma.end());
    value.input.psi1.assign(
        value.descriptor.fluxPointCount * value.descriptor.totalGroups,
        NAN);
    for (uint32_t point = 0; point < 4; ++point) {
        for (uint32_t group = 0; group < value.descriptor.totalGroups;
             ++group) {
            value.input.psi1[valueIndex(value, point, group)] =
                originalPsi1[point * value.descriptor.totalGroups + group];
            value.input.psi1[valueIndex(value, point + 4, group)] =
                originalPsi1[point * value.descriptor.totalGroups + group];
            value.input.psi1[valueIndex(value, point + 8, group)] =
                originalPsi1[(point + 4) *
                              value.descriptor.totalGroups + group];
            value.input.psi1[valueIndex(value, point + 12, group)] =
                originalPsi1[(point + 4) *
                              value.descriptor.totalGroups + group];
        }
    }
    return value;
}

double
source(const Fixture &value, uint32_t corner, uint32_t group)
{
    const size_t index = valueIndex(value, corner, group);
    return value.input.totalSource[index] +
        value.descriptor.tau * value.input.oldPsi[index];
}

double
expected(const Fixture &value, uint32_t group)
{
    const auto &corner = value.input.corners[0];
    const double sigma = value.input.totalCrossSection[group];
    const double q0 = source(value, 0, group);
    double ss = corner.volume * q0;
    ss -= value.input.faces[0].fpNorm *
        value.input.psi1[valueIndex(value, 4, group)];
    ss -= value.input.faces[2].fpNorm *
        value.input.psi1[valueIndex(value, 6, group)];

    for (uint32_t localFace = 0; localFace < corner.faceCount;
         ++localFace) {
        const auto &face = value.input.faces[localFace];
        const bool outgoing = face.ezNorm > 0.0;
        const uint32_t firstCorner = outgoing ? 0 : face.ezCorner;
        const auto &first = value.input.corners[firstCorner];
        const double aez = outgoing ? face.ezNorm : -face.ezNorm;
        const double multiplier = outgoing ? 1.0 : -1.0;
        uint32_t oppositeStart = (localFace + 1) % corner.faceCount;
        if (!outgoing) {
            ss -= face.ezNorm *
                value.input.psi1[valueIndex(value, firstCorner, group)];
            for (uint32_t reverse = 0; reverse < first.faceCount;
                 ++reverse) {
                if (value.input.faces[first.faceOffset + reverse].ezCorner ==
                        0) {
                    oppositeStart = (reverse + 1) % first.faceCount;
                }
            }
        }

        const double qq = outgoing ? q0 :
            source(value, firstCorner, group);
        const double qez = outgoing ?
            source(value, face.ezCorner, group) : q0;
        double areaOpposite = 0.0;
        double psiOpposite = 0.0;
        for (uint32_t candidate = 0;
             candidate < first.faceCount - 2; ++candidate) {
            const auto &opposite = value.input.faces[
                first.faceOffset +
                (oppositeStart + candidate) % first.faceCount];
            if (opposite.fpNorm < 0.0) {
                areaOpposite -= opposite.fpNorm;
                psiOpposite -= opposite.fpNorm * value.input.psi1[
                    valueIndex(value, opposite.fluxPoint, group)];
            }
        }

        double sez = 0.0;
        if (areaOpposite > 0.0) {
            psiOpposite /= areaOpposite;
            const double sigv = sigma * first.volume;
            const double sigv2 = sigv * sigv;
            const double aez2 = aez * aez;
            const double gnum = aez2 *
                (1.82 * sigv2 + aez * (4.0 * sigv + 3.0 * aez));
            const double gden = first.volume *
                (4.0 * sigv * sigv2 +
                 aez * (6.0 * sigv2 +
                        2.0 * aez * (2.0 * sigv + aez)));
            sez = (first.volume * gnum *
                   (sigma * psiOpposite - qq) +
                   0.5 * aez * gden * (qq - qez)) /
                (gnum + gden * sigma);
        } else {
            sez = 0.5 * aez * (qq - qez) / sigma;
        }
        ss += multiplier * sez;
    }
    return ss / (corner.normSum + sigma * corner.volume);
}

bool
sameBits(const std::vector<double> &first, const std::vector<double> &second)
{
    return first.size() == second.size() &&
        std::memcmp(first.data(), second.data(),
                    first.size() * sizeof(double)) == 0;
}

void
assertOnlySelectedChanged(const Fixture &value,
                          const UmtCornerSweepResult &result)
{
    for (uint32_t point = 0; point < value.descriptor.fluxPointCount;
         ++point) {
        for (uint32_t group = 0; group < value.descriptor.totalGroups;
             ++group) {
            if (point == 0 && group >= value.descriptor.firstGroup &&
                group < value.descriptor.firstGroup +
                    value.descriptor.groupCount) {
                continue;
            }
            const size_t index = valueIndex(value, point, group);
            assert(std::memcmp(&result.psi1[index], &value.input.psi1[index],
                               sizeof(double)) == 0);
        }
    }
}

} // anonymous namespace

int
main()
{
    {
        const auto value = fixture();
        const auto result = UmtCornerSweepModel::execute(
            value.descriptor, value.input);
        assert(result);
        assert(result.psi1[valueIndex(value, 0, 1)] == expected(value, 1));
        assert(result.psi1[valueIndex(value, 0, 2)] == expected(value, 2));
        assertOnlySelectedChanged(value, result);
        assert(result.counters.cornersValidated == 4);
        assert(result.counters.cornersCommitted == 1);
        assert(result.counters.cornerGroupsProcessed == 2);
        assert(result.counters.geometryFaceVisits == 3);
        assert(result.counters.externalFaceVisits == 6);
        assert(result.counters.interiorFaceVisits == 6);
        assert(result.counters.reverseFaceSearchVisits == 6);
        assert(result.counters.oppositeFaceVisits == 6);
        assert(result.counters.sourceReads == 16);
        assert(result.counters.crossSectionReads == 2);
        assert(result.counters.fluxReads == 10);
        assert(result.counters.uniqueFluxCacheLines == 3);
        assert(result.counters.specialOppositeFaceUpdates == 4);
        assert(result.counters.fallbackFaceUpdates == 2);
        assert(result.counters.operationWindowFills == 1);
        assert(result.counters.outputWrites == 2);
    }
    {
        const auto value = twoZoneFixture();
        const auto result = UmtCornerSweepModel::execute(
            value.descriptor, value.input);
        assert(result);
        assert(result.psi1[valueIndex(value, 0, 1)] ==
               result.psi1[valueIndex(value, 4, 1)]);
        assert(result.psi1[valueIndex(value, 0, 2)] ==
               result.psi1[valueIndex(value, 4, 2)]);
        assert(result.counters.cornersValidated == 8);
        assert(result.counters.cornersCommitted == 2);
        assert(result.counters.cornerGroupsProcessed == 4);
        assert(result.counters.outputWrites == 4);
    }
    {
        auto value = fixture();
        value.input.faces[6].ezCorner = 1;
        const auto result = UmtCornerSweepModel::execute(
            value.descriptor, value.input);
        assert(result.error == UmtCornerSweepError::BadReverseFace);
        assert(sameBits(result.psi1, value.input.psi1));
        assert(result.counters.outputWrites == 0);
    }
    {
        auto value = fixture();
        value.descriptor.selectedCornerCount = 2;
        value.descriptor.groupCount = 1;
        value.input.cornerOrder = {0, 1};
        const auto result = UmtCornerSweepModel::execute(
            value.descriptor, value.input);
        assert(result.error == UmtCornerSweepError::SelectedDependency);
        assert(sameBits(result.psi1, value.input.psi1));
    }
    {
        auto value = fixture();
        value.input.corners[0].faceCount = 2;
        auto result = UmtCornerSweepModel::execute(
            value.descriptor, value.input);
        assert(result.error == UmtCornerSweepError::BadFaceCount);
        assert(sameBits(result.psi1, value.input.psi1));

        value = fixture();
        value.input.faces[1].ezNorm = 0.0;
        result = UmtCornerSweepModel::execute(value.descriptor, value.input);
        assert(result.error ==
               UmtCornerSweepError::ZeroInteriorProjection);

        value = fixture();
        value.input.faces[1].fluxPoint = value.descriptor.fluxPointCount;
        result = UmtCornerSweepModel::execute(value.descriptor, value.input);
        assert(result.error == UmtCornerSweepError::BadFaceIndex);
    }
    {
        auto value = fixture();
        value.input.totalSource[valueIndex(value, 3, 2)] = NAN;
        auto result = UmtCornerSweepModel::execute(
            value.descriptor, value.input);
        assert(result.error == UmtCornerSweepError::NonfiniteInput);
        assert(sameBits(result.psi1, value.input.psi1));

        value = fixture();
        value.input.totalCrossSection[1] = 0.0;
        result = UmtCornerSweepModel::execute(value.descriptor, value.input);
        assert(result.error == UmtCornerSweepError::NonpositivePhysics);
        assert(sameBits(result.psi1, value.input.psi1));
    }
    {
        auto value = fixture();
        value.descriptor.tau = 0.0;
        value.input.totalSource[valueIndex(value, 0, 2)] =
            std::numeric_limits<double>::max();
        const auto result = UmtCornerSweepModel::execute(
            value.descriptor, value.input);
        assert(result.error == UmtCornerSweepError::NonfiniteResult);
        assert(sameBits(result.psi1, value.input.psi1));
        assert(result.counters.outputWrites == 0);
    }
    {
        auto value = fixture();
        value.descriptor.selectedCornerCount = 0;
        value.input.cornerOrder.clear();
        auto result = UmtCornerSweepModel::execute(
            value.descriptor, value.input);
        assert(result.error == UmtCornerSweepError::Empty);

        value = fixture();
        value.descriptor.selectedCornerCount =
            UmtSweepMaximumSelectedCorners + 1;
        result = UmtCornerSweepModel::execute(value.descriptor, value.input);
        assert(result.error == UmtCornerSweepError::TooManyCorners);

        value = fixture();
        value.descriptor.groupCount = UmtSweepMaximumGroups + 1;
        result = UmtCornerSweepModel::execute(value.descriptor, value.input);
        assert(result.error == UmtCornerSweepError::TooManyGroups);

        value = fixture();
        value.descriptor.selectedCornerCount = 3;
        value.descriptor.groupCount = UmtSweepMaximumGroups;
        result = UmtCornerSweepModel::execute(value.descriptor, value.input);
        assert(result.error == UmtCornerSweepError::TooManyCornerGroups);

        value = fixture();
        value.descriptor.firstGroup = value.descriptor.totalGroups;
        result = UmtCornerSweepModel::execute(value.descriptor, value.input);
        assert(result.error == UmtCornerSweepError::BadGroupRange);
    }
    {
        auto value = fixture();
        value.descriptor.selectedCornerCount = 2;
        value.descriptor.groupCount = 1;
        value.input.cornerOrder = {0, 0};
        auto result = UmtCornerSweepModel::execute(
            value.descriptor, value.input);
        assert(result.error == UmtCornerSweepError::DuplicateCorner);

        value = fixture();
        value.input.psi1.pop_back();
        result = UmtCornerSweepModel::execute(value.descriptor, value.input);
        assert(result.error == UmtCornerSweepError::SourceExtent);
    }
    return 0;
}
