#ifndef __MEM_MAA_LOGICAL_SPD_CACHE_SLICE_HH__
#define __MEM_MAA_LOGICAL_SPD_CACHE_SLICE_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>

#include "mem/MAA/LogicalSPDCacheController.hh"
#include "mem/MAA/LogicalStreamResponse.hh"

namespace gem5 {

class LogicalSPDCacheSliceTestAccess;

class LogicalSPDCacheSlice
{
    friend class LogicalSPDCacheSliceTestAccess;

  public:
    using Controller = LogicalSPDCacheController<>;
    using PageIdentity = Controller::PageIdentity;
    using MemoryAction = Controller::MemoryAction;
    using OverwriteReservation = Controller::OverwriteReservation;

    static constexpr std::size_t LogicalDescriptors = 2;
    static constexpr std::size_t Pages = 4;
    static constexpr std::size_t Slots = 2;
    static constexpr std::size_t PageElements = 4096;
    static constexpr std::size_t ElementBytes = sizeof(uint64_t);
    static constexpr std::size_t PageBytes = PageElements * ElementBytes;
    static constexpr std::size_t LogicalBytes = Pages * PageBytes;
    static constexpr std::size_t CacheLineBytes = 64;
    static constexpr std::size_t LinesPerPage = PageBytes / CacheLineBytes;
    static constexpr std::size_t LineWindow = 8;
    static constexpr std::size_t OperationMemorySerials = Pages * 3;
    static constexpr uint8_t Float64DataType = 5;
    static constexpr uint8_t MaxScalarOperation = 5;

    enum class DescriptorRole : uint8_t
    {
        Free,
        Source,
        Destination,
    };

    enum class RegisterResult : uint8_t
    {
        Accepted,
        Busy,
        Invalid,
        SerialExhausted,
        GenerationExhausted,
        Draining,
    };

    enum class AdmitResult : uint8_t
    {
        Accepted,
        Busy,
        Invalid,
        SourceUnavailable,
        DestinationUnavailable,
        Overlap,
        SerialExhausted,
        GenerationExhausted,
        Draining,
    };

    enum class ComputeResult : uint8_t
    {
        Accepted,
        Stale,
        Invalid,
    };

    enum class CleanupResult : uint8_t
    {
        Accepted,
        Busy,
        Stale,
        Invalid,
    };

    enum class Stage : uint8_t
    {
        Idle,
        WaitingFill,
        ComputeReady,
        Computing,
        WaitingWriteback,
        Complete,
        Faulted,
    };

    struct BackingSpan
    {
        uint64_t base = 0;
        uint64_t rangeBegin = 0;
        uint64_t rangeEnd = 0;
        int8_t rangeID = -1;
    };

    struct DescriptorRecord
    {
        DescriptorRole role = DescriptorRole::Free;
        Controller::DescriptorHandle handle{};
        BackingSpan backing{};
        uint64_t producerTransaction = 0;
        uint8_t dataType = std::numeric_limits<uint8_t>::max();
    };

    struct Admission
    {
        uint16_t sourceLogical = 0;
        uint16_t destinationLogical = 0;
        BackingSpan destination{};
        uint8_t dataType = Float64DataType;
        uint8_t operation = 0;
        uint64_t scalarBits = 0;
    };

    struct PendingMemoryAction
    {
        bool valid = false;
        MemoryAction controller{};
        LogicalStreamTransactionTag tag{};
        uint64_t backingBase = 0;
        uint64_t rangeBegin = 0;
        uint64_t rangeEnd = 0;
        int8_t rangeID = -1;
    };

    struct PendingLine
    {
        bool valid = false;
        std::size_t index = 0;
        uint64_t virtualAddress = 0;
        LogicalStreamResponseKind kind = LogicalStreamResponseKind::Read;
    };

    struct ComputeAction
    {
        bool valid = false;
        uint64_t operationID = 0;
        uint64_t transactionID = 0;
        PageIdentity source{};
        PageIdentity destination{};
        uint16_t sourceSlot = Controller::NoSlot;
        uint16_t destinationSlot = Controller::NoSlot;
        uint8_t operation = 0;
        uint64_t scalarBits = 0;

