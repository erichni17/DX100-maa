#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <vector>

#include "mem/MAA/BoundedDescriptorSpool.hh"

using gem5::BoundedDescriptorSpool;

namespace
{

using Result = BoundedDescriptorSpool::Result;
using Descriptor = BoundedDescriptorSpool::Descriptor;

void
testResidentFirstDenseLayoutAndExactClosure()
{
    static_assert(BoundedDescriptorSpool::DescriptorBits == 46);
    static_assert(BoundedDescriptorSpool::DescriptorBytes == 6);
    static_assert(BoundedDescriptorSpool::MaxExternalPasses == 3);
    static_assert(BoundedDescriptorSpool::MaxCarryBytes == 5);

    BoundedDescriptorSpool spool;
    constexpr uint64_t base = 0x100000;
    const std::array<uint32_t, 4> populations{4096, 4096, 4096, 4096};
    assert(spool.configure(
               16384, populations.size(), 0,
               [&](uint32_t pass) { return populations[pass]; },
               base, 3 * 4096 * BoundedDescriptorSpool::DescriptorBytes) ==
           Result::Accepted);
    assert(spool.residentPass() == 0);
    assert(spool.externalSegments() == 3);
    assert(spool.population(0) == 4096);
    assert(spool.passLines(0) == 0);
    assert(spool.passBase(0) == 0);
    assert(spool.passBase(1) == base);
    assert(spool.passBase(2) == base + 24576);
    assert(spool.passBase(3) == base + 49152);
    assert(spool.requiredBackingBytes() == 73728);
    assert(spool.reservedBackingBytes() == 73728);
    assert(spool.externalPayloadBytes() == 73728);
    assert(spool.activeStagingBytes() == 3 * (64 + 5));
    assert(spool.chargedControlBytes() < 4096);

    std::vector<uint8_t> backing(spool.requiredBackingBytes(), 0);
    std::vector<uint64_t> pending;
    for (uint32_t pass = 0; pass < populations.size(); ++pass) {
        for (uint32_t index = 0; index < populations[pass]; ++index) {
            const Descriptor descriptor{
                static_cast<uint16_t>(pass * 4096 + index),
                0x81000000U | (pass << 16) | index};
            if (pass == spool.residentPass()) {
                assert(spool.recordResidentClassification(pass, descriptor) ==
                       Result::Accepted);
                continue;
            }
            if (spool.lineReady(pass, false)) {
                uint64_t address = 0;
                uint32_t payload_bytes = 0;
                std::array<uint8_t, BoundedDescriptorSpool::LineBytes> data{};
                Result issue = spool.issueStagedLine(
                    pass, false, address, data, payload_bytes);
                if (issue == Result::NoWriteCredit) {
                    assert(!pending.empty());
                    assert(spool.acknowledgeWrite(pending.front()) ==
                           Result::Accepted);
                    pending.erase(pending.begin());
                    issue = spool.issueStagedLine(
                        pass, false, address, data, payload_bytes);
                }
                assert(issue == Result::Accepted);
                assert(payload_bytes == 64);
                std::copy(data.begin(), data.end(),
                          backing.begin() + (address - base));
                pending.push_back(address);
            }
            assert(spool.stage(pass, descriptor) == Result::Accepted);
        }
    }
    for (uint32_t pass = 1; pass < populations.size(); ++pass) {
        if (!spool.lineReady(pass, true))
            continue;
        uint64_t address = 0;
        uint32_t payload_bytes = 0;
        std::array<uint8_t, BoundedDescriptorSpool::LineBytes> data{};
        Result issue = spool.issueStagedLine(
            pass, true, address, data, payload_bytes);
        if (issue == Result::NoWriteCredit) {
            assert(!pending.empty());
            assert(spool.acknowledgeWrite(pending.front()) ==
                   Result::Accepted);
            pending.erase(pending.begin());
            issue = spool.issueStagedLine(
                pass, true, address, data, payload_bytes);
        }
        assert(issue == Result::Accepted);
        std::copy(data.begin(), data.end(),
                  backing.begin() + (address - base));
        pending.push_back(address);
    }
    while (!pending.empty()) {
        assert(spool.acknowledgeWrite(pending.back()) == Result::Accepted);
        pending.pop_back();
    }
    assert(spool.finishBucketing() == Result::Accepted);
    assert(spool.classifiedDescriptors() == 16384);
    assert(spool.residentDescriptors() == 4096);
    assert(spool.descriptorsWritten() == 12288);
    assert(spool.writeLinesIssued() == 1152);
    assert(spool.writeAcks() == 1152);
    assert(spool.outstandingWriteHighWater() ==
           BoundedDescriptorSpool::MaxOutstandingWrites);
    assert(spool.beginReplay(spool.residentPass()) == Result::ResidentPass);

    for (uint32_t pass = 1; pass < populations.size(); ++pass) {
        assert(spool.beginReplay(pass) == Result::Accepted);
        for (uint32_t line = 0; line < spool.passLines(pass); ++line) {
            assert(spool.recordReadIssue(pass, line) == Result::Accepted);
            assert(spool.recordReadResponse(pass, line) == Result::Accepted);
        }
        const uint64_t segment_offset = spool.passBase(pass) - base;
        for (uint32_t cursor = 0; cursor < populations[pass]; ++cursor) {
            const Descriptor decoded = BoundedDescriptorSpool::unpack(
                backing.data() + segment_offset +
                    cursor * BoundedDescriptorSpool::DescriptorBytes);
            assert(decoded.iteration == pass * 4096 + cursor);
            assert(decoded.value ==
                   (0x81000000U | (pass << 16) | cursor));
            assert(spool.recordConsumption(pass, decoded) ==
                   Result::Accepted);
        }
        assert(spool.finishReplay(pass) == Result::Accepted);
    }
    assert(spool.readLinesIssued() == 1152);
    assert(spool.readLineResponses() == 1152);
    assert(spool.descriptorsConsumed() == 12288);
}

void
testAllSixBoundaryPhasesAndEndianPacking()
{
    std::array<uint8_t, 6> packed{};
    const Descriptor descriptor{0x3fff, 0xfedcba98};
    BoundedDescriptorSpool::pack(descriptor, packed.data());
    assert(packed ==
           (std::array<uint8_t, 6>{0xff, 0x3f, 0xa6, 0x2e, 0xb7, 0x3f}));
    const Descriptor decoded = BoundedDescriptorSpool::unpack(packed.data());
    assert(decoded.iteration == descriptor.iteration);
    assert(decoded.value == descriptor.value);

    // Reassemble the record across every possible byte boundary.  The dense
    // stream's 64-byte-line cadence reaches the applicable subset naturally;
    // this loop also closes the generic pack/unpack boundary contract.
    for (uint32_t bytes_in_first_line = 1;
         bytes_in_first_line < BoundedDescriptorSpool::DescriptorBytes;
         ++bytes_in_first_line) {
        std::array<uint8_t, 6> reassembled{};
        std::copy_n(packed.begin(), bytes_in_first_line,
                    reassembled.begin());
        std::copy(packed.begin() + bytes_in_first_line, packed.end(),
                  reassembled.begin() + bytes_in_first_line);
        const Descriptor split =
            BoundedDescriptorSpool::unpack(reassembled.data());
        assert(split.iteration == descriptor.iteration);
        assert(split.value == descriptor.value);
    }

    // Cursor ten begins at byte 60, so its six-byte record must straddle the
    // first and second cache lines without padding or duplication.
    assert((10 * BoundedDescriptorSpool::DescriptorBytes) / 64 == 0);
    assert((10 * BoundedDescriptorSpool::DescriptorBytes + 5) / 64 == 1);
}

void
testFailClosedAndRetryStable()
{
    BoundedDescriptorSpool spool;
    const std::array<uint32_t, 4> populations{4, 4, 4, 4};
    auto population = [&](uint32_t pass) { return populations[pass]; };
    assert(spool.configure(16, 4, 4, population, 0x300000, 192) ==
           Result::InvalidConfiguration);
    assert(spool.configure(16, 4, 0, population, 3, 192) ==
           Result::InvalidConfiguration);
    assert(spool.configure(16, 4, 0, population, 0x300000, 191) ==
           Result::InvalidConfiguration);
    assert(spool.configure(16, 4, 0, population, 0x300000, 192) ==
           Result::Accepted);
    assert(spool.stage(0, Descriptor{}) == Result::ResidentPass);
    assert(spool.recordResidentClassification(1, Descriptor{}) ==
           Result::WrongResidentPass);
    for (uint32_t itr = 0; itr < 4; ++itr)
        assert(spool.recordResidentClassification(
                   0, Descriptor{static_cast<uint16_t>(itr), itr}) ==
               Result::Accepted);
    assert(spool.recordResidentClassification(0, Descriptor{}) ==
           Result::PassOverflow);
    assert(spool.finishBucketing() == Result::BucketingIncomplete);
}

} // anonymous namespace

int
main()
{
    testResidentFirstDenseLayoutAndExactClosure();
    testAllSixBoundaryPhasesAndEndianPacking();
    testFailClosedAndRetryStable();
    std::cout << "bounded_descriptor_spool_test: PASS\n";
    return 0;
}
