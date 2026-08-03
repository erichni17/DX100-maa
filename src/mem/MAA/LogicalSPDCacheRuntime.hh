#ifndef __MEM_MAA_LOGICAL_SPD_CACHE_RUNTIME_HH__
#define __MEM_MAA_LOGICAL_SPD_CACHE_RUNTIME_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <exception>
#include <limits>
#include <type_traits>

#include "mem/MAA/LogicalSPDCacheDatapath.hh"
#include "mem/MAA/LogicalSPDCacheSlice.hh"
#include "mem/MAA/LogicalSPDCacheTransport.hh"

namespace gem5 {

/**
 * Standalone production authority for the logical SPD-cache slice.
 *
 * This object owns the scheduler/controller Slice, the physical Transport,
 * the captured-scalar Datapath, and exactly two private 32-KiB payload slots.
 * No bridge may publish a Slice page or compute completion directly.  The
 * eventual gem5 bridge must convert a transition to ProductionStop/Poisoned
 * into panic/fatal; host tests retain the persistent fail-closed state so they
 * can inspect it without forging cleanup.
 */
class LogicalSPDCacheRuntime
{
  public:
    using Slice = LogicalSPDCacheSlice;
    using Transport = LogicalSPDCacheTransport;
    using Datapath = LogicalSPDCacheDatapath;

    /**
     * Field-mapped packed semantic-state lower bound.
     *
     * Every constant below names reachable information in an actual member
     * of Controller, Slice, Transport, or Runtime.  Bounded host pointers are
     * encoded as their fixed record/credit/slot selector, not as host virtual
     * addresses.  Slice::Counters is explicitly excluded: it is saturating
     * simulation instrumentation and is not required to operate the cache.
     * Datapath is owned below but is stateless.  This is neither area nor a
     * synthesized-size claim; host sizeof is reported independently.
     */
    struct PackedSemanticLedger
    {
        // LogicalSPDCacheController::Descriptor, twice.
        static constexpr std::size_t ControllerDescriptorAllocated = 1;
        static constexpr std::size_t ControllerDescriptorGeneration = 32;
        static constexpr std::size_t ControllerDescriptorReady = 4;
        static constexpr std::size_t ControllerDescriptors =
            2 * (ControllerDescriptorAllocated +
                 ControllerDescriptorGeneration +
                 ControllerDescriptorReady);

        // LogicalSPDCacheController::Slot, twice.
        static constexpr std::size_t ControllerSlotPhase = 3;
        static constexpr std::size_t ControllerPageIdentity = 1 + 2 + 32;
        static constexpr std::size_t ControllerSlotTransaction = 64;
        static constexpr std::size_t ControllerSlotWritebackTransaction = 64;
        static constexpr std::size_t ControllerSlotPublish = 1;
        static constexpr std::size_t ControllerSlots =
            2 * (ControllerSlotPhase + ControllerPageIdentity +
                 ControllerSlotTransaction +
                 ControllerSlotWritebackTransaction +
                 ControllerSlotPublish);

        // missQueue[4], LeaseRecord[4], queueSize, lastMemorySerial.
        static constexpr std::size_t ControllerMissQueue =
            4 * ControllerPageIdentity;
        static constexpr std::size_t ControllerLeaseActive = 1;
        static constexpr std::size_t ControllerLeaseSlot = 2;
        static constexpr std::size_t ControllerLeaseSerial = 64;
        static constexpr std::size_t ControllerLeasePage =
            ControllerPageIdentity;
        static constexpr std::size_t ControllerLeasePurpose = 2;
        static constexpr std::size_t ControllerLeaseOverwriteSerial = 64;
        static constexpr std::size_t ControllerLeases =
            4 * (ControllerLeaseActive + ControllerLeaseSlot +
                 ControllerLeaseSerial + ControllerLeasePage +
                 ControllerLeasePurpose +
                 ControllerLeaseOverwriteSerial);
        static constexpr std::size_t ControllerQueueSize = 3;
        static constexpr std::size_t ControllerLastMemorySerial = 64;
        static constexpr std::size_t ControllerBits =
            ControllerDescriptors + ControllerSlots + ControllerMissQueue +
            ControllerLeases + ControllerQueueSize +
            ControllerLastMemorySerial;

