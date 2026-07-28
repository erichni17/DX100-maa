#ifndef __MEM_LANLMAA_LANL_MAA_HH__
#define __MEM_LANLMAA_LANL_MAA_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <vector>

#include "base/statistics.hh"
#include "enums/LANLMAAUpdateOperation.hh"
#include "mem/LANLMAA/Descriptor.hh"
#include "mem/port.hh"
#include "mem/tport.hh"
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

    enum class DescriptorState
    {
        Disabled,
        Idle,
        DescriptorPending,
        DescriptorInFlight,
        AddressPending,
        AddressInFlight,
        Executing,
        ResultPending,
        ResultInFlight,
        CompletionPending,
        CompletionInFlight,
        EngineErrorDraining,
        Completed,
        Error
    };

    struct Operation
    {
        Addr address = 0;
        uint64_t expected = 0;
        uint64_t value = 0;
        size_t continuationSteps = 0;
        uint32_t remainingSteps = 0;
        OperationState state = OperationState::Unadmitted;
        bool ownsContext = false;
        bool positiveDirection = false;
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

    class ControlPort : public SimpleTimingPort
    {
      public:
        ControlPort(const std::string &name, LANLMAA &owner);

      protected:
        Tick recvAtomic(PacketPtr packet) override;
        AddrRangeList getAddrRanges() const override;

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
        statistics::Scalar atomicFp64AddUpdates;
        statistics::Scalar strictFp64Serializations;
        statistics::Scalar atomicAcknowledgements;
        statistics::Scalar atomicOldValuesReturned;
        statistics::Scalar updateOperationsAcknowledged;
        statistics::Scalar verificationReads;
        statistics::Scalar descriptorDoorbells;
        statistics::Scalar descriptorBusyRejections;
        statistics::Scalar descriptorFetches;
        statistics::Scalar descriptorAddressLineReads;
        statistics::Scalar descriptorAddressesLoaded;
        statistics::Scalar descriptorResultWrites;
        statistics::Scalar descriptorCompletionWrites;
        statistics::Scalar descriptorErrors;
        statistics::Scalar descriptorCycles;
        statistics::Scalar engineCycles;

        explicit LANLMAAStats(statistics::Group *parent);
    };

    const std::vector<Addr> addresses;
    const std::vector<uint64_t> expectedValues;
    const bool descriptorMode;
    const Addr descriptorTableBase;
    const size_t descriptorSlots;
    const size_t maxDescriptorItems;
    const Addr controlAddr;
    const Addr controlSize;
    const Tick controlLatency;
    const bool dependentMode;
    const size_t continuationEntries;
    const size_t maxContinuationSteps;
    const uint64_t terminalAddress;
    const bool updateMode;
    const std::vector<uint64_t> updateValues;
    const std::vector<double> updateFpValues;
    const enums::LANLMAAUpdateOperation updateOperation;
    const std::vector<Addr> verificationAddresses;
    const std::vector<uint64_t> verificationValues;
    const std::vector<double> verificationFpValues;
    const double verificationAbsTolerance;
    const double verificationRelTolerance;
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
    System *const system;
    const RequestorID requestorId;

    MemoryPort memoryPort;
    ControlPort controlPort;
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

    DescriptorState descriptorState = DescriptorState::Disabled;
    Descriptor descriptor;
    DescriptorError descriptorError = DescriptorError::None;
    uint32_t descriptorSlot = 0;
    size_t descriptorAddressCursor = 0;
    size_t descriptorResultCursor = 0;
    PacketPtr descriptorPacket = nullptr;
    PacketPtr addressVectorPacket = nullptr;
    PacketPtr resultPacket = nullptr;
    PacketPtr completionPacket = nullptr;

    Addr lineAddress(Addr address) const;
    LineEntry *matchingLine(Addr address);
    LineEntry *freeLine();
    size_t updateBank(Addr address) const;
    UpdateEntry *matchingUpdate(Addr address);
    UpdateEntry *freeUpdate(Addr address);
    UpdateEntry *drainableUpdate(Addr address);
    UpdateEntry *updateForPacket(PacketPtr packet);
    bool allUpdateEntriesFree() const;
    bool activeDependentMode() const;
    bool floatingUpdate() const;
    bool strictFloatingUpdate() const;
    static uint64_t encodeDouble(double value);
    static double decodeDouble(uint64_t bits);
    void validateConfiguration() const;
    void scheduleTick();
    AddrRangeList controlRanges() const;
    Tick controlAccess(PacketPtr packet);
    void ringDoorbell(uint32_t slot);
    void rejectDescriptor(DescriptorError error);
    void beginDescriptorErrorDrain(DescriptorError error);
    bool descriptorErrorDrainComplete() const;
    bool rangeOverlapsControl(uint64_t begin, uint64_t bytes) const;
    bool rangeIsMemory(uint64_t begin, uint64_t bytes) const;
    void issueDescriptorTraffic();
    void issueDescriptorFetch();
    void issueAddressVectorFetch();
    void issueResultWrite();
    void issueCompletionWrite();
    bool sendDescriptorPacket(PacketPtr packet);
    bool receiveDescriptorResponse(PacketPtr packet);
    bool receiveAddressVectorResponse(PacketPtr packet);
    bool receiveResultResponse(PacketPtr packet);
    bool receiveCompletionResponse(PacketPtr packet);
    bool receiveDrainingLineResponse(PacketPtr packet);
    void beginDescriptorExecution();
    void beginDescriptorResults();
    void completeDescriptor();
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
