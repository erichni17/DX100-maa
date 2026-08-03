#ifndef __MEM_MAA_LOGICAL_SPD_CACHE_TRANSPORT_HH__
#define __MEM_MAA_LOGICAL_SPD_CACHE_TRANSPORT_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>

namespace gem5 {

class LogicalSPDCacheTransportTestAccess;

class LogicalSPDCacheTransport
{
    friend class LogicalSPDCacheTransportTestAccess;

  public:
    static constexpr std::size_t DescriptorCount = 2;
    static constexpr std::size_t PagesPerDescriptor = 4;
    static constexpr std::size_t SlotCount = 2;
    static constexpr std::size_t PortCount = 4;
    static constexpr std::size_t PageElements = 4096;
    static constexpr std::size_t LineBytes = 64;
    static constexpr std::size_t PageBytes = PageElements * sizeof(double);
    static constexpr std::size_t LinesPerPage = PageBytes / LineBytes;
    static constexpr std::size_t RecordCount = 8;
    static constexpr std::size_t FifoEntries = 8;
    static constexpr std::size_t ResponseCredits = 4;
    static constexpr uint8_t NoRecord = std::numeric_limits<uint8_t>::max();
    static constexpr uint8_t NoCredit = std::numeric_limits<uint8_t>::max();

    static constexpr std::size_t PrivateSlotPayloadBits =
        SlotCount * PageBytes * 8;
    static constexpr std::size_t DescriptorCorrelatorBits =
        DescriptorCount * 123;
    static constexpr std::size_t SlotCorrelatorBits = SlotCount * 82;
    static constexpr std::size_t PageActionBits = 1184;
    static constexpr std::size_t TransactionCorrelatorBits =
        RecordCount * 177;
    static constexpr std::size_t RequestFifoControlBits = 38;
    static constexpr std::size_t CreditOwnerBits = 16;
    static constexpr std::size_t FixedLineBufferBits =
        ResponseCredits * LineBytes * 8;
    static constexpr std::size_t GlobalControlBits = 41;
    static constexpr std::size_t PackedLogicalStateBits =
        PrivateSlotPayloadBits + DescriptorCorrelatorBits +
        SlotCorrelatorBits + PageActionBits +
        TransactionCorrelatorBits + RequestFifoControlBits +
        CreditOwnerBits + FixedLineBufferBits + GlobalControlBits;
    static constexpr std::size_t PackedLogicalStateBytes = 66181;
    static constexpr std::size_t AlignedHardwareProjectionBytes = 66324;

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
        WrongRetryPort,
        Stale,
        ProductionStop,
        CopyActive,
        CopyFailed,
        Sealed,
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

    struct Result
    {
        Status status = Status::Invalid;
        uint8_t record = NoRecord;
        const RequestPacket *handle = nullptr;
        DeliveryTicket ticket{};
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
        std::array<uint64_t, LinesPerPage / 64> issued{};
        std::array<uint64_t, LinesPerPage / 64> acked{};

        bool operator==(const AuditSnapshot &other) const;
    };

    using CopyHook = bool (*)(void *context);

    explicit LogicalSPDCacheTransport(std::size_t ports = PortCount,
                                      std::size_t lineBytes = LineBytes);
    ~LogicalSPDCacheTransport();

    LogicalSPDCacheTransport(const LogicalSPDCacheTransport &) = delete;
    LogicalSPDCacheTransport &
    operator=(const LogicalSPDCacheTransport &) = delete;

    Status startAction(Operation operation, uint8_t descriptor,
                       uint32_t generation, uint8_t page, uint8_t slot,
                       uint64_t baseAddress, PageSpan slotSpan,
                       uint32_t *actionID = nullptr);
    Result prepare(PageSpan slotSpan, FaultPoint fault = FaultPoint::None);
    Result sendPrepared(bool accepted);
    Result trySend(bool accepted, PageSpan slotSpan,
                   FaultPoint fault = FaultPoint::None);
    Status recvReqRetry(uint8_t callbackPort);
    Result receive(ReturnedHandle &returned, uint8_t callbackPort);
    Status commitDelivery(const DeliveryTicket &ticket, PageSpan destination,
                          CopyHook hook = nullptr, void *context = nullptr);
    Status abortAction(AbortCode code);
    Status reset();
    Status seal();

