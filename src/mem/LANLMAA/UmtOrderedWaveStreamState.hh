#ifndef __MEM_LANLMAA_UMT_ORDERED_WAVE_STREAM_STATE_HH__
#define __MEM_LANLMAA_UMT_ORDERED_WAVE_STREAM_STATE_HH__

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>

#include "mem/LANLMAA/UmtOrderedWaveDescriptor.hh"

namespace gem5
{
namespace lanlmaa
{

inline double
umtOrderedWaveStreamDecodeFp64(uint64_t bits)
{
    double value = 0.0;
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}

inline uint64_t
umtOrderedWaveStreamEncodeFp64(double value)
{
    uint64_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}

constexpr size_t
umtOrderedWaveStreamBitsForStates(size_t states)
{
    size_t bits = 0;
    for (size_t maximum = states - 1; maximum != 0; maximum >>= 1)
        ++bits;
    return bits;
}

// Physical opcode-11 state carried by the existing paired
// operation/continuation rows.  Denominators remain in the returned line
// packet; a completed result overwrites its dead source word in place.
template <size_t ComputeTokenCount, size_t DividerLaneCount,
          uint64_t DividerInitiationIntervalCycles,
          size_t GlobalFpIssueWidth = 1>
class UmtOrderedWaveStreamStateModel
{
  public:
    static_assert(ComputeTokenCount != 0);
    static_assert(ComputeTokenCount <= UmtOrderedWaveMaximumGroups);
    static_assert(DividerLaneCount != 0);
    static_assert(DividerLaneCount <= UmtOrderedWaveMaximumGroups);
    static_assert(DividerInitiationIntervalCycles != 0);
    static_assert(DividerInitiationIntervalCycles <= 64);
    static_assert(GlobalFpIssueWidth != 0);
    static_assert(GlobalFpIssueWidth <= 2);
    static_assert(GlobalFpIssueWidth <= ComputeTokenCount);

    static constexpr size_t Banks = 4;
    static constexpr size_t RowsPerBank =
        UmtOrderedWaveMaximumGroups / Banks;
    static constexpr size_t SourceResultWords = UmtOrderedWaveCorners;
    static constexpr size_t MetadataWords = 1;
    static constexpr size_t AllocatedBits =
        UmtOrderedWaveMaximumGroups *
        (SourceResultWords + MetadataWords) * 64;
    static constexpr size_t AllocatedBytes = AllocatedBits / 8;
    static constexpr size_t PhysicalBits =
        UmtOrderedWaveMaximumGroups * 640;
    static constexpr size_t PhysicalBytes = PhysicalBits / 8;
    static constexpr size_t ResidualBytes = PhysicalBytes - AllocatedBytes;

    struct Reservation
    {
        bool accepted = false;
        DescriptorError error = DescriptorError::BadStartState;
        uint64_t readyCycle = 0;
        uint64_t stallCycles = 0;
        uint64_t bankReads = 0;
        uint64_t bankWrites = 0;
    };

