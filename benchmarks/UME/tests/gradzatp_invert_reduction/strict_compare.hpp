#pragma once
#include <cmath>
#include <cstddef>
#include <ostream>
#include <vector>

namespace ume_validation {
constexpr double absolute_tolerance = 1e-3;

struct Comparison {
    bool same_size = true;
    std::size_t compared = 0, nonfinite = 0, mismatches = 0;
    std::size_t relative_undefined = 0;
    double max_absolute = 0, max_relative = 0;
    long worst_absolute_index = -1, worst_relative_index = -1;
    bool passed() const { return same_size && nonfinite == 0 && mismatches == 0; }
};

inline Comparison compare(const std::vector<float>& expected, const std::vector<float>& actual) {
    Comparison result;
    result.same_size = expected.size() == actual.size();
    if (!result.same_size) return result;
    for (std::size_t i = 0; i < expected.size(); ++i) {
        ++result.compared;
        if (!std::isfinite(expected[i]) || !std::isfinite(actual[i])) {
            ++result.nonfinite;
            continue;
        }
        double error = std::fabs(double(expected[i]) - double(actual[i]));
        if (error > absolute_tolerance) ++result.mismatches;
        if (error > result.max_absolute) {
            result.max_absolute = error;
            result.worst_absolute_index = i;
        }
        if (expected[i] != 0) {
            double relative = error / std::fabs(double(expected[i]));
            if (relative > result.max_relative) {
                result.max_relative = relative;
                result.worst_relative_index = i;
            }
        } else if (error != 0) {
            ++result.relative_undefined;
        }
    }
    return result;
}

inline void write_comparison(std::ostream& out, const Comparison& r) {
    out << "{\"passed\":" << (r.passed() ? "true" : "false")
        << ",\"same_size\":" << (r.same_size ? "true" : "false")
        << ",\"compared\":" << r.compared << ",\"nonfinite\":" << r.nonfinite
        << ",\"mismatches\":" << r.mismatches << ",\"max_absolute\":" << r.max_absolute
        << ",\"max_relative\":" << r.max_relative
        << ",\"worst_absolute_index\":" << r.worst_absolute_index
        << ",\"worst_relative_index\":" << r.worst_relative_index
        << ",\"relative_undefined\":" << r.relative_undefined << "}";
}
}
