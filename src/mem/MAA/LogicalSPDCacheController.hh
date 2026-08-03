#ifndef __MEM_MAA_LOGICAL_SPD_CACHE_CONTROLLER_HH__
#define __MEM_MAA_LOGICAL_SPD_CACHE_CONTROLLER_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>

namespace gem5 {

/**
 * A simulator-independent, payload-free logical SPD page-cache controller.
 *
 * All storage is fixed by the template arguments.  A PageIdentity is the only
 * name accepted for page events, cache accesses, memory responses, and leases;
 * generation zero is reserved as invalid.  The controller never stores page
 * bytes.  A surrounding implementation owns backing memory and physical SPD
 * payload and may decline a pending memory action without changing this core.
 *
 * Ownership is split deliberately:
 *  - an allocated DescriptorHandle owns its ready bits until freeDescriptor;
 *  - a non-empty Slot owns exactly one PageIdentity and, while Filling,
 *    Reserved, Computing, or Writeback, one exact transaction identity;
 *  - a successful pin owns one bounded Lease until its exact release;
 *  - a successful full-overwrite reservation owns two managed leases and a
 *    distinct destination slot until exact completion or cancellation;
 *  - a queued miss owns only a PageIdentity, never page payload.
 *
 * Every accepted fill, overwrite compute, and writeback receives a globally
 * unique, nonzero transaction serial.  Completion requires the exact action,
 * slot/page identity, leases, and serial.  Fill completion installs data only
 * if its identity is still live and ready.  A completed overwrite destination
 * becomes dirty and is published ready only after its preallocated writeback
 * transaction receives the exact response.  Serial exhaustion permanently
 * suppresses future work instead of wrapping into an earlier transaction.
 */
template <std::size_t LogicalDescriptors = 2,
          std::size_t PagesPerDescriptor = 4,
          std::size_t PhysicalSlots = 2,
          std::size_t MissQueueEntries = 4,
          std::size_t LeaseEntries = 4>
class LogicalSPDCacheController
{
  public:
    using Generation = uint32_t;
    using TransactionSerial = uint64_t;

    static_assert(LogicalDescriptors >= 2,
                  "the cache core requires at least two logical descriptors");
    static_assert(PagesPerDescriptor > 0 && PhysicalSlots > 0 &&
                      MissQueueEntries > 0 && LeaseEntries > 0,
                  "all cache capacities must be positive");
    static_assert(LogicalDescriptors <=
                          std::numeric_limits<uint16_t>::max() &&
                      PagesPerDescriptor <=
                          std::numeric_limits<uint16_t>::max() &&
                      PhysicalSlots <= std::numeric_limits<uint16_t>::max() &&
                      LeaseEntries <= std::numeric_limits<uint16_t>::max(),
                  "public identity fields must represent every entry");

    static constexpr std::size_t DescriptorCapacity = LogicalDescriptors;
    static constexpr std::size_t PageCapacity = PagesPerDescriptor;
    static constexpr std::size_t SlotCapacity = PhysicalSlots;
    static constexpr std::size_t QueueCapacity = MissQueueEntries;
    static constexpr std::size_t LeaseCapacity = LeaseEntries;
    static constexpr uint16_t NoSlot = std::numeric_limits<uint16_t>::max();
    static constexpr uint16_t NoLease = std::numeric_limits<uint16_t>::max();
    static constexpr TransactionSerial NoTransaction = 0;

    struct DescriptorHandle
    {
        uint16_t logical = 0;
        Generation generation = 0;

        bool operator==(const DescriptorHandle &other) const
        {
            return logical == other.logical && generation == other.generation;
        }
    };

    struct PageIdentity
    {
        uint16_t logical = 0;
        uint16_t page = 0;
        Generation generation = 0;

        bool operator==(const PageIdentity &other) const
        {
            return logical == other.logical && page == other.page &&
                   generation == other.generation;
        }

        bool operator!=(const PageIdentity &other) const
        {
            return !(*this == other);
        }
    };

    struct Lease
    {
        uint16_t entry = NoLease;
        uint64_t serial = 0;
        PageIdentity page{};

        bool operator==(const Lease &other) const
        {
            return entry == other.entry && serial == other.serial &&
                   page == other.page;
        }
    };

    enum class AllocateStatus : uint8_t
    {
        Accepted,
        Busy,
        Invalid,
        GenerationExhausted,
    };

    struct AllocateReply
    {
        AllocateStatus status = AllocateStatus::Invalid;
        DescriptorHandle descriptor{};
    };

