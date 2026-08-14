#ifndef __MEM_MAA_MAA_HH__
#define __MEM_MAA_MAA_HH__

#include <array>
#include <bitset>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <deque>
#include <memory>
#include <queue>
#include <string>
#include <unordered_map>

#include "arch/generic/mmu.hh"
#include "base/trace.hh"
#include "base/types.hh"
#include "mem/MAA/DirectRetirementPortRetry.hh"
#include "mem/MAA/EarlyProducerLineReadinessLedger.hh"
#include "mem/MAA/HybridConsumerContextQueue.hh"
#include "mem/MAA/HybridMacroEventTracker.hh"
#include "mem/MAA/IF.hh"
#include "mem/MAA/InactiveProducerLinePayloadCapture.hh"
#include "mem/MAA/LogicalSPDCacheGem5Bridge.hh"
#include "mem/MAA/LogicalSPDCacheLiveAdapterState.hh"
#include "mem/cache/tags/base.hh"
#include "mem/packet.hh"
#include "mem/packet_queue.hh"
#include "mem/qport.hh"
#include "mem/ramulator2.hh"
#include "mem/request.hh"
#include "sim/clocked_object.hh"
#include "sim/system.hh"

#define ADDR_CHANNEL_LEVEL   0
#define ADDR_RANK_LEVEL      1
#define ADDR_BANKGROUP_LEVEL 2
#define ADDR_BANK_LEVEL      3
#define ADDR_ROW_LEVEL       4
#define ADDR_COLUMN_LEVEL    5
#define ADDR_MAX_LEVEL       6

namespace gem5 {

struct MAAParams;
class IF;
class RF;
class SPD;
class StreamAccessUnit;
class IndirectAccessUnit;
class Invalidator;
class ALUUnit;
class RangeFuserUnit;
class Instruction;
typedef Instruction *InstructionPtr;
struct Register;
typedef Register *RegisterPtr;

/**
 * A basic cache interface. Implements some common functions for speed.
 */
class MAA : public ClockedObject {
    typedef std::pair<Addr, Addr> AddrRegion;
    /**
     * A cache response port is used for the CPU-side port of the cache,
     * and it is basically a simple timing port that uses a transmit
     * list for responses to the CPU (or connected requestor). In
     * addition, it has the functionality to block the port for
     * incoming requests. If blocked, the port will issue a retry once
     * unblocked.
     */
    class MAAResponsePort : public QueuedResponsePort {

    protected:
        MAAResponsePort(const std::string &_name, MAA &_maa, const std::string &_label);

        MAA &maa;

        /** A normal packet queue used to store responses. */
        RespPacketQueue queue;
    };

    /**
     * The CPU-side port extends the base MAA response port with access
     * functions for functional, atomic and timing requests.
     */
    class CpuSidePort : public MAAResponsePort {
    protected:
        bool recvTimingSnoopResp(PacketPtr pkt) override;

        bool tryTiming(PacketPtr pkt) override;

        bool recvTimingReq(PacketPtr pkt) override;

        Tick recvAtomic(PacketPtr pkt) override;

        void recvFunctional(PacketPtr pkt) override;

        AddrRangeList getAddrRanges() const override;

    protected:
        int outstandingCpuSidePackets;
        int maxOutstandingCpuSidePackets;
        bool is_blocked;
        bool mustRetryTileRequest;
        bool tileRequestRetryOutstanding;
        int retryTileID;
        int core_id;

    public:
        bool sendSnoopInvalidatePacket(PacketPtr pkt);
        void retryTileRequest();
        void allocate(int _core_id, int _maxOutstandingCpuSidePackets);

    public:
        CpuSidePort(const std::string &_name, MAA &_maa,
                    const std::string &_label);
    };

    class MAAMemRequestPort : public QueuedRequestPort {
    public:
        /**
         * Schedule a send of a request packet (from the MSHR). Note
         * that we could already have a retry outstanding.
         */
        void schedSendEvent(Tick time) {
            reqQueue.schedSendEvent(time);
        }

    protected:
        MAAMemRequestPort(const std::string &_name,
                          ReqPacketQueue &_reqQueue,
                          SnoopRespPacketQueue &_snoopRespQueue)
            : QueuedRequestPort(_name, _reqQueue, _snoopRespQueue) {}

        /**
         * Memory-side port never snoops.
         *
         * @return always false
         */
        bool isSnooping() const { return false; }
    };

    class MAACacheRequestPort : public QueuedRequestPort {
    public:
        /**
         * Schedule a send of a request packet (from the MSHR). Note
         * that we could already have a retry outstanding.
         */
        void schedSendEvent(Tick time) {
            reqQueue.schedSendEvent(time);
        }

    protected:
        MAACacheRequestPort(const std::string &_name,
                            ReqPacketQueue &_reqQueue,
                            SnoopRespPacketQueue &_snoopRespQueue)
            : QueuedRequestPort(_name, _reqQueue, _snoopRespQueue) {}

        /**
         * Memory-side port always snoops.
         *
         * @return always false
         */
        bool isSnooping() const { return false; }
    };

    /**
     * Override the default behaviour of sendDeferredPacket to enable
     * the memory-side cache port to also send requests based on the
     * current MSHR status. This queue has a pointer to our specific
     * cache implementation and is used by the MemSidePort.
     */
    class MAAReqPacketQueue : public ReqPacketQueue {

    protected:
        MAA &maa;
        SnoopRespPacketQueue &snoopRespQueue;

    public:
        MAAReqPacketQueue(MAA &maa, RequestPort &port,
                          SnoopRespPacketQueue &snoop_resp_queue,
                          const std::string &label) : ReqPacketQueue(maa, port, label), maa(maa),
                                                      snoopRespQueue(snoop_resp_queue) {}

        /**
         * Override the normal sendDeferredPacket and do not only
         * consider the transmit list (used for responses), but also
         * requests.
         */
        void sendDeferredPacket();
    };

    /**
     * The memory-side port extends the base cache request port with
     * access functions for functional, atomic and timing snoops.
     */
    class MemSidePort : public MAAMemRequestPort {
    private:
        /** The maa-specific queue. */
        MAAReqPacketQueue _reqQueue;

        SnoopRespPacketQueue _snoopRespQueue;

        // a pointer to our specific MAA implementation
        MAA *maa;

    protected:
        void recvTimingSnoopReq(PacketPtr pkt);

        bool recvTimingResp(PacketPtr pkt);

        Tick recvAtomicSnoop(PacketPtr pkt);

        void recvFunctionalSnoop(PacketPtr pkt);

        void recvReqRetry();

    protected:
        int channel_id;
        void setUnblocked();

    public:
        bool sendPacket(PacketPtr pkt);
        void allocate(int _channel_id);

    public:
        MemSidePort(const std::string &_name, MAA *_maa,
                    const std::string &_label);
    };

    /**
     * The memory-side port extends the base cache request port with
     * access functions for functional, atomic and timing snoops.
     */
    class CacheSidePort : public MAACacheRequestPort {
        enum class BlockReason : uint8_t {
            NOT_BLOCKED,
            MAX_XBAR_PACKETS,
            CACHE_FAILED
        };

    private:
        /** The maa-specific queue. */
        MAAReqPacketQueue _reqQueue;

        SnoopRespPacketQueue _snoopRespQueue;

        // a pointer to our specific cache implementation
        MAA *maa;

    protected:
        void recvTimingSnoopReq(PacketPtr pkt);

        bool recvTimingResp(PacketPtr pkt);

        Tick recvAtomicSnoop(PacketPtr pkt);

        void recvFunctionalSnoop(PacketPtr pkt);

        void recvReqRetry();

    protected:
        int outstandingCacheSidePackets;
        int maxOutstandingCacheSidePackets;
        BlockReason blockReason;
        void setUnblocked(BlockReason reason);
        int core_id;

