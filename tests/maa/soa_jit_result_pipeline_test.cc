#include <array>
#include <cstdint>
#include <cstdlib>
#include <iostream>

#include "mem/MAA/SoaJitResultPipeline.hh"

using gem5::SoaJitResultPipeline;

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;        \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

int
main()
{
    using Pipeline = SoaJitResultPipeline;
    static_assert(Pipeline::Regions == 2);
    static_assert(Pipeline::LinesPerRegion == 32);
    static_assert(Pipeline::RegionPayloadBytes == 2048);
    static_assert(Pipeline::FixedPayloadBytes == 4096);
    static_assert(Pipeline::incrementalPayloadBytesVsBaseline() == 2048);
    static_assert(Pipeline::activePayloadBytes(32) == 2048);
    static_assert(Pipeline::activePayloadBytes(64) == 4096);
    static_assert(Pipeline::activePayloadBytes(48) == 0);
    static_assert(Pipeline::regionForLine(31) == 0);
    static_assert(Pipeline::regionForLine(32) == 1);

    Pipeline pipeline;
    pipeline.reset(100);
    CHECK(pipeline.observe(110, {16, 0}, {0, 0}));
    CHECK(pipeline.observe(130, {8, 8}, {8, 0}));
    CHECK(pipeline.observe(160, {0, 8}, {8, 8}));
    CHECK(pipeline.observe(170, {0, 0}, {0, 8}));
    CHECK(pipeline.observe(180, {0, 0}, {0, 0}));
    CHECK(pipeline.resultReadWriteOverlapTicks() == 40);
    CHECK(pipeline.dualRegionResultOverlapTicks() == 40);
    CHECK(pipeline.serializedWriteOnlyTicks() == 10);
    CHECK(pipeline.aReadHighWater() ==
          (std::array<uint8_t, 2>{16, 8}));
    CHECK(pipeline.aWriteHighWater() ==
          (std::array<uint8_t, 2>{8, 8}));
    CHECK(pipeline.activeLineHighWater() ==
          (std::array<uint8_t, 2>{16, 16}));
    CHECK(pipeline.assertInvariants(64));
    CHECK(!pipeline.assertInvariants(32));

    Pipeline fail_closed;
    fail_closed.reset(50);
    CHECK(!fail_closed.observe(49, {0, 0}, {0, 0}));
    CHECK(!fail_closed.assertInvariants(64));

    Pipeline overflow;
    overflow.reset(0);
    CHECK(!overflow.observe(1, {33, 0}, {0, 0}));
    CHECK(!overflow.assertInvariants(64));

    Pipeline compact;
    compact.reset(0);
    CHECK(compact.observe(10, {8, 0}, {0, 0}, 8));
    CHECK(compact.observe(30, {4, 0}, {0, 0}, 4));
    CHECK(compact.observe(40, {0, 0}, {0, 0}, 0));
    CHECK(compact.aWriteHighWater() ==
          (std::array<uint8_t, 2>{0, 0}));
    CHECK(compact.compactWriteHighWater() == 8);
    CHECK(compact.activeLineHighWater() ==
          (std::array<uint8_t, 2>{8, 0}));
    CHECK(compact.resultReadWriteOverlapTicks() == 0);
    CHECK(compact.dualRegionResultOverlapTicks() == 0);
    CHECK(compact.compactWriteOutstandingTicks() == 30);
    CHECK(compact.assertInvariants(8));

    Pipeline compact_no_region_alias;
    compact_no_region_alias.reset(0);
    CHECK(compact_no_region_alias.observe(10, {1, 1}, {0, 0}, 8));
    CHECK(compact_no_region_alias.observe(20, {0, 0}, {0, 0}, 0));
    CHECK(compact_no_region_alias.compactWriteHighWater() == 8);
    CHECK(compact_no_region_alias.resultReadWriteOverlapTicks() == 0);
    CHECK(compact_no_region_alias.dualRegionResultOverlapTicks() == 0);
    CHECK(compact_no_region_alias.compactWriteOutstandingTicks() == 10);
    CHECK(compact_no_region_alias.assertInvariants(64));

    Pipeline compact_overflow;
    compact_overflow.reset(0);
    CHECK(!compact_overflow.observe(1, {0, 0}, {0, 0}, 9));
    CHECK(!compact_overflow.assertInvariants(8));

    std::cout << "SoA/JIT result pipeline tests passed\n";
    return 0;
}
