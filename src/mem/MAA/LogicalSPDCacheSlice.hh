#ifndef __MEM_MAA_LOGICAL_SPD_CACHE_SLICE_HH__
#define __MEM_MAA_LOGICAL_SPD_CACHE_SLICE_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>

#include "mem/MAA/LogicalSPDCacheController.hh"

namespace gem5 {

class LogicalSPDCacheSliceTestAccess;

class LogicalSPDCacheSlice
{
    friend class LogicalSPDCacheSliceTestAccess;

  public:
    using Controller = LogicalSPDCacheController<>;
    using PageIdentity = Controller::PageIdentity;

    static constexpr std::size_t LogicalDescriptors = 2;
    static constexpr std::size_t Pages = 4;
    static constexpr std::size_t Slots = 2;
    static constexpr std::size_t PageElements = 4096;
    static constexpr std::size_t PageBytes = PageElements * sizeof(double);
    static constexpr std::size_t BackingBytes = Pages * PageBytes;
    static constexpr std::size_t CacheLineBytes = 64;
    static constexpr std::size_t LinesPerPage = PageBytes / CacheLineBytes;
    static constexpr std::size_t OperationMemorySerials = Pages * 3;
    static constexpr uint8_t Float64DataType = 5;
    static constexpr uint8_t MaxScalarOperation = 5;

    enum class DescriptorRole : uint8_t
    {
        Free,
        Source,
        Destination,
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

    enum class Status : uint8_t
    {
        Accepted,
        Busy,
        Invalid,
        NotReady,
        AlreadyResident,
        Stale,
        Exhausted,
        Draining,
        Sealed,
    };

    enum class PageOperation : uint8_t
    {
        Fill,
        Writeback,
    };

    struct BackingSpan
    {
        uint64_t base = 0;
        uint32_t bytes = 0;

        bool operator==(const BackingSpan &other) const
        {
            return base == other.base && bytes == other.bytes;
        }
    };

    struct DescriptorRecord
    {
        DescriptorRole role = DescriptorRole::Free;
        Controller::DescriptorHandle handle{};
        BackingSpan backing{};
        uint64_t producerTransaction = 0;
        uint8_t dataType = std::numeric_limits<uint8_t>::max();
        uint8_t backingReady = 0;
        uint8_t writebackAcked = 0;
    };

    struct Admission
    {
        uint8_t sourceLogical = 0;
        uint8_t destinationLogical = 0;
        BackingSpan destination{};
        uint8_t dataType = Float64DataType;
        uint8_t operation = 0;
        uint64_t scalarBits = 0;
    };

    struct PageAction
    {
        bool valid = false;
        PageOperation operation = PageOperation::Fill;
        uint8_t descriptor = 0;
        uint32_t generation = 0;
        uint8_t page = 0;
        uint8_t slot = 0;
        uint64_t baseAddress = 0;
        uint64_t serial = 0;
        Controller::MemoryAction controller{};

        bool operator==(const PageAction &other) const
        {
            return valid == other.valid && operation == other.operation &&
                   descriptor == other.descriptor &&
                   generation == other.generation && page == other.page &&
                   slot == other.slot && baseAddress == other.baseAddress &&
                   serial == other.serial && controller == other.controller;
        }
    };

    struct ComputeAction
    {
        bool valid = false;
        uint32_t operationID = 0;
        uint64_t computeSerial = 0;
        PageIdentity source{};
        PageIdentity destination{};
        uint8_t sourceSlot = 0;
        uint8_t destinationSlot = 0;
        uint8_t operation = 0;
        uint64_t scalarBits = 0;

        bool operator==(const ComputeAction &other) const
        {
            return valid == other.valid && operationID == other.operationID &&
                   computeSerial == other.computeSerial &&
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
        uint64_t fillsCompleted = 0;
        uint64_t writebacksCompleted = 0;
        uint64_t computesStarted = 0;
        uint64_t computesCompleted = 0;
        uint64_t pagesCompleted = 0;
        uint64_t highLevelCompletions = 0;
        uint64_t refillCompletions = 0;
        uint64_t rejects = 0;
        uint64_t drains = 0;
        uint64_t reserved = 0;
    };

    static_assert(sizeof(Counters) == 12 * sizeof(uint64_t));

