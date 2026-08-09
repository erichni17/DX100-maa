#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include "mem/LANLMAA/UmtOrderedWaveStreamState.hh"

using namespace gem5::lanlmaa;

namespace
{

constexpr size_t DenseEdges = 12;
constexpr size_t ReturnedLineWords = 8;

enum class AdmissionPolicy
{
    GlobalCornerBarrier,
    OverlappedGroupBlocks
};

const char *
policyName(AdmissionPolicy policy)
{
    return policy == AdmissionPolicy::GlobalCornerBarrier ?
        "global_corner_barrier" : "overlapped_group_blocks";
}

struct Measurement
{
    std::string configuration;
    std::string admissionPolicy;
    size_t tokens = 0;
    size_t dividerLanes = 0;
    uint64_t dividerLatency = 0;
    uint64_t dividerInitiationInterval = 0;
    size_t groups = 0;
    uint64_t activeCycles = 0;
    size_t tokenHighWater = 0;
    uint64_t tokenPressureCycles = 0;
    uint64_t fpIssueStallCycles = 0;
    uint64_t bankReadConflictCycles = 0;
    uint64_t bankWritebackStallCycles = 0;
    uint64_t resultBankStallCycles = 0;
    uint64_t completions = 0;
    uint64_t denominators = 0;
    uint64_t results = 0;
    size_t representedTokenBitsFloor = 0;
    size_t incrementalTokenBitsFloor = 0;
};

UmtOrderedWaveDescriptor
denseDescriptor(size_t groups)
{
    UmtOrderedWaveDescriptor descriptor;
    descriptor.abiVersion = UmtOrderedWaveD64DescriptorVersion;
    descriptor.groupCount = groups;
    descriptor.sumArea.fill(2.0);
    descriptor.coefficients.fill(0.0);
    // The twelve forward edges of an eight-corner hexahedron.  This is the
    // dense UMT topology; selecting coefficient slots 0..11 instead would
    // front-load seven edges on corner zero and is not the SPP2 wave shape.
    constexpr std::array<std::array<size_t, 2>, DenseEdges> edges{{
        {{0, 1}}, {{0, 2}}, {{0, 4}}, {{1, 3}},
        {{1, 5}}, {{2, 3}}, {{2, 6}}, {{3, 7}},
        {{4, 5}}, {{4, 6}}, {{5, 7}}, {{6, 7}},
    }};
    for (size_t edge = 0; edge < edges.size(); ++edge) {
        descriptor.coefficients[umtOrderedWaveCoefficientIndex(
            edges[edge][0], edges[edge][1])] = 0.01 * (edge + 1);
    }
    const size_t nonzero = std::count_if(
        descriptor.coefficients.begin(), descriptor.coefficients.end(),
        [](double coefficient) { return coefficient != 0.0; });
    if (nonzero != DenseEdges) {
        throw std::runtime_error("probe did not construct 12 dense edges");
    }
    return descriptor;
}

template <class State>
Measurement
measure(
    const std::string &configuration, size_t groups,
    AdmissionPolicy admissionPolicy)
{
    static_assert(State::ComputeTokens >= ReturnedLineWords);

    State state;
    if (!state.configure(groups)) {
        throw std::runtime_error("state rejected a legal group count");
    }
    const auto descriptor = denseDescriptor(groups);
    std::array<UmtOrderedWaveResult, UmtOrderedWaveMaximumGroups> expected{};
    for (size_t group = 0; group < groups; ++group) {
        UmtOrderedWaveRecord record;
        for (size_t corner = 0; corner < UmtOrderedWaveCorners; ++corner) {
            record.source[corner] = 4.0 + corner + group;
            record.sigtVolume[corner] = 1.0;
        }
        expected[group] = executeUmtOrderedWave(descriptor, record);
        if (!expected[group]) {
            throw std::runtime_error("scalar oracle rejected probe input");
        }
    }

    uint64_t setupCycle = 0;
    for (size_t source = 0; source < UmtOrderedWaveCorners; ++source) {
        for (size_t group = 0; group < groups; ++group) {
            const auto reservation = state.writeSource(
                group, source,
                umtOrderedWaveStreamEncodeFp64(4.0 + source + group),
                setupCycle);
            if (!reservation.accepted) {
                throw std::runtime_error("source setup was rejected");
            }
        }
        ++setupCycle;
    }
    if (!state.bindDescriptor(descriptor)) {
        throw std::runtime_error("descriptor bind was rejected");
    }

    uint64_t cycle = state.readyCycle();
    uint64_t completions = 0;
    std::vector<size_t> completionCounts(
        groups * UmtOrderedWaveCorners, 0);
    if (admissionPolicy == AdmissionPolicy::GlobalCornerBarrier) {
        for (size_t corner = 0; corner < UmtOrderedWaveCorners; ++corner) {
            size_t nextGroup = 0;
            size_t cornerCompletions = 0;
            while (cornerCompletions != groups) {
                if (nextGroup != groups) {
                    const size_t words = std::min(
                        ReturnedLineWords, groups - nextGroup);
                    if (state.availableTokens() >= words) {
                        for (size_t word = 0; word < words; ++word) {
                            const size_t group = nextGroup + word;
                            const auto admission = state.enqueueDenominator(
                                group, group, corner,
                                umtOrderedWaveStreamEncodeFp64(1.0));
                            if (!admission.accepted) {
                                throw std::runtime_error(
                                    "capacity-gated denominator was "
                                    "rejected");
                            }
                        }
                        nextGroup += words;
                    } else {
                        // This is the state model's production counter hook.
                        // The next complete returned line is assumed ready.
                        state.recordTokenCapacityBackpressure();
                    }
                }
                if (state.tokensInUse() == 0) {
                    throw std::runtime_error(
                        "probe scheduler made no progress");
                }
                const auto progress = state.cycle(cycle++);
                if (progress.error != DescriptorError::None) {
                    throw std::runtime_error("stream state latched an error");
                }
                for (size_t index = 0;
                     index < progress.completions; ++index) {
                    const size_t group =
                        progress.completedOperations[index];
                    if (group >= groups) {
                        throw std::runtime_error(
                            "completion group was invalid");
                    }
                    const size_t slot = group * UmtOrderedWaveCorners + corner;
                    if (++completionCounts[slot] != 1) {
                        throw std::runtime_error(
                            "completion was invalid or duplicated");
                    }
                }
                cornerCompletions += progress.completions;
                completions += progress.completions;
            }
        }
    } else {
        struct BlockState
        {
            size_t corner = 0;
            size_t completions = 0;
            bool inFlight = false;
        };
        const size_t blockCount =
            (groups + ReturnedLineWords - 1) / ReturnedLineWords;
        std::vector<BlockState> blocks(blockCount);
        std::deque<size_t> readyBlocks;
        for (size_t block = 0; block < blockCount; ++block) {
            readyBlocks.push_back(block);
        }
        while (completions != groups * UmtOrderedWaveCorners) {
            if (!readyBlocks.empty()) {
                const size_t block = readyBlocks.front();
                auto &blockState = blocks[block];
                const size_t firstGroup = block * ReturnedLineWords;
                const size_t words = std::min(
                    ReturnedLineWords, groups - firstGroup);
                if (state.availableTokens() >= words) {
                    for (size_t word = 0; word < words; ++word) {
                        const size_t group = firstGroup + word;
                        const auto admission = state.enqueueDenominator(
                            group, group, blockState.corner,
                            umtOrderedWaveStreamEncodeFp64(1.0));
                        if (!admission.accepted) {
                            throw std::runtime_error(
                                "overlapped denominator was rejected");
                        }
                    }
                    blockState.inFlight = true;
                    readyBlocks.pop_front();
                } else {
                    state.recordTokenCapacityBackpressure();
                }
            }
            if (state.tokensInUse() == 0) {
                throw std::runtime_error(
                    "overlapped probe scheduler made no progress");
            }
            const auto progress = state.cycle(cycle++);
            if (progress.error != DescriptorError::None) {
                throw std::runtime_error("stream state latched an error");
            }
            for (size_t index = 0; index < progress.completions; ++index) {
                const size_t group = progress.completedOperations[index];
                if (group >= groups) {
                    throw std::runtime_error("completion group was invalid");
                }
                const size_t block = group / ReturnedLineWords;
                auto &blockState = blocks[block];
                if (!blockState.inFlight ||
                    blockState.corner >= UmtOrderedWaveCorners) {
                    throw std::runtime_error(
                        "completion block was not in flight");
                }
                const size_t slot =
                    group * UmtOrderedWaveCorners + blockState.corner;
                if (++completionCounts[slot] != 1) {
                    throw std::runtime_error("completion was duplicated");
                }
                ++blockState.completions;
                ++completions;
                const size_t firstGroup = block * ReturnedLineWords;
                const size_t words = std::min(
                    ReturnedLineWords, groups - firstGroup);
                if (blockState.completions == words) {
                    blockState.completions = 0;
                    blockState.inFlight = false;
                    ++blockState.corner;
                    if (blockState.corner != UmtOrderedWaveCorners) {
                        readyBlocks.push_back(block);
                    }
                }
            }
        }
    }

    if (std::any_of(
            completionCounts.begin(), completionCounts.end(),
            [](size_t count) { return count != 1; })) {
        throw std::runtime_error("a group/corner completion was lost");
    }

    if (!state.complete() || state.tokensInUse() != 0 ||
        state.consumedDenominators() != groups * UmtOrderedWaveCorners ||
        state.producedResults() != groups * UmtOrderedWaveCorners ||
        completions != groups * UmtOrderedWaveCorners) {
        throw std::runtime_error("stream work-conservation check failed");
    }
    for (size_t corner = 0; corner < UmtOrderedWaveCorners; ++corner) {
        for (size_t group = 0; group < groups; ++group) {
            uint64_t bits = 0;
            if (!state.readResult(group, corner, cycle, bits).accepted ||
                bits != umtOrderedWaveStreamEncodeFp64(
                    expected[group].flux[corner])) {
                throw std::runtime_error("bit-exact result check failed");
            }
        }
        ++cycle;
    }

    const size_t tokenBits = State::RepresentedTokenLogicalBitsFloor;
    return {
        configuration,
        policyName(admissionPolicy),
        State::ComputeTokens,
        State::DividerLanes,
        State::DivideLatency,
        State::DividerInitiationInterval,
        groups,
        state.pipelineActiveCycles(),
        state.tokenHighWaterMark(),
        state.tokenBackpressure(),
        state.fpIssueStalls(),
        state.bankConflicts(),
        state.writebackStalls(),
        state.resultBankStalls(),
        completions,
        state.consumedDenominators(),
        state.producedResults(),
        tokenBits,
        (State::ComputeTokens - 8) * tokenBits,
    };
}

template <class State>
void
appendConfiguration(
    std::vector<Measurement> &measurements,
    const std::string &configuration)
{
    for (const size_t groups : {16U, 32U, 64U}) {
        measurements.push_back(measure<State>(
            configuration, groups, AdmissionPolicy::GlobalCornerBarrier));
    }
}

template <class State>
void
appendG16(
    std::vector<Measurement> &measurements,
    const std::string &configuration, AdmissionPolicy admissionPolicy)
{
    measurements.push_back(measure<State>(
        configuration, 16, admissionPolicy));
}

template <class State>
void
appendDirectG16Variants(
    std::vector<Measurement> &measurements,
    const std::string &configuration, AdmissionPolicy admissionPolicy)
{
    appendG16<State>(measurements, configuration, admissionPolicy);
}

std::vector<Measurement>
runSweep()
{
    std::vector<Measurement> measurements;
    measurements.reserve(46);
    appendConfiguration<UmtOrderedWaveStreamStateModel<8, 8, 64>>(
        measurements, "current_t8_l8_ii64");
    appendConfiguration<UmtOrderedWaveStreamStateModel<16, 8, 64>>(
        measurements, "tokens16_l8_ii64");
    appendConfiguration<UmtOrderedWaveStreamStateModel<24, 8, 64>>(
        measurements, "tokens24_l8_ii64");
    appendConfiguration<UmtOrderedWaveStreamStateModel<32, 8, 64>>(
        measurements, "tokens32_l8_ii64");
    appendConfiguration<UmtOrderedWaveStreamStateModel<32, 12, 64>>(
        measurements, "tokens32_lanes12_ii64");
    appendConfiguration<UmtOrderedWaveStreamStateModel<32, 16, 64>>(
        measurements, "tokens32_lanes16_ii64");
    appendConfiguration<UmtOrderedWaveStreamStateModel<32, 8, 32>>(
        measurements, "tokens32_lanes8_ii32");
    appendConfiguration<UmtOrderedWaveStreamStateModel<32, 8, 16>>(
        measurements, "tokens32_lanes8_ii16");
    appendConfiguration<UmtOrderedWaveStreamStateModel<32, 8, 8>>(
        measurements, "tokens32_lanes8_ii8");

    appendDirectG16Variants<UmtOrderedWaveStreamStateModel<16, 12, 64>>(
        measurements, "tokens16_lanes12_ii64",
        AdmissionPolicy::GlobalCornerBarrier);
    appendDirectG16Variants<UmtOrderedWaveStreamStateModel<16, 16, 64>>(
        measurements, "tokens16_lanes16_ii64",
        AdmissionPolicy::GlobalCornerBarrier);
    appendDirectG16Variants<UmtOrderedWaveStreamStateModel<16, 8, 32>>(
        measurements, "tokens16_lanes8_ii32",
        AdmissionPolicy::GlobalCornerBarrier);
    appendDirectG16Variants<UmtOrderedWaveStreamStateModel<16, 8, 16>>(
        measurements, "tokens16_lanes8_ii16",
        AdmissionPolicy::GlobalCornerBarrier);
    appendDirectG16Variants<UmtOrderedWaveStreamStateModel<16, 8, 8>>(
        measurements, "tokens16_lanes8_ii8",
        AdmissionPolicy::GlobalCornerBarrier);

#define APPEND_OVERLAPPED_G16(tokens, lanes, ii, label)                       \
    appendG16<UmtOrderedWaveStreamStateModel<tokens, lanes, ii>>(             \
        measurements, label, AdmissionPolicy::OverlappedGroupBlocks)
    APPEND_OVERLAPPED_G16(8, 8, 64, "current_t8_l8_ii64");
    APPEND_OVERLAPPED_G16(16, 8, 64, "tokens16_l8_ii64");
    APPEND_OVERLAPPED_G16(24, 8, 64, "tokens24_l8_ii64");
    APPEND_OVERLAPPED_G16(32, 8, 64, "tokens32_l8_ii64");
    APPEND_OVERLAPPED_G16(32, 12, 64, "tokens32_lanes12_ii64");
    APPEND_OVERLAPPED_G16(32, 16, 64, "tokens32_lanes16_ii64");
    APPEND_OVERLAPPED_G16(32, 8, 32, "tokens32_lanes8_ii32");
    APPEND_OVERLAPPED_G16(32, 8, 16, "tokens32_lanes8_ii16");
    APPEND_OVERLAPPED_G16(32, 8, 8, "tokens32_lanes8_ii8");
    APPEND_OVERLAPPED_G16(16, 12, 64, "tokens16_lanes12_ii64");
    APPEND_OVERLAPPED_G16(16, 16, 64, "tokens16_lanes16_ii64");
    APPEND_OVERLAPPED_G16(16, 8, 32, "tokens16_lanes8_ii32");
    APPEND_OVERLAPPED_G16(16, 8, 16, "tokens16_lanes8_ii16");
    APPEND_OVERLAPPED_G16(16, 8, 8, "tokens16_lanes8_ii8");
#undef APPEND_OVERLAPPED_G16
    return measurements;
}

void
printCsv(const std::vector<Measurement> &measurements)
{
    std::cout
        << "configuration,admission_policy,tokens,divider_lanes,"
        << "divider_latency,divider_ii,"
        << "groups,dense_edges,active_cycles,token_high_water,"
        << "token_pressure_cycles,fp_issue_stall_cycles,"
        << "bank_read_conflict_cycles,bank_writeback_stall_cycles,"
        << "result_bank_stall_cycles,completions,denominators,results,"
        << "represented_token_bits_floor,incremental_token_bits_floor\n";
    for (const auto &measurement : measurements) {
        std::cout << measurement.configuration << ','
                  << measurement.admissionPolicy << ',' << measurement.tokens
                  << ',' << measurement.dividerLanes << ','
                  << measurement.dividerLatency << ','
                  << measurement.dividerInitiationInterval << ','
                  << measurement.groups << ',' << DenseEdges << ','
                  << measurement.activeCycles << ','
                  << measurement.tokenHighWater << ','
                  << measurement.tokenPressureCycles << ','
                  << measurement.fpIssueStallCycles << ','
                  << measurement.bankReadConflictCycles << ','
                  << measurement.bankWritebackStallCycles << ','
                  << measurement.resultBankStallCycles << ','
                  << measurement.completions << ','
                  << measurement.denominators << ',' << measurement.results
                  << ',' << measurement.representedTokenBitsFloor << ','
                  << measurement.incrementalTokenBitsFloor << '\n';
    }
}

void
printJson(const std::vector<Measurement> &measurements)
{
    std::cout
        << "{\n"
        << "  \"probe\": \"UmtOrderedWaveStreamStateModel\",\n"
        << "  \"dense_edges\": " << DenseEdges << ",\n"
        << "  \"returned_line_words\": " << ReturnedLineWords << ",\n"
        << "  \"rows\": [\n";
    for (size_t index = 0; index < measurements.size(); ++index) {
        const auto &measurement = measurements[index];
        std::cout
            << "    {\"configuration\": \"" << measurement.configuration
            << "\", \"admission_policy\": \""
            << measurement.admissionPolicy
            << "\", \"tokens\": " << measurement.tokens
            << ", \"divider_lanes\": " << measurement.dividerLanes
            << ", \"divider_latency\": " << measurement.dividerLatency
            << ", \"divider_ii\": "
            << measurement.dividerInitiationInterval
            << ", \"groups\": " << measurement.groups
            << ", \"active_cycles\": " << measurement.activeCycles
            << ", \"token_high_water\": "
            << measurement.tokenHighWater
            << ", \"token_pressure_cycles\": "
            << measurement.tokenPressureCycles
            << ", \"fp_issue_stall_cycles\": "
            << measurement.fpIssueStallCycles
            << ", \"bank_read_conflict_cycles\": "
            << measurement.bankReadConflictCycles
            << ", \"bank_writeback_stall_cycles\": "
            << measurement.bankWritebackStallCycles
            << ", \"result_bank_stall_cycles\": "
            << measurement.resultBankStallCycles
            << ", \"completions\": " << measurement.completions
            << ", \"denominators\": " << measurement.denominators
            << ", \"results\": " << measurement.results
            << ", \"represented_token_bits_floor\": "
            << measurement.representedTokenBitsFloor
            << ", \"incremental_token_bits_floor\": "
            << measurement.incrementalTokenBitsFloor << '}';
        std::cout << (index + 1 == measurements.size() ? "\n" : ",\n");
    }
    std::cout << "  ]\n}\n";
}

} // anonymous namespace

int
main(int argc, char **argv)
{
    try {
        const bool json = argc == 2 && std::string(argv[1]) == "--json";
        if (argc > 2 || (argc == 2 && !json)) {
            std::cerr << "usage: " << argv[0] << " [--json]\n";
            return 2;
        }
        const auto measurements = runSweep();
        if (json) {
            printJson(measurements);
        } else {
            printCsv(measurements);
        }
    } catch (const std::exception &error) {
        std::cerr << "probe failed: " << error.what() << '\n';
        return 1;
    }
    return 0;
}