        bool operator==(const ComputeAction &other) const
        {
            return valid == other.valid && operationID == other.operationID &&
                   transactionID == other.transactionID &&
                   source == other.source &&
                   destination == other.destination &&
                   sourceSlot == other.sourceSlot &&
                   destinationSlot == other.destinationSlot &&
                   operation == other.operation &&
                   scalarBits == other.scalarBits;
        }
    };

    struct Counters
    {
        uint64_t sourceRegistrations = 0;
        uint64_t admissions = 0;
        uint64_t admissionRejects = 0;
        uint64_t fillsAccepted = 0;
        uint64_t writebacksAccepted = 0;
        uint64_t lineIssues = 0;
        uint64_t lineResponses = 0;
        uint64_t responseRejects = 0;
        uint64_t computeIssues = 0;
        uint64_t computeCompletions = 0;
        uint64_t staleComputes = 0;
        uint64_t pagesCompleted = 0;
        uint64_t operationsCompleted = 0;
        uint64_t actionBackpressure = 0;
        uint64_t lineBackpressure = 0;
        uint64_t drainRequests = 0;
        uint64_t cleanupRejects = 0;
        uint64_t internalFaults = 0;
        uint64_t lineWindowHighWater = 0;
        uint64_t reserved = 0;
    };

    static_assert(sizeof(Counters) == 20 * sizeof(uint64_t));
    static_assert(LinesPerPage ==
                  LogicalStreamResponseLedger::MaxLinesPerPage);

    void initialize(uint16_t id)
    {
        if (maaInitialized)
            return;
        maaID = id;
        maaInitialized = true;
    }

    RegisterResult registerSource(uint16_t logical, const BackingSpan &span,
                                  uint8_t dataType)
    {
        if (draining)
            return RegisterResult::Draining;
        if (!maaInitialized || logical >= LogicalDescriptors ||
            dataType != Float64DataType || !validSpan(span)) {
            return RegisterResult::Invalid;
        }
        if (active.valid || descriptors[logical].role != DescriptorRole::Free)
            return RegisterResult::Busy;
        if (lastProducerTransaction ==
            std::numeric_limits<uint64_t>::max()) {
            return RegisterResult::SerialExhausted;
        }

        const auto reply = controller.allocate(logical);
        if (reply.status == Controller::AllocateStatus::GenerationExhausted)
            return RegisterResult::GenerationExhausted;
        if (reply.status == Controller::AllocateStatus::Busy)
            return RegisterResult::Busy;
        if (reply.status != Controller::AllocateStatus::Accepted)
            return RegisterResult::Invalid;

        DescriptorRecord record;
        record.role = DescriptorRole::Source;
        record.handle = reply.descriptor;
        record.backing = span;
        record.producerTransaction = ++lastProducerTransaction;
        record.dataType = dataType;
        descriptors[logical] = record;
        for (uint16_t page = 0; page < Pages; ++page) {
            const PageIdentity identity =
                controller.identity(reply.descriptor, page);
            if (controller.notifyPageReady(identity) !=
                Controller::ReadyResult::Accepted) {
                controller.freeDescriptor(reply.descriptor);
                descriptors[logical] = DescriptorRecord{};
                increment(stats.internalFaults);
                return RegisterResult::Invalid;
            }
        }
        increment(stats.sourceRegistrations);
        return RegisterResult::Accepted;
    }

