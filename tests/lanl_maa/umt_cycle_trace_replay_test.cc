#include <array>
#include <cassert>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>

#include "mem/LANLMAA/UmtOrderedWaveStreamState.hh"

using namespace gem5::lanlmaa;

namespace
{

std::string
hex64(uint64_t value)
{
    std::ostringstream output;
    output << std::hex << std::setfill('0') << std::setw(16) << value;
    return output.str();
}

template <size_t Count>
std::string
hexBytes(const std::array<uint8_t, Count> &bytes)
{
    std::ostringstream output;
    output << std::hex << std::setfill('0');
    for (const uint8_t byte : bytes)
        output << std::setw(2) << static_cast<unsigned>(byte);
    return output.str();
}

template <size_t Count>
uint64_t
packedBits(
    const std::array<uint8_t, Count> &packed, size_t offset, size_t bits)
{
    uint64_t value = 0;
    for (size_t bit = 0; bit < bits; ++bit) {
        value |= uint64_t{(packed[(offset + bit) / 8] >>
                           ((offset + bit) % 8)) & 1U} << bit;
    }
    return value;
}

UmtOrderedWaveDescriptor
makeDescriptor(size_t groups, bool dense)
{
    UmtOrderedWaveDescriptor descriptor;
    descriptor.abiVersion = UmtOrderedWaveDescriptorVersion;
    descriptor.groupCount = groups;
    descriptor.sumArea.fill(2.0);
    descriptor.coefficients.fill(0.0);
    descriptor.coefficients[umtOrderedWaveCoefficientIndex(0, 1)] = 0.5;
    descriptor.coefficients[umtOrderedWaveCoefficientIndex(1, 7)] = -0.25;
    if (dense) {
        for (size_t edge = 0; edge < UmtOrderedWaveMaximumEdges; ++edge)
            descriptor.coefficients[edge] = 0.01 * (edge + 1);
    }
    return descriptor;
}

const char *
operationName(UmtOrderedWaveStreamState::TraceOperation operation)
{
    using Operation = UmtOrderedWaveStreamState::TraceOperation;
    switch (operation) {
      case Operation::None: return "none";
      case Operation::DenominatorAdd: return "denominator_add";
      case Operation::Divide: return "divide";
      case Operation::Multiply: return "multiply";
      case Operation::EdgeAdd: return "edge_add";
    }
    assert(false);
    return "none";
}

template <class State>
void
writeState(
    std::ostream &output, const typename State::TraceStateSnapshot &state)
{
    output << "\"state\":{\"digest\":\"" << hex64(state.digest)
           << "\",\"issue_cursor\":" << state.issueCursor
           << ",\"active_tokens\":[";
    bool first = true;
    for (const auto &token : state.tokens) {
        if (!token.active)
            continue;
        if (!first)
            output << ',';
        first = false;
        output << "{\"index\":" << token.tokenIndex << ",\"packed\":\""
               << hexBytes(token.packed) << "\"}";
    }
    output << "],\"next_bank_cycle\":[";
    for (size_t bank = 0; bank < State::Banks; ++bank) {
        if (bank != 0)
            output << ',';
        output << state.nextBankCycle[bank];
    }
    output << ']';
    output << "},\"counters\":{\"fp_operations\":"
           << state.counters.fpOperations
           << ",\"dual_issue\":" << state.counters.dualIssue
           << ",\"fp_issue_stall\":" << state.counters.fpIssueStall
           << ",\"bank_conflict\":" << state.counters.bankConflict
           << ",\"writeback_stall\":" << state.counters.writebackStall
           << ",\"result_bank_stall\":"
           << state.counters.resultBankStall
           << ",\"divider_no_lane\":" << state.counters.dividerNoLane
           << '}';
}

template <class State>
void
writeChanges(
    std::ostream &output, const typename State::TraceStateSnapshot &before,
    const typename State::TraceStateSnapshot &after)
{
    output << "\"bank_word_changes\":[";
    bool first = true;
    for (size_t bank = 0; bank < State::Banks; ++bank) {
        for (size_t row = 0; row < State::RowsPerBank; ++row) {
            for (size_t word = 0; word < State::TraceBankWords; ++word) {
                if (before.bankWords[bank][row][word] ==
                    after.bankWords[bank][row][word]) {
                    continue;
                }
                if (!first)
                    output << ',';
                first = false;
                output << "{\"bank\":" << bank << ",\"row\":" << row
                       << ",\"word\":" << word << ",\"value\":\""
                       << hex64(after.bankWords[bank][row][word]) << "\"}";
            }
        }
    }
    output << ']';
}

template <class State>
void
runScenario(const std::filesystem::path &path, size_t groups, bool dense)
{
    State state;
    assert(state.configure(groups));
    const auto descriptor = makeDescriptor(groups, dense);
    for (size_t source = 0; source < UmtOrderedWaveCorners; ++source) {
        for (size_t group = 0; group < groups; ++group) {
            assert(state.writeSource(
                group, source,
                umtOrderedWaveStreamEncodeFp64(4.0 + group + source), 0).
                accepted);
        }
    }
    assert(state.bindDescriptor(descriptor));

    std::ofstream output(path);
    assert(output);
    const std::string scenario = std::string(dense ? "dense" : "sparse") +
        "-g" + std::to_string(groups);
    // Fixture fingerprints are deterministic, versioned identities of this
    // directed C++ stimulus; production descriptor bytes are not implied.
    const std::string fixture = dense ?
        "99cf1e6998f84f8d0375f771815ae98c6c98f347933897c4f7a1643794f4da78" :
        "434921dfc38b973f4014c9d60d3069bb1fa644f1a10ca2f45b0f70da72928210";
    output << "{\"record_type\":\"header\",\"schema\":\""
           << "lanl-maa-umt-cycle-trace-v1\",\"schema_version\":1"
           << ",\"source_commit\":\"2e2de9e99670d3bb04c6616c0a3a0265dd814a7f\""
           << ",\"rtl_commit\":\"04e7804b248072e300d7935cfe07f635388e8f9b\""
           << ",\"abi_versions\":[4,5],\"compute_tokens\":"
           << State::ComputeTokens << ",\"fp_issue_width\":"
           << State::FpIssueWidth
           << ",\"divider_lanes\":8,\"divide_latency\":64,\"divide_ii\":32"
           << ",\"line_bytes\":64,\"descriptor_hash\":\"" << fixture
           << "\",\"stimulus_hash\":\"" << fixture
           << "\",\"canonicalization_version\":1,\"scenario\":\""
           << scenario << "\"}\n";

    uint64_t cycle = state.readyCycle();
    for (size_t corner = 0; corner < UmtOrderedWaveCorners; ++corner) {
        size_t nextGroup = 0;
        size_t completeCount = 0;
        bool checkedInitialFirstFreeBinding = false;
        while (completeCount != groups) {
            std::vector<size_t> admitted;
            while (nextGroup < groups && state.availableTokens() != 0) {
                const auto reservation = state.enqueueDenominator(
                    nextGroup, nextGroup, corner,
                    umtOrderedWaveStreamEncodeFp64(1.0));
                assert(reservation.accepted);
                admitted.push_back(nextGroup++);
            }
            const auto before = state.traceStateSnapshot();
            if (!checkedInitialFirstFreeBinding) {
                // The first batch starts with every token free: raw packed
                // operation bits must occupy the same first-free tag index.
                for (size_t index = 0; index < admitted.size(); ++index) {
                    assert(before.tokens[index].active);
                    assert(packedBits(before.tokens[index].packed, 4, 6) ==
                           admitted[index]);
                }
                checkedInitialFirstFreeBinding = true;
            }
            const auto result = state.cycle(cycle);
            assert(result.error == DescriptorError::None);
            const auto after = state.traceStateSnapshot();
            output << "{\"record_type\":\"cycle\",\"cycle\":" << cycle
                   << ",\"inputs\":{\"source_ingress\":[]"
                   << ",\"denominator_ingress\":[";
            for (size_t index = 0; index < admitted.size(); ++index) {
                if (index != 0)
                    output << ',';
                output << "{\"operation\":" << admitted[index]
                       << ",\"group\":" << admitted[index]
                       << ",\"corner\":" << corner << "}";
            }
            output << "],\"arithmetic_completions\":[]"
                   << ",\"external_access\":[]"
                   << ",\"line_ledger\":{\"d32\":0,\"d64\":0"
                   << ",\"response\":0,\"release\":0,\"hold\":0}}"
                   << ",\"issues\":[";
            for (size_t slot = 0; slot < State::FpIssueWidth; ++slot) {
                if (slot != 0)
                    output << ',';
                const auto &issue = result.issues[slot];
                output << "{\"valid\":" << (issue.valid ? "true" : "false")
                       << ",\"slot\":" << slot << ",\"token\":"
                       << issue.tokenIndex << ",\"operation\":\""
                       << operationName(issue.operation) << "\",\"lane\":";
                if (issue.dividerLane == std::numeric_limits<size_t>::max())
                    output << "null";
                else
                    output << issue.dividerLane;
                output << '}';
            }
            output << "],\"completion_ready\":[";
            for (size_t index = 0; index < result.completions; ++index) {
                if (index != 0)
                    output << ',';
                output << result.completedOperations[index];
            }
            output << "],";
            writeChanges<State>(output, before, after);
            output << ',';
            writeState<State>(output, after);
            output << "}\n";
            completeCount += result.completions;
            ++cycle;
            assert(cycle < 200000);
        }
    }
    assert(state.complete());
}

} // anonymous namespace

int
main(int argc, char **argv)
{
    assert(argc == 2);
    const std::filesystem::path directory(argv[1]);
    std::filesystem::create_directories(directory);
    for (const size_t groups : {size_t{1}, size_t{8}, size_t{16}, size_t{32},
                                size_t{64}}) {
        runScenario<UmtOrderedWaveStreamState>(
            directory / ("sparse-g" + std::to_string(groups) + ".jsonl"),
            groups, false);
        runScenario<UmtOrderedWaveStreamState>(
            directory / ("dense-g" + std::to_string(groups) + ".jsonl"),
            groups, true);
    }
    return 0;
}
