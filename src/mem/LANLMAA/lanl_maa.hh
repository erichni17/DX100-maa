#ifndef __MEM_LANLMAA_LANL_MAA_HH__
#define __MEM_LANLMAA_LANL_MAA_HH__

#include <cstddef>
#include <cstdint>
#include <vector>

#include "base/statistics.hh"
#include "enums/LANLMAAUpdateOperation.hh"
#include "mem/port.hh"
#include "params/LANLMAA.hh"
#include "sim/clocked_object.hh"
#include "sim/eventq.hh"

namespace gem5
{
namespace lanlmaa
{

class LANLMAA : public ClockedObject
{
  private:
    enum class LineState
    {
        Free,
        Allocated,
        InFlight
    };

    enum class OperationState
    {
        Unadmitted,
        AddressReady,
        DataPending,
        UpdatePending,
        RetireReady
    };

    enum class UpdateState
    {
        Free,
        Accumulating,
        AtomicPending,
        AtomicInFlight
    };

    struct Operation
    {
        Addr address = 0;
        uint64_t expected = 0;
        uint64_t value = 0;
        size_t continuationSteps = 0;
        OperationState state = OperationState::Unadmitted;
        bool ownsContext = false;
    };

    struct LineEntry
    {
        LineState state = LineState::Free;
        Addr lineAddress = 0;
        PacketPtr packet = nullptr;
        std::vector<size_t> waiters;

        void clear();
    };

    struct UpdateEntry
    {
        UpdateState state = UpdateState::Free;
        Addr address = 0;
        uint64_t contribution = 0;
        PacketPtr packet = nullptr;
        std::vector<size_t> waiters;

        void clear();
    };

    class MemoryPort : public RequestPort
    {
      public:
        MemoryPort(const std::string &name, LANLMAA &owner);

      protected:
        bool recvTimingResp(PacketPtr packet) override;
        void recvReqRetry() override;

      private:
        LANLMAA &owner;
    };

    struct LANLMAAStats : public statistics::Group
    {
        statistics::Scalar logicalItems;
        statistics::Scalar logicalMemoryAccesses;
        statistics::Scalar physicalLineReads;
        statistics::Scalar lineMergeHits;
        statistics::Scalar operationWouldBlockCycles;
        statistics::Scalar lineWouldBlockCycles;
        statistics::Scalar contextWouldBlockCycles;
        statistics::Scalar portSendFailures;
        statistics::Scalar portRetryNotifications;
        statistics::Scalar retryPacketResubmissions;
        statistics::Scalar retryPacketAcceptances;
        statistics::Scalar responses;
        statistics::Scalar responsesFannedOut;
        statistics::Scalar completionsRetired;
        statistics::Scalar verificationFailures;
        statistics::Scalar continuationSteps;
        statistics::Scalar continuationExhaustions;
        statistics::Scalar activeContextHighWaterMark;
        statistics::Scalar updateCombinerHits;
        statistics::Scalar updateTableWouldBlockCycles;
        statistics::Scalar updateAddressBusyCycles;
        statistics::Scalar updateDrains;
        statistics::Scalar physicalAtomicUpdates;
        statistics::Scalar atomicAddUpdates;
        statistics::Scalar atomicMinUpdates;
        statistics::Scalar atomicMaxUpdates;
        statistics::Scalar atomicAcknowledgements;
        statistics::Scalar atomicOldValuesReturned;
        statistics::Scalar updateOperationsAcknowledged;
        statistics::Scalar verificationReads;
        statistics::Scalar engineCycles;

        explicit LANLMAAStats(statistics::Group *parent);
    };

    const std::vector<Addr> addresses;
    const std::vector<uint64_t> expectedValues;
    const bool dependentMode;
    const size_t continuationEntries;
    const size_t maxContinuationSteps;
    const uint64_t terminalAddress;
    const bool updateMode;
    const std::vector<uint64_t> updateValues;
    const enums::LANLMAAUpdateOperation updateOperation;
    const std::vector<Addr> verificationAddresses;
    const std::vector<uint64_t> verificationValues;
    const size_t updateEntryCount;
    const size_t updateBanks;
    const size_t updateIssueWidth;
    const size_t operationEntries;
    const size_t lineEntries;
    const size_t logicalAdmissionWidth;
    const size_t lineIssueWidth;
    const size_t retirementWidth;
    const size_t lineBytes;
    const Cycles startCycle;
    const bool exitOnCompletion;
    const RequestorID requestorId;

    MemoryPort memoryPort;
    EventFunctionWrapper tickEvent;
    LANLMAAStats stats;

    std::vector<Operation> operations;
    std::vector<LineEntry> lines;
    std::vector<UpdateEntry> updates;
    size_t nextAdmission = 0;
    size_t nextRetirement = 0;
    size_t nextVerification = 0;
    size_t activeOperations = 0;
    size_t activeContexts = 0;
    PacketPtr verificationPacket = nullptr;
    PacketPtr rejectedPacket = nullptr;
    bool verificationInFlight = false;
    bool waitingForRetry = false;
    bool finished = false;

    Addr lineAddress(Addr address) const;
    LineEntry *matchingLine(Addr address);
    LineEntry *freeLine();
    size_t updateBank(Addr address) const;
    UpdateEntry *matchingUpdate(Addr address);
    UpdateEntry *freeUpdate(Addr address);
    UpdateEntry *drainableUpdate(Addr address);
    UpdateEntry *updateForPacket(PacketPtr packet);
    bool allUpdateEntriesFree() const;
    void validateConfiguration() const;
    void scheduleTick();
    void tick();
    void retireOperations();
    void admitOperations();
    void attachReadyOperations();
    void attachReadyUpdates();
    void scheduleUpdateDrains();
    void issueLines();
    void issueUpdates();
    void issueVerification();
    void finish();
    bool receiveTimingResponse(PacketPtr packet);
    bool receiveUpdateResponse(UpdateEntry &entry, PacketPtr packet);
    bool receiveVerificationResponse(PacketPtr packet);
    void receiveRequestRetry();

  public:
    explicit LANLMAA(const LANLMAAParams &params);

    void init() override;
    void startup() override;
    Port &getPort(
        const std::string &ifName,
        PortID index = InvalidPortID) override;
};

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_LANL_MAA_HH__
