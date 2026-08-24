#ifndef __MEM_MAA_SOA_JIT_WRITE_RETIREMENT_HH__
#define __MEM_MAA_SOA_JIT_WRITE_RETIREMENT_HH__

#include <array>
#include <cstddef>
#include <cstdint>

namespace gem5
{

/**
 * Fixed exact-response ownership for completed SoA/JIT A-line writes.
 *
 * reserve() runs before the response-bearing packet is enqueued.  commit()
 * transfers ownership from the full A-line context only after the MAA packet
 * queue owns its copied payload.  A credit cannot be reused until the exact
 * generation/sequence/address identity is acknowledged.  Hardware relies on
 * reliable exactly-once response delivery: the transient three-bit credit
 * tag indexes the charged persistent identity and is never reused before its
 * acknowledgement.
 *
 * reservations/issues/responses/highWater are simulator validation counters,
 * not additional installed hardware.  PersistentStateBits charges only the
 * functional generation, allocator sequence, and eight identity/state slots.
 */
class SoaJitWriteRetirement
{
  public:
    static constexpr size_t Credits = 8;
    static constexpr size_t LineBytes = 64;
    static constexpr size_t CreditBits = 3;
    static constexpr size_t StateBits = 2;
    static constexpr size_t PersistentStateBits =
        2 * 64 + Credits * (64 + 64 + StateBits);
    static constexpr size_t PersistentStateBytes =
        (PersistentStateBits + 7) / 8;
    static constexpr size_t TransientResponseCreditTagBits = CreditBits;
    static constexpr size_t MaxTransientResponseCreditTagBits =
        Credits * TransientResponseCreditTagBits;
    static constexpr size_t MaxTransientResponseCreditTagBytes =
        (MaxTransientResponseCreditTagBits + 7) / 8;
    static constexpr size_t MaxTransientPacketPayloadBytes =
        Credits * LineBytes;

    enum class Result : uint8_t
    {
        Accepted,
        Inactive,
        Busy,
        Full,
        InvalidGeneration,
        InvalidAddress,
        CreditOutOfRange,
        NotReserved,
        NotOutstanding,
        WrongGeneration,
        WrongSequence,
        WrongAddress,
        NotComplete,
    };

    struct Identity
    {
        uint64_t generation = 0;
        uint64_t issueSequence = 0;
        uint64_t address = 0;
        uint8_t credit = 0;
    };

    Result begin(uint64_t generation)
    {
        if (active)
            return Result::Busy;
        if (generation == 0)
            return Result::InvalidGeneration;
        clearRun();
        active = true;
        activeGeneration = generation;
        return Result::Accepted;
    }

    Result reserve(uint64_t generation, uint64_t address,
                   Identity *identity)
    {
        if (identity != nullptr)
            *identity = Identity{};
        if (!active)
            return Result::Inactive;
        if (generation != activeGeneration)
            return Result::InvalidGeneration;
        if (address % LineBytes != 0)
            return Result::InvalidAddress;

        for (size_t credit = 0; credit < Credits; ++credit) {
            Slot &slot = slots[credit];
            if (slot.state != State::Free)
                continue;
            slot.state = State::Reserved;
            slot.issueSequence = ++nextIssueSequence;
            slot.address = address;
            ++reservations;
            highWater = occupied() > highWater ? occupied() : highWater;
            if (identity != nullptr) {
                identity->generation = activeGeneration;
                identity->issueSequence = slot.issueSequence;
                identity->address = slot.address;
                identity->credit = static_cast<uint8_t>(credit);
            }
            return Result::Accepted;
        }
        return Result::Full;
    }

    Result commit(const Identity &identity)
    {
        const Result validation = validate(identity, State::Reserved);
        if (validation != Result::Accepted)
            return validation == Result::NotOutstanding
                ? Result::NotReserved : validation;
        slots[identity.credit].state = State::AwaitingResponse;
        ++issues;
        return Result::Accepted;
    }

    Result acknowledge(const Identity &identity)
    {
        const Result validation =
            validate(identity, State::AwaitingResponse);
        if (validation != Result::Accepted)
            return validation;
        slots[identity.credit] = Slot{};
        ++responses;
        return Result::Accepted;
    }

