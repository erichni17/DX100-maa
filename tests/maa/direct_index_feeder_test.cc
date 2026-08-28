#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>

#include "mem/MAA/DirectIndexFeeder.hh"

namespace
{

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << '\n';             \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

using Feeder = gem5::maa::DirectIndexFeeder;
using Result = Feeder::Result;

std::array<Feeder::Reservation, Feeder::WordsPerLine>
reservations(uint32_t first_owner, size_t count, size_t first_word = 0)
{
    std::array<Feeder::Reservation, Feeder::WordsPerLine> result{};
    for (size_t idx = 0; idx < count; ++idx) {
        result[idx] = {first_owner + static_cast<uint32_t>(idx),
                       static_cast<uint8_t>(first_word + idx)};
    }
    return result;
}

std::array<uint8_t, Feeder::LineBytes>
payload(uint32_t base)
{
    std::array<uint8_t, Feeder::LineBytes> result{};
    for (size_t word = 0; word < Feeder::WordsPerLine; ++word) {
        const uint32_t value = base + static_cast<uint32_t>(word);
        std::memcpy(result.data() + word * sizeof(value), &value,
                    sizeof(value));
    }
    return result;
}

void
testAllCapacitiesAndFullBehavior()
{
    for (size_t capacity = 1; capacity <= Feeder::MaxLines; ++capacity) {
        Feeder feeder;
        CHECK(feeder.configure(capacity, 4) == Result::Accepted);
        const auto one = reservations(0, 1);
        for (size_t slot = 0; slot < capacity; ++slot) {
            auto request = one;
            request[0].logical = static_cast<uint32_t>(slot);
            CHECK(feeder.allocate(0x1000 + slot * Feeder::LineBytes, 7,
                                  request, 1, slot / 4) == Result::Accepted);
        }
        CHECK(feeder.full());
        CHECK(feeder.linesUsed() == capacity);
        CHECK(feeder.wordsOwned() == capacity);
        CHECK(feeder.allocate(0x80000, 7, one, 1, capacity + 1) ==
              Result::Full);
        CHECK(feeder.maxLinesUsed() == capacity);
        feeder.reset();
        CHECK(feeder.empty());
        CHECK(!feeder.full());
        CHECK(feeder.capacity() == capacity);
    }
    Feeder invalid;
    CHECK(invalid.configure(0, 1) == Result::InvalidConfiguration);
    CHECK(invalid.configure(Feeder::MaxLines + 1, 1) ==
          Result::InvalidConfiguration);
    CHECK(invalid.configure(64, 3) == Result::InvalidConfiguration);
}

void
testFiniteIssueWidths()
{
    for (size_t width : {size_t{1}, size_t{2}, size_t{4}}) {
        Feeder feeder;
        CHECK(feeder.configure(8, width) == Result::Accepted);
        const auto one = reservations(10, 1);
        for (size_t issued = 0; issued < width; ++issued) {
            auto request = one;
            request[0].logical += static_cast<uint32_t>(issued);
            CHECK(feeder.allocate(0x2000 + issued * Feeder::LineBytes, 1,
                                  request, 1, 100) == Result::Accepted);
        }
        auto blocked = one;
        blocked[0].logical = 99;
        CHECK(feeder.allocate(0x3000, 1, blocked, 1, 100) ==
              Result::IssueWidthLimited);
        CHECK(feeder.allocate(0x3000, 1, blocked, 1, 101) ==
              Result::Accepted);
        const auto &counters = feeder.counters();
        CHECK(counters.linesIssued == width + 1);
        CHECK(counters.issueCycles == 2);
        CHECK(counters.issueWidthLimited == 1);
        CHECK(counters.maxLinesIssuedPerCycle == width);
    }
}

void
testResponseMatchingAndOutOfOrderReadiness()
{
    Feeder feeder;
    CHECK(feeder.configure(4, 2) == Result::Accepted);
    const auto first = reservations(20, 3, 2);
    const auto second = reservations(40, 2, 10);
    CHECK(feeder.allocate(0x4000, 9, first, 3, 10) == Result::Accepted);
    CHECK(feeder.allocate(0x5000, 9, second, 2, 10) == Result::Accepted);
    CHECK(feeder.pendingLines() == 2);
    CHECK(feeder.readyLines() == 0);
    CHECK(feeder.wordsValid() == 0);

    const auto second_payload = payload(200);
    CHECK(feeder.respond(0x5000, second_payload.data(),
                         second_payload.size()) == Result::Accepted);
    CHECK(feeder.pendingLines() == 1);
    CHECK(feeder.readyLines() == 1);
    CHECK(feeder.wordsValid() == 2);
    Feeder::Word word{};
    CHECK(feeder.read(20, 9, word) == Result::NotReady);
    CHECK(feeder.read(40, 8, word) == Result::StalePhase);
    CHECK(feeder.read(40, 9, word) == Result::Accepted);
    CHECK(word.value == 210);
    CHECK(word.lineTag == 0x5000);
    CHECK(word.wordAddress == 0x5000 + 10 * sizeof(uint32_t));
    CHECK(word.logical == 40);
    CHECK(feeder.respond(0x5000, second_payload.data(),
                         second_payload.size()) == Result::NotPending);
    CHECK(feeder.respond(0x6000, second_payload.data(),
                         second_payload.size()) == Result::NotFound);

    const auto first_payload = payload(100);
    CHECK(feeder.respond(0x4000, first_payload.data(),
                         first_payload.size()) == Result::Accepted);
    CHECK(feeder.pendingLines() == 0);
    CHECK(feeder.readyLines() == 2);
    CHECK(feeder.wordsValid() == 5);
    CHECK(feeder.maxWordsValid() == 5);
    CHECK(feeder.read(22, 9, word) == Result::Accepted);
    CHECK(word.value == 104);
}

void
testConsumptionAddressReuseAndReset()
{
    Feeder feeder;
    CHECK(feeder.configure(1, 1) == Result::Accepted);
    const auto owners = reservations(100, 2, 7);
    CHECK(feeder.allocate(0x7000, 3, owners, 2, 1) == Result::Accepted);
    const auto data = payload(300);
    CHECK(feeder.respond(0x7000, data.data(), data.size()) ==
          Result::Accepted);
    CHECK(feeder.consume(100, 999, 3, false) == Result::ValueChanged);
    CHECK(feeder.wordsOwned() == 2);
    CHECK(feeder.consume(100, 307, 2, false) == Result::StalePhase);
    CHECK(feeder.consume(100, 307, 3, true) == Result::Accepted);
    CHECK(feeder.linesUsed() == 1);
    CHECK(feeder.wordsOwned() == 1);
    CHECK(feeder.consume(101, 308, 3, false) == Result::Accepted);
    CHECK(feeder.empty());

    // The same physical tag can be allocated again after the last prior
    // owner releases it; a ready or pending duplicate cannot alias it.
    auto reused = reservations(200, 1, 1);
    CHECK(feeder.allocate(0x7000, 4, reused, 1, 2) == Result::Accepted);
    CHECK(feeder.allocate(0x7000, 4, reused, 1, 3) ==
          Result::Full);
    CHECK(feeder.hasPending(0x7000));
    feeder.reset();
    CHECK(feeder.empty());
    CHECK(!feeder.hasPending(0x7000));
    CHECK(feeder.counters().linesIssued == 0);
}

void
testReservationFailuresAreAtomic()
{
    Feeder feeder;
    CHECK(feeder.configure(4, 4) == Result::Accepted);
    auto duplicate_word = reservations(0, 2);
    duplicate_word[1].word = duplicate_word[0].word;
    CHECK(feeder.allocate(0x8000, 1, duplicate_word, 2, 1) ==
          Result::InvalidReservation);
    CHECK(feeder.empty());

    const auto valid = reservations(11, 1, 3);
    CHECK(feeder.allocate(0x8000, 1, valid, 1, 1) == Result::Accepted);
    CHECK(feeder.allocate(0x8000, 1, reservations(12, 1, 4), 1, 1) ==
          Result::DuplicateTag);
    auto duplicate_owner = reservations(11, 1, 4);
    CHECK(feeder.allocate(0x9000, 1, duplicate_owner, 1, 1) ==
          Result::DuplicateOwner);
    CHECK(feeder.linesUsed() == 1);
    CHECK(feeder.wordsOwned() == 1);
    CHECK(feeder.allocate(0x9001, 1, reservations(12, 1), 1, 2) ==
          Result::InvalidReservation);
}

void
testPackedHardwareAccounting()
{
    static_assert(Feeder::packedPayloadBits(64) == 32768);
    static_assert(Feeder::packedControlBits(1, 48, 14) == 342);
    static_assert(Feeder::packedControlBits(64, 48, 14) == 19626);
    static_assert(Feeder::packedControlBits(128, 48, 14) == 39211);
    CHECK(Feeder::packedPayloadBits(128) == 65536);
    std::cout << "direct index packed selected64 payload_bits="
              << Feeder::packedPayloadBits(64)
              << " control_bits="
              << Feeder::packedControlBits(64, 48, 14)
              << " host_size_excluded=" << sizeof(Feeder) << '\n';
}

} // anonymous namespace

int
main()
{
    testAllCapacitiesAndFullBehavior();
    testFiniteIssueWidths();
    testResponseMatchingAndOutOfOrderReadiness();
    testConsumptionAddressReuseAndReset();
    testReservationFailuresAreAtomic();
    testPackedHardwareAccounting();
    std::cout << "fixed direct index feeder tests passed\n";
    return 0;
}
