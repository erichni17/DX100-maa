#ifndef __MEM_MAA_INLINE_OPERAND_RETIREMENT_HH__
#define __MEM_MAA_INLINE_OPERAND_RETIREMENT_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>

#include <gem5/maa_page_fed_soa_abi.hh>

namespace gem5::maa
{

struct InlineRetirementRecord
{
    uint32_t destination = UINT32_MAX;
    uint32_t valueBits = 0;
};

static_assert(sizeof(InlineRetirementRecord) ==
              InlineOperandPageFedABI::RetirementRecordBytes);

/**
 * Eight response credits plus one dense line packer.  A line becomes visible
 * only through markWriteResponse(), and its credit is reusable only after the
 * matching generation/sequence ACK.  No operation-sized record queue exists.
 */
class InlineOperandRetirementState
{
  public:
    static constexpr size_t Credits = 8;
    static constexpr size_t RecordsPerLine =
        InlineOperandPageFedABI::RetirementRecordsPerLine;
    static constexpr size_t LineBytes = 64;

    enum class Result : uint8_t
    {
        Accepted,
        Disabled,
        Busy,
        Inactive,
        StaleGeneration,
        Full,
        InvalidCredit,
        EarlyVisibility,
        DuplicateResponse,
        AckOrder,
        Incomplete,
    };

    enum class CreditState : uint8_t
    {
        Free,
        Reserved,
        WriteIssued,
        Visible,
    };

    struct Credit
    {
        std::array<uint8_t, LineBytes> payload{};
        uint64_t generation = 0;
        uint32_t sequence = UINT32_MAX;
        uint8_t records = 0;
        CreditState state = CreditState::Free;
    };

    Result open(bool enabled, uint64_t newGeneration)
    {
        if (!enabled)
            return Result::Disabled;
        if (active)
            return Result::Busy;
        if (newGeneration == 0 ||
            newGeneration > InlineOperandPageFedABI::GenerationMask ||
            newGeneration == generation)
            return Result::StaleGeneration;
        for (const auto &credit : credits)
            if (credit.state != CreditState::Free)
                return Result::Incomplete;
        active = true;
        closed = false;
        generation = newGeneration;
        nextSequence = 0;
        nextAck = 0;
        records = 0;
        writeIssues = 0;
        writeResponses = 0;
        acks = 0;
        highWater = 0;
        return Result::Accepted;
    }

    Result reserve(uint8_t recordCount, uint8_t &creditIndex)
    {
        if (!active)
            return Result::Inactive;
        if (recordCount == 0 || recordCount > RecordsPerLine)
            return Result::InvalidCredit;
        for (uint8_t index = 0; index < credits.size(); ++index) {
            Credit &credit = credits[index];
            if (credit.state != CreditState::Free)
                continue;
            credit = Credit();
            credit.generation = generation;
            credit.records = recordCount;
            credit.state = CreditState::Reserved;
            creditIndex = index;
            size_t used = 0;
            for (const auto &candidate : credits)
                used += candidate.state != CreditState::Free;
            if (used > highWater)
                highWater = used;
            return Result::Accepted;
        }
        return Result::Full;
    }

    Result fill(uint8_t creditIndex, const InlineRetirementRecord *source,
                uint8_t recordCount)
    {
        if (!valid(creditIndex))
            return Result::InvalidCredit;
        Credit &credit = credits[creditIndex];
        if (!active || credit.generation != generation)
            return Result::StaleGeneration;
        if (credit.state != CreditState::Reserved || source == nullptr ||
            recordCount == 0 || recordCount > RecordsPerLine)
            return Result::InvalidCredit;
        credit.records = recordCount;
        if (credit.sequence != UINT32_MAX)
            return Result::InvalidCredit;
        credit.sequence = nextSequence++;
        std::memcpy(credit.payload.data(), source,
                    recordCount * sizeof(InlineRetirementRecord));
        records += recordCount;
        return Result::Accepted;
    }