        // LogicalSPDCacheSlice::DescriptorRecord[2].
        static constexpr std::size_t SliceDescriptorRole = 2;
        static constexpr std::size_t SliceDescriptorHandle = 1 + 32;
        static constexpr std::size_t SliceBackingSpan = 64 + 32;
        static constexpr std::size_t SliceProducerTransaction = 64;
        static constexpr std::size_t SliceDataType = 8;
        static constexpr std::size_t SliceBackingReady = 8;
        static constexpr std::size_t SliceWritebackAcked = 8;
        static constexpr std::size_t SliceDescriptors =
            2 * (SliceDescriptorRole + SliceDescriptorHandle +
                 SliceBackingSpan + SliceProducerTransaction +
                 SliceDataType + SliceBackingReady + SliceWritebackAcked);

        // OverwriteReservation and ActiveOperation (all duplicated fields).
        static constexpr std::size_t SliceLease =
            3 + 64 + ControllerPageIdentity;
        static constexpr std::size_t SliceOverwriteReservation =
            2 * SliceLease + 2 + 2 + 64 + 64;
        static constexpr std::size_t SliceStage = 3;
        static constexpr std::size_t SliceActiveOperation =
            1 + 32 + 2 * SliceDescriptorHandle + 3 + 64 + 2 +
            SliceStage + SliceOverwriteReservation;

        // acceptedMemoryAction stores PageAction plus MemoryAction duplicate.
        static constexpr std::size_t SliceControllerMemoryAction =
            2 + 2 + 64 + 2 * ControllerPageIdentity + 1;
        static constexpr std::size_t SlicePageAction =
            1 + 1 + 1 + 32 + 2 + 1 + 64 + 64 +
            SliceControllerMemoryAction;
        static constexpr std::size_t SliceMemoryActionActive = 1;
        static constexpr std::size_t SliceRefillIdentity =
            ControllerPageIdentity;
        static constexpr std::size_t SliceRefillPending = 1;
        static constexpr std::size_t SliceLastOperationID = 32;
        static constexpr std::size_t SliceLastProducerTransaction = 64;
        static constexpr std::size_t SliceMaaID = 16;
        static constexpr std::size_t SliceInitialized = 1;
        static constexpr std::size_t SliceDraining = 1;
        static constexpr std::size_t SliceSealed = 1;
        static constexpr std::size_t SlicePoisoned = 1;
        static constexpr std::size_t SliceAbortRequested = 1;
        static constexpr std::size_t SliceAbortCompletion = 1;
        static constexpr std::size_t SliceAbortCode = 1;
        static constexpr std::size_t SliceInstrumentationCounterBits =
            12 * 64;
        static constexpr std::size_t SliceBits =
            ControllerBits + SliceDescriptors + SliceActiveOperation +
            SlicePageAction + SliceMemoryActionActive +
            SliceRefillIdentity + SliceRefillPending +
            SliceLastOperationID + SliceLastProducerTransaction +
            SliceMaaID + SliceInitialized + SliceDraining + SliceSealed +
            SlicePoisoned + SliceAbortRequested + SliceAbortCompletion +
            SliceAbortCode;

        // LogicalSPDCacheTransport::TransactionRecord[8].
        static constexpr std::size_t TransportRecordState = 3;
        static constexpr std::size_t TransportRecordEpoch = 16;
        static constexpr std::size_t TransportRecordActionID = 32;
        static constexpr std::size_t TransportTransactionKey =
            1 + 32 + 1 + 2 + 9 + 1;
        static constexpr std::size_t TransportRouteToken = 4 + 16 + 32;
        static constexpr std::size_t TransportRequestIdentity = 32;
        static constexpr std::size_t TransportRequestPacket =
            32 + 4 + 4 + 8 + 2 + 64 + 3 + 16 + 3 + 7;
        static constexpr std::size_t TransportRecordAddress = 64;
        static constexpr std::size_t TransportExpectedResponse = 3;
        static constexpr std::size_t TransportRecordPort = 2;
        static constexpr std::size_t TransportRecordCredit = 3;
        static constexpr std::size_t TransportRecordFlags = 3;
        static constexpr std::size_t TransportRecords =
            8 * (TransportRecordState + TransportRecordEpoch +
                 TransportRecordActionID + TransportTransactionKey +
                 TransportRouteToken + TransportRequestIdentity +
                 TransportRequestPacket + TransportRecordAddress +
                 TransportExpectedResponse + TransportRecordPort +
                 TransportRecordCredit + TransportRecordFlags);