    public:
        bool sendPacket(
            PacketPtr pkt,
            LogicalSPDCacheLiveAdapterState::WaitAuthority *refusal =
                nullptr);
        void allocate(int _core_id, int _maxOutstandingCacheSidePackets);

    public:
        CacheSidePort(const std::string &_name, MAA *_maa,
                      const std::string &_label);
    };

protected:
    std::vector<CpuSidePort *> cpuSidePorts;
    std::vector<MemSidePort *> memSidePorts;
    std::vector<CacheSidePort *> cacheSidePorts;
    std::vector<CacheSidePort *> retirementSidePorts;

public:
    SPD *spd;
    RF *rf;
    IF *ifile;
    StreamAccessUnit *streamAccessUnits;
    IndirectAccessUnit *indirectAccessUnits;
    Invalidator *invalidator;
    ALUUnit *aluUnits;
    RangeFuserUnit *rangeUnits;
    std::unique_ptr<LogicalSPDCacheGem5Bridge> logicalSpdBridge;

    // Ramulator related variables for address mapping
    std::vector<int> m_org;
    std::vector<int> m_addr_bits; // How many address bits for each level in the hierarchy?
    int m_num_levels;             // How many levels in the hierarchy?
    int m_tx_offset;
    int m_col_bits_idx;
    int m_row_bits_idx;

public:
    std::vector<int> map_addr(Addr addr);
    int channel_addr(Addr addr);
    int core_addr(Addr addr);
    Addr calc_Grow_addr(std::vector<int> addr_vec);
    void addRamulator(memory::Ramulator2 *_ramulator2);
    bool sendPacketMem(PacketPtr pkt);
    bool sendPacketCache(
        PacketPtr pkt, uint8_t *actualPort = nullptr,
        LogicalSPDCacheLiveAdapterState::WaitAuthority *refusal = nullptr);
    bool sendPacketRetirementCache(PacketPtr pkt);
    void sendSnoopPacketCpu(PacketPtr pkt);
    bool sendSnoopInvalidateCpu(PacketPtr pkt);

protected:
    /**
     * Performs the access specified by the request.
     * @param pkt The request to perform.
     */
    void recvTimingReq(PacketPtr pkt, int core_id);

    /**
     * Handles a response from the bus.
     * @param pkt The response packet
     */
    void recvTimingResp(PacketPtr pkt, bool cached);

    /**
     * Handle a snoop response.
     * @param pkt Snoop response packet
     */
    void recvTimingSnoopResp(PacketPtr pkt);

    /**
     * Performs the access specified by the request.
     * @param pkt The request to perform.
     * @return The number of ticks required for the access.
     */
    Tick recvAtomic(PacketPtr pkt);

    /**
     * Snoop for the provided request in the cache and return the estimated
     * time taken.
     * @param pkt The memory request to snoop
     * @return The number of ticks required for the snoop.
     */
    Tick recvMemAtomicSnoop(PacketPtr pkt);

    /**
     * Snoop for the provided request in the cache and return the estimated
     * time taken.
     * @param pkt The memory request to snoop
     * @return The number of ticks required for the snoop.
     */
    Tick recvCacheAtomicSnoop(PacketPtr pkt);

    /**
     * Performs the access specified by the request.
     *
     * @param pkt The request to perform.
     * @param fromCpuSide from the CPU side port or the memory side port
     */
    void memFunctionalAccess(PacketPtr pkt, bool from_cpu_side);

    /**
     * Performs the access specified by the request.
     *
     * @param pkt The request to perform.
     * @param fromCpuSide from the CPU side port or the memory side port
     */
    void cacheFunctionalAccess(PacketPtr pkt, bool from_cpu_side);

    /**
     * Determine if an address is in the ranges covered by this
     * cache. This is useful to filter snoops.
     *
     * @param addr Address to check against
     *
     * @return The id of the range that contains the address, or -1 if none
     */
    int inRange(Addr addr) const;

    /**
     * Snoops bus transactions to maintain coherence.
     * @param pkt The current bus transaction.
     */
    void recvMemTimingSnoopReq(PacketPtr pkt);

    /**
     * Snoops bus transactions to maintain coherence.
     * @param pkt The current bus transaction.
     */
    void recvCacheTimingSnoopReq(PacketPtr pkt);

    /**
     * The address range to which the cache responds on the CPU side.
     * Normally this is all possible memory addresses. */
    const AddrRangeList addrRanges;
    std::vector<AddrRangeList> cpuPortAddrRanges;

public:
    unsigned int num_tiles;
    unsigned int num_tile_elements;
    unsigned int physical_tile_elements;
    unsigned int transparent_spd_mode;
    unsigned int logical_spd_cache_mode;
    unsigned int page_materialization_wakeup_batches;
    unsigned int page_materialization_fragment_buffers;
    bool page_materialization_direct_spd_fragments;
    unsigned int inactive_page_payload_capture_lines;
    unsigned int num_regs;
    unsigned int num_instructions_per_core;
    unsigned int num_instructions_per_maa;
    unsigned int num_instructions_total;
    unsigned int num_row_table_rows_per_slice;
    unsigned int num_offset_table_entries;
    unsigned int num_offset_table_epoch_entries;
    unsigned int num_row_table_entries_per_subslice_row;
    unsigned int num_row_table_config_cache_entries;
    bool reconfigure_row_table;
    bool reorder_row_table;
    bool force_cache_access;
    unsigned int num_initial_row_table_slices;
    unsigned int virtual_combine_slots;
    unsigned int virtual_combine_words;
    unsigned int virtual_combine_ways;
    unsigned int virtual_combine_victim_policy;
    bool virtual_page_ordered_combiner_drain;
    unsigned int virtual_combine_banks;
    unsigned int virtual_response_slots;
    unsigned int virtual_response_words;
    unsigned int virtual_response_word_pool;
    unsigned int virtual_words_per_cycle;
    unsigned int virtual_max_outstanding_writes;
    bool virtual_masked_writes;
    bool virtual_idealized_write_ack;
    bool direct_retirement_line_handoff;
    unsigned int virtual_index_buffer_lines;
    bool virtual_index_force_cache;
    unsigned int virtual_index_partitions;
    bool virtual_index_range_passes;
    bool virtual_index_descriptor_spool;
    bool virtual_descriptor_spool_read_ahead;
    unsigned int virtual_descriptor_spool_read_credits;
    unsigned int virtual_descriptor_spool_write_credits;
    bool virtual_descriptor_spool_source_bypass_cache;
    bool virtual_bounded_global_merge;
    unsigned int virtual_index_range_policy;
    std::vector<Addr> virtual_index_range_boundaries;
    unsigned int virtual_index_filter_words_per_cycle;
    bool virtual_partition_keep_combiner;
    bool virtual_grow_order;
    bool virtual_native_issue_order;
    unsigned int num_request_table_addresses;
    unsigned int num_request_table_entries_per_address;
    unsigned int num_memory_channels;
    unsigned int num_cores;
    unsigned int num_channels;
    unsigned int num_maas;
    unsigned int num_cores_per_maas;
    unsigned int num_indirect_units_per_maa;
    unsigned int num_indirect_units_total;
    unsigned int m_core_addr_bits;

    Cycles rowtable_latency;
    RequestorID requestorId;

    std::vector<AddrRegion> addrRegions;
    int maxRegionID;
    void addAddrRegion(Addr start, Addr end, int8_t id);
    void clearAddrRegion();
    int getAddrRegion(Addr addr);

public:
    static constexpr int MaxVirtualPages = 16;

    /** System we are currently operating in. */
    System *system;

    /** Registered mmu for address translations */
    BaseMMU *mmu;

public:
    MAA(const MAAParams &p);
    ~MAA();

    void init() override;

    Port &getPort(const std::string &if_name,
                  PortID idx = InvalidPortID) override;