    static constexpr size_t ComputeTokens = ComputeTokenCount;
    static constexpr size_t DividerLanes = DividerLaneCount;
    static constexpr uint64_t DivideLatency = 64;
    static constexpr uint64_t DividerInitiationInterval =
        DividerInitiationIntervalCycles;
    static constexpr size_t FpIssueWidth = GlobalFpIssueWidth;
    // Combinational lower bounds for a direct priority-encoder
    // implementation.  They count selector candidate inputs and routed bank
    // operand bits, not gates, wires, physical area, timing, power, or energy.
    static constexpr size_t FpIssueSelectionCandidateInputs =
        ComputeTokens * FpIssueWidth;
    static constexpr size_t FpIssueOperandRouteBits =
        64 * FpIssueWidth;
    static constexpr size_t IncrementalFpIssueSelectionCandidateInputs =
        ComputeTokens * (FpIssueWidth - 1);
    static constexpr size_t IncrementalFpIssueOperandRouteBits =
        64 * (FpIssueWidth - 1);
    // Minimum logical width of the independently represented Token fields:
    // phase4 + operation6 + group6 + corner3 + destination4 + readyCycle64
    // + six FP64 values.  This excludes compiler padding, ECC, queues,
    // arbiters, muxing, and every non-Token field in this class.
    static constexpr size_t RepresentedTokenLogicalBitsFloor =
        4 + 6 + 6 + 3 + 4 + 64 + 6 * 64;
    // Independently derived logical widths of every represented state-model
    // field outside the 5 KiB paired store. The decoded descriptor is shared
    // with the engine's existing descriptor register rather than copied into
    // this model. Compiler padding, ECC, datapath gates, muxes, clocking,
    // physical register cells, and unrepresented control are excluded.
    static constexpr size_t FunctionalControlLogicalBitsFloor =
        umtOrderedWaveStreamBitsForStates(ComputeTokens + 1) +
        umtOrderedWaveStreamBitsForStates(ComputeTokens) +
        1 + 5 + 2 * 64 + DividerLanes * 64;
    static constexpr size_t BankSchedulerLogicalBitsFloor =
        umtOrderedWaveStreamBitsForStates(
            UmtOrderedWaveMaximumGroups + 1) +
        Banks * umtOrderedWaveStreamBitsForStates(RowsPerBank + 1) +
        Banks * 64;
    static constexpr size_t InstrumentationLogicalBitsFloor =
        umtOrderedWaveStreamBitsForStates(
            UmtOrderedWaveMaximumGroups + 1) +
        umtOrderedWaveStreamBitsForStates(RowsPerBank + 1) +
        umtOrderedWaveStreamBitsForStates(ComputeTokens + 1) +
        17 * 64;
    static constexpr size_t AuxiliaryLogicalBitsFloor =
        ComputeTokens * RepresentedTokenLogicalBitsFloor +
        FunctionalControlLogicalBitsFloor +
        BankSchedulerLogicalBitsFloor +
        InstrumentationLogicalBitsFloor;
    static constexpr size_t PhysicalStorePlusLogicalAuxiliaryBitsFloor =
        PhysicalBits + AuxiliaryLogicalBitsFloor;

    struct CycleResult
    {
        std::array<size_t, ComputeTokens> completedOperations{};
        size_t completions = 0;
        DescriptorError error = DescriptorError::None;
    };

    bool configure(size_t groups)
    {
        clear();
        if (groups == 0 || groups > UmtOrderedWaveMaximumGroups) {
            ++capacityErrors;
            return false;
        }
        activeGroups = groups;
        occupancyHighWater = groups;
        for (size_t group = 0; group < groups; ++group) {
            ++bankOccupancy[group % Banks];
            metadata(group) = AllocatedMask;
        }
        bankOccupancyHighWater = *std::max_element(
            bankOccupancy.begin(), bankOccupancy.end());
        return true;
    }

    void clear()
    {
        activeGroups = 0;
        occupancyHighWater = 0;
        bankOccupancyHighWater = 0;
        capacityErrors = 0;
        bankAccesses = 0;
        bankReadAccesses = 0;
        bankWriteAccesses = 0;
        bankStallCycles = 0;
        sourceWrites = 0;
        denominatorWords = 0;
        resultWordsProduced = 0;
        resultReads = 0;
        bankOccupancy.fill(0);
        nextBankCycle.fill(0);
        words = {};
        metadataWords = {};
        tokens = {};
        boundDescriptor = nullptr;
        activeTokens = 0;
        tokenHighWater = 0;
        issueCursor = 0;
        addNextIssue = 0;
        multiplyNextIssue = 0;
        dividerNextIssue.fill(0);
        tokenBackpressureEvents = 0;
        pipelineActiveCycleCount = 0;
        fpIssueStallCycles = 0;
        fpOperationIssueCount = 0;
        dualIssueCycleCount = 0;
        bankConflictCycles = 0;
        writebackStallCycles = 0;
        resultBankStallCycleCount = 0;
        latchedError = DescriptorError::None;
    }

