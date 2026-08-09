#include <array>
#include <cassert>
#include <cmath>
#include <cstdint>

#include "mem/LANLMAA/UmtOrderedWaveStreamState.hh"

using namespace gem5::lanlmaa;

namespace
{

UmtOrderedWaveDescriptor
descriptor(size_t groups, bool dense = false)
{
    UmtOrderedWaveDescriptor value;
    value.groupCount = groups;
    value.sumArea.fill(2.0);
    value.coefficients.fill(0.0);
    value.coefficients[umtOrderedWaveCoefficientIndex(0, 1)] = 0.5;
    value.coefficients[umtOrderedWaveCoefficientIndex(1, 7)] = -0.25;
    if (dense) {
        for (size_t edge = 0; edge < 12; ++edge)
            value.coefficients[edge] = 0.01 * (edge + 1);
    }
    return value;
}

void
closedCase(size_t groups)
{
    UmtOrderedWaveStreamState state;
    assert(state.configure(groups));
    const auto wave = descriptor(groups);
    std::array<UmtOrderedWaveResult, UmtOrderedWaveMaximumGroups> expected{};
    for (size_t group = 0; group < groups; ++group) {
        UmtOrderedWaveRecord record;
        for (size_t corner = 0; corner < UmtOrderedWaveCorners; ++corner) {
            record.source[corner] = 4.0 + corner + group;
            record.sigtVolume[corner] = 1.0;
        }
        expected[group] = executeUmtOrderedWave(wave, record);
        assert(expected[group]);
    }
    uint64_t cycle = 10;
    for (size_t source = 0; source < UmtOrderedWaveCorners; ++source) {
        for (size_t group = 0; group < groups; ++group) {
            const auto reservation = state.writeSource(
                group, source,
                umtOrderedWaveStreamEncodeFp64(4.0 + source + group),
                cycle);
            assert(reservation.accepted);
        }
        ++cycle;
    }
    for (size_t corner = 0; corner < UmtOrderedWaveCorners; ++corner) {
        for (size_t group = 0; group < groups; ++group) {
            const auto reservation = state.consumeDenominator(
                group, corner, umtOrderedWaveStreamEncodeFp64(1.0),
                wave, cycle);
            assert(reservation.accepted);
        }
        ++cycle;
    }
    assert(state.complete());
    for (size_t corner = 0; corner < UmtOrderedWaveCorners; ++corner) {
        for (size_t group = 0; group < groups; ++group) {
            uint64_t bits = 0;
            const auto reservation = state.readResult(
                group, corner, cycle, bits);
            assert(reservation.accepted);
            assert(std::isfinite(umtOrderedWaveStreamDecodeFp64(bits)));
            assert(bits == umtOrderedWaveStreamEncodeFp64(
                expected[group].flux[corner]));
        }
        ++cycle;
    }
    const uint64_t words = groups * UmtOrderedWaveCorners;
    const uint64_t edges = groups * 2;
    assert(state.acceptedSourceWrites() == words);
    assert(state.consumedDenominators() == words);
    assert(state.producedResults() == words);
    assert(state.acceptedResultReads() == words);
    assert(state.reads() == 2 * words + edges);
    assert(state.writes() == 2 * words + edges);
    assert(state.highWater() == groups);
    assert(state.bankHighWater() == (groups + 3) / 4);
    assert(state.errors() == 0);
}

void
tokenizedCase(size_t groups, bool dense = false)
{
    UmtOrderedWaveStreamState state;
    assert(state.configure(groups));
    const auto wave = descriptor(groups, dense);
    std::array<UmtOrderedWaveResult, UmtOrderedWaveMaximumGroups> expected{};
    uint64_t cycle = 0;
    for (size_t group = 0; group < groups; ++group) {
        UmtOrderedWaveRecord record;
        for (size_t corner = 0; corner < UmtOrderedWaveCorners; ++corner) {
            record.source[corner] = 4.0 + corner + group;
            record.sigtVolume[corner] = 1.0;
        }
        expected[group] = executeUmtOrderedWave(wave, record);
        assert(expected[group]);
    }
    for (size_t source = 0; source < UmtOrderedWaveCorners; ++source) {
        for (size_t group = 0; group < groups; ++group) {
            assert(state.writeSource(
                group, source,
                umtOrderedWaveStreamEncodeFp64(4.0 + source + group),
                cycle).accepted);
        }
        ++cycle;
    }
    assert(state.bindDescriptor(wave));
    cycle = state.readyCycle();
    uint64_t activeCycleCalls = 0;
    for (size_t corner = 0; corner < UmtOrderedWaveCorners; ++corner) {
        size_t next = 0;
        size_t completed = 0;
        while (completed != groups) {
            while (next < groups && state.availableTokens() != 0) {
                assert(state.enqueueDenominator(
                    next, next, corner,
                    umtOrderedWaveStreamEncodeFp64(1.0)).accepted);
                ++next;
            }
            assert(state.tokensInUse() != 0);
            const auto progress = state.cycle(cycle++);
            ++activeCycleCalls;
            assert(progress.error == DescriptorError::None);
            completed += progress.completions;
        }
    }
    assert(state.complete());
    for (size_t corner = 0; corner < UmtOrderedWaveCorners; ++corner) {
        for (size_t group = 0; group < groups; ++group) {
            uint64_t bits = 0;
            assert(state.readResult(group, corner, cycle, bits).accepted);
            assert(bits == umtOrderedWaveStreamEncodeFp64(
                expected[group].flux[corner]));
        }
        ++cycle;
    }
    assert(state.tokenHighWaterMark() ==
           std::min(groups, UmtOrderedWaveStreamState::ComputeTokens));
    assert(state.consumedDenominators() ==
           groups * UmtOrderedWaveCorners);
    assert(state.pipelineActiveCycles() == activeCycleCalls);
    const uint64_t measuredActiveCycles = state.pipelineActiveCycles();
    assert(state.cycle(cycle++).error == DescriptorError::None);
    assert(state.pipelineActiveCycles() == measuredActiveCycles);
    assert(state.bankConflicts() <= state.pipelineActiveCycles());
    assert(state.writebackStalls() <= state.pipelineActiveCycles());
    assert(state.resultBankStalls() <= state.pipelineActiveCycles());
    assert(state.resultBankStalls() >= state.bankConflicts());
    assert(state.resultBankStalls() >= state.writebackStalls());
    assert(state.resultBankStalls() <=
           state.bankConflicts() + state.writebackStalls());
    assert(state.tokenBackpressure() == 0);
    assert(state.error() == DescriptorError::None);
}

void
capacityBackpressureCase()
{
    constexpr size_t groups = UmtOrderedWaveStreamState::ComputeTokens + 1;
    UmtOrderedWaveStreamState state;
    assert(state.consumedDenominators() == 0);
    assert(state.pipelineActiveCycles() == 0);
    assert(state.tokenBackpressure() == 0);
    assert(state.resultBankStalls() == 0);
    assert(state.configure(groups));
    const auto wave = descriptor(groups);
    uint64_t cycle = 0;
    for (size_t source = 0; source < UmtOrderedWaveCorners; ++source) {
        for (size_t group = 0; group < groups; ++group) {
            assert(state.writeSource(
                group, source,
                umtOrderedWaveStreamEncodeFp64(4.0 + source + group),
                cycle).accepted);
        }
        ++cycle;
    }
    assert(state.bindDescriptor(wave));
    cycle = state.readyCycle();

    for (size_t group = 0;
         group < UmtOrderedWaveStreamState::ComputeTokens; ++group) {
        assert(state.enqueueDenominator(
            group, group, 0,
            umtOrderedWaveStreamEncodeFp64(1.0)).accepted);
    }
    assert(state.availableTokens() == 0);
    assert(state.consumedDenominators() ==
           UmtOrderedWaveStreamState::ComputeTokens);
    assert(state.tokenBackpressure() == 0);

    const auto blocked = state.enqueueDenominator(
        UmtOrderedWaveStreamState::ComputeTokens,
        UmtOrderedWaveStreamState::ComputeTokens, 0,
        umtOrderedWaveStreamEncodeFp64(1.0));
    assert(!blocked.accepted);
    assert(blocked.error == DescriptorError::None);
    assert(state.consumedDenominators() ==
           UmtOrderedWaveStreamState::ComputeTokens);
    assert(state.tokenBackpressure() == 0);
    state.recordTokenCapacityBackpressure();
    assert(state.tokenBackpressure() == 1);

    uint64_t activeCycleCalls = 0;
    size_t completions = 0;
    while (completions == 0) {
        assert(state.tokensInUse() != 0);
        const auto progress = state.cycle(cycle++);
        ++activeCycleCalls;
        assert(progress.error == DescriptorError::None);
        completions += progress.completions;
    }
    assert(state.pipelineActiveCycles() == activeCycleCalls);
    assert(state.availableTokens() != 0);
    assert(state.enqueueDenominator(
        UmtOrderedWaveStreamState::ComputeTokens,
        UmtOrderedWaveStreamState::ComputeTokens, 0,
        umtOrderedWaveStreamEncodeFp64(1.0)).accepted);
    assert(state.consumedDenominators() == groups);

    assert(state.configure(1));
    assert(state.consumedDenominators() == 0);
    assert(state.pipelineActiveCycles() == 0);
    assert(state.tokenBackpressure() == 0);
    assert(state.resultBankStalls() == 0);
}

} // anonymous namespace

