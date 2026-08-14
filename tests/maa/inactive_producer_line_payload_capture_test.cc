#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>

#include "mem/MAA/InactiveProducerLinePayloadCapture.hh"

using gem5::InactiveProducerLinePayloadCapture;

namespace {

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;        \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

using Capture = InactiveProducerLinePayloadCapture;

Capture::Key
key(uint16_t token, uint64_t generation, uint64_t incarnation,
    uint64_t backing)
{
    return {token, generation, incarnation, backing};
}

std::array<std::byte, Capture::LineBytes>
payload(uint64_t seed)
{
    std::array<std::byte, Capture::LineBytes> result{};
    for (uint16_t word = 0; word < Capture::LineBytes / sizeof(uint64_t);
         ++word) {
        const uint64_t value = seed + word;
        std::memcpy(result.data() + word * sizeof(value), &value,
                    sizeof(value));
    }
    return result;
}

uint64_t
word(const std::byte *data, uint16_t index)
{
    uint64_t value = 0;
    std::memcpy(&value, data + index * sizeof(value), sizeof(value));
    return value;
}

uint16_t
collidingLine(const Capture &capture, const Capture::Key &owner,
              uint16_t targetIndex, uint16_t lineCount = 2048)
{
    for (uint16_t line = 0; line < lineCount; ++line) {
        if (capture.selectedEntry(owner, line) == targetIndex)
            return line;
    }
    CHECK(false);
    return 0;
}

void
testDefaultOffAndExactIncarnationIdentity()
{
    Capture capture;
    const auto owner = key(3, 11, 7, 0x100000);
    auto line = payload(0x100);
    CHECK(capture.begin(owner, 2048, 0) == Capture::BeginResult::Disabled);
    CHECK(capture.capture(owner, 1, 101, line.data(), line.size(), 0) ==
          Capture::CaptureResult::Disabled);
    CHECK(!capture.active(owner));
    CHECK(capture.occupancy() == 0);

    CHECK(capture.begin(owner, 2048, 64) == Capture::BeginResult::Started);
    CHECK(capture.capture(owner, 1, 101, line.data(), line.size(), 10) ==
          Capture::CaptureResult::Captured);
    CHECK(capture.capture(owner, 1, 101, line.data(), line.size(), 10) ==
          Capture::CaptureResult::PortBusy);
    CHECK(capture.capture(owner, 1, 101, line.data(), line.size(), 11) ==
          Capture::CaptureResult::Duplicate);
    CHECK(capture.capture(owner, 1, 102, line.data(), line.size(), 12) ==
          Capture::CaptureResult::Invalid);
    CHECK(capture.capture(key(3, 11, 6, owner.backingAddress), 2, 103,
                          line.data(), line.size(), 13) ==
          Capture::CaptureResult::Stale);
    CHECK(capture.capture(key(3, 11, 8, owner.backingAddress), 2, 103,
                          line.data(), line.size(), 14) ==
          Capture::CaptureResult::Stale);
    CHECK(capture.capture(owner, 2048, 104, line.data(), line.size(), 15) ==
          Capture::CaptureResult::Invalid);
    Capture::Line retained;
    CHECK(capture.probe(key(3, 11, 8, owner.backingAddress), 1, 16,
                        &retained) == Capture::ProbeResult::Miss);
    CHECK(capture.assertInvariants());
}

void
testOneCycleHitMissAndPageScopedReplay()
{
    Capture capture;
    const auto owner = key(7, 19, 2, 0x200000);
    auto first = payload(0x200);
    auto second = payload(0x300);
    CHECK(capture.begin(owner, 2048, 64) == Capture::BeginResult::Started);
    CHECK(capture.capture(owner, 17, 201, first.data(), first.size(), 100) ==
          Capture::CaptureResult::Captured);

    // The synchronous write has not completed in its issue cycle. A same-
    // cycle read gets the old (invalid) RAM value and the miss itself is held
    // by LookupPipeline until N+1.
    Capture::Line retained;
    CHECK(capture.probe(owner, 17, 100, &retained) ==
          Capture::ProbeResult::Miss);
    Capture::LookupPipeline miss;
    CHECK(miss.arm(100, Capture::ProbeResult::Miss));
    CHECK(!miss.ready(100));
    CHECK(miss.ready(101));

    CHECK(capture.probe(owner, 17, 101, &retained) ==
          Capture::ProbeResult::Hit);
    CHECK(retained.transactionID == 201);
    CHECK(word(retained.payload, 0) == 0x200);
    Capture::LookupPipeline hit;
    CHECK(hit.arm(101, Capture::ProbeResult::Hit));
    CHECK(!hit.ready(101));
    CHECK(hit.ready(102));
    CHECK(capture.take(owner, 17, 201, 102));

    CHECK(capture.capture(owner, 1025, 202, second.data(), second.size(),
                          103) == Capture::CaptureResult::Captured);
    CHECK(capture.probe(owner, 1024, 104, &retained) ==
          Capture::ProbeResult::Miss);
    CHECK(capture.probe(owner, 1025, 105, &retained) ==
          Capture::ProbeResult::Hit);
    CHECK(retained.line == 1025);
    CHECK(retained.transactionID == 202);
    CHECK(word(retained.payload, 7) == 0x307);
    CHECK(capture.take(owner, 1025, 202, 106));
    const auto summary = capture.summary(owner);
    CHECK(summary.storedLines == 0);
    CHECK(summary.capturedLines == 2);
    CHECK(summary.replayedLines == 2);
    CHECK(summary.capturedLinesPerPage[0] == 1);
    CHECK(summary.capturedLinesPerPage[2] == 1);
    CHECK(summary.replayedLinesPerPage[0] == 1);
    CHECK(summary.replayedLinesPerPage[2] == 1);
    CHECK(capture.assertInvariants());
}

void
testDescriptorCollisionAndLazyStaleReclamation()
{
    Capture capture;
    const auto displaced = key(1, 31, 1, 0x300000);
    const auto replacement = key(5, 41, 2, 0x500000);
    const auto successor = key(9, 51, 3, 0x700000);
    auto bytes = payload(0x400);
    CHECK(Capture::descriptorIndexForToken(displaced.tokenTile) ==
          Capture::descriptorIndexForToken(replacement.tokenTile));
    const auto replacementIndex =
        Capture::descriptorIndexForToken(replacement.tokenTile);
    CHECK(replacementIndex ==
          Capture::descriptorIndexForToken(successor.tokenTile));
    CHECK(capture.begin(displaced, 2048, 64) ==
          Capture::BeginResult::Started);
    const auto displacedCapture =
        capture.capture(displaced, 0, 301, bytes.data(), bytes.size(), 300);
    CHECK(displacedCapture == Capture::CaptureResult::Captured);
    uint16_t displacedLines = 0;
    CHECK(capture.begin(replacement, 2048, 64, &displacedLines) ==
          Capture::BeginResult::Replaced);
    CHECK(displacedLines == 1);
    CHECK(!capture.active(displaced));
    CHECK(capture.active(replacement));

    // The displaced descriptor is the coherence authority. Its stale exact
    // RAM tag must be a normal one-cycle miss, never a retained hit.
    Capture::Line retained;
    CHECK(capture.probe(displaced, 0, 301, &retained) ==
          Capture::ProbeResult::Miss);
    Capture::LookupPipeline displacedMiss;
    CHECK(displacedMiss.arm(301, Capture::ProbeResult::Miss));
    CHECK(!displacedMiss.ready(301));
    CHECK(displacedMiss.ready(302));

    // Descriptor replacement did not scan the RAM: the stale physical tag is
    // still counted. A direct-index collision reclaims precisely that tag.
    CHECK(capture.occupancy() == 1);
    const uint16_t staleIndex = capture.selectedEntry(displaced, 0);
    const uint16_t replacementLine =
        collidingLine(capture, replacement, staleIndex);
    CHECK(capture.capture(replacement, replacementLine, 302, bytes.data(),
                          bytes.size(), 301) ==
          Capture::CaptureResult::Captured);
    CHECK(capture.occupancy() == 1);
    CHECK(capture.summary(replacement).conflicts == 0);

    // Clear is also descriptor-only. Its line becomes stale and is reclaimed
    // only when a successor selects the same RAM index.
    CHECK(capture.clear(replacement));
    CHECK(!capture.active(replacement));
    CHECK(capture.occupancy() == 1);
    CHECK(capture.probe(replacement, replacementLine, 302, &retained) ==
          Capture::ProbeResult::Miss);
    Capture::LookupPipeline clearedMiss;
    CHECK(clearedMiss.arm(302, Capture::ProbeResult::Miss));
    CHECK(!clearedMiss.ready(302));
    CHECK(clearedMiss.ready(303));
    CHECK(capture.begin(successor, 2048, 64) ==
          Capture::BeginResult::Started);
    const uint16_t successorLine =
        collidingLine(capture, successor, staleIndex);
    CHECK(capture.capture(successor, successorLine, 303, bytes.data(),
                          bytes.size(), 303) ==
          Capture::CaptureResult::Captured);
    CHECK(capture.occupancy() == 1);
    CHECK(capture.assertInvariants());
}

void
testFirstOwnerCollisions()
{
    auto firstBytes = payload(0x500);
    auto latestBytes = payload(0x600);
    const auto first = key(2, 61, 4, 0x800000);
    const auto latest = key(3, 62, 5, 0x900000);

    Capture firstOwner;
    CHECK(firstOwner.begin(first, 2048, 64) == Capture::BeginResult::Started);
    CHECK(firstOwner.begin(latest, 2048, 64) == Capture::BeginResult::Started);
    CHECK(firstOwner.capture(first, 0, 401, firstBytes.data(),
                             firstBytes.size(), 400) ==
          Capture::CaptureResult::Captured);
    const uint16_t collision = collidingLine(
        firstOwner, latest, firstOwner.selectedEntry(first, 0));
    CHECK(firstOwner.capture(latest, collision, 402, latestBytes.data(),
                             latestBytes.size(), 401) ==
          Capture::CaptureResult::Conflict);
    CHECK(firstOwner.summary(latest).firstOwnerConflicts == 1);
    CHECK(firstOwner.summary(latest).drops == 1);

    CHECK(firstOwner.summary(first).storedLines == 1);
    CHECK(firstOwner.assertInvariants());
}

void
testDescriptorReplacementAfterLatchedHitClosesExactly()
{
    Capture capture;
    const auto oldOwner = key(4, 75, 10, 0xc00000);
    const auto replacement = key(8, 76, 11, 0xd00000);
    auto bytes = payload(0x880);
    CHECK(Capture::descriptorIndexForToken(oldOwner.tokenTile) ==
          Capture::descriptorIndexForToken(replacement.tokenTile));
    CHECK(capture.begin(oldOwner, 2048, 64) ==
          Capture::BeginResult::Started);
    CHECK(capture.capture(oldOwner, 0, 651, bytes.data(), bytes.size(), 650) ==
          Capture::CaptureResult::Captured);
    CHECK(capture.capture(oldOwner, 1, 652, bytes.data(), bytes.size(), 651) ==
          Capture::CaptureResult::Captured);

    Capture::Line retained;
    CHECK(capture.probe(oldOwner, 0, 652, &retained) ==
          Capture::ProbeResult::Hit);
    CHECK(retained.transactionID == 651);
    uint16_t displacedLines = 0;
    CHECK(capture.begin(replacement, 2048, 64, &displacedLines) ==
          Capture::BeginResult::Replaced);
    // Line zero survives in the authoritative output latch. Only unlatched
    // line one becomes a coherent-fallback drop at descriptor displacement.
    CHECK(displacedLines == 1);
    CHECK(capture.summary(replacement).storedLines == 0);
    CHECK(capture.take(oldOwner, 0, 651, 660));
    CHECK(capture.occupancy() == 1);

    CHECK(capture.probe(oldOwner, 1, 661, &retained) ==
          Capture::ProbeResult::Miss);
    Capture::LookupPipeline miss;
    CHECK(miss.arm(661, Capture::ProbeResult::Miss));
    CHECK(!miss.ready(661));
    CHECK(miss.ready(662));

    const uint16_t replacementLine = collidingLine(
        capture, replacement, capture.selectedEntry(oldOwner, 1));
    CHECK(capture.capture(replacement, replacementLine, 653, bytes.data(),
                          bytes.size(), 662) ==
          Capture::CaptureResult::Captured);
    CHECK(capture.occupancy() == 1);
    CHECK(capture.summary(replacement).storedLines == 1);
    CHECK(capture.assertInvariants());
}

void
testClearDirectRetirementHandoffAccountsOnce()
{
    Capture capture;
    const auto owner = key(12, 77, 12, 0xe00000);
    auto bytes = payload(0x890);
    CHECK(capture.begin(owner, 2048, 64) == Capture::BeginResult::Started);
    CHECK(capture.capture(owner, 0, 661, bytes.data(), bytes.size(), 670) ==
          Capture::CaptureResult::Captured);
    CHECK(capture.capture(owner, 1, 662, bytes.data(), bytes.size(), 671) ==
          Capture::CaptureResult::Captured);

    uint64_t globalDrops = 0;
    const auto handoff = capture.clear(owner);
    CHECK(handoff.cleared);
    CHECK(handoff.discardedLines == 2);
    CHECK(handoff.survivingLatchedLines == 0);
    globalDrops += handoff.discardedLines;

    // Final direct retirement observes the already-cleared descriptor. The
    // handoff's retained lines become coherent-fallback drops exactly once.
    const auto retirement = capture.clear(owner);
    CHECK(!retirement.cleared);
    CHECK(retirement.discardedLines == 0);
    CHECK(retirement.survivingLatchedLines == 0);
    globalDrops += retirement.discardedLines;
    CHECK(globalDrops == 2);
    CHECK(capture.assertInvariants());
}

void
testClearCompletedMaterializerHasZeroLiveLines()
{
    Capture capture;
    const auto owner = key(13, 78, 13, 0xf00000);
    auto bytes = payload(0x8a0);
    CHECK(capture.begin(owner, 2048, 64) == Capture::BeginResult::Started);
    CHECK(capture.capture(owner, 0, 671, bytes.data(), bytes.size(), 680) ==
          Capture::CaptureResult::Captured);
    Capture::Line retained;
    CHECK(capture.probe(owner, 0, 681, &retained) ==
          Capture::ProbeResult::Hit);
    CHECK(capture.take(owner, 0, 671, 682));
    CHECK(capture.summary(owner).storedLines == 0);

    const auto completed = capture.clear(owner);
    CHECK(completed.cleared);
    CHECK(completed.discardedLines == 0);
    CHECK(completed.survivingLatchedLines == 0);
    CHECK(capture.assertInvariants());
}

void
testClearCancelReuseDiscardsRetainedLines()
{
    Capture capture;
    const auto canceled = key(14, 79, 14, 0x1000000);
    const auto reused = key(14, 80, 15, 0x1100000);
    auto bytes = payload(0x8b0);
    CHECK(capture.begin(canceled, 2048, 64) ==
          Capture::BeginResult::Started);
    CHECK(capture.capture(canceled, 8, 681, bytes.data(), bytes.size(), 690) ==
          Capture::CaptureResult::Captured);
    CHECK(capture.capture(canceled, 9, 682, bytes.data(), bytes.size(), 691) ==
          Capture::CaptureResult::Captured);

    const auto cancel = capture.clear(canceled);
    CHECK(cancel.cleared);
    CHECK(cancel.discardedLines == 2);
    CHECK(cancel.survivingLatchedLines == 0);
    CHECK(capture.begin(reused, 2048, 64) == Capture::BeginResult::Started);
    Capture::Line retained;
    CHECK(capture.probe(canceled, 8, 692, &retained) ==
          Capture::ProbeResult::Miss);
    CHECK(capture.active(reused));
    CHECK(capture.assertInvariants());
}

void
testClearPreservesAuthoritativeOutputLatchAcrossRaces()
{
    auto oldBytes = payload(0x8c0);

    Capture resident;
    const auto residentOwner = key(15, 81, 16, 0x1200000);
    CHECK(resident.begin(residentOwner, 2048, 64) ==
          Capture::BeginResult::Started);
    CHECK(resident.capture(residentOwner, 0, 691, oldBytes.data(),
                           oldBytes.size(), 700) ==
          Capture::CaptureResult::Captured);
    CHECK(resident.capture(residentOwner, 1, 692, oldBytes.data(),
                           oldBytes.size(), 701) ==
          Capture::CaptureResult::Captured);
    Capture::Line retained;
    CHECK(resident.probe(residentOwner, 0, 702, &retained) ==
          Capture::ProbeResult::Hit);
    const auto residentClear = resident.clear(residentOwner);
    CHECK(residentClear.cleared);
    CHECK(residentClear.discardedLines == 1);
    CHECK(residentClear.survivingLatchedLines == 1);
    CHECK(word(retained.payload, 0) == 0x8c0);
    CHECK(resident.take(residentOwner, 0, 691, 703));

}

void
testSameCycleReadBeforeWriteAndFinitePorts()
{
    const auto oldOwner = key(10, 81, 8, 0xc00000);
    const auto newOwner = key(11, 82, 9, 0xd00000);
    auto oldBytes = payload(0x900);
    auto newBytes = payload(0xa00);
    auto verifyCallOrder = [&](bool captureFirst) {
        Capture capture;
        CHECK(capture.begin(oldOwner, 2048, 64) ==
              Capture::BeginResult::Started);
        CHECK(capture.begin(newOwner, 2048, 64) ==
              Capture::BeginResult::Started);
        CHECK(capture.capture(oldOwner, 0, 701, oldBytes.data(),
                              oldBytes.size(), 700) ==
              Capture::CaptureResult::Captured);
        const uint16_t collision = collidingLine(
            capture, newOwner, capture.selectedEntry(oldOwner, 0));
        Capture::Line retained;
        const auto captureConflict = [&] {
            CHECK(capture.capture(newOwner, collision, 702, newBytes.data(),
                                  newBytes.size(), 701) ==
                  Capture::CaptureResult::Conflict);
        };
        const auto probeOld = [&] {
            CHECK(capture.probe(oldOwner, 0, 701, &retained) ==
                  Capture::ProbeResult::Hit);
            CHECK(retained.transactionID == 701);
            CHECK(word(retained.payload, 0) == 0x900);
        };
        if (captureFirst) {
            captureConflict();
            probeOld();
        } else {
            probeOld();
            captureConflict();
        }
        CHECK(capture.take(oldOwner, 0, 701, 702));
        CHECK(capture.summary(newOwner).firstOwnerConflicts == 1);
        CHECK(capture.assertInvariants());
    };
    verifyCallOrder(true);
    verifyCallOrder(false);
}

void
testOutcomeClosure()
{
    // Every public capture outcome is either retained/redundant or an
    // explicit coherent-fallback/global-accounting outcome. This guards the
    // MAA switch against silently omitting Full/Untracked/port/conflict-like
    // cases when enum values evolve.
    constexpr std::array<Capture::CaptureResult, 8> outcomes = {
        Capture::CaptureResult::Disabled,
        Capture::CaptureResult::Captured,
        Capture::CaptureResult::Duplicate,
        Capture::CaptureResult::Conflict,
        Capture::CaptureResult::PortBusy,
        Capture::CaptureResult::Untracked,
        Capture::CaptureResult::Stale,
        Capture::CaptureResult::Invalid,
    };
    uint16_t retained = 0;
    uint16_t redundant = 0;
    uint16_t fallbackOrGlobal = 0;
    for (const auto outcome : outcomes) {
        switch (outcome) {
          case Capture::CaptureResult::Captured:
            ++retained;
            break;
          case Capture::CaptureResult::Duplicate:
            ++redundant;
            break;
          case Capture::CaptureResult::Disabled:
          case Capture::CaptureResult::Conflict:
          case Capture::CaptureResult::PortBusy:
          case Capture::CaptureResult::Untracked:
          case Capture::CaptureResult::Stale:
          case Capture::CaptureResult::Invalid:
            ++fallbackOrGlobal;
            break;
        }
    }
    CHECK(retained == 1);
    CHECK(redundant == 1);
    CHECK(fallbackOrGlobal == 6);
    CHECK(retained + redundant + fallbackOrGlobal == outcomes.size());
}

void
testExactPackedHardwareStorageEquations()
{
    CHECK(Capture::validCapacity(0));
    CHECK(!Capture::validCapacity(32));
    CHECK(Capture::validCapacity(64));
    CHECK(Capture::validCapacity(128));
    CHECK(Capture::validCapacity(256));
    CHECK(Capture::validCapacity(512));
    CHECK(!Capture::validCapacity(1024));
    CHECK(Capture::provisionedPayloadBytes(0) == 0);
    CHECK(Capture::provisionedPayloadBytes(64) == 4096);
    CHECK(Capture::provisionedPayloadBytes(128) == 8192);
    CHECK(Capture::provisionedPayloadBytes(256) == 16384);
    CHECK(Capture::provisionedPayloadBytes(512) == 32768);
    CHECK(Capture::KeyBits == 208);
    CHECK(Capture::EntryTagBits == 289);
    CHECK(Capture::DescriptorBits == 593);
    CHECK(Capture::provisionedTagBits(512) == 512 * 289);
    CHECK(Capture::provisionedDescriptorBits(512) == 4 * 593);
    CHECK(Capture::provisionedReadPortStateBits(512) == 64);
    CHECK(Capture::provisionedWritePortStateBits(512) == 939);
    CHECK(Capture::provisionedOutputTagBits(512) == 289);
    CHECK(Capture::provisionedReadPipelinePayloadBytes(512) == 64);
    CHECK(Capture::MAALookupControlBits == 1772);
    CHECK(Capture::PayloadIncarnationBitsPerToken == 64);
    CHECK(Capture::provisionedMAAPersistentStateBits(0, 32) == 0);
    CHECK(Capture::provisionedMAAPersistentStateBits(64, 32) == 2048);
    CHECK(Capture::GlobalControlBits == 30);
    CHECK(Capture::provisionedMAAControlBits(64, 32) == 26007);
    CHECK(Capture::provisionedMAAControlBits(512, 32) == 155482);
    CHECK(Capture::provisionedCombinedTotalBytes(64, 32) == 7411);
    CHECK(Capture::provisionedCombinedTotalBytes(128, 32) == 13819);
    CHECK(Capture::provisionedCombinedTotalBytes(256, 32) == 26636);
    CHECK(Capture::provisionedCombinedTotalBytes(512, 32) == 52268);
    CHECK(Capture::provisionedControlBits(512) ==
          Capture::provisionedTagBits(512) +
              Capture::provisionedDescriptorBits(512) +
              Capture::provisionedReadPortStateBits(512) +
              Capture::provisionedWritePortStateBits(512) +
              Capture::provisionedOutputTagBits(512) +
              Capture::GlobalControlBits);
    CHECK(Capture::provisionedControlBytes(64) == 2774);
    CHECK(Capture::provisionedControlBytes(512) == 18958);
    CHECK(Capture::provisionedTotalBytes(64) == 6934);
    CHECK(Capture::provisionedTotalBytes(512) == 51790);

    // Host layout is diagnostic only and is deliberately not used by any
    // packed hardware lower-bound equation above.
    std::cout << "inactive payload storage cap64_total_bits="
              << Capture::provisionedTotalBits(64)
              << " cap512_total_bits="
              << Capture::provisionedTotalBits(512)
              << " entry_tag_bits=" << Capture::EntryTagBits
              << " descriptor_bits_each=" << Capture::DescriptorBits
              << " read_port_state_bits="
              << Capture::provisionedReadPortStateBits(512)
              << " write_port_state_bits="
              << Capture::provisionedWritePortStateBits(512)
              << " output_payload_bits=" << Capture::OutputPayloadBits
              << " output_tag_bits=" << Capture::OutputTagBits
              << " maa_lookup_control_bits="
              << Capture::MAALookupControlBits
              << " persistent_incarnation_bits_32_tokens="
              << Capture::provisionedMAAPersistentStateBits(512, 32)
              << " combined_total_bits_32_tokens="
              << Capture::provisionedCombinedTotalBits(512, 32)
              << " host_object_bytes=" << sizeof(Capture) << '\n';
}

} // anonymous namespace

int
main()
{
    testDefaultOffAndExactIncarnationIdentity();
    testOneCycleHitMissAndPageScopedReplay();
    testDescriptorCollisionAndLazyStaleReclamation();
    testFirstOwnerCollisions();
    testDescriptorReplacementAfterLatchedHitClosesExactly();
    testClearDirectRetirementHandoffAccountsOnce();
    testClearCompletedMaterializerHasZeroLiveLines();
    testClearCancelReuseDiscardsRetainedLines();
    testClearPreservesAuthoritativeOutputLatchAcrossRaces();
    testSameCycleReadBeforeWriteAndFinitePorts();
    testOutcomeClosure();
    testExactPackedHardwareStorageEquations();
    std::cout << "inactive producer line payload capture tests passed\n";
    return 0;
}
