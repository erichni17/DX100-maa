#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>

#include "mem/MAA/SoaJitOverlapState.hh"

using gem5::SoaJitPredicateFeeder;
using gem5::SoaJitApplyLanePool;
using gem5::SoaJitValueCoalescer;

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

std::array<uint8_t, SoaJitValueCoalescer::LineBytes>
payload(uint8_t seed)
{
    std::array<uint8_t, SoaJitValueCoalescer::LineBytes> result{};
    for (size_t index = 0; index < result.size(); ++index)
        result[index] = static_cast<uint8_t>(seed + index);
    return result;
}

void
testThirtyTwoContextsShareOneFill()
{
    SoaJitValueCoalescer state;
    state.configure(true, 0);
    state.reset();
    constexpr uint64_t generation = 7;
    constexpr uint64_t address = 0x1000;
    for (uint16_t context = 0;
         context < SoaJitValueCoalescer::MaxContexts; ++context) {
        const uint16_t waiter =
            context * SoaJitValueCoalescer::MaxLookahead +
            SoaJitValueCoalescer::MaxLookahead - 1;
        const auto request = state.requestAlias(generation, address, waiter);
        CHECK(request.result ==
              (context == 0 ? SoaJitValueCoalescer::AliasResult::Fill
                            : SoaJitValueCoalescer::AliasResult::Merge));
    }
    CHECK(state.fillingCount() == 1);
    const auto data = payload(3);
    CHECK(state.acceptResponse(
              generation, address, data.data(), data.size()) ==
          SoaJitValueCoalescer::ResponseResult::CacheFill);
    for (uint16_t context = 0;
         context < SoaJitValueCoalescer::MaxContexts; ++context) {
        SoaJitValueCoalescer::Delivery delivery;
        const uint16_t waiter =
            context * SoaJitValueCoalescer::MaxLookahead +
            SoaJitValueCoalescer::MaxLookahead - 1;
        CHECK(state.deliver(generation, waiter, 11 + context, delivery) ==
              SoaJitValueCoalescer::DeliveryResult::Delivered);
        CHECK(delivery.data == data);
    }
    CHECK(state.requestAlias(
              generation, address,
              static_cast<uint16_t>(SoaJitValueCoalescer::MaxWaiters))
              .result == SoaJitValueCoalescer::AliasResult::Invalid);
    CHECK(state.assertInvariants());
}

void
testFourFillsFifthMissRetryAndEviction()
{
    SoaJitValueCoalescer state;
    state.configure(true, 0);
    state.reset();
    for (uint8_t index = 0; index < 4; ++index) {
        CHECK(state.requestAlias(1, 0x1000 + 0x40 * index, index).result ==
              SoaJitValueCoalescer::AliasResult::Fill);
    }
    CHECK(state.requestAlias(1, 0x2000, 8).result ==
          SoaJitValueCoalescer::AliasResult::Stall);

    const auto third = payload(30);
    CHECK(state.acceptResponse(1, 0x1080, third.data(), third.size()) ==
          SoaJitValueCoalescer::ResponseResult::CacheFill);
    CHECK(state.requestAlias(1, 0x2000, 8).result ==
          SoaJitValueCoalescer::AliasResult::Stall);
    SoaJitValueCoalescer::Delivery delivery;
    CHECK(state.deliver(1, 2, 1, delivery) ==
          SoaJitValueCoalescer::DeliveryResult::Delivered);
    const auto retry = state.requestAlias(1, 0x2000, 8);
    CHECK(retry.result == SoaJitValueCoalescer::AliasResult::Fill);
    CHECK(retry.evicted);
    CHECK(state.cacheOccupancy() == 4);
    CHECK(state.assertInvariants());
}

void
testSelectableOwnerPoolBoundsInactiveExclusionAndCapacityStall()
{
    SoaJitValueCoalescer state;
    constexpr std::array<uint8_t, 6> valid_counts = {
        4, 8, 16, 32, 64, 128,
    };
    for (const uint8_t active : valid_counts) {
        state.configure(true, 0, active);
        state.reset();
        CHECK(state.activeOwnerCount() == active);
        for (uint16_t index = 0; index < active; ++index) {
            CHECK(state.requestAlias(
                      9, 0x3000 + 0x40 * index, index).result ==
                  SoaJitValueCoalescer::AliasResult::Fill);
        }
        CHECK(state.cacheOccupancy() == active);
        CHECK(state.cacheHwm() == active);
        CHECK(state.requestAlias(
                  9, 0x3000 + 0x40 * active, active).result ==
              SoaJitValueCoalescer::AliasResult::Stall);
        const auto &lines = state.cacheLines();
        for (size_t index = active; index < lines.size(); ++index) {
            CHECK(lines[index].state ==
                  SoaJitValueCoalescer::LineState::Free);
            CHECK(lines[index].generation == 0);
            CHECK(lines[index].paddr == 0);
            CHECK(lines[index].waiterMask.none());
        }
        CHECK(state.assertInvariants());
    }

    constexpr std::array<size_t, 6> invalid_counts = {
        0, 1, 5, 63, 127, 260,
    };
    for (const size_t active : invalid_counts) {
        state.configure(true, 0, active);
        state.reset();
        CHECK(state.activeOwnerCount() == 0);
        CHECK(!state.assertInvariants());
        CHECK(state.requestAlias(11, 0x7000, 0).result ==
              SoaJitValueCoalescer::AliasResult::Stall);
    }
}