        // RequestFifo, pending, creditOwners, and four exact line buffers.
        static constexpr std::size_t TransportFifoEntries = 8 * 4;
        static constexpr std::size_t TransportFifoHead = 3;
        static constexpr std::size_t TransportFifoTail = 3;
        static constexpr std::size_t TransportFifoCount = 4;
        static constexpr std::size_t TransportFifo =
            TransportFifoEntries + TransportFifoHead + TransportFifoTail +
            TransportFifoCount;
        static constexpr std::size_t TransportPending = 4;
        static constexpr std::size_t TransportCreditOwners = 4 * 4;
        static constexpr std::size_t TransportLineBuffers = 4 * 64 * 8;

        // Transport PageAction, including exact Slice controller serial.
        static constexpr std::size_t TransportActionState = 2;
        static constexpr std::size_t TransportActionID = 32;
        static constexpr std::size_t TransportActionOperation = 1;
        static constexpr std::size_t TransportActionDescriptor = 1;
        static constexpr std::size_t TransportActionGeneration = 32;
        static constexpr std::size_t TransportActionPage = 2;
        static constexpr std::size_t TransportActionSlot = 1;
        static constexpr std::size_t TransportActionBase = 64;
        static constexpr std::size_t TransportActionControllerSerial = 64;
        static constexpr std::size_t TransportActionSpanBinding = 2;
        static constexpr std::size_t TransportActionSpanSize = 1;
        static constexpr std::size_t TransportActionNextLine = 10;
        static constexpr std::size_t TransportActionIssued = 512;
        static constexpr std::size_t TransportActionAcked = 512;
        static constexpr std::size_t TransportActionAckCount = 10;
        static constexpr std::size_t TransportActionAbortCode = 1;
        static constexpr std::size_t TransportPageAction =
            TransportActionState + TransportActionID +
            TransportActionOperation + TransportActionDescriptor +
            TransportActionGeneration + TransportActionPage +
            TransportActionSlot + TransportActionBase +
            TransportActionControllerSerial + TransportActionSpanBinding +
            TransportActionSpanSize + TransportActionNextLine +
            TransportActionIssued + TransportActionAcked +
            TransportActionAckCount + TransportActionAbortCode;

        // Remaining Transport global members, including duplicated budgets.
        static constexpr std::size_t TransportNextActionID = 32;
        static constexpr std::size_t TransportActionIDsExhausted = 1;
        static constexpr std::size_t TransportNextIncarnationID = 32;
        static constexpr std::size_t TransportIncarnationIDsExhausted = 1;
        static constexpr std::size_t TransportCopyActive = 1;
        static constexpr std::size_t TransportSealed = 1;
        static constexpr std::size_t TransportGeometryValid = 1;
        static constexpr std::size_t TransportPoisoned = 1;
        static constexpr std::size_t TransportRemainingBudget = 3 * 32;
        static constexpr std::size_t TransportBits =
            TransportRecords + TransportFifo + TransportPending +
            TransportCreditOwners + TransportLineBuffers +
            TransportPageAction + TransportNextActionID +
            TransportActionIDsExhausted + TransportNextIncarnationID +
            TransportIncarnationIDsExhausted + TransportCopyActive +
            TransportSealed + TransportGeometryValid +
            TransportPoisoned + TransportRemainingBudget;

        // Runtime correlations duplicate exact Slice actions by design.
        static constexpr std::size_t RuntimePageCorrelation =
            1 + 1 + 32 + SlicePageAction;
        static constexpr std::size_t SliceComputeAction =
            1 + 32 + 64 + 2 * ControllerPageIdentity + 1 + 1 + 3 + 64;
        static constexpr std::size_t RuntimeComputeCorrelation =
            1 + SliceComputeAction;
        static constexpr std::size_t RuntimeAbortRequested = 1;
        static constexpr std::size_t RuntimePoisoned = 1;
        static constexpr std::size_t RuntimeSealed = 1;
        static constexpr std::size_t RuntimeCorrelationBits =
            RuntimePageCorrelation + RuntimeComputeCorrelation +
            RuntimeAbortRequested + RuntimePoisoned + RuntimeSealed;
        static constexpr std::size_t DatapathBits = 0;
        static constexpr std::size_t PrivatePayloadBits =
            2 * 32 * 1024 * 8;

        static constexpr std::size_t PackedBits =
            SliceBits + TransportBits + RuntimeCorrelationBits +
            DatapathBits + PrivatePayloadBits;
        static constexpr std::size_t PackedBytes = (PackedBits + 7) / 8;
        static constexpr std::size_t PythonReferenceLowerBoundBytes = 66181;
    };

