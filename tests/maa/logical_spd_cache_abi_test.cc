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
const int ScalarRegisterCount = 32;

ABI::ScalarValidation
validate(const ABI::ScalarOperandShape &shape,
         uint8_t opcode = ABI::ALUScalarOpcode)
{
    return ABI::validateLogicalALUScalar(
        shape, opcode, ScalarRegisterCount);
}

ABI::ScalarOperandShape
validShape()
{
    ABI::ScalarOperandShape shape;
    shape.src1LogicalID = 0;
    shape.dst1LogicalID = 1;
    shape.src1RegID = 3;
    shape.sourceBackingAddr = 0x20000;
    shape.destinationBackingAddr = 0x1000;
    return shape;
}

ABI::VectorOperandShape
validVectorShape()
{
    ABI::VectorOperandShape shape;
    shape.src1LogicalID = 0;
    shape.src2LogicalID = 0;
    shape.dst1LogicalID = 1;
    shape.source1BackingAddr = 0x10000;
    shape.source2BackingAddr = 0x10000;
    shape.destinationBackingAddr = 0x20000;
    return shape;
}

ABI::StreamOperandShape
validLogicalLoadShape()
{
    ABI::StreamOperandShape shape;
    shape.dst1LogicalID = 1;
    shape.completionSpdID = 3;
    shape.backingAddr = 0x10000;
    return shape;
}

