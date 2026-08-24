#ifndef __MEM_MAA_TRANSPARENT_SPD_CONTROLLER_HH__
#define __MEM_MAA_TRANSPARENT_SPD_CONTROLLER_HH__

#include <array>
#include <cstdint>
#include <limits>

namespace gem5 {

/**
 * Finite iso-area controller for one 16K logical transparent descriptor.
 *
 * Every arm reserves the same two visible 4K physical payloads: one input
 * tile and one out-of-place output tile.  Serial4K uses each payload as one
 * slot, Serial2K deliberately uses only its lower half, and PingPong2K uses
 * two owner-tagged 2K halves.  Thus ping-pong changes utilization and
 * scheduling, not allocated payload bytes, ports, functional units, or IF
 * depth.  Producer readiness remains four 4K credits in all arms.
 */
class TransparentSPDController
{
  public:
    static constexpr uint8_t ControllerLookupCycles = 1;
    static constexpr int LogicalElements = 16384;
    static constexpr int PhysicalElements = 4096;
    // Compatibility names describe producer/physical pages, not 2K chunks.
    static constexpr int PageElements = 4096;
    static constexpr int NumPages = LogicalElements / PageElements;
    static constexpr int HalfElements = 2048;
    static constexpr int ProducerPages = LogicalElements / PhysicalElements;
    static constexpr int MaxChunks = LogicalElements / HalfElements;
    static constexpr int MaxSlots = 2;
    static constexpr uint8_t NumDataTypes = 6;
    static constexpr uint8_t NumOperations = 16;

    enum class Mode : uint8_t
    {
        Serial4K = 0,
        Serial2K = 1,
        PingPong2K = 2,
    };

    enum class State : uint8_t
    {
        Idle,
        WaitingForPage,
        Active,
        Complete,
        Failed,
    };

    enum class Action : uint8_t
    {
        None,
        Fill,
        Compute,
        Store,
    };

    enum class SubmitResult : uint8_t
    {
        Accepted,
        Busy,
        Invalid,
    };

    enum class Phase : uint8_t
    {
        Unseen,
        FillInFlight,
        Filled,
        ComputeInFlight,
        OutputReady,
        StoreInFlight,
        Done,
    };

    enum class Blocker : uint8_t
    {
        Runnable,
        ProducerNotReady,
        StreamBusy,
        ALUBusy,
        SlotOwned,
        Serialization,
        Transition,
        InstructionFileFull,
        Other,
        Inactive,
        Count,
    };

    struct Descriptor
    {
        int tokenTile = -1;
        int physicalTile = -1;
        int outputTile = -1;
        int scaleReg = -1;
        int minReg = -1;
        int maxReg = -1;
        int strideReg = -1;
        int wordSize = 0;
        int logicalElements = 0;
        int pageElements = 0;
        int coreID = -1;
        int maaID = -1;
        int contextID = -1;
        uint64_t generation = 0;
        uint8_t dataType = 0;
        uint8_t operation = 0;
        uint64_t pc = 0;
        uint64_t backingAddr = 0;
        uint64_t backingMinAddr = 0;
        uint64_t backingMaxAddr = 0;
        int backingRangeID = -1;
        uint64_t destinationAddr = 0;
        uint64_t destinationMinAddr = 0;
        uint64_t destinationMaxAddr = 0;
        int destinationRangeID = -1;
        Mode mode = Mode::Serial4K;
    };

    struct Request
    {
        Action action = Action::None;
        int page = -1;
        int logicalOffset = 0;
        int elements = 0;
        int srcSlot = -1;
        int dstSlot = -1;
        int elementOffset = 0;
        uint64_t transactionID = 0;
    };