    static_assert(PackedSemanticLedger::ControllerBits == 1287);
    static_assert(PackedSemanticLedger::SliceActiveOperation == 507);
    static_assert(PackedSemanticLedger::SliceBits == 2693);
    static_assert(PackedSemanticLedger::TransportBits == 6715);
    static_assert(PackedSemanticLedger::RuntimeCorrelationBits == 579);
    static_assert(PackedSemanticLedger::PackedBits == 534275);
    static_assert(PackedSemanticLedger::PackedBytes == 66785);

    struct ConstPageSpan
    {
        const std::byte *data = nullptr;
        std::size_t size = 0;
    };

    struct CorrelationSnapshot
    {
        bool pageActive = false;
        bool computeActive = false;
        bool abortFlush = false;
        bool abortRequested = false;
        bool poisoned = false;
        uint32_t transportActionID = 0;
        Slice::PageAction pageAction{};
        Slice::ComputeAction computeAction{};
    };

    explicit LogicalSPDCacheRuntime(
        std::size_t ports = Transport::PortCount,
        std::size_t lineBytes = Transport::LineBytes)
        : transport(ports, lineBytes)
    {}

    LogicalSPDCacheRuntime(std::size_t ports, std::size_t lineBytes,
                           Transport::IdBudget budget)
        : transport(ports, lineBytes, budget)
    {}

    ~LogicalSPDCacheRuntime()
    {
        if (!destructionSafe())
            std::terminate();
    }

    LogicalSPDCacheRuntime(const LogicalSPDCacheRuntime &) = delete;
    LogicalSPDCacheRuntime &operator=(const LogicalSPDCacheRuntime &) =
        delete;

    Slice::Status initialize(uint16_t maaID)
    {
        const Slice::Status mutation = controlMutationStatus();
        if (mutation != Slice::Status::Accepted)
            return mutation;
        if (!transport.geometryValid())
            return Slice::Status::Invalid;
        return slice.initialize(maaID);
    }

    Slice::Status registerSource(
        uint8_t logical, Slice::BackingSpan backing,
        uint8_t dataType = Slice::Float64DataType)
    {
        const Slice::Status mutation = controlMutationStatus();
        return mutation != Slice::Status::Accepted
                   ? mutation
                   : slice.registerSource(logical, backing, dataType);
    }

    Slice::Status admit(const Slice::Admission &request)
    {
        const Slice::Status mutation = controlMutationStatus();
        return mutation != Slice::Status::Accepted ? mutation
                                                   : slice.admit(request);
    }

    Slice::Status queueRefill(uint8_t logical, uint8_t page)
    {
        const Slice::Status mutation = controlMutationStatus();
        return mutation != Slice::Status::Accepted
                   ? mutation
                   : slice.queueRefill(logical, page);
    }

    Transport::Result prepare(
        Transport::FaultPoint fault = Transport::FaultPoint::None)
    {
        if (!Transport::validFaultPoint(fault))
            return {Transport::Status::Invalid};
        const Transport::Status mutation = networkMutationStatus();
        if (mutation != Transport::Status::Accepted)
            return {mutation};
        const bool completionFault =
            fault == Transport::FaultPoint::FinalCompletionIdentity;
        if (completionFault &&
            (runtimeAbortRequested || pageCorrelation.active)) {
            return {Transport::Status::Invalid};
        }
        if (runtimeAbortRequested && !pageCorrelation.active) {
            const Slice::Status progressed = progressAbort();
            if (progressed == Slice::Status::ProductionStop ||
                progressed == Slice::Status::Poisoned) {
                return {Transport::Status::ProductionStop};
            }
            if (!pageCorrelation.active)
                return {Transport::Status::NoWork};
        }
        const Transport::Status ready = ensurePageAction(
            false, completionFault ? fault : Transport::FaultPoint::None);
        if (ready != Transport::Status::Accepted)
            return {ready};
        Transport::Result result = transport.prepare(
            slotSpan(pageCorrelation.action.slot),
            completionFault ? Transport::FaultPoint::None : fault);
        if (transport.poisoned())
            poisonAuthority();
        return result;
    }

    Transport::Result sendPrepared(bool accepted)
    {
        const Transport::Status mutation = networkMutationStatus();
        if (mutation != Transport::Status::Accepted)
            return {mutation};
        if (!pageCorrelation.active)
            return {Transport::Status::NoWork};
        Transport::Result result = transport.sendPrepared(accepted);
        if (transport.poisoned())
            poisonAuthority();
        return result;
    }

    Transport::Result trySend(
        bool accepted,
        Transport::FaultPoint fault = Transport::FaultPoint::None)
    {
        const Transport::Result prepared = prepare(fault);
        if (prepared.status != Transport::Status::Accepted)
            return prepared;
        return sendPrepared(accepted);
    }