    Result markWriteIssued(uint8_t creditIndex)
    {
        if (!valid(creditIndex))
            return Result::InvalidCredit;
        Credit &credit = credits[creditIndex];
        if (!active || credit.generation != generation)
            return Result::StaleGeneration;
        if (credit.state != CreditState::Reserved)
            return Result::EarlyVisibility;
        credit.state = CreditState::WriteIssued;
        ++writeIssues;
        return Result::Accepted;
    }

    Result cancelReservation(uint8_t creditIndex)
    {
        if (!valid(creditIndex))
            return Result::InvalidCredit;
        Credit &credit = credits[creditIndex];
        if (!active || credit.generation != generation)
            return Result::StaleGeneration;
        if (credit.state != CreditState::Reserved)
            return Result::InvalidCredit;
        credit = Credit();
        return Result::Accepted;
    }

    Result markWriteResponse(uint8_t creditIndex)
    {
        if (!valid(creditIndex))
            return Result::InvalidCredit;
        Credit &credit = credits[creditIndex];
        if (!active || credit.generation != generation)
            return Result::StaleGeneration;
        if (credit.state == CreditState::Visible)
            return Result::DuplicateResponse;
        if (credit.state != CreditState::WriteIssued)
            return Result::EarlyVisibility;
        credit.state = CreditState::Visible;
        ++writeResponses;
        return Result::Accepted;
    }

    Result acknowledge(uint64_t candidateGeneration, uint32_t sequence)
    {
        if (!active)
            return Result::Inactive;
        if (candidateGeneration != generation)
            return Result::StaleGeneration;
        if (sequence != nextAck)
            return Result::AckOrder;
        for (auto &credit : credits) {
            if (credit.generation != generation ||
                credit.sequence != sequence)
                continue;
            if (credit.state != CreditState::Visible)
                return Result::EarlyVisibility;
            credit = Credit();
            ++nextAck;
            ++acks;
            return Result::Accepted;
        }
        return Result::InvalidCredit;
    }

    Result close()
    {
        if (!active)
            return Result::Inactive;
        closed = true;
        return Result::Accepted;
    }

    Result finish()
    {
        if (!active || !closed || writeIssues != writeResponses ||
            writeResponses != acks || nextSequence != nextAck)
            return Result::Incomplete;
        for (const auto &credit : credits)
            if (credit.state != CreditState::Free)
                return Result::Incomplete;
        active = false;
        closed = false;
        return Result::Accepted;
    }

    const Credit &credit(uint8_t index) const { return credits.at(index); }
    Credit &credit(uint8_t index) { return credits.at(index); }
    bool isActive() const { return active; }
    bool isClosed() const { return closed; }
    uint64_t currentGeneration() const { return generation; }
    uint32_t recordCount() const { return records; }
    uint32_t issuedLines() const { return writeIssues; }
    uint32_t respondedLines() const { return writeResponses; }
    uint32_t ackedLines() const { return acks; }
    uint32_t nextAckSequence() const { return nextAck; }
    size_t creditHighWater() const { return highWater; }
    bool visible(uint64_t candidateGeneration, uint32_t sequence) const
    {
        if (!active || candidateGeneration != generation)
            return false;
        for (const auto &credit : credits) {
            if (credit.generation == generation &&
                credit.sequence == sequence)
                return credit.state == CreditState::Visible;
        }
        return false;
    }
    size_t freeCredits() const
    {
        size_t free = 0;
        for (const auto &credit : credits)
            free += credit.state == CreditState::Free;
        return free;
    }

    static constexpr size_t IncrementalSramBytes =
        Credits * LineBytes + LineBytes + 16;

  private:
    bool valid(uint8_t index) const { return index < credits.size(); }

    std::array<Credit, Credits> credits{};
    uint64_t generation = 0;
    uint32_t nextSequence = 0;
    uint32_t nextAck = 0;
    uint32_t records = 0;
    uint32_t writeIssues = 0;
    uint32_t writeResponses = 0;
    uint32_t acks = 0;
    size_t highWater = 0;
    bool active = false;
    bool closed = false;
};

static_assert(InlineOperandRetirementState::IncrementalSramBytes == 592);
static_assert(InlineOperandRetirementState::IncrementalSramBytes <= 1024);

} // namespace gem5::maa

#endif // __MEM_MAA_INLINE_OPERAND_RETIREMENT_HH__
