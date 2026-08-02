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
 *  - a non-empty Slot owns exactly one PageIdentity and, while Filling or
 *    Writeback, exactly one matching external memory transaction;
 *  - a successful pin owns one bounded Lease until its exact release;
 *  - a queued miss owns only a PageIdentity, never page payload.
 *
 * Every accepted fill or writeback receives one globally unique, nonzero
 * transaction serial.  Completion requires the exact action kind, slot, page,
 * and serial.  Fill completion installs data only if its identity is still
 * live and ready.  Dirty data enters Writeback only when the caller accepts
 * the explicit action, and that slot cannot be reused until the exact
 * writeback response.  Serial exhaustion permanently suppresses further
 * actions instead of wrapping into an earlier transaction.
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
        Stale,
        Invalid,
    };

    enum class Phase : uint8_t
    {
        Empty,
        Filling,
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

    enum class ResponseResult : uint8_t
    {
        FillInstalled,
        FillReleasedObsolete,
        WritebackCompleted,
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
     * Return one deterministic external action without consuming it.
     *
     * Repeated calls return the same action until some accepted mutation.  An
     * obsolete dirty page is written back first.  Otherwise the FIFO miss head
     * uses the lowest empty slot, then the lowest unpinned clean victim.  Only
     * when neither exists is the lowest unpinned dirty slot written back.
     */
    MemoryAction
    pendingAction() const
    {
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
        if (action.kind == ActionKind::None || action.slot >= PhysicalSlots ||
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
        lastMemorySerial = action.serial;
        return ActionResult::Accepted;
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
        record->active = false;
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

    bool memorySerialExhausted() const
    {
        return lastMemorySerial ==
               std::numeric_limits<TransactionSerial>::max();
    }

    uint16_t residentSlot(const PageIdentity &page) const
    {
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
    };

    struct LeaseRecord
    {
        bool active = false;
        uint16_t slot = NoSlot;
        uint64_t serial = 0;
        PageIdentity page{};
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
        action.serial = nextMemorySerial();
        action.page = slots[slot].page;
        return action;
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
