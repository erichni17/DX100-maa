#ifndef __MEM_LANLMAA_REFERENCE_MODEL_HH__
#define __MEM_LANLMAA_REFERENCE_MODEL_HH__

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <deque>
#include <limits>
#include <optional>
#include <unordered_map>
#include <vector>

namespace gem5
{
namespace lanlmaa
{

enum class Admission
{
    Accepted,
    WouldBlock,
    Invalid
};

struct Configuration
{
    size_t operationEntries = 64;
    size_t lineEntries = 32;
    size_t continuationContexts = 16;
    size_t combinerEntries = 32;
    size_t combinerBanks = 1;
    size_t acknowledgementCredits = 32;
    size_t lineBytes = 64;

    bool valid() const
    {
        return operationEntries > 0 && lineEntries > 0 &&
               continuationContexts > 0 && combinerEntries > 0 &&
               combinerBanks > 0 && combinerBanks <= combinerEntries &&
               (combinerBanks & (combinerBanks - 1)) == 0 &&
               combinerEntries % combinerBanks == 0 &&
               acknowledgementCredits > 0 && lineBytes == 64;
    }
};

struct ReadCounters
{
    uint64_t logicalItemsAdmitted = 0;
    uint64_t logicalMemoryAccesses = 0;
    uint64_t physicalLineReads = 0;
    uint64_t duplicateElementHits = 0;
    uint64_t lineMergeHits = 0;
    uint64_t operationWouldBlock = 0;
    uint64_t lineWouldBlock = 0;
    uint64_t contextWouldBlock = 0;
    uint64_t continuationSteps = 0;
    uint64_t continuationStalls = 0;
    uint64_t responsesFannedOut = 0;
    uint64_t completionsRetired = 0;
    uint64_t invalidAdmissions = 0;
    uint64_t activeContextHighWater = 0;
};

struct LineRequest
{
    uint64_t lineAddress = 0;
    uint64_t requestedByteMask = 0;
};

struct ReadCompletion
{
    uint64_t logicalTag = 0;
    uint64_t value = 0;
};

class ReadContinuationModel
{
  private:
    enum class OperationState
    {
        DataPending,
        ResponseReady,
        RetireReady
    };

    enum class LineState
    {
        Free,
        Allocated,
        Requested
    };

    struct Operation
    {
        uint64_t tag = 0;
        uint64_t address = 0;
        uint8_t width = 0;
        uint64_t value = 0;
        bool continuation = false;
        OperationState state = OperationState::DataPending;
    };

    struct LineEntry
    {
        LineState state = LineState::Free;
        uint64_t lineAddress = 0;
        uint64_t requestedByteMask = 0;
        std::vector<uint64_t> waiters;

        void clear()
        {
            state = LineState::Free;
            lineAddress = 0;
            requestedByteMask = 0;
            waiters.clear();
        }
    };

    Configuration configuration;
    ReadCounters counterValues;
    std::unordered_map<uint64_t, Operation> operations;
    std::deque<uint64_t> admissionOrder;
    std::vector<LineEntry> lines;
    size_t activeContexts = 0;
    std::optional<uint64_t> lastAdmittedTag;

    static bool validWidth(uint8_t width)
    {
        return width == 1 || width == 2 || width == 4 || width == 8;
    }

    bool validAccess(uint64_t address, uint8_t width) const
    {
        if (!validWidth(width)) {
            return false;
        }
        const uint64_t offset = address & (configuration.lineBytes - 1);
        return offset + width <= configuration.lineBytes;
    }

    uint64_t lineAddress(uint64_t address) const
    {
        return address & ~(static_cast<uint64_t>(configuration.lineBytes) - 1);
    }

    static uint64_t byteMask(uint64_t offset, uint8_t width)
    {
        const uint64_t widthMask = (uint64_t{1} << width) - 1;
        return widthMask << offset;
    }

    LineEntry *findLine(uint64_t address)
    {
        for (auto &line : lines) {
            if (line.state != LineState::Free &&
                line.lineAddress == address) {
                return &line;
            }
        }
        return nullptr;
    }

    LineEntry *findFreeLine()
    {
        for (auto &line : lines) {
            if (line.state == LineState::Free) {
                return &line;
            }
        }
        return nullptr;
    }

