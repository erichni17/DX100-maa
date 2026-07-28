#ifndef __MEM_LANLMAA_CONTROL_SEQUENCER_HH__
#define __MEM_LANLMAA_CONTROL_SEQUENCER_HH__

#include <cstddef>
#include <cstdint>
#include <vector>

#include "base/statistics.hh"
#include "mem/port.hh"
#include "params/LANLMAAControlSequencer.hh"
#include "sim/clocked_object.hh"
#include "sim/eventq.hh"

namespace gem5
{
namespace lanlmaa
{

class LANLMAAControlSequencer : public ClockedObject
{
  private:
    enum class Phase
    {
        Doorbell,
        Status,
        Detail,
        Done
    };

    class SequencerPort : public RequestPort
    {
      public:
        SequencerPort(
            const std::string &name, LANLMAAControlSequencer &owner);

      protected:
        bool recvTimingResp(PacketPtr packet) override;
        void recvReqRetry() override;

      private:
        LANLMAAControlSequencer &owner;
    };

    struct SequencerStats : public statistics::Group
    {
        statistics::Scalar doorbellWritesAccepted;
        statistics::Scalar statusReadsAccepted;
        statistics::Scalar detailReadsAccepted;
        statistics::Scalar responses;
        statistics::Scalar sendFailures;
        statistics::Scalar retryNotifications;
        statistics::Scalar retryResubmissions;
        statistics::Scalar retryAcceptances;
        statistics::Scalar busyObservations;
        statistics::Scalar completedObservations;
        statistics::Scalar errorObservations;
        statistics::Scalar completedDetailsValidated;
        statistics::Scalar errorDetailsValidated;
        statistics::Scalar descriptorsAdvanced;

        explicit SequencerStats(statistics::Group *parent);
    };

    const Addr controlAddr;
    const std::vector<uint64_t> doorbellSlots;
    const std::vector<uint64_t> expectedTerminalErrors;
    const Cycles startCycle;
    const Cycles pollInterval;
    const RequestorID requestorId;

    SequencerPort port;
    EventFunctionWrapper issueEvent;
    SequencerStats stats;
    Phase phase = Phase::Doorbell;
    size_t sequenceIndex = 0;
    PacketPtr packet = nullptr;
    bool waitingForRetry = false;
    bool retryObligation = false;

    uint64_t currentSlot() const;
    uint64_t currentExpectedError() const;
    void scheduleIssue(Cycles delay);
    void issue();
    bool receiveTimingResponse(PacketPtr response);
    void receiveRequestRetry();

  public:
    explicit LANLMAAControlSequencer(
        const LANLMAAControlSequencerParams &params);

    void init() override;
    void startup() override;
    Port &getPort(
        const std::string &ifName,
        PortID index = InvalidPortID) override;
};

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_CONTROL_SEQUENCER_HH__
