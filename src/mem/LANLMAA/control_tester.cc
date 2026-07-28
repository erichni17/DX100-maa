#include "mem/LANLMAA/control_tester.hh"

#include <cstring>
#include <limits>
#include <memory>

#include "base/logging.hh"
#include "mem/packet.hh"
#include "mem/request.hh"
#include "sim/system.hh"

namespace gem5
{
namespace lanlmaa
{

LANLMAAControlTester::TesterPort::TesterPort(
    const std::string &name, LANLMAAControlTester &owner)
    : RequestPort(name), owner(owner)
{
}

bool
LANLMAAControlTester::TesterPort::recvTimingResp(PacketPtr packet)
{
    return owner.receiveTimingResponse(packet);
}

void
LANLMAAControlTester::TesterPort::recvReqRetry()
{
    owner.receiveRequestRetry();
}

LANLMAAControlTester::TesterStats::TesterStats(statistics::Group *parent)
    : statistics::Group(parent),
      ADD_STAT(writesAccepted, statistics::units::Count::get(),
               "MMIO doorbell writes accepted by the upstream port"),
      ADD_STAT(responses, statistics::units::Count::get(),
               "MMIO write responses received"),
      ADD_STAT(sendFailures, statistics::units::Count::get(),
               "MMIO writes rejected by timing backpressure"),
      ADD_STAT(retryNotifications, statistics::units::Count::get(),
               "MMIO request-retry notifications received"),
      ADD_STAT(retryResubmissions, statistics::units::Count::get(),
               "Retained MMIO packets resubmitted after retry")
{
}

LANLMAAControlTester::LANLMAAControlTester(
    const LANLMAAControlTesterParams &params)
    : ClockedObject(params),
      controlAddr(params.control_addr),
      doorbellSlot(params.doorbell_slot),
      writes(params.writes),
      startCycle(params.start_cycle),
      requestorId(params.system->getRequestorId(this)),
      port(name() + ".port", *this),
      issueEvent([this] { issue(); }, name() + ".issue"),
      stats(this)
{
    fatal_if(writes == 0,
             "LANLMAA control tester requires at least one write");
    fatal_if(doorbellSlot >
                 (std::numeric_limits<Addr>::max() - controlAddr) /
                     sizeof(uint64_t),
             "LANLMAA control tester doorbell address overflows");
}

void
LANLMAAControlTester::init()
{
    ClockedObject::init();
    fatal_if(!port.isConnected(),
             "LANLMAA control tester port is not connected");
}

void
LANLMAAControlTester::startup()
{
    ClockedObject::startup();
    schedule(issueEvent, clockEdge(startCycle));
}

Port &
LANLMAAControlTester::getPort(const std::string &ifName, PortID index)
{
    if (ifName == "port") {
        return port;
    }
    return ClockedObject::getPort(ifName, index);
}

void
LANLMAAControlTester::issue()
{
    if (responses == writes || waitingForRetry) {
        return;
    }
    if (!packet) {
        const Addr address = controlAddr +
            doorbellSlot * sizeof(uint64_t);
        RequestPtr request = std::make_shared<Request>(
            address, sizeof(uint64_t), Request::Flags(), requestorId);
        packet = new Packet(request, MemCmd::WriteReq);
        packet->allocate();
        std::memset(packet->getPtr<uint8_t>(), 0, sizeof(uint64_t));
    }
    const bool retry = retryObligation;
    if (retry) {
        ++stats.retryResubmissions;
    }
    if (!port.sendTimingReq(packet)) {
        waitingForRetry = true;
        retryObligation = true;
        ++stats.sendFailures;
        return;
    }
    retryObligation = false;
    ++accepted;
    ++stats.writesAccepted;
}

bool
LANLMAAControlTester::receiveTimingResponse(PacketPtr response)
{
    panic_if(response != packet,
             "LANLMAA control response changed packet ownership");
    panic_if(!response->isResponse() || !response->isWrite(),
             "LANLMAA control tester did not receive a write response");
    delete response;
    packet = nullptr;
    ++responses;
    ++stats.responses;
    if (responses < writes) {
        schedule(issueEvent, clockEdge(Cycles(1)));
    }
    return true;
}

void
LANLMAAControlTester::receiveRequestRetry()
{
    panic_if(!waitingForRetry || !packet,
             "LANLMAA control tester received an unexpected retry");
    waitingForRetry = false;
    ++stats.retryNotifications;
    schedule(issueEvent, clockEdge(Cycles(1)));
}

} // namespace lanlmaa
} // namespace gem5
