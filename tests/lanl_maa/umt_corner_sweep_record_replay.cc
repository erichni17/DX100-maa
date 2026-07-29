#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>

#include "umt_corner_sweep_record.hh"

using namespace gem5::lanlmaa;

namespace
{

bool
sameBits(double first, double second)
{
    return std::memcmp(&first, &second, sizeof(first)) == 0;
}

} // anonymous namespace

int
main(int argc, char **argv)
{
    if (argc != 2) {
        std::cerr << "usage: umt_corner_sweep_record_replay RECORD\n";
        return 2;
    }
    std::ifstream input(argv[1]);
    if (!input) {
        std::cerr << "unable to open UMT sweep record\n";
        return 2;
    }
    auto parsed = parseUmtCornerSweepRecord(input);
    if (!parsed) {
        std::cerr << "invalid UMT sweep record: " << parsed.error << '\n';
        return 2;
    }
    const auto result = UmtCornerSweepModel::execute(
        parsed.record.descriptor, parsed.record.input);
    if (!result) {
        std::cerr << "UMT sweep model rejected native record: "
                  << static_cast<uint32_t>(result.error) << '\n';
        return 1;
    }

    constexpr double AbsoluteTolerance = 1.0e-12;
    constexpr double RelativeTolerance = 1.0e-12;
    const uint32_t selected = parsed.record.input.cornerOrder.front();
    uint32_t exact = 0;
    double maximumAbsoluteError = 0.0;
    double maximumRelativeError = 0.0;
    for (uint32_t group = 0;
         group < parsed.record.descriptor.groupCount; ++group) {
        const size_t index =
            static_cast<size_t>(selected) *
            parsed.record.descriptor.totalGroups + group;
        const double candidate = result.psi1[index];
        const double native = parsed.record.nativeExpected[group];
        if (!std::isfinite(candidate) || !std::isfinite(native)) {
            std::cerr << "nonfinite UMT replay result\n";
            return 1;
        }
        if (sameBits(candidate, native)) {
            ++exact;
        }
        const double absoluteError = std::abs(candidate - native);
        const double scale = std::max(std::abs(candidate), std::abs(native));
        const double relativeError = scale == 0.0 ? 0.0 :
            absoluteError / scale;
        maximumAbsoluteError = std::max(maximumAbsoluteError, absoluteError);
        maximumRelativeError = std::max(maximumRelativeError, relativeError);
        if (absoluteError > AbsoluteTolerance + RelativeTolerance * scale) {
            std::cerr << std::setprecision(17)
                      << "UMT replay mismatch at group " << group
                      << ": model=" << candidate << " native=" << native
                      << " absolute_error=" << absoluteError << '\n';
            return 1;
        }
    }

    std::cout << std::setprecision(17)
              << "{\"status\":\"PASS\",\"groups\":"
              << parsed.record.descriptor.groupCount
              << ",\"exact_bit_groups\":" << exact
              << ",\"maximum_absolute_error\":"
              << maximumAbsoluteError
              << ",\"maximum_relative_error\":"
              << maximumRelativeError
              << ",\"flux_reads\":" << result.counters.fluxReads
              << ",\"special_updates\":"
              << result.counters.specialOppositeFaceUpdates
              << ",\"fallback_updates\":"
              << result.counters.fallbackFaceUpdates << "}\n";
    return 0;
}