    enum class FreeResult : uint8_t
    {
        Accepted,
        Busy,
        Stale,
        Invalid,
    };

    enum class ReadyResult : uint8_t
    {
        Accepted,
        Duplicate,
        Busy,
        Stale,
        Invalid,
    };

    enum class AccessResult : uint8_t
    {
        Hit,
        MissQueued,
        Pending,
        NotReady,
        Backpressure,
        Stale,
        Invalid,
    };

    enum class PinStatus : uint8_t
    {
        Accepted,
        NotReady,
        NotResident,
        Backpressure,
        Stale,
        Invalid,
    };

    struct PinReply
    {
        PinStatus status = PinStatus::Invalid;
        Lease lease{};
    };

    enum class LeaseResult : uint8_t
    {
        Accepted,
        Managed,
        Stale,
        Invalid,
    };

    enum class Phase : uint8_t
    {
        Empty,
        Filling,
        Reserved,
        Computing,
        Clean,
        Dirty,
        Writeback,
    };

    enum class ActionKind : uint8_t
    {
        None,
        Fill,
        Writeback,
    };

    struct MemoryAction
    {
        ActionKind kind = ActionKind::None;
        uint16_t slot = NoSlot;
        TransactionSerial serial = NoTransaction;
        PageIdentity page{};
        PageIdentity cleanVictim{};
        bool discardsCleanVictim = false;

        bool operator==(const MemoryAction &other) const
        {
            return kind == other.kind && slot == other.slot &&
                   serial == other.serial && page == other.page &&
                   cleanVictim == other.cleanVictim &&
                   discardsCleanVictim == other.discardsCleanVictim;
        }

        bool operator!=(const MemoryAction &other) const
        {
            return !(*this == other);
        }
    };

    enum class ActionResult : uint8_t
    {
        Accepted,
        Stale,
        Invalid,
    };

    enum class CancelResult : uint8_t
    {
        Accepted,
        Stale,
        Invalid,
    };

    enum class ResponseResult : uint8_t
    {
        FillInstalled,
        FillReleasedObsolete,
        WritebackCompleted,
        Stale,
        Invalid,
    };

    enum class OverwriteStatus : uint8_t
    {
        Accepted,
        SourceNotReady,
        SourceNotResident,
        DestinationReady,
        DestinationUnavailable,
        Backpressure,
        SerialExhausted,
        Stale,
        Invalid,
    };

    struct OverwriteReservation
    {
        Lease source{};
        Lease destination{};
        uint16_t sourceSlot = NoSlot;
        uint16_t destinationSlot = NoSlot;
        TransactionSerial computeSerial = NoTransaction;
        TransactionSerial writebackSerial = NoTransaction;
    };

    struct OverwriteReply
    {
        OverwriteStatus status = OverwriteStatus::Invalid;
        OverwriteReservation reservation{};
    };

    enum class OverwriteResult : uint8_t
    {
        Accepted,
        Stale,
        Invalid,
    };

    AllocateReply
    allocate(uint16_t logical)
    {
        if (logical >= LogicalDescriptors)
            return {AllocateStatus::Invalid, {}};
        Descriptor &descriptor = descriptors[logical];
        if (descriptor.allocated)
            return {AllocateStatus::Busy, {}};
        if (descriptor.generation ==
            std::numeric_limits<Generation>::max()) {
            return {AllocateStatus::GenerationExhausted, {}};
        }
        ++descriptor.generation;
        descriptor.allocated = true;
        descriptor.ready.fill(false);
        return {AllocateStatus::Accepted,
                {logical, descriptor.generation}};
    }

    PageIdentity
    identity(const DescriptorHandle &descriptor, uint16_t page) const
    {
        if (page >= PagesPerDescriptor)
            return {};
        return {descriptor.logical, page, descriptor.generation};
    }

    /**
     * Relinquish descriptor ownership.
     *
     * Active leases cause Busy and no mutation.  Queued misses are canceled,
     * clean pages are discarded, dirty pages remain owned by their slots until
     * explicit writeback actions/responses, and already issued fills or
     * writebacks retain their slots until their exact responses.  A later
     * allocation may reuse the logical index because its generation differs.
     */
    FreeResult
    freeDescriptor(const DescriptorHandle &handle)
    {
        if (handle.logical >= LogicalDescriptors || handle.generation == 0)
            return FreeResult::Invalid;
        Descriptor &descriptor = descriptors[handle.logical];
        if (!descriptor.allocated ||
            descriptor.generation != handle.generation) {
            return FreeResult::Stale;
        }
        for (const LeaseRecord &lease : leases) {
            if (lease.active && lease.page.logical == handle.logical &&
                lease.page.generation == handle.generation) {
                return FreeResult::Busy;
            }
        }

        descriptor.allocated = false;
        descriptor.ready.fill(false);
        removeQueued(handle);
        for (Slot &slot : slots) {
            if (slot.page.logical != handle.logical ||
                slot.page.generation != handle.generation) {
                continue;
            }
            if (slot.phase == Phase::Clean)
                slot = Slot{};
            // Dirty, Filling, and Writeback retain the old generation token.
        }
        return FreeResult::Accepted;
    }