    const AddrRangeList &getAddrRanges(int core_id) const { return cpuPortAddrRanges[core_id]; }
    void setTileReady(int tileID, int wordSize);
    void resetVirtualPageReady(int tokenTileID, Addr backingAddr,
                               int backingRangeID, int wordSize);
    void setVirtualPageReady(int tokenTileID, int pageID,
                             uint64_t transactionID);
    void setVirtualLineWordsReady(int tokenTileID, Addr backingAddr,
                                  uint64_t generation, int lineID,
                                  uint16_t wordMask,
                                  uint64_t transactionID,
                                  const uint8_t *writeRespPayload = nullptr,
                                  unsigned payloadBytes = 0);
    bool getVirtualPageReady(int tokenTileID, int pageID) const;
    uint64_t getVirtualPageReadyTransaction(int tokenTileID,
                                            int pageID) const;
    uint64_t getVirtualPageGeneration(int tokenTileID) const;
    Tick getVirtualProducerRegistrationTick(int tokenTileID) const;
    bool transparentControllerOwnsTile(int maaID, int tileID) const;
    bool transparentControllerUsesRegister(int maaID, int firstRegister,
                                           int registerWords) const;
    void recordTransparentConsumerAcceptance(int page, uint64_t transaction,
                                             Addr address, int ordinal,
                                             int expected);
    void recordTransparentStreamTraffic(
        TransparentSPDController::Action action, uint64_t lines,
        uint64_t bytes);
    void finishInstructionCompute(InstructionPtr instruction);
    void finishInstructionInvalidate(InstructionPtr instruction, int tileID);
    bool sentMemSidePacket(PacketPtr pkt);
    Tick getClockEdge(Cycles cycles = Cycles(0)) const;
    Cycles getTicksToCycles(Tick t) const;
    Tick getCyclesToTicks(Cycles c) const;
    void resetStats() override;
    bool getAddrRegionPermit(Instruction *instruction);
    void scheduleIssueInstructionEvent(int latency = 0);
    void completeDirectRetirementALU(int maaID, uint16_t tokenTile,
                                     uint64_t generation,
                                     uint64_t incarnation,
                                     uint64_t transactionID);

protected:
    std::vector<RequestorID> my_instruction_RIDs;
    std::map<RequestorID, int> my_RID_to_core_id;
    std::vector<PacketPtr> my_instruction_pkts;
    std::vector<bool> my_instruction_recvs;
    std::vector<PacketPtr> my_ready_pkts;
    std::vector<RegisterPtr> my_registers;
    std::vector<PacketPtr> my_register_pkts;
    std::vector<int> my_ready_tile_ids;
    std::vector<std::array<bool, MaxVirtualPages>> virtualPageReady;
    std::vector<std::array<uint64_t, MaxVirtualPages>>
        virtualPageReadyTransaction;
    std::vector<uint64_t> virtualPageGeneration;
    // Payload retention has its own monotonically allocated incarnation at
    // producer registration, before a consumer-context incarnation exists.
    // Every payload descriptor, RAM tag, lookup, take, clear, and trace uses
    // this exact lifetime identity in addition to generation/backing.
    std::vector<uint64_t> virtualPagePayloadIncarnation;
    std::vector<uint64_t> virtualPageConsumedGeneration;
    std::vector<Addr> virtualPageBackingAddr;
    std::vector<int> virtualPageBackingRangeID;
    std::vector<int> virtualPageWordSize;
    std::vector<Tick> virtualProducerRegistrationTick;
    std::vector<Tick> virtualPageLastReadyTick;
    TransparentSPDController transparentController;
    Tick transparentControllerLookupReadyTick = 0;
    uint64_t transparentTraceOccurrence = 0;
    bool transparentBlockerTracking = false;
    bool transparentInstructionFileBlocked = false;
    Tick transparentBlockerLastTick = 0;
    TransparentSPDController::Blocker transparentLastBlocker =
        TransparentSPDController::Blocker::Inactive;
    std::array<Tick, static_cast<size_t>(
                         TransparentSPDController::Blocker::Count)>
        transparentBlockerTicks{};
    HybridMacroEventTracker transparentMacroTracker;
    HybridMacroEventTracker::Record transparentMacroAllReadyRecord{};
    Tick transparentMacroAllReadyTick = 0;
    bool transparentMacroAllReadySampled = false;
    bool transparentMacroAllReadyBeforeSubmit = false;
    struct DirectRetirementSenderState : public Packet::SenderState
    {
        HybridConsumerContextQueue::Request request{};
        uint8_t callbackPort = HybridConsumerPipeline::PortCount;
    };
    struct DirectRetirementExecution
    {
        bool active = false;
        HybridConsumerContextQueue::ContextKey key{};
        int coreID = -1;
        int maaID = -1;
        int completionTile = -1;
        uint8_t datatype = 0;
        uint8_t operation = 0;
        uint8_t wordBytes = 0;
        uint64_t scalarBits = 0;
        Addr backingAddress = 0;
        int backingRangeID = -1;
        int destinationRangeID = -1;
        ContextID contextID = InvalidContextID;
        Addr pc = 0;
        HybridConsumerContextQueue::Request aluRequest{};
        HybridMacroEventTracker macro{};
    };
    struct PageMaterializationExecution
    {
        bool active = false;
        bool pageActive = false;
        HybridConsumerContextQueue::ContextKey key{};
        int coreID = -1;
        int maaID = -1;
        int destinationTile = -1;
        uint8_t page = HybridConsumerPipeline::ProducerPages;
        uint8_t wordBytes = 0;
        uint8_t pagesMaterialized = 0;
        uint16_t forwardedLines = 0;
        uint16_t stagedDirectLines = 0;
        uint16_t stagedDirectFragments = 0;
        uint16_t stagedDirectFallbackLines = 0;
        // Fixed active-page control only.  A producer page contains one bit
        // per logical word (4096); FP64 has 512 64-byte lines, so line maps
        // must cover the maximum geometry. No line payload lives here.
        static constexpr std::size_t MaxStagedWords =
            HybridConsumerPipeline::ProducerPageElements;
        static constexpr std::size_t MaxStagedLines =
            HybridConsumerPipeline::ProducerPageElements * sizeof(uint64_t) /
            HybridConsumerPipeline::LineBytes;
        std::bitset<MaxStagedWords> stagedWords{};
        std::bitset<MaxStagedLines> stagedDisallowed{};
        std::bitset<MaxStagedLines> stagedFallbackCounted{};
        uint16_t cacheReadFallbackLines = 0;
        std::array<uint16_t, HybridConsumerPipeline::ProducerPages>
            cacheReadFallbackLinesPerPage{};
        Addr backingAddress = 0;
        int backingRangeID = -1;
        ContextID contextID = InvalidContextID;
        Addr pc = 0;
    };
    HybridConsumerContextQueue directRetirementContexts;
    EarlyProducerLineReadinessLedger directRetirementEarlyLineLedger;
    InactiveProducerLinePayloadCapture inactiveProducerLinePayloadCapture;
    struct InactivePayloadLookup
    {
        HybridConsumerContextQueue::Request request{};
        InactiveProducerLinePayloadCapture::Key key{};
        uint16_t line = 0;
        InactiveProducerLinePayloadCapture::LookupPipeline timing{};
    };
    InactivePayloadLookup inactivePayloadLookup;
    struct InactivePayloadFallback
    {
        bool pending = false;
        HybridConsumerContextQueue::Request request{};
    };
    // One exact proven miss may wait per materializer context.  This fixed
    // table prevents a credit-stalled miss from blocking or overwriting the
    // round-robin lookup work of the other three contexts.
    static_assert(HybridConsumerContextQueue::ContextCount ==
                  InactiveProducerLinePayloadCapture::SlotCount);
    std::array<InactivePayloadFallback,
               HybridConsumerContextQueue::ContextCount>
        inactivePayloadFallbacks{};
    uint8_t nextInactivePayloadFallback = 0;
    std::array<DirectRetirementExecution,
               HybridConsumerContextQueue::ContextCount>
        directRetirementExecutions{};
    std::array<PageMaterializationExecution,
               HybridConsumerContextQueue::ContextCount>
        pageMaterializationExecutions{};
    struct PendingPageZeroPrearm
    {
        InstructionPtr instruction = nullptr;
    };
    // A prearm must acknowledge its MMIO request before the CPU can submit
    // the producer. Keep that request in fixed hardware state until exact
    // producer registration binds its generation and backing allocation.
    std::array<PendingPageZeroPrearm,
               HybridConsumerContextQueue::ContextCount>
        pendingPageZeroPrearms{};
    // Direct-retirement packets bypass the generic OutstandingPacket payload
    // machinery because their storage is one of the fixed queue credits.
    // These finite records keep exact physical-address and full context-owner
    // exclusion without a dynamically growing map or hidden payload store.
    struct DirectRetirementRequestRecord
    {
        bool active = false;
        Addr address = 0;
        HybridConsumerContextQueue::Request request{};
    };
    static constexpr std::size_t DirectRetirementRequestRecordCount =
        HybridConsumerContextQueue::ContextCount *
        HybridConsumerPipeline::LineBufferCount;
    std::array<DirectRetirementRequestRecord,
               DirectRetirementRequestRecordCount>
        directRetirementRequestRecords{};
    DirectRetirementPortRetry<Packet> directRetirementRetryPackets;
    struct PageMaterializationCommit
    {
        bool active = false;
        bool directStaged = false;
        Tick readyTick = 0;
        HybridConsumerContextQueue::Request request{};
        HybridConsumerContextQueue::ContextKey owner{};
        uint16_t line = HybridConsumerPipeline::MaxLines;
    };
    std::array<PageMaterializationCommit,
               DirectRetirementRequestRecordCount>
        pageMaterializationCommits{};
    uint64_t directRetirementTraceOccurrence = 0;
    uint64_t pageMaterializationTraceOccurrence = 0;
    uint64_t pageMaterializationActivationCount = 0;
    std::vector<InstructionPtr> my_instructions;
    uint8_t getTileStatus(InstructionPtr instruction, int tile_id, bool is_dst);
    void issueInstruction();
    void dispatchInstruction();
    void dispatchRegister();
    bool submitTransparentDescriptor(InstructionPtr instruction,
                                     bool directFallback = false);
    bool submitDirectRetirementDescriptor(InstructionPtr instruction);
    enum class PageMaterializationSubmit : uint8_t
    {
        Accepted,
        Retry,
        Fallback,
    };
    bool isTokenBoundPageMaterialization(InstructionPtr instruction) const;
    // Admit no speculative generic dependency: this recognizes only the
    // explicit page-zero prearm ABI marker and binds it to a virtual producer
    // before it can enter the ordinary stream path.
    bool isPageZeroPrearmMaterialization(InstructionPtr instruction) const;
    bool queuePageZeroPrearm(InstructionPtr instruction);
    void activatePendingPageZeroPrearms();
    PageMaterializationSubmit submitPageMaterialization(
        InstructionPtr instruction);
    bool dispatchTransparentMicroOp(
        const TransparentSPDController::Request &request);
    void tryIssueTransparentMicroOp();
    void startTransparentBlockerTracking();
    void updateTransparentBlockerTracking();
    void snapshotTransparentBlockerTracking(uint64_t generation);
    void finishTransparentBlockerTracking(uint64_t generation);
    void emitTransparentMacroSummary(uint64_t generation,
                                     Tick producerRegistrationTick);
    PacketPtr makeDirectRetirementPacket(
        const HybridConsumerContextQueue::Request &request);
    bool recvDirectRetirementTimingResp(PacketPtr pkt,
                                        uint8_t respondingPort);
    static bool sameDirectRetirementKey(
        const HybridConsumerContextQueue::ContextKey &lhs,
        const HybridConsumerContextQueue::ContextKey &rhs);
    static bool sameDirectRetirementRequest(
        const HybridConsumerContextQueue::Request &lhs,
        const HybridConsumerContextQueue::Request &rhs);
    DirectRetirementExecution *findDirectRetirementExecution(
        const HybridConsumerContextQueue::ContextKey &key);
    const DirectRetirementExecution *findDirectRetirementExecution(
        const HybridConsumerContextQueue::ContextKey &key) const;
    DirectRetirementExecution *findDirectRetirementExecution(
        uint16_t tokenTile, uint64_t generation);
    DirectRetirementExecution *firstInactiveDirectRetirementExecution();
    PageMaterializationExecution *findPageMaterializationExecution(
        const HybridConsumerContextQueue::ContextKey &key);
    PageMaterializationExecution *findPageMaterializationExecution(
        uint16_t tokenTile, uint64_t generation);
    PageMaterializationExecution *firstInactivePageMaterializationExecution();
    bool hasDirectRetirementOutstandingAddress(Addr address) const;
    bool hasDirectRetirementOutstandingOwner(
        const HybridConsumerContextQueue::ContextKey &key) const;
    uint16_t directRetirementOutstandingRequestCount() const;
    bool reserveDirectRetirementRequest(
        Addr address, const HybridConsumerContextQueue::Request &request);
    bool releaseDirectRetirementRequest(
        Addr address, const HybridConsumerContextQueue::Request &request);
    void serviceDirectRetirement();
    PacketPtr makePageMaterializationPacket(
        const HybridConsumerContextQueue::Request &request);
    void servicePageMaterialization();
    void schedulePageMaterializationEvent(int latency = 0);
    void finishPageMaterialization(
        const HybridConsumerContextQueue::ContextKey &key);
    bool reservePageMaterializationCommit(
        const HybridConsumerContextQueue::Request &request, Tick readyTick);
    bool reservePageMaterializationDirectCommit(
        const HybridConsumerContextQueue::ContextKey &key, uint16_t line,
        Tick readyTick);
    enum class InactivePayloadLookupStart : uint8_t
    {
        NotApplicable,
        Started,
        ReadPortBusy,
    };
    InactivePayloadLookupStart startInactiveProducerPayloadLookup(
        const HybridConsumerContextQueue::Request &request);
    bool consumeInactiveProducerPayload();
    bool pageMaterializerOwnsDestination(int maaID, int firstTile,
                                        int wordBytes) const;
    void scheduleDirectRetirementEvent(int latency = 0);
    void notifyDirectRetirementPortEvent(uint8_t port);
    void finishDirectRetirement(
        const HybridConsumerContextQueue::ContextKey &key);
    struct LogicalSPDSenderState : public Packet::SenderState
    {
        LogicalSPDCacheGem5Bridge::CallbackToken token{};
        const LogicalSPDCacheGem5Bridge::Runtime::Transport::
            RequestIdentity *request = nullptr;
        const LogicalSPDCacheGem5Bridge::Runtime::Transport::RouteToken
            *route = nullptr;
        uint32_t packetIncarnation = 0;
        uint32_t requestIncarnation = 0;
        uint8_t tokenDepth = 0;
        uint8_t tokenRecord = 0;
        uint16_t tokenEpoch = 0;
        uint32_t tokenActionID = 0;
        uint8_t callbackPort = 0;
        Addr logicalAddress = 0;
        uint16_t size = 0;
        LogicalSPDCacheGem5Bridge::Runtime::Transport::Command command =
            LogicalSPDCacheGem5Bridge::Runtime::Transport::Command::ReadReq;
    };
    struct LogicalSPDExecution
    {
        bool active = false;
        LogicalSPDCacheGem5Bridge::CallbackToken token{};
        PacketPtr completionPacket = nullptr;
        PacketPtr retryPacket = nullptr;
        uint8_t retryPort = 0;
        LogicalSPDCacheLiveAdapterState::Owner liveOwner =
            LogicalSPDCacheLiveAdapterState::NoOwner;
        LogicalSPDCacheLiveAdapterState::WaitAuthority retryAuthority =
            LogicalSPDCacheLiveAdapterState::WaitAuthority::None;
        int coreID = -1;
        ContextID contextID = InvalidContextID;
        Addr pc = 0;
    };
    std::vector<LogicalSPDExecution> logicalSpdExecutions;
    LogicalSPDCacheLiveAdapterState logicalSpdLiveBoundary;
    bool submitLogicalSPDDescriptor(
        InstructionPtr instruction, PacketPtr completionPacket);
    PacketPtr makeLogicalSPDPacket(
        LogicalSPDExecution &execution,
        const LogicalSPDCacheGem5Bridge::Runtime::Transport::RequestPacket
            &request);
    bool recvLogicalSPDTimingResp(PacketPtr pkt, uint8_t respondingPort);
    void serviceLogicalSPD();
    void scheduleLogicalSPDEvent(int latency = 0);
    void notifyLogicalSPDPortEvent(
        uint8_t actualPort,
        LogicalSPDCacheLiveAdapterState::PortEvent event);
    void notifyLogicalSPDResponse();
    DrainState drain() override;
    void drainResume() override;
    EventFunctionWrapper logicalSpdEvent, directRetirementEvent,
        pageMaterializationEvent;
    EventFunctionWrapper issueInstructionEvent, dispatchInstructionEvent,
        dispatchRegisterEvent;
    void scheduleDispatchInstructionEvent(int latency = 0);
    void scheduleDispatchRegisterEvent(int latency = 0);
    bool *streamAccessIdle;
    bool *indirectAccessIdle;
    bool *aluUnitsIdle;
    bool *rangeUnitsIdle;
    bool invalidatorIdle;
    std::unique_ptr<Packet> pendingDelete;

public:
    Tick my_last_idle_tick;
    Tick my_last_reset_tick;
    bool allFuncUnitsIdle();
    Tick getCurTick();

public:
    struct MAAStats : public statistics::Group {
        MAAStats(statistics::Group *parent, int num_indirect_units, MAA *_maa);