    bool bindDescriptor(const UmtOrderedWaveDescriptor &descriptor)
    {
        if (activeGroups == 0 || descriptor.groupCount != activeGroups ||
            activeTokens != 0 || boundDescriptor != nullptr) {
            reject(DescriptorError::BadStartState);
            return false;
        }
        // The engine retains its decoded descriptor until terminal rearm, so
        // the state model consumes that existing register without a duplicate
        // descriptor-sized allocation.
        boundDescriptor = &descriptor;
        return true;
    }

    size_t availableTokens() const { return ComputeTokens - activeTokens; }
    size_t tokensInUse() const { return activeTokens; }
    size_t tokenHighWaterMark() const { return tokenHighWater; }
    uint64_t tokenBackpressure() const { return tokenBackpressureEvents; }
    uint64_t pipelineActiveCycles() const
    {
        return pipelineActiveCycleCount;
    }
    uint64_t fpIssueStalls() const { return fpIssueStallCycles; }
    uint64_t fpOperationsIssued() const { return fpOperationIssueCount; }
    uint64_t dualIssueCycles() const { return dualIssueCycleCount; }
    uint64_t bankConflicts() const { return bankConflictCycles; }
    uint64_t writebackStalls() const { return writebackStallCycles; }
    uint64_t resultBankStalls() const
    {
        return resultBankStallCycleCount;
    }
    DescriptorError error() const { return latchedError; }

    void recordTokenCapacityBackpressure()
    {
        ++tokenBackpressureEvents;
    }

    Reservation enqueueDenominator(
        size_t operation, size_t group, size_t corner,
        uint64_t denominatorBits)
    {
        if (boundDescriptor == nullptr || !validGroup(group) ||
            corner >= SourceResultWords) {
            return reject(DescriptorError::BadStartState);
        }
        uint64_t &state = metadata(group);
        if ((state & SourceValidMask) != SourceValidMask ||
            nextCorner(state) != corner || (state & BusyMask) != 0) {
            return reject(DescriptorError::BadStartState);
        }
        const double value =
            umtOrderedWaveStreamDecodeFp64(denominatorBits);
        if (!std::isfinite(value))
            return reject(DescriptorError::BadRecordValue);
        auto iterator = std::find_if(
            tokens.begin(), tokens.end(),
            [](const Token &token) {
                return token.phase == TokenPhase::Free;
            });
        if (iterator == tokens.end()) {
            return {false, DescriptorError::None, 0, 0, 0, 0};
        }
        *iterator = {};
        iterator->phase = TokenPhase::DenominatorAddPending;
        iterator->operation = operation;
        iterator->group = group;
        iterator->corner = corner;
        iterator->denominatorInput = value;
        state |= BusyMask;
        ++activeTokens;
        ++denominatorWords;
        tokenHighWater = std::max(tokenHighWater, activeTokens);
        return {true, DescriptorError::None, 0, 0, 0, 0};
    }

