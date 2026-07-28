#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
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
    uint64_t seed = 0x535041525441ULL;
    bool sorted = true;
};

constexpr uint64_t CellMask = (uint64_t{1} << 24) - 1;
constexpr uint64_t TallyBase = 0x300000000ULL;
constexpr size_t TalliesPerCell = 6;

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
                         "[--combiner-entries N] [--seed N] "
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
        } else if (option == "--seed") {
            options.seed = std::stoull(value, nullptr, 0);
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
        std::cout << std::setprecision(17)
                  << "checksum=" << checksum(model.values) << '\n';
        return correct ? 0 : 2;
    } catch (const std::exception &error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }
}