    Transport::Status recvReqRetry(uint8_t callbackPort)
    {
        const Transport::Status mutation = networkMutationStatus();
        if (mutation != Transport::Status::Accepted)
            return mutation;
        const Transport::Status status =
            transport.recvReqRetry(callbackPort);
        if (transport.poisoned())
            poisonAuthority();
        return status;
    }

    Transport::Result receive(Transport::ReturnedHandle &returned,
                              uint8_t callbackPort)
    {
        const Transport::Status mutation = networkMutationStatus();
        if (mutation != Transport::Status::Accepted)
            return {mutation};
        const Transport::CompletionIdentity completion =
            transport.precommitReceive(returned, callbackPort);
        if (completion.valid() && !completionExact(completion))
            return poisonTransportResult();
        const Transport::CompletionAuthority authority(completion);
        Transport::Result result = transport.receiveAuthorized(
            returned, callbackPort, authority);
        if (transport.poisoned()) {
            poisonAuthority();
            return result;
        }
        if (result.status == Transport::Status::Completed)
            authenticateCompletion(result);
        else if (result.status == Transport::Status::AbortDrained &&
                 runtimeAbortRequested && pageCorrelation.active &&
                 !pageCorrelation.abortFlush)
            finishTransportAbort();
        return result;
    }

    Transport::Result commitDelivery(
        const Transport::DeliveryTicket &ticket,
        Transport::CopyHook hook = nullptr, void *context = nullptr)
    {
        const Transport::Status mutation = networkMutationStatus();
        if (mutation != Transport::Status::Accepted)
            return {mutation};
        if (!pageCorrelation.active)
            return poisonTransportResult();
        const Transport::PageSpan destination =
            slotSpan(pageCorrelation.action.slot);
        const Transport::CompletionIdentity completion =
            transport.precommitDelivery(ticket, destination);
        if (completion.valid() && !completionExact(completion))
            return poisonTransportResult();
        const Transport::CompletionAuthority authority(completion);
        Transport::Result result = transport.commitDeliveryAuthorized(
            ticket, destination, hook, context, authority);
        if (transport.poisoned()) {
            poisonAuthority();
            if (result.status != Transport::Status::ProductionStop)
                result.status = Transport::Status::Poisoned;
            return result;
        }
        if (result.status == Transport::Status::Completed)
            authenticateCompletion(result);
        return result;
    }

    Slice::Status beginCompute()
    {
        const Slice::Status mutation = controlMutationStatus();
        if (mutation != Slice::Status::Accepted)
            return mutation;
        if (runtimeAbortRequested || computeCorrelation.active ||
            pageCorrelation.active)
            return Slice::Status::Busy;
        const Slice::ComputeAction action = slice.pendingCompute();
        if (!action.valid)
            return Slice::Status::NotReady;
        const Slice::Status accepted = slice.acceptCompute(action);
        if (accepted != Slice::Status::Accepted)
            return poisonSliceStatus();
        computeCorrelation.active = true;
        computeCorrelation.action = action;
        return Slice::Status::Accepted;
    }

    Slice::Status executeCompute()
    {
        const Slice::Status mutation = controlMutationStatus();
        if (mutation != Slice::Status::Accepted)
            return mutation;
        if (!computeCorrelation.active)
            return Slice::Status::NotReady;
        const Slice::ComputeAction action = computeCorrelation.action;
        if (action.sourceSlot >= Slice::Slots ||
            action.destinationSlot >= Slice::Slots ||
            action.sourceSlot == action.destinationSlot)
            return poisonSliceStatus();
        Datapath::Operation operation;
        switch (action.operation) {
          case Slice::Operation::Add:
            operation = Datapath::Operation::Add;
            break;
          case Slice::Operation::Sub:
            operation = Datapath::Operation::Sub;
            break;
          case Slice::Operation::Mul:
            operation = Datapath::Operation::Mul;
            break;
          case Slice::Operation::Div:
            operation = Datapath::Operation::Div;
            break;
          case Slice::Operation::Min:
            operation = Datapath::Operation::Min;
            break;
          case Slice::Operation::Max:
            operation = Datapath::Operation::Max;
            break;
          default:
            return poisonSliceStatus();
        }
        const double *source = slots[action.sourceSlot].data();
        double *destination = slots[action.destinationSlot].data();
        if (datapath.transform(
                operation, {source, Slice::PageElements},
                {destination, Slice::PageElements}, action.scalarBits) !=
            Datapath::Result::Accepted) {
            return poisonSliceStatus();
        }
        if (slice.completeCompute(action) != Slice::Status::Accepted)
            return poisonSliceStatus();
        computeCorrelation = ComputeCorrelation{};
        return Slice::Status::Accepted;
    }