    AdmitResult admit(const Admission &request)
    {
        if (draining)
            return rejectAdmission(AdmitResult::Draining);
        if (active.valid)
            return rejectAdmission(AdmitResult::Busy);
        if (!maaInitialized || request.sourceLogical >= LogicalDescriptors ||
            request.destinationLogical >= LogicalDescriptors ||
            request.sourceLogical == request.destinationLogical ||
            request.dataType != Float64DataType ||
            request.operation > MaxScalarOperation ||
            !validSpan(request.destination)) {
            return rejectAdmission(AdmitResult::Invalid);
        }

        const DescriptorRecord &source = descriptors[request.sourceLogical];
        if (source.role != DescriptorRole::Source ||
            source.dataType != request.dataType ||
            !allPagesReady(source.handle)) {
            return rejectAdmission(AdmitResult::SourceUnavailable);
        }
        if (descriptors[request.destinationLogical].role !=
                DescriptorRole::Free ||
            controller.descriptorAllocated(request.destinationLogical)) {
            return rejectAdmission(AdmitResult::DestinationUnavailable);
        }
        if (spansOverlap(source.backing, request.destination))
            return rejectAdmission(AdmitResult::Overlap);
        if (nextOperationID == std::numeric_limits<uint64_t>::max() ||
            !controller.canAllocateMemorySerials(OperationMemorySerials)) {
            return rejectAdmission(AdmitResult::SerialExhausted);
        }

        const auto destination =
            controller.allocate(request.destinationLogical);
        if (destination.status ==
            Controller::AllocateStatus::GenerationExhausted) {
            return rejectAdmission(AdmitResult::GenerationExhausted);
        }
        if (destination.status != Controller::AllocateStatus::Accepted)
            return rejectAdmission(AdmitResult::DestinationUnavailable);

        DescriptorRecord destinationRecord;
        destinationRecord.role = DescriptorRole::Destination;
        destinationRecord.handle = destination.descriptor;
        destinationRecord.backing = request.destination;
        destinationRecord.dataType = request.dataType;
        descriptors[request.destinationLogical] = destinationRecord;

        ++nextOperationID;
        active.valid = true;
        active.operationID = nextOperationID;
        active.source = source.handle;
        active.destination = destination.descriptor;
        active.operation = request.operation;
        active.scalarBits = request.scalarBits;
        active.page = 0;
        active.stage = Stage::WaitingFill;
        increment(stats.admissions);
        queueCurrentSource();
        return AdmitResult::Accepted;
    }

    PendingMemoryAction pendingMemoryAction() const
    {
        if (!active.valid || memoryActionActive ||
            (active.stage != Stage::WaitingFill &&
             active.stage != Stage::WaitingWriteback)) {
            return {};
        }
        const MemoryAction action = controller.pendingAction();
        if (action.kind == Controller::ActionKind::None)
            return {};
        const PageIdentity expected =
            active.stage == Stage::WaitingFill ? currentSourcePage()
                                                : currentDestinationPage();
        if (action.page != expected)
            return {};

        const DescriptorRecord &descriptor = descriptors[action.page.logical];
        PendingMemoryAction pending;
        pending.valid = true;
        pending.controller = action;
        pending.tag = makeTag(action);
        pending.backingBase =
            descriptor.backing.base + active.page * PageBytes;
        pending.rangeBegin = descriptor.backing.rangeBegin;
        pending.rangeEnd = descriptor.backing.rangeEnd;
        pending.rangeID = descriptor.backing.rangeID;
        return pending;
    }

    Controller::ActionResult
    acceptMemoryAction(const PendingMemoryAction &pending)
    {
        if (!pending.valid)
            return Controller::ActionResult::Invalid;
        const PendingMemoryAction expected = pendingMemoryAction();
        if (!samePendingMemory(expected, pending))
            return Controller::ActionResult::Stale;
        const auto result = controller.acceptAction(pending.controller);
        if (result != Controller::ActionResult::Accepted)
            return result;

        memoryActionActive = true;
        memoryAction = pending;
        nextLine = 0;
        outstandingLines = 0;
        const auto ledgerResult =
            memoryLedger.begin(pending.tag, LinesPerPage);
        if (ledgerResult != LogicalStreamResponseResult::Accepted) {
            active.stage = Stage::Faulted;
            increment(stats.internalFaults);
            return Controller::ActionResult::Invalid;
        }
        if (pending.controller.kind == Controller::ActionKind::Fill)
            increment(stats.fillsAccepted);
        else
            increment(stats.writebacksAccepted);
        return Controller::ActionResult::Accepted;
    }

    PendingLine pendingLine() const
    {
        if (!memoryActionActive || nextLine == LinesPerPage ||
            outstandingLines == LineWindow) {
            return {};
        }
        return {true, nextLine,
                memoryAction.backingBase + nextLine * CacheLineBytes,
                expectedLineKind()};
    }

    bool canIssueLine() const
    {
        return pendingLine().valid;
    }

