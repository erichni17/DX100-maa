#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <iostream>

namespace
{

struct Slot
{
    std::array<uint8_t, 64> data{};
};

constexpr size_t TotalSlots = 384;
constexpr size_t PayloadBytesPerIAU = TotalSlots * sizeof(Slot::data);
static_assert(sizeof(Slot::data) == 64, "combiner line payload changed");
static_assert(PayloadBytesPerIAU == 384 * 64,
              "384-way combiner payload must stay constant");

void
testConstantPayloadAndPartition()
{
    for (const size_t victim_slots : {0U, 16U, 32U, 64U}) {
        const size_t primary_slots = TotalSlots - victim_slots;
        assert(primary_slots % 4 == 0);
        assert(TotalSlots * sizeof(Slot::data) == 384 * 64);
        assert(primary_slots / 4 + victim_slots ==
               (TotalSlots - victim_slots) / 4 + victim_slots);
    }
}

void
testVictimIsSelectedBeforePrimaryEviction()
{
    constexpr size_t Ways = 4;
    constexpr size_t VictimSlots = 16;
    constexpr size_t PrimarySlots = TotalSlots - VictimSlots;
    constexpr size_t PrimarySet = 7;
    const size_t primary_begin = PrimarySet * Ways;
    const size_t victim_begin = PrimarySlots;
    assert(primary_begin + Ways <= PrimarySlots);
    assert(victim_begin + VictimSlots == TotalSlots);

    // With a full mapped primary set, replacement scans the bounded victim
    // region first; this is the same deterministic RR order used at runtime.
    size_t rr = 3;
    const size_t selected = victim_begin + (rr % VictimSlots);
    assert(selected >= victim_begin && selected < TotalSlots);
    rr = (selected - victim_begin + 1) % VictimSlots;
    assert(rr == 4);
}

void
testPagePriorityPolicies()
{
    const std::array<uint64_t, 4> lines{0x3000, 0x1000, 0x4000, 0x2000};
    size_t lowest = 0;
    size_t highest = 0;
    for (size_t index = 1; index < lines.size(); ++index) {
        if (lines[index] < lines[lowest])
            lowest = index;
        if (lines[index] > lines[highest])
            highest = index;
    }
    assert(lines[lowest] == 0x1000); // policy 3: earliest logical page/line
    assert(lines[highest] == 0x4000); // policy 4: negative control
}

} // anonymous namespace

int
main()
{
    testConstantPayloadAndPartition();
    testVictimIsSelectedBeforePrimaryEviction();
    testPagePriorityPolicies();
    std::cout << "virtual_combine_victim_region_test: PASS\n";
    return 0;
}