        MAA *maa;
        void preDumpStats() override;

        /** Number of instructions. */
        statistics::Scalar numInst_INDRD;
        statistics::Scalar numInst_INDWR;
        statistics::Scalar numInst_INDRMW;
        statistics::Scalar numInst_STRRD;
        statistics::Scalar numInst_STRWR;
        statistics::Scalar numInst_RANGE;
        statistics::Scalar numInst_ALUS;
        statistics::Scalar numInst_ALUV;
        statistics::Scalar numInst_ALUR;
        statistics::Scalar numInst_INV;
        statistics::Scalar numInst;

        /** Cycles of instructions. */
        statistics::Scalar cycles_INDRD;
        statistics::Scalar cycles_INDWR;
        statistics::Scalar cycles_INDRMW;
        statistics::Scalar cycles_STRRD;
        statistics::Scalar cycles_STRWR;
        statistics::Scalar cycles_RANGE;
        statistics::Scalar cycles_ALUS;
        statistics::Scalar cycles_ALUV;
        statistics::Scalar cycles_ALUR;
        statistics::Scalar cycles_INV;
        statistics::Scalar cycles_IDLE;
        statistics::Formula cycles_BUSY;
        statistics::Scalar cycles_TOTAL;
        statistics::Scalar cycles;

        /** Average cycles per instruction. */
        statistics::Formula avgCPI_INDRD;
        statistics::Formula avgCPI_INDWR;
        statistics::Formula avgCPI_INDRMW;
        statistics::Formula avgCPI_STRRD;
        statistics::Formula avgCPI_STRWR;
        statistics::Formula avgCPI_RANGE;
        statistics::Formula avgCPI_ALUS;
        statistics::Formula avgCPI_ALUV;
        statistics::Formula avgCPI_ALUR;
        statistics::Formula avgCPI_INV;
        statistics::Formula avgCPI;

