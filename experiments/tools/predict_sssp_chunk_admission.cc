// Deterministic host-side predictor for the GAPBS SSSP old-result hybrid.
//
// This deliberately does not include or execute the benchmark.  It mirrors the
// DeltaStep frontier/bin rules and sssp_chunk_admission::Tracker decisions in
// a stable, thread-major ordering so admission can be screened before gem5.

#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <vector>

namespace {

constexpr int32_t kDistInf = std::numeric_limits<int32_t>::max() / 2;
constexpr uint64_t kMaxBin = std::numeric_limits<uint64_t>::max() / 2;
constexpr size_t kLogicalWords = 16 * 1024;
constexpr int64_t kRandSeed = 27491095;
constexpr const char *kToolVersion = "3";

enum Reason : uint8_t
{
    None = 0,
    Bounds = 1U << 0,
    ActiveSource = 1U << 1,
    CrossOwner = 1U << 2,
};

class Tracker
{
  public:
    explicit Tracker(size_t chunks) : reasons_(chunks, None) {
        if (chunks == 0)
            throw std::runtime_error("zero admission chunks");
    }

    void rejectAll(Reason reason) {
        for (auto &entry : reasons_)
            entry |= reason;
    }

    bool observeDestination(size_t owner, bool active_source, uint32_t epoch,
                            uint32_t &destination_epoch,
                            uint32_t &destination_owner) {
        if (owner >= reasons_.size() || epoch == 0)
            return false;
        if (active_source)
            reasons_[owner] |= ActiveSource;
        if (destination_epoch != epoch) {
            destination_epoch = epoch;
            destination_owner = static_cast<uint32_t>(owner);
            return true;
        }
        if (destination_owner == owner)
            return true;
        if (destination_owner >= reasons_.size())
            return false;
        reasons_[destination_owner] |= CrossOwner;
        reasons_[owner] |= CrossOwner;
        return true;
    }

    bool safe(size_t owner) const { return reasons_.at(owner) == None; }
    bool snapshotSafe(size_t owner) const {
        return (reasons_.at(owner) & Bounds) == 0;
    }
    bool hasReason(size_t owner, Reason reason) const {
        return (reasons_.at(owner) & reason) != 0;
    }
    bool hasAnyReason(size_t owner) const {
        return reasons_.at(owner) != None;
    }

  private:
    std::vector<uint8_t> reasons_;
};

class MappedGraph
{
  public:
    struct Neighbor
    {
        int32_t vertex;
        int32_t weight;
    };

    explicit MappedGraph(const std::string &path) : path_(path) {
        fd_ = open(path.c_str(), O_RDONLY);
        if (fd_ < 0)
            failErrno("open");
        struct stat st;
        if (fstat(fd_, &st) != 0)
            failErrno("fstat");
        if (st.st_size < 9)
            throw std::runtime_error(
                "serialized graph is shorter than header");
        bytes_ = static_cast<size_t>(st.st_size);
        mapping_ = static_cast<const uint8_t *>(
            mmap(nullptr, bytes_, PROT_READ, MAP_PRIVATE, fd_, 0));
        if (mapping_ == MAP_FAILED) {
            mapping_ = nullptr;
            failErrno("mmap");
        }

        directed_ = mapping_[0] != 0;
        copyAt(1, edges_);
        copyAt(5, nodes_);
        if (nodes_ <= 0 || edges_ < 0)
            throw std::runtime_error("invalid negative/zero graph dimensions");
        offsets_base_ = 9;
        neighbors_base_ = checkedAdd(offsets_base_,
            checkedMultiply(static_cast<size_t>(nodes_) + 1, 4));
        const size_t csr_bytes = checkedAdd(
            checkedMultiply(static_cast<size_t>(nodes_) + 1, 4),
            checkedMultiply(static_cast<size_t>(edges_), 8));
        const size_t expected = checkedAdd(
            9, directed_ ? checkedMultiply(2, csr_bytes) : csr_bytes);
        if (expected != bytes_) {
            std::ostringstream message;
            message << "serialized graph size mismatch: expected " << expected
                    << " bytes, found " << bytes_;
            throw std::runtime_error(message.str());
        }
        if (offset(0) != 0 || offset(nodes_) != edges_)
            throw std::runtime_error("serialized graph offsets do not close");
    }