    CycleResult cycle(uint64_t cycle)
    {
        CycleResult result;
        if (latchedError != DescriptorError::None) {
            result.error = latchedError;
            return result;
        }
        if (activeTokens != 0)
            ++pipelineActiveCycleCount;

        bool bankConflictRecorded = false;
        bool writebackStallRecorded = false;
        bool resultBankStallRecorded = false;

        for (auto &token : tokens) {
            if (token.phase == TokenPhase::DenominatorAddWait &&
                cycle >= token.readyCycle) {
                token.phase = TokenPhase::DividePending;
            } else if (token.phase == TokenPhase::DivideWait &&
                       cycle >= token.readyCycle) {
                token.flux = token.numerator / token.denominator;
                if (!std::isfinite(token.flux)) {
                    poison(DescriptorError::BadRecordValue);
                    break;
                }
                token.destination = token.corner + 1;
                skipInactiveEdges(token);
                token.phase = token.destination == SourceResultWords ?
                    TokenPhase::ResultWritePending :
                    TokenPhase::MultiplyPending;
            } else if (token.phase == TokenPhase::MultiplyWait &&
                       cycle >= token.readyCycle) {
                token.phase = TokenPhase::EdgeAddPending;
            } else if (token.phase == TokenPhase::EdgeAddWait &&
                       cycle >= token.readyCycle) {
                if (!reserveNow(token.group, cycle, false, true)) {
                    if (!writebackStallRecorded) {
                        ++writebackStallCycles;
                        writebackStallRecorded = true;
                    }
                    if (!resultBankStallRecorded) {
                        ++resultBankStallCycleCount;
                        resultBankStallRecorded = true;
                    }
                    continue;
                }
                word(token.group, token.destination) =
                    umtOrderedWaveStreamEncodeFp64(token.updatedSource);
                ++token.destination;
                skipInactiveEdges(token);
                token.phase = token.destination == SourceResultWords ?
                    TokenPhase::ResultWritePending :
                    TokenPhase::MultiplyPending;
            }
        }
        if (latchedError != DescriptorError::None) {
            result.error = latchedError;
            return result;
        }

        for (auto &token : tokens) {
            if (token.phase != TokenPhase::ResultWritePending)
                continue;
            if (!reserveNow(token.group, cycle, false, true)) {
                if (!writebackStallRecorded) {
                    ++writebackStallCycles;
                    writebackStallRecorded = true;
                }
                if (!resultBankStallRecorded) {
                    ++resultBankStallCycleCount;
                    resultBankStallRecorded = true;
                }
                continue;
            }
            word(token.group, token.corner) =
                umtOrderedWaveStreamEncodeFp64(token.flux);
            uint64_t &state = metadata(token.group);
            state |= uint64_t{1} << (ResultValidShift + token.corner);
            state &= ~BusyMask;
            setNextCorner(state, token.corner + 1);
            ++resultWordsProduced;
            result.completedOperations[result.completions++] = token.operation;
            token = {};
            --activeTokens;
        }

        // Writebacks above have first priority and have already reserved any
        // bank ports they consume. Each FP slot then walks the same
        // round-robin cursor. Per-unit next-issue cycles prevent two adds or
        // two multiplies in one cycle, divider lane state limits divides, and
        // reserveNow() rejects same-bank read pairs.
        size_t issues = 0;
        for (size_t slot = 0; slot < FpIssueWidth; ++slot) {
            bool issued = false;
            for (size_t probe = 0;
                 probe < tokens.size() && !issued; ++probe) {
                const size_t index = (issueCursor + probe) % tokens.size();
                auto &token = tokens[index];
                switch (token.phase) {
              case TokenPhase::DenominatorAddPending:
                if (cycle < addNextIssue)
                    break;
                token.denominator =
                    boundDescriptor->sumArea[token.corner] +
                    token.denominatorInput;
                if (!std::isfinite(token.denominator) ||
                    token.denominator <= 0.0) {
                    poison(DescriptorError::BadRecordValue);
                    result.error = latchedError;
                    return result;
                }
                token.readyCycle = cycle + 1;
                token.phase = TokenPhase::DenominatorAddWait;
                addNextIssue = cycle + 1;
                issued = true;
                break;
              case TokenPhase::DividePending: {
                size_t lane = dividerNextIssue.size();
                for (size_t candidate = 0;
                     candidate < dividerNextIssue.size(); ++candidate) {
                    if (cycle >= dividerNextIssue[candidate]) {
                        lane = candidate;
                        break;
                    }
                }
                if (lane == dividerNextIssue.size())
                    break;
                if (!reserveNow(token.group, cycle, true, false)) {
                    if (!bankConflictRecorded) {
                        ++bankConflictCycles;
                        bankConflictRecorded = true;
                    }
                    if (!resultBankStallRecorded) {
                        ++resultBankStallCycleCount;
                        resultBankStallRecorded = true;
                    }
                    break;
                }
                token.numerator = umtOrderedWaveStreamDecodeFp64(
                    word(token.group, token.corner));
                if (!std::isfinite(token.numerator)) {
                    poison(DescriptorError::BadRecordValue);
                    result.error = latchedError;
                    return result;
                }
                token.readyCycle = cycle + DivideLatency;
                token.phase = TokenPhase::DivideWait;
                dividerNextIssue[lane] =
                    cycle + DividerInitiationInterval;
                issued = true;
                break;
              }
              case TokenPhase::MultiplyPending:
                if (cycle < multiplyNextIssue)
                    break;
                token.product = boundDescriptor->coefficients[
                    umtOrderedWaveCoefficientIndex(
                        token.corner, token.destination)] * token.flux;
                if (!std::isfinite(token.product)) {
                    poison(DescriptorError::BadRecordValue);
                    result.error = latchedError;
                    return result;
                }
                token.readyCycle = cycle + 1;
                token.phase = TokenPhase::MultiplyWait;
                multiplyNextIssue = cycle + 1;
                issued = true;
                break;
              case TokenPhase::EdgeAddPending:
                if (cycle < addNextIssue)
                    break;
                if (!reserveNow(token.group, cycle, true, false)) {
                    if (!bankConflictRecorded) {
                        ++bankConflictCycles;
                        bankConflictRecorded = true;
                    }
                    if (!resultBankStallRecorded) {
                        ++resultBankStallCycleCount;
                        resultBankStallRecorded = true;
                    }
                    break;
                }
                token.updatedSource = umtOrderedWaveStreamDecodeFp64(
                    word(token.group, token.destination)) + token.product;
                if (!std::isfinite(token.updatedSource)) {
                    poison(DescriptorError::BadRecordValue);
                    result.error = latchedError;
                    return result;
                }
                token.readyCycle = cycle + 1;
                token.phase = TokenPhase::EdgeAddWait;
                addNextIssue = cycle + 1;
                issued = true;
                break;
              default:
                break;
                }
                if (issued)
                    issueCursor = (index + 1) % tokens.size();
            }
            if (!issued)
                break;
            ++issues;
            ++fpOperationIssueCount;
        }
        if (issues == 0 && activeTokens != 0)
            ++fpIssueStallCycles;
        if (issues == 2)
            ++dualIssueCycleCount;
        return result;
    }

