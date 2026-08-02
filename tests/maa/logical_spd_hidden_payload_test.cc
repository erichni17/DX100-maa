#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <set>
#include <vector>

#include "mem/MAA/LogicalSPDHiddenPayload.hh"

namespace gem5 {

class LogicalSPDHiddenPayloadTestAccess
{
  public:
    static uint32_t logicalSlotsPerMAA()
    {
        return LogicalSPDHiddenPayloadLayout::LogicalSlotsPerMAA;
    }

    static uint32_t fp64LanesPerSlot()
    {
        return LogicalSPDHiddenPayloadLayout::FP64LanesPerSlot;
    }

    static uint32_t hiddenLanesPerMAA()
    {
        return LogicalSPDHiddenPayloadLayout::HiddenLanesPerMAA;
    }

    static uint32_t laneElements()
    {
        return LogicalSPDHiddenPayloadLayout::LaneElements;
    }

    static uint64_t laneBytes()
    {
        return LogicalSPDHiddenPayloadLayout::LaneBytes;
    }

    static uint64_t payloadBytesPerMAA()
    {
        return LogicalSPDHiddenPayloadLayout::PayloadBytesPerMAA;
    }

    static bool tryAllocatedTileCount(uint32_t visible, uint32_t maas,
                                      uint32_t *allocated)
    {
        return LogicalSPDHiddenPayloadLayout::tryAllocatedTileCount(
            visible, maas, allocated);
    }

    static bool tryHiddenLaneTileID(uint32_t visible, uint32_t maas,
                                    int maa, int slot, int lane,
                                    uint32_t *tile)
    {
        return LogicalSPDHiddenPayloadLayout::tryHiddenLaneTileID(
            visible, maas, maa, slot, lane, tile);
    }

    static bool tryAllocatedPayloadBytes(uint32_t visible,
                                         uint32_t visible_elements,
                                         uint32_t maas,
                                         std::size_t *bytes)
    {
        return LogicalSPDHiddenPayloadLayout::tryAllocatedPayloadBytes(
            visible, visible_elements, maas, bytes);
    }

    static bool tryAllocatedElementStateCount(uint32_t visible,
                                              uint32_t visible_elements,
                                              uint32_t maas,
                                              std::size_t *count)
    {
        return LogicalSPDHiddenPayloadLayout::tryAllocatedElementStateCount(
            visible, visible_elements, maas, count);
    }

    static bool tryHiddenLaneDataOffsetBytes(uint32_t visible,
                                             uint32_t visible_elements,
                                             uint32_t maas, int maa,
                                             int slot, int lane,
                                             std::size_t *offset)
    {
        return LogicalSPDHiddenPayloadLayout::tryHiddenLaneDataOffsetBytes(
            visible, visible_elements, maas, maa, slot, lane, offset);
    }

    static bool initializeHiddenPayload(uint8_t *payload,
                                        std::size_t allocated_bytes,
                                        uint32_t visible,
                                        uint32_t visible_elements,
                                        uint32_t maas)
    {
        return LogicalSPDHiddenPayloadLayout::initializeHiddenPayload(
            payload, allocated_bytes, visible, visible_elements, maas);
    }
};

} // namespace gem5

