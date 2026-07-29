#ifndef __MEM_LANLMAA_SPARTA_FUSED_CELL_MODEL_HH__
#define __MEM_LANLMAA_SPARTA_FUSED_CELL_MODEL_HH__

#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <vector>

#include "mem/LANLMAA/Descriptor.hh"

namespace gem5
{
namespace lanlmaa
{

constexpr size_t SpartaFusedDescriptorBytes = 128;
constexpr uint16_t SpartaFusedDescriptorVersion = 2;
constexpr uint8_t SpartaFusedOpcode = 7;
constexpr uint8_t SpartaFusedTallyExclusiveFlag = 1U << 0;
constexpr uint32_t SpartaFusedChannels = 6;
constexpr uint32_t SpartaFusedMaximumParticles = 64;
constexpr uint32_t SpartaFusedMaximumCells = 64;
constexpr uint32_t SpartaFusedActiveContexts = 8;
constexpr uint64_t SpartaFusedAbiFingerprint = 0xa34d454519758371ULL;
constexpr uint64_t SpartaFusedAddressLimit = uint64_t{1} << 48;
constexpr uint64_t SpartaFusedOnePartBytes = 104;
constexpr uint64_t SpartaFusedSpeciesBytes = 192;
constexpr uint64_t SpartaFusedChildInfoBytes = 64;

struct SpartaFusedDescriptor
{
    uint32_t cellCount = 0;
    uint32_t particleCount = 0;
    uint64_t childInfoBase = 0;
    uint64_t nextBase = 0;
    uint64_t particleBase = 0;
    uint64_t speciesBase = 0;
    uint64_t speciesToGroupBase = 0;
    uint64_t tallyBase = 0;
    uint64_t completionRecord = 0;
    uint64_t abiFingerprint = 0;
    uint32_t groupBit = 0;
    int32_t targetGroup = -1;
    uint32_t speciesCount = 0;
    uint32_t tallyCellStride = 0;
};

struct SpartaFusedDescriptorDecodeResult
{
    SpartaFusedDescriptor descriptor;
    DescriptorError error = DescriptorError::None;

