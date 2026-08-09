#ifndef __MEM_MAA_ONLINE_ROW_WINDOW_HH__
#define __MEM_MAA_ONLINE_ROW_WINDOW_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>

#include "base/types.hh"

namespace gem5
{

/**
 * Finite policy state for a single-scan logical gather.
 *
 * This ledger never stores an iteration descriptor or payload.  The existing
 * Word/Offset and Row tables remain the sole precise descriptor store.  One
 * entry summarizes a translated grow that has appeared during the operation;
 * exceeding the fixed history is a hard failure, not a host-side fallback.
 */
class OnlineRowWindow
{
  public:
    static constexpr uint32_t MaxTrackedGrows = 512;
    static constexpr uint32_t MaxDescriptors = 4096;
    static constexpr uint32_t MaxLineSlots = 4096;
    static constexpr uint32_t MaxRowDirectories = 512;

    enum class Result : uint8_t
    {
        Accepted,
        NotConfigured,
        InvalidConfiguration,
        HistoryOverflow,
        DescriptorOverflow,
        LineOverflow,
        RowOverflow,
        NonSequentialAdmission,
        InvalidRetirement,
        NoVictim,
        StaleVictim,
        Incomplete,
    };

    struct Selection
    {
        Result result = Result::NoVictim;
        Addr grow = 0;
        uint32_t descriptors = 0;
        uint32_t visits = 0;
    };

    Result configure(uint32_t logical, uint32_t descriptor_capacity,
                     uint32_t line_capacity, uint32_t row_capacity)
    {
        reset();
        if (logical == 0 || descriptor_capacity == 0 ||
            descriptor_capacity > MaxDescriptors || line_capacity == 0 ||
            line_capacity > MaxLineSlots || row_capacity == 0 ||
            row_capacity > MaxRowDirectories)
            return Result::InvalidConfiguration;
        logicalIterations = logical;
        descriptorCapacity = descriptor_capacity;
        lineCapacity = line_capacity;
        rowCapacity = row_capacity;
        configured = true;
        return Result::Accepted;
    }

    void reset()
    {
        entries.fill(Entry{});
        configured = false;
        logicalIterations = 0;
        descriptorCapacity = 0;
        lineCapacity = 0;
        rowCapacity = 0;
        admissions = 0;
        retirements = 0;
        liveDescriptors = 0;
        peakDescriptors = 0;
        peakLines = 0;
        peakRows = 0;
        victimEpisodes = 0;
        reopenedGrows = 0;
        selectionVisits = 0;
        admissionSum = 0;
        retirementSum = 0;
        admissionXor = 0;
        retirementXor = 0;
        nextBirth = 1;
    }

    Result recordAdmission(Addr grow, uint32_t iteration,
                           uint32_t live_lines, uint32_t live_rows)
    {
        if (!configured)
            return Result::NotConfigured;
        if (iteration != admissions || iteration >= logicalIterations)
            return Result::NonSequentialAdmission;
        if (liveDescriptors == descriptorCapacity)
            return Result::DescriptorOverflow;
        if (live_lines == 0 || live_lines > lineCapacity)
            return Result::LineOverflow;
        if (live_rows == 0 || live_rows > rowCapacity)
            return Result::RowOverflow;

        Entry *entry = find(grow);
        if (entry == nullptr) {
            entry = allocate(grow);
            if (entry == nullptr)
                return Result::HistoryOverflow;
        }
        if (!entry->active) {
            if (entry->episodes != 0)
                reopenedGrows++;
            entry->active = true;
            entry->birth = nextBirth++;
            entry->episodes++;
        }
        if (entry->descriptors == descriptorCapacity)
            return Result::DescriptorOverflow;
        entry->descriptors++;
        admissions++;
        liveDescriptors++;
        admissionSum += iteration;
        admissionXor ^= iteration;
        if (liveDescriptors > descriptorCapacity)
            return Result::DescriptorOverflow;
        peakDescriptors = liveDescriptors > peakDescriptors
            ? liveDescriptors : peakDescriptors;
        peakLines = live_lines > peakLines ? live_lines : peakLines;
        peakRows = live_rows > peakRows ? live_rows : peakRows;
        return Result::Accepted;
    }

    Selection selectOldest() const
    {
        Selection selected;
        uint64_t oldest = std::numeric_limits<uint64_t>::max();
        for (const auto &entry : entries) {
            selected.visits++;
            if (!entry.valid || !entry.active || entry.descriptors == 0)
                continue;
            if (entry.birth < oldest ||
                (entry.birth == oldest && entry.grow < selected.grow)) {
                selected.result = Result::Accepted;
                selected.grow = entry.grow;
                selected.descriptors = entry.descriptors;
                oldest = entry.birth;
            }
        }
        return selected;
    }

