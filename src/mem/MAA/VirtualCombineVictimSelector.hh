#ifndef __MEM_MAA_VIRTUAL_COMBINE_VICTIM_SELECTOR_HH__
#define __MEM_MAA_VIRTUAL_COMBINE_VICTIM_SELECTOR_HH__

#include <cstddef>
#include <cstdint>

namespace gem5::maa
{

/**
 * Bounded selector for a set-associative line table with a shared word pool.
 */
class VirtualCombineVictimSelector
{
  public:
    struct Candidate
    {
        bool valid = false;
        uint16_t validWords = 0;
    };

    struct Decision
    {
        int victim = -1;
        int victimSet = -1;
        int nextGlobal = 0;
        int nextVictimSet = 0;
        bool globalPayloadVictim = false;
    };

    template <typename CandidateAt>
    static Decision
    select(CandidateAt candidate_at, int slots, int ways,
           int incoming_set, int target, bool word_capacity_full,
           bool line_capacity_full, int policy, int global_start,
           int incoming_set_start)
    {
        Decision decision;
        if (slots <= 0 || ways <= 0 || slots % ways != 0 || incoming_set < 0 ||
            incoming_set >= slots / ways || target < -1 || target >= slots ||
            policy < 0 || policy > 2)
            return decision;

        // A free/matching slot in the incoming set resolves line placement.
        // If only the shared payload is full, any bounded line can release a
        // word credit.  If the incoming set itself is full, the victim must
        // remain local so that eviction also creates a legal insertion slot.
        decision.globalPayloadVictim =
            word_capacity_full && !line_capacity_full;
        const int begin = decision.globalPayloadVictim
            ? 0 : incoming_set * ways;
        const int count = decision.globalPayloadVictim ? slots : ways;
        const int start = decision.globalPayloadVictim
            ? normalized(global_start, count)
            : normalized(incoming_set_start, count);
        int selected_words = 0;
        for (int offset = 0; offset < count; ++offset) {
            const int index = begin + (start + offset) % count;
            const Candidate candidate = candidate_at(index);
            if (!candidate.valid || index == target)
                continue;
            const int words = popcount(candidate.validWords);
            if (decision.victim == -1 ||
                (policy == 1 && words < selected_words) ||
                (policy == 2 && words > selected_words)) {
                decision.victim = index;
                selected_words = words;
                if (policy == 0)
                    break;
            }
        }
        // A matching target may be the only line holding payload.  It is a
        // legal last-resort victim; a free target is never valid here.
        if (decision.victim == -1 && target >= 0 &&
            candidate_at(target).valid)
            decision.victim = target;
        if (decision.victim == -1)
            return decision;

        decision.victimSet = decision.victim / ways;
        decision.nextGlobal = (decision.victim + 1) % slots;
        decision.nextVictimSet =
            (decision.victim - decision.victimSet * ways + 1) % ways;
        return decision;
    }

    static constexpr size_t
    packedGlobalPointerBits(size_t slots)
    {
        size_t bits = 0;
        size_t encoded = slots > 0 ? slots - 1 : 0;
        while (encoded != 0) {
            ++bits;
            encoded >>= 1;
        }
        return bits == 0 ? 1 : bits;
    }

  private:
    static int
    normalized(int value, int modulus)
    {
        const int result = value % modulus;
        return result < 0 ? result + modulus : result;
    }

    static int
    popcount(uint16_t value)
    {
        int count = 0;
        while (value != 0) {
            count += value & 1;
            value >>= 1;
        }
        return count;
    }
};

} // namespace gem5::maa

#endif // __MEM_MAA_VIRTUAL_COMBINE_VICTIM_SELECTOR_HH__