int
main()
{
    assert(UmtOrderedWaveStreamState::ComputeTokens == 32);
    assert(UmtOrderedWaveStreamState::DividerLanes == 8);
    assert(UmtOrderedWaveStreamState::DivideLatency == 64);
    assert(UmtOrderedWaveStreamState::DividerInitiationInterval == 32);
    assert(UmtOrderedWaveStreamState::RepresentedTokenLogicalBitsFloor == 471);
    assert(
        UmtOrderedWaveStreamState::FunctionalControlLogicalBitsFloor == 657);
    assert(UmtOrderedWaveStreamState::BankSchedulerLogicalBitsFloor == 283);
    assert(UmtOrderedWaveStreamState::InstrumentationLogicalBitsFloor == 978);
    assert(UmtOrderedWaveStreamState::AuxiliaryLogicalBitsFloor == 16990);
    assert(
        UmtOrderedWaveStreamState::
            PhysicalStorePlusLogicalAuxiliaryBitsFloor == 57950);
    closedCase(1);
    closedCase(16);
    closedCase(32);
    closedCase(33);
    closedCase(64);
    tokenizedCase(1);
    tokenizedCase(8);
    tokenizedCase(16);
    tokenizedCase(64, true);
    capacityBackpressureCase();

    UmtOrderedWaveStreamState invalid;
    assert(!invalid.configure(65));
    assert(invalid.errors() == 1);

    UmtOrderedWaveStreamState order;
    assert(order.configure(1));
    const auto wave = descriptor(1);
    auto premature = order.consumeDenominator(
        0, 0, umtOrderedWaveStreamEncodeFp64(1.0), wave, 0);
    assert(!premature.accepted);
    assert(premature.error == DescriptorError::BadStartState);
    assert(order.errors() == 1);

    UmtOrderedWaveStreamState duplicate;
    assert(duplicate.configure(1));
    assert(duplicate.writeSource(
        0, 0, umtOrderedWaveStreamEncodeFp64(1.0), 0).accepted);
    auto repeated = duplicate.writeSource(
        0, 0, umtOrderedWaveStreamEncodeFp64(1.0), 0);
    assert(!repeated.accepted);
    assert(repeated.error == DescriptorError::BadStartState);

    return 0;
}