    static const char *validate(const Descriptor &descriptor)
    {
        if (descriptor.logicalElements != LogicalElements)
            return "logical tile must contain exactly 16384 elements";
        if (descriptor.pageElements != PhysicalElements)
            return "physical mapping must contain exactly 4096 elements";
        if (descriptor.mode != Mode::Serial4K &&
            descriptor.mode != Mode::Serial2K &&
            descriptor.mode != Mode::PingPong2K)
            return "invalid iso-area controller mode";
        if (descriptor.wordSize != 4 && descriptor.wordSize != 8)
            return "word size must be four or eight bytes";
        if (descriptor.dataType >= NumDataTypes)
            return "invalid DX100 data type";
        if (descriptor.operation >= NumOperations)
            return "invalid DX100 scalar operation";
        if (descriptor.tokenTile < 0 || descriptor.physicalTile < 0 ||
            descriptor.outputTile < 0)
            return "tile identifiers must be nonnegative";
        const int tile_words = descriptor.wordSize / 4;
        if (spansOverlap(descriptor.tokenTile, tile_words,
                         descriptor.physicalTile, tile_words) ||
            spansOverlap(descriptor.tokenTile, tile_words,
                         descriptor.outputTile, tile_words) ||
            spansOverlap(descriptor.physicalTile, tile_words,
                         descriptor.outputTile, tile_words))
            return "token, physical, and output tile spans must be distinct";
        if (descriptor.scaleReg < 0 || descriptor.minReg < 0 ||
            descriptor.maxReg < 0 || descriptor.strideReg < 0)
            return "controller registers must be nonnegative";
        if (descriptor.coreID < 0 || descriptor.maaID < 0 ||
            descriptor.contextID < 0)
            return "execution identity must be valid";
        if (descriptor.generation == 0)
            return "logical tile generation must be nonzero";
        if (descriptor.backingRangeID < 0 ||
            descriptor.destinationRangeID < 0)
            return "backing and destination regions must be registered";
        const uint64_t bytes =
            static_cast<uint64_t>(LogicalElements) * descriptor.wordSize;
        if (!rangeContains(descriptor.backingMinAddr,
                           descriptor.backingMaxAddr,
                           descriptor.backingAddr, bytes))
            return "logical backing range is too small";
        if (!rangeContains(descriptor.destinationMinAddr,
                           descriptor.destinationMaxAddr,
                           descriptor.destinationAddr, bytes))
            return "destination range is too small";
        if (rangesOverlap(descriptor.backingAddr, bytes,
                          descriptor.destinationAddr, bytes))
            return "backing and destination payloads must not overlap";
        return nullptr;
    }

    SubmitResult submit(const Descriptor &descriptor)
    {
        if (state != State::Idle)
            return SubmitResult::Busy;
        if (validate(descriptor) != nullptr) {
            state = State::Failed;
            return SubmitResult::Invalid;
        }
        desc = descriptor;
        producerReady.fill(false);
        chunkReady.fill(false);
        phases.fill(Phase::Unseen);
        inputOwner.fill(-1);
        outputOwner.fill(-1);
        nextFill = nextCompute = nextStore = doneChunks = 0;
        streamInFlight = aluInFlight = false;
        transitionCycles = ControllerLookupCycles;
        state = State::WaitingForPage;
        return SubmitResult::Accepted;
    }

    bool notifyPageReady(int token_tile, int producer_page)
    {
        if (!active() || token_tile != desc.tokenTile || producer_page < 0 ||
            producer_page >= ProducerPages || producerReady[producer_page]) {
            state = State::Failed;
            return false;
        }
        producerReady[producer_page] = true;
        const int chunks_per_page = PhysicalElements / chunkElements();
        for (int i = 0; i < chunks_per_page; ++i)
            chunkReady[producer_page * chunks_per_page + i] = true;
        if (state == State::WaitingForPage)
            state = State::Active;
        return true;
    }

    Request pendingStream() const
    {
        if (!canSchedule() || streamInFlight)
            return {};
        if (nextStore < numChunks() &&
            phases[nextStore] == Phase::OutputReady)
            return makeRequest(Action::Store, nextStore);
        if (nextFill >= numChunks() || !chunkReady[nextFill] ||
            phases[nextFill] != Phase::Unseen)
            return {};
        if (desc.mode != Mode::PingPong2K && nextFill != doneChunks)
            return {};
        const int slot = slotFor(nextFill);
        if (inputOwner[slot] != -1)
            return {};
        return makeRequest(Action::Fill, nextFill);
    }

    Request pendingALU() const
    {
        if (!canSchedule() || aluInFlight || nextCompute >= numChunks() ||
            phases[nextCompute] != Phase::Filled)
            return {};
        if (desc.mode != Mode::PingPong2K && nextCompute != doneChunks)
            return {};
        const int slot = slotFor(nextCompute);
        if (inputOwner[slot] != nextCompute || outputOwner[slot] != -1)
            return {};
        return makeRequest(Action::Compute, nextCompute);
    }

    // Compatibility helper for simple serial unit clients.
    Request pending() const
    {
        const Request stream = pendingStream();
        return stream.action != Action::None ? stream : pendingALU();
    }

