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
#include "mem/LANLMAA/LineTableGeometry.hh"
#include "mem/LANLMAA/OperationPayloadPortModel.hh"
#include "mem/LANLMAA/SharedOverlayModeBarrier.hh"
#include "mem/LANLMAA/SpartaFusedCellModel.hh"
#include "mem/LANLMAA/SpartaPairedSummaryStore.hh"
#include "mem/LANLMAA/SpartaTallyDescriptor.hh"
#include "mem/LANLMAA/UmeGradzatpDescriptor.hh"
#include "mem/LANLMAA/UmtFusedCornerModel.hh"
#include "mem/LANLMAA/UmtMixedCornerDescriptor.hh"
#include "mem/LANLMAA/UmtMixedCornerScheduleModel.hh"
#include "mem/LANLMAA/UmtOrderedWaveDescriptor.hh"
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
        UmeUpdateReady,
        UmtComputeReady,
        UmtSidecarPending,
        UmtComputePending,
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
        Fp64Max,
        Fp32AddRelaxed
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

    enum class SpartaFusedPhase
    {
        Inactive,
        Traverse,
        ValidateTallies
    };

    enum class UmeGradzatpPhase
    {
        Inactive,
        Validate,
        Update
    };

    enum class UmtFusedCornerPhase
    {
        Inactive,
        Read,
        Compute
    };

    enum class SpartaFusedStage : uint8_t
    {
        CellCount,
        CellFirst,
        CellMask,
        ParticleSpecies,
        ParticleCell,
        ParticleNext,
        SpeciesGroup,
        SpeciesMass,
        VelocityX,
        VelocityY,
        VelocityZ,
        Tally
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
        // These traversal fields are live only while one of the separately
        // modeled eight active-context slots is owned.
        uint64_t spartaFusedMass = 0;
        uint64_t spartaFusedVelocitySquared = 0;
        uint32_t spartaFusedCell = 0;
        uint32_t spartaFusedParticle = 0;
        uint32_t spartaFusedRemaining = 0;
        uint32_t spartaFusedMask = 0;
        int32_t spartaFusedNext = -1;
        int32_t spartaFusedSpecies = -1;
        SpartaFusedStage spartaFusedStage = SpartaFusedStage::CellCount;
        uint8_t spartaFusedChannel = 0;
        uint8_t spartaFusedContext = SpartaFusedActiveContexts;
        // Eight folded FP64 inputs plus value form one 640-bit paired
        // operation/continuation entry for the UMT fused path.
        std::array<uint64_t, 8> umtFusedValues{};
        uint32_t umtFusedGroup = 0;
        uint8_t umtFusedReadStage = 0;
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
        // Third payload lane when two update entries/context are exclusively
        // reinterpreted as the UMT mixed-corner 2x192-bit sidecar.
        uint64_t umtPayloadThird = 0;
        UpdateKind kind = UpdateKind::Uint64Add;
        uint32_t spartaGroup = 0;
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
        statistics::Scalar lineBankConflictCycles;
        statistics::Scalar operationWouldBlockCycles;
        statistics::Scalar lineWouldBlockCycles;
        statistics::Scalar contextWouldBlockCycles;
        statistics::Scalar operationTableHighWaterMark;
        statistics::Scalar lineTableHighWaterMark;
        statistics::Scalar controlReadRequests;
        statistics::Scalar controlStatusReads;
        statistics::Scalar controlOpcodeReads;
        statistics::Scalar controlErrorReads;
        statistics::Scalar bransonContextThrottleCycles;
        statistics::Scalar portSendFailures;
        statistics::Scalar portRetryNotifications;
        statistics::Scalar retryPacketResubmissions;
        statistics::Scalar retryPacketAcceptances;
        statistics::Scalar responses;
        statistics::Scalar responsesFannedOut;
        statistics::Scalar completionsRetired;
        statistics::Scalar payloadOverlayCompletionWrites;
        statistics::Scalar payloadOverlayRetirementReads;
        statistics::Scalar payloadOverlayCompletionBankConflictCycles;
        statistics::Scalar payloadOverlayCompletionReadConflictCycles;
        statistics::Scalar payloadOverlayCompletionWouldBlockCycles;
        statistics::Scalar payloadOverlayCompletionQueueHighWaterMark;
        statistics::Scalar payloadOverlayResetAllocatedEntries;
        statistics::Scalar payloadOverlayResetQueuedCompletions;
        statistics::Scalar payloadOverlayResetCompletedEntries;
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
        statistics::Scalar atomicFp32AddUpdates;
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
        statistics::Scalar descriptorUmtResultLineWrites;
        statistics::Scalar descriptorCompletionWrites;
        statistics::Scalar descriptorErrors;
        statistics::Scalar sharedOverlayModeAcquisitions;
        statistics::Scalar sharedOverlayReservationRejections;
        statistics::Scalar sharedOverlayTrafficAccepted;
        statistics::Scalar sharedOverlayTrafficAcknowledged;
        statistics::Scalar sharedOverlayDrains;
        statistics::Scalar sharedOverlayReleases;
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
        statistics::Scalar descriptorSpartaCellGroupCompleteDrains;
        statistics::Scalar descriptorSpartaCellGroupDrainDeferrals;
        statistics::Scalar descriptorSpartaCellGroupForcedDrains;
        statistics::Scalar descriptorSpartaFusedCellsLoaded;
        statistics::Scalar descriptorSpartaFusedParticlesVisited;
        statistics::Scalar descriptorSpartaFusedEligibleParticles;
        statistics::Scalar descriptorSpartaFusedFp64Multiplies;
        statistics::Scalar descriptorSpartaFusedFp64Adds;
        statistics::Scalar descriptorSpartaFusedTallyZeroReads;
        statistics::Scalar descriptorSpartaFusedWritesAcknowledged;
        statistics::Scalar descriptorSpartaFusedPairBankAccesses;
        statistics::Scalar descriptorSpartaFusedPairBankConflictCycles;
        statistics::Scalar descriptorUmeCornersClassified;
        statistics::Scalar descriptorUmeActiveCorners;
        statistics::Scalar descriptorUmeInactiveCorners;
        statistics::Scalar descriptorUmeCornersValidated;
        statistics::Scalar descriptorUmeZoneFieldGathers;
        statistics::Scalar descriptorUmeOutputZeroReads;
        statistics::Scalar descriptorUmeFp32Multiplies;
        statistics::Scalar descriptorUmeUpdatesAcknowledged;
        statistics::Scalar descriptorUmtGroupsLoaded;
        statistics::Scalar descriptorUmtInputReads;
        statistics::Scalar descriptorUmtInputLineReads;
        statistics::Scalar descriptorUmtFp64AddSubOperations;
        statistics::Scalar descriptorUmtFp64MultiplyOperations;
        statistics::Scalar descriptorUmtFp64DivideOperations;
        statistics::Scalar descriptorUmtBatches;
        statistics::Scalar descriptorUmtBatchCycles;
        statistics::Scalar descriptorUmtResultsComputed;
        statistics::Scalar descriptorUmtSidecarWrites;
        statistics::Scalar descriptorUmtSidecarReads;
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
    const size_t lineBanks;
    const size_t logicalAdmissionWidth;
    const size_t lineIssueWidth;
    const size_t retirementWidth;
    const bool modelPayloadOverlayPorts;
    const size_t lineBytes;
    LineTableGeometry lineTableGeometry;
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
    std::unique_ptr<OperationPayloadPortModel> payloadPortModel;
    SharedOverlayModeBarrier sharedOverlayBarrier;
    bool descriptorOwnsSharedOverlay = false;

    std::vector<Operation> operations;
    SpartaPairedSummaryStore spartaFusedSummaries;
    std::array<bool, SpartaFusedActiveContexts> spartaFusedContextSlots{};
    std::vector<LineEntry> lines;
    std::vector<UpdateEntry> updates;
    size_t nextAdmission = 0;
    size_t nextRetirement = 0;
    size_t nextVerification = 0;
    size_t activeOperations = 0;
    size_t activeContexts = 0;
    size_t activeFaceComputations = 0;
    size_t activeBransonEventComputations = 0;
    size_t payloadRetirementGrants = 0;
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
    SpartaFusedDescriptor spartaFusedDescriptor;
    SpartaFusedPhase spartaFusedPhase = SpartaFusedPhase::Inactive;
    UmeGradzatpDescriptor umeGradzatp;
    UmeGradzatpPhase umeGradzatpPhase = UmeGradzatpPhase::Inactive;
    UmtFusedCornerDescriptor umtFusedCorner;
    UmtMixedCornerDescriptor umtMixedCorner;
    UmtOrderedWaveDescriptor umtOrderedWave;
    UmtFusedCornerPhase umtFusedCornerPhase =
        UmtFusedCornerPhase::Inactive;
    bool umtMixedCornerActive = false;
    bool umtOrderedWaveActive = false;
    bool umtMixedSidecarReadsQueued = false;
    UmtMixedCornerSidecarPortModel umtMixedSidecarPorts;
    std::vector<UmtOrderedWaveRecord> umtOrderedWaveRecords;
    std::vector<std::array<uint64_t, UmtOrderedWaveCorners>>
        umtOrderedWaveResults;
    UmtOrderedWaveCompletionCursor umtOrderedWaveResultCursor;
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
    uint64_t spartaFusedVisitedParticles = 0;
    uint32_t spartaFusedVisitedCount = 0;
    uint64_t spartaFusedTallyZeroReads = 0;
    uint64_t spartaFusedWritesAcknowledged = 0;
    uint64_t umeCornersClassified = 0;
    uint64_t umeActiveCorners = 0;
    uint64_t umeCornersValidated = 0;
    uint64_t umeUpdatesAcknowledged = 0;
    uint64_t umtFusedBatchReadyCycle = 0;
    uint64_t umtFusedResultsComputed = 0;
    size_t spartaFusedIssueCursor = 0;
    uint8_t spartaFusedWriteChannel = 0;
    size_t descriptorFetchOffset = 0;
    std::array<uint8_t, SpartaFusedDescriptorBytes> descriptorFetchBuffer{};
    std::array<uint8_t, UmtOrderedWaveDescriptorBytes>
        umtOrderedWaveFetchBuffer{};
    bool descriptorFaceUpdatePhase = false;
    PacketPtr descriptorPacket = nullptr;
    PacketPtr addressVectorPacket = nullptr;
    PacketPtr resultPacket = nullptr;
    PacketPtr completionPacket = nullptr;

    Addr lineAddress(Addr address) const;
    LineEntry *matchingLine(Addr address);
    LineEntry *freeLine(Addr address);
    void recordLineTableHighWaterMark();
    size_t updateBank(Addr address) const;
    UpdateEntry *matchingUpdate(Addr address);
    UpdateEntry *accumulatingUpdate(Addr address);
    UpdateEntry *freeUpdate(Addr address);
    UpdateEntry *drainableUpdate(Addr address);
    UpdateEntry *updateForPacket(PacketPtr packet);
    bool allUpdateEntriesFree() const;
    size_t updateGenerationCount(Addr address) const;
    uint8_t spartaCellGroupSize(size_t operationIndex) const;
    bool spartaCellGroupComplete(const UpdateEntry &entry) const;
    bool updateGenerationDrainBlocked(const UpdateEntry &entry) const;
    bool activeDependentMode() const;
    bool bransonEventDescriptor() const;
    bool spartaTallyDescriptor() const;
    bool spartaFusedCellDescriptor() const;
    bool umeGradzatpDescriptor() const;
    bool umtCornerDescriptor() const;
    bool umtFusedCornerDescriptor() const;
    bool umtMixedCornerDescriptor() const;
    bool umtOrderedWaveDescriptor() const;
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
    Addr spartaFusedChildAddress(
        const Operation &operation, uint64_t fieldOffset) const;
    Addr spartaFusedParticleAddress(
        const Operation &operation, uint64_t fieldOffset) const;
    Addr spartaFusedTallyAddress(const Operation &operation) const;
    SpartaPairedSummaryStore::Entry &
    spartaFusedSummary(Operation &operation);
    const SpartaPairedSummaryStore::Entry &
    spartaFusedSummary(const Operation &operation) const;
    static bool spartaFusedSummaryAccess(const Operation &operation);
    void allocateSpartaFusedContext(Operation &operation);
    void releaseSpartaFusedContext(Operation &operation);
    DescriptorError beginSpartaFusedParticle(Operation &operation);
    DescriptorError finishSpartaFusedParticle(Operation &operation);
    DescriptorError consumeSpartaFusedResponse(
        Operation &operation, const uint8_t *data, size_t offset);
    void beginSpartaFusedTallyValidation();
    uint64_t expectedSpartaFusedWrites() const;
    Addr umeGradzatpReadAddress(const Operation &operation) const;
    Addr umeGradzatpUpdateAddress(const Operation &operation) const;
    void beginUmeGradzatpUpdatePhase();
    Addr umtFusedCornerReadAddress(const Operation &operation) const;
    UpdateEntry &umtMixedSidecarEntry(uint32_t context, uint32_t word);
    const UpdateEntry &umtMixedSidecarEntry(
        uint32_t context, uint32_t word) const;
    void clearUmtMixedSidecar();
    void progressUmtFusedCornerBatch();
    bool faceMinMaxDescriptor() const;
    UpdateKind configuredUpdateKind() const;
    bool floatingUpdate() const;
    bool strictFloatingUpdate() const;
    static bool floatingUpdate(UpdateKind kind);
    static bool fp32Update(UpdateKind kind);
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
    static uint64_t encodeFloat(float value);
    static float decodeFloat(uint64_t bits);
    void tagRequest(
        PacketPtr packet, TrafficKind kind, PacketPtr *retainedPacket);
    TrafficKind acceptResponse(PacketPtr packet);
    void discardUnsentRequest(PacketPtr &packet);
    DescriptorError acquireSharedOverlay(
        const SharedOverlayReservation &reservation);
    static bool sharedOverlayTrafficKind(
        TrafficKind kind, SharedOverlayTrafficKind &overlayKind);
    void recordSharedOverlayTraffic(TrafficKind kind);
    void acknowledgeSharedOverlayTraffic(TrafficKind kind);
    void beginSharedOverlayDrain();
    void releaseSharedOverlay();
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
    void servicePayloadOverlayPorts();
    void resetPayloadOverlayPorts(bool allowDiscard);
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
