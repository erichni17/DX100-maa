#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
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
    uint32_t next = 0;
    uint16_t absorption = 0;
    uint16_t trackScale = 0;
    bool terminal = false;
};

struct Photon
{
    uint32_t cell = 0;
    double energy = 0.0;
};

struct Dataset
{
    std::vector<uint64_t> packedCells;
    std::vector<Photon> photons;
};

struct PhotonResult
{
    uint32_t finalCell = 0;
    uint32_t steps = 0;
    double finalEnergy = 0.0;
};

struct Result
{
    std::vector<PhotonResult> photons;
    std::vector<double> absorbedTally;
    std::vector<double> trackTally;
};

struct ModelResult
{
    Result values;
    ReadCounters reads;
    UpdateCounters updates;
};

struct Options
{
    size_t photons = 8192;
    size_t cells = 4096;
    size_t maximumSteps = 12;
    size_t window = 64;
    uint64_t seed = 0x4252414e534f4eULL;
};

constexpr uint64_t NextMask = (uint64_t{1} << 24) - 1;
constexpr uint64_t AbsorptionMask = (uint64_t{1} << 16) - 1;
constexpr uint64_t TrackMask = (uint64_t{1} << 12) - 1;
constexpr uint64_t TerminalBit = uint64_t{1} << 52;
constexpr uint64_t TallyBase = 0x200000000ULL;
constexpr double EnergyCutoff = 1.0e-8;

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
    if (cell.next > NextMask || cell.trackScale > TrackMask) {
        throw std::invalid_argument("cell field exceeds packed width");
    }
    return cell.next |
           (static_cast<uint64_t>(cell.absorption) << 24) |
           (static_cast<uint64_t>(cell.trackScale) << 40) |
           (cell.terminal ? TerminalBit : 0);
}

Cell
unpackCell(uint64_t word)
{
    Cell cell;
    cell.next = word & NextMask;
    cell.absorption = (word >> 24) & AbsorptionMask;
    cell.trackScale = (word >> 40) & TrackMask;
    cell.terminal = (word & TerminalBit) != 0;
    return cell;
}

double
absorptionFraction(const Cell &cell)
{
    return static_cast<double>(cell.absorption) / 65536.0;
}

double
trackMultiplier(const Cell &cell)
{
    return static_cast<double>(cell.trackScale) / 256.0;
}

Configuration
configurationFor(size_t window)
{
    Configuration configuration;
    configuration.operationEntries = window;
    configuration.lineEntries = std::max<size_t>(4, window / 2);
    configuration.continuationContexts = std::max<size_t>(1, window / 4);
    configuration.combinerEntries = std::max<size_t>(4, window / 2);
    configuration.acknowledgementCredits = configuration.combinerEntries;
    return configuration;
}

Dataset
makeDataset(const Options &options)
{
    if (options.photons == 0 || options.cells < 2 ||
        options.cells > NextMask || options.maximumSteps == 0) {
        throw std::invalid_argument("invalid photon/cell/step count");
    }
    Dataset data;
    data.packedCells.resize(options.cells);
    data.photons.resize(options.photons);
    std::mt19937_64 generator(options.seed);
    const size_t hotCells = std::min<size_t>(64, options.cells);

    for (size_t index = 0; index < options.cells; ++index) {
        Cell cell;
        switch (index % 3) {
          case 0:
            cell.next = (index + 1 + index % 7) % options.cells;
            break;
          case 1:
            cell.next = generator() % options.cells;
            break;
          default:
            cell.next = generator() % hotCells;
            break;
        }
        cell.absorption = 512 + generator() % 15873;
        cell.trackScale = 128 + generator() % 385;
        cell.terminal = index % 37 == 0;
        data.packedCells[index] = packCell(cell);
    }

    std::uniform_real_distribution<double> energy(1.0, 10.0);
    for (size_t index = 0; index < options.photons; ++index) {
        Photon photon;
        switch (index % 3) {
          case 0:
            photon.cell = index % options.cells;
            break;
          case 1:
            photon.cell = generator() % options.cells;
            break;
          default:
            photon.cell = generator() % hotCells;
            break;
        }
        photon.energy = energy(generator);
        data.photons[index] = photon;
    }
    return data;
}

Result
emptyResult(size_t photons, size_t cells)
{
    Result result;
    result.photons.resize(photons);
    result.absorbedTally.assign(cells, 0.0);
    result.trackTally.assign(cells, 0.0);
    return result;
}

bool
terminal(const Cell &cell, double remainingEnergy, size_t steps, size_t limit)
{
    return cell.terminal || remainingEnergy <= EnergyCutoff || steps == limit;
}