    ~MappedGraph() {
        if (mapping_)
            munmap(const_cast<uint8_t *>(mapping_), bytes_);
        if (fd_ >= 0)
            close(fd_);
    }

    MappedGraph(const MappedGraph &) = delete;
    MappedGraph &operator=(const MappedGraph &) = delete;

    int32_t nodes() const { return nodes_; }
    int32_t edges() const { return edges_; }
    bool directed() const { return directed_; }
    size_t bytes() const { return bytes_; }
    const std::string &path() const { return path_; }

    int32_t offset(int32_t vertex) const {
        if (vertex < 0 || vertex > nodes_)
            throw std::runtime_error("offset vertex outside graph");
        int32_t result;
        copyAt(offsets_base_ + static_cast<size_t>(vertex) * 4, result);
        return result;
    }

    Neighbor neighbor(int32_t edge) const {
        if (edge < 0 || edge >= edges_)
            throw std::runtime_error("edge outside graph");
        Neighbor result;
        copyAt(neighbors_base_ + static_cast<size_t>(edge) * 8, result);
        return result;
    }

  private:
    template <typename T> void copyAt(size_t at, T &result) const {
        if (at > bytes_ || sizeof(T) > bytes_ - at)
            throw std::runtime_error("serialized graph read outside file");
        std::memcpy(&result, mapping_ + at, sizeof(T));
    }

    static size_t checkedAdd(size_t left, size_t right) {
        if (right > std::numeric_limits<size_t>::max() - left)
            throw std::runtime_error("serialized graph size overflow");
        return left + right;
    }

    static size_t checkedMultiply(size_t left, size_t right) {
        if (left != 0 && right > std::numeric_limits<size_t>::max() / left)
            throw std::runtime_error("serialized graph size overflow");
        return left * right;
    }

    [[noreturn]] void failErrno(const char *operation) const {
        throw std::runtime_error(std::string(operation) + " " + path_ + ": " +
                                 std::strerror(errno));
    }

    std::string path_;
    int fd_ = -1;
    const uint8_t *mapping_ = nullptr;
    size_t bytes_ = 0;
    bool directed_ = false;
    int32_t edges_ = 0;
    int32_t nodes_ = 0;
    size_t offsets_base_ = 0;
    size_t neighbors_base_ = 0;
};

template <typename NodeID, typename Rng,
          typename UnsignedNodeID = typename std::make_unsigned<NodeID>::type>
class UniDist
{
  public:
    UniDist(NodeID max_value, Rng &rng) : rng_(rng) {
        no_mod_ = rng_.max() == static_cast<UnsignedNodeID>(max_value);
        mod_ = static_cast<UnsignedNodeID>(max_value) + 1;
        const UnsignedNodeID remainder_sub_1 = rng_.max() % mod_;
        cutoff_ = remainder_sub_1 == mod_ - 1
            ? 0
            : rng_.max() - remainder_sub_1;
    }

    NodeID operator()() {
        UnsignedNodeID value = rng_();
        if (no_mod_)
            return static_cast<NodeID>(value);
        while (cutoff_ != 0 && value >= cutoff_)
            value = rng_();
        return static_cast<NodeID>(value % mod_);
    }

