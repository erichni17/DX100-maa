#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <fstream>
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
using gem5::lanlmaa::DataType;
using gem5::lanlmaa::Ordering;
using gem5::lanlmaa::OverflowPolicy;
using gem5::lanlmaa::UpdateCombinerModel;
using gem5::lanlmaa::UpdateCounters;
using gem5::lanlmaa::UpdateDrain;
using gem5::lanlmaa::UpdateOperation;

constexpr size_t HeaderBytes = 64;
constexpr size_t EventBytes = 32;
constexpr size_t RootBytes = 16;
constexpr uint32_t TerminalEvent = std::numeric_limits<uint32_t>::max();
constexpr uint64_t TallyBase = 0x100000000ULL;

enum class EventKind : uint8_t
{
    Scatter = 0,
    Boundary = 1,
    Reflect = 2,
    Census = 3,
    Exit = 4,
    Killed = 5,
    Pass = 6
};

struct Event
{
    uint32_t sourceCell = 0;
    uint32_t destinationCell = 0;
    uint32_t nextEvent = TerminalEvent;
    EventKind kind = EventKind::Scatter;
    double absorbed = 0.0;
    double track = 0.0;
};

struct Root
{
    uint32_t firstEvent = 0;
    uint32_t eventCount = 0;
    uint32_t finalCell = 0;
    EventKind terminalKind = EventKind::Census;
};

struct Dataset
{
    std::vector<Event> events;
    std::vector<Root> roots;
    std::vector<double> expectedAbsorbed;
    std::vector<double> expectedTrack;
};

struct Options
{
    std::string input =
        "benchmarks/LANL/inputs/branson_native_event_replay_t1_v1.bin";
    size_t roots = 0;
    size_t continuationContexts = 16;
    size_t contextQuantum = 1;
    size_t eventLineEntries = 8;
    size_t residencyEntries = 16;
    size_t residencyBanks = 2;
    size_t combinerEntries = 16;
    size_t combinerBanks = 2;
    bool corruptFirstSource = false;
};

struct Result
{
    std::vector<uint32_t> finalCells;
    std::vector<EventKind> terminalKinds;
    std::vector<double> absorbed;
    std::vector<double> track;
    uint64_t eventsProcessed = 0;
};

struct CacheCounters
{
    uint64_t accesses = 0;
    uint64_t hits = 0;
    uint64_t misses = 0;
    uint64_t replacements = 0;
};

struct ModelResult
{
    Result values;
    CacheCounters eventLines;
    CacheCounters residency;
    UpdateCounters updates;
    uint64_t contextWouldBlock = 0;
    uint64_t activeContextHighWater = 0;
};

template <class T>
T
fromBits(uint64_t bits)
{
    static_assert(sizeof(T) == sizeof(bits));
    T value;
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}

template <class T>
uint64_t
toBits(T value)
{
    static_assert(sizeof(T) == sizeof(uint64_t));
    uint64_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}

uint32_t
readU32(const std::vector<uint8_t> &bytes, size_t offset)
{
    if (offset > bytes.size() || bytes.size() - offset < 4) {
        throw std::runtime_error("truncated replay uint32");
    }
    uint32_t value = 0;
    for (size_t byte = 0; byte < 4; ++byte) {
        value |= static_cast<uint32_t>(bytes[offset + byte]) << (8 * byte);
    }
    return value;
}

uint64_t
readU64(const std::vector<uint8_t> &bytes, size_t offset)
{
    if (offset > bytes.size() || bytes.size() - offset < 8) {
        throw std::runtime_error("truncated replay uint64");
    }
    uint64_t value = 0;
    for (size_t byte = 0; byte < 8; ++byte) {
        value |= static_cast<uint64_t>(bytes[offset + byte]) << (8 * byte);
    }
    return value;
}

size_t
checkedBytes(uint64_t count, uint64_t width, const char *field)
{
    if (count > std::numeric_limits<size_t>::max() / width) {
        throw std::runtime_error(std::string("overflowing replay ") + field);
    }
    return static_cast<size_t>(count * width);
}

EventKind
eventKind(uint8_t raw)
{
    if (raw > static_cast<uint8_t>(EventKind::Pass)) {
        throw std::runtime_error("invalid replay event kind");
    }
    return static_cast<EventKind>(raw);
}

