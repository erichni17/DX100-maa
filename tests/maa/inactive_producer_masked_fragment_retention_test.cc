#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>

#include "mem/MAA/InactiveProducerMaskedFragmentRetention.hh"

using gem5::InactiveProducerMaskedFragmentRetention;

namespace {

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;        \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

using Retention = InactiveProducerMaskedFragmentRetention;

Retention::Key
key(uint16_t token, uint64_t generation, uint64_t incarnation,
    uint64_t backing)
{
    return {token, generation, incarnation, backing};
}

std::array<std::byte, Retention::LineBytes>
payload(uint64_t seed)
{
    std::array<std::byte, Retention::LineBytes> result{};
    for (uint8_t word = 0; word < Retention::LineBytes / sizeof(uint64_t);
         ++word) {
        const uint64_t value = seed + word;
        std::memcpy(result.data() + word * sizeof(value), &value,
                    sizeof(value));
    }
    return result;
}

uint64_t
word(const std::byte *data, uint8_t index)
{
    uint64_t result = 0;
    std::memcpy(&result, data + index * sizeof(result), sizeof(result));
    return result;
}

std::array<std::byte, Retention::LineBytes>
payload32(uint32_t seed)
{
    std::array<std::byte, Retention::LineBytes> result{};
    for (uint8_t word = 0; word < Retention::LineBytes / sizeof(uint32_t);
         ++word) {
        const uint32_t value = seed + word;
        std::memcpy(result.data() + word * sizeof(value), &value,
                    sizeof(value));
    }
    return result;
}

uint32_t
word32(const std::byte *data, uint8_t index)
{
    uint32_t result = 0;
    std::memcpy(&result, data + index * sizeof(result), sizeof(result));
    return result;
}

void
testDefaultOffCapacityAndHardwareMath()
{
    Retention retention;
    const auto owner = key(3, 11, 1, 0x100000);
    const auto bytes = payload(0x100);
    CHECK(retention.begin(owner, 1024, 0) ==
          Retention::BeginResult::Disabled);
    CHECK(retention.capture(owner, 0, 1, 0x0f, 8, bytes.data(),
                            bytes.size(), 0) ==
          Retention::CaptureResult::Disabled);
    CHECK(!Retention::validCapacity(256));
    CHECK(Retention::validCapacity(0));
    constexpr std::array<uint16_t, 4> capacities{{512, 1024, 2048, 4096}};
    constexpr std::array<std::size_t, 4> combinedBits{{
        436840, 855148, 1691760, 3364980}};
    constexpr std::array<std::size_t, 4> combinedBytes{{
        54605, 106894, 211470, 420623}};
    for (std::size_t index = 0; index < capacities.size(); ++index) {
        const uint16_t capacity = capacities[index];
        CHECK(Retention::validCapacity(capacity));
        CHECK(Retention::entriesPerPartition(capacity) == capacity / 4);
        CHECK(Retention::entriesPerBankPerPartition(capacity) ==
              capacity / 16);
        CHECK(Retention::provisionedPoisonBits(capacity) ==
              4 * Retention::MaxLogicalLines);
        CHECK(Retention::provisionedTotalBits(capacity) ==
              Retention::provisionedPayloadBits(capacity) +
                  Retention::LineBytes * 8 +
                  Retention::provisionedControlBits(capacity));
        CHECK(Retention::provisionedCombinedTotalBits(capacity, 32) ==
              Retention::provisionedTotalBits(capacity) +
                  Retention::MAALookupControlBits + 32 * 64);
        CHECK(Retention::MAALookupControlBits == 1772);
        CHECK(Retention::provisionedCombinedTotalBits(capacity, 32) ==
              combinedBits[index]);
        CHECK(Retention::provisionedCombinedTotalBytes(capacity, 32) ==
              combinedBytes[index]);
    }
}

void
testTokenPartitionHashSpreadsRealAllocationPatterns()
{
    // GZP allocates six consecutive int tiles per worker and uses tiles3, the
    // fourth allocation, as its virtual completion token: token = 3 + 6k.
    // Plain token[1:0] aliases 3 with 15 and 9 with 21.
    constexpr std::array<uint16_t, 4> gzpTokens{{3, 9, 15, 21}};
    constexpr std::array<uint8_t, 4> expectedPartitions{{2, 1, 0, 3}};
    CHECK((gzpTokens[0] & 3) == (gzpTokens[2] & 3));
    CHECK((gzpTokens[1] & 3) == (gzpTokens[3] & 3));

    Retention retention;
    for (std::size_t index = 0; index < gzpTokens.size(); ++index) {
        const uint16_t token = gzpTokens[index];
        CHECK(Retention::descriptorIndexForToken(token) ==
              expectedPartitions[index]);
        CHECK(retention.begin(
                  key(token, 12 + index, 2 + index,
                      0x110000 + index * 0x10000),
                  1024, 512) == Retention::BeginResult::Started);
    }
    CHECK(retention.counters().descriptorFailures == 0);

    // The same combinational selector stays general: every aligned group of
    // eight consecutive tokens and every eight-token 3+6k sequence places
    // exactly two tokens in each of the four partitions.
    std::array<uint8_t, Retention::PartitionCount> consecutiveCounts{};
    std::array<uint8_t, Retention::PartitionCount> strideCounts{};
    for (uint16_t index = 0; index < 8; ++index) {
        ++consecutiveCounts[
            Retention::descriptorIndexForToken(index)];
        ++strideCounts[
            Retention::descriptorIndexForToken(3 + 6 * index)];
    }
    for (uint8_t partition = 0;
         partition < Retention::PartitionCount; ++partition) {
        CHECK(consecutiveCounts[partition] == 2);
        CHECK(strideCounts[partition] == 2);
    }
    CHECK(retention.assertInvariants());
}

void
testFragmentMergeCompleteAndSealGate()
{
    Retention retention;
    const auto owner = key(7, 21, 2, 0x200000);
    const auto low = payload(0x200);
    const auto high = payload(0x300);
    CHECK(retention.begin(owner, 1024, 4096) ==
          Retention::BeginResult::Started);
    CHECK(retention.capture(owner, 17, 101, 0x0f, 8, low.data(),
                            low.size(), 10) ==
          Retention::CaptureResult::Accepted);
    CHECK(retention.capture(owner, 17, 102, 0xf0, 8, high.data(),
                            high.size(), 11) ==
          Retention::CaptureResult::Reconstructed);

    Retention::Line line;
    CHECK(retention.probe(owner, 17, 8, 12, &line) ==
          Retention::ProbeResult::Miss);
    CHECK(retention.sealPage(owner, 0) == Retention::SealResult::Sealed);
    CHECK(retention.probe(owner, 17, 8, 13, &line) ==
          Retention::ProbeResult::Hit);
    CHECK(line.transactionID == 102);
    for (uint8_t index = 0; index < 4; ++index)
        CHECK(word(line.payload, index) == uint64_t{0x200} + index);
    for (uint8_t index = 4; index < 8; ++index)
        CHECK(word(line.payload, index) == uint64_t{0x300} + index);
    CHECK(retention.take(owner, 17, 102, 14));
    const auto stats = retention.counters();
    CHECK(stats.fragmentsAccepted == 2);
    CHECK(stats.wordsMerged == 8);
    CHECK(stats.reconstructedLines == 1);
    CHECK(stats.replayHits == 1);
    CHECK(stats.replayMisses == 1);
    CHECK(retention.assertInvariants());
}

void
testSixteenWordLineReconstruction()
{
    Retention retention;
    const auto owner = key(6, 25, 12, 0x280000);
    const auto low = payload32(0x1000);
    const auto high = payload32(0x2000);
    CHECK(retention.begin(owner, 1024, 4096) ==
          Retention::BeginResult::Started);
    CHECK(retention.capture(owner, 31, 151, 0x00ff, 4, low.data(),
                            low.size(), 15) ==
          Retention::CaptureResult::Accepted);
    CHECK(retention.capture(owner, 31, 152, 0xff00, 4, high.data(),
                            high.size(), 16) ==
          Retention::CaptureResult::Reconstructed);
    CHECK(retention.sealPage(owner, 0) == Retention::SealResult::Sealed);
    Retention::Line line;
    CHECK(retention.probe(owner, 31, 4, 17, &line) ==
          Retention::ProbeResult::Hit);
    for (uint8_t index = 0; index < 8; ++index)
        CHECK(word32(line.payload, index) == uint32_t{0x1000} + index);
    for (uint8_t index = 8; index < 16; ++index)
        CHECK(word32(line.payload, index) == uint32_t{0x2000} + index);
    CHECK(retention.take(owner, 31, 152, 18));
    CHECK(retention.assertInvariants());
}

void
testOverlapAndFirstOwnerConflictPoison()
{
    Retention retention;
    const auto owner = key(1, 31, 3, 0x300000);
    const auto bytes = payload(0x400);
    CHECK(retention.begin(owner, 1024, 512) ==
          Retention::BeginResult::Started);
    CHECK(retention.capture(owner, 1, 201, 0x03, 8, bytes.data(),
                            bytes.size(), 20) ==
          Retention::CaptureResult::Accepted);
    CHECK(retention.capture(owner, 1, 202, 0x02, 8, bytes.data(),
                            bytes.size(), 21) ==
          Retention::CaptureResult::OverlapPoison);
    CHECK(retention.poisoned(owner, 1));
    CHECK(retention.capture(owner, 1, 203, 0xfc, 8, bytes.data(),
                            bytes.size(), 22) ==
          Retention::CaptureResult::AlreadyPoisoned);

    const uint16_t colliding = 5 +
        4 * Retention::entriesPerBankPerPartition(512);
    CHECK(retention.capture(owner, 5, 204, 0x0f, 8, bytes.data(),
                            bytes.size(), 23) ==
          Retention::CaptureResult::Accepted);
    CHECK(retention.selectedEntry(owner, 5) ==
          retention.selectedEntry(owner, colliding));
    CHECK(retention.capture(owner, colliding, 205, 0x0f, 8, bytes.data(),
                            bytes.size(), 24) ==
          Retention::CaptureResult::ConflictPoison);
    CHECK(retention.poisoned(owner, colliding));
    CHECK(!retention.poisoned(owner, 5));
    const auto stats = retention.counters();
    CHECK(stats.overlapPoisons == 1);
    CHECK(stats.tagConflicts == 1);
    CHECK(retention.assertInvariants());
}

void
testFourBanksAndDroppedFragmentPoison()
{
    Retention retention;
    const auto owner = key(2, 41, 4, 0x400000);
    const auto bytes = payload(0x500);
    CHECK(retention.begin(owner, 1024, 4096) ==
          Retention::BeginResult::Started);
    for (uint16_t line = 0; line < 4; ++line) {
        CHECK(retention.capture(owner, line,
                                static_cast<uint64_t>(300 + line), 0x01, 8,
                                bytes.data(), bytes.size(), 30) ==
              Retention::CaptureResult::Accepted);
    }
    // line 4 shares bank zero with line 0. Losing this fragment permanently
    // poisons line 4; later words cannot manufacture a complete line.
    CHECK(retention.capture(owner, 4, 304, 0x01, 8, bytes.data(),
                            bytes.size(), 30) ==
          Retention::CaptureResult::WritePortPoison);
    CHECK(retention.capture(owner, 4, 305, 0xfe, 8, bytes.data(),
                            bytes.size(), 31) ==
          Retention::CaptureResult::AlreadyPoisoned);
    CHECK(retention.poisoned(owner, 4));
    CHECK(retention.counters().writePortPoisons == 1);
    CHECK(retention.assertInvariants());
}

void
testStaleDescriptorAndInvalidEpochPoison()
{
    Retention retention;
    const auto oldOwner = key(0, 51, 5, 0x500000);
    // Tokens 0 and 7 have the same low Gray-code partition.
    const auto newOwner = key(7, 61, 6, 0x600000);
    const auto bytes = payload(0x600);
    CHECK(retention.begin(oldOwner, 1024, 4096) ==
          Retention::BeginResult::Started);
    CHECK(retention.capture(oldOwner, 8, 401, 0x0f, 8, bytes.data(),
                            bytes.size(), 40) ==
          Retention::CaptureResult::Accepted);
    uint16_t discarded = 0;
    CHECK(retention.begin(newOwner, 1024, 4096, &discarded) ==
          Retention::BeginResult::Replaced);
    CHECK(discarded == 1);
    CHECK(retention.capture(oldOwner, 8, 402, 0xf0, 8, bytes.data(),
                            bytes.size(), 41) ==
          Retention::CaptureResult::StalePoison);
    CHECK(retention.poisoned(newOwner, 8));
    CHECK(retention.capture(newOwner, 9, 0, 0x0f, 8, bytes.data(),
                            bytes.size(), 42) ==
          Retention::CaptureResult::InvalidPoison);
    CHECK(retention.poisoned(newOwner, 9));
    const auto invalidEpoch =
        key(newOwner.tokenTile, 0, newOwner.incarnation,
            newOwner.backingAddress);
    CHECK(retention.capture(invalidEpoch, 10, 403, 0x0f, 8, bytes.data(),
                            bytes.size(), 43) ==
          Retention::CaptureResult::InvalidPoison);
    CHECK(retention.poisoned(newOwner, 10));
    CHECK(retention.counters().descriptorFailures == 1);
    CHECK(retention.counters().staleUntrackedDrops == 2);
    CHECK(retention.counters().invalidPoisons == 2);
    CHECK(retention.assertInvariants());
}

void
testOneCycleReadAndOutputLatchRaces()
{
    Retention retention;
    const auto owner = key(3, 71, 7, 0x700000);
    // Tokens 3 and 4 have the same low Gray-code partition.
    const auto replacement = key(4, 81, 8, 0x800000);
    const auto bytes = payload(0x700);
    CHECK(retention.begin(owner, 1024, 4096) ==
          Retention::BeginResult::Started);
    CHECK(retention.capture(owner, 12, 501, 0xff, 8, bytes.data(),
                            bytes.size(), 50) ==
          Retention::CaptureResult::Reconstructed);
    CHECK(retention.sealPage(owner, 0) == Retention::SealResult::Sealed);

    // A same-cycle read observes the pre-write RAM value and completes as a
    // miss only on N+1.
    Retention::Line line;
    CHECK(retention.probe(owner, 12, 8, 50, &line) ==
          Retention::ProbeResult::Miss);
    Retention::LookupPipeline miss;
    CHECK(miss.arm(50, Retention::ProbeResult::Miss));
    CHECK(!miss.ready(50));
    CHECK(miss.ready(51));
    CHECK(retention.probe(owner, 12, 8, 51, &line) ==
          Retention::ProbeResult::Hit);
    Retention::LookupPipeline hit;
    CHECK(hit.arm(51, Retention::ProbeResult::Hit));
    CHECK(!hit.ready(51));
    CHECK(hit.ready(52));
    CHECK(retention.probe(owner, 13, 8, 52, &line) ==
          Retention::ProbeResult::PortBusy);

    // Descriptor replacement and clear cannot invalidate the authenticated
    // output register selected by the preceding read.
    CHECK(retention.begin(replacement, 1024, 4096) ==
          Retention::BeginResult::Replaced);
    CHECK(!retention.clear(owner));
    CHECK(retention.take(owner, 12, 501, 53));
    CHECK(word(bytes.data(), 0) == 0x700);
    CHECK(retention.assertInvariants());
}

void
testClearClosureAndLatchedSurvival()
{
    Retention retention;
    const auto owner = key(5, 91, 9, 0x900000);
    const auto bytes = payload(0x800);
    CHECK(retention.begin(owner, 1024, 1024) ==
          Retention::BeginResult::Started);
    CHECK(retention.capture(owner, 2, 601, 0xff, 8, bytes.data(),
                            bytes.size(), 60) ==
          Retention::CaptureResult::Reconstructed);
    CHECK(retention.capture(owner, 3, 602, 0x01, 8, bytes.data(),
                            bytes.size(), 60) ==
          Retention::CaptureResult::Accepted);
    CHECK(retention.sealPage(owner, 0) == Retention::SealResult::Sealed);
    Retention::Line line;
    CHECK(retention.probe(owner, 2, 8, 61, &line) ==
          Retention::ProbeResult::Hit);
    const auto closed = retention.clear(owner);
    CHECK(closed);
    CHECK(closed.discardedEntries == 1);
    CHECK(closed.survivingLatchedLines == 1);
    CHECK(retention.counters().occupancy == 0);
    CHECK(retention.take(owner, 2, 601, 62));
    CHECK(retention.counters().clears == 1);
    CHECK(retention.assertInvariants());
}

} // anonymous namespace

int
main()
{
    testDefaultOffCapacityAndHardwareMath();
    testTokenPartitionHashSpreadsRealAllocationPatterns();
    testFragmentMergeCompleteAndSealGate();
    testSixteenWordLineReconstruction();
    testOverlapAndFirstOwnerConflictPoison();
    testFourBanksAndDroppedFragmentPoison();
    testStaleDescriptorAndInvalidEpochPoison();
    testOneCycleReadAndOutputLatchRaces();
    testClearClosureAndLatchedSurvival();
    std::cout << "inactive masked fragment retention tests passed\n";
    return 0;
}
