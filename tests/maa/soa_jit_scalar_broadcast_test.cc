#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <type_traits>

#include "mem/MAA/SoaJitScalarBroadcast.hh"

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

using Broadcast = gem5::SoaJitScalarBroadcast;
using Status = Broadcast::Status;

constexpr std::size_t LogicalElements = 16 * 1024;
constexpr std::size_t DestinationElements = 64;
constexpr std::size_t LineBytes = 64;

struct Counters
{
    uint64_t indexIssues = 0;
    uint64_t indexResponses = 0;
    uint64_t predicateIssues = 0;
    uint64_t predicateResponses = 0;
    uint64_t aReadIssues = 0;
    uint64_t aReadResponses = 0;
    uint64_t scalarCaptures = 0;
    uint64_t valueReadIssues = 0;
    uint64_t valueReadResponses = 0;
    uint64_t aliasesApplied = 0;
    uint64_t aWriteIssues = 0;
    uint64_t aWriteResponses = 0;

    bool operator==(const Counters &other) const
    {
        return std::memcmp(this, &other, sizeof(*this)) == 0;
    }
};

template <class T>
struct Result
{
    std::array<T, DestinationElements> values{};
    Counters counters{};
    std::array<Broadcast::WriteIdentity,
               DestinationElements * sizeof(T) / LineBytes> completions{};
};

template <class T>
constexpr uint8_t datatype();
template <> constexpr uint8_t datatype<float>() { return 2; }
template <> constexpr uint8_t datatype<int32_t>() { return 1; }

template <class T>
Result<T>
runGeneration(std::size_t physicalElements, uint64_t generation, T scalar,
              uint8_t operation)
{
    CHECK(physicalElements == LogicalElements || physicalElements == 4096);
    std::array<uint32_t, LogicalElements> indices{};
    std::array<uint32_t, LogicalElements> predicates{};
    for (std::size_t logical = 0; logical < LogicalElements; ++logical) {
        // Dense duplicates include adjacent and page-crossing aliases.
        indices[logical] = static_cast<uint32_t>(
            ((logical / 3) * 13 + (logical % 11 == 0 ? 7 : 0)) %
            DestinationElements);
        predicates[logical] = logical % 5 != 0 && logical % 29 != 0;
    }

    Result<T> result;
    for (std::size_t word = 0; word < result.values.size(); ++word)
        result.values[word] =
            static_cast<T>(static_cast<int>(word) - 17);
    auto oracle = result.values;
    for (std::size_t logical = 0; logical < LogicalElements; ++logical) {
        if (!predicates[logical])
            continue;
        T &destination = oracle[indices[logical]];
        if (operation == 0)
            destination += scalar;
        else if (operation == 4)
            destination = destination < scalar ? destination : scalar;
        else
            destination = destination > scalar ? destination : scalar;
    }

    Broadcast broadcast;
    CHECK(broadcast.capture(&scalar, sizeof(scalar), datatype<T>(),
                            operation) == Status::Accepted);
    result.counters.scalarCaptures = 1;
    result.counters.indexIssues =
        result.counters.indexResponses =
            LogicalElements * sizeof(uint32_t) / LineBytes;
    result.counters.predicateIssues =
        result.counters.predicateResponses =
            LogicalElements * sizeof(uint32_t) / LineBytes;

    constexpr std::size_t wordsPerLine = LineBytes / sizeof(T);
    constexpr std::size_t destinationLines =
        DestinationElements / wordsPerLine;
    std::array<bool, destinationLines> touched{};
    for (std::size_t line = 0; line < destinationLines; ++line) {
        // Physical geometry changes page boundaries only. The scan and every
        // duplicate-index chain retain logical insertion order.
        for (std::size_t page = 0; page < LogicalElements;
             page += physicalElements) {
            const std::size_t end = page + physicalElements;
            for (std::size_t logical = page; logical < end; ++logical) {
                if (!predicates[logical])
                    continue;
                const std::size_t index = indices[logical];
                if (index / wordsPerLine != line)
                    continue;
                CHECK(broadcast.apply(&result.values[index]) ==
                      Status::Accepted);
                touched[line] = true;
                result.counters.aliasesApplied++;
            }
        }
        if (!touched[line])
            continue;
        const Broadcast::WriteIdentity identity{
            generation, static_cast<uint16_t>(line),
            0x8000 + line * LineBytes};
        result.completions[line] = identity;
        result.counters.aReadIssues++;
        result.counters.aReadResponses++;
        result.counters.aWriteIssues++;
        CHECK(Broadcast::validateCompletion(identity, identity) ==
              Status::Accepted);
        result.counters.aWriteResponses++;
    }
    for (std::size_t index = 0; index < result.values.size(); ++index) {
        if constexpr (std::is_floating_point_v<T>)
            CHECK(std::memcmp(&result.values[index], &oracle[index],
                              sizeof(T)) == 0);
        else
            CHECK(result.values[index] == oracle[index]);
    }
    return result;
}

