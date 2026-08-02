#include <cstdint>
#include <cstdlib>
#include <iostream>

#include "MAA_gem5.hpp"
#include "gem5/maa_logical_spd_cache_abi.hh"

// MAA_gem5.hpp defines region helpers in the header.  The host ABI test never
// calls them, but its object still needs these two guest-only m5ops resolved.
extern "C" void
m5_add_mem_region(void *, void *, int8_t)
{
}

extern "C" void
m5_clear_mem_region()
{
}

namespace {

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;         \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

using ABI = gem5::maa::LogicalSPDCacheABI;

ABI::ScalarOperandShape
validShape()
{
    ABI::ScalarOperandShape shape;
    shape.src1LogicalID = 0;
    shape.dst1LogicalID = 1;
    shape.src1RegID = 3;
    shape.destinationBackingAddr = 0x1000;
    return shape;
}

void
testHeaderDecodeMatrix()
{
    const uint64_t physical =
        (static_cast<uint64_t>(ABI::ALUScalarOpcode) << 32) |
        (static_cast<uint64_t>(2) << 24) |
        (static_cast<uint64_t>(4) << 16) |
        (static_cast<uint64_t>(7) << 8) | 0xff;
    const auto physicalHeader = ABI::decodeWord0(physical);
    CHECK(physicalHeader.kind == ABI::HeaderKind::Physical);
    CHECK(physicalHeader.src1LogicalID == -1);
    CHECK(physicalHeader.src2LogicalID == -1);
    CHECK(physicalHeader.dst1LogicalID == -1);

    for (uint8_t src = 0; src < ABI::LogicalDescriptorCount; ++src) {
        for (uint8_t dst = 0; dst < ABI::LogicalDescriptorCount; ++dst) {
            const uint64_t word = ABI::encodeLogicalALUScalarHeader(
                src, dst, 2, 4);
            const auto header = ABI::decodeWord0(word);
            CHECK(header.kind == ABI::HeaderKind::LogicalALUScalar);
            CHECK(header.src1LogicalID == src);
            CHECK(header.src2LogicalID == -1);
            CHECK(header.dst1LogicalID == dst);
            CHECK((word & 0xffff) == 0xffff);
            CHECK(((word >> 32) & 0xff) == ABI::ALUScalarOpcode);
        }
    }

    // Every high-byte shape outside legacy physical or the tagged logical
    // subset is rejected.  This includes the plan's incompatible all-ff
    // physical convention and all out-of-range logical IDs.
    for (uint16_t src = 0; src < 256; ++src) {
        for (uint16_t dst = 0; dst < 256; ++dst) {
            const uint64_t tagged = (static_cast<uint64_t>(src) << 56) |
                                    (static_cast<uint64_t>(ABI::NoOperand)
                                     << 48) |
                                    (static_cast<uint64_t>(dst) << 40);
            const bool supported = src < ABI::LogicalDescriptorCount &&
                                   dst < ABI::LogicalDescriptorCount;
            CHECK(ABI::decodeWord0(tagged).kind ==
                  (supported ? ABI::HeaderKind::LogicalALUScalar
                             : ABI::HeaderKind::Unsupported));
        }
    }
    for (uint16_t src = 0; src < 256; ++src) {
        for (uint16_t mid = 0; mid < 256; ++mid) {
            for (uint16_t dst = 0; dst < 256; ++dst) {
                if (src == ABI::LegacyPhysicalHighByte &&
                    mid == ABI::LegacyPhysicalHighByte &&
                    dst == ABI::LegacyPhysicalHighByte) {
                    continue;
                }
                if (mid == ABI::NoOperand &&
                    src < ABI::LogicalDescriptorCount &&
                    dst < ABI::LogicalDescriptorCount) {
                    continue;
                }
                const uint64_t word = (static_cast<uint64_t>(src) << 56) |
                                      (static_cast<uint64_t>(mid) << 48) |
                                      (static_cast<uint64_t>(dst) << 40);
                CHECK(ABI::decodeWord0(word).kind ==
                      ABI::HeaderKind::Unsupported);
            }
        }
    }
}

void
testLogicalScalarValidationMatrix()
{
    const auto valid = validShape();
    CHECK(ABI::validateLogicalALUScalar(valid, ABI::ALUScalarOpcode) ==
          ABI::ScalarValidation::Valid);
    for (uint8_t datatype = 0; datatype < ABI::DataTypeCount; ++datatype) {
        for (uint8_t optype = 0; optype < ABI::ScalarOperationCount;
             ++optype) {
            auto accepted = valid;
            accepted.datatype = datatype;
            accepted.optype = optype;
            CHECK(ABI::validateLogicalALUScalar(
                      accepted, ABI::ALUScalarOpcode) ==
                  ABI::ScalarValidation::Valid);
        }
    }

    auto shape = valid;
    CHECK(ABI::validateLogicalALUScalar(shape, 9) ==
          ABI::ScalarValidation::WrongOpcode);
    shape = valid;
    shape.datatype = ABI::DataTypeCount;
    CHECK(ABI::validateLogicalALUScalar(shape, ABI::ALUScalarOpcode) ==
          ABI::ScalarValidation::UnsupportedDataType);
    shape = valid;
    shape.optype = ABI::ScalarOperationCount;
    CHECK(ABI::validateLogicalALUScalar(shape, ABI::ALUScalarOpcode) ==
          ABI::ScalarValidation::UnsupportedOperation);
    shape = valid;
    shape.src1LogicalID = 2;
    CHECK(ABI::validateLogicalALUScalar(shape, ABI::ALUScalarOpcode) ==
          ABI::ScalarValidation::InvalidLogicalID);
    shape = valid;
    shape.src2LogicalID = 0;
    CHECK(ABI::validateLogicalALUScalar(shape, ABI::ALUScalarOpcode) ==
          ABI::ScalarValidation::LogicalSource2Present);
    shape = valid;
    shape.dst1LogicalID = shape.src1LogicalID;
    CHECK(ABI::validateLogicalALUScalar(shape, ABI::ALUScalarOpcode) ==
          ABI::ScalarValidation::AliasedLogicalIDs);

    int16_t ABI::ScalarOperandShape::*const physicalFields[] = {
        &ABI::ScalarOperandShape::src1SpdID,
        &ABI::ScalarOperandShape::src2SpdID,
        &ABI::ScalarOperandShape::dst1SpdID,
        &ABI::ScalarOperandShape::dst2SpdID,
    };
    for (const auto field : physicalFields) {
        shape = valid;
        shape.*field = 0;
        CHECK(ABI::validateLogicalALUScalar(shape, ABI::ALUScalarOpcode) ==
              ABI::ScalarValidation::MixedPhysicalOperands);
    }

    shape = valid;
    shape.src1RegID = -1;
    CHECK(ABI::validateLogicalALUScalar(shape, ABI::ALUScalarOpcode) ==
          ABI::ScalarValidation::MissingScalarRegister);
    int16_t ABI::ScalarOperandShape::*const extraRegisterFields[] = {
        &ABI::ScalarOperandShape::src2RegID,
        &ABI::ScalarOperandShape::src3RegID,
        &ABI::ScalarOperandShape::dst1RegID,
        &ABI::ScalarOperandShape::dst2RegID,
    };
    for (const auto field : extraRegisterFields) {
        shape = valid;
        shape.*field = 0;
        CHECK(ABI::validateLogicalALUScalar(shape, ABI::ALUScalarOpcode) ==
              ABI::ScalarValidation::ExtraRegisterOperand);
    }
    shape = valid;
    shape.condSpdID = 0;
    CHECK(ABI::validateLogicalALUScalar(shape, ABI::ALUScalarOpcode) ==
          ABI::ScalarValidation::Conditional);
    shape = valid;
    shape.baseAddr = 0;
    CHECK(ABI::validateLogicalALUScalar(shape, ABI::ALUScalarOpcode) ==
          ABI::ScalarValidation::UnexpectedBaseAddress);
    shape = valid;
    shape.destinationBackingAddr = ABI::NoAddress;
    CHECK(ABI::validateLogicalALUScalar(shape, ABI::ALUScalarOpcode) ==
          ABI::ScalarValidation::MissingDestinationBacking);
}

void
testGuestAPIWritesTheSharedWireImage()
{
    uint64_t word0 = 0;
    uint64_t word1 = 0;
    uint64_t word2 = 0;
    uint64_t word3 = 0;
    uint32_t destination[1]{};
    INSTR_opcode_datatype_optype_tdst1_tdst2 = &word0;
    INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc = &word1;
    INSTR_baseaddr = &word2;
    INSTR_backingaddr = &word3;

    maa_alu_scalar_logical<uint32_t>(0, 1, destination, 3,
                                     Operation_t::MIN_OP);

    CHECK(word0 == ABI::encodeLogicalALUScalarHeader(
                       0, 1, static_cast<uint8_t>(DataType::UINT32_TYPE),
                       static_cast<uint8_t>(Operation_t::MIN_OP)));
    CHECK(word1 == ((static_cast<uint64_t>(0xff) << 56) |
                    (static_cast<uint64_t>(0xff) << 48) |
                    (static_cast<uint64_t>(0xff) << 40) |
                    (static_cast<uint64_t>(0xff) << 32) |
                    (static_cast<uint64_t>(3) << 24) |
                    (static_cast<uint64_t>(0xff) << 16) |
                    (static_cast<uint64_t>(0xff) << 8) | 0xff));
    CHECK(word2 == ABI::NoAddress);
    CHECK(word3 == reinterpret_cast<uint64_t>(destination));

    maa_alu_scalar<uint32_t>(7, 3, 5, Operation_t::ADD_OP);
    CHECK((word0 >> 40) == 0);
    CHECK(((word0 >> 32) & 0xff) == ABI::ALUScalarOpcode);
    CHECK(ABI::decodeWord0(word0).kind == ABI::HeaderKind::Physical);
}

} // anonymous namespace

int
main()
{
    testHeaderDecodeMatrix();
    testLogicalScalarValidationMatrix();
    testGuestAPIWritesTheSharedWireImage();
    std::cout << "logical_spd_cache_abi_test: PASS" << std::endl;
    return 0;
}
