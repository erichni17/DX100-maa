#include "mem/MAA/IF.hh"
#include "mem/MAA/MAA.hh"
#include "debug/MAAController.hh"
#include "mem/MAA/SPD.hh"
#include <cassert>

#ifndef TRACING_ON
#define TRACING_ON 1
#endif

namespace gem5 {
Instruction::Instruction() : baseAddr(0xFFFFFFFFFFFFFFFF),
                             backingAddr(0xFFFFFFFFFFFFFFFF),
                             indexAddr(0xFFFFFFFFFFFFFFFF),
                             minAddr(0xFFFFFFFFFFFFFFFF),
                             maxAddr(0xFFFFFFFFFFFFFFFF),
                             backingMinAddr(0xFFFFFFFFFFFFFFFF),
                             backingMaxAddr(0xFFFFFFFFFFFFFFFF),
                             indexMinAddr(0xFFFFFFFFFFFFFFFF),
                             indexMaxAddr(0xFFFFFFFFFFFFFFFF),
                             addrRangeID(-1),
                             backingAddrRangeID(-1),
                             indexAddrRangeID(-1),
                             src1RegID(-1),
                             src2RegID(-1),
                             src3RegID(-1),
                             dst1RegID(-1),
                             dst2RegID(-1),
                             src1SpdID(-1),
                             src2SpdID(-1),
                             src1Status(TileStatus::WaitForInvalidation),
                             src2Status(TileStatus::WaitForInvalidation),
                             src1MustBeFinished(false),
                             dst1SpdID(-1),
                             dst2SpdID(-1),
                             dst1Status(TileStatus::WaitForInvalidation),
                             dst2Status(TileStatus::WaitForInvalidation),
                             condSpdID(-1),
                             condStatus(TileStatus::WaitForInvalidation),
                             opcode(OpcodeType::MAX),
                             optype(OPType::MAX),
                             datatype(DataType::MAX),
                             accessType(AccessType::MAX),
                             state(Status::Idle),
                             funcUniType(FuncUnitType::MAX),
                             CID(-1),
                             PC(0),
                             if_id(-1),
                             core_id(-1),
                             maa_id(-1),
                             func_unit_id(-1),
                             controllerManaged(false),
                             controllerAction(
                                 TransparentSPDController::Action::None),
                             controllerPage(-1) {}
std::string Instruction::print() const {
    char baseAddrStr[32];
    std::sprintf(baseAddrStr, "0x%lx", baseAddr);
    char minAddrStr[32];
    std::sprintf(minAddrStr, "0x%lx", minAddr);
    char maxAddrStr[32];
    std::sprintf(maxAddrStr, "0x%lx", maxAddr);
    std::ostringstream str;
    ccprintf(str, "INSTR[%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s]",
             "core_id(" + std::to_string(core_id) + ")",
             " maa_id(" + std::to_string(maa_id) + ")",
             func_unit_id == -1 ? "" : " unit_id(" + std::to_string(func_unit_id) + ")",
             " opcode(" + opcode_names[(int)opcode] + ")",
             optype == OPType::MAX ? "" : " optype(" + optype_names[(int)optype] + ")",
             " datatype(" + datatype_names[(int)datatype] + ")",
             " state(" + status_names[(int)state] + ")",
             src1SpdID == -1 ? "" : " srcSPD1(" + std::to_string(src1SpdID) + "/" + tile_status_names[(uint8_t)src1Status] + ")",
             src2SpdID == -1 ? "" : " srcSPD2(" + std::to_string(src2SpdID) + "/" + tile_status_names[(uint8_t)src2Status] + ")",
             src1RegID == -1 ? "" : " srcREG1(" + std::to_string(src1RegID) + ")",
             src2RegID == -1 ? "" : " srcREG2(" + std::to_string(src2RegID) + ")",
             src3RegID == -1 ? "" : " srcREG3(" + std::to_string(src3RegID) + ")",
             dst1SpdID == -1 ? "" : " dstSPD1(" + std::to_string(dst1SpdID) + "/" + tile_status_names[(uint8_t)dst1Status] + ")",
             dst2SpdID == -1 ? "" : " dstSPD2(" + std::to_string(dst2SpdID) + "/" + tile_status_names[(uint8_t)dst2Status] + ")",
             dst1RegID == -1 ? "" : " dstREG1(" + std::to_string(dst1RegID) + ")",
             dst2RegID == -1 ? "" : " dstREG2(" + std::to_string(dst2RegID) + ")",
             condSpdID == -1 ? "" : " condSPD(" + std::to_string(condSpdID) + "/" + tile_status_names[(uint8_t)condStatus] + ")",
             baseAddr != 0xFFFFFFFFFFFFFFFF ? " baseAddr(" + std::string(baseAddrStr) + ") minAddr(" + std::string(minAddrStr) + ") maxAddr(" + std::string(maxAddrStr) + ")" : "");
    return str.str();
}
int Instruction::getWordSize(int tile_id) {
    panic_if(tile_id == -1, "Invalid tile_id %d!\n", tile_id);
    if (tile_id == condSpdID) {
        return 4;
    } else if (tile_id == src1SpdID) {
        switch (opcode) {
        case OpcodeType::ALU_SCALAR:
        case OpcodeType::ALU_VECTOR:
        case OpcodeType::ALU_REDUCE:
        case OpcodeType::STREAM_ST: {
            return WordSize();
        }
        case OpcodeType::INDIR_LD:
        case OpcodeType::INDIR_LD_VIRTUAL:
        case OpcodeType::INDIR_LD_VIRTUAL_INDEX:
        case OpcodeType::INDIR_LD_INDEX:
        case OpcodeType::INDIR_LD_SPD_STREAM:
        case OpcodeType::INDIR_ST_VECTOR:
        case OpcodeType::INDIR_ST_SCALAR:
        case OpcodeType::INDIR_RMW_VECTOR:
        case OpcodeType::INDIR_RMW_SCALAR:
        case OpcodeType::RANGE_LOOP: {
            return 4;
        }
        default:
            assert(false);
        }
    } else if (tile_id == src2SpdID) {
        switch (opcode) {
        case OpcodeType::INDIR_ST_VECTOR:
        case OpcodeType::INDIR_RMW_VECTOR:
        case OpcodeType::ALU_VECTOR: {
            return WordSize();
        }
        case OpcodeType::RANGE_LOOP: {
            return 4;
        }
        default:
            assert(false);
        }
    } else if (tile_id == dst1SpdID) {
        switch (opcode) {
        case OpcodeType::ALU_SCALAR:
        case OpcodeType::ALU_VECTOR: {
            if (optype == OPType::GT_OP || optype == OPType::GTE_OP || optype == OPType::LT_OP || optype == OPType::LTE_OP || optype == OPType::EQ_OP) {
                return 4;
            } else {
                return WordSize();
            }
        }
        case OpcodeType::STREAM_LD:
        case OpcodeType::STREAM_PREFETCH:
        case OpcodeType::INDIR_LD:
        case OpcodeType::INDIR_LD_VIRTUAL:
        case OpcodeType::INDIR_LD_VIRTUAL_INDEX:
        case OpcodeType::INDIR_LD_INDEX:
        case OpcodeType::INDIR_LD_SPD_STREAM:
        case OpcodeType::INDIR_ST_VECTOR:
        case OpcodeType::INDIR_ST_SCALAR:
        case OpcodeType::INDIR_RMW_VECTOR:
        case OpcodeType::INDIR_RMW_SCALAR: {
            return WordSize();
        }
        case OpcodeType::RANGE_LOOP: {
            return 4;
        }
        default:
            assert(false);
        }
    } else if (tile_id == dst2SpdID) {
        switch (opcode) {
        case OpcodeType::INDIR_LD_VIRTUAL_INDEX:
        case OpcodeType::RANGE_LOOP: {
            return 4;
        }
        default:
            assert(false);
        }
    } else {
        assert(false);
    }
    assert(false);
    return -1;
}
int Instruction::WordSize() {
    switch (datatype) {
    case DataType::UINT32_TYPE:
    case DataType::INT32_TYPE:
    case DataType::FLOAT32_TYPE:
        return 4;
    case DataType::UINT64_TYPE:
    case DataType::INT64_TYPE:
    case DataType::FLOAT64_TYPE:
        return 8;
    default:
        assert(false);
    }
    assert(false);
    return -1;
}
bool IF::pushInstruction(Instruction _instruction, int *inserted_slot,
                         int ignored_hazard_slot) {
    if (!_instruction.controllerManaged) {
        const int tiles[] = {
            _instruction.src1SpdID, _instruction.src2SpdID,
            _instruction.dst1SpdID, _instruction.dst2SpdID,
            _instruction.condSpdID,
        };
        for (const int tile : tiles) {
            if (tile == -1)
                continue;
            const int tile_words =
                _instruction.getWordSize(tile) / sizeof(uint32_t);
            for (int offset = 0; offset < tile_words; ++offset) {
                if (!maa->transparentControllerOwnsTile(
                        _instruction.maa_id, tile + offset))
                    continue;
                DPRINTF(MAAController,
                        "%s: %s cannot be pushed because transparent "
                        "controller owns tile %d\n",
                        __func__, _instruction.print(), tile + offset);
                return false;
            }
        }
        const int registers[] = {
            _instruction.dst1RegID, _instruction.dst2RegID,
            _instruction.src1RegID, _instruction.src2RegID,
            _instruction.src3RegID,
        };
        const int register_words = _instruction.WordSize() / sizeof(uint32_t);
        for (const int register_id : registers) {
            if (register_id != -1 &&
                maa->transparentControllerUsesRegister(
                    _instruction.maa_id, register_id, register_words))
                return false;
        }
    }
    if (_instruction.opcode ==
            Instruction::OpcodeType::INDIR_LD_VIRTUAL_INDEX &&
        _instruction.dst2SpdID != -1) {
        const int maa_id = _instruction.maa_id;
        const int prefetch_token = _instruction.dst2SpdID;
        const bool old_completion =
            completion_only_tiles[maa_id][_instruction.dst1SpdID];
        const bool old_prefetch =
            completion_only_tiles[maa_id][prefetch_token];

        Instruction stream = _instruction;
        stream.opcode = Instruction::OpcodeType::STREAM_PREFETCH;
        stream.datatype = Instruction::DataType::INT32_TYPE;
        stream.accessType = Instruction::AccessType::READ;
        stream.baseAddr = _instruction.indexAddr;
        stream.minAddr = _instruction.indexMinAddr;
        stream.maxAddr = _instruction.indexMaxAddr;
        stream.addrRangeID = _instruction.indexAddrRangeID;
        stream.dst1SpdID = prefetch_token;
        stream.dst1Status = _instruction.dst2Status;
        stream.dst2SpdID = -1;
        stream.src1SpdID = stream.src2SpdID = stream.condSpdID = -1;
        stream.backingAddr = stream.indexAddr = 0xFFFFFFFFFFFFFFFF;
        stream.backingAddrRangeID = stream.indexAddrRangeID = -1;

        Instruction load = _instruction;
        load.dst2SpdID = -1;
        load.src1SpdID = prefetch_token;
        load.src1Status = Instruction::TileStatus::WaitForService;
        load.src1MustBeFinished = false;

        int stream_slot = -1;
        int load_slot = -1;
        if (!pushInstruction(stream, &stream_slot))
            return false;
        if (!pushInstruction(load, &load_slot)) {
            panic_if(stream_slot < 0,
                     "Fused virtual-index prefetch did not report its slot\n");
            DPRINTF(MAAController,
                    "%s: rolled back fused virtual-index prefetch slot %d\n",
                    __func__, stream_slot);
            valids[maa_id][stream_slot] = false;
            instructions[maa_id][stream_slot] = Instruction();
            completion_only_tiles[maa_id][_instruction.dst1SpdID] =
                old_completion;
            completion_only_tiles[maa_id][prefetch_token] = old_prefetch;
            return false;
        }
        if (inserted_slot)
            *inserted_slot = load_slot;
        return true;
    }

    if (_instruction.opcode ==
        Instruction::OpcodeType::INDIR_LD_SPD_STREAM) {
        Instruction load = _instruction;
        load.opcode = Instruction::OpcodeType::INDIR_LD;
        load.accessType = Instruction::AccessType::READ;
        load.src1RegID = load.src2RegID = load.src3RegID = -1;
        load.backingAddr = 0xFFFFFFFFFFFFFFFF;
        load.backingAddrRangeID = -1;

        Instruction stream = _instruction;
        stream.opcode = Instruction::OpcodeType::STREAM_ST;
        stream.accessType = Instruction::AccessType::WRITE;
        stream.baseAddr = _instruction.backingAddr;
        stream.minAddr = _instruction.backingMinAddr;
        stream.maxAddr = _instruction.backingMaxAddr;
        stream.addrRangeID = _instruction.backingAddrRangeID;
        stream.src1SpdID = _instruction.dst1SpdID;
        stream.src1Status = Instruction::TileStatus::WaitForService;
        stream.src1MustBeFinished =
            load.addrRangeID == stream.addrRangeID;
        stream.src2SpdID = stream.dst1SpdID = stream.dst2SpdID = -1;
        stream.condSpdID = -1;
        stream.backingAddr = 0xFFFFFFFFFFFFFFFF;
        stream.backingAddrRangeID = -1;

        bool old_completion =
            completion_only_tiles[_instruction.maa_id][_instruction.dst1SpdID];
        int load_slot = -1;
        if (!pushInstruction(load, &load_slot))
            return false;
        if (!pushInstruction(stream, nullptr, load_slot)) {
            panic_if(load_slot < 0, "Fused load did not report its IF slot\n");
            DPRINTF(MAAController,
                    "%s: rolled back fused load slot %d because its stream "
                    "micro-op could not be inserted\n",
                    __func__, load_slot);
            valids[_instruction.maa_id][load_slot] = false;
            instructions[_instruction.maa_id][load_slot] = Instruction();
            completion_only_tiles[_instruction.maa_id]
                                 [_instruction.dst1SpdID] = old_completion;
            return false;
        }
        if (inserted_slot)
            *inserted_slot = load_slot;
        return true;
    }

    switch (_instruction.opcode) {
    case Instruction::OpcodeType::STREAM_LD:
    case Instruction::OpcodeType::STREAM_PREFETCH:
    case Instruction::OpcodeType::STREAM_ST: {
        _instruction.funcUniType = FuncUnitType::STREAM;
        break;
    }
    case Instruction::OpcodeType::INDIR_LD:
    case Instruction::OpcodeType::INDIR_LD_VIRTUAL:
    case Instruction::OpcodeType::INDIR_LD_VIRTUAL_INDEX:
    case Instruction::OpcodeType::INDIR_LD_INDEX:
    case Instruction::OpcodeType::INDIR_ST_VECTOR:
    case Instruction::OpcodeType::INDIR_ST_SCALAR:
    case Instruction::OpcodeType::INDIR_RMW_VECTOR:
    case Instruction::OpcodeType::INDIR_RMW_SCALAR: {
        _instruction.funcUniType = FuncUnitType::INDIRECT;
        break;
    }
    case Instruction::OpcodeType::RANGE_LOOP: {
        _instruction.funcUniType = FuncUnitType::RANGE;
        break;
    }
    case Instruction::OpcodeType::ALU_SCALAR:
    case Instruction::OpcodeType::ALU_VECTOR:
    case Instruction::OpcodeType::ALU_REDUCE: {
        _instruction.funcUniType = FuncUnitType::ALU;
        break;
    }
    default: {
        assert(false);
    }
    }
    int free_instruction_slot = -1;
    int maa_id = _instruction.maa_id;
    auto reject_completion_source = [&](int tile_id) {
        panic_if(tile_id != -1 && completion_only_tiles[maa_id][tile_id],
                 "%s: tile %d is a virtual completion token, not SPD data\n",
                 __func__, tile_id);
    };
    const bool completion_dependency =
        _instruction.opcode ==
            Instruction::OpcodeType::INDIR_LD_VIRTUAL_INDEX &&
        _instruction.src1SpdID != -1;
    if (!completion_dependency)
        reject_completion_source(_instruction.src1SpdID);
    reject_completion_source(_instruction.src2SpdID);
    reject_completion_source(_instruction.condSpdID);
    for (int i = 0; i < num_instructions_per_maa; i++) {
        if (valids[maa_id][i] == false) {
            if (free_instruction_slot == -1) {
                free_instruction_slot = i;
            }
        } else {
            if (i == ignored_hazard_slot)
                continue;
            if (_instruction.opcode ==
                    Instruction::OpcodeType::INDIR_LD_VIRTUAL_INDEX &&
                _instruction.src1SpdID != -1 &&
                instructions[maa_id][i].opcode ==
                    Instruction::OpcodeType::STREAM_PREFETCH &&
                instructions[maa_id][i].dst1SpdID ==
                    _instruction.src1SpdID) {
                _instruction.src1Status =
                    Instruction::TileStatus::WaitForService;
            }
            if (_instruction.dst1SpdID != -1) {
                if ((instructions[maa_id][i].dst1SpdID != -1 && _instruction.dst1SpdID == instructions[maa_id][i].dst1SpdID) ||
                    (instructions[maa_id][i].dst2SpdID != -1 && _instruction.dst1SpdID == instructions[maa_id][i].dst2SpdID) ||
                    (instructions[maa_id][i].src1SpdID != -1 && _instruction.dst1SpdID == instructions[maa_id][i].src1SpdID) ||
                    (instructions[maa_id][i].src2SpdID != -1 && _instruction.dst1SpdID == instructions[maa_id][i].src2SpdID) ||
                    (instructions[maa_id][i].condSpdID != -1 && _instruction.dst1SpdID == instructions[maa_id][i].condSpdID)) {
                    DPRINTF(MAAController, "%s: %s cannot be pushed b/c of %s!\n", __func__, _instruction.print(), instructions[maa_id][i].print());
                    return false;
                }
            }
            if (_instruction.dst2SpdID != -1) {
                if ((instructions[maa_id][i].dst1SpdID != -1 && _instruction.dst2SpdID == instructions[maa_id][i].dst1SpdID) ||
                    (instructions[maa_id][i].dst2SpdID != -1 && _instruction.dst2SpdID == instructions[maa_id][i].dst2SpdID) ||
                    (instructions[maa_id][i].src1SpdID != -1 && _instruction.dst2SpdID == instructions[maa_id][i].src1SpdID) ||
                    (instructions[maa_id][i].src2SpdID != -1 && _instruction.dst2SpdID == instructions[maa_id][i].src2SpdID) ||
                    (instructions[maa_id][i].condSpdID != -1 && _instruction.dst2SpdID == instructions[maa_id][i].condSpdID)) {
                    DPRINTF(MAAController, "%s: %s cannot be pushed b/c of %s!\n", __func__, _instruction.print(), instructions[maa_id][i].print());
                    return false;
                }
            }
            if (_instruction.addrRangeID == instructions[maa_id][i].addrRangeID) {
                if ((_instruction.accessType == Instruction::AccessType::WRITE && instructions[maa_id][i].accessType != Instruction::AccessType::COMPUTE) || // WAR hazard
                    (_instruction.accessType == Instruction::AccessType::READ && instructions[maa_id][i].accessType == Instruction::AccessType::WRITE)) {    // RAW hazard
                    DPRINTF(MAAController, "%s: %s cannot be pushed b/c of %s!\n", __func__, _instruction.print(), instructions[maa_id][i].print());
                    return false;
                }
            }
        }
    }
    if (free_instruction_slot == -1) {
        DPRINTF(MAAController, "%s: %s cannot be pushed b/c of no space!\n", __func__, _instruction.print());
        return false;
    }
    assert(free_instruction_slot < num_instructions_per_maa);
    instructions[maa_id][free_instruction_slot] = _instruction;
    valids[maa_id][free_instruction_slot] = true;
    instructions[maa_id][free_instruction_slot].if_id = free_instruction_slot;
    if (inserted_slot)
        *inserted_slot = free_instruction_slot;
    if (_instruction.dst1SpdID != -1) {
        completion_only_tiles[maa_id][_instruction.dst1SpdID] =
            _instruction.opcode == Instruction::OpcodeType::INDIR_LD_VIRTUAL ||
            _instruction.opcode ==
                Instruction::OpcodeType::INDIR_LD_VIRTUAL_INDEX ||
            _instruction.opcode == Instruction::OpcodeType::STREAM_PREFETCH;
    }
    if (_instruction.dst2SpdID != -1)
        completion_only_tiles[maa_id][_instruction.dst2SpdID] = false;
    DPRINTF(MAAController, "%s: %s pushed to instruction[%d]!\n", __func__, _instruction.print(), free_instruction_slot);
    return true;
}
bool IF::canPushRegister(Register _reg) {
    int register_id = _reg.register_id;
    int maa_id = _reg.maa_id;
    for (int i = 0; i < num_instructions_per_maa; i++) {
        if (valids[maa_id][i] == true) {
            if ((instructions[maa_id][i].dst1RegID == register_id) ||
                (instructions[maa_id][i].dst2RegID == register_id) ||
                (instructions[maa_id][i].src1RegID == register_id) ||
                (instructions[maa_id][i].src2RegID == register_id) ||
                (instructions[maa_id][i].src3RegID == register_id)) {
                DPRINTF(MAAController, "%s: register write %d cannot be pushed b/c of %s!\n", __func__, register_id, instructions[maa_id][i].print());
                return false;
            }
        }
    }
    return true;
}
bool IF::hasTileReference(int maa_id, int tile_id) {
    panic_if(maa_id < 0 || maa_id >= static_cast<int>(num_maas),
             "Invalid MAA id %d\n", maa_id);
    panic_if(tile_id < 0 || tile_id >= static_cast<int>(num_tiles),
             "Invalid tile id %d\n", tile_id);
    for (int i = 0; i < num_instructions_per_maa; ++i) {
        if (!valids[maa_id][i])
            continue;
        Instruction &instruction = instructions[maa_id][i];
        const int tiles[] = {
            instruction.src1SpdID, instruction.src2SpdID,
            instruction.dst1SpdID, instruction.dst2SpdID,
            instruction.condSpdID,
        };
        for (const int first : tiles) {
            if (first == -1)
                continue;
            const int words =
                instruction.getWordSize(first) / sizeof(uint32_t);
            if (tile_id >= first && tile_id < first + words)
                return true;
        }
    }
    return false;
}
bool IF::isCompletionOnlyTile(int maa_id, int tile_id) const {
    panic_if(maa_id < 0 || maa_id >= static_cast<int>(num_maas),
             "Invalid MAA id %d\n", maa_id);
    panic_if(tile_id < 0 || tile_id >= static_cast<int>(num_tiles),
             "Invalid tile id %d\n", tile_id);
    return completion_only_tiles[maa_id][tile_id];
}
Instruction *IF::getReady(FuncUnitType funcUniType, int maa_id) {
    int rand_base = rand() % num_instructions_per_maa;
    if (funcUniType == FuncUnitType::INVALIDATOR) {
        panic_if(maa_id != -1, "Invalidator cannot have maa_id!\n");
        int mid_base = rand() % num_maas;
        for (int mid_offset = 0; mid_offset < num_maas; mid_offset++) {
            int mid = (mid_base + mid_offset) % num_maas;
            for (int i = 0; i < num_instructions_per_maa; i++) {
                int instr_idx = (rand_base + i) % num_instructions_per_maa;
                if (valids[mid][instr_idx] && instructions[mid][instr_idx].state == Instruction::Status::Idle) {
                    int tile_id = -1;
                    if (instructions[mid][instr_idx].dst1Status == Instruction::TileStatus::WaitForInvalidation) {
                        tile_id = instructions[mid][instr_idx].dst1SpdID;
                    } else if (instructions[mid][instr_idx].dst2Status == Instruction::TileStatus::WaitForInvalidation) {
                        tile_id = instructions[mid][instr_idx].dst2SpdID;
                    } else if (instructions[mid][instr_idx].src1Status == Instruction::TileStatus::WaitForInvalidation) {
                        tile_id = instructions[mid][instr_idx].src1SpdID;
                    } else if (instructions[mid][instr_idx].src2Status == Instruction::TileStatus::WaitForInvalidation) {
                        tile_id = instructions[mid][instr_idx].src2SpdID;
                    } else if (instructions[mid][instr_idx].condStatus == Instruction::TileStatus::WaitForInvalidation) {
                        tile_id = instructions[mid][instr_idx].condSpdID;
                    }
                    if (tile_id != -1) {
                        issueInstructionInvalidate(&instructions[mid][instr_idx], tile_id);
                        DPRINTF(MAAController, "%s: returned instruction[%d] %s for invalidation!\n", __func__, instr_idx, instructions[mid][instr_idx].print());
                        return &instructions[mid][instr_idx];
                    }
                }
            }
        }
    } else {
        panic_if(maa_id == -1, "Invalidator cannot have maa_id!\n");
        for (int i = 0; i < num_instructions_per_maa; i++) {
            int instr_idx = (rand_base + i) % num_instructions_per_maa;
            if (valids[maa_id][instr_idx] &&
                instructions[maa_id][instr_idx].maa_id == maa_id &&
                instructions[maa_id][instr_idx].state == Instruction::Status::Idle &&
                (instructions[maa_id][instr_idx].src1SpdID == -1 ||
                 (instructions[maa_id][instr_idx].src1MustBeFinished
                      ? instructions[maa_id][instr_idx].src1Status ==
                            Instruction::TileStatus::Finished
                      : instructions[maa_id][instr_idx].src1Status ==
                                Instruction::TileStatus::Service ||
                            instructions[maa_id][instr_idx].src1Status ==
                                Instruction::TileStatus::Finished)) &&
                (instructions[maa_id][instr_idx].src2SpdID == -1 ||
                 instructions[maa_id][instr_idx].src2Status ==
                         Instruction::TileStatus::Service ||
                 instructions[maa_id][instr_idx].src2Status ==
                         Instruction::TileStatus::Finished) &&
                (instructions[maa_id][instr_idx].condSpdID == -1 ||
                 instructions[maa_id][instr_idx].condStatus ==
                         Instruction::TileStatus::Service ||
                 instructions[maa_id][instr_idx].condStatus ==
                         Instruction::TileStatus::Finished) &&
                (instructions[maa_id][instr_idx].dst1SpdID == -1 ||
                 instructions[maa_id][instr_idx].dst1Status ==
                         Instruction::TileStatus::WaitForService) &&
                (instructions[maa_id][instr_idx].dst2SpdID == -1 ||
                 instructions[maa_id][instr_idx].dst2Status ==
                         Instruction::TileStatus::WaitForService) &&
                instructions[maa_id][instr_idx].funcUniType == funcUniType) {
                if (maa->num_maas == 1 || maa->getAddrRegionPermit(&instructions[maa_id][instr_idx])) {
                    issueInstructionCompute(&instructions[maa_id][instr_idx]);
                    DPRINTF(MAAController, "%s: returned instruction[%d] %s for execute!\n", __func__, instr_idx, instructions[maa_id][instr_idx].print());
                    return &instructions[maa_id][instr_idx];
                }
            }
        }
    }
    return nullptr;
}
void IF::finishInstructionCompute(Instruction *instruction) {
    instruction->state = Instruction::Status::Finish;
    int maa_id = instruction->maa_id;
    valids[maa_id][instruction->if_id] = false;
    if (instruction->dst1SpdID != -1) {
        for (int i = 0; i < num_instructions_per_maa; i++) {
            if (valids[maa_id][i]) {
                if (instructions[maa_id][i].src1SpdID == instruction->dst1SpdID) {
                    instructions[maa_id][i].src1Status = Instruction::TileStatus::Finished;
                }
                if (instructions[maa_id][i].src2SpdID == instruction->dst1SpdID) {
                    instructions[maa_id][i].src2Status = Instruction::TileStatus::Finished;
                }
                if (instructions[maa_id][i].condSpdID == instruction->dst1SpdID) {
                    instructions[maa_id][i].condStatus = Instruction::TileStatus::Finished;
                }
            }
        }
    }
    if (instruction->dst2SpdID != -1) {
        for (int i = 0; i < num_instructions_per_maa; i++) {
            if (valids[maa_id][i]) {
                if (instructions[maa_id][i].src1SpdID == instruction->dst2SpdID) {
                    instructions[maa_id][i].src1Status = Instruction::TileStatus::Finished;
                }
                if (instructions[maa_id][i].src2SpdID == instruction->dst2SpdID) {
                    instructions[maa_id][i].src2Status = Instruction::TileStatus::Finished;
                }
                if (instructions[maa_id][i].condSpdID == instruction->dst2SpdID) {
                    instructions[maa_id][i].condStatus = Instruction::TileStatus::Finished;
                }
            }
        }
    }
}
Instruction::TileStatus IF::getTileStatus(int tile_id, uint8_t tile_status) {
    if (tile_status == (uint8_t)SPD::TileStatus::Idle) {
        return Instruction::TileStatus::WaitForService;
    } else if (tile_status == (uint8_t)SPD::TileStatus::Service) {
        return Instruction::TileStatus::Service;
    } else if (tile_status == (uint8_t)SPD::TileStatus::Finished) {
        return Instruction::TileStatus::Finished;
    } else {
        assert(false);
    }
    assert(false);
    return Instruction::TileStatus::WaitForService;
}
void IF::finishInstructionInvalidate(Instruction *instruction, int tile_id, uint8_t tile_status) {
    instruction->state = Instruction::Status::Idle;
    Instruction::TileStatus new_tile_status = getTileStatus(tile_id, tile_status);
    int maa_id = instruction->maa_id;
    for (int i = 0; i < num_instructions_per_maa; i++) {
        if (valids[maa_id][i]) {
            if (instructions[maa_id][i].src1SpdID == tile_id && instructions[maa_id][i].src1Status == Instruction::TileStatus::Invalidating) {
                instructions[maa_id][i].src1Status = new_tile_status;
            }
            if (instructions[maa_id][i].src2SpdID == tile_id && instructions[maa_id][i].src2Status == Instruction::TileStatus::Invalidating) {
                instructions[maa_id][i].src2Status = new_tile_status;
            }
            if (instructions[maa_id][i].condSpdID == tile_id && instructions[maa_id][i].condStatus == Instruction::TileStatus::Invalidating) {
                instructions[maa_id][i].condStatus = new_tile_status;
            }
            if (instructions[maa_id][i].dst1SpdID == tile_id && instructions[maa_id][i].dst1Status == Instruction::TileStatus::Invalidating) {
                instructions[maa_id][i].dst1Status = new_tile_status;
            }
            if (instructions[maa_id][i].dst2SpdID == tile_id && instructions[maa_id][i].dst2Status == Instruction::TileStatus::Invalidating) {
                instructions[maa_id][i].dst2Status = new_tile_status;
            }
        }
    }
}
void IF::issueInstructionCompute(Instruction *instruction) {
    instruction->state = Instruction::Status::Service;
    int maa_id = instruction->maa_id;
    if (instruction->dst1SpdID != -1) {
        for (int i = 0; i < num_instructions_per_maa; i++) {
            if (valids[maa_id][i]) {
                if (instructions[maa_id][i].src1SpdID == instruction->dst1SpdID) {
                    instructions[maa_id][i].src1Status = Instruction::TileStatus::Service;
                }
                if (instructions[maa_id][i].src2SpdID == instruction->dst1SpdID) {
                    instructions[maa_id][i].src2Status = Instruction::TileStatus::Service;
                }
                if (instructions[maa_id][i].condSpdID == instruction->dst1SpdID) {
                    instructions[maa_id][i].condStatus = Instruction::TileStatus::Service;
                }
                if (instructions[maa_id][i].dst1SpdID == instruction->dst1SpdID) {
                    instructions[maa_id][i].dst1Status = Instruction::TileStatus::Service;
                }
                if (instructions[maa_id][i].dst2SpdID == instruction->dst1SpdID) {
                    instructions[maa_id][i].dst2Status = Instruction::TileStatus::Service;
                }
            }
        }
    }
    if (instruction->dst2SpdID != -1) {
        for (int i = 0; i < num_instructions_per_maa; i++) {
            if (valids[maa_id][i]) {
                if (instructions[maa_id][i].src1SpdID == instruction->dst2SpdID) {
                    instructions[maa_id][i].src1Status = Instruction::TileStatus::Service;
                }
                if (instructions[maa_id][i].src2SpdID == instruction->dst2SpdID) {
                    instructions[maa_id][i].src2Status = Instruction::TileStatus::Service;
                }
                if (instructions[maa_id][i].condSpdID == instruction->dst2SpdID) {
                    instructions[maa_id][i].condStatus = Instruction::TileStatus::Service;
                }
                if (instructions[maa_id][i].dst1SpdID == instruction->dst2SpdID) {
                    instructions[maa_id][i].dst1Status = Instruction::TileStatus::Service;
                }
                if (instructions[maa_id][i].dst2SpdID == instruction->dst2SpdID) {
                    instructions[maa_id][i].dst2Status = Instruction::TileStatus::Service;
                }
            }
        }
    }
}
void IF::issueInstructionInvalidate(Instruction *instruction, int tile_id) {
    instruction->state = Instruction::Status::Service;
    int maa_id = instruction->maa_id;
    for (int i = 0; i < num_instructions_per_maa; i++) {
        if (valids[maa_id][i]) {
            if (instructions[maa_id][i].src1SpdID == tile_id && instructions[maa_id][i].src1Status == Instruction::TileStatus::WaitForInvalidation) {
                instructions[maa_id][i].src1Status = Instruction::TileStatus::Invalidating;
            }
            if (instructions[maa_id][i].src2SpdID == tile_id && instructions[maa_id][i].src2Status == Instruction::TileStatus::WaitForInvalidation) {
                instructions[maa_id][i].src2Status = Instruction::TileStatus::Invalidating;
            }
            if (instructions[maa_id][i].condSpdID == tile_id && instructions[maa_id][i].condStatus == Instruction::TileStatus::WaitForInvalidation) {
                instructions[maa_id][i].condStatus = Instruction::TileStatus::Invalidating;
            }
            if (instructions[maa_id][i].dst1SpdID == tile_id && instructions[maa_id][i].dst1Status == Instruction::TileStatus::WaitForInvalidation) {
                instructions[maa_id][i].dst1Status = Instruction::TileStatus::Invalidating;
            }
            if (instructions[maa_id][i].dst2SpdID == tile_id && instructions[maa_id][i].dst2Status == Instruction::TileStatus::WaitForInvalidation) {
                instructions[maa_id][i].dst2Status = Instruction::TileStatus::Invalidating;
            }
        }
    }
}
AddressRangeType::AddressRangeType(Addr _addr, AddrRangeList addrRanges) : addr(_addr) {
    valid = false;
    rangeID = 0;
    for (const auto &r : addrRanges) {
        if (r.contains(addr)) {
            base = r.start();
            offset = addr - base;
            valid = true;
            break;
        }
        rangeID++;
    }
}
std::string AddressRangeType::print() const {
    std::ostringstream str;
    ccprintf(str, "%s: 0x%lx + 0x%lx", address_range_names[rangeID], base, offset);
    return str.str();
}
const char *const AddressRangeType::address_range_names[8] = {
    "SPD_DATA_CACHEABLE_RANGE",
    "SPD_DATA_NONCACHEABLE_RANGE",
    "SPD_SIZE_RANGE",
    "SPD_READY_RANGE",
    "SCALAR_RANGE",
    "INSTRUCTION_RANGE",
    "VIRTUAL_PAGE_READY_RANGE",
    "MAX"};
} // namespace gem5
