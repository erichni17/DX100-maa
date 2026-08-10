#include "mem/MAA/ALU.hh"
#include "mem/MAA/IF.hh"
#include "mem/MAA/IndirectAccess.hh"
#include "mem/MAA/Invalidator.hh"
#include "mem/MAA/RangeFuser.hh"
#include "mem/MAA/SPD.hh"
#include "mem/MAA/StreamAccess.hh"
#include "mem/MAA/MAA.hh"

#include "base/addr_range.hh"
#include "base/logging.hh"
#include "base/trace.hh"
#include "mem/packet.hh"
#include "params/MAA.hh"
#include "debug/MAAPort.hh"
#include "debug/MAACpuPort.hh"
#include "debug/MAACachePort.hh"
#include "debug/MAAMemPort.hh"
#include "debug/MAAController.hh"
#include "sim/cur_tick.hh"
#include <cassert>
#include <cstdint>
#include <string>

#ifndef TRACING_ON
#define TRACING_ON 1
#endif
namespace gem5 {
bool
MAA::canCoalesceOutstandingRead(Addr paddr, FuncUnitType func_unit,
                                int maa_id) const
{
    const auto outstanding = my_outstanding_pkt_map.find(paddr);
    if (outstanding == my_outstanding_pkt_map.end() ||
        outstanding->second.virtualRetirement ||
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
    const bool direct_retirement_owns_address =
        directRetirementOutstandingAddresses.find(paddr) !=
        directRetirementOutstandingAddresses.end();
    if (!bypass_deferred_queue &&
        (has_deferred_packets || retirement_owns_address ||
         direct_retirement_owns_address)) {
        DPRINTF(MAAPort,
                "%s: deferring packet %s behind exact-address retirement "
                "serialization at 0x%lx\n",
                __func__, pkt->print(), paddr);
        my_deferred_pkt_map[paddr].push_back(
            {funcUnit, maaID, pkt, tick, force_cache,
             force_retirement_cache});
        if (retirement_owns_address)
            stats.virtual_retirement_native_deferrals += 1;
        else if (!direct_retirement_owns_address)
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
            my_num_outstanding_stream_pkts[maaID]++;
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
            my_outstanding_pkt_map.end() ||
        directRetirementOutstandingAddresses.find(paddr) !=
            directRetirementOutstandingAddresses.end()) {
        return;
    }

    DeferredPacket deferred = deferred_it->second.front();
    deferred_it->second.pop_front();
    if (deferred_it->second.empty())
        my_deferred_pkt_map.erase(deferred_it);

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
                        my_num_outstanding_stream_pkts[tmp.maaIDs[i]]--;
                        streamAccessUnits[tmp.maaIDs[i]].readPacketSent(it->paddr);
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
                        indirectAccessUnits[tmp.maaIDs[i]].cacheReadPacketSent(it->paddr);
                    } else if (tmp.funcUnits[i] == FuncUnitType::STREAM) {
                        my_num_outstanding_stream_pkts[tmp.maaIDs[i]]--;
                        streamAccessUnits[tmp.maaIDs[i]].readPacketSent(it->paddr);
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
                streamAccessUnits[tmp.maaIDs[0]].writePacketSent(
                    it->paddr, true);
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
                        my_num_outstanding_stream_pkts[tmp.maaIDs[i]]--;
                        streamAccessUnits[tmp.maaIDs[i]].readPacketSent(it->paddr);
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
                    streamAccessUnits[tmp.maaIDs[0]].writePacketSent(
                        it->paddr, true);
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
                            indirectAccessUnits[tmp.maaIDs[i]].cacheReadPacketSent(it->paddr);
                        } else if (tmp.funcUnits[i] == FuncUnitType::STREAM) {
                            my_num_outstanding_stream_pkts[tmp.maaIDs[i]]--;
                            streamAccessUnits[tmp.maaIDs[i]].readPacketSent(it->paddr);
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
void MAA::recvTimingResp(PacketPtr pkt, bool cached) {
    DPRINTF(MAAPort, "%s: received %s, cmd: %s, size: %d\n", __func__, pkt->print(), pkt->cmdString(), pkt->getSize());
    panic_if(pkt->cmd.toInt() != MemCmd::ReadExResp &&
             pkt->cmd.toInt() != MemCmd::ReadResp &&
             pkt->cmd.toInt() != MemCmd::WriteResp,
             "%s received an unknown response: %s\n", __func__, pkt->print());
    if (pkt->cmd != MemCmd::WriteResp)
        assert(pkt->getSize() == 64);
    Addr paddr = pkt->req->getPaddr();
    panic_if(my_outstanding_pkt_map.find(paddr) == my_outstanding_pkt_map.end(), "%s: response for packet %s not found in my_outstanding_pkt_map\n", __func__, pkt->print());
    OutstandingPacket tmp = my_outstanding_pkt_map[paddr];
    panic_if(tmp.sent == false, "%s received response %s for an unsent packet!\n", pkt->cmdString(), pkt->getSize());
    panic_if(cached != tmp.cached, "%s: response %s cached %d does not match with outstanding packet cached %d\n", __func__, pkt->print(), cached, tmp.cached);
    my_outstanding_pkt_map.erase(paddr);
    for (int i = 0; i < tmp.maaIDs.size(); i++) {
        if (tmp.funcUnits[i] == FuncUnitType::INDIRECT) {
            if (pkt->cmd == MemCmd::WriteResp) {
                my_num_outstanding_indirect_pkts[tmp.maaIDs[i]]--;
                sendNextDeferredPacket(paddr);
                indirectAccessUnits[tmp.maaIDs[i]].retirementWriteComplete(paddr);
            } else {
                panic_if(indirectAccessUnits[tmp.maaIDs[i]].recvData(pkt->getAddr(), pkt->getPtr<uint8_t>(), tmp.cached) == false, "%s: received %s but rejected from indirectAccessUnits[%d]\n", __func__, pkt->print(), tmp.maaIDs[i]);
            }
        } else if (tmp.funcUnits[i] == FuncUnitType::STREAM) {
            panic_if(streamAccessUnits[tmp.maaIDs[i]].recvData(pkt->getAddr(), pkt->getPtr<uint8_t>()) == false, "%s: received %s but rejected from streamAccessUnits[%d]\n", __func__, pkt->print(), tmp.maaIDs[i]);
        } else {
            panic("Invalid func unit type\n");
        }
    }
    sendNextDeferredPacket(paddr);
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