bool
terminal(EventKind kind)
{
    return kind == EventKind::Census || kind == EventKind::Exit ||
           kind == EventKind::Killed || kind == EventKind::Pass;
}

Dataset
loadDataset(const std::string &path)
{
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        throw std::runtime_error("cannot open replay input");
    }
    input.seekg(0, std::ios::end);
    const auto end = input.tellg();
    if (end < 0) {
        throw std::runtime_error("cannot size replay input");
    }
    std::vector<uint8_t> bytes(static_cast<size_t>(end));
    input.seekg(0, std::ios::beg);
    input.read(reinterpret_cast<char *>(bytes.data()), bytes.size());
    if (!input || bytes.size() < HeaderBytes ||
        std::memcmp(bytes.data(), "BNERPLY1", 8) != 0) {
        throw std::runtime_error("invalid replay header");
    }
    const uint32_t version = readU32(bytes, 8);
    const uint32_t eventCount = readU32(bytes, 12);
    const uint32_t rootCount = readU32(bytes, 16);
    const uint32_t cellCount = readU32(bytes, 20);
    const uint64_t eventOffset = readU64(bytes, 24);
    const uint64_t rootOffset = readU64(bytes, 32);
    const uint64_t absorbedOffset = readU64(bytes, 40);
    const uint64_t trackOffset = readU64(bytes, 48);
    const uint64_t fileBytes = readU64(bytes, 56);
    const size_t eventsSize = checkedBytes(eventCount, EventBytes, "events");
    const size_t rootsSize = checkedBytes(rootCount, RootBytes, "roots");
    const size_t talliesSize = checkedBytes(cellCount, 8, "tallies");
    if (version != 1 || eventCount == 0 || rootCount == 0 || cellCount == 0 ||
        eventOffset != HeaderBytes || rootOffset != eventOffset + eventsSize ||
        absorbedOffset != rootOffset + rootsSize ||
        trackOffset != absorbedOffset + talliesSize ||
        fileBytes != trackOffset + talliesSize || fileBytes != bytes.size()) {
        throw std::runtime_error("inconsistent replay layout");
    }

    Dataset data;
    data.events.reserve(eventCount);
    for (size_t index = 0; index < eventCount; ++index) {
        const size_t offset = eventOffset + index * EventBytes;
        Event event;
        event.sourceCell = readU32(bytes, offset);
        event.destinationCell = readU32(bytes, offset + 4);
        event.nextEvent = readU32(bytes, offset + 8);
        event.kind = eventKind(bytes[offset + 12]);
        event.absorbed = fromBits<double>(readU64(bytes, offset + 16));
        event.track = fromBits<double>(readU64(bytes, offset + 24));
        if (event.sourceCell >= cellCount ||
            event.destinationCell >= cellCount ||
            (event.nextEvent != TerminalEvent &&
             event.nextEvent >= eventCount) ||
            !std::isfinite(event.absorbed) || !std::isfinite(event.track)) {
            throw std::runtime_error("invalid replay event");
        }
        data.events.push_back(event);
    }
    data.roots.reserve(rootCount);
    for (size_t index = 0; index < rootCount; ++index) {
        const size_t offset = rootOffset + index * RootBytes;
        Root root;
        root.firstEvent = readU32(bytes, offset);
        root.eventCount = readU32(bytes, offset + 4);
        root.finalCell = readU32(bytes, offset + 8);
        root.terminalKind = eventKind(readU32(bytes, offset + 12));
        if (root.firstEvent >= eventCount || root.eventCount == 0 ||
            root.eventCount > eventCount || root.finalCell >= cellCount ||
            !terminal(root.terminalKind)) {
            throw std::runtime_error("invalid replay root");
        }
        data.roots.push_back(root);
    }
    data.expectedAbsorbed.reserve(cellCount);
    data.expectedTrack.reserve(cellCount);
    for (size_t cell = 0; cell < cellCount; ++cell) {
        data.expectedAbsorbed.push_back(
            fromBits<double>(readU64(bytes, absorbedOffset + cell * 8)));
        data.expectedTrack.push_back(
            fromBits<double>(readU64(bytes, trackOffset + cell * 8)));
    }
    return data;
}

class BankedResidency
{
  private:
    struct Entry
    {
        bool valid = false;
        uint32_t cell = 0;
        uint64_t age = 0;
    };