    Status initialize(uint16_t maaID);
    Status registerSource(uint8_t logical, BackingSpan backing,
                          uint8_t dataType = Float64DataType);
    Status admit(const Admission &request);
    PageAction pendingPageAction() const;
    Status acceptPageAction(const PageAction &pageAction);
    Status completePageAction(const PageAction &pageAction);
    ComputeAction pendingCompute() const;
    Status acceptCompute(const ComputeAction &compute);
    Status completeCompute(const ComputeAction &compute);
    Status queueRefill(uint8_t logical, uint8_t page);
    Status retireCompletedOperation();
    Status cleanupDescriptor(uint8_t logical);
    Status requestDrain();
    Status resumeAfterDrain();
    Status reset();
    Status teardown();

    bool operationComplete() const;
    bool descriptorComplete(uint8_t logical) const;
    bool drained() const;
    bool activeOperation() const { return active.valid; }
    bool pageActionActive() const { return memoryActionActive; }
    bool refillActive() const { return refillPending; }
    Stage stage() const { return active.stage; }
    uint8_t currentPage() const { return active.page; }
    uint32_t operationID() const { return active.operationID; }
    const DescriptorRecord &descriptor(uint8_t logical) const;
    const Controller &cacheController() const { return controller; }
    const Counters &counters() const { return stats; }

  private:
    struct ActiveOperation
    {
        bool valid = false;
        uint32_t operationID = 0;
        Controller::DescriptorHandle source{};
        Controller::DescriptorHandle destination{};
        uint8_t operation = 0;
        uint64_t scalarBits = 0;
        uint8_t page = 0;
        Stage stage = Stage::Idle;
        Controller::OverwriteReservation reservation{};
    };

    static bool validBacking(BackingSpan backing);
    static bool overlaps(BackingSpan left, BackingSpan right);
    static void increment(uint64_t &counter);
    Status reject(Status status);
    bool allPagesReady(const Controller::DescriptorHandle &handle) const;
    PageIdentity sourcePage() const;
    PageIdentity destinationPage() const;
    Status queueSourcePage();
    Status reserveOverwrite();
    PageAction makePageAction(const Controller::MemoryAction &action) const;
    ComputeAction makeComputeAction() const;

