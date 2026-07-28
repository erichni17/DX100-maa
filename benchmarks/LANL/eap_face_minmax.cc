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

enum class FaceKind
{
    Inactive,
    Internal,
    LowBoundary,
    HighBoundary,
};

enum class InternalMode
{
    Normal,
    DensityGuarded,
    PressureWeighted,
};

enum class BoundarySource
{
    None,
    Cell,
    FaceValue,
};

struct Face
{
    uint32_t low = 0;
    uint32_t high = 0;
    uint32_t faceValueOrdinal = 0;
    FaceKind kind = FaceKind::Inactive;
};

struct Dataset
{
    std::vector<Face> faces;
    std::vector<double> cellHalfLow;
    std::vector<double> cellHalfHigh;
    std::vector<double> cellValueLow;
    std::vector<double> cellValueHigh;
    std::vector<double> cellRho;
    std::vector<double> faceValues;
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

struct WorkloadCounts
{
    size_t inactive = 0;
    size_t internal = 0;
    size_t lowBoundary = 0;
    size_t highBoundary = 0;
    size_t vacuumInternal = 0;
    size_t pressureWeightedInternal = 0;
    uint64_t logicalReads = 0;
    uint64_t logicalUpdates = 0;
};

struct Options
{
    size_t faces = 65536;
    size_t cells = 4096;
    size_t window = 64;
    size_t lineEntries = 0;
    size_t continuationContexts = 0;
    size_t combinerEntries = 0;
    size_t combinerBanks = 4;
    uint64_t seed = 0x4c414e4c;
    InternalMode internalMode = InternalMode::Normal;
    BoundarySource boundarySource = BoundarySource::None;
};

struct PackedMemory
{
    std::vector<uint8_t> bytes;
    uint64_t halfLowBase = 0;
    uint64_t halfHighBase = 0;
    uint64_t valueLowBase = 0;
    uint64_t valueHighBase = 0;
    uint64_t rhoBase = 0;
    uint64_t faceValueBase = 0;
};

struct FacePlan
{
    size_t ordinal = 0;
    size_t gatherBegin = 0;
    size_t gatherCount = 0;
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
    if (options.faces == 0 || options.cells < 2) {
        throw std::invalid_argument("faces must be positive and cells >= 2");
    }
    constexpr uint64_t MaxPackedIndex = (1ULL << 31) - 1;
    if (options.faces > MaxPackedIndex || options.cells > MaxPackedIndex) {
        throw std::invalid_argument(
            "faces and cells must fit the 31-bit descriptor payload");
    }

    Dataset data;
    data.faces.resize(options.faces);
    data.cellHalfLow.resize(options.cells);
    data.cellHalfHigh.resize(options.cells);
    data.cellValueLow.resize(options.cells);
    data.cellValueHigh.resize(options.cells);
    data.cellRho.resize(options.cells);
    data.faceValues.resize(options.faces);

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
        face.faceValueOrdinal = static_cast<uint32_t>(ordinal);
        face.kind = generator() % 20 != 0
            ? FaceKind::Internal : FaceKind::Inactive;
        data.faces[ordinal] = face;
    }

    // Keep all random draws above stable so the default normal/internal
    // dataset remains byte-for-byte reproducible.  The branch inputs are
    // generated afterward and are consumed only by the requested modes.
    std::uniform_real_distribution<double> density(0.25, 3.0);
    for (size_t cell = 0; cell < options.cells; ++cell) {
        data.cellRho[cell] = cell % 4 == 0 ? 0.0 : density(generator);
    }
    for (size_t ordinal = 0; ordinal < options.faces; ++ordinal) {
        data.faceValues[ordinal] = value(generator);
    }

    if (options.boundarySource != BoundarySource::None) {
        for (size_t ordinal = 0; ordinal < options.faces; ++ordinal) {
            auto &face = data.faces[ordinal];
            if (face.kind != FaceKind::Internal) {
                continue;
            }
            if (ordinal % 11 == 3) {
                face.kind = FaceKind::LowBoundary;
            } else if (ordinal % 11 == 7) {
                face.kind = FaceKind::HighBoundary;
            }
        }
    }
    return data;
}

