#ifndef __MEM_LANLMAA_AMG_SPARSE_VIEW_LEASE_MODEL_HH__
#define __MEM_LANLMAA_AMG_SPARSE_VIEW_LEASE_MODEL_HH__

#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <vector>

namespace gem5
{
namespace lanlmaa
{

constexpr uint32_t AmgSparseViewLeaseEntries = 16;
constexpr uint64_t AmgSparseViewTokenSlotMask = 0xff;
constexpr uint64_t AmgSparseViewMaximumGeneration =
    std::numeric_limits<uint64_t>::max() >> 8;

struct AmgSparseViewDescriptor
{
    uint64_t rowOffsetsBase = 0;
    uint64_t columnsBase = 0;
    uint64_t valuesBase = 0;
    uint32_t rows = 0;
    uint32_t columns = 0;
    uint32_t nonzeros = 0;
};

struct AmgSparseViewInput
{
    std::vector<int32_t> rowOffsets;
    std::vector<int32_t> columnIndices;
    std::vector<double> values;
};

enum class AmgSparseViewError : uint8_t
{
    None = 0,
    Empty,
    SourceExtent,
    Misaligned,
    RangeOverflow,
    OverlappingRanges,
    BadRowOffset,
    BadColumnIndex,
    NonfiniteValue,
    CounterOverflow,
    TableFull,
    InvalidToken,
    ShapeMismatch,
    LeaseBusy
};

enum class AmgSparseViewState : uint8_t
{
    Free = 0,
    Ready,
    InUse,
    Revoking
};

struct AmgSparseViewEconomics
{
    uint64_t registrationLogicalBytes = 0;
    uint64_t logicalBytesEliminatedPerUse = 0;
    uint64_t minimumUsesToAmortize = 0;
};

struct AmgSparseViewRegistrationCounters
{
    uint64_t rowOffsetReads = 0;
    uint64_t columnIndexReads = 0;
    uint64_t valueReads = 0;
};

struct AmgSparseViewRegistrationResult
{
    AmgSparseViewError error = AmgSparseViewError::None;
    uint64_t token = 0;
    AmgSparseViewEconomics economics;
    AmgSparseViewRegistrationCounters counters;

    explicit operator bool() const
    {
        return error == AmgSparseViewError::None;
    }
};

struct AmgSparseViewTransitionResult
{
    AmgSparseViewError error = AmgSparseViewError::None;
    AmgSparseViewState state = AmgSparseViewState::Free;

    explicit operator bool() const
    {
        return error == AmgSparseViewError::None;
    }
};

struct AmgSparseViewWriteResult
{
    AmgSparseViewError error = AmgSparseViewError::None;
    uint32_t leasesInvalidated = 0;
    uint32_t leasesDraining = 0;
    bool mayProceed = false;

    explicit operator bool() const
    {
        return error == AmgSparseViewError::None;
    }
};

class AmgSparseViewLeaseModel
{
  private:
    struct Entry
    {
        AmgSparseViewDescriptor descriptor;
        uint64_t generation = 0;
        AmgSparseViewState state = AmgSparseViewState::Free;
    };

    std::array<Entry, AmgSparseViewLeaseEntries> entries{};

    static bool
    checkedRange(uint64_t base, uint64_t count, uint64_t elementBytes,
                 uint64_t &end)
    {
        if (count > std::numeric_limits<uint64_t>::max() / elementBytes) {
            return false;
        }
        const uint64_t bytes = count * elementBytes;
        if (base > std::numeric_limits<uint64_t>::max() - bytes) {
            return false;
        }
        end = base + bytes;
        return true;
    }

    static bool
    rangesOverlap(uint64_t firstBegin, uint64_t firstEnd,
                  uint64_t secondBegin, uint64_t secondEnd)
    {
        return firstBegin < secondEnd && secondBegin < firstEnd;
    }

    static bool
    sameDescriptor(const AmgSparseViewDescriptor &first,
                   const AmgSparseViewDescriptor &second)
    {
        return first.rowOffsetsBase == second.rowOffsetsBase &&
            first.columnsBase == second.columnsBase &&
            first.valuesBase == second.valuesBase &&
            first.rows == second.rows &&
            first.columns == second.columns &&
            first.nonzeros == second.nonzeros;
    }

    static uint64_t
    encodeToken(size_t slot, uint64_t generation)
    {
        return (generation << 8) | (slot + 1);
    }

    Entry *
    entryForToken(uint64_t token)
    {
        const uint64_t encodedSlot = token & AmgSparseViewTokenSlotMask;
        const uint64_t generation = token >> 8;
        if (encodedSlot == 0 || encodedSlot > entries.size() ||
            generation == 0) {
            return nullptr;
        }
        Entry &entry = entries[encodedSlot - 1];
        if (entry.state == AmgSparseViewState::Free ||
            entry.generation != generation) {
            return nullptr;
        }
        return &entry;
    }

