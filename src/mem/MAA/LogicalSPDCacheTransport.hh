#ifndef __MEM_MAA_LOGICAL_SPD_CACHE_TRANSPORT_HH__
#define __MEM_MAA_LOGICAL_SPD_CACHE_TRANSPORT_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>

namespace gem5 {

class LogicalSPDCacheRuntime;

class LogicalSPDCacheTransport
{
    friend class LogicalSPDCacheRuntime;
  public:
    static constexpr std::size_t DescriptorCount = 2;
    static constexpr std::size_t PagesPerDescriptor = 8;
    static constexpr std::size_t SlotCount = 2;
    static constexpr std::size_t PortCount = 4;
    static constexpr std::size_t PageElements = 2048;
    static constexpr std::size_t MaxPageElements = 4096;
    static constexpr std::size_t LineBytes = 64;
    static constexpr std::size_t PageBytes = PageElements * sizeof(double);
    static constexpr std::size_t LinesPerPage = PageBytes / LineBytes;
    static constexpr std::size_t MaxPageBytes =
        MaxPageElements * sizeof(double);
    static constexpr std::size_t MaxLinesPerPage = MaxPageBytes / LineBytes;
    static constexpr std::size_t RecordCount = 8;
    static constexpr std::size_t FifoEntries = 8;
    static constexpr std::size_t ResponseCredits = 4;
    static constexpr uint8_t NoRecord = std::numeric_limits<uint8_t>::max();
    static constexpr uint8_t NoCredit = std::numeric_limits<uint8_t>::max();

    enum class Operation : uint8_t
    {
        Fill,
        Writeback,
    };

    enum class Command : uint8_t
    {
        ReadReq,
        WriteReq,
        ReadResp,
        ReadRespWithInvalidate,
        WriteResp,
    };

    enum class RecordState : uint8_t
    {
        Free,
        Queued,
        PendingSend,
        WaitRetry,
        InFlight,
        Delivering,
        AbortDrain,
    };

    enum class ActionState : uint8_t
    {
        Free,
        Active,
        AbortDrain,
    };

    enum class AbortCode : uint8_t
    {
        None,
        Caller,
    };

    enum class FaultPoint : uint8_t
    {
        None,
        RequestIdentity,
        LineSnapshot,
        RequestPacket,
        // Consumed only while constructing a fresh Runtime-owned action.
        // Transport::prepare rejects it, so it cannot alter live state.
        FinalCompletionIdentity,
    };

    enum class Status : uint8_t
    {
        Accepted,
        Completed,
        DeliveryPending,
        SendAccepted,
        SendRefused,
        NoWork,
        NoCreditAvailable,
        RetryRequired,
        AbortDrained,
        AbortOwnerDrained,
        AlreadyDrained,
        Busy,
        Invalid,
        InvalidGeometry,
        Exhausted,
        FaultInjected,
        ProductionStop,
        CopyFailed,
        Sealed,
        Poisoned,
    };

    struct IdBudget
    {
        uint32_t actionIDs = std::numeric_limits<uint32_t>::max();
        uint32_t incarnationIDs = std::numeric_limits<uint32_t>::max();
        uint32_t recordEpochs =
            RecordCount * std::numeric_limits<uint16_t>::max();

        bool operator==(const IdBudget &other) const
        {
            return actionIDs == other.actionIDs &&
                   incarnationIDs == other.incarnationIDs &&
                   recordEpochs == other.recordEpochs;
        }
    };

    struct PageSpan
    {
        std::byte *data = nullptr;
        std::size_t size = 0;
    };

    struct TransactionKey
    {
        uint8_t descriptor = 0;
        uint32_t generation = 0;
        uint8_t slot = 0;
        uint8_t page = 0;
        uint16_t line = 0;
        Operation operation = Operation::Fill;

        bool operator==(const TransactionKey &other) const;
    };

    struct RouteToken
    {
        uint8_t record = NoRecord;
        uint16_t epoch = 0;
        uint32_t actionID = 0;
    };

    struct RequestIdentity
    {
        uint32_t incarnation = 0;
    };

    struct RequestPacket
    {
        uint32_t incarnation = 0;
        const RequestIdentity *request = nullptr;
        const RouteToken *token = nullptr;
        uint8_t tokenDepth = 0;
        uint8_t callbackPort = 0;
        uint64_t address = 0;
        Command command = Command::ReadReq;
        uint16_t size = 0;
        const std::byte *data = nullptr;
        std::size_t dataSize = 0;
    };

