#include <cstdint>
#include <iostream>
#include <string>
#include <vector>

#include "mem/LANLMAA/UmtFp64DependencyModel.hh"

using namespace gem5::lanlmaa;

namespace
{

struct ScreenCase
{
    std::string name;
    bool reuse = false;
    uint32_t contexts = 0;
    UmtFp64Resources resources;
};

UmtFp64Resources
separate(uint32_t adders, uint32_t multipliers, uint32_t dividers,
         uint32_t divideInterval)
{
    UmtFp64Resources value;
    value.addSubUnits = adders;
    value.multiplyUnits = multipliers;
    value.divideUnits = dividers;
    value.divideLatency = 64;
    value.divideInitiationInterval = divideInterval;
    return value;
}

UmtFp64Resources
unified(uint32_t lanes, uint32_t dividers, uint32_t divideInterval)
{
    UmtFp64Resources value;
    value.unifiedAddMultiplyUnits = lanes;
    value.divideUnits = dividers;
    value.divideLatency = 64;
    value.divideInitiationInterval = divideInterval;
    return value;
}

} // anonymous namespace

int
main()
{
    std::vector<ScreenCase> cases;
    for (const uint32_t contexts : {16U, 32U, 64U}) {
        cases.push_back({"source_sep_1a2m_iter_1d", false, contexts,
                         separate(1, 2, 1, 64)});
        cases.push_back({"source_sep_1a2m_pipelined_1d", false, contexts,
                         separate(1, 2, 1, 1)});
        cases.push_back({"reuse_sep_1a2m_iter_1d", true, contexts,
                         separate(1, 2, 1, 64)});
        cases.push_back({"reuse_sep_1a1m_iter_1d", true, contexts,
                         separate(1, 1, 1, 64)});
        cases.push_back({"reuse_sep_1a1m_iter_4d", true, contexts,
                         separate(1, 1, 4, 64)});
        cases.push_back({"reuse_sep_1a1m_iter_8d", true, contexts,
                         separate(1, 1, 8, 64)});
        cases.push_back({"reuse_sep_1a2m_iter_2d", true, contexts,
                         separate(1, 2, 2, 64)});
        cases.push_back({"reuse_sep_1a2m_iter_4d", true, contexts,
                         separate(1, 2, 4, 64)});
        cases.push_back({"reuse_sep_1a2m_iter_8d", true, contexts,
                         separate(1, 2, 8, 64)});
        cases.push_back({"reuse_sep_1a2m_interleaved_1d", true, contexts,
                         separate(1, 2, 1, 8)});
        cases.push_back({"reuse_sep_1a2m_pipelined_1d", true, contexts,
                         separate(1, 2, 1, 1)});
        cases.push_back({"reuse_unified_2lane_iter_1d", true, contexts,
                         unified(2, 1, 64)});
    }

    std::cout << "{\"status\":\"PASS\",\"add_sub_latency\":1,"
              << "\"multiply_latency\":1,\"divide_latency\":64,"
              << "\"cases\":[";
    bool first = true;
    for (const auto &screen : cases) {
        const auto dag = UmtFp64DependencyModel::buildThreeFaceSpecial(
            screen.reuse);
        const auto result = UmtFp64DependencyModel::schedule(
            dag, screen.contexts, screen.resources);
        if (!result) {
            std::cerr << "UMT FP64 dependency screen failed: "
                      << static_cast<uint32_t>(result.error) << '\n';
            return 1;
        }
        if (!first) {
            std::cout << ',';
        }
        first = false;
        std::cout << "{\"name\":\"" << screen.name
                  << "\",\"dag\":\""
                  << (screen.reuse ? "observed_safe_reuse" :
                                      "bounded_source_order")
                  << "\",\"contexts\":" << screen.contexts
                  << ",\"add_sub_units\":"
                  << screen.resources.addSubUnits
                  << ",\"multiply_units\":"
                  << screen.resources.multiplyUnits
                  << ",\"unified_units\":"
                  << screen.resources.unifiedAddMultiplyUnits
                  << ",\"divide_units\":"
                  << screen.resources.divideUnits
                  << ",\"divide_initiation_interval\":"
                  << screen.resources.divideInitiationInterval
                  << ",\"add_sub_operations\":"
                  << result.operations.addSub
                  << ",\"multiply_operations\":"
                  << result.operations.multiply
                  << ",\"divide_operations\":"
                  << result.operations.divide
                  << ",\"critical_path_cycles\":"
                  << result.criticalPathCycles
                  << ",\"resource_lower_bound_cycles\":"
                  << result.resourceLowerBoundCycles
                  << ",\"lower_bound_cycles\":"
                  << result.lowerBoundCycles
                  << ",\"makespan_cycles\":"
                  << result.makespanCycles
                  << ",\"divider_queue_high_water\":"
                  << result.dividerQueueHighWater
                  << ",\"total_divider_wait_cycles\":"
                  << result.totalDividerWaitCycles
                  << ",\"maximum_divider_wait_cycles\":"
                  << result.maximumDividerWaitCycles
                  << ",\"first_divider_ready_cycle\":"
                  << result.firstDividerReadyCycle
                  << ",\"last_divider_ready_cycle\":"
                  << result.lastDividerReadyCycle
                  << ",\"first_divider_issue_cycle\":"
                  << result.firstDividerIssueCycle
                  << ",\"last_divider_issue_cycle\":"
                  << result.lastDividerIssueCycle << '}';
    }
    std::cout << "]}\n";
    return 0;
}
