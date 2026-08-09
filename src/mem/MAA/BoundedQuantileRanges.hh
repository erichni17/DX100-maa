#ifndef __MEM_MAA_BOUNDED_QUANTILE_RANGES_HH__
#define __MEM_MAA_BOUNDED_QUANTILE_RANGES_HH__

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>

namespace gem5
{

/**
 * Storage-free quantile selection over a caller-owned finite histogram.
 *
 * The caller supplies a visitor over (source-cache-line-id, population)
 * records. Hardware can implement each visit as a scan of the phase-shared
 * 4K Word/Offset array. The planner retains only boundaries and populations.
 */
class BoundedQuantileRanges
{
  public:
    static constexpr uint32_t MaxPasses = 64;

    enum class Result : uint8_t
    {
        Accepted,
        InvalidConfiguration,
        EmptyHistogram,
        PopulationMismatch,
        BoundaryOverflow,
        BucketOverflow
    };

    /** Greedily pack whole ordered keys into variable bounded passes. */
    template <class Visit>
    Result configurePacked(uint32_t logical, uint32_t active,
                           uint32_t max_passes, Visit visit)
    {
        reset();
        if (logical == 0 || active == 0 || max_passes == 0 ||
            max_passes > MaxPasses)
            return Result::InvalidConfiguration;
        uint64_t total = 0;
        uint32_t min_key = std::numeric_limits<uint32_t>::max();
        uint32_t max_key = 0;
        visit([&](uint32_t key, uint32_t count) {
            if (count == 0)
                return;
            total += count;
            min_key = key < min_key ? key : min_key;
            max_key = key > max_key ? key : max_key;
            histogramRecords++;
        });
        if (histogramRecords == 0)
            return Result::EmptyHistogram;
        if (total != logical)
            return Result::PopulationMismatch;
        if (max_key == std::numeric_limits<uint32_t>::max())
            return Result::BoundaryOverflow;

        uint64_t cursor = min_key;
        uint64_t range_lower = min_key;
        uint32_t range_population = 0;
        while (cursor <= max_key) {
            uint32_t next_key = std::numeric_limits<uint32_t>::max();
            uint32_t next_count = 0;
            visit([&](uint32_t key, uint32_t count) {
                if (count != 0 && key >= cursor && key < next_key) {
                    next_key = key;
                    next_count = count;
                }
            });
            selectionScans++;
            if (next_key == std::numeric_limits<uint32_t>::max())
                break;
            if (next_count > active)
                return Result::BucketOverflow;
            if (range_population != 0 &&
                range_population + next_count > active) {
                if (numPasses >= max_passes)
                    return Result::BucketOverflow;
                ranges[numPasses] = {range_lower, next_key};
                populations[numPasses] = range_population;
                numPasses++;
                range_lower = next_key;
                range_population = 0;
            }
            range_population += next_count;
            cursor = static_cast<uint64_t>(next_key) + 1;
        }
        if (range_population == 0 || numPasses >= max_passes)
            return Result::BucketOverflow;
        ranges[numPasses] = {
            range_lower, static_cast<uint64_t>(max_key) + 1};
        populations[numPasses] = range_population;
        numPasses++;
        logicalEntries = logical;
        activeEntries = active;
        configuredFlag = true;
        return Result::Accepted;
    }

    struct Range
    {
        uint64_t lower = 0;
        uint64_t upper = 0;
    };

    template <class Visit>
    Result configure(uint32_t logical, uint32_t active, uint32_t passes,
                     Visit visit)
    {
        reset();
        if (logical == 0 || active == 0 || passes == 0 ||
            passes > MaxPasses ||
            static_cast<uint64_t>(active) * passes < logical)
            return Result::InvalidConfiguration;

        uint64_t total = 0;
        uint32_t min_key = std::numeric_limits<uint32_t>::max();
        uint32_t max_key = 0;
        uint32_t records = 0;
        visit([&](uint32_t key, uint32_t count) {
            if (count == 0)
                return;
            total += count;
            min_key = key < min_key ? key : min_key;
            max_key = key > max_key ? key : max_key;
            records++;
        });
        if (records == 0)
            return Result::EmptyHistogram;
        if (total != logical)
            return Result::PopulationMismatch;
        if (max_key == std::numeric_limits<uint32_t>::max())
            return Result::BoundaryOverflow;

        boundaries[0] = min_key;
        boundaries[passes] = static_cast<uint64_t>(max_key) + 1;
        for (uint32_t pass = 1; pass < passes; ++pass) {
            const uint64_t rank =
                (static_cast<uint64_t>(logical) * pass) / passes;
            if (rank == 0 || rank >= logical)
                return Result::InvalidConfiguration;
            const uint32_t key = selectByRank(rank, visit);
            boundaries[pass] = static_cast<uint64_t>(key) + 1;
        }
        for (uint32_t pass = 0; pass < passes; ++pass) {
            if (boundaries[pass] >= boundaries[pass + 1])
                return Result::BucketOverflow;
            ranges[pass] = {boundaries[pass], boundaries[pass + 1]};
        }
        visit([&](uint32_t key, uint32_t count) {
            if (count == 0)
                return;
            for (uint32_t pass = 0; pass < passes; ++pass) {
                if (key < ranges[pass].upper) {
                    populations[pass] += count;
                    return;
                }
            }
        });
        for (uint32_t pass = 0; pass < passes; ++pass) {
            if (populations[pass] > active)
                return Result::BucketOverflow;
        }
        logicalEntries = logical;
        activeEntries = active;
        numPasses = passes;
        histogramRecords = records;
        configuredFlag = true;
        // One full scan per bit per selected boundary. The caller separately
        // charges its finite physical-table width for every visit.
        selectionScans = (passes - 1) * 32;
        return Result::Accepted;
    }

