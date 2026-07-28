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
    size_t lineEntries = 0;
    size_t continuationContexts = 0;
    size_t combinerEntries = 0;
    size_t combinerBanks = 4;
    size_t descriptorItems = 16;
    uint64_t seed = 0x4252414e534f4eULL;
    std::string descriptorAssembly;
    std::string descriptorMetadata;
};

constexpr uint64_t NextMask = (uint64_t{1} << 24) - 1;
constexpr uint64_t AbsorptionMask = (uint64_t{1} << 16) - 1;
constexpr uint64_t TrackMask = (uint64_t{1} << 12) - 1;
constexpr uint64_t TerminalBit = uint64_t{1} << 52;
constexpr uint64_t TallyBase = 0x200000000ULL;
constexpr double EnergyCutoff = 1.0e-8;
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

struct StagedRoot
{
    uint32_t start = 0;
    uint32_t steps = 0;
    uint64_t expected = 0;
};

uint64_t
descriptorPayload(const Cell &cell)
{
    return static_cast<uint64_t>(cell.absorption) + cell.trackScale;
}

std::vector<StagedRoot>
selectStagedRoots(
    const Dataset &data, size_t maximumSteps, size_t requestedItems)
{
    std::vector<StagedRoot> roots;
    roots.reserve(requestedItems);
    for (const auto &photon : data.photons) {
        uint32_t cellIndex = photon.cell;
        uint64_t expected = 0;
        for (size_t step = 1; step <= maximumSteps; ++step) {
            const Cell cell = unpackCell(data.packedCells[cellIndex]);
            expected += descriptorPayload(cell);
            if (cell.terminal) {
                roots.push_back(StagedRoot{
                    photon.cell, static_cast<uint32_t>(step), expected});
                break;
            }
            cellIndex = cell.next;
        }
        if (roots.size() == requestedItems) {
            break;
        }
    }
    if (roots.size() != requestedItems) {
        throw std::runtime_error(
            "not enough explicitly terminating photon roots for descriptor");
    }
    return roots;
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
    if (data.packedCells.size() > std::numeric_limits<uint32_t>::max() ||
        options.maximumSteps > std::numeric_limits<uint32_t>::max() ||
        options.descriptorItems > std::numeric_limits<uint32_t>::max()) {
        throw std::invalid_argument(
            "descriptor staging field exceeds v1 width");
    }

    const auto roots = selectStagedRoots(
        data, options.maximumSteps, options.descriptorItems);
    uint64_t visits = 0;
    for (const auto &root : roots) {
        visits += root.steps;
    }

    std::ofstream assembly(options.descriptorAssembly);
    if (!assembly) {
        throw std::runtime_error("cannot create descriptor assembly");
    }
    assembly << "    .section .data\n"
             << "    .balign 64\n"
             << "    .org 0x" << std::hex << DescriptorAddress << "\n"
             << "    .quad 0x0002000131414d4c\n"
             << "    .quad " << std::dec << roots.size() << "\n"
             << "    .quad 0x" << std::hex << DescriptorStartVector << "\n"
             << "    .quad 0x" << DescriptorResultVector << "\n"
             << "    .quad 0x" << DescriptorCompletion << "\n"
             << "    .quad 0x" << DescriptorRecordBase << "\n"
             << "    .long " << std::dec << data.packedCells.size() << "\n"
             << "    .long " << options.maximumSteps << "\n"
             << "    .quad 0xffffffffffffffff\n"
             << "    .org 0x" << std::hex << DescriptorStartVector << "\n";
    for (const auto &root : roots) {
        assembly << "    .quad " << std::dec << root.start << "\n";
    }
    assembly << "    .org 0x" << std::hex << DescriptorResultVector << "\n"
             << "    .zero " << std::dec
             << roots.size() * sizeof(uint64_t) << "\n"
             << "    .org 0x" << std::hex << DescriptorCompletion << "\n"
             << "    .zero 32\n"
             << "    .org 0x" << DescriptorRecordBase << "\n";
    for (const uint64_t packed : data.packedCells) {
        const Cell cell = unpackCell(packed);
        assembly << "    .quad " << std::dec
                 << (cell.terminal ? DescriptorTerminal : cell.next) << ", "
                 << descriptorPayload(cell) << "\n";
    }
    assembly.close();
    if (!assembly) {
        throw std::runtime_error("failed to write descriptor assembly");
    }

    std::ofstream metadata(options.descriptorMetadata);
    if (!metadata) {
        throw std::runtime_error("cannot create descriptor metadata");
    }
    metadata << "{\n"
             << "  \"schema_version\": 1,\n"
             << "  \"mapping\": \"Branson packed-cell continuation "
                "projection; payload is absorption_plus_track_scale\",\n"
             << "  \"descriptor_address\": " << DescriptorAddress << ",\n"
             << "  \"start_vector\": " << DescriptorStartVector << ",\n"
             << "  \"result_vector\": " << DescriptorResultVector << ",\n"
             << "  \"completion_record\": " << DescriptorCompletion << ",\n"
             << "  \"record_base\": " << DescriptorRecordBase << ",\n"
             << "  \"record_count\": " << data.packedCells.size() << ",\n"
             << "  \"maximum_steps\": " << options.maximumSteps << ",\n"
             << "  \"descriptor_items\": " << roots.size() << ",\n"
             << "  \"executed_record_visits\": " << visits << ",\n"
             << "  \"start_indices\": [";
    for (size_t index = 0; index < roots.size(); ++index) {
        metadata << (index == 0 ? "" : ", ") << roots[index].start;
    }
    metadata << "],\n  \"root_steps\": [";
    for (size_t index = 0; index < roots.size(); ++index) {
        metadata << (index == 0 ? "" : ", ") << roots[index].steps;
    }
    metadata << "],\n  \"expected_results\": [";
    for (size_t index = 0; index < roots.size(); ++index) {
        metadata << (index == 0 ? "" : ", ") << roots[index].expected;
    }
    metadata << "]\n}\n";
    metadata.close();
    if (!metadata) {
        throw std::runtime_error("failed to write descriptor metadata");
    }
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
runModel(
    const Dataset &data, size_t maximumSteps,
    const Configuration &configuration)
{
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
                         "[--cells N] [--steps N] [--window N] "
                         "[--line-entries N] [--contexts N] "
                         "[--combiner-entries N] [--combiner-banks N] "
                         "[--descriptor-items N] [--seed N] "
                         "[--emit-descriptor-assembly PATH] "
                         "[--emit-descriptor-metadata PATH]\n";
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
        const Result scalar = runScalar(data, options.maximumSteps);
        const ModelResult model = runModel(
            data, options.maximumSteps, configuration);
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
