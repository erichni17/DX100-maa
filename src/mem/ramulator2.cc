#include "mem/ramulator2.hh"

#include "base/callback.hh"
#include "base/trace.hh"
#include "debug/Ramulator2.hh"
#include "debug/Drain.hh"
#include "sim/system.hh"

#include <cstdlib>

// spdlog collides with gem5...
#pragma push_macro("warn")
#undef warn

#include "ramulator2/src/base/base.h"
#include "ramulator2/src/base/request.h"
#include "ramulator2/src/base/config.h"
#include "ramulator2/src/frontend/frontend.h"
#include "ramulator2/src/memory_system/memory_system.h"

namespace gem5 {

namespace memory {

static uint64_t
auditSliceLowerBits(uint64_t &addr, int bits)
{
    if (bits <= 0) {
        return 0;
    }
    uint64_t mask = bits >= 64 ? ~0ULL : ((1ULL << bits) - 1);
    uint64_t lbits = addr & mask;
    addr >>= bits;
    return lbits;
}

Ramulator2::Ramulator2(const Params &p) : AbstractMemory(p),
                                          port(name() + ".port", *this),
                                          config_path(p.config_path),
                                          enlarge_buffer_factor(p.enlarge_buffer_factor),
                                          system_id(p.system_id), system_count(p.system_count),
                                          retryReq(false), retryResp(false), startTick(0),
                                          nbrOutstandingReads(0), nbrOutstandingWrites(0),
                                          sendResponseEvent([this] { sendResponse(); }, name()),
                                          tickEvent([this] { tick(); }, name()) {
    DPRINTF(Ramulator2, "Instantiated Ramulator2 \n");

    registerExitCallback([this]() {
        ramulator2_frontend->finalize();
        ramulator2_memorysystem->finalize();
    });
}

void Ramulator2::init() {
    AbstractMemory::init();

    if (!port.isConnected()) {
        fatal("Ramulator2 %s is unconnected!\n", name());
    } else {
        port.sendRangeChange();
    }

    YAML::Node config = Ramulator::Config::parse_config_file(config_path, {});
    int prev_queue_size = config["MemorySystem"]["Controller"]["queue_size"].as<int>();
    int new_queue_size = prev_queue_size * enlarge_buffer_factor;
    printf("Ramulator2 enlarged buffer size by %d from %d to %d\n", enlarge_buffer_factor, prev_queue_size, new_queue_size);
    config["MemorySystem"]["Controller"]["queue_size"] = new_queue_size;
    ramulator2_frontend = Ramulator::Factory::create_frontend(config);
    ramulator2_memorysystem = Ramulator::Factory::create_memory_system(config);

    ramulator2_memorysystem->m_system_id = system_id;
    ramulator2_memorysystem->m_system_count = system_count;

    ramulator2_frontend->connect_memory_system(ramulator2_memorysystem);
    ramulator2_memorysystem->connect_frontend(ramulator2_frontend);

    std::vector<int> audit_org;
    int audit_colBitsIdx = -1;
    ramulator2_memorysystem->getAddrMapData(audit_org, audit_addrBits,
                                            audit_numLevels, audit_txOffset,
                                            audit_colBitsIdx, audit_rowBitsIdx);

    // if (system()->cacheLineSize() != wrapper.burstSize())
    //     fatal("Ramulator2 burst size %d does not match cache line size %d\n",
    //           wrapper.burstSize(), system()->cacheLineSize());
}

void Ramulator2::startup() {
    startTick = curTick();

    // kick off the clock ticks
    schedule(tickEvent, clockEdge());
}

void Ramulator2::resetStats() {
    printf("Resetting ramulator's stats\n");
    ovl_cyclesAny = ovl_cyclesRead = ovl_cyclesWrite = ovl_cyclesBoth = 0;
    ovl_cyclesWriteOnly = ovl_currentWriteOnlyRun = ovl_maxWriteOnlyRun = 0;
    wr_total = wr_transitions = wr_sameRowTransitions = wr_rowRuns = 0;
    wr_currentRowRun = wr_maxRowRun = 0;
    wr_sameCL = wr_plusOneCL = wr_minusOneCL = 0;
    wr_absLe4CL = wr_absLe16CL = wr_absLe64CL = wr_absGt64CL = 0;
    wr_lastCL = wr_lastRowKey = 0;
    wr_haveLast = false;
    wr_uniqueCLs.clear();
    wr_uniqueRows.clear();
    ramulator2_memorysystem->reset_stats();
}
void Ramulator2::preDumpStats() {
    printf("Dumping ramulator's stats\n");
    // read/write overlap audit (ROI-only). both/any = fraction of busy DRAM cycles with reads AND
    // writes concurrently outstanding; both/write = how often writes had a read to overlap with.
    printf("OVERLAP_AUDIT any=%lu read=%lu write=%lu both=%lu  both/any=%.3f both/write=%.3f\n",
           (unsigned long)ovl_cyclesAny, (unsigned long)ovl_cyclesRead,
           (unsigned long)ovl_cyclesWrite, (unsigned long)ovl_cyclesBoth,
           ovl_cyclesAny ? (double)ovl_cyclesBoth / ovl_cyclesAny : 0.0,
           ovl_cyclesWrite ? (double)ovl_cyclesBoth / ovl_cyclesWrite : 0.0);
    printf("WRITE_TAIL_AUDIT write_only=%lu write_only/write=%.3f max_write_only_run=%lu\n",
           (unsigned long)ovl_cyclesWriteOnly,
           ovl_cyclesWrite ? (double)ovl_cyclesWriteOnly / ovl_cyclesWrite : 0.0,
           (unsigned long)ovl_maxWriteOnlyRun);
    printf("WRITE_ADDR_AUDIT writes=%lu unique_cl=%lu unique_rows=%lu transitions=%lu "
           "same_row=%lu same_row/trans=%.3f row_runs=%lu avg_row_run=%.2f max_row_run=%lu "
           "delta0=%lu plus1=%lu minus1=%lu abs_le4=%lu abs_le16=%lu abs_le64=%lu abs_gt64=%lu\n",
           (unsigned long)wr_total,
           (unsigned long)wr_uniqueCLs.size(),
           (unsigned long)wr_uniqueRows.size(),
           (unsigned long)wr_transitions,
           (unsigned long)wr_sameRowTransitions,
           wr_transitions ? (double)wr_sameRowTransitions / wr_transitions : 0.0,
           (unsigned long)wr_rowRuns,
           wr_rowRuns ? (double)wr_total / wr_rowRuns : 0.0,
           (unsigned long)wr_maxRowRun,
           (unsigned long)wr_sameCL,
           (unsigned long)wr_plusOneCL,
           (unsigned long)wr_minusOneCL,
           (unsigned long)wr_absLe4CL,
           (unsigned long)wr_absLe16CL,
           (unsigned long)wr_absLe64CL,
           (unsigned long)wr_absGt64CL);
    ramulator2_memorysystem->dump_stats();
}

void Ramulator2::sendResponse() {
    assert(!retryResp);
    assert(!responseQueue.empty());

    DPRINTF(Ramulator2, "Attempting to send response\n");

    bool success = port.sendTimingResp(responseQueue.front());
    if (success) {
        responseQueue.pop_front();

        DPRINTF(Ramulator2, "Have %d read, %d write, %d responses outstanding\n",
                nbrOutstandingReads, nbrOutstandingWrites,
                responseQueue.size());

        if (!responseQueue.empty() && !sendResponseEvent.scheduled())
            schedule(sendResponseEvent, curTick());

        if (nbrOutstanding() == 0)
            signalDrainDone();
    } else {
        retryResp = true;

        DPRINTF(Ramulator2, "Waiting for response retry\n");

        assert(!sendResponseEvent.scheduled());
    }
}

unsigned int
Ramulator2::nbrOutstanding() const {
    return nbrOutstandingReads + nbrOutstandingWrites + responseQueue.size();
}

void Ramulator2::tick() {
    // Only tick when it's timing mode
    if (system()->isTimingMode()) {
        ramulator2_memorysystem->tick();

        // read/write overlap audit: sample DRAM-request occupancy this cycle
        if (nbrOutstandingReads || nbrOutstandingWrites) ovl_cyclesAny++;
        if (nbrOutstandingReads) ovl_cyclesRead++;
        if (nbrOutstandingWrites) ovl_cyclesWrite++;
        if (nbrOutstandingReads && nbrOutstandingWrites) ovl_cyclesBoth++;
        if (nbrOutstandingWrites && !nbrOutstandingReads) {
            ovl_cyclesWriteOnly++;
            ovl_currentWriteOnlyRun++;
            if (ovl_currentWriteOnlyRun > ovl_maxWriteOnlyRun)
                ovl_maxWriteOnlyRun = ovl_currentWriteOnlyRun;
        } else {
            ovl_currentWriteOnlyRun = 0;
        }

        // is the connected port waiting for a retry, if so check the
        // state and send a retry if conditions have changed
        if (retryReq) {
            retryReq = false;
            port.sendRetryReq();
        }
    }

    schedule(tickEvent, curTick() + ramulator2_memorysystem->get_tCK() * sim_clock::as_float::ns);
}

Tick Ramulator2::recvAtomic(PacketPtr pkt) {
    panic_if(pkt->cacheResponding(), "Should not see packets where cache "
                                     "is responding");

    access(pkt);
    return 50000; // Arbitary latency of 50ns
}

void Ramulator2::recvFunctional(PacketPtr pkt) {
    pkt->pushLabel(name());
    functionalAccess(pkt);

    for (auto i = responseQueue.begin(); i != responseQueue.end(); ++i)
        pkt->trySatisfyFunctional(*i);

    pkt->popLabel();
}

bool Ramulator2::recvTimingReq(PacketPtr pkt) {
    DPRINTF(Ramulator2, "recvTimingReq: request %s addr %#x size %d\n",
            pkt->cmdString(), pkt->getAddr(), pkt->getSize());

    panic_if(pkt->cacheResponding(), "Should not see packets where cache "
                                     "is responding");

    panic_if(!(pkt->isRead() || pkt->isWrite()),
             "Should only see read and writes at memory controller, "
             "saw %s to %#llx\n",
             pkt->cmdString(), pkt->getAddr());

    // we should not get a new request after committing to retry the
    // current one, but unfortunately the CPU violates this rule, so
    // simply ignore it for now
    if (retryReq)
        return false;

    bool enqueue_success = false;
    if (pkt->isRead()) {
        // Generate ramulator READ request and try to send to ramulator's memory system
        enqueue_success = ramulator2_frontend->receive_external_requests(0, pkt->getAddr(), pkt->getRegion(), 0,
                                                                         [this](Ramulator::Request &req) {
                                                                             DPRINTF(Ramulator2, "Read to %ld completed.\n", req.addr);
                                                                             auto &pkt_q = outstandingReads.find(req.addr)->second;
                                                                             PacketPtr pkt = pkt_q.front();
                                                                             pkt_q.pop_front();
                                                                             if (!pkt_q.size())
                                                                                 outstandingReads.erase(req.addr);

                                                                             // added counter to track requests in flight
                                                                             --nbrOutstandingReads;

                                                                             accessAndRespond(pkt);
                                                                         });

        if (enqueue_success) {
            outstandingReads[pkt->getAddr()].push_back(pkt);

            // we count a transaction as outstanding until it has left the
            // queue in the controller, and the response has been sent
            // back, note that this will differ for reads and writes
            ++nbrOutstandingReads;
        } else {
            retryReq = true;
        }
    } else if (pkt->isWrite()) {
        // Generate ramulator WRITE request and try to send to ramulator's memory system
        enqueue_success = ramulator2_frontend->receive_external_requests(1, pkt->getAddr(), pkt->getRegion(), 0,
                                                                         [this](Ramulator::Request &req) {
                                                                             DPRINTF(Ramulator2, "Write to %ld completed.\n", req.addr);
                                                                             auto &pkt_q = outstandingWrites.find(req.addr)->second;
                                                                             PacketPtr pkt = pkt_q.front();
                                                                             pkt_q.pop_front();
                                                                             if (!pkt_q.size())
                                                                                 outstandingWrites.erase(req.addr);

                                                                             // added counter to track requests in flight
                                                                             --nbrOutstandingWrites;

                                                                             accessAndRespond(pkt);
                                                                         });

        if (enqueue_success) {
            auditWriteAddr(pkt->getAddr());
            outstandingWrites[pkt->getAddr()].push_back(pkt);

            ++nbrOutstandingWrites;

            // perform the access for writes
            accessAndRespond(pkt);
        } else {
            retryReq = true;
        }
    } else {
        // keep it simple and just respond if necessary
        accessAndRespond(pkt);
        return true;
    }

    return enqueue_success;
}

uint64_t
Ramulator2::auditRowKey(Addr addr) const
{
    if (audit_numLevels <= 0 || audit_rowBitsIdx < 0 ||
        audit_addrBits.empty()) {
        return addr >> 13;
    }

    uint64_t mapped = addr >> audit_txOffset;
    uint64_t key = auditSliceLowerBits(mapped, audit_addrBits[0]);

    // RoBaRaCoCh consumes column bits before rank/bank-group/bank/row.
    (void)auditSliceLowerBits(mapped, audit_addrBits[audit_numLevels - 1]);
    for (int level = 1; level <= audit_rowBitsIdx; level++) {
        uint64_t field = auditSliceLowerBits(mapped, audit_addrBits[level]);
        key <<= audit_addrBits[level];
        key |= field;
    }
    return key;
}

void
Ramulator2::auditWriteAddr(Addr addr)
{
    const uint64_t cl = addr >> 6;
    const uint64_t rowKey = auditRowKey(addr);

    wr_total++;
    wr_uniqueCLs.insert(cl);
    wr_uniqueRows.insert(rowKey);

    if (!wr_haveLast) {
        wr_haveLast = true;
        wr_lastCL = cl;
        wr_lastRowKey = rowKey;
        wr_rowRuns = 1;
        wr_currentRowRun = 1;
        wr_maxRowRun = 1;
        return;
    }

    wr_transitions++;

    const long long delta = (long long)cl - (long long)wr_lastCL;
    const unsigned long long absDelta = delta < 0 ? (unsigned long long)(-delta) :
                                                    (unsigned long long)delta;
    if (delta == 0)
        wr_sameCL++;
    if (delta == 1)
        wr_plusOneCL++;
    if (delta == -1)
        wr_minusOneCL++;
    if (absDelta <= 4)
        wr_absLe4CL++;
    else if (absDelta <= 16)
        wr_absLe16CL++;
    else if (absDelta <= 64)
        wr_absLe64CL++;
    else
        wr_absGt64CL++;

    if (rowKey == wr_lastRowKey) {
        wr_sameRowTransitions++;
        wr_currentRowRun++;
    } else {
        wr_rowRuns++;
        wr_currentRowRun = 1;
    }
    if (wr_currentRowRun > wr_maxRowRun)
        wr_maxRowRun = wr_currentRowRun;

    wr_lastCL = cl;
    wr_lastRowKey = rowKey;
}

void Ramulator2::recvRespRetry() {
    DPRINTF(Ramulator2, "Retrying\n");

    assert(retryResp);
    retryResp = false;
    sendResponse();
}

void Ramulator2::accessAndRespond(PacketPtr pkt) {
    DPRINTF(Ramulator2, "Access for address %lld\n", pkt->getAddr());

    bool needsResponse = pkt->needsResponse();

    access(pkt);

    // turn packet around to go back to requestor if response expected
    if (needsResponse) {
        // access already turned the packet into a response
        assert(pkt->isResponse());

        // Assume frontend latency = 0
        Tick time = curTick() + pkt->headerDelay + pkt->payloadDelay;
        // Here we reset the timing of the packet before sending it out.
        pkt->headerDelay = pkt->payloadDelay = 0;

        DPRINTF(Ramulator2, "Queuing response for address %lld\n",
                pkt->getAddr());

        // queue it to be sent back
        responseQueue.push_back(pkt);

        // if we are not already waiting for a retry, or are scheduled
        // to send a response, schedule an event
        if (!retryResp && !sendResponseEvent.scheduled())
            schedule(sendResponseEvent, time);
    } else {
        // queue the packet for deletion
        pendingDelete.reset(pkt);
    }
}

Port &
Ramulator2::getPort(const std::string &if_name, PortID idx) {
    if (if_name != "port") {
        return ClockedObject::getPort(if_name, idx);
    } else {
        return port;
    }
}

DrainState
Ramulator2::drain() {
    // check our outstanding reads and writes and if any they need to
    // drain
    return nbrOutstanding() != 0 ? DrainState::Draining : DrainState::Drained;
}

Ramulator2::MemorySystemPort::MemorySystemPort(const std::string &_name,
                                               Ramulator2 &_ramulator2)
    : ResponsePort(_name), ramulator2(_ramulator2) {}

void Ramulator2::getAddrMapData(std::vector<int> &m_org,
                                std::vector<int> &m_addr_bits,
                                int &m_num_levels,
                                int &m_tx_offset,
                                int &m_col_bits_idx,
                                int &m_row_bits_idx) {
    ramulator2_memorysystem->getAddrMapData(m_org,
                                            m_addr_bits,
                                            m_num_levels,
                                            m_tx_offset,
                                            m_col_bits_idx,
                                            m_row_bits_idx);
}

} // namespace memory
} // namespace gem5

#pragma pop_macro("warn")
