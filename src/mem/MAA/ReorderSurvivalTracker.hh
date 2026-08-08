#ifndef __MEM_MAA_REORDER_SURVIVAL_TRACKER_HH__
#define __MEM_MAA_REORDER_SURVIVAL_TRACKER_HH__

#include <algorithm>
#include <cstdint>

namespace gem5
{

/**
 * Constant-size accounting for one indirect instruction's RowTable visibility
 * epochs.  The tracker is observational only: callers decide when a completed
 * epoch is printed, and no method models latency or schedules an event.
 */
class ReorderSurvivalTracker
{
  public:
    enum class DrainReason : uint8_t
    {
        RowTableFull,
        OffsetEpochFull,
        PartitionBoundary,
    };

    struct Epoch
    {
        uint64_t id = 0;
        uint64_t admissions = 0;
        uint64_t issuedLines = 0;
        uint64_t issuedEntries = 0;
        uint64_t maxJointAdmissions = 0;
        uint64_t rowTransitions = 0;
        uint64_t rtFullDrains = 0;
        uint64_t offsetDrains = 0;
        uint64_t partitionDrains = 0;
        bool final = false;
    };

    void
    begin(uint64_t instruction_id)
    {
        instructionId = instruction_id;
        current = Epoch{};
        totalSelectedDescriptors = 0;
        totalAdmissions = 0;
        totalIssuedLines = 0;
        totalIssuedEntries = 0;
        totalRowTransitions = 0;
        totalRTFullDrains = 0;
        totalOffsetDrains = 0;
        totalPartitionDrains = 0;
        maxJointAdmissions = 0;
        visibleAdmissions = 0;
        epochs = 0;
        lastIssuedRow = 0;
        hasLastIssuedRow = false;
        lastSelectionId = 0;
        hasLastSelectionId = false;
        active = true;
        complete = false;
    }

    bool
    select(uint64_t selection_id)
    {
        if (!active || complete)
            return false;
        // A selected iteration can be retried unchanged after either table
        // reports pressure.  Count the semantic selection once, not once per
        // failed insertion attempt.
        if (!hasLastSelectionId || lastSelectionId != selection_id) {
            ++totalSelectedDescriptors;
            lastSelectionId = selection_id;
            hasLastSelectionId = true;
        }
        return true;
    }

    bool
    admit()
    {
        if (!active || complete)
            return false;
        ++current.admissions;
        ++totalAdmissions;
        ++visibleAdmissions;
        current.maxJointAdmissions =
            std::max(current.maxJointAdmissions, visibleAdmissions);
        maxJointAdmissions =
            std::max(maxJointAdmissions, visibleAdmissions);
        return true;
    }

    bool
    issueLine(uint64_t row_key)
    {
        if (!active || complete)
            return false;
        if (hasLastIssuedRow && lastIssuedRow != row_key) {
            ++current.rowTransitions;
            ++totalRowTransitions;
        }
        lastIssuedRow = row_key;
        hasLastIssuedRow = true;
        ++current.issuedLines;
        ++totalIssuedLines;
        return true;
    }

    bool
    issueEntries(uint64_t entries)
    {
        if (!active || complete || entries == 0 ||
            entries > visibleAdmissions)
            return false;
        current.issuedEntries += entries;
        totalIssuedEntries += entries;
        visibleAdmissions -= entries;
        return true;
    }

    bool
    markDrain(DrainReason reason)
    {
        if (!active || complete)
            return false;
        uint64_t *epoch_counter = nullptr;
        uint64_t *total_counter = nullptr;
        switch (reason) {
          case DrainReason::RowTableFull:
            epoch_counter = &current.rtFullDrains;
            total_counter = &totalRTFullDrains;
            break;
          case DrainReason::OffsetEpochFull:
            epoch_counter = &current.offsetDrains;
            total_counter = &totalOffsetDrains;
            break;
          case DrainReason::PartitionBoundary:
            epoch_counter = &current.partitionDrains;
            total_counter = &totalPartitionDrains;
            break;
        }
        // Every RowTable-full observation is a finite drain within the
        // current Offset/partition epoch. Offset/partition pressure itself
        // is one epoch boundary and can be observed repeatedly while that
        // boundary is outstanding.
        if (reason == DrainReason::RowTableFull) {
            ++*epoch_counter;
            ++*total_counter;
        } else if (*epoch_counter == 0) {
            ++*epoch_counter;
            ++*total_counter;
        }
        return true;
    }

    bool
    drainPending() const
    {
        return current.offsetDrains != 0 || current.partitionDrains != 0;
    }

    bool
    closeEpoch(bool final, Epoch &closed)
    {
        if (!active || complete || (!final && !drainPending()) ||
            visibleAdmissions != 0 ||
            current.admissions != current.issuedEntries)
            return false;
        current.final = final;
        closed = current;
        ++epochs;
        if (final) {
            complete = true;
            active = false;
        } else {
            current = Epoch{};
            current.id = epochs;
            hasLastIssuedRow = false;
            lastIssuedRow = 0;
        }
        return true;
    }

    bool reconciled() const
    {
        return complete && totalAdmissions == totalIssuedEntries;
    }

    bool preserves16K(bool predicate_present = false) const
    {
        return reconciled() && !predicate_present &&
               totalSelectedDescriptors == 16384 &&
               totalAdmissions == totalSelectedDescriptors && epochs == 1 &&
               midInstructionDrains() == 0 &&
               maxJointAdmissions == totalAdmissions;
    }

    uint64_t midInstructionDrains() const
    {
        return totalRTFullDrains + totalOffsetDrains +
               totalPartitionDrains;
    }

    uint64_t instructionId = 0;
    uint64_t totalSelectedDescriptors = 0;
    uint64_t totalAdmissions = 0;
    uint64_t totalIssuedLines = 0;
    uint64_t totalIssuedEntries = 0;
    uint64_t totalRowTransitions = 0;
    uint64_t totalRTFullDrains = 0;
    uint64_t totalOffsetDrains = 0;
    uint64_t totalPartitionDrains = 0;
    uint64_t maxJointAdmissions = 0;
    uint64_t epochs = 0;

  private:
    Epoch current{};
    uint64_t lastIssuedRow = 0;
    uint64_t lastSelectionId = 0;
    uint64_t visibleAdmissions = 0;
    bool hasLastIssuedRow = false;
    bool hasLastSelectionId = false;
    bool active = false;
    bool complete = false;
};

} // namespace gem5

#endif // __MEM_MAA_REORDER_SURVIVAL_TRACKER_HH__
