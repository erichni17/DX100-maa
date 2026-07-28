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

void
LANLMAA::UpdateEntry::clear()
{
    state = UpdateState::Free;
    address = 0;
    contribution = 0;
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
               "Logical record, gather, or update accesses generated"),
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
      ADD_STAT(retryPacketResubmissions, statistics::units::Count::get(),
               "Exact rejected packets resubmitted after notification"),
      ADD_STAT(retryPacketAcceptances, statistics::units::Count::get(),
               "Previously rejected packets accepted without replacement"),
      ADD_STAT(responses, statistics::units::Count::get(),
               "Coherent read or write responses accepted"),
      ADD_STAT(responsesFannedOut, statistics::units::Count::get(),
               "Logical values supplied by line responses"),
      ADD_STAT(completionsRetired, statistics::units::Count::get(),
               "Logical items retired in descriptor order"),
      ADD_STAT(verificationFailures, statistics::units::Count::get(),
               "Functional values that differ from the supplied oracle"),
      ADD_STAT(continuationSteps, statistics::units::Count::get(),
               "Dependent records consumed"),
      ADD_STAT(continuationExhaustions, statistics::units::Count::get(),
               "Cell walks terminated by the maximum-step bound"),
      ADD_STAT(activeContextHighWaterMark, statistics::units::Count::get(),
               "Maximum simultaneously allocated continuation contexts"),
      ADD_STAT(updateCombinerHits, statistics::units::Count::get(),
               "Logical updates merged into an accumulating entry"),
      ADD_STAT(updateTableWouldBlockCycles, statistics::units::Cycle::get(),
               "Cycles blocked by a full target update bank"),
      ADD_STAT(updateAddressBusyCycles, statistics::units::Cycle::get(),
               "Cycles blocked by an address already draining"),
      ADD_STAT(updateDrains, statistics::units::Count::get(),
               "Update entries promoted to acknowledged drain"),
      ADD_STAT(physicalUpdateReads, statistics::units::Count::get(),
               "Read-modify-write initialization reads accepted"),
      ADD_STAT(physicalUpdateWrites, statistics::units::Count::get(),
               "Combined writes accepted by the memory port"),
      ADD_STAT(writeAcknowledgements, statistics::units::Count::get(),
               "Combined write responses accepted"),
      ADD_STAT(updateOperationsAcknowledged,
               statistics::units::Count::get(),
               "Logical updates released by write acknowledgement"),
      ADD_STAT(verificationReads, statistics::units::Count::get(),
               "Post-drain oracle reads accepted"),
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
      updateMode(params.update_mode),
      updateValues(params.update_values),
      verificationAddresses(params.verification_addresses),
      verificationValues(params.verification_values),
      updateEntryCount(params.update_entries),
      updateBanks(params.update_banks),
      updateIssueWidth(params.update_issue_width),
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
      lines(lineEntries),
      updates(updateEntryCount)
{
    validateConfiguration();
    for (size_t index = 0; index < addresses.size(); ++index) {
        operations[index].address = addresses[index];
        if (updateMode) {
            operations[index].value = updateValues[index];
        }
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
    fatal_if(dependentMode && updateMode,
             "LANLMAA dependent and update modes are mutually exclusive");
    fatal_if(updateMode && !expectedValues.empty(),
             "LANLMAA update mode uses the post-drain verification oracle");
    fatal_if(updateMode && updateValues.size() != addresses.size(),
             "LANLMAA update_values must match update addresses");
    fatal_if(!updateMode && !updateValues.empty(),
             "LANLMAA update_values require update mode");
    fatal_if(
        verificationAddresses.size() != verificationValues.size(),
        "LANLMAA verification addresses and values must match");
    fatal_if(updateMode && verificationAddresses.empty(),
             "LANLMAA update mode requires a post-drain oracle");
    fatal_if(!updateMode && !verificationAddresses.empty(),
             "LANLMAA verification oracle requires update mode");
    fatal_if(updateEntryCount == 0,
             "LANLMAA update_entries must be nonzero");
    const bool invalidUpdateBankGeometry = updateBanks == 0 ||
        (updateBanks != 0 && updateEntryCount % updateBanks != 0);
    fatal_if(invalidUpdateBankGeometry,
             "LANLMAA update entries must divide evenly into nonzero banks");
    fatal_if(updateIssueWidth == 0,
             "LANLMAA update_issue_width must be nonzero");
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
    for (const Addr address : verificationAddresses) {
        fatal_if(address % sizeof(uint64_t) != 0,
                 "LANLMAA verification address must be 64-bit aligned");
        fatal_if(address >
                     std::numeric_limits<Addr>::max() - sizeof(uint64_t),
                 "LANLMAA verification address overflows");
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

size_t
LANLMAA::updateBank(Addr address) const
{
    return (address / sizeof(uint64_t)) % updateBanks;
}

LANLMAA::UpdateEntry *
LANLMAA::matchingUpdate(Addr address)
{
    const size_t ways = updateEntryCount / updateBanks;
    const size_t begin = updateBank(address) * ways;
    for (size_t index = begin; index < begin + ways; ++index) {
        if (updates[index].state != UpdateState::Free &&
            updates[index].address == address) {
            return &updates[index];
        }
    }
    return nullptr;
}

LANLMAA::UpdateEntry *
LANLMAA::freeUpdate(Addr address)
{
    const size_t ways = updateEntryCount / updateBanks;
    const size_t begin = updateBank(address) * ways;
    for (size_t index = begin; index < begin + ways; ++index) {
        if (updates[index].state == UpdateState::Free) {
            return &updates[index];
        }
    }
    return nullptr;
}

LANLMAA::UpdateEntry *
LANLMAA::drainableUpdate(Addr address)
{
    const size_t ways = updateEntryCount / updateBanks;
    const size_t begin = updateBank(address) * ways;
    for (size_t index = begin; index < begin + ways; ++index) {
        if (updates[index].state == UpdateState::Accumulating) {
            return &updates[index];
        }
    }
    return nullptr;
}

LANLMAA::UpdateEntry *
LANLMAA::updateForPacket(PacketPtr packet)
{
    auto entry = std::find_if(
        updates.begin(), updates.end(), [packet](const UpdateEntry &update) {
            return update.state != UpdateState::Free &&
                   update.packet == packet;
        });
    return entry == updates.end() ? nullptr : &*entry;
}

bool
LANLMAA::allUpdateEntriesFree() const
{
    return std::all_of(
        updates.begin(), updates.end(), [](const UpdateEntry &entry) {
            return entry.state == UpdateState::Free;
        });
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
    if (updateMode) {
        attachReadyUpdates();
        scheduleUpdateDrains();
        issueUpdates();
    } else {
        attachReadyOperations();
        issueLines();
    }
    if (nextRetirement == operations.size()) {
        if (!updateMode) {
            finish();
            return;
        }
        if (allUpdateEntriesFree()) {
            issueVerification();
            if (nextVerification == verificationAddresses.size() &&
                verificationPacket == nullptr) {
                finish();
                return;
            }
        }
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
        if (!updateMode && !expectedValues.empty() &&
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
LANLMAA::attachReadyUpdates()
{
    size_t attached = 0;
    bool tableBlocked = false;
    bool addressBusy = false;
    for (size_t index = nextRetirement;
         index < nextAdmission && attached < logicalAdmissionWidth; ++index) {
        auto &operation = operations[index];
        if (operation.state != OperationState::AddressReady) {
            continue;
        }

        UpdateEntry *entry = matchingUpdate(operation.address);
        if (entry && entry->state != UpdateState::Accumulating) {
            addressBusy = true;
            continue;
        }
        if (!entry) {
            entry = freeUpdate(operation.address);
            if (!entry) {
                tableBlocked = true;
                UpdateEntry *victim = drainableUpdate(operation.address);
                if (victim) {
                    victim->state = UpdateState::ReadPending;
                    ++stats.updateDrains;
                }
                continue;
            }
            entry->state = UpdateState::Accumulating;
            entry->address = operation.address;
        } else {
            ++stats.updateCombinerHits;
        }

        entry->contribution += operation.value;
        entry->waiters.push_back(index);
        operation.state = OperationState::UpdatePending;
        ++stats.logicalMemoryAccesses;
        ++attached;
    }
    if (tableBlocked) {
        ++stats.updateTableWouldBlockCycles;
    }
    if (addressBusy) {
        ++stats.updateAddressBusyCycles;
    }
}

void
LANLMAA::scheduleUpdateDrains()
{
    const bool descriptorAttached = nextAdmission == operations.size() &&
        std::none_of(
            operations.begin() + nextRetirement,
            operations.begin() + nextAdmission,
            [](const Operation &operation) {
                return operation.state == OperationState::AddressReady;
            });
    const bool windowMustDrain =
        nextAdmission < operations.size() &&
        activeOperations == operationEntries;
    if (!descriptorAttached && !windowMustDrain) {
        return;
    }

    for (auto &entry : updates) {
        if (entry.state == UpdateState::Accumulating) {
            entry.state = UpdateState::ReadPending;
            ++stats.updateDrains;
        }
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
        if (rejectedPacket && line.packet != rejectedPacket) {
            continue;
        }
        if (!line.packet) {
            RequestPtr request = std::make_shared<Request>(
                line.lineAddress, lineBytes, Request::Flags(), requestorId);
            line.packet = new Packet(request, MemCmd::ReadReq);
            line.packet->allocate();
        }
        const bool retryAttempt = rejectedPacket == line.packet;
        if (retryAttempt) {
            ++stats.retryPacketResubmissions;
        }
        if (!memoryPort.sendTimingReq(line.packet)) {
            if (!rejectedPacket) {
                rejectedPacket = line.packet;
            }
            waitingForRetry = true;
            ++stats.portSendFailures;
            return;
        }
        if (retryAttempt) {
            rejectedPacket = nullptr;
            ++stats.retryPacketAcceptances;
        }
        line.state = LineState::InFlight;
        ++stats.physicalLineReads;
        ++issued;
    }
}

void
LANLMAA::issueUpdates()
{
    if (waitingForRetry) {
        return;
    }

    size_t issued = 0;
    for (auto &entry : updates) {
        if (issued == updateIssueWidth) {
            return;
        }
        const bool read = entry.state == UpdateState::ReadPending;
        const bool write = entry.state == UpdateState::WritePending;
        if (!read && !write) {
            continue;
        }
        if (rejectedPacket && entry.packet != rejectedPacket) {
            continue;
        }
        if (!entry.packet) {
            panic_if(write,
                     "LANLMAA write-pending update has no retained packet");
            RequestPtr request = std::make_shared<Request>(
                entry.address, sizeof(uint64_t), Request::Flags(),
                requestorId);
            entry.packet = new Packet(request, MemCmd::ReadReq);
            entry.packet->allocate();
        }
        const bool retryAttempt = rejectedPacket == entry.packet;
        if (retryAttempt) {
            ++stats.retryPacketResubmissions;
        }
        if (!memoryPort.sendTimingReq(entry.packet)) {
            if (!rejectedPacket) {
                rejectedPacket = entry.packet;
            }
            waitingForRetry = true;
            ++stats.portSendFailures;
            return;
        }
        if (retryAttempt) {
            rejectedPacket = nullptr;
            ++stats.retryPacketAcceptances;
        }
        if (read) {
            entry.state = UpdateState::ReadInFlight;
            ++stats.physicalUpdateReads;
        } else {
            entry.state = UpdateState::WriteInFlight;
            ++stats.physicalUpdateWrites;
        }
        ++issued;
    }
}

void
LANLMAA::issueVerification()
{
    if (verificationInFlight ||
        nextVerification == verificationAddresses.size()) {
        return;
    }
    if (!verificationPacket) {
        RequestPtr request = std::make_shared<Request>(
            verificationAddresses[nextVerification], sizeof(uint64_t),
            Request::Flags(), requestorId);
        verificationPacket = new Packet(request, MemCmd::ReadReq);
        verificationPacket->allocate();
    }
    if (waitingForRetry) {
        return;
    }
    panic_if(rejectedPacket && rejectedPacket != verificationPacket,
             "LANLMAA verification would replace a rejected packet");
    const bool retryAttempt = rejectedPacket == verificationPacket;
    if (retryAttempt) {
        ++stats.retryPacketResubmissions;
    }
    if (!memoryPort.sendTimingReq(verificationPacket)) {
        if (!rejectedPacket) {
            rejectedPacket = verificationPacket;
        }
        waitingForRetry = true;
        ++stats.portSendFailures;
        return;
    }
    if (retryAttempt) {
        rejectedPacket = nullptr;
        ++stats.retryPacketAcceptances;
    }
    verificationInFlight = true;
    ++stats.verificationReads;
}

bool
LANLMAA::receiveTimingResponse(PacketPtr packet)
{
    if (updateMode) {
        if (packet == verificationPacket) {
            return receiveVerificationResponse(packet);
        }
        UpdateEntry *entry = updateForPacket(packet);
        panic_if(!entry,
                 "LANLMAA update response has no retained obligation");
        return receiveUpdateResponse(*entry, packet);
    }

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

bool
LANLMAA::receiveUpdateResponse(UpdateEntry &entry, PacketPtr packet)
{
    panic_if(!packet->isResponse(),
             "LANLMAA received a non-response update packet");
    panic_if(entry.packet != packet,
             "LANLMAA update response changed packet ownership");

    if (entry.state == UpdateState::ReadInFlight) {
        panic_if(!packet->isRead(),
                 "LANLMAA update read received a non-read response");
        uint64_t oldValue = 0;
        std::memcpy(
            &oldValue, packet->getConstPtr<uint8_t>(), sizeof(oldValue));
        const uint64_t combined = oldValue + entry.contribution;
        delete packet;

        RequestPtr request = std::make_shared<Request>(
            entry.address, sizeof(uint64_t), Request::Flags(), requestorId);
        entry.packet = new Packet(request, MemCmd::WriteReq);
        entry.packet->allocate();
        std::memcpy(
            entry.packet->getPtr<uint8_t>(), &combined, sizeof(combined));
        entry.state = UpdateState::WritePending;
        ++stats.responses;
        scheduleTick();
        return true;
    }

    panic_if(entry.state != UpdateState::WriteInFlight,
             "LANLMAA update response is not for an in-flight request");
    panic_if(!packet->isWrite(),
             "LANLMAA update write received a non-write response");
    for (const size_t operationIndex : entry.waiters) {
        auto &operation = operations[operationIndex];
        panic_if(operation.state != OperationState::UpdatePending,
                 "LANLMAA acknowledged update waiter is not pending");
        operation.state = OperationState::RetireReady;
        ++stats.updateOperationsAcknowledged;
    }
    ++stats.writeAcknowledgements;
    ++stats.responses;
    delete packet;
    entry.clear();
    scheduleTick();
    return true;
}

bool
LANLMAA::receiveVerificationResponse(PacketPtr packet)
{
    panic_if(!verificationInFlight || verificationPacket != packet,
             "LANLMAA verification response changed packet ownership");
    panic_if(!packet->isResponse() || !packet->isRead(),
             "LANLMAA verification received a non-read response");
    uint64_t value = 0;
    std::memcpy(&value, packet->getConstPtr<uint8_t>(), sizeof(value));
    if (value != verificationValues[nextVerification]) {
        ++stats.verificationFailures;
    }
    ++stats.responses;
    delete packet;
    verificationPacket = nullptr;
    verificationInFlight = false;
    ++nextVerification;
    scheduleTick();
    return true;
}

void
LANLMAA::receiveRequestRetry()
{
    panic_if(!waitingForRetry || !rejectedPacket,
             "LANLMAA received retry without a retained rejected packet");
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
    panic_if(!allUpdateEntriesFree(),
             "LANLMAA finished with allocated update state");
    panic_if(verificationPacket != nullptr || verificationInFlight,
             "LANLMAA finished with a verification request");
    panic_if(rejectedPacket != nullptr || waitingForRetry,
             "LANLMAA finished with an undischarged retry obligation");
    panic_if(updateMode && nextVerification != verificationAddresses.size(),
             "LANLMAA finished before its update oracle");
    panic_if(
        std::any_of(lines.begin(), lines.end(), [](const LineEntry &line) {
            return line.state != LineState::Free;
        }),
             "LANLMAA finished with allocated line state");
    finished = true;
    DPRINTF(LANLMAA,
            "completed %zu items in %llu cycles\n", operations.size(),
            static_cast<unsigned long long>(stats.engineCycles.value()));
    if (exitOnCompletion) {
        const bool correct = stats.verificationFailures.value() == 0 &&
                             stats.continuationExhaustions.value() == 0;
        const char *successCause = updateMode ? "LANLMAA update complete" :
            dependentMode ? "LANLMAA cell walk complete" :
                            "LANLMAA gather complete";
        const char *failureCause = updateMode ?
            "LANLMAA update verification failed" :
            dependentMode ? "LANLMAA cell walk verification failed" :
                            "LANLMAA gather verification failed";
        exitSimLoop(correct ? successCause : failureCause, correct ? 0 : 2);
    }
}

} // namespace lanlmaa
} // namespace gem5
