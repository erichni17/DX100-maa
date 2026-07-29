#ifndef __MEM_LANLMAA_SHARED_OVERLAY_COST_HH__
#define __MEM_LANLMAA_SHARED_OVERLAY_COST_HH__

#include <cstdint>

namespace gem5
{
namespace lanlmaa
{

struct SharedStateArray
{
    uint32_t entries;
    uint32_t roundedBitsPerEntry;

    constexpr uint64_t
    payloadBits() const
    {
        return static_cast<uint64_t>(entries) * roundedBitsPerEntry;
    }

    constexpr uint64_t
    payloadBytes() const
    {
        return payloadBits() / 8;
    }
};

constexpr SharedStateArray SharedOperationStore{64, 256};
constexpr SharedStateArray SharedLineStore{32, 704};
constexpr SharedStateArray SharedContinuationStore{64, 384};
constexpr SharedStateArray SharedUpdateStore{64, 192};
constexpr SharedStateArray SharedDescriptorStore{8, 512};
constexpr SharedStateArray SharedRetirementStore{64, 128};

constexpr uint64_t SharedBasePayloadBytes =
    SharedOperationStore.payloadBytes() + SharedLineStore.payloadBytes() +
    SharedContinuationStore.payloadBytes() +
    SharedUpdateStore.payloadBytes() + SharedDescriptorStore.payloadBytes() +
    SharedRetirementStore.payloadBytes();

constexpr uint32_t SharedPairBits =
    SharedOperationStore.roundedBitsPerEntry +
    SharedContinuationStore.roundedBitsPerEntry;
constexpr uint32_t SharedPairBanks = 4;

constexpr uint32_t SpartaSummaryBits = 448;
constexpr SharedStateArray SpartaActiveContextStore{8, 448};
constexpr SharedStateArray SpartaDescriptorControlStore{1, 128};
constexpr uint64_t SpartaOverlayPayloadBytes =
    SpartaActiveContextStore.payloadBytes() +
    SpartaDescriptorControlStore.payloadBytes();
constexpr uint64_t SharedSpartaPayloadBytes =
    SharedBasePayloadBytes + SpartaOverlayPayloadBytes;

constexpr uint32_t AmgLeaseBits = 320;
constexpr uint32_t AmgLeaseEntries = 16;
constexpr uint32_t AmgExecutionEntries = 16;
constexpr uint32_t UmtOperationBits = 256;
constexpr uint32_t UmtContinuationBits = 320;
constexpr uint32_t UmtMaximumPairedContexts = 64;

constexpr uint64_t SharedCoherenceCacheBytes = 4096;
constexpr uint64_t SharedCoherenceQueuePayloadBytes = 1056;
constexpr uint64_t SharedTransparentStorageFloorBytes =
    SharedSpartaPayloadBytes + SharedCoherenceCacheBytes +
    SharedCoherenceQueuePayloadBytes;

constexpr uint64_t SharedProvisionQuantumBits = 8192;

constexpr uint64_t
roundUpBits(uint64_t value, uint64_t quantum)
{
    return ((value + quantum - 1) / quantum) * quantum;
}

constexpr uint64_t
provisionedStateBits(uint64_t payloadBytes)
{
    const uint64_t payloadBits = payloadBytes * 8;
    const uint64_t eccBits = payloadBits / 8;
    const uint64_t protectedBits = payloadBits + eccBits;
    const uint64_t bankingControlBits = protectedBits / 4;
    return roundUpBits(
        protectedBits + bankingControlBits, SharedProvisionQuantumBits);
}

static_assert(SharedBasePayloadBytes == 11008);
static_assert(SharedPairBits == 640);
static_assert(SharedOperationStore.entries % SharedPairBanks == 0);
static_assert(SharedContinuationStore.entries % SharedPairBanks == 0);
static_assert(SpartaSummaryBits <= SharedPairBits);
static_assert(SpartaOverlayPayloadBytes == 464);
static_assert(SharedSpartaPayloadBytes == 11472);
static_assert(provisionedStateBits(SharedSpartaPayloadBytes) == 131072);
static_assert(AmgLeaseBits <= SharedContinuationStore.roundedBitsPerEntry);
static_assert(AmgLeaseEntries + AmgExecutionEntries <=
              SharedContinuationStore.entries);
static_assert(UmtOperationBits <= SharedOperationStore.roundedBitsPerEntry);
static_assert(UmtContinuationBits <=
              SharedContinuationStore.roundedBitsPerEntry);
static_assert(UmtMaximumPairedContexts <= SharedOperationStore.entries);
static_assert(UmtMaximumPairedContexts <= SharedContinuationStore.entries);
static_assert(SharedTransparentStorageFloorBytes == 16624);

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_SHARED_OVERLAY_COST_HH__
