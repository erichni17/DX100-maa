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
    CHECK(capture.begin(replacement, 2048, 64,
                        Capture::ConflictPolicy::FirstOwner,
                        &displacedLines) ==
          Capture::BeginResult::Replaced);
    CHECK(displacedLines == 1);
    CHECK(!capture.active(displaced));
    CHECK(capture.active(replacement));

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
    CHECK(capture.begin(successor, 2048, 64) ==
          Capture::BeginResult::Started);
    const uint16_t successorLine =
        collidingLine(capture, successor, staleIndex);
    CHECK(capture.capture(successor, successorLine, 303, bytes.data(),
                          bytes.size(), 302) ==
          Capture::CaptureResult::Captured);
    CHECK(capture.occupancy() == 1);
    CHECK(capture.assertInvariants());
}

void
testFirstOwnerAndLatestOwnerCollisions()
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

    Capture latestOwner;
    CHECK(latestOwner.begin(first, 2048, 64,
                            Capture::ConflictPolicy::LatestOwner) ==
          Capture::BeginResult::Started);
    CHECK(latestOwner.begin(latest, 2048, 64,
                            Capture::ConflictPolicy::LatestOwner) ==
          Capture::BeginResult::Started);
    CHECK(latestOwner.capture(first, 0, 501, firstBytes.data(),
                              firstBytes.size(), 500) ==
          Capture::CaptureResult::Captured);
    const uint16_t latestCollision = collidingLine(
        latestOwner, latest, latestOwner.selectedEntry(first, 0));
    CHECK(latestOwner.capture(latest, latestCollision, 502,
                              latestBytes.data(), latestBytes.size(), 501) ==
          Capture::CaptureResult::Overwritten);
    CHECK(latestOwner.summary(first).storedLines == 0);
    CHECK(latestOwner.summary(first).latestOwnerEvictions == 1);
    CHECK(latestOwner.summary(latest).latestOwnerOverwrites == 1);
    CHECK(latestOwner.assertInvariants());
}

void
testLatestOwnerProbeOverwriteTakeUsesOutputLatch()
{
    Capture capture;
    const auto probed = key(6, 71, 6, 0xa00000);
    const auto writer = key(7, 72, 7, 0xb00000);
    auto oldBytes = payload(0x700);
    auto newBytes = payload(0x800);
    CHECK(capture.begin(probed, 2048, 64,
                        Capture::ConflictPolicy::LatestOwner) ==
          Capture::BeginResult::Started);
    CHECK(capture.begin(writer, 2048, 64,
                        Capture::ConflictPolicy::LatestOwner) ==
          Capture::BeginResult::Started);
    CHECK(capture.capture(probed, 0, 601, oldBytes.data(), oldBytes.size(),
                          600) == Capture::CaptureResult::Captured);
    const uint16_t writerLine = collidingLine(
        capture, writer, capture.selectedEntry(probed, 0));

    Capture::Line retained;
    CHECK(capture.probe(probed, 0, 601, &retained) ==
          Capture::ProbeResult::Hit);
    CHECK(retained.transactionID == 601);
    CHECK(word(retained.payload, 0) == 0x700);
    CHECK(capture.capture(writer, writerLine, 602, newBytes.data(),
                          newBytes.size(), 601) ==
          Capture::CaptureResult::Overwritten);

    // At N+1 the replacement is in RAM, but take authenticates and consumes
    // transaction 601 from the output latch. It must not erase transaction
    // 602 or panic because mutable RAM no longer carries the probed tag.
    CHECK(!capture.take(probed, 0, 999, 602));
    CHECK(capture.take(probed, 0, 601, 602));
    CHECK(capture.probe(writer, writerLine, 603, &retained) ==
          Capture::ProbeResult::Hit);
    CHECK(retained.transactionID == 602);
    CHECK(word(retained.payload, 0) == 0x800);
    CHECK(capture.take(writer, writerLine, 602, 604));
    CHECK(capture.occupancy() == 0);
    CHECK(capture.assertInvariants());
}