    struct ReturnedHandle
    {
        uint64_t incarnation = 0;
        const RequestIdentity *request = nullptr;
        uint32_t requestIncarnation = 0;
        const RouteToken *token = nullptr;
        uint8_t tokenDepth = 0;
        uint8_t tokenRecord = NoRecord;
        uint16_t tokenEpoch = 0;
        uint32_t tokenActionID = 0;
        uint64_t address = 0;
        Command command = Command::ReadResp;
        uint16_t size = 0;
        const std::byte *data = nullptr;
        std::size_t dataSize = 0;
        bool disposed = false;
    };

    struct DeliveryTicket
    {
        uint8_t record = NoRecord;
        uint16_t epoch = 0;
        uint32_t actionID = 0;

        bool operator==(const DeliveryTicket &other) const;
    };

    class CompletionIdentity
    {
      public:
        CompletionIdentity() = default;
        CompletionIdentity(const CompletionIdentity &) = default;
        CompletionIdentity &operator=(const CompletionIdentity &) = delete;

        bool valid() const { return isValid; }
        Operation kind() const { return operation; }
        uint32_t id() const { return actionID; }
        uint8_t descriptorID() const { return descriptor; }
        uint32_t descriptorGeneration() const { return generation; }
        uint8_t pageID() const { return page; }
        uint8_t slotID() const { return slot; }
        uint64_t controllerSerial() const { return serial; }
        bool operator==(const CompletionIdentity &other) const;

      private:
        friend class LogicalSPDCacheTransport;

        CompletionIdentity(Operation completionKind, uint32_t completionID,
                           uint8_t completionDescriptor,
                           uint32_t completionGeneration,
                           uint8_t completionPage, uint8_t completionSlot,
                           uint64_t completionSerial)
            : isValid(true), operation(completionKind),
              actionID(completionID), descriptor(completionDescriptor),
              generation(completionGeneration), page(completionPage),
              slot(completionSlot), serial(completionSerial)
        {}

        bool isValid = false;
        Operation operation = Operation::Fill;
        uint32_t actionID = 0;
        uint8_t descriptor = 0;
        uint32_t generation = 0;
        uint8_t page = 0;
        uint8_t slot = 0;
        uint64_t serial = 0;
    };

    struct Result
    {
        Status status = Status::Invalid;
        uint8_t record = NoRecord;
        const RequestPacket *handle = nullptr;
        DeliveryTicket ticket{};
        CompletionIdentity completion{};
    };

    struct AuditSnapshot
    {
        ActionState actionState = ActionState::Free;
        uint32_t actionID = 0;
        uint16_t nextLine = 0;
        uint16_t ackCount = 0;
        uint32_t nextActionID = 0;
        uint32_t nextIncarnationID = 0;
        uint8_t fifoHead = 0;
        uint8_t fifoTail = 0;
        uint8_t fifoCount = 0;
        uint8_t pending = NoRecord;
        bool actionIDsExhausted = false;
        bool incarnationIDsExhausted = false;
        bool copyActive = false;
        bool sealed = false;
        bool poisoned = false;
        bool geometryValid = false;
        Operation operation = Operation::Fill;
        AbortCode abortCode = AbortCode::None;
        uint8_t descriptor = 0;
        uint32_t generation = 0;
        uint8_t page = 0;
        uint8_t slot = 0;
        uint64_t baseAddress = 0;
        uint64_t controllerSerial = 0;
        IdBudget remainingBudget{};
        std::array<uint8_t, FifoEntries> fifo{};
        std::array<uint8_t, ResponseCredits> credits{};
        std::array<RecordState, RecordCount> states{};
        std::array<uint16_t, RecordCount> epochs{};
        std::array<uint32_t, RecordCount> recordActionIDs{};
        std::array<TransactionKey, RecordCount> keys{};
        std::array<uint64_t, RecordCount> addresses{};
        std::array<uint32_t, RecordCount> requestIDs{};
        std::array<uint32_t, RecordCount> packetIDs{};
        std::array<uint8_t, RecordCount> recordCredits{};
        std::array<bool, RecordCount> keyValid{};
        std::array<bool, RecordCount> requestValid{};
        std::array<bool, RecordCount> packetOwned{};
        std::array<std::array<std::byte, LineBytes>, ResponseCredits>
            lineBuffers{};
        uint16_t activeLines = LinesPerPage;
        uint16_t activePageBytes = PageBytes;
        std::array<uint64_t, MaxLinesPerPage / 64> issued{};
        std::array<uint64_t, MaxLinesPerPage / 64> acked{};

        bool operator==(const AuditSnapshot &other) const;
    };

    using CopyHook = bool (*)(void *context);

