/*
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Shared wire-format helpers for the logical SPD-cache ABI.
 * This header deliberately has no simulator dependencies so guest API and
 * host ABI tests can use the exact same header layout.
 */

#ifndef __GEM5_MAA_LOGICAL_SPD_CACHE_ABI_HH__
#define __GEM5_MAA_LOGICAL_SPD_CACHE_ABI_HH__

#include <cstdint>

namespace gem5
{
namespace maa
{

class LogicalSPDCacheABI
{
  public:
    static constexpr uint8_t LogicalDescriptorCount = 2;
    static constexpr uint8_t NoOperand = 0xff;
    static constexpr uint8_t LegacyPhysicalHighByte = 0x00;
    static constexpr uint8_t ALUScalarOpcode = 8;
    static constexpr uint8_t DataTypeCount = 6;
    static constexpr uint8_t ScalarOperationCount = 16;
    static constexpr uint32_t LogicalElements = 16384;
    static constexpr uint64_t NoAddress = ~uint64_t(0);

    enum class HeaderKind : uint8_t
    {
        Physical,
        LogicalALUScalar,
        Unsupported,
    };

    struct Header
    {
        HeaderKind kind;
        int16_t src1LogicalID;
        int16_t src2LogicalID;
        int16_t dst1LogicalID;

        Header(HeaderKind _kind = HeaderKind::Unsupported,
               int16_t _src1LogicalID = -1,
               int16_t _src2LogicalID = -1,
               int16_t _dst1LogicalID = -1)
            : kind(_kind), src1LogicalID(_src1LogicalID),
              src2LogicalID(_src2LogicalID),
              dst1LogicalID(_dst1LogicalID)
        {
        }
    };

    enum class ScalarValidation : uint8_t
    {
        Valid,
        WrongOpcode,
        UnsupportedDataType,
        UnsupportedOperation,
        InvalidLogicalID,
        LogicalSource2Present,
        AliasedLogicalIDs,
        MixedPhysicalOperands,
        MissingScalarRegister,
        ScalarRegisterOutOfRange,
        ExtraRegisterOperand,
        Conditional,
        UnexpectedBaseAddress,
        MissingSourceBacking,
        NullSourceBacking,
        MissingDestinationBacking,
        NullDestinationBacking,
    };

    enum class DestinationValidation : uint8_t
    {
        Valid,
        UnsupportedDataType,
        MissingDestinationBacking,
        NullDestinationBacking,
        MisalignedDestinationBacking,
        UnregisteredDestinationRange,
        DestinationOutsideRange,
        IncompleteDestinationSpan,
    };

    struct ScalarOperandShape
    {
        uint8_t datatype;
        uint8_t optype;
        int16_t src1LogicalID;
        int16_t src2LogicalID;
        int16_t dst1LogicalID;
        int16_t src1SpdID;
        int16_t src2SpdID;
        int16_t dst1SpdID;
        int16_t dst2SpdID;
        int16_t src1RegID;
        int16_t src2RegID;
        int16_t src3RegID;
        int16_t dst1RegID;
        int16_t dst2RegID;
        int16_t condSpdID;
        uint64_t baseAddr;
        uint64_t sourceBackingAddr;
        uint64_t destinationBackingAddr;

        ScalarOperandShape()
            : datatype(0), optype(0), src1LogicalID(-1),
              src2LogicalID(-1), dst1LogicalID(-1), src1SpdID(-1),
              src2SpdID(-1), dst1SpdID(-1), dst2SpdID(-1),
              src1RegID(-1), src2RegID(-1), src3RegID(-1),
              dst1RegID(-1), dst2RegID(-1), condSpdID(-1),
              baseAddr(NoAddress), sourceBackingAddr(NoAddress),
              destinationBackingAddr(NoAddress)
        {
        }
    };

    static constexpr bool
    validLogicalID(int16_t logicalID)
    {
        return logicalID >= 0 && logicalID < LogicalDescriptorCount;
    }

    /**
     * Decode only the three formerly ignored high bytes of instruction word
     * zero.  Existing physical encoders write zero in all three bytes.  A
     * logical scalar form uses NoOperand in the reserved logical-source-2
     * byte, which makes it disjoint from that legacy all-zero wire image.
     */
    static constexpr uint8_t
    dataTypeBytes(uint8_t datatype)
    {
        return datatype < 3 ? 4 : (datatype < DataTypeCount ? 8 : 0);
    }

    static constexpr uint8_t
    scalarRegisterWords(uint8_t datatype)
    {
        return dataTypeBytes(datatype) / sizeof(uint32_t);
    }

    static Header
    decodeWord0(uint64_t word)
    {
        const uint8_t src1 = static_cast<uint8_t>(word >> 56);
        const uint8_t src2 = static_cast<uint8_t>(word >> 48);
        const uint8_t dst1 = static_cast<uint8_t>(word >> 40);
        if (src1 == LegacyPhysicalHighByte &&
            src2 == LegacyPhysicalHighByte &&
            dst1 == LegacyPhysicalHighByte) {
            return Header(HeaderKind::Physical, -1, -1, -1);
        }
        if (src2 == NoOperand && src1 < LogicalDescriptorCount &&
            dst1 < LogicalDescriptorCount) {
            return Header(HeaderKind::LogicalALUScalar,
                          static_cast<int16_t>(src1), -1,
                          static_cast<int16_t>(dst1));
        }
        return Header();
    }