WorkloadCounts
countWorkload(const Dataset &data, const Options &options)
{
    WorkloadCounts counts;
    for (const auto &face : data.faces) {
        switch (face.kind) {
          case FaceKind::Inactive:
            ++counts.inactive;
            continue;
          case FaceKind::LowBoundary:
            ++counts.lowBoundary;
            counts.logicalReads += 1;
            counts.logicalUpdates += 2;
            continue;
          case FaceKind::HighBoundary:
            ++counts.highBoundary;
            counts.logicalReads += 1;
            counts.logicalUpdates += 2;
            continue;
          case FaceKind::Internal:
            ++counts.internal;
            counts.logicalUpdates += 4;
            break;
        }

        if (options.internalMode == InternalMode::Normal) {
            counts.logicalReads += 4;
            continue;
        }
        if (data.cellRho[face.low] <= 0.0 &&
            data.cellRho[face.high] <= 0.0) {
            ++counts.vacuumInternal;
            counts.logicalReads += 2;
            continue;
        }
        if (options.internalMode == InternalMode::DensityGuarded) {
            counts.logicalReads += 6;
            continue;
        }
        counts.logicalReads += 8;
        if (data.cellValueLow[face.low] *
            data.cellValueHigh[face.high] <= 0.0) {
            ++counts.pressureWeightedInternal;
        }
    }
    return counts;
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

double
pressureFaceValue(
    double highHalfLow, double highRho, double lowValueHigh,
    double lowHalfHigh, double lowRho, double highValueLow)
{
    return (highHalfLow * highRho * lowValueHigh +
            lowHalfHigh * lowRho * highValueLow) /
           (highHalfLow * highRho + lowHalfHigh * lowRho);
}

double
scalarFaceValue(
    const Dataset &data, const Face &face, InternalMode internalMode,
    BoundarySource boundarySource)
{
    switch (face.kind) {
      case FaceKind::Internal: {
        if (internalMode != InternalMode::Normal &&
            data.cellRho[face.low] <= 0.0 &&
            data.cellRho[face.high] <= 0.0) {
            return 0.0;
        }
        if (internalMode == InternalMode::PressureWeighted &&
            data.cellValueLow[face.low] *
                data.cellValueHigh[face.high] <= 0.0) {
            return pressureFaceValue(
                data.cellHalfLow[face.high], data.cellRho[face.high],
                data.cellValueHigh[face.low],
                data.cellHalfHigh[face.low], data.cellRho[face.low],
                data.cellValueLow[face.high]);
        }
        return faceValue(
            data.cellHalfLow[face.high], data.cellValueHigh[face.low],
            data.cellHalfHigh[face.low], data.cellValueLow[face.high]);
      }
      case FaceKind::LowBoundary:
        return boundarySource == BoundarySource::FaceValue
            ? data.faceValues[face.faceValueOrdinal]
            : data.cellValueHigh[face.low];
      case FaceKind::HighBoundary:
        return boundarySource == BoundarySource::FaceValue
            ? data.faceValues[face.faceValueOrdinal]
            : data.cellValueLow[face.high];
      case FaceKind::Inactive:
        break;
    }
    throw std::runtime_error("inactive face has no value");
}

void
applyFaceValue(Result &result, const Face &face, double value)
{
    if (face.kind == FaceKind::Internal ||
        face.kind == FaceKind::HighBoundary) {
        result.highMinimum[face.high] =
            std::fmin(result.highMinimum[face.high], value);
        result.highMaximum[face.high] =
            std::fmax(result.highMaximum[face.high], value);
    }
    if (face.kind == FaceKind::Internal ||
        face.kind == FaceKind::LowBoundary) {
        result.lowMinimum[face.low] =
            std::fmin(result.lowMinimum[face.low], value);
        result.lowMaximum[face.low] =
            std::fmax(result.lowMaximum[face.low], value);
    }
}

Result
runScalar(const Dataset &data, const Options &options)
{
    Result result = emptyResult(data.cellHalfLow.size());
    for (const auto &face : data.faces) {
        if (face.kind == FaceKind::Inactive) {
            continue;
        }
        const double value = scalarFaceValue(
            data, face, options.internalMode, options.boundarySource);
        if (!std::isfinite(value)) {
            throw std::runtime_error("generated nonfinite scalar face value");
        }
        applyFaceValue(result, face, value);
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
    memory.rhoBase = 4 * stride;
    memory.faceValueBase = 5 * stride;
    const size_t faceValueBytes = data.faceValues.size() * sizeof(double);
    memory.bytes.resize(
        memory.faceValueBase + roundUp(faceValueBytes, 64) + 64, 0);

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
    std::memcpy(
        memory.bytes.data() + memory.rhoBase,
        data.cellRho.data(), arrayBytes);
    std::memcpy(
        memory.bytes.data() + memory.faceValueBase,
        data.faceValues.data(), faceValueBytes);
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
runModel(
    const Dataset &data, const Options &options,
    const Configuration &configuration)
{
    if (!configuration.valid()) {
        throw std::invalid_argument("invalid window configuration");
    }
    const PackedMemory memory = packMemory(data);
    std::vector<uint64_t> addresses;
    std::vector<FacePlan> plans;
    addresses.reserve(data.faces.size() * 8);
    plans.reserve(data.faces.size());
    for (size_t ordinal = 0; ordinal < data.faces.size(); ++ordinal) {
        const auto &face = data.faces[ordinal];
        if (face.kind == FaceKind::Inactive) {
            continue;
        }
        FacePlan plan;
        plan.ordinal = ordinal;
        plan.gatherBegin = addresses.size();
        if (face.kind == FaceKind::Internal) {
            if (options.internalMode != InternalMode::Normal) {
                addresses.push_back(
                    memory.rhoBase + face.low * sizeof(double));
                addresses.push_back(
                    memory.rhoBase + face.high * sizeof(double));
            }
            const bool live = options.internalMode == InternalMode::Normal ||
                data.cellRho[face.low] > 0.0 ||
                data.cellRho[face.high] > 0.0;
            if (live &&
                options.internalMode == InternalMode::PressureWeighted) {
                addresses.push_back(
                    memory.valueLowBase + face.low * sizeof(double));
                addresses.push_back(
                    memory.valueHighBase + face.high * sizeof(double));
            }
            if (live) {
                addresses.push_back(
                    memory.halfLowBase + face.high * sizeof(double));
                addresses.push_back(
                    memory.valueHighBase + face.low * sizeof(double));
                addresses.push_back(
                    memory.halfHighBase + face.low * sizeof(double));
                addresses.push_back(
                    memory.valueLowBase + face.high * sizeof(double));
            }
        } else if (options.boundarySource == BoundarySource::FaceValue) {
            addresses.push_back(
                memory.faceValueBase +
                face.faceValueOrdinal * sizeof(double));
        } else if (face.kind == FaceKind::LowBoundary) {
            addresses.push_back(
                memory.valueHighBase + face.low * sizeof(double));
        } else {
            addresses.push_back(
                memory.valueLowBase + face.high * sizeof(double));
        }
        plan.gatherCount = addresses.size() - plan.gatherBegin;
        plans.push_back(plan);
    }

    ModelResult output;
    output.values = emptyResult(data.cellHalfLow.size());
    const auto gathered = gatherWithModel(
        addresses, memory, configuration, output.reads);

    std::vector<UpdateEvent> updates;
    updates.reserve(data.faces.size() * 4);
    const size_t cells = data.cellHalfLow.size();
    for (const auto &plan : plans) {
        const auto &face = data.faces[plan.ordinal];
        const size_t gather = plan.gatherBegin;
        double value = 0.0;
        if (face.kind == FaceKind::Internal &&
            options.internalMode == InternalMode::Normal) {
            if (plan.gatherCount != 4) {
                throw std::runtime_error("invalid normal gather plan");
            }
            value = faceValue(
                gathered[gather], gathered[gather + 1],
                gathered[gather + 2], gathered[gather + 3]);
        } else if (face.kind == FaceKind::Internal) {
            const double lowRho = gathered[gather];
            const double highRho = gathered[gather + 1];
            if (lowRho <= 0.0 && highRho <= 0.0) {
                if (plan.gatherCount != 2) {
                    throw std::runtime_error("invalid vacuum gather plan");
                }
                value = 0.0;
            } else if (options.internalMode ==
                       InternalMode::DensityGuarded) {
                if (plan.gatherCount != 6) {
                    throw std::runtime_error("invalid guarded gather plan");
                }
                value = faceValue(
                    gathered[gather + 2], gathered[gather + 3],
                    gathered[gather + 4], gathered[gather + 5]);
            } else {
                if (plan.gatherCount != 8) {
                    throw std::runtime_error("invalid pressure gather plan");
                }
                const bool weighted =
                    gathered[gather + 2] * gathered[gather + 3] <= 0.0;
                if (weighted) {
                    value = pressureFaceValue(
                        gathered[gather + 4], highRho,
                        gathered[gather + 5], gathered[gather + 6],
                        lowRho, gathered[gather + 7]);
                } else {
                    value = faceValue(
                        gathered[gather + 4], gathered[gather + 5],
                        gathered[gather + 6], gathered[gather + 7]);
                }
            }
        } else {
            if (plan.gatherCount != 1) {
                throw std::runtime_error("invalid boundary gather plan");
            }
            value = gathered[gather];
        }
        if (!std::isfinite(value)) {
            throw std::runtime_error("generated nonfinite model face value");
        }
        const uint64_t bits = toBits(value);
        if (face.kind == FaceKind::Internal ||
            face.kind == FaceKind::HighBoundary) {
            updates.push_back(UpdateEvent{
                updateAddress(0, face.high, cells), bits,
                UpdateOperation::Min});
            updates.push_back(UpdateEvent{
                updateAddress(1, face.high, cells), bits,
                UpdateOperation::Max});
        }
        if (face.kind == FaceKind::Internal ||
            face.kind == FaceKind::LowBoundary) {
            updates.push_back(UpdateEvent{
                updateAddress(2, face.low, cells), bits,
                UpdateOperation::Min});
            updates.push_back(UpdateEvent{
                updateAddress(3, face.low, cells), bits,
                UpdateOperation::Max});
        }
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

InternalMode
parseInternalMode(const std::string &value)
{
    if (value == "normal") {
        return InternalMode::Normal;
    }
    if (value == "rho-guard") {
        return InternalMode::DensityGuarded;
    }
    if (value == "pressure") {
        return InternalMode::PressureWeighted;
    }
    throw std::invalid_argument("invalid value for --internal-mode");
}

const char *
internalModeName(InternalMode mode)
{
    switch (mode) {
      case InternalMode::Normal:
        return "normal";
      case InternalMode::DensityGuarded:
        return "rho-guard";
      case InternalMode::PressureWeighted:
        return "pressure";
    }
    throw std::runtime_error("invalid internal mode");
}

BoundarySource
parseBoundarySource(const std::string &value)
{
    if (value == "none") {
        return BoundarySource::None;
    }
    if (value == "cell") {
        return BoundarySource::Cell;
    }
    if (value == "faceval") {
        return BoundarySource::FaceValue;
    }
    throw std::invalid_argument("invalid value for --boundaries");
}

const char *
boundarySourceName(BoundarySource source)
{
    switch (source) {
      case BoundarySource::None:
        return "none";
      case BoundarySource::Cell:
        return "cell";
      case BoundarySource::FaceValue:
        return "faceval";
    }
    throw std::runtime_error("invalid boundary source");
}

Options
parseOptions(int argc, char **argv)
{
    Options options;
    for (int argument = 1; argument < argc; ++argument) {
        const std::string option = argv[argument];
        if (option == "--help") {
            std::cout << "usage: eap_face_minmax [--faces N] [--cells N] "
                         "[--window N] [--line-entries N] [--contexts N] "
                         "[--combiner-entries N] [--combiner-banks N] "
                         "[--internal-mode normal|rho-guard|pressure] "
                         "[--boundaries none|cell|faceval] [--seed N]\n";
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
        } else if (option == "--line-entries") {
            options.lineEntries = parseSize(value, option);
        } else if (option == "--contexts") {
            options.continuationContexts = parseSize(value, option);
        } else if (option == "--combiner-entries") {
            options.combinerEntries = parseSize(value, option);
        } else if (option == "--combiner-banks") {
            options.combinerBanks = parseSize(value, option);
        } else if (option == "--internal-mode") {
            options.internalMode = parseInternalMode(value);
        } else if (option == "--boundaries") {
            options.boundarySource = parseBoundarySource(value);
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
        const Configuration configuration = configurationFor(options);
        const Dataset data = makeDataset(options);
        const Result scalar = runScalar(data, options);
        const ModelResult model = runModel(data, options, configuration);
        const WorkloadCounts counts = countWorkload(data, options);
        const bool correct = equalResults(scalar, model.values) &&
            model.reads.logicalMemoryAccesses == counts.logicalReads &&
            model.updates.logicalUpdatesAdmitted == counts.logicalUpdates;
        const size_t active = data.faces.size() - counts.inactive;

        std::cout << "verification=" << (correct ? "PASS" : "FAIL") << '\n';
        std::cout << "faces=" << options.faces << '\n';
        std::cout << "active_faces=" << active << '\n';
        std::cout << "internal_faces=" << counts.internal << '\n';
        std::cout << "low_boundary_faces=" << counts.lowBoundary << '\n';
        std::cout << "high_boundary_faces=" << counts.highBoundary << '\n';
        std::cout << "inactive_faces=" << counts.inactive << '\n';
        std::cout << "vacuum_internal_faces="
                  << counts.vacuumInternal << '\n';
        std::cout << "pressure_weighted_internal_faces="
                  << counts.pressureWeightedInternal << '\n';
        std::cout << "internal_mode="
                  << internalModeName(options.internalMode) << '\n';
        std::cout << "boundary_source="
                  << boundarySourceName(options.boundarySource) << '\n';
        std::cout << "cells=" << options.cells << '\n';
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
        std::cout << "expected_read_logical_accesses="
                  << counts.logicalReads << '\n';
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
        std::cout << "expected_update_logical="
                  << counts.logicalUpdates << '\n';
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