    LogicalStreamResponseResult issueLine(std::size_t lineIndex,
                                          uint64_t lineAddress,
                                          LogicalStreamResponseKind kind)
    {
        if (!memoryActionActive)
            return rejectResponse(LogicalStreamResponseResult::Stale);
        if (outstandingLines == LineWindow) {
            increment(stats.lineBackpressure);
            return rejectResponse(LogicalStreamResponseResult::Invalid);
        }
        if (lineIndex != nextLine || nextLine == LinesPerPage ||
            lineAddress % CacheLineBytes != 0 ||
            kind != expectedLineKind()) {
            return rejectResponse(LogicalStreamResponseResult::Invalid);
        }

        LogicalStreamResponseResult result;
        if (kind == LogicalStreamResponseKind::Write) {
            result = memoryLedger.issueDirectWriteLine(
                memoryAction.tag, lineAddress);
        } else {
            result = memoryLedger.issueLine(memoryAction.tag, lineAddress,
                                            kind);
        }
        if (result != LogicalStreamResponseResult::Accepted)
            return rejectResponse(result, false);
        ++nextLine;
        ++outstandingLines;
        increment(stats.lineIssues);
        if (outstandingLines > stats.lineWindowHighWater)
            stats.lineWindowHighWater = outstandingLines;
        return result;
    }

    LogicalStreamResponseResult response(
        const LogicalStreamTransactionTag &tag, uint64_t lineAddress,
        LogicalStreamResponseKind kind)
    {
        if (!memoryActionActive)
            return rejectResponse(LogicalStreamResponseResult::Stale);
        const auto validation =
            memoryLedger.validateResponse(tag, lineAddress, kind);
        if (validation != LogicalStreamResponseResult::Accepted)
            return rejectResponse(validation);
        if (outstandingLines == 0) {
            increment(stats.internalFaults);
            return rejectResponse(LogicalStreamResponseResult::Invalid);
        }
        const auto result =
            memoryLedger.acceptResponse(tag, lineAddress, kind);
        if (result != LogicalStreamResponseResult::Accepted &&
            result != LogicalStreamResponseResult::Completed) {
            return rejectResponse(result, false);
        }
        --outstandingLines;
        increment(stats.lineResponses);
        if (result == LogicalStreamResponseResult::Completed)
            finishMemoryAction();
        return result;
    }

    std::size_t lineIndex(uint64_t lineAddress) const
    {
        if (!memoryActionActive)
            return LinesPerPage;
        for (std::size_t index = 0;
             index < memoryLedger.issuedLineCount(); ++index) {
            if (memoryLedger.line(index).address == lineAddress)
                return index;
        }
        return LinesPerPage;
    }

    bool ownsMemoryTag(const LogicalStreamTransactionTag &tag) const
    {
        return memoryActionActive && memoryAction.tag == tag;
    }

    ComputeAction pendingCompute() const
    {
        if (!active.valid || active.stage != Stage::ComputeReady)
            return {};
        return makeComputeAction();
    }

    ComputeResult acceptCompute(const ComputeAction &action)
    {
        if (!action.valid)
            return ComputeResult::Invalid;
        if (active.stage != Stage::ComputeReady ||
            !(action == makeComputeAction())) {
            increment(stats.staleComputes);
            return ComputeResult::Stale;
        }
        if (controller.beginOverwriteCompute(active.reservation) !=
            Controller::OverwriteResult::Accepted) {
            active.stage = Stage::Faulted;
            increment(stats.internalFaults);
            return ComputeResult::Invalid;
        }
        active.stage = Stage::Computing;
        increment(stats.computeIssues);
        return ComputeResult::Accepted;
    }

    ComputeResult completeCompute(const ComputeAction &action)
    {
        if (!action.valid)
            return ComputeResult::Invalid;
        if (active.stage != Stage::Computing ||
            !(action == makeComputeAction())) {
            increment(stats.staleComputes);
            return ComputeResult::Stale;
        }
        if (controller.completeOverwrite(active.reservation) !=
            Controller::OverwriteResult::Accepted) {
            active.stage = Stage::Faulted;
            increment(stats.internalFaults);
            return ComputeResult::Invalid;
        }
        active.stage = Stage::WaitingWriteback;
        increment(stats.computeCompletions);
        return ComputeResult::Accepted;
    }

    bool operationComplete() const
    {
        return active.valid && active.stage == Stage::Complete;
    }

    bool retireCompletedOperation()
    {
        if (!operationComplete())
            return false;
        active = ActiveOperation{};
        increment(stats.operationsCompleted);
        return true;
    }

    void requestDrain()
    {
        draining = true;
        increment(stats.drainRequests);
    }