        /** Port statistics */
        statistics::Scalar port_cache_WR_packets;
        statistics::Scalar port_cache_RD_packets;
        statistics::Scalar port_mem_WR_packets;
        statistics::Scalar port_mem_RD_packets;
        statistics::Scalar cpu_spd_data_read_deferrals;
        statistics::Scalar cpu_spd_data_read_retry_signals;
        statistics::Scalar cpu_spd_data_read_retry_attempts;
        statistics::Scalar cpu_spd_data_read_retry_acceptances;
        statistics::Scalar virtual_page_ready_signals;
        statistics::Scalar virtual_page_wait_reads;
        statistics::Scalar virtual_page_wait_deferrals;
        statistics::Scalar virtual_page_wait_responses;
        statistics::Scalar virtual_retirement_native_deferrals;
        statistics::Scalar virtual_retirement_queue_deferrals;
        statistics::Scalar direct_retirement_descriptors;
        statistics::Scalar direct_retirement_producer_acks;
        statistics::Scalar direct_retirement_producer_line_acks;
        statistics::Scalar direct_retirement_early_line_overflows;
        statistics::Scalar direct_retirement_page_fallback_lines;
        statistics::Scalar direct_retirement_read_issues;
        statistics::Scalar direct_retirement_read_responses;
        statistics::Scalar direct_retirement_alu_issues;
        statistics::Scalar direct_retirement_alu_completions;
        statistics::Scalar direct_retirement_write_issues;
        statistics::Scalar direct_retirement_write_responses;
        statistics::Scalar direct_retirement_credit_high_water;
        statistics::Scalar direct_retirement_credit_stalls;
        statistics::Scalar direct_retirement_address_stalls;
        statistics::Scalar direct_retirement_retries;
        statistics::Scalar direct_retirement_overlap_ticks;
        statistics::Scalar direct_retirement_active_stage_high_water;
        statistics::Scalar direct_retirement_context_high_water;
        statistics::Scalar direct_retirement_context_full_stalls;
        statistics::Scalar direct_retirement_request_record_high_water;
        statistics::Scalar direct_retirement_fallbacks;
        statistics::Scalar direct_retirement_payload_bytes;
        statistics::Scalar direct_retirement_control_bytes;
        statistics::Scalar page_materialization_submissions;
        statistics::Scalar page_materialization_pages;
        statistics::Scalar page_materialization_retirements;
        statistics::Scalar page_materialization_forwarded_lines;
        statistics::Scalar page_materialization_fragment_accumulated_lines;
        statistics::Scalar page_materialization_fragment_buffer_stalls;
        statistics::Scalar page_materialization_inactive_payload_captures;
        statistics::Scalar page_materialization_inactive_payload_replays;
        statistics::Scalar page_materialization_inactive_payload_conflicts;
        statistics::Scalar page_materialization_inactive_payload_drops;
        statistics::Scalar
            page_materialization_inactive_payload_first_owner_conflicts;
        statistics::Scalar
            page_materialization_inactive_payload_latest_owner_overwrites;
        statistics::Scalar
            page_materialization_inactive_payload_latest_owner_evictions;
        statistics::Scalar
            page_materialization_inactive_payload_write_port_stalls;
        statistics::Scalar
            page_materialization_inactive_payload_read_port_stalls;
        statistics::Scalar page_materialization_inactive_payload_lookup_hits;
        statistics::Scalar page_materialization_inactive_payload_lookup_misses;
        statistics::Scalar page_materialization_inactive_payload_high_water;
        statistics::Scalar page_materialization_inactive_payload_bytes;
        statistics::Scalar page_materialization_inactive_payload_control_bytes;
        statistics::Scalar page_materialization_cache_read_fallback_lines;
        statistics::Scalar page_materialization_dispatch_fallbacks;
        statistics::Scalar page_materialization_admission_fallbacks;
        statistics::Scalar page_materialization_producer_line_acks;
        statistics::Scalar page_materialization_page_fallback_lines;
        statistics::Scalar page_materialization_staged_direct_lines;
        statistics::Scalar page_materialization_staged_direct_fragments;
        statistics::Scalar page_materialization_staged_direct_fallback_lines;
        // Smart writeback queue (Phase 0 instrumentation): number of indirect
        // writebacks issued to a DRAM row already left open by the previous
        // write to that bank. rowhit / WR_packets = MAA-side write row-hit rate.
        statistics::Scalar port_mem_WR_rowhit;
        statistics::Formula port_cache_packets;
        statistics::Formula port_mem_packets;
        statistics::Formula port_cache_WR_BW;
        statistics::Formula port_cache_RD_BW;
        statistics::Formula port_cache_BW;
        statistics::Formula port_mem_WR_BW;
        statistics::Formula port_mem_RD_BW;
        statistics::Formula port_mem_BW;

