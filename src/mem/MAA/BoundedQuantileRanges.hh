#ifndef __MEM_MAA_BOUNDED_QUANTILE_RANGES_HH__
#define __MEM_MAA_BOUNDED_QUANTILE_RANGES_HH__

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
 * Bounded translated-grow plan with at most one deterministically split key.
 *
 * Unlike BoundedQuantileRanges, this planner may assign disjoint grow keys to
 * the same pass. It first assigns whole grows in descending-population order,
 * then uses one grow's replay ordinal to fill the remaining pass capacity.
 * The retained plan has a fixed 64-record ceiling and is charged separately
 * from the phase-shared Word/Offset histogram used to discover it.
 */
class BoundedGrowPassPlan
{
  public:
    static constexpr uint32_t MaxPasses = 64;
    static constexpr uint32_t MaxRecords = 64;
    static constexpr uint8_t NoPass = MaxPasses;

    enum class Result : uint8_t
    {
        Accepted,
        InvalidConfiguration,
        EmptyHistogram,
        PopulationMismatch,
        TooManyRecords,
        RequiresIterationFallback,
        StaleReplayOrdinal,
        ReplayOrdinalOverflow
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

        uint32_t hot_records = 0;
        uint32_t smallest = 0;
        uint32_t hot = 0;
        for (uint32_t record = 0; record < numRecords; ++record) {
            planningOperations++;
            if (counts[record] < counts[smallest])
                smallest = record;
            if (counts[record] > active) {
                hot = record;
                hot_records++;
            }
        }
        if (hot_records > 1)
            return Result::RequiresIterationFallback;
        splitRecord = hot_records == 1 ? hot : smallest;
        splitPopulationValue = counts[splitRecord];

        uint32_t pass = 0;
        for (uint32_t placed = 0; placed + 1 < numRecords; ++placed) {
            uint32_t selected = MaxRecords;
            for (uint32_t record = 0; record < numRecords; ++record) {
                planningOperations++;
                if (record == splitRecord || keyPass[record] != NoPass)
                    continue;
                if (selected == MaxRecords ||
                    counts[record] > counts[selected] ||
                    (counts[record] == counts[selected] &&
                     keys[record] < keys[selected])) {
                    selected = record;
                }
            }
            if (selected == MaxRecords || counts[selected] > active)
                return Result::RequiresIterationFallback;
            if (passPopulations[pass] + counts[selected] > active) {
                pass++;
                if (pass >= target_passes)
                    return Result::RequiresIterationFallback;
            }
            keyPass[selected] = pass;
            passPopulations[pass] += counts[selected];
        }

        uint32_t split_remaining = splitPopulationValue;
        for (pass = 0; pass < target_passes; ++pass) {
            planningOperations++;
            const uint32_t gap = active - passPopulations[pass];
            splitQuotas[pass] = gap < split_remaining
                ? gap : split_remaining;
            split_remaining -= splitQuotas[pass];
            passPopulations[pass] += splitQuotas[pass];
        }
        if (split_remaining != 0)
            return Result::RequiresIterationFallback;

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
        splitRecord = MaxRecords;
        splitPopulationValue = 0;
        planningOperations = 0;
        keys.fill(0);
        counts.fill(0);
        keyPass.fill(NoPass);
        passPopulations.fill(0);
        splitQuotas.fill(0);
    }

    bool configured() const { return configuredFlag; }
    uint32_t passes() const { return numPasses; }
    uint32_t records() const { return numRecords; }
    uint64_t operations() const { return planningOperations; }
    uint32_t population(uint32_t pass) const
    {
        return pass < numPasses ? passPopulations[pass] : 0;
    }
    uint32_t splitQuota(uint32_t pass) const
    {
        return pass < numPasses ? splitQuotas[pass] : 0;
    }
    uint32_t splitPopulation() const { return splitPopulationValue; }
    uint32_t splitKey() const
    {
        return splitRecord < numRecords ? keys[splitRecord] : 0;
    }
    bool isSplitKey(uint32_t key) const
    {
        return configuredFlag && splitRecord < numRecords &&
            keys[splitRecord] == key;
    }
    uint32_t passFor(uint32_t key, uint32_t split_ordinal) const
    {
        if (!configuredFlag)
            return MaxPasses;
        for (uint32_t record = 0; record < numRecords; ++record) {
            if (keys[record] != key)
                continue;
            if (record != splitRecord)
                return keyPass[record];
            uint32_t cursor = 0;
            for (uint32_t pass = 0; pass < numPasses; ++pass) {
                cursor += splitQuotas[pass];
                if (split_ordinal < cursor)
                    return pass;
            }
            return MaxPasses;
        }
        return MaxPasses;
    }

    Result commitSplitOrdinal(uint32_t observed, uint32_t &committed) const
    {
        if (!configuredFlag)
            return Result::InvalidConfiguration;
        if (observed != committed)
            return Result::StaleReplayOrdinal;
        if (committed >= splitPopulationValue)
            return Result::ReplayOrdinalOverflow;
        committed++;
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
            keyPass.size() * sizeof(uint8_t) +
            passPopulations.size() * sizeof(uint32_t) +
            splitQuotas.size() * sizeof(uint32_t) +
            1 + 6 * sizeof(uint32_t) + sizeof(uint64_t);
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
        }
        return "unknown";
    }

  private:
    static constexpr uint32_t ceilDiv(uint32_t value, uint32_t divisor)
    {
        return value / divisor + (value % divisor != 0);
    }

    bool configuredFlag = false;
    uint32_t logicalEntries = 0;
    uint32_t activeEntries = 0;
    uint32_t numPasses = 0;
    uint32_t numRecords = 0;
    uint32_t splitRecord = MaxRecords;
    uint32_t splitPopulationValue = 0;
    uint64_t planningOperations = 0;
    std::array<uint32_t, MaxRecords> keys{};
    std::array<uint32_t, MaxRecords> counts{};
    std::array<uint8_t, MaxRecords> keyPass{};
    std::array<uint32_t, MaxPasses> passPopulations{};
    std::array<uint32_t, MaxPasses> splitQuotas{};
};

} // namespace gem5

#endif // __MEM_MAA_BOUNDED_QUANTILE_RANGES_HH__