    ReadyResult
    notifyPageReady(const PageIdentity &page)
    {
        if (!validCoordinates(page))
            return ReadyResult::Invalid;
        Descriptor &descriptor = descriptors[page.logical];
        if (!descriptor.allocated ||
            descriptor.generation != page.generation) {
            return ReadyResult::Stale;
        }
        if (descriptor.ready[page.page])
            return ReadyResult::Duplicate;
        if (pageHasOwner(page))
            return ReadyResult::Busy;
        descriptor.ready[page.page] = true;
        return ReadyResult::Accepted;
    }

    /** Queue a ready-page miss, or report an exact resident/in-flight hit. */
    AccessResult
    access(const PageIdentity &page)
    {
        if (!validCoordinates(page))
            return AccessResult::Invalid;
        if (!isLive(page))
            return AccessResult::Stale;
        if (!descriptors[page.logical].ready[page.page])
            return AccessResult::NotReady;

        for (const Slot &slot : slots) {
            if (slot.page != page)
                continue;
            if (slot.phase == Phase::Clean || slot.phase == Phase::Dirty)
                return AccessResult::Hit;
            if (slot.phase == Phase::Filling)
                return AccessResult::Pending;
        }
        if (queued(page))
            return AccessResult::Pending;
        if (queueSize == MissQueueEntries)
            return AccessResult::Backpressure;
        missQueue[queueSize++] = page;
        return AccessResult::MissQueued;
    }

    /**
     * Atomically pin one ready resident source and reserve a distinct slot for
     * a full-overwrite destination.  The destination must belong to a live
     * different descriptor and must not already be ready, queued, or owned.
     * It is never fetched.  Any non-Accepted result leaves every descriptor,
     * slot, lease, queue entry, and serial unchanged.
     *
     * Two exact managed leases and both the compute and mandatory writeback
     * serials are allocated together.  Preallocating the writeback serial
     * guarantees that a computation accepted near serial exhaustion can still
     * publish or discard its dirty result through an exact response.
     */
    OverwriteReply
    reserveFullOverwrite(const PageIdentity &source,
                         const PageIdentity &destination)
    {
        if (!validCoordinates(source) || !validCoordinates(destination) ||
            source.logical == destination.logical) {
            return {OverwriteStatus::Invalid, {}};
        }
        if (!isLive(source) || !isLive(destination))
            return {OverwriteStatus::Stale, {}};
        if (!descriptors[source.logical].ready[source.page])
            return {OverwriteStatus::SourceNotReady, {}};
        if (descriptors[destination.logical].ready[destination.page])
            return {OverwriteStatus::DestinationReady, {}};

        const uint16_t sourceSlot = residentSlot(source);
        if (sourceSlot == NoSlot)
            return {OverwriteStatus::SourceNotResident, {}};
        if (queued(destination) || pageHasOwner(destination))
            return {OverwriteStatus::DestinationUnavailable, {}};

        const uint16_t destinationSlot =
            overwriteDestinationSlot(sourceSlot);
        if (destinationSlot == NoSlot)
            return {OverwriteStatus::DestinationUnavailable, {}};

        std::array<uint16_t, 2> leaseEntries{NoLease, NoLease};
        std::size_t freeLeases = 0;
        for (uint16_t entry = 0;
             entry < LeaseEntries && freeLeases < leaseEntries.size();
             ++entry) {
            const LeaseRecord &record = leases[entry];
            if (!record.active &&
                record.serial != std::numeric_limits<uint64_t>::max()) {
                leaseEntries[freeLeases++] = entry;
            }
        }
        if (freeLeases != leaseEntries.size())
            return {OverwriteStatus::Backpressure, {}};

        const TransactionSerial maximum =
            std::numeric_limits<TransactionSerial>::max();
        if (lastMemorySerial >= maximum - 1)
            return {OverwriteStatus::SerialExhausted, {}};

        const TransactionSerial computeSerial = lastMemorySerial + 1;
        const TransactionSerial writebackSerial = computeSerial + 1;
        const Lease sourceLease = activateManagedLease(
            leaseEntries[0], sourceSlot, source, LeasePurpose::OverwriteSource,
            computeSerial);
        const Lease destinationLease = activateManagedLease(
            leaseEntries[1], destinationSlot, destination,
            LeasePurpose::OverwriteDestination, computeSerial);

        Slot &slot = slots[destinationSlot];
        slot = Slot{};
        slot.phase = Phase::Reserved;
        slot.page = destination;
        slot.transaction = computeSerial;
        slot.writebackTransaction = writebackSerial;
        slot.publishOnWriteback = true;
        lastMemorySerial = writebackSerial;
        return {OverwriteStatus::Accepted,
                {sourceLease, destinationLease, sourceSlot, destinationSlot,
                 computeSerial, writebackSerial}};
    }