    void resumeAfterDrain()
    {
        if (drained())
            draining = false;
    }

    bool drained() const
    {
        if (active.valid || memoryActionActive ||
            controller.missQueueSize() != 0 ||
            controller.activeLeaseCount() != 0) {
            return false;
        }
        for (std::size_t slot = 0; slot < Slots; ++slot) {
            const auto phase = controller.slotPhase(slot);
            if (phase != Controller::Phase::Empty &&
                phase != Controller::Phase::Clean) {
                return false;
            }
        }
        return true;
    }

    CleanupResult cleanupDescriptor(uint16_t logical)
    {
        if (logical >= LogicalDescriptors)
            return CleanupResult::Invalid;
        DescriptorRecord &record = descriptors[logical];
        if (record.role == DescriptorRole::Free)
            return CleanupResult::Stale;
        if (active.valid &&
            (active.source.logical == logical ||
             active.destination.logical == logical)) {
            increment(stats.cleanupRejects);
            return CleanupResult::Busy;
        }
        const auto result = controller.freeDescriptor(record.handle);
        if (result == Controller::FreeResult::Busy) {
            increment(stats.cleanupRejects);
            return CleanupResult::Busy;
        }
        if (result == Controller::FreeResult::Stale)
            return CleanupResult::Stale;
        if (result != Controller::FreeResult::Accepted)
            return CleanupResult::Invalid;
        record = DescriptorRecord{};
        return CleanupResult::Accepted;
    }

    void resetStats()
    {
        stats = Counters{};
    }

    void noteActionBackpressure()
    {
        increment(stats.actionBackpressure);
    }

    const DescriptorRecord &descriptor(uint16_t logical) const
    {
        return descriptors[logical];
    }

    const Counters &counters() const { return stats; }
    const Controller &cacheController() const { return controller; }
    uint64_t operationID() const { return active.operationID; }
    uint16_t currentPage() const { return active.page; }
    Stage stage() const { return active.stage; }
    bool activeOperation() const { return active.valid; }
    uint16_t activeSourceLogical() const { return active.source.logical; }
    uint16_t activeDestinationLogical() const
    {
        return active.destination.logical;
    }

  private:
    struct ActiveOperation
    {
        bool valid = false;
        uint64_t operationID = 0;
        Controller::DescriptorHandle source{};
        Controller::DescriptorHandle destination{};
        uint8_t operation = 0;
        uint64_t scalarBits = 0;
        uint16_t page = 0;
        Stage stage = Stage::Idle;
        OverwriteReservation reservation{};
    };

    static void increment(uint64_t &counter)
    {
        if (counter != std::numeric_limits<uint64_t>::max())
            ++counter;
    }

    static bool validSpan(const BackingSpan &span)
    {
        return span.rangeID >= 0 && span.base % CacheLineBytes == 0 &&
               span.rangeBegin <= span.base &&
               span.base <= std::numeric_limits<uint64_t>::max() -
                                LogicalBytes &&
               span.base + LogicalBytes <= span.rangeEnd &&
               span.rangeBegin < span.rangeEnd;
    }

    static bool spansOverlap(const BackingSpan &left,
                             const BackingSpan &right)
    {
        return left.base < right.base + LogicalBytes &&
               right.base < left.base + LogicalBytes;
    }

    bool allPagesReady(const Controller::DescriptorHandle &handle) const
    {
        for (uint16_t page = 0; page < Pages; ++page) {
            if (!controller.pageIsReady(controller.identity(handle, page)))
                return false;
        }
        return true;
    }

    AdmitResult rejectAdmission(AdmitResult result)
    {
        increment(stats.admissionRejects);
        return result;
    }

    LogicalStreamResponseResult rejectResponse(
        LogicalStreamResponseResult result, bool record = true)
    {
        increment(stats.responseRejects);
        if (record && result != LogicalStreamResponseResult::Accepted &&
            result != LogicalStreamResponseResult::Completed) {
            memoryLedger.recordRejected(result);
        }
        return result;
    }

    PageIdentity currentSourcePage() const
    {
        return controller.identity(active.source, active.page);
    }

    PageIdentity currentDestinationPage() const
    {
        return controller.identity(active.destination, active.page);
    }

