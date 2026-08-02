#include <algorithm>
#include <array>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>

#include "mem/LANLMAA/UmtMixedCornerModel.hh"
#include "umt_corner_sweep_record.hh"

using namespace gem5::lanlmaa;

namespace
{

bool
sameBits(double first, double second)
{
    return std::memcmp(&first, &second, sizeof(first)) == 0;
}

UmtCornerSweepRecord
readRecord(const char *path)
{
    std::ifstream input(path);
    assert(input);
    auto parsed = parseUmtCornerSweepRecord(input);
    assert(parsed);
    return std::move(parsed.record);
}

struct Coverage
{
    std::array<bool, 8> directionMasks{};
    std::array<bool, 4> incomingFaceCounts{};
    uint32_t records = 0;
    uint32_t groups = 0;
    uint32_t bitExactFullModelGroups = 0;
    uint32_t bitExactNativeGroups = 0;
    uint64_t packedInputBytes = 0;
    uint64_t carrierInputBytes = 0;
    uint32_t derivedBranchGroups = 0;
    double maximumNativeAbsoluteError = 0.0;
    double maximumNativeRelativeError = 0.0;
};

uint32_t
compareCompactToFull(const UmtCornerSweepRecord &native)
{
    const uint32_t current = native.input.cornerOrder.front();
    const auto plan = buildUmtMixedCornerPlan(
        native.descriptor, native.input, current);
    assert(plan);
    const auto full = UmtCornerSweepModel::execute(
        native.descriptor, native.input);
    assert(full);
    for (uint32_t group = 0;
         group < native.descriptor.groupCount; ++group) {
        const auto compact = buildUmtMixedCornerRecord(
            native.descriptor, native.input, plan.plan, group);
        assert(compact);
        const auto result = executeUmtMixedCorner(
            plan.plan.geometry, compact.record);
        assert(result);
        const size_t outputIndex =
            static_cast<size_t>(current) *
            native.descriptor.totalGroups + group;
        assert(sameBits(result.value, full.psi1[outputIndex]));
    }
    return native.descriptor.groupCount;
}

void
testNativeRecord(const char *path, Coverage &coverage)
{
    const auto native = readRecord(path);
    assert(native.input.cornerOrder.size() == 1);
    const uint32_t current = native.input.cornerOrder.front();
    const auto plan = buildUmtMixedCornerPlan(
        native.descriptor, native.input, current);
    assert(plan);

    uint32_t mask = 0;
    uint32_t incomingFaces = 0;
    for (uint32_t face = 0; face < UmtMixedCornerFaceCount; ++face) {
        if (plan.plan.geometry.signedEzNorm[face] < 0.0) {
            mask |= 1U << face;
            ++incomingFaces;
        }
    }
    coverage.directionMasks[mask] = true;
    coverage.incomingFaceCounts[incomingFaces] = true;

    const auto full = UmtCornerSweepModel::execute(
        native.descriptor, native.input);
    assert(full);
    for (uint32_t group = 0;
         group < native.descriptor.groupCount; ++group) {
        const auto compact = buildUmtMixedCornerRecord(
            native.descriptor, native.input, plan.plan, group);
        assert(compact);
        const auto result = executeUmtMixedCorner(
            plan.plan.geometry, compact.record);
        assert(result);

        const size_t outputIndex =
            static_cast<size_t>(current) *
            native.descriptor.totalGroups + group;
        assert(sameBits(result.value, full.psi1[outputIndex]));
        ++coverage.bitExactFullModelGroups;

        const double expected = native.nativeExpected[group];
        const double absoluteError = std::abs(result.value - expected);
        const double scale = std::max(std::abs(result.value),
                                      std::abs(expected));
        const double relativeError = scale == 0.0 ? 0.0 :
            absoluteError / scale;
        assert(absoluteError <= 1.0e-12 + 1.0e-12 * scale);
        if (sameBits(result.value, expected)) {
            ++coverage.bitExactNativeGroups;
        }
        coverage.maximumNativeAbsoluteError = std::max(
            coverage.maximumNativeAbsoluteError, absoluteError);
        coverage.maximumNativeRelativeError = std::max(
            coverage.maximumNativeRelativeError, relativeError);
        ++coverage.groups;
        coverage.packedInputBytes +=
            umtMixedCornerPackedRecordBytes(plan.plan.geometry);
        coverage.carrierInputBytes += UmtMixedCornerRecordBytes;
    }
    ++coverage.records;
}

void
testMalformedTopology(const char *path)
{
    auto native = readRecord(path);
    const uint32_t current = native.input.cornerOrder.front();
    const auto &currentCorner = native.input.corners[current];
    uint32_t incomingLocalFace = UmtMixedCornerFaceCount;
    for (uint32_t face = 0; face < UmtMixedCornerFaceCount; ++face) {
        if (native.input.faces[currentCorner.faceOffset + face].ezNorm < 0.0) {
            incomingLocalFace = face;
            break;
        }
    }
    assert(incomingLocalFace < UmtMixedCornerFaceCount);
    const auto &incomingFace =
        native.input.faces[currentCorner.faceOffset + incomingLocalFace];
    const uint32_t upstreamIndex = incomingFace.ezCorner;
    const auto &upstream = native.input.corners[upstreamIndex];

    uint32_t reverseLocalFace = UmtMixedCornerFaceCount;
    uint32_t otherLocalFace = UmtMixedCornerFaceCount;
    for (uint32_t face = 0; face < UmtMixedCornerFaceCount; ++face) {
        const auto &candidate =
            native.input.faces[upstream.faceOffset + face];
        if (candidate.ezCorner == current) {
            reverseLocalFace = face;
        } else {
            otherLocalFace = face;
        }
    }
    assert(reverseLocalFace < UmtMixedCornerFaceCount);
    assert(otherLocalFace < UmtMixedCornerFaceCount);

    auto missing = native.input;
    missing.faces[upstream.faceOffset + reverseLocalFace].ezCorner =
        (current + 1) % native.descriptor.cornerCount;
    assert(buildUmtMixedCornerPlan(
               native.descriptor, missing, current).error ==
           UmtMixedCornerError::BadReverseFace);

    auto duplicate = native.input;
    duplicate.faces[upstream.faceOffset + otherLocalFace].ezCorner = current;
    assert(buildUmtMixedCornerPlan(
               native.descriptor, duplicate, current).error ==
           UmtMixedCornerError::BadReverseFace);

    auto zeroDirection = native.input;
    zeroDirection.faces[currentCorner.faceOffset].ezNorm = 0.0;
    assert(buildUmtMixedCornerPlan(
               native.descriptor, zeroDirection, current).error ==
           UmtMixedCornerError::InvalidGeometry);

    auto unsupported = native.input;
    unsupported.corners[current].faceCount = 4;
    assert(buildUmtMixedCornerPlan(
               native.descriptor, unsupported, current).error ==
           UmtMixedCornerError::UnsupportedFaceCount);
}

void
testMalformedCompactRecord(const char *path)
{
    const auto native = readRecord(path);
    const uint32_t current = native.input.cornerOrder.front();
    const auto plan = buildUmtMixedCornerPlan(
        native.descriptor, native.input, current);
    assert(plan);
    const auto compact = buildUmtMixedCornerRecord(
        native.descriptor, native.input, plan.plan, 0);
    assert(compact);

    auto record = compact.record;
    record.totalSource = std::numeric_limits<double>::infinity();
    assert(executeUmtMixedCorner(plan.plan.geometry, record).error ==
           UmtMixedCornerError::NonfiniteInput);

    record = compact.record;
    size_t outgoing = UmtMixedCornerFaceCount;
    for (size_t face = 0; face < UmtMixedCornerFaceCount; ++face) {
        if (plan.plan.geometry.signedEzNorm[face] > 0.0) {
            outgoing = face;
            break;
        }
    }
    assert(outgoing < UmtMixedCornerFaceCount);
    record.upstreamCornerFlux[outgoing] = 1.0;
    assert(executeUmtMixedCorner(plan.plan.geometry, record).error ==
           UmtMixedCornerError::NoncanonicalRecord);

    auto geometry = plan.plan.geometry;
    geometry.oppositeActive[0] = 0;
    record = compact.record;
    record.oppositeFlux[0] = 1.0;
    assert(executeUmtMixedCorner(geometry, record).error ==
           UmtMixedCornerError::NoncanonicalRecord);

    geometry = plan.plan.geometry;
    geometry.firstVolume[0] = 0.0;
    assert(executeUmtMixedCorner(geometry, compact.record).error ==
           UmtMixedCornerError::InvalidGeometry);
}

uint32_t
testDerivedBranches(const char *allOutgoingPath, const char *incomingPath)
{
    auto fallback = readRecord(allOutgoingPath);
    const uint32_t fallbackCurrent = fallback.input.cornerOrder.front();
    const auto &fallbackCorner = fallback.input.corners[fallbackCurrent];
    fallback.input.faces[fallbackCorner.faceOffset + 1].fpNorm =
        std::abs(fallback.input.faces[fallbackCorner.faceOffset + 1].fpNorm);
    uint32_t groups = compareCompactToFull(fallback);

    auto unequalVolume = readRecord(incomingPath);
    const uint32_t current = unequalVolume.input.cornerOrder.front();
    const auto &currentCorner = unequalVolume.input.corners[current];
    uint32_t upstream = unequalVolume.descriptor.cornerCount;
    for (uint32_t face = 0; face < UmtMixedCornerFaceCount; ++face) {
        const auto &candidate =
            unequalVolume.input.faces[currentCorner.faceOffset + face];
        if (candidate.ezNorm < 0.0) {
            upstream = candidate.ezCorner;
            break;
        }
    }
    assert(upstream < unequalVolume.descriptor.cornerCount);
    unequalVolume.input.corners[upstream].volume *= 1.25;
    assert(!sameBits(unequalVolume.input.corners[upstream].volume,
                     currentCorner.volume));
    groups += compareCompactToFull(unequalVolume);
    return groups;
}

} // anonymous namespace

