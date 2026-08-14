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
key(uint16_t token, uint64_t generation, uint64_t backing)
{
    return {token, generation, backing};
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

void
testDefaultOffAndExactIdentity()
{
    Capture capture;
    const auto owner = key(3, 11, 0x100000);
    auto line = payload(0x100);
    CHECK(capture.begin(owner, 2048, 0) == Capture::BeginResult::Disabled);
    CHECK(capture.capture(owner, 1, 101, line.data(), line.size(), 0) ==
          Capture::CaptureResult::Disabled);
    CHECK(!capture.active(owner));
    CHECK(capture.occupancy() == 0);

    CHECK(capture.begin(owner, 2048, 64) == Capture::BeginResult::Started);
    CHECK(capture.capture(owner, 1, 101, line.data(), line.size(), 10) ==
          Capture::CaptureResult::Captured);
    line.fill(std::byte{});
    CHECK(capture.capture(owner, 1, 102, line.data(), line.size(), 11) ==
          Capture::CaptureResult::Duplicate);
    CHECK(capture.capture(key(3, 10, owner.backingAddress), 2, 103,
                          line.data(), line.size(), 12) ==
          Capture::CaptureResult::Stale);
    CHECK(capture.capture(key(3, 11, owner.backingAddress + 64), 2, 103,
                          line.data(), line.size(), 13) ==
          Capture::CaptureResult::Stale);
    CHECK(capture.capture(owner, 2048, 104, line.data(), line.size(), 14) ==
          Capture::CaptureResult::Invalid);
    CHECK(capture.assertInvariants());
}

void
testPageScopedReplayPreservesPayloadAndDelayBoundary()
{
    Capture capture;
    const auto owner = key(7, 19, 0x200000);
    auto first = payload(0x200);
    auto second = payload(0x300);
    CHECK(capture.begin(owner, 2048, 64) == Capture::BeginResult::Started);
    CHECK(capture.capture(owner, 17, 201, first.data(), first.size(), 100) ==
          Capture::CaptureResult::Captured);
    CHECK(
        capture.capture(owner, 1025, 202, second.data(), second.size(), 101) ==
        Capture::CaptureResult::Captured);

    // The materializer probes one selected backing line; it never walks an
    // inactive page. A miss leaves both payloads private and unavailable to
    // SPD until the caller copies a hit to its existing charged buffer.
    Capture::Line retained;
    CHECK(capture.probe(owner, 1024, 200, &retained) ==
          Capture::ProbeResult::Miss);
    CHECK(capture.summary(owner).storedLines == 2);
    CHECK(capture.summary(owner).capturedLinesPerPage[0] == 1);
    CHECK(capture.summary(owner).capturedLinesPerPage[2] == 1);
    CHECK(capture.probe(owner, 1025, 201, &retained) ==
          Capture::ProbeResult::Hit);
    CHECK(retained.line == 1025);
    CHECK(retained.transactionID == 202);
    CHECK(word(retained.payload, 0) == 0x300);
    CHECK(word(retained.payload, 7) == 0x307);
    CHECK(capture.take(owner, 1025));
    const auto remaining = capture.summary(owner);
    CHECK(remaining.storedLines == 1);
    CHECK(remaining.capturedLines == 2);
    CHECK(remaining.replayedLines == 1);
    CHECK(remaining.replayedLinesPerPage[2] == 1);

    CHECK(capture.probe(owner, 17, 202, &retained) ==
          Capture::ProbeResult::Hit);
    CHECK(retained.transactionID == 201);
    CHECK(word(retained.payload, 0) == 0x200);
    CHECK(capture.take(owner, 17));
    CHECK(capture.summary(owner).storedLines == 0);
    CHECK(capture.summary(owner).replayedLines == 2);
    CHECK(capture.clear(owner));
    CHECK(!capture.active(owner));
    CHECK(capture.assertInvariants());
}

void
testCapacityFallbackAndGenerationCleanup()
{
    Capture capture;
    const auto oldOwner = key(9, 31, 0x300000);
    const auto newOwner = key(9, 32, 0x500000);
    auto line = payload(0x400);
    CHECK(capture.begin(oldOwner, 8, 2) == Capture::BeginResult::Started);
    CHECK(capture.capture(oldOwner, 0, 301, line.data(), line.size(), 300) ==
          Capture::CaptureResult::Captured);
    CHECK(capture.capture(oldOwner, 1, 302, line.data(), line.size(), 301) ==
          Capture::CaptureResult::Captured);
    // Lines zero and two have the same direct index at capacity two. The
    // first line wins deterministically; the later exact WriteResp falls back
    // coherently instead of evicting or priority-searching.
    CHECK(capture.capture(oldOwner, 2, 303, line.data(), line.size(), 302) ==
          Capture::CaptureResult::Conflict);
    CHECK(capture.summary(oldOwner).conflicts == 1);
    CHECK(capture.summary(oldOwner).drops == 1);
    CHECK(capture.summary(oldOwner).conflictsPerPage[1] == 1);
    CHECK(capture.summary(oldOwner).firstOwnerConflicts == 1);
    CHECK(capture.occupancy() == 2);
    CHECK(capture.begin(newOwner, 8, 2) == Capture::BeginResult::Replaced);
    CHECK(!capture.active(oldOwner));
    CHECK(capture.active(newOwner));
    CHECK(capture.occupancy() == 0);
    CHECK(capture.capture(oldOwner, 0, 304, line.data(), line.size(), 303) ==
          Capture::CaptureResult::Stale);
    CHECK(capture.clear(newOwner));
    CHECK(capture.assertInvariants());
}

void
testLatestOwnerConflictReplacement()
{
    Capture capture;
    const auto first = key(17, 51, 0x800000);
    const auto latest = key(18, 53, 0x800080);
    auto firstPayload = payload(0x700);
    auto latestPayload = payload(0x800);
    CHECK(capture.begin(first, 8, 2, Capture::ConflictPolicy::LatestOwner) ==
          Capture::BeginResult::Started);
    CHECK(capture.begin(latest, 8, 2, Capture::ConflictPolicy::LatestOwner) ==
          Capture::BeginResult::Started);
    CHECK(capture.conflictPolicy() == Capture::ConflictPolicy::LatestOwner);
    CHECK(capture.capture(first, 0, 501, firstPayload.data(),
                          firstPayload.size(), 700) ==
          Capture::CaptureResult::Captured);

    // These distinct exact keys map to the same capacity-two entry. Latest
    // owner replaces the complete tag, transaction, and payload at the same
    // write-port cost; the displaced first owner falls back coherently.
    CHECK(capture.capture(latest, 0, 502, latestPayload.data(),
                          latestPayload.size(), 701) ==
          Capture::CaptureResult::Overwritten);
    CHECK(capture.summary(first).storedLines == 0);
    CHECK(capture.summary(first).drops == 1);
    CHECK(capture.summary(first).latestOwnerEvictions == 1);
    CHECK(capture.summary(latest).storedLines == 1);
    CHECK(capture.summary(latest).conflicts == 1);
    CHECK(capture.summary(latest).latestOwnerOverwrites == 1);
    CHECK(capture.occupancy() == 1);

    Capture::Line retained;
    CHECK(capture.probe(first, 0, 800, &retained) ==
          Capture::ProbeResult::Miss);
    CHECK(capture.probe(latest, 0, 801, &retained) ==
          Capture::ProbeResult::Hit);
    CHECK(retained.transactionID == 502);
    CHECK(word(retained.payload, 0) == 0x800);
    CHECK(capture.take(latest, 0));
    CHECK(capture.clear(first));
    CHECK(capture.clear(latest));
    CHECK(capture.assertInvariants());
}

void
testFinitePortAvailability()
{
    Capture capture;
    const auto owner = key(13, 41, 0x700000);
    auto first = payload(0x500);
    auto second = payload(0x600);
    CHECK(capture.begin(owner, 2048, 64) == Capture::BeginResult::Started);

    // The fixed single write port makes same-tick WriteResp collisions
    // deterministic coherent-fallback drops, not zero-time multiwrites.
    CHECK(capture.capture(owner, 0, 401, first.data(), first.size(), 400) ==
          Capture::CaptureResult::Captured);
    CHECK(capture.capture(owner, 1, 402, second.data(), second.size(), 400) ==
          Capture::CaptureResult::PortBusy);
    CHECK(capture.summary(owner).capturedLines == 1);
    CHECK(capture.summary(owner).drops == 1);
    CHECK(capture.summary(owner).writePortDrops == 1);
    CHECK(capture.summary(owner).writePortDropsPerPage[0] == 1);
    CHECK(capture.capture(owner, 1, 402, second.data(), second.size(), 401) ==
          Capture::CaptureResult::Captured);

    // The selected-line read side has the same finite one-access-per-cycle
    // limit. A busy replay is retried; it does not fall through to
    // ReadBacking.
    Capture::Line retained;
    CHECK(capture.probe(owner, 0, 500, &retained) ==
          Capture::ProbeResult::Hit);
    CHECK(capture.probe(owner, 1, 500, &retained) ==
          Capture::ProbeResult::PortBusy);
    CHECK(capture.probe(owner, 1, 501, &retained) ==
          Capture::ProbeResult::Hit);
    CHECK(retained.transactionID == 402);
    CHECK(word(retained.payload, 0) == 0x600);
    CHECK(capture.clear(owner));
    CHECK(capture.assertInvariants());
}

void
testLookupMissWaitsOneMAACycle()
{
    Capture::LookupPipeline lookup;
    CHECK(lookup.arm(600, Capture::ProbeResult::Miss));
    CHECK(lookup.pending());
    CHECK(lookup.completionCycle() == 600 + Capture::PortAccessCycles);

    // This is the same gate used by MAA before it may issue ReadBacking: a
    // direct-indexed Miss at cycle 600 cannot bypass until cycle 601.
    bool fallbackIssued = false;
    if (lookup.ready(600) && lookup.result() == Capture::ProbeResult::Miss)
        fallbackIssued = true;
    CHECK(!fallbackIssued);
    CHECK(lookup.ready(601));
    if (lookup.ready(601) && lookup.result() == Capture::ProbeResult::Miss)
        fallbackIssued = true;
    CHECK(fallbackIssued);
    lookup.clear();
    CHECK(!lookup.pending());
}

void
testProvisionedHardwareAccounting()
{
    CHECK(Capture::provisionedPayloadBytes(0) == 0);
    CHECK(Capture::provisionedPayloadBytes(64) == 4096);
    CHECK(Capture::provisionedPayloadBytes(128) == 8192);
    CHECK(Capture::provisionedPayloadBytes(256) == 16384);
    CHECK(Capture::provisionedPayloadBytes(512) == 32768);
    CHECK(Capture::provisionedReadPipelinePayloadBytes(0) == 0);
    CHECK(Capture::provisionedReadPipelinePayloadBytes(512) == 64);
    CHECK(Capture::provisionedTagBytes(512) == 14848);
    CHECK(Capture::provisionedControlBytes(64) == 2163);
    CHECK(Capture::provisionedControlBytes(512) == 15155);
    CHECK(Capture::provisionedTotalBytes(512) == 47987);
    std::cout << "inactive payload capture storage cap64_payload="
              << Capture::provisionedPayloadBytes(64)
              << " cap64_tag_control="
              << Capture::provisionedControlBytes(64)
              << " cap512_payload="
              << Capture::provisionedPayloadBytes(512)
              << " cap512_tag_control="
              << Capture::provisionedControlBytes(512)
              << " read_pipeline_payload="
              << Capture::provisionedReadPipelinePayloadBytes(512)
              << " write_ports=" << unsigned(Capture::WritePortCount)
              << " read_ports=" << unsigned(Capture::ReadPortCount)
              << " port_access_cycles="
              << unsigned(Capture::PortAccessCycles)
              << " port_time_unit=maa_cycles\n";
}

} // anonymous namespace

int
main()
{
    testDefaultOffAndExactIdentity();
    testPageScopedReplayPreservesPayloadAndDelayBoundary();
    testCapacityFallbackAndGenerationCleanup();
    testLatestOwnerConflictReplacement();
    testFinitePortAvailability();
    testLookupMissWaitsOneMAACycle();
    testProvisionedHardwareAccounting();
    std::cout << "inactive producer line payload capture tests passed\n";
    return 0;
}