    explicit operator bool() const
    {
        return error == DescriptorError::None;
    }
};

struct SpartaFusedRange
{
    uint64_t begin = 0;
    uint64_t end = 0;
};

inline bool
spartaFusedRange(uint64_t base, uint64_t bytes, SpartaFusedRange &range)
{
    if (base >= SpartaFusedAddressLimit ||
        bytes > SpartaFusedAddressLimit - base) {
        return false;
    }
    range = {base, base + bytes};
    return true;
}

inline bool
spartaFusedScaledRange(uint64_t base, uint64_t count, uint64_t stride,
                      uint64_t tailBytes, SpartaFusedRange &range)
{
    if (count == 0 || stride == 0 || tailBytes == 0) {
        return false;
    }
    if (count - 1 >
        (std::numeric_limits<uint64_t>::max() - tailBytes) / stride) {
        return false;
    }
    return spartaFusedRange(base, (count - 1) * stride + tailBytes, range);
}

inline SpartaFusedDescriptorDecodeResult
decodeSpartaFusedDescriptor(
    const std::array<uint8_t, SpartaFusedDescriptorBytes> &bytes)
{
    SpartaFusedDescriptorDecodeResult result;
    if (descriptorReadLe32(bytes.data()) != DescriptorMagic) {
        result.error = DescriptorError::BadMagic;
        return result;
    }
    if (descriptorReadLe16(bytes.data() + 4) !=
        SpartaFusedDescriptorVersion) {
        result.error = DescriptorError::BadVersion;
        return result;
    }
    if (bytes[6] != SpartaFusedOpcode) {
        result.error = DescriptorError::BadOpcode;
        return result;
    }
    if (bytes[7] != SpartaFusedTallyExclusiveFlag) {
        result.error = DescriptorError::UnsupportedFlags;
        return result;
    }

    auto &descriptor = result.descriptor;
    descriptor.cellCount = descriptorReadLe32(bytes.data() + 8);
    descriptor.particleCount = descriptorReadLe32(bytes.data() + 12);
    if (descriptor.cellCount == 0 || descriptor.particleCount == 0) {
        result.error = DescriptorError::Empty;
        return result;
    }
    if (descriptor.cellCount > SpartaFusedMaximumCells ||
        descriptor.particleCount > SpartaFusedMaximumParticles) {
        result.error = DescriptorError::TooManyItems;
        return result;
    }

    descriptor.childInfoBase = descriptorReadLe64(bytes.data() + 16);
    descriptor.nextBase = descriptorReadLe64(bytes.data() + 24);
    descriptor.particleBase = descriptorReadLe64(bytes.data() + 32);
    descriptor.speciesBase = descriptorReadLe64(bytes.data() + 40);
    descriptor.speciesToGroupBase = descriptorReadLe64(bytes.data() + 48);
    descriptor.tallyBase = descriptorReadLe64(bytes.data() + 56);
    descriptor.completionRecord = descriptorReadLe64(bytes.data() + 64);
    descriptor.abiFingerprint = descriptorReadLe64(bytes.data() + 72);
    descriptor.groupBit = descriptorReadLe32(bytes.data() + 80);
    descriptor.targetGroup = static_cast<int32_t>(
        descriptorReadLe32(bytes.data() + 84));
    descriptor.speciesCount = descriptorReadLe32(bytes.data() + 88);
    descriptor.tallyCellStride = descriptorReadLe32(bytes.data() + 92);
    for (size_t offset = 96; offset < bytes.size(); ++offset) {
        if (bytes[offset] != 0) {
            result.error = DescriptorError::ReservedNonzero;
            return result;
        }
    }
    if (descriptor.abiFingerprint != SpartaFusedAbiFingerprint) {
        result.error = DescriptorError::BadRecordValue;
        return result;
    }
    if (descriptor.groupBit == 0 || descriptor.targetGroup < 0 ||
        descriptor.speciesCount == 0 ||
        descriptor.tallyCellStride < SpartaFusedChannels * sizeof(uint64_t) ||
        descriptor.tallyCellStride % sizeof(uint64_t) != 0) {
        result.error = DescriptorError::BadRecordGeometry;
        return result;
    }
    if (descriptor.childInfoBase % alignof(uint64_t) != 0 ||
        descriptor.nextBase % alignof(uint32_t) != 0 ||
        descriptor.particleBase % alignof(uint64_t) != 0 ||
        descriptor.speciesBase % alignof(uint64_t) != 0 ||
        descriptor.speciesToGroupBase % alignof(uint32_t) != 0 ||
        descriptor.tallyBase % alignof(uint64_t) != 0 ||
        descriptor.completionRecord % alignof(uint64_t) != 0) {
        result.error = DescriptorError::MisalignedVector;
        return result;
    }

    std::array<SpartaFusedRange, 7> ranges;
    if (!spartaFusedScaledRange(
            descriptor.childInfoBase, descriptor.cellCount,
            SpartaFusedChildInfoBytes, 12, ranges[0]) ||
        !spartaFusedScaledRange(
            descriptor.nextBase, descriptor.particleCount,
            sizeof(uint32_t), sizeof(uint32_t), ranges[1]) ||
        !spartaFusedScaledRange(
            descriptor.particleBase, descriptor.particleCount,
            SpartaFusedOnePartBytes, 64, ranges[2]) ||
        !spartaFusedScaledRange(
            descriptor.speciesBase, descriptor.speciesCount,
            SpartaFusedSpeciesBytes, 32, ranges[3]) ||
        !spartaFusedScaledRange(
            descriptor.speciesToGroupBase, descriptor.speciesCount,
            sizeof(uint32_t), sizeof(uint32_t), ranges[4]) ||
        !spartaFusedScaledRange(
            descriptor.tallyBase, descriptor.cellCount,
            descriptor.tallyCellStride,
            SpartaFusedChannels * sizeof(uint64_t), ranges[5]) ||
        !spartaFusedRange(descriptor.completionRecord, 32, ranges[6])) {
        result.error = DescriptorError::RangeOverflow;
        return result;
    }
    for (size_t first = 0; first < ranges.size(); ++first) {
        for (size_t second = first + 1; second < ranges.size(); ++second) {
            if (descriptorRangesOverlap(
                    ranges[first].begin, ranges[first].end,
                    ranges[second].begin, ranges[second].end)) {
                result.error = DescriptorError::OverlappingInput;
                return result;
            }
        }
    }
    return result;
}

struct SpartaFusedCellInfo
{
    int32_t count = 0;
    int32_t first = -1;
    uint32_t mask = 0;
};

struct SpartaFusedParticle
{
    int32_t species = -1;
    int32_t cell = -1;
    std::array<double, 3> velocity{};
};

struct SpartaFusedSpecies
{
    double mass = 0.0;
};

struct SpartaFusedInput
{
    std::vector<SpartaFusedCellInfo> cells;
    std::vector<int32_t> next;
    std::vector<SpartaFusedParticle> particles;
    std::vector<SpartaFusedSpecies> species;
    std::vector<int32_t> speciesToGroup;
};

enum class SpartaFusedExecutionError : uint8_t
{
    None = 0,
    SourceExtent,
    TallyNotZero,
    BadEmptyCell,
    BadCellCount,
    BadParticleIndex,
    DuplicateParticle,
    BadParticleCell,
    BadTerminal,
    BadSpecies,
    NonfiniteInput,
    NonfiniteContribution,
    NonfiniteSummary,
    IncompleteCoverage
};

struct SpartaFusedCounters
{
    uint32_t cellsScanned = 0;
    uint32_t nonemptyCells = 0;
    uint32_t particleVisits = 0;
    uint32_t eligibleParticles = 0;
    uint32_t summariesUsed = 0;
    uint32_t activeContextHighWater = 0;
    uint32_t fp64Multiplies = 0;
    uint32_t fp64Adds = 0;
    uint32_t coherentWrites = 0;
    uint32_t writeAcknowledgements = 0;
};

using SpartaFusedTally = std::array<double, SpartaFusedChannels>;

struct SpartaFusedExecutionResult
{
    SpartaFusedExecutionError error = SpartaFusedExecutionError::None;
    SpartaFusedCounters counters;
    std::vector<SpartaFusedTally> tallies;

