#include <array>
#include <cstdint>
#include <iostream>
#include <limits>

#include "mem/MAA/VirtualRetirementScoreboard.hh"

namespace
{

using Scoreboard = gem5::maa::VirtualRetirementScoreboard;
using Identity = Scoreboard::Identity;
using Result = Scoreboard::Result;

static_assert(Scoreboard::ConservativeBytesPerEntry == 44);
static_assert(Scoreboard::ConservativeFixedBytes == 8);
static_assert(Scoreboard::ConservativeTotalBytes == 2824);

#define CHECK(condition) \
    do { \
        if (!(condition)) { \
            std::cerr << "check failed line " << __LINE__ << ": " \
                      << #condition << "\n"; \
            return 1; \
        } \
    } while (false)

Scoreboard::Metadata
metadata(uint64_t generation, int page)
{
    Scoreboard::Metadata value;
    value.generation = generation;
    value.backingLine = page * 256;
    value.backingWordMask = 0x00ff;
    value.pageCount = 1;
    value.pageWords[0] = {page, 8};
    return value;
}

bool
sameIdentity(const Identity &left, const Identity &right)
{
    return left.address == right.address &&
           left.generation == right.generation &&
           left.transaction == right.transaction;
}

} // anonymous namespace

int
main()
{
    Scoreboard scoreboard;
    CHECK(scoreboard.reset(0) == Result::Invalid);
    CHECK(scoreboard.reset(Scoreboard::MaxEntries + 1) == Result::Invalid);
    CHECK(scoreboard.reset(Scoreboard::MaxEntries) == Result::Accepted);

    std::array<Identity, Scoreboard::MaxEntries> identities{};
    for (uint32_t entry = 0; entry < Scoreboard::MaxEntries; ++entry) {
        const uint64_t address = 0x1000 + entry * 64;
        CHECK(scoreboard.insert(address, metadata(entry + 1, entry % 4),
                                identities[entry]) == Result::Accepted);
        CHECK(identities[entry].address == address);
        CHECK(identities[entry].generation == entry + 1);
        CHECK(identities[entry].transaction != 0);
        if (entry != 0) {
            CHECK(identities[entry].transaction >
                  identities[entry - 1].transaction);
        }
    }
    CHECK(scoreboard.full());
    CHECK(scoreboard.size() == Scoreboard::MaxEntries);

    Identity rejected{1, 1, 1};
    CHECK(scoreboard.insert(0x1000, metadata(99, 0), rejected) ==
          Result::Duplicate);
    CHECK(sameIdentity(rejected, {}));
    CHECK(scoreboard.insert(0x9000, metadata(99, 0), rejected) ==
          Result::Full);
    CHECK(sameIdentity(rejected, {}));
    CHECK(scoreboard.reset(16) == Result::Busy);

    const uint64_t reusedAddress = 0x1000 + 17 * 64;
    const auto *found = scoreboard.find(reusedAddress);
    CHECK(found != nullptr);
    CHECK(found->generation == 18);
    CHECK(found->backingLine == 256);
    CHECK(found->backingWordMask == 0x00ff);
    CHECK(found->pageCount == 1);
    CHECK(found->pageWords[0].page == 1);
    CHECK(found->pageWords[0].words == 8);

    Scoreboard::Metadata retired;
    const Identity live = identities[17];
    Identity wrong = live;
    wrong.address += 64;
    CHECK(scoreboard.take(wrong, retired) == Result::WrongAddress);
    CHECK(scoreboard.contains(reusedAddress));
    wrong = live;
    ++wrong.generation;
    CHECK(scoreboard.take(wrong, retired) == Result::WrongGeneration);
    CHECK(scoreboard.contains(reusedAddress));
    wrong = live;
    wrong.transaction = std::numeric_limits<uint64_t>::max();
    CHECK(scoreboard.take(wrong, retired) == Result::WrongTransaction);
    CHECK(scoreboard.contains(reusedAddress));
    CHECK(scoreboard.take({}, retired) == Result::Invalid);
    CHECK(scoreboard.contains(reusedAddress));

    const Identity missing{0xdeadbeef, 1,
                           std::numeric_limits<uint64_t>::max()};
    CHECK(scoreboard.take(missing, retired) == Result::NotFound);
    CHECK(scoreboard.take(live, retired) == Result::Accepted);
    CHECK(retired.generation == 18);
    CHECK(!scoreboard.contains(reusedAddress));
    CHECK(scoreboard.size() == Scoreboard::MaxEntries - 1);
    CHECK(scoreboard.take(live, retired) == Result::NotFound);

    // Reuse the exact address and generation. Only the new transaction may
    // retire it; delayed and duplicate ACKs retain no authority.
    Identity replacement;
    CHECK(scoreboard.insert(reusedAddress, metadata(18, 1), replacement) ==
          Result::Accepted);
    CHECK(replacement.address == live.address);
    CHECK(replacement.generation == live.generation);
    CHECK(replacement.transaction != live.transaction);
    CHECK(scoreboard.take(live, retired) == Result::WrongTransaction);
    CHECK(scoreboard.contains(reusedAddress));
    wrong = replacement;
    ++wrong.generation;
    CHECK(scoreboard.take(wrong, retired) == Result::WrongGeneration);
    CHECK(scoreboard.contains(reusedAddress));
    wrong = replacement;
    wrong.address += 64;
    CHECK(scoreboard.take(wrong, retired) == Result::WrongAddress);
    CHECK(scoreboard.contains(reusedAddress));
    CHECK(scoreboard.take(replacement, retired) == Result::Accepted);
    CHECK(scoreboard.take(replacement, retired) == Result::NotFound);

    for (uint32_t entry = 0; entry < Scoreboard::MaxEntries; ++entry) {
        if (entry == 17)
            continue;
        CHECK(scoreboard.take(identities[entry], retired) ==
              Result::Accepted);
    }
    CHECK(scoreboard.empty());

    // The accepted 32-credit configuration remains exactly usable, while the
    // compile-time ceiling above retains 64-credit compatibility.
    CHECK(scoreboard.reset(32) == Result::Accepted);
    std::array<Identity, 32> accepted32{};
    for (uint32_t entry = 0; entry < accepted32.size(); ++entry) {
        CHECK(scoreboard.insert(0x40000 + entry * 64,
                                metadata(100 + entry, entry % 4),
                                accepted32[entry]) == Result::Accepted);
    }
    CHECK(scoreboard.full());
    CHECK(scoreboard.insert(0x50000, metadata(200, 0), rejected) ==
          Result::Full);
    for (const auto &identity : accepted32)
        CHECK(scoreboard.take(identity, retired) == Result::Accepted);
    CHECK(scoreboard.empty());

    // Resetting live-entry storage must not recycle transaction identity.
    Identity beforeReset;
    CHECK(scoreboard.insert(0x60000, metadata(300, 0), beforeReset) ==
          Result::Accepted);
    CHECK(scoreboard.take(beforeReset, retired) == Result::Accepted);
    CHECK(scoreboard.reset(32) == Result::Accepted);
    Identity afterReset;
    CHECK(scoreboard.insert(0x60000, metadata(300, 0), afterReset) ==
          Result::Accepted);
    CHECK(afterReset.transaction != beforeReset.transaction);
    CHECK(scoreboard.take(beforeReset, retired) == Result::WrongTransaction);
    CHECK(scoreboard.take(afterReset, retired) == Result::Accepted);

    auto invalid = metadata(1, 0);
    invalid.backingWordMask = 0;
    CHECK(scoreboard.insert(1, invalid, rejected) == Result::Invalid);
    CHECK(sameIdentity(rejected, {}));
    invalid = metadata(1, 0);
    invalid.pageCount = Scoreboard::MaxPagesPerEntry + 1;
    CHECK(scoreboard.insert(1, invalid, rejected) == Result::Invalid);
    CHECK(sameIdentity(rejected, {}));

    std::cout << "virtual_retirement_scoreboard_test: PASS\n";
    return 0;
}
