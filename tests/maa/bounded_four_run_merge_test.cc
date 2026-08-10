#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <tuple>
#include <vector>

#include "mem/MAA/BoundedFourRunMerge.hh"

using gem5::BoundedFourRunMerge;

namespace
{

using Descriptor = BoundedFourRunMerge::Descriptor;
using Result = BoundedFourRunMerge::Result;
using Key = std::tuple<uint32_t, uint32_t, uint64_t, uint16_t>;

struct Location
{
    uint32_t sliceRank = 0;
    uint32_t row = 0;
    uint64_t line = 0;
    uint32_t wid = 0;
};

uint64_t
valueFor(uint32_t value)
{
    return (static_cast<uint64_t>(value) * 0x9e3779b185ebca87ULL) ^
        0xd1b54a32d192ed03ULL;
}

void
testFiniteRunsCarryOrderingCoalescingAndRestoration()
{
    constexpr uint32_t PerRun = 31;
    constexpr uint32_t Logical = 4 * PerRun;
    constexpr uint64_t Base = 0x400000;
    const std::array<uint32_t, 4> populations{
        PerRun, PerRun, PerRun, PerRun};

    std::array<Location, Logical> locations{};
    std::array<std::vector<Descriptor>, 4> runs;
    for (uint32_t itr = 0; itr < Logical; ++itr) {
        // Seven A lines ensure equal-line clusters span all four runs.  The
        // nontrivial slice/row mapping also catches a line-only comparator.
        const uint32_t token = (itr * 11) % 7;
        locations[itr] = Location{
            token % 3, token / 3, 0x800000 + token * 64,
            (itr * 5) % 8};
        const uint32_t run = itr % 4;
        runs[run].push_back(Descriptor{
            static_cast<uint16_t>(itr), 0x81000000U + itr});
    }
    const auto keyFor = [&](const Descriptor &descriptor) {
        const Location &location = locations[descriptor.iteration];
        return Key{location.sliceRank, location.row, location.line,
                   descriptor.iteration};
    };
    for (auto &run : runs)
        std::sort(run.begin(), run.end(), [&](const auto &left,
                                              const auto &right) {
            return keyFor(left) < keyFor(right);
        });

    BoundedFourRunMerge merge;
    assert(merge.configure(Logical, populations, Base,
                           BoundedFourRunMerge::RequiredBackingBytes) ==
           Result::Accepted);
    assert(merge.activeHighWater() == PerRun);
    std::vector<uint8_t> backing(
        BoundedFourRunMerge::RequiredBackingBytes, 0);

    for (uint32_t run = 0; run < 4; ++run) {
        assert(merge.beginMaterialization(run) == Result::Accepted);
        std::vector<uint64_t> acks;
        for (const Descriptor descriptor : runs[run]) {
            if (merge.writeLineReady(false)) {
                uint64_t address = 0;
                uint32_t payload = 0;
                std::array<uint8_t, 64> data{};
                assert(merge.issueWriteLine(false, address, data, payload) ==
                       Result::Accepted);
                assert(payload == 64);
                std::copy(data.begin(), data.end(),
                          backing.begin() + (address - Base));
                acks.push_back(address);
            }
            assert(merge.stageMaterialized(descriptor) == Result::Accepted);
        }
        while (merge.writeLineReady(true)) {
            uint64_t address = 0;
            uint32_t payload = 0;
            std::array<uint8_t, 64> data{};
            assert(merge.issueWriteLine(true, address, data, payload) ==
                   Result::Accepted);
            std::copy(data.begin(), data.end(),
                      backing.begin() + (address - Base));
            acks.push_back(address);
        }
        for (const uint64_t address : acks)
            assert(merge.acknowledgeWrite(address) == Result::Accepted);
        assert(merge.finishMaterialization(run) == Result::Accepted);
    }
    assert(merge.materializedRecords() == Logical);
    assert(merge.sortedWriteLines() == 12);
    assert(merge.sortedWriteAcks() == 12);
    assert(merge.maxMaterializationCarryBytes() == 4);

    assert(merge.beginMerge() == Result::Accepted);
    std::array<uint64_t, Logical> restored{};
    std::array<bool, Logical> restoredValid{};
    Key previous{};
    bool havePrevious = false;
    uint64_t activeLine = 0;
    bool lineActive = false;
    uint32_t consumed = 0;

    while (!merge.mergeDone()) {
        for (uint32_t run = 0; run < 4; ++run) {
            if (!merge.needsRead(run))
                continue;
            uint64_t address = 0;
            uint32_t line = 0;
            assert(merge.nextRead(run, address, line) == Result::Accepted);
            std::array<uint8_t, 64> data{};
            std::copy_n(backing.begin() + (address - Base), 64,
                        data.begin());
            assert(merge.acceptRead(run, line, data) == Result::Accepted);
        }
        if (!merge.readyToSelect())
            continue;
        uint32_t selected = 4;
        const Result selectedResult = merge.selectHead(keyFor, selected);
        if (selectedResult == Result::NoWork)
            break;
        assert(selectedResult == Result::Accepted);
        assert(selected < 4);
        const Descriptor descriptor = merge.head(selected);
        const Key key = keyFor(descriptor);
        assert(!havePrevious || previous <= key);
        previous = key;
        havePrevious = true;
        const Location &location = locations[descriptor.iteration];
        if (!lineActive || activeLine != location.line) {
            if (lineActive)
                assert(merge.endSourceLine() == Result::Accepted);
            assert(merge.beginSourceLine(location.line) == Result::Accepted);
            activeLine = location.line;
            lineActive = true;
        }
        assert(!restoredValid[descriptor.iteration]);
        restored[descriptor.iteration] = valueFor(descriptor.value) ^
            location.wid;
        restoredValid[descriptor.iteration] = true;
        assert(merge.recordRetirement(location.line) == Result::Accepted);
        assert(merge.consumeHead(selected) == Result::Accepted);
        consumed++;
    }
    if (lineActive)
        assert(merge.endSourceLine() == Result::Accepted);
    assert(consumed == Logical);
    assert(merge.readRecords() == Logical);
    assert(merge.readLines() == 12);
    assert(merge.headHighWater() == 4);
    assert(merge.maxReaderCarryBytes() == 4);
    assert(merge.sourceLineIssues() == 7);
    assert(merge.coalescedDescriptors() == Logical - 7);
    assert(merge.retiredDescriptors() == Logical);
    for (uint32_t itr = 0; itr < Logical; ++itr) {
        assert(restoredValid[itr]);
        assert(restored[itr] ==
               (valueFor(0x81000000U + itr) ^ locations[itr].wid));
    }
    assert(merge.finishMerge() == Result::Accepted);
}

void
testFailClosedBoundsAndProtocol()
{
    BoundedFourRunMerge merge;
    const std::array<uint32_t, 4> valid{4, 4, 4, 4};
    assert(merge.configure(16, valid, 3,
                           BoundedFourRunMerge::RequiredBackingBytes) ==
           Result::InvalidConfiguration);
    assert(merge.configure(16, valid, 0x800000,
                           BoundedFourRunMerge::RequiredBackingBytes - 64) ==
           Result::InvalidConfiguration);
    const std::array<uint32_t, 4> oversized{4097, 4095, 4096, 4096};
    assert(merge.configure(16384, oversized, 0x800000,
                           BoundedFourRunMerge::RequiredBackingBytes) ==
           Result::InvalidConfiguration);
    assert(merge.configure(16, valid, 0x800000,
                           BoundedFourRunMerge::RequiredBackingBytes) ==
           Result::Accepted);
    assert(merge.beginMerge() == Result::NotReady);
    assert(merge.beginMaterialization(4) == Result::RunOutOfRange);
    assert(merge.beginMaterialization(0) == Result::Accepted);
    assert(merge.finishMaterialization(0) ==
           Result::MaterializationIncomplete);
}

} // anonymous namespace

int
main()
{
    testFiniteRunsCarryOrderingCoalescingAndRestoration();
    testFailClosedBoundsAndProtocol();
    std::cout << "bounded_four_run_merge_test: PASS\n";
    return 0;
}