    static constexpr uint64_t
    encodeLogicalALUScalarHeader(uint8_t logicalSrc1, uint8_t logicalDst1,
                                 uint8_t datatype, uint8_t optype)
    {
        return (static_cast<uint64_t>(logicalSrc1) << 56) |
               (static_cast<uint64_t>(NoOperand) << 48) |
               (static_cast<uint64_t>(logicalDst1) << 40) |
               (static_cast<uint64_t>(ALUScalarOpcode) << 32) |
               (static_cast<uint64_t>(datatype) << 24) |
               (static_cast<uint64_t>(optype) << 16) |
               (static_cast<uint64_t>(NoOperand) << 8) |
               static_cast<uint64_t>(NoOperand);
    }

    static ScalarValidation
    validateLogicalALUScalar(const ScalarOperandShape &shape,
                             uint8_t opcode,
                             uint32_t scalarRegisterCount)
    {
        if (opcode != ALUScalarOpcode)
            return ScalarValidation::WrongOpcode;
        if (shape.datatype >= DataTypeCount)
            return ScalarValidation::UnsupportedDataType;
        if (shape.optype >= ScalarOperationCount)
            return ScalarValidation::UnsupportedOperation;
        if (!validLogicalID(shape.src1LogicalID) ||
            !validLogicalID(shape.dst1LogicalID)) {
            return ScalarValidation::InvalidLogicalID;
        }
        if (shape.src2LogicalID != -1)
            return ScalarValidation::LogicalSource2Present;
        if (shape.src1LogicalID == shape.dst1LogicalID)
            return ScalarValidation::AliasedLogicalIDs;
        if (shape.src1SpdID != -1 || shape.src2SpdID != -1 ||
            shape.dst1SpdID != -1 || shape.dst2SpdID != -1) {
            return ScalarValidation::MixedPhysicalOperands;
        }
        if (shape.src1RegID == -1)
            return ScalarValidation::MissingScalarRegister;
        const uint8_t registerWords = scalarRegisterWords(shape.datatype);
        if (shape.src1RegID < 0 || scalarRegisterCount == 0 ||
            static_cast<uint32_t>(shape.src1RegID) >= scalarRegisterCount ||
            registerWords >
                scalarRegisterCount -
                    static_cast<uint32_t>(shape.src1RegID)) {
            return ScalarValidation::ScalarRegisterOutOfRange;
        }
        if (shape.src2RegID != -1 || shape.src3RegID != -1 ||
            shape.dst1RegID != -1 || shape.dst2RegID != -1) {
            return ScalarValidation::ExtraRegisterOperand;
        }
        if (shape.condSpdID != -1)
            return ScalarValidation::Conditional;
        if (shape.baseAddr != NoAddress)
            return ScalarValidation::UnexpectedBaseAddress;
        if (shape.sourceBackingAddr == NoAddress)
            return ScalarValidation::MissingSourceBacking;
        if (shape.sourceBackingAddr == 0)
            return ScalarValidation::NullSourceBacking;
        if (shape.destinationBackingAddr == NoAddress)
            return ScalarValidation::MissingDestinationBacking;
        if (shape.destinationBackingAddr == 0)
            return ScalarValidation::NullDestinationBacking;
        return ScalarValidation::Valid;
    }

    /**
     * Validate the complete 16K-element destination in the registered range.
     * The range end is exclusive, matching MAA's registered address regions.
     */
    static DestinationValidation
    validateBackingSpan(uint64_t backingAddr, uint8_t datatype,
                        uint64_t rangeBegin, uint64_t rangeEnd)
    {
        if (backingAddr == NoAddress)
            return DestinationValidation::MissingDestinationBacking;
        if (backingAddr == 0)
            return DestinationValidation::NullDestinationBacking;
        const uint8_t wordBytes = dataTypeBytes(datatype);
        if (wordBytes == 0)
            return DestinationValidation::UnsupportedDataType;
        const uint64_t payloadBytes =
            static_cast<uint64_t>(LogicalElements) * wordBytes;
        if (backingAddr % payloadBytes != 0)
            return DestinationValidation::MisalignedDestinationBacking;
        if (rangeBegin >= rangeEnd)
            return DestinationValidation::UnregisteredDestinationRange;
        if (backingAddr < rangeBegin || backingAddr >= rangeEnd) {
            return DestinationValidation::DestinationOutsideRange;
        }
        if (payloadBytes > rangeEnd - backingAddr)
            return DestinationValidation::IncompleteDestinationSpan;
        return DestinationValidation::Valid;
    }

    static DestinationValidation
    validateDestinationSpan(uint64_t destinationBackingAddr,
                            uint8_t datatype, uint64_t rangeBegin,
                            uint64_t rangeEnd)
    {
        return validateBackingSpan(destinationBackingAddr, datatype,
                                   rangeBegin, rangeEnd);
    }

    static DestinationValidation
    validateSourceSpan(uint64_t sourceBackingAddr, uint8_t datatype,
                       uint64_t rangeBegin, uint64_t rangeEnd)
    {
        return validateBackingSpan(sourceBackingAddr, datatype, rangeBegin,
                                   rangeEnd);
    }
};

} // namespace maa
} // namespace gem5

#endif // __GEM5_MAA_LOGICAL_SPD_CACHE_ABI_HH__
