#ifndef __MEM_MAA_XRAGE_ZERO_PAYLOAD_HH__
#define __MEM_MAA_XRAGE_ZERO_PAYLOAD_HH__

#include <cstddef>
#include <cstdint>
#include <limits>

namespace gem5::maa
{

/**
 * Opcode-local limits for the XRAGE A[B[i]] * scalar -> C[i] terminal chain.
 *
 * These limits deliberately do not depend on the generic logical SPD aperture
 * or on true-4K range-pass virtualization. The gem5 model may use STL maps as
 * lookup scaffolding, but every live semantic entry is credit-bounded here.
 */
class XRAGEZeroPayloadContract
{
  public:
    static constexpr uint32_t MaxLogicalEntries = 4096;
    static constexpr uint32_t IndexWordsPerLine = 16;
    static constexpr uint32_t MaxIndexLines =
        MaxLogicalEntries / IndexWordsPerLine;
    static constexpr uint32_t MaxALULanes = 64;

    struct Config
    {
        uint32_t logicalEntries = 0;
        uint32_t offsetEntries = 0;
        uint32_t offsetEpochEntries = 0;
        uint64_t rowLineSlots = 0;
        uint32_t indexLines = 0;
        uint32_t responseSlots = 0;
        uint32_t responseWordsPerSlot = 0;
        uint32_t responseWordPool = 0;
        uint32_t combinerLineSlots = 0;
        uint32_t combinerWords = 0;
        uint32_t outstandingWrites = 0;
        uint32_t wordsPerLine = 0;
        uint32_t aluLanes = 0;
        uint32_t resultWordsPerCycle = 0;
        uint32_t resultBanks = 0;
        bool rangePasses = false;
        uint32_t indexPartitions = 0;
    };

    enum class Result : uint8_t
    {
        Accepted,
        InvalidLogicalEntries,
        InvalidOffsetCapacity,
        InvalidRowCapacity,
        InvalidIndexCapacity,
        InvalidResponseCapacity,
        InvalidCombinerCapacity,
        InvalidWriteCapacity,
        InvalidALUCapacity,
        GenericRangePassEnabled,
    };

    static Result validate(const Config &config)
    {
        if (config.logicalEntries == 0 ||
            config.logicalEntries > MaxLogicalEntries)
            return Result::InvalidLogicalEntries;
        if (config.offsetEntries == 0 ||
            config.offsetEntries > MaxLogicalEntries ||
            config.offsetEpochEntries == 0 ||
            config.offsetEpochEntries > config.offsetEntries)
            return Result::InvalidOffsetCapacity;
        if (config.rowLineSlots == 0 ||
            config.rowLineSlots > MaxLogicalEntries)
            return Result::InvalidRowCapacity;
        if (config.indexLines == 0 || config.indexLines > MaxIndexLines)
            return Result::InvalidIndexCapacity;
        if (config.wordsPerLine == 0 || config.responseSlots == 0 ||
            responseLiveCapacity(config) == 0 ||
            responseLiveCapacity(config) > MaxLogicalEntries ||
            responseWordsPerSlot(config) > MaxLogicalEntries)
            return Result::InvalidResponseCapacity;
        if (config.combinerLineSlots == 0 || config.combinerWords == 0 ||
            config.combinerWords > MaxLogicalEntries ||
            static_cast<uint64_t>(config.combinerLineSlots) *
                    config.wordsPerLine <
                config.combinerWords)
            return Result::InvalidCombinerCapacity;
        if (config.outstandingWrites == 0 ||
            static_cast<uint64_t>(config.outstandingWrites) *
                    config.wordsPerLine >
                MaxLogicalEntries)
            return Result::InvalidWriteCapacity;
        if (config.aluLanes == 0 || config.aluLanes > MaxALULanes ||
            config.resultWordsPerCycle == 0 ||
            config.resultWordsPerCycle > config.aluLanes ||
            config.resultBanks == 0 || config.resultBanks > MaxLogicalEntries)
            return Result::InvalidALUCapacity;
        if (config.rangePasses || config.indexPartitions != 1)
            return Result::GenericRangePassEnabled;
        return Result::Accepted;
    }

