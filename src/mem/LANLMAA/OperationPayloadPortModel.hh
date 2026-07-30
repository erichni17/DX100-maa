#ifndef __MEM_LANLMAA_OPERATION_PAYLOAD_PORT_MODEL_HH__
#define __MEM_LANLMAA_OPERATION_PAYLOAD_PORT_MODEL_HH__

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <limits>
#include <vector>

namespace gem5
{
namespace lanlmaa
{

/**
 * Timing-only model of the selected operation-payload overlay.
 *
 * The physical design has four one-read/write banks.  Completed results are
 * written back through two held completion lanes and ordered retirement reads
 * at most two distinct banks.  Retirement reads win a same-bank collision.
 * Allocation is a metadata commit after the upstream payload initialization
 * has acknowledged, so it consumes no modeled bank port here.
 */
class OperationPayloadPortModel
{
  public:
    struct CycleResult
    {
        bool valid = false;
        size_t retirementReads = 0;
        size_t completionWrites = 0;
        bool completionBankConflict = false;
        bool completionReadConflict = false;
        bool completionWouldBlock = false;
        size_t completionQueueDepth = 0;
    };

    OperationPayloadPortModel(
        size_t entries, size_t banks, size_t completionWidth,
        size_t retirementWidth)
        : entryCount(entries), bankCount(banks),
          completionLaneCount(completionWidth),
          retirementLaneCount(retirementWidth), slots(entries)
    {
    }

    bool
    valid() const
    {
        return entryCount != 0 && bankCount != 0 &&
            entryCount % bankCount == 0 && completionLaneCount != 0 &&
            retirementLaneCount != 0;
    }

    void
    reset()
    {
        completionQueue.clear();
        for (auto &slot : slots) {
            slot = Slot{};
        }
    }

    bool
    allocate(uint64_t logicalTag)
    {
        if (!valid()) {
            return false;
        }
        Slot &slot = slots[physicalTag(logicalTag)];
        if (slot.allocated) {
            return false;
        }
        slot.allocated = true;
        slot.owner = logicalTag;
        return true;
    }

    bool
    queueCompletion(uint64_t logicalTag)
    {
        Slot *slot = ownedSlot(logicalTag);
        if (!slot || slot->completionQueued || slot->completed) {
            return false;
        }
        slot->completionQueued = true;
        completionQueue.push_back(logicalTag);
        return true;
    }

    bool
    completionQueued(uint64_t logicalTag) const
    {
        const Slot *slot = ownedSlot(logicalTag);
        return slot && slot->completionQueued;
    }

    bool
    completed(uint64_t logicalTag) const
    {
        const Slot *slot = ownedSlot(logicalTag);
        return slot && slot->completed;
    }

    bool
    allocated(uint64_t logicalTag) const
    {
        return ownedSlot(logicalTag) != nullptr;
    }

    size_t
    pendingCompletions() const
    {
        return completionQueue.size();
    }

    CycleResult
    cycle(const std::vector<uint64_t> &retirementTags)
    {
        CycleResult result;
        if (!valid() || retirementTags.size() > retirementLaneCount) {
            return result;
        }

        std::vector<bool> readBanks(bankCount, false);
        for (const uint64_t logicalTag : retirementTags) {
            const Slot *slot = ownedSlot(logicalTag);
            const size_t bank = bankFor(logicalTag);
            if (!slot || !slot->completed || readBanks[bank]) {
                return result;
            }
            readBanks[bank] = true;
        }

        const size_t lanes = std::min(
            completionLaneCount, completionQueue.size());
        std::vector<bool> writeBanks(bankCount, false);
        std::vector<size_t> acceptedLanes;
        for (size_t lane = 0; lane < lanes; ++lane) {
            const uint64_t logicalTag = completionQueue[lane];
            const Slot *slot = ownedSlot(logicalTag);
            if (!slot || !slot->completionQueued || slot->completed) {
                return CycleResult{};
            }
            const size_t bank = bankFor(logicalTag);
            if (readBanks[bank]) {
                result.completionReadConflict = true;
                continue;
            }
            if (writeBanks[bank]) {
                result.completionBankConflict = true;
                continue;
            }
            writeBanks[bank] = true;
            acceptedLanes.push_back(lane);
        }

        for (auto lane = acceptedLanes.rbegin();
             lane != acceptedLanes.rend(); ++lane) {
            const uint64_t logicalTag = completionQueue[*lane];
            Slot *slot = ownedSlot(logicalTag);
            slot->completionQueued = false;
            slot->completed = true;
            completionQueue.erase(completionQueue.begin() + *lane);
        }

        result.valid = true;
        result.retirementReads = retirementTags.size();
        result.completionWrites = acceptedLanes.size();
        result.completionWouldBlock = !completionQueue.empty();
        result.completionQueueDepth = completionQueue.size();
        return result;
    }

    bool
    release(uint64_t logicalTag)
    {
        Slot *slot = ownedSlot(logicalTag);
        if (!slot || slot->completionQueued || !slot->completed) {
            return false;
        }
        *slot = Slot{};
        return true;
    }

  private:
    static constexpr uint64_t NoOwner =
        std::numeric_limits<uint64_t>::max();

    struct Slot
    {
        uint64_t owner = NoOwner;
        bool allocated = false;
        bool completionQueued = false;
        bool completed = false;
    };

    size_t
    physicalTag(uint64_t logicalTag) const
    {
        return static_cast<size_t>(logicalTag % entryCount);
    }

    size_t
    bankFor(uint64_t logicalTag) const
    {
        return physicalTag(logicalTag) % bankCount;
    }

    Slot *
    ownedSlot(uint64_t logicalTag)
    {
        if (!valid()) {
            return nullptr;
        }
        Slot &slot = slots[physicalTag(logicalTag)];
        return slot.allocated && slot.owner == logicalTag ? &slot : nullptr;
    }

    const Slot *
    ownedSlot(uint64_t logicalTag) const
    {
        if (!valid()) {
            return nullptr;
        }
        const Slot &slot = slots[physicalTag(logicalTag)];
        return slot.allocated && slot.owner == logicalTag ? &slot : nullptr;
    }

    const size_t entryCount;
    const size_t bankCount;
    const size_t completionLaneCount;
    const size_t retirementLaneCount;
    std::vector<Slot> slots;
    std::deque<uint64_t> completionQueue;
};

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_OPERATION_PAYLOAD_PORT_MODEL_HH__