  private:
    Rng &rng_;
    bool no_mod_ = false;
    UnsignedNodeID mod_ = 0;
    UnsignedNodeID cutoff_ = 0;
};

struct Iteration
{
    uint64_t number = 0;
    uint64_t bin = 0;
    size_t frontier_words = 0;
    bool maa = false;
    int chunk_frontier_words = 0;
    size_t chunk_count = 0;
    size_t active_sources = 0;
    uint64_t active_edge_words = 0;
    uint64_t eligible = 0;
    uint64_t routed = 0;
    uint64_t unsafe = 0;
    uint64_t reason_covered = 0;
    uint64_t bounds = 0;
    uint64_t active_source = 0;
    uint64_t cross_owner = 0;
    uint64_t active_source_observed = 0;
    uint64_t cross_owner_observed = 0;
    uint64_t active_source_tolerated = 0;
    uint64_t cross_owner_tolerated = 0;
};

struct Totals
{
    uint64_t frontier_words = 0;
    uint64_t active_edge_words = 0;
    uint64_t eligible = 0;
    uint64_t routed = 0;
    uint64_t unsafe = 0;
    uint64_t reason_covered = 0;
    uint64_t bounds = 0;
    uint64_t active_source = 0;
    uint64_t cross_owner = 0;
    uint64_t active_source_observed = 0;
    uint64_t cross_owner_observed = 0;
    uint64_t active_source_tolerated = 0;
    uint64_t cross_owner_tolerated = 0;
    uint64_t base_iterations = 0;
    uint64_t maa_iterations = 0;
};

struct Options
{
    std::string input;
    std::string output;
    int32_t source = -1;
    int32_t delta = 1;
    int threads = 4;
    std::string admission_policy = "reject-hazards";
};

int chunkFrontierWords(size_t frontier_words, int threads) {
    return frontier_words > static_cast<size_t>(threads) * 4096 ? 4096
        : frontier_words > static_cast<size_t>(threads) * 2048 ? 2048
                                                                  : 1024;
}

std::pair<size_t, size_t> staticPartition(size_t items, int threads, int tid) {
    const size_t quotient = items / static_cast<size_t>(threads);
    const size_t remainder = items % static_cast<size_t>(threads);
    const size_t begin = static_cast<size_t>(tid) * quotient +
        std::min(static_cast<size_t>(tid), remainder);
    const size_t count = quotient + (static_cast<size_t>(tid) < remainder);
    return {begin, begin + count};
}

std::string jsonEscape(const std::string &input) {
    std::ostringstream output;
    for (const unsigned char ch : input) {
        switch (ch) {
        case '\\': output << "\\\\"; break;
        case '"': output << "\\\""; break;
        case '\n': output << "\\n"; break;
        case '\r': output << "\\r"; break;
        case '\t': output << "\\t"; break;
        default:
            if (ch < 0x20)
                output << "\\u" << std::hex << std::setw(4)
                       << std::setfill('0') << static_cast<unsigned>(ch)
                       << std::dec << std::setfill(' ');
            else
                output << ch;
        }
    }
    return output.str();
}

Options parseOptions(int argc, char **argv) {
    Options options;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        auto value = [&]() -> std::string {
            if (++i >= argc)
                throw std::runtime_error("missing value after " + arg);
            return argv[i];
        };
        if (arg == "--input")
            options.input = value();
        else if (arg == "--output")
            options.output = value();
        else if (arg == "--source")
            options.source = static_cast<int32_t>(std::stoll(value()));
        else if (arg == "--delta")
            options.delta = static_cast<int32_t>(std::stoll(value()));
        else if (arg == "--threads")
            options.threads = std::stoi(value());
        else if (arg == "--admission-policy")
            options.admission_policy = value();
        else if (arg == "--help") {
            std::cout <<
                "usage: predict_sssp_chunk_admission --input GRAPH.wsg "
                         "[--source NODE] [--delta N] [--threads N] "
                         "[--admission-policy "
                         "reject-hazards|snapshot-tolerant] "
                         "[--output RESULT.json]\n";
            std::exit(0);
        } else {
            throw std::runtime_error("unknown argument: " + arg);
        }
    }
    if (options.input.empty())
        throw std::runtime_error("--input is required");
    if (options.delta <= 0)
        throw std::runtime_error("--delta must be positive");
    if (options.threads <= 0)
        throw std::runtime_error("--threads must be positive");
    if (options.admission_policy != "reject-hazards" &&
        options.admission_policy != "snapshot-tolerant")
        throw std::runtime_error(
            "--admission-policy must be reject-hazards or snapshot-tolerant");
    return options;
}

