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
            panic_if(pkt->getSize() != 64, "Invalid size for SPD data: %d\n", pkt->getSize());
            int element_id = (offset % (num_tile_elements * sizeof(uint32_t))) / sizeof(uint32_t);
            for (int i = 0; i < 64 / sizeof(uint32_t); i++) {
                uint32_t data = pkt->getPtr<uint32_t>()[i];
                DPRINTF(MAACpuPort, "%s: TILE[%d][%d] = %u\n", __func__, tile_id, element_id + i, data);
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
                current_instruction->dst1SpdID = (data & NA_UINT8) == NA_UINT8 ? -1 : (data & NA_UINT8);
                data = data >> 8;
                current_instruction->optype = (data & NA_UINT8) == NA_UINT8 ? Instruction::OPType::MAX : static_cast<Instruction::OPType>(data & NA_UINT8);
                data = data >> 8;
                current_instruction->datatype = (data & NA_UINT8) == NA_UINT8 ? Instruction::DataType::MAX : static_cast<Instruction::DataType>(data & NA_UINT8);
                assert(current_instruction->datatype != Instruction::DataType::MAX);
                data = data >> 8;
                current_instruction->opcode = (data & NA_UINT8) == NA_UINT8 ? Instruction::OpcodeType::MAX : static_cast<Instruction::OpcodeType>(data & NA_UINT8);
                assert(current_instruction->opcode != Instruction::OpcodeType::MAX);
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
                current_instruction->baseAddr = data;
                current_instruction->state = Instruction::Status::Idle;
                current_instruction->CID = pkt->req->contextId();
                current_instruction->PC = pkt->req->getPC();
                if (current_instruction->isLogicalALUScalar()) {
                    panic_if(
                        data != maa::LogicalSPDCacheABI::NoAddress,
                        "Logical ALU_SCALAR word 2 must use the no-address "
                        "sentinel, got 0x%016lx\n", data);
                    break;
                }
                if (current_instruction->accessType !=
                    Instruction::AccessType::COMPUTE) {
                    current_instruction->addrRangeID =
                        getAddrRegion(current_instruction->baseAddr);
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
                        !(current_instruction->opcode ==
                              Instruction::OpcodeType::INDIR_RMW_VECTOR &&
                          current_instruction->src1SpdID == -1 &&
                          current_instruction->src2SpdID == -1 &&
                          current_instruction->condSpdID == -1) &&
                        !current_instruction->isLogicalALUScalar(),
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
                if (current_instruction->opcode ==
                        Instruction::OpcodeType::INDIR_LD_VIRTUAL_INDEX ||
                    (current_instruction->opcode ==
                         Instruction::OpcodeType::INDIR_RMW_VECTOR &&
                     current_instruction->src1SpdID == -1 &&
                     current_instruction->src2SpdID == -1))
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
                        !(current_instruction->opcode ==
                              Instruction::OpcodeType::INDIR_RMW_VECTOR &&
                          current_instruction->src1SpdID == -1 &&
                          current_instruction->src2SpdID == -1 &&
                          current_instruction->condSpdID == -1) &&
                        !current_instruction->isLogicalALUScalar(),
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
                my_instruction_recvs[instruction_id] = true;
                DPRINTF(MAAController,
                        "%s: %s received with index address 0x%lx!\n",
                        __func__, current_instruction->print(),
                        current_instruction->indexAddr);
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