    Slice::Status driveCompute()
    {
        const Slice::Status begun = beginCompute();
        return begun == Slice::Status::Accepted ? executeCompute() : begun;
    }

    Slice::Status abort(Slice::AbortCode code)
    {
        const Slice::Status mutation = controlMutationStatus();
        if (mutation != Slice::Status::Accepted)
            return mutation;
        if (!Slice::validAbortCode(code))
            return Slice::Status::Invalid;
        if (runtimeAbortRequested)
            return Slice::Status::Accepted;
        const Slice::Status begun = slice.beginAbort(code);
        if (begun != Slice::Status::Accepted)
            return begun;
        runtimeAbortRequested = true;
        return progressAbort();
    }

    Slice::Status progressAbort()
    {
        const Slice::Status mutation = controlMutationStatus();
        if (mutation != Slice::Status::Accepted)
            return mutation;
        if (!runtimeAbortRequested)
            return Slice::Status::Stale;
        if (pageCorrelation.active) {
            if (pageCorrelation.abortFlush)
                return Slice::Status::Busy;
            if (!transport.drained()) {
                const Transport::Status status =
                    transport.abortAction(Transport::AbortCode::Caller);
                if (status != Transport::Status::Accepted &&
                    status != Transport::Status::AbortDrained &&
                    status != Transport::Status::AlreadyDrained) {
                    return poisonSliceStatus();
                }
                if (!transport.drained())
                    return Slice::Status::Busy;
            }
            return finishTransportAbort();
        }
        if (computeCorrelation.active) {
            const Slice::Status canceled = slice.cancelUnacceptedForAbort();
            if (canceled != Slice::Status::Accepted)
                return poisonSliceStatus();
            computeCorrelation = ComputeCorrelation{};
            return completeRuntimeAbortIfReady();
        }
        const Slice::Status canceled = slice.cancelUnacceptedForAbort();
        if (canceled == Slice::Status::Busy) {
            const Transport::Status started = ensurePageAction(true);
            return started == Transport::Status::Accepted
                       ? Slice::Status::Busy
                       : poisonSliceStatus();
        }
        if (canceled != Slice::Status::Accepted)
            return poisonSliceStatus();
        return completeRuntimeAbortIfReady();
    }

    Slice::Status requestDrain()
    {
        const Slice::Status mutation = controlMutationStatus();
        return mutation != Slice::Status::Accepted ? mutation
                                                   : slice.requestDrain();
    }

    Slice::Status resumeAfterDrain()
    {
        const Slice::Status mutation = controlMutationStatus();
        if (mutation != Slice::Status::Accepted)
            return mutation;
        if (!drained())
            return Slice::Status::Busy;
        return slice.resumeAfterDrain();
    }

    Slice::Status reset()
    {
        const Slice::Status mutation = controlMutationStatus();
        if (mutation != Slice::Status::Accepted)
            return mutation;
        if (!drained())
            return Slice::Status::Busy;
        if (transport.reset() != Transport::Status::Accepted)
            return poisonSliceStatus();
        const Slice::Status status = slice.reset();
        if (status != Slice::Status::Accepted)
            return status;
        for (auto &slot : slots)
            slot.fill(0.0);
        return Slice::Status::Accepted;
    }

    Slice::Status teardown()
    {
        const Slice::Status mutation = controlMutationStatus();
        if (mutation != Slice::Status::Accepted)
            return mutation;
        if (!drained())
            return Slice::Status::Busy;
        for (uint8_t logical = 0; logical < Slice::LogicalDescriptors;
             ++logical) {
            if (slice.descriptor(logical).role ==
                Slice::DescriptorRole::Free)
                continue;
            if (slice.cleanupDescriptor(logical) != Slice::Status::Accepted)
                return poisonSliceStatus();
        }
        if (slice.teardown() != Slice::Status::Accepted ||
            transport.seal() != Transport::Status::Accepted)
            return poisonSliceStatus();
        runtimeSealed = true;
        return Slice::Status::Accepted;
    }

    Slice::Status retireCompletedOperation()
    {
        const Slice::Status mutation = controlMutationStatus();
        return mutation != Slice::Status::Accepted
                   ? mutation
                   : slice.retireCompletedOperation();
    }

