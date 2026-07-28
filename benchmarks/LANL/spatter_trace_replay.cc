#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

#include "mem/LANLMAA/ReferenceModel.hh"

namespace
{

using gem5::lanlmaa::Admission;
using gem5::lanlmaa::Configuration;
using gem5::lanlmaa::ReadContinuationModel;

struct Options
{
    std::string indices;
    size_t window = 64;
    size_t lineEntries = 0;
    uint8_t elementBytes = 8;
};

Configuration
configurationFor(size_t window, size_t lineEntries)
{
    Configuration configuration;
    configuration.operationEntries = window;
    configuration.lineEntries = lineEntries == 0
        ? std::max<size_t>(4, window / 2) : lineEntries;
    configuration.continuationContexts = std::max<size_t>(1, window / 4);
    configuration.combinerEntries = std::max<size_t>(4, window / 2);
    configuration.acknowledgementCredits = configuration.combinerEntries;
    return configuration;
}

std::vector<uint64_t>
readIndices(const std::string &path)
{
    std::ifstream stream(path, std::ios::binary | std::ios::ate);
    if (!stream) {
        throw std::runtime_error("cannot open index stream " + path);
    }
    const auto end = stream.tellg();
    if (end <= 0 || end % 8 != 0) {
        throw std::runtime_error(
            "index stream must contain nonempty packed uint64 values");
    }
    const size_t bytes = static_cast<size_t>(end);
    stream.seekg(0);
    std::vector<uint8_t> encoded(bytes);
    if (!stream.read(
            reinterpret_cast<char *>(encoded.data()), encoded.size())) {
        throw std::runtime_error("cannot read complete index stream");
    }

    std::vector<uint64_t> indices(bytes / 8);
    for (size_t ordinal = 0; ordinal < indices.size(); ++ordinal) {
        uint64_t index = 0;
        for (size_t byte = 0; byte < 8; ++byte) {
            index |= static_cast<uint64_t>(encoded[ordinal * 8 + byte]) <<
                     (8 * byte);
        }
        indices[ordinal] = index;
    }
    return indices;
}

uint64_t
checkedAddress(uint64_t index, uint8_t elementBytes)
{
    constexpr uint64_t AddressLimit = uint64_t{1} << 48;
    if (index >= AddressLimit / elementBytes) {
        throw std::runtime_error(
            "trace index exceeds the 48-bit address model");
    }
    return index * elementBytes;
}

int
run(const Options &options)
{
    const auto indices = readIndices(options.indices);
    const Configuration configuration = configurationFor(
        options.window, options.lineEntries);
    ReadContinuationModel model(configuration);
    if (!model.valid()) {
        throw std::invalid_argument("invalid replay configuration");
    }

    size_t next = 0;
    size_t completed = 0;
    const std::vector<uint8_t> zeroLine(64, 0);
    while (completed < indices.size()) {
        bool progress = false;
        while (next < indices.size()) {
            const Admission admission = model.admitRead(
                next + 1,
                checkedAddress(indices[next], options.elementBytes),
                options.elementBytes);
            if (admission == Admission::WouldBlock) {
                break;
            }
            if (admission == Admission::Invalid) {
                throw std::runtime_error("valid trace index was rejected");
            }
            ++next;
            progress = true;
        }

        while (auto request = model.nextLineRequest()) {
            if (!model.returnLine(request->lineAddress, zeroLine)) {
                throw std::runtime_error(
                    "synthetic line response was rejected");
            }
            progress = true;
        }

        while (model.popRetired()) {
            ++completed;
            progress = true;
        }
        if (!progress) {
            throw std::runtime_error("trace replay made no forward progress");
        }
    }
    if (model.outstandingOperations() != 0 ||
        model.outstandingContexts() != 0) {
        throw std::runtime_error("trace replay was not quiescent");
    }

    const auto &counters = model.counters();
    const double reduction = 1.0 -
        static_cast<double>(counters.physicalLineReads) /
        counters.logicalMemoryAccesses;
    std::cout << "verification=PASS-accounting\n";
    std::cout << "indices=" << indices.size() << '\n';
    std::cout << "element_bytes="
              << static_cast<unsigned>(options.elementBytes) << '\n';
    std::cout << "window=" << options.window << '\n';
    std::cout << "line_entries=" << configuration.lineEntries << '\n';
    std::cout << "logical_reads="
              << counters.logicalMemoryAccesses << '\n';
    std::cout << "physical_line_reads="
              << counters.physicalLineReads << '\n';
    std::cout << "line_merge_hits=" << counters.lineMergeHits << '\n';
    std::cout << "duplicate_element_hits="
              << counters.duplicateElementHits << '\n';
    std::cout << "line_would_block=" << counters.lineWouldBlock << '\n';
    std::cout << "operation_would_block="
              << counters.operationWouldBlock << '\n';
    std::cout << std::setprecision(17)
              << "request_reduction_fraction=" << reduction << '\n';
    return 0;
}

size_t
parseSize(const std::string &value, const std::string &option)
{
    size_t consumed = 0;
    const auto parsed = std::stoull(value, &consumed, 0);
    if (consumed != value.size() || parsed == 0) {
        throw std::invalid_argument("invalid value for " + option);
    }
    return parsed;
}

Options
parseOptions(int argc, char **argv)
{
    Options options;
    for (int argument = 1; argument < argc; ++argument) {
        const std::string option = argv[argument];
        if (option == "--help") {
            std::cout << "usage: spatter_trace_replay --indices FILE "
                         "[--window N] [--line-entries N] "
                         "[--element-bytes 1|2|4|8]\n";
            std::exit(0);
        }
        if (argument + 1 == argc) {
            throw std::invalid_argument("missing value for " + option);
        }
        const std::string value = argv[++argument];
        if (option == "--indices") {
            options.indices = value;
        } else if (option == "--window") {
            options.window = parseSize(value, option);
        } else if (option == "--line-entries") {
            options.lineEntries = parseSize(value, option);
        } else if (option == "--element-bytes") {
            const size_t bytes = parseSize(value, option);
            if (bytes != 1 && bytes != 2 && bytes != 4 && bytes != 8) {
                throw std::invalid_argument("unsupported element width");
            }
            options.elementBytes = bytes;
        } else {
            throw std::invalid_argument("unknown option " + option);
        }
    }
    if (options.indices.empty()) {
        throw std::invalid_argument("--indices is required");
    }
    return options;
}

} // anonymous namespace

int
main(int argc, char **argv)
{
    try {
        return run(parseOptions(argc, argv));
    } catch (const std::exception &error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }
}