    void reset()
    {
        configuredFlag = false;
        logicalEntries = 0;
        activeEntries = 0;
        numPasses = 0;
        histogramRecords = 0;
        selectionScans = 0;
        boundaries.fill(0);
        populations.fill(0);
        ranges.fill({});
    }

    bool configured() const { return configuredFlag; }
    uint32_t passes() const { return numPasses; }
    uint32_t records() const { return histogramRecords; }
    uint32_t scans() const { return selectionScans; }
    Range range(uint32_t pass) const
    {
        return pass < numPasses ? ranges[pass] : Range{};
    }
    uint32_t population(uint32_t pass) const
    {
        return pass < numPasses ? populations[pass] : 0;
    }

    static const char *resultName(Result result)
    {
        switch (result) {
          case Result::Accepted: return "accepted";
          case Result::InvalidConfiguration: return "invalid_configuration";
          case Result::EmptyHistogram: return "empty_histogram";
          case Result::PopulationMismatch: return "population_mismatch";
          case Result::BoundaryOverflow: return "boundary_overflow";
          case Result::BucketOverflow: return "bucket_overflow";
        }
        return "unknown";
    }

  private:
    template <class Visit>
    static uint32_t selectByRank(uint64_t rank, Visit visit)
    {
        uint32_t prefix = 0;
        uint32_t mask = 0;
        for (int bit = 31; bit >= 0; --bit) {
            const uint32_t bit_mask = uint32_t(1) << bit;
            uint64_t zero_population = 0;
            visit([&](uint32_t key, uint32_t count) {
                if ((key & mask) == prefix && !(key & bit_mask))
                    zero_population += count;
            });
            mask |= bit_mask;
            if (rank > zero_population) {
                prefix |= bit_mask;
                rank -= zero_population;
            }
        }
        return prefix;
    }

    bool configuredFlag = false;
    uint32_t logicalEntries = 0;
    uint32_t activeEntries = 0;
    uint32_t numPasses = 0;
    uint32_t histogramRecords = 0;
    uint32_t selectionScans = 0;
    std::array<uint64_t, MaxPasses + 1> boundaries{};
    std::array<uint32_t, MaxPasses> populations{};
    std::array<Range, MaxPasses> ranges{};
};

/**
 * Bounded translated-grow plan with deterministic per-key/pass quotas.
 *
 * Unlike BoundedQuantileRanges, this planner may assign disjoint grow keys to
 * the same pass. It orders grows by descending population, then fills passes
 * with contiguous ordinal quotas. Any number of grows may span pass
 * boundaries. The fixed 64x64 uint16 quota table and replay ordinals are
 * charged separately from the phase-shared Word/Offset histogram.
 */
class BoundedGrowPassPlan
{
  public:
    static constexpr uint32_t MaxPasses = 64;
    static constexpr uint32_t MaxRecords = 64;
    enum class Result : uint8_t
    {
        Accepted,
        InvalidConfiguration,
        EmptyHistogram,
        PopulationMismatch,
        TooManyRecords,
        RequiresIterationFallback,
        StaleReplayOrdinal,
        ReplayOrdinalOverflow,
        ReplayNotActive,
        UnknownReplayKey,
        IncompleteReplay
    };

