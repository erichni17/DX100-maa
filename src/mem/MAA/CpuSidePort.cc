#include <cassert>
#include <cstdint>
#include <limits>

#include "../../../include/gem5/maa_logical_spd_cache_abi.hh"
#include "base/addr_range.hh"
#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/MAAController.hh"
#include "debug/MAACpuPort.hh"
#include "debug/MAAVirtualTrace.hh"
#include "mem/MAA/ALU.hh"
#include "mem/MAA/IF.hh"
#include "mem/MAA/IndirectAccess.hh"
#include "mem/MAA/Invalidator.hh"
#include "mem/MAA/MAA.hh"
#include "mem/MAA/RangeFuser.hh"
#include "mem/MAA/SPD.hh"
#include "mem/MAA/SoaJitSafety.hh"
#include "mem/MAA/SoaJitScalarBroadcast.hh"
#include "mem/MAA/StreamAccess.hh"
#include "mem/packet.hh"
#include "params/MAA.hh"

#ifndef TRACING_ON
#define TRACING_ON 1
#endif

namespace gem5 {

void MAA::recvTimingSnoopResp(PacketPtr pkt) {
    /// print the packet
    DPRINTF(MAACpuPort, "%s: received %s\n", __func__, pkt->print());
    switch (pkt->cmd.toInt()) {
    case MemCmd::ReadExResp: {
        assert(pkt->getSize() == 64);
        for (int i = 0; i < 64; i += 4) {
            panic_if(pkt->req->getByteEnable()[i] == false, "Byte enable [%d] is not set for the read response\n", i);
        }
        AddressRangeType address_range = AddressRangeType(pkt->getAddr(), addrRanges);
        panic_if(address_range.isValid() == false, "Invalid address range: %s\n", address_range.print());
        assert(address_range.getType() == AddressRangeType::Type::SPD_DATA_CACHEABLE_RANGE);
        Addr offset = address_range.getOffset();
        int tile_id = offset / (num_tile_elements * sizeof(uint32_t));
        int element_id = offset % (num_tile_elements * sizeof(uint32_t));
        assert(element_id % sizeof(uint32_t) == 0);
        element_id /= sizeof(uint32_t);
        invalidator->recvData(tile_id, element_id, pkt->getPtr<uint8_t>());
        break;
    }
    default:
        assert(false);
    }
}
bool MAA::CpuSidePort::recvTimingSnoopResp(PacketPtr pkt) {
    assert(pkt->isResponse());
    /// print the packet
    DPRINTF(MAACpuPort, "%s: received %s\n", __func__, pkt->print());
    maa.recvTimingSnoopResp(pkt);
    outstandingCpuSidePackets--;
    if (is_blocked) {
        is_blocked = false;
        maa.invalidator->scheduleExecuteInstructionEvent();
    }
    pkt->deleteData();
    delete pkt;
    return true;
}

bool MAA::CpuSidePort::tryTiming(PacketPtr pkt) {
    /// print the packet
    DPRINTF(MAACpuPort, "%s: received %s\n", __func__, pkt->print());

    if (mustRetryTileRequest) {
        return false;
    }

    if (pkt->cmd == MemCmd::ReadExReq ||
        pkt->cmd == MemCmd::ReadSharedReq) {
        AddressRangeType address_range =
            AddressRangeType(pkt->getAddr(), maa.addrRanges);
        if (address_range.isValid() &&
            address_range.getType() ==
                AddressRangeType::Type::SPD_DATA_CACHEABLE_RANGE) {
            const Addr offset = address_range.getOffset();
            const int tile_id =
                offset / (maa.num_tile_elements * sizeof(uint32_t));
            panic_if(maa.logicalTileReservedLane(tile_id) ||
                         maa.logicalCompletionLaneOwned(tile_id),
                     "Guest cacheable read references reserved/owned SPD "
                     "lane %d\n", tile_id);
            if (!maa.spd->getTileReady(tile_id)) {
                assert(retryTileID == -1);
                assert(!tileRequestRetryOutstanding);
                mustRetryTileRequest = true;
                retryTileID = tile_id;
                maa.stats.cpu_spd_data_read_deferrals++;
                DPRINTF(MAACpuPort,
                        "%s: deferring cacheable read for tile[%d]\n",
                        __func__, tile_id);
                return false;
            }
        }
    }
    return true;
}

void MAA::recvTimingReq(PacketPtr pkt, int core_id) {
    /// print the packet
    DPRINTF(MAACpuPort, "%s: received %s, cmd: %s, isMaskedWrite: %d, size: %d\n",
            __func__,
            pkt->print(),
            pkt->cmdString(),
            pkt->isMaskedWrite(),
            pkt->getSize());
    AddressRangeType address_range = AddressRangeType(pkt->getAddr(), addrRanges);
    DPRINTF(MAACpuPort, "%s: address range type: %s\n", __func__, address_range.print());
    for (int i = 0; i < pkt->getSize(); i++) {
        panic_if(pkt->req->getByteEnable()[i] == false, "Byte enable [%d] is not set for the request\n", i);
    }
    switch (pkt->cmd.toInt()) {
    case MemCmd::WritebackDirty: {
        assert(pkt->isMaskedWrite() == false);
        switch (address_range.getType()) {
        case AddressRangeType::Type::SPD_DATA_CACHEABLE_RANGE: {
            Addr offset = address_range.getOffset();
            int tile_id = offset / (num_tile_elements * sizeof(uint32_t));
            panic_if(logicalTileReservedLane(tile_id) ||
                         logicalCompletionLaneOwned(tile_id),
                     "Guest cacheable write references reserved/owned SPD "
                     "lane %d\n", tile_id);
            panic_if(pkt->getSize() != 64,
                     "Invalid size for SPD data: %d\n", pkt->getSize());
            int element_id =
                (offset % (num_tile_elements * sizeof(uint32_t))) /
                sizeof(uint32_t);
            for (int i = 0; i < 64 / sizeof(uint32_t); i++) {
                uint32_t data = pkt->getPtr<uint32_t>()[i];
                DPRINTF(MAACpuPort,
                        "%s: TILE[%d][%d] = %u\n", __func__, tile_id,
                        element_id + i, data);
                spd->setData<uint32_t>(tile_id, element_id + i, data);
            }
            assert(pkt->needsResponse() == false);
            pendingDelete.reset(pkt);
            break;
        }
        default:
            panic_if(true, "%s: Error: Range(%s) and cmd(%s) is illegal. Packet: %s\n", __func__, address_range.print(), pkt->cmdString(), pkt->print());
            assert(false);
        }
        break;
    }
    case MemCmd::WriteReq: {
        bool respond_immediately = true;
        assert(pkt->isMaskedWrite() == false);
        switch (address_range.getType()) {
        case AddressRangeType::Type::SCALAR_RANGE: {
            panic_if(core_id != 0, "Scalar range is only for the core 0\n");
            Addr offset = address_range.getOffset();
            int element_id = offset % (num_regs * sizeof(uint32_t));
            assert(element_id % sizeof(uint32_t) == 0);
            element_id /= sizeof(uint32_t);
            panic_if(pkt->getSize() != 4 && pkt->getSize() != 8, "Invalid size for RF data: %d\n", pkt->getSize());
            RegisterPtr current_register = new Register();
            current_register->size = pkt->getSize();
            current_register->register_id = element_id;
            if (my_RID_to_core_id.find(pkt->requestorId()) == my_RID_to_core_id.end()) {
                int num_received_cores = my_RID_to_core_id.size();
                panic_if(num_received_cores == num_cores, "received more than %d instructions\n", num_cores);
                my_RID_to_core_id[pkt->requestorId()] = num_received_cores;
                num_received_cores++;
            }
            current_register->core_id = my_RID_to_core_id[pkt->requestorId()];
            current_register->maa_id = current_register->core_id % num_maas;
            if (pkt->getSize() == 4) {
                uint32_t data_UINT32 = pkt->getPtr<uint32_t>()[0];
                int32_t data_INT32 = pkt->getPtr<int32_t>()[0];
                float data_FLOAT = pkt->getPtr<float>()[0];
                DPRINTF(MAACpuPort, "%s: REG[%d] = %u/%d/%f\n", __func__, element_id, data_UINT32, data_INT32, data_FLOAT);
                current_register->data_UINT32 = data_UINT32;
                // rf->setData<uint32_t>(element_id, data_UINT32);
            } else {
                uint64_t data_UINT64 = pkt->getPtr<uint64_t>()[0];
                int64_t data_INT64 = pkt->getPtr<int64_t>()[0];
                double data_DOUBLE = pkt->getPtr<double>()[0];
                DPRINTF(MAACpuPort, "%s: REG[%d] = %lu/%ld/%lf\n", __func__, element_id, data_UINT64, data_INT64, data_DOUBLE);
                current_register->data_UINT64 = data_UINT64;
                // rf->setData<uint64_t>(element_id, data_UINT64);
            }
            my_registers.push_back(current_register);
            my_register_pkts.push_back(pkt);
            assert(pkt->needsResponse());
            respond_immediately = false;
            scheduleDispatchRegisterEvent();
            break;
        }
        case AddressRangeType::Type::INSTRUCTION_RANGE: {
            panic_if(core_id != 0, "Instruction range is only for the core 0\n");
            Addr offset = address_range.getOffset();
            int element_id = offset % (num_instructions_total * sizeof(uint64_t));
            assert(element_id % sizeof(uint64_t) == 0);
            element_id /= sizeof(uint64_t);
            uint64_t data = pkt->getPtr<uint64_t>()[0];
            DPRINTF(MAACpuPort, "%s: IF[%d] = %ld\n", __func__, element_id, data);
            InstructionPtr current_instruction;
            int instruction_id = -1;
            for (int i = 0; i < my_instruction_RIDs.size(); i++) {
                if (my_instruction_RIDs[i] == pkt->requestorId()) {
                    panic_if(instruction_id != -1, "Received multiple instructions from the same requestor\n");
                    panic_if(my_instruction_recvs[i], "Received new instruction after unissued instruction!\n");
                    current_instruction = my_instructions[i];
                    instruction_id = i;
                    my_instruction_pkts[i] = pkt;
                }
            }
            if (instruction_id == -1) {
                current_instruction = new Instruction();
                my_instruction_pkts.push_back(pkt);
                my_instruction_RIDs.push_back(pkt->requestorId());
                my_instruction_recvs.push_back(false);
                if (my_RID_to_core_id.find(pkt->requestorId()) == my_RID_to_core_id.end()) {
                    int num_received_cores = my_RID_to_core_id.size();
                    panic_if(num_received_cores == num_cores, "received more than %d instructions\n", num_cores);
                    my_RID_to_core_id[pkt->requestorId()] = num_received_cores;
                    num_received_cores++;
                }
                current_instruction->core_id = my_RID_to_core_id[pkt->requestorId()];
                current_instruction->maa_id = current_instruction->core_id % num_maas;
                my_instructions.push_back(current_instruction);
            }
#define NA_UINT8 0xFF
            switch (element_id) {
            case 0: {
                panic_if(instruction_id != -1, "Received new instruction[0] after incomplete instruction!\n");
                const auto logical_header =
                    maa::LogicalSPDCacheABI::decodeWord0(data);
                panic_if(
                    logical_header.kind ==
                        maa::LogicalSPDCacheABI::HeaderKind::Unsupported,
                    "Unsupported logical high-byte encoding in instruction "
                    "word 0: 0x%016lx\n", data);
                current_instruction->dst2SpdID =
                    (data & NA_UINT8) == NA_UINT8 ? -1 : (data & NA_UINT8);
                data = data >> 8;
                const uint8_t raw_dst1 = data & NA_UINT8;
                current_instruction->soaJitOldResult =
                    raw_dst1 == SoaJitSafety::OldResultModeTag;
                current_instruction->dst1SpdID =
                    (raw_dst1 == NA_UINT8 ||
                     current_instruction->soaJitOldResult) ? -1 : raw_dst1;
                data = data >> 8;
                current_instruction->optype = (data & NA_UINT8) == NA_UINT8 ? Instruction::OPType::MAX : static_cast<Instruction::OPType>(data & NA_UINT8);
                data = data >> 8;
                current_instruction->datatype = (data & NA_UINT8) == NA_UINT8 ? Instruction::DataType::MAX : static_cast<Instruction::DataType>(data & NA_UINT8);
                assert(current_instruction->datatype != Instruction::DataType::MAX);
                data = data >> 8;
                current_instruction->opcode = (data & NA_UINT8) == NA_UINT8 ? Instruction::OpcodeType::MAX : static_cast<Instruction::OpcodeType>(data & NA_UINT8);
                assert(current_instruction->opcode != Instruction::OpcodeType::MAX);
                panic_if(current_instruction->soaJitOldResult &&
                             current_instruction->opcode !=
                                 Instruction::OpcodeType::INDIR_RMW_VECTOR,
                         "Old-result mode tag is only valid for guarded "
                         "INDIR_RMW_VECTOR\n");
                if (logical_header.kind ==
                    maa::LogicalSPDCacheABI::HeaderKind::LogicalALUScalar) {
                    panic_if(
                        current_instruction->opcode !=
                            Instruction::OpcodeType::ALU_SCALAR,
                        "Logical high-byte operands are only supported for "
                        "ALU_SCALAR, got opcode %d\n",
                        static_cast<int>(current_instruction->opcode));
                    current_instruction->src1LogicalID =
                        logical_header.src1LogicalID;
                    current_instruction->src2LogicalID =
                        logical_header.src2LogicalID;
                    current_instruction->dst1LogicalID =
                        logical_header.dst1LogicalID;
                } else if (
                    logical_header.kind == maa::LogicalSPDCacheABI::
                                               HeaderKind::LogicalALUVector) {
                    panic_if(
                        current_instruction->opcode !=
                            Instruction::OpcodeType::ALU_VECTOR,
                        "Logical high-byte operands are only supported for "
                        "ALU_SCALAR or ALU_VECTOR, got opcode %d\n",
                        static_cast<int>(current_instruction->opcode));
                    current_instruction->src1LogicalID =
                        logical_header.src1LogicalID;
                    current_instruction->src2LogicalID =
                        logical_header.src2LogicalID;
                    current_instruction->dst1LogicalID =
                        logical_header.dst1LogicalID;
                } else if (
                    logical_header.kind ==
                        maa::LogicalSPDCacheABI::HeaderKind::
                            LogicalStreamLoad ||
                    logical_header.kind ==
                        maa::LogicalSPDCacheABI::HeaderKind::
                            LogicalStreamStore) {
                    const bool is_load = logical_header.kind ==
                        maa::LogicalSPDCacheABI::HeaderKind::LogicalStreamLoad;
                    panic_if(
                        current_instruction->opcode !=
                            (is_load ? Instruction::OpcodeType::STREAM_LD :
                                       Instruction::OpcodeType::STREAM_ST),
                        "Logical stream high-byte operands do not match "
                        "STREAM_LD/STREAM_ST opcode %d\n",
                        static_cast<int>(current_instruction->opcode));
                    current_instruction->src1LogicalID =
                        logical_header.src1LogicalID;
                    current_instruction->src2LogicalID =
                        logical_header.src2LogicalID;
                    current_instruction->dst1LogicalID =
                        logical_header.dst1LogicalID;
                    current_instruction->logicalCompletionSpdID =
                        current_instruction->dst1SpdID;
                    current_instruction->dst1SpdID = -1;
                }
                if (current_instruction->opcode ==
                        Instruction::OpcodeType::STREAM_LD ||
                    current_instruction->opcode ==
                        Instruction::OpcodeType::STREAM_PREFETCH ||
                    current_instruction->opcode ==
                        Instruction::OpcodeType::INDIR_LD_VIRTUAL ||
                    current_instruction->opcode ==
                        Instruction::OpcodeType::INDIR_LD_VIRTUAL_INDEX ||
                    current_instruction->opcode ==
                        Instruction::OpcodeType::INDIR_LD_INDEX ||
                    current_instruction->opcode ==
                        Instruction::OpcodeType::INDIR_LD_SPD_STREAM ||
                    current_instruction->opcode ==
                        Instruction::OpcodeType::VIRTUAL_TILE_ALU_SCALAR ||
                    current_instruction->opcode ==
                        Instruction::OpcodeType::INDIR_LD) {
                    current_instruction->accessType =
                        Instruction::AccessType::READ;
                } else if (
                    current_instruction->opcode ==
                            Instruction::OpcodeType::STREAM_ST ||
                        current_instruction->opcode ==
                            Instruction::OpcodeType::INDIR_ST_SCALAR ||
                        current_instruction->opcode ==
                            Instruction::OpcodeType::INDIR_ST_VECTOR ||
                        current_instruction->opcode ==
                            Instruction::OpcodeType::INDIR_RMW_SCALAR ||
                        current_instruction->opcode ==
                            Instruction::OpcodeType::INDIR_RMW_VECTOR) {
                    current_instruction->accessType =
                        Instruction::AccessType::WRITE;
                } else {
                    current_instruction->accessType = Instruction::AccessType::COMPUTE;
                }
                break;
            }
            case 1: {
                panic_if(instruction_id == -1, "Received new instruction[1] before insturction[0]!\n");
                current_instruction->condSpdID = (data & NA_UINT8) == NA_UINT8 ? -1 : (data & NA_UINT8);
                data = data >> 8;
                current_instruction->src3RegID = (data & NA_UINT8) == NA_UINT8 ? -1 : (data & NA_UINT8);
                data = data >> 8;
                current_instruction->src2RegID = (data & NA_UINT8) == NA_UINT8 ? -1 : (data & NA_UINT8);
                data = data >> 8;
                current_instruction->src1RegID = (data & NA_UINT8) == NA_UINT8 ? -1 : (data & NA_UINT8);
                data = data >> 8;
                current_instruction->dst2RegID = (data & NA_UINT8) == NA_UINT8 ? -1 : (data & NA_UINT8);
                data = data >> 8;
                current_instruction->dst1RegID = (data & NA_UINT8) == NA_UINT8 ? -1 : (data & NA_UINT8);
                data = data >> 8;
                current_instruction->src2SpdID = (data & NA_UINT8) == NA_UINT8 ? -1 : (data & NA_UINT8);
                data = data >> 8;
                current_instruction->src1SpdID = (data & NA_UINT8) == NA_UINT8 ? -1 : (data & NA_UINT8);
                data = data >> 8;
                break;
            }
            case 2: {
                panic_if(instruction_id == -1, "Received new instruction[2] before insturction[0]!\n");
                current_instruction->state = Instruction::Status::Idle;
                current_instruction->CID = pkt->req->contextId();
                current_instruction->PC = pkt->req->getPC();
                if (current_instruction->isLogicalALUScalar() ||
                    current_instruction->isLogicalALUVector()) {
                    panic_if(
                        data != maa::LogicalSPDCacheABI::NoAddress,
                        "Logical ALU operand word 2 must use the no-address "
                        "sentinel, got 0x%016lx\n", data);
                    break;
                }
                if (current_instruction->isLogicalStream()) {
                    maa::LogicalSPDCacheABI::StreamOperandShape shape;
                    shape.datatype = static_cast<uint8_t>(
                        current_instruction->datatype);
                    shape.src1LogicalID = current_instruction->src1LogicalID;
                    shape.src2LogicalID = current_instruction->src2LogicalID;
                    shape.dst1LogicalID = current_instruction->dst1LogicalID;
                    shape.src1SpdID = current_instruction->src1SpdID;
                    shape.src2SpdID = current_instruction->src2SpdID;
                    shape.dst1SpdID = current_instruction->dst1SpdID;
                    shape.dst2SpdID = current_instruction->dst2SpdID;
                    shape.completionSpdID =
                        current_instruction->logicalCompletionSpdID;
                    shape.src1RegID = current_instruction->src1RegID;
                    shape.src2RegID = current_instruction->src2RegID;
                    shape.src3RegID = current_instruction->src3RegID;
                    shape.dst1RegID = current_instruction->dst1RegID;
                    shape.dst2RegID = current_instruction->dst2RegID;
                    shape.condSpdID = current_instruction->condSpdID;
                    shape.backingAddr = data;
                    const auto validation = current_instruction->
                        isLogicalStreamLoad() ?
                        maa::LogicalSPDCacheABI::validateLogicalStreamLoad(
                            shape, static_cast<uint8_t>(
                                current_instruction->opcode)) :
                        maa::LogicalSPDCacheABI::validateLogicalStreamStore(
                            shape, static_cast<uint8_t>(
                                current_instruction->opcode));
                    panic_if(validation != maa::LogicalSPDCacheABI::
                                               StreamValidation::Valid,
                             "Rejected logical STREAM ABI shape (%d) before "
                             "controller state mutation\n",
                             static_cast<int>(validation));
                    panic_if(shape.completionSpdID < 0 ||
                                 shape.completionSpdID >=
                                     static_cast<int>(num_tiles),
                             "Rejected logical STREAM completion identity "
                             "before controller state mutation\n");
                    const int range = getAddrRegion(data);
                    panic_if(range < 0,
                             "Logical STREAM backing address 0x%lx is not "
                             "in a registered memory region\n", data);
                    const auto span_validation =
                        maa::LogicalSPDCacheABI::validateBackingSpan(
                            data, shape.datatype, addrRegions[range].first,
                            addrRegions[range].second);
                    panic_if(span_validation !=
                                 maa::LogicalSPDCacheABI::
                                     DestinationValidation::Valid,
                             "Rejected logical STREAM backing span before "
                             "controller state mutation\n");
                    if (current_instruction->isLogicalStreamLoad()) {
                        current_instruction->logicalSourceBackingAddr = data;
                        current_instruction->logicalSourceAddrRangeID = range;
                        current_instruction->logicalSourceMinAddr =
                            addrRegions[range].first;
                        current_instruction->logicalSourceMaxAddr =
                            addrRegions[range].second;
                    } else {
                        current_instruction->logicalDestinationBackingAddr =
                            data;
                        current_instruction->logicalDestinationAddrRangeID =
                            range;
                        current_instruction->logicalDestinationMinAddr =
                            addrRegions[range].first;
                        current_instruction->logicalDestinationMaxAddr =
                            addrRegions[range].second;
                    }
                    panic_if(!logicalTilePageSchedulerEnabled(),
                             "Logical STREAM ABI is disabled unless the "
                             "logical tile page scheduler is enabled\n");
                    current_instruction->baseAddr = data;
                    current_instruction->addrRangeID = range;
                    current_instruction->minAddr = addrRegions[range].first;
                    current_instruction->maxAddr = addrRegions[range].second;
                    my_instruction_recvs[instruction_id] = true;
                    DPRINTF(MAAController,
                            "%s: %s received for logical page scheduling\n",
                            __func__, current_instruction->print());
                    respond_immediately = false;
                    scheduleDispatchInstructionEvent();
                    break;
                }
                current_instruction->baseAddr = data;
                if (current_instruction->isSoaJitRmw()) {
                    panic_if(
                        !current_instruction->hasValidSoaJitRmwShape(),
                        "Rejected SoA/JIT RMW ABI shape: dst1 must be the "
                        "no-result sentinel or old-result mode tag, "
                        "dst2(completion) and all three "
                        "range registers must be present, and register "
                        "destinations must be absent\n");
                    panic_if(
                        current_instruction->src1RegID >= num_regs ||
                            current_instruction->src2RegID >= num_regs ||
                            current_instruction->src3RegID >= num_regs ||
                            current_instruction->dst2SpdID < 0 ||
                            current_instruction->dst2SpdID >=
                                static_cast<int>(num_tiles),
                        "Rejected SoA/JIT RMW register or completion-token "
                        "range before address-word dispatch\n");
                    panic_if(
                        current_instruction->optype !=
                                Instruction::OPType::ADD_OP &&
                            current_instruction->optype !=
                                Instruction::OPType::MIN_OP &&
                            current_instruction->optype !=
                                Instruction::OPType::MAX_OP,
                        "SoA/JIT RMW supports only ADD/MIN/MAX\n");
                }
                if (current_instruction->accessType !=
                    Instruction::AccessType::COMPUTE) {
                    current_instruction->addrRangeID =
                        getAddrRegion(current_instruction->baseAddr);
                    panic_if(current_instruction->addrRangeID < 0,
                             "Base address 0x%lx is not in a registered "
                             "memory region\n",
                             current_instruction->baseAddr);
                    current_instruction->minAddr =
                        addrRegions[current_instruction->addrRangeID].first;
                    current_instruction->maxAddr =
                        addrRegions[current_instruction->addrRangeID].second;
                }
                if (current_instruction->opcode ==
                        Instruction::OpcodeType::INDIR_LD_VIRTUAL ||
                    current_instruction->opcode ==
                        Instruction::OpcodeType::INDIR_LD_VIRTUAL_INDEX ||
                    current_instruction->opcode ==
                        Instruction::OpcodeType::INDIR_LD_INDEX ||
                    current_instruction->opcode ==
                        Instruction::OpcodeType::INDIR_LD_SPD_STREAM ||
                    current_instruction->opcode ==
                        Instruction::OpcodeType::VIRTUAL_TILE_ALU_SCALAR)
                    break;
                if (current_instruction->isSoaJitRmw())
                    break;
                my_instruction_recvs[instruction_id] = true;
                DPRINTF(MAAController, "%s: %s received!\n", __func__, current_instruction->print());
                respond_immediately = false;
                scheduleDispatchInstructionEvent();
                break;
            }
            case 3: {
                panic_if(instruction_id == -1,
                         "Received backing address before instruction header!\n");
                panic_if(
                    current_instruction->opcode !=
                            Instruction::OpcodeType::INDIR_LD_VIRTUAL &&
                        current_instruction->opcode !=
                            Instruction::OpcodeType::INDIR_LD_VIRTUAL_INDEX &&
                        current_instruction->opcode !=
                            Instruction::OpcodeType::INDIR_LD_SPD_STREAM &&
                        current_instruction->opcode !=
                            Instruction::OpcodeType::VIRTUAL_TILE_ALU_SCALAR &&
                        !current_instruction->isSoaJitRmw() &&
                        !current_instruction->isLogicalALUScalar() &&
                        !current_instruction->isLogicalALUVector(),
                    "Backing address is only valid for virtual or fused "
                    "indirect loads or logical ALU_SCALAR!\n");
                if (current_instruction->isLogicalALUScalar()) {
                    current_instruction->backingAddr = data;
                    current_instruction->backingAddrRangeID =
                        getAddrRegion(data);
                    panic_if(current_instruction->backingAddrRangeID < 0,
                             "Logical destination backing address 0x%lx is "
                             "not in a registered memory region\n", data);
                    current_instruction->backingMinAddr = addrRegions[
                        current_instruction->backingAddrRangeID].first;
                    current_instruction->backingMaxAddr = addrRegions[
                        current_instruction->backingAddrRangeID].second;
                    break;
                }
                if (current_instruction->isLogicalALUVector()) {
                    // Staged only: this incomplete instruction is never
                    // admitted or dispatched until word five validates it.
                    current_instruction->logicalDestinationBackingAddr = data;
                    break;
                }
                if (current_instruction->isSoaJitScalarRmw()) {
                    panic_if(
                        data > static_cast<uint64_t>(
                                   std::numeric_limits<int16_t>::max()),
                        "Rejected SoA/JIT scalar register encoding 0x%lx\n",
                        data);
                    current_instruction->soaJitScalarRegID =
                        static_cast<int16_t>(data);
                    const auto validation =
                        SoaJitScalarBroadcast::validateRegisters(
                            current_instruction->soaJitScalarRegID,
                            current_instruction->WordSize() /
                                sizeof(uint32_t),
                            current_instruction->src1RegID,
                            current_instruction->src2RegID,
                            current_instruction->src3RegID, num_regs);
                    panic_if(
                        validation !=
                            SoaJitScalarBroadcast::Status::Accepted ||
                            !SoaJitScalarBroadcast::datatypeMatchesWidth(
                                static_cast<uint8_t>(
                                    current_instruction->datatype),
                                current_instruction->WordSize()),
                        "Rejected SoA/JIT scalar width/type/register alias "
                        "shape (%d) before timed request dispatch\n",
                        static_cast<int>(validation));
                    break;
                }
                current_instruction->backingAddr = data;
                current_instruction->backingAddrRangeID = getAddrRegion(data);
                panic_if(current_instruction->backingAddrRangeID < 0,
                         "Backing address 0x%lx is not in a registered "
                         "memory region\n",
                         data);
                current_instruction->backingMinAddr =
                    addrRegions[current_instruction->backingAddrRangeID].first;
                current_instruction->backingMaxAddr = addrRegions[
                    current_instruction->backingAddrRangeID].second;
                if (current_instruction->isSoaJitRmw())
                    break;
                if (current_instruction->opcode ==
                    Instruction::OpcodeType::INDIR_LD_VIRTUAL_INDEX)
                    break;
                my_instruction_recvs[instruction_id] = true;
                DPRINTF(
                    MAAController,
                    "%s: %s received with backing address 0x%lx!\n",
                    __func__, current_instruction->print(),
                    current_instruction->backingAddr);
                respond_immediately = false;
                scheduleDispatchInstructionEvent();
                break;
            }
            case 4: {
                panic_if(instruction_id == -1,
                         "Received index address before instruction "
                         "header!\n");
                panic_if(
                    current_instruction->opcode !=
                            Instruction::OpcodeType::INDIR_LD_VIRTUAL_INDEX &&
                        current_instruction->opcode !=
                            Instruction::OpcodeType::INDIR_LD_INDEX &&
                        !current_instruction->isSoaJitRmw() &&
                        !current_instruction->isLogicalALUScalar() &&
                        !current_instruction->isLogicalALUVector(),
                    "Instruction word four is only valid for direct-index "
                    "loads or logical ALU_SCALAR source backing!\n");
                if (current_instruction->isLogicalALUScalar()) {
                    maa::LogicalSPDCacheABI::ScalarOperandShape shape;
                    shape.datatype = static_cast<uint8_t>(
                        current_instruction->datatype);
                    shape.optype = static_cast<uint8_t>(
                        current_instruction->optype);
                    shape.src1LogicalID = current_instruction->src1LogicalID;
                    shape.src2LogicalID = current_instruction->src2LogicalID;
                    shape.dst1LogicalID = current_instruction->dst1LogicalID;
                    shape.src1SpdID = current_instruction->src1SpdID;
                    shape.src2SpdID = current_instruction->src2SpdID;
                    shape.dst1SpdID = current_instruction->dst1SpdID;
                    shape.dst2SpdID = current_instruction->dst2SpdID;
                    shape.src1RegID = current_instruction->src1RegID;
                    shape.src2RegID = current_instruction->src2RegID;
                    shape.src3RegID = current_instruction->src3RegID;
                    shape.dst1RegID = current_instruction->dst1RegID;
                    shape.dst2RegID = current_instruction->dst2RegID;
                    shape.condSpdID = current_instruction->condSpdID;
                    shape.baseAddr = current_instruction->baseAddr;
                    shape.sourceBackingAddr = data;
                    shape.destinationBackingAddr =
                        current_instruction->backingAddr;
                    const auto validation =
                        maa::LogicalSPDCacheABI::validateLogicalALUScalar(
                            shape, static_cast<uint8_t>(
                                       current_instruction->opcode),
                            num_regs);
                    panic_if(validation != maa::LogicalSPDCacheABI::
                                               ScalarValidation::Valid,
                             "Rejected logical ALU_SCALAR ABI shape (%d) "
                             "before controller state mutation\n",
                             static_cast<int>(validation));
                    const int source_range = getAddrRegion(data);
                    panic_if(source_range < 0,
                             "Logical source backing address 0x%lx is not "
                             "in a registered memory region\n", data);
                    const auto source_validation =
                        maa::LogicalSPDCacheABI::validateSourceSpan(
                            data, shape.datatype,
                            addrRegions[source_range].first,
                            addrRegions[source_range].second);
                    const auto destination_validation =
                        maa::LogicalSPDCacheABI::validateDestinationSpan(
                            current_instruction->backingAddr, shape.datatype,
                            current_instruction->backingMinAddr,
                            current_instruction->backingMaxAddr);
                    panic_if(source_validation != maa::LogicalSPDCacheABI::
                                                      DestinationValidation::Valid,
                             "Rejected logical ALU_SCALAR source backing "
                             "(%d) before controller state mutation\n",
                             static_cast<int>(source_validation));
                    panic_if(destination_validation !=
                                 maa::LogicalSPDCacheABI::
                                     DestinationValidation::Valid,
                             "Rejected logical ALU_SCALAR destination "
                             "backing (%d) before controller state mutation\n",
                             static_cast<int>(destination_validation));
                    // The accepted legacy bridge remains exactly two
                    // descriptors wide.  IDs 2..6 exist only for the opt-in
                    // production page scheduler and must not reach it.
                    panic_if(
                        !logicalTilePageSchedulerEnabled() &&
                            (current_instruction->src1LogicalID >= 2 ||
                             current_instruction->dst1LogicalID >= 2),
                        "Logical ALU_SCALAR descriptor IDs 2..6 require the "
                        "logical tile page scheduler; legacy IDs 0/1 are "
                        "unchanged\n");
                    current_instruction->logicalSourceBackingAddr = data;
                    current_instruction->logicalSourceAddrRangeID =
                        source_range;
                    current_instruction->logicalSourceMinAddr =
                        addrRegions[source_range].first;
                    current_instruction->logicalSourceMaxAddr =
                        addrRegions[source_range].second;
                    my_instruction_recvs[instruction_id] = true;
                    DPRINTF(MAAController,
                            "%s: %s received with logical source backing "
                            "0x%lx and destination backing 0x%lx!\n",
                            __func__, current_instruction->print(), data,
                            current_instruction->backingAddr);
                    respond_immediately = false;
                    scheduleDispatchInstructionEvent();
                    break;
                }
                if (current_instruction->isLogicalALUVector()) {
                    current_instruction->logicalSourceBackingAddr = data;
                    break;
                }
                current_instruction->indexAddr = data;
                current_instruction->indexAddrRangeID = getAddrRegion(data);
                panic_if(current_instruction->indexAddrRangeID < 0,
                         "Index address 0x%lx is not in a registered memory "
                         "region\n",
                         data);
                current_instruction->indexMinAddr =
                    addrRegions[current_instruction->indexAddrRangeID].first;
                current_instruction->indexMaxAddr =
                    addrRegions[current_instruction->indexAddrRangeID].second;
                if (current_instruction->isSoaJitRmw())
                    break;
                my_instruction_recvs[instruction_id] = true;
                DPRINTF(MAAController,
                        "%s: %s received with index address 0x%lx!\n",
                        __func__, current_instruction->print(),
                        current_instruction->indexAddr);
                respond_immediately = false;
                scheduleDispatchInstructionEvent();
                break;
            }
            case 5: {
                panic_if(instruction_id == -1,
                         "Received predicate address before instruction "
                         "header!\n");
                if (current_instruction->isLogicalALUVector()) {
                    current_instruction->logicalSource2BackingAddr = data;
                    maa::LogicalSPDCacheABI::VectorOperandShape shape;
                    shape.datatype = static_cast<uint8_t>(
                        current_instruction->datatype);
                    shape.optype = static_cast<uint8_t>(
                        current_instruction->optype);
                    shape.src1LogicalID = current_instruction->src1LogicalID;
                    shape.src2LogicalID = current_instruction->src2LogicalID;
                    shape.dst1LogicalID = current_instruction->dst1LogicalID;
                    shape.src1SpdID = current_instruction->src1SpdID;
                    shape.src2SpdID = current_instruction->src2SpdID;
                    shape.dst1SpdID = current_instruction->dst1SpdID;
                    shape.dst2SpdID = current_instruction->dst2SpdID;
                    shape.src1RegID = current_instruction->src1RegID;
                    shape.src2RegID = current_instruction->src2RegID;
                    shape.src3RegID = current_instruction->src3RegID;
                    shape.dst1RegID = current_instruction->dst1RegID;
                    shape.dst2RegID = current_instruction->dst2RegID;
                    shape.condSpdID = current_instruction->condSpdID;
                    shape.baseAddr = current_instruction->baseAddr;
                    shape.source1BackingAddr =
                        current_instruction->logicalSourceBackingAddr;
                    shape.source2BackingAddr = data;
                    shape.destinationBackingAddr =
                        current_instruction->logicalDestinationBackingAddr;
                    const auto validation =
                        maa::LogicalSPDCacheABI::validateLogicalALUVector(
                            shape, static_cast<uint8_t>(
                                       current_instruction->opcode));
                    panic_if(validation != maa::LogicalSPDCacheABI::
                                               VectorValidation::Valid,
                             "Rejected logical ALU_VECTOR ABI shape (%d) "
                             "before controller state mutation\n",
                             static_cast<int>(validation));
                    const int source1_range = getAddrRegion(
                        shape.source1BackingAddr);
                    const int source2_range = getAddrRegion(
                        shape.source2BackingAddr);
                    const int destination_range = getAddrRegion(
                        shape.destinationBackingAddr);
                    panic_if(source1_range < 0 || source2_range < 0 ||
                                 destination_range < 0,
                             "Rejected logical ALU_VECTOR unregistered "
                             "backing span before controller state "
                             "mutation\n");
                    const auto source1_validation =
                        maa::LogicalSPDCacheABI::validateSourceSpan(
                            shape.source1BackingAddr, shape.datatype,
                            addrRegions[source1_range].first,
                            addrRegions[source1_range].second);
                    const auto source2_validation =
                        maa::LogicalSPDCacheABI::validateSourceSpan(
                            shape.source2BackingAddr, shape.datatype,
                            addrRegions[source2_range].first,
                            addrRegions[source2_range].second);
                    const auto destination_validation =
                        maa::LogicalSPDCacheABI::validateDestinationSpan(
                            shape.destinationBackingAddr, shape.datatype,
                            addrRegions[destination_range].first,
                            addrRegions[destination_range].second);
                    const auto valid_span = maa::LogicalSPDCacheABI::
                        DestinationValidation::Valid;
                    panic_if(source1_validation != valid_span ||
                                 source2_validation != valid_span ||
                                 destination_validation != valid_span,
                             "Rejected logical ALU_VECTOR backing span "
                             "before controller state mutation\n");
                    const bool sources_must_be_disjoint =
                        shape.src1LogicalID != shape.src2LogicalID;
                    panic_if(
                        maa::LogicalSPDCacheABI::backingSpansOverlap(
                            shape.destinationBackingAddr,
                            shape.source1BackingAddr, shape.datatype) ||
                        maa::LogicalSPDCacheABI::backingSpansOverlap(
                            shape.destinationBackingAddr,
                            shape.source2BackingAddr, shape.datatype) ||
                        (sources_must_be_disjoint &&
                         maa::LogicalSPDCacheABI::backingSpansOverlap(
                             shape.source1BackingAddr,
                             shape.source2BackingAddr, shape.datatype)),
                        "Rejected logical ALU_VECTOR overlapping backing "
                        "spans before controller state mutation\n");
                    panic_if(!logicalTilePageSchedulerEnabled(),
                             "Logical ALU_VECTOR ABI is disabled unless the "
                             "logical tile page scheduler is enabled\n");
                    current_instruction->logicalSourceAddrRangeID =
                        source1_range;
                    current_instruction->logicalSourceMinAddr =
                        addrRegions[source1_range].first;
                    current_instruction->logicalSourceMaxAddr =
                        addrRegions[source1_range].second;
                    current_instruction->logicalSource2AddrRangeID =
                        source2_range;
                    current_instruction->logicalSource2MinAddr =
                        addrRegions[source2_range].first;
                    current_instruction->logicalSource2MaxAddr =
                        addrRegions[source2_range].second;
                    current_instruction->logicalDestinationAddrRangeID =
                        destination_range;
                    current_instruction->logicalDestinationMinAddr =
                        addrRegions[destination_range].first;
                    current_instruction->logicalDestinationMaxAddr =
                        addrRegions[destination_range].second;
                    my_instruction_recvs[instruction_id] = true;
                    DPRINTF(MAAController,
                            "%s: %s received for logical vector page "
                            "scheduling\n",
                            __func__, current_instruction->print());
                    respond_immediately = false;
                    scheduleDispatchInstructionEvent();
                    break;
                }
                panic_if(!current_instruction->isSoaJitRmw(),
                         "Instruction word five is only valid for the "
                         "guarded SoA/JIT RMW shape\n");
                panic_if(current_instruction->soaJitPredicateWordReceived,
                         "Received duplicate SoA/JIT predicate word\n");
                panic_if(!current_instruction->hasValidSoaJitRmwOperands(),
                         "Rejected malformed SoA/JIT RMW before word-five "
                         "dispatch\n");
                panic_if(current_instruction->isSoaJitScalarRmw() &&
                             data == SoaJitSafety::MaskedIndexModeTag,
                         "Scalar-broadcast SoA/JIT requires a null or "
                         "registered predicate span; masked-index mode is "
                         "vector-only\n");
                current_instruction->soaJitMaskedIndex =
                    current_instruction->isSoaJitVectorRmw() &&
                    data == SoaJitSafety::MaskedIndexModeTag;
                current_instruction->predicateAddr =
                    current_instruction->soaJitMaskedIndex ? 0 : data;
                current_instruction->soaJitPredicateWordReceived = true;
                const int soa_word_size = current_instruction->WordSize();
                const bool operands_aligned =
                    current_instruction->isSoaJitScalarRmw()
                        ? SoaJitSafety::scalarOperandsAligned(
                              current_instruction->baseAddr,
                              current_instruction->indexAddr,
                              current_instruction->predicateAddr,
                              soa_word_size)
                        : SoaJitSafety::typedOperandsAligned(
                              current_instruction->baseAddr,
                              current_instruction->backingAddr,
                              current_instruction->indexAddr,
                              current_instruction->predicateAddr,
                              soa_word_size);
                panic_if(
                    !operands_aligned,
                    "Rejected misaligned typed SoA/JIT A, value, index, or "
                    "predicate operand before timed request dispatch\n");
                if (current_instruction->predicateAddr != 0) {
                    current_instruction->predicateAddrRangeID =
                        getAddrRegion(current_instruction->predicateAddr);
                    panic_if(current_instruction->predicateAddrRangeID < 0,
                             "Predicate address 0x%lx is not in a "
                             "registered memory region\n",
                             current_instruction->predicateAddr);
                    current_instruction->predicateMinAddr = addrRegions[
                        current_instruction->predicateAddrRangeID].first;
                    current_instruction->predicateMaxAddr = addrRegions[
                        current_instruction->predicateAddrRangeID].second;
                }
                if (current_instruction->hasSoaJitOldResult())
                    break;
                my_instruction_recvs[instruction_id] = true;
                DPRINTF(MAAController,
                        "%s: %s received with values=0x%lx indices=0x%lx "
                        "predicates=0x%lx masked_index=%d!\n",
                        __func__, current_instruction->print(),
                        current_instruction->backingAddr,
                        current_instruction->indexAddr,
                        current_instruction->predicateAddr,
                        current_instruction->soaJitMaskedIndex);
                respond_immediately = false;
                scheduleDispatchInstructionEvent();
                break;
            }
            case 6: {
                panic_if(instruction_id == -1,
                         "Received old-result backing before instruction "
                         "header!\n");
                panic_if(!current_instruction->hasSoaJitOldResult() ||
                             !current_instruction->
                                  hasValidSoaJitRmwOperands(),
                         "Instruction word six is only valid for the "
                         "guarded vector old-result SoA/JIT shape\n");
                panic_if(!current_instruction->soaJitPredicateWordReceived ||
                             current_instruction->soaJitResultWordReceived ||
                             current_instruction->addrRangeID < 0 ||
                             current_instruction->backingAddrRangeID < 0 ||
                             current_instruction->indexAddrRangeID < 0,
                         "Old-result word requires one complete, ordered "
                         "A/value/index/predicate staging sequence\n");
                panic_if(current_instruction->datatype !=
                             Instruction::DataType::FLOAT32_TYPE,
                         "SoA/JIT old-result mode currently supports FP32 "
                         "only\n");
                panic_if(!SoaJitSafety::oldResultAligned(data),
                         "SoA/JIT old-result backing 0x%lx must be a "
                         "non-null aligned cache-line span\n", data);
                current_instruction->resultAddr = data;
                current_instruction->resultAddrRangeID = getAddrRegion(data);
                panic_if(current_instruction->resultAddrRangeID < 0,
                         "Old-result address 0x%lx is not in a registered "
                         "memory region\n", data);
                current_instruction->resultMinAddr = addrRegions[
                    current_instruction->resultAddrRangeID].first;
                current_instruction->resultMaxAddr = addrRegions[
                    current_instruction->resultAddrRangeID].second;
                constexpr Addr result_bytes =
                    16 * 1024 * sizeof(float);
                panic_if(data < current_instruction->resultMinAddr ||
                             data >= current_instruction->resultMaxAddr ||
                             current_instruction->resultMaxAddr - data <
                                 result_bytes,
                         "SoA/JIT old-result full logical-16K span exceeds "
                         "its registered region\n");
                panic_if(
                    current_instruction->resultAddrRangeID ==
                            current_instruction->addrRangeID ||
                        current_instruction->resultAddrRangeID ==
                            current_instruction->backingAddrRangeID ||
                        current_instruction->resultAddrRangeID ==
                            current_instruction->indexAddrRangeID ||
                        (current_instruction->predicateAddr != 0 &&
                         current_instruction->resultAddrRangeID ==
                             current_instruction->predicateAddrRangeID),
                    "Old-result backing must not alias an input or target "
                    "memory region\n");
                current_instruction->soaJitResultWordReceived = true;
                my_instruction_recvs[instruction_id] = true;
                DPRINTF(MAAController,
                        "%s: %s received with old-result backing=0x%lx!\n",
                        __func__, current_instruction->print(),
                        current_instruction->resultAddr);
                respond_immediately = false;
                scheduleDispatchInstructionEvent();
                break;
            }
            default:
                assert(false);
            }
            assert(pkt->needsResponse());
            if (respond_immediately) {
                pkt->makeTimingResponse();
                // Here we reset the timing of the packet.
                Tick old_header_delay = pkt->headerDelay;
                pkt->headerDelay = pkt->payloadDelay = 0;
                cpuSidePorts[core_id]->schedTimingResp(pkt, getClockEdge(Cycles(1)) + old_header_delay);
            }
            break;
        }
        default:
            // Write to SPD_DATA_CACHEABLE_RANGE not possible. All SPD writes must be to SPD_DATA_NONCACHEABLE_RANGE
            // Write to SPD_SIZE_RANGE not possible. Size is read-only.
            // Write to SPD_READY_RANGE not possible. Ready is read-only.
            panic_if(true, "%s: Error: Range(%s) and cmd(%s) is illegal. Packet: %s\n", __func__, address_range.print(), pkt->cmdString(), pkt->print());
            assert(false);
        }
        break;
    }
    case MemCmd::ReadReq: {
        // all read responses have a data payload
        assert(pkt->hasRespData());
        switch (address_range.getType()) {
        case AddressRangeType::Type::SPD_SIZE_RANGE: {
            panic_if(core_id != 0, "Size range is only for the core 0\n");
            panic_if(pkt->getSize() != sizeof(uint16_t), "%s: Error: Invalid size for SPD size: %d, packet: %s\n", __func__, pkt->getSize(), pkt->print());
            Addr offset = address_range.getOffset();
            assert(offset % sizeof(uint16_t) == 0);
            int element_id = offset / sizeof(uint16_t);
            panic_if(logicalTileReservedLane(element_id),
                     "Guest size read references reserved SPD lane "
                     "%d\n", element_id);
            int full_size = spd->getSize(element_id);
            uint16_t data = (full_size > static_cast<int>(std::numeric_limits<uint16_t>::max()))
                                ? std::numeric_limits<uint16_t>::max()
                                : static_cast<uint16_t>(full_size);
            uint8_t *dataPtr = (uint8_t *)(&data);
            pkt->setData(dataPtr);
            assert(pkt->needsResponse());
            pkt->makeTimingResponse();
            // Here we reset the timing of the packet.
            Tick old_header_delay = pkt->headerDelay;
            pkt->headerDelay = pkt->payloadDelay = 0;
            cpuSidePorts[core_id]->schedTimingResp(pkt, getClockEdge(Cycles(1)) + old_header_delay);
            break;
        }
        case AddressRangeType::Type::SPD_READY_RANGE: {
            panic_if(core_id != 0, "Ready range is only for the core 0\n");
            panic_if(pkt->getSize() != sizeof(uint16_t), "%s: Error: Invalid size for SPD ready: %d, packet: %s\n", __func__, pkt->getSize(), pkt->print());
            Addr offset = address_range.getOffset();
            assert(offset % sizeof(uint16_t) == 0);
            int ready_tile_id = offset / sizeof(uint16_t);
            panic_if(logicalTileReservedLane(ready_tile_id),
                     "Guest readiness read references reserved SPD "
                     "lane %d\n", ready_tile_id);
            const uint16_t one = 1;
            pkt->setData((const uint8_t *)&one);
            assert(pkt->needsResponse());
            if (spd->getTileReady(ready_tile_id)) {
                pkt->makeTimingResponse();
                // Here we reset the timing of the packet.
                Tick old_header_delay = pkt->headerDelay;
                pkt->headerDelay = pkt->payloadDelay = 0;
                cpuSidePorts[core_id]->schedTimingResp(pkt, getClockEdge(Cycles(1)) + old_header_delay);
            } else {
                // We need to respond to this packet later
                my_ready_pkts.push_back(pkt);
                my_ready_tile_ids.push_back(ready_tile_id);
            }
            break;
        }
        case AddressRangeType::Type::VIRTUAL_PAGE_READY_RANGE: {
            panic_if(core_id != 0,
                     "Virtual-page ready range is only for core 0\n");
            panic_if(pkt->getSize() != sizeof(uint16_t),
                     "%s: invalid virtual-page ready read size %d\n",
                     __func__, pkt->getSize());
            const Addr offset = address_range.getOffset();
            panic_if(offset % sizeof(uint16_t) != 0,
                     "unaligned virtual-page ready offset 0x%lx\n", offset);
            const int virtualID = offset / sizeof(uint16_t);
            const int tokenTileID = virtualID / MAA::MaxVirtualPages;
            const int pageID = virtualID % MAA::MaxVirtualPages;
            const int readyID =
                num_tiles + tokenTileID * MAA::MaxVirtualPages + pageID;
            const uint16_t one = 1;
            pkt->setData((const uint8_t *)&one);
            assert(pkt->needsResponse());
            stats.virtual_page_wait_reads++;
            if (getVirtualPageReady(tokenTileID, pageID)) {
                DPRINTF(MAAVirtualTrace,
                        "event=page_wait_immediate token=%d page=%d\n",
                        tokenTileID, pageID);
                pkt->makeTimingResponse();
                Tick oldHeaderDelay = pkt->headerDelay;
                pkt->headerDelay = pkt->payloadDelay = 0;
                cpuSidePorts[core_id]->schedTimingResp(
                    pkt, getClockEdge(Cycles(1)) + oldHeaderDelay);
                stats.virtual_page_wait_responses++;
            } else {
                DPRINTF(MAAVirtualTrace,
                        "event=page_wait_deferred token=%d page=%d "
                        "ready_id=%d\n",
                        tokenTileID, pageID, readyID);
                my_ready_pkts.push_back(pkt);
                my_ready_tile_ids.push_back(readyID);
                stats.virtual_page_wait_deferrals++;
            }
            break;
        }
        case AddressRangeType::Type::SCALAR_RANGE: {
            panic_if(core_id != 0, "Scalar range is only for the core 0\n");
            panic_if(pkt->getSize() != 4 && pkt->getSize() != 8, "Invalid size for SPD data: %d\n", pkt->getSize());
            Addr offset = address_range.getOffset();
            int element_id = offset % (num_regs * sizeof(uint32_t));
            assert(element_id % sizeof(uint32_t) == 0);
            element_id /= sizeof(uint32_t);
            uint8_t *dataPtr = rf->getDataPtr(element_id);
            pkt->setData(dataPtr);
            assert(pkt->needsResponse());
            pkt->makeTimingResponse();
            // Here we reset the timing of the packet.
            Tick old_header_delay = pkt->headerDelay;
            pkt->headerDelay = pkt->payloadDelay = 0;
            cpuSidePorts[core_id]->schedTimingResp(pkt, getClockEdge(Cycles(1)) + old_header_delay);
            break;
        }
        default: {
            // Read from SPD_DATA_CACHEABLE_RANGE uses ReadSharedReq command.
            // Read from SPD_DATA_NONCACHEABLE_RANGE not possible. All SPD reads must be from SPD_DATA_CACHEABLE_RANGE.
            panic_if(true, "%s: Error: Range(%s) and cmd(%s) is illegal. Packet: %s\n", __func__, address_range.print(), pkt->cmdString(), pkt->print());
            assert(false);
        }
        }
        break;
    }
    case MemCmd::ReadExReq:
    case MemCmd::ReadSharedReq: {
        // all read responses have a data payload
        assert(pkt->hasRespData());
        switch (address_range.getType()) {
        case AddressRangeType::Type::SPD_DATA_CACHEABLE_RANGE: {
            Addr offset = address_range.getOffset();
            int tile_id = offset / (num_tile_elements * sizeof(uint32_t));
            panic_if(logicalTileReservedLane(tile_id) ||
                         logicalCompletionLaneOwned(tile_id),
                     "Guest cacheable data request references "
                     "reserved/owned SPD lane %d\n", tile_id);
            int element_id = offset % (num_tile_elements * sizeof(uint32_t));
            assert(element_id % sizeof(uint32_t) == 0);
            element_id /= sizeof(uint32_t);
            spd->setTileDirty(tile_id, 4);
            if (pkt->cmd == MemCmd::ReadSharedReq) {
                invalidator->read(tile_id, element_id);
            } else {
                invalidator->write(tile_id, element_id);
            }
            uint8_t *dataPtr = spd->getDataPtr(tile_id, element_id);
            pkt->setData(dataPtr);
            assert(pkt->needsResponse());
            pkt->makeTimingResponse();
            // Here we reset the timing of the packet.
            Tick old_header_delay = pkt->headerDelay;
            pkt->headerDelay = pkt->payloadDelay = 0;
            cpuSidePorts[core_id]->schedTimingResp(pkt, getClockEdge(Cycles(1)) + old_header_delay);
            break;
        }
        default:
            panic_if(true, "%s: Error: Range(%s) and cmd(%s) is illegal. Packet: %s\n", __func__, address_range.print(), pkt->cmdString(), pkt->print());
            assert(false);
        }
        break;
    }
    default:
        assert(false);
    }
}
bool MAA::CpuSidePort::recvTimingReq(PacketPtr pkt) {
    assert(pkt->isRequest());
    /// print the packet
    DPRINTF(MAACpuPort, "%s: received %s\n", __func__, pkt->print());

    // A timing retry grants the source port another attempt; gem5 does not
    // require that attempt to carry the same logical request.
    const bool is_retry_attempt = tileRequestRetryOutstanding;
    if (is_retry_attempt) {
        assert(!mustRetryTileRequest);
        tileRequestRetryOutstanding = false;
        maa.stats.cpu_spd_data_read_retry_attempts++;
    }
    if (tryTiming(pkt)) {
        if (is_retry_attempt) {
            maa.stats.cpu_spd_data_read_retry_acceptances++;
        }
        maa.recvTimingReq(pkt, core_id);
        return true;
    }
    return false;
}
void MAA::CpuSidePort::recvFunctional(PacketPtr pkt) {
    assert(false);
}
Tick MAA::recvAtomic(PacketPtr pkt) {
    /// print the packet
    DPRINTF(MAACpuPort, "%s: received %s\n", __func__, pkt->print());
    assert(false);
    return 0;
}
Tick MAA::CpuSidePort::recvAtomic(PacketPtr pkt) {
    /// print the packet
    DPRINTF(MAACpuPort, "%s: received %s\n", __func__, pkt->print());
    assert(false);
    return maa.recvAtomic(pkt);
}

AddrRangeList MAA::CpuSidePort::getAddrRanges() const {
    return maa.getAddrRanges(core_id);
}

bool MAA::CpuSidePort::sendSnoopInvalidatePacket(PacketPtr pkt) {
    /// print the packet
    DPRINTF(MAACpuPort, "%s: sending invalidation %s\n", __func__, pkt->print());
    int pkt_core_id = maa.core_addr(pkt->getAddr());
    panic_if(pkt_core_id != core_id, "%s: packet is for core %d\n", __func__, pkt_core_id);
    panic_if(is_blocked, "%s: port is blocked\n", __func__);
    if (outstandingCpuSidePackets == maxOutstandingCpuSidePackets) {
        // XBAR is full
        DPRINTF(MAACpuPort, "%s Send failed because XBAR is full...\n", __func__);
        is_blocked = true;
        return false;
    }
    sendTimingSnoopReq(pkt);
    DPRINTF(MAACpuPort, "%s Send is successfull...\n", __func__);
    if (pkt->cacheResponding())
        outstandingCpuSidePackets++;
    return true;
}
void MAA::CpuSidePort::retryTileRequest() {
    if (!mustRetryTileRequest) {
        return;
    }
    assert(retryTileID >= 0);
    if (!maa.spd->getTileReady(retryTileID)) {
        return;
    }

    assert(!tileRequestRetryOutstanding);
    const int tile_id = retryTileID;
    DPRINTF(MAACpuPort, "%s: retrying request for tile[%d]\n", __func__,
            tile_id);
    mustRetryTileRequest = false;
    tileRequestRetryOutstanding = true;
    retryTileID = -1;
    maa.stats.cpu_spd_data_read_retry_signals++;
    sendRetryReq();
}
bool MAA::sendSnoopInvalidateCpu(PacketPtr pkt) {
    panic_if(pkt->isExpressSnoop() == false, "Packet is not an express snoop packet\n");
    int pkt_core_id = core_addr(pkt->getAddr());
    return cpuSidePorts[pkt_core_id]->sendSnoopInvalidatePacket(pkt);
}

void MAA::sendSnoopPacketCpu(PacketPtr pkt) {
    panic_if(pkt->isExpressSnoop() == false, "Packet is not an express snoop packet\n");
    int pkt_core_id = core_addr(pkt->getAddr());
    cpuSidePorts[pkt_core_id]->sendTimingSnoopReq(pkt);
}

void MAA::CpuSidePort::allocate(int _core_id, int _maxOutstandingCpuSidePackets) {
    outstandingCpuSidePackets = 0;
    core_id = _core_id;
    maxOutstandingCpuSidePackets = _maxOutstandingCpuSidePackets - 16;
    is_blocked = false;
    mustRetryTileRequest = false;
    tileRequestRetryOutstanding = false;
    retryTileID = -1;
}

MAA::CpuSidePort::CpuSidePort(const std::string &_name, MAA &_maa,
                              const std::string &_label)
    : MAAResponsePort(_name, _maa, _label) {
}
} // namespace gem5
