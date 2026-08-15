#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>

#include "mem/MAA/SoaJitOverlapState.hh"

using gem5::SoaJitValueCoalescer;

int
main()
{
    SoaJitValueCoalescer coalescer;
    coalescer.configure(false, 0, 4);
    coalescer.reset();

    constexpr uint64_t Generation = 9;
    constexpr uint16_t SharedWaiter = 3;
    constexpr uint64_t Primary = 0x1000;
    constexpr uint64_t Secondary = 0x2000;
    std::array<uint8_t, SoaJitValueCoalescer::LineBytes> primary{};
    std::array<uint8_t, SoaJitValueCoalescer::LineBytes> secondary{};
    primary[4] = 0x31;
    secondary[8] = 0x72;

    assert(coalescer.requestAlias(Generation, Primary, SharedWaiter).result ==
           SoaJitValueCoalescer::AliasResult::Fill);
    const auto secondary_request =
        coalescer.requestAlias(Generation, Secondary, SharedWaiter);
    assert(secondary_request.result ==
           SoaJitValueCoalescer::AliasResult::Fill);
    assert(coalescer.acceptResponse(Generation, Primary, primary.data(),
                                    primary.size()) ==
           SoaJitValueCoalescer::ResponseResult::CacheFill);
    assert(coalescer.acceptResponse(Generation, Secondary, secondary.data(),
                                    secondary.size()) ==
           SoaJitValueCoalescer::ResponseResult::CacheFill);

    SoaJitValueCoalescer::Delivery first;
    SoaJitValueCoalescer::Delivery second;
    assert(coalescer.deliver(Generation, SharedWaiter, 10, first, 2) ==
           SoaJitValueCoalescer::DeliveryResult::Delivered);
    assert(coalescer.deliver(Generation, SharedWaiter, 10, second, 2) ==
           SoaJitValueCoalescer::DeliveryResult::Delivered);
    assert(first.paddr != second.paddr);
    assert((first.paddr == Primary && first.data[4] == 0x31) ||
           (first.paddr == Secondary && first.data[8] == 0x72));
    assert((second.paddr == Primary && second.data[4] == 0x31) ||
           (second.paddr == Secondary && second.data[8] == 0x72));
    assert(coalescer.clearGeneration(Generation));
    assert(coalescer.assertInvariants());

    std::cout << "SHARED_INDEX_DUAL_RMW_UNIT_PASS\n";
    return 0;
}
