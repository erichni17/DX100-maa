#ifndef __MEM_MAA_VIRTUAL_RESPONSE_PAYLOAD_STORE_HH__
#define __MEM_MAA_VIRTUAL_RESPONSE_PAYLOAD_STORE_HH__

#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace gem5
{

/**
 * Fixed source-line payloads for the unpacked virtual-response mode.
 *
 * Response metadata and packed useful words remain in VirtualResponseSlot.
 * This store is deliberately empty in packed mode so selecting packed words
 * does not also allocate one inactive cache line for every metadata slot.
 */
class VirtualResponsePayloadStore
{
  public:
    static constexpr std::size_t LineBytes = 64;
    using Line = std::array<uint8_t, LineBytes>;

    void configure(std::size_t responseSlots, bool packedResponse)
    {
        assert(!configured);
        assert(responseSlots != 0);
        configured = true;
        slots = responseSlots;
        packed = packedResponse;
        if (!packed)
            lines.resize(slots);
    }

    bool packedResponse() const { return packed; }
    std::size_t slotCapacity() const { return slots; }
    std::size_t lineCount() const { return lines.size(); }
    std::size_t payloadBytes() const { return lineCount() * LineBytes; }

    uint8_t *lineData(std::size_t slot)
    {
        assert(configured && !packed);
        return lines.at(slot).data();
    }

    const uint8_t *lineData(std::size_t slot) const
    {
        assert(configured && !packed);
        return lines.at(slot).data();
    }

    void reset()
    {
        for (auto &line : lines)
            line.fill(0);
    }

  private:
    bool configured = false;
    bool packed = true;
    std::size_t slots = 0;
    std::vector<Line> lines;
};

} // namespace gem5

#endif // __MEM_MAA_VIRTUAL_RESPONSE_PAYLOAD_STORE_HH__