Result
runScalar(const Dataset &data, size_t maximumSteps)
{
    Result result = emptyResult(data.photons.size(), data.packedCells.size());
    for (size_t index = 0; index < data.photons.size(); ++index) {
        uint32_t cellIndex = data.photons[index].cell;
        double energy = data.photons[index].energy;
        size_t steps = 0;
        while (steps < maximumSteps) {
            const Cell cell = unpackCell(data.packedCells[cellIndex]);
            const double absorbed = energy * absorptionFraction(cell);
            result.absorbedTally[cellIndex] += absorbed;
            result.trackTally[cellIndex] += absorbed * trackMultiplier(cell);
            energy -= absorbed;
            ++steps;
            if (terminal(cell, energy, steps, maximumSteps)) {
                break;
            }
            cellIndex = cell.next;
        }
        result.photons[index] = PhotonResult{
            cellIndex, static_cast<uint32_t>(steps), energy};
    }
    return result;
}

uint64_t
tallyAddress(size_t array, size_t cell, size_t cells)
{
    return TallyBase + (array * cells + cell) * sizeof(double);
}

void
applyDrain(Result &result, const UpdateDrain &drain, size_t cells)
{
    if (drain.address < TallyBase || drain.dataType != DataType::Float64 ||
        drain.operation != UpdateOperation::Add) {
        throw std::runtime_error("invalid Branson update drain");
    }
    const uint64_t element = (drain.address - TallyBase) / sizeof(double);
    const size_t array = element / cells;
    const size_t cell = element % cells;
    if (array > 1 || cell >= cells) {
        throw std::runtime_error("out-of-range Branson update drain");
    }
    const double value = fromBits<double>(drain.valueBits);
    if (array == 0) {
        result.absorbedTally[cell] += value;
    } else {
        result.trackTally[cell] += value;
    }
}

bool
drainUpdates(UpdateCombinerModel &model, Result &result, size_t cells)
{
    bool progress = false;
    while (auto drain = model.drainNext()) {
        applyDrain(result, *drain, cells);
        if (!model.acknowledge(drain->drainId)) {
            throw std::runtime_error("update acknowledgement failed");
        }
        progress = true;
    }
    return progress;
}

void
submitUpdate(
    UpdateCombinerModel &model, Result &result, size_t cells,
    uint64_t tag, uint64_t address, double value)
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
            throw std::runtime_error("valid Branson update was rejected");
        }
        if (!drainUpdates(model, result, cells)) {
            throw std::runtime_error("update combiner made no progress");
        }
    }
}

std::vector<uint8_t>
cellLine(const Dataset &data, uint64_t lineAddress)
{
    const size_t bytes = data.packedCells.size() * sizeof(uint64_t);
    if (lineAddress + 64 > bytes + 64) {
        throw std::runtime_error("out-of-range cell line request");
    }
    std::vector<uint8_t> line(64, 0);
    const size_t available = lineAddress < bytes
        ? std::min<size_t>(64, bytes - lineAddress) : 0;
    if (available) {
        const auto *source = reinterpret_cast<const uint8_t *>(
            data.packedCells.data());
        std::memcpy(line.data(), source + lineAddress, available);
    }
    return line;
}