    Controller controller{};
    std::array<DescriptorRecord, LogicalDescriptors> descriptors{};
    ActiveOperation active{};
    PageAction acceptedMemoryAction{};
    bool memoryActionActive = false;
    PageIdentity refillIdentity{};
    bool refillPending = false;
    uint32_t lastOperationID = 0;
    uint64_t lastProducerTransaction = 0;
    uint16_t maaID = 0;
    bool initialized = false;
    bool draining = false;
    bool isSealed = false;
    Counters stats{};
};

inline void
LogicalSPDCacheSlice::increment(uint64_t &counter)
{
    if (counter != std::numeric_limits<uint64_t>::max())
        ++counter;
}

inline LogicalSPDCacheSlice::Status
LogicalSPDCacheSlice::reject(Status status)
{
    increment(stats.rejects);
    return status;
}

inline bool
LogicalSPDCacheSlice::validBacking(BackingSpan backing)
{
    return backing.bytes == BackingBytes && backing.base % BackingBytes == 0 &&
           backing.base <= std::numeric_limits<uint64_t>::max() -
                               (BackingBytes - 1);
}

inline bool
LogicalSPDCacheSlice::overlaps(BackingSpan left, BackingSpan right)
{
    return left.base < right.base + right.bytes &&
           right.base < left.base + left.bytes;
}

inline LogicalSPDCacheSlice::Status
LogicalSPDCacheSlice::initialize(uint16_t id)
{
    if (isSealed)
        return Status::Sealed;
    if (initialized)
        return maaID == id ? Status::Accepted : Status::Busy;
    maaID = id;
    initialized = true;
    return Status::Accepted;
}

inline LogicalSPDCacheSlice::Status
LogicalSPDCacheSlice::registerSource(uint8_t logical, BackingSpan backing,
                                    uint8_t dataType)
{
    if (isSealed)
        return Status::Sealed;
    if (draining)
        return reject(Status::Draining);
    if (!initialized || logical >= LogicalDescriptors ||
        dataType != Float64DataType || !validBacking(backing)) {
        return reject(Status::Invalid);
    }
    if (active.valid || refillPending || memoryActionActive ||
        descriptors[logical].role != DescriptorRole::Free) {
        return reject(Status::Busy);
    }
    for (const DescriptorRecord &other : descriptors) {
        if (other.role != DescriptorRole::Free &&
            overlaps(backing, other.backing)) {
            return reject(Status::Invalid);
        }
    }
    if (lastProducerTransaction == std::numeric_limits<uint64_t>::max())
        return reject(Status::Exhausted);

    const auto allocation = controller.allocate(logical);
    if (allocation.status == Controller::AllocateStatus::GenerationExhausted)
        return reject(Status::Exhausted);
    if (allocation.status != Controller::AllocateStatus::Accepted)
        return reject(Status::Busy);

    for (uint8_t page = 0; page < Pages; ++page) {
        if (controller.notifyPageReady(
                controller.identity(allocation.descriptor, page)) !=
            Controller::ReadyResult::Accepted) {
            (void)controller.freeDescriptor(allocation.descriptor);
            return reject(Status::Invalid);
        }
    }
    DescriptorRecord record;
    record.role = DescriptorRole::Source;
    record.handle = allocation.descriptor;
    record.backing = backing;
    record.producerTransaction = ++lastProducerTransaction;
    record.dataType = dataType;
    record.backingReady = (uint8_t{1} << Pages) - 1;
    descriptors[logical] = record;
    increment(stats.sourceRegistrations);
    return Status::Accepted;
}

inline bool
LogicalSPDCacheSlice::allPagesReady(
    const Controller::DescriptorHandle &handle) const
{
    for (uint8_t page = 0; page < Pages; ++page) {
        if (!controller.pageIsReady(controller.identity(handle, page)))
            return false;
    }
    return true;
}

inline LogicalSPDCacheSlice::Status
LogicalSPDCacheSlice::admit(const Admission &request)
{
    if (isSealed)
        return Status::Sealed;
    if (draining)
        return reject(Status::Draining);
    if (!initialized || active.valid || refillPending || memoryActionActive)
        return reject(Status::Busy);
    if (request.sourceLogical >= LogicalDescriptors ||
        request.destinationLogical >= LogicalDescriptors ||
        request.sourceLogical == request.destinationLogical ||
        request.dataType != Float64DataType ||
        request.operation > MaxScalarOperation ||
        !validBacking(request.destination)) {
        return reject(Status::Invalid);
    }
    const DescriptorRecord &source = descriptors[request.sourceLogical];
    if (source.role != DescriptorRole::Source ||
        source.dataType != request.dataType ||
        !allPagesReady(source.handle)) {
        return reject(Status::NotReady);
    }
    if (descriptors[request.destinationLogical].role != DescriptorRole::Free ||
        controller.descriptorAllocated(request.destinationLogical)) {
        return reject(Status::Busy);
    }
    if (overlaps(source.backing, request.destination))
        return reject(Status::Invalid);
    if (lastOperationID == std::numeric_limits<uint32_t>::max() ||
        !controller.canAllocateMemorySerials(OperationMemorySerials)) {
        return reject(Status::Exhausted);
    }

    const auto allocation = controller.allocate(request.destinationLogical);
    if (allocation.status == Controller::AllocateStatus::GenerationExhausted)
        return reject(Status::Exhausted);
    if (allocation.status != Controller::AllocateStatus::Accepted)
        return reject(Status::Busy);

    DescriptorRecord destination;
    destination.role = DescriptorRole::Destination;
    destination.handle = allocation.descriptor;
    destination.backing = request.destination;
    destination.dataType = request.dataType;
    descriptors[request.destinationLogical] = destination;

    active = ActiveOperation{};
    active.valid = true;
    active.operationID = ++lastOperationID;
    active.source = source.handle;
    active.destination = allocation.descriptor;
    active.operation = request.operation;
    active.scalarBits = request.scalarBits;
    active.stage = Stage::WaitingFill;
    increment(stats.admissions);
    const Status queued = queueSourcePage();
    if (queued != Status::Accepted)
        active.stage = Stage::Faulted;
    return queued;
}

inline LogicalSPDCacheSlice::PageIdentity
LogicalSPDCacheSlice::sourcePage() const
{
    return controller.identity(active.source, active.page);
}

inline LogicalSPDCacheSlice::PageIdentity
LogicalSPDCacheSlice::destinationPage() const
{
    return controller.identity(active.destination, active.page);
}

inline LogicalSPDCacheSlice::Status
LogicalSPDCacheSlice::queueSourcePage()
{
    const auto result = controller.access(sourcePage());
    if (result == Controller::AccessResult::Hit)
        return reserveOverwrite();
    if (result == Controller::AccessResult::MissQueued ||
        result == Controller::AccessResult::Pending)
        return Status::Accepted;
    return Status::NotReady;
}

inline LogicalSPDCacheSlice::Status
LogicalSPDCacheSlice::reserveOverwrite()
{
    const auto reply =
        controller.reserveFullOverwrite(sourcePage(), destinationPage());
    if (reply.status != Controller::OverwriteStatus::Accepted)
        return Status::Busy;
    active.reservation = reply.reservation;
    active.stage = Stage::ComputeReady;
    return Status::Accepted;
}

inline LogicalSPDCacheSlice::PageAction
LogicalSPDCacheSlice::makePageAction(
    const Controller::MemoryAction &controllerAction) const
{
    if (controllerAction.kind == Controller::ActionKind::None)
        return {};
    const DescriptorRecord &record =
        descriptors[controllerAction.page.logical];
    PageAction action;
    action.valid = true;
    action.operation = controllerAction.kind == Controller::ActionKind::Fill
                           ? PageOperation::Fill
                           : PageOperation::Writeback;
    action.descriptor = static_cast<uint8_t>(controllerAction.page.logical);
    action.generation = controllerAction.page.generation;
    action.page = static_cast<uint8_t>(controllerAction.page.page);
    action.slot = static_cast<uint8_t>(controllerAction.slot);
    action.baseAddress = record.backing.base +
                         static_cast<uint64_t>(action.page) * PageBytes;
    action.serial = controllerAction.serial;
    action.controller = controllerAction;
    return action;
}

inline LogicalSPDCacheSlice::PageAction
LogicalSPDCacheSlice::pendingPageAction() const
{
    if (memoryActionActive)
        return {};
    const Controller::MemoryAction action = controller.pendingAction();
    if (action.kind == Controller::ActionKind::None)
        return {};
    if (active.valid) {
        const PageIdentity expected =
            active.stage == Stage::WaitingFill
                ? sourcePage()
                : (active.stage == Stage::WaitingWriteback
                       ? destinationPage()
                       : PageIdentity{});
        if (action.page != expected)
            return {};
    } else if (!refillPending || action.page != refillIdentity) {
        return {};
    }
    return makePageAction(action);
}

inline LogicalSPDCacheSlice::Status
LogicalSPDCacheSlice::acceptPageAction(const PageAction &pageAction)
{
    if (isSealed)
        return Status::Sealed;
    const PageAction expected = pendingPageAction();
    if (!pageAction.valid || !expected.valid)
        return Status::Invalid;
    if (!(pageAction == expected))
        return Status::Stale;
    if (controller.acceptAction(pageAction.controller) !=
        Controller::ActionResult::Accepted) {
        return Status::Stale;
    }
    acceptedMemoryAction = pageAction;
    memoryActionActive = true;
    return Status::Accepted;
}

inline LogicalSPDCacheSlice::Status
LogicalSPDCacheSlice::completePageAction(const PageAction &pageAction)
{
    if (isSealed)
        return Status::Sealed;
    if (!memoryActionActive || !(pageAction == acceptedMemoryAction))
        return Status::Stale;
    Controller::ResponseResult response;
    if (pageAction.operation == PageOperation::Fill) {
        response = controller.completeFill(
            pageAction.controller.slot, pageAction.controller.page,
            pageAction.controller.serial);
        if (response != Controller::ResponseResult::FillInstalled)
            return Status::Stale;
    } else {
        response = controller.completeWriteback(
            pageAction.controller.slot, pageAction.controller.page,
            pageAction.controller.serial);
        if (response != Controller::ResponseResult::WritebackCompleted)
            return Status::Stale;
    }
    memoryActionActive = false;
    acceptedMemoryAction = PageAction{};

    if (pageAction.operation == PageOperation::Fill) {
        increment(stats.fillsCompleted);
        if (refillPending && pageAction.controller.page == refillIdentity) {
            refillPending = false;
            refillIdentity = PageIdentity{};
            increment(stats.refillCompletions);
            return Status::Accepted;
        }
        if (!active.valid || active.stage != Stage::WaitingFill)
            return Status::Invalid;
        const Status reserved = reserveOverwrite();
        if (reserved != Status::Accepted)
            active.stage = Stage::Faulted;
        return reserved;
    }

    increment(stats.writebacksCompleted);
    DescriptorRecord &destination =
        descriptors[pageAction.controller.page.logical];
    const uint8_t bit = uint8_t{1} << pageAction.page;
    destination.backingReady |= bit;
    destination.writebackAcked |= bit;
    increment(stats.pagesCompleted);
    if (!active.valid || active.stage != Stage::WaitingWriteback)
        return Status::Invalid;
    ++active.page;
    if (active.page == Pages) {
        active.stage = Stage::Complete;
        increment(stats.highLevelCompletions);
        return Status::Accepted;
    }
    active.stage = Stage::WaitingFill;
    const Status queued = queueSourcePage();
    if (queued != Status::Accepted)
        active.stage = Stage::Faulted;
    return queued;
}

inline LogicalSPDCacheSlice::ComputeAction
LogicalSPDCacheSlice::makeComputeAction() const
{
    if (!active.valid)
        return {};
    ComputeAction action;
    action.valid = true;
    action.operationID = active.operationID;
    action.computeSerial = active.reservation.computeSerial;
    action.source = sourcePage();
    action.destination = destinationPage();
    action.sourceSlot =
        static_cast<uint8_t>(active.reservation.sourceSlot);
    action.destinationSlot =
        static_cast<uint8_t>(active.reservation.destinationSlot);
    action.operation = active.operation;
    action.scalarBits = active.scalarBits;
    return action;
}

inline LogicalSPDCacheSlice::ComputeAction
LogicalSPDCacheSlice::pendingCompute() const
{
    return active.stage == Stage::ComputeReady ? makeComputeAction()
                                               : ComputeAction{};
}

inline LogicalSPDCacheSlice::Status
LogicalSPDCacheSlice::acceptCompute(const ComputeAction &compute)
{
    if (isSealed)
        return Status::Sealed;
    if (!compute.valid || active.stage != Stage::ComputeReady)
        return Status::Invalid;
    if (!(compute == makeComputeAction()))
        return Status::Stale;
    if (controller.beginOverwriteCompute(active.reservation) !=
        Controller::OverwriteResult::Accepted) {
        active.stage = Stage::Faulted;
        return Status::Stale;
    }
    active.stage = Stage::Computing;
    increment(stats.computesStarted);
    return Status::Accepted;
}

inline LogicalSPDCacheSlice::Status
LogicalSPDCacheSlice::completeCompute(const ComputeAction &compute)
{
    if (isSealed)
        return Status::Sealed;
    if (!compute.valid || active.stage != Stage::Computing)
        return Status::Invalid;
    if (!(compute == makeComputeAction()))
        return Status::Stale;
    if (controller.completeOverwrite(active.reservation) !=
        Controller::OverwriteResult::Accepted) {
        active.stage = Stage::Faulted;
        return Status::Stale;
    }
    active.stage = Stage::WaitingWriteback;
    increment(stats.computesCompleted);
    return Status::Accepted;
}

inline LogicalSPDCacheSlice::Status
LogicalSPDCacheSlice::queueRefill(uint8_t logical, uint8_t page)
{
    if (isSealed)
        return Status::Sealed;
    if (draining)
        return Status::Draining;
    if (active.valid || refillPending || memoryActionActive)
        return Status::Busy;
    if (logical >= LogicalDescriptors || page >= Pages ||
        descriptors[logical].role == DescriptorRole::Free)
        return Status::Invalid;
    const PageIdentity identity =
        controller.identity(descriptors[logical].handle, page);
    const auto result = controller.access(identity);
    if (result == Controller::AccessResult::Hit)
        return Status::AlreadyResident;
    if (result != Controller::AccessResult::MissQueued &&
        result != Controller::AccessResult::Pending)
        return Status::NotReady;
    refillIdentity = identity;
    refillPending = true;
    return Status::Accepted;
}

inline bool
LogicalSPDCacheSlice::operationComplete() const
{
    return active.valid && active.stage == Stage::Complete;
}

inline LogicalSPDCacheSlice::Status
LogicalSPDCacheSlice::retireCompletedOperation()
{
    if (!operationComplete())
        return Status::Stale;
    active = ActiveOperation{};
    return Status::Accepted;
}

inline bool
LogicalSPDCacheSlice::descriptorComplete(uint8_t logical) const
{
    return logical < LogicalDescriptors &&
           descriptors[logical].role != DescriptorRole::Free &&
           descriptors[logical].writebackAcked ==
               (uint8_t{1} << Pages) - 1;
}

inline LogicalSPDCacheSlice::Status
LogicalSPDCacheSlice::cleanupDescriptor(uint8_t logical)
{
    if (isSealed)
        return Status::Sealed;
    if (logical >= LogicalDescriptors)
        return Status::Invalid;
    DescriptorRecord &record = descriptors[logical];
    if (record.role == DescriptorRole::Free)
        return Status::Stale;
    if (active.valid || refillPending || memoryActionActive)
        return Status::Busy;
    const auto result = controller.freeDescriptor(record.handle);
    if (result == Controller::FreeResult::Busy)
        return Status::Busy;
    if (result != Controller::FreeResult::Accepted)
        return Status::Stale;
    record = DescriptorRecord{};
    return Status::Accepted;
}

inline LogicalSPDCacheSlice::Status
LogicalSPDCacheSlice::requestDrain()
{
    if (isSealed)
        return Status::Sealed;
    draining = true;
    increment(stats.drains);
    return Status::Accepted;
}

inline bool
LogicalSPDCacheSlice::drained() const
{
    if (active.valid || refillPending || memoryActionActive ||
        controller.missQueueSize() != 0 ||
        controller.activeLeaseCount() != 0) {
        return false;
    }
    for (std::size_t slot = 0; slot < Slots; ++slot) {
        const auto phase = controller.slotPhase(slot);
        if (phase != Controller::Phase::Empty &&
            phase != Controller::Phase::Clean)
            return false;
    }
    return true;
}

inline LogicalSPDCacheSlice::Status
LogicalSPDCacheSlice::resumeAfterDrain()
{
    if (!drained())
        return Status::Busy;
    draining = false;
    return Status::Accepted;
}

inline LogicalSPDCacheSlice::Status
LogicalSPDCacheSlice::reset()
{
    if (isSealed)
        return Status::Sealed;
    if (!drained())
        return Status::Busy;
    for (uint8_t logical = 0; logical < LogicalDescriptors; ++logical) {
        if (descriptors[logical].role != DescriptorRole::Free) {
            if (cleanupDescriptor(logical) != Status::Accepted)
                return Status::Busy;
        }
    }
    draining = false;
    return Status::Accepted;
}

inline LogicalSPDCacheSlice::Status
LogicalSPDCacheSlice::teardown()
{
    if (isSealed)
        return Status::Sealed;
    if (!drained())
        return Status::Busy;
    for (const DescriptorRecord &record : descriptors) {
        if (record.role != DescriptorRole::Free)
            return Status::Busy;
    }
    isSealed = true;
    return Status::Accepted;
}

inline const LogicalSPDCacheSlice::DescriptorRecord &
LogicalSPDCacheSlice::descriptor(uint8_t logical) const
{
    static const DescriptorRecord invalid{};
    return logical < LogicalDescriptors ? descriptors[logical] : invalid;
}

static_assert(LogicalSPDCacheSlice::LogicalDescriptors == 2);
static_assert(LogicalSPDCacheSlice::Pages == 4);
static_assert(LogicalSPDCacheSlice::Slots == 2);
static_assert(LogicalSPDCacheSlice::PageElements == 4096);
static_assert(LogicalSPDCacheSlice::CacheLineBytes == 64);
static_assert(LogicalSPDCacheSlice::LinesPerPage == 512);

} // namespace gem5

#endif // __MEM_MAA_LOGICAL_SPD_CACHE_SLICE_HH__
