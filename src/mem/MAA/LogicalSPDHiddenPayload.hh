#ifndef __MEM_MAA_LOGICAL_SPD_HIDDEN_PAYLOAD_HH__
#define __MEM_MAA_LOGICAL_SPD_HIDDEN_PAYLOAD_HH__

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

namespace gem5 {

class MAA;
class SPD;
class LogicalSPDHiddenPayloadTestAccess;

/**
 * Fixed geometry for the private payload slots of the logical SPD cache.
 *
 * This type deliberately has no public members.  SPD uses it to allocate and
 * initialize the appended lanes, MAA may use it when a later patch generates
 * controller-owned micro-ops, and the named test peer verifies the arithmetic.
 * Legacy callers must continue to go through SPD's visible-tile checks.
 */
class LogicalSPDHiddenPayloadLayout
{
    friend class MAA;
    friend class SPD;
    friend class LogicalSPDHiddenPayloadTestAccess;

  private:
    static constexpr uint32_t LogicalSlotsPerMAA = 2;
    static constexpr uint32_t FP64LanesPerSlot = 2;
    static constexpr uint32_t HiddenLanesPerMAA =
        LogicalSlotsPerMAA * FP64LanesPerSlot;
    static constexpr uint32_t LaneElements = 4096;
    static constexpr uint64_t LaneBytes =
        static_cast<uint64_t>(LaneElements) * sizeof(uint32_t);
    static constexpr uint64_t PayloadBytesPerMAA =
        HiddenLanesPerMAA * LaneBytes;

    static bool
    tryMultiply(uint64_t left, uint64_t right, uint64_t *product)
    {
        if (product == nullptr ||
            (left != 0 && right > std::numeric_limits<uint64_t>::max() /
                                      left)) {
            return false;
        }
        *product = left * right;
        return true;
    }

    static bool
    tryAdd(uint64_t left, uint64_t right, uint64_t *sum)
    {
        if (sum == nullptr ||
            right > std::numeric_limits<uint64_t>::max() - left) {
            return false;
        }
        *sum = left + right;
        return true;
    }

    static bool
    tryAllocatedTileCount(uint32_t visible_tile_count, uint32_t num_maas,
                          uint32_t *allocated_tile_count)
    {
        if (num_maas == 0 || allocated_tile_count == nullptr) {
            return false;
        }
        const uint64_t count = static_cast<uint64_t>(visible_tile_count) +
            static_cast<uint64_t>(num_maas) * HiddenLanesPerMAA;
        if (count > std::numeric_limits<uint32_t>::max()) {
            return false;
        }
        *allocated_tile_count = static_cast<uint32_t>(count);
        return true;
    }

    static bool
    tryHiddenLaneTileID(uint32_t visible_tile_count, uint32_t num_maas,
                        int maa_id, int logical_slot, int fp64_lane,
                        uint32_t *tile_id)
    {
        uint32_t allocated_tile_count = 0;
        if (!tryAllocatedTileCount(visible_tile_count, num_maas,
                                   &allocated_tile_count) ||
            tile_id == nullptr || maa_id < 0 ||
            static_cast<uint32_t>(maa_id) >= num_maas ||
            logical_slot < 0 ||
            static_cast<uint32_t>(logical_slot) >= LogicalSlotsPerMAA ||
            fp64_lane < 0 ||
            static_cast<uint32_t>(fp64_lane) >= FP64LanesPerSlot) {
            return false;
        }

        const uint64_t hidden_lane =
            static_cast<uint64_t>(maa_id) * HiddenLanesPerMAA +
            static_cast<uint64_t>(logical_slot) * FP64LanesPerSlot +
            static_cast<uint64_t>(fp64_lane);
        const uint64_t mapped = visible_tile_count + hidden_lane;
        if (mapped >= allocated_tile_count) {
            return false;
        }
        *tile_id = static_cast<uint32_t>(mapped);
        return true;
    }

