#ifndef __MEM_MAA_HYBRID_PAGE_MATERIALIZATION_STATE_HH__
#define __MEM_MAA_HYBRID_PAGE_MATERIALIZATION_STATE_HH__

#include <array>
#include <bitset>
#include <cstddef>
#include <cstdint>

#include "mem/MAA/HybridConsumerPipeline.hh"

namespace gem5 {

/**
 * Exact live destination and direct-staging state for one materializer
 * lifetime.  The token/generation/incarnation owner remains in the enclosing
 * execution; each slot adds the disjoint logical page tag needed to select
 * its physical SPD destination and private staging maps.
 *
 * This control state owns no producer or cache-line payload. Both pages
 * continue to share HybridConsumerPipeline's sixteen line buffers and the
 * caller's existing cache ports. Capacity two nevertheless consumes a second
 * full ProducerPageElements result destination in the physical SPD; it is an
 * 8K-result sensitivity, not an iso-area 4K materializer.
 */
class HybridPageMaterializationState
{
  public:
    using Pipeline = HybridConsumerPipeline;

    static constexpr uint8_t DefaultActivePageCapacity = 1;
    static constexpr uint8_t MaxActivePageCapacity = 2;
    static constexpr std::size_t MaxStagedWords =
        Pipeline::ProducerPageElements;
    static constexpr std::size_t MaxStagedLines =
        Pipeline::ProducerPageElements * sizeof(uint64_t) /
        Pipeline::LineBytes;

    // Exact packed-RTL accounting. A per-page line count spans 0..512 and
    // masked fragments span 0..4096. The staging maps are control validity,
    // not hidden producer payload.
    static constexpr std::size_t PageIdentityBits = 2;
    static constexpr std::size_t LineCounterBits = 10;
    static constexpr std::size_t FragmentCounterBits = 13;
    static constexpr std::size_t StagingMapBits =
        MaxStagedWords + 2 * MaxStagedLines;
    static constexpr std::size_t PerPageCounterBits =
        4 * LineCounterBits + FragmentCounterBits;
    static constexpr std::size_t LegacySinglePageIdentityBits = 3;
    static constexpr std::size_t ActivePageBitmapBits =
        Pipeline::ProducerPages;
    static constexpr std::size_t CapacityConfigBits = 1;
    static constexpr std::size_t CapacityTwoAdditionalResultElements =
        Pipeline::ProducerPageElements;

    enum class ActivationResult : uint8_t
    {
        Accepted,
        Invalid,
        Duplicate,
        AtCapacity,
    };

    struct Page
    {
        bool active = false;
        uint8_t page = Pipeline::NoProducerPage;
        int destinationTile = -1;
        std::bitset<MaxStagedWords> stagedWords{};
        std::bitset<MaxStagedLines> stagedDisallowed{};
        std::bitset<MaxStagedLines> stagedFallbackCounted{};
        uint16_t forwardedLines = 0;
        uint16_t stagedDirectLines = 0;
        uint16_t stagedDirectFragments = 0;
        uint16_t stagedDirectFallbackLines = 0;
        uint16_t cacheReadFallbackLines = 0;
    };

    static constexpr bool validCapacity(uint8_t capacity)
    {
        return capacity >= DefaultActivePageCapacity &&
            capacity <= MaxActivePageCapacity;
    }

    static constexpr std::size_t bitsForValues(std::size_t values)
    {
        if (values <= 1)
            return 0;
        std::size_t bits = 0;
        --values;
        while (values != 0) {
            ++bits;
            values >>= 1;
        }
        return bits;
    }

    static constexpr std::size_t packedPageControlBits(
        std::size_t tileCount)
    {
        return 1 + PageIdentityBits + bitsForValues(tileCount) +
            StagingMapBits + PerPageCounterBits;
    }

    static constexpr std::size_t packedCapacityTwoAdditionalBits(
        std::size_t tileCount)
    {
        return packedPageControlBits(tileCount) + CapacityConfigBits +
            (ActivePageBitmapBits - LegacySinglePageIdentityBits);
    }

    static constexpr std::size_t packedCapacityTwoAdditionalBytes(
        std::size_t tileCount)
    {
        return (packedCapacityTwoAdditionalBits(tileCount) + 7) / 8;
    }

    ActivationResult activate(uint8_t page, int destinationTile,
                              uint8_t capacity)
    {
        if (!validCapacity(capacity) || page >= Pipeline::ProducerPages ||
            destinationTile < 0)
            return ActivationResult::Invalid;
        if (find(page) != nullptr)
            return ActivationResult::Duplicate;
        if (activeCount() >= capacity)
            return ActivationResult::AtCapacity;
        for (Page &candidate : pages) {
            if (candidate.active)
                continue;
            candidate = {};
            candidate.active = true;
            candidate.page = page;
            candidate.destinationTile = destinationTile;
            return ActivationResult::Accepted;
        }
        return ActivationResult::AtCapacity;
    }

    Page *find(uint8_t page)
    {
        for (Page &candidate : pages) {
            if (candidate.active && candidate.page == page)
                return &candidate;
        }
        return nullptr;
    }

    const Page *find(uint8_t page) const
    {
        for (const Page &candidate : pages) {
            if (candidate.active && candidate.page == page)
                return &candidate;
        }
        return nullptr;
    }

    Page *findLine(uint16_t line, uint16_t pageLines)
    {
        if (pageLines == 0)
            return nullptr;
        const uint16_t page = line / pageLines;
        return page < Pipeline::ProducerPages
            ? find(static_cast<uint8_t>(page)) : nullptr;
    }

    const Page *findLine(uint16_t line, uint16_t pageLines) const
    {
        if (pageLines == 0)
            return nullptr;
        const uint16_t page = line / pageLines;
        return page < Pipeline::ProducerPages
            ? find(static_cast<uint8_t>(page)) : nullptr;
    }

    bool retire(uint8_t page)
    {
        Page *owned = find(page);
        if (owned == nullptr)
            return false;
        *owned = {};
        return true;
    }

    uint8_t activeCount() const
    {
        uint8_t count = 0;
        for (const Page &candidate : pages)
            count += candidate.active;
        return count;
    }

    const std::array<Page, MaxActivePageCapacity> &slots() const
    {
        return pages;
    }

  private:
    std::array<Page, MaxActivePageCapacity> pages{};
};

static_assert(HybridPageMaterializationState::DefaultActivePageCapacity == 1);
static_assert(HybridPageMaterializationState::MaxActivePageCapacity == 2);
static_assert(HybridPageMaterializationState::MaxStagedLines == 512);
static_assert(HybridPageMaterializationState::StagingMapBits == 5120);
static_assert(HybridPageMaterializationState::PerPageCounterBits == 53);
static_assert(HybridPageMaterializationState::
                  CapacityTwoAdditionalResultElements == 4096);

} // namespace gem5

#endif // __MEM_MAA_HYBRID_PAGE_MATERIALIZATION_STATE_HH__
