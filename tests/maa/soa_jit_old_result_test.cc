#include <array>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>

#include "mem/MAA/LogicalTileRmwContract.hh"
#include "mem/MAA/SoaJitOldResultBuffer.hh"

using gem5::SoaJitOldResultBuffer;
using PressurePolicy = SoaJitOldResultBuffer::PressurePolicy;
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

static void
runPressurePacking()
{
    constexpr uint64_t Base = 0x5000;
    constexpr uint32_t Lines = 9;
    constexpr uint32_t LogicalWords =
        Lines * SoaJitOldResultBuffer::WordsPerLine;
    constexpr uint32_t PackedLines = 7;
    constexpr uint32_t SelectedWords = 1 +
        PackedLines * SoaJitOldResultBuffer::WordsPerLine + 1;
    SoaJitOldResultBuffer buffer;
    CHECK(buffer.begin(6, Base, LogicalWords) ==
          SoaJitOldResultBuffer::Result::Accepted);

    for (uint32_t line = 0; line < SoaJitOldResultBuffer::Credits; ++line) {
        const float old = static_cast<float>(line);
        CHECK(buffer.capture(
                  6, line, line * SoaJitOldResultBuffer::WordsPerLine,
                  reinterpret_cast<const uint8_t *>(&old), sizeof(old)) ==
              SoaJitOldResultBuffer::Result::Accepted);
    }
    const float ninth = 9.0F;
    CHECK(buffer.capture(
              6, 9, 8 * SoaJitOldResultBuffer::WordsPerLine,
              reinterpret_cast<const uint8_t *>(&ninth), sizeof(ninth)) ==
          SoaJitOldResultBuffer::Result::Full);

    SoaJitOldResultBuffer::Request pressure;
    CHECK(buffer.issueForPressure(&pressure, 1, PressurePolicy::Densest) ==
          SoaJitOldResultBuffer::Result::Accepted);
    CHECK(pressure.identity.lineAddress == Base);
    CHECK(pressure.identity.validWords == 1);
    CHECK(buffer.filling() == 7);
    CHECK(buffer.awaitingResponses() == 1);
    SoaJitOldResultBuffer::Request blocked;
    CHECK(buffer.issueForPressure(&blocked, 1, PressurePolicy::Densest) ==
          SoaJitOldResultBuffer::Result::NoReadyLine);
    CHECK(blocked.payload == nullptr);
    CHECK(buffer.filling() == 7);

    for (uint32_t line = 1; line < SoaJitOldResultBuffer::Credits; ++line) {
        for (uint32_t word = 1;
             word < SoaJitOldResultBuffer::WordsPerLine; ++word) {
            const float old = static_cast<float>(line * 100 + word);
            CHECK(buffer.capture(
                      6, line,
                      line * SoaJitOldResultBuffer::WordsPerLine + word,
                      reinterpret_cast<const uint8_t *>(&old), sizeof(old)) ==
                  SoaJitOldResultBuffer::Result::Accepted);
        }
    }
    CHECK(buffer.acknowledge(pressure.identity) ==
          SoaJitOldResultBuffer::Result::Accepted);
    CHECK(buffer.capture(
              6, 9, 8 * SoaJitOldResultBuffer::WordsPerLine,
              reinterpret_cast<const uint8_t *>(&ninth), sizeof(ninth)) ==
          SoaJitOldResultBuffer::Result::Accepted);
    CHECK(buffer.closeSelection(SelectedWords,
                                LogicalWords - SelectedWords) ==
          SoaJitOldResultBuffer::Result::Accepted);

    uint32_t fullLines = 0;
    SoaJitOldResultBuffer::Request request;
    while (buffer.issue(&request, true) ==
           SoaJitOldResultBuffer::Result::Accepted) {
        fullLines += request.identity.validWords == 0xffff ? 1 : 0;
        CHECK(buffer.acknowledge(request.identity) ==
              SoaJitOldResultBuffer::Result::Accepted);
    }
    CHECK(fullLines == PackedLines);
    CHECK(buffer.issues() == 9);
    CHECK(buffer.complete());
    CHECK(buffer.finish() == SoaJitOldResultBuffer::Result::Accepted);
}

