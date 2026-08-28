#include <array>
#include <cstdint>
#include <cstdlib>
#include <iostream>

#include "mem/MAA/VirtualCombinePayloadStore.hh"
#include "mem/MAA/VirtualCombineVictimSelector.hh"
#include "mem/MAA/VirtualRetirementScoreboard.hh"

using gem5::VirtualCombinePayloadStore;

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;        \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

using Result = VirtualCombinePayloadStore::Result;
using Selector = gem5::maa::VirtualCombineVictimSelector;
using Scoreboard = gem5::maa::VirtualRetirementScoreboard;

static std::array<uint8_t, 8>
word(uint8_t base)
{
    std::array<uint8_t, 8> value{};
    for (size_t i = 0; i < value.size(); ++i)
        value[i] = base + i;
    return value;
}

static void
checkFp32AllocateUpdateMaskedDrainAndReuse()
{
    VirtualCombinePayloadStore store;
    CHECK(store.reset(3) == Result::Ok);
    auto refs = VirtualCombinePayloadStore::emptyLineRefs();
    const auto first = word(0x10);
    const auto second = word(0x20);
    const auto updated = word(0x80);
    const auto third = word(0x30);
    const auto exhausted = word(0x40);

    CHECK(store.allocate(first.data(), 4, refs[1]) == Result::Ok);
    CHECK(store.allocate(second.data(), 4, refs[4]) == Result::Ok);
    CHECK(store.allocate(third.data(), 4, refs[9]) == Result::Ok);
    CHECK(store.used() == 3 && store.full());
    auto no_ref = VirtualCombinePayloadStore::InvalidWord;
    CHECK(store.allocate(exhausted.data(), 4, no_ref) == Result::Exhausted);
    CHECK(no_ref == VirtualCombinePayloadStore::InvalidWord);

    CHECK(store.update(refs[4], updated.data(), 4) == Result::Ok);
    VirtualCombinePayloadStore::LineData line{};
    constexpr uint16_t mask = (1U << 1) | (1U << 4) | (1U << 9);
    CHECK(store.copyLine(refs, mask, 4, line) == Result::Ok);
    for (size_t byte = 0; byte < 4; ++byte) {
        CHECK(line[1 * 4 + byte] == first[byte]);
        CHECK(line[4 * 4 + byte] == updated[byte]);
        CHECK(line[9 * 4 + byte] == third[byte]);
    }
    CHECK(line[0] == 0 && line[63] == 0);

    // A masked line write releases all referenced words atomically.
    CHECK(store.releaseMasked(refs, mask) == Result::Ok);
    CHECK(store.empty());
    CHECK(refs[1] == VirtualCombinePayloadStore::InvalidWord);
    CHECK(refs[4] == VirtualCombinePayloadStore::InvalidWord);
    CHECK(refs[9] == VirtualCombinePayloadStore::InvalidWord);

    // Reuse returns a fresh generation, so a stale reference cannot free it.
    auto reused = VirtualCombinePayloadStore::InvalidWord;
    CHECK(store.allocate(exhausted.data(), 4, reused) == Result::Ok);
    CHECK(store.data(reused) != nullptr);
    CHECK(store.release(reused) == Result::Ok);
    CHECK(store.release(reused) == Result::DoubleFree);
}

static void
checkFullFp32AndFp64Drains()
{
    VirtualCombinePayloadStore store;
    CHECK(store.reset(16) == Result::Ok);
    auto refs = VirtualCombinePayloadStore::emptyLineRefs();
    for (size_t i = 0; i < 16; ++i) {
        const auto value = word(static_cast<uint8_t>(i * 8));
        CHECK(store.allocate(value.data(), 4, refs[i]) == Result::Ok);
    }
    VirtualCombinePayloadStore::LineData line{};
    CHECK(store.copyLine(refs, 0xffff, 4, line) == Result::Ok);
    for (size_t i = 0; i < line.size(); ++i)
        CHECK(line[i] == static_cast<uint8_t>((i / 4) * 8 + i % 4));
    CHECK(store.releaseMasked(refs, 0xffff) == Result::Ok);
    CHECK(store.empty());

    CHECK(store.reset(8) == Result::Ok);
    refs = VirtualCombinePayloadStore::emptyLineRefs();
    for (size_t i = 0; i < 8; ++i) {
        const auto value = word(static_cast<uint8_t>(0xa0 + i * 8));
        CHECK(store.allocate(value.data(), 8, refs[i]) == Result::Ok);
    }
    CHECK(store.copyLine(refs, 0x00ff, 8, line) == Result::Ok);
    for (size_t i = 0; i < line.size(); ++i)
        CHECK(line[i] == static_cast<uint8_t>(0xa0 + i));

    // Word-at-a-time drain releases each reference exactly once.
    for (size_t i = 0; i < 8; ++i) {
        CHECK(store.data(refs[i]) != nullptr);
        CHECK(store.release(refs[i]) == Result::Ok);
    }
    CHECK(store.empty());
}

