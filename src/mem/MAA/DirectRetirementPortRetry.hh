#ifndef __MEM_MAA_DIRECT_RETIREMENT_PORT_RETRY_HH__
#define __MEM_MAA_DIRECT_RETIREMENT_PORT_RETRY_HH__

#include <array>
#include <cstddef>
#include <cstdint>

#include "mem/MAA/HybridConsumerPipeline.hh"

namespace gem5 {

/**
 * Fixed packet ownership for direct-retirement cache-port retries.
 *
 * A refused timing request remains owned by its exact translated physical
 * cache port.  Pointer identity is intentional: the packet's sender state
 * carries the full context key and request identity, and release must name
 * both the callback port and that same packet.  The table contains no queue,
 * map, payload copy, or dynamically growing state.
 */
template <class PacketType>
class DirectRetirementPortRetry
{
  public:
    using PacketPtr = PacketType *;
    static constexpr uint8_t PortCount = HybridConsumerPipeline::PortCount;

    bool arm(uint8_t port, PacketPtr packet)
    {
        if (port >= PortCount || packet == nullptr || slots[port] != nullptr)
            return false;
        slots[port] = packet;
        return true;
    }

    bool release(uint8_t port, PacketPtr packet)
    {
        if (port >= PortCount || packet == nullptr || slots[port] != packet)
            return false;
        slots[port] = nullptr;
        return true;
    }

    PacketPtr packet(uint8_t port) const
    {
        return port < PortCount ? slots[port] : nullptr;
    }

    bool occupied(uint8_t port) const { return packet(port) != nullptr; }

    uint8_t count() const
    {
        uint8_t result = 0;
        for (PacketPtr packet : slots)
            result += packet != nullptr;
        return result;
    }

    static constexpr std::size_t chargedControlBytes()
    {
        return sizeof(std::array<PacketPtr, PortCount>);
    }

  private:
    std::array<PacketPtr, PortCount> slots{};
};

static_assert(HybridConsumerPipeline::PortCount == 4);

} // namespace gem5

#endif // __MEM_MAA_DIRECT_RETIREMENT_PORT_RETRY_HH__
