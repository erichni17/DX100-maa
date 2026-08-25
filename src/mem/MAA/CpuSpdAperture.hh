#ifndef __MEM_MAA_CPU_SPD_APERTURE_HH__
#define __MEM_MAA_CPU_SPD_APERTURE_HH__

#include <cstdint>
#include <limits>

namespace gem5::maa
{

class CpuSpdAperture
{
  public:
    enum class Disposition : uint8_t
    {
        Valid,
        DropBoundaryPrefetch,
        PhysicalOutOfRange,
        CrossesPhysicalPayload,
        CrossesLogicalTile,
        InvalidGeometry
    };

    struct Decision
    {
        Disposition disposition = Disposition::InvalidGeometry;
        uint64_t tile = 0;
        uint64_t tileOffset = 0;
        uint64_t logicalTileBytes = 0;
        uint64_t physicalPayloadBytes = 0;
    };

    template <typename ByteEnable>
    static bool
    allBytesEnabled(const ByteEnable &byte_enable, uint64_t packet_bytes)
    {
        if (byte_enable.size() != packet_bytes)
            return false;
        for (uint64_t byte = 0; byte < packet_bytes; ++byte) {
            if (!byte_enable[byte])
                return false;
        }
        return true;
    }

    static constexpr Decision
    classify(uint64_t range_offset, uint64_t packet_bytes,
             uint64_t cache_line_bytes, uint64_t logical_elements,
             uint64_t physical_elements, uint64_t element_bytes,
             bool speculative_prefetch)
    {
        Decision decision;
        if (packet_bytes == 0 || cache_line_bytes == 0 ||
            element_bytes == 0 || logical_elements == 0 ||
            physical_elements == 0 ||
            physical_elements > logical_elements ||
            cache_line_bytes >
                std::numeric_limits<uint64_t>::max() / 2 + 1 ||
            (cache_line_bytes & (cache_line_bytes - 1)) != 0 ||
            packet_bytes != cache_line_bytes ||
            logical_elements >
                std::numeric_limits<uint64_t>::max() / element_bytes ||
            physical_elements >
                std::numeric_limits<uint64_t>::max() / element_bytes) {
            return decision;
        }

        decision.logicalTileBytes = logical_elements * element_bytes;
        decision.physicalPayloadBytes = physical_elements * element_bytes;
        if (decision.logicalTileBytes < packet_bytes ||
            decision.physicalPayloadBytes < packet_bytes ||
            decision.physicalPayloadBytes > decision.logicalTileBytes ||
            range_offset % cache_line_bytes != 0) {
            return decision;
        }

        decision.tile = range_offset / decision.logicalTileBytes;
        decision.tileOffset = range_offset % decision.logicalTileBytes;
        if (decision.tileOffset >
            decision.logicalTileBytes - packet_bytes) {
            decision.disposition = Disposition::CrossesLogicalTile;
            return decision;
        }

        if (decision.tileOffset < decision.physicalPayloadBytes) {
            if (decision.tileOffset >
                decision.physicalPayloadBytes - packet_bytes) {
                decision.disposition =
                    Disposition::CrossesPhysicalPayload;
                return decision;
            }
            decision.disposition = Disposition::Valid;
            return decision;
        }

        decision.disposition = speculative_prefetch
            ? Disposition::DropBoundaryPrefetch
            : Disposition::PhysicalOutOfRange;
        return decision;
    }

    static constexpr const char *
    name(Disposition disposition)
    {
        switch (disposition) {
          case Disposition::Valid:
            return "valid";
          case Disposition::DropBoundaryPrefetch:
            return "drop_boundary_prefetch";
          case Disposition::PhysicalOutOfRange:
            return "physical_out_of_range";
          case Disposition::CrossesPhysicalPayload:
            return "crosses_physical_payload";
          case Disposition::CrossesLogicalTile:
            return "crosses_logical_tile";
          case Disposition::InvalidGeometry:
            return "invalid_geometry";
        }
        return "unknown";
    }
};

} // namespace gem5::maa

#endif // __MEM_MAA_CPU_SPD_APERTURE_HH__