    static const char *resultName(Result result)
    {
        switch (result) {
          case Result::Accepted: return "accepted";
          case Result::InvalidLogicalEntries: return "logical_entries";
          case Result::InvalidOffsetCapacity: return "offset_capacity";
          case Result::InvalidRowCapacity: return "row_capacity";
          case Result::InvalidIndexCapacity: return "index_capacity";
          case Result::InvalidResponseCapacity: return "response_capacity";
          case Result::InvalidCombinerCapacity: return "combiner_capacity";
          case Result::InvalidWriteCapacity: return "write_capacity";
          case Result::InvalidALUCapacity: return "alu_capacity";
          case Result::GenericRangePassEnabled: return "generic_range_pass";
        }
        return "unknown";
    }

    struct ByteBreakdown
    {
        size_t operationMetadata = 0;
        size_t offsetMetadata = 0;
        size_t rowMetadata = 0;
        size_t feederMetadata = 0;
        size_t responseMetadata = 0;
        size_t aluAndLinkMetadata = 0;
        size_t combinerMetadata = 0;
        size_t writeMetadata = 0;
        size_t completionMetadata = 0;
        size_t feederPayload = 0;
        size_t responsePayload = 0;
        size_t aluPayload = 0;
        size_t combinerPayload = 0;
        size_t writePayload = 0;
        size_t spdPayload = 0;

        size_t metadataTotal() const
        {
            return operationMetadata + offsetMetadata + rowMetadata +
                   feederMetadata + responseMetadata + aluAndLinkMetadata +
                   combinerMetadata + writeMetadata + completionMetadata;
        }
        size_t internalPayloadTotal() const
        {
            return feederPayload + responsePayload + aluPayload +
                   combinerPayload + writePayload;
        }
    };

    /**
     * Bit-packed semantic capacity ledger. It charges every live architectural
     * field needed by this opcode, while excluding C++ container nodes,
     * allocator padding, SRAM periphery, ports, and wiring. Payload capacities
     * are charged separately from tags/control and from the zero SPD payload.
     */
    static ByteBreakdown byteBreakdown(const Config &config)
    {
        ByteBreakdown bytes;
        const size_t iterationBits = 12; // exactly the strict 4K domain
        const size_t lineWordBits = 3;   // eight FP64 words per A/C line
        const size_t offsetPointerBits = 12;

        // Three base addresses, min/max/stride, scalar, generation/counters,
        // range IDs, opcode state, and the completion-token identity.
        bytes.operationMetadata = 64;
        bytes.offsetMetadata = ceilBytes(config.offsetEntries *
            (iterationBits + lineWordBits + offsetPointerBits + 1));
        bytes.rowMetadata = ceilBytes(config.rowLineSlots *
            (64 + 2 * iterationBits + 2)); // tag, chain ends, valid/claimed
        bytes.feederMetadata = ceilBytes(config.indexLines *
            (64 + 5 + 2 + IndexWordsPerLine * (iterationBits + 4)));
        bytes.responseMetadata = ceilBytes(config.responseSlots *
            (1 + iterationBits + 5 + 3 * 12 + 2 * 64 + iterationBits));
        bytes.aluAndLinkMetadata = ceilBytes(
            config.aluLanes * iterationBits + 64 + config.resultBanks);
        bytes.combinerMetadata = ceilBytes(config.combinerLineSlots *
            (1 + 64 + config.wordsPerLine + 12));
        bytes.writeMetadata = ceilBytes(config.outstandingWrites *
            (1 + 64 + config.wordsPerLine + 4 + 4));
        bytes.completionMetadata =
            ceilBytes(5 * iterationBits + 1) + sizeof(uint32_t);

        bytes.feederPayload = config.indexLines * 64;
        bytes.responsePayload = responseAllocatedWords(config) *
            sizeof(uint64_t);
        bytes.aluPayload = config.aluLanes * sizeof(uint64_t);
        bytes.combinerPayload = config.combinerWords * sizeof(uint64_t);
        bytes.writePayload = config.outstandingWrites * 64;
        bytes.spdPayload = 0;
        return bytes;
    }

    struct TrafficBytes
    {
        uint64_t bMemory = 0;
        uint64_t selectedAMemory = 0;
        uint64_t aluResultLink = 0;
        uint64_t cWrites = 0;
        uint64_t indexSPDRemoved = 0;
        uint64_t resultSPDRemoved = 0;
        uint64_t spdPayload = 0;
    };

