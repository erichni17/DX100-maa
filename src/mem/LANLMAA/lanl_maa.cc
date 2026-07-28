#include "mem/LANLMAA/lanl_maa.hh"

#include <algorithm>
#include <cstring>
#include <limits>
#include <memory>

#include "base/logging.hh"
#include "debug/LANLMAA.hh"
#include "mem/packet.hh"
#include "mem/request.hh"
#include "sim/sim_exit.hh"
#include "sim/system.hh"

namespace gem5
{
namespace lanlmaa
{

void
LANLMAA::LineEntry::clear()
{
    state = LineState::Free;
    lineAddress = 0;
    packet = nullptr;
    waiters.clear();
}

LANLMAA::MemoryPort::MemoryPort(const std::string &name, LANLMAA &owner)
    : RequestPort(name), owner(owner)
{
}

bool
LANLMAA::MemoryPort::recvTimingResp(PacketPtr packet)
{
    return owner.receiveTimingResponse(packet);
}

void
LANLMAA::MemoryPort::recvReqRetry()
{
    owner.receiveRequestRetry();
}

LANLMAA::LANLMAAStats::LANLMAAStats(statistics::Group *parent)
    : statistics::Group(parent),
      ADD_STAT(logicalItems, statistics::units::Count::get(),
               "Logical operations admitted"),
      ADD_STAT(logicalMemoryAccesses, statistics::units::Count::get(),
               "Logical record or gather accesses generated"),
      ADD_STAT(physicalLineReads, statistics::units::Count::get(),
               "Coherent line reads accepted by the memory port"),
      ADD_STAT(lineMergeHits, statistics::units::Count::get(),
               "Logical items merged into an allocated line"),
      ADD_STAT(operationWouldBlockCycles, statistics::units::Cycle::get(),
               "Cycles blocked by a full operation window"),
      ADD_STAT(lineWouldBlockCycles, statistics::units::Cycle::get(),
               "Cycles blocked by a full line table"),
      ADD_STAT(contextWouldBlockCycles, statistics::units::Cycle::get(),
               "Cycles blocked by a full continuation table"),
      ADD_STAT(portSendFailures, statistics::units::Count::get(),
               "Timing sends refused by the downstream port"),
      ADD_STAT(portRetryNotifications, statistics::units::Count::get(),
               "Request-retry notifications received"),
      ADD_STAT(responses, statistics::units::Count::get(),
               "Coherent line responses accepted"),
      ADD_STAT(responsesFannedOut, statistics::units::Count::get(),
               "Logical values supplied by line responses"),
      ADD_STAT(completionsRetired, statistics::units::Count::get(),
               "Logical items retired in descriptor order"),
      ADD_STAT(verificationFailures, statistics::units::Count::get(),
               "Retired values that differ from the supplied oracle"),
      ADD_STAT(continuationSteps, statistics::units::Count::get(),
               "Dependent records consumed"),
      ADD_STAT(continuationExhaustions, statistics::units::Count::get(),
               "Cell walks terminated by the maximum-step bound"),
      ADD_STAT(activeContextHighWaterMark, statistics::units::Count::get(),
               "Maximum simultaneously allocated continuation contexts"),
      ADD_STAT(engineCycles, statistics::units::Cycle::get(),
               "Active engine cycles through descriptor completion")
{
}

LANLMAA::LANLMAA(const LANLMAAParams &params)
    : ClockedObject(params),
      addresses(params.addresses),
      expectedValues(params.expected_values),
      dependentMode(params.dependent_mode),
      continuationEntries(params.continuation_entries),
      maxContinuationSteps(params.max_continuation_steps),
      terminalAddress(params.terminal_address),
      operationEntries(params.operation_entries),
      lineEntries(params.line_entries),
      logicalAdmissionWidth(params.logical_admission_width),
      lineIssueWidth(params.line_issue_width),
      retirementWidth(params.retirement_width),
      lineBytes(params.line_bytes),
      startCycle(params.start_cycle),
      exitOnCompletion(params.exit_on_completion),
      requestorId(params.system->getRequestorId(this)),
      memoryPort(name() + ".mem_side", *this),
      tickEvent([this] { tick(); }, name() + ".tick"),
      stats(this),
      operations(addresses.size()),
      lines(lineEntries)
{
    validateConfiguration();
    for (size_t index = 0; index < addresses.size(); ++index) {
        operations[index].address = addresses[index];
        if (!expectedValues.empty()) {
            operations[index].expected = expectedValues[index];
        }
    }
}

void
LANLMAA::validateConfiguration() const
{
    fatal_if(addresses.empty(), "LANLMAA requires at least one address");
    fatal_if(
        !expectedValues.empty() && expectedValues.size() != addresses.size(),
        "LANLMAA expected_values must be empty or match addresses");
    fatal_if(operationEntries == 0,
             "LANLMAA operation_entries must be nonzero");
    fatal_if(dependentMode && continuationEntries == 0,
             "LANLMAA dependent mode requires continuation entries");
    fatal_if(dependentMode && maxContinuationSteps == 0,
             "LANLMAA dependent mode requires a nonzero step bound");
    fatal_if(lineEntries == 0, "LANLMAA line_entries must be nonzero");
    fatal_if(logicalAdmissionWidth == 0,
             "LANLMAA logical_admission_width must be nonzero");
    fatal_if(lineIssueWidth == 0, "LANLMAA line_issue_width must be nonzero");
    fatal_if(retirementWidth == 0, "LANLMAA retirement_width must be nonzero");
    fatal_if(lineBytes != 64, "LANLMAA v0 requires 64-byte lines");
    for (const Addr address : addresses) {
        const size_t accessBytes = dependentMode ? 2 * sizeof(uint64_t) :
                                                   sizeof(uint64_t);
        fatal_if(address % accessBytes != 0,
                 "LANLMAA address does not meet its access alignment");
        fatal_if(address > std::numeric_limits<Addr>::max() - accessBytes,
                 "LANLMAA access address overflows");
        fatal_if(address + accessBytes > lineAddress(address) + lineBytes,
                 "LANLMAA access crosses a coherent line");
    }
}

void
LANLMAA::init()
{
    ClockedObject::init();
    fatal_if(!memoryPort.isConnected(), "LANLMAA mem_side is not connected");
}

void
LANLMAA::startup()
{
    ClockedObject::startup();
    schedule(tickEvent, clockEdge(startCycle));
}

Port &
LANLMAA::getPort(const std::string &ifName, PortID index)
{
    if (ifName == "mem_side") {
        return memoryPort;
    }
    return ClockedObject::getPort(ifName, index);
}

Addr
LANLMAA::lineAddress(Addr address) const
{
    return address & ~(static_cast<Addr>(lineBytes) - 1);
}

LANLMAA::LineEntry *
LANLMAA::matchingLine(Addr address)
{
    auto line = std::find_if(
        lines.begin(), lines.end(), [address](const LineEntry &entry) {
            return entry.state != LineState::Free &&
                   entry.lineAddress == address;
        });
    return line == lines.end() ? nullptr : &*line;
}

LANLMAA::LineEntry *
LANLMAA::freeLine()
{
    auto line = std::find_if(
        lines.begin(), lines.end(), [](const LineEntry &entry) {
            return entry.state == LineState::Free;
        });
    return line == lines.end() ? nullptr : &*line;
}

void
LANLMAA::scheduleTick()
{
    if (!finished && !tickEvent.scheduled()) {
        schedule(tickEvent, clockEdge(Cycles(1)));
    }
}

void
LANLMAA::tick()
{
    ++stats.engineCycles;
    retireOperations();
    admitOperations();
    attachReadyOperations();
    issueLines();
    if (nextRetirement == operations.size()) {
        finish();
        return;
    }
    scheduleTick();
}

void
LANLMAA::retireOperations()
{
    size_t retired = 0;
    while (retired < retirementWidth && nextRetirement < operations.size() &&
           operations[nextRetirement].state == OperationState::RetireReady) {
        auto &operation = operations[nextRetirement];
        if (!expectedValues.empty() &&
            operation.value != operation.expected) {
            ++stats.verificationFailures;
        }
        ++stats.completionsRetired;
        operation.state = OperationState::Unadmitted;
        ++nextRetirement;
        --activeOperations;
        ++retired;
    }
}

void
LANLMAA::admitOperations()
{
    size_t admitted = 0;
    while (admitted < logicalAdmissionWidth &&
           nextAdmission < operations.size()) {
        if (activeOperations == operationEntries) {
            ++stats.operationWouldBlockCycles;
            return;
        }

        if (dependentMode && activeContexts == continuationEntries) {
            ++stats.contextWouldBlockCycles;
            return;
        }

        auto &operation = operations[nextAdmission];
        operation.state = OperationState::AddressReady;
        if (dependentMode) {
            operation.ownsContext = true;
            ++activeContexts;
            if (activeContexts > stats.activeContextHighWaterMark.value()) {
                stats.activeContextHighWaterMark = activeContexts;
            }
        }

        ++stats.logicalItems;
        ++activeOperations;
        ++nextAdmission;
        ++admitted;
    }
}

void
LANLMAA::attachReadyOperations()
{
    size_t attached = 0;
    bool lineBlocked = false;
    for (size_t index = nextRetirement;
         index < nextAdmission && attached < logicalAdmissionWidth; ++index) {
        auto &operation = operations[index];
        if (operation.state != OperationState::AddressReady) {
            continue;
        }

        const Addr aligned = lineAddress(operation.address);
        LineEntry *line = matchingLine(aligned);
        if (line) {
            ++stats.lineMergeHits;
        } else {
            line = freeLine();
            if (!line) {
                lineBlocked = true;
                continue;
            }
            line->state = LineState::Allocated;
            line->lineAddress = aligned;
        }
        line->waiters.push_back(index);
        operation.state = OperationState::DataPending;
        ++stats.logicalMemoryAccesses;
        ++attached;
    }
    if (lineBlocked) {
        ++stats.lineWouldBlockCycles;
    }
}

void
LANLMAA::issueLines()
{
    if (waitingForRetry) {
        return;
    }

    size_t issued = 0;
    for (auto &line : lines) {
        if (issued == lineIssueWidth) {
            return;
        }
        if (line.state != LineState::Allocated) {
            continue;
        }
        if (!line.packet) {
            RequestPtr request = std::make_shared<Request>(
                line.lineAddress, lineBytes, Request::Flags(), requestorId);
            line.packet = new Packet(request, MemCmd::ReadReq);
            line.packet->allocate();
        }
        if (!memoryPort.sendTimingReq(line.packet)) {
            waitingForRetry = true;
            ++stats.portSendFailures;
            return;
        }
        line.state = LineState::InFlight;
        ++stats.physicalLineReads;
        ++issued;
    }
}

bool
LANLMAA::receiveTimingResponse(PacketPtr packet)
{
    panic_if(!packet->isResponse() || !packet->isRead(),
             "LANLMAA received a non-read response");
    LineEntry *line = matchingLine(packet->getAddr());
    panic_if(!line || line->state != LineState::InFlight,
             "LANLMAA response has no in-flight line");
    panic_if(line->packet != packet,
             "LANLMAA response packet does not match its line obligation");

    const uint8_t *data = packet->getConstPtr<uint8_t>();
    for (const size_t operationIndex : line->waiters) {
        auto &operation = operations[operationIndex];
        panic_if(operation.state != OperationState::DataPending,
                 "LANLMAA response waiter is not data-pending");
        const size_t offset = operation.address - line->lineAddress;
        if (dependentMode) {
            uint64_t nextAddress = 0;
            uint64_t payload = 0;
            std::memcpy(&nextAddress, data + offset, sizeof(nextAddress));
            std::memcpy(
                &payload, data + offset + sizeof(nextAddress),
                sizeof(payload));
            operation.value += payload;
            ++operation.continuationSteps;
            ++stats.continuationSteps;

            if (nextAddress == terminalAddress) {
                panic_if(!operation.ownsContext,
                         "LANLMAA terminal operation has no context");
                operation.state = OperationState::RetireReady;
                operation.ownsContext = false;
                --activeContexts;
            } else if (operation.continuationSteps >=
                       maxContinuationSteps) {
                panic_if(!operation.ownsContext,
                         "LANLMAA exhausted operation has no context");
                ++stats.continuationExhaustions;
                operation.state = OperationState::RetireReady;
                operation.ownsContext = false;
                --activeContexts;
            } else {
                const Addr next = static_cast<Addr>(nextAddress);
                constexpr size_t recordBytes = 2 * sizeof(uint64_t);
                panic_if(nextAddress != next,
                         "LANLMAA continuation address does not fit Addr");
                panic_if(next % recordBytes != 0,
                         "LANLMAA continuation address is misaligned");
                panic_if(next > std::numeric_limits<Addr>::max() - recordBytes,
                         "LANLMAA continuation address overflows");
                panic_if(next + recordBytes > lineAddress(next) + lineBytes,
                         "LANLMAA continuation record crosses a line");
                operation.address = next;
                operation.state = OperationState::AddressReady;
            }
        } else {
            std::memcpy(
                &operation.value, data + offset, sizeof(operation.value));
            operation.state = OperationState::RetireReady;
        }
        ++stats.responsesFannedOut;
    }
    ++stats.responses;
    delete packet;
    line->clear();
    scheduleTick();
    return true;
}

void
LANLMAA::receiveRequestRetry()
{
    panic_if(!waitingForRetry,
             "LANLMAA received an unrequested request retry");
    waitingForRetry = false;
    ++stats.portRetryNotifications;
    scheduleTick();
}

void
LANLMAA::finish()
{
    panic_if(activeOperations != 0, "LANLMAA finished with active operations");
    panic_if(activeContexts != 0, "LANLMAA finished with active contexts");
    panic_if(nextAdmission != operations.size(),
             "LANLMAA finished before admitting every item");
    panic_if(
        std::any_of(lines.begin(), lines.end(), [](const LineEntry &line) {
            return line.state != LineState::Free;
        }),
             "LANLMAA finished with allocated line state");
    finished = true;
    DPRINTF(LANLMAA,
            "completed %zu items using %llu physical lines in %llu cycles\n",
            operations.size(), static_cast<unsigned long long>(
                stats.physicalLineReads.value()),
            static_cast<unsigned long long>(stats.engineCycles.value()));
    if (exitOnCompletion) {
        const bool correct = stats.verificationFailures.value() == 0 &&
                             stats.continuationExhaustions.value() == 0;
        const char *successCause = dependentMode ?
            "LANLMAA cell walk complete" : "LANLMAA gather complete";
        const char *failureCause = dependentMode ?
            "LANLMAA cell walk verification failed" :
            "LANLMAA gather verification failed";
        exitSimLoop(correct ? successCause : failureCause, correct ? 0 : 2);
    }
}

} // namespace lanlmaa
} // namespace gem5