    /**
     * Start the exact reserved compute; duplicate/forged starts are no-ops.
     */
    OverwriteResult
    beginOverwriteCompute(const OverwriteReservation &reservation)
    {
        if (!validOverwriteFields(reservation))
            return OverwriteResult::Invalid;
        if (!matchingOverwrite(reservation, Phase::Reserved))
            return OverwriteResult::Stale;
        slots[reservation.destinationSlot].phase = Phase::Computing;
        return OverwriteResult::Accepted;
    }

    /**
     * Complete the exact compute, atomically release both managed leases, and
     * transition only the destination to Dirty.  The destination remains not
     * ready and owns its slot until its preallocated writeback is
     * acknowledged.
     */
    OverwriteResult
    completeOverwrite(const OverwriteReservation &reservation)
    {
        if (!validOverwriteFields(reservation))
            return OverwriteResult::Invalid;
        if (!matchingOverwrite(reservation, Phase::Computing))
            return OverwriteResult::Stale;

        LeaseRecord &source = leases[reservation.source.entry];
        LeaseRecord &destination = leases[reservation.destination.entry];
        Slot &slot = slots[destination.slot];
        slot.phase = Phase::Dirty;
        slot.transaction = NoTransaction;
        releaseManagedLease(source);
        releaseManagedLease(destination);
        return OverwriteResult::Accepted;
    }

    /**
     * Cancel an exact reservation before issue or after the caller has
     * quiesced a failed compute.  The tentative destination is discarded,
     * neither page is published, and both managed leases are released.
     */
    OverwriteResult
    cancelOverwrite(const OverwriteReservation &reservation)
    {
        if (!validOverwriteFields(reservation))
            return OverwriteResult::Invalid;
        if (!matchingOverwrite(reservation, Phase::Reserved) &&
            !matchingOverwrite(reservation, Phase::Computing)) {
            return OverwriteResult::Stale;
        }

        LeaseRecord &source = leases[reservation.source.entry];
        LeaseRecord &destination = leases[reservation.destination.entry];
        slots[destination.slot] = Slot{};
        releaseManagedLease(source);
        releaseManagedLease(destination);
        return OverwriteResult::Accepted;
    }

    /**
     * Return one deterministic external action without consuming it.
     *
     * Repeated calls return the same action until some accepted mutation.  A
     * completed full-overwrite destination uses its already allocated serial
     * and is written back first, even when global serial allocation is now
     * exhausted.  Then an obsolete dirty page is written back.  Otherwise the
     * FIFO miss head uses the lowest empty slot, then the lowest unpinned
     * clean victim.  Only when neither exists is the lowest unpinned dirty
     * slot written back.
     */
    MemoryAction
    pendingAction() const
    {
        for (uint16_t slot = 0; slot < PhysicalSlots; ++slot) {
            if (slots[slot].phase == Phase::Dirty &&
                slots[slot].writebackTransaction != NoTransaction &&
                !slotPinned(slot)) {
                return writebackAction(slot);
            }
        }
        if (memorySerialExhausted())
            return {};
        for (uint16_t slot = 0; slot < PhysicalSlots; ++slot) {
            if (slots[slot].phase == Phase::Dirty &&
                !isLive(slots[slot].page) && !slotPinned(slot)) {
                return writebackAction(slot);
            }
        }
        if (queueSize == 0)
            return {};

        // A queued replay may wait behind dirty writeback of the same exact
        // page.  It owns no payload and cannot start a second transaction.
        if (pageHasOwner(missQueue[0]))
            return {};

        for (uint16_t slot = 0; slot < PhysicalSlots; ++slot) {
            if (slots[slot].phase == Phase::Empty)
                return fillAction(slot, false);
        }
        for (uint16_t slot = 0; slot < PhysicalSlots; ++slot) {
            if (slots[slot].phase == Phase::Clean && !slotPinned(slot))
                return fillAction(slot, true);
        }
        for (uint16_t slot = 0; slot < PhysicalSlots; ++slot) {
            if (slots[slot].phase == Phase::Dirty && !slotPinned(slot))
                return writebackAction(slot);
        }
        return {};
    }

