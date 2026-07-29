#ifndef __MEM_LANLMAA_UMT_FP64_DEPENDENCY_MODEL_HH__
#define __MEM_LANLMAA_UMT_FP64_DEPENDENCY_MODEL_HH__

#include <algorithm>
#include <cstdint>
#include <initializer_list>
#include <limits>
#include <vector>

namespace gem5
{
namespace lanlmaa
{

constexpr uint32_t UmtFp64MaximumContexts = 64;

enum class UmtFp64OperationKind : uint8_t
{
    AddSub = 0,
    Multiply,
    Divide
};

struct UmtFp64DependencyNode
{
    UmtFp64OperationKind kind = UmtFp64OperationKind::AddSub;
    std::vector<uint32_t> dependencies;
};

struct UmtFp64OperationCounts
{
    uint64_t addSub = 0;
    uint64_t multiply = 0;
    uint64_t divide = 0;

    uint64_t
    total() const
    {
        return addSub + multiply + divide;
    }
};

struct UmtFp64DependencyDag
{
    std::vector<UmtFp64DependencyNode> nodes;
    uint32_t output = 0;
    bool observedSafeReuse = false;

    UmtFp64OperationCounts
    counts() const
    {
        UmtFp64OperationCounts value;
        for (const auto &node : nodes) {
            switch (node.kind) {
              case UmtFp64OperationKind::AddSub:
                ++value.addSub;
                break;
              case UmtFp64OperationKind::Multiply:
                ++value.multiply;
                break;
              case UmtFp64OperationKind::Divide:
                ++value.divide;
                break;
            }
        }
        return value;
    }
};

struct UmtFp64Resources
{
    uint32_t addSubUnits = 0;
    uint32_t multiplyUnits = 0;
    uint32_t unifiedAddMultiplyUnits = 0;
    uint32_t divideUnits = 0;
    uint32_t addSubLatency = 1;
    uint32_t multiplyLatency = 1;
    uint32_t divideLatency = 1;
    uint32_t addSubInitiationInterval = 1;
    uint32_t multiplyInitiationInterval = 1;
    uint32_t divideInitiationInterval = 1;
};

enum class UmtFp64ScheduleError : uint8_t
{
    None = 0,
    EmptyDag,
    BadDependency,
    BadOutput,
    BadContextCount,
    BadResources,
    Deadlock
};

struct UmtFp64ScheduleResult
{
    UmtFp64ScheduleError error = UmtFp64ScheduleError::None;
    uint32_t contexts = 0;
    UmtFp64OperationCounts operations;
    uint64_t criticalPathCycles = 0;
    uint64_t resourceLowerBoundCycles = 0;
    uint64_t lowerBoundCycles = 0;
    uint64_t makespanCycles = 0;
    uint64_t dividerQueueHighWater = 0;
    uint64_t totalDividerWaitCycles = 0;
    uint64_t maximumDividerWaitCycles = 0;
    uint64_t firstDividerReadyCycle = 0;
    uint64_t lastDividerReadyCycle = 0;
    uint64_t firstDividerIssueCycle = 0;
    uint64_t lastDividerIssueCycle = 0;

    explicit operator bool() const
    {
        return error == UmtFp64ScheduleError::None;
    }
};

class UmtFp64DependencyModel
{
  private:
    static uint32_t
    append(UmtFp64DependencyDag &dag, UmtFp64OperationKind kind,
           std::initializer_list<uint32_t> dependencies = {})
    {
        const uint32_t id = dag.nodes.size();
        dag.nodes.push_back({kind, dependencies});
        return id;
    }

    static uint32_t
    latency(UmtFp64OperationKind kind, const UmtFp64Resources &resources)
    {
        switch (kind) {
          case UmtFp64OperationKind::AddSub:
            return resources.addSubLatency;
          case UmtFp64OperationKind::Multiply:
            return resources.multiplyLatency;
          case UmtFp64OperationKind::Divide:
            return resources.divideLatency;
        }
        return 0;
    }

    static uint64_t
    issueLowerBound(uint64_t operations, uint32_t units, uint32_t interval,
                    uint32_t operationLatency)
    {
        if (operations == 0) {
            return 0;
        }
        const uint64_t waves = (operations + units - 1) / units;
        return (waves - 1) * interval + operationLatency;
    }

