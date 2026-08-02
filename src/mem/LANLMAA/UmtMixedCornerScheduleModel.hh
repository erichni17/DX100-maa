#ifndef __MEM_LANLMAA_UMT_MIXED_CORNER_SCHEDULE_MODEL_HH__
#define __MEM_LANLMAA_UMT_MIXED_CORNER_SCHEDULE_MODEL_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <vector>

#include "mem/LANLMAA/SharedOverlayCost.hh"
#include "mem/LANLMAA/UmtMixedCornerModel.hh"

namespace gem5
{
namespace lanlmaa
{

constexpr uint32_t UmtMixedScheduleBaseBits =
    UmtOperationBits + UmtContinuationBits;
constexpr uint32_t UmtMixedScheduleExtraFp64Words = 6;
constexpr uint32_t UmtMixedScheduleExtraBits =
    UmtMixedScheduleExtraFp64Words * 64;
constexpr uint32_t UmtMixedSchedulePayloadBits =
    UmtMixedScheduleBaseBits + UmtMixedScheduleExtraBits;
constexpr uint32_t UmtMixedScheduleSidecarWords = 2;
constexpr uint32_t UmtMixedScheduleSelectedUpdateBanks = 8;
constexpr uint32_t UmtMixedScheduleMaximumContexts =
    SharedUpdateStore.entries / UmtMixedScheduleSidecarWords;

static_assert(UmtMixedScheduleBaseBits == 576);
static_assert(UmtMixedScheduleExtraBits == 384);
static_assert(UmtMixedScheduleExtraBits == UmtMixedCornerRetainedDeltaBits);
static_assert(UmtMixedSchedulePayloadBits == 960);
static_assert(UmtMixedSchedulePayloadBits > SharedPairBits);
static_assert(UmtMixedScheduleSidecarWords *
                  SharedUpdateStore.roundedBitsPerEntry ==
              UmtMixedScheduleExtraBits);
static_assert(SharedUpdateStore.entries %
                  UmtMixedScheduleSelectedUpdateBanks ==
              0);
static_assert(UmtMixedScheduleMaximumContexts == 32);

enum class UmtMixedOverlayResult : uint8_t
{
    Accepted = 0,
    Inactive,
    AlreadyActive,
    BadContextCount,
    BadContext,
    BadWord,
    OwnerConflict,
    OutstandingTraffic
};

enum class UmtMixedOverlayAccess : uint8_t
{
    Read = 0,
    Write
};

struct UmtMixedOverlayRequest
{
    uint64_t tag = 0;
    uint32_t context = 0;
    uint32_t word = 0;
    UmtMixedOverlayAccess access = UmtMixedOverlayAccess::Read;
};

struct UmtMixedOverlayCycleResult
{
    bool valid = false;
    std::vector<UmtMixedOverlayRequest> served;
    size_t pending = 0;
};

/**
 * Standalone port/arbitration screen for the proposed mixed-corner sidecar.
 * The real SharedOverlayModeBarrier remains responsible for owner acquisition
 * and external read/write/atomic/completion obligations.
 */
class UmtMixedCornerSidecarPortModel
{
  private:
    bool umtActive = false;
    uint32_t contextCount = 0;
    std::array<std::deque<UmtMixedOverlayRequest>,
               UmtMixedScheduleSelectedUpdateBanks> queues{};

  public:
    static constexpr uint32_t
    entryFor(uint32_t context, uint32_t word)
    {
        return context * UmtMixedScheduleSidecarWords + word;
    }

    static constexpr uint32_t
    bankFor(uint32_t context, uint32_t word)
    {
        return entryFor(context, word) %
            UmtMixedScheduleSelectedUpdateBanks;
    }

    UmtMixedOverlayResult
    activate(uint32_t contexts)
    {
        if (umtActive) {
            return UmtMixedOverlayResult::AlreadyActive;
        }
        if (contexts == 0 ||
            contexts > UmtMixedScheduleMaximumContexts) {
            return UmtMixedOverlayResult::BadContextCount;
        }
        umtActive = true;
        contextCount = contexts;
        return UmtMixedOverlayResult::Accepted;
    }

    UmtMixedOverlayResult
    enqueue(const UmtMixedOverlayRequest &request)
    {
        if (!umtActive) {
            return UmtMixedOverlayResult::Inactive;
        }
        if (request.context >= contextCount) {
            return UmtMixedOverlayResult::BadContext;
        }
        if (request.word >= UmtMixedScheduleSidecarWords) {
            return UmtMixedOverlayResult::BadWord;
        }
        queues[bankFor(request.context, request.word)].push_back(request);
        return UmtMixedOverlayResult::Accepted;
    }

    UmtMixedOverlayResult
    enqueueNormalUpdateCombiner()
    {
        return umtActive ? UmtMixedOverlayResult::OwnerConflict :
            UmtMixedOverlayResult::Accepted;
    }

    UmtMixedOverlayCycleResult
    cycle()
    {
        UmtMixedOverlayCycleResult result;
        if (!umtActive) {
            return result;
        }
        result.valid = true;
        for (auto &queue : queues) {
            if (!queue.empty()) {
                result.served.push_back(queue.front());
                queue.pop_front();
            }
        }
        result.pending = pending();
        return result;
    }

    UmtMixedOverlayResult
    deactivate()
    {
        if (!umtActive) {
            return UmtMixedOverlayResult::Inactive;
        }
        if (pending() != 0) {
            return UmtMixedOverlayResult::OutstandingTraffic;
        }
        umtActive = false;
        contextCount = 0;
        return UmtMixedOverlayResult::Accepted;
    }

    size_t
    pending() const
    {
        size_t value = 0;
        for (const auto &queue : queues) {
            value += queue.size();
        }
        return value;
    }

    bool
    active() const
    {
        return umtActive;
    }

    uint32_t
    contexts() const
    {
        return contextCount;
    }
};

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_UMT_MIXED_CORNER_SCHEDULE_MODEL_HH__