void
testFullOneTwentyEightOwnerGenerationClosesExactly()
{
    SoaJitValueCoalescer state;
    state.configure(true, 0, 128);
    state.reset();
    constexpr uint64_t generation = 21;
    constexpr uint64_t base = 0x10000;
    for (uint16_t index = 0; index < 128; ++index) {
        CHECK(state.requestAlias(
                  generation, base + 0x40 * index, index).result ==
              SoaJitValueCoalescer::AliasResult::Fill);
    }
    CHECK(!state.clearGeneration(generation));
    for (int index = 127; index >= 0; --index) {
        const auto data = payload(static_cast<uint8_t>(index));
        CHECK(state.acceptResponse(
                  generation, base + 0x40 * index,
                  data.data(), data.size()) ==
              SoaJitValueCoalescer::ResponseResult::CacheFill);
    }
    CHECK(!state.clearGeneration(generation));
    for (uint16_t index = 0; index < 128; ++index) {
        const auto expected = payload(static_cast<uint8_t>(index));
        SoaJitValueCoalescer::Delivery delivery;
        CHECK(state.deliver(generation, index, 1000 + index, delivery) ==
              SoaJitValueCoalescer::DeliveryResult::Delivered);
        CHECK(delivery.data == expected);
    }
    CHECK(state.clearGeneration(generation));
    CHECK(state.cacheOccupancy() == 0);
    CHECK(state.fillingCount() == 0);
    CHECK(state.readyCount() == 0);
    CHECK(!state.owns(generation, base));
    CHECK(state.assertInvariants());
}

void
testReadyHitMergeAndOneDeliveryPerContextCycle()
{
    SoaJitValueCoalescer state;
    state.configure(true, 0);
    state.reset();
    const auto data = payload(9);
    CHECK(state.requestAlias(2, 0x3000, 0).result ==
          SoaJitValueCoalescer::AliasResult::Fill);
    CHECK(state.requestAlias(2, 0x3000, 1).result ==
          SoaJitValueCoalescer::AliasResult::Merge);
    CHECK(state.acceptResponse(2, 0x3000, data.data(), data.size()) ==
          SoaJitValueCoalescer::ResponseResult::CacheFill);
    SoaJitValueCoalescer::Delivery first;
    SoaJitValueCoalescer::Delivery second;
    CHECK(state.deliver(2, 0, 44, first) ==
          SoaJitValueCoalescer::DeliveryResult::Delivered);
    CHECK(state.deliver(2, 1, 44, second) ==
          SoaJitValueCoalescer::DeliveryResult::CycleLimited);
    CHECK(state.deliver(2, 1, 45, second) ==
          SoaJitValueCoalescer::DeliveryResult::Delivered);
    CHECK(state.requestAlias(2, 0x3000, 2).result ==
          SoaJitValueCoalescer::AliasResult::Hit);
    CHECK(state.assertInvariants());
}