    static TrafficBytes traffic(uint64_t logicalEntries)
    {
        TrafficBytes bytes;
        bytes.bMemory = logicalEntries * sizeof(uint32_t);
        bytes.selectedAMemory = logicalEntries * sizeof(uint64_t);
        bytes.aluResultLink = logicalEntries * sizeof(uint64_t);
        bytes.cWrites = logicalEntries * sizeof(uint64_t);
        // Native x3 writes then reads the index tile once.
        bytes.indexSPDRemoved = logicalEntries * 2 * sizeof(uint32_t);
        // Gather write + ALU read + ALU write + stream-store read.
        bytes.resultSPDRemoved = logicalEntries * 4 * sizeof(uint64_t);
        bytes.spdPayload = 0;
        return bytes;
    }

    struct TimedControlBytes
    {
        uint64_t chunks = 0;
        uint64_t instructions = 0;
        uint64_t descriptorMMIO = 0;
        uint64_t boundsRegisterMMIO = 0;
        uint64_t completionReads = 0;

        uint64_t total() const
        {
            return descriptorMMIO + boundsRegisterMMIO + completionReads;
        }
    };

    // Exact useful bytes issued by the matched Spatter ROI. Every native x3
    // stage writes three 64-bit descriptor words; opcode 18 writes all five.
    // Both arms set max once, then min/max per chunk, and poll one uint16
    // token per chunk. Allocation/region-registration control is before ROI.
    static TimedControlBytes nativeX3Control(uint64_t logicalEntries)
    {
        TimedControlBytes bytes;
        bytes.chunks = ceilDiv(logicalEntries, 16384);
        bytes.instructions = bytes.chunks * 4;
        bytes.descriptorMMIO = bytes.instructions * 3 * sizeof(uint64_t);
        bytes.boundsRegisterMMIO = sizeof(uint32_t) +
            bytes.chunks * 2 * sizeof(uint32_t);
        bytes.completionReads = bytes.chunks * sizeof(uint16_t);
        return bytes;
    }

    static TimedControlBytes zeroPayloadX3Control(uint64_t logicalEntries)
    {
        TimedControlBytes bytes;
        bytes.chunks = ceilDiv(logicalEntries, MaxLogicalEntries);
        bytes.instructions = bytes.chunks;
        bytes.descriptorMMIO = bytes.instructions * 5 * sizeof(uint64_t);
        bytes.boundsRegisterMMIO = sizeof(uint32_t) +
            bytes.chunks * 2 * sizeof(uint32_t);
        bytes.completionReads = bytes.chunks * sizeof(uint16_t);
        return bytes;
    }

    static bool spanOverlaps(uint64_t firstA, uint64_t bytesA,
                             uint64_t firstB, uint64_t bytesB)
    {
        if (bytesA == 0 || bytesB == 0)
            return false;
        if (firstA > std::numeric_limits<uint64_t>::max() - bytesA ||
            firstB > std::numeric_limits<uint64_t>::max() - bytesB)
            return true;
        return firstA < firstB + bytesB && firstB < firstA + bytesA;
    }

  private:
    static uint64_t ceilDiv(uint64_t numerator, uint64_t denominator)
    {
        return numerator == 0 ? 0 : 1 + (numerator - 1) / denominator;
    }
    static uint64_t responseWordsPerSlot(const Config &config)
    {
        if (config.responseWordPool != 0)
            return config.responseWordPool;
        return config.responseWordsPerSlot == 0
            ? config.wordsPerLine : config.responseWordsPerSlot;
    }
    static uint64_t responseLiveCapacity(const Config &config)
    {
        if (config.responseWordPool != 0)
            return config.responseWordPool;
        return static_cast<uint64_t>(config.responseSlots) *
            responseWordsPerSlot(config);
    }
    static uint64_t responseAllocatedWords(const Config &config)
    {
        return static_cast<uint64_t>(config.responseSlots) *
            responseWordsPerSlot(config);
    }

    static size_t ceilBytes(uint64_t bits) { return (bits + 7) / 8; }
};

} // namespace gem5::maa

#endif // __MEM_MAA_XRAGE_ZERO_PAYLOAD_HH__
