#include <cassert>
#include <cstdint>
#include <iostream>

#include "mem/MAA/XRAGEZeroPayload.hh"

using Contract = gem5::maa::XRAGEZeroPayloadContract;

int
main()
{
    const Contract::Config tiny{
        4096, // logical entries
        4096, // Offset entries
        4096, // Offset epoch entries
        4096, // active Row line slots
        1,    // B feeder lines
        1,    // A response slots
        4,    // packed words per response slot
        4,    // total packed response words
        1,    // C combiner line slots
        1,    // C combiner words
        1,    // acknowledged writes in flight
        8,    // FP64 words per line
        16,   // shared ALU lanes
        1,    // ALU-result link words/cycle
        1,    // ALU-result link banks
        false,
        1,
    };
    assert(Contract::validate(tiny) == Contract::Result::Accepted);

    auto invalid = tiny;
    invalid.logicalEntries = 4097;
    assert(Contract::validate(invalid) ==
           Contract::Result::InvalidLogicalEntries);
    invalid = tiny;
    invalid.offsetEntries = 4097;
    assert(Contract::validate(invalid) ==
           Contract::Result::InvalidOffsetCapacity);
    invalid = tiny;
    invalid.rowLineSlots = 4097;
    assert(Contract::validate(invalid) ==
           Contract::Result::InvalidRowCapacity);
    invalid = tiny;
    invalid.indexLines = 257;
    assert(Contract::validate(invalid) ==
           Contract::Result::InvalidIndexCapacity);
    invalid = tiny;
    invalid.responseWordPool = 4097;
    assert(Contract::validate(invalid) ==
           Contract::Result::InvalidResponseCapacity);
    invalid = tiny;
    invalid.combinerWords = 0;
    assert(Contract::validate(invalid) ==
           Contract::Result::InvalidCombinerCapacity);
    invalid = tiny;
    invalid.outstandingWrites = 513;
    assert(Contract::validate(invalid) ==
           Contract::Result::InvalidWriteCapacity);
    invalid = tiny;
    invalid.aluLanes = 65;
    assert(Contract::validate(invalid) ==
           Contract::Result::InvalidALUCapacity);
    invalid = tiny;
    invalid.rangePasses = true;
    assert(Contract::validate(invalid) ==
           Contract::Result::GenericRangePassEnabled);
    invalid = tiny;
    invalid.indexPartitions = 2;
    assert(Contract::validate(invalid) ==
           Contract::Result::GenericRangePassEnabled);

    const auto storage = Contract::byteBreakdown(tiny);
    assert(storage.operationMetadata == 64);
    assert(storage.offsetMetadata == 14336);
    assert(storage.rowMetadata == 46080);
    assert(storage.feederMetadata == 41);
    assert(storage.responseMetadata == 25);
    assert(storage.aluAndLinkMetadata == 33);
    assert(storage.combinerMetadata == 11);
    assert(storage.writeMetadata == 11);
    assert(storage.completionMetadata == 12);
    assert(storage.metadataTotal() == 60613);
    assert(storage.feederPayload == 64);
    assert(storage.responsePayload == 32);
    assert(storage.aluPayload == 128);
    assert(storage.combinerPayload == 8);
    assert(storage.writePayload == 64);
    assert(storage.internalPayloadTotal() == 296);
    assert(storage.spdPayload == 0);

    // Exact semantic-capacity ledger for the matched XRAGE runner. The
    // 16-slice x 16-row selected geometry has four line slots per row.
    const Contract::Config matched{
        4096, 4096, 4096, 1024, 1, 8, 0, 64, 16, 128, 8, 8,
        16, 1, 1, false, 1,
    };
    assert(Contract::validate(matched) == Contract::Result::Accepted);
    const auto matched_storage = Contract::byteBreakdown(matched);
    assert(matched_storage.metadataTotal() == 26451);
    assert(matched_storage.feederPayload == 64);
    assert(matched_storage.responsePayload == 4096);
    assert(matched_storage.aluPayload == 128);
    assert(matched_storage.combinerPayload == 1024);
    assert(matched_storage.writePayload == 512);
    assert(matched_storage.internalPayloadTotal() == 5824);
    assert(matched_storage.spdPayload == 0);

    const auto strict_4k = Contract::traffic(4096);
    assert(strict_4k.bMemory == 16384);
    assert(strict_4k.selectedAMemory == 32768);
    assert(strict_4k.aluResultLink == 32768);
    assert(strict_4k.cWrites == 32768);
    assert(strict_4k.indexSPDRemoved == 32768);
    assert(strict_4k.resultSPDRemoved == 131072);
    assert(strict_4k.spdPayload == 0);

    const auto xrage_20k = Contract::traffic(20000);
    assert(xrage_20k.bMemory == 80000);
    assert(xrage_20k.selectedAMemory == 160000);
    assert(xrage_20k.aluResultLink == 160000);
    assert(xrage_20k.cWrites == 160000);
    assert(xrage_20k.indexSPDRemoved == 160000);
    assert(xrage_20k.resultSPDRemoved == 640000);
    assert(xrage_20k.indexSPDRemoved + xrage_20k.resultSPDRemoved == 800000);
    assert(xrage_20k.spdPayload == 0);

    const auto native_control = Contract::nativeX3Control(20000);
    assert(native_control.chunks == 2);
    assert(native_control.instructions == 8);
    assert(native_control.descriptorMMIO == 192);
    assert(native_control.boundsRegisterMMIO == 20);
    assert(native_control.completionReads == 4);
    assert(native_control.total() == 216);
    const auto zero_control = Contract::zeroPayloadX3Control(20000);
    assert(zero_control.chunks == 5);
    assert(zero_control.instructions == 5);
    assert(zero_control.descriptorMMIO == 200);
    assert(zero_control.boundsRegisterMMIO == 44);
    assert(zero_control.completionReads == 10);
    assert(zero_control.total() == 254);

    assert(!Contract::spanOverlaps(0x1000, 64, 0x1040, 64));
    assert(Contract::spanOverlaps(0x1000, 64, 0x103f, 64));
    assert(Contract::spanOverlaps(UINT64_MAX - 7, 16, 0x1000, 8));

    std::cout << "XRAGE_ZERO_PAYLOAD_ACCOUNTING_PASS metadata_bytes="
              << storage.metadataTotal() << " internal_payload_bytes="
              << storage.internalPayloadTotal()
              << " spd_payload_bytes=" << storage.spdPayload
              << " removed_spd_bytes_20k="
              << xrage_20k.indexSPDRemoved + xrage_20k.resultSPDRemoved
              << " matched_metadata_bytes="
              << matched_storage.metadataTotal()
              << " matched_internal_payload_bytes="
              << matched_storage.internalPayloadTotal()
              << " native_control_bytes_20k=" << native_control.total()
              << " zero_control_bytes_20k=" << zero_control.total()
              << std::endl;
    return 0;
}
