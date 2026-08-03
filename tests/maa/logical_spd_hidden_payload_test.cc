#include <cstddef>
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
    static_assert(Slice::PageBytes == 32768);
    static_assert(Ledger::PrivatePayloadBits / 8 == 65536);
    static_assert(
        LogicalSPDPrivatePayloadAccounting::PayloadBytesPerMAA == 65536);

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
                    "Runtime slot is exactly 32 KiB");
            require(payloadBases.insert(payload.data).second,
                    "Runtime payload slots never alias");
            for (std::size_t byte = 0; byte < payload.size; ++byte)
                require(payload.data[byte] == std::byte{0},
                        "Runtime payload is construction-zero");
            totalBytes += payload.size;
        }
    }
    require(totalBytes == 262144,
            "four MAAs own exactly four times 64 KiB in Runtime");
}

} // anonymous namespace

int
main()
{
    testRuntimeIsSolePayloadAuthority();
    std::cout << "PASS logical_spd_hidden_payload_test\n";
    return 0;
}
