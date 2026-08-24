#ifndef __MEM_MAA_STREAMACCESS_HH__
#define __MEM_MAA_STREAMACCESS_HH__

#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>

#include "arch/generic/mmu.hh"
#include "base/types.hh"
#include "mem/MAA/IF.hh"
#include "mem/MAA/ResponseBearingSpdPublisher.hh"
#include "mem/MAA/Tables.hh"
#include "mem/packet.hh"
#include "mem/request.hh"
#include "sim/system.hh"

namespace gem5 {

class MAA;

class StreamAccessUnit : public BaseMMU::Translation {
public:
    // IF intentionally knows only the legacy STREAM_ST shape.  MAA replaces
    // the guarded guest tdst1 with this bounded internal tag while the
    // instruction is resident in IF, then restores the completion identity
    // for terminal dependency wakeup.
    static constexpr uint64_t ResponseBearingPublishInstructionTag =
        0x525350445055424cULL;

    static bool isResponseBearingPublishInstruction(
        const Instruction *instruction) {
        if (instruction == nullptr ||
            (instruction->controllerManaged &&
             !instruction->logicalPageManaged) ||
            instruction->opcode != Instruction::OpcodeType::STREAM_ST)
            return false;
        if (instruction->logicalPageManaged)
            return instruction->controllerDstSlot != -1;
        return instruction->dst1SpdID != -1 ||
            (instruction->controllerTransactionID ==
                 ResponseBearingPublishInstructionTag &&
             instruction->controllerDstSlot != -1);
    }

    static int responseBearingPublishCompletionTile(
        const Instruction *instruction) {
        if (!isResponseBearingPublishInstruction(instruction))
            return -1;
        return instruction->dst1SpdID != -1
            ? instruction->dst1SpdID
            : instruction->controllerDstSlot;
    }

    // FP32 uses one 16 KiB byte chunk and FP64 uses two.  Both geometries
    // share these exact eight payload credits; there is no second datatype-
    // specific payload bank.
    using ResponsePublisher =
        ResponseBearingSpdPublisher<sizeof(uint32_t), 2, 8>;

    struct ResponseBearingPublishSenderState : public Packet::SenderState
    {
        ResponsePublisher::Identity identity{};
        int streamID = -1;
        uint64_t guestGeneration = 0;
        uint32_t logicalPage = 0;
        uint32_t logicalElementOffset = 0;
        Addr physicalAddress = 0;
        bool transportAccepted = false;
        uint32_t retryCount = 0;
    };

    struct ResponseBearingPublishAttempt
    {
        ResponsePublisher::Identity identity{};
        uint64_t guestGeneration = 0;
        uint32_t logicalPage = 0;
        Addr physicalAddress = 0;
        uint32_t retryCount = 0;
    };

    enum class Status : uint8_t
    {
        Idle = 0,
        Decode = 1,
        Request = 2,
        Response = 3,
        max
    };

protected:
    std::string status_names[5] = {
        "Idle",
        "Decode",
        "Request",
        "Response",
        "max"};
    class PageInfo {
    public:
        int max_itr, bg_addr, curr_itr, curr_idx;
        Addr last_block_vaddr;
        PageInfo(int _min_itr, int _min_idx, int _bg_addr)
            : max_itr(-1), bg_addr(_bg_addr),
              curr_itr(_min_itr), curr_idx(_min_idx), last_block_vaddr(0) {}
        PageInfo(const PageInfo &other) {
            max_itr = other.max_itr;
            bg_addr = other.bg_addr;
            curr_itr = other.curr_itr;
            curr_idx = other.curr_idx;
            last_block_vaddr = other.last_block_vaddr;
        }
        std::string print() const {
            return "Page(max_itr[" + std::to_string(max_itr) +
                   "], bg_addr: [" + std::to_string(bg_addr) +
                   "], curr_itr: [" + std::to_string(curr_itr) +
                   "], curr_idx: [" + std::to_string(curr_idx) +
                   "])";
        }
        bool operator<(const PageInfo &rhs) const {
            return curr_itr < rhs.curr_itr;
        }
    };
    struct CompareByItr {
        bool operator()(const PageInfo &lhs, const PageInfo &rhs) const {
            return lhs.curr_itr < rhs.curr_itr;
        }
    };
    std::multiset<PageInfo, CompareByItr> my_all_page_info;
    std::vector<PageInfo> my_current_page_info;
    unsigned int num_tile_elements;
    unsigned int num_request_table_addresses;
    unsigned int num_request_table_entries_per_address;
    Status state;
    RequestTable *request_table;
    int dst_tile_id;

public:
    StreamAccessUnit();
    ~StreamAccessUnit() {
        if (request_table != nullptr) {
            delete request_table;
        }
    }
    void allocate(int _my_stream_id, unsigned int _num_request_table_addresses, unsigned int _num_request_table_entries_per_address, unsigned int _num_tile_elements, MAA *_maa);