    /**
     * Accept exactly the currently advertised action; stale retries are
     * no-ops.
     */
    ActionResult
    acceptAction(const MemoryAction &action)
    {
        if ((action.kind != ActionKind::Fill &&
             action.kind != ActionKind::Writeback) ||
            action.slot >= PhysicalSlots ||
            action.serial == NoTransaction) {
            return ActionResult::Invalid;
        }
        const MemoryAction expected = pendingAction();
        if (action != expected)
            return ActionResult::Stale;

        Slot &slot = slots[action.slot];
        if (action.kind == ActionKind::Fill) {
            if (pageOwnerCount(action.page) != 0)
                return ActionResult::Stale;
            slot = Slot{};
            slot.phase = Phase::Filling;
            slot.page = action.page;
            slot.transaction = action.serial;
            popMiss();
        } else if (action.kind == ActionKind::Writeback) {
            if (pageOwnerCount(action.page) != 1 ||
                slot.page != action.page || slot.phase != Phase::Dirty) {
                return ActionResult::Stale;
            }
            slot.phase = Phase::Writeback;
            slot.transaction = action.serial;
        } else {
            return ActionResult::Invalid;
        }
        if (action.serial > lastMemorySerial)
            lastMemorySerial = action.serial;
        return ActionResult::Accepted;
    }

    /**
     * Cancel one exact, not-yet-accepted miss without disturbing its peers.
     */
    CancelResult
    cancelQueuedMiss(const PageIdentity &page)
    {
        if (!validCoordinates(page))
            return CancelResult::Invalid;
        for (std::size_t index = 0; index < queueSize; ++index) {
            if (missQueue[index] != page)
                continue;
            for (std::size_t next = index + 1; next < queueSize; ++next)
                missQueue[next - 1] = missQueue[next];
            missQueue[--queueSize] = PageIdentity{};
            return CancelResult::Accepted;
        }
        return CancelResult::Stale;
    }

    /**
     * Cancel an exact accepted physical action after its transport has proved
     * that no responder owns a request.  A fill releases Filling.  A
     * writeback reverts to Dirty and retains its exact preallocated serial so
     * an abort-flush can be reissued without losing dirty ownership.
     */
    CancelResult
    cancelAcceptedAction(const MemoryAction &action)
    {
        if ((action.kind != ActionKind::Fill &&
             action.kind != ActionKind::Writeback) ||
            action.slot >= PhysicalSlots || action.serial == NoTransaction ||
            !validCoordinates(action.page)) {
            return CancelResult::Invalid;
        }
        Slot &slot = slots[action.slot];
        if (slot.page != action.page || slot.transaction != action.serial ||
            pageOwnerCount(action.page) != 1) {
            return CancelResult::Stale;
        }
        if (action.kind == ActionKind::Fill) {
            if (slot.phase != Phase::Filling)
                return CancelResult::Stale;
            slot = Slot{};
            return CancelResult::Accepted;
        }
        if (slot.phase != Phase::Writeback)
            return CancelResult::Stale;
        slot.phase = Phase::Dirty;
        slot.transaction = NoTransaction;
        slot.writebackTransaction = action.serial;
        return CancelResult::Accepted;
    }

    ResponseResult
    completeFill(uint16_t slotIndex, const PageIdentity &page,
                 TransactionSerial serial)
    {
        if (slotIndex >= PhysicalSlots || !validCoordinates(page) ||
            serial == NoTransaction) {
            return ResponseResult::Invalid;
        }
        Slot &slot = slots[slotIndex];
        if (slot.phase != Phase::Filling || slot.page != page ||
            slot.transaction != serial || pageOwnerCount(page) != 1) {
            return ResponseResult::Stale;
        }
        if (isLive(page) && descriptors[page.logical].ready[page.page]) {
            slot.phase = Phase::Clean;
            slot.transaction = NoTransaction;
            return ResponseResult::FillInstalled;
        }
        slot = Slot{};
        return ResponseResult::FillReleasedObsolete;
    }