namespace {

using Access = gem5::LogicalSPDHiddenPayloadTestAccess;

void
require(bool condition, const char *message)
{
    if (!condition) {
        std::cerr << "FAIL: " << message << '\n';
        std::exit(1);
    }
}

void
testExactGeometryAndAccounting()
{
    require(Access::logicalSlotsPerMAA() == 2,
            "two logical payload slots per MAA");
    require(Access::fp64LanesPerSlot() == 2,
            "two FP64 lanes per logical slot");
    require(Access::hiddenLanesPerMAA() == 4,
            "four hidden 32-bit lanes per MAA");
    require(Access::laneElements() == 4096,
            "every hidden lane has 4096 elements");
    require(Access::laneBytes() == 16384,
            "every hidden lane has 16384 payload bytes");
    require(Access::payloadBytesPerMAA() == 65536,
            "hidden payload is exactly 65536 bytes per MAA");
    require(4 * Access::payloadBytesPerMAA() == 262144,
            "hidden payload is exactly 262144 bytes for four MAAs");

    uint32_t allocated = 0;
    require(Access::tryAllocatedTileCount(48, 4, &allocated),
            "four-MAA allocated tile count is representable");
    require(allocated == 64,
            "allocated count appends sixteen lanes to visible count");
}

void
testMappingBoundsAndIsolation()
{
    constexpr uint32_t visible = 48;
    constexpr uint32_t maas = 4;
    std::set<uint32_t> mapped;

    for (int maa = 0; maa < static_cast<int>(maas); ++maa) {
        std::set<uint32_t> per_maa;
        for (int slot = 0; slot < 2; ++slot) {
            uint32_t base = 0;
            require(Access::tryHiddenLaneTileID(
                        visible, maas, maa, slot, 0, &base),
                    "valid hidden slot base maps");
            for (int lane = 0; lane < 2; ++lane) {
                uint32_t tile = 0;
                require(Access::tryHiddenLaneTileID(
                            visible, maas, maa, slot, lane, &tile),
                        "valid hidden lane maps");
                const uint32_t expected =
                    visible + maa * 4 + slot * 2 + lane;
                require(tile == expected, "hidden lane mapping is exact");
                require(tile >= visible && tile < visible + maas * 4,
                        "hidden lane is outside visible IDs and in "
                        "allocation");
                require(tile == base + static_cast<uint32_t>(lane),
                        "FP64 lane IDs are adjacent");
                require(per_maa.insert(tile).second,
                        "one MAA has no hidden-lane alias");
                require(mapped.insert(tile).second,
                        "different MAAs have isolated hidden lanes");
            }
        }
        require(per_maa.size() == 4,
                "each MAA owns exactly four hidden lanes");
    }
    require(mapped.size() == 16,
            "four MAAs own exactly sixteen unique hidden lanes");

    const struct {
        int maa;
        int slot;
        int lane;
    } invalid[] = {
        {-1, 0, 0}, {4, 0, 0}, {0, -1, 0},
        {0, 2, 0}, {0, 0, -1}, {0, 0, 2},
    };
    for (const auto &coordinates : invalid) {
        uint32_t untouched = 0xdeadbeefU;
        require(!Access::tryHiddenLaneTileID(
                    visible, maas, coordinates.maa, coordinates.slot,
                    coordinates.lane, &untouched),
                "out-of-range hidden coordinates fail closed");
        require(untouched == 0xdeadbeefU,
                "failed mapping does not publish an ID");
    }
    require(!Access::tryHiddenLaneTileID(visible, maas, 0, 0, 0, nullptr),
            "null mapping output fails closed");

    uint32_t untouched = 0xabcdef01U;
    require(!Access::tryAllocatedTileCount(visible, 0, &untouched),
            "zero-MAA geometry fails closed");
    require(untouched == 0xabcdef01U,
            "invalid geometry does not publish a count");
    const uint32_t too_many_visible =
        std::numeric_limits<uint32_t>::max() - 3;
    require(!Access::tryAllocatedTileCount(too_many_visible, 1, &untouched),
            "tile-count overflow fails closed");
    require(untouched == 0xabcdef01U,
            "overflow does not publish a count");

    std::size_t untouched_size = 0x12345U;
    require(!Access::tryAllocatedPayloadBytes(
                std::numeric_limits<uint32_t>::max() - 4,
                std::numeric_limits<uint32_t>::max(), 1,
                &untouched_size),
            "payload-byte arithmetic overflow fails closed");
    require(untouched_size == 0x12345U,
            "payload-byte overflow does not publish a size");
    require(!Access::tryAllocatedElementStateCount(
                visible, 0, maas, &untouched_size),
            "zero visible element capacity fails closed");
    require(untouched_size == 0x12345U,
            "invalid element-state geometry does not publish a size");
}

void
testFixedHiddenStrideAndInitialization()
{
    constexpr uint32_t visible = 12;
    constexpr uint32_t visible_elements = 16384;
    constexpr uint32_t maas = 4;
    const std::size_t visible_bytes = static_cast<std::size_t>(visible) *
        visible_elements * sizeof(uint32_t);

    std::size_t allocated_bytes = 0;
    require(Access::tryAllocatedPayloadBytes(
                visible, visible_elements, maas, &allocated_bytes),
            "allocated payload byte count is representable");
    require(allocated_bytes == visible_bytes + 262144,
            "hidden payload accounting is independent of visible stride");

    std::size_t element_states = 0;
    require(Access::tryAllocatedElementStateCount(
                visible, visible_elements, maas, &element_states),
            "element-state count is representable");
    require(element_states ==
                static_cast<std::size_t>(visible) * visible_elements +
                    maas * 4 * 4096,
            "element-state allocation charges every appended lane word");

    for (int maa = 0; maa < static_cast<int>(maas); ++maa) {
        for (int slot = 0; slot < 2; ++slot) {
            for (int lane = 0; lane < 2; ++lane) {
                std::size_t offset = 0;
                require(Access::tryHiddenLaneDataOffsetBytes(
                            visible, visible_elements, maas, maa, slot,
                            lane, &offset),
                        "valid hidden lane byte offset maps");
                const std::size_t ordinal =
                    static_cast<std::size_t>(maa * 4 + slot * 2 + lane);
                require(offset == visible_bytes + ordinal * 4096 * 4,
                        "hidden lanes use a fixed 4096-word stride");
            }
        }
    }

    std::vector<uint8_t> guarded(allocated_bytes + 2, 0xa5);
    uint8_t *payload = guarded.data() + 1;
    require(Access::initializeHiddenPayload(
                payload, allocated_bytes, visible, visible_elements, maas),
            "exact allocation initializes hidden payload");
    require(guarded.front() == 0xa5 && guarded.back() == 0xa5,
            "hidden initialization preserves outer guards");
    for (std::size_t i = 0; i < visible_bytes; ++i) {
        require(payload[i] == 0xa5,
                "hidden initialization does not alter visible payload");
    }
    for (std::size_t i = visible_bytes; i < allocated_bytes; ++i) {
        require(payload[i] == 0,
                "every hidden payload byte initializes to zero");
    }

    std::vector<uint8_t> wrong_size(allocated_bytes, 0x6b);
    require(!Access::initializeHiddenPayload(
                wrong_size.data(), allocated_bytes - 1, visible,
                visible_elements, maas),
            "truncated allocation fails initialization closed");
    for (uint8_t byte : wrong_size) {
        require(byte == 0x6b,
                "failed initialization does not partially mutate payload");
    }
}

} // anonymous namespace

int
main()
{
    testExactGeometryAndAccounting();
    testMappingBoundsAndIsolation();
    testFixedHiddenStrideAndInitialization();
    std::cout << "PASS logical_spd_hidden_payload_test\n";
    return 0;
}
