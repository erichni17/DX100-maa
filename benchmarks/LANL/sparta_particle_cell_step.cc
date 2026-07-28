#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <numeric>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

#include "mem/LANLMAA/ReferenceModel.hh"

namespace
{

using gem5::lanlmaa::Admission;
using gem5::lanlmaa::Configuration;
using gem5::lanlmaa::DataType;
using gem5::lanlmaa::Ordering;
using gem5::lanlmaa::OverflowPolicy;
using gem5::lanlmaa::ReadContinuationModel;
using gem5::lanlmaa::ReadCounters;
using gem5::lanlmaa::UpdateCombinerModel;
using gem5::lanlmaa::UpdateCounters;
using gem5::lanlmaa::UpdateDrain;
using gem5::lanlmaa::UpdateOperation;

struct Cell
{
    uint32_t positiveNeighbor = 0;
    uint32_t negativeNeighbor = 0;
};

struct Particle
{
    uint32_t id = 0;
    uint32_t cell = 0;
    uint32_t visits = 0;
    double mass = 0.0;
    double vx = 0.0;
    double vy = 0.0;
    double vz = 0.0;
};

struct Dataset
{
    std::vector<uint64_t> packedCells;
    std::vector<Particle> particles;
};

struct ParticleResult
{
    uint32_t finalCell = 0;
    uint32_t visits = 0;
};

struct Result
{
    std::vector<ParticleResult> particles;
    std::vector<double> tallies;
};

struct ModelResult
{
    Result values;
    ReadCounters reads;
    UpdateCounters updates;
};

struct Options
{
    size_t particles = 8192;
    size_t cells = 4096;
    size_t maximumVisits = 8;
    size_t window = 64;
    size_t lineEntries = 0;
    size_t continuationContexts = 0;
    size_t combinerEntries = 0;
    size_t combinerBanks = 4;
    size_t descriptorItems = 16;
    uint64_t seed = 0x535041525441ULL;
    bool sorted = true;
    std::string descriptorAssembly;
    std::string descriptorMetadata;
    std::string compactDescriptorAssembly;
    std::string compactDescriptorMetadata;
};

constexpr uint64_t CellMask = (uint64_t{1} << 24) - 1;
constexpr uint64_t TallyBase = 0x300000000ULL;
constexpr size_t TalliesPerCell = 6;
constexpr uint64_t DescriptorAddress = 0x800;
constexpr uint64_t DescriptorStartVector = 0x900;
constexpr uint64_t DescriptorResultVector = 0xa00;
constexpr uint64_t DescriptorCompletion = 0xb00;
constexpr uint64_t DescriptorRecordBase = 0xc00;
constexpr uint64_t DescriptorTerminal =
    std::numeric_limits<uint64_t>::max();

template <class T>
uint64_t
toBits(T value)
{
    static_assert(sizeof(T) == sizeof(uint64_t));
    uint64_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}

template <class T>
T
fromBits(uint64_t bits)
{
    static_assert(sizeof(T) == sizeof(uint64_t));
    T value;
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}

uint64_t
packCell(const Cell &cell)
{
    if (cell.positiveNeighbor > CellMask ||
        cell.negativeNeighbor > CellMask) {
        throw std::invalid_argument("cell neighbor exceeds packed width");
    }
    return cell.positiveNeighbor |
           (static_cast<uint64_t>(cell.negativeNeighbor) << 24);
}

Cell
unpackCell(uint64_t word)
{
    Cell cell;
    cell.positiveNeighbor = word & CellMask;
    cell.negativeNeighbor = (word >> 24) & CellMask;
    return cell;
}

Configuration
configurationFor(const Options &options)
{
    Configuration configuration;
    configuration.operationEntries = options.window;
    configuration.lineEntries = options.lineEntries == 0
        ? std::max<size_t>(4, options.window / 2) : options.lineEntries;
    configuration.continuationContexts = options.continuationContexts == 0
        ? std::max<size_t>(1, options.window / 4)
        : options.continuationContexts;
    configuration.combinerEntries = options.combinerEntries == 0
        ? std::max<size_t>(4, options.window / 2)
        : options.combinerEntries;
    configuration.combinerBanks = options.combinerBanks;
    configuration.acknowledgementCredits = configuration.combinerEntries;
    return configuration;
}

Dataset
makeDataset(const Options &options)
{
    if (options.particles == 0 || options.cells < 2 ||
        options.cells > CellMask || options.maximumVisits == 0) {
        throw std::invalid_argument("invalid particle/cell/visit count");
    }

    Dataset data;
    data.packedCells.resize(options.cells);
    data.particles.resize(options.particles);
    std::mt19937_64 generator(options.seed);
    const size_t hotCells = std::min<size_t>(64, options.cells);
    for (size_t index = 0; index < options.cells; ++index) {
        Cell cell;
        switch (index % 3) {
          case 0:
            cell.positiveNeighbor = (index + 1) % options.cells;
            cell.negativeNeighbor =
                (index + options.cells - 1) % options.cells;
            break;
          case 1:
            cell.positiveNeighbor = generator() % options.cells;
            cell.negativeNeighbor = generator() % options.cells;
            break;
          default:
            cell.positiveNeighbor = generator() % hotCells;
            cell.negativeNeighbor = generator() % hotCells;
            break;
        }
        data.packedCells[index] = packCell(cell);
    }

    std::uniform_real_distribution<double> mass(0.5, 4.0);
    std::uniform_real_distribution<double> velocity(-3.0, 3.0);
    for (size_t index = 0; index < options.particles; ++index) {
        Particle particle;
        particle.id = index;
        switch (index % 3) {
          case 0:
            particle.cell = index % options.cells;
            break;
          case 1:
            particle.cell = generator() % options.cells;
            break;
          default:
            particle.cell = generator() % hotCells;
            break;
        }
        particle.visits = 1 + generator() % options.maximumVisits;
        particle.mass = mass(generator);
        particle.vx = velocity(generator);
        particle.vy = velocity(generator);
        particle.vz = velocity(generator);
        data.particles[index] = particle;
    }

    if (options.sorted) {
        std::stable_sort(
            data.particles.begin(), data.particles.end(),
            [](const Particle &left, const Particle &right) {
                return left.cell < right.cell;
            });
    } else {
        std::shuffle(data.particles.begin(), data.particles.end(), generator);
    }
    return data;
}

Result
emptyResult(size_t particles, size_t cells)
{
    Result result;
    result.particles.resize(particles);
    result.tallies.assign(cells * TalliesPerCell, 0.0);
    return result;
}

void
accumulateTallies(Result &result, uint32_t cell, const Particle &particle)
{
    const size_t base = cell * TalliesPerCell;
    result.tallies[base] += 1.0;
    result.tallies[base + 1] += particle.mass;
    result.tallies[base + 2] += particle.mass * particle.vx;
    result.tallies[base + 3] += particle.mass * particle.vy;
    result.tallies[base + 4] += particle.mass * particle.vz;
    result.tallies[base + 5] += particle.mass *
        (particle.vx * particle.vx + particle.vy * particle.vy +
         particle.vz * particle.vz);
}

std::vector<double>
tallyValues(const Particle &particle)
{
    return {
        1.0,
        particle.mass,
        particle.mass * particle.vx,
        particle.mass * particle.vy,
        particle.mass * particle.vz,
        particle.mass *
            (particle.vx * particle.vx + particle.vy * particle.vy +
             particle.vz * particle.vz)};
}

Result
runScalar(const Dataset &data)
{
    Result result = emptyResult(
        data.particles.size(), data.packedCells.size());
    for (const auto &particle : data.particles) {
        uint32_t cellIndex = particle.cell;
        uint32_t visits = 0;
        while (visits < particle.visits) {
            accumulateTallies(result, cellIndex, particle);
            ++visits;
            if (visits == particle.visits) {
                break;
            }
            const Cell cell = unpackCell(data.packedCells[cellIndex]);
            cellIndex = particle.vx >= 0.0
                ? cell.positiveNeighbor : cell.negativeNeighbor;
        }
        result.particles[particle.id] = ParticleResult{cellIndex, visits};
    }
    return result;
}

uint64_t
stagingIndex(
    size_t remainingVisits, bool positiveDirection, size_t cell,
    size_t cells)
{
    if (remainingVisits == 0 || cell >= cells) {
        throw std::invalid_argument("invalid SPARTA staging state");
    }
    const uint64_t visitLayer = remainingVisits - 1;
    const uint64_t directionLayer = positiveDirection ? 1 : 0;
    return (visitLayer * 2 + directionLayer) * cells + cell;
}

uint64_t
stagingPayload(size_t cell)
{
    return cell + 1;
}

void
emitDescriptorStaging(const Dataset &data, const Options &options)
{
    const bool emitAssembly = !options.descriptorAssembly.empty();
    const bool emitMetadata = !options.descriptorMetadata.empty();
    if (emitAssembly != emitMetadata) {
        throw std::invalid_argument(
            "descriptor assembly and metadata must be requested together");
    }
    if (!emitAssembly) {
        return;
    }
    if (options.descriptorItems > data.particles.size()) {
        throw std::invalid_argument(
            "descriptor items exceed generated particle count");
    }
    const size_t cells = data.packedCells.size();
    if (options.maximumVisits >
            std::numeric_limits<uint64_t>::max() / 2 / cells ||
        options.descriptorItems > std::numeric_limits<uint32_t>::max()) {
        throw std::invalid_argument("SPARTA staging geometry overflows");
    }
    const uint64_t recordCount = options.maximumVisits * 2 * cells;
    if (recordCount > std::numeric_limits<uint32_t>::max() ||
        options.maximumVisits > std::numeric_limits<uint32_t>::max()) {
        throw std::invalid_argument(
            "SPARTA staging field exceeds descriptor v1 width");
    }

    std::vector<uint64_t> starts;
    std::vector<uint32_t> rootVisits;
    std::vector<uint64_t> expected;
    std::vector<uint32_t> finalCells;
    starts.reserve(options.descriptorItems);
    rootVisits.reserve(options.descriptorItems);
    expected.reserve(options.descriptorItems);
    finalCells.reserve(options.descriptorItems);
    uint64_t executedVisits = 0;
    for (size_t index = 0; index < options.descriptorItems; ++index) {
        const Particle &particle = data.particles[index];
        const bool positive = particle.vx >= 0.0;
        uint32_t cellIndex = particle.cell;
        uint64_t sum = 0;
        starts.push_back(stagingIndex(
            particle.visits, positive, particle.cell, cells));
        rootVisits.push_back(particle.visits);
        for (size_t visit = 0; visit < particle.visits; ++visit) {
            sum += stagingPayload(cellIndex);
            if (visit + 1 != particle.visits) {
                const Cell cell = unpackCell(data.packedCells[cellIndex]);
                cellIndex = positive ? cell.positiveNeighbor :
                                       cell.negativeNeighbor;
            }
        }
        expected.push_back(sum);
        finalCells.push_back(cellIndex);
        executedVisits += particle.visits;
    }

    std::ofstream assembly(options.descriptorAssembly);
    if (!assembly) {
        throw std::runtime_error("cannot create SPARTA descriptor assembly");
    }
    assembly << "    .section .data\n"
             << "    .balign 64\n"
             << "    .org 0x" << std::hex << DescriptorAddress << "\n"
             << "    .quad 0x0002000131414d4c\n"
             << "    .quad " << std::dec << starts.size() << "\n"
             << "    .quad 0x" << std::hex << DescriptorStartVector << "\n"
             << "    .quad 0x" << DescriptorResultVector << "\n"
             << "    .quad 0x" << DescriptorCompletion << "\n"
             << "    .quad 0x" << DescriptorRecordBase << "\n"
             << "    .long " << std::dec << recordCount << "\n"
             << "    .long " << options.maximumVisits << "\n"
             << "    .quad 0xffffffffffffffff\n"
             << "    .org 0x" << std::hex << DescriptorStartVector << "\n";
    for (const uint64_t start : starts) {
        assembly << "    .quad " << std::dec << start << "\n";
    }
    assembly << "    .org 0x" << std::hex << DescriptorResultVector << "\n"
             << "    .zero " << std::dec
             << starts.size() * sizeof(uint64_t) << "\n"
             << "    .org 0x" << std::hex << DescriptorCompletion << "\n"
             << "    .zero 32\n"
             << "    .org 0x" << DescriptorRecordBase << "\n";
    for (size_t remaining = 1; remaining <= options.maximumVisits;
         ++remaining) {
        for (size_t direction = 0; direction < 2; ++direction) {
            const bool positive = direction != 0;
            for (size_t cellIndex = 0; cellIndex < cells; ++cellIndex) {
                uint64_t next = DescriptorTerminal;
                if (remaining != 1) {
                    const Cell cell = unpackCell(
                        data.packedCells[cellIndex]);
                    const size_t nextCell = positive ?
                        cell.positiveNeighbor : cell.negativeNeighbor;
                    next = stagingIndex(
                        remaining - 1, positive, nextCell, cells);
                }
                assembly << "    .quad " << std::dec << next << ", "
                         << stagingPayload(cellIndex) << "\n";
            }
        }
    }
    assembly.close();
    if (!assembly) {
        throw std::runtime_error("failed to write SPARTA descriptor assembly");
    }

    std::ofstream metadata(options.descriptorMetadata);
    if (!metadata) {
        throw std::runtime_error("cannot create SPARTA descriptor metadata");
    }
    metadata << "{\n"
             << "  \"schema_version\": 1,\n"
             << "  \"descriptor_opcode\": 2,\n"
             << "  \"mapping\": \"SPARTA state-expanded projection; "
                "state is remaining_visits_direction_cell and payload is "
                "cell_plus_one\",\n"
             << "  \"descriptor_address\": " << DescriptorAddress << ",\n"
             << "  \"start_vector\": " << DescriptorStartVector << ",\n"
             << "  \"result_vector\": " << DescriptorResultVector << ",\n"
             << "  \"completion_record\": " << DescriptorCompletion << ",\n"
             << "  \"record_base\": " << DescriptorRecordBase << ",\n"
             << "  \"native_cell_count\": " << cells << ",\n"
             << "  \"state_expansion_factor\": "
             << options.maximumVisits * 2 << ",\n"
             << "  \"record_count\": " << recordCount << ",\n"
             << "  \"record_bytes\": "
             << recordCount * 2 * sizeof(uint64_t) << ",\n"
             << "  \"native_packed_cell_bytes\": "
             << cells * sizeof(uint64_t) << ",\n"
             << "  \"maximum_steps\": " << options.maximumVisits << ",\n"
             << "  \"descriptor_items\": " << starts.size() << ",\n"
             << "  \"executed_record_visits\": " << executedVisits << ",\n"
             << "  \"start_indices\": [";
    for (size_t index = 0; index < starts.size(); ++index) {
        metadata << (index == 0 ? "" : ", ") << starts[index];
    }
    metadata << "],\n  \"root_visits\": [";
    for (size_t index = 0; index < rootVisits.size(); ++index) {
        metadata << (index == 0 ? "" : ", ") << rootVisits[index];
    }
    metadata << "],\n  \"final_cells\": [";
    for (size_t index = 0; index < finalCells.size(); ++index) {
        metadata << (index == 0 ? "" : ", ") << finalCells[index];
    }
    metadata << "],\n  \"expected_results\": [";
    for (size_t index = 0; index < expected.size(); ++index) {
        metadata << (index == 0 ? "" : ", ") << expected[index];
    }
    metadata << "]\n}\n";
    metadata.close();
    if (!metadata) {
        throw std::runtime_error("failed to write SPARTA descriptor metadata");
    }
}

uint64_t
compactStartState(const Particle &particle)
{
    if (particle.cell > CellMask || particle.visits == 0) {
        throw std::invalid_argument("invalid SPARTA compact start state");
    }
    const uint64_t direction = particle.vx >= 0.0 ? uint64_t{1} << 24 : 0;
    return particle.cell | direction |
           (static_cast<uint64_t>(particle.visits) << 25);
}

void
emitCompactDescriptorStaging(const Dataset &data, const Options &options)
{
    const bool emitAssembly = !options.compactDescriptorAssembly.empty();
    const bool emitMetadata = !options.compactDescriptorMetadata.empty();
    if (emitAssembly != emitMetadata) {
        throw std::invalid_argument(
            "compact descriptor assembly and metadata must be requested "
            "together");
    }
    if (!emitAssembly) {
        return;
    }
    if (options.descriptorItems > data.particles.size()) {
        throw std::invalid_argument(
            "descriptor items exceed generated particle count");
    }
    const size_t cells = data.packedCells.size();
    if (cells > uint64_t{1} << 24 ||
        options.maximumVisits > std::numeric_limits<uint32_t>::max() ||
        options.descriptorItems > std::numeric_limits<uint32_t>::max()) {
        throw std::invalid_argument(
            "SPARTA compact staging field exceeds descriptor v1 width");
    }

    std::vector<uint64_t> starts;
    std::vector<uint32_t> rootVisits;
    std::vector<uint64_t> expected;
    std::vector<uint32_t> finalCells;
    starts.reserve(options.descriptorItems);
    rootVisits.reserve(options.descriptorItems);
    expected.reserve(options.descriptorItems);
    finalCells.reserve(options.descriptorItems);
    uint64_t executedVisits = 0;
    for (size_t index = 0; index < options.descriptorItems; ++index) {
        const Particle &particle = data.particles[index];
        const bool positive = particle.vx >= 0.0;
        uint32_t cellIndex = particle.cell;
        uint64_t sum = 0;
        starts.push_back(compactStartState(particle));
        rootVisits.push_back(particle.visits);
        for (size_t visit = 0; visit < particle.visits; ++visit) {
            sum += stagingPayload(cellIndex);
            if (visit + 1 != particle.visits) {
                const Cell cell = unpackCell(data.packedCells[cellIndex]);
                cellIndex = positive ? cell.positiveNeighbor :
                                       cell.negativeNeighbor;
            }
        }
        expected.push_back(sum);
        finalCells.push_back(cellIndex);
        executedVisits += particle.visits;
    }

    std::ofstream assembly(options.compactDescriptorAssembly);
    if (!assembly) {
        throw std::runtime_error(
            "cannot create SPARTA compact descriptor assembly");
    }
    assembly << "    .section .data\n"
             << "    .balign 64\n"
             << "    .org 0x" << std::hex << DescriptorAddress << "\n"
             << "    .quad 0x0003000131414d4c\n"
             << "    .quad " << std::dec << starts.size() << "\n"
             << "    .quad 0x" << std::hex << DescriptorStartVector << "\n"
             << "    .quad 0x" << DescriptorResultVector << "\n"
             << "    .quad 0x" << DescriptorCompletion << "\n"
             << "    .quad 0x" << DescriptorRecordBase << "\n"
             << "    .long " << std::dec << cells << "\n"
             << "    .long " << options.maximumVisits << "\n"
             << "    .quad 0\n"
             << "    .org 0x" << std::hex << DescriptorStartVector << "\n";
    for (const uint64_t start : starts) {
        assembly << "    .quad " << std::dec << start << "\n";
    }
    assembly << "    .org 0x" << std::hex << DescriptorResultVector << "\n"
             << "    .zero " << std::dec
             << starts.size() * sizeof(uint64_t) << "\n"
             << "    .org 0x" << std::hex << DescriptorCompletion << "\n"
             << "    .zero 32\n"
             << "    .org 0x" << std::hex << DescriptorRecordBase << "\n";
    for (const uint64_t packedCell : data.packedCells) {
        assembly << "    .quad " << std::dec << packedCell << "\n";
    }
    assembly.close();
    if (!assembly) {
        throw std::runtime_error(
            "failed to write SPARTA compact descriptor assembly");
    }

    std::ofstream metadata(options.compactDescriptorMetadata);
    if (!metadata) {
        throw std::runtime_error(
            "cannot create SPARTA compact descriptor metadata");
    }
    metadata << "{\n"
             << "  \"schema_version\": 1,\n"
             << "  \"descriptor_opcode\": 3,\n"
             << "  \"mapping\": \"SPARTA compact projection; start state "
                "is remaining_visits_direction_cell, record stores two "
                "24-bit neighbors, and payload is derived cell_plus_one\",\n"
             << "  \"descriptor_address\": " << DescriptorAddress << ",\n"
             << "  \"start_vector\": " << DescriptorStartVector << ",\n"
             << "  \"result_vector\": " << DescriptorResultVector << ",\n"
             << "  \"completion_record\": " << DescriptorCompletion << ",\n"
             << "  \"record_base\": " << DescriptorRecordBase << ",\n"
             << "  \"native_cell_count\": " << cells << ",\n"
             << "  \"state_records_per_native_cell\": 1,\n"
             << "  \"record_count\": " << cells << ",\n"
             << "  \"record_bytes\": "
             << cells * sizeof(uint64_t) << ",\n"
             << "  \"native_packed_cell_bytes\": "
             << cells * sizeof(uint64_t) << ",\n"
             << "  \"maximum_steps\": " << options.maximumVisits << ",\n"
             << "  \"descriptor_items\": " << starts.size() << ",\n"
             << "  \"executed_record_visits\": " << executedVisits << ",\n"
             << "  \"start_states\": [";
    for (size_t index = 0; index < starts.size(); ++index) {
        metadata << (index == 0 ? "" : ", ") << starts[index];
    }
    metadata << "],\n  \"root_visits\": [";
    for (size_t index = 0; index < rootVisits.size(); ++index) {
        metadata << (index == 0 ? "" : ", ") << rootVisits[index];
    }
    metadata << "],\n  \"final_cells\": [";
    for (size_t index = 0; index < finalCells.size(); ++index) {
        metadata << (index == 0 ? "" : ", ") << finalCells[index];
    }
    metadata << "],\n  \"expected_results\": [";
    for (size_t index = 0; index < expected.size(); ++index) {
        metadata << (index == 0 ? "" : ", ") << expected[index];
    }
    metadata << "]\n}\n";
    metadata.close();
    if (!metadata) {
        throw std::runtime_error(
            "failed to write SPARTA compact descriptor metadata");
    }
}

uint64_t
tallyAddress(size_t cell, size_t tally)
{
    return TallyBase +
           (cell * TalliesPerCell + tally) * sizeof(double);
}

void
applyDrain(Result &result, const UpdateDrain &drain)
{
    if (drain.address < TallyBase || drain.dataType != DataType::Float64 ||
        drain.operation != UpdateOperation::Add) {
        throw std::runtime_error("invalid SPARTA update drain");
    }
    const uint64_t element = (drain.address - TallyBase) / sizeof(double);
    if (element >= result.tallies.size()) {
        throw std::runtime_error("out-of-range SPARTA update drain");
    }
    result.tallies[element] += fromBits<double>(drain.valueBits);
}

bool
drainUpdates(UpdateCombinerModel &model, Result &result)
{
    bool progress = false;
    while (auto drain = model.drainNext()) {
        applyDrain(result, *drain);
        if (!model.acknowledge(drain->drainId)) {
            throw std::runtime_error("update acknowledgement failed");
        }
        progress = true;
    }
    return progress;
}

void
submitUpdate(
    UpdateCombinerModel &model, Result &result, uint64_t tag,
    uint64_t address, double value)
{
    while (true) {
        const Admission admission = model.admitUpdate(
            tag, address, toBits(value), DataType::Float64,
            UpdateOperation::Add, Ordering::Relaxed,
            OverflowPolicy::Fault);
        if (admission == Admission::Accepted) {
            return;
        }
        if (admission == Admission::Invalid) {
            throw std::runtime_error("valid SPARTA update was rejected");
        }
        if (!drainUpdates(model, result)) {
            throw std::runtime_error("update combiner made no progress");
        }
    }
}

std::vector<uint8_t>
cellLine(const Dataset &data, uint64_t lineAddress)
{
    const size_t bytes = data.packedCells.size() * sizeof(uint64_t);
    if (lineAddress >= bytes) {
        throw std::runtime_error("out-of-range cell line request");
    }
    std::vector<uint8_t> line(64, 0);
    const size_t available = std::min<size_t>(64, bytes - lineAddress);
    const auto *source = reinterpret_cast<const uint8_t *>(
        data.packedCells.data());
    std::memcpy(line.data(), source + lineAddress, available);
    return line;
}

ModelResult
runModel(const Dataset &data, const Configuration &configuration)
{
    ReadContinuationModel readModel(configuration);
    UpdateCombinerModel updateModel(configuration);
    if (!readModel.valid() || !updateModel.valid()) {
        throw std::invalid_argument("invalid model configuration");
    }

    ModelResult output;
    output.values = emptyResult(
        data.particles.size(), data.packedCells.size());
    std::vector<bool> admitted(data.particles.size(), false);
    std::vector<bool> finished(data.particles.size(), false);
    std::vector<uint32_t> cells(data.particles.size());
    std::vector<uint32_t> visits(data.particles.size(), 0);
    for (size_t index = 0; index < data.particles.size(); ++index) {
        cells[index] = data.particles[index].cell;
    }

    size_t nextParticle = 0;
    size_t retired = 0;
    uint64_t updateTag = 1;
    while (retired < data.particles.size()) {
        bool progress = false;
        while (nextParticle < data.particles.size()) {
            const Admission admission = readModel.admitRead(
                nextParticle + 1, cells[nextParticle] * sizeof(uint64_t),
                sizeof(uint64_t), true);
            if (admission == Admission::WouldBlock) {
                break;
            }
            if (admission == Admission::Invalid) {
                throw std::runtime_error(
                    "valid particle admission was rejected");
            }
            admitted[nextParticle] = true;
            ++nextParticle;
            progress = true;
        }

        while (auto request = readModel.nextLineRequest()) {
            if (!readModel.returnLine(
                    request->lineAddress,
                    cellLine(data, request->lineAddress))) {
                throw std::runtime_error("cell line response was rejected");
            }
            progress = true;
        }

        for (size_t index = 0; index < nextParticle; ++index) {
            if (!admitted[index] || finished[index]) {
                continue;
            }
            const auto word = readModel.continuationValue(index + 1);
            if (!word) {
                continue;
            }
            const auto &particle = data.particles[index];
            const Cell cell = unpackCell(*word);
            const uint32_t nextVisit = visits[index] + 1;
            const bool done = nextVisit == particle.visits;
            const uint32_t nextCell = particle.vx >= 0.0
                ? cell.positiveNeighbor : cell.negativeNeighbor;

            if (done) {
                if (!readModel.finishContinuation(index + 1)) {
                    throw std::runtime_error(
                        "particle completion was rejected");
                }
            } else {
                const Admission admission = readModel.reissueContinuation(
                    index + 1, nextCell * sizeof(uint64_t),
                    sizeof(uint64_t));
                if (admission == Admission::WouldBlock) {
                    continue;
                }
                if (admission == Admission::Invalid) {
                    throw std::runtime_error(
                        "valid particle continuation was rejected");
                }
            }

            const auto values = tallyValues(particle);
            for (size_t tally = 0; tally < TalliesPerCell; ++tally) {
                submitUpdate(
                    updateModel, output.values, updateTag++,
                    tallyAddress(cells[index], tally), values[tally]);
            }
            visits[index] = nextVisit;
            if (done) {
                finished[index] = true;
            } else {
                cells[index] = nextCell;
            }
            progress = true;
        }

        while (auto completion = readModel.popRetired()) {
            const size_t scheduleIndex = completion->logicalTag - 1;
            const uint32_t id = data.particles[scheduleIndex].id;
            output.values.particles[id] = ParticleResult{
                cells[scheduleIndex], visits[scheduleIndex]};
            ++retired;
            progress = true;
        }

        if (!progress) {
            throw std::runtime_error(
                "particle model made no forward progress");
        }
    }
    drainUpdates(updateModel, output.values);
    if (readModel.outstandingOperations() != 0 ||
        readModel.outstandingContexts() != 0 ||
        updateModel.outstandingEntries() != 0) {
        throw std::runtime_error("model was not quiescent at completion");
    }
    output.reads = readModel.counters();
    output.updates = updateModel.counters();
    return output;
}

bool
close(double left, double right)
{
    const double scale = std::max({1.0, std::fabs(left), std::fabs(right)});
    return std::fabs(left - right) <= 1.0e-12 * scale;
}

bool
equalResults(const Result &scalar, const Result &model)
{
    if (scalar.particles.size() != model.particles.size() ||
        scalar.tallies.size() != model.tallies.size()) {
        return false;
    }
    for (size_t index = 0; index < scalar.particles.size(); ++index) {
        if (scalar.particles[index].finalCell !=
                model.particles[index].finalCell ||
            scalar.particles[index].visits != model.particles[index].visits) {
            return false;
        }
    }
    for (size_t index = 0; index < scalar.tallies.size(); ++index) {
        if (!close(scalar.tallies[index], model.tallies[index])) {
            return false;
        }
    }
    return true;
}

double
checksum(const Result &result)
{
    return std::accumulate(
        result.tallies.begin(), result.tallies.end(), 0.0);
}

size_t
parseSize(const std::string &value, const std::string &option)
{
    size_t consumed = 0;
    const auto parsed = std::stoull(value, &consumed, 0);
    if (consumed != value.size() || parsed == 0) {
        throw std::invalid_argument("invalid value for " + option);
    }
    return parsed;
}

Options
parseOptions(int argc, char **argv)
{
    Options options;
    for (int argument = 1; argument < argc; ++argument) {
        const std::string option = argv[argument];
        if (option == "--help") {
            std::cout << "usage: sparta_particle_cell_step [--particles N] "
                         "[--cells N] [--visits N] [--window N] "
                         "[--line-entries N] [--contexts N] "
                         "[--combiner-entries N] [--combiner-banks N] "
                         "[--descriptor-items N] [--seed N] "
                         "[--emit-descriptor-assembly PATH] "
                         "[--emit-descriptor-metadata PATH] "
                         "[--emit-compact-descriptor-assembly PATH] "
                         "[--emit-compact-descriptor-metadata PATH] "
                         "[--order sorted|shuffled]\n";
            std::exit(0);
        }
        if (argument + 1 == argc) {
            throw std::invalid_argument("missing value for " + option);
        }
        const std::string value = argv[++argument];
        if (option == "--particles") {
            options.particles = parseSize(value, option);
        } else if (option == "--cells") {
            options.cells = parseSize(value, option);
        } else if (option == "--visits") {
            options.maximumVisits = parseSize(value, option);
        } else if (option == "--window") {
            options.window = parseSize(value, option);
        } else if (option == "--line-entries") {
            options.lineEntries = parseSize(value, option);
        } else if (option == "--contexts") {
            options.continuationContexts = parseSize(value, option);
        } else if (option == "--combiner-entries") {
            options.combinerEntries = parseSize(value, option);
        } else if (option == "--combiner-banks") {
            options.combinerBanks = parseSize(value, option);
        } else if (option == "--descriptor-items") {
            options.descriptorItems = parseSize(value, option);
        } else if (option == "--seed") {
            options.seed = std::stoull(value, nullptr, 0);
        } else if (option == "--emit-descriptor-assembly") {
            options.descriptorAssembly = value;
        } else if (option == "--emit-descriptor-metadata") {
            options.descriptorMetadata = value;
        } else if (option == "--emit-compact-descriptor-assembly") {
            options.compactDescriptorAssembly = value;
        } else if (option == "--emit-compact-descriptor-metadata") {
            options.compactDescriptorMetadata = value;
        } else if (option == "--order") {
            if (value == "sorted") {
                options.sorted = true;
            } else if (value == "shuffled") {
                options.sorted = false;
            } else {
                throw std::invalid_argument("invalid particle order");
            }
        } else {
            throw std::invalid_argument("unknown option " + option);
        }
    }
    return options;
}

} // anonymous namespace