void appendTotals(Totals &totals, const Iteration &iteration) {
    totals.frontier_words += iteration.frontier_words;
    totals.active_edge_words += iteration.active_edge_words;
    totals.eligible += iteration.eligible;
    totals.routed += iteration.routed;
    totals.unsafe += iteration.unsafe;
    totals.reason_covered += iteration.reason_covered;
    totals.bounds += iteration.bounds;
    totals.active_source += iteration.active_source;
    totals.cross_owner += iteration.cross_owner;
    totals.active_source_observed += iteration.active_source_observed;
    totals.cross_owner_observed += iteration.cross_owner_observed;
    totals.active_source_tolerated += iteration.active_source_tolerated;
    totals.cross_owner_tolerated += iteration.cross_owner_tolerated;
    totals.base_iterations += !iteration.maa;
    totals.maa_iterations += iteration.maa;
}

std::string renderJson(const MappedGraph &graph, const Options &options,
                       int32_t source, const std::string &source_selection,
                       const std::vector<Iteration> &iterations,
                       const Totals &totals, double elapsed_seconds) {
    std::ostringstream out;
    out << std::fixed << std::setprecision(6);
    out << "{\n"
        << "  \"schema\": 3,\n"
        << "  \"tool\": \"predict_sssp_chunk_admission\",\n"
        << "  \"tool_version\": \"" << kToolVersion << "\",\n"
        << "  \"input\": \"" << jsonEscape(graph.path()) << "\",\n"
        << "  \"input_bytes\": " << graph.bytes() << ",\n"
        << "  \"directed\": " << (graph.directed() ? "true" : "false") << ",\n"
        << "  \"vertices\": " << graph.nodes() << ",\n"
        << "  \"directed_edges\": " << graph.edges() << ",\n"
        << "  \"source\": " << source << ",\n"
        << "  \"source_selection\": \"" << source_selection << "\",\n"
        << "  \"delta\": " << options.delta << ",\n"
        << "  \"threads\": " << options.threads << ",\n"
        << "  \"admission_policy\": \""
        << options.admission_policy << "\",\n"
        << "  \"logical_window_words\": " << kLogicalWords << ",\n"
        << "  \"ordering_model\": "
           "\"openmp-static-partition-thread-major-relax-and-merge\",\n"
        << "  \"elapsed_seconds\": " << elapsed_seconds << ",\n"
        << "  \"iterations\": [\n";
    for (size_t i = 0; i < iterations.size(); ++i) {
        const auto &it = iterations[i];
        const bool counts_close =
            it.routed + it.unsafe == it.eligible &&
            it.reason_covered == it.unsafe;
        out << "    {\"iteration\": " << it.number
            << ", \"bin\": " << it.bin
            << ", \"frontier_words\": " << it.frontier_words
            << ", \"path\": \"" << (it.maa ? "maa" : "base") << "\""
            << ", \"chunk_frontier_words\": " << it.chunk_frontier_words
            << ", \"chunk_count\": " << it.chunk_count
            << ", \"active_sources\": " << it.active_sources
            << ", \"active_edge_words\": " << it.active_edge_words
            << ", \"eligible_windows\": " << it.eligible
            << ", \"routed_windows\": " << it.routed
            << ", \"unsafe_eligible_windows\": " << it.unsafe
            << ", \"reason_covered_unsafe_windows\": "
            << it.reason_covered
            << ", \"bounds_rejected_windows\": " << it.bounds
            << ", \"active_source_rejected_windows\": " << it.active_source
            << ", \"cross_owner_rejected_windows\": " << it.cross_owner
            << ", \"active_source_observed_windows\": "
            << it.active_source_observed
            << ", \"cross_owner_observed_windows\": "
            << it.cross_owner_observed
            << ", \"active_source_tolerated_windows\": "
            << it.active_source_tolerated
            << ", \"cross_owner_tolerated_windows\": "
            << it.cross_owner_tolerated
            << ", \"counts_close\": " << (counts_close ? "true" : "false")
            << "}" << (i + 1 == iterations.size() ? "\n" : ",\n");
    }
    const bool totals_close =
        totals.routed + totals.unsafe == totals.eligible &&
        totals.reason_covered == totals.unsafe;
    out << "  ],\n"
        << "  \"totals\": {\n"
        << "    \"iterations\": " << iterations.size() << ",\n"
        << "    \"base_iterations\": " << totals.base_iterations << ",\n"
        << "    \"maa_iterations\": " << totals.maa_iterations << ",\n"
        << "    \"frontier_words\": " << totals.frontier_words << ",\n"
        << "    \"active_edge_words\": " << totals.active_edge_words << ",\n"
        << "    \"eligible_windows\": " << totals.eligible << ",\n"
        << "    \"routed_windows\": " << totals.routed << ",\n"
        << "    \"unsafe_eligible_windows\": " << totals.unsafe << ",\n"
        << "    \"reason_covered_unsafe_windows\": "
        << totals.reason_covered << ",\n"
        << "    \"bounds_rejected_windows\": " << totals.bounds << ",\n"
        << "    \"active_source_rejected_windows\": "
        << totals.active_source << ",\n"
        << "    \"cross_owner_rejected_windows\": "
        << totals.cross_owner << ",\n"
        << "    \"active_source_observed_windows\": "
        << totals.active_source_observed << ",\n"
        << "    \"cross_owner_observed_windows\": "
        << totals.cross_owner_observed << ",\n"
        << "    \"active_source_tolerated_windows\": "
        << totals.active_source_tolerated << ",\n"
        << "    \"cross_owner_tolerated_windows\": "
        << totals.cross_owner_tolerated << ",\n"
        << "    \"counts_close\": " << (totals_close ? "true" : "false")
        << "\n"
        << "  }\n"
        << "}\n";
    return out.str();
}