    Result recordVictim(const Selection &selection)
    {
        if (!configured)
            return Result::NotConfigured;
        if (selection.result != Result::Accepted)
            return Result::NoVictim;
        Entry *entry = find(selection.grow);
        if (entry == nullptr || !entry->active ||
            entry->descriptors != selection.descriptors)
            return Result::StaleVictim;
        entry->active = false;
        entry->descriptors = 0;
        victimEpisodes++;
        selectionVisits += selection.visits;
        return Result::Accepted;
    }

    Result recordRetirement(uint32_t iteration)
    {
        if (!configured)
            return Result::NotConfigured;
        if (iteration >= logicalIterations || retirements == admissions ||
            liveDescriptors == 0)
            return Result::InvalidRetirement;
        retirements++;
        liveDescriptors--;
        retirementSum += iteration;
        retirementXor ^= iteration;
        return Result::Accepted;
    }

    Result finish(uint32_t live_lines, uint32_t live_rows) const
    {
        if (!configured)
            return Result::NotConfigured;
        if (admissions != logicalIterations ||
            retirements != logicalIterations || liveDescriptors != 0 ||
            live_lines != 0 || live_rows != 0 ||
            admissionSum != retirementSum || admissionXor != retirementXor)
            return Result::Incomplete;
        return Result::Accepted;
    }

    uint32_t logical() const { return logicalIterations; }
    uint32_t totalAdmissions() const { return admissions; }
    uint32_t totalRetirements() const { return retirements; }
    uint32_t currentDescriptors() const { return liveDescriptors; }
    uint32_t maxDescriptors() const { return peakDescriptors; }
    uint32_t maxLines() const { return peakLines; }
    uint32_t maxRows() const { return peakRows; }
    uint32_t victims() const { return victimEpisodes; }
    uint32_t reopens() const { return reopenedGrows; }
    uint64_t visits() const { return selectionVisits; }

    static constexpr uint64_t chargedBytes()
    {
        // Per grow: 64-bit key, 64-bit birth, 13-bit descriptor count,
        // 16-bit episode count, and valid/active bits, rounded to 24 bytes.
        // The remaining 128 bytes cover counters, bounds, and victim state.
        return static_cast<uint64_t>(MaxTrackedGrows) * 24 + 128;
    }

    static const char *resultName(Result result)
    {
        switch (result) {
          case Result::Accepted: return "accepted";
          case Result::NotConfigured: return "not_configured";
          case Result::InvalidConfiguration: return "invalid_configuration";
          case Result::HistoryOverflow: return "history_overflow";
          case Result::DescriptorOverflow: return "descriptor_overflow";
          case Result::LineOverflow: return "line_overflow";
          case Result::RowOverflow: return "row_overflow";
          case Result::NonSequentialAdmission:
            return "non_sequential_admission";
          case Result::InvalidRetirement: return "invalid_retirement";
          case Result::NoVictim: return "no_victim";
          case Result::StaleVictim: return "stale_victim";
          case Result::Incomplete: return "incomplete";
        }
        return "unknown";
    }

  private:
    struct Entry
    {
        Addr grow = 0;
        uint64_t birth = 0;
        uint32_t descriptors = 0;
        uint16_t episodes = 0;
        bool valid = false;
        bool active = false;
    };

    Entry *find(Addr grow)
    {
        for (auto &entry : entries)
            if (entry.valid && entry.grow == grow)
                return &entry;
        return nullptr;
    }

    Entry *allocate(Addr grow)
    {
        for (auto &entry : entries) {
            if (entry.valid)
                continue;
            entry = Entry{};
            entry.valid = true;
            entry.grow = grow;
            return &entry;
        }
        return nullptr;
    }

    std::array<Entry, MaxTrackedGrows> entries{};
    bool configured = false;
    uint32_t logicalIterations = 0;
    uint32_t descriptorCapacity = 0;
    uint32_t lineCapacity = 0;
    uint32_t rowCapacity = 0;
    uint32_t admissions = 0;
    uint32_t retirements = 0;
    uint32_t liveDescriptors = 0;
    uint32_t peakDescriptors = 0;
    uint32_t peakLines = 0;
    uint32_t peakRows = 0;
    uint32_t victimEpisodes = 0;
    uint32_t reopenedGrows = 0;
    uint64_t selectionVisits = 0;
    uint64_t admissionSum = 0;
    uint64_t retirementSum = 0;
    uint32_t admissionXor = 0;
    uint32_t retirementXor = 0;
    uint64_t nextBirth = 1;
};

} // namespace gem5

#endif // __MEM_MAA_ONLINE_ROW_WINDOW_HH__
