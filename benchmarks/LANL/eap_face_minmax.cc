#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <limits>
#include <optional>
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

struct Face
{
    uint32_t low = 0;
    uint32_t high = 0;
    bool active = false;
};

struct Dataset
{
    std::vector<Face> faces;
    std::vector<double> cellHalfLow;
    std::vector<double> cellHalfHigh;
    std::vector<double> cellValueLow;
    std::vector<double> cellValueHigh;
};

struct Result
{
    std::vector<double> highMinimum;
    std::vector<double> highMaximum;
    std::vector<double> lowMinimum;
    std::vector<double> lowMaximum;
};

struct ModelResult
{
    Result values;
    ReadCounters reads;
    UpdateCounters updates;
};

struct Options
{
    size_t faces = 65536;
    size_t cells = 4096;
    size_t window = 64;
    uint64_t seed = 0x4c414e4c;
};

struct PackedMemory
{
    std::vector<uint8_t> bytes;
    uint64_t halfLowBase = 0;
    uint64_t halfHighBase = 0;
    uint64_t valueLowBase = 0;
    uint64_t valueHighBase = 0;
};

struct UpdateEvent
{
    uint64_t address = 0;
    uint64_t valueBits = 0;
    UpdateOperation operation = UpdateOperation::Overwrite;
};

constexpr uint64_t UpdateBase = 0x100000000ULL;

size_t
roundUp(size_t value, size_t quantum)
{
    return ((value + quantum - 1) / quantum) * quantum;
}

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
    if (options.faces == 0 || options.cells < 2) {
        throw std::invalid_argument("faces must be positive and cells >= 2");
    }

    Dataset data;
    data.faces.resize(options.faces);
    data.cellHalfLow.resize(options.cells);
    data.cellHalfHigh.resize(options.cells);
    data.cellValueLow.resize(options.cells);
    data.cellValueHigh.resize(options.cells);

    std::mt19937_64 generator(options.seed);
    std::uniform_real_distribution<double> halfDistance(0.25, 2.0);
    std::uniform_real_distribution<double> value(-10.0, 10.0);
    for (size_t cell = 0; cell < options.cells; ++cell) {
        data.cellHalfLow[cell] = halfDistance(generator);
        data.cellHalfHigh[cell] = halfDistance(generator);
        data.cellValueLow[cell] = value(generator);
        data.cellValueHigh[cell] = value(generator);
    }

    const size_t hotCells = std::min<size_t>(32, options.cells);
    for (size_t ordinal = 0; ordinal < options.faces; ++ordinal) {
        Face face;
        switch (ordinal % 3) {
          case 0:
            face.low = static_cast<uint32_t>((ordinal / 3) % options.cells);
            face.high = static_cast<uint32_t>(
                (face.low + 1 + (ordinal % 7)) % options.cells);
            break;
          case 1:
            face.low = static_cast<uint32_t>(generator() % options.cells);
            face.high = static_cast<uint32_t>(generator() % options.cells);
            break;
          default:
            face.low = static_cast<uint32_t>(generator() % hotCells);
            face.high = static_cast<uint32_t>(generator() % hotCells);
            break;
        }
        face.active = generator() % 20 != 0;
        data.faces[ordinal] = face;
    }
    return data;
}

Result
emptyResult(size_t cells)
{
    Result result;
    const double infinity = std::numeric_limits<double>::infinity();
    result.highMinimum.assign(cells, infinity);
    result.highMaximum.assign(cells, -infinity);
    result.lowMinimum.assign(cells, infinity);
    result.lowMaximum.assign(cells, -infinity);
    return result;
}

double
faceValue(
    double highHalfLow, double lowValueHigh,
    double lowHalfHigh, double highValueLow)
{
    return (highHalfLow * lowValueHigh + lowHalfHigh * highValueLow) /
           (highHalfLow + lowHalfHigh);
}

Result
runScalar(const Dataset &data)
{
    Result result = emptyResult(data.cellHalfLow.size());
    for (const auto &face : data.faces) {
        if (!face.active) {
            continue;
        }
        const double value = faceValue(
            data.cellHalfLow[face.high], data.cellValueHigh[face.low],
            data.cellHalfHigh[face.low], data.cellValueLow[face.high]);
        result.highMinimum[face.high] =
            std::fmin(result.highMinimum[face.high], value);
        result.highMaximum[face.high] =
            std::fmax(result.highMaximum[face.high], value);
        result.lowMinimum[face.low] =
            std::fmin(result.lowMinimum[face.low], value);
        result.lowMaximum[face.low] =
            std::fmax(result.lowMaximum[face.low], value);
    }
    return result;
}