void
testSelectableDeliveryAndIndependentApplyLanes()
{
    SoaJitValueCoalescer values;
    values.configure(true, 0, 8);
    values.reset();
    const auto data = payload(17);
    for (uint8_t waiter = 0; waiter < 5; ++waiter) {
        const uint64_t address = 0xa000 + 0x40 * waiter;
        CHECK(values.requestAlias(12, address, waiter).result ==
              SoaJitValueCoalescer::AliasResult::Fill);
        CHECK(values.acceptResponse(12, address, data.data(), data.size()) ==
              SoaJitValueCoalescer::ResponseResult::CacheFill);
    }
    for (uint8_t waiter = 0; waiter < 4; ++waiter) {
        SoaJitValueCoalescer::Delivery delivery;
        CHECK(values.deliver(12, waiter, 91, delivery, 4) ==
              SoaJitValueCoalescer::DeliveryResult::Delivered);
    }
    SoaJitValueCoalescer::Delivery fifth;
    CHECK(values.deliver(12, 4, 91, fifth, 4) ==
          SoaJitValueCoalescer::DeliveryResult::CycleLimited);
    CHECK(values.deliver(12, 4, 92, fifth, 4) ==
          SoaJitValueCoalescer::DeliveryResult::Delivered);

    SoaJitApplyLanePool lanes;
    lanes.configure(4);
    lanes.reset();
    lanes.beginCycle(91);
    CHECK(lanes.grant(91, 12, 0x1000, 0, 8));
    CHECK(!lanes.grant(91, 12, 0x1040, 0, 8));
    CHECK(!lanes.grant(91, 12, 0x1000, 1, 8));
    CHECK(lanes.grant(91, 12, 0x1040, 1, 8));
    CHECK(lanes.grant(91, 12, 0x1080, 2, 8));
    CHECK(lanes.grant(91, 12, 0x10c0, 3, 8));
    CHECK(!lanes.grant(91, 12, 0x1100, 4, 8));
    CHECK(lanes.currentCycleOccupancy() == 4);
    CHECK(lanes.highWater() == 4);
    CHECK(lanes.assertInvariants());
    lanes.beginCycle(92);
    CHECK(lanes.currentCycleOccupancy() == 0);
    CHECK(lanes.grant(92, 12, 0x1100, 4, 8));
    CHECK(lanes.grant(92, 12, 0x1140, 31, 32));
    CHECK(!lanes.grant(92, 12, 0x1180, 32, 32));
    CHECK(lanes.assertInvariants());

    lanes.configure(3);
    lanes.reset();
    CHECK(!lanes.grant(93, 12, 0x1140, 5, 8));
    CHECK(!lanes.assertInvariants());
}

void
testPrefetchAndAliasShareOneOwner()
{
    SoaJitValueCoalescer state;
    state.configure(true, 8);
    state.reset();
    CHECK(state.reservePrefetch(3, 0x4000, 0x8000) ==
          SoaJitValueCoalescer::PrefetchResult::Issue);
    CHECK(state.reservePrefetch(3, 0x4000, 0x8000) ==
          SoaJitValueCoalescer::PrefetchResult::AlreadyOwned);
    CHECK(state.requestAlias(3, 0x8000, 0).result ==
          SoaJitValueCoalescer::AliasResult::Merge);
    CHECK(state.fillingCount() == 1);
    CHECK(state.prefetchCount() == 1);
    const auto data = payload(12);
    uint64_t owned_vaddr = 0;
    CHECK(state.acceptResponse(
              3, 0x8000, data.data(), data.size(), &owned_vaddr) ==
          SoaJitValueCoalescer::ResponseResult::PrefetchPromote);
    CHECK(owned_vaddr == 0x4000);
    CHECK(state.prefetchCount() == 0);
    SoaJitValueCoalescer::Delivery delivery;
    CHECK(state.deliver(3, 0, 7, delivery) ==
          SoaJitValueCoalescer::DeliveryResult::Delivered);
    CHECK(delivery.data == data);

    CHECK(state.reservePrefetch(3, 0x4040, 0x8040) ==
          SoaJitValueCoalescer::PrefetchResult::Issue);
    CHECK(state.reservePrefetch(3, 0x5040, 0x8040) ==
          SoaJitValueCoalescer::PrefetchResult::Invalid);
    owned_vaddr = 0;
    CHECK(state.acceptResponse(
              3, 0x8040, data.data(), data.size(), &owned_vaddr) ==
          SoaJitValueCoalescer::ResponseResult::PrefetchDiscard);
    CHECK(owned_vaddr == 0x4040);
    CHECK(state.assertInvariants());
}

void
testPrefetchCreditsFailClosedAndBoundActivePrefix()
{
    SoaJitValueCoalescer state;
    state.configure(true, 1);
    state.reset();
    CHECK(state.activePrefetchCreditCount() == 1);
    CHECK(state.reservePrefetch(4, 0x6000, 0xa000) ==
          SoaJitValueCoalescer::PrefetchResult::Issue);
    CHECK(state.reservePrefetch(4, 0x6040, 0xa040) ==
          SoaJitValueCoalescer::PrefetchResult::Full);
    CHECK(state.prefetchCount() == 1);
    CHECK(state.prefetchHwm() == 1);
    CHECK(state.assertInvariants());

    state.configure(true, 3);
    state.reset();
    CHECK(!state.assertInvariants());
    CHECK(state.reservePrefetch(5, 0x7000, 0xb000) ==
          SoaJitValueCoalescer::PrefetchResult::Invalid);

    state.configure(true, 1);
    state.reset();
    CHECK(state.reservePrefetch(5, 0x7001, 0xb000) ==
          SoaJitValueCoalescer::PrefetchResult::Invalid);
    CHECK(state.reservePrefetch(5, 0x7000, 0xb001) ==
          SoaJitValueCoalescer::PrefetchResult::Invalid);
}