    ResponseResult
    completeWriteback(uint16_t slotIndex, const PageIdentity &page,
                      TransactionSerial serial)
    {
        if (slotIndex >= PhysicalSlots || !validCoordinates(page) ||
            serial == NoTransaction) {
            return ResponseResult::Invalid;
        }
        Slot &slot = slots[slotIndex];
        if (slot.phase != Phase::Writeback || slot.page != page ||
            slot.transaction != serial || pageOwnerCount(page) != 1) {
            return ResponseResult::Stale;
        }
        if (slot.publishOnWriteback && isLive(page))
            descriptors[page.logical].ready[page.page] = true;
        slot = Slot{};
        return ResponseResult::WritebackCompleted;
    }

    /** Acquire one explicit, finite lease on an exact clean/dirty resident. */
    PinReply
    pin(const PageIdentity &page)
    {
        if (!validCoordinates(page))
            return {PinStatus::Invalid, {}};
        if (!isLive(page))
            return {PinStatus::Stale, {}};
        if (!descriptors[page.logical].ready[page.page])
            return {PinStatus::NotReady, {}};
        const uint16_t slot = residentSlot(page);
        if (slot == NoSlot)
            return {PinStatus::NotResident, {}};

        for (uint16_t entry = 0; entry < LeaseEntries; ++entry) {
            LeaseRecord &record = leases[entry];
            if (record.active ||
                record.serial == std::numeric_limits<uint64_t>::max()) {
                continue;
            }
            ++record.serial;
            record.active = true;
            record.slot = slot;
            record.page = page;
            record.purpose = LeasePurpose::General;
            record.overwriteSerial = NoTransaction;
            return {PinStatus::Accepted,
                    {entry, record.serial, record.page}};
        }
        return {PinStatus::Backpressure, {}};
    }

    /** Dirtying requires ownership of a currently active exact lease. */
    LeaseResult
    markDirty(const Lease &lease)
    {
        LeaseRecord *record = matchingLease(lease);
        if (lease.entry >= LeaseEntries || lease.serial == 0)
            return LeaseResult::Invalid;
        if (record == nullptr)
            return LeaseResult::Stale;
        if (record->purpose != LeasePurpose::General)
            return LeaseResult::Managed;
        Slot &slot = slots[record->slot];
        if ((slot.phase != Phase::Clean && slot.phase != Phase::Dirty) ||
            slot.page != record->page) {
            return LeaseResult::Stale;
        }
        slot.phase = Phase::Dirty;
        return LeaseResult::Accepted;
    }

    /** Release exactly one lease; duplicate or forged releases are no-ops. */
    LeaseResult
    release(const Lease &lease)
    {
        if (lease.entry >= LeaseEntries || lease.serial == 0)
            return LeaseResult::Invalid;
        LeaseRecord *record = matchingLease(lease);
        if (record == nullptr)
            return LeaseResult::Stale;
        if (record->purpose != LeasePurpose::General)
            return LeaseResult::Managed;
        releaseLease(*record);
        return LeaseResult::Accepted;
    }

    bool descriptorAllocated(uint16_t logical) const
    {
        return logical < LogicalDescriptors && descriptors[logical].allocated;
    }

    Generation descriptorGeneration(uint16_t logical) const
    {
        return logical < LogicalDescriptors ? descriptors[logical].generation
                                            : 0;
    }

    bool pageIsReady(const PageIdentity &page) const
    {
        return validCoordinates(page) && isLive(page) &&
               descriptors[page.logical].ready[page.page];
    }

    std::size_t missQueueSize() const { return queueSize; }

    PageIdentity queuedMiss(std::size_t index) const
    {
        return index < queueSize ? missQueue[index] : PageIdentity{};
    }

    Phase slotPhase(std::size_t slot) const
    {
        return slot < PhysicalSlots ? slots[slot].phase : Phase::Empty;
    }

    PageIdentity slotIdentity(std::size_t slot) const
    {
        return slot < PhysicalSlots ? slots[slot].page : PageIdentity{};
    }

    TransactionSerial slotTransaction(std::size_t slot) const
    {
        return slot < PhysicalSlots ? slots[slot].transaction : NoTransaction;
    }

    TransactionSerial slotWritebackTransaction(std::size_t slot) const
    {
        return slot < PhysicalSlots ? slots[slot].writebackTransaction
                                    : NoTransaction;
    }

    bool slotPublishesOnWriteback(std::size_t slot) const
    {
        return slot < PhysicalSlots && slots[slot].publishOnWriteback;
    }

    bool canAllocateMemorySerials(std::size_t count) const
    {
        const TransactionSerial maximum =
            std::numeric_limits<TransactionSerial>::max();
        return count <= maximum - lastMemorySerial;
    }

    bool memorySerialExhausted() const
    {
        return lastMemorySerial ==
               std::numeric_limits<TransactionSerial>::max();
    }