    Admission bindToLine(Operation &operation, bool isContinuation)
    {
        const uint64_t base = lineAddress(operation.address);
        LineEntry *line = findLine(base);
        if (line && line->state == LineState::Requested) {
            const uint64_t requested = byteMask(
                operation.address - base, operation.width);
            if ((requested & ~line->requestedByteMask) != 0) {
                line = nullptr;
            }
        }

        const bool merged = line != nullptr;
        if (!line) {
            line = findFreeLine();
            if (!line) {
                ++counterValues.lineWouldBlock;
                if (isContinuation) {
                    ++counterValues.continuationStalls;
                }
                return Admission::WouldBlock;
            }
            line->state = LineState::Allocated;
            line->lineAddress = base;
        }

        if (merged) {
            ++counterValues.lineMergeHits;
            for (const auto waiter : line->waiters) {
                const auto &prior = operations.at(waiter);
                if (prior.address == operation.address &&
                    prior.width == operation.width) {
                    ++counterValues.duplicateElementHits;
                    break;
                }
            }
        }

        line->requestedByteMask |= byteMask(
            operation.address - base, operation.width);
        line->waiters.push_back(operation.tag);
        operation.state = OperationState::DataPending;
        ++counterValues.logicalMemoryAccesses;
        return Admission::Accepted;
    }

    static uint64_t extractValue(
        const std::vector<uint8_t> &data, uint64_t offset, uint8_t width)
    {
        uint64_t value = 0;
        for (uint8_t byte = 0; byte < width; ++byte) {
            value |= static_cast<uint64_t>(data[offset + byte]) << (8 * byte);
        }
        return value;
    }

  public:
    explicit ReadContinuationModel(const Configuration &config = {})
        : configuration(config), lines(config.lineEntries)
    {
    }

    bool valid() const { return configuration.valid(); }

    const ReadCounters &counters() const { return counterValues; }

    size_t outstandingOperations() const { return operations.size(); }

    size_t outstandingContexts() const { return activeContexts; }

    Admission admitRead(
        uint64_t tag, uint64_t address, uint8_t width,
        bool needsContinuation = false)
    {
        if (!valid() || !validAccess(address, width) ||
            operations.count(tag) != 0 ||
            (lastAdmittedTag && tag <= *lastAdmittedTag)) {
            ++counterValues.invalidAdmissions;
            return Admission::Invalid;
        }
        if (operations.size() == configuration.operationEntries) {
            ++counterValues.operationWouldBlock;
            return Admission::WouldBlock;
        }
        if (needsContinuation &&
            activeContexts == configuration.continuationContexts) {
            ++counterValues.contextWouldBlock;
            return Admission::WouldBlock;
        }

        Operation operation;
        operation.tag = tag;
        operation.address = address;
        operation.width = width;
        operation.continuation = needsContinuation;
        auto inserted = operations.emplace(tag, operation);
        Admission result = bindToLine(inserted.first->second, false);
        if (result != Admission::Accepted) {
            operations.erase(inserted.first);
            return result;
        }

        admissionOrder.push_back(tag);
        lastAdmittedTag = tag;
        ++counterValues.logicalItemsAdmitted;
        if (needsContinuation) {
            ++activeContexts;
            counterValues.activeContextHighWater = std::max(
                counterValues.activeContextHighWater,
                static_cast<uint64_t>(activeContexts));
        }
        return Admission::Accepted;
    }

    std::optional<LineRequest> nextLineRequest()
    {
        for (auto &line : lines) {
            if (line.state == LineState::Allocated) {
                line.state = LineState::Requested;
                ++counterValues.physicalLineReads;
                return LineRequest{
                    line.lineAddress, line.requestedByteMask};
            }
        }
        return std::nullopt;
    }

    bool returnLine(uint64_t address, const std::vector<uint8_t> &data)
    {
        LineEntry *line = findLine(address);
        if (!line || line->state != LineState::Requested ||
            data.size() != configuration.lineBytes) {
            return false;
        }

        for (const auto tag : line->waiters) {
            auto operation = operations.find(tag);
            if (operation == operations.end() ||
                operation->second.state != OperationState::DataPending) {
                return false;
            }
            auto &op = operation->second;
            op.value = extractValue(
                data, op.address - line->lineAddress, op.width);
            op.state = op.continuation ? OperationState::ResponseReady
                                       : OperationState::RetireReady;
            ++counterValues.responsesFannedOut;
        }
        line->clear();
        return true;
    }

