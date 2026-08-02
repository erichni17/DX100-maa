#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <limits>

#include "mem/LANLMAA/SharedOverlayModeBarrier.hh"
#include "mem/LANLMAA/UmtFp64DependencyModel.hh"
#include "mem/LANLMAA/UmtMixedCornerModel.hh"
#include "mem/LANLMAA/UmtMixedCornerScheduleModel.hh"
#include "umt_corner_sweep_record.hh"

using namespace gem5::lanlmaa;

namespace
{

bool
sameBits(double first, double second)
{
    return std::memcmp(&first, &second, sizeof(first)) == 0;
}

uint64_t
bitsOf(double value)
{
    uint64_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}

UmtCornerSweepRecord
readRecord(const char *path)
{
    std::ifstream input(path);
    assert(input);
    auto parsed = parseUmtCornerSweepRecord(input);
    assert(parsed);
    return std::move(parsed.record);
}

uint32_t
population(uint8_t mask)
{
    uint32_t count = 0;
    for (uint32_t bit = 0; bit < 3; ++bit) {
        count += (mask & (1U << bit)) != 0;
    }
    return count;
}

UmtFp64MixedThreeFaceConfig
configFor(const UmtMixedCornerGeometry &geometry)
{
    UmtFp64MixedThreeFaceConfig config;
    std::array<double, 4> volumes{{
        geometry.currentVolume,
        geometry.firstVolume[0],
        geometry.firstVolume[1],
        geometry.firstVolume[2]
    }};
    for (uint32_t index = 0; index < volumes.size(); ++index) {
        config.volumeBits[index] = bitsOf(volumes[index]);
    }
    uint8_t maximumClass = 0;
    for (uint32_t index = 1; index < volumes.size(); ++index) {
        bool matched = false;
        for (uint32_t previous = 0; previous < index; ++previous) {
            if (sameBits(volumes[index], volumes[previous])) {
                config.volumeClass[index] = config.volumeClass[previous];
                matched = true;
                break;
            }
        }
        if (!matched) {
            config.volumeClass[index] = ++maximumClass;
        }
    }
    config.specialMask = 0;
    for (uint32_t face = 0; face < UmtMixedCornerFaceCount; ++face) {
        if (geometry.signedEzNorm[face] < 0.0) {
            config.incomingMask |= 1U << face;
        }
        if (geometry.currentFpNorm[face] < 0.0) {
            config.incidentMask |= 1U << face;
        }
        if (geometry.oppositeActive[face]) {
            config.specialMask |= 1U << face;
        }
    }
    return config;
}

UmtFp64Resources
selectedResources()
{
    UmtFp64Resources resources;
    resources.globalIssueWidth = 1;
    resources.addSubUnits = 1;
    resources.multiplyUnits = 1;
    resources.divideUnits = 8;
    resources.divideLatency = 64;
    resources.divideInitiationInterval = 64;
    return resources;
}

void
testBuildRejections()
{
    UmtFp64MixedThreeFaceConfig config;
    config.incomingMask = 8;
    assert(UmtFp64DependencyModel::buildMixedThreeFaceSpecial(config).error ==
           UmtFp64DagBuildError::BadMask);

    config = UmtFp64MixedThreeFaceConfig{};
    config.specialMask = 6;
    assert(UmtFp64DependencyModel::buildMixedThreeFaceSpecial(config).error ==
           UmtFp64DagBuildError::UnsupportedFallback);

    config = UmtFp64MixedThreeFaceConfig{};
    config.volumeClass = {{1, 1, 1, 1}};
    assert(UmtFp64DependencyModel::buildMixedThreeFaceSpecial(config).error ==
           UmtFp64DagBuildError::NoncanonicalVolumeClasses);
    config.volumeClass = {{0, 2, 0, 0}};
    assert(UmtFp64DependencyModel::buildMixedThreeFaceSpecial(config).error ==
           UmtFp64DagBuildError::NoncanonicalVolumeClasses);
    config = UmtFp64MixedThreeFaceConfig{};
    config.volumeClass = {{0, 1, 0, 0}};
    assert(UmtFp64DependencyModel::buildMixedThreeFaceSpecial(config).error ==
           UmtFp64DagBuildError::NoncanonicalVolumeClasses);
    config = UmtFp64MixedThreeFaceConfig{};
    config.volumeBits = {{0, 1, 0, 0}};
    assert(UmtFp64DependencyModel::buildMixedThreeFaceSpecial(config).error ==
           UmtFp64DagBuildError::NoncanonicalVolumeClasses);
}

void
testUnequalVolumeReuse()
{
    UmtFp64MixedThreeFaceConfig config;
    config.incomingMask = 1;
    config.incidentMask = 6;

    const auto equal =
        UmtFp64DependencyModel::buildMixedThreeFaceSpecial(config);
    assert(equal);
    assert(equal.dag.counts().addSub == 38);
    assert(equal.dag.counts().multiply == 59);
    assert(equal.dag.counts().divide == 4);

    config.volumeClass = {{0, 1, 0, 0}};
    config.volumeBits = {{0, 1, 0, 0}};
    const auto twoClasses =
        UmtFp64DependencyModel::buildMixedThreeFaceSpecial(config);
    assert(twoClasses);
    assert(twoClasses.dag.counts().multiply == 65);

    config.volumeClass = {{0, 1, 2, 3}};
    config.volumeBits = {{0, 1, 2, 3}};
    const auto allDistinct =
        UmtFp64DependencyModel::buildMixedThreeFaceSpecial(config);
    assert(allDistinct);
    assert(allDistinct.dag.counts().multiply == 72);
}

void
testOverlayPorts()
{
    std::array<bool, SharedUpdateStore.entries> mapped{};
    for (uint32_t context = 0;
         context < UmtMixedScheduleMaximumContexts; ++context) {
        const uint32_t first =
            UmtMixedCornerSidecarPortModel::entryFor(context, 0);
        const uint32_t second =
            UmtMixedCornerSidecarPortModel::entryFor(context, 1);
        assert(first < mapped.size() && second < mapped.size());
        assert(!mapped[first] && !mapped[second]);
        mapped[first] = true;
        mapped[second] = true;
        assert(UmtMixedCornerSidecarPortModel::bankFor(context, 0) !=
               UmtMixedCornerSidecarPortModel::bankFor(context, 1));
    }
    assert(std::all_of(mapped.begin(), mapped.end(),
                       [](bool value) { return value; }));

    UmtMixedCornerSidecarPortModel model;
    assert(model.activate(0) == UmtMixedOverlayResult::BadContextCount);
    assert(model.activate(33) == UmtMixedOverlayResult::BadContextCount);
    assert(model.activate(64) == UmtMixedOverlayResult::BadContextCount);
    assert(model.activate(32) == UmtMixedOverlayResult::Accepted);
    assert(model.enqueueNormalUpdateCombiner() ==
           UmtMixedOverlayResult::OwnerConflict);
    assert(model.enqueue({1, 32, 0, UmtMixedOverlayAccess::Read}) ==
           UmtMixedOverlayResult::BadContext);
    assert(model.enqueue({1, 0, 2, UmtMixedOverlayAccess::Read}) ==
           UmtMixedOverlayResult::BadWord);

    // Contexts zero and four share banks; adjacent words do not.
    assert(model.enqueue({10, 0, 0, UmtMixedOverlayAccess::Write}) ==
           UmtMixedOverlayResult::Accepted);
    assert(model.enqueue({11, 4, 0, UmtMixedOverlayAccess::Read}) ==
           UmtMixedOverlayResult::Accepted);
    assert(model.enqueue({12, 0, 1, UmtMixedOverlayAccess::Read}) ==
           UmtMixedOverlayResult::Accepted);
    assert(model.enqueue({13, 1, 0, UmtMixedOverlayAccess::Write}) ==
           UmtMixedOverlayResult::Accepted);
    assert(model.deactivate() ==
           UmtMixedOverlayResult::OutstandingTraffic);

    auto cycle = model.cycle();
    assert(cycle.valid);
    assert(cycle.served.size() == 3);
    assert(cycle.served[0].tag == 10);
    assert(cycle.served[1].tag == 12);
    assert(cycle.served[2].tag == 13);
    assert(cycle.pending == 1);
    cycle = model.cycle();
    assert(cycle.valid && cycle.served.size() == 1);
    assert(cycle.served[0].tag == 11);
    assert(cycle.pending == 0);
    assert(model.deactivate() == UmtMixedOverlayResult::Accepted);
    assert(model.enqueueNormalUpdateCombiner() ==
           UmtMixedOverlayResult::Accepted);
    assert(!model.cycle().valid);
}

void
testRealModeBarrier()
{
    SharedOverlayModeBarrier barrier;
    SharedOverlayReservation umt;
    umt.mode = SharedOverlayMode::UmtCornerSweep;
    umt.pairedEntries = UmtMixedScheduleMaximumContexts;
    assert(barrier.acquire(umt) == SharedOverlayResult::Accepted);

    SharedOverlayReservation updateOwner;
    updateOwner.mode = SharedOverlayMode::BransonEventTally;
    updateOwner.pairedEntries = 1;
    assert(barrier.acquire(updateOwner) == SharedOverlayResult::Busy);
    assert(barrier.acceptTraffic(SharedOverlayTrafficKind::Read) ==
           SharedOverlayResult::Accepted);
    assert(barrier.acceptTraffic(SharedOverlayTrafficKind::Completion) ==
           SharedOverlayResult::Accepted);
    assert(barrier.beginDrain() == SharedOverlayResult::Accepted);
    assert(barrier.release(false) ==
           SharedOverlayResult::OutstandingObligations);
    assert(barrier.acknowledgeTraffic(SharedOverlayTrafficKind::Read) ==
           SharedOverlayResult::Accepted);
    assert(barrier.release(false) ==
           SharedOverlayResult::OutstandingObligations);
    assert(barrier.acknowledgeTraffic(SharedOverlayTrafficKind::Completion) ==
           SharedOverlayResult::Accepted);
    assert(barrier.release(false) == SharedOverlayResult::Accepted);
}

} // anonymous namespace