        /** Indirect Unit -- Row-Table Statistics. */
        std::vector<statistics::Scalar *> IND_NumInsts;
        std::vector<statistics::Scalar *> IND_NumWordsInserted;
        std::vector<statistics::Scalar *> IND_NumCacheLineInserted;
        std::vector<statistics::Scalar *> IND_NumRowsInserted;
        std::vector<statistics::Scalar *> IND_NumUniqueWordsInserted;
        std::vector<statistics::Scalar *> IND_NumUniqueCacheLineInserted;
        std::vector<statistics::Scalar *> IND_NumUniqueRowsInserted;
        std::vector<statistics::Scalar *> IND_NumRTFull;
        std::vector<statistics::Scalar *> IND_NumOTFull;
        std::vector<statistics::Scalar *> IND_NumOTEpochDrain;
        std::vector<statistics::Formula *> IND_AvgWordsPerCacheLine;
        std::vector<statistics::Formula *> IND_AvgCacheLinesPerRow;
        std::vector<statistics::Formula *> IND_AvgRowsPerInst;
        std::vector<statistics::Formula *> IND_AvgUniqueWordsPerCacheLine;
        std::vector<statistics::Formula *> IND_AvgUniqueCacheLinesPerRow;
        std::vector<statistics::Formula *> IND_AvgUniqueRowsPerInst;
        std::vector<statistics::Formula *> IND_AvgRTFullsPerInst;

        /** Indirect Unit -- Cycles of stages. */
        std::vector<statistics::Scalar *> IND_CyclesFill;
        std::vector<statistics::Scalar *> IND_CyclesBuild;
        std::vector<statistics::Scalar *> IND_CyclesRequest;
        std::vector<statistics::Scalar *> IND_VirtRequestCyclesBuild;
        std::vector<statistics::Scalar *> IND_VirtRequestCyclesSourceFlight;
        std::vector<statistics::Scalar *> IND_VirtRequestCyclesRetained;
        std::vector<statistics::Scalar *> IND_VirtRequestCyclesWrites;
        std::vector<statistics::Scalar *> IND_VirtRequestCyclesFinalDrain;
        std::vector<statistics::Scalar *> IND_VirtRequestCyclesRunnable;
        std::vector<statistics::Scalar *> IND_VirtPipelineCyclesIdle;
        std::vector<statistics::Scalar *> IND_VirtPipelineCyclesSourceOnly;
        std::vector<statistics::Scalar *> IND_VirtPipelineCyclesWriteOnly;
        std::vector<statistics::Scalar *> IND_VirtPipelineCyclesOverlap;
        std::vector<statistics::Scalar *> IND_VirtBuildRounds;
        std::vector<statistics::Scalar *> IND_VirtResponseSlotHighWater;
        std::vector<statistics::Scalar *> IND_VirtResponseWordHighWater;
        std::vector<statistics::Scalar *> IND_VirtResponseWordPoolStalls;
        std::vector<statistics::Scalar *> IND_VirtOutstandingWriteHighWater;
        std::vector<statistics::Scalar *> IND_VirtCombineLineHighWater;
        std::vector<statistics::Scalar *> IND_VirtCombineWordHighWater;
        std::vector<statistics::Scalar *> IND_VirtFullLineWrites;
        std::vector<statistics::Scalar *> IND_VirtPartialWrites;
        std::vector<statistics::Scalar *> IND_VirtPageOrderedDrainSelections;
        std::vector<statistics::Scalar *> IND_VirtPageOrderedDrainDeferrals;
        std::vector<statistics::Scalar *> IND_VirtCombineBankAccesses;
        std::vector<statistics::Scalar *> IND_VirtCombineBankConflictCycles;
        std::vector<statistics::Scalar *> IND_VirtWriteIssues;
        std::vector<statistics::Scalar *> IND_VirtWriteCompletions;
        std::vector<statistics::Scalar *> IND_VirtWriteAddressConflicts;
        std::vector<statistics::Scalar *> IND_VirtIdealizedAckPages;
        std::vector<statistics::Scalar *> IND_VirtPagesReady;
        std::vector<statistics::Scalar *> IND_VirtPagesReadyBeforeSourceDrain;
        std::vector<statistics::Scalar *> IND_VirtFirstPageReadyCycles;
        std::vector<statistics::Scalar *> IND_VirtAllPagesReadyCycles;
        std::vector<statistics::Scalar *> IND_VirtPageReadySpanCycles;
        std::vector<statistics::Scalar *> IND_VirtIndexLineReads;
        std::vector<statistics::Scalar *> IND_VirtIndexOutstandingMerges;
        std::vector<statistics::Scalar *> IND_VirtIndexOutstandingWaitCycles;
        std::vector<statistics::Scalar *> IND_VirtIndexLineHighWater;
        std::vector<statistics::Scalar *> IND_VirtIndexWords;
        std::vector<statistics::Scalar *> IND_VirtIndexWordHighWater;
        std::vector<statistics::Scalar *> IND_VirtIndexFilterWords;
        std::vector<statistics::Scalar *> IND_VirtIndexFilterCycles;
        std::vector<statistics::Scalar *> IND_VirtIndexFilterWaitEvents;
        std::vector<statistics::Scalar *> IND_VirtIndexFilterWaitCycles;
        std::vector<statistics::Scalar *> IND_BoundedSummaryLineReads;
        std::vector<statistics::Scalar *> IND_BoundedSummaryWords;
        std::vector<statistics::Scalar *> IND_BoundedSummaryRecords;
        std::vector<statistics::Scalar *> IND_BoundedSummaryHashProbes;
        std::vector<statistics::Scalar *> IND_BoundedSummaryReductionVisits;
        std::vector<statistics::Scalar *> IND_BoundedSummaryPlanBytes;
        std::vector<statistics::Scalar *> IND_BoundedBucketLineReads;
        std::vector<statistics::Scalar *> IND_BoundedBucketWords;
        std::vector<statistics::Scalar *>
            IND_DescriptorSpoolFilterRetryInspections;
        std::vector<statistics::Scalar *>
            IND_DescriptorSpoolFinalFlushStalls;
        std::vector<statistics::Scalar *> IND_DescriptorSpoolBScans;
        std::vector<statistics::Scalar *>
            IND_DescriptorSpoolResidentPopulations;
        std::vector<statistics::Scalar *>
            IND_DescriptorSpoolResidentDescriptors;
        std::vector<statistics::Scalar *>
            IND_DescriptorSpoolExternalDescriptors;
        std::vector<statistics::Scalar *>
            IND_DescriptorSpoolExternalSegments;
        std::vector<statistics::Scalar *> IND_BoundedReplayLineReads;
        std::vector<statistics::Scalar *> IND_BoundedReplayWords;
        std::vector<statistics::Scalar *> IND_BoundedReplayPasses;
        std::vector<statistics::Scalar *> IND_BoundedReplayDrains;
        std::vector<statistics::Scalar *> IND_BoundedReplayMaxEpochAdmissions;
        std::vector<statistics::Scalar *> IND_BoundedWordEntries;
        std::vector<statistics::Scalar *> IND_BoundedOffsetLinkEntries;
        std::vector<statistics::Scalar *> IND_BoundedRowDirectoryEntries;
        std::vector<statistics::Scalar *> IND_BoundedRowLineEntries;
        std::vector<statistics::Scalar *> IND_BoundedReorderMetadataBytes;
        std::vector<statistics::Scalar *> IND_DescriptorSpoolLineWrites;
        std::vector<statistics::Scalar *> IND_DescriptorSpoolWriteBytes;
        std::vector<statistics::Scalar *> IND_DescriptorSpoolWriteAcks;
        std::vector<statistics::Scalar *> IND_DescriptorSpoolLineReads;
        std::vector<statistics::Scalar *> IND_DescriptorSpoolReadBytes;
        std::vector<statistics::Scalar *> IND_DescriptorSpoolWriteCreditStalls;
        std::vector<statistics::Scalar *> IND_DescriptorSpoolReadCreditStalls;
        std::vector<statistics::Scalar *> IND_DescriptorSpoolWriteHighWater;
        std::vector<statistics::Scalar *>
            IND_DescriptorSpoolOverlapOpportunities;
        std::vector<statistics::Scalar *>
            IND_DescriptorSpoolNextPassReadIssues;
        std::vector<statistics::Scalar *>
            IND_DescriptorSpoolNextPassReadResponses;
        std::vector<statistics::Scalar *>
            IND_DescriptorSpoolUsefulPrefetchedLines;
        std::vector<statistics::Scalar *>
            IND_DescriptorSpoolDemandWaitsAvoided;
        std::vector<statistics::Scalar *>
            IND_DescriptorSpoolPrefetchOccupancyLineCycles;
        std::vector<statistics::Scalar *>
            IND_DescriptorSpoolPrefetchOccupancyHighWater;
        std::vector<statistics::Scalar *>
            IND_DescriptorSpoolWastedPrefetchedLines;
        std::vector<statistics::Scalar *>
            IND_DescriptorSpoolBoundaryDemandWaitEvents;
        std::vector<statistics::Scalar *>
            IND_DescriptorSpoolBoundaryDemandWaitCycles;
        std::vector<statistics::Scalar *>
            IND_DescriptorSpoolWithinPassDemandWaitEvents;
        std::vector<statistics::Scalar *>
            IND_DescriptorSpoolWithinPassDemandWaitCycles;
        std::vector<statistics::Scalar *> IND_DescriptorSpoolStagingEntries;
        std::vector<statistics::Scalar *> IND_DescriptorSpoolControlBytes;
        std::vector<statistics::Scalar *> IND_DescriptorSpoolBackingBytes;
        std::vector<statistics::Scalar *> IND_BoundedGlobalMergePopulations;
        std::vector<statistics::Scalar *> IND_BoundedGlobalMergeActiveHWM;
        std::vector<statistics::Scalar *>
            IND_BoundedGlobalMergeDescriptorRecords;
        std::vector<statistics::Scalar *>
            IND_BoundedGlobalMergeDescriptorBytes;
        std::vector<statistics::Scalar *> IND_BoundedGlobalMergeSortReadLines;
        std::vector<statistics::Scalar *>
            IND_BoundedGlobalMergeSortedWriteLines;
        std::vector<statistics::Scalar *>
            IND_BoundedGlobalMergeSortComparisons;
        std::vector<statistics::Scalar *> IND_BoundedGlobalMergeMergeReadLines;
        std::vector<statistics::Scalar *>
            IND_BoundedGlobalMergeMergeComparisons;
        std::vector<statistics::Scalar *> IND_BoundedGlobalMergeMergeHeadHWM;
        std::vector<statistics::Scalar *> IND_BoundedGlobalMergeALineIssues;
        std::vector<statistics::Scalar *> IND_BoundedGlobalMergeCoalesced;
        std::vector<statistics::Scalar *> IND_BoundedGlobalMergeRowGroups;
        std::vector<statistics::Scalar *> IND_BoundedGlobalMergeAdmissions;
        std::vector<statistics::Scalar *> IND_BoundedGlobalMergeRetirements;
        std::vector<statistics::Scalar *> IND_BoundedGlobalMergeRunWriteAcks;
        std::vector<statistics::Scalar *> IND_BoundedGlobalMergeTerminalAcks;
        std::vector<statistics::Scalar *> IND_BoundedGlobalMergeFallbacks;
        std::vector<statistics::Scalar *> IND_BoundedGlobalMergeControlBytes;
        std::vector<statistics::Scalar *> IND_BoundedGlobalMergeBackingBytes;
        std::vector<statistics::Scalar *> IND_CyclesRTAccess;
        std::vector<statistics::Scalar *> IND_CyclesSPDReadAccess;
        std::vector<statistics::Scalar *> IND_CyclesSPDWriteAccess;
        std::vector<statistics::Formula *> IND_AvgCyclesFillPerInst;
        std::vector<statistics::Formula *> IND_AvgCyclesBuildPerInst;
        std::vector<statistics::Formula *> IND_AvgCyclesRequestPerInst;
        std::vector<statistics::Formula *> IND_AvgCyclesRTAccessPerInst;
        std::vector<statistics::Formula *> IND_AvgCyclesSPDReadAccessPerInst;
        std::vector<statistics::Formula *> IND_AvgCyclesSPDWriteAccessPerInst;

