#ifndef __MEM_MAA_SOA_JIT_OVERLAP_STATE_HH__
#define __MEM_MAA_SOA_JIT_OVERLAP_STATE_HH__

#include <algorithm>
#include <array>
#include <bitset>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

namespace gem5
{

/**
 * Four physically provisioned apply lanes with exact per-cycle ownership.
 * A runtime treatment may activate one, two, or four lanes.  Each granted
 * lane owns one generation/A-line/context tuple for the modeled cycle, so a
 * context cannot advance twice and two contexts cannot alias the same A line.
 */
class SoaJitApplyLanePool
{
  public:
    static constexpr size_t MaxLanes = 4;
    // A context identifies one live physical A line.  All 64 entries are
    // provisioned; a treatment selects a bounded active prefix at runtime.
    static constexpr size_t MaxContexts = 64;

    struct Owner
    {
        bool valid = false;
        uint64_t generation = 0;
        uint64_t aPaddr = 0;
        uint8_t context = 0;
    };

    static constexpr bool isValidActiveLaneCount(uint8_t count)
    {
        return count == 1 || count == 2 || count == 4;
    }

    void configure(uint8_t active_lanes)
    {
        activeLanes = isValidActiveLaneCount(active_lanes)
            ? active_lanes : 0;
    }

    void reset()
    {
        owners = {};
        cycleValid = false;
        lastCycle = 0;
        usedLanes = 0;
        nextContext = 0;
        laneHighWater = 0;
    }

    bool grant(uint64_t cycle, uint64_t generation, uint64_t a_paddr,
               uint8_t context, uint8_t context_count)
    {
        if (!isValidActiveLaneCount(activeLanes) || generation == 0 ||
            context_count == 0 || context_count > MaxContexts ||
            context >= context_count)
            return false;
        startCycle(cycle);
        if (usedLanes >= activeLanes)
            return false;
        for (size_t lane = 0; lane < usedLanes; ++lane) {
            if (owners[lane].context == context ||
                owners[lane].aPaddr == a_paddr)
                return false;
        }
        owners[usedLanes++] = {true, generation, a_paddr, context};
        nextContext = (context + 1) % context_count;
        laneHighWater = std::max(laneHighWater, usedLanes);
        return true;
    }

    uint8_t cursor() const { return nextContext; }
    void beginCycle(uint64_t cycle) { startCycle(cycle); }
    uint8_t activeLaneCount() const { return activeLanes; }
    uint8_t currentCycleOccupancy() const { return usedLanes; }
    uint8_t highWater() const { return laneHighWater; }
    const std::array<Owner, MaxLanes> &laneOwners() const { return owners; }

    bool assertInvariants() const
    {
        if (!isValidActiveLaneCount(activeLanes) ||
            usedLanes > activeLanes || nextContext >= MaxContexts)
            return false;
        for (size_t first = 0; first < owners.size(); ++first) {
            const auto &owner = owners[first];
            if (first >= usedLanes) {
                if (owner.valid || owner.generation != 0 ||
                    owner.aPaddr != 0 || owner.context != 0)
                    return false;
                continue;
            }
            if (!owner.valid || owner.generation == 0 ||
                owner.context >= MaxContexts)
                return false;
            for (size_t second = first + 1; second < usedLanes; ++second) {
                if (owners[second].context == owner.context ||
                    owners[second].aPaddr == owner.aPaddr)
                    return false;
            }
        }
        return true;
    }

  private:
    std::array<Owner, MaxLanes> owners{};
    uint64_t lastCycle = 0;
    uint8_t activeLanes = 1;
    uint8_t usedLanes = 0;
    uint8_t nextContext = 0;
    uint8_t laneHighWater = 0;
    bool cycleValid = false;

    void startCycle(uint64_t cycle)
    {
        if (cycleValid && lastCycle == cycle)
            return;
        owners = {};
        lastCycle = cycle;
        usedLanes = 0;
        cycleValid = true;
    }
};

/**
 * Fixed hardware state shared by SoA/JIT demand-value reads and the
 * sequential value-stream prefetcher.  Every physical line has exactly one
 * owner: either an active value-owner fill or one of eight payload-free
 * prefetch
 * credits.  Alias waiters may attach to either owner, but attaching to a
 * prefetch owner first reserves a cache line for the eventual response.
 */
class SoaJitValueCoalescer
{
  public:
    static constexpr size_t LineBytes = 64;
    // All 128 owners are physically provisioned.  A run may activate exactly
    // 4, 8, 16, 32, 64, 96, or 128 owners; inactive entries are never eligible
    // for a fill.
    static constexpr size_t BaselineOwners = 32;
    static constexpr size_t MaxOwners = 128;
    static constexpr size_t CacheLines = MaxOwners;
    static constexpr size_t MaxPrefetchCredits = 8;
    // Keep alias identity injective across every provisioned context and its
    // ordered lookahead slots.  This preserves response ownership even when
    // a fill is shared by the full context pool.
    static constexpr size_t MaxContexts = 64;
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
        std::bitset<MaxWaiters> waiterMask{};
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

    static constexpr bool isValidActiveOwnerCount(size_t count)
    {
        return count == 4 || count == 8 || count == 16 || count == 32 ||
               count == 64 || count == 96 || count == 128;
    }

    static constexpr bool isValidActivePrefetchCreditCount(uint8_t count)
    {
        return count == 0 || count == 1 || count == 2 || count == 4 ||
               count == 8;
    }