    bool assertInvariants() const;
    bool drained() const;
    bool geometryValid() const { return geometryIsValid; }
    bool copyActive() const { return deliveryCopyActive; }
    bool sealed() const { return isSealed; }
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
        PageSpan slotSpan{};
        uint16_t nextLine = 0;
        std::array<uint64_t, LinesPerPage / 64> issued{};
        std::array<uint64_t, LinesPerPage / 64> acked{};
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

    struct DescriptorProjection
    {
        uint64_t backingBase;
        uint32_t generation;
        uint32_t backingSpan;
        std::array<uint8_t, 4> flags;
    };

    struct SlotProjection
    {
        uint32_t generation;
        uint32_t actionID;
        std::array<uint8_t, 8> fields;
    };

    struct ActionProjection
    {
        std::array<uint64_t, 16> sets;
        uint64_t baseAddress;
        uint32_t actionID;
        uint32_t generation;
        std::array<uint8_t, 16> fields;
    };

    struct TransactionProjection
    {
        uint64_t address;
        uint32_t actionID;
        uint32_t generation;
        uint16_t epoch;
        std::array<uint8_t, 14> fields;
    };

    struct FifoProjection
    {
        std::array<uint8_t, 12> fields;
        uint32_t reserved;
    };

    struct CreditProjection
    {
        std::array<uint8_t, 4> owners;
        uint32_t reserved;
    };

    struct GlobalProjection
    {
        uint32_t nextActionID;
        std::array<uint8_t, 8> fields;
    };

    static_assert(sizeof(DescriptorProjection) == 24);
    static_assert(sizeof(SlotProjection) == 16);
    static_assert(sizeof(ActionProjection) == 160);
    static_assert(sizeof(TransactionProjection) == 32);
    static_assert(sizeof(FifoProjection) == 16);
    static_assert(sizeof(CreditProjection) == 8);
    static_assert(sizeof(GlobalProjection) == 12);
    static_assert(PackedLogicalStateBytes ==
                  (PackedLogicalStateBits + 7) / 8);
    static_assert(PrivateSlotPayloadBits == 524288);
    static_assert(DescriptorCorrelatorBits == 246);
    static_assert(SlotCorrelatorBits == 164);
    static_assert(TransactionCorrelatorBits == 1416);
    static_assert(FixedLineBufferBits == 2048);
    static_assert(AlignedHardwareProjectionBytes ==
                  2 * PageBytes + 2 * sizeof(DescriptorProjection) +
                      2 * sizeof(SlotProjection) + sizeof(ActionProjection) +
                      RecordCount * sizeof(TransactionProjection) +
                      sizeof(FifoProjection) + sizeof(CreditProjection) +
                      ResponseCredits * LineBytes +
                      sizeof(GlobalProjection));

    Status publicMutationStatus() const;
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
    Status ackReleaseAndRefill(uint8_t record);
    static void setBit(std::array<uint64_t, LinesPerPage / 64> &bits,
                       std::size_t line);
    static bool getBit(const std::array<uint64_t, LinesPerPage / 64> &bits,
                       std::size_t line);
    static bool allBits(const std::array<uint64_t, LinesPerPage / 64> &bits);

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
};

static_assert(LogicalSPDCacheTransport::DescriptorCount == 2);
static_assert(LogicalSPDCacheTransport::PagesPerDescriptor == 4);
static_assert(LogicalSPDCacheTransport::SlotCount == 2);
static_assert(LogicalSPDCacheTransport::PortCount == 4);
static_assert(LogicalSPDCacheTransport::LineBytes == 64);
static_assert(LogicalSPDCacheTransport::RecordCount == 8);
static_assert(LogicalSPDCacheTransport::FifoEntries == 8);
static_assert(LogicalSPDCacheTransport::ResponseCredits == 4);
static_assert(LogicalSPDCacheTransport::LinesPerPage == 512);

} // namespace gem5

#endif // __MEM_MAA_LOGICAL_SPD_CACHE_TRANSPORT_HH__