        /** Indirect Unit -- Load accesses. */
        std::vector<statistics::Scalar *> IND_LoadsCacheHitResponding;
        std::vector<statistics::Scalar *> IND_LoadsCacheHitAccessing;
        std::vector<statistics::Scalar *> IND_LoadsMemAccessing;
        std::vector<statistics::Scalar *> IND_LoadsCacheHitRespondingLatency;
        std::vector<statistics::Scalar *> IND_LoadsCacheHitAccessingLatency;
        std::vector<statistics::Scalar *> IND_LoadsMemAccessingLatency;
        std::vector<statistics::Formula *> IND_AvgLoadsCacheHitRespondingPerInst;
        std::vector<statistics::Formula *> IND_AvgLoadsCacheHitAccessingPerInst;
        std::vector<statistics::Formula *> IND_AvgLoadsMemAccessingPerInst;
        std::vector<statistics::Formula *> IND_AvgLoadsCacheHitRespondingLatency;
        std::vector<statistics::Formula *> IND_AvgLoadsCacheHitAccessingLatency;
        std::vector<statistics::Formula *> IND_AvgLoadsMemAccessingLatency;

        /** Indirect Unit -- Store accesses. */
        std::vector<statistics::Scalar *> IND_StoresMemAccessing;
        std::vector<statistics::Formula *> IND_AvgStoresMemAccessingPerInst;

        /** Indirect Unit -- Evict accesses. */
        std::vector<statistics::Scalar *> IND_Evicts;
        std::vector<statistics::Formula *> IND_AvgEvictssPerInst;

        /** Stream Unit -- Row-Table Statistics. */
        std::vector<statistics::Scalar *> STR_NumInsts;
        std::vector<statistics::Scalar *> STR_NumWordsInserted;
        std::vector<statistics::Scalar *> STR_NumCacheLineInserted;
        std::vector<statistics::Scalar *> STR_NumRTFull;
        std::vector<statistics::Formula *> STR_AvgWordsPerCacheLine;
        std::vector<statistics::Formula *> STR_AvgCacheLinesPerInst;
        std::vector<statistics::Formula *> STR_AvgRTFullsPerInst;

