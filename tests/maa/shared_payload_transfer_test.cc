#include <array>
#include <cstdint>
#include <cstdlib>
#include <iostream>

#include "mem/MAA/SharedPayloadTransfer.hh"

using Store = gem5::VirtualCombinePayloadStore;
using Transfer = gem5::maa::SharedPayloadTransfer;
using TransferResult = Transfer::Result;
using Fanout = gem5::maa::VirtualSourceFanout;
using FanoutResult = Fanout::Result;

static_assert(sizeof(Transfer::Transaction) <= 64,
              "shared transfer transaction metadata must remain bounded");

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;         \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

struct Ownership
{
    Store payload;
    Fanout fanout;
    Store::LineRefs response_refs = Store::emptyLineRefs();
    Store::LineRefs combine_refs = Store::emptyLineRefs();
    int slot_reserved_words = 0;
    int reserved_response_words = 0;
    int response_payload_words = 0;
    int combine_words = 0;
    uint64_t transfers = 0;
    uint64_t rollbacks = 0;
    int high_water = 0;
};

static void
initializeWord(Ownership &state, uint16_t word, uint16_t uses,
               size_t capacity)
{
    CHECK(state.payload.reset(capacity) == Store::Result::Ok);
    CHECK(state.fanout.reset(16) == FanoutResult::Accepted);
    for (uint16_t use = 0; use < uses; ++use)
        CHECK(state.fanout.observe(word) == FanoutResult::Accepted);
    CHECK(state.fanout.seal(uses) == FanoutResult::Accepted);

    const std::array<uint8_t, 4> bytes{{0x12, 0x34, 0x56, 0x78}};
    CHECK(state.payload.allocate(bytes.data(), bytes.size(),
                                 state.response_refs[word]) ==
          Store::Result::Ok);
    state.slot_reserved_words = 1;
    state.reserved_response_words = 1;
    state.response_payload_words = 1;
    state.high_water = 1;
}

static Transfer
makeTransfer(Ownership &state)
{
    return Transfer(
        true, state.payload, state.combine_words,
        state.reserved_response_words, state.response_payload_words, 2,
        state.transfers, state.rollbacks, state.high_water);
}

