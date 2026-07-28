#include "mem/LANLMAA/control_sequencer.hh"

#include <cstring>
#include <limits>
#include <memory>

#include "base/logging.hh"
#include "mem/LANLMAA/Descriptor.hh"
#include "mem/packet.hh"
#include "mem/packet_access.hh"
#include "mem/request.hh"
#include "sim/system.hh"

namespace gem5
{
namespace lanlmaa
{

namespace
{

constexpr Addr ControlStatusOffset = 0x110;
constexpr Addr ControlCompletedSlotOffset = 0x118;
constexpr Addr ControlErrorOffset = 0x120;
constexpr uint64_t StatusBusy = uint64_t{1} << 1;
constexpr uint64_t StatusCompleted = uint64_t{1} << 2;
constexpr uint64_t StatusError = uint64_t{1} << 3;

} // anonymous namespace

LANLMAAControlSequencer::SequencerPort::SequencerPort(
    const std::string &name, LANLMAAControlSequencer &owner)
    : RequestPort(name), owner(owner)
{
}

bool
LANLMAAControlSequencer::SequencerPort::recvTimingResp(PacketPtr packet)
{
    return owner.receiveTimingResponse(packet);
}

void
LANLMAAControlSequencer::SequencerPort::recvReqRetry()
{
    owner.receiveRequestRetry();
}

LANLMAAControlSequencer::SequencerStats::SequencerStats(
    statistics::Group *parent)
    : statistics::Group(parent),
      ADD_STAT(doorbellWritesAccepted, statistics::units::Count::get(),
               "Descriptor doorbell writes accepted by the timing port"),
      ADD_STAT(statusReadsAccepted, statistics::units::Count::get(),
               "Control-status reads accepted by the timing port"),
      ADD_STAT(detailReadsAccepted, statistics::units::Count::get(),
               "Completed-slot or error-code reads accepted"),
      ADD_STAT(responses, statistics::units::Count::get(),
               "Control responses received"),
      ADD_STAT(sendFailures, statistics::units::Count::get(),
               "Control requests rejected by timing backpressure"),
      ADD_STAT(retryNotifications, statistics::units::Count::get(),
               "Control request-retry notifications received"),
      ADD_STAT(retryResubmissions, statistics::units::Count::get(),
               "Retained control packets resubmitted after retry"),
      ADD_STAT(retryAcceptances, statistics::units::Count::get(),
               "Retried control packets accepted without replacement"),
      ADD_STAT(busyObservations, statistics::units::Count::get(),
               "Busy terminal-status polls"),
      ADD_STAT(completedObservations, statistics::units::Count::get(),
               "Completed terminal-status polls"),
      ADD_STAT(errorObservations, statistics::units::Count::get(),
               "Error terminal-status polls"),
      ADD_STAT(completedDetailsValidated, statistics::units::Count::get(),
               "Completed-slot values matched to submitted slots"),
      ADD_STAT(errorDetailsValidated, statistics::units::Count::get(),
               "Error-code values matched to expected failures"),
      ADD_STAT(descriptorsAdvanced, statistics::units::Count::get(),
               "Terminal descriptors validated before sequence advance")
{
}

LANLMAAControlSequencer::LANLMAAControlSequencer(
    const LANLMAAControlSequencerParams &params)
    : ClockedObject(params),
      controlAddr(params.control_addr),
      doorbellSlots(params.doorbell_slots),
      expectedTerminalErrors(params.expected_terminal_errors),
      startCycle(params.start_cycle),
      pollInterval(params.poll_interval),
      requestorId(params.system->getRequestorId(this)),
      port(name() + ".port", *this),
      issueEvent([this] { issue(); }, name() + ".issue"),
      stats(this)
{
    fatal_if(doorbellSlots.empty(),
             "LANLMAA control sequencer requires at least one slot");
    fatal_if(expectedTerminalErrors.size() != doorbellSlots.size(),
             "LANLMAA control sequencer terminal oracles must match slots");
    fatal_if(pollInterval == Cycles(0),
             "LANLMAA control sequencer requires a nonzero poll interval");
    fatal_if(controlAddr >
                 std::numeric_limits<Addr>::max() -
                     ControlErrorOffset - sizeof(uint64_t),
             "LANLMAA control sequencer aperture overflows");
    for (const uint64_t slot : doorbellSlots) {
        fatal_if(slot >
                     (std::numeric_limits<Addr>::max() - controlAddr) /
                         sizeof(uint64_t),
                 "LANLMAA control sequencer doorbell address overflows");
    }
    for (const uint64_t error : expectedTerminalErrors) {
        fatal_if(error > static_cast<uint8_t>(DescriptorError::BadRecordValue),
                 "LANLMAA control sequencer error oracle is invalid");
    }
}

void
LANLMAAControlSequencer::init()
{
    ClockedObject::init();
    fatal_if(!port.isConnected(),
             "LANLMAA control sequencer port is not connected");
}

void
LANLMAAControlSequencer::startup()
{
    ClockedObject::startup();
    schedule(issueEvent, clockEdge(startCycle));
}

Port &
LANLMAAControlSequencer::getPort(const std::string &ifName, PortID index)
{
    if (ifName == "port") {
        return port;
    }
    return ClockedObject::getPort(ifName, index);
}

uint64_t
LANLMAAControlSequencer::currentSlot() const
{
    panic_if(sequenceIndex >= doorbellSlots.size(),
             "LANLMAA control sequencer has no current slot");
    return doorbellSlots[sequenceIndex];
}

uint64_t
LANLMAAControlSequencer::currentExpectedError() const
{
    panic_if(sequenceIndex >= expectedTerminalErrors.size(),
             "LANLMAA control sequencer has no current terminal oracle");
    return expectedTerminalErrors[sequenceIndex];
}

void
LANLMAAControlSequencer::scheduleIssue(Cycles delay)
{
    panic_if(issueEvent.scheduled(),
             "LANLMAA control sequencer issue event is already scheduled");
    schedule(issueEvent, clockEdge(delay));
}

void
LANLMAAControlSequencer::issue()
{
    if (phase == Phase::Done || waitingForRetry) {
        return;
    }
    if (!packet) {
        Addr address = 0;
        MemCmd command = MemCmd::ReadReq;
        switch (phase) {
          case Phase::Doorbell:
            address = controlAddr + currentSlot() * sizeof(uint64_t);
            command = MemCmd::WriteReq;
            break;
          case Phase::Status:
            address = controlAddr + ControlStatusOffset;
            break;
          case Phase::Detail:
            address = controlAddr +
                (currentExpectedError() == 0 ?
                     ControlCompletedSlotOffset : ControlErrorOffset);
            break;
          case Phase::Done:
            panic("LANLMAA control sequencer issued after completion");
        }
        RequestPtr request = std::make_shared<Request>(
            address, sizeof(uint64_t), Request::Flags(), requestorId);
        packet = new Packet(request, command);
        packet->allocate();
        if (phase == Phase::Doorbell) {
            std::memset(packet->getPtr<uint8_t>(), 0, sizeof(uint64_t));
        }
    }

    const bool retryAttempt = retryObligation;
    if (retryAttempt) {
        ++stats.retryResubmissions;
    }
    if (!port.sendTimingReq(packet)) {
        waitingForRetry = true;
        retryObligation = true;
        ++stats.sendFailures;
        return;
    }
    if (retryAttempt) {
        ++stats.retryAcceptances;
    }
    retryObligation = false;
    switch (phase) {
      case Phase::Doorbell:
        ++stats.doorbellWritesAccepted;
        break;
      case Phase::Status:
        ++stats.statusReadsAccepted;
        break;
      case Phase::Detail:
        ++stats.detailReadsAccepted;
        break;
      case Phase::Done:
        panic("LANLMAA control sequencer accepted traffic after completion");
    }
}

bool
LANLMAAControlSequencer::receiveTimingResponse(PacketPtr response)
{
    panic_if(response != packet,
             "LANLMAA sequencer response changed packet ownership");
    panic_if(!response->isResponse(),
             "LANLMAA control sequencer received a request");
    ++stats.responses;

    if (phase == Phase::Doorbell) {
        panic_if(!response->isWrite(),
                 "LANLMAA doorbell did not receive a write response");
        delete response;
        packet = nullptr;
        phase = Phase::Status;
        scheduleIssue(pollInterval);
        return true;
    }

    panic_if(!response->isRead(),
             "LANLMAA status protocol did not receive a read response");
    const uint64_t value = response->getLE<uint64_t>();
    delete response;
    packet = nullptr;

    if (phase == Phase::Status) {
        if (value == StatusBusy) {
            ++stats.busyObservations;
            scheduleIssue(pollInterval);
            return true;
        }
        if (value == StatusCompleted) {
            panic_if(currentExpectedError() != 0,
                     "LANLMAA sequencer expected an error but saw Completed");
            ++stats.completedObservations;
        } else if (value == StatusError) {
            panic_if(currentExpectedError() == 0,
                     "LANLMAA sequencer expected Completed but saw Error");
            ++stats.errorObservations;
        } else {
            panic("LANLMAA sequencer observed invalid status %#llx",
                  static_cast<unsigned long long>(value));
        }
        phase = Phase::Detail;
        scheduleIssue(Cycles(1));
        return true;
    }

    panic_if(phase != Phase::Detail,
             "LANLMAA sequencer received an unexpected read response");
    if (currentExpectedError() == 0) {
        panic_if(value != currentSlot(),
                 "LANLMAA completed-slot response changed slot");
        ++stats.completedDetailsValidated;
    } else {
        panic_if(value != currentExpectedError(),
                 "LANLMAA error response changed the error code");
        ++stats.errorDetailsValidated;
    }
    ++sequenceIndex;
    ++stats.descriptorsAdvanced;
    if (sequenceIndex == doorbellSlots.size()) {
        phase = Phase::Done;
    } else {
        phase = Phase::Doorbell;
        scheduleIssue(Cycles(1));
    }
    return true;
}

void
LANLMAAControlSequencer::receiveRequestRetry()
{
    panic_if(!waitingForRetry || !retryObligation || !packet,
             "LANLMAA sequencer received an unexpected retry");
    waitingForRetry = false;
    ++stats.retryNotifications;
    scheduleIssue(Cycles(1));
}

} // namespace lanlmaa
} // namespace gem5