int
main(int argc, char **argv)
{
    assert(argc == 17);
    static_assert(UmtMixedCornerRecordBytes == 144);
    static_assert(UmtMixedCornerRetainedBits == 896);
    static_assert(UmtMixedCornerRetainedDeltaBits == 384);
    static_assert(UmtMixedCornerMaximumRetainedDeltaBits == 24576);
    assert(UmtMixedCornerBaseRecordFp64Words == 12);

    Coverage coverage;
    for (int index = 1; index < argc; ++index) {
        testNativeRecord(argv[index], coverage);
    }
    for (bool covered : coverage.incomingFaceCounts) {
        assert(covered);
    }
    const uint32_t directionMaskCount = std::count(
        coverage.directionMasks.begin(), coverage.directionMasks.end(), true);
    assert(directionMaskCount == 6);
    testMalformedTopology(argv[2]);
    testMalformedCompactRecord(argv[2]);
    coverage.derivedBranchGroups = testDerivedBranches(argv[1], argv[2]);

    std::cout << std::setprecision(17)
              << "{\"status\":\"PASS\",\"records\":"
              << coverage.records
              << ",\"groups\":" << coverage.groups
              << ",\"direction_masks\":" << directionMaskCount
              << ",\"incoming_face_counts\":4"
              << ",\"bit_exact_full_model_groups\":"
              << coverage.bitExactFullModelGroups
              << ",\"bit_exact_native_groups\":"
              << coverage.bitExactNativeGroups
              << ",\"maximum_native_absolute_error\":"
              << coverage.maximumNativeAbsoluteError
              << ",\"maximum_native_relative_error\":"
              << coverage.maximumNativeRelativeError
              << ",\"carrier_record_bytes\":"
              << UmtMixedCornerRecordBytes
              << ",\"packed_input_bytes\":"
              << coverage.packedInputBytes
              << ",\"carrier_input_bytes\":"
              << coverage.carrierInputBytes
              << ",\"derived_branch_groups\":"
              << coverage.derivedBranchGroups
              << ",\"retained_bits\":" << UmtMixedCornerRetainedBits
              << ",\"retained_delta_bits_per_context\":"
              << UmtMixedCornerRetainedDeltaBits
              << ",\"retained_delta_bits_64_contexts\":"
              << UmtMixedCornerMaximumRetainedDeltaBits << "}\n";
    return 0;
}