    Status getState() const { return state; }

    void setInstruction(Instruction *_instruction);

    Cycles updateLatency(int num_spd_condread_accesses,
                         int num_spd_srcread_accesses,
                         int num_spd_write_accesses,
                         int num_requesttable_accesses);
    bool scheduleNextExecution(bool force = false);
    void scheduleExecuteInstructionEvent(int latency = 0);
    bool recvData(const Addr addr, uint8_t *dataptr);
    void writePacketSent(Addr addr, bool transportAccepted = false);
    void readPacketSent(Addr addr);
    bool isResponseBearingPublishPacket(PacketPtr pkt) const;
    ResponseBearingPublishAttempt responseBearingPublishPacketAttempt(
        PacketPtr pkt);
    void responseBearingPublishPacketAccepted(
        const ResponseBearingPublishAttempt &attempt);
    void responseBearingPublishPacketRetried(PacketPtr pkt);
    void responseBearingPublishWriteResponse(PacketPtr pkt);
    bool responseBearingPublisherQuiescent() const {
        return !my_response_bearing_publish && !response_publisher.active();
    }

    /* Related to BaseMMU::Translation Inheretance */
    void markDelayed() override {}
    void finish(const Fault &fault, const RequestPtr &req,
                ThreadContext *tc, BaseMMU::Mode mode) override;
    MAA *maa;

protected:
    Instruction *my_instruction;
    bool my_is_load;
    Request::Flags flags = 0;
    const Addr block_size = 64;
    const Addr page_size = 4096;
    Addr my_base_addr, my_min_addr, my_max_addr;
    int8_t my_addr_range_id;
    int my_src_tile, my_dst_tile, my_cond_tile, my_min, my_max, my_stride;
    int my_received_responses, my_sent_requests;
    int my_stream_id;
    Tick my_SPD_read_finish_tick;
    Tick my_SPD_write_finish_tick;
    Tick my_RT_access_finish_tick;
    int my_word_size;
    int my_words_per_cl, my_words_per_page;
    Tick my_decode_start_tick;
    Tick my_request_start_tick;
    int my_size;
    int my_element_base;
    int my_element_count;
    // A completion-only source on an ordinary STREAM_LD is an ordering
    // token, never an SPD payload. This is the serialized correctness
    // fallback when the bounded concurrent materializer cannot admit.
    bool my_token_bound_load;

    // A guarded STREAM_ST with tdst1 present publishes exactly one completed
    // physical 4K-element tile.  tdst1 is a completion-only output token;
    // rsrc1/2/3 contain logical page, logical element offset, and generation.
    bool my_response_bearing_publish;
    int my_publish_completion_tile;
    ResponsePublisher response_publisher;
    uint64_t response_publisher_sequence;
    uint64_t my_publish_guest_generation;
    uint32_t my_publish_logical_page;
    uint32_t my_publish_logical_element_offset;
    uint64_t my_publish_credit_stall_observations;

    Addr my_translated_addr;
    bool my_translation_done;

    void createReadPacket(Addr addr, int latency);
    void executeResponseBearingPublish();
    void captureAndIssueResponseBearingLine();
    PacketPtr makeResponseBearingPublishPacket(
        const ResponsePublisher::Request &request);
    ResponseBearingPublishSenderState *responseBearingPublishState(
        PacketPtr pkt) const;
    Addr translatePacket(Addr vaddr);
    void executeInstruction();
    EventFunctionWrapper executeInstructionEvent;
    int getGBGAddr(int channel, int rank, int bankgroup);
    PageInfo getPageInfo(int i, Addr base_addr, int word_size, int min, int stride);
    bool fillCurrentPageInfos();
};
} // namespace gem5

#endif // __MEM_MAA_STREAMACCESS_HH__