    static bool
    tryAllocatedPayloadBytes(uint32_t visible_tile_count,
                             uint32_t visible_tile_elements,
                             uint32_t num_maas,
                             std::size_t *allocated_payload_bytes)
    {
        uint32_t allocated_tile_count = 0;
        if (visible_tile_elements == 0 ||
            !tryAllocatedTileCount(visible_tile_count, num_maas,
                                   &allocated_tile_count) ||
            allocated_payload_bytes == nullptr) {
            return false;
        }
        (void)allocated_tile_count;

        uint64_t visible_elements = 0;
        uint64_t visible_bytes = 0;
        uint64_t hidden_bytes = 0;
        uint64_t total_bytes = 0;
        if (!tryMultiply(visible_tile_count, visible_tile_elements,
                         &visible_elements) ||
            !tryMultiply(visible_elements, sizeof(uint32_t),
                         &visible_bytes) ||
            !tryMultiply(num_maas, PayloadBytesPerMAA, &hidden_bytes) ||
            !tryAdd(visible_bytes, hidden_bytes, &total_bytes) ||
            total_bytes > std::numeric_limits<std::size_t>::max()) {
            return false;
        }
        *allocated_payload_bytes = static_cast<std::size_t>(total_bytes);
        return true;
    }

    static bool
    tryAllocatedElementStateCount(uint32_t visible_tile_count,
                                  uint32_t visible_tile_elements,
                                  uint32_t num_maas,
                                  std::size_t *allocated_element_count)
    {
        uint32_t allocated_tile_count = 0;
        if (visible_tile_elements == 0 ||
            !tryAllocatedTileCount(visible_tile_count, num_maas,
                                   &allocated_tile_count) ||
            allocated_element_count == nullptr) {
            return false;
        }
        (void)allocated_tile_count;

        uint64_t visible_count = 0;
        uint64_t hidden_lanes = 0;
        uint64_t hidden_count = 0;
        uint64_t count = 0;
        if (!tryMultiply(visible_tile_count, visible_tile_elements,
                         &visible_count) ||
            !tryMultiply(num_maas, HiddenLanesPerMAA, &hidden_lanes) ||
            !tryMultiply(hidden_lanes, LaneElements, &hidden_count) ||
            !tryAdd(visible_count, hidden_count, &count) ||
            count > std::numeric_limits<std::size_t>::max()) {
            return false;
        }
        *allocated_element_count = static_cast<std::size_t>(count);
        return true;
    }

    static bool
    tryHiddenLaneDataOffsetBytes(uint32_t visible_tile_count,
                                 uint32_t visible_tile_elements,
                                 uint32_t num_maas, int maa_id,
                                 int logical_slot, int fp64_lane,
                                 std::size_t *offset_bytes)
    {
        uint32_t tile_id = 0;
        if (visible_tile_elements == 0 || offset_bytes == nullptr ||
            !tryHiddenLaneTileID(visible_tile_count, num_maas, maa_id,
                                 logical_slot, fp64_lane, &tile_id)) {
            return false;
        }

        uint64_t visible_elements = 0;
        uint64_t visible_bytes = 0;
        if (!tryMultiply(visible_tile_count, visible_tile_elements,
                         &visible_elements) ||
            !tryMultiply(visible_elements, sizeof(uint32_t),
                         &visible_bytes)) {
            return false;
        }
        const uint64_t hidden_lane =
            static_cast<uint64_t>(tile_id - visible_tile_count);
        uint64_t hidden_offset = 0;
        uint64_t offset = 0;
        if (!tryMultiply(hidden_lane, LaneBytes, &hidden_offset) ||
            !tryAdd(visible_bytes, hidden_offset, &offset) ||
            offset > std::numeric_limits<std::size_t>::max()) {
            return false;
        }
        *offset_bytes = static_cast<std::size_t>(offset);
        return true;
    }

    static bool
    initializeHiddenPayload(uint8_t *payload,
                            std::size_t allocated_payload_bytes,
                            uint32_t visible_tile_count,
                            uint32_t visible_tile_elements,
                            uint32_t num_maas)
    {
        std::size_t expected_bytes = 0;
        if (payload == nullptr ||
            !tryAllocatedPayloadBytes(visible_tile_count,
                                      visible_tile_elements, num_maas,
                                      &expected_bytes) ||
            allocated_payload_bytes != expected_bytes) {
            return false;
        }

        uint64_t visible_elements = 0;
        uint64_t visible_bytes_u64 = 0;
        if (!tryMultiply(visible_tile_count, visible_tile_elements,
                         &visible_elements) ||
            !tryMultiply(visible_elements, sizeof(uint32_t),
                         &visible_bytes_u64) ||
            visible_bytes_u64 > std::numeric_limits<std::size_t>::max()) {
            return false;
        }
        const std::size_t visible_bytes =
            static_cast<std::size_t>(visible_bytes_u64);
        std::memset(payload + visible_bytes, 0,
                    allocated_payload_bytes - visible_bytes);
        return true;
    }
};

} // namespace gem5

#endif // __MEM_MAA_LOGICAL_SPD_HIDDEN_PAYLOAD_HH__