int
main(int argc, char **argv)
{
    try {
        const Options options = parseOptions(argc, argv);
        const Configuration configuration = configurationFor(options);
        const Dataset data = makeDataset(options);
        emitDescriptorStaging(data, options);
        emitCompactDescriptorStaging(data, options);
        const Result scalar = runScalar(data);
        const ModelResult model = runModel(data, configuration);
        const bool correct = equalResults(scalar, model.values);
        const uint64_t totalVisits = std::accumulate(
            model.values.particles.begin(), model.values.particles.end(),
            uint64_t{0}, [](uint64_t sum, const ParticleResult &particle) {
                return sum + particle.visits;
            });

        std::cout << "verification=" << (correct ? "PASS" : "FAIL") << '\n';
        std::cout << "particle_order="
                  << (options.sorted ? "sorted" : "shuffled") << '\n';
        std::cout << "particles=" << options.particles << '\n';
        std::cout << "cells=" << options.cells << '\n';
        std::cout << "maximum_visits=" << options.maximumVisits << '\n';
        std::cout << "executed_visits=" << totalVisits << '\n';
        std::cout << "window=" << options.window << '\n';
        std::cout << "line_entries=" << configuration.lineEntries << '\n';
        std::cout << "continuation_contexts="
                  << configuration.continuationContexts << '\n';
        std::cout << "combiner_entries="
                  << configuration.combinerEntries << '\n';
        std::cout << "combiner_banks="
                  << configuration.combinerBanks << '\n';
        std::cout << "read_logical_accesses="
                  << model.reads.logicalMemoryAccesses << '\n';
        std::cout << "read_physical_lines="
                  << model.reads.physicalLineReads << '\n';
        std::cout << "read_line_merge_hits="
                  << model.reads.lineMergeHits << '\n';
        std::cout << "read_duplicate_element_hits="
                  << model.reads.duplicateElementHits << '\n';
        std::cout << "context_would_block="
                  << model.reads.contextWouldBlock << '\n';
        std::cout << "continuation_steps="
                  << model.reads.continuationSteps << '\n';
        std::cout << "active_context_high_water="
                  << model.reads.activeContextHighWater << '\n';
        std::cout << "update_logical="
                  << model.updates.logicalUpdatesAdmitted << '\n';
        std::cout << "update_drains=" << model.updates.drains << '\n';
        std::cout << "update_combiner_hits="
                  << model.updates.combinerHits << '\n';
        std::cout << "update_conflicts="
                  << model.updates.updateConflicts << '\n';
        std::cout << "update_combiner_would_block="
                  << model.updates.combinerWouldBlock << '\n';
        std::cout << "update_bank_would_block="
                  << model.updates.combinerBankWouldBlock << '\n';
        std::cout << std::setprecision(17)
                  << "checksum=" << checksum(model.values) << '\n';
        return correct ? 0 : 2;
    } catch (const std::exception &error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }
}
