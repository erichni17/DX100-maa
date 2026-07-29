#include <array>
#include <cassert>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <vector>

#include "mem/LANLMAA/SpartaFusedCellModel.hh"

namespace
{

using namespace gem5::lanlmaa;

void
writeLe(std::array<uint8_t, SpartaFusedDescriptorBytes> &bytes,
        size_t offset, uint64_t value, size_t width)
{
    for (size_t index = 0; index < width; ++index) {
        bytes[offset + index] = (value >> (index * 8)) & 0xff;
    }
}

std::array<uint8_t, SpartaFusedDescriptorBytes>
validDescriptorBytes(uint32_t cells = 27, uint32_t particles = 64,
                     uint32_t species = 2)
{
    std::array<uint8_t, SpartaFusedDescriptorBytes> bytes{};
    writeLe(bytes, 0, DescriptorMagic, 4);
    writeLe(bytes, 4, SpartaFusedDescriptorVersion, 2);
    writeLe(bytes, 6, SpartaFusedOpcode, 1);
    writeLe(bytes, 7, SpartaFusedTallyExclusiveFlag, 1);
    writeLe(bytes, 8, cells, 4);
    writeLe(bytes, 12, particles, 4);
    writeLe(bytes, 16, 0x1000, 8);
    writeLe(bytes, 24, 0x3000, 8);
    writeLe(bytes, 32, 0x4000, 8);
    writeLe(bytes, 40, 0x6000, 8);
    writeLe(bytes, 48, 0x7000, 8);
    writeLe(bytes, 56, 0x8000, 8);
    writeLe(bytes, 64, 0x9000, 8);
    writeLe(bytes, 72, SpartaFusedAbiFingerprint, 8);
    writeLe(bytes, 80, 1, 4);
    writeLe(bytes, 84, 0, 4);
    writeLe(bytes, 88, species, 4);
    writeLe(bytes, 92, SpartaFusedChannels * sizeof(uint64_t), 4);
    return bytes;
}

void
expectDescriptorError(
    const std::array<uint8_t, SpartaFusedDescriptorBytes> &bytes,
    DescriptorError error)
{
    const auto result = decodeSpartaFusedDescriptor(bytes);
    assert(!result);
    assert(result.error == error);
}

SpartaFusedInput
makeInput(uint32_t cells = 27, uint32_t particles = 64)
{
    SpartaFusedInput input;
    input.cells.resize(cells);
    input.next.resize(particles, -1);
    input.particles.resize(particles);
    input.species = {{2.0}, {3.0}};
    input.speciesToGroup = {0, 1};
    for (auto &cell : input.cells) {
        cell.mask = 1;
    }
    if (cells > 3) {
        input.cells[3].mask = 0;
    }
    for (uint32_t particle = 0; particle < particles; ++particle) {
        auto &record = input.particles[particle];
        record.species = particle % 7 == 0 ? 1 : 0;
        record.cell = (particle * 11) % cells;
        record.velocity = {
            static_cast<double>(static_cast<int32_t>(particle % 5) - 2),
            static_cast<double>(static_cast<int32_t>(particle % 7) - 3),
            static_cast<double>(static_cast<int32_t>(particle % 3) - 1),
        };
    }
    for (int32_t particle = particles - 1; particle >= 0; --particle) {
        const uint32_t cell = input.particles[particle].cell;
        input.next[particle] = input.cells[cell].first;
        input.cells[cell].first = particle;
        input.cells[cell].count++;
    }
    return input;
}

std::vector<SpartaFusedTally>
scalarTallies(const SpartaFusedDescriptor &descriptor,
              const SpartaFusedInput &input)
{
    std::vector<SpartaFusedTally> tallies(descriptor.cellCount);
    for (uint32_t index = 0; index < descriptor.particleCount; ++index) {
        const auto &particle = input.particles[index];
        const uint32_t species = particle.species;
        if (input.speciesToGroup[species] != descriptor.targetGroup) {
            continue;
        }
        const uint32_t cell = particle.cell;
        if (!(input.cells[cell].mask & descriptor.groupBit)) {
            continue;
        }
        const double mass = input.species[species].mass;
        const double vx = particle.velocity[0];
        const double vy = particle.velocity[1];
        const double vz = particle.velocity[2];
        const double velocitySquared = (vx * vx + vy * vy) + vz * vz;
        const SpartaFusedTally contribution = {
            1.0,
            mass,
            mass * vx,
            mass * vy,
            mass * vz,
            mass * velocitySquared,
        };
        for (size_t channel = 0; channel < contribution.size(); ++channel) {
            tallies[cell][channel] += contribution[channel];
        }
    }
    return tallies;
}

uint32_t
eligibleParticles(const SpartaFusedDescriptor &descriptor,
                  const SpartaFusedInput &input)
{
    uint32_t eligible = 0;
    for (uint32_t index = 0; index < descriptor.particleCount; ++index) {
        const auto &particle = input.particles[index];
        if (input.speciesToGroup[particle.species] == descriptor.targetGroup &&
            (input.cells[particle.cell].mask & descriptor.groupBit)) {
            eligible++;
        }
    }
    return eligible;
}

uint32_t
writtenCells(const std::vector<SpartaFusedTally> &tallies)
{
    uint32_t cells = 0;
    for (const auto &tally : tallies) {
        if (tally[0] != 0.0) {
            cells++;
        }
    }
    return cells;
}

void
expectExecutionError(const SpartaFusedDescriptor &descriptor,
                     const SpartaFusedInput &input,
                     SpartaFusedExecutionError error)
{
    const std::vector<SpartaFusedTally> initial(descriptor.cellCount);
    const auto result = SpartaFusedCellModel::execute(
        descriptor, input, initial);
    assert(!result);
    assert(result.error == error);
    assert(result.tallies == initial);
    assert(result.counters.coherentWrites == 0);
    assert(result.counters.writeAcknowledgements == 0);
}

SpartaFusedInput
makeSingleCellInput(uint32_t particles)
{
    SpartaFusedInput input;
    input.cells.resize(1);
    input.cells[0].count = particles;
    input.cells[0].first = 0;
    input.cells[0].mask = 1;
    input.next.resize(particles, -1);
    input.particles.resize(particles);
    input.species = {{1.0}};
    input.speciesToGroup = {0};
    for (uint32_t index = 0; index < particles; ++index) {
        input.particles[index].species = 0;
        input.particles[index].cell = 0;
        if (index + 1 < particles) {
            input.next[index] = index + 1;
        }
    }
    return input;
}

} // anonymous namespace

