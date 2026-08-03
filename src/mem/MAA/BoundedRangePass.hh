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
        reset();
        if (logical_entries == 0 || active_entries == 0 ||
            active_entries > MaxActiveEntries || passes == 0 ||
            passes > MaxPasses || possible_grows == 0 ||
            passes < ceilDiv(logical_entries, active_entries)) {
            return Result::InvalidConfiguration;
        }
        logicalEntries = logical_entries;
        activeEntries = active_entries;
        numPasses = passes;
        possibleGrows = possible_grows;
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
        possibleGrows = 0;
        admitted.clear();
        retired.clear();
        admissionCount = 0;
        retirementCount = 0;
        passAdmissions.fill(0);
        passRetirements.fill(0);
        passFinished.fill(false);
    }

    bool configured() const { return configuredFlag; }
    uint32_t logical() const { return logicalEntries; }
    uint32_t active() const { return activeEntries; }
    uint32_t passes() const { return numPasses; }
    uint64_t grows() const { return possibleGrows; }
    uint32_t admissions() const { return admissionCount; }
    uint32_t retirements() const { return retirementCount; }

    Range range(uint32_t pass) const
    {
        if (!configuredFlag || pass >= numPasses)
            return {};
        return {
            ceilDiv(static_cast<uint64_t>(pass) * possibleGrows,
                    static_cast<uint64_t>(numPasses)),
            ceilDiv(static_cast<uint64_t>(pass + 1) * possibleGrows,
                    static_cast<uint64_t>(numPasses))};
    }

    uint32_t passForGrow(uint64_t grow) const
    {
        if (!configuredFlag || grow >= possibleGrows)
            return MaxPasses;
        return static_cast<uint32_t>(grow * numPasses / possibleGrows);
    }

    Result recordAdmission(uint32_t iteration, uint64_t grow, uint32_t pass)
    {
        if (!configuredFlag)
            return Result::NotConfigured;
        if (iteration >= logicalEntries)
            return Result::IterationOutOfRange;
        if (grow >= possibleGrows)
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

    /** Explicit payload accounting; excludes allocator/object overhead. */
    size_t chargedBytes() const
    {
        return (admitted.size() + retired.size()) * sizeof(uint64_t) +
               2 * MaxPasses * sizeof(uint32_t) +
               MaxPasses * sizeof(bool);
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
    uint64_t possibleGrows = 0;
    std::vector<uint64_t> admitted;
    std::vector<uint64_t> retired;
    uint32_t admissionCount = 0;
    uint32_t retirementCount = 0;
    std::array<uint32_t, MaxPasses> passAdmissions{};
    std::array<uint32_t, MaxPasses> passRetirements{};
    std::array<bool, MaxPasses> passFinished{};
};

} // namespace gem5

#endif // __MEM_MAA_BOUNDED_RANGE_PASS_HH__