    void queueCurrentSource()
    {
        const auto result = controller.access(currentSourcePage());
        if (result == Controller::AccessResult::Hit) {
            reserveCurrentOverwrite();
            return;
        }
        if (result != Controller::AccessResult::MissQueued &&
            result != Controller::AccessResult::Pending) {
            active.stage = Stage::Faulted;
            increment(stats.internalFaults);
        }
    }

    void reserveCurrentOverwrite()
    {
        const auto reply = controller.reserveFullOverwrite(
            currentSourcePage(), currentDestinationPage());
        if (reply.status != Controller::OverwriteStatus::Accepted) {
            active.stage = Stage::Faulted;
            increment(stats.internalFaults);
            return;
        }
        active.reservation = reply.reservation;
        active.stage = Stage::ComputeReady;
    }

    LogicalStreamTransactionTag makeTag(const MemoryAction &action) const
    {
        return {maaID, action.serial,
                action.kind == Controller::ActionKind::Fill
                    ? LogicalStreamAction::Fill
                    : LogicalStreamAction::Writeback,
                action.page.logical, action.page.page,
                action.page.generation,
                static_cast<int16_t>(action.slot)};
    }

    static bool samePendingMemory(const PendingMemoryAction &left,
                                  const PendingMemoryAction &right)
    {
        return left.valid && right.valid &&
               left.controller == right.controller && left.tag == right.tag &&
               left.backingBase == right.backingBase &&
               left.rangeBegin == right.rangeBegin &&
               left.rangeEnd == right.rangeEnd &&
               left.rangeID == right.rangeID;
    }

    LogicalStreamResponseKind expectedLineKind() const
    {
        return memoryAction.controller.kind == Controller::ActionKind::Fill
                   ? LogicalStreamResponseKind::Read
                   : LogicalStreamResponseKind::Write;
    }

    void finishMemoryAction()
    {
        if (nextLine != LinesPerPage || outstandingLines != 0) {
            active.stage = Stage::Faulted;
            increment(stats.internalFaults);
            return;
        }
        const MemoryAction completed = memoryAction.controller;
        Controller::ResponseResult result;
        if (completed.kind == Controller::ActionKind::Fill) {
            result = controller.completeFill(completed.slot, completed.page,
                                             completed.serial);
        } else {
            result = controller.completeWriteback(
                completed.slot, completed.page, completed.serial);
        }
        memoryActionActive = false;
        memoryAction = PendingMemoryAction{};
        memoryLedger.reset();
        nextLine = 0;
        if (completed.kind == Controller::ActionKind::Fill) {
            if (result != Controller::ResponseResult::FillInstalled) {
                active.stage = Stage::Faulted;
                increment(stats.internalFaults);
                return;
            }
            reserveCurrentOverwrite();
            return;
        }
        if (result != Controller::ResponseResult::WritebackCompleted) {
            active.stage = Stage::Faulted;
            increment(stats.internalFaults);
            return;
        }
        increment(stats.pagesCompleted);
        ++active.page;
        if (active.page == Pages) {
            active.stage = Stage::Complete;
            return;
        }
        active.stage = Stage::WaitingFill;
        queueCurrentSource();
    }

    ComputeAction makeComputeAction() const
    {
        ComputeAction action;
        action.valid = active.valid;
        action.operationID = active.operationID;
        action.transactionID = active.reservation.computeSerial;
        action.source = active.reservation.source.page;
        action.destination = active.reservation.destination.page;
        action.sourceSlot = active.reservation.sourceSlot;
        action.destinationSlot = active.reservation.destinationSlot;
        action.operation = active.operation;
        action.scalarBits = active.scalarBits;
        return action;
    }

    Controller controller{};
    std::array<DescriptorRecord, LogicalDescriptors> descriptors{};
    ActiveOperation active{};
    PendingMemoryAction memoryAction{};
    LogicalStreamResponseLedger memoryLedger{};
    Counters stats{};
    uint64_t nextOperationID = 0;
    uint64_t lastProducerTransaction = 0;
    std::size_t nextLine = 0;
    std::size_t outstandingLines = 0;
    uint16_t maaID = LogicalStreamTransactionTag::InvalidMAA;
    bool maaInitialized = false;
    bool memoryActionActive = false;
    bool draining = false;
};

} // namespace gem5

#endif // __MEM_MAA_LOGICAL_SPD_CACHE_SLICE_HH__
