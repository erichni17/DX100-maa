#ifndef __MEM_MAA_HYBRID_MACRO_EVENT_TRACKER_HH__
#define __MEM_MAA_HYBRID_MACRO_EVENT_TRACKER_HH__

#include <array>
#include <cstddef>
#include <cstdint>

namespace gem5 {

/**
 * Aggregate timing ledger for the three consumer actions of one hybrid
 * virtual-tile descriptor.  The tracker deliberately records intervals and
 * totals rather than individual cache-line events.
 */
class HybridMacroEventTracker
{
  public:
    enum class Stage : uint8_t
    {
        PageFill = 0,
        ALU = 1,
        StreamStore = 2,
        Count = 3,
    };

    struct StageRecord
    {
        uint64_t firstIssueTick = 0;
        uint64_t lastIssueTick = 0;
        uint64_t lastCompleteTick = 0;
        uint64_t activeTicks = 0;
        uint64_t issues = 0;
        uint64_t completions = 0;
        uint64_t lines = 0;
        uint64_t bytes = 0;
        uint64_t retries = 0;
        uint64_t queueHighWater = 0;
    };

    struct Record
    {
        uint64_t startTick = 0;
        uint64_t endTick = 0;
        uint64_t overlapTicks = 0;
        uint64_t exposedIdleTicks = 0;
        uint64_t activeStageHighWater = 0;
        std::array<StageRecord, static_cast<size_t>(Stage::Count)> stages{};
    };

    bool begin(uint64_t tick)
    {
        if (active)
            return false;
        record = Record{};
        depths.fill(0);
        active = true;
        lastTick = tick;
        record.startTick = tick;
        return true;
    }

    bool issue(Stage stage, uint64_t tick, uint64_t queueDepth = 1)
    {
        if (!advance(tick))
            return false;
        const size_t index = stageIndex(stage);
        if (index >= depths.size())
            return false;
        StageRecord &entry = record.stages[index];
        if (entry.firstIssueTick == 0)
            entry.firstIssueTick = tick;
        entry.lastIssueTick = tick;
        entry.issues++;
        depths[index]++;
        if (queueDepth > entry.queueHighWater)
            entry.queueHighWater = queueDepth;
        updateActiveHighWater();
        return true;
    }

    bool complete(Stage stage, uint64_t tick)
    {
        if (!advance(tick))
            return false;
        const size_t index = stageIndex(stage);
        if (index >= depths.size() || depths[index] == 0)
            return false;
        depths[index]--;
        StageRecord &entry = record.stages[index];
        entry.lastCompleteTick = tick;
        entry.completions++;
        return true;
    }

    bool traffic(Stage stage, uint64_t lines, uint64_t bytes)
    {
        const size_t index = stageIndex(stage);
        if (!active || index >= depths.size())
            return false;
        record.stages[index].lines += lines;
        record.stages[index].bytes += bytes;
        return true;
    }

    bool retry(Stage stage, uint64_t tick)
    {
        if (!advance(tick))
            return false;
        const size_t index = stageIndex(stage);
        if (index >= depths.size())
            return false;
        record.stages[index].retries++;
        return true;
    }

    bool sample(uint64_t tick)
    {
        return advance(tick);
    }

    bool finish(uint64_t tick)
    {
        if (!advance(tick))
            return false;
        for (uint32_t depth : depths) {
            if (depth != 0)
                return false;
        }
        record.endTick = tick;
        active = false;
        return true;
    }

    bool isActive() const { return active; }
    const Record &result() const { return record; }

  private:
    static size_t stageIndex(Stage stage)
    {
        return static_cast<size_t>(stage);
    }

    uint64_t activeStageCount() const
    {
        uint64_t count = 0;
        for (uint32_t depth : depths)
            count += depth != 0;
        return count;
    }

    void updateActiveHighWater()
    {
        const uint64_t count = activeStageCount();
        if (count > record.activeStageHighWater)
            record.activeStageHighWater = count;
    }

    bool advance(uint64_t tick)
    {
        if (!active || tick < lastTick)
            return false;
        const uint64_t elapsed = tick - lastTick;
        const uint64_t activeCount = activeStageCount();
        if (activeCount == 0) {
            record.exposedIdleTicks += elapsed;
        } else {
            if (activeCount > 1)
                record.overlapTicks += elapsed;
            for (size_t i = 0; i < depths.size(); ++i) {
                if (depths[i] != 0)
                    record.stages[i].activeTicks += elapsed;
            }
        }
        lastTick = tick;
        record.endTick = tick;
        return true;
    }

    bool active = false;
    uint64_t lastTick = 0;
    std::array<uint32_t, static_cast<size_t>(Stage::Count)> depths{};
    Record record;
};

} // namespace gem5

#endif // __MEM_MAA_HYBRID_MACRO_EVENT_TRACKER_HH__
