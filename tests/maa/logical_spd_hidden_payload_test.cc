#include <cstddef>
#include <array>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <set>
#include <vector>

#include "mem/MAA/LogicalSPDCacheRuntime.hh"
#include "mem/MAA/LogicalSPDHiddenPayload.hh"

namespace {

using gem5::LogicalSPDCacheRuntime;
using gem5::LogicalSPDPrivatePayloadAccounting;

void
require(bool condition, const char *message)
{
    if (!condition) {
        std::cerr << "FAIL: " << message << '\n';
        std::exit(1);
    }
}

void
testRuntimeIsSolePayloadAuthority()
{
    using Slice = LogicalSPDCacheRuntime::Slice;
    using Ledger = LogicalSPDCacheRuntime::PackedSemanticLedger;

    static_assert(Slice::Slots == 2);
    static_assert(Slice::PageBytes == 16384);
    static_assert(Slice::PayloadBytes == 32768);
    static_assert(Ledger::PrivatePayloadBits / 8 == 32768);
    static_assert(
        LogicalSPDPrivatePayloadAccounting::PayloadBytesPerMAA == 32768);

    std::vector<std::unique_ptr<LogicalSPDCacheRuntime>> runtimes;
    for (std::size_t maa = 0; maa < 4; ++maa)
        runtimes.emplace_back(std::make_unique<LogicalSPDCacheRuntime>());

    std::set<const std::byte *> payloadBases;
    std::size_t totalBytes = 0;
    for (const auto &runtime : runtimes) {
        for (std::size_t slot = 0; slot < Slice::Slots; ++slot) {
            const auto payload = runtime->slotPayload(slot);
            require(payload.data != nullptr, "Runtime slot has storage");
            require(payload.size == Slice::PageBytes,
                    "Runtime ping-pong slot is exactly 16 KiB");
            require(payloadBases.insert(payload.data).second,
                    "Runtime payload slots never alias");
            for (std::size_t byte = 0; byte < payload.size; ++byte)
                require(payload.data[byte] == std::byte{0},
                        "Runtime payload is construction-zero");
            totalBytes += payload.size;
        }
    }
    require(totalBytes == 131072,
            "four MAAs own exactly four times 32 KiB in Runtime");

    LogicalSPDCacheRuntime serial(
        LogicalSPDCacheRuntime::Mode::Serial4K);
    const auto serialPayload = serial.slotPayload(0);
    require(serialPayload.data != nullptr && serialPayload.size == 32768,
            "serial control exposes the same bank as one 32-KiB slot");
    require(serial.slotPayload(1).data == nullptr,
            "serial control exposes no second payload slot");
}

void testModeGeometryAndExactOutput()
{
    LogicalSPDCacheRuntime serial(LogicalSPDCacheRuntime::Mode::Serial4K);
    require(serial.pageElements() == 4096 && serial.slotCount() == 1,
            "Serial4K exposes one 4096-element slot");
    std::array<double, 4096> values{};
    for (std::size_t i = 0; i < values.size(); ++i)
        values[i] = static_cast<double>(i);
    require(LogicalSPDCacheRuntime::Datapath::transform(
                LogicalSPDCacheRuntime::Datapath::Operation::Add,
                {values.data(), values.size()},
                {values.data(), values.size()}, 0) ==
                LogicalSPDCacheRuntime::Datapath::Result::Accepted,
            "Serial4K exact in-place output accepted");
    require(values[4095] == 4095.0, "Serial4K exact output preserved");
    LogicalSPDCacheRuntime ping(LogicalSPDCacheRuntime::Mode::PingPong2K);
    require(ping.pageElements() == 2048 && ping.slotCount() == 2,
            "PingPong2K exposes two 2048-element slots");
}

} // anonymous namespace

int
main()
{
    testRuntimeIsSolePayloadAuthority();
    testModeGeometryAndExactOutput();
    std::cout << "PASS logical_spd_hidden_payload_test\n";
    return 0;
}
