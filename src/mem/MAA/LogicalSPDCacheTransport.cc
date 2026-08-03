#include "mem/MAA/LogicalSPDCacheTransport.hh"

#include <cstring>
#include <exception>

namespace gem5 {

bool
LogicalSPDCacheTransport::TransactionKey::operator==(
    const TransactionKey &other) const
{
    return descriptor == other.descriptor && generation == other.generation &&
           slot == other.slot && page == other.page && line == other.line &&
           operation == other.operation;
}

bool
LogicalSPDCacheTransport::DeliveryTicket::operator==(
    const DeliveryTicket &other) const
{
    return record == other.record && epoch == other.epoch &&
           actionID == other.actionID;
}

bool
LogicalSPDCacheTransport::AuditSnapshot::operator==(
    const AuditSnapshot &other) const
{
    return actionState == other.actionState && actionID == other.actionID &&
           nextLine == other.nextLine && ackCount == other.ackCount &&
           nextActionID == other.nextActionID &&
           nextIncarnationID == other.nextIncarnationID &&
           fifoHead == other.fifoHead && fifoTail == other.fifoTail &&
           fifoCount == other.fifoCount && pending == other.pending &&
           actionIDsExhausted == other.actionIDsExhausted &&
           incarnationIDsExhausted == other.incarnationIDsExhausted &&
           copyActive == other.copyActive && sealed == other.sealed &&
           fifo == other.fifo && credits == other.credits &&
           states == other.states && epochs == other.epochs &&
           recordActionIDs == other.recordActionIDs && keys == other.keys &&
           addresses == other.addresses && requestIDs == other.requestIDs &&
           packetIDs == other.packetIDs &&
           recordCredits == other.recordCredits &&
           keyValid == other.keyValid && requestValid == other.requestValid &&
           packetOwned == other.packetOwned &&
           lineBuffers == other.lineBuffers &&
           issued == other.issued && acked == other.acked;
}

LogicalSPDCacheTransport::LogicalSPDCacheTransport(std::size_t ports,
                                                   std::size_t lineBytes)
    : geometryIsValid(ports == PortCount && lineBytes == LineBytes)
{
    fifo.entries.fill(NoRecord);
    creditOwners.fill(NoRecord);
    for (std::size_t index = 0; index < records.size(); ++index) {
        records[index].token.record = static_cast<uint8_t>(index);
    }
}

LogicalSPDCacheTransport::~LogicalSPDCacheTransport()
{
    // A live response owner must be drained explicitly; destruction cannot
    // guess whether an external peer still owns its request handle.
    if (!drained())
        std::terminate();
}

LogicalSPDCacheTransport::Status
LogicalSPDCacheTransport::publicMutationStatus() const
{
    if (isSealed)
        return Status::Sealed;
    if (deliveryCopyActive)
        return Status::CopyActive;
    return Status::Accepted;
}

uint8_t
LogicalSPDCacheTransport::portForAddress(uint64_t address)
{
    return static_cast<uint8_t>((address >> 6) & (PortCount - 1));
}

LogicalSPDCacheTransport::Command
LogicalSPDCacheTransport::requestCommand(Operation operation)
{
    return operation == Operation::Fill ? Command::ReadReq
                                        : Command::WriteReq;
}

LogicalSPDCacheTransport::Command
LogicalSPDCacheTransport::responseCommand(Operation operation)
{
    return operation == Operation::Fill ? Command::ReadResp
                                        : Command::WriteResp;
}

void
LogicalSPDCacheTransport::setBit(
    std::array<uint64_t, LinesPerPage / 64> &bits, std::size_t line)
{
    bits[line / 64] |= uint64_t{1} << (line % 64);
}

bool
LogicalSPDCacheTransport::getBit(
    const std::array<uint64_t, LinesPerPage / 64> &bits, std::size_t line)
{
    return line < LinesPerPage &&
           (bits[line / 64] & (uint64_t{1} << (line % 64))) != 0;
}

bool
LogicalSPDCacheTransport::allBits(
    const std::array<uint64_t, LinesPerPage / 64> &bits)
{
    for (const uint64_t word : bits) {
        if (word != std::numeric_limits<uint64_t>::max())
            return false;
    }
    return true;
}

bool
LogicalSPDCacheTransport::previewIncarnations(
    uint32_t count, uint32_t &first, uint32_t &committedNext,
    bool &committedExhausted) const
{
    if (count == 0 || incarnationIDsExhausted)
        return false;
    const uint64_t maximum = std::numeric_limits<uint32_t>::max();
    const uint64_t available = maximum - nextIncarnationID + 1;
    if (available < count)
        return false;
    first = nextIncarnationID;
    const uint64_t last = static_cast<uint64_t>(first) + count - 1;
    committedExhausted = last == maximum;
    committedNext = committedExhausted
                        ? nextIncarnationID
                        : static_cast<uint32_t>(last + 1);
    return true;
}

LogicalSPDCacheTransport::Status
LogicalSPDCacheTransport::startAction(
    Operation operation, uint8_t descriptor, uint32_t generation, uint8_t page,
    uint8_t slot, uint64_t baseAddress, PageSpan slotSpan,
    uint32_t *actionID)
{
    const Status mutation = publicMutationStatus();
    if (mutation != Status::Accepted)
        return mutation;
    if (!geometryIsValid)
        return Status::InvalidGeometry;
    if (action.state != ActionState::Free)
        return Status::Busy;
    if (descriptor >= DescriptorCount || generation == 0 ||
        page >= PagesPerDescriptor || slot >= SlotCount ||
        slotSpan.data == nullptr || slotSpan.size != PageBytes ||
        baseAddress % PageBytes != 0 ||
        baseAddress > std::numeric_limits<uint64_t>::max() -
                          (PageBytes - 1)) {
        return Status::Invalid;
    }
    if (actionIDsExhausted)
        return Status::Exhausted;

    uint64_t remainingEpochs = 0;
    for (const TransactionRecord &record : records) {
        remainingEpochs +=
            std::numeric_limits<uint16_t>::max() - record.epoch;
    }
    if (remainingEpochs < LinesPerPage)
        return Status::Exhausted;

    const uint64_t requiredIncarnations = 2 * LinesPerPage;
    const uint64_t availableIncarnations =
        std::numeric_limits<uint32_t>::max() - nextIncarnationID + 1ULL;
    if (incarnationIDsExhausted ||
        availableIncarnations < requiredIncarnations) {
        return Status::Exhausted;
    }

    const uint32_t allocated = nextActionID;
    if (allocated == std::numeric_limits<uint32_t>::max()) {
        actionIDsExhausted = true;
    } else {
        ++nextActionID;
    }
    action = PageAction{};
    action.state = ActionState::Active;
    action.actionID = allocated;
    action.operation = operation;
    action.descriptor = descriptor;
    action.generation = generation;
    action.page = page;
    action.slot = slot;
    action.baseAddress = baseAddress;
    action.slotSpan = slotSpan;
    refillQueue();
    if (actionID != nullptr)
        *actionID = allocated;
    return assertInvariants() ? Status::Accepted : Status::ProductionStop;
}

int
LogicalSPDCacheTransport::allocateRecord(uint32_t actionID)
{
    for (std::size_t index = 0; index < records.size(); ++index) {
        TransactionRecord &record = records[index];
        if (record.state != RecordState::Free ||
            record.epoch == std::numeric_limits<uint16_t>::max()) {
            continue;
        }
        ++record.epoch;
        record.token.record = static_cast<uint8_t>(index);
        record.token.epoch = record.epoch;
        record.token.actionID = actionID;
        return static_cast<int>(index);
    }
    return -1;
}

void
LogicalSPDCacheTransport::fifoPush(uint8_t record)
{
    fifo.entries[fifo.tail] = record;
    fifo.tail = static_cast<uint8_t>((fifo.tail + 1) % FifoEntries);
    ++fifo.count;
}

uint8_t
LogicalSPDCacheTransport::fifoPop()
{
    const uint8_t record = fifo.entries[fifo.head];
    fifo.entries[fifo.head] = NoRecord;
    fifo.head = static_cast<uint8_t>((fifo.head + 1) % FifoEntries);
    --fifo.count;
    return record;
}

void
LogicalSPDCacheTransport::refillQueue()
{
    if (action.state != ActionState::Active)
        return;
    while (action.nextLine < LinesPerPage && fifo.count < FifoEntries) {
        const int allocated = allocateRecord(action.actionID);
        if (allocated < 0)
            return;
        const uint8_t index = static_cast<uint8_t>(allocated);
        TransactionRecord &record = records[index];
        const uint16_t line = action.nextLine;
        record.state = RecordState::Queued;
        record.actionID = action.actionID;
        record.key = {action.descriptor, action.generation, action.slot,
                      action.page, line, action.operation};
        record.keyValid = true;
        record.address = action.baseAddress +
                         static_cast<uint64_t>(line) * LineBytes;
        record.expectedResponse = responseCommand(action.operation);
        record.port = portForAddress(record.address);
        fifoPush(index);
        ++action.nextLine;
        setBit(action.issued, line);
    }
}

int
LogicalSPDCacheTransport::freeCredit() const
{
    for (std::size_t index = 0; index < creditOwners.size(); ++index) {
        if (creditOwners[index] == NoRecord)
            return static_cast<int>(index);
    }
    return -1;
}

LogicalSPDCacheTransport::Result
LogicalSPDCacheTransport::prepare(PageSpan slotSpan, FaultPoint fault)
{
    const Status mutation = publicMutationStatus();
    if (mutation != Status::Accepted)
        return {mutation};
    if (action.state != ActionState::Active)
        return {Status::NoWork};
    if (slotSpan.data != action.slotSpan.data ||
        slotSpan.size != action.slotSpan.size) {
        return {Status::Invalid};
    }
    if (pending != NoRecord) {
        const TransactionRecord &record = records[pending];
        return {record.state == RecordState::WaitRetry
                    ? Status::RetryRequired
                    : Status::Accepted,
                pending, &record.packet, {}};
    }
    if (fifo.count == 0)
        return {Status::NoWork};
    const int availableCredit = freeCredit();
    if (availableCredit < 0)
        return {Status::NoCreditAvailable};

    const uint8_t index = fifo.entries[fifo.head];
    if (index >= RecordCount)
        return {Status::ProductionStop};
    TransactionRecord &record = records[index];
    if (record.state != RecordState::Queued || !record.keyValid)
        return {Status::ProductionStop};

    uint32_t firstID = 0;
    uint32_t committedNext = 0;
    bool committedExhausted = false;
    if (!previewIncarnations(2, firstID, committedNext,
                             committedExhausted)) {
        return {Status::Exhausted};
    }
    if (fault == FaultPoint::RequestIdentity)
        return {Status::FaultInjected};

    RequestIdentity request{firstID};
    std::array<std::byte, LineBytes> snapshot{};
    if (record.key.operation == Operation::Writeback) {
        const std::size_t offset =
            static_cast<std::size_t>(record.key.line) * LineBytes;
        std::memcpy(snapshot.data(), slotSpan.data + offset, LineBytes);
    }
    if (fault == FaultPoint::LineSnapshot)
        return {Status::FaultInjected};

    RequestPacket packet{};
    packet.incarnation = firstID + 1;
    packet.token = &record.token;
    packet.tokenDepth = 1;
    packet.callbackPort = record.port;
    packet.address = record.address;
    packet.command = requestCommand(record.key.operation);
    packet.size = LineBytes;
    if (fault == FaultPoint::RequestPacket)
        return {Status::FaultInjected};

    nextIncarnationID = committedNext;
    incarnationIDsExhausted = committedExhausted;
    const uint8_t credit = static_cast<uint8_t>(availableCredit);
    creditOwners[credit] = index;
    if (fifoPop() != index)
        return {Status::ProductionStop};
    record.request = request;
    record.requestValid = true;
    lineBuffers[credit] = snapshot;
    record.packet = packet;
    record.packet.request = &record.request;
    if (record.key.operation == Operation::Writeback) {
        record.packet.data = lineBuffers[credit].data();
        record.packet.dataSize = LineBytes;
    }
    record.credit = credit;
    record.packetOwned = true;
    record.state = RecordState::PendingSend;
    pending = index;
    if (!assertInvariants())
        return {Status::ProductionStop};
    return {Status::Accepted, index, &record.packet, {}};
}

LogicalSPDCacheTransport::Result
LogicalSPDCacheTransport::sendPrepared(bool accepted)
{
    const Status mutation = publicMutationStatus();
    if (mutation != Status::Accepted)
        return {mutation};
    if (pending == NoRecord)
        return {Status::NoWork};
    TransactionRecord &record = records[pending];
    if (record.state == RecordState::WaitRetry)
        return {Status::RetryRequired, pending, &record.packet, {}};
    if (record.state != RecordState::PendingSend || !record.packetOwned)
        return {Status::ProductionStop};
    const uint8_t index = pending;
    const RequestPacket *handle = &record.packet;
    if (!accepted) {
        record.state = RecordState::WaitRetry;
        return {assertInvariants() ? Status::SendRefused
                                  : Status::ProductionStop,
                index, handle, {}};
    }
    record.packetOwned = false;
    record.state = RecordState::InFlight;
    pending = NoRecord;
    return {assertInvariants() ? Status::SendAccepted
                              : Status::ProductionStop,
            index, handle, {}};
}

LogicalSPDCacheTransport::Result
LogicalSPDCacheTransport::trySend(bool accepted, PageSpan slotSpan,
                                  FaultPoint fault)
{
    const Result prepared = prepare(slotSpan, fault);
    if (prepared.status != Status::Accepted)
        return prepared;
    return sendPrepared(accepted);
}

LogicalSPDCacheTransport::Status
LogicalSPDCacheTransport::recvReqRetry(uint8_t callbackPort)
{
    const Status mutation = publicMutationStatus();
    if (mutation != Status::Accepted)
        return mutation;
    if (pending == NoRecord)
        return Status::Stale;
    TransactionRecord &record = records[pending];
    if (record.state != RecordState::WaitRetry)
        return Status::Stale;
    if (record.port != callbackPort)
        return Status::WrongRetryPort;
    record.state = RecordState::PendingSend;
    return assertInvariants() ? Status::Accepted : Status::ProductionStop;
}

int
LogicalSPDCacheTransport::lookupToken(const ReturnedHandle &returned) const
{
    if (returned.disposed || returned.tokenDepth != 1 ||
        returned.token == nullptr) {
        return -1;
    }
    int match = -1;
    for (std::size_t index = 0; index < records.size(); ++index) {
        const TransactionRecord &record = records[index];
        if ((record.state == RecordState::InFlight ||
             record.state == RecordState::Delivering ||
             record.state == RecordState::AbortDrain) &&
            returned.token == &record.token) {
            if (match != -1)
                return -1;
            match = static_cast<int>(index);
        }
    }
    if (match < 0)
        return -1;
    const TransactionRecord &record = records[static_cast<std::size_t>(match)];
    if (returned.tokenRecord != static_cast<uint8_t>(match) ||
        returned.tokenEpoch != record.epoch ||
        returned.tokenActionID != record.actionID ||
        record.token.record != static_cast<uint8_t>(match) ||
        record.token.epoch != record.epoch ||
        record.token.actionID != record.actionID) {
        return -1;
    }
    return match;
}

bool
LogicalSPDCacheTransport::wireExact(
    const TransactionRecord &record, const ReturnedHandle &returned) const
{
    if (!record.keyValid || !record.requestValid ||
        returned.request != &record.request ||
        returned.requestIncarnation != record.request.incarnation ||
        returned.address != record.address || returned.size != LineBytes) {
        return false;
    }
    if (record.key.operation == Operation::Fill) {
        return (returned.command == Command::ReadResp ||
                returned.command == Command::ReadRespWithInvalidate) &&
               returned.data != nullptr && returned.dataSize == LineBytes;
    }
    const bool payloadShape =
        (returned.data == nullptr && returned.dataSize == 0) ||
        (record.credit < ResponseCredits &&
         returned.data == lineBuffers[record.credit].data() &&
         returned.dataSize == LineBytes);
    return returned.command == Command::WriteResp && payloadShape;
}

LogicalSPDCacheTransport::Result
LogicalSPDCacheTransport::receive(ReturnedHandle &returned,
                                  uint8_t callbackPort)
{
    const Status mutation = publicMutationStatus();
    if (mutation != Status::Accepted)
        return {mutation};
    const int found = lookupToken(returned);
    if (found < 0)
        return {Status::ProductionStop};
    const uint8_t index = static_cast<uint8_t>(found);
    TransactionRecord &record = records[index];
    if (callbackPort != record.port ||
        callbackPort != portForAddress(record.address) ||
        !wireExact(record, returned)) {
        return {Status::ProductionStop};
    }

    if (record.state == RecordState::AbortDrain) {
        returned.disposed = true;
        releaseRecord(index);
        const bool complete = finishAbortIfDrained();
        if (!assertInvariants())
            return {Status::ProductionStop};
        return {complete ? Status::AbortDrained
                         : Status::AbortOwnerDrained,
                index, nullptr, {}};
    }
    if (record.state != RecordState::InFlight)
        return {Status::ProductionStop};
    if (getBit(action.acked, record.key.line))
        return {Status::ProductionStop};

    if (record.key.operation == Operation::Fill) {
        if (record.credit >= ResponseCredits)
            return {Status::ProductionStop};
        std::memcpy(lineBuffers[record.credit].data(), returned.data,
                    LineBytes);
        record.state = RecordState::Delivering;
        returned.disposed = true;
        const DeliveryTicket ticket{index, record.epoch, record.actionID};
        if (!assertInvariants())
            return {Status::ProductionStop};
        return {Status::DeliveryPending, index, nullptr, ticket};
    }

    returned.disposed = true;
    const Status status = ackReleaseAndRefill(index);
    return {status, index, nullptr, {}};
}

bool
LogicalSPDCacheTransport::ticketExact(const DeliveryTicket &ticket,
                                      uint8_t &recordIndex) const
{
    if (ticket.record >= RecordCount)
        return false;
    const TransactionRecord &record = records[ticket.record];
    if (record.state != RecordState::Delivering ||
        record.epoch != ticket.epoch ||
        record.actionID != ticket.actionID || !record.keyValid ||
        record.key.operation != Operation::Fill ||
        action.state != ActionState::Active ||
        action.actionID != ticket.actionID) {
        return false;
    }
    recordIndex = ticket.record;
    return true;
}

LogicalSPDCacheTransport::Status
LogicalSPDCacheTransport::commitDelivery(const DeliveryTicket &ticket,
                                         PageSpan destination, CopyHook hook,
                                         void *context)
{
    const Status mutation = publicMutationStatus();
    if (mutation != Status::Accepted)
        return mutation;
    uint8_t index = NoRecord;
    if (!ticketExact(ticket, index))
        return Status::ProductionStop;
    if (destination.data != action.slotSpan.data ||
        destination.size != PageBytes) {
        return Status::ProductionStop;
    }
    TransactionRecord &record = records[index];
    const std::size_t offset =
        static_cast<std::size_t>(record.key.line) * LineBytes;
    if (offset > PageBytes - LineBytes)
        return Status::ProductionStop;

    deliveryCopyActive = true;
    if (hook != nullptr && !hook(context)) {
        deliveryCopyActive = false;
        return assertInvariants() ? Status::CopyFailed
                                  : Status::ProductionStop;
    }
    if (record.credit >= ResponseCredits)
        return Status::ProductionStop;
    std::memcpy(destination.data + offset,
                lineBuffers[record.credit].data(),
                LineBytes);
    deliveryCopyActive = false;
    return ackReleaseAndRefill(index);
}

void
LogicalSPDCacheTransport::releaseRecord(uint8_t index)
{
    TransactionRecord &record = records[index];
    if (record.credit != NoCredit && record.credit < ResponseCredits &&
        creditOwners[record.credit] == index) {
        lineBuffers[record.credit].fill(std::byte{0});
        creditOwners[record.credit] = NoRecord;
    }
    const uint16_t epoch = record.epoch;
    record = TransactionRecord{};
    record.epoch = epoch;
    record.token.record = index;
    record.token.epoch = epoch;
}

bool
LogicalSPDCacheTransport::actionHasRecords() const
{
    if (action.state == ActionState::Free)
        return false;
    for (const TransactionRecord &record : records) {
        if (record.state != RecordState::Free &&
            record.actionID == action.actionID) {
            return true;
        }
    }
    return false;
}

LogicalSPDCacheTransport::Status
LogicalSPDCacheTransport::ackReleaseAndRefill(uint8_t index)
{
    TransactionRecord &record = records[index];
    if (!record.keyValid || action.state != ActionState::Active ||
        record.actionID != action.actionID ||
        getBit(action.acked, record.key.line)) {
        return Status::ProductionStop;
    }
    setBit(action.acked, record.key.line);
    ++action.ackCount;
    releaseRecord(index);
    refillQueue();
    const bool complete =
        action.nextLine == LinesPerPage && action.ackCount == LinesPerPage &&
        allBits(action.issued) && action.acked == action.issued &&
        fifo.count == 0 && pending == NoRecord && !actionHasRecords();
    if (complete)
        action = PageAction{};
    if (!assertInvariants())
        return Status::ProductionStop;
    return complete ? Status::Completed : Status::Accepted;
}

bool
LogicalSPDCacheTransport::finishAbortIfDrained()
{
    if (action.state == ActionState::AbortDrain && !actionHasRecords()) {
        action = PageAction{};
        return true;
    }
    return false;
}

LogicalSPDCacheTransport::Status
LogicalSPDCacheTransport::abortAction(AbortCode code)
{
    const Status mutation = publicMutationStatus();
    if (mutation != Status::Accepted)
        return mutation;
    if (code == AbortCode::None)
        return Status::Invalid;
    if (action.state == ActionState::Free)
        return Status::AlreadyDrained;
    action.state = ActionState::AbortDrain;
    action.abortCode = code;
    for (std::size_t index = 0; index < records.size(); ++index) {
        TransactionRecord &record = records[index];
        if (record.actionID != action.actionID)
            continue;
        switch (record.state) {
          case RecordState::Queued:
          case RecordState::PendingSend:
          case RecordState::WaitRetry:
          case RecordState::Delivering:
            if (pending == index)
                pending = NoRecord;
            releaseRecord(static_cast<uint8_t>(index));
            break;
          case RecordState::InFlight:
            record.state = RecordState::AbortDrain;
            break;
          case RecordState::AbortDrain:
          case RecordState::Free:
            break;
        }
    }
    fifo.entries.fill(NoRecord);
    fifo.head = 0;
    fifo.tail = 0;
    fifo.count = 0;
    const bool complete = finishAbortIfDrained();
    if (!assertInvariants())
        return Status::ProductionStop;
    return complete ? Status::AbortDrained : Status::Accepted;
}

bool
LogicalSPDCacheTransport::drained() const
{
    if (deliveryCopyActive || action.state != ActionState::Free ||
        fifo.count != 0 || pending != NoRecord)
        return false;
    for (const TransactionRecord &record : records) {
        if (record.state != RecordState::Free)
            return false;
    }
    for (const uint8_t owner : creditOwners) {
        if (owner != NoRecord)
            return false;
    }
    return true;
}

LogicalSPDCacheTransport::Status
LogicalSPDCacheTransport::reset()
{
    const Status mutation = publicMutationStatus();
    if (mutation != Status::Accepted)
        return mutation;
    if (!drained())
        return Status::Busy;
    return Status::Accepted;
}

LogicalSPDCacheTransport::Status
LogicalSPDCacheTransport::seal()
{
    const Status mutation = publicMutationStatus();
    if (mutation != Status::Accepted)
        return mutation;
    if (!drained())
        return Status::Busy;
    isSealed = true;
    return Status::Accepted;
}

std::size_t
LogicalSPDCacheTransport::creditsInUse() const
{
    std::size_t count = 0;
    for (const uint8_t owner : creditOwners)
        count += owner == NoRecord ? 0 : 1;
    return count;
}

LogicalSPDCacheTransport::RecordState
LogicalSPDCacheTransport::recordState(std::size_t record) const
{
    return record < RecordCount ? records[record].state : RecordState::Free;
}

LogicalSPDCacheTransport::TransactionKey
LogicalSPDCacheTransport::recordKey(std::size_t record) const
{
    return record < RecordCount && records[record].keyValid
               ? records[record].key
               : TransactionKey{};
}

uint16_t
LogicalSPDCacheTransport::recordEpoch(std::size_t record) const
{
    return record < RecordCount ? records[record].epoch : 0;
}

const LogicalSPDCacheTransport::RouteToken *
LogicalSPDCacheTransport::recordToken(std::size_t record) const
{
    return record < RecordCount ? &records[record].token : nullptr;
}

const LogicalSPDCacheTransport::RequestIdentity *
LogicalSPDCacheTransport::recordRequest(std::size_t record) const
{
    return record < RecordCount && records[record].requestValid
               ? &records[record].request
               : nullptr;
}

const std::byte *
LogicalSPDCacheTransport::recordLineBuffer(std::size_t record) const
{
    return record < RecordCount &&
                   records[record].credit < ResponseCredits
               ? lineBuffers[records[record].credit].data()
               : nullptr;
}

const LogicalSPDCacheTransport::RequestPacket *
LogicalSPDCacheTransport::pendingHandle() const
{
    return pending < RecordCount ? &records[pending].packet : nullptr;
}

bool
LogicalSPDCacheTransport::lineIssued(std::size_t line) const
{
    return getBit(action.issued, line);
}

bool
LogicalSPDCacheTransport::lineAcked(std::size_t line) const
{
    return getBit(action.acked, line);
}

bool
LogicalSPDCacheTransport::issuedSetComplete() const
{
    return allBits(action.issued);
}

bool
LogicalSPDCacheTransport::ackSetComplete() const
{
    return allBits(action.acked);
}

LogicalSPDCacheTransport::AuditSnapshot
LogicalSPDCacheTransport::auditSnapshot() const
{
    AuditSnapshot snapshot;
    snapshot.actionState = action.state;
    snapshot.actionID = action.actionID;
    snapshot.nextLine = action.nextLine;
    snapshot.ackCount = action.ackCount;
    snapshot.nextActionID = nextActionID;
    snapshot.nextIncarnationID = nextIncarnationID;
    snapshot.fifoHead = fifo.head;
    snapshot.fifoTail = fifo.tail;
    snapshot.fifoCount = fifo.count;
    snapshot.pending = pending;
    snapshot.actionIDsExhausted = actionIDsExhausted;
    snapshot.incarnationIDsExhausted = incarnationIDsExhausted;
    snapshot.copyActive = deliveryCopyActive;
    snapshot.sealed = isSealed;
    snapshot.fifo = fifo.entries;
    snapshot.credits = creditOwners;
    snapshot.issued = action.issued;
    snapshot.acked = action.acked;
    for (std::size_t index = 0; index < records.size(); ++index) {
        snapshot.states[index] = records[index].state;
        snapshot.epochs[index] = records[index].epoch;
        snapshot.recordActionIDs[index] = records[index].actionID;
        snapshot.keys[index] = records[index].key;
        snapshot.addresses[index] = records[index].address;
        snapshot.requestIDs[index] = records[index].request.incarnation;
        snapshot.packetIDs[index] = records[index].packet.incarnation;
        snapshot.recordCredits[index] = records[index].credit;
        snapshot.keyValid[index] = records[index].keyValid;
        snapshot.requestValid[index] = records[index].requestValid;
        snapshot.packetOwned[index] = records[index].packetOwned;
    }
    snapshot.lineBuffers = lineBuffers;
    return snapshot;
}

bool
LogicalSPDCacheTransport::assertInvariants() const
{
    if (fifo.count > FifoEntries || fifo.head >= FifoEntries ||
        fifo.tail >= FifoEntries)
        return false;
    std::array<bool, RecordCount> queued{};
    for (std::size_t offset = 0; offset < fifo.count; ++offset) {
        const uint8_t index =
            fifo.entries[(fifo.head + offset) % FifoEntries];
        if (index >= RecordCount || queued[index])
            return false;
        queued[index] = true;
    }
    std::size_t bufferOwners = 0;
    for (std::size_t index = 0; index < records.size(); ++index) {
        const TransactionRecord &record = records[index];
        std::size_t creditMatches = 0;
        for (const uint8_t owner : creditOwners)
            creditMatches += owner == index ? 1 : 0;
        const bool isPending = pending == index;
        if (record.token.record != index || record.token.epoch != record.epoch)
            return false;
        switch (record.state) {
          case RecordState::Free:
            if (queued[index] || isPending || creditMatches != 0 ||
                record.keyValid || record.requestValid ||
                record.packetOwned)
                return false;
            break;
          case RecordState::Queued:
            if (!queued[index] || isPending || creditMatches != 0 ||
                !record.keyValid || record.requestValid ||
                record.packetOwned)
                return false;
            break;
          case RecordState::PendingSend:
          case RecordState::WaitRetry:
            if (queued[index] || !isPending || creditMatches != 1 ||
                !record.keyValid || !record.requestValid ||
                !record.packetOwned || record.credit >= ResponseCredits ||
                record.packet.request != &record.request ||
                record.packet.token != &record.token ||
                record.packet.tokenDepth != 1 ||
                record.packet.callbackPort != record.port ||
                record.packet.address != record.address ||
                record.packet.command !=
                    requestCommand(record.key.operation) ||
                record.packet.size != LineBytes ||
                (record.key.operation == Operation::Fill &&
                 (record.packet.data != nullptr ||
                  record.packet.dataSize != 0)) ||
                (record.key.operation == Operation::Writeback &&
                 (record.packet.data != lineBuffers[record.credit].data() ||
                  record.packet.dataSize != LineBytes)))
                return false;
            ++bufferOwners;
            break;
          case RecordState::InFlight:
          case RecordState::Delivering:
          case RecordState::AbortDrain:
            if (queued[index] || isPending || creditMatches != 1 ||
                !record.keyValid || !record.requestValid ||
                record.packetOwned)
                return false;
            ++bufferOwners;
            break;
        }
        if (record.state != RecordState::Free &&
            (record.actionID == 0 ||
             record.token.actionID != record.actionID ||
             record.token.epoch != record.epoch)) {
            return false;
        }
        if (record.credit != NoCredit &&
            (record.credit >= ResponseCredits ||
             creditOwners[record.credit] != index)) {
            return false;
        }
    }
    if (bufferOwners != creditsInUse() || bufferOwners > ResponseCredits)
        return false;
    if (deliveryCopyActive) {
        bool delivering = false;
        for (const TransactionRecord &record : records)
            delivering |= record.state == RecordState::Delivering;
        if (!delivering)
            return false;
    }
    if (action.state == ActionState::Free) {
        for (const TransactionRecord &record : records) {
            if (record.state != RecordState::Free)
                return false;
        }
    } else {
        if (action.actionID == 0 || action.ackCount > action.nextLine ||
            action.nextLine > LinesPerPage)
            return false;
        for (std::size_t word = 0; word < action.acked.size(); ++word) {
            if ((action.acked[word] & ~action.issued[word]) != 0)
                return false;
        }
    }
    return true;
}

} // namespace gem5