static void
checkRollbackRetryCommitAndClosure()
{
    constexpr uint16_t Word = 5;
    Ownership state;
    initializeWord(state, Word, 2, 2);
    Transfer transfer = makeTransfer(state);

    const Store::WordRef source_ref = state.response_refs[Word];
    const uint8_t *source_data = state.payload.data(source_ref);
    CHECK(source_data != nullptr);

    Transfer::Transaction nonfinal;
    CHECK(transfer.begin(state.fanout, state.response_refs,
                         state.slot_reserved_words, Word, nonfinal) ==
          TransferResult::Ok);
    CHECK(!nonfinal.finalUse());
    CHECK(nonfinal.wordRef() == Store::InvalidWord);
    CHECK(state.fanout.uses(Word) == 1);
    CHECK(state.response_refs[Word] == source_ref);
    CHECK(state.slot_reserved_words == 1);
    CHECK(state.reserved_response_words == 1);
    CHECK(state.response_payload_words == 1);

    CHECK(state.payload.allocate(source_data, 4, state.combine_refs[0]) ==
          Store::Result::Ok);
    ++state.combine_words;
    CHECK(transfer.commit(nonfinal) == TransferResult::Ok);
    CHECK(state.payload.used() == 2);
    CHECK(state.combine_words + state.response_payload_words == 2);
    CHECK(state.transfers == 0);

    Transfer::Transaction failed_final;
    CHECK(transfer.begin(state.fanout, state.response_refs,
                         state.slot_reserved_words, Word, failed_final) ==
          TransferResult::Ok);
    CHECK(failed_final.finalUse());
    CHECK(failed_final.wordRef() == source_ref);
    CHECK(state.fanout.uses(Word) == 0);
    CHECK(state.response_refs[Word] == Store::InvalidWord);
    CHECK(state.slot_reserved_words == 0);
    CHECK(state.reserved_response_words == 0);
    CHECK(state.response_payload_words == 0);
    CHECK(state.payload.used() == 2);

    // Simulated insert failure: combiner ownership and pool are unchanged.
    CHECK(transfer.rollback(failed_final) == TransferResult::Ok);
    CHECK(state.fanout.uses(Word) == 1);
    CHECK(state.fanout.remainingUses() == 1);
    CHECK(state.response_refs[Word] == source_ref);
    CHECK(state.slot_reserved_words == 1);
    CHECK(state.reserved_response_words == 1);
    CHECK(state.response_payload_words == 1);
    CHECK(state.combine_words == 1);
    CHECK(state.payload.used() == 2);
    CHECK(state.combine_words + state.response_payload_words == 2);
    CHECK(state.rollbacks == 1);

    Transfer::Transaction retry;
    CHECK(transfer.begin(state.fanout, state.response_refs,
                         state.slot_reserved_words, Word, retry) ==
          TransferResult::Ok);
    CHECK(retry.finalUse());
    CHECK(retry.wordRef() == source_ref);
    state.combine_refs[1] = retry.wordRef();
    ++state.combine_words;
    CHECK(transfer.commit(retry) == TransferResult::Ok);
    CHECK(state.fanout.empty());
    CHECK(state.response_refs[Word] == Store::InvalidWord);
    CHECK(state.slot_reserved_words == 0);
    CHECK(state.reserved_response_words == 0);
    CHECK(state.response_payload_words == 0);
    CHECK(state.payload.used() == 2);
    CHECK(state.combine_words == 2);
    CHECK(state.transfers == 1);
    CHECK(state.high_water == 2);

    CHECK(state.payload.release(state.combine_refs[0]) == Store::Result::Ok);
    --state.combine_words;
    CHECK(state.payload.release(state.combine_refs[1]) == Store::Result::Ok);
    --state.combine_words;
    CHECK(state.payload.empty());
    CHECK(state.combine_words == 0);
    CHECK(state.fanout.empty());
    CHECK(state.slot_reserved_words == 0);
    CHECK(state.reserved_response_words == 0);
    CHECK(state.response_payload_words == 0);
}

static void
checkIllegalStaleAndDoubleRollbackRejection()
{
    constexpr uint16_t Word = 3;
    Ownership state;
    initializeWord(state, Word, 1, 1);
    Transfer owner = makeTransfer(state);
    Transfer stale_owner = makeTransfer(state);

    Transfer::Transaction illegal;
    CHECK(owner.rollback(illegal) == TransferResult::InvalidTransaction);

    Transfer::Transaction pending;
    CHECK(owner.begin(state.fanout, state.response_refs,
                      state.slot_reserved_words, Word, pending) ==
          TransferResult::Ok);
    CHECK(pending.finalUse());
    CHECK(pending.wordRef() != Store::InvalidWord);
    CHECK(stale_owner.rollback(pending) ==
          TransferResult::StaleTransaction);
    CHECK(state.fanout.uses(Word) == 0);
    CHECK(state.response_refs[Word] == Store::InvalidWord);
    CHECK(state.slot_reserved_words == 0);
    CHECK(state.reserved_response_words == 0);
    CHECK(state.response_payload_words == 0);
    CHECK(owner.rollback(pending) == TransferResult::Ok);
    CHECK(owner.rollback(pending) == TransferResult::AlreadyResolved);
    CHECK(state.fanout.uses(Word) == 1);
    CHECK(state.fanout.remainingUses() == 1);
    CHECK(state.response_refs[Word] != Store::InvalidWord);
    CHECK(state.slot_reserved_words == 1);
    CHECK(state.reserved_response_words == 1);
    CHECK(state.response_payload_words == 1);
    CHECK(state.payload.used() == 1);

    CHECK(state.payload.release(state.response_refs[Word]) ==
          Store::Result::Ok);
}

int
main()
{
    checkRollbackRetryCommitAndClosure();
    checkIllegalStaleAndDoubleRollbackRejection();
    std::cout << "PASS shared payload transfer\n";
    return 0;
}