static void
runPressureDensitySelection()
{
    constexpr uint64_t Base = 0x6000;
    constexpr uint32_t LogicalWords =
        9 * SoaJitOldResultBuffer::WordsPerLine;
    SoaJitOldResultBuffer buffer;
    CHECK(buffer.begin(7, Base, LogicalWords) ==
          SoaJitOldResultBuffer::Result::Accepted);
    uint32_t selected = 0;
    for (uint32_t word = 0;
         word < SoaJitOldResultBuffer::WordsPerLine; ++word) {
        const float old = static_cast<float>(word);
        CHECK(buffer.capture(
                  7, 0, word,
                  reinterpret_cast<const uint8_t *>(&old), sizeof(old)) ==
              SoaJitOldResultBuffer::Result::Accepted);
        ++selected;
    }
    SoaJitOldResultBuffer::Request full;
    CHECK(buffer.issue(&full, false) ==
          SoaJitOldResultBuffer::Result::Accepted);
    CHECK(full.identity.lineAddress == Base);
    CHECK(full.identity.validWords == 0xffff);

    const float oldest = 1.0F;
    CHECK(buffer.capture(
              7, 1, SoaJitOldResultBuffer::WordsPerLine,
              reinterpret_cast<const uint8_t *>(&oldest), sizeof(oldest)) ==
          SoaJitOldResultBuffer::Result::Accepted);
    ++selected;
    for (uint32_t word = 1; word < 8; ++word) {
        const float old = static_cast<float>(200 + word);
        CHECK(buffer.capture(
                  7, 2,
                  2 * SoaJitOldResultBuffer::WordsPerLine + word,
                  reinterpret_cast<const uint8_t *>(&old), sizeof(old)) ==
              SoaJitOldResultBuffer::Result::Accepted);
        ++selected;
    }
    const float denseFirst = 200.0F;
    CHECK(buffer.capture(
              7, 2, 2 * SoaJitOldResultBuffer::WordsPerLine,
              reinterpret_cast<const uint8_t *>(&denseFirst),
              sizeof(denseFirst)) ==
          SoaJitOldResultBuffer::Result::Accepted);
    ++selected;
    for (uint32_t line = 3; line < SoaJitOldResultBuffer::Credits; ++line) {
        const float old = static_cast<float>(line);
        CHECK(buffer.capture(
                  7, line, line * SoaJitOldResultBuffer::WordsPerLine,
                  reinterpret_cast<const uint8_t *>(&old), sizeof(old)) ==
              SoaJitOldResultBuffer::Result::Accepted);
        ++selected;
    }
    const float ninth = 9.0F;
    CHECK(buffer.capture(
              7, 9, 8 * SoaJitOldResultBuffer::WordsPerLine,
              reinterpret_cast<const uint8_t *>(&ninth), sizeof(ninth)) ==
          SoaJitOldResultBuffer::Result::Full);

    SoaJitOldResultBuffer::Request pressure;
    CHECK(buffer.issueForPressure(&pressure, 1, PressurePolicy::Densest) ==
          SoaJitOldResultBuffer::Result::Accepted);
    CHECK(pressure.identity.lineAddress ==
          Base + 2 * SoaJitOldResultBuffer::LineBytes);
    CHECK(pressure.identity.validWords == 0xff);
    CHECK(buffer.awaitingResponses() == 2);
    SoaJitOldResultBuffer::Request blocked;
    CHECK(buffer.issueForPressure(&blocked, 1, PressurePolicy::Densest) ==
          SoaJitOldResultBuffer::Result::NoReadyLine);
    CHECK(blocked.payload == nullptr);
    CHECK(buffer.acknowledge(pressure.identity) ==
          SoaJitOldResultBuffer::Result::Accepted);
    CHECK(buffer.acknowledge(full.identity) ==
          SoaJitOldResultBuffer::Result::Accepted);
    CHECK(buffer.capture(
              7, 9, 8 * SoaJitOldResultBuffer::WordsPerLine,
              reinterpret_cast<const uint8_t *>(&ninth), sizeof(ninth)) ==
          SoaJitOldResultBuffer::Result::Accepted);
    ++selected;
    CHECK(buffer.closeSelection(selected, LogicalWords - selected) ==
          SoaJitOldResultBuffer::Result::Accepted);
    SoaJitOldResultBuffer::Request request;
    while (buffer.issue(&request, true) ==
           SoaJitOldResultBuffer::Result::Accepted) {
        CHECK(buffer.acknowledge(request.identity) ==
              SoaJitOldResultBuffer::Result::Accepted);
    }
    CHECK(buffer.complete());
    CHECK(buffer.finish() == SoaJitOldResultBuffer::Result::Accepted);
}