ModelResult
runModel(const Dataset &data, size_t maximumSteps, size_t window)
{
    const Configuration configuration = configurationFor(window);
    ReadContinuationModel readModel(configuration);
    UpdateCombinerModel updateModel(configuration);
    if (!readModel.valid() || !updateModel.valid()) {
        throw std::invalid_argument("invalid model configuration");
    }

    ModelResult output;
    output.values = emptyResult(data.photons.size(), data.packedCells.size());
    std::vector<bool> admitted(data.photons.size(), false);
    std::vector<bool> finished(data.photons.size(), false);
    std::vector<uint32_t> cells(data.photons.size());
    std::vector<uint32_t> steps(data.photons.size(), 0);
    std::vector<double> energies(data.photons.size());
    for (size_t index = 0; index < data.photons.size(); ++index) {
        cells[index] = data.photons[index].cell;
        energies[index] = data.photons[index].energy;
    }

    size_t nextPhoton = 0;
    size_t retired = 0;
    uint64_t updateTag = 1;
    while (retired < data.photons.size()) {
        bool progress = false;
        while (nextPhoton < data.photons.size()) {
            const uint64_t address =
                cells[nextPhoton] * sizeof(uint64_t);
            const Admission admission = readModel.admitRead(
                nextPhoton + 1, address, sizeof(uint64_t), true);
            if (admission == Admission::WouldBlock) {
                break;
            }
            if (admission == Admission::Invalid) {
                throw std::runtime_error(
                    "valid photon admission was rejected");
            }
            admitted[nextPhoton] = true;
            ++nextPhoton;
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

        for (size_t index = 0; index < nextPhoton; ++index) {
            if (!admitted[index] || finished[index]) {
                continue;
            }
            const auto value = readModel.continuationValue(index + 1);
            if (!value) {
                continue;
            }
            const Cell cell = unpackCell(*value);
            const double absorbed =
                energies[index] * absorptionFraction(cell);
            const double remaining = energies[index] - absorbed;
            const uint32_t nextStep = steps[index] + 1;
            const bool done = terminal(
                cell, remaining, nextStep, maximumSteps);

            if (done) {
                if (!readModel.finishContinuation(index + 1)) {
                    throw std::runtime_error("photon completion was rejected");
                }
            } else {
                const Admission admission = readModel.reissueContinuation(
                    index + 1, cell.next * sizeof(uint64_t),
                    sizeof(uint64_t));
                if (admission == Admission::WouldBlock) {
                    continue;
                }
                if (admission == Admission::Invalid) {
                    throw std::runtime_error(
                        "valid photon continuation was rejected");
                }
            }

            submitUpdate(
                updateModel, output.values, data.packedCells.size(),
                updateTag++, tallyAddress(
                    0, cells[index], data.packedCells.size()), absorbed);
            submitUpdate(
                updateModel, output.values, data.packedCells.size(),
                updateTag++, tallyAddress(
                    1, cells[index], data.packedCells.size()),
                absorbed * trackMultiplier(cell));
            energies[index] = remaining;
            steps[index] = nextStep;
            if (done) {
                finished[index] = true;
            } else {
                cells[index] = cell.next;
            }
            progress = true;
        }

        while (auto completion = readModel.popRetired()) {
            const size_t index = completion->logicalTag - 1;
            output.values.photons[index] = PhotonResult{
                cells[index], steps[index], energies[index]};
            ++retired;
            progress = true;
        }

        if (!progress) {
            throw std::runtime_error("photon model made no forward progress");
        }
    }
    drainUpdates(updateModel, output.values, data.packedCells.size());
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
    if (scalar.photons.size() != model.photons.size() ||
        scalar.absorbedTally.size() != model.absorbedTally.size() ||
        scalar.trackTally.size() != model.trackTally.size()) {
        return false;
    }
    for (size_t index = 0; index < scalar.photons.size(); ++index) {
        const auto &left = scalar.photons[index];
        const auto &right = model.photons[index];
        if (left.finalCell != right.finalCell || left.steps != right.steps ||
            toBits(left.finalEnergy) != toBits(right.finalEnergy)) {
            return false;
        }
    }
    for (size_t cell = 0; cell < scalar.absorbedTally.size(); ++cell) {
        if (!close(scalar.absorbedTally[cell], model.absorbedTally[cell]) ||
            !close(scalar.trackTally[cell], model.trackTally[cell])) {
            return false;
        }
    }
    return true;
}

double
checksum(const Result &result)
{
    double sum = 0.0;
    for (const auto &photon : result.photons) {
        sum += photon.finalEnergy;
    }
    for (size_t cell = 0; cell < result.absorbedTally.size(); ++cell) {
        sum += result.absorbedTally[cell] + result.trackTally[cell];
    }
    return sum;
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
            std::cout << "usage: branson_photon_cell_walk [--photons N] "
                         "[--cells N] [--steps N] [--window N] [--seed N]\n";
            std::exit(0);
        }
        if (argument + 1 == argc) {
            throw std::invalid_argument("missing value for " + option);
        }
        const std::string value = argv[++argument];
        if (option == "--photons") {
            options.photons = parseSize(value, option);
        } else if (option == "--cells") {
            options.cells = parseSize(value, option);
        } else if (option == "--steps") {
            options.maximumSteps = parseSize(value, option);
        } else if (option == "--window") {
            options.window = parseSize(value, option);
        } else if (option == "--seed") {
            options.seed = std::stoull(value, nullptr, 0);
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
        const Dataset data = makeDataset(options);
        const Result scalar = runScalar(data, options.maximumSteps);
        const ModelResult model = runModel(
            data, options.maximumSteps, options.window);
        const bool correct = equalResults(scalar, model.values);
        const uint64_t totalSteps = std::accumulate(
            model.values.photons.begin(), model.values.photons.end(),
            uint64_t{0}, [](uint64_t sum, const PhotonResult &photon) {
                return sum + photon.steps;
            });

        std::cout << "verification=" << (correct ? "PASS" : "FAIL") << '\n';
        std::cout << "photons=" << options.photons << '\n';
        std::cout << "cells=" << options.cells << '\n';
        std::cout << "maximum_steps=" << options.maximumSteps << '\n';
        std::cout << "executed_steps=" << totalSteps << '\n';
        std::cout << "window=" << options.window << '\n';
        std::cout << "continuation_contexts="
                  << configurationFor(options.window).continuationContexts
                  << '\n';
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
