#ifndef __MEM_MAA_SHARED_SOURCE_OVERLAP_SCHEDULER_HH__
#define __MEM_MAA_SHARED_SOURCE_OVERLAP_SCHEDULER_HH__

#include <cstdint>

namespace gem5::maa
{

/**
 * Pure decision logic for resuming a single sealed source from Request.
 *
 * The helper owns no source, fanout, response, or payload storage.  Its input
 * is a snapshot of the existing one-entry pending latch and the exact bounded
 * response/shared-payload credits.  Callers remain responsible for draining
 * returned work before evaluation and for attempting at most one legal spill
 * before evaluating a pool-blocked snapshot again.
 */
class SharedSourceOverlapScheduler
{
  public:
    enum class Decision : uint8_t
    {
        NoPending,
        WaitForScan,
        ResumeBuild,
        WaitForResponseSlot,
        WaitForUnifiedCredit,
        RejectNoProgress,
    };

    struct Snapshot
    {
        bool pending = false;
        uint64_t now = 0;
        uint64_t ready = 0;
        uint32_t responseSlotsUsed = 0;
        uint32_t responseSlotCapacity = 0;
        uint32_t combineWords = 0;
        uint32_t reservedResponseWords = 0;
        uint32_t pendingPayloadWords = 0;
        uint32_t unifiedPoolCapacity = 0;
        bool progressPossible = false;
    };

    static Decision
    evaluate(const Snapshot &snapshot)
    {
        if (!snapshot.pending)
            return Decision::NoPending;
        if (snapshot.ready > snapshot.now)
            return Decision::WaitForScan;

        const bool slot_available =
            snapshot.responseSlotsUsed < snapshot.responseSlotCapacity;
        const uint64_t required_words =
            static_cast<uint64_t>(snapshot.combineWords) +
            snapshot.reservedResponseWords + snapshot.pendingPayloadWords;
        const bool pool_available =
            required_words <= snapshot.unifiedPoolCapacity;
        if (slot_available && pool_available)
            return Decision::ResumeBuild;
        if (!snapshot.progressPossible)
            return Decision::RejectNoProgress;
        return slot_available ? Decision::WaitForUnifiedCredit
                              : Decision::WaitForResponseSlot;
    }

    static const char *
    decisionName(Decision decision)
    {
        switch (decision) {
          case Decision::NoPending: return "no_pending";
          case Decision::WaitForScan: return "scan";
          case Decision::ResumeBuild: return "resume";
          case Decision::WaitForResponseSlot: return "response_slot";
          case Decision::WaitForUnifiedCredit: return "unified_credit";
          case Decision::RejectNoProgress: return "no_progress";
        }
        return "unknown";
    }
};

} // namespace gem5::maa

#endif // __MEM_MAA_SHARED_SOURCE_OVERLAP_SCHEDULER_HH__
