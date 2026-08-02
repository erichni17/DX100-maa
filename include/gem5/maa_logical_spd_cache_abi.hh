/*
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Shared wire-format helpers for the non-integrated logical SPD-cache ABI.
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
    static constexpr uint64_t NoAddress = ~uint64_t(0);

    enum class HeaderKind : uint8_t
    {
        Physical,
        LogicalALUScalar,
        Unsupported,
    };

    struct Header
    {
        HeaderKind kind = HeaderKind::Unsupported;
        int16_t src1LogicalID = -1;
        int16_t src2LogicalID = -1;
        int16_t dst1LogicalID = -1;
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
        ExtraRegisterOperand,
        Conditional,
        UnexpectedBaseAddress,
        MissingDestinationBacking,
    };

    struct ScalarOperandShape
    {
        uint8_t datatype = 0;
        uint8_t optype = 0;
        int16_t src1LogicalID = -1;
        int16_t src2LogicalID = -1;
        int16_t dst1LogicalID = -1;
        int16_t src1SpdID = -1;
        int16_t src2SpdID = -1;
        int16_t dst1SpdID = -1;
        int16_t dst2SpdID = -1;
        int16_t src1RegID = -1;
        int16_t src2RegID = -1;
        int16_t src3RegID = -1;
        int16_t dst1RegID = -1;
        int16_t dst2RegID = -1;
        int16_t condSpdID = -1;
        uint64_t baseAddr = NoAddress;
        uint64_t destinationBackingAddr = NoAddress;
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
    static constexpr Header
    decodeWord0(uint64_t word)
    {
        const uint8_t src1 = static_cast<uint8_t>(word >> 56);
        const uint8_t src2 = static_cast<uint8_t>(word >> 48);
        const uint8_t dst1 = static_cast<uint8_t>(word >> 40);
        if (src1 == LegacyPhysicalHighByte &&
            src2 == LegacyPhysicalHighByte &&
            dst1 == LegacyPhysicalHighByte) {
            return {HeaderKind::Physical, -1, -1, -1};
        }
        if (src2 == NoOperand && src1 < LogicalDescriptorCount &&
            dst1 < LogicalDescriptorCount) {
            return {HeaderKind::LogicalALUScalar,
                    static_cast<int16_t>(src1), -1,
                    static_cast<int16_t>(dst1)};
        }
        return {};
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

    static constexpr ScalarValidation
    validateLogicalALUScalar(const ScalarOperandShape &shape,
                             uint8_t opcode)
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
        if (shape.src2RegID != -1 || shape.src3RegID != -1 ||
            shape.dst1RegID != -1 || shape.dst2RegID != -1) {
            return ScalarValidation::ExtraRegisterOperand;
        }
        if (shape.condSpdID != -1)
            return ScalarValidation::Conditional;
        if (shape.baseAddr != NoAddress)
            return ScalarValidation::UnexpectedBaseAddress;
        if (shape.destinationBackingAddr == NoAddress)
            return ScalarValidation::MissingDestinationBacking;
        return ScalarValidation::Valid;
    }
};

} // namespace maa
} // namespace gem5

#endif // __GEM5_MAA_LOGICAL_SPD_CACHE_ABI_HH__
