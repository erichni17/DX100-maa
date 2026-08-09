#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <vector>

#include "mem/MAA/BoundedDescriptorSpool.hh"

using gem5::BoundedDescriptorSpool;

namespace
{

using Result = BoundedDescriptorSpool::Result;
using Descriptor = BoundedDescriptorSpool::Descriptor;

void
testBalancedLayoutAndExactClosure()
{
    BoundedDescriptorSpool spool;
    constexpr uint64_t base = 0x100000;
    const std::array<uint32_t, 4> populations{4096, 4096, 4096, 4096};
    assert(spool.configure(
               16384, populations.size(),
               [&](uint32_t pass) { return populations[pass]; },
               base, 4 * 4096 * sizeof(Descriptor) +
                         BoundedDescriptorSpool::MaxPasses *
                             BoundedDescriptorSpool::LineBytes) ==
           Result::Accepted);
    assert(spool.requiredBackingBytes() == 131072);
    assert(spool.reservedBackingBytes() == 131328);
    assert(spool.activeStagingDescriptorCapacity() == 32);
    assert(spool.chargedControlBytes() < 4096);

    std::vector<uint64_t> pending;
    uint32_t iteration = 0;
    for (uint32_t pass = 0; pass < populations.size(); ++pass) {
        for (uint32_t index = 0; index < populations[pass]; ++index) {
            const Descriptor descriptor{
                static_cast<uint16_t>(iteration++),
                static_cast<uint16_t>(pass), index % 7};
            assert(spool.stage(pass, descriptor) == Result::Accepted);
            if (!spool.lineReady(pass, false))
                continue;
            uint64_t address = 0;
            uint32_t descriptors = 0;
            std::array<uint8_t, BoundedDescriptorSpool::LineBytes> data{};
            Result result = spool.issueStagedLine(
                pass, false, address, data, descriptors);
            if (result == Result::NoWriteCredit) {
                assert(!pending.empty());
                assert(spool.acknowledgeWrite(pending.front()) ==
                       Result::Accepted);
                pending.erase(pending.begin());
                result = spool.issueStagedLine(
                    pass, false, address, data, descriptors);
            }
            assert(result == Result::Accepted);
            assert(descriptors == 8);
            for (uint32_t word = 0; word < descriptors; ++word) {
                Descriptor materialized;
                std::memcpy(
                    &materialized,
                    data.data() + word *
                        BoundedDescriptorSpool::DescriptorBytes,
                    sizeof(materialized));
                const uint32_t expected_iteration =
                    iteration - descriptors + word;
                assert(materialized.iteration == expected_iteration);
                assert(materialized.sourcePage == pass);
                assert(materialized.value ==
                       (index - descriptors + 1 + word) % 7);
            }
            pending.push_back(address);
        }
    }
    while (!pending.empty()) {
        assert(spool.acknowledgeWrite(pending.back()) == Result::Accepted);
        pending.pop_back();
    }
    assert(spool.finishBucketing() == Result::Accepted);
    assert(spool.writeLinesIssued() == 2048);
    assert(spool.writeAcks() == 2048);
    assert(spool.descriptorsWritten() == 16384);
    assert(spool.outstandingWriteHighWater() ==
           BoundedDescriptorSpool::MaxOutstandingWrites);

    iteration = 0;
    for (uint32_t pass = 0; pass < populations.size(); ++pass) {
        assert(spool.beginReplay(pass) == Result::Accepted);
        for (uint32_t line = 0; line < spool.passLines(pass); ++line) {
            assert(spool.recordReadIssue(pass, line) == Result::Accepted);
            assert(spool.recordReadResponse(pass) == Result::Accepted);
            for (uint32_t word = 0; word < spool.descriptorsInLine(pass, line);
                 ++word) {
                assert(spool.recordConsumption(
                           pass,
                           Descriptor{static_cast<uint16_t>(iteration++),
                                      static_cast<uint16_t>(pass), word}) ==
                       Result::Accepted);
            }
        }
        assert(spool.finishReplay(pass) == Result::Accepted);
    }
    assert(spool.readLinesIssued() == 2048);
    assert(spool.readLineResponses() == 2048);
    assert(spool.descriptorsConsumed() == 16384);
}

void
testPartialLinesAndPaddingAreCharged()
{
    BoundedDescriptorSpool spool;
    const std::array<uint32_t, 3> populations{1, 8, 9};
    assert(spool.configure(
               18, populations.size(),
               [&](uint32_t pass) { return populations[pass]; },
               0x200000, 256) == Result::Accepted);
    assert(spool.requiredBackingBytes() == 256);
    assert(spool.passBase(0) == 0x200000);
    assert(spool.passBase(1) == 0x200040);
    assert(spool.passBase(2) == 0x200080);
    assert(spool.passLines(2) == 2);
    assert(spool.descriptorsInLine(2, 1) == 1);

    std::vector<uint64_t> writes;
    uint32_t itr = 0;
    for (uint32_t pass = 0; pass < populations.size(); ++pass) {
        for (uint32_t count = 0; count < populations[pass]; ++count) {
            assert(spool.stage(
                       pass,
                       Descriptor{static_cast<uint16_t>(itr++),
                                  static_cast<uint16_t>(pass), count}) ==
                   Result::Accepted);
            if (spool.lineReady(pass, false)) {
                uint64_t address;
                uint32_t descriptors;
                std::array<uint8_t, 64> data;
                assert(spool.issueStagedLine(
                           pass, false, address, data, descriptors) ==
                       Result::Accepted);
                writes.push_back(address);
            }
        }
        if (spool.lineReady(pass, true)) {
            uint64_t address;
            uint32_t descriptors;
            std::array<uint8_t, 64> data;
            assert(spool.issueStagedLine(
                       pass, true, address, data, descriptors) ==
                   Result::Accepted);
            writes.push_back(address);
        }
    }
    assert(spool.finishBucketing() == Result::BucketingIncomplete);
    for (uint64_t address : writes)
        assert(spool.acknowledgeWrite(address) == Result::Accepted);
    assert(spool.finishBucketing() == Result::Accepted);
}

void
testFailClosedAndRetryStable()
{
    BoundedDescriptorSpool spool;
    assert(spool.configure(65537, 1, [](uint32_t) { return 65537; },
                           0x300000, 1024 * 1024) ==
           Result::InvalidConfiguration);
    assert(spool.configure(4, 1, [](uint32_t) { return 4; },
                           3, 64) == Result::InvalidConfiguration);
    assert(spool.configure(4, 1, [](uint32_t) { return 4; },
                           0x300000, 63) == Result::InvalidConfiguration);
    assert(spool.configure(4, 1, [](uint32_t) { return 3; },
                           0x300000, 64) == Result::InvalidConfiguration);
    assert(spool.configure(4, 1, [](uint32_t) { return 4; },
                           0x300000, 64) == Result::Accepted);
    assert(spool.beginReplay(0) == Result::NotReady);
    for (uint32_t itr = 0; itr < 4; ++itr)
        assert(spool.stage(0, Descriptor{static_cast<uint16_t>(itr), 0,
                                         itr + 1}) ==
               Result::Accepted);
    assert(spool.stage(0, Descriptor{}) == Result::PassOverflow);

    uint64_t address;
    uint32_t descriptors;
    std::array<uint8_t, 64> data;
    assert(spool.issueStagedLine(0, true, address, data, descriptors) ==
           Result::Accepted);
    assert(spool.acknowledgeWrite(address + 64) == Result::UnknownWriteAck);
    assert(spool.acknowledgeWrite(address) == Result::Accepted);
    assert(spool.acknowledgeWrite(address) == Result::DuplicateWriteAck);
    assert(spool.finishBucketing() == Result::Accepted);
    assert(spool.beginReplay(0) == Result::Accepted);
    assert(spool.beginReplay(0) == Result::ReplayAlreadyActive);
    assert(spool.recordReadIssue(0, 1) == Result::ReplayOverflow);
    assert(spool.recordReadIssue(0, 0) == Result::Accepted);
    assert(spool.recordReadResponse(0) == Result::Accepted);
    assert(spool.finishReplay(0) == Result::ReplayIncomplete);
    for (uint32_t itr = 0; itr < 4; ++itr)
        assert(spool.recordConsumption(
                   0, Descriptor{static_cast<uint16_t>(itr), 0, itr}) ==
               Result::Accepted);
    assert(spool.recordConsumption(0, Descriptor{0, 0, 0}) ==
           Result::ReplayOverflow);
    assert(spool.finishReplay(0) == Result::Accepted);
}

} // anonymous namespace

int
main()
{
    testBalancedLayoutAndExactClosure();
    testPartialLinesAndPaddingAreCharged();
    testFailClosedAndRetryStable();
    std::cout << "bounded_descriptor_spool_test: PASS\n";
    return 0;
}