int run(const Options &options) {
    const auto start = std::chrono::steady_clock::now();
    MappedGraph graph(options.input);
    int32_t source = options.source;
    std::string source_selection = "explicit";
    if (source == -1) {
        std::mt19937 rng(kRandSeed);
        UniDist<int32_t, std::mt19937> distribution(graph.nodes() - 1, rng);
        do {
            source = distribution();
        } while (graph.offset(source) == graph.offset(source + 1));
        source_selection = "gapbs-mt19937-seed-27491095";
    }
    if (source < 0 || source >= graph.nodes())
        throw std::runtime_error("source is outside graph");

    std::vector<int32_t> dist(static_cast<size_t>(graph.nodes()), kDistInf);
    std::vector<uint8_t> active(static_cast<size_t>(graph.nodes()), 0);
    std::vector<uint32_t> destination_epochs(
        static_cast<size_t>(graph.nodes()), 0);
    std::vector<uint32_t> destination_owners(
        static_cast<size_t>(graph.nodes()), 0);
    std::vector<std::vector<std::vector<int32_t>>> local_bins(
        static_cast<size_t>(options.threads));
    std::vector<int32_t> frontier(1, source);
    dist[source] = 0;
    uint64_t curr_bin = 0;
    uint32_t epoch = 0;
    std::vector<Iteration> iterations;
    Totals totals;

    while (curr_bin != kMaxBin) {
        if (frontier.empty())
            throw std::runtime_error(
                "nonterminal iteration has empty frontier");
        if (++epoch == 0) {
            std::fill(destination_epochs.begin(), destination_epochs.end(), 0);
            epoch = 1;
        }

        Iteration record;
        record.number = iterations.size();
        record.bin = curr_bin;
        record.frontier_words = frontier.size();
        record.maa = frontier.size() >=
            static_cast<size_t>(options.threads) * 1024;
        record.chunk_frontier_words =
            chunkFrontierWords(frontier.size(), options.threads);
        record.chunk_count =
            (frontier.size() + record.chunk_frontier_words - 1) /
                static_cast<size_t>(record.chunk_frontier_words);
        Tracker tracker(record.chunk_count);
        const bool snapshot_tolerant =
            options.admission_policy == "snapshot-tolerant";
        bool global_safe = true;
        int64_t lower_bound = -1;
        if (curr_bin > static_cast<uint64_t>(kDistInf / options.delta)) {
            global_safe = false;
        } else {
            lower_bound = static_cast<int64_t>(options.delta) *
                static_cast<int64_t>(curr_bin);
        }

        std::fill(active.begin(), active.end(), 0);
        std::vector<int32_t> source_snapshot(frontier.size(), kDistInf);
        for (size_t pos = 0; pos < frontier.size(); ++pos) {
            const int32_t u = frontier[pos];
            if (u < 0 || u >= graph.nodes()) {
                global_safe = false;
                continue;
            }
            const int32_t source_distance = dist[u];
            source_snapshot[pos] = source_distance;
            if (source_distance < 0 || source_distance > kDistInf) {
                global_safe = false;
                continue;
            }
            if (source_distance >= lower_bound && !active[u]) {
                active[u] = 1;
                ++record.active_sources;
            }
        }

        std::vector<uint64_t> chunk_words(record.chunk_count, 0);
        for (size_t pos = 0; pos < frontier.size(); ++pos) {
            const size_t owner = pos /
                static_cast<size_t>(record.chunk_frontier_words);
            const int32_t u = frontier[pos];
            if (u < 0 || u >= graph.nodes() || !active[u])
                continue;
            const int32_t begin = graph.offset(u);
            const int32_t end = graph.offset(u + 1);
            if (begin < 0 || end < begin || end > graph.edges()) {
                global_safe = false;
                continue;
            }
            chunk_words[owner] += static_cast<uint64_t>(end - begin);
            record.active_edge_words += static_cast<uint64_t>(end - begin);
            for (int32_t edge = begin; edge < end; ++edge) {
                const auto neighbor = graph.neighbor(edge);
                const int32_t source_distance =
                    snapshot_tolerant && record.maa
                        ? source_snapshot[pos]
                        : dist[u];
                const int64_t candidate =
                    static_cast<int64_t>(source_distance) + neighbor.weight;
                if (neighbor.vertex < 0 ||
                    neighbor.vertex >= graph.nodes() ||
                    neighbor.weight <= 0 || candidate < 0 ||
                    candidate > kDistInf || dist[neighbor.vertex] < 0 ||
                    dist[neighbor.vertex] > kDistInf) {
                    global_safe = false;
                    continue;
                }
                if (!tracker.observeDestination(
                        owner, active[neighbor.vertex] != 0, epoch,
                        destination_epochs[neighbor.vertex],
                        destination_owners[neighbor.vertex]))
                    throw std::runtime_error("tracker observation failed");
            }
        }
        if (!global_safe)
            tracker.rejectAll(Bounds);

        if (record.maa) {
            for (size_t owner = 0; owner < record.chunk_count; ++owner) {
                const uint64_t windows = chunk_words[owner] / kLogicalWords;
                record.eligible += windows;
                const bool has_active_source =
                    tracker.hasReason(owner, ActiveSource);
                const bool has_cross_owner =
                    tracker.hasReason(owner, CrossOwner);
                if (has_active_source)
                    record.active_source_observed += windows;
                if (has_cross_owner)
                    record.cross_owner_observed += windows;
                const bool route = global_safe &&
                    (snapshot_tolerant ? tracker.snapshotSafe(owner)
                                       : tracker.safe(owner));
                if (route) {
                    record.routed += windows;
                    if (snapshot_tolerant && has_active_source)
                        record.active_source_tolerated += windows;
                    if (snapshot_tolerant && has_cross_owner)
                        record.cross_owner_tolerated += windows;
                } else if (windows != 0) {
                    record.unsafe += windows;
                    if (tracker.hasAnyReason(owner))
                        record.reason_covered += windows;
                    if (tracker.hasReason(owner, Bounds))
                        record.bounds += windows;
                    if (tracker.hasReason(owner, ActiveSource))
                        record.active_source += windows;
                    if (tracker.hasReason(owner, CrossOwner))
                        record.cross_owner += windows;
                }
            }
        }

        auto relaxPosition = [&](size_t pos, int tid) {
            const int32_t u = frontier[pos];
            const int32_t source_distance =
                snapshot_tolerant && record.maa
                    ? source_snapshot[pos]
                    : (u >= 0 && u < graph.nodes() ? dist[u] : kDistInf);
            if (u < 0 || u >= graph.nodes() ||
                source_distance < lower_bound)
                return;
            const int32_t begin = graph.offset(u);
            const int32_t end = graph.offset(u + 1);
            for (int32_t edge = begin; edge < end; ++edge) {
                const auto neighbor = graph.neighbor(edge);
                if (neighbor.vertex < 0 || neighbor.vertex >= graph.nodes())
                    throw std::runtime_error(
                        "relaxation destination outside graph");
                const int64_t wide_new =
                    static_cast<int64_t>(source_distance) + neighbor.weight;
                if (wide_new < std::numeric_limits<int32_t>::min() ||
                    wide_new > std::numeric_limits<int32_t>::max())
                    throw std::runtime_error("relaxation distance overflow");
                const int32_t new_dist = static_cast<int32_t>(wide_new);
                if (new_dist < dist[neighbor.vertex]) {
                    dist[neighbor.vertex] = new_dist;
                    const uint64_t destination_bin =
                        static_cast<uint64_t>(new_dist / options.delta);
                    auto &bins = local_bins[static_cast<size_t>(tid)];
                    if (destination_bin >= bins.size())
                        bins.resize(static_cast<size_t>(destination_bin) + 1);
                    bins[static_cast<size_t>(destination_bin)].push_back(
                        neighbor.vertex);
                }
            }
        };

        if (record.maa) {
            for (int tid = 0; tid < options.threads; ++tid) {
                const auto part = staticPartition(
                    record.chunk_count, options.threads, tid);
                for (size_t owner = part.first; owner < part.second; ++owner) {
                    const size_t begin = owner *
                        static_cast<size_t>(record.chunk_frontier_words);
                    const size_t end = std::min(frontier.size(), begin +
                        static_cast<size_t>(record.chunk_frontier_words));
                    for (size_t pos = begin; pos < end; ++pos)
                        relaxPosition(pos, tid);
                }
            }
        } else {
            for (int tid = 0; tid < options.threads; ++tid) {
                const auto part = staticPartition(
                    frontier.size(), options.threads, tid);
                for (size_t pos = part.first; pos < part.second; ++pos)
                    relaxPosition(pos, tid);
            }
        }

        uint64_t next_bin = kMaxBin;
        for (const auto &bins : local_bins) {
            for (size_t bin = static_cast<size_t>(curr_bin);
                 bin < bins.size(); ++bin) {
                if (!bins[bin].empty()) {
                    next_bin = std::min(next_bin, static_cast<uint64_t>(bin));
                    break;
                }
            }
        }
        appendTotals(totals, record);
        if (record.routed + record.unsafe != record.eligible ||
            record.reason_covered != record.unsafe)
            throw std::runtime_error(
                "admission reason coverage does not close");
        iterations.push_back(record);
        frontier.clear();
        if (next_bin != kMaxBin) {
            for (auto &bins : local_bins) {
                if (next_bin < bins.size()) {
                    auto &selected = bins[static_cast<size_t>(next_bin)];
                    frontier.insert(
                        frontier.end(), selected.begin(), selected.end());
                    selected.clear();
                }
            }
        }
        curr_bin = next_bin;
    }

    const double elapsed = std::chrono::duration<double>(
        std::chrono::steady_clock::now() - start).count();
    if (totals.routed + totals.unsafe != totals.eligible ||
        totals.reason_covered != totals.unsafe)
        throw std::runtime_error(
            "total admission reason coverage does not close");
    const std::string json = renderJson(
        graph, options, source, source_selection, iterations, totals, elapsed);
    if (options.output.empty()) {
        std::cout << json;
    } else {
        std::ofstream output(options.output);
        if (!output)
            throw std::runtime_error("cannot open output: " + options.output);
        output << json;
        if (!output)
            throw std::runtime_error(
                "failed writing output: " + options.output);
    }
    return 0;
}

} // namespace

int main(int argc, char **argv) {
    try {
        return run(parseOptions(argc, argv));
    } catch (const std::exception &error) {
        std::cerr << "predict_sssp_chunk_admission: " << error.what() << '\n';
        return 2;
    }
}
