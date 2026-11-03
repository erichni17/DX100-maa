#include "strict_compare.hpp"
#include <iostream>
#include <limits>
int main() {
    float nan = std::numeric_limits<float>::quiet_NaN(), inf = std::numeric_limits<float>::infinity();
    struct Test { const char* name; std::vector<float> expected, actual; bool pass; };
    std::vector<Test> cases = {
        {"identical", {1, 0, -2}, {1, 0, -2}, true},
        {"within_tolerance", {0}, {0.0005f}, true},
        {"finite_mismatch", {1}, {1.01f}, false},
        {"nan_actual", {1}, {nan}, false}, {"nan_expected", {nan}, {1}, false},
        {"infinity_actual", {1}, {inf}, false}, {"infinity_expected", {-inf}, {1}, false},
        {"shape_mismatch", {1, 2}, {1}, false}};
    bool passed = true;
    std::cout << "{\"tests\":[";
    for (std::size_t i = 0; i < cases.size(); ++i) {
        const auto& c = cases[i]; bool actual = ume_validation::compare(c.expected, c.actual).passed();
        passed = passed && actual == c.pass;
        if (i) std::cout << ',';
        std::cout << "{\"name\":\"" << c.name << "\",\"expected_pass\":" << (c.pass ? "true" : "false")
                  << ",\"actual_pass\":" << (actual ? "true" : "false") << '}';
    }
    std::cout << "],\"passed\":" << (passed ? "true" : "false") << "}\n";
    return passed ? 0 : 1;
}