    bool operationComplete() const { return slice.operationComplete(); }
    bool descriptorComplete(uint8_t logical) const
    {
        return slice.descriptorComplete(logical);
    }
    bool abortCompleted() const { return slice.abortCompleted(); }
    bool poisoned() const
    {
        return terminalPoisoned || transport.poisoned() || slice.poisoned();
    }
    bool drained() const
    {
        return transport.drained() && slice.drained() &&
               !pageCorrelation.active && !computeCorrelation.active &&
               !runtimeAbortRequested;
    }
    bool destructionSafe() const
    {
        return transport.drained() && slice.destructionSafe() &&
               !pageCorrelation.active && !computeCorrelation.active;
    }
    bool geometryValid() const { return transport.geometryValid(); }
    bool sealed() const { return runtimeSealed; }

    Slice::AuditSnapshot sliceSnapshot() const
    {
        return slice.auditSnapshot();
    }
    Transport::AuditSnapshot transportSnapshot() const
    {
        return transport.auditSnapshot();
    }
    CorrelationSnapshot correlationSnapshot() const
    {
        CorrelationSnapshot snapshot;
        snapshot.pageActive = pageCorrelation.active;
        snapshot.computeActive = computeCorrelation.active;
        snapshot.abortFlush = pageCorrelation.abortFlush;
        snapshot.abortRequested = runtimeAbortRequested;
        snapshot.poisoned = poisoned();
        snapshot.transportActionID = pageCorrelation.transportActionID;
        snapshot.pageAction = pageCorrelation.action;
        snapshot.computeAction = computeCorrelation.action;
        return snapshot;
    }

    ConstPageSpan slotPayload(uint8_t slot) const
    {
        return slot < Slice::Slots
                   ? ConstPageSpan{byteView(slots[slot]),
                                   sizeof(PayloadSlot)}
                   : ConstPageSpan{};
    }

    const Transport::RequestPacket *pendingHandle() const
    {
        return transport.pendingHandle();
    }
    Transport::RecordState recordState(std::size_t record) const
    {
        return transport.recordState(record);
    }
    Transport::TransactionKey recordKey(std::size_t record) const
    {
        return transport.recordKey(record);
    }
    uint16_t ackCount() const { return transport.ackCount(); }
    std::size_t creditsInUse() const { return transport.creditsInUse(); }
    Transport::ActionState transportActionState() const
    {
        return transport.actionState();
    }
    bool transportDrained() const { return transport.drained(); }

  private:
    using PayloadSlot = std::array<double, Slice::PageElements>;

    static_assert(sizeof(PayloadSlot) == Slice::PageBytes);
    static_assert(alignof(PayloadSlot) == alignof(double));
    static_assert(std::is_trivially_copyable<double>::value);
    static_assert(std::numeric_limits<double>::is_iec559);

    static std::byte *byteView(PayloadSlot &slot)
    {
        return reinterpret_cast<std::byte *>(slot.data());
    }

    static const std::byte *byteView(const PayloadSlot &slot)
    {
        return reinterpret_cast<const std::byte *>(slot.data());
    }

    struct PageCorrelation
    {
        bool active = false;
        bool abortFlush = false;
        uint32_t transportActionID = 0;
        Slice::PageAction action{};
    };

    struct ComputeCorrelation
    {
        bool active = false;
        Slice::ComputeAction action{};
    };

    Transport::PageSpan slotSpan(uint8_t slot)
    {
        if (slot >= Slice::Slots)
            return {};
        return {byteView(slots[slot]), sizeof(PayloadSlot)};
    }

    Slice::Status controlMutationStatus()
    {
        if (terminalPoisoned)
            return Slice::Status::Poisoned;
        if (runtimeSealed)
            return Slice::Status::Sealed;
        if (transport.copyActive()) {
            poisonAuthority();
            return Slice::Status::ProductionStop;
        }
        return Slice::Status::Accepted;
    }

    Transport::Status networkMutationStatus()
    {
        if (terminalPoisoned)
            return Transport::Status::Poisoned;
        if (runtimeSealed)
            return Transport::Status::Sealed;
        if (transport.copyActive()) {
            poisonAuthority();
            return Transport::Status::ProductionStop;
        }
        return Transport::Status::Accepted;
    }

    void poisonAuthority()
    {
        terminalPoisoned = true;
        transport.poisonFromAuthority();
        (void)slice.productionStop();
    }

    Slice::Status poisonSliceStatus()
    {
        poisonAuthority();
        return Slice::Status::ProductionStop;
    }

    Transport::Result poisonTransportResult()
    {
        poisonAuthority();
        return {Transport::Status::ProductionStop};
    }