    Result finish()
    {
        if (!active)
            return Result::Inactive;
        if (!complete())
            return Result::NotComplete;
        active = false;
        return Result::Accepted;
    }

    Result reset()
    {
        if (!empty())
            return Result::Busy;
        clearRun();
        return Result::Accepted;
    }

    bool empty() const { return occupied() == 0; }
    bool activeRun() const { return active; }
    bool complete() const
    {
        return active && empty() && reservations == issues &&
               issues == responses && assertInvariants();
    }

    size_t occupied() const
    {
        size_t count = 0;
        for (const auto &slot : slots)
            count += slot.state == State::Free ? 0 : 1;
        return count;
    }

    size_t reserved() const
    {
        size_t count = 0;
        for (const auto &slot : slots)
            count += slot.state == State::Reserved ? 1 : 0;
        return count;
    }

    size_t awaitingResponses() const
    {
        size_t count = 0;
        for (const auto &slot : slots)
            count += slot.state == State::AwaitingResponse ? 1 : 0;
        return count;
    }

    size_t creditHighWater() const { return highWater; }
    uint64_t reservationCount() const { return reservations; }
    uint64_t issueCount() const { return issues; }
    uint64_t responseCount() const { return responses; }

    std::array<uint8_t, Credits> awaitingByCredit() const
    {
        std::array<uint8_t, Credits> result{};
        for (size_t credit = 0; credit < Credits; ++credit)
            result[credit] =
                slots[credit].state == State::AwaitingResponse ? 1 : 0;
        return result;
    }

    bool assertInvariants() const
    {
        return occupied() <= Credits && reserved() <= occupied() &&
               awaitingResponses() <= occupied() &&
               reserved() + awaitingResponses() == occupied() &&
               issues <= reservations && responses <= issues &&
               reservations - issues == reserved() &&
               issues - responses == awaitingResponses() &&
               highWater <= Credits &&
               (active || (empty() && reservations == 0 && issues == 0 &&
                            responses == 0));
    }

  private:
    enum class State : uint8_t
    {
        Free,
        Reserved,
        AwaitingResponse,
    };

    struct Slot
    {
        uint64_t issueSequence = 0;
        uint64_t address = 0;
        State state = State::Free;
    };

    Result validate(const Identity &identity, State expected) const
    {
        if (!active)
            return Result::Inactive;
        if (identity.generation != activeGeneration)
            return Result::WrongGeneration;
        if (identity.credit >= Credits)
            return Result::CreditOutOfRange;
        const Slot &slot = slots[identity.credit];
        if (slot.state != expected)
            return Result::NotOutstanding;
        if (identity.issueSequence != slot.issueSequence)
            return Result::WrongSequence;
        if (identity.address != slot.address)
            return Result::WrongAddress;
        return Result::Accepted;
    }

    void clearRun()
    {
        slots = {};
        active = false;
        activeGeneration = 0;
        nextIssueSequence = 0;
        reservations = 0;
        issues = 0;
        responses = 0;
        highWater = 0;
    }

    std::array<Slot, Credits> slots{};
    uint64_t activeGeneration = 0;
    uint64_t nextIssueSequence = 0;
    uint64_t reservations = 0;
    uint64_t issues = 0;
    uint64_t responses = 0;
    size_t highWater = 0;
    bool active = false;
};

static_assert(SoaJitWriteRetirement::Credits == 8);
static_assert(SoaJitWriteRetirement::PersistentStateBits == 1168);
static_assert(SoaJitWriteRetirement::PersistentStateBytes == 146);
static_assert(SoaJitWriteRetirement::TransientResponseCreditTagBits == 3);
static_assert(SoaJitWriteRetirement::MaxTransientResponseCreditTagBits == 24);
static_assert(SoaJitWriteRetirement::MaxTransientResponseCreditTagBytes == 3);
static_assert(SoaJitWriteRetirement::MaxTransientPacketPayloadBytes == 512);

} // namespace gem5

#endif // __MEM_MAA_SOA_JIT_WRITE_RETIREMENT_HH__
