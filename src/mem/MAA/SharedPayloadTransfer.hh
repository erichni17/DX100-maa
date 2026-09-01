#ifndef __MEM_MAA_SHARED_PAYLOAD_TRANSFER_HH__
#define __MEM_MAA_SHARED_PAYLOAD_TRANSFER_HH__

#include <cstddef>
#include <cstdint>

#include "mem/MAA/VirtualCombinePayloadStore.hh"
#include "mem/MAA/VirtualSourceFanout.hh"

namespace gem5::maa
{

/**
 * One bounded ownership transaction between a shared response and combiner.
 *
 * The helper owns only fixed transaction metadata. Payload remains in the
 * configured VirtualCombinePayloadStore: a final use removes the response's
 * exact WordRef and credits before insertion, then either restores them on
 * failure or records the zero-copy transfer after successful insertion.
 */
class SharedPayloadTransfer
{
  public:
    using WordRef = VirtualCombinePayloadStore::WordRef;
    using LineRefs = VirtualCombinePayloadStore::LineRefs;
    using FanoutResult = VirtualSourceFanout::Result;

    enum class Result : uint8_t
    {
        Ok,
        InvalidTransaction,
        StaleTransaction,
        AlreadyResolved,
        FanoutRejected,
        MissingResponseCredit,
        LostReference,
        OccupancyMismatch,
        CapacityExceeded,
    };

    class Transaction
    {
      public:
        Transaction() = default;
        Transaction(const Transaction &) = delete;
        Transaction &operator=(const Transaction &) = delete;

        WordRef wordRef() const { return ref; }
        bool finalUse() const { return final_use; }

      private:
        friend class SharedPayloadTransfer;

        enum class State : uint8_t
        {
            Empty,
            Pending,
            Committed,
            RolledBack,
        };

        const SharedPayloadTransfer *owner = nullptr;
        VirtualSourceFanout *fanout = nullptr;
        LineRefs *refs = nullptr;
        int *slot_reserved_words = nullptr;
        WordRef ref = VirtualCombinePayloadStore::InvalidWord;
        uint16_t word = 0;
        bool final_use = false;
        State state = State::Empty;
    };

    SharedPayloadTransfer(
        bool enabled, const VirtualCombinePayloadStore &payload,
        const int &combine_words, int &reserved_response_words,
        int &response_payload_words, int payload_limit,
        uint64_t &transfers, uint64_t &rollbacks, int &high_water)
        : enabled(enabled), payload(payload), combine_words(combine_words),
          reserved_response_words(reserved_response_words),
          response_payload_words(response_payload_words),
          payload_limit(payload_limit), transfers(transfers),
          rollbacks(rollbacks), high_water(high_water)
    {
    }

    Result
    begin(VirtualSourceFanout &fanout, LineRefs &refs,
          int &slot_reserved_words, uint16_t word,
          Transaction &transaction)
    {
        if (transaction.state != Transaction::State::Empty ||
            transaction.owner != nullptr)
            return Result::InvalidTransaction;

        if (!enabled) {
            bind(transaction, nullptr, nullptr, nullptr, word, false,
                 VirtualCombinePayloadStore::InvalidWord);
            return Result::Ok;
        }

        bool final_use = false;
        last_fanout_result = fanout.consume(word, final_use);
        if (last_fanout_result != FanoutResult::Accepted)
            return Result::FanoutRejected;

        WordRef ref = VirtualCombinePayloadStore::InvalidWord;
        if (final_use) {
            if (slot_reserved_words <= 0 ||
                reserved_response_words <= 0 ||
                response_payload_words <= 0 ||
                refs[word] == VirtualCombinePayloadStore::InvalidWord) {
                last_fanout_result = fanout.rollback(word);
                return last_fanout_result == FanoutResult::Accepted
                    ? Result::MissingResponseCredit
                    : Result::FanoutRejected;
            }
            ref = refs[word];
            refs[word] = VirtualCombinePayloadStore::InvalidWord;
            --slot_reserved_words;
            --reserved_response_words;
            --response_payload_words;
        }

        bind(transaction, &fanout, &refs, &slot_reserved_words, word,
             final_use, ref);
        return Result::Ok;
    }