    void advanceControllerCycle()
    {
        if (transitionCycles != 0)
            --transitionCycles;
    }

    uint8_t controllerCyclesRemaining() const { return transitionCycles; }

    bool accept(const Request &request)
    {
        const Request expected = request.action == Action::Compute
            ? pendingALU() : pendingStream();
        if (!sameRequest(request, expected))
            return fail();
        const int slot = slotFor(request.page);
        switch (request.action) {
          case Action::Fill:
            inputOwner[slot] = request.page;
            phases[request.page] = Phase::FillInFlight;
            streamInFlight = true;
            ++nextFill;
            break;
          case Action::Compute:
            outputOwner[slot] = request.page;
            phases[request.page] = Phase::ComputeInFlight;
            aluInFlight = true;
            ++nextCompute;
            break;
          case Action::Store:
            phases[request.page] = Phase::StoreInFlight;
            streamInFlight = true;
            ++nextStore;
            break;
          default:
            return fail();
        }
        return true;
    }

    bool complete(const Request &request)
    {
        if (!active() || request.transactionID == 0 ||
            request.page < 0 || request.page >= numChunks())
            return fail();
        const Request expected = makeRequest(request.action, request.page);
        if (!sameRequest(request, expected))
            return fail();
        const int slot = slotFor(request.page);
        switch (request.action) {
          case Action::Fill:
            if (!streamInFlight ||
                phases[request.page] != Phase::FillInFlight ||
                inputOwner[slot] != request.page)
                return fail();
            streamInFlight = false;
            phases[request.page] = Phase::Filled;
            break;
          case Action::Compute:
            if (!aluInFlight ||
                phases[request.page] != Phase::ComputeInFlight ||
                inputOwner[slot] != request.page ||
                outputOwner[slot] != request.page)
                return fail();
            aluInFlight = false;
            inputOwner[slot] = -1;
            phases[request.page] = Phase::OutputReady;
            break;
          case Action::Store:
            if (!streamInFlight ||
                phases[request.page] != Phase::StoreInFlight ||
                outputOwner[slot] != request.page)
                return fail();
            streamInFlight = false;
            outputOwner[slot] = -1;
            phases[request.page] = Phase::Done;
            ++doneChunks;
            if (doneChunks == numChunks())
                state = State::Complete;
            break;
          default:
            return fail();
        }
        return true;
    }

    // Legacy serial completion adapter.
    bool complete(Action action, int page)
    {
        return complete(makeRequest(action, page));
    }

    bool retire()
    {
        if (state != State::Complete || streamInFlight || aluInFlight)
            return false;
        for (int owner : inputOwner)
            if (owner != -1)
                return false;
        for (int owner : outputOwner)
            if (owner != -1)
                return false;
        state = State::Idle;
        desc = Descriptor{};
        producerReady.fill(false);
        chunkReady.fill(false);
        phases.fill(Phase::Unseen);
        transitionCycles = 0;
        return true;
    }

    bool active() const
    {
        return state != State::Idle && state != State::Complete;
    }
    bool failed() const { return state == State::Failed; }
    bool complete() const { return state == State::Complete; }
    State getState() const { return state; }
    int getCurrentPage() const { return doneChunks; }
    int getMappedPage() const
    {
        for (int owner : inputOwner)
            if (owner != -1)
                return owner;
        return -1;
    }
    const Descriptor &descriptor() const { return desc; }
    int chunks() const { return numChunks(); }
    int elementsPerChunk() const { return chunkElements(); }
    int completedChunks() const { return doneChunks; }

    Blocker blocker() const
    {
        if (state == State::Idle || state == State::Complete ||
            state == State::Failed)
            return Blocker::Inactive;
        if (transitionCycles != 0)
            return Blocker::Transition;
        if (pendingStream().action != Action::None ||
            pendingALU().action != Action::None)
            return Blocker::Runnable;
        if (streamInFlight)
            return Blocker::StreamBusy;
        if (aluInFlight)
            return Blocker::ALUBusy;
        if (nextFill < numChunks() && !chunkReady[nextFill])
            return Blocker::ProducerNotReady;
        if (nextFill < numChunks()) {
            const int slot = slotFor(nextFill);
            if (inputOwner[slot] != -1)
                return Blocker::SlotOwned;
            if (desc.mode != Mode::PingPong2K && nextFill != doneChunks)
                return Blocker::Serialization;
        }
        if (nextCompute < numChunks()) {
            const int slot = slotFor(nextCompute);
            if (inputOwner[slot] != nextCompute || outputOwner[slot] != -1)
                return Blocker::SlotOwned;
            if (desc.mode != Mode::PingPong2K && nextCompute != doneChunks)
                return Blocker::Serialization;
        }
        return Blocker::Other;
    }

