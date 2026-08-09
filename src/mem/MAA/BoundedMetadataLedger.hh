#ifndef __MEM_MAA_BOUNDED_METADATA_LEDGER_HH__
#define __MEM_MAA_BOUNDED_METADATA_LEDGER_HH__

#include <cstddef>
#include <cstdint>

namespace gem5
{

/** Source-semantic byte accounting, not C++ heap or synthesized area. */
struct BoundedMetadataLedger
{
    uint32_t wordEntries = 0;
    uint32_t offsetLinkEntries = 0;
    uint32_t rowDirectoryEntries = 0;
    uint32_t rowLineEntries = 0;
    uint32_t scratchpadElementsPerTile = 0;
    uint32_t visibleTiles = 0;

    size_t wordBytes() const
    {
        // Logical iteration and response word id share OffsetTableEntry.
        return static_cast<size_t>(wordEntries) * 2 * sizeof(int);
    }
    size_t offsetBytes() const
    {
        // next link, validity, and bounded free-stack id.
        return static_cast<size_t>(offsetLinkEntries) *
            (2 * sizeof(int) + 1);
    }
    size_t rowDirectoryBytes() const
    {
        // grow address plus valid/sent state.
        return static_cast<size_t>(rowDirectoryEntries) *
            (sizeof(uint64_t) + 2);
    }
    size_t rowLineBytes() const
    {
        // line address, first/last link, valid/claimed state.
        return static_cast<size_t>(rowLineEntries) *
            (sizeof(uint64_t) + 2 * sizeof(int) + 2);
    }
    size_t reorderMetadataBytes() const
    {
        return wordBytes() + offsetBytes() + rowDirectoryBytes() +
            rowLineBytes();
    }
    size_t scratchpadPayloadBytes() const
    {
        return static_cast<size_t>(scratchpadElementsPerTile) * visibleTiles *
            sizeof(uint32_t);
    }
};

} // namespace gem5

#endif // __MEM_MAA_BOUNDED_METADATA_LEDGER_HH__