    static bool
    issue(std::vector<uint64_t> &nextIssue, uint64_t cycle,
          uint32_t interval)
    {
        for (auto &available : nextIssue) {
            if (available <= cycle) {
                available = cycle + interval;
                return true;
            }
        }
        return false;
    }

    static uint64_t
    earliestIssue(const std::vector<uint64_t> &nextIssue)
    {
        return *std::min_element(nextIssue.begin(), nextIssue.end());
    }

  public:
    static UmtFp64DependencyDag
    buildThreeFaceSpecial(bool observedSafeReuse)
    {
        UmtFp64DependencyDag dag;
        dag.observedSafeReuse = observedSafeReuse;

        const uint32_t currentTau = append(
            dag, UmtFp64OperationKind::Multiply);
        const uint32_t currentSource = append(
            dag, UmtFp64OperationKind::AddSub, {currentTau});
        uint32_t accumulator = append(
            dag, UmtFp64OperationKind::Multiply, {currentSource});

        std::vector<uint32_t> neighborSources;
        for (uint32_t face = 0; face < 3; ++face) {
            const uint32_t neighborTau = append(
                dag, UmtFp64OperationKind::Multiply);
            neighborSources.push_back(append(
                dag, UmtFp64OperationKind::AddSub, {neighborTau}));
        }

        for (uint32_t face = 0; face < 3; ++face) {
            const uint32_t incident = append(
                dag, UmtFp64OperationKind::Multiply);
            accumulator = append(
                dag, UmtFp64OperationKind::AddSub,
                {accumulator, incident});
        }

        uint32_t sharedSigv = 0;
        uint32_t sharedSigv2 = 0;
        uint32_t sharedAlphaSigv2 = 0;
        uint32_t sharedFourSigv = 0;
        uint32_t sharedSixSigv2 = 0;
        uint32_t sharedTwoSigv = 0;
        if (observedSafeReuse) {
            sharedSigv = append(dag, UmtFp64OperationKind::Multiply);
            sharedSigv2 = append(
                dag, UmtFp64OperationKind::Multiply, {sharedSigv});
            sharedAlphaSigv2 = append(
                dag, UmtFp64OperationKind::Multiply, {sharedSigv2});
            sharedFourSigv = append(
                dag, UmtFp64OperationKind::Multiply, {sharedSigv});
            sharedSixSigv2 = append(
                dag, UmtFp64OperationKind::Multiply, {sharedSigv2});
            sharedTwoSigv = append(
                dag, UmtFp64OperationKind::Multiply, {sharedSigv});
        }

        for (uint32_t face = 0; face < 3; ++face) {
            uint32_t sigv;
            uint32_t sigv2;
            uint32_t alphaSigv2;
            uint32_t fourSigvGnum;
            uint32_t fourSigvGden;
            uint32_t sixSigv2;
            uint32_t twoSigv;
            if (observedSafeReuse) {
                sigv = sharedSigv;
                sigv2 = sharedSigv2;
                alphaSigv2 = sharedAlphaSigv2;
                fourSigvGnum = sharedFourSigv;
                fourSigvGden = sharedFourSigv;
                sixSigv2 = sharedSixSigv2;
                twoSigv = sharedTwoSigv;
            } else {
                sigv = append(dag, UmtFp64OperationKind::Multiply);
                sigv2 = append(
                    dag, UmtFp64OperationKind::Multiply, {sigv});
                alphaSigv2 = append(
                    dag, UmtFp64OperationKind::Multiply, {sigv2});
                fourSigvGnum = append(
                    dag, UmtFp64OperationKind::Multiply, {sigv});
                fourSigvGden = append(
                    dag, UmtFp64OperationKind::Multiply, {sigv});
                sixSigv2 = append(
                    dag, UmtFp64OperationKind::Multiply, {sigv2});
                twoSigv = append(
                    dag, UmtFp64OperationKind::Multiply, {sigv});
            }

            // aez^2 is source-hoisted corner/angle geometry, not a group op.
            const uint32_t threeAez = append(
                dag, UmtFp64OperationKind::Multiply);
            const uint32_t gnumInner = append(
                dag, UmtFp64OperationKind::AddSub,
                {fourSigvGnum, threeAez});
            const uint32_t aezGnumInner = append(
                dag, UmtFp64OperationKind::Multiply, {gnumInner});
            const uint32_t gnumSum = append(
                dag, UmtFp64OperationKind::AddSub,
                {alphaSigv2, aezGnumInner});
            const uint32_t gnum = append(
                dag, UmtFp64OperationKind::Multiply, {gnumSum});

            const uint32_t twoSigvPlusAez = append(
                dag, UmtFp64OperationKind::AddSub, {twoSigv});
            const uint32_t twoAez = append(
                dag, UmtFp64OperationKind::Multiply);
            const uint32_t twoAezInner = append(
                dag, UmtFp64OperationKind::Multiply,
                {twoAez, twoSigvPlusAez});
            const uint32_t sixPlusInner = append(
                dag, UmtFp64OperationKind::AddSub,
                {sixSigv2, twoAezInner});
            const uint32_t aezGdenInner = append(
                dag, UmtFp64OperationKind::Multiply, {sixPlusInner});
            const uint32_t fourSigvSigv2 = append(
                dag, UmtFp64OperationKind::Multiply,
                {fourSigvGden, sigv2});
            const uint32_t gdenSum = append(
                dag, UmtFp64OperationKind::AddSub,
                {fourSigvSigv2, aezGdenInner});
            const uint32_t gden = append(
                dag, UmtFp64OperationKind::Multiply, {gdenSum});

            const uint32_t sigmaPsi = append(
                dag, UmtFp64OperationKind::Multiply);
            const uint32_t psiMinusQ = append(
                dag, UmtFp64OperationKind::AddSub,
                {sigmaPsi, currentSource});
            const uint32_t volumeGnum = append(
                dag, UmtFp64OperationKind::Multiply, {gnum});
            const uint32_t firstTerm = append(
                dag, UmtFp64OperationKind::Multiply,
                {volumeGnum, psiMinusQ});
            const uint32_t qDifference = append(
                dag, UmtFp64OperationKind::AddSub,
                {currentSource, neighborSources[face]});
            const uint32_t halfAez = append(
                dag, UmtFp64OperationKind::Multiply);
            const uint32_t halfAezGden = append(
                dag, UmtFp64OperationKind::Multiply, {halfAez, gden});
            const uint32_t secondTerm = append(
                dag, UmtFp64OperationKind::Multiply,
                {halfAezGden, qDifference});
            const uint32_t numerator = append(
                dag, UmtFp64OperationKind::AddSub,
                {firstTerm, secondTerm});
            const uint32_t gdenSigma = append(
                dag, UmtFp64OperationKind::Multiply, {gden});
            const uint32_t denominator = append(
                dag, UmtFp64OperationKind::AddSub,
                {gnum, gdenSigma});
            uint32_t contribution = append(
                dag, UmtFp64OperationKind::Divide,
                {numerator, denominator});
            if (!observedSafeReuse) {
                contribution = append(
                    dag, UmtFp64OperationKind::Multiply, {contribution});
            }
            accumulator = append(
                dag, UmtFp64OperationKind::AddSub,
                {accumulator, contribution});
        }

        uint32_t sigmaVolume;
        if (observedSafeReuse) {
            sigmaVolume = sharedSigv;
        } else {
            sigmaVolume = append(dag, UmtFp64OperationKind::Multiply);
        }
        const uint32_t outputDenominator = append(
            dag, UmtFp64OperationKind::AddSub, {sigmaVolume});
        dag.output = append(
            dag, UmtFp64OperationKind::Divide,
            {accumulator, outputDenominator});
        return dag;
    }

