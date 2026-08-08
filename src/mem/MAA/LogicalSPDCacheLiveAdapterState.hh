#ifndef __MEM_MAA_LOGICAL_SPD_CACHE_LIVE_ADAPTER_STATE_HH__
#define __MEM_MAA_LOGICAL_SPD_CACHE_LIVE_ADAPTER_STATE_HH__

#include <array>
#include <cstddef>
#include <cstdint>

namespace gem5 {

/**
 * Finite live cache-port ownership for refused logical SPD requests.
 *
 * A slot belongs to one exact logical execution until its send succeeds.  A
 * local response-credit release and a downstream request-retry callback are
 * different authorities; only the event matching the refusal can mint the
 * slot's one-shot service permit.
 */
class LogicalSPDCacheLiveAdapterState
{
  public:
    static constexpr std::size_t PortCount = 4;
    using Owner = uint64_t;
    static constexpr Owner NoOwner = 0;

    enum class WaitAuthority : uint8_t
    {
        None,
        LocalResponseCapacity,
        DownstreamRequestRetry,
    };

    enum class PortEvent : uint8_t
    {
        ResponseCapacityReleased,
        DownstreamRequestRetry,
    };

    struct Notification
    {
        bool granted = false;
        Owner owner = NoOwner;
    };

    bool arm(Owner owner, uint8_t actualPort, WaitAuthority authority)
    {
        if (owner == NoOwner || actualPort >= PortCount ||
            authority == WaitAuthority::None) {
            return false;
        }
        for (std::size_t port = 0; port < slots.size(); ++port) {
            if (slots[port].armed && slots[port].owner == owner &&
                port != actualPort) {
                return false;
            }
        }
        Slot &slot = slots[actualPort];
        if (slot.armed && slot.owner != owner)
            return false;
        if (slot.permitted)
            return false;
        slot.armed = true;
        slot.permitted = false;
        slot.owner = owner;
        slot.authority = authority;
        return true;
    }

    Notification notify(uint8_t actualPort, PortEvent event)
    {
        if (actualPort >= PortCount)
            return {};
        Slot &slot = slots[actualPort];
        if (!slot.armed || slot.permitted ||
            !matches(slot.authority, event)) {
            return {};
        }
        slot.permitted = true;
        return {true, slot.owner};
    }

    bool consume(Owner owner, uint8_t actualPort,
                 WaitAuthority authority)
    {
        if (owner == NoOwner || actualPort >= PortCount)
            return false;
        Slot &slot = slots[actualPort];
        if (!slot.armed || !slot.permitted || slot.owner != owner ||
            slot.authority != authority) {
            return false;
        }
        slot.permitted = false;
        return true;
    }

    bool release(Owner owner)
    {
        if (owner == NoOwner)
            return false;
        for (Slot &slot : slots) {
            if (slot.armed && slot.owner == owner) {
                slot = Slot{};
                return true;
            }
        }
        return false;
    }

    bool ownerPending(uint8_t actualPort) const
    {
        return actualPort < PortCount && slots[actualPort].armed;
    }

    Owner pendingOwner(uint8_t actualPort) const
    {
        return ownerPending(actualPort) ? slots[actualPort].owner : NoOwner;
    }

    WaitAuthority pendingAuthority(uint8_t actualPort) const
    {
        return ownerPending(actualPort) ? slots[actualPort].authority
                                        : WaitAuthority::None;
    }

    bool permitPending(uint8_t actualPort) const
    {
        return ownerPending(actualPort) && slots[actualPort].permitted;
    }

  private:
    struct Slot
    {
        bool armed = false;
        bool permitted = false;
        Owner owner = NoOwner;
        WaitAuthority authority = WaitAuthority::None;
    };

    static bool matches(WaitAuthority authority, PortEvent event)
    {
        return (authority == WaitAuthority::LocalResponseCapacity &&
                event == PortEvent::ResponseCapacityReleased) ||
               (authority == WaitAuthority::DownstreamRequestRetry &&
                event == PortEvent::DownstreamRequestRetry);
    }

    std::array<Slot, PortCount> slots{};
};

} // namespace gem5

#endif // __MEM_MAA_LOGICAL_SPD_CACHE_LIVE_ADAPTER_STATE_HH__