    static bool
    checkedEconomics(const AmgSparseViewDescriptor &descriptor,
                     AmgSparseViewEconomics &economics)
    {
        const uint64_t rowBytes =
            (static_cast<uint64_t>(descriptor.rows) + 1) * 4;
        if (descriptor.nonzeros >
            (std::numeric_limits<uint64_t>::max() - rowBytes) / 12) {
            return false;
        }
        economics.registrationLogicalBytes =
            rowBytes + descriptor.nonzeros * 12;
        if (descriptor.nonzeros >
            std::numeric_limits<uint64_t>::max() / 8 - descriptor.rows) {
            return false;
        }
        economics.logicalBytesEliminatedPerUse =
            (descriptor.nonzeros + descriptor.rows) * 8;
        if (economics.logicalBytesEliminatedPerUse == 0) {
            return false;
        }
        economics.minimumUsesToAmortize =
            economics.registrationLogicalBytes /
                economics.logicalBytesEliminatedPerUse +
            (economics.registrationLogicalBytes %
                 economics.logicalBytesEliminatedPerUse !=
             0);
        return true;
    }

    static bool
    descriptorRanges(const AmgSparseViewDescriptor &descriptor,
                     std::array<uint64_t, 3> &ends)
    {
        return checkedRange(descriptor.rowOffsetsBase,
                            static_cast<uint64_t>(descriptor.rows) + 1,
                            sizeof(int32_t), ends[0]) &&
            checkedRange(descriptor.columnsBase, descriptor.nonzeros,
                         sizeof(int32_t), ends[1]) &&
            checkedRange(descriptor.valuesBase, descriptor.nonzeros,
                         sizeof(double), ends[2]);
    }

  public:
    AmgSparseViewRegistrationResult
    registerView(const AmgSparseViewDescriptor &descriptor,
                 const AmgSparseViewInput &input)
    {
        AmgSparseViewRegistrationResult result;
        size_t freeSlot = entries.size();
        if (descriptor.rows == 0 || descriptor.columns == 0 ||
            descriptor.nonzeros == 0) {
            result.error = AmgSparseViewError::Empty;
            return result;
        }
        if (input.rowOffsets.size() !=
                static_cast<size_t>(descriptor.rows) + 1 ||
            input.columnIndices.size() != descriptor.nonzeros ||
            input.values.size() != descriptor.nonzeros) {
            result.error = AmgSparseViewError::SourceExtent;
            return result;
        }
        if (descriptor.rowOffsetsBase % alignof(int32_t) != 0 ||
            descriptor.columnsBase % alignof(int32_t) != 0 ||
            descriptor.valuesBase % alignof(double) != 0) {
            result.error = AmgSparseViewError::Misaligned;
            return result;
        }
        std::array<uint64_t, 3> ends{};
        if (!descriptorRanges(descriptor, ends)) {
            result.error = AmgSparseViewError::RangeOverflow;
            return result;
        }
        if (rangesOverlap(descriptor.rowOffsetsBase, ends[0],
                          descriptor.columnsBase, ends[1]) ||
            rangesOverlap(descriptor.rowOffsetsBase, ends[0],
                          descriptor.valuesBase, ends[2]) ||
            rangesOverlap(descriptor.columnsBase, ends[1],
                          descriptor.valuesBase, ends[2])) {
            result.error = AmgSparseViewError::OverlappingRanges;
            return result;
        }
        if (!checkedEconomics(descriptor, result.economics)) {
            result.error = AmgSparseViewError::CounterOverflow;
            return result;
        }
        for (size_t slot = 0; slot < entries.size(); ++slot) {
            if (entries[slot].state == AmgSparseViewState::Free &&
                entries[slot].generation <
                    AmgSparseViewMaximumGeneration) {
                freeSlot = slot;
                break;
            }
        }
        if (freeSlot == entries.size()) {
            result.error = AmgSparseViewError::TableFull;
            return result;
        }
        ++result.counters.rowOffsetReads;
        if (input.rowOffsets.front() != 0 || input.rowOffsets.back() < 0 ||
            static_cast<uint32_t>(input.rowOffsets.back()) !=
                descriptor.nonzeros) {
            result.error = AmgSparseViewError::BadRowOffset;
            return result;
        }
        for (uint32_t row = 0; row < descriptor.rows; ++row) {
            ++result.counters.rowOffsetReads;
            if (input.rowOffsets[row] < 0 ||
                input.rowOffsets[row + 1] < input.rowOffsets[row] ||
                static_cast<uint32_t>(input.rowOffsets[row + 1]) >
                    descriptor.nonzeros) {
                result.error = AmgSparseViewError::BadRowOffset;
                return result;
            }
        }
        for (uint32_t nonzero = 0; nonzero < descriptor.nonzeros;
             ++nonzero) {
            ++result.counters.columnIndexReads;
            ++result.counters.valueReads;
            const int32_t column = input.columnIndices[nonzero];
            if (column < 0 ||
                static_cast<uint32_t>(column) >= descriptor.columns) {
                result.error = AmgSparseViewError::BadColumnIndex;
                return result;
            }
            if (!std::isfinite(input.values[nonzero])) {
                result.error = AmgSparseViewError::NonfiniteValue;
                return result;
            }
        }

        Entry &entry = entries[freeSlot];
        ++entry.generation;
        entry.descriptor = descriptor;
        entry.state = AmgSparseViewState::Ready;
        result.token = encodeToken(freeSlot, entry.generation);
        return result;
    }