PackedMemory
packMemory(const Dataset &data)
{
    PackedMemory memory;
    const size_t arrayBytes = data.cellHalfLow.size() * sizeof(double);
    const size_t stride = roundUp(arrayBytes, 64);
    memory.halfLowBase = 0;
    memory.halfHighBase = stride;
    memory.valueLowBase = 2 * stride;
    memory.valueHighBase = 3 * stride;
    memory.bytes.resize(4 * stride + 64, 0);

    std::memcpy(
        memory.bytes.data() + memory.halfLowBase,
        data.cellHalfLow.data(), arrayBytes);
    std::memcpy(
        memory.bytes.data() + memory.halfHighBase,
        data.cellHalfHigh.data(), arrayBytes);
    std::memcpy(
        memory.bytes.data() + memory.valueLowBase,
        data.cellValueLow.data(), arrayBytes);
    std::memcpy(
        memory.bytes.data() + memory.valueHighBase,
        data.cellValueHigh.data(), arrayBytes);
    return memory;
}

std::vector<double>
gatherWithModel(
    const std::vector<uint64_t> &addresses, const PackedMemory &memory,
    const Configuration &configuration, ReadCounters &counters)
{
    ReadContinuationModel model(configuration);
    if (!model.valid()) {
        throw std::invalid_argument("invalid read-model configuration");
    }
    std::vector<double> values(addresses.size());
    size_t next = 0;
    size_t completed = 0;

    while (completed < addresses.size()) {
        bool progress = false;
        while (next < addresses.size()) {
            const auto admission = model.admitRead(
                next + 1, addresses[next], sizeof(double));
            if (admission == Admission::WouldBlock) {
                break;
            }
            if (admission != Admission::Accepted) {
                throw std::runtime_error("model rejected a valid gather");
            }
            ++next;
            progress = true;
        }

        while (auto request = model.nextLineRequest()) {
            if (request->lineAddress + 64 > memory.bytes.size()) {
                throw std::runtime_error(
                    "model generated an out-of-range line");
            }
            std::vector<uint8_t> line(64);
            std::memcpy(
                line.data(), memory.bytes.data() + request->lineAddress, 64);
            if (!model.returnLine(request->lineAddress, line)) {
                throw std::runtime_error("model rejected a line response");
            }
            progress = true;
        }

        while (auto completion = model.popRetired()) {
            values[completion->logicalTag - 1] =
                fromBits<double>(completion->value);
            ++completed;
            progress = true;
        }
        if (!progress) {
            throw std::runtime_error("read model made no forward progress");
        }
    }
    counters = model.counters();
    return values;
}

uint64_t
updateAddress(size_t array, size_t cell, size_t cells)
{
    return UpdateBase + (array * cells + cell) * sizeof(double);
}

void
applyDrain(Result &result, const UpdateDrain &drain, size_t cells)
{
    if (drain.address < UpdateBase) {
        throw std::runtime_error("invalid update address");
    }
    const uint64_t element = (drain.address - UpdateBase) / sizeof(double);
    const size_t array = element / cells;
    const size_t cell = element % cells;
    if (array >= 4 || cell >= cells || drain.dataType != DataType::Float64) {
        throw std::runtime_error("invalid update drain metadata");
    }
    const double value = fromBits<double>(drain.valueBits);
    std::vector<double> *target = nullptr;
    switch (array) {
      case 0:
        target = &result.highMinimum;
        break;
      case 1:
        target = &result.highMaximum;
        break;
      case 2:
        target = &result.lowMinimum;
        break;
      case 3:
        target = &result.lowMaximum;
        break;
    }
    if (drain.operation == UpdateOperation::Min) {
        (*target)[cell] = std::fmin((*target)[cell], value);
    } else if (drain.operation == UpdateOperation::Max) {
        (*target)[cell] = std::fmax((*target)[cell], value);
    } else {
        throw std::runtime_error("unexpected EAP update operation");
    }
}

bool
drainUpdates(
    UpdateCombinerModel &model, Result &result, size_t cells)
{
    bool progress = false;
    while (auto drain = model.drainNext()) {
        applyDrain(result, *drain, cells);
        if (!model.acknowledge(drain->drainId)) {
            throw std::runtime_error(
                "model rejected an update acknowledgement");
        }
        progress = true;
    }
    return progress;
}

