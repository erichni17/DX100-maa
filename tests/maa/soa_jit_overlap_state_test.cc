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
testEightContextsShareOneFill()
{
    SoaJitValueCoalescer state;
    state.configure(true, 0);
    state.reset();
    constexpr uint64_t generation = 7;
    constexpr uint64_t address = 0x1000;
    for (uint8_t context = 0;
         context < SoaJitValueCoalescer::MaxContexts; ++context) {
        const uint8_t waiter =
            context * SoaJitValueCoalescer::MaxLookahead;
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
    for (uint8_t context = 0;
         context < SoaJitValueCoalescer::MaxContexts; ++context) {
        SoaJitValueCoalescer::Delivery delivery;
        const uint8_t waiter =
            context * SoaJitValueCoalescer::MaxLookahead;
        CHECK(state.deliver(generation, waiter, 11 + context, delivery) ==
              SoaJitValueCoalescer::DeliveryResult::Delivered);
        CHECK(delivery.data == data);
    }
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
testSelectableOwnerPoolBoundsAndFailClosedConfiguration()
{
    SoaJitValueCoalescer state;
    state.configure(true, 0, 8);
    state.reset();
    for (uint8_t index = 0; index < 8; ++index) {
        CHECK(state.requestAlias(9, 0x3000 + 0x40 * index, index).result ==
              SoaJitValueCoalescer::AliasResult::Fill);
    }
    CHECK(state.requestAlias(9, 0x4000, 8).result ==
          SoaJitValueCoalescer::AliasResult::Stall);
    CHECK(state.activeOwnerCount() == 8);
    CHECK(state.assertInvariants());

    state.configure(true, 0, 32);
    state.reset();
    for (uint8_t index = 0; index < 32; ++index) {
        CHECK(state.requestAlias(10, 0x5000 + 0x40 * index, index).result ==
              SoaJitValueCoalescer::AliasResult::Fill);
    }
    CHECK(state.cacheOccupancy() == 32);
    CHECK(state.activeOwnerCount() == 32);
    CHECK(state.assertInvariants());

    state.configure(true, 0, 5);
    state.reset();
    CHECK(!state.assertInvariants());
    CHECK(state.requestAlias(11, 0x7000, 0).result ==
          SoaJitValueCoalescer::AliasResult::Stall);
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
    CHECK(state.acceptResponse(3, 0x8000, data.data(), data.size()) ==
          SoaJitValueCoalescer::ResponseResult::PrefetchPromote);
    CHECK(state.prefetchCount() == 0);
    SoaJitValueCoalescer::Delivery delivery;
    CHECK(state.deliver(3, 0, 7, delivery) ==
          SoaJitValueCoalescer::DeliveryResult::Delivered);
    CHECK(delivery.data == data);

    CHECK(state.reservePrefetch(3, 0x4040, 0x8040) ==
          SoaJitValueCoalescer::PrefetchResult::Issue);
    CHECK(state.acceptResponse(3, 0x8040, data.data(), data.size()) ==
          SoaJitValueCoalescer::ResponseResult::PrefetchDiscard);
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
    testEightContextsShareOneFill();
    testFourFillsFifthMissRetryAndEviction();
    testSelectableOwnerPoolBoundsAndFailClosedConfiguration();
    testReadyHitMergeAndOneDeliveryPerContextCycle();
    testSelectableDeliveryAndIndependentApplyLanes();
    testPrefetchAndAliasShareOneOwner();
    testFailClosedResponseIdentity();
    testPredicateBoundsAndReorderedResponses();
    std::cout << "SOA_JIT_OVERLAP_STATE_TEST_PASS\n";
    return 0;
}
