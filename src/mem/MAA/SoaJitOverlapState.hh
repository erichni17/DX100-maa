#ifndef __MEM_MAA_SOA_JIT_OVERLAP_STATE_HH__
#define __MEM_MAA_SOA_JIT_OVERLAP_STATE_HH__

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

namespace gem5
{

/**
 * Fixed hardware state shared by SoA/JIT demand-value reads and the
 * sequential value-stream prefetcher.  Every physical line has exactly one
 * owner: either a four-line cache fill or one of eight payload-free prefetch
 * credits.  Alias waiters may attach to either owner, but attaching to a
 * prefetch owner first reserves a cache line for the eventual response.
 */
class SoaJitValueCoalescer
{
  public:
    static constexpr size_t LineBytes = 64;
    static constexpr size_t CacheLines = 4;
    static constexpr size_t MaxPrefetchCredits = 8;
    static constexpr size_t MaxContexts = 8;
    static constexpr size_t MaxLookahead = 8;
    static constexpr size_t MaxWaiters = MaxContexts * MaxLookahead;

    enum class LineState : uint8_t
    {
        Free,
        Filling,
        Ready,
    };

    enum class AliasResult : uint8_t
    {
        Fill,
        Merge,
        Hit,
        Stall,
        Duplicate,
        Stale,
        Invalid,
    };

    enum class PrefetchResult : uint8_t
    {
        Issue,
        AlreadyOwned,
        Full,
        Stale,
        Invalid,
    };

    enum class ResponseResult : uint8_t
    {
        CacheFill,
        PrefetchDiscard,
        PrefetchPromote,
        Duplicate,
        Stale,
        Unknown,
        Invalid,
    };

    enum class DeliveryResult : uint8_t
    {
        Delivered,
        NotReady,
        CycleLimited,
        Stale,
        Invalid,
    };

    struct AliasOutcome
    {
        AliasResult result = AliasResult::Invalid;
        bool evicted = false;
    };

    struct Delivery
    {
        std::array<uint8_t, LineBytes> data{};
    };

    struct CacheLine
    {
        LineState state = LineState::Free;
        uint64_t generation = 0;
        uint64_t paddr = 0;
        uint64_t waiterMask = 0;
        uint64_t lru = 0;
        bool prefetchOwned = false;
        std::array<uint8_t, LineBytes> data{};
    };

    struct PrefetchCredit
    {
        bool valid = false;
        uint64_t generation = 0;
        uint64_t paddr = 0;
        uint64_t vaddr = 0;
    };

    void configure(bool cache_enable, uint8_t prefetch_credits)
    {
        cacheEnabled = cache_enable;
        activePrefetchCredits = prefetch_credits;
    }

    void reset()
    {
        cache = {};
        prefetch = {};
        lruClock = 1;
        deliveryCycleValid = false;
        lastDeliveryCycle = 0;
        prefetchHighWater = 0;
        cacheHighWater = 0;
    }

    AliasOutcome requestAlias(uint64_t generation, uint64_t paddr,
                              uint8_t waiter)
    {
        if (generation == 0 || waiter >= MaxWaiters)
            return {AliasResult::Invalid, false};
        const uint64_t bit = uint64_t{1} << waiter;

        for (auto &line : cache) {
            if (line.state == LineState::Free || line.paddr != paddr)
                continue;
            if (line.generation != generation)
                return {AliasResult::Stale, false};
            if (line.waiterMask & bit)
                return {AliasResult::Duplicate, false};
            line.waiterMask |= bit;
            touch(line);
            return {line.state == LineState::Ready ? AliasResult::Hit
                                                   : AliasResult::Merge,
                    false};
        }

        PrefetchCredit *prefetch_owner = nullptr;
        for (auto &credit : prefetch) {
            if (!credit.valid || credit.paddr != paddr)
                continue;
            if (credit.generation != generation)
                return {AliasResult::Stale, false};
            prefetch_owner = &credit;
            break;
        }

        bool evicted = false;
        CacheLine *victim = chooseVictim(evicted);
        if (victim == nullptr)
            return {AliasResult::Stall, false};
        *victim = CacheLine();
        victim->state = LineState::Filling;
        victim->generation = generation;
        victim->paddr = paddr;
        victim->waiterMask = bit;
        victim->prefetchOwned = prefetch_owner != nullptr;
        touch(*victim);
        updateCacheHighWater();
        return {prefetch_owner == nullptr ? AliasResult::Fill
                                          : AliasResult::Merge,
                evicted};
    }