    Transport::Status ensurePageAction(
        bool abortFlush,
        Transport::FaultPoint constructionFault =
            Transport::FaultPoint::None)
    {
        if (terminalPoisoned)
            return Transport::Status::Poisoned;
        if (pageCorrelation.active) {
            return constructionFault == Transport::FaultPoint::None
                       ? Transport::Status::Accepted
                       : Transport::Status::Invalid;
        }
        if (!transport.drained())
            return Transport::Status::Busy;
        const Slice::PageAction action = slice.pendingPageAction();
        if (!action.valid)
            return Transport::Status::NoWork;
        const Transport::Operation operation =
            action.operation == Slice::PageOperation::Fill
                ? Transport::Operation::Fill
                : Transport::Operation::Writeback;
        uint32_t actionID = 0;
        const Transport::Status started = transport.startAction(
            operation, action.descriptor, action.generation, action.page,
            action.slot, action.baseAddress, action.serial,
            slotSpan(action.slot), &actionID, constructionFault);
        if (started != Transport::Status::Accepted) {
            if (transport.poisoned())
                poisonAuthority();
            return started;
        }
        if (slice.acceptPageAction(action) != Slice::Status::Accepted) {
            poisonAuthority();
            return Transport::Status::ProductionStop;
        }
        pageCorrelation.active = true;
        pageCorrelation.abortFlush = abortFlush;
        pageCorrelation.transportActionID = actionID;
        pageCorrelation.action = action;
        return Transport::Status::Accepted;
    }

    bool completionExact(
        const Transport::CompletionIdentity &completion) const
    {
        if (!completion.valid() || !pageCorrelation.active)
            return false;
        const Slice::PageAction &action = pageCorrelation.action;
        const Transport::Operation expected =
            action.operation == Slice::PageOperation::Fill
                ? Transport::Operation::Fill
                : Transport::Operation::Writeback;
        return completion.kind() == expected &&
               completion.id() == pageCorrelation.transportActionID &&
               completion.descriptorID() == action.descriptor &&
               completion.descriptorGeneration() == action.generation &&
               completion.pageID() == action.page &&
               completion.slotID() == action.slot &&
               completion.controllerSerial() == action.serial;
    }

    void authenticateCompletion(Transport::Result &result)
    {
        if (!completionExact(result.completion)) {
            poisonAuthority();
            result.status = Transport::Status::ProductionStop;
            return;
        }
        const Slice::PageAction completed = pageCorrelation.action;
        pageCorrelation = PageCorrelation{};
        if (slice.completePageAction(completed) != Slice::Status::Accepted) {
            poisonAuthority();
            result.status = Transport::Status::ProductionStop;
            return;
        }
        if (runtimeAbortRequested)
            (void)completeRuntimeAbortIfReady();
    }

    Slice::Status finishTransportAbort()
    {
        if (!pageCorrelation.active || pageCorrelation.abortFlush ||
            !transport.drained())
            return poisonSliceStatus();
        const bool wasWriteback =
            pageCorrelation.action.operation ==
            Slice::PageOperation::Writeback;
        const Slice::PageAction canceled = pageCorrelation.action;
        pageCorrelation = PageCorrelation{};
        if (slice.cancelAcceptedPageAction(canceled) !=
            Slice::Status::Accepted)
            return poisonSliceStatus();
        if (wasWriteback) {
            if (ensurePageAction(true) != Transport::Status::Accepted)
                return poisonSliceStatus();
            return Slice::Status::Busy;
        }
        return completeRuntimeAbortIfReady();
    }

    Slice::Status completeRuntimeAbortIfReady()
    {
        if (!slice.abortCompleted())
            return Slice::Status::Busy;
        runtimeAbortRequested = false;
        return Slice::Status::Accepted;
    }

    LogicalSPDCacheSlice slice{};
    LogicalSPDCacheTransport transport;
    LogicalSPDCacheDatapath datapath{};
    alignas(Transport::LineBytes)
        std::array<PayloadSlot, Slice::Slots> slots{};
    PageCorrelation pageCorrelation{};
    ComputeCorrelation computeCorrelation{};
    bool runtimeAbortRequested = false;
    bool terminalPoisoned = false;
    bool runtimeSealed = false;
};

static_assert(LogicalSPDCacheSlice::Slots == 2);
static_assert(LogicalSPDCacheSlice::PageBytes == 32 * 1024);

} // namespace gem5

#endif // __MEM_MAA_LOGICAL_SPD_CACHE_RUNTIME_HH__
