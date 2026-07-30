#include <cassert>

#include "mem/LANLMAA/UmtFp64DependencyModel.hh"

using namespace gem5::lanlmaa;

namespace
{

UmtFp64Resources
separate(uint32_t adders, uint32_t multipliers, uint32_t dividers,
         uint32_t divideInterval, uint32_t issueWidth = 64)
{
    UmtFp64Resources value;
    value.globalIssueWidth = issueWidth;
    value.addSubUnits = adders;
    value.multiplyUnits = multipliers;
    value.divideUnits = dividers;
    value.divideLatency = 64;
    value.divideInitiationInterval = divideInterval;
    return value;
}

UmtFp64Resources
unified(uint32_t lanes, uint32_t dividers, uint32_t divideInterval,
        uint32_t issueWidth = 64)
{
    UmtFp64Resources value;
    value.globalIssueWidth = issueWidth;
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
    const auto source =
        UmtFp64DependencyModel::buildThreeFaceSpecial(false);
    const auto sourceCounts = source.counts();
    assert(sourceCounts.addSub == 38);
    assert(sourceCounts.multiply == 78);
    assert(sourceCounts.divide == 4);
    assert(sourceCounts.total() == 120);
    assert(source.output + 1 == source.nodes.size());

    const auto reuse =
        UmtFp64DependencyModel::buildThreeFaceSpecial(true);
    const auto reuseCounts = reuse.counts();
    assert(reuseCounts.addSub == 38);
    assert(reuseCounts.multiply == 59);
    assert(reuseCounts.divide == 4);
    assert(reuseCounts.total() == 101);
    assert(reuse.output + 1 == reuse.nodes.size());

    const auto sourceBatch = UmtFp64DependencyModel::schedule(
        source, 32, separate(1, 2, 1, 64));
    assert(sourceBatch);
    assert(sourceBatch.operations.addSub == 1216);
    assert(sourceBatch.operations.multiply == 2496);
    assert(sourceBatch.operations.divide == 128);
    assert(sourceBatch.criticalPathCycles == 143);
    assert(sourceBatch.makespanCycles == 9060);
    assert(sourceBatch.lowerBoundCycles <= sourceBatch.makespanCycles);

    const auto iterativeOne = UmtFp64DependencyModel::schedule(
        reuse, 32, separate(1, 2, 1, 64));
    const auto iterativeTwo = UmtFp64DependencyModel::schedule(
        reuse, 32, separate(1, 2, 2, 64));
    const auto iterativeFour = UmtFp64DependencyModel::schedule(
        reuse, 32, separate(1, 2, 4, 64));
    const auto iterativeEight = UmtFp64DependencyModel::schedule(
        reuse, 32, separate(1, 2, 8, 64));
    const auto interleaved = UmtFp64DependencyModel::schedule(
        reuse, 32, separate(1, 2, 1, 8));
    const auto pipelined = UmtFp64DependencyModel::schedule(
        reuse, 32, separate(1, 2, 1, 1));
    const auto sourcePipelined = UmtFp64DependencyModel::schedule(
        source, 32, separate(1, 2, 1, 1));
    assert(iterativeOne && iterativeTwo && iterativeFour && iterativeEight);
    assert(interleaved && pipelined && sourcePipelined);
    assert(iterativeOne.resourceLowerBoundCycles == 8192);
    assert(iterativeEight.resourceLowerBoundCycles == 1216);
    assert(pipelined.resourceLowerBoundCycles == 1216);
    assert(iterativeOne.criticalPathCycles == 142);
    assert(iterativeOne.makespanCycles == 8947);
    assert(iterativeTwo.makespanCycles == 4855);
    assert(iterativeFour.makespanCycles == 2815);
    assert(iterativeEight.makespanCycles == 1807);
    assert(interleaved.makespanCycles == 1835);
    assert(pipelined.makespanCycles == 1281);
    assert(sourcePipelined.makespanCycles == 1425);
    assert(iterativeTwo.makespanCycles < iterativeOne.makespanCycles);
    assert(iterativeFour.makespanCycles < iterativeTwo.makespanCycles);
    assert(iterativeEight.makespanCycles < iterativeFour.makespanCycles);
    assert(interleaved.makespanCycles < iterativeOne.makespanCycles);
    assert(pipelined.makespanCycles < interleaved.makespanCycles);
    assert(pipelined.makespanCycles < sourcePipelined.makespanCycles);
    assert(iterativeOne.dividerQueueHighWater > 1);
    assert(iterativeOne.maximumDividerWaitCycles > 0);

    const auto singleIssue = UmtFp64DependencyModel::schedule(
        reuse, 32, separate(1, 1, 8, 64, 1));
    const auto dualIssue = UmtFp64DependencyModel::schedule(
        reuse, 32, separate(1, 1, 8, 64, 2));
    const auto unconstrainedIssue = UmtFp64DependencyModel::schedule(
        reuse, 32, separate(1, 1, 8, 64, 64));
    assert(singleIssue && dualIssue && unconstrainedIssue);
    assert(singleIssue.resourceLowerBoundCycles == 3232);
    assert(singleIssue.makespanCycles >= singleIssue.resourceLowerBoundCycles);
    assert(singleIssue.makespanCycles > dualIssue.makespanCycles);
    assert(dualIssue.makespanCycles >= unconstrainedIssue.makespanCycles);

    const auto unifiedTwo = UmtFp64DependencyModel::schedule(
        reuse, 32, unified(2, 1, 64));
    assert(unifiedTwo);
    assert(unifiedTwo.operations.total() == 32 * reuseCounts.total());
    assert(unifiedTwo.lowerBoundCycles <= unifiedTwo.makespanCycles);

    const auto batch16 = UmtFp64DependencyModel::schedule(
        reuse, 16, separate(1, 2, 1, 64));
    const auto batch64 = UmtFp64DependencyModel::schedule(
        reuse, 64, separate(1, 2, 1, 64));
    assert(batch16 && batch64);
    assert(batch16.makespanCycles < iterativeOne.makespanCycles);
    assert(iterativeOne.makespanCycles < batch64.makespanCycles);

    const auto repeat = UmtFp64DependencyModel::schedule(
        reuse, 32, separate(1, 2, 1, 64));
    assert(repeat);
    assert(repeat.makespanCycles == iterativeOne.makespanCycles);
    assert(repeat.totalDividerWaitCycles ==
           iterativeOne.totalDividerWaitCycles);

    auto invalid = UmtFp64DependencyModel::schedule(
        reuse, 0, separate(1, 2, 1, 64));
    assert(invalid.error == UmtFp64ScheduleError::BadContextCount);
    invalid = UmtFp64DependencyModel::schedule(
        reuse, UmtFp64MaximumContexts + 1,
        separate(1, 2, 1, 64));
    assert(invalid.error == UmtFp64ScheduleError::BadContextCount);

    auto badResources = separate(1, 2, 1, 64);
    badResources.unifiedAddMultiplyUnits = 1;
    invalid = UmtFp64DependencyModel::schedule(reuse, 1, badResources);
    assert(invalid.error == UmtFp64ScheduleError::BadResources);

    auto corrupt = reuse;
    corrupt.nodes[0].dependencies.push_back(0);
    invalid = UmtFp64DependencyModel::schedule(
        corrupt, 1, separate(1, 2, 1, 64));
    assert(invalid.error == UmtFp64ScheduleError::BadDependency);

    UmtFp64DependencyDag empty;
    invalid = UmtFp64DependencyModel::schedule(
        empty, 1, separate(1, 2, 1, 64));
    assert(invalid.error == UmtFp64ScheduleError::EmptyDag);

    corrupt = reuse;
    corrupt.output = corrupt.nodes.size();
    invalid = UmtFp64DependencyModel::schedule(
        corrupt, 1, separate(1, 2, 1, 64));
    assert(invalid.error == UmtFp64ScheduleError::BadOutput);

    badResources = separate(1, 2, 1, 64);
    badResources.divideInitiationInterval = 0;
    invalid = UmtFp64DependencyModel::schedule(reuse, 1, badResources);
    assert(invalid.error == UmtFp64ScheduleError::BadResources);

    badResources = separate(1, 2, 1, 64);
    badResources.globalIssueWidth = 0;
    invalid = UmtFp64DependencyModel::schedule(reuse, 1, badResources);
    assert(invalid.error == UmtFp64ScheduleError::BadResources);
    return 0;
}