    size_t banks;
    size_t ways;
    uint64_t age = 0;
    std::vector<Entry> entries;
    CacheCounters counterValues;

  public:
    BankedResidency(size_t count, size_t bankCount)
        : banks(bankCount), ways(bankCount ? count / bankCount : 0),
          entries(count)
    {
        if (count == 0 || bankCount == 0 || count % bankCount != 0 ||
            (bankCount & (bankCount - 1)) != 0) {
            throw std::invalid_argument("invalid residency geometry");
        }
    }

    void access(uint32_t cell)
    {
        ++counterValues.accesses;
        ++age;
        const size_t bank = cell & (banks - 1);
        const size_t first = bank * ways;
        size_t victim = first;
        for (size_t index = first; index < first + ways; ++index) {
            auto &entry = entries[index];
            if (entry.valid && entry.cell == cell) {
                entry.age = age;
                ++counterValues.hits;
                return;
            }
            if (!entry.valid ||
                (entries[victim].valid && entry.age < entries[victim].age)) {
                victim = index;
            }
        }
        ++counterValues.misses;
        counterValues.replacements += entries[victim].valid;
        entries[victim] = Entry{true, cell, age};
    }

    const CacheCounters &counters() const { return counterValues; }
};

class EventLineCache
{
  private:
    struct Entry
    {
        bool valid = false;
        uint32_t line = 0;
        uint64_t age = 0;
    };
    uint64_t age = 0;
    std::vector<Entry> entries;
    CacheCounters counterValues;

  public:
    explicit EventLineCache(size_t count) : entries(count)
    {
        if (count == 0) {
            throw std::invalid_argument("event line entries must be positive");
        }
    }

    void access(uint32_t event)
    {
        ++counterValues.accesses;
        ++age;
        const uint32_t line = event / 2;
        size_t victim = 0;
        for (size_t index = 0; index < entries.size(); ++index) {
            auto &entry = entries[index];
            if (entry.valid && entry.line == line) {
                entry.age = age;
                ++counterValues.hits;
                return;
            }
            if (!entry.valid ||
                (entries[victim].valid && entry.age < entries[victim].age)) {
                victim = index;
            }
        }
        ++counterValues.misses;
        counterValues.replacements += entries[victim].valid;
        entries[victim] = Entry{true, line, age};
    }

    const CacheCounters &counters() const { return counterValues; }
};

Result
emptyResult(size_t roots, size_t cells)
{
    Result result;
    result.finalCells.resize(roots);
    result.terminalKinds.resize(roots);
    result.absorbed.assign(cells, 0.0);
    result.track.assign(cells, 0.0);
    return result;
}

void
applyEvent(Result &result, const Event &event)
{
    result.absorbed[event.sourceCell] += event.absorbed;
    result.track[event.sourceCell] += event.track;
    ++result.eventsProcessed;
}

Result
runScalar(const Dataset &data, size_t roots)
{
    Result result = emptyResult(roots, data.expectedAbsorbed.size());
    for (size_t rootIndex = 0; rootIndex < roots; ++rootIndex) {
        const Root &root = data.roots[rootIndex];
        uint32_t eventIndex = root.firstEvent;
        uint32_t currentCell = data.events[eventIndex].sourceCell;
        EventKind finalKind = EventKind::Scatter;
        for (uint32_t step = 0; step < root.eventCount; ++step) {
            if (eventIndex >= data.events.size()) {
                throw std::runtime_error("replay chain escaped event array");
            }
            const Event &event = data.events[eventIndex];
            if (event.sourceCell >= result.absorbed.size() ||
                event.destinationCell >= result.absorbed.size() ||
                event.sourceCell != currentCell) {
                throw std::runtime_error("replay cell validation failed");
            }
            applyEvent(result, event);
            currentCell = event.destinationCell;
            finalKind = event.kind;
            const bool last = step + 1 == root.eventCount;
            if (last != (event.nextEvent == TerminalEvent)) {
                throw std::runtime_error("replay termination mismatch");
            }
            if (!last) {
                eventIndex = event.nextEvent;
            }
        }
        if (currentCell != root.finalCell || finalKind != root.terminalKind ||
            !terminal(finalKind)) {
            throw std::runtime_error("replay root oracle mismatch");
        }
        result.finalCells[rootIndex] = currentCell;
        result.terminalKinds[rootIndex] = finalKind;
    }
    return result;
}