    Reservation writeSource(
        size_t group, size_t source, uint64_t value, uint64_t cycle)
    {
        if (!validGroup(group) || source >= SourceResultWords)
            return reject(DescriptorError::TooManyItems);
        uint64_t &state = metadata(group);
        const uint64_t bit = uint64_t{1} << (SourceValidShift + source);
        if ((state & bit) != 0 || nextCorner(state) != 0)
            return reject(DescriptorError::BadStartState);
        auto reservation = reserve(group, cycle, false, true);
        word(group, source) = value;
        state |= bit;
        ++sourceWrites;
        return reservation;
    }

    Reservation consumeDenominator(
        size_t group, size_t corner, uint64_t denominatorBits,
        const UmtOrderedWaveDescriptor &descriptor, uint64_t cycle)
    {
        if (!validGroup(group) || corner >= SourceResultWords)
            return reject(DescriptorError::TooManyItems);
        uint64_t &state = metadata(group);
        if ((state & SourceValidMask) != SourceValidMask ||
            nextCorner(state) != corner ||
            (state & (uint64_t{1} << (ResultValidShift + corner))) != 0) {
            return reject(DescriptorError::BadStartState);
        }
        const double sigtVolume =
            umtOrderedWaveStreamDecodeFp64(denominatorBits);
        if (!std::isfinite(sigtVolume) ||
            !std::isfinite(descriptor.sumArea[corner])) {
            return reject(DescriptorError::BadRecordValue);
        }

        Reservation total{true, DescriptorError::None, cycle, 0, 0, 0};
        merge(total, reserve(group, total.readyCycle, true, false));
        const double denominator =
            descriptor.sumArea[corner] + sigtVolume;
        const double source =
            umtOrderedWaveStreamDecodeFp64(word(group, corner));
        if (!std::isfinite(source) || !std::isfinite(denominator) ||
            denominator <= 0.0) {
            return reject(DescriptorError::BadRecordValue);
        }
        const double flux = source / denominator;
        if (!std::isfinite(flux))
            return reject(DescriptorError::BadRecordValue);

        for (size_t destination = corner + 1;
             destination < SourceResultWords; ++destination) {
            const double coefficient = descriptor.coefficients[
                umtOrderedWaveCoefficientIndex(corner, destination)];
            if (coefficient == 0.0)
                continue;
            merge(total, reserve(group, total.readyCycle, true, false));
            const double oldSource =
                umtOrderedWaveStreamDecodeFp64(word(group, destination));
            const double newSource = oldSource + coefficient * flux;
            if (!std::isfinite(oldSource) || !std::isfinite(newSource))
                return reject(DescriptorError::BadRecordValue);
            merge(total, reserve(group, total.readyCycle, false, true));
            word(group, destination) =
                umtOrderedWaveStreamEncodeFp64(newSource);
        }
        merge(total, reserve(group, total.readyCycle, false, true));
        word(group, corner) = umtOrderedWaveStreamEncodeFp64(flux);
        state |= uint64_t{1} << (ResultValidShift + corner);
        setNextCorner(state, corner + 1);
        ++denominatorWords;
        ++resultWordsProduced;
        return total;
    }

