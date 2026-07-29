#include <cassert>

#include "mem/LANLMAA/SharedOverlayCost.hh"

using namespace gem5::lanlmaa;

int
main()
{
    assert(SharedOperationStore.payloadBytes() == 2048);
    assert(SharedLineStore.payloadBytes() == 2816);
    assert(SharedContinuationStore.payloadBytes() == 3072);
    assert(SharedUpdateStore.payloadBytes() == 1536);
    assert(SharedDescriptorStore.payloadBytes() == 512);
    assert(SharedRetirementStore.payloadBytes() == 1024);

    assert(SharedBasePayloadBytes == 11008);
    assert(SpartaActiveContextStore.payloadBytes() == 448);
    assert(SpartaDescriptorControlStore.payloadBytes() == 16);
    assert(SpartaOverlayPayloadBytes == 464);
    assert(SharedSpartaPayloadBytes == 11472);
    assert(provisionedStateBits(SharedSpartaPayloadBytes) / 8 / 1024 == 16);
    assert(SharedTransparentStorageFloorBytes == 16624);

    return 0;
}
