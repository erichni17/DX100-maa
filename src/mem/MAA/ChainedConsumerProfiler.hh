#ifndef __MEM_MAA_CHAINED_CONSUMER_PROFILER_HH__
#define __MEM_MAA_CHAINED_CONSUMER_PROFILER_HH__

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <set>
#include <vector>

namespace gem5 {

/**
 * Timing- and data-nonfunctional profiler for native SPD producer/consumer
 * edges.  The profiler deliberately uses host containers: they are
 * instrumentation only and are never part of the modeled chained mechanism.
 */
class ChainedConsumerProfiler
{
  public:
    enum class Stage : uint8_t
    {
        Other,
        IndirectLoad,
        Alu,
        IndirectRmw,
    };

    struct EdgeSummary
    {
        Stage producer = Stage::Other;
        Stage consumer = Stage::Alu;
        uint64_t logicalElements = 0;
        uint64_t enabledElements = 0;
        uint64_t skippedElements = 0;
        uint64_t enabledConsumed = 0;
        uint64_t skippedConsumed = 0;
        uint64_t readyOrderRegressions = 0;
        uint64_t liveValueTicks = 0;
        uint64_t maxLiveValues = 0;
        uint64_t maxLiveSpan = 0;
        bool incomplete = false;
    };

    ChainedConsumerProfiler() = default;

    ChainedConsumerProfiler(bool enabled, std::size_t num_tiles,
                            std::size_t num_elements)
    {
        configure(enabled, num_tiles, num_elements);
    }

    void configure(bool enabled, std::size_t num_tiles,
                   std::size_t num_elements)
    {
        enabled_ = enabled;
        num_elements_ = num_elements;
        unsupported_fanouts_ = 0;
        malformed_events_ = 0;
        tiles_.clear();
        if (enabled_)
            tiles_.resize(num_tiles);
    }

    bool enabled() const { return enabled_; }

    uint64_t unsupportedFanouts() const { return unsupported_fanouts_; }
    uint64_t malformedEvents() const { return malformed_events_; }

    void declareProducer(int tile, Stage producer)
    {
        if (!validTile(tile))
            return;
        TileState &state = tiles_[tile];
        if (state.edge.has_value()) {
            // A destination overwrite before its consumer finishes is outside
            // the single-consumer contract.  Do not perturb execution.
            state.edge->incomplete = true;
            malformed_events_++;
        }
        state.generation++;
        state.producer = producer;
        state.hasProducer = true;
        state.elements.assign(num_elements_, ProducedElement{});
        state.productionOrder.clear();
        state.edge.reset();
    }

    void declareConsumer(int tile, Stage consumer)
    {
        if (!validTile(tile))
            return;
        TileState &state = tiles_[tile];
        if (!state.hasProducer || !eligible(state.producer, consumer))
            return;
        if (state.edge.has_value()) {
            unsupported_fanouts_++;
            state.edge.reset();
            return;
        }

        state.edge.emplace();
        EdgeState &edge = *state.edge;
        edge.producer = state.producer;
        edge.consumer = consumer;
        edge.elements.assign(num_elements_, EdgeElement{});
        for (const int element : state.productionOrder) {
            const ProducedElement &produced = state.elements[element];
            if (produced.seen)
                noteProduced(edge, element, produced.enabled,
                             produced.tick);
        }
    }

    void produce(int tile, Stage producer, int element, bool payload_enabled,
                 uint64_t tick)
    {
        if (!validElement(tile, element))
            return;
        TileState &state = tiles_[tile];
        if (!state.hasProducer || state.producer != producer) {
            malformed_events_++;
            return;
        }
        ProducedElement &produced = state.elements[element];
        if (produced.seen) {
            malformed_events_++;
            return;
        }
        produced = ProducedElement{true, payload_enabled, tick};
        state.productionOrder.push_back(element);
        if (state.edge.has_value())
            noteProduced(*state.edge, element, payload_enabled, tick);
    }

    void consume(int tile, Stage consumer, int element, bool payload_enabled,
                 uint64_t tick)
    {
        if (!validElement(tile, element))
            return;
        TileState &state = tiles_[tile];
        if (!state.edge.has_value() || state.edge->consumer != consumer)
            return;
        noteConsumed(*state.edge, element, payload_enabled, tick);
    }