    std::optional<uint64_t> continuationValue(uint64_t tag) const
    {
        const auto operation = operations.find(tag);
        if (operation == operations.end() ||
            !operation->second.continuation ||
            operation->second.state != OperationState::ResponseReady) {
            return std::nullopt;
        }
        return operation->second.value;
    }

    Admission reissueContinuation(
        uint64_t tag, uint64_t address, uint8_t width)
    {
        auto operation = operations.find(tag);
        if (!validAccess(address, width) || operation == operations.end() ||
            !operation->second.continuation ||
            operation->second.state != OperationState::ResponseReady) {
            ++counterValues.invalidAdmissions;
            return Admission::Invalid;
        }

        const uint64_t priorAddress = operation->second.address;
        const uint8_t priorWidth = operation->second.width;
        operation->second.address = address;
        operation->second.width = width;
        Admission result = bindToLine(operation->second, true);
        if (result != Admission::Accepted) {
            operation->second.address = priorAddress;
            operation->second.width = priorWidth;
            operation->second.state = OperationState::ResponseReady;
            return result;
        }
        ++counterValues.continuationSteps;
        return Admission::Accepted;
    }

    bool finishContinuation(uint64_t tag)
    {
        auto operation = operations.find(tag);
        if (operation == operations.end() ||
            !operation->second.continuation ||
            operation->second.state != OperationState::ResponseReady) {
            return false;
        }
        operation->second.state = OperationState::RetireReady;
        return true;
    }

    std::optional<ReadCompletion> popRetired()
    {
        if (admissionOrder.empty()) {
            return std::nullopt;
        }
        const uint64_t tag = admissionOrder.front();
        auto operation = operations.find(tag);
        if (operation == operations.end() ||
            operation->second.state != OperationState::RetireReady) {
            return std::nullopt;
        }

        ReadCompletion completion{tag, operation->second.value};
        if (operation->second.continuation) {
            --activeContexts;
        }
        operations.erase(operation);
        admissionOrder.pop_front();
        ++counterValues.completionsRetired;
        return completion;
    }
};

enum class DataType
{
    Uint64,
    Int64,
    Float64
};

enum class UpdateOperation
{
    Overwrite,
    Add,
    Min,
    Max
};

enum class Ordering
{
    Strict,
    Relaxed
};

enum class OverflowPolicy
{
    Wrap,
    Saturate,
    Fault
};

struct UpdateCounters
{
    uint64_t logicalUpdatesAdmitted = 0;
    uint64_t logicalUpdatesCompleted = 0;
    uint64_t updateConflicts = 0;
    uint64_t combinerHits = 0;
    uint64_t strictOrderSerializations = 0;
    uint64_t combinerWouldBlock = 0;
    uint64_t combinerBankWouldBlock = 0;
    uint64_t acknowledgementWouldBlock = 0;
    uint64_t drains = 0;
    uint64_t acknowledgements = 0;
    uint64_t invalidAdmissions = 0;
};

struct UpdateDrain
{
    uint64_t drainId = 0;
    uint64_t firstLogicalTag = 0;
    uint64_t address = 0;
    uint64_t valueBits = 0;
    uint64_t participants = 0;
    DataType dataType = DataType::Uint64;
    UpdateOperation operation = UpdateOperation::Overwrite;
    Ordering ordering = Ordering::Strict;
    OverflowPolicy overflow = OverflowPolicy::Wrap;
};

class UpdateCombinerModel
{
  private:
    enum class EntryState
    {
        Free,
        Accumulating,
        Draining
    };

    struct Entry
    {
        EntryState state = EntryState::Free;
        UpdateDrain drain;
    };

    Configuration configuration;
    UpdateCounters counterValues;
    std::vector<Entry> entries;
    size_t acknowledgementsInUse = 0;
    uint64_t nextDrainId = 1;
    std::optional<uint64_t> lastAdmittedTag;

    static bool validDataType(DataType type)
    {
        switch (type) {
          case DataType::Uint64:
          case DataType::Int64:
          case DataType::Float64:
            return true;
        }
        return false;
    }

    static bool validOperation(UpdateOperation operation)
    {
        switch (operation) {
          case UpdateOperation::Overwrite:
          case UpdateOperation::Add:
          case UpdateOperation::Min:
          case UpdateOperation::Max:
            return true;
        }
        return false;
    }