ABI::StreamOperandShape
validLogicalStoreShape()
{
    ABI::StreamOperandShape shape;
    shape.src1LogicalID = 0;
    shape.completionSpdID = 3;
    shape.backingAddr = 0x10000;
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
    for (uint8_t src1 = 0; src1 < ABI::LogicalDescriptorCount; ++src1) {
        for (uint8_t src2 = 0; src2 < ABI::LogicalDescriptorCount; ++src2) {
            for (uint8_t dst = 0; dst < ABI::LogicalDescriptorCount; ++dst) {
                const uint64_t word = ABI::encodeLogicalALUVectorHeader(
                    src1, src2, dst, 2, 4);
                const auto header = ABI::decodeWord0(word);
                CHECK(header.kind == ABI::HeaderKind::LogicalALUVector);
                CHECK(header.src1LogicalID == src1);
                CHECK(header.src2LogicalID == src2);
                CHECK(header.dst1LogicalID == dst);
                CHECK(((word >> 32) & 0xff) == ABI::ALUVectorOpcode);
            }
        }
    }
    for (uint8_t logical = 0; logical < ABI::LogicalDescriptorCount;
         ++logical) {
        const auto load = ABI::decodeWord0(
            ABI::encodeLogicalStreamLoadHeader(logical, 2, 7));
        CHECK(load.kind == ABI::HeaderKind::LogicalStreamLoad);
        CHECK(load.src1LogicalID == -1);
        CHECK(load.dst1LogicalID == logical);
        const auto store = ABI::decodeWord0(
            ABI::encodeLogicalStreamStoreHeader(logical, 2, 7));
        CHECK(store.kind == ABI::HeaderKind::LogicalStreamStore);
        CHECK(store.src1LogicalID == logical);
        CHECK(store.dst1LogicalID == -1);
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
            const auto kind = ABI::decodeWord0(tagged).kind;
            if (src == ABI::NoOperand &&
                dst < ABI::LogicalDescriptorCount) {
                CHECK(kind == ABI::HeaderKind::LogicalStreamLoad);
            } else if (src < ABI::LogicalDescriptorCount &&
                       dst == ABI::NoOperand) {
                CHECK(kind == ABI::HeaderKind::LogicalStreamStore);
            } else {
                const bool supported = src < ABI::LogicalDescriptorCount &&
                                       dst < ABI::LogicalDescriptorCount;
                CHECK(kind ==
                      (supported ? ABI::HeaderKind::LogicalALUScalar
                                 : ABI::HeaderKind::Unsupported));
            }
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
                if ((src == ABI::NoOperand && mid == ABI::NoOperand &&
                     dst < ABI::LogicalDescriptorCount) ||
                    (src < ABI::LogicalDescriptorCount &&
                     mid == ABI::NoOperand && dst == ABI::NoOperand) ||
                    (mid == ABI::NoOperand &&
                     src < ABI::LogicalDescriptorCount &&
                     dst < ABI::LogicalDescriptorCount) ||
                    (src >= ABI::LogicalVectorIDBias &&
                     src < ABI::LogicalVectorIDBias +
                               ABI::LogicalDescriptorCount &&
                     mid >= ABI::LogicalVectorIDBias &&
                     mid < ABI::LogicalVectorIDBias +
                               ABI::LogicalDescriptorCount &&
                     dst >= ABI::LogicalVectorIDBias &&
                     dst < ABI::LogicalVectorIDBias +
                               ABI::LogicalDescriptorCount)) {
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
testLogicalStreamValidationMatrix()
{
    using Result = ABI::StreamValidation;
    const auto valid_load = validLogicalLoadShape();
    const auto valid_store = validLogicalStoreShape();
    CHECK(ABI::validateLogicalStreamLoad(valid_load, ABI::StreamLoadOpcode) ==
          Result::Valid);
    CHECK(ABI::validateLogicalStreamStore(valid_store,
                                          ABI::StreamStoreOpcode) ==
          Result::Valid);
    for (uint8_t datatype = 0; datatype < ABI::DataTypeCount; ++datatype) {
        for (uint8_t id = 0; id < ABI::LogicalDescriptorCount; ++id) {
            auto load = valid_load;
            load.datatype = datatype;
            load.dst1LogicalID = id;
            CHECK(ABI::validateLogicalStreamLoad(load,
                                                  ABI::StreamLoadOpcode) ==
                  Result::Valid);
            auto store = valid_store;
            store.datatype = datatype;
            store.src1LogicalID = id;
            CHECK(ABI::validateLogicalStreamStore(store,
                                                   ABI::StreamStoreOpcode) ==
                  Result::Valid);
        }
    }
    auto shape = valid_load;
    CHECK(ABI::validateLogicalStreamLoad(shape, ABI::StreamStoreOpcode) ==
          Result::WrongOpcode);
    shape.datatype = ABI::DataTypeCount;
    CHECK(ABI::validateLogicalStreamLoad(shape, ABI::StreamLoadOpcode) ==
          Result::UnsupportedDataType);
    shape = valid_load;
    shape.dst1LogicalID = 2;
    CHECK(ABI::validateLogicalStreamLoad(shape, ABI::StreamLoadOpcode) ==
          Result::InvalidLogicalID);
    shape = valid_load;
    shape.src1SpdID = 0;
    CHECK(ABI::validateLogicalStreamLoad(shape, ABI::StreamLoadOpcode) ==
          Result::MixedPhysicalOperands);
    shape = valid_load;
    shape.completionSpdID = -1;
    CHECK(ABI::validateLogicalStreamLoad(shape, ABI::StreamLoadOpcode) ==
          Result::MissingCompletionIdentity);
    shape = valid_load;
    shape.src1RegID = 0;
    CHECK(ABI::validateLogicalStreamLoad(shape, ABI::StreamLoadOpcode) ==
          Result::RegisterOperandPresent);
    shape = valid_load;
    shape.condSpdID = 0;
    CHECK(ABI::validateLogicalStreamLoad(shape, ABI::StreamLoadOpcode) ==
          Result::Conditional);
    shape = valid_load;
    shape.backingAddr = ABI::NoAddress;
    CHECK(ABI::validateLogicalStreamLoad(shape, ABI::StreamLoadOpcode) ==
          Result::MissingBacking);
    shape = valid_load;
    shape.backingAddr = 0;
    CHECK(ABI::validateLogicalStreamLoad(shape, ABI::StreamLoadOpcode) ==
          Result::NullBacking);
    shape = valid_store;
    shape.dst1SpdID = 0;
    CHECK(ABI::validateLogicalStreamStore(shape, ABI::StreamStoreOpcode) ==
          Result::MixedPhysicalOperands);
    shape = valid_store;
    shape.completionSpdID = -1;
    CHECK(ABI::validateLogicalStreamStore(shape, ABI::StreamStoreOpcode) ==
          Result::MissingCompletionIdentity);
}

void
testLogicalVectorValidationMatrix()
{
    const auto valid = validVectorShape();
    CHECK(ABI::validateLogicalALUVector(valid, ABI::ALUVectorOpcode) ==
          ABI::VectorValidation::Valid);
    for (uint8_t datatype = 0; datatype < ABI::DataTypeCount; ++datatype) {
        for (uint8_t optype = 0; optype < ABI::ScalarOperationCount;
             ++optype) {
            auto accepted = valid;
            accepted.datatype = datatype;
            accepted.optype = optype;
            CHECK(ABI::validateLogicalALUVector(
                      accepted, ABI::ALUVectorOpcode) ==
                  ABI::VectorValidation::Valid);
        }
    }
    auto shape = valid;
    CHECK(ABI::validateLogicalALUVector(shape, ABI::ALUScalarOpcode) ==
          ABI::VectorValidation::WrongOpcode);
    shape = valid;
    shape.datatype = ABI::DataTypeCount;
    CHECK(ABI::validateLogicalALUVector(shape, ABI::ALUVectorOpcode) ==
          ABI::VectorValidation::UnsupportedDataType);
    shape = valid;
    shape.optype = ABI::ScalarOperationCount;
    CHECK(ABI::validateLogicalALUVector(shape, ABI::ALUVectorOpcode) ==
          ABI::VectorValidation::UnsupportedOperation);
    shape = valid;
    shape.src2LogicalID = 2;
    CHECK(ABI::validateLogicalALUVector(shape, ABI::ALUVectorOpcode) ==
          ABI::VectorValidation::InvalidLogicalID);
    shape = valid;
    shape.dst1LogicalID = 0;
    CHECK(ABI::validateLogicalALUVector(shape, ABI::ALUVectorOpcode) ==
          ABI::VectorValidation::AliasedLogicalDestination);
    shape = valid;
    shape.src1SpdID = 0;
    CHECK(ABI::validateLogicalALUVector(shape, ABI::ALUVectorOpcode) ==
          ABI::VectorValidation::MixedPhysicalOperands);
    shape = valid;
    shape.src1RegID = 0;
    CHECK(ABI::validateLogicalALUVector(shape, ABI::ALUVectorOpcode) ==
          ABI::VectorValidation::RegisterOperandPresent);
    shape = valid;
    shape.condSpdID = 0;
    CHECK(ABI::validateLogicalALUVector(shape, ABI::ALUVectorOpcode) ==
          ABI::VectorValidation::Conditional);
    shape = valid;
    shape.baseAddr = 0;
    CHECK(ABI::validateLogicalALUVector(shape, ABI::ALUVectorOpcode) ==
          ABI::VectorValidation::UnexpectedBaseAddress);
    shape = valid;
    shape.source1BackingAddr = ABI::NoAddress;
    CHECK(ABI::validateLogicalALUVector(shape, ABI::ALUVectorOpcode) ==
          ABI::VectorValidation::MissingSource1Backing);
    shape = valid;
    shape.source2BackingAddr = 0;
    CHECK(ABI::validateLogicalALUVector(shape, ABI::ALUVectorOpcode) ==
          ABI::VectorValidation::NullSource2Backing);
    shape = valid;
    shape.destinationBackingAddr = ABI::NoAddress;
    CHECK(ABI::validateLogicalALUVector(shape, ABI::ALUVectorOpcode) ==
          ABI::VectorValidation::MissingDestinationBacking);
    shape = valid;
    shape.source2BackingAddr = 0x30000;
    CHECK(ABI::validateLogicalALUVector(shape, ABI::ALUVectorOpcode) ==
          ABI::VectorValidation::RepeatedLogicalSourceHasDifferentBacking);

    CHECK(ABI::backingSpansOverlap(0x10000, 0x10000, 2));
    CHECK(!ABI::backingSpansOverlap(0x10000, 0x20000, 2));
}

void
testLogicalScalarValidationMatrix()
{
    const auto valid = validShape();
    CHECK(validate(valid) == ABI::ScalarValidation::Valid);
    for (uint8_t datatype = 0; datatype < ABI::DataTypeCount; ++datatype) {
        for (uint8_t optype = 0; optype < ABI::ScalarOperationCount;
             ++optype) {
            auto accepted = valid;
            accepted.datatype = datatype;
            accepted.optype = optype;
            CHECK(validate(accepted) ==
                  ABI::ScalarValidation::Valid);
        }
    }

    auto shape = valid;
    CHECK(validate(shape, 9) ==
          ABI::ScalarValidation::WrongOpcode);
    shape = valid;
    shape.datatype = ABI::DataTypeCount;
    CHECK(validate(shape) ==
          ABI::ScalarValidation::UnsupportedDataType);
    shape = valid;
    shape.optype = ABI::ScalarOperationCount;
    CHECK(validate(shape) ==
          ABI::ScalarValidation::UnsupportedOperation);
    shape = valid;
    shape.src1LogicalID = 2;
    CHECK(validate(shape) ==
          ABI::ScalarValidation::InvalidLogicalID);
    shape = valid;
    shape.src2LogicalID = 0;
    CHECK(validate(shape) ==
          ABI::ScalarValidation::LogicalSource2Present);
    shape = valid;
    shape.dst1LogicalID = shape.src1LogicalID;
    CHECK(validate(shape) ==
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
        CHECK(validate(shape) ==
              ABI::ScalarValidation::MixedPhysicalOperands);
    }

    shape = valid;
    shape.src1RegID = -1;
    CHECK(validate(shape) ==
          ABI::ScalarValidation::MissingScalarRegister);
    shape = valid;
    shape.src1RegID = -2;
    CHECK(validate(shape) ==
          ABI::ScalarValidation::ScalarRegisterOutOfRange);
    for (int16_t reg = 0; reg < ABI::NoOperand; ++reg) {
        shape = valid;
        shape.datatype = 2;
        shape.src1RegID = reg;
        CHECK(validate(shape) ==
              (reg < ScalarRegisterCount
                   ? ABI::ScalarValidation::Valid
                   : ABI::ScalarValidation::ScalarRegisterOutOfRange));
        shape.datatype = 5;
        CHECK(validate(shape) ==
              (reg + 1 < ScalarRegisterCount
                   ? ABI::ScalarValidation::Valid
                   : ABI::ScalarValidation::ScalarRegisterOutOfRange));
    }
    shape = valid;
    CHECK(ABI::validateLogicalALUScalar(shape, ABI::ALUScalarOpcode, 0) ==
          ABI::ScalarValidation::ScalarRegisterOutOfRange);
    int16_t ABI::ScalarOperandShape::*const extraRegisterFields[] = {
        &ABI::ScalarOperandShape::src2RegID,
        &ABI::ScalarOperandShape::src3RegID,
        &ABI::ScalarOperandShape::dst1RegID,
        &ABI::ScalarOperandShape::dst2RegID,
    };
    for (const auto field : extraRegisterFields) {
        shape = valid;
        shape.*field = 0;
        CHECK(validate(shape) ==
              ABI::ScalarValidation::ExtraRegisterOperand);
    }
    shape = valid;
    shape.condSpdID = 0;
    CHECK(validate(shape) ==
          ABI::ScalarValidation::Conditional);
    shape = valid;
    shape.baseAddr = 0;
    CHECK(validate(shape) ==
          ABI::ScalarValidation::UnexpectedBaseAddress);
    shape = valid;
    shape.sourceBackingAddr = ABI::NoAddress;
    CHECK(validate(shape) == ABI::ScalarValidation::MissingSourceBacking);
    shape = valid;
    shape.sourceBackingAddr = 0;
    CHECK(validate(shape) == ABI::ScalarValidation::NullSourceBacking);
    shape = valid;
    shape.destinationBackingAddr = ABI::NoAddress;
    CHECK(validate(shape) ==
          ABI::ScalarValidation::MissingDestinationBacking);
    shape = valid;
    shape.destinationBackingAddr = 0;
    CHECK(validate(shape) == ABI::ScalarValidation::NullDestinationBacking);
}

void
testDestinationBackingValidation()
{
    using Result = ABI::DestinationValidation;
    const uint64_t fp32Addr = 0x10000;
    const uint64_t fp32Bytes =
        static_cast<uint64_t>(ABI::LogicalElements) * 4;
    const uint64_t fp64Addr = 0x20000;
    const uint64_t fp64Bytes =
        static_cast<uint64_t>(ABI::LogicalElements) * 8;

    CHECK(ABI::dataTypeBytes(2) == 4);
    CHECK(ABI::dataTypeBytes(5) == 8);
    CHECK(ABI::validateDestinationSpan(
              fp32Addr, 2, fp32Addr, fp32Addr + fp32Bytes) ==
          Result::Valid);
    CHECK(ABI::validateDestinationSpan(
              fp64Addr, 5, fp64Addr, fp64Addr + fp64Bytes) ==
          Result::Valid);
    CHECK(ABI::validateDestinationSpan(
              ABI::NoAddress, 2, fp32Addr, fp32Addr + fp32Bytes) ==
          Result::MissingDestinationBacking);
    CHECK(ABI::validateDestinationSpan(
              0, 2, 0, fp32Bytes) == Result::NullDestinationBacking);
    CHECK(ABI::validateDestinationSpan(
              fp32Addr + 4, 2, fp32Addr,
              fp32Addr + fp32Bytes + 4) ==
          Result::MisalignedDestinationBacking);
    CHECK(ABI::validateDestinationSpan(fp32Addr, 2, 0x2000, 0x2000) ==
          Result::UnregisteredDestinationRange);
    CHECK(ABI::validateDestinationSpan(
              fp32Addr, 2, fp32Addr + 4,
              fp32Addr + fp32Bytes + 4) ==
          Result::DestinationOutsideRange);
    CHECK(ABI::validateDestinationSpan(
              fp32Addr, 2, fp32Addr,
              fp32Addr + fp32Bytes - 1) ==
          Result::IncompleteDestinationSpan);
    CHECK(ABI::validateDestinationSpan(
              fp64Addr, 5, fp64Addr,
              fp64Addr + fp64Bytes - 1) ==
          Result::IncompleteDestinationSpan);
    CHECK(ABI::validateDestinationSpan(
              UINT64_MAX - fp64Bytes + 1, 5,
              UINT64_MAX - fp64Bytes + 1, UINT64_MAX) ==
          Result::IncompleteDestinationSpan);
    CHECK(ABI::validateDestinationSpan(
              fp32Addr, ABI::DataTypeCount, fp32Addr,
              fp32Addr + fp32Bytes) == Result::UnsupportedDataType);
}

void
testGuestAPIWritesTheSharedWireImage()
{
    uint64_t word0 = 0;
    uint64_t word1 = 0;
    uint64_t word2 = 0;
    uint64_t word3 = 0;
    uint64_t word4 = 0;
    uint64_t word5 = 0;
    auto *source = reinterpret_cast<uint32_t *>(0x10000);
    auto *destination = reinterpret_cast<uint32_t *>(0x20000);
    INSTR_opcode_datatype_optype_tdst1_tdst2 = &word0;
    INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc = &word1;
    INSTR_baseaddr = &word2;
    INSTR_backingaddr = &word3;
    INSTR_indexaddr = &word4;
    INSTR_predicateaddr = &word5;

    maa_alu_scalar_logical<uint32_t>(0, 1, source, destination, 3,
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
    CHECK(word4 == reinterpret_cast<uint64_t>(source));

    maa_alu_scalar<uint32_t>(7, 3, 5, Operation_t::ADD_OP);
    CHECK((word0 >> 40) == 0);
    CHECK(((word0 >> 32) & 0xff) == ABI::ALUScalarOpcode);
    CHECK(ABI::decodeWord0(word0).kind == ABI::HeaderKind::Physical);

    maa_alu_vector_logical<uint32_t>(0, 0, 1, source, source, destination,
                                     Operation_t::ADD_OP);
    CHECK(word0 == ABI::encodeLogicalALUVectorHeader(
                       0, 0, 1, static_cast<uint8_t>(DataType::UINT32_TYPE),
                       static_cast<uint8_t>(Operation_t::ADD_OP)));
    CHECK(word1 == UINT64_MAX);
    CHECK(word2 == ABI::NoAddress);
    CHECK(word3 == reinterpret_cast<uint64_t>(destination));
    CHECK(word4 == reinterpret_cast<uint64_t>(source));
    CHECK(word5 == reinterpret_cast<uint64_t>(source));

    maa_stream_load_logical<uint32_t>(source, 1, 7);
    CHECK(word0 == ABI::encodeLogicalStreamLoadHeader(
                       1, static_cast<uint8_t>(DataType::UINT32_TYPE), 7));
    CHECK(word1 == UINT64_MAX);
    CHECK(word2 == reinterpret_cast<uint64_t>(source));
    maa_stream_store_logical<uint32_t>(destination, 0, 6);
    CHECK(word0 == ABI::encodeLogicalStreamStoreHeader(
                       0, static_cast<uint8_t>(DataType::UINT32_TYPE), 6));
    CHECK(word1 == UINT64_MAX);
    CHECK(word2 == reinterpret_cast<uint64_t>(destination));
}

} // anonymous namespace

int
main()
{
    testHeaderDecodeMatrix();
    testLogicalScalarValidationMatrix();
    testLogicalVectorValidationMatrix();
    testLogicalStreamValidationMatrix();
    testDestinationBackingValidation();
    testGuestAPIWritesTheSharedWireImage();
    std::cout << "logical_spd_cache_abi_test: PASS" << std::endl;
    return 0;
}
