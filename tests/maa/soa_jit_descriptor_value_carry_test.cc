#include <array>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>

#include "mem/MAA/Tables.hh"

using gem5::OffsetTableEntry;

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;         \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

int
main()
{
    static_assert(sizeof(OffsetTableEntry) == 16);

    OffsetTableEntry fp32{-1, 7, 11, -1};
    constexpr uint32_t fp32_bits = 0x7fc01234U;
    fp32.setCarriedValue(fp32_bits);
    CHECK(static_cast<uint32_t>(fp32.carriedValue()) == fp32_bits);
    CHECK(fp32.wid == 7);
    CHECK(fp32.next_itr == 11);

    OffsetTableEntry fp64{-1, 3, -1, -1};
    constexpr uint64_t fp64_bits = 0xfff8000000004321ULL;
    fp64.setCarriedValue(fp64_bits);
    CHECK(fp64.carriedValue() == fp64_bits);
    CHECK(fp64.wid == 3);
    CHECK(fp64.next_itr == -1);

    std::array<OffsetTableEntry, 4> duplicate_chain{};
    constexpr std::array<uint64_t, 4> operands = {
        0x4b800000U, 0x3f800000U, 0xcb800000U, 0x3f800000U,
    };
    for (size_t index = 0; index < duplicate_chain.size(); ++index) {
        duplicate_chain[index].wid = 5;
        duplicate_chain[index].next_itr =
            index + 1 == duplicate_chain.size() ? -1 : index + 1;
        duplicate_chain[index].setCarriedValue(operands[index]);
    }
    int cursor = 0;
    for (const uint64_t expected : operands) {
        CHECK(cursor >= 0);
        CHECK(duplicate_chain[cursor].carriedValue() == expected);
        CHECK(duplicate_chain[cursor].wid == 5);
        cursor = duplicate_chain[cursor].next_itr;
    }
    CHECK(cursor == -1);

    std::cout << "SOA_JIT_DESCRIPTOR_VALUE_CARRY_UNIT_PASS\n";
    return 0;
}
