#ifndef __MEM_MAA_VIRTUAL_COMBINER_PAGE_ORDER_HH__
#define __MEM_MAA_VIRTUAL_COMBINER_PAGE_ORDER_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace gem5
{

/**
 * Incrementally maintained full-line ready queues for virtual retirement.
 *
 * Page rank is supplied by the production address-range check.  Each combiner
 * slot contributes only intrusive queue links and a 4-bit page id; selection
 * is a fixed MaxPages head encoder rather than a slot-array priority walk.
 */
class VirtualCombinerPageOrder
{
  public:
    static constexpr uint32_t ConsumerPageWords = 4096;
    static constexpr uint32_t MaxPages = 16;

    void reset(size_t slots)
    {
        next.assign(slots, NotQueued);
        prev.assign(slots, NotQueued);
        queued.assign(slots, false);
        slot_page.assign(slots, 0);
        head.fill(NotQueued);
        tail.fill(NotQueued);
    }

    static bool
    linePage(uint64_t line_addr, uint64_t output_begin, uint64_t output_end,
             uint32_t word_bytes, uint32_t &page)
    {
        // Bounds precede subtraction so a wrapped range cannot become a
        // spurious later page.
        if (word_bytes == 0 || output_begin >= output_end ||
            line_addr < output_begin || line_addr >= output_end)
            return false;
        const uint64_t page_bytes =
            static_cast<uint64_t>(ConsumerPageWords) * word_bytes;
        if (page_bytes == 0)
            return false;
        page = static_cast<uint32_t>((line_addr - output_begin) / page_bytes);
        return true;
    }

    bool enqueue(size_t slot, uint32_t page)
    {
        if (slot >= next.size() || page >= MaxPages || queued[slot])
            return false;
        slot_page[slot] = static_cast<uint8_t>(page);
        prev[slot] = tail[page];
        if (tail[page] != NotQueued)
            next[tail[page]] = static_cast<int>(slot);
        else
            head[page] = static_cast<int>(slot);
        tail[page] = static_cast<int>(slot);
        queued[slot] = true;
        return true;
    }

    bool remove(size_t slot)
    {
        if (slot >= next.size() || !queued[slot])
            return false;
        const uint32_t page = slot_page[slot];
        const int before = prev[slot];
        const int after = next[slot];
        if (before == NotQueued)
            head[page] = after;
        else
            next[before] = after;
        if (after == NotQueued)
            tail[page] = before;
        else
            prev[after] = before;
        next[slot] = NotQueued;
        prev[slot] = NotQueued;
        queued[slot] = false;
        return true;
    }

    // Production eviction uses this name at the single transition where a
    // full line stops being full, regardless of masked or word retirement.
    bool retireFullLine(size_t slot) { return remove(slot); }

    int firstReady(uint32_t &page) const
    {
        for (uint32_t candidate = 0; candidate < MaxPages; ++candidate) {
            if (head[candidate] != NotQueued) {
                page = candidate;
                return head[candidate];
            }
        }
        return NotQueued;
    }

    bool hasReadyLater(uint32_t page) const
    {
        for (uint32_t candidate = page + 1; candidate < MaxPages;
             ++candidate) {
            if (head[candidate] != NotQueued)
                return true;
        }
        return false;
    }

  private:
    static constexpr int NotQueued = -1;
    std::array<int, MaxPages> head{};
    std::array<int, MaxPages> tail{};
    std::vector<int> next;
    std::vector<int> prev;
    std::vector<bool> queued;
    std::vector<uint8_t> slot_page;
};

} // namespace gem5

#endif // __MEM_MAA_VIRTUAL_COMBINER_PAGE_ORDER_HH__