static void
runPressureCreditLimits()
{
    constexpr std::array<size_t, 4> Limits{{1, 2, 4, 8}};
    constexpr std::array<PressurePolicy, 2> Policies{{
        PressurePolicy::OriginalOldest, PressurePolicy::Densest}};
    constexpr std::array<uint32_t, SoaJitOldResultBuffer::Credits>
        Densities{{1, 8, 3, 7, 2, 6, 4, 5}};
    constexpr std::array<uint32_t, SoaJitOldResultBuffer::Credits>
        DenseOrder{{1, 3, 5, 7, 6, 2, 4, 0}};
    for (size_t policyIndex = 0; policyIndex < Policies.size();
         ++policyIndex) {
        for (size_t limit : Limits) {
            constexpr uint64_t Base = 0x7000;
            constexpr uint32_t LogicalWords =
                SoaJitOldResultBuffer::Credits *
                SoaJitOldResultBuffer::WordsPerLine;
            const uint64_t generation = 10 + policyIndex * 10 + limit;
            SoaJitOldResultBuffer buffer;
            CHECK(buffer.begin(generation, Base, LogicalWords) ==
                  SoaJitOldResultBuffer::Result::Accepted);
            uint32_t selected = 0;
            for (uint32_t line = 0;
                 line < SoaJitOldResultBuffer::Credits; ++line) {
                for (uint32_t word = 0; word < Densities[line]; ++word) {
                    const float old = static_cast<float>(line * 100 + word);
                    CHECK(buffer.capture(
                              generation, line,
                              line * SoaJitOldResultBuffer::WordsPerLine +
                                  word,
                              reinterpret_cast<const uint8_t *>(&old),
                              sizeof(old)) ==
                          SoaJitOldResultBuffer::Result::Accepted);
                    ++selected;
                }
            }
            CHECK(buffer.closeSelection(selected, LogicalWords - selected) ==
                  SoaJitOldResultBuffer::Result::Accepted);

            std::array<SoaJitOldResultBuffer::Identity,
                       SoaJitOldResultBuffer::Credits> identities{};
            size_t issued = 0;
            SoaJitOldResultBuffer::Request request;
            while (buffer.issueForPressure(
                       &request, limit, Policies[policyIndex]) ==
                   SoaJitOldResultBuffer::Result::Accepted) {
                CHECK(issued < limit);
                const uint32_t expectedLine =
                    Policies[policyIndex] == PressurePolicy::OriginalOldest
                    ? issued : DenseOrder[issued];
                CHECK(request.identity.lineAddress ==
                      Base + expectedLine * SoaJitOldResultBuffer::LineBytes);
                CHECK(request.identity.validWords ==
                      (uint16_t{1} << Densities[expectedLine]) - 1);
                identities[issued++] = request.identity;
            }
            CHECK(issued == limit);
            CHECK(buffer.partialAwaitingResponses() == limit);
            CHECK(buffer.filling() ==
                  SoaJitOldResultBuffer::Credits - limit);
            for (size_t issue = 0; issue < issued; ++issue) {
                CHECK(buffer.acknowledge(identities[issue]) ==
                      SoaJitOldResultBuffer::Result::Accepted);
            }
            while (buffer.issue(&request, true) ==
                   SoaJitOldResultBuffer::Result::Accepted) {
                CHECK(buffer.acknowledge(request.identity) ==
                      SoaJitOldResultBuffer::Result::Accepted);
            }
            CHECK(buffer.complete());
            CHECK(buffer.finish() ==
                  SoaJitOldResultBuffer::Result::Accepted);
        }
    }

    SoaJitOldResultBuffer invalid;
    CHECK(invalid.begin(20, 0x9000, 16) ==
          SoaJitOldResultBuffer::Result::Accepted);
    const float old = 1.0F;
    CHECK(invalid.capture(20, 0, 0,
                          reinterpret_cast<const uint8_t *>(&old),
                          sizeof(old)) ==
          SoaJitOldResultBuffer::Result::Accepted);
    SoaJitOldResultBuffer::Request request;
    CHECK(invalid.issueForPressure(
              &request, 3, PressurePolicy::OriginalOldest) ==
          SoaJitOldResultBuffer::Result::InvalidPressureCredits);
    CHECK(invalid.issueForPressure(
              &request, 1, static_cast<PressurePolicy>(99)) ==
          SoaJitOldResultBuffer::Result::InvalidPressurePolicy);
}

int
main()
{
    static_assert(sizeof(SoaJitOldResultBuffer) == 1128);
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

    SoaJitOldResultBuffer empty;
    CHECK(empty.begin(4, 0x3000, 0) ==
          SoaJitOldResultBuffer::Result::Accepted);
    CHECK(empty.closeSelection(0, 0) ==
          SoaJitOldResultBuffer::Result::Accepted);
    CHECK(empty.complete());
    CHECK(empty.finish() == SoaJitOldResultBuffer::Result::Accepted);

    SoaJitOldResultBuffer delayed;
    CHECK(delayed.begin(5, 0x4000, 1) ==
          SoaJitOldResultBuffer::Result::Accepted);
    CHECK(delayed.closeSelection(1, 0) ==
          SoaJitOldResultBuffer::Result::SelectionMismatch);
    CHECK(!delayed.selectionIsClosed());
    const float old = 42.0F;
    CHECK(delayed.capture(5, 0, 0,
                          reinterpret_cast<const uint8_t *>(&old),
                          sizeof(old)) ==
          SoaJitOldResultBuffer::Result::Accepted);
    CHECK(delayed.closeSelection(1, 0) ==
          SoaJitOldResultBuffer::Result::Accepted);
    CHECK(delayed.selectionIsClosed());

    runPressurePacking();
    runPressureDensitySelection();
    runPressureCreditLimits();

    std::cout << "SOA_JIT_OLD_RESULT_TEST_PASS\n";
    return 0;
}