    template <class Visit>
    Result configure(uint32_t logical, uint32_t active,
                     uint32_t max_passes, Visit visit)
    {
        reset();
        if (logical == 0 || active == 0 || max_passes == 0 ||
            max_passes > MaxPasses)
            return Result::InvalidConfiguration;
        const uint32_t target_passes = ceilDiv(logical, active);
        if (target_passes == 0 || target_passes > max_passes)
            return Result::InvalidConfiguration;

        uint64_t total = 0;
        bool too_many = false;
        visit([&](uint32_t key, uint32_t count) {
            planningOperations++;
            if (count == 0 || too_many)
                return;
            if (numRecords == MaxRecords) {
                too_many = true;
                return;
            }
            keys[numRecords] = key;
            counts[numRecords] = count;
            total += count;
            numRecords++;
        });
        if (too_many)
            return Result::TooManyRecords;
        if (numRecords == 0)
            return Result::EmptyHistogram;
        if (total != logical)
            return Result::PopulationMismatch;

        for (uint32_t rank = 0; rank < numRecords; ++rank) {
            uint32_t selected = MaxRecords;
            for (uint32_t record = 0; record < numRecords; ++record) {
                planningOperations++;
                if (recordPlaced[record])
                    continue;
                if (selected == MaxRecords ||
                    counts[record] > counts[selected] ||
                    (counts[record] == counts[selected] &&
                     keys[record] < keys[selected])) {
                    selected = record;
                }
            }
            if (selected == MaxRecords)
                return Result::RequiresIterationFallback;
            recordPlaced[selected] = 1;
            recordOrder[rank] = static_cast<uint8_t>(selected);
        }
        recordPlaced.fill(0);

        // Preserve every grow that fits as a whole, using deterministic
        // first-fit decreasing placement.
        for (uint32_t rank = 0; rank < numRecords; ++rank) {
            const uint32_t selected = recordOrder[rank];
            if (counts[selected] > active)
                continue;
            for (uint32_t pass = 0; pass < target_passes; ++pass) {
                planningOperations++;
                if (active - passPopulations[pass] < counts[selected])
                    continue;
                passQuotas[selected][pass] =
                    static_cast<uint16_t>(counts[selected]);
                passPopulations[pass] += counts[selected];
                recordPlaced[selected] = 1;
                break;
            }
        }

        // Split only records that could not fit whole, including any number
        // of records larger than the active capacity.
        for (uint32_t rank = 0; rank < numRecords; ++rank) {
            const uint32_t selected = recordOrder[rank];
            if (recordPlaced[selected])
                continue;
            uint32_t remaining = counts[selected];
            uint32_t pass = 0;
            while (remaining != 0) {
                planningOperations++;
                if (pass >= target_passes)
                    return Result::RequiresIterationFallback;
                const uint32_t gap = active - passPopulations[pass];
                if (gap == 0) {
                    pass++;
                    continue;
                }
                const uint32_t quota = std::min(gap, remaining);
                if (quota > std::numeric_limits<uint16_t>::max())
                    return Result::RequiresIterationFallback;
                passQuotas[selected][pass] =
                    static_cast<uint16_t>(quota);
                passPopulations[pass] += quota;
                remaining -= quota;
            }
            recordPlaced[selected] = 1;
        }
        for (uint32_t pass = 0; pass < target_passes; ++pass) {
            planningOperations++;
            const uint32_t expected = pass + 1 == target_passes
                ? logical - active * (target_passes - 1) : active;
            if (passPopulations[pass] != expected)
                return Result::RequiresIterationFallback;
        }

        logicalEntries = logical;
        activeEntries = active;
        numPasses = target_passes;
        configuredFlag = true;
        return Result::Accepted;
    }

    void reset()
    {
        configuredFlag = false;
        logicalEntries = 0;
        activeEntries = 0;
        numPasses = 0;
        numRecords = 0;
        planningOperations = 0;
        replayActive = false;
        keys.fill(0);
        counts.fill(0);
        recordPlaced.fill(0);
        recordOrder.fill(0);
        passPopulations.fill(0);
        replayOrdinals.fill(0);
        for (auto &quotas : passQuotas)
            quotas.fill(0);
    }

    bool configured() const { return configuredFlag; }
    uint32_t passes() const { return numPasses; }
    uint32_t records() const { return numRecords; }
    uint64_t operations() const { return planningOperations; }
    uint32_t population(uint32_t pass) const
    {
        return pass < numPasses ? passPopulations[pass] : 0;
    }
    uint32_t quota(uint32_t key, uint32_t pass) const
    {
        const uint32_t record = findRecord(key);
        return record < numRecords && pass < numPasses
            ? passQuotas[record][pass] : 0;
    }
    uint32_t splitRecords() const
    {
        uint32_t split = 0;
        for (uint32_t record = 0; record < numRecords; ++record) {
            uint32_t used = 0;
            for (uint32_t pass = 0; pass < numPasses; ++pass)
                used += passQuotas[record][pass] != 0;
            split += used > 1;
        }
        return split;
    }
    uint32_t passFor(uint32_t key, uint32_t split_ordinal) const
    {
        if (!configuredFlag)
            return MaxPasses;
        for (uint32_t record = 0; record < numRecords; ++record) {
            if (keys[record] != key)
                continue;
            uint32_t cursor = 0;
            for (uint32_t pass = 0; pass < numPasses; ++pass) {
                cursor += passQuotas[record][pass];
                if (split_ordinal < cursor)
                    return pass;
            }
            return MaxPasses;
        }
        return MaxPasses;
    }

