#ifndef __MEM_MAA_BOUNDED_RANGE_PASS_HH__
#define __MEM_MAA_BOUNDED_RANGE_PASS_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace gem5
{

/**
 * Fail-closed accounting for the bounded direct-index range-pass candidate.
 *
 * This object deliberately stores no index, source address, row, cache-line,
 * or result payload.  The two bitmaps are an explicit four-KiB checker for a
 * 16K logical gather: one admission bit and one retirement bit per logical
 * iteration.  Row/Offset payload remains in the native finite tables.
 */
class BoundedRangePassTracker
{
  public:
    static constexpr uint32_t MaxActiveEntries = 4096;
    static constexpr uint32_t MaxPasses = 64;

    enum class Result : uint8_t
    {
        Accepted,
        NotConfigured,
        InvalidConfiguration,
        IterationOutOfRange,
        GrowOutOfRange,
        PassOutOfRange,
        WrongPass,
        DuplicateAdmission,
        RetirementBeforeAdmission,
        DuplicateRetirement,
        PassAlreadyFinished,
        PassIncomplete,
        Incomplete
    };

    struct Range
    {
        uint64_t lower = 0;
        uint64_t upper = 0;
    };

    Result configure(uint32_t logical_entries, uint32_t active_entries,
                     uint32_t passes, uint64_t possible_grows)
    {
        return configureRange(logical_entries, active_entries, passes, 0,
                              possible_grows);
    }

    Result configureRange(uint32_t logical_entries, uint32_t active_entries,
                          uint32_t passes, uint64_t grow_lower,
                          uint64_t grow_upper)
    {
        if (passes == 0 || passes > MaxPasses || grow_lower >= grow_upper)
            return Result::InvalidConfiguration;
        std::vector<Range> ranges;
        ranges.reserve(passes);
        const uint64_t span = grow_upper - grow_lower;
        for (uint32_t pass = 0; pass < passes; ++pass) {
            ranges.push_back({
                grow_lower +
                    ceilDiv(static_cast<uint64_t>(pass) * span,
                            static_cast<uint64_t>(passes)),
                grow_lower +
                    ceilDiv(static_cast<uint64_t>(pass + 1) * span,
                            static_cast<uint64_t>(passes))});
        }
        return configureRanges(logical_entries, active_entries, ranges);
    }

    Result configureRanges(uint32_t logical_entries,
                           uint32_t active_entries,
                           const std::vector<Range> &ranges)
    {
        reset();
        const uint32_t passes = ranges.size();
        if (logical_entries == 0 || active_entries == 0 ||
            active_entries > MaxActiveEntries || passes == 0 ||
            passes > MaxPasses ||
            passes < ceilDiv(logical_entries, active_entries)) {
            return Result::InvalidConfiguration;
        }
        for (uint32_t pass = 0; pass < passes; ++pass) {
            if (ranges[pass].lower >= ranges[pass].upper ||
                (pass != 0 &&
                 ranges[pass - 1].upper != ranges[pass].lower)) {
                return Result::InvalidConfiguration;
            }
        }
        logicalEntries = logical_entries;
        activeEntries = active_entries;
        numPasses = passes;
        growLower = ranges.front().lower;
        growUpper = ranges.back().upper;
        for (uint32_t pass = 0; pass < passes; ++pass)
            passRanges[pass] = ranges[pass];
        const size_t words = ceilDiv(logicalEntries, uint32_t(64));
        admitted.assign(words, 0);
        retired.assign(words, 0);
        configuredFlag = true;
        return Result::Accepted;
    }

    void reset()
    {
        configuredFlag = false;
        logicalEntries = 0;
        activeEntries = 0;
        numPasses = 0;
        growLower = 0;
        growUpper = 0;
        admitted.clear();
        retired.clear();
        admissionCount = 0;
        retirementCount = 0;
        passAdmissions.fill(0);
        passRetirements.fill(0);
        passFinished.fill(false);
        passRanges.fill({});
    }

    bool configured() const { return configuredFlag; }
    uint32_t logical() const { return logicalEntries; }
    uint32_t active() const { return activeEntries; }
    uint32_t passes() const { return numPasses; }
    uint64_t grows() const { return growUpper; }
    uint64_t lowerGrow() const { return growLower; }
    uint64_t upperGrow() const { return growUpper; }
    uint32_t admissions() const { return admissionCount; }
    uint32_t retirements() const { return retirementCount; }

    Range range(uint32_t pass) const
    {
        if (!configuredFlag || pass >= numPasses)
            return {};
        return passRanges[pass];
    }

    uint32_t passForGrow(uint64_t grow) const
    {
        if (!configuredFlag || grow < growLower || grow >= growUpper)
            return MaxPasses;
        for (uint32_t pass = 0; pass < numPasses; ++pass) {
            if (grow < passRanges[pass].upper)
                return pass;
        }
        return MaxPasses;
    }

    Result recordAdmission(uint32_t iteration, uint64_t grow, uint32_t pass)
    {
        if (!configuredFlag)
            return Result::NotConfigured;
        if (iteration >= logicalEntries)
            return Result::IterationOutOfRange;
        if (grow < growLower || grow >= growUpper)
            return Result::GrowOutOfRange;
        if (pass >= numPasses)
            return Result::PassOutOfRange;
        if (passFinished[pass])
            return Result::PassAlreadyFinished;
        if (passForGrow(grow) != pass)
            return Result::WrongPass;
        if (test(admitted, iteration))
            return Result::DuplicateAdmission;
        set(admitted, iteration);
        admissionCount++;
        passAdmissions[pass]++;
        return Result::Accepted;
    }

    Result recordRetirement(uint32_t iteration, uint32_t pass)
    {
        if (!configuredFlag)
            return Result::NotConfigured;
        if (iteration >= logicalEntries)
            return Result::IterationOutOfRange;
        if (pass >= numPasses)
            return Result::PassOutOfRange;
        if (passFinished[pass])
            return Result::PassAlreadyFinished;
        if (!test(admitted, iteration))
            return Result::RetirementBeforeAdmission;
        if (test(retired, iteration))
            return Result::DuplicateRetirement;
        set(retired, iteration);
        retirementCount++;
        passRetirements[pass]++;
        return Result::Accepted;
    }

    Result finishPass(uint32_t pass)
    {
        if (!configuredFlag)
            return Result::NotConfigured;
        if (pass >= numPasses)
            return Result::PassOutOfRange;
        if (passFinished[pass])
            return Result::PassAlreadyFinished;
        if (passAdmissions[pass] != passRetirements[pass])
            return Result::PassIncomplete;
        passFinished[pass] = true;
        return Result::Accepted;
    }

    Result finish() const
    {
        if (!configuredFlag)
            return Result::NotConfigured;
        if (admissionCount != logicalEntries ||
            retirementCount != logicalEntries)
            return Result::Incomplete;
        for (uint32_t pass = 0; pass < numPasses; ++pass) {
            if (!passFinished[pass] ||
                passAdmissions[pass] != passRetirements[pass])
                return Result::Incomplete;
        }
        return admitted == retired ? Result::Accepted : Result::Incomplete;
    }

    uint32_t admissionsForPass(uint32_t pass) const
    {
        return pass < numPasses ? passAdmissions[pass] : 0;
    }

    uint32_t retirementsForPass(uint32_t pass) const
    {
        return pass < numPasses ? passRetirements[pass] : 0;
    }

    struct SemanticByteBreakdown
    {
        size_t bitmaps = 0;
        size_t passCounters = 0;
        size_t passFinished = 0;
        size_t passRanges = 0;
        size_t scalarConfig = 0;

        size_t total() const
        {
            return bitmaps + passCounters + passFinished + passRanges +
                   scalarConfig;
        }
    };

    /**
     * Field-complete semantic storage accounting.
     *
     * Each boolean is charged as one semantic byte. This deliberately excludes
     * host padding, vector allocator/capacity overhead, and synthesized area.
     */
    SemanticByteBreakdown semanticByteBreakdown() const
    {
        SemanticByteBreakdown bytes;
        bytes.bitmaps =
            (admitted.size() + retired.size()) * sizeof(uint64_t);
        bytes.passCounters =
            (passAdmissions.size() + passRetirements.size()) *
            sizeof(uint32_t);
        bytes.passFinished = passFinished.size();
        bytes.passRanges = passRanges.size() * 2 * sizeof(uint64_t);
        bytes.scalarConfig =
            1 + // configuredFlag
            3 * sizeof(uint32_t) + // logicalEntries, activeEntries, numPasses
            2 * sizeof(uint64_t) + // growLower, growUpper
            2 * sizeof(uint32_t); // admissionCount, retirementCount
        return bytes;
    }

    size_t chargedBytes() const
    {
        return semanticByteBreakdown().total();
    }

    static const char *resultName(Result result)
    {
        switch (result) {
          case Result::Accepted: return "accepted";
          case Result::NotConfigured: return "not_configured";
          case Result::InvalidConfiguration: return "invalid_configuration";
          case Result::IterationOutOfRange: return "iteration_out_of_range";
          case Result::GrowOutOfRange: return "grow_out_of_range";
          case Result::PassOutOfRange: return "pass_out_of_range";
          case Result::WrongPass: return "wrong_pass";
          case Result::DuplicateAdmission: return "duplicate_admission";
          case Result::RetirementBeforeAdmission:
            return "retirement_before_admission";
          case Result::DuplicateRetirement: return "duplicate_retirement";
          case Result::PassAlreadyFinished: return "pass_already_finished";
          case Result::PassIncomplete: return "pass_incomplete";
          case Result::Incomplete: return "incomplete";
        }
        return "unknown";
    }

  private:
    template <class T>
    static constexpr T ceilDiv(T value, T divisor)
    {
        return value / divisor + (value % divisor != 0);
    }

    static bool test(const std::vector<uint64_t> &bits, uint32_t index)
    {
        return bits[index / 64] & (uint64_t(1) << (index % 64));
    }

    static void set(std::vector<uint64_t> &bits, uint32_t index)
    {
        bits[index / 64] |= uint64_t(1) << (index % 64);
    }

    bool configuredFlag = false;
    uint32_t logicalEntries = 0;
    uint32_t activeEntries = 0;
    uint32_t numPasses = 0;
    uint64_t growLower = 0;
    uint64_t growUpper = 0;
    std::vector<uint64_t> admitted;
    std::vector<uint64_t> retired;
    uint32_t admissionCount = 0;
    uint32_t retirementCount = 0;
    std::array<uint32_t, MaxPasses> passAdmissions{};
    std::array<uint32_t, MaxPasses> passRetirements{};
    std::array<bool, MaxPasses> passFinished{};
    std::array<Range, MaxPasses> passRanges{};
};

} // namespace gem5

#endif // __MEM_MAA_BOUNDED_RANGE_PASS_HH__