    Result
    rollback(Transaction &transaction)
    {
        const Result checked = validatePending(transaction);
        if (checked != Result::Ok)
            return checked;
        if (!enabled) {
            transaction.state = Transaction::State::RolledBack;
            return Result::Ok;
        }

        last_fanout_result = transaction.fanout->rollback(transaction.word);
        if (last_fanout_result != FanoutResult::Accepted)
            return Result::FanoutRejected;
        if (transaction.final_use) {
            if (transaction.ref == VirtualCombinePayloadStore::InvalidWord ||
                (*transaction.refs)[transaction.word] !=
                    VirtualCombinePayloadStore::InvalidWord)
                return Result::LostReference;
            (*transaction.refs)[transaction.word] = transaction.ref;
            ++*transaction.slot_reserved_words;
            ++reserved_response_words;
            ++response_payload_words;
        }
        if (!occupancyMatches())
            return Result::OccupancyMismatch;

        ++rollbacks;
        transaction.state = Transaction::State::RolledBack;
        return Result::Ok;
    }

    Result
    commit(Transaction &transaction)
    {
        const Result checked = validatePending(transaction);
        if (checked != Result::Ok)
            return checked;
        if (!enabled || !transaction.final_use) {
            transaction.state = Transaction::State::Committed;
            return Result::Ok;
        }

        const int owned_words = combine_words + reserved_response_words;
        if (owned_words > payload_limit)
            return Result::CapacityExceeded;
        if (!occupancyMatches())
            return Result::OccupancyMismatch;

        ++transfers;
        if (owned_words > high_water)
            high_water = owned_words;
        transaction.state = Transaction::State::Committed;
        return Result::Ok;
    }

    FanoutResult lastFanoutResult() const { return last_fanout_result; }

    static const char *
    resultName(Result result)
    {
        switch (result) {
          case Result::Ok: return "ok";
          case Result::InvalidTransaction: return "invalid-transaction";
          case Result::StaleTransaction: return "stale-transaction";
          case Result::AlreadyResolved: return "already-resolved";
          case Result::FanoutRejected: return "fanout-rejected";
          case Result::MissingResponseCredit:
            return "missing-response-credit";
          case Result::LostReference: return "lost-reference";
          case Result::OccupancyMismatch: return "occupancy-mismatch";
          case Result::CapacityExceeded: return "capacity-exceeded";
        }
        return "unknown";
    }

  private:
    void
    bind(Transaction &transaction, VirtualSourceFanout *fanout,
         LineRefs *refs, int *slot_reserved_words, uint16_t word,
         bool final_use, WordRef ref)
    {
        transaction.owner = this;
        transaction.fanout = fanout;
        transaction.refs = refs;
        transaction.slot_reserved_words = slot_reserved_words;
        transaction.ref = ref;
        transaction.word = word;
        transaction.final_use = final_use;
        transaction.state = Transaction::State::Pending;
    }

    Result
    validatePending(const Transaction &transaction) const
    {
        if (transaction.owner == nullptr ||
            transaction.state == Transaction::State::Empty)
            return Result::InvalidTransaction;
        if (transaction.owner != this)
            return Result::StaleTransaction;
        if (transaction.state != Transaction::State::Pending)
            return Result::AlreadyResolved;
        return Result::Ok;
    }

    bool
    occupancyMatches() const
    {
        return payload.used() == static_cast<size_t>(
            combine_words + response_payload_words);
    }

    const bool enabled;
    const VirtualCombinePayloadStore &payload;
    const int &combine_words;
    int &reserved_response_words;
    int &response_payload_words;
    const int payload_limit;
    uint64_t &transfers;
    uint64_t &rollbacks;
    int &high_water;
    FanoutResult last_fanout_result = FanoutResult::Accepted;
};

} // namespace gem5::maa

#endif // __MEM_MAA_SHARED_PAYLOAD_TRANSFER_HH__
