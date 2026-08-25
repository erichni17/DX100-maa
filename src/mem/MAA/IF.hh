#ifndef __MEM_MAA_IF_HH__
#define __MEM_MAA_IF_HH__

#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <string>

#include "base/types.hh"
#include "mem/MAA/SPD.hh"
#include "mem/MAA/TransparentSPDController.hh"
#include "sim/system.hh"
#include "arch/generic/mmu.hh"

namespace gem5 {
class MAA;

enum class FuncUnitType : uint8_t {
    STREAM = 0,
    INDIRECT = 1,
    INVALIDATOR = 2,
    ALU = 3,
    RANGE = 4,
    MAX
};
const std::string func_unit_names[6] = {
    "STREAM",
    "INDIRECT",
    "INVALIDATOR",
    "ALU",
    "RANGE",
    "MAX"};
class Instruction {
public:
    enum class OpcodeType : uint8_t {
        STREAM_LD = 0,
        STREAM_ST = 1,
        INDIR_LD = 2,
        INDIR_ST_SCALAR = 3,
        INDIR_ST_VECTOR = 4,
        INDIR_RMW_SCALAR = 5,
        INDIR_RMW_VECTOR = 6,
        RANGE_LOOP = 7,
        ALU_SCALAR = 8,
        ALU_VECTOR = 9,
        ALU_REDUCE = 10,
        INDIR_LD_VIRTUAL = 11,
        INDIR_LD_SPD_STREAM = 12,
        INDIR_LD_VIRTUAL_INDEX = 13,
        INDIR_LD_INDEX = 14,
        STREAM_PREFETCH = 15,
        VIRTUAL_TILE_ALU_SCALAR = 16,
        MAX
    };
    std::string opcode_names[17] = {
        "STREAM_LD",
        "STREAM_ST",
        "INDIR_LD",
        "INDIR_ST_SCALAR",
        "INDIR_ST_VECTOR",
        "INDIR_RMW_SCALAR",
        "INDIR_RMW_VECTOR",
        "RANGE_LOOP",
        "ALU_SCALAR",
        "ALU_VECTOR",
        "ALU_REDUCE",
        "INDIR_LD_VIRTUAL",
        "INDIR_LD_SPD_STREAM",
        "INDIR_LD_VIRTUAL_INDEX",
        "INDIR_LD_INDEX",
        "STREAM_PREFETCH",
        "VIRTUAL_TILE_ALU_SCALAR"
    };
    enum class OPType : uint8_t
    {
        ADD_OP = 0,
        SUB_OP = 1,
        MUL_OP = 2,
        DIV_OP = 3,
        MIN_OP = 4,
        MAX_OP = 5,
        AND_OP = 6,
        OR_OP = 7,
        XOR_OP = 8,
        SHL_OP = 9,
        SHR_OP = 10,
        GT_OP = 11,
        GTE_OP = 12,
        LT_OP = 13,
        LTE_OP = 14,
        EQ_OP = 15,
        MAX
    };
    std::string optype_names[16] = {
        "ADD",
        "SUB",
        "MUL",
        "DIV",
        "MIN",
        "MAX",
        "AND",
        "OR",
        "XOR",
        "SHL",
        "SHR",
        "GT",
        "GTE",
        "LT",
        "LTE",
        "EQ"};
    enum class DataType : uint8_t {
        UINT32_TYPE = 0,
        INT32_TYPE = 1,
        FLOAT32_TYPE = 2,
        UINT64_TYPE = 3,
        INT64_TYPE = 4,
        FLOAT64_TYPE = 5,
        MAX
    };
    enum class AccessType : uint8_t {
        READ = 0,
        WRITE = 1,
        COMPUTE = 2,
        MAX
    };
    struct MemoryAccess
    {
        int8_t regionID = -1;
        AccessType type = AccessType::COMPUTE;
    };
    static constexpr size_t MaxMemoryAccesses = 5;
    std::string datatype_names[6] = {
        "UINT32",
        "INT32",
        "FLOAT32",
        "UINT64",
        "INT64",
        "FLOAT64"};
    enum class Status : uint8_t {
        Idle = 0,
        Service = 1,
        Finish = 2,
        MAX
    };
    std::string status_names[4] = {
        "Idle",
        "Service",
        "Finish",
        "MAX"};
    enum class TileStatus : uint8_t {
        WaitForInvalidation = 0,
        Invalidating = 1,
        WaitForService = 2,
        Service = 3,
        Finished = 4,
        MAX
    };
    std::string tile_status_names[6] = {
        "WFI",
        "INV",
        "WFS",
        "SRV",
        "FNS",
        "MAX"};
    Addr baseAddr, backingAddr, indexAddr, predicateAddr, resultAddr;
    bool soaJitMaskedIndex;
    bool soaJitOldResult;
    bool soaJitPageFed;
    bool soaJitPredicateWordReceived;
    bool soaJitResultWordReceived;
    uint64_t soaJitPageFedGeneration;
    int16_t soaJitScalarRegID;
    Addr logicalSourceBackingAddr;
    Addr logicalSource2BackingAddr, logicalDestinationBackingAddr;
    Addr minAddr, maxAddr, backingMinAddr, backingMaxAddr;
    Addr indexMinAddr, indexMaxAddr, predicateMinAddr, predicateMaxAddr;
    Addr resultMinAddr, resultMaxAddr;
    Addr logicalSourceMinAddr, logicalSourceMaxAddr;
    Addr logicalSource2MinAddr, logicalSource2MaxAddr;
    Addr logicalDestinationMinAddr, logicalDestinationMaxAddr;
    int8_t addrRangeID, backingAddrRangeID, indexAddrRangeID;
    int8_t predicateAddrRangeID;
    int8_t resultAddrRangeID;
    int8_t logicalSourceAddrRangeID;
    int8_t logicalSource2AddrRangeID, logicalDestinationAddrRangeID;
    int16_t src1RegID, src2RegID, src3RegID, dst1RegID, dst2RegID;
    int16_t src1SpdID, src2SpdID;
    TileStatus src1Status, src2Status;
    bool src1MustBeFinished;
    int16_t dst1SpdID, dst2SpdID;
    TileStatus dst1Status, dst2Status;
    int16_t condSpdID;
    TileStatus condStatus;
    // Software-visible logical descriptor IDs.  Generation and controller
    // lifecycle fields remain inert until the logical controller is wired.
    int16_t src1LogicalID, src2LogicalID, dst1LogicalID;
    // Logical streams use this physical tile ID only as a completion fence;
    // it is never a logical data operand.
    int16_t logicalCompletionSpdID;
    uint64_t src1LogicalGeneration, src2LogicalGeneration,
        dst1LogicalGeneration;
    // {STREAM_LD, INDIR_LD, INDIR_ST, INDIR_RMW, RANGE_LOOP, CONDITION}
    OpcodeType opcode;
    // {ADD, SUB, MUL, DIV, MIN, MAX, GT, GTE, LT, LTE, EQ}
    OPType optype;
    // {Int, Float}
    DataType datatype;
    // {Read, Write, Compute}
    AccessType accessType;
    // {Idle, Translation, Fill, Request, Response}
    Status state;
    // {ALU, STREAM, INDIRECT}
    FuncUnitType funcUniType;
    ContextID CID;
    Addr PC;
    int if_id;
    Instruction();
    std::string print() const;
    int getWordSize(int tile_id);
    int WordSize();
    int core_id;
    int maa_id;
    int func_unit_id;
    // Invalidator ownership is protocol state, not a statistic.  SoA/JIT can
    // reserve several registered regions atomically before any one of them is
    // exposed to execution.
    bool memoryPermitReserved;
    bool memoryPermitGranted;
    bool controllerManaged;
    // A production LogicalTilePageScheduler micro-op.  It uses the existing
    // controller subspan fields but has a distinct completion authority from
    // TransparentSPDController.
    bool logicalPageManaged;
    TransparentSPDController::Action controllerAction;
    uint64_t controllerTransactionID;
    int16_t controllerSrcSlot, controllerDstSlot;
    int controllerPage;
    int controllerElementOffset, controllerElements;
    bool hasLogicalOperands() const {
        return src1LogicalID != -1 || src2LogicalID != -1 ||
               dst1LogicalID != -1;
    }
    bool isLogicalALUScalar() const {
        return opcode == OpcodeType::ALU_SCALAR && src1LogicalID != -1 &&
               src2LogicalID == -1 && dst1LogicalID != -1;
    }
    bool isLogicalALUVector() const {
        return opcode == OpcodeType::ALU_VECTOR && src1LogicalID != -1 &&
               src2LogicalID != -1 && dst1LogicalID != -1;
    }
    bool isLogicalStreamLoad() const {
        return opcode == OpcodeType::STREAM_LD && src1LogicalID == -1 &&
               src2LogicalID == -1 && dst1LogicalID != -1;
    }
    bool isLogicalStreamStore() const {
        return opcode == OpcodeType::STREAM_ST && src1LogicalID != -1 &&
               src2LogicalID == -1 && dst1LogicalID == -1;
    }
    bool isLogicalStream() const {
        return isLogicalStreamLoad() || isLogicalStreamStore();
    }
    /**
     * Guarded no-old-result SoA/JIT form of ordinary vector or scalar RMW.
     *
     * Ordinary INDIR_RMW_VECTOR keeps both SPD sources.  This form has no SPD
     * input or condition tile, forbids dst1 (the legacy old-value result), and
     * uses dst2 only as a completion token.  backing/index/predicate addresses
     * are delivered in instruction words 3/4/5 respectively.
     */
    bool isSoaJitRmw() const {
        return (opcode == OpcodeType::INDIR_RMW_VECTOR ||
                opcode == OpcodeType::INDIR_RMW_SCALAR) &&
               src1SpdID == -1 && src2SpdID == -1 && condSpdID == -1;
    }
    bool isSoaJitScalarRmw() const {
        return isSoaJitRmw() &&
               opcode == OpcodeType::INDIR_RMW_SCALAR;
    }
    bool isSoaJitVectorRmw() const {
        return isSoaJitRmw() && opcode == OpcodeType::INDIR_RMW_VECTOR;
    }
    bool isSoaJitMaskedIndexRmw() const {
        return isSoaJitVectorRmw() && soaJitMaskedIndex;
    }
    bool hasSoaJitOldResult() const {
        return isSoaJitVectorRmw() && soaJitOldResult;
    }
    bool isSoaJitPageFedRmw() const {
        return isSoaJitVectorRmw() && soaJitPageFed;
    }
    bool hasValidSoaJitRmwOperands() const {
        return hasValidSoaJitRmwShape() &&
               (isSoaJitPageFedRmw()
                    ? src1RegID == -1 && src2RegID == -1 &&
                          src3RegID == -1 && soaJitScalarRegID == -1
                    : src1RegID != -1 && src2RegID != -1 &&
                          src3RegID != -1 &&
                          (isSoaJitScalarRmw()
                               ? soaJitScalarRegID != -1
                               : soaJitScalarRegID == -1));
    }
    bool hasValidSoaJitRmwShape() const {
        return isSoaJitRmw() && dst1SpdID == -1 && dst2SpdID != -1 &&
               dst1RegID == -1 && dst2RegID == -1 &&
               (isSoaJitPageFedRmw() ||
                (src1RegID != -1 && src2RegID != -1 &&
                 src3RegID != -1));
    }
    size_t getMemoryAccesses(
        std::array<MemoryAccess, MaxMemoryAccesses> &accesses) const;
    bool hasMemoryHazard(const Instruction &other) const;
};

class IF {
protected:
    Instruction **instructions;
    unsigned int num_instructions_per_maa;
    unsigned int num_maas;
    unsigned int num_tiles;
    bool **valids;
    bool **completion_only_tiles;
    MAA *maa;
    Instruction::TileStatus getTileStatus(int tile_id, uint8_t tile_status);

public:
    IF(unsigned int _num_instructions_per_maa, unsigned int _num_maas,
       unsigned int _num_tiles, MAA *_maa)
        : num_instructions_per_maa(_num_instructions_per_maa),
          num_maas(_num_maas), num_tiles(_num_tiles), maa(_maa) {
        instructions = new Instruction *[num_maas];
        valids = new bool *[num_maas];
        completion_only_tiles = new bool *[num_maas];
        for (int i = 0; i < num_maas; i++) {
            instructions[i] = new Instruction[num_instructions_per_maa];
            valids[i] = new bool[num_instructions_per_maa];
            completion_only_tiles[i] = new bool[num_tiles]();
            for (int j = 0; j < num_instructions_per_maa; j++) {
                valids[i][j] = false;
            }
        }
    }
    ~IF() {
        assert(instructions != nullptr);
        assert(valids != nullptr);
        for (int i = 0; i < num_maas; i++) {
            delete[] instructions[i];
            delete[] valids[i];
            delete[] completion_only_tiles[i];
        }
        delete[] completion_only_tiles;
    }
    bool pushInstruction(Instruction _instruction,
                         int *inserted_slot = nullptr,
                         int ignored_hazard_slot = -1);
    bool canPushRegister(Register _reg);
    bool hasTileReference(int maa_id, int tile_id);
    bool isCompletionOnlyTile(int maa_id, int tile_id) const;
    bool hasLiveSoaJitRmw() const;
    Instruction *getReady(FuncUnitType funcUniType, int maa_id = -1);
    void finishInstructionCompute(Instruction *instruction);
    void finishInstructionInvalidate(Instruction *instruction, int tile_id, uint8_t tile_status);
    void issueInstructionCompute(Instruction *instruction);
    void issueInstructionInvalidate(Instruction *instruction, int tile_id);
};

class AddressRangeType {
protected:
    Addr addr;
    Addr base;
    Addr offset;
    uint8_t rangeID;
    bool valid;

public:
    static const char *const address_range_names[8];
    enum class Type : uint8_t
    {
        SPD_DATA_CACHEABLE_RANGE = 0,
        SPD_DATA_NONCACHEABLE_RANGE = 1,
        SPD_SIZE_RANGE = 2,
        SPD_READY_RANGE = 3,
        SCALAR_RANGE = 4,
        INSTRUCTION_RANGE = 5,
        VIRTUAL_PAGE_READY_RANGE = 6,
        MAX = 7
    };
    AddressRangeType(Addr _addr, AddrRangeList addrRanges);
    std::string print() const;
    Type getType() const { return static_cast<Type>(rangeID); }
    Addr getOffset() const { return offset; }
    bool isValid() const { return valid; }
};
} // namespace gem5

#endif // __MEM_MAA_IF_HH__
