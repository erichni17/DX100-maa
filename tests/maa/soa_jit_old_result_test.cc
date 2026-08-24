#include <array>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>

#include "mem/MAA/LogicalTileRmwContract.hh"
#include "mem/MAA/SoaJitOldResultBuffer.hh"

using gem5::SoaJitOldResultBuffer;
using Oracle = gem5::maa::LogicalTileRmwContract;

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;        \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

static void
applyRequest(const SoaJitOldResultBuffer::Request &request,
             std::array<float, 64> &result)
{
    const size_t line = (request.identity.lineAddress - 0x1000) / 64;
    for (size_t word = 0; word < 16; ++word) {
        if ((request.identity.validWords & (1U << word)) == 0)
            continue;
        CHECK(request.contexts[word] < SoaJitOldResultBuffer::MaxContexts);
        std::memcpy(&result[line * 16 + word],
                    request.payload + word * sizeof(float), sizeof(float));
    }
}

static void
runGeneration(uint64_t generation, std::array<float, 64> &target,
              std::array<float, 64> &result)
{
    constexpr std::array<uint32_t, 8> indices{{5, 5, 9, 5, 9, 17, 5, 17}};
    constexpr std::array<float, 8> values{{1, 2, 4, 8, 16, 32, 64, 128}};
    constexpr std::array<uint8_t, 8> selected{{1, 1, 0, 1, 1, 0, 1, 1}};
    std::array<uint64_t, 8> oracleWords{};
    std::array<uint8_t, 8> oracleValid{};
    Oracle::ResultPage page{oracleWords.data(), oracleValid.data(), 8};
    Oracle oracle({2, 64, 8}, generation,
                  Oracle::ResultMode::PageBackedOldValue);
    SoaJitOldResultBuffer buffer;
    CHECK(buffer.begin(generation, 0x1000, indices.size()) ==
          SoaJitOldResultBuffer::Result::Accepted);

    for (uint32_t ordinal = 0; ordinal < indices.size(); ++ordinal) {
        CHECK(oracle.insert(ordinal % 2, indices[ordinal]) ==
              Oracle::Status::Accepted);
        CHECK(oracle.decidePredicate(ordinal, selected[ordinal], &page,
                                     ordinal) == Oracle::Status::Accepted);
    }
    CHECK(oracle.closeSelection() == Oracle::Status::Accepted);

    size_t selectedCount = 0;
    for (uint32_t ordinal = 0; ordinal < indices.size(); ++ordinal) {
        if (!selected[ordinal])
            continue;
        const uint16_t context = ordinal % 2;
        Oracle::Ticket ticket;
        CHECK(oracle.issue(context, ordinal, &ticket) ==
              Oracle::Status::Accepted);
        float old = target[indices[ordinal]];
        uint32_t oldBits = 0;
        std::memcpy(&oldBits, &old, sizeof(oldBits));
        CHECK(oracle.acceptReadEx(ticket, sizeof(float), oldBits) ==
              Oracle::Status::Accepted);
        CHECK(buffer.capture(generation, context, ordinal,
                             reinterpret_cast<const uint8_t *>(&old),
                             sizeof(old)) ==
              SoaJitOldResultBuffer::Result::Accepted);
        target[indices[ordinal]] += values[ordinal];
        CHECK(oracle.acceptWriteResp(ticket) == Oracle::Status::Accepted);
        ++selectedCount;
    }
    CHECK(oracle.complete());
    CHECK(buffer.closeSelection(selectedCount,
                                indices.size() - selectedCount) ==
          SoaJitOldResultBuffer::Result::Accepted);

    SoaJitOldResultBuffer::Request request;
    while (buffer.issue(&request, true) ==
           SoaJitOldResultBuffer::Result::Accepted) {
        applyRequest(request, result);
        auto stale = request.identity;
        stale.generation++;
        CHECK(buffer.acknowledge(stale) ==
              SoaJitOldResultBuffer::Result::InvalidGeneration);
        CHECK(buffer.acknowledge(request.identity) ==
              SoaJitOldResultBuffer::Result::Accepted);
        CHECK(buffer.acknowledge(request.identity) ==
              SoaJitOldResultBuffer::Result::NotOutstanding);
    }
    CHECK(buffer.complete());
    CHECK(buffer.finish() == SoaJitOldResultBuffer::Result::Accepted);

    for (size_t ordinal = 0; ordinal < indices.size(); ++ordinal) {
        if (!selected[ordinal]) {
            CHECK(std::isnan(result[ordinal]));
            continue;
        }
        uint32_t resultBits = 0;
        std::memcpy(&resultBits, &result[ordinal], sizeof(resultBits));
        CHECK(oracleValid[ordinal] == 1);
        CHECK(resultBits == oracleWords[ordinal]);
    }
}

int
main()
{
    static_assert(sizeof(SoaJitOldResultBuffer) < 2048);
    std::array<float, 64> target{};
    std::array<float, 64> result{};
    target[5] = 10;
    target[9] = 20;
    target[17] = 30;
    result.fill(std::nanf(""));
    runGeneration(1, target, result);
    CHECK(target[5] == 85);
    CHECK(target[9] == 36);
    CHECK(target[17] == 158);

    result.fill(std::nanf(""));
    runGeneration(2, target, result);
    CHECK(target[5] == 160);
    CHECK(target[9] == 52);
    CHECK(target[17] == 286);

    SoaJitOldResultBuffer allFalse;
    CHECK(allFalse.begin(3, 0x2000, 16) ==
          SoaJitOldResultBuffer::Result::Accepted);
    CHECK(allFalse.closeSelection(0, 16) ==
          SoaJitOldResultBuffer::Result::Accepted);
    CHECK(allFalse.complete());
    CHECK(allFalse.issues() == 0);

    std::cout << "SOA_JIT_OLD_RESULT_TEST_PASS\n";
    return 0;
}