uint64_t
tallyAddress(size_t array, size_t cell, size_t cells)
{
    return TallyBase + (array * cells + cell) * sizeof(double);
}

void
applyDrain(Result &result, const UpdateDrain &drain, size_t cells)
{
    if (drain.address < TallyBase || drain.dataType != DataType::Float64 ||
        drain.operation != UpdateOperation::Add) {
        throw std::runtime_error("invalid event replay update drain");
    }
    const uint64_t element = (drain.address - TallyBase) / sizeof(double);
    const size_t array = element / cells;
    const size_t cell = element % cells;
    if (array > 1 || cell >= cells) {
        throw std::runtime_error("out-of-range event replay update drain");
    }
    const double value = fromBits<double>(drain.valueBits);
    if (!std::isfinite(value)) {
        throw std::runtime_error("nonfinite event replay update drain");
    }
    (array == 0 ? result.absorbed[cell] : result.track[cell]) += value;
}

bool
drainUpdates(UpdateCombinerModel &model, Result &result, size_t cells)
{
    bool progress = false;
    while (auto drain = model.drainNext()) {
        applyDrain(result, *drain, cells);
        if (!model.acknowledge(drain->drainId)) {
            throw std::runtime_error(
                "event replay update acknowledgement failed");
        }
        progress = true;
    }
    return progress;
}

void
submitUpdate(UpdateCombinerModel &model, Result &result, size_t cells,
             uint64_t tag, uint64_t address, double value)
{
    while (true) {
        const Admission admission = model.admitUpdate(
            tag, address, toBits(value), DataType::Float64,
            UpdateOperation::Add, Ordering::Relaxed, OverflowPolicy::Fault);
        if (admission == Admission::Accepted) {
            return;
        }
        if (admission == Admission::Invalid) {
            throw std::runtime_error("valid event replay update was rejected");
        }
        if (!drainUpdates(model, result, cells)) {
            throw std::runtime_error("event replay combiner made no progress");
        }
    }
}