void
testSameCycleReadBeforeWriteAndFinitePorts()
{
    Capture capture;
    const auto oldOwner = key(10, 81, 8, 0xc00000);
    const auto newOwner = key(11, 82, 9, 0xd00000);
    auto oldBytes = payload(0x900);
    auto newBytes = payload(0xa00);
    CHECK(capture.begin(oldOwner, 2048, 64,
                        Capture::ConflictPolicy::LatestOwner) ==
          Capture::BeginResult::Started);
    CHECK(capture.begin(newOwner, 2048, 64,
                        Capture::ConflictPolicy::LatestOwner) ==
          Capture::BeginResult::Started);
    CHECK(capture.capture(oldOwner, 0, 701, oldBytes.data(), oldBytes.size(),
                          700) == Capture::CaptureResult::Captured);
    const uint16_t collision = collidingLine(
        capture, newOwner, capture.selectedEntry(oldOwner, 0));

    // Call the write first to prove the model's same-cycle result does not
    // depend on C++ call order: synchronous RAM returns the old word.
    CHECK(capture.capture(newOwner, collision, 702, newBytes.data(),
                          newBytes.size(), 701) ==
          Capture::CaptureResult::Overwritten);
    Capture::Line retained;
    CHECK(capture.probe(oldOwner, 0, 701, &retained) ==
          Capture::ProbeResult::Hit);
    CHECK(retained.transactionID == 701);
    CHECK(word(retained.payload, 0) == 0x900);
    CHECK(capture.probe(newOwner, collision, 701, &retained) ==
          Capture::ProbeResult::PortBusy);
    CHECK(capture.capture(newOwner, collision + 1, 703, newBytes.data(),
                          newBytes.size(), 701) ==
          Capture::CaptureResult::PortBusy);
    CHECK(capture.take(oldOwner, 0, 701, 702));
    CHECK(capture.probe(newOwner, collision, 702, &retained) ==
          Capture::ProbeResult::Hit);
    CHECK(retained.transactionID == 702);
    CHECK(capture.assertInvariants());
}

void
testOutcomeClosure()
{
    // Every public capture outcome is either retained/redundant or an
    // explicit coherent-fallback/global-accounting outcome. This guards the
    // MAA switch against silently omitting Full/Untracked/port/conflict-like
    // cases when enum values evolve.
    constexpr std::array<Capture::CaptureResult, 9> outcomes = {
        Capture::CaptureResult::Disabled,
        Capture::CaptureResult::Captured,
        Capture::CaptureResult::Duplicate,
        Capture::CaptureResult::Conflict,
        Capture::CaptureResult::Overwritten,
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
          case Capture::CaptureResult::Overwritten:
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
    CHECK(retained == 2);
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
    CHECK(Capture::DescriptorBits == 625);
    CHECK(Capture::provisionedTagBits(512) == 512 * 289);
    CHECK(Capture::provisionedDescriptorBits(512) == 4 * 625);
    CHECK(Capture::provisionedReadPortStateBits(512) == 64);
    CHECK(Capture::provisionedWritePortStateBits(512) == 939);
    CHECK(Capture::provisionedOutputTagBits(512) == 289);
    CHECK(Capture::provisionedReadPipelinePayloadBytes(512) == 64);
    CHECK(Capture::MAALookupControlBits == 510);
    CHECK(Capture::provisionedControlBits(512) ==
          Capture::provisionedTagBits(512) +
              Capture::provisionedDescriptorBits(512) +
              Capture::provisionedReadPortStateBits(512) +
              Capture::provisionedWritePortStateBits(512) +
              Capture::provisionedOutputTagBits(512) +
              Capture::GlobalControlBits);
    CHECK(Capture::provisionedControlBytes(64) == 2790);
    CHECK(Capture::provisionedControlBytes(512) == 18974);
    CHECK(Capture::provisionedTotalBytes(64) == 6950);
    CHECK(Capture::provisionedTotalBytes(512) == 51806);

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
              << " host_object_bytes=" << sizeof(Capture) << '\n';
}

} // anonymous namespace

int
main()
{
    testDefaultOffAndExactIncarnationIdentity();
    testOneCycleHitMissAndPageScopedReplay();
    testDescriptorCollisionAndLazyStaleReclamation();
    testFirstOwnerAndLatestOwnerCollisions();
    testLatestOwnerProbeOverwriteTakeUsesOutputLatch();
    testSameCycleReadBeforeWriteAndFinitePorts();
    testOutcomeClosure();
    testExactPackedHardwareStorageEquations();
    std::cout << "inactive producer line payload capture tests passed\n";
    return 0;
}
