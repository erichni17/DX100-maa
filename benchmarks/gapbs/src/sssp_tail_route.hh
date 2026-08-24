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

constexpr bool
IsExactLogicalWindow(std::size_t words)
{
    return words == kLogicalWords;
}

struct RouteCounters
{
    std::uint64_t logical_windows;
    std::uint64_t bounded_spd_batches;
    std::uint64_t bounded_spd_words;
    std::uint64_t exact_cpu_batches;
    std::uint64_t exact_cpu_words;
    std::int64_t max_host_spd_element;

    RouteCounters()
        : logical_windows(0), bounded_spd_batches(0), bounded_spd_words(0),
          exact_cpu_batches(0), exact_cpu_words(0),
          max_host_spd_element(-1)
    {
    }

    void recordLogicalWindow()
    {
        ++logical_windows;
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
        const std::int64_t last = static_cast<std::int64_t>(words - 1);
        if (last > max_host_spd_element)
            max_host_spd_element = last;
    }

    void recordExactCpu(std::size_t words)
    {
        if (words == 0 || words > kLogicalWords)
            return;
        ++exact_cpu_batches;
        exact_cpu_words += words;
    }

    bool legal() const
    {
        return max_host_spd_element <
            static_cast<std::int64_t>(kPhysicalWords);
    }
};

} // namespace sssp_tail_route

#endif // GAPBS_SSSP_TAIL_ROUTE_HH