    Result beginReplay()
    {
        if (!configuredFlag)
            return Result::InvalidConfiguration;
        replayOrdinals.fill(0);
        replayActive = true;
        return Result::Accepted;
    }

    Result peekReplayOrdinal(uint32_t key, uint32_t &ordinal) const
    {
        if (!replayActive)
            return Result::ReplayNotActive;
        const uint32_t record = findRecord(key);
        if (record == MaxRecords)
            return Result::UnknownReplayKey;
        ordinal = replayOrdinals[record];
        return Result::Accepted;
    }

    Result commitReplayOrdinal(uint32_t key, uint32_t observed)
    {
        if (!replayActive)
            return Result::ReplayNotActive;
        const uint32_t record = findRecord(key);
        if (record == MaxRecords)
            return Result::UnknownReplayKey;
        if (observed != replayOrdinals[record])
            return Result::StaleReplayOrdinal;
        if (replayOrdinals[record] >= counts[record])
            return Result::ReplayOrdinalOverflow;
        replayOrdinals[record]++;
        return Result::Accepted;
    }

    Result finishReplay()
    {
        if (!replayActive)
            return Result::ReplayNotActive;
        for (uint32_t record = 0; record < numRecords; ++record) {
            if (replayOrdinals[record] != counts[record])
                return Result::IncompleteReplay;
        }
        replayActive = false;
        return Result::Accepted;
    }

    static uint64_t modeledReductionVisits(uint32_t table_capacity,
                                           uint64_t planning_operations)
    {
        // The visitor makes one complete physical-table scan. Planner
        // operations are the finite work performed on records after that
        // scan; there is no second reduction scan.
        return table_capacity + planning_operations;
    }

    size_t chargedBytes() const
    {
        return keys.size() * sizeof(uint32_t) +
            counts.size() * sizeof(uint32_t) +
            recordPlaced.size() * sizeof(uint8_t) +
            recordOrder.size() * sizeof(uint8_t) +
            passPopulations.size() * sizeof(uint32_t) +
            passQuotas.size() * passQuotas.front().size() *
                sizeof(uint16_t) +
            replayOrdinals.size() * sizeof(uint32_t) +
            2 + 4 * sizeof(uint32_t) + sizeof(uint64_t);
    }

    static const char *resultName(Result result)
    {
        switch (result) {
          case Result::Accepted: return "accepted";
          case Result::InvalidConfiguration:
            return "invalid_configuration";
          case Result::EmptyHistogram: return "empty_histogram";
          case Result::PopulationMismatch: return "population_mismatch";
          case Result::TooManyRecords: return "too_many_records";
          case Result::RequiresIterationFallback:
            return "requires_iteration_fallback";
          case Result::StaleReplayOrdinal: return "stale_replay_ordinal";
          case Result::ReplayOrdinalOverflow:
            return "replay_ordinal_overflow";
          case Result::ReplayNotActive: return "replay_not_active";
          case Result::UnknownReplayKey: return "unknown_replay_key";
          case Result::IncompleteReplay: return "incomplete_replay";
        }
        return "unknown";
    }

  private:
    static constexpr uint32_t ceilDiv(uint32_t value, uint32_t divisor)
    {
        return value / divisor + (value % divisor != 0);
    }

    uint32_t findRecord(uint32_t key) const
    {
        for (uint32_t record = 0; record < numRecords; ++record) {
            if (keys[record] == key)
                return record;
        }
        return MaxRecords;
    }

    bool configuredFlag = false;
    uint32_t logicalEntries = 0;
    uint32_t activeEntries = 0;
    uint32_t numPasses = 0;
    uint32_t numRecords = 0;
    uint64_t planningOperations = 0;
    bool replayActive = false;
    std::array<uint32_t, MaxRecords> keys{};
    std::array<uint32_t, MaxRecords> counts{};
    std::array<uint8_t, MaxRecords> recordPlaced{};
    std::array<uint8_t, MaxRecords> recordOrder{};
    std::array<uint32_t, MaxPasses> passPopulations{};
    std::array<std::array<uint16_t, MaxPasses>, MaxRecords> passQuotas{};
    std::array<uint32_t, MaxRecords> replayOrdinals{};
};

} // namespace gem5

#endif // __MEM_MAA_BOUNDED_QUANTILE_RANGES_HH__