void
testPrefetchResponsesPreserveReorderedVaPaOwnership()
{
    SoaJitValueCoalescer state;
    state.configure(false, 2);
    state.reset();
    CHECK(state.reservePrefetch(6, 0x8000, 0xc000) ==
          SoaJitValueCoalescer::PrefetchResult::Issue);
    CHECK(state.reservePrefetch(6, 0x8040, 0xd000) ==
          SoaJitValueCoalescer::PrefetchResult::Issue);
    const auto data = payload(41);
    uint64_t owned_vaddr = 0;
    CHECK(state.acceptResponse(
              6, 0xd000, data.data(), data.size(), &owned_vaddr) ==
          SoaJitValueCoalescer::ResponseResult::PrefetchDiscard);
    CHECK(owned_vaddr == 0x8040);
    CHECK(state.prefetchCount() == 1);
    owned_vaddr = 0;
    CHECK(state.acceptResponse(
              6, 0xc000, data.data(), data.size(), &owned_vaddr) ==
          SoaJitValueCoalescer::ResponseResult::PrefetchDiscard);
    CHECK(owned_vaddr == 0x8000);
    CHECK(state.prefetchComplete());
    CHECK(state.assertInvariants());
}

void
testFailClosedResponseIdentity()
{
    SoaJitValueCoalescer state;
    state.configure(true, 1);
    state.reset();
    const auto data = payload(1);
    CHECK(state.acceptResponse(1, 0x9000, data.data(), data.size()) ==
          SoaJitValueCoalescer::ResponseResult::Unknown);
    CHECK(state.requestAlias(1, 0x9000, 0).result ==
          SoaJitValueCoalescer::AliasResult::Fill);
    CHECK(state.requestAlias(2, 0x9000, 1).result ==
          SoaJitValueCoalescer::AliasResult::Stale);
    CHECK(state.acceptResponse(2, 0x9000, data.data(), data.size()) ==
          SoaJitValueCoalescer::ResponseResult::Stale);
    CHECK(state.acceptResponse(1, 0x9000, data.data(), data.size()) ==
          SoaJitValueCoalescer::ResponseResult::CacheFill);
    CHECK(state.acceptResponse(1, 0x9000, data.data(), data.size()) ==
          SoaJitValueCoalescer::ResponseResult::Duplicate);
    CHECK(state.assertInvariants());
}

void
testPredicateBoundsAndReorderedResponses()
{
    SoaJitPredicateFeeder feeder;
    feeder.configure(8);
    feeder.reset();
    for (uint8_t index = 0; index < 8; ++index) {
        CHECK(feeder.reserve(5, 0x1000 + 0x40 * index,
                             0x5000 + 0x40 * index) ==
              SoaJitPredicateFeeder::Result::Accepted);
    }
    CHECK(feeder.occupancy() == 8);
    CHECK(feeder.hwm() == 8);
    CHECK(feeder.reserve(5, 0x2000, 0x6000) ==
          SoaJitPredicateFeeder::Result::Full);
    const auto data = payload(20);
    CHECK(feeder.accept(5, 0x51c0, data.data(), data.size()) ==
          SoaJitPredicateFeeder::Result::Accepted);
    CHECK(feeder.accept(5, 0x5000, data.data(), data.size()) ==
          SoaJitPredicateFeeder::Result::Accepted);
    CHECK(feeder.ready(5, 0x11c0) != nullptr);
    CHECK(feeder.ready(5, 0x1000) != nullptr);
    CHECK(feeder.accept(6, 0x5040, data.data(), data.size()) ==
          SoaJitPredicateFeeder::Result::Stale);
    CHECK(feeder.accept(5, 0x7000, data.data(), data.size()) ==
          SoaJitPredicateFeeder::Result::Unknown);
    CHECK(feeder.release(5, 0x1000) ==
          SoaJitPredicateFeeder::Result::Accepted);
    CHECK(feeder.occupancy() == 7);
}

} // anonymous namespace

int
main()
{
    testThirtyTwoContextsShareOneFill();
    testFourFillsFifthMissRetryAndEviction();
    testSelectableOwnerPoolBoundsInactiveExclusionAndCapacityStall();
    testFullOneTwentyEightOwnerGenerationClosesExactly();
    testReadyHitMergeAndOneDeliveryPerContextCycle();
    testSelectableDeliveryAndIndependentApplyLanes();
    testPrefetchAndAliasShareOneOwner();
    testPrefetchCreditsFailClosedAndBoundActivePrefix();
    testPrefetchResponsesPreserveReorderedVaPaOwnership();
    testFailClosedResponseIdentity();
    testPredicateBoundsAndReorderedResponses();
    std::cout << "SOA_JIT_OVERLAP_STATE_TEST_PASS\n";
    return 0;
}