    PrefetchResult reservePrefetch(uint64_t generation, uint64_t vaddr,
                                   uint64_t paddr)
    {
        if (generation == 0 || activePrefetchCredits == 0 ||
            activePrefetchCredits > MaxPrefetchCredits)
            return PrefetchResult::Invalid;
        for (const auto &line : cache) {
            if (line.state == LineState::Free || line.paddr != paddr)
                continue;
            return line.generation == generation
                ? PrefetchResult::AlreadyOwned : PrefetchResult::Stale;
        }
        for (const auto &credit : prefetch) {
            if (!credit.valid || credit.paddr != paddr)
                continue;
            return credit.generation == generation
                ? PrefetchResult::AlreadyOwned : PrefetchResult::Stale;
        }
        if (prefetchCount() >= activePrefetchCredits)
            return PrefetchResult::Full;
        auto slot = std::find_if(
            prefetch.begin(), prefetch.end(),
            [](const PrefetchCredit &credit) { return !credit.valid; });
        if (slot == prefetch.end())
            return PrefetchResult::Full;
        *slot = {true, generation, paddr, vaddr};
        prefetchHighWater = std::max(prefetchHighWater, prefetchCount());
        return PrefetchResult::Issue;
    }

    ResponseResult acceptResponse(uint64_t generation, uint64_t paddr,
                                  const uint8_t *data, size_t bytes)
    {
        if (generation == 0 || data == nullptr || bytes != LineBytes)
            return ResponseResult::Invalid;

        CacheLine *owned_fill = nullptr;
        CacheLine *prefetch_shadow = nullptr;
        for (auto &line : cache) {
            if (line.state == LineState::Free || line.paddr != paddr)
                continue;
            if (line.generation != generation)
                return ResponseResult::Stale;
            if (line.state == LineState::Ready)
                return ResponseResult::Duplicate;
            if (line.prefetchOwned)
                prefetch_shadow = &line;
            else
                owned_fill = &line;
        }
        if (owned_fill != nullptr) {
            if (prefetch_shadow != nullptr)
                return ResponseResult::Invalid;
            makeReady(*owned_fill, data);
            return ResponseResult::CacheFill;
        }

        auto credit = std::find_if(
            prefetch.begin(), prefetch.end(),
            [generation, paddr](const PrefetchCredit &candidate) {
                return candidate.valid &&
                       candidate.generation == generation &&
                       candidate.paddr == paddr;
            });
        if (credit != prefetch.end()) {
            *credit = PrefetchCredit();
            if (prefetch_shadow != nullptr) {
                makeReady(*prefetch_shadow, data);
                return ResponseResult::PrefetchPromote;
            }
            return ResponseResult::PrefetchDiscard;
        }
        for (const auto &credit_candidate : prefetch) {
            if (credit_candidate.valid &&
                credit_candidate.paddr == paddr)
                return ResponseResult::Stale;
        }
        return prefetch_shadow == nullptr ? ResponseResult::Unknown
                                          : ResponseResult::Invalid;
    }

    DeliveryResult deliver(uint64_t generation, uint8_t waiter,
                           uint64_t cycle, Delivery &delivery)
    {
        if (generation == 0 || waiter >= MaxWaiters)
            return DeliveryResult::Invalid;
        if (deliveryCycleValid && lastDeliveryCycle == cycle)
            return DeliveryResult::CycleLimited;
        const uint64_t bit = uint64_t{1} << waiter;
        for (auto &line : cache) {
            if ((line.waiterMask & bit) == 0)
                continue;
            if (line.generation != generation)
                return DeliveryResult::Stale;
            if (line.state != LineState::Ready)
                return DeliveryResult::NotReady;
            delivery.data = line.data;
            line.waiterMask &= ~bit;
            touch(line);
            deliveryCycleValid = true;
            lastDeliveryCycle = cycle;
            if (!cacheEnabled && line.waiterMask == 0)
                line = CacheLine();
            return DeliveryResult::Delivered;
        }
        return DeliveryResult::NotReady;
    }

    bool clearGeneration(uint64_t generation)
    {
        for (const auto &line : cache) {
            if (line.state != LineState::Free &&
                line.generation == generation &&
                (line.state == LineState::Filling ||
                 line.waiterMask != 0))
                return false;
        }
        for (const auto &credit : prefetch) {
            if (credit.valid && credit.generation == generation)
                return false;
        }
        for (auto &line : cache) {
            if (line.state != LineState::Free &&
                line.generation == generation)
                line = CacheLine();
        }
        return true;
    }

    size_t cacheOccupancy() const
    {
        return std::count_if(cache.begin(), cache.end(),
            [](const CacheLine &line) {
                return line.state != LineState::Free;
            });
    }