    std::optional<EdgeSummary> finishConsumer(int tile, Stage consumer)
    {
        if (!validTile(tile))
            return std::nullopt;
        TileState &state = tiles_[tile];
        if (!state.edge.has_value() || state.edge->consumer != consumer)
            return std::nullopt;

        EdgeState &edge = *state.edge;
        EdgeSummary result;
        result.producer = edge.producer;
        result.consumer = edge.consumer;
        result.logicalElements = edge.logicalElements;
        result.enabledElements = edge.enabledElements;
        result.skippedElements = edge.skippedElements;
        result.enabledConsumed = edge.enabledConsumed;
        result.skippedConsumed = edge.skippedConsumed;
        result.readyOrderRegressions = edge.readyOrderRegressions;
        result.liveValueTicks = edge.liveValueTicks;
        result.maxLiveValues = edge.maxLiveValues;
        result.maxLiveSpan = edge.maxLiveSpan;
        result.incomplete = edge.incomplete || !edge.liveElements.empty() ||
            edge.enabledElements != edge.enabledConsumed ||
            edge.skippedElements != edge.skippedConsumed;
        state.edge.reset();
        return result;
    }

  private:
    struct ProducedElement
    {
        bool seen = false;
        bool enabled = false;
        uint64_t tick = 0;
    };

    struct EdgeElement
    {
        bool produced = false;
        bool enabled = false;
        bool consumed = false;
        uint64_t readyTick = 0;
    };

    struct EdgeState
    {
        Stage producer = Stage::Other;
        Stage consumer = Stage::Alu;
        std::vector<EdgeElement> elements;
        std::set<int> liveElements;
        uint64_t logicalElements = 0;
        uint64_t enabledElements = 0;
        uint64_t skippedElements = 0;
        uint64_t enabledConsumed = 0;
        uint64_t skippedConsumed = 0;
        uint64_t readyOrderRegressions = 0;
        uint64_t liveValueTicks = 0;
        uint64_t maxLiveValues = 0;
        uint64_t maxLiveSpan = 0;
        int lastEnabledReady = -1;
        bool incomplete = false;
    };

    struct TileState
    {
        uint64_t generation = 0;
        Stage producer = Stage::Other;
        bool hasProducer = false;
        std::vector<ProducedElement> elements;
        std::vector<int> productionOrder;
        std::optional<EdgeState> edge;
    };

    bool enabled_ = false;
    std::size_t num_elements_ = 0;
    std::vector<TileState> tiles_;
    uint64_t unsupported_fanouts_ = 0;
    uint64_t malformed_events_ = 0;

    bool validTile(int tile) const
    {
        return enabled_ && tile >= 0 &&
            static_cast<std::size_t>(tile) < tiles_.size();
    }

    bool validElement(int tile, int element)
    {
        if (!validTile(tile))
            return false;
        if (element < 0 ||
            static_cast<std::size_t>(element) >= num_elements_) {
            malformed_events_++;
            return false;
        }
        return true;
    }

    static bool eligible(Stage producer, Stage consumer)
    {
        return (producer == Stage::IndirectLoad && consumer == Stage::Alu) ||
            (producer == Stage::Alu && consumer == Stage::IndirectRmw);
    }

    static void updateLiveSpan(EdgeState &edge)
    {
        edge.maxLiveValues = std::max<uint64_t>(
            edge.maxLiveValues, edge.liveElements.size());
        if (!edge.liveElements.empty()) {
            const uint64_t span = static_cast<uint64_t>(
                *edge.liveElements.rbegin() - *edge.liveElements.begin() + 1);
            edge.maxLiveSpan = std::max(edge.maxLiveSpan, span);
        }
    }

    void noteProduced(EdgeState &edge, int element, bool payload_enabled,
                      uint64_t tick)
    {
        EdgeElement &value = edge.elements[element];
        if (value.produced) {
            edge.incomplete = true;
            malformed_events_++;
            return;
        }
        value.produced = true;
        value.enabled = payload_enabled;
        value.readyTick = tick;
        edge.logicalElements++;
        if (payload_enabled) {
            edge.enabledElements++;
            if (edge.lastEnabledReady != -1 && element < edge.lastEnabledReady)
                edge.readyOrderRegressions++;
            edge.lastEnabledReady = element;
            edge.liveElements.insert(element);
            updateLiveSpan(edge);
        } else {
            edge.skippedElements++;
        }
    }

    void noteConsumed(EdgeState &edge, int element, bool payload_enabled,
                      uint64_t tick)
    {
        EdgeElement &value = edge.elements[element];
        if (!value.produced || value.consumed ||
            value.enabled != payload_enabled) {
            edge.incomplete = true;
            malformed_events_++;
            return;
        }
        value.consumed = true;
        if (payload_enabled) {
            edge.enabledConsumed++;
            if (tick < value.readyTick) {
                edge.incomplete = true;
                malformed_events_++;
            } else {
                edge.liveValueTicks += tick - value.readyTick;
            }
            edge.liveElements.erase(element);
            updateLiveSpan(edge);
        } else {
            edge.skippedConsumed++;
        }
    }
};

} // namespace gem5

#endif // __MEM_MAA_CHAINED_CONSUMER_PROFILER_HH__
