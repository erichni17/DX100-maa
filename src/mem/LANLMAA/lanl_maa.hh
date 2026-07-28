#ifndef __MEM_LANLMAA_LANL_MAA_HH__
#define __MEM_LANLMAA_LANL_MAA_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <vector>

#include "base/statistics.hh"
#include "enums/LANLMAAUpdateOperation.hh"
#include "mem/LANLMAA/BransonContextLimit.hh"
#include "mem/LANLMAA/BransonEventDescriptor.hh"
#include "mem/LANLMAA/BransonEventTiming.hh"
#include "mem/LANLMAA/Descriptor.hh"
#include "mem/LANLMAA/FaceComputeTiming.hh"
#include "mem/LANLMAA/SpartaTallyDescriptor.hh"
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
        BransonEventComputeReady,
        BransonEventComputePending,
        BransonUpdateReady,
        SpartaUpdateReady,
        FaceComputeReady,
        FaceComputePending,
        FaceGatherComplete,
        FaceUpdateReady,
        UpdatePending,
        RetireReady
    };

    enum class UpdateKind
    {
        Uint64Add,
        Uint64Min,
        Uint64Max,
        Fp64AddRelaxed,
        Fp64AddStrict,
        Fp64Min,
        Fp64Max
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

    enum class BransonPhase
    {
        Inactive,
        Validate,
        Update
    };

    enum class SpartaTallyPhase
    {
        Inactive,
        Validate,
        Update
    };

    enum class TrafficKind
    {
        Descriptor,
        AddressVector,
        Result,
        Completion,
        Line,
        Update,
        Verification
    };

    struct RequestSenderState;

    struct Operation
    {
        Addr address = 0;
        uint64_t expected = 0;
        uint64_t value = 0;
        std::array<uint64_t, 3> faceValues{};
        size_t continuationSteps = 0;
        uint32_t remainingSteps = 0;
        uint32_t faceLow = 0;
        uint32_t faceHigh = 0;
        uint8_t faceGatherStage = 0;
        uint8_t faceUpdateOrdinal = 0;
        uint64_t faceComputeReadyCycle = 0;
        uint64_t bransonComputeReadyCycle = 0;
        uint64_t bransonAbsorbedDelta = 0;
        uint64_t bransonTrackDelta = 0;
        uint32_t bransonFirstEvent = 0;
        uint32_t bransonEvent = 0;
        uint32_t bransonExpectedEvents = 0;
        uint32_t bransonEventsRemaining = 0;
        uint32_t bransonExpectedInitialCell = 0;
        uint32_t bransonExpectedFinalCell = 0;
        uint32_t bransonCurrentCell = 0;
        uint32_t bransonDestinationCell = 0;
        uint32_t bransonNextEvent = BransonTerminalEvent;
        uint8_t bransonExpectedTerminalKind = 0;
        uint8_t bransonUpdateOrdinal = 0;
        uint32_t spartaItem = 0;
        uint32_t spartaCell = 0;
        uint8_t spartaChannel = 0;
        OperationState state = OperationState::Unadmitted;
        bool ownsContext = false;
        bool positiveDirection = false;
        bool facePressureWeighted = false;
        FaceMinMaxKind faceKind = FaceMinMaxKind::Inactive;
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
        UpdateKind kind = UpdateKind::Uint64Add;
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
        statistics::Scalar bransonContextThrottleCycles;
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
        statistics::Scalar atomicFp64MinUpdates;
        statistics::Scalar atomicFp64MaxUpdates;
        statistics::Scalar strictFp64Serializations;
        statistics::Scalar atomicAcknowledgements;
        statistics::Scalar atomicOldValuesReturned;
        statistics::Scalar updateOperationsAcknowledged;
        statistics::Scalar verificationReads;
        statistics::Scalar descriptorDoorbells;
        statistics::Scalar descriptorBusyRejections;
        statistics::Scalar descriptorRearms;
        statistics::Scalar descriptorFetches;
        statistics::Scalar descriptorAddressLineReads;
        statistics::Scalar descriptorAddressesLoaded;
        statistics::Scalar descriptorResultWrites;
        statistics::Scalar descriptorCompletionWrites;
        statistics::Scalar descriptorErrors;
        statistics::Scalar descriptorPredicatesSkipped;
        statistics::Scalar descriptorFaceValuesComputed;
        statistics::Scalar descriptorFaceVacuumValues;
        statistics::Scalar descriptorFacePressureWeightedValues;
        statistics::Scalar descriptorFaceBoundaryValues;
        statistics::Scalar descriptorFaceUpdatesAcknowledged;
        statistics::Scalar descriptorFaceComputesQueued;
        statistics::Scalar descriptorFaceComputesIssued;
        statistics::Scalar descriptorFaceComputesCompleted;
        statistics::Scalar faceComputeWouldBlockCycles;
        statistics::Scalar faceComputeActiveCycles;
        statistics::Scalar activeFaceComputeHighWaterMark;
        statistics::Scalar descriptorBransonRootsLoaded;
        statistics::Scalar descriptorBransonEventsValidated;
        statistics::Scalar descriptorBransonEventsReplayed;
        statistics::Scalar descriptorBransonUpdatesAcknowledged;
        statistics::Scalar descriptorBransonEventComputesQueued;
        statistics::Scalar descriptorBransonEventComputesIssued;
        statistics::Scalar descriptorBransonEventComputesCompleted;
        statistics::Scalar descriptorBransonEventComputesCancelled;
        statistics::Scalar descriptorBransonEventComputesCancelledInFlight;
        statistics::Scalar bransonEventComputeWouldBlockCycles;
        statistics::Scalar bransonEventComputeActiveCycles;
        statistics::Scalar activeBransonEventComputeHighWaterMark;
        statistics::Scalar descriptorSpartaItemsLoaded;
        statistics::Scalar descriptorSpartaContributionsValidated;
        statistics::Scalar descriptorSpartaContributionsReplayed;
        statistics::Scalar descriptorSpartaUpdatesAcknowledged;
        statistics::Scalar descriptorSpartaPendingGenerationsAllocated;
        statistics::Scalar spartaPendingGenerationDrainDeferrals;
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
    const bool spartaPendingGeneration;
    const Cycles faceComputeLatency;
    const Cycles faceComputeInitiationInterval;
    const size_t faceComputeUnits;
    const Cycles bransonEventComputeLatency;
    const Cycles bransonEventComputeInitiationInterval;
    const size_t bransonEventComputeUnits;
    const size_t bransonContextQuantum;
    const BransonContextLimit bransonContextLimit;
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
    std::unique_ptr<FaceComputeTiming> faceComputeTiming;
    std::unique_ptr<BransonEventTiming> bransonEventTiming;
    std::unique_ptr<BransonContextScheduler> bransonContextScheduler;

    std::vector<Operation> operations;
    std::vector<LineEntry> lines;
    std::vector<UpdateEntry> updates;
    size_t nextAdmission = 0;
    size_t nextRetirement = 0;
    size_t nextVerification = 0;
    size_t activeOperations = 0;
    size_t activeContexts = 0;
    size_t activeFaceComputations = 0;
    size_t activeBransonEventComputations = 0;
    PacketPtr verificationPacket = nullptr;
    PacketPtr rejectedPacket = nullptr;
    bool verificationInFlight = false;
    bool waitingForRetry = false;
    bool finished = false;

    DescriptorState descriptorState = DescriptorState::Disabled;
    Descriptor descriptor;
    BransonEventDescriptor bransonDescriptor;
    BransonPhase bransonPhase = BransonPhase::Inactive;
    SpartaTallyDescriptor spartaDescriptor;
    SpartaTallyPhase spartaTallyPhase = SpartaTallyPhase::Inactive;
    DescriptorError descriptorError = DescriptorError::None;
    uint32_t descriptorSlot = 0;
    size_t descriptorAddressCursor = 0;
    size_t descriptorResultCursor = 0;
    uint64_t descriptorFaceUpdatesAcknowledged = 0;
    uint64_t bransonEventsValidated = 0;
    uint64_t bransonEventsReplayed = 0;
    uint64_t bransonUpdatesAcknowledged = 0;
    uint64_t spartaContributionsValidated = 0;
    uint64_t spartaContributionsReplayed = 0;
    uint64_t spartaUpdatesAcknowledged = 0;
    bool descriptorFaceUpdatePhase = false;
    PacketPtr descriptorPacket = nullptr;
    PacketPtr addressVectorPacket = nullptr;
    PacketPtr resultPacket = nullptr;
    PacketPtr completionPacket = nullptr;

    Addr lineAddress(Addr address) const;
    LineEntry *matchingLine(Addr address);
    LineEntry *freeLine();
    size_t updateBank(Addr address) const;
    UpdateEntry *matchingUpdate(Addr address);
    UpdateEntry *accumulatingUpdate(Addr address);
    UpdateEntry *freeUpdate(Addr address);
    UpdateEntry *drainableUpdate(Addr address);
    UpdateEntry *updateForPacket(PacketPtr packet);
    bool allUpdateEntriesFree() const;
    size_t updateGenerationCount(Addr address) const;
    bool updateGenerationDrainBlocked(const UpdateEntry &entry) const;
    bool activeDependentMode() const;
    bool bransonEventDescriptor() const;
    bool spartaTallyDescriptor() const;
    static bool bransonTerminalKind(uint8_t kind);
    Addr bransonEventAddress(uint32_t event) const;
    Addr bransonTallyAddress(const Operation &operation) const;
    void resetBransonOperation(Operation &operation);
    void advanceBransonEvent(Operation &operation);
    void completeBransonEvent(Operation &operation);
    void completeBransonEventComputations();
    void issueBransonEventComputations();
    void beginBransonUpdatePhase();
    bool bransonValidationComplete() const;
    Addr spartaContributionAddress(const Operation &operation) const;
    Addr spartaTallyAddress(const Operation &operation) const;
    void resetSpartaOperation(Operation &operation);
    void advanceSpartaContribution(Operation &operation);
    void beginSpartaUpdatePhase();
    bool faceMinMaxDescriptor() const;
    UpdateKind configuredUpdateKind() const;
    bool floatingUpdate() const;
    bool strictFloatingUpdate() const;
    static bool floatingUpdate(UpdateKind kind);
    static bool strictFloatingUpdate(UpdateKind kind);
    UpdateKind operationUpdateKind(const Operation &operation) const;
    bool faceOperationActive(const Operation &operation) const;
    size_t faceGatherCount(const Operation &operation) const;
    size_t faceUpdateCount(const Operation &operation) const;
    uint8_t faceOutputOrdinal(const Operation &operation) const;
    Addr faceGatherAddress(const Operation &operation) const;
    Addr faceUpdateAddress(const Operation &operation) const;
    bool faceGatheringComplete() const;
    void completeFaceValue(Operation &operation);
    void completeFaceComputations();
    void issueFaceComputations();
    void beginFaceUpdatePhase();
    static uint64_t encodeDouble(double value);
    static double decodeDouble(uint64_t bits);
    void tagRequest(
        PacketPtr packet, TrafficKind kind, PacketPtr *retainedPacket);
    TrafficKind acceptResponse(PacketPtr packet);
    void discardUnsentRequest(PacketPtr &packet);
    void validateConfiguration() const;
    void scheduleTick();
    AddrRangeList controlRanges() const;
    Tick controlAccess(PacketPtr packet);
    bool descriptorTerminal() const;
    void rearmDescriptorEngine();
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