    explicit LogicalSPDCacheTransport(
        std::size_t ports = PortCount,
        std::size_t lineBytes = LineBytes,
        std::size_t pageBytes = PageBytes,
        std::size_t pagesPerDescriptor = PagesPerDescriptor);
    LogicalSPDCacheTransport(std::size_t ports, std::size_t lineBytes,
                             IdBudget budget,
                             std::size_t pageBytes = PageBytes,
                             std::size_t pagesPerDescriptor =
                                 PagesPerDescriptor);
    ~LogicalSPDCacheTransport();

    LogicalSPDCacheTransport(const LogicalSPDCacheTransport &) = delete;
    LogicalSPDCacheTransport &
    operator=(const LogicalSPDCacheTransport &) = delete;

    Status startAction(Operation operation, uint8_t descriptor,
                       uint32_t generation, uint8_t page, uint8_t slot,
                       uint64_t baseAddress, uint64_t controllerSerial,
                       PageSpan slotSpan,
                       uint32_t *actionID = nullptr,
                       FaultPoint constructionFault = FaultPoint::None);
    Result prepare(PageSpan slotSpan, FaultPoint fault = FaultPoint::None);
    Result sendPrepared(bool accepted);
    Result trySend(bool accepted, PageSpan slotSpan,
                   FaultPoint fault = FaultPoint::None);
    Status recvReqRetry(uint8_t callbackPort);
    Status resumeLocalCapacity(uint8_t callbackPort);
    Result receive(ReturnedHandle &returned, uint8_t callbackPort);
    Result commitDelivery(const DeliveryTicket &ticket, PageSpan destination,
                          CopyHook hook = nullptr, void *context = nullptr);
    Status abortAction(AbortCode code);
    Status reset();
    Status seal();

    bool assertInvariants() const;
    bool drained() const;
    bool geometryValid() const { return geometryIsValid; }
    std::size_t pageBytes() const { return configuredPageBytes; }
    std::size_t linesPerPage() const { return configuredLinesPerPage; }
    std::size_t pageCount() const { return configuredPagesPerDescriptor; }
    bool copyActive() const { return deliveryCopyActive; }
    bool sealed() const { return isSealed; }
    bool poisoned() const { return terminalPoisoned; }
    ActionState actionState() const { return action.state; }
    uint32_t actionID() const { return action.actionID; }
    uint16_t ackCount() const { return action.ackCount; }
    uint16_t nextLine() const { return action.nextLine; }
    std::size_t fifoCount() const { return fifo.count; }
    std::size_t creditsInUse() const;
    uint8_t pendingRecord() const { return pending; }
    RecordState recordState(std::size_t record) const;
    TransactionKey recordKey(std::size_t record) const;
    uint16_t recordEpoch(std::size_t record) const;
    const RouteToken *recordToken(std::size_t record) const;
    const RequestIdentity *recordRequest(std::size_t record) const;
    const std::byte *recordLineBuffer(std::size_t record) const;
    const RequestPacket *pendingHandle() const;
    bool lineIssued(std::size_t line) const;
    bool lineAcked(std::size_t line) const;
    bool issuedSetComplete() const;
    bool ackSetComplete() const;
    AuditSnapshot auditSnapshot() const;

    static uint8_t portForAddress(uint64_t address);
    static Command requestCommand(Operation operation);
    static Command responseCommand(Operation operation);

  private:
    /**
     * A one-call precommit authority created only by Runtime after it has
     * compared Transport's immutable candidate with the accepted Slice
     * correlation.  It is stack-local, carries no forgeable public token,
     * and is checked again at the Transport mutation boundary.
     */
    class CompletionAuthority
    {
        friend class LogicalSPDCacheRuntime;
        friend class LogicalSPDCacheTransport;

        explicit CompletionAuthority(
            const CompletionIdentity &completionIdentity)
            : completion(completionIdentity)
        {}

        CompletionIdentity completion{};
    };

    struct TransactionRecord
    {
        RecordState state = RecordState::Free;
        uint16_t epoch = 0;
        uint32_t actionID = 0;
        TransactionKey key{};
        RouteToken token{};
        RequestIdentity request{};
        RequestPacket packet{};
        uint64_t address = 0;
        Command expectedResponse = Command::ReadResp;
        uint8_t port = 0;
        uint8_t credit = NoCredit;
        bool keyValid = false;
        bool requestValid = false;
        bool packetOwned = false;
    };

    struct PageAction
    {
        ActionState state = ActionState::Free;
        uint32_t actionID = 0;
        Operation operation = Operation::Fill;
        uint8_t descriptor = 0;
        uint32_t generation = 0;
        uint8_t page = 0;
        uint8_t slot = 0;
        uint64_t baseAddress = 0;
        uint64_t controllerSerial = 0;
        PageSpan slotSpan{};
        uint16_t nextLine = 0;
        std::array<uint64_t, MaxLinesPerPage / 64> issued{};
        std::array<uint64_t, MaxLinesPerPage / 64> acked{};
        uint16_t ackCount = 0;
        AbortCode abortCode = AbortCode::None;
    };