    void configure(bool cache_enable, uint8_t prefetch_credits,
                   size_t active_owners = 4)
    {
        cacheEnabled = cache_enable;
        activePrefetchCredits =
            isValidActivePrefetchCreditCount(prefetch_credits)
                ? prefetch_credits
                : MaxPrefetchCredits + 1;
        activeOwnerLines = isValidActiveOwnerCount(active_owners)
            ? static_cast<uint8_t>(active_owners) : 0;
    }

    void reset()
    {
        cache = {};
        prefetch = {};
        lruClock = 1;
        deliveryCycleValid = false;
        lastDeliveryCycle = 0;
        deliveriesThisCycle = 0;
        prefetchHighWater = 0;
        cacheHighWater = 0;
    }

    AliasOutcome requestAlias(uint64_t generation, uint64_t paddr,
                              uint16_t waiter)
    {
        if (generation == 0 || (paddr % LineBytes) != 0 ||
            waiter >= MaxWaiters)
            return {AliasResult::Invalid, false};
        for (auto &line : cache) {
            if (line.state == LineState::Free || line.paddr != paddr)
                continue;
            if (line.generation != generation)
                return {AliasResult::Stale, false};
            if (line.waiterMask.test(waiter))
                return {AliasResult::Duplicate, false};
            line.waiterMask.set(waiter);
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
        victim->waiterMask.set(waiter);
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
            activePrefetchCredits > MaxPrefetchCredits ||
            (vaddr % LineBytes) != 0 || (paddr % LineBytes) != 0)
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
            if (credit.generation != generation)
                return PrefetchResult::Stale;
            return credit.vaddr == vaddr
                ? PrefetchResult::AlreadyOwned : PrefetchResult::Invalid;
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
                                  const uint8_t *data, size_t bytes,
                                  uint64_t *owned_vaddr = nullptr)
    {
        if (owned_vaddr != nullptr)
            *owned_vaddr = 0;
        if (generation == 0 || (paddr % LineBytes) != 0 ||
            data == nullptr || bytes != LineBytes)
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
            if (owned_vaddr != nullptr)
                *owned_vaddr = credit->vaddr;
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

    DeliveryResult deliver(uint64_t generation, uint16_t waiter,
                           uint64_t cycle, Delivery &delivery,
                           uint8_t max_deliveries = 1)
    {
        if (generation == 0 || waiter >= MaxWaiters ||
            !SoaJitApplyLanePool::isValidActiveLaneCount(max_deliveries))
            return DeliveryResult::Invalid;
        if (!deliveryCycleValid || lastDeliveryCycle != cycle) {
            deliveryCycleValid = true;
            lastDeliveryCycle = cycle;
            deliveriesThisCycle = 0;
        }
        if (deliveriesThisCycle >= max_deliveries)
            return DeliveryResult::CycleLimited;
        for (auto &line : cache) {
            if (!line.waiterMask.test(waiter))
                continue;
            if (line.generation != generation)
                return DeliveryResult::Stale;
            if (line.state != LineState::Ready)
                return DeliveryResult::NotReady;
            delivery.data = line.data;
            line.waiterMask.reset(waiter);
            touch(line);
            deliveriesThisCycle++;
            if (!cacheEnabled && line.waiterMask.none())
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
                 line.waiterMask.any()))
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
    size_t activePrefetchCreditCount() const
    {
        return activePrefetchCredits;
    }
    size_t activeOwnerCount() const { return activeOwnerLines; }
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
        if (!isValidActiveOwnerCount(activeOwnerLines) ||
            !isValidActivePrefetchCreditCount(activePrefetchCredits) ||
            prefetchCount() > activePrefetchCredits)
            return false;
        for (size_t first = 0; first < cache.size(); ++first) {
            const auto &line = cache[first];
            if (first >= activeOwnerLines && line.state != LineState::Free)
                return false;
            if (line.state == LineState::Free) {
                if (line.generation != 0 || line.paddr != 0 ||
                    line.waiterMask.any() || line.prefetchOwned)
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
            if (first >= activePrefetchCredits && credit.valid)
                return false;
            if (!credit.valid) {
                if (credit.generation != 0 || credit.paddr != 0 ||
                    credit.vaddr != 0)
                    return false;
                continue;
            }
            if (credit.generation == 0 ||
                (credit.vaddr % LineBytes) != 0 ||
                (credit.paddr % LineBytes) != 0)
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
    uint8_t activeOwnerLines = 4;
    uint64_t lruClock = 1;
    bool deliveryCycleValid = false;
    uint64_t lastDeliveryCycle = 0;
    uint8_t deliveriesThisCycle = 0;
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
        auto active_end = cache.begin() + activeOwnerLines;
        auto free = std::find_if(cache.begin(), active_end,
            [](const CacheLine &line) {
                return line.state == LineState::Free;
            });
        if (free != active_end) {
            evicted = false;
            return &*free;
        }
        auto victim = active_end;
        uint64_t oldest = std::numeric_limits<uint64_t>::max();
        for (auto candidate = cache.begin(); candidate != active_end;
             ++candidate) {
            if (candidate->state != LineState::Ready ||
                candidate->waiterMask.any() || candidate->lru >= oldest)
                continue;
            oldest = candidate->lru;
            victim = candidate;
        }
        evicted = victim != active_end;
        return victim == active_end ? nullptr : &*victim;
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