template <class T>
void checkEqual(const Result<T> &first, const Result<T> &second)
{
    CHECK(first.counters == second.counters);
    for (std::size_t index = 0; index < first.values.size(); ++index) {
        if constexpr (std::is_floating_point_v<T>)
            CHECK(std::fabs(first.values[index] - second.values[index]) <
                  1.0e-5);
        else
            CHECK(first.values[index] == second.values[index]);
    }
}

void exactTwoGenerationAndGeometryTest()
{
    const auto fpPhysical16 =
        runGeneration<float>(LogicalElements, 101, 1.25F, 0);
    const auto fpPhysical4 = runGeneration<float>(4096, 101, 1.25F, 0);
    checkEqual(fpPhysical16, fpPhysical4);
    CHECK(fpPhysical16.counters.indexIssues == 1024);
    CHECK(fpPhysical16.counters.predicateIssues == 1024);
    CHECK(fpPhysical16.counters.valueReadIssues == 0);
    CHECK(fpPhysical16.counters.valueReadResponses == 0);
    CHECK(fpPhysical16.counters.aliasesApplied == 12655);
    CHECK(fpPhysical16.counters.aReadIssues == 4);
    CHECK(fpPhysical16.counters.aReadIssues ==
          fpPhysical16.counters.aReadResponses);
    CHECK(fpPhysical16.counters.aWriteIssues ==
          fpPhysical16.counters.aWriteResponses);

    const auto intPhysical16 =
        runGeneration<int32_t>(LogicalElements, 102, 23, 5);
    const auto intPhysical4 = runGeneration<int32_t>(4096, 102, 23, 5);
    checkEqual(intPhysical16, intPhysical4);
    CHECK(intPhysical16.counters.aliasesApplied ==
          fpPhysical16.counters.aliasesApplied);
    for (const int32_t value : intPhysical16.values)
        CHECK(value >= 23);

    // A delayed generation-101 completion cannot close generation 102 even
    // when the context and physical A line are reused exactly.
    const auto stale = fpPhysical16.completions[0];
    const auto current = intPhysical16.completions[0];
    CHECK(stale.context == current.context &&
          stale.address == current.address);
    CHECK(Broadcast::validateCompletion(current, stale) ==
          Status::StaleCompletion);
    CHECK(Broadcast::validateCompletion(current, current) ==
          Status::Accepted);
}

void admissionAndFixedStateTest()
{
    CHECK(Broadcast::FixedPayloadBytes == 8);
    CHECK(sizeof(Broadcast) <= 16);
    CHECK(Broadcast::validateRegisters(4, 1, 0, 1, 2, 8) ==
          Status::Accepted);
    CHECK(Broadcast::validateRegisters(4, 2, 0, 1, 5, 8) ==
          Status::RegisterAlias);
    CHECK(Broadcast::validateRegisters(7, 2, 0, 1, 2, 8) ==
          Status::InvalidRegisterSpan);
    CHECK(Broadcast::validateRegisters(4, 1, 0, 0, 2, 8) ==
          Status::RegisterAlias);
    float value = 1.0F;
    Broadcast broadcast;
    CHECK(broadcast.capture(&value, sizeof(value), 5, 0) ==
          Status::InvalidDatatype);
    CHECK(broadcast.capture(&value, sizeof(value), 2, 2) ==
          Status::InvalidOperation);
    CHECK(Broadcast::validateCompletion({0, 0, 1}, {0, 0, 1}) ==
          Status::InvalidGeneration);
}

} // anonymous namespace

int
main()
{
    admissionAndFixedStateTest();
    exactTwoGenerationAndGeometryTest();
    std::cout << "SOA_JIT_SCALAR_BROADCAST_PASS\n";
    return 0;
}
