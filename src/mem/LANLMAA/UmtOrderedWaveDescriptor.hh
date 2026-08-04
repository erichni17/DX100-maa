#ifndef __MEM_LANLMAA_UMT_ORDERED_WAVE_DESCRIPTOR_HH__
#define __MEM_LANLMAA_UMT_ORDERED_WAVE_DESCRIPTOR_HH__

#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>

#include "mem/LANLMAA/UmtFp64DependencyModel.hh"
#include "mem/LANLMAA/UmtFusedCornerDescriptor.hh"

namespace gem5
{
namespace lanlmaa
{

constexpr size_t UmtOrderedWaveDescriptorBytes = 256;
constexpr uint16_t UmtOrderedWaveDescriptorVersion = 4;
constexpr uint8_t UmtOrderedWaveOpcode = 11;
constexpr uint8_t UmtOrderedWaveEightCornerFlag = 1U << 0;
constexpr uint8_t UmtOrderedWaveStructureOfArraysFlag = 1U << 1;
constexpr uint8_t UmtOrderedWaveFlags =
    UmtOrderedWaveEightCornerFlag | UmtOrderedWaveStructureOfArraysFlag;
constexpr uint32_t UmtOrderedWaveCorners = 8;
constexpr uint32_t UmtOrderedWaveDenseCoefficients = 28;
constexpr uint32_t UmtOrderedWaveMaximumEdges = 12;
constexpr uint32_t UmtOrderedWaveRecordFp64Words = 16;
constexpr uint32_t UmtOrderedWaveRecordBytes =
    UmtOrderedWaveRecordFp64Words * sizeof(uint64_t);
constexpr uint32_t UmtOrderedWaveResultBytes =
    UmtOrderedWaveCorners * sizeof(uint64_t);
constexpr uint32_t UmtOrderedWaveMaximumGroups = 32;
constexpr uint32_t UmtOrderedWavePlaneStride =
    UmtOrderedWaveMaximumGroups * sizeof(uint64_t);
constexpr uint64_t UmtOrderedWaveAbiFingerprint =
    0x9bafe2c1186d4075ULL;
constexpr size_t UmtOrderedWaveSumAreaOffset = 168;

struct UmtOrderedWaveDescriptor
{
    uint32_t groupCount = 0;
    uint32_t recordStride = 0;
    uint64_t recordBase = 0;
    uint64_t resultBase = 0;
    uint64_t completionRecord = 0;
    std::array<double, UmtOrderedWaveDenseCoefficients> coefficients{};
    std::array<double, UmtOrderedWaveCorners> sumArea{};
};

struct UmtOrderedWaveRecord
{
    std::array<double, UmtOrderedWaveCorners> source{};
    std::array<double, UmtOrderedWaveCorners> sigtVolume{};
};

struct UmtOrderedWaveResult
{
    std::array<double, UmtOrderedWaveCorners> flux{};
    DescriptorError error = DescriptorError::None;

    explicit operator bool() const { return error == DescriptorError::None; }
};

struct UmtOrderedWaveDescriptorDecodeResult
{
    UmtOrderedWaveDescriptor descriptor;
    DescriptorError error = DescriptorError::None;