    Reservation readResult(
        size_t group, size_t corner, uint64_t cycle, uint64_t &value)
    {
        if (!validGroup(group) || corner >= SourceResultWords)
            return reject(DescriptorError::TooManyItems);
        const uint64_t state = metadata(group);
        if ((state & (uint64_t{1} << (ResultValidShift + corner))) == 0)
            return reject(DescriptorError::BadStartState);
        auto reservation = reserve(group, cycle, true, false);
        value = word(group, corner);
        ++resultReads;
        return reservation;
    }

    bool complete() const
    {
        for (size_t group = 0; group < activeGroups; ++group) {
            if ((metadata(group) & ResultValidMask) != ResultValidMask)
                return false;
        }
        return activeGroups != 0;
    }

    uint64_t readyCycle() const
    {
        return *std::max_element(nextBankCycle.begin(), nextBankCycle.end());
    }

    size_t groups() const { return activeGroups; }
    size_t highWater() const { return occupancyHighWater; }
    size_t bankHighWater() const { return bankOccupancyHighWater; }
    uint64_t errors() const { return capacityErrors; }
    uint64_t accesses() const { return bankAccesses; }
    uint64_t reads() const { return bankReadAccesses; }
    uint64_t writes() const { return bankWriteAccesses; }
    uint64_t stalls() const { return bankStallCycles; }
    uint64_t acceptedSourceWrites() const { return sourceWrites; }
    uint64_t consumedDenominators() const { return denominatorWords; }
    uint64_t producedResults() const { return resultWordsProduced; }
    uint64_t acceptedResultReads() const { return resultReads; }

  private:
    enum class TokenPhase : uint8_t
    {
        Free = 0,
        DenominatorAddPending,
        DenominatorAddWait,
        DividePending,
        DivideWait,
        MultiplyPending,
        MultiplyWait,
        EdgeAddPending,
        EdgeAddWait,
        ResultWritePending
    };