    bool ownsTile(int maa_id, int tile_id) const
    {
        if ((state == State::Idle) || maa_id != desc.maaID)
            return false;
        const int tile_words = desc.wordSize / 4;
        return inTileSpan(desc.physicalTile, tile_words, tile_id) ||
               inTileSpan(desc.outputTile, tile_words, tile_id) ||
               inTileSpan(desc.tokenTile, tile_words, tile_id);
    }

    bool usesRegister(int maa_id, int first_register, int register_words) const
    {
        if (state == State::Idle || maa_id != desc.maaID ||
            register_words <= 0)
            return false;
        const int scale_words = desc.wordSize / 4;
        return spansOverlap(first_register, register_words, desc.scaleReg,
                            scale_words) ||
               spansOverlap(first_register, register_words, desc.minReg, 1) ||
               spansOverlap(first_register, register_words, desc.maxReg, 1) ||
               spansOverlap(first_register, register_words, desc.strideReg, 1);
    }

  private:
    bool canSchedule() const
    {
        return state == State::Active && transitionCycles == 0;
    }

    int chunkElements() const
    {
        return desc.mode == Mode::Serial4K ? PhysicalElements : HalfElements;
    }

    int numChunks() const { return LogicalElements / chunkElements(); }

    int slotFor(int page) const
    {
        return desc.mode == Mode::PingPong2K ? page % MaxSlots : 0;
    }

    Request makeRequest(Action action, int page) const
    {
        if (action == Action::None || page < 0 || page >= numChunks())
            return {};
        const int slot = slotFor(page);
        Request request;
        request.action = action;
        request.page = page;
        request.logicalOffset = page * chunkElements();
        request.elements = chunkElements();
        request.srcSlot = action == Action::Fill ? -1 : slot;
        request.dstSlot = action == Action::Store ? -1 : slot;
        request.elementOffset = slot * chunkElements();
        request.transactionID =
            (desc.generation << 16) | (static_cast<uint64_t>(page) << 8) |
            static_cast<uint64_t>(action);
        return request;
    }

    static bool sameRequest(const Request &lhs, const Request &rhs)
    {
        return lhs.action != Action::None && lhs.action == rhs.action &&
               lhs.page == rhs.page &&
               lhs.logicalOffset == rhs.logicalOffset &&
               lhs.elements == rhs.elements && lhs.srcSlot == rhs.srcSlot &&
               lhs.dstSlot == rhs.dstSlot &&
               lhs.elementOffset == rhs.elementOffset &&
               lhs.transactionID == rhs.transactionID;
    }

    bool fail()
    {
        state = State::Failed;
        return false;
    }

    static bool rangeContains(uint64_t range_min, uint64_t range_max,
                              uint64_t base, uint64_t bytes)
    {
        if (range_min >= range_max || base < range_min || base >= range_max)
            return false;
        return bytes <= range_max - base;
    }

    static bool rangesOverlap(uint64_t lhs, uint64_t lhs_bytes, uint64_t rhs,
                              uint64_t rhs_bytes)
    {
        return lhs < rhs + rhs_bytes && rhs < lhs + lhs_bytes;
    }

    static bool spansOverlap(int lhs, int lhs_count, int rhs, int rhs_count)
    {
        return lhs < rhs + rhs_count && rhs < lhs + lhs_count;
    }

    static bool inTileSpan(int first, int count, int tile)
    {
        return tile >= first && tile < first + count;
    }

    State state = State::Idle;
    Descriptor desc;
    std::array<bool, ProducerPages> producerReady{};
    std::array<bool, MaxChunks> chunkReady{};
    std::array<Phase, MaxChunks> phases{};
    std::array<int, MaxSlots> inputOwner{{-1, -1}};
    std::array<int, MaxSlots> outputOwner{{-1, -1}};
    int nextFill = 0;
    int nextCompute = 0;
    int nextStore = 0;
    int doneChunks = 0;
    bool streamInFlight = false;
    bool aluInFlight = false;
    uint8_t transitionCycles = 0;
};

} // namespace gem5

#endif // __MEM_MAA_TRANSPARENT_SPD_CONTROLLER_HH__