    size_t fillingCount() const
    {
        return std::count_if(cache.begin(), cache.end(),
            [](const CacheLine &line) {
                return line.state == LineState::Filling;
            });
    }

    size_t readyCount() const
    {
        return std::count_if(cache.begin(), cache.end(),
            [](const CacheLine &line) {
                return line.state == LineState::Ready;
            });
    }

    size_t prefetchCount() const
    {
        return std::count_if(prefetch.begin(), prefetch.end(),
            [](const PrefetchCredit &credit) { return credit.valid; });
    }

    bool owns(uint64_t generation, uint64_t paddr) const
    {
        return std::any_of(cache.begin(), cache.end(),
                   [generation, paddr](const CacheLine &line) {
                       return line.state != LineState::Free &&
                              line.generation == generation &&
                              line.paddr == paddr;
                   }) ||
               std::any_of(prefetch.begin(), prefetch.end(),
                   [generation, paddr](const PrefetchCredit &credit) {
                       return credit.valid &&
                              credit.generation == generation &&
                              credit.paddr == paddr;
                   });
    }

    bool prefetchComplete() const { return prefetchCount() == 0; }
    size_t prefetchHwm() const { return prefetchHighWater; }
    size_t cacheHwm() const { return cacheHighWater; }
    const std::array<CacheLine, CacheLines> &cacheLines() const
    {
        return cache;
    }
    const std::array<PrefetchCredit, MaxPrefetchCredits> &prefetchSlots()
        const
    {
        return prefetch;
    }

    bool assertInvariants() const
    {
        if (activePrefetchCredits > MaxPrefetchCredits ||
            prefetchCount() > activePrefetchCredits)
            return false;
        for (size_t first = 0; first < cache.size(); ++first) {
            const auto &line = cache[first];
            if (line.state == LineState::Free) {
                if (line.generation != 0 || line.paddr != 0 ||
                    line.waiterMask != 0 || line.prefetchOwned)
                    return false;
                continue;
            }
            if (line.generation == 0)
                return false;
            if (line.state == LineState::Ready && line.prefetchOwned)
                return false;
            for (size_t second = first + 1;
                 second < cache.size(); ++second) {
                const auto &other = cache[second];
                if (other.state != LineState::Free &&
                    other.generation == line.generation &&
                    other.paddr == line.paddr)
                    return false;
            }
            const bool has_prefetch_owner = std::any_of(
                prefetch.begin(), prefetch.end(),
                [&line](const PrefetchCredit &credit) {
                    return credit.valid &&
                           credit.generation == line.generation &&
                           credit.paddr == line.paddr;
                });
            if (line.prefetchOwned != has_prefetch_owner)
                return false;
        }
        for (size_t first = 0; first < prefetch.size(); ++first) {
            const auto &credit = prefetch[first];
            if (!credit.valid) {
                if (credit.generation != 0 || credit.paddr != 0 ||
                    credit.vaddr != 0)
                    return false;
                continue;
            }
            if (credit.generation == 0)
                return false;
            for (size_t second = first + 1;
                 second < prefetch.size(); ++second) {
                const auto &other = prefetch[second];
                if (other.valid &&
                    other.generation == credit.generation &&
                    other.paddr == credit.paddr)
                    return false;
            }
        }
        return true;
    }

  private:
    std::array<CacheLine, CacheLines> cache{};
    std::array<PrefetchCredit, MaxPrefetchCredits> prefetch{};
    bool cacheEnabled = false;
    uint8_t activePrefetchCredits = 0;
    uint64_t lruClock = 1;
    bool deliveryCycleValid = false;
    uint64_t lastDeliveryCycle = 0;
    size_t prefetchHighWater = 0;
    size_t cacheHighWater = 0;

    void touch(CacheLine &line)
    {
        line.lru = lruClock++;
        if (lruClock == 0)
            lruClock = 1;
    }

    void makeReady(CacheLine &line, const uint8_t *data)
    {
        std::memcpy(line.data.data(), data, LineBytes);
        line.state = LineState::Ready;
        line.prefetchOwned = false;
        touch(line);
    }

    CacheLine *chooseVictim(bool &evicted)
    {
        auto free = std::find_if(cache.begin(), cache.end(),
            [](const CacheLine &line) {
                return line.state == LineState::Free;
            });
        if (free != cache.end()) {
            evicted = false;
            return &*free;
        }
        auto victim = cache.end();
        uint64_t oldest = std::numeric_limits<uint64_t>::max();
        for (auto candidate = cache.begin(); candidate != cache.end();
             ++candidate) {
            if (candidate->state != LineState::Ready ||
                candidate->waiterMask != 0 || candidate->lru >= oldest)
                continue;
            oldest = candidate->lru;
            victim = candidate;
        }
        evicted = victim != cache.end();
        return victim == cache.end() ? nullptr : &*victim;
    }