    static bool validOrdering(Ordering ordering)
    {
        switch (ordering) {
          case Ordering::Strict:
          case Ordering::Relaxed:
            return true;
        }
        return false;
    }

    static bool validOverflow(OverflowPolicy overflow)
    {
        switch (overflow) {
          case OverflowPolicy::Wrap:
          case OverflowPolicy::Saturate:
          case OverflowPolicy::Fault:
            return true;
        }
        return false;
    }

    template <class T>
    static T fromBits(uint64_t bits)
    {
        static_assert(sizeof(T) == sizeof(bits), "unexpected 64-bit type");
        T value;
        std::memcpy(&value, &bits, sizeof(value));
        return value;
    }

    template <class T>
    static uint64_t toBits(T value)
    {
        static_assert(sizeof(T) == sizeof(uint64_t), "unexpected 64-bit type");
        uint64_t bits;
        std::memcpy(&bits, &value, sizeof(bits));
        return bits;
    }

    static std::optional<uint64_t> addInteger(
        uint64_t left, uint64_t right, DataType type,
        OverflowPolicy overflow)
    {
        if (type == DataType::Uint64) {
            if (overflow == OverflowPolicy::Wrap) {
                return left + right;
            }
            const bool over = right >
                std::numeric_limits<uint64_t>::max() - left;
            if (over && overflow == OverflowPolicy::Fault) {
                return std::nullopt;
            }
            if (over) {
                return std::numeric_limits<uint64_t>::max();
            }
            return left + right;
        }

        const int64_t lhs = fromBits<int64_t>(left);
        const int64_t rhs = fromBits<int64_t>(right);
        if (overflow == OverflowPolicy::Wrap) {
            return left + right;
        }
        const bool positive = rhs > 0 &&
            lhs > std::numeric_limits<int64_t>::max() - rhs;
        const bool negative = rhs < 0 &&
            lhs < std::numeric_limits<int64_t>::min() - rhs;
        if ((positive || negative) && overflow == OverflowPolicy::Fault) {
            return std::nullopt;
        }
        if (positive) {
            return toBits(std::numeric_limits<int64_t>::max());
        }
        if (negative) {
            return toBits(std::numeric_limits<int64_t>::min());
        }
        return toBits(lhs + rhs);
    }

    static std::optional<uint64_t> combine(
        uint64_t left, uint64_t right, DataType type,
        UpdateOperation operation, OverflowPolicy overflow)
    {
        if (operation == UpdateOperation::Overwrite) {
            return std::nullopt;
        }
        if (operation == UpdateOperation::Add) {
            if (type == DataType::Float64) {
                const double sum =
                    fromBits<double>(left) + fromBits<double>(right);
                return toBits(sum);
            }
            return addInteger(left, right, type, overflow);
        }
        if (type == DataType::Float64) {
            const double lhs = fromBits<double>(left);
            const double rhs = fromBits<double>(right);
            return toBits(operation == UpdateOperation::Min
                              ? std::fmin(lhs, rhs)
                              : std::fmax(lhs, rhs));
        }
        if (type == DataType::Int64) {
            const int64_t lhs = fromBits<int64_t>(left);
            const int64_t rhs = fromBits<int64_t>(right);
            return toBits(operation == UpdateOperation::Min
                              ? std::min(lhs, rhs)
                              : std::max(lhs, rhs));
        }
        return operation == UpdateOperation::Min ? std::min(left, right)
                                                 : std::max(left, right);
    }

    bool hasOlderSameAddress(const Entry &candidate) const
    {
        for (const auto &entry : entries) {
            if (entry.state != EntryState::Free &&
                entry.drain.address == candidate.drain.address &&
                entry.drain.firstLogicalTag <
                    candidate.drain.firstLogicalTag) {
                return true;
            }
        }
        return false;
    }

    size_t bankFor(uint64_t address) const
    {
        const uint64_t wordAddress = address >> 3;
        return wordAddress & (configuration.combinerBanks - 1);
    }

    size_t firstEntryInBank(size_t bank) const
    {
        return bank * (entries.size() / configuration.combinerBanks);
    }

    size_t pastLastEntryInBank(size_t bank) const
    {
        return firstEntryInBank(bank + 1);
    }

  public:
    explicit UpdateCombinerModel(const Configuration &config = {})
        : configuration(config), entries(config.combinerEntries)
    {
    }

    bool valid() const { return configuration.valid(); }

    const UpdateCounters &counters() const { return counterValues; }