    uint16_t residentSlot(const PageIdentity &page) const
    {
        if (!pageIsReady(page))
            return NoSlot;
        for (uint16_t slot = 0; slot < PhysicalSlots; ++slot) {
            if ((slots[slot].phase == Phase::Clean ||
                 slots[slot].phase == Phase::Dirty) &&
                slots[slot].page == page) {
                return slot;
            }
        }
        return NoSlot;
    }

    bool slotIsPinned(std::size_t slot) const
    {
        return slot < PhysicalSlots &&
               slotPinned(static_cast<uint16_t>(slot));
    }

    std::size_t activeLeaseCount() const
    {
        std::size_t count = 0;
        for (const LeaseRecord &lease : leases)
            count += lease.active ? 1 : 0;
        return count;
    }

  private:
    enum class LeasePurpose : uint8_t
    {
        General,
        OverwriteSource,
        OverwriteDestination,
    };

    struct Descriptor
    {
        bool allocated = false;
        Generation generation = 0;
        std::array<bool, PagesPerDescriptor> ready{};
    };

    struct Slot
    {
        Phase phase = Phase::Empty;
        PageIdentity page{};
        TransactionSerial transaction = NoTransaction;
        TransactionSerial writebackTransaction = NoTransaction;
        bool publishOnWriteback = false;
    };

    struct LeaseRecord
    {
        bool active = false;
        uint16_t slot = NoSlot;
        uint64_t serial = 0;
        PageIdentity page{};
        LeasePurpose purpose = LeasePurpose::General;
        TransactionSerial overwriteSerial = NoTransaction;
    };

    bool
    validCoordinates(const PageIdentity &page) const
    {
        return page.logical < LogicalDescriptors &&
               page.page < PagesPerDescriptor && page.generation != 0;
    }

    bool
    isLive(const PageIdentity &page) const
    {
        return validCoordinates(page) && descriptors[page.logical].allocated &&
               descriptors[page.logical].generation == page.generation;
    }

    bool
    queued(const PageIdentity &page) const
    {
        for (std::size_t index = 0; index < queueSize; ++index) {
            if (missQueue[index] == page)
                return true;
        }
        return false;
    }

    std::size_t
    pageOwnerCount(const PageIdentity &page) const
    {
        std::size_t count = 0;
        for (const Slot &slot : slots) {
            if (slot.phase != Phase::Empty && slot.page == page)
                ++count;
        }
        return count;
    }

    bool
    pageHasOwner(const PageIdentity &page) const
    {
        return pageOwnerCount(page) != 0;
    }

    TransactionSerial
    nextMemorySerial() const
    {
        return lastMemorySerial + 1;
    }

    void
    popMiss()
    {
        for (std::size_t index = 1; index < queueSize; ++index)
            missQueue[index - 1] = missQueue[index];
        --queueSize;
        missQueue[queueSize] = PageIdentity{};
    }

    void
    removeQueued(const DescriptorHandle &descriptor)
    {
        std::size_t write = 0;
        for (std::size_t read = 0; read < queueSize; ++read) {
            const PageIdentity &page = missQueue[read];
            if (page.logical == descriptor.logical &&
                page.generation == descriptor.generation) {
                continue;
            }
            missQueue[write++] = page;
        }
        while (queueSize > write)
            missQueue[--queueSize] = PageIdentity{};
    }

    bool
    slotPinned(uint16_t slot) const
    {
        for (const LeaseRecord &lease : leases) {
            if (lease.active && lease.slot == slot &&
                lease.page == slots[slot].page) {
                return true;
            }
        }
        return false;
    }

    uint16_t
    overwriteDestinationSlot(uint16_t sourceSlot) const
    {
        for (uint16_t slot = 0; slot < PhysicalSlots; ++slot) {
            if (slot != sourceSlot && slots[slot].phase == Phase::Empty)
                return slot;
        }
        for (uint16_t slot = 0; slot < PhysicalSlots; ++slot) {
            if (slot != sourceSlot && slots[slot].phase == Phase::Clean &&
                !slotPinned(slot)) {
                return slot;
            }
        }
        return NoSlot;
    }

    MemoryAction
    fillAction(uint16_t slot, bool cleanVictim) const
    {
        MemoryAction action;
        action.kind = ActionKind::Fill;
        action.slot = slot;
        action.serial = nextMemorySerial();
        action.page = missQueue[0];
        action.discardsCleanVictim = cleanVictim;
        if (cleanVictim)
            action.cleanVictim = slots[slot].page;
        return action;
    }