int
main(int argc, char **argv)
{
    assert(argc == 17);
    testBuildRejections();
    testUnequalVolumeReuse();
    testOverlayPorts();
    testRealModeBarrier();

    std::array<bool, 64> configurations{};
    std::array<bool, 4> incomingCounts{};
    uint32_t uniqueConfigurations = 0;
    uint64_t minimum16 = std::numeric_limits<uint64_t>::max();
    uint64_t maximum16 = 0;
    uint64_t minimum32 = std::numeric_limits<uint64_t>::max();
    uint64_t maximum32 = 0;
    for (int index = 1; index < argc; ++index) {
        const auto native = readRecord(argv[index]);
        assert(native.input.cornerOrder.size() == 1);
        const auto plan = buildUmtMixedCornerPlan(
            native.descriptor, native.input,
            native.input.cornerOrder.front());
        assert(plan);
        const auto config = configFor(plan.plan.geometry);
        assert(config.specialMask == UmtFp64ThreeFaceMask);
        assert(population(config.incomingMask) +
                   population(config.incidentMask) ==
               3);
        incomingCounts[population(config.incomingMask)] = true;
        assert(config.volumeClass ==
               (std::array<uint8_t, 4>{{0, 0, 0, 0}}));

        const auto build =
            UmtFp64DependencyModel::buildMixedThreeFaceSpecial(config);
        assert(build);
        const auto counts = build.dag.counts();
        assert(counts.addSub == 38);
        assert(counts.multiply == 59);
        assert(counts.divide == 4);

        const uint32_t key = config.incomingMask * 8 + config.incidentMask;
        if (!configurations[key]) {
            configurations[key] = true;
            ++uniqueConfigurations;
            const auto schedule16 = UmtFp64DependencyModel::schedule(
                build.dag, 16, selectedResources());
            const auto schedule32 = UmtFp64DependencyModel::schedule(
                build.dag, 32, selectedResources());
            assert(schedule16 && schedule32);
            minimum16 = std::min(minimum16, schedule16.makespanCycles);
            maximum16 = std::max(maximum16, schedule16.makespanCycles);
            minimum32 = std::min(minimum32, schedule32.makespanCycles);
            maximum32 = std::max(maximum32, schedule32.makespanCycles);
        }
    }
    assert(uniqueConfigurations == 6);
    assert(std::all_of(incomingCounts.begin(), incomingCounts.end(),
                       [](bool value) { return value; }));
    assert(minimum16 == 1819 && maximum16 == 1819);
    assert(minimum32 == 3595 && maximum32 == 3595);
    std::cout << "{\"status\":\"PASS\",\"records\":16"
              << ",\"unique_mask_pairs\":" << uniqueConfigurations
              << ",\"operations\":\"38/59/4\""
              << ",\"cycles16_min\":" << minimum16
              << ",\"cycles16_max\":" << maximum16
              << ",\"cycles32_min\":" << minimum32
              << ",\"cycles32_max\":" << maximum32
              << ",\"mixed_context_cap\":"
              << UmtMixedScheduleMaximumContexts
              << ",\"sidecar_entries\":" << SharedUpdateStore.entries
              << "}\n";
    return 0;
}
