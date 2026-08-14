#include <array>
#include <cstdint>
#include <cstdlib>
#include <iostream>

#include "mem/MAA/VirtualCombinePayloadStore.hh"

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

int
main()
{
    checkFp32AllocateUpdateMaskedDrainAndReuse();
    checkFullFp32AndFp64Drains();
    checkErrorsFailClosed();
    return 0;
}
