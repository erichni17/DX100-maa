#ifndef __GEM5_MAA_PAGE_FED_SOA_ABI_HH__
#define __GEM5_MAA_PAGE_FED_SOA_ABI_HH__

#include <cstddef>
#include <cstdint>

namespace gem5::maa
{

/**
 * Wire contract for the page-fed SoA/JIT command doorbell.
 *
 * The useful RMW is still an ordinary instruction-file descriptor.  A
 * doorbell carries only the identity of one already-complete physical SPD
 * page, or closes the operation.  It is never queued and carries no index
 * payload or coherent address.
 */
class PageFedSoaJitABI
{
  public:
    static constexpr uint16_t Magic = 0xd5a;
    static constexpr unsigned MagicShift = 52;
    static constexpr uint64_t GenerationMask = (uint64_t{1} << 40) - 1;
    static constexpr uint8_t ModeTag = 0xfd;
    static constexpr uint64_t NoIndexBacking = UINT64_MAX;
    static constexpr uint32_t Pages = 4;
    static constexpr uint32_t PageElements = 4096;
    static constexpr uint32_t LogicalElements = Pages * PageElements;

    enum class Action : uint8_t
    {
        Admit = 0,
        Close = 1,
    };

    struct Command
    {
        Action action = Action::Admit;
        uint8_t page = 0;
        uint8_t tile = 0;
        uint64_t generation = 0;
    };

    static constexpr uint64_t
    encodeAdmit(uint64_t generation, uint8_t page, uint8_t tile)
    {
        return (uint64_t{Magic} << MagicShift) |
            (uint64_t{static_cast<uint8_t>(Action::Admit)} << 50) |
            (uint64_t{page} << 48) | (uint64_t{tile} << 40) |
            generation;
    }

    static constexpr uint64_t
    encodeClose(uint64_t generation)
    {
        return (uint64_t{Magic} << MagicShift) |
            (uint64_t{static_cast<uint8_t>(Action::Close)} << 50) |
            generation;
    }

    static bool
    decode(uint64_t word, Command &command)
    {
        if ((word >> MagicShift) != Magic)
            return false;
        const uint8_t action = (word >> 50) & 0x3;
        if (action > static_cast<uint8_t>(Action::Close))
            return false;
        command.action = static_cast<Action>(action);
        command.page = (word >> 48) & 0x3;
        command.tile = (word >> 40) & 0xff;
        command.generation = word & GenerationMask;
        if (command.generation == 0)
            return false;
        if (command.action == Action::Admit)
            return command.page < Pages;
        return command.page == 0 && command.tile == 0;
    }
};

/**
 * Stateless validation for the internal product-ready notification.
 *
 * This is not a guest-visible command and carries no product payload.  The
 * response-bearing publisher presents its terminal identity to the owning
 * indirect unit only after every exact WriteResp for the page has returned.
 */
class PageFedProductReadyIdentity
{
  public:
    enum class Result : uint8_t
    {
        Accepted,
        Core,
        Generation,
        Page,
        Region,
        Backing,
        WordSize,
    };

    static Result
    validate(uint32_t expectedCore, uint64_t expectedGeneration,
             uint64_t productBacking, int16_t expectedRegion,
             uint8_t wordBytes, uint32_t core, uint64_t generation,
             uint8_t page, uint64_t pageBacking, int16_t region)
    {
        if (core != expectedCore)
            return Result::Core;
        if (generation != expectedGeneration)
            return Result::Generation;
        if (page >= PageFedSoaJitABI::Pages)
            return Result::Page;
        if (region != expectedRegion)
            return Result::Region;
        if (wordBytes != 4 && wordBytes != 8)
            return Result::WordSize;
        const uint64_t pageBytes =
            static_cast<uint64_t>(PageFedSoaJitABI::PageElements) *
            wordBytes;
        if (productBacking > UINT64_MAX - pageBytes * page ||
            pageBacking != productBacking + pageBytes * page)
            return Result::Backing;
        return Result::Accepted;
    }
};

/**
 * Exact persistent state for one bounded page-fed operation.
 *
 * admittedCount is also the next required logical ordinal, so no ordinal
 * bitmap, page descriptor array, or payload storage exists.  reserved is
 * explicit billed storage, not compiler-hidden padding.
 */
class PageFedSoaJitState
{
  public:
    enum class Result : uint8_t
    {
        Accepted,
        Disabled,
        Busy,
        Inactive,
        StaleGeneration,
        PageOrder,
        OrdinalOrder,
        PageIncomplete,
        MissingPages,
        Capacity,
        Closed,
        EarlyExecution,
        AlreadyExecuting,
        NotExecuting,
        DuplicateProductReady,
        MissingProducts,
    };

