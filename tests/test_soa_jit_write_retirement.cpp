#include <array>
#include <cstdint>
#include <cstdlib>
#include <iostream>

#include "mem/MAA/SoaJitWriteRetirement.hh"

using Tracker = gem5::SoaJitWriteRetirement;

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;        \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

int
main()
{
    using Result = Tracker::Result;
    static_assert(Tracker::Credits == 8);
    static_assert(Tracker::CreditBits == 3);
    static_assert(Tracker::PersistentStateBits == 1168);
    static_assert(Tracker::PersistentStateBytes == 146);
    static_assert(Tracker::TransientResponseCreditTagBits == 3);
    static_assert(Tracker::MaxTransientResponseCreditTagBits == 24);
    static_assert(Tracker::MaxTransientResponseCreditTagBytes == 3);
    static_assert(Tracker::MaxTransientPacketPayloadBytes == 512);
    static_assert(sizeof(Tracker) >= Tracker::PersistentStateBytes);
    static_assert(sizeof(Tracker::Identity) <= 32);

    Tracker tracker;
    CHECK(tracker.assertInvariants());
    CHECK(tracker.begin(0) == Result::InvalidGeneration);
    CHECK(tracker.begin(7) == Result::Accepted);
    CHECK(tracker.begin(8) == Result::Busy);

    Tracker::Identity invalid;
    CHECK(tracker.reserve(8, 0x1000, &invalid) ==
          Result::InvalidGeneration);
    CHECK(tracker.reserve(7, 0x1004, &invalid) ==
          Result::InvalidAddress);

    std::array<Tracker::Identity, Tracker::Credits> identities{};
    for (size_t credit = 0; credit < Tracker::Credits; ++credit) {
        CHECK(tracker.reserve(7, 0x1000 + credit * Tracker::LineBytes,
                              &identities[credit]) == Result::Accepted);
        CHECK(identities[credit].credit == credit);
        CHECK(identities[credit].issueSequence == credit + 1);
    }
    CHECK(tracker.occupied() == Tracker::Credits);
    CHECK(tracker.reserved() == Tracker::Credits);
    CHECK(tracker.creditHighWater() == Tracker::Credits);
    CHECK(tracker.reserve(7, 0x3000, &invalid) == Result::Full);
    CHECK(tracker.reset() == Result::Busy);

    for (const auto &identity : identities)
        CHECK(tracker.commit(identity) == Result::Accepted);
    CHECK(tracker.reserved() == 0);
    CHECK(tracker.awaitingResponses() == Tracker::Credits);
    CHECK(tracker.finish() == Result::NotComplete);
    CHECK(tracker.assertInvariants());

    auto stale = identities[0];
    stale.generation++;
    CHECK(tracker.acknowledge(stale) == Result::WrongGeneration);
    auto wrongSequence = identities[0];
    wrongSequence.issueSequence++;
    CHECK(tracker.acknowledge(wrongSequence) == Result::WrongSequence);
    auto wrongAddress = identities[0];
    wrongAddress.address += Tracker::LineBytes;
    CHECK(tracker.acknowledge(wrongAddress) == Result::WrongAddress);
    auto wrongCredit = identities[0];
    wrongCredit.credit = Tracker::Credits;
    CHECK(tracker.acknowledge(wrongCredit) == Result::CreditOutOfRange);

    CHECK(tracker.acknowledge(identities[0]) == Result::Accepted);
    CHECK(tracker.acknowledge(identities[0]) == Result::NotOutstanding);

    Tracker::Identity replacement;
    CHECK(tracker.reserve(7, identities[0].address, &replacement) ==
          Result::Accepted);
    CHECK(replacement.credit == identities[0].credit);
    CHECK(replacement.issueSequence != identities[0].issueSequence);
    CHECK(tracker.commit(replacement) == Result::Accepted);
    CHECK(tracker.acknowledge(identities[0]) == Result::WrongSequence);

    for (size_t credit = 1; credit < Tracker::Credits; ++credit)
        CHECK(tracker.acknowledge(identities[credit]) == Result::Accepted);
    CHECK(tracker.acknowledge(replacement) == Result::Accepted);
    CHECK(tracker.empty());
    CHECK(tracker.reservationCount() == Tracker::Credits + 1);
    CHECK(tracker.issueCount() == Tracker::Credits + 1);
    CHECK(tracker.responseCount() == Tracker::Credits + 1);
    CHECK(tracker.finish() == Result::Accepted);
    CHECK(tracker.acknowledge(replacement) == Result::Inactive);
    CHECK(tracker.reset() == Result::Accepted);
    CHECK(tracker.assertInvariants());

    CHECK(tracker.begin(8) == Result::Accepted);
    Tracker::Identity nextGeneration;
    CHECK(tracker.reserve(8, 0x4000, &nextGeneration) == Result::Accepted);
    CHECK(tracker.commit(nextGeneration) == Result::Accepted);
    CHECK(tracker.acknowledge(replacement) == Result::WrongGeneration);
    CHECK(tracker.acknowledge(nextGeneration) == Result::Accepted);
    CHECK(tracker.finish() == Result::Accepted);

    std::cout << "SoA/JIT compact write-retirement tests passed\n";
    return 0;
}
