#include <array>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include "mem/LANLMAA/SpartaFusedCellModel.hh"

namespace
{

using namespace gem5::lanlmaa;

uint32_t
readU32(std::ifstream &stream)
{
    std::array<uint8_t, 4> bytes{};
    if (!stream.read(reinterpret_cast<char *>(bytes.data()), bytes.size())) {
        throw std::runtime_error("truncated 32-bit image field");
    }
    return descriptorReadLe32(bytes.data());
}

int32_t
readI32(std::ifstream &stream)
{
    return static_cast<int32_t>(readU32(stream));
}

uint64_t
readU64(std::ifstream &stream)
{
    std::array<uint8_t, 8> bytes{};
    if (!stream.read(reinterpret_cast<char *>(bytes.data()), bytes.size())) {
        throw std::runtime_error("truncated 64-bit image field");
    }
    return descriptorReadLe64(bytes.data());
}

double
fromBits(uint64_t bits)
{
    double value;
    static_assert(sizeof(value) == sizeof(bits));
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}

uint64_t
toBits(double value)
{
    uint64_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}

void
require(bool condition, const std::string &message)
{
    if (!condition) {
        throw std::runtime_error(message);
    }
}

} // anonymous namespace

int
main(int argc, char **argv)
{
    try {
        require(argc == 2, "expected one native-batch image path");
        std::ifstream stream(argv[1], std::ios::binary);
        require(stream.is_open(), "cannot open native-batch image");
        std::array<char, 8> magic{};
        require(
            static_cast<bool>(stream.read(magic.data(), magic.size())),
            "truncated native-batch image magic");
        const std::array<char, 8> expectedMagic = {
            'L', 'M', 'A', 'A', 'N', 'R', '1', '\0'};
        require(magic == expectedMagic, "bad native-batch image magic");

        SpartaFusedDescriptor descriptor;
        descriptor.cellCount = readU32(stream);
        descriptor.particleCount = readU32(stream);
        descriptor.speciesCount = readU32(stream);
        descriptor.groupBit = readU32(stream);
        descriptor.targetGroup = readI32(stream);
        const uint32_t channels = readU32(stream);
        descriptor.tallyCellStride = channels * sizeof(uint64_t);
        require(channels == SpartaFusedChannels, "bad channel count");
        require(
            descriptor.cellCount <= SpartaFusedMaximumCells &&
                descriptor.particleCount <= SpartaFusedMaximumParticles,
            "image exceeds fused-cell bounds");

        SpartaFusedInput input;
        input.cells.resize(descriptor.cellCount);
        for (auto &cell : input.cells) {
            cell.count = readI32(stream);
            cell.first = readI32(stream);
            cell.mask = readU32(stream);
        }
        input.next.resize(descriptor.particleCount);
        for (auto &next : input.next) {
            next = readI32(stream);
        }
        input.species.resize(descriptor.speciesCount);
        input.speciesToGroup.resize(descriptor.speciesCount);
        for (uint32_t species = 0; species < descriptor.speciesCount;
             ++species) {
            input.speciesToGroup[species] = readI32(stream);
            input.species[species].mass = fromBits(readU64(stream));
        }
        input.particles.resize(descriptor.particleCount);
        for (auto &particle : input.particles) {
            particle.species = readI32(stream);
            particle.cell = readI32(stream);
            for (double &velocity : particle.velocity) {
                velocity = fromBits(readU64(stream));
            }
        }
        std::vector<std::array<uint64_t, SpartaFusedChannels>> expected(
            descriptor.cellCount);
        for (auto &cell : expected) {
            for (uint64_t &value : cell) {
                value = readU64(stream);
            }
        }
        require(stream.peek() == std::ifstream::traits_type::eof(),
                "native-batch image has trailing bytes");

        const std::vector<SpartaFusedTally> initial(descriptor.cellCount);
        const auto result = SpartaFusedCellModel::execute(
            descriptor, input, initial);
        require(
            static_cast<bool>(result),
            "fused-cell model rejected valid native batch");
        for (uint32_t cell = 0; cell < descriptor.cellCount; ++cell) {
            for (uint32_t channel = 0; channel < SpartaFusedChannels;
                 ++channel) {
                require(
                    toBits(result.tallies[cell][channel]) ==
                        expected[cell][channel],
                    "fused-cell output disagrees with native batch");
            }
        }
        require(
            result.counters.particleVisits == descriptor.particleCount,
            "particle visits did not close");
        require(
            result.counters.coherentWrites ==
                result.counters.writeAcknowledgements,
            "write acknowledgements did not close");
        std::cout << "{\"particle_visits\":"
                  << result.counters.particleVisits
                  << ",\"eligible_particles\":"
                  << result.counters.eligibleParticles
                  << ",\"nonempty_cells\":"
                  << result.counters.nonemptyCells
                  << ",\"active_context_high_water\":"
                  << result.counters.activeContextHighWater
                  << ",\"fp64_multiplies\":"
                  << result.counters.fp64Multiplies
                  << ",\"fp64_adds\":" << result.counters.fp64Adds
                  << ",\"coherent_writes\":"
                  << result.counters.coherentWrites
                  << ",\"write_acknowledgements\":"
                  << result.counters.writeAcknowledgements << "}\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "SPARTA fused native-batch test: " << error.what()
                  << '\n';
        return 2;
    }
}