static void
checkErrorsFailClosed()
{
    VirtualCombinePayloadStore store;
    CHECK(store.reset(2) == Result::Ok);
    auto refs = VirtualCombinePayloadStore::emptyLineRefs();
    const auto value = word(0x55);
    CHECK(store.allocate(value.data(), 4, refs[0]) == Result::Ok);
    CHECK(store.allocate(value.data(), 4, refs[0]) ==
          Result::DuplicateReference);
    CHECK(store.used() == 1);
    CHECK(store.update(refs[0], value.data(), 2) == Result::InvalidWordBytes);
    auto null_ref = VirtualCombinePayloadStore::InvalidWord;
    CHECK(store.allocate(nullptr, 4, null_ref) == Result::InvalidData);
    CHECK(store.update(refs[0], nullptr, 4) == Result::InvalidData);

    // Duplicate ownership in one line is rejected before either word is freed.
    refs[1] = refs[0];
    CHECK(store.releaseMasked(refs, 0x0003) == Result::DuplicateReference);
    CHECK(store.used() == 1 && store.data(refs[0]) != nullptr);
    refs[1] = VirtualCombinePayloadStore::InvalidWord;

    const auto stale = refs[0];
    CHECK(store.release(refs[0]) == Result::Ok);
    CHECK(store.empty());
    auto reused = VirtualCombinePayloadStore::InvalidWord;
    CHECK(store.allocate(value.data(), 8, reused) == Result::Ok);
    auto stale_copy = stale;
    CHECK(store.release(stale_copy) == Result::InvalidReference);
    CHECK(store.used() == 1 && store.data(reused) != nullptr);
    CHECK(store.reset(2) == Result::Busy);
    CHECK(store.release(reused) == Result::Ok);
}

static void
checkGlobalFullCurrentSetEmptyMaskedAckClosure()
{
    // Two packed words globally fill the pool while both slots in the
    // incoming set remain empty.  A set-local victim search has no legal
    // candidate and formerly panicked here.
    VirtualCombinePayloadStore store;
    CHECK(store.reset(2) == Result::Ok);
    std::array<VirtualCombinePayloadStore::LineRefs, 4> refs{};
    for (auto &line_refs : refs)
        line_refs = VirtualCombinePayloadStore::emptyLineRefs();
    const auto first = word(0x10);
    const auto second = word(0x20);
    CHECK(store.allocate(first.data(), 8, refs[0][0]) == Result::Ok);
    CHECK(store.allocate(second.data(), 8, refs[1][1]) == Result::Ok);
    CHECK(store.full());

    std::array<Selector::Candidate, 4> candidates{{
        {true, 0x0001}, {true, 0x0002}, {false, 0}, {false, 0}}};
    const auto decision = Selector::select(
        [&candidates](int index) { return candidates[index]; },
        4, 2, 1, -1, true, false, 0, 0, 0);
    CHECK(decision.globalPayloadVictim);
    CHECK(decision.victim == 0);
    CHECK(decision.victimSet == 0);
    CHECK(!decision.freesIncomingSlot);
    CHECK(decision.nextVictimSet == 1);
    CHECK(decision.nextGlobal == 1);
    std::array<int, 2> set_victims{{0, 0}};
    set_victims[decision.victimSet] = decision.nextVictimSet;
    CHECK(set_victims[0] == 1 && set_victims[1] == 0);
    // Production retains the already-free slot in incoming set one; the
    // non-local payload victim must not be treated as its insertion slot.
    int incoming_free_slot = 2;
    if (!decision.globalPayloadVictim && decision.freesIncomingSlot)
        incoming_free_slot = decision.victim;
    CHECK(incoming_free_slot == 2);

    candidates[2] = {true, 0x0004};
    candidates[3] = {true, 0x0008};
    const auto local = Selector::select(
        [&candidates](int index) { return candidates[index]; },
        4, 2, 1, -1, false, true, 0, 0, 0);
    CHECK(!local.globalPayloadVictim);
    CHECK(local.victimSet == 1);
    CHECK(local.freesIncomingSlot);
    candidates[2] = {};
    candidates[3] = {};

    VirtualCombinePayloadStore::LineData masked{};
    CHECK(store.copyLine(refs[0], candidates[0].validWords, 8, masked) ==
          Result::Ok);
    for (size_t byte = 0; byte < 8; ++byte)
        CHECK(masked[byte] == first[byte]);

    // Model the exact masked WriteReq identity and require the matching ACK
    // before closure.  Payload ownership transfers only after issue accepts.
    Scoreboard scoreboard;
    CHECK(scoreboard.reset(1) == Scoreboard::Result::Accepted);
    Scoreboard::Metadata metadata;
    metadata.generation = 9;
    metadata.backingLine = 0;
    metadata.backingWordMask = candidates[0].validWords;
    metadata.pageCount = 1;
    metadata.pageWords[0] = {0, 1};
    Scoreboard::Identity identity;
    CHECK(scoreboard.insert(0x1000, metadata, identity) ==
          Scoreboard::Result::Accepted);
    CHECK(store.releaseMasked(refs[0], candidates[0].validWords) ==
          Result::Ok);
    candidates[0] = {};
    CHECK(store.used() == 1);

    const auto incoming = word(0x80);
    CHECK(store.allocate(incoming.data(), 8, refs[2][2]) == Result::Ok);
    candidates[2] = {true, 0x0004};
    CHECK(store.full());
    Scoreboard::Metadata retired;
    auto wrong = identity;
    ++wrong.transaction;
    CHECK(scoreboard.take(wrong, retired) ==
          Scoreboard::Result::WrongTransaction);
    CHECK(scoreboard.take(identity, retired) ==
          Scoreboard::Result::Accepted);
    CHECK(retired.backingWordMask == 0x0001);
    CHECK(retired.pageWords[0].words == 1);
    CHECK(scoreboard.empty());

    CHECK(store.release(refs[1][1]) == Result::Ok);
    CHECK(store.release(refs[2][2]) == Result::Ok);
    CHECK(store.empty());
    static_assert(Selector::packedGlobalPointerBits(4) == 2);
    static_assert(Selector::packedGlobalPointerBits(384) == 9);
}

int
main()
{
    checkFp32AllocateUpdateMaskedDrainAndReuse();
    checkFullFp32AndFp64Drains();
    checkErrorsFailClosed();
    checkGlobalFullCurrentSetEmptyMaskedAckClosure();
    return 0;
}