    static constexpr uint8_t Active = 1U << 0;
    static constexpr uint8_t Closure = 1U << 1;
    static constexpr uint8_t Failed = 1U << 2;
    static constexpr uint8_t Executing = 1U << 3;
    static constexpr uint16_t ProductReadyMask =
        (uint16_t{1} << PageFedSoaJitABI::Pages) - 1;
    static constexpr std::size_t HardwareBytes = 16;

    Result
    open(bool enabled, uint64_t nextGeneration, uint32_t capacity)
    {
        if (!enabled)
            return reject(Result::Disabled);
        if (active())
            return reject(Result::Busy);
        if (nextGeneration == 0 || nextGeneration >
                PageFedSoaJitABI::GenerationMask ||
            nextGeneration == generation)
            return reject(Result::StaleGeneration);
        if (capacity < PageFedSoaJitABI::LogicalElements)
            return reject(Result::Capacity);
        generation = nextGeneration;
        admittedCount = 0;
        nextPage = 0;
        flags = Active;
        reserved = 0;
        return Result::Accepted;
    }

    Result
    beginPage(uint64_t candidateGeneration, uint8_t page)
    {
        Result result = validateOpen(candidateGeneration);
        if (result != Result::Accepted)
            return result;
        if (page != nextPage ||
            admittedCount != page * PageFedSoaJitABI::PageElements)
            return reject(Result::PageOrder);
        return Result::Accepted;
    }

    Result
    admitOrdinal(uint64_t candidateGeneration, uint8_t page,
                 uint32_t ordinal)
    {
        Result result = validateOpen(candidateGeneration);
        if (result != Result::Accepted)
            return result;
        if (page != nextPage)
            return reject(Result::PageOrder);
        if (ordinal != admittedCount ||
            ordinal >= PageFedSoaJitABI::LogicalElements)
            return reject(Result::OrdinalOrder);
        ++admittedCount;
        return Result::Accepted;
    }

    Result
    finishPage(uint64_t candidateGeneration, uint8_t page)
    {
        Result result = validateOpen(candidateGeneration);
        if (result != Result::Accepted)
            return result;
        if (page != nextPage)
            return reject(Result::PageOrder);
        if (admittedCount !=
            (static_cast<uint32_t>(page) + 1) *
                PageFedSoaJitABI::PageElements)
            return reject(Result::PageIncomplete);
        ++nextPage;
        return Result::Accepted;
    }

    Result
    close(uint64_t candidateGeneration)
    {
        Result result = validateOpen(candidateGeneration);
        if (result != Result::Accepted)
            return result;
        if (nextPage != PageFedSoaJitABI::Pages ||
            admittedCount != PageFedSoaJitABI::LogicalElements)
            return reject(Result::MissingPages);
        flags |= Closure;
        return Result::Accepted;
    }

    Result
    beginExecution(uint64_t candidateGeneration)
    {
        if (!active())
            return reject(Result::Inactive);
        if (candidateGeneration != generation)
            return reject(Result::StaleGeneration);
        if (failed() || !closed() ||
            nextPage != PageFedSoaJitABI::Pages ||
            admittedCount != PageFedSoaJitABI::LogicalElements)
            return reject(Result::EarlyExecution);
        if (executing())
            return reject(Result::AlreadyExecuting);
        flags |= Executing;
        return Result::Accepted;
    }

