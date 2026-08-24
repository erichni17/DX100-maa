#ifndef GAPBS_SSSP_TAIL_ROUTE_HH
#define GAPBS_SSSP_TAIL_ROUTE_HH

#include <cstddef>
#include <cstdint>

namespace sssp_tail_route {

constexpr std::size_t kPhysicalWords = 4096;
constexpr std::size_t kLogicalWords = 16384;

enum class BatchRoute
{
    kTerminal,
    kBoundedSpd,
    kExactCpu,
};

// A host-visible SPD tile is legal only through its physical capacity.  Larger
// logical Row/Offset batches remain valid accelerator values, but must not be
// dereferenced through the CPU SPD aperture.
constexpr BatchRoute
SelectBatchRoute(std::size_t words)
{
    return words == 0 ? BatchRoute::kTerminal
                      : words <= kPhysicalWords ? BatchRoute::kBoundedSpd
                                                : BatchRoute::kExactCpu;
}

// A logical hybrid window is assembled only from four separately admitted
// physical pages.  SelectBatchRoute(kLogicalWords) intentionally remains CPU.
constexpr std::size_t kPagesPerLogicalWindow =
    kLogicalWords / kPhysicalWords;

struct RouteCounters
{
    std::uint64_t produced_words;
    std::uint64_t consumed_words;
    std::uint64_t accelerated_words;
    std::uint64_t cpu_words;
    std::uint64_t scalar_cpu_words;
    std::uint64_t logical_windows;
    std::uint64_t bounded_spd_batches;
    std::uint64_t bounded_spd_words;
    std::uint64_t exact_cpu_batches;
    std::uint64_t exact_cpu_words;
    std::uint64_t exact_cpu_4133_batches;
    std::uint64_t illegal_host_spd_attempts;
    std::int64_t max_host_spd_element;

    RouteCounters()
        : produced_words(0), consumed_words(0), accelerated_words(0),
          cpu_words(0), scalar_cpu_words(0), logical_windows(0),
          bounded_spd_batches(0), bounded_spd_words(0),
          exact_cpu_batches(0), exact_cpu_words(0),
          exact_cpu_4133_batches(0), illegal_host_spd_attempts(0),
          max_host_spd_element(-1)
    {
    }

    void recordLogicalWindow()
    {
        ++logical_windows;
        accelerated_words += kLogicalWords;
        consumed_words += kLogicalWords;
    }

    void recordProduced(std::size_t words)
    {
        produced_words += words;
    }

    void recordScalarCpu(std::size_t words)
    {
        produced_words += words;
        consumed_words += words;
        cpu_words += words;
        scalar_cpu_words += words;
    }

    void recordBatch(std::size_t words)
    {
        const BatchRoute route = SelectBatchRoute(words);
        if (route == BatchRoute::kBoundedSpd)
            recordBoundedSpd(words);
        else if (route == BatchRoute::kExactCpu)
            recordExactCpu(words);
    }

    void recordBoundedSpd(std::size_t words)
    {
        if (words == 0 || words > kPhysicalWords)
            return;
        ++bounded_spd_batches;
        bounded_spd_words += words;
        consumed_words += words;
    }

    void recordExactCpu(std::size_t words)
    {
        if (words == 0 || words > kLogicalWords)
            return;
        ++exact_cpu_batches;
        exact_cpu_words += words;
        cpu_words += words;
        consumed_words += words;
        if (words == 4133)
            ++exact_cpu_4133_batches;
    }

    bool guardHostSpdAccess(std::size_t words)
    {
        if (words > kPhysicalWords) {
            ++illegal_host_spd_attempts;
            return false;
        }
        if (words > 0) {
            const std::int64_t last = static_cast<std::int64_t>(words - 1);
            if (last > max_host_spd_element)
                max_host_spd_element = last;
        }
        return true;
    }

    bool legal() const
    {
        return illegal_host_spd_attempts == 0 &&
            max_host_spd_element < static_cast<std::int64_t>(kPhysicalWords);
    }

    bool coverageCloses() const
    {
        return produced_words == consumed_words &&
            consumed_words == accelerated_words + bounded_spd_words +
                cpu_words &&
            cpu_words == scalar_cpu_words + exact_cpu_words;
    }
};

} // namespace sssp_tail_route

#endif // GAPBS_SSSP_TAIL_ROUTE_HH