ModelResult
runModel(const Dataset &data, size_t roots, const Options &options)
{
    struct Context
    {
        size_t root = 0;
        uint32_t event = 0;
        uint32_t remaining = 0;
        uint32_t currentCell = 0;
    };

    Configuration configuration;
    configuration.operationEntries = options.continuationContexts;
    configuration.lineEntries = options.eventLineEntries;
    configuration.continuationContexts = options.continuationContexts;
    configuration.combinerEntries = options.combinerEntries;
    configuration.combinerBanks = options.combinerBanks;
    configuration.acknowledgementCredits = options.combinerEntries;
    UpdateCombinerModel updateModel(configuration);
    if (!configuration.valid() || !updateModel.valid() ||
        options.residencyEntries == 0 || options.residencyBanks == 0) {
        throw std::invalid_argument("invalid event replay configuration");
    }
    BankedResidency residency(
        options.residencyEntries, options.residencyBanks);
    EventLineCache eventLines(options.eventLineEntries);
    ModelResult output;
    output.values = emptyResult(roots, data.expectedAbsorbed.size());
    std::vector<Context> active;
    active.reserve(options.continuationContexts);
    size_t nextRoot = 0;
    size_t retired = 0;
    uint64_t updateTag = 1;
    while (retired < roots) {
        while (nextRoot < roots &&
               active.size() < options.continuationContexts) {
            const Root &root = data.roots[nextRoot];
            active.push_back(Context{
                nextRoot, root.firstEvent, root.eventCount,
                data.events[root.firstEvent].sourceCell});
            ++nextRoot;
            output.activeContextHighWater = std::max(
                output.activeContextHighWater,
                static_cast<uint64_t>(active.size()));
        }
        if (nextRoot < roots &&
            active.size() == options.continuationContexts) {
            ++output.contextWouldBlock;
        }
        if (active.empty()) {
            throw std::runtime_error(
                "event replay scheduler made no progress");
        }

        std::vector<Context> continuing;
        continuing.reserve(active.size());
        for (auto &context : active) {
            bool done = false;
            for (size_t issued = 0;
                 issued < options.contextQuantum && !done; ++issued) {
                if (context.event >= data.events.size() ||
                    context.remaining == 0) {
                    throw std::runtime_error("invalid active replay context");
                }
                const Event &event = data.events[context.event];
                if (event.sourceCell >= output.values.absorbed.size() ||
                    event.destinationCell >= output.values.absorbed.size() ||
                    event.sourceCell != context.currentCell ||
                    !std::isfinite(event.absorbed) ||
                    !std::isfinite(event.track)) {
                    throw std::runtime_error(
                        "event replay context validation failed");
                }
                eventLines.access(context.event);
                residency.access(event.sourceCell);
                submitUpdate(
                    updateModel, output.values, data.expectedAbsorbed.size(),
                    updateTag++, tallyAddress(
                        0, event.sourceCell, data.expectedAbsorbed.size()),
                    event.absorbed);
                submitUpdate(
                    updateModel, output.values, data.expectedAbsorbed.size(),
                    updateTag++, tallyAddress(
                        1, event.sourceCell, data.expectedAbsorbed.size()),
                    event.track);
                ++output.values.eventsProcessed;
                --context.remaining;
                context.currentCell = event.destinationCell;
                done = context.remaining == 0;
                if (done != (event.nextEvent == TerminalEvent)) {
                    throw std::runtime_error(
                        "event replay model termination mismatch");
                }
                if (done) {
                    const Root &root = data.roots[context.root];
                    if (context.currentCell != root.finalCell ||
                        event.kind != root.terminalKind ||
                        !terminal(event.kind)) {
                        throw std::runtime_error(
                            "event replay model root mismatch");
                    }
                    output.values.finalCells[context.root] =
                        context.currentCell;
                    output.values.terminalKinds[context.root] = event.kind;
                    ++retired;
                } else {
                    context.event = event.nextEvent;
                }
            }
            if (!done) {
                continuing.push_back(context);
            }
        }
        active.swap(continuing);
    }
    drainUpdates(updateModel, output.values, data.expectedAbsorbed.size());
    if (updateModel.outstandingEntries() != 0) {
        throw std::runtime_error("event replay combiner did not quiesce");
    }
    output.eventLines = eventLines.counters();
    output.residency = residency.counters();
    output.updates = updateModel.counters();
    return output;
}

bool
close(double left, double right)
{
    const double scale = std::max({1.0, std::fabs(left), std::fabs(right)});
    return std::fabs(left - right) <= 1.0e-12 * scale;
}

bool
equalResults(const Result &left, const Result &right)
{
    if (left.finalCells != right.finalCells ||
        left.terminalKinds != right.terminalKinds ||
        left.eventsProcessed != right.eventsProcessed ||
        left.absorbed.size() != right.absorbed.size() ||
        left.track.size() != right.track.size()) {
        return false;
    }
    for (size_t cell = 0; cell < left.absorbed.size(); ++cell) {
        if (!close(left.absorbed[cell], right.absorbed[cell]) ||
            !close(left.track[cell], right.track[cell])) {
            return false;
        }
    }
    return true;
}

bool
matchesEmbeddedOracle(const Result &result, const Dataset &data)
{
    if (result.absorbed.size() != data.expectedAbsorbed.size()) {
        return false;
    }
    for (size_t cell = 0; cell < result.absorbed.size(); ++cell) {
        if (toBits(result.absorbed[cell]) !=
                toBits(data.expectedAbsorbed[cell]) ||
            toBits(result.track[cell]) != toBits(data.expectedTrack[cell])) {
            return false;
        }
    }
    return true;
}

size_t
parseSize(const std::string &value, const std::string &option)
{
    size_t parsed = 0;
    const unsigned long long result = std::stoull(value, &parsed, 0);
    if (parsed != value.size() ||
        result > std::numeric_limits<size_t>::max()) {
        throw std::invalid_argument("invalid value for " + option);
    }
    return static_cast<size_t>(result);
}