    explicit operator bool() const
    {
        return error == SpartaFusedExecutionError::None;
    }
};

class SpartaFusedCellModel
{
  private:
    struct Summary
    {
        bool valid = false;
        uint32_t eligible = 0;
        SpartaFusedTally sums{};
    };

    struct Context
    {
        bool valid = false;
        uint32_t cell = 0;
        int32_t particle = -1;
        uint32_t remaining = 0;
        uint32_t visited = 0;
    };

    static bool finite(double value) { return std::isfinite(value); }

  public:
    static SpartaFusedExecutionResult execute(
        const SpartaFusedDescriptor &descriptor,
        const SpartaFusedInput &input,
        const std::vector<SpartaFusedTally> &initialTallies)
    {
        SpartaFusedExecutionResult result;
        result.tallies = initialTallies;
        if (input.cells.size() < descriptor.cellCount ||
            input.next.size() < descriptor.particleCount ||
            input.particles.size() < descriptor.particleCount ||
            input.species.size() < descriptor.speciesCount ||
            input.speciesToGroup.size() < descriptor.speciesCount ||
            initialTallies.size() != descriptor.cellCount) {
            result.error = SpartaFusedExecutionError::SourceExtent;
            return result;
        }
        for (const auto &tally : initialTallies) {
            for (const double value : tally) {
                if (value != 0.0) {
                    result.error = SpartaFusedExecutionError::TallyNotZero;
                    return result;
                }
            }
        }

        std::array<Summary, SpartaFusedMaximumCells> summaries{};
        std::array<Context, SpartaFusedActiveContexts> contexts{};
        uint64_t visitedParticles = 0;
        uint32_t visitedCount = 0;
        uint32_t nextCell = 0;
        uint32_t active = 0;

        auto admit = [&]() -> SpartaFusedExecutionError {
            for (auto &context : contexts) {
                if (context.valid) {
                    continue;
                }
                while (nextCell < descriptor.cellCount) {
                    const uint32_t cell = nextCell++;
                    const auto &info = input.cells[cell];
                    result.counters.cellsScanned++;
                    if (info.count < 0 ||
                        static_cast<uint32_t>(info.count) >
                            descriptor.particleCount) {
                        return SpartaFusedExecutionError::BadCellCount;
                    }
                    if (info.count == 0) {
                        if (info.first != -1) {
                            return SpartaFusedExecutionError::BadEmptyCell;
                        }
                        continue;
                    }
                    if (info.first < 0 ||
                        static_cast<uint32_t>(info.first) >=
                            descriptor.particleCount) {
                        return SpartaFusedExecutionError::BadParticleIndex;
                    }
                    context.valid = true;
                    context.cell = cell;
                    context.particle = info.first;
                    context.remaining = info.count;
                    context.visited = 0;
                    summaries[cell].valid = true;
                    result.counters.nonemptyCells++;
                    result.counters.summariesUsed++;
                    active++;
                    if (active > result.counters.activeContextHighWater) {
                        result.counters.activeContextHighWater = active;
                    }
                    break;
                }
            }
            return SpartaFusedExecutionError::None;
        };

        auto admissionError = admit();
        if (admissionError != SpartaFusedExecutionError::None) {
            result.error = admissionError;
            return result;
        }
        while (active != 0) {
            for (auto &context : contexts) {
                if (!context.valid) {
                    continue;
                }
                if (context.particle < 0 ||
                    static_cast<uint32_t>(context.particle) >=
                        descriptor.particleCount) {
                    result.error = SpartaFusedExecutionError::BadParticleIndex;
                    return result;
                }
                const uint32_t particleIndex = context.particle;
                const uint64_t particleBit = uint64_t{1} << particleIndex;
                if (visitedParticles & particleBit) {
                    result.error =
                        SpartaFusedExecutionError::DuplicateParticle;
                    return result;
                }
                visitedParticles |= particleBit;
                visitedCount++;
                context.visited++;
                result.counters.particleVisits++;

                const auto &particle = input.particles[particleIndex];
                if (particle.cell < 0 ||
                    static_cast<uint32_t>(particle.cell) != context.cell) {
                    result.error = SpartaFusedExecutionError::BadParticleCell;
                    return result;
                }
                if (particle.species < 0 ||
                    static_cast<uint32_t>(particle.species) >=
                        descriptor.speciesCount) {
                    result.error = SpartaFusedExecutionError::BadSpecies;
                    return result;
                }

                const int32_t nextParticle = input.next[particleIndex];
                const bool finalParticle = context.remaining == 1;
                if ((finalParticle && nextParticle != -1) ||
                    (!finalParticle &&
                     (nextParticle < 0 ||
                      static_cast<uint32_t>(nextParticle) >=
                          descriptor.particleCount))) {
                    result.error = SpartaFusedExecutionError::BadTerminal;
                    return result;
                }

                const uint32_t species = particle.species;
                if (input.speciesToGroup[species] == descriptor.targetGroup &&
                    (input.cells[context.cell].mask & descriptor.groupBit)) {
                    const double mass = input.species[species].mass;
                    const double vx = particle.velocity[0];
                    const double vy = particle.velocity[1];
                    const double vz = particle.velocity[2];
                    if (!finite(mass) || !finite(vx) || !finite(vy) ||
                        !finite(vz)) {
                        result.error =
                            SpartaFusedExecutionError::NonfiniteInput;
                        return result;
                    }
                    const double vx2 = vx * vx;
                    const double vy2 = vy * vy;
                    const double vz2 = vz * vz;
                    const double velocitySquared = (vx2 + vy2) + vz2;
                    const SpartaFusedTally contribution = {
                        1.0,
                        mass,
                        mass * vx,
                        mass * vy,
                        mass * vz,
                        mass * velocitySquared,
                    };
                    for (const double value : contribution) {
                        if (!finite(value)) {
                            result.error =
                                SpartaFusedExecutionError::
                                    NonfiniteContribution;
                            return result;
                        }
                    }
                    auto &summary = summaries[context.cell];
                    for (size_t channel = 0; channel < summary.sums.size();
                         ++channel) {
                        summary.sums[channel] += contribution[channel];
                        if (!finite(summary.sums[channel])) {
                            result.error =
                                SpartaFusedExecutionError::NonfiniteSummary;
                            return result;
                        }
                    }
                    summary.eligible++;
                    result.counters.eligibleParticles++;
                    result.counters.fp64Multiplies += 7;
                    result.counters.fp64Adds += 8;
                }

                context.remaining--;
                context.particle = nextParticle;
                if (context.remaining == 0) {
                    context.valid = false;
                    active--;
                }
            }
            admissionError = admit();
            if (admissionError != SpartaFusedExecutionError::None) {
                result.error = admissionError;
                return result;
            }
        }
        if (nextCell != descriptor.cellCount ||
            visitedCount != descriptor.particleCount ||
            (descriptor.particleCount == 64 ?
                 visitedParticles != std::numeric_limits<uint64_t>::max() :
                 visitedParticles !=
                     ((uint64_t{1} << descriptor.particleCount) - 1))) {
            result.error = SpartaFusedExecutionError::IncompleteCoverage;
            return result;
        }

        for (uint32_t cell = 0; cell < descriptor.cellCount; ++cell) {
            const auto &summary = summaries[cell];
            if (!summary.valid || summary.eligible == 0) {
                continue;
            }
            result.tallies[cell] = summary.sums;
            result.counters.coherentWrites += SpartaFusedChannels;
            result.counters.writeAcknowledgements += SpartaFusedChannels;
        }
        return result;
    }
};

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_SPARTA_FUSED_CELL_MODEL_HH__