int
main()
{
    const auto decoded = decodeSpartaFusedDescriptor(validDescriptorBytes());
    assert(decoded);
    const auto descriptor = decoded.descriptor;
    assert(descriptor.cellCount == 27);
    assert(descriptor.particleCount == 64);
    assert(descriptor.childInfoBase == 0x1000);
    assert(descriptor.nextBase == 0x3000);
    assert(descriptor.particleBase == 0x4000);
    assert(descriptor.speciesBase == 0x6000);
    assert(descriptor.speciesToGroupBase == 0x7000);
    assert(descriptor.tallyBase == 0x8000);
    assert(descriptor.completionRecord == 0x9000);
    assert(descriptor.abiFingerprint == SpartaFusedAbiFingerprint);
    assert(descriptor.groupBit == 1);
    assert(descriptor.targetGroup == 0);
    assert(descriptor.speciesCount == 2);
    assert(descriptor.tallyCellStride == 48);

    auto bytes = validDescriptorBytes();
    writeLe(bytes, 0, 0, 4);
    expectDescriptorError(bytes, DescriptorError::BadMagic);
    bytes = validDescriptorBytes();
    writeLe(bytes, 4, 1, 2);
    expectDescriptorError(bytes, DescriptorError::BadVersion);
    bytes = validDescriptorBytes();
    writeLe(bytes, 6, 6, 1);
    expectDescriptorError(bytes, DescriptorError::BadOpcode);
    for (const uint8_t flags : {0U, 2U, 3U}) {
        bytes = validDescriptorBytes();
        writeLe(bytes, 7, flags, 1);
        expectDescriptorError(bytes, DescriptorError::UnsupportedFlags);
    }
    for (const size_t offset : {8UL, 12UL}) {
        bytes = validDescriptorBytes();
        writeLe(bytes, offset, 0, 4);
        expectDescriptorError(bytes, DescriptorError::Empty);
        bytes = validDescriptorBytes();
        writeLe(bytes, offset, 65, 4);
        expectDescriptorError(bytes, DescriptorError::TooManyItems);
    }
    bytes = validDescriptorBytes();
    writeLe(bytes, 72, SpartaFusedAbiFingerprint + 1, 8);
    expectDescriptorError(bytes, DescriptorError::BadRecordValue);
    for (const std::array<uint64_t, 2> field :
         std::array<std::array<uint64_t, 2>, 3>{{
             {80, 0},
             {84, uint64_t{1} << 31},
             {88, 0},
         }}) {
        bytes = validDescriptorBytes();
        writeLe(bytes, field[0], field[1], 4);
        expectDescriptorError(bytes, DescriptorError::BadRecordGeometry);
    }
    for (const uint32_t stride : {40U, 50U}) {
        bytes = validDescriptorBytes();
        writeLe(bytes, 92, stride, 4);
        expectDescriptorError(bytes, DescriptorError::BadRecordGeometry);
    }
    bytes = validDescriptorBytes();
    writeLe(bytes, 96, 1, 1);
    expectDescriptorError(bytes, DescriptorError::ReservedNonzero);
    bytes = validDescriptorBytes();
    writeLe(bytes, 24, 0x3002, 8);
    expectDescriptorError(bytes, DescriptorError::MisalignedVector);
    bytes = validDescriptorBytes();
    writeLe(bytes, 16, SpartaFusedAddressLimit - 8, 8);
    expectDescriptorError(bytes, DescriptorError::RangeOverflow);
    bytes = validDescriptorBytes();
    writeLe(bytes, 24, 0x1000, 8);
    expectDescriptorError(bytes, DescriptorError::OverlappingInput);

    const auto input = makeInput();
    const std::vector<SpartaFusedTally> initial(descriptor.cellCount);
    const auto result = SpartaFusedCellModel::execute(
        descriptor, input, initial);
    assert(result);
    const auto scalar = scalarTallies(descriptor, input);
    assert(result.tallies == scalar);
    const uint32_t eligible = eligibleParticles(descriptor, input);
    assert(result.counters.cellsScanned == descriptor.cellCount);
    assert(result.counters.nonemptyCells == descriptor.cellCount);
    assert(result.counters.particleVisits == descriptor.particleCount);
    assert(result.counters.eligibleParticles == eligible);
    assert(result.counters.summariesUsed == descriptor.cellCount);
    assert(result.counters.activeContextHighWater ==
           SpartaFusedActiveContexts);
    assert(result.counters.fp64Multiplies == eligible * 7);
    assert(result.counters.fp64Adds == eligible * 8);
    assert(result.counters.coherentWrites ==
           writtenCells(scalar) * SpartaFusedChannels);
    assert(result.counters.writeAcknowledgements ==
           result.counters.coherentWrites);

    auto badInput = input;
    badInput.particles.pop_back();
    expectExecutionError(
        descriptor, badInput, SpartaFusedExecutionError::SourceExtent);

    auto nonzero = initial;
    nonzero[0][0] = 1.0;
    const auto nonzeroResult = SpartaFusedCellModel::execute(
        descriptor, input, nonzero);
    assert(!nonzeroResult);
    assert(nonzeroResult.error == SpartaFusedExecutionError::TallyNotZero);
    assert(nonzeroResult.tallies == nonzero);

    badInput = input;
    badInput.cells[0].count = -1;
    expectExecutionError(
        descriptor, badInput, SpartaFusedExecutionError::BadCellCount);

    badInput = input;
    badInput.cells[0].count = 0;
    badInput.cells[0].first = 0;
    expectExecutionError(
        descriptor, badInput, SpartaFusedExecutionError::BadEmptyCell);

    badInput = input;
    badInput.cells[0].first = descriptor.particleCount;
    expectExecutionError(
        descriptor, badInput, SpartaFusedExecutionError::BadParticleIndex);

    uint32_t multiCell = 0;
    while (input.cells[multiCell].count < 2) {
        multiCell++;
    }
    const uint32_t first = input.cells[multiCell].first;
    badInput = input;
    badInput.next[first] = first;
    expectExecutionError(
        descriptor, badInput, SpartaFusedExecutionError::DuplicateParticle);

    badInput = input;
    badInput.particles[first].cell = (multiCell + 1) % descriptor.cellCount;
    expectExecutionError(
        descriptor, badInput, SpartaFusedExecutionError::BadParticleCell);

    uint32_t last = first;
    while (input.next[last] >= 0) {
        last = input.next[last];
    }
    badInput = input;
    badInput.next[last] = 0;
    expectExecutionError(
        descriptor, badInput, SpartaFusedExecutionError::BadTerminal);

    badInput = input;
    badInput.particles[first].species = descriptor.speciesCount;
    expectExecutionError(
        descriptor, badInput, SpartaFusedExecutionError::BadSpecies);

    uint32_t eligibleIndex = 0;
    while (input.speciesToGroup[input.particles[eligibleIndex].species] !=
               descriptor.targetGroup ||
           !(input.cells[input.particles[eligibleIndex].cell].mask &
             descriptor.groupBit)) {
        eligibleIndex++;
    }
    badInput = input;
    badInput.particles[eligibleIndex].velocity[0] =
        std::numeric_limits<double>::quiet_NaN();
    expectExecutionError(
        descriptor, badInput, SpartaFusedExecutionError::NonfiniteInput);

    badInput = input;
    badInput.particles[eligibleIndex].velocity[0] =
        std::numeric_limits<double>::max();
    expectExecutionError(
        descriptor, badInput,
        SpartaFusedExecutionError::NonfiniteContribution);

    const auto threeBytes = validDescriptorBytes(1, 3, 1);
    const auto threeDescriptor =
        decodeSpartaFusedDescriptor(threeBytes).descriptor;
    auto threeInput = makeSingleCellInput(3);
    threeInput.species[0].mass = std::numeric_limits<double>::max() / 2.0;
    expectExecutionError(
        threeDescriptor, threeInput,
        SpartaFusedExecutionError::NonfiniteSummary);

    const auto twoDescriptor =
        decodeSpartaFusedDescriptor(validDescriptorBytes(1, 2, 1)).descriptor;
    auto incomplete = makeSingleCellInput(2);
    incomplete.cells[0].count = 1;
    incomplete.next[0] = -1;
    expectExecutionError(
        twoDescriptor, incomplete,
        SpartaFusedExecutionError::IncompleteCoverage);

    return 0;
}
