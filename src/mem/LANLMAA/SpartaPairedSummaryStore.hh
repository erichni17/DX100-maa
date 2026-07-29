#ifndef __MEM_LANLMAA_SPARTA_PAIRED_SUMMARY_STORE_HH__
#define __MEM_LANLMAA_SPARTA_PAIRED_SUMMARY_STORE_HH__

#include <array>
#include <cstddef>
#include <cstdint>

#include "mem/LANLMAA/SharedOverlayCost.hh"
#include "mem/LANLMAA/SpartaFusedCellModel.hh"

namespace gem5
{
namespace lanlmaa
{

class SpartaPairedSummaryStore
{
  public:
    struct Entry
    {
        std::array<uint64_t, SpartaFusedChannels> sums{};
        uint32_t eligible = 0;
        bool valid = false;
    };

  private:
    std::array<Entry, SharedOperationStore.entries> entries{};
    std::array<bool, SharedPairBanks> banksBusy{};
    size_t configuredEntries = 0;

  public:
    bool
    configure(size_t count)
    {
        if (count == 0 || count > entries.size()) {
            return false;
        }
        clear();
        configuredEntries = count;
        for (size_t index = 0; index < count; ++index) {
            entries[index].valid = true;
        }
        return true;
    }

    void
    clear()
    {
        entries.fill(Entry{});
        banksBusy.fill(false);
        configuredEntries = 0;
    }

    void
    beginCycle()
    {
        banksBusy.fill(false);
    }

    size_t
    size() const
    {
        return configuredEntries;
    }

    size_t
    bank(size_t index) const
    {
        return index % banksBusy.size();
    }

    bool
    bankAvailable(size_t index) const
    {
        return index < configuredEntries && !banksBusy[bank(index)];
    }

    bool
    reserveAccess(size_t index)
    {
        if (!bankAvailable(index)) {
            return false;
        }
        banksBusy[bank(index)] = true;
        return true;
    }

    Entry *
    get(size_t index)
    {
        if (index >= configuredEntries || !entries[index].valid) {
            return nullptr;
        }
        return &entries[index];
    }

    const Entry *
    get(size_t index) const
    {
        if (index >= configuredEntries || !entries[index].valid) {
            return nullptr;
        }
        return &entries[index];
    }
};

static_assert(SpartaSummaryBits <= SharedPairBits);

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_SPARTA_PAIRED_SUMMARY_STORE_HH__