    static UmtFp64ScheduleResult
    schedule(const UmtFp64DependencyDag &dag, uint32_t contexts,
             const UmtFp64Resources &resources)
    {
        UmtFp64ScheduleResult result;
        result.contexts = contexts;
        if (dag.nodes.empty()) {
            result.error = UmtFp64ScheduleError::EmptyDag;
            return result;
        }
        if (dag.output >= dag.nodes.size()) {
            result.error = UmtFp64ScheduleError::BadOutput;
            return result;
        }
        for (uint32_t node = 0; node < dag.nodes.size(); ++node) {
            for (const uint32_t dependency :
                    dag.nodes[node].dependencies) {
                if (dependency >= node) {
                    result.error = UmtFp64ScheduleError::BadDependency;
                    return result;
                }
            }
        }
        if (contexts == 0 || contexts > UmtFp64MaximumContexts) {
            result.error = UmtFp64ScheduleError::BadContextCount;
            return result;
        }
        const bool separate = resources.unifiedAddMultiplyUnits == 0 &&
            resources.addSubUnits != 0 && resources.multiplyUnits != 0;
        const bool unified = resources.unifiedAddMultiplyUnits != 0 &&
            resources.addSubUnits == 0 && resources.multiplyUnits == 0;
        if ((!separate && !unified) || resources.divideUnits == 0 ||
            resources.addSubLatency == 0 ||
            resources.multiplyLatency == 0 ||
            resources.divideLatency == 0 ||
            resources.addSubInitiationInterval == 0 ||
            resources.multiplyInitiationInterval == 0 ||
            resources.divideInitiationInterval == 0 ||
            (unified &&
             (resources.addSubLatency != resources.multiplyLatency ||
              resources.addSubInitiationInterval !=
                  resources.multiplyInitiationInterval))) {
            result.error = UmtFp64ScheduleError::BadResources;
            return result;
        }

        const auto perContext = dag.counts();
        result.operations.addSub = perContext.addSub * contexts;
        result.operations.multiply = perContext.multiply * contexts;
        result.operations.divide = perContext.divide * contexts;

        std::vector<std::vector<uint32_t>> successors(dag.nodes.size());
        for (uint32_t node = 0; node < dag.nodes.size(); ++node) {
            for (const uint32_t dependency :
                    dag.nodes[node].dependencies) {
                successors[dependency].push_back(node);
            }
        }
        std::vector<uint64_t> bottom(dag.nodes.size(), 0);
        for (size_t offset = dag.nodes.size(); offset > 0; --offset) {
            const uint32_t node = offset - 1;
            uint64_t tail = 0;
            for (const uint32_t successor : successors[node]) {
                tail = std::max(tail, bottom[successor]);
            }
            bottom[node] = latency(dag.nodes[node].kind, resources) + tail;
        }
        uint64_t criticalPath = 0;
        for (uint32_t node = 0; node < dag.nodes.size(); ++node) {
            if (dag.nodes[node].dependencies.empty()) {
                criticalPath = std::max(criticalPath, bottom[node]);
            }
        }
        result.criticalPathCycles = criticalPath;

        uint64_t addMultiplyBound = 0;
        if (separate) {
            const uint64_t addBound = issueLowerBound(
                result.operations.addSub, resources.addSubUnits,
                resources.addSubInitiationInterval,
                resources.addSubLatency);
            const uint64_t multiplyBound = issueLowerBound(
                result.operations.multiply, resources.multiplyUnits,
                resources.multiplyInitiationInterval,
                resources.multiplyLatency);
            addMultiplyBound = std::max(addBound, multiplyBound);
        } else {
            addMultiplyBound = issueLowerBound(
                result.operations.addSub + result.operations.multiply,
                resources.unifiedAddMultiplyUnits,
                resources.addSubInitiationInterval,
                resources.addSubLatency);
        }
        const uint64_t divideBound = issueLowerBound(
            result.operations.divide, resources.divideUnits,
            resources.divideInitiationInterval, resources.divideLatency);
        result.resourceLowerBoundCycles =
            std::max(addMultiplyBound, divideBound);
        result.lowerBoundCycles =
            std::max(result.criticalPathCycles,
                     result.resourceLowerBoundCycles);

        const uint64_t Unscheduled = std::numeric_limits<uint64_t>::max();
        const size_t nodesPerContext = dag.nodes.size();
        const size_t totalNodes = nodesPerContext * contexts;
        std::vector<uint64_t> completion(totalNodes, Unscheduled);
        std::vector<uint64_t> addNext(resources.addSubUnits, 0);
        std::vector<uint64_t> multiplyNext(resources.multiplyUnits, 0);
        std::vector<uint64_t> unifiedNext(
            resources.unifiedAddMultiplyUnits, 0);
        std::vector<uint64_t> divideNext(resources.divideUnits, 0);

        struct Candidate
        {
            size_t global = 0;
            uint32_t local = 0;
            uint64_t ready = 0;
            uint64_t bottom = 0;
        };

        size_t scheduled = 0;
        uint64_t cycle = 0;
        bool sawDivider = false;
        while (scheduled < totalNodes) {
            std::vector<Candidate> candidates;
            uint64_t nextCompletion = Unscheduled;
            for (size_t global = 0; global < totalNodes; ++global) {
                if (completion[global] != Unscheduled) {
                    if (completion[global] > cycle) {
                        nextCompletion =
                            std::min(nextCompletion, completion[global]);
                    }
                    continue;
                }
                const uint32_t local = global % nodesPerContext;
                const size_t base = global - local;
                bool dependenciesScheduled = true;
                uint64_t ready = 0;
                for (const uint32_t dependency :
                        dag.nodes[local].dependencies) {
                    const uint64_t dependencyCompletion =
                        completion[base + dependency];
                    if (dependencyCompletion == Unscheduled) {
                        dependenciesScheduled = false;
                        break;
                    }
                    ready = std::max(ready, dependencyCompletion);
                }
                if (dependenciesScheduled && ready <= cycle) {
                    candidates.push_back({global, local, ready,
                                          bottom[local]});
                } else if (dependenciesScheduled) {
                    nextCompletion = std::min(nextCompletion, ready);
                }
            }

            uint64_t readyDivides = 0;
            for (const auto &candidate : candidates) {
                if (dag.nodes[candidate.local].kind ==
                        UmtFp64OperationKind::Divide) {
                    ++readyDivides;
                }
            }
            result.dividerQueueHighWater =
                std::max(result.dividerQueueHighWater, readyDivides);
            std::sort(candidates.begin(), candidates.end(),
                      [](const Candidate &first, const Candidate &second) {
                          if (first.bottom != second.bottom) {
                              return first.bottom > second.bottom;
                          }
                          return first.global < second.global;
                      });

            uint64_t nextResource = Unscheduled;
            for (const auto &candidate : candidates) {
                const auto kind = dag.nodes[candidate.local].kind;
                bool accepted = false;
                if (kind == UmtFp64OperationKind::Divide) {
                    accepted = issue(
                        divideNext, cycle,
                        resources.divideInitiationInterval);
                    if (!accepted) {
                        nextResource = std::min(
                            nextResource, earliestIssue(divideNext));
                    }
                } else if (unified) {
                    accepted = issue(
                        unifiedNext, cycle,
                        resources.addSubInitiationInterval);
                    if (!accepted) {
                        nextResource = std::min(
                            nextResource, earliestIssue(unifiedNext));
                    }
                } else if (kind == UmtFp64OperationKind::AddSub) {
                    accepted = issue(
                        addNext, cycle,
                        resources.addSubInitiationInterval);
                    if (!accepted) {
                        nextResource = std::min(
                            nextResource, earliestIssue(addNext));
                    }
                } else {
                    accepted = issue(
                        multiplyNext, cycle,
                        resources.multiplyInitiationInterval);
                    if (!accepted) {
                        nextResource = std::min(
                            nextResource, earliestIssue(multiplyNext));
                    }
                }
                if (!accepted) {
                    continue;
                }
                completion[candidate.global] =
                    cycle + latency(kind, resources);
                ++scheduled;
                if (kind == UmtFp64OperationKind::Divide) {
                    const uint64_t wait = cycle - candidate.ready;
                    result.totalDividerWaitCycles += wait;
                    result.maximumDividerWaitCycles = std::max(
                        result.maximumDividerWaitCycles, wait);
                    if (!sawDivider) {
                        result.firstDividerReadyCycle = candidate.ready;
                        result.lastDividerReadyCycle = candidate.ready;
                        result.firstDividerIssueCycle = cycle;
                        result.lastDividerIssueCycle = cycle;
                        sawDivider = true;
                    } else {
                        result.firstDividerReadyCycle = std::min(
                            result.firstDividerReadyCycle,
                            candidate.ready);
                        result.lastDividerReadyCycle = std::max(
                            result.lastDividerReadyCycle,
                            candidate.ready);
                        result.firstDividerIssueCycle = std::min(
                            result.firstDividerIssueCycle, cycle);
                        result.lastDividerIssueCycle = std::max(
                            result.lastDividerIssueCycle, cycle);
                    }
                }
            }

            if (scheduled == totalNodes) {
                break;
            }
            uint64_t nextCycle = std::min(nextCompletion, nextResource);
            for (const uint64_t done : completion) {
                if (done != Unscheduled && done > cycle) {
                    nextCycle = std::min(nextCycle, done);
                }
            }
            if (nextCycle == Unscheduled || nextCycle <= cycle) {
                result.error = UmtFp64ScheduleError::Deadlock;
                return result;
            }
            cycle = nextCycle;
        }

        for (const uint64_t done : completion) {
            result.makespanCycles = std::max(result.makespanCycles, done);
        }
        return result;
    }
};

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_UMT_FP64_DEPENDENCY_MODEL_HH__
