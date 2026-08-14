#ifndef __MEM_MAA_INACTIVE_PAYLOAD_FALLBACK_TABLE_HH__
#define __MEM_MAA_INACTIVE_PAYLOAD_FALLBACK_TABLE_HH__

#include <array>
#include <cstdint>

#include "mem/MAA/HybridConsumerContextQueue.hh"

namespace gem5 {

/**
 * Fixed four-slot holding table for exact inactive-payload lookup misses.
 *
 * A fallback holds only a stale request identity.  Before coherent issue, it
 * must be rebound through HybridConsumerContextQueue, so a retained entry
 * never owns a line-buffer credit.  Entries are removed by exact owner on
 * materialization completion or replacement, preventing an old incarnation
 * from surviving into a reused context slot.
 */
class InactivePayloadFallbackTable
{
  public:
    using Queue = HybridConsumerContextQueue;
    using Request = Queue::Request;
    using ContextKey = Queue::ContextKey;

    static constexpr uint8_t SlotCount = Queue::ContextCount;
    static constexpr uint8_t NoSlot = SlotCount;

    enum class ResolveResult : uint8_t
    {
        None,
        Rebound,
    };

    bool retain(const Request &request)
    {
        if (request.request.kind != Queue::Pipeline::Kind::ReadBacking)
            return false;
        for (Entry &entry : entries) {
            if (entry.pending)
                continue;
            entry.pending = true;
            entry.request = request;
            return true;
        }
        return false;
    }

    ResolveResult resolve(const Queue &contexts, Request *request,
                          uint8_t *slot)
    {
        if (request != nullptr)
            *request = {};
        if (slot != nullptr)
            *slot = NoSlot;
        for (uint8_t offset = 0; offset < SlotCount; ++offset) {
            const uint8_t index = (next + offset) % SlotCount;
            Entry &entry = entries[index];
            if (!entry.pending)
                continue;
            Request rebound;
            const auto result = contexts.rebindMaterializationRead(
                entry.request, &rebound);
            if (result == Queue::MaterializationReadRebind::Rebound) {
                if (request != nullptr)
                    *request = rebound;
                if (slot != nullptr)
                    *slot = index;
                next = (index + 1) % SlotCount;
                return ResolveResult::Rebound;
            }
            if (result == Queue::MaterializationReadRebind::Closed)
                entry = {};
        }
        return ResolveResult::None;
    }

    bool clear(uint8_t slot)
    {
        if (slot >= SlotCount || !entries[slot].pending)
            return false;
        entries[slot] = {};
        return true;
    }

    uint8_t clearOwner(const ContextKey &owner)
    {
        uint8_t cleared = 0;
        for (Entry &entry : entries) {
            if (!entry.pending || !sameKey(entry.request.owner, owner))
                continue;
            entry = {};
            ++cleared;
        }
        return cleared;
    }

    uint8_t pendingCount() const
    {
        uint8_t count = 0;
        for (const Entry &entry : entries)
            count += entry.pending;
        return count;
    }

  private:
    struct Entry
    {
        bool pending = false;
        Request request{};
    };

    static bool sameKey(const ContextKey &left, const ContextKey &right)
    {
        return left.tokenTile == right.tokenTile &&
            left.generation == right.generation &&
            left.incarnation == right.incarnation;
    }

    std::array<Entry, SlotCount> entries{};
    uint8_t next = 0;
};

} // namespace gem5

#endif // __MEM_MAA_INACTIVE_PAYLOAD_FALLBACK_TABLE_HH__