    AmgSparseViewTransitionResult
    beginUse(uint64_t token, const AmgSparseViewDescriptor &descriptor)
    {
        AmgSparseViewTransitionResult result;
        Entry *entry = entryForToken(token);
        if (!entry) {
            result.error = AmgSparseViewError::InvalidToken;
            return result;
        }
        result.state = entry->state;
        if (!sameDescriptor(entry->descriptor, descriptor)) {
            result.error = AmgSparseViewError::ShapeMismatch;
            return result;
        }
        if (entry->state != AmgSparseViewState::Ready) {
            result.error = AmgSparseViewError::LeaseBusy;
            return result;
        }
        entry->state = AmgSparseViewState::InUse;
        result.state = entry->state;
        return result;
    }

    AmgSparseViewTransitionResult
    endUse(uint64_t token)
    {
        AmgSparseViewTransitionResult result;
        Entry *entry = entryForToken(token);
        if (!entry) {
            result.error = AmgSparseViewError::InvalidToken;
            return result;
        }
        if (entry->state == AmgSparseViewState::InUse) {
            entry->state = AmgSparseViewState::Ready;
        } else if (entry->state == AmgSparseViewState::Revoking) {
            entry->state = AmgSparseViewState::Free;
        } else {
            result.error = AmgSparseViewError::LeaseBusy;
        }
        result.state = entry->state;
        return result;
    }

    AmgSparseViewTransitionResult
    release(uint64_t token)
    {
        AmgSparseViewTransitionResult result;
        Entry *entry = entryForToken(token);
        if (!entry) {
            result.error = AmgSparseViewError::InvalidToken;
            return result;
        }
        if (entry->state == AmgSparseViewState::Ready) {
            entry->state = AmgSparseViewState::Free;
        } else if (entry->state == AmgSparseViewState::InUse) {
            entry->state = AmgSparseViewState::Revoking;
        }
        result.state = entry->state;
        return result;
    }

    AmgSparseViewWriteResult
    requestWrite(uint64_t begin, uint64_t bytes)
    {
        AmgSparseViewWriteResult result;
        if (bytes == 0) {
            result.mayProceed = true;
            return result;
        }
        if (begin > std::numeric_limits<uint64_t>::max() - bytes) {
            result.error = AmgSparseViewError::RangeOverflow;
            return result;
        }
        const uint64_t end = begin + bytes;
        for (Entry &entry : entries) {
            if (entry.state == AmgSparseViewState::Free) {
                continue;
            }
            std::array<uint64_t, 3> ends{};
            if (!descriptorRanges(entry.descriptor, ends)) {
                result.error = AmgSparseViewError::RangeOverflow;
                return result;
            }
            const bool overlap = rangesOverlap(
                    begin, end, entry.descriptor.rowOffsetsBase, ends[0]) ||
                rangesOverlap(
                    begin, end, entry.descriptor.columnsBase, ends[1]) ||
                rangesOverlap(
                    begin, end, entry.descriptor.valuesBase, ends[2]);
            if (!overlap) {
                continue;
            }
            if (entry.state == AmgSparseViewState::Ready) {
                entry.state = AmgSparseViewState::Free;
                ++result.leasesInvalidated;
            } else {
                entry.state = AmgSparseViewState::Revoking;
                ++result.leasesDraining;
            }
        }
        result.mayProceed = result.leasesDraining == 0;
        return result;
    }

    AmgSparseViewState
    state(uint64_t token)
    {
        const Entry *entry = entryForToken(token);
        return entry ? entry->state : AmgSparseViewState::Free;
    }
};

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_AMG_SPARSE_VIEW_LEASE_MODEL_HH__