    Result
    finishExecution(uint64_t candidateGeneration)
    {
        if (!active())
            return reject(Result::Inactive);
        if (candidateGeneration != generation)
            return reject(Result::StaleGeneration);
        if (!executing())
            return reject(Result::NotExecuting);
        if (!allProductsReady())
            return reject(Result::MissingProducts);
        flags = 0;
        admittedCount = 0;
        nextPage = 0;
        reserved = 0;
        return Result::Accepted;
    }

    Result
    signalProductReady(uint64_t candidateGeneration, uint8_t page)
    {
        if (!active())
            return reject(Result::Inactive);
        if (candidateGeneration != generation)
            return reject(Result::StaleGeneration);
        if (failed())
            return Result::EarlyExecution;
        if (page >= PageFedSoaJitABI::Pages)
            return reject(Result::PageOrder);
        const uint16_t bit = uint16_t{1} << page;
        if (reserved & bit)
            return reject(Result::DuplicateProductReady);
        reserved |= bit;
        return Result::Accepted;
    }

    Result
    failCapacity()
    {
        return reject(Result::Capacity);
    }

    bool active() const { return flags & Active; }
    bool closed() const { return flags & Closure; }
    bool failed() const { return flags & Failed; }
    bool executing() const { return flags & Executing; }
    uint64_t currentGeneration() const { return generation; }
    uint32_t admitted() const { return admittedCount; }
    uint8_t expectedPage() const { return nextPage; }
    uint8_t productReadyPages() const
    {
        uint16_t mask = reserved & ProductReadyMask;
        uint8_t count = 0;
        while (mask != 0) {
            count += mask & 1U;
            mask >>= 1;
        }
        return count;
    }
    uint16_t productReadyMask() const
    {
        return reserved & ProductReadyMask;
    }
    bool productReady(uint8_t page) const
    {
        return page < PageFedSoaJitABI::Pages &&
            (productReadyMask() & (uint16_t{1} << page));
    }
    bool allProductsReady() const
    {
        return productReadyMask() == ProductReadyMask;
    }

    static const char *resultName(Result result)
    {
        switch (result) {
          case Result::Accepted: return "accepted";
          case Result::Disabled: return "disabled";
          case Result::Busy: return "busy";
          case Result::Inactive: return "inactive";
          case Result::StaleGeneration: return "stale_generation";
          case Result::PageOrder: return "page_order";
          case Result::OrdinalOrder: return "ordinal_order";
          case Result::PageIncomplete: return "page_incomplete";
          case Result::MissingPages: return "missing_pages";
          case Result::Capacity: return "capacity";
          case Result::Closed: return "closed";
          case Result::EarlyExecution: return "early_execution";
          case Result::AlreadyExecuting: return "already_executing";
          case Result::NotExecuting: return "not_executing";
          case Result::DuplicateProductReady:
            return "duplicate_product_ready";
          case Result::MissingProducts: return "missing_products";
        }
        return "unknown";
    }

  private:
    Result
    validateOpen(uint64_t candidateGeneration)
    {
        if (!active())
            return reject(Result::Inactive);
        if (candidateGeneration != generation)
            return reject(Result::StaleGeneration);
        if (failed())
            return Result::EarlyExecution;
        if (closed())
            return reject(Result::Closed);
        return Result::Accepted;
    }

    Result
    reject(Result result)
    {
        flags |= Failed;
        return result;
    }

    uint64_t generation = 0;
    uint32_t admittedCount = 0;
    uint8_t nextPage = 0;
    uint8_t flags = 0;
    uint16_t reserved = 0;
};

static_assert(sizeof(PageFedSoaJitState) ==
                  PageFedSoaJitState::HardwareBytes,
              "page-fed SoA/JIT control state must remain exactly 16 bytes");

} // namespace gem5::maa

#endif // __GEM5_MAA_PAGE_FED_SOA_ABI_HH__
