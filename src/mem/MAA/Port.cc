#include <algorithm>
#include <cassert>
#include <cstdint>
#include <string>

#include "base/addr_range.hh"
#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/MAACachePort.hh"
#include "debug/MAAController.hh"
#include "debug/MAACpuPort.hh"
#include "debug/MAAMemPort.hh"
#include "debug/MAAPort.hh"
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
#include "sim/cur_tick.hh"

#ifndef TRACING_ON
#define TRACING_ON 1
#endif
namespace gem5 {
namespace {

LogicalStreamResponseKind
logicalStreamRequestKind(const MemCmd &command)
{
    panic_if(command != MemCmd::ReadReq && command != MemCmd::ReadExReq &&
                 command != MemCmd::WriteReq,
             "%s: command %s has no logical stream counter ownership\n",
             __func__, command.toString());
    if (command == MemCmd::ReadReq)
        return LogicalStreamResponseKind::Read;
    if (command == MemCmd::ReadExReq)
        return LogicalStreamResponseKind::ReadEx;
    return LogicalStreamResponseKind::Write;
}

bool
decodeLogicalStreamResponseKind(const MemCmd &command,
                                LogicalStreamResponseKind &kind)
{
    if (command == MemCmd::ReadResp) {
        kind = LogicalStreamResponseKind::Read;
        return true;
    }
    if (command == MemCmd::ReadExResp) {
        kind = LogicalStreamResponseKind::ReadEx;
        return true;
    }
    if (command == MemCmd::WriteResp) {
        kind = LogicalStreamResponseKind::Write;
        return true;
    }
    return false;
}

constexpr uint32_t MaxResponseSenderStateDepth = 64;

bool
senderStateChainExcludes(const Packet::SenderState *state,
                         const Packet::SenderState *target)
{
    uint32_t depth = 0;
    while (state != nullptr && depth < MaxResponseSenderStateDepth) {
        if (state == target)
            return false;
        state = state->predecessor;
        ++depth;
    }
    // A longer or cyclic chain cannot prove unique ownership.
    return state == nullptr;
}

struct LogicalSenderStateSearch
{
    LogicalSPDTransactionState *state = nullptr;
    bool complete = false;
};

LogicalSenderStateSearch
findLogicalSenderStateBounded(Packet::SenderState *state)
{
    uint32_t depth = 0;
    while (state != nullptr && depth < MaxResponseSenderStateDepth) {
        if (auto *logical =
                dynamic_cast<LogicalSPDTransactionState *>(state)) {
            return {logical, true};
        }
        state = state->predecessor;
        ++depth;
    }
    return {nullptr, state == nullptr};
}

void
applyLogicalStreamCounterEvent(uint32_t &counter, const MemCmd &command,
                               LogicalStreamCounterEvent event, Addr address)
{
    const LogicalStreamCounterDecision decision =
        decideLogicalStreamCounterUpdate(logicalStreamRequestKind(command),
                                         event, counter);
    panic_if(!decision.valid,
             "%s: logical stream counter boundary violation for %s event %d "
             "at 0x%lx (count %u)\n",
             __func__, command.toString(), static_cast<int>(event), address,
             counter);
    counter = decision.value;
}

void
settleAcceptedStreamReadCounter(uint32_t &counter, bool logical,
                                const MemCmd &command, Addr address)
{
    if (logical) {
        applyLogicalStreamCounterEvent(
            counter, command, LogicalStreamCounterEvent::SendAccepted,
            address);
    } else {
        --counter;
    }
}

} // anonymous namespace

bool
MAA::canCoalesceOutstandingRead(Addr paddr, FuncUnitType func_unit,
                                int maa_id) const
{
    const auto outstanding = my_outstanding_pkt_map.find(paddr);
    if (outstanding == my_outstanding_pkt_map.end() ||
        outstanding->second.virtualRetirement ||
        outstanding->second.logicalResponseManaged ||
        outstanding->second.cmd != MemCmd::ReadReq)
        return false;

    for (size_t i = 0; i < outstanding->second.maaIDs.size(); ++i) {
        if (outstanding->second.maaIDs[i] == maa_id &&
            outstanding->second.funcUnits[i] == func_unit)
            return false;
    }
    const auto deferred = my_deferred_pkt_map.find(paddr);
    return deferred == my_deferred_pkt_map.end() || deferred->second.empty();
}

void
MAA::sendPacket(FuncUnitType funcUnit, int maaID, PacketPtr pkt, Tick tick,
                bool force_cache, bool force_retirement_cache,
                bool bypass_deferred_queue)
{
    Addr paddr = pkt->req->getPaddr();
    const LogicalSenderStateSearch sender_state_search =
        findLogicalSenderStateBounded(pkt->senderState);
    auto *logical_state = sender_state_search.state;
    panic_if(!sender_state_search.complete,
             "%s: sender-state stack exceeds bounded ownership proof\n",
             __func__);
    const bool logical_response_managed = logical_state != nullptr;
    const LogicalStreamTransactionTag logical_transaction =
        logical_response_managed ? logical_state->tag
                                 : LogicalStreamTransactionTag{};
    if (logical_response_managed) {
        panic_if(pkt->senderState != logical_state ||
                     !senderStateChainExcludes(logical_state->predecessor,
                                               logical_state) ||
                     funcUnit != FuncUnitType::STREAM ||
                     !logical_transaction.valid() ||
                     logical_transaction.maaID != maaID ||
                     logical_state->lineAddress != paddr,
                 "%s: invalid logical stream packet identity at 0x%lx\n",
                 __func__, paddr);
        if (logical_transaction.action == LogicalStreamAction::Writeback) {
            panic_if(pkt->cmd != MemCmd::WriteReq || !force_cache ||
                         !force_retirement_cache,
                     "%s: logical writeback must be a response-bearing "
                     "retirement-cache WriteReq\n",
                     __func__);
        }
    }
    panic_if(force_retirement_cache && !force_cache,
             "%s: retirement-cache routing requires force_cache\n", __func__);
    panic_if(pkt->getAddr() != paddr, "%s: paddr 0x%lx and addr 0x%lx do not match for packet %s\n", __func__, paddr, pkt->getAddr(), pkt->print());
    const auto outstanding_it = my_outstanding_pkt_map.find(paddr);
    const auto deferred_it = my_deferred_pkt_map.find(paddr);
    const bool has_deferred_packets =
        deferred_it != my_deferred_pkt_map.end() &&
        !deferred_it->second.empty();
    const bool retirement_owns_address =
        outstanding_it != my_outstanding_pkt_map.end() &&
        outstanding_it->second.virtualRetirement;
    const bool logical_owns_address =
        outstanding_it != my_outstanding_pkt_map.end() &&
        outstanding_it->second.logicalResponseManaged;
    if (!bypass_deferred_queue &&
        (has_deferred_packets || retirement_owns_address ||
         logical_owns_address ||
         (logical_response_managed &&
          outstanding_it != my_outstanding_pkt_map.end()))) {
        DPRINTF(MAAPort,
                "%s: deferring packet %s behind exact-address "
                "retirement serialization at 0x%lx\n",
                __func__, pkt->print(), paddr);
        my_deferred_pkt_map[paddr].push_back(
            {funcUnit, maaID, pkt, tick, force_cache,
             force_retirement_cache, logical_response_managed,
             logical_transaction});
        stats.virtual_retirement_native_deferrals += 1;
        if (!retirement_owns_address)
            stats.virtual_retirement_queue_deferrals += 1;
        return;
    }
    if (outstanding_it != my_outstanding_pkt_map.end()) {
        DPRINTF(MAAPort, "%s: found %s in outstanding packets\n", __func__, pkt->print());
        if (my_outstanding_pkt_map[paddr].cmd == MemCmd::WritebackDirty && pkt->cmd == MemCmd::ReadExReq) {
            DPRINTF(MAAPort, "%s: store to load forwarding for outstanding write packet %s and new read packet %s\n", __func__, my_outstanding_pkt_map[paddr].packet->print(), pkt->print());
            panic_if(my_outstanding_pkt_map[paddr].maaIDs.size() != 1, "%s: multiple units on outstanding write packet %s\n", __func__, my_outstanding_pkt_map[paddr].packet->print());
            panic_if(my_outstanding_pkt_map[paddr].funcUnits[0] != funcUnit || my_outstanding_pkt_map[paddr].maaIDs[0] != maaID, "%s: outstanding write maaID %d, funcUnit %s, packet %s do not match with new read maaID %d, funcUnit %s, packet %s\n", __func__, my_outstanding_pkt_map[paddr].maaIDs[0], func_unit_names[(uint8_t)my_outstanding_pkt_map[paddr].funcUnits[0]], my_outstanding_pkt_map[paddr].packet->print(), maaID, func_unit_names[(uint8_t)funcUnit], pkt->print());
            if (funcUnit == FuncUnitType::INDIRECT) {
                if (my_outstanding_pkt_map[paddr].cached) {
                    indirectAccessUnits[maaID].cacheReadPacketSent(paddr);
                } else {
                    indirectAccessUnits[maaID].memReadPacketSent(paddr);
                }
                panic_if(indirectAccessUnits[maaID].recvData(paddr, my_outstanding_pkt_map[paddr].packet->getPtr<uint8_t>(), my_outstanding_pkt_map[paddr].cached) == false, "%s: received %s but rejected from indirectAccessUnits[%d]\n", __func__, my_outstanding_pkt_map[paddr].packet->print(), maaID);
            } else if (funcUnit == FuncUnitType::STREAM) {
                streamAccessUnits[maaID].readPacketSent(paddr);
                panic_if(streamAccessUnits[maaID].recvData(paddr, my_outstanding_pkt_map[paddr].packet->getPtr<uint8_t>()) == false, "%s: received %s but rejected from streamAccessUnits[%d]\n", __func__, my_outstanding_pkt_map[paddr].packet->print(), maaID);
            } else {
                panic("Invalid func unit type\n");
            }
        } else if (my_outstanding_pkt_map[paddr].cmd == MemCmd::WritebackDirty && pkt->cmd == MemCmd::WritebackDirty) {
            DPRINTF(MAAPort, "%s: store to store replacement for outstanding write packet %s and new write packet %s\n", __func__, my_outstanding_pkt_map[paddr].packet->print(), pkt->print());
            panic_if(my_outstanding_pkt_map[paddr].maaIDs.size() != 1, "%s: multiple units on outstanding write packet %s\n", __func__, my_outstanding_pkt_map[paddr].packet->print());
            panic_if(my_outstanding_pkt_map[paddr].funcUnits[0] != funcUnit || my_outstanding_pkt_map[paddr].maaIDs[0] != maaID, "%s: outstanding write maaID %d, funcUnit %s, packet %s do not match with new write maaID %d, funcUnit %s, packet %s\n", __func__, my_outstanding_pkt_map[paddr].maaIDs[0], func_unit_names[(uint8_t)my_outstanding_pkt_map[paddr].funcUnits[0]], my_outstanding_pkt_map[paddr].packet->print(), maaID, func_unit_names[(uint8_t)funcUnit], pkt->print());
            my_outstanding_pkt_map[paddr].packet->setData(pkt->getPtr<uint8_t>());
            if (funcUnit == FuncUnitType::INDIRECT) {
                if (my_outstanding_pkt_map[paddr].cached) {
                    indirectAccessUnits[maaID].cacheWritePacketSent(paddr);
                } else {
                    indirectAccessUnits[maaID].memWritePacketSent(paddr);
                }
            } else if (funcUnit == FuncUnitType::STREAM) {
                streamAccessUnits[maaID].writePacketSent(paddr);
            } else {
                panic("Invalid func unit type\n");
            }
        } else {
            panic_if(my_outstanding_pkt_map[paddr].cmd != pkt->cmd, "%s Outstanding command %s from packet %s does not match with command %s from packet %s\n", __func__, my_outstanding_pkt_map[paddr].cmd.toString(), my_outstanding_pkt_map[paddr].packet->print(), pkt->cmdString(), pkt->print());
            panic_if(pkt->isWrite(), "%s cannot have duplicated writes %s and %s\n", __func__, my_outstanding_pkt_map[paddr].packet->print(), pkt->print());
            panic_if(pkt->isRead() == false, "%s: packet %s is not read!\n", __func__, pkt->print());
            if (funcUnit == FuncUnitType::STREAM) {
                for (size_t i = 0;
                     i < my_outstanding_pkt_map[paddr].maaIDs.size(); ++i) {
                    if (my_outstanding_pkt_map[paddr].funcUnits[i] !=
                        FuncUnitType::INDIRECT)
                        continue;
                    const int indirect_id =
                        my_outstanding_pkt_map[paddr].maaIDs[i];
                    if (indirectAccessUnits[indirect_id]
                            .hasPendingDirectIndexLine(paddr)) {
                        (*stats.IND_VirtIndexOutstandingMerges[indirect_id])++;
                    }
                }
            }
            for (int i = 0;
                 i < my_outstanding_pkt_map[paddr].maaIDs.size(); i++) {
                panic_if(
                    my_outstanding_pkt_map[paddr].maaIDs[i] == maaID &&
                        my_outstanding_pkt_map[paddr].funcUnits[i] == funcUnit,
                    "%s: maaID %d and funcUnit %s already in the "
                    "outstanding packet %s\n",
                    __func__, maaID, func_unit_names[(uint8_t)funcUnit],
                    pkt->print());
            }
            my_outstanding_pkt_map[paddr].maaIDs.push_back(maaID);
            my_outstanding_pkt_map[paddr].funcUnits.push_back(funcUnit);
            if (my_outstanding_pkt_map[paddr].sent == false) {
                if (my_outstanding_pkt_map[paddr].tick < tick) {
                    my_outstanding_pkt_map[paddr].tick = tick;
                }
                if (funcUnit == FuncUnitType::INDIRECT) {
                    my_num_outstanding_indirect_pkts[maaID]++;
                } else if (funcUnit == FuncUnitType::STREAM) {
                    my_num_outstanding_stream_pkts[maaID]++;
                } else {
                    panic("Invalid func unit type\n");
                }
            } else {
                if (funcUnit == FuncUnitType::INDIRECT) {
                    if (my_outstanding_pkt_map[paddr].cached) {
                        indirectAccessUnits[maaID].cacheReadPacketSent(paddr);
                    } else {
                        indirectAccessUnits[maaID].memReadPacketSent(paddr);
                    }
                } else if (funcUnit == FuncUnitType::STREAM) {
                    streamAccessUnits[maaID].readPacketSent(paddr);
                } else {
                    panic("Invalid func unit type\n");
                }
            }
        }
    } else {
        my_outstanding_pkt_map[paddr] = OutstandingPacket(pkt, paddr, tick, pkt->cmd);
        my_outstanding_pkt_map[paddr].virtualRetirement =
            force_retirement_cache;
        my_outstanding_pkt_map[paddr].logicalResponseManaged =
            logical_response_managed;
        my_outstanding_pkt_map[paddr].logicalTransaction =
            logical_transaction;
        bool hit_cache = true;
        if (force_cache_access == false && force_cache == false) {
            RequestPtr snoop_req = std::make_shared<Request>(pkt->req->getPaddr(), pkt->req->getSize(), pkt->req->getFlags(), pkt->req->requestorId());
            PacketPtr snoop_pkt = new Packet(snoop_req, MemCmd::SnoopReq);
            snoop_pkt->setExpressSnoop();
            snoop_pkt->headerDelay = snoop_pkt->payloadDelay = 0;
            sendSnoopPacketCpu(snoop_pkt);
            hit_cache = snoop_pkt->isBlockCached();
            DPRINTF(MAAPort, "%s: force_cache is false, snoop request for %s determined %s\n", __func__, pkt->print(), hit_cache ? "cached" : "not cached");
            delete snoop_pkt;
        }
        my_outstanding_pkt_map[paddr].maaIDs.push_back(maaID);
        my_outstanding_pkt_map[paddr].funcUnits.push_back(funcUnit);
        int core_id = core_addr(paddr);
        int channel_id = channel_addr(paddr);
        if (funcUnit == FuncUnitType::INDIRECT) {
            my_num_outstanding_indirect_pkts[maaID]++;
            if (hit_cache) {
                my_outstanding_pkt_map[paddr].cached = true;
                if (pkt->isRead()) {
                    my_outstanding_indirect_cache_read_pkts[core_id].insert(my_outstanding_pkt_map[paddr]);
                    DPRINTF(MAAPort, "%s: inserting my_outstanding_indirect_cache_read_pkts[%s\n", __func__, core_id);
                } else if (pkt->isWrite()) {
                    my_outstanding_indirect_cache_write_pkts[core_id].insert(my_outstanding_pkt_map[paddr]);
                    DPRINTF(MAAPort, "%s: inserting my_outstanding_indirect_cache_write_pkts[%s\n", __func__, core_id);
                } else {
                    panic("Invalid packet type\n");
                }
            } else {
                my_outstanding_pkt_map[paddr].cached = false;
                if (pkt->isRead()) {
                    my_outstanding_indirect_mem_read_pkts[channel_id].insert(my_outstanding_pkt_map[paddr]);
                    DPRINTF(MAAPort, "%s: inserting my_outstanding_indirect_mem_read_pkts[%s\n", __func__, channel_id);
                } else if (pkt->isWrite()) {
                    my_outstanding_indirect_mem_write_pkts[channel_id].insert(my_outstanding_pkt_map[paddr]);
                    DPRINTF(MAAPort, "%s: inserting my_outstanding_indirect_mem_write_pkts[%s\n", __func__, channel_id);
                } else {
                    panic("Invalid packet type\n");
                }
            }
        } else if (funcUnit == FuncUnitType::STREAM) {
            if (logical_response_managed) {
                applyLogicalStreamCounterEvent(
                    my_num_outstanding_stream_pkts[maaID], pkt->cmd,
                    LogicalStreamCounterEvent::Enqueued, paddr);
            } else {
                my_num_outstanding_stream_pkts[maaID]++;
            }
            my_outstanding_pkt_map[paddr].cached = true;
            if (hit_cache) {
                if (pkt->isRead()) {
                    my_outstanding_stream_cache_read_pkts[core_id].insert(my_outstanding_pkt_map[paddr]);
                    DPRINTF(MAAPort, "%s: inserting my_outstanding_stream_cache_read_pkts[%s\n", __func__, core_id);
                } else if (pkt->isWrite()) {
                    my_outstanding_stream_cache_write_pkts[core_id].insert(my_outstanding_pkt_map[paddr]);
                    DPRINTF(MAAPort, "%s: inserting my_outstanding_stream_cache_write_pkts[%s\n", __func__, core_id);
                } else {
                    panic("Invalid packet type\n");
                }
            } else {
                if (pkt->isRead()) {
                    my_outstanding_stream_mem_read_pkts[core_id].insert(my_outstanding_pkt_map[paddr]);
                    DPRINTF(MAAPort, "%s: inserting my_outstanding_stream_mem_read_pkts[%s\n", __func__, core_id);
                } else if (pkt->isWrite()) {
                    my_outstanding_stream_mem_write_pkts[core_id].insert(my_outstanding_pkt_map[paddr]);
                    DPRINTF(MAAPort, "%s: inserting my_outstanding_stream_mem_write_pkts[%s\n", __func__, core_id);
                } else {
                    panic("Invalid packet type\n");
                }
            }
        } else {
            panic("Invalid func unit type\n");
        }
        if (my_outstanding_pkt_map[paddr].cached) {
            scheduleNextSendCache();
        } else {
            scheduleNextSendMem();
        }
    }
}
void MAA::sendNextDeferredPacket(Addr paddr) {
    auto deferred_it = my_deferred_pkt_map.find(paddr);
    if (deferred_it == my_deferred_pkt_map.end() ||
        deferred_it->second.empty() ||
        my_outstanding_pkt_map.find(paddr) !=
            my_outstanding_pkt_map.end()) {
        return;
    }

    DeferredPacket deferred = deferred_it->second.front();
    deferred_it->second.pop_front();
    if (deferred_it->second.empty())
        my_deferred_pkt_map.erase(deferred_it);

    if (deferred.logicalResponseManaged) {
        auto *logical_state = dynamic_cast<LogicalSPDTransactionState *>(
            deferred.packet->senderState);
        panic_if(logical_state == nullptr ||
                     logical_state->tag != deferred.logicalTransaction ||
                     logical_state->lineAddress != paddr,
                 "%s: deferred logical stream metadata lost identity at "
                 "0x%lx\n",
                 __func__, paddr);
    }
    DPRINTF(MAAPort, "%s: releasing deferred packet %s at 0x%lx\n",
            __func__, deferred.packet->print(), paddr);
    sendPacket(deferred.funcUnit, deferred.maaID, deferred.packet,
               deferred.tick, deferred.forceCache,
               deferred.forceRetirementCache, true);
}
bool MAA::scheduleNextSendMem() {
    bool return_val = false;
    Tick tick = 0;
    for (int ch = 0; ch < num_channels; ch++) {
        if (mem_channels_blocked[ch])
            continue;
        if (my_outstanding_indirect_mem_read_pkts[ch].empty() == false) {
            if (return_val == false) {
                tick = my_outstanding_indirect_mem_read_pkts[ch].begin()->tick;
                return_val = true;
            } else {
                tick = std::min(tick, my_outstanding_indirect_mem_read_pkts[ch].begin()->tick);
            }
        }
        if (my_outstanding_indirect_mem_write_pkts[ch].empty() == false) {
            if (return_val == false) {
                tick = my_outstanding_indirect_mem_write_pkts[ch].begin()->tick;
                return_val = true;
            } else {
                tick = std::min(tick, my_outstanding_indirect_mem_write_pkts[ch].begin()->tick);
            }
        }
    }
    if (return_val) {
        Cycles latency = Cycles(0);
        if (tick > curTick()) {
            latency = getTicksToCycles(tick - curTick());
        }
        scheduleSendMemEvent(latency);
    }
    return return_val;
}
bool MAA::allIndirectEmpty() {
    for (int ch = 0; ch < num_channels; ch++) {
        if (my_outstanding_indirect_mem_read_pkts[ch].empty() == false)
            return false;
        if (my_outstanding_indirect_mem_write_pkts[ch].empty() == false)
            return false;
    }
    return true;
}
bool MAA::scheduleNextSendCache() {
    bool return_val = false;
    bool all_indirect_empty = allIndirectEmpty();
    Tick tick = 0;
    for (int core_id = 0; core_id < num_cores; core_id++) {
        if (cache_bus_blocked[core_id])
            continue;
        if (my_outstanding_indirect_cache_read_pkts[core_id].empty() == false) {
            if (return_val == false) {
                tick = my_outstanding_indirect_cache_read_pkts[core_id].begin()->tick;
                return_val = true;
            } else {
                tick = std::min(tick, my_outstanding_indirect_cache_read_pkts[core_id].begin()->tick);
            }
        }
        if (my_outstanding_indirect_cache_write_pkts[core_id].empty() == false) {
            if (return_val == false) {
                tick = my_outstanding_indirect_cache_write_pkts[core_id].begin()->tick;
                return_val = true;
            } else {
                tick = std::min(tick, my_outstanding_indirect_cache_write_pkts[core_id].begin()->tick);
            }
        }
        if (my_outstanding_stream_cache_read_pkts[core_id].empty() == false) {
            if (return_val == false) {
                tick = my_outstanding_stream_cache_read_pkts[core_id].begin()->tick;
                return_val = true;
            } else {
                tick = std::min(tick, my_outstanding_stream_cache_read_pkts[core_id].begin()->tick);
            }
        }
        if (my_outstanding_stream_cache_write_pkts[core_id].empty() == false) {
            if (return_val == false) {
                tick = my_outstanding_stream_cache_write_pkts[core_id].begin()->tick;
                return_val = true;
            } else {
                tick = std::min(tick, my_outstanding_stream_cache_write_pkts[core_id].begin()->tick);
            }
        }
        if (all_indirect_empty) {
            if (my_outstanding_stream_mem_read_pkts[core_id].empty() == false) {
                if (return_val == false) {
                    tick = my_outstanding_stream_mem_read_pkts[core_id].begin()->tick;
                    return_val = true;
                } else {
                    tick = std::min(tick, my_outstanding_stream_mem_read_pkts[core_id].begin()->tick);
                }
            }
            if (my_outstanding_stream_mem_write_pkts[core_id].empty() == false) {
                if (return_val == false) {
                    tick = my_outstanding_stream_mem_write_pkts[core_id].begin()->tick;
                    return_val = true;
                } else {
                    tick = std::min(tick, my_outstanding_stream_mem_write_pkts[core_id].begin()->tick);
                }
            }
        }
    }
    if (return_val) {
        Cycles latency = Cycles(0);
        if (tick > curTick()) {
            latency = getTicksToCycles(tick - curTick());
        }
        scheduleSendCacheEvent(latency);
    }
    return return_val;
}
void MAA::unblockMemChannel(int channel_id) {
    panic_if(mem_channels_blocked[channel_id] == false, "%s: channel %d is not blocked!\n", __func__, channel_id);
    mem_channels_blocked[channel_id] = false;
    scheduleNextSendMem();
}
void MAA::unblockCache(int core_id) {
    panic_if(cache_bus_blocked[core_id] == false, "%s: cache %d is not blocked!\n", __func__, core_id);
    cache_bus_blocked[core_id] = false;
    scheduleNextSendCache();
}
bool MAA::allIndirectPacketsSent(int maaID) {
    return my_num_outstanding_indirect_pkts[maaID] == 0;
}
bool MAA::allStreamPacketsSent(int maaID) {
    return my_num_outstanding_stream_pkts[maaID] == 0;
}
bool MAA::sendOutstandingMemPacket() {
    bool packet_remaining = false;
    bool all_empty = true;
    for (int ch = 0; ch < num_channels; ch++) {
        if (mem_channels_blocked[ch])
            continue;
        // RMW/scatter writebacks are generated by completed reads. Prioritize
        // ready indirect reads so writebacks do not starve the work that keeps
        // the MAA pipeline fed; writes still issue as soon as no ready read is
        // pending on this channel.
        for (auto it = my_outstanding_indirect_mem_read_pkts[ch].begin(); it != my_outstanding_indirect_mem_read_pkts[ch].end();) {
            if (it->tick > curTick()) {
                DPRINTF(MAAPort, "%s: waiting for %d cycles to send %s to memory\n", __func__, getTicksToCycles(it->tick - curTick()), it->packet->print());
                packet_remaining = true;
                break;
            }
            DPRINTF(MAAPort, "%s: trying sending %s to memory\n", __func__, it->packet->print());
            if (sendPacketMem(it->packet) == false) {
                DPRINTF(MAAPort, "%s: send failed for channel %d\n", __func__, ch);
                mem_channels_blocked[ch] = true;
                break;
            } else {
                Addr paddr = it->paddr;
                OutstandingPacket tmp = my_outstanding_pkt_map[paddr];
                for (int i = 0; i < tmp.maaIDs.size(); i++) {
                    if (tmp.funcUnits[i] == FuncUnitType::INDIRECT) {
                        my_num_outstanding_indirect_pkts[tmp.maaIDs[i]]--;
                        indirectAccessUnits[tmp.maaIDs[i]].memReadPacketSent(it->paddr);
                    } else if (tmp.funcUnits[i] == FuncUnitType::STREAM) {
                        settleAcceptedStreamReadCounter(
                            my_num_outstanding_stream_pkts[tmp.maaIDs[i]],
                            tmp.logicalResponseManaged, tmp.cmd, paddr);
                        streamAccessUnits[tmp.maaIDs[i]].readPacketSent(
                            it->paddr);
                    } else {
                        panic("Invalid func unit type\n");
                    }
                }
                my_outstanding_pkt_map[paddr].sent = true;
                it = my_outstanding_indirect_mem_read_pkts[ch].erase(it);
                stats.port_mem_RD_packets += 1;
            }
        }
        if (mem_channels_blocked[ch])
            continue;
        // Smart writeback queue: reads already drained above, so this loop only
        // runs in slots where no ready read exists -> overlap is preserved. Among
        // the *ready* writebacks (tick <= now), prefer one whose DRAM row is still
        // open in its bank (the last write to that bank touched the same row) so
        // it issues as a row-buffer hit. Fall back to the oldest ready write when
        // no open-row match exists. This row-groups the write stream without
        // serializing it behind the reads.
        std::multiset<OutstandingPacket, CompareByTick> &wq = my_outstanding_indirect_mem_write_pkts[ch];
        while (wq.empty() == false) {
            auto oldest = wq.begin();
            if (oldest->tick > curTick()) {
                DPRINTF(MAAPort, "%s: waiting for %d cycles to send %s to memory\n", __func__, getTicksToCycles(oldest->tick - curTick()), oldest->packet->print());
                packet_remaining = true;
                break;
            }
            // Scan the ready prefix (ordered by tick) for an open-row hit.
            auto chosen = oldest;
            bool chosen_hit = false;
            for (auto it = wq.begin(); it != wq.end() && it->tick <= curTick(); ++it) {
                uint64_t bank_key;
                Addr row;
                writeRowKey(it->paddr, bank_key, row);
                auto lr = my_writeback_last_row[ch].find(bank_key);
                if (lr != my_writeback_last_row[ch].end() && lr->second == row) {
                    chosen = it;
                    chosen_hit = true;
                    break;
                }
            }
            DPRINTF(MAAPort, "%s: trying sending %s to memory (row-%s)\n", __func__, chosen->packet->print(), chosen_hit ? "hit" : "miss");
            if (sendPacketMem(chosen->packet) == false) {
                DPRINTF(MAAPort, "%s: send failed for channel %d\n", __func__, ch);
                mem_channels_blocked[ch] = true;
                break;
            } else {
                Addr paddr = chosen->paddr;
                panic_if(chosen->packet->needsResponse(), "%s write packet %s needs response!\n", __func__, chosen->packet->print());
                OutstandingPacket tmp = my_outstanding_pkt_map[paddr];
                my_outstanding_pkt_map.erase(paddr);
                panic_if(tmp.maaIDs.size() != 1, "%s multiple write packes coalesced into one!\n", __func__);
                panic_if(tmp.funcUnits[0] != FuncUnitType::INDIRECT, "%s: func unit type %d does not match with %d\n", __func__, func_unit_names[(uint8_t)tmp.funcUnits[0]], func_unit_names[(uint8_t)FuncUnitType::INDIRECT]);
                my_num_outstanding_indirect_pkts[tmp.maaIDs[0]]--;
                sendNextDeferredPacket(paddr);
                indirectAccessUnits[tmp.maaIDs[0]].memWritePacketSent(paddr);
                uint64_t bank_key;
                Addr row;
                writeRowKey(paddr, bank_key, row);
                my_writeback_last_row[ch][bank_key] = row;
                if (chosen_hit)
                    stats.port_mem_WR_rowhit += 1;
                wq.erase(chosen);
                stats.port_mem_WR_packets += 1;
            }
        }
        if (my_outstanding_indirect_mem_read_pkts[ch].empty() == false || my_outstanding_indirect_mem_write_pkts[ch].empty() == false) {
            all_empty = false;
        }
    }
    if (packet_remaining) {
        scheduleNextSendMem();
    }
    if (all_empty) {
        scheduleNextSendCache();
    }
    return true;
}
bool MAA::sendOutstandingCachePacket() {
    bool packet_remaining = false;
    bool all_indirect_empty = allIndirectEmpty();
    for (int core = 0; core < num_cores; core++) {
        if (cache_bus_blocked[core])
            continue;
        // Same policy as memory-side: keep indirect reads ahead of writebacks
        // so write responses do not block the reads that create future work.
        for (auto it = my_outstanding_indirect_cache_read_pkts[core].begin(); it != my_outstanding_indirect_cache_read_pkts[core].end();) {
            if (it->tick > curTick()) {
                DPRINTF(MAAPort, "%s: waiting for %d cycles to send %s to cache\n", __func__, getTicksToCycles(it->tick - curTick()), it->packet->print());
                packet_remaining = true;
                break;
            }
            DPRINTF(MAAPort, "%s: trying sending %s to cache\n", __func__, it->packet->print());
            if (sendPacketCache(it->packet) == false) {
                DPRINTF(MAAPort, "%s: send failed for bus %d\n", __func__, core);
                cache_bus_blocked[core] = true;
                break;
            } else {
                Addr paddr = it->paddr;
                OutstandingPacket tmp = my_outstanding_pkt_map[paddr];
                for (int i = 0; i < tmp.maaIDs.size(); i++) {
                    if (tmp.funcUnits[i] == FuncUnitType::INDIRECT) {
                        my_num_outstanding_indirect_pkts[tmp.maaIDs[i]]--;
                        indirectAccessUnits[tmp.maaIDs[i]].cacheReadPacketSent(
                            it->paddr);
                    } else if (tmp.funcUnits[i] == FuncUnitType::STREAM) {
                        settleAcceptedStreamReadCounter(
                            my_num_outstanding_stream_pkts[tmp.maaIDs[i]],
                            tmp.logicalResponseManaged, tmp.cmd, paddr);
                        streamAccessUnits[tmp.maaIDs[i]].readPacketSent(
                            it->paddr);
                    } else {
                        panic("Invalid func unit type\n");
                    }
                }
                my_outstanding_pkt_map[paddr].sent = true;
                it = my_outstanding_indirect_cache_read_pkts[core].erase(it);
                stats.port_cache_RD_packets += 1;
            }
        }
        if (cache_bus_blocked[core])
            continue;
        for (auto it = my_outstanding_indirect_cache_write_pkts[core].begin(); it != my_outstanding_indirect_cache_write_pkts[core].end();) {
            if (it->tick > curTick()) {
                DPRINTF(MAAPort, "%s: waiting for %d cycles to send %s to cache\n", __func__, getTicksToCycles(it->tick - curTick()), it->packet->print());
                packet_remaining = true;
                break;
            }
            DPRINTF(MAAPort, "%s: trying sending %s to cache\n", __func__, it->packet->print());
            const bool needs_response = it->packet->needsResponse();
            const bool sent = it->virtualRetirement
                ? sendPacketRetirementCache(it->packet)
                : sendPacketCache(it->packet);
            if (sent == false) {
                DPRINTF(MAAPort, "%s: send failed for bus %d\n", __func__, core);
                cache_bus_blocked[core] = true;
                break;
            } else {
                Addr paddr = it->paddr;
                OutstandingPacket tmp = my_outstanding_pkt_map[paddr];
                panic_if(tmp.maaIDs.size() != 1, "%s multiple write packes coalesced into one!\n", __func__);
                panic_if(tmp.funcUnits[0] != FuncUnitType::INDIRECT, "%s: func unit type %d does not match with %d\n", __func__, func_unit_names[(uint8_t)tmp.funcUnits[0]], func_unit_names[(uint8_t)FuncUnitType::INDIRECT]);
                if (needs_response) {
                    my_outstanding_pkt_map[paddr].sent = true;
                } else {
                    my_outstanding_pkt_map.erase(paddr);
                    my_num_outstanding_indirect_pkts[tmp.maaIDs[0]]--;
                    sendNextDeferredPacket(paddr);
                    indirectAccessUnits[tmp.maaIDs[0]].cacheWritePacketSent(it->paddr);
                }
                it = my_outstanding_indirect_cache_write_pkts[core].erase(it);
                stats.port_cache_WR_packets += 1;
            }
        }
        if (cache_bus_blocked[core])
            continue;
        for (auto it = my_outstanding_stream_cache_write_pkts[core].begin(); it != my_outstanding_stream_cache_write_pkts[core].end();) {
            if (it->tick > curTick()) {
                DPRINTF(MAAPort, "%s: waiting for %d cycles to send %s to cache\n", __func__, getTicksToCycles(it->tick - curTick()), it->packet->print());
                packet_remaining = true;
                break;
            }
            DPRINTF(MAAPort, "%s: trying sending %s to cache\n", __func__, it->packet->print());
            const bool needs_response = it->packet->needsResponse();
            const bool sent = it->virtualRetirement
                ? sendPacketRetirementCache(it->packet)
                : sendPacketCache(it->packet);
            if (!sent) {
                DPRINTF(MAAPort, "%s: send failed for bus %d\n", __func__,
                        core);
                cache_bus_blocked[core] = true;
                break;
            } else {
                Addr paddr = it->paddr;
                OutstandingPacket tmp = my_outstanding_pkt_map[paddr];
                panic_if(tmp.maaIDs.size() != 1,
                         "%s multiple write packes coalesced into one!\n",
                         __func__);
                panic_if(tmp.funcUnits[0] != FuncUnitType::STREAM,
                         "%s: func unit type %d does not match with %d\n",
                         __func__,
                         func_unit_names[(uint8_t)tmp.funcUnits[0]],
                         func_unit_names[(uint8_t)FuncUnitType::STREAM]);
                if (tmp.logicalResponseManaged) {
                    panic_if(!needs_response ||
                                 tmp.logicalTransaction.action !=
                                     LogicalStreamAction::Writeback ||
                                 tmp.cmd != MemCmd::WriteReq ||
                                 !tmp.virtualRetirement,
                             "%s: logical stream write at 0x%lx lost its "
                             "response-bearing retirement identity\n",
                             __func__, paddr);
                    applyLogicalStreamCounterEvent(
                        my_num_outstanding_stream_pkts[tmp.maaIDs[0]],
                        tmp.cmd, LogicalStreamCounterEvent::SendAccepted,
                        paddr);
                    my_outstanding_pkt_map[paddr].sent = true;
                } else {
                    panic_if(needs_response,
                             "%s write packet %s needs response!\n",
                             __func__, it->packet->print());
                    my_outstanding_pkt_map.erase(paddr);
                    my_num_outstanding_stream_pkts[tmp.maaIDs[0]]--;
                    sendNextDeferredPacket(paddr);
                    streamAccessUnits[tmp.maaIDs[0]].writePacketSent(
                        it->paddr);
                }
                it = my_outstanding_stream_cache_write_pkts[core].erase(it);
                stats.port_cache_WR_packets += 1;
            }
        }
        if (cache_bus_blocked[core])
            continue;
        for (auto it = my_outstanding_stream_cache_read_pkts[core].begin(); it != my_outstanding_stream_cache_read_pkts[core].end();) {
            if (it->tick > curTick()) {
                DPRINTF(MAAPort, "%s: waiting for %d cycles to send %s to cache\n", __func__, getTicksToCycles(it->tick - curTick()), it->packet->print());
                packet_remaining = true;
                break;
            }
            DPRINTF(MAAPort, "%s: trying sending %s to cache\n", __func__, it->packet->print());
            if (sendPacketCache(it->packet) == false) {
                DPRINTF(MAAPort, "%s: send failed for bus %d\n", __func__, core);
                cache_bus_blocked[core] = true;
                break;
            } else {
                Addr paddr = it->paddr;
                OutstandingPacket tmp = my_outstanding_pkt_map[paddr];
                for (int i = 0; i < tmp.maaIDs.size(); i++) {
                    if (tmp.funcUnits[i] == FuncUnitType::INDIRECT) {
                        my_num_outstanding_indirect_pkts[tmp.maaIDs[i]]--;
                        indirectAccessUnits[tmp.maaIDs[i]].cacheReadPacketSent(it->paddr);
                    } else if (tmp.funcUnits[i] == FuncUnitType::STREAM) {
                        settleAcceptedStreamReadCounter(
                            my_num_outstanding_stream_pkts[tmp.maaIDs[i]],
                            tmp.logicalResponseManaged, tmp.cmd, paddr);
                        streamAccessUnits[tmp.maaIDs[i]].readPacketSent(
                            it->paddr);
                    } else {
                        panic("Invalid func unit type\n");
                    }
                }
                my_outstanding_pkt_map[paddr].sent = true;
                it = my_outstanding_stream_cache_read_pkts[core].erase(it);
                stats.port_cache_RD_packets += 1;
            }
        }
        if (all_indirect_empty) {
            if (cache_bus_blocked[core])
                continue;
            for (auto it = my_outstanding_stream_mem_write_pkts[core].begin(); it != my_outstanding_stream_mem_write_pkts[core].end();) {
                if (it->tick > curTick()) {
                    DPRINTF(MAAPort, "%s: waiting for %d cycles to send %s to cache\n", __func__, getTicksToCycles(it->tick - curTick()), it->packet->print());
                    packet_remaining = true;
                    break;
                }
                DPRINTF(MAAPort, "%s: trying sending %s to cache\n", __func__, it->packet->print());
                if (sendPacketCache(it->packet) == false) {
                    DPRINTF(MAAPort, "%s: send failed for bus %d\n", __func__, core);
                    cache_bus_blocked[core] = true;
                    break;
                } else {
                    Addr paddr = it->paddr;
                    panic_if(it->packet->needsResponse(), "%s write packet %s needs response!\n", __func__, it->packet->print());
                    OutstandingPacket tmp = my_outstanding_pkt_map[paddr];
                    my_outstanding_pkt_map.erase(paddr);
                    panic_if(tmp.maaIDs.size() != 1, "%s multiple write packes coalesced into one!\n", __func__);
                    panic_if(tmp.funcUnits[0] != FuncUnitType::STREAM, "%s: func unit type %d does not match with %d\n", __func__, func_unit_names[(uint8_t)tmp.funcUnits[0]], func_unit_names[(uint8_t)FuncUnitType::STREAM]);
                    my_num_outstanding_stream_pkts[tmp.maaIDs[0]]--;
                    sendNextDeferredPacket(paddr);
                    streamAccessUnits[tmp.maaIDs[0]].writePacketSent(it->paddr);
                    it = my_outstanding_stream_mem_write_pkts[core].erase(it);
                    stats.port_cache_WR_packets += 1;
                }
            }
            if (cache_bus_blocked[core])
                continue;
            for (auto it = my_outstanding_stream_mem_read_pkts[core].begin(); it != my_outstanding_stream_mem_read_pkts[core].end();) {
                if (it->tick > curTick()) {
                    DPRINTF(MAAPort, "%s: waiting for %d cycles to send %s to cache\n", __func__, getTicksToCycles(it->tick - curTick()), it->packet->print());
                    packet_remaining = true;
                    break;
                }
                DPRINTF(MAAPort, "%s: trying sending %s to cache\n", __func__, it->packet->print());
                if (sendPacketCache(it->packet) == false) {
                    DPRINTF(MAAPort, "%s: send failed for bus %d\n", __func__, core);
                    cache_bus_blocked[core] = true;
                    break;
                } else {
                    Addr paddr = it->paddr;
                    OutstandingPacket tmp = my_outstanding_pkt_map[paddr];
                    for (int i = 0; i < tmp.maaIDs.size(); i++) {
                        if (tmp.funcUnits[i] == FuncUnitType::INDIRECT) {
                            my_num_outstanding_indirect_pkts[tmp.maaIDs[i]]--;
                            indirectAccessUnits[tmp.maaIDs[i]]
                                .cacheReadPacketSent(it->paddr);
                        } else if (tmp.funcUnits[i] == FuncUnitType::STREAM) {
                            settleAcceptedStreamReadCounter(
                                my_num_outstanding_stream_pkts[tmp.maaIDs[i]],
                                tmp.logicalResponseManaged, tmp.cmd, paddr);
                            streamAccessUnits[tmp.maaIDs[i]].readPacketSent(
                                it->paddr);
                        } else {
                            panic("Invalid func unit type\n");
                        }
                    }
                    my_outstanding_pkt_map[paddr].sent = true;
                    it = my_outstanding_stream_mem_read_pkts[core].erase(it);
                    stats.port_cache_RD_packets += 1;
                }
            }
        }
    }

    if (packet_remaining) {
        scheduleNextSendCache();
    }
    return true;
}
TimingResponseDisposition
MAA::recvTimingResp(PacketPtr pkt, bool cached)
{
    DPRINTF(MAAPort, "%s: received %s, cmd: %s, size: %d\n", __func__,
            pkt->print(), pkt->cmdString(), pkt->getSize());

    const Addr response_paddr = pkt->req->getPaddr();
    const Addr response_address = pkt->getAddr();
    LogicalStreamResponseKind response_kind =
        LogicalStreamResponseKind::Read;
    const bool response_command_valid =
        decodeLogicalStreamResponseKind(pkt->cmd, response_kind);
    const LogicalSenderStateSearch sender_state_search =
        findLogicalSenderStateBounded(pkt->senderState);
    auto *logical_state = sender_state_search.state;
    const LogicalStreamTransactionTag received_tag =
        logical_state != nullptr ? logical_state->tag
                                 : LogicalStreamTransactionTag{};
    const Addr sender_line_address =
        logical_state != nullptr ? logical_state->lineAddress : 0;

    const auto exact = std::find_if(
        my_outstanding_pkt_map.begin(), my_outstanding_pkt_map.end(),
        [pkt](const auto &entry) { return entry.second.packet == pkt; });
    const auto address_outstanding =
        my_outstanding_pkt_map.find(response_paddr);
    const bool sender_state_release_safe = [&]() {
        if (logical_state == nullptr || pkt->senderState != logical_state ||
            !senderStateChainExcludes(logical_state->predecessor,
                                      logical_state)) {
            return false;
        }
        for (const auto &entry : my_outstanding_pkt_map) {
            if (entry.second.packet != pkt &&
                !senderStateChainExcludes(entry.second.packet->senderState,
                                          logical_state)) {
                return false;
            }
        }
        return true;
    }();
    auto releaseLogicalState = [&]() {
        panic_if(logical_state == nullptr || !sender_state_release_safe,
                 "%s: attempted unsafe logical sender-state release\n",
                 __func__);
        panic_if(pkt->senderState != logical_state,
                 "%s: logical sender state is not response-stack top\n",
                 __func__);
        auto *popped = pkt->popSenderState();
        panic_if(popped != logical_state,
                 "%s: logical sender state changed during response\n",
                 __func__);
        delete popped;
        logical_state = nullptr;
    };

    /*
     * No packet other than the exact map-owned PacketPtr can settle the
     * active address.  A separately owned tagged callback is consumed after
     * releasing its own state; an untagged or aliased extra is fatal because
     * it cannot be safely retried or destroyed as an ordinary duplicate.
     */
    if (exact == my_outstanding_pkt_map.end()) {
        StreamAccessUnit *received_stream = nullptr;
        LogicalStreamResponseResult ledger_result =
            response_command_valid ? LogicalStreamResponseResult::Stale
                                   : LogicalStreamResponseResult::Invalid;
        if (logical_state != nullptr && received_tag.maaID < num_maas) {
            received_stream = &streamAccessUnits[received_tag.maaID];
            if (response_command_valid) {
                ledger_result = received_stream->validateLogicalResponse(
                    received_tag, sender_line_address, response_kind);
            }
        }

        bool address_is_logical = false;
        LogicalStreamTransactionTag expected_tag{};
        Addr outstanding_address = response_paddr;
        LogicalStreamResponseKind expected_kind = response_kind;
        if (address_outstanding != my_outstanding_pkt_map.end()) {
            const OutstandingPacket &address_owner =
                address_outstanding->second;
            outstanding_address = address_outstanding->first;
            address_is_logical = address_owner.logicalResponseManaged;
            expected_tag = address_owner.logicalTransaction;
            if (address_owner.cmd == MemCmd::ReadReq) {
                expected_kind = LogicalStreamResponseKind::Read;
            } else if (address_owner.cmd == MemCmd::ReadExReq) {
                expected_kind = LogicalStreamResponseKind::ReadEx;
            } else if (address_owner.cmd == MemCmd::WriteReq) {
                expected_kind = LogicalStreamResponseKind::Write;
            } else {
                ledger_result = LogicalStreamResponseResult::Invalid;
            }
        }
        const LogicalStreamResponseRoute route = {
            address_outstanding != my_outstanding_pkt_map.end(),
            address_is_logical,
            logical_state != nullptr,
            expected_tag,
            received_tag,
            outstanding_address,
            sender_line_address,
            response_address,
            expected_kind,
            response_kind,
            ledger_result};
        const LogicalStreamResponseDispositionDecision decision =
            classifyLogicalStreamResponseDisposition(
                route, false, sender_state_release_safe);
        if (received_stream != nullptr &&
            decision.result != LogicalStreamResponseResult::Accepted &&
            decision.result != LogicalStreamResponseResult::Completed) {
            received_stream->rejectLogicalResponse(decision.result);
        }
        if (decision.popSenderState)
            releaseLogicalState();
        DPRINTF(MAAPort,
                "%s: non-owning response at 0x%lx classified %d with "
                "disposition %d; active map entry unchanged\n",
                __func__, response_paddr,
                static_cast<int>(decision.result),
                static_cast<int>(decision.disposition));
        return decision.disposition;
    }

    const Addr owned_address = exact->first;
    const OutstandingPacket tmp = exact->second;
    if (tmp.logicalResponseManaged) {
        const bool owner_valid =
            tmp.maaIDs.size() == 1 && tmp.funcUnits.size() == 1 &&
            tmp.funcUnits[0] == FuncUnitType::STREAM &&
            tmp.maaIDs[0] >= 0 && tmp.maaIDs[0] < num_maas;
        StreamAccessUnit *stream =
            owner_valid ? &streamAccessUnits[tmp.maaIDs[0]] : nullptr;
        LogicalStreamResponseKind expected_kind =
            LogicalStreamResponseKind::Read;
        const bool request_command_valid =
            tmp.cmd == MemCmd::ReadReq || tmp.cmd == MemCmd::ReadExReq ||
            tmp.cmd == MemCmd::WriteReq;
        if (tmp.cmd == MemCmd::ReadExReq)
            expected_kind = LogicalStreamResponseKind::ReadEx;
        else if (tmp.cmd == MemCmd::WriteReq)
            expected_kind = LogicalStreamResponseKind::Write;

        LogicalStreamCounterDecision response_counter{};
        bool response_counter_valid = false;
        if (owner_valid && request_command_valid) {
            response_counter = decideLogicalStreamCounterUpdate(
                logicalStreamRequestKind(tmp.cmd),
                LogicalStreamCounterEvent::ResponseAccepted,
                my_num_outstanding_stream_pkts[tmp.maaIDs[0]]);
            response_counter_valid = response_counter.valid;
        }

        const bool response_size_valid =
            response_kind == LogicalStreamResponseKind::Write ||
            pkt->getSize() == 64;
        const bool structure_valid =
            owner_valid && request_command_valid && response_command_valid &&
            response_size_valid && tmp.sent && cached == tmp.cached &&
            tmp.paddr == owned_address && response_paddr == owned_address &&
            response_address == owned_address && logical_state != nullptr &&
            sender_state_release_safe && response_counter_valid;
        LogicalStreamResponseResult ledger_result =
            logical_state == nullptr ? LogicalStreamResponseResult::Stale
                                     : LogicalStreamResponseResult::Invalid;
        if (structure_valid) {
            ledger_result = stream->validateLogicalResponse(
                received_tag, sender_line_address, response_kind);
        }
        const LogicalStreamResponseRoute route = {
            true,
            true,
            logical_state != nullptr,
            tmp.logicalTransaction,
            received_tag,
            owned_address,
            sender_line_address,
            response_address,
            expected_kind,
            response_kind,
            ledger_result};
        LogicalStreamResponseDispositionDecision decision =
            classifyLogicalStreamResponseDisposition(
                route, true, sender_state_release_safe);
        if (!structure_valid && decision.accepts()) {
            decision = {LogicalStreamResponseResult::Invalid,
                        TimingResponseDisposition::FatalOwnedCorruption, true,
                        logical_state != nullptr &&
                            sender_state_release_safe,
                        true};
        }

        if (!decision.accepts()) {
            LogicalStreamResponseResult rejected = decision.result;
            if (rejected == LogicalStreamResponseResult::Accepted ||
                rejected == LogicalStreamResponseResult::Completed) {
                rejected = LogicalStreamResponseResult::Invalid;
            }
            if (stream != nullptr)
                stream->rejectLogicalResponse(rejected);
            if (owner_valid && request_command_valid) {
                const LogicalStreamCounterDecision aborted_counter =
                    decideLogicalStreamCounterUpdate(
                        logicalStreamRequestKind(tmp.cmd),
                        LogicalStreamCounterEvent::ResponseAborted,
                        my_num_outstanding_stream_pkts[tmp.maaIDs[0]]);
                if (aborted_counter.valid) {
                    my_num_outstanding_stream_pkts[tmp.maaIDs[0]] =
                        aborted_counter.value;
                }
            }
            if (decision.popSenderState)
                releaseLogicalState();
            my_outstanding_pkt_map.erase(exact);
            DPRINTF(MAAPort,
                    "%s: exact logical packet at 0x%lx is corrupt (%d); "
                    "removed map ownership before wrapper destruction\n",
                    __func__, owned_address, static_cast<int>(rejected));
            return TimingResponseDisposition::FatalOwnedCorruption;
        }

        my_num_outstanding_stream_pkts[tmp.maaIDs[0]] =
            response_counter.value;
        const LogicalStreamResponseResult accepted =
            tmp.cmd == MemCmd::WriteReq
                ? stream->writeResponseReceived(received_tag,
                                                sender_line_address)
                : stream->logicalResponseReceived(received_tag,
                                                  sender_line_address,
                                                  response_kind);
        bool delivery_valid =
            accepted == LogicalStreamResponseResult::Accepted ||
            accepted == LogicalStreamResponseResult::Completed;
        if (delivery_valid && tmp.cmd != MemCmd::WriteReq) {
            delivery_valid = stream->recvData(response_address,
                                              pkt->getPtr<uint8_t>());
        }
        if (!delivery_valid) {
            releaseLogicalState();
            my_outstanding_pkt_map.erase(exact);
            DPRINTF(MAAPort,
                    "%s: validated logical response at 0x%lx failed owner "
                    "delivery; removed ownership before fatal destruction\n",
                    __func__, owned_address);
            return TimingResponseDisposition::FatalOwnedCorruption;
        }
        releaseLogicalState();
        my_outstanding_pkt_map.erase(exact);
        const LogicalStreamResponseResult controller_result =
            logicalStreamResponseReceived(received_tag,
                                          sender_line_address,
                                          response_kind);
        if (controller_result != LogicalStreamResponseResult::Accepted &&
            controller_result != LogicalStreamResponseResult::Completed) {
            DPRINTF(MAAPort,
                    "%s: retired logical response was rejected by "
                    "controller (%d) after owned-state cleanup\n",
                    __func__, static_cast<int>(controller_result));
            return TimingResponseDisposition::FatalOwnedCorruption;
        }
        sendNextDeferredPacket(owned_address);
        return TimingResponseDisposition::Retired;
    }

    const bool owners_valid =
        tmp.maaIDs.size() == tmp.funcUnits.size() && !tmp.maaIDs.empty() &&
        std::all_of(tmp.maaIDs.begin(), tmp.maaIDs.end(),
                    [this](int maa_id) {
                        return maa_id >= 0 && maa_id < num_maas;
                    }) &&
        std::all_of(tmp.funcUnits.begin(), tmp.funcUnits.end(),
                    [](FuncUnitType unit) {
                        return unit == FuncUnitType::INDIRECT ||
                               unit == FuncUnitType::STREAM;
                    });
    LogicalStreamResponseKind expected_kind =
        LogicalStreamResponseKind::Read;
    bool request_command_valid = true;
    if (tmp.cmd == MemCmd::ReadReq) {
        expected_kind = LogicalStreamResponseKind::Read;
    } else if (tmp.cmd == MemCmd::ReadExReq) {
        expected_kind = LogicalStreamResponseKind::ReadEx;
    } else if (tmp.cmd == MemCmd::WriteReq) {
        expected_kind = LogicalStreamResponseKind::Write;
    } else {
        request_command_valid = false;
    }
    const bool response_size_valid =
        response_kind == LogicalStreamResponseKind::Write ||
        pkt->getSize() == 64;
    const bool write_owners_valid =
        expected_kind != LogicalStreamResponseKind::Write ||
        std::all_of(tmp.funcUnits.begin(), tmp.funcUnits.end(),
                    [](FuncUnitType unit) {
                        return unit == FuncUnitType::INDIRECT;
                    });
    const bool write_counters_valid =
        expected_kind != LogicalStreamResponseKind::Write ||
        (owners_valid &&
         std::all_of(tmp.maaIDs.begin(), tmp.maaIDs.end(),
                     [this](int maa_id) {
                         return my_num_outstanding_indirect_pkts[maa_id] != 0;
                     }));
    const bool normal_valid =
        owners_valid && request_command_valid && response_command_valid &&
        response_size_valid && response_kind == expected_kind && tmp.sent &&
        cached == tmp.cached && tmp.paddr == owned_address &&
        response_paddr == owned_address && response_address == owned_address &&
        sender_state_search.complete && logical_state == nullptr &&
        write_owners_valid &&
        write_counters_valid;
    if (!normal_valid) {
        if (logical_state != nullptr && sender_state_release_safe)
            releaseLogicalState();
        my_outstanding_pkt_map.erase(exact);
        DPRINTF(MAAPort,
                "%s: exact normal packet at 0x%lx is corrupt; removed map "
                "ownership before wrapper destruction\n",
                __func__, owned_address);
        return TimingResponseDisposition::FatalOwnedCorruption;
    }

    bool delivery_valid = true;
    for (std::size_t i = 0; i < tmp.maaIDs.size(); ++i) {
        if (tmp.funcUnits[i] == FuncUnitType::INDIRECT) {
            if (response_kind == LogicalStreamResponseKind::Write) {
                --my_num_outstanding_indirect_pkts[tmp.maaIDs[i]];
                indirectAccessUnits[tmp.maaIDs[i]].retirementWriteComplete(
                    owned_address);
            } else {
                delivery_valid =
                    indirectAccessUnits[tmp.maaIDs[i]].recvData(
                        response_address, pkt->getPtr<uint8_t>(), tmp.cached);
            }
        } else {
            delivery_valid = streamAccessUnits[tmp.maaIDs[i]].recvData(
                response_address, pkt->getPtr<uint8_t>());
        }
        if (!delivery_valid)
            break;
    }
    if (!delivery_valid) {
        my_outstanding_pkt_map.erase(exact);
        DPRINTF(MAAPort,
                "%s: exact normal response at 0x%lx was rejected by its "
                "owner; removed map ownership before fatal destruction\n",
                __func__, owned_address);
        return TimingResponseDisposition::FatalOwnedCorruption;
    }
    my_outstanding_pkt_map.erase(exact);
    sendNextDeferredPacket(owned_address);
    return TimingResponseDisposition::Retired;
}
void MAA::scheduleSendCacheEvent(int latency) {
    DPRINTF(MAAPort, "%s: scheduling send cache packet in the next %d cycles!\n", __func__, latency);
    panic_if(latency < 0, "Negative latency of %d!\n", latency);
    Tick new_when = getClockEdge(Cycles(latency));
    if (!sendCacheEvent.scheduled()) {
        schedule(sendCacheEvent, new_when);
    } else {
        Tick old_when = sendCacheEvent.when();
        DPRINTF(MAAPort, "%s: send cache packet already scheduled for tick %d\n", __func__, old_when);
        if (new_when < old_when) {
            DPRINTF(MAAPort, "%s: rescheduling for tick %d!\n", __func__, new_when);
            reschedule(sendCacheEvent, new_when);
        }
    }
}
void MAA::scheduleSendMemEvent(int latency) {
    DPRINTF(MAAPort, "%s: scheduling send mem packet in the next %d cycles!\n", __func__, latency);
    panic_if(latency < 0, "Negative latency of %d!\n", latency);
    Tick new_when = getClockEdge(Cycles(latency));
    if (!sendMemEvent.scheduled()) {
        schedule(sendMemEvent, new_when);
    } else {
        Tick old_when = sendMemEvent.when();
        DPRINTF(MAAPort, "%s: send mem packet already scheduled for tick %d\n", __func__, old_when);
        if (new_when < old_when) {
            DPRINTF(MAAPort, "%s: rescheduling for tick %d!\n", __func__, new_when);
            reschedule(sendMemEvent, new_when);
        }
    }
}
} // namespace gem5