    void updateCacheHighWater()
    {
        cacheHighWater = std::max(cacheHighWater, cacheOccupancy());
    }
};

/** Fixed eight-line predicate feeder with exact generation and VA/PA tags. */
class SoaJitPredicateFeeder
{
  public:
    static constexpr size_t LineBytes = 64;
    static constexpr size_t MaxLines = 8;

    enum class Result : uint8_t
    {
        Accepted,
        Existing,
        Full,
        Unknown,
        Duplicate,
        Stale,
        Invalid,
    };

    struct Line
    {
        SoaJitValueCoalescer::LineState state =
            SoaJitValueCoalescer::LineState::Free;
        uint64_t generation = 0;
        uint64_t vaddr = 0;
        uint64_t paddr = 0;
        std::array<uint8_t, LineBytes> data{};
    };

    void configure(uint8_t active_lines) { activeLines = active_lines; }
    void reset()
    {
        lines = {};
        highWater = 0;
    }

    Result reserve(uint64_t generation, uint64_t vaddr, uint64_t paddr)
    {
        if (generation == 0 || activeLines == 0 || activeLines > MaxLines)
            return Result::Invalid;
        for (const auto &line : lines) {
            if (line.state == SoaJitValueCoalescer::LineState::Free)
                continue;
            if (line.vaddr != vaddr && line.paddr != paddr)
                continue;
            if (line.generation != generation)
                return Result::Stale;
            return line.vaddr == vaddr && line.paddr == paddr
                ? Result::Existing : Result::Invalid;
        }
        if (occupancy() >= activeLines)
            return Result::Full;
        auto slot = std::find_if(lines.begin(), lines.end(),
            [](const Line &line) {
                return line.state ==
                    SoaJitValueCoalescer::LineState::Free;
            });
        if (slot == lines.end())
            return Result::Full;
        *slot = Line();
        slot->state = SoaJitValueCoalescer::LineState::Filling;
        slot->generation = generation;
        slot->vaddr = vaddr;
        slot->paddr = paddr;
        highWater = std::max(highWater, occupancy());
        return Result::Accepted;
    }

    Result accept(uint64_t generation, uint64_t paddr,
                  const uint8_t *data, size_t bytes)
    {
        if (generation == 0 || data == nullptr || bytes != LineBytes)
            return Result::Invalid;
        for (auto &line : lines) {
            if (line.state == SoaJitValueCoalescer::LineState::Free ||
                line.paddr != paddr)
                continue;
            if (line.generation != generation)
                return Result::Stale;
            if (line.state == SoaJitValueCoalescer::LineState::Ready)
                return Result::Duplicate;
            std::memcpy(line.data.data(), data, LineBytes);
            line.state = SoaJitValueCoalescer::LineState::Ready;
            return Result::Accepted;
        }
        return Result::Unknown;
    }

    const uint8_t *ready(uint64_t generation, uint64_t vaddr) const
    {
        for (const auto &line : lines) {
            if (line.state != SoaJitValueCoalescer::LineState::Ready ||
                line.vaddr != vaddr)
                continue;
            return line.generation == generation ? line.data.data()
                                                 : nullptr;
        }
        return nullptr;
    }

    Result release(uint64_t generation, uint64_t vaddr)
    {
        for (auto &line : lines) {
            if (line.state == SoaJitValueCoalescer::LineState::Free ||
                line.vaddr != vaddr)
                continue;
            if (line.generation != generation)
                return Result::Stale;
            if (line.state != SoaJitValueCoalescer::LineState::Ready)
                return Result::Invalid;
            line = Line();
            return Result::Accepted;
        }
        return Result::Unknown;
    }

    size_t occupancy() const
    {
        return std::count_if(lines.begin(), lines.end(),
            [](const Line &line) {
                return line.state !=
                    SoaJitValueCoalescer::LineState::Free;
            });
    }

    size_t hwm() const { return highWater; }
    bool empty() const { return occupancy() == 0; }
    const std::array<Line, MaxLines> &entries() const { return lines; }

  private:
    std::array<Line, MaxLines> lines{};
    uint8_t activeLines = 1;
    size_t highWater = 0;
};

} // namespace gem5

#endif // __MEM_MAA_SOA_JIT_OVERLAP_STATE_HH__