        /** Stream Unit -- Cycles of stages. */
        std::vector<statistics::Scalar *> STR_CyclesRequest;
        std::vector<statistics::Scalar *> STR_CyclesRTAccess;
        std::vector<statistics::Scalar *> STR_CyclesSPDReadAccess;
        std::vector<statistics::Scalar *> STR_CyclesSPDWriteAccess;
        std::vector<statistics::Formula *> STR_AvgCyclesRequestPerInst;
        std::vector<statistics::Formula *> STR_AvgCyclesRTAccessPerInst;
        std::vector<statistics::Formula *> STR_AvgCyclesSPDReadAccessPerInst;
        std::vector<statistics::Formula *> STR_AvgCyclesSPDWriteAccessPerInst;

        /** Stream Unit -- Load accesses. */
        std::vector<statistics::Scalar *> STR_LoadsCacheAccessing;
        std::vector<statistics::Formula *> STR_AvgLoadsCacheAccessingPerInst;

        /** Stream Unit -- Evict accesses. */
        std::vector<statistics::Scalar *> STR_Evicts;
        std::vector<statistics::Formula *> STR_AvgEvictssPerInst;

        /** Range Fuser Unit -- Cycles of stages. */
        std::vector<statistics::Scalar *> RNG_NumInsts;
        std::vector<statistics::Scalar *> RNG_CyclesCompute;
        std::vector<statistics::Scalar *> RNG_CyclesSPDReadAccess;
        std::vector<statistics::Scalar *> RNG_CyclesSPDWriteAccess;
        std::vector<statistics::Formula *> RNG_AvgCyclesComputePerInst;
        std::vector<statistics::Formula *> RNG_AvgCyclesSPDReadAccessPerInst;
        std::vector<statistics::Formula *> RNG_AvgCyclesSPDWriteAccessPerInst;

        /** ALU Unit -- Cycles of stages. */
        std::vector<statistics::Scalar *> ALU_NumInsts;
        std::vector<statistics::Scalar *> ALU_NumInstsCompare;
        std::vector<statistics::Scalar *> ALU_NumInstsCompute;
        std::vector<statistics::Scalar *> ALU_CyclesCompute;
        std::vector<statistics::Scalar *> ALU_CyclesSPDReadAccess;
        std::vector<statistics::Scalar *> ALU_CyclesSPDWriteAccess;
        std::vector<statistics::Formula *> ALU_AvgCyclesComputePerInst;
        std::vector<statistics::Formula *> ALU_AvgCyclesSPDReadAccessPerInst;
        std::vector<statistics::Formula *> ALU_AvgCyclesSPDWriteAccessPerInst;

        /** ALU Unit -- Comparison Info. */
        std::vector<statistics::Scalar *> ALU_NumComparedWords;
        std::vector<statistics::Scalar *> ALU_NumTakenWords;
        std::vector<statistics::Formula *> ALU_AvgNumTakenWordsPerComparedWords;

        /** ALU Unit -- Comparison Info. */
        statistics::Scalar *INV_NumInvalidatedCachelines;
        statistics::Formula *INV_AvgInvalidatedCachelinesPerInst;

    } stats;

protected:
    struct pair_hash {
        template <class T1, class T2>
        std::size_t operator()(const std::pair<T1, T2> &p) const {
            return std::hash<T1>{}(p.first) ^ (std::hash<T2>{}(p.second) << 1);
        }
    };
    class OutstandingPacket {
    public:
        PacketPtr packet;
        Addr paddr;
        Tick tick;
        MemCmd cmd;
        bool cached;
        bool virtualRetirement;
        bool sent;
        std::vector<int> maaIDs;
        std::vector<FuncUnitType> funcUnits;
        OutstandingPacket(PacketPtr _packet, Addr _paddr, Tick _tick, MemCmd _cmd)
            : packet(_packet), paddr(_paddr), tick(_tick), cmd(_cmd),
              cached(false), virtualRetirement(false), sent(false) {}
        OutstandingPacket() {}
        OutstandingPacket(const OutstandingPacket &other) {
            packet = other.packet;
            paddr = other.paddr;
            tick = other.tick;
            cmd = other.cmd;
            funcUnits = other.funcUnits;
            maaIDs = other.maaIDs;
            sent = other.sent;
            cached = other.cached;
            virtualRetirement = other.virtualRetirement;
        }
        bool operator<(const OutstandingPacket &rhs) const {
            return tick < rhs.tick;
        }
        OutstandingPacket &operator=(const OutstandingPacket &other) = default;
    };
    struct CompareByTick {
        bool operator()(const OutstandingPacket &lhs, const OutstandingPacket &rhs) const {
            return lhs.tick < rhs.tick;
        }
    };
    struct DeferredPacket {
        FuncUnitType funcUnit;
        int maaID;
        PacketPtr packet;
        Tick tick;
        bool forceCache;
        bool forceRetirementCache;
    };
    std::multiset<OutstandingPacket, CompareByTick> *my_outstanding_indirect_cache_read_pkts;
    std::multiset<OutstandingPacket, CompareByTick> *my_outstanding_indirect_cache_write_pkts;
    std::multiset<OutstandingPacket, CompareByTick> *my_outstanding_indirect_mem_write_pkts;
    std::multiset<OutstandingPacket, CompareByTick> *my_outstanding_indirect_mem_read_pkts;
    // Smart writeback queue: last DRAM row issued to each bank, per channel.
    // Used to pick a ready writeback that keeps an already-open row open
    // (row-buffer hit) instead of draining in pure read-return order.
    std::unordered_map<uint64_t, Addr> *my_writeback_last_row;
    // Decompose a paddr into (bank_key, row) for the per-bank open-row tracker.
    void writeRowKey(Addr paddr, uint64_t &bank_key, Addr &row);
    std::multiset<OutstandingPacket, CompareByTick> *my_outstanding_stream_cache_read_pkts;
    std::multiset<OutstandingPacket, CompareByTick> *my_outstanding_stream_cache_write_pkts;
    std::multiset<OutstandingPacket, CompareByTick> *my_outstanding_stream_mem_write_pkts;
    std::multiset<OutstandingPacket, CompareByTick> *my_outstanding_stream_mem_read_pkts;
    std::unordered_map<Addr, OutstandingPacket> my_outstanding_pkt_map;
    std::unordered_map<Addr, std::deque<DeferredPacket>>
        my_deferred_pkt_map;
    uint32_t *my_num_outstanding_indirect_pkts;
    uint32_t *my_num_outstanding_stream_pkts;
    bool allIndirectEmpty();
    bool scheduleNextSendCache();
    bool scheduleNextSendMem();
    void scheduleSendCacheEvent(int latency = 0);
    void scheduleSendMemEvent(int latency = 0);
    bool sendOutstandingCachePacket();
    bool sendOutstandingMemPacket();
    void sendNextDeferredPacket(Addr paddr);
    EventFunctionWrapper sendCacheEvent;
    EventFunctionWrapper sendMemEvent;
    bool *mem_channels_blocked;
    bool *cache_bus_blocked;
    void unblockMemChannel(int channel_id);
    void unblockCache(int core_id);

public:
    void sendPacket(FuncUnitType funcUnit, int maaID, PacketPtr pkt, Tick tick,
                    bool force_cache = false,
                    bool force_retirement_cache = false,
                    bool bypass_deferred_queue = false);
    bool hasOutstandingPacket(Addr paddr) const {
        return my_outstanding_pkt_map.find(paddr) !=
               my_outstanding_pkt_map.end();
    }
    bool canCoalesceOutstandingRead(Addr paddr, FuncUnitType func_unit,
                                    int maa_id) const;
    bool allIndirectPacketsSent(int maaID);
    bool allStreamPacketsSent(int maaID);
};
/**
 * Returns the address of the closest aligned fixed-size block to the given
 * address.
 * @param addr Input address.
 * @param block_size Block size in bytes.
 * @return Address of the closest aligned block.
 */
inline Addr addrBlockAligner(Addr addr, Addr block_size) {
    return addr & ~(block_size - 1);
}
inline int getCeiling(int a, int b) {
    return (a + b - 1) / b;
}
} // namespace gem5

#endif //__MEM_MAA_MAA_HH__