ModelResult
runModel(const Dataset &data, size_t window)
{
    const Configuration configuration = configurationFor(window);
    if (!configuration.valid()) {
        throw std::invalid_argument("invalid window configuration");
    }
    const PackedMemory memory = packMemory(data);
    std::vector<uint64_t> addresses;
    addresses.reserve(data.faces.size() * 4);
    for (const auto &face : data.faces) {
        if (!face.active) {
            continue;
        }
        addresses.push_back(
            memory.halfLowBase + face.high * sizeof(double));
        addresses.push_back(
            memory.valueHighBase + face.low * sizeof(double));
        addresses.push_back(
            memory.halfHighBase + face.low * sizeof(double));
        addresses.push_back(
            memory.valueLowBase + face.high * sizeof(double));
    }

    ModelResult output;
    output.values = emptyResult(data.cellHalfLow.size());
    const auto gathered = gatherWithModel(
        addresses, memory, configuration, output.reads);

    std::vector<UpdateEvent> updates;
    updates.reserve(addresses.size());
    size_t gather = 0;
    const size_t cells = data.cellHalfLow.size();
    for (const auto &face : data.faces) {
        if (!face.active) {
            continue;
        }
        const double value = faceValue(
            gathered[gather], gathered[gather + 1],
            gathered[gather + 2], gathered[gather + 3]);
        gather += 4;
        const uint64_t bits = toBits(value);
        updates.push_back(UpdateEvent{
            updateAddress(0, face.high, cells), bits, UpdateOperation::Min});
        updates.push_back(UpdateEvent{
            updateAddress(1, face.high, cells), bits, UpdateOperation::Max});
        updates.push_back(UpdateEvent{
            updateAddress(2, face.low, cells), bits, UpdateOperation::Min});
        updates.push_back(UpdateEvent{
            updateAddress(3, face.low, cells), bits, UpdateOperation::Max});
    }

    UpdateCombinerModel updateModel(configuration);
    size_t next = 0;
    while (next < updates.size()) {
        const auto &update = updates[next];
        const Admission admission = updateModel.admitUpdate(
            next + 1, update.address, update.valueBits, DataType::Float64,
            update.operation, Ordering::Relaxed, OverflowPolicy::Fault);
        if (admission == Admission::Accepted) {
            ++next;
            continue;
        }
        if (admission == Admission::Invalid) {
            throw std::runtime_error("model rejected a valid EAP update");
        }
        if (!drainUpdates(updateModel, output.values, cells)) {
            throw std::runtime_error("update model made no forward progress");
        }
    }
    drainUpdates(updateModel, output.values, cells);
    if (updateModel.outstandingEntries() != 0) {
        throw std::runtime_error("updates remained after final drain");
    }
    output.updates = updateModel.counters();
    return output;
}

bool
equalBits(const std::vector<double> &left, const std::vector<double> &right)
{
    if (left.size() != right.size()) {
        return false;
    }
    for (size_t index = 0; index < left.size(); ++index) {
        if (toBits(left[index]) != toBits(right[index])) {
            return false;
        }
    }
    return true;
}

bool
equalResults(const Result &left, const Result &right)
{
    return equalBits(left.highMinimum, right.highMinimum) &&
           equalBits(left.highMaximum, right.highMaximum) &&
           equalBits(left.lowMinimum, right.lowMinimum) &&
           equalBits(left.lowMaximum, right.lowMaximum);
}

double
checksum(const Result &result)
{
    double sum = 0.0;
    const std::vector<const std::vector<double> *> arrays = {
        &result.highMinimum, &result.highMaximum,
        &result.lowMinimum, &result.lowMaximum};
    for (const auto *array : arrays) {
        for (const double value : *array) {
            if (std::isfinite(value)) {
                sum += value;
            }
        }
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
            std::cout << "usage: eap_face_minmax [--faces N] [--cells N] "
                         "[--window N] [--seed N]\n";
            std::exit(0);
        }
        if (argument + 1 == argc) {
            throw std::invalid_argument("missing value for " + option);
        }
        const std::string value = argv[++argument];
        if (option == "--faces") {
            options.faces = parseSize(value, option);
        } else if (option == "--cells") {
            options.cells = parseSize(value, option);
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
        const Result scalar = runScalar(data);
        const ModelResult model = runModel(data, options.window);
        const bool correct = equalResults(scalar, model.values);
        const size_t active = std::count_if(
            data.faces.begin(), data.faces.end(),
            [](const Face &face) { return face.active; });

        std::cout << "verification=" << (correct ? "PASS" : "FAIL") << '\n';
        std::cout << "faces=" << options.faces << '\n';
        std::cout << "active_faces=" << active << '\n';
        std::cout << "cells=" << options.cells << '\n';
        std::cout << "window=" << options.window << '\n';
        std::cout << "read_logical_accesses="
                  << model.reads.logicalMemoryAccesses << '\n';
        std::cout << "read_physical_lines="
                  << model.reads.physicalLineReads << '\n';
        std::cout << "read_line_merge_hits="
                  << model.reads.lineMergeHits << '\n';
        std::cout << "read_duplicate_element_hits="
                  << model.reads.duplicateElementHits << '\n';
        std::cout << "read_line_would_block="
                  << model.reads.lineWouldBlock << '\n';
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