    MemoryAction
    writebackAction(uint16_t slot) const
    {
        MemoryAction action;
        action.kind = ActionKind::Writeback;
        action.slot = slot;
        action.serial = slots[slot].writebackTransaction != NoTransaction
                            ? slots[slot].writebackTransaction
                            : nextMemorySerial();
        action.page = slots[slot].page;
        return action;
    }

    Lease
    activateManagedLease(uint16_t entry, uint16_t slot,
                         const PageIdentity &page, LeasePurpose purpose,
                         TransactionSerial overwriteSerial)
    {
        LeaseRecord &record = leases[entry];
        ++record.serial;
        record.active = true;
        record.slot = slot;
        record.page = page;
        record.purpose = purpose;
        record.overwriteSerial = overwriteSerial;
        return {entry, record.serial, page};
    }

    void
    releaseLease(LeaseRecord &record)
    {
        record.active = false;
        record.slot = NoSlot;
        record.page = PageIdentity{};
        record.purpose = LeasePurpose::General;
        record.overwriteSerial = NoTransaction;
    }

    void
    releaseManagedLease(LeaseRecord &record)
    {
        releaseLease(record);
    }

    bool
    validOverwriteFields(const OverwriteReservation &reservation) const
    {
        return reservation.source.entry < LeaseEntries &&
               reservation.destination.entry < LeaseEntries &&
               reservation.source.entry != reservation.destination.entry &&
               reservation.source.serial != 0 &&
               reservation.destination.serial != 0 &&
               reservation.sourceSlot < PhysicalSlots &&
               reservation.destinationSlot < PhysicalSlots &&
               reservation.sourceSlot != reservation.destinationSlot &&
               validCoordinates(reservation.source.page) &&
               validCoordinates(reservation.destination.page) &&
               reservation.source.page.logical !=
                   reservation.destination.page.logical &&
               reservation.computeSerial != NoTransaction &&
               reservation.writebackSerial != NoTransaction &&
               reservation.computeSerial != reservation.writebackSerial;
    }

    bool
    matchingOverwrite(const OverwriteReservation &reservation,
                      Phase destinationPhase) const
    {
        const LeaseRecord &source = leases[reservation.source.entry];
        const LeaseRecord &destination =
            leases[reservation.destination.entry];
        if (!source.active || !destination.active ||
            source.serial != reservation.source.serial ||
            destination.serial != reservation.destination.serial ||
            source.page != reservation.source.page ||
            destination.page != reservation.destination.page ||
            source.purpose != LeasePurpose::OverwriteSource ||
            destination.purpose != LeasePurpose::OverwriteDestination ||
            source.overwriteSerial != reservation.computeSerial ||
            destination.overwriteSerial != reservation.computeSerial ||
            source.slot >= PhysicalSlots ||
            destination.slot >= PhysicalSlots ||
            source.slot == destination.slot ||
            source.slot != reservation.sourceSlot ||
            destination.slot != reservation.destinationSlot ||
            !isLive(source.page) || !isLive(destination.page) ||
            !descriptors[source.page.logical].ready[source.page.page] ||
            descriptors[destination.page.logical]
                       .ready[destination.page.page]) {
            return false;
        }

        const Slot &sourceSlot = slots[source.slot];
        const Slot &destinationSlot = slots[destination.slot];
        return (sourceSlot.phase == Phase::Clean ||
                sourceSlot.phase == Phase::Dirty) &&
               sourceSlot.page == source.page &&
               destinationSlot.phase == destinationPhase &&
               destinationSlot.page == destination.page &&
               destinationSlot.transaction == reservation.computeSerial &&
               destinationSlot.writebackTransaction ==
                   reservation.writebackSerial &&
               destinationSlot.publishOnWriteback;
    }

    LeaseRecord *
    matchingLease(const Lease &lease)
    {
        if (lease.entry >= LeaseEntries)
            return nullptr;
        LeaseRecord &record = leases[lease.entry];
        if (!record.active || record.serial != lease.serial ||
            record.page != lease.page) {
            return nullptr;
        }
        return &record;
    }

    std::array<Descriptor, LogicalDescriptors> descriptors{};
    std::array<Slot, PhysicalSlots> slots{};
    std::array<PageIdentity, MissQueueEntries> missQueue{};
    std::array<LeaseRecord, LeaseEntries> leases{};
    std::size_t queueSize = 0;
    TransactionSerial lastMemorySerial = NoTransaction;
};

} // namespace gem5

#endif // __MEM_MAA_LOGICAL_SPD_CACHE_CONTROLLER_HH__
