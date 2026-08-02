#ifndef __MEM_MAA_TRANSPARENT_SPD_CONTROLLER_HH__
#define __MEM_MAA_TRANSPARENT_SPD_CONTROLLER_HH__

#include <array>
#include <cassert>
#include <cstdint>
#include <limits>

namespace gem5 {

/**
 * A deliberately narrow logical-tile controller.
 *
 * The controller owns one 16K-element descriptor, one 4K-element physical
 * mapping, and one in-flight native MAA micro-op.  Data always lives in the
 * coherent backing array when it is not mapped.  No element payloads are
 * buffered here.
 */
class TransparentSPDController
{
  public:
    static constexpr uint8_t ControllerLookupCycles = 1;
    static constexpr int LogicalElements = 16384;
    static constexpr int PageElements = 4096;
    static constexpr int NumPages = LogicalElements / PageElements;

    enum class State : uint8_t
    {
        Idle,
        WaitingForPage,
        IssueFill,
        FillInFlight,
        IssueCompute,
        ComputeInFlight,
        IssueStore,
        StoreInFlight,
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
    };

    struct Request
    {
        Action action = Action::None;
        int page = -1;
        int logicalOffset = 0;
        int elements = 0;
    };

    static const char *
    validate(const Descriptor &descriptor)
    {
        if (descriptor.logicalElements != LogicalElements)
            return "logical tile must contain exactly 16384 elements";
        if (descriptor.pageElements != PageElements)
            return "physical mapping must contain exactly 4096 elements";
        if (descriptor.wordSize != 4 && descriptor.wordSize != 8)
            return "word size must be four or eight bytes";
        if (descriptor.tokenTile < 0 || descriptor.physicalTile < 0 ||
            descriptor.outputTile < 0)
            return "tile identifiers must be nonnegative";
        if (descriptor.tokenTile == descriptor.physicalTile ||
            descriptor.tokenTile == descriptor.outputTile ||
            descriptor.physicalTile == descriptor.outputTile)
            return "token, physical, and output tiles must be distinct";
        if (descriptor.scaleReg < 0 || descriptor.minReg < 0 ||
            descriptor.maxReg < 0 || descriptor.strideReg < 0)
            return "controller registers must be nonnegative";
        if (descriptor.coreID < 0 || descriptor.maaID < 0 ||
            descriptor.contextID < 0)
            return "execution identity must be valid";
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
        return nullptr;
    }

    SubmitResult
    submit(const Descriptor &descriptor)
    {
        if (state != State::Idle)
            return SubmitResult::Busy;
        if (validate(descriptor) != nullptr) {
            state = State::Failed;
            return SubmitResult::Invalid;
        }
        desc = descriptor;
        pageReady.fill(false);
        currentPage = 0;
        mappedPage = -1;
        transitionCycles = ControllerLookupCycles;
        state = State::WaitingForPage;
        return SubmitResult::Accepted;
    }

    bool
    notifyPageReady(int token_tile, int page)
    {
        if (!active() || token_tile != desc.tokenTile || page < 0 ||
            page >= NumPages || pageReady[page]) {
            state = State::Failed;
            return false;
        }
        pageReady[page] = true;
        advanceReadyPage();
        return true;
    }

    Request
    pending() const
    {
        if (transitionCycles != 0)
            return {};
        Action action = Action::None;
        switch (state) {
          case State::IssueFill:
            action = Action::Fill;
            break;
          case State::IssueCompute:
            action = Action::Compute;
            break;
          case State::IssueStore:
            action = Action::Store;
            break;
          default:
            break;
        }
        if (action == Action::None)
            return {};
        return {action, currentPage, currentPage * PageElements,
                PageElements};
    }

    void advanceControllerCycle()
    {
        if (transitionCycles != 0)
            --transitionCycles;
    }

    uint8_t controllerCyclesRemaining() const { return transitionCycles; }

    bool
    accept(const Request &request)
    {
        const Request expected = pending();
        if (request.action == Action::None ||
            request.action != expected.action ||
            request.page != expected.page ||
            request.logicalOffset != expected.logicalOffset ||
            request.elements != expected.elements) {
            state = State::Failed;
            return false;
        }
        switch (request.action) {
          case Action::Fill:
            assert(mappedPage == -1);
            mappedPage = currentPage;
            state = State::FillInFlight;
            break;
          case Action::Compute:
            assert(mappedPage == currentPage);
            state = State::ComputeInFlight;
            break;
          case Action::Store:
            assert(mappedPage == currentPage);
            state = State::StoreInFlight;
            break;
          default:
            state = State::Failed;
            return false;
        }
        return true;
    }

    bool
    complete(Action action, int page)
    {
        if (page != currentPage || page != mappedPage) {
            state = State::Failed;
            return false;
        }
        if (action == Action::Fill && state == State::FillInFlight) {
            state = State::IssueCompute;
            return true;
        }
        if (action == Action::Compute && state == State::ComputeInFlight) {
            state = State::IssueStore;
            return true;
        }
        if (action == Action::Store && state == State::StoreInFlight) {
            mappedPage = -1;
            ++currentPage;
            if (currentPage == NumPages)
                state = State::Complete;
            else {
                state = State::WaitingForPage;
                advanceReadyPage();
            }
            return true;
        }
        state = State::Failed;
        return false;
    }

    bool
    retire()
    {
        if (state != State::Complete)
            return false;
        state = State::Idle;
        desc = Descriptor{};
        pageReady.fill(false);
        currentPage = 0;
        mappedPage = -1;
        transitionCycles = 0;
        return true;
    }

    bool active() const { return state != State::Idle; }
    bool failed() const { return state == State::Failed; }
    bool complete() const { return state == State::Complete; }
    State getState() const { return state; }
    int getCurrentPage() const { return currentPage; }
    int getMappedPage() const { return mappedPage; }
    const Descriptor &descriptor() const { return desc; }

    bool
    ownsTile(int maa_id, int tile_id) const
    {
        if (!active() || maa_id != desc.maaID)
            return false;
        const int tile_words = desc.wordSize / 4;
        return inTileSpan(desc.physicalTile, tile_words, tile_id) ||
               inTileSpan(desc.outputTile, tile_words, tile_id) ||
               inTileSpan(desc.tokenTile, tile_words, tile_id);
    }

    bool
    usesRegister(int maa_id, int register_id) const
    {
        if (!active() || maa_id != desc.maaID)
            return false;
        return register_id == desc.scaleReg || register_id == desc.minReg ||
               register_id == desc.maxReg || register_id == desc.strideReg;
    }

  private:
    static bool
    rangeContains(uint64_t range_min, uint64_t range_max, uint64_t base,
                  uint64_t bytes)
    {
        if (range_min >= range_max || base < range_min || base >= range_max)
            return false;
        return bytes <= range_max - base;
    }

    static bool
    inTileSpan(int first, int count, int tile)
    {
        return tile >= first && tile < first + count;
    }

    void
    advanceReadyPage()
    {
        if (state == State::WaitingForPage && currentPage < NumPages &&
            pageReady[currentPage])
            state = State::IssueFill;
    }

    State state = State::Idle;
    Descriptor desc;
    std::array<bool, NumPages> pageReady{};
    int currentPage = 0;
    int mappedPage = -1;
    uint8_t transitionCycles = 0;
};

} // namespace gem5

#endif // __MEM_MAA_TRANSPARENT_SPD_CONTROLLER_HH__