    struct Token
    {
        TokenPhase phase = TokenPhase::Free;
        size_t operation = 0;
        size_t group = 0;
        size_t corner = 0;
        size_t destination = 0;
        uint64_t readyCycle = 0;
        double denominatorInput = 0.0;
        double denominator = 0.0;
        double numerator = 0.0;
        double flux = 0.0;
        double product = 0.0;
        double updatedSource = 0.0;
    };

    static constexpr size_t SourceValidShift = 1;
    static constexpr size_t ResultValidShift = 9;
    static constexpr size_t NextCornerShift = 17;
    static constexpr uint64_t AllocatedMask = uint64_t{1};
    static constexpr uint64_t SourceValidMask = uint64_t{0xff} <<
        SourceValidShift;
    static constexpr uint64_t ResultValidMask = uint64_t{0xff} <<
        ResultValidShift;
    static constexpr uint64_t NextCornerMask = uint64_t{0xf} <<
        NextCornerShift;
    static constexpr uint64_t BusyMask = uint64_t{1} << 21;

    using Row = std::array<uint64_t, SourceResultWords>;

    bool validGroup(size_t group) const
    {
        return group < activeGroups &&
            (metadata(group) & AllocatedMask) != 0;
    }

    uint64_t &word(size_t group, size_t index)
    {
        return words[group % Banks][group / Banks][index];
    }

    const uint64_t &word(size_t group, size_t index) const
    {
        return words[group % Banks][group / Banks][index];
    }

    uint64_t &metadata(size_t group)
    {
        return metadataWords[group % Banks][group / Banks];
    }

    const uint64_t &metadata(size_t group) const
    {
        return metadataWords[group % Banks][group / Banks];
    }

    static size_t nextCorner(uint64_t state)
    {
        return (state & NextCornerMask) >> NextCornerShift;
    }

    static void setNextCorner(uint64_t &state, size_t corner)
    {
        state = (state & ~NextCornerMask) |
            (static_cast<uint64_t>(corner) << NextCornerShift);
    }

    Reservation reject(DescriptorError error)
    {
        ++capacityErrors;
        if (error != DescriptorError::None &&
            latchedError == DescriptorError::None)
            latchedError = error;
        return {false, error, 0, 0, 0, 0};
    }

    void poison(DescriptorError error)
    {
        if (latchedError == DescriptorError::None)
            latchedError = error;
        ++capacityErrors;
    }

    void skipInactiveEdges(Token &token) const
    {
        while (token.destination < SourceResultWords &&
               boundDescriptor->coefficients[
                   umtOrderedWaveCoefficientIndex(
                       token.corner, token.destination)] == 0.0) {
            ++token.destination;
        }
    }

    bool reserveNow(size_t group, uint64_t cycle, bool read, bool write)
    {
        const size_t bank = group % Banks;
        if (nextBankCycle[bank] > cycle)
            return false;
        nextBankCycle[bank] = cycle + 1;
        ++bankAccesses;
        bankReadAccesses += read;
        bankWriteAccesses += write;
        return true;
    }

    Reservation reserve(
        size_t group, uint64_t cycle, bool read, bool write)
    {
        const size_t bank = group % Banks;
        const uint64_t ready = std::max(nextBankCycle[bank], cycle);
        Reservation reservation{
            true, DescriptorError::None, ready + 1, ready - cycle,
            read ? 1U : 0U, write ? 1U : 0U};
        nextBankCycle[bank] = ready + 1;
        ++bankAccesses;
        bankReadAccesses += read;
        bankWriteAccesses += write;
        bankStallCycles += reservation.stallCycles;
        return reservation;
    }

    static void merge(Reservation &total, const Reservation &access)
    {
        total.readyCycle = std::max(total.readyCycle, access.readyCycle);
        total.stallCycles += access.stallCycles;
        total.bankReads += access.bankReads;
        total.bankWrites += access.bankWrites;
    }

