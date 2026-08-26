#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <map>
#include <random>
#include <vector>

#include "mem/MAA/FusedP16ProductState.hh"
#include "mem/MAA/SoaJitOverlapState.hh"

using gem5::SoaJitValueCoalescer;
using gem5::maa::FusedP16AluToken;
using gem5::maa::FusedP16ProductContract;
using gem5::maa::FusedP16ResponseOwner;
using gem5::maa::FusedP16ResponseState;

namespace
{

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;         \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

uint32_t
bits(float value)
{
    uint32_t result = 0;
    std::memcpy(&result, &value, sizeof(result));
    return result;
}

void
testContractAccountingAndSpans()
{
    using C = FusedP16ProductContract;
    static_assert(C::LogicalElements == 16384);
    static_assert(C::SpanBytes == 65536);
    static_assert(C::GuestBackingBytesRemoved == 262144);
    static_assert(C::ResponsePayloadBytesPerUnit == 512);
    static_assert(C::ActiveCoefficientPayloadBytesPerUnit == 2048);
    static_assert(C::CombinerPayloadBytesPerUnit == 2048);
    static_assert(C::ResponseSubstateBytesPerUnit == 8);
    static_assert(C::TaggedAluStateBytesPerLane == 8);
    CHECK(C::spanFits(0x1000, 0x1000, 0x11000));
    CHECK(!C::spanFits(0x1004, 0x1000, 0x12000));
    CHECK(!C::spanFits(0x1000, 0x1000, 0x10fff));
    CHECK(C::spansOverlap(0x1000, 0x100, 0x1080, 0x100));
    CHECK(!C::spansOverlap(0x1000, 0x100, 0x1100, 0x100));
}

void
testExactStateTransitionsAndStaleTags()
{
    FusedP16ResponseOwner owner;
    CHECK(owner.assertInvariants());
    CHECK(!owner.requestCoefficient());
    CHECK(owner.begin(9, 2, 3, 17, 29));
    CHECK(!owner.begin(9, 2, 3, 17, 29));
    CHECK(owner.currentState() == FusedP16ResponseState::NeedCoefficient);
    CHECK(owner.requestCoefficient());
    const FusedP16AluToken exact{9, 2, 3, 17};
    const FusedP16AluToken staleGeneration{8, 2, 3, 17};
    const FusedP16AluToken wrongOffset{9, 2, 3, 18};
    CHECK(!owner.issueMultiply(staleGeneration, exact));
    CHECK(owner.issueMultiply(exact, exact));
    CHECK(!owner.completeMultiply(wrongOffset, exact));
    CHECK(owner.completeMultiply(exact, exact));
    CHECK(owner.currentState() == FusedP16ResponseState::ProductReady);
    CHECK(owner.consumeProduct());
    CHECK(owner.assertInvariants());
    CHECK(!owner.consumeProduct());
}

std::array<uint8_t, SoaJitValueCoalescer::LineBytes>
coefficientLine(const std::vector<float> &coefficients, uint32_t line)
{
    std::array<uint8_t, SoaJitValueCoalescer::LineBytes> payload{};
    for (uint32_t word = 0; word < 16; ++word) {
        const uint32_t ordinal = line * 16 + word;
        std::memcpy(payload.data() + word * sizeof(float),
                    &coefficients[ordinal], sizeof(float));
    }
    return payload;
}

void
testFullCollisionPatternReorderBackpressureAndExactProducts()
{
    constexpr uint64_t generation = 41;
    constexpr uint16_t indirect = 2;
    constexpr uint64_t coefficientBase = 0x100000;
    constexpr uint32_t elements = FusedP16ProductContract::LogicalElements;
    constexpr uint32_t sourceElements = 4096;
    std::vector<float> source(sourceElements);
    std::vector<uint32_t> indices(elements);
    std::vector<float> coefficients(elements);
    std::vector<uint32_t> expected(elements);
    std::vector<uint32_t> actual(elements, 0x7fc00001U);
    for (uint32_t index = 0; index < sourceElements; ++index)
        source[index] = static_cast<float>(static_cast<int>(index) - 2048) /
            257.0f;
    uint32_t pseudo = 0x12345678U;
    for (uint32_t ordinal = 0; ordinal < elements; ++ordinal) {
        if (ordinal < 4096)
            indices[ordinal] = 7;
        else if (ordinal < 8192)
            indices[ordinal] = ordinal & 15U;
        else if (ordinal < 12288)
            indices[ordinal] = (ordinal * 257U) & (sourceElements - 1);
        else {
            pseudo ^= pseudo << 13;
            pseudo ^= pseudo >> 17;
            pseudo ^= pseudo << 5;
            indices[ordinal] = pseudo & (sourceElements - 1);
        }
        coefficients[ordinal] =
            static_cast<float>(static_cast<int>(ordinal % 251) - 125) /
            37.0f;
        const float product = source[indices[ordinal]] *
            coefficients[ordinal];
        expected[ordinal] = bits(product);
    }

    SoaJitValueCoalescer values;
    values.configure(true, 0,
                     FusedP16ProductContract::CoefficientOwnerLines);
    values.reset();
    std::array<FusedP16ResponseOwner,
               FusedP16ProductContract::ResponseSlots> owners{};
    uint64_t fills = 0;
    uint64_t merges = 0;
    uint64_t hits = 0;
    uint64_t deliveries = 0;
    uint64_t mulAccepts = 0;
    uint64_t mulCompletions = 0;
    uint64_t backpressure = 0;

    for (uint32_t base = 0; base < elements;
         base += FusedP16ProductContract::ResponseSlots) {
        std::vector<uint64_t> pendingLines;
        for (uint8_t slot = 0;
             slot < FusedP16ProductContract::ResponseSlots; ++slot) {
            const uint16_t ordinal = static_cast<uint16_t>(base + slot);
            CHECK(owners[slot].begin(generation, indirect, slot, ordinal,
                                     ordinal));
            const uint64_t line = coefficientBase +
                static_cast<uint64_t>(ordinal / 16) * 64;
            const auto request = values.requestAlias(generation, line, slot);
            CHECK(request.result != SoaJitValueCoalescer::AliasResult::Stall);
            CHECK(owners[slot].requestCoefficient());
            if (request.result == SoaJitValueCoalescer::AliasResult::Fill) {
                fills++;
                pendingLines.push_back(line);
            } else if (request.result ==
                       SoaJitValueCoalescer::AliasResult::Merge) {
                merges++;
            } else {
                CHECK(request.result ==
                      SoaJitValueCoalescer::AliasResult::Hit);
                hits++;
            }
        }
        std::reverse(pendingLines.begin(), pendingLines.end());
        for (const uint64_t line : pendingLines) {
            const uint32_t lineIndex =
                static_cast<uint32_t>((line - coefficientBase) / 64);
            const auto payload = coefficientLine(coefficients, lineIndex);
            CHECK(values.acceptResponse(generation, line, payload.data(),
                                        payload.size()) ==
                  SoaJitValueCoalescer::ResponseResult::CacheFill);
        }

        std::array<uint8_t, FusedP16ProductContract::ResponseSlots> order{};
        for (uint8_t slot = 0; slot < order.size(); ++slot)
            order[slot] = slot;
        std::reverse(order.begin(), order.end());
        for (const uint8_t slot : order) {
            const uint16_t ordinal = static_cast<uint16_t>(base + slot);
            SoaJitValueCoalescer::Delivery delivery;
            CHECK(values.deliver(generation, slot, base + slot + 1,
                                 delivery, 1) ==
                  SoaJitValueCoalescer::DeliveryResult::Delivered);
            deliveries++;
            const FusedP16AluToken token{
                generation, indirect, slot, ordinal};
            const FusedP16AluToken wrong{
                generation, indirect, slot,
                static_cast<uint16_t>((ordinal + 1) % elements)};
            CHECK(!owners[slot].issueMultiply(wrong, token));
            backpressure++;
            CHECK(owners[slot].issueMultiply(token, token));
            mulAccepts++;
            CHECK(!owners[slot].completeMultiply(wrong, token));
            const size_t byte = (ordinal & 15U) * sizeof(float);
            float coefficient = 0.0f;
            std::memcpy(&coefficient, delivery.data.data() + byte,
                        sizeof(coefficient));
            const float product = source[indices[ordinal]] * coefficient;
            actual[ordinal] = bits(product);
            CHECK(owners[slot].completeMultiply(token, token));
            mulCompletions++;
            CHECK(owners[slot].consumeProduct());
            CHECK(owners[slot].assertInvariants());
        }
    }

    CHECK(actual == expected);
    CHECK(deliveries == elements);
    CHECK(mulAccepts == elements);
    CHECK(mulCompletions == elements);
    CHECK(backpressure == elements);
    CHECK(fills == elements / 16);
    CHECK(merges > 0);
    CHECK(hits > 0);
    CHECK(values.clearGeneration(generation));
    CHECK(values.cacheOccupancy() == 0);
    CHECK(values.assertInvariants());

    // Model masked/partial transport closure separately from semantic words:
    // every ordinal closes exactly once although WriteResps are reversed.
    std::array<uint16_t, elements / 16> masks{};
    for (uint32_t ordinal = 0; ordinal < elements; ++ordinal) {
        const uint32_t line = ordinal / 16;
        const uint16_t bit = static_cast<uint16_t>(1U << (ordinal & 15U));
        CHECK((masks[line] & bit) == 0);
        masks[line] |= bit;
    }
    uint64_t semanticCompletions = 0;
    for (auto line = masks.rbegin(); line != masks.rend(); ++line) {
        CHECK(*line == UINT16_MAX);
        semanticCompletions += 16;
    }
    CHECK(semanticCompletions == elements);
    CHECK(std::none_of(actual.begin(), actual.end(), [](uint32_t value) {
        return value == 0x7fc00001U;
    }));
}

} // anonymous namespace

int
main()
{
    testContractAccountingAndSpans();
    testExactStateTransitionsAndStaleTags();
    testFullCollisionPatternReorderBackpressureAndExactProducts();
    std::cout << "fused_p16_product_state_test: PASS\n";
    return 0;
}