    explicit operator bool() const { return error == DescriptorError::None; }
};

inline size_t
umtOrderedWaveCoefficientIndex(size_t source, size_t destination)
{
    return source * (2 * UmtOrderedWaveCorners - source - 1) / 2 +
        destination - source - 1;
}

inline size_t
umtOrderedWaveWordsToLineBoundary(
    uint64_t address, size_t remainingWords, size_t lineBytes)
{
    if (lineBytes == 0 || address % sizeof(uint64_t) != 0 ||
        lineBytes % sizeof(uint64_t) != 0) {
        return 0;
    }
    const size_t bytesToBoundary =
        lineBytes - static_cast<size_t>(address % lineBytes);
    return std::min(
        remainingWords, bytesToBoundary / sizeof(uint64_t));
}

inline UmtOrderedWaveResult
executeUmtOrderedWave(const UmtOrderedWaveDescriptor &descriptor,
                      UmtOrderedWaveRecord record)
{
    UmtOrderedWaveResult result;
    for (size_t corner = 0; corner < UmtOrderedWaveCorners; ++corner) {
        if (!std::isfinite(record.source[corner]) ||
            !std::isfinite(descriptor.sumArea[corner]) ||
            !std::isfinite(record.sigtVolume[corner])) {
            result.error = DescriptorError::BadRecordValue;
            return result;
        }
        const double denominator =
            descriptor.sumArea[corner] + record.sigtVolume[corner];
        if (!std::isfinite(denominator) || denominator <= 0.0) {
            result.error = DescriptorError::BadRecordValue;
            return result;
        }
        const double flux = record.source[corner] / denominator;
        if (!std::isfinite(flux)) {
            result.error = DescriptorError::BadRecordValue;
            return result;
        }
        result.flux[corner] = flux;
        for (size_t destination = corner + 1;
             destination < UmtOrderedWaveCorners; ++destination) {
            const double coefficient = descriptor.coefficients[
                umtOrderedWaveCoefficientIndex(corner, destination)];
            if (coefficient == 0.0)
                continue;
            record.source[destination] += coefficient * flux;
            if (!std::isfinite(record.source[destination])) {
                result.error = DescriptorError::BadRecordValue;
                return result;
            }
        }
    }
    return result;
}

inline UmtFp64DependencyDag
umtOrderedWaveDependencyDag(const UmtOrderedWaveDescriptor &descriptor)
{
    UmtFp64DependencyDag dag;
    std::array<int32_t, UmtOrderedWaveCorners> sourceReady;
    sourceReady.fill(-1);
    uint32_t last = 0;
    for (size_t corner = 0; corner < UmtOrderedWaveCorners; ++corner) {
        const uint32_t denominator = dag.nodes.size();
        dag.nodes.push_back({UmtFp64OperationKind::AddSub, {}});
        std::vector<uint32_t> dependencies{denominator};
        if (sourceReady[corner] >= 0)
            dependencies.push_back(sourceReady[corner]);
        const uint32_t divide = dag.nodes.size();
        dag.nodes.push_back({UmtFp64OperationKind::Divide, dependencies});
        last = divide;
        for (size_t destination = corner + 1;
             destination < UmtOrderedWaveCorners; ++destination) {
            if (descriptor.coefficients[
                    umtOrderedWaveCoefficientIndex(corner, destination)] ==
                0.0)
                continue;
            const uint32_t multiply = dag.nodes.size();
            dag.nodes.push_back({UmtFp64OperationKind::Multiply, {divide}});
            dependencies = {multiply};
            if (sourceReady[destination] >= 0)
                dependencies.push_back(sourceReady[destination]);
            const uint32_t add = dag.nodes.size();
            dag.nodes.push_back({UmtFp64OperationKind::AddSub, dependencies});
            sourceReady[destination] = add;
            last = add;
        }
    }
    dag.output = last;
    return dag;
}

inline UmtFp64Resources
umtOrderedWaveResources()
{
    UmtFp64Resources resources;
    resources.globalIssueWidth = 1;
    resources.addSubUnits = 1;
    resources.multiplyUnits = 1;
    resources.divideUnits = 8;
    resources.divideLatency = 64;
    resources.divideInitiationInterval = 64;
    return resources;
}

inline UmtFp64ScheduleResult
umtOrderedWaveSchedule(const UmtOrderedWaveDescriptor &descriptor)
{
    return UmtFp64DependencyModel::schedule(
        umtOrderedWaveDependencyDag(descriptor), descriptor.groupCount,
        umtOrderedWaveResources());
}

inline UmtOrderedWaveDescriptorDecodeResult
decodeUmtOrderedWaveDescriptor(
    const std::array<uint8_t, UmtOrderedWaveDescriptorBytes> &bytes)
{
    UmtOrderedWaveDescriptorDecodeResult result;
    if (descriptorReadLe32(bytes.data()) != DescriptorMagic) {
        result.error = DescriptorError::BadMagic;
        return result;
    }
    if (descriptorReadLe16(bytes.data() + 4) !=
        UmtOrderedWaveDescriptorVersion) {
        result.error = DescriptorError::BadVersion;
        return result;
    }
    if (bytes[6] != UmtOrderedWaveOpcode) {
        result.error = DescriptorError::BadOpcode;
        return result;
    }
    if (bytes[7] != UmtOrderedWaveFlags) {
        result.error = DescriptorError::UnsupportedFlags;
        return result;
    }
    auto &descriptor = result.descriptor;
    descriptor.groupCount = descriptorReadLe32(bytes.data() + 8);
    descriptor.recordStride = descriptorReadLe32(bytes.data() + 12);
    descriptor.recordBase = descriptorReadLe64(bytes.data() + 16);
    descriptor.resultBase = descriptorReadLe64(bytes.data() + 24);
    descriptor.completionRecord = descriptorReadLe64(bytes.data() + 32);
    if (descriptor.groupCount == 0) {
        result.error = DescriptorError::Empty;
        return result;
    }
    if (descriptor.groupCount > UmtOrderedWaveMaximumGroups) {
        result.error = DescriptorError::TooManyItems;
        return result;
    }
    if (descriptor.recordStride != UmtOrderedWavePlaneStride) {
        result.error = DescriptorError::BadRecordGeometry;
        return result;
    }
    if (descriptorReadLe64(bytes.data() + 40) != 0 ||
        descriptorReadLe64(bytes.data() + 48) !=
            UmtOrderedWaveAbiFingerprint ||
        descriptorReadLe32(bytes.data() + 56) != UmtOrderedWaveCorners) {
        result.error = DescriptorError::BadRecordValue;
        return result;
    }
    const uint32_t edgeCount = descriptorReadLe32(bytes.data() + 60);
    const uint32_t edgeMask = descriptorReadLe32(bytes.data() + 64);
    if (edgeCount > UmtOrderedWaveMaximumEdges ||
        descriptorReadLe32(bytes.data() + 68) != 0 ||
        (edgeMask >> UmtOrderedWaveDenseCoefficients) != 0) {
        result.error = DescriptorError::BadRecordValue;
        return result;
    }
    uint32_t maskEdges = 0;
    for (uint32_t mask = edgeMask; mask != 0; mask >>= 1)
        maskEdges += mask & 1U;
    if (maskEdges != edgeCount) {
        result.error = DescriptorError::BadRecordValue;
        return result;
    }
    uint32_t sparseIndex = 0;
    for (size_t denseIndex = 0;
         denseIndex < descriptor.coefficients.size(); ++denseIndex) {
        if ((edgeMask & (uint32_t{1} << denseIndex)) == 0)
            continue;
        const double coefficient = umtFusedCornerDecodeFp64(
            bytes.data() + 72 + sparseIndex * sizeof(uint64_t));
        if (!std::isfinite(coefficient) || coefficient == 0.0) {
            result.error = DescriptorError::BadRecordValue;
            return result;
        }
        descriptor.coefficients[denseIndex] = coefficient;
        ++sparseIndex;
    }
    for (size_t offset = 72 + edgeCount * sizeof(uint64_t);
         offset < UmtOrderedWaveSumAreaOffset; ++offset) {
        if (bytes[offset] != 0) {
            result.error = DescriptorError::ReservedNonzero;
            return result;
        }
    }
    for (size_t corner = 0; corner < descriptor.sumArea.size(); ++corner) {
        descriptor.sumArea[corner] = umtFusedCornerDecodeFp64(
            bytes.data() + UmtOrderedWaveSumAreaOffset +
            corner * sizeof(uint64_t));
        if (!std::isfinite(descriptor.sumArea[corner])) {
            result.error = DescriptorError::BadRecordValue;
            return result;
        }
    }
    for (size_t offset = UmtOrderedWaveSumAreaOffset +
             UmtOrderedWaveCorners * sizeof(uint64_t);
         offset < bytes.size(); ++offset) {
        if (bytes[offset] != 0) {
            result.error = DescriptorError::ReservedNonzero;
            return result;
        }
    }
    if (descriptor.recordBase % alignof(uint64_t) != 0 ||
        descriptor.resultBase % alignof(uint64_t) != 0 ||
        descriptor.completionRecord % alignof(uint64_t) != 0) {
        result.error = DescriptorError::MisalignedVector;
        return result;
    }
    std::array<UmtFusedCornerRange, 3> ranges;
    if (!umtFusedCornerScaledRange(
            descriptor.recordBase, UmtOrderedWaveRecordFp64Words,
            descriptor.recordStride,
            descriptor.groupCount * sizeof(uint64_t), ranges[0]) ||
        !umtFusedCornerScaledRange(
            descriptor.resultBase, UmtOrderedWaveCorners,
            descriptor.recordStride,
            descriptor.groupCount * sizeof(uint64_t), ranges[1]) ||
        !umtFusedCornerScaledRange(
            descriptor.completionRecord, 1, 32, 32, ranges[2])) {
        result.error = DescriptorError::RangeOverflow;
        return result;
    }
    if (descriptorRangesOverlap(
            ranges[0].begin, ranges[0].end,
            ranges[1].begin, ranges[1].end) ||
        descriptorRangesOverlap(
            ranges[0].begin, ranges[0].end,
            ranges[2].begin, ranges[2].end)) {
        result.error = DescriptorError::OverlappingInput;
        return result;
    }
    if (descriptorRangesOverlap(
            ranges[1].begin, ranges[1].end,
            ranges[2].begin, ranges[2].end)) {
        result.error = DescriptorError::OverlappingOutput;
        return result;
    }
    if (!umtOrderedWaveSchedule(descriptor))
        result.error = DescriptorError::BadRecordGeometry;
    return result;
}

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_UMT_ORDERED_WAVE_DESCRIPTOR_HH__