    struct RequestFifo
    {
        std::array<uint8_t, FifoEntries> entries{};
        uint8_t head = 0;
        uint8_t tail = 0;
        uint8_t count = 0;
    };

    Status publicMutationStatus();
    Status resumeRefused(uint8_t callbackPort);
    Status productionStop();
    void poisonFromAuthority() { terminalPoisoned = true; }
    Result productionStopResult();
    static bool validOperation(Operation operation);
    static bool validCommand(Command command);
    static bool validAbortCode(AbortCode code);
    static bool validFaultPoint(FaultPoint fault);
    bool validPageSpan(PageSpan span) const;
    bool previewIncarnations(uint32_t count, uint32_t &first,
                             uint32_t &committedNext,
                             bool &committedExhausted) const;
    int allocateRecord(uint32_t actionID);
    void refillQueue();
    void fifoPush(uint8_t record);
    uint8_t fifoPop();
    int freeCredit() const;
    void releaseRecord(uint8_t record);
    bool actionHasRecords() const;
    bool finishAbortIfDrained();
    int lookupToken(const ReturnedHandle &returned) const;
    bool wireExact(const TransactionRecord &record,
                   const ReturnedHandle &returned) const;
    bool ticketExact(const DeliveryTicket &ticket, uint8_t &record) const;
    CompletionIdentity completionCandidate(uint8_t record) const;
    bool authorityExact(
        const CompletionIdentity &candidate,
        const CompletionAuthority *authority) const;
    CompletionIdentity precommitReceive(
        const ReturnedHandle &returned, uint8_t callbackPort) const;
    CompletionIdentity precommitDelivery(
        const DeliveryTicket &ticket, PageSpan destination) const;
    Result receiveAuthorized(ReturnedHandle &returned, uint8_t callbackPort,
                             const CompletionAuthority &authority);
    Result commitDeliveryAuthorized(
        const DeliveryTicket &ticket, PageSpan destination, CopyHook hook,
        void *context, const CompletionAuthority &authority);
    Result receiveInternal(ReturnedHandle &returned, uint8_t callbackPort,
                           const CompletionAuthority *authority);
    Result commitDeliveryInternal(
        const DeliveryTicket &ticket, PageSpan destination, CopyHook hook,
        void *context, const CompletionAuthority *authority);
    Result ackReleaseAndRefill(
        uint8_t record, const CompletionAuthority *authority = nullptr);
    static void setBit(std::array<uint64_t, MaxLinesPerPage / 64> &bits,
                       std::size_t line);
    bool getBit(const std::array<uint64_t, MaxLinesPerPage / 64> &bits,
                std::size_t line) const;
    bool allBits(
        const std::array<uint64_t, MaxLinesPerPage / 64> &bits) const;

    std::array<TransactionRecord, RecordCount> records{};
    RequestFifo fifo{};
    uint8_t pending = NoRecord;
    std::array<uint8_t, ResponseCredits> creditOwners{};
    std::array<std::array<std::byte, LineBytes>, ResponseCredits>
        lineBuffers{};
    PageAction action{};
    uint32_t nextActionID = 1;
    bool actionIDsExhausted = false;
    uint32_t nextIncarnationID = 1;
    bool incarnationIDsExhausted = false;
    bool deliveryCopyActive = false;
    bool isSealed = false;
    bool geometryIsValid = true;
    bool terminalPoisoned = false;
    IdBudget remainingBudget{};
    std::size_t configuredPageBytes = PageBytes;
    std::size_t configuredLinesPerPage = LinesPerPage;
    std::size_t configuredPagesPerDescriptor = PagesPerDescriptor;
};

static_assert(LogicalSPDCacheTransport::DescriptorCount == 2);
static_assert(LogicalSPDCacheTransport::PagesPerDescriptor == 8);
static_assert(LogicalSPDCacheTransport::SlotCount == 2);
static_assert(LogicalSPDCacheTransport::PortCount == 4);
static_assert(LogicalSPDCacheTransport::LineBytes == 64);
static_assert(LogicalSPDCacheTransport::RecordCount == 8);
static_assert(LogicalSPDCacheTransport::FifoEntries == 8);
static_assert(LogicalSPDCacheTransport::ResponseCredits == 4);
static_assert(LogicalSPDCacheTransport::LinesPerPage == 256);
static_assert(LogicalSPDCacheTransport::MaxLinesPerPage == 512);

} // namespace gem5

#endif // __MEM_MAA_LOGICAL_SPD_CACHE_TRANSPORT_HH__