    size_t outstandingEntries() const
    {
        return std::count_if(
            entries.begin(), entries.end(), [](const Entry &entry) {
                return entry.state != EntryState::Free;
            });
    }

    Admission admitUpdate(
        uint64_t tag, uint64_t address, uint64_t valueBits,
        DataType dataType, UpdateOperation operation, Ordering ordering,
        OverflowPolicy overflow = OverflowPolicy::Wrap)
    {
        if (!valid() || (address & 7) != 0 ||
            !validDataType(dataType) || !validOperation(operation) ||
            !validOrdering(ordering) || !validOverflow(overflow) ||
            (operation == UpdateOperation::Overwrite &&
             ordering != Ordering::Strict) ||
            (lastAdmittedTag && tag <= *lastAdmittedTag)) {
            ++counterValues.invalidAdmissions;
            return Admission::Invalid;
        }

        const size_t bank = bankFor(address);
        const size_t first = firstEntryInBank(bank);
        const size_t pastLast = pastLastEntryInBank(bank);

        bool conflict = false;
        for (size_t index = first; index < pastLast; ++index) {
            const auto &entry = entries[index];
            if (entry.state != EntryState::Free &&
                entry.drain.address == address) {
                conflict = true;
                break;
            }
        }
        if (conflict) {
            ++counterValues.updateConflicts;
        }

        if (ordering == Ordering::Relaxed &&
            operation != UpdateOperation::Overwrite) {
            for (size_t index = first; index < pastLast; ++index) {
                auto &entry = entries[index];
                auto &drain = entry.drain;
                if (entry.state != EntryState::Accumulating ||
                    drain.address != address || drain.dataType != dataType ||
                    drain.operation != operation ||
                    drain.ordering != ordering || drain.overflow != overflow) {
                    continue;
                }
                auto combined = combine(
                    drain.valueBits, valueBits, dataType, operation, overflow);
                if (!combined) {
                    ++counterValues.invalidAdmissions;
                    return Admission::Invalid;
                }
                drain.valueBits = *combined;
                ++drain.participants;
                ++counterValues.combinerHits;
                ++counterValues.logicalUpdatesAdmitted;
                lastAdmittedTag = tag;
                return Admission::Accepted;
            }
        }

        for (size_t index = first; index < pastLast; ++index) {
            auto &entry = entries[index];
            if (entry.state == EntryState::Free) {
                entry.state = EntryState::Accumulating;
                entry.drain = UpdateDrain{
                    nextDrainId++, tag, address, valueBits, 1,
                    dataType, operation, ordering, overflow};
                ++counterValues.logicalUpdatesAdmitted;
                if (ordering == Ordering::Strict && conflict) {
                    ++counterValues.strictOrderSerializations;
                }
                lastAdmittedTag = tag;
                return Admission::Accepted;
            }
        }

        ++counterValues.combinerWouldBlock;
        const bool freeOutsideBank = std::any_of(
            entries.begin(), entries.end(), [](const Entry &entry) {
                return entry.state == EntryState::Free;
            });
        if (freeOutsideBank) {
            ++counterValues.combinerBankWouldBlock;
        }
        return Admission::WouldBlock;
    }

    std::optional<UpdateDrain> drainNext()
    {
        if (acknowledgementsInUse == configuration.acknowledgementCredits) {
            ++counterValues.acknowledgementWouldBlock;
            return std::nullopt;
        }

        Entry *selected = nullptr;
        for (auto &entry : entries) {
            if (entry.state != EntryState::Accumulating ||
                hasOlderSameAddress(entry)) {
                continue;
            }
            if (!selected || entry.drain.firstLogicalTag <
                                 selected->drain.firstLogicalTag) {
                selected = &entry;
            }
        }
        if (!selected) {
            return std::nullopt;
        }
        selected->state = EntryState::Draining;
        ++acknowledgementsInUse;
        ++counterValues.drains;
        return selected->drain;
    }

    bool acknowledge(uint64_t drainId)
    {
        for (auto &entry : entries) {
            if (entry.state == EntryState::Draining &&
                entry.drain.drainId == drainId) {
                counterValues.logicalUpdatesCompleted +=
                    entry.drain.participants;
                ++counterValues.acknowledgements;
                --acknowledgementsInUse;
                entry = Entry{};
                return true;
            }
        }
        return false;
    }
};

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_REFERENCE_MODEL_HH__