Options
parseOptions(int argc, char **argv)
{
    Options options;
    for (int index = 1; index < argc; ++index) {
        const std::string option = argv[index];
        if (option == "--corrupt-first-source") {
            options.corruptFirstSource = true;
            continue;
        }
        if (index + 1 == argc) {
            throw std::invalid_argument("missing value for " + option);
        }
        const std::string value = argv[++index];
        if (option == "--input") {
            options.input = value;
        } else if (option == "--roots") {
            options.roots = parseSize(value, option);
        } else if (option == "--contexts") {
            options.continuationContexts = parseSize(value, option);
        } else if (option == "--context-quantum") {
            options.contextQuantum = parseSize(value, option);
        } else if (option == "--event-lines") {
            options.eventLineEntries = parseSize(value, option);
        } else if (option == "--residency-entries") {
            options.residencyEntries = parseSize(value, option);
        } else if (option == "--residency-banks") {
            options.residencyBanks = parseSize(value, option);
        } else if (option == "--combiner-entries") {
            options.combinerEntries = parseSize(value, option);
        } else if (option == "--combiner-banks") {
            options.combinerBanks = parseSize(value, option);
        } else {
            throw std::invalid_argument("unknown option " + option);
        }
    }
    return options;
}

} // anonymous namespace

int
main(int argc, char **argv)
{
    try {
        const Options options = parseOptions(argc, argv);
        Dataset data = loadDataset(options.input);
        if (options.corruptFirstSource) {
            data.events[data.roots.front().firstEvent].sourceCell =
                data.expectedAbsorbed.size();
        }
        const size_t roots = options.roots == 0 ? data.roots.size()
                                                : options.roots;
        if (roots == 0 || roots > data.roots.size() ||
            options.contextQuantum == 0) {
            throw std::invalid_argument("invalid selected root count");
        }
        const Result scalar = runScalar(data, roots);
        if (roots == data.roots.size() &&
            !matchesEmbeddedOracle(scalar, data)) {
            throw std::runtime_error("embedded native tally oracle mismatch");
        }
        const ModelResult model = runModel(data, roots, options);
        if (!equalResults(scalar, model.values) ||
            model.eventLines.accesses != scalar.eventsProcessed ||
            model.eventLines.hits + model.eventLines.misses !=
                model.eventLines.accesses ||
            model.residency.accesses != scalar.eventsProcessed ||
            model.residency.hits + model.residency.misses !=
                model.residency.accesses ||
            model.updates.logicalUpdatesAdmitted !=
                scalar.eventsProcessed * 2 ||
            model.updates.logicalUpdatesCompleted !=
                model.updates.logicalUpdatesAdmitted ||
            model.updates.drains + model.updates.combinerHits !=
                model.updates.logicalUpdatesAdmitted ||
            model.updates.acknowledgements != model.updates.drains ||
            model.updates.invalidAdmissions != 0) {
            throw std::runtime_error("event replay verification failed");
        }
        std::cout << "verification=PASS\n";
        std::cout << "native_physics_recomputed=0\n";
        std::cout << "roots=" << roots << '\n';
        std::cout << "events=" << scalar.eventsProcessed << '\n';
        std::cout << "contexts=" << options.continuationContexts << '\n';
        std::cout << "context_quantum=" << options.contextQuantum << '\n';
        std::cout << "context_would_block=" << model.contextWouldBlock << '\n';
        std::cout << "active_context_high_water="
                  << model.activeContextHighWater << '\n';
        std::cout << "event_line_entries=" << options.eventLineEntries << '\n';
        std::cout << "event_line_reads=" << model.eventLines.misses << '\n';
        std::cout << "event_line_hits=" << model.eventLines.hits << '\n';
        std::cout << "residency_entries=" << options.residencyEntries << '\n';
        std::cout << "residency_banks=" << options.residencyBanks << '\n';
        std::cout << "residency_hits=" << model.residency.hits << '\n';
        std::cout << "residency_misses=" << model.residency.misses << '\n';
        std::cout << "logical_fp64_updates="
                  << model.updates.logicalUpdatesAdmitted << '\n';
        std::cout << "combiner_entries=" << options.combinerEntries << '\n';
        std::cout << "combiner_banks=" << options.combinerBanks << '\n';
        std::cout << "fp64_combiner_hits="
                  << model.updates.combinerHits << '\n';
        std::cout << "fp64_update_drains=" << model.updates.drains << '\n';
        std::cout << "combiner_would_block="
                  << model.updates.combinerWouldBlock << '\n';
        std::cout << "combiner_bank_would_block="
                  << model.updates.combinerBankWouldBlock << '\n';
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "branson_native_event_replay: " << error.what() << '\n';
        return 2;
    }
}