    size_t activeGroups = 0;
    size_t occupancyHighWater = 0;
    size_t bankOccupancyHighWater = 0;
    uint64_t capacityErrors = 0;
    uint64_t bankAccesses = 0;
    uint64_t bankReadAccesses = 0;
    uint64_t bankWriteAccesses = 0;
    uint64_t bankStallCycles = 0;
    uint64_t sourceWrites = 0;
    uint64_t denominatorWords = 0;
    uint64_t resultWordsProduced = 0;
    uint64_t resultReads = 0;
    std::array<size_t, Banks> bankOccupancy{};
    std::array<uint64_t, Banks> nextBankCycle{};
    std::array<std::array<Row, RowsPerBank>, Banks> words{};
    std::array<std::array<uint64_t, RowsPerBank>, Banks> metadataWords{};
    // Host pointer to the engine's existing decoded descriptor register; it
    // is not an additional logical hardware pointer or descriptor copy.
    const UmtOrderedWaveDescriptor *boundDescriptor = nullptr;
    std::array<Token, ComputeTokens> tokens{};
    size_t activeTokens = 0;
    size_t tokenHighWater = 0;
    size_t issueCursor = 0;
    uint64_t addNextIssue = 0;
    uint64_t multiplyNextIssue = 0;
    std::array<uint64_t, DividerLanes> dividerNextIssue{};
    uint64_t tokenBackpressureEvents = 0;
    uint64_t pipelineActiveCycleCount = 0;
    uint64_t fpIssueStallCycles = 0;
    uint64_t fpOperationIssueCount = 0;
    uint64_t dualIssueCycleCount = 0;
    uint64_t bankConflictCycles = 0;
    uint64_t writebackStallCycles = 0;
    uint64_t resultBankStallCycleCount = 0;
    DescriptorError latchedError = DescriptorError::None;
};

// The issue-two treatment retains T32, eight divider lanes, II=32, and four
// single-ported banks. It may issue two operations only when per-unit and bank
// constraints independently permit both.
using UmtOrderedWaveStreamState =
    UmtOrderedWaveStreamStateModel<32, 8, 32, 2>;

static_assert(UmtOrderedWaveStreamState::Banks == 4);
static_assert(UmtOrderedWaveStreamState::RowsPerBank == 16);
static_assert(UmtOrderedWaveStreamState::FpIssueWidth == 2);
static_assert(
    UmtOrderedWaveStreamState::FpIssueSelectionCandidateInputs == 64);
static_assert(UmtOrderedWaveStreamState::FpIssueOperandRouteBits == 128);
static_assert(
    UmtOrderedWaveStreamState::
        IncrementalFpIssueSelectionCandidateInputs == 32);
static_assert(
    UmtOrderedWaveStreamState::IncrementalFpIssueOperandRouteBits == 64);
static_assert(UmtOrderedWaveStreamState::AllocatedBytes == 4608);
static_assert(UmtOrderedWaveStreamState::PhysicalBytes == 5120);
static_assert(UmtOrderedWaveStreamState::ResidualBytes == 512);
static_assert(
    UmtOrderedWaveStreamState::RepresentedTokenLogicalBitsFloor == 471);
static_assert(
    UmtOrderedWaveStreamState::FunctionalControlLogicalBitsFloor == 657);
static_assert(
    UmtOrderedWaveStreamState::BankSchedulerLogicalBitsFloor == 283);
static_assert(
    UmtOrderedWaveStreamState::InstrumentationLogicalBitsFloor == 1106);
static_assert(
    UmtOrderedWaveStreamState::AuxiliaryLogicalBitsFloor == 17118);
static_assert(
    UmtOrderedWaveStreamState::PhysicalStorePlusLogicalAuxiliaryBitsFloor ==
        58078);

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_UMT_ORDERED_WAVE_STREAM_STATE_HH__
